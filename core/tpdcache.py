#!/usr/bin/env python3
r"""tpdcache.py -- keep inflated `.tpd` entries on disk, per client, opt-in.

    import tpdcache
    tpdcache.set_enabled(root, True)          # the owner's per-client switch
    tpdcache.attach(asset_root)               # AssetRoot's TPD reads use it
    tpdcache.usage(root)                      # {"files": n, "bytes": b, ...}
    tpdcache.clear(root)

WHY, MEASURED
-------------
The load harness at master df9f7835 (2026-09-18) timed `cat.read` per texture:

    7878  median 6.09 ms      6609  7.79 ms      6090  6.23 ms
    everything else 0.47-1.4 ms

**ONLY 7878 OF THOSE THREE IS A DATPKG CLIENT.** 6609 and 6090 ship `c3.wdf` /
`data.wdf` and no `.tpd` at all (checked on disk 2026-09-19; `/api/tpdcache`
reports `hasTpd: false` for both), so their 6-8 ms is NOT an inflate and this
cache cannot touch it -- what it is remains unmeasured. The first version of
this docstring, the Settings text and the PR all said otherwise; the harness
labelled the column "incl .tpd inflate" for every client and I carried the
label. The installs this cache can help are the DatPkg ones: 7878 and Zephyr.

On 7878, ~6 ms x 500 textures is ~3 s of a big map's load spent re-inflating
bytes that were inflated identically last time -- IF the 6 ms is the inflate,
which the step-1 split exists to establish. DXT decode is already gone (the texture
bundle strips a header, 0.00 ms), and zlib cannot be inflated on the GPU, so
the only way to stop paying for the inflate is not to do it twice: keep the
inflated bytes on disk. **Whether a disk read beats the inflate on this
machine is a MEASUREMENT this module does not claim** -- that is step 1 of the
owner's plan, and it is why the cache is OFF until someone turns it on.

OFF BY DEFAULT, AND PER CLIENT
------------------------------
It costs disk -- roughly the inflated size of every texture read, gigabytes
for a whole client -- so nobody gets it without asking, and asking is per
install (`set_enabled(root, ...)`), because the cost differs ~30x between
clients and one of them may be the only one worth it. The switch lives in the
per-user config beside `thumbnail_paths`, keyed by the same normalised root
spelling (`coroot._root_key`), so one install spelled three ways is one switch.

THE KEY IS THE STORED BYTES
---------------------------
An entry is keyed by blake2b over the entry's COMPRESSED bytes plus its flag
and inflated size. Hashing the stored bytes costs a fraction of inflating
them, and it is the property that matters for a client that gets patched: a
patch that changes a texture changes its stored bytes, so the old inflate is
never served for it -- it lands on a different key and misses. A texture the
patch did not touch keeps its key and its entry. No path, size or mtime is in
the key, so nothing about WHERE an entry sits can make it stale.

A hit is checked against the index's inflated size before it is served, and a
wrong-sized file is a miss (and is removed): a truncated file from a crash is
the one way a content-keyed store can hold wrong bytes under a right name.

WHERE IT LIVES
--------------
``<assets_dir>/derived/tpdcache/<thumbs_key>/`` when the asset collection has
a ``derived/`` folder -- beside the thumbnails, the other expensive artefact
built from ``Clients/`` and never inside it -- else this checkout's
``out/cache/tpd`` (``out/cache/`` is declared per-install in
`coroot.PER_BASE`). `thumbs_key` and not `base_id`: a live install keeps its
folder across a patch, which is right because every entry inside is
content-addressed already -- the same reasoning `coroot.thumbs_key` gives.

A CACHE MAY ONLY EVER COST AN INFLATE
-------------------------------------
Every failure -- unreadable config, full disk, a file vanishing mid-read --
falls back to inflating from the archive. Nothing here raises into a reader.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coroot                            # noqa: E402

__all__ = ["TpdCache", "SETTINGS_KEY", "REL", "SHARED_DIR", "enabled",
           "set_enabled", "enabled_roots", "cache_dir", "for_root", "attach",
           "usage", "clear", "prepare", "entry_key"]

#: Per-user config key: ``{normalised root: true}`` for every install whose
#: TPD reads are cached. Absent means off.
SETTINGS_KEY = "tpd_cache"
#: The per-checkout fallback location (declared under ``out/cache/`` in
#: `coroot.PER_BASE`).
REL = "out/cache/tpd"
#: The shared location's folder name under ``<assets_dir>/derived``.
SHARED_DIR = "tpdcache"
#: Bumped if the entry file format ever changes; part of every key.
FORMAT = 1


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------

def enabled_roots() -> dict:
    """``{normalised root: True}`` as stored; ``{}`` on any failure."""
    try:
        saved = coroot.read_settings().get(SETTINGS_KEY) or {}
    except Exception:                                     # noqa: BLE001
        return {}
    return dict(saved) if isinstance(saved, dict) else {}


def enabled(root=None) -> bool:
    """Is ``root``'s TPD cache switched on? False whenever it cannot tell."""
    try:
        key = coroot._root_key(root)
    except Exception:                                     # noqa: BLE001
        return False
    return bool(key) and enabled_roots().get(key) is True


