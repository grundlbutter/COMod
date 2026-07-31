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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import AssetRoot, C3File, DEFAULT_ROOT, parse_ini   # noqa: E402
import c3phy                                                      # noqa: E402

__all__ = ["Match", "MeshTextureIndex", "METHODS"]

REPO = Path(__file__).resolve().parent.parent

#: Rule catalogue.  ``kind`` is "authored" when the pairing is stated by a
#: shipped data file and "inferred" when it is a naming convention measured
#: from the corpus.
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

    def as_dict(self) -> dict:
        return {"texture": self.texture, "method": self.method,
                "confidence": self.confidence, "kind": self.kind,
                "detail": self.detail}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _norm(p: str) -> str:
    return p.replace("\\", "/").strip().lstrip("/").lower()


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
        self.ini = self.root / "ini"
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
        98.7% -- see docs/assets.md section 2.3)."""
        for p in self.root.rglob("*"):
            if p.is_file():
                self.universe.add(_norm(str(p.relative_to(self.root))))
        for fn in ("out/wdf/c3_names.json", "out/wdf/data_names.json"):
            f = REPO / fn
            if f.is_file():
                self.universe.update(_norm(v) for v in
                                     json.loads(f.read_text("utf-8")).values())
        f = REPO / "out/dll/wdf_name_recovery.json"
        if f.is_file():
            try:
                self.universe.update(
                    _norm(v) for v in
                    json.loads(f.read_text("utf-8"))["resolved"].values())
            except Exception:
                pass
        self.textures = {p for p in self.universe if p.endswith(".dds")}
        self.c3_files = {p for p in self.universe if p.endswith(".c3")}
        self._exists_cache: dict[str, bool] = {}

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

    def _build_effect_table(self) -> None:
        """ini/3DEffect.ini: per effect, `Amount=N` parts each with
        `EffectId<i>` (-> 3DEffectObj.ini, a .c3) and `TextureId<i>`
        (-> 3dtexture.ini, a .dds).  VERIFIED: both id spaces resolve to
        explicit paths, 2,314/2,351 and 7,785/7,929 of which exist here."""
        for sec, kv in parse_ini(self.ini / "3DEffect.ini").items():
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
        for sec, kv in parse_ini(self.ini / "3DSimpleObj.ini").items():
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
        """ini/npc.json rows carry `simple_object` (-> 3DSimpleObj.ini, giving
        the texture) and three motion ids (-> 3dmotion.ini, giving the .c3
        files that NPC animates with).  Joining them attaches the NPC's own
        texture to every mesh file it plays.  436 of 437 rows resolve to at
        least one real texture.  VERIFIED join; both halves are authored."""
        p = self.ini / "npc.json"
        if not p.is_file():
            return
        try:
            rows = json.loads(p.read_text("utf-8", errors="replace"))
        except Exception:
            return
        simple = parse_ini(self.ini / "3DSimpleObj.ini")
        motion = _flat_ini(self.ini / "3dmotion.ini")
        for r in rows:
            kv = simple.get(f"ObjIDType{r.get('simple_object')}")
            if not kv:
                continue
            try:
                n = int(kv.get("PartAmount", "0") or 0)
            except ValueError:
                continue
            texes = [t for t in (self._tex(self.tex_paths.get(kv.get(f"Texture{i}", "")))
                                 for i in range(n)) if t]
            if not texes:
                continue
            name = r.get("name", "")
            for key in ("standby_motion", "blaze_motion", "rest_motion"):
                f = motion.get(str(r.get(key)))
                for t in texes:
                    self._add(self._npcj, f, t,
                              f"npc.json type={r.get('type')} ({name}) "
                              f"simple_object={r.get('simple_object')} via {key}")

    def _build_simplerole_table(self) -> None:
        """ini/3DsimpleRole.ini: the 24 character-creation preview roles, each
        naming a `3DSimpleObjID` (-> 3DSimpleObj.ini, giving the texture) and
        two motion ids that are bare mesh ids in 3dobj.ini / c3/mesh/.
        VERIFIED by inspection; this is what backs c3/mesh/99988*.c3."""
        p = self.ini / "3DsimpleRole.ini"
        if not p.is_file():
            return
        simple = parse_ini(self.ini / "3DSimpleObj.ini")
        motion = _flat_ini(self.ini / "3dmotion.ini")
        for sec, kv in parse_ini(p).items():
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
            for tex, detail in pairs:
                cur = found.get(tex)
                if cur is None or rank(kind, conf) < rank(cur.kind, cur.confidence):
                    found[tex] = Match(tex, method, conf, kind, detail)

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
    CACHE = REPO / "out" / "meshtex" / "mesh_index.json"

    def _load_mesh_index(self) -> dict[str, dict]:
        if self._mesh_index is not None:
            return self._mesh_index
        if self.CACHE.is_file() and not self._cache_is_stale():
            try:
                self._mesh_index = json.loads(self.CACHE.read_text("utf-8"))
                return self._mesh_index
            except Exception:
                pass
        self._mesh_index = self.scan_meshes()
        return self._mesh_index

    def _cache_is_stale(self) -> bool:
        """True when a WDF name table postdates the cached index.

        The index is built from whatever names existed at the time, so an
        index cached *before* `wdf_recover.py` ran is silently half-sized and
        looks perfectly healthy.  Guard: any name table newer than the cache
        invalidates it.  (Found the hard way -- running the viewer or tests
        before `health.py --bootstrap` used to poison this cache.)
        """
        try:
            cached = self.CACHE.stat().st_mtime
        except OSError:
            return True
        for fn in ("out/wdf/c3_names.json", "out/wdf/data_names.json",
                   "out/dll/wdf_name_recovery.json"):
            p = REPO / fn
            try:
                if p.stat().st_mtime > cached:
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
        out/meshtex/mesh_index.json."""
        out: dict[str, dict] = {}
        paths = sorted(self.c3_files)
        for i, p in enumerate(paths):
            if progress and i % 500 == 0:
                print(f"  scan {i}/{len(paths)}", file=sys.stderr, flush=True)
            rec = self._scan_one(p)
            if rec is not None:
                out[p] = rec
        self.CACHE.parent.mkdir(parents=True, exist_ok=True)
        self.CACHE.write_text(json.dumps(out), "utf-8")
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

