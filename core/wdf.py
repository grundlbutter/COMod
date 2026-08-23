#!/usr/bin/env python3
"""
wdf.py -- reader for TQ Digital WDF archives (Conquer Online "Classic Conquer 2.0").

Archive layout (VERIFIED against c3.wdf and data.wdf, see docs/assets.md):

    offset 0   u32 magic        = 0x57444650  ('PFDW' when read as LE bytes)
    offset 4   u32 fileCount
    offset 8   u32 indexOffset  -> byte offset of the index table
    offset 12  ... payload blob, entries back-to-back in OFFSET order (see below) ...
    indexOffset .. EOF : fileCount * 16 bytes of index entries

Index entry (16 bytes, little-endian) -- field ORDER verified empirically:

    u32 nameHash    TQ string hash of the lowercased, backslash-normalised path
    u32 offset      absolute byte offset of the payload in the archive
    u32 size        payload length in bytes
    u32 space       always 0 in both shipped archives (reserved / "allocated size")

Entries are sorted strictly ascending by nameHash **in the official archives**,
and the payloads tile the region [12, indexOffset) exactly -- no padding, no
overlap: sum(size) == indexOffset - 12, and sorting entries **by offset** gives
offset[i] + size[i] == offset[i+1] for every consecutive pair.  That tiling
holds in every archive measured so far, official and private alike.

**INDEX ORDER IS NOT LAYOUT ORDER.** This paragraph previously claimed
offset[i] + size[i] == offset[i+1] *in index order*, which is false for the
very archives it was written from -- worse than the ordering claim below,
which was at least true of the shipped ones.  MEASURED 2026-08-11 with
`validate()` plus an offset-sorted pass (`C-2026-08-10-quickfix-wdf-contiguity`):

    contiguous pairs           index order        offset order
    6090/c3.wdf                  1 / 10,273     10,273 / 10,273
    6090/data.wdf                2 / 14,738     14,738 / 14,738
    5517/c3.wdf                  1 / 10,273     10,273 / 10,273
    Zephyr garments*.wdf       all n-1 / n-1       all n-1 / n-1

The official index is hash-sorted while payloads sit in write order, so the
two claims this paragraph used to make -- hash-ascending AND index-order
contiguity -- are jointly satisfiable only by an archive *written* in hash
order.  Each half of the corpus falsifies one: the official archives falsify
index-order contiguity, Zephyr's falsify hash-ascending.  Neither is a format
property; the offset-order tiling is the only layout claim that has survived
every archive it was tested against.

**DO NOT BINARY-SEARCH THE INDEX.** The ordering is a property of the archives
TQ shipped, not of the format, and this docstring previously claimed it as
"(binary-searchable)" without qualification. MEASURED 2026-08-11:

    6090/c3.wdf     10,274 entries   strictly ascending
    6090/data.wdf   14,739           strictly ascending
    5517/c3.wdf     10,274           strictly ascending
    Zephyr garments.wdf   5,253      NOT ascending
    Zephyr garments1.wdf  2,341      NOT ascending
    Zephyr garments2.wdf  1,943      NOT ascending
    Zephyr garments3.wdf  2,493      NOT ascending
    Zephyr garments4.wdf  2,021      NOT ascending

All five of a private server's garment archives violate it; their hashes spread
across all sixteen top-nibble buckets unordered. **Nothing binary-searches these
today, which is why the false claim has never bitten** -- it is the claim that
was the defect, not any behaviour. Surfaced while disproving the 173 garment
"names" (`C-2026-08-10-quickfix-hash-checkable`) and correctly kept out of that
retraction as a separate finding.

Usage:
    python core/wdf.py index   <archive.wdf> [-o out.json]
    python core/wdf.py list    <archive.wdf> [--magic DDS]
    python core/wdf.py stats   <archive.wdf>
    python core/wdf.py extract <archive.wdf> --hash 0xdeadbeef -o file.bin
    python core/wdf.py extract <archive.wdf> --name "c3/foo.dds" -o file.bin
    python core/wdf.py extract-type <archive.wdf> --magic DDS -d outdir [--limit 50]
    python core/wdf.py extract-all  <archive.wdf> -d outdir      # deliberately explicit

As a module:
    from wdf import WdfArchive
    with WdfArchive("c3.wdf") as a:
        for e in a.entries: ...
        data = a.read(e)
        data = a.read_by_name("c3/monster/foo.dds")
"""
from __future__ import annotations

import argparse
import json
import mmap
import struct
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

WDF_MAGIC = 0x57444650
HEADER_SIZE = 12
ENTRY_SIZE = 16

# ---------------------------------------------------------------------------
# payload type detection
# ---------------------------------------------------------------------------

