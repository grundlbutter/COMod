#!/usr/bin/env python3
r"""depclose.py -- what does a change to this asset BREAK?

`comod diff` answers "what files does my stage tree change". It has never been
able to answer the question a modder actually asks before installing: **who is
looking at this file?** This module is that answer, and `comod impact` is its
surface.

    py -3 tools/comod.py impact c3/weapon/440140.c3
    py -3 tools/comod.py impact 440140
    py -3 tools/comod.py diff --impact

---------------------------------------------------------------------------
THE ONE DESIGN RULE: A SILENTLY INCOMPLETE DEPENDENCY REPORT IS WORSE THAN
NONE, BECAUSE IT WILL BE TRUSTED.
---------------------------------------------------------------------------

Three facts about this corpus make an "is it referenced?" answer easy to get
confidently wrong, and every one of them is a separate field in the output
rather than a merged verdict:

* **A declaration is not a file.** `ini/c3.wdb` is SHIPPED and outlives the
  art it names -- `coassets.resolve_declared` measured 28 refs on 6907 naming
  a path the install does not ship. So every declaration this module reports
  carries `present`, and the summary counts DECLARED-AND-PRESENT and
  DECLARED-BUT-ABSENT separately.
* **A file is not a declaration.** Loose art can sit at a path no table names.
  That is `PRESENT BUT UNDECLARED`, and it is reported as its own status --
  never as "no references found", which is what a caller would otherwise read
  it as.
* **We cannot enumerate every reference class, and the ones we cannot are
  printed in the report itself.** `Impact.limits` is rendered under a
  `NOT ENUMERATED` heading on every single run, including the runs that find
  plenty. A count without its exclusions is the failure mode this project has
  hit repeatedly (`docs/CORRECTIONS.md`).

---------------------------------------------------------------------------
WHAT IS VERIFIED AND WHAT IS INFERRED
---------------------------------------------------------------------------

VERIFIED (measured by `tests/test_depclose.py`, numbers in its docstring;
`AssetRoot(root, profile=False)` throughout):

* `c3/mesh/440140.c3` is referenced by **exactly 149 appearance rows** of
  `weapon.ini` on 5065, 5517, 6609 AND 7205 -- four client generations, one
  number -- and `weapon.ini` backs two ROLE PARTS (`r_weapon`, `l_weapon`) on
  all four, so one file edit reaches both slots. 7878 is NOT in that list and
  the reason is not a defect here: it does not ship the file (see below).
* the row walk sees 44,183 appearance cells on 6609 and 7205 (22,066 `Mesh<i>`
  + 22,066 `Texture<i>` + 51 `MixTex<i>`), 27,153 on 5517, 13,094 on 5065 and
  13,902 on 7878, and the forward index buckets 43,244 / 26,596 / 12,844 /
  2,045 of them under a real path;
* `ROPT` in `ini/c3.wdb` decodes to the same part -> table mapping that
  `ini/RolePart.ini` gives, on every client that ships both -- checked part by
  part on 5165/5517/6090/6609/6907/7205/7632/7878;
* an install shipping no `c3.wdb` (5017, 5065) reports `ropt()` as None and
  says so in its own output; an install with no readable appearance table at
  all reports APPEARANCE REFERENCES as UNMEASURED rather than as 0
  (`tests/test_depclose.py::NoAppearanceTables`, a synthetic root -- CCO is
  the shipped client with no `ini/RolePart.ini`, and `AssetRoot` refuses that
  tree for a different reason, so it is not the evidence for this).

**7878 IS THE CASE THIS MODULE'S WARNINGS ARE FOR.** With its declared garment
profile off, 11,857 of its 13,902 appearance cells resolve to no file at all:
that client ships almost none of its own role art
(`docs/garment_missing_art_2026-08-28.md`). A closure that answered "0
references" about one of those paths and stopped there would be read as "safe
to change". So the dangling count is a line in every report, and a file served
out of another install is stamped FOREIGN rather than presented as art this
install ships.

INFERRED:

* **Attribution of a reference to a FILE goes through `coassets`' resolution
  model, which is itself INFERRED** -- `resolve_asset`'s own comment says the
  client's real rule "lives inside the Themida-packed exe and has not been
  read directly". So "id 440140 resolves to c3/weapon/440140.c3" is this
  toolchain's model of the client, not an observation of the client. Where a
  table DECLARES a path for an id and the model resolves that id somewhere
  else, both are printed and the row is marked SHADOWED rather than silently
  dropped.
* PHY/MOTI ordinal pairing is `docs/modding.md` 11; `c3tex.MotionBinding` is
  the default-deny classifier over it and its UNKNOWN verdict is passed
  through as UNKNOWN, never rounded to "safe".
"""
from __future__ import annotations

import argparse
import struct
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                              # noqa: E402
import c3phy                                               # noqa: E402
from coassets import AssetRoot, C3File, DEFAULT_ROOT, Located   # noqa: E402


def _phy_moti(c3: C3File) -> tuple[int, int]:
    r"""`(PHY count, MOTI count)` for one container.

    The PHY tag is spelled FIVE ways and one of them is `b"PHY "` -- with a
    trailing SPACE. Counting `tags().count("PHY")` therefore returns 0 on
    every file that uses the oldest variant, which is most of the old corpus,
    and the answer looks like "this mesh has no geometry" rather than like a
    bug. `c3phy.VARIANTS` is the one list of the five and is used here so a
    sixth variant cannot be added there and missed here.
    """
    phy = sum(1 for c in c3.chunks if c.tag in c3phy.VARIANTS)
    return phy, sum(1 for c in c3.chunks if c.tag == b"MOTI")


# ---------------------------------------------------------------------------
# ROPT -- the compiled part -> table mapping
# ---------------------------------------------------------------------------

#: Byte layout per `core/dbc.py`'s `section_span` for `ROPT`, which is
#: VALIDATED on 26 shipped clients plus the vendor authoring tool's own file:
#:
#:     0x00  char[4]  "ROPT"
#:     0x04  u32      recordCount
#:     0x08  u32      dumyCount        <- SECTION level, not per record
#:     0x0c  recordCount x 544 { char[32] part, char[256] meshIni,
#:                               char[256] motionIni }
#:     then  dumyCount x 36  { u32 index, char[32] name }
#:
#: `dbc.section_span` computes the span from exactly these numbers; this
#: reader decodes the records the span skips over. It is a READER, added
#: here rather than in `core/dbc.py`, so nothing already depending on that
#: module's surface can move because of it.
ROPT_RECORD = 544
ROPT_DUMY = 36


@dataclass
class RolePartRow:
    """One ROPT record: a role part and the two tables that back it."""
    part: str
    mesh_table: str
    motion_table: str

    @property
    def mesh_stem(self) -> str:
        return Path(self.mesh_table.replace("\\", "/")).stem.lower()

    @property
    def motion_stem(self) -> str:
        return Path(self.motion_table.replace("\\", "/")).stem.lower()


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin-1")


def read_ropt(db) -> Optional[tuple[list[RolePartRow], list[str]]]:
    """`(rows, dumy_names)` from a `wdb.ResourceDb`, or None if it has no ROPT.

    None means "this install's `c3.wdb` carries no ROPT section" and is
    **not** an empty list -- the caller reports the two differently, because
    "the compiled part mapping says there are no parts" and "there is no
    compiled part mapping" are different facts and only one of them is a
    statement about the client.
    """
    if db is None:
        return None
    try:
        data = Path(db.path).read_bytes()
    except OSError:
        return None
    for sec in getattr(db, "section_table", []):
        if sec.get("tag") != "ROPT":
            continue
        off = sec["offset"]
        n = sec["count"]
        (dumy,) = struct.unpack_from("<I", data, off + 8)
        pos = off + 12
        rows: list[RolePartRow] = []
        for _ in range(n):
            rows.append(RolePartRow(
                _cstr(data[pos:pos + 32]),
                _cstr(data[pos + 32:pos + 288]),
                _cstr(data[pos + 288:pos + 544])))
            pos += ROPT_RECORD
        names = []
        for _ in range(dumy):
            names.append(_cstr(data[pos + 4:pos + 36]))
            pos += ROPT_DUMY
        return rows, names
    return None


# ---------------------------------------------------------------------------
# result records
# ---------------------------------------------------------------------------

#: The three states a (table row, file) pair can be in. Kept as constants so
#: a caller cannot spell one of them differently from the renderer.
DECLARED_PRESENT = "declared-and-present"
DECLARED_ABSENT = "declared-but-absent"
UNDECLARED = "present-but-undeclared"
ABSENT = "absent-and-undeclared"


@dataclass
class Declaration:
    """One id -> path row, from one of the install's own tables."""
    table: str          # the file actually read, e.g. "ini/c3.wdb"
    asset_id: str
    path: str           # the path the table names
    present: bool       # does that path resolve in THIS install
    is_target: bool     # does it name the asset we asked about


@dataclass
class ResolvingId:
    """An id that could name the target, and where the model sends it."""
    asset_id: str
    kind: str           # "mesh" | "texture"
    lands_on: Optional[str]
    here: bool
    foreign: bool = False


@dataclass
class AppearanceRef:
    table: str          # "weapon.ini"
    parts: tuple        # role parts this table backs: ("l_weapon","r_weapon")
    ident: str
    part_index: int
    role: str           # "Mesh0" | "Texture0" | "MixTex0" | ...
    asset_id: str
    via: str            # "id" | "beside-mesh"


@dataclass
class EffectRef:
    effect: str
    layer: int
    role: str           # "mesh" | "texture"
    asset_id: str
    table: str          # the id->path table the layer went through


# ---------------------------------------------------------------------------
# effects -- the FORWARD closure
#
# `effect_index` above answers "which effects reach this file". Everything
# from here to the end of this section answers the other direction: "what does
# this effect need", which is the question a modder asks before touching a
# weapon. Both sit on `tools/effects.py`'s `EffectDB` -- there is exactly one
# effect walker in this repo and this is not a second one.
#
# THE PROVENANCE RULE, and it is why `table_file` exists at all.
# `EffectDB.sources` records which FILE answered for the four tables it reads
# through `_load_path_table` / `_load_effects`. This module reports numbers
# derived from those tables, so it reports the filename beside every one of
# them -- an id that resolves out of `3DEffectobj.dbc` and an id that resolves
# out of `3DEffectObj.ini` are different answers on the same base, and on
# 5517/6090/6609/7205 the dbc is the live one.
# ---------------------------------------------------------------------------

def table_file(db, stem: str) -> str:
    """The FILENAME that answered for one of `EffectDB`'s tables.

    Falls back to the table stem, never to silence. This replaced a
    `getattr(source, "read", None)` that could not succeed -- `TableSource`
    has no `read` attribute, so every `EffectRef` in this module's history was
    stamped with the generic stem and the report never once named the
    compiled twin it had actually read. The bug was invisible precisely
    because the fallback was plausible.
    """
    src = getattr(db, "sources", {}).get(stem)
    p = getattr(src, "path", None)
    return Path(str(p)).name if p is not None else stem


#: The three animation forms an effect mesh can hold, keyed by
#: `effects.EffectPart.kind`. Named here rather than in the renderer so the
#: JSON and the text output cannot drift apart.
FORM_LABEL = {
    "phy_motion": "PHY+MOTI (bone matrix track)",
    "shape_trail": "SHAP+SMOT (blade line smeared into a ribbon trail)",
    "particle": "PTCL/PTC3 (particle system)",
}

#: Effect tables `EffectDB` READS, by lowercase filename. `3DEffectObj.ini`
#: and `3dtexture.ini` are read too but as ID->PATH tables rather than as
#: rules, and both are shadowed by a `.dbc` where one exists -- that is
#: reported by `effect_tables`, not here.
READ_EFFECT_TABLES = {"action3deffect.ini", "actionmap3deffect.ini",
                      "weaponeffect.ini", "3deffect.ini", "3deffectobj.ini",
                      "3dtexture.ini", "3dobj.ini"}

