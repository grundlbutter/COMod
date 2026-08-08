# Map scenery — the layers on top of the ground, and the passability they carry

`docs/ground_art.md` placed a map's painted background and ended with

> *"Scene / cover / effect layer placement, which is the **next piece of map
> work** now that this one is done."*

and with a warning that a large minority of every map is off the art. On
`newbie` that showed as two islands with a black gap between them. The project
owner looked at `out/viewer/shots/placement-newbie.png` and said the starting
platform and the NPC area are *not connected*, and that the real client uses
"smaller map resources overtop of the regular map to place a visible structure
you can cross".

He is right, it is stated in the data, and the consequence is bigger than the
picture:

> **Passability is not the cell grid. It is the cell grid with the map's
> `TERRAIN` scene layers composited onto it.** `newbie`'s starting island and
> its village are two separate components of the DMap grid. The twenty stepping
> stones strung across the gap are what join them, and they join them only
> because each stone carries its own per-cell passability.

Tools: **`tools/scene.py`** (module + CLI). Every number below comes out of it.

```bash
py -3 tools/scene.py --verify                      # the whole corpus
py -3 tools/scene.py newbie --list                 # what is placed, and where
py -3 tools/scene.py newbie --png out/viewer/shots/scene-newbie-full.png --scale 3
py -3 tools/scene.py newbie --png x.png --at 76,100 --radius 8 --overlay
py -3 tools/terrain.py newbie --at 80,95 --png g.png --cover-png c.png
py -3 tools/coplay.py --sim --sim-map newbie --sim-at 61,109
```

Claims are **VERIFIED** (proved against the shipped corpus, or corroborated by
an independent implementation, or observed against a running server) or
**INFERRED** (reproduces the data; ground truth is inside the Themida-packed
`ImConquer.exe`, which `docs/CONTEXT.md` puts out of scope — and task #27
established that no clean binary in the install even mentions a map file, so
there was no disassembly to fall back on here either).

---

## 1. The three layer types, and what each is for

`core/dmap.py` already decoded the layer table; what was missing was what the
records *mean*. All three place art in the painted image's pixel space.

| tag | name | what it is | passability |
|---|---|---|---|
| 1 | `TERRAIN` | a `map/Scene/*.scene`, i.e. a multi-part scenery object — bridges, platforms, buildings | **yes — it replaces the grid** |
| 4 | `COVER` | one `.ani` sprite drawn *in front of* the player | no |
| 10 / 15 | `EFFECT` / `SOUND` | a `3DEffect.ini` key / a sound file | no; still not drawn |

Corpus-wide (`py -3 tools/scene.py --verify`):

```
136 maps: 27 place TERRAIN scene objects, 111 place COVER sprites
9,415 scene parts, 17,364 covers
scene passability: 11,578 cells opened, 6,264 cells blocked, 0 off the map
11 maps gain a larger walkable component once the scene layers are applied
```

So the owner's "this is done a lot" is right about covers — **82 % of maps use
them** — and scene objects are rarer but decisive where they appear.

## 2. The footprint rule — VERIFIED

A `TERRAIN` layer gives a `.scene` path and a cell origin. Inside the `.scene`,
each part gives a **cell offset**, a `w × h` cell array, and a pixel offset.

```
A          = (layer.x + part.cellOffsetX,  layer.y + part.cellOffsetY)
part(i, j) -> map cell  (A.x - i,  A.y - j)          i fast, row-major
```

The cells run **backwards** from the anchor. That is the single hardest fact
here and it is what a plausible-looking forward reading gets wrong.

### How it was pinned

Not by aggregate statistics — those were ambiguous. By **connectivity**. A
bridge exists to join two things; if the placement is right, applying it must
merge walkable components, and if it is wrong it must not.

| map | components, base grid | forward reading | backward reading |
|---|---:|---:|---:|
| `newbie` | 5, largest 1,388 | 12, largest **1,388** | 8, largest **1,604** |
| `p-arena` | 3, largest 11,364 | 3, largest 11,341 | **1**, largest 14,171 |
| `task07` | 3, largest 281,184 | 3, largest 281,161 | **1**, largest 441,288 |
| `task08` | 3, largest 345,155 | 3, largest 345,134 | **2**, largest 470,174 |
| `newplain` | 35, largest 199,247 | 35, largest 199,226 | 33, largest **340,796** |

