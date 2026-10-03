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
    py -3 tools/wdf_names.py --root "C:/.../Clients/6609"

WHICH INSTALL THIS IS ABOUT, AND WHY THE FLAG HAD TO BE ADDED
-------------------------------------------------------------
**The wordlist is harvested from an install**, so what this recovers depends
entirely on which client it ran against -- and until 2026-08-23 the answer was
always ``coroot.default_root()``, read at IMPORT, with no way to say otherwise.
A user bootstrapping the client they were looking at got the configured
client's names.

The OUTPUT stays shared (``coroot.GLOBAL`` lists
``out/dll/wdf_name_recovery``): a WDF entry's name is recovered by hashing a
candidate string, the TQ hash is a pure function of that string, so an entry
resolved here is true in every install.

**THIS USED TO SAY THAT POINTING IT AT A SECOND CLIENT "TOPS UP one table
rather than producing a rival one".  IT DOES NOT, AND NEVER DID.**  ``main``
starts from an empty ``resolved`` and WRITES THE JSON FRESH, so a second
client's run REPLACES the shared table with only what that client's archives
could resolve; every name the previous client contributed and this one cannot
is lost.  The sentence described the design the shared path deserves, not the
code underneath it, which is the worst way for a docstring to be wrong: the
next reader points this at another install ON THE STRENGTH OF IT and empties
a table eight tools read, silently, with an exit code of 0.  Corrected
2026-09-18 rather than left standing while the merge is written.

**THE EARLY RETURN BELOW IS LOAD-BEARING BECAUSE OF THIS.**  When an install
ships no ``c3.wdf``/``data.wdf`` this tool returns 0 without writing, and that
is the only thing standing between a DatPkg/TPD client and a zero-entry
rewrite of every other client's names.  It is not a tidy-up for an empty
case; it is the guard.  A TPD client needs none of this anyway -- a
``.tpd``/``.tpi`` pair stores the path itself, which
``coassets.AssetRoot.declared_names`` reads directly.

Making the write a real MERGE -- seed ``resolved`` from the existing table,
union this run into it -- is the right end state and is filed separately: it
changes the semantics of a GLOBAL artefact that eight tools read, so it owes
its own control (a second client's names must survive a run against the
first), and that is a measurement rather than a rider on a docstring fix.

Shared output and install-independent input are different claims, and only the
first one holds here.  ``tools/build_opcodes.py`` is the one builder in
``health.DERIVED`` for which both hold, and it is the one offered no ``--root``.
"""
from __future__ import annotations

import argparse
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

#: The install used when nobody names one.  Kept for callers that import this
#: module, and NOT read by `harvest` or `main` any more -- a module constant
#: resolved at import names whichever install was configured *then*, which is
#: exactly the bug `--root` closes.
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


def harvest(root: Path | None = None) -> set[str]:
    """Candidate paths, walked out of ``root`` -- the install this run is
    about, not the one that happened to be configured at import."""
    root = Path(root) if root is not None else coroot.default_root()
    cands: set[str] = set()

    # 1. loose files, as-is and re-rooted under each package name
    packages = [p.stem for p in (root.glob("*.wdf"))]
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
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
        d = root / sub
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    coroot.add_root_argument(ap)
    a = ap.parse_args(argv)
    root = coroot.game_root(a.root)
    OUT.mkdir(parents=True, exist_ok=True)
    # Printed, not assumed. This tool used to be silent about which install it
    # walked, so a run against the wrong client was indistinguishable from a
    # run against the right one.
    print(f"install: {root}")
    resolved: dict[int, str] = {}
    tables = {}
    for w in WDFS:
        p = root / w
        if not p.exists():
            continue
        tables[w] = read_wdf(p)
        print(f"{w}: {len(tables[w])} entries")
    if not tables:
        # A shared artefact must not be OVERWRITTEN with an empty one because
        # somebody pointed this at a DatPkg client. out/dll/wdf_name_recovery
        # is coroot.GLOBAL -- every install reads the same file -- so a zero
        # -entry rewrite here destroys another client's recovered names.
        print(f"no {' or '.join(WDFS)} under {root} -- nothing to recover, "
              f"and the shared table is left alone rather than emptied.")
        return 0

    wanted = {}
    for w, ents in tables.items():
        for h, off, size, space in ents:
            wanted.setdefault(h, []).append((w, off, size, space))
    print(f"{len(wanted)} distinct hashes to resolve")

    cands = harvest(root)
    print(f"{len(cands)} candidate paths harvested from {root}")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
