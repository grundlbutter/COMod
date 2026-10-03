/* portagepanel.js -- the Import/Export tools pop-out (backlog item 6).
 *
 * The owner's ask, verbatim: "a pop out 'Import/Export tools' frame that lists
 * each item you see... a grouped list of every visible item and its
 * satellites. The first tier of the grouping should be the individual assets
 * if there are multiple, like in the character builder, and then a breakdown
 * of each asset grouped by type. You should be able to see their file-paths,
 * it should give you the button options to export each individual item, and a
 * way to export/import the entire batch to zip... The asset types should tell
 * you which tools you would use to work on this."
 *
 * WHAT THIS FILE DOES AND DOES NOT DO
 * -----------------------------------
 * It renders and it posts. It does not resolve satellites (that is
 * `tools/assetroot.py`, the ONE companion resolver), it does not transform
 * files or lay out the zip (that is `core/portage.py`), and it does not decide
 * which tool opens what (`portage.default_tool`, surfaced through
 * `tools/portageplan.py`). Every string in a row arrives from
 * `/api/portage/plan` already decided; the JS chooses only how to draw it.
 *
 * That matters for one specific reason. If this file computed "is this file
 * shared?" or "what tool is this?" it would be a SECOND answer to a question
 * `portage` already answers, and the two would drift -- and the direction of
 * the drift is the panel promising something the export does not do.
 *
 * THE FOUR THINGS THAT ARE THE POINT, NOT DECORATION
 * --------------------------------------------------
 * 1. A SHARED FILE IS EXPLAINED WHERE IT IS SHOWN. Two parts of a Builder
 *    composition can reference the same texture. Tier-1 grouping shows it
 *    twice, so a user who edits it under `body` has also changed it under
 *    `weapon`. The owner ruled that a footnote will not do -- the user has to
 *    see it "at the moment they click Export on `body`". So every shared row
 *    gets an amber rail, a "SHARED WITH <the other asset>" chip, and the
 *    consequence sentence inline; and the per-asset Export confirms it before
 *    it writes, naming the files and who else has them.
 * 2. A DECLARED-BUT-ABSENT SATELLITE IS A ROW, struck through, with no Export
 *    and the reason attached. Never omitted: 7,462 of 30,539 appearance refs
 *    on the live install resolve to nothing, and a list of 6 hiding 3 is a
 *    trap the user cannot detect.
 * 3. THE HONESTY BANNER IS NOT CONDITIONAL. `unresolvedCount` and the
 *    resolver's `limits` are drawn on every open, including when they are
 *    zero and empty -- a companion list with no "and N I could not resolve"
 *    line reads as an exhaustive one, and this panel is the single easiest
 *    place in COMod to mislead.
 * 4. EVERY ROW SHOWS ITS PLACE IN THE ZIP (`<asset>/<type>/<file>`). The
 *    structure on screen IS the structure on disk; `portageplan` computes both
 *    and `tests/test_portageplan.py::ZipLayoutIsWhatThePanelShowed` pins them
 *    to each other.
 *
 * WHERE THE SUBJECTS COME FROM
 * ----------------------------
 * By default `window.B` -- builder.js's own state object, which it already
 * exports. This file READS it and never writes to it: the companion/slot/
 * collect logic belongs to that file and a pop-out that mutated its loadout
 * would be a second owner of the composition. In character mode the subjects
 * are the filled slots (several, exactly as the owner drew it); in model mode
 * it is the one open model.
 *
 * A caller may instead pass its own list: `open({subjects: [...]})`. The
 * Effects Viewer does, because its subject is an EFFECT -- not a file, not in
 * any loadout, and absent from `window.B` entirely. The alternative was to
 * teach `subjects()` about a fourth page, which would put page-specific
 * knowledge in the one function whose whole job is to have none.
 *
 * TWO KINDS OF SUBJECT, AND THE CALLER SAYS WHICH
 * -----------------------------------------------
 * `{asset: 'c3/body/7130030.c3'}` is a FILE; `{effect: 'M_Fire'}` is an
 * EFFECT. Never sniffed from the value -- an effect name and a logical path
 * are both strings and `Blood` is a legal spelling of either, so a guess here
 * would send an effect to `depclose.resolve_target` and get back "no asset
 * 'Blood' in this install", which is a confident answer to the wrong question.
 *
 * AN EFFECT BRINGS A ROW THAT IS NOT A FILE
 * -----------------------------------------
 * An effect's DEFINITION is a row of `3DEffect` -- the compiled `3DEffect.dbc`
 * on 5517/6090/6609/7205, where the `.ini` beside it is a decoy. It travels in
 * the bundle as a row (`portage.DefinitionItem`), it is drawn with no checkbox
 * and no Export button (see `definitionRow` for why each of those would be a
 * specific false claim), and `honesty()` prints the count and the consequence
 * in BOTH states. That last part is the requirement: a bundle carrying a
 * definition and one carrying none look identical in a list of files, and what
 * a reader assumes from a tidy file list is that everything needed is there.
 */
'use strict';

