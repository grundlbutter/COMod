/* mapedit.js -- the MapEditor page: DOM, canvas and network.
 *
 * WHERE THE LINE IS
 * -----------------
 * Every decision about *what* to draw, *where* it lands and *what a click
 * means* is in `mapmodel.js`, which has no DOM in it. This file is the other
 * half: it measures the viewport, moves pixels, issues requests and builds
 * nodes. If you find yourself doing arithmetic here, it belongs next door.
 *
 * TWO CANVASES, AND WHY
 * ---------------------
 * `#glcanvas` draws the art; `#mapcanvas`, on top of it, draws the overlays.
 *
 * The 2D canvas is still the right renderer for the *geometry* of this page.
 * `tools/coplay.py` draws the map on a mesh because it also has a character
 * standing on it, and one renderer for both is what keeps the two in step.
 * This page has no character: it is flat art through the locked camera, and
 * `docs/map_scenery.md` §6 measures that camera as an exact scale-and-
 * translate of the painted image. Selection outlines, pending passability
 * edits and the cell diamonds are all that arithmetic and nothing more.
 *
 * What changed is where the ART comes from, and it is a performance fact
 * rather than a geometric one (`docs/asset_decode_perf.md`). Asking the
 * server for an RGBA PNG per 256-px tile per layer per zoom meant decoding
 * the same `.dds` once per tile that used it -- MEASURED 72 s to open
 * `newplain` at fit zoom. `tilebake.js` uploads the map's whole distinct
 * tile set ONCE, still DXT-compressed, and composites the visible rectangle
 * on the GPU; the projection it applies is the same scale-and-translate,
 * expressed as a rect. So this file still cannot express a projection the
 * game does not use.
 *
 * The PNG tile path below is NOT dead code. It is the fallback for a browser
 * without S3TC and the only path for `passability`, which is drawn from the
 * cell grid and has no texture in it at all.
 *
 * WHAT IT NEVER DOES
 * ------------------
 * It never writes to the game install. Textures go `/api/preview` ->
 * `/api/stage` -> `comod.py`, exactly as a `.dds` swap does on the browser
 * page. A `.DMap` passability edit goes through `/api/mapedit/passability`,
 * which refuses without the acknowledgement string because that file is one
 * of the 143 `integrity.json` lists.
 */

'use strict';

const M = window.MapModel;

const $ = s => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const app = {
  maps: [],            // /api/mapedit/maps rows
  info: null,          // /api/mapedit/map for the open map
  state: null,         // MapModel state
  pick: null,          // last /api/mapedit/pick payload
  editable: null,      // /api/mapedit/editable
  tiles: new Map(),    // key -> {img, ok}
  pending: [],         // staged-but-not-sent passability edits
  paint: false,        // passability edit mode
  previewToken: null,
  previewPath: '',
  dirty: false,
  gl: null,            // WebGL context for the art layer, once created
  baker: null,         // TileGround.Baker holding the map's resident DXT
  bakerMap: '',        // which map the baker currently holds
  bakerState: 'off',   // off | loading | ready | fallback
  bakerWhy: '',        // why it fell back; shown in the HUD
  bakerStats: null,    // { tiles, sprites, bytes, fetchMs, loadMs }
};

// -------------------------------------------------- the resident art layer
//
// `docs/asset_decode_perf.md`. The server used to render one RGBA PNG per
// 256-px tile per layer per zoom level, decoding the same `.dds` again for
// every one of them. This is coplay's model instead (`docs/map_memory.md`):
// fetch the map's whole distinct art set ONCE, still DXT-compressed, upload
// it to the GPU with no decode anywhere, and composite the visible rectangle
// locally every frame. After that one bundle no pixel crosses the wire for
// this map again -- panning, zooming and layer toggles stop being network
// operations entirely.
//
// It degrades rather than fails. No WebGL, no S3TC, or a map with no
// placeable art each set `bakerState = 'fallback'`, and the PNG tile pyramid
// further down runs exactly as it did before.

function glContext() {
  if (app.gl !== null) return app.gl;
  const c = $('#glcanvas');
  // premultipliedAlpha MUST be true. tilebake blends SRC_ALPHA /
  // ONE_MINUS_SRC_ALPHA into a framebuffer that starts fully transparent, so
  // what lands in it is `rgb * a` with `a` in the alpha channel -- which is
  // premultiplied by definition. Declaring otherwise makes the compositor
  // divide nothing out and every partially transparent texel renders dark by
  // exactly its own alpha. MEASURED before this was set: opaque texels
  // matched the server renderer to 0.21/255, and alpha-204 texels came out at
  // exactly 0.8x its value.
  //
  // `alpha: true` is separate and also required: the wrapper's checkerboard
  // has to show through wherever the map paints nothing.
  const opts = { alpha: true, premultipliedAlpha: true,
                 antialias: false, depth: false };
  app.gl = c.getContext('webgl', opts)
        || c.getContext('experimental-webgl', opts) || false;
  return app.gl;
}

async function loadTileset(name) {
  const fallback = why => {
    app.bakerState = 'fallback';
    app.bakerWhy = why;
    app.bakerMap = '';
    schedule();
  };
  app.bakerState = 'loading';
  app.bakerWhy = '';
  app.bakerStats = null;
  if (typeof TileGround === 'undefined')
    return fallback('tilebake.js did not load');
  const gl = glContext();
  if (!gl) return fallback('this browser has no WebGL');
  if (!app.baker) app.baker = new TileGround.Baker(gl);
  if (!app.baker.supported)
    return fallback('no WEBGL_compressed_texture_s3tc, so DXT cannot be ' +
                    'uploaded without decoding it');

  const t0 = performance.now();
  let manifest, buffer;
  try {
    const q = '?name=' + encodeURIComponent(name);
    const [mr, br] = await Promise.all([fetch('/api/mapedit/puzzle' + q),
                                        fetch('/api/mapedit/tileset' + q)]);
    if (!mr.ok) return fallback('no tile manifest: ' + (await mr.text()));
    if (!br.ok) return fallback('no tile bundle: ' + (await br.text()));
    manifest = await mr.json();
    buffer = await br.arrayBuffer();
  } catch (e) {
    return fallback('tileset fetch failed: ' + e.message);
  }
  if (!app.info || app.info.name !== name) return;   // map changed in flight
  const fetchMs = performance.now() - t0;
  try {
    app.baker.load(manifest, buffer);
  } catch (e) {
    return fallback('tile upload failed: ' + e.message);
  }
  app.bakerMap = name;
  app.bakerState = 'ready';
  app.bakerStats = { tiles: app.baker.stats.tiles,
                     sprites: app.baker.stats.sprites || 0,
                     bytes: buffer.byteLength, fetchMs,
                     loadMs: app.baker.stats.loadMs };
  schedule();
}

