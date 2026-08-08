#!/usr/bin/env python3
r"""
wdb.py -- reader for the client's resource database, ``ini/c3.wdb``.

This is the table the old client resolves an **asset id** through to get a
**file path**, and it is the linkage that directory conventions can only
approximate.  Layout (VERIFIED against Zephyr's c3.wdb: 72,988 rows, every
string offset lands inside the blob and the row count matches the header):

    0x00  char[4]  "BDMG"          (magic; 'GMDB' read as LE)
    0x04  u32      ?               (0x00ba0a6b -- unchanged in c31.wdb)
    0x08  u32      ?               (0x00ba0237)
    0x0c  u32      0x19
    0x10  char[4]  "RSDB"          (start of the row table)
    0x14  u32      rowCount
    0x18  ...      rowCount x { u32 id, u32 stringOffset }
    ...            NUL-terminated latin-1 paths, packed back to back

The ids are the same numbers the appearance tables and the item database
use, so this maps ``350001`` to ``c3/weapon/350001.c3`` directly rather than
by probing directories.

Usage::

    from wdb import ResourceDb
    db = ResourceDb(root / "ini" / "c3.wdb")
    db.path_for(350001)          -> 'c3/weapon/350001.c3'
    db.ids_for('c3/mesh/50000.c3') -> [50000]

CLI::

    py -3 core/wdb.py <c3.wdb> --stats
    py -3 core/wdb.py <c3.wdb> --id 350001
    py -3 core/wdb.py <c3.wdb> --grep garments/ --limit 20
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import Iterator, Optional

MAGIC = b"BDMG"
TABLE = b"RSDB"


class WdbError(ValueError):
    pass


class ResourceDb:
    """``ini/c3.wdb`` -- asset id -> resource path."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        d = self.path.read_bytes()
        if not d.startswith(MAGIC):
            raise WdbError(f"{self.path}: not a BDMG database")
        if d[0x10:0x14] != TABLE:
            raise WdbError(f"{self.path}: no RSDB table at 0x10")
        # The file holds SEVERAL RSDB tables, not one: each is
        # {"RSDB", u32 rowCount, rowCount x (u32 id, u32 stringOffset)}.
        # Reading only the first covers 8,139 of 72,988 rows.
        self.rows: list[tuple[int, int]] = []
        self.sections: list[dict] = []
        pos = 0x10
        while True:
            at = d.find(TABLE, pos)
            if at < 0:
                break
            try:
                (n,) = struct.unpack_from("<I", d, at + 4)
            except struct.error:
                break
            end = at + 8 + n * 8
            if n <= 0 or end > len(d):
                pos = at + 4
                continue
            rows = []
            for i in range(n):
                ident, soff = struct.unpack_from("<II", d, at + 8 + i * 8)
                rows.append((ident, soff))
            # a table is real only if its string offsets land in the file
            if sum(1 for _, so in rows if so < len(d)) < max(1, n // 2):
                pos = at + 4
                continue
            self.sections.append({"offset": at, "rows": n})
            self.rows.extend(rows)
            pos = end
        self.count = len(self.rows)

        self.by_id: dict[int, str] = {}
        self.by_path: dict[str, list[int]] = {}
        bad = 0
        for ident, soff in self.rows:
            if soff >= len(d):
                bad += 1
                continue
            end = d.find(b"\x00", soff)
            if end < 0:
                bad += 1
                continue
            p = d[soff:end].decode("latin-1").replace("\\", "/")
            self.by_id.setdefault(ident, p)
            self.by_path.setdefault(p.lower(), []).append(ident)
        #: rows whose string offset did not resolve; 0 in the shipped files
        self.malformed = bad

    # -- lookups -----------------------------------------------------------
    def path_for(self, ident: int | str) -> Optional[str]:
        try:
            return self.by_id.get(int(ident))
        except (TypeError, ValueError):
            return None

    def ids_for(self, path: str) -> list[int]:
        return self.by_path.get(path.replace("\\", "/").lower(), [])

    def __len__(self) -> int:
        return len(self.by_id)

    def __iter__(self) -> Iterator[tuple[int, str]]:
        return iter(sorted(self.by_id.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("wdb")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--id")
    ap.add_argument("--grep")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)

    db = ResourceDb(a.wdb)
    if a.stats or not (a.id or a.grep):
        print(f"{db.path.name}: {len(db.sections)} RSDB tables, {db.count} "
              f"rows, {len(db.by_id)} distinct ids, "
              f"{len(db.by_path)} distinct paths, {db.malformed} malformed")
        ids = sorted(db.by_id)
        print(f"  id range: {ids[0]} .. {ids[-1]}")
        exts: dict[str, int] = {}
        for p in db.by_path:
            e = p.rsplit(".", 1)[-1]
            exts[e] = exts.get(e, 0) + 1
        print("  by extension:", dict(sorted(exts.items(),
                                             key=lambda kv: -kv[1])))
    if a.id:
        print(f"{a.id} -> {db.path_for(a.id)}")
    if a.grep:
        n = 0
        for ident, p in db:
            if a.grep.lower() in p.lower():
                print(f"  {ident:>10}  {p}")
                n += 1
                if n >= a.limit:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
