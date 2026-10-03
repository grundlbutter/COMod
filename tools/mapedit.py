#!/usr/bin/env python3
r"""
mapedit.py -- the MapEditor's model: a map, its layers, what you may change.

    py -3 tools/mapedit.py --list                     # every GameMap.json row
    py -3 tools/mapedit.py newbie --info
    py -3 tools/mapedit.py newbie --pick 2100,1400    # what is under that pixel
    py -3 tools/mapedit.py newbie --png out/x.png --z 4
    py -3 tools/mapedit.py newbie --editable          # what is free vs. checked

WHAT THIS ADDS ON TOP OF puzzle.py / scene.py / dmap.py
-------------------------------------------------------
Nothing about the *placement* -- that is settled in `docs/ground_art.md` and
`docs/map_scenery.md`, and every number below comes out of `tools/puzzle.py`
and `tools/scene.py` unchanged.  What was missing for an *editor* is three
things:

1. **Layers you can draw one at a time.**  `tools/terrain.py` composites
   background -> ground -> TERRAIN into a single texture because the game view
   wants one texture.  An editor wants to toggle them, so each layer is
   rendered on its own, with transparency where it has nothing, and the
   compositing happens in the browser.  `render_layer()` is the one entry
   point; `LAYERS` names them in draw order.

2. **Tiles, because the maps are enormous.**  `Gulf` is 34,944 x 22,400 painted
   pixels.  Nothing may render a whole map at zoom 1.  A view is a set of
   `TILE`-pixel squares at an integer decimation `z`, and only the ones on
   screen are ever asked for.  `tile_rect()` is the whole of the geometry.

3. **Hit testing.**  A click is an art pixel; `pick()` turns it into the
   topmost thing drawn there -- a COVER sprite, a TERRAIN scene part, or the
   puzzle cell underneath -- by testing the sprite's own alpha, so clicking a
   gap between two tree branches selects what is behind them.

...and one thing that is not about drawing at all:

4. **Which side of the integrity line the thing you clicked is on.**
   `integrity.json` covers 155 `.DMap` files and 7 `ini/*.json` and **nothing
   else** -- not one `.pul`, `.scene`, `.ani`, `.dds`, `.Part` or `.pux`.  So
   the art of a map is entirely free to edit and the *grid* is not.
   `Integrity.status()` answers per logical path and the answer is attached to
   every inspector payload, always, rather than being a warning that appears
   only when something goes wrong.

WHAT MAY BE EDITED
------------------
    puzzle tile .dds        FREE      art of the painted ground
    scene / cover .dds      FREE      TERRAIN and COVER sprite frames
    backdrop tile .dds      FREE      the background planes
    map/ScenePart/*.Part    FREE      the human-readable scene part source
    map/Scene/*.scene       FREE      compiled scene objects
    map/puzzle/*.pul        FREE      which tile goes in which slot
    map/map/*.DMap          CHECKED   passability, dimensions, layer placement

Art edits go through exactly the same `/api/preview` -> `/api/stage` ->
`comod.py install` path as a `.dds` swap anywhere else in the viewer.  A DMap
edit is *possible* -- `stage_passability()` byte-patches the cell mask and
recomputes the row checksum `core/dmap.py` verifies -- but it is gated behind
an explicit acknowledgement, because tripping the integrity manifest by
accident is exactly what the project owner asked not to happen.

Read-only with respect to the game install: the only thing this module writes
is `mods/stage/`, which is `tools/comod.py`'s input, and comod.py is the only
sanctioned writer.

LIMITATIONS, STATED
-------------------
* **Staged art previews; staged placement does not.**  Textures are read
  through `StageFirst`, so a `.dds` you stage is on the map immediately. The
  `.pul`, `.scene` and `.ani` files are read straight from the install by
  `tools/puzzle.py` and `tools/scene.py`, so staging one of those shows up
  only after `comod.py install`. Nothing here silently pretends otherwise --
  the inspector reports which files it read from where.
* **Animated ground is frozen at frame 0**, as it is everywhere else in this
  project: a handful of `Puzzle<n>` keys name several frames. Scene and cover
  sprites *do* animate, driven by `?t=`.
* **`EFFECT` LAYERS DO DRAW; `SOUND` LAYERS DO NOT.**  This line used to say
  neither did and that "there is nothing to draw them as" -- stale on the
  effect half.  `tools/mapfx.js` is a WebGL renderer wired into this page
  (`MapFx.init/load`, a layer toggle, a glow option, an animation clock), and
  `/api/mapedit/effects` resolves each tag-19 record to a definition and a
  fractional cell.

  **WHAT IT NEEDS, AND WHY IT IS BLANK ON SOME INSTALLS.**  The names resolve
  through `ini/c3.wdb`'s EFFE section, which **41 of 45 installs ship** -- the
  other four, INCLUDING the owner's live CCO, have no definitions, so the
  endpoint answers `resolved: 0` and nothing draws.  That is reported, not
  hidden.  Sizes come from `ini/C3DMapEffect.lua` (34 of 45); without it every
  effect falls back to `DEFAULT_R`/`DEFAULT_DZ`, which the endpoint now says
  in `sizesAreDefaults` -- a default radius and a measured one are
  indistinguishable once they are numbers.

  SOUND is still undrawn and there genuinely is nothing to draw it as; this
  install ships 0 sound records in any case.

  **Replacing a stale limitation with a fresh one is the same defect** -- this
  paragraph has done it twice before -- so each half above is a measurement
  with its population, taken 2026-09-22.
* **`map/PuzzleSave/*.pux` (`TqTerrain`) DRAWS.** This line used to say it was
  undecoded and that the four maps using it had no ground art; both halves
  were stale. `dmap.read_pux_full` decodes the payload, `puzzle.py` builds the
  ground from its terrain table, and 135 of 7878's 470 maps and 4 of CCO's are
  `.pux` -- a population, not four.

  **AND ITS OVERLAY STACKS DRAW TOO.** The sentence that stood here said they
  did not -- "what a `.pux` still draws INCOMPLETELY is its overlay stack
  beyond layer 0" -- and I wrote it in `9ccb1868` while removing the previous
  stale claim from this same paragraph. It was already false when written:
  `PuzzleMap.composite_rgba` composites every layer of a stack bottom-first
  with the per-vertex 25-bit alpha mask (`dmap.pux_mask_field`, traced to the
  client's own `AlphaAt` at RVA `0x872A68`), `render_layer` calls it for every
  stacked index, and `tileset.py` ships the same stacks to the GPU path. Tiles
  carry up to 21 layers and 22,778 of 56,748 sampled tiles carry two or more.
  **Replacing a stale limitation with a fresh one is the same defect**, so
  this paragraph now states only what was measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                            # noqa: E402
import dmap as dmapmod                                   # noqa: E402
import otherdata                                         # noqa: E402
import puzzle as puzzlemod                               # noqa: E402
import scene as scenemod                                 # noqa: E402

try:                                                     # speed only
    import numpy as _np
except Exception:                                        # pragma: no cover
    _np = None

#: comod's stage tree, imported. A local copy here is how the Map Editor
#: would come to write where `comod install` does not read -- the same defect
#: that made Mod staging report "Nothing is staged" over a real stage tree.
def _stage_dir():
    import comod                                          # noqa: PLC0415
    return comod.STAGE


STAGE = _stage_dir()

#: Output edge of one map tile, in pixels. 256 keeps a full-zoom screen at a
#: few dozen requests and a whole-map view at a handful.
TILE = 256

#: The decimations a view may use. Powers of two so a tile boundary at one
#: level is a tile boundary at every coarser one, which is what lets the page
#: keep the coarse tiles on screen while the fine ones arrive.
ZOOMS = (1, 2, 4, 8, 16, 32, 64)

#: Draw order, furthest first. The page composites in exactly this order and
#: `render_layer` refuses anything not in it.
#:
#: **THESE ARE LAYER *KINDS*, NOT THE LAYERS A MAP HAS.**  A map's actual
#: layer list is `MapArt.layers()` and it is derived per map, because this
#: tuple is wrong in both directions on almost every map in the corpus.
#: Measured over the 150 drawable maps of the 156 the registry lists here:
#:
#:     0 backdrop planes    89 maps   -- "background" was a dead toggle
#:     2 or more planes     13 maps   -- one toggle drove up to 16 layers
#:     0 TERRAIN scenes    121 maps
#:     0 COVER sprites      22 maps
#:     0 painted ground      1 map    (spirit01_new: two planes and nothing else)
#:
#: **15 of the 150 have exactly the five this tuple names.**  The other 135
#: were shown controls that changed nothing, or one checkbox over sixteen
#: separately-drawn planes.  625 real layers were rendered as 750 rows.
LAYERS = ("background", "ground", "terrain", "cover", "interactive",
          "passability")

#: A `background` layer may name ONE plane: `background:3` is plane index 3.
#: The bare name still means "every plane", which is what the whole-map
#: renderer and the CLI use and what every existing caller passes.
LAYER_SEP = ":"

#: THE PUZZLE ROWS ARE NUMBERED, NOT NAMED, AND THE REASON IS MEASURED.
#:
#: These two titles used to read "Background planes" and "Base puzzle (painted
#: ground)" and they were INVERTED. On `Clients/7878`, `sary02_new`:
#:
#:   * `background` draws the `.pul` planes and the FIRST -- `sary02.pul` --
#:     is the map's MAIN GROUND. Rendered alone at cell (178,170) it is a
#:     detailed stone plaza: paving, steps, a carved medallion, a monument.
#:   * `ground` draws `sary02.pux`, magic `TqTe` (TqTerrain), an OVERLAY and a
#:     sparse one: **median 51.6% of its tiles are empty across 136 maps, and
#:     SEVEN are 100% empty** (`cho-room` 875/875, `waror_new` 504/504,
#:     `climb` 168/168, `2024love01` 560/560). A map whose ground were the
#:     `.pux` could not draw at all.
#:
#: The owner read the panel, concluded the tool had five layers ever, and went
#: hunting missing ground in the wrong layer. So did I: two of my own
#: measurements counted `.pux` absence as "no ground art" while the `.pul` was
#: supplying it.
#:
#: A first fix renamed them "Painted ground planes" / "TqTerrain overlay". The
#: owner rejected THAT too, from evidence I did not have -- they toggled the
#: pair and watched: *"they are clearly 2 parts of the same puzzle, not
#: separate parts."* So the rows are `part 1..N` in draw order and the FILE is
#: carried in `path`. Ground and overlay are roles I inferred from one map; a
#: number and a filename are what is known, and a wrong role name inverts
#: silently -- which is this constant's entire history.
#:
#: The bare keys below remain for the whole-map renderer and the CLI.
LAYER_TITLE = {
    "background": "Background planes",
    "ground": "Base puzzle (painted ground)",
    "terrain": "TERRAIN scene objects",
    "cover": "COVER sprites",
    "interactive": "INTERACTIVE sprites",
    "passability": "Passability grid",
}

LAYER_HELP = {
    "background": "A background puzzle plane from the .DMap's trailing "
                  "section, with its own parallax. What turns the void around "
                  "an island into sea and sky.",
    "ground": "The painted ground: map/puzzle/*.pul cut into PuzzleGridSize "
              "tiles, each one a .pux stack of terrain rows.",
    "terrain": "TERRAIN layers -- map/Scene objects. These carry their own "
               "passability and it REPLACES the cell grid underneath them.",
    "cover": "COVER layers -- sprites the game draws in front of the player.",
    "interactive": "The .DMap's SECOND record list (v1006 only), which "
                   "nothing drew until 2026-09-15. It is the .OtherData "
                   "InteractiveLayer: the declared count matches the tag-4 "
                   "records here on 152 of 152 v1006 maps. 21,598 sprites "
                   "across 118 maps. The owner found it as walkable "
                   "platforms with no art on gsjx03_new.",
    "passability": "Green: walkable in the .DMap's own grid. Cyan: walkable "
                   "only because a TERRAIN layer says so. Red: the grid says "
                   "yes and a TERRAIN says no.",
}


def layer_parts(layer: str) -> tuple[str, Optional[int]]:
    """`"background:2"` -> `("background", 2)`; `"ground"` -> `("ground", None)`.

    Returns `("", None)` for anything that is not a layer name at all, so a
    caller can validate with one call and never has to parse the string
    itself. The index is only meaningful for `background`; a suffix on any
    other kind is rejected rather than ignored, because a silently ignored
    selector renders the WRONG layer and looks like a render bug.
    """
    kind, sep, rest = layer.partition(LAYER_SEP)
    if kind not in LAYERS:
        return "", None
    if not sep:
        return kind, None
    if kind != "background" or not rest.isdigit():
        return "", None
    return kind, int(rest)


def valid_layer(layer: str) -> bool:
    """Whether `render_layer` will accept this name. The HTTP route asks this
    rather than `layer in LAYERS`, which would refuse every per-plane id."""
    return layer_parts(layer)[0] != ""

#: Passability overlay colours, RGBA. Deliberately not naturalistic -- this is
#: a data view laid over art, and it has to read as data.
PASS_BASE = (74, 208, 120, 90)          # walkable in the DMap's own grid
PASS_SCENE_OPEN = (64, 240, 232, 110)   # walkable only because a TERRAIN says so
PASS_SCENE_BLOCK = (232, 96, 64, 110)   # blocked by a TERRAIN over walkable grid
PASS_BLOCKED = (0, 0, 0, 0)             # blocked everywhere: draw nothing


# ---------------------------------------------------------------------------
# integrity.json
# ---------------------------------------------------------------------------

class Integrity:
    """`$ROOT/integrity.json`, as a question you can ask about a path.

    **162 entries, but only 143 distinct files** -- VERIFIED here rather than
    assumed: 136 `.DMap` and 7 `ini/*.json`. The 19 extra entries are repeats,
    and they are repeats for a reason: a map with several `GameMap.json`
    `DocumentId`s is listed once per id, always with the same hash
    (`lineup.DMap` appears 8 times, `grocery` and `p-arena` 3 each; 0 paths
    carry two different hashes). The 136 `.DMap` listed are exactly the 136
    that ship -- none missing, none extra.

    The 64-bit hash algorithm is unknown (`docs/dll_analysis.md` §9 -- the
    string `integrity` appears in none of the six clean binaries), so this
    reports *membership*, never "your edit still matches". Membership is the
    fact that matters: a file in the manifest is one the client checks, and a
    file outside it is one it does not.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else None
        self.reason = ""
        self.row_count = 0
        self.entries: dict[str, str] = {}
        if self.root is None:
            self.reason = "no game install"
            return
        p = self.root / "integrity.json"
        if not p.is_file():
            self.reason = f"{p} is missing"
            return
        try:
            rows = json.loads(p.read_text("utf-8", errors="replace"))
        except Exception as e:                           # noqa: BLE001
            self.reason = f"integrity.json did not parse: {e}"
            return
        for r in rows or []:
            self.row_count += 1
            f = str(r.get("file", "")).replace("\\", "/").lstrip("/").lower()
            if f:
                self.entries[f] = str(r.get("hash", ""))

    def __len__(self) -> int:
        return len(self.entries)

    def checked(self, logical: str) -> bool:
        return _norm(logical) in self.entries

    def status(self, logical: str) -> dict:
        """The badge every inspector panel carries, for any logical path."""
        key = _norm(logical)
        hit = key in self.entries
        return {
            "path": key,
            "checked": hit,
            "hash": self.entries.get(key, ""),
            "why": ("integrity.json lists this file, so the client verifies it. "
                    "Changing it is a deliberate act, not a texture swap."
                    if hit else
                    "integrity.json does not list this file. Editing it is "
                    "outside the manifest entirely."),
            "manifestFiles": len(self.entries),
            "manifestRows": self.row_count,
        }