/** Everything the map's art is cached in, dropped together.
 *
 *  Staging or unstaging a `.dds` changes the art, and there are now TWO
 *  caches of it: the page's PNG tile images and the GPU-resident tile set.
 *  Dropping one and not the other is how a staged texture would appear on
 *  some layers and not others -- so there is one call, and it does both. */
function refreshArt() {
  app.tiles.clear();
  app.bakerState = 'off';
  app.bakerMap = '';
  app.bakerStats = null;
  clearGL();
  if (app.info && app.info.ok) loadTileset(app.info.name);
}

/** True when the GL layer is drawing this map's art -- so the PNG tile path
 *  must not fetch it as well. */
function bakerLive() {
  return app.bakerState === 'ready' && !!app.baker && !!app.info
      && app.bakerMap === app.info.name;
}

/** The art rectangle the viewport is showing, in painted-image pixels. */
function viewRect(state, r) {
  return [state.ox, state.oy,
          state.ox + r.w / state.zoom, state.oy + r.h / state.zoom];
}

function drawGL(state, r) {
  const gl = app.gl;
  const c = $('#glcanvas');
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.w * dpr));
  const h = Math.max(1, Math.round(r.h * dpr));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  gl.viewport(0, 0, w, h);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  return app.baker.drawInto(viewRect(state, r),
                            { layers: state.layers, timeMs: state.t });
}

function clearGL() {
  if (!app.gl) return;
  const c = $('#glcanvas');
  app.gl.viewport(0, 0, c.width, c.height);
  app.gl.clearColor(0, 0, 0, 0);
  app.gl.clear(app.gl.COLOR_BUFFER_BIT);
}

// ------------------------------------------------------------------ network

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
  return r.json();
}

function toast(msg, ms) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms || 2200);
}

// ------------------------------------------------------------------ boot

async function boot() {
  CardPanels.init('comapedit.collapsed', 'btn-collapse-all');
  wire();
  const data = await api('/api/mapedit/maps');
  app.maps = data.rows;
  $('#statusline').textContent =
    `${data.total} GameMap.json rows — ` +
    Object.entries(data.states).map(([k, v]) => `${v} ${k}`).join(', ');
  renderPicker();
  renderLayers();
  const first = location.hash.replace(/^#/, '') ||
                (app.maps.find(r => r.state === 'ok') || {}).name;
  if (first) await openMap(first);
}

function wire() {
  $('#map-search').addEventListener('input', renderPicker);
  $('#btn-zoom-in').addEventListener('click', () => zoom(+1));
  $('#btn-zoom-out').addEventListener('click', () => zoom(-1));
  $('#btn-fit').addEventListener('click', fitMap);
  $('#btn-shot').addEventListener('click', () => savePage());
  $('#btn-mods').addEventListener('click', openDrawer);
  $('#drawer-close').addEventListener('click',
    () => $('#drawer').classList.add('hidden'));
  $('#btn-dry').addEventListener('click', () => runMod('/api/install?dry=1'));
  $('#btn-install').addEventListener('click', () => {
    if (!confirm('This copies mods/stage/ into the game install. ' +
                 'comod.py takes backups and writes a revert manifest. Continue?')) return;
    runMod('/api/install?dry=0');
  });
  $('#btn-uninstall').addEventListener('click', () => runMod('/api/uninstall?dry=0'));

  const c = $('#mapcanvas');
  c.addEventListener('wheel', onWheel, { passive: false });
  c.addEventListener('pointerdown', onDown);
  c.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('resize', onResize);
  window.addEventListener('keydown', onKey);
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(onResize).observe($('#mapwrap'));
  }
}

function onResize() {
  sizeCanvas();
  const r = viewSize();
  if (app.needFit && app.state && r.w > 0 && r.h > 0) {
    app.needFit = false;
    app.state = M.fit(app.state, r.w, r.h);
  }
  schedule();
}

function onKey(e) {
  const tag = (document.activeElement || {}).tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
    if (e.key === 'Escape') document.activeElement.blur();
    return;
  }
  if (e.key === 'f' || e.key === 'F') fitMap();
  else if (e.key === '+' || e.key === '=') zoom(+1);
  else if (e.key === '-' || e.key === '_') zoom(-1);
  else if (e.key === 'p' || e.key === 'P') setLayer('passability', !app.state.layers.passability);
  else if (e.key === 'c' || e.key === 'C') CardPanels.toggleAll();
}

// ------------------------------------------------------------------ picker

function renderPicker() {
  const rows = M.pickerRows(app.maps, $('#map-search').value);
  const host = $('#map-list');
  host.innerHTML = '';
  for (const r of rows) {
    const n = el('div', 'maprow' + (r.drawable ? '' : ' undrawable') +
                        (app.info && app.info.name === r.name ? ' sel' : ''));
    n.title = r.note || `${r.name} — ${r.size} cells`;
    n.appendChild(el('span', 'nm', r.name));
    if (r.state !== 'ok') n.appendChild(el('span', 'badge ' +
      (r.state === 'missing' || r.state === 'broken' ? 'stage' : 'arc'), r.state));
    n.appendChild(el('span', 'sz', r.size));
    n.addEventListener('click', () => openMap(r.name));
    host.appendChild(n);
  }
  $('#map-more').textContent = `${rows.length} of ${app.maps.length} rows`;
}

// ------------------------------------------------------------------ layers

