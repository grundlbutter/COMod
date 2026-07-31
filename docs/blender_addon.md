# Blender addon — Conquer Online C3 meshes

Import and export the game's `.c3` meshes in Blender, with a hard guarantee:
**import a mesh, export it without touching anything, and you get back the same
bytes.** That is what makes it safe to edit one vertex and ship the result.

Tested on **Blender 5.2.0 LTS** on Windows 11. Requires Blender 4.2 or newer
(it installs as an Extension). No Python packages to install — the addon is
pure stdlib plus what Blender already ships.

---

## 1. Install

```powershell
py -3 tools\build_addon.py
```

That writes `build\io_scene_c3.zip`. Then, in Blender:

1. **Edit → Preferences → Add-ons**
2. Click the **▾** button (top right of the Add-ons list) → **Install from Disk…**
3. Pick `build\io_scene_c3.zip`
4. Tick the checkbox next to **Conquer Online C3 (mesh)** if it is not already on
5. Expand it and check **Game install root**. It is filled in automatically by
   the same auto-detection the command-line tools use (`core/coroot.py`:
   `CO_ROOT`, a saved config, the conventional paths, then the Windows
   uninstall registry). The panel says `found:` with the path when it worked.
   If it did not, type your install path in — or leave it blank and set the
   `CO_ROOT` environment variable, which every tool in the repo honours.

The install root is used **read-only**, to find textures. The addon never
writes anything into the game folder — not even a cache. Archived textures are
copied into your temp directory instead.

