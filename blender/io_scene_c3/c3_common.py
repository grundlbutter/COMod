"""
Shared helpers for the Conquer Online C3 addon.

This module is deliberately thin.  All format knowledge lives in the vendored
`c3phy` / `c3write` modules, which are pure stdlib and importable without
`bpy` (see core/c3phy.py, tools/c3write.py in the source repo).

--------------------------------------------------------------------------
The round-trip contract
--------------------------------------------------------------------------

The addon's acceptance test is: import a `.c3`, export it untouched, get an
identical file.  Three things make that non-trivial, and each is handled by the
same rule -- **"prefer the source when the view is unchanged"**:

1. The Blender-facing UV is `(u, 1 - v)`, and `1 - v` is NOT invertible in
   float32.  Measured on the shipped corpus: 32,227 of 1,475,160 V coordinates
   (2.2%) do not survive a double flip.  So the untouched original `(u, v)` is
   kept in a hidden per-vertex `FLOAT2` attribute, and export uses it whenever
   the visible UV still equals the flip of it.  Edit a UV and the edit wins.

2. Skinning has two ordered slots and `bone0 == bone1` on 201,057 of 737,580
   corpus vertices (27%), 2,280 of them with a real second weight.  Vertex
   groups cannot express "the same bone twice", so the exact slots live in
   hidden `c3_bone0/c3_bone1/c3_w0/c3_w1` attributes; the vertex groups are the
   editable view.  Same rule: groups that still match the source are ignored,
   groups that changed win.

3. Everything the engine reads but never interprets -- `unknown0`, the raw
   name/label bytes, the unsorted bbox pair, the A/B count split, the C3Key
   arrays, the trailing tags -- has no place in a Blender mesh, so it is stashed
   verbatim in object custom properties.

What is NOT stashed, because it turned out not to need to be: the 36-byte
"legacy gap" of the `PHY ` / `PHY2` vertex record.  It is zero on all 1,426,831
legacy-gap vertices in this install, so the writer emits zeros.
"""

import base64
import struct

# -- per-vertex attributes -------------------------------------------------
# The `_SRC` ones are the authoritative values; the others are the editable
# view.  All are float32/int32 domains, which Blender round-trips exactly.
ATTR_UV0_SRC = "c3_uv0_src"        # FLOAT2  POINT  original (u0, v0)
ATTR_UV1_SRC = "c3_uv1_src"        # FLOAT2  POINT  original (u1, v1), PHY5
ATTR_COLOR = "C3Color"             # BYTE_COLOR POINT  editable vertex colour
ATTR_COLOR_SRC = "c3_color_src"    # INT     POINT  packed RGBA, as stored
ATTR_BONE0 = "c3_bone0"            # INT     POINT
ATTR_BONE1 = "c3_bone1"            # INT     POINT
ATTR_W0 = "c3_w0"                  # FLOAT   POINT
ATTR_W1 = "c3_w1"                  # FLOAT   POINT
ATTR_NORMAL_SRC = "c3_normal_src"  # FLOAT_VECTOR POINT, variants that store it

HIDDEN_ATTRS = (ATTR_UV0_SRC, ATTR_UV1_SRC, ATTR_COLOR_SRC,
                ATTR_BONE0, ATTR_BONE1, ATTR_W0, ATTR_W1, ATTR_NORMAL_SRC)

UV0_NAME = "uv0"
UV1_NAME = "uv1"
#: The largest bone palette this corpus ACTUALLY SHIPS -- measured, not
#: assumed: **46**, on `003136000.c3`'s `v_body`, across 1,981 PHY chunks of
#: `CCO-snapshot-2026-08-24`. Next are 41 (x4) and 40 (x5).
#:
#: **THIS IS A FLOOR, NOT THE LIMIT, and the distinction is why exceeding it
#: WARNS rather than refuses.** The vertex shader in `Env_DX9/graphic.dll`
#: declares `c3_BoneMatrix[3 * C3_MAX_BONE_COUNT_PER_BATCH]`, and that bound
#: has since been read on ONE build -- see `SHADER_BONE_CEILING`. 46 remains
#: the separate, useful number: what the client is KNOWN to draw, measured
#: from shipped data rather than from one build's constant. Refusing an export
#: at a ceiling measured on a single build would block meshes that render
#: perfectly well -- inventing a limit is the same error as ignoring one,
#: pointed the other way.
LARGEST_SHIPPED_PALETTE = 46

