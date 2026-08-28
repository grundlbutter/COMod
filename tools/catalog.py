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
Characters as per-body-type motion sets.  **There are sixteen such families,
not four** -- `c3/1001..1004`, `2001..2004` and `3001..3004` are the same
shape and are named by `ini/3dmotion.dbc`; they were landing in
`character/misc` until the rule's pattern was widened.  See the `RULES` entry
for the evidence.

`animation_buckets()` below is the one place that answers "which of these
buckets is animation the Asset Viewer cannot preview", and the viewer's
"hide animation assets" toggle is built on it.

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
from wdf import detect_magic                            # noqa: E402

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

#: The name this taxonomy uses for an animation bucket. One string, so the
#: set below is a *query against the taxonomy* rather than a second list that
#: can drift away from it.
ANIMATION_SUBCATEGORY = "motion"


def animation_buckets() -> set[tuple[str, str]]:
    r"""Every ``(category, subcategory)`` the taxonomy itself calls animation.

    **DERIVED, not declared.** It is read out of `SUBCATEGORY_ORDER` above --
    the taxonomy's own statement of which buckets exist -- by asking which of
    them are named `motion`. Today that is `character/motion` and
    `weapon/motion`; a category that gains a `motion` bucket later is covered
    without editing anything here, and a hand-written list would not have been.

    The bucket is filled by one rule, which says what it is in its own `why`:
    `^c3/[0-3]00[1-4]/` -> the per-body-type motion sets.

    WHAT THIS IS FOR, AND WHAT IT IS NOT
    ------------------------------------
    The Asset Viewer cannot preview an animation -- only the Builder / model
    view binds a motion over a mesh and plays it -- so these entries are noise
    in that mode and the viewer offers a toggle that hides them.  MEASURED on
    `patch5517-d3ba8e7aa082`, all 2,475 paths in `character/motion`, by reading
    each container's chunk tags:

        2,100   carry no PHY-family chunk at all -- `/api/mesh` returns
                `meshes: []` and the viewport draws literally nothing
          375   carry PHY + MOTI (+ CAME) and *do* draw a static pose

    So hiding the bucket is not free: 375 of them would have rendered
    something.  They are still frames of an action set rather than assets in
    their own right, and neither half can be previewed *as animation* here,
    which is the distinction the toggle is named for.  It is off by default and
    it says how many entries it removed -- see `coviewer.api_categories`.

    KNOWN GAP, measured rather than assumed.  Monster, mount and NPC families
    keep their motion-only action files in their `mesh` bucket, because the
    taxonomy files art by *who it belongs to* and only `c3/N00M/` announces
    itself as a motion set in the path.  Same sweep, same base:

        monster/mesh    1,024 of 1,127 carry no PHY
        mount/mesh        189 of   193
        npc/mesh           70 of   238
        effect/mesh     1,838 of 4,547  -- NOT unpreviewable: the effect
                                          player draws these, so "no PHY" is
                                          not a synonym for "cannot preview"

    Telling those apart needs `models.ModelCatalogue.has_geometry` per file,
    not the taxonomy, so they are **out of scope for this toggle** rather than
    silently half-covered.  OPEN.
    """
    return {(cid, sub)
            for cid, subs in SUBCATEGORY_ORDER.items()
            for sub in subs
            if sub == ANIMATION_SUBCATEGORY}


#: Materialised once: the taxonomy is a module-level constant, so this is too.
ANIMATION_BUCKETS: set[tuple[str, str]] = animation_buckets()


def is_animation(category: str, subcategory: str) -> bool:
    """True for an entry the Asset Viewer cannot preview as animation."""
    return (category, subcategory) in ANIMATION_BUCKETS


#: Role is orthogonal to category: what *kind* of file it is. It is what lets
#: the map view separate "the big map" from "the sprites that go on it".
ROLE_BY_EXT = {
    ".c3": "mesh", ".dds": "texture", ".png": "texture", ".jpg": "texture",
    ".jpeg": "texture", ".bmp": "texture", ".tga": "texture", ".ico": "texture",
    ".cur": "cursor", ".dmap": "map", ".pul": "mapdata", ".scene": "mapdata",
    ".part": "mapdata", ".ani": "mapdata", ".wav": "sound", ".mp3": "sound",
    ".ogg": "sound", ".ini": "data", ".json": "data", ".txt": "data",
}


# ---------------------------------------------------------------------------
# the weapon-effect linkage artefact, and why it might not be there
# ---------------------------------------------------------------------------

