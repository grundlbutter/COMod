/* effects.js -- the Effects Viewer's LIST, FILTERS and STAGE.
 *
 * WHAT THIS FILE IS AFTER THE MERGE (2026-09-08)
 * ----------------------------------------------
 *   `/effects` and `/fxview` were one subject seen twice and are now one page
 *   called the Effects Viewer. The split between the two scripts is not the
 *   old page split: it is a seam.
 *
 *     effects.js (here)   the effect LIST, every filter on it, and the STAGE
 *     fxview.js           the provenance strip, the census, and the panels
 *                         that DESCRIBE the selected effect
 *
 *   They talk through exactly two published objects and nothing else:
 *
 *     window.CoEffects    this file. `selectByName` drives the page from
 *                         elsewhere; `censusUpdate` is how fxview.js hands
 *                         over the whole/partial/broken classification the
 *                         state filter runs on; `setStateFilter` is what the
 *                         census panel's "show these" buttons call.
 *     window.CoFxView     fxview.js. `select` is called after this file has
 *                         resolved an effect, `setKind` when the 2D/3D switch
 *                         moves, `isTagged` by the tagged-only filter.
 *
 *   NEITHER FILE REACHES INTO THE OTHER AT LOAD TIME. Every call above happens
 *   from an event, and every one is guarded, so the page still boots -- with
 *   one half working and saying so -- if the other script fails to load. A
 *   hard dependency between two `<script>` tags is a page that dies whole.
 *
 *   THE STATE FILTER IS THE OWNER'S ASK AND IT HAS ONE RULE: it is DISABLED
 *   until the census says `ready`, and `passesState` returns true under every
 *   other state. An unclassified effect is UNMEASURED, not "not this state",
 *   so a filter over a half-built census would drop it silently and hand back
 *   a shorter list that looks like an answer. Same rule the form filter has
 *   always had, and it is enforced twice on purpose -- a disabled `<select>`
 *   is a UI fact and a filter predicate is a correctness one.
 *
 * WHAT IT PLAYS
 * -------------
 *   2D  ini/effect.ini + ani/effect.ani, through `/api/effect2d`. A list of
 *       whole images, composited on a 2D canvas at the effect's OWN declared
 *       `FrameInterval`, honouring `Delay`, `LoopInterval` and `LoopTime`.
 *
 *   3D  ini/3DEffect.ini, through `/api/effect`. The geometry, motion and
 *       particles are played by the WebGL stage that already exists on the
 *       Model Viewer -- gl.js `Viewer` and fx.js `EffectInstance` -- which
 *       this page CONSTRUCTS on `#fx-gl`. That is not a second renderer: it
 *       is the same two classes the Model Viewer and the Effects Viewer use,
 *       on a different canvas.
 *
 *       IT USED TO CONSTRUCT NOTHING, and that was bugs_open.md #3. `restart`
 *       ran `clearCanvas()` for every non-2D kind, so the "3D scene" tab
 *       painted the 2D canvas's checkerboard and stopped -- for every effect,
 *       always, with a fully populated Declared panel beside it and no error
 *       anywhere. The report guessed a degenerate bounding box; the truth was
 *       that no camera, no context and no draw call existed on the page to be
 *       wrong. `effects.html` loaded neither gl.js nor fx.js.
 *
 * THE THREE THINGS THAT ARE EASY TO GET WRONG, AND ARE NOT
 * -------------------------------------------------------
 *  1. TIME IS MILLISECONDS ON THE EFFECT'S CLOCK, NOT A FRAME INDEX.
 *     `Delay` holds the effect blank before frame 0 and `LoopInterval` holds
 *     it blank between repeats, so there are stretches of the timeline where
 *     the correct thing to draw is NOTHING. A scrubber indexed by frame
 *     cannot express those, and one that clamped to frame 0 would show a
 *     permanent first frame through every gap. `frameAt` returns null for
 *     blank and the canvas draws blank.
 *
 *  2. THE FRAME COUNT IS THE .ani's, THE TIMING IS THE .ini's, AND EITHER CAN
 *     BE MISSING WITHOUT THE OTHER. A row whose `AniTitle` names no `.ani`
 *     section is a real, shipped condition (6 rows on 7878). So is an `.ani`
 *     section no row times (33 on 7878). Both are shown, and the second is
 *     labelled as played at OUR guessed interval.
 *
 *  3. NO BLEND MODE IS DECLARED FOR 2D. Not "we did not look" -- the table
 *     has exactly 9 keys and none of them is a blend, measured over 5,457
 *     sections on 33 installs. The canvas composites with straight alpha by
 *     default and offers an additive toggle, and the rail says in words that
 *     the choice is ours. Compare the 3D path, where `ASB`/`ADB` ARE declared
 *     and `/api/effect` returns the exact GL blend factors -- those are
 *     honoured, not guessed, and the rail says that too.
 */
'use strict';

/* THE POP-OUT'S HEADER AUTO-MOUNT IS TURNED OFF HERE, AND THIS IS THE FIRST
 * STATEMENT OF THE FILE ON PURPOSE -- bugs_open.md #6.
 *
 * `portagepanel.js` runs `mount()` from its own load and skips the header
 * button when `window.CO_PORTAGE_AUTOMOUNT === false`. The flag used to be an
 * inline `<script>` in `effects.html`, and the server sends
 * `script-src 'self'` with no `'unsafe-inline'`, so it NEVER RAN: measured in
 * headless Chrome on this page, `'CO_PORTAGE_AUTOMOUNT' in window` was false
 * and `#pp-open` was in the header regardless. An external file is what the
 * CSP allows, and `effects.html` loads this one above `portagepanel.js`.
 *
 * OUTSIDE THE IIFE BELOW, because it has to be an assignment on `window` that
 * has already happened by the time the next `<script>` element executes, and
 * because a reader looking for why the header has no export button should hit
 * it in the first thirty lines rather than inside a closure. */
window.CO_PORTAGE_AUTOMOUNT = false;

(() => {

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};

const S = {
  kind: '2d',
  rows: [],           // picker rows for the current kind
  //: Which page of the filtered list is on screen. Reset to 0 by anything
  //: that changes WHAT is being filtered -- staying on page 4 of a list that
  //: just became 12 rows long shows an empty pane and reads as "no matches".
  page: 0,
  //: Non-empty when the paged fetch did not reach `total`. Rendered ON the
  //: list, because an incomplete list that looks complete is the defect this
  //: whole change exists to remove.
  fetchShort: '',
  //: One-shot line naming what `resetFilters` cleared, consumed by the
  //: next `drawList` so the button reports rather than acts silently.
  resetNote: '',
  names3d: [],
  name: '',
  def: null,          // the resolved effect (2D payload or 3D scene)
  //: THE MULTI-EFFECT STAGE, or null when one effect is playing.
  //:
  //:     { appearance, entries: [{ name, def, instIdx, error }] }
  //:
  //: The owner's feature: a SUBJECT -- appearance 410009, say -- plays three
  //: distinct named effects at once (`410009`, `Flash4102`, `m-b02`), and
  //: 2,209 of 5,384 weapon appearances on the configured install play two or
  //: more. `S.def` stays the PRIMARY effect so the camera, the layer panel
  //: and the 2D path are unchanged; this is what the transport and the
  //: per-effect rows read.
  stage: null,
  playing: true,
  t: 0,               // ms on the effect's own clock
  last: 0,
  raf: null,
  images: [],         // HTMLImageElement per 2D frame, index-aligned
  census2d: null,
  // -- the 3D stage ---------------------------------------------------------
  // `viewer` is a gl.js `Viewer`, built once, lazily, on the first 3D select.
  // Lazily because constructing a WebGL context costs one, and a visitor who
  // only ever looks at the 2D flipbook should not pay it.
  viewer: null,
  glError: '',        // why the stage could not be built, if it could not
  framing: null,      // what `frame3d` did: {source, camera, bounds}
  t0: 0,              // performance.now() origin of the 3D clock
  resizeRaf: 0,
  // -- the animation-form filter (3D only) ---------------------------------
  // `forms` is name -> ARRAY, not name -> string: a C3 container can carry
  // more than one form (5517 c3/effect/lance/560029.C3 holds 4 PHY + 4 MOTI
  // AND 2 SHAP + 2 SMOT), so an effect belongs under every form it carries.
  // `want` is a Set of canonical form names; empty means no filter, which is
  // NOT the same as "match nothing".
  forms: null,        // {} once loaded; null while never fetched
  formState: 'cold',  // cold | building | ready | failed  (from the server)
  formStatus: null,
  want: new Set(),
  formPoll: null,
  // -- the breakage-state filter (3D only), fed by fxview.js ----------------
  // `states` is name -> whole|partial|broken and `censusState` is the census
  // build's own state. Both are EMPTY until the census reports `ready`, and
  // `passesState` refuses to filter on anything else: see the file header.
  states: {},
  censusState: '',
  censusNote: '',
  // -- the mesh<->texture index (3D only) ----------------------------------
  // `/api/models` no longer waits for that index to build; it answers with
  // the rows it has and says `indexPending`. See `Catalog.models_now`. The
  // picker's own rows are not index-derived -- measured, all 2,260 effect
  // rows on CCO are identical cold and warm -- so the list is COMPLETE while
  // this is true, and only the geometry/skin columns are provisional.
  indexPending: false,
  indexNote: '',
};

/** The canonical form names. Spelled once; the buttons carry the labels. */
const FORM_PHY = 'PHY+MOTI';
const FORM_SHAP = 'SHAP+SMOT';
const FORM_PTCL = 'PTCL/PTC3';
const FORM_BTN = { [FORM_PHY]: '#fx-form-phy', [FORM_SHAP]: '#fx-form-shap',
                   [FORM_PTCL]: '#fx-form-ptcl' };

/* ---------------------------------------------------------------- fetching */

async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = `HTTP ${r.status}`;
  return j;
}

function status(msg) { $('#statusline').textContent = msg; }

/** Call `done` once the mesh<->texture index finishes building.
 *
 *  Polls `/api/index/status`. One watcher for the page, not one per fetch,
 *  and it gives up after a bound rather than polling forever -- a page that
 *  waits silently and indefinitely is the symptom this whole change is about.
 *
 *  **THE PAYLOAD NESTS.** `api_index_status` returns
 *  `{index: unified_status(), run: …, root: …, offer: …}`, so the state lives
 *  at `j.index.available` and NOT at `j.available`. Reading the top level
 *  returns `undefined` forever: the watcher never fires, polls out its whole
 *  budget and then reports "still building" against an index that landed
 *  30 s in. `sethealth.js` reads it correctly (`indexBlock` does
 *  `rep.index || {}`) and that is the shape to copy.
 */
let _indexWatch = null;
function whenIndexReady(done) {
  if (_indexWatch) return;
  const started = Date.now();
  const LIMIT_MS = 10 * 60 * 1000;
  const tick = async () => {
    // TWO STATEMENTS, NOT `(await getJSON(...)).index`, on purpose.
    // `IndexStatusReadersUseTheRightLevel` in tests/test_viewer_index_async.py
    // discovers fetch sites with `<var> = await <fn>(' + ENDPOINT`, so the
    // parenthesised form is invisible to it and this watcher would go
    // unguarded -- an arm that cannot see a site cannot fail for it.
    let j = null;
    try { j = await getJSON('/api/index/status'); } catch (e) { j = null; }
    const st = (j && j.index) || null;
    // ONE STOP CONDITION, AND THE SERVER PUBLISHES IT. `settled` is true
    // exactly when nothing further is coming -- it is `_unified_done`, the
    // Event the build sets on BOTH its success and its failure path.
    //
    // This used to stop on `st.available`, then on `st.available ||
    // st.state === 'failed'`. Both are the reader reconstructing settledness
    // from what it can see: `available` is false forever after a FAILED
    // build, so waiting on it waits forever in the one case where waiting is
    // futile; and enumerating `'failed'` fixes that only until a fifth
    // terminal state is added, at which point this watcher silently returns
    // to polling its whole budget against an answer that has arrived.
    // `state` and `available` now decide only what to SAY.
    if (st && st.settled) {
      _indexWatch = null;
      S.indexPending = false;
      S.indexNote = st.available ? '' :
        'the mesh↔texture index finished without an answer (' +
        (st.state || 'unknown') +
        (st.error ? ': ' + st.error : '') + '), so geometry and skin ' +
        'columns stay blank for this client. The effect names below are ' +
        'unaffected — they do not come from that index.';
      if (st.available) done(); else drawList();
      return;
    }
    if (Date.now() - started > LIMIT_MS) {
      _indexWatch = null;
      S.indexNote = 'the mesh↔texture index is still building after ' +
        (LIMIT_MS / 60000) + ' minutes; this list is complete but the ' +
        'geometry and skin columns are not being refreshed any more. ' +
        'Reload to start watching again.';
      drawList();
      return;
    }
    const n = (st && st.progress) || null;
    S.indexNote = n && n.total
      ? `building the mesh↔texture index ${n.done}/${n.total} — the effect ` +
        `names below are all here and searchable; geometry and skin follow`
      : 'building the mesh↔texture index — the effect names below are all ' +
        'here and searchable; geometry and skin follow';
    drawList();
    _indexWatch = setTimeout(tick, 2000);
  };
  _indexWatch = setTimeout(tick, 500);
}

