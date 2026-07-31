# The MapEditor — drawing a world map, and changing it

> **Note.** This document was written in the full development tree that this
> repository was extracted from. References to `docs/CONTEXT.md`,
> `docs/STATUS.md`, `docs/movement.md`, `tools/coplay.py`, `client/...` or the
> static-analysis tooling (`disfn`, `xref`, `riprefs`, `readtrace`, `vtable`,
> `d3dlayout`, `dll_analysis`) point at that tree and are not part of this
> repository. Every tool and command shown from `core/` and `tools/` that is
> not on that list ships here and works as written.


`docs/ground_art.md` recovered where a map's painted background sits.
`docs/map_scenery.md` placed the TERRAIN objects and COVER sprites on top of it
and found that the TERRAIN layers carry passability. Both ended with a picture
you could look at and nothing you could *do*.

This is the doing half: a **MapEditor** page in the Asset Viewer that draws any
of the 156 maps exactly as the game draws it, lets you toggle each layer, zoom
and pan, click any individual piece to inspect it, and stage a change to it
through the same `comod.py` path a `.dds` swap already uses.

```bash
py -3 tools/coviewer.py                 # then open http://127.0.0.1:8731/mapedit
```

Tool: **`tools/mapedit.py`** — module + CLI. Every number below comes out of it.

```bash
py -3 tools/mapedit.py --list                      # all 156 GameMap.json rows
py -3 tools/mapedit.py newbie --info
py -3 tools/mapedit.py newbie --editable           # what is free vs. checked
py -3 tools/mapedit.py newbie --pick 2300,1760     # what is drawn at that pixel
py -3 tools/mapedit.py newbie --png out/x.png --z 4
py -3 tools/mapedit.py newbie --png out/y.png --z 4 --layer passability
```

Claims are **VERIFIED** (measured against the shipped install by a test in
`tools/test_viewer.py`) or **INFERRED**. Nothing here re-derives the placement:
`tools/puzzle.py` and `tools/scene.py` are imported unchanged, and the camera is
`docs/map_scenery.md` §6's, not a new one.

---

## 1. The constraint that shapes the whole thing

`integrity.json` is what the client verifies. Everything the editor offers is
arranged around exactly which files are in it, so the first thing to do was
measure the file rather than quote it.

> **162 entries — but only 143 distinct files: 136 `.DMap` and 7 `ini/*.json`.**
> VERIFIED (`IntegrityManifest`).

The count usually quoted, "155 `.DMap` and 7 JSONs", is the **row** count, and
19 of those rows are repeats. They repeat for a reason: **a map with several
`GameMap.json` `DocumentId`s is listed once per id.** `lineup.DMap` appears 8
times, `grocery` and `p-arena` 3 each, `newbie`, `forum`, `tiger`, `prison` and
`mine-one` twice. Not one path carries two different hashes, so nothing is
ambiguous — and the 136 `.DMap` listed are **exactly** the 136 that ship: none
missing, none extra.

And the load-bearing negative:

| asset | in `integrity.json` |
|---|---|
| `map/map/*.DMap` | **yes — all 136** |
| `map/puzzle/*.pul` | no |
| `map/Scene/*.scene` | no |
| `map/ScenePart/*.Part` | no |
| `map/PuzzleSave/*.pux` | no |
| `ani/*.json` | no |
| every `.dds` | no |

VERIFIED by an explicit test that asserts **zero** entries with each of those
extensions. So:

* **A map's art — puzzle tiles, scene sprites, cover frames, backgrounds — is
  completely outside the manifest and edits exactly like any other texture.**
  On `newbie` that is 95 of the map's 96 files; on `Gulf`, 432 of 433.
* **The cell grid is inside it.** Passability, dimensions and layer placement
  all live in the `.DMap`, and changing one changes a file the client checks.

The editor shows which side of that line the selected thing is on **in every
inspector panel, always** — "this is free" is as much a fact as "this is not".

The hash algorithm is still unknown (`docs/dll_analysis.md` §9: the string
`integrity` appears in none of the six clean binaries), so `Integrity` reports
**membership**, never "your edit still matches". Membership is the fact that
matters.

## 2. What it draws

Five layers, each rendered **separately** and composited in the browser:

```
background  ->  ground  ->  TERRAIN  ->  COVER      + passability overlay
```

