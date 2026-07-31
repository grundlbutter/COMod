/* builder.js -- the character builder page.
 *
 * A separate page from the asset browser on purpose. The browser is for
 * *finding* things; this is for *making* a character, and the two need
 * different first screens. They link both ways and share the loadout through
 * localStorage, so moving between them never costs you your character.
 *
 * THE ONE RULE THAT SHAPES EVERYTHING HERE
 *
 *   You never pick a mesh and then a texture. You pick an APPEARANCE, because
 *   the appearance tables already are the mesh+texture pairs the game ships
 *   (armor.ini `002135300` = Mesh0 002135000 + Texture0 002135300). Offering a
 *   mesh and a texture separately is a cross-product, and most of it does not
 *   exist. So the picker is two obvious choices --
 *
 *       an item   (one distinct mesh: ~167 per body type)
 *       a colour  (its texture variants: 7, typically)
 *
 *   -- and the server (tools/builder.py) only ever emits pairs it has checked
 *   against disk.
 *
 * Everything else follows: once a body is chosen, each slot offers only art
 * made for that body type; slots whose art does not ship are greyed with the
 * reason; the right-hand panels collapse and remember; and the loadout is a
 * URL you can send someone.
 *
 * THE SECOND MODE: MODELS
 *
 *   A monster is NOT a character with different art, so it does not get the
 *   slot treatment. `c3/monster/103/` is sixteen files, one per action, and
 *   most of them carry their own PHY -- "play the walk" means load a different
 *   MESH. There is nothing to equip. So the left rail switches wholesale
 *   between "your character" (slots) and "models" (a list), and the stage,
 *   the action dropdown, the playback controls and the camera are shared
 *   because those parts genuinely are the same job.
 *
 *   tools/models.py owns the catalogue and the reasoning; this file only draws
 *   it. Monsters, NPCs, ghosts, the one shipped mount family, the eight
 *   character-select roles and the whole effect library are all in it.
 */