function renderLayers() {
  const host = $('#layers');
  host.innerHTML = '';
  for (const id of M.LAYERS) {
    const on = app.state ? app.state.layers[id] : true;
    const row = el('label', 'lrow' + (on ? ' on' : ''));
    const box = el('input');
    box.type = 'checkbox';
    box.checked = on;
    box.addEventListener('change', () => setLayer(id, box.checked));
    row.appendChild(box);
    row.appendChild(el('span', 'name', LAYER_LABEL[id]));
    row.appendChild(el('span', 'count', layerCount(id)));
    row.title = LAYER_HELP[id];
    host.appendChild(row);
  }
}

const LAYER_LABEL = {
  background: 'Background', ground: 'Base puzzle', terrain: 'TERRAIN',
  cover: 'COVER', passability: 'Passability',
};

const LAYER_HELP = {
  background: 'The map’s background puzzle planes, from the .DMap’s trailing ' +
    'section. They are what turns the void around an island into sea and sky.',
  ground: 'The painted ground: map/puzzle/*.pul cut into PuzzleGridSize tiles.',
  terrain: 'TERRAIN layers — map/Scene objects. These carry their own ' +
    'passability and it REPLACES the cell grid underneath them.',
  cover: 'COVER layers — sprites the game draws in front of the player.',
  passability: 'Green: walkable in the .DMap’s own grid. Cyan: walkable only ' +
    'because a TERRAIN layer says so. Red: the grid says yes and a TERRAIN ' +
    'says no.',
};

function layerCount(id) {
  const i = app.info;
  if (!i) return '';
  if (id === 'terrain') return String(i.sceneParts || 0);
  if (id === 'cover') return String(i.covers || 0);
  if (id === 'background') return String((i.backdrops || []).length);
  if (id === 'ground') return i.pul ? `${i.pul[0]}×${i.pul[1]}` : '—';
  if (id === 'passability') return i.mapSize ? `${i.mapSize[0]}²` : '';
  return '';
}

function setLayer(id, on) {
  if (!app.state) return;
  app.state = Object.assign({}, app.state,
    { layers: Object.assign({}, app.state.layers, { [id]: on }) });
  if (id === 'passability' && !on) setPaint(false);
  renderLayers();
  renderPassCard();
  schedule();
}

// ------------------------------------------------------------------ the map

async function openMap(name) {
  $('#mapmsg').textContent = `loading ${name}…`;
  $('#mapmsg').classList.remove('hidden');
  app.tiles.clear();
  app.pick = null;
  app.pending = [];
  app.bakerState = 'off';
  app.bakerMap = '';
  app.bakerStats = null;
  clearGL();
  setPaint(false);
  let info;
  try {
    info = await api('/api/mapedit/map?name=' + encodeURIComponent(name));
  } catch (e) {
    $('#mapmsg').textContent = 'could not open ' + name + ': ' + e.message;
    return;
  }
  app.info = info;
  location.hash = name;
  const keep = app.state ? app.state.layers : null;
  app.state = M.create(info);
  if (keep) app.state.layers = Object.assign({}, keep);
  sizeCanvas();
  const r = viewSize();
  // A pane that has not been laid out yet reports 0x0, and fitting to that
  // silently leaves the map at the default zoom in the wrong place. Remember
  // that the fit is owed and do it the moment there is a viewport.
  app.needFit = r.w <= 0 || r.h <= 0;
  if (!app.needFit) app.state = M.fit(app.state, r.w, r.h);
  renderPicker();
  renderLayers();
  renderMapCard();
  renderInspector();
  renderTexCard();
  renderPassCard();
  // A new map means a new closure. Dropped rather than re-resolved: the
  // shared-art walk is 13 s, and browsing maps should not pay it once per
  // click. The card offers the button.
  co.closure = null;
  renderCollectCard();
  loadEntries();
  // The one fetch this map's art costs. Not awaited: the grid, the HUD and
  // the inspector are usable immediately, and the art appears when it lands.
  if (info.ok) loadTileset(info.name);
  else { app.bakerState = 'fallback'; app.bakerWhy = info.why || 'no art'; }
  if (!info.ok) {
    $('#mapmsg').textContent =
      `${name} has no drawable ground art — ${info.why}`;
  } else {
    $('#mapmsg').classList.add('hidden');
  }
  schedule();
  api('/api/mapedit/editable?name=' + encodeURIComponent(name))
    .then(rep => { app.editable = rep; renderEditableCard(); })
    .catch(() => {});
}

function viewSize() {
  const w = $('#mapwrap');
  return { w: w.clientWidth, h: w.clientHeight };
}

function sizeCanvas() {
  const c = $('#mapcanvas');
  const r = viewSize();
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.max(1, Math.round(r.w * dpr));
  c.height = Math.max(1, Math.round(r.h * dpr));
}

function schedule() {
  if (app.dirty) return;
  app.dirty = true;
  const run = () => { app.dirty = false; draw(); };
  // A backgrounded tab does not fire rAF at all, which is right for an
  // animation and wrong for "render once so a screenshot has something in
  // it". Fall back to a timer when the page is hidden.
  if (document.hidden) setTimeout(run, 0);
  else requestAnimationFrame(run);
}

function draw() {
  const c = $('#mapcanvas');
  const g = c.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const r = viewSize();
  g.clearRect(0, 0, r.w, r.h);
  if (!app.state || !app.info || !app.info.ok) { updateHud(); return; }

  // Nearest-neighbour once a texel is bigger than a pixel: this is pixel art
  // seen through a fixed camera, and smoothing it is a lie about the asset.
  g.imageSmoothingEnabled = app.state.zoom < 1;

  // The four art layers come off the GPU when the map's tile set is resident,
  // and off the server as PNG tiles when it is not. `passability` is not art
  // -- it is drawn from the cell grid, has no DXT anywhere in it, and stays
  // on the tile path in both cases.
  const onGpu = bakerLive() && drawGL(app.state, r);
  if (!onGpu) clearGL();
  // `undefined` means "every enabled layer", which is the original behaviour.
  const layers = onGpu
    ? ['passability'].filter(l => app.state.layers[l])
    : undefined;
  if (!layers || layers.length) {
    for (const t of M.visibleTiles(app.state, r.w, r.h, layers)) {
      const im = tile(t);
      if (im && im.complete && im.naturalWidth) {
        g.drawImage(im, t.x, t.y, t.w, t.h);
      }
    }
  }
  drawSelection(g);
  drawPending(g);
  updateHud();
}