#: The ceiling this build's shader compiles with: **70**, the value pushed to
#: the D3DXMACRO named `C3_MAX_BONE_COUNT_PER_BATCH`.
#:
#: MEASURED ON 7878, 28 of 28 reference sites, invariant (`push 0x46` adjacent
#: to the macro-name pointer). Read by VibeCO 2026-09-21, WITH ITS CAVEAT
#: CARRIED RATHER THAN DROPPED: the macro struct is built on the stack, so
#: there is no `{name, definition}` pair in static data to follow -- this is
#: the integer handed to that macro, one inference short of watching a helper
#: format it into the `const char*` that `Definition` must be. **Not
#: reproduced on 7939**: the section-offset maths was hardcoded to 7878's
#: layout, which is the same RVA-portability trap that started this thread.
#:
#: **IT IS A THIRD DATA POINT, NOT A GATE, and that is deliberate.** 70 is
#: what THIS build compiles with; an exported mesh may be loaded by another.
#: A 60-bone palette is legal on 7878 and might not be elsewhere, so the
#: warning reports all three numbers and lets the person judge. Turning it
#: into a refusal at 70 would export one build's constant as if it were the
#: format's.
SHADER_BONE_CEILING = 70
SHADER_BONE_CEILING_BUILD = "7878"


def exported_palette(rows):
    """The bones an exported skin actually references.

    `rows` is ``[(bone0, bone1, weight0, weight1), ...]`` as `c3_export._skin`
    produces. **`Phy_Load`'s rule (RVA 0x5A1A0): every bone0, plus bone1 only
    where weight1 is non-zero** -- a bone1 at zero weight drives nothing and
    is not in the palette, which is the same rule `attach_skin` mirrors when
    it decides which vertex groups to create.

    Pure, and here rather than in `c3_export`, because that module imports
    `bpy` and a rule with no test is how the >2-influence truncation went
    years without one.
    """
    pal = set()
    for row in rows:
        b0, b1, _w0, w1 = row[0], row[1], row[2], row[3]
        pal.add(int(b0))
        if w1 != 0.0:
            pal.add(int(b1))
    return pal


def palette_warning(n_bones, largest=LARGEST_SHIPPED_PALETTE):
    """A warning for an oversized palette, or ``None``.

    Reports a **measured comparison**, never a verdict: the shader's real
    bound is unread, so this says what is known (`largest`) and what is not,
    and lets the person decide. A message that claimed the export would fail
    would be asserting the `#define`'s value.
    """
    if n_bones <= largest:
        return None
    return (f"{n_bones} bones in this mesh's palette; the largest this corpus "
            f"ships is {largest}, and this build's shader compiles with "
            f"C3_MAX_BONE_COUNT_PER_BATCH={SHADER_BONE_CEILING} (measured on "
            f"{SHADER_BONE_CEILING_BUILD}; not established on other builds). "
            f"Above that a mesh may draw wrong or drop bones, and another "
            f"build may bound it differently")


BONE_FMT = "bone_%03d"
BONE_INDEX_PROP = "c3_bone_index"
#: The anatomical name `core/bonelabel.py` derived, e.g.
#: "forearm.R".  A PROPERTY, not a rename: every lookup in
#: `c3_anim` is still by `BONE_FMT`, so phase 1a cannot break
#: import, export or the motion loader.  Renaming is 1b.
BONE_LABEL_PROP = "c3_label"


# -- base64 custom-property helpers ---------------------------------------

