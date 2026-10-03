r"""
c3write.py -- serializer for the C3 `PHY*` chunk body and the MAXFILE C3
container.  The inverse of `core/c3phy.py`.

Design rule: **byte-exactness first.**  Anything the loader reads but does not
interpret -- the 36-byte legacy gap, `unknown0`, the raw name/label bytes, the
unsorted bounding-box pair, the A/B count split -- is carried through verbatim
by `c3phy.parse_phy` and re-emitted here unchanged.  The gate for this file is
`tests/test_roundtrip.py`: parse every PHY chunk in the corpus, re-serialize,
and require the bytes to be identical.

    from c3phy import parse_phy
    from c3write import serialize_phy
    assert serialize_phy(parse_phy(tag, body)) == body

Field order is exactly `docs/modding.md` section 9.4, which was recovered from
`graphic.dll!Phy_Load`.  Nothing here is guessed; every write mirrors a read
whose RVA is cited in c3phy.py.

CLI:

    py -3 tools/c3write.py verify <file.c3> [...]     # round-trip one or more
    py -3 tools/c3write.py corpus [--limit N]         # the full corpus gate
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from c3phy import (                                              # noqa: E402
    C3_MAGIC, LEGACY_GAP, VARIANTS, VERTEX_STRIDE,
    C3Key, PhyMesh, Vertex, iter_chunks, parse_phy,
)

__all__ = [
    "serialize_phy", "build_c3", "replace_phy_chunks",
    "recompute_bounds", "PhyWriteError",
    "motion_restore_report", "restore_motion", "MotionRestoreRefused",
]


class PhyWriteError(ValueError):
    pass


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _u32(v: int) -> bytes:
    if not (0 <= int(v) <= 0xFFFFFFFF):
        raise PhyWriteError(f"u32 out of range: {v}")
    return struct.pack("<I", int(v))


def _f(v: float) -> bytes:
    return struct.pack("<f", v)


def _f3(t) -> bytes:
    return struct.pack("<3f", *t)


def _name_bytes(m: PhyMesh) -> bytes:
    """The nameLen-prefixed name.

    `name_raw` is what was actually on disk (it can carry bytes after an
    embedded NUL, and it is *not* NUL-terminated by rule -- the length prefix
    is authoritative).  Prefer it; fall back to encoding `name` when a mesh was
    built from scratch.
    """
    if m.name_raw:
        return m.name_raw
    return m.name.encode("latin-1", "replace")


def _label_bytes(m: PhyMesh) -> bytes:
    """The labelLen-prefixed label.

    Usually the original 3DSMax source-texture path, and very often GBK.  The
    parser decodes it latin-1 (a byte-preserving codec), so re-encoding latin-1
    is lossless; but `label_raw` is used when present so no codec is involved
    at all.
    """
    if m.label_raw:
        return m.label_raw
    return m.label.encode("latin-1", "replace")


# --------------------------------------------------------------------------
# vertices
# --------------------------------------------------------------------------

def _encode_vertex(v: Vertex, has_normal: bool, has_uv1: bool,
                   legacy_gap: bool, step: bool) -> bytes:
    """Rebuild the 60-byte in-memory image, then slice out the on-disk record.

    Mirrors `_decode_vertex` + the per-variant read pattern in section 9.2:

        has_normal && legacy_gap :  0x0C, gap 0x24, 0x28
        has_normal && step       :  0x3C in one block
        has_normal               :  0x34
        !has_normal && legacy_gap:  0x0C, gap 0x24, 0x1C
        !has_normal              :  0x28
    """
    img = bytearray(VERTEX_STRIDE)
    struct.pack_into("<5f", img, 0x00, v.px, v.py, v.pz, v.u0, v.v0)
    struct.pack_into("<3I", img, 0x14,
                     int(v.unknown4) & 0xFFFFFFFF,
                     int(v.bone0) & 0xFFFFFFFF,
                     int(v.bone1) & 0xFFFFFFFF)
    struct.pack_into("<2f", img, 0x20, v.weight0, v.weight1)
    if has_normal:
        struct.pack_into("<3f", img, 0x28, v.nx, v.ny, v.nz)
    if has_uv1:
        struct.pack_into("<2f", img, 0x34, v.u1, v.v1)

    if step:
        return bytes(img)                       # whole 0x3C block

    tail = 0x28 if has_normal else 0x1C
    if legacy_gap:
        gap = v.gap or b"\x00" * LEGACY_GAP
        if len(gap) != LEGACY_GAP:
            gap = (gap + b"\x00" * LEGACY_GAP)[:LEGACY_GAP]
        return bytes(img[0x00:0x0C]) + gap + bytes(img[0x0C:0x0C + tail])
    return bytes(img[0x00:0x0C + tail])


# --------------------------------------------------------------------------
# the chunk body
# --------------------------------------------------------------------------

def serialize_phy(m: PhyMesh, *, tag: bytes | None = None) -> bytes:
    """Serialize a `PhyMesh` back to a PHY chunk body.

    For a mesh straight out of `parse_phy` with nothing changed, the result is
    byte-identical to the input.
    """
    tag = tag or m.tag
    if isinstance(tag, str):
        tag = tag.encode()
    if tag not in VARIANTS:
        raise PhyWriteError(f"not a known PHY variant: {tag!r}")
    var = VARIANTS[tag]
    has_normal = var["has_normal"]
    has_uv1 = var["step"]

    out = bytearray()

    # ---- name -------------------------------------------------------------
    nb = _name_bytes(m)
    out += _u32(len(nb))
    out += nb

    # ---- unknown0 + the two-u32 vertex count split ------------------------
    # `unknown0` is read and never used by the engine (values 0/1/2 observed).
    # Preserved rather than forced to 0 so unmodified meshes stay byte-exact.
    out += _u32(m.unknown0)

    n_verts = len(m.vertices)
    a, b = int(m.vertex_count_a), int(m.vertex_count_b)
    if a + b != n_verts:
        # Geometry changed.  Keep the partition when it is still expressible
        # (edits that only touched group B, or only group A), else collapse to
        # a single group -- the loader adds them, so this is always valid.
        if b and n_verts >= a:
            b = n_verts - a
        else:
            a, b = n_verts, 0
    out += _u32(a)
    out += _u32(b)

    # ---- vertices ---------------------------------------------------------
    for v in m.vertices:
        out += _encode_vertex(v, has_normal, has_uv1,
                              var["legacy_gap"], var["step"])

    # ---- the two-u32 face count split, then the u16 index block -----------
    n_faces = len(m.faces)
    fa, fb = int(m.face_count_a), int(m.face_count_b)
    if fa + fb != n_faces:
        if fb and n_faces >= fa:
            fb = n_faces - fa
        else:
            fa, fb = n_faces, 0
    out += _u32(fa)
    out += _u32(fb)

    if n_verts > 0xFFFF:
        raise PhyWriteError(
            f"{n_verts} vertices exceeds the u16 index limit of 65535")
    flat = []
    for f in m.faces:
        if len(f) != 3:
            raise PhyWriteError("faces must be triangles (triangle LIST)")
        for i in f:
            if not (0 <= i <= 0xFFFF):
                raise PhyWriteError(f"index {i} out of u16 range")
            flat.append(int(i))
    out += struct.pack(f"<{len(flat)}H", *flat)

    # ---- label ------------------------------------------------------------
    lb = _label_bytes(m)
    out += _u32(len(lb))
    out += lb

    # ---- bounding box (stored as an unsorted pair) ------------------------
    # The loader sorts componentwise, so either order loads the same; write
    # back the original pair when we have it so unmodified meshes match.
    if m.bbox_a and m.bbox_b:
        out += _f3(m.bbox_a) + _f3(m.bbox_b)
    else:
        out += _f3(m.bbox_min) + _f3(m.bbox_max)

    # ---- the 4x4 matrix ---------------------------------------------------
    mat = tuple(m.matrix) if m.matrix else (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0)
    if len(mat) != 16:
        raise PhyWriteError(f"matrix must be 16 floats, got {len(mat)}")
    out += struct.pack("<16f", *mat)

    # ---- frame count + the three C3Key channels ---------------------------
    out += _u32(m.frame_count)
    keys = m.keys or C3Key()
    for slot in ("alphas", "draws", "change_texs"):
        arr = getattr(keys, slot) or []
        out += _u32(len(arr))
        for rec in arr:
            if len(rec) != 16:
                raise PhyWriteError(f"C3Key {slot} record must be 16 bytes")
            out += rec

    # ---- optional trailing tags, in the loader's probe order --------------
    if m.step is not None:
        # TWO f32 (M8). This wrote `_u32` until 2026-09-16 and was
        # byte-exact only because `c3phy` read them wrongly the same
        # way. `tests/test_roundtrip.py` is the detector and it stays
        # at 4,781 / 4,781 across the change -- moving ONE side reds
        # 1,193 chunks, which is how that was confirmed rather than
        # assumed.
        out += b"STEP" + _f(m.step[0]) + _f(m.step[1])
    if m.two_sided:
        out += b"2SID"
    if m.billboard:
        out += (b"BILB", b"BIB2", b"BIB3", b"BIB4")[m.billboard - 1]

    # PHY5's STEP1/STEP2 region is kept verbatim: no PHY5 chunk ships in this
    # build, so the structural write path is unvalidated and re-emitting the
    # original bytes is the only honest option.
    if var["step"] and m.phy5_raw:
        out += m.phy5_raw

    # Anything the parser did not consume.  Corpus-wide this is empty.
    if m.tail_raw:
        out += m.tail_raw

    return bytes(out)


# --------------------------------------------------------------------------
# container
# --------------------------------------------------------------------------

def build_c3(chunks) -> bytes:
    """Build a MAXFILE C3 container from an iterable of (tag, body)."""
    out = bytearray(C3_MAGIC)
    for tag, body in chunks:
        if isinstance(tag, str):
            tag = tag.encode()
        if len(tag) != 4:
            raise PhyWriteError(f"chunk tag must be 4 bytes: {tag!r}")
        out += tag + _u32(len(body)) + body
    return bytes(out)


def replace_phy_chunks(original: bytes, meshes) -> bytes:
    """Rebuild a .c3, substituting the PHY chunks with `meshes` in order.

    Non-PHY chunks (MOTI, CAME, PTCL, ...) pass through untouched, which is
    what makes an import/export cycle safe: the addon only ever understands
    the geometry chunks.

    Strict: the mesh count must equal the original's PHY count.  For adding or
    removing meshes use `rebuild_c3(..., allow_structural=True)`.
    """
    it = iter(meshes)
    out = []
    for tag, body in iter_chunks(original):
        if tag in VARIANTS:
            try:
                m = next(it)
            except StopIteration:
                raise PhyWriteError(
                    "fewer meshes supplied than PHY chunks in the original")
            out.append((m.tag or tag, serialize_phy(m)))
        else:
            out.append((tag, body))
    for _extra in it:
        raise PhyWriteError(
            "more meshes supplied than PHY chunks in the original")
    return build_c3(out)


# --------------------------------------------------------------------------
# adding and removing whole meshes
#
# A PHY chunk is bound to a MOTI chunk POSITIONALLY: the i-th PHY in a
# container is animated by the i-th MOTI.  Established two ways --
#
#   * code: `graphic.dll!MeshCreate` (RVA 0x28360) loads the geometry and then
#     calls `MotionCreate` (0x28470) on the SAME file, and the combined walker
#     `sub_28D5B` dispatches each chunk on its tag into a per-tag list.  Only
#     the relative order WITHIN a tag matters, which is why both the
#     interleaved `PMPMPM...` and the grouped `PPPP MMMM` layouts work.
#   * data: on all 792 multi-mesh containers the i-th MOTI's `boneCount` covers
#     the i-th PHY's bone palette; a reversed or shifted pairing only fits 34%.
#
# and every container that has any PHY has exactly as many MOTI chunks --
# 5,083 of 5,083, no exceptions.  So a writer that changes the mesh count MUST
# change the motion count identically, or the pairing shears.
#
# See docs/modding.md section 11 for the full derivation, including why this is
# still NOT safe for meshes driven by a shared external motion set.
# --------------------------------------------------------------------------

MOTI_TAG = b"MOTI"

_IDENTITY_MAT = struct.pack(
    "<16f", 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def synth_moti(bone_count: int, frame_count: int = 1) -> bytes:
    """A neutral MOTI: one KKEY key at frame 0, identity for every bone.

    `Motion_GetMatrix` clamps to key 0 for every frame when there is only one
    key (RVA 0x551A0), so this is a static, do-nothing motion track -- the
    right default for a mesh the user just added, which has no animation to
    inherit.  `frame_count` should match the container's other tracks so the
    shared clock does not change length.

    Layout per docs/effects.md 6.2:
        u32 boneCount ; u32 frameCount ; "KKEY" ; u32 keyCount
        keyCount x { u32 frame ; float[16] matrix[boneCount] }
        u32 extraChannels
    """
    bone_count = max(1, int(bone_count))
    if bone_count > 255:
        raise PhyWriteError(
            f"MOTI boneCount {bone_count} exceeds the engine's limit of 255")
    return (struct.pack("<II", bone_count, max(1, int(frame_count)))
            + b"KKEY" + struct.pack("<I", 1) + struct.pack("<I", 0)
            + _IDENTITY_MAT * bone_count
            + struct.pack("<I", 0))


def _slot_plan(chunks, n_new: int, n_old: int):
    """Which chunk slots hold PHY and which hold MOTI, after a count change.

    Returns a list of slot descriptors preserving the container's original
    interleaving: `("phy", None)`, `("moti", None)` and `("raw", tag, body)`.
    Surplus pairs are appended after the last existing slot of their own tag;
    removed pairs drop the trailing slots.  That keeps `PMPM...` interleaved
    and `PPPPMMMM` grouped, and in both cases the i-th PHY still meets the
    i-th MOTI.
    """
    slots = []
    for tag, body in chunks:
        if tag in VARIANTS:
            slots.append(["phy"])
        elif tag == MOTI_TAG:
            slots.append(["moti"])
        else:
            slots.append(["raw", tag, body])

    delta = n_new - n_old
    for kind in ("phy", "moti"):
        # `s` may already be None from the previous pass's removals
        idx = [i for i, s in enumerate(slots) if s and s[0] == kind]
        if delta > 0:
            at = idx[-1] + 1 if idx else len(slots)
            for _ in range(delta):
                slots.insert(at, [kind])
                at += 1
        elif delta < 0:
            for i in idx[delta:]:
                slots[i] = None
    return [s for s in slots if s is not None]


def rebuild_c3(original: bytes, meshes, *, allow_structural: bool = False,
               moti_for_new=None, moti_override=None) -> bytes:
    """Rebuild a container, optionally adding or removing whole meshes.

    Each mesh's `source_index` says which original PHY slot it came from;
    `None` means it is new.  Meshes whose original slot is absent from the list
    are removed, and their paired MOTI chunk is removed with them.  New meshes
    get a synthesised static MOTI (see `synth_moti`).

    With `allow_structural=False` (the default) any change to the mesh count is
    refused, so the ordinary export path cannot restructure a container by
    accident.

    `moti_override` is ``{source_index: body}`` -- an already-serialized MOTI
    body to write in place of the original for that slot.  The default of
    ``None`` keeps the historical behaviour of carrying every motion track
    through verbatim, which is what an exporter that does not understand
    animation must do; the Blender addon supplies it only for the tracks it
    actually decoded into an action, so a container the user did not open the
    animation of is bit-for-bit untouched.  The bodies are NOT validated here:
    `effects.serialize_moti` is the writer and it refuses what it cannot
    express, so a body reaching this function has already been vouched for.
    """
    chunks = list(iter_chunks(original))
    phy_bodies = [b for t, b in chunks if t in VARIANTS]
    moti_bodies = [b for t, b in chunks if t == MOTI_TAG]
    n_old = len(phy_bodies)
    n_new = len(meshes)

    if n_new != n_old:
        if not allow_structural:
            raise PhyWriteError(
                f"the source container has {n_old} PHY chunks but {n_new} "
                f"meshes were supplied. Adding or removing meshes needs "
                f"allow_structural=True, and is only safe when the container "
                f"is animated by its own MOTI chunks -- see "
                f"docs/modding.md section 11.")
        if moti_bodies and len(moti_bodies) != n_old:
            raise PhyWriteError(
                f"container has {n_old} PHY but {len(moti_bodies)} MOTI "
                f"chunks; refusing to guess the pairing")

    # the frame count new tracks should adopt, so the shared clock is unchanged
    frame_count = 1
    for b in moti_bodies:
        try:
            frame_count = max(frame_count, struct.unpack_from("<I", b, 4)[0])
        except struct.error:
            pass

    out_phy, out_moti = [], []
    seen = set()
    for m in meshes:
        si = m.source_index
        if si is not None:
            if si in seen:
                raise PhyWriteError(
                    f"two meshes both claim source_index {si}")
            if not (0 <= si < n_old):
                raise PhyWriteError(
                    f"source_index {si} is outside the original's "
                    f"{n_old} PHY chunks")
            seen.add(si)
        out_phy.append(serialize_phy(m))
        if si is not None and si < len(moti_bodies):
            # its own motion: the edited body if the caller decoded one, else
            # the original bytes verbatim
            out_moti.append((moti_override or {}).get(si, moti_bodies[si]))
        elif moti_bodies:
            bones = m.bones
            need = (max(bones) + 1) if bones else 1
            out_moti.append((moti_for_new or synth_moti)(need, frame_count))

    tags = [m.tag for m in meshes]
    plan = _slot_plan(chunks, len(out_phy), n_old)
    result, pi, mi = [], 0, 0
    for s in plan:
        if s[0] == "phy":
            result.append((tags[pi], out_phy[pi]))
            pi += 1
        elif s[0] == "moti":
            if mi < len(out_moti):
                result.append((MOTI_TAG, out_moti[mi]))
            mi += 1
        else:
            result.append((s[1], s[2]))
    return build_c3(result)


# --------------------------------------------------------------------------
# restoring an original's MOTI onto a donor that has none
#
# THE MEASUREMENT THIS RESTS ON, AND THE THING IT REFUSES TO DO
# -------------------------------------------------------------
# You cannot decide, from a `.c3` alone, which MOTI belongs to a given PHY.
# The container carries no skeleton: `parse_moti` reads `boneCount`,
# `frameCount`, an encoding tag and a flat array of matrices, and there are no
# bone names, no parent indices and no inverse-bind matrices anywhere in it.
# The only thing tying PHY bone index `b` to MOTI matrix `b` is the artist's
# rig, and the rig is not in the file.
#
# So the MOTI is NOT a function of the PHY, and that is measurable.  Over
# `7205/c3/monster`, taking pairs of shipped containers whose PHY chunks are
# BYTE-IDENTICAL slot for slot and cross-applying one's MOTI to the other:
# 169 of 219 chunk comparisons (77.2%) move the median vertex by more than 1%
# of that mesh's own bounding-box diagonal, the worst by 1.85x the diagonal.
# Same geometry, different animation, and nothing computable from the geometry
# says which is right.  Every weaker structural test does worse -- the
# engine's own covering constraint (`max(palette) + 1 <= boneCount`) refuses
# 23 of 5,400 cross-applications while 34.8% of the ones it accepts are
# visibly wrong.  `docs/moti_retarget_2026-09-06.md` has the commands.
#
# What is left is the one case where nothing has to be decided: a donor whose
# PHY chunks agree with the original's on **every input the skinning function
# reads**.  `Phy_Calculate` (RVA 0x5629B) and the vertex declaration at
# 0x5A774..0x5A796 consume, per vertex, only the position, `bone0`, `bone1`
# and `weight0`/`weight1`; and the chunk matrix is applied to the positions
# before any of that (RVA 0x5A735).  A donor that matches on those produces
# BIT-IDENTICAL skinned output under the original's track, at every bone and
# every frame -- so the animation is provably unchanged, whatever else the
# chunk carries.  UVs, normals, faces, name, label, bounding box and the
# C3Key channels are all outside that set and may differ freely.
#
# Note what this deliberately does NOT permit, because both look tempting and
# both were measured wrong:
#   * equal bone palettes / equal boneCount / equal chunk names -- accepted
#     400 of 400 unrelated shipped mesh pairs (6609/c3/mesh);
#   * equal per-vertex BINDING with positions free -- vacuous on a rigid chunk,
#     where every vertex is bone 0 with weight1 0, so any two rigid chunks
#     match; 33 of 316 such pairs on 6609/c3/mesh are visibly wrong, up to
#     1.33x the diagonal (`001111210.C3` vs `002111210.C3`, `v_l_weapon`).
# Requiring the POSITIONS too is what closes that hole.
# --------------------------------------------------------------------------

#: Per vertex, everything the skinning path reads.  `unknown4`, `u0/v0`,
#: `u1/v1`, the normal and the 36-byte legacy gap are all absent on purpose --
#: none of them reaches `Phy_Calculate`.
_SKIN_FIELDS = ("px", "py", "pz", "bone0", "bone1", "weight0", "weight1")


def _skin_key(m: PhyMesh):
    return [tuple(getattr(v, f) for f in _SKIN_FIELDS) for v in m.vertices]


class MotionRestoreRefused(PhyWriteError):
    """The donor cannot be given the original's MOTI, with the reason why."""


