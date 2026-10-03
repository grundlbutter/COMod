#!/usr/bin/env python3
r"""
framesets.py -- the three 2D art categories COMod could not browse: item
icons, interface art, and map backdrops.

    py -3 tools/framesets.py --client <DIR> --coverage
    py -3 tools/framesets.py --client <DIR> --category backdrop --manifests
    py -3 tools/framesets.py --client <DIR> --set ani/ItemMinIcon.Ani:Item111003
    py -3 tools/framesets.py --client <DIR> --category interface --extract-cmds

WHAT THIS IS AND WHAT IT IS DELIBERATELY NOT
--------------------------------------------
It is a **catalogue over the client's own frame manifests**, not a second
asset system. Every path it hands back is a logical path
`coassets.AssetRoot` already resolves and `comod extract` already extracts;
every `.ani` it reads is tokenized by `core/ani.py`, the canonical manifest
reader, and every `.json` twin by `core/dmap.load_ani`, which owns the
spelling choice between the two forms. Nothing here opens an archive, decodes
a texture, or writes anything.

WHAT IT ADDS THAT `core/ani.py` AND `tools/aniset.py` DO NOT
------------------------------------------------------------
Those two own the FORMAT: a sequence read as a sequence, `FrameAmount`
checked, frames exported and re-imported in order, byte-faithful rewrite.
This module owns the **CATEGORY and the CENSUS** -- which of the four asset
categories a sequence belongs to, and how much of each one a given install
actually ships. `aniset` samples 2,000 sequences per install to characterise
absence; this enumerates every sequence of every manifest and reports the
totals per category, which is what a coverage claim needs.

There is no second tokenizer here, deliberately: `core/ani.py`'s docstring
records that `.ani` had already been hand-parsed three times in this tree
before it existed.

THE THREE CATEGORIES ARE DERIVED, NOT DECLARED
----------------------------------------------
A manifest is filed by where its FRAMES point, not by its name. That matters
because the names are not a taxonomy: `faction.ani` is backdrop art,
`Control.Ani` is interface, `material.ani` is item icons, and a private
server can ship a manifest nobody here has ever seen.

**And the placing is `tools/catalog.py`'s, not a second one.** `bucket_of`
is a PROJECTION of the existing taxonomy onto the five coarse buckets this
sweep reports, nothing more -- it asks `AssetCatalog.classify` where a frame
belongs and folds the answer. A second prefix table here would have been a
second taxonomy to keep in step, which is the failure `dmap.load_ani`'s
docstring records three readers making about `.ani` itself.

That projection is what exposed the gap it also fixes. Reading all 2,312
loose `ani/*.ani` in the corpus and grouping frame targets by directory turns
up six prefixes `catalog.RULES` had no rule for -- `data/main1/`,
`data/mapminicon/`, `data/mapicon/`, `data1/interface/`, `data3/interface/`
and `data/playerfacecop/`. Every reference to them classified as
`other/other`. The rules are added in `catalog.py` in the same commit, so the
fix is in the taxonomy and every consumer of it gains, not only this file.

A MANIFEST CAN BE MIXED, AND ONE IS
------------------------------------
`category_of` reports the MAJORITY bucket and `category_counts` reports the
whole distribution, because collapsing a mixed manifest to one label is how a
category quietly loses art. MEASURED: exactly one shipped manifest is mixed
enough for the two to disagree -- `NpcFace.Ani` on the twenty installs from
6680 to 7878, whose first sections are faces and whose bulk is item icons.
Every other manifest on every install is single-bucket by a wide margin.

WHAT IT REFUSES TO SMOOTH OVER
-------------------------------
* **A declared frame that does not ship.** The corpus is full of them --
  `comod` already knows appearance refs behave this way -- and a coverage
  number that counted only resolvable frames would report a client's own gap
  as our success. `FrameSet.missing` names them.
* **``FrameAmount`` disagreeing with the rows present.** `ani.Sequence` keeps
  the declared number precisely so a writer cannot renumber `Frame0..N` out
  of step with it; `--coverage` counts the disagreements.
* **A duplicate section that the client would never reach.** `shadowed`
  sections (`ani.Sequence.occurrence > 0`) are excluded from every count and
  reported separately; the reason the FIRST occurrence wins is in
  `core/ani.py` and `core/dmap.read_ani`.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

import ani                                         # noqa: E402
import catalog                                     # noqa: E402
import coassets                                    # noqa: E402
import coroot                                      # noqa: E402
import dmap                                        # noqa: E402

#: The three the coverage sweep is about. `face` and `effect` are reported
#: too -- they fall out of the same read and hiding them would be the
#: "tidy number over a gap" this module is written against -- but they belong
#: to other workstreams.
PRIMARY = ("itemicon", "interface", "backdrop")

OTHER = "other"

#: `(catalog category, catalog subcategory)` -> this sweep's coarse bucket.
#: A `None` subcategory means "the whole category folds here". The taxonomy
#: is `catalog.RULES`; this is only the fold, so a rule added there arrives
#: here for free and a bucket cannot disagree with the browse tree.
_FOLD: dict[tuple[str, Optional[str]], str] = {
    ("ui", "itemicon"): "itemicon",
    ("ui", "mapicon"): "itemicon",
    ("ui", None): "interface",          # interface, emotion, cursor, misc...
    # The minimap is filed under Maps by the taxonomy -- correctly, it is
    # per-map art -- but it is DRAWN in the HUD, and the owner's four
    # categories separate "interface art" from "map backdrops" by where the
    # pixels land. Folded to interface, said out loud rather than left to
    # `("map", None)`: 188 frame refs on 5065, 2,553 on 7878.
    ("map", "minimap"): "interface",
    ("map", None): "backdrop",
    ("character", "face"): "face",
    ("npc", "face"): "face",
    ("ui", "face"): "face",
    ("effect", None): "effect",
}


def bucket_of(logical: str, cat: Optional["catalog.AssetCatalog"] = None) -> str:
    """Which 2D category one frame path belongs to. Never raises.

    `cat` is an `AssetCatalog` to ask; without one a throwaway is built, so
    a caller that classifies many paths should pass one and keep its cache.
    """
    p = str(logical or "").replace("\\", "/").lstrip("/").lower()
    if cat is None:
        cat = catalog.AssetCatalog(Path("."))
    try:
        c = cat.classify(p)
    except Exception:                                # noqa: BLE001
        return OTHER
    hit = _FOLD.get((c.category, c.subcategory))
    if hit is None:
        hit = _FOLD.get((c.category, None))
    return hit or OTHER


@dataclass
class FrameSet:
    """One named, ORDERED frame sequence, placed and resolved."""

    manifest: str                       # "ani/ItemMinIcon.ani", as it resolved
    name: str                           # the section name, verbatim
    frames: list[str] = field(default_factory=list)
    declared: Optional[int] = None      # FrameAmount as written
    index: int = 0                      # position in the manifest
    category: str = OTHER
    #: Frames that do not resolve in THIS install, in order. A client fault,
    #: not a resolver fault -- see the module docstring.
    missing: list[str] = field(default_factory=list)

    @property
    def animated(self) -> bool:
        """More than one frame: the sequence IS an animation."""
        return len(self.frames) > 1

    @property
    def count_matches(self) -> bool:
        return self.declared is None or self.declared == len(self.frames)

    @property
    def present(self) -> int:
        return len(self.frames) - len(self.missing)

    def to_json(self) -> dict:
        return {"manifest": self.manifest, "name": self.name,
                "category": self.category, "declared": self.declared,
                "frames": list(self.frames), "missing": list(self.missing),
                "animated": self.animated, "countMatches": self.count_matches}


class FrameLibrary:
    r"""Every frame manifest one install ships, categorised.

    `root` is an install directory; `view` is an optional
    `coassets.AssetRoot` to resolve frames through. Without one, nothing is
    resolved and `missing` stays empty on every set -- **which is reported,
    not hidden**: `resolved` is False and `--coverage` says so, because an
    unresolved sweep answering "0 missing" is the shape of a control that
    cannot fire.
    """

    def __init__(self, root: Path | str, view=None):
        self.root = Path(root)
        self.view = view
        self.resolved = view is not None
        self._sets: Optional[list[FrameSet]] = None
        self._cat_counts: dict[str, dict[str, int]] = {}
        self._shadowed = 0
        self._exists: dict[str, bool] = {}
        #: One catalog for the whole sweep, so its per-path cache is reused
        #: across the ~400,000 frame refs a modern install declares.
        self._cat = catalog.AssetCatalog(self.root)
        self._bucket: dict[str, str] = {}

    # -- discovery ---------------------------------------------------------
    def manifest_files(self) -> list[Path]:
        r"""The `ani/` directory, both spellings, sorted.

        Loose-only, and that is a MEASURED limit rather than an oversight:
        `dmap.load_ani` resolves `root/ani/<stem>.{json,ani}` off the
        filesystem, and over the 34 install directories under
        `coroot.clients_dir()` every one that ships manifests at all ships
        them loose. An install that moved `ani/` inside an archive would
        report zero manifests here, and `--coverage` prints the file count so
        that reads as "none found", not as "none exist".
        """
        d = self.root / "ani"
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in (".ani", ".json"))

    def bucket(self, logical: str) -> str:
        """`bucket_of` against THIS library's catalog, memoised."""
        hit = self._bucket.get(logical)
        if hit is None:
            hit = bucket_of(logical, self._cat)
            self._bucket[logical] = hit
        return hit

    def _exists_logical(self, logical: str) -> bool:
        if self.view is None:
            return True
        hit = self._exists.get(logical)
        if hit is None:
            try:
                hit = self.view.locate(logical) is not None
            except Exception:                        # noqa: BLE001
                # A resolver that raises on one path must not lose the other
                # 400,000. Reported as absent, which is the conservative side.
                hit = False
            self._exists[logical] = hit
        return hit

    def _sections(self, p: Path) -> tuple[str, list[tuple]]:
        r"""``(logical rel, [(name, frames, declared, index, shadowed), ...])``.

        TWO SPELLINGS, TWO OWNERS, AND NEITHER PARSER IS WRITTEN HERE. A
        `.ani` goes through `core/ani.py`, the canonical tokenizer -- which is
        also where `dmap.read_ani`'s body now lives, so this module and the
        map readers cannot disagree about a manifest. A `.json` goes through
        `core/dmap.load_ani`, which owns the choice between the forms and is
        the reason `mapparts` once resolved zero map art on CCO.

        The `.json` form carries no `FrameAmount` and its duplicate keys were
        already collapsed by `json.loads`, so `declared` is None and
        `shadowed` is False for every one of its sections -- reported as "not
        checked", never as "checked and clean".
        """
        rel = "ani/" + p.name
        if p.suffix.lower() == ".json":
            got_rel, table = dmap.load_ani(self.root, rel)
            return got_rel, [(name, frames, None, i, False)
                             for i, (name, frames) in enumerate(table.items())]
        try:
            af = ani.AniFile.read(p)
        except OSError:
            return "", []
        out = []
        for i, s in enumerate(af.sequences):
            frames = [f.logical for f in s.listed_frames()]
            if not s.name or not frames:
                continue
            out.append((s.name, frames, s.declared, i, s.occurrence > 0))
        return rel, out

    def _build(self) -> None:
        if self._sets is not None:
            return
        out: list[FrameSet] = []
        for p in self.manifest_files():
            rel, secs = self._sections(p)
            if not secs:
                continue
            counts: dict[str, int] = {}
            mine: list[FrameSet] = []
            for name, frames, declared, index, shadowed in secs:
                if shadowed:
                    self._shadowed += 1
                    continue
                fs = FrameSet(manifest=rel, name=name, frames=list(frames),
                              declared=declared, index=index)
                for f in frames:
                    b = self.bucket(f)
                    counts[b] = counts.get(b, 0) + 1
                mine.append(fs)
            if not counts:
                continue
            self._cat_counts[rel.lower()] = counts
            for fs in mine:
                # The SET's own frames decide the set's category; the
                # manifest's majority is a separate question and is what
                # `category_of` answers. A mixed manifest therefore yields
                # correctly-placed sets even though the file has one label.
                c: dict[str, int] = {}
                for f in fs.frames:
                    b = self.bucket(f)
                    c[b] = c.get(b, 0) + 1
                fs.category = max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]
                if self.view is not None:
                    fs.missing = [f for f in fs.frames
                                  if not self._exists_logical(f)]
                out.append(fs)
        self._sets = out

    # -- queries -----------------------------------------------------------
    def sets(self, category: str = "", manifest: str = "") -> Iterator[FrameSet]:
        """Every frame set, optionally filtered. File order is preserved."""
        self._build()
        m = manifest.replace("\\", "/").lower()
        for fs in self._sets or ():
            if category and fs.category != category:
                continue
            if m and fs.manifest.lower() != m and Path(fs.manifest).name.lower() != m:
                continue
            yield fs

    def get(self, manifest: str, name: str) -> Optional[FrameSet]:
        """One named set. Case-insensitive on the section name, because the
        client's own reader is: `GetPrivateProfileString` folds case."""
        want = name.lower()
        for fs in self.sets(manifest=manifest):
            if fs.name.lower() == want:
                return fs
        return None

    def category_counts(self, manifest: str) -> dict[str, int]:
        """The whole bucket distribution of one manifest, never collapsed."""
        self._build()
        key = manifest.replace("\\", "/").lower()
        if key in self._cat_counts:
            return dict(self._cat_counts[key])
        stem = Path(key).stem
        for k, v in self._cat_counts.items():
            if Path(k).stem == stem:
                return dict(v)
        return {}

    def category_of(self, manifest: str) -> str:
        """The manifest's majority bucket, or "" when it holds no frames."""
        c = self.category_counts(manifest)
        if not c:
            return ""
        return max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def manifests(self, category: str = "") -> list[tuple[str, str, int]]:
        """`[(logical rel, majority category, frame refs), ...]`, sorted."""
        self._build()
        out = []
        for rel, counts in sorted(self._cat_counts.items()):
            cat = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if category and cat != category:
                continue
            out.append((rel, cat, sum(counts.values())))
        return out

    # -- the number this module exists to produce --------------------------
    def coverage(self) -> dict:
        r"""Per-category counts for ONE install.

        Every field is a count of something measured on this install; there
        is no derived percentage here on purpose, because a percentage is
        where "sets" and "frames" and "distinct frames" get silently mixed.
        """
        self._build()
        cats: dict[str, dict] = {}
        seen: dict[str, set] = {}
        for fs in self._sets or ():
            e = cats.setdefault(fs.category, {
                "sets": 0, "animated_sets": 0, "frame_refs": 0,
                "distinct_frames": 0, "present": 0, "missing": 0,
                "count_mismatch": 0})
            s = seen.setdefault(fs.category, set())
            e["sets"] += 1
            e["animated_sets"] += 1 if fs.animated else 0
            e["frame_refs"] += len(fs.frames)
            e["count_mismatch"] += 0 if fs.count_matches else 1
            for f in fs.frames:
                if f in s:
                    continue
                s.add(f)
                e["distinct_frames"] += 1
                if self.view is None:
                    continue
                if self._exists_logical(f):
                    e["present"] += 1
                else:
                    e["missing"] += 1
        return {"install": self.root.name, "root": str(self.root),
                "manifests": len(self.manifest_files()),
                "shadowed_sections": self._shadowed,
                "resolved": self.resolved, "categories": cats}


