/* fxview.js -- the EFFECTS VIEWER's LIBRARY HALF, backlog item 15.
 *
 * IT IS NO LONGER A PAGE OF ITS OWN (2026-09-08)
 *
 *   `/effects` and `/fxview` were one subject seen twice -- one played an
 *   effect, one catalogued it -- and the owner asked for one page called the
 *   Effects Viewer, keeping the player's three-column shell because it is the
 *   toolkit's standard shape. So this file is loaded BY `effects.html` and
 *   `/fxview` forwards there. What it owns did not change; what it owns is
 *   now a smaller set, and the three things it LOST are the point:
 *
 *     the effect LIST and its filters   -> effects.js. There was one list on
 *                                          each page and two would have been
 *                                          two answers to "what is on this
 *                                          install". The whole/partial/broken
 *                                          dropdown the owner asked for is
 *                                          the census, handed over through
 *                                          `CoEffects.censusUpdate`.
 *     the STAGE                         -> effects.js. This file built a
 *                                          SECOND `Viewer` on a second canvas,
 *                                          with its own clock and its own
 *                                          camera code -- and that camera code
 *                                          read three keys and a method that
 *                                          have never existed (see the block
 *                                          that used to sit above
 *                                          `frameEffect`, kept in git and in
 *                                          `tests/test_effect_preview.py`).
 *                                          One stage, and it is the repaired
 *                                          one.
 *     the CENSUS BAND's position        -> behind the Developer Notes toggle.
 *                                          A change of PLACE, not of status:
 *                                          it is still built on load, still
 *                                          drawn in full, and still refuses to
 *                                          print a number under any state but
 *                                          `ready`.
 *
 *   THE SEAM IS TWO OBJECTS AND NOTHING ELSE. `window.CoEffects` is the list
 *   and the stage; `window.CoFxView` (published at the bottom of this file) is
 *   this half. Every call across it is guarded, so a page whose other script
 *   failed to load still boots with one half working and says which.
 *
 * WHAT THIS PANEL SET IS, AND WHAT IT DELIBERATELY IS NOT
 *
 *   Backlog section 6 established that the Model Viewer, the Character Builder
 *   and Asset Root are ONE shape -- a SUBJECT plus its SATELLITES -- served by
 *   one resolver (`tools/assetroot.py`) with different presentations. This page
 *   is the FOURTH presentation, with the subject being an EFFECT. It is NOT a
 *   fourth subsystem, and the code here reflects that: every panel below is a
 *   rendering of something that already existed.
 *
 *     play            gl.js + fx.js, the Model Viewer's stage, unmodified
 *     tag / rename    /api/tags -- core/tags.py, a dual-keyed OVERLAY
 *     collect         /api/collectoffer -- core/collection.py + the B+E schema
 *     export/import   /api/portage/* -- core/portage.py + the pop-out
 *     what it needs   /api/fx/effect -- assetroot.resolve_effect (FORWARD)
 *     connected to    the same call's `bindings` (REVERSE)
 *     walking it      /api/fx/subject -- depclose.subject_effects (SIDEWAYS)
 *                     + /api/weaponmotion, /models#mesh= (the Model Viewer)
 *
 *   The one genuinely new thing behind this page is server-side:
 *   `assetroot.resolve_effect`, which teaches the resolver that an effect is a
 *   first-class subject. Nothing here re-implements a walk.
 *
 * FIVE RULES THIS FILE MUST NOT BREAK, AND ALL FIVE ARE ABOUT HONESTY
 *
 *   1. THE FILE THAT ANSWERED IS NAMED. Where a compiled `.dbc` twin exists
 *      the client reads the TWIN and the `.ini` beside it is a DECOY -- the
 *      same id resolves DIFFERENTLY out of the two on one base. `renderTables`
 *      prints the file that actually answered and the sibling that was present
 *      and not read. If the server could not determine it, the strip says
 *      UNKNOWN; it never falls back to naming the `.ini`, because a plausible
 *      fallback is exactly how this class of bug stays invisible.
 *
 *   2. NOTHING UNRESOLVED IS DROPPED FOR BEING UNTIDY. A list showing 6
 *      companions and silently omitting 3 is worse than one showing 6 and
 *      saying "and 3 I could not resolve" -- the second is usable, the first is
 *      a trap the user cannot detect. So `limits` is inherited verbatim from
 *      the server and rendered ALWAYS, including when it is empty, and the
 *      unresolved count is printed beside every list it applies to.
 *
 *   3. AN EFFECT IS NOT A FILE, AND THE BUNDLE SAYS WHAT IT DOES ABOUT THAT.
 *      The DEFINITION is a row of `3DEffect` -- `3DEffect.dbc` on
 *      5517/6090/6609/7205, and the `.ini` beside it is rule 1's decoy. A
 *      bundle of the layer meshes and textures alone lands the ART on the far
 *      install with the effect UNDEFINED, and a tidy list of exported files is
 *      exactly what stops anyone noticing. So `renderDefinition` prints the
 *      answer in all three of its states -- carried, not carried with the
 *      server's reason, or UNKNOWN because the check failed -- and never lets
 *      a blank space stand for "no". MEASURED, which is why the answer here is
 *      "carried" rather than a disclaimer: `dbc.serialize_effe(read_effe(t))
 *      == t` on every base that ships a compiled table (5517 3,391 rows, 6090
 *      4,483, 6609 and 7205 5,313), so the row round-trips and import merges
 *      it into the TARGET's own table rather than shipping the table over it.
 *   4. A LINK MAY NOT LAUNDER A CAVEAT. Rule 2 is easy to keep in a flat
 *      list and easy to lose the moment the list becomes navigable: a page
 *      that qualifies its first screen and then opens a clean-looking second
 *      one has told the user the qualification did not travel. So every
 *      panel a link opens renders the `limits` the server returned FOR THAT
 *      SUBJECT (`renderSubjectLimits`), the "every appearance of this type"
 *      view keeps the ids weapon.ini does not declare in a list of their own
 *      rather than merging or dropping them, and a subject that cannot be
 *      resolved says UNMEASURED instead of showing nothing.
 *
 *      The same rule governs what is NOT linked. The weapon/action split is
 *      DERIVED -- `Action3DEffect.ini` carries both in one table keyed
 *      identically and no row says which it is -- so navigation is driven by
 *      the row's structured key and never by parsing its prose, and a row
 *      that names no subject (a map row keys on shape+action+terrain) says
 *      what it keys on instead of being given a link to somewhere it does
 *      not point.
 *   5. AN UNMEASURED CENSUS IS NEVER RENDERED AS A FINISHED ONE. Classifying
 *      every effect on a base means RESOLVING every effect: 10.1 s on 5517
 *      and 33.7 s on 6609, measured. So the census is a background build and
 *      `renderCensus` refuses to draw a single number under any state but
 *      "ready" -- because "0 broken effects" and "I have not looked yet" are
 *      the same numbers, and only one of them is a fact about the client. The
 *      state filter is disabled for the same reason the form filter is: a
 *      filter over a classification that does not exist yet silently hides
 *      every unclassified effect and presents the remainder as an answer.
 */
'use strict';

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const S = {
  names: [],          // every effect name on this install
  states: {},         // name -> whole|partial|broken, from /api/fx/census
  censusState: '',    // the census build's state
  census: null,       // the census totals, ONLY when censusState === 'ready'
  tags: {},           // subject -> [tag]
  renames: {},        // subject -> the user's own name
  sel: '',            // the selected effect name
  detail: null,       // the last /api/fx/effect payload
  tablesKnown: false,
};

/* The tag subject key for an effect. `core/tags.py` is dual-keyed and every
 * other subject on this server is namespaced, so effects get their own prefix
 * rather than sharing the bare-name space with assets -- an effect called
 * `Blood` and a file called `Blood` are not the same subject. */
const subjectFor = (name) => 'fx:' + name;

async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = 'HTTP ' + r.status;
  return j;
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = 'HTTP ' + r.status;
  return j;
}

function status(msg) { $('#statusline').textContent = msg; }

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/** DRIVE THE WHOLE PAGE TO AN EFFECT, from anywhere in this file.
 *
 *  Every route into an effect that does NOT come from the list comes through
 *  here: a rule row naming something `3DEffect` does not define, an effect
 *  link inside a connection expansion, a name reached from a subject walk.
 *  Those need the STAGE and the DECLARED panel as much as they need the
 *  subject cards, and `effects.js` owns both -- so this asks it to select,
 *  and it calls `select()` below on its way through. Calling `select()`
 *  directly here would render the cards beside a stage still playing the
 *  previous effect, which is the most convincing wrong screen this page could
 *  produce: everything on it is real and half of it is about something else.
 *
 *  With `effects.js` absent this still renders the subject, and the stage is
 *  simply not there to move. */
function goto_(name) {
  const E = window.CoEffects;
  if (E && E.selectByName) E.selectByName(name);
  else select(name);
}

/* ------------------------------------------------------- the provenance strip */

/** THE TWIN TRAP, ON SCREEN.
 *
 *  One row per effect table naming the file that ACTUALLY answered. Where the
 *  server reports a `not_read` sibling, that sibling is printed as a DECOY in
 *  its own right -- not as a footnote -- because "3DEffect.dbc answered" and
 *  "3DEffect.ini is sitting right there and is NOT what you are looking at"
 *  are two different things a person editing tables needs to know.
 *
 *  An empty `tables` is NOT rendered as "no twins anywhere". That reading is a
 *  claim, and a false one on 5517/6090/6609/7205. It is rendered as UNKNOWN.
 */
function renderTables(j) {
  const host = $('#prov-rows');
  host.innerHTML = '';
  const rows = j.tables || [];
  if (!rows.length) {
    S.tablesKnown = false;
    const w = el('div', 'fx-bad',
      'UNKNOWN — the server could not read the effect-table provenance on ' +
      'this install. Which file answered for each table is NOT KNOWN here; ' +
      'do not read this as "there is no compiled twin".');
    host.appendChild(w);
    return;
  }
  S.tablesKnown = true;
  const t = el('table', 'fx-provtable');
  const hd = el('tr');
  for (const h of ['table', 'file that answered', 'form', 'rows',
                   'present and NOT read']) {
    hd.appendChild(el('th', null, h));
  }
  t.appendChild(hd);
  for (const r of rows) {
    const tr = el('tr');
    tr.appendChild(el('td', 'fx-tname', r.table));
    tr.appendChild(el('td', r.form === 'missing' ? 'fx-bad' : 'fx-file',
                      r.file || '(ABSENT — read as an EMPTY table)'));
    tr.appendChild(el('td', null, r.form));
    tr.appendChild(el('td', 'fx-num', String(r.rows)));
    const d = el('td');
    if (r.not_read) {
      d.className = 'fx-decoy';
      d.textContent = r.not_read + '  ← DECOY';
      d.title = 'This file is present and the client does NOT read it. An id ' +
                'resolved out of it is a DIFFERENT ANSWER from the one the ' +
                'client gets.';
    } else {
      d.className = 'mut';
      d.textContent = '—';
    }
    tr.appendChild(d);
    t.appendChild(tr);
    if (r.note) {
      const nr = el('tr', 'fx-noterow');
      const td = el('td', 'mut', '↳ ' + r.note);
      td.colSpan = 5;
      nr.appendChild(td);
      t.appendChild(nr);
    }
  }
  host.appendChild(t);

  /* RULES THAT NAME AN EFFECT NOTHING DEFINES -- a FOURTH state, and the
   * one the list cannot reach on its own.
   *
   * The list is `EffectPlayer.names()`, the DEFINED effects, so these names
   * are not in it and no amount of filtering finds them. The viewer resolves
   * them correctly if asked and, without this, offered no way to ask -- a
   * finding the tool holds and cannot show. So they are named here, next to
   * the tables they came out of, and each is clickable straight into the
   * subject panel.
   *
   * It is a BROKEN RULE, not broken art: the client has nothing to play for
   * those rows. Saying "broken effect" would send someone hunting for a
   * missing mesh that was never referenced. */
  const undef = j.undefinedReferenced;
  if (undef && undef.length) {
    const box = el('div', 'fx-unread fx-dangling');
    box.appendChild(el('b', null,
      undef.length + ' rule row target(s) that 3DEffect does NOT define:'));
    const row = el('div', 'fx-danglist');
    for (const n of undef) {
      const b = el('button', 'fx-danglink', n);
      b.title = 'a rule table names this effect and no definition exists; ' +
                'open it to see which rows point at it';
      b.addEventListener('click', () => goto_(n));
      row.appendChild(b);
    }
    box.appendChild(row);
    box.appendChild(el('div', 'mut',
      'These are BROKEN RULES, not broken art — the client has nothing to ' +
      'play for those rows. They are NOT in the list on the left, because ' +
      'that list is the DEFINED effects; this is the only route to them.'));
    host.appendChild(box);
  }

  /* The six sibling rule tables EffectDB does not read. These are a MEASURED
   * hole in the connection view below, so they are printed next to the tables
   * that ARE read, where the comparison is visible -- not buried in a doc. */
  const un = j.unreadTables || [];
  if (un.length) {
    const box = el('div', 'fx-unread');
    box.appendChild(el('b', null,
      'Shipped on this install and NOT read (' + un.length + ' file(s)):'));
    const ul = el('ul');
    let rows2 = 0;
    for (const u of un) {
      rows2 += (u.rows || 0);
      ul.appendChild(el('li', null,
        'ini/' + u.file + '  — ' + (u.rows || 0) + ' key(s)' +
        (u.family ? '  [' + u.family + ']' : '')));
    }
    box.appendChild(ul);
    box.appendChild(el('div', 'mut',
      'No rule in these ' + rows2 + ' keys appears anywhere in the ' +
      '"connected to" panel. The connection list below is therefore ' +
      'incomplete BY THIS MEASURED AMOUNT.'));
    host.appendChild(box);
  }
}