If you would rather run it from source without installing, add the repo's
`blender\` folder to Blender's script path, or just:

```python
# adjust to wherever you cloned the repo
import sys; sys.path.append(r"D:\src\co-client-re\blender")
import io_scene_c3; io_scene_c3.register()
```

---

## 2. Import

**File → Import → Conquer Online Mesh (.c3)**

Pick any `.c3` — try `c3\mesh\002192465.c3` to start. A character mesh usually
gives you four objects:

| object | what it is |
|---|---|
| `…_0_v_body` | the actual visible, skinned mesh |
| `…_1_v_armet` | the helmet **socket** — a 24-vertex marker, not geometry |
| `…_2_v_l_weapon` | left-hand weapon socket |
| `…_3_v_r_weapon` | right-hand weapon socket |

Those tiny meshes are attachment points, not things you see in game. Their
names are the `Dumy` entries from `ini\RolePart.ini`.

### Options

| option | default | notes |
|---|---|---|
| **Materials and textures** | on | resolves the DDS through the appearance tables and wires it into a Principled BSDF, so you see the textured model |
| **Armature and vertex groups** | on | rebuilds the 2-bone skin as a real armature |
| **Bone spacing** | 4.0 | spacing of the placeholder bone ladder — cosmetic only |

### What you get

- **Geometry** with the winding already correct. The importer applies
  `(x, y, −z)` and *keeps* the original triangle order; that single-axis mirror
  both stands the model up in Blender's Z-up world and flips handedness, which
  is what converts Direct3D's clockwise-front winding to Blender's.
- **UV map `uv0`** (and `uv1` on `PHY5`, which nothing in this build ships).
- **Vertex colours** in a colour attribute called `C3Color`.
- **An armature** named `<file>_skeleton` with one bone per palette entry, and
  matching vertex groups `bone_000`, `bone_001`, …
- **A material** pointing at the real DDS.

### Two things to know about the armature

The bone **rest positions are placeholders** — a labelled ladder up the Z axis.
The real skeleton lives in the `MOTI` chunks, which are still undecoded. The
bones are there so the weights are visible and paintable, not so you can pose
the character. In rest pose an armature modifier is the identity no matter
where the bones sit, so this cannot distort your mesh.

Skinning is **exactly two influences per vertex**, and the engine derives the
second weight as `255 − weight0`, so the pair always sums to 1. If you paint
three influences onto a vertex, the exporter keeps the two heaviest and
renormalises them.

### The model is huge and the viewport clips

Character meshes are around 178 units tall. Press `N`, open the **View** tab
and raise **Clip Start/End**, or just press `Home` to frame everything.

---

## 3. Export

**File → Export → Conquer Online Mesh (.c3)**, or the **C3** panel in the 3D
view's N-sidebar.

The exporter works on a whole *collection*, not one object — one `.c3` is one
collection. It finds it from the selection, the active object, or (if there is
only one C3 collection in the scene) automatically.

| option | default | notes |
|---|---|---|
| **Selected objects only** | off | otherwise the whole collection exports, in the original chunk order |
| **Recompute bounding box** | off | leave off to keep the original bytes; **turn on after moving vertices** |
| **Allow adding/removing meshes** | off | lets the mesh *count* change. Read the section below before using it |

That last one matters. The declared bounding box is what the engine
frustum-culls against, so a mesh that grows past its old box can vanish at
certain camera angles. Turn it on whenever you actually moved geometry.

### What the exporter preserves

Everything the file contains, including several fields the engine reads and
then ignores: the `unknown0` value, the raw name and label bytes (the label is
usually the original 3DSMax source-texture path, often GBK-encoded Chinese),
the two-group vertex/face count split, the unsorted bounding-box pair, the
`C3Key` animation channels, and the trailing `STEP` / `2SID` / billboard tags.
Non-geometry chunks — `MOTI` (animation), `CAME`, `PTCL`, `SHAP` — are carried
through untouched from the original container.

### Limitations, stated plainly

- **Adding or removing whole meshes is off by default, and only safe for
  effects.** See “Adding and removing meshes” below.
- **Only triangles.** The format is a triangle list. Triangulate first
  (`Ctrl+T` in edit mode) or the export errors out with the offending polygon.
- **Max 65,535 vertices** per mesh — indices are `u16`.
- **Split UVs get collapsed.** C3 stores one UV per *vertex*, not per corner,
  so a seam that splits a vertex's UV cannot be represented. The first corner
  wins and you get a warning in the console.
- **Editing normals does nothing** on `PHY ` and `PHY4`, the two variants this
  build ships — they do not store normals at all; the engine generates them
  from the faces at load time.

### Adding and removing meshes

Each `PHY` mesh chunk is paired with a `MOTI` animation track **by position** —
the i-th mesh is animated by the i-th track. Nothing in a `MOTI` chunk names the
mesh it belongs to, so position is the only link. Every shipped container that
has a mesh has exactly as many tracks (5,083 of 5,083, no exceptions).

The addon handles that correctly: turn on **Allow adding/removing meshes** and
deleting a mesh object also deletes its motion track, while adding one gets a
freshly synthesised static track. Duplicating an object is recognised as a new
mesh, not as a replacement for the one you copied.

**But it is only safe for self-animated meshes — effects.** Character meshes
(body, armour, hair, weapon, mount, monster, NPC) are animated by *shared
external motion sets*: the motion-only `.c3` files under `c3/0001/`… that
`ini/3dmotion.ini` names. Those bind by ordinal too, across the file boundary,
and they are shared by thousands of appearances and live inside the archives.
Change a character mesh's mesh count and the body ends up driven by a one-bone
weapon-socket track. A mod cannot re-cut those shared sets, so this is not a
tooling limitation that better code could fix.

The guard rail is in `comod.py`, which refuses to stage a mesh-count change
unless the file is provably self-animated:

```powershell
py -3 tools\comod.py stage-mesh exported.c3 c3/effect/weapon/cat_long/6.c3
#   mesh count changed 2 -> 1 (MOTI 2 -> 1)
#   motion binding: FREE -- named by ini/3DEffectObj.ini and not referenced
#                   by any appearance table
```

```powershell
py -3 tools\comod.py stage-mesh exported.c3
#   motion binding: LOCKED -- referenced as a Mesh<i> by an appearance table
#   refusing without --force.
```

The classifier is default-deny: only a container named by `ini/3DEffectObj.ini`
and *not* referenced by any appearance table counts as free. Over the loose tree
that is 565 free, 655 locked, 796 unclassifiable (also refused). Full derivation
in `docs/modding.md` §11.

---

## 4. Installing your edit into the game

Export, then hand the file to `comod.py`, which owns installation (staging,
diffing, backups, uninstall). The addon deliberately does not install anything
itself.

```powershell
py -3 tools\comod.py stage-mesh path\to\exported.c3
py -3 tools\comod.py diff
py -3 tools\comod.py install --dry-run
py -3 tools\comod.py install --yes
```

`stage-mesh` re-validates the file before it can reach the install: the
container has to walk cleanly, every PHY chunk has to re-parse to exactly its
declared length, and no triangle index may point past the vertex array. It also
refuses (without `--force`) a file whose chunk layout no longer matches the
original.

`comod.py uninstall --yes` reverts, restoring anything it displaced from
`mods\backup\`.

Two facts make this safe:

- The client resolves **loose files on disk before the archives**, so a mod is
  just a mirrored file tree. `c3.wdf` and `data.wdf` are never touched.
- `integrity.json` covers only 155 `.DMap` maps and 7 `ini\*.json` files.
  **Meshes and textures are not integrity-checked at all.**

---

## 5. Recommended first edit

```powershell
py -3 tools\comod.py show 002192465
```

Import the mesh it names, move one vertex in edit mode, export with
**Recompute bounding box** on, then:

```powershell
py -3 tools\comod.py stage-mesh exported.c3
py -3 tools\comod.py diff
```

`diff` should report `MODIFIED` with the same byte count. If you instead export
*without* editing anything, `diff` will say `same` — that is the round-trip
guarantee doing its job.

---

## 6. Tests

```powershell
# format layer only, runs on bare system Python, no Blender needed
py -3 tests\test_roundtrip.py

# add/remove keeps every surviving mesh's motion track intact
py -3 tests\test_structural.py

# the addon's acceptance test
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
    --background --python tests\test_blender_roundtrip.py -- --count 400

# the zip really installs and registers
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
    --background --factory-startup --python tests\test_addon_install.py

# when a round trip is not byte-identical, say WHICH FIELD moved
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
    --background --python tests\c3_diagnose.py -- path\to\file.c3
