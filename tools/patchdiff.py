#!/usr/bin/env python3
r"""patchdiff.py -- what one install has that another does not, by id AND by
content.

The owner, 2026-09-18: "show only items that were added on that patch level
... only what's new between 6090 and 6609", then "do steps 2, 3 and 4":

  1. NEW BY ID        (PR #98) -- an appearance id absent from the other
                      install's same table.
  2. CHANGED          -- an id BOTH installs have, whose art differs: the
                      mesh or texture bytes it resolves to are not the same.
  3. NEW BY CONTENT   -- for a table whose ids were RENUMBERED between the
                      two (6090 -> 6609 mounts share no id at all), "new" is a
                      content signature the other install does not have.
  4. FILES            -- the file browser: a path the other install does not
                      ship (new), or ships with different bytes (changed).

HOW CONTENT IS COMPARED. A file's key is (size, blake2b-8 of its bytes) --
the bytes as the asset layer reads them, so a loose file and an archived one
with the same content compare equal. An appearance's signature is the tuple
of its parts' (mesh key, texture key). BOTH SIDES RESOLVE IDS THROUGH THE SAME
READER, `AssetRoot.resolve_asset`: comparing one install's `Catalog` answer
with another's `AssetRoot` answer would report the difference between two
resolvers as a difference between two patches.

WHY A SEPARATE `AssetRoot` PER SIDE, INCLUDING "HERE". The viewer's request
threads read through the active install's asset root; a comparison job reads
thousands of files on its own thread, and an archive reader's file handle is
one seek position shared by everyone holding it. So each `PatchDiff` opens
its own. And the other side is NEVER a `Catalog`: building one moves the
process's active root (`coviewer._fx_registry` says why).

UNKNOWN IS NOT "SAME". An appearance none of whose parts resolves to a file
has no content to compare; it is counted as `unknown`, never called new or
unchanged.

Content comparison reads files, so it runs as a background `Job` with a
progress count; callers poll `state()`.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Callable, Iterable, Optional

#: Modes a caller may ask for.
MODES = ("new", "changed", "both")


class Side:
    """One install, read the way the comparison needs it. Caches every answer:
    a file's key never changes during one comparison."""

    def __init__(self, assets, label: str = ""):
        self.assets = assets
        self.label = label
        self._keys: dict = {}
        self._res: dict = {}
        self._tables = None
        self._lock = threading.Lock()

    def tables(self) -> dict:
        if self._tables is None:
            self._tables = self.assets.part_tables()
        return self._tables

    def exists(self, path: str) -> bool:
        try:
            return self.assets.locate(path) is not None
        except Exception:                                  # noqa: BLE001
            return False

    def key(self, path: Optional[str]):
        """(size, content hash) of `path`, or None if it cannot be read."""
        if not path:
            return None
        k = path.lower()
        if k in self._keys:
            return self._keys[k]
        try:
            # Serialised: two jobs (the builder's and the file browser's) may
            # share this side, and an archive reader is one file position.
            with self._lock:
                data = self.assets.read(path)
            val = (len(data), hashlib.blake2b(data, digest_size=8).hexdigest())
        except Exception:                                  # noqa: BLE001
            val = None
        self._keys[k] = val
        return val

    def resolve(self, asset_id: str, kind: str) -> Optional[str]:
        if not asset_id or asset_id == "0":
            return None
        k = (asset_id, kind)
        if k not in self._res:
            try:
                loc = self.assets.resolve_asset(asset_id, kind)
                self._res[k] = loc.logical if loc is not None else None
            except Exception:                              # noqa: BLE001
                self._res[k] = None
        return self._res[k]

    def signature(self, app) -> Optional[tuple]:
        """The content of an appearance, or None when NONE of it resolves."""
        sig = tuple((self.key(self.resolve(p.mesh, "mesh")),
                     self.key(self.resolve(p.texture, "texture")))
                    for p in app.parts)
        if not any(m or t for m, t in sig):
            return None
        return sig


class Job:
    """A background comparison: `done` of `total` items, then `result`."""

    def __init__(self, total: int, fn: Callable[["Job"], dict]):
        self.total = total
        self.done = 0
        self.result: Optional[dict] = None
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self._t = threading.Thread(target=self._run, args=(fn,), daemon=True,
                                   name="patchdiff")
        self._t.start()

    def _run(self, fn):
        try:
            self.result = fn(self)
        except Exception as e:                             # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        self.finished = time.time()

    def state(self) -> dict:
        if self.error:
            return {"state": "failed", "error": self.error}
        if self.result is None:
            return {"state": "computing", "done": self.done, "total": self.total,
                    "elapsed": round(time.time() - self.started, 1)}
        return {"state": "ready", "elapsed": round(self.finished - self.started, 1)}


