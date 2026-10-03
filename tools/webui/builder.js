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

/* WHICH OF THE TWO PAGES THIS IS.
 *
 * `builder.html` and `models.html` are two documents, two routes and one
 * engine -- this file. They share the stage, the action dropdown, the
 * playback loop and the camera, because for a monster and for a character
 * those genuinely are the same job (see the header above). They do NOT share
 * markup: there is no slot rail or picker on the Model Viewer and no model
 * rail on the builder, so each page carries only what it uses.
 *
 * Read from `data-co-page`, which is also what nav.js lights the tab from --
 * one declaration per page, not two that can disagree. Never sniffed from the
 * URL: `/models`, `/models/` and `/models.html` are the same page and a
 * sniffer has to be taught each one.
 */
const PAGE = (document.body && document.body.dataset.coPage) || 'builder';
const IS_MODELS = PAGE === 'models';

/* Old links keep working. A fragment never reaches the server, so this is the
 * only place `/builder#model=…` and `/builder#mesh=…` can be honoured -- and
 * they must be: those URLs were the shareable output of `Copy link` and of
 * the browser's "open this mesh in the model viewer", so they are in people's
 * scrollback. `replace`, not `assign`, so Back does not bounce between the
 * two pages. Redirected, not refused, and not silently ignored. */
if (!IS_MODELS) {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (h.get('model') || h.get('mesh')) {
    location.replace('/models' + location.hash);
  }
}

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
  /** 'character' (slots + appearances) or 'model' (monsters, NPCs, ...).
   *  Now fixed by WHICH PAGE this is -- it was a control, and the control is
   *  what the tab bar could not honestly offer. Kept as a field because the
   *  shared stage, action and rebuild paths branch on it throughout. */
  mode: IS_MODELS ? 'model' : 'character',
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
  /** The Super aura, **per weapon hand**. A character can hold a weapon in
   *  each hand and each one's aura is its own control: both on, either alone,
   *  or neither. This used to be a single `{on, rec}` for a single hand --
   *  `equippedWeapon()` picked the right hand and fell back to the left -- so
   *  the left-hand weapon could only ever glow while the right hand was empty.
   *  Nothing below the UI had that limit: `/api/superfx?slot=` already
   *  resolves and anchors either hand, and returns different matrices for
   *  them (measured on body 003131090 with 480139 in both hands: right
   *  x=-24.6, left x=+23.4). `raf` is shared because one rAF drives both. */
  superfx: { hands: { r_weapon: { on: false, rec: null, anchor: null },
                      l_weapon: { on: false, rec: null, anchor: null } },
             raf: null },
  /** the model mode. `zoom` is monster.json's zoomPercent when a row has been
   *  paired, 100 otherwise; `row` is that paired row, which is the USER's
   *  assertion because monster.json carries no link to the art at all. */
  // `adhoc` is a mesh opened straight from the asset browser by logical
  // path. It has no catalogue key -- that is the whole point: any .c3 the
  // browser can show should be openable here, not just catalogued models.
  adhoc: null,
  /** `source` picks what the rail lists. `catalogue` is the model families
   *  the client's own directory convention describes; `path` is every mesh
   *  under `path` in the active view, which is the only way to reach art
   *  that convention does not name -- a recovered garment archive, or the
   *  Collection, where a model lives at collection/<category>/<entry>.c3. */
  model: { key: '', kind: '', action: '', data: null, list: [], index: -1,
           kinds: [], meta: null, q: '', zoom: 100, row: null,
           distinctOnly: true, raf: null,
           source: 'catalogue', path: '', dirs: [] },
  /** `${slot}:${id}` -> the quality ladder of that weapon's family. Cached
   *  because arrowing through a picker asks for it on every keypress. */
  qualityCache: {},
  /** The slot the **Colour & variants** panel is currently drawn for, which is
   *  the slot you last touched: opening its picker, arrowing inside one, or
   *  picking a quality all route through `renderLookPanel`. It is what `Q` /
   *  shift-`Q` step, so this is not hidden state -- it is the panel on screen,
   *  with that slot named at the top of it and the ladder underneath. */
  lookSlot: null,
  collapsed: {},
  loadToken: 0,
  status: null,
};
window.B = B;

let viewer = null;