#: Globs that catch the shipped effect table family, including the spellings
#: that differ only in case (`Action3Deffect2.ini` on 6609 and 7205).
EFFECT_SIBLING_GLOBS = ("[Aa]ction*3[Dd]*ffect*.ini", "3[Dd][Ee]ffect*.ini")

#: THE WEAPON/ACTION SPLIT, STATED AS DERIVED. `Action3DEffect.ini` carries
#: weapon auras and body-action effects in ONE table keyed identically, so
#: nothing in a row says which it is; the split is a predicate this module
#: applies, not a reading of the file.
#:
#: It is ONE STRING because two walks now make the split -- `effect_users`
#: (which effect does this subject play, in reverse) and `subject_effects`
#: (what else does this subject play) -- and they must state it identically
#: or a reader gets the classification from one and the caveat from neither.
#: Anything that files this string is claiming a DERIVED answer and saying
#: so; nothing that derives the split may skip filing it.
#:
#: RE-WORDED 2026-09-10, backlog item 25. It used to name `ini/weapon.ini`.
#: That file is FROZEN -- byte-identical on seven bases from 5165 to 7878 --
#: and where a compiled `weapon.dbc` twin exists the client reads the twin, so
#: the string named a file that on four bases decides nothing. `effects.py`
#: now reads the twin (`_weapon_appearances`). The split is still DERIVED; it
#: is derived from the right table.
DERIVED_SPLIT_LIMIT = (
    "the weapon/action split is DERIVED, not read: an Action3DEffect "
    "row is filed under WEAPONS when its appearance is declared in the "
    "weapon appearance table -- ini/weapon.dbc where the client ships "
    "one, ini/weapon.ini otherwise -- and under ACTIONS otherwise. The "
    "table itself does not say which a row is.")


def unread_effect_tables(root) -> list[dict]:
    r"""Effect-rule files this install SHIPS and `EffectDB` does not read.

    MEASURED on the read-only baseline, 2026-09-06 -- this is not a
    hypothetical hole. `rows` counts `key=value` lines, so it is the file's
    own size, not `EffectDB`'s parsed-row count:

        base   unread rule files                                     rows
        5017   (none)                                                   0
        5065   (none)                                                   0
        5165   (none)                                                   0
        5517   (none)                                                   0
        6090   Action3DEffect1/2/3/4/5.ini, ActionLee3DEffect.ini      674
        6609   Action3DEffect1/3/4/5.ini, Action3Deffect2.ini,
               ActionLee3DEffect.ini                                  1168
        7205   same six as 6609                                       1168
        7632   ActionLee3DEffect.ini, ActionRole3DEffect.ini           135
        7878   ActionLee3DEffect.ini, ActionRole3DEffect.ini           140

    plus `3DEffectInfo.ini` (11 lines, `[Header] Amount=0`) on all nine,
    reported under `family="other"` because its grammar is sectioned rather
    than dotted. The four oldest bases returning **(none)** is the control
    that says this function can come back empty: a scan that flagged
    something on every install would be flagging its own glob.

    7205 is the case that makes it matter: its `Action3DEffect.ini` parses to
    2,290 rules while the six unread files beside it hold 1,168 more lines.

    WHETHER THE CLIENT READS THEM IS A SEPARATE QUESTION and this function
    does not answer it. Measured against the unpacked binaries' own string
    tables (`grep -a` over every `.dll`/`.exe` of each install):
    `GameData.dll` names `ini/Action3DEffect.ini` and
    `ini/ActionMap3DEffect.ini` on 5017/5165/5517/6090/6609/7205, adds
    `ini/ActionRole3DEffect.ini` from 6090 on, and **names none of the
    numbered siblings**; 6090's `Conquer.exe` is the one binary that names
    `ActionLee3DEffect.ini`. 7632 and 7878 are Themida-packed and yield no
    strings at all, so for those two bases nothing is concluded either way.
    That is why these are reported as UNREAD rather than merged: merging a
    file the client may ignore would invent effects, and dropping the line
    would hide a file it may honour. `ActionRole3DEffect.ini` is the one this
    module would most expect to be live -- a binary names it, it ships on
    7632 and 7878, and `EffectDB` does not read it.

    WHY THEY ARE REPORTED AND NOT MERGED. Comparing the sibling keys against
    the read table's, key by key:

        base   sibling keys   absent from the read table   present, DISAGREEING
        6090        360                   20                       340
        6609        490                   19                       470
        7205        490                  463                        27
        7632         27                   23                         0
        7878         32                   28                         0

    **Both columns argue the same way.** 463 of 7205's 490 sibling keys name
    a rule the read table does not have at all, so ignoring the files loses
    them; and 470 of 6609's carry a DIFFERENT effect for a key the read table
    also has, so merging would overwrite 470 answers with no evidence about
    which spelling the client honours. Naming the files and their sizes is
    the only move that adds information without inventing any.
    """
    ini = Path(root) / "ini"
    if not ini.is_dir():
        return []
    seen: dict = {}
    for pat in EFFECT_SIBLING_GLOBS:
        for p in ini.glob(pat):
            key = p.name.lower()
            if key in READ_EFFECT_TABLES or key in seen or not p.is_file():
                continue
            try:
                rows = sum(1 for ln in p.read_bytes().splitlines()
                           if ln.strip() and not ln.lstrip().startswith(b"/")
                           and b"=" in ln)
            except OSError:                                # pragma: no cover
                rows = -1
            seen[key] = {"file": p.name, "rows": rows,
                         "bytes": p.stat().st_size,
                         "family": ("rule" if key.startswith("action")
                                    else "other")}
    return [seen[k] for k in sorted(seen)]


@dataclass
class EffectAsset:
    """One side of one layer: the id, the table that answered, the file."""
    role: str            # "mesh" | "texture"
    asset_id: str
    table: str           # "3DEffectObj" | "3dtexture"
    table_file: str      # the file that ACTUALLY answered on this base
    path: str = ""       # "" when the id resolves to no path at all
    present: bool = False
    source: str = ""     # archive or loose dir the file came out of
    foreign: bool = False


@dataclass
class EffectLayerView:
    index: int
    mesh: EffectAsset
    texture: EffectAsset
    blend: str = ""
    extra_texture_ids: list = field(default_factory=list)
    forms: list = field(default_factory=list)   # FORM_LABEL values
    parts: int = 0
    frames: int = 0
    effective_frames: int = 0
    undecoded: list = field(default_factory=list)
    error: str = ""
    #: True when the mesh id resolved to a path AND the geometry was read.
    #: `forms == []` with `geometry_read` False means UNKNOWN FORM, not "no
    #: animation" -- the same distinction `measured` draws one level up.
    geometry_read: bool = False


@dataclass
class EffectClosure:
    """What one named effect needs, and what could not be determined."""
    name: str
    root: str
    #: False when the effect tables would not load. **Every empty field below
    #: means UNMEASURED when this is False and means ZERO when it is True.**
    measured: bool = True
    found: bool = False
    source: str = ""             # "3DEffect.dbc" | "3DEffect.ini" | ...
    declared_layers: int = 0     # the table's own `Amount`
    layers: list = field(default_factory=list)
    delay: int = 0
    loop_time: int = 1
    loop_interval: int = 0
    frame_interval: int = 33
    endless: bool = False
    color_enable: bool = False
    offset: tuple = (0.0, 0.0, 0.0)
    duration_ms: Optional[float] = None
    duplicate_name: bool = False
    near: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    limits: list = field(default_factory=list)

    @property
    def missing_assets(self) -> int:
        n = 0
        for L in self.layers:
            n += sum(1 for a in (L.mesh, L.texture)
                     if a.asset_id and not a.present)
        return n


@dataclass
class EffectUse:
    """One rule row that NAMES an effect -- the REVERSE direction.

    `effect_closure` answers "what does this effect need". This answers "what
    reaches this effect", which is the question the Effects Viewer's
    connection panel asks and the one nothing in this module answered before.
    """
    category: str          # "weapon" | "action" | "map"
    role: str              # "aura" | "attack trail" | "hit spark" | ...
    table: str             # the rule table the row came out of
    table_file: str        # the file that ACTUALLY answered on this base
    label: str = ""        # one-line human rendering of the key
    shape: str = ""
    action: str = ""
    appearance: str = ""
    terrain: str = ""
    weapon_type: str = ""
    type_name: str = ""
    #: The effect name AS THE ROW SPELLS IT. Redundant when the caller
    #: already knows which effect it asked about, and load-bearing for
    #: `subject_effects`, where the row is the answer and the effect is the
    #: thing you would navigate to. Kept as the RAW spelling because the
    #: rule tables' case drifts from `3DEffect`'s and a UI that shows the
    #: normalised form is showing a string that is not in any file.
    effect: str = ""

    @property
    def effect_names(self) -> list:
        """`effect` PARSED -- `tools/effects.py::effect_names`.

        The raw spelling above is one string and, from 6907 on, it can name
        SEVERAL effects: `a,b,c~100~100~100~1`. 675 of 7878's 4,197 rule rows
        do, up to nineteen names. A consumer that treats `effect` as one name
        stages one effect whose name is a comma-separated list and finds no
        scene for it.

        A property, not a field, so `effect` stays raw: the `~parameters` are
        undecoded and whoever decodes them must not have to reconstruct the
        string this parse discarded.
        """
        from effects import effect_names as _parse          # noqa: PLC0415
        return _parse(self.effect)


@dataclass
class EffectUses:
    """Every rule row on this install that names one effect, by category.

    THE THREE STATES, same discipline as `EffectClosure`:

    * `measured=False` -- the effect tables would not load. The three lists
      below mean UNMEASURED, not empty.
    * `measured=True, defined=False` -- the rule tables were read and this
      name is not DEFINED in `3DEffect`. Rows may still point at it; a rule
      naming an undefined effect is a real and reported state.
    * `measured=True` with empty lists -- genuinely nothing reaches it
      THROUGH THE THREE TABLES `EffectDB` READS. `limits` carries the six
      sibling rule tables it does not read, so this is never a claim that
      nothing reaches it at all.
    """
    name: str
    root: str
    measured: bool = True
    defined: bool = False
    weapons: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    maps: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    limits: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.weapons) + len(self.actions) + len(self.maps)


@dataclass
class SubjectEffects:
    """Every effect ONE RULE SUBJECT plays -- the walk SIDEWAYS.

    `effect_closure` goes forward (what does this effect need), `effect_users`
    goes back (what reaches this effect). This is the third edge and the one
    that makes the reverse edge WALKABLE rather than merely readable: having
    landed on "weapon appearance 601000 plays this", the next question is
    always *what else does 601000 play* or *what else is a 601*, and neither
    was answerable without re-deriving the rule keys in the caller.

    TWO SUBJECT KINDS, and they are different questions:

    * ``kind="appearance"`` -- one appearance id. `rows` is every rule row
      whose key NAMES it, plus the `WeaponEffect` impact rows of its type.
    * ``kind="weapon_type"`` -- one weapon type (the leading digits). `rows`
      is the type's `WeaponEffect` rows; `appearances` is every appearance of
      that type **that `ini/weapon.ini` declares**, with what each plays.

    THE HOLES ARE FIELDS, NOT PROSE. `wildcard_rows` counts the rule rows
    keyed to NO appearance -- they apply to this subject too and are not in
    `rows` -- and `undeclared_appearances` counts appearance ids that rule
    rows of this type name and `ini/weapon.ini` does not declare. Both are
    counted whether or not anything renders them, because a caller that
    forgets to print a number still cannot print a wrong one.
    """
    subject: str
    kind: str                  # "appearance" | "weapon_type"
    root: str
    measured: bool = True
    appearance: str = ""
    weapon_type: str = ""
    type_name: str = ""
    in_weapon_ini: bool = False
    rows: list = field(default_factory=list)          # EffectUse
    appearances: list = field(default_factory=list)   # dicts, weapon_type kind
    #: rule rows keyed to no appearance at all; they apply here and are NOT
    #: listed in `rows`.
    wildcard_rows: int = 0
    #: appearance ids named by rule rows of this type that `ini/weapon.ini`
    #: does not declare -- the shadow the derived weapon/action split casts.
    undeclared_appearances: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    limits: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)