/** The tile cache. An <img> per (layer, level, tx, ty); the browser's own HTTP
 *  cache does the rest, and the server caches the PNG it encoded. */
function tile(t) {
  let ent = app.tiles.get(t.key + (t.t ? '|' + t.t : ''));
  if (ent) return ent.img;
  const img = new Image();
  img.onload = schedule;
  img.onerror = () => {};
  img.src = '/api/mapedit/tile?' + M.tileQuery(app.info.name, t);
  ent = { img };
  if (app.tiles.size > 900) app.tiles.clear();
  app.tiles.set(t.key + (t.t ? '|' + t.t : ''), ent);
  return img;
}

const OUTLINE_STYLE = {
  tile: { width: 1, dash: [4, 3], colour: '--accent' },
  cell: { width: 2, dash: [], colour: '--warn' },
  sprite: { width: 1.5, dash: [6, 4], colour: '--accent' },
  footprint: { width: 1, dash: [], colour: '--good' },
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawSelection(g) {
  const outs = M.outlines(app.pick, app.info);
  for (const o of outs) {
    const st = OUTLINE_STYLE[o.role] || OUTLINE_STYLE.tile;
    g.lineWidth = st.width;
    g.setLineDash(st.dash);
    g.strokeStyle = cssVar(st.colour);
    g.beginPath();
    o.points.forEach((p, i) => {
      const [x, y] = M.artToScreen(app.state, p[0], p[1]);
      if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
    });
    g.closePath();
    g.stroke();
  }
  g.setLineDash([]);
}

function drawPending(g) {
  if (!app.pending.length || !app.info.ok) return;
  g.setLineDash([]);
  for (const e of app.pending) {
    g.fillStyle = cssVar(e.blocked ? '--bad' : '--good');
    g.globalAlpha = 0.55;
    g.beginPath();
    M.diamond(app.info, e.x, e.y).forEach((p, i) => {
      const [x, y] = M.artToScreen(app.state, p[0], p[1]);
      if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
    });
    g.closePath();
    g.fill();
  }
  g.globalAlpha = 1;
}

function updateHud() {
  const s = app.state;
  $('#hud-z').textContent = s ? `${(s.zoom * 100).toFixed(0)}% (level ${M.tileLevel(s.zoom)})` : '—';
  $('#zoom-label').textContent = s ? `${(s.zoom * 100).toFixed(0)}%` : '—';
  const a = $('#hud-art');
  if (a) { a.textContent = artStatus(); a.title = artStatusDetail(); }
}

/** Which path is drawing the art. Worth a permanent readout rather than a
 *  console line: "the map looks the same but is slow again" is otherwise
 *  indistinguishable from "the map looks the same". */
function artStatus() {
  if (!app.info || !app.info.ok) return '—';
  if (app.bakerState === 'ready') {
    const b = app.bakerStats;
    return b ? `GPU · ${(b.bytes / 1e6).toFixed(1)} MB DXT` : 'GPU';
  }
  if (app.bakerState === 'loading') return 'loading…';
  if (app.bakerState === 'fallback') return 'server PNG';
  return '—';
}

function artStatusDetail() {
  const b = app.bakerStats;
  if (app.bakerState === 'ready' && b)
    return `${b.tiles} ground tiles + ${b.sprites} sprites resident, ` +
           `${(b.bytes / 1e6).toFixed(2)} MB fetched in ${b.fetchMs.toFixed(0)} ms, ` +
           `uploaded in ${b.loadMs.toFixed(0)} ms. Nothing is fetched per ` +
           `pan, zoom or layer toggle.`;
  if (app.bakerState === 'fallback')
    return 'falling back to server-rendered PNG tiles: ' + app.bakerWhy;
  return '';
}

// ------------------------------------------------------------------ input

function localPoint(e) {
  const r = $('#mapcanvas').getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}

function onWheel(e) {
  if (!app.state) return;
  e.preventDefault();
  const [x, y] = localPoint(e);
  app.state = M.zoomAt(app.state, e.deltaY > 0 ? -1 : +1, x, y);
  schedule();
}

function zoom(steps) {
  if (!app.state) return;
  const r = viewSize();
  app.state = M.zoomAt(app.state, steps, r.w / 2, r.h / 2);
  schedule();
}

function fitMap() {
  if (!app.state) return;
  const r = viewSize();
  app.state = M.fit(app.state, r.w, r.h);
  schedule();
}

let drag = null;

function onDown(e) {
  if (!app.state) return;
  const [x, y] = localPoint(e);
  drag = { x, y, moved: 0, start: [x, y] };
  $('#mapcanvas').classList.add('dragging');
  $('#mapcanvas').setPointerCapture(e.pointerId);
}

function onMove(e) {
  if (!app.state) return;
  const [x, y] = localPoint(e);
  const [ax, ay] = M.screenToArt(app.state, x, y);
  $('#hud-px').textContent = `${ax | 0}, ${ay | 0}`;
  if (app.info && app.info.ok) {
    const gx = Math.floor(ax / 64 + ay / 32);
    const gy = Math.floor(ay / 32 - ax / 64 + app.info.originCells);
    $('#hud-cell').textContent = `${gx}, ${gy}`;
  }
  if (!drag) return;
  drag.moved += Math.abs(x - drag.x) + Math.abs(y - drag.y);
  app.state = M.panBy(app.state, x - drag.x, y - drag.y);
  drag.x = x; drag.y = y;
  schedule();
}

function onUp(e) {
  $('#mapcanvas').classList.remove('dragging');
  if (!drag) return;
  const wasClick = drag.moved < 4;
  const at = drag.start;
  drag = null;
  if (wasClick && app.state) {
    const [ax, ay] = M.screenToArt(app.state, at[0], at[1]);
    if (app.paint) togglePassability(ax, ay);
    else doPick(ax, ay);
  }
}

async function doPick(ax, ay) {
  if (!app.info || !app.info.ok) return;
  const layers = M.LAYERS.filter(l => app.state.layers[l] && l !== 'passability' &&
                                      l !== 'background');
  try {
    app.pick = await api('/api/mapedit/pick?name=' +
      encodeURIComponent(app.info.name) +
      `&px=${ax.toFixed(2)}&py=${ay.toFixed(2)}&layers=${layers.join(',')}`);
    app.pick.integrityDmap = app.info.dmap;
  } catch (e) {
    toast('pick failed: ' + e.message);
    return;
  }
  renderInspector();
  renderTexCard();
  schedule();
}

// ------------------------------------------------------------------ panels

function renderMapCard() {
  const i = app.info;
  const b = $('#map-body');
  b.innerHTML = '';
  b.classList.remove('mut');
  if (!i) { b.textContent = 'nothing loaded'; return; }
  const rows = [
    ['Map', `${i.name}  (${i.mapSize[0]} × ${i.mapSize[1]} cells)`],
    ['.DMap', i.dmap + (i.dmapStaged ? '   [reading your staged copy]' : '')],
    ['Layers declared', String(i.layerCount)],
    ['TERRAIN / COVER', `${i.sceneParts} scene parts · ${i.covers} covers`],
  ];
  if (i.ok) {
    rows.push(['Painted image', `${i.pixels[0]} × ${i.pixels[1]} px ` +
                                `(${i.pul[0]} × ${i.pul[1]} tiles of ${i.gridSize}px)`]);
    rows.push(['Puzzle', i.puzzle]);
    rows.push(['Origin K', `${i.originCells} cells`]);
    rows.push(['Placement', i.consistent
      ? 'the art and the .DMap agree exactly'
      : `MISMATCH — the art implies ${i.impliedSize} cells (docs/ground_art.md §3.1)`]);
    rows.push(['Background planes',
               (i.backdrops || []).map(x => x.path).join('\n') || 'none']);
  } else {
    rows.push(['Ground art', i.why]);
  }
  if ((i.missingScenes || []).length) {
    rows.push(['Missing scenes', i.missingScenes.join('\n')]);
  }
  b.appendChild(rowList(rows));
}

function rowList(rows) {
  const host = el('div', 'insp');
  for (const [k, v, note] of rows) {
    const r = el('div', 'r');
    r.appendChild(el('div', 'k', k));
    const vv = el('div', 'v mono', v);
    r.appendChild(vv);
    host.appendChild(r);
    if (note) {
      const n = el('div', 'note', note);
      host.appendChild(n);
    }
  }
  return host;
}

function renderInspector() {
  const b = $('#insp-body');
  b.innerHTML = '';
  b.classList.remove('mut');
  const secs = M.inspector(app.pick, app.info);
  for (const s of secs) {
    const h = el('div');
    const head = el('div', 'small');
    head.appendChild(el('b', null, s.title));
    if (s.badge) {
      head.appendChild(document.createTextNode(' '));
      head.appendChild(el('span', 'badge ' + s.badge,
        s.badge === 'checked' ? 'INTEGRITY-CHECKED' : 'FREE TO EDIT'));
    }
    h.appendChild(head);
    const list = el('div', 'insp');
    for (const r of s.rows) {
      const row = el('div', 'r');
      row.appendChild(el('div', 'k', r.k));
      const v = el('div', 'v' + (r.mono ? ' mono' : ''));
      if (r.asset) {
        const btn = el('button', 'assetlink', r.v);
        btn.title = 'Open this file in the asset browser';
        btn.addEventListener('click',
          () => window.open('/#' + encodeURIComponent(r.asset), '_blank'));
        v.appendChild(btn);
      } else {
        v.textContent = r.v;
      }
      row.appendChild(v);
      list.appendChild(row);
      if (r.note) list.appendChild(el('div', 'note', r.note));
    }
    h.appendChild(list);
    h.style.marginBottom = '10px';
    b.appendChild(h);
  }
}

/** The texture swap, identical in shape to the asset browser's (§3 of
 *  docs/viewer.md) and using the same two endpoints. */
function renderTexCard() {
  const b = $('#tex-body');
  b.innerHTML = '';
  const p = app.pick;
  const path = p && ((p.editable && p.editable.art && p.editable.art[0]) || p.texture);
  if (!path) {
    b.className = 'cardbody mut';
    b.textContent = p ? 'this selection has no texture of its own'
                      : 'nothing selected';
    return;
  }
  b.className = 'cardbody';
  app.previewPath = path;
  app.previewToken = null;

  const img = el('img');
  img.style.cssText = 'max-width:100%;image-rendering:pixelated;border:1px solid var(--line)';
  img.src = '/api/texture?path=' + encodeURIComponent(path) + '&t=' + Date.now();
  b.appendChild(img);
  b.appendChild(el('div', 'small mono', path));

  const file = el('input');
  file.type = 'file';
  file.accept = '.png,.dds,.bmp,.jpg,.jpeg,image/*';
  file.style.cssText = 'font-size:11px;max-width:100%';
  b.appendChild(file);

  const fmt = el('select');
  fmt.style.width = 'auto';
  for (const f of ['(match original)', 'DXT1', 'DXT3', 'DXT5']) {
    const o = el('option', null, f);
    o.value = f[0] === '(' ? '' : f;
    fmt.appendChild(o);
  }
  const fl = el('label', null, 'DDS format');
  fl.appendChild(fmt);
  b.appendChild(fl);

  const msg = el('div');
  b.appendChild(msg);

  const btnRow = el('div', 'row');
  const bStage = el('button', 'primary', 'Stage this swap');
  const bDrop = el('button', 'ghost', 'Discard preview');
  bStage.disabled = bDrop.disabled = true;
  btnRow.append(bStage, bDrop);
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
    app.previewToken = res.token;
    img.src = '/api/texture?path=' + encodeURIComponent(path) +
              '&preview=' + res.token + '&t=' + Date.now();
    msg.appendChild(el('div', 'small',
      `previewing ${f.name} → ${res.info.width}×${res.info.height} ` +
      `${res.info.format}, ${res.bytes.toLocaleString()} bytes. Nothing has ` +
      `been written to the game. Stage it to see it on the map.`));
    for (const w of res.warnings) msg.appendChild(el('div', 'warn', '⚠ ' + w));
    bStage.disabled = bDrop.disabled = false;
  });

  bDrop.addEventListener('click', () => {
    app.previewToken = null;
    img.src = '/api/texture?path=' + encodeURIComponent(path) + '&t=' + Date.now();
    msg.innerHTML = '';
    file.value = '';
    bStage.disabled = bDrop.disabled = true;
  });

  bStage.addEventListener('click', async () => {
    if (!app.previewToken) return;
    try {
      const r = await api(`/api/stage?path=${encodeURIComponent(path)}` +
                          `&token=${app.previewToken}`, { method: 'POST' });
      toast('staged → ' + r.logical);
      // The map draws through mods/stage, so every cache of this texture is
      // now wrong. Drop them and let the art come back changed.
      refreshArt();
      schedule();
      openDrawer();
    } catch (e) {
      msg.appendChild(el('div', 'err', 'stage failed: ' + e.message));
    }
  });
}

