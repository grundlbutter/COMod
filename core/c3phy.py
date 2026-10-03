r"""
c3phy.py -- parser for the C3 `PHY*` (physique / mesh) chunk body.

The layout below was recovered by disassembling `graphic.dll!Phy_Load`
(RVA 0x59CB0) instruction by instruction, NOT by fitting strides against
sample files.  Every offset and every size here is stated by the code.
See docs/modding.md for the full derivation and the RVA of each read.

Key facts, all VERIFIED from code:

  * `Phy_Load` reads through a `C3DataLoader` vtable:
        [+0x10] GetChunk(dst8)  [+0x18] Read(dst,n)  [+0x20] Seek(off,origin)
  * The five PHY variants differ ONLY in the per-vertex on-disk record.
    The dispatch is `graphic.dll` RVA 0x25BEC: it compares the chunk tag
    against "PHY" and switches on the 4th byte, passing four bools to
    `Phy_Load`.
  * Vertex counts and face counts are each stored as TWO u32s which the
    loader simply ADDS.  The split is discarded -- there is no submesh
    partitioning at this level.
  * Indices are u16, three per face, one contiguous block: a triangle LIST.
  * The 64-byte matrix that follows the bounding box IS APPLIED to the vertex
    positions at load time (RVA 0x5A735).

Usage:

    from c3phy import parse_phy, PhyMesh
    mesh = parse_phy(b'PHY4', chunk_body)
    print(mesh.name, len(mesh.vertices), len(mesh.faces))
"""

from __future__ import annotations

import copy as _copy
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------
# variant table  --  graphic.dll RVA 0x25BEC..0x25C7A (the caller of Phy_Load)
#
#   tag     arg4 has_normal   arg5 gen_normal   arg6 legacy_gap   arg7 step
#   "PHY "      0                 1                 1                0
#   "PHY2"      1                 0                 1                0
#   "PHY3"      1                 0                 0                0
#   "PHY4"      0                 1                 0                0
#   "PHY5"      1                 0                 0                1
#
# and Phy_Load RVA 0x59E71..0x59FD5 turns those into the read pattern:
#
#   has_normal & legacy_gap : Read(v+0x00,0x0C) Seek(+0x24) Read(v+0x0C,0x28)
#   has_normal & step       : Read(all vertices, 0x3C*n)  -- one block
#   has_normal              : Read(v+0x00,0x34)
#  !has_normal & legacy_gap : Read(v+0x00,0x0C) Seek(+0x24) Read(v+0x0C,0x1C)
#  !has_normal              : Read(v+0x00,0x28)
# --------------------------------------------------------------------------

VARIANTS: dict[bytes, dict] = {
    b"PHY ": {"has_normal": False, "gen_normal": True,  "legacy_gap": True,  "step": False},
    b"PHY2": {"has_normal": True,  "gen_normal": False, "legacy_gap": True,  "step": False},
    b"PHY3": {"has_normal": True,  "gen_normal": False, "legacy_gap": False, "step": False},
    b"PHY4": {"has_normal": False, "gen_normal": True,  "legacy_gap": False, "step": False},
    b"PHY5": {"has_normal": True,  "gen_normal": False, "legacy_gap": False, "step": True},
}

# In-memory vertex is always 0x3C (60) bytes -- `Phy_Load` allocates
# `0x3C * vertexCount` at RVA 0x59E39 and memsets it.  The on-disk record is a
# prefix of that, optionally with a 36-byte legacy gap after the position.
VERTEX_STRIDE = 0x3C

LEGACY_GAP = 0x24        # 36 bytes skipped in "PHY " / "PHY2"


def disk_vertex_size(tag: bytes) -> int:
    """Bytes consumed per vertex in the file, for this variant."""
    v = VARIANTS[tag]
    tail = 0x28 if v["has_normal"] else 0x1C     # bytes written at v+0x0C
    if v["step"]:
        return VERTEX_STRIDE                     # 60, read as one block
    if v["legacy_gap"]:
        return 0x0C + LEGACY_GAP + tail
    return 0x0C + tail                           # 0x34 or 0x28