(function () {

  var API = '/api/portage';

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
              "'": '&#39;'}[c];
    });
  }

  function el(html) {
    var d = document.createElement('div');
    d.innerHTML = html;
    return d.firstElementChild;
  }

  async function getJSON(url) {
    var r = await fetch(url, {cache: 'no-store'});
    var body = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
    return body;
  }

  async function postJSON(url, obj) {
    var r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(obj)
    });
    var body = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
    return body;
  }

  // ------------------------------------------------------- subject discovery
  //
  // READ-ONLY over `window.B`. A missing or half-built state is normal (the
  // panel can be opened before a model finishes loading), so every access is
  // defensive and the answer for "nothing yet" is an empty list plus a message
  // -- never a guess at what the user might have meant.
  function subjects() {
    var B = window.B;
    if (!B) return [];
    var out = [];
    if (B.mode === 'model') {
      if (B.adhoc && B.adhoc.mesh) {
        out.push({asset: B.adhoc.mesh, label: stemLabel(B.adhoc.mesh)});
      } else if (B.model && B.model.data) {
        var d = B.model.data;
        var label = (d.label || d.key || d.id || 'model');
        if (d.mesh) out.push({asset: d.mesh, label: label});
        else if (d.id) out.push({asset: String(d.id), label: label});
      }
      return out;
    }
    // Character mode: one tier-1 entry per FILLED slot, in slot order so the
    // pop-out reads in the same order as the rail beside it.
    var order = (B.slots || []).map(function (s) { return s.name; });
    var names = Object.keys(B.loadout || {});
    names.sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    names.forEach(function (slot) {
      var v = B.loadout[slot];
      if (!v || (!v.mesh && !v.id)) return;
      out.push({asset: v.mesh || String(v.id),
                label: slot + ' ' + (v.id || '')});
    });
    return out;
  }

  function stemLabel(path) {
    var base = String(path).replace(/\\/g, '/').split('/').pop();
    return base.replace(/\.[^.]+$/, '');
  }

  // ------------------------------------------------------------------- state
  //
  // `override` is a caller-supplied subject list, set by `open({subjects:...})`
  // and cleared on close. It exists because `subjects()` reads `window.B`, and
  // the Effects Viewer has no `B`: its subject is an EFFECT, which is not a
  // file and is not in any loadout. The alternative -- teaching `subjects()`
  // about a fourth page -- would put page-specific knowledge in the one
  // function that is supposed to have none.
  // `nameTouched` is not decoration and it is not a guess at intent. The Name
  // field is PRE-FILLED with the computed default, so "the field holds a
  // name" cannot by itself distinguish a name the user chose from one this
  // file wrote a moment ago -- and the two have to behave differently. A
  // typed name wins for every button on the panel; an untouched default is
  // re-derived per export, so clicking Export on `body` and then on `weapon`
  // in a three-subject batch writes two bundles instead of clobbering one.
  // `layout` is the BUNDLE STRUCTURE the user picked: 'original' keeps the
  // client's own folders and numbers, 'readable' files each export under
  // its researched name with the original number in parentheses. It is
  // panel state and not a re-resolve, because every row already carries
  // BOTH paths -- so the toggle redraws instantly and the two spellings
  // cannot disagree about which file they describe.
  var state = {plan: null, sel: null, busy: false, override: null,
               nameTouched: false, layout: 'grouped'};

  /* The subjects this panel is currently about: the caller's list if one was
   * given, else whatever the Builder holds. ONE accessor, so the export path
   * and the plan path cannot come to disagree about what is being exported --
   * a plan drawn from an override and an export posted from `window.B` would
   * write a bundle that does not match the list the user just read. */
  function activeSubjects() {
    return state.override || subjects();
  }

  /* THE EQUIPPED COMPOSITION, by its STABLE SLOT KEYS -- `body`, `r_weapon`,
   * `l_weapon` -- never by the slot's display name. Which motion set animates
   * a body is decided by the weapon in its hand, and that pairing is the one
   * thing the server cannot recover from a flat subject list: it receives
   * paths and appearance ids with no record of which hand they came from.
   *
   * Returns '' when there is no body (the Model Viewer and the Effects Viewer
   * have no loadout at all, and an override list is somebody else's subjects).
   * The panel is never worse off for sending nothing -- the server then
   * reports the motion BINDING exactly as it did before this existed. */
  function loadoutQuery() {
    var B = window.B;
    if (state.override || !B || !B.loadout) return '';
    function id(slot) {
      var v = B.loadout[slot];
      return v && v.id ? String(v.id) : '';
    }
    var body = id('body');
    if (!body) return '';
    var q = '&body=' + encodeURIComponent(body);
    /* AND THE SUBJECT STRING THE BODY WAS SENT AS, because the two are
     * different numbers and the server cannot bridge them.
     *
     * `subjects()` sends `v.mesh || v.id`, so the body's subject is usually
     * a MESH PATH -- and appearance 002132300 resolves to mesh 002135000.
     * The server was left to work out which subject the loadout's body was,
     * and matching an appearance id against a mesh stem NEVER matches, so
     * the loadout was dropped and every composition reported "no loadout
     * given". The panel already knows which subject it built from the body
     * slot; saying so is one parameter and no inference. */
    var bv = B.loadout.body;
    var basset = bv ? (bv.mesh || String(bv.id || '')) : '';
    if (basset) q += '&bodyAsset=' + encodeURIComponent(basset);
    if (id('r_weapon')) q += '&right=' + encodeURIComponent(id('r_weapon'));
    if (id('l_weapon')) q += '&left=' + encodeURIComponent(id('l_weapon'));
    return q;
  }

  /* The same thing as an object, for the export POST. ONE source, two
   * spellings: a query for the GET and a dict for the POST body. */
  function loadoutObj() {
    var q = loadoutQuery();
    if (!q) return null;
    var out = {};
    q.replace(/^&/, '').split('&').forEach(function (kv) {
      var p = kv.split('=');
      out[p[0]] = decodeURIComponent(p[1] || '');
    });
    return out;
  }

  /* `subject=` for a file, `effect=` for an effect. NEVER sniffed from the
   * value: an effect name and a logical path are both strings and `Blood` is
   * a legal spelling of either, so the caller says which it means and this
   * only transcribes it. */
  function planQuery(subs) {
    return subs.map(function (s) {
      var label = 'label=' + encodeURIComponent(s.label || '');
      return s.effect
        ? 'effect=' + encodeURIComponent(s.effect) + '&' + label
        : 'subject=' + encodeURIComponent(s.asset) + '&' + label;
    }).join('&');
  }

  // -------------------------------------------------------------- the frame
  function ensureFrame() {
    if (document.getElementById('pp-overlay')) return;
    if (!document.querySelector('link[href="/ui/portagepanel.css"]')) {
      var link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = '/ui/portagepanel.css';
      document.head.appendChild(link);
    }
    var ov = el(
      '<div id="pp-overlay" hidden>' +
        '<div id="pp-frame" role="dialog" aria-label="Import/Export tools">' +
          '<div class="pp-head">' +
            '<h2>Import/Export tools</h2>' +
            '<span id="pp-sub" class="pp-mut"></span>' +
            '<span class="pp-grow"></span>' +
            '<button id="pp-refresh" class="pp-btn small">Refresh</button>' +
            '<button id="pp-close" class="pp-btn small">Close</button>' +
          '</div>' +
          '<div class="pp-body" id="pp-body"></div>' +
          '<div class="pp-foot">' +
            '<button id="pp-all" class="pp-btn primary">Export all to .zip</button>' +
            '<button id="pp-sel" class="pp-btn">Export selected</button>' +
            // THE STRUCTURE PICKER. It changes where files sit INSIDE
            // the zip and nothing else -- every manifest record's
            // `dest` is the logical path either way, so a readable
            // bundle reimports to exactly the same places as an
            // original one. Said in the title so the user does not
            // have to trust that it is safe.
            '<label class="pp-mut" for="pp-layout">Structure</label>' +
            '<select id="pp-layout" class="pp-btn small" ' +
              'title="Where files sit inside the zip. Reimport is ' +
              'unaffected either way &mdash; the manifest carries each ' +
              'file&#39;s real destination.">' +
              '<option value="grouped">grouped by type</option>' +
              '<option value="source">original folders</option>' +
              '<option value="readable">readable names</option>' +
            '</select>' +
            // THE NAME FIELD SITS WITH THE EXPORT BUTTONS, NOT WITH REIMPORT.
            // The `#pp-bundle` select further along is the list of bundles
            // ALREADY ON DISK and it is the reimport picker; it is not, and
            // must not become, the name of the bundle about to be written.
            // Those are opposite directions and one control cannot be both.
            '<label class="pp-mut" for="pp-name">Name</label>' +
            '<input id="pp-name" class="pp-name" type="text" ' +
              'spellcheck="false" autocomplete="off" ' +
              'title="The bundle\'s file name. Leave it as it came and you ' +
              'get the subject and today\'s date. A name that names a ' +
              'directory, a drive, a parent, or a Windows device is REFUSED ' +
              'by the server, which says what it refused.">' +
            '<span class="pp-mut pp-ext">.zip</span>' +
            '<span class="pp-grow"></span>' +
            '<label class="pp-mut" for="pp-bundle">Reimport</label>' +
            '<select id="pp-bundle" class="pp-btn"></select>' +
            '<button id="pp-import" class="pp-btn">Import into stage</button>' +
          '</div>' +
          // WHERE THE BUNDLES ACTUALLY ARE. The owner asked for it directly,
          // and the panel had every reason to show it already: both
          // `/bundles` and `/plan` have always returned the directory, and
          // nothing rendered it. A name field and a picker with no path is a
          // file manager that will not say which folder it is looking at --
          // "Export all" wrote 18 MB somewhere and the only way to find out
          // where was to read the server's source.
          //
          // It is SELECTABLE text, not a tooltip: the first thing anyone
          // does with a path is copy it into a shell or Explorer.
          '<div class="pp-where" id="pp-where">' +
            '<span class="pp-mut">Folder</span> ' +
            '<code id="pp-dir" class="pp-dir" ' +
              'title="Where bundles are written and read. Change it with ' +
              '`export_dir` in the per-user config.">…</code>' +
          '</div>' +
        '</div>' +
      '</div>');
    document.body.appendChild(ov);
    ov.addEventListener('click', function (e) {
      if (e.target === ov) close();
    });
    document.getElementById('pp-close').onclick = close;
    document.getElementById('pp-refresh').onclick = function () { load(); };
    // A REDRAW, NOT A RELOAD: every row already carries both spellings, so
    // flipping the structure never re-resolves the composition.
    //
    // THE TICKS ARE CARRIED ACROSS BY HAND, and they have to be. `render()`
    // rebuilds `pp-body` wholesale and `checkedRows()` reads the live DOM, so
    // a plain redraw silently empties a selection the user may have spent
    // real time on -- ~60 motion rows is exactly the case where that hurts.
    // Restored by the same (asset, dest) pair that identifies a row
    // everywhere else here, so a shared file under two assets keeps the one
    // the user actually ticked.
    document.getElementById('pp-layout').onchange = function () {
      state.layout = this.value;
      if (!state.plan) return;
      // Keyed by JSON.stringify of the pair rather than by a joined string:
      // a separator character is a guess about what cannot appear in a path,
      // and this needs none.
      var was = checkedRows().map(function (p) { return JSON.stringify(p); });
      render();
      if (!was.length) return;
      var want = {};
      was.forEach(function (k) { want[k] = 1; });
      document.querySelectorAll('#pp-body .pp-sel').forEach(function (c) {
        var k = JSON.stringify([c.getAttribute('data-asset'),
                                c.getAttribute('data-dest')]);
        if (want[k]) c.checked = true;
      });
    };
    document.getElementById('pp-all').onclick = function () {
      // scope null = the whole plan, so the name is the plan's own default:
      // the subject when there is one, the batch form when there is not.
      exportBatch(null, 'the whole batch', null);
    };
    document.getElementById('pp-sel').onclick = function () {
      var picked = checkedRows();
      if (!picked.length) { log('nothing ticked', 'warn'); return; }
      // A SELECTION IS NOT THE WHOLE BUNDLE and its name says so. Writing a
      // partial export under the plan's own default would overwrite the full
      // bundle with a subset carrying the same name.
      var subs = nameSubjects();
      exportBatch(picked, picked.length + ' selected file(s)',
                  planBase(subs.length, subs[0]) + '-' +
                  picked.length + '-selected');
    };
    document.getElementById('pp-import').onclick = doImport;
    // ONE listener, and it only ever sets the flag. It deliberately does not
    // validate: client-side sanitising a name the SERVER must sanitise anyway
    // is a second answer to a settled question, and the direction that drift
    // takes is the panel accepting a name the server then refuses -- or worse,
    // quietly rewriting one the server would have accepted.
    document.getElementById('pp-name').addEventListener('input', function () {
      state.nameTouched = true;
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !ov.hidden) close();
    });
  }

  /* `open()` uses the Builder's loadout, as it always has.
   * `open({subjects: [{effect: 'M_Fire', label: 'M_Fire'}], title: '...'})`
   * opens it on an explicit list -- the Effects Viewer's caller. */
  function open(opts) {
    ensureFrame();
    state.override = (opts && opts.subjects && opts.subjects.length)
      ? opts.subjects.slice() : null;
    var h = document.querySelector('#pp-frame .pp-head h2');
    if (h) h.textContent = (opts && opts.title) || 'Import/Export tools';
    document.getElementById('pp-overlay').hidden = false;
    // Filled BEFORE the plan comes back, and again from `render()` once it
    // has. The field is never empty and never stale: an empty Name box reads
    // as "naming is optional and there is no default", which is the opposite
    // of what the owner asked for.
    syncNameField();
    load();
    loadBundles();
  }

  function close() {
    var ov = document.getElementById('pp-overlay');
    if (ov) ov.hidden = true;
    // Cleared on close, not on open: a stale override would silently make the
    // NEXT open (from the Builder's own button, which passes nothing) export
    // the previous page's subject under the previous page's label.
    state.override = null;
    // The typed name is cleared for the same reason and it is the same bug.
    // A name typed for effect 410039 that survived into the next open would
    // write the Builder's composition to `410039.zip` -- a bundle whose name
    // is a confident, checkable, wrong claim about what is inside it.
    state.nameTouched = false;
  }

  // ------------------------------------------------------------------ render
  async function load() {
    var subs = activeSubjects();
    var body = document.getElementById('pp-body');
    if (!subs.length) {
      body.innerHTML = '<p class="pp-mut">Nothing is on the stage yet. ' +
        'Equip a slot (or open a model) and reopen this panel.</p>';
      state.plan = null;
      syncNameField();
      return;
    }
    body.innerHTML = '<p class="pp-mut">resolving ' + subs.length +
                     ' subject(s) and everything that goes with them…</p>';
    var qs = planQuery(subs) + loadoutQuery();
    try {
      state.plan = await getJSON(API + '/plan?' + qs);
    } catch (e) {
      body.innerHTML = '<p class="pp-mut">could not build the plan: ' +
                       esc(e.message) + '</p>';
      syncNameField();
      return;
    }
    render();
  }

  function render() {
    var p = state.plan;
    var body = document.getElementById('pp-body');
    document.getElementById('pp-sub').textContent =
      p.assets.length + ' subject(s) · ' + p.fileCount + ' file(s)' +
      (p.definitionCount ? ' + ' + p.definitionCount + ' definition row(s)' : '') +
      ' to export';
    var html = honesty(p);
    p.assets.forEach(function (a) { html += asset(a, p); });
    html += '<div class="pp-log" id="pp-logbox"><span class="pp-mut">' +
            'Bundles are written to ' + esc(p.bundleDir || '') +
            '</span></div>';
    body.innerHTML = html;
    // A row is identified by the PAIR (tier-1 key, dest), never by dest
    // alone: a SHARED file's dest matches a row under every asset that
    // carries it, so a dest-only Export on `body` also wrote the weapon's
    // folder. Measured; see `portageplan.export_items`.
    body.querySelectorAll('[data-export-row]').forEach(function (b) {
      b.onclick = function () {
        exportBatch([[b.getAttribute('data-asset'),
                      b.getAttribute('data-export-row')]], 'one file',
                    b.getAttribute('data-export-row'));
      };
    });
    body.querySelectorAll('[data-export-asset]').forEach(function (b) {
      b.onclick = function () {
        var key = b.getAttribute('data-export-asset');
        var a = p.assets.filter(function (x) { return x.key === key; })[0];
        exportAsset(a);
      };
    });
    // PER-SECTION SELECT ALL / SELECT NONE. Scoped to the button's OWN
    // `.pp-group`, and it reaches rows only through `input.pp-sel` -- so a row
    // the panel drew without a checkbox is not merely skipped by a filter, it
    // is unreachable. That is the difference between "we remembered to exclude
    // the definition rows" and "there is nothing here to exclude".
    body.querySelectorAll('[data-sel-on]').forEach(function (b) {
      b.onclick = function () {
        var box = b.closest('.pp-group');
        if (!box) return;
        var on = b.getAttribute('data-sel-on') === '1';
        var boxes = box.querySelectorAll('input.pp-sel');
        Array.prototype.forEach.call(boxes, function (c) { c.checked = on; });
        // The COUNT is reported, not just the action. The section's label
        // says "Select all 4"; if this reaches 3, the two disagree and the log
        // is where that becomes visible instead of staying a quiet miscount.
        log((on ? 'ticked ' : 'unticked ') + boxes.length + ' of ' +
            b.getAttribute('data-sel-count') + ' row(s) under ' +
            b.getAttribute('data-sel-title'));
      };
    });
    syncNameField();
  }

  /* The banner. DRAWN ALWAYS, including when the numbers are zero -- a
   * companion list with no "and N I could not resolve" line reads as an
   * exhaustive one, which is what `bulletproof` forbids. */
  function honesty(p) {
    var lim = (p.limits || []).map(function (L) {
      return '<li>' + esc(L) + '</li>';
    }).join('');
    // THE DEFINITION LINE IS DRAWN ALWAYS, IN BOTH STATES. A bundle that
    // carries a table row and one that carries none look identical in a list
    // of files, and the state a reader assumes from a tidy file list is
    // "everything needed is here". Both sentences come from the server
    // (`portageplan.DEFINITION_CARRIED` / `DEFINITION_NONE`) so the panel and
    // the zip's README cannot come to disagree about what import will do.
    // The class is picked FIRST and on its own line. `dline` itself then holds
    // no condition at all, which is what makes "this line is drawn in both
    // states" a structural fact a source gate can check
    // (`test_portage_effect.ThePageIsWiredToTheRightSubject`) rather than a
    // claim in a comment. A ternary here would be about the COLOUR, but a
    // reader -- and a checker -- cannot tell a colour ternary from an emission
    // ternary at a glance, and the emission one is the bug.
    var n = p.definitionCount || 0;
    var dcls = n ? 'pp-defline carried' : 'pp-defline';
    var dline =
      '<div class="' + dcls + '">' +
      '<b>' + n + ' definition row(s)</b> — ' + esc(p.definitionNote || '') +
      '</div>';
    return '<div class="pp-honesty">' +
      '<b>' + p.fileCount + ' file(s) can be exported</b>' +
      ' · <b>' + p.absentCount + '</b> declared but absent from this install' +
      ' · <b>' + p.unresolvedCount + '</b> I could not resolve' +
      ' · ' + p.noteRows + ' row(s) describe a relationship and are not files.' +
      dline +
      '<div class="pp-mut" style="margin-top:4px">Zip layout: <span class="pp-mono">' +
      esc(p.layout || '') + '</span> — the structure below is the structure ' +
      'on disk.</div>' +
      (lim ? '<div class="pp-mut" style="margin-top:6px">Not enumerated by ' +
             'the resolver:</div><ul class="pp-limits">' + lim + '</ul>' : '') +
      '</div>';
  }

  function asset(a, p) {
    var head =
      '<div class="pp-asset-head">' +
        '<span class="name">' + esc(a.label) + '</span>' +
        '<span class="path pp-mono">' + esc(a.subject || a.requested) + '</span>' +
        '<span class="pp-grow"></span>' +
        '<span class="pp-mut">' + a.fileCount + ' file(s)' +
          (a.definitionCount ? ' · ' + a.definitionCount + ' definition' : '') +
          (a.absentCount ? ' · ' + a.absentCount + ' absent' : '') +
          (a.unresolvedCount ? ' · ' + a.unresolvedCount + ' unresolved' : '') +
        '</span>' +
        // A DEFINITION ALONE IS ENOUGH TO OFFER AN EXPORT. An effect whose
        // layer art this install does not ship still has a definition worth
        // sending, and "0 file(s), no button" would read as "there is nothing
        // here" when there is.
        (a.fileCount || a.definitionCount
          ? '<button class="pp-btn small" data-export-asset="' + esc(a.key) +
            '">Export ▾</button>'
          : '') +
      '</div>';
    if (a.error) {
      return '<div class="pp-asset err">' + head +
             '<div class="pp-row"><div class="pp-why">' + esc(a.error) +
             '</div></div></div>';
    }
    var groups = a.groups.map(function (g) { return group(g, a.key); }).join('');
    var note = a.measured ? '' :
      '<div class="pp-row note"><div class="pp-why">This subject is ' +
      'UNMEASURED — the resolver refused it, so the groups below are not ' +
      '"nothing goes with it".</div></div>';
    return '<div class="pp-asset">' + head + note + groups + '</div>';
  }

  /* CAN THIS ROW BE TICKED? THE ONE PREDICATE, AND THAT IS THE POINT.
   *
   * `row()` emits a checkbox exactly when this says so, and a section's
   * select-all counts exactly what this returns. Two predicates would drift,
   * and the drift has a direction: a section control that reports having
   * ticked rows it never touched, because the rows it counted have no
   * checkbox to tick. The rows that must stay out, and why each one is a
   * separate false claim rather than an oversight:
   *
   *   a DEFINITION row  -- `definitionRow` draws "always included" and the
   *                        server's `definition_items` takes no `select`, so
   *                        there is nothing for "leave this out" to mean.
   *                        Counting it would promise a choice that does not
   *                        exist.
   *   an ABSENT row     -- declared but not in this install. There is no file
   *                        to put in the zip; it travels as a manifest record
   *                        with `absent:true` so a reimport can tell "never
   *                        there" from "deleted".
   *   a RELATIONSHIP row -- an effect layer named by an appearance row, a
   *                        motion binding. `manifested:false`; "not a file"
   *                        is the tool column's whole answer.
   *
   * All three arrive from the server already decided -- `r.file` is
   * `portageplan.PlanRow.file`, and it is already FALSE for all three -- so
   * this transcribes one answer rather than computing a second one.
   *
   * IT IS DELIBERATELY THE BARE FIELD, and `&& !r.definition && !r.absent`
   * was written here first and then taken out. Those clauses are redundant
   * (`PlanRow.file` is only ever set in the `path and s.present` branch, which
   * returns before any absent or definition row is built) -- and redundancy
   * here is not free. It COSTS detection: with them, swapping `r.file` for
   * `r.manifested` -- which is TRUE on an absent row, the one shape that
   * separates the two fields -- is masked by `!r.absent` and the gate goes
   * green on the mutant. The narrower predicate is the more falsifiable one,
   * so the exclusions live in this comment and in
   * `tests/test_portagepanel.py`, which evaluates this expression against
   * real `PlanRow.as_dict()` shapes, rather than in belt-and-braces that
   * quietly disarm the test. */
  function isSelectable(r) {
    return !!(r && r.file);
  }

  function selectableRows(g) {
    return ((g && g.rows) || []).filter(isSelectable);
  }

  /* ONE SECTION: its title, its select-all/select-none, and its rows.
   *
   * THE CONTROL IS DRAWN ONLY WHERE THERE IS SOMETHING FOR IT TO DO. A
   * section of definition rows or absent rows gets the sentence instead --
   * silence there would read as "the control is missing", and the user would
   * go looking for the bug rather than for the reason. */
  function group(g, key) {
    var n = selectableRows(g).length;
    var ctl = n
      ? '<span class="pp-group-sel">' +
          '<button class="pp-btn tiny" data-sel-on="1" data-sel-count="' + n +
            '" data-sel-title="' + esc(g.title) + '">Select all ' + n +
            '</button>' +
          '<button class="pp-btn tiny" data-sel-on="0" data-sel-count="' + n +
            '" data-sel-title="' + esc(g.title) + '">Select none</button>' +
        '</span>'
      : '<span class="pp-group-sel pp-mut">nothing here can be exported on ' +
        'its own</span>';
    return '<div class="pp-group">' +
      '<div class="pp-group-title">' + esc(g.title) + ctl + '</div>' +
      g.rows.map(function (r) { return row(r, key); }).join('') +
    '</div>';
  }

  /* WHERE THIS ROW LANDS IN THE BUNDLE, under the structure now selected.
   *
   * Both spellings came from the server on the SAME row, so this picks one
   * rather than deriving anything. A row with no researched name has no
   * `readablePath`, and it falls back to the grouped path -- which is honest:
   * 126 of the 192 action codes a loadout resolves have no established name,
   * and a readable tree that invented one would be confidently wrong about
   * what a motion is. */
  function bundlePath(r) {
    if (state.layout === 'source' && r.sourcePath) return r.sourcePath;
    if (state.layout === 'readable' && r.readablePath) return r.readablePath;
    return r.exportPath || '';
  }

  /* ONE ROW. The shared and absent branches are the reason this function is
   * not a template literal with three ternaries: each carries an explanation,
   * and dropping either is the failure the panel exists to prevent. */
  function row(r, key) {
    if (r.definition) return definitionRow(r);
    var cls = 'pp-row';
    if (r.shared) cls += ' shared';
    if (r.absent) cls += ' absent';
    if (!r.manifested && !r.absent) cls += ' note';

    // A real path when there is one; the declared id when the closure knew
    // only that; the label when the row is a relationship (a motion binding
    // has neither). Never "id " with nothing after it.
    var name = esc(r.path || (r.assetId ? 'id ' + r.assetId : r.label));
    var chips = '';
    if (r.shared) {
      chips += '<span class="pp-shared-chip">SHARED WITH ' +
               esc((r.sharedWith || []).join(', ')) + '</span>';
    }
    if (r.absent) chips += '<span class="pp-absent-chip">ABSENT</span>';

    var why = '';
    if (r.shared) {
      // The sentence comes from the server (`portageplan.SHARED_WARNING`) so
      // it cannot drift from what `portage.import_batch` actually does.
      why += '<div class="pp-shared-why">' +
             esc(state.plan.sharedWarning) +
             ' Also under: ' + esc((r.sharedWith || []).join(', ')) + '.</div>';
    }
    if (r.why) why += '<div class="pp-why">' + esc(r.why) + '</div>';
    else if (r.note) why += '<div class="pp-why">' + esc(r.note) + '</div>';

    var zip = bundlePath(r)
      ? '<div class="zip">→ ' + esc(bundlePath(r)) + '</div>' : '';

    var tool = r.tool
      ? '<div class="tool' + (r.tool === 'view only' ? ' viewonly' : '') + '">' +
        esc(r.tool) + '</div>'
      : '<div class="tool pp-mut">—</div>';

    var btn = r.file
      ? '<button class="pp-btn small" data-asset="' + esc(key) +
        '" data-export-row="' + esc(r.dest) + '">Export</button>'
      : '<span class="pp-mut" style="font-size:11px">no export</span>';

    // THE SAME PREDICATE THE SECTION CONTROL COUNTS. Not `r.file` spelled out
    // a second time: the section's "Select all 4" and the four checkboxes it
    // ticks have to be the same four, and one expression is the only way that
    // is true by construction rather than by coincidence.
    var tick = isSelectable(r)
      ? '<input type="checkbox" class="pp-sel" checked data-asset="' +
        esc(key) + '" data-dest="' + esc(r.dest) + '">'
      : '';

    // The label line repeats the resolver's words for the row. Where the row
    // has no path and no id the LABEL is already the name above, so it is
    // dropped here rather than printed twice.
    var bits = [];
    if (r.path || r.assetId) bits.push(r.label);
    if (r.source) bits.push(r.source);
    if (r.form) bits.push(r.form);

    return '<div class="' + cls + '">' +
      '<div>' + tick + '<span class="file">' + name + '</span>' + chips +
        '<div class="label">' + esc(bits.join(' · ')) + '</div>' +
        why + zip + '</div>' +
      tool + '<div>' + btn + '</div>' +
    '</div>';
  }

  /* A DEFINITION ROW. Deliberately not the file row with two branches removed:
   * three of its four controls would be wrong here, and each wrongness is a
   * specific false claim.
   *
   *   no checkbox   -- there is nothing for "leave this out" to mean. An
   *                    effect exported without its definition arrives on the
   *                    far install with the art present and the effect
   *                    UNDEFINED, and nothing in the zip would say so. The
   *                    server enforces the same thing (`definition_items` has
   *                    no `select`), so a user who unticked it in a future UI
   *                    would still get it -- and a checkbox that does nothing
   *                    is a worse lie than no checkbox.
   *   no Export     -- the per-row Export button posts a `dest`, and this
   *                    row's `dest` is the WHOLE TABLE. A click that "worked"
   *                    would put all 3,391 (5517) / 5,313 (6609) definitions
   *                    in the zip.
   *   the file name -- is the table, and the table is not what travels. The
   *                    row is what travels, so the row's key is what is shown
   *                    first and the table is shown as where it came OUT of.
   */
  function definitionRow(r) {
    var twin = r.note && /NOT read/.test(r.note);
    return '<div class="pp-row definition">' +
      '<div>' +
        '<span class="file">' + esc(r.label) + '</span>' +
        '<span class="pp-def-chip">TABLE ROW, NOT A FILE</span>' +
        (twin ? '<span class="pp-absent-chip">TWIN</span>' : '') +
        '<div class="label">out of <span class="pp-mono">' +
          esc(r.tableFile || r.dest) + '</span>' +
          (r.tableForm ? ' (' + esc(r.tableForm) + ')' : '') +
          (r.note ? ' · ' + esc(r.note) : '') + '</div>' +
        (r.why ? '<div class="pp-why">' + esc(r.why) + '</div>' : '') +
        (bundlePath(r) ? '<div class="zip">→ ' + esc(bundlePath(r)) + '</div>' : '') +
      '</div>' +
      '<div class="tool">merged on import</div>' +
      '<div><span class="pp-mut" style="font-size:11px">always included' +
      '</span></div>' +
    '</div>';
  }

  /* The ticked rows, as (tier-1 key, dest) PAIRS. See the note on the
   * per-row button: a dest alone does not identify a row once a file is
   * shared, and the shared file is the one this panel is about. */
  function checkedRows() {
    return Array.prototype.map.call(
      document.querySelectorAll('#pp-body .pp-sel:checked'),
      function (c) {
        return [c.getAttribute('data-asset'), c.getAttribute('data-dest')];
      });
  }

  // ------------------------------------------------------------------ export
  //
  // THE CONFIRMATION IS PART OF REQUIREMENT 1. The owner asked that the user
  // see the shared-file consequence "at the moment they click Export on
  // `body`". The row already carries it; this repeats it in the confirm, with
  // the other asset named, because that is the click that writes the file.
  function exportAsset(a) {
    if (!a) return;
    var dests = [];
    var sharedLines = [];
    a.groups.forEach(function (g) {
      g.rows.forEach(function (r) {
        if (!r.file) return;
        dests.push([a.key, r.dest]);
        if (r.shared) {
          sharedLines.push('  ' + r.path + '\n      also used by: ' +
                           (r.sharedWith || []).join(', '));
        }
      });
    });
    if (!dests.length && a.definitionCount) {
      // A tier-1 key with an EMPTY dest: it names this asset and no file. The
      // server reads the key (`portageplan._selected_keys`) and ships this
      // subject's definition row while matching no file row -- which is
      // exactly "export the effect whose art this install does not have".
      exportBatch([[a.key, '']], a.label, a.label);
      return;
    }
    if (!dests.length) { log('nothing exportable under ' + a.label, 'warn'); return; }
    if (sharedLines.length) {
      var msg = 'Exporting "' + a.label + '".\n\n' +
        sharedLines.length + ' of these file(s) are SHARED with another ' +
        'asset in this batch:\n\n' + sharedLines.join('\n') + '\n\n' +
        state.plan.sharedWarning + '\n\nExport anyway?';
      if (!window.confirm(msg)) { log('export cancelled', 'warn'); return; }
    }
    exportBatch(dests, a.label, a.label);
  }

  async function exportBatch(select, what, scope) {
    if (state.busy) return;
    state.busy = true;
    log('exporting ' + what + '…');
    try {
      var res = await postJSON(API + '/export', {
        subjects: activeSubjects(),
        select: select,
        zip: true,
        name: bundleName(scope),
        loadout: loadoutObj(),
        layout: state.layout
      });
      log('wrote ' + res.fileCount + ' file(s) to ' + res.dest, 'ok');
      // THE DEFAULT NAME IS DATE-STAMPED, NOT TIMESTAMPED -- that is what the
      // owner asked for ("name+date") and it is what makes the name typeable
      // and readable. The cost is that two exports of the same subject on the
      // same day collide, so the server reports the collision and the user is
      // told a bundle was replaced. Silence here would be the panel losing a
      // bundle without saying so, which is the one outcome a name field must
      // not introduce.
      if (res.overwrote) {
        log('  NOTE: a bundle named ' + res.name + ' was already in that ' +
            'folder and has been REPLACED. Type a different name above to ' +
            'keep both.', 'warn');
      }
      // WHAT THE BUNDLE DID ABOUT THE DEFINITION, EVERY TIME, IN BOTH STATES.
      // A log that mentioned it only when one travelled would make its
      // silence the answer, and the answer a reader takes from silence after
      // a successful export is "everything is in there".
      (res.definitions || []).forEach(function (d) {
        log('  definition: ' + d.table + ' row "' + d.key + '" out of ' +
            d.dest + ' → ' + d.export, 'ok');
      });
      if (!res.definitionCount) log('  ' + (res.definitionNote || ''), 'warn');
      (res.shared || []).forEach(function (d) {
        log('  shared: ' + d + ' — it is recorded once per asset that uses ' +
            'it, and they all reimport to the same destination', 'warn');
      });
      (res.absent || []).forEach(function (d) {
        log('  absent: ' + d + ' — recorded in the manifest with no file, so ' +
            'a reimport can tell "never there" from "deleted"', 'warn');
      });
      if (res.unresolvedCount) {
        log('  and ' + res.unresolvedCount + ' satellite(s) I could not ' +
            'resolve — see the banner above', 'warn');
      }
      if (res.download) {
        var a = document.createElement('a');
        a.href = res.download;
        a.textContent = 'download ' + res.name;
        a.setAttribute('download', res.name);
        var box = document.getElementById('pp-logbox');
        if (box) { box.appendChild(document.createTextNode('  ')); box.appendChild(a); }
      }
      loadBundles();
    } catch (e) {
      log('export failed: ' + e.message, 'bad');
    } finally {
      state.busy = false;
    }
  }

  // --------------------------------------------------------- naming a bundle
  //
  // The owner's ask: "When we export the Zip, we should be able to name it
  // rather than it just being a time/date bundle. And if someone doesn't name
  // it, it should default to name+date."
  //
  // WHAT THE DEFAULT IS, AND THE ONE CASE THAT IS NOT "name+date"
  // ------------------------------------------------------------
  // One subject -> `<subject>-<YYYY-MM-DD>`, e.g. `410039-2026-09-08`. That is
  // the ask, literally.
  //
  // SEVERAL subjects -> `comod-batch-<N>-subjects-<YYYY-MM-DD>`. There is no
  // "the name" of a three-slot composition, and the tempting answer -- name it
  // after the first subject -- produces a file called `body-130030.zip` that
  // also contains the weapon and the head. A wrong name on an archive is not a
  // cosmetic problem: it is the only thing the far end reads before opening
  // it, and `body-130030.zip` is a claim a reader has no reason to doubt and
  // no cheap way to check. So the batch name carries the COUNT and names no
  // subject at all, which is the honest thing a name can say here, and it is
  // why `batchBase` is a SEPARATE function taking only the count -- a subject
  // name is not in scope there, so this cannot regress into naming one.

  function isoDate() {
    return new Date().toISOString().slice(0, 10);
  }

  /* A label as a file name. This is CONVENIENCE, not safety -- the server
   * sanitises through `core/safepath.py` and refuses out loud, and a second
   * rule here would be a second answer to that question. What this is for is
   * that the DEFAULT should never be a name the server then refuses. */
  function slugName(text) {
    return String(text == null ? '' : text)
      .replace(/[^A-Za-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .toLowerCase()
      .slice(0, 60) || 'comod-export';
  }

  /* THE MULTI-SUBJECT NAME. Takes the count and nothing else, deliberately:
   * with no subject in scope it cannot name one, which is the requirement
   * rather than a habit. `tests/test_portagepanel.py` evaluates this
   * function's expression and asserts exactly that. */
  function batchBase(count) {
    return 'comod-batch-' + count + '-subjects';
  }

  /* The stem for the whole plan: the subject when there is one, the batch
   * form when there is not. */
  function planBase(count, firstLabel) {
    return count === 1 ? slugName(firstLabel) : batchBase(count);
  }

  /* The subjects this name is about, whether or not a plan has come back yet
   * -- the field is filled on open and the plan arrives after. */
  function nameSubjects() {
    var p = state.plan;
    if (p && p.assets && p.assets.length) {
      return p.assets.map(function (a) { return a.key || a.label; });
    }
    return activeSubjects().map(function (s) {
      return s.effect || s.label || s.asset;
    });
  }

  /* `scope` is null for "the whole plan", or a string naming the narrower
   * thing being written (one asset, one row, a selection). Either way the
   * date is appended, so two exports on different days never collide and two
   * on the same day are reported as an overwrite by the server rather than
   * silently replacing each other. */
  function defaultBundleName(scope) {
    var subs = nameSubjects();
    var base = scope ? slugName(scope) : planBase(subs.length, subs[0]);
    return base + '-' + isoDate();
  }

  /* THE NAME THAT IS ACTUALLY SENT, and the field always ends up showing it.
   * A field displaying one name while the POST carries another is the exact
   * shape of lie this panel exists to refuse, so the untouched-default branch
   * writes what it computed back into the field instead of leaving the batch
   * default sitting over a per-asset export. */
  function bundleName(scope) {
    var f = document.getElementById('pp-name');
    var typed = f ? String(f.value || '').trim() : '';
    if (state.nameTouched && typed) return typed;
    var name = defaultBundleName(scope);
    if (f) f.value = name;
    return name;
  }

  /* Called after every render, so the field tracks the subject the panel is
   * actually about. A name the user typed is never overwritten. */
  function syncNameField() {
    var f = document.getElementById('pp-name');
    if (!f) return;
    f.placeholder = defaultBundleName(null);
    if (!state.nameTouched) f.value = f.placeholder;
  }

  /** Show the folder bundles are written to and read from.
   *
   *  One writer for one element, because two places setting it is how a
   *  stale path survives a failed refresh. An empty `dir` is stated as
   *  unknown rather than left showing the previous answer. */
  function setWhere(dir) {
    var el = document.getElementById('pp-dir');
    if (!el) return;
    el.textContent = dir || '(unknown — could not reach the server)';
    el.classList.toggle('unknown', !dir);
  }

  // ------------------------------------------------------------------ import
  async function loadBundles() {
    var sel = document.getElementById('pp-bundle');
    if (!sel) return;
    try {
      var res = await getJSON(API + '/bundles');
      // The DIRECTORY IS SHOWN EVEN WHEN THE LIST IS EMPTY, and that is the
      // case it matters most in: "(no bundles yet)" beside a path is an
      // answer, while "(no bundles yet)" alone reads as a fault.
      setWhere(res.dir);
      sel.innerHTML = res.bundles.length
        ? res.bundles.map(function (b) {
            return '<option value="' + esc(b.name) + '">' + esc(b.name) +
                   '</option>';
          }).reverse().join('')
        : '<option value="">(no bundles yet)</option>';
    } catch (e) {
      sel.innerHTML = '<option value="">(could not list: ' +
                      esc(e.message) + ')</option>';
      // Say the folder is unknown rather than leaving the last one on
      // screen: a stale path beside a failed listing is a wrong answer.
      setWhere('');
    }
  }

  async function doImport() {
    var sel = document.getElementById('pp-bundle');
    var name = sel && sel.value;
    if (!name) { log('pick a bundle first', 'warn'); return; }
    log('importing ' + name + '…');
    try {
      var res = await postJSON(API + '/import', {name: name});
      (res.staged || []).forEach(function (L) { log('  STAGED   ' + L, 'ok'); });
      (res.skipped || []).forEach(function (L) { log('  skip     ' + L); });
      (res.absent || []).forEach(function (L) { log('  absent   ' + L); });
      (res.warnings || []).forEach(function (L) { log('  WARNING  ' + L, 'warn'); });
      (res.refused || []).forEach(function (L) { log('  REFUSED  ' + L, 'bad'); });
      log(res.ok
        ? 'staged into ' + res.stageDir + ' — review with `comod diff`, apply ' +
          'with `comod install`'
        : 'one or more files were refused by a format rule; nothing refused ' +
          'was staged', res.ok ? 'ok' : 'bad');
    } catch (e) {
      log('import failed: ' + e.message, 'bad');
    }
  }

  // --------------------------------------------------------------------- log
  function log(text, cls) {
    var box = document.getElementById('pp-logbox');
    if (!box) return;
    var line = document.createElement('div');
    if (cls) line.className = cls;
    line.textContent = text;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  // ---------------------------------------------------------------- launcher
  //
  // Injected rather than written into builder.html / models.html, so the only
  // edit those pages need is the one <script> line that loads this file.
  function mount() {
    // OPT-OUT, AND IT IS NOT COSMETIC. This button opens the panel on
    // `window.B`, so on a page that has no `B` -- the Effects Viewer, whose
    // subject is an effect -- it would open onto "Nothing is on the stage
    // yet" forever. A control that can only ever say nothing is worse than no
    // control: the user reads it as "export is not available here" when in
    // fact the page has its own, correctly-aimed button. Pages that own their
    // own launcher set `window.CO_PORTAGE_AUTOMOUNT = false` before loading
    // this file.
    if (window.CO_PORTAGE_AUTOMOUNT === false) return;
    if (document.getElementById('pp-open')) return;
    var head = document.querySelector('header');
    if (!head) return;
    var btn = document.createElement('button');
    btn.id = 'pp-open';
    btn.type = 'button';
    btn.textContent = 'Import/Export tools';
    btn.title = 'Every asset on this stage, its satellites grouped by type, ' +
                'the tool that opens each one, and export/import to .zip';
    btn.onclick = open;
    var share = document.getElementById('btn-share');
    if (share && share.parentNode === head) head.insertBefore(btn, share);
    else head.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

  // Exposed so a page can open the pop-out on an explicit subject list rather
  // than on whatever `window.B` holds. The Effects Viewer's "Collect & export"
  // card is the first such caller:
  //
  //   CoPortagePanel.open({subjects: [{effect: 'M_Fire', label: 'M_Fire'}],
  //                        title: 'Import/Export — effect “M_Fire”'});
  //
  // `mount()` still injects the header button on pages that HAVE a `<header>`,
  // and that button passes nothing, so the Builder's behaviour is unchanged.
  window.CoPortagePanel = {open: open, close: close, subjects: subjects};
})();