def motion_restore_report(original: bytes, donor: bytes):
    """Can `donor` inherit `original`'s MOTI chunks?  -> (ok, lines).

    `lines` is the whole verdict, one entry per precondition and one per PHY
    ordinal, phrased so the caller can print it verbatim.  Every accepted
    ordinal says WHY it was accepted; every refusal names the ordinal and the
    field that differed.  Nothing is silent.
    """
    lines: list[str] = []
    try:
        o_chunks = list(iter_chunks(original))
        d_chunks = list(iter_chunks(donor))
    except Exception as e:                                      # noqa: BLE001
        return False, [f"container will not walk: {e}"]

    o_phy = [(t, b) for t, b in o_chunks if t in VARIANTS]
    d_phy = [(t, b) for t, b in d_chunks if t in VARIANTS]
    o_moti = [b for t, b in o_chunks if t == MOTI_TAG]
    d_moti = [b for t, b in d_chunks if t == MOTI_TAG]

    if not o_phy:
        return False, ["the original has no PHY chunk, so it has no motion "
                       "track to lend"]
    if len(o_moti) != len(o_phy):
        return False, [f"the original is itself unpaired: {len(o_phy)} PHY "
                       f"but {len(o_moti)} MOTI. Refusing to guess which "
                       f"track belongs to which mesh."]
    if d_moti:
        return False, [f"the donor already carries {len(d_moti)} MOTI "
                       f"chunk(s). This restores motion only to a donor that "
                       f"has NONE -- with some tracks present there is no way "
                       f"to tell which ordinals they claim."]
    if len(d_phy) != len(o_phy):
        return False, [f"the donor has {len(d_phy)} PHY chunk(s), the "
                       f"original {len(o_phy)}. The i-th PHY is bound to the "
                       f"i-th MOTI by ordinal and external motion sets are "
                       f"bound the same way (docs/modding.md 11.3), so a "
                       f"count change shears the pairing and no track can be "
                       f"carried across it."]

    lines.append(f"donor and original both hold {len(o_phy)} PHY chunk(s); "
                 f"the donor holds no MOTI, the original {len(o_moti)}")

    ok = True
    for i, ((ot, ob), (dt, db)) in enumerate(zip(o_phy, d_phy)):
        if ob == db:
            om = parse_phy(ot, ob)
            lines.append(f"  [{i}] {om.name!r}: PHY chunk is byte-identical to "
                         f"the original -- the track is being returned to the "
                         f"mesh it was authored for")
            continue
        if ot != dt:
            ok = False
            lines.append(f"  [{i}] REFUSED: variant {dt.decode('latin-1')} vs "
                         f"the original's {ot.decode('latin-1')}. The variant "
                         f"selects the on-disk vertex record, so the two are "
                         f"not comparable vertex for vertex.")
            continue
        try:
            om, dm = parse_phy(ot, ob), parse_phy(dt, db)
        except Exception as e:                                  # noqa: BLE001
            ok = False
            lines.append(f"  [{i}] REFUSED: will not parse -- {e}")
            continue
        if om.name != dm.name:
            ok = False
            lines.append(f"  [{i}] REFUSED: chunk name {dm.name!r} vs the "
                         f"original's {om.name!r}. A socket is looked up by "
                         f"name (graphic.dll 0x266D0), so a renamed chunk is "
                         f"a different slot.")
            continue
        if len(om.vertices) != len(dm.vertices):
            ok = False
            lines.append(f"  [{i}] REFUSED: {len(dm.vertices)} vertices vs the "
                         f"original's {len(om.vertices)}. A new vertex has a "
                         f"bone binding nothing in this file can vouch for.")
            continue
        if tuple(om.matrix) != tuple(dm.matrix):
            ok = False
            lines.append(f"  [{i}] REFUSED: the chunk matrix differs. It is "
                         f"applied to every position before skinning (RVA "
                         f"0x5A735), so the motion would act on a different "
                         f"space than it was authored in.")
            continue
        ok_key, dk_key = _skin_key(om), _skin_key(dm)
        if ok_key != dk_key:
            bad = next((j for j in range(len(ok_key))
                        if ok_key[j] != dk_key[j]), 0)
            ok = False
            lines.append(
                f"  [{i}] REFUSED: vertex {bad} differs in a field the "
                f"skinning path reads -- original "
                f"{dict(zip(_SKIN_FIELDS, ok_key[bad]))}, donor "
                f"{dict(zip(_SKIN_FIELDS, dk_key[bad]))}. Position and bone "
                f"binding are the motion's inputs; they may not move.")
            continue
        lines.append(
            f"  [{i}] {dm.name!r}: {len(dm.vertices)} vertices, every "
            f"position and bone binding identical to the original -- the "
            f"skinned output under this track is bit-identical at every bone "
            f"and every frame. (Only UV/normal/face/label data differs.)")
    return ok, lines


