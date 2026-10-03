#!/usr/bin/env python3
r"""gltfread.py -- a pure-stdlib glTF 2.0 reader: accessors, nodes, skins,
animations, and SAMPLING of an animation at an arbitrary time.

    from gltfread import Gltf
    g = Gltf.load("scene.gltf")            # .gltf + .bin / data: URIs, or .glb
    world = g.world_matrices()             # rest pose, one 4x4 per node
    anim = g.animation(0)
    posed = g.world_matrices(g.sample(anim, 1.25))

WHY THIS EXISTS
---------------
`tools/c3retarget.py` puts a Mixamo clip onto a C3 body track. Its donor is
a Sketchfab glTF and the repo is pure stdlib in `core/` and `tools/`
(`tools/c3anim.py` does its matrices as tuples of tuples for the same
reason), so the reader has to be written, and it has to be written to the
SPEC rather than to the one file in hand: the next donor will be a `.glb`,
or carry `byteStride`, or normalised `unsigned short` weights, and a reader
that assumed the first file's layout would misread the second one silently.

CONVENTIONS, STATED ONCE
------------------------
* Matrices here are 4x4 tuples of ROWS in **column-vector** convention --
  ``p' = M . p`` with the translation in column 3 -- because that is how the
  glTF spec writes them (its 16 floats are column-major storage of exactly
  this matrix). A node's local matrix is ``T . R . S`` and a node's world
  matrix is ``world(parent) . local``. This is the OPPOSITE convention from
  a C3 ``MOTI`` matrix (row-vector, translation in row 3); the conversion
  is the retargeter's job and is tested there, not here.
* Quaternions are ``(x, y, z, w)`` as glTF stores them. A rotation sampled
  LINEAR is slerped along the shorter arc and renormalised.
* Times are seconds. Sampling before the first key holds the first value,
  after the last key holds the last -- the spec's rule, and also what makes
  a clip's tail well defined without the caller special-casing it.

WHAT IT REFUSES, BY NAME
------------------------
* ``CUBICSPLINE`` samplers: the spec's Hermite form needs in/out tangents
  and a different `values` layout; sampling one as LINEAR would produce a
  wrong pose that looked plausible. Refused at sample time with the
  channel named. (A reader that can *load* the file is still useful for
  inspection, so loading does not refuse.)
* Sparse accessors, and `MAT2`/`MAT3` accessors of 1- or 2-byte components
  (the spec pads their columns to 4 bytes). Neither occurs in a Mixamo
  export; a wrong decode of either would be silent, so both raise.
* A `.glb` whose header is not version 2, or whose first chunk is not JSON.

    py -3 core/gltfread.py <file.gltf|file.glb>     # a structural summary
"""
from __future__ import annotations

import base64
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

__all__ = ["Gltf", "GltfError", "Animation", "Channel", "Skin",
           "mat4_identity", "mat4_mul", "mat4_from_trs",
           "mat4_from_column_major", "quat_slerp", "quat_normalise"]


class GltfError(ValueError):
    """A file this reader cannot decode faithfully. The message names why."""


#: componentType -> (struct code, byte size)
_COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
              5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
#: The spec's normalisation for `normalized: true` integer accessors.
_NORMALISE = {5120: lambda v: max(v / 127.0, -1.0),
              5121: lambda v: v / 255.0,
              5122: lambda v: max(v / 32767.0, -1.0),
              5123: lambda v: v / 65535.0}
_TYPE_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
           "MAT2": 4, "MAT3": 9, "MAT4": 16}

GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942


# ---------------------------------------------------------------------------
# small matrix / quaternion helpers (column-vector convention, see header)
# ---------------------------------------------------------------------------

def mat4_identity():
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat4_mul(a, b):
    """Row-of-rows product ``a . b``."""
    out = []
    for r in range(4):
        ar = a[r]
        out.append(tuple(ar[0] * b[0][c] + ar[1] * b[1][c]
                         + ar[2] * b[2][c] + ar[3] * b[3][c]
                         for c in range(4)))
    return tuple(out)