def build_coverage(idx: MeshTextureIndex, verify: bool = False) -> dict:
    meshes = idx.all_meshes()
    entries = {}
    by_dir: dict[str, dict] = collections.defaultdict(
        lambda: {"meshes": 0, "matched": 0, "authored": 0, "unmatched": []})
    by_method: collections.Counter = collections.Counter()
    best_method: collections.Counter = collections.Counter()
    unmatched: list[str] = []
    verified_ok = verified_bad = 0

    for m in meshes:
        ms = idx.matches(m)
        d = "/".join(m.split("/")[:2])
        by_dir[d]["meshes"] += 1
        rec = {"matches": [x.as_dict() for x in ms[:12]], "n_matches": len(ms)}
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


def _main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    if "--help" in flags or (not args and "--coverage" not in flags
                             and "--scan" not in flags
                             and "--precision" not in flags):
        print(__doc__)
        return 0

    with MeshTextureIndex() as idx:
        if "--precision" in flags:
            p = measure_precision(idx)
            out = REPO / "out" / "meshtex"
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
            out = REPO / "out" / "meshtex"
            out.mkdir(parents=True, exist_ok=True)
            (out / "coverage.json").write_text(json.dumps(rep, indent=1), "utf-8")
            s = rep["summary"]
            (out / "summary.json").write_text(json.dumps(s, indent=1), "utf-8")
            (out / "unmatched.txt").write_text("\n".join(rep["unmatched"]), "utf-8")
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
            print(f"\nwrote {out/'coverage.json'}")
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