```

Current results:

| gate | result |
|---|---|
| `tests\test_roundtrip.py` — every PHY chunk in the install | **15,103 / 15,103 byte-exact** |
| whole `.c3` containers rebuilt byte-exactly | **5,122 / 5,122** |
| `tests\test_blender_roundtrip.py` — import → export in Blender | **400 / 400 byte-identical** |
| modify a vertex → export → re-import | **3 / 3**, exactly one vertex changed |
| delete a mesh → export → re-import | **3 / 3**, PHY and MOTI both drop by one |
| `tests\test_structural.py` — add/remove across the loose tree | **1,348 containers, PASS** |
| `tests\test_addon_install.py` | **PASS** |

---

## 7. How it is put together

```
core/c3phy.py      parse a PHY chunk        pure stdlib, no bpy
tools/c3write.py    serialize a PHY chunk    pure stdlib, no bpy
tools/c3tex.py      mesh -> DDS resolution   pure stdlib, no bpy
tools/build_addon.py  vendors those into the addon and zips it

blender/io_scene_c3/
  __init__.py       operators, menus, preferences
  c3_common.py      the round-trip contract, axis conversion, packing helpers
  c3_import.py      C3 -> Blender
  c3_export.py      Blender -> C3
  c3_material.py    DDS -> Principled BSDF
  vendor/           generated copies of the tools/ modules
```

The format code carries **no `bpy` dependency** on purpose: the corpus
round-trip gate runs the exact same code on bare system Python, so a format bug
gets caught without launching Blender. `tools/build_addon.py` copies those
modules into `vendor/` and rewrites their few top-level imports to be
package-relative; every rewrite is asserted, so renaming something in `tools/`
breaks the build loudly instead of silently shipping a stale copy.

**Edit the modules in `tools/`, never the copies in `vendor/`,** then re-run
`build_addon.py`.

### Why byte-exactness needs more than "read the fields back"

Three Blender representations are lossy or ambiguous projections of the file,
so for each one the importer stashes the exact original next to the editable
view, and the exporter uses the original **only when the view still matches
it** — edit the view and your edit wins:

1. **UVs.** The Blender convention is `(u, 1 − v)`, and `1 − v` is not
   invertible in float32: measured on the shipped corpus, **32,227 of 1,475,160
   V coordinates (2.2%) do not survive a double flip**. The untouched `(u, v)`
   lives in a hidden `c3_uv0_src` attribute.
2. **Skinning.** The two bone slots are ordered, and **`bone0 == bone1` on
   201,057 of 737,580 corpus vertices (27%)** — 2,280 of them with a real
   second weight. Blender cannot put the same vertex group on a vertex twice,
   so the exact slots live in `c3_bone0` / `c3_bone1` / `c3_w0` / `c3_w1`.
3. **The chunk matrix.** It becomes the *object transform* rather than being
   baked into vertices, so the mesh data stays exactly as it is on disk while
   the model still sits in the right place. Blender stores an object transform
   as loc/rot/scale and recomposes it in float32, which is lossy — one real
   corpus matrix has a `1.1367e-07` basis element that comes back as
   `1.8327e-07` — so the importer records what Blender *actually holds* and
   compares against that. Move the object and the exporter converts the new
   transform back into a C3 matrix instead.

The hidden attributes are visible in the Spreadsheet editor if you want to
inspect them. Deleting them is safe — you just lose bit-exactness on the
fields they back.

One thing that turned out *not* to need stashing: the 36-byte "legacy gap" in
the `PHY ` vertex record. It is zero on all 1,426,831 legacy-gap vertices in
this install, so the writer simply emits zeros.

### Custom properties the importer sets

On the collection: `c3_source` (the original path), `c3_container` (the whole
original file, base64) so non-PHY chunks survive an export, `c3_phy_count`.

On each mesh object: `c3_tag`, `c3_node_name`, `c3_name_raw`, `c3_unknown0`,
`c3_vcount_a/b`, `c3_fcount_a/b`, `c3_label_raw`, `c3_label`, `c3_bbox_a/b`,
`c3_matrix`, `c3_matrix_blender`, `c3_frame_count`, `c3_keys`, `c3_has_step`,
`c3_step`, `c3_two_sided`, `c3_billboard`, `c3_phy5_raw`, `c3_tail_raw`,
`c3_chunk_index`, and `c3_texture` / `c3_texture_source` when a texture
resolved.

---

## 8. Troubleshooting

**"nothing to export"** — select one of the imported meshes, or make one active.
With several C3 collections in the scene the addon will not guess.

**Textures come out grey** — check the **Game install root** in the addon
preferences, then hit **Reset texture lookup cache**. Not every mesh has a
texture the appearance tables can find; `misc.ini`, `head.ini` and `mount.ini`
reference assets this build does not ship at all.

**"polygon N has 4 sides"** — triangulate. `Ctrl+T` in edit mode.

**Export is not byte-identical and you did not edit anything** — run
`tests\c3_diagnose.py` on the file. It parses the original and the export and
tells you which field moved, per vertex, instead of just giving you an offset.

**The model looks inside-out** — make sure you have not *also* flipped the
normals or reversed the winding by hand. The importer already handles
handedness with the Z mirror; doing it twice puts you back where you started.