def restore_motion(original: bytes, donor: bytes) -> bytes:
    """Return `donor` with `original`'s MOTI chunks spliced in by ordinal.

    Raises `MotionRestoreRefused` -- carrying the whole report -- unless
    `motion_restore_report` says every ordinal is safe.  See the block comment
    above for what "safe" is measured to mean, and what it refuses.

    The i-th MOTI is written immediately after the i-th PHY.  Both the
    interleaved and the grouped layout ship in bulk and the engine treats them
    identically (docs/modding.md 11.1, evidence 2): `sub_28D5B` appends each
    chunk to its own tag's list, so only the order WITHIN a tag matters.
    """
    ok, lines = motion_restore_report(original, donor)
    if not ok:
        raise MotionRestoreRefused("\n".join(lines))
    o_moti = [b for t, b in iter_chunks(original) if t == MOTI_TAG]
    out, i = [], 0
    for tag, body in iter_chunks(donor):
        out.append((tag, body))
        if tag in VARIANTS:
            out.append((MOTI_TAG, o_moti[i]))
            i += 1
    return build_c3(out)


# --------------------------------------------------------------------------
# geometry helpers an exporter needs
# --------------------------------------------------------------------------

def recompute_bounds(m: PhyMesh) -> None:
    """Recompute the declared AABB from the vertices, matrix applied.

    Section 9.5 note 6: the shipped bbox matches the true extents ~95% of the
    time but the origin only ~50%, so it is not a reliable AABB -- but it IS
    what the engine frustum-culls against.  Call this only on meshes whose
    geometry actually changed; leave it alone otherwise so the original bytes
    survive.
    """
    if not m.vertices:
        return
    a = m.matrix if len(m.matrix) == 16 else None
    pts = []
    for v in m.vertices:
        x, y, z = v.px, v.py, v.pz
        if a:
            r0, r1, r2, r3 = a[0:3], a[4:7], a[8:11], a[12:15]
            x, y, z = (
                v.px * r0[0] + v.py * r1[0] + v.pz * r2[0] + r3[0],
                v.px * r0[1] + v.py * r1[1] + v.pz * r2[1] + r3[1],
                v.px * r0[2] + v.py * r1[2] + v.pz * r2[2] + r3[2],
            )
        pts.append((x, y, z))
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    # round-trip through float32 so the in-memory value equals what we write
    lo = struct.unpack("<3f", struct.pack("<3f", *lo))
    hi = struct.unpack("<3f", struct.pack("<3f", *hi))
    m.bbox_min, m.bbox_max = lo, hi
    m.bbox_a, m.bbox_b = lo, hi