'use strict';

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined) e.textContent = txt;
  return e;
};
const debounce = (fn, ms) => {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

const VALUE_LABEL = {
  any: 'Any class', unknown: 'Unclassified', 'n/a': 'n/a',
  female: 'Female', male: 'Male', other: 'Other body',
  small: 'Small', large: 'Large', hair: 'Hair', headgear: 'Headgear',
  armour: 'Armour', 'base body': 'Plain body', 'npc body': 'NPC body',
  weapon: 'Weapon',
};
const lbl = v => VALUE_LABEL[v] || v;

// ---------------------------------------------------------------- state
const B = {
  /** 'character' (slots + appearances) or 'model' (monsters, NPCs, ...). */
  mode: 'character',
  slots: [],
  slotById: {},
  config: null,
  /** slot name -> {id, table, name, detail, mesh, texture} */
  loadout: {},
  bodyType: '002',
  figure: null,
  anchors: null,
  /** the open picker */
  picker: { slot: null, open: false, data: null, items: [], index: -1, variant: 0,
            sel: {}, tags: new Set(), before: null },
  /** `seq` is the clip and whatever continues it: run alternates feet
   *  (120 <-> 121, matching runL.wav / runR.wav) rather than self-looping, so
   *  playback walks a sequence, not a single clip. */
  anim: { action: '100', frame: 0, seqAt: 0, playing: false, data: null,
          seq: [], raf: null, t0: 0, cache: {}, speed: 41, actions: [] },
  superfx: { on: false, rec: null },
  /** the model mode. `zoom` is monster.json's zoomPercent when a row has been
   *  paired, 100 otherwise; `row` is that paired row, which is the USER's
   *  assertion because monster.json carries no link to the art at all. */
  model: { key: '', kind: '', action: '', data: null, list: [], index: -1,
           kinds: [], meta: null, q: '', zoom: 100, row: null,
           distinctOnly: true, raf: null },
  /** `${slot}:${id}` -> the quality ladder of that weapon's family. Cached
   *  because arrowing through a picker asks for it on every keypress. */
  qualityCache: {},
  collapsed: {},
  loadToken: 0,
  status: null,
};
window.B = B;

let viewer = null;

// ---------------------------------------------------------------- plumbing
async function api(path, opts) {
  const r = await fetch(path, opts);
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('json')) {
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r;
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// Selection must feel instant; the render is allowed to trail. Identical to the
// browser's rule (docs/viewer.md §2): the highlight moves on every key, but the
// *load* is debounced by 130 ms and carries a monotonic token that async work
// re-checks, so a burst of arrow presses issues one request -- for the row you
// stopped on -- and a slow response for an abandoned row can never land.
const NAV_DEBOUNCE_MS = 130;
let loadTimer = null;
const tokenNow = () => B.loadToken;
const stillCurrent = tk => B.loadToken === tk;

function requestLoad(fn, { immediate = false } = {}) {
  const tk = ++B.loadToken;
  clearTimeout(loadTimer);
  const run = () => { if (stillCurrent(tk)) fn(tk); };
  if (immediate) run(); else loadTimer = setTimeout(run, NAV_DEBOUNCE_MS);
  return tk;
}

function toast(msg, ms = 2200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

function texUrl(path, { size = 0 } = {}) {
  const p = new URLSearchParams({ path });
  if (size) p.set('size', size);
  return '/api/texture?' + p.toString();
}

/** A picture of an item. Prefers task #21's rendered thumbnail of the mesh --
 *  a picture of the garment beats a picture of its skin sheet -- and falls back
 *  to the texture, then to the checkerboard placeholder, because thumbnail
 *  generation is incremental and may still be running. */
function thumbUrl(path, { size = 48 } = {}) {
  const p = new URLSearchParams({ path });
  if (size) p.set('size', size);
  return '/api/thumb?' + p.toString();
}

function setThumb(img, path, size) {
  img.loading = 'lazy';
  if (!path) return img;
  img.src = thumbUrl(path, { size });
  img.addEventListener('error', () => img.removeAttribute('src'));
  return img;
}

function kv(pairs) {
  const d = el('dl', 'kv');
  for (const [k, v] of pairs) {
    if (v === null || v === undefined || v === '') continue;
    d.appendChild(el('dt', null, k));
    const dd = el('dd');
    if (v instanceof Node) dd.appendChild(v); else dd.textContent = String(v);
    d.appendChild(dd);
  }
  return d;
}

function chip(text, count, on, onClick, cls) {
  const c = el('span', 'chip' + (on ? ' on' : '') + (count === 0 ? ' zero' : '') +
                       (cls ? ' ' + cls : ''));
  c.appendChild(document.createTextNode(text));
  if (count !== null && count !== undefined) c.appendChild(el('span', 'n', String(count)));
  if (onClick) c.addEventListener('click', onClick);
  return c;
}

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    viewer = new Viewer($('#gl'));
    window.viewer = viewer;
    viewer.setViewMode('character');    // the body is always the subject here
  } catch (e) {
    $('#gl-msg').textContent = 'WebGL unavailable: ' + e.message;
  }
  restoreCollapsed();
  bindControls();
  bindKeys();

  try {
    B.status = await api('/api/status');
    $('#statusline').textContent =
      `${B.status.root} · ${B.status.knownPaths.toLocaleString()} resolvable paths`;
  } catch (e) { $('#statusline').textContent = 'status unavailable'; }

  const restored = restoreLoadout();
  if (restored && restored.body) B.bodyType = (restored.body.id || '').slice(0, 3);
  B.config = await api('/api/builder?bodyType=' + encodeURIComponent(B.bodyType));
  B.slots = B.config.slots;
  B.slotById = Object.fromEntries(B.slots.map(s => [s.name, s]));
  $('#compat-note').textContent = B.config.compatNote;

  try {
    const saved = JSON.parse(localStorage.getItem(ANIM_KEY) || 'null');
    if (saved && saved.action) B.anim.action = saved.action;
    if (saved && saved.speed) B.anim.speed = saved.speed;
  } catch (e) { /* ignore */ }
  B.anim.speed = B.anim.speed || B.config.defaultFrameMs || 41;
  try { B.superfx.on = localStorage.getItem(AURA_KEY) === '1'; }
  catch (e) { /* ignore */ }
  if (!restored || !restored.body) await applyDefaultLoadout();
  else await refreshLoadoutNames();
  restoreModelState();
  await fillActions();
  renderSlots();
  renderAnimPanel();
  applyMode({ rebuild: false });
  if (B.mode === 'model') await loadModelList({ select: B.model.key });
  await rebuild({ reframe: true });
  if (B.mode === 'character') showLookFor('body');
}

async function applyDefaultLoadout() {
  // An empty stage is not a useful starting point for someone who has never
  // seen this before, so the page opens on a real character: the plain starting
  // clothes (armor.ini series 132 = Coat / Dress) plus a hairstyle.
  const def = B.config.defaultLoadout || {};
  for (const [slot, id] of Object.entries(def)) {
    await equip(slot, id, { silent: true, rebuild: false });
  }
}

/** The action list depends on the equipped weapon: 3dmotion.ini is keyed
 *  <shape><weaponset><action>, and a 410 swing is a different motion from the
 *  unarmed one. So this is re-fetched whenever a hand changes. */
async function fillActions() {
  if (B.mode === 'model') return fillModelActions();
  const sel = $('#anim-action');
  const p = new URLSearchParams();
  if (B.loadout.body) p.set('body', B.loadout.body.id);
  if (B.loadout.r_weapon) p.set('weapon', B.loadout.r_weapon.id);
  if (B.loadout.l_weapon) p.set('offHand', B.loadout.l_weapon.id);
  let data;
  try { data = await api('/api/actions?' + p.toString()); }
  catch (e) { return; }
  B.anim.actions = data.actions;
  B.anim.meta = data;
  sel.innerHTML = '';
  let group = null, host = sel;
  for (const a of data.actions) {
    if (a.groupLabel !== group) {
      group = a.groupLabel;
      host = document.createElement('optgroup');
      host.label = group;
      sel.appendChild(host);
    }
    const o = el('option', null,
      a.named ? a.label : `${a.label} (${a.code}, unidentified)`);
    o.value = a.code;
    o.title = a.evidence || '';
    host.appendChild(o);
  }
  if (!data.actions.some(a => a.code === B.anim.action)) {
    B.anim.action = (data.actions[0] || { code: '100' }).code;
  }
  sel.value = B.anim.action;
  $('#anim-speed').value = String(B.anim.speed || data.defaultFrameMs || 41);
}

// ---------------------------------------------------------------- slots
function slotThumb(v) {
  // A rendered picture of the item, not its skin sheet: "what am I wearing" is
  // not answerable from a 128x128 texture atlas. The render uses the mesh's
  // primary colour, so the tooltip names the colour actually equipped and the
  // Colour & variants panel shows it — the card is the item, the swatch is the
  // colour.
  const img = el('img');
  img.loading = 'lazy';
  if (!v) return img;
  img.src = thumbUrl(v.mesh || v.texture, { size: 64 });
  img.title = `${v.name}${v.detail ? ' · ' + v.detail : ''}\n${v.texture || ''}`;
  img.addEventListener('error', () => {
    if (v.texture && !img.dataset.fell) {
      img.dataset.fell = '1';
      img.src = texUrl(v.texture, { size: 64 });
    } else img.removeAttribute('src');
  });
  return img;
}

function renderSlots() {
  const primary = $('#slot-cards');
  const more = $('#slot-cards-more');
  primary.innerHTML = '';
  more.innerHTML = '';
  for (const s of B.slots) {
    const host = s.primary ? primary : more;
    host.appendChild(slotCard(s));
  }
  const hiddenCount = B.slots.filter(s => !s.primary).length;
  $('#btn-more-slots').textContent =
    (more.classList.contains('hidden') ? 'More slots ▾ ' : 'Fewer slots ▴ ') +
    `(${hiddenCount})`;
  saveLoadout();
}

function slotCard(s) {
  const v = B.loadout[s.name];
  const card = el('div', 'slotcard' + (v ? ' filled' : '') +
                          (s.usable ? '' : ' disabled'));
  card.dataset.slot = s.name;

  const head = el('div', 'sc-head');
  head.appendChild(el('b', null, s.label));
  head.appendChild(el('span', 'n', s.usable ? `${s.garments}` : '—'));
  card.appendChild(head);

  const bodyRow = el('div', 'sc-body');
  bodyRow.appendChild(slotThumb(v));
  const txt = el('div', 'sc-txt');
  if (v) {
    txt.appendChild(el('b', null, v.name || v.id));
    txt.appendChild(el('span', null, v.detail || ''));
    txt.appendChild(el('span', 'idline', v.id));
  } else if (!s.usable) {
    txt.appendChild(el('b', 'mut', 'not available'));
    txt.appendChild(el('span', null, s.reason.split('.')[0] + '.'));
  } else {
    txt.appendChild(el('b', 'mut', 'empty'));
    txt.appendChild(el('span', null, s.hint || 'click to choose'));
  }
  bodyRow.appendChild(txt);
  card.appendChild(bodyRow);

  if (s.usable) {
    const acts = el('div', 'sc-acts');
    const pick = el('button', 'ghost tiny', v ? 'Change…' : 'Choose…');
    pick.addEventListener('click', ev => { ev.stopPropagation(); openPicker(s.name); });
    acts.appendChild(pick);
    if (v) {
      const rm = el('button', 'ghost tiny', 'Remove');
      rm.addEventListener('click', ev => {
        ev.stopPropagation();
        unequip(s.name);
      });
      acts.appendChild(rm);
      const look = el('a', 'small', 'inspect →');
      look.href = '/#' + loadoutParams().toString();
      look.title = 'open this part in the asset browser';
      look.addEventListener('click', ev => {
        ev.stopPropagation();
        saveLoadout();
      });
      acts.appendChild(look);
    }
    card.appendChild(acts);
    card.addEventListener('click', () => openPicker(s.name));
  } else {
    card.title = s.reason;
    const why = el('div', 'sc-why', s.reason);
    card.appendChild(why);
  }
  return card;
}

// ---------------------------------------------------------------- equipping
async function equip(slot, id, { silent = false, rebuild: doRebuild = true } = {}) {
  const info = B.slotById[slot];
  // The head is one slot: the client draws one head covering, so equipping a
  // helmet necessarily replaces the hair and vice versa.
  for (const grp of (B.config.exclusive || [])) {
    if (grp.includes(slot)) {
      for (const other of grp) if (other !== slot) delete B.loadout[other];
    }
  }
  // Plain language over raw ids, always. The picker already knows the name; a
  // loadout restored from a link or from localStorage does not, so ask.
  let known = pickerOptionFor(slot, id);
  if (!known) {
    try {
      known = await api(`/api/option?slot=${encodeURIComponent(slot)}` +
                        `&id=${encodeURIComponent(id)}`);
    } catch (e) { known = null; }
  }
  let part = known;
  if (!known || !known.mesh) {
    try {
      const rec = await api(`/api/appearance?id=${encodeURIComponent(id)}` +
                            `&table=${encodeURIComponent(slot)}`);
      part = rec && rec[0] && (rec[0].parts.find(p => p.mesh) || rec[0].parts[0]);
    } catch (e) { /* fall through: we still know the id */ }
  }
  B.loadout[slot] = {
    id, table: slot,
    name: (known && known.name) || id,
    detail: (known && known.detail) || '',
    mesh: part && part.mesh,
    texture: part && part.texture,
  };
  if (slot === 'body') {
    const bt = id.slice(0, 3);
    if (bt !== B.bodyType) {
      B.bodyType = bt;
      dropIncompatible();
    }
  }
  saveLoadout();
  renderSlots();
  if (!silent) toast(`${B.loadout[slot].name} → ${info ? info.label : slot}`);
  if (doRebuild) await rebuild({ reframe: slot === 'body' });
}

/** A loadout restored from a link or from localStorage is bare ids. Put the
 *  plain-language names back before anything is drawn -- nothing on this page
 *  should require knowing what an appearance id is. */
async function refreshLoadoutNames() {
  for (const [slot, v] of Object.entries(B.loadout)) {
    if (!v || !v.id) continue;
    try {
      const o = await api(`/api/option?slot=${encodeURIComponent(slot)}` +
                          `&id=${encodeURIComponent(v.id)}`);
      B.loadout[slot] = { ...v, name: o.name || v.id, detail: o.detail || '',
                          mesh: o.mesh, texture: o.texture };
    } catch (e) { /* keep the id: better than an empty slot */ }
  }
}

function pickerOptionFor(slot, id) {
  const d = B.picker.data;
  if (!d || B.picker.slot !== slot) return null;
  for (const g of d.garments || []) {
    const hit = (g.variants || []).find(v => v.id === id);
    if (hit) return hit;
  }
  return null;
}

/** Changing the body drops anything that was made for a different body type.
 *  Silently keeping it would render a helmet cut for another skeleton. */
function dropIncompatible() {
  const dropped = [];
  for (const [slot, v] of Object.entries(B.loadout)) {
    if (slot === 'body' || !v) continue;
    const info = B.slotById[slot];
    if (!info || !info.bodySpecific) continue;
    if ((v.id || '').slice(0, 3) !== B.bodyType) {
      dropped.push(info.label);
      delete B.loadout[slot];
    }
  }
  if (dropped.length) {
    toast(`${dropped.join(', ')} cleared — that art is cut for a different body`, 3500);
  }
}

async function unequip(slot) {
  delete B.loadout[slot];
  saveLoadout();
  renderSlots();
  await rebuild({ reframe: false });
}

// ---------------------------------------------------------------- persistence
const LOADOUT_KEY = 'coviewer.loadout';       // shared with the asset browser
const COLLAPSE_KEY = 'cobuilder.collapsed';
const ANIM_KEY = 'cobuilder.anim';
const AURA_KEY = 'cobuilder.aura';

function saveLoadout() {
  try { localStorage.setItem(LOADOUT_KEY, JSON.stringify(B.loadout)); }
  catch (e) { /* ignore */ }
}

function restoreLoadout() {
  const fromUrl = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (fromUrl.get('body')) {
    const out = {};
    for (const [k, v] of fromUrl.entries()) out[k] = { id: v, table: k, name: v };
    B.loadout = out;
    return out;
  }
  try {
    const s = JSON.parse(localStorage.getItem(LOADOUT_KEY) || 'null');
    if (s && typeof s === 'object') {
      B.loadout = Object.fromEntries(
        Object.entries(s).filter(([, v]) => v && v.id));
      return B.loadout;
    }
  } catch (e) { /* ignore */ }
  return null;
}

function loadoutParams() {
  const p = new URLSearchParams();
  for (const [slot, v] of Object.entries(B.loadout)) if (v && v.id) p.set(slot, v.id);
  return p;
}

function copyLink() {
  if (B.mode === 'model') return copyModelLink();
  const url = location.origin + '/builder#' + loadoutParams().toString();
  location.hash = loadoutParams().toString();
  navigator.clipboard.writeText(url).then(
    () => toast('character link copied'),
    () => toast('clipboard blocked; the link is in the address bar'));
}

async function resetAll() {
  if (B.mode === 'model') {
    B.model.row = null;
    B.model.zoom = 100;
    B.model.q = '';
    B.model.kind = '';
    $('#model-search').value = '';
    await loadModelList({ select: B.model.key });
    await rebuild({ reframe: true });
    toast('model view reset');
    return;
  }
  B.loadout = {};
  B.superfx.rec = null;          // the aura *intent* is a setting, not a character
  await applyDefaultLoadout();
  B.bodyType = (B.loadout.body && B.loadout.body.id || '002').slice(0, 3);
  renderSlots();
  await rebuild({ reframe: true });
  toast('back to the starting character');
}

// ---------------------------------------------------------------- collapsing
//
// Each right-hand panel collapses on its own and remembers, exactly like the
// camera and the lock flag already do. `C` toggles the lot.
//
// The logic itself is `cards.js`, shared with the asset browser, because the
// two pages having the same CSS and only one of them having the behaviour is
// precisely how the browser page ended up with eight headers that showed a
// hand cursor and did nothing.

function restoreCollapsed() {
  CardPanels.init(COLLAPSE_KEY, 'btn-collapse-all');
  // `B.collapsed` is a live view for the console and for tests, not a second
  // copy: CardPanels owns the state and this must never shadow it.
  Object.defineProperty(B, 'collapsed', {
    configurable: true, get: () => CardPanels.state,
  });
}

function setCollapsed(id, on) { CardPanels.set(id, on); }

function toggleAllPanels() { CardPanels.toggleAll(); }

// ---------------------------------------------------------------- the picker
//
// One slot, one window: search, the chips that apply to that slot, and a list of
// ITEMS with their COLOURS underneath. Arrowing previews on the character behind
// it, so choosing is looking rather than reading ids.

function openPicker(slot) {
  if (B.mode === 'model') return;      // no slots to fill on a monster
  const info = B.slotById[slot];
  if (!info || !info.usable) {
    if (info) toast(info.reason, 5000);
    return;
  }
  showLookFor(slot);
  const p = B.picker;
  p.slot = slot;
  p.open = true;
  p.sel = {};
  p.tags = new Set();
  p.index = -1;
  p.variant = 0;
  p.before = B.loadout[slot] ? { ...B.loadout[slot] } : null;
  $('#picker').classList.remove('hidden');
  $('#picker-title').textContent =
    (B.loadout[slot] ? 'Change ' : 'Choose ') + info.label.toLowerCase();
  const sub = [];
  if (info.bodySpecific && B.bodyType) {
    const bt = (B.config.bodyTypes || []).find(b => b.id === B.bodyType);
    sub.push(`only art made for your ${bt ? bt.label.toLowerCase() : B.bodyType} body`);
  } else {
    sub.push('fits any body');
  }
  if (info.reason) sub.push(info.reason);
  $('#picker-sub').textContent = sub.join(' · ');
  $('#picker-search').value = '';
  loadPickerOptions();
  setTimeout(() => $('#picker-search').focus(), 30);
}

function closePicker() {
  B.picker.open = false;
  B.picker.slot = null;
  $('#picker').classList.add('hidden');
}

/** Esc = close, keeping whatever is currently previewed. There is no way to
 *  end up with something you did not see on the character. */
function cancelPicker() { closePicker(); }

function pickerQuery() {
  const p = new URLSearchParams({ slot: B.picker.slot, limit: '600' });
  if (B.loadout.body) p.set('body', B.loadout.body.id);
  const q = $('#picker-search').value.trim();
  if (q) p.set('q', q);
  for (const [axis, set] of Object.entries(B.picker.sel)) {
    if (set && set.size) p.set(axis, [...set].join(','));
  }
  if (B.picker.tags.has(' untagged')) p.set('untagged', '1');
  const tags = [...B.picker.tags].filter(t => t !== ' untagged');
  if (tags.length) p.set('tag', tags.join(','));
  return p;
}

async function loadPickerOptions() {
  const list = $('#picker-list');
  list.innerHTML = '<div class="mut small" style="padding:12px">loading…</div>';
  let data;
  try { data = await api('/api/options?' + pickerQuery().toString()); }
  catch (e) { list.innerHTML = ''; list.appendChild(el('div', 'err', e.message)); return; }
  B.picker.data = data;
  renderPickerFacets(data);
  list.innerHTML = '';
  const items = [];
  for (const g of data.garments) {
    const built = garmentRow(g, items.length);
    list.appendChild(built.node);
    items.push(built.item);
  }
  if (!data.garments.length) {
    list.appendChild(el('div', 'mut small',
      'nothing matches — clear the search or a chip'));
  }
  B.picker.items = items;
  B.picker.index = -1;
  // keep the currently equipped item selected so the list opens where you are
  const cur = B.loadout[B.picker.slot];
  if (cur) {
    const gi = items.findIndex(it => it.variants.some(v => v.id === cur.id));
    if (gi >= 0) {
      const vi = Math.max(0, items[gi].variants.findIndex(v => v.id === cur.id));
      highlight(gi, vi);
    }
  }
  $('#picker-count').textContent =
    `${data.garmentTotal} item${data.garmentTotal === 1 ? '' : 's'} · ` +
    `${data.total} colour variant${data.total === 1 ? '' : 's'}` +
    (data.pool !== data.total ? ` (of ${data.pool} that fit)` : '');
}

function renderPickerFacets(data) {
  const host = $('#picker-facets');
  host.innerHTML = '';
  const order = ['kind', 'type', 'class', 'quality', 'gender', 'size'];
  const axes = order.filter(a => data.facets[a]);
  for (const axis of axes) {
    const counts = data.facets[axis];
    const box = el('div', 'facet-axis');
    box.appendChild(el('div', 'axis-name', data.facetLabels[axis] || axis));
    const pref = data.facetOrder[axis] || [];
    const keys = [...pref.filter(k => k in counts),
                  ...Object.keys(counts).filter(k => !pref.includes(k)).sort()];
    for (const v of keys) {
      const set = B.picker.sel[axis] || (B.picker.sel[axis] = new Set());
      const on = set.has(v);
      box.appendChild(chip(lbl(v), counts[v], on, () => {
        on ? set.delete(v) : set.add(v);
        loadPickerOptions();
      }));
    }
    host.appendChild(box);
  }
  const tc = data.tagCounts || {};
  if (Object.keys(tc).length || B.picker.tags.size) {
    const box = el('div', 'facet-axis');
    box.appendChild(el('div', 'axis-name', 'Your tags'));
    for (const [t, n] of Object.entries(tc)) {
      const on = B.picker.tags.has(t);
      box.appendChild(chip(t, n, on, () => {
        on ? B.picker.tags.delete(t) : B.picker.tags.add(t);
        loadPickerOptions();
      }, 'tag'));
    }
    const U = ' untagged';
    const onU = B.picker.tags.has(U);
    box.appendChild(chip('untagged', null, onU, () => {
      onU ? B.picker.tags.delete(U) : B.picker.tags.add(U);
      loadPickerOptions();
    }, 'tag'));
    host.appendChild(box);
  }
  if (!axes.length && !Object.keys(tc).length) {
    host.appendChild(el('div', 'small mut',
      'no filters apply to this slot — everything here fits'));
  }
}

/** One item: the mesh, with its colours as swatches underneath. */
function garmentRow(g, index) {
  const wrap = el('div');
  const row = el('div', 'row-item');
  const img = setThumb(el('img'), g.mesh || g.texture, 48);
  const lb = el('div', 'lbl');
  lb.appendChild(el('b', null, g.name || g.meshName));
  const bits = [];
  if (g.detail) bits.push(g.detail);
  bits.push(`${g.count} colour${g.count === 1 ? '' : 's'}`);
  lb.appendChild(el('span', null, bits.join(' · ')));
  row.append(img, lb);
  const strip = el('div', 'variants');
  g.variants.forEach((v, vi) => {
    const s = el('img');
    s.loading = 'lazy';
    s.title = `${v.id}${v.detail ? '\n' + v.detail : ''}` +
              (v.tags && v.tags.length ? '\n🏷 ' + v.tags.join(', ') : '');
    if (v.texture) s.src = texUrl(v.texture, { size: 40 });
    s.addEventListener('click', ev => { ev.stopPropagation(); highlight(index, vi); });
    strip.appendChild(s);
  });
  row.addEventListener('click', () => highlight(index, 0));
  wrap.append(row, strip);
  return { node: wrap, item: { el: row, stripEl: strip, variants: g.variants,
                               mesh: g.mesh, name: g.name } };
}

/** Move the highlight and preview it on the character. Instant highlight,
 *  debounced load -- see the note by requestLoad. */
function highlight(i, variant, { immediate = false } = {}) {
  const items = B.picker.items;
  if (!items.length) return;
  i = Math.max(0, Math.min(items.length - 1, i));
  const it = items[i];
  const v = Math.max(0, Math.min(it.variants.length - 1, variant || 0));
  B.picker.index = i;
  B.picker.variant = v;

  $('#picker-list').querySelectorAll('.row-item.sel')
    .forEach(x => x.classList.remove('sel'));
  it.el.classList.add('sel');
  it.el.scrollIntoView({ block: 'nearest' });
  const imgs = [...it.stripEl.querySelectorAll('img')];
  imgs.forEach((x, k) => x.classList.toggle('sel', k === v));
  if (imgs[v]) imgs[v].scrollIntoView({ block: 'nearest', inline: 'nearest' });

  const chosen = it.variants[v];
  requestLoad(tk => {
    if (!stillCurrent(tk)) return;
    equip(B.picker.slot, chosen.id, { silent: true });
  }, { immediate });
  renderLookPanel(B.picker.slot, it.variants, v,
                  { ...chosen, name: chosen.name || it.name });
}

function pickerMove(delta) {
  const n = B.picker.items.length;
  if (!n) return;
  const cur = B.picker.index;
  let next = cur < 0 ? (delta > 0 ? 0 : n - 1) : cur + delta;
  next = Math.max(0, Math.min(n - 1, next));
  if (next === cur) return;
  highlight(next, 0);
}

function pickerMoveVariant(delta) {
  const it = B.picker.items[B.picker.index];
  if (!it) { pickerMove(delta); return; }
  const n = it.variants.length;
  if (n < 2) { pickerMove(delta); return; }
  highlight(B.picker.index, (B.picker.variant + delta + n) % n);
}

// ---------------------------------------------------------------- colour panel
//
// The second of the two choices: which colour of the item you already picked.
// Works whether or not the picker is open, so recolouring an equipped part
// never means reopening anything.

function renderLookPanel(slot, variants, index, override) {
  const b = $('#look-body');
  b.innerHTML = '';
  const info = B.slotById[slot];
  // While arrowing, the equip is still debounced, so show what is being
  // previewed rather than what was equipped a moment ago.
  const cur = override || B.loadout[slot];
  if (!cur) {
    b.appendChild(el('div', 'mut small', 'pick a slot on the left'));
    return;
  }
  b.appendChild(kv([
    ['slot', info ? info.label : slot],
    ['item', cur.name],
    ['id', cur.id],
    ['mesh', cur.mesh],
    ['texture', cur.texture],
  ]));
  // A weapon's "colours" ARE its qualities, so name them instead of showing
  // the last three digits of an id and hoping.
  if (isWeaponSlot(slot) && cur.id) {
    const qbox = el('div', 'qualitybox');
    b.appendChild(qbox);
    renderQualityInto(qbox, slot, cur.id);
  }
  if (variants && variants.length > 1) {
    b.appendChild(el('div', 'small mut', isWeaponSlot(slot)
      ? `Every id in this family, quality digit and all — ${variants.length} of ` +
        'them over one mesh. The named buttons above are the readable way in; ' +
        'this strip is the raw ladder.'
      : `${variants.length} colours of this item. They share one mesh and differ ` +
        'only in texture — which is why every one of them is a combination the ' +
        'game itself ships.'));
    const strip = el('div', 'relstrip');
    variants.forEach((v, i) => {
      const cell = el('div', 'cell' + (i === index ? ' sel' : ''));
      const img = el('img');
      if (v.texture) img.src = texUrl(v.texture, { size: 46 });
      cell.appendChild(img);
      cell.appendChild(el('div', 'cap', v.quality || v.id.slice(-3)));
      cell.title = `${v.id}${v.detail ? '\n' + v.detail : ''}`;
      cell.addEventListener('click', () => {
        if (B.picker.open && B.picker.slot === slot) {
          highlight(B.picker.index, i, { immediate: true });
        } else {
          equip(slot, v.id);
          renderLookPanel(slot, variants, i);
        }
      });
      strip.appendChild(cell);
    });
    b.appendChild(strip);
  } else {
    b.appendChild(el('div', 'small mut', 'this item ships in one colour only'));
  }
}

// ---------------------------------------------------------------- quality
//
// A weapon appearance id is `TTTSSQ`: type, style, and one QUALITY digit --
// 3-5 Normal, 6 Refined, 7 Unique, 8 Elite, 9 Super. Every id in a family
// shares ONE mesh and differs only in texture, and 3/4/5 share one texture,
// 6/7 a second and 8/9 a third, so five qualities give three looks. Verified
// in ini/weapon.ini and independently in ini/itemtype.json, where 410003…9 are
// one item whose attack rises with the digit (tools/builder.py QUALITY_DIGITS).
//
// This exists because Super is the quality that carries the aura, and hunting
// `410009` out of a list of 5,384 is not a way to find that out.

const WEAPON_SLOTS = ['r_weapon', 'l_weapon', 'shield'];
const isWeaponSlot = s =>
  ((B.config && B.config.weaponSlots) || WEAPON_SLOTS).includes(s);

const qualityKey = (slot, id) => `${slot}:${id}`;
const qualityRec = (slot, id) => B.qualityCache[qualityKey(slot, id)] || null;

async function ensureQuality(slot, id) {
  const k = qualityKey(slot, id);
  if (B.qualityCache[k]) return B.qualityCache[k];
  try {
    B.qualityCache[k] = await api(
      `/api/quality?slot=${encodeURIComponent(slot)}&id=${encodeURIComponent(id)}`);
  } catch (e) {
    B.qualityCache[k] = { isWeapon: false, qualities: [], extra: [],
                          reason: e.message };
  }
  return B.qualityCache[k];
}

/** The quality ladder of whatever is in `slot`, drawn into `host`. Renders
 *  from cache when it can and re-renders itself once the fetch lands, so
 *  arrowing through the picker never blocks on a request. */
function renderQualityInto(host, slot, id) {
  const rec = qualityRec(slot, id);
  host.innerHTML = '';
  if (!rec) {
    host.appendChild(el('div', 'mut small', 'reading the quality ladder…'));
    ensureQuality(slot, id).then(() => {
      if (host.isConnected) renderQualityInto(host, slot, id);
    });
    return;
  }
  if (!rec.isWeapon) return;

  host.appendChild(el('div', 'axis-name', 'Quality'));
  const row = el('div', 'qrow');
  for (const q of rec.qualities) {
    const on = q.available && q.id === id;
    const b = el('button',
      'qbtn' + (on ? ' on' : '') + (q.available ? '' : ' off') +
      (q.aura ? ' aura' : ''));
    b.appendChild(el('b', null, q.label));
    b.appendChild(el('span', 'qid', q.available ? q.id : '—'));
    if (q.aura) b.appendChild(el('span', 'qaura', '✦ aura'));
    if (!q.available) {
      b.disabled = true;
      b.title = q.reason || 'not shipped for this weapon';
    } else {
      b.title = `${q.id} · ${q.texture}` +
        (q.grades && q.grades.length > 1
          ? `\nids …${q.grades.join(', …')} are all ${q.label} and share this texture`
          : '') +
        (q.aura ? '\nthis quality carries the always-on aura' : '');
      b.addEventListener('click', () => setQuality(slot, q));
    }
    row.appendChild(b);
  }
  host.appendChild(row);

  // What actually changed, stated rather than implied.
  const bits = [];
  if (rec.mesh) bits.push(`one mesh (${rec.mesh})`);
  if (rec.textureTiers) {
    bits.push(`${rec.textureTiers} texture${rec.textureTiers === 1 ? '' : 's'}`);
  }
  host.appendChild(el('div', 'small mut', bits.join(' · ')));
  if ((rec.textureGroups || []).length > 1) {
    host.appendChild(el('div', 'small mut', 'sharing a look: ' +
      rec.textureGroups.map(g => g.qualities.join(' + ')).join(' · ')));
  }
  if (!rec.hasLadder && rec.reason) {
    host.appendChild(el('div', 'pending', rec.reason));
  }
  for (const e of rec.extra || []) {
    const c = chip(`…${e.digit} base row · ${e.id}`, null, e.id === id,
                   () => setQuality(slot, e));
    c.title = (B.config && B.config.qualityZeroNote) || rec.zeroNote || '';
    host.appendChild(c);
  }
  host.appendChild(el('div', 'note',
    (B.config && B.config.qualityNote) || rec.note || ''));
}

/** Equip the same weapon at a different quality. The mesh does not change, so
 *  the camera and the pose stay exactly where they were. */
async function setQuality(slot, q) {
  if (!q || !q.id) return;
  await equip(slot, q.id, { silent: true });
  toast(q.quality
    ? `${q.label} — ${q.id}${q.aura ? ' · carries the aura' : ''}`
    : `${q.id}`);
  showLookFor(slot);
}

/** Step the equipped weapon up or down its quality ladder. */
async function stepQuality(delta) {
  const slot = B.loadout.r_weapon ? 'r_weapon'
             : (B.loadout.l_weapon ? 'l_weapon' : null);
  if (!slot) { toast('no weapon equipped'); return; }
  const id = B.loadout[slot].id;
  const rec = await ensureQuality(slot, id);
  const avail = (rec.qualities || []).filter(q => q.available);
  if (avail.length < 2) {
    toast(rec.reason || 'this weapon has no quality ladder', 4000);
    return;
  }
  let i = avail.findIndex(q => q.id === id);
  if (i < 0) i = delta > 0 ? -1 : avail.length;
  const next = avail[Math.max(0, Math.min(avail.length - 1, i + delta))];
  if (!next || next.id === id) return;
  await setQuality(slot, next);
}

/** Refresh the colour panel for whatever is equipped in a slot. */
async function showLookFor(slot) {
  const cur = B.loadout[slot];
  if (!cur) { renderLookPanel(slot, null, 0); return; }
  let rec = null;
  try {
    rec = await api(`/api/option?slot=${encodeURIComponent(slot)}` +
                    `&id=${encodeURIComponent(cur.id)}`);
  } catch (e) { /* fall through */ }
  renderLookPanel(slot, rec && rec.variants, rec ? rec.variantIndex : 0);
}

// ---------------------------------------------------------------- the figure
async function rebuild({ reframe = false } = {}) {
  if (B.mode === 'model') return rebuildModel({ reframe });
  if (!B.loadout.body) {
    if (viewer) { viewer.clear(); viewer.draw(); }
    $('#gl-msg').textContent = 'choose a body to start';
    $('#gl-msg').classList.remove('hidden');
    return;
  }
  const tk = tokenNow();
  const p = loadoutParams();
  p.set('bodyTable', 'body');
  p.set('action', B.anim.action);
  p.set('frame', String(B.anim.frame | 0));
  $('#gl-msg').textContent = 'building…';
  $('#gl-msg').classList.remove('hidden');
  let fig;
  try { fig = await api('/api/figure?' + p.toString()); }
  catch (e) {
    $('#gl-msg').textContent = 'could not build this character: ' + e.message;
    return;
  }
  if (!stillCurrent(tk)) return;
  B.figure = fig;
  B.anchors = fig.anchors;

  const defs = [];
  for (const m of fig.body.scene.meshes) defs.push({ meta: m, textureKey: 'body' });
  for (const part of fig.parts) {
    for (const m of part.scene.meshes) {
      defs.push({ meta: m, textureKey: 'slot:' + part.slot,
                  translate: part.translate, matrix: part.matrix,
                  slot: part.slot, socket: part.socket });
    }
  }
  viewer.setMeshes(defs, { frameOn: fig.bodyBounds, keepFraming: !reframe });
  await applyNamedTexture('body', fig.body.texture);
  for (const part of fig.parts) {
    await applyNamedTexture('slot:' + part.slot, part.texture);
  }
  if (!stillCurrent(tk)) return;
  $('#gl-msg').classList.add('hidden');
  $('#gl-stats').textContent = viewer.stats +
    `\n${fig.body.id}` + fig.parts.map(p => ` + ${p.slot}:${p.id}`).join('');
  renderDetailPanel(fig);
  // the weapon is part of the motion lookup, so the action list is re-derived
  await fillActions();
  await ensureAnim({ silent: true });
  await refreshWeaponPanel();
  if (B.superfx.on) await applySuperFx();
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
    img.src = texUrl(texPath);
  });
}

