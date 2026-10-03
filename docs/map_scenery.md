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

Corpus-wide (`py -3 tools/scene.py --verify`), **RE-DERIVED 2026-08-11 per
install**, because the previous figures were a count over a population we now
know was not the corpus:

| install | maps | place TERRAIN | place COVER | scene parts | covers | opened | blocked | off map |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5517 | 192 | 45 | 164 | 11,374 | 57,708 | 20,094 | 7,930 | 0 |
| 6090 | 247 | 63 | 215 | 11,365 | 85,268 | 20,531 | 8,818 | 49 |
| 6609 | 297 | 73 | 257 | 11,461 | 92,073 | 20,632 | 9,399 | 49 |

**What moved, and why — the numbers are auditable rather than merely current:**

* **The old line said `136 maps`.** That is CCO's corpus, from an install no
  longer on this machine, and it had been quoted through two clients that never
  had 136 maps. Nothing recorded which install it was measured on, which is the
  whole reason it survived.
* **The survey walked `map/map/*.DMap`.** It now enumerates `dmap.map_names`
  and reads each map from the file its registry row names, so it covers the
  113 maps on 6609 that ship only as a `.7z` and reads current content for the
  77 whose loose file is a previous client's
  (`C-2026-08-10-claude-explorer-map-archive-alarm`).
* **The old survey could not tell 6090 from 6609.** Both returned *identical*
  passability totals, because all 184 of their loose `.DMap` files are
  byte-for-byte the same. The three rows above differ, which is the first time
  this measurement has discriminated between two clients at all.
* **`0 off the map` was true of the smaller corpus and is not a rule.** All 49
  cells belong to **one map, `bp-club`**, which ships archive-only and had
  therefore never been surveyed. 296 of 297 place every scene cell on the grid,
  so §2's footprint rule is unaffected — this is one map's content, named
  rather than averaged away.

So the owner's "this is done a lot" is right about covers, and by a wider margin
than the old figure showed: **85 % of maps use them on 5517, 87 % on 6090 and
6609.** Scene objects are rarer and decisive where they appear.

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
path"; what it is, is the background list, and it is **groups** of planes rather
than a flat run (`core/dmap.parse_trailer`). **VERIFIED**, and RE-DERIVED
2026-08-11 over `dmap.map_names` rather than a `*.DMap` glob:

| install | maps carrying backdrops | records |
|---|---:|---:|
| 5517 | 80 | 187 |
| 6090 | 84 | 193 |
| 6609 | 87 | 196 |

Every well-formed record names a `map/puzzle/*.pul`.
`tools/puzzle.PuzzleLibrary.backdrops()` returns them. **The old figure of
`57 of the 136 maps / 109 records` was CCO's**, carried through two clients
that never had 136 maps; the counts rose here because the corpus did, not
because the reading changed.

**And 57 was never a map count — re-measured 2026-08-26 on CCO itself.**
`PuzzleLibrary.names()` enumerates **137** maps there, **55** carry a
resolvable plane, and those 55 hold **57 GROUPS / 162 PLANES**, with **0**
records naming a `.pul` that is not shipped. 57 is the number of trailer
GROUPS, which is what the pre-`C-2026-08-10-dmap-plane-groups` flat model
counted as "records" before `core/dmap.parse_trailer` learned that a group can
hold more than one plane. The `star01`..`star10` family is the whole
difference: 10 groups holding 115 planes between them. So the row for CCO in
the table above would read **55 / 162**, on the same basis as the three rows
that are there.

| field | reading | status |
|---|---|---|
| values[0] | draw index, furthest first | VERIFIED — `2009-7x` and `beach` each carry two, numbered 0 and 1, and 0 is the further one in both |
| values[2..3] | parallax percent, x and y | INFERRED — over 57 records: 30/30 (×42), 50/50, 100/100, 40/40, 20/20, 10/10 and five mismatched pairs. Range 10..100 and never above, which is what a percentage looks like and a pixel count does not |
| values[1], [5] | constant 4 and 8 on every record | not named |
| values[4] | 1 on 47 of 57, 7..16 on the rest | not named |

Three maps (`luckytree01_new`, `luckytree02_new`, `luckytree03_new`) have a
desynchronised trailer whose "paths" are fragments of an `.ani` filename; a
record whose path is not a `.pul` that exists is dropped rather than guessed at.

