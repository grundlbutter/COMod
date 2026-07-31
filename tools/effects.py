#!/usr/bin/env python3
r"""effects.py -- weapon/action -> 3D effect linkage, and the effect playback model.

This module answers two questions the asset viewer needs:

  1. **Which effect belongs to this weapon?**  ``ini/Action3DEffect.ini`` keys an
     effect name off ``<shape>.<action>.<idHi>.<idLo>`` where ``idHi+idLo`` is the
     6-digit equipment appearance ID, and ``ini/WeaponEffect.ini`` keys the impact
     spark off the 3-digit weapon type.

  2. **How is that effect played back?**  ``ini/3DEffect.ini`` gives per-effect
     timing and a list of layers; each layer names a mesh through
     ``ini/3DEffectObj.ini`` and a texture through ``ini/3dtexture.ini``.  The
     mesh is a C3 container holding one of three animation forms -- ``PHY``+
     ``MOTI`` (node/bone matrix track), ``SHAP``+``SMOT`` (a two-point blade line
     smeared into a ribbon trail), or ``PTCL``/``PTC3`` (particle system).

Everything the module claims is marked VERIFIED or INFERRED in ``docs/effects.md``.
The three binary parsers below (``parse_moti``, ``parse_shap``, ``parse_smot``) were
recovered instruction-by-instruction from ``graphic.dll`` and consume their chunk to
exactly the declared length on 100% of this install's corpus -- run ``--validate``.

CLI::

    py -3 tools/effects.py --weapon 410009        # everything for one weapon
    py -3 tools/effects.py --effect Flash4102     # resolve one effect to assets
    py -3 tools/effects.py --coverage             # the numbers in docs/effects.md
    py -3 tools/effects.py --validate             # re-derive the parser proof
    py -3 tools/effects.py --linkage              # write out/effects/linkage.json
"""
from __future__ import annotations

import json

import struct
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import AssetRoot, C3File, DEFAULT_ROOT          # noqa: E402
import c3phy                                                   # noqa: E402

WILDCARD = "999"

# ---------------------------------------------------------------------------
# tiny ini readers.  The 3D tables are not RFC-anything; they are three shapes:
#   * flat  key=value                         (3DEffectObj, 3dtexture, ...)
#   * [section] + key=value                   (3DEffect, WeaponEffect, ...)
#   * dotted key=value with 999 wildcards     (Action3DEffect, ActionSound)
# codepage.ini is 1 byte; every legacy ini in this install decodes as GBK, and
# GBK is a superset of ASCII so pure-ASCII tables are unaffected.
# ---------------------------------------------------------------------------

ENCODING = "gbk"


def _lines(path: Path) -> Iterator[str]:
    for raw in Path(path).read_bytes().decode(ENCODING, errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", ";")):
            continue
        yield line


def read_flat(path: Path) -> dict[str, str]:
    """``key=value`` per line, later wins."""
    out: dict[str, str] = {}
    for line in _lines(path):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_sections(path: Path) -> dict[str, dict[str, str]]:
    """``[section]`` + ``key=value``."""
    out: dict[str, dict[str, str]] = {}
    cur: Optional[dict[str, str]] = None
    for line in _lines(path):
        if line[0] == "[" and line.endswith("]"):
            cur = out.setdefault(line[1:-1].strip(), {})
            continue
        if cur is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cur[k.strip()] = v.strip()
    return out


def read_dotted(path: Path) -> list[tuple[tuple[str, ...], str]]:
    """``a.b.c[.d]=value``.  Order preserved; duplicate keys are kept (the file
    has 251 of them, always with an identical value)."""
    rows: list[tuple[tuple[str, ...], str]] = []
    for line in _lines(path):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        rows.append((tuple(p.strip() for p in k.strip().split(".")), v.strip()))
    return rows


def _int(d: dict[str, str], key: str, default: int = 0) -> int:
    v = d.get(key)
    if v is None:
        return default
    try:
        return int(float(v.split()[0]))
    except (ValueError, IndexError):
        return default


def _float(d: dict[str, str], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v.split()[0])
    except (ValueError, IndexError):
        return default


# ---------------------------------------------------------------------------
# D3D blend factors -- ASB / ADB.  D3DBLEND from d3d9types.h; the same numbers
# appear as Asb/Adb in weapon.ini and armor.ini, so this is one shared enum.
# ---------------------------------------------------------------------------

D3DBLEND = {
    1: "ZERO", 2: "ONE", 3: "SRCCOLOR", 4: "INVSRCCOLOR", 5: "SRCALPHA",
    6: "INVSRCALPHA", 7: "DESTALPHA", 8: "INVDESTALPHA", 9: "DESTCOLOR",
    10: "INVDESTCOLOR", 11: "SRCALPHASAT", 12: "BOTHSRCALPHA",
    13: "BOTHINVSRCALPHA", 14: "BLENDFACTOR", 15: "INVBLENDFACTOR",
}


def blend_name(v: int) -> str:
    return D3DBLEND.get(v, f"UNKNOWN({v})")


# ---------------------------------------------------------------------------
# C3 animation chunks
# ---------------------------------------------------------------------------

Mat4 = tuple  # 16 floats, row-major, row-vector convention (translation in row 3)

IDENTITY: Mat4 = (1.0, 0.0, 0.0, 0.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0)


def quat_to_matrix(x: float, y: float, z: float, w: float) -> Mat4:
    """D3DXMatrixRotationQuaternion, row-vector convention.

    graphic.dll!Motion_Load tail-calls the real D3DX function for ZKEY frames
    (the thunk at RVA 0x1D1F0A jumps to d3dx10_43!D3DXMatrixRotationQuaternion),
    so this reproduces it rather than inventing a convention.
    """
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy), 0.0,
            2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx), 0.0,
            2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy), 0.0,
            0.0, 0.0, 0.0, 1.0)


def mat_mul(a: Mat4, b: Mat4) -> Mat4:
    out = []
    for r in range(4):
        for c in range(4):
            out.append(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)))
    return tuple(out)


