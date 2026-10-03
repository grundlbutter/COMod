#!/usr/bin/env python3
r"""
npcart.py -- where an NPC's geometry, texture and motion actually live.

**Three tables, three different answers, and guessing gets all three wrong.**

The flat NPC family looks like it should be self-describing: `npc.json` names
`999001100` and `c3/npc/999001100.c3` exists, so it is tempting to read that
one file as the whole model.  It is not.  Measured against the running game,
the client assembles a Storekeeper from three separate lookups::

    ini/npc.json          type 1 "Storekeeper"
                            simple_object   211
                            standby_motion  999001100     <- motion only
                            rest_motion     999001101
                            blaze_motion    999001190
    ini/3DSimpleObj.ini   [ObjIDType211]
                            Part0    = 9990010            <- geometry
                            Texture0 = 9990211            <- texture
    ini/3dobj.ini         9990010 -> c3/mesh/9990010.c3
    ini/3dtexture.ini     9990211 -> c3/texture/9990211.dds

So `c3/npc/999001100.c3` supplies **motion and nothing else**.  Its PHY chunk
is a copy of the body that the client never draws -- verified by replacing it
and watching the model stay exactly as it was while the animation changed.

This cost a whole session to find, in a specific way worth recording: the
texture was inferred as `c3/texture/9990010.dds` by transposing the mesh id,
reported at **0.95 confidence**, and it is simply not the file the client
loads.  Every attempt built on it failed, and the confidence is what kept the
inference from being questioned.  Two experiments settled it -- tinting that
one texture red changed nothing, tinting all 70 in the family turned every
NPC red -- which bracketed the answer to "right namespace, wrong file" and
sent us to the tables.

Bone counts are the other half of the same lesson.  The Storekeeper's real
skeleton is 30 PHY bones; the donor's is 54.  Writing the donor's 55-bone
motion into `c3/npc/999001*.c3` while the client kept drawing its own 30-bone
body is what produced a T-posing NPC with broken animation.  Resolve all
three roles together or the rig does not agree with itself.

**The chain is the client's; the containers are the install's.**  The 6090
official client answers the same three questions from different files:
`ini/npc.ini` (plaintext, the same fields `npc.json` carries) through the
compiled `.dbc` tables that `dbc.py` reads -- and its `ini/` still contains
the 2009-era plaintext `3dobj.ini`/`3dtexture.ini` as **stale decoys**, six
years older than the `.dbc` twins the client actually consults.  A `Profile`
declares which container set an install uses; `Tables` detects it from what
the install ships.  Two further 6090 facts, measured over all 2,238 NPCs:

* Motion ids are ten digits (`9990010100`) and `3dmotion.dbc` keys them by
  their **low 32 bits** -- the client atoi's into a u32 and the table was
  built to match.  The row resolves to `c3/npc/999001100.c3`, the old
  nine-digit filename: the table is a renaming shim over an archive
  namespace that never changed.  2,165 of 2,232 standby motions resolve
  this way.
* The 67 that miss the table are monster-styled NPCs whose motion lives
  beside their geometry: `dirname(geometry)/<last three digits>.c3`
  covers 59; the remaining 8 are dangling authored rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

#: The CCO tables, in the order the client consults them. Kept as module
#: constants because they are quoted all over the docs; `PROFILE_CCO` is the
#: same set in the form `Tables` actually uses.
NPC_TABLE = "ini/npc.json"
SIMPLE_OBJ_TABLE = "ini/3DSimpleObj.ini"
OBJ_TABLE = "ini/3dobj.ini"
TEXTURE_TABLE = "ini/3dtexture.ini"


@dataclass(frozen=True)
class Profile:
    """Which files answer the three questions for one kind of install.

    The resolution chain (npc row -> simple object -> geometry + texture,
    motions on the side) is the client's and does not vary; what varies is
    the container each table lives in, and clients disagree in ways that are
    measured, not assumed -- see the module docstring.
    """
    name: str
    npc_table: str
    simple_obj_table: str
    obj_table: str
    texture_table: str
    #: RSDB motion table (official clients). None means motion ids name
    #: their file directly, `c3/npc/<id>.c3` -- the CCO rule.
    motion_table: Optional[str] = None


#: CCO and its descendants: JSON npc table, plaintext ini lookups, motion
#: ids that are filenames.
PROFILE_CCO = Profile(
    name="cco",
    npc_table=NPC_TABLE,
    simple_obj_table=SIMPLE_OBJ_TABLE,
    obj_table=OBJ_TABLE,
    texture_table=TEXTURE_TABLE,
)

#: Official patch clients of the 6090 era: sectioned `npc.ini`, compiled
#: `.dbc` tables, motion ids resolved through `3dmotion.dbc` with the id
#: wrapped to u32.
PROFILE_OFFICIAL = Profile(
    name="official",
    npc_table="ini/npc.ini",
    simple_obj_table="ini/3DSimpleObj.dbc",
    obj_table="ini/3DObj.dbc",
    texture_table="ini/3DTexture.dbc",
    motion_table="ini/3dmotion.dbc",
)

#: **Patch clients 5017 / 5065 / 5165: the plaintext-table family.**
#:
#: The official npc chain -- `npc.ini` through simple objects to geometry and
#: texture -- served entirely from the plaintext `ini/*.ini` files, because
#: these clients ship **no `.dbc` at all**.  Compiled twins first appear at
#: 5517, so here the plaintext tables are the LIVE ones rather than the stale
#: decoys they become later.  See `plugins/plaintext.py` for the evidence and
#: for why that inversion has to be stated rather than inherited.
#:
#: MEASURED, all five official installs, same code, both profiles (the numbers
#: are a two-sided proof rather than a preference -- the wrong profile does not
#: raise on either side, it answers with paths the install does not ship):
#:
#:                  PROFILE_OFFICIAL   PROFILE_PLAINTEXT   plaintext paths that exist
#:     5017            0 / 503            503 / 503           2000 / 2000
#:     5065            0 / 575            575 / 575           2000 / 2000
#:     5165            0 / 880            855 / 880           1970 / 1970
#:     5517         1123 / 1123          1066 / 1123            840 / 1870
#:     6090         2256 / 2264          1815 / 2264            841 / 1880
#:
#: 5165's 25 unresolved rows are content, not format: its `npc.ini` names
#: mounts and looks 373/813 that its own (already frozen) `3DSimpleObj.ini`
#: and `3dmotion.ini` never received.
PROFILE_PLAINTEXT = Profile(
    name="plaintext",
    npc_table="ini/npc.ini",
    simple_obj_table=SIMPLE_OBJ_TABLE,
    obj_table=OBJ_TABLE,
    texture_table=TEXTURE_TABLE,
    motion_table="ini/3dmotion.ini",
)


def detect_profile(read: Callable[[str], bytes]) -> Profile:
    """The profile whose deciding table this install can actually produce.

    **This answers a container-shape question, not an identity one.**  Which
    client an install *is* belongs to `plugins.detect`, and where the two ever
    disagree the plugin is authoritative -- it reads `version.dat` and the
    user's own declaration, which no probe of the table layer can see.  Saying
    so here because the two mechanisms did disagree, in a way that read as a
    finding: `plugins.detect` returned GENERIC for 5017/5065/5165 (honest --
    no plugin claimed them) while this function returned `cco` for the same
    three.  It was never detecting CCO. It probed `npc.json`, missed, probed
    `3DSimpleObj.dbc`, missed, and hit the terminal fallback, which happened
    to be spelled with a client's name.  A default wearing an identity's label
    is worse than no answer: the resulting `Tables` loaded **0 of 503 NPCs**
    on 5017 and nothing said why.

    Probes, in order, each a positive statement about a file that only that
    shape ships:

    * `npc.json`             -> CCO and its descendants
    * `3DSimpleObj.dbc`      -> the compiled-twin clients (5517, 6090)
    * `npc.ini` + `3DSimpleObj.ini` with no compiled twin -> the plaintext
      family (5017, 5065, 5165)

    The order matters and is not arbitrary: 5517 and 6090 ship the plaintext
    tables too -- byte-identical to 5165's, because the plaintext layer froze
    at 5165 the moment a compiled twin appeared beside it -- so the third
    probe would claim them if it ran first.  **The compiled twin wins wherever
    one exists**; the third probe is reached only when there is none.

    Falls back to CCO when nothing matches, which then loads empty tables --
    the behaviour missing tables have always had here.  It is a default, not
    a detection.

    **That fallback cannot fire on any install reachable today, and that is
    measured rather than reasoned** (2026-08-09, by the parser-plugin team who
    were named as owning this call).  Every declared install matches a probe:

        5017 / 5065 / 5165  -> plaintext    510 /  582 /  890 npcs loaded
        5517 / 6090         -> official    1108 / 2251
        Classic Conquer 2.0 -> cco          437

    **THE INSTRUMENT MOVED, NOT THE FINDING** (2026-08-10).  Those figures
    read `503 / 575 / 880` and `1123 / 2264` until the reader below was
    fixed, and every one of them was measured through it while it was
    dropping `[Npctype...]` sections and keeping duplicate ids.  The proof's
    **shape** is untouched -- both profiles were probed through the same
    reader, so the comparison was always like-for-like, and the fallback is
    still unreachable on every declared install.  What changed is that the
    denominators now count what the client loads: the lowercase sections
    arrive (+16 / +16 / +18 / +12 / +13) and duplicate ids collapse
    last-wins (-9 / -9 / -8 / -27 / -26), which is why the official pair go
    **down** while the plaintext trio go up.  If you are comparing against an
    older copy of this docstring, the reader changed and the conclusion did
    not.

    and the one install shape that would reach the fallback -- a NetDragon
    DatPkg client, which ships neither `npc.json` nor a compiled twin nor
    `3DSimpleObj.ini` -- never gets this far: `AssetRoot` refuses it a layer
    earlier with *"not a complete install: missing c3.wdf"*.

    So "a default wearing an identity's label" is a real hazard and a
    currently unreachable one.  It becomes reachable the moment a `.tpi`
    plugin lands, and that is the handover: whoever passes the `c3.wdf` gate
    inherits this fallback immediately behind it and will get profile `cco`
    with empty tables unless a fourth probe goes in first.  Predicted, not
    observed -- no DatPkg install can be opened here to confirm it.
    """
    def has(p: str) -> bool:
        try:
            read(p)
            return True
        except Exception:
            return False

    if has(PROFILE_CCO.npc_table):
        return PROFILE_CCO
    if has(PROFILE_OFFICIAL.simple_obj_table):
        return PROFILE_OFFICIAL
    if has(PROFILE_PLAINTEXT.npc_table) and has(
            PROFILE_PLAINTEXT.simple_obj_table):
        return PROFILE_PLAINTEXT
    return PROFILE_CCO

#: `npc.json` names three motions per NPC. The client plays them by role;
#: all three are separate files and a swap has to cover every one it wants
#: to change, or the NPC animates as itself for the roles left behind.
MOTION_ROLES = ("standby_motion", "rest_motion", "blaze_motion")

#: The LIVE mesh index, where an install ships one: `core/wdb.py`'s
#: `ResourceDb.by_id`, an ``{object id: logical path}`` map read from
#: ``ini/c3.wdb``. Passed IN rather than opened here, because this module does
#: pure table lookup and cannot see the install -- the same split
#: `ArtPlan.texture_sibling` keeps.
LIVE_MESH_TABLE = "ini/c3.wdb"


def live_object_id(simple_obj_id: int) -> int:
    """The object id an NPC's `SimpleObjID` is addressed by in `c3.wdb`.

    ``999`` followed by the `SimpleObjID` zero-padded to four digits, which is
    the same id space `3dobj.ini` keys -- `[ObjIDType9970]`'s `Part0` on 7878
    is literally `9999970`. On rows `3DSimpleObj.ini` never received, this
    RECONSTRUCTS the `Part0` that row would have carried.

    **It is a formula, not a table read, and it is wrong some of the time.**
    MEASURED 2026-08-15 against the route that IS a table read
    (`Part0` -> `c3.wdb`), on every install shipping `c3.wdb`, over the rows
    where both routes answer with a path the install ships::

        install   this formula agrees with Part0->c3.wdb    decoy 999||(N+1)
        5165                 51 /  76   67.1%                    0 /  13   0.0%
        6090                162 / 232   69.8%                    0 /  47   0.0%
        6609                197 / 274   71.9%                    0 /  53   0.0%
        7878                811 / 876   92.6%                    0 / 878   0.0%

    The decoy column is the control that matters and it is why this is a
    mechanism rather than a naming coincidence: 7878's `c3/npc/<N>/1.c3` space
    is dense, so an off-by-one key still returns a real, shipped, correctly
    shaped mesh **92.3% as often as the right key does** -- it just never
    returns the *same* one. "A path resolved" is nearly content-free here;
    "the path agrees with the table read" is not.

    So the residual is real: **7.4% of 7878's rows that both routes answer are
    resolved to a plausible wrong mesh by this formula**, and by construction
    those errors are silent -- a shipped `.c3` of the right family under the
    wrong number. Callers that can consult `Part0` should prefer it; this is
    the fallback for the 374 of 453 orphan `SimpleObjID`s that
    `3DSimpleObj.ini` has no row for at all, where there is no `Part0` to
    prefer.
    """
    return int("999" + f"{int(simple_obj_id):04d}")

_SECTION = re.compile(r"^\[([^\]]+)\]([^\[]*)", re.M)
_KV = re.compile(r"^(\w+)\s*=\s*(.*?)\s*$", re.M)


@dataclass
class ArtPlan:
    """Every path one NPC is assembled from, by role."""
    npc_type: int
    name: str = ""
    simple_object: Optional[int] = None
    geometry: str = ""                       # c3/mesh/9990010.c3
    texture: str = ""                        # c3/texture/9990211.dds
    motions: dict = field(default_factory=dict)   # role -> logical path
    #: parts beyond the first, as (geometry, texture) pairs. Four 6090
    #: simple objects are two-part; everything CCO ships is one-part.
    extra_parts: list = field(default_factory=list)
    #: which lookups could not be completed, so a caller can say so rather
    #: than silently swapping three of four files.
    missing: list = field(default_factory=list)
    #: Geometry the LIVE `ini/c3.wdb` names for this row, when a `mesh_index`
    #: was supplied -- a CANDIDATE, exactly like `texture_sibling`, never a
    #: substitution. `geometry` above stays whatever the declared tables said,
    #: so a caller that wants only table-sanctioned art still sees the gap.
    geometry_live: str = ""
    #: How `geometry_live` was reached: ``"Part0"`` (a table read, preferred)
    #: or ``"999||%04d"`` (the reconstruction -- see `live_object_id` for its
    #: measured 7.4% wrong-but-plausible rate on 7878).
    geometry_live_how: str = ""

    @property
    def texture_sibling(self) -> str:
        """The `.dds` sitting BESIDE the geometry, as a second candidate.

        MEASURED on 7878 (2026-08-14): `3dtexture.ini` is frozen at 2008 and
        mostly names paths this client no longer ships, while container assets
        carry their texture next to the mesh -- `c3/npc/997/1.c3` pairs with
        `c3/npc/997/1.dds`, not with the `c3/texture/9999970.dds` the table
        claims. Of 74 ObjIDType rows whose geometry resolves, the table finds
        a texture for **5**; this sibling finds one for a further **13**. The
        remaining 56 have no texture either way, so this is a real but bounded
        recovery -- stated as 13, not as "fixes NPC textures".

        Deliberately a CANDIDATE and not a substitution: this class does pure
        table lookup and cannot see the install, so it must not claim a file
        exists. `audit()` -- which is handed `exists` -- decides. Same
        pure/live split the rest of this module keeps.
        """
        if not self.geometry:
            return ""
        stem = self.geometry.rsplit(".", 1)[0]
        return stem + ".dds" if stem else ""

    @property
    def ok(self) -> bool:
        return bool(self.geometry and self.texture and self.motions)

    def paths(self) -> list:
        return ([self.geometry, self.texture] if self.geometry and self.texture
                else [p for p in (self.geometry, self.texture) if p]) \
            + [p for pair in self.extra_parts for p in pair if p] \
            + list(self.motions.values())


class Tables:
    """The client's own art tables, parsed once.

    ``read`` takes a logical path and returns bytes -- an `AssetRoot`, a
    `Catalog`, or anything else that resolves the install.  With no
    ``profile`` the install is asked which container set it ships; rows are
    normalised to the `npc.json` key names either way, so a caller iterating
    ``npcs`` never sees the difference.
    """

    def __init__(self, read: Callable[[str], bytes],
                 profile: Optional[Profile] = None,
                 mesh_index: Optional[dict] = None):
        self._read = read
        self.profile = profile or detect_profile(read)
        self.npcs: list = []
        self.simple: dict = {}               # obj id -> {"part":…, "texture":…}
        self.objects: dict = {}              # id -> logical path
        self.textures: dict = {}             # id -> logical path
        self.motion_paths: dict = {}         # u32 id -> logical path (official)
        #: ``{object id: logical path}`` from `ini/c3.wdb`, or empty. Supplied
        #: by the caller (`ResourceDb(root/"ini"/"c3.wdb").by_id`) rather than
        #: opened here -- see `LIVE_MESH_TABLE`. Empty is the old behaviour
        #: exactly: every `geometry_live` stays "" and `audit` reports nothing
        #: under its key.
        self.mesh_index: dict = mesh_index or {}
        self._load()

    # -- loading -----------------------------------------------------------
    def _text(self, logical: str) -> str:
        return self._read(logical).decode("latin-1", "replace")

    def _load(self) -> None:
        p = self.profile
        try:
            if p.npc_table.endswith(".json"):
                import json
                self.npcs = json.loads(self._read(p.npc_table)
                                       .decode("utf-8", "replace"))
            else:
                self.npcs = self._npc_ini_rows(self._text(p.npc_table))
        except Exception:
            self.npcs = []
        try:
            if p.simple_obj_table.endswith(".dbc"):
                import dbc
                for rid, parts in dbc.read_simo(
                        self._read(p.simple_obj_table)).items():
                    self.simple[rid] = self._simple_entry(parts)
            else:
                for name, body in _SECTION.findall(
                        self._text(p.simple_obj_table)):
                    if not name.startswith("ObjIDType"):
                        continue
                    kv = dict(_KV.findall(body))
                    # Read the declared PartAmount rather than assuming one
                    # part, so a multi-part entry resolves whole instead of
                    # as a silent half.
                    n = int(kv.get("PartAmount", "1") or 1)
                    parts = [(_int(kv.get(f"Part{i}")),
                              _int(kv.get(f"Texture{i}")))
                             for i in range(max(n, 1))]
                    self.simple[_int(name[len("ObjIDType"):])] = \
                        self._simple_entry(parts, declared=n)
        except Exception:
            pass
        for attr, table in (("objects", p.obj_table),
                            ("textures", p.texture_table)):
            try:
                setattr(self, attr, self._path_table(table))
            except Exception:
                setattr(self, attr, {})
        if p.motion_table:
            try:
                self.motion_paths = self._path_table(p.motion_table)
            except Exception:
                self.motion_paths = {}

    def _path_table(self, table: str) -> dict:
        """An id -> logical-path table, whichever container it ships in."""
        if table.endswith(".dbc"):
            import dbc
            parsed = dbc.Rsdb.parse(self._read(table))
            return {rid: path.replace("\\", "/").lower()
                    for rid, path in parsed.paths.items()}
        out = {}
        for line in self._text(table).splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().replace("\\", "/")
            if k.isdigit() and v:
                out[int(k)] = v.lower()
        return out

    @staticmethod
    def _simple_entry(parts: list, declared: Optional[int] = None) -> dict:
        first = parts[0] if parts else (None, None)
        return {
            "parts": declared if declared is not None else len(parts),
            "part": first[0],
            "texture": first[1],
            "extra": [pr for pr in parts[1:] if pr[0] is not None],
        }

    @staticmethod
    def _npc_ini_rows(text: str) -> list:
        """`npc.ini` sections normalised to `npc.json` row keys.

        The fields are the same facts under different names; normalising
        here is what lets `plan_for_npc` and every consumer stay a single
        code path across profiles.
        """
        # CASE-INSENSITIVE, and LAST WINS. Both halves are the client's, read
        # out of `GameData.dll` (`3DRoleData.cpp`) at five addresses, one per
        # build -- not inferred from the data.
        #
        # THE CASE HALF. This was `name.startswith("NpcType")`, and TQ spells
        # some sections `[Npctype...]`. Those rows were dropped in silence:
        # 16 on 5017 and 5065, 18 on 5165, 12 on 5517, 13 on 6090 -- and all
        # 75 resolve to real geometry AND texture. Only CCO escaped, because it
        # reads `npc.json`. The client never compares the prefix at all: it
        # does `atoi(sec + strlen("NpcType"))`, where the literal survives only
        # as a compile-time way of writing 7. So `[Npctype340]` is 340,
        # byte-identical, and the game loads every one of the 75.
        #
        # Matched case-insensitively rather than prefix-blind. The client is
        # the more permissive of the two -- it would skip 7 characters of
        # anything -- but every section in every shipped `npc.ini` is one
        # spelling or the other, so the two agree on all input that exists.
        # Going prefix-blind would be a claim about sections no file contains.
        #
        # THE DUPLICATE HALF, and it is owed independently of the case fix
        # because it is live TODAY: duplicate type ids already exist among
        # correctly-spelled sections on every official base -- 9, 9, 8, 27, 26.
        # This returned every row in a list, so a consumer building a dict got
        # last-wins BY ACCIDENT and a consumer iterating rendered BOTH. The
        # client's policy is `std::map::operator[]` followed by an
        # unconditional copy-assign, i.e. LAST WINS, and it is applied here so
        # nobody inherits the accident.
        #
        # The two interact on exactly three ids and only on 5165: 340, 341 and
        # 342 carry a lowercase AND an uppercase section naming DIFFERENT NPCs
        # (`MagicFlowerPot` against `FestivalLantern` / `LanternFestivalElder`,
        # every field differing). Fixing the case without the policy would have
        # turned a silent loss into a silent wrong answer on those three, which
        # is why the two land together.
        by_type: dict = {}
        for name, body in _SECTION.findall(text):
            if not name[:7].casefold() == "npctype":
                continue
            kv = dict(_KV.findall(body))
            row = {
                "type": _int(name[7:]),
                "name": kv.get("Name", ""),
                "simple_object": kv.get("SimpleObjID"),
                "standby_motion": kv.get("StandByMotion"),
                "rest_motion": kv.get("RestMotion"),
                "blaze_motion": kv.get("BlazeMotion"),
            }
            by_type[row["type"]] = row      # last wins, as the client does
        return list(by_type.values())

    # -- resolution --------------------------------------------------------
    def plan_for_npc(self, row: dict) -> ArtPlan:
        """Every path this npc-table row is drawn from."""
        p = self.profile
        plan = ArtPlan(npc_type=_int(row.get("type")) or 0,
                       name=str(row.get("name") or ""),
                       simple_object=_int(row.get("simple_object")))
        obj = self.simple.get(plan.simple_object) if plan.simple_object else None
        if obj is not None:
            expected = len(obj["extra"]) + 1
            if obj["parts"] != expected:
                plan.missing.append(
                    f"PartAmount={obj['parts']} but {expected} parts present")
            geom = (self.objects.get(obj["part"])
                    if obj["part"] is not None else None)
            tex = (self.textures.get(obj["texture"])
                   if obj["texture"] is not None else None)
            if geom:
                plan.geometry = geom
            else:
                plan.missing.append(
                    f"object id {obj['part']} not in {p.obj_table}")
            if tex:
                plan.texture = tex
            else:
                plan.missing.append(
                    f"texture id {obj['texture']} not in {p.texture_table}")
            for part_id, tex_id in obj["extra"]:
                g2 = self.objects.get(part_id)
                t2 = self.textures.get(tex_id)
                if not g2:
                    plan.missing.append(
                        f"object id {part_id} not in {p.obj_table}")
                if not t2:
                    plan.missing.append(
                        f"texture id {tex_id} not in {p.texture_table}")
                plan.extra_parts.append((g2 or "", t2 or ""))
        else:
            plan.missing.append(
                f"no simple object {plan.simple_object} in {p.simple_obj_table}"
                if plan.simple_object else "row names no simple_object")
        self._live_geometry(plan, obj)
        for role in MOTION_ROLES:
            mid = _int(row.get(role))
            if mid is None:
                continue
            path = self._motion_path(mid, plan)
            if path:
                plan.motions[role.replace("_motion", "")] = path
        return plan

    def _live_geometry(self, plan: ArtPlan, obj: Optional[dict]) -> None:
        r"""What `ini/c3.wdb` names for this row, as a candidate.

        **ADDITIVE, never a swap, and that is measured rather than cautious.**
        `c3.wdb` is not a superset of the declared chain: on 6090, resolving
        every row through `Part0` -> `c3.wdb` instead of `3DObj.dbc` gives
        **1,877 of 2,251** shipped geometries against the declared chain's
        **2,242** -- a wholesale swap LOSES 365 rows on a client where the
        appearance path already works. So this only ever offers a second
        answer; `audit` decides, and only where the first one failed.

        Two routes, in this order, because one is a table read and the other
        is arithmetic:

        1. ``Part0`` -> `c3.wdb`. Same key `3dobj.ini` uses, live table.
        2. `live_object_id(SimpleObjID)` -> `c3.wdb`, for the rows
           `3DSimpleObj.ini` never received and which therefore have no
           ``Part0`` at all -- **374 of 7878's 453 orphan `SimpleObjID`s**.

        MEASURED 2026-08-15, NPC rows whose geometry the install actually
        ships, `npcart.audit`'s own population, both routes together::

            install   declared chain   + this candidate      by route
            5165          865 /   890    866 /   890 (+1)    999||%04d 1
            5517        1,107 / 1,108  1,107 / 1,108 (+0)    --
            6090        2,242 / 2,251  2,242 / 2,251 (+0)    --
            6609        2,714 / 2,750  2,716 / 2,750 (+2)    999||%04d 2
            7878          998 / 4,084  3,430 / 4,084 (+2,432) 999||%04d 2,281
                                                              Part0       151

        **The four controls moving by 0, 0, +1 and +2 is the point**, and it
        is what says 7878's +2,432 is a statement about 7878 rather than about
        this code: an instrument that only ever adds would have added there
        too. Note the shape of 7878's split -- the *table read* recovers 151
        and the *reconstruction* recovers 2,281, so the bulk of the gain rests
        on the formula and inherits its residual, not on a lookup.
        """
        idx = self.mesh_index
        if not idx:
            return
        pid = obj["part"] if obj and obj.get("part") is not None else None
        if pid is not None:
            hit = idx.get(int(pid))
            if hit:
                plan.geometry_live, plan.geometry_live_how = hit, "Part0"
                return
        if plan.simple_object is not None:
            hit = idx.get(live_object_id(plan.simple_object))
            if hit:
                plan.geometry_live = hit
                plan.geometry_live_how = "999||%04d"

    def _motion_path(self, mid: int, plan: ArtPlan) -> Optional[str]:
        """Where one motion id's file lives, under this profile.

        CCO: the id IS the stem, `c3/npc/<id>.c3` -- verified over every row
        that has one, see `audit`.

        Official: the id is looked up in the motion table **wrapped to u32**,
        because that is how the client holds it (see the module docstring);
        ids the table does not carry are monster-styled NPCs whose motion
        sits beside their geometry as `<dir>/<last three digits>.c3`.
        """
        if self.profile.motion_table is None:
            return f"c3/npc/{mid}.c3"
        hit = self.motion_paths.get(mid & 0xFFFFFFFF)
        if hit:
            return hit
        if plan.geometry:
            return plan.geometry.rsplit("/", 1)[0] + f"/{mid % 1000}.c3"
        plan.missing.append(
            f"motion {mid} not in {self.profile.motion_table} and no "
            "geometry to derive from")
        return None

    def plan_for_mesh(self, logical: str) -> Optional[ArtPlan]:
        """The NPC a logical path belongs to, whichever role it plays.

        Collecting starts from a path -- `c3/npc/999001100.c3` is what the
        browser shows -- and that path is a *motion* file, so the plan has to
        be reachable from it. Matching geometry too means a mesh collected
        from `c3/mesh/` finds its NPC as well.
        """
        key = (logical or "").replace("\\", "/").lstrip("/").lower()
        if not key:
            return None
        for row in self.npcs:
            plan = self.plan_for_npc(row)
            if (key in plan.motions.values() or key == plan.geometry
                    or any(key == g for g, _t in plan.extra_parts)):
                return plan
        return None


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def audit(tables: Tables, exists: Callable[[str], bool]) -> dict:
    """Resolve every NPC and report what holds, over the whole table.

    A resolver that is right about the one asset it was debugged on is worth
    very little; this is what says whether the chain describes the client or
    just the Storekeeper.
    """
    out = {"npcs": len(tables.npcs), "resolved": 0, "unresolved": 0,
           "geometryMissingOnDisk": [], "textureMissingOnDisk": [],
           "motionMissingOnDisk": [], "reasons": {}, "sharedGeometry": {},
           "sharedTexture": {}}
    geom_users: dict = {}
    tex_users: dict = {}
    for row in tables.npcs:
        plan = tables.plan_for_npc(row)
        if plan.ok:
            out["resolved"] += 1
        else:
            out["unresolved"] += 1
            for why in (plan.missing or ["unknown"]):
                key = re.sub(r"\d+", "<id>", why)
                out["reasons"][key] = out["reasons"].get(key, 0) + 1
        if plan.geometry:
            geom_users.setdefault(plan.geometry, []).append(plan.npc_type)
            if not exists(plan.geometry):
                out["geometryMissingOnDisk"].append(
                    (plan.npc_type, plan.geometry))
        # The declared chain named no geometry, or named one this install does
        # not ship. `ini/c3.wdb` may still name one -- recorded under its OWN
        # key and NOT folded into `resolved`, for the same reason
        # `textureViaSibling` is: art found by a different rule than the table
        # declared is a DIFFERENT fact. Route `999||%04d` additionally carries
        # a measured 7.4% wrong-but-plausible rate on 7878, so the route is
        # reported with every row and a caller may filter on it.
        if (plan.geometry_live and not (plan.geometry and exists(plan.geometry))
                and exists(plan.geometry_live)):
            out.setdefault("geometryViaMeshIndex", []).append(
                (plan.npc_type, plan.geometry_live, plan.geometry_live_how))
        if plan.texture:
            tex_users.setdefault(plan.texture, []).append(plan.npc_type)
            if not exists(plan.texture):
                # The frozen table's path is absent. Before reporting it
                # missing, try the sibling `.dds` beside the geometry -- see
                # ArtPlan.texture_sibling for why, and for the 5-vs-13 split
                # that says how much this actually recovers. Recorded under
                # its own key rather than folded into a "resolved" count: a
                # texture found by a different rule than the table declared
                # is a DIFFERENT fact, and a caller that wants only
                # table-sanctioned art must still be able to see the gap.
                sib = plan.texture_sibling
                if sib and exists(sib):
                    out.setdefault("textureViaSibling", []).append(
                        (plan.npc_type, plan.texture, sib))
                else:
                    out["textureMissingOnDisk"].append(
                        (plan.npc_type, plan.texture))
        elif plan.geometry:
            # No texture id at all, but geometry resolved -- the sibling is
            # the only candidate there is.
            sib = plan.texture_sibling
            if sib and exists(sib):
                out.setdefault("textureViaSibling", []).append(
                    (plan.npc_type, "", sib))
        for role, p in plan.motions.items():
            if not exists(p):
                out["motionMissingOnDisk"].append((plan.npc_type, role, p))
    # Shared art matters for a swap: replacing it changes every NPC using it,
    # which is a thing to be told before it happens rather than after.
    # `len(v) > 1` DROPS single-user entries entirely, and that absence is
    # load-bearing in a way `.get(mesh, [])` cannot express.  MEASURED
    # 2026-08-18: CCO has 48 one-user geometries and 6609 has 88, and NONE of
    # them appear here -- so `.get()` returns the same `[]` for "one NPC uses
    # this, safe to touch" and "this mesh is not in the npc table at all".
    # Opposite meanings; the empty list reads as the reassuring one.  By
    # DISTINCT GEOMETRY, which is what a swap actually operates on, that is
    # 48 of 86 on CCO -- the majority of the deployment target, not an edge.
    #
    # The maps are NOT widened: consumers depend on membership here meaning
    # "shared", and quietly redefining it would be a worse defect than this
    # one.  Establish membership separately -- `Tables.plan_for_mesh`, or
    # `core/npcaltskin.Membership`, which answers in_table(N, including N==1)
    # / not_in_table / undetermined as three values a caller cannot conflate.
    # Absence from this map is not evidence of anything.
    out["sharedGeometry"] = {k: v for k, v in geom_users.items() if len(v) > 1}
    out["sharedTexture"] = {k: v for k, v in tex_users.items() if len(v) > 1}
    return out
