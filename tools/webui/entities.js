/* entities.js -- everyone else in the world, drawn.
 *
 * `play.js` owns the player: one figure, one pose, one camera to follow. This
 * owns the rest of `world.entities` -- other players, monsters and NPCs -- and
 * it is deliberately a separate file with an explicit `init()` seam rather than
 * more of `play.js`, because the two have different failure modes. If this file
 * throws, the player, the map and the UI must still be on screen.
 *
 * WHAT ARRIVES, AND FROM WHERE
 * ----------------------------
 *   /api/game/state      positions, every poll. Cheap, and the only thing that
 *                        changes while nobody equips anything.
 *   /api/game/entities    what each one IS -- the composed appearance ids, the
 *                        dye, and for a monster or an NPC a bare mesh+texture.
 *                        Asked for only when the set or an appearance changes.
 *   /api/figure           the same endpoint the player uses. One call per
 *                        distinct outfit, not one per entity: two guards in
 *                        the same armour share a payload and its textures.
 *   /api/mesh             for monsters and NPCs, which are not assemblies.
 *
 * THREE KINDS, TWO ROUTES, AND THAT IS THE CLIENT'S OWN SPLIT
 * -----------------------------------------------------------
 * A player is composed: a body from `armor.ini` with parts hung on named
 * sockets. A monster is not -- `c3/monster/900/` is one self-contained file per
 * action -- and an NPC is a `3DSimpleObj` geometry/texture pair. Forcing all
 * three through `/api/figure` fails at the body for two of them (measured:
 * `body=910000000` answers "has no resolvable mesh"), so the server tells us
 * which route each entity takes and this draws whichever it is given.
 *
 * POSE
 * ----
 * Every figure is posed by the server: `/api/figure` bakes the idle action
 * motion into the vertices it emits, because the engine never draws a bind
 * pose. So a standing remote entity is correctly posed with no per-frame work
 * at all. Facing and position are interpolated here, per entity, on the same
 * clock as the player.
 *
 * Remote figures DO play a walk cycle, from the same `/api/anim` clips the
 * player uses and through the same `setPose(.., .., pred)` seam. Doing it meant
 * making the clip schedule per entity; see "FIGURE ANIMATION" below for the
 * state that had to stop being shared and the state that safely stayed shared.
 *
 * WHAT IS PER-INSTANCE AND WHAT IS DELIBERATELY SHARED
 * ----------------------------------------------------
 * Generalising a one-figure pipeline to N figures fails in one characteristic
 * way: some piece of per-instance state is written through a shared or
 * last-write-wins path, and the result looks plausible on screen. A sibling
 * workstream hit exactly this -- one socket matrix written into every live
 * effect instance, showing up as a left-hand glow dragged onto the right hand,
 * ~144 units off mid-swing, with the API and the socket resolution underneath
 * both correct. So the split is stated here rather than left to be inferred:
 *
 *   PER INSTANCE, and it must stay that way
 *     dx/dy/angle    the drawn position and facing
 *     frame          which frame of its idle each model is on
 *     r.anim         the whole walk-cycle schedule of a FIGURE: which clip of
 *                    the chain, which frame, its own t0, and the socket
 *                    matrices that frame put its weapons on
 *     the model matrix, and every mesh's `model`/`base`, rebuilt per entity
 *     per call -- `place()` maps slot -> matrix and never reuses one
 *
 *   SHARED, on purpose, and safe because it is never written through
 *     the /api/figure and /api/meshanim PAYLOADS, cached by appearance key.
 *       Two guards in the same armour read one payload. gl.js copies the
 *       vertices into its own buffers per def and mutates nothing on `meta`.
 *     the TEXTURES, keyed by appearance rather than by uid, for the same
 *       reason -- same outfit, same pixels, one upload.
 *     the CLIPS, keyed by body+action+weaponset. A clip is chunk positions
 *       per frame -- read-only data, the same shape as a payload. Two guards
 *       in the same armour holding the same sword share one download and
 *       still walk out of step, because the SCHEDULE is per entity and the
 *       clip is not.
 *     the idle CLOCK for MODELS. Idles are not synchronised to anything, so
 *       one phase for all models is a visual choice, not shared mutable
 *       state. Figures do not use it: a walk starts when that entity starts
 *       walking, so its phase is its own.
 *
 * There are no effects on remote entities yet, which is the other place this
 * class of bug lives. `docs/playable.md` records that as OPEN.
 *
 * WHY THE POSE FILTER EXISTS AT ALL
 * ---------------------------------
 * `Viewer.setPose(positions, sockets)` keys on `meta.index`, the chunk's
 * ordinal inside its own C3 file. Two figures built from the same body have the
 * same chunk indices and the same vertex counts -- so posing the player would
 * silently pose every remote entity wearing the same armour, from the player's
 * animation. It would look like a bug in the animation, not in the scene. Hence
 * `setPose(positions, sockets, m => m.slot === '_player')`, and hence every
 * mesh this file emits carries a `slot` that starts with `ent:`.
 */

'use strict';