// ---------------------------------------------------------------- plumbing
async function api(path, opts) {
  // A bare "NetworkError" says the request never completed and nothing about
  // why. Name the request and the two things that actually cause it, so the
  // toast is a lead rather than a dead end.
  let r;
  try {
    try {
      r = await fetch(path, opts);
    } catch (first) {
      // A caller that ABORTED gets its abort back: a retry against a signal
      // that is already dead fails at once, and the message below would then
      // blame the viewer for a request the page itself withdrew.
      if (first && first.name === 'AbortError') throw first;
      // A connection that died in the pool fails this attempt and nothing after it.
      // Chrome retries a POST like that for us; Firefox does not.
      r = await fetch(path, opts);
    }
  } catch (e) {
    if (e && e.name === 'AbortError') throw e;
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
  if (!r.ok) {
    // The STATUS travels with the error, so a caller can tell "this id does
    // not exist on this install" (404) from "the request failed".
    const err = new Error(j.error || r.statusText);
    err.status = r.status;
    err.body = j;
    throw err;
  }
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

function chip(text, count, on, onClick, cls) {
  const c = el('span', 'chip' + (on ? ' on' : '') + (count === 0 ? ' zero' : '') +
                       (cls ? ' ' + cls : ''));
  c.appendChild(document.createTextNode(text));
  if (count !== null && count !== undefined) c.appendChild(el('span', 'n', String(count)));
  if (onClick) c.addEventListener('click', onClick);
  return c;
}

// -------------------------------------------------------------- server picker
//
// The active view is one process-wide choice, shared with the asset browser --
// so this stage was always drawing from *some* namespace and had no way to say
// which. Switching reloads, because the model list, the appearance tables and
// every cached clip are derived from it.
async function initServerPicker() {
  const sel = $('#server-select');
  const label = $('#server-label');
  if (!sel) return;
  let doc;
  try { doc = await api('/api/servers'); } catch (e) { return; }
  if (!doc.servers || !doc.servers.length) return;
  sel.innerHTML = '';
  const b = el('option', null, 'base — ' + (doc.base || 'install'));
  b.value = '';
  sel.appendChild(b);
  for (const s of doc.servers) {
    const o = el('option', null,
      s.name + (s.clientVersion ? ` (v${s.clientVersion})` : '') +
      (s.files ? ` — ${s.files.toLocaleString()} files` : ''));
    o.value = s.name;
    sel.appendChild(o);
  }
  sel.value = doc.current || '';
  label.classList.remove('hidden');
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
  initServerPicker();
  Swap.configure({ api, toast, onChange: () => renderCollect({ force: true }) });
  Swap.initDrawer();

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
  // /api/builder is still asked for on both pages: it carries
  // `defaultFrameMs`, which the shared playback loop below needs. Only the
  // slot furniture it also carries is builder-only.
  if (!IS_MODELS) $('#compat-note').textContent = B.config.compatNote;

  try {
    const saved = JSON.parse(localStorage.getItem(ANIM_KEY) || 'null');
    if (saved && saved.action) B.anim.action = saved.action;
    if (saved && saved.speed) B.anim.speed = saved.speed;
  } catch (e) { /* ignore */ }
  B.anim.speed = B.anim.speed || B.config.defaultFrameMs || 41;
  restoreAura();
  // Building the starting character touches the slot rail, which does not
  // exist on the Model Viewer -- and would be work done for a figure that
  // page never draws.
  if (!IS_MODELS) {
    if (restored && restored.body) await refreshLoadoutNames();
    if (!B.loadout.body) await applyDefaultLoadout();
    B.bodyType = (B.loadout.body && B.loadout.body.id || B.bodyType || '002').slice(0, 3);
    saveLoadout();                 // now this install's own, checked
  }
  restoreModelState();
  await fillActions();
  if (!IS_MODELS) renderSlots();
  renderAnimPanel();
  applyMode({ rebuild: false });
  if (IS_MODELS) await loadModelList({ select: B.model.key });
  await rebuild({ reframe: true });
  renderCollect();
  if (!IS_MODELS) showLookFor('body');
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

/** One action's text, wherever it is listed. EVERY row carries its own
 *  animation number, named or not.
 *
 *  Before this, only unidentified rows showed a number, so the list read:
 *
 *      Dead (hold of 332)        <- a number, but it is 332's, not this row's
 *      Die variant               <- no number at all
 *      Cast / special (919, unidentified)
 *
 *  The one number you could read off a named row was somebody else's. The
 *  owner asked for the number on everything, which also makes the list
 *  searchable by the code that 3dmotion.ini is actually keyed on.
 *
 *  It is a FUNCTION because there are three call sites -- the builder's
 *  dropdown, the model panel's dropdown and the "not in this install" group.
 *  The last time a rule here changed it was fixed in one and missed in
 *  another, and the owner reported the same defect twice from two panels. */
function actionLabel(a) {
  const base = a.named ? `${a.label} (${a.code})`
                       : `${a.label} (${a.code}, unidentified)`;
  // WHETHER THIS ROW WILL SHOW YOU THIS ACTION AT ALL.
  //
  // `MotionIndex.lookup`'s five-step chain ends at the universal idle, so a
  // code this client ships no motion for still resolves, still reports
  // frames and chunks, and still plays -- as the IDLE. MEASURED on 5017,
  // body 3135990 + bow 500218: 45 of the 94 rows in this very menu resolve
  // through a fallback, and nothing distinguished them.
  //
  // The list is where the choice is made, so the warning belongs here and
  // not only on the panel after playing. Naming an unidentified code from a
  // fallback clip is a wrong answer that gets written down, and this is the
  // last point at which it is cheap to avoid.
  //
  // `own` IS THE SERVER'S VERDICT; `how` IS ITS WORDING. This used to test
  // `a.how !== 'exact'`, and `resolve()` suffixes an exact hit when the alias
  // table answered -- `exact (weapon set 580 animates from set 560)` -- so
  // every such row read as a fallback: 40 of the 157 rows on a 580 loadout
  // (5517, body 002135000 + weapon 580001, 2026-09-25) said "no own motion"
  // while playing the action's own clip. `anim.route_is_own` decides once,
  // server-side. A row with no verdict at all (an older payload) stays
  // unmarked rather than reading as a fallback.
  return a.own === false ? `${base} — no own motion` : base;
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
  // WHEN A FOREIGN SOURCE IS SELECTED, ask which actions actually DIFFER.
  // The owner's words: "I would like the animation list to show what is
  // different and what is added." Neither is visible from the action name,
  // and without it you switch source and have no idea which of ~200 entries
  // is worth watching.
  const msrc = (document.getElementById('anim-source') || {}).value || '';
  if (msrc) p.set('source', msrc);
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
    const base = actionLabel(a);
    // The mark goes in FRONT so the column of them scans down the list; a
    // suffix would sit at a different x on every row and have to be read.
    const mark = { added: '+ ', different: '≠ ', onlyhere: '− ' }[a.delta] || '';
    const o = el('option', null, mark + base);
    o.value = a.code;
    o.title = ({
      added: 'ADDED: this install resolves no file for this action; the '
           + 'selected source has one. These are the motions removed from '
           + 'client 7320 onward.',
      different: 'DIFFERENT: both installs have this motion and the BYTES '
               + 'DIFFER. Same path, different animation -- the name gives '
               + 'no hint, which is why it is marked.',
      onlyhere: 'ONLY HERE: this install has it and the source does not. Not '
              + 'an error -- the source is an older client.',
      same: 'SAME: both installs have this motion and the bytes are '
          + 'identical. Switching source will change nothing.',
      absent: 'Neither install resolves a file for this action.'
    }[a.delta] || '') + (a.delta && a.evidence ? '\n\n' : '') + (a.evidence || '');
    if (a.delta) o.dataset.delta = a.delta;
    host.appendChild(o);
  }
  if (!data.actions.some(a => a.code === B.anim.action)) {
    B.anim.action = (data.actions[0] || { code: '100' }).code;
  }
  sel.value = B.anim.action;
  $('#anim-speed').value = String(B.anim.speed || data.defaultFrameMs || 41);
  // A COUNT BESIDE THE MENU, because 200 options with marks scattered
  // through them does not answer "is there anything here worth watching".
  const lbl = document.getElementById('anim-delta');
  // A SOUL ANIMATES AS THE WEAPON MESH IT PUTS IN YOUR HAND. `804240`
  // BowSoulLv130 keys motion set 500 (its weapon.ini Mesh0 is a bow), not
  // the 804 its id splits to; the server resolves that
  // (`anim.AnimDB.weapon_type_via`, census at `anim.SOUL_TYPE_MIN`) and says
  // so with `weaponTypeVia === 'mesh'`. Without this the menu lists a bow's
  // actions under a slot whose id says otherwise and nothing explains the
  // agreement. This span is the one element beside the menu that nothing
  // else rewrites (#anim-label is the frame counter's), so the note lives
  // here and is appended to the source comparison when both apply. An
  // older payload with no `weaponTypeVia` stays as it was.
  const soul = data.weaponTypeVia === 'mesh'
    ? `animates as set ${data.weaponset}` : '';
  if (lbl && data.source) {
    const n = (t) => data.actions.filter(a => a.delta === t).length;
    lbl.textContent = `vs ${data.source}: ${n('added')} added, `
                    + `${n('different')} different, ${n('same')} same`
                    + (soul ? ` · ${soul}` : '');
    lbl.title = 'Compared by BYTES, not by name or size: two motions of '
              + 'equal length are routine.'
              + (soul ? `\n\n${data.weaponTypeNote || ''}` : '');
  } else if (lbl && !data.source) {
    lbl.textContent = soul;
    lbl.title = soul ? (data.weaponTypeNote || '') : '';
  }
  fillMotionSources();
}

/** The "motion from" menu: installs that carry motions THIS one declares and
 *  does not ship.
 *
 *  Only installs that actually supply something are listed. One that supplies
 *  nothing would sit in the menu doing nothing when picked, which reads as
 *  "I tried it and it looked identical" -- a wrong answer produced by an
 *  empty option rather than by the data.
 *
 *  Refreshed with the action list because it depends on the LOADOUT: which
 *  motions are missing is a function of shape and weaponset.
 *
 *  ONE REQUEST IN FLIGHT, the current loadout's. This is fire-and-forget
 *  (nothing awaits it) and it used to carry no AbortController, so every body
 *  switch left the previous body's request running -- and `api()` retried a
 *  failed one, doubling it. MEASURED 2026-09-29 in the owner's tab, cold
 *  server: six /api/motionsources outstanding for five bodies already left,
 *  filling the browser's ~6 connections per origin, and the NEXT body's
 *  /api/figure queued behind them in the browser. The page said "building
 *  the figure..." and nothing loaded, in two browsers. The satellites call
 *  next door already did this right; this now does the same. */
let motionSrcAbort = null;
let motionSrcFor = '';
let motionSrcInflight = false;

async function fillMotionSources() {
  const sel = document.getElementById('anim-source');
  if (!sel || B.mode === 'model' || !B.loadout.body) return;
  const keep = sel.value;
  const p = new URLSearchParams({ body: B.loadout.body.id });
  if (B.loadout.r_weapon) p.set('right', B.loadout.r_weapon.id);
  if (B.loadout.l_weapon) p.set('left', B.loadout.l_weapon.id);
  const sig = p.toString();
  // THE SAME LOADOUT ASKED TWICE IS ONE REQUEST. `fillActions` runs for the
  // default loadout and again for the first rebuild, so the page's own start
  // asks this twice for one body; aborting the first to start an identical
  // second threw away 1.6 s of server work and delayed the menu by the whole
  // second request (MEASURED 2026-09-29: 1.6 s aborted, then 7.3 s). Six
  // rapid body switches likewise collapsed to one loadout and six requests.
  if (motionSrcInflight && motionSrcFor === sig) return;
  if (motionSrcAbort) motionSrcAbort.abort();
  motionSrcAbort = new AbortController();
  motionSrcFor = sig;
  motionSrcInflight = true;
  // A VISIBLE LOADING STATE, because cold this costs ~154 s: it walks every
  // install on the box and every archive in them. An empty menu for two and
  // a half minutes does not read as "still working", it reads as broken --
  // which is how I first saw it myself.
  if (!sel.options.length) {
    sel.innerHTML = '<option value="">checking other installs…</option>';
    sel.disabled = true;
  }
  let d;
  try {
    d = await api('/api/motionsources?' + sig, { signal: motionSrcAbort.signal });
  } catch (e) {
    // Withdrawn by a newer loadout, or outrun by it: the newer call owns the
    // menu now and this one says nothing. AN ABORT IS NOT A FAILURE -- the
    // first cut wrote "this install" here for a request the page itself had
    // withdrawn, and the menu showed a failure while the real answer was
    // still on its way.
    if (motionSrcFor !== sig || (e && e.name === 'AbortError')) return;
    motionSrcInflight = false;
    sel.innerHTML = '<option value="">this install</option>';
    sel.disabled = false;
    return;
  }
  if (motionSrcFor !== sig) return;         // the loadout moved under us
  motionSrcInflight = false;
  sel.disabled = false;
  sel.innerHTML = '';
  const own = el('option', null,
    `this install (${d.localCount} motion${d.localCount === 1 ? '' : 's'})`);
  own.value = '';
  sel.appendChild(own);
  for (const s of d.sources || []) {
    const o = el('option', null, `${s.install} (+${s.supplies})`);
    o.value = s.install;
    o.title = `${s.install} carries ${s.supplies} motion file(s) this `
            + `install declares and does not ship. The BODY stays this `
            + `install's; only the motion is sourced there.`;
    sel.appendChild(o);
  }
  const wrap = document.getElementById('anim-src-wrap');
  if (wrap) {
    // HIDDEN WHEN THERE IS NOTHING TO OFFER, and only then. An empty menu
    // invites the reading that nothing is missing, which is a different
    // fact from "nothing elsewhere can supply it".
    wrap.style.display = (d.sources || []).length ? '' : 'none';
    wrap.title = `${d.foreignCount} motion(s) this loadout names are not `
               + `installed here; ${d.nowhereCount} are on no install on `
               + `this machine.`;
  }
  if (keep && Array.prototype.some.call(sel.options, o => o.value === keep)) {
    sel.value = keep;
  }
  sel.onchange = async () => {
    // Drop the cached clip and re-fetch: the whole point is to SEE the
    // difference, and a cache hit would show the previous source's frames.
    //
    // DRIVEN THROUGH THE SAME PATH THE ACTION MENU USES -- `rebuild` then
    // `ensureAnim` -- because /api/figure is asked for a pose AT this action
    // and everything derived from it (the stats line, the socket rows, the
    // how-it-is-built panel) otherwise keeps describing the previous source.
    // That panel is the one you read to check WHICH motion is in play, so a
    // stale answer there is the whole bug this feature exists to avoid.
    // My first version called `setAction`, a function that does not exist.
    animPause();
    B.anim.cache = {};
    B.anim.frame = 0;
    // REBUILD THE ACTION LIST FIRST. The marks (+ added, != different) are
    // computed against the SELECTED source, so a list left as it was would
    // describe the previous one -- and this control exists to answer "what
    // is different here", which a stale list answers wrongly rather than
    // not at all. Setting `.value` from inside does not re-fire change, so
    // this does not recurse.
    await fillActions();
    await rebuild({ reframe: false });
    await ensureAnim();
  };
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
      // Collect, for the slots whose contents are looked up in the weapon
      // tables. The owner had Change / Remove / inspect here and no way to
      // keep the weapon they were looking at.
      if (isWeaponSlot(s.name)) {
        const keep = el('button', 'ghost tiny', 'Collect…');
        keep.title = 'Keep this weapon and everything the tables link to it: '
                   + 'its appearance, motions, effects and alternate skins.';
        keep.addEventListener('click', ev => {
          ev.stopPropagation();      // the card itself opens the picker
          openWeaponCollect(s.name);
        });
        acts.appendChild(keep);
      }
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
  // A part THIS install does not have is left empty and NAMED -- never kept
  // to 404 silently on every render (the old "keep the id" rule, which is
  // what made a character from another install fail with nothing on screen).
  // Any other failure still keeps the id: a flaky request is not a verdict.
  const missing = [];
  let total = 0;
  for (const [slot, v] of Object.entries(B.loadout)) {
    if (!v || !v.id) continue;
    total++;
    try {
      const o = await api(`/api/option?slot=${encodeURIComponent(slot)}` +
                          `&id=${encodeURIComponent(v.id)}`);
      B.loadout[slot] = { ...v, name: o.name || v.id, detail: o.detail || '',
                          mesh: o.mesh, texture: o.texture };
    } catch (e) {
      if (e.status === 404) {
        missing.push(`${(B.slotById[slot] || {}).label || slot} ${v.id}`);
        delete B.loadout[slot];
      }
    }
  }
  if (missing.length) {
    const where = restoredFrom === 'link' ? 'This link'
      : restoredFrom === 'legacy' ? 'Your saved character (from before characters were saved per install)'
      : 'Your saved character';
    const here = (B.status && B.status.root) || 'this install';
    const n = missing.length;
    const tail = !B.loadout.body
      // No body, no character: init falls back to the default one, which
      // replaces the parts that DID exist too -- so say that, not "left empty".
      ? " The body is one of them, so the builder started from its default character instead."
      : ` ${n === 1 ? 'That slot was' : 'Those slots were'} left empty.`;
    toast(`${where}: ${n} of ${total} part${total === 1 ? '' : 's'} ` +
          `${n === 1 ? "doesn't" : "don't"} exist on ${here} (${missing.join(', ')}).` +
          tail, 9000);
  }
  return missing;
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

/** THE SAVED CHARACTER IS PER INSTALL (the owner, 2026-09-18: "fix the saved
 *  loadout issue"). It used to be ONE key for the whole browser, so a
 *  character saved while browsing 6609 came back on CCO, where its ids do not
 *  exist, and every part 404'd silently. Now each install remembers its own
 *  last character, keyed by the root being browsed; the old shared key is
 *  read once, as a fallback, and CHECKED (see `refreshLoadoutNames`). */
function loadoutKey() {
  const norm = x => String(x || '').replace(/[\\/]+/g, '/').replace(/\/$/, '').toLowerCase();
  const root = B.status && B.status.root;
  return root ? `${LOADOUT_KEY}@${norm(root)}` : LOADOUT_KEY;
}

function saveLoadout() {
  try { localStorage.setItem(loadoutKey(), JSON.stringify(B.loadout)); }
  catch (e) { /* ignore */ }
}

/** Where the character being restored came from: `link` (a copied link --
 *  could be from any install), `install` (this install's own save), or
 *  `legacy` (the pre-fix shared save -- could be from any install). */
let restoredFrom = null;

function restoreLoadout() {
  const fromUrl = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (fromUrl.get('body')) {
    const out = {};
    for (const [k, v] of fromUrl.entries()) out[k] = { id: v, table: k, name: v };
    B.loadout = out;
    restoredFrom = 'link';
    return out;
  }
  for (const [key, from] of [[loadoutKey(), 'install'], [LOADOUT_KEY, 'legacy']]) {
    try {
      const s = JSON.parse(localStorage.getItem(key) || 'null');
      if (s && typeof s === 'object') {
        B.loadout = Object.fromEntries(
          Object.entries(s).filter(([, v]) => v && v.id));
        restoredFrom = from;
        return B.loadout;
      }
    } catch (e) { /* ignore */ }
  }
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
  // the aura *intent* is a setting, not a character -- per hand, both cleared
  for (const s of AURA_HANDS) { aura(s).rec = null; aura(s).anchor = null; }
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
  // No button id: the header's "Collapse panels" control was removed. Every
  // card header still collapses its own card on click and `C` still toggles
  // all of them -- the button was a second way to say what the headers
  // already say, not the behaviour itself.
  CardPanels.init(COLLAPSE_KEY);
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

/** Slot -> the number key that opens its picker. One table rather than four
 *  `case` labels, because `stepQuality` tells the user which key moves the
 *  Colour & variants panel onto the hand they meant, and a hand-written second
 *  copy of that mapping is a copy that goes stale. */
const PICKER_KEYS = { body: '1', armet: '2', r_weapon: '3', l_weapon: '4' };
const SLOT_FOR_PICKER_KEY = Object.fromEntries(
  Object.entries(PICKER_KEYS).map(([slot, k]) => [k, slot]));

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
  const since = $('#picker-since-base') && $('#picker-since-base').value;
  if (since) {
    p.set('newSince', since);
    p.set('sinceMode', ($('#picker-since-mode') || {}).value || 'new');
  }
  return p;
}

/** "New since <install>": fill the picker's comparison list once, from the
 *  installs the user has declared, and remember the choice per browser. The
 *  current install is left out -- nothing is new relative to itself. */
let sinceFilled = false;
async function fillSinceBases() {
  const sel = $('#picker-since-base');
  if (!sel || sinceFilled) return;
  sinceFilled = true;
  let d;
  try { d = await api('/api/bases'); } catch (e) { return; }
  // The install being browsed is left out -- nothing is new relative to
  // itself -- matched by its ROOT from /api/status.
  const norm = x => String(x || '').replace(/[\\/]+/g, '/').replace(/\/$/, '').toLowerCase();
  const here = norm(B.status && B.status.root);
  for (const b of (d && d.paths) || []) {
    if (b.kind !== 'install' || (here && norm(b.detail) === here)) continue;
    const o = document.createElement('option');
    o.value = b.id;
    o.textContent = b.label || b.id;
    sel.appendChild(o);
  }
  let saved = '';
  try { saved = localStorage.getItem('co.picker.newSince') || ''; } catch (e) {}
  if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
  sel.addEventListener('change', () => {
    try { localStorage.setItem('co.picker.newSince', sel.value); } catch (e) {}
    loadPickerOptions();
  });
  const mode = $('#picker-since-mode');
  if (mode) {
    let savedMode = '';
    try { savedMode = localStorage.getItem('co.picker.sinceMode') || ''; } catch (e) {}
    if (savedMode) mode.value = savedMode;
    mode.addEventListener('change', () => {
      try { localStorage.setItem('co.picker.sinceMode', mode.value); } catch (e) {}
      if (sel.value) loadPickerOptions();
    });
  }
}

/** Say what the filter did -- or why it refused, e.g. a renumbered table. */
function renderSinceNote(data) {
  const note = $('#picker-since-note');
  if (!note) return;
  const ns = data.newSince;
  if (!ns) { note.textContent = ''; return; }
  const what = { new: 'new', changed: 'changed', both: 'new or changed' }[ns.mode] || 'new';
  if (!ns.comparable) { note.textContent = `not filtered: ${ns.why}`; return; }
  if (ns.state === 'computing') {
    // A content comparison reads files on both installs; say so, and ask
    // again until it lands -- the list below is NOT filtered yet.
    note.textContent = `comparing with ${ns.label}… ${ns.done} of ${ns.total} ` +
                       `(list not filtered yet)`;
    clearTimeout(renderSinceNote.t);
    renderSinceNote.t = setTimeout(() => {
      if (B.picker.open) loadPickerOptions();
    }, 1500);
    return;
  }
  if (ns.state === 'failed') { note.textContent = `comparison failed: ${ns.error}`; return; }
  const by = ns.how === 'content' ? ' (by content: this table was renumbered)' : '';
  const unk = ns.unknown ? ` · ${ns.unknown} with no readable art not counted` : '';
  note.textContent = `showing ${ns.shown} of ${ns.ofTotal} colour variants ` +
                     `${what} since ${ns.label}${by}${unk}`;
}

async function loadPickerOptions() {
  await fillSinceBases();
  const list = $('#picker-list');
  list.innerHTML = '<div class="mut small" style="padding:12px">loading…</div>';
  let data;
  try { data = await api('/api/options?' + pickerQuery().toString()); }
  catch (e) { list.innerHTML = ''; list.appendChild(el('div', 'err', e.message)); return; }
  B.picker.data = data;
  renderPickerFacets(data);
  renderSinceNote(data);
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
  // AN ACTIVE FILTER IS ALWAYS DRAWN. The owner, 2026-09-18: "filters will get
  // stuck or disappear when they shouldn't ... there are things filtered, but
  // not shown." Counts come from the FILTERED results, so a ticked value whose
  // count fell to 0 -- or an axis with no counts left -- used to vanish while
  // still filtering, leaving nothing to untick. Selected values and their
  // axes are drawn whatever the counts say, at 0 if need be.
  const selOf = a => B.picker.sel[a] || new Set();
  const axes = order.filter(a => data.facets[a] || selOf(a).size);
  for (const axis of axes) {
    const counts = data.facets[axis] || {};
    const box = el('div', 'facet-axis');
    box.appendChild(el('div', 'axis-name', (data.facetLabels || {})[axis] || axis));
    const pref = (data.facetOrder || {})[axis] || [];
    const keys = [...pref.filter(k => k in counts),
                  ...Object.keys(counts).filter(k => !pref.includes(k)).sort()];
    for (const v of selOf(axis)) if (!keys.includes(v)) keys.push(v);
    for (const v of keys) {
      const set = B.picker.sel[axis] || (B.picker.sel[axis] = new Set());
      const on = set.has(v);
      box.appendChild(chip(lbl(v), counts[v] || 0, on, () => {
        on ? set.delete(v) : set.add(v);
        loadPickerOptions();
      }));
    }
    host.appendChild(box);
  }
  const tc = Object.assign({}, data.tagCounts || {});
  for (const t of B.picker.tags) if (t !== ' untagged' && !(t in tc)) tc[t] = 0;
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
  paintPickerClear();
}

/** How many filters narrow the picker right now: facet values, tags, the
 *  search box and "New since". */
function pickerFilterCount() {
  let n = 0;
  for (const set of Object.values(B.picker.sel || {})) n += set ? set.size : 0;
  n += (B.picker.tags || new Set()).size;
  if ($('#picker-search') && $('#picker-search').value.trim()) n++;
  if ($('#picker-since-base') && $('#picker-since-base').value) n++;
  return n;
}

/** "Clear filters (N)" -- the owner asked for one button that undoes every
 *  filter; disabled, and saying so, when there is nothing to clear. */
function paintPickerClear() {
  const b = $('#picker-clear-filters');
  if (!b) return;
  const n = pickerFilterCount();
  b.disabled = n === 0;
  b.textContent = n ? `Clear filters (${n})` : 'No filters';
}

function clearPickerFilters() {
  B.picker.sel = {};
  B.picker.tags = new Set();
  if ($('#picker-search')) $('#picker-search').value = '';
  const since = $('#picker-since-base');
  if (since && since.value) {
    since.value = '';
    try { localStorage.setItem('co.picker.newSince', ''); } catch (e) {}
  }
  loadPickerOptions();
}

/** One item: the mesh, with its colours as swatches underneath. */
function garmentRow(g, index) {
  // THE THUMBNAIL SPANS BOTH LINES (the owner, 2026-09-18: "that thumbnail
  // could be 4x as big ... 2x2 ... and it would still be the same amount of
  // lines"). It sits in the entry's left column beside the name line AND the
  // colour-swatch line, twice as wide and twice as tall as before; the image
  // requested is the same, only drawn larger.
  const wrap = el('div', 'garment');
  const row = el('div', 'row-item');
  const img = setThumb(el('img', 'garment-thumb'), g.mesh || g.texture, 48);
  img.addEventListener('click', () => highlight(index, 0));
  const lb = el('div', 'lbl');
  lb.appendChild(el('b', null, g.name || g.meshName));
  const bits = [];
  if (g.detail) bits.push(g.detail);
  bits.push(`${g.count} colour${g.count === 1 ? '' : 's'}`);
  lb.appendChild(el('span', null, bits.join(' · ')));
  row.append(lb);
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
  wrap.append(img, row, strip);
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
  // Every path that changes what this panel shows comes through here, so this
  // is the one place that can record it. `Q` reads it -- see `stepQuality`.
  B.lookSlot = slot;
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
const weaponSlots = () => (B.config && B.config.weaponSlots) || WEAPON_SLOTS;
const isWeaponSlot = s => weaponSlots().includes(s);
/** A slot's own name for itself, from the server's slot list. Falls back to
 *  the raw slot so a slot the config has not described still reads. */
const slotLabel = s => (B.slotById[s] && B.slotById[s].label) || s;
/** Weapon slots with something in them, in the order the UI lists them. */
const armedWeaponSlots = () =>
  weaponSlots().filter(s => B.loadout[s] && B.loadout[s].id);

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

  // Name the hand in the heading. Two hands can hold two weapons at two
  // qualities, and "Quality" on its own does not say which one you are
  // looking at -- nor, therefore, which one `Q` is about to step.
  const head = el('div', 'axis-name', `Quality — ${slotLabel(slot)}`);
  head.title = `Q steps this ladder up, shift+Q down. It follows this panel, ` +
               `so it always means the ${slotLabel(slot).toLowerCase()}.`;
  host.appendChild(head);
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
  // Say what the shortcut acts on, next to the thing it acts on. `Q` used to
  // be the right hand whatever this panel showed, and there was nothing on
  // screen that would have told you so.
  host.appendChild(el('div', 'small mut',
    'Q / shift-Q step this ladder — whichever slot this panel is showing.'));

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
 *  the camera and the pose stay exactly where they were.
 *
 *  `where` prefixes the toast with the slot. Clicking a button in the ladder
 *  needs no such thing -- you clicked the ladder, so you know which one it was
 *  -- but a keystroke does, which is what `stepQuality` passes it for. */
async function setQuality(slot, q, { where = '' } = {}) {
  if (!q || !q.id) return;
  await equip(slot, q.id, { silent: true });
  toast((where ? `${where}: ` : '') + (q.quality
    ? `${q.label} — ${q.id}${q.aura ? ' · carries the aura' : ''}`
    : `${q.id}`));
  showLookFor(slot);
}

/** Which weapon `Q` steps.
 *
 *  ONE KEY, TWO HANDS. This used to read
 *
 *      B.loadout.r_weapon ? 'r_weapon' : (B.loadout.l_weapon ? 'l_weapon' : null)
 *
 *  which is the same collapsed one-weapon assumption the Super aura had before
 *  it was split per hand: with both hands full the left one was unreachable
 *  from the keyboard, and stepping it meant the picker or the per-hand *Switch
 *  to Super* button. `Q` and shift-`Q` are already up and down, so it cannot
 *  grow a second key the way the aura's `A` / shift-`A` did.
 *
 *  So it takes its hand from the **Colour & variants** panel instead, which is
 *  the slot you last touched and the one whose ladder is on screen with its
 *  name over it. No new mode, no new persisted state, and no invisible target:
 *  the panel IS the selector, `3` / `4` / a click on a slot move it, and the
 *  ladder says so under the buttons.
 *
 *  When that panel is on something with no ladder (the body, a helmet, an
 *  empty slot) the answer has to come from the loadout, and there it either
 *  is or is not ambiguous. Exactly one weapon equipped -> that one, because
 *  there is nothing to get wrong. More than one -> say so and name the way to
 *  choose, rather than quietly preferring a hand, which is the behaviour being
 *  removed. Returns `{slot}` or `{ask}`. */
function qualityStepTarget() {
  const shown = B.lookSlot;
  if (shown && isWeaponSlot(shown) && B.loadout[shown] && B.loadout[shown].id) {
    return { slot: shown };
  }
  const armed = armedWeaponSlots();
  if (!armed.length) return { ask: 'no weapon equipped' };
  if (armed.length === 1) return { slot: armed[0] };
  // A shield counts, so this list is not always two long.
  return { ask: `${andList(armed.map(slotLabel))} are ` +
                `${armed.length === 2 ? 'both' : 'all'} holding something. ` +
                `Q steps whichever one the Colour & variants panel is ` +
                `showing, so point it at the one you mean first: ` +
                `${armed.map(s => keyHintFor(s)).join('; ')}.` };
}

/** `a`, `a and b`, `a, b and c` — because a shield makes the list three long
 *  and "a and b and c" is not a sentence anyone writes. */
function andList(names) {
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** How to point the panel at a slot, in prose. Read off the same table the
 *  keys are bound from rather than spelled out twice -- and a slot with no
 *  key of its own (a shield) still gets a usable instruction rather than its
 *  bare name dropped into the middle of a sentence. */
function keyHintFor(slot) {
  const k = PICKER_KEYS[slot];
  return k ? `${k} for the ${slotLabel(slot).toLowerCase()}`
           : `click ${slotLabel(slot)} in the slot list`;
}

/** Step the weapon the Colour & variants panel is showing up or down its
 *  quality ladder. See `qualityStepTarget` for how that slot is chosen. */
async function stepQuality(delta) {
  const target = qualityStepTarget();
  if (target.ask) { toast(target.ask, 5000); return; }
  const slot = target.slot;
  const id = B.loadout[slot].id;
  const rec = await ensureQuality(slot, id);
  const avail = (rec.qualities || []).filter(q => q.available);
  if (avail.length < 2) {
    toast(`${slotLabel(slot)}: ` +
          (rec.reason || 'this weapon has no quality ladder'), 4000);
    return;
  }
  let i = avail.findIndex(q => q.id === id);
  if (i < 0) i = delta > 0 ? -1 : avail.length;
  const next = avail[Math.max(0, Math.min(avail.length - 1, i + delta))];
  if (!next || next.id === id) {
    // Already at the end of the ladder. Silence here reads as a dropped
    // keypress, and with two hands in play "which one did that?" is a real
    // question, so name the hand and where it is.
    toast(`${slotLabel(slot)}: already the ` +
          `${avail[delta > 0 ? avail.length - 1 : 0].label}`, 3000);
    return;
  }
  await setQuality(slot, next, { where: slotLabel(slot) });
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
  $('#gl-msg').textContent = 'building the figure…';
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
  // One /api/texbundle instead of 1 + N serial <img> loads, with the bytes
  // staying DXT from the archive to the GPU. Falls back to the PNG route per
  // texture -- see Viewer.applyTextureBundle in gl.js.
  await viewer.applyTextureBundle(
    [{ key: 'body', path: fig.body.texture }].concat(
      fig.parts.map(p => ({ key: 'slot:' + p.slot, path: p.texture }))),
    { pngUrl: texUrl, isCurrent: () => stillCurrent(tk) });
  if (!stillCurrent(tk)) return;
  // The figure is drawn; the animation for a NEW body is still on its way
  // (MEASURED 0.7-1.0 s for /api/anim the first time each body type is shown),
  // and before this the page went silent for exactly that stretch.
  $('#gl-msg').textContent = 'loading the animation…';
  $('#gl-stats').textContent = viewer.stats +
    `\n${fig.body.id}` + fig.parts.map(p => ` + ${p.slot}:${p.id}`).join('');
  renderDetailPanel(fig);
  // the weapon is part of the motion lookup, so the action list is re-derived
  await fillActions();
  await ensureAnim({ silent: true });
  if (stillCurrent(tk)) $('#gl-msg').classList.add('hidden');
  await refreshWeaponPanel();
  // The companion set is a property of the LOADOUT, not of the render, so it
  // is refreshed here and re-fetched only when what is equipped changed --
  // `compSignature` is what makes a camera nudge free.
  //
  // DEBOUNCED, and the stale request CANCELLED. MEASURED 2026-09-18 (owner:
  // "moving between bodies ... occasional slowdowns that feel awful"): each
  // body switch fired /api/buildersatellites at once, ~8 s of server work
  // each, and clicking through bodies stacked them -- 11-17.5 s apiece while
  // they overlapped, and the switch's own 6 ms /api/option took 5-8 s behind
  // them. Only the loadout the user STOPS on needs its companions.
  scheduleCompanions();
  if (anyAuraOn()) await applySuperFx();
  viewer.draw();
}

/** One texture, through the same DXT path a whole figure uses.
 *
 *  Delegating rather than keeping a second <img> loader means the model
 *  viewer (a monster is one mesh and one texture) and the effect layers get
 *  the compressed upload for free, and there is one fallback rule in the tree
 *  instead of two that can drift. */
function applyNamedTexture(key, texPath) {
  if (!texPath) return Promise.resolve(false);
  const tk = tokenNow();
  return viewer.applyTextureBundle([{ key, path: texPath }],
    { pngUrl: texUrl, isCurrent: () => stillCurrent(tk) })
    .then(r => r.uploaded > 0 || r.fallback.length > 0);
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
  // A socket correction is the viewer deliberately disagreeing with the
  // client. It is stated, in full, at the point it changes what you see --
  // an invisible correction is indistinguishable from a broken reader, and
  // this one must never be mistaken for how the real client behaves.
  for (const [sock, note] of Object.entries(fig.socketCorrections || {})) {
    const w = el('div', 'note');
    w.appendChild(el('b', null, `⚠ ${sock}: corrected, not what the client draws. `));
    w.appendChild(document.createTextNode(note));
    b.appendChild(w);
  }
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
  // WHICH ROUTE ANSWERED. `resolve()` has always returned it and `/api/anim`
  // has always sent it as `how`; nothing rendered it, and that silence is a
  // correctness problem rather than a missing detail.
  //
  // `MotionIndex.lookup` has a five-step fallback chain ending at the
  // universal idle, so an action this client ships NO motion for still comes
  // back with a clip: frames, aligned, four chunks -- indistinguishable from
  // a real animation. MEASURED on 5017, body 3135990 + bow 500218: actions
  // 010, 975 and 984 all resolve to `c3/0003/500/100.c3` via
  // "action -> 100 (idle)" and every one reports frames=21, aligned=true.
  //
  // That matters because the reason to watch an unidentified code is to NAME
  // it. Watching the idle and labelling it "975" is a wrong answer produced
  // by a viewer that looked like it worked -- and it would be written down.
  if (d && d.how) rows.push(['resolved by', d.how]);
  if (d && d.chain) rows.push(['chains with', `action ${d.chain}`]);
  b.appendChild(kv(rows));
  if (d && d.evidence) b.appendChild(el('div', 'small mut', d.evidence));
  // A FALLBACK IS A WARNING, NOT A ROW. `own` is the server's verdict on
  // `how` (`anim.route_is_own`, the one predicate); `how` is the route's
  // WORDING, shown in the row above and never compared here. This used to
  // test `d.how !== 'exact'`, and `resolve()` suffixes an exact hit when the
  // alias table answered -- `exact (weapon set 580 animates from set 560)`
  // -- so the panel said "Do not name 401 from this" over 401's own motion
  // (5517, body 002135000 + weapon 580001, 2026-09-25). A payload with no
  // verdict at all (an older server) stays unwarned rather than reading as
  // a fallback.
  if (d && d.own === false) {
    b.appendChild(el('div', 'warn',
      `⚠ NOT THIS ACTION'S OWN MOTION. Action ${d.action} resolves through ` +
      `the fallback chain (${d.how}), so what you are watching is ` +
      `${d.motion} — not a motion this client ships for ${d.action}. ` +
      `Do not name ${d.action} from this.`));
  }
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
  // THE MOTION'S SOURCE INSTALL, empty for "this one". The body is never
  // sourced elsewhere -- only the motion -- so what is on screen is always
  // this install's character wearing another patch level's animation.
  const src = (document.getElementById('anim-source') || {}).value || '';
  if (src) p.set('source', src);
  return p;
}

async function fetchClip(action) {
  if (B.mode === 'model') return fetchModelClip(action);
  // THE SOURCE IS PART OF THE CACHE KEY. Without it, switching install
  // would show the previous install's frames from cache and look like the
  // two were identical -- which is the exact question being asked.
  const key = `${B.loadout.body.id}:${action}:` +
              `${(B.loadout.r_weapon || {}).id || ''}:` +
              `${(B.loadout.l_weapon || {}).id || ''}:` +
              `${(document.getElementById('anim-source') || {}).value || ''}`;
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
  if (anyAuraOn()) placeSuperFx(d.sockets[f] || {});
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
//
// ONE AURA PER HAND, and they are independent.
//
// The second complaint was "I can only make one weapon super at a time".
// That was this file and only this file. `superfx.SLOT_SOCKET` has always
// mapped l_weapon -> v_l_weapon and r_weapon -> v_r_weapon, `SuperFxDB.anchor`
// is a static method taking the slot, and `/api/superfx?slot=` returns a
// different matrix for each hand. What did not exist was two states to hold
// the two answers in: `B.superfx` was one `{on, rec}`, one localStorage flag
// and one checkbox, and `equippedWeapon()` returned the right hand or, failing
// that, the left. So the left-hand aura was only reachable with the right hand
// empty, and it was never possible to have both.
//
// Everything below is keyed by hand. The one thing that must not be shared is
// the anchor: `placeSuperFx` used to write a single matrix into EVERY live
// effect instance, which with two auras would drag the left glow onto the
// right hand. Each instance now carries its own `slot` and reads its own
// hand's socket.

/** The two weapon hands, right first — the order the UI lists them in. */
const AURA_HANDS = ['r_weapon', 'l_weapon'];
/** Hand -> the `[Dumy]` socket its aura rides. Mirrors superfx.SLOT_SOCKET;
 *  `tools/superfx.py` is the authority and the server does the maths. */
const HAND_DUMY = { r_weapon: 'v_r_weapon', l_weapon: 'v_l_weapon' };
const HAND_LABEL = { r_weapon: 'right hand', l_weapon: 'left hand' };

/** This hand's aura state. Always an object, so callers never branch. */
const aura = slot => B.superfx.hands[slot] ||
                     (B.superfx.hands[slot] = { on: false, rec: null, anchor: null });

/** Every equipped weapon, right hand first. Both hands, not the first one. */
function equippedWeapons() {
  return AURA_HANDS.filter(s => B.loadout[s] && B.loadout[s].id)
                   .map(s => ({ slot: s, v: B.loadout[s] }));
}

const hasAura = slot => !!(aura(slot).rec && aura(slot).rec.found);
const anyAuraOn = () => AURA_HANDS.some(s => aura(s).on);
/** Hands that are switched on AND have something to show. */
const glowingHands = () =>
  AURA_HANDS.filter(s => aura(s).on && hasAura(s) && aura(s).rec.effect);

/** Why this hand's aura toggle is greyed, in that weapon's own terms. Never a
 *  bare "disabled": the whole point of the quality selector is that the answer
 *  is "you are not wearing the Super one", and it names the id that is. */
function auraWhyNot(slot) {
  const v = B.loadout[slot];
  if (!v || !v.id) {
    return `Put a weapon in the ${HAND_LABEL[slot]} and its aura, if it has ` +
           `one, toggles here. Each hand has its own switch.`;
  }
  const w = { slot, v };
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

/** Persist both hands. One JSON object rather than two keys, so a half-written
 *  pair is impossible and the legacy scalar has somewhere to land. */
function persistAura() {
  try {
    localStorage.setItem(AURA_KEY, JSON.stringify(
      Object.fromEntries(AURA_HANDS.map(s => [s, !!aura(s).on]))));
  } catch (e) { /* ignore */ }
}

/** Read the saved intent, migrating the pre-per-hand value.
 *
 *  The old key was the string '1' or '0' for the *one* aura there was. '1'
 *  meant "show the aura on whichever hand is holding the weapon", and with
 *  the fallback in the old `equippedWeapon()` that could be either hand — so
 *  it restores to both hands on, which reproduces what that setting did in
 *  both of the cases it could reach. */
function restoreAura() {
  let raw = null;
  try { raw = localStorage.getItem(AURA_KEY); } catch (e) { return; }
  if (raw === null) return;
  if (raw === '1' || raw === '0') {
    for (const s of AURA_HANDS) aura(s).on = raw === '1';
    persistAura();
    return;
  }
  let saved = null;
  try { saved = JSON.parse(raw); } catch (e) { return; }
  if (!saved || typeof saved !== 'object') return;
  for (const s of AURA_HANDS) aura(s).on = !!saved[s];
}

/** One toggle per hand, two places each: a checkbox in the viewport bar
 *  (always in sight) and the switch in that hand's Weapon effects block. Both
 *  drive the same per-hand state, and neither touches the other hand. */
function setAura(slot, on) {
  if (!HAND_DUMY[slot]) return;
  aura(slot).on = !!on;
  persistAura();
  applySuperFx();
  syncAuraControl();
  refreshWeaponPanel();
}

function toggleAura(slot) {
  if (!hasAura(slot)) { toast(auraWhyNot(slot), 5000); syncAuraControl(); return; }
  setAura(slot, !aura(slot).on);
}

function auraControls(slot) {
  return { lab: $(`#aura-label-${slot}`), box: $(`#chk-aura-${slot}`) };
}

function syncAuraControl() {
  for (const slot of AURA_HANDS) {
    const { lab, box } = auraControls(slot);
    if (!lab || !box) continue;
    const held = !!(B.loadout[slot] && B.loadout[slot].id);
    // Only shown for a hand that is holding something: an empty hand has no
    // aura to discuss and two permanent greyed boxes is noise.
    lab.classList.toggle('hidden', !held || B.mode === 'model');
    const ok = hasAura(slot);
    const on = ok && aura(slot).on;
    box.disabled = !ok;
    box.checked = on;
    lab.classList.toggle('on', on);
    lab.classList.toggle('nofx', !ok);
    const key = slot === 'l_weapon' ? 'Shift+A' : 'A';
    lab.title = ok
      ? (on
          ? `The ${aura(slot).rec.name} aura is on the ${HAND_LABEL[slot]} `
            + `weapon. (${key})`
          : `The ${HAND_LABEL[slot]} weapon carries the ${aura(slot).rec.name} `
            + `aura — tick to show it. (${key})`)
      : auraWhyNot(slot);
  }
}

/** Resolve one hand's aura against the server.
 *
 *  `weapon` / `offHand` are BOTH sent, and that matters for the left hand:
 *  the motion set is a property of the loadout, not of the id being asked
 *  about (`anim.AnimDB.weaponset(right, left)`), and the socket matrix comes
 *  out of whichever clip is resolved. Sending only `id=` let the server treat
 *  a left-hand weapon as the main hand and anchor the glow on a pose the body
 *  is not in. */
async function fetchAura(slot) {
  const v = B.loadout[slot];
  if (!v || !v.id) return null;
  const p = new URLSearchParams({ id: v.id, slot, action: B.anim.action,
                                  frame: String(B.anim.frame | 0) });
  if (B.loadout.body) p.set('body', B.loadout.body.id);
  if (B.loadout.r_weapon) p.set('weapon', B.loadout.r_weapon.id);
  if (B.loadout.l_weapon) p.set('offHand', B.loadout.l_weapon.id);
  try { return await api('/api/superfx?' + p.toString()); }
  catch (e) { return null; }                          // non-fatal
}

/** One block per hand, each with its own switch. Both hands are resolved on
 *  every refresh, so neither block can be showing the other's answer. */
async function refreshWeaponPanel() {
  const b = $('#fx-body');
  const held = equippedWeapons();
  b.innerHTML = '';
  if (!held.length) {
    b.appendChild(el('div', 'mut small', 'equip a weapon to see its effect'));
    for (const s of AURA_HANDS) { aura(s).rec = null; aura(s).anchor = null; }
    // The *intent* to show the aura is kept, per hand: re-equipping a Super
    // weapon brings that hand's glow straight back rather than making you
    // find the switch again.
    applySuperFx();
    syncAuraControl();
    return;
  }
  const tk = tokenNow();
  b.appendChild(el('div', 'mut small', 'resolving…'));
  const recs = await Promise.all(held.map(w => fetchAura(w.slot)));
  if (!stillCurrent(tk)) return;
  held.forEach((w, i) => { aura(w.slot).rec = recs[i]; });
  for (const s of AURA_HANDS) {
    if (!B.loadout[s] || !B.loadout[s].id) { aura(s).rec = null; aura(s).anchor = null; }
  }
  const qrecs = await Promise.all(held.map(w => ensureQuality(w.slot, w.v.id)));
  if (!stillCurrent(tk)) return;

  b.innerHTML = '';
  held.forEach((w, i) => renderHandFx(b, w, recs[i], qrecs[i]));

  // the per-attack trail and the impact spark are a different thing and the
  // browser page already resolves all three
  const a = el('a', 'small');
  a.href = '/#' + loadoutParams().toString();
  a.textContent = 'attack trail and impact spark → asset browser';
  b.appendChild(a);
  syncAuraControl();
}

function renderHandFx(b, w, sfx, qrec) {
  const slot = w.slot;
  qrec = qrec || {};
  const sec = el('div', 'handfx');
  b.appendChild(sec);
  sec.appendChild(el('h3', 'small', HAND_LABEL[slot]));
  const qname = qrec.current
    ? (B.config.qualityLabels || {})[qrec.current] || qrec.current : '';
  sec.appendChild(kv([
    ['weapon', `${w.v.name} (${w.v.id})`],
    ['quality', qname || 'not a quality id'],
  ]));

  // ---- the switch, whatever the answer is. One per hand, and it names the
  // hand: two identical "Super aura" switches would be a coin toss.
  const ok = hasAura(slot);
  const sw = el('label', 'bigtoggle' + (ok ? '' : ' nofx'));
  const cb = el('input');
  cb.type = 'checkbox';
  cb.checked = ok && aura(slot).on;
  cb.disabled = !ok;
  cb.addEventListener('change', () => setAura(slot, cb.checked));
  sw.appendChild(cb);
  sw.appendChild(el('b', null, `Super aura — ${HAND_LABEL[slot]}`));
  sw.appendChild(el('span', 'small mut',
    ok ? (aura(slot).on ? 'showing on this weapon' : 'this weapon has one')
       : 'not on this quality'));
  sec.appendChild(sw);

  if (sfx && sfx.found) {
    sec.appendChild(kv([
      ['effect', sfx.name],
      ['family', sfx.family],
      ['layers', sfx.layers.length],
      ['socket', HAND_DUMY[slot]],
      ['timing', `${sfx.frameIntervalMs} ms/frame, ` +
                 (sfx.endless ? 'endless' : `${sfx.loopTime}x`)],
    ]));
    sec.appendChild(el('div', 'note',
      (B.config && B.config.superAnchorNote) || ''));
    if (sfx.anchorSource) sec.appendChild(el('div', 'small mut', sfx.anchorSource));
    const strip = el('div', 'relstrip');
    for (const L of sfx.layers) {
      if (L.texture) strip.appendChild(fxCell(L.texture, `L${L.index}`));
    }
    sec.appendChild(strip);
  } else {
    // Say WHY there is nothing, and offer the one click that fixes it.
    sec.appendChild(el('div', 'pending', auraWhyNot(slot)));
    for (const q of (qrec.qualities || [])) {
      if (!q.available || !q.aura) continue;
      const jump = el('button', 'primary');
      jump.textContent = `Switch to ${q.label} (${q.id})`;
      jump.title = 'same weapon, same mesh — only the quality digit changes';
      jump.addEventListener('click', async () => {
        await setQuality(slot, q);
        setAura(slot, true);
      });
      sec.appendChild(jump);
    }
    if (sfx && sfx.note) sec.appendChild(el('div', 'small mut', sfx.note));
  }
  sec.appendChild(el('div', 'note',
    (B.config && B.config.auraQualityNote) || ''));
}

function fxCell(path, label) {
  const cell = el('div', 'cell');
  const img = setThumb(el('img'), path, 46);
  cell.appendChild(img);
  cell.appendChild(el('div', 'cap', label));
  cell.title = path;
  return cell;
}

/** Attach (or detach) the weapon effects through fx.js -- the same playback the
 *  asset browser uses, so it is the tested path rather than a second one.
 *
 *  One EffectInstance per glowing hand, each tagged with its `slot` and each
 *  carrying its own anchor. The texture keys are namespaced by hand as well:
 *  two auras whose layers both claimed `sfx:0` would fight over one entry in
 *  the viewer's texture map and the second hand would repaint the first. */
async function applySuperFx() {
  if (!viewer) return;
  const hands = glowingHands();
  if (!hands.length) {
    viewer.clearEffects();
    $('#fx-wrap').classList.add('hidden');
    cancelAnimationFrame(B.superfx.raf);
    viewer.draw();
    return;
  }
  const defs = [];
  for (const slot of hands) {
    const st = aura(slot);
    const keys = {};
    for (const lay of st.rec.effect.layers || []) {
      if (lay.texture) keys[lay.index] = `sfx:${slot}:${lay.index}`;
    }
    st.anchor = (st.rec.anchor && st.rec.anchor.length === 16)
      ? Array.from(st.rec.anchor) : FX.IDENT.slice();
    defs.push({ def: st.rec.effect, role: 'aura', slot,
                anchor: st.anchor, textureKeys: keys });
  }
  const n = viewer.setEffects(defs);
  for (const d of defs) {
    for (const lay of d.def.layers || []) {
      if (lay.texture) await applyNamedTexture(d.textureKeys[lay.index], lay.texture);
    }
  }
  // setEffects() clears the texture map, so the figure's skins go back on
  if (B.figure) {
    await viewer.applyTextureBundle(
      [{ key: 'body', path: B.figure.body.texture }].concat(
        B.figure.parts.map(p => ({ key: 'slot:' + p.slot, path: p.texture }))),
      { pngUrl: texUrl });
  }
  $('#fx-wrap').classList.toggle('hidden', !n);
  if (n) fxLoop();
}

/** Follow the hand: during playback the socket matrix changes every frame, and
 *  the effect is anchored to the socket, so it has to move with it.
 *
 *  Per hand, from that hand's own dummy. The old version read ONE socket and
 *  wrote it into every live instance, which is fine with one aura and wrong
 *  the moment there are two -- the left glow would be dragged onto the right
 *  hand and the two would sit on top of each other. */
function placeSuperFx(sockets) {
  if (!sockets) return;
  for (const slot of AURA_HANDS) {
    const st = aura(slot);
    if (!st.on || !hasAura(slot)) continue;
    const m = sockets[HAND_DUMY[slot]];
    if (!(m && m.length === 16)) continue;
    st.anchor = Array.from(m);
  }
  // Push it straight into the live instances. The effect's own rAF supplies
  // the anchor on every tick, but it is not running while the animation is
  // paused or being scrubbed — and a glow that only tracks the hand while
  // *both* loops happen to be running is exactly the class of bug this panel
  // exists to have fixed.
  for (const f of (viewer && viewer.fx) || []) {
    const st = B.superfx.hands[f.slot];
    if (st && st.anchor && f.anchor && f.anchor.length === 16) f.anchor.set(st.anchor);
  }
}

function fxLoop() {
  cancelAnimationFrame(B.superfx.raf);
  const t0 = performance.now();
  const hands = glowingHands();
  if (!hands.length) return;
  const durOf = st => Math.max(400,
    (st.rec.effect.effectiveFrames || st.rec.effect.frames || 1) *
    (st.rec.effect.frameIntervalMs || 41));
  // Two auras share one clock and one slider. They are loops, not a timeline,
  // so the longer of the two sets the period and the shorter simply repeats
  // inside it -- which is what the endless flag means anyway.
  const dur = Math.max(...hands.map(s => durOf(aura(s))));
  const names = hands.map(s => `${HAND_LABEL[s]} ${aura(s).rec.name}`).join(' + ');
  const step = () => {
    if (!anyAuraOn() || !viewer.fx.length) return;
    let t = performance.now() - t0;
    if (t > dur) { viewer.resetEffects(); }
    // Each instance rides ITS OWN hand's socket. One shared anchor here is
    // the same bug placeSuperFx had, arriving through the playback loop.
    viewer.setEffectTime(t % dur,
                         f => (B.superfx.hands[f.slot] || {}).anchor || null);
    $('#fx-label').textContent =
      `${Math.round(t % dur)} / ${Math.round(dur)} ms · ${names}`;
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
      // `mode` is NOT restored: it is the page now. A stale 'model' in
      // storage used to be able to open the builder on the other rail.
      B.adhoc = s.adhoc || null;
      B.model.key = s.key || '';
      B.model.action = s.action || '';
      B.model.kind = s.kind || '';
      B.model.q = s.q || '';
      B.model.zoom = +s.zoom || 100;
      B.model.row = s.row || null;
      if (s.distinctOnly !== undefined) B.model.distinctOnly = !!s.distinctOnly;
      if (s.source === 'path' || s.source === 'catalogue') B.model.source = s.source;
      B.model.path = s.path || '';
    }
  } catch (e) { /* ignore */ }
  // A link that names a model wins over whatever was last open.
  if (!IS_MODELS) return;               // the builder has no model to restore
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (h.get('model')) {
    B.adhoc = null;
    B.model.key = h.get('model');
    if (h.get('action')) B.model.action = h.get('action');
    if (h.get('zoom')) B.model.zoom = +h.get('zoom') || 100;
  } else if (h.get('mesh')) {
    // ...and a link that names a bare mesh path wins the same way.
    B.adhoc = { mesh: h.get('mesh'), tex: h.get('tex') || '' };
    B.model.key = '';
    if (h.get('zoom')) B.model.zoom = +h.get('zoom') || 100;
  }
}

function saveModelState() {
  try {
    localStorage.setItem(MODEL_KEY, JSON.stringify({
      mode: B.mode, key: B.model.key,
      // Only what identifies the mesh. `data` and `clip` are a decoded mesh
      // and its baked frames -- 298 KB for a monster, megabytes for a
      // 120-frame collected action -- and both are re-fetched on restore
      // regardless. Two callers (the ad-hoc ACTION select, setModelZoom) run
      // once those are attached, and overflowing the quota here fails into
      // the catch below: the page would simply stop remembering anything.
      adhoc: B.adhoc ? { mesh: B.adhoc.mesh, tex: B.adhoc.tex || '',
                         action: B.adhoc.action || '' } : null,
      action: B.model.action,
      kind: B.model.kind, q: B.model.q, zoom: B.model.zoom,
      row: B.model.row, distinctOnly: B.model.distinctOnly,
      source: B.model.source, path: B.model.path,
    }));
  } catch (e) { /* ignore */ }
}

/** Show the rails and cards this mode owns, and hide the other mode's.
 *  Nothing is destroyed: switching back finds the character exactly as it was,
 *  because the loadout and the model selection are separate state. */
function applyMode({ rebuild: doRebuild = true } = {}) {
  const model = B.mode === 'model';
  // Each page ships only its own rails and cards, so most of what this used
  // to toggle is simply absent on the other page. `hide` is null-tolerant on
  // purpose: the alternative is a list of ids that has to stay in step with
  // two documents, which is the drift this whole change exists to remove.
  const hide = (sel, on) => {
    const n = $(sel);
    if (n) n.classList.toggle('hidden', on);
  };
  hide('#slots-rail', model);
  hide('#model-rail', !model);
  hide('#card-model', !model);
  hide('#card-look', model);
  hide('#card-fx', model);
  hide('#clips-label', !model);
  // `B.model.kind` is the FILTER chip; the selected model's own kind lives on
  // its payload. Conflating the two is how a monster panel ends up on a ghost.
  hide('#card-monster',
       !(model && B.model.data && B.model.data.kind === 'monster'));
  if (model) {
    let had = false;
    for (const s of AURA_HANDS) {
      const lab = $(`#aura-label-${s}`);
      if (lab) lab.classList.add('hidden');
      if (aura(s).rec) { aura(s).rec = null; aura(s).anchor = null; had = true; }
    }
    if (had) viewer && viewer.clearEffects();
  }
  document.body.classList.toggle('mode-model', model);
  saveModelState();
  if (doRebuild) rebuild({ reframe: true });
}

/** Switching mode is now switching PAGE.
 *
 *  It used to swap one rail for another in place. That is what made "Model
 *  Viewer" a control on the builder rather than a place, which a tab bar
 *  cannot represent without lying. `M` and any remaining caller land on the
 *  other route instead; each page then boots in its own mode. */
function setMode(mode) {
  if (mode === B.mode) return;
  location.href = mode === 'model' ? '/models' : '/builder';
}

// ---------------------------------------------------------------- the list
async function loadModelList({ select = '' } = {}) {
  renderModelSource();
  if (B.model.source === 'path') return loadPathList();
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
  // Do NOT fall back to the first model while an ad-hoc mesh is open: that
  // auto-pick would count as a list selection and evict the very mesh the
  // link asked for.
  else if (data.models.length && !B.model.key && !B.adhoc) {
    highlightModel(0, { load: false });
  }
  $('#model-count').textContent =
    `${data.total} model${data.total === 1 ? '' : 's'} shown of ${data.pool}` +
    (data.models.length < data.total ? ` (first ${data.models.length})` : '');
}

/** Catalogue or by-path, and the controls each one needs. */
function renderModelSource() {
  const host = $('#model-source');
  if (!host) return;
  const byPath = B.model.source === 'path';
  host.innerHTML = '';
  host.appendChild(el('div', 'axis-name', 'List'));
  const pick = (src, label, why) => {
    const c = chip(label, null, B.model.source === src, () => {
      if (B.model.source === src) return;
      B.model.source = src;
      B.model.index = -1;
      saveModelState();
      loadModelList();
    });
    c.title = why;
    host.appendChild(c);
  };
  pick('catalogue', 'Catalogue',
       'Model families the client’s own layout describes: monsters, '
       + 'NPCs, ghosts, mounts, roles, effects.');
  pick('path', 'By path',
       'Every mesh under a path in the selected view — including art no '
       + 'catalogue names, like the Collection and the recovered archives.');
  $('#model-search').classList.toggle('hidden', byPath);
  $('#model-path').classList.toggle('hidden', !byPath);
  $('#model-kinds').classList.toggle('hidden', byPath);
  $('#model-crumbs').classList.toggle('hidden', !byPath);
  if (byPath && $('#model-path').value !== B.model.path) {
    $('#model-path').value = B.model.path;
  }
}

/** Every mesh under a path prefix, in whatever view is selected.
 *
 *  Deliberately the same `/api/files` the asset browser uses, so anything
 *  visible there is openable here; and rows open as ad-hoc meshes, which is
 *  the path that already handles geometry with no catalogue entry.
 */
async function loadPathList() {
  const host = $('#model-list');
  host.innerHTML = '<div class="mut small" style="padding:12px">loading…</div>';
  const prefix = (B.model.path || '').replace(/\\/g, '/').replace(/^\/+/, '');
  const p = new URLSearchParams({ ext: '.c3', limit: '400', unified: '0',
                                  dir: prefix.replace(/\/+$/, '') });
  let data, dirs = [];
  try {
    [data, dirs] = await Promise.all([
      api('/api/files?' + p.toString()),
      api('/api/dirs?ext=.c3').catch(() => []),
    ]);
  } catch (e) {
    host.innerHTML = '';
    host.appendChild(el('div', 'err', e.message));
    return;
  }
  B.model.dirs = dirs;
  // A motion file is not a model. They are still reachable by clearing the
  // filter, but listing an entry's twelve action files beside it turns a
  // folder of three NPCs into thirty-nine rows of mostly noise.
  const rows = data.rows.filter(r => !/__motion-[^/]*\.c3$/.test(r.path));
  const hiddenParts = data.rows.length - rows.length;
  B.model.list = rows.map(r => ({ key: 'path:' + r.path, path: r.path,
                                  label: r.path.split('/').pop(),
                                  dir: r.path.split('/').slice(0, -1).join('/'),
                                  kind: 'path' }));
  renderPathCrumbs(prefix);
  renderModelList();
  $('#model-count').textContent =
    `${rows.length} mesh${rows.length === 1 ? '' : 'es'} of ${data.total}`
    + (hiddenParts ? ` · ${hiddenParts} action file(s) not listed` : '');
  if (!rows.length) {
    host.innerHTML = '';
    host.appendChild(el('div', 'mut small',
      prefix ? `nothing under ${prefix} in this view`
             : 'no meshes in this view'));
  }
}

/** Clickable folders under the current prefix, so a path is navigable
 *  rather than something you have to already know how to spell. */
function renderPathCrumbs(prefix) {
  const host = $('#model-crumbs');
  if (!host) return;
  host.innerHTML = '';
  host.appendChild(el('div', 'axis-name', 'Folder'));
  const go = (to) => {
    B.model.path = to;
    $('#model-path').value = to;
    saveModelState();
    loadPathList();
  };
  if (prefix) {
    const up = prefix.replace(/\/+$/, '').split('/').slice(0, -1).join('/');
    host.appendChild(chip('↑ ' + (up || 'all'), null, false,
                          () => go(up ? up + '/' : '')));
  }
  const base = prefix.replace(/\/+$/, '');
  const seen = new Map();
  for (const d of (B.model.dirs || [])) {
    const dir = d.dir || '';
    if (base) {
      if (!dir.startsWith(base + '/')) continue;
    } else if (!dir) continue;
    const next = dir.slice(base ? base.length + 1 : 0).split('/')[0];
    if (!next) continue;
    seen.set(next, (seen.get(next) || 0) + d.count);
  }
  for (const [name, n] of [...seen].sort((a, b) => a[0].localeCompare(b[0]))) {
    host.appendChild(chip(name, n, false,
                          () => go((base ? base + '/' : '') + name + '/')));
  }
  if (!seen.size && !prefix) {
    host.appendChild(el('span', 'mut small', 'no folders in this view'));
  }
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
    row.appendChild(setThumb(el('img'), m.thumb || m.path, 48));
    const lb = el('div', 'lbl');
    lb.appendChild(el('b', null, m.label || m.key));
    const bits = [];
    if (m.kind === 'path') {
      // No clip or action counts here: they cost a parse per row, and this
      // list exists precisely for meshes the catalogue never measured.
      lb.appendChild(el('span', null, m.path));
      lb.appendChild(el('span', 'kindline', m.dir || '(root)'));
      row.appendChild(lb);
      row.addEventListener('click', () => highlightModel(i));
      host.appendChild(row);
      return;
    }
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
  // A by-path row IS an ad-hoc mesh -- same route a #mesh= link takes, which
  // is what lets geometry with no catalogue entry onto the stage at all.
  if (m.kind === 'path') {
    B.model.key = m.key;
    if (!B.adhoc || B.adhoc.mesh !== m.path) {
      B.adhoc = { mesh: m.path, tex: '' };
    }
    markModelSelection();
    const r = $('#model-list').querySelector(`.row-item[data-key="${cssEsc(m.key)}"]`);
    if (r) r.scrollIntoView({ block: 'nearest' });
    saveModelState();
    if (!load) { renderModelPanel(); return; }
    requestLoad(tk => {
      if (!stillCurrent(tk)) return;
      rebuildAdhoc({ reframe: true });
    }, { immediate });
    return;
  }
  // Only a real pick (one that loads) leaves the ad-hoc mesh. Restoring the
  // saved selection at boot passes load:false, and clearing on that would
  // evict the mesh a #mesh= link just asked for.
  if (load && B.adhoc) B.adhoc = null;
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
      actionLabel(a) +
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
      const o = el('option', null, `${actionLabel(a)} — file missing`);
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
  if (B.adhoc && B.adhoc.mesh) return rebuildAdhoc({ reframe });
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
    B.model.tex = null;   // a colour choice belongs to one model
    $('#card-monster').classList.toggle('hidden', info.kind !== 'monster');
  }
  if (info.kind === 'effect') return showEffectModel(info, tk);

  fillModelActions();
  const d = await fetchModelClip(B.model.action);
  if (!stillCurrent(tk)) return;
  if (!d || !d.frames || !d.scene || !d.scene.meshes.length) {
    // Clear the stage. Leaving the previous model's meshes up made a failed
    // clip read as "it switched back to the old model and stopped
    // animating" -- a wrong answer on screen beats an error only in a
    // corner nobody reads. An empty stage plus the reason is the truth.
    if (viewer) { viewer.clear(); viewer.draw(); }
    $('#gl-stats').textContent = '';
    $('#gl-msg').textContent =
      (d && d.error) || 'no motion ships for this action';
    $('#gl-msg').classList.remove('hidden');
    B.anim.seq = []; B.anim.data = null;
    renderModelPanel();
    renderAnimPanel();
    return;
  }

  const mat = zoomMatrix();
  const defs = d.scene.meshes.map(m => ({ meta: m, textureKey: 'model',
                                          matrix: mat }));
  viewer.setMeshes(defs, { frameOn: scaledBounds(d.bounds),
                           keepFraming: !reframe });
  await applyNamedTexture('model', B.model.tex || d.texture);
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
  renderCollect();
  viewer.draw();
}

// ------------------------------------------------------------- collection
//
// Same card as the asset browser's, driven from whatever the model stage is
// showing: an ad-hoc mesh opened by path, or a catalogued model's own files.
let collectionMeta = null;

// ------------------------------------------------------- satellite choosing
//
// Backlog section 7: the user chooses what comes with a collected asset.
// Everything below is driven by `/api/collectoffer`, and the three rules the
// owner set are honoured HERE ONLY BY DEFERRING TO THE SERVER, which is the
// point:
//
//  1. SELECT ALL DOES NOT REACH ANIMATIONS. `offer.selectAllKinds` is
//     `collection.select_all_kinds()` -- the single definition of that ruling
//     -- and `satSelectAll` ticks exactly those names. **This file holds no
//     list of satellite kinds at all.** Writing one here is how a ruling gets
//     honoured in the table and broken in the button, so a gate
//     (`tests/test_collect_checkboxes.py`) asserts that no kind name appears
//     literally in this file and that the select-all handler decides
//     membership by asking the server's set.
//  2. THE COST IS SHOWN BEFORE THE CLICK. Every row carries a count and a
//     size, and the footer totals the ticked ones, so select-all is not a
//     blind action.
//  3. AN ABSENT COMPANION IS SHOWN, NOT DROPPED. A row reads "0 of 3
//     present"; an absent item renders greyed with why. Section 6: a list of
//     six silently hiding three is a trap the user cannot detect.

/** How a kind's count reads, and the three states it must not collapse.
 *
 *  `available` arrives as the STRING "unknown" whenever nothing could count
 *  this kind -- either the whole resolve failed, or this particular kind's
 *  sources are classified-but-not-enumerated (a shared motion set is the
 *  measured case). Printing "none" for either is the lie this panel exists to
 *  avoid, and the string is what makes an accidental `0` impossible. */
function satCountText(row) {
  if (!row.measured) return 'not measured';
  if (row.unenumerated || row.available === 'unknown') return 'not enumerated';
  if (!row.available) return 'none';
  if (row.absent) return `${row.present} of ${row.available} present`;
  return String(row.present);
}

function satCountClass(row) {
  if (!row.measured || row.unenumerated || row.available === 'unknown') {
    return 'warn';
  }
  return row.available ? '' : 'mut';
}

/** Bytes, in the units a person uses. */
function satSize(n) {
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

/** The offer for one asset, plus which kinds are ticked. Keyed by subject so
 *  moving the stage and coming back does not resurrect a stale offer. */
let satState = null;

/** SELECT ALL.
 *
 *  The reachable set comes from the SERVER and nothing else. A kind is ticked
 *  if and only if it is in `selectAllKinds`, so the NOT RECOMMENDED group is
 *  left exactly as the user had it -- not cleared, not set: untouched, because
 *  select-all is a statement about the recommended group and says nothing
 *  about the other one. */
function satSelectAll(st) {
  const reach = new Set(st.offer.selectAllKinds || []);
  for (const row of st.offer.kinds || []) {
    if (reach.has(row.name)) st.picked[row.name] = true;
  }
}

/** SELECT NONE. Unticks everything, the NOT RECOMMENDED group included --
 *  clearing a deliberate choice is what "none" means, and unlike select-all it
 *  cannot surprise anyone by taking something extra. */
function satSelectNone(st) {
  for (const row of st.offer.kinds || []) st.picked[row.name] = false;
}

/** What ticking these kinds would cost right now. */
function satTotals(st) {
  let files = 0, bytes = 0, absent = 0, unsized = 0, unknown = 0;
  for (const row of st.offer.kinds || []) {
    if (!st.picked[row.name]) continue;
    if (!row.measured || row.unenumerated
        || typeof row.present !== 'number') { unknown += 1; continue; }
    files += row.present;
    bytes += row.bytes;
    absent += row.absent;
    unsized += row.unsized;
  }
  return { files, bytes, absent, unsized, unknown };
}

/** One checkbox row. */
function satRow(st, row, onChange) {
  const wrap = el('div', 'sat-row' + (row.selectAll ? '' : ' sat-optin'));
  const lab = el('label', 'chk');
  const box = el('input');
  box.type = 'checkbox';
  box.checked = !!st.picked[row.name];
  // Disabled only when the whole offer is UNMEASURED. Deliberately NOT
  // disabled for a kind that is merely `unenumerated`: the offer could not
  // count it, but a CALLER may still have its own list -- the weapon panel
  // sends core/weaponcollect's table-resolved motions, which is the only
  // authority that knows them. Greying the box there would take away a
  // capability this panel used to have, and would do it in the name of
  // honesty while actually collecting less.
  box.disabled = !row.measured;
  box.addEventListener('change', () => {
    st.picked[row.name] = box.checked;
    onChange();
  });
  lab.appendChild(box);
  lab.appendChild(el('b', null, ' ' + row.label));

  // The count, and it never reads as a measurement when it is not one.
  // `available` is the string "unknown" from the server precisely so that
  // arithmetic on it fails loudly instead of quietly printing 0.
  const n = el('span', 'sat-n ' + satCountClass(row));
  n.textContent = ' ' + satCountText(row);
  lab.appendChild(n);
  if (row.bytes) lab.appendChild(el('span', 'sat-b mut', ' · ' + satSize(row.bytes)));
  if (row.unsized) {
    lab.appendChild(el('span', 'sat-b mut',
      ` · ${row.unsized} of unknown size`));
  }
  wrap.appendChild(lab);

  if (!row.selectAll && row.whyNot) {
    wrap.appendChild(el('div', 'sat-why small mut', row.whyNot));
  }
  if (row.measured && row.unenumerated) {
    // The count is unknown; the box still works. Say both, or a user reads
    // "not enumerated" as "there is nothing here to tick".
    wrap.appendChild(el('div', 'sat-why small mut',
      'Nothing here could count these, so the number is unknown rather than '
      + 'zero. Ticking it still takes whatever this asset’s own tables '
      + 'name — for a weapon that is the motion rows the collector '
      + 'resolved — and the entry records the count as unknown.'));
  }
  for (const note of row.notes || []) {
    wrap.appendChild(el('div', 'sat-why small mut', note));
  }

  // The items, on demand. Absent ones are IN this list, greyed, with the
  // reason -- that is the whole of the bulletproof rule at row level.
  if ((row.items || []).length) {
    const more = el('button', 'ghost tiny', `list ${row.items.length}`);
    const box2 = el('div', 'sat-items hidden');
    more.addEventListener('click', () => {
      box2.classList.toggle('hidden');
      if (box2.childElementCount) return;
      for (const it of row.items) {
        const r = el('div', 'sat-item' + (it.present ? '' : ' absent'));
        r.appendChild(el('code', null, it.path || '(no file)'));
        const tail = [];
        if (!it.present) tail.push('ABSENT from this install');
        if (it.bytes) tail.push(satSize(it.bytes));
        if (it.form) tail.push(it.form);
        if (it.source) tail.push(it.source);
        if (it.note) tail.push(it.note);
        if (tail.length) r.appendChild(el('span', 'small mut', ' — ' + tail.join(' · ')));
        box2.appendChild(r);
      }
    });
    wrap.appendChild(more);
    wrap.appendChild(box2);
  }
  return wrap;
}

/** The whole chooser: recommended group, NOT RECOMMENDED group, the two
 *  buttons, the cost line and the honesty carries. */
function satPanel(host, st, onChange) {
  host.innerHTML = '';
  const offer = st.offer;

  if (!offer.measured) {
    // UNMEASURED is not "nothing to collect". Say which one this is, or the
    // empty panel reads as a complete answer.
    host.appendChild(el('div', 'sat-unmeasured warn small',
      'The companion set could not be resolved for this asset, so what is on '
      + 'offer is UNKNOWN — not empty. Collecting now takes the mesh and its '
      + 'primary skin, and the entry will say the offer was never measured.'));
  }

  const bar = el('div', 'sat-bar');
  const all = el('button', 'ghost tiny', 'Select all');
  all.title = 'Tick every recommended kind. It does not reach the '
            + 'NOT RECOMMENDED group below.';
  all.addEventListener('click', () => { satSelectAll(st); onChange(); });
  const none = el('button', 'ghost tiny', 'Select none');
  none.addEventListener('click', () => { satSelectNone(st); onChange(); });
  bar.appendChild(all);
  bar.appendChild(none);
  host.appendChild(bar);

  const reach = new Set(offer.selectAllKinds || []);
  const main = el('div', 'sat-group');
  const optin = el('div', 'sat-group sat-notrec');
  for (const row of offer.kinds || []) {
    (reach.has(row.name) ? main : optin).appendChild(
      satRow(st, row, onChange));
  }
  host.appendChild(main);
  if (optin.childElementCount) {
    const h = el('div', 'sat-head warn small', 'NOT RECOMMENDED');
    h.title = 'Exportable, and opt-in only. Select all does not reach this.';
    host.appendChild(h);
    host.appendChild(el('div', 'small mut',
      'Select all does not touch these. Tick one deliberately if you mean it.'));
    host.appendChild(optin);
  }

  const t = satTotals(st);
  const cost = el('div', 'sat-cost');
  cost.textContent = `Would collect ${t.files} file(s)`
    + (t.bytes ? `, ${satSize(t.bytes)}` : '')
    + (t.unsized ? ` (+${t.unsized} of unknown size)` : '');
  host.appendChild(cost);
  if (t.absent) {
    host.appendChild(el('div', 'small warn',
      `${t.absent} ticked companion(s) are declared but ABSENT from this `
      + 'install. They cannot be copied; the entry will record that they were '
      + 'on offer, so the gap stays visible.'));
  }
  if (t.unknown) {
    host.appendChild(el('div', 'small warn',
      `${t.unknown} ticked kind(s) were not measured.`));
  }
  // The two carries that survive every filter, exactly as `assetroot` keeps
  // them. Without these a tidy list reads as an exhaustive one.
  if (offer.unresolvedCount) {
    host.appendChild(el('div', 'small warn',
      `and ${offer.unresolvedCount} I could not resolve — named by this `
      + 'install, resolving to no file here.'));
  }
  if ((offer.limits || []).length) {
    const b = el('button', 'ghost tiny', `what was not enumerated (${offer.limits.length})`);
    const d = el('ul', 'sat-limits hidden');
    b.addEventListener('click', () => {
      d.classList.toggle('hidden');
      if (d.childElementCount) return;
      for (const L of offer.limits) d.appendChild(el('li', 'small mut', L));
    });
    host.appendChild(b);
    host.appendChild(d);
  }
  if (st.report) {
    const held = el('div', 'sat-held small');
    held.appendChild(el('b', null, 'Already collected: '));
    held.appendChild(document.createTextNode(
      st.report.filter(r => r.taken || r.state !== 'none')
        .map(r => `${r.kind} ${r.taken} of `
                + (r.available === 'unknown' ? '? (availability unknown)'
                                             : r.available))
        .join(' · ') || 'nothing yet'));
    host.appendChild(held);
  }
}

// ------------------------------------- the Builder's "what goes with this"
//
// Backlog section 6, step 4 -- the hard one, and it is hard for one reason:
// **the Builder's subject is a COMPOSITION, not an asset.** So this panel is
// per-slot, and three things follow that the Model Viewer's version does not
// have to deal with:
//
//  * A slot whose art does not ship for the chosen body is GREYED WITH ITS
//    REASON. That greying already exists (`slotCard`, `s.usable` / `s.reason`
//    out of `/api/builder`), and this builds on it rather than forming a
//    second opinion: the server carries the SAME `slot_info()` verdict into
//    the response and the panel prints it verbatim.
//  * A satellite can belong to two slots at once. In a composition two parts
//    can reference the same texture, and a user who exports or edits it under
//    `body` has also changed it under `weapon`. That is invisible in a
//    per-asset view by construction, so the server computes it across the
//    whole loadout and it is shown at the top, not in a footnote.
//  * The honesty carries have to TOTAL. Six slots each saying "and 2 I could
//    not resolve" still needs one number, or the summary reads as complete.

let compFor = '';          // the loadout signature the panel was built for
let compData = null;

/** A stable signature of the current composition, so the panel is not rebuilt
 *  on every stage event -- only when what is equipped actually changes. */
function compSignature() {
  return B.slots.map(s => {
    const v = B.loadout[s.name];
    return `${s.name}=${v ? (v.mesh || v.id) : ''}`;
  }).join('|');
}

/** The card, created once and inserted into the right rail. Built here rather
 *  than in builder.html so this feature is one file. */
function compCard() {
  let card = $('#card-companions');
  if (card) return card;
  const rail = $('#right');
  if (!rail) return null;
  card = el('section', 'card');
  card.id = 'card-companions';
  card.dataset.card = 'companions';
  const h = el('h2');
  const tog = el('button', 'cardtoggle', '▾');
  tog.setAttribute('aria-expanded', 'true');
  h.appendChild(tog);
  h.appendChild(document.createTextNode('What goes with this'));
  card.appendChild(h);
  const bodyEl = el('div', 'cardbody');
  bodyEl.id = 'companions-body';
  card.appendChild(bodyEl);
  const anchor = $('#card-look');
  rail.insertBefore(card, anchor || rail.firstChild);
  return card;
}

function compSlotBlock(rec) {
  const wrap = el('div', 'comp-slot' + (rec.usable ? '' : ' disabled'));
  const head = el('div', 'comp-head');
  head.appendChild(el('b', null, rec.label || rec.slot));
  wrap.appendChild(head);

  if (!rec.usable) {
    // The existing greying, carried through rather than restated. The reason
    // is the one `/api/builder` already gives the slot card.
    wrap.appendChild(el('div', 'small mut',
      rec.reason || 'no art for this slot ships in this build'));
    return wrap;
  }
  if (!rec.mesh) {
    wrap.appendChild(el('div', 'small mut', rec.note || 'nothing equipped'));
    return wrap;
  }
  head.appendChild(el('code', 'small mut', ' ' + rec.mesh));
  const offer = rec.offer || {};
  if (!offer.measured) {
    wrap.appendChild(el('div', 'small warn',
      'the companion set for this part could not be resolved — UNKNOWN, '
      + 'not empty'));
    return wrap;
  }
  const rows = el('div', 'comp-kinds');
  for (const row of offer.kinds || []) {
    // EVERY kind prints, the empty ones included. "No effects were found" and
    // "effects were not looked for" are different answers, and a row that
    // disappears when it is empty says neither.
    const r = el('div', 'comp-kind');
    r.appendChild(el('span', 'comp-k', row.label));
    r.appendChild(el('span', row.absent ? 'comp-v warn'
                                        : ('comp-v ' + satCountClass(row)),
                     satCountText(row)));
    if (row.bytes) r.appendChild(el('span', 'comp-v mut', satSize(row.bytes)));
    if (!row.selectAll) {
      // "opt-in", not "shared": the chip is about how SELECT ALL treats this
      // group, and a chip reading "shared" beside a satellite list is read as
      // "this file is shared between two slots", which is a different fact
      // that this panel reports separately and must not be confused with.
      const c = el('span', 'chip', 'opt-in');
      c.title = row.whyNot;
      r.appendChild(c);
    }
    rows.appendChild(r);
    // The notes carry the reason a row is empty or unknown. Hiding them is
    // how "Animations: none" gets printed about a body that plainly animates
    // -- its motion is a SHARED set that MotionBinding classifies and does
    // not enumerate. Measured live on CCO 2.0, 2026-09-07.
    for (const note of row.notes || []) {
      rows.appendChild(el('div', 'comp-absent small mut', note));
    }
    for (const it of (row.items || []).filter(i => !i.present)) {
      // Declared and absent: shown, with the reason. Section 6's rule.
      const a = el('div', 'comp-absent small mut');
      a.appendChild(el('code', null, it.path || '(no file)'));
      a.appendChild(document.createTextNode(
        ' — ABSENT' + (it.note ? ': ' + it.note : '')));
      rows.appendChild(a);
    }
  }
  wrap.appendChild(rows);
  if (offer.unresolvedCount) {
    wrap.appendChild(el('div', 'small warn',
      `and ${offer.unresolvedCount} I could not resolve`));
  }
  return wrap;
}

function renderCompanions(data) {
  const host = $('#companions-body');
  if (!host) return;
  host.innerHTML = '';
  if (!data) {
    host.appendChild(el('div', 'mut small', 'working it out…'));
    return;
  }
  const equipped = (data.slots || []).filter(s => s.mesh).length;
  host.appendChild(el('div', 'mut small',
    `${equipped} part(s) equipped. Each one's companions are listed under it; `
    + 'a part whose art does not ship for this body is greyed with the '
    + 'reason, exactly as its slot card is.'));

  // Shared satellites FIRST, because the whole hazard is that a user acts on
  // one part without knowing they are acting on another.
  if ((data.shared || []).length) {
    const box = el('div', 'comp-shared warn');
    box.appendChild(el('b', null,
      `${data.shared.length} file(s) belong to more than one part`));
    for (const s of data.shared) {
      const r = el('div', 'small');
      r.appendChild(el('code', null, s.path));
      r.appendChild(document.createTextNode(' — ' + s.slots.join(', ')
        + '. Editing or replacing it changes all of them.'));
      box.appendChild(r);
    }
    host.appendChild(box);
  }

  for (const rec of data.slots || []) host.appendChild(compSlotBlock(rec));

  if (data.unresolvedCount) {
    host.appendChild(el('div', 'warn',
      `and ${data.unresolvedCount} I could not resolve across this character `
      + '— named by this install, resolving to no file here.'));
  }
  if ((data.limits || []).length) {
    const b = el('button', 'ghost tiny',
      `what was not enumerated (${data.limits.length})`);
    const d = el('ul', 'sat-limits hidden');
    b.addEventListener('click', () => {
      d.classList.toggle('hidden');
      if (d.childElementCount) return;
      for (const L of data.limits) d.appendChild(el('li', 'small mut', L));
    });
    host.appendChild(b);
    host.appendChild(d);
  }
}

/** Ask the server for this composition's companion sets.
 *
 *  Every slot is sent, equipped or not, so the panel can show a greyed slot
 *  with its reason rather than silently omitting it -- an omitted slot reads
 *  as "nothing goes there", which is a different claim from "no art for this
 *  slot ships in this build". */
let compTimer = null;
let compAbort = null;
/** Ask for companions once the loadout has been still for COMP_SETTLE_MS. */
const COMP_SETTLE_MS = 700;
function scheduleCompanions() {
  clearTimeout(compTimer);
  compTimer = setTimeout(() => refreshCompanions(), COMP_SETTLE_MS);
}

async function refreshCompanions({ force = false } = {}) {
  if (IS_MODELS) return;                    // the Model Viewer has its own
  if (!compCard()) return;
  const sig = compSignature();
  if (!force && sig === compFor) return;
  compFor = sig;
  // A request for a loadout the user has already left is dropped, not left to
  // finish: the page never renders it (`compFor !== sig` below), and closing
  // it frees the browser's connection for the requests that matter now.
  if (compAbort) compAbort.abort();
  compAbort = new AbortController();
  renderCompanions(null);
  const items = B.slots.map(s => {
    const v = B.loadout[s.name] || {};
    return { slot: s.name, mesh: v.mesh || '', appearance: v.id || '' };
  });
  try {
    const data = await api('/api/buildersatellites', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }), signal: compAbort.signal });
    if (compFor !== sig) return;            // the loadout moved under us
    compData = data;
    renderCompanions(data);
  } catch (e) {
    if (compFor !== sig) return;
    const host = $('#companions-body');
    if (!host) return;
    host.innerHTML = '';
    // A failure is UNKNOWN, never "nothing goes with this".
    host.appendChild(el('div', 'warn',
      'The companion set could not be worked out: ' + e.message
      + '. That is not the same as "nothing goes with this character".'));
  }
}

/** Fetch the offer for one subject. Never throws into the caller: a failure
 *  becomes an UNMEASURED offer, which the panel renders as "unknown" rather
 *  than as an empty list. */
async function satFetch(mesh, appearance) {
  const p = new URLSearchParams({ path: mesh });
  if (appearance) p.set('appearance', appearance);
  // THE EQUIPPED COMPOSITION, so the Animations row can be ENUMERATED rather
  // than only classified. Which motion set animates a body depends on what is
  // in its hands, and this panel is per-SLOT -- so without these three the
  // resolver can only answer "classified, not enumerated", which is what the
  // owner read as "no animations" in this card AFTER the same bug was fixed
  // in the Import/Export pop-out. One surface fixed and not the other leaves
  // the identical sentence on screen somewhere else.
  //
  // `bodyAsset` is the subject string the body slot was sent as: the Builder
  // sends MESH PATHS while the loadout names APPEARANCE ids, and appearance
  // 002132300 resolves to mesh 002135000 -- two numbers that never match.
  if (B.loadout && B.loadout.body && B.loadout.body.id) {
    p.set('body', String(B.loadout.body.id));
    const bm = B.loadout.body.mesh || String(B.loadout.body.id);
    if (bm) p.set('bodyAsset', bm);
    if (B.loadout.r_weapon && B.loadout.r_weapon.id) {
      p.set('right', String(B.loadout.r_weapon.id));
    }
    if (B.loadout.l_weapon && B.loadout.l_weapon.id) {
      p.set('left', String(B.loadout.l_weapon.id));
    }
  }
  try {
    return await api('/api/collectoffer?' + p.toString());
  } catch (e) {
    return { subject: mesh, measured: false, kinds: [], selectAllKinds: [],
             notRecommended: [], unresolvedCount: 0,
             limits: [`the offer could not be fetched: ${e.message}`] };
  }
}

function currentCollectable() {
  if (B.adhoc && B.adhoc.mesh) {
    return { mesh: B.adhoc.mesh, tex: B.adhoc.resolvedTex || B.adhoc.tex || '',
             name: B.adhoc.mesh.split('/').pop().replace(/\.c3$/i, '') };
  }
  const d = B.model.data;
  if (d && d.mesh) {
    // `appearance` lets the offer consult WeaponEffect.ini / Action3DEffect.ini
    // for this id. Without it those tables are not read at all, and the offer
    // says so as a limit rather than reporting zero hit effects.
    return { mesh: d.mesh, tex: d.texture || '', name: d.label || d.key || '',
             appearance: d.ident || '' };
  }
  return null;
}

/* The satellite panels' own layout. Injected from here rather than added to
 * style.css so this feature is one file; it sets structure only and takes
 * every colour from the page's existing variables, so it cannot fight a theme
 * change made in the stylesheet. */
(function satStyles() {
  if (document.getElementById('sat-style')) return;
  const s = el('style');
  s.id = 'sat-style';
  s.textContent = `
.sat-panel { margin-top: 8px; border-top: 1px solid var(--line, #333); padding-top: 6px; }
.sat-bar { display: flex; gap: 4px; margin-bottom: 4px; }
.sat-row { padding: 2px 0; }
.sat-row .chk { display: block; }
.sat-n { font-variant-numeric: tabular-nums; }
.sat-why { margin: 0 0 3px 18px; }
.sat-head { margin-top: 8px; font-weight: 700; letter-spacing: .04em; }
.sat-notrec { border-left: 2px solid var(--warn, #e6b455); padding-left: 6px; }
.sat-items { margin: 2px 0 4px 18px; max-height: 180px; overflow: auto; }
.sat-item { padding: 1px 0; word-break: break-all; }
.sat-item.absent { opacity: .6; text-decoration-line: line-through;
                   text-decoration-color: var(--warn, #e6b455); }
.sat-cost { margin-top: 6px; font-weight: 700; }
.sat-limits { margin: 4px 0 0 14px; padding: 0; }
.sat-held { margin-top: 6px; }
.comp-slot { border-top: 1px solid var(--line, #333); padding: 5px 0; }
.comp-slot.disabled { opacity: .55; }
.comp-head { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
.comp-kind { display: flex; gap: 6px; align-items: baseline; font-size: 11px; }
.comp-k { flex: 1 1 auto; }
.comp-v { font-variant-numeric: tabular-nums; }
.comp-absent { margin-left: 10px; word-break: break-all; }
.comp-shared { border: 1px solid var(--warn, #e6b455); padding: 5px; margin-bottom: 6px; }
.hidden { display: none; }
`;
  document.head.appendChild(s);
})();

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

let collectFor = null;

/** Which render of this card is the current one.
 *
 *  `renderCollect` awaits before it appends anything, and the "already drawn"
 *  guard below cannot see a render that is still inside that await -- it looks
 *  for an `<input>` that has not been created yet. So two calls arriving while
 *  `/api/keep` is in flight both proceeded, and the card was drawn TWICE: two
 *  category pickers, two Collect buttons, two satellite choosers each with
 *  their own selection, and `satState` left holding whichever finished last.
 *  Observed live on the model viewer, 2026-09-07.
 *
 *  A sequence number is the fix that does not depend on what has been
 *  appended yet: every await re-checks it, and a superseded render stops. */
let collectSeq = 0;

async function renderCollect({ force = false } = {}) {
  const host = $('#collect-body');
  if (!host) return;
  const subj = (currentCollectable() || {}).mesh || '';
  // See app.js: a rebuild discards half-typed input, and this is
  // called whenever the stage changes for any reason.
  if (!force && collectFor === subj && host.querySelector('input')) {
    return;
  }
  const seq = ++collectSeq;
  collectFor = subj;
  if (!collectionMeta) {
    try { collectionMeta = await api('/api/keep'); }
    catch (e) {
      if (seq !== collectSeq) return;
      host.innerHTML = '';
      host.appendChild(el('div', 'mut small', 'unavailable'));
      return;
    }
  }
  // Cleared only once this render is committed to drawing, and only if it is
  // still the current one. Clearing before the await left the card blank for
  // the duration of a slow fetch and then filled it twice.
  if (seq !== collectSeq) return;
  host.innerHTML = '';
  if (!collectionMeta.library) {
    host.appendChild(el('div', 'mut small',
      'No COmmunity Library configured.'));
    return;
  }
  const total = Object.values(collectionMeta.counts || {})
    .reduce((a, b) => a + b, 0);
  host.appendChild(el('div', 'mut small', `${total} collected`));

  const cur = currentCollectable();
  if (!cur) {
    host.appendChild(el('div', 'mut small', 'Nothing on the stage to collect.'));
    return;
  }
  // Matched by the path it came from OR by the Collection's own copy, so the
  // view you go to in order to look at what you kept is not the one view
  // that fails to recognise it. See Swap.entryFor.
  const hit = Swap.entryFor(collectionMeta.entries, cur.mesh);
  const existing = hit && hit.entry;
  if (hit && hit.isCopy) { renderCollectedCopy(host, existing, cur); return; }

  const catSel = el('select');
  for (const c of Object.keys(collectionMeta.categories || {})) {
    const o = el('option', null, c);
    o.value = c;
    catSel.appendChild(o);
  }
  catSel.value = existing ? existing.category : guessCategory(cur.mesh);
  const nameIn = el('input');
  nameIn.type = 'text';
  nameIn.placeholder = 'name (optional)';
  nameIn.value = existing ? existing.name : cur.name;
  nameIn.style.width = '100%';
  nameIn.style.marginTop = '4px';
  const go = el('button', 'primary', existing ? 'Update entry' : 'Collect');
  go.style.marginTop = '6px';
  go.addEventListener('click', async () => {
    go.disabled = true;
    try {
      const body = { path: cur.mesh, texture: cur.tex,
                     category: catSel.value, name: nameIn.value.trim() };
      // `kinds` is sent ONLY when the chooser is on screen. Its absence means
      // "collect as this tool always did", so nothing about an older page or
      // the CLI changes; its presence is the user's explicit selection.
      if (satState && satState.subject === cur.mesh) {
        body.kinds = (satState.offer.kinds || [])
          .filter(r => satState.picked[r.name]).map(r => r.name);
        if (cur.appearance) body.appearance = cur.appearance;
      }
      const r = await api('/api/keep/add', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) });
      collectionMeta = null;
      const np = (r.entry.parts || []).length;
      toast(`collected ${r.entry.id}` + (np ? ` (+${np} motion/effect files)` : ''));
      renderCollect({ force: true });
    } catch (e) {
      toast('collect failed: ' + e.message, 5000);
      go.disabled = false;
    }
  });
  host.appendChild(catSel);
  host.appendChild(nameIn);

  // The chooser. Loaded after the buttons are on screen so a slow resolve on
  // a cold install never delays the thing the user came here to click; until
  // it lands the row says so, because a blank space would read as "there is
  // nothing to choose".
  const satHost = el('div', 'sat-panel');
  satHost.appendChild(el('div', 'mut small', 'working out what goes with this…'));
  host.appendChild(satHost);
  (async () => {
    const offer = await satFetch(cur.mesh, cur.appearance || '');
    // Both checks: the stage may have moved, and a second render of the SAME
    // subject may have superseded this one.
    if (collectFor !== subj || seq !== collectSeq) return;
    const st = { subject: cur.mesh, offer, picked: {},
                 report: (offer.entry || {}).report || null };
    // Nothing is ticked by default. A default selection is a decision made
    // for the user, and the one thing it must never quietly include is in the
    // NOT RECOMMENDED group; starting from the server's recommended set is
    // both explicit and honours the ruling.
    satSelectAll(st);
    satState = st;
    const redraw = () => satPanel(satHost, st, redraw);
    redraw();
  })();

  host.appendChild(go);
  const diag = el('button', 'ghost tiny', 'Test connection');
  diag.style.marginLeft = '6px';
  diag.addEventListener('click', () => diagnoseCollect(host));
  host.appendChild(diag);
  host.appendChild(el('div', 'mut small', cur.mesh));
  if (existing) {
    const info = el('div', 'mut small');
    info.style.marginTop = '4px';
    info.textContent = `already collected as ${existing.id}`
      + ((existing.parts || []).length
         ? ` with ${existing.parts.length} motion/effect file(s)` : '');
    host.appendChild(info);

    // This page could collect and nothing else -- no replace, no remove, no
    // drawer -- which is what "my mod staging is gone" describes from the
    // page where you actually decide one model should stand in for another.
    const panel = el('div', 'swap-panel');
    panel.style.marginTop = '8px';
    const swap = el('button', 'ghost tiny', 'Replace an asset with this…');
    swap.title = 'Write this entry over the asset it replaces, choosing '
               + 'whether its skin and animations travel with it.';
    swap.addEventListener('click', () => {
      if (panel.childElementCount) { panel.innerHTML = ''; return; }
      Swap.replacePanel(panel, existing,
                        { defaultTarget: existing.swapFor || cur.mesh });
    });
    host.appendChild(swap);

    const drop = el('button', 'ghost tiny', 'Remove');
    drop.style.marginLeft = '6px';
    drop.title = 'Drop this entry from the Collection and delete its files.';
    drop.addEventListener('click', async () => {
      try {
        await api('/api/keep/remove', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: existing.id }) });
        collectionMeta = null;
        toast('removed from the collection');
        renderCollect({ force: true });
      } catch (e) { toast('remove failed: ' + e.message, 5000); }
    });
    host.appendChild(drop);
    host.appendChild(panel);
  }
}