That is `docs/map_scenery.md` §7's draw order. `tools/terrain.py` composites the
first three into one texture because the *game view* wants one texture; an
editor wants to turn them off one at a time, so `mapedit.render_layer()` draws
each on its own with transparency where it has nothing, and toggling a layer
costs no request at all.

Two consequences worth stating:

* **The ground layer is genuinely transparent where a `.pul` slot is empty.**
  `puzzle.PuzzleMap.render` flattens alpha onto `VOID` (near-black), which is
  right for one opaque texture and wrong here — turning the background off would
  show black rather than showing through. VERIFIED: a walkable cell's own pixels
  come back fully opaque, an empty slot comes back fully transparent.
* **The passability overlay distinguishes the two kinds of walkable.** Green is
  the `.DMap`'s own grid; **cyan is a cell only a TERRAIN layer opens**; red is a
  cell the grid opens and a TERRAIN blocks. On `newbie` that draws the twenty
  stepping stones in cyan across a green-free void, which is
  `docs/map_scenery.md`'s entire finding as a picture you can click on.

`out/viewer/shots/mapedit-newbie-bridge-passability.png` is that picture.

### The camera is not a setting

`docs/map_scenery.md` §6 measured the locked camera — orthographic, yaw −45°,
pitch `asin(½)` — and found it maps art pixels to screen pixels with a worst
residual of **0.00 px** over 121 cell centres. So **the painted image already is
the game's view**, and drawing it is drawing the game's camera.

The editor therefore has no camera state at all: `zoom` is display pixels per art
pixel and `pan` is a translation, and there is no code path that could express a
different projection. `test_viewer.py::MapEditorUi` asserts that `mapmodel.js`
contains no `pitch`, `yaw`, `perspective`, `lookAt` or `Math.tan`. Zooming
cannot drift into a free camera because there is no free camera to drift into.

### Large maps

`Gulf` is 1,246² cells and **34,944 × 22,400 painted pixels** — 137 × 88 tiles at
full resolution. Nothing may ever ask for a whole map at zoom 1.

A view is a set of 256-pixel tiles at an integer decimation `z ∈ {1,2,4,8,16,32,
64}`, anchored at the art's own origin so a tile boundary at one level is a
boundary at every coarser one. At the fit zoom `Gulf` is **15 tiles**, and
`MapTileGeometry` asserts a whole-map view stays under 24. Only the tiles on
screen are ever requested; the server caches the encoded PNG and the browser
caches the image.

`out/viewer/shots/mapedit-gulf-whole-map.png` is the whole of `Gulf` at 3%.

Sprite drawing is culled by rectangle: `newplain` places 1,083 covers and a tile
touches a handful of them. The one unavoidable cost is decoding each map's
sprites once (2.8 s on `newplain`, cached for the session) — the frames are
needed for hit-testing as well as for drawing.

## 3. The map picker

All **156** `ini/GameMap.json` rows, largest first, each with the state of its
art rather than a silent omission. Measured:

```
150 ok       art places exactly
  4 pux      map/PuzzleSave/*.pux (TqTerrain), undecoded — the grid still draws
  1 mismatch sky: the art implies 608 cells and the .DMap says 588
  1 missing  GameMap.json names a .DMap that does not ship
```

The three `grocery` rows are three `DocumentId`s naming one file, and they are
shown as three rows because that is what the data says.

## 4. Clicking anything

A click is an art pixel. `MapArt.pick()` walks COVER, then TERRAIN, then the
ground, and a sprite counts as hit **only where its own alpha is non-zero** — so
a click through a gap in a tree selects what is behind it. VERIFIED by a test
that clicks a point inside `stand08`'s 256 × 256 rectangle but on a transparent
pixel and gets the ground.

What comes back for a scene part, verbatim from the running install:

```
kind            terrain
title           stand08.tga            ani     ani/mapscene.ani
frames          data/map/mapobj/sky/stand08.dds        1 frame, every 200 ms
anchor cell     91, 79                 footprint  3x3 cells, [89..91] x [77..79]
pixel offset    -90, -76               sprite origin  2214, 1700  (256 x 256 px)
layer index     32                     thickness 0    offsetElevation 0
compiled from   map/scene/stand08.scene
editable source map/ScenePart/stand08.Part
passability     opens 9 cells, blocks 0
integrity       not in integrity.json -> free to edit
```

