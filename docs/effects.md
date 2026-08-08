# 3D effects — weapon linkage and playback

How the client decides which visual effect belongs to an equipped weapon, and
exactly how that effect is animated. Written so a renderer can be implemented
from this document alone.

Every claim is marked **VERIFIED** (proven against the real files, or read out of
`graphic.dll` / `GraphicData.dll` instruction by instruction, with the RVA cited)
or **INFERRED** (a working rule whose ground truth has not been read — the code
that would prove it is inside the Themida-packed `ImConquer.exe`).

Companion tool: **`tools/effects.py`** — module + CLI. Everything below is
reproducible with it. Machine-readable output: **`out/effects/linkage.json`** (§11).

> **Correction to `docs/modding.md` §9.9.** That section lists `MOTI` as
> undecoded and groups it with `MNEW`/`CCFL`. It does not belong there: `MNEW`
> and `CCFL` really are unreachable, but `MOTI`'s reader is the exported,
> unobfuscated `graphic.dll!Motion_Load` at RVA `0x557A0`. It is decoded in §6.2
> below and validated on 15,278 chunks. `SHAP`/`SMOT` likewise, and the three
> `C3Key` channels §9.6 kept opaque are decoded in §6.4.

```bash
py -3 tools/effects.py --weapon 410009     # every effect for one weapon, resolved
py -3 tools/effects.py --effect Flash4102  # one effect -> meshes, textures, timing
py -3 tools/effects.py --validate          # the parser proof (15,278 MOTI chunks)
py -3 tools/effects.py --coverage          # the numbers in §9
py -3 tools/effects.py --linkage           # writes out/effects/linkage.json
```

---

## 1. The answer in one page

**A weapon has three separate visual effects, and they come from three different
tables.**

| what you see | table | keyed by |
|---|---|---|
| the **swing trail** during an attack | `ini/Action3DEffect.ini`, action `401`/`402`/`403` | weapon appearance ID |
| the **permanent aura** on a high-quality weapon | `ini/Action3DEffect.ini`, action `999` (any action) | weapon appearance ID |
| the **impact spark** on the target when you hit or block | `ini/WeaponEffect.ini` | 3-digit weapon type |

All three yield an **effect name**, e.g. `Flash4102`, `410009`, `m-b02`. That
name is a section in `ini/3DEffect.ini`, which gives timing plus a list of
layers; each layer resolves to a **mesh** through `ini/3DEffectObj.ini` and a
**texture** through `ini/3dtexture.ini`.

```
weapon appearance 410009  (weapon.ini section, 6 digits: type 410 + style/quality 009)
   │
   ├─ Action3DEffect.ini  999.401.410.009 = Flash4102     ← swing trail
   ├─ Action3DEffect.ini  999.999.410.009 = 410009        ← aura
   └─ WeaponEffect.ini    [410] HitEffect=m-b02           ← impact spark
                                    │
                                    ▼
                          3DEffect.ini [Flash4102]
                            FrameInterval=41   LoopTime=1   Delay=0
                            EffectId0=4100  TextureId0=4102  ASB0=5  ADB0=6
                                    │              │
                3DEffectObj.ini 4100│              │4102 3dtexture.ini
                                    ▼              ▼
                       C3/Effect/flash/410000.C3   c3/effect/flash/4102.dds
                                    │
                                    ▼
                       SHAP + SMOT  (a 2-point blade line + a per-frame matrix)
```

**And the animation model is not skeletal.** An effect mesh is a handful of
4-vertex quads, each carrying its own `MOTI` track of one 4×4 matrix per frame,
plus three key channels (alpha / visibility / texture-cell). Trails are a
separate form entirely: a two-point line smeared into a ribbon. Both are now
fully decoded — see §6.

---

## 2. `ini/Action3DEffect.ini` — the weapon link — VERIFIED

8,926 rows, flat `key=value`, no sections. The key is **four dot-separated
3-digit groups**:

```
999 . 401 . 410 . 009  =  Flash4102
 │     │     └──┬──┘
 │     │        └────── the 6-digit equipment appearance ID, split 3 + 3
 │     └─────────────── action (motion) ID
 └───────────────────── character shape ID
```

`999` is the wildcard in every position (same convention the sibling file
documents in prose — see §3).

**Evidence for the field order.**

* Group 0 is `999` on all 8,926 rows — the shape wildcard.
* Group 1 takes the values `100, 300, 305, 310, 320, 330, 331, 340, 341, 401,
  402, 403, 501, 510, 900, 901, 903, 999` — exactly the action vocabulary that
  forms the last three digits of a `3dmotion.ini` key (§5).
* Groups 2+3 concatenated give **2,431 of 2,678 distinct values that are literal
  `weapon.ini` section names**. The 247 that are not are armour families
  (`135293` = ConquestArmor) reached through the same 6-digit item numbering.
* The effect chosen for `999.999.410.009` is literally named `410009` — the
  weapon's own appearance ID. Weapon auras are named after their weapon.

### 2.1 What each action selects

