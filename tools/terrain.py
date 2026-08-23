#!/usr/bin/env python3
"""
terrain.py -- turn a DMap cell grid into something the existing WebGL viewport
can draw, without touching the viewport.

    py -3 tools/terrain.py newbie --at 61,109 -o out/viewer/terrain.json

WHY A MESH AND NOT A NEW RENDERER
---------------------------------
`tools/webui/gl.js` already draws indexed triangle lists with the engine's own
winding, culling and UV conventions, and every one of those was hard to get
right. So the ground is emitted in **exactly the shape `mesh_to_json` produces
for a C3 chunk** -- same keys, same ordering, same `bboxRender` -- and handed to
the same `Viewer.setMeshes()`. The renderer cannot tell the difference, which
means the map and the character are drawn by one code path rather than two.

WHAT IS DRAWN
-------------
The cell grid. Per-cell elevation from the `.DMap` (`i16`, signed), per-cell
passability, per-cell surface id. All of it is file data, verified by the row
checksum that `core/dmap.py` computes. **Not on `newbie`**: its elevation field
is entirely zero, so its 132/132 passes identically under an unsigned read and
validates only the stride, field order and mask/surface widths -- it is
structurally incapable of validating the elevation read. The elevation read is
verified on `desert` (984/984, 75 rows with a negative cell) and `d_antre01`
(908/908, 454), where an unsigned read fails **exactly** those rows and no
others. See `docs/dmap_elevation.md` 5.2.

**And, since the placement was recovered, the map's own painted ground art.**
`tools/puzzle.py` has the rule and the evidence; the short version is that a
cell-grid lattice point (gx, gy) lands on the painted image at

    px = (gx - gy + K) * 32,   py = (gx + gy - K) * 16,   K = pul.width * G / 64

i.e. one cell is a 64x32 pixel isometric diamond and the cell axes run along the
painted image's diagonals. That is a **linear** map applied to the very corner
coordinates this module already emits, so the art needs no new geometry at all
-- only different UVs and a different texture. `build_patch(..., puzzle=pm)`
switches the UVs from window-relative to art-relative and `art_texture()` cuts
the matching piece out of the painted image.

Without a `puzzle=` the old behaviour is unchanged: the ground is shaded by
passability and surface id through `patch_texture()`, which is still the right
view for the four maps whose art is the undecoded `.pux` format, for the `sky`
family whose ground is scenery rather than paint, and for debugging movement.

SCALE
-----
`CELL` is **recovered**, as of `docs/map_scenery.md` 6.1. It used to be a
chosen 100.0, and that was wrong by a factor of 2.21.

Two things are solid. Characters are drawn at the size they were authored --
`ini/AdditiveSize.json`'s per-appearance `scale` is a percentage and reads 100
on 27 of its 30 rows -- and body `003000000`'s `v_body` chunk is 176.2 units
tall. And a map cell is a 64 x 32 **pixel** diamond in the painted art, which
`tools/puzzle.py` proves on 131 of 132 maps.

What links them was NOT solid, and this is where an earlier version went
wrong. `CPuzzleBlockX::Create(w, h, nx, ny)` builds the ground's vertices as
`w*i/nx`, but `w` comes from the CALLER, and the caller is inside the packed
exe. Reading `w` as the image's pixel width -- i.e. one world unit is one
painted pixel, `CELL = 32*sqrt(2)` -- is an assumption, and
`docs/ground_art.md` 6 always listed it as OPEN. Held against the real game it
is wrong: it makes characters roughly 1.5x too big for the world.

So the scale lives on the MAP, not on the character:

    CELL = 64.0        the ground is drawn sqrt(2) larger than its pixels
    FIGURE_SCALE = 1.0 characters at their authored size, as the engine does

which puts a character at ~1.69 cell widths -- what the game looks like. The
cost is that the painted art is magnified sqrt(2), so it is sampled above its
native resolution.

`ZSCALE` is **0**: characters are not lifted by terrain height at all. The
DMap elevation is gameplay data -- it gates jumps and records which tile you
stand on, and is never drawn -- while a platform's height is painted into the
art, so the placement rule already puts a character on top of the step. See
its own comment below.

Read-only. Imports `client.gamemap` for the grid type so the client and the
renderer cannot drift apart on what "walkable" means.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

#: World units per map cell.
#:
#: 64.0, which draws the painted ground at **sqrt(2) times its native pixel
#: size** and lets characters be drawn at the size they were authored
#: (`FIGURE_SCALE` 1.0, which is what `ini/AdditiveSize.json` says the engine
#: does). See the module docstring; the short version is that the previous
#: 45.2548 = 32*sqrt(2) made one world unit one painted pixel, and that came
#: from ASSUMING what `CPuzzleBlockX::Create` is passed for its block width --
#: an assumption `docs/ground_art.md` 6 always listed as OPEN, because the
#: caller is inside the packed exe. Held against the game, it makes characters
#: about 1.5x too large; the map is drawn bigger than its pixels instead.
CELL = 64.0
#: World units per unit of DMap elevation: **0.0 -- characters are not
#: lifted at all**, and that is a recovered conclusion rather than the last
#: value in a sequence of guesses.
#:
#: The elevation `i16` is GAMEPLAY data, not a drawing offset. Every use of
#: it on the server is either recording which tile you stand on
#: (`set_elevation`, after a walk or a jump) or gating a jump
#: (`sample_elevation`, the "too steep" rule -- `docs/jump.md` 2.3). Nothing
#: positional, nothing rendered, nothing sent to a client for drawing.
#:
#: And the art does not need it. The ground draws FLAT because a platform's
#: height is painted into the picture, and the placement rule lands a cell on
#: its own painted surface -- `docs/ground_art.md` 3.2 measured 100% of
#: walkable cells on painted art across several maps. So the top of Twin
#: City's central square is already where a character standing there belongs;
#: lifting it by the elevation counts the height twice, which is why 5.43,
#: 1.0, 0.5 and 0.25 all read as too high in turn.
#:
#: Kept as a constant at zero rather than deleted, so the finding is visible
#: at the point someone would reach for it, and so a map that genuinely needs
#: a lift has somewhere to say so.
ZSCALE = 0.0
#: Half-width of the window drawn around the player, in cells. 24 gives a 49x49
#: patch -- 9,604 vertices, comfortably inside the 65,535 a `Uint16` index
#: buffer can address, which is what `gl.js` uses.
DEFAULT_RADIUS = 24
#: Pixels per cell in the generated ground texture. 8 keeps cell edges crisp
#: under `gl.js`'s LINEAR magnification without needing a filter change there.
TEX_CELL_PX = 8


def world_xy(cx: float, cy: float) -> tuple[float, float]:
    """Map cell coordinates -> world X/Y.

    Map +y runs south (DMap row order). World -Y is where the viewport's default
    camera stands, and the character corpus is authored facing -Y, so mapping
    map-south to world -Y makes a character walking south walk toward the
    camera, facing it. That is a convenience, and it is the only reason for the
    sign; nothing in the files demands it.
    """
    return cx * CELL, -cy * CELL


class Window:
    """The rectangle of cells being drawn, clamped to the map."""

    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, grid, cx: int, cy: int, radius: int) -> None:
        self.x0 = max(0, cx - radius)
        self.y0 = max(0, cy - radius)
        self.x1 = min(grid.width - 1, cx + radius)
        self.y1 = min(grid.height - 1, cy + radius)

    @property
    def w(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def h(self) -> int:
        return self.y1 - self.y0 + 1

    def to_json(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1,
                "w": self.w, "h": self.h}


def build_patch(grid, cx: int, cy: int, radius: int = DEFAULT_RADIUS,
                *, cell: float = CELL, zscale: float = ZSCALE,
                puzzle=None) -> dict:
    """A window of the cell grid, as one mesh in `mesh_to_json`'s shape.

    One quad per cell, with its own four corners rather than a shared vertex
    lattice. That is 4x the vertices, and it is deliberate: shared corners would
    average elevation across a cliff edge and smear the boundary between a
    walkable cell and a blocked one, which is exactly the thing this view exists
    to show.

    `puzzle` is an optional `tools/puzzle.PuzzleMap`. Given one, the UVs address
    the map's painted background instead of the generated passability texture --
    the geometry is identical either way, which is the whole point of the
    placement rule being linear.
    """
    win = Window(grid, int(cx), int(cy), int(radius))
    rect = None
    if puzzle is not None:
        rect = puzzle.window_rect(win.x0, win.y0, win.x1, win.y1)
    pos: list[float] = []
    nrm: list[float] = []
    uv0: list[float] = []
    idx: list[int] = []
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]

    def elev(x: int, y: int) -> float:
        return grid.elevation_at(x, y) * zscale

    if rect is not None:
        rx0, ry0, rx1, ry1 = rect
        rw = float(rx1 - rx0) or 1.0
        rh = float(ry1 - ry0) or 1.0

        def uv_of(gx: int, gy: int) -> tuple[float, float]:
            """The painted image, cropped to `rect`. Same corner coordinates,
            different projection -- one linear map, no resampling."""
            px, py = puzzle.corner_px(gx, gy)
            return (px - rx0) / rw, (py - ry0) / rh
    else:
        def uv_of(gx: int, gy: int) -> tuple[float, float]:
            """UVs span the window, so one generated texture covers the whole
            patch and each cell lands on its own block of texels."""
            return (gx - win.x0) / win.w, (gy - win.y0) / win.h

    n = 0
    for y in range(win.y0, win.y1 + 1):
        for x in range(win.x0, win.x1 + 1):
            z = elev(x, y)
            # Cell (x, y) covers world [x, x+1) x [y, y+1) before the sign flip.
            corners = ((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1))
            base = n
            for gx, gy in corners:
                wx, wy = gx * cell, -gy * cell
                pos += [wx, wy, z]
                nrm += [0.0, 0.0, 1.0]
                uv0 += list(uv_of(gx, gy))
                lo[0] = min(lo[0], wx); hi[0] = max(hi[0], wx)
                lo[1] = min(lo[1], wy); hi[1] = max(hi[1], wy)
                lo[2] = min(lo[2], z); hi[2] = max(hi[2], z)
                n += 1
            # Counter-clockwise in this right-handed Z-up frame, matching what
            # gl.js expects of a C3 chunk (frontFace CCW + cullFace BACK).
            idx += [base, base + 3, base + 2, base, base + 2, base + 1]

    if not pos:
        lo = hi = [0.0, 0.0, 0.0]

    return {
        "index": 0,
        "name": f"terrain:{getattr(grid, 'name', '?')}",
        "tag": "TERR",
        "vertexCount": n,
        "vertexCountA": n,
        "vertexCountB": 0,
        "faceCount": len(idx) // 3,
        "positions": pos,
        "normals": nrm,
        "uv0": uv0,
        "uv1": [],
        "colors": [],
        "indices": idx,
        "bboxRender": [lo, hi],
        "bboxDeclared": [lo, hi],
        "label": "",
        "labelRaw": 0,
        "isC3ExpColor": False,
        # Two-sided: the ground has no back to hide, and a winding mistake here
        # would show as an invisible map rather than as anything diagnosable.
        "twoSided": True,
        "billboard": False,
        "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "matrixIdentity": True,
        "motionApplied": False,
        "frameCount": 0,
        "bones": 0,
        "skinned": False,
        "normalsFromFile": False,
        "keys": {"alphas": [], "draws": [], "changeTexs": []},
        "isSocket": False,
        "trailing": 0,
        # -- our own additions; gl.js ignores unknown keys --
        "window": win.to_json(),
        "cell": cell,
        "zscale": zscale,
        "synthetic": True,
        "source": "DMap cell grid (core/dmap.py)",
        "ground": {
            "source": "puzzle" if rect is not None else "passability",
            "rect": list(rect) if rect is not None else None,
            "puzzle": puzzle.pul_path if rect is not None else "",
            "consistent": bool(puzzle.consistent) if rect is not None else None,
        },
    }


# -- the ground texture -----------------------------------------------------

#: (r, g, b) for each thing a cell can be. Deliberately not naturalistic: this
#: is a data view, and reading it as "grass" would be worse than reading it as
#: "walkable".
COLOUR_WALKABLE = (74, 104, 78)
COLOUR_BLOCKED = (52, 46, 44)
COLOUR_EDGE = (30, 28, 27)
COLOUR_PLAYER = (232, 196, 96)


def _png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal 8-bit RGB PNG. Same approach as `core/dmap.py`'s encoder, kept
    local so this module has no import-time dependency on it."""
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


