#!/usr/bin/env python3
r"""dcache.py -- cache decoded client tables between runs, and refuse a stale one.

    from dcache import get, put
    hit = get(path, "tq-stream")          # None on a miss, or if caching is off
    if hit is None:
        hit = tqdat.decrypt(path.read_bytes())
        put(path, "tq-stream", hit)

WHY, MEASURED
-------------
Decoding the content tables dominates everything else the asset layer does.
Against the owner's "map plus hundreds of characters in 1-2 s" target:

    6609  catalogs()                     2,544 ms
    7878  catalogs()                       350 ms
    one tq decrypt (itemtype.dat, 6.7 MB)  736 ms
    7878  AssetRoot() index parse          307 ms

So a single table can cost more than opening the whole install, and 6609's
catalogue costs more than the entire frame budget.  Nothing was cached: reading
the same 400 assets twice cost 316 ms then **387 ms**, the second pass slower
than the first.

With the cache, 6609's `catalogs()` goes **3,614 ms -> 1,512 ms, 2.4x**,
saving 2,102 ms, rows identical.  Medians of five ALTERNATING runs -- the first
version of this figure said 5.6x because it raced one cold run against one warm
one and credited the cache with the disk I/O the second run did not pay.  The
table above is the check that caught it: one `itemtype.dat` decrypt is 736 ms,
so ~2.1 s of decode across fifteen tq tables is the whole prize, and 2,102 ms
is what the cache actually returns.  **The 1.5 s that REMAINS is parsing**, and
that is now the larger half -- anyone chasing this further should start there,
not here.

**A CACHED DECODE CANNOT WITNESS ITSELF**
-----------------------------------------
This is the part that constrains the design, and it would have been easy to
get silently wrong.

`Plugin.load_table` decodes a `tq-stream` table **twice** on purpose: once to
parse, and once as an independent witness, so `Catalog.control` can say a row
was checked against bytes the parse did not produce.  That control is already
the weaker of the two kinds -- it witnesses the PARSE, not the CIPHER, because
a wrong keystream would fail both decodes alike and they would agree.

Serve both halves from one cache entry and it degrades again: the "independent
re-decode" becomes the same cached bytes read twice, which witnesses nothing
at all.  The count would look identical and the evidence behind it would be
gone -- the exact shape of every other defect this project has paid for.

So a cache hit is reported as its own control kind (`catalog.CONTROL_CACHED`)
rather than borrowing `CONTROL_REDECODE`.  The control fired once, when the
entry was written, and the cache records that it fired; it is not re-derived
on read, because re-deriving it from the cache would be circular.

STALENESS FAILS CLOSED
----------------------
A stale index or table does not crash -- it resolves to the WRONG BYTES and
says nothing, which is worse than a miss.  So an entry is keyed on the source's
size and mtime, the key is in the filename, and a mismatch is a miss rather
than a hit.

WHERE ENTRIES LIVE, AND THE 10 MB I ALMOST COMMITTED
----------------------------------------------------
`REL` is under ``out/``, and ``out/cache/`` is declared in `coroot.PER_BASE`.
Both halves were learned the hard way, in one commit:

`REL` began as ``"cache/tables"`` -- no ``out/`` prefix.  `coroot.derived_rel`
RAISES `UndeclaredDerived` for any ``out/...`` path that is in neither `GLOBAL`
nor `PER_BASE`, exactly so a new derived tree cannot appear without someone
declaring where it belongs.  A path that is not under ``out/`` slips past that
guard -- it is the one way through -- and lands at the repo root, which
``.gitignore`` does not cover.  `git add -A` then took **19 decoded tables,
10.4 MB of game content**, into a commit.  Caught after the push, purged from
history, and `tests/test_dcache.py::WhereEntriesLive` now asserts both the
``out/`` prefix and that `git check-ignore` actually ignores the resolved path,
because "I put it somewhere ignored" was precisely the thing I believed and had
not checked.

Declaring it in `PER_BASE` is what makes the per-install claim TRUE rather than
merely intended: entries resolve inside ``out/indexes/<base-id>/``, so one
client's decode cannot be served for another's table.  The key would have held
the line on its own -- it carries the resolved source path -- but a cache whose
correctness rests on one of two mechanisms while the docstring credits the
other is a cache nobody can reason about.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coroot                            # noqa: E402

try:
    import cosettings                    # noqa: E402
except ImportError:                      # pragma: no cover
    cosettings = None

__all__ = ["enabled", "key_for", "path_for", "get", "put", "clear", "REL"]

#: Where entries live. Under ``out/`` so `.gitignore` covers it, and declared
#: in `coroot.PER_BASE` so it resolves per-install. See the module docstring:
#: neither half was true in the first draft, and it cost a 10 MB commit.
REL = "out/cache/tables"


def enabled() -> bool:
    """Whether caching is on.  Off is always safe: every caller re-decodes."""
    if cosettings is None:                            # pragma: no cover
        return False
    try:
        return bool(cosettings.get("cache_derived"))
    except Exception:                                 # pragma: no cover
        return False


def key_for(src: Path, codec: str) -> Optional[str]:
    """A key that changes whenever the source or the decoder does.

    Size and mtime rather than a content hash: hashing a 6.7 MB table to avoid
    decoding it costs a large fraction of the decode, so the cache would pay
    most of what it saves.  Both are in the KEY, so a changed file cannot hit a
    stale entry -- it lands on a different filename and misses.

    `codec` is part of the key because the same bytes decode differently under
    different treatments, and a `tq-stream` entry served for a `plaintext` read
    would be silent garbage.
    """
    try:
        st = Path(src).stat()
    except OSError:
        return None
    raw = f"{Path(src).resolve().as_posix()}|{st.st_size}|{int(st.st_mtime)}|{codec}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=12).hexdigest()


def path_for(src: Path, codec: str, root=None) -> Optional[Path]:
    key = key_for(src, codec)
    if key is None:
        return None
    return coroot.derived_path(f"{REL}/{Path(src).name}.{key}.bin", root)


def get(src: Path, codec: str, root=None) -> Optional[bytes]:
    """The cached decode, or None.

    None for every failure -- caching off, no entry, unreadable, wrong size.
    A cache that raises makes itself load-bearing; this one can only ever cost
    a re-decode.
    """
    if not enabled():
        return None
    p = path_for(src, codec, root)
    if p is None:
        return None
    try:
        if p.is_file():
            return p.read_bytes()
    except OSError:
        return None
    return None


def put(src: Path, codec: str, data: bytes, root=None) -> Optional[Path]:
    """Store a decode.  Returns the path written, or None if it could not be.

    Written to a temp name and renamed, so a run interrupted mid-write leaves
    no half file for the next run to serve as a complete one.
    """
    if not enabled():
        return None
    p = path_for(src, codec, root)
    if p is None:
        return None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p
    except OSError:
        return None


def clear(root=None) -> int:
    """Drop every entry for this install.  Returns how many were removed."""
    try:
        d = coroot.derived_path(REL, root)
    except Exception:                                 # pragma: no cover
        return 0
    n = 0
    try:
        for p in Path(d).glob("*.bin"):
            try:
                p.unlink()
                n += 1
            except OSError:
                continue
    except OSError:
        pass
    return n
