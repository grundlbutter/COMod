#!/usr/bin/env python3
r"""mapdigest.py -- reduce a rendered map to a per-TILE signature, so two
renderers can be compared tile-by-tile instead of by one global number.

WHY THIS EXISTS
---------------
The oracle for "does a client draw this map right" is a SECOND implementation:
retail `6271/Conquer.exe` is the ground truth, VibeCO is the subject under test
(owner, 2026-09-10). A global correlation says "close" or "not"; it does not say
WHICH tile is wrong. This is the comparison currency between them: any renderer's
output -- mapedit's own render, a client capture, VibeCO's frame -- reduces to
the SAME `{(tx, ty): signature}` shape via `digest_image`, and `compare` scores
them per tile.

RENDERER-AGNOSTIC BY CONSTRUCTION. `digest_image` takes raw RGBA plus a tile
size; it does not know or care what drew the pixels. `digest_map` is a
convenience that renders a map layer through `tools/mapedit.py` and digests that.
So the format is defined once and every producer emits it.

THE SIGNATURE, and why it is not a hash. Two correct renderers differ in
resampling, subpixel placement and DPI; an exact hash would false-alarm on every
tile. Each tile is instead mean-pooled to an `POOL x POOL` grid of RGB plus a
mean colour and an alpha coverage fraction -- a small vector that is tolerant of
those nuisance differences and sensitive to the ones that matter: a wrong
terrain, a wrong blend, a wrong sub-quad diagonal. `compare` scores a tile by
mean absolute difference over the pooled cells, normalised to a [0, 1]
similarity.

THE PROJECTION CAVEAT, stated because it is the load-bearing follow-on.
`digest_image` reads an AXIS-ALIGNED tile grid over the RGBA. That is exact for
mapedit's painted-image (orthographic) space and for any renderer that can emit
the map orthographically. A game client's screen frame is ISOMETRIC -- a tile is
a diamond, not a rectangle -- so an isometric capture must first be resampled to
the orthographic tile grid (project each tile's four corners, sample) before it
is digested. That projection step is a separate piece; this module defines the
currency and does the orthographic case, which is what mapedit produces today and
what the two clients must be reduced TO.

    py -3 tools/mapdigest.py digest --map ninja01_new --layer ground --z 1 --out a.json
    py -3 tools/mapdigest.py digest-image frame.png --tile 256 --out b.json
    py -3 tools/mapdigest.py compare a.json b.json           # per-tile verdict
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

#: Per-tile pooled grid edge. 8x8 keeps the macro structure of a tile (where
#: ground is, how it blends) while discarding the per-pixel grain two correct
#: renderers disagree on. 192 bytes of RGB per tile.
POOL = 8

#: Default tile edge in pixels -- mapedit's TILE. A digest records the tile size
#: it was taken at; `compare` refuses two digests taken at different sizes.
DEFAULT_TILE = 256


def _pool_tile(rgba: bytes, w: int, h: int, x0: int, y0: int, tpx: int):
    """Mean-pool one tile region into POOL*POOL cells of (r, g, b) plus the
    tile's mean colour and its alpha coverage (fraction of pixels with a > 127).

    A tile that runs off the image edge pools only the pixels that exist; a cell
    with no pixels is 0. This is deterministic and independent of how the RGBA
    was produced.
    """
    cells = [[0, 0, 0, 0] for _ in range(POOL * POOL)]   # r, g, b, count
    covered = 0
    total = 0
    sr = sg = sb = 0
    for py in range(tpx):
        iy = y0 + py
        if iy < 0 or iy >= h:
            continue
        cy = (py * POOL) // tpx
        row = (iy * w) * 4
        for px in range(tpx):
            ix = x0 + px
            if ix < 0 or ix >= w:
                continue
            o = row + ix * 4
            r, g, b, a = rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]
            cx = (px * POOL) // tpx
            cell = cells[cy * POOL + cx]
            cell[0] += r
            cell[1] += g
            cell[2] += b
            cell[3] += 1
            total += 1
            if a > 127:
                covered += 1
            sr += r
            sg += g
            sb += b
    pooled = bytearray(POOL * POOL * 3)
    for i, cell in enumerate(cells):
        n = cell[3] or 1
        pooled[i * 3] = cell[0] // n
        pooled[i * 3 + 1] = cell[1] // n
        pooled[i * 3 + 2] = cell[2] // n
    n = total or 1
    return {
        "pool": pooled.hex(),
        "mean": [sr // n, sg // n, sb // n],
        "cov": round(covered / n, 4),
    }


def digest_image(rgba: bytes, w: int, h: int, tile: int = DEFAULT_TILE) -> dict:
    """Reduce a whole RGBA image to a per-tile digest over an axis-aligned grid.

    Renderer-agnostic: `rgba` may be mapedit's render, a client capture already
    resampled to orthographic tile space, or VibeCO's frame. Tiles are keyed
    ``"tx,ty"`` so the result is plain JSON.
    """
    if len(rgba) != w * h * 4:
        raise ValueError(f"rgba is {len(rgba)} bytes, expected {w * h * 4} "
                         f"for {w}x{h} RGBA")
    cols = (w + tile - 1) // tile
    rows = (h + tile - 1) // tile
    tiles = {}
    for ty in range(rows):
        for tx in range(cols):
            tiles[f"{tx},{ty}"] = _pool_tile(rgba, w, h, tx * tile, ty * tile, tile)
    return {"tile": tile, "pool": POOL, "w": w, "h": h,
            "cols": cols, "rows": rows, "tiles": tiles}


def _tile_score(a: dict, b: dict) -> float:
    """Similarity of two tile signatures in [0, 1]: 1 minus the mean absolute
    difference of the pooled RGB cells, normalised by 255."""
    pa, pb = bytes.fromhex(a["pool"]), bytes.fromhex(b["pool"])
    if len(pa) != len(pb) or not pa:
        return 0.0
    diff = sum(abs(x - y) for x, y in zip(pa, pb))
    return round(1.0 - diff / (len(pa) * 255), 6)


def compare(a: dict, b: dict, *, threshold: float = 0.9) -> dict:
    """Per-tile verdict of digest `b` (subject) against digest `a` (oracle).

    Returns a summary and the tiles that score below `threshold`, worst first --
    the tiles the subject renderer drew differently from the oracle.
    """
    if (a.get("tile"), a.get("pool")) != (b.get("tile"), b.get("pool")):
        raise ValueError("digests differ in tile size or pool; not comparable "
                         f"({a.get('tile')}/{a.get('pool')} vs "
                         f"{b.get('tile')}/{b.get('pool')})")
    ta, tb = a["tiles"], b["tiles"]
    keys = sorted(set(ta) & set(tb), key=lambda k: (int(k.split(",")[1]), int(k.split(",")[0])))
    only_a = sorted(set(ta) - set(tb))
    only_b = sorted(set(tb) - set(ta))
    scores = {k: _tile_score(ta[k], tb[k]) for k in keys}
    below = sorted((k for k in keys if scores[k] < threshold), key=lambda k: scores[k])
    n = len(scores) or 1
    return {
        "tiles_compared": len(scores),
        "mean_score": round(sum(scores.values()) / n, 6),
        "min_score": min(scores.values()) if scores else None,
        "below_threshold": len(below),
        "threshold": threshold,
        "worst": [{"tile": k, "score": scores[k]} for k in below[:32]],
        "only_in_oracle": only_a,
        "only_in_subject": only_b,
    }


def digest_map(map_name: str, layer: str = "ground") -> dict:
    """Render one layer of a map through `tools/mapedit.py` and digest it, one
    digest tile PER MAP CELL (`pm.grid` px at z=1).

    A map cell is the renderer-independent unit both clients share, so keying
    the digest on it is what lets a mapedit render, a retail-6271 capture and a
    VibeCO frame be compared cell-for-cell. This is the ORACLE-side convenience;
    the subject side (a capture already resampled to this cell grid) goes through
    `digest_image` on its own pixels with the same `tile`.

    Rendered CELL BY CELL through `mapedit.tile_rect`, not as one full-map
    buffer: a world map is gigabytes at z=1, and the per-cell path is flat in
    memory (one cell at a time) and handles any size. Each cell's signature
    comes from the same `_pool_tile` `digest_image` uses, so a cell rendered
    here and the same cell cut out of a full-frame capture reduce identically.
    """
    import mapedit                                    # noqa: E402
    art = mapedit.MapEditor().get(map_name)
    pm = getattr(art, "pm", None)
    if pm is None:
        raise ValueError(f"{map_name!r} has no puzzle ground to render")
    grid = pm.grid
    cols, rows = pm.pul_w, pm.pul_h
    tiles = {}
    for ty in range(rows):
        for tx in range(cols):
            rect = mapedit.tile_rect(tx, ty, 1, tile=grid)
            w, h, rgba = art.render_layer(layer, rect, 1)
            tiles[f"{tx},{ty}"] = _pool_tile(bytes(rgba), w, h, 0, 0, grid)
    return {"tile": grid, "pool": POOL, "w": pm.px_w, "h": pm.px_h,
            "cols": cols, "rows": rows, "tiles": tiles}


def _png_to_rgba(png: bytes):
    """Decode a PNG to (w, h, rgba) with Pillow -- the same dependency the rest
    of the tree uses for image IO (`core/dds.py`)."""
    from PIL import Image                             # noqa: PLC0415
    import io
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    return im.width, im.height, im.tobytes()


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-tile map render digest and compare.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("digest", help="digest a map layer rendered via mapedit "
                       "(one tile per map cell)")
    d.add_argument("--map", required=True)
    d.add_argument("--layer", default="ground")
    d.add_argument("--out", required=True)

    di = sub.add_parser("digest-image", help="digest a PNG on disk already "
                        "resampled to the map's cell grid")
    di.add_argument("image")
    di.add_argument("--tile", type=int, required=True,
                    help="cell-grid px this image is at (must match the oracle "
                         "digest's `tile`)")
    di.add_argument("--out", required=True)

    c = sub.add_parser("compare", help="compare two digests (oracle, subject)")
    c.add_argument("oracle")
    c.add_argument("subject")
    c.add_argument("--threshold", type=float, default=0.9)

    a = ap.parse_args(argv)
    if a.cmd == "digest":
        dig = digest_map(a.map, a.layer)
        Path(a.out).write_text(json.dumps(dig), encoding="utf-8")
        print(f"digest: {a.map}/{a.layer} -> {dig['cols']}x{dig['rows']} "
              f"tiles (tile={dig['tile']}px), {a.out}")
        return 0
    if a.cmd == "digest-image":
        w, h, rgba = _png_to_rgba(Path(a.image).read_bytes())
        dig = digest_image(rgba, w, h, tile=a.tile)
        Path(a.out).write_text(json.dumps(dig), encoding="utf-8")
        print(f"digest-image: {a.image} -> {dig['cols']}x{dig['rows']} tiles, {a.out}")
        return 0
    rep = compare(_load(a.oracle), _load(a.subject), threshold=a.threshold)
    print(json.dumps(rep, indent=2))
    return 0 if rep["tiles_compared"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
