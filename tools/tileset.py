#!/usr/bin/env python3
r"""tileset.py -- a map's ground tiles, packaged for a client that composites.

    py -3 tools/tileset.py newplain            # manifest summary + arithmetic check
    py -3 tools/tileset.py Gulf --json out/tileset-gulf.json

The residency model this serves is `docs/map_memory.md`: a map's painted
ground is a slot grid over a small library of DXT-compressed tiles, and
the whole library fits in GPU memory (median 6 MB, worst 47 MB). So the
server hands the client two things, once per map:

    manifest    JSON. The slot grid (base64 u32 LE, 0xFFFF = empty), the
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
import dmap                                              # noqa: E402
import safepath                                          # noqa: E402

#: DDS: 4-byte magic + 124-byte header. Payload starts here.
DDS_DATA_OFFSET = 128
#: fourcc lives at magic(4) + 76 + 8 = offset 84 in the file.
_FOURCC_OFFSET = 84

#: DXT block bytes per 4x4 texel block.
_BLOCK_BYTES = {"DXT1": 8, "DXT3": 16, "DXT5": 16}

#: Bytes per slot in the packed grid, and the `struct` code that writes it.
#:
#: **u32 since 2026-08-29; it was u16, and the u16 was inherited from the
#: wrong thing.** A `.pul` really does store its tile table as u16 on disk
#: (`docs/mobility.md` 2), so while every slot value WAS a .pul tile index
#: the two widths agreed by construction. They stopped agreeing when
#: `puzzle.PuzzleLibrary` began minting synthetic ids at ``1 << 20`` for a
#: `.pux` (TqTerrain) tile's LAYER STACK -- a value that comes from no file
#: and never had a u16 obligation. `struct.pack` then refused the map
#: outright rather than truncating it, which is the one good thing about
#: how this failed.
#:
#: MEASURED over the install `coroot.find()` resolves, 2026-08-29: 4 of the
#: 136 resolvable maps carry slot values above 65535 -- `ninja01_new` (968
#: of 1,485 slots, max 1,049,296), `bp-flandlords-y_new` (278 of 960, max
#: 1,048,780), `2020love01_new` (232 of 1,320) and `magictower01_new` (42 of
#: 165). All four are `.pux` grounds and every one of those values is a
#: synthetic stack id.
#:
#: Why not a dense per-map index instead, which would have stayed u16: the
#: payload argument for it does not survive measurement (the whole corpus's
#: slot grids are 0.8 MB at u16 and 1.7 MB at u32, and the worst single map,
#: `Gulf` at 47,775 slots, goes from 0.13 MB to 0.25 MB of base64 against a
#: bundle that is a median 6 MB and a worst 47 MB), and the cost is real: a
#: dense index runs over the tiles that SHIP, so a painted slot whose art
#: does not resolve has to collapse to `EMPTY`. That erases the difference
#: between "nothing is painted here" and "something is painted here and its
#: art did not resolve" -- which is the entire content of these four maps,
#: because **not one of their 1,520 over-ceiling slots resolves to any art**
#: (`tile_path` returns "" for every synthetic id; `puzzle.py` fills
#: `frames` for terrain ROWS, and a stack replaces its rows). Widening keeps
#: them visible as painted-and-unresolved. A dense index would have made all
#: four maps build green while asserting they are unpainted there.
SLOT_BYTES = 4
_SLOT_CODE = "I"


def dxt_payload_size(fmt: str, w: int, h: int) -> int:
    """Bytes of one mip level. The formula every DXT reader uses."""
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK_BYTES[fmt]


def dds_mip_levels(data: bytes, fmt: str, w: int, h: int) -> int:
    """How many mip levels to SHIP from this DDS: its whole chain when the
    chain is COMPLETE and present, else 1.

    Complete means down to 1x1 -- floor(log2(max(w, h))) + 1 levels. WebGL 1
    treats a texture sampled with a mipmapped filter and a partial chain as
    INCOMPLETE and draws it BLACK, so a short chain is worse than none: the
    caller would have to know to fall back. Shipping only mip 0 for it keeps
    the one rule on the page -- `levels > 1` means mipmapped filtering is
    safe. `dwMipMapCount` is at file offset 28, honoured only when the
    DDSD_MIPMAPCOUNT flag (0x20000) is set.
    """
    try:
        flags, = struct.unpack_from("<I", data, 8)
        count, = struct.unpack_from("<I", data, 28)
    except struct.error:
        return 1
    if not (flags & 0x20000) or count <= 1:
        return 1
    full = max(w, h).bit_length()
    if count < full:
        return 1
    need = 0
    lw, lh = w, h
    for _ in range(full):
        need += dxt_payload_size(fmt, lw, lh)
        lw, lh = max(1, lw // 2), max(1, lh // 2)
    if len(data) < DDS_DATA_OFFSET + need:
        return 1
    return full


def _dds_dims_fmt(data: bytes):
    """(w, h, fourcc) from a DDS file, or None if it is not plain DXT."""
    if len(data) < DDS_DATA_OFFSET or data[:4] != b"DDS ":
        return None
    height, width = struct.unpack_from("<II", data, 12)
    fourcc = data[_FOURCC_OFFSET:_FOURCC_OFFSET + 4].decode("latin1").strip("\0")
    if fourcc not in _BLOCK_BYTES:
        return None
    return width, height, fourcc


def _refuse(refused: list, path: str, e: Exception) -> None:
    """A map texture path the asset layer REFUSED as a traversal.

    Map tile and sprite paths come from the game's own data (`.ani`
    `Frame<N>=`, scene records), and `AssetRoot.locate` confines every read,
    so a path that escapes the install raises `safepath.UnsafePath`. For a
    MAP the decision is SKIP-AND-COUNT, loudly: one hostile or malformed
    entry must not take down a whole map, and it must not be filed as an
    ordinary missing tile either. Every refusal is logged and listed in the
    manifest's `refusedPaths` (SD ruling, 2026-09-18). `/api/texbundle`
    decides the other way -- a figure request naming an escaping path is a
    400 -- because there the path came from the request, not the game."""
    refused.append(path)
    print(f"[tileset] REFUSED map texture path {path!r}: {e}", file=sys.stderr)


def _map_add(w, assets, path: str, refused: list):
    """`BundleWriter.add_dds` for map data: an UnsafePath is refused and
    counted rather than propagated (see `_refuse`)."""
    try:
        return w.add_dds(assets, path)
    except safepath.UnsafePath as e:
        _refuse(refused, path, e)
        return None


def build(pm) -> tuple[dict, bytes]:
    """(manifest, bundle) for a `puzzle.PuzzleMap`.

    The manifest's `entries` and the bundle are in the same order; `off` is
    a byte offset into the bundle. Only the first mip level ships (shipped
    tiles have none anyway -- `docs/map_memory.md` §1).
    """
    slots = pm.tiles
    # A `.pux` slot can be a STACK: `puzzle.py` gives each distinct stack a
    # synthetic index and `tile_path` returns "" for every one of them, so the
    # loop below used to drop them and the page painted nothing -- the
    # wrapper's checkerboard showed through instead (`mapedit.js:104`).
    # Measured on `2025tsf_new` (7878): 3,551 stacks, built from **64 distinct
    # terrain rows, 64 of which resolve to art**, against 26 tiles that shipped.
    # So we ship the ROWS and let the page composite, which is this module's
    # stated design ("shipped once, compressed, and composited by the page
    # instead of by this process") and the only affordable one: 3,551 stacks at
    # 256x256 RGBA is ~900 MB against a 90 MB budget.
    #
    # `stack_layers` is bottom-first and carries `[terrain row, MASK]` per
    # layer.
    #
    # **THE MASK IS NEW ON 2026-09-06 AND IT IS THE POINT.** Until then this
    # shipped the terrain row alone, on the reasoning that "layer ORDER alone
    # recovers the ground" -- which is true only if every layer covers its whole
    # tile, and none of them do. The two per-layer `i16` are the low 16 and high
    # 9 bits of one 25-bit per-vertex alpha mask
    # (`docs/pux_f1_f2_attribution_2026-09-06.md`), so a page handed rows alone
    # draws every layer full-tile and the topmost opaque one covers the rest of
    # the stack. `tilebake.js`'s `_maskQuads` is what consumes this.
    stack_layers: dict[int, list[list[int]]] = {}
    for sid, key in getattr(pm, "stacks", {}).items():
        lays = []
        for lay in key:
            if isinstance(lay, (tuple, list)):
                row = lay[0]
                mask = (dmap.pux_layer_mask(lay[1], lay[2]) if len(lay) >= 3
                        else dmap.PUX_MASK_FULL)
            else:
                row, mask = lay, dmap.PUX_MASK_FULL
            lays.append([row, mask])
        if lays:
            stack_layers[sid] = lays
    distinct = sorted(
        {t for t in slots if t != pz.EMPTY and t not in stack_layers}
        | {r for lays in stack_layers.values() for r, _m in lays}
    )
    entries: list[dict] = []
    blobs: list[bytes] = []
    off = 0
    refused: list = []
    for idx in distinct:
        path = pm.tile_path(idx)
        data = None
        if path and pm.assets is not None:
            try:
                data = pm.assets.read(path)
            except safepath.UnsafePath as e:
                _refuse(refused, path, e)
                data = None
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
        # Paths the asset layer refused as traversals (see `_refuse`);
        # `build_full` appends its sprite/plane refusals to this same list.
        "refusedPaths": refused,
        "grid": pm.grid,
        "pul": [pm.pul_w, pm.pul_h],
        "k": pm.k,
        "pixels": [pm.px_w, pm.px_h],
        "consistent": pm.consistent,
        # NO `rollSpeed` HERE. There was one until 2026-08-26, spelled
        # `list(pm.roll_speed) if getattr(pm, "roll_speed", None) else None`,
        # and it emitted `null` on every map ever built -- twice over:
        # `PuzzleMap` has no `roll_speed` attribute at all (so the `getattr`
        # default was the only branch that could run), and **0 of the 132
        # resolvable GROUND .pul files carry a non-zero roll** anyway. A
        # scrolling walkable surface is not a thing; roll is a backdrop-plane
        # property, and it rides `build_full`'s per-plane `roll` key below,
        # which is live.
        #
        # It is removed rather than fixed because a key that has never
        # carried a value and structurally cannot is worse than no key: this
        # one was read as evidence that the roll pipeline was already
        # complete, and the plane-side work was scoped off that reading.
        # Nothing consumes it (`grep -rn rollSpeed` over tools/ and
        # tools/webui/ finds only its own definition), and it is not among
        # the ground keys `tests/test_ground_animation.py` pins.
        "empty": pz.EMPTY,
        "slotBits": SLOT_BYTES * 8,
        "slots": base64.b64encode(
            struct.pack(f"<{len(slots)}{_SLOT_CODE}", *slots)).decode("ascii"),
        "entries": entries,
        # synthetic slot index -> its layers, BOTTOM FIRST, each
        # `[terrain row, 25-bit vertex mask]`. A slot whose index appears here
        # is a stack: the page draws that layer's masked quads, in this order,
        # instead of looking for a single texture. Empty on any map with no
        # `.pux` ground, which is most of them.
        #
        # WIDENED FROM A BARE ROW 2026-09-06. `tilebake._layersOf` still reads a
        # bare number as a full mask, so a manifest a page cached before the
        # change renders as it did rather than going blank -- but nothing in
        # this tree emits that shape any more.
        "stacks": {str(k): v for k, v in sorted(stack_layers.items())},
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

    def add_dds(self, assets, path: str, mips: bool = False) -> int | None:
        """Append one .dds payload; returns its entry index, dedup'd by path.
        Uncompressed DDS ships as raw BGRA under fmt "RGBA" (rare; counted).

        ``mips=True`` ships a DXT texture's WHOLE mip chain, level after level,
        when the file carries a complete one (see `dds_mip_levels`), and adds
        ``levels`` to the entry. The map-effects pass needs it: its meshes
        repeat and minify, and without mips a frond at fit zoom aliases into
        noise. Off by default, so every existing bundle is byte-identical."""
        key = path.lower()
        if key in self._by_path:
            return self._by_path[key]
        try:
            data = assets.read(path)
        except safepath.UnsafePath:
            # A TRAVERSAL REFUSAL IS NOT A MISSING TEXTURE. The broad except
            # below used to swallow it too, so `/api/texbundle`'s own
            # `except safepath.UnsafePath: -> 400` was unreachable and an
            # escape attempt was filed as an ordinary miss -- safe only
            # because nothing downstream did anything unsafe with a None
            # (convention, not construction; SD ruling 2026-09-18). Let it
            # through so the caller refuses it, visibly.
            #
            # Re-raised by NAME rather than by narrowing the except to a list
            # of "unreadable" errors: this function also serves the map
            # bundle (`build` below), where a corrupt archive entry must stay
            # a skipped tile, and an allow-list of read errors would turn
            # whatever it forgot into a 500 on a map.
            raise
        except Exception:                                 # noqa: BLE001
            return None
        info = _dds_dims_fmt(data)
        if info is not None:
            w, h, fmt = info
            size = dxt_payload_size(fmt, w, h)
            levels = dds_mip_levels(data, fmt, w, h) if mips else 1
            if levels > 1:
                lw, lh, size = w, h, 0
                for _ in range(levels):
                    size += dxt_payload_size(fmt, lw, lh)
                    lw, lh = max(1, lw // 2), max(1, lh // 2)
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
            levels = 1
        idx = len(self.entries)
        self.entries.append({"i": idx, "w": w, "h": h, "fmt": fmt,
                             "off": self.off, "size": size})
        if mips:
            self.entries[-1]["levels"] = levels
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
    tile INDEX (the v1 shape §build uses).

    Shares `_SLOT_CODE` with `build` deliberately: two encoders of the same
    grid are two places for the width to drift, and `tilebake.js` has exactly
    one decoder for both. No backdrop plane overflows u16 today -- measured
    2026-08-29 on BOTH parsers, 0 of 162 planes over 55 maps here and 0 of
    172 over 60 on `claude/dmap-planegroup-1005-2026-08-29@2ee16bfa`, which
    reveals ten more; the highest plane tile id anywhere is 14,362. A plane's
    art is always a `.pul` and the wide ids are a `.pux` ground fact, so this
    site is widened for the shared shape, not for a live defect.
    """
    return {
        "grid": pm.grid, "pul": [pm.pul_w, pm.pul_h],
        "pixels": [pm.px_w, pm.px_h],
        "slots": base64.b64encode(
            struct.pack(f"<{len(pm.tiles)}{_SLOT_CODE}",
                        *pm.tiles)).decode("ascii"),
    }