def patch_texture(grid, win_json: dict, *, px: int = TEX_CELL_PX,
                  mark: "tuple[int, int] | None" = None) -> bytes:
    """A PNG the size of the window, `px` pixels per cell.

    Walkable and blocked are shaded differently and each cell gets a one-pixel
    darker border, so the grid is legible and a step can be counted by eye --
    which is how a movement bug gets spotted in a screenshot.
    """
    x0, y0, w, h = win_json["x0"], win_json["y0"], win_json["w"], win_json["h"]
    tw, th = w * px, h * px
    buf = bytearray(tw * th * 3)
    for cy in range(h):
        for cx in range(w):
            gx, gy = x0 + cx, y0 + cy
            walk = grid.walkable(gx, gy)
            base = COLOUR_WALKABLE if walk else COLOUR_BLOCKED
            if mark and (gx, gy) == tuple(mark):
                base = COLOUR_PLAYER
            # Surface id, where a map uses more than one, tints the cell so the
            # terrain types are distinguishable without inventing art for them.
            surf = 0
            if getattr(grid, "surface", None) is not None and grid.inside(gx, gy):
                surf = grid.surface[gy * grid.width + gx] or 0
            tint = (surf * 37) % 40 if surf else 0
            col = tuple(min(255, c + tint) for c in base)
            for py in range(px):
                row = (cy * px + py) * tw
                edge_row = py == 0 or py == px - 1
                for pxi in range(px):
                    c = COLOUR_EDGE if (edge_row or pxi == 0 or pxi == px - 1) else col
                    o = (row + cx * px + pxi) * 3
                    buf[o] = c[0]
                    buf[o + 1] = c[1]
                    buf[o + 2] = c[2]
    return _png_rgb(tw, th, bytes(buf))