`newbie`'s 1,604 is 1,388 (the village) + 130 (the starting island) + the
bridge. `p-arena` and `task07` become **one** walkable component — every
reachable cell on the map, connected. The forward reading not only fails to
join anything, it scatters three orphan fragments into the water on `newbie`.
A wrong rule cannot land every cell of a bridge on the gap it spans, on five
maps at once.

`tools/test_viewer.py::ScenePassability` pins all of it, including an explicit
assertion that the forward reading does **not** join `newbie`.

### Corroboration from an independent implementation

CoEmu (`server/coemu/server/game/src/systems/floor.rs`, a Rust codebase with no
relationship to this one) computes

```rust
let px = location.x + start_location.x - x;
let py = location.y + start_location.y - y;
...
coordinates[i].access = access;
```

— the same anchor, the same backwards walk, and an **assignment** rather than a
union. Two independent derivations agreeing is the strongest evidence available
without unpacking the client.

## 3. Overwrite, not union — VERIFIED against CoEmu, INFERRED for retail

The scene cell's first `u32` is the same "invalid movement" flag the DMap cell
carries, and it only ever takes the values 0 and 1 across all 8,393 cells in the
corpus. The scene value **replaces** the grid's.

The direction matters in both directions: corpus-wide the scene layers open
**11,578** cells the grid blocks *and* block **6,264** cells the grid opens. A
union would keep the bridges and silently delete the walls of every building
placed as scenery, letting a player walk through them.

## 4. Where the art lands — INFERRED, but tight

In the painted image's pixel space (`tools/puzzle.py`, `cell_px`):

```
scene sprite top-left = cell_px(A) + part.pixelOffset
cover sprite top-left = cell_px(A) - cover.offset
```

Note the sign flip: a `.scene` stores the displacement already negative, a
`COVER` record stores the same displacement positive.

Evidence — the sprite's opaque bounding box must sit on the cell footprint it
declares:

* **1,504 covers on four maps.** Median horizontal error **+2.5 px**; the
  sprite's bottom edge lands within **4 px** of the footprint's bottom corner.
  Flipping the sign moves the median to +121 px and +246 px.
* **The six `newbie` scene sprites.** Anchoring on the cell *centre* gives a
  mean vertical error of +0.2 px (spread ±6 px on a 128–256 px sprite);
  anchoring on the cell *corner* is biased −14 px on all six.
* **Every one of the 111 cells a `newbie` scene layer opens lands on a
  non-transparent pixel of the sprite that part draws.** The picture and the
  passability agree, so the player never stands on thin air beside a stone
  (`SceneSprites::test_the_stepping_stone_art_sits_on_the_cells_it_opens`).

Two shots make the same claim by eye. `--overlay` dots every walkable cell, red
from the base grid and cyan from a scene layer:

* `out/viewer/shots/scene-newbie-bridge.png` — every cyan dot sits on a stepping
  stone's flat top.
* `out/viewer/shots/scene-p-arena-bridge.png` — a stone bridge over open water,
  its deck entirely cyan and the land either side entirely red. Eight parts
  (`bridge05`, six × `bridge06`, `bridge07`) laid end to end by their cell
  offsets. This is the "structure you can cross" in its plainest form.

> **`.msk` frames.** 14 of the 2,621 `MapScene.ani` frames name a `.msk` instead
> of a `.dds` — and they are every piece of both `p-arena` bridges, which is why
> those drew as nothing at first. A `.msk` is a 1-bit-per-pixel mask
> (`bridge05.msk` is exactly 512·512/8 = 32,768 bytes) and the colour lives in a
> `.dds` of the same stem. Unpacking the mask and comparing it against that
> `.dds`'s own alpha channel gives **97.0 %** agreement, so the `.dds` alone is
> already the right sprite; `SpriteCache` substitutes it. INFERRED — the 3 % is
> the DXT3 alpha's soft edge against the mask's hard one.

### The `.scene` part header — the wiki's first two labels are wrong

`refs/conquer-online-wiki/Files/Scene.md` calls fields 0..1 "Origin X/Y" and
6..7 "Offset X/Y". Matching 105 `map/ScenePart/*.Part` text files against the
`.scene` binaries they were compiled from shows it is the other way round:

