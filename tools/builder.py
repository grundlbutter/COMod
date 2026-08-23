#!/usr/bin/env python3
r"""
builder.py -- the character builder's catalogue: what can go in each slot, and
what actually fits the body you picked.

    py -3 tools/builder.py --slots
    py -3 tools/builder.py --slot body --body-type 002
    py -3 tools/builder.py --slot armet --body 002135300 --kind hair
    py -3 tools/builder.py --superfx 410099
    py -3 tools/builder.py --audit          # the numbers this docstring quotes

WHY THIS MODULE EXISTS
----------------------
The old equip panel let you choose a *mesh* and then hunt for a *texture*.  That
is a cross-product, and most of it does not exist: it permits states the game
never ships.

**The appearance tables already are the valid combinations.**  `armor.ini`'s
entry `002135300` says `Mesh0=002135000, Texture0=002135300` -- one row, one
pair, shipped by the game.  So the builder offers *appearances*, never a mesh
and a texture chosen independently, and `Option` is constructed only when BOTH
sides resolve to a file that exists.  `tools/test_viewer.py:CharacterBuilder`
asserts that no offered option can be an invalid pair.

Two levels, because 850 rows in one list is not a choice a person can make:

    GARMENT   one distinct mesh          ~167 per body type in armor.ini
      COLOUR    its appearances           7 texture variants each, typically

Grouping on the mesh is not a presentation trick -- it is the data's own shape
(docs/appearance_ids.md §1: digit 7 selects the colour, digit 8 the cut, and the
mesh id is the appearance id with the colour digit zeroed).

WHAT SHIPS, MEASURED IN THIS BUILD
----------------------------------
Counting only appearances whose mesh AND texture both resolve:

    slot          ini            rows   usable   garments   note
    body          armor.ini      3418     3082        664
    armet         armet.ini      2918     1798        270   hair + headgear
    l/r_weapon    weapon.ini     5384     5214        438   not body-specific
    armet_dx8     armet1.ini      950        8          5   legacy DX8 catalogue
    head          head.ini          4        0          0   no mesh resolves
    misc          misc.ini        272        0          0   no mesh resolves
    mount         mount.ini      1317        0          0   no mesh resolves
    shield        shield.ini        -        -          -   ini not shipped
    pelvis        pelvis.ini        -        -          -   ini not shipped

Slots with nothing usable are reported with a *reason* and offer an empty list
rather than a list of things that render as nothing.

COMPATIBILITY -- VERIFIED IN THIS BUILD
---------------------------------------
* `armor.ini` and `armet.ini` ids are body-type prefixed `001`..`004`:
  3,233/3,418 and **2,918/2,918** respectively.  Filtering on the chosen body's
  prefix is therefore exact for headgear and near-exact for bodies.
* `weapon.ini` ids are 6-7 digits and **not one** carries a body-type prefix, so
  weapons are universal.  (Both measured, not assumed -- see `audit()`.)
* `armet1.ini`, `head.ini`, `misc.ini` and `mount.ini` ids are 7-8 digits with no
  body prefix either, so they are *not* filtered by body.  The old viewer listed
  `armet_dx8`, `head` and `misc` as body-specific; that is wrong for this build.

HAIR -- CORRECTED HERE
----------------------
`docs/viewer.md` §5 said hair is `armet.ini` series **111**.  The data says
otherwise and it is worth stating plainly:

    series 111  ->  IronHelmet, BronzeHelmet, SilverHelmet, ...   (10 item names)
    series 112  ->  ConquestHelmet, PhoenixHat, ...
    series 113  ->  BadgerHat, CatHat, MartenHat, ...
    series 114  ->  DestinyCap, LacyCap, ...
    series 119  ->  **no items at all**, 515 of 707 armet meshes, most of them
                    resolving into `c3/hair/`

and `docs/attachment.md` §8.3 measured how far each series sits above the skull:
series 119 median **+4.0** units (hair lies on the scalp) against 111's +18.1,
113's +9.4 and 114's +11.0 (helmets sit proud).  Series 111 also carries
`requiredProfession 21` -- Warrior -- which hair could not.

So **hair is series 119**; 111-116 are helmets and hats.  `HAIR_SERIES` here is
the authority the builder uses, and `parts.HAIR_SERIES` was corrected to match.
Nothing is hidden either way: the head picker has a hair/headgear chip and both
are always selectable.

SUPER WEAPON EFFECTS
--------------------
`c3/effect/<type>/<appearance>.c3` -- 194 meshes across 14 folders, every one
named after a real `weapon.ini` appearance and every one ending in quality digit
**9**.  `super_effect()` indexes them by appearance id.  The *anchor transform*
for these is task #20's (`tools/superfx.py`); until it lands the viewer rides
them on the weapon socket matrix and says so.
"""

from __future__ import annotations

import collections
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import bodyfacets                                         # noqa: E402
import parts as partsmod                                  # noqa: E402
from coassets import DEFAULT_ROOT, load_items             # noqa: E402

try:                                                      # task #13, imported
    import effects as effectsmod                          # noqa: E402
except Exception:                                         # pragma: no cover
    effectsmod = None


# ---------------------------------------------------------------------------
# hair vs headgear
# ---------------------------------------------------------------------------

#: `armet.ini` series that are hairstyles rather than head*gear*.  See the
#: module docstring: 119 is the only series with no items behind it, it is 515
#: of the 707 armet meshes, it resolves into `c3/hair/`, and it is the only one
#: that sits flat on the skull.  VERIFIED against itemtype.json and against
#: docs/attachment.md §8.3's measurements.
HAIR_SERIES = {"119"}

#: The seven hair colour digits, `Hairstyle Code = (Hair Colour x 100) + Style`.
HAIR_COLOURS = dict(partsmod.HAIR_COLOURS)


def head_kind(ident: str) -> str:
    """"hair" or "headgear" for one `armet.ini` appearance id."""
    if ident.isdigit() and len(ident) == 9 and ident[3:6] in HAIR_SERIES:
        return "hair"
    return "headgear"


