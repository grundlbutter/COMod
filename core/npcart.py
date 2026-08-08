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


def detect_profile(read: Callable[[str], bytes]) -> Profile:
    """The profile whose deciding table this install can actually produce.

    `npc.json` decides CCO; failing that, the compiled `3DSimpleObj.dbc`
    decides official.  Neither present falls back to CCO, which then loads
    empty tables -- the behaviour missing tables have always had here.
    """
    for probe, profile in ((PROFILE_CCO.npc_table, PROFILE_CCO),
                           (PROFILE_OFFICIAL.simple_obj_table,
                            PROFILE_OFFICIAL)):
        try:
            read(probe)
            return profile
        except Exception:
            continue
    return PROFILE_CCO

#: `npc.json` names three motions per NPC. The client plays them by role;
#: all three are separate files and a swap has to cover every one it wants
#: to change, or the NPC animates as itself for the roles left behind.
MOTION_ROLES = ("standby_motion", "rest_motion", "blaze_motion")

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
                 profile: Optional[Profile] = None):
        self._read = read
        self.profile = profile or detect_profile(read)
        self.npcs: list = []
        self.simple: dict = {}               # obj id -> {"part":…, "texture":…}
        self.objects: dict = {}              # id -> logical path
        self.textures: dict = {}             # id -> logical path
        self.motion_paths: dict = {}         # u32 id -> logical path (official)
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
        rows = []
        for name, body in _SECTION.findall(text):
            if not name.startswith("NpcType"):
                continue
            kv = dict(_KV.findall(body))
            rows.append({
                "type": _int(name[len("NpcType"):]),
                "name": kv.get("Name", ""),
                "simple_object": kv.get("SimpleObjID"),
                "standby_motion": kv.get("StandByMotion"),
                "rest_motion": kv.get("RestMotion"),
                "blaze_motion": kv.get("BlazeMotion"),
            })
        return rows

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
        for role in MOTION_ROLES:
            mid = _int(row.get(role))
            if mid is None:
                continue
            path = self._motion_path(mid, plan)
            if path:
                plan.motions[role.replace("_motion", "")] = path
        return plan

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
        if plan.texture:
            tex_users.setdefault(plan.texture, []).append(plan.npc_type)
            if not exists(plan.texture):
                out["textureMissingOnDisk"].append(
                    (plan.npc_type, plan.texture))
        for role, p in plan.motions.items():
            if not exists(p):
                out["motionMissingOnDisk"].append((plan.npc_type, role, p))
    # Shared art matters for a swap: replacing it changes every NPC using it,
    # which is a thing to be told before it happens rather than after.
    out["sharedGeometry"] = {k: v for k, v in geom_users.items() if len(v) > 1}
    out["sharedTexture"] = {k: v for k, v in tex_users.items() if len(v) > 1}
    return out