def extract_commands(fs: FrameSet, root: Path | str) -> list[str]:
    r"""The `comod extract` command line for each frame, IN ORDER.

    Deliberately a list of the EXISTING command rather than a new extractor:
    a frame is an ordinary logical path, `comod extract` already resolves it
    through the same `AssetRoot`, and a second extraction path would be a
    second set of archive, overlay and precedence rules to keep in step.

    The order is the animation, so the list is emitted in frame order and the
    caller is told the index; a set exported out of order is a manifest the
    client reads as a different animation rather than a broken one.

    **``--root`` GOES BEFORE THE SUBCOMMAND**, because `comod.py` declares it
    on the top-level parser. The first draft of this function emitted
    ``extract --root <dir> <path>`` and every line it produced died with
    ``unrecognized arguments`` -- a command that is printed but never run is
    exactly the shape `docs/` calls out for owner-facing instructions, so
    `tests/test_asset_coverage.py::TheExtractCommandParses` feeds these lines
    to `comod`'s own parser rather than eyeballing them.
    """
    return [f'py -3 tools/comod.py --root "{root}" extract {f}'
            for f in fs.frames]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _open(root: Path, want_view: bool = True):
    view = None
    if want_view:
        try:
            view = coassets.AssetRoot.bare(root)
        except Exception as e:                        # noqa: BLE001
            print(f"  (no AssetRoot for {root}: {e!r} -- frames will not be "
                  f"resolved and 'missing' cannot fire)")
    return FrameLibrary(root, view)


