/* app.js -- UI wiring for the CO asset viewer.
 *
 * Four jobs, matching the four requirements:
 *   (a) preview textures      -- thumbnail lists + full-size decode panel
 *   (b) swap texture files    -- drop a PNG/DDS in, see the model re-render,
 *                                then stage + install through comod.py
 *   (c) point at the files    -- the "Where this comes from" card
 *   (d) render accurately     -- gl.js; this file only feeds it data
 */

'use strict';

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined) e.textContent = txt;
  return e;
};

/* The sentinel for "has no tags", kept out of the tag namespace by a leading
 * NUL -- no real tag can contain one, so it cannot collide.
 *
 * WRITTEN AS AN ESCAPE, NOT AS A RAW BYTE. Three literal NULs used to sit in
 * this file's source, and `grep` therefore reported app.js as a BINARY FILE:
 * `grep -n 'function toast' tools/webui/app.js` printed
 * "Binary file ... matches" with no line number. Every text audit of the two
 * files that define the most shared helpers silently skipped them unless the
 * operator remembered `-a`. Identical string at runtime, text-safe source. */
const UNTAGGED = '\u0000untagged';

const AXIS_LABEL = { class: 'Class', gender: 'Gender', size: 'Body size', kind: 'Kind' };
const VALUE_LABEL = {
  any: 'Any class', unknown: 'Unclassified', 'n/a': 'n/a',
  female: 'Female', male: 'Male', other: 'Other body',
  small: 'Small', large: 'Large',
  armour: 'Armour', 'base body': 'Base body', 'npc body': 'NPC body',
};
const lbl = v => VALUE_LABEL[v] || v;

const state = {
  mode: 'category',
  table: null,
  /** Category browsing: the taxonomy tree and where we are in it. */
  categories: [],
  cat: { id: null, sub: null, role: null, group: null },
  /** The last /api/catfiles payload, kept so the Group disclosure can redraw
   *  its own chips without a round trip. */
  catFacets: null,
  /** Group disclosure: `null` means "decide from the size of the list"
   *  (see GROUP_COLLAPSE_OVER); `true`/`false` is an explicit choice by the
   *  user and outranks the default until they leave the category. */
  catGroupsOpen: null,
  /** "hide animation assets": the motion buckets cannot be previewed in this
   *  mode. Restored from localStorage below, alongside the panel-collapse and
   *  folder-tree state, so it survives a trip to the builder and back. */
  hideMotion: false,
  /** The server's count of what the filter removed, catalogue-wide. `null`
   *  until it has answered -- a filter banner must not invent a number. */
  motionHidden: null,
  maps: [],
  mapName: null,
  /** The end goal is "this body + this left weapon + this right weapon".
   *  Assembly needs the sibling workstreams, but the selection model carries
   *  the slots now so it does not have to be retrofitted later. */
  loadout: {},               // slot name (from RolePart.ini) -> equipped part
  partManifest: null,
  figure: null,
  anchors: null,
  lastFiguredBody: null,
  /** active facet selection: axis -> Set(values). Multi-select inside an axis
   *  is OR; across axes it is AND. */
  sel: { class: new Set(), gender: new Set(), size: new Set(), kind: new Set() },
  selTags: new Set(),
  /** The Your-tags search box: what is typed in it, and whether the caret was
   *  in it when the facet host was last rebuilt. Neither is a filter on the
   *  results -- both are a view over the tag vocabulary. */
  tagQuery: '',
  tagQueryFocus: false,
  group: false,
  lastQuery: null,       // the last /api/appearances payload
  /** Keyboard navigation over whatever the list is currently showing.
   *  `items` mirrors the rendered rows exactly, so navigation always respects
   *  the active filters and the grouped/flat mode. */
  nav: { mode: 'appearance', items: [], index: -1, variant: 0 },
  /** Monotonic load token. Bumped the instant a selection changes; every async
   *  UI function captures it on entry and bails if it has moved on, so a late
   *  response for an abandoned selection can never overwrite the current one. */
  loadToken: 0,
  vocabulary: {},
  appearances: [],
  files: [],
  selection: null,       // {kind:'appearance'|'file', ...}
  meshPath: null,
  texPath: null,
  previewToken: null,    // active un-staged texture override
  meshData: null,
  status: null,
};

let viewer = null;
// exposed so the viewport can be driven from the console / a test harness
window.state = state;

// ------------------------------------------------------------- server picker
//
// One selector, top left: the baseline install ("base") or any server profile
// catalogued in the COmmunity Library. Switching swaps the whole catalogue on
// the server side -- namespace, appearance tables, provenance -- then reloads
// the page, because every pane caches derived state.
function serverOptionLabel(s) {
  return s.name + (s.clientVersion ? ` (v${s.clientVersion})` : '') +
    (s.files ? ` — ${s.files.toLocaleString()} files` : '');
}

/** Re-read /api/servers and re-label the picker in place.
 *
 *  The Collection is the one profile this page can *change*, so its
 *  "N files" is the one label that goes stale while you look at it. The
 *  listener stays bound because the options are relabelled rather than
 *  rebuilt; a profile that did not exist at load -- the Collection
 *  publishes itself the first time you keep something -- is appended. */
async function refreshServerLabels() {
  const sel = $('#server-select');
  if (!sel) return;
  let doc;
  try { doc = await api('/api/servers'); } catch (e) { return; }
  const pending = new Map((doc.servers || []).map(s => [s.name, s]));
  if (!sel.options.length && pending.size) {
    // The picker starts empty when the library had no profiles at load.
    // Keeping the first entry means "the baseline install" everywhere.
    const b = el('option', null, 'base — ' + (doc.base || 'install'));
    b.value = '';
    sel.appendChild(b);
    sel.value = doc.current || '';
  }
  for (const o of sel.options) {
    if (!o.value) continue;
    const s = pending.get(o.value);
    if (s) { o.textContent = serverOptionLabel(s); pending.delete(o.value); }
  }
  for (const s of pending.values()) {
    const o = el('option', null, serverOptionLabel(s));
    o.value = s.name;
    sel.appendChild(o);
  }
  if (sel.options.length > 1) $('#server-label').classList.remove('hidden');
}

async function initServerPicker(st) {
  let doc;
  try { doc = await api('/api/servers'); } catch (e) { return; }
  const sel = $('#server-select');
  const label = $('#server-label');
  // The two dropdowns were merged into one (`basepicker.js`), so this markup
  // is gone. Leave rather than throw on the missing label -- the path list
  // includes library servers now.
  if (!sel || !label) return;
  if (!doc.servers || !doc.servers.length) {
    label.classList.add('hidden');
    $('#origin-label').classList.add('hidden');
    return;
  }
  sel.innerHTML = '';
  const b = el('option', null, 'base — ' + (doc.base || 'install'));
  b.value = '';
  sel.appendChild(b);
  for (const s of doc.servers) {
    const o = el('option', null, serverOptionLabel(s));
    o.value = s.name;
    sel.appendChild(o);
  }
  sel.value = doc.current || '';
  $('#origin-label').classList.toggle('hidden', !doc.current);
  label.classList.remove('hidden');
  // Name the filter after the server, so "unique to this client" reads as
  // "Zephyr only" rather than as jargon about where bytes are stored.
  const tag = (st && st.serverTag) || '';
  if (tag) {
    const opts = $('#source-select').options;
    opts[1].textContent = `${tag} only (unique to this server)`;
    opts[2].textContent = `shared with the base install`;
    $('#origin-label').title =
      `${tag} only = assets this server does not share with your base ` +
      `install — the ones tagged ${tag}.`;
  }
  sel.addEventListener('change', async () => {
    const name = sel.value;
    sel.disabled = true;
    toast(name ? `switching to ${name}… (first switch builds the catalogue)`
               : 'switching to the baseline install…', 8000);
    try {
      await api('/api/server', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: name }) });
      location.reload();
    } catch (e) {
      sel.disabled = false;
      toast('switch failed: ' + e.message, 6000);
      sel.value = doc.current || '';
    }
  });
}

/** "Open in the model viewer" for whatever mesh is on screen.
 *
 *  The prose link beside it goes to the builder's *character* side, which
 *  starts from a body; that is why following it loses the thing you were
 *  looking at. This carries the actual mesh (and the texture already
 *  resolved for it) so the model stage opens on the same asset.
 */
function renderModelViewerLink() {
  const host = $('#model-open');
  if (!host) return;
  host.innerHTML = '';
  const mesh = state.meshPath;
  if (!mesh) {
    host.appendChild(el('div', 'mut small',
      'Select a mesh to open it in the model viewer.'));
    return;
  }
  const p = new URLSearchParams({ mesh });
  if (state.texPath) p.set('tex', state.texPath);
  const a = el('a', 'navlink-inline', '→ Open this mesh in the model viewer');
  a.href = '/models#' + p.toString();
  a.title = 'Same geometry and skin, on the Model Viewer\u2019s stage: ' +
            'zoom, camera and lighting, without needing a catalogue entry.';
  host.appendChild(a);
  const what = el('div', 'mut small');
  what.textContent = mesh.split('/').pop() +
    (state.texPath ? ' · ' + state.texPath.split('/').pop() : ' · untextured');
  host.appendChild(what);
}

// ------------------------------------------------------------ asset library
//
// Choosing the library is a folder question, and a browser cannot hand a page
// a real folder path. So: discover the likely ones server-side and offer them
// as one-click buttons, with pasting a path as the fallback rather than the
// only option.
function initLibraryPanel() {
  const host = $('#libdrawer');
  const out = $('#lib-output');
  const open = () => host.classList.remove('hidden');
  const close = () => host.classList.add('hidden');

  async function refresh() {
    let doc;
    try { doc = await api('/api/library'); }
    catch (e) { out.textContent = 'could not read the library: ' + e.message; return; }
    const cur = $('#lib-current');
    cur.innerHTML = '';
    if (doc.library && doc.library.ok) {
      const names = doc.library.servers.map(s => `${s.tag} (${s.name})`).join(', ');
      cur.appendChild(el('div', null, 'Using: '));
      cur.appendChild(el('code', null, doc.library.path));
      cur.appendChild(el('div', 'mut small',
        `${doc.library.servers.length} server profile(s): ${names}`));
      $('#lib-path').value = doc.library.path;
    } else if (doc.library) {
      cur.appendChild(el('div', 'mut',
        `Not usable: ${doc.library.path} — ${doc.library.why}`));
    } else {
      cur.appendChild(el('div', 'mut', 'No library chosen yet.'));
    }

    const found = $('#lib-found');
    found.innerHTML = '';
    if (doc.candidates && doc.candidates.length) {
      found.appendChild(el('div', 'mut small', 'Found on this machine:'));
      for (const c of doc.candidates) {
        const b = el('button', 'ghost');
        b.style.display = 'block';
        b.style.marginTop = '4px';
        b.textContent = `${c.path}  —  ` +
          c.servers.map(s => s.tag).join(', ');
        b.addEventListener('click', () => save(c.path));
        found.appendChild(b);
      }
    }
  }

  async function save(path) {
    out.textContent = 'saving…';
    try {
      const r = await api('/api/setlibrary', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }) });
      out.textContent = path
        ? `library set to ${r.library.path} — reloading…`
        : 'library forgotten — reloading…';
      setTimeout(() => location.reload(), 500);
    } catch (e) {
      out.textContent = 'failed: ' + e.message;
    }
  }

  $('#btn-library').addEventListener('click', async () => {
    if (!host.classList.contains('hidden')) { close(); return; }
    open();
    out.textContent = '';
    await refresh();
  });
  $('#libdrawer-close').addEventListener('click', close);
  $('#lib-save').addEventListener('click', () => save($('#lib-path').value.trim()));
  $('#lib-clear').addEventListener('click', () => save(''));
}

// ------------------------------------------------------------------ helpers
async function api(path, opts) {
  // A bare "NetworkError" says the request never completed and nothing about
  // why. Name the request and the two things that actually cause it, so the
  // toast is a lead rather than a dead end.
  let r;
  try {
    try {
      r = await fetch(path, opts);
    } catch (first) {
      // A connection that died in the pool fails this attempt and nothing after it.
      // Chrome retries a POST like that for us; Firefox does not.
      r = await fetch(path, opts);
    }
  } catch (e) {
    const how = (opts && opts.method) || 'GET';
    throw new Error(
      `${how} ${path} never reached the viewer (${e.message}). Either it is `
      + `no longer running at ${location.origin}, or something is blocking `
      + `${how} requests to localhost. Use "Test connection" to tell which.`);
  }
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('json')) {
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r;
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// --------------------------------------------------- load throttle + ordering
//
// Selection must feel instant; the render is allowed to trail. So the *load* is
// debounced (holding an arrow key fires nothing until you pause) while the row
// highlight moves immediately. Every load carries a token; async work checks it
// before touching the viewport or the panels, so out-of-order responses cannot
// land on the wrong asset.
const NAV_DEBOUNCE_MS = 130;
let loadTimer = null;

/** Capture the current token at the top of an async UI function. */
const tokenNow = () => state.loadToken;
/** True while `tk` is still the selection the user is waiting for. */
const stillCurrent = tk => state.loadToken === tk;

function requestLoad(fn, { immediate = false } = {}) {
  const tk = ++state.loadToken;
  clearTimeout(loadTimer);
  const run = () => { if (stillCurrent(tk)) fn(tk); };
  if (immediate) run();
  else loadTimer = setTimeout(run, NAV_DEBOUNCE_MS);
  return tk;
}

function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

function texUrl(path, { size = 0, preview = null, src = null } = {}) {
  const p = new URLSearchParams({ path });
  if (size) p.set('size', size);
  if (preview) p.set('preview', preview);
  if (src) p.set('src', src);
  return '/api/texture?' + p.toString();
}

/** A picture of any asset, mesh included.
 *
 *  `/api/thumb` serves task #21's pre-rendered PNG when it has got to this
 *  asset, the decoded texture when it has not, and 404 when there is nothing --
 *  which the <img> shows as the checkerboard placeholder. Batch rendering is
 *  incremental and resumable, so "no thumbnail yet" is a normal state and the
 *  list must not look broken in it. */
function thumbUrl(path, { size = 48 } = {}) {
  const p = new URLSearchParams({ path });
  if (size) p.set('size', size);
  return '/api/thumb?' + p.toString();
}

function cmdBlock(text) {
  const c = el('code', 'cmd', text);
  c.title = 'click to copy';
  c.addEventListener('click', () => {
    navigator.clipboard.writeText(text).then(() => {
      const o = c.textContent;
      c.textContent = 'copied to clipboard';
      setTimeout(() => { c.textContent = o; }, 800);
    }, () => toast('clipboard blocked by the browser'));
  });
  return c;
}

function kv(pairs) {
  /* NULL IS A VALUE THE SERVER SENT; '' IS THE CALLER CHOOSING TO HIDE.
   *
   * This used to skip null, undefined and '' alike, so a row the server
   * answered as null simply VANISHED -- and "the server said null" was then
   * indistinguishable from "this panel does not show that field". Three
   * defects in one night were fields the API returned that no panel
   * rendered; a helper that drops nulls is that failure built in.
   *
   * null/undefined now render an em dash carrying the reason on hover.
   * '' still omits the row, because that is the caller's own choice and all
   * 13 call sites pass an explicit key list. The em-dash convention is
   * borrowed from ui.js field(), not invented here. */
  const d = el('dl', 'kv');
  for (const [k, v] of pairs) {
    if (v === '') continue;
    d.appendChild(el('dt', null, k));
    const dd = el('dd');
    if (v === null || v === undefined) {
      dd.className = 'null';
      dd.textContent = '—';
      dd.title = 'returned as ' + (v === null ? 'null' : 'undefined');
    } else if (v instanceof Node) {
      dd.appendChild(v);
    } else {
      dd.textContent = String(v);
    }
    d.appendChild(dd);
  }
  return d;
}

// ------------------------------------------------------------------ boot
async function boot() {
  try {
    viewer = new Viewer($('#gl'));
    window.viewer = viewer;
  } catch (e) {
    $('#gl-msg').textContent = 'WebGL unavailable: ' + e.message;
  }
  // Collapsible detail panels -- the same cards.js the builder uses. This page
  // had the `cursor: pointer` on every `.card h2` and NO listener behind it,
  // which is the "I can see the cursor change, but it doesnt collaps" bug.
  // No button id: the header's "Collapse panels" control was removed (the
  // card headers are each collapsible, so it was a second way to say the
  // same thing). `C` below still toggles the lot.
  CardPanels.init('coviewer.collapsed');
  bindControls();
  bindKeys();
  if (viewer) {
    // the lock flag survives a reload, so reflect what was restored
    $('#chk-lock').checked = viewer.opts.lock;
    $('#lock-label').classList.toggle('on', viewer.opts.lock);
  }

  const st = await api('/api/status');
  state.status = st;
  await initServerPicker(st);
  initLibraryPanel();
  $('#statusline').textContent =
    (st.server ? `server: ${st.server} · ` : '') +
    `${st.root} · ${st.looseFiles.toLocaleString()} loose files · ` +
    Object.entries(st.archives).map(([k, v]) => `${k} ${v.entries.toLocaleString()}`).join(' · ') +
    ` · ${st.knownPaths.toLocaleString()} resolvable paths` +
    (st.numpy ? '' : ' · numpy absent (pure-python paths)');
  $('#stage-dir').textContent = st.stageDir;

  const tables = await api('/api/tables');
  const sel = $('#table-select');
  for (const t of tables) {
    const o = el('option', null, `${t.name} — ${t.count} (${t.ini})`);
    o.value = t.name;
    sel.appendChild(o);
  }
  // Prefer 'body', but never land on an empty table: a community client
  // may ship no armor.ini at all, and an empty pane reads as a broken one.
  const filled = tables.filter(t => t.count > 0);
  const body = filled.find(t => t.name === 'body');
  sel.value = (body || filled[0] || tables[0] || {}).name || '';
  state.table = sel.value;
  await refreshVocabulary();
  const hadLoadout = restoreLoadout();
  await loadPartManifest();
  $('#view-mode').value = viewer ? viewer.viewMode : 'asset';
  await loadCategories();          // Categories is the default landing pane
  await fillDirs('', 'c3/mesh');
  if (hadLoadout) { setViewMode('character'); renderFigure(); }
  // The Collection card must say something before anything is selected --
  // an empty card reads as a broken one.
  renderCollect();

  // #file=<logical> opens straight onto one asset. This is the return leg of
  // the model-viewer round trip, and it makes any asset linkable.
  const fromUrl = new URLSearchParams(location.hash.replace(/^#/, ''));
  const wanted = fromUrl.get('file');
  if (wanted) {
    const tab = document.querySelector('.tab[data-mode="files"]');
    if (tab) tab.click();
    const search = $('#file-search');
    if (search) {
      search.value = wanted;
      await loadFiles();
    }
    await selectFile(wanted);
  }
}

/** Repopulate the directory dropdown for an extension filter. */
// ------------------------------------------------------------- collection
//
// The library holds everything a client shipped; the Collection is what you
// have chosen to keep. Collecting copies the mesh AND the skin currently
// resolved for it, so an entry is a self-contained pair rather than a
// pointer into an archive that may not be there later.
let collectionMeta = null;

let collectFor = null;

async function renderCollect({ force = false } = {}) {
  const host = $('#collect-body');
  if (!host) return;
  const mesh = state.meshPath;
  // Rebuilding the card throws away whatever is half-typed in the
  // name box. A late texture load calls this, so a name typed while
  // the skin was still decoding was being silently reverted to the
  // prefill. Leave the card alone unless the subject changed.
  if (!force && collectFor === (mesh || '') && host.querySelector('input')) {
    return;
  }
  collectFor = mesh || '';
  host.innerHTML = '';
  if (!collectionMeta) {
    try { collectionMeta = await api('/api/keep'); }
    catch (e) { host.appendChild(el('div', 'mut small', 'unavailable')); return; }
  }
  if (!collectionMeta.library) {
    host.appendChild(el('div', 'mut small',
      'Choose a COmmunity Library first (the "Asset library" button).'));
    return;
  }
  const total = Object.values(collectionMeta.counts || {})
    .reduce((a, b) => a + b, 0);
  const summary = el('div', 'mut small', `${total} collected`);
  host.appendChild(summary);

  if (!mesh) {
    host.appendChild(el('div', 'mut small',
      'Select a mesh to collect it.'));
    return;
  }
  // Matched by the path it came from OR by the Collection's own copy: this
  // page can browse the `collection` view too, and there the entry is at
  // collection/<category>/<id>.c3. See Swap.entryFor.
  const hit = Swap.entryFor(collectionMeta.entries, mesh);
  const existing = hit && hit.entry;
  if (hit && hit.isCopy) { renderCollectedCopy(host, existing); return; }

  const row = el('div');
  row.style.marginTop = '6px';
  const catSel = el('select');
  for (const c of Object.keys(collectionMeta.categories || {})) {
    const o = el('option', null, c);
    o.value = c;
    catSel.appendChild(o);
  }
  catSel.value = existing ? existing.category : guessCategory(mesh);
  catSel.title = (collectionMeta.categories || {})[catSel.value] || '';
  catSel.addEventListener('change', () => {
    catSel.title = (collectionMeta.categories || {})[catSel.value] || '';
  });
  const nameIn = el('input');
  nameIn.type = 'text';
  nameIn.placeholder = 'name (optional)';
  nameIn.value = existing ? existing.name : '';
  nameIn.style.width = '100%';
  nameIn.style.marginTop = '4px';

  const go = el('button', 'primary', existing ? 'Update entry' : 'Collect');
  go.style.marginTop = '6px';
  go.addEventListener('click', async () => {
    go.disabled = true;
    try {
      const r = await api('/api/keep/add', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: mesh, texture: state.texPath || '',
                               category: catSel.value,
                               name: nameIn.value.trim() }) });
      collectionMeta = null;
      toast(`collected as ${r.entry.id}`);
      renderCollect({ force: true });
      // What you keep joins the library immediately: the profile is
      // republished server-side, so the picker's count is re-read here.
      refreshServerLabels();
      afterCollectionChanged(r);
    } catch (e) {
      toast('collect failed: ' + e.message, 5000);
      go.disabled = false;
    }
  });

  row.appendChild(catSel);
  row.appendChild(nameIn);
  row.appendChild(go);
  const diag = el('button', 'ghost tiny', 'Test connection');
  diag.style.marginLeft = '6px';
  diag.title = 'Checks whether this page can reach the viewer, and '
             + 'whether POSTs specifically are blocked.';
  diag.addEventListener('click', () => diagnoseCollect(host));
  row.appendChild(diag);
  host.appendChild(row);

  if (existing) {
    const info = el('div', 'mut small');
    info.style.marginTop = '6px';
    const np = (existing.parts || []).length;
    info.textContent = `already collected as ${existing.id}` +
      (existing.skins && existing.skins.length ? ' with skin' : ', no skin') +
      (np ? `, ${np} motion/effect file(s)` : '');
    host.appendChild(info);

    // A prompt() asked for a path and then wrote everything the entry had,
    // with no say in it -- and no way to place a skin that does not sit
    // beside its mesh, which it then told you to do yourself.
    const panel = el('div', 'swap-panel');
    panel.style.marginTop = '8px';
    const stage = el('button', 'ghost tiny', 'Replace an asset with this…');
    stage.title = 'Write this entry into Installed/stage/ over the asset it '
                + 'replaces, choosing what travels with it. Nothing touches '
                + 'the game until you install.';
    stage.addEventListener('click', () => {
      if (panel.childElementCount) { panel.innerHTML = ''; return; }
      Swap.replacePanel(panel, existing, { defaultTarget: existing.swapFor || mesh });
    });
    host.appendChild(stage);

    const drop = el('button', 'ghost tiny', 'Remove');
    drop.style.marginLeft = '6px';
    drop.addEventListener('click', async () => {
      try {
        const r = await api('/api/keep/remove', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: existing.id }) });
        collectionMeta = null;
        toast('removed from the collection');
        renderCollect({ force: true });
        refreshServerLabels();
        afterCollectionChanged(r);
      } catch (e) { toast('remove failed: ' + e.message, 5000); }
    });
    host.appendChild(drop);
    host.appendChild(panel);
  }
}

