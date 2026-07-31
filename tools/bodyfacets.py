#!/usr/bin/env python3
r"""
bodyfacets.py -- classify body / mix_body appearances by class, gender and size.

The three axes the viewer filters on, and where each one actually comes from:

    gender + size   the first 3 digits of the 9-digit appearance ID
    class           the NEXT 3 digits (the "armour series"), resolved through
                    ini/itemtype.json's `requiredProfession`

Derivation, evidence and confidence are in docs/appearance_ids.md.  Short
version, all of it measured against this install rather than assumed:

  * 3,233 of the 3,418 body appearances are prefixed 001/002/003/004 in near
    equal groups.  The remaining 185 are all of the form `NNN000000` -- one
    base body per NPC/monster body type, no armour.

  * 16 of the 17 gender-locked garments in itemtype.json (`requiredSex` 1 or 2)
    have art under exactly two of the four prefixes, and the split is clean:
    every female-only garment appears under 001+002, every male-only one under
    003+004.  That fixes the gender axis. VERIFIED.

  * Measuring the four base body meshes gives a strict ordering on every axis
    (height, arm span, front-to-back depth): 001 < 002 and 003 < 004.  Rendering
    them confirms 001/002 female and 003/004 male by sight.  So the lower number
    of each pair is the smaller body. VERIFIED by measurement + render.

  * The armour series maps to exactly one `requiredProfession` for 99.0% of
    those 3,233 appearances, and no series is internally inconsistent.
    VERIFIED.

  * Profession codes present in this build: 0, 11, 15, 21, 25, 40, 45, 143, 190.
    No Ninja/Monk/Pirate codes exist here -- it really is the four classes plus
    "any".  Read off item names and, decisively, weapon exclusivity: shields
    (900xxx) are 21, bows (500xxx) are 40, backswords (421xxx) are 190.

Nothing in this module writes anything or touches the game install.
"""

from __future__ import annotations

import collections
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT, load_items          # noqa: E402

# ---------------------------------------------------------------------------
# the four player body types
# ---------------------------------------------------------------------------

#: prefix -> (gender, size).  VERIFIED, see the module docstring.
BODY_TYPES: dict[str, tuple[str, str]] = {
    "001": ("female", "small"),
    "002": ("female", "large"),
    "003": ("male", "small"),
    "004": ("male", "large"),
}

#: Measured from the four base meshes `c3/mesh/00X000000.c3`, after the chunk
#: matrix and the (x, y, -z) conversion.  Reported in the UI so the size axis
#: is not just an assertion.
BODY_METRICS: dict[str, dict[str, float]] = {
    "001": {"height": 168.1, "span": 135.6, "depth": 24.6},
    "002": {"height": 170.5, "span": 159.5, "depth": 30.9},
    "003": {"height": 176.2, "span": 178.9, "depth": 32.3},
    "004": {"height": 195.7, "span": 217.7, "depth": 41.0},
}

#: itemtype.json `requiredProfession` -> class name.  The units digit is a tier
#: within the class (e.g. 11 and 15 are both Trojan), so this folds on tens.
#: 143 is two consumables (FireofHell, BombScroll) and never backs a body
#: appearance; it is listed for completeness.
PROFESSION_CLASS: dict[int, str] = {
    0: "any",
    11: "Trojan", 15: "Trojan",
    21: "Warrior", 25: "Warrior",
    40: "Archer", 45: "Archer",
    143: "Taoist", 190: "Taoist",
}

CLASS_ORDER = ["Warrior", "Trojan", "Taoist", "Archer", "any", "unknown"]
GENDER_ORDER = ["female", "male", "other"]
SIZE_ORDER = ["small", "large", "n/a"]

#: itemtype.json `requiredSex`.  0 on 11,125 of 11,142 rows, so it is only
#: useful on the 17 that set it -- which is exactly what pinned the gender axis.
SEX_NAMES = {0: "any", 1: "male", 2: "female"}


@dataclass
class BodyRecord:
    """One appearance of the body / mix_body tables, fully classified."""
    ident: str
    body_type: str = ""            # "001".."004", or the raw prefix
    gender: str = "other"
    size: str = "n/a"
    series: str = ""               # the 3-digit armour series
    klass: str = "unknown"
    profession: Optional[int] = None
    kind: str = "armour"           # armour | base body | npc body
    mesh_id: str = ""
    texture_id: str = ""
    mesh: Optional[str] = None     # logical path
    texture: Optional[str] = None
    item_family: str = ""
    item_ids: list[str] = field(default_factory=list)
    item_name: str = ""
    tables: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["class"] = d.pop("klass")
        d["metrics"] = BODY_METRICS.get(self.body_type)
        return d

    @property
    def auto_tags(self) -> list[str]:
        """Derived tags. Recomputed every time; never persisted, so they can
        never overwrite anything the user typed."""
        t = [f"class:{self.klass}", f"gender:{self.gender}", f"body:{self.body_type}"]
        if self.size != "n/a":
            t.append(f"size:{self.size}")
        t.append(f"kind:{self.kind.replace(' ', '-')}")
        if self.series:
            t.append(f"series:{self.series}")
        return t


