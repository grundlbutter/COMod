# Action animation, and weapon-attached super effects

How the Conquer client plays a character action — idle, walk, run, jump, swing,
cast, die — and where a weapon's always-on "super" glow actually goes.

Written so a renderer can be implemented from this document alone, the way
`docs/effects.md` §8 was. It builds directly on two earlier specs and does not
repeat them:

* **`docs/attachment.md`** — sockets, the `PHY`↔`MOTI` ordinal binding, the
  transform chain. Everything here about placing a part is that document.
* **`docs/effects.md`** — `MOTI`/`SHAP`/`SMOT` binary layouts, `C3Key` channels,
  the effect tables, and the effect playback loop (§8).

> ## SUBJECT BINARY, added 2026-08-26 by RE New Functionality. The RVAs below are **x86-64** and belong to **one** DLL.
>
> This document cites `graphic.dll` RVAs and never says which `graphic.dll`. The
> corpus holds **eleven**, from 180 KB to 5.0 MB, and **every retail one is 32-bit**:
>
>     Clients/CCO-snapshot-2026-08-24/bin/64/graphic.dll   2,728,960 B
>       machine 0x8664 (x86-64), PE32+, SizeOfImage 0x2c8000
>       sha256 1e6cf71ff3f1871a4cb9f861e02c7c9686db4ee9a1b39d5912380bc95cc3534a
>       ALL TEN RVAs cited across this document and docs/attachment.md land in
>       its .text: 0x266d0 0x26880 0x27610 0x277c0 0x27b30 0x28360 0x56040
>       0x5a735 0x5b44c 0x5bbc0
>
>     every retail graphic.dll: machine 0x14c (i386, 32-bit)
>       5017 5065 180,224 B · 5165 286,720 · 5517 323,584 · 6090 327,680
>       6609 339,968 · 6609/Env_DX9 830,464
>       7878/Env_DX8 3,222,680 · 7878/Env_DX9 5,000,728 · 7878/graphicDX9.dll 1,744,144
>       and the HIGH RVAs (0x56040, 0x5a735, 0x5b44c, 0x5bbc0) are OUTSIDE THE
>       WHOLE IMAGE of 5017, 5065, 5165, 5517, 6090 and 6609.
>
> **So these addresses must never be tried against a retail client.** It is not a
> build difference, it is a different ARCHITECTURE -- `py -3 tools/disfn.py` on a
> 32-bit DLL at `0x5b44c` will decode something, and it will be unrelated code.
>
> **The identification is positive, not circumstantial:** this document's own
> content claim -- *"`graphic.dll` divides the STEP1/STEP2 UV-scroll rates by the
> literal 33.0"* -- is verifiable there and nowhere else. The IEEE float and double
> encodings of 33.0 each appear **once** in the CCO binary and **zero times in all
> ten retail `graphic.dll` files**, against a working control (1.0f appears 306
> times in the CCO binary, 219 in 5065).
>
> *Method note, because the negative nearly misled its own author: "33.0 absent from
> every retail graphic.dll" was read as "the claim may be unverifiable" before the
> subject was found. A zero is only informative once you have seen the instrument
> return a one.*

Every claim is marked **VERIFIED** (read out of `graphic.dll` / `Role3D.dll`
with the RVA cited, stated by an ini file's own header, or proven across the
whole shipped corpus) or **INFERRED** (a rule that reproduces the data, whose
ground truth is inside the Themida-packed `ImConquer.exe`) or **UNKNOWN**.

Companion tools — every number below comes out of these:

```bash
py -3 tools/anim.py --keys                        # the 3dmotion.ini key space
py -3 tools/anim.py --actions                     # the action-code table
py -3 tools/anim.py --clip 002135000 401 --weapon 410009
py -3 tools/anim.py --clip 002135000 130 --as-shape 1001 --distance 120
py -3 tools/anim.py --sequence 002135000 run      # the chained run cycle
py -3 tools/anim.py --root-motion 002135000 131   # the jump arc, per frame
py -3 tools/anim.py --actionctrl                  # ini/ActionCtrl.ini decoded
py -3 tools/anim.py --validate                    # sections 1-9 of the proof

py -3 tools/superfx.py --weapon 410099
py -3 tools/superfx.py --anchor 002135000 410099  # the actual matrices
py -3 tools/superfx.py --families                 # every attachable family
py -3 tools/superfx.py --validate
```

---

## 1. The answer in one page

### Playing an action

```
body appearance 002135000            equipped weapon 410009
        |                                    |
   shape = "2"                          type = "410"  -> weaponset
        |                                    |
        +---------------+--------------------+
                        v
   ini/3dmotion.ini["2" + "410" + "401"]  =  c3/0002/410/401.c3
                        |
                        v
   the action motion .c3: N MOTI chunks (+ N PHY on CCO, §8 step 2)
                        |
        C3Mesh::SetMotion  (graphic.dll 0x277C0)  -- BY ORDINAL
                        v
   bodyMesh.phy[i].motion = motionSet.Get(i)          for every i
                        |
        +---------------+--------------------------------+
        v                                                v
   v_body: skin the mesh                        v_r_weapon: the socket
   p' = SUM_k w_k * (p x Mbone_k(frame))        Msock = Motion_GetMatrix(
                                                          motion[idx], 0, frame)
```

* frame advance: `frame = (frame + 1) % frameCount`, `Phy_NextFrame`
  (`graphic.dll 0x5BBC0`) — **VERIFIED**.
* frame interval: **41 ms** (24.4 fps) — **INFERRED**, §5.
* loop behaviour is per action, §6. Run is two clips that alternate.
* **no locomotion clip carries horizontal root motion** — walk and run are
  in-place cycles. The jumps *do* carry their vertical arc. §7.

### Placing a super weapon effect

```
   weapon appearance 410099
        |
   Action3DEffect.ini[999.999.410.099]  =  "410099"
        |
   3DEffect.ini[410099]  FrameInterval=80  LoopTime=99999999  Offset=(0,0,0)
        |   EffectId0=2205                     TextureId0=3580
        v                                          v
   3DEffectObj.ini[2205]                     3dtexture.ini[3580]
   = c3/effect/blade/410099.C3               = c3/effect/blade/410099.dds
        |
   7 quads, each with its own 101-frame MOTI
        |
        v
   p_world = p_bind x M_fxBone(vertex.bone0, frame_fx)
                    x M_offset                       (3DEffect.ini OffsetX/Y/Z)
                    x M_socket                       (v_r_weapon on the BODY)
                    x M_role
```

**The effect is a sibling of the weapon at the socket, not a child of it.** It
must **not** inherit the weapon's own bone-0 `MOTI` matrix. §10.

---

## 2. `ini/3dmotion.ini` — the key space — VERIFIED

229,481 rows, 228,985 distinct keys (496 duplicated rows, 62 of which
disagree with their first value — take the last, as a naive ini reader does).
3,260 distinct motion files are referenced.

Every key is the concatenation

```
[ <distance> ] <shape> <weaponset:3> <action:3>
```

with the shape written at its **natural width** and the optional leading
distance field present only on jump rows. That gives six key lengths:

| len | keys | layout | population |
|---:|---:|---|---|
| 7 | 89,705 | `shape1 + ws3 + act3` | player bodies `1`–`4` |
| 8 | 416 | `shape2 + ws3 + act3` | ghost shapes `98`, `99` |
| 9 | 36,298 | `shape3 + ws3 + act3` | monster / NPC shapes (267 of them) |
| 10 | 96,662 | `shape4 + ws3 + act3` | `0001`–`0004`, `1001`–`1004`, and 4-digit monster/NPC ids |
| 12 | 4,428 | `dist2 + shape4 + ws3 + act3` | jump distance 10…90 |
| 13 | 1,476 | `dist3 + shape4 + ws3 + act3` | jump distance 100…120 |

`py -3 tools/anim.py --validate` section 1 accounts for **228,985 of 228,985**
keys with this decomposition.

