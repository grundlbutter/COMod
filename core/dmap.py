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
                u16 mask        0 = walkable, non-zero = blocked.  NOT a
                                boolean -- v1006 carries 4 and 5, and the
                                row checksum uses the RAW value
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
        cs += mask * (surface + y + 1) + (elevation + 2) * (surface + x + 1)
    cs &= 0xFFFFFFFF

This reproduces 58,245 of 58,898 rows across the 136 shipped maps, with 133
files at 100%.  If the cell stride, field order, field widths, the signedness of
elevation, or the per-row (rather than per-file) checksum placement were wrong,
it would fail on row 0.  It reproduces **100%** of rows on every install
here once ``mask`` is read RAW rather than normalised to 0/1 -- see
`row_checksum`.  The old "653 failures confined to three version-1006 maps
that diverge partway down the grid, still unexplained" WAS that
normalisation: those maps carry mask 4, and they do not diverge, they
interleave.

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
    python core/dmap.py stale    [--root ROOT]   loose .DMap vs its .7z twin
    python core/dmap.py render   <file.DMap> -o map.png   (.pgm also supported)
"""
from __future__ import annotations

import argparse
import json
import lzma
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
    #: Not in the wiki's enum -- 1006's tag for what 4 is everywhere else.
    24: "COVER_1006",
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
               15: "sound", 19: "effect",
               # 1005's own record. Named rather than decoded: the walk needs
               # its size, and calling its two u32s something would be a guess.
               0: "unknown",
               # 1006's spelling of COVER. Same 420-byte shape, new tag.
               24: "cover"}

# THE COVER RECORD GREW BY FOUR BYTES AT VERSION 1005.
#
# 6609 is the first client to ship 1005 maps (15 of them; 6090 and 5517 ship
# none, 5517 has a single 1005 that is not a cover map).  Under the 416-byte
# reading every one of the 15 reads its FIRST cover and then stops on a zero
# u32 -- `layers 1/1146` on `150newmaze_new` -- leaving 4.5 KB to 486 KB
# unread.  At 420 the walk consumes every declared layer on 11 of the 15.
#
# **Closure is not the evidence.**  A stride that merely closes the walk is
# exactly the agreement that survives re-running and fails a content check, so
# the claim rests on what the records CONTAIN.  MEASURED over all 15:
#
#     COVER records recovered ......... 3,746
#     .ani path exists on disk ........ 3,746   (0 missing)
#     origin off the map .............. 0
#     frame interval .................. 100 ms on all 3,746
#     footprint sizes ................. 2x2, 3x3, 1x1, 5x5 ... all small
#
# and the path they name, `ani/mapscene-new.ani`, ships on **6609 and on no
# earlier client** -- a new sprite library arriving with the new version.
#
# **The four bytes are appended, not inserted**, which is why the first record
# read correctly at 416 and hid the change: every field keeps its offset and a
# new u32 sits after the seventh.  It is **0 on all 3,746**, so this corpus
# cannot distinguish a new always-zero field from four bytes of inter-record
# padding.  Both readings parse identically; neither is claimed here.
#
# 1006 is NOT included.  Only two 1006 maps exist and neither reaches a cover
# record -- `newplain_new` stops at layer 0 on an unmodelled type 24 and
# `forum01_new` fails its row checksums outright -- so there is no measurement
# to justify extending this to it, and guessing would be the whole failure this
# comment exists to record.
COVER_1005 = 420

# AND VERSION 1005 ADDS A LAYER OF ITS OWN, TAG 0, WITH A 20-BYTE PAYLOAD.
#
# Four of 6609's fifteen 1005 maps read their covers correctly at 420 and then
# stopped on `unmodelled type 0 (NONE)` -- `family06-01_new` at 83 of 149.
# The bytes at the stop are two `u32` zeros, three floats `1.0 1.0 1.0`, and
# then `19`, followed by a NUL-padded name.
#
# **`19` is not a length prefix, it is the next record's TAG** -- `EFFECT3D`,
# which has sat in `LAYER_PAYLOAD` at 72 bytes since the table was written,
# annotated "tag not observed in the shipped maps". It is observed now. Reading
# it as a length is the mistake this comment exists to stop the next person
# repeating: a plausible wrong structure is easy to build around a real string.
#
# So the record is `tag 0` + 20 bytes, and the three floats are a unit scale.
# What the two `u32`s are is NOT claimed.
#
# MEASURED 2026-08-11 with the size applied:
#
#     family06-01_new   83/149 -> 149/149      refine_new    285/349 -> 349/349
#     family06-02_new   75/173 -> 173/173      tsm_fb1_new    89/148 -> 148/148
#     6609 corpus                              296 of 297 maps read every
#                                              declared layer (the 1 is v1006)
#     tag-0 records recovered                  144
#
# **Closure is not the evidence** -- that standard is `COVER_1005`'s and it
# applies here too. The content check is the tag-19 records this unblocks:
# **3,111 of the 3,157 effect records recovered name a real row in
# `ini/3DEffect.dbc`** (5,313 rows, 5,290 distinct names). The 46 that do not
# are 28 distinct names and they are one family -- `mj_ei_*`, `mj_hhdc_*` --
# not scatter, which is what a wrong stride produces.
#
# **The first content check was run against the wrong file and said zero.**
# `ini/3DEffect.ini` has 2,599 sections and none of these names; 6609 is a
# compiled-table client and the plaintext `.ini` is the stale decoy
# `patch6090.QUIRKS` already names. An empty grep looked exactly like a fact.
LAYER_TAG0_1005 = 20

#: Layer payload sizes for version >= 1005.  Same table, wider cover, plus the
#: 1005-only tag 0.
LAYER_PAYLOAD_1005 = {**LAYER_PAYLOAD, 4: COVER_1005, 0: LAYER_TAG0_1005}


# AND VERSION 1006 RENUMBERS THE COVER TAG: 4 BECOMES 24.
#
# The shape does not change -- it is 1005's 420-byte cover, unchanged -- only
# the tag.  Under the shipped table every 1006 map that declares layers read
# **zero** of them, because the walk met tag 24 and refused.
#
# **This is a bigger population than "the two 1006 maps on 6609".**  MEASURED
# 2026-08-11 over every install on this machine: 6609 has 2, Zephyr 1, CCO 6,
# and **7878 has 162** -- 171 map rows in all.  1006 is overwhelmingly a 7878
# format, and 7878 is the TPD-era client.
#
#     v1006 maps declaring layers, reading every declared layer
#         as shipped   0 of 136
#         with 24=420  136 of 136        129,779 records, ALL of them tag 24
#
# **How the tag was pinned, because two readings fitted the first record.**
# The section opens `18 00 00 00` then `ani\mapscene-new.ani`, which reads
# equally well as a **length** (24 = 20 chars + NUL, padded to 4) or as a
# **tag** followed by the old `char[260]`.  Yesterday's v1005 lesson was that a
# number before a string was a tag and not a length; the remedy was never
# "assume it is not a length", it was *apply a size and see whether the walk
# closes*.  Here the discriminator is a map with many records: the gaps between
# occurrences of "ani" alternate **17** and **407**, and 17 is the distance
# between the two "ani" substrings *inside* the one string -- so there is one
# string per record and the stride is 17 + 407 = **424**, which is 4 + 420.
# A fixed record with a new tag.  Both readings survived the eye; only the
# stride separated them.
#
# **Content, because closure is not evidence** -- the standard `COVER_1005`
# set.  Of the recovered cover records, **125,232 of 125,232 on 7878 and 375
# of 375 on 6609 name an `.ani` that exists**, 17 of ~125,000 origins fall off
# their map, footprints are 1x1..6x6 and the frame interval is 100 ms on all
# but three.  On CCO the same check reports 0 of 4,172 and **that is the
# instrument, not the data**: `ani/MapScene.ani` -- the classic file that must
# exist -- also fails to resolve there, which is the negative control saying
# so.
#
# 1006 does **not** inherit 1005's tag 0 or its widened tag 4.  Nothing
# observed says it should, and carrying unmeasured entries across a version is
# the guess this comment exists to refuse.  A 1006 map holding a tag-4 record
# would read 416 and go loud, which is the correct failure.
COVER_TAG_1006 = 24

#: Layer payload sizes for version 1006: the base table plus the renumbered
#: cover.  Deliberately NOT built on `LAYER_PAYLOAD_1005`.
LAYER_PAYLOAD_1006 = {**LAYER_PAYLOAD, COVER_TAG_1006: COVER_1005}


def layer_payload(version: int) -> dict:
    """The payload table this map version uses.

    Split out rather than branched inline so a test can ask for one directly,
    and so the 1005 boundary has exactly one home.
    """
    if version == 1005:
        return LAYER_PAYLOAD_1005
    if version == 1006:
        return LAYER_PAYLOAD_1006
    return LAYER_PAYLOAD

# MSVC debug fill.  Some maps declare more layers than they actually wrote and
# leave the tail as uninitialised heap; treat it as end-of-data rather than
# pretending to decode it.
UNINIT = 0xCDCDCDCD

# Trailing background-plane section.  It is GROUPS of planes, not a flat run
# of records:
#
#     u32 n_groups
#     per group:  u32 v0, v1, v2, v3        <- shared: draw index, parallax
#                 u32 n_planes
#                 n_planes x { u32 flag(8); char[260] path }
#
# GROUP_HEADER + PLANE_RECORD == 284, and that identity is why the old flat
# "6 x u32 then char[260], stride 284" model read 171 of 181 maps correctly:
# a group holding exactly ONE plane is byte-for-byte indistinguishable from
# one 284-byte record.  The old model's `values[4]` was the plane count and
# its `values[5]` was the first plane's flag.  Ten maps (star01..star10)
# carry 7..16 planes in one group; on those the flat model read the first
# plane and left the rest unconsumed.  See docs/map_scenery.md section 5.
GROUP_HEADER = 20
PLANE_RECORD = 264
#: Retained: the size of a single-plane group, which is what the flat model
#: called a record.  Kept because it is the number every earlier measurement
#: is stated in, and `test_viewer.py::DMapTrailingSection` pins it against
#: the tempting-but-wrong "the stride is really 264" fix.
EXTRA_RECORD = GROUP_HEADER + PLANE_RECORD          # 284


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
    #: Planes the file *declares* -- the sum of every group's `n_planes`.
    #: Deliberately not `len(extra)`: the two disagreeing is the parser's
    #: own proof that it could not walk what the file promised, and
    #: `DMapTrailingSection` reads exactly that.
    extra_count: int = 0
    #: How many groups those planes came from.
    extra_groups: int = 0
    extra: list[dict] = field(default_factory=list, repr=False)
    bytes_unconsumed: int = 0
    #: What the loose file is worth against its `.7z` twin -- one of
    #: `ARCHIVE_STATES`.  The other audible channels report whether the file
    #: PARSED; this one reports whether it is the file the client reads, which
    #: no amount of successful parsing can establish.  See `archive_state`.
    archive: str = "none"

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
    """The per-row checksum documented in the wiki.  Verified against all files.

    **Two details, and only one of them is actually tested by our corpus.**

    ``elevation`` is SIGNED here, and that is load-bearing: MEASURED 2026-08-11
    over 17,742 rows on 5517, signed elevation matches the stored checksum on
    100% of rows and unsigned matches 90.5% -- and the 1,683 mismatches are
    **exactly** the 1,683 rows containing a below-zero cell.  ``co-stuff``'s
    ``web-floor-editor`` gets this wrong and writes the result back on save
    (``docs/external_refs_2026-08-11.md`` section 1).

    ``mask`` IS THE RAW VALUE, and the note that used to stand here is the
    only reason anyone ever looked.  It said:

        The ``0 if mask == 0 else 1`` normalisation is UNFALSIFIABLE on every
        map we ship ... a map with ``mask > 1`` would be the first evidence
        either way.  **Do not read the 100% as validating it.**

    **Version 1006 is that map.**  It carries masks of **4 and 5**, and the
    normalisation ate them.  MEASURED 2026-08-11 over 13,368 rows on 32 maps:

        rows that FAIL the normalised form and contain mask>1   6,640
        rows that FAIL it and do NOT                                0
        rows that PASS it and contain mask>1                        0
        rows that PASS it and do NOT                            6,728

    A partition with zero exceptions in either direction, not a correlation.
    Whole-install agreement moves 7878 **86.496% -> 100%**, CCO 97.531% ->
    100%, Zephyr 99.700% -> 100%, and every 0/1-only client stays at 100%,
    because the raw form is **strictly more general**: arithmetically identical
    wherever ``mask`` is 0 or 1, so nothing that passed before can break.

    **The shape of the failure is what found it**, and it refuted the
    description this module itself was carrying.  A v1006 grid does not
    "diverge partway down": it interleaves -- ``campfief_new`` is 108 rows
    good, 144 bad, 108 good -- and rows *after* a bad run verify again.  That
    rules out an insertion and a stride change outright, since both
    desynchronise everything downstream.  The cells were at the right offsets
    all along.

    **What a mask of 4 MEANS is not claimed.**  It is not a passability
    change: every consumer tests ``== 0``/``!= 0`` and always saw the raw
    value, so `walkable_count` and the walk grids are identical before and
    after this -- only the checksum moved.  Observed over 952,116 rows on
    2,087 maps: ``1`` 549.1M cells, ``0`` 156.5M, **``4`` 366,347, ``5``
    31,230, ``2`` 668** -- so it is not "4 and 5", which is what a sample of
    32 maps said and what this docstring nearly claimed.  The high values sit
    in the map interior rather than at an edge and carry elevation 0.  ``5 =
    4|1`` and ``2`` is a bit of its own, which reads like a bitfield with bit
    0 as "blocked"; that is a hypothesis, written down as one, and the corpus
    that would test it is the one that just produced ``2`` -- **Zephyr**,
    which holds all 668 of them and zero fives.

    **TWO INSTRUMENTS HAVE SINCE BEEN SPENT ON IT.  Read
    `docs/map_scenery.md` section 10 before spending a third**, because one
    of them is a negative: **connectivity does NOT discriminate this** -- a
    control promoting an equal number of arbitrary open-adjacent blocked
    cells beats the hypothesis on ``2020tsf_new``.  Painted-art coverage
    does discriminate and supports bit-0-as-blocked (mask 2 and mask 4 both
    at 100% on art against mask 1's 18-40%, on two corpora that do not
    share their high value) **without settling it**: on-art is necessary
    for walkable and not sufficient, and ``mask 5`` has zero testable
    samples because those maps are all ``.pux``.
    """
    cs = 0
    base = y * width
    for x in range(width):
        mask, surface, elevation = cells[base + x]
        cs += mask * (surface + y + 1) + (elevation + 2) * (surface + x + 1)
    return cs & MASK32


def parse(path: str | Path, want_cells: bool = True, verify: bool = True) -> DMap:
    p = Path(path)
    # `map_names()` yields NAMES ("desert"); this function takes a PATH.  The
    # by-name entry points are `open_map(root, name)` and `parse_map(root,
    # name)`, and passing a name here fails as a bare FileNotFoundError that
    # says nothing about which of the three to use.
    #
    # THIRD instance of one shape, which is why this exists: a caller took a
    # value from one entry point and handed it to another that could not
    # consume it -- `PuzzleLibrary.get()`'s None used unchecked, `open_map()`'s
    # (bytes|None, reason) unpacked and used, and this one, which produced 470
    # FileNotFoundError in a single loop.  The fix is deliberately NOT a
    # redesign of the public surface: one branch, no signature change, turning
    # a silent error into an instruction.
    if (not p.suffix and len(p.parts) == 1):
        raise ValueError(
            f"parse() takes a PATH and got the bare name {str(path)!r} -- "
            f"this is what `map_names()` yields. Use "
            f"`parse_map(root, {str(path)!r})` for a name, or pass a path "
            f"with a directory or a suffix.")
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
    row_stride = width * CELL_SIZE + 4
    # `want_cells=False` used to decode the whole grid and then throw it away.
    # The map picker asks 181 maps for headers only and was paying 62.8 M
    # tuple appends for cells nobody reads -- MEASURED 35.9 s of a 38.5 s
    # `MapEditor.rows()`.  The grid is fixed-stride, so skipping it is
    # arithmetic.  `verify` still needs the cells: the checksum is over them.
    if want_cells or verify:
        row_fmt = struct.Struct("<%dh" % (width * 3))
        for y in range(height):
            vals = row_fmt.unpack_from(b, o)
            o += width * CELL_SIZE
            # mask and surface are unsigned in the file; elevation is signed.
            # Three strided slices zipped produce the identical list of
            # 3-tuples with a fraction of the interpreter work.
            cells.extend(zip([v & 0xFFFF for v in vals[0::3]],
                             [v & 0xFFFF for v in vals[1::3]],
                             vals[2::3]))
            (cs,) = struct.unpack_from("<I", b, o)
            o += 4
            d.row_checksums.append(cs)
    else:
        csum = struct.Struct("<I")
        base = GRID_OFF + width * CELL_SIZE
        for y in range(height):
            d.row_checksums.append(csum.unpack_from(b, base + y * row_stride)[0])
        o = GRID_OFF + height * row_stride

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
    payload = layer_payload(version)
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
            size = payload.get(t)
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

    # Trailing section: groups of background planes.  See GROUP_HEADER above
    # for the layout and for why the older flat-284 reading looked right.
    #
    # VERIFIED 2026-08-10 over all 181 maps with a trailer on 5517: the group
    # walk ends EXACTLY at EOF on 181 of 181, 0 misfits, and every one of the
    # recovered paths is a `.pul`.  171 maps yield byte-identical output to
    # the flat model; the 10 star maps gain the planes they were dropping.
    # The clincher is that the flat model's `values[4]` reads 1 on all 66
    # single-plane records and 7,8,...,16 on exactly star01..star10 -- it was
    # the plane count all along.
    #
    # `values` keeps the flat model's 6-int shape, [v0,v1,v2,v3,n_planes,flag],
    # so `tools/puzzle.py` (which reads v[0] as draw index and v[2],v[3] as
    # parallax) is unchanged.  Planes in a group share the group's header,
    # which is what makes them a group.
    if d.layers_complete and o + 4 <= len(b):
        d.extra_groups, planes, declared, end = parse_trailer(b, o)
        o = end
        d.extra_count = declared
        d.extra = planes
    d.bytes_unconsumed = len(b) - o
    # Last, and unconditionally: everything above says whether the bytes we
    # read are well-formed, and none of it can say whether they are the bytes
    # the client reads.  `b` is already in hand, so this costs a small header
    # read and a CRC32 -- 0.01 s over all 181 maps of 5517.
    d.archive = archive_state(p, b)
    return d


def parse_trailer(b: bytes, o: int) -> tuple[int, list[dict], int, int]:
    """Walk the background-plane groups at `o`.

    Returns `(n_groups, planes, declared, end)`. `declared` is the sum of
    every group's `n_planes` — what the file *claims* — and `planes` is what
    could actually be walked. **On any shortfall the walk yields no planes
    and `end` returns to `o`**, so `declared != len(planes)` and every
    trailing byte stays counted in `bytes_unconsumed`. Reading what fits and
    calling it a parse is the failure mode this shape exists to refuse.

    Split out from `parse` so the refusal can be tested on a crafted body
    with no game install — a control that skips is not a control.
    """
    (n_groups,) = struct.unpack_from("<I", b, o)
    start = o = o + 4
    planes: list[dict] = []
    declared = 0
    ok = True
    for _ in range(n_groups):
        if o + GROUP_HEADER > len(b):
            ok = False
            break
        head = struct.unpack_from("<4I", b, o)
        (n_planes,) = struct.unpack_from("<I", b, o + 16)
        o += GROUP_HEADER
        declared += n_planes
        if o + n_planes * PLANE_RECORD > len(b):
            ok = False
            break
        for _ in range(n_planes):
            (flag,) = struct.unpack_from("<I", b, o)
            planes.append({"values": [*head, n_planes, flag],
                           "path": _cstr(b[o + 4:o + PLANE_RECORD])})
            o += PLANE_RECORD
    if not ok:
        # Leave the tail unread rather than half-read.  `declared` still
        # carries what the file claimed, so the disagreement with
        # `len(planes)` stays visible instead of being rounded away.
        planes, o = [], start
    return n_groups, planes, declared, o


# ---------------------------------------------------------------------------
# the loose .DMap and its archive twin
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS.  From 5517 on, a map ships as ``map/map/<stem>.7z`` holding
# one ``<stem>.DMap``, and the loose ``<stem>.DMap`` beside it is a leftover
# that the client does not read.  The archive is the shipped form -- it is what
# ``GameMap.dat`` names (``map/map/desert.7z``) -- and the two drift apart:
#
#     MEASURED 2026-08-10, loose file vs the .DMap inside its OWN archive
#         5517     181 loose, 192 archives,  11 archive-only,   3 stale
#         6090     184 loose, 247 archives,  63 archive-only,  74 stale
#         6609     184 loose, 297 archives, 113 archive-only,  77 stale
#
# and 6609's 184 loose files are byte-for-byte 6090's -- every one of them.
#
# **Nothing in this parser can notice.**  A stale map is a VALID map: it
# checksums 100%, its layers walk to completion, its plane groups reconcile and
# it consumes every byte.  So the reader accepts it and answers from it, and
# the answer is a previous client's.  On `island` the loose file reports 31
# portals where the archive has 1; on `newwoods` the loose file is a whole
# format version behind (1003 against 1004) and 2,231 layers short.
#
# We did not see this for the same reason it is dangerous: **5517 is 98.3%
# honest**, and 5517 is the install every measurement here was taken on.  The
# strategy of walking ``map/map/*.DMap`` did not break with a new client, it
# expired years ago -- 6090 was already 40% wrong before 6609 existed -- and
# our instrument was pointed at the one install where it still nearly worked.
#
# This section is the ALARM, not the cure: it makes a stale read say so.
# Reading maps out of the archives is the larger change and is not done here.
#
# The check costs a 32-byte read, a ~115-byte read and a CRC32 over bytes
# `parse` has already loaded -- MEASURED at 0.01 s for all 181 maps of 5517,
# against 0.16 s just to read them.  It is on by default because an alarm you
# have to ask for does not fire on the person who did not know to ask.

SZ_SIG = b"7z\xbc\xaf\x27\x1c"

#: What `DMap.archive` can say.  Four values, because "I could not check" must
#: never be spelled the same as "I checked and it is fine" -- that collapse is
#: how an unchecked thing gets read as a clean one.
ARCHIVE_STATES = ("current", "stale", "none", "unreadable")


class _Refused(Exception):
    """The 7z header is not the one shape we accept.  Say so, do not guess."""


class _Hdr:
    """A cursor over a 7z header, refusing rather than running off the end."""

    def __init__(self, b: bytes) -> None:
        self.b, self.o = b, 0

    def u8(self) -> int:
        if self.o >= len(self.b):
            raise _Refused("truncated")
        v = self.b[self.o]
        self.o += 1
        return v

    def u32(self) -> int:
        if self.o + 4 > len(self.b):
            raise _Refused("truncated u32")
        (v,) = struct.unpack_from("<I", self.b, self.o)
        self.o += 4
        return v

    def num(self) -> int:
        """7z's REAL_UINT64: a mask byte, then up to 8 little-endian bytes."""
        first = self.u8()
        mask, value = 0x80, 0
        for i in range(8):
            if not (first & mask):
                return value | ((first & (mask - 1)) << (8 * i))
            if self.o >= len(self.b):
                raise _Refused("truncated number")
            value |= self.b[self.o] << (8 * i)
            self.o += 1
            mask >>= 1
        return value

    def skip(self, n: int) -> None:
        if self.o + n > len(self.b):
            raise _Refused("truncated skip")
        self.o += n

    def bits(self, n: int) -> list:
        out, cur = [], 0
        for i in range(n):
            if i % 8 == 0:
                cur = self.u8()
            out.append(bool(cur & (0x80 >> (i % 8))))
        return out

    def crcs(self, n: int) -> list:
        defined = [True] * n if self.u8() else self.bits(n)
        return [self.u32() if d else None for d in defined]