| field | what it is | how we know |
|---|---|---|
| 0..1 | the sprite's **pixel** offset | equals the `.Part`'s own `OffsetX=` / `OffsetY=` on 76 of the matched pairs (the rest are titles shared by several `.Part` variants) |
| 2 | frame interval, ms | `= AniInterval=` |
| 3..4 | width, height, in cells | `= Width= / Height=` |
| 5 | thickness | `= Thick=` |
| 6..7 | the part's **cell** offset inside the scene | no counterpart in the text — it is assigned when parts are composed. `bridgeA-1.scene` places bridge01 at (0,0), bridge02 at (−2,−9) and bridge03 at (0,−37): one long bridge from three reusable pieces |
| then `i32` | offset elevation | **uninitialised on ~12 % of parts** (values like 1446641773 are ASCII debris). Read and reported; never applied |

`map/ScenePart/*.Part` is plain CRLF text and is the human-editable source form
— by far the easiest entry point into map scenery for a modder.

## 5. Background planes — the DMap's trailing section

`newbie.DMap`'s header names only `map/puzzle/newbie.pul`. **`newbiebg.pul` is
in the file's trailer**, which is why the ground pipeline never loaded it.

`core/dmap.py` already decoded that trailer as "6 × u32 then a `char[260]`
path"; what it is, is the background list. **VERIFIED:** 57 of the 136 maps
carry 109 such records and every well-formed one names a `map/puzzle/*.pul`.
`tools/puzzle.PuzzleLibrary.backdrops()` returns them.

| field | reading | status |
|---|---|---|
| values[0] | draw index, furthest first | VERIFIED — `2009-7x` and `beach` each carry two, numbered 0 and 1, and 0 is the further one in both |
| values[2..3] | parallax percent, x and y | INFERRED — over 57 records: 30/30 (×42), 50/50, 100/100, 40/40, 20/20, 10/10 and five mismatched pairs. Range 10..100 and never above, which is what a percentage looks like and a pixel count does not |
| values[1], [5] | constant 4 and 8 on every record | not named |
| values[4] | 1 on 47 of 57, 7..16 on the rest | not named |

Three maps (`luckytree01_new`, `luckytree02_new`, `luckytree03_new`) have a
desynchronised trailer whose "paths" are fragments of an `.ani` filename; a
record whose path is not a `.pul` that exists is dropped rather than guessed at.

This is what turns the black void around `newbie`'s islands into sea, cloud and
farmland — compare `out/viewer/shots/scene-newbie-full.png` (no backdrop) with
`out/viewer/shots/scenery-newbie-crossing-*.png`.

> **Where this model is weakest.** The backdrop is drawn here in the ground
> texture's *world* pixel space, tiled, offset by the parallax factor. A
> 6 × 6-tile plane behind a 4,000-pixel window therefore shows its wrap seams,
> and the shipped art does not tile seamlessly — which is a fair hint that the
> engine draws these in **screen** space, one plane filling the viewport, with
> the parallax as a scroll rate. That is not recovered. The `rollSpeedX/Y`
> fields in a `PUZZLE2` `.pul` (a scrolling sky) are read and not animated.

## 6. The camera — derived, not chosen

The owner's direction: *"This is still going to be a locked camera game, in
2.5d… a game where you are a 3d character moving around a 2d map."* That is
what Conquer Online is, and it makes the camera a **consequence of the art**
rather than a taste decision. The ground-art placement rule

```
px = (gx - gy + K) * 32        py = (gx + gy - K) * 16
```

makes one cell a 64 × 32 pixel diamond — a 2:1 dimetric projection — and that
fixes all three properties.

**Orthographic.** Pre-rendered art has one projection everywhere. Perspective
gives the ground a vanishing point the painting does not have, so a character's
feet drift off their own cell as they move away from the screen centre.

**Yaw = −π/4.** `gl.js`'s `lookAt` builds screen-right as
`normalize(cross(Z, dir))` = `(−sin yaw, cos yaw, 0)`. `tools/terrain.py` puts
cell `(cx, cy)` at world `(cx·CELL, −cy·CELL)`, and the art sends +x to
screen-right-and-down and +y to screen-left-and-down, so screen-right must be
world `(1,1,0)/√2`. Only `yaw = −π/4` does that; `+π/4` mirrors the map. The
sign independently lands the camera on −Y, which is the side the character
corpus is authored facing — two constraints, one answer.

