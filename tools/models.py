#!/usr/bin/env python3
r"""
models.py -- everything that is a *model* rather than an assembled character:
monsters, NPCs, ghosts, the one shipped mount family, the character-select
roles, and the effect library.

    py -3 tools/models.py --kinds
    py -3 tools/models.py --list monster
    py -3 tools/models.py --model monster:103
    py -3 tools/models.py --monsters            # ini/monster.json, as render props
    py -3 tools/models.py --audit

WHY THIS IS NOT THE CHARACTER BUILDER
-------------------------------------
A player character is an *assembly*: one body mesh, one motion set per action,
and parts hung on named sockets.  `tools/builder.py` is that model and it is
correct for `armor.ini` / `armet.ini` / `weapon.ini`.

**A monster is not built that way.**  `c3/monster/103/` is sixteen files, one
per action, and each one carries its own `PHY` *and* its own `MOTI`:

    c3/monster/103/100.c3   PHY + MOTI + CAME     idle
    c3/monster/103/110.c3   PHY + MOTI + CAME     walk
    c3/monster/103/401.c3   PHY + MOTI + CAME     attack

so "play the walk" means **load a different mesh file**, not bind a different
track over one mesh.  There are no equip slots to fill; the choice is
*which model* and *which action*.  Forcing that into the character builder's
slot model would be wrong in both directions, so this is a second mode.

TWO LAYOUTS SHIP, AND BOTH ARE REAL -- VERIFIED
-----------------------------------------------
Measured over the 65 directories under `c3/monster/` and the 35 under
`c3/npc/` in this build:

    per-action meshes    every action file carries PHY + MOTI
                         c3/monster/103/, 121/, 122/, 207/, 222/, ghosts
    skeleton + motions   one geometry file (`1.c3`, or `<shape>000000.c3`) plus
                         motion-ONLY action files -- the player pattern
                         c3/monster/198/ (3 PHY4 + sockets), c3/npc/013/,
                         c3/mount/850/

and a great many directories are *mixed*: `c3/monster/103/` ships `315`, `340`
and `341` as motion-only files alongside thirteen self-contained ones.  So the
rule is per action, not per family:

    if the action file has geometry   -> it is both the mesh and the motion
    otherwise                         -> bind it over the family's base mesh

`C3Mesh::SetMotion` binds **by ordinal** either way (`graphic.dll 0x277C0`,
docs/attachment.md §3), which is why the same code path serves both.

WHAT NAMES A MODEL
------------------
* **Monsters: nothing does.**  `ini/monster.json`'s `type` is a sequential
  index 1..374 and its `bodyType` is `0` on every one of the 374 rows, so there
  is no join column to the art at all -- the server picks the appearance at
  spawn.  This module therefore browses monsters **by mesh directory** and lets
  the user *optionally* attach a `monster.json` row to borrow its render
  properties.  That pairing is the user's assertion, never presented as one the
  data makes.  See `MONSTER_LINK_NOTE`.
* **NPCs: `ini/npc.json` does.**  `standby_motion` is a literal `3dmotion.ini`
  key and **435 of its 437 rows resolve to a file that ships**, which is a far
  better link than `simple_object` -> `3DSimpleObj.ini` (only 100 of 437 of
  those meshes are on disk).  Both are used; the motion path wins.
* **Roles: `ini/3DsimpleRole.ini` does** -- `Role0`..`Role7`, each naming a
  `3DStandByMotion` and a `3DBlazeMotion` that resolve to `c3/mesh/9998xx0.C3`.

WHAT `ini/monster.json` IS GOOD FOR
----------------------------------
It carries real render properties even though it carries no art link:

    zoomPercent   60 .. 350   -- monsters are SCALED, sometimes drastically.
                                 Drawing one at 100% is simply the wrong size.
    bornAction    315 or 101  -- the action played on spawn
    bornEffect    MBStandard / MBGhost
    asb / adb     D3DBLEND source/dest factors for the body
    sizeAdd       0..5        -- unit unknown
    actResCtrl    0/1/24      -- probably the ini/ActionCtrl.ini gate
                                 (docs/animation.md §7.3), UNKNOWN

TEXTURES
--------
`tools/meshtex.py` (imported, never edited) is the authority and it reaches
these trees where the same-stem guess does not: `c3/monster/103/100.c3` has no
sibling `.dds` at all and meshtex resolves it to `c3/texture/103000000.dds`
through `3dmotion.ini 103000100 -> armor.ini [103000000]`.  Coverage in this
build: 636/636 `c3/monster`, 165/167 `c3/npc`, 4/4 `c3/ghost`, 1/1 `c3/mount`.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT, parse_ini              # noqa: E402

try:                                                      # task #20, imported
    import anim as animmod                                # noqa: E402
except Exception:                                         # pragma: no cover
    animmod = None


# ---------------------------------------------------------------------------
# the families this module knows how to browse
# ---------------------------------------------------------------------------

#: kind -> (plural label, singular label, one-line description)
KINDS: dict[str, tuple[str, str, str]] = {
    "monster": ("Monsters", "monster",
                "one directory per creature under c3/monster/, one file per "
                "action"),
    "npc": ("NPCs", "NPC",
            "c3/npc/<id>/ — a directory per NPC, named by ini/npc.json"),
    "npc_simple": ("NPCs (standby set)", "NPC",
                   "the flat c3/npc/999<type><action>.c3 files ini/npc.json "
                   "names through standby_motion / rest_motion / blaze_motion"),
    "ghost": ("Ghosts", "ghost",
              "c3/ghost/098 and 099 — shapes 98 and 99 in 3dmotion.ini"),
    "mount": ("Mounts", "mount",
              "c3/mount/850 — the only mount family whose art ships here"),
    "role": ("Character-select roles", "role",
             "ini/3DsimpleRole.ini Role0..Role7, the class-choice figures"),
    "effect": ("Effects", "effect",
               "ini/3DEffect.ini names, played through the same fx.js the "
               "asset browser uses"),
}

KIND_ORDER = ("monster", "npc", "npc_simple", "ghost", "mount", "role", "effect")

#: kinds whose models are a mesh + an action list (as opposed to an effect,
#: which is a layered particle/quad scene on its own clock).
POSED_KINDS = ("monster", "npc", "npc_simple", "ghost", "mount", "role")

#: The directory each mesh-backed kind lives under.
KIND_TREE = {"monster": "c3/monster/", "npc": "c3/npc/",
             "ghost": "c3/ghost/", "mount": "c3/mount/"}


MONSTER_LINK_NOTE = (
    "ini/monster.json carries NO link to the art. Its `type` is a sequential "
    "index 1–374 and `bodyType` is 0 on all 374 rows, so nothing in the "
    "shipped data says which mesh directory a monster uses — the server picks "
    "the appearance when it spawns the creature. Monsters are therefore "
    "browsed by mesh directory. Attaching a monster.json row is YOUR pairing, "
    "not one the data asserts; it is worth doing because that row does carry "
    "real render properties (zoomPercent 60–350 above all).")

ZOOM_NOTE = (
    "zoomPercent is the one monster.json field that visibly changes the "
    "render: it runs 60 to 350 across the 374 rows, so a creature drawn at "
    "100% can be nearly four times too small. It is applied as a uniform "
    "scale on the model matrix. VERIFIED as a field; that it is a percentage "
    "of the authored size is INFERRED from its range and its name.")

LAYOUT_NOTE = (
    "A monster is not one mesh with a motion track per action — it is one "
    "FILE per action, and most of those files carry their own geometry. So "
    "changing the action here changes the mesh being drawn. Where a family "
    "does ship a shared skeleton (c3/monster/198/1.c3 and friends) the "
    "motion-only action files bind over it by ordinal, exactly as a player "
    "body does. Both layouts ship and both are handled.")

MISSING_NOTE = (
    "1,100 of the 3,260 motion files ini/3dmotion.ini names are absent from "
    "this install (63% of all keys point at one). An action whose file does "
    "not ship is listed and disabled with that reason, rather than being "
    "hidden or offered as a dead control.")


# ---------------------------------------------------------------------------
# one action of one model
# ---------------------------------------------------------------------------

@dataclass
class ModelAction:
    """One action code a model can play, already checked against disk."""
    code: str
    label: str = ""
    group: str = "action"
    group_label: str = ""
    confidence: str = "unknown"
    evidence: str = ""
    motion: str = ""            # the file 3dmotion.ini (or the directory) gives
    mesh: str = ""              # the geometry this action poses
    self_contained: bool = False
    available: bool = True
    source: str = ""            # "3dmotion.ini" | "directory"
    key: str = ""               # the 3dmotion.ini key, when there is one
    reason: str = ""
    chain: Optional[str] = None
    named: bool = True
    #: False when an earlier action code already resolves to this same file.
    #: `ini/3dmotion.ini` gives monster 103 eighty action codes over sixteen
    #: files -- `404`, `405`, `407` and `903` are all `401.c3` -- so the list
    #: can offer "distinct clips only" without hiding what the ini declares.
    distinct: bool = True
    alias_of: str = ""

    def to_json(self) -> dict:
        return {"code": self.code, "label": self.label, "group": self.group,
                "groupLabel": self.group_label, "confidence": self.confidence,
                "evidence": self.evidence, "motion": self.motion,
                "mesh": self.mesh, "selfContained": self.self_contained,
                "available": self.available, "source": self.source,
                "key": self.key, "reason": self.reason, "chain": self.chain,
                "named": self.named, "distinct": self.distinct,
                "aliasOf": self.alias_of}


@dataclass
class Model:
    """One previewable model: a mesh (or a set of them) plus its actions."""
    kind: str
    ident: str
    label: str = ""
    detail: str = ""
    mesh: str = ""              # the representative geometry
    texture: str = ""
    texture_method: str = ""
    texture_kind: str = ""
    directory: str = ""
    shape: str = ""             # the ini/3dmotion.ini shape, when there is one
    #: every shape whose keys reach this directory. Several usually do --
    #: c3/monster/105/ is named by shape 105 AND shape 249 -- so the art is
    #: shared between creatures and `shape` is only the one whose action list
    #: is offered.
    shapes: list[str] = field(default_factory=list)
    actions: list[ModelAction] = field(default_factory=list)
    names: list[str] = field(default_factory=list)   # names from npc.json etc.
    files: int = 0
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ident}"

    @property
    def playable(self) -> int:
        return sum(1 for a in self.actions if a.available)

    @property
    def clips(self) -> int:
        """Distinct motion files behind those actions."""
        return len({a.motion for a in self.actions if a.available})

    def action(self, code: str) -> Optional[ModelAction]:
        for a in self.actions:
            if a.code == code:
                return a
        return None

    def default_action(self) -> str:
        for want in ("100", "standby", "101", "110", "1"):
            a = self.action(want)
            if a is not None and a.available:
                return a.code
        for a in self.actions:
            if a.available:
                return a.code
        return self.actions[0].code if self.actions else ""

    def to_json(self, *, with_actions: bool = False) -> dict:
        out = {
            "key": self.key, "kind": self.kind, "id": self.ident,
            "label": self.label, "detail": self.detail,
            "mesh": self.mesh, "texture": self.texture,
            "textureMethod": self.texture_method,
            "textureKind": self.texture_kind,
            "dir": self.directory, "shape": self.shape,
            "shapes": self.shapes,
            "names": self.names, "files": self.files,
            "actions": len(self.actions), "playable": self.playable,
            "clips": self.clips, "layout": _layout_key(self),
            "defaultAction": self.default_action(), "note": self.note,
        }
        if with_actions:
            out["actionList"] = [a.to_json() for a in self.actions]
        return out


# ---------------------------------------------------------------------------
# ini/monster.json -- render properties, deliberately not an art link
# ---------------------------------------------------------------------------

@dataclass
class MonsterRow:
    type: int
    name: str
    zoom_percent: int = 100
    size_add: int = 0
    max_life: int = 0
    level: int = 0
    born_action: str = ""
    born_effect: str = ""
    born_sound: str = ""
    act_res_ctrl: int = 0
    asb: int = 5
    adb: int = 6
    body_type: int = 0

    def to_json(self) -> dict:
        return {"type": self.type, "name": self.name,
                "zoomPercent": self.zoom_percent, "sizeAdd": self.size_add,
                "maxLife": self.max_life, "level": self.level,
                "bornAction": self.born_action, "bornEffect": self.born_effect,
                "bornSound": self.born_sound, "actResCtrl": self.act_res_ctrl,
                "asb": self.asb, "adb": self.adb, "bodyType": self.body_type,
                "scale": round(self.zoom_percent / 100.0, 4)}


def load_monster_rows(root: Path | str = DEFAULT_ROOT) -> list[MonsterRow]:
    p = Path(root) / "ini" / "monster.json"
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text("utf-8", errors="replace"))
    except Exception:                                     # pragma: no cover
        return []
    out = []
    for r in raw:
        try:
            out.append(MonsterRow(
                type=int(r.get("type", 0) or 0), name=str(r.get("name", "")),
                zoom_percent=int(r.get("zoomPercent", 100) or 100),
                size_add=int(r.get("sizeAdd", 0) or 0),
                max_life=int(r.get("maxLife", 0) or 0),
                level=int(r.get("level", 0) or 0),
                born_action=str(r.get("bornAction", "") or ""),
                born_effect=str(r.get("bornEffect", "") or ""),
                born_sound=str(r.get("bornSound", "") or ""),
                act_res_ctrl=int(r.get("actResCtrl", 0) or 0),
                asb=int(r.get("asb", 5) or 5), adb=int(r.get("adb", 6) or 6),
                body_type=int(r.get("bodyType", 0) or 0)))
        except Exception:                                 # pragma: no cover
            continue
    return out


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

_ACT_RE = re.compile(r"^\d{1,3}$")


def _spaced(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return s.replace("~", " ").replace("_", " ").replace("`", "'").strip()


class ModelCatalogue:
    """Every previewable non-character model, with its action list.

    Built from three sources and nothing else:

    * the **path set** -- what geometry actually ships, per directory;
    * **ini/3dmotion.ini** -- which action codes exist for a shape and which
      file each one names (through `anim.MotionIndex`, imported);
    * the **naming tables** -- `ini/npc.json`, `ini/3DsimpleRole.ini`,
      `ini/3DSimpleObj.ini`.

    `has_geometry` decides whether an action file is a mesh or a motion set.
    It is answered from `tools/meshtex.py`'s coverage index when that is
    available (instant, 4,964 meshes) and by opening the file otherwise.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT, *,
                 paths: Optional[Iterable[str]] = None,
                 exists: Optional[Callable[[str], bool]] = None,
                 read: Optional[Callable[[str], bytes]] = None,
                 has_geometry: Optional[Callable[[str], Optional[bool]]] = None,
                 texture_for: Optional[Callable[[str], Optional[dict]]] = None,
                 effect_names: Optional[Callable[[], list[str]]] = None,
                 motion_index=None):
        self.root = Path(root)
        self.paths: set[str] = {p.replace("\\", "/").lower()
                                for p in (paths or [])}
        self._exists = exists or (lambda p: p.lower() in self.paths)
        self._read = read
        self._geom_hint = has_geometry
        self._texture_for = texture_for
        self._effect_names = effect_names
        self._geom_cache: dict[str, bool] = {}

        self.index = motion_index
        if self.index is None and animmod is not None:
            try:
                self.index = animmod.MotionIndex(self.root)
            except Exception:                             # pragma: no cover
                self.index = None

        self.monsters: list[MonsterRow] = load_monster_rows(self.root)
        self.models: list[Model] = []
        self.by_key: dict[str, Model] = {}
        self._build()

    # -- helpers -----------------------------------------------------------
    def exists(self, logical: str) -> bool:
        if not logical:
            return False
        return bool(self._exists(logical))

    def has_geometry(self, logical: str) -> bool:
        """Does this `.c3` carry a `PHY`-family chunk?

        1,426 of the 6,390 `.c3` files in this install carry none at all
        (`docs/meshtex.md` §1) -- 453 of them under `c3/monster` -- so this is
        the difference between "the action ships its own mesh" and "the action
        is a motion set that has to be bound over one".
        """
        key = logical.lower()
        hit = self._geom_cache.get(key)
        if hit is not None:
            return hit
        val: Optional[bool] = None
        if self._geom_hint is not None:
            try:
                val = self._geom_hint(key)
            except Exception:                             # pragma: no cover
                val = None
        if val is None and self._read is not None:
            try:
                data = self._read(logical)
                val = _has_phy(data)
            except Exception:
                val = None
        if val is None:
            val = False
        self._geom_cache[key] = bool(val)
        return bool(val)

    def texture(self, mesh: str) -> tuple[str, str, str]:
        """`(path, method, kind)` for a mesh, through meshtex when supplied."""
        if not mesh or self._texture_for is None:
            return "", "", ""
        try:
            rec = self._texture_for(mesh)
        except Exception:                                 # pragma: no cover
            rec = None
        if not rec:
            return "", "", ""
        return (rec.get("texture", ""), rec.get("method", ""),
                rec.get("kind", ""))

    # -- building ----------------------------------------------------------
    def _build(self) -> None:
        fams = self._directory_families()
        ini_actions = self._ini_actions()
        self._build_directory_models(fams, ini_actions)
        self._build_flat_npc_models(ini_actions)
        self._build_role_models()
        self._attach_npc_names()
        self._build_effect_models()
        # A family with nothing playable is kept (--audit reports it) but the
        # default query hides it: a list row that draws nothing is worse than
        # no row. In this install that is `npc_simple:217`, whose two files are
        # both motion-only and whose look id is not referenced by npc.json, so
        # there is no authored route to a base mesh -- and 13 effect names that
        # 3DEffect.ini defines with no resolving layer.
        for m in self.models:
            if m.kind != "effect" and m.playable == 0:
                m.note = (
                    f"{m.directory} ships {m.files} file(s) but none of them "
                    f"carries geometry, and no shipped table links this look to "
                    f"a base mesh — so there is nothing to bind the motion over. "
                    f"Listed here for the record; hidden from the picker.")
        self.models.sort(key=lambda m: (KIND_ORDER.index(m.kind)
                                        if m.kind in KIND_ORDER else 99,
                                        _sortkey(m.ident)))
        self.by_key = {m.key: m for m in self.models}

    def _directory_families(self) -> dict[tuple[str, str], list[str]]:
        """`(kind, dir) -> [stem, ...]` for every `c3/<tree>/<dir>/*.c3`."""
        out: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
        for p in self.paths:
            if not p.endswith(".c3"):
                continue
            for kind, tree in KIND_TREE.items():
                if not p.startswith(tree):
                    continue
                rest = p[len(tree):]
                if "/" not in rest:
                    continue
                d, f = rest.split("/", 1)
                if "/" in f:
                    continue
                out[(kind, d)].append(f[:-3])
                break
        return out

    def _ini_actions(self) -> dict[str, list[tuple[str, str, str]]]:
        """Motion paths grouped by the directory they land in.

        `dir key -> [(action, key, path)]` where the dir key is the logical
        directory of the motion file (`c3/monster/103` or `c3/npc` for the
        flat ones).  This is what makes shape `104` find directory `104n`
        without a hardcoded alias: the *value* says where the file is.
        """
        out: dict[str, list[tuple[str, str, str, str]]] = \
            collections.defaultdict(list)
        if self.index is None:
            return out
        for mk in self.index.keys:
            v = (mk.path or "").replace("\\", "/").lower()
            if not v.endswith(".c3"):
                continue
            d = v.rsplit("/", 1)[0]
            out[d].append((mk.action, mk.raw, v, mk.shape))
        return out

    def _build_directory_models(self, fams, ini_actions) -> None:
        for (kind, d), stems in sorted(fams.items()):
            tree = KIND_TREE[kind]
            dirpath = f"{tree}{d}"
            files = {s: f"{dirpath}/{s}.c3" for s in stems}
            geom = {s: self.has_geometry(p) for s, p in files.items()}
            base = _pick_base(files, geom)
            rows = ini_actions.get(dirpath, [])
            shape, all_shapes = self._shape_for(dirpath, rows)
            # Only this shape's rows. Several shapes reach one directory
            # (`105` and `249` both reach c3/monster/105/) and unioning them
            # would offer 220 "actions" over 13 files.
            mine = [r for r in rows if r[3] == shape] or rows
            actions = self._actions_for_dir(dirpath, files, geom, base, mine)
            if not actions:
                continue
            rep = base or next((files[a.code] for a in actions
                                if a.self_contained and a.code in files), "")
            if not rep:
                rep = next((a.mesh for a in actions if a.mesh), "")
            tex, meth, tkind = self.texture(rep)
            m = Model(kind=kind, ident=d, mesh=rep, texture=tex,
                      texture_method=meth, texture_kind=tkind,
                      directory=dirpath, shape=shape, actions=actions,
                      files=len(files))
            m.shapes = all_shapes
            m.label = _default_label(kind, d)
            m.detail = _layout_of(actions, base)
            self.models.append(m)

    def _actions_for_dir(self, dirpath, files, geom, base,
                         ini_rows) -> list[ModelAction]:
        """Union of what `3dmotion.ini` names and what the directory holds.

        The ini is the authority on which action *codes* exist (shape `103`
        declares 80 codes that resolve to 16 distinct files), and the
        directory is the authority on what actually ships.
        """
        by_code: dict[str, ModelAction] = {}
        for action, key, path, _shape in sorted(ini_rows):
            stem = path.rsplit("/", 1)[-1][:-3]
            have = self.exists(path)
            a = by_code.get(action)
            if a is not None and a.available:
                continue
            sc = have and geom.get(stem, self.has_geometry(path))
            mesh = path if sc else (base or "")
            by_code[action] = ModelAction(
                code=action, motion=path, mesh=mesh, self_contained=bool(sc),
                available=bool(have and mesh), source="3dmotion.ini", key=key,
                reason=("" if have else
                        f"{path} is named by ini/3dmotion.ini but does not "
                        f"ship in this install.")
                if not have else
                ("" if mesh else
                 "this action is motion-only and the family ships no base mesh "
                 "to bind it over."))
        # files present that no ini key names -- never drop art
        for stem, path in sorted(files.items()):
            if not _ACT_RE.match(stem):
                continue
            # `1.c3` is the family's skeleton, not an action. Offering it as
            # "action 001" is noise -- unless it is the only file there is, in
            # which case it is the one thing you can look at.
            if path == base and any(a.available for a in by_code.values()):
                continue
            code = stem.zfill(3)
            if code in by_code and by_code[code].available:
                continue
            sc = geom.get(stem, False)
            mesh = path if sc else (base or "")
            by_code[code] = ModelAction(
                code=code, motion=path, mesh=mesh, self_contained=bool(sc),
                available=bool(mesh), source="directory",
                reason="" if mesh else
                "motion-only file with no base mesh in this directory.")
        for a in by_code.values():
            _name_action(a)
            if a.source == "directory" and a.motion == base and \
                    animmod is not None and a.code not in animmod.ACTIONS:
                a.label = "Base mesh (no action motion of its own)"
                a.group_label = "Standing"
                a.named = True
        return _mark_aliases(sorted(by_code.values(), key=_action_sort))

    def _shape_for(self, dirpath, rows) -> tuple[str, list[str]]:
        """The `3dmotion.ini` shape that owns this directory.

        **Several shapes share one directory.** `c3/monster/105/` is reached by
        shape `105` and also by `249`, and `c3/monster/104n/` only by `104` --
        the directory name and the shape are not the same string. So: prefer
        the shape that matches the directory name (with any trailing letter
        dropped, which is what `104n` needs), and otherwise take the shape with
        the most keys pointing here. Every shape found is reported in `shapes`.
        """
        counts: collections.Counter = collections.Counter(r[3] for r in rows)
        if not counts:
            return "", []
        every = sorted(counts, key=_sortkey)
        d = dirpath.rsplit("/", 1)[-1]
        for cand in (d, d.rstrip("abcdefghijklmnopqrstuvwxyz"), d.lstrip("0")):
            if cand in counts:
                return cand, every
        return max(sorted(counts), key=lambda s: counts[s]), every

    def _build_flat_npc_models(self, ini_actions) -> None:
        r"""`c3/npc/999<type><action>.c3` -- one family per middle field.

        `npc.json`'s `standby_motion` is literally `999001100`, a 9-digit
        `3dmotion.ini` key whose value is `c3/npc/999001100.c3`, so the middle
        three digits are the NPC *look* and the last three are the action --
        the same `<shape><weaponset><action>` decomposition every other key
        uses, with `999` as the shape.
        """
        groups: dict[str, dict[str, str]] = collections.defaultdict(dict)
        for p in sorted(self.paths):
            if not (p.startswith("c3/npc/") and p.endswith(".c3")):
                continue
            rest = p[len("c3/npc/"):]
            if "/" in rest:
                continue
            stem = rest[:-3]
            if not (len(stem) == 9 and stem.isdigit() and stem.startswith("999")):
                continue
            groups[stem[3:6]][stem[6:]] = p
        for grp, acts in sorted(groups.items()):
            actions = []
            base = ""
            for code, path in sorted(acts.items()):
                sc = self.has_geometry(path)
                if sc and not base:
                    base = path
            for code, path in sorted(acts.items()):
                sc = self.has_geometry(path)
                a = ModelAction(code=code, motion=path,
                                mesh=path if sc else base,
                                self_contained=sc, available=bool(sc or base),
                                source="directory",
                                key=f"999{grp}{code}",
                                reason="" if (sc or base) else
                                "motion-only and no geometry in this set.")
                _name_action(a)
                actions.append(a)
            if not actions:
                continue                                  # pragma: no cover
            actions = _mark_aliases(sorted(actions, key=_action_sort))
            rep = base or actions[0].mesh
            tex, meth, tkind = self.texture(rep)
            m = Model(kind="npc_simple", ident=grp, mesh=rep, texture=tex,
                      texture_method=meth, texture_kind=tkind,
                      directory="c3/npc", shape="999",
                      actions=sorted(actions, key=_action_sort),
                      files=len(acts))
            m.label = f"NPC look {grp}"
            m.detail = "standby / rest / blaze, one self-contained file each"
            self.models.append(m)

    def _build_role_models(self) -> None:
        """`ini/3DsimpleRole.ini` -- the eight character-select figures."""
        p = self.root / "ini" / "3DsimpleRole.ini"
        if not p.is_file():
            return                                        # pragma: no cover
        try:
            sr = parse_ini(p)
        except Exception:                                 # pragma: no cover
            return
        simple = self._simple_obj()
        for sec, row in sr.items():
            standby = (row.get("3DStandByMotion") or "").strip()
            blaze = (row.get("3DBlazeMotion") or "").strip()
            if not standby:
                continue                                  # Role100..115 are empty
            actions = []
            base = ""
            for label, ident in (("standby", standby), ("blaze", blaze)):
                if not ident:
                    continue
                path = self._motion_value(ident) or f"c3/mesh/{ident}.c3"
                path = path.replace("\\", "/")
                if not self.exists(path):
                    continue
                sc = self.has_geometry(path)
                if sc and not base:
                    base = path
                a = ModelAction(code=label,
                                label=label.title(), group="idle",
                                group_label="Standing", confidence="verified",
                                evidence=f"ini/3DsimpleRole.ini [{sec}] "
                                         f"3D{'Blaze' if label == 'blaze' else 'StandBy'}"
                                         f"Motion={ident}",
                                motion=path, mesh=path if sc else base,
                                self_contained=sc, available=True,
                                source="3DsimpleRole.ini", key=ident)
                actions.append(a)
            if not actions:
                continue
            rep = base or actions[0].mesh
            tex, meth, tkind = self.texture(rep)
            objid = (row.get("3DSimpleObjID") or "").strip()
            m = Model(kind="role", ident=sec, mesh=rep, texture=tex,
                      texture_method=meth, texture_kind=tkind,
                      directory=rep.rsplit("/", 1)[0], shape="9998",
                      actions=actions, files=len(actions))
            m.label = _spaced(sec)
            m.detail = (f"3DSimpleObj {objid}" if objid else "")
            m.names = [n for n in [simple.get(objid, {}).get("_name", "")] if n]
            self.models.append(m)

    def _simple_obj(self) -> dict[str, dict]:
        p = self.root / "ini" / "3DSimpleObj.ini"
        if not p.is_file():
            return {}                                     # pragma: no cover
        try:
            raw = parse_ini(p)
        except Exception:                                 # pragma: no cover
            return {}
        out = {}
        for sec, row in raw.items():
            if sec.lower().startswith("objidtype"):
                out[sec[len("ObjIDType"):]] = dict(row)
        return out

    def _motion_value(self, key: str) -> Optional[str]:
        if self.index is None:
            return None
        v = self.index.raw.get(str(key))
        return v.replace("\\", "/") if v else None

    def _attach_npc_names(self) -> None:
        r"""`ini/npc.json` -> the family its `standby_motion` lands in.

        This is the real NPC art link and it is worth stating why it is
        preferred: 435 of 437 rows resolve to a motion file that ships,
        against 100 of 437 for `simple_object` -> `3DSimpleObj.ini` -> a mesh
        on disk.  Both are recorded; the motion path is what picks the family.
        """
        p = self.root / "ini" / "npc.json"
        if not p.is_file():
            return                                        # pragma: no cover
        try:
            rows = json.loads(p.read_text("utf-8", errors="replace"))
        except Exception:                                 # pragma: no cover
            return
        by_dir = {m.directory: m for m in self.models if m.kind == "npc"}
        by_grp = {m.ident: m for m in self.models if m.kind == "npc_simple"}
        for r in rows:
            name = _spaced(str(r.get("name", "")))
            if not name or name.upper() == "UNKNOWN":
                continue
            path = self._motion_value(str(r.get("standby_motion", "")))
            if not path:
                continue
            path = path.lower()
            m = None
            rest = path[len("c3/npc/"):] if path.startswith("c3/npc/") else ""
            if rest and "/" in rest:
                m = by_dir.get(path.rsplit("/", 1)[0])
            elif rest:
                stem = rest[:-3]
                if len(stem) == 9 and stem.startswith("999"):
                    m = by_grp.get(stem[3:6])
            if m is None:
                continue
            if name not in m.names:
                m.names.append(name)
        for m in self.models:
            if m.kind in ("npc", "npc_simple") and m.names:
                m.label = m.names[0] if len(m.names) == 1 else \
                    f"{m.names[0]} +{len(m.names) - 1}"
                m.detail = (m.detail + " · " if m.detail else "") + \
                    f"{len(m.names)} npc.json row" + \
                    ("s" if len(m.names) != 1 else "")

    def _build_effect_models(self) -> None:
        """The effect library, listed but resolved by `tools/effects.py`.

        Nothing is re-derived here: an effect is a `3DEffect.ini` section key
        and the viewer already plays one through `effectplay` + `fx.js`
        (docs/viewer.md §4.5).  This only makes them *browsable* alongside the
        other model families.
        """
        if self._effect_names is None:
            return
        try:
            names = list(self._effect_names())
        except Exception:                                 # pragma: no cover
            return
        for n in names:
            m = Model(kind="effect", ident=n, label=n,
                      detail="3DEffect.ini section",
                      directory="c3/effect", files=0)
            self.models.append(m)

    # -- querying ----------------------------------------------------------
    def kinds(self) -> list[dict]:
        counts = collections.Counter(m.kind for m in self.models)
        out = []
        for k in KIND_ORDER:
            if k not in KINDS:
                continue                                  # pragma: no cover
            plural, singular, desc = KINDS[k]
            out.append({"kind": k, "label": plural, "singular": singular,
                        "description": desc, "count": counts.get(k, 0)})
        return out

    def query(self, *, kind: str = "", text: str = "",
              playable_only: bool = True,
              tag_map: Optional[dict] = None,
              want_tags: Optional[set] = None,
              untagged: bool = False) -> dict:
        """Filter, with cross-filtered kind counts.

        Same rule as everywhere else in this viewer: each count is "how many
        would I get if I picked this", so no chip leads to an empty list.
        """
        tag_map = tag_map or {}
        want = {t.lower() for t in (want_tags or set())}
        q = (text or "").strip().lower()

        def text_ok(m: Model) -> bool:
            if not q:
                return True
            hay = " ".join([m.ident, m.label, m.detail, m.mesh, m.kind,
                            " ".join(m.names),
                            " ".join(tag_map.get(f"model:{m.key}", []))]).lower()
            return q in hay

        def tag_ok(m: Model) -> bool:
            have = tag_map.get(f"model:{m.key}", [])
            if untagged and have:
                return False
            return not want or want.issubset({t.lower() for t in have})

        def play_ok(m: Model) -> bool:
            return (not playable_only) or m.kind == "effect" or m.playable > 0

        base = [m for m in self.models if text_ok(m) and tag_ok(m) and play_ok(m)]
        counts = collections.Counter(m.kind for m in base)
        matched = [m for m in base if not kind or m.kind == kind]
        return {"kind": kind, "total": len(matched), "pool": len(self.models),
                "kindCounts": dict(counts), "matched": matched}

    def get(self, key: str) -> Optional[Model]:
        return self.by_key.get(key)

    # -- monster.json ------------------------------------------------------
    def monster_rows(self) -> list[dict]:
        return [r.to_json() for r in self.monsters]

    def monster_row(self, type_id) -> Optional[dict]:
        try:
            t = int(type_id)
        except (TypeError, ValueError):
            return None
        for r in self.monsters:
            if r.type == t:
                return r.to_json()
        return None

    # -- audit -------------------------------------------------------------
    def audit(self) -> dict:
        out: dict = {"kinds": {}, "monsterRows": len(self.monsters)}
        for k in KIND_ORDER:
            ms = [m for m in self.models if m.kind == k]
            if not ms:
                out["kinds"][k] = {"models": 0}
                continue
            acts = sum(len(m.actions) for m in ms)
            play = sum(m.playable for m in ms)
            sc = sum(1 for m in ms for a in m.actions if a.self_contained)
            out["kinds"][k] = {
                "models": len(ms), "actions": acts, "playable": play,
                "selfContainedActions": sc,
                "withTexture": sum(1 for m in ms if m.texture),
                "layouts": dict(collections.Counter(
                    _layout_key(m) for m in ms)),
            }
        zooms = [r.zoom_percent for r in self.monsters]
        if zooms:
            out["zoomPercent"] = {"min": min(zooms), "max": max(zooms),
                                  "distinct": len(set(zooms))}
        return out


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _has_phy(data: bytes) -> bool:
    """Cheap `PHY`-family scan over a `.c3` container."""
    try:
        import c3phy
        for tag, _body in c3phy.iter_chunks(data):
            if tag in c3phy.VARIANTS:
                return True
        return False
    except Exception:                                     # pragma: no cover
        return b"PHY" in data[:4096]