class BodyFacets:
    """Builds the class/gender/size classification for every body appearance.

    Construction cost is dominated by loading ini/itemtype.json (8.3 MB) once.
    """

    def __init__(self, root: Path = DEFAULT_ROOT, part_tables: Optional[dict] = None,
                 resolve=None):
        self.root = Path(root)
        self._resolve = resolve or (lambda ident, kind: None)
        items = load_items(self.root)

        #: 5-digit item family -> the rows in it.  Item ids are 6 digits whose
        #: LAST digit is a quality/tier step (3..9 mostly), and appearance ids
        #: always end in 0 -- so an appearance names a *family* of items, not
        #: one item.  Matching on the family lifts appearance->item coverage
        #: from 19.4% to 65.9%.
        self.families: dict[str, list[dict]] = collections.defaultdict(list)
        #: 3-digit series -> Counter of requiredProfession over every item in it
        self.series_prof: dict[str, collections.Counter] = collections.defaultdict(
            collections.Counter)
        for it in items:
            sid = str(it.get("id", ""))
            if len(sid) != 6:
                continue
            self.families[sid[:5]].append(it)
            self.series_prof[sid[:3]][it.get("requiredProfession")] += 1

        self.records: dict[str, BodyRecord] = {}
        if part_tables:
            self.build(part_tables)

    # ------------------------------------------------------------------
    def series_profession(self, series: str) -> Optional[int]:
        """The requiredProfession of a 3-digit armour series, or None when the
        series has no items or its items disagree.

        Returning None on disagreement rather than a majority vote is
        deliberate: a mixed series would mean the whole class axis is unsound
        for it, and the user should see "unknown" rather than a guess.  As it
        happens no series in this build is mixed.
        """
        c = self.series_prof.get(series)
        if not c:
            return None
        if len(c) != 1:
            return None
        return next(iter(c))

    def classify(self, ident: str) -> BodyRecord:
        r = BodyRecord(ident=ident)
        if not (ident.isdigit() and len(ident) == 9):
            r.kind = "other"
            return r
        r.body_type = ident[:3]
        r.series = ident[3:6]

        if r.body_type in BODY_TYPES:
            r.gender, r.size = BODY_TYPES[r.body_type]
        else:
            r.gender, r.size = "other", "n/a"

        if ident[3:] == "000000":
            r.kind = "base body" if r.body_type in BODY_TYPES else "npc body"
            r.klass = "any" if r.body_type in BODY_TYPES else "unknown"
            return r

        r.item_family = ident[3:8]
        fam = self.families.get(r.item_family, [])
        r.item_ids = [str(i["id"]) for i in fam]
        if fam:
            r.item_name = str(fam[0].get("name", ""))

        prof = self.series_profession(r.series)
        r.profession = prof
        r.klass = PROFESSION_CLASS.get(prof, "unknown") if prof is not None else "unknown"
        return r

    def build(self, part_tables: dict) -> dict[str, BodyRecord]:
        """Classify every appearance of the body-shaped tables."""
        seen: dict[str, BodyRecord] = {}
        for table_name in ("body", "mix_body"):
            ini = part_tables.get(table_name)
            if ini is None:
                continue
            for app in ini:
                rec = seen.get(app.ident)
                if rec is None:
                    rec = self.classify(app.ident)
                    if app.parts:
                        p = app.parts[0]
                        rec.mesh_id, rec.texture_id = p.mesh, p.texture
                        rec.mesh = self._resolve(p.mesh, "mesh")
                        rec.texture = self._resolve(p.texture, "texture")
                    seen[app.ident] = rec
                if table_name not in rec.tables:
                    rec.tables.append(table_name)
        self.records = seen
        return seen

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """Counts per facet value -- what the UI shows next to each filter."""
        out = {"class": collections.Counter(), "gender": collections.Counter(),
               "size": collections.Counter(), "kind": collections.Counter(),
               "bodyType": collections.Counter(), "series": collections.Counter()}
        for r in self.records.values():
            out["class"][r.klass] += 1
            out["gender"][r.gender] += 1
            out["size"][r.size] += 1
            out["kind"][r.kind] += 1
            out["bodyType"][r.body_type] += 1
            out["series"][r.series] += 1
        return {k: dict(v) for k, v in out.items()}


def _cli(argv):
    import argparse
    import json
    from coassets import AssetRoot
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--json", action="store_true", help="dump every record as JSON")
    ap.add_argument("--id", help="classify one appearance id and stop")
    args = ap.parse_args(argv)

    with AssetRoot(args.root) as R:
        bf = BodyFacets(Path(args.root),
                        resolve=lambda i, k: (lambda l: l.logical if l else None)(
                            R.resolve_asset(i, k)))
        if args.id:
            print(json.dumps(bf.classify(args.id).to_json(), indent=2))
            return 0
        bf.build(R.part_tables())

    if args.json:
        print(json.dumps([r.to_json() for r in bf.records.values()], indent=1))
        return 0

    s = bf.summary()
    print(f"{len(bf.records)} body / mix_body appearances\n")
    for axis in ("class", "gender", "size", "kind"):
        order = {"class": CLASS_ORDER, "gender": GENDER_ORDER,
                 "size": SIZE_ORDER}.get(axis)
        rows = s[axis]
        keys = ([k for k in order if k in rows] + sorted(set(rows) - set(order or ()))
                ) if order else sorted(rows, key=lambda k: -rows[k])
        print(f"  {axis}")
        for k in keys:
            print(f"      {k:<12} {rows[k]:>5}")
    print("\n  body type")
    for bt in sorted(s["bodyType"], key=lambda k: -s["bodyType"][k])[:8]:
        g, z = BODY_TYPES.get(bt, ("other", "n/a"))
        m = BODY_METRICS.get(bt)
        extra = f"  height {m['height']:.1f}  span {m['span']:.1f}  depth {m['depth']:.1f}" if m else ""
        print(f"      {bt}  {g:<7} {z:<6} {s['bodyType'][bt]:>5}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