// -- the gated half ---------------------------------------------------------

function setPaint(on) {
  app.paint = !!on;
  $('#mapcanvas').classList.toggle('painting', app.paint);
}

function togglePassability(ax, ay) {
  const gx = Math.floor(ax / 64 + ay / 32);
  const gy = Math.floor(ay / 32 - ax / 64 + app.info.originCells);
  if (gx < 0 || gy < 0 || gx >= app.info.mapSize[0] || gy >= app.info.mapSize[1]) return;
  const i = app.pending.findIndex(e => e.x === gx && e.y === gy);
  if (i >= 0) app.pending.splice(i, 1);
  else app.pending.push({ x: gx, y: gy, blocked: false });
  renderPassCard();
  schedule();
}

function renderPassCard() {
  const b = $('#pass-body');
  b.innerHTML = '';
  b.className = 'cardbody';
  if (!app.info) { b.classList.add('mut'); b.textContent = 'nothing loaded'; return; }

  const gate = el('div', 'gate');
  gate.appendChild(el('h3', null, `${app.info.dmap} is in integrity.json`));
  const i = app.info.integrity || {};
  gate.appendChild(el('p', null,
    `The manifest has ${i.manifestRows} entries naming ${i.manifestFiles} ` +
    `distinct files — 136 .DMap and 7 ini/*.json, and nothing else. Every ` +
    `piece of this map's ART is outside it and swaps like any other .dds. ` +
    `Passability, dimensions and layer placement are not: they live in the ` +
    `.DMap, and changing one changes a file the client verifies.`));

  const chk = el('label', 'chk');
  const box = el('input');
  box.type = 'checkbox';
  box.checked = app.paint;
  box.addEventListener('change', () => {
    if (box.checked && !app.state.layers.passability) setLayer('passability', true);
    setPaint(box.checked);
    renderPassCard();
  });
  chk.appendChild(box);
  chk.appendChild(document.createTextNode(
    ' Edit passability — click a cell to open it'));
  gate.appendChild(chk);
  b.appendChild(gate);

  if (!app.pending.length) {
    b.appendChild(el('p', 'small mut',
      app.paint ? 'Click cells on the map. Nothing is written until you press ' +
                  'Stage below.'
                : 'No changes pending.'));
    return;
  }

  const list = el('div', 'pendlist');
  for (const e of app.pending) {
    list.appendChild(el('div', null,
      `cell ${e.x}, ${e.y} → ${e.blocked ? 'blocked' : 'walkable'}`));
  }
  b.appendChild(list);

  const row = el('div', 'row');
  const stage = el('button', 'danger',
    `Stage ${app.pending.length} change(s) to the .DMap`);
  const clear = el('button', 'ghost', 'Clear');
  clear.addEventListener('click', () => { app.pending = []; renderPassCard(); schedule(); });
  stage.addEventListener('click', stagePassability);
  row.append(stage, clear);
  b.appendChild(row);

  const flip = el('button', 'ghost tiny', 'Flip the pending cells to BLOCKED');
  flip.addEventListener('click', () => {
    for (const e of app.pending) e.blocked = !e.blocked;
    renderPassCard();
    schedule();
  });
  b.appendChild(flip);
}