@dataclass
class WeaponEffects:
    """Every effect one weapon appearance can play, plus the holes."""
    appearance: str
    root: str
    measured: bool = True
    weapon_type: str = ""
    type_name: str = ""
    in_weapon_ini: bool = False
    aura: str = ""
    attack: dict = field(default_factory=dict)
    hit_effect: str = ""
    blk_effect: str = ""
    hit_sound: str = ""
    blk_sound: str = ""
    has_impact_row: bool = False
    closures: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    limits: list = field(default_factory=list)


@dataclass
class Impact:
    target: str
    root: str
    kind: str                       # "mesh" | "texture" | "other"
    status: str
    located: Optional[Located] = None
    declarations: list = field(default_factory=list)
    resolving_ids: list = field(default_factory=list)
    appearance_refs: list = field(default_factory=list)
    effect_refs: list = field(default_factory=list)
    anim: dict = field(default_factory=dict)
    limits: list = field(default_factory=list)
    #: One row per (role part, table) the references above go through, with
    #: `ROPT`'s compiled answer beside `ini/RolePart.ini`'s. See
    #: `DepGraph.role_part_rows`.
    role_parts: list = field(default_factory=list)
    #: True when `locate()` answered out of a DIFFERENT install (a fallback
    #: corpus or a garment overlay -- `Located.origin_root`). Kept beside
    #: `status` rather than folded into it: "this install ships it" and "this
    #: install declares it" are two axes, and a file can be present-because-
    #: borrowed while the install's own tables say nothing about it. Reported
    #: loudly because a borrowed file reads exactly like shipped art
    #: otherwise -- which is `Located.origin_root`'s own subject.
    foreign: bool = False
    #: Set when the caller is asking about a STAGED replacement rather than
    #: about the installed file: what the swap would do to PHY/MOTI pairing.
    staged: dict = field(default_factory=dict)

    @property
    def appearance_idents(self) -> set:
        return {(r.table, r.ident) for r in self.appearance_refs}

    @property
    def referenced(self) -> bool:
        return bool(self.appearance_refs or self.effect_refs)


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