**Pitch = asin(½) = 30°.** With yaw at 45° a one-cell ground step projects to
`(CELL/√2, −CELL·sin(pitch)/√2)`, so the on-screen ratio of vertical to
horizontal travel is exactly `sin(pitch)`. The art wants 16/32.

> **It is not `atan(½)`.** 26.565° is the angle the cell axes make *on the
> finished picture* — a property of the output, and a very tempting wrong
> answer for the camera. It gives a ratio of 0.4472, an 11 % vertical squash.
> True isometric (35.264°, `asin(1/√3)`) gives 0.5774, a 15 % stretch.

Measured, by fitting `screen = s·artPixel + t` with one uniform scale over 121
cell centres spread across `newbie` — the test perspective cannot pass:

| camera | worst residual |
|---|---:|
| **ortho, yaw −45°, pitch asin(½)** | **0.00 px** |
| ortho, yaw −45°, pitch atan(½) | 131.9 px |
| ortho, yaw −45°, pitch 35.264° | 193.3 px |
| ortho, yaw +45°, pitch asin(½) | 3,110 px |
| perspective, yaw −45°, pitch asin(½) | 37,990 px |
| perspective at the old default pitch 0.72 | 311,903 px |

Run against the live page in a browser and reproduced as pure arithmetic in
`tools/test_viewer.py::IsometricCamera`, which also asserts that `gl.js` still
carries the constants and that the free camera is still reachable.

The orbiting perspective camera **stays available** behind `?freecam=1` — it is
genuinely the right tool for diagnosing placement — but it is a debug view. The
locked camera also refuses to write itself into the asset viewer's stored
orientation, so opening `coplay` once cannot silently re-aim `coviewer`.

### 6.1 Character scale — the original never "chose a zoom"

The question this answers: *how did the original client decide what size a
character is drawn at on the map?* The answer is that *nothing decides it at
runtime*. There is no zoom control and no scale constant to tune — the scale
is a consequence of the units the art is authored in.

**There is no zoom.** `graphic.dll` exports
`CMyBitmap::GameCameraZoom(bool)` at `0x41440` — a *boolean*, i.e. one step
in or out — and in this build it is **a stub that immediately returns**
(`0x41440` falls straight through to `add rsp,0x38 / ret` at `0x4150C`).
The camera is fixed. VERIFIED.

**The world's unit is the painted image's pixel.** `docs/ground_art.md` §3.5
already established (VERIFIED, RVAs cited) that `CPuzzleBlockX::Create`
builds the ground's vertex positions in **the pixel units of the painted
image** — `pos.x = w*i/nx` where `w` is the block's pixel width. The ground
therefore *is* the world coordinate system, and one map cell is a 64 × 32
**world-unit** diamond.

**Characters are drawn in that space at 100 %.** `ini/AdditiveSize.json` (30
rows, integrity-checked) carries a per-appearance `scale` — and it is a
**percentage**: `100` on 27 of 30 rows, with `110` and `120` on a few large
monsters. So the base transform is identity: **one character mesh unit is
one map pixel.** `Role3D.dll`'s `LoadAdjustConfig` (`0x7000`) reads
`ini/role3d.ini` (string at `.rdata 0x5E738`), which **is not present in
this install** — so even the adjustment layer is at its defaults. VERIFIED
for the tables; INFERRED that identity is the default `role3d.ini` would
otherwise modify.

**So the "zoom level" lives in the art.** The character meshes were authored
at whatever size looks correct beside a 64 × 32 px tile, and the engine
simply draws them 1:1. Measured on this install's own data, body appearance
`003000000` has a render bounding box **183.5 units tall × 57.8 wide**, i.e.
a character stands **183 map pixels** tall — just under three cell-widths —
which is exactly the proportion seen in retail footage.

#### What this corrects in our client

`tools/terrain.py`'s `CELL = 100.0` world units per cell is one of the two
"chosen, not recovered" constants (the other is `ZSCALE`). It is **wrong**,
and the right value follows from the projection in §6: a one-cell step must
land 32 px right and 16 px down, and with screen-right at `(1,1,0)/√2` that
requires