def hair_style(ident: str) -> str:
    """The style number inside a hair series ("01".."68"), best effort."""
    if not (ident.isdigit() and len(ident) == 9):
        return ""
    return ident[7:9]


# ---------------------------------------------------------------------------
# slot presentation
# ---------------------------------------------------------------------------

#: Slots the picker shows first, in this order.  Everything else RolePart.ini
#: declares is still offered, just under "more slots" -- nothing is dropped.
PRIMARY_SLOTS = ("body", "armet", "r_weapon", "l_weapon")

#: Plain-language slot names.  The right-hand column is what a person would
#: call the thing; the RolePart.ini name is kept as the id.
SLOT_TITLES = {
    "body": "Body & armour",
    "armet": "Head",
    "armet_dx8": "Head (legacy DX8)",
    "l_weapon": "Left hand",
    "r_weapon": "Right hand",
    "shield": "Shield",
    "mount": "Mount",
    "misc": "Accessory",
    "head": "Bare head",
    "pelvis": "Pelvis",
}

SLOT_HINTS = {
    "body": "the character itself — pick this first, everything else fits to it",
    "armet": "hair and headgear share one slot, so a helmet replaces the hair",
    "l_weapon": "any weapon; weapons are not tied to a body type",
    "r_weapon": "any weapon; weapons are not tied to a body type",
}


# ---------------------------------------------------------------------------
# one offered option
# ---------------------------------------------------------------------------

@dataclass
class Option:
    """One appearance that can actually be equipped.

    Constructed only when the appearance's mesh AND texture both resolve to a
    file the client can open, so an Option is by construction a pair the game
    ships.  There is no code path that pairs an arbitrary mesh with an
    arbitrary texture.
    """
    ident: str
    slot: str
    table: str
    mesh: str
    texture: str
    mesh_id: str = ""
    texture_id: str = ""
    name: str = ""                 # plain-language label
    detail: str = ""               # secondary line
    body_type: str = ""            # "001".."004", "" when not body-specific
    series: str = ""
    axes: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.ident, "slot": self.slot, "table": self.table,
                "mesh": self.mesh, "texture": self.texture,
                "meshId": self.mesh_id, "textureId": self.texture_id,
                "name": self.name, "detail": self.detail,
                "bodyType": self.body_type, "series": self.series,
                "subject": f"app:{self.ident}", **self.axes}


#: The filter axes an option can carry.  Only axes with more than one distinct
#: value are rendered, so the head picker does not show a one-item "Gender" row.
AXES = ("class", "gender", "size", "kind", "type", "quality")

AXIS_LABEL = {"class": "Class", "gender": "Gender", "size": "Body size",
              "kind": "Kind", "type": "Weapon type", "quality": "Quality"}


# ---------------------------------------------------------------------------
# weapon quality -- the last digit of the appearance id
# ---------------------------------------------------------------------------
#
# A weapon appearance id is `TTTSSQ`: 3-digit weapon **type**, 2-digit **style**
# inside that type, and one **quality** digit.  The first five are the family;
# every member of a family shares one mesh and differs only in texture.
#
# VERIFIED two independent ways in this build:
#
#   ini/weapon.ini   41000  ->  3,4,5 = texture 410005   6,7 = 410006
#                               8,9 = 410008   (0 = 410006, see below)
#   ini/itemtype.json 410003..410009 are all "SteelBlade" with attackMax rising
#                     monotonically 9,10,10,11,12,13,14 -- the quality ladder,
#                     stated by the item database rather than inferred from art.
#                     410000/410001/410002 have NO item entry at all.
#
# Corpus, 828 six-digit weapon families:
#
#   distinct textures per family   3 -> 538   1 -> 216   2 -> 5   4..10 -> 69
#   the 3-texture split            (345)(67)(89) on 533 of the 538
#   digits 3..9                    643 itemtype.json rows each -- the real ladder
#   digits 1,2                     4 and 1 item rows; present in 31 families only
#
#: quality digit -> the named quality.  3/4/5 are three *grades* of Normal and
#: share one texture, which is why five names cover ten digits.
QUALITY_DIGITS = {"3": "normal", "4": "normal", "5": "normal",
                  "6": "refined", "7": "unique", "8": "elite", "9": "super"}

#: Presentation order, worst to best.
QUALITY_ORDER = ("normal", "refined", "unique", "elite", "super")

QUALITY_LABEL = {"normal": "Normal", "refined": "Refined", "unique": "Unique",
                 "elite": "Elite", "super": "Super"}

#: Which digit stands for each quality when several map to it.  Normal prefers
#: 5 (the top normal grade, and the id `itemtype.json` prices as the plain
#: weapon); the texture is identical across 3/4/5 either way.
QUALITY_PREF = {"normal": ("5", "4", "3"), "refined": ("6",),
                "unique": ("7",), "elite": ("8",), "super": ("9",)}

#: Digits that are NOT a player quality.  `0` is the family's default/base row
#: and 265 of its 617 occurrences have no `itemtype.json` entry; in **350** of
#: the families that have one it points at the *Refined* texture, not Normal --
#: which is why `410000` resolves to `410006` and must not be labelled "Normal".
#: In 139 families it is the only id there is, i.e. an untiered weapon.
#: `1` and `2` exist in 31 families (mostly the 350xx/360xx many-texture ones)
#: and carry 4 and 1 item rows between them.
NON_QUALITY_DIGITS = ("0", "1", "2")

QUALITY_NOTE = (
    "The last digit of a weapon appearance id is its quality: 3-5 Normal, "
    "6 Refined, 7 Unique, 8 Elite, 9 Super. The mesh never changes — only the "
    "texture — and 3/4/5 share one texture and 6/7 another and 8/9 a third, so "
    "five qualities give three distinct looks. Verified against ini/weapon.ini "
    "and, independently, against ini/itemtype.json, where 410003…410009 are one "
    "item whose attack rises with the digit.")

