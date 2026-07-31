r"""
verify_tqhash.py -- independent validation of core/tqhash.py against the real
WDF index tables.

Method: read the u32 name-hash of every entry in c3.wdf / data.wdf, then take
real file paths that exist loose on disk under the install root and check how
many of their hashes land in the archive's hash set.  A random 32-bit hash
would collide with a 10k-entry set with probability ~2.4e-6, so even a handful
of hits is decisive; a high hit rate on a whole directory tree is proof.

Several candidate "what string is actually hashed" hypotheses are tested, so
the output also settles the input-format question, not just the algorithm.

    py -3 tools/verify_tqhash.py
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                            # noqa: E402
from tqhash import tq_hash, normalise_path          # noqa: E402

ROOT = coroot.default_root()
WDFS = ["c3.wdf", "data.wdf"]


def read_wdf_index(path: Path):
    """(hashes:set[int], entries:list[(hash, offset, size, space)])"""
    with path.open("rb") as f:
        magic, count, index_off = struct.unpack("<III", f.read(12))
        assert magic == 0x57444650, f"bad magic 0x{magic:08X} in {path.name}"
        f.seek(index_off)
        blob = f.read(count * 16)
    entries = [struct.unpack_from("<IIII", blob, i * 16) for i in range(count)]
    return {e[0] for e in entries}, entries


def loose_files(root: Path, sub: str):
    base = root / sub
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        if p.is_file():
            out.append(p.relative_to(root).as_posix())
    return out


def main():
    archives = {}
    for w in WDFS:
        p = ROOT / w
        if not p.exists():
            print(f"!! missing {p}")
            continue
        hs, entries = read_wdf_index(p)
        archives[w] = hs
        print(f"{w}: {len(entries)} entries, {len(hs)} distinct hashes")
    allhashes = set().union(*archives.values()) if archives else set()
    print(f"union: {len(allhashes)} distinct hashes\n")

    # candidate inputs, given a path relative to the install root
    hypotheses = {
        "full relative path            (e.g. c3/weapon/410.c3)":
            lambda rel: rel,
        "path minus first segment      (e.g. weapon/410.c3)":
            lambda rel: rel.split("/", 1)[1] if "/" in rel else rel,
        "backslash form                (e.g. c3\\weapon\\410.c3)":
            lambda rel: rel.replace("/", "\\"),
        "basename only                 (e.g. 410.c3)":
            lambda rel: rel.rsplit("/", 1)[-1],
    }

    subdirs = ["c3", "ani", "data", "graphics", "sound", "avatar", "font", "map"]
    files = []
    for s in subdirs:
        f = loose_files(ROOT, s)
        if f:
            print(f"  loose {s}/: {len(f)} files")
            files.extend(f)
    print(f"total loose candidates: {len(files)}\n")

    best = None
    for label, fn in hypotheses.items():
        hits = 0
        per_dir = Counter()
        examples = []
        for rel in files:
            h = tq_hash(fn(rel))
            if h in allhashes:
                hits += 1
                per_dir[rel.split("/", 1)[0]] += 1
                if len(examples) < 6:
                    examples.append((rel, fn(rel), h))
        pct = 100.0 * hits / len(files) if files else 0
        print(f"{label}\n    {hits}/{len(files)} = {pct:.1f}%   {dict(per_dir)}")
        for rel, inp, h in examples:
            print(f"      {rel}  ->  hash({normalise_path(inp)!r}) = 0x{h:08X}")
        print()
        if best is None or hits > best[1]:
            best = (label, hits)

    if best and best[1]:
        print(f"BEST: {best[0]}  ({best[1]} hits)")
    else:
        print("NO HITS — either the algorithm or the input form is wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