```
CELL = 32 * sqrt(2) = 45.2548...        (not 100)
```

At `CELL = 100` a character is drawn **100 / (32√2) = 2.21×** too small
against its own ground — which is exactly the discrepancy the retail video
showed. Two equivalent fixes: set `CELL = 32√2` so world units are map
pixels outright (preferred — it makes every other placement number
literal), or keep `CELL = 100` and scale figures by 2.21. Verified live by
scaling the figure by 2.21 in the running client: the character then stands
about one cell wide and two-and-a-half cells tall on Twin City's paving,
matching the footage. **Not yet applied** — it touches `terrain.py`,
`gl.js`'s camera framing and the viewer tests together.

### 6.2 The oblique tilt — how a 3D role stands on a 2D map

§6 derived the camera and §6.1 the character's size. The remaining piece is
the character's *orientation*, and the engine has an explicit control for it.

**The oblique angle — VERIFIED, RVAs cited.** `graphic.dll` keeps a global
`int` at `.data 0x25FC28`, read and written by
`CMyBitmap::GetObliqueAngle()` (`0x4B160`) and
`CMyBitmap::SetObliqueAngle(int)` (`0x4BCF0`) — each a two-instruction
accessor, so the global *is* the setting. Two things pin its value:

* the shipped `.data` initialiser is **`0xFFFFFFE2` = −30**, and
* device init explicitly re-sets it: `mov [0x25FC28], 0xFFFFFFE2` at
  `0x4B2E5`.

**It is degrees, and it drives a rotation about X.** At `0x4BAEB` the angle
is loaded, widened `cvtdq2pd`, multiplied by the double at `.rdata 0x1EAD48`
— `0x3F91DF46A2529D39`, which is exactly **π/180** — narrowed back to float
and passed to `D3DXMatrixRotationX` (the import thunk at `0x537FA`), whose
result is then applied via `D3DXVec3TransformNormal` (`0x537C4`).

**Every 3D object carries its own copy.** `Role3D.dll`'s `SyncObliqueAngle`
(`0x70B0`) calls `GetObliqueAngle` and caches it in two globals, and the
3D-object constructor (`sub_A630`) stores the current angle in the instance
at **+0x10** — so a role is *created* with the tilt, and an individual object
can deviate from the global.

#### What it is: the camera pitch, in the other frame

**−30 is our +30.** The engine does not use a pitched camera over a
horizontal world. Its ground is drawn as **flat quads in painted-image pixel
coordinates** (`CPuzzleBlockX`, §6.1) — i.e. effectively screen-aligned — so
the isometric look comes entirely from the *art*, and a 3D role must be
rotated to sit in that picture. The oblique angle is that rotation. Our
renderer instead keeps a real horizontal ground plane and **pitches the
camera** by `asin(½) = 30°`, which produces the identical image.

The two are the same rotation expressed in different frames:

| | engine | ours |
|---|---|---|
| ground | screen-aligned quads, in image px | horizontal plane, world units |
| the 30° | rotates the **object** (`D3DXMatrixRotationX`, −30) | rotates the **camera** (pitch `asin(½)`) |
| a standing figure | tilted back 30° into a flat scene | upright in a scene viewed 30° from above |
| on screen | height × `cos 30° = 0.866`, seen from above | height × `cos 30° = 0.866`, seen from above |

So the shipped **−30 is a VERIFIED, independent confirmation of §6's camera
pitch**, which was derived from the art's 2:1 diamond alone. Two unrelated
routes — the painted tile geometry and a constant in `.data` — give the same
30°.

#### The trap, which this project fell into

Applying the oblique tilt *on top of* a pitched camera **double-counts it**.
Measured live when we did exactly that: the figure's up axis came out
perpendicular to the view direction (dot = −0.0000), its on-screen height
rose to `1.0000` from `0.866`, and its "seen from above" component fell from
`sin 30° = 0.5` to **zero** — a face-on billboard. You stop seeing the top of
the head and shoulders at all, and the figure no longer sits in the picture.
The retail look is the `0.866 / 0.5` pair, not `1.0 / 0.0`.

Two independent checks agree the untilted figure is the correct one:

* **The video** (§6.1). A character measures ~2.5 cell-widths tall in retail
  footage, which is `183.5 · cos 30° / 64` — the *foreshortened* height. The
  tilted figure would be 1.155× that.
* **The geometry.** A camera 30° above the ground must show a standing figure
  from 30° above. Zero is the signature of a bug, not of a design.

**So `playerModel()` composes `T · facing · scale`, with no tilt term**, and
the 30° comes from the camera alone. Map layers are likewise untilted:
ground, scenery and covers are 2D art in the ground plane, and §5.1's 1:1
pixel mapping already draws them exactly as authored.

### 6.3 Lighting — there is none, on purpose

World assets are drawn **flat**: the art is pre-shaded by the artists and the
engine adds no lighting term of its own, which is why a character's shading
never changes as it walks. The game view therefore runs `gl.js`'s `unlit`
mode — texture × vertex colour and nothing else. `lit` (a hardcoded
directional term, `0.42 + 0.58·max(dot(n,l),0)`) is an **asset-viewer**
affordance for inspecting geometry and is wrong in the world: it invents a
light the game does not have and fights the art's own baked shading.

## 7. Draw order

```
background planes  ->  painted ground  ->  TERRAIN scenery  ->  entities  ->  COVER
```

The first four are composited into the ground texture in painter's order
(anchor `x + y` ascending, which is screen depth in a 2:1 view). `COVER` is a
separate RGBA layer over the *same pixel rectangle*, hung on a **shallow clone
of the ground mesh** — identical vertices, identical UVs — and drawn last with
`depthFunc(ALWAYS)`. Sharing the geometry rather than building a second quad is
deliberate: a cover can then never land half a cell off the thing it covers.

Only 197 of the 26,779 placed sprites have more than one frame (19 maps, led by
`qiling` with 34), so `/api/game/cover?t=<ms>` is only re-fetched on a timer
when the map actually animates.

## 8. Verified against a running server

Both a local `client/simserver.py` and **CoEmu**, at patch 5017, on loopback:

* A route from the starting island to the village exists in the overlaid grid
  and **does not exist** in the base grid.
* Our client walked it — **54 steps against CoEmu**, 34 of which stood on cells
  that only the scene layers make walkable. Every step confirmed.
* The control: forcing a step onto a cell the overlaid grid calls blocked
  (`client/`'s `--force`, which sends a `MsgWalk` the client would normally
  refuse) is met with `rejected-relocated` — CoEmu puts the character back.
  So the server is arbitrating, not accepting whatever it is sent, and its
  passability agrees with ours cell for cell along the whole bridge.

`out/viewer/shots/scenery-newbie-bridge-ingame-*.png` and
`scenery-newbie-crossing-*.png` are the character standing on a stepping stone
in the fixed isometric camera, with the background behind it.

## 9. What this corrects elsewhere

`docs/ground_art.md` §3.2 recorded **100.00 % of `newbie`'s walkable cells on
painted art**. That is still exactly true *of the cell grid* — and the bridge
cells are off the art precisely because they are not in the cell grid at all.
101 of the 111 scene-supplied cells hang over unpainted void and 10 overlap the
painted island edges. `puzzle.coverage()` now splits the two counts and
`test_viewer.py` asserts both halves together, so neither can be "fixed" by
weakening the other.

## 10. Still open

* **`EFFECT` and `SOUND` layers** are decoded and not used.
* **The backdrop's true drawing space** (§5) and the `rollSpeedX/Y` scrollers.
* **`thickness`** on a scene part, and the per-cell `elevation` a scene carries.
  Both are read; neither is applied. Elevation is the interesting one — a
  bridge deck at a different height than the water is what `offsetElevation`
  would be for, and it is uninitialised too often to trust.
* **`map/PuzzleSave/*.pux`** (`TqTerrain\0`), used by 4 maps, still undecoded.
* **Whether a cover's `origin` is its footprint's top-left or bottom-right.**
  The two differ by `(w−1, h−1)` cells and *no* measurement here can separate
  them, because every metric available moves with the anchor. Bottom-right is
  used, for consistency with the scene rule and because it gives 3–8 % less
  overlap between cover art rectangles on every map tested. 1,616 of the 2,380
  covers measured are 1 × 1, where the question does not arise.