def archive_entry(path: str | Path) -> tuple[int, int] | None:
    """``(uncompressed_size, crc32)`` of a one-file ``.7z``, or None.

    Reads only the 32-byte signature header and the small header it points at
    -- the content is never decompressed, and no external tool is involved.

    **Deliberately narrow.**  Every ``map/map/*.7z`` in the corpus is one file,
    one folder, one coder, with a plain (unencoded) header of 81..115 bytes --
    736 archives across 5517, 6090 and 6609, no exceptions.  Anything else
    returns None, which callers must treat as *unknown*, never as *matching*.
    An LZMA-encoded header (``kEncodedHeader``) is the obvious next shape and
    is refused rather than half-supported.

    VERIFIED against 7-Zip 's own ``l -slt`` output on all 736: identical size
    and CRC on 736, zero disagreements, zero refusals.  `test_viewer.py
    ::LooseMapVsArchive` re-runs that comparison where 7z.exe is installed and
    skips where it is not -- the point of the control being an *independent*
    implementation, not this one twice.
    """
    try:
        with open(path, "rb") as f:
            sig = f.read(32)
            if len(sig) < 32 or sig[:6] != SZ_SIG:
                return None
            nh_off, nh_size, _nh_crc = struct.unpack_from("<QQI", sig, 12)
            if not 0 < nh_size <= 1 << 16:
                return None                    # encoded/huge: not our shape
            f.seek(32 + nh_off)
            hdr = f.read(nh_size)
    except OSError:
        return None
    if len(hdr) != nh_size:
        return None

    try:
        f = _walk_header(hdr)
    except _Refused:
        return None
    return f["size"], f["crc"]