def art_texture(puzzle, patch: dict, *, scale: int = 1, backdrops=None,
                scenery=None, sprites=None, time_ms: int = 0) -> bytes:
    """The painted ground under a patch, as a PNG.

    The rectangle comes from the patch itself (`patch["ground"]["rect"]`) so the
    texture and the UVs cannot disagree: both are the same numbers, computed
    once in `build_patch`.

    Optional layers, in the order the client draws them:

        backdrops   `tools/puzzle.Backdrop` planes, tiled behind everything.
                    Without these the off-art part of a map is flat VOID; with
                    them it is the sea or sky the map was drawn against.
        scenery     `tools/scene.Placed` TERRAIN objects, drawn on the ground
                    in painter's order.

    COVER sprites are deliberately NOT accepted here: a cover is defined by
    drawing in front of the player, so it cannot be baked into the ground.
    `cover_texture()` is its own layer.
    """
    rect = (patch.get("ground") or {}).get("rect")
    if rect is None:
        raise ValueError("this patch was built without a puzzle map")
    rect = tuple(rect)
    import puzzle as puzzlemod                              # noqa: PLC0415
    if backdrops:
        w, h, rgb = _ground_over_backdrops(puzzle, rect, backdrops, scale)
    else:
        w, h, rgb = puzzle.render(rect, scale=scale)
    if scenery:
        import scene as scenemod                            # noqa: PLC0415
        rgb, _ = scenemod.composite(puzzle, rect, rgb, scenery,
                                    sprites or scenemod.SpriteCache(),
                                    scale=scale, time_ms=time_ms)
    return puzzlemod.encode_png(w, h, rgb)