# (magic bytes, label, file extension). Order matters: first match wins.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"DDS ", "DDS", ".dds"),
    (b"MAXF", "MAXF", ".c3"),
    (b"\x89PNG\r\n\x1a\n", "PNG", ".png"),
    (b"\xff\xd8\xff", "JPEG", ".jpg"),
    (b"GIF87a", "GIF", ".gif"),
    (b"GIF89a", "GIF", ".gif"),
    (b"RIFF", "RIFF", ".wav"),
    (b"OggS", "OGG", ".ogg"),
    (b"ID3", "MP3", ".mp3"),
    (b"BM", "BMP", ".bmp"),
    (b"\x00\x00\x01\x00", "ICO", ".ico"),
    (b"\x00\x00\x02\x00", "CUR", ".cur"),
    (b"Fold", "FOLD", ".bin"),
    (b"PK\x03\x04", "ZIP", ".zip"),
    (b"\x1f\x8b", "GZIP", ".gz"),
]


def detect_magic(head: bytes) -> tuple[str, str]:
    """Return (label, extension) for a payload given its first bytes."""
    for sig, label, ext in _SIGNATURES:
        if head.startswith(sig):
            # BMP is only 2 bytes of magic; sanity-check the reserved fields
            if label == "BMP":
                if len(head) >= 14:
                    r1, r2 = struct.unpack_from("<HH", head, 6)
                    if r1 or r2:
                        continue
            return label, ext
    if head[:4] == b"\x00\x00\x00\x00":
        return "ZEROS", ".bin"
    return "UNKNOWN", ".bin"


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WdfEntry:
    hash: int
    offset: int
    size: int
    space: int
    index: int = -1

    @property
    def hash_hex(self) -> str:
        return f"{self.hash:08x}"


class WdfError(Exception):
    pass


