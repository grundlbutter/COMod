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
* **`EFFECT` and `SOUND` layers are not drawn.**  They are decoded
  (`core/dmap.py`) and reported in the map summary, and there is nothing to
  draw them as.
* **`map/PuzzleSave/*.pux` (`TqTerrain`) is undecoded**, so the four maps that
  use it have no ground art. Their grid, scenery and passability still draw.
"""

from __future__ import annotations

import argparse
import json
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
LAYERS = ("background", "ground", "terrain", "cover", "passability")

LAYER_TITLE = {
    "background": "Background planes",
    "ground": "Base puzzle (painted ground)",
    "terrain": "TERRAIN scene objects",
    "cover": "COVER sprites",
    "passability": "Passability grid",
}

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
        self._walk: Optional[tuple[bytes, bytes, object]] = None
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
        """Which `.DMap` the editor reads.

        **A staged copy wins**, exactly as a loose file wins over an archive
        in the game itself (`tools/comod.py`). So once you stage a passability
        edit the editor draws *your* grid, not the shipped one, and the
        difference between "staged" and "installed" stays visible rather than
        being something you have to remember.
        """
        p = self.dmap_path
        if p is None:
            return None
        staged = self.lib.stage / f"map/map/{p.name}"
        return staged if staged.is_file() else p

    @property
    def staged(self) -> bool:
        p = self.dmap_path
        return p is not None and (self.lib.stage / f"map/map/{p.name}").is_file()

    def invalidate_grid(self) -> None:
        """Forget the parsed cell grid -- called after staging an edit."""
        with self._lock:
            self._dmap_header = None
            self._dmap_cells = None
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
                p = self.read_path()
                if p is None:
                    # No loose .DMap: read it out of the .7z.
                    raw = self._archive_dmap_bytes()
                    if raw is None:
                        return None
                    self._dmap_header = dmapmod.parse(
                        self.archive_path, want_cells=False,
                        verify=False, data=raw)
                    return self._dmap_header
                self._dmap_header = dmapmod.parse(p, want_cells=False, verify=False)
            return self._dmap_header

    def cells(self):
        """The `.DMap` *with* its cell grid. 1.5M cells on `Gulf`, so this is
        only ever reached by the passability layer and by an inspector click."""
        with self._lock:
            if self._dmap_cells is None:
                p = self.read_path()
                if p is None:
                    raw = self._archive_dmap_bytes()
                    if raw is None:
                        return None
                    self._dmap_cells = dmapmod.parse(
                        self.archive_path, want_cells=True,
                        verify=False, data=raw)
                    return self._dmap_cells
                self._dmap_cells = dmapmod.parse(p, want_cells=True, verify=False)
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
                    self._scenery = scenemod.gather(d.layers, self.lib.scenes)
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
                self._items = {"scene": [], "cover": []}
                sc = self.scenery()
                if self.pm is not None:
                    for k, seq in (("scene", sc.sorted_scenes()),
                                   ("cover", sc.sorted_covers())):
                        for i, p in enumerate(seq):
                            self._items[k].append(self._describe(p, i))
            return self._items.get(kind, [])

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

    def render_ground(self, rect, scale: int) -> tuple[int, int, bytes]:
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
        return ow, oh, bytes(buf)

    def render_background(self, rect, scale: int) -> tuple[int, int, bytes]:
        """The background planes behind `rect`, furthest first, as RGBA.

        `puzzle.Backdrop.render_tiled` produces RGB over VOID; VOID is turned
        back into transparency here so a map with no background plane is a
        transparent layer rather than a black one.
        """
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = _blank(ow, oh)
        void = bytes(puzzlemod.VOID)
        for b in self.backdrops():
            try:
                bw, bh, brgb = b.render_tiled(tuple(rect), scale=scale)
            except Exception:                            # noqa: BLE001
                continue
            for i in range(0, min(bw * bh, ow * oh)):
                s = i * 3
                if brgb[s:s + 3] == void:
                    continue
                d = i * 4
                buf[d:d + 3] = brgb[s:s + 3]
                buf[d + 3] = 255
        return ow, oh, bytes(buf)

    def render_sprites(self, kind: str, rect, scale: int, *,
                       time_ms: int = 0) -> tuple[int, int, bytes]:
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
            key = (p.ani, p.title, n % len(fr), scale)
            hit = self._sprite_cache.get(key)
            if hit is None:
                hit = _decimate_rgba(rgba, sw, sh, scale)
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
                     time_ms: int = 0) -> tuple[int, int, bytes]:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer {layer!r}; have {', '.join(LAYERS)}")
        if layer == "background":
            return self.render_background(rect, scale)
        if layer == "ground":
            return self.render_ground(rect, scale)
        if layer == "terrain":
            return self.render_sprites("scene", rect, scale, time_ms=time_ms)
        if layer == "cover":
            return self.render_sprites("cover", rect, scale, time_ms=time_ms)
        return self.render_passability(rect, scale)

    def tile_png(self, layer: str, tx: int, ty: int, z: int, *,
                 time_ms: int = 0, tile: int = TILE) -> bytes:
        rect = tile_rect(tx, ty, z, tile=tile)
        w, h, rgba = self.render_layer(layer, rect, z, time_ms=time_ms)
        return encode_png_rgba(w, h, rgba)

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
                manifest, bundle = tilesetmod.build_full(
                    self.pm, sc, self.lib.sprites, self.backdrops())
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
            "missingScenes": sc.missing_scenes,
            "backdrops": [b.to_json() for b in self.backdrops()] if pm else [],
            "tile": TILE,
            "zooms": list(ZOOMS),
            "layers": [{"id": k, "title": LAYER_TITLE[k]} for k in LAYERS],
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
            row["state"] = "stale"
            row["why"] = ("this loose .DMap is not the one inside the .7z "
                          "beside it; the archive is what the client reads, "
                          "so this is a previous client's map")
            return row
        if row["puzzle"].endswith(".pux"):
            row["state"] = "pux"
            row["why"] = ("uses map/PuzzleSave/*.pux (TqTerrain), which is not "
                          "decoded -- the grid and the layers still draw")
            return row
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
    if a.json and not (a.pick or a.editable):
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
    if a.info or not a.png:
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
