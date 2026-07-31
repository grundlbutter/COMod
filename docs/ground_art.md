# Ground art — where a map's painted background sits on the cell grid

> **Note.** This document was written in the full development tree that this
> repository was extracted from. References to `docs/CONTEXT.md`,
> `docs/STATUS.md`, `docs/movement.md`, `tools/coplay.py`, `client/...` or the
> static-analysis tooling (`disfn`, `xref`, `riprefs`, `readtrace`, `vtable`,
> `d3dlayout`, `dll_analysis`) point at that tree and are not part of this
> repository. Every tool and command shown from `core/` and `tools/` that is
> not on that list ships here and works as written.


The last missing piece of the map system. `docs/movement.md` §5 ended with

> *"Placing it needs an origin and an isometric projection that neither the
> `.DMap` nor the `.pul` states… **This is the next piece of map work.**"*

It is placed now. The rule is two lines of arithmetic, it is exact on 131 of the
132 maps that have a `.pul`, and the ground in `tools/coplay.py` is the game's
own art rather than a passability shading.

Companion tool: **`tools/puzzle.py`** — module + CLI. Every number below comes
out of it.

```bash
py -3 tools/puzzle.py --verify                     # the rule vs. all 136 maps
py -3 tools/puzzle.py newbie --info --coverage
py -3 tools/puzzle.py arena --full --overlay --scale 4 -o out/viewer/shots/placement-arena.png
py -3 tools/terrain.py newbie --at 61,109 --png /tmp/ground.png
py -3 tools/coplay.py --sim --sim-map newbie --sim-at 61,109      # look at it
```

Claims are marked **VERIFIED** (proved against the whole shipped corpus, or read
out of machine code with the RVA cited) or **INFERRED** (reproduces the data;
ground truth is inside the Themida-packed `ImConquer.exe`).

---

## 1. The rule

```
G    = ini/GameMap.json[mapId].PuzzleGridSize      256 on 134 rows, 128 on 22
PxW  = pul.width  * G                             the painted image, in pixels
PxH  = pul.height * G
K    = PxW / 64                                   the origin, in cells

lattice point (gx, gy)  ->  px = (gx - gy + K) * 32
                            py = (gx + gy - K) * 16

inverse                     gx = px/64 + py/32
                            gy = py/32 - px/64 + K
```

A *lattice point* is the shared corner of four cells. Cell `(x, y)` is the
diamond whose corners are lattice points `(x,y)`, `(x+1,y)`, `(x+1,y+1)`,
`(x,y+1)`; its **centre** is therefore at `((x-y+K)*32, (x+y-K+1)*16)`.

So **one map cell is a 64 × 32 pixel isometric diamond** and the cell axes run
along the painted image's diagonals: `+x` goes down-right, `+y` goes down-left.

And the DMap's own dimensions pin `K`, because the cell grid is exactly the
bounding box of the rotated image:

```
W = H = PxW/64 + PxH/32
```

## 2. Why it looked unrecoverable, and why it is not

The brief recorded two facts as evidence that no rule existed. Both are the rule
in disguise.

**"The puzzle-to-cell ratio ranges 2.4 → 35.6 with no pattern."** That ratio is
`PxW / W`, and it varies because the painted image's *aspect ratio* is free —
`boa` is a canyon corridor (4 × 25 tiles), `Dcloister` is a wide maze
(188 × 75). The invariant is not `PxW/W`, it is `PxW/64 + PxH/32`, and that one
is dead constant:

| map | cells | pul | G | painted px | `PxW/64 + PxH/32` |
|---|---:|---:|---:|---:|---:|
| `arena` | 96×96 | 10×7 | 256 | 2560×1792 | 40 + 56 = **96** |
| `newbie` | 132×132 | 15×9 | 256 | 3840×2304 | 60 + 72 = **132** |
| `boa` | 216×216 | 4×25 | 256 | 1024×6400 | 16 + 200 = **216** |
| `Dcloister` | 676×676 | 188×75 | 128 | 24064×9600 | 376 + 300 = **676** |
| `Gulf` | 1246×1246 | 273×175 | 128 | 34944×22400 | 546 + 700 = **1246** |

**"Every shipped `.DMap` is square."** Of course it is — that is a *consequence*,
not a coincidence. The bounding box of a rectangle rotated into a lattice whose
axes are its own diagonals has equal extents on both axes whatever the
rectangle's shape. All 136 maps are square because all 136 were generated this
way. Noticing that the squareness needed explaining is what produced the rule.

