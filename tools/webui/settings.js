/* settings.js -- the settings window.
 *
 * Reads `/api/settings`, which serves each declared setting together with its
 * default and -- the part that matters here -- what OBSERVABLY changes when it
 * is flipped. That string is rendered under every control rather than kept in
 * a tooltip: a settings list that shows names and values leaves the reader
 * guessing, and a knob nobody dares touch is a knob nobody uses.
 *
 * The same string is what `comod settings <name>` prints, from the same
 * registry in core/cosettings.py, so the two surfaces cannot drift into
 * describing one toggle two ways.
 *
 * DEFERRED SETTINGS ARE SHOWN AS DEFERRED. `thumbnails`, `cache_derived` and
 * `default_build` are named in the settings brief and are not wired to
 * anything yet. They are listed in their own section, disabled, saying so.
 * Hiding them would lose the fact that they are coming; showing them as live
 * switches would be a lie the user only discovers by flipping one.
 */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ======================================================================
   * IS THIS VIEWER RUNNING THE CODE ON DISK?  (`#viewer-stale`)
   * ======================================================================
   *
   * THE BUG. The viewer is a long-running Python process, and `tools/webui/*`
   * is read from disk on every request -- so a reload gets TODAY's HTML and
   * JavaScript talking to handlers imported hours ago. The page renders
   * perfectly and its buttons answer `no route /api/...`, with nothing on
   * screen saying why. It has cost the owner three times, as
   * `no route /api/installs/dirs`, `no route /api/bootstrap/status` and
   * `no route /api/selection`. The property this code is here to hold is
   * narrow and testable: **a user who sees `no route` also sees why.**
   *
   * TWO TRIGGERS, NOT ONE.
   *
   *   1. A check on load (and when the tab is brought back to the front,
   *      which is when someone returns from editing a file). This catches the
   *      staleness BEFORE a button fails.
   *   2. A `fetch` wrapper that watches for the exact symptom -- a 404 whose
   *      body says `no route` -- and re-checks immediately, naming the path
   *      that failed. This is the one that cannot be missed, because it fires
   *      on the very click that would otherwise go unexplained.
   *
   * The wrapper reads a CLONE of the response and never touches the original,
   * so the caller's `.json()` is unaffected; and it only clones on a 404, so
   * the ordinary path costs one integer comparison.
   *
   * A 404 ON THE CHECK ITSELF IS AN ANSWER, NOT A FAILURE. A viewer old
   * enough to predate `/api/viewer/build` cannot report its own staleness --
   * and a page asking for that route is by construction newer than those
   * handlers. So `no route /api/viewer/build` is treated as stale. This is
   * what makes the feature work against the servers it was written for
   * instead of only against future ones.
   *
   * NOT CRYING WOLF is the design constraint, because a false "restart me"
   * is worse than the bug -- the next real one gets ignored. Most of the work
   * is server-side (see `SourceWatch` in coviewer.py: content digests rather
   * than timestamps, only modules actually imported, only files in the
   * checkout, unreadable is not changed, lazily imported modules baselined at
   * first sight). This file adds three of its own:
   *
   *   * The banner has exactly ONE trigger -- `stale === true` from that
   *     endpoint, or its absence. No heuristics, no timeouts, no "the server
   *     feels slow".
   *   * A network error is silence. `fetch` rejecting says the viewer is
   *     down or the wifi went; it does not say the code is old.
   *   * It UN-shows. If the edit is reverted the digests match again, the
   *     next check answers `stale: false`, and the banner goes away by
   *     itself. A warning that can only appear is a warning that becomes
   *     furniture.
   */
  const stale = {
    shown: false,
    dismissed: false,
    inFlight: null,
    noRoute: [],          // paths that answered `no route`, in order seen
    last: null,           // the last non-null probe result
  };

  // The unwrapped `fetch`, captured before we wrap it. The probe uses THIS
  // one: a probe that went through the wrapper would see its own 404, call
  // itself, and never stop.
  const rawFetch = window.fetch.bind(window);

  function staleBanner() {
    let b = document.getElementById("viewer-stale");
    if (b) return b;
    b = el("div", "viewer-stale");
    b.id = "viewer-stale";
    b.setAttribute("role", "alert");
    b.hidden = true;
    document.body.insertBefore(b, document.body.firstChild);
    return b;
  }

  async function probeStale(force) {
    let r;
    try {
      r = await rawFetch("/api/viewer/build" + (force ? "?force=1" : ""));
    } catch (e) {
      return null;                       // down, not old. Say nothing.
    }
    if (r.status === 404) {
      return { stale: true, reason: "no-endpoint", changed: [],
               tracked: 0, uptimeSeconds: null };
    }
    if (!r.ok) return null;
    const doc = await r.json().catch(function () { return null; });
    if (!doc || doc.watched === false) return null;
    return {
      stale: !!doc.stale, reason: "changed-on-disk",
      changed: doc.changed || [], tracked: doc.tracked || 0,
      uptimeSeconds: doc.uptimeSeconds,
    };
  }

  function ago(sec) {
    if (sec == null) return "";
    if (sec < 90) return Math.round(sec) + " s";
    if (sec < 5400) return Math.round(sec / 60) + " min";
    return (sec / 3600).toFixed(1) + " h";
  }

  function paintStale(res) {
    const b = staleBanner();
    // `null` is "I could not tell", and it is a THIRD state -- never folded
    // into "fresh". A probe returns null when `fetch` rejects or the answer
    // does not parse, which says the viewer is down or the wifi went, not
    // that its code is current. Folding it into the not-stale branch would
    // make a true banner VANISH the moment the process the user needs to
    // restart stopped answering, which is the worst possible moment for it
    // to disappear. Leave the screen exactly as it is.
    if (!res) return;
    if (!res.stale) {
      // Reverted, or never stale. Take the banner away and let a future
      // dismissal start fresh -- see "It UN-shows" above.
      b.hidden = true;
      b.textContent = "";
      stale.shown = false;
      stale.dismissed = false;
      return;
    }
    stale.shown = true;
    if (stale.dismissed && !stale.reopen) return;
    stale.reopen = false;
    b.hidden = false;
    b.textContent = "";

    b.appendChild(el("strong", "viewer-stale-head",
      "This viewer is running older code than the page you are reading."));

    if (res.reason === "no-endpoint") {
      b.appendChild(el("div", null,
        "The page asked this process whether its code is current and it has " +
        "no such route, which only happens when the handlers predate the " +
        "files being served. tools/webui/* is read from disk on every " +
        "request; the Python was imported when the viewer started."));
    } else {
      const n = res.changed.length;
      b.appendChild(el("div", null,
        n + (n === 1 ? " source file this process imported has"
                     : " source files this process imported have") +
        " changed on disk since it started" +
        (res.uptimeSeconds != null ? " " + ago(res.uptimeSeconds) + " ago" : "") +
        ". The page is current because tools/webui/* is served from disk; " +
        "the handlers are not."));
      const ul = el("ul", "viewer-stale-files");
      res.changed.slice(0, 8).forEach(function (f) {
        ul.appendChild(el("li", null, f));
      });
      if (n > 8) ul.appendChild(el("li", null, "…and " + (n - 8) + " more"));
      b.appendChild(ul);
    }

    if (stale.noRoute.length) {
      b.appendChild(el("div", "viewer-stale-symptom",
        "That is why " +
        (stale.noRoute.length === 1 ? "this request" : "these requests") +
        " came back “no route”: " + stale.noRoute.join(", ")));
    }

    b.appendChild(el("div", "viewer-stale-fix",
      "Restart the viewer -- Ctrl-C in the window it is running in, then " +
      "start it again. Nothing on disk is broken; only this process is " +
      "behind. Reloading the page cannot fix it, because the page was " +
      "never the stale half."));

    const x = el("button", "viewer-stale-x", "Dismiss");
    x.addEventListener("click", function () {
      stale.dismissed = true;
      b.hidden = true;
    });
    b.appendChild(x);
  }

  function checkStale(force) {
    if (stale.inFlight) return stale.inFlight;
    stale.inFlight = probeStale(force).then(function (res) {
      stale.inFlight = null;
      if (res) stale.last = res;
      paintStale(res);
      return res;
    }, function () { stale.inFlight = null; return null; });
    return stale.inFlight;
  }

  function noteNoRoute(url) {
    let p = String(url || "");
    try { p = new URL(p, location.href).pathname; } catch (e) { /* keep raw */ }
    if (p === "/api/viewer/build") return;      // that 404 is its own answer
    if (stale.noRoute.indexOf(p) < 0) stale.noRoute.push(p);
    // A dismissed banner reopens for a NEW symptom. Dismissing "I know, I
    // will restart in a minute" should not silence the next surprise.
    stale.reopen = true;
    stale.dismissed = false;
    checkStale(true);
  }

  if (!window.__coStaleWrapped) {
    window.__coStaleWrapped = true;
    const inner = window.fetch;
    window.fetch = function (input, init) {
      const out = inner.apply(this, arguments);
      return out.then(function (r) {
        if (r && r.status === 404) {
          const url = (typeof input === "string") ? input
                    : (input && input.url) || "";
          // A clone, so the caller's body is untouched.
          r.clone().text().then(function (t) {
            if (t.indexOf("no route") >= 0) noteNoRoute(url);
          }).catch(function () { /* not readable: not our business */ });
        }
        return r;
      });
    };
  }

  window.coStale = {
    check: checkStale, probe: probeStale, paint: paintStale, state: stale,
  };

  document.addEventListener("visibilitychange", function () {
    // Coming back to the tab is when someone returns from an editor. No
    // interval polling: a settings page nobody is looking at has no reason
    // to keep asking.
    if (!document.hidden) checkStale(false);
  });

  async function load() {
    const r = await fetch("/api/settings");
    if (!r.ok) throw new Error("settings unavailable (" + r.status + ")");
    return r.json();
  }

  // Plain `fetch`: csrf.js wraps it and attaches this run's token to every
  // same-origin POST, so a hand-rolled poster here would be a second path to
  // keep in sync -- and the one that gets forgotten when the token changes.
  async function post(payload) {
    const r = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(doc.error || ("HTTP " + r.status));
    return doc;
  }

  /* IS THE CHROME AROUND THIS PAGE STILL THE RIGHT CHROME?
   *
   * `ui_mode` decides which tabs nav.js draws, and nav.js reads it from a
   * `<meta>` the server injects when the page is SERVED -- it cannot be a
   * fetch, because the bar renders synchronously before any fetch could
   * return (nav.js says why at length). So changing the mode from this page
   * writes the setting and leaves the bar around it showing the old mode's
   * tabs: the row says "saved" and the visible effect is missing, which
   * core/cosettings.py's own docstring calls worse than having no toggle.
   *
   * This asks the SERVER what it is serving now and compares it with what
   * this document was served with. It names no setting at all -- deliberately.
   * A list of "settings that need a reload" here would be the name list in
   * the browser that `surfaceOf` above exists to avoid, and it would go stale
   * silently the day a setting is renamed. This keeps working if the mode is
   * renamed, moved, or joined by a second piece of serve-time chrome.
   */
  async function chromeIsCurrent() {
    const meta = document.querySelector('meta[name="co-mode"]');
    if (!meta) return true;            // older viewer: nothing to compare
    let doc;
    try {
      doc = await (await fetch("/api/mode")).json();
    } catch (e) {
      return true;                     // unreachable is not the same as stale
    }
    return String(doc.mode || "") === String(meta.content || "");
  }

  const save = (name, value) => post({ name: name, value: value });
  const resetAll = () => post({ reset: true });

  function control(s, onChange) {
    // bool -> checkbox, str with choices -> select, int -> number.
    // The kind comes from the server's declaration rather than from sniffing
    // the value, so a bool that happens to be stored as 1 cannot render as a
    // number field.
    if (s.kind === "bool") {
      const i = el("input");
      i.type = "checkbox";
      i.checked = !!s.value;
      i.addEventListener("change", () => onChange(i.checked));
      return i;
    }
    if (s.choices && s.choices.length) {
      const sel = el("select");
      s.choices.forEach((c) => {
        const o = el("option", null, c);
        o.value = c;
        if (c === s.value) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", () => onChange(sel.value));
      return sel;
    }
    const i = el("input");
    i.type = s.kind === "int" ? "number" : "text";
    if (s.bounds && s.bounds.length === 2) {
      i.min = s.bounds[0];
      i.max = s.bounds[1];
    }
    i.value = s.value;
    i.addEventListener("change", () => onChange(i.value));
    return i;
  }

  function row(s, note) {
    const wrap = el("div", "set-row");
    const head = el("div", "set-head");
    const label = el("label", "set-name", s.name);
    head.appendChild(label);

    const c = control(s, async (v) => {
      note.textContent = "saving…";
      note.className = "set-note";
      try {
        await save(s.name, v);
        note.textContent = "saved";
        // Re-read rather than trusting the local value: the server coerces
        // and may have clamped or rejected, and showing what we SENT would
        // report a setting as applied that is not.
        render();
      } catch (e) {
        note.textContent = "refused: " + e.message;
        note.className = "set-note set-bad";
      }
    });
    c.id = "set-" + s.name;
    label.htmlFor = c.id;
    head.appendChild(c);
    if (!s.isDefault) {
      head.appendChild(el("span", "set-changed",
        "changed (default " + JSON.stringify(s.default) + ")"));
    }
    wrap.appendChild(head);
    wrap.appendChild(el("div", "set-help", s.help));
    wrap.appendChild(el("div", "set-effect", "Changing it: " + s.effect));
    return wrap;
  }

  // WHICH SECTION A SETTING BELONGS TO COMES FROM THE SERVER, not from a list
  // of names here. `developer_notes` moved to Developer options by gaining
  // `surface="developer"` in `core/cosettings.py` and nothing in this file
  // knows its name -- which is the point: a name list in the browser is the
  // copy that goes stale when a setting is renamed, and it goes stale
  // silently, leaving the row rendering in the section it used to be in.
  //
  // `surface: "panel"` is rendered by NOBODY here. `selected_installs` is a
  // list of install paths drawn by the health panel as a client checklist;
  // falling through to the generic control would give it a text input, and a
  // hand-typed path in that box would be pruned as undeclared the moment it
  // was read back -- a control that appears to accept a value and discards it.
  function surfaceOf(s) {
    return s.surface || "preferences";
  }

  function fill(host, list, doc) {
    host.textContent = "";
    const note = el("div", "set-note");
    list.forEach((s) => host.appendChild(row(s, note)));
    host.appendChild(note);
    return host;
  }

  async function render() {
    const host = $("#settings-body");
    if (!host) return;
    const devHost = $("#dev-body");
    host.textContent = "";
    let doc;
    try {
      doc = await load();
    } catch (e) {
      host.appendChild(el("div", "set-bad", String(e.message || e)));
      return;
    }

    const prefs = doc.settings.filter((s) => surfaceOf(s) === "preferences");
    const dev = doc.settings.filter((s) => surfaceOf(s) === "developer");
    fill(host, prefs, doc);

    if (devHost) {
      fill(devHost, dev, doc);
      if (!dev.length) {
        // The section keeps saying so rather than vanishing: it was
        // deliberately present before it had controls, and a section that
        // disappears when its last control is removed reads as a bug.
        devHost.appendChild(el("p", "set-help",
          "No developer-facing switches are declared."));
      }
    }

    if (doc.deferred && doc.deferred.length) {
      host.appendChild(el("h4", "set-sub", "Declared in the brief, not wired yet"));
      const why = el("div", "set-help",
        "These are wanted and are not connected to anything, so they are not " +
        "offered as switches. A setting that appears in a list reads as " +
        "connected to something; these would change nothing. They arrive with " +
        "the views that read them.");
      host.appendChild(why);
      const ul = el("ul", "set-deferred");
      doc.deferred.forEach((n) => ul.appendChild(el("li", null, n)));
      host.appendChild(ul);
    }

    const foot = el("div", "set-foot");
    foot.appendChild(el("div", "set-help", "Stored in " + doc.storePath));
    const rst = el("button", "set-reset", "Reset all to defaults");
    rst.addEventListener("click", async () => {
      await resetAll();
      render();
    });
    foot.appendChild(rst);
    host.appendChild(foot);

    // LAST, and after the page is painted: if the mode this document was
    // served with is no longer the mode the server serves, the bar is stale
    // and only a reload can fix it. `render()` runs on boot and after every
    // save, so this one call site covers both without a hook per control.
    // It cannot loop -- after the reload the meta matches.
    if (!(await chromeIsCurrent())) location.reload();
  }

  window.coSettings = { render: render, load: load, save: save };

  function boot() {
    // The staleness check runs FIRST and is not awaited. If the handlers are
    // old, `render()` below is one of the calls that will fail, and the
    // explanation should not be queued behind the thing it explains.
    checkStale(false);
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
