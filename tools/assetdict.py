#!/usr/bin/env python3
r"""
assetdict.py -- a per-install dictionary of WHAT EACH CLIENT CONTAINS, so
"which installs carry this file?" stops costing an archive walk.

    py -3 tools/assetdict.py --build            # build/refresh every install
    py -3 tools/assetdict.py --has c3/0003/000/010.c3
    py -3 tools/assetdict.py --stats

A real motion path is `c3/0003/000/010.c3` -- shape, weapon set, action.
The first draft of this usage line invented `c3/motion/011002100.c3`,
which no install carries, so copying it returned NO and taught the wrong
shape.

THE DEFECT THIS EXISTS TO FIX
------------------------------
`motionsource.sources_for` cached its answer **per loadout**. But what an
install contains does not depend on the loadout, so every new body+weapon
combination re-opened all 45 installs to re-derive facts that had not
changed. The cache was keyed on the wrong thing: it could never amortise,
and a first look at any new composition paid the full cold cost forever.

This keys the cache on the INSTALL instead, which is the thing the facts
actually belong to. One build serves every loadout, present and future.

WHY THIS IS CHEAP, MEASURED 2026-09-22
---------------------------------------
A WDF index stores `tq_hash(name)` natively (`core/wdf.py`, the `h` of each
16-byte entry), so collecting a WDF install's hash set needs **no hashing at
all**. Only TPD indices store plaintext and must be hashed once:

    install                     open_s   hash_s    entries
    6090      (2x WDF)            0.02     0.00      25,013
    CCO       (2x WDF)            0.06     0.01      24,757
    4274      (2x WDF)            0.03     0.01      19,017
    7878      (4x TPD)            0.35     3.61     146,196
    Zephyr    (TPD + 5 WDF)       0.55     3.17     144,583

41 of the 45 installs on this box are the free case. Loose trees are walked
too -- they are not optional, 6090 alone has 54,217 loose files and an
archives-only dictionary would under-report every one of them -- and that
walk measured 0.12-1.04 s per install.

WHAT A LOOKUP IS AND IS NOT
----------------------------
The key is `tq_hash`, a **32-bit** hash, because on a WDF install that is
the only identity the format keeps -- 331 of the official archive's 24,757
names were never recovered. So:

  * A **NO is exact.** If an install held the file, its hash would be in the
    set. There are no false negatives.
  * A **YES is a candidate.** Two paths can share a 32-bit hash. Across the
    ~150k entries of the largest install the chance a given query collides is
    about 0.0035%, so a 3,700-query sweep expects ~0.1 false positives.

`which(..., verify=True)` re-checks each YES against `AssetRoot.exists`. It
is **off by default, and that is a measurement, not a preference.** An
earlier draft of this file defaulted it on and claimed it "costs a few
seconds, not the sweep". MEASURED 2026-09-22, that is false: constructing an
AssetRoot averaged ~3.3 s and a real query's hits span 20+ installs, so
verification cost 60-130 s -- it would reintroduce the entire cost this
tool exists to remove.

**`root_for` now opens BARE roots, which is 32-77x cheaper per install**
(MEASURED 2026-09-25: 7867 9.99 s default vs 0.31 s bare; 5517 0.70 s vs
0.009 s). Warm, all 45 bare opens together cost **5.5 s** -- median 0.011 s,
slowest 0.38 s.

**But the dominant term is the OS FILE CACHE, not the constructor, and it is
worth stating because it is the number a user feels.** A `verify=True` for
one path that 26 installs claim measured **269 s** on the first touch of the
night and 5.5 s once those archive indices were resident. So verification is
seconds on a warm box and minutes on a cold one, and the honest form of the
claim is that number pair, not either half of it.

It stays OFF by default regardless, because the asymmetry is the design and
not the cost: a NO is exact and needs no confirming, so `--verify` buys
precision only on the ~0.0035% of YESes that could be a collision.

STALENESS
---------
The signature covers each archive's (name, size, mtime). A change to an
install's LOOSE tree is NOT detected. That is sound here only because
`ConquerAssets/Clients` is read-only to every seat by standing rule -- it is
stated rather than hidden, and `--build` / `refresh=True` rebuilds
unconditionally for when that rule stops holding or an install is added.

Nothing here writes to any install.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import coassets                                              # noqa: E402
import coroot                                                # noqa: E402
from tqhash import tq_hash                                   # noqa: E402

#: Bumped when the on-disk layout changes, so an old cache is rebuilt rather
#: than misread. A format change that kept the magic would be read as data.
MAGIC = b"CODICT\x02\x00"


def _default_cache_dir() -> Path:
    """Where the dictionaries live: SHARED across checkouts when the asset
    collection has a `derived/` folder, this checkout's `out/` otherwise.

    The rule `coroot.thumbs_base` applies to thumbnails, for the same reason:
    the artefact is expensive, keyed on the INSTALL, and identical from every
    worktree, so a per-checkout copy meant every fresh viewer paid the walk
    again. MEASURED 2026-09-29 in the owner's viewer, served from a fresh
    tree: no checkout on the box had an `out/assetdict/`, and the first
    `/api/motionsources` after launch started the 45-install walk from
    nothing (`_BUILD_LOCK` records what that walk did to the process). A
    shared folder means one box pays once.

    `MAGIC` still guards the layout, so a checkout that changes the format
    rebuilds rather than misreads a neighbour's file, and `save` writes
    through a per-process temp name so two viewers saving the same install
    cannot rename each other's half-written file into place.
    """
    try:
        derived = coroot.assets_dir() / "derived"
        if derived.is_dir():
            return derived / "assetdict"
    except Exception:                                        # noqa: BLE001
        pass                          # no asset collection: per-checkout
    return _HERE.parent / "out" / "assetdict"


CACHE_DIR = _default_cache_dir()

#: ONE BUILD AT A TIME, process-wide. `get` takes this around build-and-save
#: and looks at the cache again once it holds it, so a caller that arrives
#: during a build waits for that build and LOADS what it saved instead of
#: walking the install itself.
#:
#: MEASURED 2026-09-29 in the owner's viewer (pid 25712, read-only
#: `sys.remote_exec` thread dumps 20:07-20:11Z): 13 of 16 handler threads
#: were inside `build`'s `os.walk`, the SAME 13 thread ids across dumps 69 s
#: apart, nine of them on one install at once. Each `/api/motionsources` for
#: a new body had started its own walk of all 45 installs, because nothing
#: here told the second caller the first was already doing it. Thirteen
#: copies of one walk contending for the GIL ran ~40 minutes and ~2,360
#: CPU-seconds without finishing; ONE process walking the nine installs they
#: had not reached took 99 s wall. The walk was never slow. The herd was.
#:
#: Process-wide rather than per install on purpose: the cost that compounds
#: is concurrent walking, whichever installs are walked, and `load_all`
#: visits them in one order anyway. Across PROCESSES the atomic `save` is
#: the only guard; two viewers may each build an install once.
_BUILD_LOCK = threading.Lock()

#: Opened roots are reused within a process; `verify` would otherwise re-open
#: the same install once per confirmed hit.
_ROOTS: dict = {}


def root_for(path):
    """The install at `path` as a BARE root, memoised per process.

    **Bare, not the default constructor.** A default `AssetRoot` runs
    `_pick_profile` -> `profile_match` -> `_qualifies_for_uncached` for every
    install whose name is not in `ASSET_PROFILES`, which builds `part_tables`,
    `_c3_names` and `_hurdle_rate` on two throwaway roots and opens the donor
    installs. MEASURED 2026-09-25 on this box, cold: 7867 costs **9.99 s**
    that way and **0.31 s** bare (32x); 5517 **0.70 s** against **0.009 s**
    (77x). A warm re-run of the same pair earlier the same night read
    4.94/0.33 and 0.296/0.011 -- the ratio is the durable part, the absolute
    numbers move with the OS file cache.

    And bare is not merely cheaper, it is the CORRECT object for this
    question. `bare().locate` is loose-then-own-archives -- exactly the
    population `build()` indexes -- while a profiled root also answers out of
    an overlay this dictionary never saw, so `verify=True` through a profiled
    root can confirm a hit that the dictionary could not have produced.
    `AssetRoot.bare`'s own docstring says to use it for any survey, census or
    provenance claim.
    """
    key = str(path)
    if key not in _ROOTS:
        try:
            _ROOTS[key] = coassets.AssetRoot.bare(key)
        except Exception:
            _ROOTS[key] = None
    return _ROOTS[key]


#: The old private spelling. `motionsource` carried a byte-identical copy of
#: this function and of `installs` until they were folded into this module.
_asset_root = root_for


def normalise(logical: str) -> str:
    """The form `tq_hash` is taken over, matching `AssetRoot.locate`."""
    return logical.replace(chr(92), "/").lstrip("/").lower()


def installs(base=None) -> list:
    base = str(base or coroot.clients_dir())
    if not base or not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)))


def _signature(root: Path) -> list:
    """(name, size, mtime_ns) per archive, sorted. See STALENESS above."""
    sig = []
    for p in sorted(root.iterdir() if root.is_dir() else []):
        if p.is_file() and p.suffix.lower() in (".wdf", ".tpi", ".tpd"):
            st = p.stat()
            sig.append([p.name.lower(), st.st_size, st.st_mtime_ns])
    return sig


class InstallDict:
    """One install's hash set, plus how it was built."""

    def __init__(self, name, path, hashes, sig, counts):
        self.name = name
        self.path = str(path)
        self.hashes = hashes
        self.signature = sig
        self.counts = counts

    def has(self, logical: str) -> bool:
        return tq_hash(normalise(logical)) in self.hashes

    def __len__(self) -> int:
        return len(self.hashes)

    def __repr__(self) -> str:
        return "<InstallDict %s %d hashes>" % (self.name, len(self.hashes))