/** The card when the selected file IS a Collection entry's own copy.
 *
 *  Browsing the `collection` view to find what you kept is the obvious way
 *  to go and use it. What must not be offered here is Collect: posting this
 *  path would file a second entry whose source is the first entry's copy.
 */
function renderCollectedCopy(host, entry) {
  host.innerHTML = '';
  host.appendChild(el('div', 'mut small',
    `In the Collection as ${entry.id} — ${entry.category}.`));
  const nMotion = (entry.parts || []).filter(p => p.role === 'motion').length;
  const nOther = (entry.parts || []).length - nMotion;
  host.appendChild(el('div', 'mut small',
    (entry.skins && entry.skins.length ? 'skin kept' : 'no skin')
    + (nMotion ? ` · ${nMotion} animation file(s)` : ' · no animations')
    + (nOther ? ` · ${nOther} effect/sound file(s)` : '')));

  const panel = el('div', 'swap-panel');
  panel.style.marginTop = '8px';
  const stage = el('button', 'ghost tiny', 'Replace an asset with this…');
  stage.title = 'Write this entry into Installed/stage/ over the asset it '
              + 'replaces, choosing what travels with it. Nothing touches '
              + 'the game until you install.';
  stage.addEventListener('click', () => {
    if (panel.childElementCount) { panel.innerHTML = ''; return; }
    Swap.replacePanel(panel, entry,
                      { defaultTarget: entry.swapFor || entry.sourceMesh });
  });
  host.appendChild(stage);

  const drop = el('button', 'ghost tiny', 'Remove');
  drop.style.marginLeft = '6px';
  drop.addEventListener('click', async () => {
    try {
      const r = await api('/api/keep/remove', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entry.id }) });
      collectionMeta = null;
      toast('removed from the collection');
      renderCollect({ force: true });
      refreshServerLabels();
      afterCollectionChanged(r);
    } catch (e) { toast('remove failed: ' + e.message, 5000); }
  });
  host.appendChild(drop);
  host.appendChild(panel);

  if (entry.sourceMesh) {
    const from = el('div', 'mut small');
    from.style.marginTop = '6px';
    from.textContent = 'collected from '
      + (entry.server ? entry.server + ':' : '') + entry.sourceMesh;
    host.appendChild(from);
  }
}

/** The Collection changed; if you are *looking* at it, show the change.
 *
 *  The server rebuilds its catalogue and says so with `view.rebuilt`. Every
 *  pane on this page -- folder tree, extension filter, model list, the row
 *  list itself -- was derived from the namespace as it was when the page
 *  loaded, so a reload is what makes them agree. Only when the Collection is
 *  the active view: collecting *into* it from another client changes nothing
 *  on screen. */
function afterCollectionChanged(r) {
  if (!r || !r.view || !r.view.rebuilt) return;
  toast('the Collection changed — reloading the view…', 1500);
  setTimeout(() => location.reload(), 700);
}

/** A first guess at the shelf, from where the asset lives. Always editable:
 *  the path is evidence, not a verdict. */
function guessCategory(p) {
  const k = (p || '').toLowerCase();
  if (k.includes('/monster/')) return 'Monsters';
  if (k.includes('/npc/')) return 'NPCs';
  if (k.includes('/mount/')) return 'Mounts';
  if (k.includes('/weapon')) return 'Weapons';
  if (k.includes('/shield')) return 'Shields';
  if (k.includes('/armet') || k.includes('/hair')) return 'Headgear';
  if (k.includes('garment')) return 'Garments';
  if (k.includes('/effect/')) return 'Effects';
  if (k.startsWith('data/map') || k.includes('/map/')) return 'Maps';
  if (k.startsWith('data/interface') || k.includes('icon')) return 'UI';
  if (/^c3\/\d{4}\//.test(k)) return 'Characters';
  return 'Other';
}


/** Say which layer failed, because "NetworkError" alone does not.
 *  GET works but POST does not => something is refusing non-GET requests to
 *  loopback (security software, an extension), not the feature. */
const UI_BUILD = 'ui-2026-08-04-e-name';

async function diagnoseCollect(host) {
  const out = el('div', 'mut small');
  out.style.marginTop = '6px';
  out.style.whiteSpace = 'pre-wrap';
  out.textContent = 'checking…';
  host.appendChild(out);
  const lines = [];
  try {
    await api('/api/keep');
    lines.push('GET  /api/collection: ok');
  } catch (e) { lines.push('GET  /api/collection: ' + e.message); }
  try {
    const r = await api('/api/ping', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    lines.push('POST /api/ping: ok (origin ' + (r.origin || 'none') + ')');
  } catch (e) {
    lines.push('POST /api/ping: ' + e.message);
    lines.push('→ If GET works and POST does not, something between this '
             + 'page and the viewer is refusing POSTs to localhost — a '
             + 'browser extension or security software that filters local '
             + 'traffic. The viewer itself is answering.');
  }
  lines.push('page origin: ' + location.origin);
  lines.push('ui build: ' + UI_BUILD);
  // Exercise the real endpoint, not just a no-op POST: a
  // failing Collect and a failing request look identical
  // from the outside, and they are not the same problem.
  const subject = (typeof state !== 'undefined' && state.meshPath)
    || (typeof B !== 'undefined' && B.adhoc && B.adhoc.mesh) || '';
  if (subject) {
    try {
      const d = await api('/api/keep/add', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: subject, dryRun: true,
                               category: 'Other' }) });
      lines.push('POST /api/keep/add (dry run): ok — '
                 + d.parts + ' part(s), ' + d.skins + ' skin(s)');
    } catch (e) {
      lines.push('POST /api/keep/add (dry run): ' + e.message);
    }
  } else {
    lines.push('POST /api/collect: not tried (nothing selected)');
  }
  out.textContent = lines.join('\n');
}

// ------------------------------------------------------------ folder tree
//
// The picker used to be a flat <select> holding the 500 largest folders of
// 5,386, ordered by file count -- so a folder was findable only if it was
// big, and its place in the hierarchy was invisible. The tree is the shape
// the paths already have: alphabetical at every level, open by default,
// each folder collapsible.
const DIR_CLOSED_KEY = 'coviewer.dirsClosed';
/** Folders the user has closed. Empty by default: fully open on first run. */
let dirClosed = new Set();
try {
  dirClosed = new Set(JSON.parse(localStorage.getItem(DIR_CLOSED_KEY) || '[]'));
} catch (e) { /* ignore */ }

function saveDirClosed() {
  try {
    localStorage.setItem(DIR_CLOSED_KEY, JSON.stringify([...dirClosed]));
  } catch (e) { /* ignore */ }
}

/** Flat ["a/b", count] rows -> nested nodes, with subtree totals. */
function buildDirTree(rows) {
  const root = { name: '', path: '', own: 0, total: 0, kids: new Map() };
  for (const r of rows) {
    const parts = (r.dir || '').split('/').filter(Boolean);
    let node = root;
    root.total += r.count;
    let acc = '';
    for (const seg of parts) {
      acc = acc ? acc + '/' + seg : seg;
      let kid = node.kids.get(seg);
      if (!kid) {
        kid = { name: seg, path: acc, own: 0, total: 0, kids: new Map() };
        node.kids.set(seg, kid);
      }
      kid.total += r.count;
      node = kid;
    }
    node.own += r.count;
  }
  return root;
}

function renderDirTree(root) {
  const host = $('#dir-tree');
  host.innerHTML = '';

  const frag = document.createDocumentFragment();
  const allRow = el('div', 'dirrow' + (state.dir === '' ? ' on' : ''));
  allRow.appendChild(el('span', 'dirtwist leaf', ''));
  allRow.appendChild(el('span', 'dirname', 'all folders'));
  allRow.appendChild(el('span', 'dircount', String(root.total)));
  allRow.addEventListener('click', () => selectDir(''));
  frag.appendChild(allRow);

  const walk = (node, into) => {
    const kids = [...node.kids.values()]
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const k of kids) {
      const wrap = el('div', 'dirnode' + (dirClosed.has(k.path) ? ' closed' : ''));
      const row = el('div', 'dirrow' + (state.dir === k.path ? ' on' : ''));
      const hasKids = k.kids.size > 0;
      const tw = el('span', 'dirtwist' + (hasKids ? '' : ' leaf'),
                    hasKids ? (dirClosed.has(k.path) ? '\u25B6' : '\u25BC') : '');
      if (hasKids) {
        tw.addEventListener('click', ev => {
          ev.stopPropagation();
          if (dirClosed.has(k.path)) dirClosed.delete(k.path);
          else dirClosed.add(k.path);
          saveDirClosed();
          wrap.classList.toggle('closed');
          tw.textContent = dirClosed.has(k.path) ? '\u25B6' : '\u25BC';
        });
      }
      row.appendChild(tw);
      row.appendChild(el('span', 'dirname', k.name));
      row.appendChild(el('span', 'dircount', String(k.total)));
      row.title = k.path + '  —  ' + k.total + ' file(s) in this folder and below';
      row.addEventListener('click', () => selectDir(k.path));
      wrap.appendChild(row);
      if (hasKids) {
        const box = el('div', 'dirkids');
        walk(k, box);
        wrap.appendChild(box);
      }
      into.appendChild(wrap);
    }
  };
  walk(root, frag);
  host.appendChild(frag);
}

function selectDir(path) {
  state.dir = path;
  for (const r of document.querySelectorAll('#dir-tree .dirrow')) {
    r.classList.remove('on');
  }
  renderDirTree(state.dirTree);
  loadFilesFirstPage();
}

async function fillDirs(ext, prefer) {
  const dirs = await api('/api/dirs?ext=' + encodeURIComponent(ext || ''));
  state.dirTree = buildDirTree(dirs);
  if (state.dir === undefined) state.dir = '';
  // keep the current folder if it still exists under this extension filter
  if (state.dir && !dirs.some(d => d.dir === state.dir)) {
    if (prefer && dirs.some(d => d.dir === prefer)) state.dir = prefer;
    else state.dir = '';
  } else if (!state.dir && prefer && dirs.some(d => d.dir === prefer)) {
    state.dir = prefer;
  }
  renderDirTree(state.dirTree);
}

function bindControls() {
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    state.mode = b.dataset.mode;
    for (const m of ['category', 'appearance', 'map', 'files']) {
      $('#pane-' + m).classList.toggle('hidden', state.mode !== m);
    }
    if (state.mode === 'files' && !state.files.length) loadFiles();
    if (state.mode === 'category' && !state.categories.length) loadCategories();
    if (state.mode === 'map' && !state.maps.length) loadMaps();
    if (state.mode === 'appearance' && !state.lastQuery) loadAppearances();
  }));
  // ---- effect playback ----
  $('#btn-fx-play').addEventListener('click', () => {
    if (fx.playing) fxPause(); else fxPlay();
  });
  $('#btn-fx-stop').addEventListener('click', fxStop);
  $('#fx-slider').addEventListener('input', e => {
    fxPause();
    viewer.resetEffects();
    // Ribbons are path-dependent -- they are built by advancing, not sampled --
    // so scrubbing has to replay from zero to land on the same smear.
    const target = +e.target.value;
    const stepMs = 1000 / 60;
    for (let t = 0; t < target; t += stepMs) viewer.setEffectTime(t, fxParentFor);
    fxSeek(target);
  });
  $('#chk-fx-swing').addEventListener('change', e => {
    fx.swing = e.target.checked;
    viewer.resetEffects();
    fxSeek(0);
    if (!fx.playing) fxPlay();
  });

  initCatSince();
  const hideBox = $('#chk-hide-motion');
  if (hideBox) {
    hideBox.checked = state.hideMotion;
    hideBox.addEventListener('change', e => setHideMotion(e.target.checked));
  }
  showMotionNote();

  $('#cat-search').addEventListener('input', debounce(loadCategoryFirstPage, 240));
  if ($('#cat-clear-filters'))
    $('#cat-clear-filters').addEventListener('click', clearCatFilters);
  $('#map-search').addEventListener('input', debounce(renderMapList, 200));

  $('#table-select').addEventListener('change', e => {
    state.table = e.target.value;
    for (const s of Object.values(state.sel)) s.clear();
    loadAppearances();
  });
  $('#app-search').addEventListener('input', debounce(loadAppearances, 220));
  $('#chk-group').addEventListener('change', e => {
    state.group = e.target.checked; loadAppearances();
  });
  $('#btn-clear').addEventListener('click', clearFilters);
  $('#btn-bulktag').addEventListener('click', bulkTag);
  $('#btn-export').addEventListener('click', () => {
    const fmt = confirm('OK for CSV (with the derived class/gender/size columns),\n' +
                        'Cancel for raw JSON (re-importable).') ? 'csv' : 'json';
    window.open('/api/tags/export?format=' + fmt, '_blank');
  });
  $('#source-select').addEventListener('change', loadFilesFirstPage);
  $('#dir-expand').addEventListener('click', () => {
    dirClosed.clear();
    saveDirClosed();
    renderDirTree(state.dirTree);
  });
  $('#dir-collapse').addEventListener('click', () => {
    const mark = node => {
      for (const k of node.kids.values()) {
        if (k.kids.size) dirClosed.add(k.path);
        mark(k);
      }
    };
    if (state.dirTree) mark(state.dirTree);
    saveDirClosed();
    renderDirTree(state.dirTree);
  });
  $('#ext-select').addEventListener('change', async () => {
    const ext = $('#ext-select').value;
    await fillDirs(ext, ext === '.c3' ? 'c3/mesh' : 'c3/texture');
    loadFilesFirstPage();
  });
  $('#file-search').addEventListener('input', debounce(loadFilesFirstPage, 260));

  const rerender = () => { if (viewer) viewer.draw(); };
  $('#cull-mode').addEventListener('change', e => { viewer.opts.cull = e.target.value; rerender(); });
  $('#alpha-mode').addEventListener('change', e => { viewer.opts.alpha = e.target.value; rerender(); });
  $('#shade-mode').addEventListener('change', e => { viewer.opts.shade = e.target.value; rerender(); });
  $('#chk-wire').addEventListener('change', e => { viewer.opts.wire = e.target.checked; rerender(); });
  $('#chk-sockets').addEventListener('change', e => { viewer.opts.sockets = e.target.checked; rerender(); });
  $('#chk-grid').addEventListener('change', e => { viewer.opts.grid = e.target.checked; rerender(); });
  $('#chk-vcolor').addEventListener('change', e => { viewer.opts.vcolor = e.target.checked; rerender(); });
  $('#btn-reset').addEventListener('click', () => {
    viewer.resetView();
    // in character view, reset means "frame the body", not the last part
    if (viewer.viewMode === 'character' && state.figure) renderFigure();
  });
  $('#btn-shot').addEventListener('click', saveShot);
  $('#view-mode').addEventListener('change', e => {
    setViewMode(e.target.value);
    if (e.target.value === 'character') renderFigure();
    else if (state.selection && state.selection.kind === 'appearance') {
      switchToAppearance(state.selection.id);
    }
  });
  $('#btn-focus').addEventListener('click', focusHead);
  $('#chk-lock').addEventListener('change', e => {
    viewer.setLock(e.target.checked);
    $('#lock-label').classList.toggle('on', e.target.checked);
    toast(e.target.checked
      ? 'camera locked — view frozen across assets (R to reset)'
      : 'camera unlocked — angle carries over, distance re-fits to each model');
  });
  $('#btn-help').addEventListener('click', toggleHelp);
  $('#help-close').addEventListener('click', () => $('#help').classList.add('hidden'));
  $('#help').addEventListener('click', e => {
    if (e.target.id === 'help') $('#help').classList.add('hidden');
  });
  $('#frame-slider').addEventListener('input', e => {
    viewer.opts.frame = +e.target.value;
    $('#frame-label').textContent = `${e.target.value} / ${e.target.max}`;
    rerender();
  });

  // The drawer, its table and the install buttons live in swap.js, which the
  // builder loads too -- that page had no staging at all, and porting this
  // would have made a second copy to keep in step.
  Swap.configure({
    api, toast,
    onChange: async () => {
      if (state.texPath) showProvenance(state.texPath);
      try { state.status = await api('/api/status'); } catch (e) { /* ignore */ }
    },
  });
  Swap.initDrawer();
}

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ------------------------------------------------------------------ lists
function facetQuery() {
  const p = new URLSearchParams({
    table: state.table,
    q: $('#app-search').value.trim(),
    limit: '300',
  });
  for (const [axis, set] of Object.entries(state.sel)) {
    if (set.size) p.set(axis, [...set].join(','));
  }
  if (state.selTags.has(UNTAGGED)) p.set('untagged', '1');
  const tags = [...state.selTags].filter(t => t !== UNTAGGED);
  if (tags.length) p.set('tag', tags.join(','));
  if (state.group) p.set('group', 'mesh');
  return p;
}

