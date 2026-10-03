#!/usr/bin/env python3
r"""c3anim.py -- ``MOTI`` <-> armature-action conversion, with no Blender in it.

`tools/effects.py` decodes a ``MOTI`` chunk into `Motion` / `MotionKey` and
`serialize_moti` writes it back byte-exactly.  This module is the layer between
that and an animation tool: it turns a `Motion` into the per-bone
**location / rotation_quaternion / scale** triples a Blender action is made of,
and turns edited triples back into a `Motion`.

**Everything here is pure stdlib.**  `blender/io_scene_c3/c3_anim.py` is the
`bpy` glue and holds no format or matrix knowledge; this file holds all of it,
so the part where the bugs live is the part that runs in CI.  That split is the
same one `c3phy`/`c3write` already have against `c3_import`/`c3_export`, and it
exists for the same reason: Blender cannot be launched on this rig.

--------------------------------------------------------------------------
The two constraints that decide the whole design
--------------------------------------------------------------------------

1. **The encoding is a property of the file, not of us.**  MOTI has four
   (``KKEY`` / ``ZKEY`` / ``XKEY`` / ``RAW``) and installs are dominated by
   different ones -- MEASURED per client: 7632 is 89.7% XKEY, 4274 is 96.4%
   RAW, 6090 is 53.5% ZKEY, CCO is 57.8% RAW.  An exporter that always wrote
   one of them would silently re-encode an entire install.  `build_motion`
   therefore writes back **the encoding it was handed** and has no option to
   choose another.

2. **The matrix view is derived, and for ZKEY it is lossy.**  `quat_to_matrix`
   normalises, and ``q`` and ``-q`` give the same matrix.  MEASURED: 686 of
   1,393 ZKEY chunks (49.2%) carry a bone key with ``w < 0`` or ``|q| != 1``
   that no matrix-based writer can recover.  `MotionKey.source` carries the
   on-disk floats for exactly this reason and `serialize_moti` refuses a
   ZKEY/XKEY key that has none.

   So the rule this module runs on is the addon's existing one --
   **"prefer the source when the view is unchanged"**.  `moti_to_view` hands
   out the editable triples; `build_motion` is given both the triples the tool
   holds *now* and the ones the importer *wrote*, and for every (key, bone)
   whose triple is bit-identical it re-emits the original disk floats instead
   of re-deriving them.  A key the user actually moved is re-baked, and will
   not be byte-identical -- that is correct, and it is the only case where it
   is allowed to happen.

--------------------------------------------------------------------------
Where the bone's rest pose comes into it
--------------------------------------------------------------------------

A MOTI matrix is a **skinning** matrix: `Motion_GetMatrix` hands it straight to
the vertex transform, so it maps rest space to posed space.  Blender's armature
deform for a parentless bone is ``pose.matrix @ bone.matrix_local.inverted()``,
i.e. ``L . B . L^-1`` where ``L`` is the bone's rest matrix and ``B`` is
`pose_bone.matrix_basis`.  Setting

    B = L^-1 . M_blender . L

therefore makes the deformation exactly ``M_blender`` **whatever L is**, which
is what lets the addon keep its existing placeholder bone ladder (a rest pose
of coincident bones at the origin would be correct too, and unusable).  The
conjugation is done here, in `moti_to_view` / `view_to_c3_matrix`, and the rest
matrices are passed in rather than assumed.

Because ``L`` is an input to the reconstruction, a change to it is an edit:
`build_motion` refuses to reuse any source floats when the rest matrices it is
given differ from the ones the importer recorded.

    py -3 tools/c3anim.py --self-test        # the matrix identities, no corpus
"""
from __future__ import annotations

import math
import sys

# The bootstrap block below is REPLACED WHOLE by `tools/build_addon.py` when
# this module is vendored into the addon -- `from pathlib import Path` included,
# because inside the package nothing else here uses it and a vendored copy with
# a dead import is a vendored copy someone will "tidy" out of sync.
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from effects import (Motion, MotionKey, MotiWriteError, parse_moti,  # noqa: E402
                     quat_to_matrix, serialize_moti)