/* -------------------------------------------- the table read, not the list
 *
 * `/api/fx/list` is still called here and it still is not a list any more:
 * `effects.js` draws the picker. What this half needs out of the same
 * response is the PROVENANCE (which file answered, per table), the standing
 * `limits`, the write status, and `names` -- which is used to mark an effect
 * link that names something `3DEffect` does not define, BEFORE the click
 * rather than after it.
 *
 * The endpoint is called once by each half rather than shared through a
 * global, because they want different fields off it and a shared cache would
 * make the order the two scripts happen to run in load-bearing. The server
 * builds the dependency graph once and answers the second call from it.
 */

async function loadList() {
  status('reading the effect tables…');
  const j = await getJSON('/api/fx/list');
  renderTables(j);
  if (!j.available) {
    status(j.error || 'this install has no 3D effect table');
    renderLimits(j.limits || [], 0, null);
    /* The census panel must say the same thing rather than sitting at its
     * placeholder: a panel frozen on "not started…" is indistinguishable
     * from one that is about to fill in. */
    S.censusState = 'failed';
    S.censusError = j.error || 'this install has no 3D effect table';
    renderCensus();
    publishCensus();
    return;
  }
  S.names = j.names || [];
  S.listLimits = j.limits || [];
  S.writeStatus = j.writeStatus || null;
  loadTags();
}

/* ---------------------------------------------------------------- the census */

/** THE STATE LABELS, and the fourth one is not a state of a listed effect.
 *
 *  `whole` / `partial` / `broken` classify a name `3DEffect` DEFINES.
 *  `undefined` is a name a RULE row points at that `3DEffect` does not define
 *  -- so it is not in the list, cannot be a row, and cannot be filtered for.
 *  It is a DIFFERENT POPULATION and it is rendered as its own line, never
 *  added into `broken`: a broken effect is bad ART (the definition is there,
 *  the files are not) and an undefined reference is a bad RULE (the rule
 *  points at a definition nobody wrote). They live in different files and are
 *  fixed by different people, and folding them together destroys the only
 *  distinction that tells you which one you have.
 */
const STATE_LABEL = {
  whole: 'WHOLE',
  partial: 'PARTIALLY RESOLVED',
  broken: 'BROKEN',
};
const STATE_BLURB = {
  whole: 'every layer resolves to a mesh file AND a texture file this ' +
         'install actually ships',
  partial: 'some layers resolve and some do not — the effect exists and ' +
           'will play wrong',
  broken: 'no layer resolves, INCLUDING a definition that declares no ' +
          'layers at all',
};

/** The census is a background build on the server, 10.1 s on 5517 and 33.7 s
 *  on 6609 (measured). It NEVER blocks the list: `states` and `census` are
 *  empty under every state but "ready", and an empty answer under any other
 *  state is UNMEASURED — it must never be drawn as "this client ships no
 *  broken effects". Same contract `/api/effect/forms` publishes. */
async function loadCensus() {
  const j = await getJSON('/api/fx/census');
  S.censusState = j.state || '';
  S.censusProgress = j.progress || null;
  S.censusElapsed = j.elapsedSeconds;
  S.censusError = j.error || '';
  S.censusProv = j.provenance || null;
  if (S.censusState === 'ready') {
    S.states = j.states || {};
    S.census = j.census || {};
  } else {
    /* Deliberately NOT merged with whatever arrived: a partial map would let
     * the filter answer for some effects and silently drop the rest. */
    S.states = {};
    S.census = null;
  }
  S.allStates = j.allStates || S.allStates || [];
  renderCensus();
  publishCensus();
  if (S.censusState !== 'ready' && S.censusState !== 'failed') {
    setTimeout(loadCensus, 2000);
  }
}

/** HAND THE CLASSIFICATION TO THE LIST. This is the whole of the owner's
 *  "add the Effects Viewer's dropdown filter for whole, partially resolved
 *  and broken": the buckets are measured here and the FILTER lives on the
 *  picker, in `effects.js`, because there is one list and it must not grow a
 *  second.
 *
 *  THE STATE MAP IS ONLY EVER SENT WHEN IT IS `ready`. `loadCensus` above
 *  clears `S.states` under every other state on purpose -- a partial map
 *  would let the filter answer for some effects and silently drop the rest --
 *  and this passes the state along with it so the receiving side can refuse
 *  for the same reason rather than trusting an empty object. */
function publishCensus() {
  const E = window.CoEffects;
  if (!E || !E.censusUpdate) return;
  const p = S.censusProgress || {};
  E.censusUpdate({
    state: S.censusState,
    states: S.states,
    allStates: S.allStates || [],
    note: S.censusState === 'failed'
      ? 'the census FAILED (' + (S.censusError || 'no reason given') +
        ') — nothing on this install is classified, so there is nothing to ' +
        'filter by'
      : S.censusState === 'ready' ? ''
      : 'the census is still building (' + (p.done || 0) + '/' +
        (p.total || '?') + ') — filtering by state would hide effects that ' +
        'are simply not classified yet',
  });
}

/** ALWAYS DRAWS SOMETHING, and what it draws under a non-ready state is a
 *  refusal, not a number. This is the whole point of the panel: a census that
 *  rendered its empty in-flight buckets would publish an unmeasured install
 *  as a clean one, and the reader could not tell. */
function renderCensus() {
  const host = $('#census-body');
  host.innerHTML = '';

  if (S.censusState !== 'ready') {
    const box = el('div', 'fx-bad');
    if (S.censusState === 'failed') {
      box.appendChild(el('div', null,
        'THE CENSUS FAILED — ' + (S.censusError || 'no reason was reported') +
        '. Nothing on this install is classified.'));
    } else {
      const p = S.censusProgress || {};
      box.appendChild(el('div', null,
        'UNMEASURED — the census is still building' +
        (p.total ? ' (' + (p.done || 0) + ' of ' + p.total + ' effects' +
                   (S.censusElapsed != null ? ', ' + S.censusElapsed + ' s' : '') +
                   ')'
                 : '') + '.'));
    }
    box.appendChild(el('div', 'mut',
      'There are NO NUMBERS on this panel yet, on purpose. An empty census ' +
      'and a census of zero broken effects are the same JSON, and only one ' +
      'of them is a fact about this client. Classifying an effect means ' +
      'resolving it: 10.1 s on 5517 and 33.7 s on 6609, measured. The list ' +
      'above is complete and usable meanwhile; only the state filter waits.'));
    host.appendChild(box);
    return;
  }

  const c = S.census || {};
  const by = c.by_state || {};
  const total = c.effects || 0;
  const grid = el('div', 'fx-censusgrid');
  for (const st of ['whole', 'partial', 'broken']) {
    const n = by[st] || 0;
    const cell = el('div', 'fx-censuscell fx-cs-' + st);
    cell.appendChild(el('div', 'fx-censusnum', String(n)));
    cell.appendChild(el('div', 'fx-censuslab', STATE_LABEL[st]));
    cell.appendChild(el('div', 'mut fx-censuspct',
      total ? (100 * n / total).toFixed(1) + '% of ' + total : '—'));
    cell.appendChild(el('div', 'mut fx-censusblurb', STATE_BLURB[st]));
    /* Clicking a bucket IS the filter. The census and the list are the same
     * measurement seen twice, and a number you cannot open is a number you
     * cannot check. */
    const b = el('button', 'ghost', 'show these');
    b.addEventListener('click', () => {
      const E = window.CoEffects;
      if (E && E.setStateFilter) E.setStateFilter(st);
      else status('the effect list is not on this page, so there is nothing ' +
                  'to filter — this count is still the census\'s');
    });
    cell.appendChild(b);
    grid.appendChild(cell);
  }
  host.appendChild(grid);

  /* THE FOURTH ROW. Its own block, visually apart from the three buckets,
   * because it counts a DIFFERENT POPULATION -- names that are not in the
   * list above and never can be. */
  const und = c.referenced_but_undefined || [];
  const four = el('div', 'fx-census4');
  four.appendChild(el('b', null,
    (c.referenced_but_undefined_count != null
      ? c.referenced_but_undefined_count : und.length) +
    ' name(s) REFERENCED BY A RULE AND NOT DEFINED'));
  four.appendChild(el('div', 'mut',
    'This is NOT a fourth bucket of the ' + total + ' above and it is NOT ' +
    'added into BROKEN. These names are not in 3DEffect at all, so they are ' +
    'not rows in the list and no filter can reach them. A BROKEN effect is ' +
    'bad ART — the definition is there and the files are not. An UNDEFINED ' +
    'reference is a bad RULE — a weapon/action/map row points at a ' +
    'definition that was never written. They are repaired in different ' +
    'files by different people.'));
  if (und.length) {
    const CAP = 40;
    const ul = el('ul', 'fx-undeflist');
    for (const n of und.slice(0, CAP)) ul.appendChild(el('li', null, n));
    four.appendChild(ul);
    if (und.length > CAP) {
      four.appendChild(el('div', 'mut',
        'showing the first ' + CAP + ' of ' + und.length +
        ' — the count above is the whole set'));
    }
  } else {
    four.appendChild(el('div', 'mut',
      'None on this base: every effect the three rule tables EffectDB reads ' +
      'name is defined. That is a measured zero, not an unread table — the ' +
      'census is ready. It says nothing about the SIX sibling rule tables ' +
      'EffectDB does not read, listed in the strip above.'));
  }
  host.appendChild(four);

  /* WHERE THE BREAK IS, per link. "N broken" is not actionable; "the id has
   * no row in the path table" and "the row names a file this install does
   * not ship" are two different repairs and only one is a table edit. */
  const lk = c.layer_links || {};
  const lay = el('div', 'fx-censuslinks');
  lay.appendChild(el('b', null,
    (c.layers || 0) + ' layer(s) across those ' + total + ' effects'));
  const lt = el('table', 'fx-linktable');
  const hd = el('tr');
  for (const h of ['link', 'names no id', 'NO ROW in the table',
                   'row names a file NOT SHIPPED', 'resolves']) {
    hd.appendChild(el('th', null, h));
  }
  lt.appendChild(hd);
  for (const [k, label] of [['mesh', 'mesh — 3DEffectObj'],
                            ['tex', 'texture — 3dtexture']]) {
    const tr = el('tr');
    tr.appendChild(el('td', 'fx-tname', label));
    for (const s of ['_no_id', '_no_row', '_absent', '_ok']) {
      tr.appendChild(el('td', 'fx-num', String(lk[k + s] || 0)));
    }
    lt.appendChild(tr);
  }
  lay.appendChild(lt);
  lay.appendChild(el('div', 'mut',
    (c.layers_naming_an_unresolvable_id || 0) + ' layer link(s) name an id ' +
    'the path table has NO ROW for. This is the same CLASS of hole as the ' +
    'standing "125 of 3,987 effect layers" figure and is NOT that number: ' +
    'that one counts a LAYER and this counts a LINK, so a layer whose mesh ' +
    'id and texture id are both unresolvable is 1 there and 2 here. Of the ' +
    'BROKEN effects, ' + (c.declaring_no_layers || 0) +
    ((c.declaring_no_layers === 1) ? ' declares' : ' declare') +
    ' no layers at all — a fact about the table, not a missing file, and a ' +
    'different repair from a row naming a file the install does not ship.'));
  host.appendChild(lay);

  /* WHAT IT COULD NOT CLASSIFY. Printed when it is zero, because a census
   * that only prints the buckets it filled reads as a complete one. */
  const unc = c.unclassified || 0;
  const uc = el('div', unc ? 'fx-bad' : 'mut');
  uc.textContent = unc
    ? unc + ' effect(s) COULD NOT BE CLASSIFIED and are in no bucket above: ' +
      (c.unclassified_names || []).join(', ')
    : '0 effects could not be classified — the three buckets account for ' +
      'all ' + total + ' names 3DEffect defines on this base.';
  host.appendChild(uc);
  if (c.duplicate_effect_names) {
    host.appendChild(el('div', 'fx-warn',
      c.duplicate_effect_names + ' DUPLICATE effect name(s) on this base — ' +
      'these counts are keyed by NAME, so they are name counts and not ' +
      'record counts, and the two differ here.'));
  }

  renderCensusProvenance(host);
}

