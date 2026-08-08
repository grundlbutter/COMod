/* swap.js -- replacing an asset with a collected one, for BOTH pages.
 *
 * WHY THIS FILE EXISTS
 *
 *   Collecting and *using* what you collected were built on different pages.
 *   The asset browser could stage a swap -- through a `prompt()` that asked
 *   for a path and then wrote everything the entry had, with no say in it --
 *   and it owned the Mod staging drawer. The builder, which is where you
 *   actually look at a model and decide it should replace another one, could
 *   collect and nothing else: no stage, no remove, no drawer. Reported as
 *   "my mod staging is gone", which is exactly what it looks like from the
 *   page that never had it.
 *
 *   Porting the drawer into builder.js would have made two copies of it, and
 *   this project has already paid for that once: the rule for finding a
 *   model's action files was written twice and only one copy learned about
 *   the Collection. So both pages load this instead.
 *
 * WHAT A REPLACEMENT IS
 *
 *   The game reads a loose file before the archive, so a file written at the
 *   right logical path IS the swap. What makes it more than a file copy is
 *   that a model is not one file: it has a skin, and it has an action set,
 *   and both are named after the asset they belong to. `Collection.stage`
 *   does that renaming; this panel decides what travels.
 *
 *   Nothing here touches the game. Staging writes to `mods/stage/`, and
 *   `comod.py install` -- the drawer's button, the CLI's command -- is the
 *   only thing that copies into the install, with its backups and its revert.
 */