// ------------------------------------------------------------ weapon collect
//
// Everything below renders by reading a STATUS, never by asking whether a
// list came back empty.
//
// That is not style. `empty` and `unavailable` both carry zero rows and mean
// opposite things -- "weaponeffect.ini was consulted and has nothing for this
// weapon" versus "no table that could answer this ships here" -- and a third
// zero-row case, the resolver not being wired in at all, means a third thing
// again. A renderer keyed on `rows.length` collapses all three into one
// reassuring blank. So the discriminator is always `f.status`, looked up in
// WC_FACET below, and the row list is touched only inside the branch that has
// already established there are rows to show.

/** status -> how that status looks. The keys ARE the discriminator.
 *
 *  Exactly the four `Facet.STATUSES` from core/weaponparts.py. A fifth status
 *  arriving here renders as `wc-unknown` and says so, rather than silently
 *  falling through to the blank that looks like "nothing to see".
 */
const WC_FACET = {
  present:     { cls: 'wc-yes',     chip: 'present' },
  partial:     { cls: 'wc-partial', chip: 'partial' },
  empty:       { cls: 'wc-empty',   chip: 'none' },
  unavailable: { cls: 'wc-unknown', chip: 'not looked up' },
};

/** displacement state -> how it looks.
 *
 *  These answer ONE question: will writing this overwrite art that is already
 *  there? They are keyed on whether the resolved mesh BYTES differ, never on
 *  whether the two installs' tables name the same mesh id. Measured
 *  2026-08-17: the tables agree while the bytes differ on 3,763 of 4,710
 *  shared ids, so a table-keyed chip renders the common overwrite green.
 *
 *  `undetermined` is not merged into either safe state. "Could not read it"
 *  and "read it, nothing will break" are opposite instructions to someone
 *  about to overwrite something -- the same reason `unavailable` is not
 *  `empty` in WC_FACET above. */
