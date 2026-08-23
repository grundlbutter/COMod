/* hitfx.js -- the effect channel's ONE-SHOTS: hit sparks.
 *
 * THIS FILE IS THE THIRD MECHANISM, AND IT IS THE ONE THAT NEEDED A NEW
 * CAPABILITY RATHER THAN NEW WIRING.
 *
 *              trigger                table                anchor      lifetime
 *   aura       what was DONE to you   statuseffect.ini     the BODY    endless
 *   glow       what you are WEARING   Action3DEffect       a SOCKET    endless
 *   spark      what just HAPPENED     WeaponEffect.ini     the TARGET  ONE SHOT
 *
 * The first two are a STATE. You can ask the world "who is buffed, who is
 * armed" at any moment and get the whole answer, which is why `play.js`
 * composes them into one array and hands it to `setEffects()`, which replaces
 * the list. Every effect source the page had was of that kind, so every
 * instance in the channel lived exactly as long as its owner and nothing ever
 * had to leave on its own.
 *
 * A hit is an EVENT. No later poll of the world will mention it again. There
 * was nowhere in the channel for such a thing to live -- and THAT is why hit
 * sparks were never built. Not the wire: a melee hit is `MsgInteract` (1022),
 * fully decoded since before this page existed, and `client/game.py` has been
 * journalling every one of them into `world.combat_log` with the attacker, the
 * target and the target's cell. Not the art: `ini/WeaponEffect.ini` has 109
 * rows, all 109 name a `HitEffect`, they are 5 distinct names and all 5 build
 * on 5517. `docs/hit_spark_2026-08-15.md` has the measurement.
 *
 * NOT 1105 AND NOT 1015. Those are the SKILL spark (`MsgMagicEffect` ->
 * `magictype.json TargetEffect`) and the server-triggered named effect
 * (`MsgName` action 9/10), both genuinely undecoded, and a melee hit uses
 * NEITHER. Nothing in this file reads either opcode.
 *
 * WHAT THIS FILE OWNS AND WHAT IT DOES NOT
 * ----------------------------------------
 * It owns: which hits are new, what art each attacker's weapon calls for, and
 * where the spark goes. It does NOT own the effect channel -- it asks
 * `play.js` to spawn, exactly as `entities.js` asks it to recompose, because
 * the page is the one place that knows what the shared clock is doing.
 *
 * THE ANCHOR IS THE PACKET'S CELL, NOT THE TARGET ENTITY'S DRAWN POSITION.
 * `MsgInteract` carries the cell the hit landed on and that is what is used.
 * The drawn entity may be a fraction of a cell away mid-tween, and it may have
 * been removed entirely by a kill -- a spark that vanished because its victim
 * did would be wrong about the one thing it is reporting. The height is the
 * cell's ground elevation plus the scene's own `offsetRender`; the packet says
 * nothing about where on a body a blow landed and no body height is invented
 * here.
 */

'use strict';