class PatchDiff:
    """`here` vs `other`, both `AssetRoot`-shaped. One per (here, other) pair;
    jobs are kept so a repeated question is answered from the finished one."""

    def __init__(self, here_assets, other_assets, other_label: str = ""):
        self.here = Side(here_assets, "here")
        self.other = Side(other_assets, other_label)
        self.label = other_label
        self._jobs: dict = {}
        self._lock = threading.Lock()

    # -- jobs ----------------------------------------------------------------
    def _job(self, key, total: int, fn) -> Job:
        with self._lock:
            j = self._jobs.get(key)
            if j is None or j.error:
                j = Job(total, fn)
                self._jobs[key] = j
            return j

    # -- the builder ---------------------------------------------------------
    def options(self, slot: str, idents: Iterable[str], mode: str) -> dict:
        """Which of `idents` (this install's appearances in `slot`) are new,
        changed, or either, relative to the other install.

        Returns `{comparable, how, state, ...}` and, when ready, `keep` (the
        idents to show) plus counts. `how` is "id" or "content"."""
        if mode not in MODES:
            return {"comparable": False, "why": f"unknown mode {mode!r}"}
        idents = list(idents)
        theirs_tab = self.other.tables().get(slot)
        if theirs_tab is None:
            return {"comparable": False,
                    "why": f"{self.label} has no {slot} table, so nothing in "
                           f"it can be called new or changed relative to it"}
        theirs = {a.ident: a for a in theirs_tab}
        by_id = bool(set(idents) & set(theirs))

        if not by_id:
            # STEP 3 -- the ids were renumbered: compare by CONTENT.
            if mode == "changed":
                return {"comparable": False, "how": "content",
                        "why": f"the {slot} table shares no id with "
                               f"{self.label}'s (renumbered), so 'changed' -- "
                               f"the same id with different art -- cannot be "
                               f"asked; 'new' is answered by content instead"}
            key = ("opts-content", slot, len(idents), hash(tuple(idents)))
            here_tab = self.here.tables().get(slot)
            here = {a.ident: a for a in (here_tab or [])}

            def run(job: Job) -> dict:
                sigs = set()
                for a in theirs.values():
                    s = self.other.signature(a)
                    if s is not None:
                        sigs.add(s)
                    job.done += 1
                keep, unknown = [], 0
                for i in idents:
                    a = here.get(i)
                    s = self.here.signature(a) if a is not None else None
                    if s is None:
                        unknown += 1
                    elif s not in sigs:
                        keep.append(i)
                    job.done += 1
                return {"keep": keep, "unknown": unknown}
            j = self._job(key, len(theirs) + len(idents), run)
            out = {"comparable": True, "how": "content", **j.state()}
            if j.result is not None:
                out.update(j.result)
            return out

        # STEP 1 / 2 -- the ids line up.
        new_ids = [i for i in idents if i not in theirs]
        if mode == "new":
            return {"comparable": True, "how": "id", "state": "ready",
                    "keep": new_ids, "unknown": 0}
        both = [i for i in idents if i in theirs]
        key = ("opts-changed", slot, len(both), hash(tuple(both)))
        here_tab = self.here.tables().get(slot)
        here = {a.ident: a for a in (here_tab or [])}

        def run(job: Job) -> dict:
            changed, unknown = [], 0
            for i in both:
                a, b = here.get(i), theirs.get(i)
                sa = self.here.signature(a) if a is not None else None
                sb = self.other.signature(b)
                if sa is None or sb is None:
                    unknown += 1
                elif sa != sb:
                    changed.append(i)
                job.done += 1
            return {"changed": changed, "unknown": unknown}
        j = self._job(key, len(both), run)
        out = {"comparable": True, "how": "id", **j.state()}
        if j.result is not None:
            changed = j.result["changed"]
            out["unknown"] = j.result["unknown"]
            out["changedCount"] = len(changed)
            out["newCount"] = len(new_ids)
            out["keep"] = changed if mode == "changed" else new_ids + changed
        return out

    # -- the file browser ----------------------------------------------------
    def files(self, paths: Iterable[str], mode: str) -> dict:
        """Which of `paths` (this install's files) the other install does not
        ship (new), ships with different bytes (changed), or either."""
        if mode not in MODES:
            return {"comparable": False, "why": f"unknown mode {mode!r}"}
        paths = list(paths)
        key = ("files", mode, len(paths), hash(tuple(paths)))

        def run(job: Job) -> dict:
            new, changed, unknown = [], [], 0
            for p in paths:
                if not self.other.exists(p):
                    new.append(p)
                elif mode != "new":
                    a, b = self.here.key(p), self.other.key(p)
                    if a is None or b is None:
                        unknown += 1
                    elif a != b:
                        changed.append(p)
                job.done += 1
            keep = (new if mode == "new" else changed if mode == "changed"
                    else new + changed)
            return {"keep": keep, "newCount": len(new),
                    "changedCount": len(changed), "unknown": unknown}
        j = self._job(key, len(paths), run)
        out = {"comparable": True, "how": "path", **j.state()}
        if j.result is not None:
            out.update(j.result)
        return out