(function (global) {

  //: Everything `play.js` owns and this needs. Injected rather than reached
  //: for: these are `const`s in another classic script, and depending on that
  //: sharing is the kind of coupling that breaks silently when a file moves.
  let D = null;

  //: uid -> record. `art` is the server's render plan, `fig` the fetched
  //: payload, `dx/dy/angle` the DRAWN position (see play.js on why the drawn
  //: position is not the server's).
  const ents = new Map();

  //: art key -> Promise of the fetched payload. One outfit, one fetch.
  const artCache = new Map();

  //: textureKey -> url, merged into the page's own map at rebuild.
  const textures = new Map();

  let signature = '';        // the set + appearance keys we last built for
  let pending = false;
  let note = '';             // whatever the server said about limits
  let dropped = 0;
  //: The client's own screen rule -- "18 tiles in both x and y"
  //: (`refs/conquer-online-wiki/Systems/Screen.md`). The server tells us the
  //: number it used so the two halves cannot disagree; this is the value
  //: before the first answer arrives, not a second opinion.
  //:
  //: It matters because `world.entities` keeps everything it has been told
  //: about, including things left far behind on the same map. Tracking those
  //: here would put them in the panel as "not drawn" with no reason, which
  //: reads as a failure rather than as distance.
  let screenRadius = 18;

  //: How long a remote entity is drawn taking one cell, before we have seen it
  //: take two. The server tells us WHERE something is, never how fast it got
  //: there, so this is a chosen number: the player's own walk pace.
  const REMOTE_STEP_MS = 480;
  //: ...and once we HAVE seen it take two, the gap between them is better
  //: information than any constant, so the drawn pace follows it.
  //:
  //: This is not polish, it is what makes the walk cycle legible. MEASURED on
  //: `--sim`, whose neighbours step every 900 ms (`SIM_WANDER_MS`): drawn at a
  //: fixed 480 ms per cell, an entity crosses its cell, then STANDS for the
  //: remaining ~420 ms, every step. The legs then start and stop twice a
  //: second, which reads worse than not animating at all -- and the figure was
  //: not slow, it was early. Following the cadence we are actually told about
  //: spreads the same movement over the whole interval, so a continuously
  //: stepping entity walks continuously and one that genuinely stops still
  //: stops.
  //:
  //: The bounds are what keeps it a pacing choice rather than a trust
  //: exercise: a burst of queued steps cannot make it sprint, and a long
  //: silence cannot make it wade.
  const REMOTE_STEP_MIN_MS = 240;
  const REMOTE_STEP_MAX_MS = 1100;
  //: Weight on the newest observation. Low enough that one late poll does not
  //: swing the pace, high enough to follow a real change of gait.
  const STEP_EMA = 0.4;
  //: Past this, jump instead of sliding -- a respawn or a map change is not a
  //: walk, and sliding one across the map looks like a bug.
  const SNAP_CELLS = 6;

  function init(deps) {
    D = deps;
    return API;
  }

  // ---------------------------------------------------------------- syncing

  /** Fold one `/api/game/state` poll in. Returns true if anything moved.
   *
   *  Positions come from here, appearance does not: an entity that walked has
   *  new coordinates and the same outfit, and re-deciding the outfit at poll
   *  rate would be the same pointless work `ensureCharacter` avoids.
   */
  function sync(state) {
    if (!D || !state || !state.connected) return false;
    const seen = new Set();
    let moved = false;
    let statusChanged = false;
    for (const e of (state.entities || [])) {
      if (e.isSelf) continue;
      if (Math.max(Math.abs(e.x - state.x), Math.abs(e.y - state.y))
          > screenRadius) { ents.delete(e.uid); continue; }
      seen.add(e.uid);
      let r = ents.get(e.uid);
      if (!r) {
        r = { uid: e.uid, dx: e.x, dy: e.y, angle: 0, seeded: false,
              art: null, fig: null, meshes: [], movedAt: 0, anim: newAnim() };
        ents.set(e.uid, r);
      }
      if (r.x !== e.x || r.y !== e.y) { moved = true; noteStep(r, e); }
      // The status bitfield rides the POSITION poll, not the appearance
      // fetch. That is the opposite of the equipment split above and it is
      // deliberate: an outfit changes when someone equips something, a status
      // changes when someone is buffed or cursed mid-fight, and the whole
      // point of an aura is that it appears the moment the bit does.
      // Comparing an integer per entity per poll costs nothing; the scene
      // fetch behind it is still keyed on the set of DISTINCT statuses.
      const wasStatus = r.status || 0;
      const nowStatus = e.status || 0;
      if (wasStatus !== nowStatus) {
        r.status = nowStatus;
        // A NEW aura starts its own clock NOW. We are told a bit is set, never
        // when it was set, so "when we first saw it" is the honest t0 and it
        // is the only one available. What it must not be is a clock shared
        // with the other entities: see fx.js `tOffset`.
        r.auraT0 = performance.now();
        statusChanged = true;
      }
      Object.assign(r, {
        kind: e.kind, name: e.name || '', hasName: !!e.hasName,
        warnings: e.warnings || [], x: e.x, y: e.y,
        direction: e.direction, level: e.level, life: e.life,
      });
    }
    let removed = false;
    for (const uid of [...ents.keys()])
      if (!seen.has(uid)) { ents.delete(uid); removed = true; }
    if (removed) statusChanged = true;       // one fewer aura to draw

    // The appearance fetch is keyed on the SET, not on the positions. A new
    // uid or a departed one is a rebuild; a step is not.
    const sig = [...seen].sort().join(',');
    if (sig !== signature && !pending) {
      signature = sig;
      refresh();
    } else if (removed) {
      D.requestRebuild();
    }
    if (statusChanged) refreshAuras();
    return moved;
  }

  // -------------------------------------------------------- status auras
  //
  // docs/world_effects.md 4.3: `MsgPlayer`'s u64 `status`, one bit per row of
  // `ini/statuseffect.ini`, column 1 a `3DEffect.*` name drawn while the bit
  // is set. `/api/game/entityfx` resolves the bits and ships one scene per
  // distinct NAME; this turns that into one instance per (entity, name).
  //
  // WHAT IS PER INSTANCE HERE, and it is the whole point of the file's header
  // block being where it is:
  //
  //   r.status      the bitfield, per entity, from the position poll
  //   r.auraT0      when THIS entity's aura started, so two entities under
  //                 the same buff are not in lockstep (fx.js `tOffset`)
  //   r.auraAnchor  a Float32Array PER ENTITY, rewritten in `place()` from
  //                 that entity's own model matrix. One array shared between
  //                 instances is exactly the `placeSuperFx` defect: the last
  //                 entity written would drag every other entity's aura onto
  //                 itself, and it would look like a scene bug, not a
  //                 rendering one.
  //
  // SHARED, on purpose and safe because nothing writes through it:
  //
  //   auraScenes    name -> the /api/game/entityfx payload. Eight players
  //                 under one buff read one scene, the way two guards in one
  //                 armour read one figure payload. `fx.js` copies geometry
  //                 into its own GL buffers per instance and mutates nothing
  //                 on the payload.
  //   auraTextures  name+layer -> url, for the same reason.
  //
  // THE ANCHOR IS THE BODY, NOT A SOCKET, AND THAT IS INFERRED. A weapon aura
  // rides `v_r_weapon` because `Action3DEffect` names an anchor; a STATUS row
  // names only an effect. Riding the entity's model matrix is the reading
  // that matches "a persistent aura while the bit is set" and it is labelled
  // as inferred in the payload rather than presented as recovered.

  //: name -> effect scene payload. Shared, read-only.
  const auraScenes = new Map();
  //: The (uid:status) set the scenes were fetched for. A re-fetch is a change
  //: of WHO is buffed with WHAT, not a step and not a poll.
  let auraSig = '';
  let auraPending = false;
  //: Why there is nothing on screen, when there is nothing -- straight from
  //: the server. "Nobody is buffed" and "this install ships no art for the
  //: bit that is set" are different facts and the panel must not merge them.
  let auraNote = { unresolved: [], selfStatusSource: '', note: '' };

  const auraKey = (name, layer) => 'entfx:' + name + ':' + layer;

  /** Fetch the scenes for whatever is buffed now. Keyed on (uid,status)
   *  pairs, so a buff appearing, expiring or walking off screen re-fetches
   *  and a walk does not. */
  function refreshAuras() {
    if (!D) return Promise.resolve();
    const sig = [...ents.values()]
      .filter(r => r.status)
      .map(r => r.uid + ':' + r.status).sort().join(',');
    if (sig === auraSig || auraPending) return Promise.resolve();
    auraSig = sig;
    if (!sig) {                       // nobody is buffed: drop the scenes
      auraScenes.clear();
      D.requestEffects();
      return Promise.resolve();
    }
    auraPending = true;
    return D.api('/api/game/entityfx').then(payload => {
      auraScenes.clear();
      for (const [name, def] of Object.entries(payload.effects || {})) {
        auraScenes.set(name, def);
        for (const lay of def.layers || [])
          if (lay.texture)
            textures.set(auraKey(name, lay.index), texUrl(lay.texture));
      }
      for (const row of (payload.entities || [])) {
        const r = ents.get(row.uid);
        if (!r) continue;
        r.auraNames = row.names || [];
        r.auraBits = row.auras || [];
      }
      for (const r of ents.values())
        if (!r.status) { r.auraNames = []; r.auraBits = []; }
      auraNote = {
        unresolved: payload.unresolved || [],
        selfStatusSource: payload.selfStatusSource || '',
        note: payload.note || '',
      };
    }).catch(() => {
      // A failed fetch must not leave last screen's auras hanging off this
      // screen's entities. Empty and say nothing, rather than draw a stale
      // buff on somebody who no longer has it.
      auraScenes.clear();
      for (const r of ents.values()) { r.auraNames = []; r.auraBits = []; }
    }).then(() => {
      auraPending = false;
      D.requestEffects();
    });
  }

  /** Effect-instance defs for every entity aura, for the page to compose with
   *  the map's ambient set. One `setEffects` owns the whole channel.
   *
   *  The anchor handed over here is the entity's OWN array, and `place()`
   *  keeps writing into that same array every frame -- so the instance and
   *  this file are pointing at one buffer per entity, never one between them.
   */
  function auraDefs(now) {
    const out = [];
    if (!D) return out;
    for (const r of ents.values()) {
      if (!r.status || !(r.auraNames || []).length) continue;
      if (!r.seeded || !(r.fig || r.model)) continue;   // nothing to hang it on
      if (!r.auraAnchor) r.auraAnchor = new Float32Array(16);
      r.auraAnchor.set(modelFor(r));
      for (const name of r.auraNames) {
        const def = auraScenes.get(name);
        if (!def) continue;
        const keys = {};
        for (const lay of def.layers || []) {
          if (!lay.texture) continue;
          keys[lay.index] = auraKey(name, lay.index);
          // Re-registered here and not only at fetch: `defs()` clears this
          // map on every rebuild, and an effect layer whose texture key has
          // no url uploads nothing and renders pure white -- the same failure
          // `applyTextures` exists for, arriving through the other channel.
          textures.set(keys[lay.index], texUrl(lay.texture));
        }
        out.push({
          def, role: 'entaura', slot: 'ent:' + r.uid + ':' + name,
          anchor: r.auraAnchor, textureKeys: keys,
          // Its own phase, measured from when we first saw the bit.
          //
          // THE SIGN IS THE WHOLE OF IT. `setEffectTime` ticks every instance
          // with `ms = now - clockBase` and `fx.js` plays `ms - tOffset`, so
          // to make an instance play "time since ITS start" the offset must be
          // `auraT0 - clockBase`, NOT the age of the aura. `now` is the clock
          // base, because `play.js::fxAttach` restarts the clock at exactly
          // this moment (it must: `setEffects` disposes every instance, so
          // nothing survives an attach to keep an older base honest).
          //
          // Written the other way round first, and it is worth naming because
          // it is invisible in a still frame: an aura seen 62 s ago got
          // `tOffset = +62531`, so it played a NEGATIVE time for the next
          // minute and sat frozen on frame 0. On screen that is a buff that
          // renders but does not animate -- which reads as a slow effect, or
          // as art with a long fade-in, and not as a clock bug.
          tOffset: (r.auraT0 || now || 0) - (now || 0),
        });
      }
    }
    return out;
  }

  /** The live anchor for one aura instance, by the `slot` it was given.
   *  Handed to `setEffectTime` so a buffed entity's glow walks with it. */
  function auraAnchorFor(inst) {
    if (!inst || inst.role !== 'entaura') return null;
    const uid = +String(inst.slot || '').split(':')[1];
    const r = ents.get(uid);
    return (r && r.auraAnchor) || null;
  }

  /** What the panel says about auras. Never a bare absence. */
  function auraStatus() {
    const rows = [];
    for (const r of ents.values())
      if (r.status)
        rows.push({ uid: r.uid, name: r.name || null, status: r.status,
                    drawn: (r.auraNames || []).length,
                    bits: r.auraBits || [] });
    return { entities: rows, scenes: [...auraScenes.keys()],
             unresolved: auraNote.unresolved,
             selfStatusSource: auraNote.selfStatusSource,
             note: auraNote.note };
  }

  // -------------------------------------------------------- weapon glows
  //
  // THE SECOND MECHANISM, AND IT IS NOT THE ONE ABOVE. `docs/playable.md`:
  // status auras, weapon glows and hit sparks "were one line in this list and
  // are three jobs". The three differ in every axis that matters here:
  //
  //              trigger              table                anchor
  //   aura       what was DONE to you statuseffect.ini     the BODY (inferred)
  //   glow       what you are WEARING Action3DEffect       a [Dumy] SOCKET
  //   spark      what just HAPPENED   magictype.json       the TARGET
  //
  // So a glow is keyed on (entity, HAND) rather than on (entity, name): the
  // same blade in two hands is two instances riding two different sockets, and
  // that is the exact defect `builder.js::placeSuperFx` was corrected for --
  // one socket matrix written into every live instance, showing up as the left
  // glow dragged onto the right hand. Here that would be one array per entity
  // instead of one per hand, so the anchors live in a per-entity Map keyed by
  // slot and nothing is ever shared between hands.
  //
  // THE ANCHOR IS `model x socket`, AND THE SOCKET IS THE ONE THE WEAPON IS
  // ACTUALLY ON THIS FRAME. `place()` already composes exactly that product
  // for the weapon MESH -- `r.anim.sockets[dumy]` while walking, the part's
  // idle `local` otherwise -- so the glow rides the same expression rather than
  // a second convention. `fx.js` applies the effect's own `offsetRender` on
  // top; the server therefore ships the socket NAME, not a baked matrix.
  //
  // WHAT THE SERVER SHIPS, and the third state is the one that matters:
  // `found` draws; `no-effect` means the table has no always-on row for that
  // appearance, which is the ORDINARY answer and is a row on screen rather
  // than an omission; `unresolved` means the table named one and our scene
  // builder could not build it. A silently-absent glow and a correctly-absent
  // glow must not look alike.

  //: OFF switch, so the three mechanisms can be measured apart. `?entglow=0`
  //: turns weapon glows off and leaves status auras and the map's ambient set
  //: exactly as they were -- which is what makes "these are three jobs"
  //: checkable rather than asserted.
  const GLOW_OFF = new URLSearchParams(location.search).get('entglow') === '0';

  //: name -> effect scene payload. Shared, read-only, same contract as
  //: `auraScenes`: eight guards with one blade read one scene.
  const glowScenes = new Map();
  //: The (uid:slot=appearance) set the scenes were fetched for. A re-fetch is
  //: a change of WHO is holding WHAT, not a step and not a poll.
  let glowSig = '';
  let glowPending = false;
  let glowNote = { unresolved: [], selfSource: '', note: '', slots: [] };

  const glowKey = (name, layer) => 'entglow:' + name + ':' + layer;

  /** The signature of who is holding what, from the appearance plan. */
  function glowSignature() {
    const parts = [];
    for (const r of ents.values()) {
      const s = (r.art && r.art.slots) || {};
      for (const slot of ['r_weapon', 'l_weapon'])
        if (s[slot]) parts.push(r.uid + ':' + slot + '=' + s[slot]);
    }
    return parts.sort().join(',');
  }

  /** Fetch the scenes for whatever is equipped now.
   *
   *  Driven off the APPEARANCE plan, not the position poll -- the opposite of
   *  `refreshAuras`, and deliberately: a glow changes when somebody equips
   *  something, which is exactly when `refresh()` has already re-asked what
   *  everyone is wearing. A status changes mid-fight and rides the poll.
   */
  function refreshGlows() {
    if (!D || GLOW_OFF) return Promise.resolve();
    const sig = glowSignature();
    if (sig === glowSig || glowPending) return Promise.resolve();
    glowSig = sig;
    if (!sig) {                       // nobody is holding anything
      glowScenes.clear();
      for (const r of ents.values()) r.glows = [];
      D.requestEffects();
      return Promise.resolve();
    }
    glowPending = true;
    return D.api('/api/game/entityglow').then(payload => {
      glowScenes.clear();
      for (const [name, def] of Object.entries(payload.effects || {})) {
        glowScenes.set(name, def);
        for (const lay of def.layers || [])
          if (lay.texture) textures.set(glowKey(name, lay.index),
                                        texUrl(lay.texture));
      }
      const seen = new Set();
      for (const row of (payload.entities || [])) {
        const r = ents.get(row.uid);
        if (!r) continue;
        seen.add(row.uid);
        r.glows = row.weapons || [];
        // Its own phase, per HAND, from when we first saw that hand holding
        // that effect. Same reasoning as `auraT0` and the same sign trap --
        // see `glowDefs`. Two entities that equipped a Super blade a minute
        // apart must not be in lockstep.
        if (!r.glowT0) r.glowT0 = {};
        for (const w of r.glows) {
          const k = w.slot + '=' + (w.name || '');
          if (!(k in r.glowT0)) r.glowT0[k] = performance.now();
        }
      }
      for (const r of ents.values()) if (!seen.has(r.uid)) r.glows = [];
      glowNote = {
        unresolved: payload.unresolved || [],
        selfSource: payload.selfSource || '',
        note: payload.note || '',
        slots: payload.slots || [],
      };
    }).catch(() => {
      // A failed fetch must not leave last screen's glows hanging off this
      // screen's weapons -- the same rule `refreshAuras` follows.
      glowScenes.clear();
      for (const r of ents.values()) r.glows = [];
    }).then(() => {
      glowPending = false;
      D.requestEffects();
    });
  }

  /** The socket matrix this entity's `dumy` is on RIGHT NOW, or null.
   *
   *  A walking figure's socket comes from the clip frame it is on; a standing
   *  one's from the part it was built with. Exactly what `place()` uses for
   *  the weapon mesh, so the glow and the weapon cannot disagree about where
   *  the hand is -- which is the whole complaint the socket work was opened
   *  for ("~144 units off mid-swing").
   */
  function socketMatrixFor(r, dumy) {
    if (!dumy) return null;
    const live = r.anim && r.anim.playing && r.anim.sockets
                 && r.anim.sockets[dumy];
    if (live && live.length === 16) return live;
    const f = r.fig && r.fig.figure;
    for (const p of ((f && f.parts) || []))
      if (p.socket === dumy && p.matrix && p.matrix.length === 16)
        return p.matrix;
    return null;
  }

  /** Effect-instance defs for every weapon glow, for the page to compose with
   *  the map's set and the status auras. One `setEffects` owns the channel.
   *
   *  ONE ANCHOR PER (ENTITY, HAND), never one per entity. Two glowing hands on
   *  one figure are two sockets and the arrays must not be the same object.
   */
  function glowDefs(now) {
    const out = [];
    if (!D || GLOW_OFF) return out;
    for (const r of ents.values()) {
      if (!(r.glows || []).length) continue;
      if (!r.seeded || !r.fig) continue;   // only a composed figure has sockets
      const model = modelFor(r);
      if (!r.glowAnchors) r.glowAnchors = new Map();
      for (const w of r.glows) {
        if (w.state !== 'found' || !w.name) continue;
        const def = glowScenes.get(w.name);
        if (!def) continue;
        const sm = socketMatrixFor(r, w.socket);
        // No socket on this body is NOT a silent skip: `attach.dumy_names()`
        // raises rather than guessing since 299d9b2, and the server has
        // already said the table names one. Report it and draw nothing --
        // `glowStatus` surfaces it as `no-socket`.
        if (!sm) { w.placed = false; continue; }
        w.placed = true;
        let a = r.glowAnchors.get(w.slot);
        if (!a) { a = new Float32Array(16); r.glowAnchors.set(w.slot, a); }
        a.set(D.mul(model, new Float32Array(sm)));
        const keys = {};
        for (const lay of def.layers || []) {
          if (!lay.texture) continue;
          keys[lay.index] = glowKey(w.name, lay.index);
          textures.set(keys[lay.index], texUrl(lay.texture));
        }
        const t0 = (r.glowT0 || {})[w.slot + '=' + w.name];
        out.push({
          def, role: 'entglow',
          // uid AND hand. `role` alone cannot tell two hands apart and
          // `uid` alone cannot either -- fx.js says exactly this about
          // `slot`, and it is why the anchor lookup below can be per hand.
          slot: 'ent:' + r.uid + ':' + w.slot,
          anchor: a, textureKeys: keys,
          // The sign is the whole of it -- see `auraDefs`. `tOffset` must be
          // (start - clockBase), not the age; written the other way round an
          // effect first seen a minute ago plays a NEGATIVE time and sits
          // frozen on frame 0, which reads as slow art rather than as a bug.
          tOffset: (t0 || now || 0) - (now || 0),
        });
      }
    }
    return out;
  }

  /** The live anchor for one glow instance, by the `slot` it was given.
   *  Per (uid, hand) -- one matrix for every instance is the defect. */
  function glowAnchorFor(inst) {
    if (!inst || inst.role !== 'entglow') return null;
    const bits = String(inst.slot || '').split(':');
    const r = ents.get(+bits[1]);
    return (r && r.glowAnchors && r.glowAnchors.get(bits[2])) || null;
  }

  /** What the panel says about weapon glows. Never a bare absence: every hand
   *  holding anything is a row, including the hands that correctly do not
   *  glow, with the reason the server gave. */
  function glowStatus() {
    const rows = [];
    for (const r of ents.values())
      for (const w of (r.glows || []))
        rows.push({
          uid: r.uid, name: r.name || null, slot: w.slot,
          appearance: w.appearance, socket: w.socket,
          effect: w.name || null, state: w.state,
          why: w.why || '',
          // Resolved by the TABLE and placed on a SOCKET are two different
          // successes. A found effect on a body with no such dummy draws
          // nothing, and that must not read as "no effect".
          drawn: w.state === 'found' && w.placed !== false,
          placed: w.placed !== false,
        });
    rows.sort((a, b) => (a.uid - b.uid) || a.slot.localeCompare(b.slot));
    return { rows, scenes: [...glowScenes.keys()], off: GLOW_OFF,
             unresolved: glowNote.unresolved, selfSource: glowNote.selfSource,
             note: glowNote.note };
  }

  /** Learn one entity's step cadence from the gap between two confirmed cells.
   *
   *  Per cell, not per report: a poll that arrives late carries a two-cell move
   *  and dividing by the distance is what stops that being read as one very
   *  slow step. `r.x`/`r.y` are still the PREVIOUS confirmed cell here -- this
   *  runs before the assign, deliberately.
   */
  function noteStep(r, e) {
    const now = performance.now();
    const cells = Math.max(1, Math.max(Math.abs(e.x - r.x), Math.abs(e.y - r.y)));
    if (r.lastStepAt && cells <= SNAP_CELLS) {
      const per = (now - r.lastStepAt) / cells;
      r.stepMs = r.stepMs ? (r.stepMs * (1 - STEP_EMA) + per * STEP_EMA) : per;
    }
    r.lastStepAt = now;
  }

  //: The pace to draw this entity at: what it has been observed doing, held
  //: inside bounds, and the chosen default until it has been observed at all.
  function stepMsFor(r) {
    return Math.max(REMOTE_STEP_MIN_MS,
                    Math.min(REMOTE_STEP_MAX_MS, r.stepMs || REMOTE_STEP_MS));
  }

  /** Ask what everyone looks like, then fetch each distinct outfit once. */
  function refresh() {
    if (!D) return Promise.resolve();
    pending = true;
    return D.api('/api/game/entities').then(payload => {
      note = payload.note || '';
      dropped = payload.dropped || 0;
      if (payload.screenRadius) screenRadius = payload.screenRadius;
      const wants = [];
      for (const row of (payload.entities || [])) {
        const r = ents.get(row.uid);
        if (!r) continue;
        // A new outfit is a new clip key (the weaponset is part of it), so a
        // stride scheduled against the old one must not carry over: it would
        // keep posing chunks that belong to a body this entity no longer has.
        const wasKey = r.art && r.art.key;
        if (wasKey !== ((row.art || {}).key || null)) r.anim = newAnim();
        r.art = row.art || null;
        r.name = row.name || '';
        r.hasName = !!row.hasName;
        r.warnings = row.warnings || [];
        if (r.art && r.art.drawable) wants.push(fetchArt(r));
      }
      // Anything the server did not describe cannot be drawn, and saying so
      // beats leaving a stale figure standing where an entity used to be.
      for (const r of ents.values())
        if (!r.art) { r.fig = null; r.model = null; }
      return Promise.all(wants);
    }).then(() => {
      // A glow is a property of the OUTFIT, so this is where it is re-asked --
      // the appearance plan has just landed and `glowSignature()` can read it.
      // Deliberately not on the position poll: that is where a STATUS aura is
      // re-asked, because a status changes mid-fight and equipment does not.
      // Not awaited: a figure must draw whether or not its glow resolves.
      refreshGlows();
      D.requestRebuild();
    }).catch(err => {
      // A failure here must not take the world down with it. The entities are
      // still listed in the sidebar; they are simply not drawn, and the reason
      // is on the console rather than nowhere.
      console.warn('[entities] appearance fetch failed:', err);
    }).finally(() => { pending = false; });
  }

  function fetchArt(r) {
    const art = r.art;
    const key = art.key;
    if (!artCache.has(key)) artCache.set(key, load(art));
    return artCache.get(key).then(payload => {
      if (payload && payload.kind === 'figure') { r.fig = payload; r.model = null; }
      else { r.model = payload; r.fig = null; }
    }).catch(err => {
      console.warn('[entities]', key, 'did not resolve:', err);
      r.fig = r.model = null;
    });
  }

  function load(art) {
    if (art.how === 'figure') {
      const qs = new URLSearchParams({ body: art.body });
      for (const [slot, id] of Object.entries(art.slots || {})) qs.set(slot, id);
      return D.api('/api/figure?' + qs.toString())
        .then(f => ({ kind: 'figure', key: art.key, figure: f,
                      // The dye. `/api/figure` answers with the appearance
                      // row's authored Texture0; the spawn packet's colour
                      // selects a different texture for the SAME mesh, which
                      // is what `ItemTexture.ini` is for. Overriding here
                      // rather than teaching the shared endpoint a colour
                      // argument keeps the viewer's contract untouched.
                      textures: art.textures || {} }));
    }
    const m = art.model || {};
    if (!m.mesh) return Promise.resolve(null);
    // POSE IT WITH ITS OWN MOTION, not with its bind pose.
    //
    // `/api/meshanim` returns a scene already posed at frame 0 plus the rest
    // of the cycle. That is not a nicety: an NPC's geometry and its standby
    // motion are separate files (`core/npcart.py`), and drawn without the
    // motion the Storekeeper family renders as a thin vertical sliver rather
    // than a person -- MEASURED, and it is what `/api/mesh` alone produced
    // here. `docs/attachment.md` §8.2 makes the same point for characters:
    // the engine never draws a bind pose.
    //
    // A monster's action file carries its own MOTI, so the same call serves
    // both with `motion` pointing at the geometry.
    const q = '/api/meshanim?path=' + encodeURIComponent(m.mesh)
      + (m.motion ? '&motion=' + encodeURIComponent(m.motion) : '');
    return D.api(q).then(clip => {
      if (clip && clip.scene && clip.scene.meshes && clip.scene.meshes.length)
        return { kind: 'model', key: art.key, scene: clip.scene, clip,
                 texture: m.texture || '' };
      throw new Error(clip && clip.error || 'no posed scene');
    }).catch(() => D.api('/api/mesh?path=' + encodeURIComponent(m.mesh))
      .then(scene => ({ kind: 'model', key: art.key, scene, clip: null,
                        texture: m.texture || '',
                        note: 'drawn at its bind pose: no motion resolved' })));
  }

  // ---------------------------------------------------------------- drawing

  /** How big to draw this entity, relative to its authored size.
   *
   *  1 for a player and an NPC. For a monster the server answers from the
   *  install's own monster table, joined on the SPAWN PACKET'S NAME -- see
   *  `coplay.EntityArt.monster_rows` for why the name is the join and the art
   *  is not, and for what that does and does not establish. A monster whose
   *  name does not resolve comes back at 1 **with a reason attached**, which
   *  the panel shows: "the table says 100 %" and "we could not ask" are the
   *  same number and not the same claim.
   */
  function scaleFor(r) {
    const s = r.art && r.art.scale;
    return (typeof s === 'number' && s > 0) ? s : 1;
  }

  /** The model matrix for one entity at its DRAWN position. */
  function modelFor(r) {
    const [wx, wy] = D.worldXY(r.dx + 0.5, r.dy + 0.5);
    const wz = D.elevationAt(Math.round(r.dx), Math.round(r.dy));
    return D.mul(D.mul(D.translation(wx, wy, wz), D.rotationZ(r.angle)),
                 D.scaling(D.figureScale * scaleFor(r)));
  }

  /** Render defs for everything drawable, to concat into the page's list.
   *
   *  Also (re)fills `textures`, because the texture set is decided by exactly
   *  the same walk -- keeping them in one function is what stops a figure
   *  being drawn with no texture, which renders pure white rather than absent.
   */
  function defs() {
    textures.clear();
    const out = [];
    if (!D) return out;
    for (const r of ents.values()) {
      const model = modelFor(r);
      const tag = 'ent:' + r.uid;
      if (r.fig) {
        const f = r.fig.figure;
        const key = r.fig.key;
        const dyed = r.fig.textures || {};
        if (f.body && f.body.scene) {
          const tk = 'art:' + key + ':body';
          textures.set(tk, texUrl(dyed.body || f.body.texture));
          for (const m of f.body.scene.meshes)
            out.push({ meta: m, textureKey: tk, matrix: model, slot: tag });
        }
        for (const p of (f.parts || [])) {
          const local = p.matrix && p.matrix.length === 16
            ? new Float32Array(p.matrix)
            : D.translation(...(p.offset || [0, 0, 0]));
          const tk = 'art:' + key + ':slot:' + p.slot;
          if (p.texture || dyed[p.slot])
            textures.set(tk, texUrl(dyed[p.slot] || p.texture));
          for (const m of (p.scene ? p.scene.meshes : []))
            out.push({ meta: m, textureKey: tk, matrix: D.mul(model, local),
                       base: model, local, slot: tag + ':' + p.slot,
                       socket: p.socket });
        }
      } else if (r.model && r.model.scene) {
        const key = r.model.key;
        const tk = 'art:' + key + ':model';
        if (r.model.texture) textures.set(tk, texUrl(r.model.texture));
        for (const m of (r.model.scene.meshes || []))
          out.push({ meta: m, textureKey: tk, matrix: model, slot: tag });
      }
    }
    return out;
  }

  const texUrl = p => '/api/texture?path=' + encodeURIComponent(p);

  /** Where to put a ground shadow, per entity, at its DRAWN position.
   *
   *  Positions rather than draw calls, because the shadow is the map's art and
   *  the map's blitter owns it: `play.js` already holds the `baker` and the
   *  ground hook, and handing this file a GL context to paint into would make
   *  it a second renderer -- which is the one thing it is not (see the header).
   *
   *  ONLY FOR SOMETHING THAT IS ACTUALLY DRAWN. A shadow under an entity whose
   *  art did not resolve is a stain on the floor with nothing above it, and the
   *  panel already says that entity is "not drawn" for a reason.
   *
   *  Continuous coordinates, like the player's: the shadow slides with the walk
   *  instead of hopping a cell at a time under a figure that is between cells.
   */
  function shadows() {
    const out = [];
    if (!D) return out;
    for (const r of ents.values())
      if (r.seeded && (r.fig || r.model))
        // A 3.5x monster casts a 3.5x shadow. The shadow art is authored at
        // one cell for a figure, so anything that scales the body has to scale
        // what it stands on or the two stop agreeing about where the feet are.
        out.push({ uid: r.uid, x: r.dx, y: r.dy,
                   scale: D.figureScale * scaleFor(r) });
    return out;
  }

  /** Merge this file's textures into the page's map. Called at rebuild. */
  function applyTextures(map) {
    for (const [k, v] of textures) map.set(k, v);
  }

  /** Re-point every entity's meshes at its current drawn position.
   *
   *  The same trick `placeAvatar` uses: mutate `model` (and `base`, which
   *  `setPose` composes socket matrices onto) rather than rebuilding the
   *  scene, because `setMeshes` tears down and re-uploads every buffer.
   */
  function place() {
    if (!D || !D.viewer) return;
    const models = new Map();
    const poses = new Map();
    for (const r of ents.values()) {
      const model = modelFor(r);
      models.set('ent:' + r.uid, model);
      // Its aura rides its body, so the anchor moves here, on the WALK clock.
      //
      // `EffectInstance` COPIES the anchor it is given (`Float32Array.from`),
      // so this array is not shared with the instance and writing it is not
      // enough on its own -- measured, by perturbing one instance's anchor and
      // watching no other move. Two things therefore keep them in step, and
      // both are needed for the same reason `builder.js::placeSuperFx` needs
      // both: the effect timer pushes this array in through `auraAnchorFor`
      // on every tick, and the tick is 41 ms while the walk is 60 Hz -- so a
      // glow that only tracked the timer would sit up to two frames behind
      // the body, and one that only tracked here would freeze whenever the
      // frame loop slept. This is the authoritative value; the push is what
      // makes it visible between ticks.
      if (r.auraAnchor) r.auraAnchor.set(model);
      if (r.anim && r.anim.playing && r.anim.sockets)
        poses.set('ent:' + r.uid, r.anim.sockets);
      // A weapon glow rides the SOCKET, not the body, so it needs the same
      // model x socket product the weapon mesh gets below -- and it needs it
      // here, on the 60 Hz walk clock, for the same reason the aura does: the
      // effect timer ticks at 41 ms and a glow that only tracked the timer
      // would sit up to two frames behind the hand mid-swing. Per HAND: one
      // array per entity would drag the left glow onto the right, which is
      // `placeSuperFx`'s original defect arriving through this door instead.
      if (r.glowAnchors && r.glowAnchors.size) {
        for (const w of (r.glows || [])) {
          const a = r.glowAnchors.get(w.slot);
          if (!a) continue;
          const sm = socketMatrixFor(r, w.socket);
          if (sm) a.set(D.mul(model, new Float32Array(sm)));
        }
      }
    }
    for (const m of D.viewer.meshes) {
      if (!m.slot || m.slot.indexOf('ent:') !== 0) continue;
      const tag = m.slot.split(':').slice(0, 2).join(':');
      const base = models.get(tag);
      if (!base) continue;
      if (m.local) {
        m.base = base;
        // A part on a WALKING figure rides the clip's socket matrix, not the
        // one it was built with. `local` is the idle socket; re-placing with
        // it would drag the sword back to the standing pose on every frame the
        // position moved and the clip did not -- which at 41 ms per clip frame
        // and 60 Hz is three frames in four, and reads as a weapon that
        // vibrates while its owner walks. `setPose` composes the same product
        // onto `base`, so this is that product and not a second convention.
        const sk = poses.get(tag);
        const sm = sk && m.socket && sk[m.socket];
        m.model = D.mul(base, (sm && sm.length === 16)
                              ? new Float32Array(sm) : m.local);
      } else m.model = base;
    }
    // Straight into the live instances too, for the frames the effect timer
    // does not get to: it ticks at 41 ms and this runs at 60 Hz, and an aura
    // that tracks its owner only on the ticks they happen to share is the
    // same half-fix `placeSuperFx` was corrected for. Per instance, from that
    // instance's OWN uid -- one matrix written into every live instance is
    // the defect itself, arriving through a different door.
    for (const f of (D.viewer.fx || [])) {
      if (!f) continue;
      if (f.role === 'entaura') {
        const m = models.get('ent:' + String(f.slot || '').split(':')[1]);
        if (m && f.anchor && f.anchor.length === 16) f.anchor.set(m);
      } else if (f.role === 'entglow') {
        // Its own hand's anchor, which `glowAnchors` has just rewritten above.
        const a = glowAnchorFor(f);
        if (a && f.anchor && f.anchor.length === 16) f.anchor.set(a);
      }
    }
  }

  // ------------------------------------------------------------- animation
  //
  // Only the MODEL route animates, and that is an honest asymmetry rather
  // than an oversight. A monster or an NPC is one mesh with one motion, so a
  // frame is one `bufferSubData` per chunk and the clip is already in hand
  // from the same call that fetched the geometry. A player-shaped figure
  // needs its clip chosen per action from `3dmotion.ini`, its sockets
  // re-composed onto a moving base, and a per-entity clock to run it -- the
  // mechanism exists (`setPose`'s `pred`) and the scheduling does not.
  // `docs/playable.md` grades that OPEN rather than half-doing it.

  //: One clock for every model. They are idling, not synchronised to
  //: anything, so a shared phase costs nothing and one timer beats N.
  let clock = 0;
  const MODEL_FRAME_MS = 60;

  /** Whether anything on screen is mid-animation -- a model idling or a figure
   *  walking.
   *
   *  Separate from `stepModels`/`stepFigures`'s return value on purpose. Those
   *  say "the pose changed *this frame*", which at a 41-60 ms frame interval is
   *  false on three frames out of four at 60 Hz -- using one to decide whether
   *  to schedule the next rAF would stop the loop between frames of a running
   *  animation and leave the NPCs twitching once per poll. */
  function animating() {
    for (const r of ents.values())
      if (r.model && r.model.clip && r.model.clip.frames > 1) return true;
    return figuresWalking();
  }

  function stepModels(dtMs) {
    if (!D || !D.viewer || !animating()) return false;
    clock += dtMs;
    let posed = false;
    for (const r of ents.values()) {
      const clip = r.model && r.model.clip;
      if (!clip || !clip.frames || clip.frames < 2) continue;
      const per = clip.frameIntervalMs || MODEL_FRAME_MS;
      const f = Math.floor(clock / per) % clip.frames;
      if (r.frame === f) continue;
      r.frame = f;
      const positions = {};
      for (const ch of (clip.chunks || [])) positions[ch.index] = ch.frames[f];
      const tag = 'ent:' + r.uid;
      D.viewer.setPose(positions, {}, m => m.slot === tag);
      posed = true;
    }
    return posed;
  }

  // ------------------------------------------------------ figure animation
  //
  // The player's stride, per entity. `play.js` already had every piece of the
  // mechanism -- `/api/anim` returns a clip and whatever it chains to,
  // `poseFrame(clip, f)` and `stepAnim(now)` both take their clip explicitly,
  // and `setPose` takes a `pred` so one figure's stride cannot land on
  // another's meshes. The only thing in the way was that the SCHEDULE lived in
  // one module-level `anim` object with one `seq`, one `frame` and one clock.
  // Here it is a record per entity, and nothing else about the mechanism
  // changed.
  //
  // WHAT DECIDES THE GAIT, and it is a choice rather than a reading. The wire
  // tells us where a remote entity IS and never how fast it got there --
  // `MsgWalk`/`MsgPlayer` carry no run flag for someone else -- so there is
  // no honest way to pick run over walk from the packet. We draw remote
  // entities crossing a cell in `REMOTE_STEP_MS` (480 ms, the player's WALK
  // pace), so the walk clip is the one that matches the ground speed we chose.
  // Picking `run` here would make the legs disagree with the feet.
  //
  // THE WEAPONSET IS PART OF THE KEY. `3dmotion.ini` resolves an action
  // through the equipped weapon (`client/appearance.py` says so where it
  // composes the slots), so an entity holding a sword and one holding nothing
  // are two different clips for the same body and the same action.

  const ACT_IDLE = '100';                    // stand
  const ACT_WALK = '110';                    // walk

  //: Fallback frame interval when a clip does not report one. `/api/anim`'s
  //: own default is 41 ms and it says plainly that the rate is not recoverable
  //: from the motion data, so this is only ever reached when the clip is
  //: silent.
  const FIGURE_FRAME_MS = 41;

  //: Keep the stride alive this long after the drawn position stops moving.
  //:
  //: Not smoothing for its own sake: a remote entity's position arrives on the
  //: poll (500 ms idle / 120 ms moving) and is drawn across `REMOTE_STEP_MS`
  //: (480 ms), so a *continuously* walking entity still lands on its cell a
  //: few milliseconds before the next one is confirmed. Without a hold, the
  //: legs stop and restart in that gap, once per cell, which reads worse than
  //: either state. Deliberately much shorter than one cell: an entity that has
  //: genuinely stopped stops.
  const WALK_HOLD_MS = 140;

  function newAnim() {
    return { seq: [], at: 0, frame: -1, t0: 0, playing: '', wanted: '',
             lastClip: null,
             //: A fetch is in flight / this outfit has no walk in this
             //: install. Both exist because the gait is decided every frame:
             //: see `startWalk`.
             fetching: false, absent: false,
             //: The socket matrices the current frame put the parts on.
             //: `place()` needs them -- see there.
             sockets: null };
  }

  //: `body|action|weapon|offHand` -> Promise of the chained clips.
  const clipCache = new Map();

  function clipKey(art, action) {
    const s = art.slots || {};
    return [art.body, action, s.r_weapon || '', s.l_weapon || ''].join('|');
  }

  /** The clips for one action and whatever it chains to, or `[]`.
   *
   *  `[]` is an ordinary outcome, not an error: 1,100 of the 3,260 motions
   *  `3dmotion.ini` names are absent from a given install, and an entity whose
   *  walk does not ship keeps its server-baked idle pose rather than falling
   *  to a bind pose. */
  function loadSequence(art, action) {
    const key = clipKey(art, action);
    if (!clipCache.has(key)) clipCache.set(key, chaseClips(art, action));
    return clipCache.get(key);
  }

  async function chaseClips(art, action) {
    const seq = [];
    const seen = new Set();
    const s = art.slots || {};
    let next = action;
    while (next && !seen.has(next)) {
      seen.add(next);
      const q = new URLSearchParams({ body: art.body, action: next });
      if (s.r_weapon) q.set('weapon', s.r_weapon);
      if (s.l_weapon) q.set('offHand', s.l_weapon);
      let c;
      try { c = await D.api('/api/anim?' + q.toString()); } catch (e) { break; }
      if (!c || !c.frames) break;
      seq.push(c);
      next = c.chain;
    }
    return seq;
  }

  //: Every mesh belonging to one entity: its body (`ent:<uid>`) and its parts
  //: (`ent:<uid>:<slot>`). The `:` is what stops `ent:100` claiming
  //: `ent:1000002`'s meshes -- a bare `startsWith(tag)` would, and uids in
  //: this world are prefixes of each other.
  function isEntityMesh(m, tag) {
    const s = String(m.slot || '');
    return s === tag || s.indexOf(tag + ':') === 0;
  }

  /** Upload one frame of one entity's clip. Does not draw. */
  function poseEntity(r, clip, f) {
    if (!D || !D.viewer || !clip || !clip.frames) return;
    f = ((f % clip.frames) + clip.frames) % clip.frames;
    const positions = {};
    for (const ch of (clip.chunks || [])) positions[ch.index] = ch.frames[f];
    const sockets = (clip.sockets && clip.sockets[f]) || {};
    r.anim.sockets = sockets;
    D.viewer.setPose(positions, sockets,
                     m => isEntityMesh(m, 'ent:' + r.uid));
  }

  function startWalk(r) {
    const a = r.anim;
    a.wanted = 'walk';
    if (a.playing === 'walk' || !r.art || !r.art.body) return;
    // The gait is decided every frame, so without this the "this install does
    // not ship the walk" case re-attaches a continuation 60 times a second per
    // entity, forever. It is not the rare case either: 1,100 of the 3,260
    // motions `3dmotion.ini` names are absent from a given install, and a clip
    // that never arrives never sets `playing`, so the early-out above never
    // fires. The Promise is cached, so this is churn rather than traffic --
    // which is exactly the kind of cost that is invisible until it is 24
    // entities deep.
    if (a.fetching || a.absent) return;
    a.fetching = true;
    loadSequence(r.art, ACT_WALK).then(seq => {
      a.fetching = false;
      // "This install ships no walk for this body and weaponset" is a settled
      // answer, not a transient one, and remembering it is the other half of
      // the guard above -- otherwise the empty result is re-examined every
      // frame for as long as the entity keeps walking. It resets with the
      // record when the outfit changes, because the weaponset is part of the
      // clip key.
      if (!seq.length) { a.absent = true; return; }
      // Same race the player has, for the same reason: a one-cell step can
      // finish while its own clip is still in flight, and without this the
      // arriving clip would start a stride on an entity already standing.
      if (a.wanted !== 'walk') return;
      a.seq = seq; a.at = 0; a.frame = -1; a.lastClip = null;
      a.t0 = performance.now();
      a.playing = 'walk';
      if (D.wake) D.wake();
    }).catch(() => {
      // The fetch itself failed, which is not the same as the motion being
      // absent -- but retrying it once a frame is wrong either way, and the
      // server answers "no motion" with a payload rather than an error.
      a.fetching = false; a.absent = true;
    });
  }

  function stopWalk(r) {
    const a = r.anim;
    a.wanted = '';
    if (!a.playing) return;
    a.playing = '';
    a.seq = []; a.at = 0; a.frame = -1; a.lastClip = null;
    // Back to the idle pose rather than freezing mid-stride. `/api/figure`
    // already baked frame 0 of this idle into the vertices it sent, so this is
    // restoring what the server drew, not inventing a rest pose.
    if (!r.art || !r.art.body) return;
    loadSequence(r.art, ACT_IDLE).then(seq => {
      if (a.wanted || !seq.length) return;
      poseEntity(r, seq[0], 0);
      if (D.viewer) D.viewer.draw();
    }).catch(() => { /* leave the last pose: it is a pose, not a bind */ });
  }

  /** Advance every walking figure. Returns true if any pose changed.
   *
   *  Called by `play.js`'s one frame loop, after `place()`, so a figure's
   *  socket matrices are composed onto the position it is being drawn at this
   *  frame and not the previous one. */
  function stepFigures(now) {
    if (!D || !D.viewer) return false;
    let posed = false;
    for (const r of ents.values()) {
      if (!r.fig || !r.art || !r.art.body) continue;
      // The gait decision, every frame rather than on the poll -- the same
      // reason `play.js:syncAnim` runs per frame: the drawn position is what
      // is on screen, so arrival is frame-accurate and the legs stop exactly
      // when the feet do.
      if (r.movedAt && (now - r.movedAt) < WALK_HOLD_MS) startWalk(r);
      else stopWalk(r);

      const a = r.anim;
      if (!a.playing || !a.seq.length) continue;
      let clip = a.seq[a.at];
      if (!clip || !clip.frames) { stopWalk(r); continue; }
      const per = clip.frameIntervalMs || FIGURE_FRAME_MS;
      let f = Math.floor((now - a.t0) / per);
      if (f >= clip.frames) {
        // Hand over to the other foot. A walk chains the same way a run does
        // (`docs/animation.md`: the halves are one footfall each), so playing
        // the head of the chain alone would hop on one leg.
        a.at = (a.at + 1) % a.seq.length;
        a.t0 = now;
        f = 0;
        clip = a.seq[a.at];
      }
      if (f === a.frame && clip === a.lastClip) continue;
      a.frame = f; a.lastClip = clip;
      poseEntity(r, clip, f);
      posed = true;
    }
    return posed;
  }

  //: Whether any figure is mid-stride. Kept apart from the frame-changed
  //: answer for the reason `animating()` documents.
  function figuresWalking() {
    for (const r of ents.values())
      if (r.anim && r.anim.playing) return true;
    return false;
  }

  // --------------------------------------------------------- interpolation

  /** Chase each entity's server position. Returns true if anything moved.
   *
   *  Deliberately the simple version of what the player does: the player has a
   *  route to walk in straight segments, and a remote entity has one confirmed
   *  cell at a time and nothing else. Chebyshev pacing, same as the player, so
   *  a diagonal step takes the same time as an orthogonal one -- which is what
   *  it costs on the wire.
   */
  function advance(dtMs) {
    let moved = false;
    let seededWithEffects = false;
    for (const r of ents.values()) {
      if (!r.seeded) {
        r.dx = r.x; r.dy = r.y;
        r.angle = D.facingAngle(r.direction);
        r.seeded = true;
        moved = true;
        // `auraDefs` refuses an unseeded entity -- it has no drawn position to
        // hang an anchor on -- and seeding happens HERE, on the frame loop,
        // long after the poll that learned the status. Without this the first
        // attach after a status arrives finds nothing to place and nothing
        // ever asks again, so the aura appears only at the next rebuild.
        //
        // Measured, and it is why the first browser check of this work showed
        // a fully resolved aura set with ZERO instances attached: every
        // successful attach in that session had been typed by hand.
        // ...and a weapon glow has exactly the same dependency: `glowDefs`
        // refuses an unseeded entity for the same reason. The race is
        // narrower than the aura's -- a glow arrives after two round trips
        // (/api/game/entities, then /api/figure) while a status rides the
        // position poll that CREATED the entity -- but "narrower" is not
        // "closed", and the failure looks identical: a fully resolved glow
        // with zero instances attached and nothing left to ask again.
        if (r.status || (r.glows || []).length) seededWithEffects = true;
        continue;
      }
      const ddx = r.x - r.dx, ddy = r.y - r.dy;
      const cheb = Math.max(Math.abs(ddx), Math.abs(ddy));
      if (cheb > 1e-4) {
        if (cheb > SNAP_CELLS) { r.dx = r.x; r.dy = r.y; }
        else {
          const step = Math.min(1, (dtMs / stepMsFor(r)) / cheb);
          r.dx += ddx * step;
          r.dy += ddy * step;
          // Walking, for the stride. A SNAP is deliberately not: a respawn or
          // a map change is a teleport, and playing a walk over it would put
          // legs on something that did not walk.
          r.movedAt = performance.now();
        }
        moved = true;
      }
      const want = D.facingAngle(r.direction);
      const d = D.angleDelta(r.angle, want);
      if (Math.abs(d) > 1e-3) {
        r.angle += d * Math.min(1, dtMs / 120);
        moved = true;
      }
    }
    // Once per frame at most, not once per entity: a scene where everyone
    // seeds on the same frame would otherwise tear down and rebuild every
    // instance N times, resetting all their phases N times.
    if (seededWithEffects && D.requestEffects) D.requestEffects();
    return moved;
  }

  // ------------------------------------------------------------ nameplates

  /** Name labels over each entity's head.
   *
   *  This is the visible half of the honesty contract, and the reason it is
   *  worth the projection maths: an entity whose name did not arrive shows
   *  **"no data"** and carries the decoder's reason in its tooltip. It does
   *  not show an empty label, and it never shows a plausible-looking blank.
   *  The 5017-spec-on-a-5065-spawn bug produces exactly a world full of
   *  nameless entities, so this is what makes it visible instead of eerie.
   */
  function labels() {
    const host = D && D.labelHost;
    if (!host) return;
    const mvp = D.mvp();
    if (!mvp) return;
    const cw = D.viewer.canvas.clientWidth, ch = D.viewer.canvas.clientHeight;
    const live = new Set();
    for (const r of ents.values()) {
      if (!r.fig && !r.model) continue;
      live.add(r.uid);
      let el = host.querySelector(`[data-uid="${r.uid}"]`);
      if (!el) {
        el = document.createElement('div');
        el.className = 'nameplate';
        el.dataset.uid = String(r.uid);
        host.appendChild(el);
      }
      const label = r.hasName ? r.name : 'no data';
      const cls = 'nameplate kind-' + (r.kind || 'player')
        + (r.hasName ? '' : ' unnamed');
      if (el.className !== cls) el.className = cls;
      if (el.textContent !== label) el.textContent = label;
      const tip = r.hasName
        ? `${r.kind} ${r.uid}` + (r.level ? ` · level ${r.level}` : '')
        : (r.warnings || []).join('\n') || 'the spawn packet carried no name';
      if (el.title !== tip) el.title = tip;

      // Above the head. The figure is ~170 authored units tall and the label
      // wants to clear it; this is a display offset, not a measurement. It
      // tracks the entity's own scale, or a 3.5x monster wears its nameplate
      // through its chest.
      const [wx, wy] = D.worldXY(r.dx + 0.5, r.dy + 0.5);
      const wz = D.elevationAt(Math.round(r.dx), Math.round(r.dy))
        + 200 * D.figureScale * scaleFor(r);
      const c = mulVec(mvp, [wx, wy, wz, 1]);
      if (c[3] <= 0) { el.style.display = 'none'; continue; }
      const px = (c[0] / c[3] * 0.5 + 0.5) * cw;
      const py = (1 - (c[1] / c[3] * 0.5 + 0.5)) * ch;
      if (px < -200 || py < -100 || px > cw + 200 || py > ch + 100) {
        el.style.display = 'none';
        continue;
      }
      el.style.display = '';
      el.style.transform = `translate(${Math.round(px)}px,${Math.round(py)}px)`;
    }
    for (const el of [...host.children])
      if (!live.has(+el.dataset.uid)) el.remove();
  }

  /** Column-major 4x4 times a column vector. gl.js's M4 has no vector op. */
  function mulVec(m, v) {
    const o = [0, 0, 0, 0];
    for (let r = 0; r < 4; r++)
      o[r] = m[0 * 4 + r] * v[0] + m[1 * 4 + r] * v[1]
           + m[2 * 4 + r] * v[2] + m[3 * 4 + r] * v[3];
    return o;
  }

  // ---------------------------------------------------------------- report

  /** What is on screen and what is not -- for the sidebar, and honest.
   *
   *  Three separate absences, deliberately not collapsed into one: an entity
   *  with no name, an entity with no art in this install, and an entity the
   *  render budget dropped. They have different causes and different fixes.
   */
  function status() {
    const rows = [];
    let drawn = 0, unnamed = 0, undrawable = 0, walking = 0;
    for (const r of ents.values()) {
      if (r.fig || r.model) drawn++;
      else undrawable++;
      if (!r.hasName) unnamed++;
      if (r.anim && r.anim.playing) walking++;
      rows.push({
        uid: r.uid, kind: r.kind, name: r.hasName ? r.name : null,
        x: r.x, y: r.y, drawn: !!(r.fig || r.model),
        walking: !!(r.anim && r.anim.playing),
        why: (r.art && !r.art.drawable)
          ? ((r.art.missing || []).concat((r.art.model || {}).missing || [])
              .join('; ') || 'no mesh resolved')
          : '',
        superseded: (r.art && r.art.superseded) || [],
        // Reported whenever it is not 1, AND whenever the server had to guess:
        // "drawn at authored size because the table has no such name" must not
        // look like "the table says 100 %".
        scale: scaleFor(r),
        scaleWhy: (r.art && r.art.scaleWhy) || '',
        warnings: r.warnings || [],
        // The status bits, and what each one did or did not become. A set bit
        // that draws nothing is the ORDINARY case here (54 of the 79 named 3D
        // effects on 5517 have no art), so it has to be on screen with its
        // reason -- "nobody is buffed" and "buffed with something this install
        // cannot draw" are the same blank figure and not the same fact.
        status: r.status || 0,
        auraNames: r.auraNames || [],
        auraBits: r.auraBits || [],
      });
    }
    rows.sort((a, b) => a.uid - b.uid);
    return { rows, drawn, unnamed, undrawable, walking, dropped, note,
             auraNote };
  }

  /** Called after the page rebuilds the scene: `setMeshes` re-uploads the
   *  original vertices, so every posed model is back at frame 0 and the
   *  frame-dedupe would keep it there. Same dance `play.js:rebuild` does for
   *  the player, for the same reason. */
  function onRebuilt() {
    for (const r of ents.values()) {
      r.frame = -1;
      if (r.anim) { r.anim.frame = -1; r.anim.lastClip = null; r.anim.sockets = null; }
    }
    stepModels(0);
    // Figures lose their stride to the same re-upload, and unlike a model a
    // figure also loses its parts' socket matrices -- `setMeshes` rebuilt them
    // from `local`. Re-posing puts both back in the same call.
    stepFigures(performance.now());
  }

  const API = { init, sync, defs, place, advance, labels, status, shadows,
                applyTextures, refresh, stepModels, stepFigures, onRebuilt,
                animating, count: () => ents.size,
                auraDefs, auraAnchorFor, auraStatus, refreshAuras,
                // The second mechanism, kept a separate quartet on purpose:
                // fusing weapon glows into the aura entry points is the
                // recorded failure this file's section header names.
                glowDefs, glowAnchorFor, glowStatus, refreshGlows };
  global.Entities = API;

})(window);