const WC_VERDICT = {
  not_on_target: { cls: 'wc-yes',     chip: 'safe to add' },
  no_op:         { cls: 'wc-empty',   chip: 'identical' },
  replaces:      { cls: 'wc-danger',  chip: 'WOULD REPLACE' },
  undetermined:  { cls: 'wc-unknown', chip: 'CANNOT TELL' },
};

function wcBlock(cls, chip, headline, detail) {
  const box = el('div', 'wc-block ' + cls);
  const head = el('div', 'wc-head');
  head.appendChild(el('span', 'wc-chip', chip));
  head.appendChild(el('b', null, headline || ''));
  box.appendChild(head);
  if (detail) box.appendChild(el('div', 'wc-detail small', detail));
  return box;
}

/** The resolver's own absence, rendered so it cannot be mistaken for an
 *  answer. `unavailable` says a table was missing; THIS says the thing that
 *  reads tables is missing, and the two must not look alike. */
function wcResolverBlock(r) {
  if (r.available) return null;
  const box = wcBlock('wc-stub', 'NOT WIRED IN', r.headline, r.detail);
  const who = el('div', 'wc-detail small');
  who.appendChild(document.createTextNode('awaiting '));
  who.appendChild(el('code', null, r.contract || ''));
  who.appendChild(document.createTextNode(' from ' + (r.owner || '?')
                                          + ' (' + (r.module || '?') + ')'));
  box.appendChild(who);
  return box;
}

