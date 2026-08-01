#!/usr/bin/env python3
"""
tpd.py -- reader for NetDragon "DatPkg" archives (.tpi index + .tpd data).

The format ships with older / community Conquer Online clients (seen in
"Zephyr Conquer 1057": c3.tpi/c3.tpd and data.tpi/data.tpd) in place of the
WDF pair the Classic Conquer 2.0 client uses.  Unlike WDF, the index stores
**plaintext file paths**, and every payload is a raw zlib stream.

Layout (VERIFIED empirically against both Zephyr archive pairs -- 53,609 and
76,923 entries respectively; every offset/size cross-checked for contiguity):

  .tpi (index)
    offset 0x00  char[16]  magic  = "NetDragonDatPkg\\0"
    offset 0x10  u32       1000       (version? identical in all four files)
    offset 0x14  u32       0
    offset 0x18  u32       1
    offset 0x1C  u32       3
    offset 0x20  u32       indexOffset = 0x30 (where entries start)
    offset 0x24  u32       fileCount
    offset 0x28  u32       ~index byte length (off-by-one from measured; unused)
    offset 0x2C  u32       0
    offset 0x30  ...       fileCount variable-length entries, back to back

  index entry (little-endian):
    u8   nameLen
    char name[nameLen]     forward-slash path, e.g. "data/arrow.dds" -- no NUL
    u16  flag              always 1 in both shipped pairs (zlib-compressed)
    u32  uncompressedSize
    u32  compressedSize
    u32  compressedSize    (exact duplicate; "allocated size", always == above)
    u32  uncompressedSize  (exact duplicate)
    u32  offset            absolute byte offset of the payload in the .tpd

  .tpd (data)
    Same 0x20-byte magic+version header, then zlib streams (78 DA) laid out
    contiguously: offset[i] + compressedSize[i] == offset[i+1] for every i,
    first payload at 0x20.

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
    flag: int               # 1 = zlib (the only value ever observed)
    uncompressed: int
    compressed: int
    offset: int             # into the .tpd

    def as_dict(self) -> dict:
        return {"name": self.name, "flag": self.flag,
                "uncompressed": self.uncompressed,
                "compressed": self.compressed, "offset": self.offset}


class TpdFormatError(ValueError):
    pass


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
        flag, unc, comp, comp2, unc2, off = struct.unpack_from("<HIIIII", blob, pos)
        pos += 22
        if comp != comp2 or unc != unc2:
            raise TpdFormatError(
                f"{source} entry {i} ({name!r}): size fields disagree "
                f"({unc}/{unc2}, {comp}/{comp2}) -- layout assumption broken")
        entries.append(TpdEntry(name, flag, unc, comp, off))
    return entries


def read_index(tpi_path: Path | str) -> list[TpdEntry]:
    """Parse a .tpi index alone; the .tpd data file need not exist."""
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
        self.entries = _parse_index(self.tpi.read_bytes(), str(self.tpi))
        self._by_name = {e.name.lower(): e for e in self.entries}
        self._fh = open(self.tpd, "rb")
        head = self._fh.read(16)
        if head != TPD_MAGIC:
            raise TpdFormatError(f"{self.tpd}: not a NetDragonDatPkg data file")

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "TpdArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    # -- reads -------------------------------------------------------------
    def read_compressed(self, e: TpdEntry) -> bytes:
        self._fh.seek(e.offset)
        return self._fh.read(e.compressed)

    def read(self, e: TpdEntry) -> bytes:
        raw = self.read_compressed(e)
        if e.flag != 1:
            return raw
        out = zlib.decompress(raw)
        if len(out) != e.uncompressed:
            raise TpdFormatError(
                f"{e.name}: decompressed to {len(out)} bytes, "
                f"index says {e.uncompressed}")
        return out

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
