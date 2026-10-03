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
from coassets import AssetRoot, DMap, Pul                 # noqa: E402
import dds                                               # noqa: E402
#: The `.pux` reader, for `pux_layer_mask` / `pux_mask_field`. Module level
#: rather than the lazy `from dmap import ...` the loader below uses, because
#: `_composite_rgba` runs per LAYER and an import inside it would be a dict
#: lookup per layer on every tile of every stacked map. `dmap` imports only
#: `coroot` from this tree, so there is no cycle back to here.
import dmap as _dmap                                     # noqa: E402

try:                                    # speed only -- see dds.py's docstring
    import numpy as _np
except Exception:                                        # pragma: no cover
    _np = None

#: Pixels per cell along the image's x axis. VERIFIED by the corpus identity.
CELL_PX_W = 64
#: Pixels per cell along the image's y axis. Half of CELL_PX_W: 2:1 isometric.
CELL_PX_H = 32
#: An empty slot in a .pul -- no tile painted there.
EMPTY = 0xFFFF
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
    #: FRAME 0 of an animated tile -- every existing caller reads this and
    #: gets exactly the byte it got before `frame_list` was added.
    frames: dict = field(repr=False, default_factory=dict)
    #: idx -> EVERY frame the .ani names for that tile, in `Frame<N>` order.
    #: `frames[idx] == frame_list[idx][0]` wherever both are set. Separate
    #: from `frames` deliberately: the still renderers (`terrain.py`, the
    #: `/api/game/*` PNG endpoints) must keep taking frame 0 with no change,
    #: so animation is an ADDITIVE fact rather than a different value in an
    #: existing field. See `docs/ground_animation.md`.
    frame_list: dict = field(repr=False, default_factory=dict)
    assets: Optional[AssetRoot] = field(repr=False, default=None)
    #: synthetic tile index -> the terrain rows to composite, bottom
    #: first. Only `.pux` fills this. A `.pul` tile is ONE texture and
    #: `tiles` holds its index directly; a `.pux` tile is a STACK, and
    #: rather than teach the render loop about stacks -- which would
    #: cost its per-index cache, the thing that makes it fast -- each
    #: DISTINCT stack is given a synthetic index and cached like any
    #: other tile. A map reuses a few dozen stacks thousands of times,
    #: so the cache still does its job.
    stacks: dict = field(repr=False, default_factory=dict)
    #: WHERE `grid` CAME FROM, because one of the two answers is a
    #: DERIVATION and a reader must not have to guess which. ``"registry"``
    #: means this map has a row in `ini/GameMap.dat` (or `.json`) and `grid`
    #: is that row's ``PuzzleGridSize``, read. ``"derived"`` means the map has
    #: NO row and `grid` was solved out of the map's own bytes by
    #: `derive_grid_size` -- see that function for how often the solution
    #: agrees with a registry row where one exists to check against. Nothing
    #: downstream branches on this; it exists so a caller can refuse a derived
    #: figure if it wants one the client itself would have read.
    grid_source: str = "registry"
    _tile_rgb: dict = field(repr=False, default_factory=dict)
    _layer_cache: dict = field(repr=False, default_factory=dict)

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
        """The .dds behind a tile index, via the .ani. **Frame 0** of an
        animated tile, always -- the still renderers depend on that and
        `tile_frames` is where the rest live."""
        return self.frames.get(idx, "")

    def tile_frames(self, idx: int) -> list:
        """Every .dds the .ani names for a tile index, `Frame<N>` order.

        Always at least as long as ``[tile_path(idx)]`` when the tile
        resolves at all, so a caller can loop over this instead of
        special-casing the single-frame case.

        MEASURED (2026-08-26) over the nine corpus installs under
        ``CO_CLIENTS`` **and** the conventional install `core/coroot.py`
        resolves: **no GROUND puzzle on any of them has a tile whose key
        names more than one frame.** Every multi-frame key in the corpus
        belongs to a BACKDROP plane. See `docs/ground_animation.md` 1.
        """
        v = self.frame_list.get(idx)
        if v:
            return list(v)
        p = self.frames.get(idx, "")
        return [p] if p else []

    @property
    def animated_tiles(self) -> dict:
        """``{idx: frame count}`` for tiles the .ani gives more than one
        frame. Empty on every ground puzzle in the corpus."""
        return {i: len(v) for i, v in self.frame_list.items() if len(v) > 1}

    def _tile_pixels(self, idx: int) -> Optional[bytes]:
        """One tile as ``grid*grid*3`` RGB bytes, alpha flattened onto VOID.
        Cached: a map reuses a few dozen tiles thousands of times."""
        if idx in self._tile_rgb:
            return self._tile_rgb[idx]
        if idx in self.stacks:
            out = self._composite(self.stacks[idx])
            self._tile_rgb[idx] = out
            return out
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

    def _layer_rgba(self, row: int):
        """One terrain row's texture as ``grid*grid*4`` RGBA, or None.

        Kept separate from `_tile_pixels` because a LAYER must keep its
        alpha -- that is the whole point of stacking -- while a finished
        tile has it flattened onto VOID.
        """
        cache = self._layer_cache
        if row in cache:
            return cache[row]
        out = None
        path = self.frames.get(row, "")
        if path and self.assets is not None:
            try:
                w, h, rgba = dds.decode(self.assets.read(path))
                out = _resample_rgba(rgba, w, h, self.grid)
            except Exception:                            # noqa: BLE001
                out = None
        cache[row] = out
        return out

    def composite_rgba(self, idx: int, *, mask: bool = True) -> Optional[bytes]:
        """A stack as ``grid*grid*4`` RGBA, ALPHA KEPT, or None.

        Public because there are two ground renderers and they need
        different things from the same stack: this module flattens onto
        VOID for a finished still, and `mapedit` keeps the alpha so a layer
        can let the background through.  Sharing the composite rather than
        writing it twice -- the second copy is how one of them stays wrong.

        `mask=False` is the pre-2026-09-06 full-tile blend, kept as an
        ABLATION rather than as an option anyone should pass: it is what the
        render comparison in `docs/pux_mask_composite_2026-09-06.md` measures
        against, and a test that cannot turn the mask off cannot show the mask
        is doing anything.
        """
        rows = self.stacks.get(idx)
        if not rows:
            return None
        return self._composite_rgba(rows, mask=mask)

    def _composite_rgba(self, rows, *, mask: bool = True) -> Optional[bytes]:
        """Alpha-composite terrain rows, bottom first. RGBA out.

        Accepts either a bare terrain row or a full layer tuple whose first
        element is that row.

        **THE LAYER MASK IS APPLIED HERE SINCE 2026-09-06.**  A `.pux` layer
        entry is ``(terrain row, i16, i16)`` and the two `i16` are the low 16
        and high 9 bits of ONE 25-bit per-vertex alpha mask over the 5x5
        vertices bounding a 4x4 subdivision of the tile
        (`docs/pux_f1_f2_attribution_2026-09-06.md`; the client's own
        ``AlphaAt`` at `Clients/7878/Env_DX9/Conquer.exe` RVA `0x872A68`).
        Each layer's own alpha is multiplied by
        `dmap.pux_mask_field(mask, grid)` -- the mask bilinearly interpolated
        across the quads -- before the layer is composited.

        Until this change every layer was stacked FULL-TILE, so the topmost
        opaque layer of a stack covered everything under it and the mask, which
        is the entire mechanism by which a `.pux` ground blends one terrain
        into another, drew nothing.  **MEASURED against the shipped minimap
        renders, per stacked tile, median Pearson r of my tile against the
        shipped one:**

            ninja01_new (CCO, 45.5 px/tile)  full +0.011  MASK +0.160
            ninja01_new (7878, 11.4 px/tile) full +0.011  MASK +0.230
            bp-flandlords-y_new (7878)       full +0.051  MASK +0.208

        against a DEPTH-MATCHED shuffled-mask control that scores with `full`
        (+0.016 / +0.016 / +0.077) and a registration arm -- the same masked
        tile scored one tile to the right -- at +0.065 / +0.088 / +0.110.
        `docs/pux_mask_composite_2026-09-06.md` has the method and the limits.

        A bare terrain row (no tuple) carries no mask and is composited
        full-tile, which is what a `.pul` tile and a single-layer `.pux` tile
        have always been.  Passing `mask=False` restores the old blend for
        every layer; nothing in the tree does except the ablation arm.

        **The interpolation is an ASSUMPTION, and it is the one to attack
        first if a rendered map still looks wrong.**  The disassembly pins
        alpha 255/0 AT THE VERTICES and hands them to `m_pPuzzleTriangle` as
        vertex colours; how the raster fills between them was not traced
        (`docs/pux_f1_f2_attribution_2026-09-06.md` 9).  `dmap.pux_mask_field`
        is where a different reading would go, and it is the only place.
        """
        base = None
        for layer in rows:
            # The `isinstance` is spelled out twice rather than hoisted into a
            # local: `tests/test_pux_stackkey.py` pins this exact expression as
            # a source guard -- the projection is one of three coupled sites and
            # dropping it hands a 3-tuple to `_layer_rgba`.
            row = layer[0] if isinstance(layer, tuple) else layer
            lay = self._layer_rgba(row)
            if lay is None:
                continue
            if mask and isinstance(layer, tuple) and len(layer) >= 3:
                lay = _apply_mask(lay, self.grid,
                                  _dmap.pux_layer_mask(layer[1], layer[2]))
            if base is None:
                base = bytearray(lay)
                continue
            _over(base, lay, self.grid)
        return bytes(base) if base is not None else None

    def _composite(self, rows) -> Optional[bytes]:
        """Alpha-composite a stack of terrain rows, BOTTOM FIRST, onto VOID.

        The stack order is the file's order.  The two `i16` that follow each
        layer's row index ARE applied, by `_composite_rgba`, which this
        delegates to -- see its docstring for the blend and for the render
        comparison behind it.  The sentence this paragraph used to end with,
        "the two `i16` ... are NOT applied", was true until 2026-09-06.

        **THEY ARE NOW NAMED, AND THIS PARAGRAPH'S DECOMPOSITION WAS WRONG.**
        `docs/pux_f1_f2_attribution_2026-09-06.md`: they are not two fields.
        They are the low 16 and high 9 bits of **one 25-bit per-vertex alpha
        mask** over the 5x5 grid of vertices bounding a 4x4 subdivision of the
        tile -- read as ONE u32 by the client and consumed by
        ``alpha(i) = 255 if i < 25 and (mask >> i) & 1 else 0`` at
        `Clients/7878/Env_DX9/Conquer.exe` RVA `0x872A68`.  `core/dmap.py`'s
        `pux_layer_mask` / `pux_mask_alpha` are the accessors.

        So a compositor should mask each layer by that alpha, bilinearly
        interpolated across the 4x4 quads, instead of stacking full tiles.
        **THAT CHANGE IS MADE NOW** -- `docs/pux_mask_composite_2026-09-06.md`,
        and `dmap.pux_mask_field` is the expansion.  This paragraph used to end
        "that change is NOT made here", and the two sentences after it recorded
        the full-tile stack as a known-wrong blend.  It is no longer the blend.

        What follows is the characterisation that preceded the name, kept
        because it is what the field looks like from the data side and because
        one of its two conclusions was a real refutation.  Measured over
        155,404 layer entries:

            field B   bounded 0..511 on EVERY entry -- 9 bits, never more.
                      Its popcount distribution is a clean U: 26.3% zero,
                      26.2% all-nine, and a smooth tail between.  Layer 0 is
                      overwhelmingly 511 while overlays are mostly 0, and
                      the recurring values are coherent 3x3 shapes -- 16 is
                      the centre bit alone, 495 is a ring with the centre
                      missing.  It is A NINE-BIT MASK.
            field A   full i16 range, 9,762 distinct, 45.7% negative, with
                      -1 (34,707) and -32768 (5,305) dominant.  Sentinel-
                      shaped.  Not characterised further.

        **REFUTED, and recorded so it is not re-tried:** the obvious reading
        of field B is an autotile / neighbour-transition mask.  It is not.
        Tested over 91,324 comparisons on partial masks -- excluding the
        saturated 0 and 511, which cannot discriminate -- asking whether a
        set bit predicts that the neighbour in that direction carries the
        same terrain id: **48.5%, which is chance.**  The 3x3 shapes are
        persuasive and the hypothesis is still wrong; only the neighbour
        test could show that.

        The surviving reading -- intra-tile coverage, "which parts of its own
        tile a layer paints" -- **is the right one, at the wrong resolution**.
        It was called UNTESTED here and it stayed untested because the two
        halves were treated as two fields: 9 bits of a 25-bit mask is the top
        row and a bit, so "3x3 shapes" were the top 9 vertices of a 5x5 grid
        read as if they were a grid of their own.  *A field split at the wrong
        boundary produces coherent-looking structure in both halves*, and the
        3x3 ring at 495 is that artefact.

        **The autotile refutation still stands and is now explained**: the
        mask describes THIS tile's own vertices, so a set bit was never going
        to predict a neighbour's terrain id.  48.5% was the right answer.  Run
        on the whole 25 bits the neighbour question becomes a different one --
        does my vertex column 4 equal my right neighbour's column 0, the same
        seam -- and that answers 98.8% against a 60.2% control.

        So the layers WERE stacked FULL-TILE, and that was the known-wrong
        blend.  `core/dmap.pux_mask_alpha` was named as the fix and it is the
        fix that landed; `dmap.pux_mask_field` is it, applied per layer in
        `_composite_rgba`.
        """
        out = self._composite_rgba(rows)
        if out is None:
            return None
        return _flatten(out, self.grid, self.grid, self.grid)

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