def mat4_from_column_major(m16):
    """glTF stores a matrix as 16 floats, COLUMN-major: element ``c*4 + r``
    is row ``r``, column ``c`` of the column-vector matrix."""
    if len(m16) != 16:
        raise GltfError("a matrix accessor element is 16 floats, got %d"
                        % len(m16))
    return tuple(tuple(float(m16[c * 4 + r]) for c in range(4))
                 for r in range(4))


def quat_to_mat3(q):
    """``(x, y, z, w)`` -> the 3x3 that rotates a COLUMN vector by ``q``.

    Normalised first: an accessor may carry a quaternion a float short of
    unit length, and the matrix of an un-normalised quaternion carries a
    scale that would then be read as one.
    """
    x, y, z, w = quat_normalise(q)
    return ((1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
            (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
            (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)))


def quat_normalise(q):
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-30:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / n, y / n, z / n, w / n)


def quat_slerp(a, b, u):
    """Spherical interpolation along the SHORTER arc, ``u`` in [0, 1].

    ``q`` and ``-q`` are the same rotation, so when the dot is negative one
    operand is negated first; without that a 10-degree step between two
    keys reads as a 350-degree one and the limb whips round the long way.
    Near-parallel inputs fall back to a normalised lerp, which is what the
    slerp tends to and does not divide by a vanishing sine.
    """
    ax, ay, az, aw = quat_normalise(a)
    bx, by, bz, bw = quat_normalise(b)
    dot = ax * bx + ay * by + az * bz + aw * bw
    if dot < 0.0:
        bx, by, bz, bw, dot = -bx, -by, -bz, -bw, -dot
    if dot > 0.9995:
        return quat_normalise((ax + (bx - ax) * u, ay + (by - ay) * u,
                               az + (bz - az) * u, aw + (bw - aw) * u))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    s = math.sin(theta)
    wa = math.sin((1.0 - u) * theta) / s
    wb = math.sin(u * theta) / s
    return (ax * wa + bx * wb, ay * wa + by * wb,
            az * wa + bz * wb, aw * wa + bw * wb)