function renderDetailPanel(fig) {
  const b = $('#detail-body');
  b.innerHTML = '';
  b.appendChild(kv([
    ['character', fig.body.id],
    ['body mesh', fig.body.mesh],
    ['pose', fig.body.pose],
    ['parts', fig.parts.length],
    ['attachment', fig.attachConfidence],
  ]));
  for (const w of fig.warnings || []) b.appendChild(el('div', 'warn', '⚠ ' + w));
  const bodyH = fig.bodyBounds ? (fig.bodyBounds.max[2] - fig.bodyBounds.min[2]) : null;
  const ul = el('ul', 'chunks');
  for (const p of fig.parts) {
    const li = el('li');
    li.appendChild(el('b', null, `${p.label}: ${p.id}`));
    li.appendChild(el('div', 'flags',
      `socket ${p.socket} · ${p.anchorSource} [${p.anchorConfidence}]`));
    if (p.bboxRender && bodyH) {
      const r = p.bboxRender.longest / bodyH;
      li.appendChild(el('div', 'flags' + (r > 1.6 ? ' warn' : ''),
        `${p.bboxRender.longest.toFixed(1)} units = ${r.toFixed(2)}× the ` +
        `body's ${bodyH.toFixed(1)}`));
    }
    for (const s of (p.attachChain && p.attachChain.stages) || []) {
      const d = s.decomposed;
      li.appendChild(el('div', 'flags mut',
        `${s.name}: ${s.applied}` +
        (d ? ` · scale ${d.scale.map(v => v.toFixed(3)).join('/')}` : '')));
    }
    ul.appendChild(li);
  }
  b.appendChild(ul);
  b.appendChild(el('div', 'note', fig.attachNote));
}

