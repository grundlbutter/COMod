#!/usr/bin/env python3
r"""mapproject.py -- resample an ISOMETRIC client screen frame into the
orthographic CELL GRID `mapdigest.digest_image` consumes.

The oracle instrument grades a subject client (VibeCO) against retail 6271 per
tile. Both draw ISO, but the digest reads an axis-aligned cell grid, so a screen
frame must be resampled to that grid first. This is that resample, and it is a
STATIC AFFINE, not a camera-tracking problem: director-vibeco measured Route B's
camera (`routeb/render.cpp`) as a fixed 2:1 dimetric orthographic projection
whose GROUND term has no height component (`ELEV_SCALE == 0`,
`docs/routeb_elevation_2026-08-15.md`):

    world (wx, wz) = (cellx, celly) - FOCUS         # focus cell at the origin
    sx = ORIGIN_X + AX*(wx - wz)                     # AX = 32
    sy = ORIGIN_Y + AY*(wx + wz)                     # AY = 16, wy drops out for ground
    the focus cell lands dead centre at ORIGIN = (800, 450) on the 1600x900 frame

So a cell's screen position is a closed form in its cell coordinates alone. The
resample is the INVERSE map applied per output pixel: for each pixel of cell
(cx, cy)'s orthographic tile, take its fractional world position, project it to
the frame by the affine above, and sample. No depth buffer, no per-frame camera
state, no projection matrix captured from the client.

WHAT THIS IS NOT. It does NOT digest `routeb/export_terrain.py`'s painted ground
-- that is the renderer's INPUT, and grading it would compare asset pipelines
and never exercise the renderer under test (director-vibeco, 2026-09-11). The
frame passed here must be what the client DREW.

    # frame is the client capture (RGBA), focus is the camera's focus cell:
    grid_w, grid_h, grid = project_to_cellgrid(frame, w, h, cols, rows, focus)
    digest = mapdigest.digest_image(grid, grid_w, grid_h, tile=mapproject.TILE)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: Orthographic output pixels per cell edge. Matches mapdigest's default tile.
TILE = 256

#: Route B's measured camera constants (director-vibeco, routeb/render.cpp).
AX, AY = 32, 16                       # sx = AX*(wx-wz), sy = AY*(wx+wz)
ORIGIN_X, ORIGIN_Y = 800.0, 450.0     # focus cell centre on a 1600x900 frame


def cell_to_screen(cx: float, cy: float, focus, *,
                   origin=(ORIGIN_X, ORIGIN_Y), a=(AX, AY)):
    """World cell (fractional) -> screen pixel, the ground affine above."""
    fx, fz = focus
    wx, wz = cx - fx, cy - fz
    return (origin[0] + a[0] * (wx - wz), origin[1] + a[1] * (wx + wz))


def _sample(frame: bytes, w: int, h: int, sx: float, sy: float) -> tuple:
    """Nearest-neighbour RGBA sample; out-of-frame is transparent black."""
    xi, yi = int(sx + 0.5), int(sy + 0.5)
    if xi < 0 or xi >= w or yi < 0 or yi >= h:
        return (0, 0, 0, 0)
    o = (yi * w + xi) * 4
    return (frame[o], frame[o + 1], frame[o + 2], frame[o + 3])


def project_to_cellgrid(frame: bytes, w: int, h: int, cols: int, rows: int,
                        focus, *, tile: int = TILE,
                        origin=(ORIGIN_X, ORIGIN_Y), a=(AX, AY)):
    """Resample an ISO screen `frame` into a `cols*tile` x `rows*tile`
    orthographic cell grid. Returns (grid_w, grid_h, rgba).

    Cell (cx, cy) occupies the tile block at grid (cx*tile, cy*tile); output
    pixel (u, v) within it is world position (cx + u/tile, cy + v/tile),
    projected to the frame and sampled. The FOCUS cell is the camera centre the
    capture was taken at -- it aligns the two coordinate systems and must be
    supplied with the frame.
    """
    if len(frame) != w * h * 4:
        raise ValueError(f"frame is {len(frame)} bytes, expected {w*h*4}")
    gw, gh = cols * tile, rows * tile
    out = bytearray(gw * gh * 4)
    for cy in range(rows):
        for cx in range(cols):
            for v in range(tile):
                wy_cell = cy + (v + 0.5) / tile
                orow = ((cy * tile + v) * gw + cx * tile) * 4
                for u in range(tile):
                    wx_cell = cx + (u + 0.5) / tile
                    sx, sy = cell_to_screen(wx_cell, wy_cell, focus,
                                            origin=origin, a=a)
                    r, g, b, al = _sample(frame, w, h, sx, sy)
                    o = orow + u * 4
                    out[o], out[o + 1], out[o + 2], out[o + 3] = r, g, b, al
    return gw, gh, bytes(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resample an iso client frame to "
                                 "the orthographic cell grid, then digest it.")
    ap.add_argument("frame", help="client capture PNG (what the client DREW)")
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--focus", required=True, help="focus cell 'cx,cy' the "
                    "capture was centred on")
    ap.add_argument("--tile", type=int, default=TILE)
    ap.add_argument("--out", required=True, help="digest JSON out")
    a = ap.parse_args(argv)
    import mapdigest                                  # noqa: E402
    import json
    w, h, frame = mapdigest._png_to_rgba(Path(a.frame).read_bytes())
    fx, fy = (int(x) for x in a.focus.split(","))
    gw, gh, grid = project_to_cellgrid(frame, w, h, a.cols, a.rows, (fx, fy),
                                       tile=a.tile)
    dig = mapdigest.digest_image(grid, gw, gh, tile=a.tile)
    Path(a.out).write_text(json.dumps(dig), encoding="utf-8")
    print(f"projected {a.frame} ({w}x{h}) -> {a.cols}x{a.rows} cell grid, "
          f"digest {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
