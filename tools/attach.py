#!/usr/bin/env python3
r"""attach.py -- how the Conquer client attaches a character part to a socket.

Reference implementation of the composition recovered in `docs/attachment.md`
by disassembling `Role3D.dll` and `graphic.dll`.  Everything here is either
read out of the machine code (RVA cited in the docstring) or measured on the
shipped assets; nothing is curve-fitted.

The whole answer in four lines:

    idx    = mesh.FindPhyByName(dumy)                # graphic.dll 0x266D0, stricmp
    Msock  = Motion_GetMatrix(bodyMotion[idx], 0, f) # graphic.dll 0x551A0, bone 0
    Mbone  = Motion_GetMatrix(partMotion[c],  b, f)  # the PART's own motion
    world  = Mbone x Msock x Mrole                   # row-vector, D3DXMatrixMultiply

The bit the viewer was missing is `Mbone`: **an equipped part carries its own
`MOTI` and its bone-0 matrix is where the part's scale and its axis correction
live.**  Drop it and a sword renders ~4x too long and a fifth of all headgear
sits at the wrong angle.

    py -3 tools/attach.py --body 002135000 --armet 001111310 --r-weapon 410009
    py -3 tools/attach.py --sockets 002135000
    py -3 tools/attach.py --validate            # the numbers in docs/attachment.md
    py -3 tools/attach.py --motis               # what the part MOTIs actually do

As a module::

    from attach import PartMesh, socket_matrix, world_for_part, SLOT_SOCKET
    body = PartMesh.load(root, "c3/mesh/002135000.c3")
    hat  = PartMesh.load(root, "c3/mesh/001111010.c3")
    S    = socket_matrix(body, "v_armet", frame=0)
    for x, y, z in hat.world_vertices(S, frame=0):
        ...
"""
from __future__ import annotations

import argparse
import copy
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import c3phy                                              # noqa: E402
import effects as fx                                      # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot, parse_ini   # noqa: E402

Mat4 = tuple

def socket_for(slot: str, plugin=None) -> Optional[str]:
    r"""The socket a slot hangs off, asking the parser plugin first.

    RE'd against patch 6090 (2026-08-05), and the reason this is a plugin
    question rather than a constant: **`RolePart.ini` is not the art.** 6090
    declares 8 parts and 7 dummies -- including `v_mount`, `v_misc`,
    `v_l_shield` and `v_r_shield` -- while every one of its 3,001 body
    meshes carries exactly `v_body`, `v_armet`, `v_l_weapon`, `v_r_weapon`
    and nothing more. CCO declared 52 dummies including `v_head`, which 6090
    drops entirely.

    A plugin returning "" means "this client cannot attach that slot", which
    is a different and more useful answer than falling through to the body
    origin.
    """
    if plugin is not None:
        hit = plugin.slot_socket(slot)
        if hit is not None:
            return hit or None
    return SLOT_SOCKET.get(slot)


#: `RolePart.ini [Config]` part name -> the `[Dumy]` socket it hangs off.
#: INFERRED, but 7 of the 8 attachable parts have an exact `v_<part>` entry in
#: the same file's `[Dumy]` list, and the body meshes ship exactly those chunks.
#: `shield` has no `v_shield`; `v_l_shield` is the only shield dummy that
#: occurs, and the slot is not shipped in this build anyway.
SLOT_SOCKET = {
    "body":      None,          # the body IS the frame everything else is in
    "mix_body":  None,
    "armet":     "v_armet",
    "armet_dx8": "v_armet",
    "mix_armet": "v_armet",
    "mix_armet_dx8": "v_armet",
    "head":      "v_head",
    "l_weapon":  "v_l_weapon",
    "r_weapon":  "v_r_weapon",
    "shield":    "v_l_shield",
    "misc":      "v_misc",
    "pelvis":    "v_pelvis",
    "mount":     "v_mount",     # INVERTED: on the MOUNT mesh, not the body
}