// ---------------------------------------------------------------- animation
//
// The POSES are the game's own: ini/3dmotion.ini keyed
// <bodyDigit><weaponType><action>, evaluated per frame by the server. What is
// NOT here is the frame rate and the root-motion curve, which live in
// ini/ActionCtrl.ini and are tools/anim.py's (task #20). The panel says so, and
// the moment anim.py lands the server answers with its numbers instead of the
// placeholder without a line changing here.

function renderAnimPanel() {
  const b = $('#anim-body');
  b.innerHTML = '';
  const d = B.anim.data;
  const rows = [
    ['action', d ? `${d.label} (${d.action})` : B.anim.action],
    ['frames', d ? d.frames : '—'],
    ['motion file', d ? d.motion : ''],
    ['weapon set', d ? d.weaponset : ''],
    ['loop', d ? d.loop : ''],
    ['speed', `${B.anim.speed} ms/frame`],
    ['evidence', d ? `${d.confidence}` : ''],
  ];
  if (d && d.chain) rows.push(['chains with', `action ${d.chain}`]);
  b.appendChild(kv(rows));
  if (d && d.evidence) b.appendChild(el('div', 'small mut', d.evidence));
  if (d && d.error) b.appendChild(el('div', 'warn', '⚠ ' + d.error));
  if (d && d.chain) {
    b.appendChild(el('div', 'note',
      `Played as a pair: ${d.action} and ${d.chain} are the two halves of the ` +
      `stride (runL.wav / runR.wav), and chaining them reads better than ` +
      `looping either on its own.`));
  }
  if (d && d.rootMotion) {
    b.appendChild(el('div', 'note',
      d.rootMotion.riseUp > 5
        ? `This clip lifts the body ${d.rootMotion.riseUp.toFixed(1)} units — ` +
          `the arc is baked into the poses, so it plays as authored.`
        : `Root translation ${d.rootMotion.maxXY.toFixed(2)} — an in-place ` +
          `cycle. The client moves the character across the map, so running ` +
          `on the spot here is correct rather than a limitation.`));
  }
  b.appendChild(el('div', 'note', (B.config && B.config.animNote) || ''));
  if (!(B.config && B.config.animAvailable)) {
    b.appendChild(el('div', 'pending',
      'tools/anim.py is not importable, so no action can be resolved.'));
  }
  b.appendChild(el('div', 'small mut',
    (B.anim.meta && B.anim.meta.missingNote) || ''));
}


function animParams(action) {
  const p = new URLSearchParams({ body: B.loadout.body.id, action });
  if (B.loadout.r_weapon) p.set('weapon', B.loadout.r_weapon.id);
  if (B.loadout.l_weapon) p.set('offHand', B.loadout.l_weapon.id);
  return p;
}

async function fetchClip(action) {
  if (B.mode === 'model') return fetchModelClip(action);
  const key = `${B.loadout.body.id}:${action}:` +
              `${(B.loadout.r_weapon || {}).id || ''}:` +
              `${(B.loadout.l_weapon || {}).id || ''}`;
  if (B.anim.cache[key]) return B.anim.cache[key];
  const d = await api('/api/anim?' + animParams(action).toString());
  B.anim.cache[key] = d;
  return d;
}

/** Load the clip and, when it chains, its partner. `120` and `121` are the two
 *  halves of a running stride — runL.wav and runR.wav — and chaining them beats
 *  looping either on its own, so playback walks the pair. */
async function ensureAnim({ silent = false } = {}) {
  if (B.mode === 'model') { if (!B.model.key) return null; }
  else if (!B.loadout.body) return null;
  if (!silent) $('#anim-label').textContent = 'loading motion…';
  let seq = [];
  try {
    const first = await fetchClip(B.mode === 'model' ? B.model.action
                                                     : B.anim.action);
    seq = [first];
    const seen = new Set([first.action]);
    let next = first.chain;
    while (next && !seen.has(next)) {
      seen.add(next);
      const c = await fetchClip(next);
      if (!c || !c.frames) break;
      seq.push(c);
      next = c.chain;
    }
  } catch (e) { $('#anim-label').textContent = 'motion unavailable'; return null; }
  B.anim.seq = seq.filter(c => c.frames);
  B.anim.data = B.anim.seq[0] || seq[0];
  B.anim.seqAt = 0;
  syncAnimUi();
  renderAnimPanel();
  return B.anim.data;
}

function syncAnimUi() {
  const d = B.anim.data;
  const s = $('#anim-frame');
  const n = d && d.frames ? d.frames : 1;
  s.max = String(Math.max(0, n - 1));
  s.value = String(Math.min(B.anim.frame, n - 1));
  $('#anim-label').textContent = d && d.frames
    ? `${(+s.value) + 1} / ${n} · ${B.anim.speed} ms` : 'not in this install';
  $('#btn-anim-play').disabled = !(d && d.frames > 1);
}