def set_enabled(root, on: bool) -> Path:
    """Switch ``root``'s cache on or off. Returns the config file written.

    Off does NOT delete what is on disk (`clear` does): switching a cache off
    to measure without it should not cost the rebuild to switch it back. It
    does detach every live reader for that root in this process, so off is
    off immediately rather than at the next restart.
    """
    key = coroot._root_key(root)
    if not key:
        raise ValueError("no install named, so there is nothing to key the "
                         "TPD cache switch to")
    saved = enabled_roots()
    if on:
        saved[key] = True
    else:
        saved.pop(key, None)
    written = coroot.write_settings(**{SETTINGS_KEY: saved})
    with _LIVE_LOCK:
        for c in _LIVE.get(key, []):
            c.on = bool(on)
    return written


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------

def cache_dir(root=None) -> Path:
    """The folder ``root``'s entries live in. Creates nothing."""
    try:
        derived = coroot.assets_dir() / "derived"
        if derived.is_dir():
            return derived / SHARED_DIR / coroot.thumbs_key(root)
    except Exception:                                     # noqa: BLE001
        pass
    return coroot.derived_path(REL, root)


def entry_key(e, stored: bytes) -> str:
    """The content key: the stored bytes, the flag and the inflated size.

    The flag and size are in it because they decide what `inflate` does with
    the bytes -- the same stored bytes under a different flag are a different
    file -- and FORMAT because a change to the entry layout must miss.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{FORMAT}|{int(e.flag)}|{int(e.uncompressed)}|".encode("ascii"))
    h.update(stored)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

class TpdCache:
    """One install's entries. `on` is live: `set_enabled` flips it in place."""

    def __init__(self, folder: Path, root_key: str = "", on: bool = True):
        self.dir = Path(folder)
        self.root_key = root_key
        self.on = on
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def _path(self, key: str) -> Path:
        # 256 fan-out folders: a whole client is ~100k entries, and one folder
        # that size makes every listing (and Explorer) slow.
        return self.dir / key[:2] / f"{key}.bin"

    def get(self, e, stored: bytes) -> Optional[bytes]:
        if not self.on:
            return None
        p = self._path(entry_key(e, stored))
        try:
            data = p.read_bytes()
        except OSError:
            self.misses += 1
            return None
        if len(data) != int(e.uncompressed):
            # A truncated write under a right name: the one way a content-
            # keyed file can be wrong. Drop it and inflate.
            try:
                p.unlink()
            except OSError:
                pass
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put(self, e, stored: bytes, data: bytes) -> Optional[Path]:
        """Store an inflate. Temp name + rename, so a crash leaves no half
        file under the final name. None when it could not be written."""
        if not self.on or len(data) != int(e.uncompressed):
            return None
        p = self._path(entry_key(e, stored))
        try:
            if p.is_file():
                return p
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(
                f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
            self.writes += 1
            return p
        except OSError:
            return None


#: Every live `TpdCache` by root key, so `set_enabled` can switch readers that
#: are already open. Weakly held would be tidier; a viewer opens a handful of
#: roots in its life, so plain lists are fine.
_LIVE: dict = {}
_LIVE_LOCK = threading.Lock()


def for_root(root=None) -> TpdCache:
    """This process's `TpdCache` for ``root`` (one per root key), with `on`
    set from the switch. Returned even when off, so switching it on later
    reaches readers that are already open."""
    key = ""
    try:
        key = coroot._root_key(root)
    except Exception:                                     # noqa: BLE001
        pass
    with _LIVE_LOCK:
        have = _LIVE.get(key)
        if have:
            have[0].on = enabled(root)
            return have[0]
        c = TpdCache(cache_dir(root), key, on=enabled(root))
        _LIVE.setdefault(key, []).append(c)
        return c


def attach(assets, root=None) -> Optional[TpdCache]:
    """Give every TPD container of ``assets`` (an `coassets.AssetRoot`) the
    cache for its root. Returns the cache, or None if the root holds no TPD.

    A WDF-only install is left alone: its reads never inflate a DatPkg entry,
    so there is nothing to cache and no reason to create a folder.
    """
    use = getattr(assets, "use_tpd_cache", None)
    if use is None:
        return None
    if not getattr(assets, "tpd_containers", lambda: [])():
        return None
    c = for_root(root if root is not None else getattr(assets, "root", None))
    use(c)
    return c


# ---------------------------------------------------------------------------
# the cost, and clearing it
# ---------------------------------------------------------------------------

def usage(root=None) -> dict:
    """``{"dir", "files", "bytes", "enabled"}`` -- the disk this client's cache
    costs, counted from the folder rather than remembered, so a cache someone
    deleted by hand reads as empty rather than as its old size."""
    d = cache_dir(root)
    files = total = 0
    # `scandir`, because on Windows its entries carry the size from the
    # directory listing itself: ~100k entries cost a listing, not 100k stats.
    try:
        with os.scandir(d) as subs:
            for sub in subs:
                if not sub.is_dir():
                    continue
                try:
                    with os.scandir(sub.path) as items:
                        for p in items:
                            if p.name.endswith(".bin"):
                                try:
                                    total += p.stat().st_size
                                    files += 1
                                except OSError:
                                    continue
                except OSError:
                    continue
    except OSError:
        pass
    return {"dir": str(d), "files": files, "bytes": total,
            "enabled": enabled(root)}


def clear(root=None) -> dict:
    """Delete this client's cached entries (and stray temp files). Returns
    ``{"removed": n, "bytes": b}``. The switch is left as it is."""
    d = cache_dir(root)
    n = freed = 0
    try:
        subs = [s for s in d.iterdir() if s.is_dir()]
    except OSError:
        subs = []
    for sub in subs:
        try:
            items = list(sub.iterdir())
        except OSError:
            continue
        for p in items:
            if p.suffix not in (".bin", ".tmp"):
                continue
            try:
                size = p.stat().st_size
                p.unlink()
                n += 1
                freed += size
            except OSError:
                continue
        try:
            sub.rmdir()
        except OSError:
            pass
    return {"removed": n, "bytes": freed}


# ---------------------------------------------------------------------------
# filling it ahead of time
# ---------------------------------------------------------------------------

def prepare(assets, root=None, *, only: Optional[Callable[[str], bool]] = None,
            progress: Optional[Callable[[int, int, str], None]] = None,
            stop: Optional[threading.Event] = None) -> dict:
    """Inflate and store every TPD entry of ``assets`` (or those ``only``
    accepts, by logical name). The "Prepare this client" job.

    Refuses (returns ``{"ran": False, "reason": ...}``) when the switch is off:
    preparing a cache nobody turned on is spending the disk they declined.
    Entries already present are hashed and skipped, not re-inflated, so a
    second run after a patch costs the hashing plus the entries that changed.
    """
    r = root if root is not None else getattr(assets, "root", None)
    if not enabled(r):
        return {"ran": False,
                "reason": "the TPD cache is off for this install; switch it "
                          "on first (it costs disk, so it is never on by "
                          "default)"}
    c = attach(assets, r)
    if c is None:
        return {"ran": False, "reason": "this install has no .tpd archives"}
    work = []
    for cont in assets.tpd_containers():
        for e in cont.entries:
            if e.flag in (1, 2) and (only is None or only(e.name)):
                work.append((cont, e))
    t0 = time.perf_counter()
    done = present = failed = 0
    before = c.writes
    for cont, e in work:
        if stop is not None and stop.is_set():
            break
        try:
            raw = cont.read_compressed(e)
            if c._path(entry_key(e, raw)).is_file():
                present += 1
            else:
                cont.inflate_into_cache(e, raw)
        except Exception:                                 # noqa: BLE001
            failed += 1
        done += 1
        if progress is not None and (done % 256 == 0 or done == len(work)):
            progress(done, len(work), e.name)
    stored = c.writes - before
    return {"ran": True, "entries": len(work), "done": done,
            "stored": stored, "alreadyPresent": present, "failed": failed,
            "stopped": bool(stop is not None and stop.is_set()),
            "seconds": round(time.perf_counter() - t0, 2),
            **{k: v for k, v in usage(r).items() if k in ("files", "bytes")}}
