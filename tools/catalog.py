#!/usr/bin/env python3
r"""
catalog.py -- the browsing taxonomy: what kind of thing is each asset, and what
goes with it.

24,757 archived entries plus ~53,669 loose files is too many to browse as one
list. This module splits them into categories and, for a chosen character or
weapon, gathers the assets that belong to it.

**The taxonomy is derived, not declared.** Three sources of evidence, strongest
first:

  1. **Appearance-table membership.** If `armor.ini` names a mesh or texture,
     that asset *is* character body art -- the game says so. Same for
     `weapon.ini`, `armet.ini`, `mount.ini`. The NPC chain (npc table ->
     simple object -> obj/texture tables, `core/npcart.py`) is the same kind
     of evidence and files an NPC's geometry and skin under NPCs even though
     they live in the shared `c3/mesh/` and `c3/texture/` buckets. It only
     overrides those generic buckets: a monster-styled NPC that borrows
     `c3/monster/108/` does not pull the file out of Monsters -- the art is
     the monster's, the NPC merely wears it. This is the only evidence that
     comes from the client's own data rather than from us.
  2. **Directory layout.** `c3/monster/...`, `data/map/puzzle/<region>/...`,
     `c3/0001..0004/...`. Verified against the real tree; every rule below
     records what it matched and why, and the rule that fired is reported in
     the UI so a wrong call is visible rather than silent.
  3. **Extension / role.** `.c3` is geometry, `.dds`/`.png` art, `.pul`/`.scene`
     map data, and so on.

Anything none of that classifies lands in **`other`**, which is a real,
selectable bucket -- nothing is ever hidden for failing to fit.

`c3/0001` .. `c3/0004` are not four mystery folders: they line up exactly with
the four player body types (see docs/appearance_ids.md), so they fold into
Characters as per-body-type motion sets.

Slots left for the sibling workstreams: `related_for_*` returns a list of
groups, and `effects.py` / `meshtex.py` are imported if present and skipped if
not. Nothing here breaks when they are absent.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT                       # noqa: E402

try:
    import bodyfacets                                   # noqa: E402
except Exception:                                       # pragma: no cover
    bodyfacets = None

# Sibling workstreams (tasks #12 / #13). Optional by design.
try:
    import meshtex                                      # noqa: E402  (task #12)
except Exception:
    meshtex = None
try:
    import effects                                      # noqa: E402  (task #13)
except Exception:
    effects = None


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------

CATEGORIES: list[dict] = [
    {"id": "character", "label": "Characters",
     "blurb": "Player bodies, armour, helmets, hair and the per-body-type motion sets."},
    {"id": "weapon", "label": "Weapons",
     "blurb": "Weapon and shield meshes, their skins and their motion meshes."},
    {"id": "monster", "label": "Monsters",
     "blurb": "Monster models and skins, one folder per monster type."},
    {"id": "npc", "label": "NPCs",
     "blurb": "Non-player character models and skins."},
    {"id": "mount", "label": "Mounts",
     "blurb": "Rideable mount models and skins."},
    {"id": "effect", "label": "Effects",
     "blurb": "Particle and animated effect meshes, weather and fireworks."},
    {"id": "map", "label": "Maps",
     "blurb": "World maps and every piece of art that draws them."},
    {"id": "ui", "label": "UI & icons",
     "blurb": "Interface art, item icons, faces, emotes and cursors."},
    {"id": "sound", "label": "Sound", "blurb": "Audio."},
    {"id": "system", "label": "Not art",
     "blurb": "Config, logs, binaries and the archives themselves — real files, "
              "but nothing to look at."},
    {"id": "other", "label": "Unclassified",
     "blurb": "Art that matched no rule. Nothing is hidden — if a rule is "
              "wrong or missing, what it missed shows up here."},
]
CATEGORY_IDS = [c["id"] for c in CATEGORIES]
CATEGORY_LABEL = {c["id"]: c["label"] for c in CATEGORIES}

#: Sub-buckets, in the order the UI should offer them.
SUBCATEGORY_ORDER: dict[str, list[str]] = {
    "character": ["body", "armet", "hair", "face", "motion", "texture", "misc"],
    "weapon": ["mesh", "texture", "motion", "icon"],
    "monster": ["mesh", "texture"],
    "npc": ["mesh", "texture", "face"],
    "mount": ["mesh", "texture"],
    "effect": ["mesh", "texture", "weather", "firework"],
    "map": ["world", "puzzle", "scene", "scenepart", "tiles", "objects",
            "cartoon", "minimap", "misc"],
    "ui": ["itemicon", "mapicon", "interface", "face", "emotion", "cursor",
           "cosmetics", "misc"],
    "sound": ["sound"],
    "system": ["config", "log", "binary", "archive", "misc"],
    "other": ["other"],
}

#: Role is orthogonal to category: what *kind* of file it is. It is what lets
#: the map view separate "the big map" from "the sprites that go on it".
ROLE_BY_EXT = {
    ".c3": "mesh", ".dds": "texture", ".png": "texture", ".jpg": "texture",
    ".jpeg": "texture", ".bmp": "texture", ".tga": "texture", ".ico": "texture",
    ".cur": "cursor", ".dmap": "map", ".pul": "mapdata", ".scene": "mapdata",
    ".part": "mapdata", ".ani": "mapdata", ".wav": "sound", ".mp3": "sound",
    ".ogg": "sound", ".ini": "data", ".json": "data", ".txt": "data",
}


@dataclass
class Classification:
    category: str = "other"
    subcategory: str = "other"
    role: str = "data"
    why: str = ""
    group: str = ""          # a natural grouping key within the subcategory

    def to_json(self) -> dict:
        return {"category": self.category, "subcategory": self.subcategory,
                "role": self.role, "why": self.why, "group": self.group,
                "categoryLabel": CATEGORY_LABEL.get(self.category, self.category)}


# ---------------------------------------------------------------------------
# path rules
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    pattern: str                       # a path prefix, or a regex if it has ^
    category: str
    subcategory: str
    why: str
    group_from: int = -1               # which path segment is the natural group

    def match(self, p: str) -> bool:
        if self.pattern.startswith("^"):
            return re.match(self.pattern, p) is not None
        return p.startswith(self.pattern)


#: Order matters: the first matching rule wins, so put the specific ones first.
#: Every counted path in this install is covered by one of these or falls to
#: `other`; `catalog.py --audit` prints what is left over.
RULES: list[Rule] = [
    # ---- maps: the data files, then the art, most specific first ----
    Rule("map/map/", "map", "world", "map/map/*.DMap is the world grid"),
    Rule("map/puzzle/", "map", "puzzle", "map/puzzle/*.pul is the tile layout"),
    Rule("map/puzzlesave/", "map", "puzzle", "puzzle layouts (save variants)"),
    Rule("map/scenepart/", "map", "scenepart", "map/ScenePart/*.Part, plain text"),
    Rule("map/scene/", "map", "scene", "map/Scene/*.scene scenery objects"),
    Rule("map/", "map", "misc", "under map/"),
    Rule("data/map/puzzle/", "map", "tiles",
         "data/map/puzzle/<region>/... are the ground tiles an .ani names", 3),
    Rule("data/map/mapobj/", "map", "objects",
         "data/map/mapobj/<region>/... are props placed on the map", 3),
    Rule("data/map/scene/", "map", "scene", "scenery sprite frames", 3),
    Rule("data/map/cartoon/", "map", "cartoon", "cartoon-style map art", 3),
    Rule("data/map/", "map", "misc", "under data/map/", 2),
    Rule("data/minimap/", "map", "minimap", "minimap art"),

    # ---- characters ----
    Rule("^c3/000[1-4]/", "character", "motion",
         "c3/000N/ is the motion/equipment set for player body type N", 1),
    Rule("c3/body/", "character", "body", "c3/body/ character body art"),
    Rule("c3/hair/", "character", "hair", "c3/hair/ hair meshes and skins"),
    Rule("data/playerface/", "character", "face", "player portrait art"),

    # ---- other actors ----
    Rule("c3/monster/", "monster", "mesh", "c3/monster/<type>/ monster art", 2),
    Rule("c3/npc/", "npc", "mesh", "c3/npc/<id>/ NPC art", 2),
    Rule("data/npcface/", "npc", "face", "NPC portrait art"),
    Rule("c3/mount/", "mount", "mesh", "c3/mount/ mount art"),
    Rule("c3/ghost/", "monster", "mesh", "c3/ghost/ ghost models"),

    # ---- weapons ----
    Rule("c3/weapon/", "weapon", "mesh", "c3/weapon/ weapon art"),

    # ---- effects ----
    Rule("c3/effect/", "effect", "mesh", "c3/effect/<name>/ effect art", 2),
    Rule("data/weather/", "effect", "weather", "weather sprite frames"),
    Rule("data/firework/", "effect", "firework", "firework sprite frames"),
    Rule("ini/3deffect", "effect", "mesh", "effect definition table"),

    # ---- UI ----
    Rule("data/itemminicon/", "ui", "itemicon", "inventory item icons"),
    Rule("data/mapitemicon/", "ui", "mapicon", "ground/drop item icons"),
    Rule("data/emotionico/", "ui", "emotion", "chat emote icons"),
    Rule("data/interface/", "ui", "interface", "window and widget art"),
    Rule("data/main/", "ui", "interface", "main UI art"),
    Rule("data/pic/", "ui", "misc", "assorted UI pictures"),
    Rule("data/cursor/", "ui", "cursor", "mouse cursors"),
    Rule("graphics/cosmetics/", "ui", "cosmetics", "cosmetic shop art"),
    Rule("graphics/garments/", "ui", "cosmetics", "garment shop art"),
    Rule("graphics/", "ui", "misc", "under graphics/"),
    Rule("avatar/", "ui", "face", "avatar art"),

    # ---- generic c3 buckets, last so the specific ones win ----
    Rule("c3/mesh/", "character", "body",
         "c3/mesh/ is shared geometry; the appearance tables say which part "
         "each id belongs to, and that evidence overrides this rule"),
    Rule("c3/texture/", "character", "texture",
         "c3/texture/ is shared skin art; appearance-table evidence overrides"),
    Rule("c3/", "character", "misc", "under c3/"),

    # ---- the rest ----
    Rule("sound/", "sound", "sound", "under sound/"),
    Rule("ani/", "map", "misc", "ani/ definitions name map and sprite frames"),

    # ---- not art: real files, but nothing to render. Kept out of
    #      "Unclassified" so that bucket keeps meaning "art we failed to place".
    Rule("ini/", "system", "config", "the game database"),
    Rule("log/", "system", "log", "log output"),
    Rule("debug/", "system", "log", "debug logs"),
    Rule("bin/", "system", "binary", "executables and DLLs"),
    Rule(".sentry-native/", "system", "log", "crash reporter state"),
    Rule("launcherresources/", "system", "misc", "launcher assets"),
    Rule("font/", "system", "misc", "font files"),
    Rule("^[^/]+\\.wdf$", "system", "archive", "the WDF archives themselves"),
    Rule("^[^/]+\\.(json|ini|txt|log|exe|dll|dat|cfg|pdb|bak)$", "system", "config",
         "a top-level config or binary file"),
]

#: The shared buckets whose rules already promise that table evidence
#: overrides them. NPC-table membership reclassifies a path only when its
#: rule is one of these (or none matched) -- specific family folders like
#: `c3/monster/` keep their art even when an NPC references it.
GENERIC_SHARED = {"c3/mesh/", "c3/texture/", "c3/"}

#: Appearance tables -> the category their meshes and textures belong to.
#: This is the strongest evidence there is: it comes from the client's own ini.
TABLE_CATEGORY: dict[str, tuple[str, str]] = {
    "body": ("character", "body"),
    "mix_body": ("character", "body"),
    "armet": ("character", "armet"),
    "armet_dx8": ("character", "armet"),
    "mix_armet": ("character", "armet"),
    "mix_armet_dx8": ("character", "armet"),
    "head": ("character", "body"),
    "pelvis": ("character", "body"),
    "misc": ("character", "misc"),
    "l_weapon": ("weapon", "mesh"),
    "r_weapon": ("weapon", "mesh"),
    "shield": ("weapon", "mesh"),
    "mount": ("mount", "mesh"),
}


def role_of(path: str) -> str:
    return ROLE_BY_EXT.get(Path(path).suffix.lower(), "data")


class AssetCatalog:
    """Classifies logical asset paths and gathers "what goes with this".

    `table_membership` maps a logical path to the appearance tables that
    reference it -- pass the viewer's reverse index so table evidence is used.
    """

    def __init__(self, root: Path = DEFAULT_ROOT,
                 table_membership: Optional[Callable[[str], list]] = None,
                 exists: Optional[Callable[[str], bool]] = None,
                 list_under: Optional[Callable[[str], list]] = None,
                 npc_membership: Optional[Callable[[str], bool]] = None):
        self.root = Path(root)
        self._tables = table_membership or (lambda p: [])
        self._exists = exists or (lambda p: (self.root / p).is_file())
        self._list_under = list_under or (lambda prefix: [])
        self._npc = npc_membership or (lambda p: False)
        self._cache: dict[str, Classification] = {}
        self._weapon_motion: Optional[dict[str, str]] = None
        self._linkage: Optional[dict] = None

    # -- classification ----------------------------------------------------
    def classify(self, path: str) -> Classification:
        p = path.replace("\\", "/").lstrip("/").lower()
        hit = self._cache.get(p)
        if hit is not None:
            return hit
        c = self._classify(p)
        self._cache[p] = c
        return c

    def _classify(self, p: str) -> Classification:
        role = role_of(p)

        # (1) strongest evidence: the client's own appearance tables
        refs = self._tables(p)
        if refs:
            tables = {r.get("table") for r in refs}
            for t in ("l_weapon", "r_weapon", "shield", "mount", "body",
                      "mix_body", "armet", "armet_dx8", "mix_armet",
                      "mix_armet_dx8", "head", "pelvis", "misc"):
                if t in tables:
                    cat, sub = TABLE_CATEGORY[t]
                    if role == "texture" and cat == "character":
                        sub = "texture"
                    if role == "texture" and cat == "weapon":
                        sub = "texture"
                    return Classification(
                        cat, sub, role,
                        f"referenced by {t} in the appearance tables",
                        group=self._group_for(p, cat))

        rule = next((r for r in RULES if r.match(p)), None)

        # (2) the NPC chain is table evidence too, but it only overrides the
        #     generic shared buckets -- see GENERIC_SHARED.
        if ((rule is None or rule.pattern in GENERIC_SHARED)
                and self._npc(p)):
            return Classification(
                "npc", "texture" if role == "texture" else "mesh", role,
                "referenced by the NPC tables "
                "(npc -> simple object -> obj/texture)")

        # (3) directory layout
        if rule is not None:
            sub = rule.subcategory
            # A folder that holds both geometry and skins (c3/monster,
            # c3/effect, ...) splits by role. Rules that already name a
            # specific bucket -- weather, firework, face -- keep it.
            if sub == "mesh" and role == "texture":
                sub = "texture"
            seg = p.split("/")
            grp = seg[rule.group_from] if 0 <= rule.group_from < len(seg) else ""
            return Classification(rule.category, sub, role, rule.why, grp)

        # (4) nothing matched -- say so out loud
        return Classification("other", "other", role, "no rule matched")

    @staticmethod
    def _group_for(p: str, category: str) -> str:
        seg = p.split("/")
        return seg[1] if len(seg) > 2 else ""

    # -- counts ------------------------------------------------------------
    def summarise(self, paths) -> dict:
        """{category: {"count": n, "subs": {sub: n}, "roles": {role: n}}}"""
        out: dict[str, dict] = {}
        for p in paths:
            c = self.classify(p)
            e = out.setdefault(c.category, {"count": 0, "subs": {}, "roles": {}})
            e["count"] += 1
            e["subs"][c.subcategory] = e["subs"].get(c.subcategory, 0) + 1
            e["roles"][c.role] = e["roles"].get(c.role, 0) + 1
        return out

    # -- "and what goes with them" ----------------------------------------
    def weapon_motion(self) -> dict[str, str]:
        r"""ini/WeaponMotion.ini -- a flat `key=value` file (no sections) mapping
        a weapon appearance id to the `c3/mesh/*.c3` that carries its swing
        animation, e.g. `510000300=c3/mesh/510000401.c3`. VERIFIED by reading
        the file; 1,700-odd entries."""
        if self._weapon_motion is not None:
            return self._weapon_motion
        out: dict[str, str] = {}
        p = self.root / "ini" / "WeaponMotion.ini"
        if p.is_file():
            for line in p.read_text("latin-1", errors="replace").splitlines():
                line = line.strip()
                if not line or "=" not in line or line.startswith((";", "#", "[")):
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().replace("\\", "/").lower()
        self._weapon_motion = out
        return out

    def icon_candidates(self, ident: str) -> list[str]:
        """Item icons are keyed by item id. INFERRED: the exact id resolves for
        1,223 of 11,142 items, so we probe the obvious id forms and return only
        those that exist rather than claiming one."""
        forms = []
        for v in (ident, ident.lstrip("0"), ident.zfill(6), ident.zfill(7)):
            if v and v not in forms:
                forms.append(v)
        out = []
        for d in ("data/itemminicon", "data/mapitemicon"):
            for f in forms:
                cand = f"{d}/{f}.dds"
                if cand not in out and self._exists(cand):
                    out.append(cand)
        return out

    def related_groups(self, *, appearance: Optional[dict] = None,
                       path: str = "", siblings=None) -> list[dict]:
        """Assets that belong with the selection, as labelled groups.

        `appearance` is a dict with at least `id`, `table`, `mesh`, `texture`.
        `siblings` is an optional callable(mesh_logical) -> [appearance ids]
        used for "other colourways of this mesh".

        Each group is `{"title", "note", "items":[{path, role, label}], "pending"}`.
        `pending` marks a group whose data source is not available yet -- the
        effects linkage is task #13's -- so the UI can show the slot rather
        than pretend it does not exist.
        """
        groups: list[dict] = []

        def add(title, items, note="", pending=False):
            items = [i for i in items if i.get("path")]
            if items or pending:
                groups.append({"title": title, "note": note, "items": items,
                               "pending": pending})

        if appearance:
            ident = str(appearance.get("id", ""))
            table = appearance.get("table", "")
            mesh = appearance.get("mesh")
            tex = appearance.get("texture")
            add("This asset", [
                {"path": mesh, "role": "mesh", "label": "mesh"},
                {"path": tex, "role": "texture", "label": "skin"},
            ])
            extra = []
            for k, lbl in (("mixTex", "mix texture"), ("thirdTex", "third texture"),
                           ("fourthTex", "fourth texture")):
                v = appearance.get(k)
                if v and v not in ("0", 0):
                    for cand in (f"c3/texture/{str(v).zfill(9)}.dds",
                                 f"c3/texture/{v}.dds"):
                        if self._exists(cand):
                            extra.append({"path": cand, "role": "texture", "label": lbl})
                            break
            add("Extra skins", extra,
                "MixTex / ThirdTex / FourthTex from the appearance table")

            if siblings and mesh:
                seen_tex = {tex}
                sib = []
                for s in siblings(mesh):
                    t = s.get("texture")
                    # several appearances can share one skin, so dedupe on the
                    # texture rather than the appearance id
                    if s.get("id") == ident or not t or t in seen_tex:
                        continue
                    seen_tex.add(t)
                    sib.append({"path": t, "role": "texture",
                                "label": s.get("id"), "appearance": s.get("id")})
                add("Other colourways on this mesh", sib[:64],
                    "the mesh is shared across colourways; only the skin differs")

            icons = self.icon_candidates(ident)
            add("Icons", [{"path": i, "role": "texture",
                           "label": i.split("/")[1]} for i in icons],
                "item icon, matched by id (inferred)")

            if table in ("l_weapon", "r_weapon", "shield"):
                # One row per action that swaps the mesh, deduped by path: the
                # 11 non-default actions usually all point at the same swing
                # mesh, so listing 11 identical cells would be noise.
                seen_mo: dict[str, list] = {}
                for act in ("999", "401", "402", "403", "300", "305", "310",
                            "320", "330", "331", "340", "341"):
                    hit = self.weapon_motion_for(ident, act)
                    if hit:
                        seen_mo.setdefault(hit["mesh"], []).append(hit["action"])
                add("Motion mesh",
                    [{"path": p, "role": "mesh",
                      "label": ("default" if "999" in acts
                                else "actions " + ",".join(sorted(set(acts))))}
                     for p, acts in seen_mo.items()],
                    "ini/WeaponMotion.ini — the weapon swaps mesh per action, "
                    "it does not deform")
                items, ready = self._effect_items(ident, table)
                add("Effects", items,
                    "weapon effect linkage (tools/effects.py)",
                    pending=not ready or not items)

        if path:
            cls = self.classify(path)
            add("Same folder", [], "")
            groups = [g for g in groups if g["items"] or g["pending"]]
            if cls.category == "weapon" and role_of(path) == "mesh":
                stem = Path(path).stem
                hit = self.weapon_motion_for(stem, "401")
                add("Motion mesh",
                    [{"path": hit["mesh"], "role": "mesh",
                      "label": "swing animation"}] if hit else [],
                    "from ini/WeaponMotion.ini")

        return [g for g in groups if g["items"] or g["pending"]]

    def weapon_linkage(self) -> dict:
        """`out/effects/linkage.json` from task #13. Loaded once, lazily."""
        if self._linkage is None:
            self._linkage = {}
            import coroot
            p = coroot.find_derived("out/effects/linkage.json")
            if p is not None:
                try:
                    self._linkage = json.loads(p.read_text("utf-8"))
                except Exception:
                    self._linkage = {}
        return self._linkage

    def weapon_effects(self, ident: str) -> dict:
        r"""The three separate effects a weapon has, per docs/effects.md.

        * **aura** -- always on, from the `999` wildcard action.
        * **attack trail** -- one per attack action (401/402/403). Only quality
          6-9 weapons have one; that is the data, not a gap, so the UI says
          "no attack trail" rather than looking broken.
        * **impact spark** -- keyed by the 3-digit weapon type and drawn **at
          the target**, never on the character.
        """
        link = self.weapon_linkage()
        if not link:
            return {}
        wa = link.get("weapon_appearances") or {}
        rec = wa.get(ident) or wa.get(ident.lstrip("0")) or {}
        types = link.get("weapon_types") or {}
        # `weapon_appearances` only lists the 2,921 appearances that have a
        # trail or an aura. The impact spark is keyed by the 3-digit weapon
        # TYPE, so a quality-5 blade still has one -- it just has no trail.
        # Falling through to the type table is what makes "no trail, but there
        # IS an impact" come out right instead of looking like nothing exists.
        wtype = rec.get("type") or (ident[:-3] if len(ident) > 3 else "")
        wt = types.get(wtype, {})
        if not rec and not wt:
            return {}
        return {
            "aura": rec.get("aura") or "",
            "attack": rec.get("attack") or {},
            "type": wtype,
            "typeName": rec.get("type_name") or wt.get("name", ""),
            "hitEffect": rec.get("hit_effect") or wt.get("hit_effect", ""),
            "blockEffect": rec.get("blk_effect") or wt.get("blk_effect", ""),
            "hitSound": wt.get("hit_sound", ""),
        }

    def effect_assets(self, name: str) -> list[str]:
        """Logical paths for a named effect.

        An effect *name* is a `3DEffect.ini` section key, not a folder name --
        `Flash4102` lives in `c3/effect/flash/`, `m-b02` in
        `c3/effect/Monster-bomb/m-b02/`. So the real resolution is
        `3DEffect.ini` -> `EffectId`/`TextureId` -> `3DEffectObj.ini` /
        `3dtexture.ini`, which is what `linkage.json`'s `effects[name].layers`
        already holds (2,207 of 2,255 effects resolve on every layer).

        The old folder guess is kept only as a fallback for the handful of names
        whose layers do not resolve, and it is *checked* against the path set
        rather than assumed.
        """
        if not name:
            return []
        out: list[str] = []
        eff = (self.weapon_linkage().get("effects") or {}).get(name)
        if eff:
            for lay in eff.get("layers", []):
                for key in ("mesh", "texture"):
                    p = (lay.get(key) or "").replace("\\", "/").lower()
                    if p and p not in out and self._exists(p):
                        out.append(p)
        if out:
            # meshes first, so the strip's lead cell is something drawable
            return ([p for p in out if p.endswith(".c3")] +
                    [p for p in out if not p.endswith(".c3")])
        found = self._list_under(f"c3/effect/{name.lower()}/")
        if not found:
            return []
        meshes = sorted(p for p in found if p.endswith(".c3"))
        texes = sorted(p for p in found if p.endswith(".dds"))
        return meshes + texes

    def effect_layers(self, name: str) -> list[dict]:
        """`{mesh, texture, srcBlend, dstBlend}` per layer of a named effect."""
        eff = (self.weapon_linkage().get("effects") or {}).get(name)
        if not eff:
            return []
        out = []
        for lay in eff.get("layers", []):
            out.append({
                "index": lay.get("index"),
                "mesh": (lay.get("mesh") or "").replace("\\", "/").lower(),
                "meshFound": bool(lay.get("mesh_found")),
                "texture": (lay.get("texture") or "").replace("\\", "/").lower(),
                "textureFound": bool(lay.get("texture_found")),
                "srcBlend": lay.get("src_blend"), "dstBlend": lay.get("dst_blend"),
                "srcBlendName": lay.get("src_blend_name"),
                "dstBlendName": lay.get("dst_blend_name"),
            })
        return out

    def weapon_motion_for(self, ident: str, action: str = "999") -> dict:
        r"""The mesh a weapon shows for one action.

        `ini/WeaponMotion.ini` is keyed `<appearance><action>` -- a 9-digit
        string -- so looking it up with the bare 6-digit appearance never
        matches. Order per docs/effects.md §8 step 3::

            WeaponMotion[A + action] -> WeaponMotion[A + "999"] -> weapon.ini Mesh0
        """
        wm = self.weapon_motion()
        forms = [f for f in (str(ident), str(ident).lstrip("0")) if f]
        for act, src in ((action, f"WeaponMotion.ini {action}"),
                         ("999", "WeaponMotion.ini 999")):
            for f in forms:
                hit = wm.get(f + act)
                if hit and self._exists(hit):
                    return {"mesh": hit, "action": act, "source": src}
        return {}

    def _effect_items(self, ident: str, table: str) -> tuple[list[dict], bool]:
        """Weapon -> effect linkage, from task #13's `out/effects/linkage.json`.

        Returns `(items, ready)`. `ready` is False only when the linkage file is
        missing, so the group stays on screen as an explicit placeholder rather
        than silently vanishing.
        """
        link = self.weapon_linkage()
        if not link:
            return [], False
        eff = self.weapon_effects(ident)
        if not eff:
            return [], True
        out: list[dict] = []
        seen = set()

        def push(name, label, role):
            if not name or name in seen:
                return
            seen.add(name)
            layers = self.effect_layers(name)
            paths = self.effect_assets(name)
            out.append({
                "path": paths[0] if paths else "",
                "role": role_of(paths[0]) if paths else "effect",
                "label": f"{label}: {name}",
                "effect": name, "effectRole": role,
                "layers": len(layers),
                "resolved": bool(paths),
                # every extra mesh/texture the effect pulls in, so the strip
                # shows the whole effect rather than only its first layer
                "assets": paths,
            })

        push(eff.get("aura"), "aura (always on)", "aura")
        for action, name in sorted((eff.get("attack") or {}).items()):
            push(name if isinstance(name, str) else str(name),
                 f"attack trail {action}", f"trail:{action}")
        push(eff.get("hitEffect"), "impact — drawn at the TARGET", "impact")
        push(eff.get("blockEffect"), "block — drawn at the TARGET", "block")
        # Effect names are 3DEffect.ini keys, resolved to meshes and textures
        # through 3DEffectObj.ini / 3dtexture.ini -- see effect_assets().
        return out, True