class DepGraph:
    """Reverse indices over one installed client, built once and reused.

    Read-only in every direction: it opens an `AssetRoot`, walks the
    appearance tables and the effect tables, and never writes.
    """

    #: Roles a `PartRef` carries, in the order the ini spells them. `mesh` is
    #: matched against a `.c3` target and the other four against a `.dds`.
    MESH_ROLES = ("mesh",)
    TEX_ROLES = ("texture", "mix_tex", "third_tex", "fourth_tex")
    ROLE_LABEL = {"mesh": "Mesh", "texture": "Texture", "mix_tex": "MixTex",
                  "third_tex": "ThirdTex", "fourth_tex": "FourthTex"}

    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 assets: Optional[AssetRoot] = None, progress: bool = False):
        self.root = Path(root)
        #: Say on stderr that the slow pass has started. Off by default so a
        #: library caller and a test see nothing; the CLI turns it on.
        self.progress = progress
        self.assets = assets if assets is not None else AssetRoot(self.root)
        self._own = assets is None
        #: Every limit discovered while BUILDING the indices, carried into
        #: every report this graph produces. A source that could not be read
        #: is a hole in every answer, not just in the answer that touched it.
        self.build_limits: list[str] = []
        self._res_cache: dict = {}
        self._rows: Optional[list] = None
        self._forward: Optional[dict] = None
        self._tables: list = []
        self._effect_index: Optional[dict] = None
        self._effect_db = None
        self._effect_layers = 0
        self._effect_unresolved = 0
        self._effect_geom: dict = {}
        self._ropt: Optional[tuple] = None
        self._ropt_read = False
        #: THE SLOW PART IS NOT THE CONSTRUCTOR. `__init__` stores fields and
        #: returns; the ~28 s is `forward_index()` (and `effect_index()`), built
        #: lazily on first call. A viewer shares ONE graph across request
        #: threads, so without this every request that arrived during the
        #: build ran its own copy of it on the same object -- the 2+ cores for
        #: ~2 minutes measured in the owner's viewer 2026-09-19. Re-entrant,
        #: because `effect_index` and `forward_index` reach `rows()` and
        #: `effect_db()` from inside the lock.
        self._build_lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._own:
            try:
                self.assets.close()
            except Exception:                              # noqa: BLE001
                pass

    def __enter__(self) -> "DepGraph":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def norm(p: str) -> str:
        return str(p).replace("\\", "/").lstrip("/").lower()

    @staticmethod
    def canon(asset_id: str) -> str:
        """The one spelling of an id. `PartIni.get` tries three forms, so an
        index keyed on the literal text would miss two of them."""
        s = str(asset_id).strip()
        return s.lstrip("0") or ("0" if s else "")

    def resolve(self, asset_id: str, kind: str) -> Optional[Located]:
        key = (str(asset_id), kind)
        if key not in self._res_cache:
            try:
                self._res_cache[key] = self.assets.resolve_asset(
                    str(asset_id), kind)
            except Exception:                              # noqa: BLE001
                self._res_cache[key] = None
        return self._res_cache[key]

    # -- ROPT --------------------------------------------------------------
    def ropt(self) -> Optional[list]:
        """The compiled part -> table mapping, or None where there is none."""
        if not self._ropt_read:
            self._ropt_read = True
            try:
                out = read_ropt(self.assets.resource_db)
            except Exception as e:                         # noqa: BLE001
                self.build_limits.append(
                    f"ini/c3.wdb ROPT section would not decode "
                    f"({e.__class__.__name__}: {e}); the part -> table mapping "
                    f"below is ini/RolePart.ini's alone")
                out = None
            self._ropt = out
        return None if self._ropt is None else self._ropt[0]

    # -- appearance rows ---------------------------------------------------
    def rows(self) -> list:
        """Every `Mesh<i>` / `Texture<i>` / `MixTex<i>` / ... cell that names
        something, as `(table, parts, ident, part_index, role, id, mesh_id)`.

        Tables are de-duplicated BY FILE and the role parts they back are
        carried on the row, because `ini/RolePart.ini` names one file under
        several parts -- `armor.ini` backs both `body` and `mix_body`, and
        `weapon.ini` backs both `r_weapon` and `l_weapon`. Walking per PART
        would count the same table row twice and report 298 references to a
        mesh that 149 appearance rows name.

        `mesh_id` is the mesh of the SAME sub-part, carried on every row so
        the beside-the-mesh texture rule (see `forward_index`) can be applied
        without a second pass over the tables.
        """
        if self._rows is not None:
            return self._rows
        out: list = []
        by_file: dict = {}
        try:
            tables = self.assets.part_tables()
        except Exception as e:                             # noqa: BLE001
            self.build_limits.append(
                f"no appearance tables could be loaded "
                f"({e.__class__.__name__}: {e}) -- APPEARANCE REFERENCES is "
                f"EMPTY BECAUSE IT WAS NEVER MEASURED, not because there are "
                f"none")
            self._rows = out
            return out
        if not tables:
            self.build_limits.append(
                "ini/RolePart.ini names no table that this install ships; "
                "no appearance reference of any kind could be enumerated")
        for part, ini in sorted(tables.items()):
            by_file.setdefault(self.norm(str(ini.path)), [ini, []])[1].append(part)
        for _key, (ini, parts) in sorted(by_file.items()):
            tup = tuple(sorted(parts))
            self._tables.append((ini, tup))
            if getattr(ini, "twin_error", None):
                self.build_limits.append(
                    f"{ini.name}: its compiled twin {ini.twin} is present and "
                    f"would not parse ({ini.twin_error}); references below "
                    f"come from the STALE plaintext ini")
            for app in ini:
                for pr in app.parts:
                    for role in self.MESH_ROLES + self.TEX_ROLES:
                        v = getattr(pr, role, "") or ""
                        if not v or v == "0":
                            continue
                        out.append((ini.name, tup, app.ident, pr.index,
                                    role, v, pr.mesh or ""))
        self._rows = out
        return out

    def tables(self) -> list:
        self.rows()
        return self._tables

    # -- the forward index -------------------------------------------------
    def forward_index(self) -> dict:
        """`_build_forward_index`, built ONCE however many threads ask (see
        `_build_lock`): a caller arriving mid-build waits for it."""
        if self._forward is not None:
            return self._forward
        with self._build_lock:
            return self._build_forward_index()

    def _build_forward_index(self) -> dict:
        r"""`resolved path -> [AppearanceRef]`, by RESOLVING EVERY REFERENCE.

        **This is a forward pass, not a reverse guess, and the difference is
        the whole correctness of the module.** The obvious cheap design is to
        take the target path, derive the ids that "should" name it (its stem,
        plus whatever `c3.wdb` lists for it) and look those up. That design
        is silently incomplete, and this corpus has the counter-examples:

        * `coassets.resolve_asset` records that on 7878 the declaration
          re-points **28 mesh refs** at a file whose stem is a DIFFERENT id
          (`561000` -> `c3/weapon/800205.c3`), and 158 texture refs at art
          served out of another install entirely;
        * MEASURED here on 7878, appearance `4135000` of `armor.ini` names
          mesh `4143000`, which resolves to `c3/armet/7143000.c3`. Neither
          `7143000` nor that path is derivable from `4143000`, and the
          reverse-guess design reported **0** references to that file.

        So every reference is resolved once, through the same
        `resolve_asset` the rest of the toolchain uses, and bucketed by where
        it LANDS. The cost is one resolution per distinct (id, kind) --
        MEASURED 24s + 11s over 3,623 ids on 7878 and 18s + 15s over 6,991 on
        6609, cached for the life of the graph -- and it buys an answer whose
        completeness is bounded by the resolution model rather than by a
        naming convention.

        Two things are counted rather than dropped, and both become limits in
        the report: references that resolve to NOTHING (dangling in the
        install as shipped), and references served from ANOTHER install.
        """
        if self._forward is not None:
            return self._forward
        idx: dict = {}
        if self.progress:
            # STDERR, so a report piped to a file is unchanged. A command that
            # sits silent for half a minute reads as hung, and the answer to
            # that is to say what it is doing, not to make it less complete.
            print(f"[depclose] resolving every appearance reference in "
                  f"{self.root} -- this is the slow part", file=sys.stderr)
        unresolved = 0
        foreign = 0
        beside = 0
        rows = self.rows()
        for table, parts, ident, pidx, role, value, mesh_id in rows:
            kind = "mesh" if role == "mesh" else "texture"
            loc = self.resolve(value, kind)
            if loc is not None:
                if loc.foreign:
                    foreign += 1
                idx.setdefault(self.norm(loc.logical), []).append(AppearanceRef(
                    table=table, parts=parts, ident=ident, part_index=pidx,
                    role=f"{self.ROLE_LABEL[role]}{pidx}", asset_id=value,
                    via="id"))
                continue
            # THE BESIDE-THE-MESH TEXTURE, and it is not a convenience.
            #
            # From 7878 the texture is the MESH's own name with `.dds` on it
            # and the table's separate `Texture0` id resolves NOWHERE -- 0 of
            # 955 body and 0 of 1168 armet references exist under it
            # (`coassets.resolve_appearance`, which is the rule reproduced
            # here). An index that stopped at the failed texture id would
            # report ZERO references to every body texture on that client.
            hit = None
            if role == "texture" and mesh_id and mesh_id != "0":
                mloc = self.resolve(mesh_id, "mesh")
                if mloc is not None:
                    stem = mloc.logical.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    sub = mloc.logical.split("/")[1]
                    cand = f"c3/{sub}/{stem}.dds"
                    if self.assets.locate(cand) is not None:
                        hit = self.norm(cand)
            if hit is None:
                unresolved += 1
                continue
            beside += 1
            idx.setdefault(hit, []).append(AppearanceRef(
                table=table, parts=parts, ident=ident, part_index=pidx,
                role=f"Mesh{pidx} (texture beside the mesh)",
                asset_id=mesh_id, via="beside-mesh"))
        if rows:
            self.build_limits.append(
                f"{unresolved} of {len(rows)} appearance references resolve "
                f"to no file at all in this install; they are dangling as "
                f"shipped and appear under NO path's report -- so a count of "
                f"0 here never means 'the table is clean'")
            if foreign:
                self.build_limits.append(
                    f"{foreign} of {len(rows)} appearance references resolve "
                    f"to art served from ANOTHER install (a fallback corpus "
                    f"or a garment overlay); they are indexed under the "
                    f"borrowed path, which THIS install does not ship")
            if beside:
                self.build_limits.append(
                    f"{beside} of {len(rows)} texture references were "
                    f"attributed by the beside-the-mesh rule rather than by "
                    f"their own Texture<i> id, which resolved to nothing")
        self._forward = idx
        return idx

    # -- role part mapping -------------------------------------------------
    def role_part_rows(self, tables: set) -> list:
        r"""Which ROLE PART each referencing table backs, from both sources.

        `ini/RolePart.ini` is the plaintext mapping `AssetRoot.part_tables`
        already reads; `ROPT` inside `ini/c3.wdb` is the same mapping in
        compiled form, and it carries a column the plaintext does not put in
        front of a modder: **the MOTION table**. That column is the answer to
        "which shared motion set animates the thing I am about to edit", and
        `docs/modding.md` 11 is why that matters -- a PHY binds to a MOTI by
        ordinal, and those sets are shared across thousands of appearances.

        `ROPT` names its tables `.dbc` where `RolePart.ini` names them `.ini`
        (direct vendor confirmation of the ini -> dbc compilation, see
        `docs/c3tools_discovery_2026-09-05.md` 4.3), so the two are compared
        by STEM. A part with no ROPT row is reported as `-- no ROPT row --`,
        never as agreement.
        """
        rows = self.ropt()
        by_part = {}
        if rows is not None:
            for r in rows:
                by_part[r.part.strip().lower()] = r
        out: list = []
        for ini, parts in self.tables():
            if ini.name not in tables:
                continue
            stem = Path(ini.name).stem.lower()
            for part in parts:
                r = by_part.get(part.lower())
                if rows is None:
                    state, motion = "no ROPT section in this install", ""
                elif r is None:
                    state, motion = "-- no ROPT row for this part --", ""
                elif r.mesh_stem == stem:
                    state, motion = "ROPT agrees", r.motion_table
                else:
                    state = f"ROPT DISAGREES: it backs this part with {r.mesh_table}"
                    motion = r.motion_table
                out.append({"part": part, "table": ini.name,
                            "source": ini.source, "ropt": state,
                            "motion": motion})
        return out

    # -- effect index ------------------------------------------------------
    def effect_db(self):
        if self._effect_db is None:
            ini = self.root / "ini"
            if not ini.is_dir():
                self.build_limits.append(
                    f"no {ini} directory on disk: the effect tables "
                    f"(3DEffect / 3DEffectObj / 3dtexture) were NOT read, so "
                    f"EFFECT LAYER REFERENCES is unmeasured, not zero")
                self._effect_db = False
                return None
            try:
                import effects                             # noqa: PLC0415
                self._effect_db = effects.EffectDB(
                    self.root, assets=self.assets)
            except Exception as e:                         # noqa: BLE001
                self.build_limits.append(
                    f"the effect tables would not load "
                    f"({e.__class__.__name__}: {e}); EFFECT LAYER REFERENCES "
                    f"is unmeasured, not zero")
                self._effect_db = False
        return self._effect_db or None

    def effect_index(self) -> dict:
        """`_build_effect_index`, built ONCE however many threads ask (see
        `_build_lock`)."""
        if self._effect_index is not None:
            return self._effect_index
        with self._build_lock:
            return self._build_effect_index()

    def _build_effect_index(self) -> dict:
        """`normalised path -> [EffectRef]` over every layer of every effect.

        An effect layer names its mesh through `ini/3DEffectObj.ini` and its
        texture through `ini/3dtexture.ini`, both of which are id -> PATH
        tables -- so this index is keyed on the PATH directly and needs no
        resolution model at all. That makes it the one class of reference in
        this module that is not downstream of an inference.
        """
        if self._effect_index is not None:
            return self._effect_index
        idx: dict = {}
        db = self.effect_db()
        if db is None:
            self._effect_index = idx
            return idx
        n_layers = 0
        unresolved = 0
        for name, e in db.effects.items():
            for lay in e.layers:
                n_layers += 1
                hit = False
                for role, table, ident in (
                        ("mesh", "3DEffectObj", lay.effect_id),
                        ("texture", "3dtexture", lay.texture_id)):
                    src = db.objs if role == "mesh" else db.textures
                    raw = db._table_get(src, ident) if ident else ""
                    if not raw:
                        continue
                    hit = True
                    idx.setdefault(self.norm(raw), []).append(EffectRef(
                        effect=name, layer=lay.index, role=role,
                        asset_id=ident, table=table_file(db, table)))
                if not hit:
                    unresolved += 1
        self._effect_layers = n_layers
        self._effect_unresolved = unresolved
        if unresolved:
            self.build_limits.append(
                f"{unresolved} of {n_layers} effect layers name an id that "
                f"neither ini/3DEffectObj nor ini/3dtexture resolves to a "
                f"path; those layers cannot appear in any report, whatever "
                f"they point at")
        self._effect_index = idx
        return idx

    #: The three tables an effect answer stands on. `3dobj` is reported
    #: beside them but no effect layer goes through it.
    EFFECT_TABLES = ("3DEffect", "3DEffectObj", "3dtexture")

    def effect_tables_missing(self) -> list[str]:
        r"""Which of `EFFECT_TABLES` this install does not have a file for.

        `EffectDB` reads an absent ini as an EMPTY table -- `read_sections`
        on a missing path returns `{}` and nothing raises. That is the
        zero-byte failure mode `core/dbcshadow.py` names: an empty table is
        indistinguishable from an absent feature, and every downstream count
        is correspondingly smaller with no indication that anything happened.
        `TableSource.form` is `"missing"` in exactly that case, so this asks
        it rather than inferring absence from a count of zero.
        """
        db = self.effect_db()
        if db is None:
            return list(self.EFFECT_TABLES)
        return [s for s in self.EFFECT_TABLES
                if getattr(db.sources.get(s), "form", "missing") == "missing"]

    def effect_index_measured(self) -> bool:
        """Did the effect index get BUILT, as opposed to coming out empty?

        `effect_index()` returns `{}` in three situations that must not read
        alike: this install has 0 effect layers naming a path; the tables
        would not load at all; and the table FILES are not there, which
        `EffectDB` reports as empty rather than as an error. Only the first
        is a measurement. See `EffectClosure.measured`.
        """
        self.effect_index()
        if self._effect_db in (None, False):
            return False
        return not self.effect_tables_missing()

    # -- effect tables: which FILE answered ---------------------------------
    def effect_tables(self) -> list[dict]:
        r"""One row per effect table, naming the file that actually answered.

        Printed above every effect report this module produces, empty or not.
        The four tables `EffectDB` records a `TableSource` for carry a `form`
        of `dbc`/`ini`/`missing` and the twin they did NOT read; the three
        rule tables it reads with `read_sections`/`read_dotted` carry no such
        record, so their twin state is asked here directly through
        `dbcshadow.compiled_twin` rather than left blank.

        MEASURED on the read-only baseline, 2026-09-06: **no base in the
        corpus ships a compiled twin of `Action3DEffect.ini`,
        `ActionMap3DEffect.ini` or `WeaponEffect.ini`** -- the twins are
        `3DEffect.dbc`, `3DEffectobj.dbc`, `3DTexture.dbc` and `3DObj.dbc`
        only, and only on 5517/6090/6609/7205. So "read from the plaintext"
        is the CORRECT answer for the rule tables on every base, and the row
        says `no compiled twin` rather than leaving a reader to wonder.
        """
        db = self.effect_db()
        rows: list[dict] = []
        if db is None:
            return rows
        import dbcshadow                                   # noqa: PLC0415
        for stem in ("3DEffect", "3DEffectObj", "3dtexture", "3dobj"):
            src = db.sources.get(stem)
            if src is None:                                # pragma: no cover
                continue
            note = src.note
            if src.twin is None and src.form != "dbc":
                # Stated, not left blank. "read from the plaintext" and "read
                # from the plaintext BECAUSE there is no twin" are the same
                # row without this line, and only the second is a finding.
                note = ("no compiled twin on this base"
                        + (f"; {note}" if note else ""))
            if src.form == "missing":
                note = ("THE FILE IS NOT THERE; EffectDB read it as an empty "
                        "table" + (f"; {src.note}" if src.note else ""))
            d = {"table": stem, "form": src.form,
                 "file": src.path.name if src.path else None,
                 "rows": src.rows, "note": note}
            if src.twin is not None:
                d["not_read"] = src.twin.name
            rows.append(d)
        for name, count in (("Action3DEffect.ini", len(db.action_rules)),
                            ("ActionMap3DEffect.ini", len(db.action_map)),
                            ("WeaponEffect.ini", len(db.weapon_impact))):
            p = self.root / "ini" / name
            twin = dbcshadow.compiled_twin(p)
            rows.append({
                "table": p.stem, "file": p.name if p.is_file() else None,
                "form": "ini" if p.is_file() else "missing", "rows": count,
                "note": (f"compiled twin {twin.name} EXISTS and this reader "
                         f"does not use it" if twin is not None
                         else "no compiled twin on this base"),
                **({"not_read": twin.name} if twin is not None else {})})
        return rows

    def effect_table_limits(self) -> list[str]:
        """Holes that belong to EVERY effect report on this install."""
        out: list[str] = []
        db = self.effect_db()
        if db is None:
            return list(self.build_limits)
        gone = self.effect_tables_missing()
        if gone:
            out.append(
                "this install has no file for " + ", ".join(gone)
                + " -- EffectDB reads an absent table as an EMPTY one, so "
                  "every effect count on this install is UNMEASURED rather "
                  "than zero")
        unread = unread_effect_tables(self.root)
        rules = [u for u in unread if u["family"] == "rule"]
        other = [u for u in unread if u["family"] != "rule"]
        if rules:
            out.append(
                "this install ships "
                + ", ".join(f"ini/{u['file']} ({u['rows']} rows)"
                            for u in rules)
                + " -- action->effect rule files with the same dotted "
                  "grammar as Action3DEffect.ini that EffectDB does NOT "
                  "read. No rule in them appears anywhere in this report. "
                  "See depclose.unread_effect_tables for what the binaries' "
                  "string tables do and do not name.")
        if other:
            out.append(
                "this install ships "
                + ", ".join(f"ini/{u['file']} ({u['rows']} rows)"
                            for u in other)
                + " -- further effect table(s) EffectDB does not read")
        if db.duplicate_effect_names:
            out.append(
                f"{len(db.duplicate_effect_names)} effect name(s) appear "
                f"more than once in the definition table; the LAST record "
                f"wins here and which one the client uses is not decidable "
                f"from these files")
        if db.json_only:
            out.append(
                f"{len(db.json_only)} effect name(s) come from the shipped "
                f"ini/3DEffect.json export, not from a live table")
        self.effect_index()
        if self._effect_unresolved:
            out.append(
                f"{self._effect_unresolved} of {self._effect_layers} effect "
                f"layers in this install name an id that neither "
                f"3DEffectObj nor 3dtexture resolves to a path")
        out.append(
            "an effect reached from the client executable, from a skill "
            "table, from npc/monster/scene data or from MediaEffect is not "
            "enumerated here; only the weapon and action-rule paths are")
        return out

    # -- effects: the forward closure --------------------------------------
    def _effect_asset(self, db, role: str, ident: str) -> EffectAsset:
        stem = "3DEffectObj" if role == "mesh" else "3dtexture"
        a = EffectAsset(role=role, asset_id=str(ident or ""), table=stem,
                        table_file=table_file(db, stem))
        if not ident:
            return a
        raw = db._table_get(db.objs if role == "mesh" else db.textures, ident)
        if not raw:
            return a
        a.path = raw.replace("\\", "/")
        loc = self.assets.locate(a.path)
        a.present = loc is not None
        if loc is not None:
            a.source = loc.source
            a.foreign = bool(loc.foreign)
        return a

    def _geometry(self, db, logical: str):
        if logical not in self._effect_geom:
            import effects                                 # noqa: PLC0415
            self._effect_geom[logical] = effects.load_effect_object(
                self.assets, "", logical)
        return self._effect_geom[logical]

    def effect_closure(self, name: str, *, geometry: bool = True
                       ) -> EffectClosure:
        r"""Everything one named effect needs, with the holes named.

        The three states a caller must be able to tell apart, and they are
        three DIFFERENT field settings rather than three empty lists:

        * `measured=False` -- the effect tables would not load on this
          install (no `ini/` on disk, or `EffectDB` raised). Nothing below
          means anything and `limits` says why.
        * `measured=True, found=False` -- the tables loaded and hold no
          record under this name. `near` carries substring matches.
        * `measured=True, found=True, declared_layers=0` -- the record EXISTS
          and declares no layers. Three effects on 6090/6609/7205 and one on
          5517 are genuinely in this state; it is a fact about the table, not
          a failure to read it.
        """
        db = self.effect_db()
        c = EffectClosure(name=name, root=str(self.root))
        if db is None:
            c.measured = False
            c.limits = list(self.build_limits)
            return c
        c.tables = self.effect_tables()
        c.limits = self.effect_table_limits()
        if self.effect_tables_missing():
            c.measured = False
            return c
        e = db.resolve(name)
        if e is None:
            c.near = [n for n in db.effects if name.lower() in n.lower()][:12]
            return c
        c.found = True
        c.source = e.source
        c.declared_layers = e.amount
        c.delay, c.loop_time = e.delay, e.loop_time
        c.loop_interval, c.frame_interval = e.loop_interval, e.frame_interval
        c.endless, c.color_enable = e.endless, e.color_enable
        c.offset = tuple(e.offset)
        c.duplicate_name = name in set(db.duplicate_effect_names)
        for lay in e.layers:
            view = EffectLayerView(
                index=lay.index,
                mesh=self._effect_asset(db, "mesh", lay.effect_id),
                texture=self._effect_asset(db, "texture", lay.texture_id),
                blend=f"{lay.asb_name} -> {lay.adb_name}",
                extra_texture_ids=list(lay.extra_texture_ids))
            if geometry and view.mesh.present:
                obj = self._geometry(db, view.mesh.path)
                view.geometry_read = not obj.error
                view.forms = [FORM_LABEL.get(k, k) for k in sorted(obj.kinds)]
                view.parts = len(obj.parts)
                view.frames = obj.frame_count
                view.effective_frames = obj.effective_frames
                view.undecoded = list(obj.undecoded)
                view.error = obj.error
            c.layers.append(view)
        if len(c.layers) != c.declared_layers:
            c.limits.append(
                f"the definition declares {c.declared_layers} layer(s) and "
                f"{len(c.layers)} were parsed")
        if geometry and not c.endless:
            frames = max([(L.effective_frames or L.frames)
                          for L in c.layers] or [0])
            if frames:
                c.duration_ms = (c.delay + c.loop_time * frames
                                 * c.frame_interval
                                 + max(0, c.loop_time - 1) * c.loop_interval)
        return c

    def effect_users(self, name: str) -> EffectUses:
        r"""Which rule rows NAME this effect -- weapons, skills/actions, maps.

        The reverse of `effect_closure`, and the answer the Effects Viewer's
        connection panel renders. `DepGraph.impact()` inverts the FILE graph;
        this inverts the RULE tables, which the file graph does not contain --
        a rule row names an effect by NAME, never by path, so no amount of
        walking files reaches it.

        THE WEAPON / ACTION SPLIT IS DERIVED, AND IT IS NAMED AS DERIVED.
        `Action3DEffect.ini` carries weapon auras and body-action effects in
        ONE table keyed the same way, so nothing in a row says which it is.
        The split here is *"is this row's appearance a section in
        `ini/weapon.ini`"* -- the same predicate `effects_for_weapon` uses for
        `in_weapon_ini`. A row whose appearance is all-nines pins no
        appearance at all and so cannot be attributed to a weapon; it is filed
        under ACTIONS and its label says the key was a wildcard. This is a
        classification, not a reading of the file, and `limits` says so.

        NAME MATCHING IS CASE-INSENSITIVE and that is not a convenience.
        `3DEffect` keys and the rule tables' `Effect=` values are written by
        hand in different files and their case does drift; an exact-only match
        drops real rows and drops them silently. Every row kept records the
        spelling it actually carried in `label` when it differs from `name`.
        """
        db = self.effect_db()
        u = EffectUses(name=name, root=str(self.root))
        if db is None:
            u.measured = False
            u.limits = list(self.build_limits)
            return u
        u.tables = self.effect_tables()
        u.limits = self.effect_table_limits()
        if self.effect_tables_missing():
            u.measured = False
            return u
        u.defined = name in db.effects
        want = name.strip().lower()
        if not want:
            return u

        import effects                                      # noqa: PLC0415
        weapon_apps = set(db.weapon_appearances)
        # The file that ANSWERED, taken from the provenance rows rather than
        # from `db.sources`. `table_file` is right for the four ID->PATH
        # tables and WRONG here: `EffectDB` records no `TableSource` for the
        # three RULE tables, so `table_file` falls back to the stem and every
        # row would be stamped with a plausible name that is not a filename.
        # That is the exact shape of the bug `table_file`'s own docstring
        # records -- a fallback too plausible to notice -- so this asks
        # `effect_tables()`, which resolves the rule tables' twin state
        # directly through `dbcshadow`.
        # Keyed by `p.stem`, which is what `effect_tables` writes, and the
        # miss says "(absent)" rather than naming a file. `file` is None
        # exactly when the .ini is NOT on disk -- `EffectDB` reads an absent
        # table as an empty one, so a stamp that named the file anyway would
        # attribute rows to a file that is not there.
        by_table = {r["table"]: (r.get("file") or "(absent)") for r in u.tables}
        af = by_table.get("Action3DEffect", "(absent)")
        mf = by_table.get("ActionMap3DEffect", "(absent)")
        wf = by_table.get("WeaponEffect", "(absent)")

        def spelling(raw: str) -> str:
            return "" if raw == name else f"  [spelled {raw!r} in the table]"

        for r in db.action_rules:
            if (r.effect or "").strip().lower() != want:
                continue
            aura = effects.is_always_on(r.action)
            app = r.appearance
            pinned = not (effects.all_nines(r.group_hi)
                          and effects.all_nines(r.group_lo))
            is_weapon = pinned and app in weapon_apps
            role = "aura (always-on)" if aura else f"action {r.action}"
            if is_weapon:
                label = (f"weapon appearance {app}, {role}"
                         + spelling(r.effect))
                u.weapons.append(EffectUse(
                    category="weapon", role=role, table="Action3DEffect",
                    table_file=af, label=label, shape=r.shape,
                    action=r.action, appearance=app, effect=r.effect,
                    weapon_type=db.split_appearance(app)[0],
                    type_name=db.weapon_type_names.get(
                        db.split_appearance(app)[0], "")))
            else:
                who = (f"appearance {app}" if pinned
                       else "ANY appearance (wildcard key)")
                u.actions.append(EffectUse(
                    category="action", role=role, table="Action3DEffect",
                    table_file=af,
                    label=f"{who}, shape {r.shape}, {role}" + spelling(r.effect),
                    shape=r.shape, action=r.action, appearance=app,
                    effect=r.effect))

        for r in db.action_map:
            if (r.effect or "").strip().lower() != want:
                continue
            u.maps.append(EffectUse(
                category="map", role="map effect", table="ActionMap3DEffect",
                table_file=mf,
                label=(f"shape {r.shape}, action {r.action}, terrain "
                       f"{r.terrain}"
                       + (", at motion END" if r.show_time else
                          ", at motion start")
                       + (", rotates with facing" if r.dir_enable else "")
                       + spelling(r.effect)),
                shape=r.shape, action=r.action, terrain=r.terrain,
                effect=r.effect))

        for wt, w in sorted(db.weapon_impact.items()):
            for role, raw in (("hit spark", w.hit_effect),
                              ("block spark", w.blk_effect)):
                if (raw or "").strip().lower() != want:
                    continue
                tn = db.weapon_type_names.get(wt, "")
                u.weapons.append(EffectUse(
                    category="weapon", role=role, table="WeaponEffect",
                    table_file=wf,
                    label=(f"weapon type {wt}"
                           + (f" ({tn})" if tn else "")
                           + f", {role} -- drawn at the TARGET, never on the "
                             f"character" + spelling(raw)),
                    weapon_type=wt, type_name=tn, effect=raw))

        u.limits.append(DERIVED_SPLIT_LIMIT)
        return u

    def subject_effects(self, *, appearance: str = "",
                        weapon_type: str = "") -> SubjectEffects:
        r"""What ONE rule subject plays -- the walk that makes the reverse
        edge navigable.

        `effect_users(name)` answers "who reaches this effect" and lands you
        on a SUBJECT: an appearance id, or a weapon type. This answers the
        next question -- *what else does that subject play* / *what else is
        that type* -- and it must be answered HERE rather than in a page,
        because the appearance-key grammar (hi/lo split, the low-group
        wildcard, the all-nines sentinel) is this module's, and a second
        implementation of it in JavaScript would drift silently.

        THE SAME DERIVED SPLIT, AND THE SAME STATEMENT OF IT. Which rows are
        "a weapon's" is `ini/weapon.ini` membership, exactly as in
        `effect_users`; both file `DERIVED_SPLIT_LIMIT`, the one string, so
        neither can lose it while the other keeps it.

        THE TWO HOLES ARE COUNTED, NOT DESCRIBED:

        * `wildcard_rows` -- rows keyed to no appearance at all. They apply
          to every appearance INCLUDING this one, `rules_for_appearance`
          does not return them, and listing them per subject would repeat
          the whole wildcard table under every id. Counted so the caller can
          say "and N that apply to everything" instead of implying `rows` is
          the whole set.
        * `undeclared_appearances` -- appearance ids that rule rows of this
          type NAME and `ini/weapon.ini` does not declare. These are exactly
          the rows the derived split files under ACTIONS, so a "every
          appearance of this type" list built from `weapon.ini` alone would
          be short by them and look complete. They are returned, not just
          counted.
        """
        db = self.effect_db()
        app = str(appearance or "").strip()
        wt_in = str(weapon_type or "").strip()
        s = SubjectEffects(
            subject=app or wt_in,
            kind="appearance" if app else "weapon_type",
            root=str(self.root), appearance=app, weapon_type=wt_in)
        if db is None:
            s.measured = False
            s.limits = list(self.build_limits)
            return s
        s.tables = self.effect_tables()
        s.limits = self.effect_table_limits()
        if self.effect_tables_missing():
            s.measured = False
            return s
        if not (app or wt_in):
            s.measured = False
            s.limits.append(
                "no subject was given: pass an appearance id or a weapon "
                "type. An empty answer here is a bad call, not a finding.")
            return s

        by_table = {r["table"]: (r.get("file") or "(absent)") for r in s.tables}
        af = by_table.get("Action3DEffect", "(absent)")
        wf = by_table.get("WeaponEffect", "(absent)")
        weapon_apps = set(db.weapon_appearances)

        import effects                                      # noqa: PLC0415

        if app:
            s.weapon_type = db.split_appearance(app)[0]
            s.in_weapon_ini = app in weapon_apps
        s.type_name = db.weapon_type_names.get(s.weapon_type, "")

        # -- the appearance's own rule rows -------------------------------
        if app:
            for r in db.rules_for_appearance(app):
                aura = effects.is_always_on(r.action)
                role = "aura (always-on)" if aura else f"action {r.action}"
                is_weapon = app in weapon_apps
                s.rows.append(EffectUse(
                    category="weapon" if is_weapon else "action",
                    role=role, table="Action3DEffect", table_file=af,
                    label=(f"appearance {r.appearance}, shape {r.shape}, "
                           f"{role} -> {r.effect}"),
                    shape=r.shape, action=r.action, appearance=r.appearance,
                    weapon_type=s.weapon_type, type_name=s.type_name,
                    effect=r.effect))
            # Rows keyed to nothing apply here too. `rules_for_appearance`
            # does not return them and this does not pretend otherwise.
            s.wildcard_rows = sum(
                1 for r in db.action_rules
                if effects.all_nines(r.group_hi) and effects.all_nines(r.group_lo))

        # -- the type's impact rows, which key on the TYPE, not the id ----
        imp = db.weapon_impact.get(s.weapon_type)
        if imp is not None:
            for role, raw in (("hit spark", imp.hit_effect),
                              ("block spark", imp.blk_effect)):
                if not (raw or "").strip():
                    continue
                s.rows.append(EffectUse(
                    category="weapon", role=role, table="WeaponEffect",
                    table_file=wf,
                    label=(f"weapon type {s.weapon_type}"
                           + (f" ({s.type_name})" if s.type_name else "")
                           + f", {role} -> {raw} -- drawn at the TARGET, "
                             f"never on the character"),
                    weapon_type=s.weapon_type, type_name=s.type_name,
                    effect=raw))
        elif s.weapon_type:
            s.limits.append(
                f"WeaponEffect.ini has no row for weapon type "
                f"{s.weapon_type}: the impact spark is UNMAPPED for this "
                f"subject, which is not the same as it having none")

        # -- every appearance of the type ---------------------------------
        if s.weapon_type:
            per: dict[str, list] = {}
            for r in db.action_rules:
                if r.appearance.startswith(s.weapon_type) and r.effect:
                    per.setdefault(r.appearance, []).append(r.effect)
            for a in sorted(x for x in weapon_apps
                            if db.split_appearance(x)[0] == s.weapon_type):
                names = per.get(a, [])
                s.appearances.append({
                    "appearance": a, "ruleRows": len(names),
                    "effects": sorted({n for n in names
                                       if n.lower() != "none"}),
                    "inWeaponIni": True, "isSubject": a == app})
            for a in sorted(x for x in per
                            if x not in weapon_apps
                            and not effects.all_nines(
                                db.split_appearance(x)[1])):
                s.undeclared_appearances.append({
                    "appearance": a, "ruleRows": len(per[a]),
                    "effects": sorted({n for n in per[a]
                                       if n.lower() != "none"}),
                    "inWeaponIni": False, "isSubject": a == app})

        s.limits.append(DERIVED_SPLIT_LIMIT)
        if s.wildcard_rows:
            s.limits.append(
                f"{s.wildcard_rows} Action3DEffect row(s) on this install "
                f"are keyed to NO appearance and so apply to this subject "
                f"as well; they are counted, not listed, because they are "
                f"the same rows under every id")
        if s.undeclared_appearances:
            s.limits.append(
                f"{len(s.undeclared_appearances)} appearance id(s) of type "
                f"{s.weapon_type} are named by Action3DEffect rows and are "
                f"NOT declared in the weapon appearance table. A list of 'every "
                f"appearance of this type' taken from that table alone is "
                f"short by exactly these, and they are the rows the derived "
                f"split files under ACTIONS rather than WEAPONS.")
        return s

    def weapon_effects(self, appearance: str, *, geometry: bool = True
                       ) -> WeaponEffects:
        """Every effect keyed off one weapon appearance id, plus its holes."""
        db = self.effect_db()
        w = WeaponEffects(appearance=str(appearance).strip(),
                          root=str(self.root))
        if db is None:
            w.measured = False
            w.limits = list(self.build_limits)
            return w
        if self.effect_tables_missing():
            w.measured = False
            w.tables = self.effect_tables()
            w.limits = self.effect_table_limits()
            return w
        es = db.effects_for_weapon(w.appearance)
        w.weapon_type, w.type_name = es.weapon_type, es.type_name
        w.in_weapon_ini = es.in_weapon_ini
        w.aura, w.attack = es.aura, dict(es.attack)
        w.has_impact_row = es.impact is not None
        if es.impact is not None:
            w.hit_effect, w.blk_effect = es.impact.hit_effect, es.impact.blk_effect
            w.hit_sound, w.blk_sound = es.impact.hit_sound, es.impact.blk_sound
        w.tables = self.effect_tables()
        w.limits = self.effect_table_limits()
        for n in es.effect_names:
            w.closures.append(self.effect_closure(n, geometry=geometry))
        if not es.effect_names:
            w.limits.append(
                f"no Action3DEffect row and no WeaponEffect row names an "
                f"effect for appearance {w.appearance}; that is what the "
                f"tables say, not a failure to read them")
        return w

    def effect_refs_for(self, path: str) -> tuple:
        """`(refs, measured)` for one logical path -- the CHEAP reverse query.

        `impact()` builds the appearance forward index too, which costs one
        `resolve_asset` per reference in the install (34s on 7878). This is
        the effect half alone, which needs `EffectDB` and nothing else --
        MEASURED at 0.1s (5017) to 1.6s (7205) -- so `stage`, `stage-mesh`
        and `diff` can afford to warn on every run.
        """
        measured = self.effect_index_measured()
        return list(self.effect_index().get(self.norm(path), [])), measured

    # -- declarations ------------------------------------------------------
    def declarations_for_path(self, path: str) -> list:
        """Every id -> path row in this install that names `path`."""
        want = self.norm(path)
        out: list = []
        db = self.assets.resource_db
        if db is None:
            self.build_limits.append(
                "this install ships no readable ini/c3.wdb: the id -> path "
                "declarations below come from ini/3dobj alone, and ids this "
                "install names ONLY in c3.wdb cannot appear at all")
        else:
            # THE INDEX IS NOT A COMPLETE LISTING OF THE TREE, and the limit
            # above states only the OPPOSITE direction. MEASURED on 6271,
            # 2026-09-10: `ini/c3.wdb` indexes 1,593 of the 1,997 directories
            # under the loose `c3/effect/` tree -- **404 are on disk and
            # absent from the index**. So `ids_for` answers nothing for a
            # file that is there, and "no declaration" is indistinguishable
            # from "no such file".
            #
            # `self.assets.locate` below does NOT inherit this: it resolves
            # overlay -> loose -> archives by hash and never consults the
            # index, which is why the `present` flag can be True on a path
            # this loop produced no ids for. **The library can resolve an
            # asset it cannot enumerate.**
            #
            # Written here because the caveat one branch up -- "ids this
            # install names ONLY in c3.wdb cannot appear at all" -- is the
            # index naming MORE than the tree, and a reader who finds that
            # concludes it is the whole of the asymmetry. It is half of it.
            for ident in db.ids_for(path):
                out.append(Declaration("ini/c3.wdb", str(ident), path,
                                       self.assets.locate(path) is not None,
                                       True))
        obj = self.assets.object_db
        if not obj:
            self.build_limits.append(
                "this install ships no readable ini/3dobj table")
        for ident, p in (obj or {}).items():
            if self.norm(p) == want:
                out.append(Declaration("ini/3dobj", str(ident), p,
                                       self.assets.locate(p) is not None,
                                       True))
        db2 = self.effect_db()
        if db2 is not None:
            for table, rows in (("ini/3DEffectObj", db2.objs),
                                ("ini/3dtexture", db2.textures)):
                for ident, p in rows.items():
                    if self.norm(p) == want:
                        out.append(Declaration(
                            table, str(ident), p.replace("\\", "/"),
                            self.assets.locate(p.replace("\\", "/")) is not None,
                            True))
        return out

    def declared_path_for_id(self, asset_id: str, kind: str) -> list:
        """What the install's own tables name for an id, existence unchecked."""
        try:
            return self.assets.declared_paths(str(asset_id), kind)
        except Exception:                                  # noqa: BLE001
            return []

    # -- candidate ids -----------------------------------------------------
    def candidate_ids(self, path: str, kind: str) -> list:
        """Ids that COULD name `path`. A superset; every one is verified."""
        p = self.norm(path)
        stem = Path(p).name.rsplit(".", 1)[0]
        cands: list = []

        def add(v):
            v = str(v).strip()
            if v and v.isdigit() and v not in cands:
                cands.append(v)

        if stem.isdigit():
            add(stem)
            add(stem.lstrip("0"))
            add(stem.zfill(9))
        db = self.assets.resource_db
        sib = p[:-4] + ".c3" if p.endswith(".dds") else p
        if db is not None:
            for ident in db.ids_for(p):
                add(ident)
            if sib != p:
                # `c3.wdb` only ever stores the MESH path, so a texture is
                # named through its mesh's row -- `resolve_declared` derives
                # `<meshdir>/<id>.dds` and `<meshstem>.dds` from it. The
                # reverse of that derivation is: look up the sibling `.c3`.
                for ident in db.ids_for(sib):
                    add(ident)
        obj = self.assets.object_db or {}
        for ident, decl in obj.items():
            nd = self.norm(decl)
            if nd == p or (sib != p and nd == sib):
                add(ident)
        return cands

    def resolving_ids(self, path: str, kind: str,
                      extra: tuple = ()) -> list:
        """Which candidate ids the resolution model actually sends here.

        `extra` carries the ids the FORWARD index found landing here, which
        the candidate generator cannot derive -- `4143000` landing on
        `c3/armet/7143000.c3` is not recoverable from that path by any rule.
        Without them this section would contradict the reference list right
        below it, and a report that disagrees with itself is worse than one
        that says less.
        """
        cands = list(self.candidate_ids(path, kind))
        for e in extra:
            if str(e) not in cands:
                cands.append(str(e))
        out: list = []
        for cand in cands:
            loc = self.resolve(cand, kind)
            lands = loc.logical if loc is not None else None
            out.append(ResolvingId(
                cand, kind, lands,
                lands is not None and self.norm(lands) == self.norm(path),
                bool(loc is not None and loc.foreign)))
        return out

    # -- the query ---------------------------------------------------------
    def impact(self, target: str) -> Impact:
        """The dependency closure of one logical asset path."""
        path = self.norm(target)
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        kind = {"c3": "mesh", "dds": "texture"}.get(ext, "other")
        loc = self.assets.locate(path)
        imp = Impact(target=path, root=str(self.root), kind=kind,
                     status=UNDECLARED if loc is not None else ABSENT,
                     located=loc, foreign=bool(loc is not None and loc.foreign))

        if kind == "other":
            imp.limits.append(
                f"{path!r} is neither a .c3 nor a .dds. Only those two kinds "
                f"are indexed here; NO reference class was enumerated for "
                f"this file and the empty sections below mean UNMEASURED.")
            imp.limits.extend(self.build_limits)
            return imp

        # -- declarations
        imp.declarations = self.declarations_for_path(path)

        # -- appearance references, straight out of the forward index
        imp.appearance_refs = list(self.forward_index().get(path, []))

        # -- which ids land here
        imp.resolving_ids = self.resolving_ids(
            path, kind, tuple({r.asset_id for r in imp.appearance_refs
                               if r.via == "id"}))

        # -- role part / motion table mapping for the tables that referenced it
        imp.role_parts = self.role_part_rows(
            {r.table for r in imp.appearance_refs})

        # -- effect layers
        imp.effect_refs = list(self.effect_index().get(path, []))

        # -- status
        declared_here = [d for d in imp.declarations if d.is_target]
        if loc is not None:
            imp.status = DECLARED_PRESENT if declared_here else UNDECLARED
        else:
            imp.status = DECLARED_ABSENT if declared_here else ABSENT

        # -- animation
        if kind == "mesh" and loc is not None:
            imp.anim = self.anim_of(path)

        imp.limits.extend(self.build_limits)
        imp.limits.extend(self._standing_limits(kind))
        return imp

    # -- animation ---------------------------------------------------------
    def anim_of(self, path: str) -> dict:
        """PHY/MOTI counts and the shared-motion-set verdict for one `.c3`."""
        out: dict = {}
        try:
            data = self.assets.read(path)
            c3 = C3File(data, strict=False)
        except Exception as e:                             # noqa: BLE001
            return {"error": f"{e.__class__.__name__}: {e}"}
        out["tags"] = c3.tags()
        out["phy"], out["moti"] = _phy_moti(c3)
        out["paired"] = out["phy"] == out["moti"]
        try:
            from c3tex import MotionBinding                # noqa: PLC0415
            with MotionBinding(self.root, assets=self.assets) as mb:
                cls, why = mb.classify(path)
            out["binding"], out["binding_why"] = cls, why
        except Exception as e:                             # noqa: BLE001
            out["binding"], out["binding_why"] = "unknown", (
                f"the classifier would not run: {e.__class__.__name__}: {e}")
        return out

    # -- staged comparison -------------------------------------------------
    def staged_change(self, path: str, new: bytes) -> dict:
        """What swapping `new` in at `path` does to PHY/MOTI pairing."""
        out: dict = {}
        try:
            n = C3File(new, strict=False)
        except Exception as e:                             # noqa: BLE001
            return {"error": f"the staged file will not walk as a C3 "
                             f"container: {e.__class__.__name__}: {e}"}
        nt = n.tags()
        out["new_phy"], out["new_moti"] = _phy_moti(n)
        loc = self.assets.locate(path)
        if loc is None:
            out["original"] = None
            return out
        try:
            o = C3File(self.assets.read(path), strict=False)
        except Exception as e:                             # noqa: BLE001
            return {"error": f"the ORIGINAL at {path} will not walk as a C3 "
                             f"container: {e.__class__.__name__}: {e}"}
        ot = o.tags()
        out["original"] = True
        out["old_phy"], out["old_moti"] = _phy_moti(o)
        out["count_changed"] = (out["old_phy"] != out["new_phy"]
                                or out["old_moti"] != out["new_moti"])
        out["layout_changed"] = ot != nt
        out["unpaired"] = out["new_phy"] != out["new_moti"] and out["old_moti"]
        return out

    # -- standing limits ---------------------------------------------------
    def _standing_limits(self, kind: str) -> list:
        """Reference classes this module CANNOT enumerate, on every run.

        Printed with every report, including the ones that found plenty. A
        count of references means nothing without the list of places that
        were not looked in, and putting that list only in a commit message or
        a docstring is how a partial answer becomes a trusted complete one.
        """
        out = [
            "the client executable itself -- any path or id compiled into "
            "Conquer.exe is unreachable from here (it is Themida-packed and "
            "has not been read directly)",
            "ini/itemtype -- `coassets.load_items` exposes id and name only, "
            "so no item row -> appearance link is enumerated",
            "npc / monster / scene tables (npc.ini, SimpleObjID, .pul, "
            "Scene, .DMap object lists) are not indexed",
            "shared external motion sets (ini/3dmotion and friends) are "
            "classified, not enumerated: MotionBinding answers free/locked/"
            "unknown for one file and does not list which sets target it",
            "references from inside another asset's bytes (a C3 naming "
            "another C3, a material naming a texture) are not followed",
        ]
        if kind == "texture":
            out.append(
                "a texture reached only through a material or a runtime "
                "substitution (Material<i>, MixOpt) is not enumerated; the "
                "texture roles indexed are Texture<i>, MixTex<i>, "
                "ThirdTex<i>, FourthTex<i> and the beside-the-mesh fallback")
        return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(imp: Impact, out=None, limit: int = 12) -> None:
    """The human-readable report. Every section prints, including empty ones.

    An empty section that is simply omitted reads as "there is nothing there";
    an empty section that prints `0` beside the source it consulted reads as
    what it is. That distinction is the whole point of this module, so the
    renderer never skips.
    """
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p(f"asset      {imp.target}")
    p(f"install    {imp.root}")
    p(f"kind       {imp.kind}")
    if imp.located is not None:
        p(f"status     {imp.status.upper()}  --  {imp.located}")
    else:
        p(f"status     {imp.status.upper()}  --  this install does not ship "
          f"a file at that path")
    if imp.foreign:
        p(f"PROVENANCE this install does NOT ship this file. It was served "
          f"from {imp.located.origin_root}")
        p("           -- every reference below is a reference to a path THIS "
          "install's tables name")
        p("              and THIS install has no art for.")

    p("")
    p("DECLARED BY")
    if not imp.declarations:
        p("   (nothing)  no id -> path table in this install names this path")
    for d in imp.declarations:
        state = "present" if d.present else "ABSENT from this install"
        p(f"   {d.table:20s} id {d.asset_id:<12s} -> {d.path}   [{state}]")

    p("")
    p("IDS THAT RESOLVE HERE   (resolution model: coassets.resolve_asset, "
      "INFERRED)")
    if not imp.resolving_ids:
        p("   (none)  no id in this install's tables, and no numeric stem, "
          "could name this file")
    for r in imp.resolving_ids:
        if r.here:
            p(f"   {r.asset_id:<12s} {r.kind:8s} -> here"
              + ("   FOREIGN (served from another install)" if r.foreign
                 else ""))
        elif r.lands_on is None:
            p(f"   {r.asset_id:<12s} {r.kind:8s} -> the model resolves this "
              f"id to NOTHING; the file is reached by PATH, not by id")
        else:
            p(f"   {r.asset_id:<12s} {r.kind:8s} -> SHADOWED, the model "
              f"resolves it to {r.lands_on!r}")

    p("")
    idents = imp.appearance_idents
    p(f"APPEARANCE REFERENCES   {len(imp.appearance_refs)} reference(s) "
      f"in {len({r.table for r in imp.appearance_refs})} table(s), "
      f"{len(idents)} appearance row(s)")
    bytab: dict = {}
    for r in imp.appearance_refs:
        bytab.setdefault((r.table, r.parts), []).append(r)
    if not bytab:
        p("   (none found)")
    for (tab, parts), refs in sorted(bytab.items()):
        p(f"   {tab}   role parts: {', '.join(parts)}   {len(refs)} ref(s)")
        shown = refs[:limit]
        for r in shown:
            p(f"      [{r.ident}] {r.role}  = {r.asset_id}")
        if len(refs) > len(shown):
            p(f"      ... and {len(refs) - len(shown)} more")

    p("")
    p("ROLE PART MAPPING   (ini/RolePart.ini, cross-checked against the "
      "compiled ROPT)")
    if not imp.role_parts:
        p("   (nothing)  no appearance table referenced this file, so there "
          "is no part to map")
    for r in imp.role_parts:
        p(f"   {r['part']:14s} <- {r['table']:14s} (read from {r['source']})"
          f"   [{r['ropt']}]")
        if r["motion"]:
            p(f"                     motion set: {r['motion']}  -- shared; a "
              f"PHY binds to its MOTI by ordinal")

    p("")
    p(f"EFFECT LAYER REFERENCES   {len(imp.effect_refs)}")
    if not imp.effect_refs:
        p("   (none found)")
    for r in imp.effect_refs[:limit]:
        p(f"   {r.effect}  layer {r.layer}  as {r.role}  "
          f"(id {r.asset_id} via {r.table})")
    if len(imp.effect_refs) > limit:
        p(f"   ... and {len(imp.effect_refs) - limit} more")

    if imp.anim:
        p("")
        p("ANIMATION")
        if "error" in imp.anim:
            p(f"   could not read the container: {imp.anim['error']}")
        else:
            pair = "paired" if imp.anim["paired"] else "NOT PAIRED"
            p(f"   {imp.anim['phy']} PHY / {imp.anim['moti']} MOTI  -- {pair}"
              f"   (a PHY binds to a MOTI BY ORDINAL, docs/modding.md 11)")
            p(f"   motion binding: {imp.anim['binding'].upper()} -- "
              f"{imp.anim['binding_why']}")

    if imp.staged:
        p("")
        p("STAGED REPLACEMENT")
        s = imp.staged
        if "error" in s:
            p(f"   {s['error']}")
        elif s.get("original") is None:
            p(f"   pure addition: {s['new_phy']} PHY / {s['new_moti']} MOTI, "
              f"no original to compare against")
        else:
            p(f"   PHY {s['old_phy']} -> {s['new_phy']}, "
              f"MOTI {s['old_moti']} -> {s['new_moti']}")
            if s["unpaired"]:
                p("   BREAKS PAIRING: the replacement leaves PHY and MOTI "
                  "unequal, so at least one mesh has no animation.")
            elif s["count_changed"]:
                p("   the mesh count changes, which renumbers the ordinals a "
                  "shared external motion set relies on.")
            elif s["layout_changed"]:
                p("   the chunk layout changes but the PHY/MOTI counts do "
                  "not.")

    p("")
    p("IF THIS FILE CHANGED OR WENT AWAY")
    n = len(imp.appearance_refs) + len(imp.effect_refs)
    if n:
        p(f"   {len(idents)} appearance row(s) and {len(imp.effect_refs)} "
          f"effect layer(s) would be affected, across "
          f"{len({pt for r in imp.appearance_refs for pt in r.parts})} "
          f"role part(s).")
    elif imp.status == UNDECLARED:
        p("   PRESENT BUT UNDECLARED: the file is here and no table in this "
          "install names it, so nothing this report")
        p("   can enumerate references it. THAT IS NOT PROOF NOTHING DOES -- "
          "see NOT ENUMERATED below.")
    elif imp.status == DECLARED_ABSENT:
        p("   DECLARED BUT ABSENT: the install names this path and does not "
          "ship it. A table row pointing at a")
        p("   file that is not there is already dangling; staging one here "
          "fills a hole rather than replacing")
        p("   anything.")
    elif imp.status == DECLARED_PRESENT:
        p("   The file is here and a table names it, but no appearance row "
          "and no effect layer reaches it")
        p("   through the resolution model. A declaration alone does not "
          "draw anything -- and see NOT")
        p("   ENUMERATED below for what was not looked at.")
    else:
        p("   Nothing found, and nothing declares it either: this install "
          "neither ships nor names this path.")

    p("")
    p("NOT ENUMERATED -- reference classes outside this report")
    for line in imp.limits:
        p(f"   * {line}")


