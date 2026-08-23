# 3D effects — weapon linkage and playback

How the client decides which visual effect belongs to an equipped weapon, and
exactly how that effect is animated. Written so a renderer can be implemented
from this document alone.

Every claim is marked **VERIFIED** (proven against the real files, or read out of
`graphic.dll` / `GraphicData.dll` instruction by instruction, with the RVA cited)
or **INFERRED** (a working rule whose ground truth has not been read — the code
that would prove it is inside the Themida-packed `ImConquer.exe`).

> ### Which image every RVA in this document belongs to
>
> **All of them are the CCO install's `bin/64/graphic.dll`, which is
> `IMAGE_FILE_MACHINE_AMD64` (`0x8664`) — an x64 build.** They were
> written here unlabelled, which reads as "the client", and this project's
> general statement that *"Conquer binaries are i386"* is **REFUTED for this
> surface**.
>
> MEASURED 2026-08-09 by resolving each cited RVA in both images:
>
> | | CCO `bin/64/graphic.dll` | retail 5517 / 6090 `graphic.dll` |
> |---|---|---|
> | machine | **`0x8664`** (x64) | `0x14C` (i386) |
> | exports | 509 | 168 |
> | `Motion_Load` | **`0x557A0`** | absent |
> | `Motion_GetMatrix` | **`0x551A0`** | absent |
> | `SMotion_Load` | **`0x5CE30`** | absent |
> | `Shape_Draw` | **`0x5CF60`** | absent |
> | `Phy_Calculate` | **`0x56040`** | absent |
> | `Ptcl_Load` ×3 | **`0x60A50` `0x60CE0` `0x61150`** | absent |
>
> Every RVA quoted below resolves in the x64 image and **none of them exist in
> the retail i386 image**, which exports neither `Motion_Load` nor any
> `Ptcl_*` *function*. (It does export two `Ptcl_*` symbols —
> `DestroyPtclInfo` and `DestroyPtcl3Info` — so "no `Ptcl_*` at all" is very
> slightly too strong; none of the readers cited here are among them.) The
> exports are C++-decorated in both images; the undecorated spellings used
> below are for readability.
>
> **An RVA without its image is the same defect as an index without its base
> id.** The decoded *formats* are unaffected — a C3 chunk layout is the same
> whatever the reader was compiled for — but do not attach these addresses to
> a retail client, and do not build a patch or a hook from them.

Companion tool: **`tools/effects.py`** — module + CLI. Everything below is
reproducible with it. Machine-readable output: **`out/effects/linkage.json`** (§11).

> **Correction to `docs/modding.md` §9.9.** That section lists `MOTI` as
> undecoded and groups it with `MNEW`/`CCFL`. It does not belong there: `MNEW`
> and `CCFL` really are unreachable, but `MOTI`'s reader is the exported,
> unobfuscated `graphic.dll!Motion_Load` at RVA `0x557A0`. It is decoded in §6.2
> below and validated on 15,278 chunks. `SHAP`/`SMOT` likewise, and the three
> `C3Key` channels §9.6 kept opaque are decoded in §6.4.

> **Scope: every effect figure below is now measured on the table the client
> actually loads, on each of the six declared bases.** `EffectDB` reads
> `ini/3DEffect.dbc` (and the compiled `3DEffectObj` / `3dtexture` / `3dobj`
> twins) on 5517 and 6090, and the plaintext ini on 5017 / 5065 / 5165 / CCO,
> which ship no twins and where the plaintext *is* the live table. The choice is
> per file and per base, never per client — `--coverage` prints a `tables_read`
> block naming the file behind every number. §9a is the before/after.
>
> **Two surfaces are still plaintext-only and are named where they occur**:
> `ini/weapon.ini` (the §9 *Weapons* rows — its twin is a `MESH` table and
> wiring it is that table's owner's call) and `ini/WeaponMotion.ini`, whose
> compiled key encoding is undecoded (§9a). Neither feeds `effect_layers_total`
> or any §7a figure.

> ### The banner that used to be here, and what settled it — 2026-08-09
>
> This document carried a **PROVISIONAL ON EVERY COMPILED BASE** warning: the
> `EFFE` reader existed and was **not on the definitions path**, so on 5517 and
> 6090 every effect figure counted the shadowed plaintext table. Its sharpest
> claim was that **`effect_layers_total` read 5,099 on 5517 *and* 6090 — not
> because the clients agree but because both reads landed on one frozen 2009
> file** — so any cross-base comparison through this document returned
> "identical" as a tautology.
>
> **That is now fixed and the claim is confirmed by the fix**: with the reader
> wired in, the same figure reads **8,759 on 5517 and 13,248 on 6090**, and it
> was never a property of the clients. `C50-effe-definitions` records the
> re-derivation; `C45-ini-shadow-class` still covers the class. The banner is
> gone rather than narrowed because the numbers it warned about have all moved
> and are restated below with their before/after (§9a).
>
> **The residue, stated so it is not lost with the banner:** `weapon.ini` and
> `WeaponMotion.ini` are still read plaintext on 5517/6090 (above), and the
> §9 *Weapons* rows inherit that. They are labelled in place.

```bash
py -3 tools/effects.py --weapon 410009     # every effect for one weapon, resolved
py -3 tools/effects.py --effect Flash4102  # one effect -> meshes, textures, timing
py -3 tools/effects.py --validate          # exact-length proof, every chunk form
py -3 tools/effects.py --coverage          # the numbers in §9
py -3 tools/effects.py --linkage           # writes out/effects/linkage.json
py -3 tools/ptclprove.py --all-bases       # the particle layout proof (§9)
```

> Prefix any of these with `CO_ROOT=<install>` to target a specific one. The
> numbers differ substantially between installs and `--coverage` / `--validate`
> print the `root`, the `base_id` and a `tables_read` block as their first
> keys — quote those with any figure taken from them.
>
> **Check the `base_id` on every run, not once.** Re-deriving these figures
> the first time produced six identical answers because a shell mangled
> `CO_ROOT` and all six runs resolved the *configured* install. Six numbers
> that agree is what both this document's old defect and a broken `--root`
> look like from the outside; the only thing that tells them apart is the
> resolved base printed beside each one. `tables_read` is the second half of
> the same check: it names the file, so "did this see the live table?" is
> answered by the output rather than by reading the code.

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
separate form entirely: a two-point line smeared into a ribbon. Particles are a
third: a **baked** simulation, one solved particle set per frame, drawn as
camera-facing quads off a flipbook atlas. All three are decoded — see §6.

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
| `PTCL` / `PTC3` | 431 | particle system | **yes** — §6.6 |
| mixed with particles | 22 | | **yes** |
| file missing / empty | 43 | data bugs in the ini | — |