async function loadAppearances() {
  const list = $('#app-list');
  list.innerHTML = '<div class="mut small" style="padding:10px">loading…</div>';
  let data;
  try {
    data = await api('/api/appearances?' + facetQuery().toString());
  } catch (e) { list.innerHTML = ''; list.appendChild(el('div', 'err', e.message)); return; }
  state.lastQuery = data;
  renderFacets(data);
  list.innerHTML = '';

  const items = [];
  if (data.groups) {
    for (const g of data.groups) {
      const built = groupRow(g, items.length);
      list.appendChild(built.node);
      items.push(built.item);
    }
    $('#app-more').textContent =
      `${data.groups.length} of ${data.groupedTotal} distinct meshes ` +
      `(${data.total} appearances)`;
  } else {
    state.appearances = data.rows;
    for (const r of data.rows) {
      const built = appearanceRow(r, items.length);
      list.appendChild(built.node);
      items.push(built.item);
    }
    $('#app-more').textContent =
      `${data.rows.length} shown of ${data.total} in ${state.table}` +
      (data.total > data.rows.length ? ' — narrow with the filters' : '');
  }
  navSet('appearance', items);
  // keep the current asset selected across a filter change when it survived
  const keep = state.selection && state.selection.kind === 'appearance'
    ? items.findIndex(it => it.id === state.selection.id ||
        (it.variants || []).some(v => v.id === state.selection.id))
    : -1;
  if (keep >= 0) {
    const it = items[keep];
    const v = it.variants ? Math.max(0, it.variants.findIndex(
      x => x.id === state.selection.id)) : 0;
    state.nav.index = keep;
    state.nav.variant = v;
    if (it.el) it.el.classList.add('sel');
  }
  $('#btn-bulktag').textContent = `Tag all ${data.total}…`;
  $('#btn-bulktag').disabled = !data.total;
}

function facetLine(r) {
  if (!r.class) return r.mesh || (r.meshId ? `mesh ${r.meshId} — unresolved` : 'no mesh');
  const bits = [];
  if (r.class && r.class !== 'unknown') bits.push(lbl(r.class));
  if (r.gender && r.gender !== 'other') bits.push(lbl(r.gender));
  if (r.size && r.size !== 'n/a') bits.push(lbl(r.size).toLowerCase());
  if (r.kind && r.kind !== 'armour') bits.push(r.kind);
  if (r.itemName) bits.push('· ' + r.itemName);
  return bits.join(' ') || (r.mesh || '');
}

function appearanceRow(r, index) {
  const row = el('div', 'row-item');
  row.dataset.id = r.id;
  row.dataset.subject = r.subject;
  const img = el('img');
  img.loading = 'lazy';
  if (r.texture) img.src = texUrl(r.texture, { size: 48 });
  const lb = el('div', 'lbl');
  lb.appendChild(el('b', null, r.id));
  lb.appendChild(el('span', null, facetLine(r)));
  if (r.tags && r.tags.length) {
    const t = el('span', 'tagline', '🏷 ' + r.tags.join(', '));
    lb.appendChild(t);
  }
  row.append(img, lb);
  row.addEventListener('click', () => navActivate(index, 0, { immediate: true }));
  return { node: row, item: { id: r.id, mesh: r.mesh, el: row } };
}

/** Grouped-by-mesh row: one line per distinct body mesh, expanding to its
 *  colour variants. Mesh is shared across colourways and texture is per
 *  colour, so this is the smaller, more useful browsing unit. */
function groupRow(g, index) {
  const wrap = el('div');
  const row = el('div', 'row-item grouphead');
  const img = el('img');
  img.loading = 'lazy';
  if (g.texture) img.src = texUrl(g.texture, { size: 48 });
  const lb = el('div', 'lbl');
  lb.appendChild(el('b', null, (g.mesh || '').split('/').pop() || g.mesh));
  lb.appendChild(el('span', null,
    `${g.count} colour variant${g.count === 1 ? '' : 's'} · ${facetLine(g)}`));
  row.append(img, lb);
  const strip = el('div', 'variants hidden');
  g.variants.forEach((v, vi_i) => {
    const vi = el('img');
    vi.loading = 'lazy';
    vi.title = v.id + (v.tags.length ? '\n🏷 ' + v.tags.join(', ') : '');
    if (v.texture) vi.src = texUrl(v.texture, { size: 40 });
    vi.addEventListener('click', ev => {
      ev.stopPropagation();
      navActivate(index, vi_i, { immediate: true });
    });
    strip.appendChild(vi);
  });
  row.addEventListener('click', () => navActivate(index, 0, { immediate: true }));
  wrap.append(row, strip);
  return {
    node: wrap,
    item: {
      mesh: g.mesh, el: row, stripEl: strip, variants: g.variants,
      expand: () => strip.classList.remove('hidden'),
    },
  };
}

// ------------------------------------------------------------------ categories
//
// The taxonomy comes from the server (tools/catalog.py), which derives it from
// appearance-table membership first and directory layout second. Nothing is
// hardcoded here except the presentation.

// ---- "hide animation assets" -------------------------------------------
//
// The reported want: "since animations can't be previewed in the asset viewer
// mode, only in character/model mode, can we have a toggle that hides them".
// They can't -- `/api/mesh` on `c3/0001/000/001.c3` returns `meshes: []`,
// because the file is four MOTI chunks and no geometry. The builder and the
// model view bind a motion over a mesh and play it; this page does not.
//
// Three rules this obeys, in order of how badly they would be missed:
//
//  1. OFF BY DEFAULT. A browser whose job is "every asset the client can see"
//     must not start by hiding a bucket. The user asked for a toggle, which is
//     not the same as asking for the filtered view to be the normal one.
//  2. VISIBLE WHILE ON. The checkbox goes amber and `#motion-note` states the
//     count that is being withheld, every render, not just on the click.
//  3. THE COUNTS FOLLOW THE LIST. Nothing is filtered here: the flag goes to
//     the server, which drops the buckets before it counts anything. A chip
//     reading 2,475 above a list that no longer holds those files would be
//     the readout disagreeing with the instrument.
//
// Persistence matches the page's other view state -- `coviewer.collapsed`
// (panels) and `coviewer.dirsClosed` (folder tree) -- one localStorage key,
// read once at load, written on change.
const HIDE_MOTION_KEY = 'coviewer.hideMotion';
try { state.hideMotion = localStorage.getItem(HIDE_MOTION_KEY) === '1'; }
catch (e) { /* private mode: the toggle still works, just not across loads */ }

function saveHideMotion() {
  try { localStorage.setItem(HIDE_MOTION_KEY, state.hideMotion ? '1' : '0'); }
  catch (e) { /* ignore */ }
}

/** Add the flag to a catalogue request. One place, so the tree and the file
 *  list can never end up filtered differently. */
function motionParam(p) {
  if (state.hideMotion) p.set('hideMotion', '1');
  return p;
}

/** Say what is being withheld, in the list, for as long as it is withheld.
 *
 *  The number is always `/api/categories`' own `motionHidden` -- the whole
 *  catalogue's total, cached in `state.motionHidden` -- and never one computed
 *  here, which could drift from what the server actually did. `null` means
 *  "not answered yet", and then the banner says so rather than printing a
 *  zero it has not earned. The per-category figure is a different number and
 *  belongs on the per-category line (`#cat-more`), not here.
 */
function showMotionNote() {
  const note = $('#motion-note');
  const label = $('#motion-label');
  if (label) label.classList.toggle('on', !!state.hideMotion);
  if (!note) return;
  note.classList.toggle('hidden', !state.hideMotion);
  if (!state.hideMotion) return;
  const n = state.motionHidden;
  note.textContent =
    'Filtered: ' +
    (n === null ? 'animation entries are being hidden'
                : `${n.toLocaleString()} animation entries hidden`) +
    '. Counts shown exclude them. Motion sets preview in the character ' +
    'builder and the model view.';
}

function setHideMotion(on) {
  state.hideMotion = !!on;
  saveHideMotion();
  const box = $('#chk-hide-motion');
  if (box) box.checked = state.hideMotion;
  state.motionHidden = null;          // unknown until the server answers
  showMotionNote();
  // The tree caches its rows in `state.categories`; both views have to be
  // rebuilt from the server or the one you are not looking at keeps the old
  // counts and shows them the next time you open it.
  state.categories = [];
  loadCategories();
  if (state.cat.id) loadCategoryFiles();
}

async function loadCategories() {
  const host = $('#cat-tree');
  host.innerHTML = '<div class="mut small" style="padding:10px">classifying…</div>';
  let data;
  try {
    data = await api('/api/categories?' +
                     motionParam(new URLSearchParams()).toString());
  }
  catch (e) { host.innerHTML = ''; host.appendChild(el('div', 'err', e.message)); return; }
  state.categories = data.categories;
  state.motionHidden = data.motionFilter ? (data.motionHidden || 0) : null;
  showMotionNote();
  host.innerHTML = '';
  for (const c of data.categories) {
    const row = el('div', 'cat-row');
    const head = el('div', 'cat-head');
    head.appendChild(el('b', null, c.label));
    head.appendChild(el('span', 'n', c.count.toLocaleString()));
    row.appendChild(head);
    row.appendChild(el('div', 'blurb', c.blurb));
    const subs = el('div', 'cat-subs');
    for (const s of c.subs) {
      const ch = chip(s.id, s.count, false, ev => {
        ev.stopPropagation();
        openCategory(c.id, s.id);
      });
      subs.appendChild(ch);
    }
    row.appendChild(subs);
    row.addEventListener('click', () => openCategory(c.id, null));
    host.appendChild(row);
  }
  $('#cat-more') && ($('#cat-more').textContent = '');
  // The pager belongs to the category that is open; leaving it on screen
  // after that category closes offers navigation through a list that is no
  // longer there.
  if ($('#cat-pager')) { $('#cat-pager').innerHTML = ''; $('#cat-pager').hidden = true; }
}

function openCategory(id, sub) {
  state.cat = { id, sub, role: null, group: null };
  // Back to "decide from the size of the list". Carrying the choice across
  // categories would collapse Weapons' three groups because Effects' four
  // hundred were collapsed a moment ago.
  state.catGroupsOpen = null;
  $('#cat-tree').classList.add('hidden');
  $('#cat-controls').classList.remove('hidden');
  $('#cat-search').value = '';
  loadCategoryFirstPage();
}

function closeCategory() {
  state.cat = { id: null, sub: null, role: null, group: null };
  state.catFacets = null;
  state.catGroupsOpen = null;
  $('#cat-tree').classList.remove('hidden');
  $('#cat-controls').classList.add('hidden');
  navSet('category', []);
}

/** Rows per page of the category list. Matches what this list already
 *  requested, so the pane looks unchanged until you reach the bottom of it. */
const CAT_PAGE = 300;

/** Prev / Next under the category list.
 *
 *  `data.total` here is the count AFTER the filters and AFTER folding
 *  (`foldedAway` counts skins merged into their mesh), which is what makes it
 *  the right divisor: the pager must count the rows this list will actually
 *  render, not the catalogue-wide figure the banner carries. Getting that
 *  wrong would produce a last page that is empty and a total that never
 *  matches what you can see.
 */
function drawCatPager(pages) {
  const host = $('#cat-pager');
  if (!host) return;
  host.innerHTML = '';
  if (pages <= 1) { host.hidden = true; return; }
  host.hidden = false;
  const mk = (text, to, enabled) => {
    const b = el('button', 'fx-pagebtn', text);
    b.type = 'button';
    b.disabled = !enabled;
    if (enabled) {
      b.addEventListener('click', () => {
        state.catPage = to;
        loadCategoryFiles();
      });
    }
    return b;
  };
  const cur = state.catPage || 0;
  host.appendChild(mk('\u2039 prev', cur - 1, cur > 0));
  host.appendChild(el('span', 'mut small', ` ${cur + 1} / ${pages} `));
  host.appendChild(mk('next \u203a', cur + 1, cur < pages - 1));
}

/** Load page ONE of whatever the category filters now select.
 *
 *  Opening a category, a sub, a role chip, a group chip and the search box all
 *  change WHICH rows exist, so all of them come through here. Only the pager
 *  itself sets `state.catPage` directly -- staying on page 40 of Maps while
 *  switching to Mounts (4 rows) would render an empty pane, and an empty pane
 *  reads as "this category is empty".
 */
function loadCategoryFirstPage() {
  state.catPage = 0;
  return loadCategoryFiles();
}

async function loadCategoryFiles() {
  const list = $('#cat-list');
  list.innerHTML = '<div class="mut small" style="padding:10px">loading…</div>';
  const p = motionParam(
    new URLSearchParams({ category: state.cat.id, limit: String(CAT_PAGE) }));
  // PAGED SERVER-SIDE, for the same reason the file list is: Maps holds
  // 55,849 rows after folding, so pulling the category into the browser to
  // page it there would trade one bad answer for a slow one. `offset` was
  // always accepted here and never sent -- verified before relying on it:
  // offset=55800 returns exactly 49 rows of 55,849.
  p.set('offset', String((state.catPage || 0) * CAT_PAGE));
  if (state.cat.sub) p.set('sub', state.cat.sub);
  if (state.cat.role) p.set('role', state.cat.role);
  if (state.cat.group) p.set('group', state.cat.group);
  const q = $('#cat-search').value.trim();
  if (q) p.set('q', q);
  const since = ($('#cat-since-base') || {}).value;
  if (since) {
    p.set('newSince', since);
    p.set('sinceMode', ($('#cat-since-mode') || {}).value || 'new');
  }
  let data;
  try { data = await api('/api/catfiles?' + p.toString()); }
  catch (e) { list.innerHTML = ''; list.appendChild(el('div', 'err', e.message)); return; }
  paintCatClear();

  // breadcrumbs
  const cb = $('#cat-crumbs');
  cb.innerHTML = '';
  const meta = state.categories.find(c => c.id === state.cat.id);
  const back = el('a', null, '← all categories');
  back.addEventListener('click', closeCategory);
  cb.append(back, document.createTextNode(' · '),
            el('b', null, meta ? meta.label : state.cat.id));
  if (state.cat.sub) cb.append(document.createTextNode(' / ' + state.cat.sub));

  // role + group chips. Kept out of this function so the group disclosure can
  // redraw itself from the payload it already has instead of re-querying.
  state.catFacets = data;
  renderCatFacets();

  list.innerHTML = '';
  const items = [];
  for (const r of data.rows) {
    const idx = items.length;
    const row = unifiedRow(r, idx, `${r.source} · ${r.role} · ${r.path}`);
    row.title = r.why;
    list.appendChild(row);
    items.push({ path: r.path, el: row });
  }
  navSet('category', items);
  showMotionNote();
  // `data.total` is the server's count *after* the filter, so this line is
  // true either way; the hidden entries are named separately rather than
  // quietly subtracted and never mentioned. This figure is the one for THIS
  // category; the banner carries the catalogue-wide one.
  const cFrom = (state.catPage || 0) * CAT_PAGE;
  const cPages = Math.max(1, Math.ceil((data.total || 0) / CAT_PAGE));
  $('#cat-more').textContent =
    `${data.rows.length} shown of ${data.total.toLocaleString()}` +
    (cPages > 1
      ? ` — ${data.rows.length ? cFrom + 1 : 0}\u2013${cFrom + data.rows.length},`
        + ` page ${(state.catPage || 0) + 1} of ${cPages}`
      : '') +
    (data.foldedAway ? ` · ${data.foldedAway.toLocaleString()} skins merged into their mesh` : '') +
    (data.motionFilter
      ? ` · ${(data.motionHidden || 0).toLocaleString()} animation entries in this category hidden by the filter`
      : '') + sinceText(data.newSince);
  drawCatPager(cPages);
  if (data.newSince && data.newSince.state === 'computing') {
    const at = state.cat.id;
    clearTimeout(loadCategoryFiles.sinceT);
    loadCategoryFiles.sinceT = setTimeout(() => {
      if (state.cat.id === at) loadCategoryFiles();
    }, 1500);
  }
}

/** How many filters narrow the file list: kind, group, the path filter and
 *  "Since". ("hide animation assets" is a separate, announced toggle.) */
function catFilterCount() {
  let n = 0;
  if (state.cat.role) n++;
  if (state.cat.group) n++;
  if ($('#cat-search') && $('#cat-search').value.trim()) n++;
  if ($('#cat-since-base') && $('#cat-since-base').value) n++;
  return n;
}

function paintCatClear() {
  const b = $('#cat-clear-filters');
  if (!b) return;
  const n = catFilterCount();
  b.disabled = n === 0;
  b.textContent = n ? `Clear filters (${n})` : 'No filters';
}

function clearCatFilters() {
  state.cat.role = null;
  state.cat.group = null;
  if ($('#cat-search')) $('#cat-search').value = '';
  if ($('#cat-since-base')) $('#cat-since-base').value = '';
  loadCategoryFirstPage();
}

/** "Since <install>": what the file filter did, in the count line -- or that
 *  it is still comparing (list NOT filtered yet), or why it refused. */