__all__ = [
    "AnimEditError", "FLOATS_PER_BONE", "ENCODINGS",
    "c3_matrix_to_blender", "blender_matrix_to_c3",
    "mat_mul4", "mat_inv4", "mat_ident4",
    "decompose", "compose", "matrix_to_quat_c3",
    "moti_to_view", "view_to_c3_matrix", "required_frames", "build_motion",
    "ladder_rest_locals", "motion_to_bytes", "flatten_rest", "rest_inverses",
    # re-exported so the `bpy` glue has exactly one vendored import and no
    # opinion of its own about where the format code lives
    "Motion", "MotionKey", "MotiWriteError", "parse_moti", "serialize_moti",
]

#: loc(3) + rotation_quaternion(4, Blender's w,x,y,z order) + scale(3).
#: One flat run of these per (key, bone), which is what a Blender IDProperty
#: float array can hold and compare without any structure of its own.
FLOATS_PER_BONE = 10

ENCODINGS = ("KKEY", "ZKEY", "XKEY", "RAW")

#: docs/modding.md 9.7 -- C3 is left-handed with +Z down.  Negating Z stands
#: the model up in Blender's frame.  Identical to `c3_common.to_blender`'s
#: sign vector, and `tests/test_moti_action.py` asserts the two agree rather
#: than trusting that they still do.
AXIS = (1.0, 1.0, -1.0)


class AnimEditError(ValueError):
    """An edited action that cannot be expressed in its own MOTI encoding."""


# --------------------------------------------------------------------------
# 4x4 helpers.  Matrices here are Blender-shaped: a tuple of 4 rows of 4, in
# COLUMN-vector convention (translation in column 3), which is what
# `mathutils.Matrix` indexing gives.  The C3 form is 16 flat floats in
# ROW-vector convention (translation in row 3) -- see `c3_matrix_to_blender`.
# --------------------------------------------------------------------------

def mat_ident4():
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat_mul4(a, b):
    """Row-of-rows 4x4 product, UNROLLED.

    The obvious `sum(a[r][k] * b[k][c] for k in range(4))` was 70% of
    `tests/test_moti_action.py`'s runtime -- two of these per bone key over
    1.2 million bone keys -- and took the full-corpus gate to 20 minutes
    against its sibling's 2 seconds.  The accumulation order is unchanged
    (`sum` over a generator adds left to right and so does `p0+p1+p2+p3`), so
    this is a speed change and not a numerical one.
    """
    a0, a1, a2, a3 = a
    (b00, b01, b02, b03) = b[0]
    (b10, b11, b12, b13) = b[1]
    (b20, b21, b22, b23) = b[2]
    (b30, b31, b32, b33) = b[3]
    out = []
    for r0, r1, r2, r3 in (a0, a1, a2, a3):
        out.append((r0 * b00 + r1 * b10 + r2 * b20 + r3 * b30,
                    r0 * b01 + r1 * b11 + r2 * b21 + r3 * b31,
                    r0 * b02 + r1 * b12 + r2 * b22 + r3 * b32,
                    r0 * b03 + r1 * b13 + r2 * b23 + r3 * b33))
    return tuple(out)


def mat_inv4(m):
    """Gauss-Jordan with partial pivoting.  Raises on a singular matrix.

    General rather than the affine shortcut on purpose: the caller feeds it a
    bone rest matrix straight out of Blender, and an addon that silently
    produced garbage for a scaled or mirrored bone would be worse than one that
    raised.
    """
    a = [[float(m[r][c]) for c in range(4)] + [1.0 if r == c else 0.0
                                               for c in range(4)]
         for r in range(4)]
    for col in range(4):
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-20:
            raise AnimEditError(
                f"bone rest matrix is singular (column {col} has no pivot); "
                f"a zero-length or degenerate bone cannot carry a motion track")
        a[col], a[piv] = a[piv], a[col]
        d = a[col][col]
        a[col] = [v / d for v in a[col]]
        for r in range(4):
            if r == col:
                continue
            f = a[r][col]
            if f:
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return tuple(tuple(a[r][4:]) for r in range(4))


