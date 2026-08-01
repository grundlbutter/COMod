# Part attachment — how the client puts a helmet on a head

How `ImConquer` positions, orients and scales an equipped part on a character.
Recovered by disassembling `Role3D.dll` and `graphic.dll` instruction by
instruction, then applied to the shipped assets and checked numerically.

Every claim is marked **VERIFIED** (read out of the machine code, with the RVA
cited so it can be re-checked with `py -3 tools/disfn.py <dll> <rva>`, or proven
against the whole corpus) or **INFERRED** (a rule that reproduces the data, whose
ground truth is inside the Themida-packed `ImConquer.exe`).

Companion tool: **`tools/attach.py`** — module + CLI. Every number below comes
out of `py -3 tools/attach.py --validate`.

```bash
py -3 tools/attach.py --body 002135000 --armet 002119342 --r-weapon 410009
py -3 tools/attach.py --sockets 002135000            # every socket on a body
py -3 tools/attach.py --sockets 004134000 --action   # ... in the idle pose
py -3 tools/attach.py --motis                        # what part MOTIs contain
py -3 tools/attach.py --validate                     # the proof
```

---

## 1. The answer in one page

```
                       ini/armet.ini[001111310].Mesh0 = 001111010
                                    |
   ini/3dobj.ini  ------------------+--> c3/mesh/001111010.c3
                                                 |
   PHY chunks  ->  phy[0..n]      MOTI chunks -> motion[0..n]     (two ordinal
                       |                              |            lists, §3)
                       +----------- i == i -----------+

   socket:   idx  = bodyMesh.FindPhyByName("v_armet")      # stricmp, §2
             Msock = Motion_GetMatrix(bodyMotion[idx], bone=0, frame)

   part:     Mbone = Motion_GetMatrix(partMotion[c], vertex.bone, frame)

   vertex:   p_world = (SUM_k w_k * (p_bind x Mbone_k)) x Msock x Mrole
```

Row-vector convention throughout: `p' = p * M`, basis in rows 0–2, translation
in row 3, exactly like the PHY chunk matrix (`docs/modding.md` §9.4).

**The piece the viewer was missing is `Mbone` — the part's *own* `MOTI`.**
`docs/viewer.md` §5 already applies `Msock`; what it does not apply is the
equipped part's own bone-0 matrix, and that matrix is where the part's **scale**
and its **axis correction** live:

| | armet.ini | weapon.ini | armor.ini |
|---|---:|---:|---:|
| meshes with a resolvable path | 707 | 441 | 766 |
| … whose own bone-0 matrix scales by more than 1 % | **180** | **252** | 102 |
| … whose own bone-0 matrix rotates by more than 5° | **142** | **296** | 182 |
| … by more than 45° (almost always exactly 90°) | 141 | **286** | 135 |

Concretely, `410009` (a Blade) is mesh `c3/mesh/410000.c3`, whose geometry spans
**359 units** — 2.1× the 170-unit body. Its own `MOTI` bone 0 is a uniform
**0.252** scale. Apply it and the sword is **85 units**, half the body height.
That is the reported "renders several times the character's height".

---

## 2. Finding a socket — VERIFIED

### 2.1 By name, case-insensitively, over the PHY array

`C3Mesh::FindPhyByName` is `graphic.dll` **`0x266D0`**, vtable slot **`+0x78`**
of the mesh vtable at `.rdata 0x1E8638` (installed by the mesh ctor at
`0x24A0E`). It is a linear scan:

```c
for (i = 0; i < this->phyCount /* +0x164 */; i++)
    if (stricmp(this->phys[i] /* +0x168 */ ->name, want) == 0)
        return i;
return -1;
```

The compare is `0x841B0`, a hand-rolled **case-insensitive** `strcmp`
(`lea eax,[rcx-0x41]; cmp eax,0x19` → uppercase, `add 0x20`). So `V_ARMET`,
`v_armet` and `v_Armet` all match. **No hashing, no index table, no ordering
assumption.**

### 2.2 The returned index is used directly on the motion list

`Role3D!sub_86C0` (**`0x86C0`**) is the socket resolver. Given a role, an
out-parameter and a dummy name:

```
0x86F2  GameDataSetQuery()                     -- the mesh cache
0x8710  for i in 0 .. role->meshCount (+0x140):
0x8713      mesh = role->meshes[i] (+0x230)  or  Load(role->meshIds[i] (+0x144))
0x8749      idx  = mesh->vtbl[0x78](dumyName)          <- FindPhyByName
0x874E      if idx == -1: continue (unless a fallback index was passed in r9d)
0x876F      out->motion      = mesh->vtbl[0xC0](idx)   <- phys[idx]->motion  (+0x78)
0x8780      out->blendMotion = mesh->vtbl[0xC8](idx)   <- phys[idx]->blend   (+0x80)
0x878A      out->pMatrix     = &role->localMatrix (+0x4C)
            return
```