function sinceText(ns) {
  if (!ns) return '';
  const what = { new: 'new', changed: 'changed', both: 'new or changed' }[ns.mode] || 'new';
  if (!ns.comparable) return ` · since-filter off: ${ns.why}`;
  if (ns.state === 'computing')
    return ` · comparing with ${ns.label}… ${ns.done} of ${ns.total} (not filtered yet)`;
  if (ns.state === 'failed') return ` · comparison failed: ${ns.error}`;
  const unk = ns.unknown ? `, ${ns.unknown} unreadable not counted` : '';
  return ` · ${ns.shown.toLocaleString()} of ${ns.ofTotal.toLocaleString()} ${what} since ${ns.label}${unk}`;
}

/** Fill the "Since" list from the declared installs, leaving out the one being
 *  browsed (by ROOT), and reload the list when either control changes. */
async function initCatSince() {
  const sel = $('#cat-since-base'), mode = $('#cat-since-mode');
  if (!sel || initCatSince.done) return;
  initCatSince.done = true;
  let d, st;
  try { d = await api('/api/bases'); st = await api('/api/status'); } catch (e) { return; }
  const norm = x => String(x || '').replace(/[\\/]+/g, '/').replace(/\/$/, '').toLowerCase();
  const here = norm(st && st.root);
  for (const b of (d && d.paths) || []) {
    if (b.kind !== 'install' || (here && norm(b.detail) === here)) continue;
    const o = document.createElement('option');
    o.value = b.id;
    o.textContent = b.label || b.id;
    sel.appendChild(o);
  }
  const reload = () => { if (state.cat.id) loadCategoryFirstPage(); };
  sel.addEventListener('change', reload);
  mode.addEventListener('change', () => { if (sel.value) reload(); });
}

/** Above this many groups the chip list starts collapsed.
 *
 *  Measured on 5517 at a 1500px window (the left pane is ~330px, which fits
 *  about four chips a row): weapon/mesh has 2 groups, Weapons 3,
 *  Characters 19, Maps 42, monster/mesh 85, npc/mesh 156 -- 130 of them a
 *  single file -- and effect/mesh 412. Twelve is three rows of chips, and the
 *  distribution is bimodal with nothing between 19 and 42: the small axes that
 *  read as a legend stay open, every axis that reads as a wall starts shut. */
const GROUP_COLLAPSE_OVER = 12;

/** How many group chips this page is willing to draw at once. Unchanged from
 *  the original `slice(0, 60)`; what IS new is that the count line says so
 *  instead of truncating in silence. */
const GROUP_RENDER_CAP = 60;

/** Draw the "Kind of file" and "Group" axes from `state.catFacets`.
 *
 *  Separate from `loadCategoryFiles` because opening and closing the group
 *  disclosure changes nothing the server knows: it redraws from the payload
 *  already in hand rather than asking /api/catfiles the same question again.
 */
function renderCatFacets() {
  const fh = $('#cat-facets');
  if (!fh) return;
  fh.innerHTML = '';
  const data = state.catFacets;
  if (!data) return;

  const roles = Object.entries(data.roles || {}).sort((a, b) => b[1] - a[1]);
  // AN ACTIVE FILTER IS ALWAYS DRAWN (the owner: "filters will get stuck or
  // disappear"). Selecting a kind narrows the rows to that kind, so the counts
  // come back with ONE role -- and `roles.length > 1` then hid the very chip
  // that would untick it. Same for the group axis below.
  if (state.cat.role && !roles.some(([r]) => r === state.cat.role))
    roles.push([state.cat.role, 0]);
  if (roles.length > 1 || state.cat.role) {
    const box = el('div', 'facet-axis');
    box.appendChild(el('div', 'axis-name', 'Kind of file'));
    for (const [r, n] of roles) {
      box.appendChild(chip(r, n, state.cat.role === r, () => {
        state.cat.role = state.cat.role === r ? null : r;
        loadCategoryFirstPage();
      }));
    }
    fh.appendChild(box);
  }

  const groups = data.groups || [];
  if (groups.length <= 1 && !state.cat.group) return;

  // `groups` is the server's top 400; `groupTotal` is how many exist. Fall
  // back to the list length only for a server too old to send the figure --
  // never quietly report the size of a truncation as a total.
  const total = (typeof data.groupTotal === 'number' && data.groupTotal)
    ? data.groupTotal : groups.length;
  const sel = state.cat.group;
  const selRec = sel
    ? (groups.find(g => g.id === sel) || { id: sel, count: 0 })
    : null;
  const open = state.catGroupsOpen === null
    ? total <= GROUP_COLLAPSE_OVER
    : state.catGroupsOpen;

  const mkChip = g => chip(g.id, g.count, state.cat.group === g.id, () => {
    state.cat.group = state.cat.group === g.id ? null : g.id;
    loadCategoryFirstPage();
  });

  const box = el('div', 'facet-axis');
  const head = el('div', 'axis-name');
  head.appendChild(el('span', null,
    state.cat.id === 'map' ? 'Region / map folder' : 'Group'));

  // What the disclosure hides. Collapsed, the selected group is still drawn --
  // a filter you cannot see is a filter you forget is on -- so it does not
  // count as hidden.
  const drawn = open ? Math.min(groups.length, GROUP_RENDER_CAP)
                     : (selRec ? 1 : 0);
  const hidden = Math.max(0, total - drawn);

  const btn = el('button', 'ghost tiny disclose');
  btn.type = 'button';
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  btn.setAttribute('aria-controls', 'cat-groups');
  // Expanded, the count is only on the button when it is the count actually on
  // screen: "hide 412 groups" over a note reading "60 of 412 shown" would be
  // two numbers for one thing. Truncated, the note owns the arithmetic.
  btn.textContent = open
    ? (drawn < total ? '▾ hide groups'
                     : `▾ hide ${total.toLocaleString()} groups`)
    : (hidden === 1 ? '▸ show 1 group'
                    : `▸ show ${hidden.toLocaleString()} groups`);
  btn.title = open
    ? 'Collapse the group filter. Any group you have selected stays visible.'
    : `${total.toLocaleString()} groups in this category` +
      (selRec ? ` — ${hidden.toLocaleString()} hidden, the one you selected is shown`
              : ` — all ${hidden.toLocaleString()} hidden`);
  btn.addEventListener('click', () => {
    state.catGroupsOpen = !open;
    renderCatFacets();
  });
  head.appendChild(btn);
  box.appendChild(head);

  const chips = el('div', 'axis-chips');
  chips.id = 'cat-groups';
  if (open) {
    const shown = groups.slice(0, GROUP_RENDER_CAP);
    // A selection outside the drawn slice would otherwise be invisible even
    // with the list open, which is the same fault as hiding it when closed.
    if (selRec && !shown.some(g => g.id === sel)) shown.unshift(selRec);
    for (const g of shown.slice(0, GROUP_RENDER_CAP)) chips.appendChild(mkChip(g));
    if (hidden > 0) {
      chips.appendChild(el('span', 'mut small axis-note',
        `${drawn.toLocaleString()} of ${total.toLocaleString()} shown` +
        (total >= 400 ? ' (the server sends the 400 largest)' : '')));
    }
  } else if (selRec) {
    chips.appendChild(mkChip(selRec));
  }
  box.appendChild(chips);
  fh.appendChild(box);
}

// ------------------------------------------------------------------ maps
async function loadMaps() {
  const list = $('#map-list');
  list.innerHTML = '<div class="mut small" style="padding:10px">reading 136 maps…</div>';
  let data;
  try { data = await api('/api/maps'); }
  catch (e) { list.innerHTML = ''; list.appendChild(el('div', 'err', e.message)); return; }
  state.maps = data.rows;
  renderMapList();
}

function renderMapList() {
  const list = $('#map-list');
  const q = $('#map-search').value.trim().toLowerCase();
  const rows = state.maps.filter(r => !q || r.name.toLowerCase().includes(q));
  const biggest = Math.max(1, ...state.maps.map(r => r.area));
  list.innerHTML = '';
  const items = [];
  for (const r of rows) {
    const idx = items.length;
    const row = el('div', 'map-row');
    const sz = el('div', 'sz', String(r.width));
    const lb = el('div', 'lbl');
    lb.appendChild(el('b', null, r.name));
    lb.appendChild(el('span', null,
      `${r.width}×${r.height} · ${r.layerCount} layers` +
      (r.documentId ? ` · id ${r.documentId}` : '')));
    const bar = el('div', 'scale-bar');
    bar.style.width = Math.max(3, 100 * r.area / biggest) + '%';
    lb.appendChild(bar);
    row.append(sz, lb);
    row.addEventListener('click', () => navActivate(idx, 0, { immediate: true }));
    list.appendChild(row);
    items.push({ mapName: r.name, el: row });
  }
  navSet('map', items);
  $('#map-more').textContent =
    `${rows.length} of ${state.maps.length} maps · largest first`;
}

async function selectMap(name) {
  const tk = tokenNow();
  state.selection = { kind: 'map', name };
  state.mapName = name;
  clearViewport(`map “${name}” — the world grid itself is not a 3D mesh; ` +
    `its art is listed on the right.`);
  $('#card-mappieces').classList.remove('hidden');
  $('#card-related').classList.add('hidden');
  const body = $('#mappieces-body');
  body.textContent = 'resolving map art…';
  showTagPanel('map:' + name);
  let rec;
  try { rec = await api('/api/map?name=' + encodeURIComponent(name)); }
  catch (e) { body.textContent = 'failed: ' + e.message; return; }
  if (!stillCurrent(tk)) return;
  renderMapPieces(rec);
  await showProvenance(rec.file);
}

/** How many unresolved rows to print per reason before summarising. */
const UNRES_SHOWN = 40;

/** The order the reasons are printed in, and what each one MEANS.
 *
 * Five reasons rather than one bucket, because a reader has to be able to act
 * on the difference and the actions are not the same. "The index does not
 * define this key" means the art cannot even be named and there is nothing to
 * go looking for; "this install ships no such file" names the exact file that
 * is missing. Collapsing them into "missing" throws away the half that says
 * what to do next. The server (`mapindex.UNRESOLVED_REASONS`) is where the
 * labels are decided; this is the order and the heading text. */
const UNRES_ORDER = [
  ['absent', 'Named, not shipped'],
  ['unknown', 'Named a key the index does not define'],
  ['unreadable', 'A container on the way did not parse'],
  ['loose-only', 'Ships only inside an archive'],
  ['unsupported', 'A format this reader does not read'],
];

function renderMapPieces(rec) {
  const b = $('#mappieces-body');
  b.innerHTML = '';
  b.appendChild(kv([
    ['map', rec.name],
    ['size', `${rec.width} × ${rec.height} cells`],
    ['version', rec.version],
    ['map id', rec.documentId],
    ['region', rec.region || '—'],
    ['puzzle', rec.puzzle],
    ['tile set', rec.ani],
    ['layers', `${rec.layersDecoded} of ${rec.layerCount} decoded`],
  ]));

  // ---- the headline: how big this map's art set actually is.
  //
  // Both halves, always, and the unresolved half is stated even when it is
  // zero. A panel that prints the count only when it is non-zero cannot be
  // read as "this map is complete" -- an absent line and a line saying none
  // look identical, and the reader has to know the feature exists to tell
  // them apart.
  const unres = rec.unresolved || [];
  const sceneFrames = rec.sceneFrameCount || 0;
  const total = (rec.resolvedCount || 0) + sceneFrames;
  const sum = el('div', 'artset' + (unres.length ? ' bad' : ''));
  sum.appendChild(el('b', null, `${total.toLocaleString()} assets go with this map`));
  sum.appendChild(el('span', null,
    unres.length
      ? ` · ${unres.length.toLocaleString()} more it names are unresolved`
      : ' · every reference it makes resolves'));
  b.appendChild(sum);

  // ABOVE the art, not below it. The strips run to hundreds of thumbnails --
  // 316 on icecrypt-lev2 -- and a section underneath them is 1,500px past
  // where anyone stops reading. The count in the summary would be the only
  // thing seen, and a count without the list is back to "something is
  // missing and you cannot tell what". The exceptional half goes first; the
  // bulk of the art set is what the reader scrolls FOR.
  renderUnresolved(b, unres);

  const note = el('div', 'small mut');
  note.style.margin = '6px 0 10px';
  note.textContent =
    'The big thing is the world grid; everything below is the smaller artwork ' +
    'placed on it. Click any tile to open it.';
  b.appendChild(note);

  const nUn = g => unres.filter(u => u.group === g).length;

  artGroup(b, 'Ground tiles', rec.tileCount, nUn('ground'), rec.tiles,
           'the .pul names tile indices, the .ani turns them into these files');
  const coverFrames = rec.covers.flatMap(c => c.frames);
  artGroup(b, 'Animated sprites', rec.coverCount, nUn('cover'),
           coverFrames, 'cover layers — props and animations',
           'sprite', 'sprites');
  if (rec.scenes.length) {
    const g = el('div', 'relgroup');
    g.appendChild(el('h3', null,
      groupTitle('Scenery objects', rec.scenes.length, nUn('scene'),
                 'object', 'objects')));
    let n = 'map/Scene/*.scene, each made of sprite parts';
    // The server walks only the first N scenes. Saying so is the same duty as
    // listing an unresolved reference: the alternative is a strip that is
    // short for a reason the reader cannot see.
    if (rec.sceneDetailShown < rec.scenes.length) {
      n += ` — parts walked for the first ${rec.sceneDetailShown} of ` +
           `${rec.scenes.length}`;
    }
    g.appendChild(el('div', 'note', n));
    const frames = (rec.scenePartDetail || [])
      .flatMap(s => s.parts.flatMap(p => p.frames));
    b.appendChild(g);
    strip(b, '', frames, '');
    const names = el('div', 'small mut');
    names.textContent = rec.scenes.slice(0, 12).join(', ');
    b.appendChild(names);
    for (const s of (rec.scenePartDetail || [])) {
      if (!s.error) continue;
      b.appendChild(el('div', 'small warn', `⚠ ${s.scene} — ${s.error}`));
    }
  }
  if (rec.effects.length) {
    const g = el('div', 'relgroup');
    g.appendChild(el('h3', null, `Effects (${rec.effects.length})`));
    g.appendChild(el('div', 'note',
      'keys into 3DEffect.ini — the effect meshes live under Effects'));
    g.appendChild(el('div', 'small', rec.effects.slice(0, 20).join(', ')));
    b.appendChild(g);
  }
  if (rec.sounds.length) {
    const g = el('div', 'relgroup');
    g.appendChild(el('h3', null,
      groupTitle('Sounds', rec.sounds.length, nUn('sound'))));
    g.appendChild(el('div', 'small mut', rec.sounds.slice(0, 8).join(', ')));
    b.appendChild(g);
  }
  if (rec.error) b.appendChild(el('div', 'warn', '⚠ ' + rec.error));
}

/** One group of a map's art: its thumbnails, or — when none of it resolved —
 *  the heading anyway.
 *
 *  `strip` returns silently on an empty list, which is right for a group the
 *  map never used and WRONG for a group the map filled and the install cannot
 *  answer. On 5517's `hq` the .ani defines none of the 1,196 tile keys the
 *  .pul places, so "Ground tiles" disappeared from the panel entirely: a map
 *  with no ground and a map whose ground is all missing rendered identically,
 *  which is the same collapse this whole panel exists to prevent, one level up
 *  from the individual reference. */
function artGroup(host, name, n, missing, paths, note, one, many) {
  const title = groupTitle(name, n, missing, one, many);
  if (paths && paths.length) { strip(host, title, paths, note); return; }
  if (!missing) return;         // the map genuinely does not use this group
  const g = el('div', 'relgroup');
  g.appendChild(el('h3', null, title));
  g.appendChild(el('div', 'note', note));
  g.appendChild(el('div', 'small mut',
    `nothing here resolved — all ${missing.toLocaleString()} are listed under ` +
    'Unresolved above'));
  host.appendChild(g);
}

/** "Ground tiles (230)" — or "Ground tiles (230 · 2 unresolved)". */
function groupTitle(name, n, missing, one = '', many = '') {
  const unit = many ? ` ${n === 1 ? one : many}` : '';
  return `${name} (${n.toLocaleString()}${unit}` +
         (missing ? ` · ${missing.toLocaleString()} unresolved` : '') + ')';
}

/** Every reference the map makes that did not end at a shipped file.
 *
 * Nothing here is a thumbnail, because there is no file to draw — that is the
 * point. Each row names what the map asked for, which group asked for it and
 * what it asked through, so the entry is actionable rather than a tally. */
function renderUnresolved(host, unres) {
  if (!unres.length) return;
  const g = el('div', 'relgroup unres');
  g.appendChild(el('h3', null, `Unresolved (${unres.length.toLocaleString()})`));
  g.appendChild(el('div', 'note',
    'the map names these and this install does not answer them. They are ' +
    'listed rather than dropped: a map that quietly omits them looks smaller ' +
    'than it is.'));
  const rest = new Set(unres.map(u => u.reason));
  for (const [reason, heading] of UNRES_ORDER) {
    const rows = unres.filter(u => u.reason === reason);
    rest.delete(reason);
    if (!rows.length) continue;
    g.appendChild(unresSection(heading, rows));
  }
  // A reason the server grew and this page has not been taught about must
  // still appear. Falling through to nothing is how a new state becomes an
  // invisible one.
  for (const reason of rest) {
    g.appendChild(unresSection(reason, unres.filter(u => u.reason === reason)));
  }
  host.appendChild(g);
}

function unresSection(heading, rows) {
  const sec = el('div', 'unres-sec');
  sec.appendChild(el('h4', null, `${heading} — ${rows.length.toLocaleString()}`));
  if (rows[0] && rows[0].why) sec.appendChild(el('div', 'note', rows[0].why));
  for (const u of rows.slice(0, UNRES_SHOWN)) {
    const r = el('div', 'unres-row');
    r.appendChild(el('span', 'g', u.group));
    r.appendChild(el('span', 'r', u.ref));
    if (u.via) r.appendChild(el('span', 'v', `via ${u.via}`));
    sec.appendChild(r);
  }
  if (rows.length > UNRES_SHOWN) {
    sec.appendChild(el('div', 'small mut',
      `… ${(rows.length - UNRES_SHOWN).toLocaleString()} more`));
  }
  return sec;
}

/** A thumbnail strip of asset paths, clickable through to the asset. */
function strip(host, title, paths, note, limit = 96) {
  if (!paths || !paths.length) return;
  const g = el('div', 'relgroup');
  if (title) g.appendChild(el('h3', null, title));
  if (note) g.appendChild(el('div', 'note', note));
  const s = el('div', 'relstrip');
  // Dedupe FIRST, then cap. It used to cap the raw list and dedupe inside the
  // loop, so the "… N more" line counted duplicates it was never going to
  // draw -- a map whose 26 cover layers share 21 frames was told five files
  // had been held back that did not exist. The line is a claim about what is
  // not on screen; it has to be counted on the same set the screen is.
  const uniq = [...new Set(paths)];
  for (const p of uniq.slice(0, limit)) {
    s.appendChild(assetCell(p, p.split('/').pop()));
  }
  g.appendChild(s);
  if (uniq.length > limit) {
    // The cap is a page-weight limit -- one thumbnail request per cell, and a
    // map can name 500 -- so it stays as the default. But the ask this panel
    // answers is "see a map's full art set in one place", and a limit with no
    // way past it answers a smaller question. Stated, and openable.
    const more = el('div', 'small mut');
    more.appendChild(document.createTextNode(
      `showing ${limit} of ${uniq.length.toLocaleString()} — `));
    const a = el('a', 'showall',
      `draw the other ${(uniq.length - limit).toLocaleString()}`);
    a.href = '#';
    a.addEventListener('click', e => {
      e.preventDefault();
      for (const p of uniq.slice(limit)) {
        s.appendChild(assetCell(p, p.split('/').pop()));
      }
      more.textContent = `all ${uniq.length.toLocaleString()} drawn`;
    });
    more.appendChild(a);
    g.appendChild(more);
  }
  host.appendChild(g);
}