/* ------------------------------------------------------------- the picker */

async function loadList() {
  $('#fx-list').innerHTML = '';
  $('#fx-count').textContent = 'loading…';
  if (S.kind === '2d') {
    const j = await getJSON('/api/effect2d/list');
    S.census2d = j.coverage || null;
    S.rows = (j.rows || []).map(r => ({
      name: r.name,
      meta: r.frameCount + (r.frameCount === 1 ? ' frame' : ' frames') +
            ' · ' + r.frameIntervalMs + ' ms',
      gone: r.frameCount === 0 || r.missingFrames > 0,
      timed: r.timed,
    }));
    renderCensus2d(j.coverage || {});
  } else {
    // The 3D name list is the model catalogue's effect family; it is large,
    // so the picker is search-driven rather than a full dump.
    // `models` is the key `api_models` actually returns; `rows` is checked
    // first only so a rename on that side degrades to an empty list with a
    // stated reason rather than a TypeError in this file.
    // PAGED, AND THE PAGING IS THE WHOLE POINT.
    //
    // This was a single `limit=2000` call. `/api/models` caps `limit` at 2000
    // server-side (`min(int(arg("limit","300")), 2000)`), so on an install
    // with more effects than that the tail was NEVER SENT TO THE BROWSER --
    // not truncated in the list, not filtered out, simply absent from
    // `S.rows`. MEASURED on Classic Conquer 2.0, 2,255 effects: `tj` sits
    // past index 2000 and the viewer could not reach it by scrolling, by
    // filtering, or by typing its exact name, because the search box filters
    // `S.rows` and `tj` was not in it. The owner reported it as "I can't find
    // this effect"; it was not there to find.
    //
    // The endpoint has always supported `offset` (`matched[offset:offset+
    // limit]`) and no client code sent it. So: page until we have `total`.
    // At limit=2000 that is two requests for this install and one for most.
    const rows = [];
    let fxOffset = 0;
    let fxTotal = null;
    // THE LIST NO LONGER WAITS FOR THE MESH<->TEXTURE INDEX. `/api/models`
    // used to reach `Catalog.models`, which blocks on that build -- MEASURED
    // 64.33 s on a cold CCO against 0.05 s warm -- so this picker showed
    // `loading…` for the length of the build with nothing said about why.
    // The rows themselves were never the reason: all 2,260 effect rows are
    // identical cold and warm. The server now answers with what it has and
    // sets `indexPending`; see `Catalog.models_now`.
    let pending = false;
    // A server that stops advancing must end the loop rather than spin. The
    // guard is the PAGE COUNT, not a row count, so a short final page cannot
    // be mistaken for a stall.
    for (let page = 0; page < 64; page++) {
      const j2 = await getJSON('/api/models?kind=effect&limit=2000&offset=' + fxOffset);
      const got = j2.rows || j2.models || [];
      // ANY page may carry it: the build can land between page 1 and page 2,
      // so this is an OR over the pages and not the last response's value.
      if (j2.indexPending) pending = true;
      if (fxTotal === null) fxTotal = Number(j2.total);
      if (!got.length) break;
      rows.push(...got);
      fxOffset += got.length;
      if (!Number.isFinite(fxTotal) || rows.length >= fxTotal) break;
    }
    // The census render wants the first page's shape; it reads counts, not
    // the row array, so the last response is as good as the first.
    const j = await getJSON('/api/models?kind=effect&limit=1');
    // SAY SO IF THE PAGING DID NOT COMPLETE. A short list that looks whole is
    // exactly the failure this replaced.
    S.fetchShort = Number.isFinite(fxTotal) && rows.length < fxTotal
      ? `the server reports ${fxTotal} effects and only ${rows.length} were `
        + `fetched -- this list is INCOMPLETE and the missing names cannot be `
        + `found by searching it`
      : '';
    S.rows = rows.map(r => ({
      // `id` is the 3DEffect.ini name -- what `/api/effect?name=` wants.
      // `key` is `effect:<id>` -- what the Model Viewer's `#model=` wants.
      // They are different strings and using one for the other is a 404.
      name: r.id || r.ident || '',
      key: r.key || '',
      meta: r.label && r.label !== r.id ? r.label : '',
      gone: false, timed: true,
    })).filter(r => r.name);
    renderCensus3d(j);
    // Re-fetch once the build lands, so the geometry and skin columns stop
    // being provisional. The rows are already right, so this is a refresh
    // and not a retry -- nothing below waits on it.
    S.indexPending = pending;
    if (pending) whenIndexReady(() => loadList());
  }
  drawList();
}

/* ------------------------------------------------ the animation-form filter
 *
 * Three forms, and an effect can carry more than one, so this is SET
 * MEMBERSHIP and never a bucket per effect: with `ribbon` pressed the list
 * keeps every effect whose form array CONTAINS `SHAP+SMOT`, and pressing
 * `mesh` as well keeps the union of the two (matching `comod effects --form
 * ribbon --form phy`, whose default mode is `any`).
 *
 * The index is built server-side and costs one read of every container the
 * effect tables name -- 13 s on 5517, 108 s on 6609, MEASURED. So the page
 * never waits for it: the list renders unfiltered immediately, the buttons
 * stay disabled with a stated reason, and a poll enables them when the build
 * lands. AN EMPTY `forms` IS NOT "this client has no ribbons" and the note
 * below says which of the two you are looking at.
 */

async function loadForms() {
  const j = await getJSON('/api/effect/forms');
  S.formStatus = j;
  S.formState = j.state || 'failed';
  S.forms = j.forms || {};
  renderFormRow();
  drawList();
  if (S.formState === 'building') {
    if (!S.formPoll) S.formPoll = setInterval(loadForms, 2000);
  } else if (S.formPoll) {
    clearInterval(S.formPoll); S.formPoll = null;
  }
}

function renderFormRow() {
  const row = $('#fx-formrow');
  row.hidden = S.kind !== '3d';
  if (row.hidden) return;
  const ready = S.formState === 'ready';
  for (const [form, sel] of Object.entries(FORM_BTN)) {
    const b = $(sel);
    b.disabled = !ready;
    b.setAttribute('aria-pressed', String(S.want.has(form)));
  }
  const n = $('#fx-form-note');
  const st = S.formStatus || {};
  if (S.formState === 'building') {
    const p = st.progress || {};
    n.textContent = `Reading every effect container to classify it — ` +
      `${p.done || 0} of ${p.total || '?'} effects, ` +
      `${st.elapsedSeconds || 0}s. The list above is unfiltered until this ` +
      `finishes; it is not an answer about the client yet.`;
  } else if (S.formState === 'failed') {
    n.textContent = 'The form index could not be built: ' +
      (st.error || 'no reason given') + '. The list is unfiltered.';
  } else if (ready) {
    const c = st.byForm || {};
    // All three counts, always, so an absent form reads as a measured zero.
    n.textContent =
      `mesh ${c[FORM_PHY] || 0} · ribbon ${c[FORM_SHAP] || 0} · ` +
      `particles ${c[FORM_PTCL] || 0} of ${st.effects} effects — ` +
      `${st.multiForm} carry more than one form and appear under each, so ` +
      `these do not sum.`;
  } else {
    n.textContent = '';
  }
}

/** Does this effect pass the current form filter? Membership, and an empty
 *  filter passes everything. An effect the index has no row for is KEPT and
 *  marked, never silently dropped: dropping it would make an incomplete index
 *  look like a smaller client. */
function passesForm(name) {
  if (!S.want.size || S.formState !== 'ready') return true;
  const got = (S.forms && S.forms[name]) || null;
  if (got === null) return true;
  return got.some(f => S.want.has(f));
}

function toggleForm(form) {
  if (S.want.has(form)) S.want.delete(form); else S.want.add(form);
  renderFormRow();
  refilter();
}

/* ------------------------------------------- the breakage-state filter
 *
 * THE OWNER'S ASK, 2026-09-08: the Effects Viewer's whole / partially
 * resolved / broken buckets, as a dropdown on this list. The classification
 * is the CENSUS's -- `fxview.js` builds it and hands it here through
 * `censusUpdate` -- and this file never computes it, because a second
 * classifier would drift from the numbers the Developer Notes panel prints
 * and the two would disagree on screen about the same install.
 *
 * A FOURTH BUCKET IS NOT A BUCKET OF THIS LIST. `referenced-but-undefined` is
 * a name a RULE points at that `3DEffect` does not define, so it is not a row
 * here and no option can reach it. It is counted in the census panel and its
 * names are clickable there. Filing it under BROKEN would send someone
 * hunting for a missing mesh that was never referenced.
 */

const STATE_LABEL = {
  whole: 'WHOLE',
  partial: 'PARTIALLY RESOLVED',
  broken: 'BROKEN',
};

/** Does this effect pass the state filter? UNMEASURED PASSES, always.
 *
 *  Three separate ways this returns true without looking at the effect, and
 *  each is a refusal rather than a default: no state picked, the census is
 *  not ready, or the list is the 2D flipbook table -- which the census does
 *  not classify at all, because a census of `3DEffect` says nothing about
 *  `ini/effect.ini`. Returning false in any of those would empty the list and
 *  present that as a finding about the client. */
function passesState(name) {
  const want = $('#fx-state').value;
  if (!want) return true;
  if (S.censusState !== 'ready') return true;
  if (S.kind !== '3d') return true;
  return (S.states[name] || '') === want;
}

/** The tagged-only filter. Tags live in `fxview.js` (they are `core/tags.py`
 *  overlays keyed `fx:<name>`), so this asks rather than keeping a copy. With
 *  fxview.js absent the checkbox filters nothing instead of everything. */
function passesTagged(name) {
  if (!$('#fx-tagged-only').checked) return true;
  const V = window.CoFxView;
  if (!V || !V.isTagged) return true;
  return !!V.isTagged(name);
}

/** Rebuild the dropdown from the states the SERVER named, and set whether it
 *  is usable AT ALL.
 *
 *  The options are never written out in this file. A fourth state added
 *  server-side turns up here on its own; a hard-coded list would silently
 *  drop it, and the effects in it would then be unreachable through a control
 *  that still looked complete. Pass no argument to keep the options already
 *  there and re-decide only the enabled state.
 *
 *  TWO CONDITIONS, NOT ONE, AND THE SECOND WAS MISSING UNTIL IT WAS DRIVEN.
 *  The census must be `ready` AND the list must be the 3D one. This function
 *  is called from two places -- the kind switch and the census arriving --
 *  and with only the census condition here the later of the two won: on a
 *  page sitting on the 2D flipbook list, the census landing re-enabled a
 *  control that classifies `ini/3DEffect` rows and can say nothing about
 *  `ini/effect.ini`. `passesState` refuses in 2D, so picking BROKEN there
 *  filtered NOTHING while the control read as engaged -- a filter that lies
 *  about being applied, which is worse than one that is greyed out with a
 *  reason. Both conditions live here so the two callers cannot disagree. */