`vtbl+0xC0` is `0x26880`: `return phys[idx % phyCount]->[0x78]`. The motion
belongs to the **phy at the same ordinal**; nothing re-matches by name.

A role can hold several meshes (`mount.ini` rows are `Part=2`), which is why
`sub_86C0` searches all of them and takes the first hit.

> **Which of these is live.** `sub_86C0` is the one on the drawing path:
> `sub_2F20` (Draw, `0x2FAB`) -> `sub_3600` (`0x3681`) -> `sub_86C0`.
> `Role3D` also contains three sibling helpers with the identical lookup —
> `sub_87B0`, `sub_88A0`, `sub_8A20` — that have **no caller and no pointer
> anywhere in the DLL** (checked with `tools/xref.py` and `tools/riprefs.py`,
> and by scanning every 8-byte aligned and unaligned absolute pointer in the
> image). They are leftovers, quoted here only because they spell the same
> algorithm out more plainly. The picking paths `sub_8CA0` (`0x8D7C`) and
> `sub_8E60` (`0x8F39`) *are* live and use the same lookup, which is the
> cross-check.

### 2.3 The matrix is bone 0, always

`Role3D!sub_87B0` (`0x87B0`) — the by-name "get dummy matrix" helper — and
`sub_8E60` (`0x8F39`) both evaluate the socket with **bone index 0**:

```
0x8AAE   xor r8d, r8d                  ; bone = 0
0x8AB1   call CMyBitmap::Motion_GetMatrix(motion, frame, bone, &out)
```

That is correct because a socket chunk is rigid: `v_armet` on `002135000` is a
24-vertex box and every vertex carries `BLENDINDICES0 == 0`.

### 2.4 Argument order of `Motion_GetMatrix` — settled

The brief flagged this as a trap, and it is. Three overloads, and the thunks
give the answer without ambiguity:

| RVA | signature | what it does |
|---|---|---|
| `0x551A0` | `Motion_GetMatrix(C3Motion*, unsigned long, int, D3DXMATRIX*)` | the real one |
| `0x26DE0` | `CMyBitmap::Motion_GetMatrix(C3Motion*, int, unsigned long, D3DXMATRIX*)` | swaps args 2/3, wraps arg 2 |
| `0x26E10` | `CMyBitmap::Motion_GetMatrix(C3Motion*, unsigned long, D3DXMATRIX*)` | supplies the current frame |
| `0x55480` | `Motion_GetMatrix_Blend(C3MotionTracker const&, unsigned long, D3DXMATRIX*)` | blend of two motions |

`0x26DE0` is four instructions:

```
mov r10d,[rcx+0x04]        ; frameCount
mov r11d,r8d               ; save arg3
mov r8d,edx                ; arg2 ->
test r10d,r10d ; je +      ;
div r10d ; mov r8d,edx     ; ... wrapped MODULO frameCount   => arg2 is the FRAME
mov edx,r11d               ; arg3 -> arg2 of the callee      => arg3 is the BONE
jmp 0x551A0
```

Only a frame index gets wrapped by `frameCount`, so:

> **`Motion_GetMatrix(motion, bone, frame, out)`** — argument 2 is the **bone**,
> argument 3 is the **frame**. `0x551A0` confirms it by range-checking argument 2
> against `motion->[0x00]` (the bone count) at `0x551B3`.

The three-argument `CMyBitmap` overload takes `(motion, bone, out)` and fills the
frame from **`C3Motion+0x18`**, the motion's own current frame. `Phy_SetFrame`
(`0x5C140`) writes that field as `frame % motion->frameCount`.

`C3Motion` layout, from these two functions: `+0x00` boneCount, `+0x04`
frameCount, `+0x18` currentFrame. `tools/effects.py`'s `Motion.matrix(bone,
frame)` already has the order right; this is the independent confirmation.

---

## 3. Two ordinal lists, not adjacent pairs — VERIFIED, and a live bug

`MeshCreate` (`graphic.dll` `0x28360`) builds the phy array from a file and then
calls `MotionCreate` (`0x283CB`) on the *same* file to build a separate motion
set. Neither reader looks at the other's position. `C3Mesh::SetMotion`
(`0x277C0`, vtable `+0xA8`) then distributes them **by ordinal**:

```
0x27810  for i in 0 .. phyCount:
0x27818      phy = this->phys[i]
0x27823      rax = motionSet->vtbl[0x58](i)        ; motion #i
0x27828      phy->[0x78] = rax
```

with a single guard at `0x277F5`: the motion set is rejected outright if it has
fewer entries than the mesh has phys.

So **`MOTI` number *i* belongs to `PHY` number *i*, wherever the chunks sit in
the file.** Pairing a `MOTI` with the `PHY` that happens to precede it is not the
same thing, and it really does differ:

```
py -3 tools/attach.py --validate     (section 6)