def archive_folder(path: str | Path) -> Optional[dict]:
    """Everything the header says about a one-file ``.7z``, or None.

    ``{"size", "crc", "pack_pos", "pack_size", "coder", "props"}`` -- what
    `read_archive` needs to decompress without an external tool.  Same strict
    walk and same refusal as `archive_entry`, which is a thin view over this.
    """
    try:
        with open(path, "rb") as f:
            sig = f.read(32)
            if len(sig) < 32 or sig[:6] != SZ_SIG:
                return None
            nh_off, nh_size, _crc = struct.unpack_from("<QQI", sig, 12)
            if not 0 < nh_size <= 1 << 16:
                return None
            f.seek(32 + nh_off)
            hdr = f.read(nh_size)
    except OSError:
        return None
    if len(hdr) != nh_size:
        return None
    try:
        return _walk_header(hdr)
    except _Refused:
        return None


def read_archive(path: str | Path) -> Optional[bytes]:
    r"""The single file inside a ``map/map/*.7z``, decompressed, or None.

    **Stdlib only -- no 7-Zip, no subprocess.**  That is the whole reason this
    exists rather than shelling out: `colibrary.materialize_maproot` already
    has to probe three paths for `7z.exe` and report `failed` when it finds
    none, and a map reader that needs an external binary is a map reader that
    is absent on someone's machine.

    MEASURED 2026-08-11 over 6609's **297** map archives: **293 LZMA1**
    (coder ``03 01 01``, five property bytes) and **4 LZMA2** (coder ``21``,
    one).  Both are `lzma.FORMAT_RAW` filters, so both decode here.  Every one
    of the 297 reproduces its stored CRC32, and the four LZMA2 bodies plus a
    sampled LZMA1 body are **byte-identical to 7-Zip's own ``e -so`` output**.

    Returns None rather than a guess for any shape outside that -- an encoded
    header, a second coder, a chain of them.  A wrong decode here is a wrong
    *map*, which is the failure the whole archive line of work exists to stop.
    """
    f = archive_folder(path)
    if f is None:
        return None
    filt = _lzma_filter(f["coder"], f["props"])
    if filt is None:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(32 + f["pack_pos"])
            packed = fh.read(f["pack_size"])
    except OSError:
        return None
    if len(packed) != f["pack_size"]:
        return None
    try:
        out = lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
                                    filters=[filt]).decompress(packed,
                                                               f["size"])
    except lzma.LZMAError:
        return None
    if len(out) != f["size"]:
        return None
    # The archive states its own CRC; a decode that does not reproduce it is
    # not a partial answer, it is a wrong one.
    if (zlib.crc32(out) & MASK32) != f["crc"]:
        return None
    return out