/** THE TWIN TRAP, CHECKED RATHER THAN ASSUMED.
 *
 *  The census is computed by EffectPlayer's EffectDB; the strip at the top of
 *  this page is drawn from DepGraph's — two readers over one root. They
 *  should name the same files. But where a compiled `.dbc` twin exists the
 *  client reads the TWIN and the `.ini` beside it is a decoy, and the same id
 *  resolves DIFFERENTLY out of the two — so "should" is exactly the
 *  assumption this class of bug hides behind. The server compares them and
 *  this prints the verdict, including "not checked", which is its own answer
 *  and must not read as "checked and fine".
 */
function renderCensusProvenance(host) {
  const p = S.censusProv || {};
  const box = el('div', 'fx-censusprov');
  const mine = p.censusTables || {};
  const stems = Object.keys(mine).sort();
  box.appendChild(el('b', null, 'These numbers came out of:'));
  if (!stems.length) {
    box.appendChild(el('div', 'fx-bad',
      'UNKNOWN — the census did not report which files it read. Do not ' +
      'attribute these numbers to the files named in the strip above.'));
    host.appendChild(box);
    return;
  }
  const ul = el('ul', 'fx-provlist');
  for (const s of stems) {
    const r = mine[s] || {};
    ul.appendChild(el('li', null,
      s + ' ← ' + (r.file || '(ABSENT — read as an EMPTY table)') +
      '  [' + (r.form || '?') + ', ' + (r.rows != null ? r.rows : '?') +
      ' rows]'));
  }
  box.appendChild(ul);
  if (p.agree === true) {
    box.appendChild(el('div', 'fx-ok',
      'These are the SAME files the provenance strip at the top of this page ' +
      'names, with the same row counts — checked per table, not assumed.'));
  } else if (p.agree === false) {
    const bad = el('div', 'fx-bad');
    bad.appendChild(el('div', null,
      'THE TWO READERS DISAGREE. The census below describes a DIFFERENT ' +
      'table from the one the strip at the top names. Do not read these ' +
      'numbers as that table\'s.'));
    const dl = el('ul');
    for (const d of (p.disagree || [])) {
      dl.appendChild(el('li', null,
        d.table + ': census read ' + (d.census.file || '(absent)') + ' (' +
        d.census.rows + ' rows), the strip names ' +
        (d.strip.file || '(absent)') + ' (' + d.strip.rows + ' rows)'));
    }
    bad.appendChild(dl);
    box.appendChild(bad);
  } else {
    box.appendChild(el('div', 'fx-warn',
      'NOT CHECKED against the strip at the top of this page — one of the ' +
      'two readers did not report. That is not the same as "they agree".'));
  }
  host.appendChild(box);
}

async function loadTags() {
  const j = await getJSON('/api/tags');
  S.vocab = j.vocabulary || {};
}

/* -------------------------------------------------------------- the subject */

/** RESOLVE the selected effect and draw its panels.
 *
 *  CALLED BY `effects.js`, not by a click in this file. The list and the
 *  stage are that file's; it selects, plays, and then hands the name here, so
 *  the picture and the panels beside it are always the same effect. The one
 *  call this file makes in the other direction is `goto_`, which routes back
 *  through `CoEffects.selectByName` and arrives here again -- once.
 *
 *  IT NO LONGER FETCHES `/api/effect`. That payload is the playable geometry
 *  and `effects.js` already has it on the stage; a second fetch of it here
 *  would be a second copy of the scene, and the two would be a frame apart
 *  every time a user clicked twice quickly. */
async function select(name) {
  S.sel = name;
  $('#fx-empty').hidden = true;
  $('#fx-detail').hidden = false;
  $('#fx-name').textContent = name;
  resetCompare(name);
  $('#fx-status').textContent = 'resolving…';
  for (const id of ['#fx-layers', '#fx-conn', '#fx-collect', '#fx-limits']) {
    $(id).innerHTML = '<span class="mut">…</span>';
  }
  const d = await getJSON('/api/fx/effect?name=' + encodeURIComponent(name));
  if (S.sel !== name) return;             // the user moved on
  S.detail = d;
  renderSubject(d);
  renderTagPanel(name);
}

/** The 2D/3D switch, from `effects.js`.
 *
 *  THE LIBRARY HALF IS 3D ONLY AND SAYS SO RATHER THAN GOING BLANK. What it
 *  resolves is a row of `ini/3DEffect`; the 2D flipbook is `ini/effect.ini`,
 *  a different table with different names, and answering about it would
 *  produce a confident "the tables hold NO record under this name" -- a true
 *  sentence about the wrong file, which reads on screen as a broken effect.
 *  So the subject column states the boundary instead of showing an empty
 *  panel that looks like an answer. */
function setKind(kind) {
  S.sel = '';
  if (kind === '3d') {
    $('#fx-detail').hidden = true;
    $('#fx-empty').hidden = false;
    $('#fx-empty').textContent = 'Pick an effect from the list.';
    return;
  }
  $('#fx-detail').hidden = true;
  $('#fx-empty').hidden = false;
  $('#fx-empty').textContent =
    'The 2D flipbook table is ini/effect.ini and everything below resolves ' +
    'ini/3DEffect — a different table, with different names. Nothing here ' +
    'is measured for a 2D effect, so nothing is claimed about one. Switch to ' +
    '3D scene for tags, satellites, connections, compare and export.';
}

function renderSubject(d) {
  if (d.error) {
    $('#fx-status').innerHTML = '';
    $('#fx-status').appendChild(el('span', 'fx-bad', d.error));
    return;
  }
  const bits = [];
  bits.push(d.status);
  if (d.located) bits.push('from ' + d.located);
  bits.push(d.declaredLayers + ' declared layer(s)');
  if (d.duplicateName) {
    bits.push('DUPLICATE NAME — 3DEffect holds more than one row under it');
  }
  $('#fx-status').textContent = bits.join('  ·  ');

  if (!d.measured) {
    /* UNMEASURED and ZERO are different answers and they look identical if
     * you only render the lists. Say it before anything below is read. */
    const w = el('div', 'fx-bad',
      'The effect tables would not load on this install. EVERYTHING below is ' +
      'UNMEASURED, not zero.');
    $('#fx-layers').innerHTML = '';
    $('#fx-layers').appendChild(w);
    $('#fx-conn').innerHTML = '';
    $('#fx-conn').appendChild(w.cloneNode(true));
    /* THE COLLECT CARD TOO, AND THIS WAS A REAL HOLE. This early return meant
     * the card kept the "…" placeholder `select()` put there -- a card that
     * looks like it is still loading, forever. Harmless while it only listed
     * files; not harmless now that it is where the page answers "does a bundle
     * carry this effect's definition", because a spinner that never resolves is
     * read as "working on it", never as "I could not look". */
    const c = $('#fx-collect');
    c.innerHTML = '';
    c.appendChild(w.cloneNode(true));
    c.appendChild(el('div', 'mut',
      'No bundle can be planned from an install whose effect tables would not ' +
      'load, so whether one would carry this effect’s 3DEffect definition row ' +
      'is UNKNOWN here — not "it would not".'));
    renderLimits(d.limits || [], d.unresolvedCount || 0, d);
    return;
  }
  if (!d.present) {
    const box = el('div', 'fx-bad');
    box.appendChild(el('div', null,
      'The tables were read and hold NO record under this name.'));
    if ((d.near || []).length) {
      box.appendChild(el('div', 'mut',
        'Near matches: ' + d.near.join(', ')));
    } else {
      box.appendChild(el('div', 'mut', 'Nothing like it either.'));
    }
    $('#fx-layers').innerHTML = '';
    $('#fx-layers').appendChild(box);
  } else {
    renderLayers(d);
  }
  renderConnections(d);
  renderCollect(d);
  renderLimits(d.limits || [], d.unresolvedCount || 0, d);
  renderTiming(d);
}

function renderTiming(d) {
  const t = d.timing || {};
  $('#fx-timing').textContent = t.endless
    ? 'ENDLESS — loops until something stops it'
    : (t.durationMs != null ? Math.round(t.durationMs) + ' ms' : 'duration unknown') +
      '  ·  delay ' + t.delay + '  ·  ' + t.loopTime + ' loop(s)' +
      '  ·  ' + t.frameInterval + ' ms/frame';
}

/** WHAT IT NEEDS -- the three-link chain, one row per layer, with each link
 *  labelled by the TABLE that answered it. A break in each link is a different
 *  problem: no 3DEffect row is a missing definition, no 3DEffectObj row is a
 *  missing mesh binding, no 3dtexture row is a missing texture binding, and a
 *  bound path whose file is absent is a missing FILE. All four are shown
 *  apart, because "this effect is broken" is not an actionable answer. */
function renderLayers(d) {
  const host = $('#fx-layers');
  host.innerHTML = '';
  const layers = d.effects || [];
  const byIdx = {};
  for (const g of (d.geometry || [])) {
    const m = /^layer (\d+)/.exec(g.label);
    if (m) (byIdx[m[1]] = byIdx[m[1]] || {}).mesh = g;
  }
  for (const t of (d.textures || [])) {
    const m = /^layer (\d+)/.exec(t.label);
    if (m) (byIdx[m[1]] = byIdx[m[1]] || {}).tex = t;
  }
  const _sub = $('#fx-layers-sub');
  _sub.textContent =
    '— ' + layers.length + ' parsed of ' + d.declaredLayers + ' declared' +
    (layers.length !== d.declaredLayers ? '  ⚠ THEY DISAGREE' : '');
  // `#fx-layers-sub` is ONE reused node, and `applyStageBuild` stashes the
  // parsed half in `dataset.parsed` so it can rewrite the stage half without
  // re-deriving it. That stash must die with the render, or the next effect
  // gets the previous effect's parsed counts back. Same node, different
  // subject -- the shape that makes a per-node cache lie.
  delete _sub.dataset.parsed;

  if (!layers.length) {
    host.appendChild(el('div', 'mut',
      d.declaredLayers
        ? 'The definition declares ' + d.declaredLayers + ' layer(s) and NONE ' +
          'parsed — see the honesty rail below.'
        : 'This effect record EXISTS and declares no layers. That is a fact ' +
          'about the table, not a failure to read it.'));
    return;
  }
  for (const L of layers) {
    const idx = (/(\d+)/.exec(L.label) || [])[1];
    const pair = byIdx[idx] || {};
    const card = el('div', 'fx-layer');
    // The reconciliation key with the stage (backlog 24). `markStageBuild`
    // finds a row by this and by nothing else -- not by position in the list,
    // which differs the moment a declared layer fails to PARSE and never
    // reaches this loop at all.
    if (idx != null) card.dataset.layerIndex = String(idx);
    const h = el('div', 'fx-layer-h');
    h.appendChild(el('b', null, L.label));
    h.appendChild(el('span', 'fx-form', L.form));
    // Filled by `markStageBuild` when the stage reports. Until then it says
    // so, rather than being absent: an empty slot reads as "nothing wrong".
    const v = el('span', 'fx-stage fx-stage-unknown', 'stage: not reported');
    v.dataset.stage = 'unknown';
    h.appendChild(v);
    // THE VISIBILITY TOGGLE (backlog 24, and the owner's second question).
    //
    // Drawn DISABLED and stays disabled until the stage reports that this
    // layer built something. A checkbox that is present and does nothing is
    // the failure this page has already paid for once -- bugs_open.md #6, a
    // control that could only report absence -- so a layer the stage could
    // not build is shown as FAILED with the box disabled, never as a switch
    // that shrugs.
    const box = el('input');
    box.type = 'checkbox';
    box.checked = true;
    box.disabled = true;
    box.className = 'fx-vis';
    box.title = 'the stage has not reported on this layer yet';
    const lab = el('label', 'fx-vis-l');
    lab.appendChild(box);
    lab.appendChild(el('span', null, 'show'));
    h.appendChild(lab);
    card.appendChild(h);

    card.appendChild(chainRow('mesh', '3DEffectObj', pair.mesh));
    card.appendChild(chainRow('texture', '3dtexture', pair.tex));

    const w = el('div', 'fx-write' +
      (/READ-ONLY/.test(L.source) ? ' fx-ro' : ''), L.source);
    card.appendChild(w);
    if (L.note) card.appendChild(el('div', 'mut fx-note', L.note));
    host.appendChild(card);
  }
  // The stage may already have reported -- it usually has, /api/effect being
  // the smaller call. Re-apply rather than wait for another report that is
  // never coming. `applyStageBuild` is a no-op when nothing is held or when
  // what is held is about a different effect.
  applyStageBuild();
}