def _norm(p: str) -> str:
    return str(p).replace("\\", "/").lstrip("/").lower()


# ---------------------------------------------------------------------------
# tile geometry
# ---------------------------------------------------------------------------

def tile_rect(tx: int, ty: int, z: int, *, tile: int = TILE) -> tuple[int, int, int, int]:
    """The painted-image pixel rectangle one tile covers.

    A tile is always `tile` output pixels square; at decimation `z` it
    therefore covers `tile * z` art pixels. Tile (0,0) starts at the art's own
    origin, so a tile boundary at z=1 is a tile boundary at every z.
    """
    span = tile * int(z)
    x0 = int(tx) * span
    y0 = int(ty) * span
    return (x0, y0, x0 + span, y0 + span)


def tile_grid(px_w: int, px_h: int, z: int, *, tile: int = TILE) -> tuple[int, int]:
    """How many tiles cover a `px_w` x `px_h` image at decimation `z`."""
    span = tile * int(z)
    return ((px_w + span - 1) // span, (px_h + span - 1) // span)


def fit_zoom(px_w: int, px_h: int, view_w: int, view_h: int) -> int:
    """The coarsest-but-one zoom that puts the whole map in a viewport.

    Returned as a member of `ZOOMS`, because a decimation the renderer cannot
    do is not a zoom level. This is what the editor's "fit map" button uses,
    and it is arithmetic rather than a DOM measurement so it is testable.
    """
    if view_w <= 0 or view_h <= 0:
        return ZOOMS[-1]
    need = max(px_w / view_w, px_h / view_h, 1e-9)
    for z in ZOOMS:
        if z >= need:
            return z
    return ZOOMS[-1]


# ---------------------------------------------------------------------------
# raster helpers
# ---------------------------------------------------------------------------

def encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    return scenemod.encode_png_rgba(width, height, rgba)


def _blank(w: int, h: int) -> bytearray:
    return bytearray(w * h * 4)


def _decimate_rgba(rgba: bytes, w: int, h: int, scale: int):
    if scale == 1:
        return rgba, w, h
    return scenemod._decimate(rgba, w, h, scale)          # noqa: SLF001


def _blit_rgba(dst: bytearray, dw: int, dh: int, src, sw: int, sh: int,
               x: int, y: int) -> bool:
    """Source-over onto an RGBA buffer. True when anything was touched."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return False
    scenemod._blit_rgba(dst, dw, dh, src, sw, sh, x, y)   # noqa: SLF001
    return True


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------

@dataclass
class PlacedInfo:
    """A `scene.Placed` with the extra facts the inspector needs.

    Kept beside the `Placed` rather than inside it so `tools/scene.py` stays
    the pure placement module the game view imports.
    """

    placed: object
    index: int
    frames: list = field(default_factory=list)      # logical .dds paths
    size: tuple[int, int] = (0, 0)                  # sprite pixels, frame 0
    origin: tuple[float, float] = (0.0, 0.0)        # top-left in art pixels
    frame_count: int = 0

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.origin[0], self.origin[1],
                self.origin[0] + self.size[0], self.origin[1] + self.size[1])


class MapArt:
    """One map, resolved once and cached: art, scenery, passability.

    Everything is lazy. Opening the picker must not parse 1.5 million cells,
    and turning on the passability layer for `Gulf` must not be paid for by
    someone who only wanted to look at `arena`.
    """

    def __init__(self, name: str, lib: "MapEditor") -> None:
        self.name = name
        self.lib = lib
        self.reason = ""
        self.pm = lib.puzzles.get(name)
        if self.pm is None:
            self.reason = lib.puzzles.reason
        self._dmap_header = None
        self._dmap_cells = None
        self._scenery: Optional[object] = None
        self._scenery_why = ""
        self._backdrops: Optional[list] = None
        self._items: Optional[dict[str, list[PlacedInfo]]] = None
        #: `({dmap_index: (r,g,b,a)}, {id(placed): dmap_index})` from the
        #: `.OtherData` sidecar -- see `cover_tints()`.
        self._tints: Optional[tuple[dict, dict]] = None
        #: `{late_record_index: (r,g,b,a)}` -- see `late_tints()`. A SECOND
        #: index space; keeping it in its own cache is what stops the two
        #: being confused.
        self._late_tints: Optional[dict] = None
        #: `(ground_tint, {plane: tint})` -- see `puzzle_tints()`.
        self._puzzle_tints: Optional[tuple] = None
        self._walk: Optional[tuple[bytes, bytes, object]] = None
        #: Which file answered the last parse -- "staged", "archive"
        #: or "loose". Recorded rather than re-derived, because the
        #: caller that most needs it is the one reporting to a person.
        self._dmap_origin: Optional[str] = None
        self._tile_rgba: dict[int, Optional[bytes]] = {}
        self._sprite_cache: dict[tuple, tuple] = {}
        #: `(manifest, bundle)` from `tileset()`. Up to ~47 MB, so it is built
        #: once per map and dropped by the same invalidations as the art.
        self._tileset: Optional[tuple[dict, bytes]] = None
        self._lock = threading.RLock()

    # -- the .DMap ---------------------------------------------------------

    @property
    def dmap_path(self) -> Optional[Path]:
        root = self.lib.root
        if root is None:
            return None
        d = root / "map" / "map"
        if not d.is_dir():
            return None
        for p in d.iterdir():
            if p.suffix.lower() == ".dmap" and p.stem.lower() == self.name.lower():
                return p
        return None

    @property
    def archive_path(self) -> Optional[Path]:
        """The `map/map/<name>.7z` this map ships in, if there is one.

        From 5517 on a map ships as an archive and the loose `.DMap` is the
        exception; every one of 7878's 730 is archive-only. Before this the
        editor listed all 730 with width/height/area all 0, because
        `dmap_path` found nothing and every reader downstream received None.
        """
        root = self.lib.root
        if root is None:
            return None
        p = root / "map" / "map" / f"{self.name}.7z"
        return p if p.is_file() else None

    def _archive_dmap_bytes(self) -> Optional[bytes]:
        """The `.DMap` inside this map's archive, decompressed in memory.

        Standard library only -- these are LZMA1 and LZMA2 with plain headers.
        `archive_bytes` verifies its output against the CRC32 the archive
        carries, so this returns correct bytes or None, never plausible wrong
        ones.
        """
        a = self.archive_path
        if a is None:
            return None
        try:
            return dmapmod.archive_bytes(a)
        except Exception:
            return None

    @property
    def dmap_logical(self) -> str:
        """The map's logical `.DMap` path, by NAME.

        **Not derived from the loose file.** It used to be
        `f"map/map/{dmap_path.name}"`, which is the empty string for any map
        that ships only as a `.7z` -- 113 of 6609's and every one of 7878's --
        so staging one wrote to the stage root. That was unreachable while the
        writer refused an absent loose file, and became reachable the moment
        the writer started basing edits on what the registry names.
        """
        return f"map/map/{self.name}.DMap"

    def read_path(self) -> Optional[Path]:
        """The staged-or-loose `.DMap` path.

        **This is NOT necessarily the map the editor reads**, and the name
        used to claim it was. Where an archive ships beside the loose file the
        archive wins -- see `dmap_source`, which is what `header()` and
        `cells()` actually ask.

        The docstring this replaces said *"a staged copy wins, exactly as a
        loose file wins over an archive in the game itself"*.
        `docs/map_stage_precedence.md` established that the second clause is
        false for maps: `TqFOpen`'s loose-over-archive rule is about one
        logical path with two backings, and `map/map/x.DMap` is a different
        filename from the `map/map/x.7z` the registry names, so the rule never
        arises. A staged copy still wins, and that half stands.
        """
        p = self.dmap_path
        if p is None:
            return None
        staged = self.lib.stage / f"map/map/{p.name}"
        return staged if staged.is_file() else p

    def dmap_source(self) -> tuple[Optional[Path], Optional[bytes], str]:
        """`(path, data, origin)` for the `.DMap` the editor must parse.

        Precedence is **staged > archive > loose**, and the middle term is the
        correction. `header()`/`cells()` used to take the loose file whenever
        one existed and reach for the archive only as a fallback, so every map
        carrying a previous client's leftover was parsed, rendered and
        inspected as that older map:

            build  loose/archive pairs that DISAGREE
            5165    0 (ships no archives)        5517   3
            6090   74                            6271  75
            6716   75                            7878   0 (archive-only)

        Of 6090's 74, **51 differ in layer count and 19 in the cell grid** --
        scenery placement and passability, the latter being the surface COMod
        stages edits to. `boa` parsed to 0 layers from the leftover and 62
        from the archive.

        `origin` is returned rather than inferred so a caller can say which
        file it is showing. `map_stage_precedence.md`'s second proposal asks
        for exactly that, on every row, always: *a label that appears only on
        disagreement is a label nobody learns to read.*
        """
        p = self.dmap_path
        if p is not None:
            staged = self.lib.stage / f"map/map/{p.name}"
            if staged.is_file():
                return staged, None, "staged"
        if self.archive_path is not None:
            raw = self._archive_dmap_bytes()
            if raw is not None:
                return self.archive_path, raw, "archive"
            # An archive that will not decompress is not a reason to silently
            # serve the leftover instead -- say which one answered.
            if p is not None and p.is_file():
                return p, None, "loose (archive would not decompress)"
            return None, None, "archive unreadable"
        if p is not None and p.is_file():
            return p, None, "loose"
        return None, None, "no .DMap"

    @property
    def staged(self) -> bool:
        p = self.dmap_path
        return p is not None and (self.lib.stage / f"map/map/{p.name}").is_file()

    def invalidate_grid(self) -> None:
        """Forget the parsed cell grid -- called after staging an edit."""
        with self._lock:
            self._dmap_header = None
            self._dmap_cells = None
            self._dmap_origin = None
            self._walk = None
            self._scenery = None
            self._items = None
            self._tileset = None

    def invalidate_art(self) -> None:
        """Forget every decoded texture -- called after a `.dds` is staged or
        unstaged, because the same file may back a hundred tiles."""
        with self._lock:
            self._tile_rgba.clear()
            self._sprite_cache.clear()
            self._items = None
            self._tileset = None
            # The backdrop planes carry their own decoded-tile cache inside
            # `puzzle.PuzzleMap`, and they are rebuilt from scratch, so
            # dropping the list is enough.
            self._backdrops = None
            self.lib.sprites._frames.clear()          # noqa: SLF001

    def header(self):
        """The `.DMap` without its cell grid -- cheap even on `Gulf`."""
        with self._lock:
            if self._dmap_header is None:
                p, raw, origin = self.dmap_source()
                if p is None:
                    return None
                self._dmap_origin = origin
                self._dmap_header = dmapmod.parse(
                    p, want_cells=False, verify=False, data=raw)
            return self._dmap_header

    def cells(self):
        """The `.DMap` *with* its cell grid. 1.5M cells on `Gulf`, so this is
        only ever reached by the passability layer and by an inspector click."""
        with self._lock:
            if self._dmap_cells is None:
                p, raw, origin = self.dmap_source()
                if p is None:
                    return None
                self._dmap_origin = origin
                self._dmap_cells = dmapmod.parse(
                    p, want_cells=True, verify=False, data=raw)
            return self._dmap_cells

    # -- scenery -----------------------------------------------------------

    def scenery(self):
        with self._lock:
            if self._scenery is None:
                d = self.header()
                if d is None:
                    self._scenery_why = "no .DMap"
                    self._scenery = scenemod.Scenery(self.name)
                else:
                    # v1006's SECOND record list. `core/dmap.py` has parsed it
                    # for months as `late_layers`; every consumer passed only
                    # `d.layers`, so 21,598 cover sprites across 118 maps were
                    # decoded and dropped on the floor. See
                    # `scene.Scenery.late_covers`.
                    self._scenery = scenemod.gather(
                        d.layers, self.lib.scenes,
                        getattr(d, "late_layers", ()) or ())
                    self._scenery.name = self.name
            return self._scenery

    def backdrops(self):
        with self._lock:
            if self._backdrops is None:
                try:
                    self._backdrops = self.lib.puzzles.backdrops(self.name)
                except Exception:                        # noqa: BLE001
                    self._backdrops = []
            return self._backdrops

    # -- placed sprites, with their pixel rectangles ------------------------

    def items(self, kind: str) -> list[PlacedInfo]:
        """Every placed `scene` or `cover`, in painter's order, with the sprite
        rectangle it actually occupies.

        Decoding every sprite of `newplain` (1,083 covers) is not free, so it
        is done once per map and kept. It has to happen anyway -- both drawing
        and hit-testing need the frames.
        """
        with self._lock:
            if self._items is None:
                self._items = {"scene": [], "cover": [], "late": []}
                sc = self.scenery()
                if self.pm is not None:
                    for k, seq in (("scene", sc.sorted_scenes()),
                                   ("cover", sc.sorted_covers()),
                                   ("late", sc.sorted_late_covers())):
                        for i, p in enumerate(seq):
                            self._items[k].append(self._describe(p, i))
            return self._items.get(kind, [])

    def cover_tints(self, enabled: bool = True) -> tuple[dict, dict]:
        r"""`({dmap_index: (r,g,b,a)}, {id(placed): dmap_index})`.

        The `.OtherData` sidecar carries a per-cover `Alpha/Red/Green/Blue`
        that nothing in this project read until `tools/otherdata.py`, so every
        cover was drawn at full brightness and full opacity. It is two
        different effects on the two maps the owner was looking at -- colour
        on `sary02_new`, real alpha on `2024thx_new`.

        **NO SHIPPED CLIENT DRAWS THIS, AND THIS DOCSTRING USED TO IMPLY ONE
        DID.** It called the tint the owner's *"almost like theres a
        transparency effect thats not being utilised"*, which reads as a
        client effect we were missing. The Director of RE checked all 38
        installs, pinned by `version.dat`, 1064..7952: no standalone `Alpha%d`
        / `Red%d` / `Green%d` / `Blue%d` in any of them, and no
        `GetPrivateProfileSection` in any modern build to read them unnamed
        (`docs/otherdata_percover_tint_inert_2026-09-16.md`, `09ad1411`). So
        with `enabled` true this draws what the MAP declares, not what the
        GAME shows. Keep that distinction in anything user-facing.

        The binding below is NOT affected: `MapObjIndex%d` IS read, 7065+.

        **THE SECOND DICT IS NOT OPTIONAL.** `MapObjIndexK` indexes the
        `.DMap` cover order; `items("cover")` walks `sorted_covers()` and
        `PlacedInfo.index` is the position in THAT list. Keying the tint by
        `PlacedInfo.index` would hand covers the wrong tint and still render a
        plausible picture.

        **The two orders COINCIDE as of 2026-09-16** -- covers paint in `.DMap`
        list order now (`scene.cover_paint_order`), where 265 of 268 sorted to
        a different position on `sary02_new` before. The identity key stays:
        their agreeing today is a property of the cover order, not of the
        binding, and `tests/test_otherdata_cover_tint.py` holds the tripwire
        for the day they diverge again.

        Set `MAPEDIT_COVER_TINT=0` to render as we did before the sidecar was
        read; the gate uses it to compare the two.
        """
        if not enabled:
            return ({}, {})
        with self._lock:
            if self._tints is None:
                tints: dict = {}
                index: dict = {}
                if os.environ.get("MAPEDIT_COVER_TINT", "1") != "0":
                    p = (otherdata.sidecar_path(self.lib.root, self.name)
                         if self.lib.root else None)
                    if p is not None:
                        tints = otherdata.cover_tints(p)
                    if tints:
                        sc = self.scenery()
                        if sc is not None:
                            index = otherdata.dmap_index_of(sc.covers)
                self._tints = (tints, index)
            return self._tints

    def puzzle_tints(self, enabled: bool = True) -> tuple:
        r"""`(ground_tint, {plane_index: tint})` for the painted surfaces.

        TWO DIFFERENT SURFACES FROM TWO DIFFERENT SECTIONS, which is why this
        returns a pair rather than one value:

            TerrainLayer0.Puzzle*  ->  the MAIN GROUND  (.pul + .pux)
            SceneLayerN.Puzzle*    ->  BACKDROP PLANE N

        See `otherdata.ground_puzzle_tint` for the RVAs. The maps that looked
        like they declared conflicting values were declaring values for
        different surfaces all along.
        """
        if not enabled:
            return (None, {})
        with self._lock:
            if getattr(self, "_puzzle_tints", None) is None:
                g, pl = None, {}
                if os.environ.get("MAPEDIT_COVER_TINT", "1") != "0":
                    p = (otherdata.sidecar_path(self.lib.root, self.name)
                         if self.lib.root else None)
                    if p is not None:
                        g = otherdata.ground_puzzle_tint(p)
                        pl = otherdata.plane_puzzle_tints(p)
                self._puzzle_tints = (g, pl)
            return self._puzzle_tints

    def late_tints(self, enabled: bool = True) -> dict:
        r"""`{late_record_index: (r,g,b,a)}` for the INTERACTIVE layer.

        SEPARATE FROM `cover_tints` BECAUSE THE INDEX SPACES OVERLAP.
        `TerrainLayer*` indexes the main cover list by position (0..2969 on
        gsjx03_new); `InteractiveLayer*` indexes the v1006 late record list by
        the record's own `index` (14..487 on the same map). One dict keyed on
        a bare integer would hand a main cover's tint to a late one and render
        a plausible, wrong picture.

        The key here is `Placed.layer_index`, which `scene.gather` copies
        straight off the record. Verified identical to the record index set on
        gsjx03_new (399 of 399), and every one of the 103 indices the sidecar
        declares is present in it.
        """
        if not enabled:
            return {}
        with self._lock:
            if getattr(self, "_late_tints", None) is None:
                out: dict = {}
                if os.environ.get("MAPEDIT_COVER_TINT", "1") != "0":
                    p = (otherdata.sidecar_path(self.lib.root, self.name)
                         if self.lib.root else None)
                    if p is not None:
                        out = otherdata.interactive_tints(p)
                self._late_tints = out
            return self._late_tints

    def _describe(self, p, index: int) -> PlacedInfo:
        cache = self.lib.sprites
        frames = cache.frames(p.ani, p.title)
        paths = cache.frame_paths(p.ani, p.title)
        w = h = 0
        if frames:
            w, h = frames[0][0], frames[0][1]
        ox, oy = p.sprite_origin(self.pm)
        return PlacedInfo(placed=p, index=index, frames=paths, size=(w, h),
                          origin=(ox, oy), frame_count=len(frames))

    # -- passability -------------------------------------------------------

    def passability(self):
        """`(base, overlaid, stats)` -- the DMap's own grid, the grid with the
        TERRAIN layers composited on, and what the compositing changed.

        The overlay rule is `docs/map_scenery.md` §2-3: the scene cell
        *replaces* the grid cell, and both directions are used.
        """
        with self._lock:
            if self._walk is None:
                d = self.cells()
                if d is None:
                    return None
                base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
                sc = self.scenery()
                over, st = scenemod.apply_passability(base, d.width, d.height,
                                                      sc.scenes)
                self._walk = (base, over, st)
            return self._walk

    def cell_state(self, x: int, y: int) -> dict:
        d = self.cells()
        if d is None or not (0 <= x < d.width and 0 <= y < d.height):
            return {"inside": False}
        mask, surface, elev = d.cell(x, y)
        walk = self.passability()
        i = y * d.width + x
        base = bool(walk[0][i]) if walk else (mask == 0)
        over = bool(walk[1][i]) if walk else base
        return {"inside": True, "x": x, "y": y, "mask": mask,
                "surface": surface, "elevation": elev,
                "baseWalkable": base, "walkable": over,
                "fromScenery": bool(over != base)}

    # -- rendering ---------------------------------------------------------

    def _tile_pixels(self, idx: int) -> Optional[bytes]:
        """One puzzle tile as `grid*grid` RGBA. `PuzzleMap._tile_pixels`
        flattens onto VOID, which is right for the game's one-texture ground
        and wrong for a layer that has to let the background show through."""
        if idx in self._tile_rgba:
            return self._tile_rgba[idx]
        # A `.pux` tile is a STACK of terrain rows, and the editor gets it
        # from the same composite the still renderer uses. Without this the
        # index is synthetic, `tile_path` finds nothing, and every stacked
        # tile renders as a HOLE -- which on a map whose tiles are mostly
        # stacks is indistinguishable from the map not loading at all.
        if self.pm is not None and idx in getattr(self.pm, "stacks", ()):
            out = self.pm.composite_rgba(idx)
            self._tile_rgba[idx] = out
            return out
        out = None
        path = self.pm.tile_path(idx) if self.pm else ""
        if path and self.lib.assets is not None:
            try:
                import dds                               # noqa: PLC0415
                w, h, rgba = dds.decode(self.lib.assets.read(path))
                out = _resample_rgba(rgba, w, h, self.pm.grid)
            except Exception:                            # noqa: BLE001
                out = None
        self._tile_rgba[idx] = out
        return out

    def render_ground(self, rect, scale: int, *,
                      tint: bool = True) -> tuple[int, int, bytes]:
        """The painted ground over `rect`, as RGBA. Empty slots and off-image
        are transparent, not VOID."""
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = _blank(ow, oh)
        if self.pm is None:
            return ow, oh, bytes(buf)
        pm = self.pm
        g = pm.grid
        stride4 = 4 * scale
        for oy in range(oh):
            py = y0 + oy * scale
            j, ty = divmod(py, g)
            if not (0 <= j < pm.pul_h):
                continue
            rowbase = oy * ow * 4
            ox = 0
            px = x0
            while ox < ow:
                i, tx = divmod(px, g)
                room = (g - tx + scale - 1) // scale
                n = min(room, ow - ox)
                if n <= 0:
                    break
                if 0 <= i < pm.pul_w:
                    idx = pm.tiles[j * pm.pul_w + i]
                    if idx != puzzlemod.EMPTY:
                        src = self._tile_pixels(idx)
                        if src is not None:
                            s0 = (ty * g + tx) * 4
                            s1 = (ty * g + tx + (n - 1) * scale + 1) * 4
                            seg = src[s0:s1]
                            o = rowbase + ox * 4
                            if scale == 1:
                                buf[o:o + n * 4] = seg
                            else:
                                for c in range(4):
                                    buf[o + c:o + n * 4:4] = seg[c::stride4][:n]
                ox += n
                px += n * scale
        # The `.OtherData` GROUND tint -- `TerrainLayer0.Puzzle*`, ONE section
        # for the whole ground, traced to `ApplyPuzzleTint` (0x89E890) and its
        # single caller. NOT `SceneLayer`, which tints the backdrop planes.
        g, _ = self.puzzle_tints(tint)
        if g is not None:
            for i in range(0, len(buf), 4):
                if not buf[i + 3]:
                    continue
                buf[i] = (buf[i] * g[0]) // 255
                buf[i + 1] = (buf[i + 1] * g[1]) // 255
                buf[i + 2] = (buf[i + 2] * g[2]) // 255
                buf[i + 3] = (buf[i + 3] * g[3]) // 255
        return ow, oh, bytes(buf)

    def render_background(self, rect, scale: int, *,
                          plane: Optional[int] = None,
                          tint: bool = True) -> tuple[int, int, bytes]:
        """The background planes behind `rect`, furthest first, as RGBA.

        `puzzle.Backdrop.render_tiled` produces RGB over VOID; VOID is turned
        back into transparency here so a map with no background plane is a
        transparent layer rather than a black one.

        `plane` selects ONE plane by index. `None` keeps the old behaviour --
        every plane, in order -- which is what the whole-map renderer and the
        CLI want. Per-plane exists because a map can carry sixteen of them
        (`star10`) and "background" as a single toggle is then a control over
        sixteen different pieces of art at once.
        """
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = _blank(ow, oh)
        void = bytes(puzzlemod.VOID)
        all_planes = self.backdrops()
        # ENUMERATED so each plane keeps its OWN index: `SceneLayerN` tints
        # backdrop plane N, and the values differ per plane -- `sdragon01_new`
        # carries four. Collapsing them into one map-wide tint was the build
        # this stopped one step short of. Selecting a single plane must not
        # renumber it, so the index travels with the object.
        idx_planes = list(enumerate(all_planes))
        if plane is not None:
            idx_planes = ([(plane, all_planes[plane])]
                          if 0 <= plane < len(all_planes) else [])
        _, plane_tints = self.puzzle_tints(tint)
        for pidx, b in idx_planes:
            try:
                bw, bh, brgb = b.render_tiled(tuple(rect), scale=scale)
            except Exception:                            # noqa: BLE001
                continue
            t = plane_tints.get(pidx)
            for i in range(0, min(bw * bh, ow * oh)):
                s = i * 3
                if brgb[s:s + 3] == void:
                    continue
                d = i * 4
                if t is None:
                    buf[d:d + 3] = brgb[s:s + 3]
                else:
                    # RGB only: a backdrop plane is opaque where it is not
                    # VOID, and VOID is skipped above, so there is no source
                    # alpha to multiply. `PuzzleAlpha` on a PLANE is therefore
                    # recorded and NOT applied here -- see the gate.
                    buf[d] = (brgb[s] * t[0]) // 255
                    buf[d + 1] = (brgb[s + 1] * t[1]) // 255
                    buf[d + 2] = (brgb[s + 2] * t[2]) // 255
                buf[d + 3] = 255
        return ow, oh, bytes(buf)

    def render_sprites(self, kind: str, rect, scale: int, *,
                       time_ms: int = 0,
                       tint: bool = True) -> tuple[int, int, bytes]:
        """TERRAIN or COVER sprites over `rect`, as RGBA.

        Only the sprites whose own rectangle intersects the tile are touched,
        which is what makes a 1,083-cover map tolerable at zoom 1.
        """
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = _blank(ow, oh)
        if self.pm is None:
            return ow, oh, bytes(buf)
        # Covers carry the `.OtherData` tint; scene parts do not -- the
        # sections that bind to the cover list are the only ones whose index
        # space is settled. See `cover_tints()`.
        tints, dmap_ix = (self.cover_tints(tint)
                          if kind == "cover" else ({}, {}))
        # The INTERACTIVE layer has its OWN sidecar sections and its own index
        # space; `cover_tints` would be the wrong dict, not merely an empty one.
        ltints = self.late_tints(tint) if kind == "late" else {}
        for it in self.items(kind):
            rx0, ry0, rx1, ry1 = it.rect
            if rx1 <= x0 or rx0 >= x1 or ry1 <= y0 or ry0 >= y1:
                continue
            p = it.placed
            fr = self.lib.sprites.frames(p.ani, p.title)
            if not fr:
                continue
            n = 0
            if p.frame_interval > 0 and len(fr) > 1 and time_ms:
                n = time_ms // p.frame_interval
            sw, sh, rgba = fr[n % len(fr)]
            # The `.OtherData` tint, bound by the .DMap index and NOT by
            # `it.index` -- see `cover_tints()`. It is part of the cache key
            # because two covers can share a sprite and differ only in tint;
            # keying without it would serve the first one's colour to the
            # second, which renders as a plausible picture and is wrong.
            if kind == "late":
                tint = ltints.get(p.layer_index) if ltints else None
            else:
                tint = tints.get(dmap_ix.get(id(p), -1)) if tints else None
            key = (p.ani, p.title, n % len(fr), scale, tint)
            hit = self._sprite_cache.get(key)
            if hit is None:
                hit = _decimate_rgba(rgba, sw, sh, scale)
                if tint is not None:
                    hit = (otherdata.apply_tint(hit[0], tint), hit[1], hit[2])
                if len(self._sprite_cache) > 4096:
                    self._sprite_cache.clear()
                self._sprite_cache[key] = hit
            drgba, dw, dh = hit
            _blit_rgba(buf, ow, oh, drgba, dw, dh,
                       int(round((it.origin[0] - x0) / scale)),
                       int(round((it.origin[1] - y0) / scale)))
        return ow, oh, bytes(buf)

    def render_passability(self, rect, scale: int) -> tuple[int, int, bytes]:
        """A dot or a diamond on every cell, coloured by how it got its state.

        This is the layer that makes the editor an editor rather than a
        viewer: `docs/map_scenery.md`'s whole point is that the cell grid and
        the scene layers disagree, and you cannot edit what you cannot see.
        """
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = _blank(ow, oh)
        pm = self.pm
        walk = self.passability()
        if pm is None or walk is None:
            return ow, oh, bytes(buf)
        base, over, _ = walk
        d = self.cells()
        # The cell range whose diamonds can touch this rectangle: the corners
        # of the pixel rect, inverted through the placement rule, plus one
        # cell of slack for the half-cell phase.
        cs = [pm.px_to_cell(x, y) for x, y in
              ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        cx0 = max(0, int(min(c[0] for c in cs)) - 2)
        cx1 = min(d.width - 1, int(max(c[0] for c in cs)) + 2)
        cy0 = max(0, int(min(c[1] for c in cs)) - 2)
        cy1 = min(d.height - 1, int(max(c[1] for c in cs)) + 2)
        half_w = max(1, 32 // scale)
        half_h = max(1, 16 // scale)
        for cy in range(cy0, cy1 + 1):
            rowi = cy * d.width
            for cx in range(cx0, cx1 + 1):
                i = rowi + cx
                b, o = base[i], over[i]
                if not b and not o:
                    continue
                col = (PASS_SCENE_OPEN if (o and not b) else
                       PASS_SCENE_BLOCK if (b and not o) else PASS_BASE)
                px, py = pm.cell_px(cx, cy)
                ox = int((px - x0) / scale)
                oy = int((py - y0) / scale)
                _diamond(buf, ow, oh, ox, oy, half_w, half_h, col)
        return ow, oh, bytes(buf)

    def render_layer(self, layer: str, rect, scale: int, *,
                     time_ms: int = 0,
                     tint: bool = True) -> tuple[int, int, bytes]:
        kind, plane = layer_parts(layer)
        if not kind:
            raise ValueError(f"unknown layer {layer!r}; have {', '.join(LAYERS)}"
                             " (and background:N for one plane)")
        if kind == "background":
            return self.render_background(rect, scale, plane=plane, tint=tint)
        layer = kind
        if layer == "ground":
            return self.render_ground(rect, scale, tint=tint)
        if layer == "terrain":
            return self.render_sprites("scene", rect, scale, time_ms=time_ms)
        if layer == "cover":
            return self.render_sprites("cover", rect, scale, time_ms=time_ms,
                                       tint=tint)
        if layer == "interactive":
            return self.render_sprites("late", rect, scale, time_ms=time_ms,
                                       tint=tint)
        return self.render_passability(rect, scale)

    def tile_png(self, layer: str, tx: int, ty: int, z: int, *,
                 time_ms: int = 0, tile: int = TILE,
                 tint: bool = True) -> bytes:
        rect = tile_rect(tx, ty, z, tile=tile)
        w, h, rgba = self.render_layer(layer, rect, z, time_ms=time_ms,
                                       tint=tint)
        return encode_png_rgba(w, h, rgba)

    # -- digests, for comparing one render against another -----------------

    def tile_digest(self, layer: str, tx: int, ty: int, z: int, *,
                    time_ms: int = 0, tile: int = TILE) -> dict:
        r"""One tile's pixels, reduced to a comparable value.

        Returns::

            {layer, tx, ty, z, timeMs, w, h, bytes, digest, opaque, blank}

        **THE DIGEST IS OVER THE RAW RGBA, NOT OVER THE PNG.**  `tile_png`
        encodes the same pixels, and a PNG's bytes depend on the encoder's
        filter choices and zlib level as well as on the image -- so two
        identical pictures can encode to different files, and a comparison
        built on `tile_png` would report differences that are not there.  The
        buffer `render_layer` returns is the picture itself.

        **`timeMs` IS PART OF THE ANSWER AND IS RECORDED.**  `terrain` and
        `cover` are animated sprites (`render_sprites` takes the clock), so a
        digest without the time it was taken at is not reproducible and two
        digests taken at different times are not comparable.  It is in the
        record rather than folded into the hash, so a caller can still ask
        "are these the same pixels" across two different clocks and get a
        true answer.

        **`opaque` AND `blank` EXIST BECAUSE AN EMPTY TILE IS A REAL ANSWER.**
        Most tiles of most layers draw nothing -- `cover` on open ground,
        `terrain` away from scenery -- and a fully transparent tile hashes to
        a perfectly good constant.  Without a count, a manifest of four
        thousand identical digests reads exactly like a successful
        comparison.  `opaque` is how many pixels have any alpha at all;
        `blank` is `opaque == 0`.  **A digest set that is entirely blank is a
        well-formed, complete, meaningless answer**, and the caller is given
        what it needs to say so.

        The coordinates are NOT hashed.  Keeping the digest purely a function
        of the pixels is what lets a caller ask the two different questions a
        comparison needs: *did this tile change* (same address, two builds)
        and *did this art move* (same digest, two addresses).  Mixing the
        address in would answer only the first.
        """
        rect = tile_rect(tx, ty, z, tile=tile)
        w, h, rgba = self.render_layer(layer, rect, z, time_ms=time_ms)
        opaque = 0
        for i in range(3, len(rgba), 4):
            if rgba[i]:
                opaque += 1
        return {
            "layer": layer, "tx": int(tx), "ty": int(ty), "z": int(z),
            "timeMs": int(time_ms), "w": w, "h": h, "bytes": len(rgba),
            "digest": hashlib.sha256(bytes(rgba)).hexdigest(),
            "opaque": opaque, "blank": opaque == 0,
        }

    def layer_digests(self, layer: str, z: int, *, time_ms: int = 0,
                      tile: int = TILE) -> dict:
        r"""Every tile of one layer at one zoom, with the map's identity on it.

        THE IDENTITY IS NOT DECORATION.  Two digest sets are only comparable
        if they describe the same map at the same zoom with the same tiling,
        and a set that carries only digests can be diffed against anything at
        all -- including a different map, which would report every tile as
        changed and look like a catastrophic finding.  So `map`, `pixels`,
        `grid`, `tile`, `z` and the `.DMap` this was rendered from travel with
        the digests.

        `blank` and `drawn` are summed here for the same reason `tile_digest`
        reports `opaque`: **a set that is 100% blank must not read as a clean
        comparison**, and the only way to make that visible is to count it
        where the caller cannot miss it.

        **TWO SETS ARE ONLY COMPARABLE AT THE SAME `z` AND THE SAME `tile`,
        and this is the property most likely to be got wrong.**  A tile
        address is not a resolution of a fixed region -- it is an address into
        a grid whose cells cover `tile * z` art pixels, so tile (1,1) is a
        DIFFERENT PART OF THE MAP at every zoom.  Measured on `newbie`::

            z=2  tile(1,1) -> art (512, 512)-(1024, 1024)
            z=8  tile(1,1) -> art (2048, 2048)-(4096, 4096)

        On that map `ground` tile (1,1) is blank at z=2 and draws 5,335
        pixels at z=8 -- not because the zoom revealed anything, but because
        the two addresses name different ground.  **A comparator that diffs a
        z=2 set against a z=8 set reports every tile as changed**, which
        looks like a catastrophic finding and is an addressing mistake.  That
        is why `z` and `tile` travel with the digests rather than being the
        caller's business to remember.
        """
        if self.pm is None:
            return {"map": self.name, "ok": False, "why": self.reason,
                    "layer": layer, "z": int(z), "tiles": []}
        gx, gy = tile_grid(self.pm.px_w, self.pm.px_h, z, tile=tile)
        tiles = []
        for ty in range(gy):
            for tx in range(gx):
                tiles.append(self.tile_digest(layer, tx, ty, z,
                                              time_ms=time_ms, tile=tile))
        blank = sum(1 for t in tiles if t["blank"])
        return {
            "map": self.name, "ok": True, "layer": layer, "z": int(z),
            "timeMs": int(time_ms), "tile": int(tile),
            "pixels": [self.pm.px_w, self.pm.px_h], "grid": [gx, gy],
            "dmap": self.dmap_logical,
            "integrityChecked": self.lib.integrity.checked(self.dmap_logical),
            "tiles": tiles, "blank": blank, "drawn": len(tiles) - blank,
        }

    # -- the resident tile set ---------------------------------------------

    def tileset(self) -> Optional[tuple[dict, bytes]]:
        """`(manifest, bundle)` -- everything this map draws, still DXT.

        This is the coplay/VibeCO residency model (`docs/map_memory.md`)
        applied to the editor: the whole map's distinct art, shipped once,
        compressed, and composited by the page instead of by this process.
        The route above renders a PNG per 256-px tile per layer per zoom and
        decodes the same `.dds` again for each of them; this decodes nothing
        at all.  `tools/tileset.py` owns the format, unchanged -- the page
        that consumes it is `tilebake.js`, also unchanged in what it expects.

        `None` when the map has no placeable art, which is exactly when the
        PNG path is the only one that works: the caller falls back.
        """
        if self.pm is None:
            return None
        with self._lock:
            if getattr(self, "_tileset", None) is None:
                import tileset as tilesetmod                 # noqa: PLC0415
                sc = self.scenery()
                # THE TINT HAS TO TRAVEL WITH THE BUNDLE. It used to be
                # applied in `render_sprites` only -- the PNG path -- so a
                # viewer on the GPU path got untinted covers while the feature
                # was reported as shipped. Keyed by object identity, the same
                # way `cover_tints` binds, because the .DMap index and the
                # sorted position are different numbers.
                tints, ix = self.cover_tints()
                lt = self.late_tints()
                by_obj = {}
                for p_ in sc.covers:
                    t = tints.get(ix.get(id(p_), -1)) if tints else None
                    if t:
                        by_obj[id(p_)] = t
                for p_ in sc.late_covers:
                    t = lt.get(p_.layer_index) if lt else None
                    if t:
                        by_obj[id(p_)] = t
                manifest, bundle = tilesetmod.build_full(
                    self.pm, sc, self.lib.sprites, self.backdrops(),
                    tints=by_obj, puzzle_tints=self.puzzle_tints())
                manifest["map"] = self.name
                manifest["backdrops"] = len(self.backdrops())
                manifest["sceneParts"] = len(sc.scenes) if sc else 0
                manifest["coverCount"] = len(sc.covers) if sc else 0
                self._tileset = (manifest, bundle)
            return self._tileset

    # -- hit testing -------------------------------------------------------

    def pick(self, px: float, py: float, *, layers=None) -> dict:
        """What is drawn at art pixel `(px, py)`, topmost first.

        COVER is above TERRAIN is above the ground, which is the draw order in
        `docs/map_scenery.md` §7. A sprite counts as hit only where its own
        alpha is non-zero, so the gaps in a tree are see-through to the click
        as well as to the eye.
        """
        want = set(layers or ("cover", "terrain", "ground"))
        for kind, name in (("cover", "cover"), ("scene", "terrain")):
            if name not in want:
                continue
            for it in reversed(self.items(kind)):
                if self._alpha_at(it, px, py):
                    return self.describe_item(kind, it, hit=(px, py))
        if "ground" in want:
            return self.describe_cell_at(px, py)
        return {"kind": "none", "px": [px, py]}

    def _alpha_at(self, it: PlacedInfo, px: float, py: float) -> bool:
        sx = int(px - it.origin[0])
        sy = int(py - it.origin[1])
        w, h = it.size
        if not (0 <= sx < w and 0 <= sy < h):
            return False
        fr = self.lib.sprites.frames(it.placed.ani, it.placed.title)
        if not fr:
            return False
        fw, fh, rgba = fr[0]
        return rgba[(sy * fw + sx) * 4 + 3] > 8

    # -- inspector payloads ------------------------------------------------

    def describe_item(self, kind: str, it: PlacedInfo, *, hit=None) -> dict:
        p = it.placed
        integ = self.lib.integrity
        opened = blocked = 0
        cells = []
        if kind == "scene":
            for x, y, m, s, e in p.map_cells():
                if m:
                    blocked += 1
                else:
                    opened += 1
                if len(cells) < 400:
                    cells.append({"x": x, "y": y, "blocked": bool(m),
                                  "surface": s, "elevation": e})
        x0, y0, x1, y1 = p.cell_bounds
        source = _norm(p.scene_path) if kind == "scene" else self.dmap_logical
        out = {
            "kind": "terrain" if kind == "scene" else "cover",
            "index": it.index,
            "title": p.title,
            "ani": _norm(p.ani),
            "aniKey": p.title,
            "frames": it.frames,
            "frameCount": it.frame_count,
            "frameInterval": p.frame_interval,
            "animated": it.frame_count > 1,
            "anchorCell": list(p.anchor),
            "cellBounds": [x0, y0, x1, y1],
            "cellSize": [p.width, p.height],
            "pixelOffset": list(p.pixel_offset),
            "spriteOrigin": [round(it.origin[0], 2), round(it.origin[1], 2)],
            "spriteSize": list(it.size),
            "layerIndex": p.layer_index,
            "thickness": p.thickness,
            "offsetElevation": p.offset_elevation,
            "elevationUsable": abs(p.offset_elevation) < scenemod.ELEVATION_SANE,
            "source": source,
            "scenePath": _norm(p.scene_path),
            "partSource": _part_source(self.lib.root, p.title),
            "opensCells": opened,
            "blocksCells": blocked,
            "carriesPassability": kind == "scene",
            "cells": cells,
            "integrity": integ.status(source),
            "editable": _editable(integ, it.frames, source),
        }
        if hit:
            out["px"] = [round(hit[0], 1), round(hit[1], 1)]
            gx, gy = self.pm.px_to_cell(*hit)
            out["cellUnderCursor"] = [int(gx), int(gy)]
        return out

    def describe_cell_at(self, px: float, py: float) -> dict:
        """The puzzle cell (a tile of the painted image) plus the map cell the
        pixel lies in. Two different grids at the same point, and the panel
        must not conflate them."""
        pm = self.pm
        if pm is None:
            return {"kind": "none", "why": self.reason}
        g = pm.grid
        i, j = int(px // g), int(py // g)
        gx, gy = pm.px_to_cell(px, py)
        cell = self.cell_state(int(gx), int(gy))
        idx = pm.tile_at(i, j)
        path = pm.tile_path(idx) if idx != puzzlemod.EMPTY else ""
        integ = self.lib.integrity
        return {
            "kind": "puzzle",
            "px": [round(px, 1), round(py, 1)],
            "tileSlot": [i, j],
            "tileIndex": None if idx == puzzlemod.EMPTY else idx,
            "tileKey": "" if idx == puzzlemod.EMPTY else f"Puzzle{idx}",
            "empty": idx == puzzlemod.EMPTY,
            "frames": [path] if path else [],
            "texture": path,
            "tileRect": [i * g, j * g, (i + 1) * g, (j + 1) * g],
            "gridSize": g,
            "ani": pm.ani,
            "source": pm.pul_path,
            "mapCell": [int(gx), int(gy)],
            "cell": cell,
            "integrity": integ.status(pm.pul_path),
            "editable": _editable(integ, [path] if path else [], pm.pul_path),
        }

    # -- reporting ---------------------------------------------------------

    def layers(self) -> list[dict]:
        r"""**The layers THIS map has**, in default draw order, furthest first.

        `LAYERS` is a list of layer KINDS and the page used to render one
        checkbox per kind, always five, on every map. That is wrong in both
        directions and the corpus says so -- measured over the 150 drawable
        maps of the 156 the registry lists, 625 real layers in all:

            0 backdrop planes   89 maps    "Background" toggled nothing
            1 plane             48 maps
            2+ planes           13 maps    one checkbox, up to 16 layers
                                           (star01..star10 carry 7..16)
            0 TERRAIN scenes   121 maps    "TERRAIN" toggled nothing
            0 COVER sprites     22 maps    "COVER" toggled nothing
            0 painted ground     1 map     spirit01_new: planes and cells only

        Panel sizes that result: 2 rows on 6 maps, 3 on 85, 4 on 33, 5 on 15,
        and 6..19 on the remaining 11. **Only 15 of 150 maps have the five
        the old panel always drew.**

        So each backdrop plane is its own layer here (`background:0`,
        `background:1`, ...), and **a layer with nothing in it is not
        returned at all** rather than returned greyed: the panel is built
        from this list, and a control that cannot change the picture is
        noise in a panel whose whole job is telling you what the picture is
        made of.

        `count` is the MEASURED population of the layer, not a capacity --
        non-empty slots for a plane and for the ground, placements for
        TERRAIN and COVER, cells for passability -- because that count is
        what "hide the empty ones" is decided on, and a count derived from
        the grid dimensions would call a fully empty plane full.

        `passability` is always present when there is a `.DMap`: it is a data
        overlay over the cell grid rather than art, and its population is the
        grid, which is never zero on a map that parses.
        """
        out: list[dict] = []
        d = self.header()
        # LIST POSITION, never `b.index`. `star01..star10` carry 7..16 planes
        # and EVERY ONE of them reports `index == 0`, so an id built from
        # `b.index` gave sixteen layers the same id and sixteen checkboxes
        # one state. Position is also exactly what `render_background(plane=)`
        # and `tilebake`'s `this.planes[]` address, so the id, the renderer
        # and the manifest all mean the same plane.
        for i, b in enumerate(self.backdrops()):
            art = b.art
            n = sum(1 for t in art.tiles if t != puzzlemod.EMPTY)
            if not n:
                continue
            out.append({
                "id": "background%s%d" % (LAYER_SEP, i),
                "kind": "background", "plane": i,
                "title": "part %d" % (i + 1),
                "count": n, "detail": "%d×%d slots, parallax %d/%d"
                                      % (art.pul_w, art.pul_h,
                                         b.parallax[0], b.parallax[1]),
                "help": LAYER_HELP["background"], "path": b.path,
            })
        if self.pm is not None:
            n = sum(1 for t in self.pm.tiles if t != puzzlemod.EMPTY)
            if n:
                out.append({
                    "id": "ground", "kind": "ground", "plane": None,
                    "title": "part %d" % (len([r for r in out
                                               if r["kind"] == "background"])
                                          + 1), "count": n,
                    "detail": "%d×%d slots" % (self.pm.pul_w, self.pm.pul_h),
                    "help": LAYER_HELP["ground"], "path": self.pm.pul_path,
                })
        sc = self.scenery()
        for kind, n in (("terrain", len(sc.scenes)), ("cover", len(sc.covers)),
                        ("interactive", len(sc.late_covers))):
            if not n:
                continue
            out.append({
                "id": kind, "kind": kind, "plane": None,
                "title": LAYER_TITLE[kind], "count": n,
                "detail": "%d placed" % n, "help": LAYER_HELP[kind], "path": "",
            })
        if d is not None:
            out.append({
                "id": "passability", "kind": "passability", "plane": None,
                "title": LAYER_TITLE["passability"], "count": d.width * d.height,
                "detail": "%d×%d cells" % (d.width, d.height),
                "help": LAYER_HELP["passability"], "path": self.dmap_logical,
            })
        return out

    def to_json(self) -> dict:
        pm = self.pm
        d = self.header()
        sc = self.scenery()
        out = {
            "name": self.name,
            "ok": pm is not None,
            "why": self.reason,
            "dmap": self.dmap_logical,
            "dmapStaged": self.staged,
            "mapSize": [d.width, d.height] if d else [0, 0],
            "layerCount": d.layer_count if d else 0,
            "version": (d.version_string or d.version) if d else "",
            "sceneParts": len(sc.scenes),
            "covers": len(sc.covers),
            "lateCovers": len(sc.late_covers),
            "missingScenes": sc.missing_scenes,
            "backdrops": [b.to_json() for b in self.backdrops()] if pm else [],
            "tile": TILE,
            "zooms": list(ZOOMS),
            # DERIVED PER MAP, not the `LAYERS` constant -- see `layers()`
            # for the corpus measurement that says why. Empty layers are
            # absent from this list, so the page's panel is the map.
            "layers": self.layers(),
            "layerKinds": list(LAYERS),
            "integrity": self.lib.integrity.status(self.dmap_logical),
        }
        if pm is not None:
            out |= {
                "puzzle": pm.pul_path,
                "ani": pm.ani,
                "gridSize": pm.grid,
                "pul": [pm.pul_w, pm.pul_h],
                "pixels": [pm.px_w, pm.px_h],
                "originCells": pm.k,
                "impliedSize": pm.implied_size,
                "consistent": pm.consistent,
                "tiles": tile_grid(pm.px_w, pm.px_h, 1),
            }
        return out

    def editable_report(self) -> dict:
        """Every file this map is made of, on one side of the line or the
        other. The answer to "what may I change here?" as data."""
        integ = self.lib.integrity
        free, checked = [], []
        seen = set()

        def add(path: str, what: str):
            key = _norm(path)
            if not key or key in seen:
                return
            seen.add(key)
            row = {"path": key, "what": what}
            (checked if integ.checked(key) else free).append(row)

        add(self.dmap_logical, "cell grid: passability, dimensions, layer placement")
        if self.pm is not None:
            add(self.pm.pul_path, "which painted tile goes in which slot")
            add(self.pm.ani, "tile index -> texture")
            for idx in sorted(self.pm.frames):
                add(self.pm.frames[idx], "painted ground tile")
        for b in self.backdrops():
            add(b.path, "background plane")
        sc = self.scenery()
        for p in sc.scenes:
            add(p.scene_path, "compiled scene object")
            src = _part_source(self.lib.root, p.title)
            if src:
                add(src, "scene part source (editable text)")
        for k in ("scene", "cover"):
            for it in self.items(k):
                for f in it.frames:
                    add(f, "TERRAIN sprite frame" if k == "scene" else "COVER sprite frame")
        return {"name": self.name, "free": free, "checked": checked,
                "freeCount": len(free), "checkedCount": len(checked),
                "manifestFiles": len(integ), "manifestRows": integ.row_count}


def _editable(integ: Integrity, frames, source: str) -> dict:
    """The one sentence every inspector panel leads with."""
    art = [f for f in frames if f]
    src_checked = integ.checked(source)
    return {
        "artFree": bool(art) and not any(integ.checked(f) for f in art),
        "art": art,
        "sourceChecked": src_checked,
        "source": _norm(source),
        "summary": ("Art is free to edit; the placement lives in an "
                    "integrity-checked file." if src_checked and art else
                    "Free to edit -- nothing here is in integrity.json."
                    if art else
                    "This is an integrity-checked file." if src_checked else
                    "Nothing here is in integrity.json."),
    }


def _part_source(root: Optional[Path], title: str) -> str:
    """`map/ScenePart/<title>.Part`, when it exists.

    A `.scene` is compiled from these CRLF text files and they are by far the
    easiest thing for a modder to edit (`docs/map_scenery.md` §4). The title
    inside a `.scene` is the `.Part`'s own name with a `.tga` suffix on most
    parts, so both spellings are tried.
    """
    if root is None or not title:
        return ""
    stem = Path(str(title)).stem
    d = root / "map" / "ScenePart"
    if not d.is_dir():
        return ""
    for cand in (f"{stem}.Part", f"{title}.Part"):
        p = d / cand
        if p.is_file():
            return f"map/ScenePart/{p.name}"
    return ""


def _resample_rgba(rgba: bytes, w: int, h: int, grid: int) -> bytes:
    if (w, h) == (grid, grid):
        return rgba
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8).reshape(h, w, 4)
        yi = (_np.arange(grid) * h // grid).clip(0, h - 1)
        xi = (_np.arange(grid) * w // grid).clip(0, w - 1)
        return a[yi][:, xi].tobytes()
    out = bytearray(grid * grid * 4)
    for y in range(grid):
        sy = min(h - 1, y * h // grid)
        for x in range(grid):
            sx = min(w - 1, x * w // grid)
            s = (sy * w + sx) * 4
            d = (y * grid + x) * 4
            out[d:d + 4] = rgba[s:s + 4]
    return bytes(out)


def _diamond(buf: bytearray, w: int, h: int, cx: int, cy: int,
             half_w: int, half_h: int, col) -> None:
    """Fill the isometric diamond centred on `(cx, cy)`, clipped.

    Degenerates to a single pixel once the cell is smaller than one output
    pixel, which is what a whole-map view of `Gulf` needs.
    """
    r, g, b, a = col
    if half_w <= 1 or half_h <= 1:
        if 0 <= cx < w and 0 <= cy < h:
            o = (cy * w + cx) * 4
            buf[o] = r; buf[o + 1] = g; buf[o + 2] = b; buf[o + 3] = a
        return
    for dy in range(-half_h + 1, half_h):
        y = cy + dy
        if not (0 <= y < h):
            continue
        span = int(half_w * (1 - abs(dy) / half_h))
        x0 = max(0, cx - span)
        x1 = min(w - 1, cx + span)
        o = (y * w + x0) * 4
        for _ in range(x1 - x0 + 1):
            buf[o] = r; buf[o + 1] = g; buf[o + 2] = b; buf[o + 3] = a
            o += 4


# ---------------------------------------------------------------------------
# the library
# ---------------------------------------------------------------------------

class StageFirst:
    """An `AssetRoot` that reads `mods/stage/` before the install.

    This is the *same* precedence the game itself uses once a mod is
    installed -- `tools/comod.py`'s whole premise is that a loose file beats
    an archive -- so a staged `.dds` shows on the map immediately, and what
    you see before you install is what you get after.

    It is a read-only view: nothing here writes, and the install is never
    touched. Delegation is by `__getattr__` so the wrapper cannot drift from
    `AssetRoot`'s interface.
    """

    def __init__(self, base, stage: Path) -> None:
        self._base = base
        self._stage = Path(stage)

    def read(self, logical: str) -> bytes:
        p = self._stage / _norm(logical)
        if p.is_file():
            return p.read_bytes()
        return self._base.read(logical)

    def staged(self, logical: str) -> bool:
        return (self._stage / _norm(logical)).is_file()

    def __getattr__(self, name):
        return getattr(self._base, name)


def _cstr_head(head: bytes) -> str:
    """The puzzle path from a header-only `.DMap` slice."""
    raw = head[dmapmod.HEADER_PATH_OFF:
               dmapmod.HEADER_PATH_OFF + dmapmod.HEADER_PATH_LEN]
    return raw.split(b"\x00", 1)[0].decode("latin-1", "replace")


class MapEditor:
    """Every map the editor can offer, and the shared caches behind them.

    One of these lives on the viewer's `Catalog`. It owns a `PuzzleLibrary`, a
    `SceneLibrary` and a `SpriteCache` so a session parses `MapScene.ani` once
    rather than once per map.
    """

    def __init__(self, root: Optional[Path] = None, assets=None,
                 stage: Optional[Path] = None) -> None:
        self.reason = ""
        self.stage = Path(stage) if stage else STAGE
        self.root: Optional[Path] = Path(root) if root else None
        if self.root is None:
            try:
                found = coroot.find()
                self.root = Path(found.path) if found else None
            except Exception as e:                       # noqa: BLE001
                self.reason = f"could not resolve the game install: {e}"
        self.puzzles = puzzlemod.PuzzleLibrary(self.root)
        self.scenes = scenemod.SceneLibrary(self.root)
        self.integrity = Integrity(self.root)
        # Everything that reads *art* reads through the stage tree first, so a
        # staged .dds is on the map the moment it is staged. The `.pul`,
        # `.scene` and `.ani` files that decide *placement* are still read
        # straight from the install -- see the module docstring's limitations.
        base = assets if assets is not None else self.puzzles.assets
        self.assets = StageFirst(base, self.stage) if base is not None else None
        self.puzzles._assets = self.assets            # noqa: SLF001
        self.sprites = scenemod.SpriteCache(self.root, assets=self.assets)
        self._maps: dict[str, MapArt] = {}
        self._rows: Optional[list[dict]] = None
        #: which spelling of the map registry answered, "" until `rows()` runs
        #: and "" after it if this install ships neither. Recorded rather than
        #: inferred so the picker can say which file it read (C-2026-08-09-ani-json-spelling).
        self.registry_rel: str = ""
        self._lock = threading.RLock()

    def invalidate(self, logical: str = "") -> list[str]:
        """Drop cached art after something was staged or unstaged.

        Returns the map names whose caches were cleared. A `.DMap` only
        affects its own map; a texture could be shared by any of them, so the
        honest answer for anything else is "all of them".
        """
        key = _norm(logical)
        with self._lock:
            if key.startswith("map/map/") and key.endswith(".dmap"):
                names = [Path(key).stem.lower()]
            else:
                names = list(self._maps)
            for n in names:
                art = self._maps.get(n)
                if art is not None:
                    art.invalidate_art()
                    if not key or key.endswith(".dmap"):
                        art.invalidate_grid()
            return names

    # -- the picker --------------------------------------------------------

    def rows(self) -> list[dict]:
        """Every map-registry row, with what state its art is in.

        The picker lists all of them and says which are unusable and why,
        because a map that silently vanishes from a list is indistinguishable
        from a bug.

        The registry is `ini/GameMap.json` on the community client and the
        binary `ini/GameMap.dat` on every official one; `registry_rel` says
        which answered. MEASURED across the six declared installs -- rows,
        distinct stems, `.DMap` files on disk:

            cco   156/137/136    5017  145/119/142    5065  153/124/144
            5165  179/138/154    5517  262/180/181    6090  303/216/184

        **Rows outnumber maps on every base**, because a map is reused under
        several DocumentIds, so this list is deliberately longer than the map
        count and duplicate names in it are data rather than a defect.
        """
        with self._lock:
            if self._rows is not None:
                return self._rows
            rows: list[dict] = []
            if self.root is None:
                self._rows = rows
                return rows
            # Both spellings. Reading only the .json gave every official
            # client an empty registry -- the picker still listed maps,
            # because the `.DMap` scan below fills it, so it LOOKED right
            # while `documentId` and `gridSize` were None on every row
            # (156/156 populated on CCO, 0 of 142-184 elsewhere). A partial
            # failure that leaves the list intact is why this one outlived
            # the two that were fixed. See `dmap.load_gamemap` and C-2026-08-09-ani-json-spelling.
            self.registry_rel, raw = dmapmod.load_gamemap(self.root)
            files = {}
            d = self.root / "map" / "map"
            if d.is_dir():
                for p in d.iterdir():
                    if p.suffix.lower() == ".dmap":
                        files[p.stem.lower()] = p
            seen = set()
            for r in raw:
                fn = str(r.get("FileName", "")).replace("\\", "/")
                stem = Path(fn).stem
                if not stem:
                    continue
                seen.add(stem.lower())
                rows.append(self._row(stem, files.get(stem.lower()), r))
            for key, p in sorted(files.items()):
                if key not in seen:
                    rows.append(self._row(p.stem, p, None))
            rows.sort(key=lambda x: (-x["area"], x["name"].lower()))
            if rows and not self.registry_rel:
                # Every row came from the `.DMap` scan, so none carries a
                # DocumentId or a PuzzleGridSize. That is a missing registry,
                # not a client whose maps have no ids -- say which.
                print(f"mapedit: no map registry under {self.root / 'ini'} "
                      f"(neither GameMap.json nor GameMap.dat); "
                      f"{len(rows)} maps listed from the .DMap scan alone, "
                      f"all without a DocumentId", file=sys.stderr)
            self._rows = rows
            return rows

    def _row(self, stem: str, path: Optional[Path], gm: Optional[dict]) -> dict:
        row = {"name": stem, "documentId": (gm or {}).get("DocumentId"),
               "gridSize": (gm or {}).get("PuzzleGridSize"),
               "width": 0, "height": 0, "area": 0, "puzzle": "",
               "state": "missing", "why": "", "consistent": None,
               "inGameMap": gm is not None}
        if path is None:
            # NO LOOSE .DMap. From 5517 on that is the NORM rather than an
            # error -- every one of 7878's 730 maps ships only as a .7z --
            # and this branch used to return a row reading 0x0 "no .DMap
            # ships", which is a client with 730 maps presenting as a client
            # with none.
            #
            # The dimensions live at DIMS_OFF (268), so only the first
            # GRID_OFF bytes are decompressed: 470 archives in 0.1s against
            # 2.9s to extract them whole. `archive_head` is deliberately the
            # UNVERIFIED call -- a CRC covers a whole file and this stops
            # early -- so the row is marked `archive` and the editor still
            # reads the full, CRC-checked bytes when it OPENS the map.
            arch = (self.root / "map" / "map" / f"{stem}.7z"
                    if self.root else None)
            if arch is not None and arch.is_file():
                head = dmapmod.archive_head(arch, dmapmod.GRID_OFF)
                if head and len(head) >= dmapmod.DIMS_OFF + 8:
                    w, h = struct.unpack_from("<II", head, dmapmod.DIMS_OFF)
                    if 0 < w <= 65536 and 0 < h <= 65536:
                        row |= {"width": w, "height": h, "area": w * h,
                                "state": "archive",
                                "puzzle": _norm(_cstr_head(head)),
                                "why": "ships only inside a .7z; "
                                       "dimensions read from the archive"}
                        return row
                row["why"] = ("ships only inside a .7z and its header did "
                              "not read")
                return row
            row["why"] = "the map registry names it but no .DMap ships"
            return row
        try:
            d = dmapmod.parse(path, want_cells=False, verify=False)
        except Exception as e:                           # noqa: BLE001
            row["why"] = f".DMap did not parse: {e}"
            row["state"] = "broken"
            return row
        row |= {"width": d.width, "height": d.height, "area": d.width * d.height,
                "puzzle": _norm(d.puzzle_path), "layerCount": d.layer_count,
                "archive": d.archive}
        if d.archive == "stale":
            # Carried on every row, not only as a state, because a stale map is
            # otherwise indistinguishable from a good one at every level of
            # this picker: it parses, it has art, and it opens.  The row stays
            # usable -- refusing to list it would hide a map the user can see
            # in their own folder -- but it must not present as `ok`.
            #
            # `d` IS THE LEFTOVER. Every figure above this line was read from
            # the loose file, and on 6090 74 of these disagree with the
            # archive -- 2 in dimensions, 51 in layer count, 19 in the cell
            # grid. `header()`/`cells()` now read the archive
            # (`dmap_source`), so leaving the row on `d` would leave two
            # surfaces of one tool reporting different maps.
            #
            # The dimensions and puzzle path come from the archive HEAD, the
            # same 0.1s-for-470 read the `archive` branch above uses.
            # `layerCount` is a u32 that sits AFTER the cell grid
            # (`core/dmap.py:34`), so no head read reaches it and a full parse
            # of these costs 7.9s against this whole call's 0.8s. It is
            # therefore dropped rather than reported from the leftover --
            # which is exactly what the `archive` branch already does for the
            # 44 maps that ship with no loose file at all.
            row["state"] = "stale"
            row.pop("layerCount", None)
            arch = (self.root / "map" / "map" / f"{stem}.7z"
                    if self.root else None)
            read_from = "the leftover (the archive head did not read)"
            if arch is not None and arch.is_file():
                head = dmapmod.archive_head(arch, dmapmod.GRID_OFF)
                if head and len(head) >= dmapmod.DIMS_OFF + 8:
                    w, h = struct.unpack_from("<II", head, dmapmod.DIMS_OFF)
                    if 0 < w <= 65536 and 0 < h <= 65536:
                        row |= {"width": w, "height": h, "area": w * h,
                                "puzzle": _norm(_cstr_head(head))}
                        read_from = "the archive"
            row["dimsFrom"] = read_from
            row["why"] = ("this loose .DMap is not the one inside the .7z "
                          "beside it; the archive is what the client reads, "
                          "so the loose file is a previous client's map. The "
                          "editor reads the archive; the size and puzzle "
                          "above came from " + read_from + ", and the layer "
                          "count is not read here because it sits past the "
                          "cell grid")
            return row
        # **THERE WAS A `.pux` BRANCH HERE AND IT LIED.** It gave the row
        # `state = "pux"`, `why = "...which is not decoded"`, and returned
        # EARLY -- so the four CCO maps that use one were marked as a special
        # not-really-supported state and denied their `pixels`, `consistent`
        # and real `state`, while `MapEditor.get()` opened all four with a
        # full ground and up to three backdrop planes. `puzzle.py` has built
        # pux grounds through `dmap.read_pux_full` since that reader landed;
        # this row builder never learnt. A `.pux` now takes the same path as
        # a `.pul`, which is what it has actually been doing all along.
        pm = self.puzzles.get(stem)
        if pm is None:
            row["state"] = "noart"
            row["why"] = self.puzzles.reason
            return row
        row["consistent"] = pm.consistent
        row["pixels"] = [pm.px_w, pm.px_h]
        if not pm.consistent:
            row["state"] = "mismatch"
            row["why"] = (f"the art implies a {pm.implied_size:g}-cell map and "
                          f"the .DMap says {d.width}; see docs/ground_art.md "
                          f"§3.1")
        else:
            row["state"] = "ok"
        return row

    # -- one map -----------------------------------------------------------

    def get(self, name: str) -> MapArt:
        key = str(name).lower()
        with self._lock:
            if key not in self._maps:
                self._maps[key] = MapArt(_canonical(self.root, name), self)
            return self._maps[key]


def _canonical(root: Optional[Path], name: str) -> str:
    """The map's name as the filesystem spells it -- `Dcloister`, not
    `dcloister`. The logical path a mod is staged at has to match."""
    if root is None:
        return name
    d = root / "map" / "map"
    if d.is_dir():
        for p in d.iterdir():
            if p.suffix.lower() == ".dmap" and p.stem.lower() == str(name).lower():
                return p.stem
    return name


# ---------------------------------------------------------------------------
# the one gated write: a .DMap passability patch
# ---------------------------------------------------------------------------

ACK = "I understand this file is integrity-checked"


class NotAcknowledged(Exception):
    """Raised when a DMap edit is attempted without the explicit opt-in."""


class _RowView:
    """One row of cells, addressed as though it were still inside the grid."""

    __slots__ = ("_row", "_base")

    def __init__(self, row, base: int) -> None:
        self._row = row
        self._base = base

    def __getitem__(self, i: int):
        return self._row[i - self._base]


def stage_passability(art: MapArt, edits, *, ack: str = "",
                      stage: Optional[Path] = None) -> dict:
    r"""Byte-patch a `.DMap`'s cell masks and stage the result.

    WHY THIS IS SAFE TO DO AT ALL, AND WHY IT IS STILL GATED
    --------------------------------------------------------
    The cell grid's layout is exact and proved: `core/dmap.py`'s per-row
    checksum reproduces 58,245 of 58,898 shipped rows, 133 files at 100%. So a
    cell's mask is at a known byte offset and the row checksum that covers it
    is at another, and patching both is a surgical edit rather than a rewrite:
    every other byte of the file is copied through untouched, and the result
    is re-parsed before it is written.

    That does **not** make it free. `.DMap` is one of the 155 files
    `integrity.json` lists, so this is the one thing in the MapEditor that
    changes a checked file, and it will not run without `ack == ACK`.

    `edits` is an iterable of `{x, y, blocked}`. The base is the already-staged
    copy when there is one, so successive edits accumulate instead of each one
    reverting the last.
    """
    if ack != ACK:
        raise NotAcknowledged(
            "map/map/*.DMap is listed in integrity.json. Editing it changes a "
            "file the client verifies. Pass the acknowledgement to proceed.")
    stage = Path(stage) if stage else art.lib.stage
    logical = art.dmap_logical
    staged = stage / logical
    if staged.is_file():
        data = bytearray(staged.read_bytes())
        base_src = f"{logical} (staged)"
    else:
        # **The base is what the REGISTRY names, not the loose leftover.**
        # From 5517 on the registry names `map/map/<stem>.7z` and the loose
        # `.DMap` beside it is a previous client's: 77 of 6609's 184 disagree,
        # and 7878 ships no loose `.DMap` at all.  Editing the loose file means
        # your edit lands on the wrong content before the client reads
        # anything -- a defect one layer beneath the one this staging note is
        # about.  See `docs/map_twin_precedence.md`.
        raw, base_src = dmapmod.open_map(art.lib.root, art.name)
        if raw is None:
            raise FileNotFoundError(f"no readable map for {art.name}: {base_src}")
        data = bytearray(raw)

    width, height = struct.unpack_from("<II", data, dmapmod.DIMS_OFF)
    row_stride = width * dmapmod.CELL_SIZE + 4
    changed, unchanged, outside = [], 0, 0
    refused: list[dict] = []
    rows_touched = set()
    for e in edits:
        x, y = int(e["x"]), int(e["y"])
        blocked = bool(e.get("blocked"))
        if not (0 <= x < width and 0 <= y < height):
            outside += 1
            continue
        off = dmapmod.GRID_OFF + y * row_stride + x * dmapmod.CELL_SIZE
        (old,) = struct.unpack_from("<H", data, off)
        if old not in (0, 1):
            # **Refuse rather than clobber.**  `mask` is not a boolean: the
            # corpus carries 2, 4 and 5 as well (`core/dmap.row_checksum`), and
            # writing `0` or `1` over one silently discards a bit nobody has
            # decoded.  Unblocking a mask-4 cell cannot be done without either
            # losing bit 2 or assuming bit 0 is the blocked bit -- and that is
            # a hypothesis, not a finding (`docs/map_scenery.md` section 10).
            # Declining costs an edit; guessing costs a value.
            refused.append({"x": x, "y": y, "mask": old})
            continue
        new = 1 if blocked else 0
        if (old != 0) == blocked:
            unchanged += 1
            continue
        struct.pack_into("<H", data, off, new)
        changed.append({"x": x, "y": y, "from": old, "to": new})
        rows_touched.add(y)

    # Recompute the checksum of every row we touched. Leaving it stale would
    # be a file the client's own verification of the grid rejects, and the
    # checksum is the only part of the layout that is not pure copy-through.
    for y in sorted(rows_touched):
        base = dmapmod.GRID_OFF + y * row_stride
        vals = struct.unpack_from("<%dh" % (width * 3), data, base)
        cells = [(vals[i * 3] & 0xFFFF, vals[i * 3 + 1] & 0xFFFF, vals[i * 3 + 2])
                 for i in range(width)]
        # `dmap.row_checksum` indexes the WHOLE grid (`cells[y*width + x]`) and
        # uses `y` in the formula as well, so it is handed a view that offsets
        # one row into place. Reusing it rather than re-typing the formula is
        # the point: the checksum here can never drift from the one that
        # verified 58,245 shipped rows.
        cs = dmapmod.row_checksum(_RowView(cells, y * width), y, width)
        struct.pack_into("<I", data, base + width * dmapmod.CELL_SIZE, cs)

    # Prove it before it reaches the stage tree.
    tmp = stage / (logical + ".verify")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(bytes(data))
    try:
        check = dmapmod.parse(tmp, want_cells=True, verify=True)
        if check.width != width or check.height != height:
            raise ValueError("dimensions moved")
        bad = check.height - check.checksum_ok
    finally:
        try:
            tmp.unlink()
        except OSError:                                  # pragma: no cover
            pass

    dest = stage / logical
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(data))
    return {
        "logical": logical, "staged": str(dest), "bytes": len(data),
        "changed": changed, "changedCount": len(changed),
        "alreadyThatWay": unchanged, "outsideMap": outside,
        # Cells declined because their mask is not 0/1 -- reported, never
        # silently dropped, because a refusal the caller cannot see is
        # indistinguishable from an edit that worked.
        "refusedNonBooleanMask": refused,
        "refusedCount": len(refused),
        "base": base_src,
        "rowsRechecksummed": len(rows_touched),
        "rowChecksumsBad": bad,
        "note": ("The original was copied byte for byte apart from the cell "
                 "masks listed and the row checksums that cover them. Nothing "
                 "has touched the game install: run comod.py install to do "
                 "that, and comod.py uninstall to revert."),
        "integrity": art.lib.integrity.status(logical),
    }


#: Byte offset of a COVER record's `origin` inside its payload, and the two
#: u32 it holds. `core/dmap.decode_layer` is the ONE home for this layout --
#: `path[260] + key[128]` then `<IIIIiiI>` for origin / size / offset /
#: frame_interval -- and this names the offset it reads rather than a second
#: copy of the shape. A duplicated layout is how `mapindex` and `dmap` drifted
#: until one of them silently reported a smaller map.
COVER_ORIGIN_OFF = 388
COVER_TAG = 4


def _cover_records(raw: bytes, d):
    """`[(span, record_bytes)]` for the COVER records only, in list order."""
    out = []
    for i, (a, b) in enumerate(dmapmod.late_layer_spans(raw, d)):
        (tag,) = struct.unpack_from("<I", raw, a)
        if tag == COVER_TAG:
            out.append((i, a, b))
    return out


def apply_cover_edits(records, edits):
    """Apply `edits` to a list of raw record bytes. PURE -- no file, no map.

    Split out of `stage_covers` so the decisions can be tested without an
    install: which edits are refused, what an `add` copies, where an origin
    is written. `stage_covers` owns the gating, the base selection and the
    verify-before-write; this owns what an edit MEANS.

    Returns `(records, added, moved, removed, refused)` with `records` a NEW
    list -- removals are applied here rather than deferred to the caller, so
    the indices the caller reports and the list it writes cannot disagree.

    **Indices name positions in the WHOLE late list**, as `late_layers`
    reports them, so they match what an inspector shows. Edits are applied in
    order and `add` appends, so an index is stable for the duration of one
    call: a `remove` is recorded and applied at the end rather than shifting
    the list under a later edit in the same batch.
    """
    records = [bytearray(r) for r in records]
    added, moved, removed, refused = [], [], [], []
    drop: set = set()

    def tag_of(rec):
        return struct.unpack_from("<I", bytes(rec), 0)[0]

    for e in edits:
        op = str(e.get("op") or "").lower()
        if op == "add":
            src = int(e.get("from", -1))
            if not (0 <= src < len(records)):
                refused.append({"op": op, "why": "no record %d" % src})
                continue
            if tag_of(records[src]) != COVER_TAG:
                refused.append({"op": op, "index": src,
                                "why": "record %d is not a cover (tag %d)"
                                       % (src, tag_of(records[src]))})
                continue
            rec = bytearray(records[src])
            struct.pack_into("<II", rec, 4 + COVER_ORIGIN_OFF,
                             int(e["x"]), int(e["y"]))
            records.append(rec)
            added.append({"copiedFrom": src, "x": int(e["x"]),
                          "y": int(e["y"]), "index": len(records) - 1})
        elif op in ("move", "remove"):
            i = int(e.get("index", -1))
            if not (0 <= i < len(records)):
                refused.append({"op": op, "why": "no record %d" % i})
                continue
            if tag_of(records[i]) != COVER_TAG:
                # The origin offset is the COVER layout. Writing it into a
                # scene / sound / effect record would corrupt a different
                # shape at an offset that happens to exist.
                refused.append({"op": op, "index": i,
                                "why": "record %d is not a cover (tag %d)"
                                       % (i, tag_of(records[i]))})
                continue
            if op == "move":
                (ox, oy) = struct.unpack_from("<II", bytes(records[i]),
                                              4 + COVER_ORIGIN_OFF)
                struct.pack_into("<II", records[i], 4 + COVER_ORIGIN_OFF,
                                 int(e["x"]), int(e["y"]))
                moved.append({"index": i, "from": [ox, oy],
                              "to": [int(e["x"]), int(e["y"])]})
            else:
                drop.add(i)
                removed.append({"index": i})
        else:
            refused.append({"op": op or "(none)", "why": "unknown operation"})

    kept = [r for i, r in enumerate(records) if i not in drop]
    return kept, added, moved, removed, refused


def stage_covers(art: MapArt, edits, *, ack: str = "",
                 stage: Optional[Path] = None) -> dict:
    r"""Add, move or remove COVER sprites on a map, and stage the result.

    WHY THIS IS POSSIBLE AT ALL, AND WHY IT IS STILL GATED
    -------------------------------------------------------
    Cover sprites live in the `.DMap`'s v1006 second counted record list. The
    edit is SURGICAL in exactly the sense `stage_passability` is: the record
    list is spliced, the count is rewritten, and **every other byte of the
    file is copied through untouched**. Measured before this was written --
    396 of 396 v1006 maps across five installs splice byte-identically as a
    no-op, grow by exactly one record's length, and the grown file re-parses
    with `bytes_unconsumed == 0` (`scratchpad/late_splice_corpus.py`).

    That does **not** make it free. `.DMap` is one of the 155 files
    `integrity.json` lists, so like `stage_passability` this will not run
    without `ack == ACK`.

    WHAT AN EDIT MAY SAY

        {"op": "add", "from": <i>, "x": <px>, "y": <px>}
        {"op": "move", "index": <i>, "x": <px>, "y": <px>}
        {"op": "remove", "index": <i>}

    `add` COPIES an existing cover's record and gives the copy a new origin.
    **It does not author one from scratch, deliberately.** A cover payload is
    `path[260] + key[128]` plus six more fields, and while `decode_layer`
    reads all of them, what makes a given `path`/`key` pair resolve on a given
    install is the `.ani` index -- so a record invented here could name art
    the client cannot find, and would look authored. Copying a record that
    the map already draws means the art is known to resolve. Placing NEW art
    is a bigger feature and needs the Asset Viewer (T4) in front of it.

    `index` is the index within the WHOLE late list, as `late_layers` reports
    it, so it matches what an inspector shows. Non-cover records are never
    touched: an index naming one is refused rather than rewritten, because
    the origin offset above is the COVER layout and writing it into a scene
    or effect record would corrupt a different shape at a plausible offset.

    Read-only with respect to the install: this writes `mods/stage/` and
    nothing else.
    """
    if ack != ACK:
        raise NotAcknowledged(
            "map/map/*.DMap is listed in integrity.json. Editing it changes a "
            "file the client verifies. Pass the acknowledgement to proceed.")
    stage = Path(stage) if stage else art.lib.stage
    logical = art.dmap_logical
    staged = stage / logical
    if staged.is_file():
        raw = staged.read_bytes()
        base_src = f"{logical} (staged)"
    else:
        # The REGISTRY's map, not the loose leftover beside it -- same reason
        # `stage_passability` says so: 77 of 6609's 184 loose `.DMap` files
        # disagree with their `.7z` twin and 7878 ships none at all.
        raw, base_src = dmapmod.open_map(art.lib.root, art.name)
        if raw is None:
            raise FileNotFoundError(f"no readable map for {art.name}: {base_src}")

    # `parse` takes the bytes directly. This used to probe
    # `hasattr(dmapmod, "parse_bytes")` for a function that has never existed
    # -- `grep "def parse_bytes" core/dmap.py` returns 0 against a control of
    # `def parse` on its own line -- so the fallback ALWAYS ran, and the
    # fallback wrote a NamedTemporaryFile and read it back. A probe for a name
    # nobody ever wrote is a branch that reads as a choice and is not one.
    d = dmapmod.parse(art.dmap_path or Path(art.name), want_cells=False,
                      data=raw)
    if d is None or d.late_layer_offset < 0:
        # The limit is this editor's write path, not the map's format: a
        # pre-1006 map keeps its covers in the FIRST layer table, which
        # nothing here writes yet. Say that, not "it cannot carry a cover".
        raise ValueError(
            f"{art.name} has no v1006 second record list (version "
            f"{getattr(d, 'version', '?')}), and this editor writes covers "
            f"only there; the map may already hold covers in its first layer "
            f"table. See docs/map_cover_support.md for which maps this editor "
            f"can write.")

    spans = dmapmod.late_layer_spans(raw, d)
    if len(spans) != d.late_layer_count:
        raise ValueError("the record list does not walk cleanly; refusing to "
                         "edit (a splice at a wrong offset corrupts silently)")
    records = [bytearray(raw[a:b]) for a, b in spans]

    records, added, moved, removed, refused = apply_cover_edits(
        records, edits)
    final = [bytes(r) for r in records]
    data = dmapmod.splice_late_layers(raw, d, final)

    # PROVE IT BEFORE IT REACHES THE STAGE TREE, exactly as the cell-mask
    # path does. A spliced file whose count and body disagree walks past the
    # end or stops short, and this is the check that catches it.
    tmp = stage / (logical + ".verify")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        check = dmapmod.parse(tmp, want_cells=False)
        if check.late_layer_count != len(final):
            raise ValueError("spliced count %d != %d records written"
                             % (check.late_layer_count, len(final)))
        if len(check.late_layers) != len(final):
            raise ValueError("spliced file decodes %d records, wrote %d"
                             % (len(check.late_layers), len(final)))
        if check.bytes_unconsumed:
            raise ValueError("spliced file leaves %d byte(s) unconsumed"
                             % check.bytes_unconsumed)
    finally:
        try:
            tmp.unlink()
        except OSError:                                  # pragma: no cover
            pass

    dest = stage / logical
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {
        "logical": logical, "staged": str(dest), "bytes": len(data),
        "recordsBefore": len(spans), "recordsAfter": len(final),
        "added": added, "moved": moved, "removed": removed,
        # Refusals are reported, never silently dropped: a refusal the caller
        # cannot see is indistinguishable from an edit that worked.
        "refused": refused, "refusedCount": len(refused),
        "base": base_src,
        "note": ("The original was copied byte for byte apart from the second "
                 "record list and its count. Nothing has touched the game "
                 "install: run comod.py install to do that, and comod.py "
                 "uninstall to revert."),
        "integrity": art.lib.integrity.status(logical),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    coroot.add_root_argument(ap)
    ap.add_argument("map", nargs="?", help="a map name, e.g. newbie")
    ap.add_argument("--list", action="store_true", help="every GameMap.json row")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--editable", action="store_true",
                    help="which of this map's files are integrity-checked")
    ap.add_argument("--pick", default="", metavar="PX,PY",
                    help="what is drawn at this art pixel")
    ap.add_argument("--layer", default="", choices=("", *LAYERS))
    ap.add_argument("--png", type=Path, default=None,
                    help="render the whole map (all layers, or --layer) here")
    ap.add_argument("--z", type=int, default=8, help="integer decimation")
    ap.add_argument("--digest", action="store_true",
                   help="per-tile digests of --layer at --z, for comparing "
                        "this render against another one (an oracle, another "
                        "build, or this map before an edit). Hashes the RAW "
                        "RGBA, never the PNG.")
    ap.add_argument("--time-ms", type=int, default=0,
                   help="the clock terrain/cover sprites are sampled at; part "
                        "of the answer for those layers and recorded with it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    lib = MapEditor(Path(a.root) if getattr(a, "root", None) else None)
    if a.list or not a.map:
        rows = lib.rows()
        if a.json:
            print(json.dumps(rows, indent=1))
            return 0
        by = {}
        for r in rows:
            by[r["state"]] = by.get(r["state"], 0) + 1
        print(f"{len(rows)} GameMap.json rows: "
              + ", ".join(f"{v} {k}" for k, v in sorted(by.items())) + "\n")
        print(f"  {'map':<24}{'cells':>12}{'state':>10}  why")
        for r in rows:
            size = f"{r['width']}x{r['height']}" if r["width"] else "-"
            print(f"  {r['name']:<24}{size:>12}{r['state']:>10}  {r['why']}")
        return 0

    art = lib.get(a.map)
    # `--digest` has its own JSON, so it must not be swallowed by the
    # whole-map dump here. MEASURED as a defect: `--digest --json` printed
    # `to_json()` and the caller got a map summary where it asked for tile
    # digests -- valid JSON, wrong document, and nothing said so.
    if a.json and not (a.pick or a.editable or a.digest):
        print(json.dumps(art.to_json(), indent=1))
        return 0
    if a.editable:
        rep = art.editable_report()
        if a.json:
            print(json.dumps(rep, indent=1))
            return 0
        print(f"{rep['name']}: {rep['freeCount']} files free to edit, "
              f"{rep['checkedCount']} integrity-checked "
              f"(manifest: {rep['manifestRows']} entries naming "
              f"{rep['manifestFiles']} distinct files)\n")
        for r in rep["checked"]:
            print(f"  CHECKED  {r['path']:<44} {r['what']}")
        for r in rep["free"][:24]:
            print(f"  free     {r['path']:<44} {r['what']}")
        if len(rep["free"]) > 24:
            print(f"  ... {len(rep['free']) - 24} more free files")
        return 0
    if a.pick:
        px, py = (float(v) for v in a.pick.split(","))
        print(json.dumps(art.pick(px, py), indent=1))
        return 0

    j = art.to_json()
    # `--digest` prints its own header; the info block would put a map
    # summary in front of it for no reason.
    if a.info or not (a.png or a.digest):
        print(f"{art.name}  {j['mapSize'][0]}x{j['mapSize'][1]} cells   "
              f"{j['sceneParts']} scene parts, {j['covers']} covers")
        if not j["ok"]:
            print(f"  no ground art: {j['why']}")
        else:
            print(f"  puzzle   : {j['puzzle']}  {j['pul'][0]}x{j['pul'][1]} "
                  f"tiles of {j['gridSize']}px")
            print(f"  painted  : {j['pixels'][0]}x{j['pixels'][1]} px  "
                  f"({j['tiles'][0]}x{j['tiles'][1]} tiles at z=1)")
            print(f"  origin K : {j['originCells']}   consistent={j['consistent']}")
            print(f"  backdrops: {[b['path'] for b in j['backdrops']] or 'none'}")
        print(f"  .DMap    : {j['dmap']}  integrity-checked="
              f"{j['integrity']['checked']}")
    if a.digest:
        if art.pm is None:
            print(f"cannot render {art.name}: {art.reason}", file=sys.stderr)
            return 1
        z = max(1, a.z)
        layers = (a.layer,) if a.layer else LAYERS
        out = [art.layer_digests(name, z, time_ms=a.time_ms)
               for name in layers]
        if a.json:
            print(json.dumps(out if len(out) > 1 else out[0], indent=2))
        else:
            d0 = out[0]
            print(f"{art.name}  {d0['pixels'][0]}x{d0['pixels'][1]} px  "
                  f"z={z}  tile={d0['tile']}  "
                  f"grid={d0['grid'][0]}x{d0['grid'][1]}")
            print(f"  .DMap {d0['dmap']}  integrity-checked="
                  f"{d0['integrityChecked']}")
            for d in out:
                n = len(d["tiles"])
                print(f"  {d['layer']:<12} {d['drawn']:>6} drawn, "
                      f"{d['blank']:>6} blank, of {n}"
                      + ("   <-- ENTIRELY BLANK: this layer draws nothing on "
                         "this map at this zoom, so its digests compare "
                         "equal to any other blank layer's"
                         if d["drawn"] == 0 else ""))
            # The digests themselves only on request: a 34,944x22,400 map at
            # z=1 is 19,096 tiles per layer and nobody reads that at a
            # terminal. `--json` is the machine path and is what a comparator
            # consumes.
            if not a.json:
                print("  (--json for the digests themselves)")
        return 0
    if a.png:
        if art.pm is None:
            print(f"cannot draw {art.name}: {art.reason}", file=sys.stderr)
            return 1
        pm = art.pm
        z = max(1, a.z)
        rect = (0, 0, pm.px_w, pm.px_h)
        order = (a.layer,) if a.layer else LAYERS
        w = h = 0
        buf = None
        for name in order:
            lw, lh, rgba = art.render_layer(name, rect, z)
            if buf is None:
                w, h, buf = lw, lh, bytearray(rgba)
            else:
                _blit_rgba(buf, w, h, rgba, lw, lh, 0, 0)
        a.png.parent.mkdir(parents=True, exist_ok=True)
        a.png.write_bytes(encode_png_rgba(w, h, bytes(buf)))
        print(f"  -> {a.png}  {w}x{h}  (z={z}, layers {', '.join(order)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