#: `AssetCatalog.linkage_status()["state"]`.  Four values because an empty
#: `weapon_linkage()` has four causes and only one of them is a fact about the
#: game data; collapsing them is what let a missing artefact be reported as
#: "Flash4102 does not resolve" for as long as it was.
LINKAGE_OK = "ok"                  # loaded, with content
LINKAGE_MISSING = "missing"        # find_derived found nothing to read
LINKAGE_UNREADABLE = "unreadable"  # there, but did not parse into an object
LINKAGE_EMPTY = "empty"            # parsed, and holds no linkage at all

#: How to build it.  No ``--out``: the artefact is per-base (`coroot.PER_BASE`
#: carries ``out/effects/``), so `--out out/effects/linkage.json` writes the
#: unkeyed literal, which `find_derived` never reads -- there is deliberately
#: no fallback from the keyed path to the unkeyed one.
LINKAGE_BUILD_CMD = "py -3 tools/effects.py --root {root} --linkage"


def _linkage_reason(st: dict) -> str:
    """One line naming the state, the keyed path, and the base it was keyed to.

    The path and the base id travel together on purpose. "Not built yet" and
    "built, then orphaned when the install re-keyed" are indistinguishable
    without both, and telling them apart is the difference between a 30-second
    answer and re-deriving a 346 MB index (`docs/CORRECTIONS.md` C18).
    """
    where = f"{st.get('rel', '?')} (base {st.get('base_id') or 'undeclared'})"
    state = st.get("state")
    if state == LINKAGE_OK:
        return (f"linkage loaded from {where}: "
                f"{st.get('effects', 0)} effects, {st.get('weapons', 0)} weapon "
                f"appearances")
    build = LINKAGE_BUILD_CMD.format(root=st.get("root") or "<install>")
    if state == LINKAGE_MISSING:
        return (f"no weapon-effect linkage: {where} is in neither this checkout "
                f"nor the primary one. Nothing was loaded, so every effect "
                f"lookup answers empty for want of a table, not for want of a "
                f"row. Build it with: {build}")
    if state == LINKAGE_UNREADABLE:
        return (f"weapon-effect linkage at {where} did not parse "
                f"({st.get('detail') or 'unknown error'}); nothing was loaded. "
                f"Rebuild it with: {build}")
    return (f"weapon-effect linkage at {where} parsed but is empty -- no "
            f"effects and no weapon appearances. Rebuild it with: {build}")


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
    # `c3/0001..0004` are the four player body types (docs/appearance_ids.md).
    # `c3/1001..1004`, `2001..2004` and `3001..3004` are the SAME shape --
    # `<family>/<group>/<action>.c3` -- and were landing in `character/misc`
    # because the pattern only allowed a leading `0`. Three sources agree they
    # are motion sets, so the pattern now covers all sixteen:
    #   1. the client's own motion table names them. `ini/3dmotion.ini` has
    #      not heard of them (0 references), but `ini/3dmotion.dbc` names all
    #      twelve, on both 5517 and 6090.
    #   2. MEASURED: 736/736 of their `.c3` files on 5517 and 1,516/1,528 on
    #      6090 carry no PHY-family chunk -- pure motion, nothing to draw.
    #      (The `000N` families are mixed the same way: 375 of 1,739 do have
    #      geometry, so a stray PHY here is the normal pattern, not a
    #      counter-example.)
    #   3. CCO ships only `0001..0004` and no `.dbc` at all, so the widened
    #      pattern cannot match anything there. Checked on all three declared
    #      bases rather than the two that were to hand.
    Rule("^c3/[0-3]00[1-4]/", "character", "motion",
         "c3/N00M/ is a motion set for player body type M; ini/3dmotion.dbc "
         "names it and its files carry no geometry", 1),
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


#: Roles whose file format this module can actually recognise from its first
#: bytes (`wdf.detect_magic`). Only these may be overruled by a sniff: a
#: `.DMap`, `.ani` or `.pul` has no magic here, so "unrecognised" says nothing
#: about it and must not demote it.
SNIFFABLE_ROLES = frozenset({"mesh", "texture"})