class WdfArchive:
    """Random-access reader for a WDF archive. Memory-maps the file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = open(self.path, "rb")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            self._fh.close()
            raise
        self.file_size = self.path.stat().st_size

        if self.file_size < HEADER_SIZE:
            raise WdfError(f"{self.path}: too small to be a WDF")
        magic, count, index_offset = struct.unpack_from("<III", self._mm, 0)
        if magic != WDF_MAGIC:
            raise WdfError(f"{self.path}: bad magic {magic:#010x}, expected {WDF_MAGIC:#010x}")
        self.magic = magic
        self.file_count = count
        self.index_offset = index_offset

        expected_tail = count * ENTRY_SIZE
        if index_offset + expected_tail > self.file_size:
            raise WdfError(
                f"{self.path}: index at {index_offset} + {expected_tail} exceeds file size {self.file_size}"
            )

        self.entries: list[WdfEntry] = []
        raw = self._mm[index_offset : index_offset + expected_tail]
        for i in range(count):
            h, off, size, space = struct.unpack_from("<IIII", raw, i * ENTRY_SIZE)
            self.entries.append(WdfEntry(h, off, size, space, i))
        self._by_hash = {e.hash: e for e in self.entries}

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "WdfArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._mm.close()
        finally:
            self._fh.close()

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[WdfEntry]:
        return iter(self.entries)

    # -- reading -----------------------------------------------------------
    def read(self, entry: WdfEntry) -> bytes:
        return self._mm[entry.offset : entry.offset + entry.size]

    def peek(self, entry: WdfEntry, n: int = 32) -> bytes:
        return self._mm[entry.offset : entry.offset + min(n, entry.size)]

    def get(self, name_hash: int) -> Optional[WdfEntry]:
        return self._by_hash.get(name_hash & 0xFFFFFFFF)

    def read_by_hash(self, name_hash: int) -> bytes:
        e = self.get(name_hash)
        if e is None:
            raise KeyError(f"hash {name_hash:#010x} not in {self.path.name}")
        return self.read(e)

    def read_by_name(self, name: str) -> bytes:
        # tqhash exports `tq_hash` (normalise + hash, matching the client at
        # TqPackageWdf.dll RVA 0x3BA0). The old `wdf_hash` name never existed,
        # so every read_by_name call raised ImportError.
        from tqhash import tq_hash  # local import; optional dependency

        return self.read_by_hash(tq_hash(name))

    # -- validation --------------------------------------------------------
    def validate(self) -> dict:
        """Structural self-check. Returns a dict of findings."""
        n = len(self.entries)
        bad_offset = [e.index for e in self.entries if e.offset < HEADER_SIZE
                      or e.offset + e.size > self.index_offset]
        bad_size = [e.index for e in self.entries if e.size <= 0]
        contiguous = sum(
            1 for i in range(n - 1)
            if self.entries[i].offset + self.entries[i].size == self.entries[i + 1].offset
        )
        sorted_hashes = all(
            self.entries[i].hash < self.entries[i + 1].hash for i in range(n - 1)
        )
        total = sum(e.size for e in self.entries)
        return {
            "file": self.path.name,
            "file_size": self.file_size,
            "file_count": n,
            "header_count": self.file_count,
            "index_offset": self.index_offset,
            "index_bytes": self.file_size - self.index_offset,
            "index_bytes_expected": n * ENTRY_SIZE,
            "entries_with_offset_out_of_range": len(bad_offset),
            "entries_with_bad_size": len(bad_size),
            "contiguous_pairs": contiguous,
            "contiguous_pairs_possible": n - 1,
            "hashes_strictly_ascending": sorted_hashes,
            "unique_hashes": len(self._by_hash),
            "payload_bytes_sum": total,
            "payload_region_bytes": self.index_offset - HEADER_SIZE,
            "payload_accounts_for_region": total == self.index_offset - HEADER_SIZE,
            "space_field_values": sorted({e.space for e in self.entries})[:8],
        }

    # -- index dump --------------------------------------------------------
    def index_records(self, names: dict[int, str] | None = None) -> list[dict]:
        names = names or {}
        out = []
        for e in self.entries:
            label, ext = detect_magic(self.peek(e, 32))
            rec = {
                "hash": e.hash,
                "hash_hex": e.hash_hex,
                "offset": e.offset,
                "size": e.size,
                "space": e.space,
                "magic": label,
                "ext": ext,
            }
            nm = names.get(e.hash)
            if nm:
                rec["name"] = nm
            out.append(rec)
        return out

    def histogram(self) -> dict[str, int]:
        h: dict[str, int] = {}
        for e in self.entries:
            label, _ = detect_magic(self.peek(e, 32))
            h[label] = h.get(label, 0) + 1
        return dict(sorted(h.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_names(path: Optional[str]) -> dict[int, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(k, 16) if isinstance(k, str) and not k.isdigit() else int(k): v
            for k, v in data.items()}


def _safe_stem(entry: WdfEntry, name: Optional[str], ext: str) -> str:
    if name:
        return name.replace("\\", "/").lstrip("/")
    return f"{entry.hash_hex}{ext}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TQ WDF archive reader")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("archive", type=Path)
        p.add_argument("--names", help="JSON map hash->filename to annotate output")

    p = sub.add_parser("index", help="dump the full index to JSON")
    add_common(p)
    p.add_argument("-o", "--out", type=Path, required=True)

    p = sub.add_parser("stats", help="structural validation + payload histogram")
    add_common(p)

    p = sub.add_parser("list", help="print entries")
    add_common(p)
    p.add_argument("--magic", help="filter by detected magic label, e.g. DDS")
    p.add_argument("-n", "--limit", type=int, default=50)

    p = sub.add_parser("extract", help="extract one entry")
    add_common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hash", help="entry hash, decimal or 0x-hex")
    g.add_argument("--name", help="filename (requires tqhash.py)")
    p.add_argument("-o", "--out", type=Path, required=True)

    p = sub.add_parser("extract-type", help="extract entries of one detected type")
    add_common(p)
    p.add_argument("--magic", required=True)
    p.add_argument("-d", "--dir", type=Path, required=True)
    p.add_argument("--limit", type=int, default=50,
                   help="max entries to write (default 50; use 0 for no limit)")

    p = sub.add_parser("extract-all", help="extract EVERY entry (large!)")
    add_common(p)
    p.add_argument("-d", "--dir", type=Path, required=True)
    p.add_argument("--yes", action="store_true", help="required confirmation")

    args = ap.parse_args(argv)
    names = _load_names(getattr(args, "names", None))

    with WdfArchive(args.archive) as a:
        if args.cmd == "stats":
            v = a.validate()
            v["payload_histogram"] = a.histogram()
            print(json.dumps(v, indent=2))
            return 0

        if args.cmd == "index":
            recs = a.index_records(names)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(recs, indent=1), encoding="utf-8")
            print(f"wrote {len(recs)} records -> {args.out}")
            return 0

        if args.cmd == "list":
            shown = 0
            for e in a:
                label, ext = detect_magic(a.peek(e, 32))
                if args.magic and label != args.magic:
                    continue
                nm = names.get(e.hash, "")
                print(f"{e.hash_hex}  off={e.offset:<10} size={e.size:<9} {label:<8} {nm}")
                shown += 1
                if args.limit and shown >= args.limit:
                    break
            return 0

        if args.cmd == "extract":
            if args.hash:
                h = int(args.hash, 0)
                e = a.get(h)
                if e is None:
                    print(f"hash {h:#010x} not found", file=sys.stderr)
                    return 1
                data = a.read(e)
            else:
                data = a.read_by_name(args.name)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(data)
            print(f"wrote {len(data)} bytes -> {args.out}")
            return 0

        if args.cmd == "extract-type":
            args.dir.mkdir(parents=True, exist_ok=True)
            n = 0
            for e in a:
                label, ext = detect_magic(a.peek(e, 32))
                if label != args.magic:
                    continue
                rel = _safe_stem(e, names.get(e.hash), ext)
                dest = args.dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(a.read(e))
                n += 1
                if args.limit and n >= args.limit:
                    break
            print(f"extracted {n} '{args.magic}' entries -> {args.dir}")
            return 0

        if args.cmd == "extract-all":
            if not args.yes:
                print("refusing: extract-all writes every entry; pass --yes", file=sys.stderr)
                return 2
            args.dir.mkdir(parents=True, exist_ok=True)
            for e in a:
                label, ext = detect_magic(a.peek(e, 32))
                rel = _safe_stem(e, names.get(e.hash), ext)
                dest = args.dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(a.read(e))
            print(f"extracted {len(a)} entries -> {args.dir}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