function syncStateFilter(all) {
  const sel = $('#fx-state');
  const keep = sel.value;
  if (all) {
    sel.innerHTML = '';
    sel.appendChild(el('option', '', 'any state'));
    sel.lastChild.value = '';
    for (const st of all) {
      const o = el('option', '', STATE_LABEL[st] || st);
      o.value = st;
      sel.appendChild(o);
    }
    sel.value = keep;
  }
  if (S.kind !== '3d') {
    sel.disabled = true;
    sel.title = 'the whole/partial/broken census classifies ini/3DEffect ' +
      'rows. The 2D flipbook table (ini/effect.ini) is a different file and ' +
      'is NOT classified by it — this filter would be answering about the ' +
      'wrong table.';
    return;
  }
  const ready = S.censusState === 'ready';
  sel.disabled = !ready;
  sel.title = ready ? '' : (S.censusNote ||
    'the census is still building — filtering by state would hide effects ' +
    'that are simply not classified yet');
}

/** `fxview.js` calls this every time the census build reports. It is the ONE
 *  route by which a classification reaches this file. */
function censusUpdate(info) {
  const j = info || {};
  S.censusState = j.state || '';
  S.states = (S.censusState === 'ready' && j.states) ? j.states : {};
  S.censusNote = j.note || '';
  syncStateFilter(j.allStates || []);
  drawList();
}

/** The census panel's "show these" buttons. Picking a bucket IS the filter --
 *  a number you cannot open is a number you cannot check. */
function setStateFilter(st) {
  const sel = $('#fx-state');
  if (sel.disabled) return;
  sel.value = st;
  if (S.kind !== '3d') setKind('3d');
  drawList();
}

/** How many rows one page of the effect list holds. Kept at the number the
 *  hard slice used, so the page looks the same until you reach the end of it
 *  -- what changes is that there is now an end to reach. */
const FX_PAGE = 600;

/** Prev / Next under the list. Rendered ONLY when there is more than one page,
 *  because a disabled pager on a 12-row list is furniture that teaches the eye
 *  to ignore the control exactly where it will later matter. */
function drawPager(pages) {
  const host = $('#fx-pager');
  if (!host) return;
  host.innerHTML = '';
  if (pages <= 1) { host.hidden = true; return; }
  host.hidden = false;
  const mk = (text, to, enabled) => {
    const b = el('button', 'fx-pagebtn', text);
    b.type = 'button';
    b.disabled = !enabled;
    if (enabled) b.addEventListener('click', () => { S.page = to; drawList(); });
    return b;
  };
  host.appendChild(mk('\u2039 prev', S.page - 1, S.page > 0));
  host.appendChild(el('span', 'mut small', ` ${S.page + 1} / ${pages} `));
  host.appendChild(mk('next \u203a', S.page + 1, S.page < pages - 1));
}

/** Put every filter back to "everything", and say what was on.
 *
 *  FOUR filters narrow this list -- the search box, the form buttons, the
 *  state dropdown and `tagged only` -- and they sit in four different places
 *  on the rail. A list narrowed by one the eye has stopped seeing looks like
 *  an install that simply does not have many effects, which is the same false
 *  negative as an unreachable page: the screen is not lying, it is just not
 *  saying which of its own controls produced what you see.
 *
 *  It reports what it cleared instead of clearing silently, because a button
 *  that changes a list without saying why teaches you to press it and then
 *  re-derive the state by eye.
 */
function resetFilters() {
  const was = [];
  const q = $('#fx-search');
  if (q && q.value) { was.push('search ' + JSON.stringify(q.value)); q.value = ''; }
  if (S.want && S.want.size) {
    was.push(S.want.size === 1 ? '1 form filter' : S.want.size + ' form filters');
    S.want.clear();
    renderFormRow();
  }
  const st = $('#fx-state');
  if (st && st.value) { was.push('state ' + JSON.stringify(st.value)); st.value = ''; }
  const tg = $('#fx-tagged-only');
  if (tg && tg.checked) { was.push('tagged only'); tg.checked = false; }
  S.resetNote = was.length ? 'cleared: ' + was.join(', ') : '';
  refilter();
}

/** Show the button only while something is actually filtering. A control that
 *  is always present and usually a no-op is furniture; one that appears when
 *  it has work to do is a status light as well as a button. */
function syncResetButton() {
  const b = $('#fx-reset');
  if (!b) return;
  const on = !!(($('#fx-search') || {}).value)
          || !!(S.want && S.want.size)
          || !!(($('#fx-state') || {}).value)
          || !!(($('#fx-tagged-only') || {}).checked);
  b.hidden = !on;
}

/** Re-filter from page one.
 *
 *  Anything that changes WHAT is being filtered goes through this, never
 *  `drawList` directly: staying on page 4 while the result set shrinks to 12
 *  rows renders an empty pane, and an empty pane reads as "nothing matched".
 *  That is a false negative produced by the pager itself, which would be a
 *  poor trade for the one it was added to fix. Selecting an effect does NOT
 *  come through here -- that must not move you off the page you are reading.
 */
function refilter() {
  S.page = 0;
  drawList();
}

function drawList() {
  const q = ($('#fx-search').value || '').trim().toLowerCase();
  const box = $('#fx-list');
  box.innerHTML = '';
  const hits = S.rows.filter(r => (!q || r.name.toLowerCase().includes(q))
                                  && passesForm(r.name)
                                  && passesState(r.name)
                                  && passesTagged(r.name));
  // PAGED RENDER. `hits.slice(0, 600)` drew the first 600 matches and there
  // was no control anywhere on the page that reached row 601 -- the count line
  // said "(first 600 shown)" and that was the entire affordance. Rows beyond
  // it were reachable only by narrowing the filter until they floated into the
  // top 600, which is not navigation.
  const pageSize = FX_PAGE;
  const pages = Math.max(1, Math.ceil(hits.length / pageSize));
  if (S.page >= pages) S.page = pages - 1;
  if (S.page < 0) S.page = 0;
  const from = S.page * pageSize;
  for (const r of hits.slice(from, from + pageSize)) {
    const row = el('div', 'row' + (r.gone ? ' gone' : ''));
    row.setAttribute('aria-selected', r.name === S.name ? 'true' : 'false');
    row.appendChild(el('span', 'name', r.name));
    // The forms this effect carries, as the same P/S/T shorthand the CLI
    // prints. Several letters on one row IS the multi-form case.
    const got = (S.kind === '3d' && S.forms && S.forms[r.name]) || null;
    const badge = got
      ? [[FORM_PHY, 'P'], [FORM_SHAP, 'S'], [FORM_PTCL, 'T']]
          .filter(([f]) => got.includes(f)).map(([, c]) => c).join('')
      : '';
    const meta = el('span', 'n', badge ? badge + ' · ' + r.meta : r.meta);
    // THE STATE BADGE IS DRAWN ONLY WHEN THE CENSUS IS READY. A dash on every
    // row while it builds is honest; a badge reading "whole" because the map
    // has no entry yet would be a claim made out of an absence.
    if (S.kind === '3d' && S.censusState === 'ready') {
      const st = S.states[r.name];
      const dot = el('span', 'fx-statehint fx-cs-' + (st || 'unknown'),
                     st ? st.charAt(0).toUpperCase() : '?');
      dot.title = st
        ? STATE_LABEL[st] || st
        : 'the census is ready and did not classify this name — UNMEASURED, ' +
          'not whole';
      meta.appendChild(document.createTextNode(' '));
      meta.appendChild(dot);
    }
    row.appendChild(meta);
    row.onclick = () => select(r.name);
    box.appendChild(row);
  }
  // WHICH FILTERS ARE ACTUALLY ON, named one by one. "12 of 3,391" with no
  // reason is the same screen whether a filter is narrowing the list or the
  // install really holds twelve.
  const on = [];
  if (S.kind === '3d' && S.want.size && S.formState === 'ready') on.push('form');
  if ($('#fx-state').value && S.censusState === 'ready' && S.kind === '3d') {
    on.push('state');
  }
  if ($('#fx-tagged-only').checked) on.push('tagged');
  const stale = $('#fx-state').value && S.censusState !== 'ready'
    ? ' — state filter INACTIVE, the census is still building' : '';
  const shownFrom = hits.length ? from + 1 : 0;
  const shownTo = Math.min(from + pageSize, hits.length);
  $('#fx-count').textContent =
    `${hits.length} of ${S.rows.length}` +
    (on.length ? ` (${on.join(' + ')} filter on)` : '') +
    (pages > 1 ? ` — showing ${shownFrom}\u2013${shownTo}, page ${S.page + 1} of ${pages}`
               : '') + stale;
  drawPager(pages);
  syncResetButton();
  if (S.resetNote) {
    box.appendChild(el('div', 'mut small', S.resetNote));
    S.resetNote = '';
  }
  // Rule: a list that could not be fully fetched says so ON the list, not in
  // a console message nobody reads.
  if (S.fetchShort) {
    const w = el('div', 'fx-bad', S.fetchShort);
    box.appendChild(w);
  }
  // A DIFFERENT CLAIM FROM `fetchShort` ABOVE, and they must not be confused:
  // that one says the list is INCOMPLETE, this one says the list is complete
  // and some COLUMNS are not filled in yet. Saying "loading…" for both is
  // what made a 64 s index build look like a wedged page.
  // 3D ONLY, the same scoping `renderFormRow` uses. The watcher outlives a
  // switch to the 2D list -- it is a page-level poll, not a per-list one --
  // and without this the flipbook picker grew a note about a mesh<->texture
  // index that has nothing to do with it.
  if (S.indexNote && S.kind === '3d') {
    box.appendChild(el('div', 'mut small', S.indexNote));
  }
}

function renderCensus2d(c) {
  const b = $('#fx-census');
  b.innerHTML = '';
  const kv = el('div', 'kv');
  const add = (k, v) => { kv.appendChild(el('b', '', k));
                          kv.appendChild(el('span', '', String(v))); };
  add('table', c.iniPath ? c.iniPath.split(/[\\/]/).pop() : 'ABSENT');
  add('frames from', c.aniSource || 'ABSENT');
  add('rows', c.rows);
  add('resolved', `${c.resolved} (${c.multiFrame} multi-frame)`);
  add('unresolved', c.unresolved);
  add('untimed .ani', c.untimedSequences);
  b.appendChild(kv);
  if (c.unresolved) {
    b.appendChild(el('div', 'fx-note',
      'Unresolved rows name an AniTitle that ani/effect.ani has no section ' +
      'for: ' + (c.unresolvedNames || []).join(', ') + '. That is this ' +
      'install’s data, not a decode failure.'));
  }
  if (!c.available) {
    b.appendChild(el('div', 'fx-note',
      'NOTHING WAS READ. This install ships neither ini/effect.ini nor ' +
      'ani/effect.ani, so an empty picker here says nothing about effects.'));
  }
}

function renderCensus3d(j) {
  const b = $('#fx-census');
  b.innerHTML = '';
  const kv = el('div', 'kv');
  const add = (k, v) => { kv.appendChild(el('b', '', k));
                          kv.appendChild(el('span', '', String(v))); };
  add('names', S.rows.length);
  b.appendChild(kv);
  b.appendChild(el('div', 'fx-note',
    'These are ini/3DEffect.ini scenes. Geometry, MOTI motion and particles ' +
    'are drawn here by the Model Viewer’s own WebGL stage — the same ' +
    'gl.js Viewer and fx.js EffectInstance, constructed on this page’s ' +
    'canvas. One renderer, not two. "Play in Model Viewer" opens the same ' +
    'scene beside a character, which is what that page adds.'));
  if (!S.rows.length) {
    b.appendChild(el('div', 'fx-note',
      'NOTHING WAS LISTED. An empty list here is about the catalogue, not ' +
      'about the install having no 3D effects.'));
  }
}

/* -------------------------------------------------------------- selection */

async function select(name) {
  S.name = name;
  S.t = 0;
  S.def = null;
  S.images = [];
  drawList();
  location.hash = `#kind=${S.kind}&name=${encodeURIComponent(name)}`;
  status(`loading ${name}…`);
  if (S.kind === '2d') {
    const j = await getJSON('/api/effect2d?name=' + encodeURIComponent(name));
    if (!j.found) { fail(j.error || 'not found'); return; }
    S.def = j;
    await preload2d(j);
  } else {
    const j = await getJSON('/api/effect?name=' + encodeURIComponent(name));
    if (!j.found) { fail(j.error || 'not found'); return; }
    S.def = j;
  }
  status(name);
  renderPanels();
  restart();
  tellFxView(name);
}