function showAnimFrame(f) {
  const d = B.anim.data;
  if (!d || !d.frames || !viewer) return;
  f = ((f % d.frames) + d.frames) % d.frames;
  B.anim.frame = f;
  const positions = {};
  for (const ch of d.chunks) positions[ch.index] = ch.frames[f];
  viewer.setPose(positions, d.sockets[f] || {});
  if (B.superfx.on) placeSuperFx(d.sockets[f] || {});
  viewer.draw();
  $('#anim-frame').value = String(f);
  const part = B.anim.seq.length > 1
    ? ` · clip ${B.anim.seqAt + 1}/${B.anim.seq.length} (${d.action})` : '';
  $('#anim-label').textContent =
    `${f + 1} / ${d.frames} · ${B.anim.speed} ms${part}`;
  try {
    localStorage.setItem(ANIM_KEY, JSON.stringify(
      { action: B.anim.action, speed: B.anim.speed }));
  } catch (e) { /* ignore */ }
}

function animPlay() {
  if (!B.anim.seq.length || !B.anim.data || B.anim.data.frames < 2) return;
  B.anim.playing = true;
  $('#btn-anim-play').textContent = '❚❚ pause';
  B.anim.t0 = performance.now() - B.anim.frame * B.anim.speed;
  const step = () => {
    if (!B.anim.playing) return;
    const cur = B.anim.data;
    if (!cur || !cur.frames) { animPause(); return; }
    const f = Math.floor((performance.now() - B.anim.t0) / B.anim.speed);
    if (f >= cur.frames) {
      if (B.anim.seq.length > 1) {
        // chained: hand over to the other half of the stride
        B.anim.seqAt = (B.anim.seqAt + 1) % B.anim.seq.length;
        B.anim.data = B.anim.seq[B.anim.seqAt];
        syncAnimUi();
        B.anim.t0 = performance.now();
        showAnimFrame(0);
      } else if (cur.loop !== 'oneshot') {
        B.anim.t0 = performance.now();
        showAnimFrame(0);
      } else {
        showAnimFrame(cur.frames - 1);
        animPause();
        return;
      }
    } else {
      showAnimFrame(f);
    }
    B.anim.raf = requestAnimationFrame(step);
  };
  cancelAnimationFrame(B.anim.raf);
  B.anim.raf = requestAnimationFrame(step);
}

function animPause() {
  B.anim.playing = false;
  cancelAnimationFrame(B.anim.raf);
  $('#btn-anim-play').textContent = '▶ play';
}

// ---------------------------------------------------------------- weapon fx
//
// The user's complaint was that the super weapon effect drew "off in space".
// tools/superfx.py settled why: the effect is a SIBLING of the weapon at
// v_r_weapon, not its child, so it must NOT inherit the weapon's own bone-0
// matrix. Composing that as well shrinks a 410009 glow from a span of 111.8 to
// 28.1 — buried inside the grip. The anchor comes back from the server as
// M_offset x M_socket, already in render space, and rides the same socket the
// weapon does.

/** The equipped weapon and which hand it is in, right hand first. */
function equippedWeapon() {
  if (B.loadout.r_weapon) return { slot: 'r_weapon', v: B.loadout.r_weapon };
  if (B.loadout.l_weapon) return { slot: 'l_weapon', v: B.loadout.l_weapon };
  return null;
}

const hasAura = () => !!(B.superfx.rec && B.superfx.rec.found);

/** Why the aura toggle is greyed, in this weapon's own terms. Never a bare
 *  "disabled": the whole point of the quality selector is that the answer is
 *  "you are not wearing the Super one", and it names the id that is. */
function auraWhyNot() {
  const w = equippedWeapon();
  if (!w) return 'Equip a weapon and its aura, if it has one, toggles here.';
  const rec = qualityRec(w.slot, w.v.id);
  const glowing = rec ? (rec.qualities || []).filter(q => q.available && q.aura)
                      : [];
  if (glowing.length) {
    return `${w.v.name} carries no aura at this quality. ` +
           glowing.map(q => `${q.label} (${q.id})`).join(' and ') +
           ` does — switch quality and it lights up.`;
  }
  return `No id in this weapon's family carries an always-on aura. ` +
         `796 of the 5,384 weapon appearances do, overwhelmingly the Super ` +
         `(…9) ones, so an empty answer here is the table rather than a ` +
         `lookup failure.`;
}

/** One toggle, two places: a checkbox in the viewport bar (always in sight)
 *  and the switch in the Weapon effects card. Both drive the same state. */
function setAura(on) {
  B.superfx.on = !!on;
  try { localStorage.setItem(AURA_KEY, B.superfx.on ? '1' : '0'); }
  catch (e) { /* ignore */ }
  applySuperFx();
  syncAuraControl();
  refreshWeaponPanel();
}

function toggleAura() {
  if (!hasAura()) { toast(auraWhyNot(), 5000); syncAuraControl(); return; }
  setAura(!B.superfx.on);
}

function syncAuraControl() {
  const lab = $('#aura-label');
  const box = $('#chk-aura');
  if (!lab || !box) return;
  const w = equippedWeapon();
  lab.classList.toggle('hidden', !w);
  const ok = hasAura();
  box.disabled = !ok;
  box.checked = ok && B.superfx.on;
  lab.classList.toggle('on', ok && B.superfx.on);
  lab.classList.toggle('nofx', !ok);
  lab.title = ok
    ? (B.superfx.on
        ? `The ${B.superfx.rec.name} aura is on the weapon. (A)`
        : `This weapon carries the ${B.superfx.rec.name} aura — tick to show it. (A)`)
    : auraWhyNot();
}

async function refreshWeaponPanel() {
  const b = $('#fx-body');
  const w = equippedWeapon();
  b.innerHTML = '';
  if (!w) {
    b.appendChild(el('div', 'mut small', 'equip a weapon to see its effect'));
    B.superfx.rec = null;
    // The *intent* to show the aura is kept: re-equipping a Super weapon
    // brings it straight back rather than making you find the switch again.
    applySuperFx();
    syncAuraControl();
    return;
  }
  const slot = w.slot;
  const tk = tokenNow();
  b.appendChild(el('div', 'mut small', 'resolving…'));
  let sfx = null;
  const p = new URLSearchParams({ id: w.v.id, slot, action: B.anim.action,
                                  frame: String(B.anim.frame | 0) });
  if (B.loadout.body) p.set('body', B.loadout.body.id);
  try { sfx = await api('/api/superfx?' + p.toString()); }
  catch (e) { /* non-fatal */ }
  if (!stillCurrent(tk)) return;
  B.superfx.rec = sfx;
  B.superfx.slot = slot;
  const qrec = await ensureQuality(slot, w.v.id);
  if (!stillCurrent(tk)) return;

  b.innerHTML = '';
  const qname = qrec.current
    ? (B.config.qualityLabels || {})[qrec.current] || qrec.current : '';
  b.appendChild(kv([
    ['weapon', `${w.v.name} (${w.v.id})`],
    ['quality', qname || 'not a quality id'],
  ]));

  // ---- the switch, whatever the answer is
  const sw = el('label', 'bigtoggle' + (hasAura() ? '' : ' nofx'));
  const cb = el('input');
  cb.type = 'checkbox';
  cb.checked = hasAura() && B.superfx.on;
  cb.disabled = !hasAura();
  cb.addEventListener('change', () => setAura(cb.checked));
  sw.appendChild(cb);
  sw.appendChild(el('b', null, 'Super aura'));
  sw.appendChild(el('span', 'small mut',
    hasAura() ? (B.superfx.on ? 'showing on the weapon' : 'this weapon has one')
              : 'not on this quality'));
  b.appendChild(sw);

  if (sfx && sfx.found) {
    b.appendChild(kv([
      ['effect', sfx.name],
      ['family', sfx.family],
      ['layers', sfx.layers.length],
      ['timing', `${sfx.frameIntervalMs} ms/frame, ` +
                 (sfx.endless ? 'endless' : `${sfx.loopTime}x`)],
    ]));
    b.appendChild(el('div', 'note',
      (B.config && B.config.superAnchorNote) || ''));
    if (sfx.anchorSource) b.appendChild(el('div', 'small mut', sfx.anchorSource));
    const strip = el('div', 'relstrip');
    for (const L of sfx.layers) {
      if (L.texture) strip.appendChild(fxCell(L.texture, `L${L.index}`));
    }
    b.appendChild(strip);
  } else {
    // Say WHY there is nothing, and offer the one click that fixes it.
    b.appendChild(el('div', 'pending', auraWhyNot()));
    for (const q of (qrec.qualities || [])) {
      if (!q.available || !q.aura) continue;
      const jump = el('button', 'primary');
      jump.textContent = `Switch to ${q.label} (${q.id})`;
      jump.title = 'same weapon, same mesh — only the quality digit changes';
      jump.addEventListener('click', async () => {
        await setQuality(slot, q);
        setAura(true);
      });
      b.appendChild(jump);
    }
    if (sfx && sfx.note) b.appendChild(el('div', 'small mut', sfx.note));
  }
  b.appendChild(el('div', 'note',
    (B.config && B.config.auraQualityNote) || ''));

  // the per-attack trail and the impact spark are a different thing and the
  // browser page already resolves all three
  const a = el('a', 'small');
  a.href = '/#' + loadoutParams().toString();
  a.textContent = 'attack trail and impact spark → asset browser';
  b.appendChild(a);
  syncAuraControl();
}

function fxCell(path, label) {
  const cell = el('div', 'cell');
  const img = setThumb(el('img'), path, 46);
  cell.appendChild(img);
  cell.appendChild(el('div', 'cap', label));
  cell.title = path;
  return cell;
}

/** Attach (or detach) the weapon effect through fx.js -- the same playback the
 *  asset browser uses, so it is the tested path rather than a second one. */
async function applySuperFx() {
  if (!viewer) return;
  const rec = B.superfx.rec;
  if (!B.superfx.on || !rec || !rec.found || !rec.effect) {
    viewer.clearEffects();
    $('#fx-wrap').classList.add('hidden');
    cancelAnimationFrame(B.superfx.raf);
    viewer.draw();
    return;
  }
  const keys = {};
  for (const lay of rec.effect.layers || []) {
    if (lay.texture) keys[lay.index] = `sfx:${lay.index}`;
  }
  B.superfx.anchor = (rec.anchor && rec.anchor.length === 16)
    ? Array.from(rec.anchor) : FX.IDENT.slice();
  const n = viewer.setEffects([{ def: rec.effect, role: 'aura',
                                 anchor: B.superfx.anchor, textureKeys: keys }]);
  for (const lay of rec.effect.layers || []) {
    if (lay.texture) await applyNamedTexture(`sfx:${lay.index}`, lay.texture);
  }
  // setEffects() clears the texture map, so the figure's skins go back on
  if (B.figure) {
    await applyNamedTexture('body', B.figure.body.texture);
    for (const p of B.figure.parts) await applyNamedTexture('slot:' + p.slot, p.texture);
  }
  $('#fx-wrap').classList.toggle('hidden', !n);
  if (n) fxLoop();
}

/** Follow the hand: during playback the socket matrix changes every frame, and
 *  the effect is anchored to the socket, so it has to move with it. */
function placeSuperFx(sockets) {
  const rec = B.superfx.rec;
  if (!rec || !rec.found) return;
  const dumy = B.superfx.slot === 'l_weapon' ? 'v_l_weapon' : 'v_r_weapon';
  const m = sockets && sockets[dumy];
  if (!(m && m.length === 16)) return;
  B.superfx.anchor = Array.from(m);
  // Push it straight into the live instances. The effect's own rAF supplies
  // the anchor on every tick, but it is not running while the animation is
  // paused or being scrubbed — and a glow that only tracks the hand while
  // *both* loops happen to be running is exactly the class of bug this panel
  // exists to have fixed.
  for (const f of (viewer && viewer.fx) || []) {
    if (f.anchor && f.anchor.length === 16) f.anchor.set(B.superfx.anchor);
  }
}