#: Weapon set -> the body motion folder that holds it, recovered from the
#: **official lineage's own authored data**: CCO's `3dmotion.ini` spells the
#: pairing out one key at a time -- 259 rows for set 480 alone, each of the
#: form `1480100 = c3/0001/410/100.c3` -- and the dominant non-`000` folder
#: per set is unambiguous (480 -> 410, 350 -> 560, 370 -> 500, 380 -> 741).
#:
#: **6090 ships none of those rows** in either the stale ini or the compiled
#: dbc, so a lookup for `<shape>480<action>` misses and falls through to the
#: unarmed folder -- which posed every armed character empty-handed. The
#: folders themselves all still ship (6090 keys 18 of them), so the mapping
#: is what went missing, not the motions.
#:
#: RECOVERED, not inferred: this is TQ's pairing, read out of a client of
#: the same lineage. Applied only when the aliased target actually exists.
WEAPON_MOTION_SET = {
    "350": "560",
    "360": "410",
    "370": "500",
    "380": "741",
    "410": "410",
    "420": "410",
    "421": "410",
    "422": "410",
    "430": "410",
    "440": "410",
    "450": "410",
    "460": "410",
    "480": "410",
    "481": "410",
    "490": "410",
    "500": "500",
    "510": "560",
    "530": "560",
    "540": "560",
    "560": "560",
    "561": "560",
    "562": "560",
    "580": "560",
    "601": "601",
    "611": "611",
    "612": "611",
    "613": "611",
    "614": "611",
    "615": "611",
    "616": "611",
    "617": "611",
    "618": "611",
    "619": "611",
    "621": "611",
    "622": "611",
    "623": "611",
    "624": "611",
    "625": "611",
    "626": "611",
    "627": "611",
    "628": "611",
    "629": "611",
    "631": "611",
    "632": "611",
    "633": "611",
    "634": "611",
    "635": "611",
    "636": "611",
    "637": "611",
    "638": "611",
    "639": "611",
    "641": "611",
    "642": "611",
    "643": "611",
    "644": "611",
    "645": "611",
    "646": "611",
    "647": "611",
    "648": "611",
    "649": "611",
    "651": "611",
    "652": "611",
    "653": "611",
    "654": "611",
    "655": "611",
    "656": "611",
    "657": "611",
    "658": "611",
    "659": "611",
    "661": "611",
    "662": "611",
    "663": "611",
    "664": "611",
    "665": "611",
    "666": "611",
    "667": "611",
    "668": "611",
    "669": "611",
    "671": "611",
    "672": "612",
    "673": "611",
    "674": "611",
    "675": "611",
    "676": "611",
    "677": "611",
    "678": "611",
    "679": "611",
    "681": "611",
    "682": "611",
    "683": "611",
    "684": "611",
    "685": "611",
    "686": "611",
    "687": "611",
    "688": "611",
    "689": "611",
    "691": "611",
    "692": "611",
    "693": "611",
    "694": "611",
    "695": "611",
    "696": "611",
    "697": "611",
    "698": "611",
    "699": "611",
    "700": "741",
    "735": "756",
    "736": "741",
    "741": "741",
    "742": "741",
    "743": "741",
    "744": "741",
    "745": "741",
    "746": "741",
    "748": "741",
    "749": "741",
    "751": "756",
    "753": "756",
    "754": "756",
    "756": "756",
    "757": "756",
    "758": "756",
}


def motion_set_for(weapon_type: str) -> str:
    """The motion folder a weapon set is animated from, or the set itself."""
    ws = (weapon_type or "").strip()
    return WEAPON_MOTION_SET.get(ws, ws)


#: Sockets a body may not carry; fall back to these in order.
SOCKET_FALLBACK = {
    "v_head": ("v_armet",),
    "v_l_shield": ("v_l_weapon",),
    "v_r_shield": ("v_r_weapon",),
    "v_misc": ("v_back", "v_body"),
    "v_pelvis": ("v_body",),
}

IDENTITY: Mat4 = fx.IDENTITY


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """One `PHY` chunk plus the `MOTI` that follows it, if any.

    `graphic.dll!MeshCreate` (0x28360) builds a C3Mesh from a file and then
    `MotionCreate`s a motion set from the *same* file; the mesh keeps the phy
    array at `+0x168` and the motion set at `+0x2D0`.  Index i of the motion
    set belongs to index i of the phy array -- `sub_86C0` (Role3D 0x86C0)
    looks a name up in the phy array and uses the returned index on the motion
    set without any remapping.
    """
    index: int
    phy: "c3phy.PhyMesh"
    motion: Optional["fx.Motion"] = None

    @property
    def name(self) -> str:
        return self.phy.name

    def matrix(self, bone: int = 0, frame: int = 0) -> Mat4:
        if self.motion is None:
            return IDENTITY
        return self.motion.matrix(bone, frame)


@dataclass
class PartMesh:
    """A parsed `.c3`: the phy chunks, their motions, and the name index."""
    logical: str
    chunks: list[Chunk] = field(default_factory=list)
    #: extra chunk tags seen (CAME, PTCL, ...), for reporting only
    other: list[bytes] = field(default_factory=list)

    # -- loading ----------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes, logical: str = "<memory>") -> "PartMesh":
        """Two independent ordinal lists, NOT adjacent pairs.

        `MeshCreate` (graphic.dll `0x28360`) builds the phy array and then
        calls `MotionCreate` on the *same file* to build a separate motion
        array; neither reader looks at the other's position.  Pairing a `MOTI`
        with the `PHY` that happens to precede it is therefore wrong, and it
        really does break: `c3/mount/850/8500000.c3` stores all eight `PHY`
        chunks first and all eight `MOTI` chunks after them.
        """
        m = cls(logical)
        phys: list = []
        motions: list = []
        for tag, body in c3phy.iter_chunks(data):
            if tag in c3phy.VARIANTS:
                phys.append(c3phy.parse_phy(tag, body))
            elif tag == b"MOTI":
                try:
                    motions.append(fx.parse_moti(body))
                except Exception:
                    motions.append(None)
            else:
                m.other.append(tag)
        for i in range(max(len(phys), len(motions))):
            phy = phys[i] if i < len(phys) else _EmptyPhy(i)
            mo = motions[i] if i < len(motions) else None
            m.chunks.append(Chunk(i, phy, mo))
        return m

    @classmethod
    def load(cls, root: AssetRoot, logical: str) -> "PartMesh":
        return cls.parse(root.read(logical), logical)

    # -- the engine's own accessors ---------------------------------------
    def find(self, name: str) -> int:
        """`C3Mesh::FindPhyByName` -- graphic.dll `0x266D0`, vtable slot `+0x78`.

        Linear scan over the phy array comparing with the engine's own
        case-insensitive compare (`0x841B0`); returns `-1` when absent.
        """
        low = name.lower()
        for c in self.chunks:
            if c.name and c.name.lower() == low:
                return c.index
        return -1

    def motion_for(self, index: int) -> Optional["fx.Motion"]:
        if 0 <= index < len(self.chunks):
            return self.chunks[index].motion
        return None

    def bind_chunks(self) -> Iterator[Chunk]:
        for c in self.chunks:
            if getattr(c.phy, "vertices", None):
                yield c

    # -- geometry ---------------------------------------------------------
    def world_vertices(self, world: Mat4 = IDENTITY, frame: int = 0,
                       skip_sockets: bool = True) -> Iterator[tuple]:
        """Every vertex of every drawable chunk, in world space.

        `p' = (sum_k w_k * (p x Mbone_k)) x world`, exactly the composition in
        `Phy_Calculate` (graphic.dll `0x56040`) followed by the mesh world
        matrix that `C3Mesh::SetMatrix` (`0x27B30`) hands each phy.
        """
        for c in self.bind_chunks():
            if skip_sockets and is_socket_name(c.name):
                continue
            q = copy.deepcopy(c.phy)
            c3phy.apply_matrix_to(q)         # the chunk's own 4x4, as Phy_Load does
            for v in q.vertices:
                yield transform_vertex(v, c.motion, world, frame)

    def bbox(self, world: Mat4 = IDENTITY, frame: int = 0,
             skip_sockets: bool = True) -> Optional[tuple]:
        lo = [1e30] * 3
        hi = [-1e30] * 3
        n = 0
        for p in self.world_vertices(world, frame, skip_sockets):
            n += 1
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
        return None if not n else (tuple(lo), tuple(hi))