/** Hand the selection to the library half.
 *
 *  3D ONLY, and that is not a shortcut: `fxview.js` resolves a row of
 *  `3DEffect`, and the 2D flipbook is `ini/effect.ini` -- a different table
 *  with a different grammar and different names. Asking it about a 2D effect
 *  would get a confident "the tables hold NO record under this name", which
 *  is a true sentence about the wrong table and reads as a broken effect.
 *
 *  GUARDED, so this page still works with `fxview.js` absent or broken: the
 *  stage, the list and every filter but `state` and `tagged` are this file's
 *  own. A page that dies whole because its second script failed is worse than
 *  one that loses a column and says which. */
function tellFxView(name) {
  const V = window.CoFxView;
  if (!V || !V.select) return;
  if (S.kind === '3d') V.select(name);
}

function fail(msg) {
  S.def = null;
  $('#fx-msg').textContent = msg;
  $('#fx-declared').textContent = msg;
  $('#fx-frames').textContent = '';
  $('#fx-camera').textContent = '';
  renderApprox();
  clearCanvas();
  status(msg);
}

/** Fetch every frame through `/api/texture`, which decodes DDS/TGA/BMP/PNG to
 *  PNG server-side. Index-aligned with `def.frames` INCLUDING the misses: a
 *  frame the install does not ship stays a hole in the array, so frame 4 is
 *  still frame 4 and the timeline does not shift under a missing file. */
async function preload2d(def) {
  const imgs = new Array((def.frames || []).length).fill(null);
  await Promise.all((def.frames || []).map((f, i) => new Promise(res => {
    if (!f.found) { res(); return; }
    const im = new Image();
    im.onload = () => { imgs[i] = im; res(); };
    im.onerror = () => res();
    im.src = '/api/texture?path=' + encodeURIComponent(f.path);
  })));
  S.images = imgs;
}

/* ------------------------------------------------------------ 2D playback */

/** Which frame is on screen at `ms`, or null for a blank stretch.
 *
 *  A LINE-FOR-LINE MIRROR OF `effect2d.Effect2D.frame_at`. It is duplicated
 *  rather than fetched because scrubbing must be instant, and the Python side
 *  is the one the gate exercises -- `tests/test_effect_preview.py` asserts the
 *  two agree at every millisecond of a shipped effect's timeline, so a drift
 *  between them is a red test rather than a picture nobody checked. */
function frameAt(def, ms) {
  const n = (def.frames || []).length;
  if (!n) return null;
  const t = ms - (def.delayMs || 0);
  if (t < 0) return null;
  const interval = Math.max(1, def.frameIntervalMs || 1);
  const loopMs = n * interval;
  const cycle = loopMs + Math.max(0, def.loopIntervalMs || 0);
  if (cycle <= 0) return 0;
  const k = Math.floor(t / cycle);
  if (!def.endless && (def.loopTime || 0) > 0 && k >= def.loopTime) return null;
  const within = t - k * cycle;
  if (within >= loopMs) return null;
  return Math.min(Math.floor(within / interval), n - 1);
}

/** The CCFL kind-13 scroll counter. A MIRROR OF `effectplay.wall_counter`.
 *
 *  `counter = (floor(wallMs) >>> 0) / periodMs`, floored. The mesh's scroll is
 *  `uv += step * counter`, and the client computes the same number as
 *  `timeGetTime() / period` -- an absolute, shared wall clock, where `frameAt`
 *  above is per-instance and restarts when an effect spawns. Different
 *  animations, not different constants.
 *
 *  **`>>> 0` AND NOT `& 0xFFFFFFFF`, AND THIS IS THE WHOLE REASON THIS
 *  FUNCTION HAS A GATE.** JS `&` coerces both operands to a SIGNED int32, so
 *  `4294967295 & 0xFFFFFFFF` is `-1`, not `4294967295` -- a Python-looking
 *  line that silently disagrees with Python above 2^31. `>>> 0` is ToUint32
 *  and is the operator that means what the client's DWORD division means.
 *  `tests/test_effect_preview.JsMirrorsPython` asserts the two agree across
 *  that boundary rather than only on small numbers, where they cannot differ.
 *
 *  `wallMs` is SUPPLIED, never read here: every consumer takes one number from
 *  one place per frame, or two parts of one effect disagree with each other.
 *  Callers multiply in double -- the counter reaches ~1.3e8, so the fraction
 *  of `counter * step` lives in the low bits and a float32 multiply throws
 *  away exactly the visible quantity.
 *
 *  Returns `null` when there is no kind 13 (absent, the -1 sentinel, or
 *  non-positive), which means USE `frameAt`. */
function wallCounter(wallMs, periodMs) {
  if (periodMs == null) return null;
  const period = Math.trunc(periodMs);
  if (!(period > 0)) return null;
  return Math.floor((Math.floor(wallMs) >>> 0) / period);
}

function totalMs(def) {
  if (def.durationMs != null) return def.durationMs;
  // Endless: scrub over three cycles, which is enough to see the loop AND the
  // LoopInterval gap that only appears between repeats.
  return Math.max(1000, (def.cycleMs || 1000) * 3);
}

function clearCanvas() {
  const c = $('#fx-canvas'), g = c.getContext('2d');
  g.clearRect(0, 0, c.width, c.height);
  if ($('#fx-checker').checked) checker(g, c.width, c.height);
}

/** A checkerboard, so a black additive spark on a black page is visible.
 *  OURS. The game draws over the world, not over a checkerboard. */
function checker(g, w, h) {
  const s = 16;
  for (let y = 0; y < h; y += s) {
    for (let x = 0; x < w; x += s) {
      g.fillStyle = ((x / s + y / s) & 1) ? '#242629' : '#1b1d20';
      g.fillRect(x, y, s, s);
    }
  }
}

function draw2d() {
  const def = S.def;
  const c = $('#fx-canvas'), g = c.getContext('2d');
  g.globalCompositeOperation = 'source-over';
  g.clearRect(0, 0, c.width, c.height);
  if ($('#fx-checker').checked) checker(g, c.width, c.height);
  const i = frameAt(def, S.t);
  const im = i == null ? null : S.images[i];
  markFrameList(i);
  if (i == null) {
    /* A ZERO-FRAME EFFECT IS NOT A GAP BETWEEN REPEATS. `frameAt` returns
     * null both when the clock is between frames AND when the row declares
     * no frames at all, and `blankReason` only knows the first kind -- so an
     * effect with nothing to draw reported "LoopInterval=500 ms -- the gap
     * between repeats", which is a plausible, specific and wrong answer
     * about a file that has no frames in it. Asked before the reason is
     * composed, because `blankReason` cannot tell these apart from the
     * clock alone. */
    const n = (def.frames && def.frames.length) || 0;
    $('#fx-msg').textContent = n === 0
      ? `no frames — the row ${def.aniSource || '(unknown .ani)'} declares none`
      : blankReason(def, S.t);
    return;
  }
  if (!im) {
    $('#fx-msg').textContent =
      `frame ${i} is declared by ${def.aniSource} and this install does not ship it`;
    return;
  }
  $('#fx-msg').textContent = '';
  // `OffsetX`/`OffsetY` are the client's SCREEN anchor offset -- where the
  // sprite sits relative to whatever it is attached to. There is no attachment
  // here, so the frame is centred and the offset is REPORTED rather than
  // applied: applying it against nothing would move the picture for a reason
  // the preview cannot show.
  g.globalCompositeOperation = $('#fx-additive').checked ? 'lighter' : 'source-over';
  const sc = Math.min(1, (c.width - 16) / im.width, (c.height - 16) / im.height);
  const w = im.width * sc, h = im.height * sc;
  g.drawImage(im, (c.width - w) / 2, (c.height - h) / 2, w, h);
  g.globalCompositeOperation = 'source-over';
}

/** Why the canvas is blank right now. Three different reasons and the user
 *  should not have to guess which. */
function blankReason(def, ms) {
  if (ms < (def.delayMs || 0)) return `Delay=${def.delayMs} ms — nothing is drawn yet`;
  const d = def.durationMs;
  if (d != null && ms >= d) return `finished — LoopTime=${def.loopTime}`;
  return `LoopInterval=${def.loopIntervalMs} ms — the gap between repeats`;
}

function markFrameList(i) {
  const ol = $('#fx-frames').querySelector('ol');
  if (!ol) return;
  [...ol.children].forEach((li, k) => li.classList.toggle('on', k === i));
}

/* ------------------------------------------------------------ the 3D stage
 *
 * TWO THINGS THE PAYLOAD PUTS SOMEWHERE OTHER THAN WHERE YOU WOULD GUESS, AND
 * BOTH OF THEM READ AS "the renderer is broken" WHEN GUESSED WRONG.
 *
 *   The CAMERAS ARE ON THE SCENE, NOT ON THE PARTS. `effectplay.py` collects
 *   every layer's `CAME` into ONE top-level `cameras` list (and `camera`, the
 *   first of them). No part ever carries a `camera` key. Code that walks
 *   `layer.parts[].camera` finds nothing on every effect ever authored, so
 *   the artist's camera is never applied and the page reports a fitted view
 *   it did not fit.
 *
 *   THE BOUNDS ARE ON THE GEOMETRY, NOT ON THE PART, and they are a PAIR OF
 *   ARRAYS. The key is `part.geometry.bboxRender`, shaped
 *   `[[minx,miny,minz],[maxx,maxy,maxz]]` -- not `part.bounds` and not
 *   `{min,max}`. Code that reads `part.bounds.min` gets `undefined`, skips
 *   every part, and computes no box at all.
 *
 * Those are not hypotheticals: `fxview.js` had all of it wrong at the same
 * time and, on top of that, called `viewer.frameBounds(...)`, which is not a
 * method `gl.js` has ever defined -- guarded by `if (viewer.frameBounds)`, so
 * it failed silently forever. Three dead camera paths in one function, none
 * of which could raise. This is why `frame3d` REPORTS which branch it took
 * and what box it computed, in the Camera pane, rather than trusting itself.
 */

/** The effect's authored camera, or null. TOP-LEVEL, see above. */
function sceneCamera(def) {
  const cams = (def && def.cameras) || [];
  if (cams.length) return cams[0];
  return (def && def.camera) || null;
}

/** The union of every part's RENDER-space bounding box, as {center, radius},
 *  or null when not one part declared one.
 *
 *  A DEGENERATE AXIS IS NORMAL HERE AND IS NOT A FAILURE. Effect parts are
 *  flat camera-facing quads: 2NDMetempsychosis' five planes are all exactly
 *  z = 0, so the z extent is 0.0 and always will be. The radius is therefore
 *  the LARGEST half-extent, never the smallest and never the mean -- a fit
 *  that averaged in a zero axis would pull the camera a third of the way into
 *  the geometry. Only a box that is degenerate on ALL THREE axes, or that
 *  carries a NaN, is unusable, and those two are reported rather than fitted:
 *  a camera placed from a NaN points nowhere and draws an empty frame with no
 *  error, which is precisely the failure this page was accused of. */
function sceneBounds(def) {
  let lo = null, hi = null, parts = 0;
  for (const lay of (def && def.layers) || []) {
    for (const p of lay.parts || []) {
      const bb = p.geometry && p.geometry.bboxRender;
      if (!bb || bb.length !== 2 || !bb[0] || !bb[1]) continue;
      parts++;
      if (!lo) { lo = bb[0].slice(); hi = bb[1].slice(); continue; }
      for (let i = 0; i < 3; i++) {
        lo[i] = Math.min(lo[i], bb[0][i]);
        hi[i] = Math.max(hi[i], bb[1][i]);
      }
    }
  }
  if (!lo) return null;
  const center = [0, 1, 2].map(i => (lo[i] + hi[i]) / 2);
  const half = [0, 1, 2].map(i => (hi[i] - lo[i]) / 2);
  const radius = Math.max(half[0], half[1], half[2]);
  const finite = center.concat(half).every(Number.isFinite);
  return { min: lo, max: hi, center, half, radius, parts,
           degenerate: !(radius > 0), nan: !finite,
           usable: finite && radius > 0 };
}