def _print_coverage(cov: dict) -> None:
    print(f"  {cov['install']}   manifests={cov['manifests']}  "
          f"shadowed sections={cov['shadowed_sections']}"
          f"{'' if cov['resolved'] else '   [UNRESOLVED: no AssetRoot]'}")
    if not cov["categories"]:
        print("      no frame manifests found under ani/")
        return
    print(f"      {'category':<12}{'sets':>7}{'anim':>7}{'refs':>9}"
          f"{'distinct':>10}{'present':>9}{'missing':>9}{'badcount':>10}")
    order = list(PRIMARY) + sorted(k for k in cov["categories"]
                                   if k not in PRIMARY)
    for c in order:
        e = cov["categories"].get(c)
        if not e:
            continue
        print(f"      {c:<12}{e['sets']:>7}{e['animated_sets']:>7}"
              f"{e['frame_refs']:>9}{e['distinct_frames']:>10}"
              f"{e['present']:>9}{e['missing']:>9}{e['count_mismatch']:>10}")


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", help="one install directory")
    ap.add_argument("--all-clients", action="store_true",
                    help="sweep every install under the clients directory")
    ap.add_argument("--category", default="",
                    help="itemicon | interface | backdrop | face | effect | other")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--manifests", action="store_true")
    ap.add_argument("--sets", action="store_true", help="list frame sets")
    ap.add_argument("--set", dest="one", default="",
                    help="<manifest>:<section> -- print one set in order")
    ap.add_argument("--extract-cmds", action="store_true",
                    help="with --set, print the comod extract command per frame")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--no-resolve", action="store_true",
                    help="skip AssetRoot; faster, and reports nothing missing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    roots: list[Path] = []
    if a.all_clients:
        cd = coroot.clients_dir()
        roots = [p for p in sorted(cd.iterdir())
                 if p.is_dir() and coroot.looks_like_root(p)]
    elif a.client:
        roots = [Path(a.client)]
    else:
        ap.error("one of --client or --all-clients is required")

    import json as _json
    out = []
    for root in roots:
        lib = _open(root, not a.no_resolve)
        if a.one:
            man, _, sec = a.one.partition(":")
            fs = lib.get(man, sec)
            if fs is None:
                print(f"  no such set: {a.one} in {root.name}")
                return 1
            if a.json:
                out.append(fs.to_json())
            else:
                print(f"  {fs.manifest} [{fs.name}]  category={fs.category}  "
                      f"declared={fs.declared}  frames={len(fs.frames)}"
                      f"{'' if fs.count_matches else '   COUNT MISMATCH'}")
                for i, f in enumerate(fs.frames):
                    mark = "  MISSING" if f in fs.missing else ""
                    print(f"      Frame{i}  {f}{mark}")
                if a.extract_cmds:
                    print()
                    for c in extract_commands(fs, root):
                        print("      " + c)
            continue
        if a.manifests:
            print(f"  {root.name}")
            for rel, cat, refs in lib.manifests(a.category):
                dist = lib.category_counts(rel)
                mixed = "" if len(dist) == 1 else f"   mixed: {dist}"
                print(f"      {rel:<34}{cat:<11}{refs:>8} refs{mixed}")
            continue
        if a.sets:
            print(f"  {root.name}")
            n = 0
            for fs in lib.sets(a.category):
                n += 1
                if n > a.limit:
                    continue
                mark = "  MISSING %d" % len(fs.missing) if fs.missing else ""
                print(f"      {fs.manifest:<30}{fs.name:<26}"
                      f"{fs.category:<11}{len(fs.frames):>4} frames{mark}")
            if n > a.limit:
                print(f"      ... {n - a.limit} more (--limit)")
            continue
        cov = lib.coverage()
        out.append(cov)
        if not a.json:
            _print_coverage(cov)
    if a.json and out:
        print(_json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