def mat4_from_trs(t=None, r=None, s=None):
    """``T . R . S`` for a node's translation / rotation (xyzw) / scale."""
    tx, ty, tz = t if t is not None else (0.0, 0.0, 0.0)
    sx, sy, sz = s if s is not None else (1.0, 1.0, 1.0)
    R = quat_to_mat3(r) if r is not None else ((1.0, 0.0, 0.0),
                                                (0.0, 1.0, 0.0),
                                                (0.0, 0.0, 1.0))
    return ((R[0][0] * sx, R[0][1] * sy, R[0][2] * sz, float(tx)),
            (R[1][0] * sx, R[1][1] * sy, R[1][2] * sz, float(ty)),
            (R[2][0] * sx, R[2][1] * sy, R[2][2] * sz, float(tz)),
            (0.0, 0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# the decoded pieces
# ---------------------------------------------------------------------------

@dataclass
class Channel:
    """One animated property of one node."""
    node: int
    path: str                    # translation | rotation | scale | weights
    interpolation: str           # LINEAR | STEP | CUBICSPLINE
    times: list                  # seconds, increasing
    values: list                 # one tuple per key (3 per key for CUBICSPLINE)
    sampler: int = -1

    @property
    def duration(self) -> float:
        return float(self.times[-1]) if self.times else 0.0


@dataclass
class Animation:
    index: int
    name: str
    channels: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((c.duration for c in self.channels), default=0.0)

    @property
    def start(self) -> float:
        return min((float(c.times[0]) for c in self.channels if c.times),
                   default=0.0)

    def animated_nodes(self) -> list:
        return sorted({c.node for c in self.channels})


@dataclass
class Skin:
    index: int
    name: str
    joints: list
    inverse_bind: list           # one 4x4 per joint, or [] when absent
    skeleton: int | None = None


# ---------------------------------------------------------------------------
# the reader
# ---------------------------------------------------------------------------

class Gltf:
    """A loaded glTF 2.0 document with its buffers resolved to bytes."""

    def __init__(self, doc: dict, buffers: list, source: str = ""):
        if not isinstance(doc, dict) or "asset" not in doc:
            raise GltfError("not a glTF document: no `asset` object")
        ver = str(doc["asset"].get("version", ""))
        if not ver.startswith("2"):
            raise GltfError("glTF version %r is not 2.x" % ver)
        self.doc = doc
        self.buffers = buffers
        self.source = source
        self.nodes = list(doc.get("nodes", []))
        self._parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get("children", ()) or ():
                if c in self._parent:
                    raise GltfError("node %d has two parents (%d and %d)"
                                    % (c, self._parent[c], i))
                self._parent[int(c)] = i
        self._acc_cache: dict = {}
        self._anim_cache: dict = {}

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, path) -> "Gltf":
        p = Path(path)
        data = p.read_bytes()
        return cls.from_bytes(data, base_dir=p.parent, source=str(p))

    @classmethod
    def from_bytes(cls, data: bytes, base_dir=None, source: str = "") -> "Gltf":
        """`.glb` when the magic says so, otherwise JSON text."""
        bin_chunk = None
        if data[:4] == GLB_MAGIC:
            if len(data) < 12:
                raise GltfError("GLB header truncated")
            _magic, version, length = struct.unpack_from("<4sII", data, 0)
            if version != 2:
                raise GltfError("GLB version %d is not 2" % version)
            if length > len(data):
                raise GltfError("GLB declares %d bytes, file has %d"
                                % (length, len(data)))
            off = 12
            doc = None
            while off + 8 <= length:
                clen, ctype = struct.unpack_from("<II", data, off)
                body = data[off + 8:off + 8 + clen]
                if len(body) != clen:
                    raise GltfError("GLB chunk at %d truncated" % off)
                if doc is None:
                    if ctype != _CHUNK_JSON:
                        raise GltfError("GLB first chunk is not JSON")
                    doc = json.loads(body.decode("utf-8"))
                elif ctype == _CHUNK_BIN and bin_chunk is None:
                    bin_chunk = bytes(body)
                off += 8 + clen
                off += (-off) % 4
            if doc is None:
                raise GltfError("GLB carries no JSON chunk")
        else:
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError) as e:
                raise GltfError("not JSON and not GLB: %s" % e) from None

        buffers = []
        for i, b in enumerate(doc.get("buffers", [])):
            uri = b.get("uri")
            if uri is None:
                if i == 0 and bin_chunk is not None:
                    blob = bin_chunk
                else:
                    raise GltfError("buffer %d has no uri and no GLB BIN chunk"
                                    % i)
            elif uri.startswith("data:"):
                blob = _decode_data_uri(uri)
            else:
                if base_dir is None:
                    raise GltfError("buffer %d names external file %r but the "
                                    "document was loaded from bytes with no "
                                    "base directory" % (i, uri))
                fp = Path(base_dir) / unquote(uri)
                if not fp.is_file():
                    raise GltfError("buffer %d: %s does not exist" % (i, fp))
                blob = fp.read_bytes()
            declared = int(b.get("byteLength", len(blob)))
            if len(blob) < declared:
                raise GltfError("buffer %d declares %d bytes, has %d"
                                % (i, declared, len(blob)))
            buffers.append(blob)
        return cls(doc, buffers, source=source)

    # -- accessors --------------------------------------------------------

    def accessor(self, index: int) -> list:
        """Decoded elements: floats for SCALAR, tuples otherwise.

        Honours `byteStride` (an interleaved vertex buffer), `byteOffset` on
        both the view and the accessor, and `normalized` integers. Cached:
        an animation with 64 channels reads its time accessors repeatedly.
        """
        if index in self._acc_cache:
            return self._acc_cache[index]
        accs = self.doc.get("accessors", [])
        if not (0 <= index < len(accs)):
            raise GltfError("accessor %d does not exist" % index)
        a = accs[index]
        if "sparse" in a:
            raise GltfError("accessor %d is sparse; not supported" % index)
        ctype = int(a["componentType"])
        if ctype not in _COMPONENT:
            raise GltfError("accessor %d: unknown componentType %d"
                            % (index, ctype))
        code, size = _COMPONENT[ctype]
        atype = str(a["type"])
        if atype not in _TYPE_N:
            raise GltfError("accessor %d: unknown type %r" % (index, atype))
        n = _TYPE_N[atype]
        if atype in ("MAT2", "MAT3") and size < 4:
            raise GltfError("accessor %d: %s of %d-byte components has padded "
                            "columns; not supported" % (index, atype, size))
        count = int(a["count"])
        norm = bool(a.get("normalized", False))

        if "bufferView" not in a:
            # The spec allows an accessor with no view (all zeros, for
            # sparse to overlay). Sparse is refused above, so this is zeros.
            zero = 0.0 if code == "f" else 0
            out = [zero] * count if n == 1 else [tuple([zero] * n)] * count
            self._acc_cache[index] = out
            return out

        views = self.doc.get("bufferViews", [])
        bv_i = int(a["bufferView"])
        if not (0 <= bv_i < len(views)):
            raise GltfError("accessor %d names bufferView %d which does not "
                            "exist" % (index, bv_i))
        bv = views[bv_i]
        buf = self.buffers[int(bv["buffer"])]
        base = int(bv.get("byteOffset", 0)) + int(a.get("byteOffset", 0))
        elem = n * size
        stride = int(bv.get("byteStride", 0)) or elem
        if stride < elem:
            raise GltfError("accessor %d: byteStride %d is narrower than one "
                            "element (%d bytes)" % (index, stride, elem))
        end = base + (count - 1) * stride + elem if count else base
        limit = int(bv.get("byteOffset", 0)) + int(bv["byteLength"])
        if count and (end > limit or end > len(buf)):
            raise GltfError("accessor %d runs past its bufferView (%d > %d)"
                            % (index, end, min(limit, len(buf))))
        fmt = "<%d%s" % (n, code)
        conv = _NORMALISE.get(ctype) if norm else None
        out = []
        for k in range(count):
            vals = struct.unpack_from(fmt, buf, base + k * stride)
            if conv is not None:
                vals = tuple(conv(v) for v in vals)
            elif code != "f":
                vals = tuple(int(v) for v in vals)
            else:
                vals = tuple(float(v) for v in vals)
            out.append(vals[0] if n == 1 else vals)
        self._acc_cache[index] = out
        return out

    # -- nodes ------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    def node_name(self, i: int) -> str:
        return str(self.nodes[i].get("name", "") or "")

    def parent(self, i: int):
        return self._parent.get(int(i))

    def children(self, i: int) -> list:
        return [int(c) for c in (self.nodes[i].get("children", ()) or ())]

    def roots(self) -> list:
        return [i for i in range(len(self.nodes)) if i not in self._parent]

    def find_node(self, name: str):
        """The index of the node with exactly this name, or None."""
        for i, n in enumerate(self.nodes):
            if n.get("name") == name:
                return i
        return None

    def node_trs(self, i: int, override=None) -> dict:
        """The node's translation/rotation/scale, with `override` (a dict
        from `sample`) applied on top. A node stored as `matrix` has no TRS
        to override and is returned as ``{"matrix": ...}``."""
        n = self.nodes[i]
        if "matrix" in n and not override:
            return {"matrix": n["matrix"]}
        if "matrix" in n and override:
            raise GltfError("node %d (%r) is stored as a matrix and is also "
                            "animated; the spec forbids that"
                            % (i, n.get("name")))
        out = {"translation": tuple(n.get("translation", (0.0, 0.0, 0.0))),
               "rotation": tuple(n.get("rotation", (0.0, 0.0, 0.0, 1.0))),
               "scale": tuple(n.get("scale", (1.0, 1.0, 1.0)))}
        if override:
            for k in ("translation", "rotation", "scale"):
                if k in override:
                    out[k] = tuple(override[k])
        return out

    def local_matrix(self, i: int, override=None):
        trs = self.node_trs(i, override)
        if "matrix" in trs:
            return mat4_from_column_major(trs["matrix"])
        return mat4_from_trs(trs["translation"], trs["rotation"],
                             trs["scale"])

    def world_matrices(self, pose=None) -> list:
        """One column-vector 4x4 per node: ``world(parent) . local``.

        `pose` is ``{node: {path: value}}`` as `sample` returns; None is the
        rest pose (the nodes' static TRS with nothing applied).
        """
        pose = pose or {}
        world = [None] * len(self.nodes)

        def visit(i, depth=0):
            if world[i] is not None:
                return world[i]
            if depth > len(self.nodes):
                raise GltfError("node hierarchy has a cycle at node %d" % i)
            local = self.local_matrix(i, pose.get(i))
            p = self._parent.get(i)
            world[i] = local if p is None else mat4_mul(visit(p, depth + 1),
                                                         local)
            return world[i]

        for i in range(len(self.nodes)):
            visit(i)
        return world

    @staticmethod
    def position(m4) -> tuple:
        return (m4[0][3], m4[1][3], m4[2][3])

    # -- skins ------------------------------------------------------------

    def skin(self, index: int = 0) -> Skin:
        skins = self.doc.get("skins", [])
        if not (0 <= index < len(skins)):
            raise GltfError("skin %d does not exist (%d skin(s))"
                            % (index, len(skins)))
        s = skins[index]
        joints = [int(j) for j in s.get("joints", [])]
        ibm = []
        if "inverseBindMatrices" in s:
            raw = self.accessor(int(s["inverseBindMatrices"]))
            if len(raw) != len(joints):
                raise GltfError("skin %d: %d inverse bind matrices for %d "
                                "joints" % (index, len(raw), len(joints)))
            ibm = [mat4_from_column_major(m) for m in raw]
        return Skin(index, str(s.get("name", "") or ""), joints, ibm,
                    s.get("skeleton"))

    def skin_count(self) -> int:
        return len(self.doc.get("skins", []))

    # -- animations -------------------------------------------------------

    def animation_count(self) -> int:
        return len(self.doc.get("animations", []))

    def animation(self, index: int = 0) -> Animation:
        if index in self._anim_cache:
            return self._anim_cache[index]
        anims = self.doc.get("animations", [])
        if not (0 <= index < len(anims)):
            raise GltfError("animation %d does not exist (%d animation(s))"
                            % (index, len(anims)))
        a = anims[index]
        samplers = a.get("samplers", [])
        out = Animation(index, str(a.get("name", "") or ""))
        for ch in a.get("channels", []):
            tgt = ch.get("target", {})
            if "node" not in tgt:
                continue                      # an extension target; not ours
            si = int(ch["sampler"])
            if not (0 <= si < len(samplers)):
                raise GltfError("animation %d: channel names sampler %d of %d"
                                % (index, si, len(samplers)))
            s = samplers[si]
            interp = str(s.get("interpolation", "LINEAR"))
            times = [float(t) for t in self.accessor(int(s["input"]))]
            values = list(self.accessor(int(s["output"])))
            expect = len(times) * (3 if interp == "CUBICSPLINE" else 1)
            if len(values) != expect:
                raise GltfError("animation %d sampler %d: %d values for %d "
                                "keys (%s)" % (index, si, len(values),
                                               len(times), interp))
            for k in range(1, len(times)):
                if times[k] < times[k - 1]:
                    raise GltfError("animation %d sampler %d: input times are "
                                    "not increasing at key %d" % (index, si, k))
            out.channels.append(Channel(int(tgt["node"]), str(tgt["path"]),
                                        interp, times, values, si))
        self._anim_cache[index] = out
        return out

    @staticmethod
    def sample_channel(ch: Channel, t: float):
        """The channel's value at time `t` (clamped to its key range)."""
        times = ch.times
        if not times:
            raise GltfError("channel on node %d has no keys" % ch.node)
        if ch.interpolation == "CUBICSPLINE":
            raise GltfError("channel %s on node %d is CUBICSPLINE; this reader "
                            "samples LINEAR and STEP only"
                            % (ch.path, ch.node))
        if ch.interpolation not in ("LINEAR", "STEP"):
            raise GltfError("channel %s on node %d: unknown interpolation %r"
                            % (ch.path, ch.node, ch.interpolation))
        if t <= times[0]:
            return _as_tuple(ch.values[0])
        if t >= times[-1]:
            return _as_tuple(ch.values[-1])
        hi = _bisect_right(times, t)
        lo = hi - 1
        if ch.interpolation == "STEP":
            return _as_tuple(ch.values[lo])
        span = times[hi] - times[lo]
        u = 0.0 if span <= 0.0 else (t - times[lo]) / span
        a, b = _as_tuple(ch.values[lo]), _as_tuple(ch.values[hi])
        if ch.path == "rotation":
            return quat_slerp(a, b, u)
        return tuple(x + (y - x) * u for x, y in zip(a, b))

    def sample(self, anim: Animation, t: float) -> dict:
        """``{node: {path: value}}`` at time `t` -- feed to `world_matrices`."""
        pose: dict = {}
        for ch in anim.channels:
            if ch.path not in ("translation", "rotation", "scale"):
                continue
            pose.setdefault(ch.node, {})[ch.path] = self.sample_channel(ch, t)
        return pose

    # -- reporting --------------------------------------------------------

    def summary(self) -> str:
        lines = ["%s" % (self.source or "<bytes>"),
                 "  generator %s, %d node(s), %d mesh(es), %d skin(s), "
                 "%d animation(s), %d buffer(s) (%d bytes)"
                 % (self.doc["asset"].get("generator", "?"), len(self.nodes),
                    len(self.doc.get("meshes", [])), self.skin_count(),
                    self.animation_count(), len(self.buffers),
                    sum(len(b) for b in self.buffers))]
        for i in range(self.skin_count()):
            s = self.skin(i)
            lines.append("  skin %d %r: %d joints, skeleton %s"
                         % (i, s.name, len(s.joints), s.skeleton))
        for i in range(self.animation_count()):
            a = self.animation(i)
            kinds = sorted({c.interpolation for c in a.channels})
            lines.append("  animation %d %r: %d channel(s) on %d node(s), "
                         "%d key(s), %.3f s, %s"
                         % (i, a.name, len(a.channels),
                            len(a.animated_nodes()),
                            sum(len(c.times) for c in a.channels),
                            a.duration, "/".join(kinds)))
        return "\n".join(lines)


def _as_tuple(v):
    return tuple(v) if isinstance(v, (list, tuple)) else (v,)


def _bisect_right(times, t):
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if t < times[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _decode_data_uri(uri: str) -> bytes:
    head, _, payload = uri.partition(",")
    if not _:
        raise GltfError("malformed data: URI")
    if ";base64" in head:
        return base64.b64decode(payload)
    return unquote(payload).encode("latin-1")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    for p in argv:
        try:
            g = Gltf.load(p)
        except (GltfError, OSError) as e:
            print("%s: %s" % (p, e))
            return 1
        print(g.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