/** Point the stage at the effect. Returns what it did, for the Camera pane.
 *
 *  The order is the artist first, ours second, and NOTHING third -- a state
 *  that has to be nameable, because "no box to fit" is a real answer for an
 *  effect whose only parts are undecoded, and leaving the camera wherever the
 *  last effect left it would be indistinguishable from a dead renderer. */
function frame3d(def) {
  const cam = sceneCamera(def);
  if (cam && S.viewer.applyAuthoredCamera(cam)) {
    return { source: 'CAME', camera: cam, bounds: null };
  }
  const b = sceneBounds(def);
  if (b && b.usable) {
    // `radius` also sets the far plane and the zoom stops (gl.js `draw` and
    // `_bindInput`), which default to a 100-unit model; an effect two orders
    // of magnitude off that would clip without this.
    S.viewer.radius = b.radius;
    S.viewer.focusOn(b.center, b.radius);
    return { source: 'fitted', camera: null, bounds: b,
             rejected: cam ? 'the CAME did not resolve to a direction' : '' };
  }
  return { source: 'none', camera: null, bounds: b };
}

/** Build the stage once. Returns false and records why if WebGL is refused --
 *  which must be SAID, not silently drawn around. */
function ensureViewer() {
  if (S.viewer) return true;
  try {
    S.viewer = new Viewer($('#fx-gl'), { camScope: 'effects3d' });
    S.glError = '';
    return true;
  } catch (e) {
    S.glError = 'the WebGL stage could not be created: ' + e;
    return false;
  }
}

/** How long the scrubber should span. `durationMs` is ONE cycle of playing
 *  frames and leaves out both blank stretches, so it is not the timeline: an
 *  effect with Delay=500 would have its first half-second unreachable. */
function total3d(def) {
  const n = def.effectiveFrames || def.frames || 1;
  const cycle = n * Math.max(1, def.frameIntervalMs || 1) +
                Math.max(0, def.loopIntervalMs || 0);
  const delay = Math.max(0, def.delayMs || 0);
  // Endless: three cycles, enough to show the loop AND the gap between
  // repeats. Finite: exactly what the row declares, and not one repeat more.
  const loops = def.endless ? 3 : Math.max(1, def.loopTime || 1);
  return Math.max(1, delay + cycle * loops);
}

/** Fit the camera to EVERY effect on the stage, not to one of them.
 *
 *  **THIS WAS MEASURED AS A DEFECT BEFORE IT WAS WRITTEN, and the first
 *  version of `playSubject` had it.** That version framed on the PRIMARY
 *  effect -- the first entry with a scene -- and shipped a comment admitting
 *  a union would be better. It was not a nicety. Measured in headless Chrome
 *  on appearance 410009, whose three effects are `Flash4102`, `410009` and
 *  `m-b02`, by hiding each instance and differencing the framebuffer:
 *
 *      total absolute difference when hidden, 410009 also hidden:
 *          Flash4102     0
 *          m-b02         0        <- but 1,686 lit pixels when played ALONE
 *
 *  `m-b02` was on the stage, built, textured, and reported `drawn -- 3 of 3
 *  layer(s)`, and it was **outside the camera**. The primary's bounds were
 *  unusable (`Flash4102` is a ribbon and contributes no bbox), so `frame3d`
 *  returned `source: 'none'` and left the camera wherever the previous
 *  effect had put it. Two of three effects were invisible and every readout
 *  on the page said they were fine.
 *
 *  That is the exact failure the feature exists to prevent, arriving one
 *  level up: a control that reports success while the user sees nothing.
 *
 *  THE UNION IS OVER `min`/`max`, NOT over centres and radii. Averaging
 *  centres and taking the largest radius is the obvious version and it is
 *  wrong: two effects far apart get a box centred between them that contains
 *  neither. `sceneBounds` already returns the real corners.
 *
 *  An effect with NO usable bounds contributes nothing to the union rather
 *  than collapsing it -- a ribbon has no bbox and must not veto the framing
 *  of the meshes beside it. If NOTHING has bounds, this returns null and the
 *  caller falls back to `frame3d` on the primary, which is honest about
 *  having nothing to fit. */
function frameSubject(defs) {
  let lo = null, hi = null, used = 0;
  for (const d of defs) {
    const b = sceneBounds(d);
    if (!b || !b.usable) continue;
    used++;
    if (!lo) { lo = b.min.slice(); hi = b.max.slice(); continue; }
    for (let i = 0; i < 3; i++) {
      lo[i] = Math.min(lo[i], b.min[i]);
      hi[i] = Math.max(hi[i], b.max[i]);
    }
  }
  if (!lo) return null;
  const center = [0, 1, 2].map(i => (lo[i] + hi[i]) / 2);
  const half = [0, 1, 2].map(i => (hi[i] - lo[i]) / 2);
  const radius = Math.max(half[0], half[1], half[2]);
  if (!(radius > 0) || !center.concat(half).every(Number.isFinite)) return null;
  S.viewer.radius = radius;
  S.viewer.focusOn(center, radius);
  return { source: 'fitted-union', camera: null, fitted: used,
           of: defs.length,
           bounds: { min: lo, max: hi, center, half, radius, usable: true } };
}


/** The transport's span across EVERY effect on the stage.
 *
 *  A subject's effects have different lengths and different loop rules, so
 *  the scrubber has to cover the longest of them or the tail of the longest
 *  is unreachable -- and `endless` is a property of the SET: if any one of
 *  them never ends, the composite never ends.
 *
 *  Falls back to the single `def` when nothing multi is staged, so the
 *  one-effect path is byte-for-byte the behaviour it had.
 */
function stageTotal(def) {
  if (!S.stage || !S.stage.entries.length) return total3d(def);
  return Math.max(1, ...S.stage.entries
    .filter(e => e.def)
    .map(e => total3d(e.def)));
}

function stageEndless(def) {
  if (!S.stage || !S.stage.entries.length) return !!(def && def.endless);
  return S.stage.entries.some(e => e.def && e.def.endless);
}

/** Load the scene onto the stage and start it.
 *
 *  AUTOPLAY IS NOT A UI PREFERENCE HERE (bugs_open.md #4). The clock this
 *  feeds `setEffectTime` is MONOTONIC, and every loop decision --  `Delay`
 *  before frame 0, `LoopInterval` between repeats, `LoopTime` repeats, and
 *  `endless` -- is made inside `fx.js` from the row's own declared fields.
 *  The page never wraps the clock itself. Wrapping it (`t % duration`) is how
 *  a page silently overrides a finite `LoopTime` into an endless one, because
 *  the instance can then never reach the frame at which it would report
 *  `done`. `fxview.js` did exactly that. */
/** Claim the stage for one play. Returns a token; `stale(tok)` is true once a
 *  LATER play has claimed it, and counts the abandonment in
 *  `S.playAbandoned` so a test can see the older play stand down.
 *
 *  WHY A TOKEN AND NOT THE EXISTING GUARDS. `playSubject` checked only
 *  `S.kind !== '3d'` after its fetches -- and `play3d` sets `S.kind` to '3d'
 *  too, so an older subject that was still fetching when the user picked a
 *  single effect PASSED that guard, rewrote `S.stage` after `play3d` had
 *  cleared it, replaced the viewer's one effect with its own three, and set
 *  `S.name`, which made `play3d`'s own guard drop ITS render. The page then
 *  showed the previous subject's list under the new selection -- the
 *  `{'hidden': False, 'rows': 3}` in test_effect_preview's
 *  `test_a_single_effect_shows_no_list_at_all`. Every guard compared state
 *  the other play also writes; the token is the one thing only a newer play
 *  changes. */
function claimPlay() {
  S.playGen = (S.playGen || 0) + 1;
  return S.playGen;
}
function stale(tok) {
  if (S.playGen === tok) return false;
  S.playAbandoned = (S.playAbandoned || 0) + 1;
  return true;
}

async function play3d(def) {
  if (!ensureViewer()) { $('#fx-msg').textContent = S.glError; return; }
  const tok = claimPlay();
  const v = S.viewer;
  const keys = {};
  for (const lay of def.layers || []) {
    if (lay.texture) keys[lay.index] = 'fx3d:' + lay.index;
  }
  // ONE effect: the multi-effect stage is cleared here rather than by the
  // caller, because every route into a single effect goes through this
  // function and a `stage` left standing would make the transport span an
  // effect that is no longer on screen.
  S.stage = null;
  const n = v.setEffects([{ def, role: 'model', anchor: FX.IDENT.slice(),
                            textureKeys: keys }]);
  // BACKLOG 24: tell the panel what the STAGE did with each layer.
  //
  // The two are fed by different calls -- "What it needs" by /api/fx/effect,
  // the stage by /api/effect -- and nothing reconciled them, so the panel
  // could list a layer the stage never drew and neither said so. This is the
  // reconciliation, and it is one-directional: the stage REPORTS, the panel
  // MARKS. Nothing here reads back from the panel, and nothing is refetched.
  //
  // Sent even when `n` is 0, because "every layer failed" is exactly the case
  // the panel most needs marked. The guard is only for a page where fxview.js
  // did not load -- the same defensive shape as `tellFxView`, for the same
  // reason: a page that dies whole because its second script failed is worse
  // than one that loses a column and says which.
  const _V = window.CoFxView;
  if (_V && _V.markStageBuild) {
    _V.markStageBuild(def.name, (v.fxBuild && v.fxBuild[0]) || null);
  }
  for (const lay of def.layers || []) {
    if (!lay.texture) continue;
    await v.applyTextureBundle(
      [{ key: 'fx3d:' + lay.index, path: lay.texture }],
      { pngUrl: p => '/api/texture?path=' + encodeURIComponent(p) });
    if (stale(tok)) return;
  }
  // The user may have moved on while the textures were in flight.
  if (S.name !== def.name || S.kind !== '3d') return;
  S.framing = frame3d(def);
  renderCamera3d(def);
  if (!n) {
    $('#fx-msg').textContent = (def.layers || []).length
      ? 'no drawable layer — every layer of this effect failed to load'
      : 'this effect declares no layers';
    v.draw();
    return;
  }
  $('#fx-msg').textContent = '';
  v.resetEffects();
  S.t = 0;
  S.t0 = performance.now();
  $('#fx-time').max = String(Math.round(stageTotal(def)));
  renderStageList();
  tick3d();
}

/** THE OWNER'S FEATURE -- play every effect a SUBJECT plays, together, with
 *  one row per effect and an independent switch on each.
 *
 *  *"Get COMod to work on a feature to be able to toggle individual effects
 *  for multi effect skills in the effects viewer."*  -- owner, 2026-09-10.
 *
 *  WHY THIS IS NOT BACKLOG 24, WHICH IT LOOKS LIKE. Item 24 toggles the
 *  LAYERS inside one effect. This toggles the EFFECTS inside one subject.
 *  Both are real and neither answers the other -- measured on the
 *  CONFIGURED INSTALL (`coroot.find()`; 5165-era on the box this was
 *  written on):
 *  (A drive-rooted path here would be a check-3 finding: non-prose
 *   files are scanned WHOLE, with no comment exemption as in .py.)
 *
 *      effects with 2+ LAYERS                    771 of 2,255
 *      appearances playing 2+ distinct EFFECTS 2,209 of 5,384
 *
 *  Appearance 410009 plays `410009`, `Flash4102` and `m-b02` -- an aura, a
 *  flash and an impact spark. Before this the page could not put them on the
 *  stage together at all, so there was nothing to toggle between.
 *
 *  THE EFFECT LIST IS NOT COMPUTED HERE. It comes from `/api/fx/subject`,
 *  which is `depclose.subject_effects`. The appearance key grammar -- the
 *  hi/lo split, the low-group wildcard, the all-nines sentinel -- belongs to
 *  the walk, and a second implementation of it in this file would drift from
 *  the answer it is meant to be showing. Same rule `fxview.js::openSubject`
 *  already states for the same endpoint.
 *
 *  TEXTURE KEYS ARE NAMESPACED PER INSTANCE. `play3d` uses `fx3d:<layer>`,
 *  which is unique within ONE effect and collides across several -- two
 *  effects both having a layer 0 would share one texture slot and the second
 *  bundle would silently replace the first's. `fx3d:<inst>:<layer>` cannot.
 *
 *  FRAMING USES THE PRIMARY EFFECT and says so on screen. A union of every
 *  effect's bounds would be better and is a follow-on, not a silent gap: an
 *  impact spark is small and an aura is large, so fitting the union can push
 *  the thing the user came to see into a few pixels. Stated rather than
 *  chosen quietly. */