**Evidence for the split** — the *value* names the fields:

```
2000100      = c3/0002/000/100.c3      shape 2,    weaponset 000, action 100
0004804631   = c3/0004/500/631.c3      shape 0004, weaponset 804, action 631
103000100    = c3/monster/103/100.c3   shape 103,  weaponset 000, action 100
98000001     = c3/ghost/098/100.c3     shape 98
101001000130 = c3/1001/000/130.c3      distance 10, shape 1001, ws 000, act 130
```

### 2.1 Shapes `1`–`4` and `1001`–`1004`

Both families exist. `1`–`4` are the four player bodies (`c3/0001` …
`c3/0004`) and carry ~22,400 rows each; `1001`–`1004` are a parallel set with
~22,750 rows each under `c3/1001` … `c3/1004`. `0001`–`0004` also appear, as a
ten-wide spelling of the same four bodies.

**That ten-wide family is wider than this section used to say.** It read "only
~1,420 rows each and only under the `800`–`804` weaponsets". MEASURED
2026-09-25 on the live CCO install (`C:\Program Files\Classic Conquer 2.0`, the
default root): **5,240 rows over nine weaponsets**, ~1,310 per shape
(`0001` 1,307, the other three 1,311 each) —

| weaponsets | rows |
|---|---:|
| `350`, `360`, `370`, `380` | **3,436** (976 / 932 / 736 / 792) |
| `800`–`804` | **1,804** (344 / 352 / 352 / 352 / 404) |