ZERO_DIGIT_NOTE = (
    "The …0 id is not a quality. It is the family's base row, it has no "
    "itemtype.json entry in 265 of the 617 families that carry one, and in 350 "
    "of them it points at the REFINED texture rather than the Normal one — "
    "410000 resolves to texture 410006. It is offered separately rather than "
    "mislabelled.")

AURA_QUALITY_NOTE = (
    "Only the Super (…9) id carries the always-on aura in 575 of the 646 "
    "families that have one at all; 70 families glow at every quality (the "
    "cosmetic weapon sets) and a handful are one-offs. So the toggle asks the "
    "table for the id you are actually wearing rather than assuming the rule.")


def quality_of(ident: str) -> str:
    """The named quality of one weapon appearance id, or "" if it has none."""
    if not (ident.isdigit() and len(ident) == 6):
        return ""
    return QUALITY_DIGITS.get(ident[5], "")


def weapon_family(ident: str) -> str:
    """`410009` -> `41000`. "" when the id is not a 6-digit weapon id."""
    if not (ident.isdigit() and len(ident) == 6):
        return ""
    return ident[:5]


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

class BuilderIndex:
    """Every equippable option, per slot, already checked against disk.

    Built once (~1.5 s over the 20k appearance rows in this install) and reused;
    the filters then run in memory.
    """

    def __init__(self, tables: dict, resolve: Callable[[str, str], Optional[str]],
                 root: Path = DEFAULT_ROOT, facets=None, exists=None):
        self.root = Path(root)
        self.tables = tables
        self._resolve = resolve
        self._exists = exists or (lambda p: True)
        self.facets = facets or bodyfacets.BodyFacets(self.root)

        self.items6: dict[str, dict] = {}
        self.items5: dict[str, dict] = {}
        for it in load_items(self.root):
            sid = str(it.get("id", ""))
            if len(sid) != 6:
                continue
            self.items6.setdefault(sid, it)
            self.items5.setdefault(sid[:5], it)

        self.weapon_type_names: dict[str, str] = {}
        if effectsmod is not None:
            try:
                self.weapon_type_names = dict(
                    effectsmod.EffectDB(self.root).weapon_type_names)
            except Exception:                             # pragma: no cover
                self.weapon_type_names = {}

        self.manifest = partsmod.PartManifest(self.root, tables=tables)
        #: slot -> why the rows this slot does not offer were not offered.
        #: Filled by `_build_slot`; read by `slot_info` so the reason string
        #: names the half that is actually missing.
        self.dropped: dict[str, dict] = {}
        self.options: dict[str, list[Option]] = {}
        for slot in self.manifest.slots:
            self.options[slot] = self._build_slot(slot)
        self._super: Optional[dict[str, str]] = None
        self._superdb = None

    # -- naming ------------------------------------------------------------
    def item_name(self, ident: str) -> str:
        """`itemtype.json`'s name for an appearance, via the item *family*.

        Item ids carry a trailing quality digit and appearance ids do not, so an
        appearance names a family (docs/appearance_ids.md §4).  Try the exact
        6-digit id first, then the 5-digit family.
        """
        cands: list[str] = []
        if len(ident) == 9:
            cands = [ident[3:], ident[3:8]]
        elif len(ident) in (6, 7, 8):
            cands = [ident[:6], ident[:5]]
        for c in cands:
            it = self.items6.get(c) if len(c) == 6 else self.items5.get(c)
            if it:
                return str(it.get("name", ""))
        return ""

    @staticmethod
    def _spaced(name: str) -> str:
        """`OxhideArmor` -> `Oxhide Armor`, so a label reads like English."""
        if not name:
            return ""
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("`", "'")

    # -- construction ------------------------------------------------------
    def _build_slot(self, slot: str) -> list[Option]:
        ini = self.tables.get(slot)
        if ini is None:
            return []
        out: list[Option] = []
        # WHY THE SPLIT IS RECORDED: the test below is mesh AND texture, and
        # `slot_info` used to report every rejection as "no mesh on disk".
        # MEASURED on CCO's weapon.ini: 5,381 of 5,384 entries resolve a mesh
        # and 5,214 resolve a texture, so of the 170 entries not offered, 167
        # are missing a TEXTURE and the readout blamed the mesh for all of
        # them. A reason string that names the wrong half sends the reader to
        # the wrong file.
        dropped = {"noMesh": 0, "noTexture": 0, "noParts": 0}
        for app in ini:
            if not app.parts:
                dropped["noParts"] += 1
                continue
            pr = app.parts[0]
            mesh = self._resolve(pr.mesh, "mesh")
            tex = self._resolve(pr.texture, "texture")
            # The whole point: an option is a SHIPPED pair or it is not offered.
            if not mesh or not tex:
                if not mesh:
                    dropped["noMesh"] += 1
                else:
                    dropped["noTexture"] += 1
                continue
            out.append(self._make(slot, app.ident, pr, mesh, tex))
        self.dropped[slot] = dropped
        return out

    def _make(self, slot: str, ident: str, pr, mesh: str, tex: str) -> Option:
        o = Option(ident=ident, slot=slot, table=slot, mesh=mesh, texture=tex,
                   mesh_id=pr.mesh, texture_id=pr.texture)
        raw_name = self.item_name(ident)
        nice = self._spaced(raw_name)
        if ident.isdigit() and len(ident) == 9:
            o.series = ident[3:6]
            if ident[:3] in bodyfacets.BODY_TYPES:
                o.body_type = ident[:3]
        elif len(ident) >= 3:
            o.series = ident[:3]

        if slot in ("body", "mix_body"):
            rec = self.facets.records.get(ident) or self.facets.classify(ident)
            o.axes = {"class": rec.klass, "gender": rec.gender, "size": rec.size,
                      "kind": rec.kind}
            o.name = nice or (f"{rec.kind.title()} {o.series}"
                              if rec.kind != "armour" else f"Armour {o.series}")
            o.detail = " · ".join(x for x in [
                rec.klass if rec.klass not in ("unknown",) else "",
                f"series {o.series}"] if x)
        elif slot.endswith("armet") or slot in ("armet", "armet_dx8",
                                                "mix_armet", "mix_armet_dx8"):
            kind = head_kind(ident)
            rec = self.facets.classify(ident)
            o.axes = {"class": rec.klass, "gender": rec.gender, "size": rec.size,
                      "kind": kind}
            if kind == "hair":
                colour = HAIR_COLOURS.get(int(ident[6])) if len(ident) == 9 else None
                o.name = f"Hairstyle {hair_style(ident)}"
                o.detail = colour or "hair"
                o.axes["quality"] = colour or ""
            else:
                o.name = nice or f"Headgear {o.series}"
                o.detail = " · ".join(x for x in [
                    rec.klass if rec.klass != "unknown" else "",
                    f"series {o.series}"] if x)
        elif slot in ("l_weapon", "r_weapon", "shield"):
            tname = self.weapon_type_names.get(o.series, "")
            prof = self.facets.series_profession(o.series)
            klass = bodyfacets.PROFESSION_CLASS.get(prof, "unknown") \
                if prof is not None else "unknown"
            o.axes = {"class": klass, "kind": "weapon",
                      "type": tname or f"type {o.series}",
                      "quality": ident[-1] if ident[-1].isdigit() else ""}
            o.name = nice or (tname or f"Weapon {o.series}")
            o.detail = " · ".join(x for x in [
                tname, f"quality {ident[-1]}" if ident[-1].isdigit() else "",
                klass if klass not in ("unknown", "any") else ""] if x)
        else:
            o.axes = {"kind": slot}
            o.name = nice or f"{SLOT_TITLES.get(slot, slot)} {ident}"
            o.detail = f"series {o.series}"
        o.axes = {k: v for k, v in o.axes.items() if v}
        return o

    # -- slot descriptors --------------------------------------------------
    def slot_info(self) -> list[dict]:
        """Every slot RolePart.ini declares, with why it is or is not usable."""
        out = []
        for s in self.manifest.ordered():
            opts = self.options.get(s.name, [])
            ini = self.tables.get(s.name)
            rows = len(ini) if ini else 0
            garments = len({o.mesh for o in opts})
            usable = bool(opts)
            # Both numbers in every sentence below are ENTRIES of `s.mesh_ini`
            # -- appearance rows -- tested one row at a time against that
            # row's own first part. They are never a per-mesh count: many rows
            # share one mesh and differ only in texture (`garments` below is
            # the distinct-mesh number, and on CCO's weapon.ini it is 437
            # against 5,384 rows). Saying "entries" and meaning meshes is how
            # a 12x discrepancy reads as a bug in the resolver.
            drop = self.dropped.get(s.name, {})
            no_mesh = drop.get("noMesh", 0)
            no_tex = drop.get("noTexture", 0)
            if not s.shipped:
                reason = (f"{s.mesh_ini} is declared by ini/RolePart.ini but does "
                          f"not ship in this build, so there is nothing to offer.")
            elif not usable:
                reason = (f"{s.mesh_ini} ships {rows} entries but none of them "
                          f"resolves to a mesh AND a texture the client can open "
                          f"({no_mesh} have no mesh, {no_tex} have a mesh but no "
                          f"texture), so equipping any of them would draw nothing.")
            elif len(opts) < rows * 0.2:
                reason = (f"only {len(opts)} of {rows} entries in {s.mesh_ini} "
                          f"resolve both a mesh and a texture that ship "
                          f"({no_mesh} of the rest have no mesh, {no_tex} have a "
                          f"mesh but no texture). Entries, not meshes: these "
                          f"{len(opts)} use {garments} distinct meshes.")
            else:
                reason = ""
            body_specific = self._body_specific(s.name)
            out.append({
                "name": s.name,
                "label": SLOT_TITLES.get(s.name, s.label),
                "rolePartLabel": s.label,
                "socket": s.socket,
                "hint": SLOT_HINTS.get(s.name, s.note or ""),
                "ini": s.mesh_ini,
                "motionIni": s.motion_ini,
                "shipped": s.shipped,
                "usable": usable,
                "reason": reason,
                # `rows` and `count` are both ENTRIES of mesh_ini; `garments`
                # is the distinct-mesh count behind `count`, and the two must
                # never be swapped in a sentence -- see the reasons above.
                "rows": rows,
                "count": len(opts),
                "garments": garments,
                "noMesh": no_mesh,
                "noTexture": no_tex,
                "bodySpecific": body_specific,
                "primary": s.name in PRIMARY_SLOTS,
                "aliases": s.aliases,
                "axes": sorted({a for o in opts for a in o.axes}),
            })
        return out

    def _body_specific(self, slot: str) -> bool:
        """Measured, not declared: are this slot's ids body-type prefixed?

        `armor.ini` 94.6%, `armet.ini` 100%, `weapon.ini` 0%.  Deciding from the
        data means a slot cannot be mis-declared body-specific and then filter
        everything away.
        """
        opts = self.options.get(slot) or []
        if not opts:
            return False
        n = sum(1 for o in opts if o.body_type)
        return n / len(opts) > 0.9

    # -- querying ----------------------------------------------------------
    def compatible(self, slot: str, body_type: str = "") -> list[Option]:
        """The options that fit a body, and nothing that does not."""
        opts = self.options.get(slot) or []
        if body_type and self._body_specific(slot):
            return [o for o in opts if o.body_type == body_type]
        return opts

    def query(self, slot: str, *, body_type: str = "", text: str = "",
              selected: Optional[dict] = None,
              tag_map: Optional[dict] = None, want_tags: Optional[set] = None,
              untagged: bool = False) -> dict:
        """Filter + cross-filtered facet counts + garments, in one pass.

        `selected` is axis -> set(values).  Within an axis the selection is OR,
        across axes AND -- the same rule the appearance browser already uses.
        Every count is computed with the *other* axes applied, so a chip's
        number is "how many would I get if I ticked this" and no chip can ever
        lead to an empty list.
        """
        selected = {k: set(v) for k, v in (selected or {}).items() if v}
        tag_map = tag_map or {}
        want_tags = {t.lower() for t in (want_tags or set())}
        pool = self.compatible(slot, body_type)
        q = (text or "").strip().lower()

        def text_ok(o: Option) -> bool:
            if not q:
                return True
            hay = (f"{o.ident} {o.name} {o.detail} {o.mesh} "
                   f"{' '.join(tag_map.get(f'app:{o.ident}', []))}").lower()
            return q in hay

        def tag_ok(o: Option) -> bool:
            have = tag_map.get(f"app:{o.ident}", [])
            if untagged and have:
                return False
            return not want_tags or want_tags.issubset({t.lower() for t in have})

        base = [o for o in pool if text_ok(o) and tag_ok(o)]

        def axis_ok(o: Option, skip: Optional[str] = None) -> bool:
            for axis, want in selected.items():
                if axis == skip:
                    continue
                if o.axes.get(axis) not in want:
                    return False
            return True

        matched = [o for o in base if axis_ok(o)]

        facets: dict[str, dict[str, int]] = {}
        for axis in AXES:
            counts: dict[str, int] = {}
            for o in base:
                v = o.axes.get(axis)
                if v is None or not axis_ok(o, skip=axis):
                    continue
                counts[v] = counts.get(v, 0) + 1
            if len(counts) > 1 or (axis in selected and counts):
                facets[axis] = counts

        tag_counts: dict[str, int] = {}
        for o in matched:
            for t in tag_map.get(f"app:{o.ident}", []):
                tag_counts[t] = tag_counts.get(t, 0) + 1

        return {"slot": slot, "bodyType": body_type,
                "total": len(matched), "pool": len(pool),
                "facets": facets, "facetLabels": AXIS_LABEL,
                "facetOrder": {"class": bodyfacets.CLASS_ORDER,
                               "gender": bodyfacets.GENDER_ORDER,
                               "size": bodyfacets.SIZE_ORDER},
                "tagCounts": dict(sorted(tag_counts.items(),
                                         key=lambda kv: (-kv[1], kv[0]))),
                "matched": matched}

    @staticmethod
    def garments(matched: Iterable[Option], tag_map: Optional[dict] = None) -> list[dict]:
        """Collapse appearances into one row per distinct mesh.

        The mesh is shared across colourways and only the texture changes, so
        this is the unit a person actually chooses: ~167 garments per body type
        instead of ~800 appearances.
        """
        tag_map = tag_map or {}
        groups: dict[str, dict] = {}
        for o in matched:
            g = groups.get(o.mesh)
            if g is None:
                g = groups[o.mesh] = {
                    "mesh": o.mesh, "name": o.name, "detail": o.detail,
                    "series": o.series, "bodyType": o.body_type,
                    "count": 0, "appearances": 0, "variants": [],
                    "_seen": {}, **o.axes}
                g["meshName"] = o.mesh.rsplit("/", 1)[-1]
            g["appearances"] += 1
            # **Deduplicate by look.** 1,082 of the 3,082 usable armor.ini rows
            # are a different appearance id resolving to exactly the same mesh
            # AND the same texture -- `002182320`, `002182420` ... `002186320`
            # are eight ids for one garment. Eight identical swatches is noise,
            # so the first id stands for the look and the rest ride along as
            # `aliases`: nothing is dropped, it just is not shown eight times.
            prev = g["_seen"].get(o.texture)
            if prev is not None:
                prev["aliases"].append(o.ident)
                continue
            v = {**o.to_json(), "aliases": [],
                 "tags": tag_map.get(f"app:{o.ident}", [])}
            g["_seen"][o.texture] = v
            g["count"] += 1
            g["variants"].append(v)
            # a named variant beats an unnamed one for the group label
            if len(g["name"]) < len(o.name):
                g["name"] = o.name
        out = list(groups.values())
        for g in out:
            g.pop("_seen", None)
            g["variants"].sort(key=lambda v: v["id"])
            g["texture"] = g["variants"][0]["texture"]
            g["id"] = g["variants"][0]["id"]
        out.sort(key=lambda g: (g.get("type") or "", g["name"] or "", g["mesh"]))
        return out

    # -- defaults ----------------------------------------------------------
    #: Series 132 is `Coat` / `Dress` -- the starting clothes, no class
    #: requirement.  A builder that opens on an empty stage is not useful, so it
    #: opens on one of these.
    DEFAULT_SERIES = ("132", "137", "131", "130")

    def default_body(self, body_type: str = "002") -> Optional[str]:
        opts = [o for o in self.options.get("body", []) if o.body_type == body_type]
        if not opts:
            opts = self.options.get("body", [])
        if not opts:
            return None
        for series in self.DEFAULT_SERIES:
            hits = sorted(o.ident for o in opts if o.series == series)
            if hits:
                return hits[0]
        return sorted(o.ident for o in opts)[0]

    def default_loadout(self, body_type: str = "002") -> dict:
        """Something on screen the moment the page opens."""
        body = self.default_body(body_type)
        out: dict[str, str] = {}
        if body:
            out["body"] = body
        hair = [o.ident for o in self.compatible("armet", body_type)
                if head_kind(o.ident) == "hair"]
        if hair:
            out["armet"] = sorted(hair)[0]
        return out

    # -- super weapon effects ---------------------------------------------
    #
    # **This used to be a filename scan and that was wrong.** The link is a
    # table lookup -- `Action3DEffect[999.999.<type>.<sub>]` -> `3DEffect.ini`
    # -> `EffectId0` -> `3DEffectObj.ini` -> the mesh -- and `blade/` is one of
    # **29** families. Guessing `c3/effect/<family>/<id>.c3` found 194 of them
    # and missed the rest; the table finds **796 weapon appearances with an
    # aura, 816 of 816 resolving**. `tools/superfx.py` (task #20) owns it and
    # is imported, never re-derived.

    def super_db(self):
        if self._superdb is None and superfxmod is not None:
            try:
                self._superdb = superfxmod.SuperFxDB(self.root)
            except Exception:                             # pragma: no cover
                self._superdb = False
        return self._superdb or None

    def super_effects(self) -> dict[str, str]:
        """appearance id -> its effect name, over every weapon this build has.

        Built once, on demand; it is a table walk over 5,384 appearances and
        only the builder page's "how many weapons glow" line needs the total.
        """
        if self._super is None:
            sdb = self.super_db()
            idx: dict[str, str] = {}
            if sdb is not None:
                seen: set[str] = set()
                for slot in ("r_weapon", "l_weapon"):
                    for o in self.options.get(slot, []):
                        if o.ident in seen:
                            continue
                        seen.add(o.ident)
                        try:
                            name = sdb.effect_name(o.ident)
                        except Exception:                 # pragma: no cover
                            continue
                        if name:
                            idx[o.ident] = name
            self._super = idx
        return self._super

    def set_effect_paths(self, paths: Iterable[str]) -> None:
        """Kept for callers that used to feed the effect tree in. The lookup no
        longer scans filenames, so this is now only a cache reset."""
        self._super = None

    def super_effect(self, ident: str, slot: str = "r_weapon") -> Optional[dict]:
        """The resolved aura of one weapon: name, family, timing, layers."""
        se = super_effect_for(self.super_db(), ident, slot)
        if se is None:
            return None
        layers = [{"index": L.index, "effectId": L.effect_id, "mesh": L.mesh,
                   "textureId": L.texture_id, "texture": L.texture,
                   "asb": L.asb, "adb": L.adb, "scale": L.scale,
                   "resolved": L.resolved} for L in se.layers]
        return {"appearance": ident, "slot": slot, "name": se.name,
                "family": se.family, "frames": se.frames,
                "frameIntervalMs": se.frame_interval_ms,
                "loopTime": se.loop_time, "endless": se.endless,
                "offset": list(se.offset), "layers": layers,
                "resolved": se.resolved,
                "mesh": (layers[0]["mesh"] if layers else None),
                "texture": (layers[0]["texture"] if layers else None)}

    # -- weapon quality ----------------------------------------------------

    def _by_ident(self, slot: str) -> dict:
        cache = getattr(self, "_ident_cache", None)
        if cache is None:
            cache = self._ident_cache = {}
        if slot not in cache:
            cache[slot] = {o.ident: o for o in self.options.get(slot, [])}
        return cache[slot]

    def has_aura(self, ident: str, slot: str = "r_weapon") -> bool:
        """Does this exact appearance carry an always-on effect? Table lookup."""
        sdb = self.super_db()
        if sdb is None:
            return False
        try:
            return bool(sdb.effect_name(ident))
        except Exception:                                 # pragma: no cover
            return False

    def quality_family(self, slot: str, ident: str) -> dict:
        r"""The five named qualities of one weapon, and what each one changes.

        The point of this is that the mesh is constant and only the texture
        moves, so switching quality is a *skin* change on the weapon you already
        chose -- and it is the only way to see the Super aura without hunting
        `410009` out of a list of 5,384.

        Outliers are reported, not forced into five: a family with one texture
        says so, a family with more than three says how many, and the `...0`
        row and any `...1`/`...2` rows are listed under `extra` because they are
        not qualities (see `ZERO_DIGIT_NOTE`).

        Every id offered here comes from `self.options`, so it has already been
        checked against disk -- the selector cannot equip something that does
        not render.
        """
        out: dict = {"id": ident, "slot": slot, "family": "",
                     "isWeapon": slot in ("l_weapon", "r_weapon", "shield"),
                     "qualities": [], "extra": [], "textureTiers": 0,
                     "mesh": None, "current": "", "note": QUALITY_NOTE,
                     "zeroNote": ZERO_DIGIT_NOTE, "auraNote": AURA_QUALITY_NOTE,
                     "auraQualities": [], "reason": ""}
        if not out["isWeapon"]:
            out["reason"] = "quality is a weapon-id property; this slot has none."
            return out
        fam = weapon_family(ident)
        by = self._by_ident(slot)
        if not fam:
            out["reason"] = (
                f"{ident} is not a 6-digit weapon id, so it carries no quality "
                f"digit. The 20 seven-digit ids in weapon.ini (1050000…) are one "
                f"mesh and one texture between them.")
            return out
        out["family"] = fam
        me = by.get(ident)
        out["mesh"] = me.mesh if me else None
        out["current"] = quality_of(ident)

        sibs = {k[5]: by[k] for k in by if len(k) == 6 and k[:5] == fam}
        if me is not None:
            sibs.setdefault(ident[5], me)
            # A family is ONE mesh. Where a sibling disagrees it is a different
            # weapon that happens to share five digits (`35004` is ten meshes,
            # not ten qualities), so it is excluded rather than offered as a
            # quality of something it is not.
            sibs = {d: o for d, o in sibs.items() if o.mesh == me.mesh}
        out["textureTiers"] = len({o.texture for o in sibs.values()})

        ini = self.tables.get(slot)
        ini_ids = {a.ident for a in ini} if ini is not None else set()

        def why_missing(digit: str) -> str:
            wid = fam + digit
            if wid not in ini_ids:
                return f"weapon.ini has no …{digit} id in family {fam}."
            other = by.get(wid)
            if other is None:
                return (f"weapon.ini lists {wid} but its mesh or texture does "
                        f"not ship, so equipping it would draw nothing.")
            return (f"weapon.ini's {wid} is a different mesh ({other.mesh}), so "
                    f"it is another weapon rather than another quality of this "
                    f"one.")

        def entry(digit, o, quality=""):
            return {"digit": digit, "id": o.ident, "quality": quality,
                    "label": QUALITY_LABEL.get(quality, f"id …{digit}"),
                    "texture": o.texture, "textureId": o.texture_id,
                    "mesh": o.mesh, "name": o.name,
                    "aura": self.has_aura(o.ident, slot)}

        for q in QUALITY_ORDER:
            pick = next((d for d in QUALITY_PREF[q] if d in sibs), None)
            if pick is None:
                out["qualities"].append(
                    {"quality": q, "label": QUALITY_LABEL[q], "id": None,
                     "available": False, "aura": False,
                     "reason": " ".join(why_missing(d)
                                        for d in QUALITY_PREF[q])})
                continue
            e = entry(pick, sibs[pick], q)
            e["available"] = True
            e["grades"] = sorted(d for d in QUALITY_PREF[q] if d in sibs)
            out["qualities"].append(e)
            if e["aura"]:
                out["auraQualities"].append(q)

        for d in NON_QUALITY_DIGITS:
            if d in sibs:
                out["extra"].append(entry(d, sibs[d]))

        # which qualities share a texture -- the honest version of "three tiers"
        groups: dict = {}
        for e in out["qualities"]:
            if e.get("available"):
                groups.setdefault(e["texture"], []).append(e["label"])
        out["textureGroups"] = [
            {"texture": t, "qualities": names} for t, names in groups.items()]
        n = len(out["textureGroups"])
        avail = sum(1 for e in out["qualities"] if e.get("available"))
        out["available"] = avail
        out["hasLadder"] = avail >= 2
        if avail < 2:
            out["shape"] = "no ladder"
            out["reason"] = (
                f"family {fam} offers {avail} of the five qualities on this "
                f"mesh, so there is no ladder to step through. The ids that do "
                f"exist are listed as they are.")
        elif n == 3:
            out["shape"] = "three tiers"
        elif n == 1:
            out["shape"] = "one look"
        elif n == 0:                                      # pragma: no cover
            out["shape"] = "none"
        else:
            out["shape"] = f"{n} tiers"
        return out

    # -- audit -------------------------------------------------------------
    def audit(self) -> dict:
        """The numbers the module docstring quotes, recomputed."""
        out: dict[str, dict] = {}
        for s in self.manifest.ordered():
            opts = self.options.get(s.name, [])
            ini = self.tables.get(s.name)
            ids = [a.ident for a in ini] if ini else []
            pref = sum(1 for i in ids if i[:3] in bodyfacets.BODY_TYPES)
            out[s.name] = {
                "ini": s.mesh_ini, "rows": len(ids), "usable": len(opts),
                "garments": len({o.mesh for o in opts}),
                "bodyPrefixed": (pref / len(ids)) if ids else 0.0,
                "bodySpecific": self._body_specific(s.name),
                "shipped": s.shipped,
            }
        out["_superEffects"] = {"weaponsWithAura": len(self.super_effects())}
        return out


