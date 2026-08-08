#!/usr/bin/env python3
r"""
dbc.py -- readers for the compiled ``ini/*.dbc`` tables of official clients.

Patch-6090-era clients ship the entity tables twice: the plaintext ``.ini``
files everyone knows, and a compiled ``.dbc`` twin.  **The `.ini` twins are
stale decoys** -- in the 6090 install they are stamped 2009 while the `.dbc`
files are stamped 2015, and the two disagree; the client reads the `.dbc`.
A parser that reaches for `3dobj.ini` because it exists is reading data six
years out of date.

Each compiled table opens with a four-byte magic naming its own layout:

    RSDB    id -> path rows          3DObj, 3DTexture, 3DEffectobj, 3dmotion
    SIMO    simple-object records    3DSimpleObj
    EFFE    (not parsed here)        3DEffect
    EMOI    (not parsed here)        EmotionIco

``RSDB`` is the same row-table format ``wdb.py`` documents inside
``ini/c3.wdb`` -- here it stands alone as a whole file (VERIFIED against the
6090 install: 3DObj 3,023 rows, 3DTexture 18,995, 3DEffectobj 9,539,
3dmotion 568,584; every string offset lands inside the file, zero bad rows):

    0x00  char[4]  "RSDB"
    0x04  u32      rowCount
    0x08  ...      rowCount x row
    ...            NUL-terminated latin-1 paths, offsets absolute from file
                   start, packed immediately after the row table

The row is ``{u32 id, u32 stringOffset}`` -- except ``3dmotion.dbc``, whose
rows are ``{u32 id, u32 extra, u32 stringOffset}``.  The stride is detected,
not assumed: for the right stride every row's offset lands inside the string
region, and for the wrong one the interleaved fields land outside it.  The
meaning of ``extra`` is unestablished (0 on role motions, 2 on NPC motions,
13634 on some late role rows); it is preserved, not interpreted.

``SIMO`` (VERIFIED: 388 records in 6090, walks to exactly EOF):

    0x00  char[4]  "SIMO"
    0x04  u32      recordCount
    then per record:
        u32 id, u32 partAmount, partAmount x { u32 partId, u32 textureId }

Decoded against the stale 2009 ``3DSimpleObj.ini`` as a Rosetta: record 1 is
Part0=9990010 Texture0=9990010, matching ``[ObjIDType1]`` exactly -- down to
the truncated ``Texture0=999005`` on id 5, which both carry, so that value is
authored rather than a typo.  Four records are multi-part (ids 50, 57, 612,
626, two parts each); everything else is PartAmount=1.

**Ids that overflow overflow the way the client overflows them.**  The 6090
``npc.ini`` names ten-digit motion ids (``StandByMotion=9990010100``) that do
not fit in a u32, and ``3dmotion.dbc`` keys them by their low 32 bits:
``9990010100 & 0xFFFFFFFF == 1400075508`` is the row, and it resolves to
``c3/npc/999001100.c3`` -- the *old nine-digit filename*.  Two facts in one:
lookups must wrap to u32 or miss everything, and the motion table is a
renaming shim -- TQ renumbered the ini layer without renaming archive files,
which is consistent with all five official clients sharing ``c3.wdf`` byte
for byte.  Measured over the whole table: 2,165 of 2,232 standby motions
resolve through the wrapped key.

Usage::

    from dbc import Rsdb, read_simo
    obj = Rsdb.parse(assets.read("ini/3DObj.dbc"))
    obj.get(9990010)                  -> 'c3/mesh/9990010.c3'
    motion = Rsdb.parse(assets.read("ini/3dmotion.dbc"))
    motion.get(9990010100 & 0xFFFFFFFF) -> 'c3/npc/999001100.c3'
    simo = read_simo(assets.read("ini/3DSimpleObj.dbc"))
    simo[211]                         -> [(9990010, 9990211)]

CLI::

    py -3 core/dbc.py show PATH [--limit N]
"""
from __future__ import annotations

import struct
from typing import Optional

#: Magic -> what it is. The unparsed members are listed so a caller probing a
#: directory can name what it found instead of guessing.
MAGICS = {
    b"RSDB": "id -> path row table",
    b"SIMO": "simple-object records (3DSimpleObj)",
    b"MESH": "appearance records (armor/armet/weapon/... .dbc)",
    b"EFFE": "3DEffect records (not parsed)",
    b"EMOI": "EmotionIco records (not parsed)",
}


def magic(data: bytes) -> Optional[bytes]:
    """The table's four-byte magic, if it is one we know of."""
    return data[:4] if data[:4] in MAGICS else None