@dataclass
class Vertex:
    """One source vertex, exactly as `Phy_Load` lays it out in memory.

    Field offsets are VERIFIED:
      0x00 position   written by every variant                    (RVA 0x59EFD)
      0x0C uv0        -> GPU TEXCOORD0                            (RVA 0x5A774)
      0x14 unknown4   read from file, never consumed by graphic.dll
      0x18 bone0      -> GPU BLENDINDICES0, LOW BYTE ONLY         (RVA 0x5A784)
      0x1C bone1      -> GPU BLENDINDICES1, LOW BYTE ONLY         (RVA 0x5A78D)
      0x20 weight0    -> GPU BLENDWEIGHT0 as round(w*255)         (RVA 0x5A796)
      0x24 weight1    only tested against 0 to decide whether bone1
                       participates (RVA 0x5A284); the GPU weight1 is
                       derived as 255-weight0, NOT read from here
      0x28 normal     absent in "PHY " / "PHY4" (generated instead)
      0x34 uv1        present only in "PHY5". READ FROM FILE; its GPU
                      destination is NOT established -- see below.

    **THE uv1 LINE USED TO READ "-> GPU TEXCOORD1" AND THAT WAS NOT VERIFIED.**
    It was the one entry in this block with no RVA beside it while all four of
    its neighbours carry one, and the absence was the tell. Corrected
    2026-09-21 by reading the shipped shader source out of
    `Env_DX9/graphic.dll` (identical on 7878 and 7939):

      * Across 64 vertex-shader sources, the TEXCOORD1 **input** slot holds
        ``c3_BoneIndexWeight`` in **28** of them and ``c3_TexCoord1`` in 13;
        four more put ``c3_TexCoord1`` on TEXCOORD2 because bones already
        occupy slot 1. **So the slot is not fixed, and for a SKINNED mesh
        TEXCOORD1 is bone index/weight data.** PHY5 meshes are skinned.
      * A second vertex UV (``c3_TexCoord1``) therefore does exist, but this
        field's mapping to it is unproven, and the slot depends on skinning.

    What this field is NOT: the thing CCFL kind 9 drives. That annotation is
    the uniform ``c3_UVAnimStep``, added to ``c3_TexCoord0`` in 25 of its 30
    shader uses -- the PRIMARY UV. See `core/c3ccfl.KIND_UV_ANIM_STEP`.

    `0x14 unknown4` above is the honest precedent for this shape: read from
    file, no consumer found. Until a store into a vertex declaration or a
    ``SetStreamSource``/declaration site is traced, `uv1` belongs in that
    category rather than in the arrow-bearing one.

    **AND THE ORIGINAL ARROW IS BUILD-DEPENDENT RATHER THAN SIMPLY WRONG.**
    What made it false on the 32-bit DX9 client is that TEXCOORD1 was already
    taken by the packed bone attribute. **CCO does not have that collision**
    -- measured in `Classic Conquer 2.0/bin/64/graphic.dll` (64-bit, MSVC
    2022, SM4):

      * ``c3_BoneIndexWeight``  140 occurrences on 7878 -> **0** on CCO
      * ``BLENDINDICES`` / ``BLENDWEIGHT``   1 / 1 on 7878 -> **35 / 35** on CCO

    CCO moved skinning onto the real semantics, which frees TEXCOORD1. So in
    CCO ``uv1 -> TEXCOORD1`` is plausible and untested, while on the 32-bit
    DX9 renderer it is contradicted. **Do not carry either reading across
    that boundary**; it is the same lesson as the RVAs above, which also do
    not transfer between these DLLs.

    (The same codebase spans both: ``CMyBitmap`` appears 152 times in CCO's
    renderer and 155 in 7878's, and kind 9's ``c3_UVAnimStep`` is present in
    both -- 88 occurrences in CCO against 69 -- still added to
    ``c3_TexCoord0``/``c3_TexCoord1``. It is a port, not a rewrite.)
    """
    px: float = 0.0
    py: float = 0.0
    pz: float = 0.0
    u0: float = 0.0
    v0: float = 0.0
    unknown4: int = 0
    bone0: int = 0
    bone1: int = 0
    weight0: float = 0.0
    weight1: float = 0.0
    nx: float = 0.0
    ny: float = 0.0
    nz: float = 0.0
    u1: float = 0.0
    v1: float = 0.0
    gap: bytes = b""          # the 36 raw bytes the loader Seeks past in
                              # "PHY " / "PHY2".  Never interpreted; kept
                              # verbatim so a writer can round-trip exactly.

    @property
    def position(self):
        return (self.px, self.py, self.pz)

    @property
    def normal(self):
        return (self.nx, self.ny, self.nz)


