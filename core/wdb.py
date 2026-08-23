#!/usr/bin/env python3
r"""
wdb.py -- reader for the client's resource database, ``ini/c3.wdb``.

This is the table the old client resolves an **asset id** through to get a
**file path**, and it is the linkage that directory conventions can only
approximate.

Layout, re-measured 2026-08-09 on every client that ships one -- 5165, 5517,
6090 and Zephyr -- with **100% of declared rows resolving on all four**
(1,552 / 1,559 / 1,559 / 30,841; zero malformed, zero out of range):

    0x00  char[4]  "BDMG"          (magic; 'GMDB' read as LE)
    0x04  u32      file size       (equals len(data) on all four)
    0x08  u32      ?               (a little under the file size)
    0x0c  u32      ?               25 on Zephyr, 22 on 5165/5517/6090.
                                   NOT the table count -- there are 2.
    -- then one or more tables, laid out back to back: --
    0x10  char[4]  "RSDB"
    0x14  u32      rowCount
    0x18  ...      rowCount x { u32 id, u32 stringOffset }
    ...            NUL-terminated latin-1 paths, packed back to back,
                   starting at the byte the row table ends on (zero gap)

**A string offset is relative to its own table's start, not to the file.**
That is the whole of the correction below, and it matters because reading it
as absolute does not fail -- it lands inside a neighbouring string and returns
a shorter path that still looks like a path.

> **CORRECTED, and the previous figure was never right.** This docstring used
> to claim *"VERIFIED against Zephyr's c3.wdb: 72,988 rows"*. Zephyr's header
> declares **8,139** in its first table and **30,841** across both. 72,988
> matches nothing in the file. `docs/handoff_community_update.md` said 30,841
> across two validated tables from the start and **was right all along**;
> where the two disagreed, the handoff was correct and the code comment was
> not. See `docs/CORRECTIONS.md`.

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

**A SUCCESSFUL LOOKUP IS NOT EVIDENCE THE ID IS REAL, AND THE RISK IS A
FUNCTION OF ID WIDTH.**  The table merges several id spaces -- effects,
meshes, hair, npc, monster, mount -- and the effect space is dense from 0,
so a small id lands on *something* almost every time.  MEASURED, 4,000
random ids per width, none of them real, counting any hit as a false
positive::

    width   random ids           Zephyr        6090
      1-2   0-99                 41% / 80%    41% / 80%
        3   100-999                 26.7%       26.7%
        4   1000-9999               20.9%       20.9%
        5   10000-99999             13.1%        1.6%
       6+   100000 and up         <= 0.2%     <= 0.1%

``db.path_for(211)`` returns ``c3/effect/heal…`` and looks like an answer;
that is how 37.3% of `npc.ini`'s 3-digit ``SimpleObjID``s once "resolved"
as effects (`docs/handoff_zephyr_tpi.md` §3.1).

So the practical rule, which is cheap: **ids of 6 digits or more may be
looked up directly; ids of 5 or fewer need the returned path's FAMILY
checked against the id space you asked about** (a weapon id must come back
under ``c3/weapon`` or ``c3/mesh``, not ``c3/effect``), and any population
claim needs a random-id control of the same width beside it.
`docs/CORRECTIONS.md` `C-2026-08-09-claude-elastic-elion-0da45c`.
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

    #: Longest path accepted from the blob. The real maximum measured across
    #: every shipped file is well under this; it exists to stop a wrong base
    #: scanning to the end of a 12 MB file looking for a NUL.
    MAX_PATH = 260

    def __init__(self, path: Path | str):
        self.path = Path(path)
        d = self.path.read_bytes()
        if not d.startswith(MAGIC):
            raise WdbError(f"{self.path}: not a BDMG database")
        if d[0x10:0x14] != TABLE:
            raise WdbError(f"{self.path}: no RSDB table at 0x10")
        # The file holds several RSDB tables laid out SEQUENTIALLY, each
        # {"RSDB", u32 rowCount, rowCount x (u32 id, u32 stringOffset)}
        # followed immediately by its own string blob. Two things about that
        # sentence were wrong here until 2026-08-09 and both produced a
        # plausible answer rather than an error:
        #
        #   * a string offset is relative to ITS OWN TABLE's start, not to the
        #     file. Read as absolute it lands a few bytes into a neighbouring
        #     string, so `c3/effect/zf2-e181/1.c3` came back as
        #     `t/zf2-e181/1.c3` -- still printable, still path-shaped.
        #   * tables must be walked sequentially (rows, then blob, then the
        #     next table). SEARCHING for "RSDB" matches the literal wherever it
        #     occurs inside string data: on Zephyr that inflated 30,841 real
        #     rows to 260,897, of which the extras were garbage.
        #
        # The blob starts at exactly the byte the row table ends on -- measured
        # as a zero-byte gap on every client that ships one -- which is what
        # makes the sequential walk exact rather than heuristic.
        self.rows: list[tuple[int, str]] = []
        self.sections: list[dict] = []
        pos = 0x10
        while pos + 8 <= len(d) and d[pos:pos + 4] == TABLE:
            (n,) = struct.unpack_from("<I", d, pos + 4)
            row_end = pos + 8 + n * 8
            if n <= 0 or row_end > len(d):
                break
            rows: list[tuple[int, str]] = []
            blob_end = row_end
            for i in range(n):
                ident, soff = struct.unpack_from("<II", d, pos + 8 + i * 8)
                at = pos + soff                     # TABLE-relative, not absolute
                if not (row_end <= at < len(d)):
                    rows = []
                    break
                end = d.find(b"\x00", at)
                if end < 0 or end - at > self.MAX_PATH:
                    rows = []
                    break
                raw = d[at:end]
                # Every byte of every path is printable in the shipped files.
                # This is the check the old `half the offsets are < len(d)`
                # test could not make: a wrong base still yields a string, so
                # only the CONTENT can refuse it.
                if not raw or not all(32 <= c < 127 for c in raw):
                    rows = []
                    break
                rows.append((ident, raw.decode("latin-1").replace("\\", "/")))
                blob_end = max(blob_end, end + 1)
            if not rows:
                break
            self.sections.append({"offset": pos, "rows": n, "end": blob_end})
            self.rows.extend(rows)
            pos = blob_end
        self.count = len(self.rows)

        self.by_id: dict[int, str] = {}
        self.by_path: dict[str, list[int]] = {}
        for ident, p in self.rows:
            self.by_id.setdefault(ident, p)
            self.by_path.setdefault(p.lower(), []).append(ident)
        #: Rows a table declared but whose path would not resolve. A table is
        #: accepted whole or not at all, so this is the shortfall against the
        #: row counts in the accepted headers -- 0 on every shipped file.
        self.malformed = sum(s["rows"] for s in self.sections) - len(self.rows)

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
