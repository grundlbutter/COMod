#!/usr/bin/env python3
r"""rigtarget.py -- WHICH files a rig is solved from, for a player shape, a
monster family or an NPC geometry family.

    from rigtarget import resolve
    t = resolve(root, shape="900")            # monster dir c3/monster/900/
    t = resolve(root, npc="9992720")          # NPC geometry c3/npc/9992720.c3
    t = resolve(root, npc="Pharmacist")       # ... by npc.json name
    t = resolve(root, shape="3")              # the player path, unchanged

`tools/bonerig.family_files` was written for the player space: it builds the
prefix ``c3/000<shape>/`` and lists every `ini/3dmotion.ini` row under it.
Monsters and NPCs bind differently, and both bindings are already measured
in this repo; this module only puts them in front of the solver.

MONSTERS (`core/monsterart.py`, `ini/3dmotion.ini`)
----------------------------------------------------
A 9-digit row ``<body3><ws3><act3>`` names ``c3/monster/<dir>/<act>.c3``.
The directory is a FAMILY: colour morphs share it (111 serves 111-116, 120
serves 120-124; shape != dir on 235 of 314 shape/dir pairs on the CCO
snapshot), so a 3-digit shape resolves to the directory its rows point at,
and the family's clips are EVERY row under that directory, whatever shape
declared them -- the skeleton is the directory's. The idle is ``<dir>/100.c3``.
The DRAWN mesh is `ini/3dobj.ini`'s ``<body>000000`` row: ``<dir>/100.c3``
on 30 of the 59 shipping families (the standby clip IS the mesh container),
``<dir>/1.c3`` on 18, ``<dir>/<id>000000.c3`` on 10 to 11.

NPCs (`core/npcart.py`)
-----------------------
A CCO NPC is an `ini/npc.json` row; its geometry comes through
`3DSimpleObj.ini` -> `3dobj.ini`, and its three motion ids ARE filenames
``c3/npc/<id>.c3`` (standby / rest / blaze). The FAMILY is the geometry:
every row that draws the same mesh shares its skeleton by the same
convention the player shapes do -- MEASURED here per family, never assumed:
`bonerig.accept` refuses a clip whose body track has another bone count,
and the builder records every refusal by path. The family KEY is the full
geometry path (`npc_key`): ``c3/npc/013/1.c3`` and ``c3/npc/029/1.c3`` are
two skeletons, and keyed by the basename both were ``npc-1`` -- the Dryad
loaded the Reindeer's 1-bone rig (2026-10-02).

PLAYER-BODIED NPCs (`player_body`)
----------------------------------
An NPC whose standby holds the player's bone count on the player's own
skeleton (Sage, ``c3/npc/9990111100.c3``, 18 names: 84 bones, shape 3's
joints) is not solved from its three clips -- 73 frames give a rig that
agrees with the player's on 7 of 26 parents. It BORROWS the player rig of
the shape whose joints its own clips keep connected. The test and its
measured thresholds are `player_body`'s.

WHAT THIS MODULE RECORDS
------------------------
A `RigTarget` carries the family key, the clip paths, the idle clips a
retarget would write, the drawn mesh and how it was found, the names the
tables give the family, and which table rows alias each file -- so the
catalog a build writes can say what a file is and who reads it.

Nothing here writes. `solve` and `load_or_solve` call `bonerig` with the
paths and the mesh, and the cache lands under the install's derived
namespace as ``out/indexes/<base-id>/rig/<family>.json``.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bonerig                                               # noqa: E402
import coroot                                                # noqa: E402

PLAYER, MONSTER, NPC = "player", "monster", "npc"

MONSTER_PREFIX = "c3/monster/"
NPC_PREFIX = "c3/npc/"
#: The idle action every monster plays when it stands: ``<dir>/100.c3``.
MONSTER_IDLE_ACTION = "100"
#: The NPC roles, in the order `core/npcart.MOTION_ROLES` lists them; the
#: standby is what an idle NPC plays.
NPC_ROLES = ("standby", "rest", "blaze")
NPC_IDLE_ROLE = "standby"


class TargetError(ValueError):
    """A shape or NPC key that resolves to nothing this install ships."""


class RigCacheMismatch(TargetError):
    """A cached family rig that was solved for ANOTHER body than the target
    it was looked up for: another drawn mesh, another reference mesh or
    another bone count. Refused by name rather than used -- a rig for the
    wrong skeleton labels and gates the wrong creature without a single
    error (the Dryad on the Reindeer's rig), and nothing downstream can
    tell."""


@dataclass
class RigTarget:
    """What a rig is solved from, and what a retarget would write."""
    kind: str                            # PLAYER | MONSTER | NPC
    id: str                              # "3", "900", "9992720"
    family: str                          # "player-3", "monster-900", "npc-9992720"
    #: the clip paths the solver reads; None for the player path (prefix)
    paths: Optional[list] = None
    prefix: str = ""
    #: the clip(s) a retarget writes: `<dir>/100.c3`, the standby files
    idle: list = field(default_factory=list)
    #: the drawn mesh (`ini/3dobj.ini` / `3DSimpleObj.ini`), "" when unknown
    drawn_mesh: str = ""
    drawn_mesh_how: str = ""
    #: names the tables give this family (monster.json / npc.json)
    names: list = field(default_factory=list)
    #: ``{path: [table keys]}`` -- which rows alias each idle file
    aliases: dict = field(default_factory=dict)
    #: monster: the shapes the directory serves; npc: the npc.json types
    members: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: monster: ``{directory: [keys]}`` -- rows of the SHAPE asked for that
    #: point at another directory than the family's (shape 125 plays most
    #: of 120's clips; 130 borrows two of 111's). Recorded, not refused.
    foreign: dict = field(default_factory=dict)
    #: the action codes (key digits 7-9) of the rows naming each idle file
    #: -- rewriting ``<dir>/100.c3`` changes EVERY action listed (Guard 900:
    #: 55 of them), and an idle no action-100 row names is noted as not a
    #: standby (none of the 59 shipping directories on the CCO snapshot)
    idle_actions: dict = field(default_factory=dict)
    #: npc: ``{idle path: {"how", "rows", "stemAgrees", "ids"}}`` -- how each
    #: idle file was RESOLVED from the rows' standby ids (`npc_families`:
    #: the `ini/3dmotion.ini` row first, the ``c3/npc/<id>.c3`` stem when the
    #: id has no row) and whether the stem rule names the same file. The
    #: table route is TABLE EVIDENCE, not live-verified (2026-10-02).
    idle_how: dict = field(default_factory=dict)
    #: families FOLDED into this one by the builder because their rows'
    #: idle is a file this family writes (an NPC drawn as the Guard whose
    #: standby id names ``c3/monster/900/100.c3``): ``[{family, geometry,
    #: names, members, rows}]``. Their names and members are added to this
    #: target's; nothing of theirs is built separately.
    merged: list = field(default_factory=list)
    #: why the family could not be resolved, when a caller keeps a
    #: placeholder for it (the builder's refusal row carries THIS text)
    error: str = ""

    @property
    def rig_shape(self) -> str:
        """What `FamilyRig.shape` records for this target: the DIRECTORY for
        a monster (the skeleton is the directory's -- a colour morph asked
        for by its own shape number must find the family's cached rig, not
        re-solve it under another name), the id otherwise."""
        if self.kind == MONSTER and self.family.startswith("monster-"):
            return self.family[len("monster-"):]
        return self.id

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "family": self.family,
                "paths": list(self.paths) if self.paths is not None else None,
                "prefix": self.prefix, "idle": list(self.idle),
                "drawnMesh": self.drawn_mesh, "drawnMeshHow": self.drawn_mesh_how,
                "names": list(self.names),
                "aliases": {k: list(v) for k, v in sorted(self.aliases.items())},
                "members": list(self.members), "notes": list(self.notes),
                "foreign": {k: list(v) for k, v in sorted(self.foreign.items())},
                "idleActions": {k: list(v) for k, v in sorted(self.idle_actions.items())},
                "idleHow": {k: dict(v) for k, v in sorted(self.idle_how.items())},
                "merged": [dict(m) for m in self.merged],
                "error": self.error}


def _norm(p) -> str:
    return str(p).replace("\\", "/").strip().lower()


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def motion_rows(root) -> dict:
    """``{key: path}`` of `ini/3dmotion.ini` (and its dbc twin where one
    ships), paths normalised to forward slashes and lower case -- through
    `anim.MotionIndex`, the one reader of that table."""
    import anim                                              # noqa: PLC0415
    idx = anim.MotionIndex(str(root))
    return {str(k): _norm(v) for k, v in idx.raw.items()}


def obj_rows(root, assets=None) -> dict:
    """``{id: path}`` of `ini/3dobj.ini`, normalised."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    out: dict = {}
    try:
        text = ar.read("ini/3dobj.ini").decode("latin-1", "replace")
    except Exception:                                        # noqa: BLE001
        return out
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k:
            out[k] = _norm(v)
    return out


def monster_names(root, assets=None) -> dict:
    """``{type: name}`` from `ini/monster.json`. The type is NOT the look
    id in general (`docs/CORRECTIONS.md`: it holds for 900, 126 and 111 by a
    live control and nowhere else is it measured), so a caller labels these
    as CANDIDATES."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    out: dict = {}
    try:
        rows = json.loads(ar.read("ini/monster.json").decode("utf-8-sig", "replace"))
    except Exception:                                        # noqa: BLE001
        return out
    if isinstance(rows, dict):
        rows = list(rows.values())
    for r in rows or ():
        if isinstance(r, dict) and r.get("type") is not None:
            out[str(r["type"])] = str(r.get("name") or "")
    return out


# ---------------------------------------------------------------------------
# monsters
# ---------------------------------------------------------------------------

def monster_dirs(rows: dict) -> dict:
    """``{dir path: {path: [keys]}}`` for every 9-digit row under
    `c3/monster/`: the clip directories the motion table declares, each with
    the rows that alias each clip."""
    out: dict = {}
    for k, v in rows.items():
        if len(k) != 9 or not v.startswith(MONSTER_PREFIX):
            continue
        d = v.rsplit("/", 1)[0]
        out.setdefault(d, {}).setdefault(v, []).append(k)
    return out


def shipping_monster_dirs(root, assets=None) -> list:
    """The monster directories with at least one PRESENT clip, sorted."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    out = []
    for d, clips in sorted(monster_dirs(motion_rows(root)).items()):
        if any(_exists(ar, p) for p in clips):
            out.append(d)
    return out


def _exists(ar, p) -> bool:
    try:
        return bool(ar.exists(p))
    except Exception:                                        # noqa: BLE001
        return False


def _monster_family(root, d: str, shape_id: str, ar, rows: dict, dirs: dict) -> RigTarget:
    """The `RigTarget` of ONE clip directory `d` (``c3/monster/<dir>``):
    every row under it is the family, whatever shape declared it."""
    clips = dirs.get(d, {})
    dir_name = d.rsplit("/", 1)[1]
    t = RigTarget(kind=MONSTER, id=str(shape_id), family="monster-%s" % dir_name,
                  paths=sorted(clips), prefix=d + "/")
    shapes = sorted({k[:3] for ks in clips.values() for k in ks})
    t.members = shapes
    idle = d + "/" + MONSTER_IDLE_ACTION + ".c3"
    if idle in clips:
        t.idle = [idle]
        t.aliases[idle] = sorted(clips[idle])
        acts = sorted({k[6:9] for k in clips[idle]})
        t.idle_actions[idle] = acts
        if MONSTER_IDLE_ACTION not in acts:
            t.notes.append("%s is named by action(s) %s and by NO action-%s row: "
                           "rewriting it changes those actions, not a standby"
                           % (idle, acts, MONSTER_IDLE_ACTION))
    else:
        t.notes.append("no %s row under %s: the family has no idle clip to "
                       "retarget" % (MONSTER_IDLE_ACTION, d))
    # the drawn mesh: 3dobj.ini's <body>000000 row for this shape, else any
    # row of the directory (a morph borrows the base's mesh)
    objs = obj_rows(root, ar)
    key = "%s000000" % shape_id
    if objs.get(key, "").startswith(d + "/"):
        t.drawn_mesh = objs[key]
        t.drawn_mesh_how = "ini/3dobj.ini %s" % key
    else:
        in_dir = sorted((k, v) for k, v in objs.items() if v.startswith(d + "/"))
        if in_dir:
            t.drawn_mesh = in_dir[0][1]
            t.drawn_mesh_how = ("ini/3dobj.ini %s (no %s row; the directory's "
                                "first mesh row)" % (in_dir[0][0], key))
        else:
            t.drawn_mesh_how = "no ini/3dobj.ini row under %s" % d
    names = monster_names(root, ar)
    t.names = sorted({names[s] for s in shapes if s in names and names[s]})
    if t.names:
        t.notes.append("names are monster.json rows whose TYPE equals a served "
                       "shape -- a candidate, live-verified only for 900/126/111")
    return t


def resolve_monster_dir(root, name, *, assets=None, rows=None) -> RigTarget:
    """A clip DIRECTORY (``125``, ``104n`` or ``c3/monster/125``) -> its
    family, whatever the shape of that number plays.

    The builder iterates directories, and a directory is not a shape: shape
    125's own rows point 149 times at ``c3/monster/120/`` and 36 times at
    ``c3/monster/125/`` (measured on the CCO snapshot), so asking
    `resolve_monster` for "125" answers a different question -- which
    directory shape 125 STANDS in (120) -- and until 2026-10-02 it answered
    it by refusing, which the builder then reported as 'no idle clip
    shipped' for a directory whose ``100.c3`` exists."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    rows = rows if rows is not None else motion_rows(root)
    dirs = monster_dirs(rows)
    n = _norm(name)
    d = n if n.startswith(MONSTER_PREFIX) else MONSTER_PREFIX + n
    d = d.rstrip("/")
    if d not in dirs:
        raise TargetError("%r is no clip directory of ini/3dmotion.ini under %s "
                          "(%d monster directories declared)"
                          % (str(name), MONSTER_PREFIX, len(dirs)))
    return _monster_family(root, d, d.rsplit("/", 1)[1], ar, rows, dirs)


def resolve_monster(root, shape, *, assets=None, rows=None) -> RigTarget:
    """A 3-digit monster shape -> its clip directory, as a `RigTarget`.

    The shape's own 9-digit rows name the directory (a colour morph's rows
    point at the base family's directory, which is the rule
    `core/monsterart` spells); the family is then EVERY row under that
    directory.

    A shape whose rows point at TWO directories (5 of 265 shapes on the CCO
    snapshot: 125 -> 120/ and 125/, 130 -> 130/ and 111/, 283, 317, 999)
    resolves to the directory that holds its OWN IDLE -- the file its
    ``<shape>000100`` row names, else the one directory all its action-100
    rows agree on -- and the rows pointing elsewhere are recorded in
    `RigTarget.foreign`, not refused. Only a shape whose action-100 rows
    themselves name several directories (999: twelve, one per middle field)
    is refused, with the directories listed; ``c3/monster/<dir>`` names one
    directly (`resolve_monster_dir`)."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    shape = str(shape).strip()
    rows = rows if rows is not None else motion_rows(root)
    dirs = monster_dirs(rows)
    if _norm(shape).startswith(MONSTER_PREFIX):
        return resolve_monster_dir(root, shape, assets=ar, rows=rows)
    mine = {}
    for k, v in rows.items():
        if len(k) == 9 and k[:3] == shape and v.startswith(MONSTER_PREFIX):
            mine.setdefault(v.rsplit("/", 1)[0], []).append(k)
    if not mine:
        # a bare directory name (`104n`, `900`) is accepted too
        cand = MONSTER_PREFIX + shape.lower()
        if cand in dirs:
            return _monster_family(root, cand, shape.lower(), ar, rows, dirs)
    if not mine:
        raise TargetError("shape %r has no 9-digit row under %s in ini/3dmotion.ini "
                          "(%d monster directories declared)"
                          % (shape, MONSTER_PREFIX, len(dirs)))
    how = ""
    if len(mine) == 1:
        d = next(iter(mine))
    else:
        own = shape + "000" + MONSTER_IDLE_ACTION
        idle_dirs = sorted({rows[k].rsplit("/", 1)[0] for ks in mine.values() for k in ks
                            if k[6:9] == MONSTER_IDLE_ACTION})
        if own in rows and rows[own].startswith(MONSTER_PREFIX):
            d = rows[own].rsplit("/", 1)[0]
            how = "its standby row %s names %s" % (own, rows[own])
        elif len(idle_dirs) == 1:
            d = idle_dirs[0]
            how = "every action-%s row of the shape is under %s" % (MONSTER_IDLE_ACTION, d)
        else:
            raise TargetError(
                "shape %r points at %d directories %s and its action-%s rows name "
                "%d of them %s; one skeleton per family, so name the directory "
                "(%s<dir>) instead"
                % (shape, len(mine), sorted(mine), MONSTER_IDLE_ACTION,
                   len(idle_dirs), idle_dirs, MONSTER_PREFIX))
    t = _monster_family(root, d, shape, ar, rows, dirs)
    if len(mine) > 1:
        t.foreign = {o: sorted(ks) for o, ks in sorted(mine.items()) if o != d}
        t.notes.append(
            "shape %s's rows point at %d directories; resolved to %s (%s); %s"
            % (shape, len(mine), d, how,
               "; ".join("%d row(s) under %s recorded as foreign (actions %s)"
                         % (len(ks), o, sorted({k[6:9] for k in ks})[:8])
                         for o, ks in t.foreign.items())))
    return t