# ---------------------------------------------------------------------------
# effect rendering
#
# Same rule as `render` above: EVERY section prints, including the empty ones,
# and an empty one always says which file it consulted. The extra rule these
# renderers carry is that an UNMEASURED report and a ZERO report never look
# alike -- `measured=False` short-circuits to a refusal instead of printing a
# table of zeroes.
# ---------------------------------------------------------------------------

def render_effect_tables(rows: list, out=None) -> None:
    """The provenance block. Printed above every effect report, always."""
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p("TABLES READ   (the file that ACTUALLY answered; where a compiled .dbc "
      "twin exists the client reads the twin)")
    if not rows:
        p("   (nothing)  the effect tables were NOT read on this install -- "
          "every count below is UNMEASURED, not zero")
        return
    for r in rows:
        f = r["file"] or "(absent)"
        line = (f"   {r['table']:<18s} {f:<22s} {r['form']:<8s} "
                f"{r['rows']:>7d} rows")
        if r.get("not_read"):
            line += f"   [{r['not_read']} present, NOT read]"
        p(line)
        if r.get("note"):
            p(f"   {'':<18s} -- {r['note']}")


def _render_limits(limits: list, p, heading: str) -> None:
    p("")
    p(heading)
    if not limits:
        p("   (none recorded)")
    for line in limits:
        p(f"   * {line}")


