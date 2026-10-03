#!/usr/bin/env python3
"""
tpd.py -- reader for NetDragon "DatPkg" archives (.tpi index + .tpd data).

The format ships with older / community Conquer Online clients (seen in
"Zephyr Conquer 1057": c3.tpi/c3.tpd and data.tpi/data.tpd) in place of the
WDF pair the Classic Conquer 2.0 client uses.  Unlike WDF, the index stores
**plaintext file paths**, and every payload is a raw zlib stream.

Layout (VERIFIED empirically against both Zephyr archive pairs -- 53,609 and
76,923 entries respectively -- and both 7878 pairs, 59,964 and 86,149; every
offset/size cross-checked for contiguity):

  .tpi (index)
    offset 0x00  char[16]  magic  = "NetDragonDatPkg\\0"
    offset 0x10  u32       1000       (version? identical in all four files)
    offset 0x14  u32       0
    offset 0x18  u32       1
    offset 0x1C  u32       3
    offset 0x20  u32       indexOffset = 0x30 (where entries start)
    offset 0x24  u32       fileCount
    offset 0x28  u32       ~index byte length -- APPROXIMATE, do not seek by it:
                           exact on 7878, but 9 and 1 bytes short on Zephyr's
                           two pairs.  The record walk is the reliable measure.
    offset 0x2C  u32       freeCount -- number of 8-byte free-list records that
                           follow the entries.  0 in every full archive; 3 in
                           7878's `c31.tpi`.
    offset 0x30  ...       fileCount variable-length entries, back to back
    then         ...       freeCount records of {u32 size, u32 offset}

  index entry (little-endian) -- THE TAIL LENGTH DEPENDS ON `flag`:
    u8   nameLen
    char name[nameLen]     forward-slash path, e.g. "data/arrow.dds" -- no NUL
    u16  flag              0 = empty file, 1 = one zlib stream, 2 = chunked

    flag 1 -- 22-byte tail, one zlib stream:
      u32  uncompressedSize
      u32  compressedSize
      u32  compressedSize     of the first chunk == the whole file, so equal
      u32  uncompressedSize   of the first chunk == the whole file, so equal
      u32  offset             absolute byte offset of the payload in the .tpd

    flag 2 -- the same 22-byte tail, then (chunkCount - 1) chunk descriptors:
      u32  compressedSize     } one per chunk after the first, in order
      u32  uncompressedSize   }
      u32  offset             }
      chunkCount = ceil(uncompressedSize / firstChunkUncompressedSize); the
      chunk size is 2 MiB.  Each chunk is its own zlib stream; concatenating
      the inflated chunks gives the file.

    flag 0 -- 10-byte tail, and this one is load-bearing:
      u32  uncompressedSize (0)
      u32  compressedSize   (0)
      An empty file has NO offset field, because there is no payload to point
      at.  The record is 12 bytes shorter than every other record.

  .tpd (data)
    Same 0x20-byte magic+version header, then zlib streams (78 DA) laid out
    contiguously: offset[i] + compressedSize[i] == offset[i+1] for every i,
    first payload at 0x20.  Chunks of one file are contiguous too.

WHY THIS DOCSTRING WAS WRONG BEFORE, AND THE SHAPE OF THE BUG
-------------------------------------------------------------
This file used to describe the 3rd and 4th u32 as "exact duplicate; always ==
above", and raised `layout assumption broken` when they were not.  That was a
true statement about the Zephyr archives it was verified against, written down
as a property of the *format* -- so the first client that used the fields for
what they are (a chunk table) read as a corrupt archive.  Cf. `wdf.py`'s
ascending-by-nameHash claim and `inidb`'s Win32-parity claim: same shape, both
harmless until a new client arrived.  See docs/CORRECTIONS.md
C-2026-08-10-parser-tpd-grammar.

The expensive half is `flag 0`.  It is rare -- 4 entries in 7878's c3.tpi, 1 in
its data.tpi -- but a walk that assumes the 22-byte tail over-reads an empty
record by 12 bytes and every subsequent entry is then read at the wrong offset.
The corruption therefore surfaces thousands of entries later as drifting
garbage, nowhere near the record that caused it, which is why naive attempts to
fix this read like a chunk-size problem.  `_parse_index` guards against that
class directly: it refuses an unknown flag at the entry that carries it, and
`read_index` checks that the walk consumed the index exactly.

Usage:
    python core/tpd.py index   <archive.tpi> [-o out.json]
    python core/tpd.py list    <archive.tpi> [--glob "data/emotionico/*"]
    python core/tpd.py stats   <archive.tpi>
    python core/tpd.py extract <archive.tpi> --name "data/arrow.dds" -o file.bin
    python core/tpd.py extract-all <archive.tpi> -d outdir

As a module:
    from tpd import TpdArchive
    with TpdArchive("c3.tpi") as a:      # finds c3.tpd next to it
        for e in a.entries: ...
        data = a.read(e)                  # decompressed bytes
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

TPD_MAGIC = b"NetDragonDatPkg\x00"
HEADER_SIZE = 0x30          # .tpi; the .tpd header is the first 0x20 bytes


@dataclass(frozen=True)
class TpdEntry:
    name: str               # forward-slash relative path, as stored
    flag: int               # 0 = empty, 1 = one zlib stream, 2 = chunked
    uncompressed: int       # whole file, summed over chunks
    compressed: int         # whole file, summed over chunks
    offset: int             # into the .tpd; first chunk's, 0 for an empty file
    # (compressed, uncompressed, offset) per chunk, in order.  One entry for
    # flag 1, several for flag 2, and empty for flag 0 -- an empty file has no
    # payload, so there is nothing to point at.
    chunks: tuple[tuple[int, int, int], ...] = ()

    @property
    def size(self) -> int:
        """The inflated size, under the name `WdfEntry` uses for it.

        `coassets.Located` and everything downstream read `.size` off an
        archive entry; spelling it here lets a TPD entry travel the same code
        path as a WDF one instead of needing a wrapper at the boundary."""
        return self.uncompressed

    def as_dict(self) -> dict:
        d = {"name": self.name, "flag": self.flag,
             "uncompressed": self.uncompressed,
             "compressed": self.compressed, "offset": self.offset}
        if len(self.chunks) > 1:      # keep single-chunk output as it was
            d["chunks"] = [list(c) for c in self.chunks]
        return d


class TpdFormatError(ValueError):
    pass


CHUNK_SIZE = 2097152        # 2 MiB; the unit flag-2 files are split into


def _parse_index(blob: bytes, source: str) -> list[TpdEntry]:
    if not blob.startswith(TPD_MAGIC):
        raise TpdFormatError(f"{source}: not a NetDragonDatPkg index")
    index_off, count = struct.unpack_from("<II", blob, 0x20)
    entries: list[TpdEntry] = []
    pos = index_off
    for i in range(count):
        name_len = blob[pos]
        pos += 1
        name = blob[pos:pos + name_len].decode("latin-1")
        pos += name_len
        (flag,) = struct.unpack_from("<H", blob, pos)
        pos += 2

        if flag == 0:
            # Empty file: a 10-byte tail with no offset field.  Reading the
            # 22-byte tail here would over-read by 12 and desynchronise every
            # entry after this one.
            unc, comp = struct.unpack_from("<II", blob, pos)
            pos += 8
            if unc or comp:
                raise TpdFormatError(
                    f"{source} entry {i} ({name!r}): flag 0 means an empty "
                    f"file but sizes are {unc}/{comp}")
            entries.append(TpdEntry(name, flag, 0, 0, 0, ()))
            continue

        if flag not in (1, 2):
            # Refuse here rather than guess a tail length -- a wrong guess
            # corrupts every subsequent entry and reports the damage far from
            # this record.
            raise TpdFormatError(
                f"{source} entry {i} ({name!r}): unknown flag {flag}; "
                f"tail length is unknown, refusing to guess")

        unc, comp, comp2, unc2, off = struct.unpack_from("<IIIII", blob, pos)
        pos += 20
        chunks = [(comp2, unc2, off)]
        if flag == 2:
            if not unc2:
                raise TpdFormatError(
                    f"{source} entry {i} ({name!r}): chunked but first chunk "
                    f"is 0 bytes; cannot derive the chunk count")
            n_chunks = -(-unc // unc2)
            for _ in range(n_chunks - 1):
                chunks.append(struct.unpack_from("<III", blob, pos))
                pos += 12

        if sum(c[0] for c in chunks) != comp or sum(c[1] for c in chunks) != unc:
            raise TpdFormatError(
                f"{source} entry {i} ({name!r}): chunk sizes sum to "
                f"{sum(c[0] for c in chunks)}/{sum(c[1] for c in chunks)}, "
                f"header says {comp}/{unc}")
        entries.append(TpdEntry(name, flag, unc, comp, off, tuple(chunks)))

    # Patch-overlay archives carry a free list after the entries: `free_count`
    # records of {u32 size, u32 offset} naming space in the .tpd that no entry
    # references any more, because a patch replaced a file in place and
    # orphaned its old payload.  VERIFIED on 7878's c31.tpi: the three records
    # are exactly the three gaps left in the .tpd by the 82 entries' coverage.
    (free_count,) = struct.unpack_from("<I", blob, 0x2C)
    free: list[tuple[int, int]] = []
    for _ in range(free_count):
        size, off = struct.unpack_from("<II", blob, pos)
        pos += 8
        free.append((size, off))

    if pos != len(blob):
        # A variable-length walk that ends anywhere but the end of the index
        # has mis-read a tail somewhere; the count alone would not catch it.
        raise TpdFormatError(
            f"{source}: consumed {pos} of {len(blob)} index bytes after "
            f"{count} entries and {free_count} free-list records -- a record "
            f"tail was mis-read")
    return entries, free


def read_index(tpi_path: Path | str) -> list[TpdEntry]:
    """Parse a .tpi index alone; the .tpd data file need not exist."""
    p = Path(tpi_path)
    return _parse_index(p.read_bytes(), str(p))[0]


def read_index_and_free(tpi_path: Path | str
                        ) -> tuple[list[TpdEntry], list[tuple[int, int]]]:
    """As `read_index`, plus the free list of (size, offset) in the .tpd."""
    p = Path(tpi_path)
    return _parse_index(p.read_bytes(), str(p))


class TpdArchive:
    """One .tpi/.tpd pair.  Open with either path; the sibling is inferred."""

    def __init__(self, path: Path | str):
        p = Path(path)
        if p.suffix.lower() == ".tpd":
            self.tpi, self.tpd = p.with_suffix(".tpi"), p
        else:
            self.tpi, self.tpd = p, p.with_suffix(".tpd")
        self.entries, self.free = _parse_index(self.tpi.read_bytes(),
                                               str(self.tpi))
        self._by_name = {e.name.lower(): e for e in self.entries}
        self._fh = open(self.tpd, "rb")
        # THE MAGIC CHECK RAISES WITH THE HANDLE OPEN, and a constructor that
        # raises leaves the caller nothing to `close()`.  Same defect, same
        # shape, as the one `WdfArchive.__init__` carries.
        try:
            head = self._fh.read(16)
            if head != TPD_MAGIC:
                raise TpdFormatError(
                    f"{self.tpd}: not a NetDragonDatPkg data file")
        except Exception:
            self._fh.close()
            raise

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "TpdArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    # -- reads -------------------------------------------------------------
    def read_compressed(self, e: TpdEntry) -> bytes:
        """The stored bytes, chunks concatenated in order."""
        out = []
        for c_comp, _c_unc, c_off in e.chunks:
            self._fh.seek(c_off)
            out.append(self._fh.read(c_comp))
        return b"".join(out)

    def read(self, e: TpdEntry) -> bytes:
        if e.flag == 0:                 # empty file, no payload
            return b""
        if e.flag != 1 and e.flag != 2:
            return self.read_compressed(e)
        return self.inflate(e, self.read_compressed(e))

    @staticmethod
    def inflate(e: TpdEntry, stored: bytes) -> bytes:
        """`read`, from stored bytes the caller already has.

        Split out so a cache keyed on the STORED bytes (`core/tpdcache.py`)
        can hash them and, on a miss, inflate the same bytes instead of
        seeking and reading the entry a second time. `stored` is exactly what
        `read_compressed(e)` returns: the chunks concatenated in order, so
        each chunk is sliced back out by its compressed size.
        """
        if e.flag == 0:
            return b""
        if e.flag != 1 and e.flag != 2:
            return stored
        out = []
        pos = 0
        for c_comp, c_unc, c_off in e.chunks:
            part = zlib.decompress(stored[pos:pos + c_comp])
            pos += c_comp
            if len(part) != c_unc:
                raise TpdFormatError(
                    f"{e.name}: chunk at {c_off} inflated to {len(part)} "
                    f"bytes, index says {c_unc}")
            out.append(part)
        data = b"".join(out)
        if len(data) != e.uncompressed:
            raise TpdFormatError(
                f"{e.name}: decompressed to {len(data)} bytes, "
                f"index says {e.uncompressed}")
        return data

    def read_by_name(self, name: str) -> bytes:
        e = self._by_name.get(name.replace("\\", "/").lower())
        if e is None:
            raise KeyError(name)
        return self.read(e)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[TpdEntry]:
        return iter(self.entries)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _open(path: str) -> TpdArchive:
    return TpdArchive(path)


def cmd_index(a: argparse.Namespace) -> int:
    arc = _open(a.archive)
    doc = [e.as_dict() for e in arc.entries]
    if a.output:
        Path(a.output).write_text(json.dumps(doc, indent=1), "utf-8")
        print(f"wrote {len(doc)} entries -> {a.output}")
    else:
        json.dump(doc, sys.stdout, indent=1)
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    arc = _open(a.archive)
    for e in arc.entries:
        if a.glob and not fnmatch.fnmatch(e.name.lower(), a.glob.lower()):
            continue
        print(f"{e.uncompressed:>12}  {e.name}")
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    arc = _open(a.archive)
    total_c = sum(e.compressed for e in arc.entries)
    total_u = sum(e.uncompressed for e in arc.entries)
    by_ext: dict[str, int] = {}
    for e in arc.entries:
        ext = Path(e.name).suffix.lower() or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
    print(f"{arc.tpi.name}: {len(arc)} entries")
    print(f"  compressed   {total_c:>14,} bytes")
    print(f"  uncompressed {total_u:>14,} bytes")
    for ext, n in sorted(by_ext.items(), key=lambda kv: -kv[1]):
        print(f"  {ext:<10} {n:>7}")
    return 0


def cmd_extract(a: argparse.Namespace) -> int:
    arc = _open(a.archive)
    data = arc.read_by_name(a.name)
    out = Path(a.output or Path(a.name).name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{a.name} -> {out}  ({len(data)} bytes)")
    return 0


def cmd_extract_all(a: argparse.Namespace) -> int:
    arc = _open(a.archive)
    base = Path(a.dir)
    n = 0
    for e in arc.entries:
        rel = Path(*[p for p in e.name.split("/") if p not in ("", ".", "..")])
        out = base / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(arc.read(e))
        n += 1
    print(f"extracted {n} files -> {base}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index");        p.add_argument("archive")
    p.add_argument("-o", "--output");   p.set_defaults(func=cmd_index)

    p = sub.add_parser("list");         p.add_argument("archive")
    p.add_argument("--glob");           p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats");        p.add_argument("archive")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("extract");      p.add_argument("archive")
    p.add_argument("--name", required=True)
    p.add_argument("-o", "--output");   p.set_defaults(func=cmd_extract)

    p = sub.add_parser("extract-all");  p.add_argument("archive")
    p.add_argument("-d", "--dir", required=True)
    p.set_defaults(func=cmd_extract_all)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