| action | meaning | typical effect |
|---|---|---|
| `999` | any action → **always-on aura** | `410009`, `410229-f`, `lightsword-m-3` |
| `401` `402` `403` | the three attack swings | `Flash4102`, `Flash42045`, … |
| `900` `901` `903` | skill / cast animations | `Flash42145`, … |
| `100` | idle — used by super-armour series `135`/`136`/`138`/`139` | `f-taoist`, `fighter-p`, `archer-p`, `w-taoist` |
| `501` `510` | archer poses | `archer-p` |
| `300` `305` `310` `320` `330` `331` `340` `341` | hurt / die / knock-down | always `none` — an explicit suppression |

`none` is a real value meaning *no effect*, not a missing row. `tools/effects.py`
normalises it to an empty string.

### 2.2 Which weapons get effects

Only high-quality weapons get a swing trail. **VERIFIED**: all 2,143 weapon
appearances that resolve to a trail end in **6, 7, 8 or 9** — the
refined/unique/elite/super quality tiers — and not one that ends in 0–5 does.
(The converse does not hold: 529 appearances ending in 6–9 still have no trail,
because their whole weapon type has no rows.)

Auras are looser. 646 of the 1,286 are on quality-9 appearances, but the rest
come from the seven **group-3 wildcard rows**, which are the only wildcards
outside group 0/1 in the whole file:

```
999.100.135.999 = f-taoist      999.501.138.999 = archer-p
999.100.136.999 = fighter-p     999.510.138.999 = archer-p
999.100.138.999 = archer-p      999.999.900.999 = 900x99
999.100.139.999 = w-taoist
```

Six are the idle auras on the super-armour series `135`/`136`/`138`/`139`; the
last is a blanket glow that applies to **every** shield appearance (type `900`,
560 of them). A lookup that ignores group-3 wildcards loses all of these.

The **flash mesh** is chosen by weapon *style*, the **texture** by quality tier:

```
999.401.410.009 = Flash4102 -> EffectId 4100 = C3/Effect/flash/410000.C3, Texture 4102
999.401.410.019 = Flash4104 -> EffectId 4101 = C3/Effect/flash/410010.C3, Texture 4102
999.401.410.029 = Flash4106 -> EffectId 4102 = C3/Effect/flash/410020.C3, Texture 4102
```

i.e. `C3/Effect/flash/<weaponAppearanceWithQuality0>.C3`. **VERIFIED** on the
`410` family; the naming holds across the `Flash*` block.

### 2.3 Duplicates

251 keys appear twice, always with an identical value. Deduplicate on
`(key, value)`; do not treat the second row as an override.

### 2.4 Lookup rule — INFERRED

The matcher lives in the packed exe. `tools/effects.py` probes every row and
takes the one with the **most non-wildcard fields**. That reproduces the data
unambiguously here because no two rows in this file collide at equal
specificity. If a future patch introduces a tie, the tool takes the first.

Left and right weapons are looked up **independently**, each with its own
appearance ID. `ini/RolePart.ini` declares `l_weapon` and `r_weapon` as separate
parts both backed by `weapon.ini`, and the character mesh carries separate
`v_l_weapon` / `v_r_weapon` dummy sockets. *(INFERRED — consistent with the
part table, not read from code.)*

---

## 3. `ini/ActionMap3DEffect.ini` — terrain and death effects — VERIFIED

60 sections. Its own header comment documents the format. The file is GBK; here
it is verbatim and translated.

```
//SectionTitle为9位数字，每3位一组，分别指定“外形”，“动作”，“地形”，注意"999"为通配数，同时支持 2 个通配数；
//Effect对应的是3deffect.ini里面的特效Title
//ShowTime是播放次特效的时间,0为此动作开始做的时候，1为结束的时候。
//DirEnable是控制特效是否随人物方向旋转的开关
```

> **SectionTitle is a 9-digit number, in groups of 3, specifying respectively
> "shape" (外形), "action" (动作) and "terrain" (地形). Note that "999" is a
> wildcard; two wildcards are supported at the same time.**
>
> **Effect** corresponds to the effect Title inside `3deffect.ini`.
>
> **ShowTime** is when this effect is played: `0` = when this action *starts*,
> `1` = when it *ends*.
>
> **DirEnable** is the switch controlling whether the effect rotates with the
> character's facing direction.

```ini
[104330999]        ; shape 104, action 330 (die), any terrain
Effect=Feather
ShowTime=0
DirEnable=0

[999120002]        ; any shape, action 120 (run), terrain 002
Effect=RunDust
ShowTime=1
DirEnable=0
```

The shape group is the **3-digit monster/NPC shape** that opens a 9-digit
`3dmotion.ini` key. **VERIFIED**: `104`, `132`, `205`, `304`, `332`, `405` all
exist there (`104` → `c3/monster/104N/100.c3`), and they are all bird bodies —
which is why their death action yields `Feather`.

Note `ShowTime=1` means *at the end of the action*, not "at the moment of
impact". That is what the comment says; do not read it as a hit marker.

Terrain IDs seen: `000`–`024` plus `999`. `001` and `004` give `WaterSplash`,
the rest `Dust` / `RunDust`.

---

## 4. `ini/WeaponEffect.ini` — impact spark — VERIFIED

109 sections, keyed by the **3-digit weapon type** (the first three digits of a
weapon appearance ID). `[000]` is unarmed.

```ini
[410]
HitEffect=m-b02
HitSound=sound/blunt_h01.wav
BlkEffect=m-b02
BlkSound=sound/tartness_h01.wav
```