def _pick_base(files: dict[str, str], geom: dict[str, bool]) -> str:
    """The family's shared skeleton, when it has one.

    `1.c3` and `<shape>000000.c3` are the two names the shipped data uses for
    it.  A family with neither is a per-action-mesh family and its motion-only
    action files (`c3/monster/103/340.c3`) bind over whichever geometry file is
    loaded -- ordinal binding does not care which, and every chunk count in
    those families is 1.
    """
    for stem in sorted(files):
        if geom.get(stem) and (stem == "1" or
                               (len(stem) > 3 and stem.endswith("000000"))):
            return files[stem]
    for stem in ("100", "101", "1"):
        if geom.get(stem):
            return files[stem]
    for stem in sorted(files):
        if geom.get(stem):
            return files[stem]
    return ""


def _mark_aliases(actions: list[ModelAction]) -> list[ModelAction]:
    """Flag every action after the first that resolves to the same file.

    `ini/3dmotion.ini` declares 80 action codes for monster shape `103` and
    they land on 16 files: `404`/`405`/`407`/`903` are all `401.c3`. Both facts
    matter -- the codes are what the server sends, the files are what there is
    to look at -- so nothing is dropped and the duplicates are labelled.
    """
    live = [a for a in actions if a.available and a.motion]
    seen: dict[str, str] = {}
    # First claim: the code whose own file is named after it. `401.c3` belongs
    # to action 401 even though 140 sorts first and also resolves to it, and
    # calling 401 "the same clip as 140" would be exactly backwards.
    for a in live:
        if a.motion.rsplit("/", 1)[-1][:-3].lstrip("0") == a.code.lstrip("0"):
            seen.setdefault(a.motion, a.code)
    for a in live:
        first = seen.get(a.motion)
        if first is None:
            seen[a.motion] = a.code
        elif first != a.code:
            a.distinct = False
            a.alias_of = first
    return actions