> **RESOLVED 2026-08-10 — the trailer is GROUPS of planes, not a flat run of
> records.** The whole open question below is answered, and the answer is that
> both candidate strides were right about their own maps because neither was a
> stride:
>
> ```
> u32 n_groups
> per group:  u32 v0, v1, v2, v3        <- shared draw index + parallax
>             u32 n_planes
>             n_planes x { u32 flag(8); char[260] path }
> ```
>
> `20 + 264 == 284`. **A group holding exactly one plane is byte-for-byte a
> 284-byte record**, which is why the flat model read 171 of 181 maps
> correctly and why `2009-7x` and `icecrypt-lev5` — two groups of one plane
> each — appeared to validate it. The flat model's `values[4]` was the plane
> count: it reads **1** on all 66 single-plane records and **7, 8, … 16** on
> exactly `star01`..`star10`.
>
> MEASURED over all 181 maps with a trailer on 5517:
>
> | | |
> |---|---|
> | group walk ends exactly at EOF | **181 / 181**, 0 misfits |
> | `bytes_unconsumed != 0` | **0** (was 10) |
> | `extra_count != len(extra)` | 0 |
> | recovered paths that are not `.pul` | 0 |
> | maps whose output is unchanged | 171 |
> | maps that gain planes | 10 (7…16, one per map) |
>
> One dangling reference surfaced and is unrelated to the parse: `hq.DMap`
> names `map\puzzle\hqbg.pul`, which is on no install — not loose, not in any
> archive on 5017/5517/6090. It read identically under the old model and
> `puzzle.py` already drops it. Pinned in `DMapTrailingSection.KNOWN_DANGLING`
> so a *second* one fails.
>
> `core/dmap.py::parse_trailer`, `tools/test_viewer.py::DMapTrailingSection`
> (7 tests, two of them controls), `docs/CORRECTIONS.md`
> `C-2026-08-10-dmap-plane-groups`.