Four fields: the effect and sound for a landed hit, and for a blocked hit. The
effect plays **at the target**, not on the weapon. `HitSound` / `BlkSound` are
paths relative to the install root.

**81 of the 109 sections are `6XY` codes that are not weapon appearance prefixes
at all** — `611, 621, …, 691, 612, …, 699`. `weapon.ini` only ever uses the
prefixes `105, 350, 360, 370, 380, 410, 420, 421, 422, 430, 440, 450, 460, 480,
481, 490, 500, 510, 530, 540, 560, 561, 562, 580, 800`–`804, 900`. The `6XY`
block is 9 × 9 and `ini/WeaponCombin.ini` lists legal weapon pairs
(`410.000`, `420.000`, `410.410`), so these read as **dual-wield combination
codes**: the impact effect for holding family X and family Y at once.
*(INFERRED — the 9 × 9 shape and the WeaponCombin pairing are the evidence.)*
They also appear in `ActionDelay.ini`. Single-weapon lookups never need them.

Timing comes from `ini/ActionDelay.ini`, keyed `<slot 1><body 1><type 3><action 3>`:

* slot `1` / `2` = right / left hand, slot `3` = two-handed. **INFERRED**, but
  slot `3` exists only for `500, 510, 530, 540, 560, 561, 562, 580` — the bow and
  polearm types, which are exactly the two-handed weapons.
* body `1`–`4` = the four player body prefixes (`001`–`004`, see
  `docs/appearance_ids.md`).
* action is always `401`/`402`/`403`.

**All 2,712 entries in this build carry identical values** —
`WoundDelay=50, BlockDelay=50, DieDelay=300` (milliseconds). The table is
effectively a constant here; treat 50 ms as the hit-reaction delay.

---

## 4.5 `ini/WeaponMotion.ini` — the weapon swaps mesh per action — VERIFIED

Not an effect table, but it belongs to the same picture: **the weapon's own
animation is done by swapping the mesh, not by deforming one.**

1,863 flat rows, key = `<weaponAppearance><action>`, value = a C3 mesh path.

```
510000999 = c3/mesh/510000.c3        ; default (999 = any action)
510000300 = c3/mesh/510000401.c3     ; a different mesh for action 300
510000305 = c3/mesh/510000401.c3
```

**100 % of the appearance prefixes are literal `weapon.ini` section names and
100 % of the 1,863 mesh paths resolve to real files.** Actions covered: `999`
(181 default rows) plus `300, 305, 310, 320, 330, 331, 340, 341, 401, 402, 403`.

The swap meshes carry their own `PHY`+`MOTI` — `c3/mesh/510000401.c3` is a
55-vertex mesh with a 2-key `KKEY` track, `c3/mesh/1050000401.c3` (an arrow) is
`Line01`, 61 vertices, 30-frame `RAW`. So a renderer picks the weapon mesh with
`WeaponMotion[appearance + action]`, falling back to
`WeaponMotion[appearance + "999"]` and then to `weapon.ini`'s `Mesh0`, and plays
its `MOTI` on the same clock as the body. The whole table is in
`linkage.json` under `weapon_motion`.

---

## 5. Action IDs

The last three digits of every motion key across `3dmotion.ini`,
`miscmotion.ini`, `ActionSound.ini`, `Action3DEffect.ini` and `ActionDelay.ini`
share one vocabulary. What the sound table pins down (**VERIFIED** — the sound
filename names the action):

| action | evidence | meaning |
|---|---|---|
| `110` `115` | `sound/walk.wav` | walk |
| `120` `121` `125` `126` | `sound/runL.wav` / `runR.wav` | run, left/right foot |
| `130` `131` | `sound/jump.wav` | jump |
| `290` | `sound/dig.wav` | dig |
| `320`–`323` | `sound/bruise0X.wav` | hurt |
| `330`–`336` | `sound/faint0X.wav` | die / knocked down |
| `401` `402` `403` | `sound/attack004.wav` | attack 1 / 2 / 3 |
| `903` | `magictype.json` `SenderAction: 903` on `Thunder` | cast |

`100` is idle (it is the action the always-on armour auras hang off and the
first motion of every body). `406`, `408`, `150`, `151`, `160`, `170`, `180`,
`190`, `200`–`280` exist in `3dmotion.ini` but are not identified here.

---

## 6. The animation model — the crux

### 6.1 Three forms, and which is which

An effect mesh named by `3DEffectObj.ini` is a C3 container (`MAXFILE C3 00001`,
see `docs/modding.md` §5). Of the 2,351 entries in this build:

| chunks present | count | what it is | decoded? |
|---|---:|---|---|
| `PHY` + `MOTI` | 1,203 | quads with a per-frame matrix track | **yes** |
| `SHAP` + `SMOT` | 589 | a swept ribbon trail | **yes** |
| both of the above | 63 | | **yes** |
| `PTCL` / `PTC3` | 431 | particle system | **no** — §6.6 |
| mixed with particles | 22 | | partial |
| file missing / empty | 43 | data bugs in the ini | — |

`CAME` (camera) chunks also appear; they are 3DSMax authoring metadata with
nothing to play.

**There is no skeletal animation in an effect.** A typical effect part is
`verts=4 faces=2 bones=1` — a single textured quad with its own transform track.
`m-b02` (the blunt-weapon impact spark) is 3 layers of 1, 1 and 16 such quads.
That is why this is achievable: it is billboard/quad animation plus alpha and
texture-cell keys, exactly as suspected.