async function playSubject(appearance) {
  if (!ensureViewer()) { $('#fx-msg').textContent = S.glError; return; }
  const tok = claimPlay();
  const v = S.viewer;
  S.kind = '3d';
  $('#fx-msg').textContent = 'resolving the subject…';

  const j = await getJSON('/api/fx/subject?appearance=' +
                          encodeURIComponent(appearance));
  if (stale(tok)) return;
  if (j.error || !j.measured) {
    S.stage = null;
    $('#fx-msg').textContent = j.error
      ? 'subject: ' + j.error
      : 'UNMEASURED — the rule tables would not load for this subject, so ' +
        'its effects are missing rather than absent';
    renderStageList();
    return;
  }
  // Distinct, in the order the rows name them, so the list on screen matches
  // the walk's order rather than an alphabetical one this page invented.
  // `r.effects` is the value PARSED by `tools/effects.py::effect_names`;
  // `r.effect` is the raw string. One rule value can name several effects on
  // the newest clients -- 675 of 7878's 4,197 rows, up to nineteen names --
  // so taking `r.effect` whole stages one "effect" whose name is a
  // comma-separated list and finds no scene for it.
  //
  // The fallback to `[r.effect]` is for a server older than this field, not
  // for a value this page could parse itself: the grammar lives in ONE place
  // and it is not here.
  const names = [];
  for (const r of (j.rows || [])) {
    for (const nm of (r.effects || (r.effect ? [r.effect] : []))) {
      if (nm && names.indexOf(nm) < 0) names.push(nm);
    }
  }
  if (!names.length) {
    S.stage = null;
    $('#fx-msg').textContent =
      'no rule row names an effect for appearance ' + appearance +
      ' — that is what the tables say, not a failure to read them';
    renderStageList();
    return;
  }

  const entries = [];
  for (const nm of names) {
    const d = await getJSON('/api/effect?name=' + encodeURIComponent(nm));
    if (stale(tok)) return;
    entries.push({ name: nm, def: (d && d.name) ? d : null,
                   error: (d && d.error) || (d && d.name ? '' : 'no scene'),
                   instIdx: -1 });
  }
  if (S.kind !== '3d') return;          // the user moved on while fetching

  const staged = [];
  for (const e of entries) {
    if (!e.def) continue;
    const keys = {};
    for (const lay of e.def.layers || []) {
      if (lay.texture) keys[lay.index] = 'fx3d:' + staged.length + ':' + lay.index;
    }
    e.instIdx = staged.length;
    staged.push({ def: e.def, role: 'model', anchor: FX.IDENT.slice(),
                  textureKeys: keys });
  }
  S.stage = { appearance: appearance, entries: entries };
  const n = v.setEffects(staged);
  for (const e of entries) {
    if (e.instIdx < 0) continue;
    for (const lay of e.def.layers || []) {
      if (!lay.texture) continue;
      await v.applyTextureBundle(
        [{ key: 'fx3d:' + e.instIdx + ':' + lay.index, path: lay.texture }],
        { pngUrl: p => '/api/texture?path=' + encodeURIComponent(p) });
      if (stale(tok)) return;
    }
  }
  if (S.kind !== '3d' || !S.stage || S.stage.appearance !== appearance) return;

  const primary = entries.find(e => e.def);
  S.name = primary ? primary.name : '';
  S.def = primary ? primary.def : null;
  // Fit EVERY staged effect, falling back to the primary only when nothing
  // on the stage has usable bounds. See `frameSubject` for what framing on
  // the primary actually cost -- two of three effects off-camera while the
  // page reported them drawn.
  if (primary) {
    const u = frameSubject(entries.filter(e => e.def).map(e => e.def));
    S.framing = u || frame3d(primary.def);
    renderCamera3d(primary.def);
  }
  $('#fx-msg').textContent = n
    ? ''
    : 'no drawable effect — every effect this subject plays failed to load';
  v.resetEffects();
  S.t = 0;
  S.t0 = performance.now();
  $('#fx-time').max = String(Math.round(stageTotal(S.def)));
  renderStageList();
  tick3d();
}


/** One row per effect on the stage, each saying what the stage DID with it
 *  and carrying its own switch.
 *
 *  Empty and hidden when a single effect is playing: a list of one is not a
 *  list, and a control that can only ever be on is the "control that can
 *  only report absence" this page has already paid for twice
 *  (bugs_open.md #6).
 *
 *  THREE STATES, and they are three different problems -- the same
 *  distinction backlog 24 draws one level down:
 *
 *      drawn      the instance built parts and is on the stage
 *      EMPTY      it built, every layer came back with nothing
 *      FAILED     its scene would not load at all
 *
 *  Only `drawn` gets a live switch. The other two are labelled and disabled,
 *  with the reason in the tooltip, because a switch that does nothing when
 *  flipped reads as a broken page rather than as a missing effect. */
function renderStageList() {
  const host = $('#fx-stagelist');
  if (!host) return;
  host.innerHTML = '';
  const st = S.stage;
  // A RENDER COUNTER, FOR THE GATE THAT COULD NOT SEE THIS LIST SETTLE.
  // `play3d` clears `S.stage` and sets `fx.length === 1` at the top, then
  // AWAITS the textures, and only then calls this function -- so a test that
  // waits on the MODEL can read the DOM while the previous subject's rows
  // are still in it. That is what `{'hidden': false, 'rows': 3}` was, twice
  // (gatelogs d5426406 on 2026-09-15 and f575598d on 2026-09-18). Bumped on
  // EVERY path out of here, including the early return, because "the list
  // was hidden" is a render too.
  S.slRender = (S.slRender || 0) + 1;
  if (!st || st.entries.length < 2) { host.hidden = true; return; }
  host.hidden = false;

  const v = S.viewer;
  const head = el('div', 'fx-sl-h');
  const live = st.entries.filter(e => e.instIdx >= 0).length;
  head.appendChild(el('b', null, 'appearance ' + st.appearance));
  head.appendChild(el('span', 'mut',
    st.entries.length + ' effect(s) this subject plays' +
    (live < st.entries.length
      ? '  ⚠ ' + (st.entries.length - live) + ' would not load'
      : '')));
  host.appendChild(head);
  const fit = (S.framing && S.framing.source === 'fitted-union')
    ? 'The camera fits all ' + S.framing.fitted + ' effect(s) with usable ' +
      'bounds, so a small spark beside a large aura is small on screen ' +
      'rather than off it.'
    : 'The camera could not be fitted to this subject — nothing on the stage ' +
      'has usable bounds — so it is wherever the last effect left it.';
  host.appendChild(el('div', 'mut fx-sl-note',
    'The list is /api/fx/subject — depclose.subject_effects — not computed ' +
    'here. ' + fit));

  for (const e of st.entries) {
    const row = el('div', 'fx-sl-row');
    row.dataset.effect = e.name;
    const nm = el('span', 'fx-sl-name', e.name);
    row.appendChild(nm);

    const rep = (v && v.fxBuild && e.instIdx >= 0) ? v.fxBuild[e.instIdx] : null;
    const drawn = rep && (rep.layers || []).some(l => l.kept > 0);
    let cls, txt, tag, why;
    if (e.instIdx < 0) {
      tag = 'failed'; cls = 'fx-stage fx-stage-absent';
      txt = 'FAILED — the scene would not load';
      why = e.error || 'no scene';
    } else if (!drawn) {
      tag = 'empty'; cls = 'fx-stage fx-stage-empty';
      txt = 'EMPTY — built, and every layer came back with nothing';
      why = 'this effect contributes no pixels';
    } else {
      tag = 'drawn'; cls = 'fx-stage fx-stage-drawn';
      const kept = (rep.layers || []).filter(l => l.kept > 0).length;
      txt = 'drawn — ' + kept + ' of ' + (rep.layers || []).length + ' layer(s)';
      why = '';
    }
    const badge = el('span', cls, txt);
    badge.dataset.stage = tag;
    row.appendChild(badge);

    const box = el('input');
    box.type = 'checkbox';
    box.className = 'fx-sl-vis';
    box.checked = true;
    box.disabled = (tag !== 'drawn');
    box.title = tag === 'drawn'
      ? 'hide this effect on the stage (view only — nothing is written)'
      : 'FAILED: ' + why;
    const lab = el('label', 'fx-vis-l' + (tag === 'drawn' ? '' : ' fx-vis-dead'));
    lab.appendChild(box);
    lab.appendChild(el('span', null, tag === 'drawn' ? 'show' : 'FAILED'));
    row.appendChild(lab);

    if (tag === 'drawn') {
      box.addEventListener('change', () => {
        const ok = v.setEffectVisible(e.instIdx, box.checked);
        // If the stage refused, the box must not keep claiming a state the
        // picture does not have.
        if (!ok) { box.checked = true; box.disabled = true; }
      });
    }
    host.appendChild(row);
  }
}


function tick3d() {
  const v = S.viewer, def = S.def;
  if (!v || !def || S.kind !== '3d') return;
  const now = performance.now();
  if (S.playing) S.t = now - S.t0; else S.t0 = now - S.t;
  const total = stageTotal(def);
  const endless = stageEndless(def);
  // The clock is monotonic THROUGH one playthrough; the only wrap is the
  // scrubber's, and it exists because an endless effect has no end to scrub
  // to. A finite effect is left sitting on its last frame -- which is what
  // `fx.js` reports as `done` -- and is NOT restarted behind the user's back.
  if (endless && S.t > total) { v.resetEffects(); S.t = 0; S.t0 = now; }
  v.setEffectTime(S.t, null);
  v.draw();                    // explicit now: setEffectTime only ticks
  $('#fx-time').value = String(Math.round(Math.min(S.t, total)));
  $('#fx-clock').textContent =
    `${Math.round(S.t)} / ${Math.round(total)} ms` +
    (endless ? ' (endless)' : '');
  S.raf = requestAnimationFrame(tick3d);
}

/** Put the 3D clock at `ms` exactly. Ribbons and particles carry history, so
 *  a seek BACKWARDS has to rewind the instances rather than just move the
 *  clock -- otherwise a scrubbed-back ribbon keeps the smear it had at the
 *  later time and the picture is of a moment that never happened. */
function seek3d(ms) {
  const v = S.viewer;
  if (!v || !S.def) return;
  if (ms < S.t) v.resetEffects();
  S.t = ms;
  S.t0 = performance.now() - ms;
  v.setEffectTime(ms, null);
  v.draw();                    // explicit now: setEffectTime only ticks
}

/* --------------------------------------------------------------- the box
 *
 * bugs_open.md #5. A canvas has TWO sizes -- the CSS box the browser lays out
 * and the drawing buffer the pixels live in -- and nothing keeps them in step
 * on its own. `#fx-canvas` shipped a fixed 512x512 buffer and a `max-width:
 * 100%` CSS box, so every window narrower than the buffer scaled the picture
 * down and no window ever changed the buffer. The stage now tracks the
 * column, on load and on every resize.
 *
 * The 2D buffer is deliberately ONE device pixel per CSS pixel. `draw2d`
 * caps its scale at 1 so a frame is never upscaled past its authored size --
 * that cap is an honesty rule, not a performance one -- and a 2x buffer with
 * that cap in place would draw every effect at half the size it draws today.
 * The GL canvas is the opposite case and is left alone: `gl.js _resize()`
 * matches its buffer to its CSS box at device-pixel-ratio on every `draw`,
 * and has since it shipped.
 */