async function stagePassability() {
  const ok = confirm(
    `${app.info.dmap} is one of the files integrity.json lists.\n\n` +
    `Staging this writes mods/stage/${app.info.dmap} — the game install is ` +
    `NOT touched until you run Install. The original is copied byte for byte ` +
    `apart from the ${app.pending.length} cell mask(s) and the row checksums ` +
    `that cover them.\n\nProceed?`);
  if (!ok) return;
  let res;
  try {
    res = await api('/api/mapedit/passability?name=' +
                    encodeURIComponent(app.info.name), {
      method: 'POST',
      body: JSON.stringify({ cells: app.pending, ack: ACK }),
    });
  } catch (e) {
    toast('stage failed: ' + e.message, 5000);
    return;
  }
  if (!res.ok) {
    toast(res.why || 'refused', 5000);
    return;
  }
  toast(`staged ${res.changedCount} cell(s); ${res.rowsRechecksummed} row ` +
        `checksum(s) recomputed, ${res.rowChecksumsBad} bad`, 5000);
  app.pending = [];
  refreshArt();
  app.info = await api('/api/mapedit/map?name=' + encodeURIComponent(app.info.name));
  renderMapCard();
  renderPassCard();
  schedule();
  openDrawer();
}

/** The exact string `mapedit.ACK` demands. Spelled out here rather than
 *  fetched, so the opt-in is visible in the source of the thing doing it. */
const ACK = 'I understand this file is integrity-checked';

function renderEditableCard() {
  const b = $('#editable-body');
  b.innerHTML = '';
  b.className = 'cardbody';
  const rep = app.editable;
  if (!rep) { b.classList.add('mut'); b.textContent = 'nothing loaded'; return; }
  b.appendChild(el('p', 'small',
    `${rep.freeCount} of this map's files are free to edit; ` +
    `${rep.checkedCount} are integrity-checked.`));
  for (const r of rep.checked) {
    const d = el('div', 'r');
    d.appendChild(el('span', 'badge checked', 'CHECKED'));
    d.appendChild(el('span', 'mono small', ' ' + r.path));
    b.appendChild(d);
    b.appendChild(el('div', 'note', r.what));
  }
  const det = el('details');
  det.appendChild(el('summary', 'small', `${rep.freeCount} files free to edit`));
  const list = el('div', 'pendlist');
  for (const r of rep.free) list.appendChild(el('div', null, r.path));
  det.appendChild(list);
  b.appendChild(det);
}