# ---------------------------------------------------------------------------
# animation -- the action catalogue, and the seam tools/anim.py plugs into
# ---------------------------------------------------------------------------

try:                                                      # task #20, imported
    import anim as animmod                                # noqa: E402
except Exception:                                         # pragma: no cover
    animmod = None

try:                                                      # task #20, imported
    import superfx as superfxmod                          # noqa: E402
except Exception:                                         # pragma: no cover
    superfxmod = None


def anim_available() -> bool:
    return animmod is not None


#: What to offer in the action dropdown, and in what order. `anim.ACTIONS`
#: describes 75 codes; 12 of them are still `unknown`, and a person choosing
#: "what should my character be doing" does not want twelve unlabelled numbers.
#: They are offered under "Other", after the ones with real names, and never
#: silently dropped.
ACTION_GROUP_ORDER = ["idle", "move", "attack", "cast", "react", "emote",
                      "pose", "action", "death"]
ACTION_GROUP_LABEL = {
    "idle": "Standing", "move": "Moving", "attack": "Attacking",
    "cast": "Casting", "react": "Being hit", "emote": "Emotes",
    "pose": "Poses", "action": "Other", "death": "Dying",
}

#: The frame interval to start at. **NOT determinable from the shipped data.**
#: `tools/anim.py` settles on 41 ms because every effect bound to a body action
#: (401/402/403 swing, 900/901/903 cast) declares `FrameInterval=41`, which is
#: strong but indirect: it is the effect's rate, matched to the action it plays
#: over. 33 ms and 50 ms are the documented alternatives. So the UI ships a
#: speed control rather than presenting 41 as fact.
DEFAULT_FRAME_MS = getattr(animmod, "DEFAULT_FRAME_INTERVAL_MS", 41)