def _det3(r):
    return (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
            - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
            + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))


# --------------------------------------------------------------------------
# C3 <-> Blender matrix conversion
#
# DUPLICATED, DELIBERATELY, from `blender/io_scene_c3/c3_common.py`.  That file
# is addon glue and is not vendored from here; this file is vendored INTO the
# addon.  Rather than have one import the other (which would make the addon's
# thinnest module depend on the vendor tree, or this module unimportable from
# `tools/`), `tests/test_moti_action.py` loads both and asserts they agree
# element-by-element on a spread of matrices.  A copy with a test against it is
# honest; a copy without one is the drift `test_vendor_sync.py` exists for.
# --------------------------------------------------------------------------

def c3_matrix_to_blender(m):
    """16 C3 floats -> a 4x4 nested tuple in Blender's column-vector form."""
    if not m or len(m) != 16:
        m = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    b = [[0.0] * 4 for _ in range(4)]
    s = AXIS
    for r in range(3):
        for c in range(3):
            b[r][c] = s[r] * m[c * 4 + r] * s[c]
    for r in range(3):
        b[r][3] = s[r] * m[12 + r]
    b[3] = [0.0, 0.0, 0.0, 1.0]
    return tuple(tuple(row) for row in b)


def blender_matrix_to_c3(b):
    """Inverse of `c3_matrix_to_blender`.  `b` is a 4x4 row-of-rows."""
    s = AXIS
    m = [0.0] * 16
    for r in range(3):
        for c in range(3):
            m[c * 4 + r] = s[r] * b[r][c] * s[c]
    for r in range(3):
        m[12 + r] = s[r] * b[r][3]
    m[3] = m[7] = m[11] = 0.0
    m[15] = 1.0
    return tuple(m)


# --------------------------------------------------------------------------
# quaternions
#
# `effects.quat_to_matrix` is D3DXMatrixRotationQuaternion in ROW-vector form,
# taking (x, y, z, w) -- it is what `graphic.dll!Motion_Load` tail-calls, so it
# is the definition, not a convention we picked.  Blender's quaternions are
# (w, x, y, z) and its matrices are column-vector, i.e. the transpose.  Both
# conversions below go through that one function so there is a single place
# where the handedness of a rotation is decided.
# --------------------------------------------------------------------------

def _quat_from_row_matrix(m):
    """(x, y, z, w) from the 3x3 of a 16-float ROW-vector matrix.

    Exact inverse of `effects.quat_to_matrix` for a proper rotation.  The rows
    are normalised first: a caller may hand in a matrix that carries scale, and
    the four-branch extraction below is only meaningful for an orthonormal
    basis.  Whether the scale it dropped MATTERED is the caller's question --
    `build_motion` asks it and warns.
    """
    rows = []
    for r in range(3):
        v = (m[r * 4], m[r * 4 + 1], m[r * 4 + 2])
        n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        rows.append(tuple(c / n for c in v) if n > 1e-20 else
                    tuple(1.0 if i == r else 0.0 for i in range(3)))
    m0, m1, m2 = rows[0]
    m4, m5, m6 = rows[1]
    m8, m9, m10 = rows[2]
    tr = m0 + m5 + m10
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return ((m6 - m9) / s, (m8 - m2) / s, (m1 - m4) / s, 0.25 * s)
    if m0 > m5 and m0 > m10:
        s = math.sqrt(1.0 + m0 - m5 - m10) * 2.0
        return (0.25 * s, (m1 + m4) / s, (m2 + m8) / s, (m6 - m9) / s)
    if m5 > m10:
        s = math.sqrt(1.0 + m5 - m0 - m10) * 2.0
        return ((m1 + m4) / s, 0.25 * s, (m6 + m9) / s, (m8 - m2) / s)
    s = math.sqrt(1.0 + m10 - m0 - m5) * 2.0
    return ((m2 + m8) / s, (m6 + m9) / s, 0.25 * s, (m1 - m4) / s)