// ------------------------------------------------------------------ drawer

async function openDrawer() {
  $('#drawer').classList.remove('hidden');
  await refreshStage();
}

async function refreshStage() {
  const data = await api('/api/stage');
  $('#stage-dir').textContent = data.stageDir;
  const host = $('#stage-list');
  host.innerHTML = '';
  if (!data.rows.length) {
    host.appendChild(el('p', 'mut',
      'Nothing staged yet. Click a tile or a sprite, load a replacement image ' +
      'in the Texture panel, then press "Stage this swap".'));
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
      await api('/api/unstage?path=' + encodeURIComponent(r.logical),
                { method: 'POST' });
      await refreshStage();
      refreshArt();
      if (app.info) {
        app.info = await api('/api/mapedit/map?name=' +
                             encodeURIComponent(app.info.name));
        renderMapCard();
        renderPassCard();
      }
      schedule();
    });
    act.appendChild(rm);
    tr.appendChild(act);
    t.appendChild(tr);
  }
  host.appendChild(t);
}

async function runMod(url) {
  const out = $('#mod-output');
  out.textContent = 'running…';
  try {
    const r = await api(url, { method: 'POST' });
    out.textContent = `$ ${r.cmd}\n\n${r.stdout}` +
                      `${r.stderr ? '\n' + r.stderr : ''}\n[exit ${r.returncode}]`;
  } catch (e) { out.textContent = 'failed: ' + e.message; }
  await refreshStage();
}

// ------------------------------------------------------------------ shots

/** The whole page, including the map, into out/viewer/shots/.
 *
 *  `pageshot.js` substitutes every live canvas with its own `toDataURL`, so
 *  the map survives the SVG round trip. It also reads a page-level `viewer`
 *  (the WebGL one the other two pages have); this page has none, and leaving
 *  the identifier undeclared would be a ReferenceError inside pageShot rather
 *  than a missing picture.
 */
window.viewer = null;

async function savePage(name) {
  const base = (name || ('mapedit-' + (app.info ? app.info.name : 'page')))
    .replace(/[^a-z0-9-]+/gi, '_');
  try {
    return await window.pageShot(base);
  } catch (e) { toast('snapshot failed: ' + e.message, 4000); return null; }
}
window.saveShot = savePage;

// ---------------------------------------------- collect / export a map
//
// A map is a closure, not a file. Three things about that closure are
// surprising enough that this shows them BEFORE anything is taken, and
// every toggle is one of those facts rather than a preference:
//
//   * its scenery is a small named subset of an index shared with other
//     maps -- 09christmas01 takes 15 tiles from an index of 363, and that
//     index is used by 31 other maps;
//   * nearly all of its art lives inside c3.wdf/data.wdf, so a file count
//     taken from the filesystem is wrong by an order of magnitude;
//   * the .DMap alone can be hashed by the install's integrity manifest.
//
// `co.shared` is the expensive one: resolving who else uses each file walks
// every map on the install (~13 s). It defaults on, because without it the
// entry cannot record what it shares and export cannot warn -- but it is a
// toggle, because a quick look should not cost a full walk.

const co = { closure: null, busy: false, shared: true,
             roles: { puzzle: true, ani: true, art: true },
             withShared: true, entries: [] };

async function loadClosure() {
  const i = app.info;
  if (!i || !i.name) { co.closure = null; renderCollectCard(); return; }
  co.busy = true; renderCollectCard();
  try {
    co.closure = await api('/api/keep/map?name=' + encodeURIComponent(i.name) +
                           (co.shared ? '' : '&fast=1'));
  } catch (e) {
    co.closure = { error: e.message };
  }
  co.busy = false;
  renderCollectCard();
}

function chosenCount() {
  const c = co.closure;
  if (!c || !c.parts) return 0;
  return c.parts.filter(p => {
    if (p.role === 'dmap' || p.role === 'effect' || p.missing) return false;
    if (!co.roles[p.role]) return false;
    if (p.sharedWith && p.sharedWith.length && !co.withShared) return false;
    return true;
  }).length;
}

function coCheck(label, on, onChange, note) {
  const w = el('label', 'co-check');
  const b = document.createElement('input');
  b.type = 'checkbox'; b.checked = !!on;
  b.addEventListener('change', () => onChange(b.checked));
  w.appendChild(b);
  w.appendChild(el('span', '', ' ' + label));
  if (note) w.appendChild(el('div', 'note', note));
  return w;
}