def render_effect(c: EffectClosure, out=None, tables: bool = True) -> None:
    """One effect: timing, layers, the assets each layer needs, the holes."""
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p(f"effect     {c.name}")
    p(f"install    {c.root}")
    if not c.measured:
        p("status     UNMEASURED -- the effect tables were not read on this "
          "install.")
        p("           This is NOT 'the effect has no layers'. Nothing below "
          "was determined.")
        _render_limits(c.limits, p, "WHY")
        return
    if tables:
        p("")
        render_effect_tables(c.tables, out=out)
    p("")
    if not c.found:
        p(f"status     NOT DEFINED -- the effect tables were read and hold "
          f"no record named {c.name!r}.")
        if c.near:
            p("           near matches: " + ", ".join(c.near))
        _render_limits(c.limits, p, "NOT ENUMERATED -- rule classes outside "
                                    "this report")
        return
    loops = "forever" if c.endless else f"{c.loop_time}x"
    p(f"status     DEFINED in {c.source}"
      + ("   [this name appears more than once in that table; the LAST "
         "record is shown]" if c.duplicate_name else ""))
    p(f"timing     frameInterval={c.frame_interval} ms  loop={loops}  "
      f"loopInterval={c.loop_interval} ms  delay={c.delay} ms")
    p(f"placement  offset={c.offset}  colorEnable={c.color_enable}")
    if c.duration_ms is not None:
        p(f"duration   {c.duration_ms:.0f} ms  (one playthrough, from the "
          f"alpha envelope -- INFERRED)")
    elif c.endless:
        p("duration   endless (LoopTime is the forever sentinel)")
    p("")
    p(f"LAYERS   {c.declared_layers} declared, {len(c.layers)} parsed")
    if not c.layers:
        p("   THE DEFINITION DECLARES NO LAYERS. The record exists and is "
          "empty -- this effect draws")
        p("   nothing. That is a fact about the table, not a failure to read "
          "it.")
    for L in c.layers:
        p(f"   layer {L.index}   blend {L.blend}")
        for a in (L.mesh, L.texture):
            if not a.asset_id:
                p(f"      {a.role:<8s} (no id in the definition)")
                continue
            if not a.path:
                p(f"      {a.role:<8s} id {a.asset_id:<10s} -> UNRESOLVED: "
                  f"{a.table_file} has no row for this id")
                continue
            state = "PRESENT" if a.present else "MISSING FROM THIS INSTALL"
            extra = f" [{a.source}]" if a.source else ""
            if a.foreign:
                extra += "  FOREIGN (served from another install)"
            p(f"      {a.role:<8s} id {a.asset_id:<10s} -> {a.path}   "
              f"{state}{extra}")
            p(f"      {'':<8s} via {a.table} <- {a.table_file}")
        if L.extra_texture_ids:
            p(f"      extra tex ids: {', '.join(L.extra_texture_ids)}   "
              f"(TextureId<i>_1.._3; not resolved here)")
        # FOUR states, never merged. "no animation form" and "the form was
        # not determined" are the same empty list, and only `geometry_read`
        # separates them -- the same distinction `measured` draws for the
        # whole report.
        if not L.mesh.path:
            p(f"      form     UNKNOWN -- the mesh id resolves to no path in "
              f"{L.mesh.table_file}, so nothing was read")
        elif not L.mesh.present:
            p("      form     UNKNOWN -- this install does not ship that "
              "mesh, so the animation form was not determined")
        elif L.error:
            p(f"      form     UNKNOWN -- {L.error}")
        elif not L.geometry_read:
            p("      form     NOT READ -- geometry reading is off "
              "(--no-geometry). This is not 'no animation'.")
        elif L.forms:
            p(f"      form     {', '.join(L.forms)}")
            p(f"      geometry {L.parts} part(s), {L.frames} frames "
              f"({L.effective_frames} effective)")
            if L.undecoded:
                p(f"      UNDECODED chunks: {', '.join(L.undecoded)} -- this "
                  f"build has no reader for them")
        else:
            p("      form     NONE -- the container was read and holds no "
              "PHY/MOTI, SHAP/SMOT or PTCL chunk")
    if c.layers:
        named = sum(1 for L in c.layers for a in (L.mesh, L.texture)
                    if a.asset_id)
        unres = sum(1 for L in c.layers for a in (L.mesh, L.texture)
                    if a.asset_id and not a.path)
        p("")
        p(f"   {named - c.missing_assets} of {named} layer asset(s) present, "
          f"{c.missing_assets - unres} named but NOT SHIPPED, "
          f"{unres} id(s) resolving to no path at all")
    _render_limits(c.limits, p,
                   "NOT ENUMERATED -- rule classes outside this report")