function stageBox() {
  const st = $('#fx-stage');
  const w = Math.max(160, (st.clientWidth || 512) - 2);
  const h = Math.max(240, Math.min(Math.round(w * 0.75),
                                   Math.round((window.innerHeight || 800) * 0.6)));
  return { w, h };
}

function layoutStage() {
  const { w, h } = stageBox();
  for (const sel of ['#fx-canvas', '#fx-gl']) {
    const c = $(sel);
    c.style.width = w + 'px';
    c.style.height = h + 'px';
  }
  const c = $('#fx-canvas');
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  if (S.kind === '2d') {
    if (S.def) draw2d(); else clearCanvas();
  } else if (S.viewer) {
    S.viewer.draw();          // gl.js `_resize` runs inside `draw`
  }
}

function onResize() {
  if (S.resizeRaf) return;    // coalesce a drag into one relayout per frame
  S.resizeRaf = requestAnimationFrame(() => {
    S.resizeRaf = 0;
    layoutStage();
  });
}

/* ---------------------------------------------------------------- the loop */

/** Start whichever kind is selected, from the top.
 *
 *  BOTH KINDS AUTOPLAY (bugs_open.md #4) and neither invents a loop: 2D asks
 *  `frameAt`, 3D asks `fx.js`, and both of those read `LoopTime`,
 *  `LoopInterval` and `Delay` off the effect's own row. `S.playing` starts
 *  true and no click is needed to see motion. */
function restart() {
  S.t = 0;
  S.last = performance.now();
  S.t0 = S.last;
  // SELECTION RESUMES PLAY. Pause is a statement about the effect you were
  // watching, not a mode the page stays in: carrying it into the next
  // selection is how "I paused this one" becomes "the viewer is dead", and on
  // any effect whose alpha envelope opens at 0.0 -- which is the common case
  // -- a paused stage is indistinguishable from a broken one.
  S.playing = true;
  $('#fx-play').textContent = '▶';
  if (S.raf) { cancelAnimationFrame(S.raf); S.raf = null; }
  if (!S.def) { clearCanvas(); return; }
  if (S.kind === '2d') {
    $('#fx-time').max = String(Math.round(totalMs(S.def)));
    tick();
  } else {
    play3d(S.def);
  }
}

function tick() {
  const now = performance.now();
  const dt = now - S.last;
  S.last = now;
  if (S.playing && S.def) {
    const total = totalMs(S.def);
    S.t += dt;
    if (S.t > total) S.t = S.def.endless ? S.t % total : total;
  }
  if (S.def) {
    draw2d();
    $('#fx-time').value = String(Math.round(S.t));
    $('#fx-clock').textContent =
      `${Math.round(S.t)} / ${Math.round(totalMs(S.def))} ms` +
      (S.def.endless ? ' (endless)' : '');
  }
  S.raf = requestAnimationFrame(tick);
}

/* ---------------------------------------------------------------- panels */

function kvPane(sel, pairs) {
  const b = $(sel);
  b.innerHTML = '';
  const kv = el('div', 'kv');
  for (const [k, v] of pairs) {
    kv.appendChild(el('b', '', k));
    kv.appendChild(el('span', '', v == null ? '—' : String(v)));
  }
  b.appendChild(kv);
  return b;
}

function renderPanels() {
  const d = S.def;
  if (!d) return;
  if (S.kind === '2d') {
    const b = kvPane('#fx-declared', [
      ['name', d.name],
      ['AniTitle', d.aniTitle],
      ['FrameInterval', `${d.frameIntervalMs} ms (${d.fps} fps)`],
      ['LoopTime', d.endless ? `${d.loopTime} (endless)` : d.loopTime],
      ['LoopInterval', `${d.loopIntervalMs} ms`],
      ['Delay', `${d.delayMs} ms`],
      ['OffsetX/Y', d.offset.join(', ') + '  (reported, not applied)'],
      ['ShowWay', `${d.showWay}  (undecoded)`],
      ['Exigence', `${d.exigence}  (undecoded)`],
      ['one loop', `${d.loopMs} ms`],
      ['total', d.durationMs == null ? 'endless' : `${d.durationMs} ms`],
      ['blend', 'NOT DECLARED — see below'],
      ['z-buffer', 'NOT DECLARED — see below'],
      ['from', `${d.source} + ${d.aniSource}`],
    ]);
    if (d.error) b.appendChild(el('div', 'fx-note', d.error));
    if (Object.keys(d.unknownKeys || {}).length) {
      b.appendChild(el('div', 'fx-note',
        'This row carries a key this reader has never seen: ' +
        JSON.stringify(d.unknownKeys) + '. It is shown, not dropped.'));
    }
    renderFrames2d(d);
    $('#fx-camera-pane').style.display = 'none';
  } else {
    kvPane('#fx-declared', [
      ['name', d.name],
      ['FrameInterval', `${d.frameIntervalMs} ms (${d.fps} fps)`],
      ['frames', `${d.effectiveFrames} played of ${d.frames} declared`],
      ['duration', d.durationMs == null ? 'endless' : `${d.durationMs} ms`],
      ['LoopTime', d.endless ? `${d.loopTime} (endless)` : d.loopTime],
      ['layers', (d.layers || []).length],
      ['playable parts', d.playableParts],
      ['particle parts', d.particleParts],
      ['undecoded parts', d.undecodedParts],
      ['from', d.source],
    ]);
    renderLayers3d(d);
    $('#fx-camera-pane').style.display = '';
    renderCamera3d(d);
  }
  renderApprox();
}

function renderFrames2d(d) {
  const b = $('#fx-frames');
  b.innerHTML = '';
  if (!d.frames.length) {
    b.appendChild(el('div', 'fx-note', 'no frames — ' + (d.error || '')));
    return;
  }
  const ol = document.createElement('ol');
  for (const f of d.frames) {
    const li = el('li', f.found ? '' : 'gone', f.path + (f.found ? '' : '  (MISSING)'));
    ol.appendChild(li);
  }
  b.appendChild(ol);
}

function renderLayers3d(d) {
  const b = $('#fx-frames');
  b.innerHTML = '';
  for (const lay of d.layers || []) {
    const box = el('div', 'fx-note');
    box.textContent =
      `layer ${lay.index}: ${lay.mesh || '(no mesh)'} · blend ` +
      `${lay.srcBlendName}/${lay.dstBlendName}` +
      (lay.additive ? ' (additive)' : '') +
      ` · zbuffer ${lay.zbuffer == null ? '—' : lay.zbuffer}` +
      ` · ${(lay.parts || []).length} part(s)` +
      (lay.error ? ` · ${lay.error}` : '');
    b.appendChild(box);
  }
  // The Model Viewer restores from `#model=<kind>:<ident>`, which is the row's
  // `key`, NOT its name. Fall back to composing it rather than sending a link
  // the other page will silently ignore.
  const row = S.rows.find(r => r.name === d.name);
  const key = (row && row.key) || ('effect:' + d.name);
  const go = el('button', 'ghost', 'Play in Model Viewer');
  go.onclick = () => { location.href = '/models#model=' + encodeURIComponent(key); };
  b.appendChild(go);
}

/** The Camera pane. It prints the CAME's declared numbers when there is one,
 *  and then -- ALWAYS -- what the stage ACTUALLY DID with them.
 *
 *  Those are different claims and the page used to make only the first. The
 *  server says "fitted" whenever the effect carries no CAME; that is a
 *  statement about the DATA and it stays true even when no fit ever ran. A
 *  reader looking at an empty viewport under the words "the view is FITTED to
 *  the geometry's bounding box" has been told the camera is fine by a
 *  sentence that never looked. So the numbers below come from `frame3d`'s
 *  return value, and a box that was rejected for being degenerate or NaN says
 *  so with its coordinates next to it. */
function renderCamera3d(d) {
  const c = d.camera;
  const b = $('#fx-camera');
  b.innerHTML = '';
  const kv = el('div', 'kv');
  const add = (k, v) => { kv.appendChild(el('b', '', k));
                          kv.appendChild(el('span', '', String(v))); };
  if (c) {
    add('CAME', c.name || '(unnamed)');
    add('eye', (c.eye0 || []).map(v => v.toFixed(1)).join(', '));
    add('target', (c.target0 || []).map(v => v.toFixed(1)).join(', '));
    add('fov', `${c.fovDegrees}°`);
    add('track', c.static ? 'still (13,935 of 13,935 are)'
                          : `${c.frameCount} frames, MOVES`);
    add('in layer', c.layer);
  }
  const f = S.framing;
  const num = v => (v == null ? '—' : Number(v).toFixed(1));
  const vec = a => (a || []).map(num).join(', ');
  if (f) {
    add('stage used', f.source === 'CAME' ? 'the CAME (the artist’s)'
        : f.source === 'fitted' ? 'a fit to the bounding box (OURS)'
        : 'NOTHING — the camera was not moved');
    const bb = f.bounds;
    if (bb) {
      add('bbox min', vec(bb.min));
      add('bbox max', vec(bb.max));
      add('bbox from', `${bb.parts} part(s) declaring one`);
      add('centre', vec(bb.center));
      add('radius', num(bb.radius) + ' (largest half-extent)');
    }
  }
  b.appendChild(kv);
  if (!c) b.appendChild(el('div', 'fx-note', d.cameraNote || 'no CAME'));
  else b.appendChild(el('div', 'fx-note', c.note || ''));
  if (f && f.source === 'none') {
    const bb = f.bounds;
    b.appendChild(el('div', 'fx-note',
      'THE VIEW WAS NOT AIMED AT THIS EFFECT. ' + (
        !bb ? 'No part of it declares a bounding box, so there was nothing ' +
              'to fit and no CAME to use instead. The picture is whatever ' +
              'the camera was already looking at.'
        : bb.nan ? 'The union of the part boxes contains a NaN, so a fit ' +
                   'would have pointed the camera nowhere and drawn an ' +
                   'empty frame with no error.'
        : 'The union of the part boxes has zero extent on every axis (' +
          vec(bb.min) + ' to ' + vec(bb.max) + '), which is a point, not ' +
          'a volume — there is no distance to place a camera at.')));
  }
  if (f && f.rejected) b.appendChild(el('div', 'fx-note', f.rejected));
  if (S.glError) b.appendChild(el('div', 'fx-note', S.glError));
}

/* ------------------------------------------------- the approximation rail */

/** Everything this preview does that the client does not, or does not do that
 *  the client does. Built from the SERVER's own notes wherever one exists, so
 *  a change to what the reader believes reaches this list without an edit
 *  here -- a hand-written copy is the thing that goes stale while still
 *  reading as authoritative. */
function renderApprox() {
  const ul = $('#fx-approx-list');
  ul.innerHTML = '';
  const add = (txt, cls) => ul.appendChild(el('li', cls || '', txt));
  const d = S.def;

  add('This is not the game. No client is running and none can be launched ' +
      'from here, so nothing on this page has been compared against one ' +
      'frame of real output.', 'always');

  if (!d) return;

  if (S.kind === '2d') {
    add(d.blendNote);
    add(d.undecodedNote);
    add('OffsetX/OffsetY are the screen anchor offset. There is nothing here ' +
        'to anchor to, so they are printed and NOT applied — the frame is ' +
        'centred instead.');
    add('The checkerboard and the frame scaling are ours. The client draws ' +
        'these at their authored pixel size over the world.');
    if (!d.timed) add(d.error);
    if (d.missingFrames) {
      add(`${d.missingFrames} of ${d.frameCount} frames are named by ` +
          `${d.aniSource} and absent from this install. They are held as ` +
          `blank gaps at their real positions, not skipped — skipping ` +
          `would shorten the effect.`);
    }
  } else {
    add('Blend and z-buffer here ARE declared (ASB/ADB/ZBuffer) and are ' +
        'honoured exactly — unlike the 2D table, which declares neither.');
    add(d.timingNote || '');
    if (d.particleParts) {
      add(`${d.particleParts} particle part(s). A PTCL/PTC3 system is BAKED ` +
          `in the file — one solved set per frame — so the positions ` +
          `are the file’s, not a simulation of ours. What IS ours is the ` +
          `billboard plane: the quads are rebuilt against the preview camera, ` +
          `and the client builds them against its own.`);
    }
    if (d.undecodedParts) {
      add(`${d.undecodedParts} part(s) in this effect did not decode and are ` +
          `NOT DRAWN. They are omitted, not approximated — the picture is ` +
          `missing something rather than showing something invented.`);
    }
    add(d.cameraNote || '');
    if (d.camera) {
      add(`The CAME’s field of view (${d.camera.fovDegrees}°) is NOT ` +
          `applied: this stage projects at a fixed 45°. The direction and ` +
          `distance are the artist’s; the lens is ours.`);
    }
  }
}