# ---------------------------------------------------------------------------
# NPCs
# ---------------------------------------------------------------------------

#: How an NPC row's motion id became a file (`npc_families`, per role).
MOTION_HOW_TABLE = "ini/3dmotion.ini"
MOTION_HOW_STEM = "c3/npc/<id>.c3 (no table row)"
MOTION_HOW_NONE = "none"


def resolve_motion_id(mid, rows: dict, ar=None) -> tuple:
    """``(path or None, how)`` for ONE npc.json motion id on a CCO-profile
    install: the `ini/3dmotion.ini` row of the id FIRST, the
    ``c3/npc/<id>.c3`` stem when the id has no row.

    `core/npcart` spells the CCO rule as "the id IS the stem" and verified
    it over every row THAT HAS SUCH A FILE. 164 of the CCO snapshot's 437
    npc.json rows have none (MEASURED 2026-10-02, the rig-library
    analysis): every one of their standby ids has a `3dmotion.ini` row
    naming a file the install ships -- the geometry file itself for 96
    (``c3/npc/9990111100.c3``: TaoistMoon's standby 999012100 names the
    Sage's container), a monster directory's ``100.c3`` for 35
    (TerminalGuard and Soldier play the Guard's), a ``c3/npc/<nnn>/`` clip
    for 26, the ghost's for 5. Where BOTH exist the table and the stem
    agree on 270 of 271 rows; the one disagreement is the ChristmasReindeer
    (013_1, 79 drawn bones), whose stem ``c3/npc/9990130100.c3`` is the
    CityGate's 1-bone track and whose table row names ``c3/npc/013/100.c3``
    (84 bones, its own directory) -- the table is the anatomically right
    answer there, so it is read first. TABLE EVIDENCE: that the client
    consults the table for an NPC standby is not live-verified; the
    builder's catalog says so per file (`idleHow`)."""
    try:
        i = int(str(mid).strip())
    except (TypeError, ValueError):
        return None, MOTION_HOW_NONE
    hit = rows.get(str(i)) if rows else None
    if hit:
        return _norm(hit), MOTION_HOW_TABLE
    stem = "c3/npc/%d.c3" % i
    if ar is None or _exists(ar, stem):
        return stem, MOTION_HOW_STEM
    return None, MOTION_HOW_NONE


def npc_families(root, assets=None, rows=None) -> dict:
    """``{geometry path: [(type, name, {role: path}, missing, {role: how})]}``
    over every `ini/npc.json` row, through `core/npcart.Tables` for the
    geometry and `resolve_motion_id` for the three motion ids (the
    `3dmotion.ini` row first, the stem second; `rows` is `motion_rows`)."""
    import coassets                                          # noqa: PLC0415
    import npcart                                            # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    tables = npcart.Tables(ar.read)
    if rows is None:
        try:
            rows = motion_rows(root)
        except Exception:                                    # noqa: BLE001
            rows = {}
    out: dict = {}
    for row in tables.npcs:
        plan = tables.plan_for_npc(row)
        g = _norm(plan.geometry) if plan.geometry else ""
        motions: dict = {}
        hows: dict = {}
        missing = list(plan.missing)
        for role in npcart.MOTION_ROLES:
            short = role.replace("_motion", "")
            mid = row.get(role)
            if mid in (None, ""):
                continue
            path, how = resolve_motion_id(mid, rows, ar)
            if path is None:
                # the profile's own answer (the stem), so the row still
                # names what it declares -- the builder then reports it
                # as declared and not shipped
                path = _norm(plan.motions.get(short) or "c3/npc/%s.c3" % str(mid).strip())
                how = MOTION_HOW_NONE
                missing.append("%s id %s: no ini/3dmotion.ini row and no %s"
                               % (short, mid, path))
            motions[short] = path
            hows[short] = {"how": how, "id": str(mid).strip(),
                           "stem": _norm(plan.motions.get(short) or "")}
        out.setdefault(g, []).append((plan.npc_type, plan.name, motions, missing, hows))
    return out


def npc_key(geometry) -> str:
    """The family key of an NPC geometry: its FULL path, flattened.

    ``c3/npc/9992720.c3`` -> ``9992720`` (the id the tables print),
    ``c3/npc/013/1.c3`` -> ``013_1``, ``c3/mesh/9990010.c3`` ->
    ``mesh_9990010``. The basename alone is NOT a key: 26 geometries of the
    CCO snapshot are ``c3/npc/<nnn>/1.c3`` and 15 are ``<nnn>/100.c3``, so
    keyed by stem the Reindeer (``013/1.c3``, 79 bones drawn, a 1-bone
    standby) and the Dryad (``029/1.c3``, 40 bones) shared ``npc-1``, one
    cache file and one verdict (2026-10-02). `resolve_npc` refuses two
    geometries that still flatten to one key."""
    g = _norm(geometry)
    if g.endswith(".c3"):
        g = g[:-3]
    if g.startswith(NPC_PREFIX):
        g = g[len(NPC_PREFIX):]
    elif g.startswith("c3/"):
        g = g[3:]
    return g.replace("/", "_")


def _row5(r) -> tuple:
    """A `npc_families` row as a 5-tuple: a 4-tuple (no resolution record,
    the pre-2026-10-02 shape and the hand-built fixtures) gets an empty one."""
    r = tuple(r)
    return r if len(r) >= 5 else r + ({},) * (5 - len(r))