class _EmptyPhy:
    """Placeholder so a motion-only chunk still occupies an index."""
    def __init__(self, i: int):
        self.name = ""
        self.vertices: list = []
        self.vertex_count = 0


#: The 52 `[Dumy]` names, lowercased.  Filled from `ini/RolePart.ini` the first
#: time it is needed; the literal fallback is that file's contents in this build.
_DUMY: set[str] = set()

_DUMY_FALLBACK = """V_ARMET_EFFECT01 V_ARMET_EFFECT02 v_armet v_back v_head
v_l_flap v_l_foot v_l_forearm v_l_leg v_l_shield v_l_shoulder v_l_weapon
v_mantle v_misc v_mount v_pelvis v_pet v_r_leg v_r_flap v_r_foot v_r_forearm
v_r_shield v_r_shoulder v_r_weapon v_rootloc v_wsocket1 v_wsocket2 v_wsocket3
v_zero v_mount_01 v_slot v_r_slot01 v_r_slot02 v_l_slot01 v_l_slot02 v_l_arm
v_r_arm""".split() + [f"v_extend{i}" for i in range(1, 16)]


def dumy_names(root: Path | str = DEFAULT_ROOT) -> set[str]:
    """`ini/RolePart.ini [Dumy]`, lowercased.  52 entries in this build."""
    global _DUMY
    if _DUMY:
        return _DUMY
    try:
        cfg = parse_ini(Path(root) / "ini" / "RolePart.ini")
        vals = [v for k, v in cfg.get("Dumy", {}).items()
                if k.lower() != "count" and v]
        _DUMY = {v.lower() for v in vals}
    except Exception:
        _DUMY = set()
    if not _DUMY:
        _DUMY = {n.lower() for n in _DUMY_FALLBACK}
    return _DUMY


def is_socket_name(name: str) -> bool:
    """True for a `[Dumy]` marker chunk, i.e. an attachment point rather than
    visible geometry.

    Membership of the `[Dumy]` list is the test, not a `v_` prefix: hair meshes
    ship chunks called `v_armet01` / `v_armet02` that are real geometry, and
    `v_body` is the body itself.  The engine does not use names for this at all
    -- it carries an explicit per-phy hide flag at `C3Mesh+0x268+i`, checked in
    `Mesh::Draw` (graphic.dll `0x25F53`) and exposed as vtable slot `+0x38`
    (`0x26AC0`) -- but that flag is set by the packed exe, so a name test is
    the best available reconstruction.
    """
    n = (name or "").lower()
    return n != "v_body" and n in dumy_names()


# ---------------------------------------------------------------------------
# the composition
# ---------------------------------------------------------------------------

def transform_vertex(v, motion, world: Mat4 = IDENTITY, frame: int = 0) -> tuple:
    """Skin one vertex and push it through `world`.

    Two influences maximum, and the second weight is `1 - weight0` -- the
    engine derives it with `not al` at graphic.dll `0x5A869`, so the pair is
    constrained to sum to 1 (docs/modding.md 9.3).
    """
    p = (v.px, v.py, v.pz)
    if motion is None:
        x, y, z = p
    else:
        x, y, z = fx.transform_point(motion.matrix(v.bone0, frame), p)
        if v.weight1:
            x1, y1, z1 = fx.transform_point(motion.matrix(v.bone1, frame), p)
            w = v.weight0
            x = x * w + x1 * (1 - w)
            y = y * w + y1 * (1 - w)
            z = z * w + z1 * (1 - w)
    return fx.transform_point(world, (x, y, z))