function wcFacetRow(f) {
  const look = WC_FACET[f.status]
    || { cls: 'wc-unknown', chip: 'status ' + f.status + '?' };
  const box = el('div', 'wc-facet ' + look.cls);
  const head = el('div', 'wc-head');
  head.appendChild(el('span', 'wc-chip', look.chip));
  head.appendChild(el('b', null, f.surface));
  box.appendChild(head);
  box.appendChild(el('div', 'wc-detail small', f.headline || ''));
  if (f.detail) box.appendChild(el('div', 'wc-detail small mut', f.detail));
  if (f.source) box.appendChild(el('div', 'idline small', f.source));
  // Only now, having switched on the status, is the row list looked at --
  // and only for the two statuses whose contract guarantees it is non-empty.
  if (f.status === 'present' || f.status === 'partial') {
    const ul = el('div', 'wc-rows small');
    for (const r of f.rows.slice(0, 8)) {
      ul.appendChild(el('div', 'idline',
        r.mesh ? (r.mesh + (r.texture ? ' / ' + r.texture : ''))
               : JSON.stringify(r)));
    }
    if (f.rows.length > 8) {
      ul.appendChild(el('div', 'mut', '+' + (f.rows.length - 8) + ' more'));
    }
    box.appendChild(ul);
  }
  return box;
}