/* ------------------------------------------------------------------- wiring */

function setKind(k) {
  S.kind = k;
  S.name = '';
  S.def = null;
  S.framing = null;
  // Both loops schedule themselves into `S.raf`; leaving the outgoing one
  // running would have it draw the other kind's canvas from a stale `S.def`.
  if (S.raf) { cancelAnimationFrame(S.raf); S.raf = null; }
  if (S.viewer) S.viewer.resetEffects();
  // Exactly one canvas is present at a time. `hidden` and not `display:none`
  // by hand, so the rule lives in one place and a canvas with a zero-sized
  // CSS box -- which reads to gl.js as a 1x1 drawing buffer -- cannot happen.
  $('#fx-canvas').hidden = k !== '2d';
  $('#fx-gl').hidden = k !== '3d';
  $('#fx-kind-2d').setAttribute('aria-pressed', String(k === '2d'));
  $('#fx-kind-3d').setAttribute('aria-pressed', String(k === '3d'));
  $('#fx-kind-note').textContent = k === '2d'
    ? 'ini/effect.ini → ani/effect.ani. Whole images, shown one after ' +
      'another. Played on this page.'
    : 'ini/3DEffect.ini → .c3 scenes. Geometry, MOTI, particles, CAME. ' +
      'Played here on the Model Viewer’s own stage.';
  renderFormRow();
  if (k === '3d' && S.forms === null) loadForms();
  layoutStage();
  clearCanvas();
  $('#fx-msg').textContent = 'Pick an effect on the left.';
  $('#fx-declared').textContent = '';
  $('#fx-frames').textContent = '';
  $('#fx-camera').textContent = '';
  renderApprox();
  // The state filter classifies 3DEffect rows and nothing else, so it goes
  // dead on the 2D table rather than filtering a population it never measured.
  // No argument: the options stand, only the enabled state is re-decided.
  syncStateFilter();
  const V = window.CoFxView;
  if (V && V.setKind) V.setKind(k);
  loadList();
}

function fromHash() {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  const k = h.get('kind');
  if (k === '2d' || k === '3d') S.kind = k;
  return h.get('name') || '';
}

function boot() {
  $('#fx-kind-2d').onclick = () => setKind('2d');
  $('#fx-kind-3d').onclick = () => setKind('3d');
  $('#fx-reset').onclick = resetFilters;
  $('#fx-search').oninput = refilter;
  $('#fx-state').onchange = refilter;
  $('#fx-tagged-only').onchange = refilter;
  // DEVELOPER NOTES. `hidden` is the mechanism and `aria-expanded` is the
  // announcement; they are set together so a screen reader and a sighted
  // reader are never told different things about the same panel.
  $('#btn-devnotes').onclick = () => {
    const band = $('#fx-devnotes');
    band.hidden = !band.hidden;
    $('#btn-devnotes').setAttribute('aria-expanded', String(!band.hidden));
  };
  $('#fx-form-phy').onclick = () => toggleForm(FORM_PHY);
  $('#fx-form-shap').onclick = () => toggleForm(FORM_SHAP);
  $('#fx-form-ptcl').onclick = () => toggleForm(FORM_PTCL);
  $('#fx-play').onclick = () => {
    S.playing = !S.playing;
    $('#fx-play').textContent = S.playing ? '▶' : '‖';
  };
  $('#fx-rewind').onclick = () => {
    if (S.kind === '3d') seek3d(0); else S.t = 0;
  };
  $('#fx-time').oninput = e => {
    S.playing = false;
    $('#fx-play').textContent = '‖';
    const ms = Number(e.target.value) || 0;
    if (S.kind === '3d') seek3d(ms); else S.t = ms;
  };
  // 2D-only switches: neither is declared by the 2D table, and neither has
  // anything to toggle on the 3D path, where ASB/ADB ARE declared and are
  // honoured by gl.js. Calling `draw2d` from here in 3D mode would paint a
  // hidden canvas from a 3D payload.
  $('#fx-checker').onchange = () => {
    if (S.kind === '2d' && !S.playing) draw2d();
  };
  $('#fx-additive').onchange = () => {
    if (S.kind === '2d' && !S.playing) draw2d();
  };
  $('#btn-share').onclick = () => {
    navigator.clipboard && navigator.clipboard.writeText(location.href);
    status('link copied');
  };
  // THE COLLAPSIBLE RIGHT-HAND PANELS, the same `cards.js` the builder, the
  // model viewer, the map editor and the asset browser all init. The owner:
  // "the right pane doesn't scroll or allow for collapsing panels like every
  // other page." Guarded rather than assumed: this file must still boot with
  // one half of the page working if a script above it fails to load, which is
  // the rule the whole effects.js/fxview.js seam is built on. A missing
  // cards.js therefore costs the collapse and nothing else -- but it is
  // REPORTED, because a page that silently drops a control the owner asked
  // for by name is the failure this repo keeps paying for.
  if (window.CardPanels) {
    const r = window.CardPanels.init('coeffects.collapsed');
    if (r && r.unwired.length) {
      console.warn('effects.js: right-column card(s) with no .cardbody:',
                   r.unwired.join(', '));
    }
  } else {
    console.warn('effects.js: cards.js did not load — the right-hand panels ' +
                 'will not collapse. /ui/cards.js is missing or 404ed.');
  }
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === ' ') { e.preventDefault(); $('#fx-play').click(); }
    // `C` collapses/expands every right-hand panel, exactly as on the builder
    // and the map editor. Same key, same effect, on every page that has them.
    if ((e.key === 'c' || e.key === 'C') && window.CardPanels) {
      window.CardPanels.toggleAll();
    }
  });
  // bugs_open.md #5: the page installs a resize handler. The GL canvas also
  // has gl.js's own, which has always been there; this one is what keeps the
  // 2D drawing buffer and both CSS boxes in step with the column.
  window.addEventListener('resize', onResize);
  const want = fromHash();
  setKind(S.kind);
  layoutStage();
  if (want) setTimeout(() => select(want), 0);
  status('ready');
}

/* THE BOOTSTRAP RUNS AT THE BOTTOM OF THIS FILE, NOT HERE, so that a bootstrap
 * failure cannot take this file's published surface with it.
 *
 * `boot()` was called HERE, 36 lines above the `window.CoEffects = {...}`
 * assignment that publishes that surface. On the `else` branch of the
 * readyState check below, the call is SYNCHRONOUS -- so anything `boot()` throws
 * propagates out of the enclosing IIFE, the assignment is never reached, and
 * `window.CoEffects` is `undefined` FOR THE LIFE OF THE PAGE. Every gate and
 * every panel that asks this page a question then waits out its budget for a
 * global that is never coming, and no budget can be large enough.
 *
 * DEMONSTRATED, 2026-09-30, by breaking `boot()` on purpose (removing an element
 * it dereferences) in a real browser: with the publish first, the thrown
 * bootstrap is recorded as
 *   TypeError: Cannot set properties of null (setting 'onclick')
 *       at boot (.../ui/effects.js) at HTMLDocument.bootGuarded (...)
 * and `typeof window.CoEffects` is still `'object'`. The surface survives the
 * wiring, which is the whole point of the ordering.
 *
 * WHAT IS *NOT* KNOWN, stated here because an earlier version of this comment
 * claimed it and was wrong: WHICH loads take the synchronous branch. The first
 * account said a loaded box takes it because the page's eleven scripts arrive
 * slowly. That is RETRACTED -- `effects.html` carries no `defer` and no `async`
 * on any of its script tags, so this file executes during parsing, while
 * `readyState` is still `'loading'`, on every box; the `DOMContentLoaded` branch
 * is the one normally taken. The ordering fix is defensive and correct
 * regardless, but do not read it as the diagnosis of any particular red.
 *
 * AND THE 2026-09-30 STALL THIS WAS FOUND CHASING TURNED OUT TO BE SOMETHING
 * ELSE. Its remaining red was measured as EIGHT `<script>` subresources failing
 * to load -- `nav.js`, `swap.js`, `gl.js`, `fx.js`, `cards.js`, `effects.js` and
 * others firing resource-load errors -- so this file was never DELIVERED and no
 * ordering inside it could have mattered. Chrome does not retry a failed
 * subresource, which is why such a page completes and stays broken. That defect
 * is OPEN.
 */

/** THE PUBLISHED SURFACE. Two audiences and they are named apart.
 *
 *  FOR THE GATE: `frameAt`/`wallCounter`/`totalMs` are driven against the Python
 *  reference,
 *  and `total3d`/`sceneBounds`/`sceneCamera` are the three the 3D stage's
 *  correctness turns on -- the loop span the effect declares, and the two
 *  payload reads that were wrong in `fxview.js` for months without one error
 *  anywhere. `state` carries `framing`, which is what the stage DID as opposed
 *  to what the server said it would do.
 *
 *  FOR `fxview.js`: `censusUpdate` is the one route a classification takes
 *  into this file, `setStateFilter` is what the census panel's "show these"
 *  buttons call, and `selectByName` is how a name reached from somewhere
 *  other than the list -- a rule row that names an effect nothing defines, an
 *  effect link inside a connection expansion -- drives the whole page. It
 *  switches to 3D first, because those names are all `3DEffect` names, and it
 *  does not go through `fxview.js` again: `select` calls back into the
 *  library half itself, so routing it that way would be a loop.
 */
/** BACKLOG 24 -- the panel asks, this file acts, because this file owns the
 *  viewer. `fxview.js` never touches `S.viewer`: the seam between the two is
 *  two objects and nothing else, and a panel reaching into the stage's
 *  private handle is how that seam stops being a seam.
 *
 *  Returns whether the stage actually did it. `false` means the layer was
 *  never built and there is nothing to hide -- the caller must then show
 *  FAILED rather than a switch that shrugs. */
function setLayerVisible(layerIndex, on) {
  const v = S.viewer;
  if (!v || !v.setLayerVisible || S.kind !== '3d') return false;
  return v.setLayerVisible(0, layerIndex, on);
}

window.CoEffects = { frameAt, wallCounter, totalMs, total3d, sceneBounds, sceneCamera,
                     setLayerVisible, playSubject, renderStageList,
                     setEffectVisible: (i, on) => {
                       const v = S.viewer;
                       return !!(v && v.setEffectVisible &&
                                 v.setEffectVisible(i, on));
                     },
                     state: S, passesForm, toggleForm,
                     passesState, censusUpdate, setStateFilter,
                     selectByName, redrawList: drawList };

function selectByName(name) {
  if (S.kind !== '3d') setKind('3d');
  select(name);
}

/* NOW the bootstrap, with the surface above it already published. See the long
 * note where this used to live.
 *
 * GUARDED, AND IT SAYS SO OUT LOUD. A bootstrap that failed silently is how
 * this went unexplained: the page looked loaded, the picker was simply empty,
 * and nothing anywhere named a cause. `#statusline` is the one place a reader
 * is already looking, and `CoEffectsBootError` gives a gate something to assert
 * on -- an empty picker and a failed boot are different faults and must not
 * report the same way.
 *
 * The error is re-thrown after being recorded: swallowing it would hide a real
 * defect from the console, and the surface is already published by this point,
 * so a throw here no longer costs anything but the wiring.
 */
function bootGuarded() {
  try {
    boot();
  } catch (e) {
    window.CoEffectsBootError = String((e && e.stack) || (e && e.message) || e);
    try {
      const s = document.querySelector('#statusline');
      if (s) s.textContent = 'this page failed to start: '
                           + ((e && e.message) || e);
    } catch (ignored) { /* reporting must not replace the fault */ }
    console.error('effects.js: boot() threw, so the page is not wired', e);
    throw e;
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootGuarded);
} else {
  bootGuarded();
}

})();