/** BACKLOG 24 -- what the STAGE did with each layer the panel is showing.
 *
 *  THE TWO PAYLOADS ANSWER DIFFERENT QUESTIONS AND THAT IS CORRECT.
 *  "What it needs" comes from `/api/fx/effect` and is about RESOLUTION: does
 *  this layer name a mesh id, does that id have a row, is the file shipped.
 *  The stage comes from `/api/effect` and is about the GL BUILD: did the
 *  geometry arrive with indices, did the particle chunk decode. A layer can
 *  resolve perfectly and build nothing.
 *
 *  Nothing reconciled them, so the panel could list a layer the stage never
 *  drew and neither said so. The only signal was all-or-nothing --  `play3d`
 *  prints "no drawable layer" when ZERO instances build, and partial loss
 *  produced no message at all.
 *
 *  **THE COUNT COMES FROM THE BUILDER, NEVER FROM `def.layers`.** That is
 *  item 24's named trap: `def.layers.length` is the DECLARED count, which is
 *  the number printed two lines above, so a "drawn" column derived from it
 *  agrees with the parsed column by construction and is a check that cannot
 *  fire. `report.layers` is written in `fx.js::_build`, by the loop that
 *  actually decides what to keep.
 *
 *  ONE-DIRECTIONAL AND NOT A REFETCH. The stage reports; this marks. It
 *  touches the DOM this file already built and nothing else -- no state, no
 *  fetch, no write to the definition. `name` is checked because the user can
 *  move on while textures are in flight, and marking row 3 of a different
 *  effect is worse than not marking at all.
 *
 *  Three verdicts, and they are three different bugs:
 *      drawn             the layer built parts and the stage has them
 *      built but empty   the layer is there, every part was dropped
 *      not built         the builder never reached this layer
 *  A layer marked `drawn` that shows nothing on screen is a rendering
 *  problem. One marked `built but empty` is a data problem. Before this they
 *  were the same blank canvas. */
function markStageBuild(name, report) {
  // CACHE FIRST, THEN APPLY, and the order is the whole fix.
  //
  // The two payloads are independent fetches, so the stage can report BEFORE
  // the panel has drawn its rows -- and it usually does, because /api/effect
  // is the smaller call. Marking straight into the DOM lost the report every
  // time `renderLayers` ran afterwards and rebuilt the rows as `unknown`.
  // MEASURED before this: all three rows of `5fire` read "stage: not
  // reported" while the stage was drawing them.
  //
  // So the report is STATE, and `renderLayers` re-applies it at the end of
  // every render. Whichever arrives second, the page ends up correct.
  S.stageBuild = { name: name, report: report };
  applyStageBuild();
}


/** Paint `S.stageBuild` onto whatever rows are on screen right now. */
function applyStageBuild() {
  const host = $('#fx-layers');
  if (!host) return;
  const held = S.stageBuild;
  if (!held) return;
  const name = held.name, report = held.report;
  // The user can move on while textures are in flight; marking row 3 of a
  // different effect is worse than not marking at all.
  if (name != null && S.name != null && name !== S.name) return;
  const rows = host.querySelectorAll('[data-layer-index]');
  const byIdx = {};
  for (const L of ((report && report.layers) || [])) {
    byIdx[String(L.index)] = L;
  }
  let drawn = 0, empty = 0, absent = 0;
  for (const row of rows) {
    const badge = row.querySelector('.fx-stage');
    if (!badge) continue;
    const L = byIdx[row.dataset.layerIndex];
    let cls, txt, tag;
    if (!L) {
      cls = 'fx-stage fx-stage-absent'; tag = 'absent';
      txt = (report && report.threw)
        ? 'stage: NOT BUILT — the instance threw'
        : 'stage: NOT BUILT — the builder never reached this layer';
      absent += 1;
    } else if (L.kept > 0) {
      cls = 'fx-stage fx-stage-drawn'; tag = 'drawn';
      txt = 'stage: drawn — ' + L.kept + ' of ' + L.declared + ' part(s)';
      if (L.kept < L.declared) txt += ', ' + (L.declared - L.kept) + ' dropped';
      drawn += 1;
    } else {
      cls = 'fx-stage fx-stage-empty'; tag = 'empty';
      txt = 'stage: BUILT BUT EMPTY — all ' + L.declared +
            ' part(s) dropped, this layer contributes no pixels';
      empty += 1;
    }
    badge.className = cls;
    badge.dataset.stage = tag;
    badge.textContent = txt;
    // Arm the toggle only for a layer the stage actually built. `drawn` is
    // the ONLY armed state; `built but empty` and `not built` leave the box
    // disabled and say why in its tooltip, because there is nothing on the
    // stage for it to hide.
    const box = row.querySelector('.fx-vis');
    if (box) {
      const live = (tag === 'drawn');
      box.disabled = !live;
      box.checked = live ? box.checked : true;
      box.title = live
        ? 'hide this layer on the stage (view only -- nothing is written)'
        : (tag === 'empty'
            ? 'FAILED: every part of this layer was dropped, so there is '
              + 'nothing on the stage to hide'
            : 'FAILED: the stage never built this layer');
      const lab = box.parentNode;
      if (lab) lab.classList.toggle('fx-vis-dead', !live);
      const span = lab && lab.querySelector('span');
      if (span) span.textContent = live ? 'show' : 'FAILED';
      if (live && !box.dataset.wired) {
        box.dataset.wired = '1';
        box.addEventListener('change', () => {
          const E = window.CoEffects;
          const ok = E && E.setLayerVisible
            ? E.setLayerVisible(row.dataset.layerIndex, box.checked)
            : false;
          // If the stage refused, the box must not keep claiming a state the
          // picture does not have.
          if (!ok) { box.checked = true; box.disabled = true; }
        });
      }
    }
    // The REASON, per dropped part, at the point of use. A count tells you
    // to go looking; the reason is the thing you would have gone looking for.
    const had = row.querySelector('.fx-stage-why');
    if (had) had.remove();
    const drops = (L && L.drops) || [];
    if (drops.length) {
      const d = el('div', 'mut fx-note fx-stage-why',
        drops.map(x => x.kind + ': ' + x.why).join(' · '));
      row.appendChild(d);
    }
  }
  const sub = $('#fx-layers-sub');
  if (sub && rows.length) {
    const base = (sub.dataset.parsed || sub.textContent || '');
    sub.dataset.parsed = base;
    let extra = '  ·  stage drew ' + drawn + ' of ' + rows.length;
    if (empty) extra += ', ' + empty + ' built EMPTY';
    if (absent) extra += ', ' + absent + ' NOT BUILT';
    if (empty || absent) extra += '  \u26a0';
    sub.textContent = base + extra;
  }
}


function chainRow(what, table, sat) {
  const row = el('div', 'fx-chain');
  row.appendChild(el('span', 'fx-chain-k', what));
  if (!sat) {
    row.appendChild(el('span', 'mut',
      'the layer names no ' + what + ' id at all'));
    return row;
  }
  row.appendChild(el('span', 'fx-id', 'id ' + sat.asset_id));
  row.appendChild(el('span', 'fx-arrow', '→'));
  if (!sat.path) {
    row.appendChild(el('span', 'fx-bad', 'NO ROW in ' + table));
  } else if (!sat.present) {
    row.appendChild(el('span', 'fx-warn', sat.path));
    row.appendChild(el('span', 'fx-bad', 'FILE NOT SHIPPED'));
  } else {
    row.appendChild(el('span', 'fx-path', sat.path));
    if (sat.foreign) row.appendChild(el('span', 'fx-warn', 'FOREIGN install'));
  }
  row.appendChild(el('span', 'mut fx-src', '[' + (sat.source || table) + ']'));
  return row;
}

/** WHAT IT IS CONNECTED TO -- the REVERSE walk, in the owner's three
 *  categories. The counts are printed per category AND the known hole is
 *  restated here, at the point of use, because a reader who scrolled straight
 *  to this panel has not seen the strip at the top.
 *
 *  AND IT IS WALKABLE, not just readable. Each row names a SUBJECT -- a
 *  weapon appearance, a weapon type -- and every one of them is a place you
 *  would then want to go: what does that weapon look like, what ELSE does it
 *  play, what else is that type. `openSubject` and `openWeaponMesh` below are
 *  those three edges. Three rules govern all of them:
 *
 *    1. NAVIGATION IS DRIVEN BY `r.rule`, NEVER BY `r.label`. The label is
 *       prose written for a person; parsing an id back out of a sentence
 *       makes every future rewording a silent dead link. A row that arrives
 *       WITHOUT `rule` is rendered exactly as before and says why it cannot
 *       be walked, rather than offering a button that guesses.
 *
 *    2. THE DERIVED SPLIT SURVIVES THE LINK. `renderConnCat` prints, on the
 *       WEAPONS and SKILLS/ACTIONS headers, that membership of those two
 *       categories is a predicate over `ini/weapon.ini` and not a column in
 *       Action3DEffect.ini -- and every subject panel a link opens repeats it
 *       from the server's own `limits`. A link that reads as "the table says
 *       this is a weapon" would be the page asserting something no file says.
 *
 *    3. A LINK CARRIES THE BLIND SPOTS OF WHERE IT LANDS. Every expansion
 *       renders the `limits` the server returned for THAT subject, including
 *       the count of appearance ids of a type that rule rows name and
 *       `weapon.ini` does not declare -- 70 of type 601 on 5517. Navigation
 *       that dropped those would launder the caveat off the answer, which is
 *       the exact failure §6 calls a trap the user cannot detect.
 */
function renderConnections(d) {
  const host = $('#fx-conn');
  host.innerHTML = '';
  const cats = { weapon: [], action: [], map: [] };
  for (const b of (d.bindings || [])) {
    (cats[b.form] || (cats[b.form] = [])).push(b);
  }
  const LABEL = {
    weapon: 'WEAPONS',
    action: 'SKILLS / ACTIONS',
    map: 'MAP EFFECTS',
  };
  const SRC = {
    weapon: 'WeaponEffect.ini (impact spark, by weapon type) + ' +
            'Action3DEffect.ini rows whose appearance is in weapon.ini',
    action: 'Action3DEffect.ini rows not attributable to a weapon appearance',
    map: 'ActionMap3DEffect.ini',
  };
  /* THE SPLIT, NAMED AS DERIVED, ON THE TWO CATEGORIES IT INVENTS. It is
   * repeated per category rather than stated once at the bottom because the
   * two headers are what a reader takes away, and now that each row is a
   * link, the click happens right here. */
  const DERIVED = {
    weapon: 'DERIVED, not read: a row lands here because its appearance is a ' +
            'section in ini/weapon.ini. Action3DEffect.ini has no column ' +
            'saying a row is a weapon’s.',
    action: 'DERIVED, not read: a row lands here because its appearance is ' +
            'NOT a section in ini/weapon.ini (or pins no appearance at all). ' +
            'That is this page’s classification, not the table’s.',
    map: '',
  };
  let total = 0;
  for (const k of ['weapon', 'action', 'map']) {
    const rows = cats[k] || [];
    total += rows.length;
    host.appendChild(renderConnCat(k, rows, LABEL[k], SRC[k], DERIVED[k]));
  }
  const foot = el('div', 'fx-connfoot');
  foot.appendChild(el('b', null, total + ' connection(s) found'));
  foot.appendChild(el('div', 'mut',
    'THIS IS NOT A COMPLETE ANSWER and the amount it is short by is measured. ' +
    'It covers only the three rule tables EffectDB reads. Sibling rule files ' +
    'this install ships and EffectDB does NOT read are listed in the strip at ' +
    'the top of this page; an effect reached from the client executable, from ' +
    'a skill table, from npc/monster/scene data or from MediaEffect is not ' +
    'enumerated at all.'));
  foot.appendChild(el('div', 'mut',
    'Following a link does not widen this. Every panel a link opens is built ' +
    'from the SAME three tables and carries the same hole; each one restates ' +
    'what it is short by rather than inheriting a clean-looking answer.'));
  host.appendChild(foot);
  return host;
}

function renderConnCat(k, rows, label, src, derived) {
  const sec = el('div', 'fx-conncat');
  const h = el('div', 'fx-conncat-h');
  h.appendChild(el('b', null, label));
  h.appendChild(el('span', 'fx-num', String(rows.length)));
  sec.appendChild(h);
  sec.appendChild(el('div', 'mut fx-note', 'from ' + src));
  if (derived) sec.appendChild(el('div', 'fx-derived', derived));
  if (!rows.length) {
    sec.appendChild(el('div', 'mut',
      'No row in the table(s) above names this effect.'));
    return sec;
  }
  const ul = el('ul', 'fx-connlist');
  /* Long lists are capped in the DOM but NEVER in the count. The count
   * above is the real one; this only limits what is drawn, and it says
   * so, so the two can never be confused. */
  const CAP = 60;
  for (const r of rows.slice(0, CAP)) ul.appendChild(connRow(r));
  sec.appendChild(ul);
  if (rows.length > CAP) {
    sec.appendChild(el('div', 'mut',
      'showing the first ' + CAP + ' of ' + rows.length +
      ' — the count above is the whole set. Every row not drawn is still ' +
      'counted, and the subjects they name are reachable from the rows ' +
      'that are.'));
  }
  return sec;
}