A third statement in the brief is simply wrong and worth correcting: *"most
`.pul` cells are empty"*. Across the corpus **105 of the 132 `.pul` files a map
actually uses have no empty slot at all**; the puzzle is a full covering of its
own rectangle. What is empty is the rest of the *map* — see §5.

## 3. Evidence

### 3.1 The identity, on the whole corpus — VERIFIED

`py -3 tools/puzzle.py --verify`:

```
131 maps match the placement identity, 1 does not, 4 have no usable .pul
```

* **131 exact matches.** Not "within a tile" — `W == H == PxW/64 + PxH/32` with
  `PxW % 64 == 0` and `PxH % 32 == 0`, integer arithmetic, no tolerance.
* **1 mismatch: `sky`.** 588×588 cells against a `20×66` × 256 puzzle, which
  implies 608. `sky.pul` is one of the 31 legacy `PUZZLE` (not `PUZZLE2`) files
  and its art is 20 cells taller than the map. It is named rather than absorbed
  into a tolerance, and `PuzzleMap.consistent` is False for it so the client can
  say so on screen.
* **4 skipped:** `2020love01_new`, `bp-flandlords-y_new`, `magictower01_new`,
  `ninja01_new` point at `map/PuzzleSave/*.pux`, whose header is `TqTerrain\0`.
  That is a different, undecoded format — recorded as unreachable rather than
  guessed at, the same treatment `MNEW`/`CCFL` got.

### 3.2 Walkable cells land on painted art — VERIFIED

> **Read this alongside `docs/map_scenery.md`.** Everything in this section is
> about the **cell grid**, and the cell grid turns out not to be the whole of a
> map's passability: a `TERRAIN` scene layer carries its own, and it replaces
> the grid underneath it. The figures below are unchanged and still exact — but
> "walkable" now means "walkable in the `.DMap`'s own grid", and
> `puzzle.coverage()` splits the two counts (`base*` vs `scenery*`) so they
> cannot be confused. On `newbie` the 111 scene-supplied cells are *deliberately*
> off the art: they are the stepping stones over the void.


The sharp test, because it uses a *different* part of the file: a cell you can
stand on must be on the picture. `puzzle.coverage()` over all 132 placed maps:

```
11,460,704 walkable cells      10,798,140 on painted art (94.22%)
                                  594,910 outside the painted rectangle
                                   67,654 inside it but on an empty tile slot
```

Of the walkable cells that fall **inside** the painted rectangle, **99.38%** are
on a painted tile. And on four maps chosen to span the whole shipped range of
shapes the figure is exactly 100.00%, with zero cells off the image and zero on
an empty slot:

| map | grid | pul | walkable cells | on art |
|---|---:|---:|---:|---:|
| `newbie` | 256 | 15×9 | 1,561 | **100.00 %** |
| `arena` | 256 | 10×7 | 1,587 | **100.00 %** |
| `boa` | 256 | 4×25 | 4,924 | **100.00 %** |
| `Dcloister` | 128 | 188×75 | 43,858 | **100.00 %** |

`icecrypt-lev5` (100,084 walkable cells, 8,490 empty tile slots) is also exactly
100 %. A wrong origin or a transposed 32/16 could not do that on one map, let
alone on maps whose puzzle rectangles differ by a factor of 50 in aspect.

Nine maps fall below 99.9 %, and each has a reason, not a fudge:

| map | on art | why |
|---|---:|---|
| `island` | 21.5 % | 594,890 walkable cells lie **outside** the painted rectangle. `island` marks its unreachable off-art region walkable; every cell that *is* on the image is on paint (0 on an empty slot). |
| `skymaze`, `skymaze1..3` | 21.9–23.2 % | the sky maps' ground is **scenery, not paint** — 2,198 decoded layers on `skymaze` against 270 painted tiles. The `.pul` is a backdrop. |
| `sky` | 76.2 % | the one map that fails the identity (§3.1). |
| `spirit01_new` | 0 % | its `.pul` (`spirit01_new-bg01.pul`) has **no painted tiles at all**. |
| `2009-7x`, `skycut` | 99.45 %, 99.83 % | 168 and 10 stray cells. Unexplained; stated. |

### 3.3 By eye — VERIFIED

`out/viewer/shots/placement-*.png` (`--full --overlay`) puts a red dot on every
walkable cell over the painted image. On `arena` the dots fill the arena floor
and the entrance corridor and stop at the wall; on `Dcloister` they follow every
zigzag of a 676-cell maze; on `island` they cover the land masses and not the
sea. A projection that was wrong by a constant would show as dots in the water.

`out/viewer/shots/groundart-*.png` are the same four maps drawn by the client.