def _ground_over_backdrops(puzzle, rect, backdrops, scale: int):
    """Backdrop planes first, then the ground painted over them wherever the
    ground has art. The ground's own VOID is what lets a backdrop show."""
    import puzzle as puzzlemod                              # noqa: PLC0415
    w = h = 0
    base = None
    for b in backdrops:
        bw, bh, brgb = b.render_tiled(rect, scale=scale)
        if base is None:
            w, h, base = bw, bh, bytearray(brgb)
        else:
            n = min(len(base), len(brgb))
            base[:n] = brgb[:n]
    gw, gh, grgb = puzzle.render(rect, scale=scale)
    if base is None:
        return gw, gh, grgb
    void = bytes(puzzlemod.VOID)
    out = bytearray(base)
    for i in range(0, min(len(out), len(grgb)), 3):
        if grgb[i:i + 3] != void:
            out[i:i + 3] = grgb[i:i + 3]
    return w, h, bytes(out)


def cover_texture(puzzle, patch: dict, covers, sprites=None, *, scale: int = 1,
                  time_ms: int = 0) -> bytes:
    """The COVER layer over a patch, as an RGBA PNG.

    Its rectangle is the patch's, so it lines up with the ground pixel for
    pixel and the page can hang it on a quad with the same UVs.
    """
    rect = (patch.get("ground") or {}).get("rect")
    if rect is None:
        raise ValueError("this patch was built without a puzzle map")
    import scene as scenemod                                # noqa: PLC0415
    w, h, rgba = scenemod.cover_layer(puzzle, tuple(rect), covers,
                                      sprites or scenemod.SpriteCache(),
                                      scale=scale, time_ms=time_ms)
    return scenemod.encode_png_rgba(w, h, rgba)