def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(s) -> bytes:
    if not s:
        return b""
    if isinstance(s, bytes):
        return s
    return base64.b64decode(s)


def pack_keys(keys) -> bytes:
    """Flatten the three C3Key channels into one opaque blob."""
    out = bytearray()
    for slot in ("alphas", "draws", "change_texs"):
        arr = getattr(keys, slot) or []
        out += struct.pack("<I", len(arr))
        for rec in arr:
            out += rec
    return bytes(out)


def unpack_keys(blob: bytes, C3Key):
    k = C3Key()
    p = 0
    for slot in ("alphas", "draws", "change_texs"):
        if p + 4 > len(blob):
            break
        (n,) = struct.unpack_from("<I", blob, p)
        p += 4
        arr = []
        for _ in range(n):
            arr.append(blob[p:p + 16])
            p += 16
        setattr(k, slot, arr)
    return k


# -- axis conversion -------------------------------------------------------
#
# docs/modding.md 9.7: C3 is a left-handed frame with +Z down, wound clockwise
# from outside (D3D).  Negating Z stands the model up in Blender's Z-up frame
# AND flips handedness, which fixes the winding -- so the triangle index order
# is kept unchanged.  Do exactly one of the two, never both.

def to_blender(p):
    return (p[0], p[1], -p[2])


def to_c3(p):
    return (p[0], p[1], -p[2])


def flip_v(v: float) -> float:
    return 1.0 - v


def f32(x: float) -> float:
    """Round a Python float to what float32 storage will actually hold."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


# -- the chunk matrix ------------------------------------------------------
#
# The C3 matrix is row-vector (v' = v * M, basis in rows 0-2, translation in
# row 3) and the engine applies it to positions at load (RVA 0x5A735).
#
# We do NOT bake it into the vertices.  It becomes the Blender object's
# transform instead, so the model is placed correctly in the viewport while the
# mesh data stays exactly as it is on disk.  Export re-emits the stored matrix
# unless the user actually moved the object, in which case the object transform
# is converted back and wins.
#
# With S = diag(1, 1, -1) (the axis conversion above) and M3 the C3 3x3 basis:
#     p_blender          = S . p_c3
#     q_c3               = M3^T . p_c3 + t
#     => M_blender_3x3   = S . M3^T . S      (S is its own inverse)
#        M_blender_trans = S . t

def c3_matrix_to_blender(m):
    """16 C3 floats -> a 4x4 nested tuple in Blender's column-vector form."""
    if not m or len(m) != 16:
        m = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    b = [[0.0] * 4 for _ in range(4)]
    s = (1.0, 1.0, -1.0)
    for r in range(3):
        for c in range(3):
            # transpose (row-vector -> column-vector) then mirror both sides
            b[r][c] = s[r] * m[c * 4 + r] * s[c]
    for r in range(3):
        b[r][3] = s[r] * m[12 + r]
    b[3] = [0.0, 0.0, 0.0, 1.0]
    return tuple(tuple(row) for row in b)


def blender_matrix_to_c3(b):
    """Inverse of `c3_matrix_to_blender`.  `b` is a 4x4 row-of-rows."""
    s = (1.0, 1.0, -1.0)
    m = [0.0] * 16
    for r in range(3):
        for c in range(3):
            m[c * 4 + r] = s[r] * b[r][c] * s[c]
    for r in range(3):
        m[12 + r] = s[r] * b[r][3]
    m[3] = m[7] = m[11] = 0.0
    m[15] = 1.0
    return tuple(m)


IDENTITY16 = (1.0, 0.0, 0.0, 0.0,
              0.0, 1.0, 0.0, 0.0,
              0.0, 0.0, 1.0, 0.0,
              0.0, 0.0, 0.0, 1.0)


# -- packed vertex colour --------------------------------------------------
#
# 9.3: the file field at 0x14 is a u32 that reaches the GPU as
# R8G8B8A8_UNORM COLOR0, so little-endian byte 0 is R and byte 3 is A.