def resolve_npc(root, key, *, assets=None, families=None) -> RigTarget:
    """An NPC family by geometry path, npc.json type (`3`), family key
    (`9992720`, `013_1`), geometry id or name (`Pharmacist`) -> the rows
    sharing that geometry, their standby/rest/blaze clips, and the drawn mesh.

    Looked up in that order. A geometry id (the basename) shared by several
    geometries is refused with the list -- `1` names 26 of them -- and so is
    a name drawn by two geometries: two skeletons under one word."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    fams = families if families is not None else npc_families(root, ar)
    fams = {g: [_row5(r) for r in rows] for g, rows in fams.items()}
    key_s = str(key).strip()
    k_norm = _norm(key_s)
    geom = None
    how = ""
    if k_norm in fams:
        geom, how = k_norm, "geometry path"
    if geom is None:
        for g, rows in fams.items():
            if any(str(t) == key_s for t, _n, _m, _x, _h in rows):
                geom, how = g, "npc.json type %s" % key_s
                break
    if geom is None:
        key_hits = sorted(g for g in fams if g and npc_key(g) == k_norm)
        if len(key_hits) == 1:
            geom, how = key_hits[0], "family key %s" % key_s
    if geom is None:
        stem_hits = sorted(g for g in fams
                           if g and g.rsplit("/", 1)[1].rsplit(".", 1)[0] == k_norm)
        if len(stem_hits) == 1:
            geom, how = stem_hits[0], "geometry id %s" % key_s
        elif len(stem_hits) > 1:
            raise TargetError("geometry id %r names %d geometries %s%s; give the "
                              "path or the family key (%s)"
                              % (key_s, len(stem_hits), stem_hits[:4],
                                 " ..." if len(stem_hits) > 4 else "",
                                 npc_key(stem_hits[0])))
    if geom is None:
        name_hits = sorted({g for g, rows in fams.items()
                            if any(str(n).lower() == key_s.lower()
                                   for _t, n, _m, _x, _h in rows)})
        if len(name_hits) > 1:
            raise TargetError("NPC name %r is drawn by %d geometries %s; name "
                              "the geometry id" % (key_s, len(name_hits), name_hits))
        if name_hits:
            geom, how = name_hits[0], "npc.json name %s" % key_s
    if geom is None or not geom:
        raise TargetError("%r is no npc.json type, name or geometry of this "
                          "install (%d geometry families)" % (key_s, len(fams)))
    rows = fams[geom]
    fkey = npc_key(geom)
    twins = sorted(g for g in fams if g and g != geom and npc_key(g) == fkey)
    if twins:
        raise TargetError("NPC geometries %s and %s flatten to one family key %r; "
                          "the key must name ONE skeleton" % (geom, twins, fkey))
    t = RigTarget(kind=NPC, id=fkey, family="npc-%s" % fkey,
                  prefix=geom.rsplit("/", 1)[0] + "/")
    t.notes.append("resolved by %s" % how)
    paths: set = set()
    idle: dict = {}
    how: dict = {}
    for typ, name, motions, missing, hows in rows:
        for role, p in motions.items():
            paths.add(p)
            if role == NPC_IDLE_ROLE:
                idle.setdefault(p, []).append("npc.json type %s %s" % (typ, name))
                h = hows.get(role, {})
                rec = how.setdefault(p, {"how": h.get("how", MOTION_HOW_NONE), "rows": 0,
                                         "ids": [], "stemAgrees": None})
                rec["rows"] += 1
                rec["ids"].append(h.get("id"))
                if h.get("how") == MOTION_HOW_TABLE:
                    # False once ANY row's stem names another file
                    agrees = _norm(h.get("stem") or "") == _norm(p)
                    rec["stemAgrees"] = False if (not agrees or rec["stemAgrees"] is False) else True
                elif h.get("how") == MOTION_HOW_STEM and rec["stemAgrees"] is None:
                    rec["stemAgrees"] = True
                if h.get("how") != rec["how"] and not str(rec["how"]).startswith("mixed"):
                    rec["how"] = "mixed: %s / %s" % (rec["how"], h.get("how"))
        if missing:
            t.notes.append("type %s %s: %s" % (typ, name, "; ".join(missing)))
    present = sorted(p for p in paths if _exists(ar, p))
    absent = sorted(paths - set(present))
    t.paths = present
    if absent:
        t.notes.append("%d declared motion file(s) not shipped: %s"
                       % (len(absent), absent[:6]))
    t.idle = sorted(p for p in idle if p in present)
    t.aliases = {p: sorted(idle[p]) for p in t.idle}
    for rec in how.values():
        rec["ids"] = sorted({str(i) for i in rec["ids"] if i is not None})
    t.idle_how = {p: how[p] for p in t.idle}
    for p in t.idle:
        if how[p]["how"] == MOTION_HOW_TABLE and how[p]["stemAgrees"] is False:
            t.notes.append("idle %s is named by %s row(s) %s of ini/3dmotion.ini and not "
                           "by the c3/npc/<id>.c3 stem rule (table evidence; the client's "
                           "lookup is not live-verified)"
                           % (p, how[p]["rows"], how[p]["ids"][:4]))
    if not t.idle and idle:
        t.notes.append("standby file(s) %s declared (%s) and not shipped"
                       % (sorted(idle)[:4], "; ".join(sorted({h["how"] for h in how.values()}))))
    t.members = [str(typ) for typ, _n, _m, _x, _h in rows]
    t.names = sorted({str(n) for _t, n, _m, _x, _h in rows if n})
    t.drawn_mesh = geom
    t.drawn_mesh_how = "3DSimpleObj.ini Part0 -> ini/3dobj.ini"
    if not t.paths:
        raise TargetError("NPC family %s declares %d motion file(s) and this "
                          "install ships none of them" % (geom, len(paths)))
    return t


# ---------------------------------------------------------------------------
# the one entry point
# ---------------------------------------------------------------------------

def is_player_shape(shape) -> bool:
    """The player bodies: `c3/000<N>/` -- one or two digits, 1..9 on every
    client measured. A 3-digit shape is a monster's."""
    s = str(shape).strip()
    return s.isdigit() and len(s) <= 2


def resolve(root, *, shape=None, npc=None, assets=None) -> RigTarget:
    """`npc` names an NPC family; else `shape` is a player shape (the
    unchanged `bonerig` prefix path), a 3-digit monster shape, or a clip
    directory spelled ``c3/monster/<dir>``."""
    if npc not in (None, ""):
        return resolve_npc(root, npc, assets=assets)
    s = str(shape if shape is not None else "3").strip()
    if is_player_shape(s):
        return RigTarget(kind=PLAYER, id=s, family="player-%s" % s,
                         paths=None, prefix="c3/000%s/" % s)
    return resolve_monster(root, s, assets=assets)


def rig_path(target: RigTarget, root=None) -> Path:
    """Where THIS install's rig for a monster or NPC family lives:
    ``out/indexes/<base-id>/rig/<family>.json``. The player path keeps
    `bonerig.rig_path`'s name."""
    if target.kind == PLAYER:
        return player_rig_path(target.id, root)
    return coroot.derived_path("out/rig/%s.json" % target.family, root)


def player_rig_path(shape, root=None) -> Path:
    """The player space's cache file for `shape`, through whichever
    `bonerig` this checkout has: the per-shape cache (`find_rig`, from the
    2026-10-01 shapes-2/4 work: `p84-shape3.json`, with the pre-keyed
    `p84.json` still read when it holds the shape) or the space-only
    `rig_path(space, root)` before it."""
    space = "p%d" % (84 if str(shape).strip() != "1" else 81)
    find = getattr(bonerig, "find_rig", None)
    if find is not None:
        found, _legacy = find(space, shape, root)
        if found is not None:
            return Path(found)
        return bonerig.rig_path(space, root, shape)
    return bonerig.rig_path(space, root)


def solve(target: RigTarget, root, *, max_frames=bonerig.DEFAULT_MAX_FRAMES,
          assets=None, progress=None, mesh_path=None):
    """`bonerig.solve_family` on the target's clips, with the drawn mesh as
    the reference-mesh fallback (`--mesh` overrides). The rig's `solved_from`
    records the target."""
    rig = bonerig.solve_family(
        str(root), target.rig_shape, max_frames=max_frames, progress=progress,
        assets=assets,
        paths=target.paths if target.kind != PLAYER else None,
        mesh_path=mesh_path if mesh_path else (target.drawn_mesh or None))
    rig.solved_from["target"] = target.as_dict()
    return rig


def cache_mismatch(rig, target: RigTarget, root, *, assets=None,
                   mesh_path=None) -> list:
    """Why a rig read from the family cache is NOT `target`'s -- an empty
    list when it is.

    Five independent readings, any one enough: the rig's recorded shape,
    the drawn mesh its solve recorded (`solved_from.target.drawnMesh`), the
    reference mesh it was actually solved on (when that was a drawn mesh),
    the clip list (`solved_from.clipPaths`), and the BONE COUNT the target's
    own clips hold today (`bonerig.accept` over `target.paths` -- the count
    a fresh solve would use, read from headers). The Dryad-on-the-Reindeer's
    rig case fails all of them: drawn mesh ``c3/npc/013/1.c3`` against
    ``029/1.c3``, 1 bone against 40."""
    why = []
    sf = getattr(rig, "solved_from", None) or {}
    # A monster family's rig may record any shape its DIRECTORY serves: one
    # solved before 2026-10-02 through a colour morph (`--shape 226`) is the
    # 126 family's rig under the morph's number, the same clips and mesh
    # (265 shapes measured: every one draws its directory's mesh).
    served = {str(target.rig_shape)}
    if target.kind == MONSTER:
        served.update(str(m) for m in target.members)
    if str(rig.shape) not in served:
        why.append("it records shape %r, the target is %r" % (rig.shape, target.rig_shape))
    rec = sf.get("target") or {}
    if rec.get("drawnMesh") and target.drawn_mesh \
            and _norm(rec["drawnMesh"]) != _norm(target.drawn_mesh):
        why.append("it was solved for drawn mesh %s, the target draws %s"
                   % (rec["drawnMesh"], target.drawn_mesh))
    want = _norm(mesh_path or target.drawn_mesh or "")
    ref = _norm(sf.get("referenceMesh") or "")
    if str(sf.get("referenceMeshHow") or "").startswith("drawn mesh") and want \
            and ref and ref != want:
        why.append("its reference mesh is %s, the target's is %s" % (ref, want))
    cp = sf.get("clipPaths")
    if cp is not None and target.paths is not None:
        a, b = sorted(_norm(p) for p in cp), sorted(_norm(p) for p in target.paths)
        if a != b:
            why.append("it was solved from %d clip(s) (%s ...), the target lists %d (%s ...)"
                       % (len(a), a[:1], len(b), b[:1]))
    if target.paths:
        try:
            rows = bonerig.family_files(str(root), target.rig_shape, assets=assets,
                                        paths=target.paths)
            _acc, bc, _ord = bonerig.accept(rows)
        except Exception:                                    # noqa: BLE001
            bc = None
        if bc is not None and int(bc) != int(rig.bone_count):
            why.append("it has %d bones, the target's clips hold %d"
                       % (rig.bone_count, bc))
    return why


def load_or_solve(target: RigTarget, root, *, max_frames=bonerig.DEFAULT_MAX_FRAMES,
                  assets=None, progress=None, mesh_path=None, rig_file=None,
                  warnings=None) -> tuple:
    """``(rig, path, how)``: `rig_file` if given, else the cached family rig,
    else a fresh solve SAVED to the cache. `how` is "loaded" or "solved".

    A cached rig is ASSERTED to be this target's before it is used
    (`cache_mismatch`: shape, drawn mesh, reference mesh, clip list, bone
    count); one that is not raises `RigCacheMismatch` naming the file and
    every difference. It is never silently re-solved over: a mismatch means
    two families reached one cache name, and that is the finding."""
    if rig_file:
        return bonerig.load(rig_file), Path(rig_file), "loaded"
    p = rig_path(target, root)
    if p.is_file():
        rig = bonerig.load(p)
        why = cache_mismatch(rig, target, root, assets=assets, mesh_path=mesh_path)
        if why:
            raise RigCacheMismatch(
                "cached rig %s is not %s's: %s -- delete the file to re-solve, or "
                "pass --rig" % (p, target.family, "; ".join(why)))
        return rig, p, "loaded"
    rig = solve(target, root, max_frames=max_frames, assets=assets,
                progress=progress, mesh_path=mesh_path)
    bonerig.save(rig, p)
    return rig, p, "solved"


# ---------------------------------------------------------------------------
# player-bodied NPCs: borrow the player's rig
# ---------------------------------------------------------------------------

#: body-track bone count -> the player shapes of that bone-index space
#: (`bonerig.space_for_shape`: p81 is shape 1, p84 is shapes 2/3/4).
PLAYER_SPACES = {81: ("1",), 84: ("2", "3", "4")}

