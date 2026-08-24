/* swappage.js -- the swap page's two panes.
 *
 * SHAPE, and it is the owner's ruling rather than a preference:
 *
 *   LEFT   is the curated COmmunity Library. Always. It cannot be pointed at
 *          an install -- `/api/swap/library` takes no root. If you want to
 *          swap something you collect it first.
 *   RIGHT  is an installed private server. Today that is CCO alone; every
 *          other declared install is listed as supported-but-not-a-target so
 *          that "not offered" never looks like "not found".
 *
 * The right pane's NPC list is keyed on `ini/npc.json` -- the table the owner
 * actually edits -- and NOT on art discovery. The two disagree: on CCO the
 * model catalogue browses 62 groups while the table references 55, and they
 * overlap in 24. A page keyed on discovery silently omits 31 groups that can
 * be swapped, including one holding 12 NPCs and another holding 17.
 *
 * Nothing here computes a plan. `tools/npcsplit.py` already produces the
 * whole answer -- the allocator's reasoning, the cohort, the numbered steps,
 * the assertions, the reverse -- and this file RENDERS it. Two implementations
 * of one question is how a UI and a CLI come to disagree in front of a user.
 */
(function () {
  'use strict';

  const $ = s => document.querySelector(s);
  const el = (t, c, txt) => {
    const n = document.createElement(t);
    if (c) n.className = c;
    if (txt != null) n.textContent = txt;
    return n;
  };
  // The server answers errors as {"error": "..."}; api() rethrows the raw
  // body, so printing e.message put JSON punctuation in front of the reader at
  // the moment something had already gone wrong. Unwrap it to the sentence.
  function say(e) {
    const raw = (e && e.message) || String(e);
    try {
      const d = JSON.parse(raw);
      return d.error || d.detail || d.headline || raw;
    } catch (_) { return raw; }
  }

  async function api(url, opts) {
    // `opts` is forwarded, not ignored: the staging and install routes are
    // POSTs, and a dropped method turns a write into a silent GET that 404s
    // or, worse, reaches a same-named read route. csrf.js attaches the token
    // to same-origin POSTs on its own.
    const r = await fetch(url, opts || undefined);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  const V = {
    donor: { viewer: null, rows: [], sel: null },
    target: { viewer: null, rows: [], sel: null },
  };
  let LIB = null;        // /api/swap/library
  let TARGETS = null;    // /api/swap/targets
  let NPCSET = null;     // /api/swap/npcset
  let scope = 'one';     // 'one' = split this NPC only; 'all' = the whole set

  function makeViewer(side) {
    try {
      const v = new Viewer($('#gl-' + side), { camScope: 'swap.' + side });
      v.opts.grid = true;
      return v;
    } catch (e) {
      $('#msg-' + side).textContent = 'WebGL unavailable: ' + e.message;
      return null;
    }
  }

  /* Which install a library entry's art came from -- and, when that could not
     be answered, that the model on screen was classified with the install
     being SERVED instead.

     This is a fallback that has to announce itself. Attachment points are
     whatever `ini/RolePart.ini [Dumy]` lists, and that list is per install:
     CCO and 7878 declare 52 names, 5065/5165/5517/6609/Zephyr declare 7. A
     chunk not on the list is drawn as geometry, so art collected from CCO and
     classified under 5517 grows textured boxes -- `v_zero` sitting on the
     ground at the model's feet. Nothing fails; a short list parses fine and
     simply stops hiding sockets, which is why the only symptom is something
     that reads as bad art. */
  const PROV_UI = {
    resolved:   { cls: 'sw-ok',
                  head: p => 'Classified as ' + (p.kind || p.baseId) + ' art' },
    unrecorded: { cls: 'sw-none',
                  head: p => 'Source install not recorded' },
    unresolved: { cls: 'sw-unknown',
                  head: p => 'Collected from ' + (p.kind || p.baseId)
                             + ', which is not declared here' },
    malformed:  { cls: 'sw-fault',
                  head: p => 'This entry’s provenance record is unreadable' },
  };

  function drawProvenance(p) {
    const host = $('#donor-prov');
    if (!host) return;
    host.textContent = '';
    if (!p) return;
    const ui = PROV_UI[p.state] || PROV_UI.malformed;
    const box = el('div', 'sw-state ' + ui.cls);
    box.appendChild(el('b', null, ui.head(p)));
    const d = el('div', 'sw-detail', p.note || '');
    box.appendChild(d);
    // Named explicitly rather than left to the note, because "the install on
    // screen" is only useful if you are told WHICH install that is.
    if (p.usingServed) {
      box.appendChild(el('div', 'sw-detail',
        'Drawn with ' + (p.servedKind || 'the install on screen')
        + '’s socket list. Chunks it does not name are drawn as geometry.'));
    }
    host.appendChild(box);
  }

  async function draw(side, mesh, texture, libId) {
    const v = V[side].viewer, msg = $('#msg-' + side);
    // Cleared before the fetch, not after: a stale provenance box left
    // standing over a different model is the same lie in a smaller font.
    if (side === 'donor') drawProvenance(null);
    if (!v) return;
    if (!mesh && !libId) {
      v.clear(); v.draw();
      msg.textContent = 'nothing to draw for this selection';
      msg.style.display = '';
      return;
    }
    msg.textContent = 'loading…'; msg.style.display = '';
    let d;
    // A library entry is drawn from the LIBRARY. Its `sourceMesh` records
    // where it was collected FROM -- a path in another client -- so asking the
    // currently-open install for it fails on every entry, and reads as a
    // broken library rather than as the wrong lookup.
    const url = libId ? '/api/swap/libmesh?id=' + encodeURIComponent(libId)
                      : '/api/mesh?path=' + encodeURIComponent(mesh) + '&guesstex=1';
    try {
      d = await api(url);
    } catch (e) {
      v.clear(); v.draw();
      msg.textContent = 'could not load ' + (mesh || libId) + ': ' + say(e);
      return;
    }
    if (side === 'donor') drawProvenance(d.provenance || null);
    if (!d.meshes || !d.meshes.length) {
      v.clear(); v.draw();
      msg.textContent = d.note || 'no drawable geometry in ' + mesh;
      return;
    }
    let tex = (libId ? '' : texture) || d.guessedTexture || '';
    // Skins that live in the library are served BY the library. The `lib:`
    // prefix marks one; applyTextureBundle resolves single textures through
    // `opts.pngUrl`, so the override goes there -- a per-pair url field does
    // not exist and would be dropped without a word.
    let texOpts = {};
    if (tex.indexOf('lib:') === 0) {
      tex = tex.slice(4);
      texOpts = { pngUrl: p => '/api/swap/libtex?path=' + encodeURIComponent(p) };
    }
    v.setMeshes(d.meshes.map(m => ({ meta: m, textureKey: tex ? 'model' : null })), {});
    if (tex) {
      try { await v.applyTextureBundle([{ key: 'model', path: tex }], texOpts); }
      catch (e) { /* still draws untextured */ }
    }
    v.draw();
    msg.style.display = 'none';
  }

  /* ------------------------------------------------------------ LEFT pane */

  let repaintDonor = null;  // set by drawLibrary so the toggle can repaint

  /* -------------------------------------------- what makes a row "no parts"
   *
   * MEASURED FROM THE RENDERER, not from the library file and not from a
   * summary of it. The row prints "no parts" when `e.parts` is falsy, and
   * `e.parts` is not the library's `parts` list -- `/api/swap/library` sends
   * `len(entry["parts"] or [])`, a COUNT. So the label has always meant
   * "the collected part list is empty", and the toggle now means exactly
   * that, by calling the same predicate the label calls.
   *
   * Checked against the owner's library on 2026-08-24: 36 entries, 7 of them
   * printing "no parts" -- Exp Bonus Icon, EXP Bonus Icon, Pervade (Effects),
   * Flag w Stone Base, Gold and Blue Chest (NPCs), base-800915 (Other),
   * Minecraft Sword (Weapons). All 7 carry `"parts": []` in
   * Collection/collection.json, so the label and the data agree; an earlier
   * count of 4 with no Effects was a smaller, older library, not a different
   * meaning.
   *
   * WHAT THE COUNT CANNOT TELL APART, stated because it is the one place this
   * could go wrong later: a missing `parts` key, a null one and an empty list
   * all arrive as 0, so "we collected no parts" and "this entry never had a
   * parts field" read identically. Neither state exists in the library today
   * (0 of 36 entries), so nothing is being hidden by the collapse right now.
   */
  const hasNoParts = e => !e.parts;

  function drawLibrary() {
    const host = $('#donor-list');
    host.textContent = '';
    const kinds = $('#donor-kinds');
    kinds.textContent = '';

    if (!LIB) { host.appendChild(el('div', 'sw-note', 'loading…')); return; }

    // Three states, kept apart on purpose. An empty library is the FIRST
    // state this page is ever in and it is an invitation, not a fault; a
    // library that would not READ is a fault and says which path failed.
    if (LIB.error) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, LIB.headline || 'The library would not read.'));
      b.appendChild(el('div', null, LIB.detail || ''));
      host.appendChild(b);
      return;
    }
    if (!LIB.configured) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, LIB.headline));
      b.appendChild(el('div', null, LIB.detail || ''));
      host.appendChild(b);
      return;
    }
    if (!LIB.entries.length) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, LIB.headline));
      b.appendChild(el('div', null, LIB.detail || ''));
      host.appendChild(b);
      return;
    }

    // Category chips, from the library's own vocabulary.
    const cats = [];
    for (const e of LIB.entries) if (cats.indexOf(e.category) < 0) cats.push(e.category);
    cats.sort();
    let cat = '';
    const paint = () => {
      host.textContent = '';
      const q = ($('#donor-q').value || '').toLowerCase();
      const hideNoParts = !!($('#swap-hide-noparts') || {}).checked;
      let n = 0, hidden = 0;
      for (const e of LIB.entries) {
        if (cat && e.category !== cat) continue;
        if (q && (e.name + ' ' + e.sourceMesh).toLowerCase().indexOf(q) < 0) continue;
        // ONE question, asked in both places, for the same reason the right
        // pane's `hasIssue` exists: a filter that decides "no parts" its own
        // way is free to drift from the label the row prints beside it.
        if (hideNoParts && hasNoParts(e)) { hidden++; continue; }
        n++;
        const row = el('div', 'sw-item');
        row.appendChild(el('span', null, e.name || e.id));
        row.appendChild(el('span', 'sw-id', e.category +
          (hasNoParts(e) ? ' · no parts' : ' · ' + e.parts + ' part(s)')));
        row.onclick = () => {
          V.donor.sel = e;
          for (const x of host.querySelectorAll('.sw-item')) x.classList.remove('on');
          row.classList.add('on');
          draw('donor', e.mesh || e.sourceMesh, '', e.id);
          drawMid();
        };
        host.appendChild(row);
      }
      // The same rule the right pane's filters follow: a shortened list that
      // says nothing about what it removed reads as the whole library.
      const shown = el('div', 'sw-count');
      shown.textContent = 'showing ' + n + ' of ' + (n + hidden) +
        ' entr' + ((n + hidden) === 1 ? 'y' : 'ies') +
        (hidden ? ' — ' + hidden + ' with no parts hidden' : '') +
        ((cat || q) ? '  (within the category and search above)' : '');
      host.insertBefore(shown, host.firstChild);
      if (!n) host.appendChild(el('div', 'sw-note',
        hidden ? 'Everything that matches has no parts; the toggle above is '
                 + 'hiding all ' + hidden + '.'
               : 'no library entry matches'));
    };
    repaintDonor = paint;
    for (const c of cats) {
      const chip = el('span', 'sw-kind', c);
      chip.onclick = () => {
        cat = (cat === c) ? '' : c;
        for (const x of kinds.querySelectorAll('.sw-kind')) x.classList.remove('on');
        if (cat) chip.classList.add('on');
        paint();
      };
      kinds.appendChild(chip);
    }
    $('#donor-q').oninput = paint;
    paint();
  }

  /* ----------------------------------------------------------- RIGHT pane */

  // Which install we are targeting, and which family within it. Both are
  // explicit state: inferring the install from whichever root the viewer
  // happens to have open renders one client while labelling it another.
  let targetPath = '';
  let targetKind = 'npc';
  let WRITABLE = null;   // /api/swap/writable -- probed, not guessed
  let LASTSTAGE = null;  // what the last stage call reported
  let MODIFIED = null;   // /api/swap/modified -- comod's OWN install record
  let repaintTarget = null;  // set by drawNpcSet so the filter bar can repaint

  const TARGET_KINDS = [
    { key: 'npc', label: 'NPCs',
      note: 'table-keyed; motion ids are shared, so a swap can need a split' },
    { key: 'monster', label: 'Monsters',
      note: 'one look per monster -- 374 rows, 374 distinct types, no sharing' },
    { key: 'weapon', label: 'Weapons',
      note: 'flat equipment files, not table rows' }
  ];

  function drawTargets() {
    const kinds = $('#target-kinds');
    kinds.textContent = '';
    if (!TARGETS) return;

    const all = TARGETS.targets || [];
    const off = all.filter(t => t.offered);
    if (!off.length) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, TARGETS.headline || 'No target install.'));
      b.appendChild(el('div', null, TARGETS.detail || ''));
      kinds.appendChild(b);
      return;
    }
    // Default to the install the viewer actually has OPEN. Defaulting to the
    // first declared one instead is what made every row fail to draw: the
    // pane listed CCO's table while the renderer held another client.
    if (!targetPath) targetPath = (TARGETS.active || off[0].path);

    // A DROPDOWN, not chips: chips read as filters you can combine, and
    // exactly one install is being targeted at a time.
    const bar = el('div', 'sw-row');
    bar.appendChild(el('label', 'sw-lab', 'Private server'));
    const sel = el('select', 'sw-sel');
    for (const t of off) {
      const o = el('option', null, t.label + '  -  ' + t.path);
      o.value = t.path;
      if (t.path === targetPath) o.selected = true;
      sel.appendChild(o);
    }
    // Installs we know of but do not offer are shown DISABLED rather than
    // omitted: a name missing from a list reads as "you do not have it".
    for (const t of (TARGETS.targets || [])) {
      if (t.offered) continue;
      const o = el('option', null, t.label + '  -  not open in this viewer');
      o.value = t.path; o.disabled = true; o.title = t.why || '';
      sel.appendChild(o);
    }
    sel.onchange = () => { targetPath = sel.value; loadTargetList(); };
    bar.appendChild(sel);
    kinds.appendChild(bar);

    // The family filter: NPCs, Monsters, Weapons.
    const row = el('div', 'sw-row');
    for (const k of TARGET_KINDS) {
      const chip = el('span', 'sw-kind' + (targetKind === k.key ? ' on' : ''), k.label);
      chip.title = k.note;
      chip.onclick = () => {
        if (targetKind === k.key) return;
        targetKind = k.key;
        V.target.sel = null;
        drawTargets();
        loadTargetList();
        drawMid();
      };
      row.appendChild(chip);
    }
    kinds.appendChild(row);

    const cur = TARGET_KINDS.filter(k => k.key === targetKind)[0];
    const note = el('div', 'sw-note');
    if (!TARGETS.activeIsPrivateServer) {
      // Say which install is open and that it is not the one the pane is
      // for, rather than listing rows that cannot be drawn.
      note.appendChild(el('strong', null, TARGETS.headline || ''));
      note.appendChild(el('div', null, TARGETS.detail || ''));
    }
    note.appendChild(el('div', null, cur ? cur.note : ''));
    // The identity is a DECLARATION, not a measurement of bytes.
    const who = off.filter(t => t.path === targetPath)[0] || off[0];
    note.appendChild(el('div', null, 'identity ' + who.basis));
    kinds.appendChild(note);
  }

  async function loadTargetList() {
    const host = $('#target-list');
    host.textContent = '';
    host.appendChild(el('div', 'sw-note', 'loading ' + targetKind + '...'));
    const root = encodeURIComponent(targetPath || '');
    try {
      if (targetKind === 'npc') {
        NPCSET = await api('/api/swap/npcset?kind=npc&root=' + root);
      } else {
        // Monsters and Weapons are not table-keyed, so they come from browse
        // one look at a time, shaped into the rows this list already draws
        // with `count: 1` -- there is no group, so there is nothing to split.
        const d = await api('/api/swap/browse?kind=' +
                            encodeURIComponent(targetKind) + '&limit=1000');
        const src = d.rows || d.assets || [];
        const rows = src.map(r => ({
          group: r.label || r.key || r.id,
          groupKey: r.id, count: 1,
          members: [{ name: r.label || r.key || r.id }],
          paths: { standby_motion: r.mesh || '' },
          texture: r.texture || '',
          art: r.usable ? 'ok' : 'unresolved',
          artNote: r.reason || ''
        }));
        // Two facts a short list would otherwise hide. Browse is capped, and
        // it is keyed on ART DISCOVERY while the install's table names rows --
        // on CCO the catalogue browses 66 monsters while monster.json holds
        // 374. Showing 66 unlabelled reads as "you have 66 monsters".
        let gap = '';
        if (targetKind === 'monster') {
          try {
            const g = await api('/api/swap/npcset?kind=monster&root=' + root);
            if (g && g.tableRows && g.tableRows > rows.length) {
              gap = '  Browsing ' + rows.length + ' of the ' + g.tableRows +
                    ' rows monster.json names -- the rest have no art that ' +
                    'resolves in this install.';
            }
          } catch (e) { gap = ''; }
        }
        NPCSET = {
          groups: rows, kind: targetKind, totalRows: rows.length,
          droppedRows: 0,
          headline: rows.length ? '' : ('No ' + targetKind + ' is listed here.'),
          detail: rows.length
            ? (rows.length + ' ' + targetKind + '(s) in this install; each has '
               + 'its own art, so there is no group to split.'
               + (d.truncNote ? '  ' + d.truncNote : '') + gap)
            : (d.detail || 'The catalogue returned no rows for this kind.')
        };
      }
    } catch (e) {
      NPCSET = { error: true, groups: [], kind: targetKind,
                 headline: 'Could not read the ' + targetKind + ' list.',
                 detail: e.message };
    }
    // Before the paint, not after: the rows carry a "COMod" mark, so painting
    // first would show every row unmarked for as long as this call takes and
    // then silently change under the reader.
    await loadModified();
    drawNpcSet();
  }

  /* ---------------------------------------------- what makes a row grey
   *
   * ONE predicate, used by the dimming and by the "hide issues" toggle, so
   * the toggle cannot come to mean something other than what the eye sees.
   * It is NOT read off the CSS: `sw-dim` is applied here, from `art`, which
   * `/api/swap/npcset` computes per group by asking the open install's asset
   * root whether each of the three motion files (standby, blaze, rest)
   * resolves. `art` is:
   *
   *   ok          all three resolve
   *   partial     some do -- the standby often DRAWS PERFECTLY WELL, so a
   *               partial row is greyed but still swappable
   *   unresolved  none do
   *   unknown     the resolver THREW. Not the same as missing, and kept apart
   *               deliberately so a broken lookup cannot read as absent art.
   *
   * So "an issue resolving" is exactly `art !== 'ok'`, which is what the
   * owner's tooltip says and what the greying already showed.
   */
  const hasIssue = g => g.art !== 'ok';

  /* ------------------------------------------- what "modified" can mean
   *
   * `/api/swap/modified` answers "what has COMod changed in this install?"
   * from comod's own dated install record -- NOT "what differs from the
   * vendor's pristine client", which for a private server has no answer,
   * because a live shard's files are whatever its operator last pushed and
   * there is no original edition of them to diff against. The control is
   * labelled for what it does.
   *
   * Two signals, of different reach, and both are needed:
   *   - a row's TABLE entry differs from the copy COMod displaced  (per row)
   *   - the ART the row points at is a file COMod wrote            (per group,
   *     correctly: overwriting group 001's art changes all 38 of its NPCs)
   */
  function isModified(g, m) {
    if (!MODIFIED || !MODIFIED.recorded) return false;
    if (MODIFIED.tableAllRows) return true;
    if (m && MODIFIED.rowTypes && MODIFIED.rowTypes.indexOf(m.type) >= 0) return true;
    const files = MODIFIED.files || [];
    const paths = g.paths || {};
    for (const k in paths) {
      if (paths[k] && files.indexOf(paths[k]) >= 0) return true;
    }
    return false;
  }

  // A total order by construction. `type` is unique across CCO's 437 rows
  // today, but a comparator that RELIES on that would leave equal rows in
  // whatever order the last filter produced, and 92 groups means sorting by
  // group leaves ties of up to 49 -- a list that reshuffles under you between
  // renders. The final key is the row's position in the server's payload,
  // which is fixed for a given load, so ties can never be arbitrary.
  const cmpText = (a, b) => {
    const x = String(a || '').toLowerCase(), y = String(b || '').toLowerCase();
    return x < y ? -1 : x > y ? 1 : 0;
  };
  const cmpNum = (a, b) => {
    const x = (typeof a === 'number') ? a : Infinity;
    const y = (typeof b === 'number') ? b : Infinity;
    return x < y ? -1 : x > y ? 1 : 0;
  };

  function sortRows(rows, how) {
    // sort by name  : name, then npc.json `type`, then payload order
    // sort by group : motion group, then name, then `type`, then payload order
    const byName = (a, b) => cmpText(a.label, b.label) ||
                             cmpNum(a.type, b.type) || (a.idx - b.idx);
    const byGroup = (a, b) => cmpNum(a.g.groupKey, b.g.groupKey) || byName(a, b);
    rows.sort(how === 'group' ? byGroup : byName);
    return rows;
  }

  // One entry per NPC -- the flat list, and the default. `m` is the member;
  // `g` is the group it shares art with, kept on every row because that is
  // the warning, not decoration.
  function flatRows() {
    const out = [];
    let i = 0;
    for (const g of NPCSET.groups) {
      for (const m of (g.members || [])) {
        out.push({ g: g, m: m, idx: i++,
                   label: m.name || '(unnamed)', type: m.type });
      }
    }
    return out;
  }

  // The old view, kept: one row per group with a representative and a count.
  function groupRows() {
    return NPCSET.groups.map((g, i) => ({
      g: g, m: null, idx: i,
      label: (g.members[0] && g.members[0].name) || '(unnamed)',
      type: (g.members[0] && g.members[0].type)
    }));
  }

  // A member the server's cap dropped is an NPC that cannot be reached from
  // this pane, so it is COUNTED and SAID rather than left off silently.
  function cappedAway() {
    let n = 0;
    for (const g of NPCSET.groups) {
      if (g.membersCapped || (g.members || []).length < g.count) {
        n += g.count - (g.members || []).length;
      }
    }
    return n;
  }

  function selectMember(g, m) {
    // The clicked NPC becomes members[0] and the rest of the cohort follows.
    // Everything downstream -- `whichRow`, which identifies the row by `type`,
    // the instructions call, and the mid pane's cohort list -- reads
    // members[0], so this is what makes an un-named member actionable at all.
    // The group, its count and its paths are carried through unchanged.
    if (!m) return g;
    const rest = (g.members || []).filter(x => x !== m);
    const sel = {};
    for (const k in g) sel[k] = g[k];
    sel.members = [m].concat(rest);
    sel.member = m;
    return sel;
  }

  function drawNpcSet() {
    const host = $('#target-list');
    host.textContent = '';
    repaintTarget = null;
    if (!NPCSET) { host.appendChild(el('div', 'sw-note', 'loading...')); return; }
    if (NPCSET.error || !NPCSET.groups.length) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, NPCSET.headline || 'Nothing to show.'));
      b.appendChild(el('div', null, NPCSET.detail || ''));
      host.appendChild(b);
      return;
    }
    host.appendChild(el('div', 'sw-note', NPCSET.detail || ''));

    // Rows the table named but this key could not read are STATED, never
    // dropped: a shorter list reads as "those NPCs do not exist".
    if (NPCSET.droppedRows) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null,
        NPCSET.droppedRows + ' row(s) carry no usable motion id.'));
      b.appendChild(el('div', null, 'Listed as dropped rather than hidden: ' +
        (NPCSET.dropped || []).join(', ')));
      host.appendChild(b);
    }
    const cut = cappedAway();
    if (cut) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null,
        cut + ' NPC(s) are not listed individually.'));
      b.appendChild(el('div', null, 'Their groups returned more members than '
        + 'the endpoint sends. They are counted in every "N others move with '
        + 'it" below, but there is no row to click for them.'));
      host.appendChild(b);
    }
    // The count line, and it is not cosmetic: with four controls able to
    // shorten this list, "showing 12 of 437" is what keeps a filter from
    // reading as missing data.
    const count = el('div', 'sw-count');
    host.appendChild(count);
    const rowHost = el('div');
    host.appendChild(rowHost);

    const paint = () => {
      rowHost.textContent = '';
      const q = ($('#target-q').value || '').toLowerCase();
      const collapsed = !!($('#swap-collapse') || {}).checked;
      const hideIssues = !!($('#swap-hide-issues') || {}).checked;
      const modOnly = !!($('#swap-modified-only') || {}).checked;
      const how = (($('#swap-sort') || {}).value) || 'name';

      const all = collapsed ? groupRows() : flatRows();
      const shown = sortRows(all.filter(r => {
        const hay = collapsed
          ? (r.g.group + ' ' + (r.g.members || []).map(m => m.name).join(' '))
          : (r.label + ' ' + r.g.group);
        if (q && hay.toLowerCase().indexOf(q) < 0) return false;
        if (hideIssues && hasIssue(r.g)) return false;
        if (modOnly) {
          if (collapsed) {
            if (!(r.g.members || []).some(m => isModified(r.g, m))) return false;
          } else if (!isModified(r.g, r.m)) return false;
        }
        return true;
      }), how);

      for (const r of shown) rowHost.appendChild(collapsed ? groupRow(r)
                                                           : npcRow(r));
      const unit = collapsed ? 'group' : 'NPC';
      count.textContent = 'showing ' + shown.length + ' of ' + all.length +
        ' ' + unit + (all.length === 1 ? '' : 's') +
        (shown.length === all.length ? '' : ' \u2014 ' +
         (all.length - shown.length) + ' hidden by the filters above');
      if (!shown.length) {
        rowHost.appendChild(el('div', 'sw-note',
          all.length ? 'Nothing matches. ' + all.length + ' ' + unit + '(s) are '
                       + 'here; the search box and the toggles above hide the rest.'
                     : 'nothing matches'));
      }
    };

    const mark = (row, r) => {
      // Selection is by identity of the row's member (or its group when
      // collapsed), not by DOM node: a repaint replaces every node, and a
      // selection that lived on the node would vanish when you changed the
      // sort.
      const cur = V.target.sel;
      if (!cur) return;
      if (r.m ? (cur.member === r.m) : (cur.groupKey === r.g.groupKey && !cur.member))
        row.classList.add('on');
    };

    function pick(row, g, m) {
      V.target.sel = selectMember(g, m);
      for (const x of rowHost.querySelectorAll('.sw-item')) x.classList.remove('on');
      row.classList.add('on');
      // Keyed on whether the STANDBY resolves, not on the summary label:
      // `partial` means some action is missing while the standby draws
      // perfectly well, and refusing to draw it would hide a model the
      // install does ship. Measured on CCO: group 118 is exactly that case.
      const standbyOk = g.resolves && g.resolves.standby_motion === true;
      if (!standbyOk) {
        // The row is listed because the TABLE names it -- dropping it would
        // read as "that NPC does not exist". But asking the renderer for a
        // file the install does not ship produces a failure that looks like a
        // broken viewer instead of a client that never shipped the art. Say
        // which it is, and draw nothing.
        const v = V.target.viewer, msg = $('#msg-target');
        if (v) { v.clear(); v.draw(); }
        msg.style.display = '';
        msg.textContent = 'This NPC is in npc.json, but the art it names ('
          + (g.paths.standby_motion || 'no path') + ') is not in this '
          + 'install. Nothing to draw, and nothing to split from.';
      } else {
        draw('target', g.paths.standby_motion, g.texture || '');
      }
      drawMid();
    }

    // ONE NPC. The sharing fact is on the row, in words, next to the name --
    // not in a header above 437 rows. The people this list is for are the ones
    // reaching a member that was never the representative, and for whom "this
    // swap also moves 37 others" is the surprise.
    function npcRow(r) {
      const g = r.g, m = r.m;
      const row = el('div', 'sw-item swap-npc-row ' +
                     (g.count > 1 ? 'swap-shared' : 'swap-alone'));
      row.appendChild(el('span', 'sw-who', r.label));
      const tag = el('span', 'sw-id');
      tag.appendChild(el('span', null, 'group ' + g.group + ' \u00b7 '));
      const others = g.count - 1;
      tag.appendChild(el('span', 'sw-share',
        others > 0 ? others + ' other' + (others === 1 ? '' : 's')
                     + ' move' + (others === 1 ? 's' : '') + ' with it'
                   : 'only this NPC'));
      if (hasIssue(g)) tag.appendChild(el('span', null, ' \u00b7 art ' + g.art));
      row.appendChild(tag);
      // WHICH others, by name, on the row itself. The full cohort is also
      // listed unabbreviated in the middle pane the moment you select it.
      const names = (g.members || []).filter(x => x !== m)
                                     .map(x => x.name || '(unnamed)');
      const who = others > 0
        ? ('A swap here also moves, on group ' + g.group + ':\n  '
           + names.join('\n  ')
           + (names.length < others
              ? '\n  ... and ' + (others - names.length) + ' more not sent'
              : ''))
        : ('Nothing else uses group ' + g.group + '; a swap here moves this '
           + 'NPC alone.');
      row.title = who + (hasIssue(g) ? '\n\n' + (g.artNote || '') : '');
      if (hasIssue(g)) row.classList.add('sw-dim');
      if (isModified(g, m)) {
        const b = el('span', 'sw-id', ' \u00b7 COMod');
        b.title = 'COMod\u2019s install record says it changed this row or '
                + 'the art it points at.';
        tag.appendChild(b);
      }
      mark(row, r);
      row.onclick = () => pick(row, g, m);
      return row;
    }

    // The group as a unit, unchanged in meaning from before the expansion.
    function groupRow(r) {
      const g = r.g;
      const row = el('div', 'sw-item');
      const who = g.count === 1 ? (g.members[0].name || '(unnamed)')
                                : g.members[0].name + ' +' + (g.count - 1) + ' more';
      row.appendChild(el('span', null, who));
      const tag = (g.count > 1
                     ? 'group ' + g.group + ' \u00b7 ' + g.count + ' NPC(s)'
                     : String(g.group)) +
                  (hasIssue(g) ? ' \u00b7 art ' + g.art : '');
      row.appendChild(el('span', 'sw-id', tag));
      if (hasIssue(g)) { row.classList.add('sw-dim'); row.title = g.artNote; }
      mark(row, r);
      row.onclick = () => pick(row, g, null);
      return row;
    }

    repaintTarget = paint;
    $('#target-q').oninput = paint;
    paint();
  }

  // The filter bar is wired ONCE, at boot, and calls whatever paint the
  // current list installed. Re-binding it inside drawNpcSet would stack a
  // handler per load and repaint the pane N times on one click.
  function wireFilters() {
    const go = () => { if (repaintTarget) repaintTarget(); };
    for (const id of ['#swap-hide-issues', '#swap-modified-only',
                      '#swap-collapse']) {
      const n = $(id);
      if (n) n.onchange = go;
    }
    const s = $('#swap-sort');
    if (s) s.onchange = go;
    // The library pane's own filter. Wired here, once, for the same reason:
    // drawLibrary() re-runs whenever the library reloads and re-binding
    // inside it would stack a handler per load.
    const np = $('#swap-hide-noparts');
    if (np) np.onchange = () => { if (repaintDonor) repaintDonor(); };
  }

  async function loadModified() {
    // A failure to READ the record is not "nothing is modified": that would
    // make the toggle hide every row and read as a clean install.
    try {
      MODIFIED = await api('/api/swap/modified?root=' +
                           encodeURIComponent(targetPath || ''));
    } catch (e) {
      MODIFIED = { recorded: false, error: true, files: [], rowTypes: [],
                   headline: 'The install record would not read.',
                   detail: say(e) };
    }
    const box = $('#lbl-modified-only');
    if (!box) return;
    const cb = $('#swap-modified-only');
    const base = 'Show only what COMod\u2019s own install record says it '
      + 'changed in this install. It cannot tell you what differs from the '
      + 'vendor\u2019s original client: a private server has no pristine '
      + 'edition to compare against.';
    let extra = '';
    if (MODIFIED.error) {
      extra = '\n\nRIGHT NOW: ' + (MODIFIED.headline || '') + ' '
            + (MODIFIED.detail || '') + ' Nothing can be filtered on.';
    } else if (!MODIFIED.recorded) {
      extra = '\n\nRIGHT NOW: ' + (MODIFIED.headline || '')
            + ' ' + (MODIFIED.detail || '');
    } else {
      extra = '\n\nRIGHT NOW: ' + (MODIFIED.detail || '')
            + (MODIFIED.tableAmbiguous
               ? '\n' + MODIFIED.headline + ' ' + MODIFIED.detail : '')
            + (MODIFIED.tableError
               ? '\n' + MODIFIED.headline + ' ' + MODIFIED.detail : '');
    }
    box.title = base + extra;
    // A toggle that can only ever empty the list is disabled with its reason
    // on it, rather than offered and then blamed for showing nothing.
    const dead = MODIFIED.error || !MODIFIED.recorded;
    if (cb) {
      cb.disabled = !!dead;
      if (dead) cb.checked = false;
    }
    box.style.opacity = dead ? '.55' : '';
  }

  /* -------------------------------------------------------------- the mid */

  function drawMid() {
    const host = $('#mid-body');
    host.textContent = '';
    const D = V.donor.sel, T = V.target.sel;
    if (!D || !T) {
      host.appendChild(el('div', null,
        !D && !T ? 'Pick something from your library, then the NPC it replaces.'
                 : !D ? 'Now pick the library entry you want to bring.'
                      : 'Now pick the NPC on the right that it replaces.'));
      return;
    }
    // Name the NPC, not just the group. The pane now lets you pick a member
    // that is not the representative, so "group 001" alone no longer says
    // which of its 38 rows is about to change.
    const picked = (T.members && T.members[0] && T.members[0].name) || '';
    host.appendChild(el('div', null, (D.name || D.id) + ' → ' +
      (picked ? picked + '  (group ' + T.group + ')' : 'group ' + T.group)));

    // The scope choice, and SPLIT is the default because sharing is the
    // common case: most standby groups on CCO are used by more than one NPC,
    // so the destructive option is the one you would hit by accident.
    const standbyResolves = T.resolves && T.resolves.standby_motion === true;
    if (T.art && T.art !== 'ok' && !standbyResolves) {
      // npcsplit refuses this case too ("the current motion path did not
      // resolve, so there is no source file to name"). Offering the button
      // and letting it fail later would move a knowable refusal to the far
      // side of a click.
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null,
        'This NPC\u2019s art is not in this install.'));
      b.appendChild(el('div', null,
        'ini/npc.json names ' + (T.paths && T.paths.standby_motion || '?')
        + ', which does not resolve here. A split copies that file, so there '
        + 'is nothing to copy. ' + (T.artNote || '')));
      host.appendChild(b);
      return;
    }
    if (targetKind !== 'npc') {
      // Measured, not assumed: only NPC rows share motion ids. Offering a
      // split here would invent a problem this family does not have.
      host.appendChild(el('div', 'sw-note',
        'Each ' + targetKind + ' has its own art, so there is nothing to '
        + 'split -- this is a straight replacement.'));
      const go2 = el('button', 'sw-go', 'Show me what to change');
      go2.onclick = () => showInstructions(D, T);
      host.appendChild(go2);
      host.appendChild(el('div', 'sw-note',
        'COMod describes the change; it does not apply it.'));
      return;
    }
    if (T.count > 1) {
      const warn = el('div', 'sw-note');
      warn.appendChild(el('strong', null,
        'This art is used by ' + T.count + ' NPCs.'));
      host.appendChild(warn);

      // Who they ARE, by name. "39 NPCs" is a number to agree to; the names
      // are what tells you whether the one you care about is in the blast
      // radius. Listed rather than summarised, and scrolled rather than
      // truncated -- a cut-off list reads as a shorter cohort.
      const box = el('div', 'sw-members');
      (T.members || []).forEach((m, i) => {
        const who = (m.name || '(unnamed)') +
          (m.type !== undefined && m.type !== null ? '   type ' + m.type : '');
        box.appendChild(el('div', null, (i + 1) + '.  ' + who));
      });
      if ((T.members || []).length < T.count) {
        box.appendChild(el('div', null,
          '... ' + (T.count - T.members.length) + ' more not listed here'));
      }
      host.appendChild(box);
      const mk = (val, label, sub) => {
        const b = el('div', 'sw-item' + (scope === val ? ' on' : ''));
        b.appendChild(el('span', null, label));
        b.appendChild(el('span', 'sw-id', sub));
        b.onclick = () => { scope = val; drawMid(); };
        return b;
      };
      host.appendChild(mk('one', 'Replace ' + (picked || 'this NPC') + ' only',
                          'gives it private art; the other ' + (T.count - 1) +
                          ' keep theirs'));
      host.appendChild(mk('all', 'Replace all ' + T.count,
                          'every NPC on group ' + T.group + ' changes'));
    } else {
      host.appendChild(el('div', 'sw-note',
        'Only one NPC uses this art, so there is nothing to split from.'));
    }

    const row = el('div', 'sw-row');
    const stage = el('button', 'sw-go', 'Stage this swap');
    stage.onclick = () => stageSplit(D, T);
    row.appendChild(stage);
    const go = el('button', 'sw-go', 'Show me what is being changed');
    go.onclick = () => showInstructions(D, T);
    row.appendChild(go);
    host.appendChild(row);
    host.appendChild(el('div', 'sw-note',
      'Staging writes into mods/stage only. Nothing reaches the game install '
      + 'until you press Install in Mod staging, which backs up every file it '
      + 'displaces and can be reverted.'));
  }

  /* ------------------------------------------------------------ staging */

  function whichRow(T) {
    // A row is identified by `type`, never by name: names repeat in npc.json
    // (3 rows are called Blacksmith), so a name would sometimes split a
    // different NPC than the one on screen -- silently.
    const m = (T.members || [])[0] || {};
    return { type: m.type, name: m.name || '' };
  }

  async function stageSplit(D, T) {
    const host = $('#mid-body');
    host.textContent = '';
    host.appendChild(el('div', null, 'staging...'));
    const who = whichRow(T);
    const q = (who.type !== undefined && who.type !== null)
      ? 'type=' + encodeURIComponent(who.type)
      : 'npc=' + encodeURIComponent(who.name);
    let res;
    try {
      // The donor is what makes this a swap. Sending it also turns on the
      // appearance fork, because the donor's mesh on a shared simple_object
      // would either change nothing or change it for every NPC on that slot.
      const donor = D && D.id ? '&donor=' + encodeURIComponent(D.id) : '';
      res = await api('/api/swap/stage?' + q + donor + '&root=' +
                      encodeURIComponent(targetPath || ''), { method: 'POST' });
    } catch (e) {
      host.textContent = '';
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, 'Could not stage.'));
      b.appendChild(el('div', null, e.message));
      host.appendChild(b);
      return;
    }
    LASTSTAGE = res;
    host.textContent = '';
    host.appendChild(el('div', null,
      (D.name || D.id) + '  \u2192  ' + (who.name || 'row ' + who.type)));
    if (!res.ok) {
      // npcstage refuses rather than staging half a split. That is an answer,
      // and its reason is in the text -- rendering it as "nothing happened"
      // is how a refusal turns into a mystery.
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, res.headline || 'Nothing was staged.'));
      // One refusal has a specific cure, and saying so beats making the
      // reader infer it: the stage tree still holds an earlier split of this
      // row, so its OLD no longer matches the install.
      if ((res.text || '').indexOf('was written against') >= 0) {
        b.appendChild(el('div', null,
          'The stage tree still holds an earlier split of this NPC. An '
          + 'uninstall reverts the install but leaves mods/stage as it was, '
          + 'so the two disagree. Open Mod staging and use "Clear staging", '
          + 'then plan this split again.'));
      }
      host.appendChild(b);
    } else {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, 'Staged. Nothing is installed yet.'));
      b.appendChild(el('div', null,
        'Open Mod staging to review it and install, or to revert.'));
      host.appendChild(b);
    }
    host.appendChild(el('pre', 'sw-plan', res.text || '(no output)'));
    const bar = el('div', 'sw-row');
    const open = el('button', 'sw-go', 'Open Mod staging');
    open.onclick = openDrawer;
    bar.appendChild(open);
    const back = el('button', 'sw-go', 'Back');
    back.onclick = drawMid;
    bar.appendChild(back);
    host.appendChild(bar);
  }

  /* ------------------------------------------------- the staging drawer */

  function renderWritable(host, w) {
    if (!w || w.writable) return false;
    const b = el('div', 'sw-warn');
    b.appendChild(el('strong', null, w.headline ||
      'COMod cannot write to this install.'));
    b.appendChild(el('div', null, w.detail || ''));
    if (w.why) b.appendChild(el('div', null, w.why.split('\n')[0]));
    b.appendChild(el('div', null, 'Checked by ' + (w.basis || 'probe') + '.'));
    host.appendChild(b);
    return true;
  }

  async function refreshWritable() {
    try {
      WRITABLE = await api('/api/swap/writable?root=' +
                           encodeURIComponent(targetPath || ''));
    } catch (e) { WRITABLE = null; }
    const host = $('#sw-writable');
    if (host) { host.textContent = ''; renderWritable(host, WRITABLE); }
  }

  async function loadStageList() {
    const host = $('#stage-list');
    host.textContent = '';
    let d;
    try { d = await api('/api/stage'); }
    catch (e) {
      host.appendChild(el('div', 'sw-note', 'could not read the stage tree: ' +
                          e.message));
      return;
    }
    const rows = d.files || d.rows || [];
    const dir = $('#stage-dir');
    if (dir && d.stageDir) dir.textContent = d.stageDir;
    if (!rows.length) {
      // Empty is a state, not a failure, and it is the state this page is in
      // before you stage anything.
      host.appendChild(el('div', 'sw-note',
        'Nothing is staged. Pick a library entry and an NPC, then '
        + '"Stage this split".'));
      return;
    }
    for (const r of rows) {
      const line = el('div', 'sw-file');
      line.appendChild(el('b', null, r.status || ''));
      line.appendChild(el('span', null, r.logical || ''));
      host.appendChild(line);
    }
    host.appendChild(el('div', 'sw-note', rows.length + ' staged file(s).'));
  }

  async function loadRecord() {
    const host = $('#record-list');
    if (!host) return;
    host.textContent = '';
    let d;
    try {
      d = await api('/api/swap/record?root=' +
                    encodeURIComponent(targetPath || ''));
    } catch (e) {
      host.appendChild(el('div', 'sw-note', 'could not read the record: '
                          + say(e)));
      return;
    }
    if (!d.entries || !d.entries.length) {
      // Nothing installed is a state, and the ordinary one before you start.
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, d.headline || 'Nothing is installed.'));
      if (d.detail) b.appendChild(el('div', null, d.detail));
      host.appendChild(b);
      return;
    }
    host.appendChild(el('div', 'sw-note', d.detail || ''));
    d.entries.forEach((e, i) => {
      const row = el('div', 'sw-file');
      row.appendChild(el('b', null, String(e.n)));
      const mid = el('span', null,
        (e.at || '(undated)') + '  \u00b7  ' + e.count + ' file(s)'
        + (e.label ? '  \u00b7  ' + e.label : ''));
      mid.title = (e.files || []).join('\n');
      row.appendChild(mid);
      // Only a suffix can be peeled back, so the button is offered on the
      // newest entry and as "this and everything after" on the rest. An
      // entry cannot be lifted out of the middle: the ones above it were
      // backed up against the state it left.
      const isLast = (i === d.entries.length - 1);
      const b = el('button', 'sw-go',
                   isLast ? 'Revert this' : 'Revert this and newer');
      b.onclick = () => {
        const what = isLast ? 'the newest entry'
                            : ('entry ' + e.n + ' and the '
                               + (d.entries.length - e.n) + ' after it');
        if (!confirm('Revert ' + what + '?\n\n'
                     + 'Files it displaced are restored from that entry\u2019s '
                     + 'own backups. Anything older stays installed.')) return;
        const q = isLast ? 'last=1' : 'entry=' + encodeURIComponent(e.n);
        runMod('/api/uninstall?dry=0&' + q + '&root='
               + encodeURIComponent(targetPath || ''), 'revert');
      };
      row.appendChild(b);
      host.appendChild(row);
    });
  }

  async function openDrawer() {
    $('#drawer').classList.remove('hidden');
    $('#mod-output').textContent = '';
    const warn = $('#drawer-warn');
    warn.textContent = '';
    await refreshWritable();
    renderWritable(warn, WRITABLE);
    await loadStageList();
  }

  async function clearStaging() {
    // `uninstall` reverts the INSTALL; it does not empty the stage tree. So
    // after a revert the stage still holds the split that was just undone,
    // and re-staging that NPC refuses -- correctly, because its OLD no longer
    // matches -- but with no way forward offered. This is the way forward.
    let d;
    try { d = await api('/api/stage'); }
    catch (e) { $('#mod-output').textContent = 'could not read the stage tree: '
                                               + say(e); return; }
    const rows = d.files || d.rows || [];
    if (!rows.length) {
      $('#mod-output').textContent = 'The stage tree is already empty.';
      return;
    }
    if (!confirm('Remove all ' + rows.length + ' staged file(s)?\n\n'
                 + 'This only empties mods/stage. Anything already installed '
                 + 'stays installed -- use "Uninstall / revert" for that.')) return;
    const out = $('#mod-output');
    out.textContent = 'clearing ' + rows.length + ' file(s)...';
    let gone = 0; const failed = [];
    for (const r of rows) {
      try {
        await api('/api/unstage?path=' + encodeURIComponent(r.logical),
                  { method: 'POST' });
        gone++;
      } catch (e) { failed.push(r.logical + ': ' + say(e)); }
    }
    // Partial success is stated, not rounded to "done".
    out.textContent = 'cleared ' + gone + ' of ' + rows.length + ' file(s).'
      + (failed.length ? '\n\nleft behind:\n  ' + failed.join('\n  ') : '');
    await loadStageList();
    await loadRecord();
  }

  async function runMod(url, label) {
    const out = $('#mod-output');
    for (const old of Array.from(
        (out.parentNode || document).querySelectorAll('.sw-warn'))) {
      if (old.id !== 'sw-writable') old.remove();
    }
    out.textContent = label + '...';
    try {
      const r = await api(url, { method: 'POST' });
      // comod's own words, not the envelope around them. Its stdout is the
      // per-file account of what was added, overwritten and backed up; a
      // JSON dump buries that in escaping at the moment it matters most.
      const body = [r.stdout, r.stderr].filter(Boolean).join('\n').trim();
      out.textContent = body || r.output || r.text ||
                        JSON.stringify(r, null, 1);
      if (r.returncode !== undefined && r.returncode !== 0) {
        out.textContent = 'exit ' + r.returncode + '\n\n' + out.textContent;
      }
      // comod keeps ONE install record, because the manifest is what lets
      // uninstall put every displaced file back exactly. A second install
      // layered on it would leave backups describing a state that no longer
      // exists. The stage tree, meanwhile, ACCUMULATES: each swap edits the
      // staged tables, so everything staged goes on together in one install.
      // That makes this refusal a two-step, not a dead end -- say so.
      if ((body || '').indexOf('already has an install recorded') >= 0) {
        const b = el('div', 'sw-warn');
        b.appendChild(el('strong', null,
          'One install at a time -- and nothing staged is lost.'));
        b.appendChild(el('div', null,
          'Everything in the list above is staged together, including swaps '
          + 'you installed earlier. Press "Uninstall / revert" first, then '
          + '"Install for real": the whole stage tree goes on in one install, '
          + 'with one manifest that can revert all of it.'));
        b.appendChild(el('div', null,
          'The single record is what made your earlier revert exact. Layering '
          + 'a second install on it is what would take that away.'));
        out.parentNode.insertBefore(b, out);
      }
    } catch (e) {
      out.textContent = label + ' failed: ' + e.message;
    }
    await loadStageList();
    await loadRecord();
  }

  function wireDrawer() {
    const btn = $('#btn-mods');
    if (btn) btn.onclick = openDrawer;
    const close = $('#drawer-close');
    if (close) close.onclick = () => $('#drawer').classList.add('hidden');
    const dry = $('#btn-dry');
    if (dry) dry.onclick = () => runMod(
      '/api/install?dry=1&root=' + encodeURIComponent(targetPath || ''),
      'install (dry run)');
    const inst = $('#btn-install');
    if (inst) inst.onclick = () => {
      if (WRITABLE && !WRITABLE.writable &&
          !confirm('COMod could not write to this install when it checked. '
                   + 'Install will probably fail. Try anyway?')) return;
      if (!confirm('This copies mods/stage into the game install at\n\n'
                   + (targetPath || '(the open install)')
                   + '\n\nEvery file it replaces is backed up first, and '
                   + '"Uninstall / revert" puts them back. Continue?')) return;
      runMod('/api/install?dry=0&root=' + encodeURIComponent(targetPath || ''),
             'install');
    };
    const clr = $('#btn-clear');
    if (clr) clr.onclick = clearStaging;
    const am = $('#btn-amend');
    if (am) am.onclick = () => {
      if (!confirm('Add everything staged to the existing install as a new '
                   + 'dated entry?\n\nIt gets its own backups, so it can be '
                   + 'reverted on its own without disturbing what is already '
                   + 'installed.')) return;
      runMod('/api/install?dry=0&amend=1&root='
             + encodeURIComponent(targetPath || ''), 'amend');
    };
    const un = $('#btn-uninstall');
    if (un) un.onclick = () => {
      if (!confirm('This reverts the last install: restores every backed-up '
                   + 'file and removes the ones that were added. Continue?')) return;
      runMod('/api/uninstall?dry=0&root=' + encodeURIComponent(targetPath || ''),
             'uninstall');
    };
  }

  async function showInstructions(D, T) {
    const host = $('#mid-body');
    host.textContent = '';
    host.appendChild(el('div', null, 'working out what to change…'));
    let plan;
    try {
      // ONE source of truth: this runs tools/npcsplit.py and shows what it
      // says. The page does not recompute the free set, the cohort, or the
      // OLD values -- a UI that re-derives them will drift from the command.
      // The SAME identity staging uses. `whichRow` answers by `type` because
      // names repeat in npc.json, and this call used to send the name alone --
      // so on a duplicate name the plan on screen could describe a different
      // row than the one being staged, with nothing to see. Reachable now in
      // a way it was not before: the list is one row per NPC, so both rows
      // called Shelley are individually clickable.
      const row = whichRow(T);
      plan = await api('/api/swap/instructions?npc=' +
                       encodeURIComponent(row.name) +
                       ((row.type === undefined || row.type === null) ? ''
                        : '&type=' + encodeURIComponent(row.type)));
    } catch (e) {
      host.textContent = '';
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, 'Could not work out the change.'));
      b.appendChild(el('div', null, e.message));
      // A failure to PLAN is not "nothing to do" and must not render as an
      // empty step list.
      host.appendChild(b);
      return;
    }
    host.textContent = '';
    renderPlan(host, plan, D, T);
  }

  function renderPlan(host, plan, D, T) {
    const picked = (T.members && T.members[0] && T.members[0].name) || '';
    host.appendChild(el('div', null, (D.name || D.id) + ' \u2192 ' +
      (picked ? picked + '  (group ' + T.group + ')' : 'group ' + T.group) +
      (scope === 'one' ? '  (this NPC only)' : '  (all ' + T.count + ')')));

    if (plan.headline) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, plan.headline));
      if (plan.detail) b.appendChild(el('div', null, plan.detail));
      host.appendChild(b);
    }

    // A refusal is not an empty answer. npcsplit declines to print an
    // instruction whose OLD value it could not read at describe-time, because
    // an automated edit that is wrong gets caught by a test while a printed
    // instruction that is wrong gets typed. Say so instead of showing blank.
    if (!plan.text) {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null, 'No instructions were produced.'));
      b.appendChild(el('div', null, plan.detail ||
        'That is a fault, not an empty change.'));
      host.appendChild(b);
      return;
    }

    if (scope === 'all') {
      const b = el('div', 'sw-note');
      b.appendChild(el('strong', null,
        'You chose to replace all ' + T.count + ' NPCs on this group.'));
      b.appendChild(el('div', null,
        'Overwrite the group art in place -- no new id, no table edit. Every ' +
        'NPC on group ' + T.group + ' changes. The steps below describe the ' +
        'SPLIT instead; use them if you meant only one.'));
      host.appendChild(b);
    }

    const pre = el('pre', 'sw-plan', plan.text);
    host.appendChild(pre);

    if (plan.command) {
      host.appendChild(el('div', 'sw-note', 'The same answer from a terminal:'));
      const c = el('pre', 'sw-cmd', plan.command);
      c.title = 'click to copy';
      c.onclick = () => navigator.clipboard && navigator.clipboard.writeText(plan.command);
      host.appendChild(c);
    }

    host.appendChild(el('div', 'sw-note',
      'COMod describes the change; it does not apply it. Nothing above has ' +
      'been written to your install.'));

    const back = el('button', 'sw-go', 'Back');
    back.onclick = drawMid;
    host.appendChild(back);
  }

  /* ---------------------------------------------------------------- boot */

  async function boot() {
    V.donor.viewer = makeViewer('donor');
    V.target.viewer = makeViewer('target');
    $('#msg-donor').textContent = 'pick from your library';
    $('#msg-target').textContent = 'pick an NPC to replace';

    try { LIB = await api('/api/swap/library'); }
    catch (e) { LIB = { configured: true, error: true, entries: [],
                        headline: 'The library would not read.', detail: e.message }; }
    drawLibrary();

    try { TARGETS = await api('/api/swap/targets'); }
    catch (e) { TARGETS = { targets: [], headline: 'Could not read targets.',
                            detail: e.message }; }
    drawTargets();

    const off = (TARGETS.targets || []).filter(t => t.offered);
    if (off.length) {
      await loadTargetList();
    } else {
      NPCSET = { error: true, groups: [],
                 headline: 'No target install is offered.',
                 detail: 'Declare a private-server install to populate this pane.' };
      drawNpcSet();
    }
    drawMid();
    wireDrawer();
    wireFilters();
    refreshWritable();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