/** Collect the weapon held in `slot`, with its linked effects and alt skins. */
async function openWeaponCollect(slot) {
  const v = B.loadout[slot];
  if (!v || !v.id) { toast('nothing in that slot'); return; }
  const host = $('#collect-body');
  if (!host) return;
  host.innerHTML = '';
  collectFor = null;                       // this panel owns the card now
  host.appendChild(el('div', 'mut small', 'Collect weapon ' + v.id
                      + (v.name ? ' — ' + v.name : '')));
  const busy = el('div', 'mut small', 'reading the weapon tables…');
  host.appendChild(busy);

  let d;
  try {
    d = await api('/api/keep/weapon?id=' + encodeURIComponent(v.id));
  } catch (e) {
    busy.remove();
    host.appendChild(wcBlock('wc-unknown', 'request failed',
      'Could not ask the server about this weapon.', e.message));
    return;
  }
  busy.remove();

  // 1. Is the resolver even here? Checked before anything else, because when
  //    it is not there are no facets at all -- not four empty ones.
  const stub = wcResolverBlock(d.resolver || {});
  if (stub) host.appendChild(stub);

  // 2. What would this do to the install being modded?
  const ver = d.verdict || {};
  const vl = WC_VERDICT[ver.state]
    || { cls: 'wc-unknown', chip: 'state ' + ver.state + '?' };
  // An absent displacement resolver is styled like the missing weapon_parts
  // resolver, not like a verdict: it is a fact about our build. It still
  // reports state `undetermined`, so nothing downstream can read it as safe.
  const vb = wcBlock(ver.stub ? 'wc-stub' : vl.cls,
                     ver.stub ? 'NOT WIRED IN' : vl.chip,
                     ver.headline, ver.detail);
  if (ver.stub) {
    const who = el('div', 'wc-detail small');
    who.appendChild(document.createTextNode('awaiting '));
    who.appendChild(el('code', null, ver.contract || ''));
    who.appendChild(document.createTextNode(' from ' + (ver.owner || '?')));
    vb.appendChild(who);
  }
  // The table-key question, reported beside the verdict and never driving it.
  // Measured 2026-08-17: table-key disagreement is a strict subset of byte
  // difference (0 counterexamples), so this can corroborate a warning but can
  // never be the reason for one.
  const tk = ver.tableKeys || {};
  if (tk.comparable && tk.note) {
    vb.appendChild(el('div', 'wc-detail small mut',
      (tk.differ ? 'Tables also disagree: ' : 'For reference: ') + tk.note));
  }
  host.appendChild(vb);

  // 3. The four surfaces, each by its own status.
  const donor = d.donor || {};
  if (donor.resolverAvailable && donor.presence === 'on_install') {
    for (const surface of ['appearance', 'motions', 'effects', 'skins']) {
      const f = (donor.facets || {})[surface];
      if (f) host.appendChild(wcFacetRow(f));
    }
  } else if (donor.resolverAvailable) {
    host.appendChild(wcBlock('wc-unknown', donor.presence || 'unknown',
                             donor.headline, donor.detail));
  }

  // 4. What would actually travel.
  const plan = d.plan || {};
  const nMotion = (plan.motions || []).length;
  const nEffect = (plan.effects || []).length;
  const nExtra = (plan.extraParts || []).length;
  // The button says which of the three it is. `undetermined` is not styled as
  // safe -- but it also does not get the modal below, and that is deliberate:
  // until the displacement resolver lands EVERY weapon is undetermined, and a
  // modal that fires on every single collect is one users learn to dismiss
  // without reading. Spending the interruption on the state we actually know
  // is destructive is what keeps it worth reading when it appears.
  const go = el('button', ver.safe ? 'primary' : 'ghost',
                ver.state === 'replaces' ? 'Collect anyway'
                : ver.state === 'undetermined' ? 'Collect (unverified)'
                : 'Collect this weapon');
  go.style.marginTop = '6px';
  const summary = el('div', 'mut small',
    plan.mesh ? (plan.mesh + ' · ' + nMotion + ' motion, ' + nEffect
                 + ' effect, ' + nExtra + ' extra part file(s)')
              : 'No mesh could be resolved for this weapon.');
  host.appendChild(summary);
  // Files a table NAMED and the install does not have. Shown rather than
  // dropped: "the effect is m-b02 and m-b02 is not here" is a fact about the
  // weapon you are about to collect, and silence about it is how you find out
  // in the game instead.
  for (const sk of (plan.skipped || []).slice(0, 6)) {
    host.appendChild(el('div', 'wc-detail small mut',
      sk.surface + '/' + sk.role + ' “' + sk.name + '”: ' + sk.reason));
  }
  // Keys where the compiled table and its plaintext twin name different
  // files. Rare, and not a reason to refuse -- but the collected entry then
  // depends on which table was believed, so it is said out loud.
  for (const cf of (plan.conflicts || []).slice(0, 4)) {
    host.appendChild(wcBlock('wc-partial', 'tables disagree',
      'Motion key ' + cf.key + ': ' + cf.reason,
      (cf.tables || []).join(' vs ') + ' — took ' + cf.path));
  }
  if (!plan.mesh) { go.disabled = true; }

  // The satellite chooser, on the Builder's own Collect path.
  //
  // The weapon collector resolves its OWN motions and effects out of
  // `weaponmotion.dbc`/`.ini` (core/weaponcollect.plan_files), and that stays
  // the authority -- `c3/weapon/` has no per-action siblings at all, so
  // anything the offer derived from the directory would be empty for every
  // weapon on the install. So the checkboxes GATE that list rather than
  // replacing it: a kind the user did not tick contributes nothing, and the
  // ticked kinds still travel by the path that actually knows them.
  //
  // The one rule the page is NOT trusted with is the animations ruling. The
  // server refuses an explicit motions list when animations is unticked,
  // whatever this code does -- see post_collect.
  let wcSat = null;
  const wcHost = el('div', 'sat-panel');
  if (plan.mesh) {
    wcHost.appendChild(el('div', 'mut small',
      'working out what goes with this\u2026'));
    host.appendChild(wcHost);
    (async () => {
      const offer = await satFetch(plan.mesh, String(v.id || ''));
      const st = { subject: plan.mesh, offer, picked: {},
                   report: (offer.entry || {}).report || null };
      satSelectAll(st);
      wcSat = st;
      const redraw = () => satPanel(wcHost, st, redraw);
      redraw();
    })();
  }

  go.addEventListener('click', async () => {
    // `undetermined` confirms too, since 2026-08-27. It was suppressed
    // on ONE argument: with no displacement provider wired in, EVERY
    // weapon reported `undetermined`, so the modal would fire on 100%
    // of collects -- and a modal that always fires is one users learn
    // to dismiss unread. A provider is now resolvable, so the state
    // means something else: 'this pair could not be read', ~5% of
    // shared ids. Rare, and a real uncertainty about whether something
    // gets overwritten. The premise that justified suppressing it is
    // GONE rather than weakened, which is why this is a re-decision
    // and not a threshold being loosened.
    if ((ver.state === 'replaces' || ver.state === 'undetermined')
        && !confirm(ver.headline + '\n\n' + ver.detail
                    + '\n\nCollecting only files this in your Collection — '
                    + 'the target is written when you stage or install it. '
                    + '\n\nKeep this weapon anyway?')) return;
    go.disabled = true;
    try {
      const r = await api('/api/keep/add', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(wcCollectBody(plan, v, wcSat)) });
      collectionMeta = null;
      const np = (r.entry.parts || []).length;
      toast('collected ' + r.entry.id + (np ? ' (+' + np + ' file(s))' : ''));
      renderCollect({ force: true });
    } catch (e) {
      toast('collect failed: ' + e.message, 5000);
      go.disabled = false;
    }
  });
  host.appendChild(go);
  const back = el('button', 'ghost tiny', 'Back');
  back.style.marginLeft = '6px';
  back.addEventListener('click', () => renderCollect({ force: true }));
  host.appendChild(back);
}

