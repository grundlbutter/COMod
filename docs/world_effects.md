# World effects — how effects get INTO the world, and the skill path

`docs/effects.md` decoded what an effect *is* (a `3DEffect.ini` section →
layers → C3 quads/trails with their own animation tracks) and how a *weapon*
selects one. Its §10 left open "what spawns and despawns an effect instance".
This doc answers the spawn half for the world: the three channels that place
an effect at a map location or on a role, including the skill (PvP) path —
prompted by the companion-app observation that some effects *behave like
items, targeting map coordinates*. That observation is correct, and the
message doing it is named below.

Companion tool: **`tools/worldfx.py`** — module + CLI. Every corpus number
below comes out of `--verify`.

```bash
py -3 tools/worldfx.py --verify          # every corpus claim in this doc
py -3 tools/worldfx.py --map newplain    # Twin City's authored effects, as cells
py -3 tools/worldfx.py --magic 1002      # Tornado's effect chain, all levels
py -3 tools/worldfx.py --status         # status bit -> persistent aura
```

Claims are **VERIFIED** (proved against the shipped corpus, confirmed on live
traffic, or corroborated across independent sources) or **INFERRED** (best
reading; ground truth inside the Themida-packed exe, which stays out of
scope per `docs/CONTEXT.md`).

---

## 1. The answer in one page

Three channels spawn a world effect, and they converge on one name space —
`ini/3DEffect.ini`, the same 2,240-section table the weapon effects use —
and one playback model (`docs/effects.md` §6–8):

| channel | carrier | placement |
|---|---|---|
| **the map itself** | `.DMap` EFFECT layer: `char[64]` effect name + `u32 x, y` | a fixed pixel point — torches, magic stones, flower beds |
| **the wire, ambient** | `MsgName` (1015) action **9 MAP_EFFECT** (x,y packed in `data`) and **10 ROLE_EFFECT** (role id), effect *name* in the string list | server-triggered: fireworks at a spot, `goldendragon` on a player |
| **the wire, combat** | `MsgMagicEffect` (1105): caster, target-id *or* coordinate, magic type+level, victim list | skill casts — the type+level keys `ini/magictype.json`, whose columns name the intone/target effects |

Plus the standing decoration on entities: **status bits** (`MsgUserAttrib`
1017 field 26) index `ini/statuseffect.ini`, whose first column is a
`3DEffect.ini` name (a persistent aura) and whose second is a legacy 2D
`effect.ini`/.ani effect.

The companion finding, resolved: `MsgName` action 9 has exactly the shape of
a ground item drop — a thing appearing at a packed x/y — except the payload
is an effect *name* instead of an item id. The client spawns the named
3DEffect at those coordinates. Effects really do "behave like items
targeting map coordinates", down to sharing the message idiom of
`MsgMapItem` (1101).

## 2. Map-authored effects — the `.DMap` EFFECT layer

`core/dmap.py` already parsed the record (tag 10: `char[64]` name,
`u32 x, y`); what was missing was the coordinate *space* and whether the
names mean anything. Both are settled.

**The corpus** (`--verify`): **2,504 EFFECT records across 45 of the 136
maps**. Busiest: `Dcloister` 362, `Gulf` 354, `Dgate` 319, `woods` 180,
`newplain` (Twin City) 153. **2,499 of the 2,504 names are literal
`3DEffect.ini` sections** — the five that are not (`zf2-e150`,
`zf2-e173..176`, one record each) are data bugs in two maps, not a second
name space. VERIFIED.

**The coordinate space — recovered, VERIFIED 2,504/2,504.** The x,y are
pixels in the map's **full isometric diamond** — the bounding box of the
whole cell grid, origin at cell (0,0)'s top corner:

```
x = (gx - gy + W) * 32          gx = (x/32 + y/16 - W) / 2
y = (gx + gy) * 16              gy = (y/16 - x/32 + W) / 2
```

Every one of the 2,504 records maps to a cell inside its map's grid under
this transform, zero tolerance, zero exceptions. It is *not* the painted
image's pixel space (`tools/puzzle.py`) — only 368 records land inside the
painted rectangle — but it differs from it by a constant only:

```
painted_x = x - (W - K) * 32        painted_y = y - K * 16
```

so the viewer's existing placement pipeline gets these for free.

**The SOUND layer (tag 15) lives in the same space**: 937 of 941 records
land inside the grid (the 4 outliers are on one desynchronised map). Twin
City pairs them: the `magicstone1` effect at cell (795.8, 465.8) has
`ball1.wav`, range 1000, at (795.6, 465.6) — the same landmark, both layers.
VERIFIED.

These are ambient loops — `flower04-*` beds around Twin City's plaza,
`fp-lt` torch flames placed 361 times around the cloister. Their
`3DEffect.ini` sections carry `LoopTime` ≥ 99999 (endless); playback is
`docs/effects.md` §8 verbatim, anchored at the record's point. *(That the
engine treats them as endless loops anchored at the point is INFERRED; the
names, coordinates and loop fields are VERIFIED data.)*