def open_scenery(grid, *, root=None):
    """The `Scenery` for a `client.gamemap.GameMap`, or None with a reason.

    Same contract as `open_puzzle`: never raises, never a hard dependency.
    """
    try:
        import scene as scenemod                            # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        return None, f"tools/scene.py unavailable: {e}"
    try:
        sc = scenemod.for_map(getattr(grid, "name", "") or "", root=root)
        if not sc.scenes and not sc.covers:
            return sc, "this map places no scene or cover layers"
        return sc, ""
    except Exception as e:                                  # noqa: BLE001
        return None, str(e)


def open_puzzle(grid, *, root=None):
    """The `PuzzleMap` for a `client.gamemap.GameMap`, or None with a reason.

    Never raises and never a hard dependency: a client with no install, or a map
    whose art is the undecoded `.pux` format, gets `(None, why)` and falls back
    to `patch_texture`.
    """
    try:
        import puzzle as puzzlemod                         # noqa: PLC0415
    except Exception as e:                                 # noqa: BLE001
        return None, f"tools/puzzle.py unavailable: {e}"
    try:
        lib = puzzlemod.PuzzleLibrary(root)
        pm = lib.get(getattr(grid, "name", "") or "")
        return pm, ("" if pm else lib.reason)
    except Exception as e:                                 # noqa: BLE001
        return None, str(e)


# -- CLI --------------------------------------------------------------------