def matrix_to_quat_c3(m):
    """16 C3 floats -> (qx, qy, qz, qw), the ZKEY on-disk quaternion order.

    NOT lossless in the direction that matters: `quat_to_matrix(-q)` and
    `quat_to_matrix(q)` are the same matrix, so this can only ever return one
    of the two, and a non-unit quaternion comes back unit.  That is the whole
    reason `MotionKey.source` exists -- this function is used ONLY to re-bake a
    bone key the user actually edited, never to reconstruct one they did not.
    """
    return _quat_from_row_matrix(m)


def _quat_to_mat3_bl(q):
    """Blender (w, x, y, z) -> 3x3 column-vector rows (transpose of the C3 form)."""
    w, x, y, z = q
    m = quat_to_matrix(x, y, z, w)
    return tuple(tuple(m[c * 4 + r] for c in range(3)) for r in range(3))


def _mat3_to_quat_bl(r):
    """3x3 column-vector rows -> Blender (w, x, y, z)."""
    flat = (r[0][0], r[1][0], r[2][0], 0.0,
            r[0][1], r[1][1], r[2][1], 0.0,
            r[0][2], r[1][2], r[2][2], 0.0,
            0.0, 0.0, 0.0, 1.0)
    x, y, z, w = _quat_from_row_matrix(flat)
    return (w, x, y, z)


# --------------------------------------------------------------------------
# decompose / compose -- `mathutils.Matrix.decompose`, reproduced
# --------------------------------------------------------------------------

def decompose(b):
    """4x4 rows -> (location3, quaternion4 as w,x,y,z, scale3).

    Follows Blender's `mat3_to_rot_size`: the scale is the length of each basis
    COLUMN, and when the basis is left-handed both the rotation and the scale
    are negated rather than the sign being parked on one axis.  Shear is not
    representable and is dropped -- which is exactly why an unedited key is
    re-emitted from its source floats instead of from this.
    """
    loc = (b[0][3], b[1][3], b[2][3])
    size = []
    rot = [[0.0] * 3 for _ in range(3)]
    for c in range(3):
        col = (b[0][c], b[1][c], b[2][c])
        n = math.sqrt(col[0] ** 2 + col[1] ** 2 + col[2] ** 2)
        size.append(n)
        for r in range(3):
            rot[r][c] = b[r][c] / n if n > 1e-20 else (1.0 if r == c else 0.0)
    if _det3(rot) < 0.0:
        rot = [[-v for v in row] for row in rot]
        size = [-s for s in size]
    return loc, _mat3_to_quat_bl(rot), tuple(size)


def compose(loc, quat, scale):
    """The inverse of `decompose` (up to the shear it cannot carry)."""
    r = _quat_to_mat3_bl(quat)
    b = [[0.0] * 4 for _ in range(4)]
    for row in range(3):
        for col in range(3):
            b[row][col] = r[row][col] * scale[col]
        b[row][3] = loc[row]
    b[3] = [0.0, 0.0, 0.0, 1.0]
    return tuple(tuple(x) for x in b)


# --------------------------------------------------------------------------
# rest poses
# --------------------------------------------------------------------------

def ladder_rest_locals(bone_count: int, spacing: float = 4.0):
    """Rest matrices in the SHAPE of `c3_import.build_armature`'s ladder.

    Bone *i* has its head at ``(0, 0, i*spacing)`` and points along world +Z,
    so ``L`` carries both a rotation and a translation and the conjugation in
    `moti_to_view` is not a no-op.  That is the whole reason this exists: with
    an identity rest pose every test here would pass with the conjugation
    deleted.

    It is NOT a claim about Blender's roll convention -- the addon passes the
    real `bone.matrix_local` at runtime and never calls this.  Only the tests
    do, and what they need from it is that it is awkward, not that it matches.
    """
    out = []
    for i in range(bone_count):
        # +90 degrees about X: the bone's local +Y maps to world +Z.
        out.append(((1.0, 0.0, 0.0, 0.0),
                    (0.0, 0.0, -1.0, 0.0),
                    (0.0, 1.0, 0.0, i * spacing),
                    (0.0, 0.0, 0.0, 1.0)))
    return out