const HitFx = (() => {

  //: OFF switch, so the three mechanisms can be measured apart -- the same
  //: instrument `?entglow=0` is, and the one the separation control uses.
  //: `?hitfx=0` stops sparks and leaves auras, glows and the map's ambient set
  //: exactly as they were.
  const OFF = new URLSearchParams(location.search).get('hitfx') === '0';

  //: The page bridge (`play.js::bindHitFx`). Null until then.
  let D = null;

  //: effect name -> scene payload. Shared, read-only, same contract as
  //: `entities.js::auraScenes`: many sparks of one name read one scene and
  //: `fx.js` copies geometry into its own GL buffers per instance.
  const scenes = new Map();
  //: texture key -> url, merged into the page's set so the art is uploaded
  //: BEFORE a hit lands. A one-shot cannot afford the asynchronous first
  //: upload the glow work measured (`docs/weapon_glow_2026-08-15.md` 3.1):
  //: the whole effect is over in a dozen frames, so a texture that arrives
  //: late arrives after the thing that needed it.
  const textures = new Map();

  //: uid -> the server's row for that attacker. Every possible attacker has
  //: one, including the ones that correctly cannot spark.
  let rows = new Map();
  //: The (uid=appearance) set `rows` was fetched for. A re-fetch is a change
  //: of who is holding what, not a poll and not a step.
  let sig = '';
  let pending = false;
  let note = { trigger: '', anchorNote: '', note: '', unresolved: [], slot: '' };

  //: The absolute index of the next combat-log entry we have not sparked.
  //: -1 until the first poll, which is NOT the same as 0: joining a session
  //: that already has a hundred hits behind it must not fire a hundred sparks.
  let cursor = -1;

  //: What actually happened, for the panel. Never a bare absence -- a hit that
  //: drew nothing is a row with the reason, exactly as an unglowing hand is.
  const fired = [];
  const FIRED_KEEP = 12;
  let spawned = 0, suppressed = 0, seen = 0, outbound = 0;

  const texKey = (name, layer) => 'hitfx:' + name + ':' + layer;

  function init(deps) {
    D = deps;
  }

  // ------------------------------------------------------------- resolution

  /** Who could throw a punch here, and with what. Keyed on the appearance in
   *  every candidate's right hand, so equipping something re-fetches and
   *  walking does not. */
  function signature(state) {
    const parts = [];
    const ents = (state && state.entities) || [];
    for (const e of ents) {
      const a = e && e.equipment && e.equipment.r_weapon;
      parts.push(e.uid + '=' + (a || ''));
    }
    return parts.sort().join(',');
  }

  function refresh() {
    if (!D || OFF) return Promise.resolve();
    pending = true;
    return D.api('/api/game/hitfx').then(payload => {
      scenes.clear();
      textures.clear();
      for (const [name, def] of Object.entries(payload.effects || {})) {
        scenes.set(name, def);
        for (const lay of def.layers || [])
          if (lay.texture)
            textures.set(texKey(name, lay.index), D.texUrl(lay.texture));
      }
      rows = new Map();
      for (const r of (payload.attackers || [])) rows.set(r.uid, r);
      note = {
        trigger: payload.trigger || '', anchorNote: payload.anchorNote || '',
        note: payload.note || '', unresolved: payload.unresolved || [],
        slot: payload.slot || '',
      };
    }).catch(() => {
      // A failed fetch must not leave last screen's art hanging off this
      // screen's weapons -- the rule `refreshAuras` and `refreshGlows` follow.
      scenes.clear();
      textures.clear();
      rows = new Map();
    }).then(() => {
      pending = false;
      // Upload now, not at the first hit. See `textures` above.
      if (D.requestTextures) D.requestTextures();
    });
  }

  /** Merge this mechanism's texture urls into the page's set. */
  function applyTextures(into) {
    for (const [k, v] of textures) into.set(k, v);
  }

  // ------------------------------------------------------------- the trigger

  /** Read the poll. Spawns a spark for every hit that is new since the last
   *  call, and re-fetches the art when somebody's weapon changes.
   *
   *  NEW IS DECIDED BY ABSOLUTE INDEX, NOT BY CONTENT. `/api/game/state` ships
   *  `combatLog[-64:]` -- a rolling window with no ids -- alongside
   *  `combatLogTotal`, the number of hits that have ever landed. The index of
   *  the first entry shipped is therefore `total - log.length`, and every
   *  entry has an exact identity.
   *
   *  Content cannot do this job. Two identical hits are byte-identical
   *  records, so a seen-set silently drops the second one -- and the second
   *  one is the common case, because a sim monster takes the same 10 damage
   *  every swing. A suffix match on the window is worse: it looks right and it
   *  refires the entire window the moment more than 64 hits land between two
   *  polls, which is a spark storm, not a dropped frame.
   */
  function sync(state) {
    if (!D || OFF || !state) return 0;
    const log = state.combatLog || [];
    const total = state.combatLogTotal;
    if (typeof total !== 'number') {
      // An older server. Say so through `status()` rather than guessing at an
      // identity we have just finished explaining cannot be guessed at.
      note.noTotal = true;
      return 0;
    }
    const s = signature(state);
    if (s !== sig && !pending) { sig = s; refresh(); }

    const first = total - log.length;          // absolute index of log[0]
    if (cursor < 0) { cursor = total; return 0; }   // joined mid-session
    let n = 0;
    // A window that has slid past the cursor has lost entries we never saw.
    // Report the gap; do not invent sparks for hits whose packets are gone.
    if (first > cursor) { note.missed = (note.missed || 0) + (first - cursor); }
    for (let i = Math.max(cursor, first); i < total; i++) {
      const e = log[i - first];
      if (e) { seen++; if (spark(e, i)) n++; }
    }
    cursor = total;
    return n;
  }

  //: The two `MsgInteract` actions that are a blow landing. `client/game.py`
  //: journals every interact, including trades and marriage proposals, and a
  //: spark on a trade would be this mechanism inventing combat.
  const HIT_ACTIONS = new Set(['Attack', 'Kill']);

  /** One combat-log entry -> at most one transient. Returns true if one
   *  actually spawned. Always records a row either way. */
  function spark(entry, index) {
    const rec = {
      index, attacker: entry.attacker, target: entry.target,
      x: entry.x, y: entry.y, action: entry.actionName || entry.action,
      effect: null, state: '', why: '', anchor: null,
    };
    const keep = r => {
      fired.push(r);
      while (fired.length > FIRED_KEEP) fired.shift();
      return r.state === 'drawn';
    };
    // TWO JOURNALS IN ONE LIST, and only one of them is a blow landing.
    // `world.combat_log` carries RECEIVED packets, keyed `msg`, and our own
    // OUTBOUND requests, keyed `sent` -- `client/game.py::attack` appends one
    // of each per swing, and the outbound one repeats the server's frame
    // inside `serverFrames`. It has an `actionName` of "Attack" and no
    // `attacker`, no `x` and no `y`, so an action-name filter alone lets it
    // through and it then fails as an unknown attacker: a suppression row for
    // every hit that worked, which is the kind of noise that makes a panel
    // stop being read. A hit is a packet that ARRIVED.
    if (entry.msg !== 'MsgInteract') {
      // Not kept as a row either: it would evict a real one from the panel's
      // window at a rate of one per swing, and "we sent a request" is not a
      // fact about whether a spark should have drawn.
      outbound++;
      return false;
    }
    if (!HIT_ACTIONS.has(String(entry.actionName))) {
      rec.state = 'not-a-hit';
      rec.why = `MsgInteract ${entry.actionName} is not a blow landing`;
      suppressed++;
      return keep(rec);
    }
    const row = rows.get(entry.attacker);
    if (!row) {
      rec.state = 'unknown-attacker';
      rec.why = 'the attacker is not among the entities this page was told '
              + 'about, so we cannot say what they are holding';
      suppressed++;
      return keep(rec);
    }
    rec.appearance = row.appearance;
    rec.who = row.name || row.who;
    if (row.state !== 'found' || !row.effect) {
      rec.state = row.state;
      rec.why = row.why || 'no hit effect for this weapon';
      suppressed++;
      return keep(rec);
    }
    const def = scenes.get(row.effect);
    if (!def) {
      rec.state = 'no-scene';
      rec.why = `the server named ${row.effect} and shipped no scene for it`;
      suppressed++;
      return keep(rec);
    }
    const keys = {};
    for (const lay of def.layers || []) {
      if (!lay.texture) continue;
      keys[lay.index] = texKey(row.effect, lay.index);
      textures.set(keys[lay.index], D.texUrl(lay.texture));
    }
    // The packet's cell, centred, at the ground. See the file header for why
    // this is not the target entity's drawn matrix.
    const [wx, wy] = D.worldXY(entry.x + 0.5, entry.y + 0.5);
    const wz = D.elevationAt(entry.x, entry.y);
    const inst = D.spawnTransient(def, {
      role: 'hitspark',
      slot: 'hit:' + entry.target + ':' + index,
      anchor: D.translation(wx, wy, wz),
      textureKeys: keys,
    });
    if (!inst) {
      rec.state = 'unbuildable';
      rec.why = `${row.effect} resolved on the server and would not build here`;
      suppressed++;
      return keep(rec);
    }
    rec.state = 'drawn';
    rec.effect = row.effect;
    rec.anchor = [Math.round(wx), Math.round(wy), Math.round(wz)];
    // The lifetime, IN FRAMES. Not ms: a frame count is what the alpha
    // envelope is authored in, and duration measurements are fleet-blocked on
    // this box anyway. The instance expires on its own when `frameAt` reports
    // done -- nothing here schedules a removal, which is exactly why there is
    // no timer to leak.
    rec.lifetimeFrames = row.lifetimeFrames || 0;
    spawned++;
    return keep(rec);
  }

  // ------------------------------------------------------------- the panel

  /** What the panel says about hit sparks. Never a bare absence.
   *
   *  Every possible attacker is a row, including the ones that correctly
   *  cannot spark -- 5 of the 23 weapon types on 5517 carry no `HitEffect` at
   *  all, so "this weapon draws nothing" is an ordinary answer and must not
   *  look like a lookup that failed. And every hit that DID land is a row,
   *  including the ones that drew nothing, with the reason.
   */
  function status() {
    return {
      off: OFF,
      attackers: [...rows.values()],
      fired: fired.slice().reverse(),
      counts: { seen, spawned, suppressed, outbound,
                live: D && D.liveTransients ? D.liveTransients() : 0,
                expired: D && D.expiredTransients ? D.expiredTransients() : 0 },
      scenes: [...scenes.keys()],
      unresolved: note.unresolved || [],
      trigger: note.trigger || '', anchorNote: note.anchorNote || '',
      note: note.note || '',
      missed: note.missed || 0,
      noTotal: !!note.noTotal,
    };
  }

  return { init, sync, refresh, applyTextures, status,
           //: For the measurement harness only -- a deterministic spark at a
           //: named cell, so a capture does not have to race a live fight.
           //:
           //: It builds the record `on_interact` builds and hands it to
           //: `spark()`, so it exercises the same resolution, the same
           //: suppression rules and the same anchor as a real hit. `msg` is
           //: part of that record and not decoration: the received-packet
           //: check reads it, and a probe without one is rejected as our own
           //: outbound request -- which is exactly what happened the first
           //: time this was run, and is the probe agreeing with the filter.
           probe: (attacker, target, x, y) =>
             spark({ msg: 'MsgInteract', action: 2, actionName: 'Attack',
                     attacker, target, x, y, data: 0 }, -1) };
})();

window.HitFx = HitFx;