def _resample_rgba(rgba: bytes, w: int, h: int, grid: int) -> bytes:
    """RGBA resampled to ``grid*grid``, alpha KEPT. Companion to `_flatten`,
    which is the same resample followed by a flatten onto VOID."""
    if (w, h) == (grid, grid):
        return rgba
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8).reshape(h, w, 4)
        yi = (_np.arange(grid) * h // grid).clip(0, h - 1)
        xi = (_np.arange(grid) * w // grid).clip(0, w - 1)
        return a[yi][:, xi].tobytes()
    out = bytearray(grid * grid * 4)
    for y in range(grid):
        sy = y * h // grid
        for x in range(grid):
            sx = x * w // grid
            s0 = (sy * w + sx) * 4
            o = (y * grid + x) * 4
            out[o:o + 4] = rgba[s0:s0 + 4]
    return bytes(out)


#: (mask, grid) -> the interpolated alpha field. A map has a few hundred
#: distinct masks and reuses each across thousands of tiles, so this is the
#: same shape of cache as `_layer_cache` and for the same reason. Bounded
#: because the key space is the map's own mask vocabulary, not 2**25.
def _full_mask(layer) -> bool:
    """Is this layer's per-vertex mask the full 0x1FFFFFF?

    Only a FULL-masked single-layer tile may be flattened to a bare terrain
    row: a bare row carries no mask, so flattening a PARTIAL one silently
    promotes it to fully opaque in both renderers.
    """
    if not isinstance(layer, (tuple, list)) or len(layer) < 3:
        return True                     # no mask carried: the old flat shape
    m = _dmap.pux_layer_mask(layer[1], layer[2]) & _dmap.PUX_MASK_FULL
    return m == _dmap.PUX_MASK_FULL


_MASK_FIELDS: dict = {}


def _apply_mask(rgba: bytes, grid: int, mask: int) -> bytes:
    """One layer's RGBA with its per-vertex alpha mask multiplied in.

    Returns `rgba` UNCHANGED when the mask is the full 0x1FFFFFF, which is the
    single most common value on three of the four installs -- so the common
    case costs one comparison and no copy.
    """
    if mask & _dmap.PUX_MASK_FULL == _dmap.PUX_MASK_FULL:
        return rgba
    key = (mask, grid)
    fld = _MASK_FIELDS.get(key)
    if fld is None:
        fld = _MASK_FIELDS[key] = _dmap.pux_mask_field(mask, grid)
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8).reshape(-1, 4).copy()
        f = _np.frombuffer(fld, dtype=_np.uint8).astype(_np.uint16)
        a[:, 3] = ((a[:, 3].astype(_np.uint16) * f + 127) // 255).astype(_np.uint8)
        return a.tobytes()
    out = bytearray(rgba)
    for i in range(grid * grid):
        out[i * 4 + 3] = (out[i * 4 + 3] * fld[i] + 127) // 255
    return bytes(out)


def _over(base: bytearray, top: bytes, grid: int) -> None:
    """Source-over composite `top` onto `base`, both ``grid*grid`` RGBA.

    In place, because a stack is composited one layer at a time and copying
    the accumulator per layer is the whole cost on a map with nine of them.
    """
    if _np is not None:
        b = _np.frombuffer(bytes(base), dtype=_np.uint8).reshape(-1, 4).astype(_np.uint16)
        t = _np.frombuffer(top, dtype=_np.uint8).reshape(-1, 4).astype(_np.uint16)
        ta = t[:, 3:4]
        rgb = (t[:, :3] * ta + b[:, :3] * (255 - ta) + 127) // 255
        al = ta[:, 0] + (b[:, 3] * (255 - ta[:, 0]) + 127) // 255
        outa = _np.concatenate([rgb, _np.minimum(al, 255)[:, None]], axis=1)
        base[:] = outa.astype(_np.uint8).tobytes()
        return
    for i in range(0, grid * grid * 4, 4):
        ta = top[i + 3]
        if ta == 0:
            continue
        if ta == 255:
            base[i:i + 4] = top[i:i + 4]
            continue
        inv = 255 - ta
        for c in range(3):
            base[i + c] = (top[i + c] * ta + base[i + c] * inv + 127) // 255
        base[i + 3] = min(255, ta + (base[i + 3] * inv + 127) // 255)


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


def derive_grid_size(map_width: int, pul_w: int, pul_h: int,
                     alphabet) -> Optional[int]:
    r"""``PuzzleGridSize`` solved out of a map's OWN bytes, or ``None``.

    **Why this exists.** ``PuzzleGridSize`` -- the tile edge in pixels -- is
    carried in exactly one place, the map's row in ``ini/GameMap.dat`` (or the
    community client's ``ini/GameMap.json``), and a map with no row therefore
    had no grid and could not be assembled at all. MEASURED 2026-09-03 over
    all 34 installs under ``Clients/``: that single missing integer is
    **1,777 of the 2,079 whole-map build failures the oracle's `map.build`
    dimension counts, 85.5%** -- every other byte of those maps is present and
    parses.

    **The solution.** `PuzzleMap.consistent` already states the identity the
    shipped maps satisfy::

        map_width == map_height == PxW/64 + PxH/32
                  == grid*pul_w/64 + grid*pul_h/32

    which inverts to a closed form with no free parameter::

        grid == map_width * 64 / (pul_w + 2*pul_h)

    `map_width` is the ``.DMap``'s own declared width and `pul_w`/`pul_h` are
    the ``.pul``'s own declared extent, so the answer is read out of the two
    files the caller already holds.

    **What it is allowed to return, and why it refuses rather than rounds.**
    A non-integral solution means the map's ``.DMap`` and its art DISAGREE
    about the map's size -- `consistent` is False for it -- and there is then
    no grid that satisfies the identity. Rounding to the nearest plausible
    value there would manufacture a number for exactly the maps whose geometry
    is known to be odd. So the solution is accepted only when it is an exact
    integer AND a member of `alphabet`, which is *this install's own set of
    ``PuzzleGridSize`` values as actually registered* -- not a constant. Every
    other case returns ``None`` and the caller refuses the map as before.

    **HOW OFTEN IT IS RIGHT, measured against the answer.** Run over the maps
    that DO have a registry row, on 7 installs (5017, 5517, 6090, 6609, 7205,
    7878, Zephyr), 1,459 registered ``.pul`` maps:

        accepted and equal to the registry     1,439
        accepted and DIFFERENT from it             5
        refused (non-integral / not in the set)   15

    The five disagreements are `2013ganenjie` (4 installs) and 7878's
    `2015valentine`, and they are worth stating precisely rather than
    averaging away: in **all five** the registry's value makes the map
    INCONSISTENT (2013ganenjie's row says 256, which implies a 416-cell map
    against the ``.DMap``'s 208) and the derived value makes it consistent.
    That is not evidence the derivation is better -- the client reads the
    registry, so on those maps the registry is what the client draws with.
    It is the reason this function is **only ever called where there is no row
    to read**: where the client has an answer, the client's answer wins, and
    this one is never consulted.

    Over the whole corpus the rule accepts 1,672 of the 1,777 unregistered
    maps (94.1%) and refuses 105.
    """
    # `CELL_PX_W // CELL_PX_H` rather than a literal 2: the identity above is
    # `PxW/CELL_PX_W + PxH/CELL_PX_H`, and writing the ratio out means the two
    # constants cannot drift apart from the arithmetic that assumes them.
    den = int(pul_w) + (CELL_PX_W // CELL_PX_H) * int(pul_h)
    if den <= 0 or not alphabet or CELL_PX_W % CELL_PX_H:
        return None
    num = int(map_width) * CELL_PX_W
    if num % den:
        return None                      # the identity has no integral solution
    g = num // den
    return g if g in alphabet else None


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
        """Shipped maps with no row in this install's registry.

        Computed once, on the install that is loaded, because the number
        varies by client and a quoted one is a different install's fact:
        **MEASURED 2026-08-11 -- 5517: 21 of 181, 6090: 21 of 184, 6609: 20 of
        184.** Those are recorded here as provenance, not consulted.

        **IT COUNTED THE LOOSE DIRECTORY, AND ON EVERY 7632-AND-LATER CLIENT
        THAT MADE IT REPORT ZERO** -- ``sum(... for p in map/map.iterdir())``,
        over a tree whose maps ship inside archives. 7878 has 146 unregistered
        maps and this number said "0 of this install's shipped maps have
        none", inside the very message that exists to tell a reader how common
        their situation is. Counted over `names()` -- the registry UNION the
        archives UNION the loose tree, which is the same population every
        other figure about this install uses -- it is 146. A count that is
        zero exactly where the thing it counts is most common is worse than no
        count at all, because it reads as reassurance.
        """
        if self._unregistered is None:
            self._unregistered = sum(1 for n in self.names()
                                     if str(n).lower() not in self._grid_size)
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

    #: A machine-readable companion to `reason`, set by `_build` on the paths
    #: a CALLER has to tell apart. Empty everywhere else, including success.
    #:
    #: `reason` is prose for a human and it stays prose. `routeb/oracle.py`
    #: needs to separate "this map cannot be built because our rule is
    #: incomplete" from "this map cannot be built because the bytes disagree
    #: with each other", and the only handle it had was a substring match on
    #: that prose. The block just above `_build`'s grid solve records what
    #: happened when the previous parse of a reason string pulled out the
    #: literal `"a .7z"` and excluded 48 maps on it: a prose parse is wrong
    #: only in the case the check exists to catch. So the fact is published
    #: rather than re-derived.
    reason_code: str = ""

    def _build(self, name: str) -> Optional[PuzzleMap]:
        self.reason_code = ""
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
        if pul.suffix.lower() == ".pux":
            # TqTerrain. This used to return None with a message saying the
            # tile payload "is not a compiled index". The geometry half of
            # that message was right and the conclusion was wrong: the tile
            # records are variable-length because a tile carries a VARIABLE
            # NUMBER OF LAYERS (0 to 11+ here), which is exactly the 20.8 to
            # 172.2 bytes per tile that was measured and read as disproof of
            # an index. `dmap.read_pux_full` decodes it -- 149 of 149 files
            # on 6609 and 7878, none refused.
            #
            # A `.pux` differs from a `.pul` in one way that matters here:
            # a `.pul` names ONE .ani for the whole map and its tiles index
            # into it, while a `.pux` carries a terrain TABLE whose every
            # row names its own .ani and PuzzleNN key. So the tile values
            # below index the terrain table, and `frames` is filled per row
            # rather than from a single table.
            from dmap import read_pux_full, PUX_GRID     # noqa: PLC0415
            px = read_pux_full(pul)
            if px is None:
                self.reason = (f"{src} uses {rel} -- TqTerrain (.pux), and it "
                               f"did not decode (see dmap.read_pux_full)")
                return None
            # Layer 0 is the ground; the rest are overlays this renderer does
            # not place yet. Stated because a map with layers drawn from
            # layer 0 alone is INCOMPLETE, not wrong, and a reader comparing
            # it against the client will see the difference.
            # Give every DISTINCT layer stack one synthetic index. A tile
            # with a single layer keeps that layer's own index, so a .pux
            # that happens to be flat costs exactly what it did before.
            SYNTH = 1 << 20                  # far above any terrain row
            stacks: dict = {}
            by_key: dict = {}
            tiles = []
            for t in px["tiles"]:
                if not t:
                    tiles.append(EMPTY)
                elif len(t) == 1 and _full_mask(t[0]):
                    # The flat-tile shortcut, and it is now CONDITIONAL.
                    #
                    # It used to be `elif len(t) == 1: tiles.append(t[0][0])`,
                    # which keeps the terrain row and DROPS THE TWO i16 that
                    # carry the mask -- the same defect the comment below
                    # describes for the multi-layer branch, fixed there and
                    # missed here. A single-layer tile can carry a PARTIAL
                    # mask, and flattening it made the tile draw FULL-TILE
                    # OPAQUE: `_composite_rgba` skips `_apply_mask` for a bare
                    # row, and `tileset.py` emits no stack entry so the page
                    # reads a FULL mask too. Both renderers, one cause.
                    #
                    # Found from `sary02_new`, where tile (19,12) is
                    # `[(231, 16, 0)]` -- mask 0x10, ONE of 25 vertices -- and
                    # drew as a solid red rectangle. Single-layer tiles
                    # carrying a partial mask: **166 on sary02, 91 on
                    # 2024thx, 93 on 2024xmas**.
                    tiles.append(t[0][0])
                else:
                    # THE FULL LAYER TUPLE, not the terrain rows alone.
                    # Keying on rows collapsed tiles that differ only in the two
                    # discarded per-layer fields onto ONE synthetic id, so a
                    # correct compositor fed by this key would still draw some
                    # tiles wrong. MEASURED on 7878, distinct stacks:
                    #     ninja01          721 ->  968   1.34x
                    #     bp-flandlords-y  205 ->  277   1.35x
                    #     2020love01_new    15 ->  224  14.93x
                    #     magictower01       5 ->   42   8.40x
                    # The aggregate 1.60x understates it: the two maps where most
                    # slots already draw are the two where the collapse is worst.
                    # Id space is not a constraint -- 1,511 against the ~4.29
                    # billion the u32 slot widening left above SYNTH.
                    key = tuple(tuple(e) for e in t)
                    sid = by_key.get(key)
                    if sid is None:
                        sid = SYNTH + len(by_key)
                        by_key[key] = sid
                        stacks[sid] = key
                    tiles.append(sid)
            pm = PuzzleMap(
                name=str(name), map_id=self._doc_id.get(str(name).lower()),
                map_width=m.width, map_height=m.height,
                grid=PUX_GRID, pul_w=px["width"], pul_h=px["height"],
                tiles=tiles, ani="", pul_path=rel.lower(), assets=self.assets,
                stacks=stacks,
                # NEITHER read nor solved: `dmap.PUX_GRID` is a constant this
                # repo asserts for TqTerrain. Labelled as its own third answer
                # rather than left wearing the default "registry", which would
                # be a false claim about where the number came from.
                grid_source="pux-constant",
            )
            # Every row any tile OR any stack names -- a stack's rows never
            # appear in `tiles` (they are replaced by the synthetic index),
            # so iterating `tiles` alone leaves every stacked layer with no
            # texture and the map renders as its single-layer tiles only.
            wanted = {i for i in tiles if i != EMPTY and i < SYNTH}
            for key in stacks.values():
                # `key` is now a tuple of LAYER TUPLES, so the terrain row has to
                # be projected out. Before the widening this read
                # `wanted.update(key)` and key was a tuple of row ints; leaving it
                # would have put 3-tuples into a set that is indexed against
                # `px["terrain"]` two lines below.
                wanted.update(e[0] for e in key)
            for idx in wanted:
                if idx >= len(px["terrain"]):
                    continue
                row = px["terrain"][idx]
                v = self._ani_table(row["ani"]).get(row["key"])
                if isinstance(v, str):
                    v = [v]
                if v:
                    seq = [str(x).replace("\\", "/").lstrip("/").lower()
                           for x in v]
                    pm.frames[idx] = seq[0]
                    pm.frame_list[idx] = seq
            self.reason = ""
            return pm
        if pul.suffix.lower() != ".pul":
            self.reason = (f"{src} names {rel}, which is neither a .pul nor "
                           f"a .pux -- no reader for that form")
            return None
        try:
            z = Pul.load(pul)
        except Exception as e:                           # noqa: BLE001
            self.reason = f"{rel} did not parse: {e}"
            return None
        stem = str(name).lower()
        g = self._grid_size.get(stem)
        grid_source = "registry"
        if g is None:
            # NO ROW -- so solve the grid out of the map's own two files.
            #
            # This branch used to be the end of the road, and it is by a wide
            # margin the biggest single hole in the ground pipeline: MEASURED
            # 2026-09-03 across all 34 installs, 1,777 of 2,079 whole-map
            # failures (85.5%) are maps that ship every byte, parse, name a
            # `.pul` that is present, and were refused for one missing
            # integer. `derive_grid_size` recovers 1,672 of them; its
            # docstring carries the measurement of how often the recovered
            # value equals the registry's where a registry value exists.
            #
            # ORDER MATTERS AND IS NOT AN OPTIMISATION: the registry is
            # consulted FIRST and the derivation is reached only when there is
            # nothing to read. On the five registered maps where the two
            # disagree, the client draws with the registry's value, so a
            # derivation that could override it would be a wrong answer with a
            # plausible justification. It cannot: it is unreachable there.
            g = derive_grid_size(m.width, z.width, z.height,
                                 set(self._grid_size.values()))
            grid_source = "derived"
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
            #
            # THE SECOND HALF OF THE MESSAGE IS NEW AND IS THE PART THAT NOW
            # MATTERS. Reaching here means BOTH sources failed: no row to read
            # AND no integral solution in this install's own set of grid
            # sizes. Saying only "no row" would send a reader to the registry
            # for a map whose real problem is that its `.DMap` and its art
            # disagree about how big the map is, which no registry edit fixes.
            den = z.width + (CELL_PX_W // CELL_PX_H) * z.height
            solved = (m.width * CELL_PX_W / den) if den else float("nan")
            alpha = sorted(set(self._grid_size.values()))
            # TWO DIFFERENT FACTS WEAR THIS ONE MESSAGE, and only one of them
            # is about us. MEASURED 2026-09-04 over all 34 installs, on the
            # 105 unregistered maps `derive_grid_size` refuses:
            #
            #   unsatisfiable   105   `map_width*64` is not divisible by
            #                         `pul_w + 2*pul_h`, so NO positive
            #                         integer grid satisfies the identity --
            #                         not "none we would accept", none.
            #   not-in-alphabet   0   an exact integer this install has never
            #                         registered. None occur; the branch is
            #                         kept because it is the case where the
            #                         refusal IS a choice of ours.
            #
            # The first is a statement about the two shipped files. The
            # second would be a statement about our willingness to trust a
            # number. `oracle.map.build` excludes the first and counts the
            # second, and it can only do that if they are told apart here.
            if grid_source == "derived" and den:
                num = m.width * CELL_PX_W
                self.reason_code = ("unregistered-unsatisfiable"
                                    if num % den else
                                    "unregistered-not-in-alphabet")
            self.reason = (f"{name} has no row in {self._registry_rel} "
                           f"({self._unregistered_count()} of this install's "
                           f"shipped maps have none), and no PuzzleGridSize "
                           f"could be derived either: "
                           f"{m.width}*{CELL_PX_W}/({z.width}+"
                           f"{CELL_PX_W // CELL_PX_H}*{z.height}) = "
                           f"{solved:.4f}, which is not an integer in this "
                           f"install's registered set {alpha} -- the .DMap "
                           f"and its art disagree about the map's size"
                           if self._registry_rel else
                           f"{name} has no PuzzleGridSize: this install "
                           f"ships no map registry at all (neither "
                           f"ini/GameMap.json nor ini/GameMap.dat), so there "
                           f"is neither a row to read nor a set of registered "
                           f"grid sizes to solve within")
            return None
        pm = PuzzleMap(
            name=str(name), map_id=self._doc_id.get(stem),
            map_width=m.width, map_height=m.height,
            grid=g, pul_w=z.width, pul_h=z.height, tiles=z.tiles,
            ani=rel_ani(z.ani_path), pul_path=rel.lower(),
            assets=self.assets, grid_source=grid_source,
        )
        table = self._ani_table(z.ani_path)
        for idx in set(z.tiles):
            if idx == EMPTY:
                continue
            v = table.get(f"Puzzle{idx}")
            if isinstance(v, str):
                v = [v]
            if v:
                seq = [str(x).replace("\\", "/").lstrip("/").lower() for x in v]
                pm.frames[idx] = seq[0]
                pm.frame_list[idx] = seq
        self.reason = ""
        return pm


    # -- backdrops ---------------------------------------------------------

    def backdrops(self, name: str) -> list["Backdrop"]:
        """The map's background puzzle layers, furthest first.

        FURTHEST FIRST IS ASCENDING `values[0]`, and the list really is
        sorted that way -- it used to be sorted `-values[0]`, contradicting
        this line. `docs/ground_animation.md` 6.

        WHERE THEY COME FROM -- VERIFIED. A `.DMap`'s trailing section is the
        background list. `core/dmap.parse_trailer` decodes it as GROUPS of
        planes (a one-plane group is byte-for-byte the 284-byte record an
        earlier flat model read), and `extra` flattens the groups into one
        row per plane: six `u32` then a `char[260]` path. Every well-formed
        one names a `map/puzzle/*.pul`. `newbie` names `newbiebg.pul`, which
        nothing in the header references -- which is why the ground pipeline
        never loaded it.

        THE OLD FIGURE HERE -- "57 of the 136 shipped maps carry 109 such
        records" -- WAS WRONG TWICE, and 57 is the number that misled.
        Re-measured 2026-08-26 over `PuzzleLibrary.names()` on the install
        `core/coroot.py` resolves: **137 maps, of which 55 carry a
        resolvable plane, holding 57 GROUPS and 162 PLANES**, and 0 records
        name a `.pul` that is not shipped. 57 is the GROUP count, not a map
        count. `docs/map_scenery.md` 5 publishes 80/187, 84/193 and 87/196
        for 5517/6090/6609 and flags the 57/109 pair as CCO's; it does not
        reproduce on CCO either.

        WHAT THE SIX INTEGERS ARE. values[0] is VERIFIED as a draw index,
        furthest first: `2009-7x` and `beach` each carry two, numbered 0 and
        1, and 0 is the further one in both -- confirmed independently from
        the raw binary trailer by the Route B survey
        (`docs/routeb_backdrop_planes_2026-08-26.md` on
        `claude/vibeco-dx-backdrop`), which never calls this method.
        values[4] is the PLANE COUNT OF THE GROUP (VERIFIED 2026-08-10;
        re-measured here 162/162 records agree), which is why `star01`..
        `star10` carry 7..16 planes ALL AT values[0] == 0 -- one group, one
        draw index -- and why the sort below must be STABLE: within a group
        the trailer's own order is the only order there is.
        values[2..3] are a parallax PERCENTAGE -- 30/30 (42x), 50/50,
        100/100, 40/40, 20/20, 10/10, and five mismatched pairs; the range is
        10..100 and never above it, which is what a percentage looks like and
        what a pixel or tile count does not. **MEASURED 2026-08-26, no longer
        inferred**: 5017 `Conquer.exe` reads exactly these two dwords out of
        the map file at VA 0x474019 (`MSVCRT!fread`, 4 bytes each) into the
        plane object's +4/+8, then forms `camera * [esi+4] / 100` at VA
        0x473F0A -- a division by 100, so a percentage by construction. The
        `values[4]` plane count read by the same loop is the control that the
        field indices line up. The same two integers are also the roll
        `rate`; see `Backdrop.roll` and `docs/ground_animation.md` 10.10.
        values[1] is 4 on every record and values[5] is 8 on every record;
        neither is named here.

        REFINED 2026-08-29, and it does not change what this method returns.
        `values[4]` is the group's ITEM count, not its plane count, and
        `values[5]` is the item's TAG -- 8, which is `PUZZLE` in
        `dmap.LAYER_TYPES`, which is why "8 on every record" held: this list
        is filtered to tag-8 items. The two readings coincide on every map on
        this box but one (`7878 newbie`, whose group holds a plane and a
        tag-19 effect), and `dmap.parse_trailer` still hands this method
        planes only. See `docs/dmap_plane_groups_1005_2026-08-29.md`.
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
        g = self._grid_size.get(str(name).lower())
        gsrc = "registry" if g is not None else "default-256"
        g = g or 256
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
                           assets=self.assets, grid_source=gsrc)
            table = self._ani_table(z.ani_path)
            for idx in set(z.tiles):
                if idx == EMPTY:
                    continue
                t = table.get(f"Puzzle{idx}")
                if isinstance(t, str):
                    t = [t]
                if t:
                    seq = [str(x).replace("\\", "/").lstrip("/").lower()
                           for x in t]
                    pm.frames[idx] = seq[0]
                    pm.frame_list[idx] = seq
            out.append(Backdrop(art=pm, index=v[0] if v else 0,
                                parallax=(v[2] if len(v) > 2 else 100,
                                          v[3] if len(v) > 3 else 100),
                                roll=tuple(z.roll_speed or (0, 0)),
                                values=v, path=rel.lower()))
        # FURTHEST FIRST -- ascending, so the NEAREST plane is last and a
        # last-wins compositor puts it on top. This used to be `-b.index`,
        # which contradicted this method's own docstring and put the far
        # plane over the near one in every server-rendered PNG.
        # `tilebake.js:load()` has always sorted ascending and draws in list
        # order with blending off, so this is the server agreeing with the
        # client rather than a new convention. Only `beach` and `2009-7x`
        # move: they are the only two maps in the corpus with more than one
        # plane AND distinct indices -- `star01`..`star10` carry 7..16 planes
        # all at index 0, where a STABLE sort returns them in .DMap trailer
        # order either way. `docs/ground_animation.md` 6.
        out.sort(key=lambda b: b.index)
        return out


@dataclass
class Backdrop:
    """One background puzzle plane behind a map's ground art."""

    art: PuzzleMap
    index: int = 0
    #: Percent of the ground's own scroll this plane follows. MEASURED --
    #: see `PuzzleLibrary.backdrops` for the two disassembly sites.
    parallax: tuple[int, int] = (100, 100)
    #: `rollSpeedX/Y` from the .pul -- a scrolling backdrop. Not animated
    #: here (a still PNG cannot show a scroll) but animated in the GL
    #: client, whose `tilebake.js` scrolls the plane at
    #: **`roll * parallax / 100` PIXELS PER SECOND**. Sign and magnitude from
    #: `coassets.Pul` (int32 over 2,724 files); the time base and the
    #: parallax factor are both recovered from the client --
    #: `docs/ground_animation.md` 10 and 10.10.
    roll: tuple[int, int] = (0, 0)
    values: list = field(default_factory=list)
    path: str = ""

    def sample_rect(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Which piece of this plane sits behind a ground rectangle.

        The plane is smaller than the map (12x40 tiles against a 132-cell map is
        typical), so it tiles; `render_tiled` does the wrapping and this only
        applies the parallax shift. The `/ 100` is the client's own -- see
        `backdrops()` for the disassembly site.
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