# --------------------------------------------------------------------------
# MOTI -> editable view
# --------------------------------------------------------------------------

def rest_inverses(rest_locals):
    """``[L^-1]``, deduplicated by identical L.

    A bone ladder repeats the same rest matrix shape for every bone, and the
    conversion needs the inverse of each one twice (import and export).
    Inverting per bone was the second-largest cost in the gate after
    `mat_mul4`; matching on the matrix itself is exact and cannot pair two
    bones that are actually different.
    """
    seen = {}
    out = []
    for L in rest_locals:
        key = tuple(tuple(row) for row in L)
        got = seen.get(key)
        if got is None:
            got = seen[key] = mat_inv4(L)
        out.append(got)
    return out


def root_first(parents, bone_count):
    """Bone indices ordered so every parent precedes its children.

    A LOCAL COPY of `bonetree.order_root_first` on purpose.  `c3anim` is the
    most heavily gated module here -- 16,600 MOTI chunks byte-exact -- and
    giving it a new cross-module import to reach ten lines of graph walk buys
    a vendoring dependency and a rewrite entry for no benefit.  `bonetree`
    owns the DERIVATION; this owns the ORDER it has to be applied in.

    The guard is not decoration: `parents` can be read back from a .blend a
    user has re-parented by hand, and a cycle there must terminate with a
    usable order rather than hang Blender.
    """
    out, seen = [], set()

    def visit(b, guard=0):
        if b in seen or guard > bone_count:
            return
        p = parents.get(b)
        if p is not None and 0 <= int(p) < bone_count:
            visit(int(p), guard + 1)
        if b not in seen:
            seen.add(b)
            out.append(b)

    for b in range(bone_count):
        visit(b)
    return out


def moti_to_view(motion: Motion, rest_locals, parents=None):
    """-> ``(frames, view)``.

    `frames` is one integer per `MotionKey`; `view` is a flat list of
    ``len(frames) * bone_count * FLOATS_PER_BONE`` floats -- the
    location/quaternion/scale a Blender pose bone would hold at that key.

    `parents` is ``{bone: parent_or_None}`` when the armature is a HIERARCHY.
    A MOTI matrix is ABSOLUTE -- the format stores no parent indices at all --
    but Blender composes a parented bone as ``P = P_parent . L_parent^-1 . L
    . B``, so the basis it needs is conjugated by the PARENT-RELATIVE matrix:

        flat        B = L^-1 . M . L
        parented    B = L^-1 . (M_parent^-1 . M) . L

    Both give an applied skin transform of exactly ``M``.

    HOW CLOSELY, MEASURED -- this said 6.2e-12 and "a re-baked key still writes
    identical bytes", and both were wrong.  Forced full re-bake
    (``src_view=None``) on two real 84-bone shape-3 tracks of the live CCO
    install, 2026-09-25, trees derived from their own frames (82 edges, 2 roots,
    depth 8 / 10), worst absolute element error:

        track                            flat+ident  flat+ladder  parented+ident
        c3/0003/One-Handed/Sword/403.c3    5.86e-07     1.57e-04       5.93e-05
        c3/0003/500/131.c3                 1.54e-06     2.12e-05       9.87e-06

    The flat path with an identity rest is the tight case; the ladder rest
    multiplies the error by its translation (``bone_spacing * index``) and the
    parented path accumulates down the chain.  A re-baked key does NOT come back
    byte-identical -- re-serialised, the tightest row above still differs in
    14,001 of 161,420 bytes.  Byte-exactness is the REUSE path's guarantee, not
    this one's: see constraint 2 in the module docstring and `build_motion`'s
    ``src_view`` reuse rule, which `test_moti_action.py` gates byte-for-byte
    over 16,841 chunks (PASS 2026-09-25).

    No test asserts these numbers.  `tests/test_moti_parented.py:135` bounds the
    parented re-bake at ``< 1e-6`` on a SYNTHETIC 6-bone motion, and
    `control_rebake_fidelity` never passes ``parents``, so the parented path has
    no corpus-level bound.  Per-encoding relative bounds that ARE asserted:
    `docs/blender_animation_2026-09-06.md` §4.

    ``parents=None`` takes the flat path unchanged, byte for byte.
    """
    bc = int(motion.bone_count)
    if len(rest_locals) < bc:
        raise AnimEditError(
            f"{len(rest_locals)} rest matrices supplied for a {bc}-bone "
            f"motion; every MOTI bone needs a bone in the armature")
    inv = rest_inverses(rest_locals[:bc])
    order = range(bc) if not parents else root_first(parents, bc)
    frames, view = [], []
    for k in motion.keys:
        frames.append(int(k.frame))
        absolute, basis_of = {}, {}
        for b in order:
            bl = c3_matrix_to_blender(k.matrices[b])
            absolute[b] = bl
            p = None if not parents else parents.get(b)
            if p is None or int(p) not in absolute:
                rel = bl
            else:
                rel = mat_mul4(mat_inv4(absolute[int(p)]), bl)
            basis_of[b] = mat_mul4(inv[b], mat_mul4(rel, rest_locals[b]))
        # The view is laid out in BONE order whatever order it was computed
        # in; every reader indexes it as (key, bone).
        for b in range(bc):
            loc, q, s = decompose(basis_of[b])
            view.extend(loc)
            view.extend(q)
            view.extend(s)
    return frames, view