function assetCell(path, label, { appearance = null } = {}) {
  const isTex = /\.(dds|png|jpe?g|bmp)$/i.test(path);
  const cell = el('div', 'cell' + (isTex ? '' : ' meshcell'));
  const img = el('img');
  img.loading = 'lazy';
  img.alt = path;
  img.src = thumbUrl(path, { size: 48 });
  img.addEventListener('error', () => img.removeAttribute('src'));
  cell.appendChild(img);
  cell.appendChild(el('div', 'cap', label || path.split('/').pop()));
  cell.title = path;
  cell.addEventListener('click', () => {
    if (appearance) { switchToAppearance(appearance); return; }
    requestLoad(() => selectFile(path), { immediate: true });
  });
  return cell;
}

async function switchToAppearance(id, table) {
  if (table && table !== state.table && $('#table-select')
      && [...$('#table-select').options].some(o => o.value === table)) {
    state.table = table;
    $('#table-select').value = table;
  }
  requestLoad(() => selectAppearance(id), { immediate: true });
}

// ------------------------------------------------------------------ related
async function showRelated({ id = '', table = '', path = '' } = {}) {
  const tk = tokenNow();
  const card = $('#card-related');
  const b = $('#related-body');
  b.textContent = 'looking…';
  card.classList.remove('hidden');
  const p = new URLSearchParams();
  if (id) p.set('id', id);
  if (table) p.set('table', table);
  if (path) p.set('path', path);
  let data;
  try { data = await api('/api/related?' + p.toString()); }
  catch (e) { b.textContent = 'failed: ' + e.message; return; }
  if (!stillCurrent(tk)) return;
  b.innerHTML = '';
  if (!data.groups.length) {
    b.appendChild(el('div', 'mut small', 'nothing else resolves for this asset'));
    return;
  }
  for (const g of data.groups) {
    const box = el('div', 'relgroup');
    box.appendChild(el('h3', null, g.title));
    if (g.note) box.appendChild(el('div', 'note', g.note));
    if (g.noThumbs) {
      // Motion tracks and effect scenes have nothing to picture. Listing
      // them by name and kind is the honest presentation -- a placeholder
      // tile would imply a render that does not exist.
      const ul = el('ul', 'components');
      for (const it of g.items) {
        const li = el('li');
        const txt = el('div', 'ctext');
        txt.appendChild(el('b', null, it.label));
        const meta = el('span', 'flags');
        meta.appendChild(el('span', 'role', it.kind));
        if (it.note) meta.appendChild(el('span', 'mut', it.note));
        if (it.source) meta.appendChild(el('span', 'mut', it.source));
        txt.appendChild(meta);
        if (it.detail) txt.appendChild(el('div', 'note', it.detail));
        li.appendChild(txt);
        li.title = it.path;
        li.style.cursor = 'pointer';
        li.addEventListener('click', () => selectFile(it.path));
        ul.appendChild(li);
      }
      box.appendChild(ul);
      b.appendChild(box);
      continue;
    }
    if (g.components) {
      // The entry taken apart again. The left-hand list merges a mesh and its
      // skins into one row; here each half is its own line you can open. The
      // authored / inferred label is meshtex.py's and is the difference between
      // "armor.ini says so" and "there is a .dds with the same stem" -- not the
      // same claim, so not shown the same way.
      const ul = el('ul', 'components');
      for (const it of g.items) {
        const li = el('li', it.primary ? 'primary' : '');
        const im = el('img');
        im.loading = 'lazy';
        im.src = thumbUrl(it.path, { size: 40 });
        im.addEventListener('error', () => im.removeAttribute('src'));
        const txt = el('div', 'ctext');
        txt.appendChild(el('b', null, it.label));
        const meta = el('span', 'flags');
        meta.appendChild(el('span', 'role', it.role));
        if (it.role === 'texture' && it.primarySkin === false) {
          const s = el('span', 'badge arc', 'alt skin');
          s.title = 'Not this mesh’s default texture: the same geometry ' +
                    'is shipped with several skins and this is one of the others.';
          meta.appendChild(s);
        }
        if (it.thumbNote) {
          const n = el('span', 'badge stage', 'thumb');
          n.title = it.thumbNote;
          meta.appendChild(n);
        }
        if (it.kind) {
          const k = el('span', 'badge ' + (it.kind === 'authored' ? 'loose' : 'stage'),
                       it.kind);
          k.title = it.kind === 'authored'
            ? 'A shipped data file states this pairing: ' + it.method
            : 'A naming convention measured off the corpus, not a declaration: '
              + it.method;
          meta.appendChild(k);
        }
        if (it.note) meta.appendChild(document.createTextNode(' ' + it.note));
        txt.appendChild(meta);
        if (it.detail) txt.appendChild(el('span', 'lab', it.detail));
        li.append(im, txt);
        li.title = 'open ' + it.path;
        li.addEventListener('click', () =>
          requestLoad(() => selectFile(it.path), { immediate: true }));
        ul.appendChild(li);
      }
      box.appendChild(ul);
      b.appendChild(box);
      continue;
    }
    if (g.pending) {
      box.appendChild(el('div', 'pending',
        'Weapon → effect linkage is still being built (tools/effects.py). ' +
        'This panel will fill in without any change here once it lands.'));
    } else {
      const s = el('div', 'relstrip');
      for (const it of g.items) {
        s.appendChild(assetCell(it.path, it.label, { appearance: it.appearance }));
      }
      box.appendChild(s);
    }
    b.appendChild(box);
  }
}

// ------------------------------------------------------------------ effects
//
// docs/effects.md is the spec; fx.js is the playback. This is the UI over it.
//
// A weapon has THREE separate effects and the distinction is the whole point:
//   * aura         -- always on, the 999 wildcard action
//   * attack trail -- per attack action; ONLY quality 6-9 weapons have one, so
//                     "no trail" is the data, not a missing asset
//   * impact spark -- drawn at the TARGET, never on the character. The viewer
//                     anchors it to a separate dummy in front of the figure and
//                     labels it, rather than pretending it hangs off the sword.

const fx = {
  playing: false,
  t0: 0,
  pausedAt: 0,
  raf: null,
  loaded: [],        // [{role, name, effect}]
  duration: 1000,
  swing: false,
  anchors: {},       // role -> mat4 (base, before the swing preview)
};
// exposed alongside `state` and `viewer` so playback can be driven from the
// console or a headless render check
window.fx = fx;

function fxDurationOf(def) {
  const frames = def.effectiveFrames || def.frames || 1;
  const cycle = frames * (def.frameIntervalMs || 33) + (def.loopIntervalMs || 0);
  if (def.endless) return Math.max(400, cycle);
  return (def.delayMs || 0) + cycle * Math.max(1, def.loopTime || 1);
}

/** Where each effect rides. See docs/effects.md §8 "Anchoring" -- INFERRED. */
function fxAnchorFor(role) {
  const a = state.anchors || {};
  const socket = a.v_r_weapon || a.v_l_weapon;
  const base = FX.IDENT.slice();
  if (role === 'impact' || role === 'block') {
    // NOT on the character: an impact effect is drawn on whatever was hit.
    // A dummy target one body-width in front of the figure makes that visible
    // instead of quietly wrong.
    const b = (state.figure && state.figure.bodyBounds) || null;
    const h = b ? (b.max[2] - b.min[2]) : 170;
    return FX.translation(0, -h * 0.55, h * 0.5);
  }
  if (socket && socket.matrix) return Array.from(socket.matrix);
  if (socket && socket.pos) return FX.translation(socket.pos[0], socket.pos[1], socket.pos[2]);
  return base;
}

/** The swing preview: sweep the parent through an arc so a SHAP ribbon has
 *  something to smear along. Explicitly OURS -- 3dmotion.ini is not wired in,
 *  so this is not the game's attack animation and the tooltip says so. */
function fxSwingMatrix(base, tNorm) {
  if (!fx.swing) return base;
  const ang = (-0.9 + 2.2 * tNorm);
  const c = Math.cos(ang), s = Math.sin(ang);
  const rot = [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];  // yaw, Z-up
  return FX.mul(rot, base);
}

/** One sweep, in ms. Deliberately NOT the effect's own length: the ribbon only
 *  holds `maxPairs / subdiv` ticks of history (~7 at 60 fps for a 7-segment
 *  trail), so sweeping over an 8-second aura would smear across 0.06 rad and
 *  look like nothing happened. A real attack is ~12 frames at 41 ms. */
const FX_SWING_MS = 600;

function fxParentFor(inst) {
  const base = fx.anchors[inst.role] || FX.IDENT.slice();
  return fxSwingMatrix(base, (viewer.fxTime % FX_SWING_MS) / FX_SWING_MS);
}

async function loadEffects(list) {
  if (!viewer) return 0;
  fxStop();
  fx.loaded = list || [];
  fx.anchors = {};
  const defs = [];
  for (const rec of fx.loaded) {
    if (!rec.effect) continue;
    const keys = {};
    for (const lay of rec.effect.layers || []) {
      if (lay.texture) keys[lay.index] = `fx:${rec.role}:${lay.index}`;
    }
    fx.anchors[rec.role] = fxAnchorFor(rec.role);
    defs.push({ def: rec.effect, role: rec.role,
                anchor: fx.anchors[rec.role], textureKeys: keys });
  }
  const n = viewer.setEffects(defs);
  // textures after setEffects: setMeshes/clear() empties the texture map
  for (const rec of fx.loaded) {
    for (const lay of (rec.effect && rec.effect.layers) || []) {
      if (lay.texture) await applyNamedTexture(`fx:${rec.role}:${lay.index}`, lay.texture);
    }
  }
  fx.duration = Math.max(400, ...fx.loaded
    .filter(r => r.effect).map(r => fxDurationOf(r.effect)));
  $('#fx-wrap').classList.toggle('hidden', n === 0);
  $('#fx-slider').max = String(Math.round(fx.duration));
  fxSeek(0);
  if (n) fxPlay();
  return n;
}

function fxPlay() {
  if (!viewer || !viewer.fx.length) return;
  fx.playing = true;
  fx.t0 = performance.now() - fx.pausedAt;
  $('#btn-fx-play').textContent = '❚❚ pause';
  const step = () => {
    if (!fx.playing) return;
    let t = performance.now() - fx.t0;
    if (t > fx.duration) { t = 0; fx.t0 = performance.now(); viewer.resetEffects(); }
    fxSeek(t, true);
    fx.raf = requestAnimationFrame(step);
  };
  cancelAnimationFrame(fx.raf);
  fx.raf = requestAnimationFrame(step);
}

function fxPause() {
  fx.playing = false;
  fx.pausedAt = viewer ? viewer.fxTime : 0;
  cancelAnimationFrame(fx.raf);
  $('#btn-fx-play').textContent = '▶ play';
}

function fxStop() {
  fxPause();
  fx.pausedAt = 0;
  fx.loaded = [];
  if (viewer) { viewer.clearEffects(); viewer.draw(); }
  $('#fx-wrap').classList.add('hidden');
}

function fxSeek(ms, fromRaf) {
  if (!viewer) return;
  const alive = viewer.setEffectTime(ms, fxParentFor);
  // `setEffectTime` ticks and no longer draws, so the one draw the scrub
  // needs happens HERE -- once, at the target. The replay loop above it
  // (`#fx-slider` input) advances from zero to rebuild path-dependent
  // ribbons and used to pay a full scene draw on every 16.7 ms step:
  // ~180 draws for a 3 s scrub, on an input event.
  viewer.draw();
  if (!fromRaf) fx.pausedAt = ms;
  $('#fx-slider').value = String(Math.round(ms));
  const names = fx.loaded.filter(r => r.effect).map(r => r.name).join(' + ');
  $('#fx-label').textContent =
    `${Math.round(ms)} / ${Math.round(fx.duration)} ms · ${names}` +
    (alive ? '' : ' · finished');
}

/** The Effects card: names, roles, resolved meshes, and a Play button each. */
async function showEffects({ id = '', table = '' } = {}) {
  const card = $('#card-effects');
  const b = $('#effects-body');
  if (!id || !['l_weapon', 'r_weapon', 'shield'].includes(table)) {
    card.classList.add('hidden');
    return;
  }
  const tk = tokenNow();
  b.textContent = 'resolving…';
  card.classList.remove('hidden');
  let data;
  try { data = await api('/api/effects?id=' + encodeURIComponent(id)); }
  catch (e) { b.textContent = 'failed: ' + e.message; return; }
  if (!stillCurrent(tk)) return;
  b.innerHTML = '';
  if (!data.available) {
    b.appendChild(el('div', 'pending', data.error ||
      'tools/effects.py is not importable, so effect names cannot be resolved.'));
    return;
  }
  b.appendChild(kv([
    ['weapon type', `${data.type}${data.typeName ? ' — ' + data.typeName : ''}`],
    ['effects', data.roles.length],
  ]));
  if (!data.hasTrail) {
    b.appendChild(el('div', 'note', data.trailNote));
  }
  const playable = data.roles.filter(r => r.found && r.effect &&
                                     r.effect.playableParts);
  if (playable.length) {
    const all = el('button', 'ghost tiny', `▶ play all ${playable.length}`);
    all.addEventListener('click', () => loadEffects(playable));
    b.appendChild(all);
  }
  for (const r of data.roles) {
    const box = el('div', 'relgroup');
    box.appendChild(el('h3', null, `${r.role} — ${r.name}`));
    box.appendChild(el('div', 'note', r.note));
    if (!r.found) {
      box.appendChild(el('div', 'warn', '⚠ ' + r.error));
      b.appendChild(box);
      continue;
    }
    const d = r.effect;
    box.appendChild(kv([
      ['timing', `${d.frameIntervalMs} ms/frame (${d.fps} fps)`],
      ['length', `${d.effectiveFrames} of ${d.frames} declared frames` +
                 (d.durationMs ? ` = ${d.durationMs} ms` : '')],
      ['loop', d.endless ? 'endless' : `${d.loopTime}x`],
      ['layers', `${d.layers.length}`],
      ['parts', `${d.playableParts} drawable` +
                (d.particleParts ? `, ${d.particleParts} particle (not decoded)` : '')],
    ]));
    if (d.effectiveFrames < d.frames) {
      box.appendChild(el('div', 'note',
        `Plays ${d.effectiveFrames} frames, not the declared ${d.frames}: the ` +
        `alpha envelope is what says when a burst is over (docs/effects.md ` +
        `§6.5, INFERRED). The declared length would run this for ` +
        `${Math.round(d.frames * d.frameIntervalMs)} ms.`));
    }
    const s = el('div', 'relstrip');
    for (const lay of d.layers) {
      if (lay.texture) s.appendChild(assetCell(lay.texture,
        `L${lay.index} ${lay.srcBlendName}/${lay.dstBlendName}`));
      if (lay.mesh) s.appendChild(assetCell(lay.mesh, `L${lay.index} mesh`));
    }
    box.appendChild(s);
    if (d.playableParts) {
      const btn = el('button', 'ghost tiny', '▶ play this one');
      btn.addEventListener('click', () => loadEffects([r]));
      box.appendChild(btn);
    } else {
      box.appendChild(el('div', 'pending',
        'Every layer of this effect is a PTCL/PTC3 particle system, which is ' +
        'not decoded (docs/effects.md §6.6). Nothing is drawn rather than ' +
        'something wrong.'));
    }
    b.appendChild(box);
  }
}

/** The weapon's per-action mesh swap (ini/WeaponMotion.ini, docs/effects.md
 *  §4.5). A weapon does not deform -- the client swaps the mesh -- so a swing
 *  trail animating against a static weapon reads wrong. */
async function showWeaponMotion({ id = '', table = '' } = {}) {
  if (!id || !['l_weapon', 'r_weapon', 'shield'].includes(table)) return null;
  let wm;
  try { wm = await api('/api/weaponmotion?id=' + encodeURIComponent(id)); }
  catch (e) { return null; }
  const b = $('#effects-body');
  const box = el('div', 'relgroup');
  box.appendChild(el('h3', null, 'Per-action mesh'));
  box.appendChild(el('div', 'note', wm.note));
  const rows = [['default', wm.default || '(weapon.ini Mesh0)'],
                ['source', wm.defaultSource]];
  if (Object.keys(wm.actions).length) {
    const byMesh = {};
    for (const [act, v] of Object.entries(wm.actions))
      (byMesh[v.mesh] = byMesh[v.mesh] || []).push(act);
    for (const [mesh, acts] of Object.entries(byMesh))
      rows.push(['actions ' + acts.sort().join(','), mesh]);
  } else {
    rows.push(['actions', 'none — this appearance has no WeaponMotion rows, ' +
                          'so weapon.ini Mesh0 stands for every action']);
  }
  box.appendChild(kv(rows));
  if (Object.keys(wm.actions).length) {
    const sel = el('select');
    for (const act of ['999', ...Object.keys(wm.actions).filter(a => a !== '999').sort()]) {
      const o = el('option', null, act === '999' ? '999 (default)' : 'action ' + act);
      o.value = act;
      sel.appendChild(o);
    }
    sel.addEventListener('change', async () => {
      const r = await api(`/api/weaponmotion?id=${encodeURIComponent(id)}` +
                          `&action=${encodeURIComponent(sel.value)}`);
      if (r.scene && r.chosen) {
        state.meshPath = r.chosen;
        await loadMesh(r.chosen, r.texture || state.texPath);
        toast(`action ${sel.value}: ${r.chosen}`);
      }
    });
    const w = el('div', 'small');
    w.appendChild(el('span', 'mut', 'show mesh for '));
    w.appendChild(sel);
    box.appendChild(w);
  }
  b.appendChild(box);
  return wm;
}

// ------------------------------------------------------------------ equip
//
// The character is the subject; everything else hangs off a named socket.
// Slots come from ini/RolePart.ini via /api/parts, so the list is whatever the
// client actually composes rather than a guess. Hair and headgear share one
// slot because that is what the game does -- a helmet replaces the hair.

async function loadPartManifest() {
  try {
    state.partManifest = await api('/api/parts');
  } catch (e) { state.partManifest = null; }
  renderLoadout();
}

function equippedSlots() {
  const m = state.partManifest;
  if (!m) return [];
  return m.slots.filter(s => s.available || state.loadout[s.name]);
}