(function (global) {
  'use strict';

  const $ = s => document.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  };

  /** The page supplies these: it already has an `api()` that names its own
   *  failures, and a `toast()` styled for its own layout. */
  let API = null, TOAST = null, ON_CHANGE = null;

  function configure({ api, toast, onChange }) {
    API = api;
    TOAST = toast || (m => console.log(m));
    ON_CHANGE = onChange || null;
  }

  /** True when the entry's skin sits beside its mesh, same stem -- the only
   *  case where its home under a new target can be derived rather than
   *  guessed. The flat NPC family keeps skins in `c3/texture/`, so for those
   *  the answer is no and the destination has to be said out loud. */
  function skinFollowsMesh(entry) {
    const mesh = (entry.sourceMesh || '').toLowerCase();
    const tex = (entry.sourceTexture || '').toLowerCase();
    if (!mesh || !tex) return false;
    const dot = tex.lastIndexOf('.');
    const stem = dot < 0 ? tex : tex.slice(0, dot);
    return stem === mesh.replace(/\.[^./]*$/, '');
  }

  function countRole(entry, role) {
    return (entry.parts || []).filter(p => p.role === role).length;
  }

  /** The Collection publishes itself as a server profile under this name, so
   *  its own copy of an entry is browsable at `collection/<category>/<id>.c3`
   *  (`core/collection.py::PROFILE_NAME`). */
  const PROFILE_NAME = 'collection';

  /** The entry a logical path belongs to, matched BOTH ways it can be seen.
   *
   *  An entry has two paths, and the card only ever knew the first:
   *
   *    sourceMesh   c3/npc/999001100.c3                where it came FROM
   *    mesh         NPCs/base-storekeeper-36.c3        the copy that was kept
   *
   *  Browsing the Collection view puts the second on screen -- as
   *  `collection/npcs/base-storekeeper-36.c3` -- and a lookup that compared
   *  only against `sourceMesh` found nothing there. So the page you go to in
   *  order to look at what you kept was the one page that would not offer
   *  Replace or Remove for it, and offered to *collect* it instead, which
   *  would have filed a second entry pointing at the first one's copy.
   *
   *  Returns `{entry, isCopy}`: `isCopy` says the path on screen is the
   *  Collection's own copy rather than the original, which changes what the
   *  card should offer -- there is nothing left to collect.
   */
  function entryFor(entries, logical) {
    const key = (logical || '').replace(/\\/g, '/').toLowerCase();
    if (!key) return null;
    for (const e of (entries || [])) {
      if ((e.sourceMesh || '').toLowerCase() === key) {
        return { entry: e, isCopy: false };
      }
    }
    for (const e of (entries || [])) {
      if (`${PROFILE_NAME}/${(e.mesh || '')}`.toLowerCase() === key) {
        return { entry: e, isCopy: true };
      }
    }
    return null;
  }

  /** The Replace panel: what to overwrite, and what travels with it. */
  function replacePanel(host, entry, { defaultTarget = '' } = {}) {
    host.innerHTML = '';
    const nMotion = countRole(entry, 'motion');
    const nEffect = countRole(entry, 'effect') + countRole(entry, 'sound');
    const hasSkin = !!(entry.skins && entry.skins.length);

    host.appendChild(el('div', 'mut small',
      'Write this entry over another asset. Nothing reaches the game until '
      + 'you install.'));

    const targetIn = el('input');
    targetIn.type = 'text';
    targetIn.style.width = '100%';
    targetIn.style.marginTop = '4px';
    targetIn.placeholder = 'logical path to replace, e.g. c3/npc/999002100.c3';
    targetIn.value = defaultTarget || entry.swapFor || entry.sourceMesh || '';
    targetIn.title = 'The path the game asks for. Staging here replaces what '
                   + 'the client would otherwise load from the archive.';
    host.appendChild(targetIn);

    const opts = el('div', 'swap-opts');
    opts.style.marginTop = '6px';
    const boxes = {};
    const addBox = (key, label, n, why, on) => {
      const lb = el('label', 'chk');
      lb.style.display = 'block';
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = on;
      cb.disabled = !n;
      boxes[key] = cb;
      lb.appendChild(cb);
      lb.appendChild(document.createTextNode(
        ' ' + label + (n ? ` (${n})` : ' — none in this entry')));
      lb.title = why;
      opts.appendChild(lb);
    };
    addBox('skin', 'its skin', hasSkin ? 1 : 0,
           'Unticked, the target keeps its own texture — new geometry, old '
           + 'colours.', hasSkin);
    addBox('motion', 'its animations', nMotion,
           'The action files collected with it, renamed onto the target so '
           + 'the target’s own actions are what get replaced.', nMotion > 0);
    addBox('effect', 'its effects and sounds', nEffect,
           'These keep their own logical paths — an effect is not filed under '
           + 'the model that triggers it.', nEffect > 0);
    host.appendChild(opts);

    // Only asked for when it cannot be derived, because for most entries it
    // can, and a field that is usually noise stops being read when it matters.
    const skinTo = el('input');
    skinTo.type = 'text';
    skinTo.style.width = '100%';
    skinTo.style.marginTop = '4px';
    skinTo.placeholder = 'where the skin goes, e.g. c3/texture/9990020.dds';
    const needsSkinTo = hasSkin && !skinFollowsMesh(entry);
    skinTo.classList.toggle('hidden', !needsSkinTo);
    const why = el('div', 'mut small');
    why.style.marginTop = '4px';
    why.classList.toggle('hidden', !needsSkinTo);
    host.appendChild(why);
    host.appendChild(skinTo);

    /** Where the skin goes, asked of the resolver rather than derived.
     *
     *  String arithmetic only works when the skin sits beside its mesh. The
     *  flat NPC family keeps textures in `c3/texture/` named by look, so the
     *  destination used to have to be typed -- and getting it wrong put a
     *  texture somewhere the game never looks. The resolver already knows
     *  where the TARGET's own skin lives, and that is the answer: every file
     *  that moves lands where the target's equivalent already is.
     *
     *  Filled in rather than applied silently, so it can be seen and edited.
     */
    async function fillSkinTarget() {
      if (!needsSkinTo) return;
      const target = targetIn.value.trim();
      why.textContent = `This skin (${entry.sourceTexture}) does not sit `
        + 'beside its mesh, so where it belongs is worked out from the '
        + 'target instead.';
      if (!target) { skinTo.value = ''; return; }
      try {
        const r = await API('/api/skintarget?target=' + encodeURIComponent(target));
        if (r.skinTo) {
          skinTo.value = r.skinTo;
          why.textContent = `This skin (${entry.sourceTexture}) does not sit `
            + `beside its mesh. ${target.split('/').pop()} uses `
            + `${r.skinTo} — the replacement goes there. Edit it if that is `
            + 'not what you meant.';
        } else {
          skinTo.value = '';
          why.textContent = `This skin (${entry.sourceTexture}) does not sit `
            + 'beside its mesh, and the target has no skin the resolver can '
            + 'find — say where it goes, or untick it.';
        }
      } catch (e) { /* leave the field for the user */ }
    }
    targetIn.addEventListener('input', debounce(fillSkinTarget, 350));
    fillSkinTarget();

    const go = el('button', 'primary', 'Stage this replacement');
    go.style.marginTop = '6px';
    const out = el('div', 'mut small');
    out.style.marginTop = '6px';
    out.style.whiteSpace = 'pre-wrap';

    go.addEventListener('click', async () => {
      const target = targetIn.value.trim();
      if (!target) { TOAST('say which path this replaces', 4000); return; }
      const roles = [];
      if (boxes.motion.checked) roles.push('motion');
      if (boxes.effect.checked) roles.push('effect', 'sound');
      go.disabled = true;
      out.textContent = 'staging…';
      try {
        const r = await API('/api/keep/stage', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: entry.id, swapFor: target, roles,
            skin: boxes.skin.checked,
            skinTo: boxes.skin.checked ? skinTo.value.trim() : '',
          }) });
        const lines = (r.wrote || []).map(w => '  staged ' + w);
        for (const s of (r.skipped || [])) lines.push('  left alone: ' + s);
        if (r.skinSkipped) lines.push('  ' + r.skinSkipped);
        // Composing changes what the file IS, so it is said out loud rather
        // than left to be noticed: this client keeps geometry in each action
        // file, and the donor keeps it in one, so the model was built in.
        for (const c of (r.composed || [])) {
          lines.push('  composed ' + c.path
                     + '  (model built in alongside the action)');
        }
        for (const m of (r.mismatch || [])) lines.push('  !! ' + m.why);
        // Those are logical paths -- where the GAME will look. Without the
        // tree they were written into they read as though nothing left the
        // page, which is exactly how "I had no idea where it went" happens.
        if (r.stageDir) {
          lines.push('', 'written under: ' + r.stageDir,
                     'nothing reaches a game install until you press Install.');
        }
        out.textContent = lines.join('\n') || 'nothing written';
        TOAST(`staged ${(r.wrote || []).length} file(s) over ${target}`, 4000);
        if (ON_CHANGE) ON_CHANGE();
      } catch (e) {
        out.textContent = 'stage failed: ' + e.message;
      }
      go.disabled = false;
    });
    host.appendChild(go);

    const open = el('button', 'ghost tiny', 'Mod staging…');
    open.style.marginLeft = '6px';
    open.addEventListener('click', openDrawer);
    host.appendChild(open);
    host.appendChild(out);
  }

  // ------------------------------------------------------------- the drawer
  function initDrawer() {
    const drawer = $('#drawer');
    if (!drawer) return;                    // a page without staging markup
    const btn = $('#btn-mods');
    if (btn) btn.addEventListener('click', openDrawer);
    const close = $('#drawer-close');
    if (close) close.addEventListener('click', () => drawer.classList.add('hidden'));
    drawer.addEventListener('click', e => {
      if (e.target.id === 'drawer') drawer.classList.add('hidden');
    });
    const bind = (id, url, ask) => {
      const b = $(id);
      if (!b) return;
      b.addEventListener('click', () => {
        // The confirmations guard the only two actions here that reach a
        // game install. They are part of the button, not decoration -- and
        // they name the install, because with a picker above them "which
        // one?" is now a real question.
        const msg = typeof ask === 'function' ? ask() : ask;
        if (msg && !confirm(msg)) return;
        runMod(url);
      });
    };
    bind('#btn-dry', '/api/install?dry=1');
    bind('#btn-install', '/api/install?dry=0', () =>
         'This writes the staged files into:\n\n  ' + chosenInstall()
         + '\n\ncomod.py backs up anything it displaces and records a manifest '
         + 'for THAT install, so "Uninstall / revert" can undo it there '
         + 'without touching any other copy.\n\nProceed?');
    bind('#btn-uninstall', '/api/uninstall?dry=0', () =>
         'Revert the install at:\n\n  ' + chosenInstall()
         + '\n\nDisplaced originals are restored from that install’s own '
         + 'backups.');
    const sel = $('#install-select');
    if (sel) sel.addEventListener('change', () => {
      // "custom" is the only option that is not itself a path.
      $('#install-path').value = sel.value === CUSTOM ? '' : sel.value;
      describeInstall();
    });
    const box = $('#install-path');
    if (box) box.addEventListener('input', debounce(describeInstall, 300));
  }

  const CUSTOM = ' custom';
  let INSTALLS = [];

  function debounce(fn, ms) {
    let t = null;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  /** The install a write is aimed at: the pasted path wins, else the pick. */
  function chosenInstall() {
    const box = $('#install-path');
    const typed = box ? box.value.trim() : '';
    if (typed) return typed;
    const sel = $('#install-select');
    return sel && sel.value !== CUSTOM ? sel.value : '';
  }

  async function loadInstalls() {
    const sel = $('#install-select');
    if (!sel) return;
    let doc;
    try { doc = await API('/api/installs'); } catch (e) { return; }
    INSTALLS = doc.installs || [];
    const keep = chosenInstall();
    sel.innerHTML = '';
    for (const i of INSTALLS) {
      const bits = [];
      if (i.isCurrent) bits.push('browsing');
      if (i.installed) bits.push(`${i.installedFiles} installed`);
      if (!i.ok) bits.push('not a game install');
      const o = el('option', null,
                   i.root + (bits.length ? `  —  ${bits.join(', ')}` : ''));
      o.value = i.root;
      sel.appendChild(o);
    }
    const other = el('option', null, 'somewhere else — paste a path below');
    other.value = CUSTOM;
    sel.appendChild(other);
    const match = INSTALLS.find(i => i.root === keep)
               || INSTALLS.find(i => i.isCurrent);
    sel.value = match ? match.root : CUSTOM;
    if (match && $('#install-path')) $('#install-path').value = match.root;
    describeInstall();
  }

  /** Say what the chosen install is and what is already on it, before the
   *  button is pressed rather than in the output after it. */
  async function describeInstall() {
    const note = $('#install-note');
    if (!note) return;
    const root = chosenInstall();
    if (!root) { note.textContent = 'No install chosen.'; return; }
    const known = INSTALLS.find(i => i.root.toLowerCase() === root.toLowerCase());
    if (known) {
      note.textContent = known.ok
        ? (known.installed
           ? `${known.installedFiles} file(s) already installed here — revert `
             + 'before installing again.'
           : 'Valid install, nothing installed here yet.')
        : 'This folder is not a game install.';
      return;
    }
    note.textContent = 'checking…';
    try {
      const r = await API('/api/installs?check=' + encodeURIComponent(root));
      note.textContent = r.check.ok
        ? 'Valid install.'
        : 'Not a game install: missing ' + (r.check.missing || []).join(', ');
    } catch (e) { note.textContent = 'could not check: ' + e.message; }
  }

  async function openDrawer() {
    const drawer = $('#drawer');
    if (!drawer) { TOAST('this page has no staging drawer', 4000); return; }
    drawer.classList.remove('hidden');
    await Promise.all([refreshStage(), loadInstalls()]);
  }

  async function refreshStage() {
    const host = $('#stage-list');
    if (!host) return;
    let data;
    try { data = await API('/api/stage'); }
    catch (e) {
      host.innerHTML = '';
      host.appendChild(el('div', 'err', e.message));
      return;
    }
    const dir = $('#stage-dir');
    if (dir && data.stageDir) dir.textContent = data.stageDir;
    host.innerHTML = '';
    if (!data.rows.length) {
      host.appendChild(el('p', 'mut',
        'Nothing staged yet. Collect an asset, then use Replace on it to '
        + 'write it over the one it should stand in for.'));
      return;
    }
    const t = el('table', 'stage');
    t.innerHTML = '<tr><th>status<th>logical path<th>orig<th>new<th>from<th></tr>';
    for (const r of data.rows) {
      const tr = el('tr');
      const st = el('td');
      st.appendChild(el('span', 'badge ' + (r.status === 'MODIFIED' ? 'stage' :
                                            r.status === 'NEW' ? 'loose' : 'arc'),
                        r.status));
      tr.appendChild(st);
      tr.appendChild(el('td', null, r.logical));
      tr.appendChild(el('td', null, r.oldBytes ? r.oldBytes.toLocaleString() : '—'));
      tr.appendChild(el('td', null, r.newBytes.toLocaleString()));
      tr.appendChild(el('td', null, r.originalSource || '—'));
      const act = el('td');
      const rm = el('button', 'ghost', 'unstage');
      rm.addEventListener('click', async () => {
        await API('/api/unstage?path=' + encodeURIComponent(r.logical),
                  { method: 'POST' });
        await refreshStage();
        if (ON_CHANGE) ON_CHANGE();
      });
      act.appendChild(rm);
      tr.appendChild(act);
      t.appendChild(tr);
    }
    host.appendChild(t);
  }

  async function runMod(url) {
    const out = $('#mod-output');
    if (out) out.textContent = 'running…';
    try {
      // The root travels in the body: it decides which folder gets written
      // to, and the server validates it as a game install before handing it
      // to comod.py. Empty means "the view you are browsing", the old
      // behaviour, kept as the default rather than as the only option.
      const r = await API(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root: chosenInstall() }) });
      if (out) {
        out.textContent = `$ ${r.cmd}\n\n${r.stdout}`
          + `${r.stderr ? '\n' + r.stderr : ''}\n[exit ${r.returncode}]`;
      }
    } catch (e) {
      if (out) out.textContent = 'failed: ' + e.message;
    }
    await refreshStage();
    if (ON_CHANGE) ON_CHANGE();
  }

  global.Swap = { configure, replacePanel, initDrawer, openDrawer,
                  refreshStage, skinFollowsMesh, entryFor, PROFILE_NAME };
})(window);