def view_to_c3_matrix(vals, rest_local, rest_inv):
    """One bone's 10 view floats -> its 16 C3 matrix floats."""
    basis = compose(tuple(vals[0:3]), tuple(vals[3:7]), tuple(vals[7:10]))
    return blender_matrix_to_c3(mat_mul4(rest_local, mat_mul4(basis, rest_inv)))


# --------------------------------------------------------------------------
# editable view -> MOTI
# --------------------------------------------------------------------------

def required_frames(motion: Motion, tool_frames=()) -> list[int]:
    """Which frames the exporter has to sample, for this encoding.

    ``RAW`` has no frame numbers on disk at all -- every frame ``0 ..
    frameCount-1`` is a key, in that order, and `serialize_moti` refuses
    anything else.  So a RAW track ignores whatever keys the tool holds and is
    always resampled densely; a keyed encoding takes the union of its own key
    frames and the tool's, which is what lets a user add one.
    """
    if motion.encoding == "RAW":
        return list(range(int(motion.frame_count)))
    want = {int(k.frame) for k in motion.keys}
    for f in tool_frames:
        want.add(int(round(float(f))))
    return sorted(want)


def flatten_rest(rest_locals) -> list:
    """``[4x4, ...]`` -> one flat float list, 16 per bone.

    What the addon stores in an ID property, and the form `build_motion`
    compares in.  Nested input is flattened and an already-flat list is passed
    through, because the caller on the Blender side reads back an ID property
    (flat) and the caller in the tests holds matrices (nested) -- and a
    silently-wrong comparison here would disable the rest-pose control without
    failing anything.
    """
    if not rest_locals:
        return []
    first = rest_locals[0]
    if isinstance(first, (int, float)):
        return [float(v) for v in rest_locals]
    return [float(v) for L in rest_locals for row in L for v in row]


def _rest_changed(rest_locals, src_rest) -> bool:
    if src_rest is None:
        return True
    return flatten_rest(rest_locals) != flatten_rest(src_rest)


def _rebake(encoding, m16, warn):
    """(matrix16, source_tuple_or_None) for one re-derived bone key."""
    if encoding in ("KKEY", "RAW"):
        return m16, None
    if encoding == "XKEY":
        src = (m16[0], m16[1], m16[2],
               m16[4], m16[5], m16[6],
               m16[8], m16[9], m16[10],
               m16[12], m16[13], m16[14])
        return m16, src
    # ZKEY: quaternion + translation only.  A rotation is all it can hold, so
    # say so rather than writing a matrix that silently loses the scale.
    q = matrix_to_quat_c3(m16)
    back = quat_to_matrix(*q)
    for i in (0, 1, 2, 4, 5, 6, 8, 9, 10):
        if abs(back[i] - m16[i]) > 1e-4:
            warn["ZKEY stores rotation + translation only; scale/shear on an "
                 "edited bone was dropped"] = \
                warn.get("ZKEY stores rotation + translation only; scale/shear "
                         "on an edited bone was dropped", 0) + 1
            break
    m = list(back)
    m[12], m[13], m[14], m[15] = m16[12], m16[13], m16[14], 1.0
    return tuple(m), (q[0], q[1], q[2], q[3], m16[12], m16[13], m16[14])