### 6.2 `MOTI` — VERIFIED, and newly decoded

`docs/modding.md` §9.9 listed `MOTI` as undecoded. **It is not inside Themida.**
`graphic.dll` exports `Motion_Load` (RVA `0x557A0`) and `Motion_GetMatrix`
(RVA `0x551A0`) as ordinary named functions. The layout below is read straight
out of `Motion_Load`.

```
u32   boneCount        # rejected with "invalid bone count : %d, 255 is MAX" if > 255
u32   frameCount       # animation length; the modulus used by Phy_NextFrame
char[4] encoding       # "KKEY" | "ZKEY" | "XKEY"; anything else -> seek back 4, RAW

-- KKEY --------------------------------------------------------------
u32   keyCount
keyCount x { u32 frameIndex ; float[16] matrix[boneCount] }

-- ZKEY --------------------------------------------------------------
u32   keyCount
keyCount x { u16 frameIndex ; { float4 quaternion ; float3 translation }[boneCount] }

-- XKEY --------------------------------------------------------------
u32   keyCount
keyCount x { u16 frameIndex ; float[12] matrix4x3[boneCount] }

-- RAW (no tag) ------------------------------------------------------
float[16] matrix[frameCount]   per bone, BONE-MAJOR
                               (key i is simply frame i)

-- all forms ---------------------------------------------------------
u32   extraChannelCount
      skip extraChannelCount * frameCount * 4 bytes    # read and discarded
```

Notes, all VERIFIED:

* **Everything becomes a 4×4 matrix at load time.** `ZKEY` is expanded with
  `D3DXMatrixRotationQuaternion` (the thunk at RVA `0x1D1F0A` jumps to
  `d3dx10_43!D3DXMatrixRotationQuaternion`) and the translation is written into
  row 3 with `m[15] = 1.0` — the same **row-vector** convention as the PHY chunk
  matrix. `XKEY`'s 12 floats fill rows 0–3 columns 0–2, with column 3 set to
  `0,0,0,1`.
* `KKEY` is **frame-major** (all bones of one key contiguous); the tagless `RAW`
  form is **bone-major** (all frames of one bone contiguous). Getting these the
  wrong way round still parses to the right length, so this is worth stating.
* The trailing `extraChannelCount` block is read past and never used.

**Interpolation** — `Motion_GetMatrix(motion, bone, frame, out)`:

1. `frame <= keys[0].frameIndex` → key 0, verbatim.
2. `frame >= keys[last].frameIndex` → last key, verbatim.
3. otherwise find the bracketing keys and **linearly interpolate all 16 matrix
   elements** with `t = (frame - prev.frameIndex) / (next.frameIndex - prev.frameIndex)`.

Element-wise lerp of a matrix, not slerp — that is literally what the code does
(the quaternions were already baked at load time).

**Frame advance** — `Phy_NextFrame(phy, delta)` is
`current = (current + delta) % motion->frameCount`. The current frame lives on
the motion object, so a `PHY` and its `MOTI` share one clock.

**Validation.** `py -3 tools/effects.py --validate` parses every `MOTI` in every
C3 named by `3DEffectObj.ini`, `WeaponMotion.ini`, `3dobj.ini`, `3dmotion.ini`,
`miscmotion.ini` and `MountMotion.ini`:

```
files 5874    MOTI 15278   exact 15278 (100%)
              RAW 9315   KKEY 4536   ZKEY 1182   XKEY 245
```

All four encodings occur in real data and every chunk is consumed to exactly its
declared length. Independently: on 1,967 sampled meshes the `MOTI` `boneCount`
covers the paired `PHY`'s bone palette in **every** case, zero counterexamples.

### 6.3 `SHAP` + `SMOT` — the weapon trail — VERIFIED

This is what makes a sword swing leave a streak. From `Shape_Load`
(RVA `0x5D570`) and `SMotion_Load` (RVA `0x5CE30`):

```
SHAP
  u32 nameLen ; char[nameLen] name          # e.g. "Line01"; the engine Seeks past it
  u32 frameCount
  frameCount x { u32 pointCount ; float3 points[pointCount] }
  u32 labelLen ; char[labelLen] label       # the 3DSMax source texture path; skipped
  u32 segments                              # 0 is normalised to 1

SMOT
  u32 frameCount
  float[16] matrix[frameCount]
```

`Shape_Draw` (RVA `0x5CF60`) uses only **`frames[0]`, and only its first two
points** — 533 of 543 shipped `SHAP` chunks have exactly 2 points anyway. The
line is the blade cross-section:

```
c3/effect/flash/410000.c3   SHAP "Line01"
   A = (-0.34,  41.86,  2.00)
   B = (-0.55, -29.45, -0.12)      segments = 7
```

Rendering, from `Shape_SetSegment` (RVA `0x5D8C0`) and `Shape_Draw`:

* the ribbon holds `nSeg = min(segments * 5, 800)` segments and a vertex buffer
  of `nSeg * 2 + 2` vertices at **20 bytes each** (position + UV) — a triangle
  strip.
* each frame, transform `A` and `B` by `SMOT.matrix[currentFrame] × world`, then
  push **5 linearly interpolated pairs** between the previous transformed line
  and the new one. Old pairs fall off the end.