/** What the weapon panel POSTs, with the chooser applied.
 *
 *  Split out so the gating rule is one readable thing rather than an inline
 *  ternary inside a click handler.
 *
 *  `motions` is sent even when EMPTY and that is load-bearing: its presence
 *  means "the tables were asked", and its absence would put the directory
 *  sweep back -- which returns nothing for every weapon on the install and
 *  would file a motionless entry while reporting success
 *  (core/weaponcollect.py).
 *
 *  With no chooser (it is still loading, or the offer failed) the body is
 *  exactly what this panel always sent. The chooser can only ever REMOVE from
 *  that list, never add to it. */
function wcCollectBody(plan, v, st) {
  const body = {
    path: plan.mesh, texture: plan.texture, category: 'Weapons',
    name: v.name || String(v.id),
    motions: plan.motions || [],
    effects: (plan.effects || []).concat(plan.extraParts || []),
  };
  if (!st || st.subject !== plan.mesh) return body;
  body.kinds = (st.offer.kinds || [])
    .filter(r => st.picked[r.name]).map(r => r.name);
  body.appearance = String(v.id || '');
  // Which roles survive is read off the SERVER's kind table, so this stays
  // free of any kind name of its own -- the same rule satSelectAll follows.
  const roles = new Set((st.offer.kinds || [])
    .filter(r => st.picked[r.name]).map(r => r.role));
  if (!roles.has('motion')) body.motions = [];
  if (!roles.has('effect') && !roles.has('sound')) body.effects = [];
  return body;
}

/** The card when the thing on the stage IS a Collection entry's own copy.
 *
 *  Browsing the `collection` view and finding what you kept is the obvious
 *  way to go and use it, so this is where Replace has to be. What must NOT
 *  be here is Collect: posting this path would file a second entry whose
 *  source is the first entry's copy, and the Collection is a set of
 *  decisions, not a chain of them.
 */