def role_of(path: str, head: bytes = b"") -> str:
    """The asset's role. With ``head``, the CONTENT decides; without it, the
    extension does.

    **On this client family the extension lies, and the viewer believed it.**
    MEASURED in Zephyr's `c3.tpd`, over all 53,536 `.c3`/`.dds` entries:

        20  .c3  files that are really DDS       (a texture called a mesh)
         8  .dds files that are really MAXF C3   (a mesh called a texture)
        24  .dds files that are really PNG
         5  .dds files that are really JPEG
         3  .c3  files with an unrecognised magic
         1  .c3  file that is plain ini text (`c3/npc/006/2000.c3`)

    61 assets, and every one of them was unreadable: `role_of` sent them to a
    decoder chosen by extension, and `dds.decode` / `C3File` reject a payload
    whose magic disagrees. `c3/0004/615/120.c3` is a perfectly good DDS that
    **decodes fine** once it reaches the right decoder -- the file was never
    broken, the routing was.

    Two deliberate limits, because a sniff that overrides too much is worse
    than one that overrides too little:

    * only `SNIFFABLE_ROLES` may be overruled -- an unrecognised magic on a
      `.DMap` means "this module does not know that format", not "the
      extension is wrong";
    * a recognised magic wins outright, which is what fixes the 57 files
      above whose real format IS one we know.

    `tools/assetdiff.py` has sniffed unnamed WDF payloads this way since it
    was written; this brings the catalogue's *named* path into line with it.
    """
    by_ext = ROLE_BY_EXT.get(Path(path).suffix.lower(), "data")
    if not head:
        return by_ext
    label, ext = detect_magic(head)
    if label != "UNKNOWN":
        return ROLE_BY_EXT.get(ext, by_ext)
    # Unrecognised bytes: only meaningful where we would have recognised the
    # real thing. `c3/npc/006/2000.c3` is ini text under a mesh's name.
    return "data" if by_ext in SNIFFABLE_ROLES else by_ext


#: Rows one map group will list before it stops and says how many there were.
#:
#: MEASURED over 7878's 470 maps: the worst single map names **1,211 distinct
#: (sheet, key) cover pairs** (`n-newplain08`), while terrain, effects and
#: sound peak at 12, 13 and 10.  So the cap only ever bites on cover, and a
#: card is not the place for a 1,211-row second catalogue.  The note states
#: the true total, so the cap is never mistaken for the data.
MAP_GROUP_CAP = 256

#: Where a world grid lives.  Both suffixes, because 7878 ships **470 `.7z`
#: and not one loose `.DMap`** while 5017/5065/5165 ship only `.DMap` -- a
#: check for one spelling answers "not a map" on half the installs.
_MAP_GRID_SUFFIXES = (".dmap", ".7z")


def map_name_of(path: str) -> str:
    """The map NAME a `map/map/...` selection is about, or "".

    A world grid is the one selection whose companions are not files beside
    it: it is a container of layers.  This is the test that routes such a
    selection to `AssetCatalog.map_layer_groups`, and it is deliberately
    narrow -- `map/puzzle/island.pul` and `data/map/mapobj/...` are map ART,
    already classified under `map`, and must NOT be treated as grids.

    The name is returned rather than the path because every reader downstream
    (`dmap.open_map`, `dmap.parse_map`, the registry in `GameMap.dat`) is
    keyed by name, and because the two suffixes are two spellings of one map.
    """
    p = str(path or "").replace("\\", "/").lstrip("/")
    parts = p.split("/")
    if len(parts) != 3 or parts[0].lower() != "map" or parts[1].lower() != "map":
        return ""
    q = Path(parts[2])
    return q.stem if q.suffix.lower() in _MAP_GRID_SUFFIXES else ""