def render_weapon(w: WeaponEffects, out=None) -> None:
    """One weapon appearance: which effect plays for which action."""
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p(f"weapon     appearance {w.appearance}")
    p(f"install    {w.root}")
    if not w.measured:
        p("status     UNMEASURED -- the effect tables were not read on this "
          "install.")
        p("           This is NOT 'the weapon has no effects'.")
        _render_limits(w.limits, p, "WHY")
        return
    named = w.type_name or "unnamed in the weapon-skill table"
    p(f"type       {w.weapon_type} ({named})"
      + ("" if w.in_weapon_ini else "   [NOT in weapon.ini]"))
    p("")
    render_effect_tables(w.tables, out=out)
    p("")
    p("WHAT PLAYS")
    p(f"   aura (always-on)   {w.aura or '(no always-on row)'}")
    if w.attack:
        for a, n in sorted(w.attack.items()):
            p(f"   action {a:<11s} {n}")
    else:
        p("   action ...         (no Action3DEffect row for this appearance)")
    if w.has_impact_row:
        p(f"   hit                {w.hit_effect or '(none)'}   "
          f"{w.hit_sound or ''}")
        p(f"   block              {w.blk_effect or '(none)'}   "
          f"{w.blk_sound or ''}")
    else:
        p(f"   hit / block        WeaponEffect.ini has NO row for weapon "
          f"type {w.weapon_type}; the impact spark is unmapped, not absent")
    for c in w.closures:
        p("")
        p("-" * 72)
        render_effect(c, out=out, tables=False)