def _load(name_or_id: str):
    from client import gamemap as GM                     # noqa: PLC0415
    lib = GM.MapLibrary()
    try:
        return lib.load(int(name_or_id)), lib
    except ValueError:
        pass
    idx = lib.index or {}
    for doc_id in idx:
        if lib.name_for(doc_id).lower() == name_or_id.lower():
            return lib.load(doc_id), lib
    return None, lib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", help="a map name (newbie) or a GameMap.json DocumentId (1010)")
    ap.add_argument("--at", default="", metavar="X,Y",
                    help="centre the window here (default: the map's middle)")
    ap.add_argument("--radius", type=int, default=DEFAULT_RADIUS)
    ap.add_argument("-o", "--out", type=Path,
                    default=_REPO / "out" / "viewer" / "terrain.json")
    ap.add_argument("--png", type=Path, default=None,
                    help="also write the ground texture here")
    ap.add_argument("--no-art", action="store_true",
                    help="shade by passability even where the painted art is known")
    ap.add_argument("--scale", type=int, default=1,
                    help="integer decimation of the painted ground texture")
    ap.add_argument("--no-scenery", action="store_true",
                    help="ground art only: no TERRAIN scene objects")
    ap.add_argument("--no-background", action="store_true",
                    help="leave the off-art region as VOID")
    ap.add_argument("--cover-png", type=Path, default=None,
                    help="also write the COVER layer (RGBA) here")
    a = ap.parse_args(argv)

    grid, lib = _load(a.map)
    if grid is None:
        print(f"no map data: {lib.reason}", file=sys.stderr)
        return 1
    if a.at:
        cx, cy = (int(v) for v in a.at.split(","))
    else:
        cx, cy = grid.width // 2, grid.height // 2

    pm, why = (None, "--no-art") if a.no_art else open_puzzle(grid)
    patch = build_patch(grid, cx, cy, a.radius, puzzle=pm)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(patch), encoding="utf-8")
    print(f"{grid.name}.DMap {grid.width}x{grid.height}, window "
          f"{patch['window']['w']}x{patch['window']['h']} at ({cx},{cy})")
    print(f"{patch['vertexCount']} verts, {patch['faceCount']} tris -> {a.out}")
    if pm is not None:
        print(f"ground art  : {pm.pul_path}  rect {patch['ground']['rect']}"
              f"{'' if pm.consistent else '   (map size disagrees with the art -- see puzzle.py --verify)'}")
    else:
        print(f"ground art  : not used ({why or 'unavailable'}); "
              f"shading by passability instead")
    sc, why_sc = (None, "--no-scenery") if a.no_scenery else open_scenery(grid)
    if sc is not None:
        print(f"scenery     : {len(sc.scenes)} scene parts, {len(sc.covers)} covers"
              + (f"   MISSING {sc.missing_scenes}" if sc.missing_scenes else ""))
        ov = getattr(grid, "scene_overlay", None)
        if ov:
            print(f"passability : {ov.get('opened', 0)} cells opened by scenery, "
                  f"{ov.get('blocked', 0)} blocked")
    else:
        print(f"scenery     : not drawn ({why_sc})")
    bds = []
    if pm is not None and not a.no_background:
        try:
            import puzzle as puzzlemod                      # noqa: PLC0415
            bds = puzzlemod.PuzzleLibrary().backdrops(grid.name)
        except Exception:                                   # noqa: BLE001
            bds = []
        print(f"background  : {[b.path for b in bds] or 'none'}")
    if a.png:
        a.png.parent.mkdir(parents=True, exist_ok=True)
        if pm is not None:
            a.png.write_bytes(art_texture(
                pm, patch, scale=a.scale, backdrops=bds,
                scenery=(sc.scenes if sc else None)))
        else:
            a.png.write_bytes(patch_texture(grid, patch["window"], mark=(cx, cy)))
        print(f"ground texture -> {a.png}")
    if a.cover_png and pm is not None and sc is not None:
        a.cover_png.parent.mkdir(parents=True, exist_ok=True)
        a.cover_png.write_bytes(cover_texture(pm, patch, sc.covers, scale=a.scale))
        print(f"cover layer    -> {a.cover_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