> ── BOUNDED 2026-08-26 ─────────────────────────────────────────────────────
>
> **The `181 / 181, 0 misfits` above was measured on 5517 and on nothing else,
> and the table did not say so.** Re-measured over every declared install,
> each map loaded the way the client loads it (`dmap.parse_map` over
> `dmap.map_names`):
>
> | install | parsed | v1005/v1006 | leave bytes unread | bytes unread |
> |---|---:|---:|---:|---:|
> | 5017 | 142 | 0 | 0 | 0 |
> | 5065 | 144 | 0 | 0 | 0 |
> | 5165 | 154 | 0 | 0 | 0 |
> | 5517 | 192 | 1 | **0** | 0 |
> | 6090 | 247 | 0 | 0 | 0 |
> | CCO 2.0 | 136 | 7 | **7** | 134,908 |
> | 6609 | 297 | 17 | **17** | 203,864 |
> | Zephyr-1057 | 305 | 31 | **41** | 984,768 |
> | 7878 | 470 | 163 | **164** | 11,579,752 |
>
> > **SUPERSEDED 2026-09-07 — this table is the PRE-`2ee16bfa` reading.**
> > `dmap: the plane-group section at 1005/1006` (2026-08-29) taught `parse`
> > the 1006 second counted list (`late_layers`), and the "leave bytes unread"
> > column is now **0 on eight of the nine installs** — CCO 7 to 0, 6609 17 to
> > 0, 7878 164 to 0 — with zephyr1057 at **16**, of which 10 are the same
> > v1003/v1004 residue names as before. The live figures are the `GAP` table
> > in `DMapTrailerModelRange` (`tools/test_viewer.py`), which is pinned per
> > install and gated. The numbers above are left unedited because they record
> > a run that happened; the differential that proves they were right when
> > taken is in `C-2026-09-07-worktree-agent-a1b45eeb59c461193`
> > (`docs/CORRECTIONS.md`).
>
> **The boundary is the map's VERSION.** 218 of the fleet's 219 v1005/v1006
> maps leave their tail unread and the single exception is 5517's
> `icecrypt-lev3`, which declares **`n_groups = 0`** — it ships no
> background-plane section at all, so it ends at EOF with nothing to read
> rather than with the section read correctly. (It is *not* "the one 1005 map
> with no cover records": it carries two. Its **trailer** is empty.) Every v1003/v1004
> map on all six official clients and on CCO still closes exactly at EOF. The
> figure above is not wrong; it was **unbounded**, and a suite that resolved
> its install ambiently could not tell a reader which half they were in.
>
> **`*_new` is not the unit, and the three `luckytree*_new` maps named earlier
> in this section are the same phenomenon under a name that does not carry.**
> The suffix holds on CCO (7 of 7) and 6609 (17 of 17), which is why it looked
> like a family; **29 of Zephyr-1057's 41** gap maps carry no `_new` at all —
> `canyon1`, `desert1`, `crystalcave02`, `Asgard`, `godes-church`, `magic`,
> `southgate` — and 2 of 7878's 164 do.
>
> **It is a different SECTION, not a wrong stride** — the same shape of answer
> this section already reached once for the star family, one version later.
> Scanning all 229 gap maps for a group walk that lands on EOF recovers one in
> **142** of them, and **140 of those need a 32-byte group header, not the
> modelled 20**: the four `values` u32s, then three more (`160` on every
> recovered group; the other two drawn from `{3,5}` and `{1,6}`), then
> `n_planes`. The section ends either exactly at EOF or 8 zero bytes short,
> and **302 of the 311 recovered paths are `.pul` files that exist on disk** —
> a content check, not closure. The two that fit the modelled 20 are Zephyr's
> `desert1` and `southgate`, both v1004 residue, where only the section's
> START had moved. It also does not begin where the layer walk
> ends: on CCO's `bp-flandlords-y_new` the group walk starts exactly
> `4 + 135 × 424` bytes later — 135 being the u32 sitting there and 424 the
> 1005/1006 cover record — so a **second counted list** separates the layers
> from the backdrops, 43,696 bytes of it on `ninja01_new`. Its first record's
> tag is **4**, the tag 1006 renumbered to **24** for the list `dmap.parse`
> already walks (`COVER_TAG_1006`), naming `ani\mapscene-new.ani`. Recorded as
> an observation and not modelled: two readings fitted 1006's first record
> once already, and only a stride separated them.
>
> `parse_trailer` is deliberately **not** changed on that evidence. 87 of the
> 229 fit no reading defensible today, and
> `C-2026-08-11-claude-explorer-1005-tag0` is the standing record of what a
> plausible wrong reading of an unmodelled 1005 structure costs. What changed
> is that the gap is now named, bounded and audible:
> `tools/test_viewer.py::DMapTrailerModelRange` holds each install to its own
> figure and refuses to measure one it cannot identify, and
> `DMapTrailingSection` — every number of which is 5517's — is PINNED to 5517
> instead of taking whatever install the reader had configured. (The 08-26
> repair resolved it by *declared kind*, and would have SKIPPED where the kind
> was absent; what landed is `DeclaredInstall`, which names the install and
> FAILS when it is missing, per the owner's ruling of 2026-08-28. The two
> classes therefore differ on purpose: `DMapTrailingSection` asserts one
> install's numbers and must not run anywhere else, while
> `DMapTrailerModelRange` asks the *range* question over whatever is
> configured and skips only a corpus it cannot identify.)
> `docs/CORRECTIONS.md` `C-2026-08-26-claude-vibeco-dx-camera`.
>
> <details><summary>The original OPEN entry, kept because the reasoning in it
> is what nearly bought the wrong fix</summary>
>
> **OPEN (2026-08-10) — `star01`..`star10` leave the trailer half-read, and
> the model above does not explain them.** `dmap.parse` finishes with
> `bytes_unconsumed` non-zero on exactly these ten maps and nowhere else
> (181 parsed, 0 raised). On `star01` the count reads **1** and **seven**
> records follow; the unread remainder is 6 × 264 bytes, every one naming a
> real `map/puzzle/starbg*.pul`. So six of seven background planes are
> dropped, growing to fifteen of sixteen on `star10`.
>
> **Do not "fix" this by setting `EXTRA_RECORD = 264`.** That is the obvious
> reading — `values[5]` is constant 8 above, and every unread record begins
> with 8, which looks exactly like a stride 20 bytes too long. Measured
> across every map with a trailer, it is wrong: at 284 `2009-7x` and
> `icecrypt-lev5` give **2/2 real paths and end exactly at EOF**, and at 264
> they give 1/2 with 40 bytes over. One fixed stride does not fit both
> families.
>
> The likelier shape is that the star family's section does not **begin**
> where the parser thinks it does — a count of 1 in front of seven records is
> a misplaced start, not a wrong stride. Nobody has taken it.
> `tools/test_viewer.py::DMapTrailingSection` pins all of the above,
> including a test that fails the 264 fix specifically.
>
> Nothing had noticed because `d.bytes_unconsumed` — the parser's own audible
> channel — has no reader in the suite for `dmap`; the only assertion on that
> field is `scene.parse_scene`'s.
>
> </details>

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

> **SUPERSEDED 2026-08-15 — the arithmetic below is sound; the premise it rests
> on is not. Do not delete it, and do not re-derive it.** `tools/terrain.py:106`
> is **`CELL = 64.0`** today, and `tools/webui/play.js:37` carries the same
> 64.0. See `C-2026-08-15-claude-vibeco-gl-openlist`.
>
> **Which premise failed — and it is not the projection.** §6's geometry is
> untouched: a one-cell step really is 32 px right and 16 px down, so
> `CELL = 32*sqrt(2)` really is the value that makes **one world unit one pixel
> of the painted image**. What failed is the step *before* the arithmetic —
> reading `CPuzzleBlockX::Create`'s block width `w` as *the painted image's
> pixel width*. That is an **assumption**, never a measurement;
> `docs/ground_art.md` §6 has always listed it as OPEN, because the caller is
> inside the packed exe. Held against the real game, `32*sqrt(2)` makes
> characters roughly **1.5× too large**.
>
> So the derivation below is kept **as a correct piece of projection
> arithmetic resting on an unproven premise about `w`**. Anyone who re-derives
> 45.2548 and concludes the code is wrong has re-derived the sound half and
> skipped the premise that broke.

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
matching the footage.

**What was actually applied — the scale went on the MAP, not the character.**
The "not yet applied" this paragraph used to end on is stale: a value *was*
applied, and it is not this one. `tools/terrain.py:106` is **`CELL = 64.0`**,
which draws the painted ground `sqrt(2)` **larger** than its own pixels, paired
with **`FIGURE_SCALE = 1.0`** — characters at the size they were authored,
which is what `ini/AdditiveSize.json` says the engine does. `tools/terrain.py`'s
module docstring (§SCALE) is the **home** of that reasoning, including the
cell-widths a body then stands at and the cost (the art is sampled above its
native resolution); this section cites it rather than restating it.
`tools/webui/play.js:37` carries the same 64.0.

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

A third, from pixels: Route B's DX11 renderer measures **30.04°** off its own
backbuffer, by sliding and lifting a marker of known world position and fitting
the centroid slopes (`routeb/repro_entity_scale.py`, 2026-08-15). That gate also
re-reads the −30 out of the install's own `graphic.dll` rather than citing the
RVAs above — which is worth knowing, because **the RVAs in this section do not
resolve in 5065 or 5517**. The accessor is found through the export table
instead, and the global's address taken from `mov eax,[imm32]`'s own operand:
5517 `0x13fa0 → 0x100441c4`, 5065 `0x0b150 → 0x10024300`, both initialised −30.
See `docs/routeb_entity_scale_2026-08-15.md`.

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

> **The DX11 renderer shares the mapping, not the mesh.** `routeb/render.cpp`
> places each cover as its own quad, whose corners come from **inverting the
> ground's own vertex rule** `corner_px(gx,gy)` at the sprite's corner pixels
> rather than evaluating it at lattice points. That preserves the property the
> shallow clone existed for — one cell↔pixel mapping, so a cover cannot land
> half a cell off — without compositing the layer into an RGBA image, which
> would have forced a CPU DXT decode. Measured agreement between the two
> derivations of a quad's screen rect: **0.000 px** over 68 quads.
> `docs/routeb_cover_layer_2026-08-15.md` §4.

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

* ~~**`EFFECT` and `SOUND` layers** are decoded and not used.~~ — **split
  2026-08-15, and only half of it was ever true. `C-2026-08-15-claude-vibeco-gl-openlist`.**

  * **`EFFECT` is DRAWN — this bullet is closed.** `tools/coplay.py`'s
    `_map_effects` reads the `.DMap` EFFECT records and converts them to cells,
    `MAPFX_DRAW_LIMIT = 48` caps how many are drawn near the player, and the
    route is `/api/game/mapfx` (`api_mapfx`). `docs/world_effects.md` §2 is the
    home of the coordinate space and the corpus (**2,504 EFFECT records across
    45 of 136 maps**, all 2,504 landing inside their map's grid).
  * **`SOUND` is genuinely open — and note WHY, because it is a different
    state from EFFECT's.** It is not "decoded but not wired up": **there is no
    audio subsystem in this client at all.** `client/settings.py:148-152` says
    so in the settings themselves — the sound sliders store a number and
    nothing reads it, *"There is no audio path"* — and the tables are read
    (`ActionSound.ini`, `docs/animation.md` §3) while nothing plays. "Not
    drawn" and "there is nowhere to draw it" are different problems: EFFECT
    needed a route, SOUND needs a subsystem first.
* **The backdrop's true drawing space** (§5) and the `rollSpeedX/Y` scrollers.
* **`thickness`** on a scene part, and the per-cell `elevation` a scene carries.
  Both are read; neither is applied. Elevation is the interesting one — a
  bridge deck at a different height than the water is what `offsetElevation`
  would be for, and it is uninitialised too often to trust.
* **`map/PuzzleSave/*.pux`** (`TqTerrain\0`) — **the header is decoded; the
  payload is NOT, and the negative is the useful part.**

  > **REACHABILITY, measured 2026-08-15 — 237 was the corpus AT THE TIME OF
  > MEASUREMENT; 169 is what is reachable today.** The difference is entirely
  > CCO's 68: `CCO-local` has **no `map/` directory and zero `.pux`**, because
  > CCO's corpus came from an install no longer on this machine — a caveat §1
  > already carries and this figure did not inherit. Reachable today: **6609 20,
  > 7878 136, Zephyr-1057-local 13 = 169**, and 237 − 169 = 68 exactly.
  > **Control: the same sweep returns 20 on 6609, so it is not a broken scan.**
  > The older figure is **not wrong, it is unreachable** — and those are
  > different. Anyone re-deriving the corpus gets 169 and should not read that
  > as contradicting the 237. `C-2026-08-15-claude-vibeco-gl-openlist`.

  **Corpus, RE-DERIVED 2026-08-11:** 237 files across four installs — 6609 20
  (in *two* directories), 7878 136, Zephyr 13, CCO 68 — and **160 maps** name
  one as their puzzle path. The *"used by 4 maps"* this bullet used to carry
  was **CCO's** figure, and CCO is the only install where it is still true.

  **Header** (`core/dmap.read_pux`, verified on all 237):

        +0   char[10]  "TqTerrain\0"
        +16  u32  1000      constant on all 237
        +20  u32  width     in tiles
        +24  u32  height    in tiles
        +28  u32  1000      constant on all 237

  **The tile is 256 px, and `GameMap.dat` disagrees.** §4's placement identity
  ties a puzzle's tile dimensions to the map's own cell grid, so the grid size
  can be **solved rather than looked up**: `G = W_cells / (pux_w/64 + pux_h/32)`
  comes out **256.0 on all 160, no spread**. The registry says
  `PuzzleGridSize` **128** for **113** of those maps; forcing 256 satisfies the
  identity **160 of 160**, and every registry-128 failure is out by **exactly
  2×**, never anything else — the signature of a wrong constant rather than a
  wrong parse. Whether the registry's 128 is wrong or means something else for
  a `.pux` is **not claimed**; only which number the geometry demands.
  `dmap.PUX_GRID`.

  > **DO NOT DECODE THE PAYLOAD AS A TILE INDEX — it is not one, and this is
  > measured.** Over 14 files on 7878 the size runs **20.8 to 172.2 bytes per
  > tile**, and a linear fit of file size against tile count is out by
  > **17,725 bytes** at worst. A flat index cannot do that. `PuzzleSave` is
  > what the name says: an **editor save**, far richer than the compiled
  > `.pul` it stands in for. This negative cost a measurement and is recorded
  > so the next person does not spend a day rediscovering it.

  `tools/puzzle.py` still refuses these 160 maps — it has no tiles to draw —
  but its refusal now carries the geometry it *does* have (`15x11 tiles at
  256 px, implying a 148-cell map`), because *"not decoded"* sent every reader
  back to the format when the answer was already in hand. See
  `C-2026-08-11-claude-explorer-pux`.
* **What a cell `mask` of 2, 4 or 5 MEANS — open, and TWO instruments have
  already been spent on it. Read this before spending a third.**

  The row checksum uses the **raw** `mask` (`core/dmap.row_checksum`), and the
  corpus carries `0`, `1`, and then **2 (668 cells, Zephyr only) · 4 (366,347)
  · 5 (31,230)**. `5 = 4|1`. The obvious model is a bitfield with **bit 0 as
  "blocked"**, which would make `2` and `4` *walkable* — and every consumer in
  this repo tests `== 0` / `!= 0`, so it would mean 353,326 cells on 7878 are
  being blocked that are not.

  > **CONNECTIVITY DOES NOT DISCRIMINATE THIS. Do not spend an afternoon on
  > it.** Promoting mask-4 cells to walkable merges components — 18→15, 8→4,
  > **10→1** on three maps — and it looks conclusive. It is not: a control
  > promoting an *equal number of arbitrary blocked cells that touch open
  > ground* merges **better** on `2020tsf_new` (baseline 40, mask-4 → 30,
  > **control → 18**). §2's footprint rule was strong because the *wrong*
  > reading joined nothing; here the wrong reading sometimes joins more. The
  > asymmetry is the evidence, and this class of question does not have it.

  **The painted art is the instrument that does discriminate**, and it gives
  the strongest evidence available today. On-art rates, by cell value:

        7878 + 6609    mask 0  100.0%   mask 1  17.8%   mask 4  100.0%  (n=557)
        Zephyr         mask 0  100.0%   mask 1  40.5%   mask 2  100.0%  (n=668)

  **Two bit-0-clear values, on two corpora that do not share them, both
  tracking walkable cells exactly and unlike blocked cells.** Note which
  corpus is which: **7878 holds 96% of all mask-4 cells**, so it corroborates
  nothing about `4`; **Zephyr is the only install with a `2`** and has zero
  fives, which is what makes it independent.

  **This is NOT settled, and the limit is precise: "on painted art" is
  necessary for walkable, not sufficient** — an interior wall is on the art
  too. What is shown is that `2` and `4` are *interior*, not that they are
  *walkable*. **And `mask 5` — the one value with bit 0 SET — has zero
  testable samples.**

  **Why it has zero samples is the finding that reorders this list: the maps
  carrying high masks are overwhelmingly `.pux` maps**, and `tools/puzzle.py`
  refuses those for want of a tile payload — so only **2 of 7878's maps** can
  run the art instrument at all. **Decoding the `.pux` payload turns a 2-map
  sample into a 160-map one and settles bit 0 either way.** The two items
  above are one item with a dependency, not two.

* ~~**Whether a cover's `origin` is its footprint's top-left or bottom-right.**~~
  **CLOSED 2026-08-15 — it is BOTTOM-RIGHT, and now measured rather than
  assumed.** `docs/routeb_cover_layer_2026-08-15.md` §3; re-derive with
  `py -3 routeb/verify_cover.py --anchor`.

  This bullet used to say *no measurement here can separate them, because every
  metric available moves with the anchor*, and *1,616 of the 2,380 covers
  measured are 1 × 1, where the question does not arise.* **The second half is
  exactly right and is the reason the first half was wrong.** The two
  hypotheses differ by `16·((w−1)+(h−1))` px of screen depth — identically zero
  on a 1 × 1 — so the corpus that had been measured could not answer, but the
  **multi-cell** covers can, and there are 2,160 of them across
  `newplain`/`newbie`/`p-arena`/`desert`.

  §4's own cover metric (opaque bounding box vs footprint bounding box), paired
  per cover and decided by a sign test:

  | axis | n | median \|err\| BR | TL | paired wins BR–TL | sign p |
  |---|---:|---:|---:|---:|---:|
  | vertical | 2,160 | 12 px | 35 px | 1,573–586 | 5.9 × 10⁻¹⁰⁴ |
  | horizontal (only `w ≠ h` can speak) | 479 | 15 px | 46 px | 389–90 | 1.3 × 10⁻⁴⁵ |

  and the margin **grows with footprint size** — 2×2: 10 vs 28 px; 3×3: 3.5 vs
  67; 4×4: 12 vs 108 — which is what a right convention against a wrong one
  looks like, and what noise does not do.

  The control is the old caveat turned into an assertion: **1,234 of 1,234
  one-cell covers tie exactly**, on both metrics and both axes.
  `verify_cover.py` FAILS if they ever separate, because on a 1 × 1 the two
  conventions are one convention and an instrument that told them apart there
  would be broken rather than informative.

  Nothing in `tools/scene.py` changed: the convention tested is the one already
  in use. The old justification (*"consistency with the scene rule … 3–8 % less
  overlap"*) was an aesthetic argument for an untested choice, and it happens to
  have been right.
