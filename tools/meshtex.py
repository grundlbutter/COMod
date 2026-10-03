r"""
meshtex.py -- rank the DDS textures that could belong to a `.c3` mesh.

Pure stdlib.  Imports `coassets` (asset VFS + ini tables) and `c3phy` (PHY
parser); writes nothing into the game install.

Why this exists
---------------
`c3tex.py` answers "give me *a* texture for this mesh" by inverting the
appearance tables (`armor.ini`, `weapon.ini`, ...).  That covers equipment and
nothing else -- 1,389 of the 4,964 real meshes in this install.  This module
adds the other authored tables the client actually ships, plus a small set of
measured naming conventions, and -- crucially -- **labels every answer with the
rule that produced it and a confidence**, so a caller can tell
"`armor.ini` says so" apart from "there is a `.dds` next to it".

    from meshtex import MeshTextureIndex
    idx = MeshTextureIndex()
    for m in idx.matches("c3/monster/105/401.c3"):
        print(m.confidence, m.kind, m.method, m.texture)

    0.90 authored motion_appearance c3/texture/105000000.dds
    0.90 authored motion_appearance c3/texture/145000000.dds
    ...          (body types 105/145/305/505/705 share this mesh, different skins)

Every rule's status (VERIFIED / INFERRED) and its measured precision is in
`docs/meshtex.md`.  Nothing here guesses silently.

What counts as a mesh
---------------------
A `.c3` file is a chunk container.  1,426 of the 6,390 `.c3` files in this
install contain **no PHY-family chunk at all** -- they are pure `MOTI`
(animation), `PTCL`/`PTC3` (particle), `CAME` (camera) or `SHAP`+`SMOT` files.
Those have no geometry and therefore no texture, and counting them as
"unmatched meshes" would be dishonest.  `MeshTextureIndex.is_mesh()` decides
this by parsing the container, and `all_meshes()` returns only the 4,964 files
that really carry geometry.

CLI
---
    py -3 tools/meshtex.py c3/monster/105/401.c3      # rank one mesh
    py -3 tools/meshtex.py --coverage                 # write out/meshtex/
    py -3 tools/meshtex.py --coverage --verify        # ...and decode every DDS
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import AssetRoot, C3File, DEFAULT_ROOT, parse_ini   # noqa: E402
from wdf import detect_magic                                     # noqa: E402
import c3phy                                                      # noqa: E402
import coroot                                                     # noqa: E402
import provenance                                                 # noqa: E402

__all__ = ["Match", "MeshTextureIndex", "METHODS"]

REPO = Path(__file__).resolve().parent.parent

#: Rule catalogue.  ``kind`` is "authored" when the pairing is stated by a
#: shipped data file and "inferred" when it is a naming convention measured
#: from the corpus.
#:
#: **The confidence below is the rule's, for a mesh the rule identifies.**
#: When a rule names several textures for one mesh it has not identified it,
#: and `matches` divides the confidence by how many it named -- picking blind
#: among n is right 1/n of the time.  1,290 of 4,964 meshes here are in that
#: position, so this is the normal case rather than an edge one, and the
#: figures below are ceilings.
#:
#: That mattered: `c3/npc/999001100.c3` is the standby motion of thirteen
#: NPCs with two different skins between them, `npc_table` returned three
#: textures, and `best()` handed back whichever sorted first at the full
#: 0.95.  It was the wrong one, and everything built on it failed while
#: looking like a plumbing problem.  A shared asset has no single texture,
#: and the honest answer is a low confidence with `alternatives` set -- not
#: a confident pick.  Where one answer is actually needed, resolve the
#: *entity* instead: `core/npcart.py` does it for NPCs.
#:
#: Candidates are ranked **by kind first** -- every authored match outranks
#: every inferred one -- and by ``confidence`` within a kind.  For the
#: inferred rules ``confidence`` is literally the top-1 precision measured by
#: ``py -3 tools/meshtex.py --precision`` against the authored rules, so the
#: number in this table is a measurement and not an opinion.  Re-run that
#: command after touching a rule and update the number.
METHODS: dict[str, tuple[str, float, str]] = {
    # method                 kind         conf   one-line description
    "effect_table":     ("authored", 0.98, "3DEffect.ini EffectId->TextureId"),
    "simpleobj_table":  ("authored", 0.98, "3DSimpleObj.ini Part->Texture"),
    "npc_table":        ("authored", 0.95, "npc.json -> 3DSimpleObj.ini -> Texture"),
    "simplerole_table": ("authored", 0.95, "3DsimpleRole.ini -> 3DSimpleObj.ini -> Texture"),
    "appearance_table": ("authored", 0.95, "armor/weapon/armet/... Mesh<i>->Texture<i>"),
    "motion_appearance": ("authored", 0.90, "motion id -> appearance id -> Texture<i>"),
    "bodytype_dir":     ("inferred", 0.98, "c3/<kind>/<NNN>/ -> c3/texture/<NNN>000000.dds"),
    "stem_pair":        ("inferred", 0.97, "identical numeric stem in c3/texture or own dir"),
    "npc_look_id":      ("inferred", 0.97, "c3/npc/999<look><action>.c3 -> c3/texture/999<look>*.dds"),
    "objtex_id":        ("inferred", 0.73, "same id in 3dobj.ini and 3dtexture.ini"),
    "dir_consensus":    ("inferred", 0.71, "an authored match of a .c3 sibling in the same dir"),
    "label_sibling":    ("inferred", 0.62, "PHY 3DSMax label basename, in the mesh's own dir"),
    # top-1 only 0.30, but the right texture is somewhere in the returned set
    # 90% of the time -- treat this one as a shortlist, not an answer.
    "dir_sibling":      ("inferred", 0.30, "any .dds in the mesh's own directory"),
}

#: `c3/<kind>/<body type>/<action>.c3` layout.  VERIFIED for monster via the
#: 3dmotion.ini key decomposition; the same shape holds for ghost/npc/mount.
_BODYTYPE_DIRS = ("monster", "ghost", "npc", "mount")

#: Directories `coassets.AssetRoot.resolve_asset` probes for a bare numeric
#: texture id.  Kept here so the ranking can use the *order* as a tie-break.
TEX_DIRS = ("texture", "weapon", "body", "hair", "mount", "npc", "monster")

_PHY_TAGS = (b"PHY ", b"PHY4", b"PHY2", b"PHY3", b"PHY5", b"MNEW")
_NUM = re.compile(r"^\d+$")
_IMG_EXT = re.compile(r"\.(tga|png|jpg|jpeg|bmp|dds)$", re.I)


@dataclass(frozen=True)
class Match:
    """One candidate texture for a mesh.

    ``texture``    logical asset path, e.g. ``c3/texture/105000000.dds``.
                   Always a path that exists (loose or archived) at index
                   build time -- candidates that do not resolve are dropped.
    ``method``     a key of :data:`METHODS`.
    ``confidence`` 0..1, the ranking key.  See :data:`METHODS`.
    ``kind``       "authored" or "inferred".
    ``detail``     free text naming the exact table row / rule instance.
    """
    texture: str
    method: str
    confidence: float
    kind: str
    detail: str = ""
    #: How many distinct textures the SAME rule produced for this mesh.
    #: 1 is a definite answer; more means the rule cannot tell them apart
    #: and `confidence` has been divided accordingly -- see `matches`.
    alternatives: int = 1

    @property
    def ambiguous(self) -> bool:
        return self.alternatives > 1

    def as_dict(self) -> dict:
        return {"texture": self.texture, "method": self.method,
                "confidence": self.confidence, "kind": self.kind,
                "detail": self.detail, "alternatives": self.alternatives,
                "ambiguous": self.ambiguous}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _norm(p: str) -> str:
    return p.replace("\\", "/").strip().lstrip("/").lower()


def _loose_files(root) -> set[str]:
    """Every regular file under `root`, as `_norm`ed paths relative to it."""
    top = os.path.join(str(root), "")          # exactly one trailing separator
    n = len(top)
    out: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(top):
        rel = dirpath[n:]
        for fn in filenames:
            out.add(_norm(os.path.join(rel, fn) if rel else fn))
    return out


def pooled_names() -> tuple[set[str], bool]:
    """The recovered WDF filenames, normalised -- ``(names, any_table_read)``.

    **GLOBAL, not per-base.**  The TQ hash is a pure function of the filename
    string, so a recovered name is true in *any* install; `coroot.GLOBAL`
    says so for `out/wdf/` and `out/dll/wdf_name_recovery` and this reads
    them through `find_derived` accordingly.  A name here is not a promise
    that a given install ships the bytes -- see `_build_universe`.

    Lifted out of `_build_universe` so that a caller which needs the *size*
    of the asset universe can compute it without constructing an index and
    paying its `rglob` (36.8 s on Clients/7878).  `health.thumbnail_corpus`
    is that caller, and the alternative -- restating this loading beside it
    -- is the drift `tests/test_health_thumbs.py` exists to make impossible.
    """
    names: set[str] = set()
    loaded = False
    for fn in ("out/wdf/c3_names.json", "out/wdf/data_names.json"):
        f = coroot.find_derived(fn)
        if f is not None:
            names.update(_norm(v) for v in
                         json.loads(f.read_text("utf-8")).values())
            loaded = True
    f = coroot.find_derived("out/dll/wdf_name_recovery.json")
    if f is not None:
        try:
            names.update(_norm(v) for v in
                         json.loads(f.read_text("utf-8"))["resolved"].values())
            loaded = True
        except Exception:
            pass
    return names, loaded


def _flat_ini(path: Path) -> dict[str, str]:
    """`id=path` tables (3dobj.ini, 3dtexture.ini, 3DEffectObj.ini, *motion.ini).

    These are not sectioned inis -- they are one flat `key=value` list, often
    with blank lines and trailing tabs.  VERIFIED by inspection of all six.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text("latin1", errors="replace").splitlines():
        line = line.strip()
        if not line or line[0] in ";#[" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = _norm(v)
        if k and v:
            out[k] = v
    return out