def socket_matrix(body: PartMesh, dumy: str, frame: int = 0,
                  motion_set: Optional[PartMesh] = None) -> Optional[Mat4]:
    """The world matrix of one `[Dumy]` socket on a body mesh.

    `Role3D!sub_86C0` (0x86C0) resolves it: `FindPhyByName(dumy)` on each of
    the role's meshes, then the motion at that same index; `Role3D!sub_8E60`
    (0x8F39) evaluates it with `Motion_GetMatrix(motion, bone=0, ...)`.
    Bone 0 always, because a socket chunk is rigid and every one of its
    vertices carries `BLENDINDICES0 == 0`.

    `motion_set` is an action motion `.c3` (from `ini/3dmotion.ini`) bound over
    the mesh with `C3Mesh::SetMotion` (graphic.dll `0x277C0`); when given, its
    chunk *i* replaces the mesh's own chunk *i*.
    """
    names = [dumy] + list(SOCKET_FALLBACK.get(dumy.lower(), ()))
    for n in names:
        idx = body.find(n)
        if idx < 0:
            continue
        mo = None
        if motion_set is not None:
            mo = motion_set.motion_for(idx)
        if mo is None:
            mo = body.motion_for(idx)
        if mo is None:
            return IDENTITY
        return mo.matrix(0, frame)
    return None


def world_for_part(socket: Optional[Mat4], role: Mat4 = IDENTITY) -> Mat4:
    """`Msocket x Mrole`.

    The chain is assembled in `Role3D!sub_2F20` (0x3058):
    `D3DXMatrixMultiply(&M, &Msocket, &M)` per attachment link, innermost
    first, then `Role::SetMatrix` (0xA4B0) folds in the role's own local
    matrix.  Row-vector throughout -- `D3DXMatrixMultiply(out, A, B)` is
    `out = A*B` and D3DX row-vectors mean A is applied first.
    """
    if socket is None:
        return role
    return fx.mat_mul(socket, role)


def mat_scale_rot(m: Mat4) -> tuple:
    """(sx, sy, sz, rotation-angle-degrees, translation) of a 4x4."""
    sx = math.dist(m[0:3], (0.0, 0.0, 0.0))
    sy = math.dist(m[4:7], (0.0, 0.0, 0.0))
    sz = math.dist(m[8:11], (0.0, 0.0, 0.0))
    if min(sx, sy, sz) < 1e-9:
        return sx, sy, sz, 0.0, tuple(m[12:15])
    r = (m[0] / sx, m[5] / sy, m[10] / sz)
    t = max(-1.0, min(3.0, r[0] + r[1] + r[2]))
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (t - 1.0) / 2.0))))
    return sx, sy, sz, ang, tuple(m[12:15])


def to_render(m: Mat4) -> list:
    """C3 -> viewer space.  Render space is C3 with Z negated
    (docs/modding.md 9.7), so the matrix conjugates: `S M S`, `S = diag(1,1,-1)`.
    Returned column-major (GL / `parts.Anchor.matrix` order)."""
    s = (m[0], m[1], -m[2],
         m[4], m[5], -m[6],
         -m[8], -m[9], m[10],
         m[12], m[13], -m[14])
    return [s[0], s[1], s[2], 0.0,
            s[3], s[4], s[5], 0.0,
            s[6], s[7], s[8], 0.0,
            s[9], s[10], s[11], 1.0]


# ---------------------------------------------------------------------------
# asset resolution (thin, and deliberately reusing what already exists)
# ---------------------------------------------------------------------------