@dataclass
class C3Key:
    """`C3Key` from TQ's leaked c3_key.h -- three keyframe channels.
    Each frame record is 16 bytes.  VERIFIED at RVA 0x5B0E8..0x5B1E5;
    the counts land at C3Phy +0xA0 / +0xB0 / +0xC0 and the arrays at
    +0xA8 / +0xB8 / +0xC8, which is exactly the header's field order."""
    alphas: list[bytes] = field(default_factory=list)
    draws: list[bytes] = field(default_factory=list)
    change_texs: list[bytes] = field(default_factory=list)


@dataclass
class PhyMesh:
    tag: bytes = b"PHY "
    name: str = ""
    unknown0: int = 0                 # 0 or 2; read at 0x59DEC and IGNORED
    vertex_count_a: int = 0
    vertex_count_b: int = 0
    face_count_a: int = 0
    face_count_b: int = 0
    vertices: list[Vertex] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    label: str = ""                   # the length-prefixed tag, e.g. C3EXP_COLOR
    is_c3exp_color: bool = False
    bbox_min: tuple = (0.0, 0.0, 0.0)
    bbox_max: tuple = (0.0, 0.0, 0.0)
    matrix: tuple = ()                # 16 floats, row-major as stored
    frame_count: int = 0              # C3Phy+0x190
    keys: C3Key = field(default_factory=C3Key)
    #: The ``STEP`` tag's two f32: the per-tick U/V scroll rate.
    #:
    #: **RETYPED 2026-09-16 (M8). It was `tuple[int, int]` and the dwords were
    #: always floats.** `c3write` wrote them back as u32, so the round trip was
    #: byte-exact ONLY BECAUSE THE WRONG READ AND THE WRONG WRITE CANCELLED --
    #: correct output from two errors, which stops being correct the moment
    #: either side is fixed alone. Measured on this tree:
    #:
    #:     baseline                  4,781 / 4,781 byte-exact   PASS
    #:     ONE side moved            3,588 / 4,781, 1,193 fail  FAIL
    #:     BOTH sides moved together 4,781 / 4,781 byte-exact   PASS
    #:
    #: The round-trip gate is loud about one side and STRUCTURALLY BLIND to
    #: both; `tests/test_uv_step_retype.py` covers the half it cannot, by
    #: pinning the rendered UVs from before the change in BOTH engines.
    step: Optional[tuple[float, float]] = None
    two_sided: bool = False
    billboard: int = 0                # 0 none, 1 BILB, 2 BIB2, 3 BIB3, 4 BIB4
    step1: Optional[tuple[float, float]] = None
    step2: Optional[tuple[float, float]] = None
    bytes_consumed: int = 0
    trailing: int = 0                 # unconsumed bytes left in the chunk

    # ---- verbatim capture, for byte-exact re-serialisation (tools/c3write.py)
    # None of these change what the engine does; they exist so a writer can
    # reproduce the original bytes of an unmodified mesh exactly.
    name_raw: bytes = b""             # the nameLen bytes, before NUL-splitting
    label_raw: bytes = b""            # the labelLen bytes, before decoding
    bbox_a: tuple = ()                # boundsA exactly as stored (pre-sort)
    bbox_b: tuple = ()                # boundsB exactly as stored (pre-sort)
    phy5_raw: bytes = b""             # PHY5 STEP1/STEP2 region, verbatim
    tail_raw: bytes = b""             # anything after the last parsed field

    # Ordinal of this mesh among the container's PHY chunks, or None for a
    # mesh that did not come from a file.  This is load-bearing: the i-th PHY
    # chunk is bound to the i-th MOTI chunk POSITIONALLY (see docs/modding.md
    # section 11), so a writer that adds or removes meshes has to know which
    # original slot each surviving mesh came from.
    source_index: Optional[int] = None

    @property
    def vertex_count(self) -> int:
        return self.vertex_count_a + self.vertex_count_b

    @property
    def face_count(self) -> int:
        return self.face_count_a + self.face_count_b

    @property
    def skinned(self) -> bool:
        return any(v.weight1 != 0.0 for v in self.vertices)

    @property
    def bones(self) -> list[int]:
        """The bone palette, exactly as `Phy_Load` builds it (RVA 0x5A1A0):
        every vertex contributes bone0; it contributes bone1 only when
        weight1 != 0.  The result is a sorted unique set."""
        s = set()
        for v in self.vertices:
            s.add(v.bone0)
            if v.weight1 != 0.0:
                s.add(v.bone1)
        return sorted(s)