The `350`–`380` half was missed entirely, and it is the larger half. The same
counts hold on `CCO-snapshot-2026-08-24`, which is the install §2's own row
totals were measured on, so **this was never a stale figure — it was wrong when
written**. (The live install carries 229,493 rows / 228,997 distinct keys to the
snapshot's 229,481 / 228,985; §2's table is the snapshot's.)

Those 5,240 rows name **1,091 distinct files, 1,043 of which a seven-wide key
also names — so 48 files are reachable only through a ten-wide key.** A reader
that indexes one spelling and not the other loses those 48 and nothing
complains; `MotionIndex` indexes both, and `SRC_INI_PATH` marks which spelling
came from a row's own path.

The shape for a player is **the first three digits of the `armor.ini`
appearance with leading zeros stripped** — `002135000` → `2` (VERIFIED, this
is `docs/attachment.md` §8.1's `<bodyDigit>000100` generalised).

Which appearance uses shape `1001`–`1004` rather than `1`–`4` is **UNKNOWN** —
nothing in the shipped `armor.ini` selects it, and the choice is made by the
packed exe. The `1001`–`1004` sets are the only ones with distance-tiered
jumps, so they are presumably a later, higher-fidelity body generation.

> **The `c3/1001` … `c3/1004` trees do not ship in this install.** All 292
> files those shapes name are absent from both the loose tree and both WDF
> archives, so shapes `1001`–`1004` — and therefore every jump-distance-tiered
> key — are unplayable here. The `1`–`4` bodies are unaffected. More generally,
> **1,100 of the 3,260 distinct motion files named by `3dmotion.ini` are absent,
> and 144,908 of the 228,985 keys (63 %) point at one of them**
> (`anim --validate` section 2). A viewer must handle a resolved-but-missing
> motion gracefully; `AnimDB.clip()` returns `None`.

### 2.2 Two stray key families

18 keys do not decompose: `8500100`/`8500101` (→ `c3/mount/850/…`, and mounts
have their own `ini/MountMotion.ini`) and `9998800`–`9998871` (→
`c3/mesh/9998xxx.C3`, looked up whole by `ini/3DsimpleRole.ini`'s
`3DStandByMotion` / `3DBlazeMotion` fields). Treat both as opaque ids.

### 2.3 The jump-distance field — VERIFIED

Only shapes `1001`–`1004` ship it. The leading field takes exactly the twelve
values `10, 20, …, 120` and selects among four clips:

| distance | clip |
|---|---|
| 10, 20, 30, 40 | `c3/1001/000/130.c3` |
| 50, 60, 70, 80 | `c3/1001/000/130-1.c3` |
| 90, 100 | `c3/1001/000/130-2.c3` |
| 110, 120 | `c3/1001/000/130-3.c3` |

A longer jump gets a bigger arc. The unit of "distance" is **INFERRED** to be
map tiles or a percentage of maximum jump range; nothing states it. All four
clips are missing from this install (see the note in §2.1), so this is a
documented mechanism with nothing to play — do not build UI around it.

---

## 3. The action codes

The last three digits. `py -3 tools/anim.py --actions` prints this table with
the clip each code resolves to; `tools/anim.py:ACTIONS` is the machine-readable
form, each entry carrying its own evidence string.

191 distinct action codes ship for player shape `2` weaponset `000`; 75 of
them are identified below.

### 3.1 Identified

| code | meaning | confidence | evidence |
|---|---|---|---|
| `100` | stand / idle | VERIFIED | the first motion of every shape; the action the always-on armour auras hang off (`Action3DEffect 999.100.135.999`); 19 of the 191 codes a player body ships resolve to its `100.c3` |
| `101` `102` `103` | idle variant, 60 frames | INFERRED | own clip `101.c3`, 102/103 alias it |
| `105` | combat stance | INFERRED | own 16-frame clip; posed silhouette is 159.8 tall against idle's 170.0 — a lower, hunched stance |
| `110` `111` | walk | VERIFIED | `ActionSound.ini` `<shape>.999.110 = sound/walk.wav` |
| `115` | walk, second style | VERIFIED | `ActionSound.ini 115 = sound/walk.wav`; does **not** chain with 110 |
| `120` `122` | run, left foot | VERIFIED | `ActionSound.ini 120 = sound/runL.wav` |
| `121` `123` | run, right foot | VERIFIED | `ActionSound.ini 121 = sound/runR.wav` |
| `125` / `126` | run 2, left / right foot | VERIFIED | `ActionSound.ini` `runL.wav` / `runR.wav` |
| `130` `132` | jump — hop in place | VERIFIED | `ActionSound.ini 130 = sound/jump.wav`; centroid 91.7 → 110.8 → 91.7 |
| `131` | jump — leap | VERIFIED | `ActionSound.ini 131 = sound/jump.wav`; centroid 94.0 → 153.6 → 90.8, a full arc |
| `140` `404` `407` | attack 1 | INFERRED | alias `401.c3` |
| `150` `151` `180` | emote | UNKNOWN | own 21-frame clip, upright |
| `160` `170` `190` `200` `280` | emote | UNKNOWN | own clips, upright, no sound and no effect row |
| `210` `220` | kneel / crouch | INFERRED | 20-frame clip held at 123.5 units on a 170-unit body |
| `230` → `231` | emote into a held pose | INFERRED | 38 frames that do not return; `231` is its 2-frame hold |
| `250` `260` `261` | sit | INFERRED | 20-frame cycle held at 87.9 units — half body height |
| `270` → `271` | lie down | INFERRED | 40 frames ending flat (top 170 → 34); `271` is the 2-frame hold |
| `290` | dig / gather | VERIFIED | `ActionSound.ini 290 = sound/dig.wav` |
| `300` `305` | hurt, light | INFERRED | in `Action3DEffect`'s explicit `none` suppression group |
| `310`(`311`–`319`) | knocked back | INFERRED | same suppression group; own 20-frame clip |
| `320`(`321`–`329`) | hurt | VERIFIED | `ActionSound.ini 320–323 = sound/bruise0X.wav` |
| `330` → `331` | die → corpse | VERIFIED | `ActionSound.ini 330/332/334/336 = sound/d_monster0X.wav`; `ActionMap3DEffect [104330999]` plays `Feather` on a bird shape's 330; the clip ends with the silhouette top at 20.8 of 170 |
| `332`→`333`, `334`→`335`, `336`→`337` | die variants → corpse | VERIFIED | same sound family |
| `340` → `341` | die, second family → corpse | VERIFIED | 175 shapes have their own `340.c3`; monster 103's bone-0 collapses from up 313 to −15. `ActionSound 340/342/344/346/370 = sound/blk_monster0X.wav` |
| `342`→`343`, `344`→`345`, `346`→`347`, `370`→`371` | die variants → corpse | VERIFIED | same |
| `401` `402` `403` | attack swing 1 / 2 / 3 | VERIFIED | `ActionSound.ini` `sound/t<weapon>_m0N.wav`; `Action3DEffect 999.40N.<type>.<sub>` is the swing trail (`docs/effects.md` §2) |
| `405` `408` / `406` | attack 2 / 3 | INFERRED | alias `402.c3` / `403.c3` |
| `451` `452` `453` | special attacks | UNKNOWN | own 31-frame clips, same length as `401` |
| `501` `510` | archer poses | INFERRED | `Action3DEffect 999.501.138.999 = archer-p`; both fall back to `100.c3` on player bodies |
| `900` `901` `903` | cast | VERIFIED | `Action3DEffect` group-1 values; `magictype.json` `SenderAction: 903` on `Thunder`. `901` is a 16-frame *cycle* — the intoning loop — while `900` and `903` are one-shots |
| `919` | cast / special | UNKNOWN | own 41-frame clip |

### 3.2 The N → N+1 "terminal hold" rule — VERIFIED

`231`, `271`, `331`, `333`, `335`, `337`, `341`, `343`, `345`, `347`, `371` are
all **2-frame motions holding the last pose of the even action below them**.
Measured as the largest per-vertex distance between the last frame of `N` and
frame 0 of `N+1`, on a ~170-unit body (`--validate` section 8):

| pair | 001 | 002 | 003 | 004 |
|---|---:|---:|---:|---:|
| `230`→`231` | 0.000 | 13.699 | 0.000 | 0.000 |
| `270`→`271` | 2.924 | 4.125 | 1.065 | 3.497 |
| `330`→`331` | 16.830 | 5.118 | 0.000 | 0.000 |

Exact on bodies 003 and 004; 001 and 002 settle by up to 17 units. So it is the
*same pose*, not always the same matrices. Play `N` once, then hold `N+1`.

### 3.3 Not identified

124 codes ship for player shape 2 and are not identified here — most notably
`001`–`008` (500–680-frame clips posed at ~90 units tall, i.e. long seated
emotes or cutscene loops), `221`–`229`, `291`–`296`, `461`–`483`, `502`–`529`,
`598`/`599`, `610`–`651`, `700`–`712`, `904`–`917`. `ActionSound.ini`,
`ActionDelay.ini` and `Action3DEffect.ini` carry no rows for any of them, so
there is no evidence in this build to name them. Several resolve to a file that
does not ship (`--actions` marks those).

### 3.4 The attack cycle

`Action3DEffect` gives all three of `401`, `402`, `403` a trail for every
weapon that has one, and `ActionDelay.ini` has rows only for those three. The
client cycles them; the order is **INFERRED** to be 401 → 402 → 403 → 401.

---

## 4. The weaponset field

The middle 3 digits. This is what makes a swing with a blade different from an
unarmed swing.

### 4.1 It really does change the motion — VERIFIED

Body `002135000`, action `401`, largest per-vertex difference against the
unarmed clip at frame 0 and mid-clip (a 170-unit body):

| weaponset | clip | frames | Δ frame 0 | Δ mid |
|---|---|---:|---:|---:|
| `000` unarmed | `c3/0002/000/401.c3` | 31 | — | — |
| `410` | `c3/0002/410/401.c3` | 30 | 61.6 | 115.3 |
| `490` | `c3/0002/490/401.c3` | 30 | 60.7 | 41.6 |
| `500` bow | `c3/0002/500/401.c3` | 30 | 65.7 | 44.0 |
| `510` | `c3/0002/510/401.c3` | 30 | 65.0 | 44.5 |
| `560` | `c3/0002/560/401.c3` | 30 | 67.3 | 118.0 |
| `611` | `c3/0002/611/401.c3` | 30 | 69.0 | 100.3 |
| `741` | `c3/0002/741/401.c3` | 30 | 61.8 | 117.2 |
| `756` | `c3/0002/756/401.c3` | 29 | 55.1 | 162.6 |

### 4.2 The 123 weaponsets, and the 11 folders they alias to — VERIFIED

Shape `2` ships 123 weaponsets. They alias down to eleven motion folders
(`--validate`, and `tools/anim.py --clip` shows the resolved path):

| folder | weaponsets that use it |
|---|---|
| `000` | `000` (unarmed) |
| `410` | `360 410 420 421 422 430 440 450 460 480 481` |
| `490` | `490` |
| `500` | `370 500` |
| `510` | `350 510 540` |
| `560` | `530 560 561 562 580` |
| `601` | `601` |
| `611` | 78 of the 81 `6XY` codes |
| `612` | `672` |
| `741` | `380 700 736 741 742 743 744 745 746 748 749` |
| `756` | `735 751 753 754 756 757 758` |

### 4.3 What the code means

* **`000`** — unarmed. **VERIFIED**: it is the only weaponset every shape ships
  for every action, and it is the universal fallback.
* **a 3-digit weapon type** (`350 360 370 380 410 420 421 422 430 440 450 460
  480 481 490 500 510 530 540 560 561 562 580`) — a single equipped weapon.
  **VERIFIED**: those 23 codes are exactly `weapon.ini`'s one- and two-handed
  type prefixes.
* **`6XY`** — two weapons at once. 81 codes, a full 9 × 9 grid (`6X0` never
  occurs), plus the odd `601`. The same `6XY` block appears in
  `ini/WeaponEffect.ini` and `ini/ActionDelay.ini`, and `ini/WeaponCombin.ini`
  lists legal pairs (`410.000`, `420.000`, `410.410`). **UNKNOWN**: what the
  family digits X and Y index. Nothing in the shipped data spells out the
  mapping, and 78 of the 81 resolve to the same folder anyway, so a viewer
  loses almost nothing by falling back to the right hand's type.
  `anim.AnimDB.weaponset()` does exactly that.
* **`7XY`** — 17 codes, and the arithmetic is exact: **`7` followed by the
  first two digits of a weapon type**. `700`←`000`, `735`←`350`, `736`←`360`,
  `741`←`410`, `742`←`420`, `743`←`430`, `744`←`440`, `745`←`450`, `746`←`460`,
  `748`←`480`, `749`←`490`, `751`←`510`, `753`←`530`, `754`←`540`, `756`←`560`,
  `757`←`570`, `758`←`580`. All 17 shipped codes fit and no code outside the
  pattern exists — **VERIFIED arithmetic**. What the configuration *is* is
  **INFERRED**: the natural reading is "that weapon type plus an off-hand item
  (shield)", because `ActionDelay.ini` carries only the one-handed subset
  (`700`, `741`–`749`) while `3dmotion.ini` also has the two-handed ones.

### 4.4 Resolution, with fallback — INFERRED

The matcher is in the packed exe. This chain is the only one that leaves no
hole in the shipped grid, and `--validate` section 2 confirms it:

```
1. <distance><shape><weaponset><action>     (jump rows only)
2. <shape><weaponset><action>
3. <shape>000<action>                        drop the weapon
4. <shape><weaponset>100                     fall back to that set's idle
5. <shape>000100                             the universal idle
```

Measured over the four player bodies × their 123 weaponsets × the 75 identified
actions = **36,900 lookups, 0 unresolved, 32,014 exact**. Across every shape in
the file (295 of them) × {idle, walk, run, jump, attack} using each shape's own
default weaponset: **1,460 resolved, 15 unresolved** — three NPC-only shapes
(`280`, `281`, `9`) that ship no locomotion at all.

---

## 5. Frame rate — the honest answer is *not determinable*

**No ini file and no clean DLL states the frame interval for character
motion.** `graphic.dll` exposes only `Phy_SetFrame(phy, frame)` and
`Phy_NextFrame(phy, delta)` — both take a frame *index*, not a time
(`0x5C140`, `0x5BBC0`) — and `Role3D!Role::SetFrame` (`0x9B00`) just forwards
an integer to every mesh. The clock lives in `ImConquer.exe`.

Three candidates, with their evidence:

| candidate | fps | evidence |
|---:|---:|---|
| **41 ms** | 24.4 | Every one of the **6,051** `Action3DEffect.ini` rows for the swing actions `401`/`402`/`403` and the **375** for the cast actions `900`/`901`/`903` names an effect whose `3DEffect.ini FrameInterval` is exactly **41**, with no exceptions. Those effects are spawned by, and frame-locked to, the body motion. 41 ms is also the plurality of the whole `3DEffect.ini` (908 of 2,240 sections) and is the 3DSMax film rate these assets were authored at. |
| 33 ms | 30.3 | `graphic.dll` divides the `STEP1`/`STEP2` UV-scroll rates by the literal **33.0** (`RVA 0x5B44C`, `0x5B4DD`), which is the engine's own notion of a frame time; and 33 is the default `FrameInterval` in `ini/3DEffect.json`. |
| 50 ms | 20 | `sound/walk.wav` is 1.032 s against the 20-frame walk clip (51.6 ms/frame), and the legacy 2D `ini/effect.ini` uses `FrameInterval=50` throughout. |

Counter-evidence for 41: the archer-pose rows (`501`/`510`) use 35 ms and the
free-running auras (`999`) are mostly 40 ms — so `FrameInterval` is per-effect
authoring, not a global constant. But those are all *loops* with nothing to
sync to; the action-locked ones are uniform.

**`tools/anim.py` defaults to 41 ms and takes `--interval`.**
`DEFAULT_FRAME_INTERVAL_MS` is a single constant. The viewer should expose a
speed control; do not hard-code it in the renderer.

At 41 ms the shipped clip lengths come out as: idle 16 frames = 656 ms, walk 20
= 820 ms (two footfalls), run 8 + 8 = 656 ms (two steps), jump 25 = 1,025 ms,
swing 30 = 1,230 ms, die 20 = 820 ms.

Effect layers keep **their own** `FrameInterval` from `3DEffect.ini` and run on
their own clock (`docs/effects.md` §8) — an aura at 80 ms/frame over a body at
41 ms/frame is normal and correct.

---

## 6. Loop model — VERIFIED by measurement

Three kinds, decided per clip. `tools/anim.py:Clip.classify()` measures them;
`Clip.loop` returns the table's answer where there is one, because a swing
whose end pose happens to land near its start is still a one-shot.

Let `wrap` = the largest matrix-element difference between the last key and the
first, and `maxstep` = the largest between consecutive keys, sampled over ~12
bones.

| kind | test | how to play |
|---|---|---|
| **closed** | `wrap < 0.05 × maxstep` — the last frame *duplicates* the first | loop over frames `[0, N−2]`; frame `N−1` is a copy |
| **cyclic** | `wrap ≤ 1.25 × maxstep` — the last frame flows into the first | loop over `[0, N−1]` |
| **oneshot** | otherwise | play once, then chain (§6.1) or hold the last frame |

Measured on body `002135000`, weaponset `000` (`--validate` section 4):

| clip | n | wrap | maxstep | kind |
|---|---:|---:|---:|---|
| `100.c3` idle | 16 | 4.41 | 5.37 | cyclic |
| `110.c3` walk | 20 | 36.14 | 42.89 | cyclic |
| `115.c3` walk 2 | 20 | 8.33 | 14.04 | cyclic |
| `120.c3` run L | 8 | 124.39 | 44.25 | oneshot → chains |
| `121.c3` run R | 8 | 129.08 | 43.60 | oneshot → chains |
| `130.c3` jump | 25 | **0.05** | 70.30 | closed |
| `131.c3` leap | 20 | 17.35 | 168.60 | one-shot in practice |
| `210.c3` kneel | 20 | 0.00 | 1.26 | closed |
| `230.c3` | 38 | 238.46 | 45.07 | oneshot → `231` |
| `270.c3` lie down | 40 | 204.18 | 60.88 | oneshot → `271` |
| `320.c3` hurt | 16 | 0.00 | 121.58 | closed |
| `330.c3` die | 20 | 229.99 | 79.61 | oneshot → `331` |
| `401.c3` swing | 31 | 34.11 | 78.79 | one-shot (table) |
| `901.c3` intone | 16 | 5.05 | 5.85 | cyclic |
| `903.c3` cast | 20 | 243.69 | 217.01 | one-shot (table) |

Note `130`: `wrap = 0.05` means the 25th frame is a byte-for-byte copy of the
first. Play `[0, 23]` or you will hold the start pose for two frame times.

### 6.1 The run cycle is two clips — VERIFIED

`120` (left foot) and `121` (right foot) are 8-frame **half** cycles. Neither
loops on itself; they alternate. Same for `125`/`126`.

Cross-clip continuity, expressed as the seam step divided by the largest step
*inside* the clip (`--validate` section 7). ≤ 1.0 means the seam is
indistinguishable from an ordinary frame advance:

| body | `120`→`121` | `120`→`120` | `125`→`126` | `125`→`125` |
|---|---:|---:|---:|---:|
| 001 | **0.66** | 1.26 | **0.60** | 1.42 |
| 002 | **0.97** | 2.81 | **0.74** | 2.17 |
| 003 | **0.71** | 0.65 | **0.72** | 1.55 |
| 004 | **0.28** | 0.47 | **1.17** | 1.98 |

Walk `110` loops on itself (`110`→`110` ratio 0.09–0.98 across the four
bodies) and contains two footfalls in its 20 frames. `110` and `115` are two
independent walk styles, **not** a chain — the `110`→`115` seam is 16.7× a
normal step.

`ActionSound.ini` corroborates: `120 = runL.wav`, `121 = runR.wav`, so one
footstep sound per clip.

### 6.2 The chain table

`anim.Action.chain` and `AnimDB.sequence()`:

```
120 <-> 121        run cycle
125 <-> 126        run 2 cycle
122 <-> 123        aliases of 120/121
230  -> 231        emote -> hold
270  -> 271        lie down -> lying
330  -> 331        die -> corpse   (and 332/333, 334/335, 336/337,
340  -> 341                          342/343, 344/345, 346/347, 370/371)
```

---

## 7. Root motion

### 7.1 Locomotion is animated in place — VERIFIED

Measured on `002135000` (`--validate` section 5). `dx`/`dy` are the horizontal
travel of the skinned track's bone 0 over the whole clip; `rise` is the travel
of the posed body's centroid height:

| action | dx | dy | rise | lowest point |
|---|---:|---:|---:|---:|
| `100` idle | 0.00 | 0.50 | 0.10 | −0.04 |
| `110` walk | 0.00 | 0.00 | 0.89 | −1.87 |
| `115` walk 2 | 0.00 | 0.00 | 0.95 | −1.75 |
| `120` run L | 0.00 | 0.00 | 4.31 | 0.40 |
| `121` run R | 0.00 | 0.00 | 4.93 | −0.36 |
| `130` jump | 0.00 | 0.00 | **19.47** | −4.93 |
| `131` leap | 0.00 | 13.10 | **70.58** | −1.21 |
| `401` swing | 3.67 | 20.05 | 8.81 | −0.20 |
| `330` die | 0.00 | 0.00 | 101.04 | −6.10 |

**Walk and run do not translate the character at all.** The client moves the
role's world matrix; the clip supplies only the leg cycle. A viewer plays them
on the spot and is correct.

### 7.2 The jumps carry their vertical arc — VERIFIED

`--validate` section 6, centroid height of the posed body:

| body | action | start | apex | @frame | end | rise | return |
|---|---|---:|---:|---:|---:|---:|---:|
| 001 | `130` | 78.8 | 91.3 | 14 | 77.0 | 12.5 | 1.8 |
| 001 | `131` | 96.2 | 101.9 | 17 | 98.1 | 5.7 | 1.9 |
| 002 | `130` | 91.7 | 110.8 | 20 | 91.7 | 19.1 | 0.0 |
| 002 | `131` | 94.0 | **153.6** | 9 | 90.8 | **59.6** | 3.2 |
| 003 | `130` | 75.6 | 94.1 | 16 | 75.6 | 18.5 | 0.0 |
| 004 | `130`/`131` | — | — | — | — | rises and returns | — |

Every one rises and comes back. `131` on body 002 is the textbook case: a
crouch (94 → 85), a launch, an apex at frame 9 nearly 60 units up, a landing
crouch (82), and a recovery. **Horizontal** travel is *not* baked — the client
lerps the role between the two map tiles over the clip's duration.

That is why the distance-tiered jump keys (§2.3) exist for shapes `1001`–`1004`:
a longer jump needs a taller arc, and the arc is in the clip.

### 7.3 `ini/ActionCtrl.ini` is **not** the run/jump root motion — VERIFIED

The brief expected a time→distance curve here. It is one, but it is nowhere
near locomotion.

30 sections, key `<shape4><weaponset3><action3>` with `0999` as the shape
wildcard. Every field:

```ini
[0148000340]
Section=5              ; number of TimePercent/MovePercent pairs
TimePercent0=10        MovePercent0=2
TimePercent1=13        MovePercent1=15
TimePercent2=25        MovePercent2=40
TimePercent3=35        MovePercent3=90
TimePercent4=100       MovePercent4=100
Visible=1
```

Two distinct curves in the whole file:

| curve | used by |
|---|---|
| `(10,2) (13,15) (25,40) (35,90) (100,100)` | 25 sections: shapes `0148`, `0188`, `0348`, `0548`, `0748` × actions `340 342 344 346 370` |
| `(20,2) (23,15) (35,40) (70,90) (100,100)` | 5 sections: shape `0999` (wildcard) × the same five actions |

Both are **piecewise-linear `time % → move %`**, monotonic, front-loaded: 90 %
of the movement happens in the first 35 % (or 70 %) of the elapsed time, then
it settles.

Facts that pin down what it is *not*:

* Every action it covers — `340`, `342`, `344`, `346`, `370` — is a **death**
  (§3.1). There is no row for `110`, `120`, `130` or `401`.
* The five specific shapes `148 / 188 / 348 / 548 / 748` all resolve to the
  *same* monster body, `c3/monster/148/…`, and that body's death clip is
  animated **in place** (bone 0 moves 2.2 in x, 9.2 in y over 17 frames) —
  unlike monster 103's, which slides 121 units. So `ActionCtrl` exists exactly
  for the family whose death does *not* translate itself.
* Nothing named `ActionCtrl`, `TimePercent` or `MovePercent` appears in
  `graphic.dll`, `GraphicData.dll`, `Role3D.dll` or `TqPackage.dll`. The
  consumer is the packed exe.

**INFERRED reading:** it is the knock-back / slide curve applied while a
corpse falls — `MovePercent` is the fraction of a knock-back distance travelled
at that fraction of the death animation's duration, and `Visible` says the role
stays drawn throughout. A second reading, that it *retimes* the clip
(`MovePercent` = fraction of the animation's frames), fits the numbers equally
well and cannot be told apart from the shipped data. **State it as unresolved;
neither reading affects idle / walk / run / jump / swing.**

There is one further clue worth chasing later: `ini/monster.json` carries an
`actResCtrl` field (0 on 311 monsters, 1 on 62, 24 on one). The name reads as
"action resource control" and is very likely the per-monster gate for this
table — but `monster.json` in this build has `bodyType: 0` on every row, so the
shapes cannot be joined up. **UNKNOWN.**

`tools/anim.py` decodes the file (`load_action_ctrl`, `ActionCtrl.move_at(t)`)
and attaches the matching rule to every `Clip`, so it is there when the meaning
is settled.

---

## 8. Binding a motion set over a body

This is `docs/attachment.md` §3 and §8.1; restated only because it is the step
most likely to be got wrong.

1. Load the body mesh (`armor.ini[app].Mesh0` → `3dobj.ini` →
   `c3/mesh/<id>.c3`). Collect its `PHY` chunks into one list and its `MOTI`
   chunks into another, **separately**, and pair them by index. 447 of 2,002
   loose `.c3` files store all `PHY`s then all `MOTI`s; adjacency pairing
   produces nothing on those. `attach.PartMesh.parse` does this.
2. Load the action motion `.c3`. **Whether it is "motion-only" is a property of
   the install, not of the format** — MEASURED 2026-09-25 on the live CCO
   install (`C:\Program Files\Classic Conquer 2.0`, the default root) against
   client 5517, on the same logical path `c3/0002/000/100.c3`, both served out
   of `c3.wdf`:

   | | 5517 | CCO (default root) |
   |---|---:|---:|
   | size | 39,232 B | 140,252 B |
   | `PHY` chunks | 0 | 4 |
   | `MOTI` chunks | 4 (ZKEY) | 4 (RAW) |
   | `CAME` chunks | 0 | 1 |

   On CCO that file carries a full reference body — `v_body` at 562 vertices /
   720 faces plus the three sockets `v_armet`, `v_l_weapon`, `v_r_weapon` (24 /
   6 / 6 vertices) — and its four `MOTI`s are one 84-bone track and three
   1-bone socket tracks, 16 keys each. **So "`MOTI` chunks and no `PHY`" is
   true on 5517 and false on CCO**, and reading step 1's body chunks out of an
   action file would silently succeed there.

   Corpus-wide, over the 1,664 distinct files named by the seven-wide
   (shape `1`–`4`) `3dmotion.ini` keys, **1,120 are present on CCO and 1,043 of
   those carry at least one `PHY`** — 1,041 with exactly 4 and 2 with 5; only
   77 are motion-only. Bind by ordinal regardless (step 3): the extra `PHY`s
   are a reference copy of the body, not the body you are posing.

   > `docs/attachment.md` §3's "343 motion-only" is **not** in conflict: it
   > counts the 2,002 **loose** `.c3` files, and these motion sets are served
   > from the WDF archives. Resolve through `AssetRoot`, not `os.walk`, or the
   > population is a different one.
3. Bind by ordinal: `bodyMesh.phy[i].motion = motionSet.Get(i)`
   (`C3Mesh::SetMotion`, `graphic.dll 0x277C0`, loop at `0x27818`). There is no
   name matching. The engine rejects a motion set with fewer entries than the
   mesh has phys (`0x277F5`).
4. Skin `v_body` with its bound track; evaluate each socket chunk as
   `Motion_GetMatrix(motion[idx], bone = 0, frame)`.

**Verified across the shipped data** (`anim --validate` section 3): 299 clips
loaded over the four player bodies, **299 aligned, 0 misaligned**. One motion
file named by the ini is absent from this install (`003/919` →
`c3/0003/741/919.c3`).

The chunk order differs *between* bodies — `002135000` is
`[v_body, v_armet, v_l_weapon, v_r_weapon]` while `001`/`003`/`004` are
`[v_armet, v_l_weapon, v_r_weapon, v_body]` — and the motion sets are authored
to match. Never assume a fixed index.

`anim.Clip.socket(dumy, frame)` wraps step 4 and returns the socket matrix with
the *action* motion bound, which is what makes an equipped weapon follow the
swing instead of hanging at the idle pose.

---

## 9. Super weapon effects — resolution

### 9.1 It is a table, not a filename convention — VERIFIED

The question was whether `c3/effect/blade/<appearance>.c3` is found by name.
It is not. Every one of those meshes is reached through the ordinary effect
pipeline:

```
Action3DEffect.ini  999.999.410.099   =  410099        <- effect NAME
3DEffect.ini        [410099]  EffectId0 = 2205
3DEffectObj.ini     2205 = c3/effect/blade/410099.C3   <- the mesh
3dtexture.ini       3580 = c3/effect/blade/410099.dds  <- the texture
```

Numbers (`superfx --validate` section 2):

* `3DEffectObj.ini` holds **82** entries under `c3/effect/blade/`, object ids
  330 … 2225.
* **75** of them have a file stem that looks like an appearance id; **30** of
  those stems are literal `weapon.ini` sections.
* **71** weapon appearances reach a `blade/` mesh through the table.

So the stem matching the weapon appearance is an authoring habit. Nothing looks
it up by name, and the `-f` variants (`410229-f.C3`) and the multi-part
sub-directories (`c3/effect/blade/410239/1.c3` … `3.c3`, reached by
`3DEffect.ini[410239]`'s three layers) could not be found by a filename rule at
all.

### 9.2 Which equipment carries one — VERIFIED

`ini/Action3DEffect.ini`, action `999` (any action → always on):

| | |
|---|---:|
| rows with action `999` and a real effect | **820** |
| distinct appearances | **819** |
| … that are literal `weapon.ini` sections | **796** |
| … that are not (garment / cosmetic auras) | **23** |
| super effects that fully resolve to mesh + texture | **816 of 816** |
| layer meshes that open | **975 of 975** |
| drawable parts across them | **5,262** |
| parts with no `MOTI` | **0** |

Quality digit of the 796 weapon appearances: **646 end in `9`**, 134 end in `0`
(those come in through the group-3 wildcard rows, e.g.
`999.999.900.999 = 900x99` covering every shield), and the remaining 16 are
scattered. This refines `docs/effects.md` §2.2: the *trail* is strictly
quality 6–9, the *aura* is mostly quality 9 plus blanket wildcards.

28 weapon types are covered: `350 360 370 380 410 420 421 422 430 440 450 460
480 481 490 500 510 530 540 560 561 580 800 801 802 803 804 900`.

### 9.3 `blade/` is one of 29 families — VERIFIED

`py -3 tools/superfx.py --families`. Layer counts over the action-`999` rules:

| family | layers | effects | appearances | in `weapon.ini` |
|---|---:|---:|---:|---:|
| `weapon` | 142 | 14 | 18 | 142 |
| `coat` | 113 | 11 | 17 | 14 |
| `blade` | 102 | 37 | 71 | 102 |
| `whip.club` | 91 | 42 | 91 | 91 |
| `900` (shields) | 70 | 10 | 70 | 70 |
| `bow` | 56 | 23 | 56 | 56 |
| `hook` `axe` `hammer` `pestle` `bigblade` `bigaxe` `bighammer` `lance` `stick` `halberd` | 34 each | 21 each | 34 each | 34 each |
| `dagger` | 32 | 21 | 32 | 32 |
| `lightsword` `onecolorsword` `swordeffect` `blinksword` `icesword` `firesword` `7colorsword` `peri` `other` `body` | 1–27 | | | |

The families whose appearances are **not** `weapon.ini` rows — `coat`, `body`,
and two `graphics/cosmetics/weapon/...` paths — are garment auras and anchor to
the body, not to a weapon socket. Everything else in the list is a weapon
family and uses §10.

---

## 10. Super weapon effects — the anchor

### 10.1 The formula

```
p_world =  p_bind
        x  M_fxBone( vertex.bone0, frame_fx )     the effect part's own MOTI
        x  M_offset                               translate(OffsetX,Y,Z)
        x  M_socket                               v_r_weapon / v_l_weapon
        x  M_role
```

Row-vector throughout, exactly as `docs/attachment.md` §4. `M_socket` is
`Motion_GetMatrix(bodyMotion[FindPhyByName("v_r_weapon")], bone = 0,
frame_body)` — **the same matrix the weapon itself uses**, evaluated on the
body's *current action motion* at the body's *current frame*, so the glow
swings with the sword.

`p_bind` is the vertex position after the `PHY` chunk's own 4×4 has been
applied at load time (`Phy_Load`, `graphic.dll 0x5A735`).
`frame_fx` runs on the **effect's** clock (`3DEffect.ini FrameInterval`,
`docs/effects.md` §8), not the body's.

`tools/superfx.py:SuperFxDB.anchor()` returns `M_offset × M_socket × M_role`;
`world_vertices()` applies the rest.

### 10.2 The critical negative: do NOT include the weapon's own bone-0 matrix

The weapon renders as

```
p_world = p_bind x M_weaponBone0 x M_socket x M_role
```

where `M_weaponBone0` carries the weapon's scale and its 90° axis correction
(`docs/attachment.md` §5–§6). **The effect does not get that matrix.** It is a
sibling of the weapon at the socket, not a child of it.

Proof, `superfx --validate` section 3. The raw effect geometry already occupies
the space the weapon reaches *after* its own bone-0 matrix — measured on
`410009`, whose weapon mesh's own bone-0 matrix is a uniform **0.252** scale:

| | longest span |
|---|---:|
| weapon `c3/mesh/410000.c3`, placed | **90.0** |
| effect `c3/effect/blade/410009.C3` × `M_socket` | **111.8** ← matches |
| effect × `M_weaponBone0` × `M_socket` | **28.1** ← a quarter size, buried in the grip |

And the ends line up. Along the weapon's longest axis, on `410009`:
effect `y[−13.1, 98.7]`, weapon `y[−12.6, 77.4]` — **the hilt ends coincide to
0.4 units** and the glow overshoots the tip by 21.

### 10.3 The whole 410 Blade family

`superfx --validate` section 3. `d_hilt` is the gap between the effect's and the
weapon's near ends along the weapon's longest axis; `d_tip` the gap at the far
end. Positive `d_tip` = the glow reaches past the blade.

| effect mesh | weapon mesh | eff_len | wpn_len | ratio | d_hilt | d_tip |
|---|---|---:|---:|---:|---:|---:|
| `410009.C3` | `410000.c3` | 111.8 | 90.0 | 1.24 | 0.4 | +21.3 |
| `410019.C3` | `410010.c3` | 111.9 | 95.9 | 1.17 | 1.3 | +14.7 |
| `410029.C3` | `410020.c3` | 117.3 | 99.2 | 1.18 | 2.7 | +15.4 |
| `410039.C3` | `410030.c3` | 122.7 | 122.5 | 1.00 | 25.2 | +25.5 |
| `410049.C3` | `410040.c3` | 122.7 | 89.4 | 1.37 | 3.9 | +29.5 |
| `410059.C3` | `410050.c3` | 122.7 | 100.4 | 1.22 | 2.2 | +20.2 |
| `410069.C3` | `410060.c3` | 122.7 | 81.4 | 1.51 | 1.2 | +40.1 |
| `410079.C3` | `410070.c3` | 134.8 | 107.1 | 1.26 | 0.4 | +28.1 |
| `410089.C3` | `410080.c3` | 122.7 | 98.7 | 1.24 | 5.4 | +29.5 |
| `410099.C3` | `410090.c3` | 122.7 | 95.1 | 1.29 | 2.0 | +29.6 |
| `410109.C3` | `410100.c3` | 132.1 | 121.5 | 1.09 | 11.3 | +22.0 |
| `410119.C3` | `410110.c3` | 115.2 | 98.6 | 1.17 | 7.3 | +23.8 |
| `410129.C3` | `410120.c3` | 190.3 | 115.1 | 1.65 | 33.3 | +41.9 |
| `410139.C3` | `410130.c3` | 293.8 | 119.2 | 2.47 | 87.0 | +87.7 |
| `410149.C3` | `410140.c3` | 219.5 | 113.7 | 1.93 | 47.1 | +58.7 |
| `410159.C3` | `410150.c3` | 255.1 | 122.4 | 2.08 | 76.8 | +55.9 |
| `410169.C3` | `410160.c3` | 218.0 | 110.5 | 1.97 | 35.6 | +71.9 |
| `410179.C3` | `410170.c3` | 174.7 | 110.1 | 1.59 | 19.9 | +44.8 |
| `410189.C3` | `410180.c3` | 158.9 | 117.7 | 1.35 | 2.5 | +38.8 |
| `410199.C3` | `410190.c3` | 147.6 | 119.7 | 1.23 | 9.8 | +37.7 |
| `410209.C3` | `410200.c3` | 180.7 | 131.1 | 1.38 | 4.2 | +41.0 |
| `410219-f.C3` | `410210.c3` | 180.6 | 139.5 | 1.29 | 3.2 | +37.9 |
| `410229-f.C3` | `410220.c3` | 172.3 | 139.2 | 1.24 | 10.7 | +43.8 |

The later, flashier appearances (`410129` upward) have bigger, more sprawling
glows — that is the art, not a placement error.

### 10.4 It generalises to every family

Over 359 distinct (effect mesh, weapon mesh) pairs, `superfx --validate`
section 3:

| family | n | median ratio | p10 | p90 | median `d_hilt` | median `d_tip` |
|---|---:|---:|---:|---:|---:|---:|
| `blade` | 35 | 1.24 | 0.79 | 1.97 | 11.3 | +29.5 |
| `whip.club` | 26 | 1.27 | 1.07 | 1.44 | 10.1 | +21.2 |
| `axe` | 23 | 1.13 | 0.89 | 1.48 | 18.3 | +25.4 |
| `bow` | 23 | 1.85 | 1.62 | 2.06 | 47.4 | +61.1 |
| `lance` | 23 | 0.30 | 0.27 | 0.32 | 201.1 | −8.9 |
| `bigblade` | 21 | 0.61 | 0.51 | 1.03 | 140.8 | +37.4 |
| `stick` | 21 | 1.22 | 1.12 | 1.27 | 24.6 | +25.0 |
| `hammer` | 19 | 1.19 | 0.85 | 1.69 | 27.1 | +25.2 |
| `hook` | 18 | 1.38 | 1.12 | 1.75 | 12.9 | +35.2 |
| `dagger` | 18 | 1.19 | 1.05 | 1.95 | 4.4 | +12.9 |
| `bigaxe` | 18 | 0.51 | 0.44 | 0.59 | 178.3 | +37.7 |
| `halberd` | 18 | 0.28 | 0.24 | 0.29 | 232.9 | +15.8 |
| `bighammer` | 13 | 0.51 | 0.42 | 0.69 | 137.9 | +34.5 |
| `onecolorsword` | 12 | 1.31 | 1.13 | 1.40 | 2.4 | +35.0 |
| `blinksword` | 9 | 1.32 | 0.97 | 1.38 | 1.4 | +36.4 |
| `900` shields | 9 | 1.45 | 1.14 | 1.61 | 14.8 | +21.1 |
| `icesword` | 6 | 1.14 | 1.04 | 1.31 | 4.6 | +24.8 |
| all families | 359 | **1.17** | 0.31 | 1.71 | 22.7 | — |

Read the two long-weapon groups carefully — they are the strongest
confirmation in the table. `lance`, `halberd`, `bigaxe`, `bigblade`,
`bighammer` are polearms and two-handers: their glows are **0.28–0.62** of the
weapon's length and sit **138–233 units** from the butt of the shaft, yet their
`d_tip` is only **+16 to +38**. The glow is a *head* glow on a long haft,
aligned to the business end. That only works out if the effect and the weapon
are in the same local frame — which is the claim.

Under the wrong composition (`× M_weaponBone0`), across the 243 weapons whose
own bone-0 matrix is not unit scale, the median span ratio drops from 1.17 to
**0.80** with a p10 of **0.21**.

### 10.5 Placement on a real character

`superfx --validate` section 6 — `002135000` (height 170.4) at `v_r_weapon`,
idle motion bound, frame 0:

```
410009  effect 410009    x[ -29.6,   3.1] y[-103.4,  31.0] up[ 43.9, 177.5]
        weapon 410000.c3 x[ -16.9, -10.2] y[ -68.2,  17.2] up[ 90.1, 125.1]
        centre separation 10.7 units

410099  effect 410099    x[ -29.7,   3.1] y[-112.9,  32.1] up[ 43.6, 180.4]
        weapon 410090.c3 x[ -18.1,  -8.3] y[ -65.9,  20.6] up[ 87.9, 135.8]
        centre separation 17.7 units

560009  effect 560029    x[ -58.4,  16.8] y[ -98.7, -32.9] up[ 83.2, 165.0]
        weapon 560000.c3 x[ -22.0,   7.8] y[ -93.4, 129.4] up[ 51.1, 137.5]
        centre separation 83.8 units      <- a spear; the glow is on the head
```

### 10.6 What is still unknown

* **Which hand.** The lookup is per appearance, so a weapon in the left hand
  gets the same effect at `v_l_weapon`. That both weapons resolve independently
  is INFERRED (`docs/effects.md` §2.4), not read from code.
* **`ColorEnable`.** 108 `3DEffect.ini` sections set it; the owner's colour
  that it tints from is supplied by the exe. Ignore it for now.
* **Whether the aura is suppressed during some actions.** `Action3DEffect`'s
  `999` action means "any action" by the file's own wildcard convention, and
  the hurt/die group is suppressed only for the *trail* rows. INFERRED: keep
  the aura on at all times.

---

## 11. Playback, end to end

What a renderer does per frame. Steps 1–5 are setup; 6–9 run every frame.

```
SETUP
1.  shape      = int(bodyAppearance[:3])                    -> "2"
    weaponset  = weapon ? weapon[:-3] : "000"               -> "410"
    path       = 3dmotion[shape + weaponset + action]       (fallback chain 4.4)
2.  bodyMesh   = parse c3/mesh/<bodyMesh0>.c3   (PHY list and MOTI list, ordinal)
    motionSet  = parse <path>                   (MOTI only)
    require len(motionSet) >= len(bodyMesh.phys)
3.  N          = motionSet[0].frameCount
    loopKind   = table, else measured (section 6)
    playLen    = loopKind == closed ? N-1 : N
4.  weaponMesh = WeaponMotion[app + action] || WeaponMotion[app + "999"]
                 || weapon.ini[app].Mesh0                   (docs/effects.md 4.5)
5.  aura       = Action3DEffect[999.999.<type>.<sub>]       (section 9)
    for each layer: load 3DEffectObj[EffectId] and 3dtexture[TextureId]

PER FRAME  (t = elapsed ms)
6.  fBody = floor(t / 41) % playLen                          section 5
    if loopKind == oneshot and t/41 >= playLen:
        if action has a chain partner: switch to it, reset t
        else: hold frame playLen-1
    if action is a run half-cycle: alternate 120/121 each time t wraps

7.  BODY.  for each drawable chunk c of bodyMesh:
        motion = motionSet[c.index]
        for each vertex v:
            p = w0 * (v.p x motion[v.bone0][fBody])
              + (1-w0) * (v.p x motion[v.bone1][fBody])      two influences,
                                                             w1 = 1 - w0

8.  WEAPON.  idx    = bodyMesh.FindPhyByName("v_r_weapon")   stricmp, 0x266D0
             Msock  = motionSet[idx].matrix(bone=0, fBody)
             Mown   = weaponMesh.motion[0].matrix(0, fWeapon)
             world  = Mown x Msock
             (fWeapon shares the body clock; a weapon has no external motion
              set, only the MOTI in its own mesh -- docs/attachment.md 7.3)

9.  AURA.    fFx    = floor(t / aura.FrameInterval) % aura.frames
             anchor = translate(aura.Offset) x Msock          <- NOT x Mown
             for each layer, for each part p of its mesh:
                 visible = Key_ProcessDraw(p.keys, fFx)       docs/effects.md 6.4
                 alpha   = Key_ProcessAlpha(p.keys, fFx)
                 uv      = flipbook or STEP scroll
                 world   = p.motion.matrix(v.bone0, fFx) x anchor
                 draw with blend (ASB, ADB)
```

Coordinate conversion is unchanged from the rest of the project: `(x, y, −z)`,
UV `(u, 1 − v)`, keep index order (`docs/modding.md` §9.7).
`attach.to_render(M)` conjugates a row-vector 4×4 into the viewer's
column-major GL order and is what `parts.Anchor.matrix` already emits.

### 11.1 Suggested UI

The user asked for "swing, run and jump". The minimum set:

| label | action(s) | play as |
|---|---|---|
| Idle | `100` | loop |
| Stance | `105` | loop |
| Walk | `110` | loop |
| Run | `120` ↔ `121` | alternating loop |
| Jump | `130` | once, then back to idle |
| Leap | `131` | once, then back to idle |
| Swing 1 / 2 / 3 | `401` `402` `403` | once each; or cycle all three |
| Cast | `903` | once |
| Die | `330` → `331` | once, then hold |
| Sit / Kneel / Lie | `250` / `210` / `270`→`271` | loop / loop / once + hold |

Plus a frame-interval slider (default 41 ms) and a super-effect toggle.

---

## 12. Coverage

`py -3 tools/anim.py --validate` and `py -3 tools/superfx.py --validate`, on
this install. Both exit 0.

### Motion

| | |
|---|---:|
| `ini/3dmotion.ini` rows / distinct keys | 229,481 / **228,985** |
| keys accounted for by the six layouts | **228,985 (100 %)** |
| distinct motion files referenced | 3,260 |
| … actually present in this install | **2,160** (1,100 absent) |
| keys pointing at an absent file | 144,908 (63 %) |
| distinct shapes | 295 |
| player-body lookups (4 bodies × 123 weaponsets × 75 actions) | 36,900 |
| … resolved | **36,900 (100 %)**, 32,014 of them exact |
| every shape × {idle, walk, run, jump, attack} | 1,475 |
| … resolved | **1,460**, 15 missing on 3 NPC-only shapes |
| clips loaded over the four player bodies | 299 |
| … whose motion set covers the mesh's chunks | **299 (100 %)** |
| motion files named by the ini but absent from the install | 1 |
| loop classifications where the measurement disagrees with the table | 17, all deliberate (a swing is a one-shot even when its ends nearly meet) |

### Super effects

| | |
|---|---:|
| action-`999` rules with a real effect | 820 |
| distinct appearances | 819 (796 in `weapon.ini`) |
| super effects fully resolving to mesh + texture | **816 of 816 (100 %)** |
| layer meshes that open | **975 of 975 (100 %)** |
| drawable parts | 5,262 |
| parts with no `MOTI` | **0** |
| effect families | 29 |
| (effect mesh, weapon mesh) pairs measured for anchoring | 359 |
| median effect/weapon span ratio, correct anchor | **1.17** |
| … wrong anchor (× `M_weaponBone0`) | 0.80, p10 0.21 |

---

## 13. Verified vs inferred, at a glance

**VERIFIED**

* The `3dmotion.ini` key layouts, all six, accounting for 100 % of the keys.
* The jump-distance field: twelve tiers 10…120 selecting four clips.
* `ActionSound.ini` naming walk / run-L / run-R / jump / dig / hurt / die /
  attack, and therefore actions `110 115 120 121 125 126 130 131 290 320-323
  330-336 340-347 370 371 401 402 403`.
* `ActionMap3DEffect.ini`'s own header defining its key as shape/action/terrain
  and confirming `330` = die (`Feather` on a bird) and `120` = run (`RunDust`).
* Action `N` → `N+1` terminal-hold pairs, measured in posed space.
* The weaponset field changing the motion (41–163 units of pose difference on a
  170-unit body), and the 123 → 11 folder alias table.
* `7XY` = "7" + the first two digits of a weapon type — all 17 shipped codes fit
  exactly and nothing outside the pattern exists.
* Locomotion clips carry no horizontal root motion; the jumps carry their
  vertical arc.
* The run cycle being `120` ↔ `121` (seam ratio 0.28–1.17 versus 0.47–2.81 for
  self-looping).
* `ActionCtrl.ini`'s field set, its two curves, and that it covers **only**
  death actions on one monster family plus a wildcard.
* Ordinal `PHY`↔`MOTI` binding and `C3Mesh::SetMotion`'s behaviour
  (`graphic.dll 0x277C0` / `0x27818` / `0x277F5`).
* `Motion_GetMatrix(motion, bone, frame, out)` argument order and its
  element-wise lerp (`0x551A0`, thunk `0x26DE0`).
* `Phy_NextFrame` = `(current + delta) % frameCount` (`0x5BBC0`).
* Blade effects are reached by table (`Action3DEffect` → `3DEffect.ini` →
  `3DEffectObj.ini`), not by filename.
* The effect's geometry occupying the weapon's post-bone-0 local frame — hilt
  ends within 0.4 units on `410009`, and head-glow alignment on every polearm
  family.
* Every super-effect part carrying its own `MOTI` (5,262 of 5,262).

**INFERRED**

* The frame interval, 41 ms (§5). Three candidates; this is the best supported.
* The `3dmotion.ini` fallback chain (§4.4).
* `6XY` being a two-weapon combination code, and `7XY` being weapon + off-hand.
* `ActionCtrl.ini` being a knock-back/slide curve for a death whose clip does
  not translate itself — the retiming reading fits equally well.
* The attack cycle order 401 → 402 → 403.
* The aura anchoring to `v_l_weapon` when the weapon is in the left hand.
* Action meanings marked INFERRED in §3.1 (`105`, `210`, `230`, `250`, `270`,
  `300`, `305`, `310`, `501`, `510` and the alias rows).

**UNKNOWN**

* What selects shape `1001`–`1004` over `1`–`4`.
* The unit of the jump-distance field.
* The family digits of the `6XY` weaponset codes.
* 124 action codes that ship for a player body with no corroborating row in any
  table (§3.3).
* Whether `monster.json`'s `actResCtrl` is the gate for `ActionCtrl.ini` — the
  join column is stripped in this build.
* What blends two actions together at a transition. `Motion_GetMatrix_Blend`
  (`0x55480`) exists and `C3MotionTracker` holds two motions plus a weight, but
  the exe drives the cross-fade; only the single-motion branch is decoded
  (`docs/attachment.md` §11 already flags this).

---

## 14. Reproducing

```bash
py -3 tools/anim.py --keys
py -3 tools/anim.py --actions
py -3 tools/anim.py --actionctrl
py -3 tools/anim.py --clip 002135000 100
py -3 tools/anim.py --clip 002135000 401 --weapon 410009
py -3 tools/anim.py --clip 002135000 130 --as-shape 1001 --distance 120  # absent here
py -3 tools/anim.py --sequence 002135000 run
py -3 tools/anim.py --sequence 002135000 die
py -3 tools/anim.py --root-motion 002135000 131
py -3 tools/anim.py --validate

py -3 tools/superfx.py --weapon 410099
py -3 tools/superfx.py --weapon 560009
py -3 tools/superfx.py --anchor 002135000 410099
py -3 tools/superfx.py --anchor 002135000 410099 --slot l_weapon
py -3 tools/superfx.py --families
py -3 tools/superfx.py --validate
```

As modules:

```python
from anim import AnimDB
from superfx import SuperFxDB

adb  = AnimDB()                              # optional interval_ms=33
clip = adb.clip("002135000", "swing", weapon="410009")
clip.frame_count, clip.loop, clip.duration_ms
M    = clip.matrix(chunk=0, bone=7, frame=12)     # row-vector 4x4
S    = clip.socket("v_r_weapon", frame=12)        # the weapon socket, posed
run  = adb.sequence("002135000", "run")           # [Clip(120), Clip(121)]

sdb  = SuperFxDB()
sfx  = sdb.super_effect("410099")                 # -> SuperEffect
A    = sdb.anchor(clip.body, "r_weapon", 12, clip.motion, sfx.offset)
for x, y, z in sdb.world_vertices(sdb.mesh(sfx.layers[0].mesh), A, frame=3):
    ...
```
