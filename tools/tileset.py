#!/usr/bin/env python3
r"""tileset.py -- a map's ground tiles, packaged for a client that composites.

    py -3 tools/tileset.py newplain            # manifest summary + arithmetic check
    py -3 tools/tileset.py Gulf --json out/tileset-gulf.json

The residency model this serves is `docs/map_memory.md`: a map's painted
ground is a u16 slot grid over a small library of DXT-compressed tiles, and
the whole library fits in GPU memory (median 6 MB, worst 47 MB). So the
server hands the client two things, once per map:

    manifest    JSON. The slot grid (base64 u16 LE, 0xFFFF = empty), the
                placement constants, and one entry per DISTINCT tile:
                its index, pixel size, DXT format, and [off, size) into...
    bundle      ...one binary blob of raw DXT payloads, DDS headers
                stripped, concatenated in manifest order.

The client uploads each slice with compressedTexImage2D and never sees a
DDS header. Stripping server-side is deliberate: entry sizes become pure
arithmetic (`DXT1 = w*h/2`, `DXT3/5 = w*h`), which the tests pin.

Tiles whose .dds cannot be read, whose format is not DXT, or whose payload
is short are LEFT OUT of the manifest; the client paints nothing there and
the window's VOID clear shows through -- same degradation as the server
renderer. Corpus-wide today that is zero tiles (`tools/pulmem.py`).

Read-only.  Nothing here writes to the game install.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for p in (str(_HERE), str(_REPO), str(_REPO / "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

import puzzle as pz                                       # noqa: E402

#: DDS: 4-byte magic + 124-byte header. Payload starts here.
DDS_DATA_OFFSET = 128
#: fourcc lives at magic(4) + 76 + 8 = offset 84 in the file.
_FOURCC_OFFSET = 84

#: DXT block bytes per 4x4 texel block.
_BLOCK_BYTES = {"DXT1": 8, "DXT3": 16, "DXT5": 16}


def dxt_payload_size(fmt: str, w: int, h: int) -> int:
    """Bytes of one mip level. The formula every DXT reader uses."""
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK_BYTES[fmt]


def _dds_dims_fmt(data: bytes):
    """(w, h, fourcc) from a DDS file, or None if it is not plain DXT."""
    if len(data) < DDS_DATA_OFFSET or data[:4] != b"DDS ":
        return None
    height, width = struct.unpack_from("<II", data, 12)
    fourcc = data[_FOURCC_OFFSET:_FOURCC_OFFSET + 4].decode("latin1").strip("\0")
    if fourcc not in _BLOCK_BYTES:
        return None
    return width, height, fourcc


def build(pm) -> tuple[dict, bytes]:
    """(manifest, bundle) for a `puzzle.PuzzleMap`.

    The manifest's `entries` and the bundle are in the same order; `off` is
    a byte offset into the bundle. Only the first mip level ships (shipped
    tiles have none anyway -- `docs/map_memory.md` §1).
    """
    slots = pm.tiles
    distinct = sorted({t for t in slots if t != pz.EMPTY})
    entries: list[dict] = []
    blobs: list[bytes] = []
    off = 0
    for idx in distinct:
        path = pm.tile_path(idx)
        data = None
        if path and pm.assets is not None:
            try:
                data = pm.assets.read(path)
            except Exception:                             # noqa: BLE001
                data = None
        info = _dds_dims_fmt(data) if data else None
        if info is None:
            continue
        w, h, fmt = info
        size = dxt_payload_size(fmt, w, h)
        payload = data[DDS_DATA_OFFSET:DDS_DATA_OFFSET + size]
        if len(payload) < size:
            continue
        entries.append({"i": idx, "w": w, "h": h, "fmt": fmt,
                        "off": off, "size": size})
        blobs.append(payload)
        off += size
    manifest = {
        "map": pm.name,
        "mapId": pm.map_id,
        "grid": pm.grid,
        "pul": [pm.pul_w, pm.pul_h],
        "k": pm.k,
        "pixels": [pm.px_w, pm.px_h],
        "consistent": pm.consistent,
        "rollSpeed": list(pm.roll_speed) if getattr(pm, "roll_speed", None) else None,
        "empty": pz.EMPTY,
        "slots": base64.b64encode(
            struct.pack(f"<{len(slots)}H", *slots)).decode("ascii"),
        "entries": entries,
        "bytes": off,
        "void": list(pz.VOID),
    }
    return manifest, b"".join(blobs)


class BundleWriter:
    """Accumulates payload slices and dedups by asset path.

    **This is the shared DXT-passthrough layer, and it is deliberately not
    map-specific.** It takes anything with a `.read(logical) -> bytes` -- a
    `puzzle` asset root, a `coassets.AssetRoot`, a viewer `Catalog` -- and a
    logical path, and it never decodes a texture the GPU could consume as-is.
    The map path built it first (`build` / `build_full` below); the
    character/monster path now uses the same class through
    `coviewer.api_texbundle`, because a second implementation of "strip the
    128-byte DDS header and concatenate" is a second place for the block
    arithmetic to drift.

    Was `_BundleWriter`. The underscore claimed "map-internal", which is no
    longer true; the old name stays bound below so nothing breaks.
    """

    def __init__(self) -> None:
        self.blobs: list[bytes] = []
        self.off = 0
        self._by_path: dict[str, int] = {}
        self.entries: list[dict] = []

    def add_dds(self, assets, path: str) -> int | None:
        """Append one .dds payload; returns its entry index, dedup'd by path.
        Uncompressed DDS ships as raw BGRA under fmt "RGBA" (rare; counted)."""
        key = path.lower()
        if key in self._by_path:
            return self._by_path[key]
        try:
            data = assets.read(path)
        except Exception:                                 # noqa: BLE001
            return None
        info = _dds_dims_fmt(data)
        if info is not None:
            w, h, fmt = info
            size = dxt_payload_size(fmt, w, h)
            payload = data[DDS_DATA_OFFSET:DDS_DATA_OFFSET + size]
            if len(payload) < size:
                return None
        else:
            # Not plain DXT: decode to RGBA once, ship uncompressed.
            try:
                import dds as ddsmod                      # noqa: PLC0415
                w, h, rgba = ddsmod.decode(data)
            except Exception:                             # noqa: BLE001
                return None
            fmt, size, payload = "RGBA", w * h * 4, bytes(rgba)
        idx = len(self.entries)
        self.entries.append({"i": idx, "w": w, "h": h, "fmt": fmt,
                             "off": self.off, "size": size})
        self.blobs.append(payload)
        self.off += size
        self._by_path[key] = idx
        return idx

    def bundle(self) -> bytes:
        return b"".join(self.blobs)


#: Kept so any caller written against the map-only name still works.
_BundleWriter = BundleWriter


def _grid_manifest(pm) -> dict:
    """The slot grid + distinct-tile list for one PuzzleMap, entries keyed by
    tile INDEX (the v1 shape §build uses)."""
    return {
        "grid": pm.grid, "pul": [pm.pul_w, pm.pul_h],
        "pixels": [pm.px_w, pm.px_h],
        "slots": base64.b64encode(
            struct.pack(f"<{len(pm.tiles)}H", *pm.tiles)).decode("ascii"),
    }


def build_full(pm, scenery=None, sprites=None, backdrops=None) -> tuple[dict, bytes]:
    """(manifest v2, bundle): EVERYTHING a map draws, resident.

    On top of `build()`'s ground tiles: each backdrop plane's own slot grid
    and tiles, every distinct scene/cover sprite frame, and the placement
    records (painted-image pixels, computed here once per map with
    `tools/scene.py`'s own rules) -- so a client never fetches per window.
    `docs/map_memory.md`; the refresh-free requirement is the whole point.
    """
    manifest, ground = build(pm)
    w = _BundleWriter()
    w.blobs.append(ground)
    w.off = len(ground)

    # Backdrop planes: small .puls, tiles into the shared bundle.
    bds = []
    for b in (backdrops or []):
        art = b.art
        rec = _grid_manifest(art)
        rec.update(index=b.index, parallax=list(b.parallax),
                   roll=list(b.roll), tiles={})
        used = sorted({t for t in art.tiles if t != pz.EMPTY})
        for t in used:
            path = art.tile_path(t)
            idx = w.add_dds(pm.assets, path) if path else None
            if idx is not None:
                rec["tiles"][str(t)] = idx
        bds.append(rec)

    # Scene + cover sprites: distinct frames, placements in painted-image px.
    placements = {"scenes": [], "covers": []}
    if scenery is not None and sprites is not None:
        for kind, items in (("scenes", scenery.scenes), ("covers", scenery.covers)):
            for p in sorted(items, key=lambda q: q.depth()):
                paths = sprites.frame_paths(p.ani, p.title)
                frames = []
                for rel in paths:
                    cands = [rel] if not rel.endswith(".msk") else \
                            [rel[:-4] + ".dds", rel]
                    idx = None
                    for cand in cands:
                        idx = w.add_dds(pm.assets, cand)
                        if idx is not None:
                            break
                    if idx is not None:
                        frames.append(idx)
                if not frames:
                    continue
                sx, sy = p.sprite_origin(pm)
                placements[kind].append({
                    "frames": frames,
                    "interval": p.frame_interval if len(frames) > 1 else 0,
                    "x": int(sx), "y": int(sy), "depth": p.depth(),
                })

    manifest.update({
        "version": 2,
        "planes": bds,
        "sprites": w.entries,
        "scenes": placements["scenes"],
        "covers": placements["covers"],
        "spriteBytes": w.off - len(ground),
        "bytes": w.off,
    })
    return manifest, b"".join(w.blobs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("map", help="map name (DMap stem)")
    ap.add_argument("--json", metavar="PATH", help="write the manifest as JSON")
    args = ap.parse_args(argv)

    lib = pz.PuzzleLibrary()
    pm = lib.get(args.map)
    if pm is None:
        print(f"no puzzle for {args.map!r}: {lib.reason}", file=sys.stderr)
        return 2
    manifest, bundle = build(pm)
    n = len(manifest["entries"])
    print(f"{pm.name}: {n} tiles, {manifest['bytes'] / 1e6:.1f} MB bundle, "
          f"grid {manifest['grid']}, pul {manifest['pul']}, "
          f"consistent={manifest['consistent']}")
    if manifest["entries"]:
        last = manifest["entries"][-1]
        assert last["off"] + last["size"] == len(bundle) == manifest["bytes"]
        print(f"offsets check out: last entry ends at {len(bundle)}")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=1), "utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
