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

  async function draw(side, mesh, texture, libId) {
    const v = V[side].viewer, msg = $('#msg-' + side);
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
      let n = 0;
      for (const e of LIB.entries) {
        if (cat && e.category !== cat) continue;
        if (q && (e.name + ' ' + e.sourceMesh).toLowerCase().indexOf(q) < 0) continue;
        n++;
        const row = el('div', 'sw-item');
        row.appendChild(el('span', null, e.name || e.id));
        row.appendChild(el('span', 'sw-id', e.category +
          (e.parts ? ' · ' + e.parts + ' part(s)' : ' · no parts')));
        row.onclick = () => {
          V.donor.sel = e;
          for (const x of host.querySelectorAll('.sw-item')) x.classList.remove('on');
          row.classList.add('on');
          draw('donor', e.mesh || e.sourceMesh, '', e.id);
          drawMid();
        };
        host.appendChild(row);
      }
      if (!n) host.appendChild(el('div', 'sw-note', 'no library entry matches'));
    };
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
    drawNpcSet();
  }

  function drawNpcSet() {
    const host = $('#target-list');
    host.textContent = '';
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

    const paint = () => {
      for (const x of Array.from(host.querySelectorAll('.sw-item, .sw-grp'))) x.remove();
      const q = ($('#target-q').value || '').toLowerCase();
      let n = 0;
      for (const g of NPCSET.groups) {
        const names = g.members.map(m => m.name).join(' ').toLowerCase();
        if (q && (g.group + ' ' + names).indexOf(q) < 0) continue;
        n++;
        const row = el('div', 'sw-item');
        const who = g.count === 1 ? (g.members[0].name || '(unnamed)')
                                  : g.members[0].name + ' +' + (g.count - 1) + ' more';
        row.appendChild(el('span', null, who));
        const tag = (g.count > 1
                       ? 'group ' + g.group + ' \u00b7 ' + g.count + ' NPC(s)'
                       : String(g.group)) +
                    (g.art === 'ok' ? '' : ' \u00b7 art ' + g.art);
        row.appendChild(el('span', 'sw-id', tag));
        if (g.art !== 'ok') { row.classList.add('sw-dim'); row.title = g.artNote; }
        row.onclick = () => {
          V.target.sel = g;
          for (const x of host.querySelectorAll('.sw-item')) x.classList.remove('on');
          row.classList.add('on');
          // Keyed on whether the STANDBY resolves, not on the summary
          // label: `partial` means some action is missing while the standby
          // draws perfectly well, and refusing to draw it would hide a model
          // the install does ship. Measured on CCO: group 118 is exactly
          // that case.
          const standbyOk = g.resolves && g.resolves.standby_motion === true;
          if (!standbyOk) {
            // The row is listed because the TABLE names it -- dropping it
            // would read as "that NPC does not exist". But asking the
            // renderer for a file the install does not ship produces a
            // failure that looks like a broken viewer instead of a client
            // that never shipped the art. Say which it is, and draw nothing.
            const v = V.target.viewer, m = $('#msg-target');
            if (v) { v.clear(); v.draw(); }
            m.style.display = '';
            m.textContent = 'This NPC is in npc.json, but the art it names ('
              + (g.paths.standby_motion || 'no path') + ') is not in this '
              + 'install. Nothing to draw, and nothing to split from.';
          } else {
            draw('target', g.paths.standby_motion, g.texture || '');
          }
          drawMid();
        };
        host.appendChild(row);
      }
      if (!n) host.appendChild(el('div', 'sw-note sw-grp', 'nothing matches'));
    };
    $('#target-q').oninput = paint;
    paint();
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
    host.appendChild(el('div', null, (D.name || D.id) + ' → group ' + T.group));

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
      host.appendChild(mk('one', 'Replace this NPC only',
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
      const who = (T.members[0] && T.members[0].name) || '';
      plan = await api('/api/swap/instructions?npc=' + encodeURIComponent(who));
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
    host.appendChild(el('div', null, (D.name || D.id) + ' \u2192 group ' + T.group +
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
    refreshWritable();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