function renderLoadout() {
  const b = $('#loadout-body');
  b.innerHTML = '';
  renderModelViewerLink();
  const m = state.partManifest;
  $('#mode-pill').textContent =
    (viewer && viewer.viewMode === 'character') ? 'character view' : 'asset view';
  $('#mode-pill').className = 'badge ' +
    ((viewer && viewer.viewMode === 'character') ? 'loose' : 'arc');

  if (!m) {
    b.appendChild(el('div', 'mut small', 'part manifest unavailable'));
    return;
  }
  const note = el('div', 'small mut');
  note.textContent = `${m.socketCount} attachment points from RolePart.ini. ` +
    'Pick a body, then equip parts — the figure re-renders as you go.';
  b.appendChild(note);

  const row = el('div', 'slots');
  row.style.cssText = 'margin-top:8px;flex-wrap:wrap';
  for (const s of equippedSlots()) {
    const v = state.loadout[s.name];
    const slot = el('div', 'slot' + (v ? ' filled' : ''));
    slot.style.flex = '0 0 30%';
    slot.appendChild(el('div', 'cap', s.label));
    if (v) {
      const img = el('img');
      if (v.texture) img.src = texUrl(v.texture, { size: 54 });
      slot.appendChild(img);
      slot.appendChild(el('div', 'who', v.id + (v.headKind === 'hair'
        ? ' · ' + (v.hairColour || 'hair') : '')));
      const rr = el('div', 'row');
      rr.style.cssText = 'justify-content:center;gap:3px;margin-top:2px';
      const view = el('button', 'ghost tiny', 'view');
      view.title = 'inspect this part on its own, then come back — the loadout is kept';
      view.addEventListener('click', ev => {
        ev.stopPropagation();
        setViewMode('asset');
        switchToAppearance(v.id, v.table);
      });
      const rm = el('button', 'ghost tiny', '✕');
      rm.title = 'unequip';
      rm.addEventListener('click', ev => {
        ev.stopPropagation();
        state.loadout[s.name] = null;
        renderLoadout();
        if (viewer.viewMode === 'character') renderFigure();
      });
      rr.append(view, rm);
      slot.appendChild(rr);
    } else {
      const e2 = el('div', 'empty', s.available ? 'empty' : 'not shipped');
      e2.title = s.note || '';
      slot.appendChild(e2);
    }
    row.appendChild(slot);
  }
  b.appendChild(row);

  const acts = el('div', 'row');
  const goChar = el('button', state.loadout.body ? 'primary' : 'ghost',
                    'Character view');
  goChar.disabled = !state.loadout.body;
  goChar.addEventListener('click', () => { setViewMode('character'); renderFigure(); });
  const share = el('button', 'ghost tiny', 'Copy link');
  share.title = 'a URL that restores this exact loadout';
  share.addEventListener('click', copyLoadoutLink);
  const clr = el('button', 'ghost tiny', 'Clear all');
  clr.addEventListener('click', () => {
    state.loadout = {};
    saveLoadout(); renderLoadout(); clearViewport('loadout cleared');
  });
  acts.append(goChar, share, clr);
  b.appendChild(acts);
  saveLoadout();
}

/** Per install, exactly as the builder keys it (see builder.js
 *  `loadoutKey`): a character saved on one install must not come back on
 *  another, whose ids it does not name. */
function loadoutKey() {
  const norm = x => String(x || '').replace(/[\\/]+/g, '/').replace(/\/$/, '').toLowerCase();
  const root = state.status && state.status.root;
  return root ? `coviewer.loadout@${norm(root)}` : 'coviewer.loadout';
}

function saveLoadout() {
  try { localStorage.setItem(loadoutKey(), JSON.stringify(state.loadout)); }
  catch (e) { /* ignore */ }
}

function restoreLoadout() {
  const fromUrl = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (fromUrl.get('body')) {
    const out = {};
    for (const [k, v] of fromUrl.entries()) out[k] = { id: v, table: k };
    state.loadout = out;
    return true;
  }
  // This install's own save only. The pre-fix shared save may be another
  // install's character; the builder reads it once and CHECKS it, this page
  // does not, so it is not trusted here.
  try {
    const s = JSON.parse(localStorage.getItem(loadoutKey()) || 'null');
    if (s && typeof s === 'object') { state.loadout = s; return !!s.body; }
  } catch (e) { /* ignore */ }
  return false;
}

function loadoutParams() {
  const p = new URLSearchParams();
  for (const [slot, v] of Object.entries(state.loadout)) {
    if (v && v.id) p.set(slot, v.id);
  }
  return p;
}

function copyLoadoutLink() {
  const url = location.origin + location.pathname + '#' + loadoutParams().toString();
  navigator.clipboard.writeText(url).then(
    () => toast('loadout link copied'),
    () => toast('clipboard blocked; the link is in the address bar'));
  location.hash = loadoutParams().toString();
}

/** Which slots may a given appearance table be equipped into? */
function slotsForTable(table) {
  const m = state.partManifest;
  if (!m) return [];
  const direct = m.slots.filter(s => s.name === table);
  if (direct.length) return direct;
  // weapon.ini backs both hands; armor.ini backs body and mix_body
  if (table === 'l_weapon' || table === 'r_weapon') {
    return m.slots.filter(s => s.name === 'l_weapon' || s.name === 'r_weapon');
  }
  if (table === 'mix_body') return m.slots.filter(s => s.name === 'body');
  if (table.startsWith('mix_armet')) {
    return m.slots.filter(s => s.name === table.replace('mix_', ''));
  }
  return [];
}

function loadoutAssignControls(rec, part) {
  const wrap = el('div', 'row');
  for (const s of slotsForTable(rec.table)) {
    const btn = el('button', 'ghost tiny', 'equip → ' + s.label.toLowerCase());
    btn.addEventListener('click', () => {
      // head covering is one slot: equipping a helmet replaces the hair
      for (const grp of (state.partManifest.exclusive || [])) {
        if (grp.includes(s.name)) {
          for (const other of grp) if (other !== s.name) state.loadout[other] = null;
        }
      }
      state.loadout[s.name] = {
        id: rec.id, table: rec.table, mesh: part && part.mesh,
        texture: part && part.texture,
        headKind: rec.headKind, hairColour: rec.hairColour,
      };
      renderLoadout();
      if (s.name === 'body') setViewMode('character');
      if (viewer.viewMode === 'character') renderFigure();
      toast(`${rec.id} → ${s.label}`);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

// ------------------------------------------------------------------ modes
function setViewMode(mode) {
  if (!viewer) return;
  const changed = viewer.viewMode !== mode;
  viewer.setViewMode(mode);
  $('#view-mode').value = viewer.viewMode;
  $('#chk-lock').checked = viewer.opts.lock;
  $('#lock-label').classList.toggle('on', viewer.opts.lock);
  renderLoadout();
  return changed;
}

/** Draw the assembled character: body plus every equipped part at its socket. */
async function renderFigure() {
  if (!state.loadout.body) {
    clearViewport('pick a body to start a character');
    return;
  }
  const tk = tokenNow();
  const p = loadoutParams();
  p.set('bodyTable', state.loadout.body.table || 'body');
  $('#gl-msg').textContent = 'assembling…';
  $('#gl-msg').classList.remove('hidden');
  let fig;
  try { fig = await api('/api/figure?' + p.toString()); }
  catch (e) { clearViewport('assembly failed: ' + e.message); return; }
  if (!stillCurrent(tk)) return;
  state.figure = fig;

  const defs = [];
  for (const m of fig.body.scene.meshes) defs.push({ meta: m, textureKey: 'body' });
  for (const part of fig.parts) {
    for (const m of part.scene.meshes) {
      defs.push({ meta: m, textureKey: 'slot:' + part.slot,
                  translate: part.translate, matrix: part.matrix,
                  slot: part.slot });
    }
  }
  // Frame on the BODY only, and only when the body itself changed: equipping a
  // long weapon must never yank the camera back.
  const bodyChanged = state.lastFiguredBody !== fig.body.id;
  state.lastFiguredBody = fig.body.id;
  viewer.setMeshes(defs, {
    frameOn: fig.bodyBounds,
    keepFraming: !bodyChanged,
  });
  // One /api/texbundle instead of 1 + N serial <img> loads, with the bytes
  // staying DXT from the archive to the GPU. Falls back to the PNG route per
  // texture -- see Viewer.applyTextureBundle in gl.js.
  await viewer.applyTextureBundle(
    [{ key: 'body', path: fig.body.texture }].concat(
      fig.parts.map(p => ({ key: 'slot:' + p.slot, path: p.texture }))),
    { pngUrl: p => texUrl(p, {}), isCurrent: () => stillCurrent(tk) });
  if (!stillCurrent(tk)) return;
  state.anchors = fig.anchors;
  // setMeshes() clears the GL state, effects included; re-attach whatever was
  // playing so equipping a part does not silently kill the aura.
  if (fx.loaded.length) loadEffects(fx.loaded);
  $('#gl-msg').classList.add('hidden');
  $('#gl-stats').textContent = viewer.stats +
    `\ncharacter: ${fig.body.id}` +
    fig.parts.map(p => ` + ${p.slot}:${p.id}`).join('');
  renderFigurePanel(fig);
  viewer.draw();
}

function applyNamedTexture(key, texPath) {
  if (!texPath) return Promise.resolve(false);
  const tk = tokenNow();
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => {
      if (!stillCurrent(tk)) return resolve(false);
      viewer.setTexture(key, img); viewer.draw(); resolve(true);
    };
    img.onerror = () => resolve(false);
    img.src = texUrl(texPath, {});
  });
}

function renderFigurePanel(fig) {
  const b = $('#mesh-body');
  b.innerHTML = '';
  b.appendChild(kv([
    ['character', fig.body.id],
    ['body mesh', fig.body.mesh],
    ['parts', fig.parts.length],
    ['attachment', fig.attachConfidence],
  ]));
  const n = el('div', 'small mut');
  n.style.margin = '6px 0';
  n.textContent = fig.attachNote;
  b.appendChild(n);
  for (const w of fig.warnings || []) b.appendChild(el('div', 'warn', '⚠ ' + w));
  const bodyH = fig.bodyBounds
    ? (fig.bodyBounds.max[2] - fig.bodyBounds.min[2]) : null;
  const ul = el('ul', 'chunks');
  for (const p of fig.parts) {
    const li = el('li');
    li.appendChild(el('b', null, `${p.label}: ${p.id}`));
    li.appendChild(el('div', 'flags',
      `socket ${p.socket} · ${p.anchorSource} [${p.anchorConfidence}]` +
      (p.headKind ? ` · ${p.headKind}` : '') +
      (p.hairColour ? ` · ${p.hairColour}` : '')));
    // The transform chain, stage by stage. Stated numerically on purpose: a
    // regression should read as "scale 4.0 on partMotion", not "looks big".
    if (p.bboxRender && bodyH) {
      const r = p.bboxRender.longest / bodyH;
      li.appendChild(el('div', 'flags' + (r > 1.2 ? ' warn' : ''),
        `authored size ${p.bboxRender.longest.toFixed(1)} = ` +
        `${r.toFixed(2)}x the body's ${bodyH.toFixed(1)} height`));
    }
    for (const s of (p.attachChain && p.attachChain.stages) || []) {
      const d = s.decomposed;
      li.appendChild(el('div', 'flags mut',
        `${s.name}: ${s.applied}` +
        (d ? ` · scale ${d.scale.map(v => v.toFixed(3)).join('/')}` +
             ` · t ${d.translate.map(v => v.toFixed(1)).join(', ')}` : '') +
        ` — ${s.note}`));
    }
    ul.appendChild(li);
  }
  b.appendChild(ul);
  if (fig.parts.length && fig.parts[0].attachChain) {
    b.appendChild(el('div', 'note', fig.parts[0].attachChain.note));
  }
}

// ------------------------------------------------------------------ navigation
//
// One ordered list of "entries" mirroring exactly what is rendered, so arrow
// keys move through the filtered / grouped view rather than the underlying set.
//
//   Up / Down      previous / next entry
//   Left / Right   previous / next colour variant of the current entry's mesh
//   PgUp / PgDn    +/- 10 entries      Home / End   first / last
//
// In grouped mode an entry is a mesh and its variants are the group's colour
// ways. In flat mode an entry is one appearance and its "variants" are the
// other rows sharing the same mesh -- which are 10 apart in the ID ordering,
// so Left/Right is a genuine shortcut there rather than a duplicate of Down.

function navSet(mode, items) {
  state.nav = { mode, items, index: -1, variant: 0 };
}

/** Rows sharing a mesh, as index lists, for Left/Right in flat mode. */
function navSiblingIndices(i) {
  const items = state.nav.items;
  const mesh = items[i] && items[i].mesh;
  if (!mesh) return [i];
  const out = [];
  for (let k = 0; k < items.length; k++) if (items[k].mesh === mesh) out.push(k);
  return out.length ? out : [i];
}

function navActivate(i, variant, { immediate = false } = {}) {
  const items = state.nav.items;
  if (!items.length) return;
  i = Math.max(0, Math.min(items.length - 1, i));
  const it = items[i];
  state.nav.index = i;

  document.querySelectorAll('.row-item.sel, .map-row.sel')
    .forEach(x => x.classList.remove('sel'));
  if (it.el) {
    it.el.classList.add('sel');
    it.el.scrollIntoView({ block: 'nearest' });
  }

  if (it.variants && it.variants.length) {
    const v = Math.max(0, Math.min(it.variants.length - 1, variant || 0));
    state.nav.variant = v;
    if (it.expand) it.expand();
    if (it.stripEl) {
      const imgs = [...it.stripEl.querySelectorAll('img')];
      imgs.forEach((x, k) => x.classList.toggle('sel', k === v));
      if (imgs[v]) imgs[v].scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    requestLoad(() => selectAppearance(it.variants[v].id), { immediate });
  } else if (it.mapName) {
    state.nav.variant = 0;
    requestLoad(() => selectMap(it.mapName), { immediate });
  } else if (it.path) {
    state.nav.variant = 0;
    requestLoad(() => selectFile(it.path), { immediate });
  } else if (it.id) {
    state.nav.variant = 0;
    requestLoad(() => selectAppearance(it.id), { immediate });
  }
}

function navMove(delta) {
  const n = state.nav.items.length;
  if (!n) return;
  const cur = state.nav.index < 0 ? -1 : state.nav.index;
  let next = cur < 0 ? (delta > 0 ? 0 : n - 1) : cur + delta;
  next = Math.max(0, Math.min(n - 1, next));
  if (next === cur) return;
  navActivate(next, 0);
}

function navMoveVariant(delta) {
  const it = state.nav.items[state.nav.index];
  if (!it) { navMove(delta); return; }
  if (it.variants && it.variants.length > 1) {
    const n = it.variants.length;
    const v = (state.nav.variant + delta + n) % n;
    navActivate(state.nav.index, v);
    return;
  }
  // flat mode: hop to the next row sharing this mesh
  const sibs = navSiblingIndices(state.nav.index);
  if (sibs.length < 2) { navMove(delta); return; }
  const at = sibs.indexOf(state.nav.index);
  const nxt = sibs[(at + delta + sibs.length) % sibs.length];
  navActivate(nxt, 0);
}

function navJump(where) {
  const n = state.nav.items.length;
  if (!n) return;
  navActivate(where === 'home' ? 0 : n - 1, 0);
}

const TEXT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
function typingInAField() {
  const a = document.activeElement;
  return !!a && (TEXT_TAGS.has(a.tagName) || a.isContentEditable);
}

function bindKeys() {
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    // Never steal keys from a text field -- the search box has to keep working.
    // Escape is the documented way back out to the list.
    if (typingInAField()) {
      if (e.key === 'Escape') { document.activeElement.blur(); e.preventDefault(); }
      return;
    }

    switch (e.key) {
      case 'ArrowDown': navMove(1); break;
      case 'ArrowUp': navMove(-1); break;
      case 'ArrowRight': navMoveVariant(1); break;
      case 'ArrowLeft': navMoveVariant(-1); break;
      case 'PageDown': navMove(10); break;
      case 'PageUp': navMove(-10); break;
      case 'Home': navJump('home'); break;
      case 'End': navJump('end'); break;
      case '/': $(state.mode === 'files' ? '#file-search' : '#app-search').focus(); break;
      case '?': toggleHelp(); break;
      case ' ':
        if (viewer && viewer.fx.length) { fx.playing ? fxPause() : fxPlay(); }
        else return;
        break;
      case 'Escape': $('#help').classList.add('hidden'); return;
      default:
        switch (e.key.toLowerCase()) {
          case 'l': $('#chk-lock').click(); break;
          case 'r': $('#btn-reset').click(); break;
          case 'v': {
            const next = viewer.viewMode === 'character' ? 'asset' : 'character';
            setViewMode(next);
            if (next === 'character') renderFigure();
            toast(next + ' view');
            break;
          }
          case 'f': focusHead(); break;
          case 'w': $('#chk-wire').click(); break;
          case 'g': $('#chk-grid').click(); break;
          case 's': $('#chk-sockets').click(); break;
          // No label to keep in step now that the button is gone -- and
          // this line used to dereference it unconditionally, so leaving
          // the removal to the HTML alone would have thrown here.
          case 'c': CardPanels.toggleAll(); break;
          default: return;
        }
    }
    e.preventDefault();
  });
}

/** Zoom to the head socket -- attachments are small and the body is large, so
 *  inspecting a helmet otherwise means hunting for it. */
function focusHead() {
  if (!viewer) return;
  const a = state.anchors && (state.anchors.v_armet || state.anchors.v_head);
  if (!a) { toast('no head socket on this asset'); return; }
  viewer.focusOn(a.pos, viewer.radius * 0.16);
  toast('focused on the head socket — R to go back');
}

function toggleHelp() {
  $('#help').classList.toggle('hidden');
}

// ------------------------------------------------------------------ facets
function chip(text, count, on, onClick, cls) {
  const c = el('span', 'chip' + (on ? ' on' : '') + (count === 0 ? ' zero' : '') +
                       (cls ? ' ' + cls : ''));
  c.appendChild(document.createTextNode(text));
  if (count !== null && count !== undefined) c.appendChild(el('span', 'n', String(count)));
  if (onClick) c.addEventListener('click', onClick);
  return c;
}

function renderFacets(data) {
  const host = $('#facets');
  host.innerHTML = '';
  if (!data.isBodyTable) {
    // Non-body tables have no class/gender/size axes; only tags apply.
    renderTagFacet(host, data);
    return;
  }
  for (const axis of ['class', 'gender', 'size']) {
    const counts = data.facets[axis] || {};
    const order = (data.facetOrder[axis] || []).filter(v => v in counts || state.sel[axis].has(v));
    const rest = Object.keys(counts).filter(v => !order.includes(v)).sort();
    // A ticked value the counts no longer name stays drawn (at 0), so it can
    // be unticked -- the same stuck-filter defect as the picker's.
    for (const v of state.sel[axis]) if (!order.includes(v) && !rest.includes(v)) rest.push(v);
    const box = el('div', 'facet-axis');
    box.appendChild(el('div', 'axis-name', AXIS_LABEL[axis]));
    for (const v of [...order, ...rest]) {
      const on = state.sel[axis].has(v);
      const c = chip(lbl(v), counts[v] || 0, on, () => {
        on ? state.sel[axis].delete(v) : state.sel[axis].add(v);
        loadAppearances();
      });
      if (axis === 'size' && data.bodyMetrics) {
        const m = Object.entries(data.bodyMetrics)
          .filter(([k]) => true).map(([k, mm]) => mm);
        c.title = 'Body size is the first 3 digits of the appearance id. ' +
          'Measured heights: 001 female small 168, 002 female large 171, ' +
          '003 male small 176, 004 male large 196.';
      }
      if (axis === 'class') {
        c.title = v === 'any'
          ? 'Wearable by every class — the item series has requiredProfession 0 ' +
            '(basic clothes, event outfits and the modern garments).'
          : v === 'unknown'
            ? 'No item in itemtype.json backs this armour series, so no class ' +
              'can be derived. Shown, never hidden.'
            : `Derived from itemtype.json requiredProfession for this armour series.`;
      }
      box.appendChild(c);
    }
    host.appendChild(box);
  }
  renderTagFacet(host, data);
}

/** The Your-tags axis: a search box over the vocabulary, not a wall of chips.
 *
 *  Typing narrows the chips in place -- no request, no full re-render, so the
 *  caret stays where it is. Two things survive the search on purpose:
 *
 *    - a SELECTED tag is always drawn, even when the query excludes it. A
 *      filter you cannot see is a filter you forget is on, and the box is a
 *      view over the vocabulary, not a second filter on the results.
 *    - `untagged` is a member of the vocabulary and matches like any other
 *      name. It is still the one chip with no count: the server sends a count
 *      per tag and has none for "has no tag", and a made-up number beside it
 *      would be worse than none.
 */
function renderTagFacet(host, data) {
  const counts = data.tagCounts || {};
  const known = { ...state.vocabulary, ...counts };
  const U = UNTAGGED;
  const names = Object.keys(known).sort((a, b) => (counts[b] || 0) - (counts[a] || 0) ||
                                                  a.localeCompare(b));
  //: every selectable name, with the value each one puts in `state.selTags`.
  const all = [...names.map(t => ({ key: t, label: t, count: counts[t] || 0 })),
               { key: U, label: 'untagged', count: null }];

  const box = el('div', 'facet-axis');
  const head = el('div', 'axis-name');
  head.appendChild(el('span', null, 'Your tags'));
  const note = el('span', 'mut tagcount');
  head.appendChild(note);
  box.appendChild(head);

  // Same idiom as the "filter path" inputs in the Categories and Files panes:
  // a bare `input type=search` that narrows what is already on screen.
  const q = el('input');
  q.type = 'search';
  q.id = 'tag-search';
  q.placeholder = 'search tags…';
  q.value = state.tagQuery || '';
  q.setAttribute('aria-controls', 'tag-chips');
  q.title = 'Type to narrow the tag list. Tags you have already selected stay ' +
            'visible whatever you type.';
  box.appendChild(q);

  const chips = el('div', 'axis-chips');
  chips.id = 'tag-chips';
  box.appendChild(chips);

  function paint() {
    const needle = (state.tagQuery || '').trim().toLowerCase();
    chips.innerHTML = '';
    let keptSelected = 0;
    const show = all.filter(t => {
      const hit = !needle || t.label.toLowerCase().includes(needle);
      if (hit) return true;
      if (state.selTags.has(t.key)) { keptSelected++; return true; }
      return false;
    });
    for (const t of show) {
      const on = state.selTags.has(t.key);
      chips.appendChild(chip(t.label, t.count, on, () => {
        on ? state.selTags.delete(t.key) : state.selTags.add(t.key);
        loadAppearances();
      }, 'tag'));
    }
    if (!names.length) {
      chips.appendChild(el('span', 'mut small axis-note',
        'none yet — select an asset and add one in the Tags panel'));
    } else if (show.length === keptSelected) {
      chips.appendChild(el('span', 'mut small axis-note',
        `no tag matches “${(state.tagQuery || '').trim()}”` +
        ` — ${all.length} in the list`));
    }
    // Say what the box is hiding, rather than letting the list quietly shorten.
    note.textContent = needle
      ? `${show.length - keptSelected} of ${all.length}` +
        (keptSelected ? ` · ${keptSelected} selected kept` : '')
      : String(all.length);
  }

  q.addEventListener('input', () => { state.tagQuery = q.value; paint(); });
  // Escape clears the box, the way a search input is expected to behave. Only
  // then is the event swallowed: on an EMPTY box it must still reach the
  // global handler, because blurring back to the list is the documented way
  // out of a text field and this input may not quietly be the exception.
  q.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || !q.value) return;
    ev.stopPropagation();
    ev.preventDefault();
    q.value = '';
    state.tagQuery = '';
    paint();
  });
  q.addEventListener('focus', () => { state.tagQueryFocus = true; });
  q.addEventListener('blur', () => { state.tagQueryFocus = false; });

  paint();
  host.appendChild(box);
  // `renderFacets` rebuilds this whole subtree after every chip click, which
  // would drop the caret mid-search. Put it back where it was.
  if (state.tagQueryFocus) { q.focus(); }
}