IDENTITY_MATRIX = (1.0, 0.0, 0.0, 0.0,
                   0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0,
                   0.0, 0.0, 0.0, 1.0)


def unbake_matrix(m: PhyMesh) -> bool:
    """Inverse of `c3phy.apply_matrix_to`, for an importer that baked.

    Returns False when the matrix is singular (never seen in the corpus).
    """
    a = m.matrix
    if len(a) != 16:
        return False
    r = [[a[0], a[1], a[2]], [a[4], a[5], a[6]], [a[8], a[9], a[10]]]
    t = (a[12], a[13], a[14])
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
           - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    if abs(det) < 1e-20:
        return False
    inv = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            a1, a2 = [k for k in range(3) if k != j]
            b1, b2 = [k for k in range(3) if k != i]
            minor = (r[a1][b1] * r[a2][b2] - r[a1][b2] * r[a2][b1])
            inv[i][j] = ((-1) ** (i + j)) * minor / det
    for v in m.vertices:
        x, y, z = v.px - t[0], v.py - t[1], v.pz - t[2]
        v.px = x * inv[0][0] + y * inv[1][0] + z * inv[2][0]
        v.py = x * inv[0][1] + y * inv[1][1] + z * inv[2][1]
        v.pz = x * inv[0][2] + y * inv[1][2] + z * inv[2][2]
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _verify_blob(blob: bytes) -> tuple[int, int, list[str]]:
    """Round-trip every PHY chunk in a container.  -> (ok, total, problems)"""
    ok = total = 0
    problems = []
    for tag, body in iter_chunks(blob):
        if tag not in VARIANTS:
            continue
        total += 1
        try:
            m = parse_phy(tag, body)
            got = serialize_phy(m)
        except Exception as e:                                  # noqa: BLE001
            problems.append(f"{tag.decode(errors='replace')}: {e!r}")
            continue
        if got == body:
            ok += 1
        else:
            where = next((i for i in range(min(len(got), len(body)))
                          if got[i] != body[i]), min(len(got), len(body)))
            problems.append(
                f"{tag.decode(errors='replace')} {m.name!r}: "
                f"len {len(got)} vs {len(body)}, first diff at 0x{where:X}")
    return ok, total, problems


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "verify":
        tot_ok = tot = 0
        for a in rest:
            blob = Path(a).read_bytes()
            ok, n, probs = _verify_blob(blob)
            tot_ok += ok
            tot += n
            status = "OK" if ok == n else "FAIL"
            print(f"{status:4s} {Path(a).name}: {ok}/{n} chunks byte-exact")
            for p in probs:
                print(f"       {p}")
        print(f"\n{tot_ok}/{tot} PHY chunks byte-exact")
        return 0 if tot_ok == tot else 1

    if cmd == "corpus":
        import test_roundtrip                                   # noqa
        return test_roundtrip.main(rest)

    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    raise SystemExit(main(sys.argv[1:]))
