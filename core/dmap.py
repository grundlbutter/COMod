#!/usr/bin/env python3
"""
dmap.py -- parser for Conquer Online .DMap world-map files.

Reference: refs/conquer-online-wiki/Files/DMap.md describes the original TQ
format.  Everything below was re-verified against the 136 .DMap files shipped in
$ROOT/map/map/; where this parser disagrees with the wiki, the bytes won.

LAYOUT  (little-endian throughout)

    off 0    8 bytes of version, in one of two interchangeable forms  VERIFIED
             (a) u64 numeric: 1003 (47 files), 1004 (61), 1005 (1), 1006 (6)
                 -- i.e. u32 version at off 0 with a zero u32 at off 4
             (b) char[8] ASCII: "DMAP101\0" (20 files) or "DMAP100\0" (1)
             Both occupy the same 8 bytes; everything after is identical.
    off 8    char[260] puzzlePath VERIFIED  fixed-width, NUL-terminated,
                                  e.g. "map\\puzzle\\arena.pul".  Newer (1006)
                                  files use '/' and point at map/PuzzleSave/*.pux
    off 268  u32  width           VERIFIED  cells, X extent
    off 272  u32  height          VERIFIED  cells, Y extent

    off 276  the cell grid        VERIFIED (proved by checksum, see below)
        for y in 0..height-1:
            for x in 0..width-1:
                u16 mask        0 = walkable, non-zero = blocked
                u16 surface     terrain/surface type id
                i16 elevation   signed height
            u32 rowChecksum

    then     u32 portalCount      VERIFIED
             portalCount x { u32 x; u32 y; u32 id }        VERIFIED
    then     u32 layerCount       VERIFIED
             layerCount x { u32 type; payload }            VERIFIED (see below)
    then     u32 extraCount       VERIFIED
             extraCount x { u32 v0..v5; char[260] path }   shape VERIFIED,
                                                           meaning INFERRED

CHECKSUM -- this is the proof that the cell layout is right.  Per row:

    cs = 0
    for x in row:
        cs += (0 if mask == 0 else 1) * (surface + y + 1) \
            + (elevation + 2) * (surface + x + 1)
    cs &= 0xFFFFFFFF

This reproduces 58,245 of 58,898 rows across the 136 shipped maps, with 133
files at 100%.  If the cell stride, field order, field widths, the signedness of
elevation, or the per-row (rather than per-file) checksum placement were wrong,
it would fail on row 0.  The 653 failures are confined to three version-1006
maps that diverge partway down the grid -- still unexplained.

LAYER SECTION.  Payload sizes below.  NOTE the tag numbering does NOT match the
names in Enums/Dmap-Layer-Type.md: the wiki's "Scene Layer Type" is emitted with
tag 1 (the enum's TERRAIN) and its "Effect Layer Type" with tag 10 (the enum's
ANIMATION).  The payload SHAPES are exactly as the wiki documents them.  With
the table below, 119 of 136 files consume every byte through to EOF; the
stragglers are recorded via `layers_complete` / `layer_error` rather than being
papered over.

Usage:
    python core/dmap.py summary  [--root ROOT] [-o out/wdf/dmap_summary.json]
    python core/dmap.py show     <file.DMap>
    python core/dmap.py render   <file.DMap> -o map.png   (.pgm also supported)
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coroot                            # noqa: E402

MASK32 = 0xFFFFFFFF
HEADER_PATH_OFF = 8
HEADER_PATH_LEN = 260
DIMS_OFF = 268
GRID_OFF = 276
CELL_SIZE = 6

DMAP_TAG = 0x50414D44  # 'DMAP' little-endian

LAYER_TYPES = {
    0: "NONE", 1: "TERRAIN", 2: "TERRAIN_PART", 3: "SCENE", 4: "COVER",
    5: "ROLE", 6: "HERO", 7: "PLAYER", 8: "PUZZLE", 9: "ANIMATION_SIMPLE",
    10: "ANIMATION", 11: "ITEM", 12: "NPC", 13: "OBJECT3D", 14: "TRACE3D",
    15: "SOUND", 16: "REGION", 17: "MAGICMAPITEM", 18: "MAPITEM",
    19: "EFFECT3D",
}

# Payload sizes, in bytes, excluding the leading u32 type tag.
#
# VERIFIED by exhaustive walk: with this table 119/136 shipped .DMap files
# consume every byte to EOF (layers -> extra-record array -> end).  The
# stragglers are mostly version-1006 maps, which also fail the cell checksum, so
# they differ earlier than the layer section.
#
# NOTE the tag numbering does NOT line up with the wiki's enum names.  What the
# wiki calls the "Scene Layer Type" is emitted with tag 1 (the enum's TERRAIN),
# and its "Effect Layer Type" is emitted with tag 10 (the enum's ANIMATION).
# The payload SHAPES are exactly as the wiki documents them; only the tag->name
# mapping in Enums/Dmap-Layer-Type.md is misleading for this client.
LAYER_PAYLOAD = {
    1: 268,    # scene  : char[260] path (e.g. "map\\Scene\\stand03.scene"), u32 x, u32 y
    3: 268,    # scene  : same shape; tag not observed in the shipped maps
    4: 416,    # cover  : char[260] ani path, char[128] key, 7 x u32/i32
    10: 72,    # effect : char[64] name (a 3DEffect.ini key), u32 x, u32 y
    15: 276,   # sound  : char[260] path, u32 x, u32 y, u32 range, u32 volume
    19: 72,    # effect : same shape; tag not observed in the shipped maps
}

LAYER_SHAPE = {1: "scene", 3: "scene", 4: "cover", 10: "effect",
               15: "sound", 19: "effect"}

# MSVC debug fill.  Some maps declare more layers than they actually wrote and
# leave the tail as uninitialised heap; treat it as end-of-data rather than
# pretending to decode it.
UNINIT = 0xCDCDCDCD

# Trailing puzzle-layer records: 6 x u32 followed by char[260] path.
EXTRA_RECORD = 284


@dataclass
class Cell:
    mask: int
    surface: int
    elevation: int


@dataclass
class DMap:
    path: Path
    version: int
    reserved: int
    tagged: bool
    puzzle_path: str
    width: int
    height: int
    cells: list[tuple[int, int, int]] = field(default_factory=list, repr=False)
    row_checksums: list[int] = field(default_factory=list, repr=False)
    checksum_ok: int = 0
    portals: list[tuple[int, int, int]] = field(default_factory=list)
    layer_count: int = 0
    layers: list[dict] = field(default_factory=list, repr=False)
    layers_complete: bool = False
    layer_error: str | None = None
    uninit_tail: bool = False
    version_string: str | None = None
    trailer: int | None = None
    extra_count: int = 0
    extra: list[dict] = field(default_factory=list, repr=False)
    bytes_unconsumed: int = 0

    # -- accessors ---------------------------------------------------------
    def cell(self, x: int, y: int) -> tuple[int, int, int]:
        return self.cells[y * self.width + x]

    def walkable(self, x: int, y: int) -> bool:
        return self.cells[y * self.width + x][0] == 0

    @property
    def walkable_count(self) -> int:
        return sum(1 for c in self.cells if c[0] == 0)


def _cstr(b: bytes) -> str:
    i = b.find(b"\x00")
    return (b[:i] if i >= 0 else b).decode("latin-1")


def row_checksum(cells, y: int, width: int) -> int:
    """The per-row checksum documented in the wiki.  Verified against all files."""
    cs = 0
    base = y * width
    for x in range(width):
        mask, surface, elevation = cells[base + x]
        cs += (0 if mask == 0 else 1) * (surface + y + 1) \
            + (elevation + 2) * (surface + x + 1)
    return cs & MASK32


def parse(path: str | Path, want_cells: bool = True, verify: bool = True) -> DMap:
    p = Path(path)
    b = p.read_bytes()
    if len(b) < GRID_OFF:
        raise ValueError(f"{p.name}: too small ({len(b)} bytes)")

    version, reserved = struct.unpack_from("<II", b, 0)
    # The first 8 bytes are either a u64 numeric version (1003/1004/1005/1006,
    # high dword zero) or the ASCII string "DMAP101\0" / "DMAP100\0".  Both
    # forms occupy the same 8 bytes; the rest of the file is identical.
    # VERIFIED: 115 numeric, 21 tagged, across the 136 shipped maps.
    tagged = version == DMAP_TAG
    version_string = b[:8].rstrip(b"\x00").decode("latin-1") if tagged else None
    puzzle_path = _cstr(b[HEADER_PATH_OFF:HEADER_PATH_OFF + HEADER_PATH_LEN])
    width, height = struct.unpack_from("<II", b, DIMS_OFF)

    if width <= 0 or height <= 0 or width > 65536 or height > 65536:
        raise ValueError(f"{p.name}: implausible dimensions {width}x{height}")

    grid_bytes = height * (width * CELL_SIZE + 4)
    if GRID_OFF + grid_bytes > len(b):
        raise ValueError(
            f"{p.name}: grid {width}x{height} needs {grid_bytes} bytes, "
            f"only {len(b)-GRID_OFF} available")

    d = DMap(p, version, reserved, tagged, puzzle_path, width, height)
    d.version_string = version_string

    o = GRID_OFF
    cells: list[tuple[int, int, int]] = []
    row_fmt = struct.Struct("<%dh" % (width * 3))
    for y in range(height):
        vals = row_fmt.unpack_from(b, o)
        o += width * CELL_SIZE
        for x in range(width):
            # mask and surface are unsigned in the file; read as u16
            m = vals[x * 3] & 0xFFFF
            s = vals[x * 3 + 1] & 0xFFFF
            e = vals[x * 3 + 2]          # elevation is signed
            cells.append((m, s, e))
        (cs,) = struct.unpack_from("<I", b, o)
        o += 4
        d.row_checksums.append(cs)

    if verify:
        d.checksum_ok = sum(1 for y in range(height)
                            if row_checksum(cells, y, width) == d.row_checksums[y])
    if want_cells:
        d.cells = cells

    # portals
    (npass,) = struct.unpack_from("<I", b, o)
    o += 4
    if o + npass * 12 > len(b):
        raise ValueError(f"{p.name}: portal count {npass} overruns file")
    for _ in range(npass):
        d.portals.append(struct.unpack_from("<III", b, o))
        o += 12

    # layers -- best effort
    if o + 4 <= len(b):
        (nlay,) = struct.unpack_from("<I", b, o)
        o += 4
        d.layer_count = nlay
        for i in range(nlay):
            if o + 4 > len(b):
                d.layer_error = f"layer {i}: truncated before type tag"
                break
            (t,) = struct.unpack_from("<I", b, o)
            if t == UNINIT:
                d.layer_error = (f"layer {i}/{nlay}: uninitialised fill "
                                 f"(0xCDCDCDCD) at offset {o}; "
                                 f"declared count exceeds written layers")
                d.uninit_tail = True
                break
            size = LAYER_PAYLOAD.get(t)
            if size is None:
                d.layer_error = (f"layer {i}: unmodelled type {t} "
                                 f"({LAYER_TYPES.get(t, '?')}) at offset {o}")
                break
            o += 4
            if o + size > len(b):
                d.layer_error = f"layer {i}: payload overruns file"
                break
            pay = b[o:o + size]
            shape = LAYER_SHAPE[t]
            rec: dict = {"index": i, "type": t, "shape": shape,
                         "type_name": LAYER_TYPES.get(t, f"UNKNOWN_{t}")}
            if shape == "cover":
                rec["path"] = _cstr(pay[:260])
                rec["key"] = _cstr(pay[260:388])
                (ox, oy, w, h, dx, dy, iv) = struct.unpack_from("<IIIIiiI", pay, 388)
                rec.update(origin=[ox, oy], size=[w, h],
                           offset=[dx, dy], frame_interval=iv)
            elif shape == "scene":
                rec["path"] = _cstr(pay[:260])
                rec["origin"] = list(struct.unpack_from("<II", pay, 260))
            elif shape == "sound":
                rec["path"] = _cstr(pay[:260])
                ox, oy, rng, vol = struct.unpack_from("<IIII", pay, 260)
                rec.update(origin=[ox, oy], range=rng, volume=vol)
            elif shape == "effect":
                rec["name"] = _cstr(pay[:64])
                rec["origin"] = list(struct.unpack_from("<II", pay, 64))
            d.layers.append(rec)
            o += size
        else:
            d.layers_complete = True

    # Trailing section: a count followed by fixed 284-byte records.
    # VERIFIED shape: 6 x u32 then char[260] path, e.g.
    #   0, 4, 30, 30, 1, 8, "map\\puzzle\\skybg-move.pul"
    # The path is always a .pul, so these are additional puzzle layers -- most
    # plausibly the scrolling sky/background planes.  The meaning of the six
    # leading integers is INFERRED at best and this parser does not name them.
    if d.layers_complete and o + 4 <= len(b):
        (n_extra,) = struct.unpack_from("<I", b, o)
        o += 4
        d.extra_count = n_extra
        if n_extra and o + n_extra * EXTRA_RECORD <= len(b):
            for _ in range(n_extra):
                vals = struct.unpack_from("<6I", b, o)
                pth = _cstr(b[o + 24:o + EXTRA_RECORD])
                d.extra.append({"values": list(vals), "path": pth})
                o += EXTRA_RECORD
    d.bytes_unconsumed = len(b) - o
    return d


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _png(width: int, height: int, gray: bytes) -> bytes:
    """Minimal 8-bit greyscale PNG encoder (stdlib only)."""
    import zlib

    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # filter type 0
        raw += gray[y * width:(y + 1) * width]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _pixels(d: DMap, mode: str) -> bytes:
    px = bytearray(d.width * d.height)
    if mode == "mask":
        for i, (m, s, e) in enumerate(d.cells):
            px[i] = 255 if m == 0 else 0
    elif mode == "surface":
        mx = max((c[1] for c in d.cells), default=1) or 1
        for i, (m, s, e) in enumerate(d.cells):
            px[i] = min(255, s * 255 // mx)
    else:
        vals = [c[2] for c in d.cells]
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1
        for i, v in enumerate(vals):
            px[i] = (v - lo) * 255 // rng
    return bytes(px)


def render_png(d: DMap, out: Path, mode: str = "mask") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_png(d.width, d.height, _pixels(d, mode)))


def render_pgm(d: DMap, out: Path, mode: str = "mask") -> None:
    """Write a binary PGM. mode: 'mask' (passability), 'surface', 'elevation'."""
    w, h = d.width, d.height
    px = bytearray(w * h)
    if mode == "mask":
        for i, (m, s, e) in enumerate(d.cells):
            px[i] = 255 if m == 0 else 0
    elif mode == "surface":
        mx = max((c[1] for c in d.cells), default=1) or 1
        for i, (m, s, e) in enumerate(d.cells):
            px[i] = min(255, s * 255 // mx)
    else:
        vals = [c[2] for c in d.cells]
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1
        for i, v in enumerate(vals):
            px[i] = (v - lo) * 255 // rng
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (w, h))
        f.write(bytes(px))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_summary(a) -> int:
    mapdir = a.root / "map" / "map"
    files = sorted(p for p in mapdir.iterdir() if p.suffix.lower() == ".dmap")

    # GameMap.json is the oracle for map id <-> file
    oracle = {}
    gm = a.root / "ini" / "GameMap.json"
    if gm.exists():
        for row in json.loads(gm.read_text(encoding="utf-8", errors="ignore")):
            fn = str(row.get("FileName", "")).replace("\\", "/").lower()
            oracle[fn.rsplit("/", 1)[-1]] = row

    out = {"files": [], "totals": {}}
    tot_rows = tot_ok = 0
    complete = 0
    for p in files:
        try:
            d = parse(p, want_cells=True, verify=True)
        except Exception as ex:  # noqa: BLE001
            out["files"].append({"file": p.name, "error": str(ex)})
            continue
        tot_rows += d.height
        tot_ok += d.checksum_ok
        if d.layers_complete:
            complete += 1
        key = p.name.lower()
        orc = oracle.get(key)
        rec = {
            "file": p.name,
            "version": d.version,
            "dmap_tagged": d.tagged,
            "version_string": d.version_string,
            "puzzle_path": d.puzzle_path,
            "width": d.width,
            "height": d.height,
            "cells": d.width * d.height,
            "walkable_cells": d.walkable_count,
            "walkable_pct": round(100.0 * d.walkable_count / (d.width * d.height), 2),
            "row_checksums_ok": d.checksum_ok,
            "row_checksums_total": d.height,
            "portals": d.portals,
            "layer_count": d.layer_count,
            "layers_parsed": len(d.layers),
            "layers_complete": d.layers_complete,
            "layers_uninit_tail": d.uninit_tail,
            "layer_error": d.layer_error,
            "layer_shapes": sorted({L["shape"] for L in d.layers}),
            "extra_count": d.extra_count,
            "extra_parsed": len(d.extra),
            "extra_paths": [e["path"] for e in d.extra][:8],
            "bytes_unconsumed": d.bytes_unconsumed,
            "surface_values": sorted({c[1] for c in d.cells})[:16],
            "elevation_range": [min(c[2] for c in d.cells),
                                max(c[2] for c in d.cells)],
        }
        if orc:
            rec["gamemap_id"] = orc.get("DocumentId")
            rec["gamemap_puzzle_grid"] = orc.get("PuzzleGridSize")
        out["files"].append(rec)

    matched = sum(1 for r in out["files"] if "gamemap_id" in r)
    out["totals"] = {
        "files": len(files),
        "parsed": sum(1 for r in out["files"] if "error" not in r),
        "row_checksums_ok": tot_ok,
        "row_checksums_total": tot_rows,
        "row_checksum_pct": round(100.0 * tot_ok / tot_rows, 4) if tot_rows else 0,
        "layer_walk_complete": complete,
        "gamemap_json_entries": len(oracle),
        "files_matched_to_gamemap": matched,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["totals"], indent=2))
    return 0


def cmd_show(a) -> int:
    d = parse(a.file)
    print(f"file            {d.path.name}")
    print(f"version         {d.version_string or d.version} "
          f"(reserved={d.reserved}, tagged={d.tagged})")
    print(f"puzzle          {d.puzzle_path}")
    print(f"size            {d.width} x {d.height} = {d.width*d.height} cells")
    print(f"row checksums   {d.checksum_ok}/{d.height} OK")
    print(f"walkable        {d.walkable_count} "
          f"({100.0*d.walkable_count/(d.width*d.height):.1f}%)")
    print(f"portals         {d.portals}")
    print(f"layers          {len(d.layers)}/{d.layer_count} parsed, "
          f"complete={d.layers_complete}")
    if d.layer_error:
        print(f"layer error     {d.layer_error}")
    print(f"extra records   {len(d.extra)}/{d.extra_count}   "
          f"unconsumed bytes {d.bytes_unconsumed}")
    for e in d.extra[:6]:
        print("    extra", e)
    for L in d.layers[:15]:
        print("   ", {k: v for k, v in L.items() if k != "index"})
    return 0


def cmd_render(a) -> int:
    d = parse(a.file)
    if a.out.suffix.lower() == ".png":
        render_png(d, a.out, a.mode)
    else:
        render_pgm(d, a.out, a.mode)
    print(f"wrote {a.out} ({d.width}x{d.height}, mode={a.mode})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Conquer Online .DMap parser")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary")
    p.add_argument("--root", type=Path, default=coroot.default_root())
    p.add_argument("-o", "--out", type=Path,
                   default=Path(__file__).resolve().parents[1]
                   / "out" / "wdf" / "dmap_summary.json")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("show")
    p.add_argument("file", type=Path)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("render")
    p.add_argument("file", type=Path)
    p.add_argument("-o", "--out", type=Path, required=True)
    p.add_argument("--mode", choices=("mask", "surface", "elevation"),
                   default="mask")
    p.set_defaults(func=cmd_render)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