function clearFilters() {
  for (const s of Object.values(state.sel)) s.clear();
  state.selTags.clear();
  // The tag box narrows nothing but the chip list, yet this button already
  // empties `#app-search`; leaving one search box full and clearing the other
  // is the inconsistency, not the extra reset.
  state.tagQuery = '';
  $('#app-search').value = '';
  loadAppearances();
}

async function refreshVocabulary() {
  try {
    const v = await api('/api/tags');
    state.vocabulary = v.vocabulary || {};
  } catch (e) { /* non-fatal */ }
}

async function bulkTag() {
  const data = state.lastQuery;
  if (!data || !data.allSubjects || !data.allSubjects.length) return;
  const n = data.allSubjects.length;
  const answer = prompt(
    `Tag all ${n} matching appearance${n === 1 ? '' : 's'}.\n\n` +
    `Space-separated tags to ADD. Prefix a tag with "-" to remove it instead.\n` +
    `Existing tags in use: ${Object.keys(state.vocabulary).join(', ') || '(none)'}`, '');
  if (!answer) return;
  const parts = answer.split(/\s+/).filter(Boolean);
  const add = parts.filter(t => !t.startsWith('-'));
  const del = parts.filter(t => t.startsWith('-')).map(t => t.slice(1)).filter(Boolean);
  try {
    if (add.length) await api('/api/tags', { method: 'POST', body: JSON.stringify(
      { action: 'add', subjects: data.allSubjects, tags: add }) });
    if (del.length) await api('/api/tags', { method: 'POST', body: JSON.stringify(
      { action: 'remove', subjects: data.allSubjects, tags: del }) });
  } catch (e) { return toast('tagging failed: ' + e.message, 4000); }
  toast(`tagged ${n} appearance${n === 1 ? '' : 's'}`);
  await refreshVocabulary();
  await loadAppearances();
  if (state.selection && state.selection.kind === 'appearance') {
    showTagPanel('app:' + state.selection.id);
  }
}

/** Rows per page of the file list. The server caps `limit` at 2000; 300 is
 *  what this list already asked for, kept so the pane looks unchanged until
 *  you reach the bottom of it -- what changes is that there is now a bottom
 *  to reach. */
const FILE_PAGE = 300;

/** Prev / Next under the file list.
 *
 *  Only rendered when the folder has more than one page. A disabled pager on
 *  a four-file folder is furniture, and furniture teaches the eye to skip the
 *  control exactly where it will later matter.
 */
function drawFilePager(pages) {
  const host = $('#file-pager');
  if (!host) return;
  host.innerHTML = '';
  if (pages <= 1) { host.hidden = true; return; }
  host.hidden = false;
  const mk = (text, to, enabled) => {
    const b = el('button', 'fx-pagebtn', text);
    b.type = 'button';
    b.disabled = !enabled;
    if (enabled) {
      b.addEventListener('click', () => { state.filePage = to; loadFiles(); });
    }
    return b;
  };
  const cur = state.filePage || 0;
  host.appendChild(mk('\u2039 prev', cur - 1, cur > 0));
  host.appendChild(el('span', 'mut small', ` ${cur + 1} / ${pages} `));
  host.appendChild(mk('next \u203a', cur + 1, cur < pages - 1));
}

/** Load page ONE of whatever is being listed now.
 *
 *  Everything that changes WHICH files are listed -- a folder click, the
 *  extension dropdown, the search box, the source picker -- goes through here.
 *  Staying on page 7 while switching to a folder with 12 files renders an
 *  empty pane, and an empty pane reads as "this folder is empty": a false
 *  negative manufactured by the pager, which would be a poor trade for the
 *  one it was added to fix.
 */
function loadFilesFirstPage() {
  state.filePage = 0;
  return loadFiles();
}

/** Monotonic guard: clicking through folders issues overlapping requests,
 *  and without this the slower one lands last and shows the wrong folder's
 *  files under the right folder's highlight. */
let fileLoadSeq = 0;

async function loadFiles() {
  const seq = ++fileLoadSeq;
  const list = $('#file-list');
  list.innerHTML = '<div class="mut small" style="padding:10px">loading…</div>';
  const dir = state.dir || '';
  const p = new URLSearchParams({
    dir,
    ext: $('#ext-select').value || '',
    q: $('#file-search').value.trim(),
    limit: String(FILE_PAGE),
    // PAGED, SERVER-SIDE, and server-side is the right half of the choice:
    // the install root holds 71,453 entries, so fetching them all to page in
    // the browser would trade one bad answer for a slow one. `/api/files` has
    // always accepted `offset` (`rows[offset:offset+limit]`); no client ever
    // sent it, so every folder showed its first 300 rows and offered
    // "narrow with the filter" as the only way forward. That is not
    // navigation -- it asks you to already know the name of the thing you are
    // looking for, which is the state the owner reported.
    offset: String((state.filePage || 0) * FILE_PAGE),
  });
  const origin = $('#source-select').value;
  if (origin) p.set('source', origin);
  const data = await api('/api/files?' + p.toString());
  if (seq !== fileLoadSeq) return;      // a newer folder click won
  state.files = data.rows;
  list.innerHTML = '';
  const items = [];
  for (const r of data.rows) {
    const idx = items.length;
    list.appendChild(unifiedRow(r, idx, `${r.source} · ${r.path}`));
    items.push({ path: r.path, el: list.lastChild });
  }
  navSet('files', items);
  const fFrom = (state.filePage || 0) * FILE_PAGE;
  const fPages = Math.max(1, Math.ceil((data.total || 0) / FILE_PAGE));
  $('#file-more').textContent =
    `${data.rows.length} shown of ${data.total}` +
    (fPages > 1
      ? ` — ${data.rows.length ? fFrom + 1 : 0}\u2013${fFrom + data.rows.length},`
        + ` page ${(state.filePage || 0) + 1} of ${fPages}`
      : '') +
    (data.unified ? ' · mesh + skins merged into one entry' : '');
  drawFilePager(fPages);
  $('#file-more').title = data.unifiedNote || '';
}

/** One row of a unified list: the asset, its picture, and -- when a mesh has
 *  absorbed textures -- a count so the merge is visible rather than silent.
 *  Clicking still selects the row; the right-hand panel is where the mesh and
 *  each texture become separately selectable. */
function unifiedRow(r, index, subtitle) {
  const row = el('div', 'row-item');
  const img = el('img');
  img.loading = 'lazy';
  img.src = thumbUrl(r.thumb || r.path, { size: 48 });
  img.addEventListener('error', () => { img.removeAttribute('src'); });
  const lb = el('div', 'lbl');
  const head = el('b', null, r.path.split('/').pop());
  lb.appendChild(head);
  lb.appendChild(el('span', null, subtitle));
  if (r.folded) {
    const f = el('span', 'foldline',
      `+ ${r.folded} skin${r.folded === 1 ? '' : 's'} in this entry`);
    f.title = (r.textures || []).join('\n');
    lb.appendChild(f);
  }
  if (r.tags && r.tags.length) lb.appendChild(el('span', 'tagline', '🏷 ' + r.tags.join(', ')));
  row.append(img, lb);
  row.addEventListener('click', () => navActivate(index, 0, { immediate: true }));
  return row;
}

// ------------------------------------------------------------------ selection
async function selectAppearance(id) {
  const tk = tokenNow();
  state.previewToken = null;
  let res;
  try {
    res = await api(`/api/appearance?id=${encodeURIComponent(id)}` +
                    `&table=${encodeURIComponent(state.table)}`);
  } catch (e) { if (stillCurrent(tk)) showError(e.message); return; }
  if (!stillCurrent(tk)) return;          // the user has already moved on
  const rec = res[0];
  const part = rec.parts.find(p => p.mesh) || rec.parts[0];
  state.selection = { kind: 'appearance', id, table: state.table, rec, part };
  state.meshPath = part ? part.mesh : null;
  state.texPath = part ? part.texture : null;

  if (viewer && viewer.viewMode !== 'asset') setViewMode('asset');
  renderMeshPanelPlaceholder(rec, part);
  $('#mesh-body').appendChild(loadoutAssignControls(rec, part));
  showTagPanel('app:' + id);
  showRelated({ id, table: rec.table });
  showEffects({ id, table: rec.table }).then(() =>
    showWeaponMotion({ id, table: rec.table }));
  $('#card-mappieces').classList.add('hidden');
  await Promise.all([
    state.meshPath ? loadMesh(state.meshPath, state.texPath) : clearViewport(
      part ? `appearance ${id}: mesh ${part.meshId} does not resolve to a shipped file` :
             `appearance ${id} has no parts`),
    state.texPath ? showProvenance(state.texPath) : showProvenance(state.meshPath),
    state.texPath ? showTexturePanel(state.texPath) : clearTexturePanel(),
  ]);
}

async function selectFile(path) {
  const tk = tokenNow();
  state.previewToken = null;
  state.selection = { kind: 'file', path };
  showTagPanel('file:' + path);
  showRelated({ path });
  usedByShow(path);
  $('#card-effects').classList.add('hidden');
  fxStop();
  $('#card-mappieces').classList.add('hidden');
  if (path.endsWith('.c3')) {
    state.meshPath = path; state.texPath = null;
    $('#mesh-body').innerHTML = '';
    // A bare .c3 has no appearance row telling us which skin belongs to it;
    // the server inverts the appearance tables to guess one (see c3tex.py).
    const guessed = await loadMesh(path, null, { guessTexture: true });
    if (!stillCurrent(tk)) return;
    if (guessed) {
      state.texPath = guessed;
      await applyTexture(guessed, null);
      await showTexturePanel(guessed, { guessed: true });
    } else {
      clearTexturePanel('no texture could be inferred for this mesh — the ' +
        'appearance tables do not pair it with one. Drawn untextured (white).');
    }
  } else {
    state.texPath = path;
    await showTexturePanel(path);
    if (!stillCurrent(tk)) return;
    // if the texture is referenced by an appearance, offer to load that mesh
    const pv = await api('/api/provenance?path=' + encodeURIComponent(path));
    if (!stillCurrent(tk)) return;
    const ref = (pv.references || []).find(r => r.kind === 'texture');
    if (ref) {
      const rec = await api(`/api/appearance?id=${encodeURIComponent(ref.appearance)}` +
                            `&table=${encodeURIComponent(ref.table)}`);
      if (!stillCurrent(tk)) return;
      const part = rec[0].parts.find(p => p.texture === path) || rec[0].parts[0];
      if (part && part.mesh) { state.meshPath = part.mesh; await loadMesh(part.mesh, path); }
      else clearViewport('no mesh resolves for this texture');
    } else {
      clearViewport('no appearance in the ini tables references this texture, so ' +
                    'there is no mesh to put it on. The 2D preview is on the right.');
    }
  }
  if (!stillCurrent(tk)) return;
  await showProvenance(path);
}

function clearViewport(msg) {
  if (viewer) { viewer.clear(); viewer.draw(); }
  $('#gl-msg').textContent = msg || '';
  $('#gl-msg').classList.toggle('hidden', !msg);
  $('#gl-stats').textContent = '';
  $('#frame-wrap').classList.add('hidden');
}

// ------------------------------------------------------------------ mesh
async function loadMesh(meshPath, texPath, { guessTexture = false } = {}) {
  const tk = tokenNow();
  $('#gl-msg').textContent = 'loading mesh…';
  $('#gl-msg').classList.remove('hidden');
  let data;
  try {
    data = await api('/api/mesh?path=' + encodeURIComponent(meshPath) +
                     (guessTexture ? '&guesstex=1' : ''));
  } catch (e) {
    if (stillCurrent(tk)) clearViewport('mesh load failed: ' + e.message);
    return null;
  }
  // A mesh that arrived for an abandoned selection must never reach the viewport.
  if (!stillCurrent(tk)) return null;
  state.meshData = data;
  if (guessTexture && data.guessedTexture) texPath = data.guessedTexture;

  // A motion-only container has nothing to draw; the server names the model
  // it animates, so say that instead of leaving the viewport blank.
  if (!data.meshes.length && data.note) {
    clearViewport(data.note);
    return data;
  }

  const defs = data.meshes.map(m => ({ meta: m, textureKey: texPath ? 'main' : null }));
  viewer.setMeshes(defs);

  if (texPath) await applyTexture(texPath, state.previewToken);

  $('#gl-msg').classList.add('hidden');
  $('#gl-stats').textContent = viewer.stats;
  renderModelViewerLink();
  renderCollect();

  // The C3Key alpha track's own last keyframe is the useful range. C3Phy+0x190
  // ("frameCount") is 0/1/2 on meshes whose alpha keys run out to frame 70, so
  // it is not the animation length and must not drive the slider.
  const keyFrames = data.meshes.flatMap(m => m.keys.alphas.map(k => k.frame));
  const maxFrame = Math.max(0, ...keyFrames);
  const hasKeys = keyFrames.length > 1;
  $('#frame-wrap').classList.toggle('hidden', !(hasKeys && maxFrame > 0));
  if (hasKeys && maxFrame > 0) {
    const s = $('#frame-slider');
    s.max = maxFrame; s.value = 0;
    viewer.opts.frame = 0;
    $('#frame-label').textContent = `0 / ${maxFrame}`;
  } else {
    viewer.opts.frame = 0;
  }
  renderMeshPanel(data);
  viewer.draw();
  return texPath;
}

async function applyTexture(texPath, previewToken) {
  const tk = tokenNow();
  // No preview override: take the DXT path, which skips the server's
  // decode+PNG-encode and the browser's PNG decode. With one, stay on the PNG
  // route -- a staged preview is the one case where the user is deliberately
  // looking at bytes that are not the archive's, and it is not hot.
  if (!previewToken) {
    const r = await viewer.applyTextureBundle([{ key: 'main', path: texPath }],
      { pngUrl: p => texUrl(p, {}), isCurrent: () => stillCurrent(tk) });
    if (stillCurrent(tk)) renderModelViewerLink();
    return r.uploaded > 0 || r.fallback.length > 0;
  }
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => {
      // a texture decoded for a selection the user has left must not be applied
      if (!stillCurrent(tk)) return resolve(false);
      viewer.setTexture('main', img);
      viewer.draw();
      renderModelViewerLink();
      resolve(true);
    };
    img.onerror = () => { resolve(false); };
    img.src = texUrl(texPath, { preview: previewToken });
  });
}

function renderMeshPanelPlaceholder(rec, part) {
  const b = $('#mesh-body');
  b.innerHTML = '';
  b.appendChild(kv([
    ['appearance', rec.id],
    ['table', `${rec.table} (${rec.ini})`],
    ['parts', rec.parts.length],
    ['material', part ? part.material : ''],
  ]));
  if (rec.parts.length > 1) {
    const p = el('div', 'small mut',
      'This appearance has several parts. The viewport shows part ' +
      (part ? part.index : 0) + '; the others are listed under Where this comes from.');
    b.appendChild(p);
  }
}