function renderCollectedCopy(host, entry, cur) {
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
  const swap = el('button', 'ghost tiny', 'Replace an asset with this…');
  swap.title = 'Write this entry over the asset it replaces, choosing '
             + 'whether its skin and animations travel with it.';
  swap.addEventListener('click', () => {
    if (panel.childElementCount) { panel.innerHTML = ''; return; }
    // The original is the default target: replacing what it was collected
    // from is the common case, and every other target is a path away.
    Swap.replacePanel(panel, entry,
                      { defaultTarget: entry.swapFor || entry.sourceMesh });
  });
  host.appendChild(swap);

  const drop = el('button', 'ghost tiny', 'Remove');
  drop.style.marginLeft = '6px';
  drop.title = 'Drop this entry from the Collection and delete its files.';
  drop.addEventListener('click', async () => {
    try {
      await api('/api/keep/remove', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entry.id }) });
      collectionMeta = null;
      toast('removed from the collection');
      renderCollect({ force: true });
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

/** The ACTION dropdown for an ad-hoc mesh.
 *
 *  The old client splits geometry from animation: c3/npc/001/1.c3 is the
 *  model and 100/101/190.c3 beside it are MOTI-only action files. The
 *  model's own track is usually a two-frame idle, which is why this looked
 *  like "no animation" -- the real clips are the siblings.
 */
/** At or below this, a container's own MOTI is a bind pose rather than a
 *  clip. Measured, not guessed: collected NPCs carry exactly 2. */
const OWN_TRACK_POSE_FRAMES = 2;

function fillAdhocActions() {
  const sel = $('#anim-action');
  if (!sel) return;
  sel.innerHTML = '';
  // Say what the own track actually holds. Offering it as an unqualified
  // choice beside the real actions is what made picking it look like a
  // failure -- it plays, it just has nothing to show.
  const n = B.adhoc ? B.adhoc.ownFrames : undefined;
  const own = el('option', null,
    n === 0 ? 'this file\u2019s own track (empty)'
    : (n > 0 && n <= OWN_TRACK_POSE_FRAMES)
      ? `this file\u2019s own track (${n} frames \u2014 a pose)`
      : 'this file\u2019s own track');
  own.value = '';
  sel.appendChild(own);
  for (const a of (B.adhoc.actions || [])) {
    const o = el('option', null, `${a.code} — ${a.label}`);
    o.value = a.path;
    sel.appendChild(o);
  }
  sel.value = B.adhoc.action || '';
  sel.disabled = false;
  sel.onchange = async () => {
    B.adhoc.action = sel.value;
    saveModelState();
    await rebuildAdhoc({ reframe: false });
  };
}

/** A mesh opened by path from the asset browser.
 *
 *  Deliberately not routed through /api/model: that endpoint answers for
 *  catalogued models (monsters, NPCs, effects) and a garment recovered from
 *  a community archive is in no catalogue. The geometry comes from the same
 *  /api/mesh the browser itself uses, so anything visible there is visible
 *  here, with this stage's camera, zoom and lighting.
 */
/** Call `done` once the mesh<->texture index finishes building.
 *
 *  Polls `/api/index/status`, which `sethealth.js` already reads -- the
 *  endpoint existed and no 3D page asked it anything. Gives up after a bound
 *  rather than polling forever, and says so in the stage message: a page that
 *  waits silently and indefinitely is the symptom this whole change is about.
 */
let _indexWatch = null;
function whenIndexReady(tk, done) {
  if (_indexWatch) return;                 // one watcher, not one per mesh
  const started = Date.now();
  const LIMIT_MS = 5 * 60 * 1000;
  const tick = async () => {
    let st = null;
    try { st = await api('/api/index/status'); } catch (e) { st = null; }
    if (!stillCurrent(tk)) { _indexWatch = null; return; }
    // THE STATE IS NESTED UNDER `index`, AND READING IT FLAT MEANT THIS
    // WATCHER COULD NEVER FIRE.
    //
    // `api_index_status` answers `{index: unified_status(), run, root, offer}`,
    // so `available`, `progress` and `state` live one level down. The first
    // version of this function read `st.available` and `st.progress` at the
    // top level: both are `undefined` for every response the server can send,
    // so the watcher polled for its full five minutes and then printed "the
    // index is still building" on a client whose index had landed thirty
    // seconds in -- and the progress counts never rendered at all.
    //
    // WHAT MADE IT SURVIVE REVIEW is worth more than the fix: this PR verified
    // the SERVER side (`texture_for_mesh_now` 0.0000 s steady-state,
    // `unified_status` reporting state=building) and asserted that the page
    // POLLS the endpoint. It does poll. A watcher that polls forever satisfies
    // "the page polls /api/index/status" exactly as well as one that works.
    // The outcome was never checked, only the mechanism.
    //
    // `sethealth.js` had it right from the start -- `const idx = rep.index ||
    // {}` -- so the shape was documented by a working reader in the same tree.
    // Caught by a peer session copying this function and hitting the same
    // `state=None available=None` against a server that was `ready/true`.
    const ix = (st && st.index) || null;
    // STOP ON `settled`, NOT ON `available` -- the SECOND half of the defect
    // described above, and it survived the first fix.
    //
    // Reading `available` at the right level made this watcher able to fire.
    // It did not make it able to STOP: a build that FAILED publishes an empty
    // index, so `available` is false and stays false while nothing further is
    // coming, and this function would poll its whole five minutes and then
    // report "still building" about a build that finished. Same reassuring
    // wrong answer, one layer in.
    //
    // `settled` is `_unified_done`, set on both the success and the failure
    // path -- the one signal the failure cannot leave undisturbed. `available`
    // now only decides WHICH outcome to report.
    if (ix && ix.settled) {
      _indexWatch = null;
      if (ix.available) {
        done();
      } else {
        $('#gl-msg').textContent =
          'the mesh/texture index finished without an answer' +
          (ix.error ? ' (' + ix.error + ')' : '') +
          '; the model is untextured';
        $('#gl-msg').classList.remove('hidden');
      }
      return;
    }
    if (Date.now() - started > LIMIT_MS) {
      _indexWatch = null;
      $('#gl-msg').textContent =
        'the mesh/texture index is still building; the model is untextured';
      $('#gl-msg').classList.remove('hidden');
      return;
    }
    const n = ix && ix.progress ? ix.progress : null;
    $('#gl-msg').textContent = n && n.total
      ? `building the mesh/texture index ${n.done}/${n.total} — geometry first, the skin follows`
      : 'building the mesh/texture index — geometry first, the skin follows';
    $('#gl-msg').classList.remove('hidden');
    _indexWatch = setTimeout(tick, 2000);
  };
  _indexWatch = setTimeout(tick, 500);
}

async function rebuildAdhoc({ reframe = false } = {}) {
  const tk = tokenNow();
  const path = B.adhoc.mesh;
  $('#gl-msg').textContent = 'loading…';
  $('#gl-msg').classList.remove('hidden');
  let d;
  try {
    d = await api('/api/mesh?path=' + encodeURIComponent(path) + '&guesstex=1');
  } catch (e) {
    $('#gl-msg').textContent = 'could not load ' + path + ': ' + e.message;
    renderModelPanel();
    return;
  }
  if (!stillCurrent(tk)) return;
  if (!d.meshes || !d.meshes.length) {
    $('#gl-msg').textContent = d.note ||
      'no drawable geometry in ' + path;
    renderModelPanel();
    return;
  }
  const tex = B.adhoc.tex || d.guessedTexture || '';
  /* THE SKIN CAN ARRIVE LATER THAN THE GEOMETRY. `/api/mesh` no longer waits
   * on the mesh<->texture index -- that wait is ~52 s on an un-indexed
   * client, for a satellite answer, while the geometry beside it is ready in
   * milliseconds. When the server says `indexPending` it has told us the
   * texture is NOT KNOWN YET, which is a different fact from "this mesh has
   * no texture", so the model is drawn now and asked about again once the
   * build lands. */
  if (d.indexPending && !B.adhoc.tex) whenIndexReady(tk, () => rebuildAdhoc());
  const mat = zoomMatrix();

  // The clip is fetched first so its bounds can frame the camera in the same
  // call that uploads the meshes -- `frameOn` is a setMeshes option, not a
  // method on the viewer.
  let clip = null;
  try {
    const q = new URLSearchParams({ path });
    if (B.adhoc.action) q.set('motion', B.adhoc.action);
    clip = await api('/api/meshanim?' + q.toString());
  } catch (e) { clip = null; }
  if (!stillCurrent(tk)) return;
  const playable = clip && clip.frames > 0 && !clip.error;
  // How the *container's own* track plays is only knowable from a fetch that
  // asked for it. Recorded once, so selecting an action later cannot make
  // the own-track option look richer than it is.
  if (!B.adhoc.action) B.adhoc.ownFrames = playable ? clip.frames : 0;

  // A model whose geometry and animation live in separate files carries a
  // token track of its own: measured on a collected NPC, 2 frames against
  // the 50, 120 and 120 of its three real actions. So the default selection
  // was a static pose, and "this file's own track" read as broken rather
  // than as "the clips are in the ACTION list". Open on the first real
  // action instead.
  //
  // Once per mesh, and only when nothing was chosen: selecting the own track
  // back deliberately has to stick, pose or not.
  const ownIsAPose = !playable || clip.frames <= OWN_TRACK_POSE_FRAMES;
  if (ownIsAPose && !B.adhoc.action && !B.adhoc.autoActioned
      && clip && (clip.actions || []).length) {
    B.adhoc.autoActioned = true;
    B.adhoc.action = clip.actions[0].path;
    return rebuildAdhoc({ reframe });
  }

  viewer.setMeshes(d.meshes.map(m => ({ meta: m, textureKey: tex ? 'model' : null,
                                        matrix: mat })),
                   { keepFraming: !reframe,
                     frameOn: playable && clip.bounds
                       ? scaledBounds(clip.bounds) : undefined });
  if (tex) await applyNamedTexture('model', tex);
  if (!stillCurrent(tk)) return;
  B.adhoc.data = d;
  B.adhoc.resolvedTex = tex;
  $('#gl-msg').classList.add('hidden');
  $('#gl-stats').textContent = viewer.stats + '\n' + path +
    (tex ? ' · ' + tex : ' · untextured');

  // Most of these containers carry their own MOTI, one per PHY chunk, so
  // they animate without belonging to any catalogue. Fetch it and hand it
  // to the same animation UI the catalogued models use.
  B.anim.seq = [];
  B.anim.data = null;
  B.adhoc.actions = (clip && clip.actions) || [];
  fillAdhocActions();
  if (playable) {
    B.adhoc.clip = clip;
    B.anim.seq = [clip];
    B.anim.data = clip;
    B.anim.seqAt = 0;
    B.anim.frame = 0;
    syncAnimUi();
    renderAnimPanel();
  } else {
    B.adhoc.clip = null;
    B.adhoc.clipError = (clip && clip.error) || 'no motion in this container';
    syncAnimUi();
    renderAnimPanel();
  }
  renderModelPanel();
  renderCollect();
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
 *  last model left it at and a small effect is an invisible dot.
 *
 *  A particle part carries the same `bboxRender` key (effectplay emits it from
 *  the baked frames, half-extent included), which is what makes a PURE
 *  particle effect framable at all -- it has no `geometry` to measure. */
function effectBounds(def) {
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const lay of (def && def.layers) || []) {
    for (const p of lay.parts || []) {
      const b = (p.geometry && p.geometry.bboxRender) || p.bboxRender;
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

/** Frame the stage the way the EFFECT says to, falling back to the box fit.
 *
 *  `effectBounds` above is the fit, and it stays: it is what sets `center`
 *  and `radius`, and `applyAuthoredCamera` pans relative to that centre. So
 *  the order matters -- fit first, then override the orbit if the file has an
 *  opinion. Returns the string the panel prints, so the page always says
 *  WHICH of the two framings the user is looking at. */
function frameEffect(def, eb) {
  viewer.setMeshes([], { frameOn: eb, keepFraming: !eb });
  const cam = def && def.camera;
  if (!cam) return null;
  return viewer.applyAuthoredCamera(cam) ? cam : null;
}

async function showEffectModel(info, tk) {
  const eb = effectBounds(info.effect);
  B.model.authoredCam = frameEffect(info.effect, eb);
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
    // Particles are drawn now (docs/effects.md §6.6), so reaching here no
    // longer means "pure particles" -- it means every layer failed to load or
    // the effect really has nothing in it. Say that instead of naming a gap
    // that is closed.
    $('#gl-msg').textContent =
      (info.effect.layers || []).length
        ? 'no drawable layer — every layer of this effect failed to load'
        : 'this effect declares no layers';
  }
  // Which framing you are looking at is not cosmetic: an artist's camera and
  // our box fit disagree, and someone tuning an effect against this view needs
  // to know which one it is.
  $('#gl-stats').textContent =
    `${info.ident || info.id} · ${n} layer(s) · camera: ` +
    (B.model.authoredCam
      ? `CAME "${B.model.authoredCam.name || 'unnamed'}" (the artist's)`
      : 'fitted to bounds (ours, not the artist\'s)');
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
  if (B.adhoc && B.adhoc.mesh) {
    b.appendChild(el('div', 'small',
      'Opened from the asset browser, not from the model catalogue.'));
    b.appendChild(kv([
      ['mesh', B.adhoc.mesh],
      ['texture', B.adhoc.resolvedTex || '(none resolved)'],
      ['chunks', String(((B.adhoc.data || {}).meshes || []).length)],
      ['motion', B.adhoc.clip
        ? `${B.adhoc.clip.frames} frames, ${B.adhoc.clip.loop}`
        : (B.adhoc.clipError || 'none')],
      ['action', B.adhoc.action
        ? B.adhoc.action.split('/').pop()
        : (B.adhoc.actions && B.adhoc.actions.length
           ? 'own track (' + B.adhoc.actions.length + ' more beside it)'
           : 'own track')],
    ]));
    b.appendChild(el('div', 'mut small',
      !B.adhoc.clip
        ? 'Nothing to play: this container has geometry but no motion track.'
        : B.adhoc.action
          ? 'Motion comes from ' + B.adhoc.action.split('/').pop()
            + ', a MOTI-only file beside this model, bound over its PHY '
            + 'chunks by ordinal — press play.'
          : 'Motion is this file’s own MOTI track — often just a '
            + 'short idle. The real clips are the sibling action files in '
            + 'the ACTION list.'));
    b.appendChild(el('div', 'mut small', 'Pick anything in the Models list '
                     + 'to leave this mesh.'));
    const back = el('a', 'navlink-inline', '← back to this asset in the browser');
    back.href = '/#file=' + encodeURIComponent(B.adhoc.mesh);
    b.appendChild(back);
    const clear = el('button', 'ghost tiny', 'Leave this mesh');
    clear.style.marginTop = '8px';
    clear.addEventListener('click', async () => {
      B.adhoc = null;
      saveModelState();
      await loadModelList({ select: B.model.key });
      await rebuildModel({ reframe: true });
    });
    b.appendChild(clear);
    return;
  }
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

  // 6090 ships monster colourways as sibling texture files (the leading
  // digit of the 9-digit id), not as table rows — ThunderApe's nine skins.
  const cw = info.colourways || [];
  if (cw.length > 1) {
    b.appendChild(el('div', 'small mut',
                     `${cw.length} colourways ship for this body`));
    const strip = el('div', 'variants');
    const current = B.model.tex || info.texture;
    for (const p of cw) {
      const img = document.createElement('img');
      img.src = '/api/texture?path=' + encodeURIComponent(p);
      img.title = p;
      img.loading = 'lazy';
      if (p === current) img.classList.add('sel');
      img.addEventListener('click', async () => {
        B.model.tex = p;
        await applyNamedTexture('model', p);
        if (viewer) viewer.draw();
        renderMonsterPanel();
      });
      strip.appendChild(img);
    }
    b.appendChild(strip);
  }

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
  const url = location.origin + '/models#' + p.toString();
  location.hash = p.toString();
  navigator.clipboard.writeText(url).then(
    () => toast('model link copied'),
    () => toast('clipboard blocked; the link is in the address bar'));
}

// ---------------------------------------------------------------- controls
function bindControls() {
  // The two pages' OWN controls. Split rather than null-guarded one line at a
  // time: which controls a page has is a fact about the page, and forking here
  // means a missing element is a bug rather than a silently skipped binding.
  if (IS_MODELS) bindModelControls(); else bindCharacterControls();

  // panel collapse (per-card headers and `C`) is CardPanels', bound in
  // restoreCollapsed() before this runs — one implementation, shared with the
  // asset browser page. The header button that also did it is gone.

  $('#anim-action').addEventListener('change', async e => {
    // An ad-hoc mesh drives this select itself (fillAdhocActions), and its
    // values are motion FILE PATHS, not action codes -- selectModelAction
    // would misread them.
    if (B.adhoc && B.adhoc.mesh) return;
    if (B.mode === 'model') { selectModelAction(e.target.value); return; }
    animPause();
    B.anim.action = e.target.value;
    B.anim.frame = 0;
    // Rebuild as well as re-clip. /api/figure is asked for a pose AT this
    // action, and everything derived from it -- the extent in the stats
    // line, the socket rows and the "pose" key in the how-it-is-built panel
    // -- otherwise keeps describing the action you just navigated away from.
    // That panel is the one you read to check which motion is in play, so a
    // stale answer there is the same class of bug as the hardcoded weapon
    // set it used to carry.
    await rebuild({ reframe: false });
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
  for (const slot of AURA_HANDS) {
    const { lab, box } = auraControls(slot);
    if (!lab || !box) continue;
    box.addEventListener('change', e => setAura(slot, e.target.checked));
    lab.addEventListener('click', e => {
      // A disabled checkbox swallows the click, so the label explains instead
      // of doing nothing — "why is this greyed" is the actual question here.
      if (box.disabled) { e.preventDefault(); toast(auraWhyNot(slot), 6000); }
    });
  }
  // The ✕ stops the effect playback, which means both hands: it sits on the
  // shared fx strip, not on either hand's switch.
  $('#btn-fx-stop').addEventListener('click', () => {
    for (const slot of AURA_HANDS) aura(slot).on = false;
    persistAura();
    applySuperFx();
    syncAuraControl();
    refreshWeaponPanel();
  });
}

/** Builder-only: the slot rail, the picker, and the character's own buttons.
 *  None of this markup exists on models.html. */
function bindCharacterControls() {
  $('#btn-more-slots').addEventListener('click', () => {
    $('#slot-cards-more').classList.toggle('hidden');
    renderSlots();
  });
  $('#btn-share').addEventListener('click', copyLink);
  $('#btn-reset-all').addEventListener('click', resetAll);
  $('#picker-close').addEventListener('click', cancelPicker);
  $('#picker-clear').addEventListener('click', async () => {
    const slot = B.picker.slot;
    closePicker();
    if (slot) await unequip(slot);
  });
  $('#picker-search').addEventListener('input', debounce(loadPickerOptions, 220));
  $('#picker-search').addEventListener('input', paintPickerClear);
  if ($('#picker-clear-filters'))
    $('#picker-clear-filters').addEventListener('click', clearPickerFilters);
  $('#picker').addEventListener('click', e => {
    if (e.target.id === 'picker') cancelPicker();
  });
}

/** Model Viewer only: the model rail and the clip filter. None of this markup
 *  exists on builder.html. */
function bindModelControls() {
  $('#btn-share').addEventListener('click', copyLink);
  $('#model-search').addEventListener('input', debounce(e => {
    B.model.q = e.target.value.trim();
    loadModelList();
  }, 220));
  $('#model-path').addEventListener('input', debounce(e => {
    B.model.path = e.target.value.trim();
    saveModelState();
    loadPathList();
  }, 260));
  $('#chk-clips').addEventListener('change', e => {
    B.model.distinctOnly = e.target.checked;
    saveModelState();
    fillModelActions();
  });
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
        // Q works in here too, and has to: opening a picker is the ONLY way
        // to point the Colour & variants panel at a slot, and that panel is
        // what Q reads. Without this, "press 4 for the left hand, then Q"
        // would mean closing the picker again first.
        //
        // Not reachable straight after opening one, and that is correct: the
        // search box takes focus, and `typingInAField()` above returns before
        // this switch so `q` types a `q`. It goes live the moment you leave
        // that box, which ArrowDown does on its way into the list -- i.e. as
        // soon as you are browsing rather than searching. The panel is
        // already showing this slot's ladder by then (openPicker calls
        // showLookFor) and that ladder's own buttons are already live, so
        // this is a key for an affordance that is on screen either way.
        case 'Q': case 'q': stepQuality(e.shiftKey ? -1 : 1); break;
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
      // Bound off PICKER_KEYS, which stepQuality quotes back at the user when
      // it needs to be told which hand it is talking about.
      case '1': case '2': case '3': case '4':
        openPicker(SLOT_FOR_PICKER_KEY[e.key]); break;
      // Two hands, two keys. This case is on `e.key`, so it is reached only
      // with Shift held; plain `a` falls through to the lowercase switch.
      case 'A': toggleAura('l_weapon'); break;
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
          case 'a': toggleAura('r_weapon'); break;
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