def build_motion(src: Motion, rest_locals, frames, view, *,
                 src_frames=None, src_view=None, src_rest=None, warn=None,
                 parents=None):
    """-> ``(Motion, stats)``.  The encoding is always `src.encoding`.

    `frames`/`view` are what the tool holds now (`view` laid out exactly as
    `moti_to_view` produces it).  `src_frames`/`src_view`/`src_rest` are what
    the importer wrote and read back out of the tool.  A (key, bone) whose ten
    floats are bit-identical to the recorded ones -- and whose bone rest matrix
    is unchanged -- is re-emitted from `src`'s own disk floats; anything else is
    re-baked from the view.

    Pass ``src_view=None`` to force a full re-bake.  That is not an export
    mode, it is the control `tests/test_moti_action.py` uses to prove the reuse
    path is load-bearing rather than decorative.
    """
    warn = {} if warn is None else warn
    bc = int(src.bone_count)
    stats = {"reused": 0, "rebaked": 0, "new_keys": 0, "dropped_keys": 0,
             "bones": bc, "encoding": src.encoding}

    if len(rest_locals) < bc:
        raise AnimEditError(
            f"{len(rest_locals)} rest matrices supplied for a {bc}-bone motion")
    inv = rest_inverses(rest_locals[:bc])

    order = range(bc) if not parents else root_first(parents, bc)
    per_frame = len(frames) * bc * FLOATS_PER_BONE
    if len(view) != per_frame:
        raise AnimEditError(
            f"view has {len(view)} floats; {len(frames)} frames x {bc} bones "
            f"x {FLOATS_PER_BONE} needs {per_frame}")

    reuse_ok = src_view is not None and not _rest_changed(rest_locals, src_rest)
    if src_view is not None and not reuse_ok:
        warn["bone rest pose changed; every key was re-baked"] = 1
    src_at = {}
    if reuse_ok:
        sf = [int(f) for f in (src_frames or [])]
        want = len(sf) * bc * FLOATS_PER_BONE
        if len(src_view) != want:
            raise AnimEditError(
                f"recorded view has {len(src_view)} floats, but {len(sf)} "
                f"recorded frames x {bc} bones needs {want}")
        for j, f in enumerate(sf):
            src_at[f] = j
        stats["dropped_keys"] = sum(1 for f in sf if f not in set(frames))

    keys = []
    for i, f in enumerate(frames):
        f = int(f)
        j = src_at.get(f)
        if j is None and reuse_ok:
            stats["new_keys"] += 1
        mat_of, src_of = {}, {}
        # A REBAKED PARENT MAKES ITS CHILDREN STALE even when their own ten
        # floats are untouched.  A stored MOTI matrix is ABSOLUTE, so rotating
        # a parent moves the child in the world while its `matrix_basis` --
        # and therefore its view -- does not change at all.  Reusing the
        # child's recorded matrix there would write the pose it had BEFORE the
        # parent moved, silently, and the asset would no longer round-trip.
        # So dirtiness propagates down the tree, root-first.
        dirty, absolute = set(), {}
        for b in order:
            o = (i * bc + b) * FLOATS_PER_BONE
            vals = [float(x) for x in view[o:o + FLOATS_PER_BONE]]
            par = None if not parents else parents.get(b)
            par = None if par is None or int(par) not in absolute else int(par)

            unchanged = False
            if j is not None:
                p = (j * bc + b) * FLOATS_PER_BONE
                unchanged = vals == [float(x) for x
                                     in src_view[p:p + FLOATS_PER_BONE]]
            if unchanged and not (par is not None and par in dirty):
                k = src.keys[j]
                mat_of[b] = k.matrices[b]
                if k.source:
                    src_of[b] = k.source[b]
                absolute[b] = c3_matrix_to_blender(k.matrices[b])
                stats["reused"] += 1
                continue

            dirty.add(b)
            local = mat_mul4(rest_locals[b],
                             mat_mul4(compose(tuple(vals[0:3]),
                                              tuple(vals[3:7]),
                                              tuple(vals[7:10])), inv[b]))
            abs_b = local if par is None else mat_mul4(absolute[par], local)
            absolute[b] = abs_b
            mat, s = _rebake(src.encoding, blender_matrix_to_c3(abs_b), warn)
            mat_of[b] = mat
            if s is not None:
                src_of[b] = s
            stats["rebaked"] += 1

        mats = [mat_of[b] for b in range(bc)]
        srcs = [src_of[b] for b in range(bc) if b in src_of]
        keys.append(MotionKey(f, mats, srcs))

    out = Motion(bc, int(src.frame_count), src.encoding, keys,
                 int(src.extra_channels), 0, 0,
                 src.extra_payload, src.trailing)
    return out, stats