/** ONE connection row: the sentence it always was, plus the walks its own
 *  key supports. Nothing here infers a subject the row did not carry. */
function connRow(r) {
  const li = el('li', 'fx-connrow');
  const head = el('div', 'fx-connrow-h');
  head.appendChild(el('span', null, r.label));
  head.appendChild(el('span', 'mut fx-src', '[' + r.source + ']'));
  li.appendChild(head);

  const rule = r.rule || null;
  const links = el('div', 'fx-connlinks');
  const exp = el('div', 'fx-connexp');
  exp.hidden = true;

  if (!rule) {
    /* An older server, or `connections=0`. The row is still true; it is
     * simply not walkable, and saying so beats a button that guesses an id
     * out of the prose above. */
    links.appendChild(el('span', 'mut',
      'not navigable — this row arrived without its structured key, so the ' +
      'subject it names is not known to this page (the sentence above is ' +
      'still what the table said)'));
    li.appendChild(links);
    return li;
  }

  const app = rule.appearance || '';
  const wt = rule.weaponType || '';

  if (app) {
    links.appendChild(navBtn('appearance ' + app + ' — what else it plays',
      () => openSubject(exp, { appearance: app })));
    links.appendChild(navBtn('open ' + app + ' in the Model Viewer',
      () => openWeaponMesh(exp, app)));
    if (wt) {
      links.appendChild(navBtn(
        'type ' + wt + (rule.typeName ? ' (' + rule.typeName + ')' : '') +
        ' — every appearance',
        () => openSubject(exp, { type: wt })));
    }
  } else if (wt) {
    links.appendChild(navBtn(
      'type ' + wt + (rule.typeName ? ' (' + rule.typeName + ')' : '') +
      ' — every appearance of this type',
      () => openSubject(exp, { type: wt })));
  } else {
    /* Map rows key on shape + action + terrain and name no appearance and no
     * weapon type. There IS no subject view for that key, and inventing a
     * link to one would be the page asserting a subject the table does not
     * have. Say what it keys on instead. */
    const bits = [];
    if (rule.shape) bits.push('shape ' + rule.shape);
    if (rule.action) bits.push('action ' + rule.action);
    if (rule.terrain) bits.push('terrain ' + rule.terrain);
    links.appendChild(el('span', 'mut',
      'keys on ' + (bits.join(' + ') || '(no key fields)') +
      ' — it names no appearance and no weapon type, so there is no subject ' +
      'to open. That is what this row keys on, not a gap in this page.'));
  }
  li.appendChild(links);
  li.appendChild(exp);
  return li;
}

function navBtn(text, fn) {
  const b = el('button', 'ghost fx-nav', text);
  b.addEventListener('click', fn);
  return b;
}

/* ------------------------------------------------- walking the reverse edge */

/** THE SIDEWAYS WALK, RENDERED IN PLACE.
 *
 *  `?appearance=` is "what else does this subject play"; `?type=` is "every
 *  appearance of this type". Both are `depclose.subject_effects` and neither
 *  is computed here -- the appearance key grammar (the hi/lo split, the
 *  low-group wildcard, the all-nines sentinel) belongs to the walk, and a
 *  second implementation of it in this file would drift from the answer it
 *  is meant to be showing.
 */
async function openSubject(host, q) {
  host.hidden = false;
  host.innerHTML = '';
  host.appendChild(el('div', 'mut', 'resolving the subject…'));
  const qs = q.appearance
    ? 'appearance=' + encodeURIComponent(q.appearance)
    : 'type=' + encodeURIComponent(q.type);
  const j = await getJSON('/api/fx/subject?' + qs);
  host.innerHTML = '';
  const close = el('button', 'ghost fx-nav', 'close');
  close.addEventListener('click', () => { host.hidden = true; });
  host.appendChild(close);
  if (j.error) {
    host.appendChild(el('div', 'fx-bad', j.error));
    return;
  }
  if (!j.measured) {
    /* Same distinction as everywhere else on this page: a walk that could
     * not run is not a walk that found nothing. */
    host.appendChild(el('div', 'fx-bad',
      'UNMEASURED — the rule tables would not load for this subject. ' +
      'Everything below is missing, not absent.'));
    renderSubjectLimits(host, j.limits || []);
    return;
  }
  const h = el('div', 'fx-subjh');
  h.appendChild(el('b', null,
    j.kind === 'appearance' ? 'appearance ' + j.appearance
                            : 'weapon type ' + j.weaponType));
  if (j.typeName) h.appendChild(el('span', 'mut', j.typeName));
  host.appendChild(h);

  if (j.kind === 'appearance') {
    /* The predicate that put the row you clicked in WEAPONS or ACTIONS,
     * stated for this id specifically. It is the whole of the split. */
    host.appendChild(el('div', 'fx-derived',
      j.inWeaponIni
        ? 'ini/weapon.ini DOES declare a section ' + j.appearance +
          ' — which is the only reason rows keyed to it are filed under ' +
          'WEAPONS. Action3DEffect.ini itself does not say so.'
        : 'ini/weapon.ini does NOT declare a section ' + j.appearance +
          ' — which is the only reason rows keyed to it are filed under ' +
          'SKILLS / ACTIONS. The table does not say they are not a ' +
          'weapon’s; nothing in it says either way.'));
  }

  const rows = j.rows || [];
  host.appendChild(el('div', 'fx-subjcount',
    rows.length + ' rule row(s) name this subject'));

  /* PLAY THEM ALL, TOGETHER (owner, 2026-09-10). The rows above are LINKS --
   * one effect at a time, which is what this pop-out has always offered, and
   * it is the wrong shape for the question "what does this skill look like".
   * A subject like appearance 410009 plays three named effects at once and
   * you cannot see that by opening them one after another.
   *
   * Only offered when the subject is an APPEARANCE and names two or more
   * distinct effects: a button that stages a list of one is a button that
   * does what clicking the link already did, and offering it on a subject
   * with nothing to compose reads as a feature that is broken rather than
   * one that does not apply here.
   *
   * `CoEffects` owns the stage; this file never touches `S.viewer`. Guarded
   * the same way `tellFxView` is, and for the same reason -- a page that
   * dies whole because its second script failed is worse than one that loses
   * a control and says which. */
  const distinct = [];
  for (const r of rows) {
    if (r.effect && distinct.indexOf(r.effect) < 0) distinct.push(r.effect);
  }
  if (j.kind === 'appearance' && distinct.length > 1) {
    const E = window.CoEffects;
    const play = el('button', 'ghost fx-playall',
      '▶ play all ' + distinct.length + ' effects together');
    if (!E || !E.playSubject) {
      play.disabled = true;
      play.title = 'the stage script did not load on this page';
    } else {
      play.title = 'Stage every effect this subject plays, with a switch on ' +
                   'each. View only — nothing is written.';
      play.addEventListener('click', () => {
        host.hidden = true;
        E.playSubject(j.appearance);
      });
    }
    host.appendChild(play);
    host.appendChild(el('div', 'mut fx-subjnote',
      distinct.length + ' distinct effect(s): ' + distinct.join(', ')));
  }
  if (!rows.length) {
    host.appendChild(el('div', 'mut',
      'No Action3DEffect or WeaponEffect row names it. That is what the ' +
      'tables say.'));
  } else {
    const ul = el('ul', 'fx-connlist');
    for (const r of rows.slice(0, 60)) {
      const li = el('li');
      li.appendChild(el('span', 'mut', r.table + ' · ' + r.role + ' →'));
      li.appendChild(effectLink(r.effect));
      ul.appendChild(li);
    }
    host.appendChild(ul);
    if (rows.length > 60) {
      host.appendChild(el('div', 'mut',
        'showing the first 60 of ' + rows.length));
    }
  }

  /* Rendered for BOTH subject kinds, not just `weapon_type`. Landing on an
   * appearance, "what else is this type" is the same next question, and the
   * server already answered it in this response -- withholding it would make
   * the walk take two round trips to reach an answer it was holding. */
  if ((j.appearances || []).length || (j.undeclaredAppearances || []).length) {
    renderTypeAppearances(host, j);
  }
  renderSubjectLimits(host, j.limits || [], j);
}

/** Every appearance of a weapon type -- in TWO lists, and they must stay two.
 *
 *  `appearances` is what `ini/weapon.ini` declares. `undeclaredAppearances`
 *  is the ids that Action3DEffect rows NAME and weapon.ini does not declare
 *  -- 70 of type 601 on 5517, and they are precisely the rows the derived
 *  split files under ACTIONS instead of WEAPONS. Merging them would make the
 *  split invisible; dropping them would make "every appearance of this type"
 *  short by 70 and look complete. So: both, apart, each labelled by the rule
 *  that put it there.
 */
function renderTypeAppearances(host, j) {
  const CAP = 80;
  const mk = (title, list, cls, note) => {
    const box = el('div', 'fx-appbox' + (cls ? ' ' + cls : ''));
    const hh = el('div', 'fx-subjcount');
    hh.appendChild(el('b', null, title));
    hh.appendChild(el('span', 'fx-num', String(list.length)));
    box.appendChild(hh);
    box.appendChild(el('div', 'mut fx-note', note));
    if (!list.length) {
      box.appendChild(el('div', 'mut', 'none'));
      host.appendChild(box);
      return;
    }
    const ul = el('ul', 'fx-applist');
    for (const a of list.slice(0, CAP)) {
      const li = el('li', a.isSubject ? 'fx-appself' : null);
      const b = el('button', 'ghost fx-nav', a.appearance);
      b.title = 'open this appearance as a subject';
      if (a.isSubject) li.appendChild(el('span', 'mut', '▸ you are here:'));
      const nested = el('div', 'fx-connexp');
      nested.hidden = true;
      b.addEventListener('click',
        () => openSubject(nested, { appearance: a.appearance }));
      li.appendChild(b);
      li.appendChild(el('span', 'mut',
        a.ruleRows + ' rule row(s)' +
        (a.effects && a.effects.length
          ? ' → ' + a.effects.slice(0, 4).join(', ') +
            (a.effects.length > 4 ? ', …' : '')
          : '')));
      li.appendChild(nested);
      ul.appendChild(li);
    }
    box.appendChild(ul);
    if (list.length > CAP) {
      box.appendChild(el('div', 'mut',
        'showing the first ' + CAP + ' of ' + list.length +
        ' — the count above is the whole set'));
    }
    host.appendChild(box);
  };
  host.appendChild(el('div', 'fx-subjcount',
    'every appearance of weapon type ' + j.weaponType +
    (j.typeName ? ' (' + j.typeName + ')' : '')));
  mk('declared in ini/weapon.ini', j.appearances || [], '',
     'These are the appearance ids weapon.ini declares for this type. A row ' +
     'keyed to one of them is what this page files under WEAPONS.');
  mk('named by rule rows, NOT declared in weapon.ini',
     j.undeclaredAppearances || [], 'fx-appbox-undecl',
     'Action3DEffect rows name these ids and weapon.ini has no section for ' +
     'them. They ARE appearances of this type by the id grammar and they are ' +
     'exactly the rows the derived split files under SKILLS / ACTIONS. A ' +
     '"every appearance of this type" list built from weapon.ini alone is ' +
     'short by this many.');
}

/** A clickable effect name. Names not in this install’s 3DEffect list are
 *  marked BEFORE the click, not after: a rule row may name an effect the
 *  definition table does not define, and that is a finding, not a 404. */
function effectLink(name) {
  if (!name) return el('span', 'mut', '(the row names no effect)');
  const wrap = el('span', 'fx-fxlink-w');
  const b = el('button', 'fx-fxlink', name);
  b.addEventListener('click', () => goto_(name));
  wrap.appendChild(b);
  if (S.names.length && !S.names.includes(name)) {
    wrap.appendChild(el('span', 'fx-warn',
      'NOT DEFINED in 3DEffect on this install'));
  }
  return wrap;
}

/** The blind spots of the place a link LANDED, carried verbatim. */
function renderSubjectLimits(host, limits, j) {
  const box = el('div', 'fx-subjlim');
  box.appendChild(el('b', null, 'What this subject panel is short by'));
  const ul = el('ul');
  if (j && j.wildcardRows) {
    ul.appendChild(el('li', null,
      j.wildcardRows + ' Action3DEffect row(s) are keyed to NO appearance ' +
      'and apply to this subject too; they are counted here and not listed.'));
  }
  for (const l of (limits || [])) ul.appendChild(el('li', null, l));
  if (!ul.childElementCount) {
    ul.appendChild(el('li', 'mut', '(none recorded)'));
  }
  box.appendChild(ul);
  host.appendChild(box);
}

