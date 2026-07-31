#!/usr/bin/env python3
r"""
tagstore.py -- the viewer's user-tag store.

This is the user's own curation data, so three properties matter more than
anything clever:

  1. **It is never lost.**  Every write goes to a temp file in the same
     directory and is then `os.replace`d over the target, which is atomic on
     Windows and POSIX alike.  The previous version is kept as `.bak`.
  2. **It is never locked in.**  A single small JSON file, human-readable,
     sorted, stable key order.  Also exportable as CSV.  Deleting the viewer
     loses nothing.
  3. **Hand tags are sacred.**  Only user-typed tags live here.  The derived
     class/gender/size tags are recomputed from `bodyfacets.py` on every
     request and are never written to this file, so no automatic process can
     clobber a hand tag -- there is no code path that could.

Subjects are opaque strings so the same store can tag anything:

    app:002135300              an appearance id (shared by body and mix_body,
                               which are the same armor.ini row)
    file:c3/texture/00...dds   a raw asset by logical path

Nothing here touches the game install.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_VERSION = 1

#: Tags are lowercased and trimmed.  Anything is allowed except the characters
#: that would make the file or a CSV export ambiguous.
_BAD = re.compile(r'[\x00-\x1f",]')
MAX_TAG_LEN = 64


class TagError(ValueError):
    pass


def normalise(tag: str) -> str:
    t = (tag or "").strip().lower()
    if not t:
        raise TagError("empty tag")
    if _BAD.search(t):
        raise TagError("a tag cannot contain commas, quotes or control characters")
    if len(t) > MAX_TAG_LEN:
        raise TagError(f"tag longer than {MAX_TAG_LEN} characters")
    return t


class TagStore:
    """Thread-safe, atomically-persisted subject -> set(tags) map."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._tags: dict[str, list[str]] = {}
        self._notes: dict[str, str] = {}
        self.created = ""
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            if not self.path.is_file():
                self._tags, self._notes = {}, {}
                self.created = _now()
                return
            try:
                raw = json.loads(self.path.read_text("utf-8"))
            except Exception as e:
                # Never silently start from empty over a corrupt file -- move it
                # aside so the user still has it.
                bad = self.path.with_suffix(".corrupt")
                try:
                    os.replace(self.path, bad)
                except OSError:
                    pass
                raise TagError(f"{self.path} is not readable JSON ({e}); "
                               f"moved to {bad.name}, starting empty")
            subjects = raw.get("subjects", {})
            self._tags = {k: sorted(set(v.get("tags", []))) for k, v in subjects.items()}
            self._notes = {k: v["note"] for k, v in subjects.items() if v.get("note")}
            self.created = raw.get("created", _now())

    def _write(self) -> None:
        """Called with the lock held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema": SCHEMA_VERSION,
            "created": self.created or _now(),
            "updated": _now(),
            "note": ("User-typed tags for the CO asset viewer. Derived "
                     "class/gender/size tags are NOT stored here -- they are "
                     "recomputed from bodyfacets.py, so nothing automatic can "
                     "overwrite what you wrote."),
            # union of both maps: a subject may carry only a note and no tags
            "subjects": {
                k: ({"tags": self._tags.get(k, [])}
                    | ({"note": self._notes[k]} if k in self._notes else {}))
                for k in sorted(set(self._tags) | set(self._notes))
                if self._tags.get(k) or k in self._notes
            },
        }
        tmp = self.path.with_name(self.path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False),
                       "utf-8")
        if self.path.is_file():
            try:
                os.replace(self.path, self.path.with_suffix(".bak"))
            except OSError:
                pass
        os.replace(tmp, self.path)

    # -- reads -------------------------------------------------------------
    def get(self, subject: str) -> list[str]:
        with self._lock:
            return list(self._tags.get(subject, []))

    def note(self, subject: str) -> str:
        with self._lock:
            return self._notes.get(subject, "")

    def all_subjects(self) -> dict[str, list[str]]:
        with self._lock:
            return {k: list(v) for k, v in self._tags.items()}

    def vocabulary(self) -> dict[str, int]:
        """Every tag in use with how many subjects carry it."""
        counts: dict[str, int] = {}
        with self._lock:
            for tags in self._tags.values():
                for t in tags:
                    counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def subjects_with(self, tag: str) -> set[str]:
        t = normalise(tag)
        with self._lock:
            return {k for k, v in self._tags.items() if t in v}

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for v in self._tags.values() if v)

    # -- writes ------------------------------------------------------------
    def add(self, subjects: Iterable[str], tags: Iterable[str]) -> int:
        norm = [normalise(t) for t in tags]
        if not norm:
            return 0
        changed = 0
        with self._lock:
            for s in subjects:
                if not s:
                    continue
                cur = set(self._tags.get(s, []))
                new = cur | set(norm)
                if new != cur:
                    self._tags[s] = sorted(new)
                    changed += 1
            if changed:
                self._write()
        return changed

    def remove(self, subjects: Iterable[str], tags: Iterable[str]) -> int:
        norm = {normalise(t) for t in tags}
        changed = 0
        with self._lock:
            for s in subjects:
                cur = set(self._tags.get(s, []))
                new = cur - norm
                if new != cur:
                    if new:
                        self._tags[s] = sorted(new)
                    else:
                        self._tags.pop(s, None)
                    changed += 1
            if changed:
                self._write()
        return changed

    def set(self, subject: str, tags: Iterable[str]) -> list[str]:
        norm = sorted({normalise(t) for t in tags})
        with self._lock:
            if norm:
                self._tags[subject] = norm
            else:
                self._tags.pop(subject, None)
            self._write()
        return norm

    def set_note(self, subject: str, text: str) -> str:
        text = (text or "").strip()[:2000]
        with self._lock:
            if text:
                self._notes[subject] = text
            else:
                self._notes.pop(subject, None)
            self._write()
        return text

    def rename(self, old: str, new: str) -> int:
        o, n = normalise(old), normalise(new)
        changed = 0
        with self._lock:
            for s, tags in list(self._tags.items()):
                if o in tags:
                    self._tags[s] = sorted(set(tags) - {o} | {n})
                    changed += 1
            if changed:
                self._write()
        return changed

    def delete_tag(self, tag: str) -> int:
        return self.remove(list(self.all_subjects()), [tag])

    # -- export ------------------------------------------------------------
    def export_csv(self, extra: Optional[dict[str, dict]] = None) -> str:
        """subject, tags, note (+ any derived columns the caller supplies)."""
        extra = extra or {}
        cols = sorted({k for v in extra.values() for k in v})
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["subject", "tags", "note", *cols])
        with self._lock:
            keys = sorted(set(self._tags) | set(self._notes))
            for s in keys:
                e = extra.get(s, {})
                w.writerow([s, " ".join(self._tags.get(s, [])),
                            self._notes.get(s, ""), *[e.get(c, "") for c in cols]])
        return buf.getvalue()

    def export_json(self) -> str:
        with self._lock:
            return json.dumps({
                "schema": SCHEMA_VERSION, "exported": _now(),
                "subjects": {k: {"tags": v} for k, v in sorted(self._tags.items()) if v},
            }, indent=1, ensure_ascii=False)

    def import_json(self, text: str, *, merge: bool = True) -> int:
        """Bring tags back in from an export.  Merging by default -- an import
        should never be able to silently delete work."""
        doc = json.loads(text)
        subjects = doc.get("subjects", doc if isinstance(doc, dict) else {})
        n = 0
        with self._lock:
            for s, v in subjects.items():
                tags = v.get("tags", []) if isinstance(v, dict) else list(v)
                tags = [normalise(t) for t in tags if str(t).strip()]
                if not tags:
                    continue
                cur = set(self._tags.get(s, [])) if merge else set()
                self._tags[s] = sorted(cur | set(tags))
                n += 1
            self._write()
        return n


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="inspect the viewer's tag store")
    ap.add_argument("--file", default=str(
        Path(__file__).resolve().parent.parent / "out" / "viewer" / "tags.json"))
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    st = TagStore(Path(a.file))
    if a.csv:
        print(st.export_csv(), end="")
    else:
        print(f"{a.file}\n{len(st)} tagged subjects, {len(st.vocabulary())} distinct tags")
        for t, n in st.vocabulary().items():
            print(f"   {n:>5}  {t}")
