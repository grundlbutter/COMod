#!/usr/bin/env python3
"""
wdf_recover.py -- recover WDF entry filenames by dictionary + pattern attack.

The WDF index stores only a u32 name hash (core/wdf.py).  core/tqhash.py has
the exact hash lifted from TqPackageWdf.dll, so name recovery is a search
problem: generate plausible archive-relative paths, hash them, keep the hits.

Two things make it tractable:

  * The loose files shipped alongside the archives mirror the archive namespace
    exactly, so they give both the directory tree and the basename conventions.
  * The hash consumes the (lowercased, '/'-normalised) path as little-endian
    u32 words, sequentially.  So the mixing state after any 4-byte-aligned
    prefix can be CACHED and reused for every candidate sharing that prefix.
    Enumerating `<dir>/pic0000.dds` .. `<dir>/pic9999.dds` therefore costs ~3
    mixing rounds per candidate instead of ~12.

False positives: a 32-bit hash against ~25k target hashes yields roughly
    n_candidates * 25000 / 2**32
spurious hits, i.e. ~6 per million candidates.  This tool verifies every hit
that it can (payload magic must agree with the extension) and reports the
residual expectation so the number is never silently trusted.

Usage:
    python tools/wdf_recover.py                    # full run, writes out/wdf/
    python tools/wdf_recover.py --no-enumerate     # dictionary only
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                            # noqa: E402
from tqhash import (MAGIC_A, MAGIC_B, SEED_V, SEED_H1, SEED_H2, ROUND_K,
                    OR_A, AND_A, OR_B, AND_B, MASK32, normalise_path, tq_hash)
from wdf import WdfArchive, detect_magic

ROOT_DEFAULT = coroot.default_root()
ARCHIVES = ("c3.wdf", "data.wdf")

EXTS = (".dds", ".c3", ".png", ".jpg", ".bmp", ".wav", ".msk", ".ani",
        ".cur", ".ico", ".tga", ".gif", ".dat")

_PATHISH = re.compile(r"[A-Za-z0-9_\-./\\]{3,120}")
_NUMPAT = re.compile(r"^(.*?)(\d+)$")


# ---------------------------------------------------------------------------
# incremental hashing
# ---------------------------------------------------------------------------

def _mix(v: int, h1: int, h2: int, w: int) -> tuple[int, int, int]:
    """One round of the core loop at TqPackageWdf.dll rva 0x3E42."""
    v = ((v << 1) | (v >> 31)) & MASK32
    k = ROUND_K ^ v
    h1 ^= w
    h2 ^= w
    t = (((k + h2) & MASK32) | OR_A) & AND_A
    prod = h1 * t
    lo, hi = prod & MASK32, (prod >> 32) & MASK32
    s = lo + hi + (1 if hi else 0)
    acc = (s & MASK32) + (1 if s > MASK32 else 0)
    acc &= MASK32
    t2 = (((k + h1) & MASK32) | OR_B) & AND_B
    h1 = acc
    prod = h2 * t2
    lo, hi = prod & MASK32, (prod >> 32) & MASK32
    d = (hi * 2) & MASK32
    s = lo + d + (1 if hi * 2 > MASK32 else 0)
    acc2 = s & MASK32
    if s > MASK32:
        acc2 = (acc2 + 2) & MASK32
    return v, acc, acc2


def hash_state(prefix: bytes) -> tuple[tuple[int, int, int], bytes]:
    """State after the largest 4-byte-aligned chunk of `prefix`, plus the tail."""
    n = len(prefix) & ~3
    v, h1, h2 = SEED_V, SEED_H1, SEED_H2
    for w in struct.unpack("<%dI" % (n // 4), prefix[:n]):
        v, h1, h2 = _mix(v, h1, h2, w)
    return (v, h1, h2), prefix[n:]


def hash_from(state: tuple[int, int, int], rest: bytes) -> int:
    """Finish a hash given a cached prefix state and the remaining bytes."""
    v, h1, h2 = state
    pad = (-len(rest)) % 4
    buf = rest + b"\x00" * pad
    for w in struct.unpack("<%dI" % (len(buf) // 4), buf):
        v, h1, h2 = _mix(v, h1, h2, w)
    v, h1, h2 = _mix(v, h1, h2, MAGIC_A)
    v, h1, h2 = _mix(v, h1, h2, MAGIC_B)
    return (h1 ^ h2) & MASK32


# ---------------------------------------------------------------------------
# wordlist harvesting
# ---------------------------------------------------------------------------

def harvest_loose(root: Path) -> set[str]:
    out = set()
    for p in root.rglob("*"):
        if p.is_file():
            try:
                out.add(p.relative_to(root).as_posix().lower())
            except ValueError:
                pass
    return out


_RUNS = re.compile(rb"[\x20-\x7e]{4,200}")
_TOK = re.compile(r"[A-Za-z0-9_\-./\\]{4,150}")
_HASEXT = re.compile(r"\.[A-Za-z0-9]{2,4}$")

# Directories worth scraping for embedded asset paths.  ani/*.json is by far
# the richest source: the .ani animation definitions name every puzzle tile and
# every cover frame explicitly, which is what unlocks data.wdf.
SCRAPE_DIRS = ("map", "ani", "ini", "c3", "graphics", "data",
               "LauncherResources", "sound")


def harvest_strings(root: Path, max_file: int = 12_000_000) -> set[str]:
    """Scrape path-looking tokens out of every loose asset that might name one.

    Covers ini/ (game database), ani/ (animation definitions -> puzzle tiles and
    cover frames), and map/ (.DMap embedded puzzle paths, plus .pul/.scene/.Part
    which enumerate the individual tile images).
    """
    out: set[str] = set()
    for sub in SCRAPE_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            try:
                if p.stat().st_size > max_file:
                    continue
                blob = p.read_bytes()
            except OSError:
                continue
            for m in _RUNS.finditer(blob):
                s = m.group(0).decode("latin-1")
                for tok in _TOK.findall(s):
                    if ("/" in tok or "\\" in tok) and _HASEXT.search(tok):
                        out.add(normalise_path(tok).lstrip("/"))
    return out


def ext_permute(words) -> set[str]:
    out = set()
    for w in words:
        i = w.rfind(".")
        if i <= 0 or "/" in w[i:]:
            continue
        for e in EXTS:
            out.add(w[:i] + e)
    return out


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------

class Recovery:
    def __init__(self, root: Path):
        self.root = root
        self.arcs = {a: WdfArchive(root / a) for a in ARCHIVES if (root / a).exists()}
        self.want: dict[int, str] = {}
        for a, ar in self.arcs.items():
            for e in ar:
                self.want[e.hash] = a
        self.found: dict[int, str] = {}
        # provenance: "dict" = came from a real string observed on disk (a loose
        # filename or a path embedded in an asset) -> false-positive risk is
        # negligible.  "enum" = synthesised by pattern enumeration -> carries the
        # 32-bit birthday risk quantified in the summary.
        self.origin: dict[int, str] = {}
        self.tested = 0
        self.tested_dict = 0

    def close(self):
        for ar in self.arcs.values():
            ar.close()

    def try_paths(self, paths, origin: str = "dict") -> int:
        new = 0
        want, found = self.want, self.found
        for p in paths:
            self.tested += 1
            if origin == "dict":
                self.tested_dict += 1
            h = tq_hash(p)
            if h in want and h not in found:
                found[h] = p
                self.origin[h] = origin
                new += 1
        return new

    def try_prefixed(self, prefix: str, suffixes, origin: str = "enum") -> int:
        """All candidates share `prefix`; reuse the cached mixing state."""
        state, tail = hash_state(prefix.encode("latin-1", "replace"))
        want, found = self.want, self.found
        new = 0
        for s in suffixes:
            self.tested += 1
            h = hash_from(state, tail + s.encode("latin-1", "replace"))
            if h in want and h not in found:
                found[h] = prefix + s
                self.origin[h] = origin
                new += 1
        return new

    def report(self, label: str, t0: float):
        n = len(self.found)
        tot = len(self.want)
        fp = self.tested * tot / 2 ** 32
        print(f"  {label:<34} {n:6}/{tot} ({100.0*n/tot:5.1f}%)  "
              f"tested={self.tested:,} exp_fp~{fp:.1f}  {time.time()-t0:.0f}s",
              flush=True)


def build_patterns(names) -> dict[str, set[tuple[str, int, str]]]:
    """dir -> {(literal_prefix, ndigits, ext)} observed in that directory."""
    pats = defaultdict(set)
    for n in names:
        i = n.rfind("/")
        if i <= 0:
            continue
        d, base = n[:i], n[i + 1:]
        j = base.rfind(".")
        if j <= 0:
            continue
        stem, ext = base[:j], base[j:]
        m = _NUMPAT.match(stem)
        if m:
            pats[d].add((m.group(1), len(m.group(2)), ext))
    return pats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "out" / "wdf")
    ap.add_argument("--no-enumerate", action="store_true")
    ap.add_argument("--seed", type=Path,
                    help="existing hash->name JSON to seed from")
    a = ap.parse_args()

    t0 = time.time()
    r = Recovery(a.root)
    print(f"targets: {len(r.want)} hashes across {list(r.arcs)}", flush=True)

    if a.seed and a.seed.exists():
        d = json.loads(a.seed.read_text())
        d = d.get("resolved", d)
        for k, v in d.items():
            h = int(k, 16)
            if h in r.want:
                r.found[h] = normalise_path(v).lstrip("/")
        r.report("seeded", t0)

    loose = harvest_loose(a.root)
    strings = harvest_strings(a.root)
    print(f"loose paths: {len(loose)}, path-ish strings: {len(strings)}", flush=True)

    r.try_paths(loose | strings)
    r.report("dictionary", t0)
    r.try_paths(ext_permute(loose | strings))
    r.report("+ extension permute", t0)

    if not a.no_enumerate:
        known = set(loose) | set(r.found.values())
        dirs = sorted({n[:n.rfind("/")] for n in known if "/" in n})
        pats = build_patterns(known)
        # patterns common enough to be worth trying in EVERY directory
        glob_pats = Counter()
        for d, ps in pats.items():
            for p in ps:
                glob_pats[p] += 1
        universal = [p for p, c in glob_pats.most_common(12)
                     if p[1] <= 4 and c >= 3]
        print(f"dirs={len(dirs)} universal patterns={universal}", flush=True)

        # per-directory: its own observed patterns, then the universal ones
        for idx, d in enumerate(dirs):
            todo = set(pats.get(d, ())) | set(universal)
            for (lit, nd, ext) in todo:
                if nd > 4:
                    continue
                sfx = [f"/{lit}{i:0{nd}d}{ext}" for i in range(10 ** nd)]
                r.try_prefixed(d, sfx)
            if idx % 100 == 0:
                r.report(f"enumerate dir {idx}/{len(dirs)}", t0)
        r.report("after enumeration", t0)

        # one more dictionary sweep over the newly-learned basenames
        known2 = set(r.found.values())
        bases = {n[n.rfind("/") + 1:] for n in known2}
        for d in dirs:
            r.try_prefixed(d, ["/" + b for b in bases])
        r.report("dir x basename cross", t0)

    # ---- verify + emit -----------------------------------------------------
    outdir = a.out
    outdir.mkdir(parents=True, exist_ok=True)
    EXT_MAGIC = {".dds": "DDS", ".c3": "MAXF", ".png": "PNG", ".jpg": "JPEG",
                 ".bmp": "BMP", ".wav": "RIFF", ".cur": "CUR", ".ico": "ICO",
                 ".gif": "GIF"}
    summary = {}
    suspect_total = 0
    for name, ar in r.arcs.items():
        stem = name.split(".")[0]
        mine = {e.hash: r.found[e.hash] for e in ar if e.hash in r.found}
        # consistency check: extension vs detected payload magic
        suspect = []
        for e in ar:
            nm = mine.get(e.hash)
            if not nm:
                continue
            ext = nm[nm.rfind("."):].lower() if "." in nm else ""
            want_magic = EXT_MAGIC.get(ext)
            got = detect_magic(ar.peek(e, 32))[0]
            if want_magic and got != want_magic:
                suspect.append({"hash": f"{e.hash:08x}", "name": nm,
                                "detected": got, "expected": want_magic})
        suspect_total += len(suspect)
        (outdir / f"{stem}_names.json").write_text(
            json.dumps({f"{h:08x}": v for h, v in sorted(mine.items())}, indent=1),
            encoding="utf-8")
        recs = ar.index_records(mine)
        (outdir / f"{stem}_index.json").write_text(json.dumps(recs, indent=1),
                                                   encoding="utf-8")
        n_dict = sum(1 for h in mine if r.origin.get(h) == "dict")
        summary[name] = {
            "entries": len(ar),
            "names_recovered": len(mine),
            "hit_rate_pct": round(100.0 * len(mine) / len(ar), 2),
            "from_observed_strings": n_dict,
            "from_pattern_enumeration": len(mine) - n_dict,
            "unresolved": len(ar) - len(mine),
            "extension_magic_mismatches": len(suspect),
            "unresolved_by_magic": dict(Counter(
                detect_magic(ar.peek(e, 32))[0] for e in ar if e.hash not in mine)),
            "payload_histogram": ar.histogram(),
        }
        if suspect:
            (outdir / f"{stem}_suspect_names.json").write_text(
                json.dumps(suspect, indent=1), encoding="utf-8")

    summary["_meta"] = {
        "candidates_tested": r.tested,
        "candidates_from_observed_strings": r.tested_dict,
        "expected_false_positives_total": round(r.tested * len(r.want) / 2 ** 32, 2),
        "expected_false_positives_dict_only":
            round(r.tested_dict * len(r.want) / 2 ** 32, 3),
        "observed_extension_magic_mismatches": suspect_total,
        "note": ("Names marked from_observed_strings came from real strings found "
                 "on disk and are effectively certain. Names from "
                 "from_pattern_enumeration carry the birthday risk above, spread "
                 "across that subset."),
        "seconds": round(time.time() - t0, 1),
    }
    (outdir / "name_recovery_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2)[:2500])
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