### 3.4 The half-cell phase — INFERRED

Whether a cell index names a diamond's *corner* or its *centre* is a half-cell
(32 px across, 16 px down) and nothing states it. It was settled against the
art's own alpha channel: a walkable cell must not sit on a transparent pixel.
Scanning offsets on `newbie` (1,561 walkable cells, an island on a transparent
background):

```
            dx -32   dx -16   dx  0   dx +16   dx +32
  dy -16       21       15      11      12       20        <- transparent hits
  dy   0       11        5       3       4        9
  dy +16        4        1       0       0        4
```

Monotone in `dy` and symmetric in `dx` about zero, with the minimum at
`(0, +16)` — which is exactly "cell centres are at continuous lattice
coordinates `(x+0.5, y+0.5)`", i.e. the image's top-left pixel is a cell
*corner*. `PuzzleMap.cell_px()` implements that; `corner_px()` is the raw
lattice map and is what the mesh UVs use.

### 3.5 What the client's own code says — VERIFIED, and its limit

Following the method that cracked PHY, MOTI and the attachment chain, the DLLs
were searched first. The result is a real answer but a partial one, so it is
stated plainly rather than dressed up.

`graphic.dll` exports the engine's puzzle primitives:

| RVA | export |
|---|---|
| `0x46FE0` | `PuzzelBlockCreate(w, h, gridX, gridY)` |
| `0x47060` | `PuzzelCellCreate()` — a 0xF8-byte object, ctor `0x44560`, 28-slot vtable at `.rdata 0x1EAB90` |
| `0x47090` | `PuzzelTriangleCreate()` — ctor `0x44690` |

`CPuzzleBlockX::Create` is **`0x45600`** (named by its own log string at
`.rdata 0x1EAD10`, *"CPuzzleBlockX::Create with Big Grid Count: %d, %d"*, loaded
at `0x4567B`). It stores `w` at `+0x28`, `h` at `+0x2C`, and clamped grid counts
at `+0x30`/`+0x34`, then fills a `(nx+1) × (ny+1)` lattice of 20-byte vertices
(`0x45750`–`0x457FF`):

```
pos.x = w * i / nx      (0x45776 mulss / 0x45785 divss)
pos.y = h * j / ny      (0x45792 / 0x457AC)
colour = 0xFFFFFFFF     (0x457BE)
u = i / nx              (0x457D1)
v = j / ny              (0x457EA)
```

followed by a `nx*ny*6`-entry `u16` index buffer at `0x4581F`. Two things follow:
the puzzle is drawn as **subdivided 3D quads**, not blitted; and its geometry is
authored in the **pixel units of the painted image**, which is what makes a
pixel-space placement rule the natural one.

**The limit.** `w`, `h`, `nx`, `ny` and the block's position are all supplied by
the caller, and the caller is `ImConquer.exe`. No clean binary in the install
contains the string `.pul`, `.DMap`, `puzzle`, `GameMap` or any `map\…` path —
checked across every string in `graphic.dll`, `GraphicData.dll`, `Role3D.dll`,
`TqPackage.dll`, `TqPackageWdf.dll` and `ImLauncher.exe`. `GraphicData.dll`'s
only scene-ish content is `ini/3dscene.ini`.

> **So the `.pul` reader and the placement arithmetic live only inside the
> Themida-packed exe, and were not read.** That is why §1 is marked VERIFIED
> *against the corpus* rather than VERIFIED *from code*: the rule reproduces
> every shipped file exactly, which is the strongest evidence available without
> unpacking, and unpacking is out of scope (`docs/CONTEXT.md`).

---

## 4. Drawing it

The placement is **linear in the same corner coordinates `tools/terrain.py`
already emits**, so the ground art needed no new geometry — only different UVs
and a different texture:

```python
patch = terrain.build_patch(grid, cx, cy, radius, puzzle=pm)   # UVs -> the art
png   = terrain.art_texture(pm, patch)                         # the matching crop
```

`build_patch` computes the window's pixel rectangle once, uses it for the UVs,
and publishes it as `patch["ground"]["rect"]`; `art_texture` cuts exactly that
rectangle out of the painted image. The texture and the UVs are therefore the
same numbers rather than two computations that could drift.

Vertex positions, indices, winding, elevation and the passability data are
untouched — pinned by
`test_viewer.py::PuzzlePlacement::test_terrain_uvs_switch_to_the_art_without_moving_a_vertex`.

In `tools/coplay.py`:

* `/api/game/terrain` reports `ground.source` = `puzzle` or `passability`, the
  rect, the `.pul` path, and `consistent`.