* the trail therefore comes from the *parent's* motion, not the effect's own —
  the `SMOT` on the shipped flash meshes is a constant matrix on all 101 frames.

`Shape_NextFrame` / `Shape_SetFrame` advance the frame modulo the `SMOT` frame
count, i.e. the shape's clock is its `SMOT`.

`tools/effects.py` exposes this as `shape_ribbon(shape, smotion, world, frame,
history)`, which mutates and returns the caller's rolling pair list.

### 6.4 `C3Key` — alpha, visibility, texture cell — VERIFIED

`docs/modding.md` §9.6 established that a `PHY` chunk ends with three arrays of
16-byte records (`nAlpha`, `nDraw`, `nChgTex`) and left them opaque. They are
decoded now. `struct C3Frame { int nFrame; float fParam; BOOL bParam; int nParam; }`
— which member each channel uses is settled by which offset the reader touches:

| channel | reader | reads | semantics |
|---|---|---|---|
| alphas | `Key_ProcessAlpha` `0x725A0` | `+0x04` float | **linearly interpolated** between bracketing keys, clamped at both ends, then clamped to `[0,1]` |
| draws | `Key_ProcessDraw` `0x72720` | `+0x08` bool | visibility; **exact frame match only** |
| changeTexs | `Key_ProcessChangeTex` `0x726B0` | `+0x0C` int | **step function** — exact match, else the key immediately before the first key past `frame` |

`Phy_Calculate` (RVA `0x56040`) drives all three off `motion->currentFrame`:

```c
Key_ProcessDraw(&phy->keys, motion->currentFrame, &phy->visible);
if (!phy->visible) return;                       // skip the whole part
alpha = 1.0f;
Key_ProcessAlpha(&phy->keys, motion->currentFrame, motion->frameCount, &alpha);
alpha = clamp(alpha, 0, 1);
texIndex = -1;
Key_ProcessChangeTex(&phy->keys, motion->currentFrame, &texIndex);
```

#### The texture animation — VERIFIED, RVA `0x56102`–`0x5616C`

This is the "texture/frame animation" the effect system is built on:

```
N = phy->frameCount            // the u32 stored right after the chunk matrix
                               // (docs/modding.md §9.4), at C3Phy+0x190

if (texIndex >= 0):            // a ChangeTex key applies
    uOffset = (texIndex % N) * (1.0 / N)
    vOffset = (texIndex / N) * (1.0 / N)      // integer division
else:                          // no ChangeTex channel
    uOffset = tick * step.u                   // step = STEP chunk, C3Phy+0x198
    vOffset = tick * step.v                   //                    C3Phy+0x19C
                                              // tick  = C3Phy+0x194

both offsets are then wrapped into [-1.1, 1.1] by subtracting floor/ceil
```

So **the texture is a flipbook atlas of `N × N` cells** and `nParam` is the cell
index, row-major. Worked example, `c3/effect/Monster-bomb/m-b02/2.C3`, part
`Plane02`: `phy->frameCount = 2`, ChangeTex keys `(0→0) (2→1) (4→2) (6→3)` — a
2×2 grid stepping through all four cells over frames 0–6.

When there is no ChangeTex channel the UVs **scroll** instead, at the rate given
by the optional `STEP` chunk. `docs/modding.md` §10.3 already noted that STEP's
two `u32` are float bit patterns; this is what they are for. `Phy_Load` stores
them at `C3Phy+0x198`/`+0x19C` (RVA `0x5B21E`, `0x5B235`). Meshes without a
`STEP` chunk get `0,0` and therefore no scroll.

The multiplier `tick` at `C3Phy+0x194` is **read but never written anywhere in
`graphic.dll`** — the owner sets it, and that owner is in the packed exe. Drive
it with the elapsed animation frame; **INFERRED**, but it is the only quantity in
scope, and with `STEP = (0,0)` on every mesh that lacks the chunk the choice is
inert for all but the handful that have one.

### 6.5 How long an effect actually lasts

`3DEffect.ini` gives `FrameInterval` (ms per frame) and `LoopTime`, but effect
meshes habitually declare a 101-frame `MOTI` and fade out after ten. **The alpha
envelope is what says when a burst is over.**

Rule used by `tools/effects.py` (**INFERRED**, but it is what the data plainly
shows): if a part has two or more alpha keys and the last one is `0.0`, the
animation ends at that key's frame; otherwise the full `MOTI` length stands.

That makes `m-b02` `11 frames × 33 ms = 363 ms` — a believable impact spark —
instead of 3.3 s. 1,579 of the 2,255 effects are non-endless (`LoopTime` < 99999)
and 1,499 of those get a concrete duration; the remaining 80 have no loadable
geometry to measure.

`SHAP` trails have no alpha channel, so their nominal length is the full `SMOT`
frame count. In practice a trail is bounded by the attack motion that spawned
it. *(INFERRED — the spawn/despawn logic is in the packed exe.)*

### 6.6 `PTCL` / `PTC3` — not decoded

431 effect objects are pure particle systems and 22 more mix particles with
geometry. The readers are reachable and unpacked — `graphic.dll` exports
`Ptcl_Load(C3Ptcl**, void*)` at `0x61150`, `Ptcl_Load(C3Ptcl2**, void*)` at
`0x60A50` and `Ptcl_Load(C3Ptcl3**, void*)` at `0x60CE0`, with matching
`Ptcl_Draw` / `Ptcl_SetFrame` / `Ptcl_NextFrame` — so this is a tractable
follow-up, not a blocked one. It is simply out of scope here.

