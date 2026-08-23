#!/usr/bin/env python3
r"""
puzzle.py -- where a map's painted ground art sits on the cell grid.

    py -3 tools/puzzle.py --verify                 # the rule, against all 136 maps
    py -3 tools/puzzle.py newbie --info
    py -3 tools/puzzle.py newbie --at 61,109 --radius 24 -o out/viewer/shots/newbie.png
    py -3 tools/puzzle.py newbie --full --scale 4 -o out/viewer/shots/newbie-full.png

THE PLACEMENT RULE
------------------
A map's background is one big painted image, cut into ``PuzzleGridSize``-pixel
squares and stored as tile indices in ``map/puzzle/<name>.pul``.  Let

    G    = ini/GameMap.json[mapId].PuzzleGridSize        (256 on 134 maps, 128 on 22)
    PxW  = pul.width  * G                                the painted image, in pixels
    PxH  = pul.height * G
    K    = PxW / 64                                      the origin, in cells

then a cell-grid **lattice point** (gx, gy) -- the shared corner of four cells --
lands on the painted image at

    px = (gx - gy + K) * 32
    py = (gx + gy - K) * 16

so **one map cell is a 64 x 32 pixel diamond**, the classic TQ isometric tile,
and the cell axes run along the image's diagonals.  Cell (x, y) is the diamond
with corners at lattice points (x,y), (x+1,y), (x+1,y+1), (x,y+1); its centre is
therefore at ``((x-y+K)*32, (x+y-K+1)*16)``.

The origin K is not stored anywhere.  It is forced by the geometry: rotating the
painted rectangle into the cell lattice puts its top-left corner K cells down the
y axis, and that is the only unknown, because the DMap's own dimensions pin it:

    W = H = PxW/64 + PxH/32                              (the map is the bounding
                                                          box of the rotated image)

**That identity holds exactly on 131 of the 132 maps that have a `.pul`** -- see
``--verify`` -- which is what makes the rule a recovered fact rather than a fit.
It also explains the two things that made the placement look unrecoverable:

  * *"the puzzle-to-cell ratio ranges 2.4 to 35.6 with no pattern"* -- that ratio
    is just the painted image's aspect ratio, and the aspect ratio is free.
    ``PxW/64 + PxH/32`` is the invariant; ``PxW/W`` is not.
  * *"every DMap is square"* -- of course it is.  The bounding box of a rectangle
    rotated into a lattice whose axes are its own diagonals has equal extents on
    both axes, whatever the rectangle's shape.  All 136 maps are square because
    they are all derived this way.

A consequence worth knowing before you look at a map: **the painted image is a
diamond inside the cell grid, so a large minority of every map's cells are off
the art entirely** and are always blocked.  On `newbie` the art occupies 8,640 of
the 17,424 cells.

VERIFIED / INFERRED
-------------------
See ``docs/ground_art.md``.  In short: the arithmetic above is VERIFIED against
the shipped corpus (131/132 maps, and 0 of 174,000 walkable cells on nine
different maps land off the painted image); the client code that computes it
lives in the Themida-packed ``ImConquer.exe`` and was not read.  The half-cell
phase -- whether a cell index names a diamond's corner or its centre -- is
INFERRED from the art's own alpha channel (``--phase``).

Read-only.  Nothing here writes to the game install.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
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
from coassets import AssetRoot, DMap, Pul, PUL_EMPTY      # noqa: E402
import dds                                               # noqa: E402

try:                                    # speed only -- see dds.py's docstring
    import numpy as _np
except Exception:                                        # pragma: no cover
    _np = None

#: Pixels per cell along the image's x axis. VERIFIED by the corpus identity.
CELL_PX_W = 64
#: Pixels per cell along the image's y axis. Half of CELL_PX_W: 2:1 isometric.
CELL_PX_H = 32
#: An empty slot in a .pul -- no tile painted there.  Re-exported, not
#: redeclared: the constant is a property of the `.pul` format and now lives
#: with the reader (`coassets.PUL_EMPTY`), because a second consumer that did
#: not know it treated 0xFFFF as a reference to tile 65535.  The name stays
#: here so `puzzle.EMPTY` keeps meaning what it always did.
EMPTY = PUL_EMPTY
#: What an unpainted pixel becomes when the art is flattened to RGB. Deliberately
#: near-black rather than a grass green: off-art is *void*, not ground.
VOID = (12, 12, 14)


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------

@dataclass
class PuzzleMap:
    """One map's painted background, placed on its cell grid."""

    name: str
    map_id: Optional[int]
    #: Cell-grid dimensions from the .DMap. Always square in this build.
    map_width: int
    map_height: int
    #: ini/GameMap.json PuzzleGridSize -- the tile edge in pixels.
    grid: int
    pul_w: int
    pul_h: int
    tiles: list = field(repr=False, default_factory=list)
    ani: str = ""
    pul_path: str = ""
    #: idx -> logical .dds path, resolved through the .ani. Filled lazily.
    frames: dict = field(repr=False, default_factory=dict)
    assets: Optional[AssetRoot] = field(repr=False, default=None)
    _tile_rgb: dict = field(repr=False, default_factory=dict)

    # -- geometry ----------------------------------------------------------

    @property
    def px_w(self) -> int:
        return self.pul_w * self.grid

    @property
    def px_h(self) -> int:
        return self.pul_h * self.grid

    @property
    def k(self) -> int:
        """The origin, in cells: the painted image's top-left corner sits at
        lattice point (0, K). ``PxW`` is a multiple of 64 on every shipped map,
        so this is exact."""
        return self.px_w // CELL_PX_W

    @property
    def implied_size(self) -> float:
        """The map size the art implies: ``PxW/64 + PxH/32``."""
        return self.px_w / CELL_PX_W + self.px_h / CELL_PX_H

    @property
    def consistent(self) -> bool:
        """True when the .DMap's own dimensions agree with the art's."""
        return (self.map_width == self.map_height == self.implied_size
                and self.px_w % CELL_PX_W == 0 and self.px_h % CELL_PX_H == 0)

    def corner_px(self, gx: float, gy: float) -> tuple[float, float]:
        """Lattice point (the shared corner of four cells) -> image pixel."""
        return ((gx - gy + self.k) * (CELL_PX_W // 2),
                (gx + gy - self.k) * (CELL_PX_H // 2))

    def cell_px(self, x: float, y: float) -> tuple[float, float]:
        """Cell (x, y) -> the pixel at the centre of its diamond."""
        return self.corner_px(x + 0.5, y + 0.5)

    def px_to_cell(self, px: float, py: float) -> tuple[float, float]:
        """Image pixel -> continuous lattice coordinates. The exact inverse of
        ``corner_px``; ``floor`` the pair to get the cell the pixel is inside,
        because cell (i, j) is the diamond spanned by lattice points
        (i..i+1, j..j+1)."""
        return (px / CELL_PX_W + py / CELL_PX_H,
                py / CELL_PX_H - px / CELL_PX_W + self.k)

    def window_rect(self, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
        """Pixel bounding box of the cell rectangle [x0..x1] x [y0..y1].

        The four extremes are corners of the rectangle, because the projection
        is linear: leftmost at (x0, y1+1), rightmost at (x1+1, y0), top at
        (x0, y0), bottom at (x1+1, y1+1).
        """
        left = self.corner_px(x0, y1 + 1)[0]
        right = self.corner_px(x1 + 1, y0)[0]
        top = self.corner_px(x0, y0)[1]
        bottom = self.corner_px(x1 + 1, y1 + 1)[1]
        return int(left), int(top), int(right), int(bottom)

    # -- art ---------------------------------------------------------------

    def tile_at(self, i: int, j: int) -> int:
        if not (0 <= i < self.pul_w and 0 <= j < self.pul_h):
            return EMPTY
        return self.tiles[j * self.pul_w + i]

    def tile_path(self, idx: int) -> str:
        """The .dds behind a tile index, via the .ani. Frame 0 of an animated
        tile -- a few water tiles have several and we do not animate the
        ground."""
        return self.frames.get(idx, "")

    def _tile_pixels(self, idx: int) -> Optional[bytes]:
        """One tile as ``grid*grid*3`` RGB bytes, alpha flattened onto VOID.
        Cached: a map reuses a few dozen tiles thousands of times."""
        if idx in self._tile_rgb:
            return self._tile_rgb[idx]
        out = None
        path = self.tile_path(idx)
        if path and self.assets is not None:
            try:
                w, h, rgba = dds.decode(self.assets.read(path))
                out = _flatten(rgba, w, h, self.grid)
            except Exception:                            # noqa: BLE001
                out = None
        self._tile_rgb[idx] = out
        return out

    def render(self, rect: tuple[int, int, int, int], *, scale: int = 1) -> tuple[int, int, bytes]:
        """Flatten the painted image over a pixel rectangle to RGB bytes.

        `rect` is (x0, y0, x1, y1) in image pixels, x1/y1 exclusive; it may run
        off the image, and off-image is VOID like an unpainted tile. `scale` is
        an integer decimation -- 1 is native, 4 is a thumbnail.
        """
        x0, y0, x1, y1 = rect
        scale = max(1, int(scale))
        ow = max(1, (x1 - x0 + scale - 1) // scale)
        oh = max(1, (y1 - y0 + scale - 1) // scale)
        buf = bytearray(bytes(VOID) * (ow * oh))
        g = self.grid
        stride3 = 3 * scale

        for oy in range(oh):
            py = y0 + oy * scale
            j, ty = divmod(py, g)
            if not (0 <= j < self.pul_h):
                continue
            rowbase = oy * ow * 3
            ox = 0
            px = x0
            while ox < ow:
                i, tx = divmod(px, g)
                # How many output pixels stay inside tile column i.
                room = (g - tx + scale - 1) // scale
                n = min(room, ow - ox)
                if 0 <= i < self.pul_w:
                    idx = self.tiles[j * self.pul_w + i]
                    if idx != EMPTY:
                        src = self._tile_pixels(idx)
                        if src is not None:
                            seg = src[(ty * g + tx) * 3:(ty * g + tx + (n - 1) * scale + 1) * 3]
                            o = rowbase + ox * 3
                            if scale == 1:
                                buf[o:o + n * 3] = seg
                            else:
                                buf[o + 0:o + n * 3:3] = seg[0::stride3][:n]
                                buf[o + 1:o + n * 3:3] = seg[1::stride3][:n]
                                buf[o + 2:o + n * 3:3] = seg[2::stride3][:n]
                ox += n
                px += n * scale
        return ow, oh, bytes(buf)

    def png(self, rect, *, scale: int = 1) -> bytes:
        w, h, rgb = self.render(rect, scale=scale)
        return encode_png(w, h, rgb)

    # -- reporting ---------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "name": self.name, "mapId": self.map_id,
            "puzzle": self.pul_path, "ani": self.ani,
            "gridSize": self.grid,
            "pul": [self.pul_w, self.pul_h],
            "pixels": [self.px_w, self.px_h],
            "cellPixels": [CELL_PX_W, CELL_PX_H],
            "originCells": self.k,
            "mapSize": [self.map_width, self.map_height],
            "impliedSize": self.implied_size,
            "consistent": self.consistent,
            "paintedTiles": sum(1 for t in self.tiles if t != EMPTY),
            "tileSlots": len(self.tiles),
        }


def _flatten(rgba: bytes, w: int, h: int, grid: int) -> bytes:
    """RGBA -> RGB over VOID, resampled to grid*grid if the tile is not already
    that size (every shipped puzzle tile is, but a modder's need not be)."""
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8).reshape(h, w, 4)
        if (w, h) != (grid, grid):
            yi = (_np.arange(grid) * h // grid).clip(0, h - 1)
            xi = (_np.arange(grid) * w // grid).clip(0, w - 1)
            a = a[yi][:, xi]
        alpha = a[:, :, 3:4].astype(_np.uint16)
        bg = _np.array(VOID, dtype=_np.uint16)
        rgb = (a[:, :, :3].astype(_np.uint16) * alpha
               + bg * (255 - alpha) + 127) // 255
        return rgb.astype(_np.uint8).tobytes()
    out = bytearray(grid * grid * 3)
    for y in range(grid):
        sy = y * h // grid
        for x in range(grid):
            sx = x * w // grid
            s = (sy * w + sx) * 4
            al = rgba[s + 3]
            o = (y * grid + x) * 3
            if al == 255:
                out[o:o + 3] = rgba[s:s + 3]
            else:
                for c in range(3):
                    out[o + c] = (rgba[s + c] * al + VOID[c] * (255 - al)) // 255
    return bytes(out)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal 8-bit RGB PNG, same encoder shape as tools/terrain.py's."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

# `_parse_ani` stood here as a shim over `core/dmap.read_ani`, kept "so this
# module's own callers are unchanged". Its last caller was `_ani_table`
# below, and `mapparts` reached across for it -- which is how `mapparts`
# inherited the half `read_ani` does NOT answer: which of the two spellings
# to open. Both now call `dmap.load_ani`, so the shim has no callers and the
# reach-across has nowhere to land (C-2026-08-09-ani-json-spelling).


class PuzzleLibrary:
    """`GameMap.json` + `map/map/*.DMap` + `map/puzzle/*.pul`, joined.

    Degrades the way `client/gamemap.MapLibrary` does: with no install, every
    lookup returns None and `.reason` says why. Nothing here is a hard
    dependency of the client.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.reason = ""
        self._root: Optional[Path] = Path(root) if root else None
        if self._root is None:
            try:
                found = coroot.find()
                self._root = Path(found.path) if found else None
            except Exception as e:                       # noqa: BLE001
                self.reason = f"could not resolve the game install: {e}"
        self._grid_size: dict[str, int] = {}
        self._doc_id: dict[str, int] = {}
        #: Which registry actually answered ("ini/GameMap.dat", "" for none).
        #: Kept so a message about a missing row can name the file that was
        #: read rather than the one this module used to assume.
        self._registry_rel = ""
        #: Lazily counted by `_unregistered_count`, per install, never quoted.
        self._unregistered: Optional[int] = None
        #: DocumentId -> file stem. See by_id.
        self._id_stem: dict[int, str] = {}
        self._assets: Optional[AssetRoot] = None
        self._ani: dict[str, dict] = {}
        self._cache: dict[str, Optional[PuzzleMap]] = {}
        self._load_gamemap()

    @property
    def root(self) -> Optional[Path]:
        return self._root

    def _unregistered_count(self) -> int:
        """Shipped `.DMap` files with no row in this install's registry.

        Computed once, on the install that is loaded, because the number
        varies by client and a quoted one is a different install's fact:
        **MEASURED 2026-08-11 -- 5517: 21 of 181, 6090: 21 of 184, 6609: 20 of
        184.** Those are recorded here as provenance, not consulted.
        """
        if self._unregistered is None:
            self._unregistered = 0
            d = (self._root / "map" / "map") if self._root else None
            if d and d.is_dir():
                self._unregistered = sum(
                    1 for p in d.iterdir()
                    if p.suffix.lower() == ".dmap"
                    and p.stem.lower() not in self._grid_size)
        return self._unregistered

    def _load_gamemap(self) -> None:
        if self._root is None:
            self.reason = self.reason or "no game install found (core/coroot.py)"
            return
        # Official clients ship only the binary index. Both the reader and the
        # CHOICE between the two spellings are COre's -- this module and
        # `mapindex` each hand-rolled the choice, `mapedit` had none at all,
        # and a third copy is how one of them stays wrong (C-2026-08-09-ani-json-spelling). It lives in
        # COre so that sharing it does not make this module depend on
        # `client/`, which is not extracted into COMod.
        from dmap import load_gamemap                    # noqa: PLC0415
        p = self._root / "ini" / "GameMap.json"
        q = self._root / "ini" / "GameMap.dat"
        self._registry_rel, rows = load_gamemap(self._root)
        if not rows:
            # ABSENT and UNPARSEABLE are different answers and the caller acts
            # on `reason`, so they are not collapsed into one string.
            present = [str(x) for x in (p, q) if x.is_file()]
            self.reason = (f"{' and '.join(present)} did not parse"
                           if present else
                           f"{p} is missing, and so is {q}")
            return
        for r in rows:
            stem = Path(str(r.get("FileName", "")).replace("\\", "/")).stem.lower()
            if not stem:
                continue
            try:
                self._grid_size[stem] = int(r["PuzzleGridSize"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                self._doc_id[stem] = int(r["DocumentId"])
                # ...and the other way round, which is the direction by_id
                # actually needs. Many ids share one stem, never the reverse.
                self._id_stem[int(r["DocumentId"])] = stem
            except (KeyError, TypeError, ValueError):
                pass

    @property
    def assets(self) -> Optional[AssetRoot]:
        """The asset namespace, for reading tile textures out of data.wdf."""
        if self._assets is None and self._root is not None:
            try:
                self._assets = AssetRoot(self._root)
            except Exception as e:                       # noqa: BLE001
                self.reason = f"assets unavailable: {e}"
        return self._assets

    def names(self) -> list[str]:
        """Every map the install has, not every loose `.DMap` it happens to
        leave lying about.

        MEASURED 2026-08-11 -- a `*.DMap` glob against the union of registry,
        archives and loose files:

            5517   181 -> 192     the 11 that ship only as .7z
            6090   184 -> 247     63 archive-only
            6609   184 -> 306     113 archive-only, plus 9 registry rows
                                  whose maps ship in neither form

        `core/dmap.map_names` is the one place that union is computed.
        """
        if self._root is None:
            return []
        from dmap import map_names                          # noqa: PLC0415
        return map_names(self._root)

    def _dmap_bytes(self, name: str) -> tuple[Optional[bytes], str]:
        """One map's `.DMap` content, from wherever the client reads it.

        **Not the loose file.** From 5517 on the registry names
        ``map/map/<stem>.7z`` and the loose `.DMap` beside it is a leftover
        that drifts: 77 of 6609's 184 disagree with their own archive, and the
        two this used to feed `PuzzlePlacement` -- `icecrypt-lev6` at 808x808
        against its archive's 740x740, `poker` at 188x188 against 232x232 --
        are why a corpus-wide identity measured over loose files failed on
        exactly two maps. See `C-2026-08-10-claude-explorer-map-archive-alarm`
        and `docs/map_twin_precedence.md`.

        Returns ``(bytes, source)``; the source phrase says which rule chose
        the file and travels into `reason` when something downstream fails.
        """
        assert self._root is not None
        from dmap import open_map                           # noqa: PLC0415
        return open_map(self._root, name)

    def _ani_table(self, ani_path: str) -> dict:
        stem = Path(ani_path.replace("\\", "/")).stem.lower()
        if stem in self._ani:
            return self._ani[stem]
        assert self._root is not None
        # The pre-parsed .json is what a community client ships; every
        # official client ships only the raw .ani, INI-shaped:
        #     [Puzzle72]  FrameAmount=1  Frame0=data/.../canyon072.dds
        # Without the second spelling every tile resolves to "" and the
        # ground renders completely blank, which is exactly how it failed.
        # The resolution itself is COre's (`dmap.load_ani`) rather than this
        # module's, because by the time it was written here it had been
        # written in `mapindex` too and was still MISSING from `mapparts` --
        # a third copy is how one of them stays wrong (C-2026-08-09-ani-json-spelling).
        from dmap import load_ani                         # noqa: PLC0415
        _rel, out = load_ani(self._root, f"{stem}.ani")
        self._ani[stem] = out
        return out

    def by_id(self, map_id: int) -> Optional[PuzzleMap]:
        # Search the index by ID, not by scanning a stem->id map.
        #
        # `_doc_id` is keyed by FILE STEM, and 40 stems are shared by several
        # map ids because maps reuse art -- `newbie` is both 1010 and 1035,
        # `forum` is four ids. Keyed that way the later row wins and every
        # earlier id silently vanishes: 262 rows collapse to 180 entries, and
        # by_id(1010) reported "not in GameMap.json" for a map that is plainly
        # in it. Latent on any install; the binary index only made it visible.
        want = int(map_id)
        for stem, doc in self._id_stem.items():
            if stem == want:
                return self.get(doc)
        self.reason = f"map id {map_id} is not in the map index"
        return None

    def get(self, name: str) -> Optional[PuzzleMap]:
        """Everything needed to place and draw one map's ground art."""
        key = str(name).lower()
        if key in self._cache:
            return self._cache[key]
        pm = self._build(name)
        self._cache[key] = pm
        return pm

    def _build(self, name: str) -> Optional[PuzzleMap]:
        if self._root is None:
            self.reason = self.reason or "no game install found (core/coroot.py)"
            return None
        raw, src = self._dmap_bytes(name)
        if raw is None:
            self.reason = src
            return None
        try:
            m = DMap.parse(raw)
        except Exception as e:                           # noqa: BLE001
            self.reason = f"{src} did not parse: {e}"
            return None
        rel = m.puzzle_path.replace("\\", "/")
        pul = self._root / rel
        if not pul.is_file():
            self.reason = f"{src} names {rel}, which is not present"
            return None
        if pul.suffix.lower() != ".pul":
            # map/PuzzleSave/*.pux is "TqTerrain", a different and undecoded
            # format. Four maps use it on 5517; 12 do on 6609, where the
            # map/PuzzleSave/*.pux is "TqTerrain".  Its HEADER is decoded --
            # `dmap.read_pux` gives the puzzle's tile dimensions and
            # `dmap.PUX_GRID` the 256-pixel tile the placement identity solves
            # for on all 160 maps that name one -- but the tile PAYLOAD is not:
            # 20.8 to 172.2 bytes per tile, so it is not a compiled index.
            # Say what is known, because "not decoded" sent every reader back
            # to the format when the geometry was already in hand.
            from dmap import read_pux, PUX_GRID          # noqa: PLC0415
            hdr = read_pux(pul)
            if hdr:
                self.reason = (
                    f"{src} uses {rel} -- TqTerrain (.pux).  Its geometry IS "
                    f"known: {hdr['width']}x{hdr['height']} tiles at "
                    f"{PUX_GRID} px, implying a "
                    f"{(hdr['width']*PUX_GRID)//64 + (hdr['height']*PUX_GRID)//32}"
                    f"-cell map.  What is missing is the tile payload, which "
                    f"is not a compiled index (see dmap.read_pux)")
            else:
                self.reason = (f"{src} uses {rel} -- the TqTerrain (.pux) "
                               f"format, and its header did not read")
            return None
        try:
            z = Pul.load(pul)
        except Exception as e:                           # noqa: BLE001
            self.reason = f"{rel} did not parse: {e}"
            return None
        stem = str(name).lower()
        g = self._grid_size.get(stem)
        if g is None:
            # Two defects used to wear this one string, and it named the wrong
            # file for both.  The registry is `GameMap.json` on the community
            # client and the binary `GameMap.dat` on every official one, and no
            # install ships both -- so this message sent every official-client
            # reader to a file they do not have.  And it blamed a *container*
            # gap for what is a genuine *absence*: reading the other spelling
            # recovers the maps that HAVE a row and cannot recover the ones
            # that have none, so a reader who fixes the spelling and still sees
            # this must be told which of the two they are looking at.
            #
            # **The count is measured here, not quoted.**  Its first form said
            # "21 of 5517's 181 shipped maps have none" -- a 5517 fact, printed
            # verbatim at anyone running 6090, which is the same defect one
            # install over as naming the wrong registry file.  It is now
            # counted against the install that is actually loaded.
            self.reason = (f"{name} has no row in {self._registry_rel} "
                           f"({self._unregistered_count()} of this install's "
                           f"shipped maps have none), so no PuzzleGridSize"
                           if self._registry_rel else
                           f"{name} has no PuzzleGridSize: this install "
                           f"ships no map registry at all (neither "
                           f"ini/GameMap.json nor ini/GameMap.dat)")
            return None
        pm = PuzzleMap(
            name=str(name), map_id=self._doc_id.get(stem),
            map_width=m.width, map_height=m.height,
            grid=g, pul_w=z.width, pul_h=z.height, tiles=z.tiles,
            ani=rel_ani(z.ani_path), pul_path=rel.lower(),
            assets=self.assets,
        )
        table = self._ani_table(z.ani_path)
        for idx in set(z.tiles):
            if idx == EMPTY:
                continue
            v = table.get(f"Puzzle{idx}")
            if isinstance(v, str):
                v = [v]
            if v:
                pm.frames[idx] = str(v[0]).replace("\\", "/").lstrip("/").lower()
        self.reason = ""
        return pm


    # -- backdrops ---------------------------------------------------------

    def backdrops(self, name: str) -> list["Backdrop"]:
        """The map's background puzzle layers, furthest first.

        WHERE THEY COME FROM -- VERIFIED. A `.DMap`'s trailing section (the
        `extra` array `core/dmap.py` decodes: six `u32` then a `char[260]`
        path) is the background list. 57 of the 136 shipped maps carry 109 such
        records between them and every well-formed one names a
        `map/puzzle/*.pul`. `newbie` names `newbiebg.pul`, which nothing in the
        header references -- which is why the ground pipeline never loaded it.

        WHAT THE SIX INTEGERS ARE. values[0] is VERIFIED as a draw index:
        `2009-7x` and `beach` each carry two, numbered 0 and 1, and 0 is the
        further one in both. values[2..3] are INFERRED to be a parallax
        percentage -- over the 57 well-formed records they are 30/30 (42x),
        50/50, 100/100, 40/40, 20/20, 10/10, and five mismatched pairs; the
        range is 10..100 and never above it, which is what a percentage looks
        like and what a pixel or tile count does not. values[1] is 4 on every
        record and values[5] is 8 on every record; values[4] is 1 on 47 of 57
        and 7..16 on the rest. None of those three is named here.
        """
        out: list[Backdrop] = []
        if self._root is None:
            return out
        raw, _src = self._dmap_bytes(name)
        if raw is None:
            return out
        try:
            import dmap as dmapmod                          # noqa: PLC0415
            d, _why = dmapmod.parse_map(self._root, name,
                                        want_cells=False, verify=False)
            if d is None:
                return out
        except Exception:                                   # noqa: BLE001
            return out
        g = self._grid_size.get(str(name).lower()) or 256
        for e in d.extra:
            rel = str(e.get("path", "")).replace("\\", "/")
            # Three maps have a desynchronised trailer; a record whose path is
            # not a .pul that exists is debris, not a backdrop.
            if not rel.lower().endswith(".pul"):
                continue
            p = self._root / rel
            if not p.is_file():
                continue
            try:
                z = Pul.load(p)
            except Exception:                               # noqa: BLE001
                continue
            v = list(e.get("values", []))
            pm = PuzzleMap(name=Path(rel).stem, map_id=None,
                           map_width=d.width, map_height=d.height,
                           grid=g, pul_w=z.width, pul_h=z.height, tiles=z.tiles,
                           ani=rel_ani(z.ani_path), pul_path=rel.lower(),
                           assets=self.assets)
            table = self._ani_table(z.ani_path)
            for idx in set(z.tiles):
                if idx == EMPTY:
                    continue
                t = table.get(f"Puzzle{idx}")
                if isinstance(t, str):
                    t = [t]
                if t:
                    pm.frames[idx] = str(t[0]).replace("\\", "/").lstrip("/").lower()
            out.append(Backdrop(art=pm, index=v[0] if v else 0,
                                parallax=(v[2] if len(v) > 2 else 100,
                                          v[3] if len(v) > 3 else 100),
                                roll=tuple(z.roll_speed or (0, 0)),
                                values=v, path=rel.lower()))
        out.sort(key=lambda b: -b.index)
        return out


@dataclass
class Backdrop:
    """One background puzzle plane behind a map's ground art."""

    art: PuzzleMap
    index: int = 0
    #: Percent of the ground's own scroll this plane follows. INFERRED.
    parallax: tuple[int, int] = (100, 100)
    #: `rollSpeedX/Y` from the .pul -- a scrolling backdrop. Not animated here.
    roll: tuple[int, int] = (0, 0)
    values: list = field(default_factory=list)
    path: str = ""

    def sample_rect(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Which piece of this plane sits behind a ground rectangle.

        The plane is smaller than the map (12x40 tiles against a 132-cell map is
        typical), so it tiles; `render_tiled` does the wrapping and this only
        applies the parallax shift. INFERRED -- see `backdrops()`.
        """
        px, py = self.parallax
        x0, y0, x1, y1 = rect
        return (int(x0 * px / 100), int(y0 * py / 100),
                int(x0 * px / 100) + (x1 - x0), int(y0 * py / 100) + (y1 - y0))

    def render_tiled(self, rect: tuple[int, int, int, int], *, scale: int = 1):
        """The plane behind `rect`, wrapped in both axes so it never runs out.

        Returns (w, h, rgb) like `PuzzleMap.render`.
        """
        sx0, sy0, sx1, sy1 = self.sample_rect(rect)
        pw, ph = max(1, self.art.px_w), max(1, self.art.px_h)
        ow = max(1, (sx1 - sx0 + scale - 1) // scale)
        oh = max(1, (sy1 - sy0 + scale - 1) // scale)
        out = bytearray(bytes(VOID) * (ow * oh))
        # Draw the wrapped copies that intersect the window. A backdrop is at
        # most a few hundred pixels across, so this is a handful of renders.
        ty0 = (sy0 // ph) * ph
        tx0 = (sx0 // pw) * pw
        oy = ty0
        while oy < sy1:
            ox = tx0
            while ox < sx1:
                cx0, cy0 = max(sx0, ox), max(sy0, oy)
                cx1, cy1 = min(sx1, ox + pw), min(sy1, oy + ph)
                if cx1 > cx0 and cy1 > cy0:
                    w, h, rgb = self.art.render(
                        (cx0 - ox, cy0 - oy, cx1 - ox, cy1 - oy), scale=scale)
                    dx = (cx0 - sx0) // scale
                    dy = (cy0 - sy0) // scale
                    for r in range(min(h, oh - dy)):
                        n = min(w, ow - dx) * 3
                        if n <= 0:
                            break
                        d = ((dy + r) * ow + dx) * 3
                        out[d:d + n] = rgb[r * w * 3:r * w * 3 + n]
                ox += pw
            oy += ph
        return ow, oh, bytes(out)

    def to_json(self) -> dict:
        return {"path": self.path, "index": self.index,
                "parallax": list(self.parallax), "roll": list(self.roll),
                "pul": [self.art.pul_w, self.art.pul_h],
                "pixels": [self.art.px_w, self.art.px_h],
                "values": self.values}


def rel_ani(p: str) -> str:
    return p.replace("\\", "/").lstrip("/").lower()


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify(lib: Optional[PuzzleLibrary] = None) -> dict:
    """Check the placement identity ``W == H == PxW/64 + PxH/32`` on every
    shipped map, and report the exceptions by name."""
    lib = lib or PuzzleLibrary()
    rows = []
    for name in lib.names():
        pm = lib.get(name)
        if pm is None:
            rows.append({"name": name, "status": "skipped", "why": lib.reason})
            continue
        rows.append({
            "name": pm.name, "status": "ok" if pm.consistent else "mismatch",
            "map": [pm.map_width, pm.map_height],
            "pul": [pm.pul_w, pm.pul_h], "grid": pm.grid,
            "pixels": [pm.px_w, pm.px_h], "k": pm.k,
            "implied": pm.implied_size,
        })
    return {
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "mismatch": sum(1 for r in rows if r["status"] == "mismatch"),
        "skipped": sum(1 for r in rows if r["status"] == "skipped"),
        "rows": rows,
    }


def overlay_walkable(pm: PuzzleMap, grid, *, scale: int = 16,
                     colour=(255, 40, 40), step: int = 0) -> tuple[int, int, bytes]:
    """The whole painted image with every walkable cell dotted on it.

    This is the picture that settles the placement by eye: if the projection or
    the origin were wrong the dots would sit in the sea, or in the sky, or a
    constant distance from the paths they are supposed to be on.
    """
    w, h, rgb = pm.render((0, 0, pm.px_w, pm.px_h), scale=scale)
    buf = bytearray(rgb)
    step = step or max(1, scale // 8)
    for y in range(0, grid.height, step):
        for x in range(0, grid.width, step):
            if not grid.walkable(x, y):
                continue
            px, py = pm.cell_px(x, y)
            ox, oy = int(px) // scale, int(py) // scale
            if 0 <= ox < w and 0 <= oy < h:
                o = (oy * w + ox) * 3
                buf[o], buf[o + 1], buf[o + 2] = colour
    return w, h, bytes(buf)


def coverage(pm: PuzzleMap, grid, *, base_only: bool = False) -> dict:
    """How many of a map's walkable cells land on painted art.

    The sharp test of the placement: a cell you can stand on should be on the
    picture. `grid` is a `client.gamemap.GameMap`.

    **A cell a scene layer opens is a deliberate exception.** `newbie`'s
    stepping stones hang in the void precisely *because* the scenery is the
    ground there -- the painted image has nothing under them. So the counts are
    split: `base*` covers the cells the DMap's own grid calls walkable, and
    `scenery*` the ones only a `TERRAIN` layer opens (`tools/scene.py`).
    `base_only=True` ignores the scene-supplied cells entirely, which is the
    form the placement rule is judged on.
    """
    on = off_image = on_empty = 0
    s_on = s_off = s_empty = 0
    g = pm.grid
    for y in range(grid.height):
        for x in range(grid.width):
            if not grid.walkable(x, y):
                continue
            scenery = grid.from_scenery(x, y) if hasattr(grid, "from_scenery") else False
            if scenery and base_only:
                continue
            px, py = pm.cell_px(x, y)
            i, j = int(px) // g, int(py) // g
            if not (0 <= i < pm.pul_w and 0 <= j < pm.pul_h):
                bucket = "off"
            elif pm.tiles[j * pm.pul_w + i] == EMPTY:
                bucket = "empty"
            else:
                bucket = "on"
            if scenery:
                s_on += bucket == "on"
                s_off += bucket == "off"
                s_empty += bucket == "empty"
            else:
                on += bucket == "on"
                off_image += bucket == "off"
                on_empty += bucket == "empty"
    base_total = on + off_image + on_empty
    total = base_total + s_on + s_off + s_empty
    return {"walkable": total, "onArt": on + s_on,
            "offImage": off_image + s_off, "onEmptyTile": on_empty + s_empty,
            "fraction": ((on + s_on) / total) if total else 0.0,
            "baseWalkable": base_total, "baseOnArt": on,
            "baseOffImage": off_image, "baseOnEmptyTile": on_empty,
            "baseFraction": (on / base_total) if base_total else 0.0,
            "sceneryWalkable": s_on + s_off + s_empty, "sceneryOnArt": s_on,
            "sceneryOffImage": s_off, "sceneryOnEmptyTile": s_empty}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_verify(res: dict) -> None:
    print(f"{res['ok']} maps match the placement identity, "
          f"{res['mismatch']} do not, {res['skipped']} have no usable .pul\n")
    print(f"  {'map':<22}{'cells':>11}{'pul':>10}{'grid':>6}{'pixels':>14}"
          f"{'K':>7}{'implied':>9}")
    for r in res["rows"]:
        if r["status"] == "skipped":
            print(f"  {r['name']:<22}{'-':>11}{'':>10}{'':>6}{'':>14}{'':>7}"
                  f"{'':>9}  skipped: {r['why']}")
            continue
        flag = "" if r["status"] == "ok" else "   <-- MISMATCH"
        cells = "{}x{}".format(*r["map"])
        pul = "{}x{}".format(*r["pul"])
        pix = "{}x{}".format(*r["pixels"])
        print(f"  {r['name']:<22}{cells:>11}{pul:>10}{r['grid']:>6}{pix:>14}"
              f"{r['k']:>7}{r['implied']:>9.1f}{flag}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", nargs="?", help="a map name (newbie) or a DocumentId (1010)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--verify", action="store_true",
                    help="check the placement identity on every shipped map")
    ap.add_argument("--info", action="store_true", help="print the placement numbers")
    ap.add_argument("--coverage", action="store_true",
                    help="how many walkable cells land on painted art")
    ap.add_argument("--at", default="", metavar="X,Y", help="centre a window here")
    ap.add_argument("--radius", type=int, default=24)
    ap.add_argument("--full", action="store_true", help="the whole painted image")
    ap.add_argument("--overlay", action="store_true",
                    help="with --full: dot every walkable cell onto the art, which "
                         "is the by-eye proof of the placement")
    ap.add_argument("--scale", type=int, default=1, help="integer decimation")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-o", "--out", type=Path, default=None)
    a = ap.parse_args(argv)

    lib = PuzzleLibrary(Path(a.root) if a.root else None)
    if a.verify:
        res = verify(lib)
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            _print_verify(res)
        return 0 if res["mismatch"] <= 1 else 1

    if not a.map:
        ap.error("a map name is required unless --verify is given")

    try:
        pm = lib.by_id(int(a.map))
    except ValueError:
        pm = lib.get(a.map)
    if pm is None:
        print(f"no ground art: {lib.reason}", file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps(pm.to_json(), indent=1))
    else:
        j = pm.to_json()
        print(f"{pm.name}  id={pm.map_id}  {pm.map_width}x{pm.map_height} cells")
        print(f"  puzzle      : {pm.pul_path}  ({pm.pul_w}x{pm.pul_h} tiles of {pm.grid}px)")
        print(f"  ani         : {pm.ani}")
        print(f"  painted     : {pm.px_w}x{pm.px_h} px, {j['paintedTiles']}/{j['tileSlots']} tiles placed")
        print(f"  origin K    : {pm.k} cells   (image (0,0) is lattice point (0,{pm.k}))")
        print(f"  implied size: {pm.implied_size:g}   "
              f"{'== map size, consistent' if pm.consistent else '!= map size, MISMATCH'}")
        print(f"  cell (0,0) centre -> px {tuple(int(v) for v in pm.cell_px(0, 0))}")

    if a.coverage:
        from client import gamemap as GM                  # noqa: PLC0415
        g = GM.MapLibrary().load(pm.map_id) if pm.map_id else None
        if g is None:
            print("  coverage: no passability data", file=sys.stderr)
        else:
            c = coverage(pm, g)
            print(f"  coverage    : {c['onArt']}/{c['walkable']} walkable cells on "
                  f"painted art ({c['fraction']*100:.2f}%), "
                  f"{c['offImage']} off the image, {c['onEmptyTile']} on an empty tile")

    if a.out or a.full or a.at:
        if a.full:
            rect = (0, 0, pm.px_w, pm.px_h)
        else:
            if a.at:
                cx, cy = (int(v) for v in a.at.split(","))
            else:
                cx = cy = pm.map_width // 2
            x0 = max(0, cx - a.radius); y0 = max(0, cy - a.radius)
            x1 = min(pm.map_width - 1, cx + a.radius)
            y1 = min(pm.map_height - 1, cy + a.radius)
            rect = pm.window_rect(x0, y0, x1, y1)
        out = a.out or (_REPO / "out" / "viewer" / "shots" / f"{pm.name}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        if a.overlay and a.full:
            from client import gamemap as GM                # noqa: PLC0415
            g = GM.MapLibrary().load(pm.map_id) if pm.map_id else None
            if g is None:
                print("  --overlay needs passability data", file=sys.stderr)
                return 1
            w, h, rgb = overlay_walkable(pm, g, scale=max(1, a.scale))
            png = encode_png(w, h, rgb)
        else:
            png = pm.png(rect, scale=a.scale)
        out.write_bytes(png)
        w, h = struct.unpack(">II", png[16:24])
        print(f"  -> {out}  {w}x{h} px  (rect {rect}, scale {a.scale})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