* `/api/game/ground` serves the painted crop, or the old passability shading for
  `art=0` and for any map whose art cannot be placed — with `ground.why` saying
  which. The passability view is still the right one when the question is
  movement rather than looks.
* `--sim-map NAME` hands `client/simserver.py` a **real** `.DMap` instead of its
  synthetic rectangle, so any of the 132 placed maps can be looked at without a
  live server. Without it, `--sim` keeps its synthetic map and therefore its
  passability shading, exactly as before.

### Costs

A 49×49-cell window is a 3136×1568-pixel rectangle (`(w+h)·32` by `(w+h)·16`) —
**half of it is off the mesh**, because a square in cell space is a diamond in
the painted image. That is inherent to an axis-aligned texture over a rotated
grid. Cutting and encoding it takes ~0.65 s cold, ~0.15 s warm, and it is cached
per window; `?scale=2` quarters it.

---

## 5. Things worth knowing before you look at a map

* **A large minority of every map is off the art.** The painted rectangle is a
  diamond inside the square cell grid; on `newbie` the art covers 8,640 of
  17,424 cells. Those cells are normally blocked, and the client used to draw
  them as void (`puzzle.VOID`, near-black) rather than pretending. **The void is
  where the map's *background* planes go** — `newbie.DMap`'s trailing section
  names `newbiebg.pul`, which nothing in its header references, and drawing it
  turns the black gap into sea and cloud. `PuzzleLibrary.backdrops()`;
  `docs/map_scenery.md` §5.
* ~~**Scenery is still missing.**~~ **Drawn, as of task #29.** `scene` and
  `cover` layers are placed and composited by `tools/scene.py`, and the `scene`
  layers turn out to carry **passability** as well as art — which is why the
  sky maps' walkable cells looked like they were floating in nothing. See
  `docs/map_scenery.md`. `effect` and `sound` layers are still parsed and not
  drawn.
* **Animated tiles are frozen.** A handful of `Puzzle<n>` keys name several
  frames; frame 0 is used. Four `.pul` files carry a non-zero `rollSpeedX/Y`
  (`market-sky`, `skybg-move` and friends) — a scrolling backdrop, not modelled.
* **The art is not integrity-checked.** `integrity.json` covers the 155 `.DMap`
  files, not the `.pul` or the `.dds`. Repainting a map's ground is a texture
  edit outside the manifest (`docs/modding.md` §6).

---

## 6. Verified vs inferred, at a glance

**VERIFIED — proved against the shipped corpus**

* `px = (gx-gy+K)*32`, `py = (gx+gy-K)*16`, `K = pul.width * G / 64`.
* `W = H = PxW/64 + PxH/32` on 131 of 132 maps, exactly, with `sky` named.
* One cell is a 64 × 32 px diamond; the cell axes are the image's diagonals.
* 100.00 % of walkable cells on `newbie`, `arena`, `boa`, `Dcloister` and
  `icecrypt-lev5` land on painted tiles; 99.38 % corpus-wide of those inside the
  painted rectangle.
* `PuzzleGridSize` is the tile edge in pixels, 256 on 134 rows and 128 on 22, and
  the 22 are exactly the maps whose numbers only work at 128.

**VERIFIED — read out of machine code, RVAs cited in §3.5**

* `graphic.dll` `0x45600` builds a puzzle block as a subdivided quad whose vertex
  positions are in the painted image's pixel units.
* `PuzzelBlockCreate` / `PuzzelCellCreate` / `PuzzelTriangleCreate` at `0x46FE0`
  / `0x47060` / `0x47090` are the exe's entry points into it.
* No clean binary in the install references a map file, a `.pul`, or
  `GameMap.json`.

**INFERRED**

* The half-cell phase (§3.4) — the alpha scan chooses it, nothing states it.
* That `sky`'s art really is 20 cells taller than its map, rather than there
  being a second rule for legacy `PUZZLE` files. Of the 132 placed maps, 101 use
  `PUZZLE2` and all 101 satisfy the identity; 31 use the legacy `PUZZLE` header
  and **30 of those 31** satisfy it. One bad file, not one bad format.

**OPEN**

* `map/PuzzleSave/*.pux` (`TqTerrain\0`), used by 4 maps.
* Whether the engine draws the puzzle at one block per tile or one per map, and
  what subdivision it asks for — `CPuzzleBlockX::Create`'s arguments come from
  the packed exe.
* The `rollSpeedX/Y` scrolling backdrops.
* ~~Scene / cover / effect layer placement~~ — **done for scene and cover**:
  `docs/map_scenery.md`. `effect` and `sound` remain.