def _lzma_filter(coder: bytes, props: bytes) -> Optional[dict]:
    """A `lzma` raw filter for a 7z coder id, or None for one we do not read."""
    if coder == b"\x03\x01\x01" and len(props) == 5:          # LZMA1
        b = props[0]
        return {"id": lzma.FILTER_LZMA1,
                "dict_size": struct.unpack_from("<I", props, 1)[0],
                "lc": b % 9, "lp": (b // 9) % 5, "pb": (b // 9) // 5}
    if coder == b"\x21" and len(props) == 1:                  # LZMA2
        b = props[0]
        # 7z stores the dictionary size as one byte: bit 0 selects 2 or 3 as
        # the mantissa, the rest is the exponent.
        return {"id": lzma.FILTER_LZMA2,
                "dict_size": (2 | (b & 1)) << (b // 2 + 11)}
    return None


def _walk_header(hdr: bytes) -> dict:
    r = _Hdr(hdr)
    if r.u8() != 0x01:                         # kHeader, plain
        raise _Refused("not a plain kHeader")
    if r.u8() != 0x04:                         # kMainStreamsInfo
        raise _Refused("no kMainStreamsInfo")
    size = crc = pack_pos = pack_size = coder = props = None
    while True:
        pid = r.u8()
        if pid == 0x00:                        # kEnd
            break
        if pid == 0x06:                        # kPackInfo
            pack_pos = r.num()
            n = r.num()                        # numPackStreams
            if n != 1:
                raise _Refused("not one packed stream")
            while True:
                q = r.u8()
                if q == 0x00:
                    break
                if q == 0x09:                  # kSize
                    pack_size = [r.num() for _ in range(n)][0]
                elif q == 0x0A:                # kCRC (of the packed stream)
                    r.crcs(n)
                else:
                    raise _Refused(f"0x{q:02x} in kPackInfo")
        elif pid == 0x07:                      # kUnPackInfo
            if r.u8() != 0x0B:                 # kFolder
                raise _Refused("no kFolder")
            if r.num() != 1:
                raise _Refused("not one folder")
            if r.u8() != 0:
                raise _Refused("external folder definition")
            num_out, num_coders = 0, r.num()
            if num_coders != 1:
                raise _Refused("a coder chain, not one coder")
            for _ in range(num_coders):
                flags = r.u8()
                n_id = flags & 0x0F
                coder = hdr[r.o:r.o + n_id]
                r.skip(n_id)
                nout = 1
                if flags & 0x10:               # complex coder
                    r.num()
                    nout = r.num()
                num_out += nout
                if flags & 0x20:               # coder attributes
                    n_attr = r.num()
                    props = hdr[r.o:r.o + n_attr]
                    r.skip(n_attr)
                if flags & 0x80:
                    raise _Refused("alternative methods")
            for _ in range(num_coders - 1):    # bind pairs
                r.num()
                r.num()
            if r.u8() != 0x0C:                 # kCodersUnPackSize
                raise _Refused("no kCodersUnPackSize")
            size = [r.num() for _ in range(num_out)][-1]
            while True:
                q = r.u8()
                if q == 0x00:
                    break
                if q == 0x0A:                  # kCRC (of the unpacked folder)
                    crc = r.crcs(1)[0]
                else:
                    raise _Refused(f"0x{q:02x} in kUnPackInfo")
        elif pid == 0x08:                      # kSubStreamsInfo
            while True:
                q = r.u8()
                if q == 0x00:
                    break
                if q == 0x0D:                  # kNumUnPackStream
                    if r.num() != 1:
                        raise _Refused("more than one substream")
                elif q == 0x0A:
                    c = r.crcs(1)[0]
                    if c is not None:
                        crc = c
                else:
                    raise _Refused(f"0x{q:02x} in kSubStreamsInfo")
        else:
            raise _Refused(f"0x{pid:02x} in kMainStreamsInfo")
    if size is None or crc is None:
        raise _Refused("no size or no crc")
    return {"size": size, "crc": crc, "pack_pos": pack_pos,
            "pack_size": pack_size, "coder": coder, "props": props}


def archive_twin(dmap_path: str | Path) -> Optional[Path]:
    """The ``<stem>.7z`` shipped beside a ``<stem>.DMap``, or None.

    Sibling lookup, not a registry lookup: `GameMap.dat` names the archive
    (``map/map/desert.7z``) but a map can ship without a registry row at all --
    21 of 5517's 181 do -- and those are exactly the ones nobody is watching.
    """
    p = Path(dmap_path)
    twin = p.with_suffix(".7z")
    return twin if twin.is_file() else None


def archive_state(dmap_path: str | Path,
                  raw: Optional[bytes] = None) -> str:
    """One of `ARCHIVE_STATES` for a loose ``.DMap``.

    * ``"none"``       -- no ``.7z`` beside it.  5017/5065/5165 ship no
      archives at all, so there is nothing to disagree with.
    * ``"current"``    -- the loose file IS the file inside the archive.
    * ``"stale"``      -- it is not.  **The archive is the shipped form**; the
      loose file is a leftover and every answer taken from it is a previous
      client's.
    * ``"unreadable"`` -- there is an archive and this reader could not read
      its header.  Not ``"current"``: an unchecked thing is not a clean one.

    `raw` is the loose file's bytes when the caller already has them, which is
    the whole cost saving -- `parse` passes what it read.
    """
    twin = archive_twin(dmap_path)
    if twin is None:
        return "none"
    got = archive_entry(twin)
    if got is None:
        return "unreadable"
    size, crc = got
    try:
        b = Path(dmap_path).read_bytes() if raw is None else raw
    except OSError:
        return "unreadable"
    if len(b) != size:
        return "stale"
    return "current" if (zlib.crc32(b) & MASK32) == crc else "stale"


def stale_loose_maps(root: str | Path = None) -> dict:
    """Every map in ``map/map/`` sorted by what its loose file is worth.

    Returns counts plus the names, so a caller can print the alarm without
    walking the tree twice::

        {"dir": "...", "stale": ["island", ...], "current": 107,
         "none": 0, "unreadable": 0, "archive_only": ["drop01", ...]}

    ``archive_only`` is the other half of the same defect and the bigger
    number: **113 of 6609's maps ship as a ``.7z`` with no loose twin at all**,
    so a walk of ``*.DMap`` does not merely read them wrongly, it never sees
    them.  They are counted here and nowhere else.
    """
    r = Path(root) if root is not None else coroot.game_root()
    d = r / "map" / "map"
    out = {"dir": str(d), "stale": [], "current": 0, "none": 0,
           "unreadable": 0, "archive_only": []}
    if not d.is_dir():
        return out
    loose, arch = {}, {}
    for p in d.iterdir():
        s = p.suffix.lower()
        if s == ".dmap":
            loose[p.stem.lower()] = p
        elif s == ".7z":
            arch[p.stem.lower()] = p
    for stem in sorted(loose):
        st = archive_state(loose[stem])
        if st == "stale":
            out["stale"].append(loose[stem].stem)
        else:
            out[st] += 1
    out["archive_only"] = sorted(arch[s].stem for s in set(arch) - set(loose))
    return out


def map_names(root: str | Path = None) -> list:
    """Every map this install has, however it ships.

    A walk of ``map/map/*.DMap`` is **not** that list.  MEASURED 2026-08-11:

        install  loose  .7z  registry stems  map_names  a glob cannot see
        5017       142    0             119        151                 9
        5065       144    0             124        153                 9
        5165       154    0             138        163                 9
        5517       181  192             180        201                20
        6090       184  247             216        256                72
        6609       184  297             261        306               122

    Three sources, and each holds names the other two do not: 113 of 6609's
    maps ship only as a `.7z`, and **nine registry rows on every install name
    a map that ships in neither form** (`kunlun1`..`kunlun9`).  The last column
    is `map_names` minus the loose files, which is the number a `*.DMap` walk
    misses -- **not** the archive-only count, which understates it by the nine.
    """
    r = Path(root) if root is not None else coroot.game_root()
    d = r / "map" / "map"
    names: dict[str, str] = {}
    if d.is_dir():
        for p in d.iterdir():
            s = p.suffix.lower()
            if s in (".dmap", ".7z"):
                names.setdefault(p.stem.lower(), p.stem)
    _rel, rows = load_gamemap(r)
    for row in rows:
        stem = Path(str(row.get("FileName", "")).replace("\\", "/")).stem
        if stem:
            names.setdefault(stem.lower(), stem)
    return sorted(names.values(), key=str.lower)


def open_map(root: str | Path, name: str) -> tuple[Optional[bytes], str]:
    r"""One map's ``.DMap`` bytes, and a phrase naming what answered.

    **The registry decides.**  `GameMap.dat` names the file the client opens --
    ``map/map/desert.7z`` on 5517 and later, ``map/map/desert.DMap`` on
    5017/5065/5165 -- and it is a perfect predictor: 100% of rows on every
    install measured, private-server repack included.  See
    `docs/map_twin_precedence.md`, which also records that this is *why* the
    rule lives here and not in a parser plugin.

    Where a map has **no registry row at all** the archive wins, and that is a
    labelled default rather than a fact: nine maps on 6090 and 6609 ship both
    forms, disagree, and have nothing naming either.  Every registered map on
    those clients opens from its archive, so a map without a row is likelier to
    follow its neighbours -- but the second element of the return value says
    ``"archive (no registry row -- default)"`` so the guess is visible at the
    call site rather than inherited.

    Returns ``(None, reason)`` when nothing can be read.
    """
    r = Path(root)
    d = r / "map" / "map"
    stem = str(name).lower()
    arch, loose = d / f"{name}.7z", d / f"{name}.DMap"
    if not arch.is_file() or not loose.is_file():
        # Case-insensitive fallback: the registry's spelling and the file's
        # differ on several maps (`Dsigil` against `dsigil`).
        for p in d.iterdir() if d.is_dir() else ():
            if p.stem.lower() == stem:
                if p.suffix.lower() == ".7z":
                    arch = p
                elif p.suffix.lower() == ".dmap":
                    loose = p

    _rel, rows = load_gamemap(r)
    named = None
    for row in rows:
        fn = str(row.get("FileName", "")).replace("\\", "/")
        if Path(fn).stem.lower() == stem:
            named = fn.rsplit(".", 1)[-1].lower()
            break

    if named == "7z" and arch.is_file():
        raw = read_archive(arch)
        if raw is not None:
            return raw, f"{arch.name} (named by the registry)"
        return None, f"{arch.name} is named by the registry and did not read"
    if named == "dmap" and loose.is_file():
        return loose.read_bytes(), f"{loose.name} (named by the registry)"

    if named is None and arch.is_file():
        raw = read_archive(arch)
        if raw is not None:
            return raw, f"{arch.name} (no registry row -- default)"
    if loose.is_file():
        why = ("no registry row -- default" if named is None
               else f"registry names a .{named}, which is absent")
        return loose.read_bytes(), f"{loose.name} ({why})"
    if arch.is_file():
        raw = read_archive(arch)
        if raw is not None:
            return raw, f"{arch.name} (the only form that ships)"
    return None, f"no .DMap and no readable .7z for {name!r}"


def parse_map(root: str | Path, name: str, **kw) -> tuple[Optional[DMap], str]:
    """`open_map` then `parse`, without a temporary file.

    The parser takes a path, so an archive member is written to a scratch file
    only if a caller needs one; here the bytes are parsed in place.
    """
    raw, why = open_map(root, name)
    if raw is None:
        return None, why
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{name}.DMap"
        p.write_bytes(raw)
        try:
            d = parse(p, **kw)
        except Exception as e:                            # noqa: BLE001
            return None, f"{why}: did not parse: {e}"
    d.path = Path(root) / "map" / "map" / f"{name}.DMap"
    return d, why


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

#: A `.pux` puzzle's tile is 256 pixels, whatever the registry says.
#:
#: SOLVED, not assumed.  `ground_art.md`'s placement identity ties a puzzle's
#: tile dimensions to the map's own cell grid --
#: ``W == H == (pux_w * G)/64 + (pux_h * G)/32`` -- so G can be computed rather
#: than looked up.  Over **all 160 maps that name a `.pux`**, on 6609, 7878,
#: Zephyr and CCO, it comes out **256.0 every time**: one value, no spread.
#:
#: **`GameMap.dat` disagrees and the data wins.**  The registry says
#: `PuzzleGridSize` 128 for **113** of those maps and 256 for 3; forcing 256
#: satisfies the identity on **160 of 160**, forcing the registry's value fails
#: on every 128.  Each failure was out by exactly 2x, never anything else,
#: which is what a wrong constant looks like and a wrong parse does not.
PUX_GRID = 256

#: What a `.pux` header is, and where the decode stops.
PUX_MAGIC = b"TqTerrain\0"


def read_pux(path: str | Path) -> Optional[dict]:
    r"""A `map/PuzzleSave/*.pux` header: ``{"width", "height"}`` in tiles, or
    None.

    **The header only.**  `.pux` is `TqTerrain`, the format the *PuzzleSave*
    directory is named for, and it is not a compiled tile index -- it is far
    richer than the `.pul` it stands in for.  MEASURED over 14 files on 7878:
    **20.8 to 172.2 bytes per tile**, and a linear fit of size against tile
    count is out by **17,725 bytes** at worst.  A flat array cannot do that.
    So the tile payload is variable-length per entry and is **not decoded**;
    what is decoded is the geometry, which is what `puzzle.py` needs to place
    the ground and what it has been refusing for want of.

    Layout, VERIFIED on all 237 `.pux` in the corpus (6609 20, 7878 136,
    Zephyr 13, CCO 68 -- every one carrying the magic)::

        +0   char[10]  "TqTerrain\0"
        +10  2 bytes   varies; not named
        +12  u32       varies; not named
        +16  u32       1000        constant on all 237
        +20  u32       width       in tiles
        +24  u32       height      in tiles
        +28  u32       1000        constant on all 237

    The two constants are `1000` on every file and are **not** claimed to mean
    anything; they are recorded because a reader that expects them will notice
    when one is not there.  Width and height are pinned by the placement
    identity rather than by their plausibility -- see `PUX_GRID`.
    """
    try:
        b = Path(path).read_bytes()
    except OSError:
        return None
    if len(b) < 32 or b[:10] != PUX_MAGIC:
        return None
    a, w, h, c = struct.unpack_from("<4I", b, 16)
    if not (0 < w < 4096 and 0 < h < 4096):
        return None                       # not the shape; refuse rather than guess
    return {"width": w, "height": h, "const_a": a, "const_b": c,
            "bytes": len(b)}


def read_gamemap_dat(path: str | Path) -> list[dict] | None:
    """`ini/GameMap.dat` -> rows shaped like `ini/GameMap.json`.

    Every OFFICIAL client ships this and none of them ship the .json, which is
    the community client's form. It is NOT TQ-ciphered -- despite the .dat
    extension it is a plain little-endian table:

        u32  record count
        per record:
            u32  DocumentId
            u32  length of the path
            char[length]      e.g. "map/map/newbie.7z"   (no NUL)
            u32  PuzzleGridSize    256 or 128

    Verified on 5517: 262 declared, 262 parsed, consuming exactly 8131 of 8131
    bytes, and the 256/128 split matches what docs/cell_recovered.md read out
    of the engine. A short or trailing-garbage file returns None rather than a
    partial index, because a half-read map table is worse than none.

    It lives in COre because both readers of the map index need it and neither
    can import the other: `client/gamemap.py` is the game client's MapLibrary
    and `tools/puzzle.py` is the viewer's PuzzleLibrary. It was briefly in the
    former with the latter importing it, which quietly made the viewer's map
    stack depend on the client package -- and that dependency does not survive
    an extraction, so COMod shipped a MapEditor that could not open a map.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if len(raw) < 4:
        return None
    (count,) = struct.unpack_from("<I", raw, 0)
    off = 4
    rows: list[dict] = []
    try:
        for _ in range(count):
            (doc_id,) = struct.unpack_from("<I", raw, off); off += 4
            (n,) = struct.unpack_from("<I", raw, off); off += 4
            name = raw[off:off + n].decode("latin-1"); off += n
            (puzzle,) = struct.unpack_from("<I", raw, off); off += 4
            rows.append({"DocumentId": doc_id, "FileName": name,
                         "PuzzleGridSize": puzzle})
    except (struct.error, UnicodeDecodeError):
        return None
    if off != len(raw):
        return None
    return rows


def load_gamemap(root: str | Path) -> tuple[str, list[dict]]:
    r"""The install's map registry, whichever spelling it ships.

    `read_gamemap_dat` settled how to *parse* the binary form. This settles
    **which file to open**, which is the half that kept getting re-answered:
    the community client ships ``ini/GameMap.json`` and every official client
    ships the binary ``ini/GameMap.dat`` and no ``.json``.

    Returns ``(rel, rows)`` -- the logical path that actually answered, so a
    caller can say which file it read -- or ``("", [])`` when neither ships.
    Rows are the ``.json`` shape either way (`DocumentId`, `FileName`,
    `PuzzleGridSize`), which is what `read_gamemap_dat` already guarantees.

    MEASURED over ``ini/`` on all six declared installs (2026-08-09):

        cco         GameMap.json   156 rows
        5017        GameMap.dat    145      5065  153      5165  179
        5517        GameMap.dat    262      6090  303

    A clean split -- no install ships both.

    **`FileName` is not the same extension on both sides**, and a caller that
    matches on the whole name rather than the stem gets nothing: 5017/5065/5165
    name ``map/map/desert.DMap`` and 5517/6090 name ``map/map/desert.7z``. Every
    caller here takes ``Path(fn).stem``, which is ``desert`` in all four cases.

    **Rows are not unique per map, on any base.** CCO is 156 rows over 137
    stems; 6090 is 303 over 216. A map is reused under several DocumentIds --
    `newbie` is 1010 and 1035 -- so keying a dict by stem silently drops rows
    (that is `puzzle.by_id`'s bug, written up there).

    THIS IS THE THIRD READER TO NEED IT and the first to get it from COre.
    `mapindex` and `puzzle` each hand-rolled the same twelve lines, `mapedit`
    had none and reported **`documentId` and `gridSize` as None on 156/156 CCO
    rows' worth of official-client equivalents** -- 0 of 142-184 rows carried
    either -- and `cmd_summary` below had none either. Same class as C-2026-08-09-ani-json-spelling.
    """
    r = Path(root)
    js = r / "ini" / "GameMap.json"
    if js.is_file():
        try:
            rows = json.loads(js.read_text("utf-8", errors="replace"))
        except ValueError:
            rows = None
        if isinstance(rows, list) and rows:
            return "ini/GameMap.json", [x for x in rows if isinstance(x, dict)]
    dat = r / "ini" / "GameMap.dat"
    if dat.is_file():
        rows = read_gamemap_dat(dat)
        if rows:
            return "ini/GameMap.dat", rows
    return "", []


def read_ani(path: str | Path) -> dict[str, list[str]]:
    """`ani/<name>.ani` -> ``{"Puzzle72": ["data/.../canyon072.dds", ...]}``.

    The tile-frame index behind every ground puzzle. Returns **the same shape
    the community client's shipped `.json` has**, so a caller cannot tell
    which source answered -- which is the point: the two forms are the same
    fact written twice.

    Official clients ship this INI-flavoured text::

        [Puzzle0]
        FrameAmount=1
        Frame0=data/map/puzzle/island/lake/lake000.dds

    while the community client ships `ani/<name>.json`. That is the same
    split `read_gamemap_dat` exists for, and it has now been missed three
    times in three readers -- so like that function this lives in COre and is
    imported, never copied. `FrameAmount` is deliberately ignored: frames are
    collected by their own index and returned in sorted order, so a file
    whose declared count disagrees with its rows still yields every frame it
    carries rather than silently truncating.

    Unreadable or empty returns ``{}``: a missing tile index degrades to "no
    art found", which callers already handle, and there is no partial state
    worth inventing.
    """
    out: dict[str, list[str]] = {}
    section = ""
    frames: dict[int, str] = {}

    def flush() -> None:
        if section and frames:
            out[section] = [frames[k] for k in sorted(frames)]

    try:
        text = Path(path).read_text("latin-1", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            section, frames = line[1:-1].strip(), {}
            continue
        if "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if k.lower().startswith("frame") and k[5:].isdigit():
            frames[int(k[5:])] = v.replace("\\", "/").lstrip("/").lower()
    flush()
    return out


def load_ani(root: str | Path, rel: str) -> tuple[str, dict[str, list[str]]]:
    r"""Resolve a DMap's ``ani/<stem>.ani`` reference against one install.

    `read_ani` settled *how to parse* the two forms. This settles **which
    file to open**, which is the half that kept getting re-answered: a DMap
    names ``ani/MapScene.ani`` on every client, and the community client
    ships that index as ``ani/MapScene.json`` and no ``.ani`` at all.

    Returns ``(rel, table)`` -- the logical path that actually backed the
    answer, not the one the DMap asked for -- because a caller that collects
    or stages the index needs the file this install *has*. ``("", {})`` when
    neither spelling is present.

    MEASURED over ``ani/`` on all six declared installs (2026-08-09):

        cco         60 .json      0 .ani
        5017/5065    0           51
        5165         0           52
        5517         0           54
        6090         0           56

    A clean split, so preferring the JSON cannot shadow an official client's
    index -- there is no install where both spellings exist.

    The JSON's values are normalised the way `read_ani` normalises the INI's,
    so the two sources stay indistinguishable to a caller. CCO writes them
    mixed-case (``data/map/MapObj/desert/d_map0100.dds``); `coassets.AssetRoot`
    resolves either casing, and lowercasing is what makes a collected map's
    logical paths compare equal across the split.

    THIS IS THE FOURTH READER TO NEED IT and the first to get it from COre.
    `puzzle.PuzzleLibrary` and `mapindex` each grew their own copy, and
    `mapparts` -- which had none -- resolved **zero** map art on CCO: all 35
    of ``2009-7x``'s keys landed in `unresolved` and `collect_map` produced a
    three-file map with no scenery, exactly the failure that module's
    docstring is about. See ``docs/CORRECTIONS.md`` C-2026-08-09-ani-json-spelling.
    """
    r = Path(root)
    stem = Path(str(rel).replace("\\", "/")).stem
    js = r / "ani" / f"{stem}.json"
    if js.is_file():
        try:
            raw = json.loads(js.read_text("utf-8", errors="replace"))
        except ValueError:
            raw = None
        out: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                vals = [v] if isinstance(v, str) else v
                if not isinstance(vals, list):
                    continue
                out[k] = [str(x).replace("\\", "/").lstrip("/").lower()
                          for x in vals if isinstance(x, str)]
        # An empty or unparseable JSON falls through rather than answering
        # "no tiles". The two hand-rolled copies disagreed here -- `puzzle`
        # fell through, `mapindex` did not -- which is the drift a second
        # copy buys you even when both were written carefully. No install
        # ships both spellings, so this edge is unreachable today; it is
        # decided once rather than left to whichever copy a caller found.
        if out:
            return f"ani/{js.name}", out
    native = r / "ani" / f"{stem}.ani"
    if native.is_file():
        return f"ani/{native.name}", read_ani(native)
    return "", {}


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

    # The map registry is the oracle for map id <-> file. Through `load_ani`'s
    # sibling, because reading only the .json left this dict EMPTY on every
    # official client -- with `read_gamemap_dat` defined 250 lines up in this
    # same file. C-2026-08-09-ani-json-spelling.
    registry, _rows = load_gamemap(a.root)
    oracle = {}
    for row in _rows:
        fn = str(row.get("FileName", "")).replace("\\", "/").lower()
        # keyed by stem, not by the whole name: the .dat spells these
        # `desert.7z` where the .json spells them `desert.DMap`
        oracle[Path(fn).stem] = row
    if not oracle:
        print(f"dmap: no map registry read under {a.root / 'ini'} "
              f"(GameMap.json and GameMap.dat both absent or unparseable); "
              f"every gamemap_id below will be missing. This is a reader "
              f"result, not a fact about the maps.", file=sys.stderr)

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
        key = p.stem.lower()          # stem: the two registries disagree on
        orc = oracle.get(key)         # the extension, never on the stem
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
            "archive": d.archive,
            "surface_values": sorted({c[1] for c in d.cells})[:16],
            "elevation_range": [min(c[2] for c in d.cells),
                                max(c[2] for c in d.cells)],
        }
        if orc:
            rec["gamemap_id"] = orc.get("DocumentId")
            rec["gamemap_puzzle_grid"] = orc.get("PuzzleGridSize")
        out["files"].append(rec)

    matched = sum(1 for r in out["files"] if "gamemap_id" in r)
    if oracle and not matched:
        print(f"dmap: the map registry ({registry}) holds {len(oracle)} rows "
              f"and NONE of them joined to the {len(files)} .DMap files on "
              f"disk. That is a key mismatch, not a client whose maps are "
              f"unregistered.", file=sys.stderr)
    out["totals"] = {
        "files": len(files),
        "parsed": sum(1 for r in out["files"] if "error" not in r),
        "row_checksums_ok": tot_ok,
        "row_checksums_total": tot_rows,
        "row_checksum_pct": round(100.0 * tot_ok / tot_rows, 4) if tot_rows else 0,
        "layer_walk_complete": complete,
        # Kept under its old name so a saved report still compares; the
        # source is now named beside it, because the count alone cannot say
        # which of the two registry forms answered.
        "gamemap_json_entries": len(oracle),
        "gamemap_registry": registry,
        "files_matched_to_gamemap": matched,
    }
    twins = stale_loose_maps(a.root)
    out["archive"] = twins
    out["totals"] |= {
        "loose_stale": len(twins["stale"]),
        "loose_current": twins["current"],
        "loose_unreadable_archive": twins["unreadable"],
        "maps_archive_only": len(twins["archive_only"]),
    }
    if twins["stale"] or twins["archive_only"]:
        # On stderr and unconditional: this summary's own per-file numbers are
        # taken from the loose files, so where they are stale the report above
        # is a previous client's answer, correctly parsed.
        print(f"dmap: {len(twins['stale'])} of {len(files)} loose .DMap files "
              f"disagree with the .7z beside them -- the archive is the form "
              f"the client reads, so those rows are a previous client's map. "
              f"{len(twins['archive_only'])} more maps ship only as .7z and "
              f"are not in this report at all.", file=sys.stderr)
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
    print(f"archive twin    {d.archive}"
          + {"stale": "   <== THIS FILE IS NOT WHAT THE .7z BESIDE IT HOLDS."
                      "  Everything above parsed cleanly and is a previous"
                      " client's map.",
             "unreadable": "   <== a .7z is there and its header could not be"
                           " read, so nothing above is confirmed current",
             }.get(d.archive, ""))
    for e in d.extra[:6]:
        print("    extra", e)
    for L in d.layers[:15]:
        print("   ", {k: v for k, v in L.items() if k != "index"})
    return 0


def cmd_stale(a) -> int:
    """The corpus answer, with nothing parsed -- header reads and a CRC32."""
    t = stale_loose_maps(a.root)
    total = len(t["stale"]) + t["current"] + t["none"] + t["unreadable"]
    print(f"{t['dir']}")
    print(f"  loose .DMap files            {total}")
    print(f"    match their .7z            {t['current']}")
    print(f"    STALE (archive differs)    {len(t['stale'])}")
    print(f"    no .7z beside them         {t['none']}")
    print(f"    .7z header unreadable      {t['unreadable']}")
    print(f"  maps shipping ONLY as .7z    {len(t['archive_only'])}"
          f"   <- invisible to any *.DMap walk")
    if t["stale"]:
        print("\n  stale:", ", ".join(t["stale"][:40])
              + (" ..." if len(t["stale"]) > 40 else ""))
    if t["archive_only"]:
        print("\n  archive-only:", ", ".join(t["archive_only"][:40])
              + (" ..." if len(t["archive_only"]) > 40 else ""))
    # Exit 1 when the tree cannot be read at face value, so a caller that
    # never reads the text still learns of it.
    return 1 if (t["stale"] or t["archive_only"] or t["unreadable"]) else 0


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

    p = sub.add_parser("stale", help="loose .DMap files vs the .7z beside them")
    p.add_argument("--root", type=Path, default=coroot.default_root())
    p.set_defaults(func=cmd_stale)

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