#: The cut on the MEAN joint residual of a player rig under the NPC's own
#: clips -- ``|J . M_child - J . M_parent|`` over every rig edge with a joint
#: and every key, in c3 units. MEASURED on the CCO snapshot, 2026-10-02
#: (three clips per body: 000/100, 000/001, 000/101; the Sage's three):
#:
#:     clips of \ rig of    shape 2    shape 3    shape 4
#:     shape 2              0.0007     4.015      13.16
#:     shape 3              1.789      0.0103     4.652
#:     shape 4              5.539      4.259      0.0065
#:     Sage 9990111100      0.849      0.0030     3.258
#:
#: The SAME skeleton reads 0.0007-0.0103, another 0.85-13.2: the cut sits
#: 10x over the highest own-shape mean and 8x under the lowest wrong one.
#: The MAX is recorded too but is not the test: one odd key moves it (own
#: shape 0.41-1.23 on these clips, 11.3 on shape 3's worst of 286), and it
#: still separates here (Sage 0.28 on shape 3, 12.2 / 37.3 on 2 / 4).
BORROW_JOINT_MEAN_CUT = 0.1

#: The share of the PLAYER reference body's skinned bones the NPC's drawn
#: mesh skins too -- RECORDED, NOT A GATE (2026-10-02). It was a cut at
#: 0.85 on the Sage's reading (26 of 28 on shape 3, 0.929) with no refusing
#: control. Measured on every 84-bone NPC body of the CCO snapshot against
#: the MUST-REFUSE control, the ChristmasReindeer (013_1: not a player body,
#: joints 15-23 mean against the player rigs):
#:
#:     body              coverage s2 / s3 / s4    joint mean on its shape
#:     Reindeer (refuse)  0.938 / 0.929 / 0.929    15.2 - 22.6 (all shapes)
#:     Sage 9990111100    0.875 / 0.929 / 0.952    0.0030 (shape 3)
#:     9990110100         0.844 / 0.929 / 0.952    0.0021 (shape 3)
#:     9990112100         0.938 / 1.000 / 0.714    0.0055 (shape 2)
#:     ArcherHerald 9990113100  0.812 / 0.857 / 0.833    0.0098 (shape 2)
#:
#: The control PASSES coverage on every shape and the ArcherHerald -- a
#: shape-2 body by its joints at 10x under the cut -- FAILED it (0.812). A
#: gate the negative control cannot fail is not evidence, and this one
#: refused a true positive: the joint residual alone decides, and the
#: coverage is written into the catalog beside it (``coverageLow`` when
#: under this number) for the eye.
BORROW_COVERAGE_MIN = 0.85