/** A WEAPON APPEARANCE, OPENED IN THE MODEL VIEWER.
 *
 *  The Model Viewer restores from `#model=<kind>:<ident>` and from
 *  `#mesh=<path>`, and there is NO `weapon` model kind -- weapons are
 *  appearance rows, not a model family -- so this resolves the appearance to
 *  a mesh first, through the existing `/api/weaponmotion`, and links to the
 *  path. Composing a `#model=weapon:NNN` link would produce a URL the other
 *  page silently ignores, which is the same class of bug `effects.js` records
 *  beside its own "Play in Model Viewer" button.
 *
 *  IT REPORTS THE MISS RATHER THAN HIDING THE BUTTON. An appearance whose
 *  mesh this install does not ship is a real state -- 7,462 of 30,539
 *  appearance references resolve to no file at all -- and a button that
 *  quietly vanished would make that state look like it does not exist.
 *
 *  AND IT CARRIES THE SWAP. A weapon does not deform; the client swaps in a
 *  different mesh per action (WeaponMotion.ini). What opens is ONE mesh --
 *  the default -- and the panel names which, so the viewer is not read as
 *  showing the whole weapon.
 */
async function openWeaponMesh(host, appearance) {
  host.hidden = false;
  host.innerHTML = '';
  host.appendChild(el('div', 'mut', 'resolving the appearance’s mesh…'));
  const j = await getJSON('/api/weaponmotion?id=' + encodeURIComponent(appearance));
  host.innerHTML = '';
  const close = el('button', 'ghost fx-nav', 'close');
  close.addEventListener('click', () => { host.hidden = true; });
  host.appendChild(close);
  if (j.error && !j.chosen) {
    host.appendChild(el('div', 'fx-bad', j.error));
    return;
  }
  const mesh = j.chosen || '';
  if (!mesh) {
    host.appendChild(el('div', 'fx-bad',
      'Appearance ' + appearance + ' resolves to NO mesh on this install: ' +
      'neither weapon.ini nor WeaponMotion.ini names one. Nothing to open ' +
      '— and that is a measured state, not a missing feature.'));
    return;
  }
  if (!j.exists) {
    host.appendChild(el('div', 'fx-bad',
      'DECLARED AND ABSENT — the tables name ' + mesh + ' for appearance ' +
      appearance + ' and this install does not ship that file. Opening it ' +
      'would show an empty stage, so the link is not offered.'));
    host.appendChild(el('div', 'mut',
      'This is the same class as the standing measure: 7,462 of 30,539 ' +
      'appearance references resolve to no file at all.'));
    return;
  }
  const p = el('div', 'mut');
  p.appendChild(el('span', 'fx-path', mesh));
  host.appendChild(p);
  host.appendChild(el('div', 'mut',
    'A weapon does not deform — the client swaps in a different mesh per ' +
    'action (ini/WeaponMotion.ini, ' + ((j.actions &&
      Object.keys(j.actions).length) || 0) + ' action row(s) here). This ' +
    'opens ONE of them: ' + (j.chosenSource || 'the default') + '. The ' +
    'Model Viewer will show that mesh, not the whole weapon.'));
  const go = el('button', 'ghost fx-nav', 'Open in the Model Viewer');
  go.addEventListener('click', () => {
    let u = '/models#mesh=' + encodeURIComponent(mesh);
    if (j.texture) u += '&tex=' + encodeURIComponent(j.texture);
    location.href = u;
  });
  host.appendChild(go);
  if (!j.texture) {
    host.appendChild(el('div', 'fx-warn',
      'No texture is declared for this appearance, so it opens untextured. ' +
      'That is this install’s row, not a viewer fault.'));
  }
}

/* ------------------------------------------------- compare across installs */

/** MULTI-BASE. The same named effect on N installs at once.
 *
 *  WHY THIS PANEL IS THE ONE THAT CANNOT GUESS THE FILE THAT ANSWERED, and
 *  it is rule 1 at the top of this file applied N times rather than once.
 *  The twin trap is a PER-BASE fact: `3DEffect.dbc`, `3DEffectobj.dbc`,
 *  `3DTexture.dbc` and `3DObj.dbc` ship on 5517/6090/6609/7205 and nowhere
 *  else, so a comparison is the one screen where "this base answered out of
 *  a .dbc and that one out of the .ini" becomes visible -- and equally the
 *  one screen where getting it wrong silently invents or deletes a finding.
 *
 *  MEASURED, and it is the case this panel is built around: the decoy
 *  `3DEffect.ini` disagrees with the live `3DEffect.dbc` for 18 names on
 *  6090 and 19 on 6609/7205. `InsigniaNoble02s` reads FOUR layers out of the
 *  decoy on all three and TWO out of the twin the client reads -- and four
 *  on 5017/5165/5517/7878, which have no twin. Read the decoy and those
 *  seven bases render as SEVEN IDENTICAL COLUMNS.
 *
 *  THREE COLUMN STATES AND THEY NEVER LOOK ALIKE:
 *    building        the graph is still being walked. Not empty, not zero.
 *    UNMEASURED      `measured: false` -- the tables would not load there.
 *    UNPROVENANCED   measured, but which file answered is not known, so the
 *                    column is shown and takes NO part in any verdict.
 *  Only columns that are measured AND provenanced carry verdicts, and the
 *  server names them in `comparedOver` on every single row -- which this
 *  panel prints, so "same" can never be read as "same across everything you
 *  picked" when it was not.
 */
const CMP = {
  bases: [],        // /api/fx/bases rows
  chosen: [],       // base ids the user picked
  data: null,       // the last /api/fx/compare payload
  name: '',         // the effect that payload is for
  poll: 0,
  known: false,     // did /api/fx/bases answer at all
};

async function loadCmpBases() {
  const j = await getJSON('/api/fx/bases');
  if (j.error && !(j.bases || []).length) {
    CMP.known = false;
    $('#fx-cmp-chosen').textContent = 'the install list could not be read: ' +
      j.error;
    return;
  }
  CMP.known = true;
  CMP.bases = j.bases || [];
  if (!CMP.chosen.length) CMP.chosen = (j.default || []).slice();
  renderCmpChosen();
}

function baseRow(id) {
  return CMP.bases.find(b => b.id === id) || null;
}

function renderCmpChosen() {
  const n = CMP.chosen.length;
  $('#fx-cmp-chosen').textContent = n
    ? n + ' install(s): ' +
      CMP.chosen.map(i => (baseRow(i) || {}).label || i).join(', ')
    : 'no install picked';
}

/** The picker. It shows the `.dbc` twin probe next to each install BECAUSE
 *  picking two bases on the same side of that split and two across it are
 *  different experiments -- but it labels the probe as a HINT, because a
 *  `3DEffect.dbc` sitting on disk is not the same claim as "that file
 *  answered", and only the compare response can make the second one. */
function renderCmpPicker() {
  const host = $('#fx-cmp-picker');
  host.innerHTML = '';
  if (!CMP.known) {
    host.appendChild(el('div', 'fx-bad',
      'The install list is UNREAD, so no comparison can be set up here. ' +
      'This is not "there are no other installs".'));
    return;
  }
  host.appendChild(el('div', 'mut',
    'Each install picked costs one dependency-graph build (7–34 s cold in ' +
    'the worst case, measured 0.4–0.9 s on 5517/6090/7878). Pick the few ' +
    'you actually want to compare.'));
  const ul = el('ul', 'fx-cmp-baselist');
  for (const b of CMP.bases) {
    const li = el('li');
    const lab = el('label');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = CMP.chosen.includes(b.id);
    cb.addEventListener('change', () => {
      CMP.chosen = cb.checked
        ? CMP.chosen.concat([b.id])
        : CMP.chosen.filter(x => x !== b.id);
      renderCmpChosen();
    });
    lab.appendChild(cb);
    lab.appendChild(el('span', 'fx-cmp-blabel', b.label));
    /* HINT, not provenance, and the wording has to keep saying so: the
     * compare response is the only thing that knows what answered. */
    let hint, cls;
    if (!b.twinKnown) {
      hint = 'compiled twin UNKNOWN here (a library view has no ini/ of its own)';
      cls = 'mut';
    } else if (b.twin) {
      hint = 'ships ' + b.twin + ' — the .ini beside it is a decoy';
      cls = 'fx-cmp-hint-dbc';
    } else {
      hint = 'no compiled 3DEffect twin';
      cls = 'fx-cmp-hint-ini';
    }
    lab.appendChild(el('span', cls, hint));
    if (b.state === 'ready') lab.appendChild(el('span', 'mut', '· built'));
    lab.appendChild(el('span', 'mut fx-cmp-root', b.root));
    li.appendChild(lab);
    ul.appendChild(li);
  }
  host.appendChild(ul);
}

/** Selecting a new effect INVALIDATES the comparison rather than leaving the
 *  old one on screen under a new heading. A stale matrix beside a different
 *  effect's name is the most convincing wrong answer this page could give:
 *  every number in it is real, and every one of them is about something
 *  else. Re-run is automatic only when a comparison was already open, so
 *  browsing the list never spends graph builds you did not ask for. */
function resetCompare(name) {
  if (CMP.poll) { clearTimeout(CMP.poll); CMP.poll = 0; }
  const had = CMP.data && CMP.name && CMP.name !== name;
  CMP.data = null;
  const host = $('#fx-cmp-out');
  host.innerHTML = '';
  if (had) {
    host.appendChild(el('div', 'mut', 're-comparing for “' + name + '”…'));
    runCompare(true);
  } else {
    host.appendChild(el('div', 'mut',
      'Not run for “' + name + '”. A dependency graph per install is a ' +
      '7–34 s cold walk in the worst case, so nothing is opened until you ' +
      'ask for it.'));
  }
}

async function runCompare(force) {
  if (CMP.poll) { clearTimeout(CMP.poll); CMP.poll = 0; }
  if (!S.sel) return;
  const host = $('#fx-cmp-out');
  if (!CMP.chosen.length) {
    host.innerHTML = '';
    host.appendChild(el('div', 'fx-bad',
      'No install picked, so nothing was compared. This is an empty ' +
      'SELECTION, not a finding about the effect.'));
    return;
  }
  const name = S.sel;
  if (force) {
    host.innerHTML = '';
    host.appendChild(el('div', 'mut', 'opening ' + CMP.chosen.length +
                        ' install(s)…'));
  }
  const j = await getJSON('/api/fx/compare?name=' + encodeURIComponent(name) +
                          '&bases=' + encodeURIComponent(CMP.chosen.join(',')));
  if (S.sel !== name) return;                 // the user moved on
  CMP.data = j;
  CMP.name = name;
  renderCompare(j);
  if ((j.building || []).length) {
    /* A base still walking is polled for, never rendered as an empty
     * column. The distinction is the whole page. */
    CMP.poll = setTimeout(() => runCompare(false), 1500);
  }
}