TIMING_NOTE = (
    "Frame rate is NOT recoverable from the data. 41 ms is tools/anim.py's "
    "reading — every effect bound to a body action declares FrameInterval=41 — "
    "but that is the effect's rate matched to the action, not the action's own; "
    "33 ms and 50 ms are the documented alternatives (docs/animation.md). Use "
    "the speed control rather than trusting the default.")

ROOT_MOTION_NOTE = (
    "Walk and run translate the root by exactly 0.00: they are in-place cycles "
    "and the client moves the character across the map, so playing them on the "
    "spot is correct, not a limitation. Jumps are different — 131 lifts body "
    "002 from 94 to 153.6 and back to 90.8 — and that arc is baked into the "
    "poses, so it plays as authored.")

MISSING_NOTE = (
    "1,100 of the 3,260 motion files ini/3dmotion.ini names are absent from "
    "this install (63% of all keys point at a missing file). The four player "
    "bodies are unaffected — 299 of 299 clips load and bind — so anything the "
    "builder needs is here; an action that is not says so rather than doing "
    "nothing.")


def actions_for(db, body: str, weapon: str = "", off_hand: str = "") -> list[dict]:
    """Every action this body+loadout can actually play, named in English.

    Resolution goes through `anim.AnimDB`, which means the *equipped weapon* is
    part of the lookup -- `3dmotion.ini` is keyed
    `<shape><weaponset><action>` and a 410 swing differs from the unarmed one by
    up to 115 units of pose on a 170-unit body. Asking for the body alone would
    animate an armed character as though it were empty-handed.
    """
    if animmod is None or db is None:
        return []
    out = []
    for code, meta in animmod.ACTIONS.items():
        path, how = db.resolve(db.shape_of(body),
                               db.weaponset(weapon, off_hand), code)
        if not path:
            continue
        chain = meta.chain
        named = meta.confidence != "unknown"
        out.append({
            "code": code,
            "label": meta.name[0].upper() + meta.name[1:] if meta.name else code,
            "group": meta.group,
            # the 12 codes anim.py could not name go in one clearly-labelled
            # bucket at the end rather than repeating every group heading
            "groupLabel": (ACTION_GROUP_LABEL.get(meta.group, meta.group.title())
                           if named else "Unidentified codes"),
            "confidence": meta.confidence,
            "evidence": meta.evidence,
            "chain": chain,
            "motion": path,
            "how": how,
            "named": named,
        })
    order = {g: i for i, g in enumerate(ACTION_GROUP_ORDER)}
    out.sort(key=lambda a: (not a["named"], order.get(a["group"], 99), a["code"]))
    return out