For a puzzle click the panel keeps the **two grids apart**, because they meet at
one pixel and conflating them would be wrong: the `.pul`'s 256-pixel *tile slot*
(with its `Puzzle<n>` key and the `.dds` it resolves to) and the `.DMap`'s
64 × 32 *cell* (with its mask, surface, elevation, and whether a TERRAIN layer
is what makes it walkable).

`map/ScenePart/*.Part` is surfaced deliberately: it is plain CRLF text, it is the
source a `.scene` was compiled from, and it is by far the easiest entry point
into map scenery for a modder (`docs/map_scenery.md` §4).

## 5. Changing things

### Art — free, and it shows immediately

Identical to the `.dds` flow in `docs/viewer.md` §3, using the same two
endpoints: load a replacement image → `/api/preview` → **Stage this swap** →
`/api/stage`.

The one addition is that **the map reads through `mods/stage/` first**
(`mapedit.StageFirst`), which is the same precedence the game itself uses once a
mod is installed — `comod.py`'s whole premise is that a loose file beats an
archive. So a staged texture is on the map the moment it is staged, before
anything is installed.

Verified end to end against the running server: recolouring
`data/map/puzzle/newbie/newbie015.dds` and staging it changed **33,707 of 65,536
pixels** of the rendered map tile it appears in; unstaging restored that tile
**byte-identically**. `out/viewer/shots/mapedit-newbie-staged-tile.png` is the
magenta result on the map.

### The cell grid — gated, and surgical

`map/map/*.DMap` is one of the 136 files the manifest lists. The editor does not
forbid editing it — the owner may want it for a new map — but it will not happen
by accident:

* the panel is titled **"Passability — integrity-checked"** and leads with what
  the manifest is and what is in it;
* editing is behind a checkbox that has to be ticked;
* staging asks for confirmation naming the file and the number of cells;
* and the server **refuses with HTTP 409** unless the request carries the exact
  acknowledgement string, returning the explanation rather than a bare error.

The edit itself is a byte patch, not a rewrite. The cell layout is exact and
proved — `core/dmap.py`'s per-row checksum reproduces 58,245 of 58,898 shipped
rows with 133 files at 100% — so a cell's mask is at a known offset and the row
checksum that covers it is at another. `stage_passability()` copies the file,
writes those two, recomputes the checksum **through `dmap.row_checksum` itself**
so the two can never drift, re-parses the result before writing it, and reports
how many row checksums came back bad.

Measured on `newbie`, opening cells (80,93) and (81,93):

```
118,644 bytes in, 118,644 out
4 bytes differ            two cell masks + one row checksum
132/132 row checksums OK  before and after
2 cells changed           walkable 1,561 -> 1,563
layers, portals, extras, puzzle path: byte-identical
```

VERIFIED by `DMapPassabilityEdit`, which also pins that a refusal leaves no file
behind, that successive edits accumulate on the staged copy rather than each one
reverting the last, and that once staged **the editor reads your grid** — the map
you look at is the map you are making.

### Everything goes through comod.py

The viewer never writes to the game install. It writes `mods/stage/`, and
*Install for real* shells out to `comod.py install --yes`, which takes the
backups, writes `mods/manifest.json` and gives you `uninstall`. `comod.py diff`
sees a staged `.DMap` as an ordinary `MODIFIED` row.

## 6. Layout

```
tools/mapedit.py         the model: layers, tiles, picking, integrity, the gated patch
tools/webui/mapmodel.js  state + geometry + inspector descriptors — NO DOM, NO fetch
tools/webui/mapedit.js   canvas, DOM and network — the only half that touches either
tools/webui/mapedit.html the page
tools/webui/mapedit.css  no literal colour; every value is a style.css token
```

Two rules are enforced by tests rather than hoped for, both for the same reason
(the client has to port off the browser eventually, and both failure modes are
silent):

* **`mapmodel.js` contains no `document.`, `window.`, `localStorage`, `fetch(`,
  `createElement`, `innerHTML` or `new Image`** — the same check `views.js` has.
* **`mapedit.css` contains no hex, `rgb()` or `hsl()` literal**, and every
  `var(--…)` it uses is declared in `style.css` — the same rule `ui.css` lives
  under. The map canvas itself is deliberately *not* skinned: it draws the
  game's own art, and tinting it would be lying about the asset.

