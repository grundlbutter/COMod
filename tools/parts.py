#!/usr/bin/env python3
r"""
parts.py -- the character part manifest, the socket list, and where an equipped
part actually goes.

Three separate things, with very different confidence levels. Read the honesty
notes; they matter more than the code.

1. THE PART MANIFEST -- VERIFIED, straight from ini/RolePart.ini
   `[Config] Count=13` enumerates every part the client composes a character
   from, each naming its mesh table and its motion table. Driving the equip
   slots off this file rather than a hardcoded list keeps the viewer honest to
   what the client actually assembles. Checked against disk in this build:

       armet     ini/armet.ini    2918   shipped
       armet_dx8 ini/armet1.ini    950   shipped   (disjoint id set from armet)
       body      ini/armor.ini    3418   shipped
       head      ini/head.ini        4   shipped   (one bare head per body type)
       l_weapon  ini/weapon.ini   5384   shipped
       r_weapon  ini/weapon.ini   5384   shipped
       misc      ini/misc.ini      272   shipped
       mount     ini/mount.ini    1317   shipped
       shield    ini/shield.ini      -   DECLARED BUT NOT SHIPPED
       pelvis    ini/pelvis.ini      -   DECLARED BUT NOT SHIPPED

   `mix_armet`, `mix_armet_dx8` and `mix_body` point at the *same* ini files as
   their non-mix counterparts, so there are really 4 head-covering catalogues
   exposed under 6 part names, not 6 catalogues.

2. THE SOCKETS -- VERIFIED as a list, from `[Dumy] Count=52`
   "Dumy" is 3DSMax dummy objects: attachment points. All 52 are listed below,
   and they match the submesh names that come out of the meshes themselves
   (`v_body`, `v_armet`, `v_l_weapon`, `v_r_weapon`, ...). Two of them --
   `V_ARMET_EFFECT01` / `V_ARMET_EFFECT02` -- exist specifically to hang
   effects off headgear, which is where task #13's data will attach.

3. WHERE A PART GOES -- **INFERRED, and this one is a real approximation.**
   The socket submeshes are physically present in every body mesh, but measuring
   them says they are *not* pre-placed:

       002135000  v_body    z 0.08 .. 170.59   (a whole standing figure)
                  v_armet   centroid (0, 0, 0)          <- at the feet, not the head
                  v_l_weapon centroid (1.9, 38.2, -0.5)
       every socket submesh is rigid and skinned to **bone 0**, and bone 0 is
       used by no vertex of `v_body` at all.

   So a socket's real transform comes from bone 0 of the `MOTI` animation
   track, and MOTI is still undecoded (docs/modding.md 9.9). There is no
   bind-pose bone matrix anywhere in the PHY data to read instead.

   Rather than not shipping assembly, this module derives an anchor from the
   body's own skin clusters: the weighted centroid of the vertices each bone
   drives. The highest of those is the head, and the extreme +X / -X ones in
   the upper body are the hands. That is a defensible estimate -- it is
   measured off the actual model, not typed in -- but it is an estimate, it
   carries no orientation, and the viewer says so on screen. When MOTI lands,
   `socket_anchors()` is the single function to replace.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import c3phy                                            # noqa: E402
from coassets import DEFAULT_ROOT, parse_ini            # noqa: E402
# `pad9` is imported HARD, not through the guarded `attachmod` further down:
# a fallback that shrugged when attach.py failed to import would put the
# seven-wide defect straight back (2026-09-25, tests/test_shape_width.py).
from attach import pad9                                  # noqa: E402

try:
    # task #13's workstream decoded MOTI. When it is importable we get the
    # engine's actual socket transforms instead of estimating them.
    import effects as effectsmod                        # noqa: E402
except Exception:                                       # pragma: no cover
    effectsmod = None

try:
    # task #18 recovered the client's own attachment composition out of
    # Role3D.dll / graphic.dll. Import it; never edit it.
    import attach as attachmod                          # noqa: E402
except Exception:                                       # pragma: no cover
    attachmod = None

#: Parts declared by RolePart.ini that have no ini file in this build.
#: Surfaced in the UI as "declared but not shipped" instead of an empty list.
UNSHIPPED_NOTE = "declared by RolePart.ini but the ini file is not in this build"

#: Slot presentation. `sockets` are the `[Dumy]` names the part hangs off.
SLOT_META: dict[str, dict] = {
    "body":      {"label": "Body", "socket": "v_body", "primary": True,
                  "note": "the subject: everything else attaches to this"},
    "armet":     {"label": "Head", "socket": "v_armet",
                  "note": "hair AND headgear share this slot -- see head_kind()"},
    "armet_dx8": {"label": "Head (dx8)", "socket": "v_armet",
                  "note": "the legacy DirectX 8 headgear catalogue, a disjoint id set"},
    "l_weapon":  {"label": "Left hand", "socket": "v_l_weapon"},
    "r_weapon":  {"label": "Right hand", "socket": "v_r_weapon"},
    "shield":    {"label": "Shield", "socket": "v_l_shield"},
    "mount":     {"label": "Mount", "socket": "v_mount"},
    "misc":      {"label": "Misc", "socket": "v_misc"},
    "head":      {"label": "Bare head", "socket": "v_head",
                  "note": "4 entries, one per body type"},
    "pelvis":    {"label": "Pelvis", "socket": "v_pelvis"},
}

#: The order the equip panel shows slots in. Body first: it is the subject.
SLOT_ORDER = ["body", "armet", "armet_dx8", "l_weapon", "r_weapon",
              "shield", "mount", "misc", "head", "pelvis"]

#: Slots that are mutually exclusive with each other -- the client draws one
#: head covering, and hair is simply what is there when no helmet is.
EXCLUSIVE_GROUPS = [["armet", "armet_dx8"]]

#: Hair, per the wiki's `Hairstyle Code = (Hair Colour x 100) + Hairstyle`.
#: VERIFIED against armet.ini: appearance `001111310` is body 001, series 111,
#: colour digit 3, style digit 1 -- and the whole colour family shares one mesh
#: (`001111010`) while the texture changes (`002111310` .. `002111910`), the
#: same mesh-shared/texture-per-colour pattern as body armour.
HAIR_COLOURS = {3: "black", 4: "white", 5: "red", 6: "brown",
                7: "green", 8: "blue", 9: "purple"}
#: Armour series inside the head-covering tables that are hairstyles rather
#: than helmets.
#:
#: **CORRECTED.** This used to say `{"111"}`, following docs/viewer.md §5. The
#: data says series 111 is a helmet family and 119 is the hair:
#:
#:     itemtype.json, items whose id starts with the series
#:       111  IronHelmet, BronzeHelmet, SilverHelmet, ShiningHelmet, ...  (10)
#:       112  ConquestHelmet, PhoenixHat, UltimateCap, MagicCoronet
#:       113  BadgerHat, CatHat, JackalHat, LeopardHat, MartenHat, ...   (10)
#:       114  DestinyCap, LacyCap, VeinCap, StoneCap, CloudCap, ...      (10)
#:       115  ChristmasCap, GiftHat        116  NewYearCap
#:       119  ** no items at all **
#:
#: and docs/attachment.md §8.3 measured how far each series sits above the top
#: of the skull: series 119 median **+4.0** units -- hair lying on the scalp --
#: against 111's +18.1, 113's +9.4 and 114's +11.0, which are hats. Series 111
#: additionally carries `requiredProfession 21` (Warrior), which a hairstyle
#: could not. Series 119 is also 515 of the 707 distinct armet meshes and is the
#: family that resolves into `c3/hair/`.
#:
#: The colour digit and the shared-mesh/one-texture-per-colour structure are the
#: same in both families, which is why the old reading looked plausible.
HAIR_SERIES = {"119"}


def head_kind(ident: str) -> str:
    """"hair" or "headgear" for an entry of the head-covering slot.

    The id is read NINE WIDE (`attach.pad9`) before the series is sliced
    out.  `armet.ini` is seven wide on 5017/5517/5065/7878/6609 and nine
    wide only on CCO, so read raw this said "headgear" for every hairstyle
    on those clients -- MEASURED 2026-09-25 on a 5017 BuilderIndex: armet
    kind `Counter({'headgear': 1708})`, and `default_loadout('002')` picked
    no armet because it asks this for "hair".  `builder.head_kind` is this
    function, not a copy of it.
    """
    wide = pad9(ident)
    if wide.isdigit() and len(wide) == 9 and wide[3:6] in HAIR_SERIES:
        return "hair"
    return "headgear"


def hair_colour(ident: str) -> Optional[str]:
    """The colour digit's name, read nine wide for the same reason as
    `head_kind`; None for a non-hair or non-numeric id."""
    wide = pad9(ident)
    if wide.isdigit() and len(wide) == 9:
        return HAIR_COLOURS.get(int(wide[6]))
    return None


@dataclass
class PartSlot:
    name: str
    label: str
    mesh_ini: str = ""
    motion_ini: str = ""
    socket: str = ""
    shipped: bool = False
    count: int = 0
    note: str = ""
    primary: bool = False
    body_specific: bool = False
    aliases: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)


class PartManifest:
    """ini/RolePart.ini, read as the authority on what a character is made of."""

    def __init__(self, root: Path = DEFAULT_ROOT, tables: Optional[dict] = None):
        self.root = Path(root)
        cfg = {}
        p = self.root / "ini" / "RolePart.ini"
        if p.is_file():
            cfg = parse_ini(p)
        self.raw = cfg
        self.sockets: list[str] = []
        d = cfg.get("Dumy", {})
        for k, v in d.items():
            if k.lower() != "count" and v:
                self.sockets.append(v)

        self.slots: dict[str, PartSlot] = {}
        conf = cfg.get("Config", {})
        try:
            n = int(conf.get("Count", "0") or 0)
        except ValueError:
            n = 0
        by_ini: dict[str, str] = {}
        for i in range(n):
            name = conf.get(f"Part{i}")
            mesh_ini = conf.get(f"MeshIni{i}", "")
            motion_ini = conf.get(f"MotionIni{i}", "")
            if not name:
                continue
            meta = SLOT_META.get(name, {})
            shipped = bool(mesh_ini) and (self.root / mesh_ini.replace("/", "\\")).is_file()
            slot = PartSlot(
                name=name, label=meta.get("label", name.replace("_", " ")),
                mesh_ini=mesh_ini, motion_ini=motion_ini,
                socket=meta.get("socket", ""), shipped=shipped,
                note=meta.get("note", "") if shipped else UNSHIPPED_NOTE,
                primary=meta.get("primary", False),
            )
            if tables is not None and name in tables:
                slot.count = len(tables[name])
            # mix_* variants point at the same ini as their base slot
            if mesh_ini and mesh_ini in by_ini and name.startswith("mix_"):
                self.slots[by_ini[mesh_ini]].aliases.append(name)
                continue
            if mesh_ini:
                by_ini.setdefault(mesh_ini, name)
            self.slots[name] = slot

    def ordered(self) -> list[PartSlot]:
        known = [self.slots[n] for n in SLOT_ORDER if n in self.slots]
        rest = [s for n, s in self.slots.items() if n not in SLOT_ORDER]
        return known + rest

    def to_json(self) -> dict:
        return {
            "slots": [s.to_json() for s in self.ordered()],
            "slotOrder": [s.name for s in self.ordered()],
            "sockets": self.sockets,
            "socketCount": len(self.sockets),
            "exclusive": EXCLUSIVE_GROUPS,
            "effectSockets": [s for s in self.sockets if "EFFECT" in s.upper()],
            "hairColours": {str(k): v for k, v in HAIR_COLOURS.items()},
            "hairSeries": sorted(HAIR_SERIES),
        }


# ---------------------------------------------------------------------------
# socket anchors
# ---------------------------------------------------------------------------

@dataclass
class Anchor:
    socket: str
    pos: tuple            # render space (x, y, -z), i.e. what the viewport uses
    source: str           # how it was derived
    bone: Optional[int] = None
    confidence: str = "inferred"
    #: full 4x4 (column-major, GL order) when the transform is known, not just
    #: the position. Only the MOTI path can supply orientation.
    matrix: Optional[list] = None


def _body_chunk(meshes):
    for m in meshes:
        if m.name.lower() == "v_body":
            return m
    # some npc/monster meshes name the main body differently: take the largest
    return max(meshes, key=lambda m: m.vertex_count, default=None)


def bone_clusters(body: "c3phy.PhyMesh") -> dict[int, tuple]:
    """Weighted centroid, in render space, of the vertices each bone drives.

    This is the substitute for a bind-pose skeleton: the bone matrices live in
    the undecoded MOTI track, but the *skin* is right here, and a bone sits
    inside the cluster of vertices it moves.
    """
    acc: dict[int, list[float]] = {}
    for v in body.vertices:
        pairs = [(v.bone0, v.weight0 if v.weight1 else 1.0)]
        if v.weight1:
            pairs.append((v.bone1, v.weight1))
        for b, w in pairs:
            if w <= 0:
                continue
            a = acc.setdefault(b, [0.0, 0.0, 0.0, 0.0])
            a[0] += v.px * w
            a[1] += v.py * w
            a[2] += (-v.pz) * w
            a[3] += w
    return {b: (a[0] / a[3], a[1] / a[3], a[2] / a[3]) for b, a in acc.items() if a[3]}


#: render space = C3 space with Z negated (see c3phy.to_blender)
def _flip(p):
    return (p[0], p[1], -p[2])


#: Cache for `ini/3dmotion.ini`, which is 6.9 MB / 229,481 rows -- parsing it
#: per request would dominate an equip round trip.
#:
#: **Keyed by root.** It used to be a single global built from whichever
#: install asked first, and every later call ignored its own `root` argument.
#: That was invisible while one client was ever configured and wrong the
#: moment two were: the viewer holds a catalogue per base so 6090 and 5517
#: can be compared, and after a switch the figure was posed from the previous
#: client's motion tables. It also silently corrupted a measurement in this
#: session -- a script that walked CCO, 5517 and 6090 in one process got
#: CCO's motions three times and reported all three clean.
_ACTION_CATS: dict = {}


def action_catalogue(root: Path = DEFAULT_ROOT):
    """`attach.Catalogue` for `root`, built once per root. None when
    attach.py is not importable."""
    if attachmod is None:
        return None
    try:
        key = str(Path(root).resolve())
    except OSError:                                       # pragma: no cover
        key = str(root)
    if key not in _ACTION_CATS:
        try:
            _ACTION_CATS[key] = attachmod.Catalogue(root)
        except Exception:                                 # pragma: no cover
            return None
    return _ACTION_CATS[key]


def idle_motion(body_appearance: str, root: Path = DEFAULT_ROOT,
                weapon_type: str = "000", action: str = "100"):
    r"""The body's idle action motion, `3dmotion.ini[<bodyDigit>000100]`.

    **This, not the mesh's embedded `MOTI`, is what a static preview should
    use** (docs/attachment.md §8.2). `Mesh::Draw` always applies a motion
    (`0x2607E`), so there is no bind pose to show; the embedded track is
    whatever pose the artist happened to save. On `004134000` that is a
    **T-pose** -- hands at x = ±98, up 154 -- against the idle pose's ±32, up 83.
    Bodies 001/002/003 agree between the two to within 1.3 units, which is
    exactly why a four-body sample did not catch it.

    Returns an `attach.PartMesh` whose chunk *i* supplies the motion for the
    body mesh's chunk *i* (ordinal binding, `C3Mesh::SetMotion` `0x27818`), or
    None when the lookup fails.

    `action` defaults to the idle `100`, which is what a static preview wants.
    Any other action code from `ini/3dmotion.ini` works the same way -- the key
    is `<bodyDigit><weaponType3><action3>` -- which is how the character
    builder plays walk / run / jump / swing off the game's own motion data
    rather than a motion model of its own.
    """
    cat = action_catalogue(root)
    if cat is None:
        return None
    try:
        return cat.idle_motion(body_appearance, weapon_type, action)
    except Exception:                                     # pragma: no cover
        return None


def _orthonormal(mat):
    r"""**Not used. Kept as the record of a fix that was worse than the bug.**

    Weapons flatten partway through some swings: `v_l_weapon` in attack
    swing 3 has row lengths (0.999, 0.051, 0.056) at frame 24 where frame 0
    has (0.967, 0.969, 0.994). Dividing the scale out of the basis fixed
    that frame and broke every resting pose, because **the socket scale is
    load-bearing**: at idle frame 0 the same socket's rows measure (0.111,
    0.149, 0.994), and that 10x shrink on two axes is what sizes a 188-unit
    sword down into a hand. Removing it produced a blade longer than the
    character, and the Gram-Schmidt -- which rebuilt axes longest-first --
    also swung one axis 43 degrees off where the file put it.

    So scale stays. What is still wrong is narrower than "the basis is not a
    rotation": *some frames' scales are wildly out of line with the same
    socket's other frames*. The measured fix, not yet implemented, is to
    keep every axis DIRECTION exactly as stored and replace an outlier
    axis LENGTH with the median that axis holds across the motion -- 0.051
    at frame 24 against a median near 0.15. That needs a per-(mesh, socket,
    motion) pass rather than the single-frame evaluation here, which is why
    it is written down instead of guessed at.

    The rows are nearly orthogonal as stored (dot products 0.003, 0.024,
    0.102), so whatever the engine does, it is not fighting a skewed basis.
    """
    return mat


def moti_sockets(body_c3: bytes, frame: int = 0, motion_set=None,
                 root=None) -> dict[str, Anchor]:
    r"""Socket transforms read from the mesh's own `MOTI` tracks.

    **VERIFIED against the data.** Each socket submesh is its own PHY chunk
    followed by a one-bone `MOTI` chunk; evaluating that bone at a frame gives
    the socket's world matrix. Sanity check on real bodies (heights in
    brackets): `002135000` [170.6] puts `v_armet` at z=164.4, `001131000` [168]
    at 166.7, `004134000` [195.9] at 190.8 -- i.e. exactly on top of the head in
    every case -- and `004134000`'s hands land at x = +-110 at z=152, the
    outstretched T-pose. That is the engine's own placement, not an estimate.

    Returns a full 4x4 per socket in *render* space, so orientation comes with
    it. Falls back to {} when the mesh has no per-socket MOTI (some bodies ship
    only one MOTI, for `v_body`).

    **Resolution is delegated to `tools/attach.py`** (task #18), which read it
    out of `Role3D!sub_86C0` / `sub_8E60`: `FindPhyByName(dumy)` gives an index,
    the motion at that *same ordinal index* is the socket's track, and bone 0 of
    it is the transform -- always bone 0, because a socket chunk is rigid and
    every one of its vertices carries `BLENDINDICES0 == 0`. The `PHY`/`MOTI`
    pairing being ordinal rather than adjacent is the part this module used to
    get wrong.

    **`root` is the install `body_c3` came from**, and which chunks count as
    sockets is read from its `ini/RolePart.ini [Dumy]`. Passing it matters
    because the answer really does differ: measured 2026-08-24 over the nine
    installs on this rig, `[Dumy]` declares 52 names under CCO and 7878 and 7
    under the other seven -- and the 7 are a **strict subset** of the 52, which
    is what makes the failure one-directional. This function keeps a chunk only
    if the socket test accepts it, so classifying a 52-name client's body under
    the 7-name list **drops** its `v_head`, `v_back`, `v_slot`, ... anchors --
    silently, since a short list parses fine and simply matches less. Leaving it
    None falls back to `attach.DEFAULT_ROOT` (i.e. `CO_ROOT`), which is right
    only by luck.
    """
    if attachmod is None:
        return {}
    try:
        pm = attachmod.PartMesh.parse(body_c3, root=root)
    except Exception:
        return {}

    out: dict[str, Anchor] = {}
    src = ("idle action motion (3dmotion.ini) bound by ordinal"
           if motion_set is not None else "the mesh's own embedded MOTI")
    for c in pm.chunks:
        name = c.name
        if not name or not attachmod.is_socket_name(name, pm._root()):
            continue
        if c.motion is None and motion_set is None:
            continue
        try:
            m = attachmod.socket_matrix(pm, name, frame, motion_set)
            if m is None:
                continue
            mat = attachmod.to_render(m)
        except Exception:
            continue
        out[name] = Anchor(name, (mat[12], mat[13], mat[14]),
                           f"chunk {c.index} bone 0 at frame {frame}, from {src}",
                           bone=0, confidence="verified")
        out[name].matrix = mat                # type: ignore[attr-defined]
    # attach.SOCKET_FALLBACK is the engine-derived substitution list; mirror the
    # two the viewer's slots actually need so a body missing them still equips.
    if "v_armet" in out and "v_head" not in out:
        out["v_head"] = out["v_armet"]
    if "v_l_weapon" in out and "v_l_shield" not in out:
        out["v_l_shield"] = out["v_l_weapon"]
    return out


def socket_anchors(body_c3: bytes, motion_set=None,
                   frame: int = 0, root=None) -> dict[str, Anchor]:
    """Where each socket sits on a body mesh.

    The real answer comes from `moti_sockets` (the engine's own transforms, via
    `tools/attach.py`). What is left here is the **fallback** for meshes that
    ship no usable motion: an estimate measured off the body's own skin
    clusters, position only, no rotation. It is labelled `inferred` and every
    part reports which of the two it got.

    `root` is the install `body_c3` came from; it reaches the socket test via
    `moti_sockets`, whose docstring says why the fallback is not harmless.
    """
    # Prefer the engine's own transforms.
    exact = moti_sockets(body_c3, frame=frame, motion_set=motion_set, root=root)

    meshes = [m for m in c3phy.meshes_from_c3(body_c3)]
    if not meshes:
        return exact
    body = _body_chunk(meshes)
    if body is None:
        return {}
    body = c3phy.apply_matrix_copy(body)
    if not body.vertices:
        return {}

    zs = [-v.pz for v in body.vertices]
    xs = [v.px for v in body.vertices]
    ys = [v.py for v in body.vertices]
    lo_z, hi_z = min(zs), max(zs)
    height = max(1e-6, hi_z - lo_z)
    mid_x = (min(xs) + max(xs)) / 2
    mid_y = (min(ys) + max(ys)) / 2

    cl = bone_clusters(body)
    out: dict[str, Anchor] = {}

    if cl:
        head_bone, head_pos = max(cl.items(), key=lambda kv: kv[1][2])
        out["v_armet"] = Anchor("v_armet", head_pos,
                                "highest bone cluster of the body's own skin",
                                head_bone)
        out["v_head"] = Anchor("v_head", head_pos, "same as v_armet", head_bone)

        upper = {b: c for b, c in cl.items() if c[2] > lo_z + height * 0.45}
        if upper:
            rb, rp = max(upper.items(), key=lambda kv: kv[1][0])
            lb, lp = min(upper.items(), key=lambda kv: kv[1][0])
            out["v_r_weapon"] = Anchor("v_r_weapon", rp,
                                       "furthest +X bone cluster above mid-height", rb)
            out["v_l_weapon"] = Anchor("v_l_weapon", lp,
                                       "furthest -X bone cluster above mid-height", lb)
            out["v_l_shield"] = Anchor("v_l_shield", lp, "same as v_l_weapon", lb)

    out.setdefault("v_armet", Anchor("v_armet", (mid_x, mid_y, hi_z),
                                     "top of the bounding box (no skin data)"))
    out.setdefault("v_head", out["v_armet"])
    out.setdefault("v_r_weapon", Anchor("v_r_weapon",
                                        (max(xs), mid_y, lo_z + height * 0.6),
                                        "bounding box (no skin data)"))
    out.setdefault("v_l_weapon", Anchor("v_l_weapon",
                                        (min(xs), mid_y, lo_z + height * 0.6),
                                        "bounding box (no skin data)"))
    out.setdefault("v_l_shield", out["v_l_weapon"])
    out["v_misc"] = Anchor("v_misc", (mid_x, mid_y, lo_z + height * 0.55),
                           "torso centre")
    out["v_back"] = Anchor("v_back", (mid_x, max(ys), lo_z + height * 0.7),
                           "behind the torso")
    out["v_mantle"] = out["v_back"]
    out["v_pelvis"] = Anchor("v_pelvis", (mid_x, mid_y, lo_z + height * 0.5),
                             "mid height")
    out["v_mount"] = Anchor("v_mount", (mid_x, mid_y, lo_z),
                            "ground level under the figure")
    out["v_body"] = Anchor("v_body", (0.0, 0.0, 0.0), "the body is the origin",
                           confidence="verified")
    # anything MOTI gave us wins over the estimate
    out.update(exact)
    return out


# ---------------------------------------------------------------------------
# the attachment transform chain -- STAGED, NOT YET CORRECT
# ---------------------------------------------------------------------------
#
# **Read this before changing anything here.**
#
# Composing an equipped part onto a socket needs three transforms and the order
# is not obvious. This project has already been burnt twice by getting exactly
# this wrong in opposite directions:
#
#   * double-applying the chunk matrix -> a 438-unit offset
#   * skipping it -> a body measuring 52.8 tall x 361.3 deep instead of
#     170.5 x 159.5
#
# So the chain is kept as **separate, individually inspectable stages** rather
# than one collapsed multiply. `attach_chain()` returns each stage plus its
# decomposition (translation / scale / rotation), so a regression shows up as a
# scale of 4.0 on one named stage instead of "the sword looks big".
#
# WHAT IS KNOWN, AND WHAT IS NOT
#
#   stage 1  chunkMatrix   VERIFIED. The engine applies the PHY chunk's own 4x4
#                          to positions at load (RVA 0x5A735). `mesh_to_json`
#                          bakes it into the emitted positions, so it is
#                          `applied: "baked"` -- applying it again is the bug.
#   stage 2  partMotion    The part's OWN `MOTI`, per vertex over its two bone
#                          influences. Measured on `c3/mesh/410000.c3`: bone 0
#                          frame 0 is a ~0.25 uniform scale, and the mesh is 359
#                          units long as authored -- 0.25 x 359 = 90, a blade
#                          length against a ~170-unit body. On headgear it is a
#                          near-unit matrix that carries ROTATION, which is the
#                          other half of the same symptom.
#   stage 3  socketMatrix  VERIFIED as the socket's placement, position AND
#                          orientation.
#
# **The composition is now VERIFIED, from `tools/attach.py` (task #18), which
# read it out of Role3D.dll and graphic.dll:**
#
#     idx   = mesh.FindPhyByName(dumy)                 graphic.dll 0x266D0
#     Msock = Motion_GetMatrix(bodyMotion[idx], 0, f)  graphic.dll 0x551A0
#     Mbone = Motion_GetMatrix(partMotion[c], b, f)    the PART's own motion
#     world = Mbone x Msock x Mrole                    row-vector, D3DX order
#
# Because `Mbone` is indexed **per vertex** it cannot be folded into a single
# model matrix for a skinned part, so the server bakes it into the emitted
# positions (`mesh_to_json(motion=...)`) and hands the client only `Msock` in
# render space. That is the same composition, split at the point the wire
# format allows.
#
# `tools/attach.py` is imported, never edited. The staged reporting below stays
# because it is what makes a regression legible.

#: Render-space GL identity (column-major).
GL_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def part_motion(part_c3: bytes, chunk_name: str = "", frame: int = 0):
    """The part's own `MOTI` transform, in render space (GL column-major).

    Returns `(matrix, note)`; `matrix` is None when the mesh ships no `MOTI`
    for that chunk, which is common and is not an error.

    Delegates the `PHY` -> `MOTI` pairing to `attach.PartMesh`, which is
    **ordinal, not adjacency** -- the two arrays are built independently by
    `MeshCreate` / `MotionCreate` and `c3/mount/850/8500000.c3` really does
    store all its `PHY` chunks before all its `MOTI` chunks.
    """
    if attachmod is None:
        return None, "tools/attach.py not importable"
    try:
        pm = attachmod.PartMesh.parse(part_c3)
    except Exception as exc:                              # pragma: no cover
        return None, f"parse failed: {exc}"
    want = (chunk_name or "").lower()
    for c in pm.chunks:
        if not getattr(c.phy, "vertices", None):
            continue
        if want and c.name.lower() != want:
            continue
        if c.motion is None:
            return None, f"chunk {c.index} {c.name!r} has no MOTI"
        bone = min((v.bone0 for v in c.phy.vertices), default=0)
        return (attachmod.to_render(c.motion.matrix(bone, frame)),
                f"MOTI chunk {c.index} bone {bone} at frame {frame} of "
                f"{c.name!r} ({c.motion.encoding}, {c.motion.frame_count} frames)")
    return None, "this mesh ships no MOTI for that chunk"


def attach_chain(part_c3: bytes, chunk_name: str, socket: Optional[Anchor],
                 frame: int = 0) -> dict:
    """Every stage of one part's attachment transform, kept apart.

    The point is that each stage can be checked on its own -- see the module
    note above. Nothing here silently multiplies anything into anything else;
    `compose_attachment()` does that, and says how.
    """
    import effectplay
    pm, pm_note = part_motion(part_c3, chunk_name, frame)
    stages = [
        {"name": "chunkMatrix", "applied": "baked",
         "matrix": None, "confidence": "verified",
         "note": "the PHY chunk's own 4x4, applied to positions at load "
                 "(RVA 0x5A735) and already baked into the emitted vertices — "
                 "applying it again is the 438-unit-offset bug"},
        {"name": "partMotion",
         "applied": "baked per vertex" if pm else "no MOTI on this chunk",
         "matrix": pm, "confidence": "verified" if pm else "none",
         "decomposed": effectplay.decompose(pm) if pm else None,
         "note": pm_note + (" — applied per vertex over both bone influences, "
                            "so it is baked into the emitted positions rather "
                            "than sent as a model matrix" if pm else "")},
        {"name": "socketMatrix",
         "applied": "applied" if (socket and socket.matrix) else "position only",
         "matrix": list(socket.matrix) if (socket and socket.matrix) else None,
         "confidence": socket.confidence if socket else "none",
         "decomposed": (effectplay.decompose(socket.matrix)
                        if (socket and socket.matrix) else None),
         "note": socket.source if socket else "no anchor for this socket"},
    ]
    return {"stages": stages, "frame": frame,
            "note": "world = Mbone x Msock x Mrole (row-vector), recovered from "
                    "Role3D.dll by tools/attach.py. Mbone is indexed per vertex, "
                    "so it is baked server-side and only Msock travels as a "
                    "model matrix."}


def compose_attachment(chain: dict, *, apply_part_motion: bool = False) -> dict:
    """Collapse the staged chain into one render-space GL matrix.

    `apply_part_motion` is **off by default and deliberately so**: applying the
    part's own MOTI in the wrong slot of the product is how this goes wrong in
    the other direction, and the correct order has not been read out of the
    engine yet. The flag exists so the fix, when `docs/attachment.md` lands, is
    a one-line change plus a test flip rather than a rewrite.
    """
    import effectplay
    out = list(GL_IDENTITY)
    used = []
    by_name = {s["name"]: s for s in chain["stages"]}
    sock = by_name.get("socketMatrix", {}).get("matrix")
    if sock:
        out = effectplay.mat_mul_gl(out, sock)
        used.append("socketMatrix")
    if apply_part_motion:
        pm = by_name.get("partMotion", {}).get("matrix")
        if pm:
            out = effectplay.mat_mul_gl(out, pm)
            used.append("partMotion")
            by_name["partMotion"]["applied"] = "applied"
    return {"matrix": out, "stagesApplied": used,
            "decomposed": effectplay.decompose(out),
            "confidence": "inferred"}


def body_bounds(body_c3: bytes, motion_set=None, frame: int = 0,
                root=None) -> Optional[dict]:
    """Render-space bounds of the body mesh alone. The camera frames on this and
    only this, so equipping a long weapon never yanks the view.

    When `motion_set` is given the body is posed by it first, so the bounds
    match what is actually drawn rather than the stored positions.

    `root` is the install `body_c3` came from. Here a miss goes the *other*
    way from `moti_sockets`: an unrecognised socket chunk is counted as
    geometry, so the box grows to enclose an attachment point and the camera
    frames on a model that is partly empty air.
    """
    if attachmod is not None:
        try:
            pm = attachmod.PartMesh.parse(body_c3, root=root)
            world = attachmod.IDENTITY
            lo = [1e30] * 3
            hi = [-1e30] * 3
            n = 0
            for c in pm.chunks:
                if not getattr(c.phy, "vertices", None):
                    continue
                if attachmod.is_socket_name(c.name, pm._root()):
                    continue
                q = c3phy.apply_matrix_copy(c.phy)
                mo = c.motion
                if motion_set is not None:
                    mo = motion_set.motion_for(c.index) or mo
                for v in q.vertices:
                    x, y, z = attachmod.transform_vertex(v, mo, world, frame)
                    z = -z
                    n += 1
                    for i, val in enumerate((x, y, z)):
                        lo[i] = min(lo[i], val)
                        hi[i] = max(hi[i], val)
            if n:
                return {"min": lo, "max": hi}
        except attachmod.DumyVocabularyUnavailable:
            # NOT swallowed, and this narrow arm exists only to say so. The
            # broad `except` below would turn "I cannot read this install's
            # [Dumy]" into a silent fall-through to the socket-blind estimate
            # -- bounds computed with the socket chunks counted as geometry,
            # which is a wrong answer wearing a right answer's shape. That is
            # the exact collapse `attach.dumy_names` was just made to raise
            # about, and re-swallowing it one layer out would undo the fix.
            raise
        except Exception:                                 # pragma: no cover
            pass
    meshes = c3phy.meshes_from_c3(body_c3)
    if not meshes:
        return None
    body = _body_chunk(meshes)
    if body is None:
        return None
    body = c3phy.apply_matrix_copy(body)
    if not body.vertices:
        return None
    xs = [v.px for v in body.vertices]
    ys = [v.py for v in body.vertices]
    zs = [-v.pz for v in body.vertices]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def _cli(argv):
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--mesh", help="a body .c3 to derive socket anchors from")
    a = ap.parse_args(argv)
    root = Path(a.root)
    pm = PartManifest(root)
    print(f"RolePart.ini: {len(pm.slots)} distinct part slots, "
          f"{len(pm.sockets)} sockets\n")
    for s in pm.ordered():
        alias = f"  (also {', '.join(s.aliases)})" if s.aliases else ""
        print(f"  {s.label:<14}{s.name:<12}{s.mesh_ini:<20}"
              f"{'shipped' if s.shipped else 'NOT SHIPPED':<12}{s.socket}{alias}")
    print("\n  sockets:")
    for i in range(0, len(pm.sockets), 6):
        print("   ", ", ".join(pm.sockets[i:i + 6]))
    if a.mesh:
        data = Path(a.mesh).read_bytes()
        print(f"\n  anchors derived from {Path(a.mesh).name}:")
        for k, v in sorted(socket_anchors(data).items()):
            print("    %-14s (%8.2f,%8.2f,%8.2f)  %s  [%s]"
                  % (k, *v.pos, v.source, v.confidence))
        print("  body bounds:", json.dumps(body_bounds(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