def super_effect_for(sdb, appearance: str, slot: str = "r_weapon"):
    """`superfx.SuperFxDB.super_effect`, or None. Never re-derived here.

    Two corrections from task #20 that this module used to get wrong:

    * The link is a **table lookup**, not a filename convention:
      `Action3DEffect[999.999.<type>.<sub>]` -> `3DEffect.ini` -> `EffectId0`
      -> `3DEffectObj.ini` -> the mesh. Guessing `c3/effect/<family>/<id>.c3`
      happened to work for blades and missed 28 other families.
    * It is far broader than the one blade example suggested: **796 weapon
      appearances carry an aura and 816 of 816 resolve.**
    """
    if superfxmod is None or sdb is None:
        return None
    try:
        return sdb.super_effect(appearance, slot)
    except Exception:                                     # pragma: no cover
        return None


SUPER_ANCHOR_NOTE = (
    "The effect is a SIBLING of the weapon at v_r_weapon, not a child: "
    "p_world = p_bind x M_fxBone x M_offset x M_socket x M_role, with the "
    "weapon's OWN bone-0 matrix deliberately left out. Composing it as well is "
    "exactly the 'off in space' bug — on 410009 that shrinks the glow from a "
    "span of 111.8 to 28.1, buried inside the grip, where the correct chain "
    "puts its hilt within 0.4 units of the blade's. Verified across 359 "
    "effect/weapon pairs (tools/superfx.py, docs/animation.md).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv) -> int:
    import argparse
    import json
    from coassets import AssetRoot

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--slots", action="store_true")
    ap.add_argument("--slot")
    ap.add_argument("--body", help="a body appearance id, for compatibility")
    ap.add_argument("--body-type", help='"001".."004"')
    ap.add_argument("--kind", help="filter one axis value, e.g. hair")
    ap.add_argument("--q", default="")
    ap.add_argument("--superfx", help="a weapon appearance id")
    ap.add_argument("--quality", help="a weapon appearance id: its quality ladder")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)

    root = Path(a.root)
    R = AssetRoot(root)
    tables = R.part_tables()
    bf = bodyfacets.BodyFacets(root, resolve=lambda i, k: (
        lambda l: l.logical if l else None)(R.resolve_asset(i, k)))
    bf.build(tables)
    idx = BuilderIndex(tables, lambda i, k: (lambda l: l.logical if l else None)(
        R.resolve_asset(i, k)), root, facets=bf, exists=R.exists)

    if a.audit:
        print(json.dumps(idx.audit(), indent=2))
        return 0
    if a.superfx:
        print(json.dumps(idx.super_effect(a.superfx), indent=2))
        return 0
    if a.quality:
        rec = idx.quality_family(a.slot or "r_weapon", a.quality)
        if not rec["isWeapon"] or not rec["family"]:
            print(rec["reason"])
            return 0
        print(f"family {rec['family']}  mesh {rec['mesh']}  "
              f"{rec['textureTiers']} texture(s)  [{rec['shape']}]")
        for q in rec["qualities"]:
            mark = "*" if q.get("id") == a.quality else " "
            if q.get("available"):
                print(f" {mark} {q['label']:<8} {q['id']:<8} {q['texture']:<28}"
                      f"{'  AURA' if q['aura'] else ''}")
            else:
                print(f" {mark} {q['label']:<8} {'—':<8} {q['reason']}")
        for e in rec["extra"]:
            print(f"   {'…' + e['digit']:<8} {e['id']:<8} {e['texture']:<28}"
                  f"{'  AURA' if e['aura'] else ''}")
        for g in rec["textureGroups"]:
            print(f"   share {g['texture']}: {' + '.join(g['qualities'])}")
        return 0
    if a.slots or not a.slot:
        print(f"{'slot':<14}{'ini':<18}{'rows':>6}{'usable':>8}{'garments':>10}"
              f"  body-specific")
        for s in idx.slot_info():
            print(f"  {s['name']:<12}{s['ini']:<18}{s['rows']:>6}{s['count']:>8}"
                  f"{s['garments']:>10}  {'yes' if s['bodySpecific'] else 'no':<4}"
                  f"  {s['reason'][:60]}")
        print(f"\n  default loadout: {idx.default_loadout()}")
        print(f"  weapons with an always-on effect: {len(idx.super_effects())}")
        return 0

    bt = a.body_type or (a.body[:3] if a.body else "")
    sel = {}
    if a.kind:
        sel = {"kind": {a.kind}}
    res = idx.query(a.slot, body_type=bt, text=a.q, selected=sel)
    gs = idx.garments(res["matched"])
    print(f"{a.slot}: {res['total']} of {res['pool']} options, "
          f"{len(gs)} distinct garments"
          + (f" (body {bt})" if bt else ""))
    for axis, counts in res["facets"].items():
        print(f"  {AXIS_LABEL.get(axis, axis)}: " +
              ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for g in gs[:a.limit]:
        print(f"    {g['name']:<28} {g['count']:>2} colour(s)  {g['mesh']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