A third catches the drift that would be hardest to notice: `TILE`, `ZOOMS` and
`LAYERS` are asserted identical in `mapmodel.js` and `mapedit.py`, because a
tile the page asks for and the server cannot render is a blank square with no
error.

Server routes, all on the viewer's own port:

```
GET  /mapedit                      the page
GET  /api/mapedit/maps             all 156 rows + state
GET  /api/mapedit/map?name=        one map: geometry, layers, backdrops, integrity
GET  /api/mapedit/tile?name=&layer=&z=&tx=&ty=[&t=]     one layer of one tile, PNG
GET  /api/mapedit/pick?name=&px=&py=[&layers=]          what is drawn there
GET  /api/mapedit/editable?name=   every file of this map, sorted by the line
POST /api/mapedit/passability?name=    {cells, ack}  — 409 without the ack
```

Tests: `py -3 tools/test_viewer.py IntegrityManifest MapTileGeometry
MapEditorModel DMapPassabilityEdit MapEditorStagedArt MapEditorUi
MapEditorRoutes` (36 tests inside the suite's 326).

## 7. What the data does not let you inspect

Stated rather than papered over.

* **`EFFECT` and `SOUND` layers are decoded and not drawn.** `core/dmap.py`
  reads them; an `EFFECT` layer is a `3DEffect.ini` key and a `SOUND` layer is a
  file path, and neither has a footprint on the painted image. They are counted
  in the map's declared layer total and nothing pretends to place them.
* **`map/PuzzleSave/*.pux` (`TqTerrain\0`) is undecoded**, so the four maps that
  use it have no ground art at all. Their cell grid, scenery and passability
  still draw and the picker says why.
* **`sky` cannot be drawn correctly.** Its art is 20 cells taller than its map
  (`docs/ground_art.md` §3.1). It is listed, labelled `mismatch`, and drawn
  anyway with the placement it does have.
* **Staged *placement* does not preview, only staged art.** Textures are read
  through the stage tree; the `.pul`, `.scene` and `.ani` files are read straight
  from the install by `tools/puzzle.py` and `tools/scene.py`, so staging one of
  those shows up only after `comod.py install`. Making those stage-aware means
  changing two modules the game view also imports, and was not worth doing
  quietly.
* **Nothing on the page animates, deliberately.** The tile route takes a `?t=`
  and `mapmodel.visibleTiles` carries the clock, so an animated `TERRAIN` or
  `COVER` sprite *can* be asked for at a time — the CLI and the API will do it.
  There is no play button, because animating would re-fetch every terrain and
  cover tile on a timer for the 197 sprites out of 26,779 that have more than
  one frame. The inspector reports the frame count and the interval instead.
  Animated **ground** is frozen at frame 0 as it is everywhere else in this
  project, and the four `.pul` files with a non-zero `rollSpeedX/Y` do not
  scroll.
* **A scene part's `thickness` and per-cell `elevation` are read and never
  applied**, and `offsetElevation` is uninitialised on ~12% of parts — the panel
  says which of those two it is looking at rather than showing debris as data.
* **Whether a cover's origin is its footprint's top-left or bottom-right is
  still undecidable** (`docs/map_scenery.md` §10). Bottom-right is used, as
  everywhere else in this project; 1,616 of 2,380 covers are 1 × 1, where the
  question does not arise.
* **The background planes tile visibly.** `docs/map_scenery.md` §5 already
  suspects the engine draws them in *screen* space with the parallax as a scroll
  rate; the seams in a wide view are that model's weakness showing, not a bug in
  the editor.
* **`integrity.json`'s hash cannot be recomputed**, so nothing can tell you
  whether an edit would still pass — only that the file is checked.

## 8. Shots

```
out/viewer/shots/mapedit-newbie-all-layers.png           every layer on, a scene
                                                          part selected
out/viewer/shots/mapedit-newbie-bridge-passability.png   the stepping stones with
                                                          the passability overlay
out/viewer/shots/mapedit-newbie-passability-gate.png     the integrity gate and
                                                          four pending cell edits
out/viewer/shots/mapedit-newbie-staged-tile.png          a staged .dds on the map
out/viewer/shots/mapedit-gulf-whole-map.png              34,944 x 22,400 px at 3%
out/viewer/shots/mapedit-newbie-all.png                  the CLI's own render
out/viewer/shots/mapedit-newbie-pass.png                 the passability layer alone
```