def _id_forms(ident: str) -> list[str]:
    out = []
    for c in (ident, ident.zfill(9), ident.lstrip("0")):
        if c and c not in out:
            out.append(c)
    return out


def _decode_label(raw: bytes) -> str:
    """Decode a PHY label.  These are 3DSMax source paths, mostly GBK Chinese.

    docs/modding.md 10.1 note 3 warns the raw bytes must never be round-tripped
    through a codec; this is display/keying only, never written back.
    """
    for enc in ("ascii", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

class MeshTextureIndex:
    """Mesh -> ranked texture candidates.

    Construction walks the install tree and the recovered WDF name table to
    build the asset universe (~1 s), then loads the ini tables lazily on first
    query.  Safe to keep alive; holds the two WDF archives open.
    """

    #: The ini tables that map an appearance id to Mesh<i>/Texture<i>.  Loaded
    #: through RolePart.ini (VERIFIED -- see docs/modding.md section 3).
    #: `Mount.ini` is reached that way too (RolePart part name "mount").

    #: (motion table, digits of the key that are the *action*, part table,
    #:  suffix appended to the remaining prefix to form an appearance id).
    #: VERIFIED against 3dmotion.ini: key 105000100 -> c3/monster/105/100.c3,
    #: i.e. key = <bodytype><3-digit weapon set><3-digit action>, and the
    #: appearance id of a monster/npc body type is <bodytype>000000.
    MOTION_TABLES = (
        ("3dmotion.ini", 6, "body", "000000"),
        ("WeaponMotion.ini", 3, "l_weapon", ""),
        ("MountMotion.ini", 3, "mount", ""),
        ("miscmotion.ini", 3, "misc", ""),
    )

    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        #: Where THIS index's census lives -- resolved from **this index's
        #: own root**, not the configured one. It used to be a class
        #: attribute evaluated at import, which meant every instance in a
        #: process shared one path no matter which install it was built for,
        #: and `scan_meshes` wrote through it. Two bases in one process
        #: therefore overwrote each other's census: measured, 6090's 18,020
        #: meshes landed in `patch5517-.../mesh_index.json`, were rejected by
        #: `_cache_covers` on the next 5517 read, rebuilt, and thrashed back.
        #: `_cache_covers` only caught it because the two clients differ in
        #: size; at comparable sizes it is accepted in silence.
        self.CACHE = coroot.derived_path("out/meshtex/mesh_index.json", self.root)
        self.ini = self.root / "ini"
        #: ini tables this install does not ship, in the order they were
        #: asked for.  Filled by `_sections`; reported once by `_load_tables`.
        self.missing_tables: list[str] = []
        self.assets = assets or AssetRoot(self.root)
        self._owns_assets = assets is None
        self.universe: set[str] = set()
        self.textures: set[str] = set()
        self.c3_files: set[str] = set()
        self._tex_by_dir: dict[str, list[str]] = collections.defaultdict(list)
        self._build_universe()
        for t in self.textures:
            self._tex_by_dir[t.rsplit("/", 1)[0]].append(t)
        for v in self._tex_by_dir.values():
            v.sort()
        self._tables_loaded = False
        self._mesh_index: Optional[dict[str, dict]] = None

    # -- universe ----------------------------------------------------------
    def _build_universe(self) -> None:
        """Every asset path the client can open: loose files plus the archive
        entries whose name the earlier workstream recovered (24,426 of 24,757,
        98.7% -- see docs/assets.md section 2.3).

        The recovered-name tables are **global** -- one set of names pooled
        from every install anyone has ever unpacked -- and this is the only
        input here that is not already per-base.  A name in that pool is not
        a promise that *this* install ships the bytes; 774 of the 24,426 are
        not resolvable against 5517's archives at all, 210 of them ``.c3``.
        Folding the pool in unfiltered is what made a per-base census get
        measured against a global universe, and it cost a permanently
        rejected cache (``_cache_covers``, ``C-2026-08-10-quickfix-gap210``).

        So the pooled names are kept, but *marked*.  ``_global_only`` is the
        subset that neither a loose file nor a CONTAINER-DECLARED path backs,
        i.e. exactly the names whose presence this install has not yet been
        asked to confirm.  A declared path is not among them: a `.tpi` stores
        the path, so the install itself is what named it.  Callers
        that need a per-base answer resolve them through ``_in_this_install``
        -- one TQ hash into this install's own WDF index, which is the same
        decisive check ``exists`` already falls through to.

        The marking is deliberately not a filter here.  Confirming all 24,426
        costs 8.2 s (measured, 5517), and most constructions of this class
        never ask a coverage question; ``_cache_covers`` only ever needs the
        few hundred that a cache actually misses.  Pay it where it is owed.
        """
        # `os.walk`, not `rglob("*") + is_file()` -- the third site of the
        # substitution `coassets` made twice (see `_c3_names`). rglob asks
        # the OS again, one stat per entry, for what the directory listing
        # already said. MEASURED 2026-09-18 (loadharness, box exclusive, walk
        # alone, median of 3): 7878 8.31 s -> 0.69 s of a 12.64 s index
        # build; 6609 4.91 -> 0.30 of 7.02; 6090 2.41 -> 0.15 of 3.34.
        #
        # The SET MUST NOT CHANGE: a smaller universe here is a wrong answer,
        # not a fast one. `tests/test_meshtex_walk.py` pins the two
        # constructions equal, path by path, on every installed client.
        loose = _loose_files(self.root)
        self.universe.update(loose)
        # What the install's own containers DECLARE. A `.tpd`/`.tpi` pair
        # stores the path, so these need no recovered-name table and are not
        # "unconfirmed" in the sense `_global_only` means -- this install is
        # the thing that named them. `AssetRoot.locate` has always preferred
        # a declared path to a hash; enumerating them is what was missing,
        # and on a TPD client it is almost the whole corpus: Zephyr offered
        # 324 loose `.c3` against a census of 31,421 until this line existed.
        try:
            declared = {_norm(n) for n in self.assets.container_names()}
        except Exception:
            # A container that cannot be enumerated must not take the index
            # down with it; the recovered tables below are still true.
            declared = set()
        self.universe.update(declared)
        #: Kept because `scan_meshes` asks a different question of it than
        #: the universe does: whether this install's corpus is COMPLETE
        #: without any recovered table, which is what decides the cache.
        self._declared = declared
        pooled, self.names_loaded = pooled_names()
        self.universe.update(pooled)
        #: Pooled names with no loose file behind them -- unconfirmed against
        #: this install until `_in_this_install` says otherwise.
        self._global_only = pooled - loose - declared
        self.textures = {p for p in self.universe if p.endswith(".dds")}
        self.c3_files = {p for p in self.universe if p.endswith(".c3")}
        self._exists_cache: dict[str, bool] = {}

    def _in_this_install(self, p: str) -> bool:
        """Does *this* install's archive index resolve `p`?

        Hashes the path with the real TQ hash and looks it up -- the same
        stage-two check `exists` uses, and the only one that distinguishes
        "this install ships it" from "somebody's install shipped it once".
        Shares `_exists_cache`, so a path asked about twice costs once.
        """
        hit = self._exists_cache.get(p)
        if hit is None:
            try:
                hit = self.assets.locate(p) is not None
            except Exception:
                hit = False
            self._exists_cache[p] = hit
        return hit

    def claim_by_content(self) -> tuple[set[str], dict[str, int]]:
        """`.dds`-NAMED files whose bytes are a C3 mesh, and how many could
        not be claimed.

        TQ shelved some animated meshes under texture names -- `MAXFILE C3
        00001`, PHY+MOTI, one with CAME -- and the texture pass then failed on
        them with UnidentifiedImageError. Confirmed by content out of Zephyr's
        containers: at least EIGHT, not the four first found, and one of them
        is `c3/effect/flash/2996158.dds`, which is NOT under `c3/texture/`.
        **So the only rule that works is BY CONTENT**: a rule keyed on the
        texture shelf misses that file, and the shelf is not even
        consistently the wrong shelf.

        Classified with `wdf.detect_magic` over `AssetRoot.peek` -- the
        EXISTING classifier and the containers' existing `peek`, so there is
        no third definition of "what is this file" to drift from the other
        two.

        Returns the claimed paths and a count of C3-by-content entries this
        index could NOT claim, because a WDF index stores only a hash and a
        path that no recovered table names cannot be offered to anything.
        Those are counted, not dropped silently -- the same shape as
        `in_scope_textures`' withheld count.

        Called from `scan_meshes`, not `__init__`: every tool builds an
        index, only the mesh census needs this, so this is where it is owed.
        """
        claimed: set[str] = set()
        for p in self.textures:
            if p in self._global_only and not self._resolvable_unconfirmed(p):
                continue                 # not in this install; nothing to peek
            try:
                head = self.assets.peek(p, 16)
            except (OSError, KeyError, ValueError):
                continue
            if detect_magic(head)[1] == ".c3":
                claimed.add(p)
        # C3 content in a hash-only WDF whose hash NO path in this universe
        # produces: present in this install, and unclaimable, because there is
        # no name to offer it under. Counted so it is not invisible.
        from tqhash import tq_hash                      # noqa: PLC0415
        known = {tq_hash(p) for p in self.universe}
        unnamed = 0
        for arc in getattr(self.assets, "_archives", {}).values():
            if getattr(arc, "names", None) is not None:
                continue                 # a TPD stores every entry's path
            for e in getattr(arc, "entries", []):
                if (detect_magic(arc.peek(e, 16))[1] == ".c3"
                        and e.hash not in known):
                    unnamed += 1
        return claimed, {"claimed": len(claimed), "wdf_unnamed": unnamed}

    def _resolvable_unconfirmed(self, p: str) -> bool:
        """Is `p` -- known to be in `_global_only` -- actually in this install?

        `_global_only` is the pooled names with no loose file and no
        container declaration behind them, so `locate`'s loose-file stat
        cannot succeed for one of them and costs 0.32 ms against
        `in_archives`' 0.01 ms (measured, 2,000 names on 5017).

        **The overlay is the exception and it is why this is not simply
        `in_archives`.** An overlay supplies paths no archive holds -- that
        is its entire purpose -- so an install with one configured falls back
        to the full `locate`. Fast where it is safe, correct where it is not.
        """
        if self.assets.overlays:
            return self._in_this_install(p)
        hit = self._exists_cache.get(p)
        if hit is None:
            try:
                hit = self.assets.in_archives(p)
            except Exception:
                hit = False
            self._exists_cache[p] = hit
        return hit

    def in_scope_c3(self) -> set[str]:
        """`c3_files` restricted to what this install can actually open.

        The per-base universe, resolved.  Costs one hash per unconfirmed
        pooled name (8.2 s on 5517) -- call it when the whole set is needed,
        which is a full rescan; a coverage check should filter its misses
        with `_in_this_install` instead of building this.
        """
        return {p for p in self.c3_files
                if p not in self._global_only or self._in_this_install(p)}

    def in_scope_textures(self) -> tuple[set[str], dict[str, int]]:
        """`textures` restricted to what this install can actually open,
        with a COUNT OF WHAT WAS DROPPED AND WHY.

        The mesh side has had `in_scope_c3` since it was written; the texture
        side never asked, and `thumbs.texture_universe` returned
        `self.textures` -- the raw union -- straight to the renderer.
        `pooled_names()` is GLOBAL, "one set of names pooled from every
        install anyone has ever unpacked", so the work list offered every
        recovered `.dds` name on the box to whichever client was selected and
        the renderer discovered the absence one file at a time: **7,507 of
        142,435 on 7878, 848 on CCO** in the nine-client matrix, every
        sampled one "path does not exist".

        Returns the dropped paths BY REASON rather than a single number,
        because "the corpus cannot read 7,507 paths" and "7,507 paths were
        never in this install to begin with" are different problems and only
        the second one is benign:

            pooled_not_here    a GLOBAL recovered name this install's archive
                               index does not resolve, and nothing in this
                               install asks for it. Somebody else's client
                               shipped it; benign, and expected to be the
                               bulk.
            table_named_absent the same, EXCEPT that one of this install's
                               OWN tables names it (`3dtexture.ini` and
                               friends, via `tex_paths`). Not benign: the
                               client says it needs a file it does not ship,
                               so an asset will render untextured in the
                               game and not only in our previews.

        A third bucket, "referenced by a table and present", cannot appear
        here: such a path resolves and is kept. And there is no "other" --
        a dropped path is by construction one the pool offered and this
        install does not resolve, so the two above partition the drop
        exactly. Stated rather than left implicit, because a bucket that
        cannot be non-zero is not a measurement.

        The cost this pays is the one `_build_universe` deliberately deferred
        ("Pay it where it is owed"): one hash and one index lookup per
        unconfirmed name, shared through `_exists_cache`. A thumbnail run is
        exactly where it is owed -- it is about to open every one of these
        paths anyway, and failing at `Image.open` costs more than failing at
        a dict lookup.
        """
        # `3dtexture.ini` DIRECTLY rather than `_load_tables()`, which builds
        # the appearance, npc, motion and effect tables too. MEASURED: the
        # full load dominated this call at ~19 s against the confirmation's
        # ~1 s, to populate one dict this needs and four it does not. Same
        # values -- `_load_tables` builds `tex_paths` from this very file.
        wanted = {_norm(v) for v in _flat_ini(self.ini / "3dtexture.ini").values()}
        keep: set[str] = set()
        why: dict[str, int] = {"pooled_not_here": 0, "table_named_absent": 0}
        for p in self.textures:
            if p not in self._global_only:
                # Backed by a loose file or declared by a container: this
                # install named it, so it is in scope without a lookup.
                keep.add(p)
                continue
            if self._resolvable_unconfirmed(p):
                keep.add(p)
            elif p in wanted:
                why["table_named_absent"] += 1
            else:
                why["pooled_not_here"] += 1
        return keep, why

    def exists(self, logical: str) -> bool:
        """Does the client have this asset?

        Two-stage, and the second stage matters.  The enumerable universe is
        loose files plus the *recovered* archive names, and name recovery is
        98.7% -- 331 archive entries have no known name.  So a miss there
        falls through to ``AssetRoot.locate``, which hashes the candidate path
        with the real TQ hash and looks it up in the WDF index directly.  That
        answers "does `c3/texture/223000000.dds` exist" decisively even though
        nobody ever recovered that name.

        This is worth 4 extra textures on the residue alone, and it is the
        difference between "not shipped" and "we never learned its name".
        """
        p = _norm(logical)
        if p in self.universe:
            return True
        hit = self._exists_cache.get(p)
        if hit is None:
            try:
                hit = self.assets.locate(p) is not None
            except Exception:
                hit = False
            self._exists_cache[p] = hit
            if hit:
                self.universe.add(p)
                if p.endswith(".dds"):
                    self.textures.add(p)
                    self._tex_by_dir[p.rsplit("/", 1)[0]].append(p)
                elif p.endswith(".c3"):
                    self.c3_files.add(p)
        return hit

    def _tex(self, logical: Optional[str]) -> Optional[str]:
        """A texture path, but only if it really exists."""
        if not logical:
            return None
        p = _norm(logical)
        return p if self.exists(p) else None

    def _tex_from_id(self, ident: str) -> list[str]:
        """Bare numeric texture id -> every existing file it could name.

        INFERRED, and it is the same rule `coassets.resolve_asset` uses:
        probe c3/<dir>/<id>.dds over TEX_DIRS with the id as written,
        zero-padded to 9 and stripped of leading zeros.
        """
        out: list[str] = []
        if not ident or ident == "0":
            return out
        for sub in TEX_DIRS:
            for c in _id_forms(ident):
                p = f"c3/{sub}/{c}.dds"
                if p not in out and self.exists(p):
                    out.append(p)
        return out

    # -- ini tables --------------------------------------------------------
    def _load_tables(self) -> None:
        if self._tables_loaded:
            return
        self._tables_loaded = True
        ini = self.ini

        # id -> path tables
        self.obj_paths: dict[str, str] = {}
        for n in ("3dobj.ini", "3DEffectObj.ini"):
            for k, v in _flat_ini(ini / n).items():
                self.obj_paths.setdefault(k, v)
        self.tex_paths: dict[str, str] = {
            k: v for k, v in _flat_ini(ini / "3dtexture.ini").items()}
        self._path_obj_ids: dict[str, list[str]] = collections.defaultdict(list)
        for k, v in self.obj_paths.items():
            self._path_obj_ids[v].append(k)

        # appearance tables (armor/weapon/armet/mount/misc/head), via RolePart
        try:
            self.parts = self.assets.part_tables()
        except Exception:
            self.parts = {}

        # --- rule tables, all mesh-path -> [(texture, detail)] -------------
        self._effect: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self._simple: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self._npcj: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self._appear: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self._motion: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
        self._role: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)

        self._build_effect_table()
        self._build_simpleobj_table()
        self._build_npc_table()
        self._build_simplerole_table()
        self._build_appearance_table()
        self._build_motion_table()
        if self.missing_tables:
            print(f"[meshtex] {len(self.missing_tables)} ini table(s) this "
                  f"install does not ship, so their pairings are absent "
                  f"rather than empty: {', '.join(self.missing_tables)}",
                  file=sys.stderr, flush=True)

        #: .c3 files grouped by directory, for the dir_consensus rule.
        self._c3_by_dir: dict[str, list[str]] = collections.defaultdict(list)
        for p in self.c3_files:
            self._c3_by_dir[p.rsplit("/", 1)[0]].append(p)

    def _add(self, d: dict, mesh: Optional[str], tex: Optional[str], detail: str) -> None:
        if not mesh or not tex:
            return
        mesh = _norm(mesh)
        if mesh not in self.c3_files:
            return
        for t, _ in d[mesh]:
            if t == tex:
                return
        d[mesh].append((tex, detail))

    def _sections(self, name: str, *, allow_stale: bool = False
                  ) -> dict[str, dict[str, str]]:
        """`parse_ini` for a table an install may legitimately not ship.

        `_flat_ini` has returned `{}` for an absent file since it was
        written; the SECTIONED reader did not, and the difference was not a
        style one.  A client missing one of these tables did not lose that
        table's rule -- `parse_ini` raised FileNotFoundError out of
        `_load_tables`, so the install built NOTHING.  Measured on Zephyr
        2026-09-18: 6 of the 10 ini tables this module names are absent
        there, 4 of them behind `_flat_ini` (silent, correct) and 2 behind
        this reader (fatal).  Zephyr produced 0 of its 31,421 meshes for
        want of two files that only ever contributed mesh<->texture
        PAIRINGS, never corpus.

        An absence is recorded rather than swallowed: `self.missing_tables`
        is printed once by `_load_tables`, because "this install has no
        effect table" and "this install's effect table is empty" are
        different facts and the second one is the one that hides.
        """
        p = self.ini / name
        if not p.is_file():
            if name not in self.missing_tables:
                self.missing_tables.append(name)
            return {}
        return parse_ini(p, allow_stale=allow_stale)

    def _build_effect_table(self) -> None:
        """ini/3DEffect.ini: per effect, `Amount=N` parts each with
        `EffectId<i>` (-> 3DEffectObj.ini, a .c3) and `TextureId<i>`
        (-> 3dtexture.ini, a .dds).  VERIFIED: both id spaces resolve to
        explicit paths, 2,314/2,351 and 7,785/7,929 of which exist here."""
        # **RETRACTED REASON:** this note used to say "there is no EFFE reader
        # yet". There is -- `core/dbc.py::read_effe` (C44), and
        # `tools/effects.py::EffectDB` reads the compiled table on its
        # definitions path (C47). meshtex's own effect figures are therefore
        # the only plaintext-lineage ones left; docs/effects.md §7a/§9a are not.
        # STALE-INI: 3DEffect.dbc (magic EFFE) shadows this from 5517 on, and
        # the read stays plaintext for the same reason 3DSimpleObj does below:
        # switching moves *this tool's* coverage index on 5517/6090, and
        # rebuilding those indexes is meshtex's owner's call rather than a
        # passing session's (C21).
        for sec, kv in self._sections("3DEffect.ini",
                                      allow_stale=True).items():
            try:
                n = int(kv.get("Amount", "0") or 0)
            except ValueError:
                continue
            for i in range(n):
                e = kv.get(f"EffectId{i}")
                t = kv.get(f"TextureId{i}")
                if not e or not t:
                    continue
                self._add(self._effect, self.obj_paths.get(e),
                          self._tex(self.tex_paths.get(t)),
                          f"3DEffect.ini [{sec}] EffectId{i}={e} TextureId{i}={t}")

    def _build_simpleobj_table(self) -> None:
        """ini/3DSimpleObj.ini: `[ObjIDType<n>] PartAmount=N`, then `Part<i>`
        (-> 3dobj.ini) and `Texture<i>` (-> 3dtexture.ini).  Same two id
        spaces as 3DEffect.ini.  VERIFIED by inspection; 137 sections."""
        # STALE-INI: 3DSimpleObj.dbc (magic SIMO) shadows this from 5517 on
        # and `dbc.read_simo` can already read it. Switching would move the
        # meshtex coverage numbers on 5517/6090, and rebuilding those indexes
        # is not a gate session's call (C21 -- a coverage index that changes
        # under other sessions is exactly what caused the cross-base leak).
        # Declared here; conversion is listed in docs/gamedata.md.
        for sec, kv in self._sections("3DSimpleObj.ini",
                                      allow_stale=True).items():
            try:
                n = int(kv.get("PartAmount", "0") or 0)
            except ValueError:
                continue
            for i in range(n):
                e = kv.get(f"Part{i}")
                t = kv.get(f"Texture{i}")
                if not e or not t:
                    continue
                self._add(self._simple, self.obj_paths.get(e),
                          self._tex(self.tex_paths.get(t)),
                          f"3DSimpleObj.ini [{sec}] Part{i}={e} Texture{i}={t}")

    def _build_npc_table(self) -> None:
        """An NPC's own texture, attached to every mesh it animates with.

        The join is `npc table -> simple_object -> 3DSimpleObj.ini` for the
        texture, and the row's three motion ids for the `.c3` files that NPC
        plays.  436 of 437 CCO rows resolve to at least one real texture.
        VERIFIED join; both halves are authored.

        **Routed through `core/npcart.py` rather than parsing a table here.**
        The community client ships `ini/npc.json`; every official client
        ships a sectioned `ini/npc.ini` whose motion ids are **ten** digits
        wide against the json's nine, and whose `simple_object` is a
        zero-padded string against the json's int.  Reading only the json
        left this rule dead on every official client.

        That mattered more than a missing rule usually does. `npc_table` is
        what discovers that a shared mesh has *several* candidate skins --
        `c3/npc/999001100.c3` is the standby motion of thirteen NPCs -- so
        with it dead the mesh fell through to an `inferred` 0.73 answer and
        was reported as **definite**, `ambiguous=False`. A shared asset
        silently acquiring one confident texture is the exact failure this
        rule exists to prevent, so the gap did not merely lose data: it
        inverted the finding, on the mesh the whole scoring design was built
        around.

        `npcart` normalises both forms to one row shape and resolves the id
        widths, so this imports it rather than re-deriving it -- the same
        call the class's own `npcart` test already makes.
        """
        try:
            import npcart                                # noqa: PLC0415
            # ASK THE PLUGIN rather than let the tables probe for themselves.
            # This call matters more than the other three: what it builds is
            # the mesh->texture index most of the app reads, so a profile
            # chosen wrongly here does not fail here -- it propagates, which
            # is the C21 shape. `plugin_for` is the rule the viewer already
            # uses: a stored declaration outranks detection, because a
            # private-server repack of 6090 reads as 6090 to any test of the
            # bytes. Measured 2026-08-09: plugin and probe agree on all six
            # declared installs, so no answer moves today.
            #
            # ASK `self.assets`, NOT `self.root`. `assets` may be a
            # `colibrary.ServerView`, whose `.root` is the **baseline** it
            # composes over rather than the client it shows, so resolving from
            # the root asks the wrong install
            # (`docs/CORRECTIONS.md` C-2026-08-09-plugin-c-serverview-profile).
            # `table_profile_for` is the single definition of that rule and
            # falls back to `plugin_for(root)` for a bare install, which is
            # every caller today: **MEASURED 2026-08-09, no caller anywhere
            # passes `assets=`** (`meshtex:18`, `meshtex:1242`,
            # `thumbs:739/754/1276/1381`, `unify:228`, `test_viewer:8932` all
            # construct from a root), so `self.assets` is unconditionally an
            # `AssetRoot` and this changes no answer here TODAY. It is wired
            # because the parameter exists and must be honoured the first time
            # somebody uses it -- this index is the one that propagates.
            #
            # The flip counts and the STABLE-not-CORRECT scope live in
            # `table_profile_for`'s docstring rather than being copied here:
            # four call sites resting on one measurement is exactly how three
            # of the copies go stale (the `_DBC_CENSUS` lesson). The two sites
            # a `ServerView` can actually reach -- `coviewer` and `artcrawl` --
            # state them inline because that is where the answer is shown or
            # persisted.
            prof = None
            try:
                from assetdiff import table_profile_for   # noqa: PLC0415
                prof, _rep = table_profile_for(self.assets, self.root)
            except Exception:                            # pragma: no cover
                prof = None
            tables = npcart.Tables(self.assets.read, prof)
        except Exception:
            return
        source = getattr(tables.profile, "npc_table", "npc table")
        for row in tables.npcs:
            try:
                plan = tables.plan_for_npc(row)
            except Exception:
                continue
            texes = [t for t in
                     ([self._tex(plan.texture)]
                      + [self._tex(x) for _, x in plan.extra_parts])
                     if t]
            if not texes:
                continue
            name = row.get("name", "")
            for role, path in plan.motions.items():
                for t in texes:
                    self._add(self._npcj, path, t,
                              f"{source} type={row.get('type')} ({name}) "
                              f"simple_object={row.get('simple_object')} via {role}")

    def _build_simplerole_table(self) -> None:
        """ini/3DsimpleRole.ini: the 24 character-creation preview roles, each
        naming a `3DSimpleObjID` (-> 3DSimpleObj.ini, giving the texture) and
        two motion ids that are bare mesh ids in 3dobj.ini / c3/mesh/.
        VERIFIED by inspection; this is what backs c3/mesh/99988*.c3."""
        roles = self._sections("3DsimpleRole.ini")
        if not roles:
            return
        # STALE-INI: same SIMO twin as _build_simpleobj_table above.
        simple = self._sections("3DSimpleObj.ini", allow_stale=True)
        motion = _flat_ini(self.ini / "3dmotion.ini")
        for sec, kv in roles.items():
            obj = simple.get(f"ObjIDType{kv.get('3DSimpleObjID')}")
            if not obj:
                continue
            try:
                n = int(obj.get("PartAmount", "0") or 0)
            except ValueError:
                continue
            texes = [t for t in (self._tex(self.tex_paths.get(obj.get(f"Texture{i}", "")))
                                 for i in range(n)) if t]
            for key in ("3DStandByMotion", "3DBlazeMotion"):
                mid = kv.get(key)
                if not mid:
                    continue
                for cand in (motion.get(mid), self.obj_paths.get(mid),
                             f"c3/mesh/{mid}.c3"):
                    if cand and _norm(cand) in self.c3_files:
                        for t in texes:
                            self._add(self._role, cand, t,
                                      f"3DsimpleRole.ini [{sec}] {key}={mid} "
                                      f"3DSimpleObjID={kv.get('3DSimpleObjID')}")
                        break

    def _build_appearance_table(self) -> None:
        """The RolePart.ini part tables (armor/weapon/armet/armet1/Mount/misc/
        head).  VERIFIED structure; the bare-id -> file step is INFERRED (see
        docs/modding.md section 3, 94/98/79% reproduction)."""
        for part, ini in self.parts.items():
            for app in ini:
                for pr in app.parts:
                    if not pr.mesh or pr.mesh == "0":
                        continue
                    meshes = []
                    for sub in ("mesh", "weapon", "body", "hair", "mount", "npc", "monster"):
                        for c in _id_forms(pr.mesh):
                            p = f"c3/{sub}/{c}.c3"
                            if p in self.c3_files and p not in meshes:
                                meshes.append(p)
                    if not meshes:
                        continue
                    for tid in (pr.texture, pr.mix_tex, pr.third_tex, pr.fourth_tex):
                        if not tid or tid == "0":
                            continue
                        for t in self._tex_from_id(tid):
                            for m in meshes:
                                self._add(self._appear, m, t,
                                          f"{ini.name} [{app.ident}] "
                                          f"Mesh{pr.index}={pr.mesh} "
                                          f"Texture{pr.index}={tid}")

    def _build_motion_table(self) -> None:
        """Motion tables name the .c3 that plays a motion; the motion id
        encodes the appearance the motion belongs to, and that appearance's
        row in the part table names the texture.

        VERIFIED key layout for 3dmotion.ini: `<bodytype><3-digit weapon
        set><3-digit action>`.  105000100 -> c3/monster/105/100.c3,
        1000001 -> c3/0001/000/001.c3.  The matching appearance id is
        `<bodytype>000000` zero-padded to 9, which is exactly the 185
        `NNN000000` monster/NPC body rows documented in
        docs/appearance_ids.md section 1.1.  WeaponMotion.ini and
        MountMotion.ini strip only the 3-digit action.

        Note this legitimately gives a mesh SEVERAL textures: body types 120
        and 122 both animate through c3/monster/120/*.c3 but carry different
        skins.  That is the same "one mesh, many textures" pattern the
        equipment tables show.
        """
        for name, strip, part, suffix in self.MOTION_TABLES:
            table = _flat_ini(self.ini / name)
            ini = self.parts.get(part)
            if not ini:
                continue
            for key, path in table.items():
                if path not in self.c3_files or len(key) <= strip:
                    continue
                app = ini.get((key[:-strip] + suffix).zfill(9))
                if not app:
                    continue
                for pr in app.parts:
                    for tid in (pr.texture, pr.mix_tex):
                        if not tid or tid == "0":
                            continue
                        for t in self._tex_from_id(tid):
                            self._add(self._motion, path, t,
                                      f"{name} {key} -> {ini.name} "
                                      f"[{app.ident}] Texture{pr.index}={tid}")

    # -- inferred rules ----------------------------------------------------
    def _rule_stem_pair(self, mesh: str) -> list[tuple[str, str]]:
        stem = mesh.rsplit("/", 1)[-1][:-3]
        own = mesh.rsplit("/", 1)[0]
        out = []
        cands = [f"{own}/{stem}.dds"]
        if _NUM.match(stem):
            cands += [f"c3/texture/{c}.dds" for c in _id_forms(stem)]
        else:
            cands.append(f"c3/texture/{stem}.dds")
        for c in cands:
            if c not in [x for x, _ in out] and self.exists(c):
                out.append((c, f"stem '{stem}'"))
        return out

    def _rule_objtex_id(self, mesh: str) -> list[tuple[str, str]]:
        out = []
        for oid in self._path_obj_ids.get(mesh, ()):
            t = self._tex(self.tex_paths.get(oid))
            if t and t not in [x for x, _ in out]:
                out.append((t, f"3dobj/3dtexture id {oid}"))
        return out

    def _rule_label_sibling(self, mesh: str) -> list[tuple[str, str]]:
        own = mesh.rsplit("/", 1)[0]
        out: list[tuple[str, str]] = []
        for lab in self.labels_of(mesh):
            base = _IMG_EXT.sub("", _norm(lab).rsplit("/", 1)[-1])
            if not base:
                continue
            for c in (f"{own}/{base}.dds", f"c3/texture/{base}.dds"):
                if c not in [x for x, _ in out] and self.exists(c):
                    out.append((c, f"PHY label {lab!r}"))
        return out

    def _rule_bodytype_dir(self, mesh: str) -> list[tuple[str, str]]:
        parts = mesh.split("/")
        if len(parts) != 4 or parts[0] != "c3" or parts[1] not in _BODYTYPE_DIRS:
            return []
        # dir names are sometimes suffixed ("104n"); the body type is the digits
        num = re.sub(r"\D", "", parts[2])
        out = []
        if num:
            for c in (f"c3/texture/{num}000000.dds", f"c3/{parts[1]}/{parts[2]}/1.dds"):
                if c not in [x for x, _ in out] and self.exists(c):
                    out.append((c, f"body type {num} from directory '{parts[2]}'"))
        return out

    def _rule_npc_look_id(self, mesh: str) -> list[tuple[str, str]]:
        """`c3/npc/999<look><action>.c3` -> `c3/texture/999<look>*.dds`.

        INFERRED but strongly evidenced: on the 63 such files that npc.json +
        3DSimpleObj.ini pin down independently, this reproduces the authored
        texture 97% of the time (2 misses, both NPC look 011, which is skinned
        from c3/npc/029/1.dds instead).

        The zero-padding of the id is not consistent in the shipped data --
        look 265 is texture 9992650, look 118 is texture 9990118 -- so all six
        forms are probed and the first that exists wins.  Most of these names
        were never recovered from the WDF hash table, so this rule only works
        because `exists()` falls back to hashing the candidate directly.
        """
        stem = mesh.rsplit("/", 1)[-1][:-3]
        if not mesh.startswith("c3/npc/999") or len(stem) != 9 or not stem.isdigit():
            return []
        look = stem[3:6]
        short = look.lstrip("0") or "0"
        out: list[tuple[str, str]] = []
        for c in (f"999{look}0", f"9990{look}", f"999{short}0",
                  f"9990{short}", f"999{look}", f"999{short}"):
            p = f"c3/texture/{c}.dds"
            if p not in [x for x, _ in out] and self.exists(p):
                out.append((p, f"NPC look {look} from filename"))
        return out

    def _authored_only(self, mesh: str) -> list[tuple[str, str]]:
        return (self._effect.get(mesh, []) + self._simple.get(mesh, [])
                + self._npcj.get(mesh, []) + self._role.get(mesh, [])
                + self._appear.get(mesh, []) + self._motion.get(mesh, []))

    #: dir_consensus guards.  A directory only speaks for its contents if it
    #: is a genuine per-creature/per-effect grouping.  `c3/monster/127/` is
    #: (12 files, 3 authored textures between them); `c3/npc/` is not (125
    #: unrelated flat files whose authored matches span dozens of textures),
    #: and firing there would manufacture matches out of nothing.
    CONSENSUS_MAX_FILES = 64
    CONSENSUS_MAX_TEXTURES = 6

    def _rule_dir_consensus(self, mesh: str) -> list[tuple[str, str]]:
        """Inherit the *authored* matches of the other `.c3` files sitting in
        the same directory.

        The directory layout is per-creature -- `c3/monster/127/` is one
        monster's whole animation set, and every file in it carries the same
        3DSMax source-texture label -- so a texture an authored table pins to
        one file in that directory applies to its siblings.

        Only fires when (a) the mesh has no authored match of its own, (b) the
        directory holds at most CONSENSUS_MAX_FILES `.c3` files, and (c) its
        siblings' authored matches agree to within CONSENSUS_MAX_TEXTURES
        distinct textures.  Without (b)/(c) this rule degenerates into
        "anything in the neighbourhood", which is worse than no answer.
        """
        own = mesh.rsplit("/", 1)[0]
        sibs = self._c3_by_dir.get(own, ())
        if self._authored_only(mesh) or not sibs or len(sibs) > self.CONSENSUS_MAX_FILES:
            return []
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for sib in sorted(sibs):
            if sib == mesh:
                continue
            for t, _d in self._authored_only(sib):
                if t not in seen:
                    seen.add(t)
                    out.append((t, f"authored match of sibling {sib}"))
        if len(out) > self.CONSENSUS_MAX_TEXTURES:
            return []
        return out

    def _dir_consensus_holdout(self, mesh: str) -> list[tuple[str, str]]:
        """`_rule_dir_consensus` as if `mesh` had no authored match of its own.

        Used only by :func:`measure_precision`: the live rule deliberately
        stays silent whenever an authored answer exists, which would leave it
        with an empty scoring sample.  This variant hides the mesh's own
        authored row so the rule can be graded against it.
        """
        own = mesh.rsplit("/", 1)[0]
        sibs = self._c3_by_dir.get(own, ())
        if not sibs or len(sibs) > self.CONSENSUS_MAX_FILES:
            return []
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for sib in sorted(sibs):
            if sib == mesh:
                continue
            for t, _d in self._authored_only(sib):
                if t not in seen:
                    seen.add(t)
                    out.append((t, f"authored match of sibling {sib}"))
        return [] if len(out) > self.CONSENSUS_MAX_TEXTURES else out

    def _rule_dir_sibling(self, mesh: str) -> list[tuple[str, str]]:
        """Any `.dds` sitting in the mesh's own directory.

        Gated by the same "is this really a grouping?" test as dir_consensus.
        Ungated it does real damage: `c3/npc/` holds 125 unrelated flat `.c3`
        files and exactly one stray `.dds`, and without the gate that one file
        gets claimed as the texture of all 125.
        """
        own = mesh.rsplit("/", 1)[0]
        if len(self._c3_by_dir.get(own, ())) > self.CONSENSUS_MAX_FILES:
            return []
        return [(t, f"same directory {own}/") for t in self._tex_by_dir.get(own, ())]

    # -- public API --------------------------------------------------------
    def labels_of(self, mesh: str) -> list[str]:
        """The 3DSMax source-texture labels embedded in a mesh's PHY chunks.

        docs/modding.md section 9.5 note 4: `labelLen`/`label` is usually the
        original export path.  In this corpus 14,542 of 14,736 PHY chunks
        carry one, but they are the *artist's* local paths -- only 6 of 1,936
        distinct labels are a path that resolves inside the game tree, so the
        useful part is the basename.
        """
        rec = self._mesh_record(_norm(mesh))
        return list(dict.fromkeys(rec["labels"])) if rec else []

    def is_mesh(self, path: str) -> bool:
        """True if this `.c3` carries PHY-family geometry (as opposed to being
        a pure motion / particle / camera / shape container)."""
        rec = self._mesh_record(_norm(path))
        return bool(rec and rec["chunks"])

    def matches(self, mesh: str, *, include_inferred: bool = True) -> list[Match]:
        """Ranked texture candidates for one mesh, best first.

        Deduplicated by texture path: if two rules name the same file, the
        higher-confidence rule wins and the other is dropped.  Within one
        confidence level the order the rule produced is preserved.
        """
        self._load_tables()
        mesh = _norm(mesh)
        found: dict[str, Match] = {}

        def rank(kind: str, conf: float) -> tuple[int, float]:
            return (0 if kind == "authored" else 1, -conf)

        def emit(method: str, pairs: Iterable[tuple[str, str]]) -> None:
            kind, conf, _ = METHODS[method]
            if kind == "inferred" and not include_inferred:
                return
            pairs = list(pairs)
            # **A rule that names several textures for one mesh has not
            # identified one.** `c3/npc/999001100.c3` is the standby motion of
            # thirteen NPCs -- Storekeeper, GuildConductor1..4, RuoDie and the
            # rest -- and they do not share a skin, so `npc_table` yields
            # three. Reporting each at the rule's full 0.95 and letting
            # `best()` take whichever sorted first presented an arbitrary pick
            # as an authored fact, and the pick was wrong: the Storekeeper
            # uses 9990211 and this returned 9990010.
            #
            # Picking blind among n equally-authored candidates is right 1/n
            # of the time, so that is the confidence. The rule is still
            # `authored` -- the *table* said this, and the ambiguity is real
            # rather than a failure to read it -- but a caller ranking by
            # confidence now sees the difference between "the table says X"
            # and "the table says one of these three".
            distinct = {t for t, _ in pairs}
            n = max(1, len(distinct))
            eff = conf / n
            for tex, detail in pairs:
                cur = found.get(tex)
                if cur is None or rank(kind, eff) < rank(cur.kind, cur.confidence):
                    found[tex] = Match(tex, method, eff, kind, detail,
                                       alternatives=n)

        emit("effect_table", self._effect.get(mesh, ()))
        emit("simpleobj_table", self._simple.get(mesh, ()))
        emit("npc_table", self._npcj.get(mesh, ()))
        emit("simplerole_table", self._role.get(mesh, ()))
        emit("appearance_table", self._appear.get(mesh, ()))
        emit("motion_appearance", self._motion.get(mesh, ()))
        emit("bodytype_dir", self._rule_bodytype_dir(mesh))
        emit("stem_pair", self._rule_stem_pair(mesh))
        emit("npc_look_id", self._rule_npc_look_id(mesh))
        emit("objtex_id", self._rule_objtex_id(mesh))
        emit("dir_consensus", self._rule_dir_consensus(mesh))
        emit("label_sibling", self._rule_label_sibling(mesh))
        emit("dir_sibling", self._rule_dir_sibling(mesh))

        order = list(METHODS)
        return sorted(found.values(),
                      key=lambda m: (m.kind != "authored", -m.confidence,
                                     order.index(m.method), m.texture))

    def best(self, mesh: str) -> Optional[Match]:
        m = self.matches(mesh)
        return m[0] if m else None

    # -- mesh census (cached) ----------------------------------------------
    # `CACHE` is set per instance in __init__, from that instance's own root.
    # It is deliberately NOT a class attribute: as one it resolved at import
    # against whatever install was configured then, and every index in the
    # process wrote through it.

    def _cache_covers(self, idx: dict) -> bool:
        """Does a cached census cover the current universe?

        The census is complete by construction -- ``scan_meshes`` walks every
        known ``.c3`` -- so a cache missing more than a sliver of today's
        universe was built against a smaller one, typically before name
        recovery ran, and must not be trusted.  A healthy cache misses
        nothing (measured: 6390 of 6390); the sliver only absorbs files
        added or renamed since the scan.

        That premise held for the census but not for the universe it was
        compared against, and for a while THIS FUNCTION COULD NEVER RETURN
        TRUE.  ``_build_universe`` folded the **global**
        ``out/wdf/{c3,data}_names.json`` into ``universe`` -- ``find_derived``
        with no root -- so ``c3_files`` carried recovered names that a given
        install's archives do not contain.  ``_scan_one`` returns ``None`` on
        anything unreadable, so those never entered ``idx``, and they were
        counted missing on every subsequent read, forever.  **The cache each
        run wrote was rejected by the next one**, which was the whole of the
        292 s-vs-29 s cost.  MEASURED 2026-08-10, and the missing set was
        *identical* across installs spanning 3.6x in size:

            base   universe  cached  missing  tolerance  covers
            5017      5,021   4,811      210         25      NO
            5517     10,473  10,263      210         52      NO
            6090     18,230  18,020      210         91      NO

            pairwise shared = 210, only-A = 0, only-B = 0   (the SAME paths)
            readable on any base .......... 0 of 210
            drawn from the global tables .. 210 of 210 (100.0%)

        Not drift, not a stale cache, not per-tree: one fixed set of names
        recovered from the archives' own tables that no archive resolves.
        The fix is the one the shape of that table argues for -- **make the
        comparand per-base** rather than widen the tolerance, which would
        have hidden a real signal about the recovered-name set.  A miss is
        only a miss if this install could have supplied it, so a pooled name
        with no loose file behind it is put to ``_in_this_install``, one TQ
        hash into this install's own archive index.  The 210 fail that and
        leave scope; a genuinely absent local ``.c3`` still counts, because
        it is either loose or resolvable and so never reaches the filter.

        Cost is bounded by the misses, not by the universe: a healthy cache
        pays ~210 hashes, and a cache that really is stale was going to
        rescan for 292 s anyway.  ``docs/CORRECTIONS.md``
        ``C-2026-08-10-quickfix-gap210``."""
        missing = [p for p in self.c3_files if p not in idx]
        missing = [p for p in missing if p not in self._global_only
                   or self._in_this_install(p)]
        return len(missing) <= max(8, len(self.c3_files) // 200)

    def _load_mesh_index(self) -> dict[str, dict]:
        if self._mesh_index is not None:
            return self._mesh_index
        cached = coroot.find_derived("out/meshtex/mesh_index.json", self.root)
        if cached is not None and not self._cache_is_stale(cached):
            try:
                doc = json.loads(cached.read_text("utf-8"))
            except Exception:
                doc = None
            # Stamped since the provenance migration; `unwrap` returns an
            # unstamped document unchanged, so an index built before it still
            # loads. `strict=False` is deliberate: the only thing refused
            # outright is an index that *says* it came from another install.
            verdict = provenance.verdict(doc, self.root) if doc is not None else None
            if verdict == provenance.FOREIGN:
                print(f"[meshtex] refusing mesh index {cached}: it was built "
                      f"from {provenance.describe(doc)}, and this is "
                      f"{coroot.base_id(self.root)} -- rescanning",
                      file=sys.stderr)
                doc = None
            idx = provenance.unwrap(doc)[1] if doc is not None else None
            if isinstance(idx, dict):
                if self._cache_covers(idx):
                    self._mesh_index = idx
                    return self._mesh_index
                # Report the count the decision was actually made on. The
                # old wording compared entries against the whole universe,
                # which made the 210 pooled names look like a size gap and
                # sent three readers looking for a stale cache.
                short = [p for p in self.c3_files if p not in idx
                         and (p not in self._global_only
                              or self._in_this_install(p))]
                print(f"[meshtex] ignoring stale mesh index {cached}: it has "
                      f"{len(idx)} entries and misses {len(short)} .c3 files "
                      f"this install can open (tolerance "
                      f"{max(8, len(self.c3_files) // 200)}; "
                      f"{len(self.c3_files)} .c3 in the universe, of which "
                      f"{len(self._global_only & self.c3_files)} are pooled "
                      "names not backed by a loose file) -- rescanning",
                      file=sys.stderr)
        self._mesh_index = self.scan_meshes()
        return self._mesh_index

    def _cache_is_stale(self, cache: Path) -> bool:
        """True when a WDF name table postdates the cached index.

        The index is built from whatever names existed at the time, so an
        index cached *before* `wdf_recover.py` ran is silently half-sized and
        looks perfectly healthy.  (Found the hard way -- running the viewer
        or tests before `health.py --bootstrap` used to poison this cache.)
        Two independent guards close the trap: this mtime check -- any name
        table newer than the cache invalidates it -- and `_cache_covers`,
        which rejects a cache that no longer spans the universe regardless
        of timestamps.  Name tables resolve through `coroot.find_derived`,
        same as the cache itself.
        """
        try:
            cached = cache.stat().st_mtime
        except OSError:
            return True
        for fn in ("out/wdf/c3_names.json", "out/wdf/data_names.json",
                   "out/dll/wdf_name_recovery.json"):
            p = coroot.find_derived(fn)
            try:
                if p is not None and p.stat().st_mtime > cached:
                    return True
            except OSError:
                continue
        return False

    def _mesh_record(self, path: str) -> Optional[dict]:
        idx = self._load_mesh_index()
        if path in idx:
            return idx[path]
        rec = self._scan_one(path)
        idx[path] = rec
        return rec

    def _scan_one(self, path: str) -> Optional[dict]:
        try:
            data = self.assets.read(path)
            f = C3File(data)
        except Exception:
            return None
        chunks, labels = [], []
        for c in f.chunks:
            if c.tag not in _PHY_TAGS:
                continue
            if c.tag == b"MNEW":
                chunks.append({"tag": "MNEW", "name": f.node_name(c) or "",
                               "nv": 0, "nf": 0, "uv": None})
                continue
            try:
                m = c3phy.parse_phy(c.tag, c.body)
            except Exception:
                chunks.append({"tag": c.name, "name": "", "nv": 0, "nf": 0, "uv": None})
                continue
            if m.label_raw:
                labels.append(_decode_label(m.label_raw))
            us = [v.u0 for v in m.vertices]
            vs = [v.v0 for v in m.vertices]
            chunks.append({
                "tag": c.name, "name": m.name,
                "nv": len(m.vertices), "nf": len(m.faces),
                "uv": [min(us), max(us), min(vs), max(vs)] if us else None,
                "c3exp_color": m.is_c3exp_color,
            })
        return {"chunks": chunks, "labels": labels,
                "tags": sorted({c.name for c in f.chunks})}

    def scan_meshes(self, progress: bool = False) -> dict[str, dict]:
        """Parse every `.c3` in the universe.  ~2 minutes; cached in
        out/meshtex/mesh_index.json.

        The cache is written when THE CORPUS IS COMPLETE.  That is the
        question; "were the recovered name tables loaded" was only ever a
        proxy for it.

        **THE PROXY USED TO BE EXACT AND THIS COMMIT FALSIFIES IT.**  The
        rule here read `if not self.names_loaded`, and its reason was
        written as: *a census taken without them sees only loose files, and
        persisting it would poison every later run with a half-sized index
        that looks fine.*  True of every install when it was written --
        every archive on the box was a `.wdf`, whose index stores only
        `tq_hash(name)`, so with no recovered table there was nothing but
        the loose tree.  A `.tpd`/`.tpi` pair declares its own paths
        (`coassets.AssetRoot.container_names`), so such an install has a
        COMPLETE corpus and no recovered table, and never will have one --
        there is no WDF to recover names from.  Measured on Zephyr: the
        census is 29,717 of 29,717 `.c3`, and the old rule refused to cache
        it, re-scanning all 29,717 container entries on every run and
        telling the user to run the bootstrap they had just run.

        So a half-sized census is still refused, and a complete one is kept
        however it was completed."""
        out: dict[str, dict] = {}
        claimed, why = self.claim_by_content()
        if claimed or why["wdf_unnamed"]:
            # LOUD, the way `in_scope_textures`' withheld count is: a mesh
            # the census gained by content, or one it knows exists and cannot
            # name, must be a number someone sees.
            print(f"[meshtex] {why['claimed']} .dds-named file(s) are C3 meshes "
                  f"by content and were added to the mesh census; "
                  f"{why['wdf_unnamed']} more C3-by-content entries sit in a "
                  f"hash-only WDF under no known name and cannot be claimed",
                  file=sys.stderr, flush=True)
        paths = sorted(self.c3_files | claimed)
        for i, p in enumerate(paths):
            if progress and i % 500 == 0:
                print(f"  scan {i}/{len(paths)}", file=sys.stderr, flush=True)
            rec = self._scan_one(p)
            if rec is not None:
                out[p] = rec
        if not (self.names_loaded or self._declared):
            print("[meshtex] WARNING: no recovered name tables "
                  "(out/wdf/*_names.json, out/dll/wdf_name_recovery.json) "
                  "and no container declares its own paths -- "
                  f"this census sees only {len(paths)} loose .c3 files and "
                  "none of the ~25,000 archived assets. It will NOT be "
                  "cached. Run `py -3 tools/health.py --bootstrap` first.",
                  file=sys.stderr)
            return out
        # Stamped with the install it was built from, so a later read can
        # *refuse* a foreign one rather than infer from its size. This index
        # is why the stamp exists: two bases in one process used to share one
        # path, and `_cache_covers` only noticed because the two clients
        # happen to differ in mesh count.
        provenance.write_json("out/meshtex/mesh_index.json", out, self.root,
                              tool="meshtex.py", indent=None)
        return out

    def all_meshes(self) -> list[str]:
        """Every `.c3` that carries PHY-family geometry, sorted."""
        return sorted(k for k, v in self._load_mesh_index().items() if v.get("chunks"))

    def non_mesh_c3(self) -> list[str]:
        """`.c3` containers with no geometry: motion, particle, camera, shape."""
        return sorted(k for k, v in self._load_mesh_index().items() if not v.get("chunks"))

    # -- sanity checks -----------------------------------------------------
    def verify(self, mesh: str, texture: str) -> dict:
        """Cheap plausibility check on a (mesh, texture) pair.

        Checks only things that are decidable without rendering:
          * the DDS exists, has a valid header and decodes level 0
          * dimensions are power-of-two (all 19,295 archived textures are)
          * the mesh's UVs are inside a sane range

        Returns a dict of findings.  A ``False`` in ``ok`` means the pair is
        certainly wrong; ``True`` means nothing objected -- it is not proof.
        """
        res: dict = {"texture": texture, "ok": True, "notes": []}
        try:
            data = self.assets.read(texture)
        except Exception as e:
            res["ok"] = False
            res["notes"].append(f"unreadable: {e}")
            return res
        try:
            import dds as _dds
            hdr = _dds.parse_header(data)
            res["width"], res["height"] = hdr.width, hdr.height
            res["format"] = hdr.fourcc or f"RGB{hdr.rgb_bits}"
            if hdr.width & (hdr.width - 1) or hdr.height & (hdr.height - 1):
                res["notes"].append("non-power-of-two")
            _dds.decode(data, 0)
        except Exception as e:
            res["ok"] = False
            res["notes"].append(f"DDS decode failed: {e}")
            return res
        rec = self._mesh_record(_norm(mesh))
        if rec:
            bad = [c["name"] for c in rec["chunks"]
                   if c.get("uv") and (c["uv"][0] < -8 or c["uv"][1] > 8
                                       or c["uv"][2] < -8 or c["uv"][3] > 8)]
            if bad:
                res["notes"].append(f"UVs far outside [0,1] on {bad}")
        return res

    def close(self) -> None:
        if self._owns_assets:
            try:
                self.assets.close()
            except Exception:
                pass

    def __enter__(self) -> "MeshTextureIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# coverage report
# ---------------------------------------------------------------------------

#: Matches kept per mesh in coverage.json. ONE number for both writers: the
#: CLI and the viewer's live build (`unify.UnifiedIndex`) both go through
#: `build_coverage`, so what the viewer builds live and what it later loads
#: from the file are the same answer. Before 2026-09-18 the live build kept
#: EVERY match while the file kept 12, and the viewer's texture-owner table
#: differed between the two states.
COVERAGE_MATCHES = 12


def build_coverage(idx: MeshTextureIndex, verify: bool = False,
                   progress=None) -> dict:
    """The coverage report for `idx`. `progress(done, total)` is ticked every
    250 meshes and on the last one -- the viewer's build shows it."""
    meshes = idx.all_meshes()
    total = len(meshes)
    entries = {}
    by_dir: dict[str, dict] = collections.defaultdict(
        lambda: {"meshes": 0, "matched": 0, "authored": 0, "unmatched": []})
    by_method: collections.Counter = collections.Counter()
    best_method: collections.Counter = collections.Counter()
    unmatched: list[str] = []
    verified_ok = verified_bad = 0

    for i, m in enumerate(meshes):
        if progress is not None and (i % 250 == 0 or i + 1 == total):
            progress(i + 1, total)
        ms = idx.matches(m)
        d = "/".join(m.split("/")[:2])
        by_dir[d]["meshes"] += 1
        rec = {"matches": [x.as_dict() for x in ms[:COVERAGE_MATCHES]],
               "n_matches": len(ms)}
        if ms:
            by_dir[d]["matched"] += 1
            best_method[ms[0].method] += 1
            if ms[0].kind == "authored":
                by_dir[d]["authored"] += 1
            for x in ms:
                by_method[x.method] += 1
            if verify:
                v = idx.verify(m, ms[0].texture)
                rec["verify"] = v
                verified_ok += bool(v["ok"])
                verified_bad += (not v["ok"])
        else:
            unmatched.append(m)
            by_dir[d]["unmatched"].append(m)
        entries[m] = rec

    n = len(meshes)
    matched = n - len(unmatched)
    summary = {
        "c3_files_total": len(idx.c3_files),
        "c3_files_without_geometry": len(idx.non_mesh_c3()),
        "meshes": n,
        "matched": matched,
        "unmatched": len(unmatched),
        "coverage_pct": round(matched / n * 100, 2) if n else 0.0,
        "authored_pct": round(sum(v["authored"] for v in by_dir.values()) / n * 100, 2) if n else 0.0,
        "textures_in_universe": len(idx.textures),
        "by_best_method": dict(best_method.most_common()),
        "by_method_any_rank": dict(by_method.most_common()),
        "by_directory": {k: {kk: vv for kk, vv in v.items() if kk != "unmatched"}
                         for k, v in sorted(by_dir.items())},
        "methods": {k: {"kind": v[0], "confidence": v[1], "description": v[2]}
                    for k, v in METHODS.items()},
    }
    if verify:
        summary["verified_ok"] = verified_ok
        summary["verified_failed"] = verified_bad
    return {"summary": summary, "unmatched": unmatched, "meshes": entries}


def write_coverage(rep: dict, root, *, tool: str) -> Path:
    """Persist a `build_coverage` report for `root`'s base -- THE ONE WRITER.

    `out/meshtex/` resolves per base (`coroot.derived_path`), so a patched
    install -- a new base id -- gets its own file and never inherits the old
    base's answers. coverage.json is written to a temp name and moved into
    place, so a reader (another viewer, the CLI) never sees half a file; the
    provenance sidecar is stamped LAST, over the finished bytes.
    """
    import os
    out = coroot.derived_path("out/meshtex", root)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "coverage.json"
    tmp = out / f"coverage.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(rep, indent=1), "utf-8")
    os.replace(tmp, dest)
    # Sidecar, not an envelope: seven modules read this file's shape
    # directly, so changing it to carry a stamp would mean changing
    # all seven at once. The sidecar leaves the artefact byte-
    # identical and still lets a reader ask what built it.
    provenance.stamp_file(dest, root, tool=tool)
    (out / "summary.json").write_text(json.dumps(rep["summary"], indent=1), "utf-8")
    (out / "unmatched.txt").write_text("\n".join(rep["unmatched"]), "utf-8")
    return dest


def measure_precision(idx: MeshTextureIndex) -> dict:
    """Score every inferred rule against the authored rules.

    For each mesh where an authored rule *and* the inferred rule both fire,
    ask whether the inferred rule's first candidate (`top`) and whether any of
    its candidates (`any`) is in the authored answer set.  This is the only
    ground truth available, and it is genuine ground truth: the authored rules
    read the pairing straight out of a shipped data file.

    Caveat, stated because it matters: the sample is biased toward meshes the
    authored tables already cover, which are exactly the ones the inferred
    rules are *not* needed for.  Treat the numbers as an upper bound on
    trustworthiness, not a guarantee.
    """
    idx._load_tables()
    inferred = [(k, v) for k, v in METHODS.items() if v[0] == "inferred"]
    rules = {
        "stem_pair": idx._rule_stem_pair,
        "npc_look_id": idx._rule_npc_look_id,
        # graded with the mesh's own authored row held out -- the live rule
        # never fires when one exists, so it would otherwise be unscoreable
        "dir_consensus": idx._dir_consensus_holdout,
        "bodytype_dir": idx._rule_bodytype_dir,
        "objtex_id": idx._rule_objtex_id,
        "label_sibling": idx._rule_label_sibling,
        "dir_sibling": idx._rule_dir_sibling,
    }
    out: dict[str, dict] = {}
    meshes = idx.all_meshes()
    for name, _ in inferred:
        fn = rules[name]
        fires = n = top = any_ = 0
        for m in meshes:
            cands = [t for t, _d in fn(m)]
            if not cands:
                continue
            fires += 1
            gt = {t for t, _d in idx._authored_only(m)}
            if not gt:
                continue
            n += 1
            top += cands[0] in gt
            any_ += bool(set(cands) & gt)
        out[name] = {
            "fires_on": fires, "scored_against_authored": n,
            "top1_precision": round(top / n, 3) if n else None,
            "any_precision": round(any_ / n, 3) if n else None,
            "declared_confidence": METHODS[name][1],
        }
    return out


#: Every flag this CLI understands, so an unrecognised one can be REFUSED
#: rather than swallowed.  The hand-rolled parser below dropped any unknown
#: `--flag` into a set nobody read, so `--root <dir>` ran against the
#: CONFIGURED install, wrote that install's index, and printed success --
#: `docs/CORRECTIONS.md` C22.  The rule it breaks is from
#: `handoff_5517_base_prep` §1.2: accepting an argument and discarding it is
#: worse than refusing one.
_FLAGS = {"--help", "--coverage", "--precision", "--scan", "--verify", "--root"}


def _main(argv: list[str]) -> int:
    argv = list(argv)

    #: `--root DIR`, now honoured rather than ignored.  `CO_ROOT` already
    #: worked and remains the documented fallback; this makes the flag mean
    #: what every other tool here means by it.
    root: Path | None = None
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            print("--root needs a directory", file=sys.stderr)
            return 2
        root = Path(argv[i + 1])
        if not root.is_dir():
            print(f"--root: no such directory: {root}", file=sys.stderr)
            return 2
        del argv[i:i + 2]

    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    unknown = sorted(flags - _FLAGS)
    if unknown:
        print(f"unrecognised flag(s): {' '.join(unknown)}\n"
              f"known: {' '.join(sorted(_FLAGS))}", file=sys.stderr)
        return 2

    if "--help" in flags or (not args and "--coverage" not in flags
                             and "--scan" not in flags
                             and "--precision" not in flags):
        print(__doc__)
        return 0

    # The index AND the writers below must both resolve for `root`, not
    # for the configured install -- writing one base's coverage into
    # another's namespace is the damage C22 actually did.
    with MeshTextureIndex(root or DEFAULT_ROOT) as idx:
        if "--precision" in flags:
            p = measure_precision(idx)
            out = coroot.derived_path("out/meshtex", root)
            out.mkdir(parents=True, exist_ok=True)
            (out / "precision.json").write_text(json.dumps(p, indent=1), "utf-8")
            print(f"{'rule':<18}{'fires':>8}{'scored':>8}{'top-1':>8}{'any':>8}{'conf':>7}")
            for k, v in p.items():
                t = f"{v['top1_precision']*100:.0f}%" if v["top1_precision"] is not None else "-"
                a = f"{v['any_precision']*100:.0f}%" if v["any_precision"] is not None else "-"
                print(f"{k:<18}{v['fires_on']:>8}{v['scored_against_authored']:>8}"
                      f"{t:>8}{a:>8}{v['declared_confidence']:>7}")
            return 0

        if "--scan" in flags:
            m = idx.scan_meshes(progress=True)
            print(f"scanned {len(m)} .c3 files -> {idx.CACHE}")
            return 0

        if "--coverage" in flags:
            print("scanning c3 containers (cached after the first run)...",
                  file=sys.stderr)
            idx._load_mesh_index()
            rep = build_coverage(idx, verify="--verify" in flags)
            dest = write_coverage(rep, idx.root, tool="meshtex.py --coverage")
            s = rep["summary"]
            print(f"\n{s['meshes']} meshes ({s['c3_files_total']} .c3 files, "
                  f"{s['c3_files_without_geometry']} of them geometry-free)")
            print(f"matched {s['matched']}  = {s['coverage_pct']}%   "
                  f"(authored-quality: {s['authored_pct']}%)")
            print("\nby best method:")
            for k, v in s["by_best_method"].items():
                print(f"  {k:<20} {v:>6}   [{METHODS[k][0]}]")
            print("\nby directory:")
            print(f"  {'dir':<22}{'meshes':>8}{'matched':>9}{'rate':>8}{'authored':>10}")
            for k, v in s["by_directory"].items():
                print(f"  {k:<22}{v['meshes']:>8}{v['matched']:>9}"
                      f"{v['matched']/v['meshes']*100:>7.1f}%{v['authored']:>10}")
            print(f"\nwrote {dest}")
            return 0

        for a in args:
            print(a)
            ms = idx.matches(a)
            if not ms:
                print("    (no candidate)")
            for m in ms[:15]:
                print(f"    {m.confidence:.2f} {m.kind:<9} {m.method:<18} "
                      f"{m.texture}\n              {m.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