## 3. Wire-spawned effects — `MsgName` 1015

Layout (both wikis, and the frame is **VERIFIED against live traffic** —
`capture/pktfeed.py` decodes 1015 from the current server's protobuf
dialect):

```
u16 size, u16 type=1015
u32 data          # role id, guild id, or LOW:x HIGH:y -- depends on action
u8  action
strings           # length-prefixed string list
```

| action | name | data | string | status |
|---|---|---|---|---|
| 9 | **MAP_EFFECT** | `LOW` x, `HIGH` y | effect name, e.g. `rainbow` | [S] — both wikis, ordinals corroborated at six points |
| 10 | **ROLE_EFFECT** | role id | effect name | **[V] — observed live 2026-07-30: `goldendragon` on a player** |
| 1 | FIREWORKS | hero id | text; client spawns a firework per 2 chars near screen centre | [S] |
| 20 | PLAYER_WAVE | `LOW` x, `HIGH` y | a `.wav` path | [S] |

All three probed names — `rainbow`, `goldendragon`, `warrior-s` (the wikis'
examples plus the live capture) — **resolve exactly in this build's
`3DEffect.ini`**. VERIFIED. The name on the wire *is* the section name; there
is no id indirection.

`MsgMapItem` (1101) is the same idiom for items (id + x/y, add/remove
actions, already decoded for the companion's ground watcher), which is why
the two looked alike from the packet feed.

## 4. The skill path — `MsgMagicEffect` 1105 + `ini/magictype.json`

### 4.1 The message

From the wiki (patch 4267, ☑️ observed) plus **one live sample** decoded by
`capture/pktfeed.py` (`[?]` there — field guesses, one packet):

```
u16 size, u16 type=1105
u32 caster
u32 target            # a role id, OR LOW:x HIGH:y for ground-cast
u16 magicType
u16 magicLevel
u32 count
count x { u32 role; u32 damage; u32 reserved }   # every victim, with its hit
```

Note the dual `target`: a skill either points at a role or **at a map
coordinate** — the second place effects target coordinates like items do.
Later clients lightly encrypt the coordinates (per the wiki, as in
`MsgInteract`); the 1073 dialect's exact framing is protobuf field numbers,
one sample so far. Direct melee/PvP damage travels on `MsgInteract` (1022)
— actor, target, target x/y, damage — which the companion already
established field-by-field [V].

### 4.2 The lookup

`(magicType, magicLevel)` keys `ini/magictype.json` — 610 rows, and the pair
is distinct on 605 of them (types 3000 and 3060 each carry a duplicated
level-0 row; take the first). Each row carries the whole client-side
production, VERIFIED against the corpus:

| field | meaning | evidence |
|---|---|---|
| `IntoneEffect` + `IntoneDuration` | the **channel/wind-up** effect on the caster, for N ms | `Thunder` → `Intone-1`, 500 ms |
| `SenderAction` | the caster's action id — `903` = cast (`docs/effects.md` §5) | 903 on the mage line |
| `TargetEffect` + `TargetSound` + `TargetDelay` | what plays **on each victim** in the 1105 list, delayed N ms | `Tornado` L0..L3 → `thor-1..thor-4` + `sound\thunder.wav` — per-level effect art |
| `Ground` | the skill is cast **at a coordinate**, not a role | 256 rows, including the whole `FastBlade`/`ScentSword` line — exactly the skills whose 1105 `target` is an x/y |
| `Multi` / `Range` / `Distance` | AoE shape parameters | `FastBlade` range grows 4→8 by level |
| `Status` + `Duration` | status **mask** the skill applies, for N seconds | `Superman` → 8192 (bit 13), `Invisibility` → 131072 (bit 17), 181 rows |
| `ScreenRepresent` / `ClientRepresent` | small enums (1–3), unnamed | present on a handful of rows |

**131 distinct effect names appear across the intone/target columns and 125
resolve in `3DEffect.ini`.** The six misses (`1－bomb4`, `2－bomb3`,
`3－bomb`, `4－bomb2`, `5－bomb1` — full-width dashes — and `hitlight`) exist
in *neither* dash spelling: absent art, same as §4.3's four status names,
not a missing subsystem. VERIFIED.

So a PvP cast renders as a timeline the client can drive entirely from data:

```
1105 arrives → row = magictype[(type, level)]
caster: play IntoneEffect for IntoneDuration, action = SenderAction
        (attack skills also pull the weapon-trail row for action 900/901/903
         from Action3DEffect.ini -- docs/effects.md §2)
target(s): after TargetDelay, play TargetEffect + TargetSound on each
           role in the victim list; apply its damage number
ground-cast (Ground=1): the anchor is the packed x/y, not a role
status (Status!=0): set the bit for Duration -- which turns on §5's aura
```

*(The timeline ordering is INFERRED — assembled from the field names,
durations and the wiki's message semantics; every table lookup in it is
VERIFIED data.)*

### 4.3 Status auras — `ini/statuseffect.ini`

20 rows: `<bit index> <3D effect | NULL> <2D effect | NULL>`. The bit index
is the same numbering the companion verified against `MsgUserAttrib` (1017)
status bitfields on live traffic. Column 1 is a `3DEffect.ini` name — a
persistent aura while the bit is set (`attackfast40`, `SuperSoldier`,
`BodyShield`, `CTF_Flag`…): 15 of 19 non-NULL names resolve; `Reflect`,
`ReflectMagic`, `Dodge`, `Bleed` are in neither the ini nor the JSON (absent
art, stated not padded). Column 2 is a section of **`ini/effect.ini`** — the
legacy 2D `.ani` effect system, still alive in exactly five sections:
`TeamLeader`, `Silent`, `MapItemFlash`, `ShitsHappen`, `Freeze` — and
`MapItemFlash` is the flash on a ground item drop, closing the item/effect
symmetry from the other side. VERIFIED.

### 4.4 The legacy stubs

`ini/MagicEffect.ini` (119 bytes, one section) and `ini/3DEffectInfo.ini`
(200 bytes, `Amount=0`) are vestigial in this build — the live tables are
`magictype.json` + `3DEffect.ini`. VERIFIED by size and content.

## 5. What the engine side already provides

From `docs/dll_analysis.md` and `docs/effects.md`, the unpacked DLLs supply
everything below the spawn decision: `Role3D.dll` exports
`Game3DEffectCreate`, `LimitGame3DEffectLevel`, `Set3DEffectMaxLOD`, and an
effect cache with preloading and lifetimes (`Add_EffectToPreLoadList`,
`Remove_EffectFromPreLoadList`, `Reset_EffectLifeTime(name, ms)`) — i.e. the
retail engine keeps recently-used effects warm by *name* with a lifetime
clock, which is also the natural design for ours. The spawn/despawn
orchestration itself is in the packed exe (unchanged conclusion).

## 6. Drawing this in our client — the short version

Everything needed is already in the repo's vocabulary:

* **Ambient map effects**: at map load, read the EFFECT layers, convert with
  the §2 transform (or the constant shift into painted-image space the
  viewer already uses), and instantiate each name through
  `tools/effects.py`'s `EffectDB` playback (§8 of that doc). They are
  endless loops; cull by window like everything else. SOUND records give the
  ambient audio with range and volume.
* **1015 action 9/10**: spawn the named effect at the packed x/y (one-shot,
  duration from its own alpha envelope / `LoopTime`) or attach to the role.
* **1105**: the §4.2 timeline. The victim list also carries the damage
  numbers the UI wants — same message, both jobs.
* **Status bits**: diff on every 1017; attach/remove the §4.3 aura names.

## 7. Verified vs inferred, at a glance

**VERIFIED**

* EFFECT layer: 2,504 records / 45 maps; the full-diamond pixel transform
  maps 2,504/2,504 into the grid; 2,499 names are `3DEffect.ini` sections;
  SOUND shares the space (937/941).
* The wire names (`rainbow`, `goldendragon`, `warrior-s`) resolve literally
  in `3DEffect.ini`; 1015 action 10 observed on live traffic with a real
  effect name; the 1015 action table is corroborated across two wikis at six
  ordinal points.
* `magictype.json`: 610 rows keyed by (MagicType, Level) — distinct on 605;
  125/131 effect names resolve; per-level target effects (`thor-1..4`);
  `Ground=1` on 256 rows including the coordinate-cast melee lines;
  `Status` masks on 181 rows.
* `statuseffect.ini` column semantics (3D aura + legacy 2D), 15/19 + 5/5
  resolving; `effect.ini`'s five live sections.
* `MagicEffect.ini` / `3DEffectInfo.ini` are stubs.

**INFERRED**

* Map EFFECT records are endless anchored loops (their `LoopTime` says
  endless; the anchoring behaviour is not readable).
* The §4.2 cast timeline ordering, and that `TargetDelay` is measured from
  the 1105 arrival.
* The 1105 protobuf-dialect field mapping (one live sample, marked `[?]` in
  `capture/pktfeed.py`).

**OPEN**

* `ScreenRepresent` / `ClientRepresent` enum meanings.
* The four status-aura names with no art (`Reflect`, `ReflectMagic`,
  `Dodge`, `Bleed`).
* The 1073 dialect's coordinate encryption for 1105 (if any survives the
  protobuf rewrite), and action 9 on live traffic — not yet captured; worth
  a capture session next to a firework or map event.
* Particle-only effects (`PTCL`/`PTC3`) are still the `docs/effects.md`
  §6.6 gap; some ambient map effects will need them.