def build(install_path, name=None) -> InstallDict:
    """Collect every identity `install_path` can serve, archives and loose."""
    root = Path(install_path)
    name = name or root.name
    hashes: set = set()
    counts = {"archive": 0, "loose": 0, "containers": []}

    # The archives are opened DIRECTLY, not through `AssetRoot`. MEASURED
    # 2026-09-22: constructing 45 AssetRoots costs **149.93 s**, which was
    # essentially the whole of the 190 s this tool exists to remove -- the
    # per-file lookups were only 0.2 s for 10 files across all 45 installs.
    # `AssetRoot.__init__` does overlay discovery, ini parsing and mesh-DBC
    # loading that a containment question does not need. The discovery and
    # reader-selection classmethods ARE reused, so this does not re-derive
    # which files are archives or which reader each takes.
    for p in coassets.AssetRoot._discover_archives(root):
        reader = coassets.AssetRoot._reader_for(p)
        if reader is None:
            continue
        try:
            arc = reader(p)
            ents = list(arc.entries)
        except Exception:
            counts["containers"].append(p.name + ":unreadable")
            continue
        counts["archive"] += len(ents)
        # A WDF entry carries `.hash` and needs no hashing; a TPD entry
        # carries `.name` and does. Decided per CONTAINER, not per install:
        # Zephyr is a TPD pair PLUS five garment WDFs.
        native = bool(ents) and getattr(ents[0], "hash", None) is not None
        counts["containers"].append(
            "%s:%s" % (p.name, "hash" if native else "name"))
        if native:
            for e in ents:
                hashes.add(e.hash)
        else:
            for e in ents:
                hashes.add(tq_hash(normalise(e.name)))
        close = getattr(arc, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass

    # Loose files shadow archives in `locate`, so they are part of what the
    # install can serve and an archives-only set would under-report.
    rootstr = str(root)
    cut = len(rootstr) + 1
    for dirpath, _dirnames, filenames in os.walk(rootstr):
        for fn in filenames:
            rel = normalise(os.path.join(dirpath, fn)[cut:])
            hashes.add(tq_hash(rel))
            counts["loose"] += 1

    return InstallDict(name, root, hashes, _signature(root), counts)


def _cache_file(name: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
    return CACHE_DIR / (safe + ".idx")


def save(d: InstallDict) -> Path:
    """Sorted uint32 array behind a JSON header. Binary because a 150k-entry
    JSON list costs ~1.5 MB and a second to parse, against ~600 KB and
    milliseconds here -- and this is read on every query."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    head = json.dumps({"name": d.name, "path": d.path,
                       "signature": d.signature, "counts": d.counts}
                      ).encode("utf-8")
    arr = array.array("I", sorted(d.hashes))
    if sys.byteorder != "little":                            # pragma: no cover
        arr.byteswap()
    p = _cache_file(d.name)
    # Per process AND thread: the folder is shared (`_default_cache_dir`),
    # so two viewers can save one install at the same moment, and a shared
    # temp name would let one of them rename the other's torn file into
    # place. `load` treats a torn file as a miss, so the cost would be a
    # rebuild rather than a wrong answer, but it is a rebuild for nothing.
    tmp = p.with_name("%s.%d-%d.tmp" % (p.name, os.getpid(),
                                        threading.get_ident()))
    with open(tmp, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<I", len(head)))
        fh.write(head)
        fh.write(struct.pack("<I", len(arr)))
        arr.tofile(fh)
    os.replace(tmp, p)                # atomic: a torn cache reads as garbage
    return p


def load(name: str):
    p = _cache_file(name)
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as fh:
            if fh.read(len(MAGIC)) != MAGIC:
                return None
            (hlen,) = struct.unpack("<I", fh.read(4))
            head = json.loads(fh.read(hlen).decode("utf-8"))
            (n,) = struct.unpack("<I", fh.read(4))
            arr = array.array("I")
            arr.fromfile(fh, n)
        if sys.byteorder != "little":                        # pragma: no cover
            arr.byteswap()
        return InstallDict(head["name"], head["path"], set(arr),
                           head.get("signature", []), head.get("counts", {}))
    except Exception:
        return None                   # a corrupt cache is rebuilt, not fatal


def warm(install_path):
    """The cached dictionary for one install if it is current, else None.
    Never builds -- this is the question "would `get` have to walk?"."""
    root = Path(install_path)
    d = load(root.name)
    if d is not None and d.signature == _signature(root):
        return d
    return None


def get(install_path, *, refresh: bool = False):
    """The cached dictionary for one install, building it if needed.

    A miss builds under `_BUILD_LOCK` and looks at the cache AGAIN once it
    holds the lock: the second caller for a cold install finds the first
    caller's file and never walks. `refresh` skips both looks -- it means
    "walk this install now", and a refresh that found a fresh file and
    returned it would not have refreshed anything.
    """
    root = Path(install_path)
    name = root.name
    if not refresh:
        d = warm(root)
        if d is not None:
            return d
    with _BUILD_LOCK:
        if not refresh:
            d = warm(root)            # built while we waited for the lock
            if d is not None:
                return d
        d = build(root, name)
        try:
            save(d)
        except Exception:                                    # pragma: no cover
            pass                      # an unwritable cache must not fail a read
    return d


def load_all(base=None, *, refresh: bool = False, progress=None) -> dict:
    base = str(base or coroot.clients_dir())
    out = {}
    names = installs(base)
    for i, n in enumerate(names):
        if progress:
            progress(n, i, len(names))
        out[n] = get(os.path.join(base, n), refresh=refresh)
    return out


def which(paths, base=None, *, refresh: bool = False, verify: bool = False,
          dicts=None, progress=None) -> dict:
    """For each logical path, the installs that carry it.

    A NO from the dictionary is exact, so only a YES can be wrong, and only
    by a 32-bit collision (~0.0035% per query per install). `verify` is OFF
    by default because a NO needs no confirming; a hit is re-opened through
    `root_for`, a BARE root at ~0.01-0.38 s warm (MEASURED 2026-09-25), not
    the ~3.3 s a profiled one cost -- though a cold file cache dominates
    both, see the module docstring. See "WHAT A LOOKUP
    IS AND IS NOT" for why the asymmetry is the whole design.
    """
    base = str(base or coroot.clients_dir())
    dicts = dicts if dicts is not None else load_all(
        base, refresh=refresh, progress=progress)
    out = {}
    for p in paths:
        key = tq_hash(normalise(p))
        hit = [n for n, d in dicts.items() if d is not None and key in d.hashes]
        if verify and hit:
            confirmed = []
            for n in hit:
                ar = _asset_root(os.path.join(base, n))
                if ar is None:
                    # THE ROOT WOULD NOT OPEN, so verification cannot
                    # adjudicate -- and an instrument that cannot say yes
                    # must not be read as saying no. An earlier version
                    # dropped the hit here, which turned "these installs
                    # carry it" into "none do" for every install whose root
                    # failed to construct: a fast wrong answer, which is the
                    # one thing this tool must not produce. The dictionary's
                    # own finding stands when nothing can overturn it.
                    confirmed.append(n)
                elif ar.exists(p):
                    confirmed.append(n)
            hit = confirmed
        out[p] = hit
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Per-install dictionary of what each client contains.")
    ap.add_argument("--base", default=None)
    ap.add_argument("--build", action="store_true",
                    help="rebuild every install's dictionary")
    ap.add_argument("--has", metavar="LOGICAL",
                    help="which installs carry this path")
    ap.add_argument("--verify", action="store_true",
                    help="confirm each hit against a bare AssetRoot "
                         "(~0.01-0.3 s per install that claimed one; "
                         "off by default)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv)

    base = str(args.base or coroot.clients_dir())
    if not os.path.isdir(base):
        print("no clients directory: %s" % base, file=sys.stderr)
        return 2

    def progress(n, i, total):
        print("  [%2d/%2d] %s" % (i + 1, total, n), file=sys.stderr)

    if args.build or args.stats or args.has:
        t0 = time.perf_counter()
        dicts = load_all(base, refresh=args.build,
                         progress=progress if args.build else None)
        el = time.perf_counter() - t0
        ok = [d for d in dicts.values() if d is not None]
        print("%d install(s), %d readable, %.2fs"
              % (len(dicts), len(ok), el))
    else:
        ap.print_help()
        return 0

    if args.stats:
        print()
        print("%-28s %10s %10s %10s" % ("install", "hashes", "archive", "loose"))
        for n, d in dicts.items():
            if d is None:
                print("%-28s  UNREADABLE" % n)
                continue
            c = d.counts or {}
            print("%-28s %10d %10d %10d"
                  % (n, len(d), c.get("archive", 0), c.get("loose", 0)))

    if args.has:
        res = which([args.has], base, verify=args.verify, dicts=dicts)
        got = res[args.has]
        print()
        print("%s" % args.has)
        if got:
            print("  carried by %d install(s): %s" % (len(got), ", ".join(got)))
        else:
            print("  carried by NO install on this box "
                  "(a dictionary NO is exact)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