**It barely matters for weapons.** Of the 1,134 effects reachable from a weapon,
1,105 need no particles at all (§9).

---

## 7. `ini/3DEffect.ini` — the effect definition — VERIFIED

2,240 sections. The full field list is not guesswork: `GraphicData.dll` holds
the literal `scanf` format strings, at `.rdata 0x91FEC`–`0x923D0`, right next to
the literal `ini/3DEffect.ini` at `0x921A8`.

```ini
[Flash4102]
Amount=1
EffectId0=4100
TextureId0=4102
ASB0=5
ADB0=6
Delay=0
LoopTime=1
FrameInterval=41
LoopInterval=0
OffsetX=0
OffsetY=0
OffsetZ=0
```

### Per-effect fields

| key | meaning | in this build |
|---|---|---|
| `Amount` | number of layers | 1–8 |
| `Delay` | ms before the effect first appears | mostly 0 |
| `LoopTime` | number of playthroughs; ≥ 99999 means "forever" | 1 … 99999999 |
| `LoopInterval` | ms of dead time between loops | |
| `FrameInterval` | **ms per animation frame** — 33 ≈ 30 fps, 41 ≈ 24 fps | |
| `OffsetX/Y/Z` | translation applied to the whole effect | |
| `ColorEnable` | tint from the owner's colour | 108 sections |
| `Billboard` | face-camera mode | unused |
| `Lev` | LOD level (see `Set3DEffectMaxLOD`) | 17 sections |

### Per-layer fields, suffixed with the layer index

| key | meaning | in this build |
|---|---|---|
| `EffectId<i>` | → `3DEffectObj.ini` → a C3 mesh | 3,877 layers |
| `TextureId<i>` | → `3dtexture.ini` → a DDS | 3,877 layers |
| `TextureId<i>_1/_2/_3` | extra texture slots | **never used** |
| `ASB<i>` / `ADB<i>` | D3D source / destination blend factor | 3,877 layers |
| `Scale<i>` | uniform scale | 27 layers |
| `ZBuffer<i>` / `ZTest<i>` | depth write / depth test | 1 layer |
| `FrameOffset<i>`, `Interval<i>`, `LoopOnce<i>`, `MixOpt<i>`, `MixData<i>`, `Lev<i>`, `Billboard<i>`, `XSelf<i>`, `YSelf<i>`, `ZSelf<i>` | read by the loader | **never used** |

`ManualUVStep` and `nAABB` appear in the string block with no matching format
string and are not present in the data.

**`ASB`/`ADB` are `D3DBLEND`** — the same enum as `Asb`/`Adb` in `weapon.ini`
and `armor.ini`. `5 = SRCALPHA`, `6 = INVSRCALPHA` (ordinary alpha blending),
`2 = ONE` (additive — `ASB=5, ADB=2` is the usual glow). `tools/effects.py`
exposes `blend_name()`; the full table is in the module.

### `ini/3DEffect.json`

A **stale 2022 export** of the same data — 2,209 entries against the `.ini`'s
2,240, and dated three years earlier. `GraphicData.dll` reads `ini/3DEffect.ini`
(and `ini/3DEffect.dbc`); it never mentions the JSON. Prefer the `.ini`.
`tools/effects.py` merges in the 15 names the JSON has and the `.ini` does not
(`cat_bow`, `TuLong_gold`, `qingren_weapon`, …) so nothing referenced elsewhere
goes missing, with the `.ini` always winning.

### `ini/3DEffectObj.ini` and `ini/3dtexture.ini`

Flat `id=path`. 2,351 and 7,929 entries. Paths are install-relative with
inconsistent case and backslashes; normalise before lookup.

```
1066=C3/Effect/MFire/1.C3
4100=C3/Effect/flash/410000.C3
```

37 of the 2,351 object paths and 144 of the 7,929 texture paths point at files
that do not exist in this install, and 4 object entries point at a `.dds`
instead of a `.c3`. Those are data bugs, not resolution failures.

---

## 8. Playback algorithm

Everything a renderer needs, in order.

**Setup, once per equipped weapon:**

1. Take the weapon appearance ID `A` (a `weapon.ini` section name, 6 digits).
   Split it: `type = A[:-3]`, `sub = A[-3:]`.
2. Aura: `Action3DEffect[999.999.<type>.<sub>]`, falling back to
   `999.999.<type>.999`. Attack trails: `Action3DEffect[999.<action>.<type>.<sub>]`
   for `action` in `401,402,403` (and `900,901,903` for skills), same fallback.
   Impact: `WeaponEffect[<type>]`. Discard the value `none`.
3. Weapon mesh for the current action: `WeaponMotion[A + action]`, else
   `WeaponMotion[A + "999"]`, else `weapon.ini[A].Mesh0` (§4.5).
4. For each effect name, read its `3DEffect.ini` section → `FrameInterval`,
   `LoopTime`, `LoopInterval`, `Delay`, `Offset`, and the layer list.
5. For each layer, resolve `EffectId` → C3 path and `TextureId` → DDS path, and
   load both. Decode each C3 chunk per §6.