def _cli(argv):
    import argparse
    import collections
    import json
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--audit", action="store_true",
                    help="show what falls into 'other'")
    ap.add_argument("path", nargs="?", help="classify one path and stop")
    a = ap.parse_args(argv)

    if a.path:
        c = AssetCatalog(Path(a.root)).classify(a.path)
        print(json.dumps(c.to_json(), indent=2))
        return 0

    import coviewer
    cat = coviewer.Catalog(Path(a.root))
    try:
        cat.wait_tables()
        ac = AssetCatalog(Path(a.root), table_membership=cat.references,
                          exists=cat.exists)
        s = ac.summarise(cat.all_paths)
        total = sum(v["count"] for v in s.values())
        print(f"{total} resolvable assets\n")
        for cid in CATEGORY_IDS:
            e = s.get(cid)
            if not e:
                continue
            print(f"  {CATEGORY_LABEL[cid]:<16}{e['count']:>7}")
            order = SUBCATEGORY_ORDER.get(cid, [])
            subs = sorted(e["subs"].items(),
                          key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99,
                                          -kv[1]))
            for k, v in subs:
                print(f"      {k:<14}{v:>7}")
        if a.audit:
            print("\nunclassified sample:")
            oth = [p for p in cat.all_paths if ac.classify(p).category == "other"]
            pre = collections.Counter("/".join(p.split("/")[:2]) for p in oth)
            for k, v in pre.most_common(25):
                print(f"   {k:<44}{v:>6}")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