function renderCompare(j) {
  const host = $('#fx-cmp-out');
  host.innerHTML = '';
  if (j.error && !(j.columns || []).length) {
    host.appendChild(el('div', 'fx-bad', j.error));
    return;
  }
  const cols = j.columns || [];
  const d = j.diff || {};

  /* THE HEADLINE. A `.dbc` base being read beside an `.ini` base is the
   * single fact that makes every row below it interpretable, so it goes
   * first and it is loud. */
  const split = d.provenanceSplit || {};
  const splitKeys = Object.keys(split);
  if (splitKeys.length) {
    const box = el('div', 'fx-cmp-split');
    box.appendChild(el('b', null,
      'THESE COLUMNS DID NOT ALL ANSWER OUT OF THE SAME KIND OF FILE.'));
    for (const t of splitKeys) {
      const bits = [];
      for (const form of Object.keys(split[t])) {
        bits.push(form.toUpperCase() + ': ' +
                  split[t][form].map(i => labelOf(j, i)).join(', '));
      }
      box.appendChild(el('div', null, t + ' — ' + bits.join('  ·  ')));
    }
    box.appendChild(el('div', 'mut',
      'Where a compiled .dbc twin exists the client reads the twin and the ' +
      '.ini beside it is a DECOY. A difference below between a dbc column ' +
      'and an ini column is a difference between two different files as ' +
      'well as between two clients — read them together, never apart.'));
    host.appendChild(box);
  } else if ((d.comparedOver || []).length > 1) {
    host.appendChild(el('div', 'fx-cmp-nosplit',
      'All ' + d.comparedOver.length + ' compared column(s) answered out of ' +
      'the same KIND of file for 3DEffect, 3DEffectObj and 3dtexture. ' +
      'Differences below are between the clients, not between a twin and a ' +
      'decoy.'));
  }

  /* WHO IS ACTUALLY BEING COMPARED. Printed before the table, not after,
   * because a "same" verdict means nothing until you know over what. */
  const rail = el('div', 'fx-cmp-rail');
  rail.appendChild(el('div', null,
    'Verdicts computed over ' + (d.comparedOver || []).length +
    ' comparable column(s): ' +
    ((d.comparedOver || []).map(i => labelOf(j, i)).join(', ') || '(none)')));
  for (const [key, txt] of [
    ['pending', 'still being read — UNREAD, not empty'],
    ['unmeasured', 'UNMEASURED — the effect tables would not load there'],
    ['unprovenanced',
     'UNPROVENANCED — read, but which file answered is not known, so these ' +
     'take no part in any verdict below'],
    ['failed', 'could not be opened at all'],
  ]) {
    const ids = d[key] || [];
    if (!ids.length) continue;
    rail.appendChild(el('div', 'fx-cmp-rail-bad',
      ids.map(i => labelOf(j, i)).join(', ') + ' — ' + txt));
  }
  if ((d.comparedOver || []).length < 2) {
    rail.appendChild(el('div', 'fx-cmp-rail-bad',
      'Fewer than two comparable columns, so every verdict below is ' +
      'INSUFFICIENT. Nothing here says two installs agree.'));
  }
  host.appendChild(rail);

  /* THE PER-COLUMN PROVENANCE STRIP. One per base, above the matrix, so the
   * file that answered is never more than a glance from the value it
   * produced. Rule 1 at the top of this file, N times. */
  const heads = el('div', 'fx-cmp-heads');
  for (const c of cols) heads.appendChild(cmpHead(c));
  host.appendChild(heads);

  /* THE MATRIX. */
  const t = el('table', 'fx-cmp-table');
  const hd = el('tr');
  hd.appendChild(el('th', null, ''));
  for (const c of cols) hd.appendChild(el('th', null, c.label));
  hd.appendChild(el('th', null, 'verdict'));
  t.appendChild(hd);
  for (const r of (d.rows || [])) {
    const tr = el('tr', 'fx-cmp-r-' + r.kind + ' fx-cmp-v-' + r.verdict);
    const th = el('th', null, r.label);
    if (r.note) th.title = r.note;
    tr.appendChild(th);
    for (const c of cols) tr.appendChild(cmpCell(r, c));
    const v = el('td', 'fx-cmp-verdict', VERDICT[r.verdict] || r.verdict);
    v.title = 'computed over: ' +
              (r.comparedOver.map(i => labelOf(j, i)).join(', ') || '(none)');
    /* A row whose verdict was reached over FEWER columns than the header
     * shows says so on the row, not in a footnote. Otherwise "same" reads as
     * "same everywhere you picked", and for the form rows it would not be:
     * a base that does not ship the mesh has no form to compare, and setting
     * it aside silently is the same class of lie as an empty cell. */
    if ((r.notApplicable || []).length) {
      const na = el('span', 'fx-cmp-na', ' (not over ' +
        r.notApplicable.map(i => labelOf(j, i)).join(', ') + ')');
      na.title = 'this row was not measured on those bases, so they take no ' +
                 'part in this verdict';
      v.appendChild(na);
    }
    tr.appendChild(v);
    t.appendChild(tr);
  }
  host.appendChild(t);

  const foot = el('div', 'fx-cmp-foot');
  foot.appendChild(el('div', 'mut', d.note || ''));
  const diffs = (d.differing || []);
  foot.appendChild(el('b', null, diffs.length
    ? diffs.length + ' data row(s) differ between the compared columns'
    : 'no data row differs between the compared columns'));
  /* The honesty carry, per column, and it is per column because the amount
   * differs per base: 674 keys on 6090, 1,168 on 6609, none on 5017. */
  for (const c of cols) {
    if (c.state !== 'ready' || !c.unreadKnown) continue;
    if (!(c.unreadTables || []).length) continue;
    foot.appendChild(el('div', 'mut',
      c.label + ' ships ' + c.unreadTables.length + ' rule file(s) (' +
      c.unreadRows + ' keys) that EffectDB does NOT read: ' +
      c.unreadTables.map(u => u.file).join(', ') +
      ' — this column\'s connection counts are short by that measured amount.'));
  }
  host.appendChild(foot);
}

const VERDICT = {
  same: 'same',
  differs: 'DIFFERS',
  insufficient: 'not comparable',
};

function labelOf(j, id) {
  for (const c of (j.columns || [])) if (c.id === id) return c.label;
  return id;
}

/** One column's own provenance strip. Nothing is inferred here: `answeredBy`
 *  comes from `depclose.effect_tables()` and where it is absent the strip
 *  says UNKNOWN rather than naming the `.ini`, for the same reason
 *  `renderTables` does. */
function cmpHead(c) {
  const box = el('div', 'fx-cmp-head' +
                 (c.state === 'ready' ? '' : ' fx-cmp-head-off'));
  box.appendChild(el('b', null, c.label));
  if (c.state === 'building') {
    box.appendChild(el('div', 'fx-warn',
      'reading this install… every cell below is UNREAD, not zero'));
    return box;
  }
  if (c.state === 'error') {
    box.appendChild(el('div', 'fx-bad',
      'could not open: ' + (c.error || 'unknown') +
      ' — UNMEASURED, not zero'));
    return box;
  }
  if (!c.measured) {
    box.appendChild(el('div', 'fx-bad',
      'the effect tables would not load here — every cell below is ' +
      'UNMEASURED, not zero'));
    return box;
  }
  if (!c.tablesKnown) {
    box.appendChild(el('div', 'fx-bad',
      'PROVENANCE UNKNOWN — the values below were read, but which file ' +
      'produced them is not known on this base. Do NOT read this as "the ' +
      '.ini". This column takes no part in any verdict.'));
    return box;
  }
  const ul = el('ul', 'fx-cmp-prov');
  for (const tbl of ['3DEffect', '3DEffectObj', '3dtexture']) {
    const a = (c.answeredBy || {})[tbl];
    const li = el('li');
    li.appendChild(el('span', 'fx-tname', tbl));
    if (!a || !a.file) {
      li.appendChild(el('span', 'fx-bad',
        a && a.form === 'missing'
          ? 'ABSENT — read as an EMPTY table'
          : 'NOT REPORTED'));
    } else {
      li.appendChild(el('span',
        a.form === 'dbc' ? 'fx-file fx-cmp-dbc' : 'fx-file', a.file));
      li.appendChild(el('span', 'mut', a.rows + ' row(s)'));
      if (a.notRead) {
        const d = el('span', 'fx-decoy', a.notRead + ' ← DECOY');
        d.title = 'present, and the client does NOT read it. An id resolved ' +
                  'out of it is a DIFFERENT answer from the one the client ' +
                  'gets on this base.';
        li.appendChild(d);
      }
    }
    ul.appendChild(li);
  }
  box.appendChild(ul);
  return box;
}

/** ONE CELL, AND THE WHOLE RULE IS HERE.
 *
 *  An UNMEASURED column and a column that measured ZERO must never render
 *  alike, so the column's own state is consulted BEFORE the value is looked
 *  at -- an unmeasured column carries a `null` in every row, and if the value
 *  were consulted first it would print as a blank exactly like a measured
 *  absence. `EffectClosure` draws that distinction with `measured`/`found`
 *  and this is where it is carried onto the screen.
 *
 *  A `null` in a MEASURED column is not blank either: the row says what its
 *  own null means (`nullLabel`), because "the effect is not defined on that
 *  base", "there is no unread sibling to name" and "the scan did not run"
 *  all serialise identically and are three different facts.
 */
function cmpCell(row, c) {
  const td = el('td');
  if (c.state === 'building') {
    td.className = 'fx-cmp-unread';
    td.textContent = 'reading…';
    td.title = 'this install is still being walked — UNREAD, not zero';
    return td;
  }
  if (c.state === 'error' || !c.measured) {
    td.className = 'fx-cmp-unmeasured';
    td.textContent = 'UNMEASURED';
    td.title = c.state === 'error'
      ? 'this install could not be opened: ' + (c.error || '')
      : 'the effect tables would not load on this install. This is NOT zero ' +
        'and NOT an absence of the effect.';
    return td;
  }
  const v = row.values ? row.values[c.id] : undefined;
  if (v === null || v === undefined) {
    td.className = 'fx-cmp-null';
    td.textContent = '—';
    td.title = row.nullLabel || 'no value';
    return td;
  }
  td.textContent = String(v);
  if (!c.tablesKnown) {
    td.className = 'fx-cmp-unprov';
    td.title = 'read, but which file answered on this base is NOT known — ' +
               'this column takes no part in the verdict';
  } else if (row.verdict === 'differs' && row.comparedOver.includes(c.id)) {
    td.className = 'fx-cmp-diff';
  }
  return td;
}

/* --------------------------------------------------------- tag / name panel */

async function renderTagPanel(name) {
  const subj = subjectFor(name);
  const j = await getJSON('/api/tags?subject=' + encodeURIComponent(subj));
  if (S.sel !== name) return;
  S.tags[subj] = j.tags || [];
  S.renames[subj] = j.note || '';
  $('#fx-rename').value = S.renames[subj] || '';
  const host = $('#fx-tags');
  host.innerHTML = '';
  if (!(j.tags || []).length) {
    host.appendChild(el('span', 'mut', 'no tags yet'));
  }
  for (const t of (j.tags || [])) {
    const chip = el('span', 'fx-chip', t);
    const x = el('button', 'fx-chipx', '×');
    x.title = 'remove this tag';
    x.addEventListener('click', () => setTags(
      name, (S.tags[subj] || []).filter(v => v !== t)));
    chip.appendChild(x);
    host.appendChild(chip);
  }
}

async function setTags(name, tags) {
  const subj = subjectFor(name);
  const j = await postJSON('/api/tags', { subject: subj, tags });
  if (j.error) { status('tag write failed: ' + j.error); return; }
  S.tags[subj] = tags;
  renderTagPanel(name);
  redrawList();
}

/** Ask `effects.js` to redraw its picker after a tag or a rename.
 *
 *  It is a REDRAW and not a data hand-off: the tagged-only filter asks
 *  `isTagged` per name at draw time, so there is one copy of the tag state
 *  and it is this file's. Guarded, like every other call across the seam. */
function redrawList() {
  const E = window.CoEffects;
  if (E && E.redrawList) E.redrawList();
}

async function saveRename(name) {
  const subj = subjectFor(name);
  const val = ($('#fx-rename').value || '').trim();
  const j = await postJSON('/api/tags',
    { subject: subj, tags: S.tags[subj] || [], note: val });
  if (j.error) { status('rename failed: ' + j.error); return; }
  S.renames[subj] = val;
  status(val ? 'named “' + val + '” — overlay only, the install is untouched'
             : 'name cleared');
  redrawList();
}

/* --------------------------------------------------------- collect / export */

/** Collect and export are the EXISTING subsystems, offered per satellite FILE
 *  because that is what they take. An effect is not a file, so the page offers
 *  its layer meshes -- and says that is what it is doing rather than implying
 *  "collect this effect" moves something the resolver never named.
 *
 *  AND THE DEFINITION IS THE POINT, NOT A FOOTNOTE.
 *  An effect's DEFINITION is a row of `3DEffect` -- the compiled `3DEffect.dbc`
 *  on 5517/6090/6609/7205, where the `.ini` beside it is a decoy that answers
 *  DIFFERENTLY for the same id. A bundle carrying only the layer meshes and
 *  textures lands the ART on the far install with the effect UNDEFINED, and a
 *  tidy list of exported files is exactly what would stop anyone noticing.
 *  Backlog section 6 names that class of omission as the trap the whole
 *  Import/Export feature exists to refuse, so this card states the answer
 *  either way and never leaves it to be inferred.
 *
 *  THE ANSWER IS THE SERVER'S, ASKED PER EFFECT, NOT COMPUTED HERE. It would
 *  be easy to decide "yes, it travels" from `d.tables` and `d.present`, which
 *  are already on this page. That would be a SECOND answer to a question
 *  `portageplan` already answers, the two would drift, and the direction of
 *  the drift is this card promising a definition the bundle does not carry.
 *  So `renderDefinition` asks `/api/portage/plan` -- the same call the pop-out
 *  makes, so what this card says and what the zip contains cannot differ --
 *  and prints UNKNOWN if the call fails rather than falling back to a plausible
 *  claim.
 */