def render_effect_warning(refs: list, measured: bool, tables: list,
                          limits: list, out=None, limit: int = 8) -> None:
    r"""The line `stage` / `stage-mesh` / `diff` print. **Never silent.**

    A modder who replaces `c3/effect/blade/410009.C3` gets no hint today that
    an effect layer points at it. This is that hint, and its whole design
    constraint is that a run which found nothing and a run which could not
    look must not print the same thing.
    """
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p("EFFECT DEPENDENCIES")
    if not measured:
        p("   UNMEASURED -- the effect tables (3DEffect / 3DEffectObj / "
          "3dtexture) were not read on this")
        p("   install, so whether an effect layer points at the staged "
          "file(s) is UNKNOWN, not 'no'.")
        for line in limits:
            p(f"   * {line}")
        return
    files = ", ".join(f"{r['table']}<-{r['file'] or '(absent)'}"
                      for r in tables if r["table"] in
                      ("3DEffect", "3DEffectObj", "3dtexture"))
    if not refs:
        p(f"   0 effect layer(s) name any staged file. Tables read: {files}")
    else:
        bypath: dict = {}
        for path, r in refs:
            bypath.setdefault(path, []).append(r)
        p(f"   {len(refs)} effect layer(s) name {len(bypath)} staged file(s). "
          f"Tables read: {files}")
        for path, rs in sorted(bypath.items()):
            p(f"   {path}   {len(rs)} layer(s)")
            for r in rs[:limit]:
                p(f"      {r.effect}  layer {r.layer}  as {r.role}  "
                  f"(id {r.asset_id} via {r.table})")
            if len(rs) > limit:
                p(f"      ... and {len(rs) - limit} more")
        p("   Changing these files changes what those effects draw. "
          "`comod.py effects --effect <name>` shows each in full.")
    p("   NOT ENUMERATED:")
    for line in limits:
        p(f"      * {line}")


def effect_warning(root, logicals: list, out=None,
                   graph: Optional[DepGraph] = None) -> int:
    """Print `render_effect_warning` for a list of staged logical paths.

    Returns the number of effect layers found. Opens and closes its own
    `DepGraph` unless one is handed in.
    """
    own = graph is None
    g = graph or DepGraph(root)
    try:
        refs: list = []
        measured = g.effect_index_measured()
        for lg in logicals:
            for r in g.effect_index().get(g.norm(lg), []):
                refs.append((g.norm(lg), r))
        render_effect_warning(refs, measured, g.effect_tables(),
                              g.effect_table_limits(), out=out)
        return len(refs)
    finally:
        if own:
            g.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolve_target(g: DepGraph, token: str) -> tuple:
    """`(path, note)` for a CLI argument that may be a path OR a bare id."""
    t = str(token).strip()
    if "/" in t or "\\" in t or t.lower().endswith((".c3", ".dds")):
        return g.norm(t), ""
    if not t.isdigit():
        return g.norm(t), ""
    hits = []
    for kind in ("mesh", "texture"):
        loc = g.resolve(t, kind)
        if loc is not None:
            hits.append((kind, loc.logical))
    if not hits:
        decl = (g.declared_path_for_id(t, "mesh")
                or g.declared_path_for_id(t, "texture"))
        if decl:
            return g.norm(decl[0]), (
                f"id {t} is DECLARED at {decl[0]} and this install does not "
                f"ship it")
        return "", f"id {t} resolves to nothing in this install"
    return g.norm(hits[0][1]), (
        f"id {t} -> {hits[0][1]} ({hits[0][0]})"
        + (f"; also {hits[1][1]} as {hits[1][0]}" if len(hits) > 1 else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("target", help="a logical path (c3/mesh/1.c3) or an id")
    ap.add_argument("--root", default=None)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--ropt", action="store_true",
                    help="print the compiled ROPT part->table mapping and exit")
    a = ap.parse_args(argv)
    root = a.root or coroot.default_root()
    with DepGraph(root, progress=True) as g:
        if a.ropt:
            rows = g.ropt()
            if rows is None:
                print("this install's ini/c3.wdb carries no ROPT section "
                      "(or it ships no c3.wdb at all)")
            else:
                for r in rows:
                    print(f"  {r.part:16s} {r.mesh_table:24s} "
                          f"{r.motion_table}")
            return 0
        path, note = resolve_target(g, a.target)
        if note:
            print(f"({note})")
        if not path:
            return 1
        render(g.impact(path), limit=a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
