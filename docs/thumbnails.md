# Asset thumbnails — 71,784 previews, and how they are made

A square PNG preview for **every `.c3` mesh that resolves to a texture**
(4,950 of 4,964 — the work list is `tools/meshtex.py`'s answer, not a new one),
plus one for **every `.dds` in the client's texture namespace** (66,834).

Tool: **`tools/thumbs.py`**. Output: `out/thumbs/` (gitignored) with
`manifest.json`. Consumers should read the manifest, never guess paths.

```bash
py -3 tools/thumbs.py --all --textures      # the whole thing; resumable
py -3 tools/thumbs.py --all                 # meshes only
py -3 tools/thumbs.py --all --dry-run       # plan + disk estimate, writes nothing
py -3 tools/thumbs.py --selftest            # the winding / orientation proof
py -3 tools/thumbs.py --one c3/mesh/002135000.c3 --out /tmp/x.png
```

Everything below is **VERIFIED** (measured on this build, or read out of the
modules whose own claims are code-derived) or **CHOSEN** (a preview-policy
decision with the reasoning stated, not a fact about the engine).

---

## 0. Generation is opt-in

**Nothing here ever runs by itself.** A fresh clone has no `out/thumbs/`, and
filling it costs ~637 MB and — depending entirely on the machine — anywhere
from about four minutes on a fast many-core desktop to 15–30 minutes or more on
a laptop. Starting that on someone's behalf, on their first launch, is not a
reasonable default.

So the Asset Viewer **asks** (`tools/webui/firstrun.js`, backed by
`tools/health.py`), states the real cost, offers **meshes only** as the
recommended middle option, remembers the answer, and works perfectly well if
you decline — assets with no thumbnail get a placeholder tile and nothing else
changes. The viewer drives *this script*, as a child process, with the flags
below; it does not re-implement any of it.

`tools/health.py` computes the time estimate for the machine it is running on,
by scaling the reference measurement in §1 by the worker count, and shows the
reference alongside it so the extrapolation is inspectable rather than
authoritative.

---

## 1. Headline

| | |
|---|---:|
| mesh thumbnails rendered | **4,950 / 4,950** |
| …failed | **0** |
| …needing a documented fallback | 42 |
| …usable but near-empty (sub-pixel geometry) | 19 |
| meshes with no texture at all, so not in scope | 14 |
| texture thumbnails rendered | **66,834 / 66,834** |
| …failed | **0** |
| **total on disk** | **505 MB** (118 MB meshes + 360 MB textures + 27 MB manifests) |
| wall time, cold, 20 workers | **226 s** (82 s meshes + 144 s textures) |
| wall time, warm (nothing changed) | **14 s** |

Mesh thumbnails are 256×256 RGBA with straight alpha and a transparent
background; texture thumbnails are ≤128×128, palettised.

---

## 2. Why a numpy rasterizer and not the viewer

`tools/coviewer.py` + `tools/webui/gl.js` can already screenshot a model —
`gl.js`'s `snapshot()` is how `out/viewer/shots/` was captured. For five
thousand unattended renders it is the wrong instrument: it needs a browser, a
GPU driver and a live HTTP server, it cannot be resumed mid-run, a single
WebGL context loss loses the batch, and the viewer UI is under concurrent
change by another workstream — a pipeline that needed viewer edits would
collide with it.

So `thumbs.py` rasterizes in numpy, and **imports rather than reimplements**
every module where a private copy could silently drift from what the viewer
draws:

| imported, never edited | what it supplies |
|---|---|
| `core/c3phy.py` | `PHY` chunk parsing, the chunk's own 4×4 (`apply_matrix_to`) |
| `tools/effects.py` | `MOTI` decoding and `Motion_GetMatrix` |
| `tools/attach.py` | `PHY`↔`MOTI` **ordinal** pairing, the `[Dumy]` socket test |
| `core/dds.py` | the verified DDS / BC decoder |
| `core/coassets.py` | the loose-file-shadows-WDF asset VFS |
| `tools/meshtex.py` | mesh → texture, with method, confidence and kind |

Cost of the choice: ~60 meshes/s/20 workers, no external dependency beyond the
numpy and Pillow that were already installed.

---

## 3. What is drawn — VERIFIED

For a standalone asset preview the chain in `docs/attachment.md` §4.5
degenerates to its first two stages, because there is no body to hang off:

```
p_render = ( SUM_k  w_k * ( p_bind x Mbone_k ) ) x I        then  z -> -z
```

* **`p_bind` is post-chunk-matrix.** `Phy_Load` applies the chunk's 4×4 to every
  position at load time (`graphic.dll 0x5A735`); `c3phy.apply_matrix_to`
  reproduces it. Skipping it misplaces limbs and weapons.
* **`Mbone_k` is the chunk's own `MOTI`,** indexed by the vertex's
  `BLENDINDICES0/1` at frame 0, two influences maximum, `w1 = 1 - w0`
  (`0x5A869`). `Mesh::Draw` always passes `useMotion = true` (`0x2607E`), so
  **the engine never draws a bind pose and neither does this.** That is why a
  body renders with its arms at its sides rather than in the stored T-pose, and
  why weapons come out at their authored size rather than 2.11× a character.
* **`MOTI` *i* pairs with `PHY` *i* by ordinal**, not by adjacency
  (`C3Mesh::SetMotion 0x27818`). `attach.PartMesh.parse` does this; 447 of 2,002
  loose `.c3` files group all PHYs before all MOTIs and an adjacency reader gets
  zero pairs on them.
* **Socket chunks are skipped by `[Dumy]` membership**, via
  `attach.is_socket_name` — **not** by a `v_` prefix. 66 armet meshes lead with
  a chunk called `v_armet01` that is real geometry, and a prefix test renders
  them empty.
* **Winding.** C3 is D3D clockwise-front in a left-handed, Z-down frame
  (`docs/modding.md` §9.7). Negating Z flips handedness, so in render space the
  front faces are counter-clockwise and the rasterizer drops the clockwise ones.
  Chunks carrying the `2SID` tag are not culled at all.
* **UVs are not flipped.** D3D's V runs top-down and so does PNG row 0, matching
  `gl.js`'s comment. Sampling is bilinear with `REPEAT` wrap — `gl.js` uses
  `LINEAR` + `REPEAT` and every texture in this install is power-of-two, so
  `REPEAT` is always the live path.

Compositing is **sorted per-pixel alpha**, not a z-buffer plus an alpha test:
every fragment is bucketed by pixel, ordered front-to-back by view depth and
composited with accumulated transmittance. That is exact for opaque geometry
(the nearest fragment wins) and correct for the 1,326 effect meshes whose
textures are soft alpha gradients — an alpha test renders those as holes.

### Camera — CHOSEN

45° vertical FOV, `up = +Z`, orbit direction `(cos p cos y, cos p sin y, sin p)`,
eye at `centre + dir · 2.6 · radius`: all copied from `gl.js`. Two deliberate
differences:

1. **Yaw is `-0.9`, not `gl.js`'s `+0.9`.** `+0.9` puts the eye at (+X, +Y) and
   **this corpus is authored facing -Y**, so the viewer's *default* camera looks
   at the back of a character. `--selftest` writes
   `out/thumbs/_selftest/yaw_grid.png` — the same body from the four axis
   directions — and only the -Y eye shows a face. (The shots in
   `out/viewer/shots/` show fronts because the viewer persists an orbited
   camera; one of them is literally named `..._front.png`.) Mirroring the yaw
   keeps the viewer's 3/4 elevation and puts it on the front.
2. **The frame is fitted exactly to the projected geometry**, by a uniform 2-D
   scale about the projected centre with a 6 % margin, rather than `gl.js`'s
   bounding-sphere fit. A uniform scale in NDC is still a valid perspective
   image — just a tighter FOV — and the sphere fit wastes most of the frame on
   anything that is not spherical. Per-asset fitting is right here precisely
   because these are standalone previews; the viewer's *character* mode
   deliberately does not re-frame, because there the body anchors the framing.

### Deviations from the live viewer — CHOSEN

* **The `C3Key` alpha / draw tracks are ignored.** Many effect chunks are keyed
  to alpha 0 at frame 0 and would render as an empty thumbnail.
* **Shading is `unlit`** — `gl.js`'s own default. `--shade lit` reproduces its
  lit mode (same light vector and 0.42 + 0.58·N·L ramp).

---

## 4. The winding proof

An inside-out model still looks entirely plausible at 128 px, so this is checked
numerically, not by eye. `--selftest`:

```
reference: c3/mesh/002135000.c3
  chunks 4, sockets skipped 3, drawn ['v_body']
  transform chain vs attach.world_vertices: max |delta| = 7.12522e-06
  render-space bounds  x[-21.0,24.9] y[-38.4,28.2] z[-0.1,170.4]  height 170.4
  texture: c3/texture/002135300.dds  (appearance_table, authored, conf 0.95)
  winding: 'screen area < 0' == 'engine outward normal faces the eye' on 100.00% of triangles
  failed checks: 0
```

Four independent checks, each of which catches a different silent failure:

1. **`[Dumy]` filtering** — exactly `v_body` is drawn; the three socket chunks
   are not.
2. **The transform chain agrees with `tools/attach.py`'s own scalar
   implementation to 7 × 10⁻⁶.** The vectorised path here and
   `attach.world_vertices` are independent code, so this pins the chunk matrix,
   the ordinal pairing and the skinning at once.
3. **Height is 170.4 and the model stands on z = 0** — `docs/attachment.md` §8.2
   measures this body at 170.4 units. Catches an axis or scale mistake.
4. **Winding**: for every triangle, the screen-space signed area's sign is
   compared against the **engine's own generated outward normal**
   (`cross(p2-p1, p1-p0)` in C3 space, `docs/modding.md` §9.7 — which under the
   render-space mirror becomes `cross(q1-q0, q2-q1)`, the other edge order,
   because `det S = -1`). They must agree on 100 % of faces. A backwards cull
   scores 0 %, not 50 %, so this cannot pass by accident.

`--selftest` also writes `reference_correct.png` and `reference_inverted.png`
side by side. The inverted one is visibly hollow — but only once you know to
look, which is the point of check 4.

> **Note for anyone reading `c3phy.generate_normals`:** it computes
> `cross(p1-p0, p2-p1)`, the negation of the engine's normal as documented in
> `docs/modding.md` §9.7. Handed C3-space positions it therefore produces
> *inward* normals (measured: outward on only 20–31 % of faces on the four
> reference bodies). Handed **render-space** positions — which is all this
> module ever does — the mirror flips it back and it is outward. Not changed,
> since it is not this workstream's file; recorded here so the next caller does
> not get it backwards.

---

## 5. Fallbacks — 42 meshes, all recorded in the manifest

"Nothing at all" is a worse thumbnail than "the right asset drawn slightly
unlike the engine would draw it at this exact instant", so four fallbacks fire
in order. Every one of them is named in the entry's `fallbacks` array, so a
consumer can tell an ordinary render from a coaxed one.

| fallback | n | why |
|---|---:|---|
| `no_cull` | 25 | a single-sided flat plane (1–2 triangles) whose front happens to face away from the default camera. Drawn two-sided instead. |
| `uv_cell` | 10 | an effect flipbook whose frame-0 cell is blank. The UVs are offset onto whichever cell of the sheet carries the most alpha — the same UV scroll the engine does (`docs/effects.md` §6.4, `gl.js`'s `uUVOffset`). The chosen cell is recorded as `uv_cell: [i, j]`. |
| `no_motion` | 4 | the chunk's own bone-0 matrix at frame 0 is a zero scale, collapsing the mesh to a point — an effect that grows from nothing, or `c3/weapon/350170.c3`. Falls back to the stored pose (chunk matrix only). |
| `alpha_from_luma` | 3 | the texture's alpha channel is entirely zero. That is an additive effect sheet whose RGB *is* the intensity, so luminance is used as the key. |

Two further honest labels:

* **`low_coverage: true` — 19 meshes.** Real geometry, correct texture, but the
  triangles are ~1 px² of area even framed to their own bounds: particle ribbons
  and slivers such as `c3/effect/weapon/qingrenjie2017_*` (398 sub-pixel
  triangles) and the single 180-unit sliver in `c3/monster/224/*`. The engine's
  own pixel-centre coverage rule gives the same result. Not a failure, but not
  worth showing either — a browser should prefer a placeholder.
* **`container: "not-dds"` — 2 textures.** `c3/effect/arrow-poison/pic9839.dds`
  is a JPEG and `c3/map/puzzle/newplain/scene/altar/pic0055.dds` is a BMP,
  both wearing a `.dds` extension. Decoded with Pillow instead.

### What is *not* covered

The **14 meshes with no texture in this build** (`docs/meshtex.md` §5 — orphaned
hair styles 52–56, two backswords, a bow, two NPCs and the `004137*` garments
whose textures the database names but the build does not ship). They are listed
verbatim in the manifest's `unmatched_meshes`. Rendering them untextured was
rejected: a white silhouette in a browsing grid reads as a broken thumbnail, and
the manifest already says exactly why they are absent.

The **1,426 `.c3` files with no `PHY`-family chunk at all** (pure
`MOTI`/`PTCL`/`PTC3`/`CAME`/`SHAP`) are not meshes and were never in scope —
`meshtex.is_mesh()` excludes them. The renderer is robust to being handed one
anyway: it reports `no drawable geometry` with the chunk tags it did find.

---

## 6. Incremental and resumable — VERIFIED

Every entry carries a `key`: `sha1(renderer version ‖ the options that affect
this kind of output ‖ sha1(source bytes) ‖ sha1(texture bytes))`. A run
re-renders an entry only when that key differs from the manifest's or the PNG is
missing, so:

* an interrupted run continues where it stopped;
* changing a source asset re-renders exactly the thumbnails that use it —
  including every mesh that shares a re-skinned texture;
* changing a *texture-pass* option does not invalidate 4,950 mesh renders, and
  vice versa (`KEY_OPTS` keeps the two option sets disjoint);
* bumping `RENDERER_VERSION` re-renders everything, which is the point — it
  prevents a manifest silently mixing output from two renderer generations.

Measured: a full warm re-run over all 71,784 entries is **14 s** (8 s meshes,
6 s textures) and writes nothing. `--no-resume` forces a cold rebuild.

Content hashes rather than mtimes, deliberately: WDF-backed assets have no
meaningful mtime of their own, and the cost of hashing is the file read the
worker has to do anyway.

---

## 7. Manifest schema

`out/thumbs/manifest.json` — everything, compact JSON, ~23 MB.
`out/thumbs/manifest_meshes.json` — identical schema with `entries` filtered to
`kind == "mesh"`, indented, ~3.5 MB. **Read this one if you only want model
previews.** Both are written atomically.

```jsonc
{
  "schema": 1,
  "generated": "2026-07-26T14:19:07",

  "renderer": {                    // everything that affects the pixels
    "tool": "tools/thumbs.py", "version": 1,
    "size": 256, "supersample": 2, "shade": "unlit", "frame": 0,
    "yaw": -0.9, "pitch": 0.28, "fov_deg": 45.0,
    "background": "transparent",   // or [r,g,b] floats when --bg is used
    "texture_size": 128, "texture_palettised": true
  },

  "counts": {
    "entries": 71784, "meshes": 4950, "textures": 66834,
    "fallback_rendered": 42, "low_coverage": 19, "unmatched_meshes": 14,
    "bytes": 477733766             // sum of the PNG sizes
  },

  "unmatched_meshes": ["c3/hair/002119052.c3", ...],   // the 14, not rendered

  "entries": {
    "<logical asset path>": { ... }
  }
}
```

### A mesh entry

```jsonc
"c3/mesh/002135000.c3": {
  "kind": "mesh",
  "logical": "c3/mesh/002135000.c3",
  "thumb": "mesh/c3/mesh/002135000.png",   // RELATIVE TO out/thumbs/
  "width": 256, "height": 256,
  "bytes": 17339,
  "key": "160b369d8ad2c14570dd27e7520dd4872ffaf4ab",

  // which texture was used, and on whose authority -- straight from
  // tools/meshtex.py's best match, see docs/meshtex.md section 3
  "texture":    "c3/texture/002135300.dds",
  "method":     "appearance_table",
  "confidence": 0.95,
  "match_kind": "authored",                // "authored" | "inferred"
  "detail":     "armor.ini [002135300] Mesh0=002135000 Texture0=002135300",

  // what was actually drawn
  "chunks": 4,               // PHY chunks in the container
  "drawn_chunks": 1,         // after the [Dumy] socket filter
  "sockets": 3,              // how many were filtered out
  "triangles": 672,
  "drawn_triangles": 335,    // after back-face culling
  "coverage": 0.0957,        // fraction of the thumbnail with alpha > 0
  "bounds": [[-20.991, -38.351, -0.058], [24.917, 28.241, 170.377]],
                             // render-space AABB, +Z up, units are game units

  // present only when they apply
  "fallbacks": ["uv_cell"],  // see section 5
  "uv_cell": [2, 2],
  "low_coverage": true,      // real but essentially invisible; show a placeholder
  "sockets_only": true,      // container held nothing but [Dumy] chunks
  "capped": true             // fragment budget hit; render may be incomplete
}
```

`match_kind` is the distinction that matters for a UI: **`authored` means a
shipped table states the pairing** (4,245 meshes), **`inferred` means a naming
convention guessed it** (705). `confidence` is that rule's *measured* top-1
precision, not a feeling — the numbers come from
`out/meshtex/precision.json`. The weakest rule in the set is `dir_sibling`
at 0.30, and it is the best match for exactly 5 meshes.

### A texture entry

```jsonc
"c3/texture/002135300.dds": {
  "kind": "texture",
  "logical": "c3/texture/002135300.dds",
  "thumb": "texture/c3/texture/002135300.png",
  "width": 128, "height": 128,      // after downscale; aspect preserved
  "source_size": [128, 128],        // the real .dds dimensions
  "bytes": 6789,
  "key": "143db68b...",
  "used_by_mesh": true,             // is this the chosen skin of some mesh?
  "container": "not-dds"            // only on the 2 mislabelled files
}
```

`used_by_mesh` splits the library into the **2,782** textures that are some
mesh's chosen skin and the **64,052** that are map art, UI, icons, faces and
particle sheets no mesh claims. A browser almost certainly wants to present
those two groups differently.

### Path rules

* Keys are **logical asset paths** — exactly what `coassets.AssetRoot.read()`
  takes, `/`-separated, no leading slash, and identical to the keys in
  `out/meshtex/coverage.json`. Join on them.
* `thumb` is **relative to `out/thumbs/`** and mirrors the logical path with the
  extension swapped for `.png`, so it is stable and collision-free. Resolve it,
  don't reconstruct it.
* Mesh PNGs are RGBA with **straight (un-premultiplied) alpha** and a fully
  transparent background. `--bg viewer` flattens them onto `gl.js`'s clear
  colour `#0e0f11` instead; `--bg '#rrggbb'` takes any colour.

---

## 8. Regenerating

```bash
py -3 tools/thumbs.py --all --textures --jobs 20     # ~4 min cold, ~14 s warm
```

Options that matter:

| flag | default | effect |
|---|---|---|
| `--size N` | 256 | mesh thumbnail edge in px |
| `--ss N` | 2 | supersampling; the buffer is `size × ss` square |
| `--tex-size N` | 128 | texture thumbnail edge |
| `--tex-truecolor` | off | keep texture thumbnails 24-bit — about **4×** the disk (1.35 GB instead of 360 MB). Palettising to 255 colours via octree is invisible at 128 px; it was compared side by side over a 300-file sample. |
| `--jobs N` | cpu−1 | worker processes |
| `--shade lit` | `unlit` | `gl.js`'s lit mode instead of flat texture |
| `--bg` | transparent | `viewer` or `#rrggbb` to flatten |
| `--only-unmatched-textures` | off | with `--textures`, skip the 2,782 already used as mesh skins |
| `--limit N` | — | stop after N items (for a quick sample) |
| `--no-resume` | off | ignore the manifest and re-render everything |
| `--dry-run` | off | print the plan and the disk estimate, write nothing |

The work list comes from `out/meshtex/coverage.json` when it exists (instant);
otherwise `thumbs.py` builds a live `meshtex.MeshTextureIndex`, which takes
about two minutes because it has to parse every `.c3`. Regenerate that file
with `py -3 tools/meshtex.py --coverage --verify` if the asset tree changes.

Disk, measured: **505 MB** total — 118 MB for 4,950 mesh PNGs (24 KB mean),
360 MB for 66,834 texture PNGs (5.4 KB mean), 27 MB of manifests. `--dry-run`
estimates from the same per-pixel constants.

---

## 9. Known limits

1. **One texture per mesh.** Only `meshtex`'s *best* match is rendered, even
   though a body mesh legitimately has a whole armour family of skins
   (`docs/meshtex.md` §8.2) and the appearance tables also carry
   `MixTex`/`ThirdTex`/`FourthTex` layers that are not composited here. The
   alternates are still available in `out/meshtex/coverage.json`, keyed
   identically.
2. **Frame 0 of the mesh's own `MOTI`.** For the `c3/000N/<weapon>/<action>.c3`
   files that means the first frame of that action, which is why a browse of
   them shows characters mid-stride and lying down. That is the honest preview
   of what the file contains; `--frame N` renders any other frame.
   `docs/attachment.md` §8.2's recommendation to prefer the idle action motion
   applies to a *character* view, where a body anchors the pose — it has no
   meaning for a standalone action file.
3. **No `PTCL` / `PTC3` particles, no `SHAP` trails, no billboard orientation.**
   A `BILB`-tagged chunk is drawn in its authored orientation rather than turned
   to face the camera. `tools/effectplay.py` is the tool for animated effects.
4. **Sub-pixel geometry is not conservatively rasterized** — see the 19
   `low_coverage` entries in §5.
5. **Up to ~40 archived meshes are invisible to the work list**, inherited from
   `docs/meshtex.md` §8.1: 78 `c3.wdf` entries still have unrecovered names, 40
   of them `MAXF` payloads. They cannot be enumerated, so they are in neither
   the 4,950 nor the 14.