class Rsdb:
    """A standalone ``RSDB`` file: ids to NUL-terminated path strings.

    ``paths`` maps id -> path exactly as stored (latin-1, unnormalised).
    ``extra`` is populated only for stride-12 tables (``3dmotion.dbc``) and
    carries that row's middle u32, meaning unknown.
    """

    def __init__(self, paths: dict, extra: dict, stride: int):
        self.paths = paths
        self.extra = extra
        self.stride = stride

    def get(self, rid: int) -> Optional[str]:
        return self.paths.get(rid)

    def __contains__(self, rid: int) -> bool:
        return rid in self.paths

    def __len__(self) -> int:
        return len(self.paths)

    @classmethod
    def parse(cls, data: bytes) -> "Rsdb":
        if data[:4] != b"RSDB":
            raise ValueError(f"not an RSDB table: magic {data[:4]!r}")
        (count,) = struct.unpack_from("<I", data, 4)
        stride = cls._detect_stride(data, count)
        paths: dict = {}
        extra: dict = {}
        n = stride // 4
        for i in range(count):
            fields = struct.unpack_from(f"<{n}I", data, 8 + i * stride)
            rid, off = fields[0], fields[-1]
            end = data.find(b"\0", off)
            paths[rid] = data[off:end].decode("latin-1")
            if n == 3:
                extra[rid] = fields[1]
        return cls(paths, extra, stride)

    @staticmethod
    def _detect_stride(data: bytes, count: int) -> int:
        """The stride under which every row's last field is a valid string
        offset. Interleaved fields of the wrong stride land outside the
        string region, which is what makes this decidable."""
        if count == 0:
            return 8
        for stride in (8, 12):
            table_end = 8 + count * stride
            if table_end > len(data):
                continue
            ok = all(
                table_end <= struct.unpack_from(
                    "<I", data, 8 + i * stride + stride - 4)[0] < len(data)
                for i in range(count))
            if ok:
                return stride
        raise ValueError("no stride in (8, 12) makes every offset valid")


def read_simo(data: bytes) -> dict:
    """``3DSimpleObj.dbc``: id -> [(partId, textureId), ...].

    Records are variable-length, so the file must be walked; walking must
    land exactly on EOF or the record shapes were misread, and that is an
    error rather than a truncation to tolerate silently.
    """
    if data[:4] != b"SIMO":
        raise ValueError(f"not a SIMO table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    pos, out = 8, {}
    for _ in range(count):
        rid, amount = struct.unpack_from("<II", data, pos)
        pos += 8
        parts = []
        for _ in range(amount):
            parts.append(struct.unpack_from("<II", data, pos))
            pos += 8
        out[rid] = parts
    # a record with a wild partAmount walks past EOF and raises struct.error
    # above; this catches the quieter failure of a short or padded file
    if pos != len(data):
        raise ValueError(f"SIMO walk ended at {pos}, file is {len(data)}")
    return out


def read_mesh(data: bytes) -> dict:
    """A ``MESH`` appearance table: id -> ordered part rows.

    The compiled twin of armor.ini / armet.ini / weapon.ini. Fixed 24-byte
    records -- every one of the three 6090 files is exactly
    ``8 + rowCount x 24`` bytes (armor 3,338 rows, armet 2,609, weapon
    11,185) -- laid out as six u32s mirroring the ini schema:

        u32 id, u32 partCount, u32 meshId, u32 textureId, u32 mixTexId,
        u32 packed  -- bytes [mixOpt, asb, adb, 0]

    VERIFIED against the stale inis as Rosetta: every one of armet's 1,168
    shared ids matches Mesh0/Texture0 exactly; weapon differs on 14 of
    4,835 (patch-era retextures) and carries 6,350 rows the 2008 ini never
    heard of. Ids are the ini section numbers stored as ints, so the CCO
    form ``002135000`` appears here as ``2135000`` -- ``str(id)`` matches
    the 6090 inis' own unpadded convention and `coassets.PartIni.get`
    bridges the zero-padded spelling. A multi-part appearance repeats its
    id on consecutive rows; the returned lists keep that order.
    """
    if data[:4] != b"MESH":
        raise ValueError(f"not a MESH table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    if 8 + count * 24 != len(data):
        raise ValueError(
            f"MESH row count {count} does not tile the file "
            f"({len(data)} bytes, expected {8 + count * 24})")
    out: dict = {}
    for i in range(count):
        rid, part, mesh, tex, mixtex, packed = struct.unpack_from(
            "<6I", data, 8 + i * 24)
        b = packed.to_bytes(4, "little")
        out.setdefault(rid, []).append({
            "part": part, "mesh": mesh, "texture": tex, "mixtex": mixtex,
            "mixopt": b[0], "asb": b[1], "adb": b[2]})
    return out


def _main(argv=None) -> int:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="dump a .dbc table's rows")
    s.add_argument("path", type=Path)
    s.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    data = a.path.read_bytes()
    m = magic(data)
    if m is None:
        print(f"unknown magic {data[:4]!r}")
        return 1
    print(f"{a.path.name}: {m.decode()} -- {MAGICS[m]}")
    if m == b"RSDB":
        t = Rsdb.parse(data)
        print(f"{len(t)} rows, stride {t.stride}")
        for rid in list(t.paths)[:a.limit]:
            e = f"  extra={t.extra[rid]}" if t.extra else ""
            print(f"  {rid:>12}  {t.paths[rid]}{e}")
    elif m == b"SIMO":
        t = read_simo(data)
        print(f"{len(t)} records")
        for rid in list(t)[:a.limit]:
            print(f"  {rid:>6}  {t[rid]}")
    else:
        print("recognised but not parsed here")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