def transform_point(m: Mat4, p) -> tuple[float, float, float]:
    """Row-vector: p' = p * M (matches c3phy.apply_matrix_to)."""
    x, y, z = p
    return (x * m[0] + y * m[4] + z * m[8] + m[12],
            x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


@dataclass
class MotionKey:
    frame: int
    matrices: list[Mat4]        # one per bone


@dataclass
class Motion:
    """A decoded ``MOTI`` chunk -- see docs/effects.md §6.1."""
    bone_count: int
    frame_count: int
    encoding: str               # "RAW" | "KKEY" | "ZKEY" | "XKEY"
    keys: list[MotionKey]
    extra_channels: int = 0     # trailing block the engine skips
    consumed: int = 0
    size: int = 0

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    def matrix(self, bone: int, frame: int) -> Mat4:
        """Reproduces graphic.dll!Motion_GetMatrix (RVA 0x551A0).

        Clamps to the first/last key, otherwise linearly interpolates the 16
        matrix elements between the bracketing keys.  Element-wise lerp is what
        the engine literally does -- no quaternion slerp at this level, because
        ZKEY has already been baked to matrices at load time.
        """
        if not self.keys or bone >= self.bone_count:
            return IDENTITY
        if frame <= self.keys[0].frame:
            return self.keys[0].matrices[bone]
        if frame >= self.keys[-1].frame:
            return self.keys[-1].matrices[bone]
        for i in range(1, len(self.keys)):
            if frame < self.keys[i].frame:
                a, b = self.keys[i - 1], self.keys[i]
                span = b.frame - a.frame
                t = 0.0 if span == 0 else (frame - a.frame) / span
                ma, mb = a.matrices[bone], b.matrices[bone]
                return tuple(ma[k] + (mb[k] - ma[k]) * t for k in range(16))
        return self.keys[-1].matrices[bone]


def parse_moti(body: bytes) -> Motion:
    """Decode a ``MOTI`` chunk.  VERIFIED against graphic.dll!Motion_Load (0x557A0).

    ::

        u32 boneCount           # error "invalid bone count : %d, 255 is MAX" if > 255
        u32 frameCount          # the modulus used by Phy_NextFrame
        char[4] encoding        # "KKEY" | "ZKEY" | "XKEY", else rewind 4 and use RAW
        -- KKEY --  u32 keyCount ; keyCount x { u32 frame ; mat4x4[boneCount] }
        -- ZKEY --  u32 keyCount ; keyCount x { u16 frame ; {quat xyzw; float3 t}[boneCount] }
        -- XKEY --  u32 keyCount ; keyCount x { u16 frame ; float[12][boneCount] }
        -- RAW  --  mat4x4[frameCount] per bone, bone-major; keys are frames 0..n-1
        u32 extraChannels ; skip extraChannels * frameCount * 4 bytes
    """
    bone_count, frame_count = struct.unpack_from("<II", body, 0)
    if bone_count > 255:
        raise ValueError(f"invalid bone count {bone_count} (255 is MAX)")
    off = 8
    tag = bytes(body[8:12])
    keys: list[MotionKey] = []

    if tag in (b"KKEY", b"ZKEY", b"XKEY"):
        off = 12
        (key_count,) = struct.unpack_from("<I", body, off)
        off += 4
        for _ in range(key_count):
            if tag == b"KKEY":
                (frame,) = struct.unpack_from("<I", body, off)
                off += 4
                mats = [tuple(struct.unpack_from("<16f", body, off + 64 * b))
                        for b in range(bone_count)]
                off += 64 * bone_count
            elif tag == b"ZKEY":
                (frame,) = struct.unpack_from("<H", body, off)
                off += 2
                mats = []
                for b in range(bone_count):
                    qx, qy, qz, qw, tx, ty, tz = struct.unpack_from("<7f", body, off + 28 * b)
                    m = list(quat_to_matrix(qx, qy, qz, qw))
                    m[12], m[13], m[14], m[15] = tx, ty, tz, 1.0
                    mats.append(tuple(m))
                off += 28 * bone_count
            else:  # XKEY -- 4x3, expanded to 4x4
                (frame,) = struct.unpack_from("<H", body, off)
                off += 2
                mats = []
                for b in range(bone_count):
                    f = struct.unpack_from("<12f", body, off + 48 * b)
                    mats.append((f[0], f[1], f[2], 0.0,
                                 f[3], f[4], f[5], 0.0,
                                 f[6], f[7], f[8], 0.0,
                                 f[9], f[10], f[11], 1.0))
                off += 48 * bone_count
            keys.append(MotionKey(frame, mats))
        enc = tag.decode("ascii")
    else:
        # No recognised tag: the loader seeks back 4 and reads a dense
        # bone-major block of frameCount matrices per bone.
        enc = "RAW"
        per_bone: list[list[Mat4]] = []
        for _ in range(bone_count):
            per_bone.append([tuple(struct.unpack_from("<16f", body, off + 64 * f))
                             for f in range(frame_count)])
            off += 64 * frame_count
        for f in range(frame_count):
            keys.append(MotionKey(f, [per_bone[b][f] for b in range(bone_count)]))

    (extra,) = struct.unpack_from("<I", body, off)
    off += 4
    if extra:
        off += extra * frame_count * 4
    return Motion(bone_count, frame_count, enc, keys, extra, off, len(body))


@dataclass
class Shape:
    """A decoded ``SHAP`` chunk -- the trail/ribbon source line."""
    name: str
    frames: list[list[tuple[float, float, float]]]
    label: str
    segments: int
    consumed: int = 0
    size: int = 0

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def line(self) -> list[tuple[float, float, float]]:
        """The two endpoints Shape_Draw actually consumes: frames[0][0..1]."""
        return self.frames[0][:2] if self.frames else []


def parse_shap(body: bytes) -> Shape:
    """Decode a ``SHAP`` chunk.  VERIFIED against graphic.dll!Shape_Load (0x5D570)::

        u32 nameLen ; char[nameLen] name        # skipped by Seek in the engine
        u32 frameCount
        frameCount x { u32 pointCount ; float3[pointCount] }
        u32 labelLen ; char[labelLen] label     # skipped by Seek
        u32 segments                            # 0 is normalised to 1
    """
    off = 0
    (n,) = struct.unpack_from("<I", body, off); off += 4
    name = bytes(body[off:off + n]); off += n
    (fc,) = struct.unpack_from("<I", body, off); off += 4
    frames = []
    for _ in range(fc):
        (pc,) = struct.unpack_from("<I", body, off); off += 4
        frames.append([struct.unpack_from("<3f", body, off + 12 * j) for j in range(pc)])
        off += 12 * pc
    (n2,) = struct.unpack_from("<I", body, off); off += 4
    label = bytes(body[off:off + n2]); off += n2
    (seg,) = struct.unpack_from("<I", body, off); off += 4
    return Shape(name.decode(ENCODING, "replace"), frames,
                 label.decode(ENCODING, "replace"), seg or 1, off, len(body))


@dataclass
class SMotion:
    """A decoded ``SMOT`` chunk: one 4x4 per frame, nothing else."""
    matrices: list[Mat4]
    consumed: int = 0
    size: int = 0

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def frame_count(self) -> int:
        return len(self.matrices)


def parse_smot(body: bytes) -> SMotion:
    """VERIFIED against graphic.dll!SMotion_Load (0x5CE30): ``u32 n; mat4x4[n]``."""
    (n,) = struct.unpack_from("<I", body, 0)
    mats = [tuple(struct.unpack_from("<16f", body, 4 + 64 * i)) for i in range(n)]
    return SMotion(mats, 4 + 64 * n, len(body))


# --- C3Key channels (the 16-byte C3Frame records c3phy keeps opaque) --------

@dataclass
class KeyFrame:
    frame: int
    f_param: float
    b_param: int
    n_param: int


def decode_c3frames(raw: Iterable[bytes]) -> list[KeyFrame]:
    """``struct C3Frame { int nFrame; float fParam; BOOL bParam; int nParam; }``.

    VERIFIED by which member each Key_Process* export reads:
      * Key_ProcessAlpha      (0x725A0) reads +0x04 as float  -> fParam
      * Key_ProcessDraw       (0x72720) reads +0x08 as bool   -> bParam
      * Key_ProcessChangeTex  (0x726B0) reads +0x0C as int    -> nParam
    """
    out = []
    for r in raw:
        if len(r) < 16:
            continue
        n, f, b, i = struct.unpack_from("<ifii", r, 0)
        out.append(KeyFrame(n, f, b, i))
    return out


def key_alpha(keys: list[KeyFrame], frame: int) -> Optional[float]:
    """Key_ProcessAlpha: linear interpolation between the bracketing keys,
    clamped at both ends.  None when the channel is empty."""
    if not keys:
        return None
    prev = max((k for k in keys if k.frame <= frame), key=lambda k: k.frame, default=None)
    nxt = min((k for k in keys if k.frame > frame), key=lambda k: k.frame, default=None)
    if prev is None:
        return nxt.f_param
    if nxt is None:
        return prev.f_param
    span = nxt.frame - prev.frame
    t = 0.0 if span == 0 else (frame - prev.frame) / span
    return prev.f_param + (nxt.f_param - prev.f_param) * t


def key_change_tex(keys: list[KeyFrame], frame: int) -> Optional[int]:
    """Key_ProcessChangeTex: step function, no interpolation.  Exact frame match
    wins; otherwise the key immediately before the first key past `frame`."""
    if not keys:
        return None
    for i, k in enumerate(keys):
        if k.frame == frame:
            return k.n_param
        if i and frame < k.frame:
            return keys[i - 1].n_param
    return None


def key_draw(keys: list[KeyFrame], frame: int) -> Optional[bool]:
    """Key_ProcessDraw: exact frame match only -- visibility toggles."""
    for k in keys:
        if k.frame == frame:
            return k.b_param != 0
    return None


# ---------------------------------------------------------------------------
# an effect mesh, loaded
# ---------------------------------------------------------------------------

PART_PHY = "phy_motion"
PART_SHAPE = "shape_trail"
PART_PARTICLE = "particle"


@dataclass
class EffectPart:
    kind: str
    name: str = ""
    mesh: object = None            # c3phy.PhyMesh   (kind == phy_motion)
    motion: Optional[Motion] = None
    shape: Optional[Shape] = None  # kind == shape_trail
    smotion: Optional[SMotion] = None
    raw_size: int = 0              # kind == particle: undecoded chunk size
    alpha_keys: list[KeyFrame] = field(default_factory=list)
    draw_keys: list[KeyFrame] = field(default_factory=list)
    tex_keys: list[KeyFrame] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        if self.motion:
            return self.motion.frame_count
        if self.smotion:
            return self.smotion.frame_count
        if self.mesh is not None:
            return getattr(self.mesh, "frame_count", 0)
        return 0

    @property
    def uv_grid(self) -> int:
        """Flipbook grid size N for ChangeTex: the PHY's own ``frameCount``
        field (C3Phy+0x190).  Cell for key value ``n`` is ``(n % N, n // N)``
        with cell size ``1/N``.  VERIFIED at graphic.dll 0x56102-0x5616C."""
        return getattr(self.mesh, "frame_count", 0) if self.mesh is not None else 0

    @property
    def uv_step(self) -> Optional[tuple[float, float]]:
        """The optional ``STEP`` block, reinterpreted as two floats: the per-tick
        U/V scroll used when the mesh has no ChangeTex keys.  VERIFIED: Phy_Load
        stores STEP's two dwords at C3Phy+0x198/+0x19C (RVA 0x5B21E/0x5B235) and
        Phy_Calculate multiplies them by the animation counter at +0x194."""
        step = getattr(self.mesh, "step", None) if self.mesh is not None else None
        if not step:
            return None
        return tuple(struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
                     for v in step)

    @property
    def alpha_end(self) -> Optional[int]:
        """Last frame at which the alpha envelope is still non-zero.

        Effect meshes routinely carry a 101-frame motion track but fade to zero
        after ~10 frames; that is the real length of the animation.
        """
        if not self.alpha_keys:
            return None
        if all(k.f_param == 0.0 for k in self.alpha_keys):
            return 0
        return max(k.frame for k in self.alpha_keys if k.f_param > 0.0)

    @property
    def effective_frames(self) -> int:
        """Playable length in frames.

        The alpha envelope is the only thing in the data that says when a burst
        is over: effect meshes habitually declare a 101-frame motion track and
        fade to zero after ~10.  So: if the channel has two or more keys and the
        last one is alpha 0, the animation ends there; otherwise there is no
        fade-out and the full motion length stands.  INFERRED, but it is what
        the data plainly shows and it makes the impact effects come out at
        sensible lengths (m-b02 = 11 frames x 33 ms = 363 ms).
        """
        if len(self.alpha_keys) < 2:
            return self.frame_count
        last = max(self.alpha_keys, key=lambda k: k.frame)
        if last.f_param > 0.0:
            return self.frame_count
        return min(self.frame_count or (last.frame + 1), last.frame + 1)


@dataclass
class EffectObject:
    """A ``3DEffectObj.ini`` entry, loaded and decoded."""
    obj_id: str
    logical: str
    source: str = ""
    parts: list[EffectPart] = field(default_factory=list)
    error: str = ""

    @property
    def kinds(self) -> set[str]:
        return {p.kind for p in self.parts}

    @property
    def frame_count(self) -> int:
        return max([p.frame_count for p in self.parts] or [0])

    @property
    def effective_frames(self) -> int:
        return max([p.effective_frames for p in self.parts] or [0])


def load_effect_object(root: AssetRoot, obj_id: str, logical: str) -> EffectObject:
    """Read one effect C3 and decode every chunk we know how to play."""
    logical = logical.replace("\\", "/")
    loc = root.locate(logical)
    if loc is None:
        return EffectObject(obj_id, logical, error="asset not found")
    obj = EffectObject(obj_id, logical, loc.source)
    try:
        c3 = C3File(root.read(logical))
    except Exception as exc:                                  # pragma: no cover
        obj.error = f"C3 parse failed: {exc}"
        return obj

    pending_phy: Optional[EffectPart] = None
    pending_shape: Optional[EffectPart] = None
    for ch in c3.chunks:
        try:
            if ch.tag in (b"PHY ", b"PHY2", b"PHY3", b"PHY4", b"PHY5"):
                mesh = c3phy.parse_phy(ch.tag, ch.body)
                pending_phy = EffectPart(
                    PART_PHY, mesh.name, mesh=mesh,
                    alpha_keys=decode_c3frames(mesh.keys.alphas),
                    draw_keys=decode_c3frames(mesh.keys.draws),
                    tex_keys=decode_c3frames(mesh.keys.change_texs))
                obj.parts.append(pending_phy)
            elif ch.tag == b"MOTI":
                m = parse_moti(ch.body)
                if pending_phy is not None and pending_phy.motion is None:
                    pending_phy.motion = m
                    pending_phy = None
                else:
                    obj.parts.append(EffectPart(PART_PHY, motion=m))
            elif ch.tag == b"SHAP":
                sh = parse_shap(ch.body)
                pending_shape = EffectPart(PART_SHAPE, sh.name, shape=sh)
                obj.parts.append(pending_shape)
            elif ch.tag == b"SMOT":
                sm = parse_smot(ch.body)
                if pending_shape is not None and pending_shape.smotion is None:
                    pending_shape.smotion = sm
                    pending_shape = None
                else:
                    obj.parts.append(EffectPart(PART_SHAPE, smotion=sm))
            elif ch.tag in (b"PTCL", b"PTC3"):
                obj.parts.append(EffectPart(PART_PARTICLE, ch.tag.decode("latin1"),
                                            raw_size=len(ch.body)))
            # CAME (camera) is authoring metadata; nothing to play.
        except Exception as exc:                              # pragma: no cover
            obj.error = f"{ch.name}: {exc}"
    return obj


def shape_ribbon(shape: Shape, smotion: Optional[SMotion], world: Mat4,
                 frame: int, history: list[tuple], *, subdiv: int = 5,
                 max_segments: int = 800) -> list[tuple]:
    """Advance a SHAP trail by one frame and return the ribbon vertex pairs.

    Mirrors graphic.dll!Shape_Draw / Shape_SetSegment: the ribbon holds
    ``min(segments * 5, 800)`` segments, each draw pushes ``subdiv`` linearly
    interpolated pairs between the previous transformed line and the current
    one, and the strip is ``segments*2 + 2`` vertices wide.

    ``history`` is the caller's rolling list of ``(a, b)`` point pairs, newest
    last; it is mutated and returned.  Pass ``[]`` on the first frame.
    """
    line = shape.line
    if len(line) < 2:
        return history
    m = world
    if smotion and smotion.matrices:
        m = mat_mul(smotion.matrices[frame % len(smotion.matrices)], world)
    a = transform_point(m, line[0])
    b = transform_point(m, line[1])
    cap = min(shape.segments * subdiv, max_segments)
    if history:
        pa, pb = history[-1]
        for s in range(1, subdiv + 1):
            t = s / subdiv
            history.append((
                tuple(pa[i] + (a[i] - pa[i]) * t for i in range(3)),
                tuple(pb[i] + (b[i] - pb[i]) * t for i in range(3)),
            ))
    else:
        history.append((a, b))
    if len(history) > cap + 1:
        del history[:len(history) - (cap + 1)]
    return history


# ---------------------------------------------------------------------------
# effect definitions -- ini/3DEffect.ini
# ---------------------------------------------------------------------------

@dataclass
class EffectLayer:
    index: int
    effect_id: str = ""
    texture_id: str = ""
    extra_texture_ids: list[str] = field(default_factory=list)
    asb: int = 5
    adb: int = 6
    scale: Optional[int] = None
    frame_offset: Optional[int] = None
    interval: Optional[int] = None
    loop_once: Optional[int] = None
    zbuffer: Optional[int] = None
    ztest: Optional[int] = None
    billboard: Optional[int] = None
    lev: Optional[int] = None
    mix_opt: Optional[int] = None
    x_self: Optional[float] = None
    y_self: Optional[float] = None
    z_self: Optional[float] = None
    # filled in by EffectDB.resolve()
    mesh_path: str = ""
    mesh_found: bool = False
    texture_path: str = ""
    texture_found: bool = False

    @property
    def asb_name(self) -> str:
        return blend_name(self.asb)

    @property
    def adb_name(self) -> str:
        return blend_name(self.adb)


@dataclass
class EffectDef:
    name: str
    amount: int = 0
    delay: int = 0                 # ms before the effect first appears
    loop_time: int = 1             # number of loops; 99999999 == "forever"
    loop_interval: int = 0         # ms of dead time between loops
    frame_interval: int = 33       # ms per animation frame
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color_enable: bool = False
    billboard: Optional[int] = None
    lev: Optional[int] = None
    layers: list[EffectLayer] = field(default_factory=list)
    source: str = "3DEffect.ini"

    @property
    def fps(self) -> float:
        return 1000.0 / self.frame_interval if self.frame_interval else 0.0

    @property
    def endless(self) -> bool:
        return self.loop_time >= 99999


def _parse_effect_section(name: str, d: dict[str, str], source: str) -> EffectDef:
    amount = _int(d, "Amount", 0)
    e = EffectDef(
        name=name,
        amount=amount,
        delay=_int(d, "Delay"),
        loop_time=_int(d, "LoopTime", 1),
        loop_interval=_int(d, "LoopInterval"),
        frame_interval=_int(d, "FrameInterval", 33),
        offset=(_float(d, "OffsetX"), _float(d, "OffsetY"), _float(d, "OffsetZ")),
        color_enable=bool(_int(d, "ColorEnable")),
        billboard=_int(d, "Billboard") if "Billboard" in d else None,
        lev=_int(d, "Lev") if "Lev" in d else None,
        source=source,
    )
    for i in range(amount):
        layer = EffectLayer(
            index=i,
            effect_id=d.get(f"EffectId{i}", ""),
            texture_id=d.get(f"TextureId{i}", ""),
            extra_texture_ids=[d[k] for k in (f"TextureId{i}_1", f"TextureId{i}_2",
                                              f"TextureId{i}_3") if k in d],
            asb=_int(d, f"ASB{i}", 5),
            adb=_int(d, f"ADB{i}", 6),
        )
        for attr, key, conv in (
            ("scale", f"Scale{i}", int), ("frame_offset", f"FrameOffset{i}", int),
            ("interval", f"Interval{i}", int), ("loop_once", f"LoopOnce{i}", int),
            ("zbuffer", f"ZBuffer{i}", int), ("ztest", f"ZTest{i}", int),
            ("billboard", f"Billboard{i}", int), ("lev", f"Lev{i}", int),
            ("mix_opt", f"MixOpt{i}", int), ("x_self", f"XSelf{i}", float),
            ("y_self", f"YSelf{i}", float), ("z_self", f"ZSelf{i}", float),
        ):
            if key in d:
                setattr(layer, attr, (_int if conv is int else _float)(d, key))
        e.layers.append(layer)
    return e


# ---------------------------------------------------------------------------
# the database
# ---------------------------------------------------------------------------

@dataclass
class WeaponImpact:
    """One ``ini/WeaponEffect.ini`` section: the spark shown at the target."""
    weapon_type: str
    hit_effect: str = ""
    hit_sound: str = ""
    blk_effect: str = ""
    blk_sound: str = ""


@dataclass
class ActionEffectRule:
    """One ``ini/Action3DEffect.ini`` row."""
    shape: str
    action: str
    group_hi: str
    group_lo: str
    effect: str

    @property
    def appearance(self) -> str:
        return self.group_hi + self.group_lo

    @property
    def specificity(self) -> int:
        return sum(1 for f in (self.shape, self.action, self.group_hi,
                               self.group_lo) if f != WILDCARD)


@dataclass
class ActionMapRule:
    """One ``ini/ActionMap3DEffect.ini`` section."""
    shape: str
    action: str
    terrain: str
    effect: str
    show_time: int = 0             # 0 = at motion start, 1 = at motion end
    dir_enable: int = 0            # 1 = effect rotates with the character facing

    @property
    def specificity(self) -> int:
        return sum(1 for f in (self.shape, self.action, self.terrain) if f != WILDCARD)


@dataclass
class WeaponEffectSet:
    """Everything the viewer needs to show one equipped weapon's effects."""
    appearance: str
    weapon_type: str
    type_name: str = ""
    aura: str = ""                                  # action 999 -- always on
    attack: dict[str, str] = field(default_factory=dict)   # action -> effect
    impact: Optional[WeaponImpact] = None
    motion_meshes: dict[str, str] = field(default_factory=dict)
    in_weapon_ini: bool = False

    @property
    def effect_names(self) -> list[str]:
        out = [self.aura] + list(self.attack.values())
        if self.impact:
            out += [self.impact.hit_effect, self.impact.blk_effect]
        seen, res = set(), []
        for n in out:
            if n and n.lower() != "none" and n not in seen:
                seen.add(n)
                res.append(n)
        return res


class EffectDB:
    """All effect tables, loaded once."""

    def __init__(self, root: Path | str = DEFAULT_ROOT, assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        self.ini = self.root / "ini"
        self.assets = assets or AssetRoot(self.root)

        # -- effect definitions.  3DEffect.ini is what the engine reads
        # (GraphicData.dll holds the literal "ini/3DEffect.ini"); the shipped
        # 3DEffect.json is a stale 2022 export.  Merge json-only names in so
        # nothing referenced elsewhere is silently missing, ini always wins.
        self.effects: dict[str, EffectDef] = {}
        for name, d in read_sections(self.ini / "3DEffect.ini").items():
            self.effects[name] = _parse_effect_section(name, d, "3DEffect.ini")
        self.json_only: list[str] = []
        for e in self._load_effect_json():
            if e.name not in self.effects:
                self.effects[e.name] = e
                self.json_only.append(e.name)

        self.objs: dict[str, str] = read_flat(self.ini / "3DEffectObj.ini")
        self.textures: dict[str, str] = read_flat(self.ini / "3dtexture.ini")
        self.meshes: dict[str, str] = read_flat(self.ini / "3dobj.ini")
        self.weapon_motion: dict[str, str] = read_flat(self.ini / "WeaponMotion.ini")

        # -- weapon impact
        self.weapon_impact: dict[str, WeaponImpact] = {}
        for t, d in read_sections(self.ini / "WeaponEffect.ini").items():
            self.weapon_impact[t] = WeaponImpact(
                t, d.get("HitEffect", ""), d.get("HitSound", ""),
                d.get("BlkEffect", ""), d.get("BlkSound", ""))

        # -- action -> effect
        self.action_rules: list[ActionEffectRule] = []
        seen_rule = set()
        for key, val in read_dotted(self.ini / "Action3DEffect.ini"):
            if len(key) != 4:
                continue
            if (key, val) in seen_rule:
                continue
            seen_rule.add((key, val))
            self.action_rules.append(ActionEffectRule(*key, val))
        self._by_appearance: dict[str, list[ActionEffectRule]] = {}
        for r in self.action_rules:
            self._by_appearance.setdefault(r.appearance, []).append(r)

        self.action_map: list[ActionMapRule] = []
        for sec, d in read_sections(self.ini / "ActionMap3DEffect.ini").items():
            if len(sec) != 9 or not sec.isdigit():
                continue
            self.action_map.append(ActionMapRule(
                sec[0:3], sec[3:6], sec[6:9], d.get("Effect", ""),
                _int(d, "ShowTime"), _int(d, "DirEnable")))

        # -- ancillary
        self.action_delay: dict[str, dict[str, int]] = {}
        for sec, d in read_sections(self.ini / "ActionDelay.ini").items():
            self.action_delay[sec] = {k: _int(d, k) for k in
                                      ("WoundDelay", "BlockDelay", "DieDelay")}
        self.flying: dict[str, dict[str, str]] = read_sections(self.ini / "3DFlyingObj.ini")
        self.media_effect: dict[str, dict[str, str]] = read_sections(self.ini / "MediaEffect.ini")
        self.weapon_type_names: dict[str, str] = {}
        try:
            for row in json.loads((self.ini / "WeaponSkillName.json")
                                  .read_text("utf-8-sig")):
                self.weapon_type_names[str(row["id"]).zfill(3)] = row["name"]
        except Exception:                                     # pragma: no cover
            pass
        self.weapon_appearances: dict[str, dict[str, str]] = read_sections(
            self.ini / "weapon.ini")

    # -- loading helpers ---------------------------------------------------

    def _load_effect_json(self) -> list[EffectDef]:
        p = self.ini / "3DEffect.json"
        if not p.is_file():
            return []
        try:
            rows = json.loads(p.read_text("utf-8-sig"))
        except Exception:                                     # pragma: no cover
            return []
        out = []
        for row in rows:
            off = row.get("Offset") or {}
            e = EffectDef(
                name=row.get("Name", ""),
                amount=len(row.get("Effects") or []),
                delay=int(row.get("Delay", 0)),
                loop_time=int(row.get("LoopTime", 1)),
                loop_interval=int(row.get("LoopInterval", 0)),
                frame_interval=int(row.get("FrameInterval", 33)),
                offset=(float(off.get("X", 0)), float(off.get("Y", 0)),
                        float(off.get("Z", 0))),
                color_enable=bool(row.get("ColorEnable", False)),
                source="3DEffect.json",
            )
            for i, lay in enumerate(row.get("Effects") or []):
                e.layers.append(EffectLayer(
                    index=i,
                    effect_id=str(lay.get("Effect", "")),
                    texture_id=str(lay.get("Texture", "")),
                    asb=int(lay.get("ASB", 5)), adb=int(lay.get("ADB", 6))))
            out.append(e)
        return out

    # -- weapon queries ----------------------------------------------------

    @staticmethod
    def split_appearance(appearance: str) -> tuple[str, str]:
        """``410009`` -> ``('410', '009')``.  The last three digits are the
        style+quality group; everything before is the weapon type."""
        a = str(appearance).strip()
        return (a[:-3], a[-3:]) if len(a) > 3 else (a, "")

    def weapon_type_of(self, appearance: str) -> str:
        return self.split_appearance(appearance)[0]

    def rules_for_appearance(self, appearance: str) -> list[ActionEffectRule]:
        hi, lo = self.split_appearance(appearance)
        out = list(self._by_appearance.get(hi + lo, []))
        # wildcard on the low group -- INFERRED, no such row ships today for
        # weapons but the format allows it and ActionMap documents 999.
        out += [r for r in self._by_appearance.get(hi + WILDCARD, [])]
        return out

    def effects_for_weapon(self, appearance: str, *, shape: str = WILDCARD) -> WeaponEffectSet:
        """Everything keyed off one weapon appearance ID (e.g. ``410009``)."""
        appearance = str(appearance).strip()
        hi, _lo = self.split_appearance(appearance)
        es = WeaponEffectSet(
            appearance=appearance, weapon_type=hi,
            type_name=self.weapon_type_names.get(hi, ""),
            impact=self.weapon_impact.get(hi),
            in_weapon_ini=appearance in self.weapon_appearances)
        for r in self.rules_for_appearance(appearance):
            if r.shape not in (WILDCARD, shape):
                continue
            if r.action == WILDCARD:
                if not es.aura or r.effect.lower() != "none":
                    es.aura = r.effect
            else:
                es.attack[r.action] = r.effect
        # `none` is an explicit "no effect" row (the hurt/die actions carry it on
        # every weapon).  It means the same as no row at all here, so drop it
        # from this convenience view; the raw rows are still in `action_rules`.
        es.attack = {k: v for k, v in sorted(es.attack.items())
                     if v.lower() != "none"}
        if es.aura.lower() == "none":
            es.aura = ""
        for mid, mesh in self.weapon_motion.items():
            if mid.startswith(appearance):
                es.motion_meshes[mid[len(appearance):]] = mesh
        return es

    def lookup_action_effect(self, appearance: str, action: str,
                             shape: str = WILDCARD) -> Optional[str]:
        """Most specific matching Action3DEffect row, or None."""
        hi, lo = self.split_appearance(appearance)
        best: Optional[ActionEffectRule] = None
        for r in self.action_rules:
            if r.shape not in (WILDCARD, shape):
                continue
            if r.action not in (WILDCARD, action):
                continue
            if r.group_hi not in (WILDCARD, hi):
                continue
            if r.group_lo not in (WILDCARD, lo):
                continue
            if best is None or r.specificity > best.specificity:
                best = r
        return best.effect if best else None

    def lookup_action_map(self, shape: str, action: str,
                          terrain: str) -> Optional[ActionMapRule]:
        """Most specific matching ActionMap3DEffect row, or None.

        Key format, from the file's own GBK header comment: nine digits in three
        groups of three -- shape (外形), action (动作), terrain (地形).  "999" is
        a wildcard and two wildcards may appear at once.
        """
        best: Optional[ActionMapRule] = None
        for r in self.action_map:
            if r.shape not in (WILDCARD, str(shape)):
                continue
            if r.action not in (WILDCARD, str(action)):
                continue
            if r.terrain not in (WILDCARD, str(terrain)):
                continue
            if best is None or r.specificity > best.specificity:
                best = r
        return best

    # -- effect resolution -------------------------------------------------

    def resolve(self, name: str) -> Optional[EffectDef]:
        """Return a copy of the effect definition with asset paths filled in."""
        base = self.effects.get(name)
        if base is None:
            return None
        e = EffectDef(**{**asdict(base), "layers": []})
        for lay in base.layers:
            L = EffectLayer(**asdict(lay))
            raw_mesh = self.objs.get(L.effect_id, "")
            L.mesh_path = raw_mesh.replace("\\", "/")
            L.mesh_found = bool(L.mesh_path) and self.assets.exists(L.mesh_path)
            raw_tex = self.textures.get(L.texture_id, "")
            L.texture_path = raw_tex.replace("\\", "/")
            L.texture_found = bool(L.texture_path) and self.assets.exists(L.texture_path)
            e.layers.append(L)
        return e

    def load_geometry(self, name: str) -> list[EffectObject]:
        """Resolve an effect and load every layer's C3, decoded and playable."""
        e = self.resolve(name)
        if e is None:
            return []
        return [load_effect_object(self.assets, L.effect_id, L.mesh_path)
                for L in e.layers if L.mesh_path]

    def frames_for(self, name: str) -> tuple[int, int]:
        """``(motion_frames, effective_frames)`` across every layer of an effect."""
        total = eff = 0
        for obj in self.load_geometry(name):
            total = max(total, obj.frame_count)
            eff = max(eff, obj.effective_frames)
        return total, eff

    def duration_ms(self, name: str, *, effective: bool = True) -> Optional[float]:
        """Wall-clock length of one playthrough, or None when endless.

        ``delay + loops * (frames * frameInterval) + (loops - 1) * loopInterval``.

        ``frames`` is the alpha-envelope length by default, because effect meshes
        habitually declare a 101-frame motion track and fade out after ~10.
        INFERRED -- the timer itself lives in the packed exe -- but every field it
        uses is read by GraphicData.dll, so the shape of the formula is not a guess.
        """
        e = self.resolve(name)
        if e is None or e.endless:
            return None
        total, effn = self.frames_for(name)
        frames = (effn or total) if effective else total
        if not frames:
            return None
        one = frames * e.frame_interval
        return e.delay + e.loop_time * one + max(0, e.loop_time - 1) * e.loop_interval


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def coverage(db: EffectDB) -> dict:
    """The numbers quoted in docs/effects.md."""
    out: dict = {}

    types = sorted(db.weapon_impact)
    out["weapon_types_with_impact"] = len(types)
    out["weapon_types_in_skill_table"] = len(db.weapon_type_names)
    out["weapon_types_missing_impact"] = sorted(
        set(db.weapon_type_names) - set(types))

    apps = sorted(db.weapon_appearances)
    with_aura = with_attack = with_impact = 0
    per_type: dict[str, list[int]] = {}
    for a in apps:
        es = db.effects_for_weapon(a)
        hit = per_type.setdefault(es.weapon_type, [0, 0])
        hit[1] += 1
        if es.aura:
            with_aura += 1
        if es.attack:
            with_attack += 1
            hit[0] += 1
        if es.impact:
            with_impact += 1
    out["weapon_appearances"] = len(apps)
    out["weapon_appearances_with_attack_trail"] = with_attack
    out["weapon_appearances_with_aura"] = with_aura
    out["weapon_appearances_with_impact"] = with_impact
    out["per_type_attack_coverage"] = {
        t: {"with_trail": v[0], "appearances": v[1]}
        for t, v in sorted(per_type.items())}

    # effect name resolution
    referenced: set[str] = set()
    for r in db.action_rules:
        referenced.add(r.effect)
    for r in db.action_map:
        referenced.add(r.effect)
    for w in db.weapon_impact.values():
        referenced.update((w.hit_effect, w.blk_effect))
    referenced = {n for n in referenced if n and n.lower() != "none"}
    out["effect_names_referenced"] = len(referenced)
    out["effect_names_defined"] = len(db.effects)
    out["effect_names_referenced_but_undefined"] = sorted(referenced - set(db.effects))

    layers = meshes_ok = texes_ok = 0
    full = partial = broken = 0
    for name in sorted(db.effects):
        e = db.resolve(name)
        if not e.layers:
            broken += 1
            continue
        ok = 0
        for L in e.layers:
            layers += 1
            meshes_ok += L.mesh_found
            texes_ok += L.texture_found
            ok += L.mesh_found and L.texture_found
        if ok == len(e.layers):
            full += 1
        elif ok:
            partial += 1
        else:
            broken += 1
    out["effect_layers_total"] = layers
    out["effect_layers_mesh_found"] = meshes_ok
    out["effect_layers_texture_found"] = texes_ok
    out["effects_fully_resolved"] = full
    out["effects_partially_resolved"] = partial
    out["effects_unresolved"] = broken
    out["json_only_effect_names"] = len(db.json_only)

    # which animation form backs each 3DEffectObj entry
    kinds: dict[str, int] = {}
    obj_kind: dict[str, set[str]] = {}
    for oid, path in db.objs.items():
        o = load_effect_object(db.assets, oid, path)
        obj_kind[oid] = o.kinds
        k = "+".join(sorted(o.kinds)) or ("missing" if o.error else "empty")
        kinds[k] = kinds.get(k, 0) + 1
    out["effect_objects_total"] = len(db.objs)
    out["effect_objects_by_animation_form"] = dict(sorted(kinds.items()))

    # and what that means for the effects that reference them
    playable = particle_only = mixed = unknown = 0
    for name in sorted(db.effects):
        e = db.resolve(name)
        ks: set[str] = set()
        for L in e.layers:
            ks |= obj_kind.get(L.effect_id, set())
        if not ks:
            unknown += 1
        elif ks == {PART_PARTICLE}:
            particle_only += 1
        elif PART_PARTICLE in ks:
            mixed += 1
        else:
            playable += 1
    out["effects_playable_today"] = playable
    out["effects_particle_only"] = particle_only
    out["effects_partly_particle"] = mixed
    out["effects_no_geometry"] = unknown

    # the subset the viewer actually needs: effects reachable from a weapon
    weapon_effects: set[str] = set()
    for a in apps:
        weapon_effects.update(db.effects_for_weapon(a).effect_names)
    wp = wpart = wmiss = 0
    for n in sorted(weapon_effects):
        e = db.resolve(n)
        if e is None:
            wmiss += 1
            continue
        ks: set[str] = set()
        for L in e.layers:
            ks |= obj_kind.get(L.effect_id, set())
        if not ks:
            wmiss += 1
        elif PART_PARTICLE in ks:
            wpart += 1
        else:
            wp += 1
    out["weapon_reachable_effects"] = len(weapon_effects)
    out["weapon_reachable_playable"] = wp
    out["weapon_reachable_partly_particle"] = wpart
    out["weapon_reachable_unresolved"] = wmiss
    return out


#: Every ini in this install whose values are asset paths.  Together they name
#: every C3 the client can reach by name.
PATH_INIS = ("3DEffectObj.ini", "WeaponMotion.ini", "3dobj.ini", "3dmotion.ini",
             "miscmotion.ini", "MountMotion.ini")


def validate(db: EffectDB, limit: Optional[int] = None) -> dict:
    """Re-derive the parser proof: every MOTI/SHAP/SMOT chunk in every C3 named
    by a path-valued ini must consume exactly its declared chunk length."""
    seen: set[str] = set()
    stats = {k: 0 for k in ("files", "missing", "MOTI", "MOTI_exact", "SHAP",
                            "SHAP_exact", "SMOT", "SMOT_exact", "PTCL", "PTC3",
                            "errors")}
    enc: dict[str, int] = {}
    problems: list[str] = []
    paths: list[str] = []
    for name in PATH_INIS:
        p = db.ini / name
        if p.is_file():
            paths.extend(read_flat(p).values())
    for lg in paths:
        lg = lg.replace("\\", "/")
        key = lg.lower()
        if key in seen:
            continue
        seen.add(key)
        if limit and stats["files"] >= limit:
            break
        if not db.assets.exists(lg):
            stats["missing"] += 1
            continue
        stats["files"] += 1
        try:
            c3 = C3File(db.assets.read(lg))
        except Exception as exc:
            stats["errors"] += 1
            problems.append(f"{lg}: {exc}")
            continue
        for ch in c3.chunks:
            try:
                if ch.tag == b"MOTI":
                    m = parse_moti(ch.body)
                    stats["MOTI"] += 1
                    stats["MOTI_exact"] += m.exact
                    enc[m.encoding] = enc.get(m.encoding, 0) + 1
                    if not m.exact:
                        problems.append(f"{lg} MOTI {m.consumed}/{m.size}")
                elif ch.tag == b"SHAP":
                    s = parse_shap(ch.body)
                    stats["SHAP"] += 1
                    stats["SHAP_exact"] += s.exact
                    if not s.exact:
                        problems.append(f"{lg} SHAP {s.consumed}/{s.size}")
                elif ch.tag == b"SMOT":
                    s = parse_smot(ch.body)
                    stats["SMOT"] += 1
                    stats["SMOT_exact"] += s.exact
                    if not s.exact:
                        problems.append(f"{lg} SMOT {s.consumed}/{s.size}")
                elif ch.tag in (b"PTCL", b"PTC3"):
                    stats[ch.tag.decode()] += 1
            except Exception as exc:
                stats["errors"] += 1
                problems.append(f"{lg} {ch.name}: {exc}")
    stats["moti_encodings"] = enc
    stats["problems"] = problems[:40]
    return stats


def _effect_json(db: EffectDB, name: str) -> Optional[dict]:
    e = db.resolve(name)
    if e is None:
        return None
    d = {
        "name": e.name, "source": e.source,
        "delay_ms": e.delay, "loop_time": e.loop_time,
        "loop_interval_ms": e.loop_interval,
        "frame_interval_ms": e.frame_interval,
        "fps": round(e.fps, 3), "endless": e.endless,
        "offset": {"x": e.offset[0], "y": e.offset[1], "z": e.offset[2]},
        "color_enable": e.color_enable,
        "layers": [],
    }
    if e.billboard is not None:
        d["billboard"] = e.billboard
    if e.lev is not None:
        d["lev"] = e.lev
    for L in e.layers:
        ld = {
            "index": L.index,
            "effect_id": L.effect_id, "mesh": L.mesh_path, "mesh_found": L.mesh_found,
            "texture_id": L.texture_id, "texture": L.texture_path,
            "texture_found": L.texture_found,
            "src_blend": L.asb, "src_blend_name": L.asb_name,
            "dst_blend": L.adb, "dst_blend_name": L.adb_name,
        }
        for key, val in (("extra_textures", L.extra_texture_ids), ("scale", L.scale),
                         ("frame_offset", L.frame_offset), ("interval", L.interval),
                         ("loop_once", L.loop_once), ("zbuffer", L.zbuffer),
                         ("ztest", L.ztest), ("billboard", L.billboard),
                         ("lev", L.lev), ("mix_opt", L.mix_opt),
                         ("x_self", L.x_self), ("y_self", L.y_self),
                         ("z_self", L.z_self)):
            if val:
                ld[key] = val
        d["layers"].append(ld)
    return d


def build_linkage(db: EffectDB, *, with_geometry: bool = True) -> dict:
    """The machine-readable bundle written to out/effects/linkage.json."""
    doc: dict = {
        "schema": 1,
        "source_root": str(db.root),
        "notes": "See docs/effects.md. show_time: 0=at motion start, 1=at motion end.",
        "wildcard": WILDCARD,
    }

    doc["weapon_types"] = {
        t: {
            "name": db.weapon_type_names.get(t, ""),
            "hit_effect": w.hit_effect, "hit_sound": w.hit_sound,
            "blk_effect": w.blk_effect, "blk_sound": w.blk_sound,
        } for t, w in sorted(db.weapon_impact.items())
    }
    for t, n in sorted(db.weapon_type_names.items()):
        doc["weapon_types"].setdefault(t, {"name": n, "hit_effect": "",
                                           "hit_sound": "", "blk_effect": "",
                                           "blk_sound": ""})

    weapons: dict[str, dict] = {}
    for app in sorted(db.weapon_appearances):
        es = db.effects_for_weapon(app)
        if not (es.aura or es.attack):
            continue
        weapons[app] = {
            "type": es.weapon_type, "type_name": es.type_name,
            "aura": es.aura, "attack": es.attack,
            "hit_effect": es.impact.hit_effect if es.impact else "",
            "blk_effect": es.impact.blk_effect if es.impact else "",
        }
    doc["weapon_appearances"] = weapons

    doc["action_effect_rules"] = [
        {"shape": r.shape, "action": r.action, "group_hi": r.group_hi,
         "group_lo": r.group_lo, "appearance": r.appearance, "effect": r.effect}
        for r in db.action_rules]
    doc["action_map_rules"] = [
        {"shape": r.shape, "action": r.action, "terrain": r.terrain,
         "effect": r.effect, "show_time": r.show_time, "dir_enable": r.dir_enable}
        for r in db.action_map]
    doc["action_delay"] = db.action_delay

    geo: dict[str, dict] = {}
    if with_geometry:
        for oid, path in sorted(db.objs.items()):
            obj = load_effect_object(db.assets, oid, path)
            entry = {"path": obj.logical, "kinds": sorted(obj.kinds),
                     "frames": obj.frame_count,
                     "effective_frames": obj.effective_frames,
                     "parts": len(obj.parts)}
            if obj.error:
                entry["error"] = obj.error
            if obj.source:
                entry["source"] = obj.source
            geo[oid] = entry

    doc["effects"] = {}
    for name in sorted(db.effects):
        ed = _effect_json(db, name)
        if ed is None:
            continue
        if geo:
            frames = max([geo.get(L["effect_id"], {}).get("frames", 0)
                          for L in ed["layers"]] or [0])
            eff = max([geo.get(L["effect_id"], {}).get("effective_frames", 0)
                       for L in ed["layers"]] or [0])
            ed["frames"] = frames
            ed["effective_frames"] = eff
            play = eff or frames
            if play and not ed["endless"]:
                ed["duration_ms"] = (ed["delay_ms"]
                                     + ed["loop_time"] * play * ed["frame_interval_ms"]
                                     + max(0, ed["loop_time"] - 1) * ed["loop_interval_ms"])
        doc["effects"][name] = ed

    # bows have no swing trail; their visible projectile comes from here.
    # Key: "<bowAppearance>.<arrowAppearance>", 999999 = any bow.
    doc["flying_objects"] = {
        k: {"simple_obj_id": v.get("SimpleObjID", ""),
            "effect": v.get("EffectIndex", ""),
            "flying_sound": v.get("FlyingSound", ""),
            "hit_sound": v.get("HitSound", ""),
            "target_effect": v.get("TargetEffect", "")}
        for k, v in sorted(db.flying.items())}

    doc["effect_objects"] = {k: v.replace("\\", "/") for k, v in sorted(db.objs.items())}
    doc["effect_textures"] = {k: v.replace("\\", "/") for k, v in sorted(db.textures.items())}
    doc["weapon_motion"] = {k: v.replace("\\", "/") for k, v in sorted(db.weapon_motion.items())}
    if geo:
        doc["effect_object_geometry"] = geo

    doc["coverage"] = coverage(db)
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_effect(db: EffectDB, name: str) -> None:
    e = db.resolve(name)
    if e is None:
        print(f"no effect named {name!r}")
        near = [n for n in db.effects if name.lower() in n.lower()][:12]
        if near:
            print("  did you mean:", ", ".join(near))
        return
    loops = "forever" if e.endless else f"{e.loop_time}x"
    print(f"[{e.name}]   ({e.source})")
    print(f"  timing     frameInterval={e.frame_interval} ms  ({e.fps:.1f} fps)  "
          f"loop={loops}  loopInterval={e.loop_interval} ms  delay={e.delay} ms")
    print(f"  offset     {e.offset}   colorEnable={e.color_enable}")
    for L in e.layers:
        print(f"  layer {L.index}: obj {L.effect_id:>6} -> {L.mesh_path or '(unmapped)'}"
              f"  {'OK' if L.mesh_found else 'MISSING'}")
        print(f"           tex {L.texture_id:>6} -> {L.texture_path or '(unmapped)'}"
              f"  {'OK' if L.texture_found else 'MISSING'}")
        print(f"           blend {L.asb_name} -> {L.adb_name}")
    for obj in db.load_geometry(name):
        if obj.error:
            print(f"  geom  {obj.logical}: {obj.error}")
            continue
        print(f"  geom  {obj.logical}  [{obj.source}]  {len(obj.parts)} part(s), "
              f"{obj.frame_count} frames ({obj.effective_frames} effective), "
              f"{'+'.join(sorted(obj.kinds))}")
        for p in obj.parts:
            if p.kind == PART_PHY and p.mesh is not None:
                m = p.motion
                print(f"          PHY  {p.name or '-':<14} verts={len(p.mesh.vertices):<5}"
                      f" faces={len(p.mesh.faces):<5}"
                      f" motion={m.encoding if m else 'none'}"
                      f" bones={m.bone_count if m else 0}"
                      f" frames={m.frame_count if m else 0}"
                      f" keys={len(m.keys) if m else 0}"
                      f" a/d/t={len(p.alpha_keys)}/{len(p.draw_keys)}/{len(p.tex_keys)}"
                      f" eff={p.effective_frames}"
                      f"{' uvgrid=%d' % p.uv_grid if p.tex_keys else ''}"
                      f"{' uvstep=%s' % (p.uv_step,) if p.uv_step else ''}")
            elif p.kind == PART_SHAPE and p.shape is not None:
                print(f"          SHAP {p.name or '-':<14} line={p.shape.line}"
                      f" segments={p.shape.segments}"
                      f" smot_frames={p.smotion.frame_count if p.smotion else 0}")
            elif p.kind == PART_PARTICLE:
                print(f"          {p.name:<5}{'':<14} {p.raw_size} bytes (undecoded)")
    ms = db.duration_ms(name)
    if ms is not None:
        print(f"  duration   {ms:.0f} ms")


def _print_weapon(db: EffectDB, app: str) -> None:
    es = db.effects_for_weapon(app)
    print(f"weapon appearance {es.appearance}  type {es.weapon_type} "
          f"({es.type_name or 'unnamed'})"
          f"{'' if es.in_weapon_ini else '   [NOT in weapon.ini]'}")
    print(f"  aura (action 999) : {es.aura or '(none)'}")
    if es.attack:
        for a, n in es.attack.items():
            print(f"  action {a}         : {n}")
    else:
        print("  attack trail      : (none)")
    if es.impact:
        w = es.impact
        print(f"  hit               : {w.hit_effect}   {w.hit_sound}")
        print(f"  block             : {w.blk_effect}   {w.blk_sound}")
    if es.motion_meshes:
        print(f"  weapon motion meshes: {len(es.motion_meshes)}")
        for k, v in list(es.motion_meshes.items())[:6]:
            print(f"      +{k} -> {v}")
    print()
    for n in es.effect_names:
        _print_effect(db, n)
        print()


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--weapon", help="weapon appearance ID, e.g. 410009")
    ap.add_argument("--effect", help="effect name, e.g. Flash4102")
    ap.add_argument("--action-map", nargs=3, metavar=("SHAPE", "ACTION", "TERRAIN"))
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--linkage", action="store_true",
                    help="write out/effects/linkage.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pretty", action="store_true",
                    help="indent out/effects/linkage.json (roughly 2x the size)")
    args = ap.parse_args(argv[1:])

    db = EffectDB(args.root)

    if args.weapon:
        if args.json:
            print(json.dumps(asdict(db.effects_for_weapon(args.weapon)),
                             indent=2, ensure_ascii=False))
        else:
            _print_weapon(db, args.weapon)
    if args.effect:
        if args.json:
            print(json.dumps(_effect_json(db, args.effect), indent=2, ensure_ascii=False))
        else:
            _print_effect(db, args.effect)
    if args.action_map:
        r = db.lookup_action_map(*args.action_map)
        print(r if r else "no match")
    if args.coverage:
        print(json.dumps(coverage(db), indent=2, ensure_ascii=False))
    if args.validate:
        print(json.dumps(validate(db), indent=2, ensure_ascii=False))
    if args.linkage:
        out = Path(args.out) if args.out else \
            Path(__file__).resolve().parent.parent / "out" / "effects" / "linkage.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = build_linkage(db)
        text = (json.dumps(doc, indent=1, ensure_ascii=False) if args.pretty
                else json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        out.write_text(text, "utf-8")
        print(f"wrote {out}  ({out.stat().st_size / 1e6:.2f} MB)")
        print(json.dumps(doc["coverage"], indent=2, ensure_ascii=False))
    if not any((args.weapon, args.effect, args.action_map, args.coverage,
                args.validate, args.linkage)):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