**Per rendered frame, for one active effect instance.** `frames` is the part's
playable length — the alpha-envelope length where there is one, else
`moti.frameCount` (§6.5).

```
elapsed = now - spawnTime
if elapsed < Delay: draw nothing
t      = elapsed - Delay
cycle  = frames * FrameInterval + LoopInterval
loop   = t / cycle
if LoopTime < 99999 and loop >= LoopTime: the effect is over, despawn
frame  = ((t % cycle) / FrameInterval)          # integer; >= frames -> in the gap
```

**For a `PHY` + `MOTI` part** (a quad):

```
visible = Key_ProcessDraw(keys, frame)            # exact-match only; default true
if visible is false: skip this part
alpha   = clamp(Key_ProcessAlpha(keys, frame), 0, 1)   # lerped; default 1.0
texCell = Key_ProcessChangeTex(keys, frame)            # step; -1 if no keys
if texCell >= 0:
    N = phy.frameCount
    uvOffset = ((texCell % N) / N, (texCell // N) / N)
else:
    uvOffset = (frame * step.u, frame * step.v)        # STEP chunk, else (0,0);
                                                       # the multiplier is INFERRED
M = Motion_GetMatrix(moti, bone = vertex.bone0, frame)   # lerped 4x4
world = M x chunkMatrix x effectOffset x attachSocket
draw the quad with world, uv + uvOffset, colour alpha, blend (ASB, ADB)
```

`vertex.bone0` is the PHY vertex's `BLENDINDICES0` (`docs/modding.md` §9.3); on
a one-bone effect quad it is 0 on every vertex.

Vertex positions, UVs and indices come from the `PHY` chunk exactly as
`docs/modding.md` §9.3–§9.4 describes; `core/c3phy.parse_phy` already does it.
Apply the same coordinate conversion the rest of the project uses —
`(x, y, −z)`, UV `(u, 1 − v)`, keep index order (`docs/modding.md` §9.7).

For a skinned part (`bones > 1`) index `Motion_GetMatrix` per bone and blend the
two influences as §9.3 describes. Effect meshes in practice have one bone.

**For a `SHAP` + `SMOT` part** (a trail):

```
A, B    = shape.frames[0].points[0..1]
M       = smot.matrices[frame % smot.frameCount] x world
a, b    = M * A, M * B
push 5 pairs linearly interpolated from the previous (a, b) to the new one
keep at most min(shape.segments * 5, 800) + 1 pairs
emit a triangle strip through the pair list, U along the strip, V across it
```

**Anchoring — INFERRED.** A weapon's trail and aura ride the weapon: apply the
weapon socket's world matrix (`v_l_weapon` / `v_r_weapon` on the body mesh),
then the effect's `OffsetX/Y/Z`. The flash geometry is authored in the weapon's
local frame (`c3/effect/flash/410000.c3`'s line runs along the weapon's +Y blade
axis). Impact effects from `WeaponEffect.ini` anchor to the *target*, and
`ActionMap3DEffect` effects to the character, honouring `DirEnable` for whether
the effect yaws with the facing.

---

## 9. Coverage

`py -3 tools/effects.py --coverage`, on this install.

### Weapons

| | |
|---|---:|
| weapon appearances in `weapon.ini` | **5,384** |
| … with an attack trail (action `401`/`402`/`403`) | **2,143** |
| … with an always-on aura (action `999`) | **1,286** |
| … with a hit/block impact effect (via type) | **4,436** |
| weapon types with an entry in `WeaponEffect.ini` | 109 (28 real + 81 dual-wield `6XY`) |
| types named in `WeaponSkillName.json` but with no impact entry | 27 of 48 |

Sixteen weapon types carry trails: `410 420 421 430 440 450 460 480 481 490 510
530 540 560 561 580`. Fifteen carry none at all — `350 360 370 380 422 500 562
800 801 802 803 804 900 1050 1051` — these are bows (`500`), arrows (`1050`,
`1051`), shields (`900`, which get a blanket aura instead) and accessory types.
A bow's visible projectile comes from `ini/3DFlyingObj.ini`, keyed
`<bowAppearance>.<arrowAppearance>` and naming an `EffectIndex`, a flying sound
and a `TargetEffect`; that path is mapped in `linkage.json` but not decoded
further here.

### Effects

| | |
|---|---:|
| effect names defined (`3DEffect.ini` + JSON-only) | **2,255** |
| effect names referenced by any table | 1,175 |
| … referenced but undefined | **4** (`cat_black`, `cat_white`, `_p_0_1194870_armet`, `_p_0_3194870_armet`) |
| layers total | 3,987 |
| … whose mesh resolves to a real file | 3,825 (95.9 %) |
| … whose texture resolves to a real file | 3,804 (95.4 %) |
| effects where **every** layer resolves | **2,207** (97.9 %) |
| effects partially resolving | 7 |
| effects with no layer resolving | 41 |

### Playability with the decoded animation forms

| | |
|---|---:|
| effect objects backed by `PHY`+`MOTI` and/or `SHAP`+`SMOT` | 1,855 |
| … backed by particles only | 431 |
| effects playable today | **1,755** |
| effects needing particles for some layer | 464 |
| **effects reachable from a weapon** | **1,134** |
| … fully playable today | **1,105 (97.4 %)** |
| … needing particles for some layer | 29 |
| … unresolved | **0** |

