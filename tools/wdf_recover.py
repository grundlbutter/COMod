#!/usr/bin/env python3
r"""
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

A DatPkg community client (.tpi/.tpd, see core/tpd.py) is a third, very rich
wordlist source: its index stores ~130k **plaintext** asset paths in the same
TQ-hash namespace the WDF client hashes.  Point `--tpi` at those indexes and
every path they name is tried directly, and its numeric basenames feed the
pattern enumerator -- which is what recovers the custom-numbered reskins in a
community client's own garments*.wdf archives.

Usage:
    python tools/wdf_recover.py                    # baseline c3/data, out/wdf/
    python tools/wdf_recover.py --no-enumerate     # dictionary only
    python tools/wdf_recover.py \                   # + a DatPkg client as a
        --tpi "D:/Zephyr Conquer 1057"             #   wordlist (dir or *.tpi)
    python tools/wdf_recover.py \                   # recover a DIFFERENT set of
        --root "D:/Zephyr Conquer 1057" \           #   archives (community
        --archives garments.wdf garments1.wdf \     #   garments), seeded from
        --tpi "D:/Zephyr Conquer 1057" \            #   the baseline names and
        --seed out/wdf/c3_names.json \              #   the DatPkg wordlist
        --max-digits 7 --out out/garments
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
from tpd import read_index
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


#: UTF-16LE runs.  A .NET assembly stores its literals this way, and the
#: interleaved NULs break `_RUNS` -- so an ASCII-only scan reads a .NET tool as
#: having almost no strings at all.  MEASURED: Zephyr's own `EffectChanger.exe`
#: spells out hundreds of asset paths (`\c3\effect\CountB\Count-1\countb3.dds`)
#: that this tool had never once seen.
_RUNS16 = re.compile(rb"(?:[\x20-\x7e]\x00){4,200}")


def _tokens(s: str):
    for tok in _TOK.findall(s):
        if ("/" in tok or "\\" in tok) and _HASEXT.search(tok):
            yield normalise_path(tok).lstrip("/")


def harvest_strings(root: Path, max_file: int = 12_000_000) -> set[str]:
    """Scrape path-looking tokens out of everything that might name an asset.

    Covers ini/ (game database), ani/ (animation definitions -> puzzle tiles and
    cover frames), and map/ (.DMap embedded puzzle paths, plus .pul/.scene/.Part
    which enumerate the individual tile images).

    **Also every .exe and .dll, wherever they sit, and in UTF-16 as well as
    ASCII.** Two blind spots, both found by pointing this at a private server's
    client and asking why it knew nothing about garments:

      * the binaries live at the ROOT, outside `SCRAPE_DIRS`, so they were
        never opened -- and a client's own executable names the UI art it
        loads.
      * literals in a .NET tool are UTF-16LE, and the NUL between every
        character breaks an ASCII run, so an ASCII-only scan reads a .NET
        assembly as having almost no strings at all.

    **MEASURED YIELD ON 6090: ZERO new names.** 102,778 -> 103,148 path
    strings and the dictionary pass lands on 23,963/24,757 either way. This is
    a capability, not a win, and the docstring says so rather than implying
    otherwise.

    A correction worth keeping, because it is the reason the number above is
    not 3: a probe of mine reported "3 names nothing else had" -- measured
    against the MERGED committed tables while the tool measures against its
    own fresh run. Different baselines, so the three were already reachable by
    the existing scrape. *A background is a property of the measure, not of
    the thing measured*, and importing another instrument's is how a null
    becomes a finding.

    What it is worth keeping for: the blind spot is real and structural, and
    it cost the garment investigation a whole source. Zephyr's own
    `EffectChanger.exe` spells out hundreds of asset paths that this tool
    could not physically see -- wrong directory and wrong encoding. It still
    did not name a garment, so the capability is unproven on the case that
    motivated it; it is cheap, and it means the next client's tooling is
    readable rather than invisible.
    """
    out: set[str] = set()
    seen: set[Path] = set()

    def scrape(p: Path) -> None:
        if p in seen or not p.is_file():
            return
        seen.add(p)
        try:
            if p.stat().st_size > max_file:
                return
            blob = p.read_bytes()
        except OSError:
            return
        for m in _RUNS.finditer(blob):
            out.update(_tokens(m.group(0).decode("latin-1")))
        for m in _RUNS16.finditer(blob):
            out.update(_tokens(m.group(0).decode("utf-16-le", "replace")))

    for sub in SCRAPE_DIRS:
        d = root / sub
        if d.is_dir():
            for p in d.rglob("*"):
                scrape(p)
    # The binaries, wherever they are. A client names its own UI art.
    for p in root.rglob("*"):
        if p.suffix.lower() in (".exe", ".dll"):
            scrape(p)
    return out


def harvest_tpi(paths) -> set[str]:
    """Plaintext asset paths out of every DatPkg .tpi index in `paths`.

    Each element of `paths` is either a .tpi file or a directory scanned
    (non-recursively) for *.tpi.  The names are stored already normalised
    (forward slashes) in the DatPkg namespace, which is byte-identical to the
    namespace the WDF client hashes, so they can be hashed as-is.
    """
    out: set[str] = set()
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.tpi")))
        elif p.is_file():
            files.append(p)
    for f in files:
        try:
            entries = read_index(f)
        except Exception as exc:                       # noqa: BLE001
            print(f"  !! skipped {f}: {exc}", flush=True)
            continue
        for e in entries:
            out.add(normalise_path(e.name).lstrip("/"))
        print(f"  tpi {f.name}: {len(entries)} names", flush=True)
    return out


#: Bare numeric ids live in these; the PATHS do not.
_ID_TABLES = ("armor.ini", "armet.ini", "armet1.ini", "weapon.ini",
              "misc.ini", "mount.ini", "head.ini", "3DSimpleObj.ini")

_ID_KEYS = ("mesh", "texture", "mixtex", "thirdtex", "fourthtex", "obj",
            "simpleobjid", "standbymotion")


def harvest_ids(root: Path) -> set[int]:
    r"""Bare numeric asset ids out of the appearance tables.

    **This is the wordlist `harvest_strings` cannot produce.** Its filter is
    *a token containing a slash AND an extension*, so `armor.ini`'s
    `Mesh0=1000000` -- no slash, no extension -- is skipped, and the path the
    client builds from that id is never tried unless some other file happens
    to spell it out.

    Usually another file does: `3dobj.ini` / `3DObj.dbc` map id -> path with
    the path written out, and the scraper finds those. The gap is the paths
    **no table spells out**, which the client assembles from an id by a
    convention -- monster skins, colourway variants, npc geometry.
    """
    out: set[int] = set()
    ini = root / "ini"
    if not ini.is_dir():
        return out
    for name in _ID_TABLES:
        p = ini / name
        if not p.is_file():
            for q in ini.iterdir():             # case-insensitive on purpose
                if q.is_file() and q.name.lower() == name.lower():
                    p = q
                    break
        if not p.is_file():
            continue
        for line in p.read_text("latin1", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                body = line[1:-1].strip()
                if body.isdigit():
                    out.add(int(body))
            elif "=" in line:
                k, v = line.split("=", 1)
                if k.strip().lower().rstrip("0123456789") in _ID_KEYS:
                    v = v.strip()
                    if v.isdigit() and v != "0":
                        out.add(int(v))
    return out


def id_paths(ids) -> set[str]:
    r"""Candidate paths built from real ids by this repo's OWN conventions.

    The rules are imported from where they are recorded rather than retyped:
    `core/monsterart.py` owns the monster ones and states what it measured
    them against. A convention copied into a second file is a convention that
    drifts.

    **This is a DICTIONARY, not an enumeration**, and that is the whole point.
    The ids are real rows out of real tables, so the candidate set is small and
    the birthday risk is negligible -- MEASURED on 6090, 50,488 candidates
    against 25,013 target hashes is **0.294 expected false positives**, against
    the pattern enumerator's ~797 over 138 million candidates.

    MEASURED YIELD, and it is small -- stated because the percentage will not
    show it. Dictionary-only on 6090, no DatPkg wordlist, so the id source is
    the only variable:

        --no-ids   23,956 / 24,757   exp_fp 0.7
        with ids   23,963 / 24,757   exp_fp 1.4      **+7 names**

    16,079 ids expand to 127,849 candidate paths. Seven names for seven tenths
    of an expected false positive is a good trade, and it is a trade the
    enumerator cannot offer: it buys its last ~240 names with ~797.

    Do not expect more on this client. 6090 is already 94.9% recovered, so
    seven is close to what is left for ANY method there.

    ZEPHYR'S GARMENT ARCHIVES ARE A MEASURED DEAD END -- recorded so nobody
    repeats it. Five `garments*.wdf`, 14,051 entries, **0.0% named**, and they
    stay 0.0% against every wordlist this tool has:

        seeded with 24,655 official baseline names        0 / 14,051
        Zephyr's own 130,532 plaintext DatPkg names       0
        loose 18,036 + strings 126,177 + tpi 223,508
          + ids 38,856   (~370k candidates)               2, exp_fp ~1.0
        + extension permute  (4.1M candidates)            8, exp_fp ~13.5

    **The expected false positives exceed the finds**, and the finds are
    implausible on inspection -- `data/map/mapobj/.../fo-stone15.cur`, a
    puzzle-tile `.cur`, a scene effect -- none of which belong in a garment
    archive. That is noise, not recovery.

    The namespace is disjoint from everything nameable: not the official
    baseline, not Zephyr's own DatPkg index. The payloads are ordinary
    (200 `.c3` and 200 `.dds` in the first 400 sampled -- mesh+texture pairs,
    so ~7,025 garments), so the assets are unremarkable and only their NAMES
    are unreachable.

    **Enumeration cannot rescue it either, by construction.** The pattern
    enumerator bootstraps its directory list from names already known
    (`dirs = {n[:n.rfind('/')] for n in known}`). With zero known names in
    this namespace it has no directories of its own to work in and would brute
    force inside the official client's directories, which are measured not to
    apply. Running it would cost hours and could only produce more of the
    noise above. What this needs is a *source* for the names -- the server's
    own tables, a patch manifest, a client string dump -- not more search.
    """
    try:
        import monsterart
    except ImportError:                                    # pragma: no cover
        monsterart = None

    out: set[str] = set()
    for i in ids:
        # An appearance id addresses geometry and a skin directly. `core/dbc.py`
        # records the same shape from the compiled side: 9990010 -> c3/mesh/9990010.c3
        out.add(f"c3/mesh/{i}.c3")
        out.add(f"c3/texture/{i}.dds")
        # Nine-wide zero padding: the compiled tables store ids bare and the
        # mesh files kept their zeros, so both spellings are real.
        out.add(f"c3/mesh/{i:09d}.c3")
        out.add(f"c3/texture/{i:09d}.dds")
        # Colourway variants (`core/collection.py`).
        out.add(f"c3/texture/999{i}0.dds")
        out.add(f"c3/texture/999{i}.dds")
        # NPC geometry (`core/npcart.py`).
        out.add(f"c3/npc/{i}.c3")
        if monsterart is not None:
            out.add(monsterart.TEXTURE.format(body=i))
    return {normalise_path(p).lstrip("/") for p in out}


def discover_tpi_roots() -> list[Path]:
    r"""Declared installs that ship a DatPkg index, for the default wordlist.

    **`--tpi` has existed since this tool was written and the baseline run
    never used it**, because it defaulted to empty. The docstring above calls
    a DatPkg client *"a third, very rich wordlist source"* and then the run
    that produces `out/wdf/*_names.json` did not consult one, on a box holding
    two -- so the committed tables sit at 94.06% of 6090's 25,013 hashes while
    the material to do better was on disk the whole time.

    MEASURED, hashing the plaintext names straight out of the indexes and
    intersecting with the archive's hashes:

        6090 archives                25,013 hashes
          named by the tables        23,526   94.06%
          still unnamed               1,487
        7878 supplies 146,194 pairs, resolving   201
        Zephyr        130,464 pairs, resolving   678
        union                                    795   -> 97.23%

    And the control fires, which is what makes it worth defaulting on: where a
    DatPkg index and the recovery table both know a hash they AGREE on the
    name -- 23,365 of 23,395 for Zephyr, 12,974 of 12,975 for 7878. The method
    reproduces the existing recovery on the overlap before extending past it.

    Sourced from `coroot`'s declared-kinds map rather than a glob of the
    clients tree, so this uses installs the user has actually declared and
    nothing it merely found lying about.
    """
    try:
        kinds = coroot.read_settings().get(coroot.KINDS_KEY) or {}
    except Exception:                                  # noqa: BLE001
        return []
    out: list[Path] = []
    for raw in sorted(kinds):
        p = Path(raw)
        try:
            if p.is_dir() and any(p.glob("*.tpi")):
                out.append(p)
        except OSError:
            continue
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
    def __init__(self, root: Path, archives=ARCHIVES):
        self.root = root
        self.arcs = {a: WdfArchive(root / a) for a in archives if (root / a).exists()}
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
    ap.add_argument("--archives", nargs="+", default=list(ARCHIVES),
                    help="archive filenames under --root to recover "
                         "(default: the baseline c3.wdf data.wdf)")
    ap.add_argument("--tpi", nargs="+", type=Path, default=[],
                    help="DatPkg .tpi files or dirs to mine for a plaintext "
                         "wordlist (see core/tpd.py). Omit to use every "
                         "DECLARED install that ships one")
    ap.add_argument("--no-ids", action="store_true",
                    help="skip the id-referenced wordlist (bare asset ids "
                         "from the appearance tables, expanded by the "
                         "conventions core/monsterart.py records)")
    ap.add_argument("--no-tpi", action="store_true",
                    help="do not use any DatPkg wordlist, not even a "
                         "discovered one -- for reproducing an older run")
    ap.add_argument("--max-digits", type=int, default=4,
                    help="widest numeric run to brute-force per directory "
                         "pattern; 10**N candidates each (default 4)")
    ap.add_argument("--no-enumerate", action="store_true")
    ap.add_argument("--seed", type=Path,
                    help="existing hash->name JSON to seed from")
    a = ap.parse_args()

    t0 = time.time()
    r = Recovery(a.root, a.archives)
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
    # A DatPkg index is the richest wordlist available and it used to be
    # opt-in, so the run that produced the committed tables never consulted
    # one. Discovered by default now; `--tpi` still overrides and `--no-tpi`
    # reproduces the old behaviour.
    if a.tpi:
        tpi_src, how = list(a.tpi), "given"
    elif a.no_tpi:
        tpi_src, how = [], "suppressed by --no-tpi"
    else:
        tpi_src, how = discover_tpi_roots(), "discovered from declared installs"
    if tpi_src:
        print(f"DatPkg wordlist ({how}): "
              f"{', '.join(p.name for p in tpi_src)}", flush=True)
    else:
        # Said out loud. A silent empty wordlist is indistinguishable from a
        # box with no DatPkg client, and that difference is 795 names.
        print(f"DatPkg wordlist: NONE ({how}) -- recovery will run on loose "
              f"files and binary strings only", flush=True)
    tpi = harvest_tpi(tpi_src) if tpi_src else set()

    # Bare ids -> paths by convention. A DICTIONARY, not an enumeration: the
    # ids are real table rows, so the set is small and the birthday risk is
    # ~0.3 expected false positives rather than the enumerator's ~797.
    ids = set() if a.no_ids else harvest_ids(a.root)
    id_words = id_paths(ids) if ids else set()
    print(f"loose paths: {len(loose)}, path-ish strings: {len(strings)}, "
          f"tpi names: {len(tpi)}, ids: {len(ids)} -> {len(id_words)} "
          f"convention paths", flush=True)

    # tpi names are real observed strings -> same "dict" (near-certain) origin.
    r.try_paths(loose | strings | tpi | id_words)
    r.report("dictionary", t0)
    r.try_paths(ext_permute(loose | strings | tpi | id_words))
    r.report("+ extension permute", t0)

    if not a.no_enumerate:
        # tpi names seed BOTH the directory list and the observed number
        # patterns, so a DatPkg client's numbering conventions get brute-forced
        # in the target archives even when the exact paths differ.
        known = set(loose) | set(tpi) | set(id_words) | set(r.found.values())
        dirs = sorted({n[:n.rfind("/")] for n in known if "/" in n})
        pats = build_patterns(known)
        # patterns common enough to be worth trying in EVERY directory
        glob_pats = Counter()
        for d, ps in pats.items():
            for p in ps:
                glob_pats[p] += 1
        # Universal patterns get tried in EVERY directory, so they must stay
        # narrow (<= 4 digits) no matter how wide --max-digits opens per-dir
        # enumeration: a 6-digit "universal" would be 10**6 * len(dirs) hashes.
        # Wide numeric series (mesh/texture ids) are dir-specific, not universal.
        universal = [p for p, c in glob_pats.most_common(12)
                     if p[1] <= min(a.max_digits, 4) and c >= 3]
        print(f"dirs={len(dirs)} universal patterns={universal}", flush=True)

        # per-directory: its own observed patterns, then the universal ones
        for idx, d in enumerate(dirs):
            todo = set(pats.get(d, ())) | set(universal)
            for (lit, nd, ext) in todo:
                if nd > a.max_digits:
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
    rejected_total = 0
    for name, ar in r.arcs.items():
        stem = name.split(".")[0]
        mine = {e.hash: r.found[e.hash] for e in ar if e.hash in r.found}
        # consistency check: extension vs detected payload magic.  A name whose
        # extension disagrees with the payload's magic is either a birthday
        # collision from pattern enumeration or a real file with a misleading
        # extension.  For an ENUMERATED name we treat the mismatch as proof of a
        # false positive and drop it -- the string was never observed, so there
        # is nothing to vouch for it.  A DICTIONARY name came from a real string
        # on disk, so we keep it and merely flag the mismatch.
        suspect, rejected = [], []
        for e in ar:
            nm = mine.get(e.hash)
            if not nm:
                continue
            ext = nm[nm.rfind("."):].lower() if "." in nm else ""
            want_magic = EXT_MAGIC.get(ext)
            got = detect_magic(ar.peek(e, 32))[0]
            if want_magic and got != want_magic:
                rec = {"hash": f"{e.hash:08x}", "name": nm,
                       "detected": got, "expected": want_magic,
                       "origin": r.origin.get(e.hash, "?")}
                if r.origin.get(e.hash) == "enum":
                    rejected.append(rec)
                else:
                    suspect.append(rec)
        for rec in rejected:
            del mine[int(rec["hash"], 16)]
        suspect_total += len(suspect)
        rejected_total += len(rejected)
        if rejected:
            (outdir / f"{stem}_rejected_names.json").write_text(
                json.dumps(rejected, indent=1), encoding="utf-8")
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
            "enum_false_positives_dropped": len(rejected),
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
        "enum_false_positives_dropped": rejected_total,
        "note": ("Names marked from_observed_strings came from real strings found "
                 "on disk and are effectively certain. Names from "
                 "from_pattern_enumeration carry the birthday risk above, spread "
                 "across that subset; enumerated names whose extension disagreed "
                 "with the payload magic were dropped (see *_rejected_names.json) "
                 "and are NOT counted in names_recovered."),
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