def unpack_rgba(c: int):
    return ((c & 0xFF) / 255.0, ((c >> 8) & 0xFF) / 255.0,
            ((c >> 16) & 0xFF) / 255.0, ((c >> 24) & 0xFF) / 255.0)


def pack_rgba(r, g, b, a) -> int:
    def q(x):
        return max(0, min(255, int(round(x * 255.0))))
    return q(r) | (q(g) << 8) | (q(b) << 16) | (q(a) << 24)


def as_signed32(u: int) -> int:
    u &= 0xFFFFFFFF
    return u - 0x100000000 if u >= 0x80000000 else u


def as_unsigned32(i: int) -> int:
    return i & 0xFFFFFFFF


# -- STEP <-> IDProperty ---------------------------------------------------
#
# ONE PURE FUNCTION EACH WAY, SO THE CONVERSION CAN BE GATED OUTSIDE BLENDER.
# It lived inline in `c3_import` and `c3_export`, which is a `bpy` module and a
# `bpy` module, so the only gate that could see it was blender-class -- and
# launching Blender needs owner clearance that no gate run here has. That is
# how M8 shipped a retype with its third consumer untouched.

#: Any |value| at or above this is a u32 BIT PATTERN from a pre-M8 `.blend`,
#: never a real scroll rate.
#:
#: **THE TWO POPULATIONS ARE DISJOINT BY MEASUREMENT, NOT BY GUESS.** Over the
#: whole of RSDB section 1 on 7878 -- 156,815 chunks carrying a STEP, 79,367
#: non-zero -- EVERY non-zero component read as f32 lands in 1e-5..1e0, with no
#: NaN, no Inf and no tail (`scratchpad/m8_step_values.py`). The pre-M8
#: property held those same dwords as signed int32, so a non-zero one is at
#: least 2^20 in magnitude. Between 1.0 and 1,048,576 there is nothing at all,
#: which is a six-decade margin rather than a threshold somebody tuned.
_STEP_OLD_FORMAT_MIN = 1 << 20


def step_to_prop(step) -> list:
    """`PhyMesh.step` -> the `c3_step` IDProperty. **Two floats.**

    It used to be `[as_signed32(x) for x in step]`, because `step` was two u32
    and Blender's IDProperty ints are int32. M8 made the field two f32; this
    stores floats, and `-0.017` reads as `-0.017` in Blender's UI instead of
    `-1131507814`.
    """
    return [float(x) for x in (step or (0.0, 0.0))]


def step_from_prop(value):
    """The `c3_step` IDProperty -> `PhyMesh.step`, or `None`.

    **READS A PRE-M8 `.blend` TOO, and that is not a guess.** A file saved by
    the old add-on holds the dwords as signed int32; a file saved by this one
    holds floats. The two ranges do not overlap -- see `_STEP_OLD_FORMAT_MIN`
    -- so a value at or beyond 2^20 is reinterpreted from its bit pattern
    rather than taken at face value.

    Without this, exporting an old `.blend` through the new f32 writer emits
    `3163243414.0` where `-0.017` was meant: a silent corruption of somebody's
    saved work, which is the same shape as staging a cut over a shared table.
    """
    if value is None:
        return None
    out = []
    for v in tuple(value)[:2]:
        f = float(v)
        if abs(f) >= _STEP_OLD_FORMAT_MIN and float(int(f)) == f:
            f = struct.unpack("<f", struct.pack("<I", as_unsigned32(int(f))))[0]
        out.append(f)
    while len(out) < 2:
        out.append(0.0)
    return (out[0], out[1])


# -- label decoding --------------------------------------------------------
#
# 9.5 note 4: the label is usually the original 3DSMax source-texture path and
# is frequently GBK-encoded Chinese.  Only ever DISPLAYED as text -- the exact
# bytes are what gets written back, so no codec is in the round-trip path.

def decode_label(raw: bytes) -> str:
    for enc in ("ascii", "gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", "replace")
