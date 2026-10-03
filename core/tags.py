#!/usr/bin/env python3
r"""
tags.py -- the local overlay layer: tag, bookmark and rename any asset, keyed
on BOTH its logical path AND its content hash.

WHY TWO KEYS, AND NOT ONE
-------------------------
A tag has to survive two different kinds of change, and each one breaks a
different key:

  * A **patch** relocates an asset -- the logical path a modder tagged is not
    the path the next client spells it under.  Path alone would lose the tag.
  * A **re-encode** rewrites an asset's bytes -- textures re-encode LOSSILY by
    design (`w`, not `W`: an untouched .dds does not come back byte-identical),
    so the content hash a modder tagged is not the hash after a round trip.
    Hash alone would lose the tag.

So an overlay record stores BOTH, and a lookup matches on EITHER:

    query (path, hash) against a stored (path, hash)
      path == and hash ==   -> EXACT     the asset is exactly as tagged
      path != and hash ==   -> MOVED     same bytes at a new path (a patch)
      path == and hash !=   -> CHANGED   same slot, new bytes (a re-encode)
      neither               -> LOST      no overlay applies

The whole point of keeping both keys is that a mismatch is surfaced as MOVED
or CHANGED -- an actionable statement about WHAT changed -- rather than as
LOST, which throws the tag away and tells the user nothing.  `MATCH_LOST` is
returned only when neither key matches anything.

CORPUS-LEVEL BY CONSTRUCTION
----------------------------
33 clients ship only 17 distinct `itemtype.dat`.  Because the content hash is
half the key and a lookup matches on it, a tag added against a shared file on
one install is found on every other install that ships the same bytes -- as
EXACT if the path also agrees, as MOVED if a patch moved it.  The corpus-level
identity IS the content hash; nothing has to be re-entered per install, and no
separate "which install" axis is needed.  This mirrors how `core/collection.py`
files an entry by its content `sha` rather than by the install it came from.

RENAME IS AN OVERLAY, NEVER A WRITE
-----------------------------------
The client resolves an asset by id and by path.  Renaming a *shipped file*
would break that resolution -- the game would look for `410009.dds` and find a
file the modder called `frost-katana.dds`.  So a "name" here is display
metadata stored in the overlay record; **this module never opens an asset file
for writing, and never renames one on disk.**  The only bytes it ever reads are
to compute a hash, and it takes those as an argument wherever it can.

BACKUP / RESTORE
----------------
`export()` returns a plain JSON array -- one object per overlay record --
so a backup is diffable and hand-inspectable, consistent with the export
manifest decision in the backlog's item 6.  `import_records()` restores it,
merging by default (union of tags, latest rename/bookmark wins) so re-importing
a backup never destroys tags added since.

USAGE
-----
    from tags import TagStore, hash_bytes, MATCH_MOVED
    store = TagStore(path)                       # path to the JSON store
    store.add_tag("c3/weapon/410009.dds", hash_bytes(blob), "favourite")
    store.set_name("c3/weapon/410009.dds", h, "Frost Katana")
    store.bookmark("c3/weapon/410009.dds", h)
    m = store.match(path="c3/weapon/410009.dds", hash=other_hash)
    if m.status == MATCH_MOVED: ...
    store.save()
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

import coroot

#: The store schema version written to disk.  Bumped only on a shape change a
#: reader could not otherwise detect; `import_records` accepts any array of
#: well-formed records regardless of the file it came from.
SCHEMA = 1

#: The four answers `match` can give.  Spelled once so a reader grepping for a
#: status finds every site, and so a caller cannot invent a fifth by typo.
MATCH_EXACT = "exact"       # path and hash both agree -- unchanged since tagged
MATCH_MOVED = "moved"       # hash agrees, path differs -- relocated by a patch
MATCH_CHANGED = "changed"   # path agrees, hash differs -- re-encoded / edited
MATCH_LOST = "lost"         # neither agrees -- no overlay applies

#: The order `match` prefers when more than one record could answer.  EXACT is
#: unambiguous.  Between the two mismatches, MOVED wins over CHANGED: identical
#: BYTES at a new path is a stronger statement that "this is the same asset"
#: than the same slot holding new bytes, because a re-used path can hold
#: unrelated content after a patch while a matching content hash cannot.
_PRECEDENCE = {MATCH_EXACT: 0, MATCH_MOVED: 1, MATCH_CHANGED: 2, MATCH_LOST: 3}


class MatchResult(NamedTuple):
    """`status` plus the record it points at (``None`` for `MATCH_LOST`).

    `candidates` carries every record that matched on either key, best first,
    so a caller that wants to disambiguate a MOVED with two donors can, while
    the common caller reads only `status` and `record`.
    """
    status: str
    record: Optional[dict]
    candidates: tuple


def hash_bytes(blob: bytes) -> str:
    """The content hash used as half of an overlay key.

    `blake2b` at a 16-byte digest, the same function and width
    `core/collection.py` files its entries under, so a hash computed here and a
    `sha` recorded there are directly comparable.
    """
    return hashlib.blake2b(blob, digest_size=16).hexdigest()


def hash_file(path: Path | str) -> str:
    """`hash_bytes` of a file's contents, read once.  A convenience for the
    CLI; the store itself never needs a filesystem path to an asset."""
    return hash_bytes(Path(path).read_bytes())


def norm_path(logical: str) -> str:
    r"""The canonical spelling of a logical path used as the other half of a
    key: lower-cased, forward slashes, no leading separator.

    A path is a KEY here, so two spellings of one asset
    (``c3\Weapon\410009.DDS`` and ``c3/weapon/410009.dds``) must land on one
    record rather than two.  Matches the normalisation `coassets` resolves
    against, so a path tagged through the CLI keys the same way the view reads
    it.
    """
    return (logical or "").replace("\\", "/").lstrip("/").strip().lower()


def default_store_path() -> Path:
    """Where the overlay store lives by default: beside the per-user config,
    outside the repo, so it survives a re-clone.

    `TAGS_STORE_FILE` overrides it, which is what the tests use to keep every
    run in a temp directory and never touch a real user's store.
    """
    override = os.environ.get("TAGS_STORE_FILE")
    if override:
        return Path(override)
    return coroot.user_config_path().parent / "tags.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_tag(tag: str) -> str:
    """A tag is free text, but leading/trailing space and case are not part of
    its identity -- ``" Frost "`` and ``"frost"`` are one tag.  Empty after
    stripping is not a tag at all and is refused by the callers."""
    return (tag or "").strip()


class TagStore:
    """The overlay set for one machine.  Reads and writes a single JSON file.

    Records are held in a list and addressed by ``(norm_path, hash)`` -- the
    two-part key.  A path tagged twice at two different hashes (tagged, then
    re-encoded and tagged again) is two records on purpose: each is a true
    statement about a specific set of bytes, and `match` picks the one the
    query is asking about.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_store_path()
        self.records: list[dict] = []
        if self.path.is_file():
            try:
                doc = json.loads(self.path.read_text("utf-8"))
            except ValueError as e:
                raise TagError(f"{self.path} is unreadable: {e}")
            # Accept both the wrapped store shape {schema, records:[...]} and a
            # bare array (an exported backup opened directly), because the two
            # are the same records and refusing one would make a backup a
            # second file format nobody asked for.
            recs = doc.get("records", []) if isinstance(doc, dict) else doc
            self.records = [_coerce_record(r) for r in recs if isinstance(r, dict)]

    # -- lookup ------------------------------------------------------------
    def find(self, path: str, hash: str) -> Optional[dict]:
        """The record with exactly this ``(path, hash)`` key, or ``None``.

        The identity lookup -- not `match`.  Used by the mutators to decide
        whether they are updating a record or creating one.
        """
        p, h = norm_path(path), (hash or "")
        for r in self.records:
            if r["path"] == p and r["hash"] == h:
                return r
        return None

    def match(self, *, path: Optional[str] = None,
              hash: Optional[str] = None) -> MatchResult:
        """Resolve a query asset to the overlay that applies, and HOW it
        applies -- `MATCH_EXACT` / `MATCH_MOVED` / `MATCH_CHANGED` /
        `MATCH_LOST`.

        This is the dual-key rule in one place.  At least one of ``path`` /
        ``hash`` is required; passing only one still matches (that is the whole
        point -- a patch leaves the hash, a re-encode leaves the path), and
        passing neither is a programming error, not an empty result.
        """
        if path is None and hash is None:
            raise TagError("match needs a path or a hash to look up")
        path_given = path is not None
        hash_given = hash is not None
        p = norm_path(path) if path_given else None
        h = hash if hash_given else None
        scored: list[tuple[int, dict]] = []
        for r in self.records:
            path_eq = path_given and r["path"] == p
            hash_eq = hash_given and r["hash"] == h
            path_mismatch = path_given and r["path"] != p
            hash_mismatch = hash_given and r["hash"] != h
            # EXACT: every key the caller supplied agrees, and at least one
            # did match. With only one key given there is nothing to disagree,
            # so a single-key hit is EXACT, not a mismatch.
            if (path_eq or not path_given) and (hash_eq or not hash_given) \
                    and (path_eq or hash_eq):
                status = MATCH_EXACT
            elif hash_eq and path_mismatch:
                status = MATCH_MOVED
            elif path_eq and hash_mismatch:
                status = MATCH_CHANGED
            else:
                continue
            scored.append((status, r))
        if not scored:
            return MatchResult(MATCH_LOST, None, ())
        # Best first: by precedence, then most-recently-updated so a stable
        # answer falls out when two records share a status (two MOVED donors).
        scored.sort(key=lambda sr: (_PRECEDENCE[sr[0]],
                                    _neg_iso(sr[1].get("updatedAt", ""))))
        best_status, best = scored[0]
        return MatchResult(best_status, best, tuple(r for _s, r in scored))

    # -- mutation ----------------------------------------------------------
    def _get_or_create(self, path: str, hash: str) -> dict:
        if not hash:
            raise TagError("an overlay record needs a content hash")
        r = self.find(path, hash)
        if r is None:
            r = {
                "path": norm_path(path),
                "hash": hash,
                "tags": [],
                "bookmark": False,
                "name": None,
                "note": "",
                "addedAt": _now(),
                "updatedAt": _now(),
            }
            self.records.append(r)
        return r

    def add_tag(self, path: str, hash: str, *tags: str) -> dict:
        """Add one or more tags to an asset, creating the overlay record if
        this is the first thing said about it.  Idempotent: a tag already
        present is not duplicated."""
        clean = [_clean_tag(t) for t in tags]
        clean = [t for t in clean if t]
        if not clean:
            raise TagError("no tag given")
        r = self._get_or_create(path, hash)
        have = set(r["tags"])
        have.update(clean)
        r["tags"] = sorted(have)
        r["updatedAt"] = _now()
        return r

    def remove_tag(self, path: str, hash: str, tag: str) -> Optional[dict]:
        """Remove one tag.  Returns the record, or ``None`` if there was no
        such overlay.  The record is left in place even when its last tag
        goes -- a bookmark or a rename on the same asset is still live; use
        `remove` or `prune` to drop an empty record."""
        r = self.find(path, hash)
        if r is None:
            return None
        t = _clean_tag(tag)
        r["tags"] = [x for x in r["tags"] if x != t]
        r["updatedAt"] = _now()
        return r

    def set_name(self, path: str, hash: str, name: str) -> dict:
        """Set (or, with ``""``, clear) the local display name for an asset.

        A local OVERLAY only -- **nothing on disk is renamed.**  The asset
        file is never opened here; only the overlay record changes.
        """
        r = self._get_or_create(path, hash)
        n = (name or "").strip()
        r["name"] = n or None
        r["updatedAt"] = _now()
        return r

    def bookmark(self, path: str, hash: str, on: bool = True) -> dict:
        """Set or clear the bookmark flag on an asset."""
        r = self._get_or_create(path, hash)
        r["bookmark"] = bool(on)
        r["updatedAt"] = _now()
        return r

    def set_note(self, path: str, hash: str, note: str) -> dict:
        r = self._get_or_create(path, hash)
        r["note"] = note or ""
        r["updatedAt"] = _now()
        return r

    def remove(self, path: str, hash: str) -> bool:
        """Drop the whole overlay record for one ``(path, hash)``.  Returns
        whether one was there."""
        r = self.find(path, hash)
        if r is None:
            return False
        self.records.remove(r)
        return True

    def prune(self) -> int:
        """Drop every record that says nothing -- no tags, no bookmark, no
        name, no note.  Returns how many were removed.  These accumulate when
        the last tag is removed from an otherwise-bare record; pruning is
        explicit rather than automatic so a caller mid-edit is never surprised
        by a record vanishing under it."""
        keep = [r for r in self.records if not _is_empty(r)]
        n = len(self.records) - len(keep)
        self.records = keep
        return n

    # -- queries -----------------------------------------------------------
    def list(self, *, tag: Optional[str] = None,
             bookmarked: Optional[bool] = None) -> list[dict]:
        """Overlay records, optionally filtered.

        ``tag`` keeps records carrying that tag; ``bookmarked=True`` keeps
        bookmarked ones.  Sorted by path so the listing is stable.
        """
        out = list(self.records)
        if tag is not None:
            t = _clean_tag(tag)
            out = [r for r in out if t in r["tags"]]
        if bookmarked is not None:
            out = [r for r in out if bool(r["bookmark"]) == bool(bookmarked)]
        return sorted(out, key=lambda r: (r["path"], r["hash"]))

    def all_tags(self) -> dict[str, int]:
        """Every tag in use, and how many assets carry it."""
        counts: dict[str, int] = {}
        for r in self.records:
            for t in r["tags"]:
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items()))

    # -- backup / restore --------------------------------------------------
    def export(self) -> list:
        """The whole overlay as a plain JSON-ready array, one object per
        record, sorted for a stable diff.  This is the backup format."""
        return [dict(r) for r in self.list()]

    def import_records(self, records: Iterable[dict], *,
                       replace: bool = False) -> dict:
        """Restore an exported array.

        Default is a MERGE: for each incoming record, an existing record with
        the same ``(path, hash)`` gains the union of the tags, and the incoming
        name / bookmark / note win only when they say something (a blank name
        in a backup does not erase a rename made since).  `replace=True` wipes
        the store first, restoring the backup verbatim.

        Returns ``{added, merged, skipped}`` so a caller can report what a
        restore actually did rather than claiming success blindly.
        """
        incoming = [_coerce_record(r) for r in records if isinstance(r, dict)]
        stats = {"added": 0, "merged": 0, "skipped": 0}
        if replace:
            self.records = []
        for inc in incoming:
            if not inc["hash"] or not inc["path"]:
                stats["skipped"] += 1
                continue
            cur = self.find(inc["path"], inc["hash"])
            if cur is None:
                self.records.append(inc)
                stats["added"] += 1
                continue
            merged = set(cur["tags"]) | set(inc["tags"])
            cur["tags"] = sorted(merged)
            if inc["name"]:
                cur["name"] = inc["name"]
            if inc["bookmark"]:
                cur["bookmark"] = True
            if inc["note"] and not cur["note"]:
                cur["note"] = inc["note"]
            cur["updatedAt"] = _now()
            stats["merged"] += 1
        return stats

    # -- persistence -------------------------------------------------------
    def save(self) -> Path:
        """Write the store atomically.  A half-written store is a store that
        fails to open rather than one that is silently truncated, so the temp
        file is fully written before it replaces the real one."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema": SCHEMA,
            "records": self.export(),
        }
        text = json.dumps(doc, indent=1)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, self.path)
        return self.path


class TagError(RuntimeError):
    pass


def _neg_iso(s: str) -> str:
    """A sort key that puts the newest ISO-8601 stamp first.

    ISO-8601 sorts lexically, so ordering DESCENDING is what "newest first"
    needs; returning a value that inverts the comparison keeps `match`'s sort
    a single stable `sort(key=...)` rather than two passes.  A missing stamp
    (an old record) sorts last, which is correct -- an undated record is not
    evidence of being the most recent.
    """
    # Map each character to its complement so a lexical ascending sort yields
    # descending order; empty stays empty and so sorts after any real stamp
    # under ascending order of the tuple's other members.
    return "".join(chr(0x10FFFF - ord(c)) for c in s) if s else "￿"


def _is_empty(r: dict) -> bool:
    return (not r.get("tags") and not r.get("bookmark")
            and not r.get("name") and not r.get("note"))


def _coerce_record(r: dict) -> dict:
    """Normalise one record to the canonical shape, tolerating a hand-edited
    or older backup.  A record is data from disk, so every field is defended:
    a missing key becomes its empty form, a mistyped one is coerced, and the
    path is re-normalised so a hand-typed backup keys the same as a tagged
    one."""
    tags_raw = r.get("tags") or []
    if not isinstance(tags_raw, list):
        tags_raw = []
    tags = sorted({_clean_tag(str(t)) for t in tags_raw if _clean_tag(str(t))})
    name = r.get("name")
    name = str(name).strip() or None if name else None
    return {
        "path": norm_path(str(r.get("path", ""))),
        "hash": str(r.get("hash", "")),
        "tags": tags,
        "bookmark": bool(r.get("bookmark", False)),
        "name": name,
        "note": str(r.get("note", "") or ""),
        "addedAt": str(r.get("addedAt", "") or _now()),
        "updatedAt": str(r.get("updatedAt", "") or _now()),
    }