def _layout_key(m: Model) -> str:
    sc = sum(1 for a in m.actions if a.self_contained)
    if sc == 0:
        return "skeleton + motion sets"
    if sc == len(m.actions):
        return "per-action meshes"
    return "mixed"


def _layout_of(actions: list[ModelAction], base: str) -> str:
    sc = sum(1 for a in actions if a.self_contained)
    if sc == 0:
        return "one skeleton, a motion set per action"
    if sc == len(actions):
        return "one mesh per action"
    return f"{sc} of {len(actions)} actions ship their own mesh"


def _default_label(kind: str, ident: str) -> str:
    if kind == "monster":
        return f"Monster {ident}"
    if kind == "npc":
        return f"NPC {ident}"
    if kind == "ghost":
        return f"Ghost {ident}"
    if kind == "mount":
        return f"Mount {ident}"
    return f"{KINDS.get(kind, ('', ident, ''))[1].title()} {ident}"


def _name_action(a: ModelAction) -> None:
    """Borrow `anim.ACTIONS`'s name, group and evidence for an action code.

    The action vocabulary is shared: `100` is idle and `401` is a swing for a
    monster exactly as for a player (`docs/animation.md` §3), so there is no
    second table here.  Codes `anim.py` could not identify keep their number
    and are grouped last rather than hidden.
    """
    meta = animmod.ACTIONS.get(a.code) if animmod is not None else None
    if meta is not None:
        a.label = (meta.name[0].upper() + meta.name[1:]) if meta.name else a.code
        a.group = meta.group
        a.confidence = meta.confidence
        a.evidence = meta.evidence
        a.chain = meta.chain
        a.named = meta.confidence != "unknown"
        a.group_label = _GROUP_LABEL.get(meta.group, meta.group.title()) \
            if a.named else "Unidentified codes"
    else:
        a.label = a.label or f"action {a.code}"
        a.group = a.group or "action"
        a.named = False
        a.group_label = a.group_label or "Unidentified codes"


