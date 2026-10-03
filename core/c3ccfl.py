r"""c3ccfl.py -- parser for the C3 ``CCFL`` chunk body.

WHAT IT IS
----------
An AUTHORING ANNOTATION block. It rides immediately after the chunk it
describes -- a ``PHY*``, a ``PTC3``, a ``PTCL`` or a ``SHAP`` -- and carries
the artist's own note plus the time the chunk was last written.

    "ccflag"                    6 bytes, literal
    u32  payload length         == len(body) - 14
    u32  entry count            1 or 2 across the whole corpus
      per entry:
        u16 entry length        INCLUDING these 4 header bytes
        u16 kind                1 = text, 10 = four floats
        bytes payload           entry length - 4

**IT IS NOT RENDER DATA, AND THAT IS THE USEFUL PART OF THIS FILE.** It was
opened while hunting the texture for the `lhd_*` banner meshes on
`2024tsf_new`, whose texture id appeared to resolve to another effect's
container rather than to an image. Every one of those meshes carries a
CCFL; every one of those CCFLs holds a single kind-1 text entry -- `"zz-scale
CreateTime:2023/12/27 20:22:13"` and the like -- and no texture reference of
any sort.

    The banner's texture was never in here and was never missing: an RSDB id
    is a row in SEVERAL typed tables, `path_for` returns the first (a mesh),
    and the `.dds` was sitting in the texture table all along
    (`wdb.ResourceDb`). This file is the record of a negative that was
    correct, on a question that turned out to have a different answer
    somewhere else.

HOW FAR THE DECODE IS CHECKED
-----------------------------
Over a DETERMINISTIC sample of the effect-mesh table -- every 17th path of
`ini/c3.wdb` RSDB section 1, 3,497 files that read, 7,995 CCFL chunks::

    parse clean, walking EXACTLY to the end    7,995 / 7,995
    entry counts                              1 x 6,472, 2 x 1,462, 3 x 61
    entry kinds                               1 x 7,995, 10 x 1,452,
                                              4 x 64, 13 x 39, 3 x 17,
                                              5 x 7, 9 x 2, 7 x 2, 14 x 1

An exact walk to the end on every one of 7,995 is the check that matters; a
stride wrong by any amount does not land on the end that many times.

    **THE FIRST VERSION OF THIS FILE SAID "entry counts 1 or 2" AND "kinds 1
    and 10" AND "kind 10 takes one of FOUR values".** All three were wrong,
    and the reason is worth more than the numbers: the population I measured
    was "every `.c3` a map effect names, mesh OR TEXTURE" -- and at the time
    a map effect's texture wrongly resolved to another effect's `.c3`. The
    corpus was 574 chunks of files I was reaching BY ACCIDENT, through a
    defect. Fixing the texture lookup shrank it to 216 and the gate went
    red, which is the only reason any of this was re-measured.

    A sample assembled by a bug is not a sample of anything. The corpus is
    now named explicitly and does not move when a resolver changes.

KIND 1 -- the note, present on every chunk. GBK text,
`<name> CreateTime:YYYY/MM/DD HH:MM:SS`. The names are the artists' own.

KIND 10 -- 16 bytes, four LE floats, following a ``PTC3``. **THIS PARAGRAPH
SAID "1,452 of 1,452 -- never once a mesh" AND THAT WAS A SAMPLE FIGURE
WEARING A POPULATION'S CLOTHES.** Over the whole of RSDB section 1 it is
**25,061 of 25,063, with 2 after a ``PHY4``**::

    c3/effect/yuanshen/skill/yuanshen_ds_newskill1xh/1.c3   after PHY4
    c3/effect/yuanshen/skill/yuanshen_ws_skill2/11.c3       after PHY4

`docs/ccfl_kind_13_2026-09-16.md`. The GATE was right and this prose was
wrong: `tests/test_c3ccfl` asserts the SAMPLED figure and still passes, so
nothing was broken -- but "never once" is a claim about a population and the
number behind it was a claim about 1 file in 17. **A universal quantifier is
the one word a sample may never lend you.**

The values are an offset drawn from {0, 0.5} with an extent from
{0, 0.5, 1.0}: eight distinct tuples, dominated by the four 0.5-extent ones.
Shaped like a UV sub-rect, and **the objection this paragraph used to raise
against that reading is now retired**: it said a 0.5 cell cannot line up
because the particle systems it rides declare an ``atlas`` of 1 or 4 and never
2. That reasoning assumed a SQUARE atlas. `KIND_ATLAS_COLS` (kind 3) overrides
the column count, so a system with ``texGrid`` 4 and ``K`` 8 has 0.5-wide
cells on the u axis, and the objection dissolves. Still not confirmed as a UV
sub-rect -- nothing has been rendered to test it -- but it is no longer
argued against.

KIND 19 -- present on the two newest installs and **in no table this repo
held until 2026-09-16**. Zero payload, after ``RIBB``/``PTC3``; the particle
reader's arm at ``graphic.dll`` 0x1506AB sets a presence byte and reads
nothing (`tools/effects.CCFL_READER_TYPES`). Recorded here because a format we
call decoded grew a new member and this parser said nothing: an unknown kind
is skipped by length rather than refused, which is the right behaviour and a
silent one.

THE OTHER KINDS -- 3, 4, 5, 7, 9, 13, 14 -- are decoded as far as their
bounds, with 3 and 13 given fields above. `CcflEntry.raw` carries the rest;
nothing here names a field inside them. **Counts in this docstring are from
the 1-in-17 sample unless they say otherwise**, which is the habit that made
the kind-10 sentence wrong.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"ccflag"

#: Bytes before the first entry: the magic plus the two u32.
HEADER = 14

#: Entry kinds seen on the shipped corpus.
KIND_TEXT = 1
#: Four LE f32 after a `PTC3`: a UV offset pair then an extent pair.
#: **APPLIED, AS A HYBRID** (`docs/ccfl_kind10_consumer_2026-09-16.md`): the
#: offset is ADDED to the flipbook cell's UV origin (0x14DCA9) and the extent
#: is MULTIPLIED into the cell SIZE (0x14F554); both sites load the annotation
#: through `PTC3+0x6C`. A flag at +0x58 gates it, and a clear flag draws the
#: unmodified cell.
#:
#: The three readings first proposed (REPLACE, MULTIPLY-into-cell, OFFSET)
#: were all wrong -- the truth was a fourth. Over 3,464 entries REPLACE and
#: OFFSET coincide on 3,350, only at cell (0,0) of a 2x2, which is why the
#: corpus alone could not settle it (`scratchpad/kind10_readings.py`).
#:
#: This module only decodes it. `effectplay.particle_quads` and
#: `webui/fx.js particleQuads` apply it; `tests/test_ccfl_kind10_applied.py`
#: gates both.
KIND_FLOAT4 = 10

#: The MESH scroll period, in milliseconds. `docs/ccfl_kind_13_2026-09-16.md`.
#:
#: An i32 at the CCFL object's ``+0x6C``, and the client drives the PRECEDING
#: mesh's scrolling UVs as ``timeGetTime() / period`` -- an absolute, shared
#: WALL CLOCK divided by this number, not the effect's own frame index.
#: `tools/effectplay.wall_counter` is the reference implementation of that
#: division and the thing both renderers match against.
#:
#: **DO NOT ENCODE "33 ms = 30 fps" ANYWHERE.** Every instance holds 33, so the
#: value carries nothing that distinguishes that hypothesis from any other; the
#: kind-13 agent declined the inference and gave its reason, and
#: `core/c3phy.py` already hardcodes a `/33.0` derived from a DIFFERENT
#: binary's RVAs, so treating the convergence as confirmation double-counts one
#: observation.
#:
#: MEASURED over the whole of RSDB section 1 on 7878 -- 59,393 files, 132,898
#: CCFL bodies, 0 parse refusals (`scratchpad/ccfl13_widths.py`):
#:
#:     kind 13 entries        775
#:     payload widths         4 bytes x775, and nothing else
#:     values                 33 x775
#:     preceding chunk        PHY4 749, PHY5 26   <- mesh only, never a particle
KIND_PERIOD_MS = 13

#: The particle atlas's COLUMN COUNT, overriding the square assumption.
#: `docs/ccfl_kind_3_2026-09-16.md`.
#:
#: A `PTC3` flipbook normally walks an N x N atlas where N is the chunk's own
#: `texGrid`. With a kind 3 present the atlas is **K columns wide**:
#: ``totalCells = K*N``, the cell modulus becomes ``K``, the u-axis cell width
#: becomes ``1/K``, and v keeps ``1/N``.
#:
#: **BOTH OF OUR RENDERERS ASSUME A SQUARE ATLAS AND SAY SO IN A COMMENT**
#: (`effectplay.Particle.cell`, `fx.js`), and VibeCO's GL/DX paths inherit the
#: same assumption from `effects.py`. On the 75 files that carry a kind 3, the
#: flipbook indexes the wrong cell and reads the wrong `u`.
#:
#: MEASURED over the whole of RSDB section 1 on 7878 -- 59,393 files, 132,898
#: CCFL bodies, 0 parse refusals (`scratchpad/ccfl_kind_widths.py 7878 3`):
#:
#:     kind 3 entries     225
#:     payload widths     4 bytes x225, and nothing else
#:     values             4 x196, 8 x13, 3 x12, 2 x4   -- never 0, never 1
#:     preceded by        PTC3 x225, and nothing else
#:
#: The parent distribution matches the CODE exactly: the arm lives only in the
#: PARTICLE reader, at `graphic.dll` 0x150581, reading one dword into +0x10
#: (`docs/ccfl_reader_type_map_2026-09-16.md`). Data and code showing the same
#: partition twice, which is what makes `tools/effects.CCFL_READER_TYPES`
#: keyable by parent class at all.
#:
#: Zero-initialised in the engine, so **0 means absent** -- and no instance in
#: the corpus stores 0, which is why `NO_COLS` is a separate sentinel rather
#: than a reuse of the value.
KIND_ATLAS_COLS = 3

#: The MESH UV-ANIMATION STEP: two LE f32, a per-axis scroll rate.
#: `docs/ccfl_kind_9_2026-09-16.md`, and the CONSUMER is settled -- see below.
#:
#: Two f32 landing at the parent object's ``+0x1CC`` on 7878 (``add eax,0x1CC``
#: at 0x15F2AA) and ``+0x1C4`` on 7632: **the displacement MOVED between
#: builds**, so never hardcode it. The client multiplies the pair by a mesh
#: counter at ``[ebp+0x1B0]`` -- the effect's OWN frame counter, not the
#: absolute wall clock kind 13 substitutes.
#:
#: WHAT IT DRIVES, read out of the shipped SHADER SOURCE rather than inferred
#: (2026-09-21, `Env_DX9/graphic.dll`, identical on 7878 and 7939). The DX9
#: renderer is programmable HLSL, and this pair is the uniform
#: ``c3_UVAnimStep``, ADDED to a vertex texcoord in the VERTEX shader:
#:
#:     uniform float2 c3_UVAnimStep;
#:     outVert.PixelTexCoord0 = inVert.c3_TexCoord0 + c3_UVAnimStep;
#:
#: Counted over the 30 shader sources that use it: added to
#: ``c3_TexCoord0`` **x25** and to ``c3_TexCoord1`` **x4**. So the dominant
#: consumer scrolls the PRIMARY UV -- the same channel kind 13 scrolls, on a
#: different counter -- and NOT a second UV set.
#:
#: **THIS RETIRED A PLANNED FEATURE.** `docs/ccfl_kind10_kind9_vibeco_2026-09-16.md`
#: scoped an `aUV1` build (emit `uv1` into the mesh payload, add a second
#: texcoord attribute, sample a second texture stage). That rested on
#: `c3phy`'s ``uv1 -> GPU TEXCOORD1``, which was the ONE line in its VERIFIED
#: block with no RVA beside it, and it is wrong for skinned meshes: 28 shaders
#: put ``c3_BoneIndexWeight`` in TEXCOORD1, and PHY5 meshes are skinned.
#:
#: MEASURED before the width refusal below, over every loose `.c3` on
#: ThroneOfKings7939 and 7878:
#:
#:     kind 9 entries      109
#:     payload widths      8 bytes x109, and nothing else
#:     preceding chunk     PHY5 x109  <- PHY5 ONLY, never PHY4, never a particle
#:     distinct values     (-0.01,-0.02) x29, (0.01,0.02) x26, (0.08,0.08) x3,
#:                         (0,0) x2, then seven singletons
#:
#: The PHY5 exclusivity is the half of the old reasoning that SURVIVED: kind 9
#: and the `uv1` FIELD are both PHY5-only. What did not survive is the
#: inference that the field is therefore what the annotation drives.
KIND_UV_ANIM_STEP = 9

#: `CcflEntry.atlas_cols` when the entry is not a kind 3.
NO_COLS = -1

#: `CcflEntry.period_ms` when the entry is not a kind 13. **Not 0**: zero is a
#: legal-looking period that would divide, and the point of a sentinel is that
#: it cannot be mistaken for an answer.
NO_PERIOD = -1


class CcflError(ValueError):
    """A CCFL body that does not decode. Raised rather than tolerated.

    The whole value of this parser is that it walks to the end exactly; a
    reader that shrugs at a short entry would turn that check into a
    coin flip.
    """


@dataclass
class CcflEntry:
    kind: int
    raw: bytes
    #: `kind == KIND_TEXT` only. GBK, falling back to latin-1 -- the
    #: annotation is the artist's own and the corpus is a Chinese build.
    text: str = ""
    #: `kind == KIND_FLOAT4` only.
    floats: tuple = ()
    #: `kind == KIND_PERIOD_MS` only; `NO_PERIOD` everywhere else.
    period_ms: int = NO_PERIOD
    #: `kind == KIND_ATLAS_COLS` only; `NO_COLS` everywhere else.
    atlas_cols: int = NO_COLS
    #: `kind == KIND_UV_ANIM_STEP` only: the `(rate_u, rate_v)` pair. Kept in
    #: its own field rather than reusing `floats`, which is documented as
    #: kind-10's four -- one field holding two different kinds' payloads is how
    #: a consumer reads the wrong one and cannot tell.
    uv_anim: tuple = ()


@dataclass
class Ccfl:
    entries: list = field(default_factory=list)

    @property
    def note(self) -> str:
        """The first text entry, or `''`. What a human wants from this."""
        for e in self.entries:
            if e.kind == KIND_TEXT:
                return e.text
        return ""

    @property
    def period_ms(self) -> int:
        """The mesh scroll period in ms, or `NO_PERIOD`.

        A chunk carries at most one kind 13 on the measured corpus; the FIRST
        wins rather than the last, so a second would be a visible
        inconsistency rather than a silently preferred one.
        """
        for e in self.entries:
            if e.kind == KIND_PERIOD_MS:
                return e.period_ms
        return NO_PERIOD

    @property
    def uv_rect(self) -> tuple:
        """The kind-10 quad ``(off_u, off_v, ext_u, ext_v)``, or `()`.

        Offset ADDED to the cell origin, extent MULTIPLIED into the cell size
        -- see `KIND_FLOAT4`. The renderers apply it; this only decodes it.
        """
        for e in self.entries:
            if e.kind == KIND_FLOAT4 and e.floats:
                return tuple(e.floats)
        return ()

    @property
    def uv_anim_step(self) -> tuple:
        """The kind-9 ``(rate_u, rate_v)`` scroll rate, or `()`.

        The consumer adds ``rate * per-effect-counter`` to the mesh's PRIMARY
        UV -- the shipped shader is ``PixelTexCoord0 = c3_TexCoord0 +
        c3_UVAnimStep`` in 25 of 30 uses. It is NOT a second UV set; see
        `KIND_UV_ANIM_STEP`.

        The FIRST wins, as with every other kind here, so a second entry is a
        visible inconsistency rather than a silently preferred one. No shipped
        chunk carries two.
        """
        for e in self.entries:
            if e.kind == KIND_UV_ANIM_STEP and e.uv_anim:
                return tuple(e.uv_anim)
        return ()

    @property
    def atlas_cols(self) -> int:
        """The atlas column count, or `NO_COLS`.

        The corpus holds at most one kind 3 per chunk and the value is
        constant per file; the FIRST wins, so a second is a visible
        inconsistency rather than a silently preferred one.
        """
        for e in self.entries:
            if e.kind == KIND_ATLAS_COLS:
                return e.atlas_cols
        return NO_COLS


def parse_ccfl(body: bytes) -> Ccfl:
    """One ``CCFL`` chunk body -> its entries.

    Raises `CcflError` on anything that does not walk exactly to the end.
    """
    b = bytes(body)
    if not b.startswith(MAGIC):
        raise CcflError(f"not a CCFL body: starts {b[:8]!r}")
    if len(b) < HEADER:
        raise CcflError(f"body is {len(b)} bytes, header alone is {HEADER}")
    plen, count = struct.unpack_from("<II", b, 6)
    if plen != len(b) - HEADER:
        raise CcflError(
            f"declared payload {plen} but body carries {len(b) - HEADER}")
    out = Ccfl()
    pos = HEADER
    for i in range(count):
        if pos + 4 > len(b):
            raise CcflError(f"entry {i} header runs past the end at {pos}")
        elen, kind = struct.unpack_from("<HH", b, pos)
        if elen < 4 or pos + elen > len(b):
            raise CcflError(f"entry {i} length {elen} at {pos} of {len(b)}")
        raw = b[pos + 4:pos + elen]
        e = CcflEntry(kind=kind, raw=raw)
        if kind == KIND_TEXT:
            try:
                e.text = raw.decode("gbk")
            except Exception:                                # noqa: BLE001
                e.text = raw.decode("latin-1")
        elif kind == KIND_FLOAT4:
            # REFUSED ON WIDTH, like kinds 3 and 13. This read
            # `and len(raw) == 16` and SILENTLY SKIPPED anything else, which
            # made it the one member of this family that shrugs -- and a
            # shrugging decode is indistinguishable from an absent field.
            #
            # Measured over the whole of RSDB section 1 on 7878 before the
            # refusal shipped (`scratchpad/ccfl_kind_widths.py 7878 10`):
            # 59,393 files, 132,898 CCFL bodies, 0 parse refusals, and
            # **25,063 kind-10 entries, every one exactly 16 bytes**. So this
            # reds nothing today and surfaces the first install that varies it.
            if len(raw) != 16:
                raise CcflError(
                    f"entry {i} is kind {KIND_FLOAT4} (uv offset+extent) with "
                    f"{len(raw)} payload bytes, not 16: {raw[:16]!r}")
            e.floats = struct.unpack("<4f", raw)
        elif kind == KIND_UV_ANIM_STEP:
            # REFUSED ON WIDTH, like kinds 3, 10 and 13, and measured first so
            # the refusal is a tripwire rather than an outage: 109 kind-9
            # entries over every loose `.c3` on 7939 and 7878, every one
            # exactly 8 bytes. A silently truncated scroll RATE is a rate wrong
            # by an arbitrary factor, and a wrong rate looks like art rather
            # than like a bug -- the same argument kind 13 carries.
            if len(raw) != 8:
                raise CcflError(
                    f"entry {i} is kind {KIND_UV_ANIM_STEP} (uv anim step) "
                    f"with {len(raw)} payload bytes, not 8: {raw[:16]!r}")
            e.uv_anim = struct.unpack("<2f", raw)
        elif kind == KIND_PERIOD_MS:
            # REFUSED, not tolerated, and the WIDTH is the whole check.
            #
            # Measured over the WHOLE of section 1 before this shipped, not on
            # the 1-in-17 sample the gate uses: every kind-13 payload on 7878
            # is exactly 4 bytes and every value is 33. So this reds nothing
            # today and surfaces the first install that varies it -- which is
            # what a renderer consuming this number needs, because a silently
            # truncated period is a scroll rate wrong by an integer factor,
            # and a scroll rate wrong by an integer factor looks like art.
            #
            # A refusal that reds the shipped corpus is an outage rather than
            # a tripwire, which is why the measurement came first.
            if len(raw) != 4:
                raise CcflError(
                    f"entry {i} is kind {KIND_PERIOD_MS} (period ms) with "
                    f"{len(raw)} payload bytes, not 4: {raw[:16]!r}")
            e.period_ms = struct.unpack("<i", raw)[0]
        elif kind == KIND_ATLAS_COLS:
            # Same refusal, same reason, and measured the same way before it
            # shipped: every kind-3 payload in section 1 is exactly 4 bytes.
            # A truncated column count is an atlas the wrong width, which
            # indexes a real cell of a real texture and therefore looks like
            # art rather than like a bug.
            if len(raw) != 4:
                raise CcflError(
                    f"entry {i} is kind {KIND_ATLAS_COLS} (atlas columns) "
                    f"with {len(raw)} payload bytes, not 4: {raw[:16]!r}")
            e.atlas_cols = struct.unpack("<i", raw)[0]
        out.entries.append(e)
        pos += elen
    if pos != len(b):
        raise CcflError(f"walk ended at {pos}, body is {len(b)}")
    return out