### Parsers

| chunk | parsed | consumed to exactly the declared length |
|---|---:|---:|
| `MOTI` | 15,278 | **15,278 (100 %)** |
| `SHAP` | 543 | **543 (100 %)** |
| `SMOT` | 543 | **543 (100 %)** |

---

## 10. Verified vs inferred, at a glance

**VERIFIED — read out of code or proven on the whole corpus**

* `MOTI` / `SHAP` / `SMOT` binary layouts, and all four `MOTI` encodings.
* `Motion_GetMatrix` interpolation, `Phy_NextFrame` modulo behaviour.
* `C3Frame`'s three channels and which member each uses.
* The flipbook UV formula and the `STEP` UV scroll.
* The `3DEffect.ini` field set (from `GraphicData.dll`'s own format strings).
* `ActionMap3DEffect.ini`'s key format and field meanings (the file says so).
* `Action3DEffect.ini`'s key format (from the data: 2,431 of 2,678 group-2+3
  values are literal `weapon.ini` section names, and group 1 is the shared action
  vocabulary).
* `ASB`/`ADB` are `D3DBLEND`.
* `3DEffect.ini` is authoritative over `3DEffect.json`.

**INFERRED — reproduces the data, ground truth not read**

* The wildcard resolution order when two rows tie on specificity.
* That the alpha envelope bounds an effect's real duration.
* Where an effect is anchored (weapon socket vs character vs target) and how
  `Offset` composes with it.
* Left/right weapon effects being resolved independently.
* `ActionDelay`'s first digit being the weapon slot.
* The `6XY` block in `WeaponEffect.ini` being dual-wield combination codes.
* The UV-scroll multiplier at `C3Phy+0x194` (never written in `graphic.dll`).
* Trails being cut short by the attack motion rather than running their nominal
  101 frames.

**OPEN**

* `PTCL` / `PTC3` particle systems (§6.6) — reachable in `graphic.dll`, not done.
* What spawns and despawns an effect instance, and the exact hit moment inside an
  attack motion. That logic is in the packed `ImConquer.exe`.
  **The spawn *triggers* are now mapped in `docs/world_effects.md`**: map
  EFFECT layers, `MsgName` 1015 actions 9/10, `MsgMagicEffect` 1105 +
  `magictype.json`, and status bits via `statuseffect.ini`. The per-instance
  lifetime management stays exe-side.
* `MOTI`'s trailing `extraChannelCount` block — read and discarded by the engine,
  so its content is unknown and irrelevant to playback.
* `3DEffectInfo.ini`, `MagicEffect.ini` and `effect.ini` (the legacy 2D `.ani`
  effect system) are not used by the weapon path and are not decoded here.

---

## 11. `out/effects/linkage.json`

Written by `py -3 tools/effects.py --linkage`. ~3.9 MB, UTF-8, compact
(`--pretty` to indent). Top-level keys:

| key | shape |
|---|---|
| `weapon_types` | `"410" -> {name, hit_effect, hit_sound, blk_effect, blk_sound}` |
| `weapon_appearances` | `"410009" -> {type, type_name, aura, attack{action->effect}, hit_effect, blk_effect}` — only the 2,921 with at least one effect. `none` rows are stripped from this view; they are still in `action_effect_rules` |
| `action_effect_rules` | every `Action3DEffect.ini` row, split into fields |
| `action_map_rules` | every `ActionMap3DEffect.ini` row, with `show_time` / `dir_enable` |
| `action_delay` | `ActionDelay.ini` verbatim |
| `effects` | `"Flash4102" -> {frame_interval_ms, fps, loop_time, endless, delay_ms, loop_interval_ms, offset, frames, effective_frames, duration_ms, layers[...]}` |
| `flying_objects` | `"500009.1050000" -> {simple_obj_id, effect, flying_sound, hit_sound, target_effect}` — arrows, 192 rows |
| `effect_objects` / `effect_textures` / `weapon_motion` | the raw id→path tables, normalised to forward slashes |
| `effect_object_geometry` | `"4100" -> {path, kinds, frames, effective_frames, parts, source}` — which animation form each object uses, so a viewer can skip particles without opening the file |
| `coverage` | §9, machine-readable |

`kinds` values are `phy_motion`, `shape_trail`, `particle`.

---

## 12. Reproducing

```bash
py -3 tools/effects.py --weapon 410009          # a super Blade: trail + aura + impact
py -3 tools/effects.py --weapon 421229          # a super Backsword
py -3 tools/effects.py --effect m-b02           # the blunt impact spark, 3 layers
py -3 tools/effects.py --effect Flash4102       # a trail: SHAP line + segments
py -3 tools/effects.py --action-map 104 330 002 # -> Feather
py -3 tools/effects.py --validate               # the parser proof
py -3 tools/effects.py --coverage
py -3 tools/effects.py --linkage
```

As a module:

```python
from effects import EffectDB, shape_ribbon, IDENTITY
db = EffectDB()
es = db.effects_for_weapon("410009")            # -> WeaponEffectSet
eff = db.resolve(es.attack["401"])              # -> EffectDef with asset paths
objs = db.load_geometry(es.attack["401"])       # -> [EffectObject] with parsed chunks
m = objs[0].parts[0].motion.matrix(bone=0, frame=12)
```