_GROUP_LABEL = {
    "idle": "Standing", "move": "Moving", "attack": "Attacking",
    "cast": "Casting", "react": "Being hit", "emote": "Emotes",
    "pose": "Poses", "action": "Other", "death": "Dying",
}

_GROUP_ORDER = ["idle", "move", "attack", "cast", "react", "emote", "pose",
                "action", "death"]


def _action_sort(a: ModelAction):
    return (not a.named, _GROUP_ORDER.index(a.group)
            if a.group in _GROUP_ORDER else 99, a.code)


def _sortkey(s: str):
    m = re.match(r"^(\d+)(.*)$", s)
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, s)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv) -> int:
    import argparse
    from coassets import AssetRoot

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--kinds", action="store_true")
    ap.add_argument("--list", metavar="KIND", nargs="?", const="")
    ap.add_argument("--model", metavar="KIND:ID")
    ap.add_argument("--monsters", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args(argv)

    root = Path(a.root)
    R = AssetRoot(root)
    paths = _scan_paths(root, R)
    cov = _coverage(root)
    cat = ModelCatalogue(
        root, paths=paths, exists=R.exists, read=R.read,
        has_geometry=(None if cov is None
                      else (lambda p: p.lower() in cov)),
        texture_for=(None if cov is None
                     else (lambda m: (cov.get(m.lower()) or [None])[0])))

    if a.audit:
        print(json.dumps(cat.audit(), indent=2))
        return 0
    if a.monsters:
        print(f"{'type':>5} {'name':<26}{'zoom':>6}{'lvl':>5}{'life':>8}"
              f"  born")
        for r in cat.monster_rows()[:a.limit]:
            print(f"{r['type']:>5} {r['name'][:25]:<26}{r['zoomPercent']:>5}%"
                  f"{r['level']:>5}{r['maxLife']:>8}"
                  f"  {r['bornAction']} / {r['bornEffect']}")
        print(f"\n  {len(cat.monsters)} rows. {MONSTER_LINK_NOTE}")
        return 0
    if a.model:
        m = cat.get(a.model)
        if m is None:
            print(f"no model {a.model!r}. Try --list monster")
            return 1
        print(f"{m.key}  {m.label}")
        print(f"  mesh      {m.mesh}")
        print(f"  texture   {m.texture}  [{m.texture_kind} {m.texture_method}]")
        print(f"  dir       {m.directory}   shape {m.shape or '—'}")
        print(f"  layout    {m.detail}")
        if m.names:
            print(f"  named     {', '.join(m.names[:8])}")
        print(f"  {m.playable} of {len(m.actions)} actions playable")
        for act in m.actions[:a.limit]:
            flag = "mesh" if act.self_contained else "motion"
            mark = " " if act.available else "!"
            print(f"   {mark}{act.code:<5}{act.label[:22]:<24}{flag:<7}"
                  f"{act.motion}")
            if not act.available:
                print(f"        {act.reason}")
        return 0
    if a.kinds or a.list is None:
        print(f"{'kind':<12}{'models':>8}  description")
        for k in cat.kinds():
            print(f"  {k['kind']:<10}{k['count']:>8}  {k['description']}")
        print(f"\n  {sum(k['count'] for k in cat.kinds())} models in total")
        return 0
    res = cat.query(kind=a.list)
    print(f"{res['total']} models"
          + (f" of kind {a.list}" if a.list else "") + f" (of {res['pool']})")
    for m in res["matched"][:a.limit]:
        print(f"  {m.key:<20}{m.label[:28]:<30}{m.playable:>3}/"
              f"{len(m.actions):<4} actions  {m.mesh}")
    return 0


def _scan_paths(root: Path, assets) -> list[str]:
    """Loose files plus whatever archive names have been recovered."""
    import os
    out: set[str] = set()
    rootlen = len(str(root)) + 1
    for dirpath, _d, files in os.walk(root):
        rel = dirpath[rootlen:].replace("\\", "/")
        prefix = (rel + "/") if rel else ""
        for f in files:
            out.add((prefix + f).lower())
    repo = Path(__file__).resolve().parent.parent
    for rel in ("out/wdf/c3_names.json", "out/wdf/data_names.json"):
        p = repo / rel
        if p.is_file():
            try:
                out.update(v.lower()
                           for v in json.loads(p.read_text("utf-8")).values())
            except Exception:                             # pragma: no cover
                pass
    return sorted(out)


def _coverage(root: Path):
    """`tools/meshtex.py`'s coverage file: `mesh -> [match, ...]`.

    Membership answers "does this `.c3` have geometry" (the file lists every
    real mesh and excludes the 1,426 `.c3` containers that carry none), and the
    first match is the texture. The viewer passes the same two things in from
    `unify.UnifiedIndex`; this is only the CLI's route to them. Returns None
    when the file has not been generated, in which case the caller opens the
    `.c3` itself.
    """
    p = Path(__file__).resolve().parent.parent / "out" / "meshtex" / "coverage.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
    except Exception:                                     # pragma: no cover
        return None
    out = {k.lower(): (v.get("matches") or [])
           for k, v in (data.get("meshes") or {}).items()}
    return out or None


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