def motion_to_bytes(m: Motion) -> bytes:
    """`serialize_moti`, with the refusals restated in the caller's language."""
    try:
        return serialize_moti(m)
    except MotiWriteError as e:
        raise AnimEditError(str(e)) from e


# --------------------------------------------------------------------------

def _self_test() -> int:
    """The matrix identities, with no corpus and no Blender.

    `tests/test_moti_action.py` is the real gate; this is the two-second
    version so the module can be sanity-checked from a shell.
    """
    import random
    rng = random.Random(20260906)
    bad = 0

    # The MATRIX is what has to survive, not the four components: the
    # extraction below returns q or -q (whichever branch fires), which is the
    # exact ambiguity `MotionKey.source` exists to carry.  A test comparing
    # components would fail on 27% of random quaternions for a correct
    # implementation -- measured, and it is how this self-test was first wrong.
    n_bad = 0
    for _ in range(2000):
        q = [rng.uniform(-1, 1) for _ in range(4)]
        n = math.sqrt(sum(v * v for v in q)) or 1.0
        x, y, z, w = (v / n for v in q)
        m = quat_to_matrix(x, y, z, w)
        back = quat_to_matrix(*matrix_to_quat_c3(m))
        if max(abs(back[i] - m[i]) for i in range(16)) > 1e-5:
            n_bad += 1
    bad += n_bad
    print(f"quat -> matrix -> quat    : {2000 - n_bad}/2000 matrices "
          f"within 1e-5")

    worst = 0.0
    for _ in range(500):
        b = tuple(tuple(rng.uniform(-3, 3) if r < 3 else (1.0 if c == 3 else 0.0)
                        for c in range(4)) for r in range(4))
        try:
            i = mat_inv4(b)
        except AnimEditError:
            continue
        p = mat_mul4(b, i)
        worst = max(worst, max(abs(p[r][c] - (1.0 if r == c else 0.0))
                               for r in range(4) for c in range(4)))
    print(f"mat_inv4 worst residual   : {worst:.3e}")
    if worst > 1e-6:
        bad += 1

    worst = 0.0
    for _ in range(500):
        loc = tuple(rng.uniform(-50, 50) for _ in range(3))
        q = [rng.uniform(-1, 1) for _ in range(4)]
        n = math.sqrt(sum(v * v for v in q)) or 1.0
        q = tuple(v / n for v in q)
        s = tuple(rng.uniform(0.2, 3.0) for _ in range(3))
        b = compose(loc, q, s)
        l2, q2, s2 = decompose(b)
        b2 = compose(l2, q2, s2)
        worst = max(worst, max(abs(b[r][c] - b2[r][c])
                               for r in range(4) for c in range(4)))
    print(f"decompose/compose residual: {worst:.3e}")
    if worst > 1e-6:
        bad += 1

    print("RESULT:", "FAIL" if bad else "PASS")
    return 1 if bad else 0


def main(argv=()) -> int:
    argv = list(argv)
    if "--self-test" in argv:
        return _self_test()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