function fxLoop() {
  cancelAnimationFrame(B.superfx.raf);
  const t0 = performance.now();
  const rec = B.superfx.rec;
  const dur = Math.max(400, (rec.effect.effectiveFrames || rec.effect.frames || 1) *
                            (rec.effect.frameIntervalMs || 41));
  const step = () => {
    if (!B.superfx.on || !viewer.fx.length) return;
    let t = performance.now() - t0;
    if (t > dur) { viewer.resetEffects(); }
    viewer.setEffectTime(t % dur, () => B.superfx.anchor);
    $('#fx-label').textContent =
      `${Math.round(t % dur)} / ${Math.round(dur)} ms · ${rec.name}`;
    $('#fx-slider').max = String(Math.round(dur));
    $('#fx-slider').value = String(Math.round(t % dur));
    viewer.draw();
    B.superfx.raf = requestAnimationFrame(step);
  };
  B.superfx.raf = requestAnimationFrame(step);
}

// ================================================================= MODEL MODE
//
// Monsters, NPCs, ghosts, the one shipped mount family, the eight
// character-select roles and the effect library. `tools/models.py` builds the
// catalogue; this draws it and reuses the stage, the action dropdown and the
// playback loop, because those parts of the job really are the same.
//
// The one thing that is NOT the same, and the reason this is a mode rather
// than a slot: **the mesh changes with the action.** 69 of monster 103's 80
// action codes resolve to a file that carries its own geometry, so switching
// action reloads the model. Where a family ships a shared skeleton
// (c3/monster/198/1.c3) the motion-only action files bind over it by ordinal
// instead, exactly as a player body does. The server says which, per action.

const MODEL_KEY = 'cobuilder.model';

function restoreModelState() {
  try {
    const s = JSON.parse(localStorage.getItem(MODEL_KEY) || 'null');
    if (s && typeof s === 'object') {
      if (s.mode === 'model' || s.mode === 'character') B.mode = s.mode;
      B.model.key = s.key || '';
      B.model.action = s.action || '';
      B.model.kind = s.kind || '';
      B.model.q = s.q || '';
      B.model.zoom = +s.zoom || 100;
      B.model.row = s.row || null;
      if (s.distinctOnly !== undefined) B.model.distinctOnly = !!s.distinctOnly;
    }
  } catch (e) { /* ignore */ }
  // A link that names a model wins over whatever was last open.
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (h.get('model')) {
    B.mode = 'model';
    B.model.key = h.get('model');
    if (h.get('action')) B.model.action = h.get('action');
    if (h.get('zoom')) B.model.zoom = +h.get('zoom') || 100;
  }
}

function saveModelState() {
  try {
    localStorage.setItem(MODEL_KEY, JSON.stringify({
      mode: B.mode, key: B.model.key, action: B.model.action,
      kind: B.model.kind, q: B.model.q, zoom: B.model.zoom,
      row: B.model.row, distinctOnly: B.model.distinctOnly,
    }));
  } catch (e) { /* ignore */ }
}

/** Show the rails and cards this mode owns, and hide the other mode's.
 *  Nothing is destroyed: switching back finds the character exactly as it was,
 *  because the loadout and the model selection are separate state. */
function applyMode({ rebuild: doRebuild = true } = {}) {
  const model = B.mode === 'model';
  $('#mode-character').classList.toggle('on', !model);
  $('#mode-model').classList.toggle('on', model);
  $('#mode-character').setAttribute('aria-selected', String(!model));
  $('#mode-model').setAttribute('aria-selected', String(model));
  $('#slots-rail').classList.toggle('hidden', model);
  $('#model-rail').classList.toggle('hidden', !model);
  $('#card-model').classList.toggle('hidden', !model);
  $('#card-look').classList.toggle('hidden', model);
  $('#card-fx').classList.toggle('hidden', model);
  $('#clips-label').classList.toggle('hidden', !model);
  // `B.model.kind` is the FILTER chip; the selected model's own kind lives on
  // its payload. Conflating the two is how a monster panel ends up on a ghost.
  $('#card-monster').classList.toggle(
    'hidden', !(model && B.model.data && B.model.data.kind === 'monster'));
  if (model) {
    $('#aura-label').classList.add('hidden');
    if (B.superfx.rec) { B.superfx.rec = null; viewer && viewer.clearEffects(); }
  }
  document.body.classList.toggle('mode-model', model);
  saveModelState();
  if (doRebuild) rebuild({ reframe: true });
}

async function setMode(mode) {
  if (mode === B.mode) return;
  animPause();
  stopModelEffect();
  B.mode = mode;
  B.anim.cache = {};
  applyMode({ rebuild: false });
  if (mode === 'model' && !B.model.list.length) {
    await loadModelList({ select: B.model.key });
  }
  await fillActions();
  await rebuild({ reframe: true });
  if (mode === 'character') showLookFor('body');
}

// ---------------------------------------------------------------- the list
async function loadModelList({ select = '' } = {}) {
  const host = $('#model-list');
  host.innerHTML = '<div class="mut small" style="padding:12px">loading…</div>';
  const p = new URLSearchParams({ limit: '600' });
  if (B.model.kind) p.set('kind', B.model.kind);
  if (B.model.q) p.set('q', B.model.q);
  let data;
  try { data = await api('/api/models?' + p.toString()); }
  catch (e) { host.innerHTML = ''; host.appendChild(el('div', 'err', e.message)); return; }
  B.model.meta = data;
  B.model.kinds = data.kinds;
  B.model.list = data.models;
  renderModelKinds(data);
  renderModelList();
  const want = select || B.model.key;
  const i = data.models.findIndex(m => m.key === want);
  if (i >= 0) highlightModel(i, { load: false });
  else if (data.models.length && !B.model.key) highlightModel(0, { load: false });
  $('#model-count').textContent =
    `${data.total} model${data.total === 1 ? '' : 's'} shown of ${data.pool}` +
    (data.models.length < data.total ? ` (first ${data.models.length})` : '');
}

function renderModelKinds(data) {
  const host = $('#model-kinds');
  host.innerHTML = '';
  host.appendChild(el('div', 'axis-name', 'Kind'));
  const counts = data.kindCounts || {};
  host.appendChild(chip('All', Object.values(counts).reduce((a, b) => a + b, 0),
                        !B.model.kind, () => { B.model.kind = ''; loadModelList(); }));
  for (const k of data.kinds) {
    if (!counts[k.kind] && B.model.kind !== k.kind) continue;
    const c = chip(k.label, counts[k.kind] || 0, B.model.kind === k.kind,
                   () => { B.model.kind = k.kind; loadModelList(); });
    c.title = k.description;
    host.appendChild(c);
  }
}

function renderModelList() {
  const host = $('#model-list');
  host.innerHTML = '';
  if (!B.model.list.length) {
    host.appendChild(el('div', 'mut small',
      'nothing matches — clear the search or the kind chip'));
    return;
  }
  B.model.list.forEach((m, i) => {
    const row = el('div', 'row-item');
    row.dataset.key = m.key;
    row.appendChild(setThumb(el('img'), m.thumb, 48));
    const lb = el('div', 'lbl');
    lb.appendChild(el('b', null, m.label || m.key));
    const bits = [];
    if (m.kind === 'effect') bits.push('effect');
    else {
      bits.push(`${m.clips} clip${m.clips === 1 ? '' : 's'}`);
      bits.push(`${m.playable} action${m.playable === 1 ? '' : 's'}`);
    }
    if (m.detail) bits.push(m.detail);
    lb.appendChild(el('span', null, bits.join(' · ')));
    lb.appendChild(el('span', 'kindline',
      m.kind === 'effect' ? 'c3/effect' : (m.dir || m.kind)));
    if (m.tags && m.tags.length) {
      lb.appendChild(el('span', 'tagline', '🏷 ' + m.tags.join(', ')));
    }
    row.appendChild(lb);
    row.addEventListener('click', () => highlightModel(i));
    host.appendChild(row);
  });
  markModelSelection();
}

function markModelSelection() {
  const host = $('#model-list');
  host.querySelectorAll('.row-item').forEach(r =>
    r.classList.toggle('sel', r.dataset.key === B.model.key));
}

/** Move the selection, previewing on the stage. Same 130 ms debounce and load
 *  token as everything else here, so holding an arrow key issues one request
 *  for the row you stopped on. */
function highlightModel(i, { load = true, immediate = false } = {}) {
  const list = B.model.list;
  if (!list.length) return;
  i = Math.max(0, Math.min(list.length - 1, i));
  const m = list[i];
  B.model.index = i;
  if (m.key !== B.model.key) {
    B.model.key = m.key;
    B.model.data = null;
    B.model.action = '';
    B.model.row = null;
    B.model.zoom = 100;
  }
  markModelSelection();
  const row = $('#model-list').querySelector(`.row-item[data-key="${cssEsc(m.key)}"]`);
  if (row) row.scrollIntoView({ block: 'nearest' });
  saveModelState();
  if (!load) { renderModelPanel(); return; }
  requestLoad(tk => {
    if (!stillCurrent(tk)) return;
    rebuild({ reframe: true });
  }, { immediate });
}