class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def read(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise ValueError(f"PHY truncated: want {n} at {self.p}, "
                             f"have {len(self.d) - self.p}")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def peek(self, n: int) -> bytes:
        return self.d[self.p:self.p + n]

    def seek(self, delta: int):
        self.p += delta

    def u32(self) -> int:
        return struct.unpack_from("<I", self.read(4))[0]

    @property
    def left(self) -> int:
        return len(self.d) - self.p


def _decode_vertex(buf: bytes, has_normal: bool, has_uv1: bool,
                   gap: bytes = b"") -> Vertex:
    """Decode one 60-byte in-memory vertex image (zero-padded if the variant
    supplies fewer bytes)."""
    px, py, pz, u0, v0 = struct.unpack_from("<5f", buf, 0x00)
    unk4, bone0, bone1 = struct.unpack_from("<3I", buf, 0x14)
    w0, w1 = struct.unpack_from("<2f", buf, 0x20)
    nx = ny = nz = 0.0
    u1 = v1 = 0.0
    if has_normal:
        nx, ny, nz = struct.unpack_from("<3f", buf, 0x28)
    if has_uv1:
        u1, v1 = struct.unpack_from("<2f", buf, 0x34)
    return Vertex(px, py, pz, u0, v0, unk4, bone0, bone1, w0, w1,
                  nx, ny, nz, u1, v1, gap)


def parse_phy(tag: bytes, body: bytes, *, apply_matrix: bool = False) -> PhyMesh:
    """Parse a PHY chunk body.  `tag` is the 4-byte chunk tag.

    `apply_matrix=True` reproduces what the engine does at RVA 0x5A735 --
    it transforms every position by the chunk's 4x4 matrix.  Left off by
    default so an exporter can decide (and so round-tripping stays exact).
    """
    if isinstance(tag, str):
        tag = tag.encode()
    if tag not in VARIANTS:
        raise ValueError(f"not a known PHY variant: {tag!r}")
    var = VARIANTS[tag]
    m = PhyMesh(tag=tag)
    r = _Reader(body)

    # ---- name (RVA 0x59D5C / 0x59D7A) ----
    name_len = r.u32()
    m.name_raw = r.read(name_len)
    m.name = m.name_raw.split(b"\x00")[0].decode("latin-1", "replace")

    # ---- three u32s: an ignored field, then two vertex counts ----
    m.unknown0 = r.u32()                       # RVA 0x59DEC -- read, never used
    m.vertex_count_a = r.u32()                 # RVA 0x59E08
    m.vertex_count_b = r.u32()                 # RVA 0x59E1C
    n_verts = m.vertex_count_a + m.vertex_count_b

    # ---- vertices ----
    has_normal = var["has_normal"]
    has_uv1 = var["step"]                      # only PHY5 carries a 2nd UV set
    tail = 0x28 if has_normal else 0x1C
    for _ in range(n_verts):
        img = bytearray(VERTEX_STRIDE)
        gap = b""
        if var["step"]:
            img[0:VERTEX_STRIDE] = r.read(VERTEX_STRIDE)
        elif var["legacy_gap"]:
            img[0x00:0x0C] = r.read(0x0C)
            gap = r.read(LEGACY_GAP)          # Seek'd past by the engine
            img[0x0C:0x0C + tail] = r.read(tail)
        else:
            n = 0x0C + tail
            img[0:n] = r.read(n)
        m.vertices.append(_decode_vertex(bytes(img), has_normal, has_uv1, gap))

    # ---- faces (RVA 0x59FEE / 0x5A002 / 0x5A044) ----
    m.face_count_a = r.u32()
    m.face_count_b = r.u32()
    n_faces = m.face_count_a + m.face_count_b
    idx = struct.unpack_from(f"<{n_faces * 3}H", r.read(n_faces * 6))
    m.faces = [tuple(idx[i * 3:i * 3 + 3]) for i in range(n_faces)]

    # ---- optional length-prefixed label (RVA 0x5A05D..0x5A0BF) ----
    label_len = r.u32()
    if label_len:
        m.label_raw = r.read(label_len)
        m.label = m.label_raw.decode("latin-1", "replace")
        m.is_c3exp_color = (label_len == 11 and m.label_raw == b"C3EXP_COLOR")

    # ---- bounding box, then the 4x4 matrix (RVA 0x5A0D3..0x5A146) ----
    a = struct.unpack("<3f", r.read(12))
    b = struct.unpack("<3f", r.read(12))
    m.bbox_a, m.bbox_b = a, b
    m.bbox_min = tuple(min(x, y) for x, y in zip(a, b))
    m.bbox_max = tuple(max(x, y) for x, y in zip(a, b))
    m.matrix = struct.unpack("<16f", r.read(64))

    # ---- frame count + the three C3Key channels ----
    m.frame_count = r.u32()                      # RVA 0x5B0DB -> C3Phy+0x190
    for slot in ("alphas", "draws", "change_texs"):
        cnt = r.u32()
        setattr(m.keys, slot, [r.read(16) for _ in range(cnt)])

    # ---- "STEP" -> TWO f32 (RVA 0x5B1FB) ----
    #
    # This said "two u32" until 2026-09-16 and read them as u32, and
    # `c3write` wrote them back as u32, so the round trip was byte-exact only
    # because the two errors cancelled.
    #
    # MEASURED over the whole of RSDB section 1 on 7878, through this parser
    # rather than a byte scan (`scratchpad/m8_step_values.py`): 156,815 chunks
    # carry a STEP, 79,367 are non-zero, and EVERY non-zero component lands in
    # 1e-5..1e0 -- no NaN, no Inf, no tail. As integers they would be
    # arbitrary dwords with no reason to cluster in a band four decades wide.
    # The f32 unpack/repack is byte-exact on all 434 distinct dwords in the
    # corpus, which is what makes this retype safe for `c3write`.
    #
    # Four modules move together or the round trip breaks: this reader,
    # `c3write` (writes `_f` now), `editbundle` (shows a human -0.017 instead
    # of 3163243414) and `effects.EffectPart.uv_step`, whose reinterpretation
    # is gone because the value arrives correct.
    if r.peek(4) == b"STEP":
        r.read(4)
        m.step = struct.unpack("<2f", r.read(8))

    # ---- "2SID" then optional billboard tag (RVA 0x5B274) ----
    nxt = r.peek(4)
    if nxt == b"2SID":
        r.read(4)
        m.two_sided = True
        nxt = r.peek(4)
    for i, bb in enumerate((b"BILB", b"BIB2", b"BIB3", b"BIB4"), start=1):
        if nxt == bb:
            r.read(4)
            m.billboard = i
            break

    # ---- PHY5 only: "STEP1"/"STEP2" 5-byte tags (RVA 0x5B32B..0x5B505) ----
    if var["step"]:
        _phy5_start = r.p
        five = r.peek(5)
        if five == b"STEP2":
            r.read(5)
            r.read(8)
            r.u32()
            cnt = r.u32()
            if cnt > 0:
                r.read(16 * cnt)                 # engine reads then frees this
        elif five == b"STEP1":
            r.read(5)
            f0, f1 = struct.unpack("<2f", r.read(8))
            m.step1 = (f0 / 33.0, f1 / 33.0)     # divisor 33.0 at RVA 0x5B44C
            if r.peek(5) == b"STEP2":
                r.read(5)
                g0, g1 = struct.unpack("<2f", r.read(8))
                m.step2 = (g0 / 33.0, g1 / 33.0)
        m.phy5_raw = body[_phy5_start:r.p]

    m.bytes_consumed = r.p
    m.trailing = r.left
    m.tail_raw = body[r.p:]

    if apply_matrix:
        apply_matrix_to(m)
    return m


def apply_matrix_to(m: PhyMesh) -> None:
    """Transform positions by the chunk matrix, as the engine does before
    filling the vertex buffer (RVA 0x5A735).  Row-vector convention:
    v' = v * M, using rows 0..2 for the basis and row 3 as translation."""
    a = m.matrix
    if len(a) != 16:
        return
    r0, r1, r2, r3 = a[0:3], a[4:7], a[8:11], a[12:15]
    for v in m.vertices:
        x, y, z = v.px, v.py, v.pz
        v.px = x * r0[0] + y * r1[0] + z * r2[0] + r3[0]
        v.py = x * r0[1] + y * r1[1] + z * r2[1] + r3[1]
        v.pz = x * r0[2] + y * r1[2] + z * r2[2] + r3[2]


def apply_matrix_copy(m: PhyMesh) -> PhyMesh:
    """`apply_matrix_to` on a COPY, without deep-copying the whole mesh.

    Every caller of `apply_matrix_to` in this tree wants the transformed
    positions and must not clobber the mesh it was handed, so the idiom was
    uniformly::

        q = copy.deepcopy(c.phy)
        c3phy.apply_matrix_to(q)

    MEASURED (`v_body` of `c3/mesh/002135260.c3`, 672 vertices / 916 faces,
    median of 20): **deepcopy 4.200 ms, this 0.512 ms** -- 8.2x, and the
    output is bit-identical (positions compared exactly, every other vertex
    field compared exactly, `faces` compared equal). `copy.deepcopy` was
    **67% of the whole /api/figure body path** in cProfile: 417,650 recursive
    calls for 30 top-level copies, because a `PhyMesh` is a graph of ~700
    `Vertex` dataclasses plus ~900 face tuples plus the `C3Key` channels, and
    `deepcopy` walks and memoises every node of it.

    What this does instead: shallow-copy the container, then rebuild
    `vertices` as fresh `Vertex` objects carrying the transformed position and
    the *same* values for every other field. That is sound because a
    `Vertex`'s other fields are all immutable scalars or `bytes`, so sharing
    them cannot leak a mutation. `faces` is copied as a new list (cheap: it is
    a list of tuples) so a caller that reorders it cannot reach back into the
    original. `keys`, `matrix` and the verbatim `*_raw` captures stay shared:
    they are tuples/bytes/dataclass-of-lists that nothing on this path writes.

    **Only use this where the copy is read-only afterwards, or where the
    caller mutates nothing but vertex fields.** If you need to mutate
    `q.keys` or `q.tail_raw`, deepcopy is still the right tool.

    Row-vector convention, identical to `apply_matrix_to`: `v' = v * M`.
    A mesh with no 16-float matrix is returned as an untransformed copy, which
    is what `apply_matrix_to` does for the same input.
    """
    q = _copy.copy(m)
    q.faces = list(m.faces)
    a = m.matrix
    if len(a) != 16:
        q.vertices = list(m.vertices)
        return q
    r0, r1, r2, r3 = a[0:3], a[4:7], a[8:11], a[12:15]
    r00, r01, r02 = r0[0], r0[1], r0[2]
    r10, r11, r12 = r1[0], r1[1], r1[2]
    r20, r21, r22 = r2[0], r2[1], r2[2]
    r30, r31, r32 = r3[0], r3[1], r3[2]
    V = Vertex
    q.vertices = [
        V(v.px * r00 + v.py * r10 + v.pz * r20 + r30,
          v.px * r01 + v.py * r11 + v.pz * r21 + r31,
          v.px * r02 + v.py * r12 + v.pz * r22 + r32,
          v.u0, v.v0, v.unknown4, v.bone0, v.bone1, v.weight0, v.weight1,
          v.nx, v.ny, v.nz, v.u1, v.v1, v.gap)
        for v in m.vertices
    ]
    return q


def generate_normals(m: PhyMesh) -> None:
    """Reproduce the engine's normal generation for "PHY " / "PHY4"
    (RVA 0x5A8C0): accumulate the un-normalised cross product of each face's
    two edges onto its three vertices, then normalise."""
    import math
    acc = [[0.0, 0.0, 0.0] for _ in m.vertices]
    for i0, i1, i2 in m.faces:
        if max(i0, i1, i2) >= len(m.vertices):
            continue
        p0, p1, p2 = (m.vertices[i].position for i in (i0, i1, i2))
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
        nx = e1[1] * e2[2] - e1[2] * e2[1]
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        nz = e1[0] * e2[1] - e1[1] * e2[0]
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln > 0:
            nx, ny, nz = nx / ln, ny / ln, nz / ln
        for i in (i0, i1, i2):
            acc[i][0] += nx
            acc[i][1] += ny
            acc[i][2] += nz
    for v, (nx, ny, nz) in zip(m.vertices, acc):
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln > 0:
            v.nx, v.ny, v.nz = nx / ln, ny / ln, nz / ln


# --------------------------------------------------------------------------
# convenience: pull every PHY chunk out of a .c3 file
# --------------------------------------------------------------------------

C3_MAGIC = b"MAXFILE C3 00001"


def iter_chunks(data: bytes):
    """Yield (tag, body) for a MAXFILE C3 container."""
    if not data.startswith(C3_MAGIC):
        raise ValueError("not a MAXFILE C3 container")
    p = len(C3_MAGIC)
    while p + 8 <= len(data):
        tag = data[p:p + 4]
        (ln,) = struct.unpack_from("<I", data, p + 4)
        body = data[p + 8:p + 8 + ln]
        if len(body) != ln:
            raise ValueError(f"chunk {tag!r} truncated at {p}")
        yield tag, body
        p += 8 + ln


def meshes_from_c3(data: bytes, **kw) -> list[PhyMesh]:
    out = []
    for tag, body in iter_chunks(data):
        if tag in VARIANTS:
            m = parse_phy(tag, body, **kw)
            m.source_index = len(out)      # its ordinal among the PHY chunks
            out.append(m)
    return out


# --------------------------------------------------------------------------
# shipped placeholders
# --------------------------------------------------------------------------

#: 3ds Max's default names for its primitive objects. An artist who creates a
#: box and never renames it ships `Box01`. TQ did: NPC 2202 "SeeFlower" on 7878
#: resolves to `c3/npc/997/1.c3`, one submesh named `Box01`, 14 verts, extents
#: 30x30x30 -- which is why the owner reported "a texture applied to a square".
#: The renderer was faithful; the ASSET is a placeholder.
_MAX_PRIMITIVE = re.compile(
    r"^(Box|Sphere|GeoSphere|Cylinder|Tube|Torus|Pyramid|Plane|Cone|Teapot"
    r"|Prism|Capsule|ChamferBox|Spindle)\d{2,}$")

#: A placeholder is a primitive nobody bothered to build on. Real content that
#: happens to be simple is not a placeholder, so the name is required -- the
#: vertex bound alone would promote every low-poly prop in the game.
PLACEHOLDER_MAX_VERTS = 64


def placeholder_reason(meshes: "list[PhyMesh]") -> "Optional[str]":
    r"""Why these meshes look like a SHIPPED PLACEHOLDER, or ``None``.

    **The distinction this exists to protect.** An appearance can be in three
    states and collapsing the last two is the error this is named for:

    * **resolved** -- geometry found, real content;
    * **resolved to a shipped placeholder** -- geometry found, and it is a
      default primitive TQ shipped without replacing. Drawing it is CORRECT;
      presenting it as content is what misleads;
    * **unresolved** -- no geometry at all. We have nothing.

    A viewer that shows a box for the middle case and a box for the last case
    has told the user the same thing about two different facts.

    The test is a CONJUNCTION so it cannot promote real content: a single
    submesh, a 3ds Max default primitive name, and a vertex count under
    ``PLACEHOLDER_MAX_VERTS``. The name is load-bearing -- dropping it would
    claim every low-poly prop in the game. Returns a reason string (for a
    badge or a log line) rather than a bool, because "why" is what the owner
    needs and a bool is what gets collapsed.
    """
    if len(meshes) != 1:
        return None
    m = meshes[0]
    name = (m.name or "").strip()
    if not _MAX_PRIMITIVE.match(name):
        return None
    verts = len(m.vertices) or m.vertex_count_a
    if verts > PLACEHOLDER_MAX_VERTS:
        return None
    ext = tuple(round(m.bbox_max[i] - m.bbox_min[i], 3) for i in range(3))
    return (f"shipped placeholder: 3ds Max default primitive {name!r}, "
            f"{verts} verts, extents {ext[0]}x{ext[1]}x{ext[2]}")


# --------------------------------------------------------------------------
# coordinate conversion for DCC tools
# --------------------------------------------------------------------------

def to_blender(p):
    r"""C3 -> Blender coordinates.

    C3 stores a left-handed frame whose Z axis points DOWN (a `v_body` mesh
    occupies z in roughly [-height, 0] once the chunk matrix is applied), and
    its triangles are wound so that the engine's own generated face normal,
    `cross(p2-p1, p1-p0)`, points outward -- measured outward on 482 of 496
    real character meshes.

    Negating Z does two jobs at once: it stands the model up in Blender's
    Z-up frame, and because a single-axis mirror flips handedness it also
    corrects the winding.  So use this AND KEEP THE ORIGINAL INDEX ORDER.

    (Verified empirically: with `(x, y, -z)` and unchanged winding, Blender's
    own front-face test `cross(p1-p0, p2-p0)` agrees with "points away from
    the centroid" on the same 2:1 majority of faces as the engine's rule.
    The equivalent alternative is to keep coordinates and reverse each
    triangle instead -- do exactly one of the two, never both.)
    """
    return (p[0], p[1], -p[2])


def export_obj(mesh: "PhyMesh", path, *, apply_matrix: bool = True,
               blender_axes: bool = True) -> None:
    """Write a Wavefront OBJ.  Deliberately minimal -- it exists to prove the
    layout end to end, and as a reference for a real exporter."""
    from pathlib import Path as _P
    m = mesh
    if apply_matrix:
        import copy
        m = copy.deepcopy(mesh)
        apply_matrix_to(m)
    if not any((v.nx or v.ny or v.nz) for v in m.vertices):
        generate_normals(m)
    conv = to_blender if blender_axes else (lambda p: p)
    lines = [f"# exported from C3 {m.tag.decode(errors='replace')} chunk "
             f"{m.name!r} by core/c3phy.py",
             f"# {m.vertex_count} vertices, {m.face_count} triangles, "
             f"{len(m.bones)} bones",
             f"o {m.name or 'phy'}"]
    for v in m.vertices:
        x, y, z = conv(v.position)
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for v in m.vertices:
        x, y, z = conv(v.normal)
        lines.append(f"vn {x:.6f} {y:.6f} {z:.6f}")
    for v in m.vertices:
        # OBJ's V axis runs opposite to D3D's
        lines.append(f"vt {v.u0:.6f} {1.0 - v.v0:.6f}")
    for a, b, c in m.faces:
        lines.append(f"f {a+1}/{a+1}/{a+1} {b+1}/{b+1}/{b+1} {c+1}/{c+1}/{c+1}")
    _P(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    argv = sys.argv[1:]
    obj_dir = None
    if "--obj" in argv:
        i = argv.index("--obj")
        obj_dir = Path(argv[i + 1])
        obj_dir.mkdir(parents=True, exist_ok=True)
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    for a in args:
        d = Path(a).read_bytes()
        for i, mesh in enumerate(meshes_from_c3(d)):
            if obj_dir:
                out = obj_dir / f"{Path(a).stem}_{i}_{mesh.name or 'phy'}.obj"
                export_obj(mesh, out)
                print(f"  -> {out}")
            bb = ", ".join(f"{x:.2f}" for x in mesh.bbox_min + mesh.bbox_max)
            print(f"{Path(a).name}  {mesh.tag.decode()}  {mesh.name!r}\n"
                  f"    verts={mesh.vertex_count} ({mesh.vertex_count_a}+"
                  f"{mesh.vertex_count_b})  faces={mesh.face_count}"
                  f"  bones={len(mesh.bones)}  frames={mesh.frame_count}\n"
                  f"    bbox=({bb})  trailing={mesh.trailing}")