class Catalogue:
    """`ini/*.ini` -> mesh paths, plus the idle action motion for a body."""

    BODIES = {"001": "001131000", "002": "002135000",
              "003": "003133000", "004": "004134000"}

    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.assets = AssetRoot(root)
        self.root = self.assets.root
        self.obj: dict[str, str] = {}
        self._load_flat("3dobj.ini", self.obj)
        self.motion: dict[str, str] = {}
        self._load_flat("3dmotion.ini", self.motion)
        self._tables: dict[str, dict] = {}
        self._cache: dict[str, PartMesh] = {}

    def _load_flat(self, ini_name: str, into: dict) -> None:
        r"""A flat `key=value` table, **preferring the compiled twin**.

        This is the stale-decoy trap in its second hiding place. Official
        6090-era clients ship `3dmotion.ini` and `3dobj.ini` stamped 2009
        beside `.dbc` twins stamped 2015, and the client reads the twins.
        `tools/anim.py` was taught this; this Catalogue kept its own
        independent load and was not, so `idle_motion` looked up key
        `2000100` in a table that has not carried it for years, got None,
        and every static preview fell back to the mesh's embedded MOTI --
        while the body itself drew unposed. Two different spaces, and a
        headgear offset of (1.1, -2.9, 2.1) that vanished the moment an
        animation played, because playing one supplied the motion the
        lookup had failed to find.

        The dbc keys are rebuilt from each row's path, not its integer id,
        for the reason `anim.MotionIndex` documents: the ini key space
        strips the shape's leading zeros and `int()` cannot round-trip it.
        """
        p = self.root / "ini" / ini_name
        if p.is_file():
            for line in p.read_text("latin-1", errors="replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    into[k.strip()] = v.strip().replace("\\", "/")
        twin = p.with_suffix(".dbc")
        if not twin.is_file():
            return
        try:
            import dbc as dbcmod
            rows = dbcmod.Rsdb.parse(twin.read_bytes()).paths
        except Exception:                                 # pragma: no cover
            return
        pat = re.compile(r"^c3/(\d{4})/(\d{3})/(\d{1,3})\.c3$", re.I)
        for rid, val in rows.items():
            val = val.replace("\\", "/")
            m = pat.match(val)
            if m:
                into[str(int(m.group(1))) + m.group(2) +
                     m.group(3).zfill(3)] = val
            else:
                into[str(rid)] = val

    def table(self, ini: str) -> dict:
        if ini not in self._tables:
            p = self.root / "ini" / ini
            self._tables[ini] = parse_ini(p) if p.is_file() else {}
        return self._tables[ini]

    def mesh_path(self, mesh_id: str) -> Optional[str]:
        if not mesh_id or mesh_id == "0":
            return None
        if mesh_id in self.obj:
            return self.obj[mesh_id]
        loc = self.assets.resolve_asset(mesh_id, "mesh")
        return loc.logical if loc else None

    def appearance_mesh(self, ini: str, appearance: str) -> Optional[str]:
        sec = self.table(ini).get(appearance)
        if not sec:
            return None
        return self.mesh_path(sec.get("Mesh0", ""))

    def load(self, logical: str) -> Optional[PartMesh]:
        if logical in self._cache:
            return self._cache[logical]
        try:
            m = PartMesh.load(self.assets, logical)
        except Exception:
            return None
        self._cache[logical] = m
        return m

    def idle_motion(self, body_appearance: str, weapon_type: str = "000",
                    action: str = "100") -> Optional[PartMesh]:
        """`ini/3dmotion.ini` key `<bodyDigit><weaponType3><action3>`.

        VERIFIED from the data: `2000100 = c3/0002/000/100.c3`, and every value
        under `c3/000N/` is `<weaponType>/<action>.c3`.  The chunk list of that
        file mirrors the body mesh's chunk list name-for-name and index-for-
        index, which is what `C3Mesh::SetMotion` (0x277C0) requires.
        """
        if len(body_appearance) < 3:
            return None
        shape = int(body_appearance[:3])
        path = self.motion.get(f"{shape}{weapon_type}{action}")
        if not path:
            # 6090 dropped the per-(set, action) alias rows CCO ships, so an
            # armed key misses and the caller would silently get the unarmed
            # idle. Map the set to its motion folder and try that.
            alias = motion_set_for(weapon_type)
            if alias != weapon_type:
                path = self.motion.get(f"{shape}{alias}{action}")
        return self.load(path) if path else None


# ---------------------------------------------------------------------------
# the assembled figure
# ---------------------------------------------------------------------------

@dataclass
class Placed:
    slot: str
    appearance: str
    logical: str
    socket: Optional[str]
    socket_source: str
    world: Mat4
    bbox: Optional[tuple]
    own_scale: tuple
    own_rotation: float


class Figure:
    """A body plus equipped parts, composed the way the client composes them."""

    INI = {"body": "armor.ini", "armet": "armet.ini", "armet_dx8": "armet1.ini",
           "l_weapon": "weapon.ini", "r_weapon": "weapon.ini",
           "misc": "misc.ini", "mount": "mount.ini", "head": "head.ini",
           "shield": "shield.ini", "pelvis": "pelvis.ini"}

    def __init__(self, cat: Catalogue, body: str, frame: int = 0,
                 use_action_motion: bool = False, weapon_type: str = "000",
                 plugin=None):
        #: The parser plugin, so `equip` asks `socket_for` rather than the
        #: raw `SLOT_SOCKET` default. Without it a plugin's "" -- "this
        #: client cannot attach that slot" -- is silently ignored and the
        #: part is hung off a socket the bodies do not carry.
        self.plugin = plugin
        self.cat = cat
        self.frame = frame
        self.body_appearance = body
        self.body_path = cat.appearance_mesh("armor.ini", body) or \
            cat.mesh_path(body) or f"c3/mesh/{body}.c3"
        self.body = cat.load(self.body_path)
        self.body_motion = cat.idle_motion(body, weapon_type) if use_action_motion else None
        self.parts: list[Placed] = []

    def body_bbox(self) -> Optional[tuple]:
        if self.body is None:
            return None
        return self.body.bbox(IDENTITY, self.frame)

    def height(self) -> float:
        bb = self.body_bbox()
        return 0.0 if bb is None else (-bb[0][2]) - (-bb[1][2])

    def equip(self, slot: str, appearance: str) -> Optional[Placed]:
        if self.body is None:
            return None
        ini = self.INI.get(slot)
        logical = self.cat.appearance_mesh(ini, appearance) if ini else None
        if logical is None:
            logical = self.cat.mesh_path(appearance)
        if logical is None:
            return None
        part = self.cat.load(logical)
        if part is None:
            return None
        dumy = socket_for(slot, self.plugin)
        src = "none"
        S: Optional[Mat4] = IDENTITY
        if dumy:
            S = socket_matrix(self.body, dumy, self.frame, self.body_motion)
            if S is None:
                src = f"{dumy}: ABSENT on this body"
                S = IDENTITY
            else:
                idx = self.body.find(dumy)
                which = ("idle action motion" if self.body_motion
                         else "the mesh's own MOTI")
                src = (f"{dumy} = chunk {idx} bone 0 frame {self.frame} "
                       f"of {which}" if idx >= 0 else f"{dumy} (fallback)")
        world = world_for_part(S)
        first = next(iter(part.bind_chunks()), None)
        sc = mat_scale_rot(first.matrix(0, self.frame)) if first else (1, 1, 1, 0, (0, 0, 0))
        p = Placed(slot, appearance, logical, dumy, src, world,
                   part.bbox(world, self.frame), sc[:3], sc[3])
        self.parts.append(p)
        return p


# ---------------------------------------------------------------------------
# reporting / validation
# ---------------------------------------------------------------------------

def _fmt_bbox(bb) -> str:
    if bb is None:
        return "(empty)"
    lo, hi = bb
    return ("x[%7.1f,%7.1f] y[%7.1f,%7.1f] up[%7.1f,%7.1f]"
            % (lo[0], hi[0], lo[1], hi[1], -hi[2], -lo[2]))


def cmd_sockets(cat: Catalogue, body_id: str, frame: int, action: bool) -> None:
    fig = Figure(cat, body_id, frame, use_action_motion=action)
    if fig.body is None:
        print("cannot load", fig.body_path)
        return
    print(f"{fig.body_path}   height {fig.height():.1f}   frame {frame}"
          + ("   (action motion bound)" if fig.body_motion else ""))
    for c in fig.body.chunks:
        if not c.name:
            continue
        role = "socket" if is_socket_name(c.name) else "geometry"
        mo = c.motion
        S = socket_matrix(fig.body, c.name, frame, fig.body_motion)
        sc = mat_scale_rot(S) if S else None
        line = ("  [%2d] %-14s %-8s verts=%-5d bones=%-3s"
                % (c.index, c.name, role, len(c.phy.vertices),
                   mo.bone_count if mo else "-"))
        if sc:
            line += ("  origin (%8.2f,%8.2f,%8.2f)  rot %5.1f deg  scale %.3f"
                     % (sc[4][0], sc[4][1], -sc[4][2], sc[3], sc[0]))
        print(line)


def cmd_figure(cat: Catalogue, args) -> None:
    fig = Figure(cat, args.body, args.frame, use_action_motion=args.action)
    if fig.body is None:
        print("cannot load body", fig.body_path)
        return
    print(f"body {args.body}  {fig.body_path}")
    print("     height %.1f   %s" % (fig.height(), _fmt_bbox(fig.body_bbox())))
    for slot, val in (("armet", args.armet), ("armet_dx8", args.armet_dx8),
                      ("l_weapon", args.l_weapon), ("r_weapon", args.r_weapon),
                      ("misc", args.misc), ("mount", args.mount)):
        if not val:
            continue
        p = fig.equip(slot, val)
        if p is None:
            print(f"{slot:>10} {val}  -- unresolved")
            continue
        print(f"{slot:>10} {p.appearance}  {p.logical}")
        print(f"           socket {p.socket_source}")
        print("           own MOTI scale (%.3f,%.3f,%.3f) rot %.1f deg"
              % (p.own_scale[0], p.own_scale[1], p.own_scale[2], p.own_rotation))
        print("           placed  " + _fmt_bbox(p.bbox))
        if p.bbox:
            span = max(p.bbox[1][i] - p.bbox[0][i] for i in range(3))
            print("           longest span %.1f  = %.2f x body height"
                  % (span, span / max(fig.height(), 1e-6)))


def _iter_appearances(cat: Catalogue, ini: str, limit: Optional[int] = None):
    seen: set[str] = set()
    n = 0
    for name, sec in cat.table(ini).items():
        if not name.isdigit():
            continue
        mid = sec.get("Mesh0")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        path = cat.mesh_path(mid)
        if not path:
            continue
        yield name, mid, path
        n += 1
        if limit and n >= limit:
            return


def cmd_motis(cat: Catalogue, limit: Optional[int]) -> None:
    """What the part's own bone-0 MOTI matrix actually contains, per table."""
    print("Own-MOTI bone-0 matrix at frame 0, per appearance table.")
    print("A viewer that ignores it drops every number in the last three columns.\n")
    print("%-12s %6s %8s %8s %8s %8s %8s" %
          ("table", "meshes", "scale~=1", "non-unif", "rot>5deg", "rot>45", "medScale"))
    for ini in ("armet.ini", "armet1.ini", "weapon.ini", "armor.ini", "mount.ini"):
        rows = []
        for name, mid, path in _iter_appearances(cat, ini, limit):
            m = cat.load(path)
            if m is None:
                continue
            c = next(iter(m.bind_chunks()), None)
            if c is None or c.motion is None:
                continue
            rows.append(mat_scale_rot(c.matrix(0, 0)))
        if not rows:
            print("%-12s %6d  (none)" % (ini, 0))
            continue
        sc = [r[0] for r in rows]
        print("%-12s %6d %8d %8d %8d %8d %8.3f" % (
            ini, len(rows),
            sum(1 for r in rows if abs(r[0] - 1) > 0.01),
            sum(1 for r in rows if abs(r[0] - r[1]) > 0.01 * max(r[0], 1e-9)),
            sum(1 for r in rows if r[3] > 5),
            sum(1 for r in rows if r[3] > 45),
            statistics.median(sc)))


def cmd_validate(cat: Catalogue, limit: Optional[int]) -> int:
    """Numeric checks across all four body types and the shipped part tables.

    Every number quoted in `docs/attachment.md` comes out of here.
    """
    import collections
    bad = 0
    heights: dict[str, float] = {}

    print("=" * 76)
    print("1. sockets, per body type  (frame 0 of the mesh's own MOTI)")
    print("=" * 76)
    for bt, app in sorted(Catalogue.BODIES.items()):
        fig = _fig_cache(cat, bt)
        if fig is None:
            print("  %-4s %-11s MISSING" % (bt, app)); bad += 1; continue
        h = fig.height(); heights[bt] = h
        cells = []
        for dumy in ("v_armet", "v_l_weapon", "v_r_weapon"):
            S = socket_matrix(fig.body, dumy, 0)
            cells.append("-" if S is None
                         else "%.1f" % -mat_scale_rot(S)[4][2])
        print("  %-4s %-11s height %6.1f   v_armet z=%-7s l_weapon z=%-7s "
              "r_weapon z=%s" % (bt, app, h, cells[0], cells[1], cells[2]))
        S = socket_matrix(fig.body, "v_armet", 0)
        if S is None or not (0.85 * h <= -mat_scale_rot(S)[4][2] <= 1.05 * h):
            print("      !! v_armet is not near the top of the body"); bad += 1

    print()
    print("=" * 76)
    print("2. binding the idle action motion from ini/3dmotion.ini")
    print("   key <bodyDigit>000100 -> c3/000N/000/100.c3.  C3Mesh::SetMotion")
    print("   (graphic.dll 0x277C0) binds BY INDEX, so the chunk lists must line up.")
    print("=" * 76)
    for bt, app in sorted(Catalogue.BODIES.items()):
        fig = _fig_cache(cat, bt)
        act = cat.idle_motion(app, "000", "100")
        if fig is None or act is None:
            print("  %-4s no idle motion" % bt); continue
        mn = [c.name for c in fig.body.chunks]
        an = [c.name for c in act.chunks]
        aligned = len(an) >= len(mn) and all(
            a.lower() == b.lower() for a, b in zip(mn, an))
        print("  %-4s chunks %2d/%2d  order %s   %s"
              % (bt, len(an), len(mn), "MATCHES" if aligned else "MISALIGNED",
                 ",".join(mn)))
        if not aligned:
            bad += 1
        cells = []
        for dumy in ("v_armet", "v_l_weapon", "v_r_weapon"):
            a = socket_matrix(fig.body, dumy, 0)
            b = socket_matrix(fig.body, dumy, 0, act)
            if a is None or b is None:
                continue
            cells.append("%s own(%.0f,%.0f) idle(%.0f,%.0f)"
                         % (dumy, a[12], -a[14], b[12], -b[14]))
        print("       embedded MOTI vs idle motion, (x, up):  " + "   ".join(cells))

    print()
    print("=" * 76)
    print("3. skinning sanity: the body's own MOTI at frame 0 vs the stored pose")
    print("=" * 76)
    for bt, app in sorted(Catalogue.BODIES.items()):
        fig = _fig_cache(cat, bt)
        if fig is None:
            continue
        c = next((c for c in fig.body.bind_chunks()
                  if c.name.lower() == "v_body"), None)
        if c is None or c.motion is None:
            continue
        q = copy.deepcopy(c.phy); c3phy.apply_matrix_to(q)
        raw = [-v.pz for v in q.vertices]
        sk = [-transform_vertex(v, c.motion, IDENTITY, 0)[2] for v in q.vertices]
        d = sorted(abs(a - b) for a, b in zip(raw, sk))
        dz = abs(max(raw) - max(sk))
        print("  %-4s %-11s stored z[%6.1f,%6.1f]  posed z[%6.1f,%6.1f]  "
              "height delta %4.2f  per-vertex median %5.2f p90 %5.2f"
              % (bt, app, min(raw), max(raw), min(sk), max(sk), dz,
                 d[len(d) // 2], d[9 * len(d) // 10]))
        if dz > 1.5:
            print("      !! posing changes the height"); bad += 1

    print()
    print("=" * 76)
    print("4. head parts (armet.ini) by series: how far the top rises above")
    print("   the head, once socket + own MOTI are applied.  119 = modern hair.")
    print("=" * 76)
    by = collections.defaultdict(list)
    seen: set = set()
    for name, sec in cat.table("armet.ini").items():
        if not (name.isdigit() and len(name) == 9):
            continue
        bt = name[:3]
        if bt not in Catalogue.BODIES:
            continue
        path = cat.mesh_path(sec.get("Mesh0", ""))
        if not path or (bt, path) in seen:
            continue
        seen.add((bt, path))
        fig = _fig_cache(cat, bt)
        part = cat.load(path)
        if fig is None or part is None:
            continue
        S = socket_matrix(fig.body, "v_armet", 0)
        bb = part.bbox(world_for_part(S), 0)
        bb0 = _bbox_no_motion(part, world_for_part(S))
        if bb is None or bb0 is None:
            continue
        # how far the own MOTI actually moves the geometry, as a fraction of
        # the part's own size -- the direct measure of "hair is mis-oriented"
        S2 = world_for_part(S)
        size = max(1e-6, max(bb[1][i] - bb[0][i] for i in range(3)))
        disp = 0.0
        for c in part.bind_chunks():
            if is_socket_name(c.name):
                continue
            q = copy.deepcopy(c.phy); c3phy.apply_matrix_to(q)
            for v in q.vertices[::7]:
                a1 = transform_vertex(v, c.motion, S2, 0)
                a0 = fx.transform_point(S2, (v.px, v.py, v.pz))
                disp = max(disp, math.dist(a1, a0))
        by[name[3:6]].append((-bb[0][2] - heights.get(bt, 0.0),
                              -bb0[0][2] - heights.get(bt, 0.0), disp / size))
        if limit and len(seen) >= limit:
            break
    for ser in sorted(by, key=lambda s: -len(by[s])):
        v = sorted(x[0] for x in by[ser])
        w = sorted(x[1] for x in by[ser])
        d = sorted(x[2] for x in by[ser])
        print("  series %-4s n=%-5d with MOTI: med %6.1f p90 %6.1f   "
              "without: med %6.1f p90 %6.1f   MOTI moves >10%% of the part "
              "on %d, >50%% on %d"
              % (ser, len(v), v[len(v) // 2], v[9 * len(v) // 10],
                 w[len(w) // 2], w[9 * len(w) // 10],
                 sum(1 for x in d if x > 0.10), sum(1 for x in d if x > 0.50)))

    print()
    print("=" * 76)
    print("5. weapons (weapon.ini) by type, on body 002135000 at v_r_weapon:")
    print("   longest span, with the part's own MOTI and with it dropped")
    print("=" * 76)
    fig = _fig_cache(cat, "002")
    h = heights.get("002", 1.0)
    W = world_for_part(socket_matrix(fig.body, "v_r_weapon", 0))
    rows = []
    seen = set()
    for name, sec in cat.table("weapon.ini").items():
        if not name.isdigit():
            continue
        mid = sec.get("Mesh0")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        path = cat.mesh_path(mid)
        if not path:
            continue
        part = cat.load(path)
        if part is None:
            continue
        a = part.bbox(W, 0)
        b = _bbox_no_motion(part, W)
        if not a or not b:
            continue
        c = next(iter(part.bind_chunks()))
        sc = mat_scale_rot(c.matrix(0, 0))
        rows.append((name[:3], max(a[1][i] - a[0][i] for i in range(3)),
                     max(b[1][i] - b[0][i] for i in range(3)), sc[0], sc[3]))
        if limit and len(rows) >= limit:
            break
    grp = collections.defaultdict(list)
    for r in rows:
        grp[r[0]].append(r)
    print("  %-5s %5s %10s %10s %9s %8s" %
          ("type", "n", "med_span", "med_dropped", "medScale", "medRot"))
    for t in sorted(grp, key=lambda t: -len(grp[t])):
        v = grp[t]
        med = statistics.median([r[1] for r in v])
        print("  %-5s %5d %10.1f %10.1f %9.3f %8.1f   (%.2f x body)"
              % (t, len(v), med, statistics.median([r[2] for r in v]),
                 statistics.median([r[3] for r in v]),
                 statistics.median([r[4] for r in v]), med / h))
    if rows:
        aw = statistics.median([r[1] for r in rows])
        ao = statistics.median([r[2] for r in rows])
        print("  ALL   %5d %10.1f %10.1f" % (len(rows), aw, ao))
        print("  rotation in the part's own MOTI: >5 deg on %d of %d, "
              ">45 deg on %d" % (sum(1 for r in rows if r[4] > 5), len(rows),
                                 sum(1 for r in rows if r[4] > 45)))
        print("  scale != 1 (>1%%) on %d of %d"
              % (sum(1 for r in rows if abs(r[3] - 1) > 0.01), len(rows)))

    print()
    print("=" * 76)
    print("6. chunk layout: PHY and MOTI are two ordinal lists, not adjacent")
    print("   pairs.  C3Mesh::SetMotion (0x27818) does phy[i]->motion =")
    print("   motionSet->Get(i).  Files that group all PHY then all MOTI break")
    print("   any reader that pairs a MOTI with the PHY before it.")
    print("=" * 76)
    for ini in ("armor.ini", "armet.ini", "weapon.ini", "mount.ini"):
        st = collections.Counter()
        seen = set()
        for name, sec in cat.table(ini).items():
            mid = sec.get("Mesh0")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            path = cat.mesh_path(mid)
            if not path:
                continue
            try:
                data = cat.assets.read(path)
            except Exception:
                continue
            seq = "".join("P" if t in c3phy.VARIANTS else
                          ("M" if t == b"MOTI" else "")
                          for t, _ in c3phy.iter_chunks(data))
            np_, nm = seq.count("P"), seq.count("M")
            if np_ != nm:
                st["PHY/MOTI count mismatch"] += 1
            if np_ < 2:
                st["single chunk (order irrelevant)"] += 1
            elif seq == "P" * np_ + "M" * nm:
                st["GROUPED - adjacent pairing FAILS"] += 1
            elif seq == "PM" * np_:
                st["interleaved"] += 1
            else:
                st["other order"] += 1
        print("  %-12s %s" % (ini, dict(st)))

    print()
    print("failed checks:", bad)
    return 1 if bad else 0



_FIGS: dict[str, Figure] = {}


def _fig_cache(cat: Catalogue, bt: str) -> Optional[Figure]:
    if bt not in _FIGS:
        app = Catalogue.BODIES.get(bt)
        if not app:
            return None
        _FIGS[bt] = Figure(cat, app, 0)
    f = _FIGS[bt]
    return f if f.body is not None else None


def _bbox_no_motion(part: PartMesh, world: Mat4):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    n = 0
    for c in part.bind_chunks():
        if is_socket_name(c.name):
            continue
        q = copy.deepcopy(c.phy); c3phy.apply_matrix_to(q)
        for v in q.vertices:
            p = fx.transform_point(world, (v.px, v.py, v.pz))
            n += 1
            for i in range(3):
                lo[i] = min(lo[i], p[i]); hi[i] = max(hi[i], p[i])
    return None if not n else (tuple(lo), tuple(hi))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--action", action="store_true",
                    help="bind the idle action motion from ini/3dmotion.ini")
    ap.add_argument("--body")
    ap.add_argument("--armet")
    ap.add_argument("--armet-dx8", dest="armet_dx8")
    ap.add_argument("--l-weapon", dest="l_weapon")
    ap.add_argument("--r-weapon", dest="r_weapon")
    ap.add_argument("--misc")
    ap.add_argument("--mount")
    ap.add_argument("--sockets", metavar="BODY")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--motis", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    cat = Catalogue(a.root)
    if a.sockets:
        cmd_sockets(cat, a.sockets, a.frame, a.action)
        return 0
    if a.motis:
        cmd_motis(cat, a.limit)
        return 0
    if a.validate:
        return cmd_validate(cat, a.limit)
    if a.body:
        cmd_figure(cat, a)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