const cssEsc = s => (window.CSS && CSS.escape) ? CSS.escape(s)
                                               : String(s).replace(/"/g, '\\"');

function modelMove(delta) {
  const n = B.model.list.length;
  if (!n) return;
  const cur = B.model.index;
  highlightModel(cur < 0 ? (delta > 0 ? 0 : n - 1) : cur + delta);
}

// ---------------------------------------------------------------- actions
/** The action dropdown for a model.
 *
 *  The vocabulary is the SAME as a player's — 100 idle, 110 walk, 401 swing
 *  (docs/animation.md §3) — so the names, groups and evidence come from
 *  anim.ACTIONS through the server rather than from a second table here.
 *
 *  What is different is the alias density: ini/3dmotion.ini declares 80 codes
 *  for monster 103 and they land on 16 files. "distinct clips only" hides the
 *  duplicates; unticking it shows every code the ini declares, each labelled
 *  with the code it repeats.
 */
function fillModelActions() {
  const sel = $('#anim-action');
  const d = B.model.data;
  sel.innerHTML = '';
  if (!d || !d.actionList) return;
  // Whatever is selected always stays in the list, alias or not — a filter
  // that silently moves you off the thing you chose is worse than no filter.
  const acts = d.actionList.filter(
    a => a.available && (!B.model.distinctOnly || a.distinct ||
                         a.code === B.model.action));
  let group = null, host = sel;
  for (const a of acts) {
    if (a.groupLabel !== group) {
      group = a.groupLabel;
      host = document.createElement('optgroup');
      host.label = group;
      sel.appendChild(host);
    }
    const o = el('option', null,
      (a.named ? a.label : `${a.label} (${a.code}, unidentified)`) +
      (a.distinct ? '' : ` — same clip as ${a.aliasOf}`));
    o.value = a.code;
    o.title = [a.evidence, a.motion,
               a.selfContained ? 'this action ships its own mesh'
                               : 'motion only — bound over the shared skeleton']
      .filter(Boolean).join('\n');
    host.appendChild(o);
  }
  const missing = d.actionList.filter(a => !a.available);
  if (missing.length) {
    const og = document.createElement('optgroup');
    og.label = `Not in this install (${missing.length})`;
    for (const a of missing.slice(0, 60)) {
      const o = el('option', null, `${a.label} (${a.code}) — file missing`);
      o.value = a.code;
      o.disabled = true;
      o.title = a.reason;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  if (!acts.some(a => a.code === B.model.action)) {
    B.model.action = (acts[0] || { code: d.defaultAction }).code;
  }
  sel.value = B.model.action;
  $('#anim-speed').value = String(B.anim.speed || 41);
}

async function fetchModelClip(action) {
  const key = `model:${B.model.key}:${action}`;
  if (B.anim.cache[key]) return B.anim.cache[key];
  const p = new URLSearchParams({ key: B.model.key });
  if (action) p.set('action', action);
  let d;
  try { d = await api('/api/modelanim?' + p.toString()); }
  catch (e) { return null; }
  B.anim.cache[key] = d;
  return d;
}

// ---------------------------------------------------------------- the stage
const modelScale = () => Math.max(0.05, (B.model.zoom || 100) / 100);

/** monster.json's zoomPercent, as a model matrix.
 *  Applied as a uniform scale because that is what a percentage of the
 *  authored size means; the field is VERIFIED, the reading is INFERRED. */
function zoomMatrix() {
  const s = modelScale();
  return [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, 0, 0, 0, 1];
}

function scaledBounds(b) {
  if (!b || !b.min || !b.max) return null;
  const s = modelScale();
  return { min: b.min.map(v => v * s), max: b.max.map(v => v * s) };
}

async function rebuildModel({ reframe = false } = {}) {
  stopModelEffect();
  if (!B.model.key) {
    if (viewer) { viewer.clear(); viewer.draw(); }
    $('#gl-msg').textContent = 'pick a model on the left';
    $('#gl-msg').classList.remove('hidden');
    renderModelPanel();
    return;
  }
  const tk = tokenNow();
  $('#gl-msg').textContent = 'loading…';
  $('#gl-msg').classList.remove('hidden');

  let info = B.model.data;
  if (!info || info.key !== B.model.key) {
    try { info = await api('/api/model?key=' + encodeURIComponent(B.model.key)); }
    catch (e) {
      $('#gl-msg').textContent = 'could not load this model: ' + e.message;
      return;
    }
    if (!stillCurrent(tk)) return;
    B.model.data = info;
    $('#card-monster').classList.toggle('hidden', info.kind !== 'monster');
  }
  if (info.kind === 'effect') return showEffectModel(info, tk);

  fillModelActions();
  const d = await fetchModelClip(B.model.action);
  if (!stillCurrent(tk)) return;
  if (!d || !d.frames || !d.scene) {
    $('#gl-msg').textContent =
      (d && d.error) || 'no motion ships for this action';
    renderModelPanel();
    renderAnimPanel();
    return;
  }

  const mat = zoomMatrix();
  const defs = d.scene.meshes.map(m => ({ meta: m, textureKey: 'model',
                                          matrix: mat }));
  viewer.setMeshes(defs, { frameOn: scaledBounds(d.bounds),
                           keepFraming: !reframe });
  await applyNamedTexture('model', d.texture);
  if (!stillCurrent(tk)) return;
  $('#gl-msg').classList.add('hidden');
  $('#gl-stats').textContent = viewer.stats +
    `\n${info.label} · ${d.action} · ${d.mesh}`;

  B.anim.seq = [d];
  B.anim.data = d;
  B.anim.seqAt = 0;
  B.anim.frame = 0;
  syncAnimUi();
  renderModelPanel();
  renderMonsterPanel();
  renderAnimPanel();
  viewer.draw();
}

// ---------------------------------------------------------------- effects
//
// An effect is not a posed mesh, so it does not go through setMeshes/setPose.
// It is the same layered scene the asset browser plays (docs/viewer.md §4.5),
// rendered by the same fx.js, anchored at the origin because a standalone
// effect has no parent to ride.

function stopModelEffect() {
  if (B.model.raf) cancelAnimationFrame(B.model.raf);
  B.model.raf = null;
}

/** The bounds of an effect's own quads, so the camera has something to fit.
 *  Effects carry no body to frame on and their scale varies wildly (a 12-unit
 *  spark against a 400-unit aura), so without this the view keeps whatever the
 *  last model left it at and a small effect is an invisible dot. */
function effectBounds(def) {
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const lay of (def && def.layers) || []) {
    for (const p of lay.parts || []) {
      const b = p.geometry && p.geometry.bboxRender;
      if (!b) continue;
      for (let i = 0; i < 3; i++) {
        lo[i] = Math.min(lo[i], b[0][i]);
        hi[i] = Math.max(hi[i], b[1][i]);
      }
    }
  }
  if (!isFinite(lo[0])) return null;
  return { min: lo, max: hi };
}

async function showEffectModel(info, tk) {
  const eb = effectBounds(info.effect);
  viewer.setMeshes([], { frameOn: eb, keepFraming: !eb });
  $('#anim-action').innerHTML = '';
  if (!info.effect) {
    $('#gl-msg').textContent = info.error || 'this effect has no playable geometry';
    renderModelPanel();
    return;
  }
  const keys = {};
  for (const lay of info.effect.layers || []) {
    if (lay.texture) keys[lay.index] = `mfx:${lay.index}`;
  }
  const n = viewer.setEffects([{ def: info.effect, role: 'model',
                                 anchor: FX.IDENT.slice(), textureKeys: keys }]);
  for (const lay of info.effect.layers || []) {
    if (lay.texture) await applyNamedTexture(`mfx:${lay.index}`, lay.texture);
  }
  if (!stillCurrent(tk)) return;
  $('#gl-msg').classList.toggle('hidden', !!n);
  if (!n) {
    $('#gl-msg').textContent =
      'no drawable layer — this effect is pure PTCL/PTC3 particles, which are ' +
      'not decoded (docs/effects.md §6.6)';
  }
  $('#gl-stats').textContent = `${info.ident || info.id} · ${n} layer(s)`;
  $('#fx-wrap').classList.toggle('hidden', !n);
  B.anim.seq = [];
  B.anim.data = null;
  syncAnimUi();
  renderModelPanel();
  renderAnimPanel();
  if (n) modelEffectLoop(info);
}

function modelEffectLoop(info) {
  stopModelEffect();
  const t0 = performance.now();
  const e = info.effect;
  const dur = Math.max(400, (e.effectiveFrames || e.frames || 1) *
                            (e.frameIntervalMs || 41));
  const step = () => {
    if (B.mode !== 'model' || !viewer.fx.length) return;
    const t = (performance.now() - t0) % dur;
    if (t < 40) viewer.resetEffects();
    viewer.setEffectTime(t, null);
    $('#fx-label').textContent =
      `${Math.round(t)} / ${Math.round(dur)} ms · ${info.id}`;
    $('#fx-slider').max = String(Math.round(dur));
    $('#fx-slider').value = String(Math.round(t));
    viewer.draw();
    B.model.raf = requestAnimationFrame(step);
  };
  B.model.raf = requestAnimationFrame(step);
}

// ---------------------------------------------------------------- panels
function renderModelPanel() {
  const b = $('#model-body');
  b.innerHTML = '';
  const info = B.model.data;
  if (!info) {
    b.appendChild(el('div', 'mut small', 'pick a model on the left'));
    return;
  }
  const act = (info.actionList || []).find(a => a.code === B.model.action);
  b.appendChild(kv([
    ['model', info.label],
    ['kind', (B.model.kinds.find(k => k.kind === info.kind) || {}).singular ||
             info.kind],
    ['id', info.id],
    ['directory', info.dir],
    ['3dmotion shape', info.shape || '—'],
    ['mesh', act ? act.mesh : info.mesh],
    ['texture', info.texture],
    ['texture from', info.textureMethod
      ? `${info.textureKind} · ${info.textureMethod}` : ''],
    ['files on disk', info.files || ''],
    ['actions', info.actions
      ? `${info.playable} playable over ${info.clips} distinct clip` +
        `${info.clips === 1 ? '' : 's'}` : ''],
    ['layout', info.detail],
  ]));
  if ((info.names || []).length) {
    b.appendChild(el('div', 'small mut',
      'ini/npc.json calls this: ' + info.names.join(', ')));
  }
  if ((info.shapes || []).length > 1) {
    b.appendChild(el('div', 'small mut',
      `Shapes ${info.shapes.join(', ')} all resolve into this directory — the ` +
      `art is shared between several creatures. The action list shown is ` +
      `shape ${info.shape}'s.`));
  }
  if (act) {
    b.appendChild(el('div', 'small mut',
      act.selfContained
        ? `Action ${act.code} ships its own geometry (${act.motion}), so ` +
          `changing action here changes the mesh being drawn.`
        : `Action ${act.code} is motion-only (${act.motion}); it binds over ` +
          `${act.mesh} by ordinal, exactly as a player body's motion set does.`));
    if (!act.distinct) {
      b.appendChild(el('div', 'small mut',
        `ini/3dmotion.ini gives this code the same file as ${act.aliasOf}.`));
    }
  }
  b.appendChild(el('div', 'note', info.layoutNote || ''));
  const link = el('button', 'ghost tiny', 'Copy link to this model');
  link.addEventListener('click', copyModelLink);
  b.appendChild(link);
  const insp = el('a', 'small');
  insp.href = '/#';
  insp.textContent = 'open this mesh in the asset browser →';
  insp.addEventListener('click', () => {
    try { localStorage.setItem('coviewer.jumpTo', act ? act.mesh : info.mesh); }
    catch (e) { /* ignore */ }
  });
  b.appendChild(insp);
}

/** ini/monster.json, offered as a pairing the USER makes.
 *
 *  There is no join column: `type` is a sequential 1..374 and `bodyType` is 0
 *  on every row, so the client's own appearance choice is made by the server
 *  at spawn. What the row does carry is `zoomPercent` (60..350), which changes
 *  the render more than anything else on this page.
 */
function renderMonsterPanel() {
  const b = $('#monster-body');
  const info = B.model.data;
  b.innerHTML = '';
  if (!info || info.kind !== 'monster') return;
  b.appendChild(el('div', 'note', info.monsterLinkNote || ''));

  const row = el('div', 'zoomrow');
  row.appendChild(el('span', 'small mut', 'zoom'));
  const sl = el('input');
  sl.type = 'range'; sl.min = '40'; sl.max = '400'; sl.step = '5';
  sl.value = String(B.model.zoom);
  sl.title = info.zoomNote || '';
  sl.addEventListener('input', e => setModelZoom(+e.target.value));
  row.appendChild(sl);
  row.appendChild(el('b', null, `${B.model.zoom}%`));
  const rst = el('button', 'ghost tiny', '100%');
  rst.addEventListener('click', () => setModelZoom(100));
  row.appendChild(rst);
  b.appendChild(row);

  if (B.model.row) {
    const r = B.model.row;
    b.appendChild(kv([
      ['paired with', `${r.name} (type ${r.type})`],
      ['zoomPercent', `${r.zoomPercent}%`],
      ['level · life', `${r.level} · ${r.maxLife}`],
      ['born action', r.bornAction],
      ['born effect', r.bornEffect],
      ['blend (asb/adb)', `${r.asb} / ${r.adb}`],
      ['sizeAdd', r.sizeAdd],
      ['actResCtrl', r.actResCtrl],
    ]));
    const un = el('button', 'ghost tiny', 'Unpair');
    un.addEventListener('click', () => { B.model.row = null; setModelZoom(100); });
    b.appendChild(un);
    if (r.bornAction) {
      const jump = el('button', 'ghost tiny',
                      `Play born action ${r.bornAction}`);
      jump.addEventListener('click', () => selectModelAction(String(r.bornAction)));
      b.appendChild(jump);
    }
  }

  const search = el('input');
  search.type = 'search';
  search.placeholder = 'pair a monster.json row (name or type)…';
  search.addEventListener('input', debounce(
    e => loadMonsterRows(e.target.value), 220));
  b.appendChild(search);
  const list = el('div', 'monsterpick');
  list.id = 'monster-rows';
  b.appendChild(list);
  loadMonsterRows('');
}

async function loadMonsterRows(q) {
  const host = $('#monster-rows');
  if (!host) return;
  let data;
  try {
    data = await api('/api/monsterrows?limit=200&q=' + encodeURIComponent(q || ''));
  } catch (e) { host.innerHTML = ''; return; }
  host.innerHTML = '';
  for (const r of data.rows) {
    const row = el('div', 'mrow' + (B.model.row && B.model.row.type === r.type
                                    ? ' sel' : ''));
    row.appendChild(el('span', null, r.name));
    row.appendChild(el('span', 'z', `${r.zoomPercent}%`));
    row.appendChild(el('span', 'lv', `lv ${r.level}`));
    row.title = `type ${r.type} · born ${r.bornAction} / ${r.bornEffect} · ` +
                `asb ${r.asb} adb ${r.adb} · sizeAdd ${r.sizeAdd}`;
    row.addEventListener('click', () => pairMonsterRow(r));
    host.appendChild(row);
  }
  if (!data.rows.length) {
    host.appendChild(el('div', 'mut small', 'no monster.json row matches'));
  }
}

function pairMonsterRow(r) {
  B.model.row = r;
  setModelZoom(r.zoomPercent || 100);
  toast(`${r.name} paired — zoom ${r.zoomPercent}%. Your pairing, not the data's.`,
        4000);
}

function setModelZoom(pct) {
  B.model.zoom = Math.max(10, Math.min(600, pct | 0));
  saveModelState();
  if (viewer) {
    // The zoom is a model matrix, so nothing has to be re-fetched or re-posed
    // — but `resetView` frames on the *last* bounds it was told about, so
    // those have to move with it or R would snap back to the unscaled size.
    const mat = zoomMatrix();
    for (const m of viewer.meshes) m.model = new Float32Array(mat);
    const b = scaledBounds(B.anim.data && B.anim.data.bounds);
    if (b) {
      viewer._lastBounds = {
        center: [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2,
                 (b.min[2] + b.max[2]) / 2],
        radius: Math.max(1, Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1],
                                       b.max[2] - b.min[2]) / 2),
      };
    }
    viewer.draw();
  }
  renderMonsterPanel();
}