class AssetCatalog:
    """Classifies logical asset paths and gathers "what goes with this".

    `table_membership` maps a logical path to the appearance tables that
    reference it -- pass the viewer's reverse index so table evidence is used.
    """

    def __init__(self, root: Path = DEFAULT_ROOT,
                 table_membership: Optional[Callable[[str], list]] = None,
                 exists: Optional[Callable[[str], bool]] = None,
                 list_under: Optional[Callable[[str], list]] = None,
                 npc_membership: Optional[Callable[[str], bool]] = None,
                 peek: Optional[Callable[[str], bytes]] = None):
        self.root = Path(root)
        self._tables = table_membership or (lambda p: [])
        self._exists = exists or (lambda p: (self.root / p).is_file())
        #: True when nobody injected `exists` and the fallback above -- a
        #: LOOSE-FILE STAT -- is what answers. It cannot see inside `c3.tpd`
        #: or `c3.wdf`, so on an archive-heavy install it reports art that
        #: ships as absent. MEASURED on 7878's map effect layers: the loose
        #: walk calls **1,659 of 2,941 records (56.4%)** mesh-absent where
        #: `coassets.AssetRoot.exists` -- which asks the containers too --
        #: calls **0**. `map_layer_groups` reads this and says so on the
        #: group rather than presenting the artefact as the data.
        self._exists_is_loose_only = exists is None
        self._list_under = list_under or (lambda prefix: [])
        self._npc = npc_membership or (lambda p: False)
        #: First bytes of a logical path, for `role_of`'s content sniff, or
        #: b"" when they cannot be had cheaply. Injected because only the
        #: caller knows how to reach into its archives.
        #:
        #: **The default does NOT peek, and that is deliberate.**
        #: `coviewer.category_index` classifies every path in the install in
        #: one pass -- 77k of them -- and a peek there costs one filesystem
        #: probe each. MEASURED: a default that stats the loose file made
        #: classification **6x slower** (0.22s -> 1.28s per 40k paths) to
        #: correct 61 files, in an index used for *browsing*, where the role
        #: only picks a folder. The 61 files break at **decode**, one at a
        #: time, where the bytes are already in hand and the sniff is free --
        #: so that is where a caller should pass `peek`.
        self._peek = peek or (lambda p: b"")
        self._cache: dict[str, Classification] = {}
        self._weapon_motion: Optional[dict[str, str]] = None
        self._linkage: Optional[dict] = None
        self._linkage_status: Optional[dict] = None
        #: Where `map_layer_groups` looks for `map/map/`, `ani/` and
        #: `GameMap.dat`.  Separate from `self.root` because a **server view**
        #: reads its maps from a materialized root under `out/serverviews/`
        #: while every other lookup still goes to the install -- see
        #: `coviewer`'s `_map_root`, which assigns this.  Defaulting it to
        #: `self.root` rather than requiring it keeps every existing caller
        #: correct; a server view that forgets to assign it reads the
        #: install's maps, which is wrong but visible, not silently empty.
        self.map_root = self.root
        #: `.ani` sheet -> (logical rel, frame index). See `_ani_table`.
        self._ani_cache: dict[str, tuple] = {}
        #: Lower-cased `3DEffect.ini` section names, built on first map
        #: selection only. See `_effect_db`.
        self._effect_names: Optional[set] = None

    # -- classification ----------------------------------------------------
    def classify(self, path: str) -> Classification:
        p = path.replace("\\", "/").lstrip("/").lower()
        hit = self._cache.get(p)
        if hit is not None:
            return hit
        c = self._classify(p)
        self._cache[p] = c
        return c

    def _peek_loose(self, p: str) -> bytes:
        """First bytes of a loose file, or b"". Never raises: a sniff that
        fails must degrade to the extension, not break classification."""
        try:
            q = self.root / p
            if q.is_file():
                with open(q, "rb") as f:
                    return f.read(16)
        except OSError:
            pass
        return b""

    def _classify(self, p: str) -> Classification:
        try:
            head = self._peek(p) or b""
        except Exception:
            head = b""
        role = role_of(p, head)

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
                # When the linkage did not load, the note says which keyed
                # path was missing and for which base -- an empty slot that
                # explains itself, rather than one that looks like "this
                # weapon has no effects".
                add("Effects", items,
                    ("weapon effect linkage (tools/effects.py)" if ready
                     else self.linkage_status().get("reason", "")),
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
            map_name = map_name_of(path)
            if map_name:
                groups.extend(self.map_layer_groups(map_name))

        return [g for g in groups if g["items"] or g["pending"]]

    # -- what goes with a MAP ----------------------------------------------

    def map_layer_groups(self, name: str) -> list[dict]:
        r""""What goes with this map", as the same labelled groups.

        A map is not one asset with satellites: it is a **container of four
        independent layer kinds**, each with its own resolution chain, so this
        returns up to four groups and never one flat list.  The fourth is
        **sound**, and it is the one a visual browser drops -- a map goes with
        its sounds.  See `docs/viewer.md` section *What goes with this* ->
        *When the selection is a MAP* for the measurement this implements.

        MEASURED over all 470 of 7878's maps, 2026-08-27:

            tag  1 terrain     2,294 records  100.00%  -> map/Scene/*.scene
            tag  4 + 24 cover  244,086         99.90%  -> .ani sheet + key
            tag 10 effect      2,941           94.15%  -> 3DEffect.ini, 3 hops
            tag 15 sound       1,412           85.62%  -> sound/*.wav
            tag  5             0 records in all 470 maps

        **`range` and `volume` ride the RECORD, not the file.**  20 distinct
        `.wav` files carry **32 distinct `(range, volume)` pairs**;
        `sound/water.wav` alone is placed under 12 of them.  A group listing
        twenty filenames has thrown the layer away, so a sound row carries
        both fields and its origin.

        **An empty group is nearly always the data.**  Of 470 maps, 404 carry
        cover, 69 terrain, 59 effects and **27 sound**, and 48 declare no
        layers at all.  Every group therefore ships a `note` saying what the
        emptiness means, the way the weapon *attack trail* group does.

        **The map is opened through the registry, not by globbing.**
        `dmap.open_map` lets `GameMap.dat` pick which twin the client actually
        reads; a directory walk counts a map that ships as BOTH `.DMap` and
        `.7z` twice.  That is not hypothetical -- it doubled a 6609 cover
        figure to 1,272 / 1,266 from the true 636 / 633.

        Returns `[]` for a name this install does not ship, and a single
        `pending` group when the map is there and would not parse -- those are
        different facts and an empty card cannot tell them apart.
        """
        try:
            import dmap as _dmap
        except Exception as exc:                          # pragma: no cover
            return [{"title": "Map layers", "note": f"core/dmap.py: {exc}",
                     "items": [], "pending": True}]

        raw, why = _dmap.open_map(self.map_root, name)
        if raw is None:
            return []
        try:
            d = _dmap.parse(self.map_root / "map" / "map" / f"{name}.DMap",
                            data=raw, want_cells=False, verify=False)
        except Exception as exc:
            return [{"title": "Map layers",
                     "note": f"{why}: did not parse: {exc}",
                     "items": [], "pending": True}]
        return self.map_groups_for_layers(d.layers)

    def map_groups_for_layers(self, layers: list[dict]) -> list[dict]:
        r"""The four groups, from a decoded layer table.

        **Split out from `map_layer_groups` so it can be tested at all**, and
        that is not a stylistic preference -- it is a defect this split fixed.
        The first version of these tests asserted the blank-row skip and the
        sound dedupe key against whichever map the configured install happened
        to ship. Both went GREEN with the code deliberately broken: the
        fixture map had no all-zero cover rows and no file placed at two
        volumes, so neither assertion could reach the thing it was named for.
        *An instrument whose output is independent of the hypothesis cannot
        test it, however true that output is.* Feeding synthetic layers in
        here makes the negative control fire on every install.

        `layers` is `dmap.DMap.layers` -- a list of `decode_layer` dicts.
        """
        scenes: dict[str, int] = {}
        cover: dict[tuple, int] = {}
        names: dict[str, int] = {}
        sounds: dict[tuple, int] = {}
        # Folded key -> the first spelling seen, so rows group correctly and
        # still display the path the map actually wrote.
        spelling: dict[str, str] = {}
        unfilled = 0

        def fold(p: str) -> str:
            r"""One map path, folded for GROUPING.

            Both `\`->`/` AND case, because the same sheet is written
            `ani\mapscene-new.ani` and `ani/MapScene-new.ani` in the wild and
            folding only the separator counts one file as two. That is the
            double-count this repo has now paid for twice -- here, and in the
            `.DMap`/`.7z` twin walk `open_map` above avoids.
            """
            f = str(p).replace("\\", "/")
            spelling.setdefault(f.lower(), f)
            return f.lower()

        for lay in layers:
            shape = lay.get("shape")
            if shape == "scene":
                p = lay.get("path") or ""
                if p:
                    k = fold(p)
                    scenes[k] = scenes.get(k, 0) + 1
            elif shape == "cover":
                p = lay.get("path") or ""
                # Two distinct kinds of never-filled editor row, and
                # `size == [0,0]` is NOT the same test as `"=" in path`:
                # on 7878 they catch 917 and 2, on 6609 1,819 and 636. The
                # `=` form has the ini FIELD NAMES where the values go
                # (`AniTitle=` / `PosCell=[0,0]`); the other is all zero.
                # Either test alone leaves one kind of blank row in the card.
                if "=" in p or lay.get("size") == [0, 0]:
                    unfilled += 1
                    continue
                k = (fold(p), lay.get("key") or "")
                cover[k] = cover.get(k, 0) + 1
            elif shape == "effect":
                n = lay.get("name") or ""
                if n:
                    names[n] = names.get(n, 0) + 1
            elif shape == "sound":
                p = lay.get("path") or ""
                if p:
                    k = (fold(p), lay.get("range"), lay.get("volume"))
                    sounds[k] = sounds.get(k, 0) + 1

        loose = (" — resolution is a LOOSE-FILE stat, so anything that ships "
                 "only inside c3.tpd / c3.wdf reads as absent"
                 if self._exists_is_loose_only else "")

        def cap(rows: list) -> tuple[list, str]:
            if len(rows) <= MAP_GROUP_CAP:
                return rows, ""
            return (rows[:MAP_GROUP_CAP],
                    f" — showing {MAP_GROUP_CAP} of {len(rows)}")

        out: list[dict] = []

        # -- terrain
        rows = [{"path": spelling[k], "role": "mapdata",
                 "label": Path(k).name,
                 "count": c, "found": bool(self._exists(k))}
                for k, c in sorted(scenes.items(), key=lambda kv: -kv[1])]
        rows, more = cap(rows)
        out.append({
            "title": "Terrain scenes", "items": rows,
            "note": ("tag 1 — the layer's `path` IS a file. 100.00% of 7878's "
                     "2,294 records resolve, so a miss here is worth a look. "
                     "Only 69 of 470 maps carry terrain: empty is the data"
                     + more + loose),
            "pending": not rows})

        # -- cover
        sheets: dict[str, int] = {}
        for (sh, _k), c in cover.items():
            sheets[sh] = sheets.get(sh, 0) + c
        rows = []
        # A cover record can fail in TWO places and they are different facts,
        # so the row carries both rather than one merged `found`:
        #   * the `key` is not a section in the sheet -- an ini bug. 254
        #     records over 16 keys on 7878, and 15 of those 16 are defined in
        #     no install's copy of the same sheet.
        #   * the key IS there and one of its frame files is absent -- 509
        #     records over 121 files, all 121 absent from every install.
        # Merging them puts 99.90% and 99.69% behind one flag and loses the
        # ability to say WHICH half of the chain broke.
        for (sh, key), c in sorted(cover.items(), key=lambda kv: -kv[1]):
            rel, table = self._ani_table(sh)
            frames = None
            for k2, v2 in table.items():
                if k2.lower() == key.lower():
                    frames = v2
                    break
            missing = ([f for f in frames if not self._exists(f)]
                       if frames else [])
            rows.append({"path": spelling[sh], "role": "mapdata",
                         "label": f"{Path(sh).name} [{key}]", "key": key,
                         "count": c, "sheet": rel or spelling[sh],
                         "frames": len(frames or []),
                         "framesMissing": len(missing),
                         "found": frames is not None})
        rows, more = cap(rows)
        out.append({
            "title": "Cover sheets", "items": rows,
            "note": (f"tags 4 and 24 — `path` is an .ani sheet and `key` is a "
                     f"frame section inside it. {len(sheets)} sheet(s) here; "
                     f"all 470 of 7878's maps together use only 7. "
                     f"`found` false means the KEY is missing from the sheet; "
                     f"`framesMissing` means the key is there and the art is "
                     f"not — two different faults, both in the ini rather than "
                     f"in the lookup. "
                     f"{unfilled} never-filled editor row(s) skipped"
                     + more + loose),
            "pending": not rows})

        # -- effects
        rows = []
        for n, c in sorted(names.items(), key=lambda kv: -kv[1]):
            rows.append({"path": "", "role": "effect", "label": n,
                         "effect": n, "count": c,
                         "found": self._effect_defined(n)})
        rows, more = cap(rows)
        # `found` here can only be False for two reasons and they are not
        # the same fact, so the note names the second one explicitly.
        no_table = ("effect names cannot be checked: tools/effects.py did "
                    "not import, so every row reads undefined"
                    if self._effect_db() is None else "")
        out.append({
            "title": "Effects", "items": rows,
            "note": ((no_table or
                      "tag 10 — the layer carries a NAME, not a path: "
                      "3DEffect.ini -> EffectId/TextureId -> 3DEffectObj.ini "
                      "and 3dtexture.ini -> the .c3. 18 map-sourced names "
                      "are undefined on 7878 (172 records) — because it "
                      "ships no compiled 3DEffect.dbc, not because its "
                      "3DEffect.ini differs: that ini is byte-identical to "
                      "6609's. A DIFFERENT population from the 2,255 "
                      "weapon/item names, which all resolve") + more),
            "pending": not rows})

        # -- sound
        rows = []
        for (p, rng, vol), c in sorted(sounds.items(), key=lambda kv: -kv[1]):
            rows.append({"path": spelling[p], "role": "sound",
                         "label": (f"{Path(spelling[p]).name}"
                                   f" · range {rng} · volume {vol}"),
                         "range": rng, "volume": vol, "count": c,
                         "found": bool(self._exists(p))})
        rows, more = cap(rows)
        out.append({
            "title": "Sounds", "items": rows,
            "note": ("tag 15 — **range and volume are per RECORD**, not "
                     "properties of the .wav: 20 files carry 32 distinct "
                     "(range, volume) pairs across 7878, water.wav alone "
                     "under 12. Only 27 of 470 maps carry sound at all, so an "
                     "empty panel here is the data and not a broken chain"
                     + more + loose),
            "pending": not rows})

        return out

    def _ani_table(self, rel: str) -> tuple[str, dict]:
        """One `.ani` frame index, loaded once per sheet.

        Cached because 7878's 244,086 cover records reach only **7** distinct
        sheets and `MapScene-new.ani` alone holds 13,516 sections -- 35 ms to
        read. Keyed on the path folded BOTH ways, `\\`->`/` and case, because
        `ani\\mapscene-new.ani` and `ani/MapScene-new.ani` are one file and
        counting them as two is the double-count this cache exists to avoid.
        """
        key = str(rel).replace("\\", "/").lower()
        if key in self._ani_cache:
            return self._ani_cache[key]
        try:
            import dmap as _dmap
            hit = _dmap.load_ani(self.map_root, rel)
        except Exception:                                  # pragma: no cover
            hit = ("", {})
        self._ani_cache[key] = hit
        return hit

    def _effect_defined(self, name: str) -> bool:
        """Is `name` defined in this install's LIVE 3DEffect table?

        The live table is the compiled `ini/3DEffect.dbc` where one ships
        and the plaintext `ini/3DEffect.ini` otherwise -- `EffectDB` picks,
        the same way `GraphicData.dll` does. **Not "is it in the ini", and
        the difference is not academic:** `3DEffect.ini` is byte-identical
        across 5165/5517/6090/6609/7878 (599,875 B, sha256 aa059fc7a8fc...),
        so every disagreement between those installs is a disagreement
        about whether a `.dbc` ships -- 6609 reads 5,290 names from a 2017
        twin, 7878 reads 2,595 from the 2009 ini and has no twin at all.
        Describing this as an ini difference is a false claim that was
        written down once already; see docs/viewer.md gap 2.

        Case-folded, because a map writes `zf2-e225` and the table's
        spelling is its own. False when `tools/effects.py` is not
        importable at all -- the group's note carries that reason, so this
        is never the only signal.
        """
        db = self._effect_db()
        if db is None:
            return False
        return name.lower() in db

    def _effect_db(self) -> Optional[set]:
        """Lower-cased `3DEffect.ini` section names, built once, or None.

        `EffectDB` costs ~100 ms to construct and reads the compiled `.dbc`
        twin where one ships, so this is lazy and cached rather than built in
        `__init__`: most selections are not maps and never need it.
        """
        if self._effect_names is None:
            if effects is None:
                self._effect_names = set()
            else:
                try:
                    db = effects.EffectDB(self.root)
                    self._effect_names = {k.lower() for k in db.effects}
                except Exception:                          # pragma: no cover
                    self._effect_names = set()
        return self._effect_names or None

    def weapon_linkage(self) -> dict:
        r"""`out/effects/linkage.json` from task #13. Loaded once, lazily.

        Empty is **four different facts**, and `linkage_status` is how a caller
        tells them apart:

        * `LINKAGE_MISSING`  -- nothing was loaded; the artefact has not been
          built for this base.
        * `LINKAGE_UNREADABLE` -- it is there and did not parse.
        * `LINKAGE_EMPTY`    -- it parsed and holds no linkage.
        * `LINKAGE_OK`       -- it loaded, and an empty *answer* from it is the
          table saying no.

        This used to `except Exception: return {}` and say nothing, so
        `effect_assets` answered `[]` and `weapon_effects` answered `{}` for
        both "the table says no" and "there is no table" -- and two viewer
        tests read as content refutations ("Flash4102 does not resolve") when
        the truth was "nothing was loaded". That is exactly the degradation
        `coroot.find_derived`'s docstring forbids: *"a caller that would
        degrade without the artefact must then say so out loud rather than
        carry on with less"*.

        **Resolved against `self.root`, not the configured install.** The
        catalogue knows which client it is describing and the process may hold
        two of them at once -- the read side of the C22 / C41(c) rule, and the
        same instruction `coviewer._derived_text` already carries.
        """
        if self._linkage is None:
            self._linkage = {}
            import coroot
            rel = coroot.derived_rel("out/effects/linkage.json", self.root)
            st = {"state": LINKAGE_MISSING, "rel": rel, "path": "",
                  "base_id": coroot.base_id(self.root), "root": str(self.root),
                  "effects": 0, "weapons": 0, "detail": ""}
            p = coroot.find_derived("out/effects/linkage.json", self.root)
            if p is not None:
                st["path"] = str(p)
                try:
                    doc = json.loads(p.read_text("utf-8"))
                except (OSError, ValueError) as exc:
                    st["state"] = LINKAGE_UNREADABLE
                    st["detail"] = f"{type(exc).__name__}: {exc}"
                else:
                    if not isinstance(doc, dict):
                        st["state"] = LINKAGE_UNREADABLE
                        st["detail"] = (f"top level is {type(doc).__name__}, "
                                        f"expected an object")
                    else:
                        self._linkage = doc
                        st["effects"] = len(doc.get("effects") or {})
                        st["weapons"] = len(doc.get("weapon_appearances") or {})
                        st["state"] = (LINKAGE_OK
                                       if (st["effects"] or st["weapons"])
                                       else LINKAGE_EMPTY)
            st["reason"] = _linkage_reason(st)
            self._linkage_status = st
        return self._linkage

    def linkage_status(self) -> dict:
        """Why `weapon_linkage` holds what it holds -- loaded, or why not.

        Always populated (loading is forced if it has not happened), so a
        caller never has to guess whether the blank it is looking at is an
        answer or an absence. `reason` is a one-line sentence naming the
        **keyed** path that was looked for and the base it was keyed to,
        which is the pair that identifies a "never built here" from an
        "orphaned by a re-key" without any further digging.
        """
        self.weapon_linkage()
        return dict(self._linkage_status or {})

    def linkage_ready(self) -> bool:
        """True when the linkage artefact loaded with content in it."""
        return self.linkage_status().get("state") == LINKAGE_OK

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

        Returns `(items, ready)`. `ready` is False when the linkage artefact did
        not load -- missing, unreadable, or empty -- so the group stays on screen
        as an explicit placeholder rather than silently vanishing, and the
        caller can put `linkage_status()["reason"]` on it. `ready` True with no
        items is the other fact: the table loaded and this weapon has none.
        """
        if not self.linkage_ready():
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




#: WHEN THE DERIVED CHANNEL IS UNAVAILABLE, SAY SO. Measured 2026-08-15 and
#: still true 2026-08-27: with `CO_DERIVED_FALLBACK=0` this tool reports
#: 53,757 resolvable assets instead of 77,825 on CCO, and 54,215 instead of
#: 77,475 on 6090 -- with EXIT CODE 0, no warning line and no banner. It
#: reports a smaller world as though it were complete.
#:
#: `tools/health.py` is the one tool that names the loss, and it also exits 0.
#: The knowledge existed and nothing carried it here: a wiring gap, not a
#: knowledge gap. The rule this implements, decided 2026-08-27 after the
#: finding sat twelve days in a dark seat's charter: "fewer assets" and "all
#: the assets" must not be the same exit code with the same silence.
def derived_channel_suppressed():
    """Why this run may under-count, or None.

    Fires only when the fallback is switched OFF *and* a primary checkout
    exists to have been read -- i.e. when there is a real loss to name. A
    checkout with no primary is not suppressed, it is alone, and saying
    otherwise would cry wolf on every ordinary clone.
    """
    import os
    try:
        import coroot
    except ImportError:
        return None
    val = os.environ.get(coroot.DERIVED_FALLBACK_VAR, "").strip().lower()
    if val not in ("0", "off", "no"):
        return None
    primary = coroot.primary_checkout()
    if primary is None:
        return None
    return ("%s=%s -- derived artefacts in %s were NOT read. This count is a "
            "LOWER BOUND, not a census: the same corpus reports 53,757 "
            "instead of 77,825 with this switch off. Unset it, or run "
            "tools/health.py, before quoting any number below."
            % (coroot.DERIVED_FALLBACK_VAR, val or "0", primary))


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
        suppressed = derived_channel_suppressed()
        if suppressed:
            print("UNDER-COUNTING: " + suppressed)
            print()
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
    # A caller that cannot see the banner still gets the answer in its
    # exit status. 3 = "ran, and the number is short".
    return 3 if derived_channel_suppressed() else 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