function renderCollectCard() {
  const b = $('#collect-body');
  if (!b) return;
  b.innerHTML = '';
  b.classList.remove('mut');
  const i = app.info;
  if (!i || !i.name) {
    b.classList.add('mut'); b.textContent = 'pick a map on the left'; return;
  }
  if (co.busy) { b.classList.add('mut'); b.textContent = 'resolving…'; return; }
  const c = co.closure;
  if (!c) {
    const go = el('button', 'ghost', 'Show what this map is made of');
    go.addEventListener('click', loadClosure);
    b.appendChild(go);
    return;
  }
  if (c.error) { b.classList.add('mut'); b.textContent = c.error; return; }

  const s = c.summary || {};
  const byRole = s.counts || {};
  const kb = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + ' MB'
                                 : Math.round(n / 1024) + ' KB');
  const rows = [
    ['Closure', `${s.files} files · ${kb(s.totalBytes || 0)}` +
                (byRole.effect ? ` · ${byRole.effect} effects` : '')],
    ['Breakdown', Object.entries(byRole)
      .map(([k, v]) => `${k} ×${v}`).join('  ')],
  ];
  if (s.sharedResolved) {
    rows.push(['Shared with other maps', `${s.shared} of ${s.files} files`,
               s.shared ? 'staging these changes those maps too' : '']);
  } else {
    rows.push(['Shared with other maps', 'not resolved',
               'turn on "resolve sharing" to find out — without it the entry ' +
               'cannot record what it shares and export cannot warn']);
  }
  if (s.integrity) {
    rows.push(['Integrity-hashed', `${s.integrity} file(s)`,
               'this install hashes the .DMap, so replacing it is detectable']);
  }
  if (s.missing) {
    rows.push(['Unresolved keys', String(s.missing),
               'layers naming art this client’s index does not resolve — ' +
               'reported, never dropped']);
  }
  if (s.unreadable) rows.push(['Unreadable', String(s.unreadable)]);
  b.appendChild(rowList(rows));

  const opts = el('div', 'co-opts');
  opts.appendChild(el('div', 'note',
    'The .DMap always travels — it is what the map is.'));
  opts.appendChild(coCheck(`Background (.pul) ×${byRole.puzzle || 0}`,
    co.roles.puzzle, v => { co.roles.puzzle = v; renderCollectCard(); }));
  opts.appendChild(coCheck(`Scenery index (.ani) ×${byRole.ani || 0}`,
    co.roles.ani, v => { co.roles.ani = v; renderCollectCard(); }));
  opts.appendChild(coCheck(`Tiles ×${byRole.art || 0}`,
    co.roles.art, v => { co.roles.art = v; renderCollectCard(); }));
  opts.appendChild(coCheck('Include art shared with other maps',
    co.withShared, v => { co.withShared = v; renderCollectCard(); },
    'off leaves it behind; the map still draws from the install’s own copies'));
  opts.appendChild(coCheck('Resolve sharing (walks every map, ~13 s)',
    co.shared, v => { co.shared = v; loadClosure(); }));
  b.appendChild(opts);

  b.appendChild(el('div', 'note',
    `Will collect the .DMap + ${chosenCount()} file(s).`));

  const row = el('div', 'row');
  const dry = el('button', 'ghost', 'Preview');
  dry.addEventListener('click', () => doCollect(true));
  const go = el('button', '', 'Collect');
  go.addEventListener('click', () => doCollect(false));
  row.appendChild(dry); row.appendChild(go);
  b.appendChild(row);
  const out = el('pre', 'out'); out.id = 'collect-out';
  b.appendChild(out);
}

async function doCollect(dry) {
  const i = app.info;
  const out = $('#collect-out');
  out.textContent = dry ? 'previewing…' : 'collecting…';
  try {
    const r = await api('/api/keep/map', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        map: i.name, dryRun: !!dry, fast: !co.shared,
        roles: Object.keys(co.roles).filter(k => co.roles[k]),
        shared: co.withShared,
      }),
    });
    if (dry) {
      const w = r.would || [];
      out.textContent =
        `would collect ${w.length} item(s), leaving ${(r.dropped || []).length} behind\n` +
        w.slice(0, 14).map(x => `  ${x.role.padEnd(7)} ${x.rel}`).join('\n') +
        (w.length > 14 ? `\n  … ${w.length - 14} more` : '');
    } else {
      const e = r.entry || {};
      const lines = ['collected ' + e.id];
      if ((r.dropped || []).length) {
        lines.push(`  ${r.dropped.length} file(s) left behind by your toggles`);
      }
      if (e.integrity) lines.push('  the .DMap is integrity-hashed here');
      if (e.unresolved) lines.push(`  ${e.unresolved.length} unresolved key(s)`);
      if (e.unread) lines.push(`  ${e.unread.length} file(s) unreadable`);
      out.textContent = lines.join('\n');
      toast('collected ' + e.id);
      loadEntries();
    }
  } catch (e) {
    out.textContent = 'failed: ' + e.message;
  }
}

async function loadEntries() {
  try {
    const d = await api('/api/keep');
    co.entries = (d.entries || []).filter(e => e.category === 'Maps');
  } catch (e) { co.entries = []; }
  renderExportCard();
}

function renderExportCard() {
  const b = $('#export-body');
  if (!b) return;
  b.innerHTML = '';
  b.classList.remove('mut');
  if (!co.entries.length) {
    b.classList.add('mut'); b.textContent = 'no maps collected yet'; return;
  }
  const sel = document.createElement('select');
  for (const e of co.entries) {
    const o = document.createElement('option');
    o.value = e.id;
    const n = (e.parts || []).length;
    o.textContent = `${e.name || e.id}  (${n} file${n === 1 ? '' : 's'})`;
    sel.appendChild(o);
  }
  b.appendChild(sel);

  const opts = el('div', 'co-opts');
  opts.appendChild(el('div', 'note',
    'Staged into mods/stage/ only. Nothing touches the install until you ' +
    'press Install in Mod staging.'));
  const policy = document.createElement('select');
  for (const [v, t] of [
    ['skip-identical', 'Shared art: write only where it differs (recommended)'],
    ['always', 'Shared art: always write it'],
    ['never', 'Shared art: never write it'],
  ]) {
    const o = document.createElement('option'); o.value = v; o.textContent = t;
    policy.appendChild(o);
  }
  opts.appendChild(policy);
  b.appendChild(opts);

  const row = el('div', 'row');
  const go = el('button', '', 'Stage');
  go.addEventListener('click', () => doExport(sel.value, policy.value));
  row.appendChild(go);
  b.appendChild(row);
  const out = el('pre', 'out'); out.id = 'export-out';
  b.appendChild(out);
}

async function doExport(id, policy) {
  const out = $('#export-out');
  out.textContent = 'staging…';
  try {
    const r = await api('/api/keep/stage', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, sharedPolicy: policy }),
    });
    const lines = [];
    for (const w of (r.wrote || [])) lines.push('staged      ' + w);
    for (const s of (r.skipped || [])) lines.push('left alone  ' + s);
    for (const w of (r.sharedArt || [])) {
      const m = w.maps || [];
      lines.push(`SHARED      ${w.path} — also used by ${m.length} other map(s)` +
                 (m.length ? ': ' + m.slice(0, 8).join(', ') +
                             (m.length > 8 ? ', …' : '') : ''));
    }
    for (const w of (r.integrity || [])) {
      lines.push(`INTEGRITY   ${w.path} — ${w.why}`);
    }
    out.textContent = lines.join('\n') || 'nothing to do';
    toast('staged ' + id);
  } catch (e) {
    out.textContent = 'failed: ' + e.message;
  }
}

boot().catch(e => {
  $('#statusline').textContent = 'startup failed: ' + e.message;
  console.error(e);
});