function renderMeshPanel(data) {
  const b = $('#mesh-body');
  const head = kv([
    ['file', data.path],
    ['chunks', `${data.chunkCount} (${data.meshes.length} PHY)`],
  ]);
  b.appendChild(head);

  const ul = el('ul', 'chunks');
  data.meshes.forEach((m, i) => {
    const li = el('li');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = !m.isSocket;
    cb.addEventListener('change', () => {
      const entry = viewer.meshes.find(x => x.meta.index === m.index);
      if (entry) { entry.visible = cb.checked; viewer.draw(); }
    });
    const lab = el('label', 'chk');
    lab.appendChild(cb);
    lab.appendChild(el('span', null,
      `${m.name || '(unnamed)'} — ${m.tag}` + (m.isSocket ? '  [socket]' : '')));
    li.appendChild(lab);
    const flags = [];
    flags.push(`${m.vertexCount} verts (${m.vertexCountA}+${m.vertexCountB})`);
    flags.push(`${m.faceCount} tris`);
    if (m.bones.length) flags.push(`${m.bones.length} bones`);
    if (m.skinned) flags.push('skinned');
    if (m.twoSided) flags.push('2SID two-sided');
    if (m.billboard) flags.push('billboard ' + m.billboard);
    if (!m.matrixIdentity) flags.push('non-identity matrix (applied)');
    if (!m.normalsFromFile) flags.push('normals generated');
    if (m.isC3ExpColor) flags.push('C3EXP_COLOR');
    if (m.keys.alphas.length) flags.push(`${m.keys.alphas.length} alpha keys`);
    if (m.keys.changeTexs.length) flags.push(`${m.keys.changeTexs.length} chgtex keys`);
    li.appendChild(el('div', 'flags', flags.join(' · ')));
    if (m.label) {
      const lb = el('div', 'lab', '3DSMax source: ' + m.label);
      lb.title = 'The PHY chunk label is normally the texture path from the ' +
                 'original 3DSMax export — what this mesh was authored against.';
      li.appendChild(lb);
    }
    ul.appendChild(li);
  });
  b.appendChild(ul);

  if (data.otherChunks.length) {
    const o = el('div', 'small mut');
    o.textContent = 'other chunks: ' +
      data.otherChunks.map(c => `${c.tag}(${c.size}B)`).join(', ');
    b.appendChild(o);
  }
}

// ------------------------------------------------------------------ provenance
async function showProvenance(path) {
  const b = $('#prov-body');
  if (!path) { b.textContent = 'nothing selected'; return; }
  b.textContent = 'loading…';
  let pv;
  try { pv = await api('/api/provenance?path=' + encodeURIComponent(path)); }
  catch (e) { b.textContent = 'error: ' + e.message; return; }

  b.innerHTML = '';
  const badge = el('span', 'badge ' +
    (!pv.exists ? 'none' : pv.source === 'loose' ? 'loose' : 'arc'),
    !pv.exists ? 'NOT FOUND' :
    pv.source === 'loose' ? 'LOOSE FILE WINS' : `FROM ${pv.source.toUpperCase()}`);
  const line = el('div');
  line.appendChild(badge);
  if (pv.staged) {
    line.appendChild(document.createTextNode(' '));
    line.appendChild(el('span', 'badge stage', 'STAGED MOD PENDING'));
  }
  b.appendChild(line);

  const note = el('div', 'small mut');
  note.style.marginTop = '6px';
  note.textContent = pv.source === 'loose'
    ? (pv.overriding
        ? `A loose file on disk is overriding the copy inside ${pv.shadowed_archive}. The game reads the loose one.`
        : 'This asset only exists as a loose file on disk; there is no archive copy.')
    : pv.exists
      ? 'No loose file shadows this, so the game reads it out of the archive. Dropping a file at this path would override it.'
      : 'The client cannot resolve this path at all.';
  b.appendChild(note);

  const rows = [
    ['logical', pv.logical],
    ['resolves to', pv.source || '—'],
    ['on disk', pv.real_path],
    ['size', pv.size ? pv.size.toLocaleString() + ' bytes' : ''],
    ['name hash', pv.name_hash],
  ];
  if (pv.archive_offset >= 0)
    rows.push(['archive slice', `offset ${pv.archive_offset.toLocaleString()}, ` +
                                `${pv.archive_size.toLocaleString()} bytes`]);
  if (pv.overriding)
    rows.push(['shadowing', `${pv.shadowed_archive} copy (${pv.shadowed_size.toLocaleString()} bytes)`]);
  if (pv.dds)
    rows.push(['format', `${pv.dds.width}x${pv.dds.height} ${pv.dds.format}` +
                          ` mips=${pv.dds.mipmaps}${pv.dds.has_alpha ? ' +alpha' : ''}`]);
  if (pv.staged)
    rows.push(['staged file', pv.stagedPath]);
  b.appendChild(kv(rows));

  if (pv.referenceCount) {
    const h = el('div', 'small mut');
    h.style.marginTop = '8px';
    h.textContent = `referenced by ${pv.referenceCount} appearance entr` +
                    (pv.referenceCount === 1 ? 'y' : 'ies') + ':';
    b.appendChild(h);
    const list = el('div', 'small');
    list.style.cssText = 'max-height:96px;overflow:auto;font-family:var(--mono);font-size:11px';
    list.textContent = pv.references
      .map(r => `${r.appearance} (${r.table}/${r.ini}, part ${r.part}, as ${r.kind})`)
      .join('\n');
    b.appendChild(list);
  }

  if (pv.commands && pv.commands.length) {
    const h = el('div', 'small mut');
    h.style.marginTop = '8px';
    h.textContent = 'next actions — click to copy:';
    b.appendChild(h);
    pv.commands.forEach(c => b.appendChild(cmdBlock(c)));
  }
}

// ------------------------------------------------------------------ tag panel
function clearTagPanel(msg) {
  $('#tags-body').innerHTML = '';
  $('#tags-body').appendChild(el('div', 'mut', msg || 'nothing selected'));
}

/** The Tags card for one subject.
 *  Derived tags (green, from bodyfacets.py) are recomputed server-side on every
 *  request and are never written to tags.json, so nothing automatic can ever
 *  overwrite a tag the user typed. */
async function showTagPanel(subject) {
  const b = $('#tags-body');
  b.innerHTML = '';
  let data;
  try { data = await api('/api/tags?subject=' + encodeURIComponent(subject)); }
  catch (e) { b.appendChild(el('div', 'err', e.message)); return; }

  if (data.auto && data.auto.length) {
    const head = el('div', 'small mut', 'derived automatically — not editable, recomputed each time');
    b.appendChild(head);
    const row = el('div', 'tagrow');
    for (const t of data.auto) row.appendChild(chip(t, null, false, null, 'auto'));
    b.appendChild(row);
  }

  const head2 = el('div', 'small mut', 'your tags');
  head2.style.marginTop = '9px';
  b.appendChild(head2);
  const row = el('div', 'tagrow');
  if (!data.tags.length) row.appendChild(el('span', 'mut small', 'none yet'));
  for (const t of data.tags) {
    const c = chip(t, null, true, null, 'tag');
    const x = el('span', 'x', '×');
    x.title = 'remove this tag';
    x.addEventListener('click', async ev => {
      ev.stopPropagation();
      await api('/api/tags', { method: 'POST', body: JSON.stringify(
        { action: 'remove', subjects: [subject], tags: [t] }) });
      await refreshVocabulary();
      await showTagPanel(subject);
      loadAppearances();
    });
    c.appendChild(x);
    row.appendChild(c);
  }
  b.appendChild(row);

  const input = el('input');
  input.type = 'text';
  input.placeholder = 'add tags (space separated), Enter to save';
  input.setAttribute('list', 'tagvocab');
  input.addEventListener('keydown', async e => {
    if (e.key !== 'Enter') return;
    const tags = input.value.split(/\s+/).filter(Boolean);
    if (!tags.length) return;
    input.value = '';
    try {
      await api('/api/tags', { method: 'POST', body: JSON.stringify(
        { action: 'add', subjects: [subject], tags }) });
    } catch (err) { return toast(err.message, 4000); }
    await refreshVocabulary();
    await showTagPanel(subject);
    loadAppearances();
  });
  b.appendChild(input);

  let dl = document.getElementById('tagvocab');
  if (!dl) { dl = el('datalist'); dl.id = 'tagvocab'; document.body.appendChild(dl); }
  dl.innerHTML = '';
  for (const t of Object.keys(state.vocabulary)) {
    const o = el('option'); o.value = t; dl.appendChild(o);
  }

  const note = el('textarea');
  note.placeholder = 'note (free text, saved with the tags)';
  note.value = data.note || '';
  let noteTimer;
  note.addEventListener('input', () => {
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => api('/api/tags', { method: 'POST', body: JSON.stringify(
      { action: 'note', subjects: [subject], note: note.value }) })
      .then(() => toast('note saved', 1200))
      .catch(e => toast(e.message, 3000)), 800);
  });
  b.appendChild(note);

  const foot = el('div', 'small mut');
  foot.style.marginTop = '6px';
  foot.textContent = 'subject: ' + subject;
  b.appendChild(foot);
}

// ------------------------------------------------------------------ texture panel
function clearTexturePanel(msg) {
  $('#tex-body').innerHTML = '';
  $('#tex-body').appendChild(el('div', 'mut', msg || 'nothing selected'));
}

async function showTexturePanel(path, { guessed = false } = {}) {
  const b = $('#tex-body');
  b.innerHTML = '';
  const img = el('img', 'texpreview');
  img.src = texUrl(path, { preview: state.previewToken });
  img.alt = path;
  b.appendChild(img);
  b.appendChild(el('div', 'small mut', path));
  if (guessed) {
    const g = el('div', 'small mut',
      'Inferred from the appearance tables — this mesh is not opened through a ' +
      'specific appearance, and many appearances share one mesh with different ' +
      'skins, so another texture may be the one you want.');
    g.style.marginTop = '4px';
    b.appendChild(g);
  }

  // --- swap controls ---------------------------------------------------
  const row = el('div', 'row');
  const file = el('input');
  file.type = 'file';
  file.accept = '.png,.dds,.bmp,.jpg,.jpeg,.tga,image/*';
  file.style.cssText = 'font-size:11px;max-width:100%';
  row.appendChild(file);
  b.appendChild(row);

  const fmtRow = el('div', 'row');
  const fmt = el('select');
  fmt.style.width = 'auto';
  for (const f of ['(match original)', 'DXT1', 'DXT3', 'DXT5']) {
    const o = el('option', null, f); o.value = (f[0] === '(' ? '' : f); fmt.appendChild(o);
  }
  const fl = el('label', null, 'DDS format');
  fl.appendChild(fmt);
  fmtRow.appendChild(fl);
  b.appendChild(fmtRow);

  const msg = el('div');
  b.appendChild(msg);

  const btnRow = el('div', 'row');
  const bStage = el('button', 'primary', 'Stage this swap');
  const bRevert = el('button', 'ghost', 'Discard preview');
  bStage.disabled = true; bRevert.disabled = true;
  btnRow.append(bStage, bRevert);
  b.appendChild(btnRow);

  file.addEventListener('change', async () => {
    const f = file.files[0];
    if (!f) return;
    msg.innerHTML = '';
    const buf = await f.arrayBuffer();
    let res;
    try {
      res = await api(`/api/preview?path=${encodeURIComponent(path)}` +
                      `&name=${encodeURIComponent(f.name)}` +
                      `&format=${encodeURIComponent(fmt.value)}`,
                      { method: 'POST', body: buf });
    } catch (e) {
      msg.appendChild(el('div', 'err', 'preview failed: ' + e.message));
      return;
    }
    state.previewToken = res.token;
    img.src = texUrl(path, { preview: res.token }) + '&t=' + Date.now();
    if (state.meshPath) await applyTexture(path, res.token);
    msg.appendChild(el('div', 'small',
      `previewing ${f.name} → ${res.info.width}x${res.info.height} ${res.info.format}, ` +
      `${res.bytes.toLocaleString()} bytes. Nothing has been written to the game.`));
    for (const w of res.warnings) msg.appendChild(el('div', 'warn', '⚠ ' + w));
    bStage.disabled = false; bRevert.disabled = false;
  });

  bRevert.addEventListener('click', async () => {
    state.previewToken = null;
    img.src = texUrl(path) + '&t=' + Date.now();
    if (state.meshPath) await applyTexture(path, null);
    msg.innerHTML = '';
    bStage.disabled = true; bRevert.disabled = true;
    file.value = '';
  });

  bStage.addEventListener('click', async () => {
    if (!state.previewToken) return;
    try {
      const r = await api(`/api/stage?path=${encodeURIComponent(path)}` +
                          `&token=${state.previewToken}`, { method: 'POST' });
      toast('staged → ' + r.staged);
      await showProvenance(path);
      openDrawer();
    } catch (e) { msg.appendChild(el('div', 'err', 'stage failed: ' + e.message)); }
  });
}

/** Save the current viewport frame to out/viewer/shots/. The canvas is created
 *  with preserveDrawingBuffer so toDataURL always has a valid frame. */
async function saveShot(name) {
  if (!viewer) return null;
  const base = typeof name === 'string' && name
    ? name
    : (state.meshPath || state.texPath || 'viewport')
        .replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '');
  const url = viewer.snapshot();
  try {
    const r = await api('/api/snapshot?name=' + encodeURIComponent(base),
                        { method: 'POST', body: url });
    toast('saved ' + r.file, 3000);
    return r.file;
  } catch (e) { toast('snapshot failed: ' + e.message, 4000); return null; }
}
window.saveShot = saveShot;

function showError(m) {
  clearViewport(m);
  toast(m, 4000);
}

// ------------------------------------------------------------------ mod drawer
//
// The drawer, the staged-file table and the install buttons are swap.js's,
// shared with the builder. They used to live here, which is why the builder
// -- the page you are actually on when you decide a model should replace
// another -- had no staging at all.
const openDrawer = () => Swap.openDrawer();

boot().catch(e => {
  document.getElementById('statusline').textContent = 'startup failed: ' + e.message;
  console.error(e);
});

// --------------------------------------------- which effect uses this file
//
// THE ROUTE BACK, and it did not exist. The Files tab could show you
// `c3/effect/tj/2.c3` in full -- archive, offset, byte size, motion binding --
// and never once say the word `tj`, which is the name of the only effect that
// plays it. The Effects Viewer indexes by effect NAME, so a file you found by
// browsing was a dead end: you could see the thing and not name it.
//
// NONE OF THIS IS NEW MACHINERY. `depclose.DepGraph.impact()` has carried
// `effect_refs` -- a path-keyed reverse index -- the whole time, `assetroot`
// groups it into `Satellites.effects`, and `/api/assetroot` already returns it
// as JSON. MEASURED on Classic Conquer 2.0: `c3/effect/tj/2.c3` ->
// `EffectRef(effect='tj', layer=1, role='mesh', asset_id='2084')`, graph built
// in 0.8 s. No JS called that route. This is the wire, not the resolver.
//
// THREE THINGS IT MUST NOT DO, all of them ways to look helpful and lie:
//
//  1. AN EMPTY LIST IS NOT "NO EFFECT USES THIS". `impact()` indexes `.c3` and
//     `.dds` and NOTHING else -- it returns `kind: "other"` and says so in its
//     own limits. For a `.ani`, a sound, a `.wdb`, the honest answer is NOT
//     INDEXED, and it is rendered differently from an indexed asset that
//     genuinely has no users. Those two look identical in a bare list and only
//     one of them is a fact about the client.
//  2. IT MUST NOT PAY THE COLD WALK WITHOUT SAYING SO. The forward index is a
//     7-34 s build on a cold install (the same walk `/api/fx/list` and the
//     compare panel warn about). So the first lookup on a page is a BUTTON
//     with the cost stated, exactly as `fx-cmp` does it; once the graph is
//     warm the answer is a dict lookup and later files resolve on selection
//     without asking. `usedBy.warm` is that latch and nothing else sets it.
//  3. A FAILED LOOKUP IS NOT AN EMPTY ONE. Any error prints what failed.

const usedBy = { warm: false, token: 0 };

/** `/effects#kind=3d&name=<effect>` -- the deep link effects.js already reads
 *  (it writes the same shape on selection). Built here rather than by
 *  string-appending a name a caller may not have encoded. */
function effectHref(name) {
  return '/effects#kind=3d&name=' + encodeURIComponent(name);
}

/** Render one `Satellites.effects` row as a link back to the Effects Viewer. */
function usedByRow(it) {
  // The server's label is prose for a person ("tj layer 1 (mesh)"); the NAME
  // is what the link needs, and parsing it back out of the label would make
  // every future rewording a silent dead link. `effect` is carried separately
  // for exactly this reason -- see fxview.js rule 1 on navigation by structure.
  const r = it.rule || {};
  const name = r.effect || '';
  const li = el('li', 'usedby-row');
  if (name) {
    const a = el('a', 'navlink-inline', name);
    a.href = effectHref(name);
    li.appendChild(a);
  } else {
    li.appendChild(el('b', null, it.label || '(unnamed effect)'));
  }
  const bits = [];
  if (r.layer !== undefined && r.layer !== null) bits.push('layer ' + r.layer);
  if (r.role) bits.push(r.role);
  if (it.asset_id) bits.push('id ' + it.asset_id);
  if (bits.length) li.appendChild(el('span', 'mut small', ' — ' + bits.join(' · ')));
  if (it.source) li.appendChild(el('div', 'mut fx-src', '[' + it.source + ']'));
  if (it.form) li.appendChild(el('div', 'note', it.form));
  return li;
}

/** The card. `path` is a logical asset path; anything else is refused loudly. */
async function usedByShow(path) {
  const card = $('#card-usedby');
  const b = $('#usedby-body');
  if (!path) { card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  b.innerHTML = '';

  // Rule 2: on a cold page the walk is the user's to authorise.
  if (!usedBy.warm) {
    b.appendChild(el('div', 'mut small',
      'Finding which effects name this file needs the install-wide dependency ' +
      'index. Building it is a 7–34 s walk on a cold install; after the first ' +
      'build every later file answers immediately.'));
    const btn = el('button', null, 'Find the effects that use this');
    btn.type = 'button';
    btn.addEventListener('click', () => { usedBy.warm = true; usedByShow(path); });
    b.appendChild(btn);
    return;
  }

  const tk = ++usedBy.token;
  b.textContent = 'looking…';
  let d;
  try {
    d = await api('/api/assetroot?asset=' + encodeURIComponent(path));
  } catch (e) {
    // Rule 3.
    b.innerHTML = '';
    b.appendChild(el('div', 'bad',
      'the lookup failed, so this says NOTHING about which effects use the ' +
      'file: ' + e.message));
    return;
  }
  if (tk !== usedBy.token) return;          // a later selection won
  b.innerHTML = '';

  if (d.error) {
    b.appendChild(el('div', 'bad', 'the resolver refused this asset: ' + d.error));
    return;
  }

  // Rule 1: not-indexed and no-users are different answers.
  if (d.kind === 'other' || d.measured === false) {
    b.appendChild(el('div', 'note',
      'NOT INDEXED. The reverse index covers .c3 meshes and .dds textures ' +
      'only, so for this file the question was not asked — this is not the ' +
      'same as "no effect uses it".'));
    for (const l of (d.limits || [])) b.appendChild(el('div', 'mut small', l));
    return;
  }

  const rows = d.effects || [];
  if (!rows.length) {
    b.appendChild(el('div', 'mut small',
      'No effect layer names this file. The index WAS built and this asset ' +
      'is in it, so this is a measured "none" rather than a gap.'));
  } else {
    const ul = el('ul', 'components');
    for (const it of rows) ul.appendChild(usedByRow(it));
    b.appendChild(ul);
    b.appendChild(el('div', 'mut small',
      rows.length === 1 ? '1 effect layer names this file.'
                        : rows.length + ' effect layers name this file.'));
  }
  // The subject's own blind spots travel with the answer -- fxview.js rule 4,
  // a link may not launder a caveat.
  for (const l of (d.limits || [])) b.appendChild(el('div', 'mut small', l));
}