(Counts are CCO's — see the note at the head of §9.)

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

### 6.6 `PTCL` / `PTCX` / `PTC3` — VERIFIED, and newly decoded

> **This section replaces "not decoded".** All three generations are read out of
> `graphic.dll` and parse to exactly the declared length on every chunk of every
> declared install (§9). `tools/effects.py::parse_ptcl`.

**There are three tags, not two.** The C3 container walker `sub_1A1E0` — the
same function that drives `Phy_Load` / `Motion_Load` / `Shape_Load` /
`SMotion_Load` — compares the four tag bytes and dispatches:

| tag | loader | RVA | call site |
|---|---|---|---|
| `PTCL` | `Ptcl_Load(C3Ptcl**, void*)` | `0x61150` | `0x1A567` |
| `PTCX` | `Ptcl_Load(C3Ptcl2**, void*)` | `0x60A50` | `0x1A5C3` |
| `PTC3` | `Ptcl_Load(C3Ptcl3**, void*)` | `0x60CE0` | `0x1A623` |

`PTCX` is the middle generation, and it is **rare, real, and easy to miss**: 6
chunks in patch5517, 8 in patch6090, 0 in CCO and 0 in the three 50xx clients.
Every one of them is in a file that **no `.ini` names** — they are reachable
only through the compiled `.dbc` tables, so enumerating the plaintext reports
`PTCX` as a form nobody ships. This document said exactly that for one draft;
see the note below and `docs/CORRECTIONS.md` C41(e).

```
c3/effect/weapon/800220/5.c3   PCloud09, PCloud10     patch5517 + patch6090
c3/effect/weapon/800420/5.c3   PCloud09, PCloud10
c3/effect/weapon/800520/5.c3   PCloud09, PCloud10
c3/effect/other/_p_5_targetm/4.c3  04PCloud01, 04PCloud05   patch6090 only
```

A reader that omits `PTCX` will mis-walk any file that has one, because the
dispatcher's fallthrough seeks past the chunk by its declared length rather than
erroring — you get silence, not a failure.

> **Enumerate through `effects.asset_paths()`, never through `PATH_INIS`
> alone.** On patch5517 the plaintext tables name 13,533 asset paths and the
> `.dbc` twins name 21,993, of which **8,493 are in no ini at all**;
> `ini/miscmotion.ini` and `ini/MountMotion.ini` are literally **zero bytes**
> while their twins hold 218 and 784 rows. `core/dbc.py` has said since it was
> written that *"the `.ini` twins are stale decoys … the client reads the
> `.dbc`"*. CCO is the mirror case — no `.dbc` at all, plaintext complete — so
> **twin-presence and twin-absence both have to be checked, per file and per
> base.**

> **RVA base, and it is not what the tooling note says.** Every RVA in this
> document is into **CCO's `bin/64/graphic.dll`, which is AMD64**
> (COFF machine `0x8664`, 509 exports, 2.7 MB) — *not* i386. The retail
> `graphic.dll` shipped beside `Conquer.exe` in patch5517/6090 is i386 with 168
> exports and **exports none of the `Ptcl_*` loaders**. It *does* export
> `DestroyPtclInfo` and `DestroyPtcl3Info`, so the absolute form — "no `Ptcl_*`
> at all" — is **wrong**; what the claim needs, and what holds, is that none of
> the cited *readers* are among retail's exports. See `docs/CORRECTIONS.md`
> C41. `tools/disfn.py` takes the mode from the COFF header and gets this right
> by itself; a hand-forced `--bits 32` would not.

#### The layout

`TQFRead(dst, elemSize, count, file)` and `TqFSeek(file, n, SEEK_CUR)` are the
only stream calls in all three loaders, so the byte order is unambiguous:

```
u32 nameLen  ; char[nameLen] name     # PTCL/PTCX Seek past it; PTC3 KEEPS it
u32 labelLen ; char[labelLen] label   # all three Seek past it (3DSMax source path)
u32 texGrid                           # -> +0x14; the texture is texGrid x texGrid

-- PTC3 only, 50 bytes ----------------------------------------------
u8   billboard          # -> +0x30, and 100 is subtracted if it is >= 100
u8   worldSpace         # -> +0x34, non-zero skips the position transform
f32  roll[2]            # +0x3C, +0x40
f32  alpha[3]           # +0x44, +0x48, +0x4C  -- system alpha envelope
u32  fadeFrame[2]       # +0x50, +0x54         -- its two frame thresholds
f32  particleAlpha[3]   # +0x58, +0x5C, +0x60  -- per-particle envelope
f32  particleLife[2]    # +0x64, +0x68         -- its two life thresholds
---------------------------------------------------------------------

u32 maxParticles                      # -> +0x10
u32 frameCount                        # -> +0x24
frameCount x {
    u32 count
    if count:                         # count == 0 reads NOTHING further
        u16   ids[count]              # PTCX / PTC3 only
        f32x3 position[count]
        f32   cellPhase[count]
        f32   size[count]
        f32   matrix[16]              # a D3DXMATRIX, 0x40 bytes
}
```

**The zero-count branch is the whole difficulty** (`0x612B9` / `0x60BB5` /
`0x61016` jump straight to the null-pointer tail). Reading the 64-byte matrix
unconditionally desynchronises the stream, and **54,713 of patch5517's 115,561
particle frames are empty (47%)** — so getting it wrong fails on most files,
not on a few.

#### What the fields mean, from `Ptcl_Draw`

**The simulation is baked.** There is no integrator anywhere in `graphic.dll`:
`Ptcl_Draw` reads `ptcl->currentFrame` (`movsxd rax,[rcx+0x20]`, RVA `0x605BB`),
indexes the frame array with it, and emits one quad per live particle from
values already in the file. `Ptcl_SetFrame` (`0x61570`) and `Ptcl_NextFrame`
(`0x613B0`) are `frame % frameCount` — the same clock discipline as
`Phy_NextFrame` (§6.2).

| field | read at | meaning |
|---|---|---|
| `position` | `0x607A3` | passed as a **stride-12 array** to `D3DXVec3TransformCoordArray` (`0x1D1F10`) with `frame.matrix × world` |
| `cellPhase` | `0x607D0` | `i = int(phase × N²)`, then `col = i % N`, `row = i // N`; UV offset `(col/N, row/N)`. **The same flipbook atlas as the PHY ChangeTex channel** (§6.4), addressed by a normalised float instead of an integer key |
| `size` | `0x60808` | half-extent; multiplied by the world matrix's scale (the length of a transformed `(1,1,1)` times `1/√3`, `0x1EC968`) and subtracted from the quad's x and y |
| `matrix` | `0x606F5` | a per-frame `D3DXMATRIX`, premultiplied onto the world matrix before the position array is transformed |
| `ids` (PTCX/PTC3) | `0x5ECC4` | a `u16` key into the owner's `unordered_map<int, PTCL_MATRIX>` (`Ptcl_CreateInfo(int)`), i.e. a per-instance attachment transform supplied by the caller |
| `texGrid` | `0x606D4` | `N`; `Ptcl_Clear` defaults it to **1**, and shipped values are 1–5 and 8 |
| `maxParticles` | `0x6120F` | the vertex buffer is `4 × maxParticles` vertices of **20 bytes** (PTCL/PTCX: position + UV) or **24 bytes** (PTC3, `0x60F84`) |

The vertex writes at `0x6082D`–`0x6085C` are five floats each — `x, y, z, u, v`
— four per particle, which is where the 20-byte stride comes from.

`PTC3`'s two envelopes are ordinary three-level ramps. The system one
(`0x5F5BA`–`0x5F648`) runs on the **frame**: ramp `alpha[0]→alpha[1]` by frame
`fadeFrame[0]`, hold to `fadeFrame[1]`, ramp to `alpha[2]` over the rest, clamp
to `[0,1]`. The per-particle one (`0x5FCD3`–`0x5FD3B`) is the same shape on the
particle's normalised life with `particleLife[]` as thresholds, and the two
multiply.

`PTC3` also parses **`_C3EXP_STRETCH=`** out of its own object *name* with
`strstr` + `atof` (`0x60D75`) and stores the value at `+0x38` — an authoring
tool smuggling a render parameter through the name. No object name in any of
the six declared installs contains the token (checked over the full `.dbc`
corpus, 5,723 `PTC3` chunks), so this is `[V]` from code and **unexercised**
against data.

#### Playable length

A particle system declares 101 frames and empties after a dozen, exactly as the
PHY tracks do. The right length is **one past the last frame with any
particle**: `Ptcl_Draw` returns immediately on `count == 0` (`0x605CF`), so an
empty frame draws nothing in the same way alpha 0 does. 976 of patch5517's
1,259 chunks carry an empty tail, 39,451 dead frames in total.

Unlike the alpha-envelope rule for PHY parts (§6.5, `[I]`), this one is `[V]`:
the engine's own early-out is the evidence, not an inference from the shape of
the data.

#### Rendering, in the same form as §8

```
frame   = the effect clock, as §8
f       = ptcl.frames[frame % ptcl.frameCount]
if f.count == 0: draw nothing this frame
M       = f.matrix x world                      # premultiply, then transform
alphaS  = ptcl.system_alpha(frame)              # PTC3 only, else 1.0
for i in 0 .. f.count-1:
    p   = M * f.position[i]                     # D3DXVec3TransformCoord
    s   = f.size[i] * scale_of(world)
    c   = int(f.cellPhase[i] * N*N)
    uv0 = (c % N / N, c // N / N)               # cell size 1/N
    emit a camera-facing quad centred on p, half-extent s, uv0 .. uv0 + 1/N
```

**It barely mattered for weapons and it matters everywhere else.** Of the 1,134
effects reachable from a weapon only 29 needed particles; of all 2,255 effects,
**464 did** (§9). Those two figures are **CCO's** — `docs/CORRECTIONS.md`
C41(b). On patch5517 with the compiled tables read, **1,200 of 3,391 effects
need a particle system somewhere and 127 are nothing but particles**.

#### And it is drawn — `tools/webui/fx.js`

The pseudocode above is implemented, not pending. `effectplay.particle_quads`
is the reference (with `particle_scale` / `particle_frame` / `particle_bounds`
beside it), `fx.js` mirrors it the way it mirrors `frame_at` and
`sample_part`, and `tools/test_viewer.py::ParticleDrawPath` exercises the
reference against shipped chunks — the atlas cell cross-checked against
`effects.Particle.cell`, the quad's plane checked against a deliberately skew
camera basis, the half-extent checked against a scaled world matrix.

**The one thing the pseudocode leaves to the caller is which plane the quad
lies in.** `Ptcl_Draw` subtracts the half-extent from the quad's x and y
*after* the position array is transformed (0x60808) — the ordinary billboard
idiom, where the caller's world matrix is expected to already face the camera.
There is no such caller in `graphic.dll`'s scope, so the renderer supplies the
camera basis explicitly. `[I]` for the plane; `[V]` for everything the file
states.

**Not applied, and named rather than approximated:** the `PTC3` *per-particle*
alpha envelope (`particleAlpha` / `particleLife`, 0x5FCD3) runs on a
particle's normalised life and a baked frame carries neither a life nor a
birth frame — the input does not exist; and `worldSpace` (+0x34), `billboard`
(+0x30) and `roll` (+0x3C), which are 165, 337 and 333 of patch5517's 2,211
`PTC3` parts. The **system** envelope (0x5F5BA) *is* applied and is **inert on
every one of those 2,211 parts** — all declare `alpha = (1,1,1)` with
`fadeFrame = (0, 0xFFFFFFFF)` — so a flat glow on this base is not evidence
that it works, which is why the test reads the value back instead.

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

## 7a. `ini/3DEffect.dbc` — the compiled twin, and how far it is from the `.ini` — VERIFIED

**On an official 5517- or 6090-era client the plaintext file is not the table
the client reads, and the gap is large.** This section is the number, measured
file against file. **`tools/effects.py` now reads the compiled table** — §9a is
what that did to the derived figures.

`GraphicData.dll` names **both** files — the literal `ini/3DEffect.ini` at
`.rdata 0x921A8` and `ini/3DEffect.dbc` at `0x369A8` in the 6090 build. Where
the compiled twin exists it is the newer file by six years. Reader:
`core/dbc.py::read_effe` (the `EFFE` magic; the layout and its proof live in
that module's docstring — that is the home for the format, this is the
citation).

**A missing `.dbc` is a normal answer, not a failure.** Compiled twins first
appear at **5517**; 5017, 5065, 5165 and CCO ship none at all, and on those
four the plaintext ini **is** the live table — the exact inverse of the 6090
trap. Three of the five official clients are in that state, so any rule of the
form "prefer the `.dbc`" has to mean "prefer it *where it exists*".

### The divergence, per install

Reproduce with `CO_ROOT=<install> py -3 tools/dbcdiff.py`, which prints the
resolved root, `base_id` and both files' size and mtime beside every count.

| | 5517 | 6090 |
|---|---:|---:|
| `.dbc` | 363,958 B, 2011-07-26 | 508,446 B, 2015-05-22 |
| `.ini` | 599,875 B, 2009-04-23 | *the same file, byte for byte* |
| `base_id` | `patch5517-d3ba8e7aa082` | `patch6090-d162f6e2f364` |
| compiled records | **3,391** | **4,483** |
| … distinct names | 3,391 | **4,472** (11 names appear twice) |
| … layers | 8,759 | 13,285 |
| `.ini` sections | 2,595 | 2,595 |
| shared names | 2,595 | 2,594 |
| in the `.dbc` only | **796** | **1,878** |
| in the `.ini` only | 0 | 1 (`red-flower-smallrain`) |
| shared **and identical** | 2,590 | 2,538 |
| shared but **differing** | 5 | 56 |

> **On 6090, reading `ini/3DEffect.ini` misses 1,878 of 4,472 effects — 42.0 %
> of the table — and misreads 56 more of the 2,594 it does carry. It is correct
> about 2,538 of 4,472 effects, 56.8 %.** [V]
>
> On 5517 the same comparison misses 796 of 3,391 (23.5 %) and misreads 5. The
> 2011 table is essentially the 2009 ini plus 796 additions; the 2015 one has
> drifted.

The 56 are content edits, and not all of them are cosmetic. By kind (a record
can appear in more than one row): **Offset 32 · Amount 17 · layer mesh/texture
ids 12 · LoopTime 8 · layer blend 4 · FrameInterval 1.** Offsets dominate
(twenty-four `itemadd*` / `itemaddex*` records moved to `(95, 0, 0)`), but
**seventeen effects changed layer count and twelve changed which mesh and
texture the layer draws** — `luckdiffuse`'s three layers went from
`266/267/268` to `15553/15554/15555`, `itemupgrade01`'s five from `9684…9688`
to `19173…19177`. Those are complete re-arts: a plaintext reader does not draw
a slightly-old effect, it draws a different one.
`py -3 tools/dbcdiff.py --names` lists all 56.

**This is a disagreement, so it needs no defence against the shared-fallback
trap** — a leak that makes two reads resolve to one file can manufacture
agreement, never difference. The positive control is in the table anyway: the
`.ini` column is *one byte-identical file* on both clients (5165, 5517 and 6090
all ship 599,875 bytes stamped 2009-04-23), and comparing it against the two
different `.dbc` files returns two different answers, 5 divergences against 56.
A comparison whose two sides quietly resolved to the same place could not do
that.

### What it costs the numbers in §9

| | via `.ini` | via `.dbc` |
|---|---:|---:|
| 6090 `Action3DEffect.ini` distinct effect names (1,699) that resolve | 1,155 (68.0 %) | **1,654 (97.4 %)** |
| … of its 10,267 rows | 7,140 (69.5 %) | **9,088 (88.5 %)** |
| 5517 `Action3DEffect.ini` distinct names (1,381) | 1,156 (83.7 %) | **1,348 (97.6 %)** |
| 6090 `Action3DEffect1.ini` (43 names) | **0** | 43 |
| 6090 `Action3DEffect2.ini` (9 names) | **0** | 9 |
| 6090 `ActionLee3DEffect.ini` (17 names) | **0** | 3 |

The three auxiliary link tables are 6090-only files (2014–2015) and **not one
of their 69 effect names exists in the plaintext `3DEffect.ini`.** A
plaintext-only reader does not report them as a stale source; it reports them
as undefined effects, which reads like a content bug.

**That prediction is now confirmed end to end, out of `--coverage` rather than
out of a file diff.** `effect_names_referenced_but_undefined` counts names any
link table points at that the definition table does not define:

| `effect_names_referenced_but_undefined` | 5017 | 5065 | 5165 | 5517 | 6090 | CCO |
|---|---:|---:|---:|---:|---:|---:|
| reading the `.ini` (before) | 0 | 0 | 0 | **224** | **543** | 4 |
| reading the `.dbc` (after) | 0 | 0 | 0 | **32** | **44** | 4 |

543 phantom "content bugs" on 6090 were the shadowed table, and **all but one
of the 44 survivors are a different mechanism entirely** — `610009~80~80~80`
and 42 like it, the dual-wield composite spellings of §4, which no table
defines under that name on any base. The one that is not is `_p_4_targetm`.
Note the four plaintext bases do not move at all: `Action3DEffect.ini` has no
compiled twin anywhere, so the *referenced* side of this comparison is the same
file before and after, on every base. A number that should not have moved did
not, which is what makes the two that did move readable.

### Which effect names the plaintext lineage covers

| plaintext catalogue | sections | present in 6090's `.dbc` | absent from it |
|---|---:|---:|---:|
| CCO 2.0 | 2,240 | 2,178 | 62 |
| 5065 | 2,294 | **2,294** | 0 |
| 5165 / 5517 / 6090 (one file) | 2,595 | 2,594 | 1 |

5065's catalogue is a strict subset of 6090's compiled table. CCO's is not — 62
of its names are CCO-only content, a reminder that CCO is a separate lineage
rather than an early point on this one.

### `ini/EmotionIco.dbc` — the other compiled twin

**6090 is the only install of the six that ships one.** 5517 has compiled twins
for fourteen tables and still reads the plaintext `EmotionIco.ini`, so "this
client is official" does not imply "this table is compiled" — gate per file,
not per client. Reader: `core/dbc.py::read_emoi` (`EMOI`).

70 records, `8 + 70 × 36` bytes exactly. All 68 ids the 2008 plaintext carries
have byte-identical names; the compiled table adds **`76 Silver`** and
**`77 CP`**. Ids are not dense — 0…67 then 76, 77 — so indexing by rank is
wrong by two on the only client that has the file. [V]

### Corrections to §7 while I was in there

* **§7's "2,240 sections" is CCO's file, not "the" `3DEffect.ini`.** Measured:
  CCO 2,240 · 5017 2,278 · 5065 2,294 · 5165/5517/6090 2,595. [V]
* **§7's "`ColorEnable` — 108 sections" and "`Lev` — 17 sections" are CCO's
  too.** In the 5165+ file `ColorEnable=1` appears in **all 2,595** sections and
  `Lev` in 1,452; in the 5017 and 5065 files **neither key appears at all**.
  Both counts are per-install, not per-format. [V]
* Three bytes of the compiled record are **preserved and not interpreted** — see
  `core/dbc.py`. `unk64` is **not** `Lev` and `unk62` is **not** `Billboard`,
  both refuted on 5517 where the two tables otherwise agree. Whether `unk63` is
  `ColorEnable` is **OPEN** and cannot be settled from these files: it is ~1
  everywhere on both sides, so the check would read the same if the
  identification were wrong (CORRECTIONS §2).

Tests: `CompiledEffectTable`, `CompiledEmotionIcons`, `CompiledTableRefusals`
in `tools/test_viewer.py` — green on 5517 and 6090, skipping cleanly on the
three clients with no compiled table.

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

```bash
CO_ROOT="<install>" py -3 tools/effects.py --coverage
```

> **Which install.** The single-column tables in this section are **CCO**
> (`cco-3f201d08bbc5`), which is what the original text meant by "this
> install" — it was written before the base id was printed and the reader had
> no way to tell. It is not a small difference: *every* row changes per base.
> **§9a carries all six bases** for the figures that moved. `--coverage` and
> `--validate` emit `root`, `base_id` and `tables_read` as their first keys;
> quote them with the numbers. (`docs/CORRECTIONS.md` §3 already says a count
> in prose is a timestamp — it is a *base* too. Registered as C41.)

> ### Which file each figure came from
>
> The **Effects** and **Playability** blocks below are measured on
> `ini/3DEffect.dbc` plus the compiled `3DEffectObj` / `3dtexture` / `3dobj`
> twins on 5517 and 6090, and on the plaintext ini on 5017 / 5065 / 5165 /
> CCO, which ship no `.dbc` at all and where the plaintext *is* the table the
> client loads. **Verified from the other side too: re-running coverage on CCO
> with the twin-preferring readers changes nothing, on every metric** — which
> is what "absence is the normal answer" has to look like in the output.
>
> That is a **per-file, per-base** decision resolved at the read through
> `core/dbcshadow.py`, not a rule about which client you are on:
> `3DEffect.dbc` ships on 5517 and 6090, `EmotionIco.dbc` on 6090 only.
> `py -3 core/dbcshadow.py report --root <install>` lists what is shadowed
> there; `--coverage`'s `tables_read` block lists what this tool actually
> opened.
>
> The id→path tables are read as the client reads them — the twin **overlaid
> on** the plaintext, twin winning (`read_flat_live`). MEASURED, distinct C3
> paths named by the six path-valued inis:
>
> | base | plaintext | live | invisible before |
> |---|---:|---:|---:|
> | CCO | 12,600 | 12,600 | — (no twins) |
> | 5517 | 5,867 | 8,898 | **34.1 %** |
> | 6090 | 5,867 | 14,113 | **58.4 %** |
>
> **On 6090 more than half of every C3 the client can reach by name was
> invisible to us.** Per-table key counts, ini → live: `3DEffectObj`
> 3,268 → 6,268 (5517) / 9,472 (6090); `3dtexture` 8,793 → 13,810 / 18,645;
> `3dobj` 1,443 → 2,126 / 2,986.
>
> > The last two rows read **13,856 / 18,691** and **2,174 / 3,034** in the
> > edition of this document that first published them. Those counts were
> > inflated by 46 and 48 zero-padded ini keys that the raw-string overlay
> > could not see were spellings of ids the twin already had, so each survived
> > as a second entry for one logical id. `C49-effe-definitions-merge`.
>
> **Two of those six inis are zero bytes** on both installs — `miscmotion.ini`
> and `MountMotion.ini`, whose twins carry 218 / 784 rows on 5517 and
> 218 / 8,758 on 6090. That is a *distinct* failure mode from disagreement and
> a worse one: a zero-byte file reads as **"this table is empty"** rather than
> **"you opened the wrong file"**. Nothing errors, every loop runs zero times,
> and an empty table is indistinguishable from an absent feature.
>
> **It also manufactures false equivalence between bases.** Every shadowed
> `.ini` is *byte-identical* between 5517 and 6090 (8 of 8) while the `.dbc`
> twins differ (7 of 8) — so any cross-base comparison done on the plaintext
> returns "identical" as a tautology, which is exactly what the 5,867 / 5,867
> column above is. This has already produced one wrong published conclusion:
> `ptclprove` reported the two as a byte-identical particle corpus, correct on
> the plaintext and **wrong by 2,303 chunks** on the real one. The same
> tautology reached the effect *definitions*, where `effect_layers_total` read
> 5,099 on both bases; that is settled in §9a and is why this section no
> longer carries a provisional banner.
>
> `WeaponMotion` is deliberately **not** overlaid: 25,944 ini keys against
> 169,968 twin keys with **zero** in common on 6090, and the `& 0xFFFFFFFF`
> wrap that bridges `3dmotion` resolves none of them. `EffectDB` matches those
> keys by string *prefix*, so merging would match spuriously rather than merely
> uselessly. The twin is exposed unmerged as `EffectDB.weapon_motion_twin`;
> bridging the two id spaces is **OPEN** (§9a).
>
> The **Weapons** block is still `ini/weapon.ini` on every base, including the
> two where `weapon.dbc` (a `MESH` table, not `EFFE`) shadows it. Read those
> six rows as plaintext-lineage figures on 5517 and 6090.
>
> The argument for reading the twin is about *which file is authoritative* and
> holds whatever the two files contain: it would still hold if they matched,
> because a zero diff is a fact about today's content and not about which file
> is loaded. **They do not match**, and by how much is §7a.
>
> `tools/meshtex.py::_build_effect_table` still carries a `STALE-INI:` note at
> its own read. **Its old reason — "there is no `EFFE` reader yet" — is
> retracted**; the reader exists and is wired here. meshtex stays plaintext
> because switching moves *its* coverage index, which is that tool's owner's
> call (C21). So meshtex's effect figures are plaintext-lineage and this
> section's are not.

### Weapons

**Still plaintext on every base** — `weapon.ini` is shadowed by `weapon.dbc`
(a `MESH` table) on 5517 and 6090 and is read plaintext there anyway; see §9a.
CCO figures.

| | |
|---|---:|
| weapon appearances in `weapon.ini` | **5,384** |
| … with an attack trail (action `401`/`402`/`403`) | **2,143** |
| … with an always-on aura (action `999`) | **1,286** |
| … with a hit/block impact effect (via type) | **4,436** |
| weapon types with an entry in `WeaponEffect.ini` | 109 (28 real + 81 dual-wield `6XY`) |
| types named in `WeaponSkillName` but with no impact entry | 27 of 48 |

> **That last row could only ever have been a CCO figure, and until 2026-08-09
> it read `0 of 0` everywhere else — silently.** `EffectDB` knew only CCO's
> `WeaponSkillName.json` spelling; every official client ships
> `WeaponSkillName.ini` (a flat `id,name` list, not sectioned ini) and it was
> never opened, inside a bare `except: pass`. So `weapon_types_in_skill_table`
> was **0** on all five, and `weapon_types_missing_impact` was an **empty
> list** — a zero that reads as *"nothing is missing"* when it meant *"the
> table was empty, so nothing could be missing."* Now that both spellings are
> read, the same two fields are:
>
> | | cco | 5017 | 5065 | 5165 | 5517 | 6090 |
> |---|---|---|---|---|---|---|
> | types in skill table | 48 | 48 | 48 | 49 | 50 | **58** |
> | …with no impact entry | 27 | 27 | 27 | 28 | 29 | **30** |
>
> **Any `coverage()` output taken from an official client before 2026-08-09
> carries those two fields as vacuous zeros.** Nothing else in this table is
> affected — the other rows never read that file. See `C53` and
> `docs/handoff_2026-08-09_ani_index_spelling.md` §4.

Sixteen weapon types carry trails: `410 420 421 430 440 450 460 480 481 490 510
530 540 560 561 580`. Fifteen carry none at all — `350 360 370 380 422 500 562
800 801 802 803 804 900 1050 1051` — these are bows (`500`), arrows (`1050`,
`1051`), shields (`900`, which get a blanket aura instead) and accessory types.
A bow's visible projectile comes from `ini/3DFlyingObj.ini`, keyed
`<bowAppearance>.<arrowAppearance>` and naming an `EffectIndex`, a flying sound
and a `TargetEffect`; that path is mapped in `linkage.json` but not decoded
further here.

### Effects

**CCO column, from `ini/3DEffect.ini` — which is CCO's live table.** The same
run on the other five bases is in §9a; on 5517 and 6090 it reads the compiled
twin and every row below changes.

| | CCO |
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

Both columns come from **one run of one build** (`coverage()` emits the old and
the new classification side by side), so this is a before/after of the readers
and not of two corpora.

| | before particles | after |
|---|---:|---:|
| effect objects backed by `PHY`+`MOTI` and/or `SHAP`+`SMOT` | 1,855 | 1,855 |
| … backed by particles only | 431 | 431 |
| … mixing particles with geometry | 22 | 22 |
| effects playable | **1,755** | **2,219** |
| effects needing particles for some layer | 464 | 464 |
| … of those, now playable | 0 | **464 (100 %)** |
| effects with a chunk this build cannot decode | 464 | **0** |
| effects with no loadable geometry at all (data bugs) | 36 | 36 |
| **effects reachable from a weapon** | **1,134** | **1,134** |
| … fully playable | **1,105 (97.4 %)** | **1,134 (100 %)** |
| … needing particles for some layer | 29 | 29 |
| … unresolved | **0** | **0** |

`1,755 + 464 = 2,219`, and `2,219 + 36 = 2,255`, the whole effect name set.
The residual 36 are the pre-existing `3DEffectObj.ini` data bugs of §7 — a
missing `.C3`, not a missing reader. **Particles are no longer a gap for any
effect in any declared install.**

On the configured `patch5517` install (`patch5517-d3ba8e7aa082`) the same run
now gives **2,135 → 3,335**, with **1,200** effects needing particles and
**1,200** of them playable; 56 have no loadable geometry. On `patch6090`,
**2,537 → 4,412**, **1,876** needing particles and all 1,876 playable, 59 with
no loadable geometry. (Those two lines used to read 1,873 → 2,566 / 693 and
were the same number on both bases, which was the defect — §9a.)

> **The predicate.** "Playable" means *every chunk in every C3 this effect's
> layers name has a reader in this build, and the file loads* —
> `EffectObject.playable`. It is deliberately **not** "has a `PHY`": 431 of the
> 464 are pure particle systems and have no `PHY` by construction, so a
> geometry-presence test would have classified the exact population being
> unblocked as unrenderable and produced a confident before/after of zero.
> Checked rather than assumed: on CCO, **1,021 effect objects hold no `PHY`
> part at all** (431 particle, 589 `SHAP` trail, 1 mixed) and
> `EffectObject.playable` is `True` for **1,021 of 1,021**; 60 of 60 sampled
> pure-particle effects build a scene through `effectplay` with drawable parts.
>
> **The `.dbc` blind spot described here is closed.** This note used to read
> *"the patch5517 figures inherit the `.dbc` blind spot: `EffectDB` builds its
> object table from `3DEffectObj.ini` (3,268 rows) and the compiled twin has
> 6,268 … treat 693 as a floor"*. `EffectDB` now builds it from the twin —
> 6,268 rows on 5517 and 9,472 on 6090 — and the floor of 693 resolved to
> **1,200** and **1,876**. The CCO figures never had the blind spot, because
> CCO ships no `.dbc`, and they are unchanged.
>
> **One row of the old table did move for a reason that is not a reader gap.**
> *"effects with a chunk this build cannot decode"* reads **1** on 6090 after
> the change, and it is a mis-named counter rather than a regression: the
> metric marks an effect whose layer is unplayable *for any reason*, including
> a layer whose `.c3` is simply not in the install. 6090's single hit is
> `800540`, which names eight layers — seven whose files do not exist and one
> pointing at `800530/8.c3`, another effect's file. `--coverage` now splits the
> two: **`effects_blocked_by_undecoded_chunk` is 0 on all six bases** and
> `effects_blocked_by_missing_asset` is 1 on 6090 and 0 elsewhere. The
> parser-completeness claim is unaffected; quote the split keys.
>
> ### ~~The `weapon_*` rows are broken on the official clients~~ — FIXED, C35
>
> This note used to read: *"`WILDCARD = "999"`, but 5517 and 6090 widened the
> action sentinel to `9999` … `effects_for_weapon` matches no always-on rule
> at all on those bases: **aura hits 0 of 4,828 on 5517 and 0 of 4,828 on
> 6090** … left unfixed on purpose."* **It was fixed, on
> `comod/per-hand-super`, and the remedy is better than the one this note
> proposed.** Widening the constant would have been wrong — measured, treating
> any all-nines value as the wildcard moves **19,143** action-effect answers on
> 5517 and **30,942** on 6090, almost all of them serving the aura where the
> per-attack trail belongs. The narrow fix is `effects.is_always_on(action)`:
> all-nines *of any width*, asked only where the always-on group is meant.
>
> Current readings — `weapon_appearances_with_aura` **621** on 5165, 5517 and
> 6090 (was 0 on the latter two), 1,286 on CCO, 1,135 on 5017, 585 on 5065;
> `weapon_appearances_with_attack_trail` 2,494 → **2,404** on 5517 and
> 2,495 → **2,405** on 6090, rows that had been mis-attributed to the trail.
>
> **The 621 repeated on three bases is real agreement, and it is worth saying
> why given how much of §9a is about the opposite case.** `Action3DEffect.ini`
> has no compiled twin anywhere, and the three files are demonstrably
> different — 270,899 / 306,060 / 289,537 bytes, three distinct SHA-256s — yet
> each yields the same 621 weapon appearances and 407 distinct aura effects.
> 5165's *mapping* differs from 5517's (same weapons, some different effects);
> 5517's and 6090's are identical. Three different inputs agreeing is evidence;
> one input read three times is not, and that is the whole distinction.

### Parsers

`py -3 tools/effects.py --validate`, counted over every C3 the install names
through **`effects.asset_paths()`** — the ini tables *unioned with their `.dbc`
twins*. Every cell is *parsed / consumed to exactly the declared length*.

| chunk | patch5517 | patch6090 | CCO |
|---|---:|---:|---:|
| `MOTI` | 27,118 / **27,118** | 56,533 / **56,533** | 15,278 / **15,278** |
| `SHAP` | 693 / **693** | 918 / **918** | 543 / **543** |
| `SMOT` | 693 / **693** | 918 / **918** | 543 / **543** |
| `PTCL` | 1,515 / **1,515** | 1,812 / **1,812** | 508 / **508** |
| `PTCX` | 6 / **6** | 8 / **8** | 0 |
| `PTC3` | 1,682 / **1,682** | 3,686 / **3,686** | 355 / **355** |

100 % on all three. The CCO column is unchanged from the previous edition of
this document (CCO ships no `.dbc`); **patch5517's more than doubled** when the
compiled twins were included — `MOTI` 13,508 → 27,118 — which is the size of
the blind spot, not a change in the data. **These three columns did not move
when the definitions path was rewired**, and that is the intended result:
`asset_paths()` already unioned the twins, so the parser corpus was never
blind. It is also the control for §9a — the corpus is constant across that
before/after, so nothing there is a change of corpus.

Particle chunks across **all six declared installs**, `tools/ptclprove.py
--all-bases`:

| base | `PTCL` | `PTCX` | `PTC3` | total | corpus digest |
|---|---:|---:|---:|---:|---|
| patch5017 | 663 | 0 | 0 | 663 | `2e9817b82c50fdd7` |
| patch5065 | 685 | 0 | 0 | 685 | `f8b0ae5fb5ffd2c2` |
| patch5165 | 1,183 | 0 | 72 | 1,255 | `a4a85b4ee4601a61` |
| patch5517 | 1,515 | 6 | 1,682 | 3,203 | `3127e63208d15694` |
| patch6090 | 1,812 | 8 | 3,686 | 5,506 | `0eb60491c7c63174` |
| CCO | 508 | 0 | 355 | 863 | `6e12298b533f3cc0` |
| **total** | **6,366** | **14** | **5,795** | **12,175** | 15 independent pairs |

**12,175 / 12,175 exact.** Six distinct corpus digests, so these are six
measurements and not one printed six times — the tool hashes every chunk body
per base and prints `NOT INDEPENDENT` when two agree. (An earlier draft of this
table did report 5517 and 6090 as byte-identical. That was the ini-only blind
spot again: on the plaintext corpus they genuinely are, and on the real one
they differ by 2,303 chunks.)

### Is the layout actually proved?

Exact-length consumption is the project's stated bar and it is met, but on its
own **it cannot fail for a parser with the fields in the wrong order**: swap the
12-byte position array for the 4-byte size array and the total is unchanged.
`tools/ptclprove.py` is the instrument that separates the two, and it shares no
constants with the parser:

* **12 mutant layouts** — no per-frame tail; a 48-byte tail; a tail on empty
  frames; `u32` ids; no id array; 2-D or 4-wide positions; one or three scalar
  arrays; no second string; a 48- or 52-byte `PTC3` envelope — **all 12 are
  rejected by the corpus** on every base that ships the affected generation.
* **Four value predictions the parser does not enforce**, over **9.6 million
  particles** across the six bases: every flipbook phase in `[0,1]`; every
  64-byte tail block has fourth column `(0,0,0,·)`, which a block misplaced by
  even four bytes cannot manage; no frame's count exceeds `maxParticles`
  (`PTCL`'s loader does **not** check this, so on `PTCL` it is a genuine
  prediction); `texGrid` a small side length. All pass on all six.
* **Three content anomalies, none of them layout**, and all scored apart so
  they cannot be mistaken for either:
  * **15 NaN phases** in 5 files (CCO `nba_kobe/6.c3`, 5517/6090
    `halo_huo_fir/1.c3`, 6090 `weapon/6130{7,8,9}9/4.c3`) among 9.6 M — each on
    a particle whose position and size are finite and sensible.
  * **one matrix with `m[15] = 0`** (6090 `sen_gif_sen/1.c3`, frame 69) whose
    other 15 floats are an exact identity and whose neighbouring frame is a
    clean identity. The alignment test is the three exact zeros at
    `m[3]`,`m[7]`,`m[11]` — which hold — so this is one authored scalar, not a
    misplaced read. A wrong offset produces 16 wrong floats, not 15 right ones.
  * `PTC3` `particleLife` values outside `[0,1]` — 2 on 5517, 16 of 7,372 on
    6090, 0 on CCO. Left OPEN in §10 rather than explained away.

---

## 9a. Wiring `EFFE` onto the definitions path — what moved — VERIFIED

`tools/effects.py::EffectDB` used to enumerate effect definitions from
`ini/3DEffect.ini` on every base. It now reads `ini/3DEffect.dbc` through
`core/dbc.py::read_effe` wherever `core/dbcshadow.compiled_twin` says there is
one, and the `RSDB` twins of `3DEffectObj` / `3dtexture` / `3dobj` with it.
Landed on `comod/effe-definitions`; registered as `C50-effe-definitions`.

Every figure below is `py -3 tools/effects.py --coverage`, one process per
base, `CO_ROOT` set, **all six `base_id`s checked distinct in the same run**.
A dash means the number did not move.

| `--coverage` key | 5017 | 5065 | 5165 | 5517 | 6090 | CCO |
|---|---:|---:|---:|---:|---:|---:|
| `effect_names_defined` | — 2,278 | — 2,294 | — 2,595 | 2,595 → **3,391** | 2,595 → **4,472** | — 2,255 |
| **`effect_layers_total`** | — 3,832 | — 3,872 | — 5,099 | 5,099 → **8,759** | 5,099 → **13,248** | — 3,987 |
| `effect_layers_mesh_found` | — 3,752 | — 3,803 | — 4,975 | 4,980 → **8,530** | 4,980 → **12,982** | — 3,825 |
| `effect_layers_texture_found` | — 3,745 | — 3,800 | — 4,968 | 4,973 → **8,536** | 4,973 → **12,989** | — 3,804 |
| `effects_fully_resolved` | — 2,251 | — 2,269 | — 2,562 | 2,563 → **3,338** | 2,563 → **4,415** | — 2,207 |
| `effects_partially_resolved` | — 7 | — 7 | — 7 | 7 → **0** | 7 → **1** | — 7 |
| `effects_unresolved` | — 20 | — 18 | — 26 | 25 → **53** | 25 → **56** | — 41 |
| `effect_objects_total` | — 2,265 | — 2,277 | — 3,268 | 3,268 → **6,268** | 3,268 → **9,472** | — 2,351 |
| `particle_chunks_in_effect_objects` | — 750 | — 772 | — 1,848 | 1,852 → **4,265** | 1,852 → **6,700** | — 913 |
| `effects_playable_today` | — 2,254 | — 2,272 | — 2,565 | 2,566 → **3,335** | 2,566 → **4,412** | — 2,219 |
| `effects_playable_geometry_only` | — 1,763 | — 1,773 | — 1,873 | 1,873 → **2,135** | 1,873 → **2,537** | — 1,755 |
| `effects_needing_particles` | — 491 | — 499 | — 692 | 693 → **1,200** | 693 → **1,876** | — 464 |
| `effects_no_geometry` | — 24 | — 22 | — 30 | 29 → **56** | 29 → **59** | — 36 |
| `effect_names_referenced_but_undefined` | — 0 | — 0 | — 0 | 224 → **32** | 543 → **44** | — 4 |
| `weapon_reachable_unresolved` | — 4 | — 4 | — 4 | 5 → **4** | 5 → **4** | — 0 |
| `duplicate_effect_names` (new key) | 0 | 0 | 0 | 0 | **11** | 0 |
| `effects_blocked_by_undecoded_chunk` (new key) | 0 | 0 | 0 | **0** | **0** | 0 |
| `effects_blocked_by_missing_asset` (new key) | 0 | 0 | 0 | 0 | **1** | 0 |

Everything not listed was identical on all six bases before and after —
including `effect_names_referenced`, `weapon_reachable_effects` and the whole
*Parsers* block.

> **Read the two columns as "merge base → this change", and nothing else.**
> `origin/master` moved fourteen commits while this branch was open, and two
> of them touch the same output. Isolating the attribution matters more than
> a single tidy table, so:
>
> * **C35 (`comod/per-hand-super`) moved two §9 *Weapons* rows**, not this
>   change: `weapon_appearances_with_aura` 0 → **621** on 5517 and 6090, and
>   `weapon_appearances_with_attack_trail` 2,494 → 2,404 / 2,495 → 2,405. §9
>   carries the retraction.
> * **`C45-ini-shadow-class`'s overlay landed on the id→path tables first**
>   and moved two rows on its own — `effect_layers_texture_found` 4,973 →
>   4,980 and `effects_fully_resolved` 2,563 → 2,570 — while the definitions
>   were still plaintext. The "before" column above predates that, so those
>   two rows fold both changes together; every other row is this change alone.
> * **Reconciling the two implementations of the overlay at merge moved
>   nothing at all.** Re-running all six bases after the merge reproduces
>   every figure in this table exactly, which is the check that says the
>   union-vs-substitution difference and the padded-key fix were numerically
>   inert on the effect path (`C49-effe-definitions-merge`).
>
>   *(Cited as bare `C48` until 2026-08-09. That entry was renumbered
>   C48 → C49 under "published wins" when `C48-invisible-to-diff` reached
>   master, and this citation was not updated with it — so it did not dangle,
>   it silently retargeted onto an entry about merge-invisible prose. Cite the
>   full slug, not the bare number.)*

### `effect_layers_total` — the question the banner asked

**5,099 on 5517 and 5,099 on 6090 → 8,759 and 13,248.** The two clients do not
agree and never did; the old equality was one 2009 file being read twice.
`ini/3DEffect.ini` is byte-identical (599,875 bytes, 2009-04-23) on 5165, 5517
and 6090, so **any cross-base comparison taken through the old figures could
only return "identical"** — it was a property of the reader, not of the data.
Note that 5165 still reads 5,099, and correctly: it ships no `.dbc`, so there
that file *is* the table. The tautology was 5165 ≡ 5517 ≡ 6090; what survives
is 5165 alone.

**Why 13,248 and not the 13,285 layers `core/dbc.py` reports for 6090.**
`--coverage` counts layers over a **name-keyed** table and 6090's compiled
table holds 4,483 records under **4,472 names** — eleven names appear twice
(`FF03_1`, `FF07_1`, `FF08_1`, `FF09_1`, `CircleUp4_1`, `TakeOnLilySuit-1`,
`TakeOnOrchisSuit-1`, `elf-follow`, `fire-bomb_1`, `flyflower-3`, `zf2-e222`).
`EffectDB` keeps the **last** record for a repeated name, matching what
`read_sections` does for a repeated `[section]`, and the eleven shadowed
records carry **37 layers**: `13,285 − 37 = 13,248`. Both members of each pair
declare the same layer count, so the choice does not change any figure here —
but which record the *client* uses is **not decidable from this file**, and a
silent choice would have looked like knowledge. `duplicate_effect_names` is
emitted so a name count is never mistaken for a record count. 5517 has no
duplicates and its 8,759 matches `core/dbc.py` exactly.

### The choices, and what each cost

* **The compiled table is substituted for the plaintext one, not unioned with
  it.** Where a twin exists the client reads the twin, so an ini-only
  *definition* is one the client does not have. Measured cost: **0 names on
  5517, 1 on 6090** (`red-flower-smallrain`). This is deliberately the
  opposite of `asset_paths()`, which *unions* — there the question is "what
  assets exist", here it is "what does the client define".
* **The three id→path tables go the other way — union, twin winning
  (`read_flat_live`) — and that is not an inconsistency.** There the question
  is "what assets exist", where an ini-only row still names a real file. The
  two rules were built independently by two teams (substitution here, union on
  `master`) and **they are numerically equivalent on this path**: re-resolving
  every layer of 6090's 4,472 effects through the plaintext tables as a
  fallback rescues **0 meshes and 0 textures**, so the choice was settled on
  the argument rather than on a number. Union kept, because the argument is
  better.
* **`ini/WeaponMotion.ini` is deliberately left plaintext even on 5517 and
  6090.** `weaponmotion.dbc` parses cleanly (130,049 / 169,968 rows) and its
  ids **join 0 of the ini's 25,944** — the ini spells keys as 12–13 decimal
  digits (`500000999310`), the twin as u32 (`668057766`), and no reduction
  tried joins them (identity, the `& 0xFFFFFFFF` wrap that works for
  `3dmotion`, truncation by one/two/three digits). The rows do correspond:
  within one mesh the ini's low three digits track the twin's exactly (ini
  310/320/330/331 ↔ dbc 766/776/786/787, a constant +456). ~~Only the
  composition is unknown.~~ Wiring it on `str(id)` would have returned **zero
  matches and no error** — absence reported with total confidence, the exact
  failure `docs/CORRECTIONS.md` §2 catalogues four instances of. Two teams
  reached this independently and `read_flat_live` now enforces it as a
  *mechanism* rather than a name check: **any** table whose twin shares no key
  with it keeps the plaintext and records `disjoint` in `tables_read`. The
  twin is still exposed, unmerged, as `EffectDB.weapon_motion_twin`.

  > **The composition is SOLVED (2026-08-09) and the `+456` was its shadow.**
  > It is a key **width**: the ini writes the action in 3 decimal digits, the
  > twin in 4, wrapping — `id = (appearance * 10_000 + action) mod 2**32`.
  > **25,944 of 25,944 on 5517, 0 wrong**; every alternative width scores
  > zero on both compiled bases. `dbc.weaponmotion_key` /
  > `dbc.weaponmotion_join` implement it and the join **raises** rather than
  > returning `{}`.
  >
  > **Keeping the plaintext is now a CHOICE with a measured cost, not a
  > forced hand** — and the cost is larger than "deliberately left plaintext"
  > suggests. `WeaponMotion.ini` is **byte-identical on all five official
  > clients** (md5 `edf77851440297a05dd3fcfb0d797a1e`) while the twin is not,
  > so the ini is frozen 2009 content. On **5517** it resolves all 25,944 of
  > its rows but names only **19.9%** of the twin; on **6090** it resolves
  > **2,808** and names **1.7%**, with 2,133 of its 2,375 appearances absent
  > from the compiled table entirely. Whoever owns this table should now
  > decide on evidence rather than on the join being impossible.
  > `CORRECTIONS.md` C-2026-08-09-weaponmotion-key.
* **`ini/weapon.ini` is left plaintext** on 5517/6090 too. Its twin is a
  `MESH` table whose row shape is not the ini's section shape, and three other
  tools read `EffectDB.weapon_appearances` for `Mesh0`. That is the appearance
  table's owner's call, not this change's. The §9 *Weapons* rows are labelled
  accordingly.
* **Zero-padded ini keys are folded onto the twin's spelling, and that fixed a
  live defect in published code.** `3dtexture.ini` *looks* like it holds 53
  ids 6090's twin lacks and **46 of the 53 are padded spellings**
  (`001137360`) of ids the twin has as `1137360`; `3dobj.ini`, 48 of 49. The
  overlay that shipped on `master` compared raw strings, so all 94 survived
  the merge **beside** the rows they duplicate — inflating `3dtexture` to
  13,856 / 18,691 and `3dobj` to 2,174 / 3,034. Corrected in
  `read_flat_live`; the counts above are the fixed ones.
  `C49-effe-definitions-merge` has the full entry, including the part that
  keeps it honest: **all 94 ghost pairs carried the same path under both
  spellings**, so nothing was reading a stale value — it was right for the
  wrong reason, and would have gone wrong the first time one of those ids was
  re-pointed.
* `EffectDB._table_get` additionally tries the canonical spelling at *lookup*
  time, for the case the per-file gate makes possible but no shipped base is
  in: compiled definitions beside a plaintext path table. It changes no number
  above and is pinned by a unit test rather than by a base.

### What this does not settle

* `unk62` / `unk63` / `unk64` are carried through to `EffectDef.extra`
  **uninterpreted**. `color_enable`, `billboard` and `lev` therefore read as
  their defaults on a compiled base rather than being fabricated from bytes
  whose meaning is refuted (`unk64` ≠ `Lev`, `unk62` ≠ `Billboard`) or
  unfalsifiable here (`unk63` vs `ColorEnable`). §7's `ColorEnable` and `Lev`
  counts remain plaintext-only facts.
* ~~The `WILDCARD = "999"` sentinel defect is untouched: aura still resolves 0
  of 4,828 appearances on 5517 and 6090.~~ **RETRACTED, and it was already
  false when written on this branch** — `comod/per-hand-super` fixed it via
  `is_always_on` while this branch was in flight, and the fix reached `master`
  before the merge. Aura now resolves **621** of 4,828 on both. The retraction
  is kept rather than deleted because it is a live instance of the thing this
  register keeps recording: a "still open" note is a claim with a timestamp,
  and this one aged in under a day. See §9 and C35.
* `tools/meshtex.py` and `tools/worldfx.py` still enumerate effects from the
  plaintext file. Their figures are plaintext-lineage; this section's are not.

---

## 10. Verified vs inferred, at a glance

**VERIFIED — read out of code or proven on the whole corpus**

* `MOTI` / `SHAP` / `SMOT` / `PTCL` / `PTC3` binary layouts, and all four `MOTI`
  encodings. The particle layouts additionally survive 12 mutation tests and
  four value predictions on two independent corpora (§9).
* `PTCL` / `PTCX` / `PTC3` are dispatched by tag in `sub_1A1E0` at `0x1A567`,
  `0x1A5C3`, `0x1A623` — three generations, not two.
* The particle simulation is **baked**: one solved particle set per frame, no
  integrator in `graphic.dll`, clock `frame % frameCount` (`Ptcl_SetFrame`
  `0x61570`).
* The particle flipbook is the same `N × N` atlas as the PHY ChangeTex channel,
  addressed by a normalised float (`Ptcl_Draw` `0x607D0`).
* A particle system's playable length is one past its last non-empty frame —
  `Ptcl_Draw` early-outs on `count == 0` (`0x605CF`).
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
* The `EFFE` and `EMOI` record layouts (§7a; `core/dbc.py`), decoded against
  the plaintext twins and cross-checked against `3DEffectobj.dbc` /
  `3DTexture.dbc`, walking to exactly EOF on 5517 and 6090.
* **On 6090 the plaintext `3DEffect.ini` misses 42.0 % of the live effect
  table and misreads 56 more entries** (§7a). On 5517, 23.5 % and 5.
* Compiled twins first appear at 5517; three of the five official clients ship
  none, and there the plaintext ini is the live table.
* The compiled layer's `scale` is a f32 fraction and the plaintext `Scale<i>`
  the same value as an integer percent — **5,099 of 5,099 shared layers agree
  under `ini / 100` on 5517**, which is an independent check on the layer
  layout as well as a field identification (`core/dbc.py`).
* **`effect_layers_total` is 8,759 on 5517 and 13,248 on 6090** (§9a), from
  the compiled table. The two clients' effect tables are not equal; the
  earlier equality at 5,099 was one plaintext file being read on both.

**OPEN — measured, cause identified, not settled**

* ~~`weaponmotion.dbc`'s key encoding.~~ **SOLVED 2026-08-09 — it was a key
  WIDTH.** The ini writes the action in a **3-digit** decimal field; the twin
  packs it in a **4-digit** one and wraps:
  `id = (appearance * 10_000 + action) mod 2**32`. Verified **25,944 of
  25,944 on 5517, 0 wrong**, every alternative width scoring **zero** on both
  compiled bases. `dbc.weaponmotion_key` / `dbc.weaponmotion_join` implement
  it, and the join **raises rather than returning an empty map**. The `+456`
  above was the shadow of the extra digit. See `CORRECTIONS.md`
  C-2026-08-09-weaponmotion-key — which also records what the join then exposed: this
  ini is **byte-identical on all five official clients**, so it is frozen,
  and on 6090 it is a **1.7% view** of the live table.
* `unk62` / `unk63` / `unk64` in the compiled effect record — preserved
  uninterpreted; `unk63` vs `ColorEnable` is not answerable from these files.

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

**VERIFIED FROM CODE, UNEXERCISED AGAINST DATA** — a third grade, because
calling these `[V]` unqualified would overstate them

* **`_C3EXP_STRETCH=`** in a `PTC3` object name (`0x60D75`). The `strstr`/`atof`
  is read from code; **no object name in 5,795 `PTC3` chunks across six
  installs contains the token.** Nothing has ever run it on real bytes.

  *`PTCX` was in this list for one draft and has been promoted to VERIFIED:
  14 chunks across two installs, all exact. It was listed here because the
  files holding them are named only in the `.dbc` tables. Absence measured
  against an incomplete enumeration is not absence.*

**OPEN**

* **What `PTC3`'s `billboard` (`+0x30`) and `roll` (`+0x3C`,`+0x40`) select.**
  The branch structure is read (`0x5F499`: modes 1 and 4 take the camera-facing
  path, everything else builds a basis from the view matrix; `roll` feeds a
  random angle through `sub_92540`), but the *enumeration* is not named
  anywhere and the shipped values are small integers with no legend. Enough to
  render the common case, not enough to claim the meaning of each value.
* **Whether `PTC3`'s `particleLife` thresholds are always normalised.** Nearly
  all shipped values lie in `[0,1]` as the divide at `0x5FCE8` implies, but not
  all: 2 outliers on patch5517 and **16 of 7,372** on patch6090 (`5.0`, `15.0`,
  `2.0`), 0 on CCO. Either the field is dual-purpose or those files are
  authored wrong; not resolved.
* **The `.ini`/`.dbc` split is much wider than the effect tables.** `PTCX` was
  found only because the compiled twins were enumerated; nothing else in this
  repo that walks `PATH_INIS`-shaped tables has been audited for the same blind
  spot, and `EffectDB` itself still builds `objs` / `textures` / `weapon_motion`
  from the plaintext (`3DEffectObj.ini` 3,268 rows against
  `3DEffectobj.dbc`'s 6,268 on patch5517). §9's per-base coverage figures
  inherit that. Sized and registered as C41(e); **not** fixed here, because
  changing `EffectDB`'s tables changes `linkage.json` and every viewer number
  built on it.
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

Written by `py -3 tools/effects.py --linkage`. UTF-8, compact (`--pretty` to
indent). **The size is per base and tracks the size of the effect table**:
~3.9 MB on CCO, **~10 MB on 6090** now that the compiled tables are read.
Anything built from an older 6090 bundle is a plaintext-lineage artefact —
rebuild it. Top-level keys:

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
| `effect_object_geometry` | `"4100" -> {path, kinds, frames, effective_frames, parts, playable, source}` — which animation form each object uses, and whether this build can play every chunk in it |
| `coverage` | §9, machine-readable, `root` and `base_id` first |

`kinds` values are `phy_motion`, `shape_trail`, `particle`. `playable` is
`false` — with an `undecoded` list naming the parts — when the file holds a
chunk this build has no reader for. It is **not** a synonym for "has
particles": particles are playable, and the field exists so a future
undecodable form shows up instead of being counted as geometry.

---

## 12. Reproducing

```bash
py -3 tools/effects.py --weapon 410009          # a super Blade: trail + aura + impact
py -3 tools/effects.py --weapon 421229          # a super Backsword
py -3 tools/effects.py --effect m-b02           # the blunt impact spark, 3 layers
py -3 tools/effects.py --effect Flash4102       # a trail: SHAP line + segments
py -3 tools/effects.py --action-map 104 330 002 # -> Feather
py -3 tools/effects.py --effect Health          # a pure particle burst
py -3 tools/effects.py --validate               # the exact-length proof
py -3 tools/effects.py --coverage
py -3 tools/effects.py --linkage
py -3 tools/ptclprove.py --all-bases            # the particle layout proof
py -3 tools/effectplay.py --effect m-b02        # the renderable scene
```

As a module:

```python
from effects import EffectDB, shape_ribbon, IDENTITY
db = EffectDB()
db.sources["3DEffect"].form                     # 'dbc' on 5517/6090, 'ini' elsewhere
db.sources["3DEffect"].path.name                # the file the definitions came from
es = db.effects_for_weapon("410009")            # -> WeaponEffectSet
eff = db.resolve(es.attack["401"])              # -> EffectDef with asset paths
objs = db.load_geometry(es.attack["401"])       # -> [EffectObject] with parsed chunks
m = objs[0].parts[0].motion.matrix(bone=0, frame=12)
```
