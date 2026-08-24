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
  }

  window.coSettings = { render: render, load: load, save: save };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