def build_full(pm, scenery=None, sprites=None, backdrops=None,
               tints=None, puzzle_tints=None) -> tuple[dict, bytes]:
    """(manifest v2, bundle): EVERYTHING a map draws, resident.

    On top of `build()`'s ground tiles: each backdrop plane's own slot grid
    and tiles, every distinct scene/cover sprite frame, and the placement
    records (painted-image pixels, computed here once per map with
    `tools/scene.py`'s own rules) -- so a client never fetches per window.
    `docs/map_memory.md`; the refresh-free requirement is the whole point.
    """
    manifest, ground = build(pm)
    refused = manifest["refusedPaths"]     # one list for the whole map
    w = _BundleWriter()
    w.blobs.append(ground)
    w.off = len(ground)

    # Backdrop planes: small .puls, tiles into the shared bundle.
    bds = []
    gtint, ptints = puzzle_tints if puzzle_tints else (None, {})
    for _pi, b in enumerate(backdrops or []):
        art = b.art
        rec = _grid_manifest(art)
        rec.update(index=b.index, parallax=list(b.parallax),
                   roll=list(b.roll), tiles={})
        # `SceneLayerN` tints backdrop plane N, keyed by LIST POSITION and not
        # by `b.index`: star01..star10 carry 7..16 planes and EVERY one of them
        # reports `index == 0`, so an index-keyed tint would paint sixteen
        # planes with one value. Position is what `background:N`,
        # `render_background(plane=)` and the layer panel already mean.
        _pt = ptints.get(_pi)
        if _pt:
            rec["tint"] = [int(v) for v in _pt]
        used = sorted({t for t in art.tiles if t != pz.EMPTY})
        # `tiles` stays FRAME 0 for every tile -- a client that knows nothing
        # about `anim` renders exactly the manifest it rendered before, byte
        # for byte. `anim` is added only for tiles that really have several
        # frames, so a plane with none carries no new key at all and the
        # whole manifest is unchanged. (docs/ground_animation.md 3)
        anim: dict[str, list[int]] = {}
        for t in used:
            path = art.tile_path(t)                    # frame 0, exactly as before
            idx = _map_add(w, pm.assets, path, refused) if path else None
            if idx is None:
                continue
            rec["tiles"][str(t)] = idx
            seq = art.tile_frames(t)
            if len(seq) < 2:
                continue
            ents = [_map_add(w, pm.assets, p, refused) for p in seq]
            # ALL-OR-NOTHING. A tile whose frame 3 will not decode falls back
            # to the still frame 0 rather than animating over a gap-shortened
            # cycle -- a cycle that silently drops frames is a WRONG
            # animation, and it would be indistinguishable from a right one
            # in every measurement below. Corpus-wide today no frame fails
            # (measured: 0 missing over beach-bg02's 45 and icecrypt-lev5/6's
            # 24x17), so this branch costs nothing and only bounds the damage
            # if a future install ships a broken frame.
            if any(e is None for e in ents) or ents[0] != idx:
                continue
            anim[str(t)] = ents
        if anim:
            rec["anim"] = anim
            #: Cycle length in FRAMES -- the property of the content. The
            #: .ani format carries FrameAmount and Frame<N> and NO interval
            #: (verified on 5017/5065/5165/5517/6090/6609/7878/Zephyr's raw
            #: .ani and on CCO's .json), so the rate is the client's to pick
            #: and only the LENGTH is authored. docs/ground_animation.md 4.
            rec["animFrames"] = max(len(v) for v in anim.values())
        bds.append(rec)

    # Scene + cover sprites: distinct frames, placements in painted-image px.
    #
    # `interactive` is the .DMap's SECOND record list (v1006), which this
    # builder did not carry until 2026-09-15: it iterated (scenes, covers) and
    # `late_covers` went nowhere, so the GPU path could not draw a layer the
    # PNG path drew. Pinned by `tests/test_render_paths_agree.py`.
    placements = {"scenes": [], "covers": [], "interactive": []}
    if scenery is not None and sprites is not None:
        for kind, items in (("scenes", scenery.scenes),
                            ("covers", scenery.covers),
                            ("interactive",
                             getattr(scenery, "late_covers", []))):
            # Covers go in .DMap list order (the client does not
            # depth-sort them -- `scene.cover_paint_order`); scenes and the
            # interactive list keep the isometric key, which is a scope limit
            # on RE's measurement and not a claim about them. The manifest's
            # own `depth` field is metadata: `tilebake.js` consumes this list
            # in the order it is written ("already depth-sorted").
            import scene as scenemod                 # noqa: PLC0415
            order = (scenemod.cover_paint_order(items) if kind == "covers"
                     else sorted(items, key=lambda q: q.depth()))
            for p in order:
                paths = sprites.frame_paths(p.ani, p.title)
                frames = []
                for rel in paths:
                    cands = [rel] if not rel.endswith(".msk") else \
                            [rel[:-4] + ".dds", rel]
                    idx = None
                    for cand in cands:
                        idx = _map_add(w, pm.assets, cand, refused)
                        if idx is not None:
                            break
                    if idx is not None:
                        frames.append(idx)
                if not frames:
                    continue
                sx, sy = p.sprite_origin(pm)
                rec = {
                    "frames": frames,
                    "interval": p.frame_interval if len(frames) > 1 else 0,
                    "x": int(sx), "y": int(sy), "depth": p.depth(),
                }
                # The `.OtherData` per-cover tint. Emitted ONLY when it
                # modulates, so the manifest does not grow by a neutral
                # (255,255,255,255) on every one of a 2,970-cover map -- and
                # so `len([c for c in covers if c.get("tint")])` is the count
                # of covers this map actually changes.
                #
                # THE GPU CANNOT APPLY WHAT IT WAS NOT SENT. The tint lived in
                # `mapedit.render_sprites` alone, which is the PNG path, so
                # every viewer on the GPU path saw untinted covers while the
                # feature was reported as shipped.
                t = (tints or {}).get(id(p))
                if t and tuple(t) != (255, 255, 255, 255):
                    rec["tint"] = [int(v) for v in t]
                placements[kind].append(rec)

    manifest.update({
        "version": 2,
        # `TerrainLayer0.Puzzle*` -- ONE tint for the whole main ground, a
        # different section and a different surface from the per-plane ones
        # above. Emitted only when it modulates.
        **({"groundTint": [int(v) for v in gtint]} if gtint else {}),
        "planes": bds,
        "sprites": w.entries,
        "scenes": placements["scenes"],
        "covers": placements["covers"],
        "interactive": placements["interactive"],
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