def _xf(m, p):
    """A point through a MOTI row-vector matrix (16 floats)."""
    x, y, z = p
    return (x * m[0] + y * m[4] + z * m[8] + m[12],
            x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


def _body_motions(ar, paths) -> list:
    """``[(path, Motion)]``: the body track (the MOTI with the most bones)
    of every clip that parses."""
    import c3phy                                             # noqa: PLC0415
    import effects                                           # noqa: PLC0415
    out = []
    for p in paths or ():
        try:
            best = None
            for tag, body in c3phy.iter_chunks(ar.read(p)):
                if tag[:3] != b"MOT" or len(body) < 12:
                    continue
                bc = int.from_bytes(body[0:4], "little")
                if best is None or bc > best[0]:
                    best = (bc, body)
            if best is not None:
                out.append((p, effects.parse_moti(best[1])))
        except Exception:                                    # noqa: BLE001
            continue
    return out


def joint_residual_under(rig, motions) -> dict:
    """``{"max", "mean", "keys", "edges"}``: how far `rig`'s joints come
    apart under `motions` (`_body_motions`) -- the test of "these clips
    move THIS skeleton". None values when no key of the rig's bone count
    was read."""
    par = {int(k): v for k, v in rig.parents.items()}
    edges = []
    for b, p in par.items():
        if p is None:
            continue
        lo, hi = (int(p), int(b)) if int(p) < int(b) else (int(b), int(p))
        J = (rig.joint_points or {}).get("%d-%d" % (lo, hi))
        if J is not None:
            edges.append((int(p), int(b), tuple(float(x) for x in J)))
    worst = total = 0.0
    n = keys = 0
    for _path, mo in motions:
        if int(mo.bone_count) != int(rig.bone_count):
            continue
        for key in mo.keys:
            keys += 1
            for p, b, J in edges:
                a, c = _xf(key.matrices[p], J), _xf(key.matrices[b], J)
                r = ((a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2 + (a[2] - c[2]) ** 2) ** 0.5
                total += r
                n += 1
                if r > worst:
                    worst = r
    if not n:
        return {"max": None, "mean": None, "keys": keys, "edges": len(edges)}
    return {"max": round(worst, 4), "mean": round(total / n, 5), "keys": keys,
            "edges": len(edges)}


def player_body(target: RigTarget, root, *, assets=None,
                max_frames=bonerig.DEFAULT_MAX_FRAMES, solve_missing=True) -> dict:
    """Is this NPC a PLAYER body? ``{"borrow": shape or None, "why", ...}``.

    THE TEST, three readings, all recorded:

    1. the standby's body track has a player space's bone count (81: shape
       1; 84: shapes 2/3/4). One NPC family of 24 on the CCO snapshot does
       (Sage's, 84) and no monster directory of 59.
    2. the drawn mesh skins the player's bones: the share of the bones the
       player reference body skins is RECORDED (`BORROW_COVERAGE_MIN` says
       why it is no longer a cut: the must-refuse control passes it and a
       true shape-2 body failed it).
    3. the player rig's JOINTS stay connected under the NPC's own clips:
       mean residual at most `BORROW_JOINT_MEAN_CUT`, on the shape that
       reads lowest. This is the one that names the shape -- 2, 3 and 4
       share a numbering and not a geometry -- and the one that REFUSES
       (the Reindeer: 15-23 on every shape).

    A player rig the cache lacks is solved and saved (41-59 s, once) unless
    `solve_missing` is False, in which case that shape is recorded as
    unmeasured and cannot be borrowed."""
    import bonetree                                          # noqa: PLC0415
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    out = {"borrow": None, "why": "", "boneCount": None, "candidates": {},
           "cuts": {"jointMean": BORROW_JOINT_MEAN_CUT, "coverage": BORROW_COVERAGE_MIN}}
    if target.kind != NPC or not target.idle:
        out["why"] = "not an NPC family with a standby"
        return out
    motions = _body_motions(ar, target.paths)
    idle = [m for p, m in motions if p == target.idle[0]]
    if not idle:
        out["why"] = "standby %s has no body track" % target.idle[0]
        return out
    bc = int(idle[0].bone_count)
    out["boneCount"] = bc
    shapes = PLAYER_SPACES.get(bc)
    if not shapes:
        out["why"] = ("standby body track has %d bones; the player spaces have %s"
                      % (bc, sorted(PLAYER_SPACES)))
        return out
    mesh = bonerig.reference_mesh_from_file(ar, target.drawn_mesh) if target.drawn_mesh else None
    npc_skinned = set(bonetree.bone_centroids(mesh.vertices)) if mesh is not None else set()
    out["skinned"] = len(npc_skinned)
    best = None
    for sh in shapes:
        space = bonerig.space_for_shape(sh)
        found, _legacy = bonerig.find_rig(space, sh, str(root))
        row = {"rig": str(found) if found is not None else None}
        out["candidates"][sh] = row
        if found is None:
            if not solve_missing:
                row["why"] = "no cached player rig; not solved"
                continue
            try:
                rig = bonerig.solve_family(str(root), sh, max_frames=max_frames, assets=ar)
            except Exception as e:                           # noqa: BLE001
                row["why"] = "player rig unsolvable: %s" % e
                continue
            row["rig"] = str(bonerig.save(rig, root=str(root)))
            row["solved"] = True
        else:
            rig = bonerig.load(found)
        if int(rig.bone_count) != bc:
            row["why"] = "player rig has %d bones" % rig.bone_count
            continue
        p_sk = {int(b) for b in (rig.skinned or ())}
        cov = (len(p_sk & npc_skinned) / len(p_sk)) if p_sk else 0.0
        res = joint_residual_under(rig, motions)
        row.update(coverage=round(cov, 3), playerSkinned=len(p_sk),
                   shared=len(p_sk & npc_skinned),
                   jointResidualMean=res["mean"], jointResidualMax=res["max"],
                   keys=res["keys"], edges=res["edges"],
                   referenceMesh=(rig.solved_from or {}).get("referenceMesh"))
        if res["mean"] is not None and (best is None or res["mean"] < best[0]):
            best = (res["mean"], sh)
    if best is None:
        out["why"] = "no player rig of %d bones could be measured" % bc
        return out
    mean, sh = best
    row = out["candidates"][sh]
    if mean > BORROW_JOINT_MEAN_CUT:
        out["why"] = ("%d bones like the player, but no player rig's joints hold under "
                      "its clips: best shape %s reads mean %.4g (cut %g)"
                      % (bc, sh, mean, BORROW_JOINT_MEAN_CUT))
        return out
    out["coverageLow"] = bool(row["coverage"] < BORROW_COVERAGE_MIN)
    if out["coverageLow"]:
        # recorded, never refused: the Reindeer control reads 0.93 and the
        # ArcherHerald 0.81 (see BORROW_COVERAGE_MIN)
        out["coverageNote"] = ("the mesh skins %d of shape %s's %d skinned bones (%.3f, under "
                               "the %g the Sage set); the joints decide, this is for the eye"
                               % (row["shared"], sh, row["playerSkinned"], row["coverage"],
                                  BORROW_COVERAGE_MIN))
    out["borrow"] = sh
    out["rig"] = row["rig"]
    out["referenceMesh"] = row["referenceMesh"]
    out["why"] = ("borrowed: player shape %s (%d bones; joint residual mean %.4g / max "
                  "%.4g over %d keys, cut %g; mesh skins %d of the player's %d skinned "
                  "bones)" % (sh, bc, mean, row["jointResidualMax"], row["keys"],
                              BORROW_JOINT_MEAN_CUT, row["shared"], row["playerSkinned"]))
    return out


# ---------------------------------------------------------------------------
# same-skeleton NPCs: borrow a better-evidenced family's rig, verbatim
# ---------------------------------------------------------------------------

#: borrower family -> (lender family, why). THE LENDER TABLE: a family is
#: listed here only where the rig-library analysis (2026-10-02, the CCO
#: snapshot, 107 skinned bodies in 2,548 pairs) MEASURED the two to be the
#: same skeleton -- the lender's joints, used VERBATIM, stay connected under
#: the borrower's OWN clips to within `BORROW_SEPARATION_TOL` of the
#: borrower's height on every edge those clips articulate:
#:
#:     borrower          lender             worst articulated edge   the lender on itself
#:     npc-9992690       npc-9992720        0.32 units = 0.19% of H   0.39%
#:       Maud, Pedlar    Pharmacist         (20 edges judged, 1 untested: neck)
#:     npc-9992670       npc-9992680        0.0004 units = 0.00%      0.05%
#:       Bruce, Evan, Mike, Shelby,  Boxer  (18 judged, 1 untested: finger)
#:       Watson, William, 韩逍子
#:
#: and six MUST-REFUSE controls (another skeleton whose bone indices overlap:
#: Boxer <- Guard, 153 <- Guard, GuildDirector <- Guard, 999007100 <-
#: Pharmacist, Reindeer <- shape 3, 9992670 <- Guard) read 16% - 97% of
#: height. Same TOPOLOGY is not enough: the Guard 900 shares the
#: Pharmacist's tree and 20/20 labels and its joints come apart by 1.4% of
#: height on the Pharmacist's clips (4.7% the other way) -- both are
#: controls that REFUSE. The table is never taken on its word: `borrow_lender`
#: re-measures the separation at build time and refuses above the cut.
LENDERS = {
    "npc-9992690": ("npc-9992720",
                    "Maud/Pedlar's 30-bone body skins the Pharmacist's 22 bone indices on "
                    "the same joints (scale 1.000, translation 0.06 units; worst articulated "
                    "edge 0.19% of height on its own 3 clips, 2026-10-02)"),
    "npc-9992670": ("npc-9992680",
                    "Bruce/Evan/Mike/Shelby/Watson/William's 26-bone body is the Boxer's "
                    "skeleton (scale 1.000, translation 0.0, joint fit rms 0.0004 units over "
                    "18 edges on its own 3 clips, 2026-10-02)"),
}

#: The cut on the WORST articulated-edge joint separation of a borrowed rig
#: under the borrower's own clips, as a fraction of the borrower's mesh
#: height. The accepted own rigs read 0.36% (Guard 900 -- the rig the
#: owner verified in game), 0.39% (Pharmacist), 0.44% (153), 0.05% (Boxer)
#: on their own clips: 0.5% admits nothing looser than what already ships,
#: and the must-refuse controls sit at 16% and above -- two orders of
#: magnitude of room on either side.
BORROW_SEPARATION_TOL = 0.005


def resolve_family(root, family: str, *, assets=None) -> RigTarget:
    """A family KEY (``monster-900``, ``npc-9992720``, ``npc-013_1``) -> its
    `RigTarget`, the inverse of `RigTarget.family`."""
    f = str(family).strip()
    if f.startswith("monster-"):
        return resolve_monster_dir(root, f[len("monster-"):], assets=assets)
    if f.startswith("npc-"):
        return resolve_npc(root, f[len("npc-"):], assets=assets)
    if f.startswith("player-"):
        return resolve(root, shape=f[len("player-"):], assets=assets)
    raise TargetError("%r is no family key (monster-<dir>, npc-<key>, player-<shape>)" % f)


def mesh_height(mesh) -> float:
    """The z extent of a skinned mesh's vertices (c3 units; +Z is down)."""
    zs = [float(v.position[2]) for v in mesh.vertices]
    return (max(zs) - min(zs)) if zs else 0.0


def _xf34(M, p):
    return tuple(M[r][0] * p[0] + M[r][1] * p[1] + M[r][2] * p[2] + M[r][3] for r in range(3))


def edge_separation(rig, motions, *, skinned=None) -> dict:
    """Per rig edge, on `motions` (`_body_motions`, the bone count's): the
    joint SEPARATION ``|J . M_parent - J . M_child|`` (max and rms over
    every key), the pair's relative motion (`bonejoint.relative_motion`)
    and whether any clip ARTICULATES it (``>= MIN_RELATIVE_MOTION``). An
    edge no clip articulates is UNTESTED -- any point satisfies a rigid
    pair, so its zero is not evidence. `skinned` limits the judged set to
    edges whose two bones the mesh skins: the ones a viewer can see part.
    -> ``{"edges": {child: {...}}, "judged", "untested", "worst",
    "worstEdge", "keys"}``."""
    import bonejoint                                         # noqa: PLC0415
    par = {int(k): (None if v is None else int(v)) for k, v in rig.parents.items()}
    bc = int(rig.bone_count)
    frames_by_clip = []
    keys = 0
    for _p, mo in motions:
        if int(mo.bone_count) != bc:
            continue
        fr = bonejoint.matrices_by_key(mo)
        if fr:
            frames_by_clip.append(fr)
            keys += len(fr)
    edges: dict = {}
    judged = []
    untested = []
    worst = (None, None)
    for b, p in sorted(par.items()):
        if p is None or b >= bc or p >= bc:
            continue
        J = (rig.joint_points or {}).get("%d-%d" % (min(p, b), max(p, b)))
        rec: dict = {"parent": p, "joint": J is not None}
        vis = skinned is None or (b in skinned and p in skinned)
        rec["skinned"] = bool(vis)
        mot = 0.0
        mx = 0.0
        ss = 0.0
        n = 0
        for fr in frames_by_clip:
            mot = max(mot, float(bonejoint.relative_motion(fr, p, b)))
            if J is None:
                continue
            for f in fr:
                a, c = _xf34(f[p], J), _xf34(f[b], J)
                d = ((a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2 + (a[2] - c[2]) ** 2) ** 0.5
                mx = max(mx, d)
                ss += d * d
                n += 1
        rec["relMotion"] = round(mot, 5)
        rec["articulated"] = mot >= bonejoint.MIN_RELATIVE_MOTION
        if J is not None and n:
            rec["sepMax"] = round(mx, 4)
            rec["sepRms"] = round((ss / n) ** 0.5, 4)
            if vis:
                if rec["articulated"]:
                    judged.append(b)
                    if worst[0] is None or mx > worst[0]:
                        worst = (mx, "%d<-%d" % (p, b))
                else:
                    untested.append(b)
        edges[str(b)] = rec
    return {"edges": edges, "judged": judged, "untested": untested,
            "worst": None if worst[0] is None else round(worst[0], 4),
            "worstEdge": worst[1], "keys": keys, "clips": len(frames_by_clip)}


def borrow_lender(target: RigTarget, root, *, assets=None, lender=None,
                  lender_target: RigTarget = None, lender_rig=None,
                  max_frames=bonerig.DEFAULT_MAX_FRAMES,
                  tol: float = BORROW_SEPARATION_TOL) -> dict:
    """Can `target` wear ANOTHER family's rig? ``{"borrow": lender family or
    None, "why", "rig", "lender", "lenderRig", "transform", "residuals",
    "cuts", ...}``.

    The lender is `LENDERS[target.family]` unless `lender` (a family key)
    or `lender_target` names one -- the tests and the CLI pass a wrong one
    on purpose. The lender's rig is loaded from the family cache (or
    solved); its parents and joint points are taken VERBATIM (scale 1, no
    translation: the two accepted pairs register at 1.000 / 0.06 units,
    and the bind-centroid transform that would have "corrected" them
    reads 2.1% of height on the Boxer pair -- the skinned centroids of two
    bodies on one skeleton differ, their joints do not), restricted to
    bones below the BORROWER's bone count, and the skinned set is the
    borrower's drawn mesh's.

    THE RE-CHECK, every time, never the table's word: `edge_separation` of
    that rig under the borrower's OWN clips. Refused when no skinned edge
    is articulated (the test would be vacuous), or when the worst
    articulated edge separates by more than `tol` of the borrower's mesh
    height. The lender's untested edges (ones the borrower's clips never
    move) are recorded, not refused: the lender's own clips tested them."""
    import bonetree                                          # noqa: PLC0415
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    out = {"borrow": None, "why": "", "lender": None, "lenderRig": None,
           "transform": {"scale": 1.0, "translate": [0.0, 0.0, 0.0], "how": "verbatim"},
           "cuts": {"separationOverHeight": tol}}
    if target.kind != NPC or not target.idle or not target.paths:
        out["why"] = "not an NPC family with a shipped standby"
        return out
    key = None
    table_why = ""
    if lender_target is not None:
        key = lender_target.family
    elif lender:
        key = str(lender)
    elif target.family in LENDERS:
        key, table_why = LENDERS[target.family]
    if key is None:
        out["why"] = "no lender: %s is not in the lender table (%d families)" % (
            target.family, len(LENDERS))
        return out
    out["lender"] = key
    out["lenderWhy"] = table_why
    if key == target.family:
        out["why"] = "a family cannot lend to itself"
        return out
    try:
        lt = lender_target if lender_target is not None else resolve_family(root, key, assets=ar)
    except TargetError as e:
        out["why"] = "lender %s does not resolve: %s" % (key, e)
        return out
    if lender_rig is None:
        try:
            lender_rig, lp, how = load_or_solve(lt, root, max_frames=max_frames, assets=ar)
        except Exception as e:                               # noqa: BLE001
            out["why"] = "lender %s has no rig: %s: %s" % (key, e.__class__.__name__, e)
            return out
        out["lenderRig"] = str(lp)
        out["lenderRigHow"] = how
    # -- the borrower's own facts ------------------------------------------
    rows = bonerig.family_files(str(root), target.rig_shape, assets=ar, paths=target.paths)
    acc, bc, ordinal = bonerig.accept(rows)
    if not bc:
        out["why"] = "the borrower's clips %s hold no body track" % target.paths[:3]
        return out
    bc = int(bc)
    mesh = bonerig.reference_mesh_from_file(ar, target.drawn_mesh) if target.drawn_mesh else None
    if mesh is None:
        out["why"] = "the borrower's drawn mesh %s has no skinned PHY" % target.drawn_mesh
        return out
    skinned = sorted(int(b) for b in bonetree.bone_centroids(mesh.vertices))
    h = mesh_height(mesh)
    out.update(boneCount={"borrower": bc, "lender": int(lender_rig.bone_count)},
               height=round(h, 3), skinned=len(skinned))
    # -- the lender's rig, verbatim, in the borrower's numbering ------------
    parents = {}
    dropped = []
    for b, p in lender_rig.parents.items():
        b = int(b)
        p = None if p is None else int(p)
        if b >= bc or (p is not None and p >= bc):
            dropped.append(b)
            continue
        parents[b] = p
    joints = {}
    for k, J in (lender_rig.joint_points or {}).items():
        a, b = (int(x) for x in k.split("-"))
        if a < bc and b < bc:
            joints[k] = tuple(float(x) for x in J)
    uncovered = sorted(b for b in skinned if b not in parents)
    rig = bonerig.FamilyRig(
        space="p%d" % bc, shape=target.rig_shape, bone_count=bc,
        body_ordinal=int(ordinal or 0), parents=parents, joint_points=joints,
        welded={}, socket_constants={}, skinned=skinned,
        census=dict(getattr(lender_rig, "census", {}) or {}),
        solved_from={"how": "borrowed verbatim from %s" % key, "lender": key,
                     "lenderRig": out["lenderRig"],
                     "lenderShape": str(lender_rig.shape),
                     "transform": dict(out["transform"]),
                     "referenceMesh": target.drawn_mesh,
                     "referenceMeshHow": "drawn mesh (the borrower's; the rig is the lender's)",
                     "clipPaths": list(target.paths), "clipPrefix": target.prefix,
                     "accepted": [r.path for r in acc], "refused": [],
                     "lenderBonesDropped": dropped, "skinnedNotInLender": uncovered,
                     "target": target.as_dict()})
    # -- THE RE-CHECK: the lender's joints under the borrower's clips ------
    motions = _body_motions(ar, target.paths)
    sep = edge_separation(rig, motions, skinned=set(skinned))
    tol_units = tol * h
    over = {b: sep["edges"][str(b)]["sepMax"] for b in sep["judged"]
            if sep["edges"][str(b)]["sepMax"] > tol_units}
    out["residuals"] = {
        "worstSeparation": sep["worst"], "worstEdge": sep["worstEdge"],
        "worstOverHeight": (round(sep["worst"] / h, 5) if sep["worst"] is not None and h else None),
        "toleranceUnits": round(tol_units, 4), "judgedEdges": len(sep["judged"]),
        "untestedEdges": sep["untested"], "edgesOverTolerance": {str(b): v for b, v in sorted(over.items())},
        "keys": sep["keys"], "clips": sep["clips"],
        "lenderBonesDropped": dropped, "skinnedNotInLender": uncovered,
        "edges": sep["edges"]}
    out["rig"] = rig
    if not sep["clips"]:
        out["why"] = ("borrow from %s REFUSED: no clip of %s holds a %d-bone body track"
                      % (key, target.family, bc))
        return out
    if not sep["judged"]:
        out["why"] = ("borrow from %s REFUSED: no skinned edge of the lender's rig is "
                      "articulated by %s's own clips (%d keys) -- the separation test "
                      "would be vacuous" % (key, target.family, sep["keys"]))
        return out
    if over:
        out["why"] = ("borrow from %s REFUSED: %d of %d articulated edges separate by more "
                      "than %.2f units (%.1f%% of height %.1f): worst %s at %.2f = %.1f%%"
                      % (key, len(over), len(sep["judged"]), tol_units, 100 * tol, h,
                         sep["worstEdge"], sep["worst"], 100 * sep["worst"] / h))
        return out
    out["borrow"] = key
    out["why"] = ("borrowed: %s (%s's rig verbatim; worst articulated edge %s separates "
                  "%.3f units = %.2f%% of height %.1f over %d keys, cut %.1f%%; %d edge(s) "
                  "judged, %d untested%s)"
                  % (key, key, sep["worstEdge"], sep["worst"], 100 * sep["worst"] / h, h,
                     sep["keys"], 100 * tol, len(sep["judged"]), len(sep["untested"]),
                     "; lender bones %s dropped" % dropped if dropped else ""))
    return out


# ---------------------------------------------------------------------------
# the skin tree: what the drawn mesh says hangs off what, confirmed by the
# family's own clips
# ---------------------------------------------------------------------------
#
# WHY A FOURTH ROUTE. `bonerig.solve_family` solves a family's skeleton from
# its clips alone, and on the town NPC bodies that is three clips of 20-40
# keys: enough to place the joints the clips move, not enough to say which
# bones are NEIGHBOURS -- junction coincidences win ties and the labeller
# reads them as hat chains and second legs (seven families, 191 npc.json
# rows, refused by the label gate with no lender in the snapshot). The
# two-weight skin tree (`core/bonetree`) has nothing to read on them either:
# 11 of 23 NPC body meshes and 17 of 59 monster meshes carry NO blended
# vertex at all -- they are rigidly skinned, one bone per vertex (the
# rig-library analysis, 2026-10-02: that route admits 0 families, median
# worst-edge separation 41% of height).
#
# What every one of those meshes DOES carry is FACES that span two bones:
# the triangles that close a sleeve onto a hand, a shin onto a foot. That is
# the mesh's own statement of adjacency, and `seam_table` reads it (plus
# WELDS -- duplicated vertices of two bones at one position, a hard seam --
# and the blends where they exist). Adjacency is not a joint, so every seam
# is then put to the family's own clips with the shipped solver's per-clip
# vote (`bonejoint.solve_from_clips`, the seam centroid as the hinge prior):
# a seam the clips ARTICULATE and SOLVE under `MAX_RESIDUAL` is CONFIRMED and
# takes the solved point; one the clips never move is UNCONFIRMED (the joint
# stays at the seam centroid -- any point satisfies a rigid pair, so it is
# recorded, never claimed); one the clips contradict (they move the pair with
# no common point) is VETOED and never enters the tree. A maximum spanning
# forest over confirmed-then-unconfirmed seams is the tree; what it leaves
# apart is bridged by proximity (`bonetree.link_components`, the one inferred
# step, recorded per edge) -- and `c3retarget.skin_tree_route` refuses the
# family if a MAPPED bone hangs on such a bridge.
#
# MEASURED on the seven town families (analyst's prototype, 2026-10-02, the
# same rules re-derived here): seams confirmed / unconfirmed / vetoed
# 21/3/0 (999009100), 25/4/0 (9990010), 23/1/0 (999007100), 16/21/0
# (999008100: robe panels), 19/5/0 (9990050), 27/1/0 (999006100),
# 14/11/1 (999004100, two clips only). The confirmed joints are PARTLY
# CIRCULAR -- fitted on the clips they are then measured against -- so the
# independent evidence is the gate, MOTION 0.93-0.99, the donor's lasso read
# right in 67-68 of 68 frames, hover +0.4..+10, and the eye.

#: `seam_table` rounds a vertex position to this many decimals to find a
#: WELD: two vertices of different bones at one place.
SEAM_WELD_DECIMALS = 2

# ---------------------------------------------------------------------------
# THREE REPAIRS TO THE TREE, 2026-10-03 (the owner: six town families "are
# missing thigh.L or thigh.R, but I dont think they should be"). They were
# right: every one of those meshes has legs. The tree lost them three ways,
# each MEASURED on the CCO snapshot:
#
# 1. A CONFIRMED JOINT CAN BE SOLVED FROM A CLIP THAT BARELY MOVES THE PAIR.
#    A hinge leaves the point along its axis unknowable, so the per-clip
#    solve can land anywhere on that line: the Barber's (9992660) hip joint
#    sat 704 units OUTSIDE the body (y = 690), held to 0.3 / 0.6 units in two
#    idle clips and separated by 81.9 in the third, which bends at the waist.
#    `skin_tree_from` now keeps the shipped point unless it separates by more
#    than the tolerance on SOME clip; then it takes whichever of the shipped
#    point, a solve over every clip at once and the seam centroid holds best
#    on all of them (a near-tie, within `JOINT_TIE_UNITS`, to the point
#    nearest the seam). The Barber's hip: the all-clips solve, 3.7 from the
#    seam, worst 0.69. A joint that already holds is never moved, so a family
#    that built before builds byte-identical.
# 2. THE PER-CLIP VETO IS STRICTER THAN THE FINAL CHECK. `solve_from_clips`
#    drops a seam whose per-clip residual passes `bonejoint.MAX_RESIDUAL`
#    (0.43 RMS units); the Armorer's (999004100) left hip read 0.29 by the
#    vote and was dropped, and the leg then hung on the RIGHT thigh by
#    proximity, so the hips had one leg and the labeller found none. A
#    vetoed seam whose all-clips joint separates within the re-check's own
#    tolerance is now READMITTED before anything is bridged (the mesh says
#    the two pieces touch; every clip says they hold).
# 3. A BRIDGE BY PROXIMITY IGNORED THE CLIPS. A piece the seams leave apart
#    (arms modelled as separate parts: the Taoists 9992650/9992730, General
#    Shou 9992710) hung on the nearest centroid even when the clips move the
#    two independently. It now hangs on the NEAREST bone among the pairs the
#    clips articulate whose solved joint stays near the pair and separates
#    within `SKIN_TREE_PIECE_TOL` of the height -- CLIP-BRIDGED, recorded per
#    edge. Only a piece no clip can place is bridged by proximity as before.
#
# `SKIN_TREE_PIECE_TOL` is looser than the seam tolerance on purpose and on
# the record: two SEPARATE pieces share no vertex, so a drifting joint opens
# a gap between them rather than tearing a skin. Measured on the town arms:
# 1.4-2.2% of height under the families' own idles. It is a judgement about
# what a gap at a shoulder looks like in game, and the viewer is its check.
#
# A STITCHED seam (the mesh joins the two; the per-clip vote dropped it) is
# readmitted only within the seam tolerance by default. `stitched_tol` lets a
# build readmit it within a looser one, on the record per edge: the town
# shoulders of the Taoists (9992650/9992730) and General Shou (9992710) drift
# 1.4-2.2% of height under their idles, and the seam stretch that buys was
# MEASURED on the built dance (every two-bone triangle edge, posed / bind,
# p95 / p99): Taoists 2.16 / 4.09 and General Shou 2.54 / 6.20 against 1.83-
# 1.91 / 2.71-3.28 on three families the owner approved. Whether that reads
# as a stretched shoulder in game is the owner's eye, so it is not a default.
JOINT_TIE_UNITS = 0.05
SKIN_TREE_PIECE_TOL = 0.025
#: repairs 2 and 3 on (the default) or off: off, every piece hangs by
#: proximity exactly as `bonetree.link_components` hangs it -- the tree as it
#: was before 2026-10-03, kept so a control can reproduce it on demand
SKIN_TREE_LINK_BY_CLIPS = True
#: candidate parents for a clip bridge lie within this fraction of the
#: height of the child's centroid
SKIN_TREE_BRIDGE_REACH = 0.35
#: a clip bridge's solved joint lies within this fraction of the height of
#: the pair's centroid midpoint; one further out is the hinge-axis artefact
#: of repair 1, not a joint
SKIN_TREE_BRIDGE_JOINT_REACH = 0.10


def dominant_bone(v) -> int:
    """The bone a vertex mostly belongs to: slot 0 unless slot 1 names
    another bone and carries MORE weight (ties go to slot 0, as the
    engine's palette order does)."""
    a, b = int(v.bone0), int(v.bone1)
    if a == b:
        return a
    return a if float(v.weight0) >= float(v.weight1) else b


def seam_table(mesh) -> dict:
    """``{(lo, hi): {"faces", "welds", "blends", "vertices", "point"}}``:
    every pair of bones the DRAWN MESH joins, and where.

    * ``faces``: triangles whose vertices' dominant bones include both.
    * ``welds``: positions (rounded to `SEAM_WELD_DECIMALS`) holding a
      vertex of each -- a hard seam with duplicated vertices.
    * ``blends``: vertices weighted between the two (`bonetree.MIN_BLEND`).
    * ``point``: the centroid of every vertex of either bone on those
      faces, welds and blends -- the SEAM, the hinge prior the clips refine.

    Duck-typed on `c3phy.PhyMesh` (``vertices`` with ``position``,
    ``bone0/1``, ``weight0/1``; ``faces`` as index triples)."""
    import bonetree                                          # noqa: PLC0415
    verts = list(mesh.vertices)
    dom = [dominant_bone(v) for v in verts]
    acc: dict = {}

    def rec(a, b):
        k = (a, b) if a < b else (b, a)
        return acc.setdefault(k, {"faces": 0, "welds": 0, "blends": 0, "vs": set()})

    for f in (getattr(mesh, "faces", None) or ()):
        idx = [int(i) for i in f if 0 <= int(i) < len(verts)]
        bs = sorted({dom[i] for i in idx})
        if len(bs) < 2:
            continue
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                r = rec(bs[i], bs[j])
                r["faces"] += 1
                r["vs"].update(k for k in idx if dom[k] in (bs[i], bs[j]))
    pos: dict = {}
    for i, v in enumerate(verts):
        pos.setdefault(tuple(round(float(x), SEAM_WELD_DECIMALS) for x in v.position), []).append(i)
    for _p, idxs in pos.items():
        bs = sorted({dom[i] for i in idxs})
        if len(bs) < 2:
            continue
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                r = rec(bs[i], bs[j])
                r["welds"] += 1
                r["vs"].update(k for k in idxs if dom[k] in (bs[i], bs[j]))
    for i, v in enumerate(verts):
        a, b = int(v.bone0), int(v.bone1)
        if a == b:
            continue
        if float(v.weight0) > bonetree.MIN_BLEND and float(v.weight1) > bonetree.MIN_BLEND:
            r = rec(a, b)
            r["blends"] += 1
            r["vs"].add(i)
    out: dict = {}
    for k, r in sorted(acc.items()):
        pts = [tuple(float(x) for x in verts[i].position) for i in sorted(r["vs"])]
        c = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
        out[k] = {"faces": r["faces"], "welds": r["welds"], "blends": r["blends"],
                  "vertices": len(pts), "point": c}
    return out


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _worst_sep(frames, a, b, J) -> float:
    """The largest distance between where `a` and `b` put the point `J`,
    over every key of `frames` (3x4 matrices, `bonejoint.matrices_by_key`)."""
    mx = 0.0
    for f in frames:
        p, q = _xf34(f[a], J), _xf34(f[b], J)
        mx = max(mx, ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2) ** 0.5)
    return mx


def _place_failing_joints(cand, used, pooled, tol_units) -> list:
    """Repair 1 (module comment above `JOINT_TIE_UNITS`): a CONFIRMED seam
    joint that separates by more than `tol_units` on some key is replaced by
    the best-holding of the shipped point, the all-clips solve and the seam
    centroid. Mutates the `cand` records; returns what it moved."""
    import bonejoint                                         # noqa: PLC0415
    moved = []
    for k in sorted(used):
        rec = cand[k][0]
        if rec.get("how") != "confirmed" or rec.get("joint") is None:
            continue
        a, b = k
        shipped = tuple(float(x) for x in rec["joint"])
        s0 = _worst_sep(pooled, a, b, shipped)
        if s0 <= tol_units:
            continue
        seam = tuple(float(x) for x in rec["seamPoint"])
        cands = [("per-clip", shipped, s0)]
        _res, J = bonejoint.solve_joint(pooled, a, b, seam)
        if J is not None:
            J = tuple(float(x) for x in J)
            cands.append(("all clips", J, _worst_sep(pooled, a, b, J)))
        cands.append(("seam centroid", seam, _worst_sep(pooled, a, b, seam)))
        best = min(s for _n, _p, s in cands)
        name, P, s = min((c for c in cands if c[2] <= best + JOINT_TIE_UNITS),
                         key=lambda c: _dist(c[1], seam))
        if name == "per-clip":
            continue
        rec["joint"] = [round(x, 4) for x in P]
        rec["movedFromSeam"] = round(_dist(P, seam), 3)
        rec["jointFrom"] = name
        rec["jointWas"] = {"joint": [round(x, 4) for x in shipped], "sepMax": round(s0, 4)}
        moved.append({"edge": "%d-%d" % k, "from": name, "sepMaxWas": round(s0, 4),
                      "sepMax": round(s, 4), "movedFromSeam": rec["movedFromSeam"]})
    return moved


def _link_pieces(parent, cents, frames_by_clip, pooled, h, tol, cand,
                 stitched_tol=None) -> dict:
    """Repairs 2 and 3: hang every piece the seams leave apart, in ROUNDS.
    Each round places the piece with the best clip-backed attachment to what
    is already placed: a VETOED seam whose all-clips joint holds within
    `tol` (READMITTED) ranks first, then the nearest CLIP-BRIDGED pair
    (articulated by some clip, joint solved over every clip near the pair,
    separation within `SKIN_TREE_PIECE_TOL`). Rounds, not one pass, because
    a held prop must wait for the hand it hangs from (the Taoists' 9992650:
    a single largest-first pass offered the prop before the arm and hung it
    on a guess). When no piece has a clip-backed attachment, the next piece
    in `bonetree.link_components`' own order goes by proximity, exactly its
    step -- so a mesh no clip can place builds as it always did. Mutates
    `parent`; returns ``{(lo, hi): record}`` for the readmitted and
    clip-bridged edges (the caller records proximity bridges itself)."""
    import bonejoint                                         # noqa: PLC0415
    made: dict = {}

    def root_of(b):
        while parent.get(b) is not None:
            b = parent[b]
        return b

    def reroot(a):
        path = [a]
        while parent.get(path[-1]) is not None:
            path.append(parent[path[-1]])
        for i in range(len(path) - 1, 0, -1):
            parent[path[i]] = path[i - 1]
        parent[a] = None

    comps: dict = {}
    for b in parent:
        comps.setdefault(root_of(b), []).append(b)
    if len(comps) < 2:
        return made
    order = sorted(comps.values(), key=lambda c: (-len(c), min(c)))
    linked = set(order[0])
    tol_units, piece_units = tol * h, SKIN_TREE_PIECE_TOL * h
    stitched_units = max(tol, stitched_tol if stitched_tol is not None else tol) * h
    solved: dict = {}

    def joint_for(p, c, prior):
        """The all-clips joint of (p, c) and its worst separation, cached."""
        if (p, c) not in solved:
            res, J = bonejoint.solve_joint(pooled, p, c, prior)
            if J is None:
                solved[(p, c)] = None
            else:
                J = tuple(float(x) for x in J)
                solved[(p, c)] = (J, _worst_sep(pooled, p, c, J), float(res))
        return solved[(p, c)]

    def best_for(cs):
        """The best clip-backed attachment of the piece `cs` to `linked`:
        ``(rank, record)`` or None. A readmitted seam ranks first; then a
        clip bridge whose joint holds within the seam tolerance; then one
        that holds only within `SKIN_TREE_PIECE_TOL`; within a tier, the
        nearer pair of centroids."""
        if not SKIN_TREE_LINK_BY_CLIPS:
            return None
        best = None
        for (a, b), (rec, _w) in sorted(cand.items()):
            if rec.get("how") != "vetoed":
                continue
            for c, p in ((a, b), (b, a)):
                if c not in cs or p not in linked:
                    continue
                got = joint_for(p, c, tuple(rec["seamPoint"]))
                if got is None or got[1] > stitched_units:
                    continue
                rank = (0 if got[1] <= tol_units else 1, got[1], 0.0, c, p)
                if best is None or rank < best[0]:
                    best = (rank, {"how": "readmitted", "child": c, "parent": p,
                                   "joint": got[0], "sepMax": got[1], "residual": got[2],
                                   "loose": got[1] > tol_units})
        if best is not None:
            return best
        # A piece the mesh STITCHES to what is placed (a vetoed seam joins
        # them) is readmitted under the seam tolerance or not at all: never
        # clip-bridged under the looser piece tolerance, which is for pieces
        # that share no vertex. Measured on the synthetic hip drifting 0.6-0.9
        # units a frame: without this the stitched thigh passed as a "gap"
        # at 1.0-1.5 units, the tear the seam tolerance exists to refuse.
        for (a, b), (rec, _w) in cand.items():
            if rec.get("how") == "vetoed" and (
                    (a in cs and b in linked) or (b in cs and a in linked)):
                return None
        for c in sorted(cs):
            if c not in cents:
                continue
            for p in sorted(linked):
                if p not in cents:
                    continue
                d = _dist(cents[c], cents[p])
                if d > SKIN_TREE_BRIDGE_REACH * h:
                    continue
                if max(float(bonejoint.relative_motion(fr, p, c))
                       for fr in frames_by_clip) < bonejoint.MIN_RELATIVE_MOTION:
                    # RIGID in every clip: no evidence against the pair, and
                    # no joint to solve. It competes in the seam-tolerance
                    # tier by distance, as the proximity bridge it is (the
                    # midpoint, untested) -- so a hand rigid with its forearm
                    # stays on the forearm rather than on the upper arm the
                    # clips CAN place it on (the synthetic, measured).
                    mid = tuple((cents[c][i] + cents[p][i]) / 2.0 for i in range(3))
                    rank = (2, d, 0.0, c, p)
                    if best is None or rank < best[0]:
                        best = (rank, {"how": "bridged", "child": c, "parent": p,
                                       "joint": mid, "sepMax": 0.0, "residual": 0.0})
                    continue
                mid = tuple((cents[c][i] + cents[p][i]) / 2.0 for i in range(3))
                got = joint_for(p, c, mid)
                if got is None or got[1] > piece_units:
                    continue
                if _dist(got[0], mid) > SKIN_TREE_BRIDGE_JOINT_REACH * h:
                    continue
                # TIERS before distance: a pair whose joint holds within the
                # seam tolerance outranks any that holds only within the
                # looser piece tolerance. Measured on the synthetic leg cut
                # in two: by distance alone the shin hung on the OTHER foot
                # (it passes 2.5% and is nearer) instead of its own thigh.
                rank = (2 if got[1] <= tol_units else 3, d, got[1], c, p)
                if best is None or rank < best[0]:
                    best = (rank, {"how": "clip-bridged", "child": c, "parent": p,
                                   "joint": got[0], "sepMax": got[1], "residual": got[2]})
        return best

    remaining = [list(c) for c in order[1:]]
    while remaining:
        # each round places the piece with the best clip-backed attachment,
        # so a held prop waits for the hand it hangs from
        pick = None
        for idx, comp in enumerate(remaining):
            got = best_for(set(comp))
            if got is not None and (pick is None or got[0] < pick[0]):
                pick = (got[0], idx, got[1])
        if pick is not None:
            _rank, idx, rec = pick
            comp = remaining.pop(idx)
            c, p = rec["child"], rec["parent"]
            reroot(c)
            parent[c] = p
            if rec["how"] != "bridged":
                # a rigid pair is a proximity bridge: the caller records it
                # as one (midpoint joint, untested), exactly as before
                made[(min(c, p), max(c, p))] = rec
            linked.update(comp)
            continue
        # no piece has one: proximity for the next piece in
        # `bonetree.link_components`' own order, exactly its step
        comp = remaining.pop(0)
        best = None
        for a in comp:
            ca = cents.get(a)
            if ca is None:
                continue
            for tb in linked:
                ct = cents.get(tb)
                if ct is None:
                    continue
                dd = sum((ca[i] - ct[i]) ** 2 for i in range(3))
                if best is None or dd < best[0]:
                    best = (dd, a, tb)
        if best is not None:
            _dd, a, tb = best
            reroot(a)
            parent[a] = tb
        linked.update(comp)
    return made


def skin_tree_from(mesh, motions, bone_count: int, *, shape="skin", body_ordinal: int = 0,
                   tol: float = BORROW_SEPARATION_TOL, solved_from: dict = None,
                   stitched_tol: float = None) -> dict:
    """The skin tree of ONE mesh under ITS clips -- the pure half of
    `skin_tree`, so a synthetic mesh and synthetic clips can run it.

    `motions` is ``[(path, Motion)]`` (`_body_motions`); clips of another
    bone count are not read. -> ``{"ok", "why", "rig", "mesh", "edges",
    "edgeHow", "confirmed", "unconfirmed", "vetoed", "vetoedEdges",
    "bridged", "cycleEdges", "componentsBeforeBridging", "residuals",
    "seams", "height", "keys", "clips", "cuts"}``: ``edges`` is keyed by
    child bone with how its edge was placed (confirmed / unconfirmed /
    bridged, the votes, the residual, the seam point and the joint);
    ``edgeHow`` by ``"lo-hi"`` for a reader that re-roots the tree. The
    separation re-check (`edge_separation`, the lender route's) refuses the
    tree whose worst ARTICULATED skinned edge separates by more than `tol`
    of the mesh height, or that no clip articulates at all."""
    import bonejoint                                         # noqa: PLC0415
    import bonetree                                          # noqa: PLC0415
    bc = int(bone_count)
    cents = {int(b): tuple(float(x) for x in c)
             for b, c in bonetree.bone_centroids(mesh.vertices).items()}
    mass = {int(b): float(w) for b, w in bonetree.bone_mass(mesh.vertices).items()}
    skinned = sorted(cents)
    h = mesh_height(mesh)
    out: dict = {"ok": False, "why": "", "boneCount": bc, "height": round(h, 3),
                 "skinned": skinned, "mesh": mesh,
                 "cuts": {"separationOverHeight": tol,
                          "jointResidual": bonejoint.MAX_RESIDUAL,
                          "relativeMotion": bonejoint.MIN_RELATIVE_MOTION}}
    if not skinned:
        out["why"] = "the drawn mesh skins no bone"
        return out
    over = [b for b in skinned if b >= bc]
    if over:
        out["why"] = ("the drawn mesh skins bone(s) %s and the body track has %d bones: "
                      "another skeleton than the clips'" % (over, bc))
        return out
    seams = seam_table(mesh)
    out["seams"] = {"%d-%d" % k: {"faces": v["faces"], "welds": v["welds"],
                                  "blends": v["blends"], "vertices": v["vertices"],
                                  "point": [round(x, 4) for x in v["point"]]}
                    for k, v in seams.items()}
    frames_by_clip = []
    keys = 0
    for _p, mo in motions:
        if int(mo.bone_count) != bc:
            continue
        fr = bonejoint.matrices_by_key(mo)
        if fr:
            frames_by_clip.append(fr)
            keys += len(fr)
    out["clips"] = len(frames_by_clip)
    out["keys"] = keys
    if not frames_by_clip:
        out["why"] = "no clip holds a %d-bone body track to confirm the seams on" % bc
        return out
    pooled = [f for fr in frames_by_clip for f in fr]
    # -- every seam put to the clips: confirmed, unconfirmed or vetoed -------
    cand: dict = {}
    for (a, b), s in seams.items():
        pt = tuple(s["point"])
        graph, census = bonejoint.solve_from_clips(frames_by_clip, [a, b],
                                                   centroids={a: pt, b: pt})
        votes = tuple(int(x) for x in census.get((a, b), (0, 0, 0)))
        weight = int(s["faces"]) + int(s["welds"]) + int(s["blends"])
        rec = {"faces": s["faces"], "welds": s["welds"], "blends": s["blends"],
               "seamPoint": [round(x, 4) for x in pt], "votes": list(votes)}
        if (a, b) in graph:
            res, p = graph[(a, b)]
            rec.update(how="confirmed", residual=round(float(res), 6),
                       joint=[round(float(x), 4) for x in p],
                       movedFromSeam=round(_dist(p, pt), 3))
        elif votes[0] == 0 and votes[1] == 0:
            rec.update(how="unconfirmed", residual=None, joint=list(rec["seamPoint"]),
                       note="no clip articulates the pair: the joint is the seam centroid")
        else:
            r_pool, p_pool = bonejoint.solve_joint(pooled, a, b, pt)
            rec.update(how="vetoed",
                       residual=None if p_pool is None else round(float(r_pool), 6),
                       joint=None,
                       note="the clips move the pair with no common point: dropped")
        cand[(a, b)] = (rec, weight)
    confirmed = sorted((k for k, (r, _w) in cand.items() if r["how"] == "confirmed"),
                       key=lambda k: (cand[k][0]["residual"], -cand[k][1], k))
    unconfirmed = sorted((k for k, (r, _w) in cand.items() if r["how"] == "unconfirmed"),
                         key=lambda k: (-cand[k][1], k))
    # -- a maximum spanning forest: confirmed seams first, then unconfirmed --
    up = {b: b for b in skinned}

    def find(x):
        while up[x] != x:
            up[x] = up[up[x]]
            x = up[x]
        return x

    keep: dict = {b: set() for b in skinned}
    used: set = set()
    cycle: list = []
    for a, b in confirmed + unconfirmed:
        ra, rb = find(a), find(b)
        if ra == rb:
            cycle.append("%d-%d" % (a, b))                   # the mesh loops: a skirt on both legs
            continue
        up[ra] = rb
        keep[a].add(b)
        keep[b].add(a)
        used.add((a, b))
    comps: dict = {}
    for b in skinned:
        comps.setdefault(find(b), []).append(b)
    parent: dict = {}
    for comp in comps.values():
        root = max(comp, key=lambda b: (mass.get(b, 0.0), -b))
        parent[root] = None
        queue, seen = [root], {root}
        while queue:
            x = queue.pop(0)
            for y in sorted(keep[x]):
                if y not in seen:
                    seen.add(y)
                    parent[y] = x
                    queue.append(y)
    n_comp = len(comps)
    # repair 1: a confirmed joint that fails the clips is re-placed
    joints_moved = _place_failing_joints(cand, used, pooled, tol * h)
    # repairs 2 and 3, then the ONE inferred step, labelled as such: a piece
    # no seam and no clip can place hangs on the nearest centroid, exactly
    # as `bonetree.link_components` hangs it
    pieces = _link_pieces(parent, cents, frames_by_clip, pooled, h, tol, cand,
                          stitched_tol=stitched_tol)
    edges: dict = {}
    edge_how: dict = {}
    joints: dict = {}
    bridged: list = []
    for b, p in sorted(parent.items()):
        if p is None:
            continue
        k = (b, p) if b < p else (p, b)
        key = "%d-%d" % k
        if k in pieces:
            pc = pieces[k]
            base = cand[k][0] if k in cand else {}
            rec = {"how": pc["how"], "parent": p,
                   "faces": base.get("faces", 0), "welds": base.get("welds", 0),
                   "blends": base.get("blends", 0), "votes": base.get("votes"),
                   "residual": round(pc["residual"], 6),
                   "seamPoint": base.get("seamPoint"),
                   "joint": [round(x, 4) for x in pc["joint"]],
                   "sepMax": round(pc["sepMax"], 4), "loose": bool(pc.get("loose")),
                   "note": ("a VETOED seam whose joint, solved over every clip, holds within "
                            "the re-check's tolerance" if pc["how"] == "readmitted" else
                            "no seam joins the two pieces; the nearest pair the clips "
                            "articulate, joint solved over every clip, within %.1f%% of "
                            "height (SKIN_TREE_PIECE_TOL)" % (100 * SKIN_TREE_PIECE_TOL))}
            edge_how[key] = pc["how"]
            joints[key] = tuple(float(x) for x in pc["joint"])
        elif k in used:
            rec = dict(cand[k][0], parent=p)
            edge_how[key] = rec["how"]
            joints[key] = tuple(float(x) for x in rec["joint"])
        else:
            ca, cb = cents[b], cents[p]
            mid = tuple((ca[i] + cb[i]) / 2.0 for i in range(3))
            rec = {"how": "bridged", "parent": p, "faces": 0, "welds": 0, "blends": 0,
                   "votes": None, "residual": None, "seamPoint": None,
                   "joint": [round(x, 4) for x in mid],
                   "note": "no face, weld or blend joins the two; attached to the nearest "
                           "centroid (bonetree.link_components), the joint their midpoint"}
            edge_how[key] = "bridged"
            joints[key] = mid
            bridged.append(key)
        edges[str(b)] = rec
    vetoed = {"%d-%d" % k: r for k, (r, _w) in sorted(cand.items()) if r["how"] == "vetoed"}
    readmitted = sorted("%d-%d" % k for k, pc in pieces.items() if pc["how"] == "readmitted")
    clip_bridged = sorted("%d-%d" % k for k, pc in pieces.items() if pc["how"] == "clip-bridged")
    roots = sorted(b for b, p in parent.items() if p is None)
    out.update(confirmed=len(confirmed), unconfirmed=len(unconfirmed), vetoed=len(vetoed),
               vetoedEdges=vetoed, bridged=bridged, cycleEdges=cycle,
               readmitted=readmitted, clipBridged=clip_bridged, jointsMoved=joints_moved,
               componentsBeforeBridging=n_comp, roots=roots, edges=edges, edgeHow=edge_how)
    how = ("skin tree: the drawn mesh's face adjacency, %d seam(s) confirmed on the "
           "family's own clips, %d unconfirmed (joint at the seam), %d vetoed, %d bridged "
           "by proximity" % (len(confirmed), len(unconfirmed), len(vetoed), len(bridged)))
    if readmitted or clip_bridged or joints_moved:
        how += ("; %d vetoed seam(s) readmitted, %d piece(s) clip-bridged, %d joint(s) "
                "re-placed" % (len(readmitted), len(clip_bridged), len(joints_moved)))
    sf = dict(solved_from or {})
    sf.update(how=how, seams=len(seams), confirmed=len(confirmed),
              unconfirmed=len(unconfirmed), vetoed=sorted(vetoed), bridged=list(bridged),
              cycleEdges=list(cycle), componentsBeforeBridging=n_comp, keys=keys,
              clips=len(frames_by_clip))
    if readmitted or clip_bridged or joints_moved:
        sf.update(readmitted=readmitted, clipBridged=clip_bridged,
                  jointsMoved=[m["edge"] for m in joints_moved])
    rig = bonerig.FamilyRig(
        space="p%d" % bc, shape=str(shape), bone_count=bc, body_ordinal=int(body_ordinal),
        parents={int(b): (None if p is None else int(p)) for b, p in parent.items()},
        joint_points={k: tuple(round(float(x), 6) for x in v) for k, v in joints.items()},
        welded={}, socket_constants={}, skinned=list(skinned),
        census={"%d-%d" % k: tuple(r["votes"]) for k, (r, _w) in cand.items()},
        solved_from=sf)
    out["rig"] = rig
    # -- THE RE-CHECK: the lender route's, on the family's own clips --------
    sep = edge_separation(rig, motions, skinned=set(skinned))
    tol_units = tol * h
    piece_units = SKIN_TREE_PIECE_TOL * h
    stitched_units = max(tol, stitched_tol if stitched_tol is not None else tol) * h

    def _edge_limit(rec):
        # a CLIP-BRIDGED edge joins two separate pieces: judged as a gap; a
        # loosely READMITTED seam at the build's stitched tolerance; the rest
        # at the seam tolerance
        if rec.get("how") == "clip-bridged":
            return piece_units
        if rec.get("how") == "readmitted":
            return stitched_units
        return tol_units
    # a CLIP-BRIDGED edge joins two separate pieces: judged as a gap, not a tear
    over = {b: sep["edges"][str(b)]["sepMax"] for b in sep["judged"]
            if sep["edges"][str(b)]["sepMax"] > _edge_limit(edges.get(str(b), {}))}
    out["residuals"] = {
        "worstSeparation": sep["worst"], "worstEdge": sep["worstEdge"],
        "worstOverHeight": (round(sep["worst"] / h, 5) if sep["worst"] is not None and h else None),
        "toleranceUnits": round(tol_units, 4), "pieceToleranceUnits": round(piece_units, 4),
        "stitchedToleranceUnits": round(stitched_units, 4),
        "judgedEdges": len(sep["judged"]),
        "untestedEdges": sep["untested"],
        "edgesOverTolerance": {str(b): v for b, v in sorted(over.items())},
        "keys": sep["keys"], "clips": sep["clips"], "edges": sep["edges"]}
    if not sep["judged"]:
        out["why"] = ("no skinned edge of the tree is articulated by the family's own clips "
                      "(%d keys): every joint would be a seam centroid, untested" % sep["keys"])
        return out
    if over:
        out["why"] = ("%d of %d articulated edges separate by more than %.2f units (%.1f%% of "
                      "height %.1f) on the family's own clips: worst %s at %.2f = %.1f%%"
                      % (len(over), len(sep["judged"]), tol_units, 100 * tol, h,
                         sep["worstEdge"], sep["worst"], 100 * sep["worst"] / h))
        return out
    out["ok"] = True
    out["why"] = how
    return out


def skin_tree(target: RigTarget, root, *, assets=None, tol: float = BORROW_SEPARATION_TOL,
              stitched_tol: float = None) -> dict:
    """`skin_tree_from` on a family: its DRAWN mesh (`RigTarget.drawn_mesh`)
    and every clip of its own (`target.paths`) that holds the standby's
    bone count. Never a player shape (the player's rig is solved from
    hundreds of clips and verified), and nothing without an idle."""
    import coassets                                          # noqa: PLC0415
    ar = assets if assets is not None else coassets.AssetRoot.bare(str(root))
    if target.kind == PLAYER or not target.idle or not target.paths:
        return {"ok": False, "why": "not a monster or NPC family with a shipped standby"}
    rows = bonerig.family_files(str(root), target.rig_shape, assets=ar, paths=target.paths)
    acc, bc, ordinal = bonerig.accept(rows)
    if not bc:
        return {"ok": False, "why": "the family's clips %s hold no body track" % target.paths[:3]}
    mesh = bonerig.reference_mesh_from_file(ar, target.drawn_mesh) if target.drawn_mesh else None
    if mesh is None:
        return {"ok": False, "why": "the drawn mesh %s has no skinned PHY to read a tree from"
                % (target.drawn_mesh or "(none named)")}
    motions = _body_motions(ar, target.paths)
    out = skin_tree_from(
        mesh, motions, int(bc), shape=target.rig_shape, body_ordinal=int(ordinal or 0), tol=tol,
        stitched_tol=stitched_tol,
        solved_from={"root": str(root), "referenceMesh": target.drawn_mesh,
                     "referenceMeshHow": "drawn mesh (%s): its faces are the tree"
                                         % target.drawn_mesh_how,
                     "clipPaths": list(target.paths), "clipPrefix": target.prefix,
                     "accepted": [r.path for r in acc], "refused": [],
                     "target": target.as_dict()})
    out["family"] = target.family
    out["drawnMesh"] = target.drawn_mesh
    return out


def _main(argv=None) -> int:
    import argparse                                          # noqa: PLC0415
    ap = argparse.ArgumentParser(description="Resolve a rig target.")
    ap.add_argument("--root", default=None)
    ap.add_argument("--shape", default=None)
    ap.add_argument("--npc", default=None)
    ap.add_argument("--list", choices=("monsters", "npcs"), default=None)
    a = ap.parse_args(argv)
    root = str(a.root or coroot.default_root())
    if a.list == "monsters":
        for d in shipping_monster_dirs(root):
            print(d)
        return 0
    if a.list == "npcs":
        for g, rows in sorted(npc_families(root).items()):
            print("%-28s %3d rows  %s" % (g, len(rows),
                                          ", ".join(sorted({str(n) for _t, n, _m, _x, _h in rows})[:4])))
        return 0
    t = resolve(root, shape=a.shape, npc=a.npc)
    print(json.dumps(t.as_dict(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
