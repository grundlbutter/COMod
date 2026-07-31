r"""
wdf_names.py -- recover WDF entry filenames from hashes, using the recovered
TQ hash (core/tqhash.py) plus a wordlist harvested from the install tree.

The WDF index stores only `u32 nameHash`, so names are recovered by hashing
candidate paths and matching.  Candidates come from:
  * every loose file under the install root (relative path, POSIX form)
  * the same paths re-rooted under each package name (c3/..., data/...)
  * paths referenced inside .DMap headers
  * quoted path-like strings found in ini/*.json and ini/*.ini

Writes out/dll/wdf_name_recovery.txt and .json.

    py -3 tools/wdf_names.py
"""
from __future__ import annotations

import json
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                            # noqa: E402
from tqhash import tq_hash, normalise_path            # noqa: E402

ROOT = coroot.default_root()
OUT = Path(__file__).resolve().parents[1] / "out" / "dll"
WDFS = ["c3.wdf", "data.wdf"]

PATHISH = re.compile(rb"[A-Za-z0-9_\-.]+(?:[\\/][A-Za-z0-9_\-. ]+)+\.[A-Za-z0-9]{1,5}")


def read_wdf(path: Path):
    with path.open("rb") as f:
        magic, count, index_off = struct.unpack("<III", f.read(12))
        if magic != 0x57444650:
            raise SystemExit(f"bad magic in {path}")
        f.seek(index_off)
        blob = f.read(count * 16)
    return [struct.unpack_from("<IIII", blob, i * 16) for i in range(count)]


def harvest() -> set[str]:
    cands: set[str] = set()

    # 1. loose files, as-is and re-rooted under each package name
    packages = [p.stem for p in (ROOT.glob("*.wdf"))]
    for p in ROOT.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        top = rel.split("/", 1)[0].lower()
        if top in ("bin", "debug", "log", ".sentry-native", "launcherresources"):
            continue
        cands.add(rel)
        rest = rel.split("/", 1)[1] if "/" in rel else rel
        for pk in packages:
            cands.add(f"{pk}/{rest}")
            cands.add(f"{pk}/{rel}")

    # 2. path-like strings inside ini/ and map/
    for sub, pats in (("ini", ("*.ini", "*.json")), ("map", ("*.DMap",))):
        d = ROOT / sub
        if not d.is_dir():
            continue
        for pat in pats:
            for f in d.rglob(pat):
                try:
                    blob = f.read_bytes()
                except OSError:
                    continue
                for m in PATHISH.findall(blob):
                    s = m.decode("latin-1")
                    cands.add(s)
                    for pk in packages:
                        cands.add(f"{pk}/{s}")
    return cands


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    resolved: dict[int, str] = {}
    tables = {}
    for w in WDFS:
        p = ROOT / w
        if not p.exists():
            continue
        tables[w] = read_wdf(p)
        print(f"{w}: {len(tables[w])} entries")

    wanted = {}
    for w, ents in tables.items():
        for h, off, size, space in ents:
            wanted.setdefault(h, []).append((w, off, size, space))
    print(f"{len(wanted)} distinct hashes to resolve")

    cands = harvest()
    print(f"{len(cands)} candidate paths harvested")

    seen = set()
    for c in cands:
        n = normalise_path(c)
        if n in seen:
            continue
        seen.add(n)
        h = tq_hash(n)
        if h in wanted and h not in resolved:
            resolved[h] = n
    print(f"resolved {len(resolved)}/{len(wanted)} "
          f"({100.0*len(resolved)/len(wanted):.1f}%)")

    per_arch = Counter()
    per_dir = Counter()
    for h, name in resolved.items():
        for w, *_ in wanted[h]:
            per_arch[w] += 1
        per_dir[name.split("/", 1)[0] if "/" in name else "(root)"] += 1

    lines = ["# WDF filename recovery via the recovered TQ hash", "",
             f"resolved {len(resolved)} of {len(wanted)} distinct hashes",
             "", "## per archive", ""]
    for w, ents in tables.items():
        n = sum(1 for h, *_ in ents if h in resolved)
        lines.append(f"  {w}: {n}/{len(ents)} ({100.0*n/len(ents):.1f}%)")
    lines += ["", "## per top-level directory of the recovered name", ""]
    for d, n in per_dir.most_common():
        lines.append(f"  {d:24s} {n}")
    lines += ["", "## resolved entries", "", "# hash\tarchive\toffset\tsize\tname"]
    for h in sorted(resolved, key=lambda h: resolved[h]):
        for w, off, size, space in wanted[h]:
            lines.append(f"0x{h:08X}\t{w}\t{off}\t{size}\t{resolved[h]}")
    (OUT / "wdf_name_recovery.txt").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "wdf_name_recovery.json").write_text(json.dumps(
        {"resolved": {f"0x{h:08X}": n for h, n in sorted(resolved.items())},
         "n_distinct_hashes": len(wanted),
         "n_resolved": len(resolved)}, indent=1), encoding="utf-8")
    for d, n in per_dir.most_common(15):
        print(f"   {d:24s} {n}")
    print(f"wrote {OUT/'wdf_name_recovery.txt'}")


if __name__ == "__main__":
    main()