async function selectModelAction(code) {
  const d = B.model.data;
  if (!d) return;
  const act = (d.actionList || []).find(a => a.code === code);
  if (!act || !act.available) {
    toast(act ? act.reason : `no action ${code} on this model`, 5000);
    return;
  }
  animPause();
  B.model.action = code;
  $('#anim-action').value = code;
  saveModelState();
  // Unlike an equipped part, a model's *geometry* changes with the action on
  // most families, and several clips (monster 103's death slides 121 units)
  // carry real root translation — so the frame is re-fitted rather than held,
  // or picking "attack" walks the creature out of shot. Lock camera (L) still
  // overrides, which is the way to compare two actions pixel for pixel.
  await rebuild({ reframe: true });
}

function modelActionStep(delta) {
  const d = B.model.data;
  if (!d || !d.actionList) return;
  const acts = d.actionList.filter(
    a => a.available && (!B.model.distinctOnly || a.distinct ||
                         a.code === B.model.action));
  if (!acts.length) return;
  let i = acts.findIndex(a => a.code === B.model.action);
  if (i < 0) i = 0;
  const next = acts[(i + delta + acts.length) % acts.length];
  selectModelAction(next.code);
}

function copyModelLink() {
  const p = new URLSearchParams({ model: B.model.key });
  if (B.model.action) p.set('action', B.model.action);
  if (B.model.zoom !== 100) p.set('zoom', String(B.model.zoom));
  const url = location.origin + '/builder#' + p.toString();
  location.hash = p.toString();
  navigator.clipboard.writeText(url).then(
    () => toast('model link copied'),
    () => toast('clipboard blocked; the link is in the address bar'));
}

// ---------------------------------------------------------------- controls
function bindControls() {
  $('#btn-more-slots').addEventListener('click', () => {
    $('#slot-cards-more').classList.toggle('hidden');
    renderSlots();
  });
  $('#btn-share').addEventListener('click', copyLink);
  $('#btn-reset-all').addEventListener('click', resetAll);
  // panel collapse (header button, per-card headers and `C`) is CardPanels',
  // bound in restoreCollapsed() before this runs — one implementation, shared
  // with the asset browser page.
  $('#picker-close').addEventListener('click', cancelPicker);
  $('#picker-clear').addEventListener('click', async () => {
    const slot = B.picker.slot;
    closePicker();
    if (slot) await unequip(slot);
  });
  $('#picker-search').addEventListener('input', debounce(loadPickerOptions, 220));
  $('#picker').addEventListener('click', e => {
    if (e.target.id === 'picker') cancelPicker();
  });

  $('#mode-character').addEventListener('click', () => setMode('character'));
  $('#mode-model').addEventListener('click', () => setMode('model'));
  $('#model-search').addEventListener('input', debounce(e => {
    B.model.q = e.target.value.trim();
    loadModelList();
  }, 220));
  $('#chk-clips').addEventListener('change', e => {
    B.model.distinctOnly = e.target.checked;
    saveModelState();
    fillModelActions();
  });

  $('#anim-action').addEventListener('change', async e => {
    if (B.mode === 'model') { selectModelAction(e.target.value); return; }
    animPause();
    B.anim.action = e.target.value;
    B.anim.frame = 0;
    await ensureAnim();
    showAnimFrame(0);
    renderAnimPanel();
    refreshWeaponPanel();
  });
  $('#anim-speed').addEventListener('input', e => {
    B.anim.speed = +e.target.value;
    if (B.anim.playing) { animPause(); animPlay(); } else syncAnimUi();
    renderAnimPanel();
  });
  $('#btn-anim-play').addEventListener('click', () => {
    B.anim.playing ? animPause() : animPlay();
  });
  $('#anim-frame').addEventListener('input', e => {
    animPause();
    showAnimFrame(+e.target.value);
  });

  const rerender = () => { if (viewer) viewer.draw(); };
  $('#chk-grid').addEventListener('change', e => { viewer.opts.grid = e.target.checked; rerender(); });
  $('#chk-wire').addEventListener('change', e => { viewer.opts.wire = e.target.checked; rerender(); });
  $('#chk-lock').addEventListener('change', e => {
    viewer.setLock(e.target.checked);
    $('#lock-label').classList.toggle('on', e.target.checked);
  });
  if (viewer) {
    $('#chk-lock').checked = viewer.opts.lock;
    $('#lock-label').classList.toggle('on', viewer.opts.lock);
  }
  $('#btn-reset').addEventListener('click', () => {
    viewer.resetView();
    if (B.mode === 'model') { if (B.model.key) rebuild({ reframe: true }); }
    else if (B.figure) rebuild({ reframe: true });
  });
  $('#btn-focus').addEventListener('click', focusHead);
  $('#btn-shot').addEventListener('click', () => saveShot());
  $('#btn-help').addEventListener('click', () => $('#help').classList.toggle('hidden'));
  $('#help-close').addEventListener('click', () => $('#help').classList.add('hidden'));
  $('#help').addEventListener('click', e => {
    if (e.target.id === 'help') $('#help').classList.add('hidden');
  });
  $('#chk-aura').addEventListener('change', e => setAura(e.target.checked));
  $('#aura-label').addEventListener('click', e => {
    // A disabled checkbox swallows the click, so the label explains instead of
    // doing nothing — "why is this greyed" is the actual question here.
    if ($('#chk-aura').disabled) { e.preventDefault(); toast(auraWhyNot(), 6000); }
  });
  $('#btn-fx-stop').addEventListener('click', () => setAura(false));
}

function focusHead() {
  const a = B.anchors && (B.anchors.v_armet || B.anchors.v_head);
  if (!a) { toast('no head socket on this body'); return; }
  viewer.focusOn(a.pos, viewer.radius * 0.16);
}

const TEXT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const typingInAField = () => {
  const a = document.activeElement;
  return !!a && (TEXT_TAGS.has(a.tagName) || a.isContentEditable);
};

function bindKeys() {
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    // Arrow keys must never be stolen from the search box. Escape is the way
    // back out of it, and the second Escape closes the picker.
    if (typingInAField()) {
      if (e.key === 'Escape') { document.activeElement.blur(); e.preventDefault(); }
      else if (e.key === 'ArrowDown' && B.picker.open) {
        document.activeElement.blur(); pickerMove(1); e.preventDefault();
      } else if (e.key === 'Enter' && B.picker.open) {
        document.activeElement.blur(); e.preventDefault();
      }
      return;
    }
    if (B.picker.open) {
      switch (e.key) {
        case 'ArrowDown': pickerMove(1); break;
        case 'ArrowUp': pickerMove(-1); break;
        case 'ArrowRight': pickerMoveVariant(1); break;
        case 'ArrowLeft': pickerMoveVariant(-1); break;
        case 'PageDown': pickerMove(10); break;
        case 'PageUp': pickerMove(-10); break;
        case 'Home': highlight(0, 0); break;
        case 'End': highlight(B.picker.items.length - 1, 0); break;
        case 'Enter': {
          const it = B.picker.items[B.picker.index];
          if (it) highlight(B.picker.index, B.picker.variant, { immediate: true });
          closePicker();
          break;
        }
        case 'Escape': cancelPicker(); break;
        case '/': $('#picker-search').focus(); break;
        default: return;
      }
      e.preventDefault();
      return;
    }
    if (e.key === 'Q' || e.key === 'q') {
      if (B.mode !== 'model') stepQuality(e.shiftKey ? -1 : 1);
      e.preventDefault();
      return;
    }
    if (e.key === 'M' || e.key === 'm') {
      setMode(B.mode === 'model' ? 'character' : 'model');
      e.preventDefault();
      return;
    }
    if (B.mode === 'model') {
      switch (e.key) {
        case 'ArrowDown': modelMove(1); break;
        case 'ArrowUp': modelMove(-1); break;
        case 'PageDown': modelMove(10); break;
        case 'PageUp': modelMove(-10); break;
        case 'Home': highlightModel(0); break;
        case 'End': highlightModel(B.model.list.length - 1); break;
        case ']': modelActionStep(1); break;
        case '[': modelActionStep(-1); break;
        default: break;
      }
      if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End',
           ']', '['].includes(e.key)) { e.preventDefault(); return; }
    }
    switch (e.key) {
      case ' ': B.anim.playing ? animPause() : animPlay(); break;
      case 'Escape': $('#help').classList.add('hidden'); return;
      case '?': $('#help').classList.toggle('hidden'); break;
      case '1': openPicker('body'); break;
      case '2': openPicker('armet'); break;
      case '3': openPicker('r_weapon'); break;
      case '4': openPicker('l_weapon'); break;
      default:
        switch (e.key.toLowerCase()) {
          case 'b': openPicker('body'); break;
          case 'h': openPicker('armet'); break;
          case 'r': $('#btn-reset').click(); break;
          case 'f': focusHead(); break;
          case 'l': $('#chk-lock').click(); break;
          case 'g': $('#chk-grid').click(); break;
          case 'w': $('#chk-wire').click(); break;
          case 'c': toggleAllPanels(); break;
          case 'a': toggleAura(); break;
          default: return;
        }
    }
    e.preventDefault();
  });
}

async function saveShot(name) {
  if (!viewer) return null;
  const base = typeof name === 'string' && name ? name
    : (B.mode === 'model'
        ? `model_${(B.model.key || 'none').replace(/[^a-z0-9]+/gi, '-')}` +
          `_${B.model.action || ''}`
        : 'character_' + Object.entries(B.loadout)
            .map(([k, v]) => `${k}-${v.id}`).join('_')).slice(0, 70);
  try {
    const r = await api('/api/snapshot?name=' + encodeURIComponent(base),
                        { method: 'POST', body: viewer.snapshot() });
    toast('saved ' + r.file, 3000);
    return r.file;
  } catch (e) { toast('snapshot failed: ' + e.message, 4000); return null; }
}
window.saveShot = saveShot;


boot().catch(e => {
  const s = document.getElementById('statusline');
  if (s) s.textContent = 'startup failed: ' + e.message;
  console.error(e);
});