function renderCollect(d) {
  const host = $('#fx-collect');
  host.innerHTML = '';

  /* Drawn FIRST and drawn ALWAYS, including for an effect with no exportable
   * file at all: "there is nothing to export" and "there is nothing to export
   * and the definition is not travelling either" are different answers, and
   * the old early return made them look the same. */
  const defbox = el('div', 'fx-defbox');
  defbox.appendChild(el('div', 'mut', 'checking what a bundle would carry…'));
  host.appendChild(defbox);
  renderDefinition(d, defbox);

  const tools = el('div', 'fx-toolsrow');
  const open = el('button', 'primary', 'Import/Export tools…');
  open.title = 'Every file this effect resolves to, grouped by type, the tool ' +
               'that opens each one, its place in the zip, and export/import ' +
               'of the whole bundle — including the 3DEffect definition row.';
  open.addEventListener('click', () => openPortage(d));
  tools.appendChild(open);
  tools.appendChild(el('span', 'mut',
    'The pop-out is the same one the Model Viewer and Character Builder use. ' +
    'Here the subject is the EFFECT, so the bundle is the effect: its layer ' +
    'meshes and textures as files, and its 3DEffect row as a row.'));
  host.appendChild(tools);

  const paths = [];
  for (const g of (d.geometry || [])) if (g.path && g.present) paths.push(g.path);
  for (const t of (d.textures || [])) if (t.path && t.present) paths.push(t.path);
  const uniq = Array.from(new Set(paths));
  if (!uniq.length) {
    host.appendChild(el('div', 'mut',
      'No layer of this effect resolves to a file this install ships, so ' +
      'there is no FILE to collect. That is a statement about THIS install, ' +
      'not about the effect — and it does not mean there is nothing to ' +
      'export: see the definition line above.'));
  } else {
    host.appendChild(el('div', 'mut',
      'Collect operates on FILES. These are the ' + uniq.length +
      ' file(s) this effect resolves to on this install.'));
    const ul = el('ul', 'fx-collectlist');
    for (const p of uniq) {
      const li = el('li');
      li.appendChild(el('span', 'fx-path', p));
      const b = el('button', 'ghost', 'Collect…');
      b.addEventListener('click', () => offerCollect(p, li));
      li.appendChild(b);
      ul.appendChild(li);
    }
    host.appendChild(ul);
  }

  const w = el('div', 'fx-writebox');
  w.appendChild(el('b', null, 'Write status'));
  const ws = S.writeStatus || {};
  w.appendChild(el('div', null,
    'WRITABLE, byte-exact round-trip: ' + (ws.writable || []).join(', ')));
  const ro = ws.readOnly || {};
  const roKeys = Object.keys(ro);
  if (roKeys.length) {
    w.appendChild(el('div', 'fx-ro',
      'READ-ONLY: ' + roKeys.map(k => k + ' (' + ro[k] + ')').join('; ')));
  }
  const nw = ws.noWriterNeeded || {};
  for (const k of Object.keys(nw)) {
    w.appendChild(el('div', 'mut', k + ' needs no writer — ' + nw[k]));
  }
  host.appendChild(w);
}

function tableFileFor(d, table) {
  for (const r of (d.tables || [])) if (r.table === table) return r.file;
  return null;
}

/** WHAT A BUNDLE OF THIS EFFECT WOULD ACTUALLY CARRY, from the server.
 *
 *  Three outcomes and they are kept APART, because collapsing any two of them
 *  is how this card would start lying:
 *
 *    CARRIED    the plan holds a definition row. The line names the FILE THAT
 *               ANSWERED -- `3DEffect.dbc` where a compiled twin exists -- and
 *               says what import does with it, which is MERGE into the target's
 *               own table, not overwrite it.
 *    NOT        the plan holds none, and the server's own reason is printed
 *               verbatim from the plan's `limits`. "The bundle has no
 *               definition" is a usable answer; a blank space is not.
 *    UNKNOWN    the call failed. NOT "no" -- a failed measurement and a
 *               measured absence look identical unless one of them says so,
 *               and this page's whole second rule is that they must not.
 */
async function renderDefinition(d, box) {
  const name = S.sel;
  const j = await getJSON('/api/portage/plan?effect=' +
                          encodeURIComponent(name) + '&label=' +
                          encodeURIComponent(name));
  if (S.sel !== name) return;
  box.innerHTML = '';
  if (j.error) {
    box.className = 'fx-defbox unknown';
    box.appendChild(el('b', null, 'UNKNOWN — could not check'));
    box.appendChild(el('div', 'fx-bad',
      'The export plan would not build for this effect (' + j.error + '). ' +
      'Whether a bundle would carry the 3DEffect definition row is NOT KNOWN ' +
      'here; do not read this as "it does not".'));
    return;
  }
  S.plan = j;
  const rows = [];
  for (const a of (j.assets || [])) {
    for (const g of (a.groups || [])) {
      for (const r of (g.rows || [])) if (r.definition) rows.push(r);
    }
  }
  if (j.definitionCount && rows.length) {
    box.className = 'fx-defbox carried';
    box.appendChild(el('b', null,
      'The bundle CARRIES this effect’s definition.'));
    for (const r of rows) {
      const line = el('div', 'fx-defrow');
      line.appendChild(el('span', null, r.table + ' row “' + r.rowKey + '”'));
      line.appendChild(el('span', 'fx-arrow', '←'));
      line.appendChild(el('span', 'fx-file', r.tableFile));
      line.appendChild(el('span', 'mut', '(' + r.tableForm + ')'));
      box.appendChild(line);
      if (r.note) box.appendChild(el('div', 'mut fx-note', r.note));
      if (r.exportPath) {
        box.appendChild(el('div', 'mut fx-note',
          'in the zip as ' + r.exportPath));
      }
    }
    box.appendChild(el('div', 'mut', j.definitionNote));
  } else {
    box.className = 'fx-defbox missing';
    box.appendChild(el('b', null,
      'The bundle would carry NO definition row for this effect.'));
    box.appendChild(el('div', null, j.definitionNote));
    /* The server's reason, verbatim. `build_effect_plan` files it as a limit
     * on the asset AND on the plan, so it is here rather than being
     * re-invented from what this page happens to know. */
    const why = (j.assets || []).map(a => a.note || a.error).filter(Boolean);
    for (const w of why) box.appendChild(el('div', 'fx-bad', w));
    if (!why.length) {
      box.appendChild(el('div', 'fx-bad',
        'The plan reported no reason, which is itself unexpected — treat the ' +
        'absence as unexplained, not as "there was nothing to carry".'));
    }
  }
}

/** Open the shared Import/Export pop-out on THIS effect.
 *
 *  `{effect: name}`, never `{asset: name}`. The two are different subjects on
 *  the server and there is no spelling rule that separates them: an effect
 *  called `Blood` and a file called `Blood` are not the same thing, and
 *  sending an effect down the file path yields "no asset 'Blood' in this
 *  install" -- a confident answer to a question nobody asked. */
function openPortage(d) {
  if (!window.CoPortagePanel) {
    status('the Import/Export pop-out did not load on this page');
    return;
  }
  const name = S.sel;
  window.CoPortagePanel.open({
    subjects: [{effect: name, label: name}],
    title: 'Import/Export — effect “' + name + '”',
  });
}

async function offerCollect(path, li) {
  const j = await getJSON('/api/collectoffer?path=' + encodeURIComponent(path));
  const old = li.querySelector('.fx-offer');
  if (old) old.remove();
  const box = el('div', 'fx-offer');
  if (j.error) {
    box.appendChild(el('div', 'fx-bad', j.error));
  } else {
    for (const k of (j.kinds || [])) {
      box.appendChild(el('div', 'mut',
        k.kind + ': ' + (k.present || 0) + ' present, ' +
        (k.absent || 0) + ' absent'));
    }
    if (!(j.kinds || []).length) {
      box.appendChild(el('div', 'mut', 'the offer returned no kinds'));
    }
  }
  li.appendChild(box);
}

/* ------------------------------------------------------------ honesty rail */

/** ALWAYS RENDERED. An empty rail says "nothing recorded", which is itself a
 *  statement a reader can weigh; a rail that disappears when empty teaches the
 *  reader that its absence means "complete", and it does not. */
function renderLimits(limits, unresolved, d) {
  const host = $('#fx-limits');
  host.innerHTML = '';
  const head = el('div', 'fx-limhead');
  head.appendChild(el('b', null,
    unresolved + ' satellite(s) named and NOT resolved'));
  host.appendChild(head);
  if (d && unresolved) {
    const ul = el('ul', 'fx-unres');
    for (const u of (d.unresolved || [])) {
      ul.appendChild(el('li', null, u.label + ' — ' + u.note));
    }
    host.appendChild(ul);
  }
  const all = (S.listLimits || []).concat(limits || []);
  const seen = new Set();
  const uniq = all.filter(l => !seen.has(l) && seen.add(l));
  const ul = el('ul', 'fx-limlist');
  if (!uniq.length) {
    ul.appendChild(el('li', 'mut', '(none recorded)'));
  }
  for (const l of uniq) ul.appendChild(el('li', null, l));
  host.appendChild(ul);
}

/* --------------------------------------------------- THE STAGE IS NOT HERE
 *
 * IT WAS, UNTIL 2026-09-08, AND IT WAS A SECOND ONE. This file built its own
 * `Viewer` on its own canvas, with its own clock and its own camera code,
 * beside the one `effects.js` builds on `#fx-gl`. Both were "the Model
 * Viewer's stage, verbatim"; they were two stages, and only one of them
 * worked.
 *
 * WHAT THE SECOND ONE COST, MEASURED, and it is why the deletion is recorded
 * here rather than left to `git log`. Three separate reads in the framing
 * code named a key or a method that has NEVER existed, and every one was
 * written so that its absence was a silent no-op:
 *
 *   `p.camera`               parts carry NO camera key. `effectplay.py`
 *                            collects every layer's CAME into the payload's
 *                            TOP-LEVEL `cameras` list. So the artist's camera
 *                            was never applied to any effect on any install.
 *   `p.bounds` / `b.min`     parts carry NO bounds key either. The box is
 *                            `p.geometry.bboxRender`, and it is a PAIR OF
 *                            ARRAYS, not a `{min, max}` object. Every part was
 *                            skipped and the union was always null.
 *   `viewer.frameBounds()`   is not a method `gl.js` defines, anywhere. It was
 *                            called as `if (viewer.frameBounds) { ... }`, so
 *                            the guard that was meant to make the call safe is
 *                            what made the bug invisible.
 *
 * The consequence was not a wrong camera, it was NO camera -- and the note
 * under the viewport read "fitted to bounds (ours, not the artist's)", false
 * in both halves. A guarded call to a method that does not exist and an
 * optional read of a key that does not exist PASS EVERY TEST AND EVERY
 * REVIEW: neither has a failure to observe.
 *
 * `tests/test_effect_preview.py::WhatTheCameraCodeReadsExistsInThePayload` is
 * the gate that catches that class, and it now points at ONE file because
 * there is one stage. Do not add a second here. If this half ever needs to
 * move the picture, ask `window.CoEffects`. */


/* -------------------------------------------------------------------- wiring */

/** EVERY CONTROL THIS FILE STILL BINDS IS ONE IT OWNS.
 *
 *  The search box, the form and state filters, the tagged-only checkbox and
 *  the transport are all `effects.js`'s now, and binding them here as well
 *  would give one control two handlers with two ideas about what the list is.
 *  What is left is the tag/rename panel and the compare card, both of which
 *  are this half's alone. */
function init() {
  $('#fx-tag-add').addEventListener('click', () => {
    const v = ($('#fx-tag-input').value || '').trim();
    if (!v || !S.sel) return;
    const subj = subjectFor(S.sel);
    const cur = S.tags[subj] || [];
    if (!cur.includes(v)) setTags(S.sel, cur.concat([v]));
    $('#fx-tag-input').value = '';
  });
  $('#fx-rename-save').addEventListener('click', () => {
    if (S.sel) saveRename(S.sel);
  });
  $('#fx-cmp-pick').addEventListener('click', () => {
    const p = $('#fx-cmp-picker');
    p.hidden = !p.hidden;
    if (!p.hidden) renderCmpPicker();
  });
  $('#fx-cmp-run').addEventListener('click', () => runCompare(true));
  loadCmpBases();
  /* IN PARALLEL WITH THE LIST, NOT AFTER IT. The census does not depend on
   * the list -- both come off the same install -- and `/api/fx/list` builds
   * the dependency graph, which is 7-34 s cold. Chained, the census panel sat
   * at its placeholder for that whole time on 6609, and a panel frozen on a
   * placeholder is the one state this page must never be in: it is
   * indistinguishable from a panel that is about to fill in and from one that
   * has decided there is nothing to say. Started here, it says UNMEASURED
   * within a frame and keeps saying it until it has an answer. */
  loadCensus();
  loadList();
  /* The subject column starts in whatever kind the picker starts in, which is
   * 2D. Saying so at boot rather than waiting for the first switch is what
   * stops an empty panel sitting under "Pick an effect from the list" on a
   * page whose list is 2D and whose panels only answer for 3D. */
  const E = window.CoEffects;
  setKind((E && E.state && E.state.kind) || '2d');
}

document.addEventListener('DOMContentLoaded', init);

/** THE PUBLISHED SURFACE -- the other half of the seam described at the top
 *  of this file. `effects.js` calls `select` after it has resolved and staged
 *  an effect, `setKind` when the 2D/3D switch moves, and `isTagged` once per
 *  drawn row when the tagged-only filter is on.
 *
 *  `isTagged` reads the map this file already keeps rather than handing a copy
 *  over: there is ONE tag state and it is the one the tag panel writes. A
 *  copy would be right until the first tag was added and wrong afterwards,
 *  and the list is where that would show up as a row quietly filtered out. */
window.CoFxView = {
  select, setKind, markStageBuild,
  isTagged: (name) => !!(S.tags[subjectFor(name)] || []).length,
  state: S,
};