armor.ini    interleaved 270   GROUPED 440   single-chunk 56
armet.ini    single-chunk 695  GROUPED  12
weapon.ini   single-chunk 428  interleaved 3   GROUPED  10
mount.ini    GROUPED 1
```

**440 of the 766 `armor.ini` body meshes store all their `PHY` chunks first and
all their `MOTI` chunks after them.** On those files an adjacent-pairing reader
produces zero pairs, so every socket comes back unplaced. `tools/parts.py`
`moti_sockets()` pairs adjacently, which is why it silently falls back to the
skin-cluster estimate — *position only, no rotation* — on the majority of
bodies. The four bodies it was tested against (`001131000`, `002135000`,
`003133000`, `004134000`) all happen to be interleaved.

Demonstration on the one resolvable mount, `c3/mount/850/8500000.c3`
(`PPPPPPPPMMMMMMMM`):

| | ordinal pairing (correct) | adjacent pairing |
|---|---|---|
| `v_mount` | `(0.00, 0.21, up 87.0)` | not found |
| `v_armet` | `(42.2, −317.6, up 121.0)` | not found |
| `v_pet` | `(0, 0, 0)` | `(0,0,0)` from the 106-bone body motion |

Across the 2,002 loose `.c3` files: 901 interleaved, **447 grouped**, 343
motion-only, 311 with neither. **Zero files have a `PHY`/`MOTI` count mismatch**,
so the ordinal pairing is always well-defined.

`tools/attach.py:PartMesh.parse` collects the two lists separately and zips them
by index. This is the single change most likely to fix hair orientation for the
majority of bodies.

---

## 4. The transform chain — VERIFIED

### 4.1 A part is its own role, attached to the body role

`Role3D!sub_3600` (**`0x3600`**) is the attachment-chain builder, and it is
recursive. Each attachment node holds two `std::string`s — a parent role name
(`node+0x08`, length at `+0x18`) and a dummy name (`node+0x28`, length at
`+0x40`):

```
0x364B   parent = ResolveRoleByName(node->parentName)
0x3681   sub_86C0(parent, &tracker, node->dumyName, -1)     ; §2.2
0x36xx   push tracker into the list
0x371E   sub_3600(..., parent's own attachment node)        ; recurse upward
```

So the object graph is *role -> parent role at a named dummy*, arbitrarily deep.
A character is a body role plus one role per equipped part, plus (when mounted)
the mount role above the body.

### 4.2 Draw folds the chain into the world matrix

`Role3D!sub_2F20` (**`0x2F20`**, slot 3 of the `I3DRole` vtable at
`.rdata 0x5E588`):

```
0x2FB0   M = callerWorldMatrix                     (copied in, 4 x xmm)
0x2FD5   if (this->mirrorX /* +0x64 */)  negate M.m00, M.m01, M.m02
0x3035   for each tracker t in the chain:
             Motion_GetMatrix_Blend(t, bone = 0, &Mi)
0x3058       D3DXMatrixMultiply(&M, &Mi, &M)       ; M = Mi * M
0x3072   Role::SetMatrix(impl, &M, r15)
0x3077   PrepareModel()
0x30D3   Role::Draw(...)
```

`D3DXMatrixMultiply(out, A, B)` is `out = A*B`, and D3DX is row-vector, so `A`
is applied to the point **first**. One attachment link therefore gives
`M = Msocket * Mparent`, which is the natural reading.

Note the **X-mirror flag** at `+0x64`: the engine flips the first row of the
world matrix rather than scaling by −1 on a separate axis. A viewer that mirrors
a character must do the same or the winding will disagree with the engine's.

### 4.3 `Role::SetMatrix` and the per-role local matrix

`Role3D!sub_A4B0` (**`0xA4B0`**):

```
if (this->useLocal /* byte +0x11A */):
    tmp        = this->local (+0x4C) * M          ; 0xA4FA
    this->world (+0xC) = tmp * M2                 ; 0xA50B
    for each mesh:  mesh->vtbl[0x130](tmp, M2, &this->world)
else:
    this->world (+0xC) = M * M2                   ; 0xA579
    for each mesh:  mesh->vtbl[0x130](M,  M2, &this->world)
```

Role object layout, from the ctor `sub_7140` (`0x7140`): `+0x0C` world matrix
(identity at `0x71C7`–`0x71DC`), `+0x4C` local matrix (identity at `0x71FE`–
`0x7216`), `+0x11A` `useLocal`, `+0x140` mesh count, `+0x144+4i` mesh ids,
`+0x154+4i` texture ids, `+0x230+8i` loaded mesh pointers.

**`useLocal` is written in exactly one place in the whole DLL** — the ctor, which
sets it to 0 (`0x717B`). Nothing in `Role3D.dll` ever sets it true, so in this
build the local matrix is identity and `world = M * M2`. **A per-role scale
therefore does not come from here** (see §6).

### 4.4 `C3Mesh::SetMatrix` hands each phy three matrices

`graphic.dll` `0x27B30` (vtable `+0x130`) copies, for every phy:

| phy offset | contents |
|---|---|
| `+0xD0` | `M1` — the world matrix built in §4.2 |
| `+0x110` | `M2` — the caller's outer matrix |
| `+0x150` | the pre-multiplied `M1 * M2` |

### 4.5 `Phy_Calculate` fills the bone palette from the phy's own motion

`graphic.dll` `0x56040`. After the three `C3Key` channels
(`docs/effects.md` §6.4) it does, at `0x56280`:

```c
tracker = { motion: phy->[0x78], blend: phy->[0x80], ... };   // 0x5624F, 0x56254
for (b = 0; b < paletteCount; b++) {                          // palette at +0x1C0..+0x1C8
    boneIdx = phy->palette[b];
    Motion_GetMatrix_Blend(tracker, boneIdx,
                           &desc.matrices[boneIdx]);          // 0x5629B
}
```

and when the caller passes `useMotion == false` it writes identity into every
palette slot instead (`0x562C0`–`0x56455`). `Mesh::Draw` (`0x25E90`) always
passes **true** (`0x2607E`: `mov r9b,1`), so **the engine never draws a bind
pose** — the motion is always applied.

Putting §4.2–§4.5 together, one vertex of an equipped part ends up at

```
p_world = ( SUM_k  w_k * ( p_bind x Mbone_k ) )  x  Msocket  x  Mrole  x  M2
```

* `p_bind` is the file position **after** the chunk's own 4×4 (`Phy_Load` applies
  it at load time, `graphic.dll` `0x5A735` — `docs/modding.md` §9.5 item 3;
  `c3phy.apply_matrix_to` reproduces it).
* `Mbone_k` comes from the **part's own** motion, indexed by the vertex's
  `BLENDINDICES0/1`, at the part's own current frame.
* two influences maximum, `w1 = 1 - w0` (`not al`, `0x5A869`).
* `Msocket` is §2, evaluated on the **body's** motion at the **body's** frame.
* `Mrole` / `M2` are identity for a static preview.

---

## 5. Orientation — VERIFIED

There is **no fixed per-slot rotation** and no axis-convention special case. All
of the orientation lives in two ordinary matrices, and both are full 4×4s:

1. **The socket matrix** carries a real rotation. On `002135000`,
   `v_armet` is 2.0° off identity but `v_l_weapon` is 180.0° and `v_r_weapon`
   159.7° — the hand grips point the weapon backwards and inwards relative to
   body axes. Any placement that uses only the socket's translation puts every
   weapon at the wrong angle by construction.

2. **The part's own bone-0 matrix** carries the rest. This is the one the viewer
   drops. Measured over the shipped tables (`py -3 tools/attach.py --motis`):

   * **weapon.ini: 296 of 441 meshes rotate by more than 5°, 286 by more than
     45°, and the median rotation is exactly 90.0°.** Weapon geometry is authored
     with the blade along a different axis and the `MOTI` corrects it.
   * **armet.ini: 142 of 707 rotate by more than 5°, 141 by more than 45°.**

   Expressed as displacement rather than angle — how far the own-`MOTI` moves the
   geometry as a fraction of the part's own size, `--validate` section 4:

   | armet series | n | moves >10 % of the part | >50 % |
   |---|---:|---:|---:|
   | 119 (modern hair) | 278 | 52 | 46 |
   | 111 (legacy hair) | 40 | 18 | 8 |
   | 113 | 40 | 28 | 23 |
   | 114 | 40 | 37 | 36 |
   | 112 | 12 | 10 | 8 |
   | 115 | 8 | 4 | 4 |

   150 of 422 (body-type, mesh) pairs are visibly displaced, 126 of them by more than half
   their own size. That is the hair-orientation bug.

47 of 707 armet meshes and 189 of 441 weapon meshes have a **non-uniform** scale
in that matrix (`|sx − sy| > 1 %`), so it cannot be reduced to a scalar — it has
to be applied as a matrix.

### Which hair family is which

Both hair families in the brief are real and they are different things.

* **`armet.ini` series 111/112/113/114/115/116** — the legacy head-covering
  entries. `001111310` = body 001, series 111, colour 3, style 1, `Mesh0 =
  001111010`, resolving to `c3/mesh/001111010.c3`, chunk name `v_armet01`. Its
  own bone-0 matrix is a **non-uniform** scale `(0.940, 0.912, 0.940)`.
* **`armet.ini` series 119** — the modern family, **515 of the 707** distinct
  armet mesh ids, most of them resolving into **`c3/hair/`** via `ini/3dobj.ini`
  (1,210 appearances land in `c3/hair/`, 700 still in `c3/mesh/`)
  (`002119342 = c3/hair/002119042.c3`). Note the id shift: the appearance's
  4th–6th digits are `119` and so are the file's, but digits 7–9 differ
  (`342 -> 042`) — the appearance encodes the colour, the mesh does not.
  The visible geometry in these files is the chunk called **`v_body`**; many of
  them additionally carry their own `v_armet` / `v_l_weapon` / `v_r_weapon`
  socket chunks, which must not be drawn.

So: series-111 entries do **not** point at `c3/hair/`; the two families coexist
and both are reached through `armet.ini`. `ini/HairMotion.ini` and
`ini/headmotion.ini` are **0 bytes** and `ini/armetmotion.ini` does not ship, so
head parts have no per-action motion table and always use the `MOTI` embedded in
their own mesh (§7).

**Do not use the embedded 3DSMax label** for any of this — measured, only 6 of
1,936 resolve as a real path.

---

## 6. Scale — VERIFIED

Scale comes from **the part's own bone-0 `MOTI` matrix** and from nowhere else.
Specifically, none of these is the source:

* **`Role3D!LoadAdjustConfig` (`0x7000`) is not attachment tuning.** It reads
  `ini/role3d.ini` with the format string `"%d %d %d %d"` (`0x7050`), stores two
  ints into globals `0x79254`/`0x79258` and a flag at `0x79250`, and on failure
  falls back to `CMyBitmap::GetObliqueAngle()` twice (`0x707F`, `0x708B`). Its
  only consumer is `SyncObliqueAngle` (`0x70B0`), the camera's oblique
  projection angle. **`ini/role3d.ini` does not ship in this build**, so the
  function always takes the fallback path. It has nothing to do with parts.
* **The role's local matrix (`+0x4C`)** is identity — `useLocal` is never set
  (§4.3).
* **`ini/` has no per-part scale column.** `armet.ini` / `weapon.ini` rows carry
  only `Part`, `Mesh0`, `Texture0`, `MixTex0`, `ThirdTex0`, `FourthTex0`,
  `MixOpt0`, `MixData0`, `Asb0`, `Adb0`, `ZBuffer0`, `Material0`.
* **The socket matrix has unit scale.** Measured on all four reference bodies:
  `1.000` on `v_armet`, `v_l_weapon` and `v_r_weapon`.

Measured effect, `--validate` section 5 — longest bounding-box span of every
`weapon.ini` mesh placed at `v_r_weapon` on `002135000` (height 170.4):

| type | n | with own MOTI | own MOTI dropped | median scale | median rot |
|---|---:|---:|---:|---:|---:|
| 410 blade | 24 | **97.4** (0.57×) | 142.2 | 0.823 | 90.0° |
| 420 | 23 | 121.7 (0.71×) | 182.8 | 0.774 | 90.0° |
| 421 | 18 | 93.0 (0.55×) | 138.7 | 0.563 | 90.0° |
| 430 | 18 | 106.7 (0.63×) | 158.4 | 0.693 | 90.0° |
| 490 | 16 | 47.3 (0.28×) | 91.2 | 0.789 | 90.0° |
| 510 polearm | 21 | 254.8 (1.50×) | 271.7 | 0.855 | 90.0° |
| 560 | 23 | 225.4 (1.32×) | 287.1 | 0.808 | 90.0° |
| 580 | 18 | 242.1 (1.42×) | 326.3 | 0.838 | 90.0° |
| **all** | **441** | **116.1** | **134.9** | — | — |

One-handed types land at 0.28–0.71× body height; the 1.3–1.6× families are the
polearms, spears and staves, which are genuinely longer than a person. Scale is
*not* a constant: 252 of 441 weapon meshes and 180 of 707 armet meshes carry a
non-unit scale, and 189 weapon meshes carry a non-uniform one.

Worked example — the case in the brief:

```
py -3 tools/attach.py --body 002135000 --r-weapon 410009

body 002135000  c3/mesh/002135000.c3   height 170.4
  r_weapon 410009  c3/mesh/410000.c3
     socket v_r_weapon = chunk 3 bone 0 frame 0 of the mesh's own MOTI
     own MOTI scale (0.252,0.252,0.252) rot 6.5 deg
     placed  x[-16.9,-10.2] y[-68.2,17.2] up[90.1,125.1]
     longest span 85.4  = 0.50 x body height
```

Raw, with the own-`MOTI` dropped, that mesh spans **359 units** — 2.1× the body.

---

## 7. Per-slot differences

The mechanism is **one mechanism**. The user's hunch is right: the same
name-lookup + bone-0 + compose chain covers hair, helmets, weapons, shields and
mounts. What varies is only (a) the dummy name and (b) where each slot's
*motion* comes from.

### 7.1 Socket per slot

`ini/RolePart.ini` gives 13 parts and 52 dummies, and the parts map onto the
dummies by name: `armet -> v_armet`, `head -> v_head`, `l_weapon ->
v_l_weapon`, `r_weapon -> v_r_weapon`, `misc -> v_misc`, `mount -> v_mount`,
`pelvis -> v_pelvis` — 7 exact `v_<part>` matches out of the 8 attachable parts.
`shield` is the exception: there is no `v_shield`, only `v_l_shield` /
`v_r_shield`, and the slot is not shipped in this build.
**INFERRED**, but the body meshes carry exactly the expected chunks: of 766
resolvable `armor.ini` meshes, 703 have `v_armet`, 704 `v_l_weapon`, 705
`v_r_weapon`.

### 7.2 Mounts are inverted — INFERRED

A mount is not hung off the body; the body is hung off the mount. The only
resolvable mount mesh, `c3/mount/850/8500000.c3`, carries its own socket set —
`v_mount` at `(0.00, 0.21, up 87.0)` dead centre (the saddle), plus `v_armet`,
`v_l_arm`, `v_r_arm`, `v_l_shield`, `v_r_shield`, `v_pet`. Body meshes do **not**
carry `v_mount` (10 of 766 carry `v_pet`). Since `sub_3600` builds an arbitrary
parent chain, mounting is simply re-parenting the character role onto the mount
role at `v_mount`. `mount.ini` rows are `Part=2` — a mount is two meshes in one
role, which is why `sub_86C0` searches every mesh of the parent.

### 7.3 Which motion each slot uses

`RolePart.ini` names a `MotionIni<N>` per part. What actually ships:

| part | MotionIni | shipped? | what it contains |
|---|---|---|---|
| body | `ini/3dmotion.ini` | 229,481 rows | motion `.c3` files, `c3/000N/<weaponType>/<action>.c3` |
| armet / armet_dx8 | `ini/armetmotion.ini` | **absent** | — |
| head | `ini/headmotion.ini` | **0 bytes** | — |
| (hair) | `ini/HairMotion.ini` | **0 bytes** | — |
| l_weapon / r_weapon / shield | `ini/weaponmotion.ini` → `WeaponMotion.ini` | 1,863 rows | **mesh** paths, not motions (`docs/effects.md` §4.5) |
| misc | `ini/miscmotion.ini` | 4.1 MB | motion `.c3` under `c3/miscmotion/` |
| mount | `ini/MountMotion.ini` | 1.4 MB | motion `.c3` under `c3/mount/` |
| pelvis | `ini/pelvismotion.ini` | **absent** | — |

So **head parts and weapons always animate from the `MOTI` embedded in their own
mesh**; only the body, misc and mount get an external motion set bound over them
with `SetMotion`. A weapon changes its *mesh* per action instead of its motion.

---

## 8. Per-frame behaviour, and what a static preview should use

### 8.1 The transform genuinely varies with the frame — VERIFIED

Sockets are motion nodes. `Role3D!sub_9B00` (`0x9B00`) is `Role::SetFrame`: it
calls `mesh->vtbl[0xE0](frame)` on **every mesh of the role**, and that
(`graphic.dll` `0x27610`) calls `Phy_SetFrame` on every phy, which stores
`frame % motion->frameCount` into `C3Motion+0x18`. One frame counter per role;
each attached part role has its own.

The body's own animation is bound by `Role::SetMotion` (`Role3D` `0x9C49` →
`graphic.dll` `0x277C0`) from `ini/3dmotion.ini`, keyed
**`<bodyDigit><weaponType3><action3>`** — e.g. `2000100 = c3/0002/000/100.c3`
(body 2, unarmed, idle). **VERIFIED**: the chunk list of that motion file mirrors
the body mesh's chunk list name-for-name and index-for-index on all four bodies,
which is exactly what the ordinal binding in §3 requires:

```
001  chunks 4/4  order MATCHES  v_armet,v_l_weapon,v_r_weapon,v_body
002  chunks 4/4  order MATCHES  v_body,v_armet,v_l_weapon,v_r_weapon
003  chunks 4/4  order MATCHES  v_armet,v_l_weapon,v_r_weapon,v_body
004  chunks 4/4  order MATCHES  v_armet,v_l_weapon,v_r_weapon,v_body
```

Note that the order differs *between bodies* — another reason not to assume a
fixed chunk layout.

### 8.2 What frame to preview — and a caveat that matters

`Mesh::Draw` always applies the motion (§4.5), so **there is no bind pose to
show**. The honest static preview is *some* motion at *some* frame. Two options,
and they are not equivalent:

**(a) the mesh's own embedded `MOTI` at frame 0** — what `tools/parts.py` uses
today. Cheap, needs no ini lookup, and for three of the four bodies it is within
a unit of the idle pose. But it is **whatever pose the artist saved**, and for
`004134000` that is the **T-pose**:

```
                 embedded MOTI          idle action motion
v_armet          (x -0, up 191)         (x 1, up 186)
v_l_weapon       (x 98, up 154)         (x 33, up 84)
v_r_weapon       (x -99, up 154)        (x -32, up 83)
```

Hands straight out at shoulder height versus hands at the sides. A weapon
equipped on body `004134000` with option (a) floats out at arm's length:

```
py -3 tools/attach.py --body 004134000 --r-weapon 410009
    placed  x[-125.4,-92.8] y[-68.9,16.8] up[145.1,156.8]
py -3 tools/attach.py --body 004134000 --r-weapon 410009 --action
    placed  x[ -40.1,  7.5] y[-84.3,-5.3] up[ 72.6, 90.3]
```

**(b) the idle action motion, `3dmotion.ini[<bodyDigit>000100]`, frame 0** —
`tools/attach.py --action`. This is what the game shows a standing character and
it is the recommendation. For bodies 001/002/003 it agrees with (a) to within
1.3 units on every socket; for 004 it is the difference above.

Sockets on the four reference bodies, option (a), frame 0:

| body | height | `v_armet` | `v_l_weapon` | `v_r_weapon` |
|---|---:|---:|---:|---:|
| `001131000` | 167.2 | 156.7 | 82.3 | 95.7 |
| `002135000` | 170.4 | **164.4** | 81.6 | 97.8 |
| `003133000` | 176.2 | 172.7 | 82.1 | 80.3 |
| `004134000` | 195.9 | 190.8 | 153.6 (T-pose) | 153.6 (T-pose) |

`002135000` reproduces the known-good reference in the brief: `v_armet` at
**164.4** on a **170.4**-tall body.

### 8.3 Head-part placement, checked

Top of the placed part relative to the top of the body, over every distinct
`armet.ini` mesh on its matching body type (`--validate` section 4):

| series | n | median | p90 |
|---|---:|---:|---:|
| 119 (modern hair) | 278 | **+4.0** | +17.3 |
| 111 | 40 | +18.1 | +24.7 |
| 112 | 12 | +19.9 | +26.7 |
| 113 | 40 | +9.4 | +39.7 |
| 114 | 40 | +11.0 | +30.4 |
| 115 | 8 | +14.6 | +20.4 |
| 116 | 4 | +3.2 | +4.0 |

Hair sits a few units proud of the skull; helmets and hats rise 9–20. Nothing
lands at the feet, nothing floats. Worked example:

```
py -3 tools/attach.py --body 002135000 --armet 002119342
  armet 002119342  c3/hair/002119042.c3
     socket v_armet = chunk 1 bone 0 frame 0
     own MOTI scale (1.000,1.000,1.000) rot 0.0 deg
     placed  x[-9.6,12.4] y[-15.0,13.9] up[134.7,173.6]     # body top 170.4
```

---

## 9. Which chunks to draw

The engine does **not** decide this by name. `Mesh::Draw` (`0x25E90`) skips a
phy when the byte at `C3Mesh+0x268+i` is non-zero (`0x25F53`), exposed as vtable
slot `+0x38` (`0x26AC0`). That flag is written by the packed exe, so the rule
behind it is not recoverable statically.

The reconstruction in `tools/attach.py` is: **a chunk is a socket iff its name
is one of the 52 `[Dumy]` entries in `ini/RolePart.ini`** (case-insensitive),
with `v_body` excluded because that is the visible geometry. This matters —
hair meshes ship chunks called `v_armet01` and `v_armet02` that are *real
geometry* (46 and 20 meshes respectively), so a `v_`-prefix test hides them and
the part renders empty.

---

## 10. Implementing this in the viewer

Minimal change list, in order of impact:

1. **Pair `PHY` and `MOTI` by ordinal, not adjacency** (§3). Fixes socket
   extraction on 440 of 766 body meshes and on every mount.
2. **Apply the equipped part's own `MOTI`** (§4.5). For a rigid part this is one
   matrix, `Motion_GetMatrix(partMotion[chunk], 0, frame)`, pre-multiplied onto
   the socket matrix:
   `model = M_partBone0 * M_socket` (row-vector) —
   or, in the column-major GL order the viewer already uses,
   `model_gl = toRender(M_socket) * toRender(M_partBone0)`.
   `tools/attach.to_render()` does the `S M S` conjugation with
   `S = diag(1, 1, −1)`; it agrees with the matrix `parts.Anchor.matrix` already
   emits.
   For a skinned part index per vertex bone and blend with `w1 = 1 − w0`.
3. **Filter socket chunks by the `[Dumy]` list, not by a `v_` prefix** (§9).
4. **Prefer the idle action motion** `3dmotion.ini[<bodyDigit>000100]` frame 0
   over the mesh's embedded `MOTI` for both the body pose and the socket
   matrices (§8.2). Bind it by ordinal over the body mesh's chunks.
5. Optional, for correctness rather than for these bugs: the engine never draws a
   bind pose, so the body itself should also be skinned by its motion. Median
   per-vertex movement between the stored positions and the frame-0 pose is
   0.0–1.0 units with a p90 of 18–55, i.e. a visibly different stance on
   `001131000` / `002135000` / `003133000` and no change at all on `004134000`.

---

## 11. Verified vs inferred, at a glance

**VERIFIED — read out of code, RVA cited above**

* Socket lookup is by **name**, case-insensitive, linear over the phy array
  (`graphic.dll 0x266D0`, `0x841B0`).
* The returned index selects the motion at the **same ordinal**
  (`Role3D 0x86C0`, `graphic.dll 0x26880`, `0x27818`).
* The socket matrix is **bone 0** (`Role3D 0x8AAE`, `0x8F34`).
* `Motion_GetMatrix(motion, bone, frame, out)` — argument order proven by the
  `frameCount` modulo in the thunk at `0x26DE0`.
* `C3Motion` `+0x00` boneCount, `+0x04` frameCount, `+0x18` currentFrame.
* Composition order and row-vector convention (`Role3D 0x3058`, `0xA4FA`,
  `0xA50B`, `0x8D93`, `0x8F4C`).
* Bone matrices come from the phy's **own** motion (`Phy_Calculate 0x5629B`) and
  the engine always applies them (`Mesh::Draw 0x2607E`).
* `MOTI` and `PHY` are two ordinal lists (`SetMotion 0x27818`), and 447 of 2,002
  loose files really do group them.
* The X-mirror flag flips row 0 of the world matrix (`Role3D 0x2FD5`).
* `LoadAdjustConfig` is the camera oblique angle, not part tuning
  (`Role3D 0x7000`, `0x70B0`).
* The role's local matrix is never enabled in this build (`+0x11A` written only
  by the ctor at `0x717B`).
* Per-phy visibility is an explicit flag at `C3Mesh+0x268+i` (`0x25F53`).
* `3dmotion.ini` key format, and that its chunk list matches the mesh's.

**INFERRED — reproduces the data, ground truth is in the packed exe**

* `RolePart.ini` part name → `v_<part>` dummy (7 of 8 exact; `shield` guessed as
  `v_l_shield`).
* Mounts being the parent rather than the child, at `v_mount`.
* Identifying socket chunks by `[Dumy]` membership (the engine uses a flag the
  exe sets).
* Which action a static preview should show (idle `100` is the obvious choice
  and matches the always-on aura convention in `docs/effects.md` §2.1).
* Left/right weapons resolving independently, as `docs/effects.md` §2.4 already
  noted.

**OPEN**

* What sets the per-phy hide flag, and whether anything besides sockets is
  hidden.
* The second matrix `M2` passed to `Role::SetMatrix` — the exe supplies it; for a
  static preview identity is right, but its in-game contents are unread.
* `armet1.ini` (254 distinct meshes) and `misc.ini` (59) resolve almost nothing
  through `ini/3dobj.ini` or the probing fallback in `coassets.resolve_asset`, so
  those two slots are untested here.
* `Motion_GetMatrix_Blend`'s cross-fade path (`0x554BE` onward) — the engine
  blends two motions during action transitions. Only the single-motion branch is
  decoded; a static preview never reaches the other.
