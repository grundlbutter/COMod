#!/usr/bin/env python3
r"""editbundle.py -- EXPORT / IMPORT / VERIFY for round-trippable asset edits.

WHAT THIS GENERALISES
---------------------
`tools/c3write.py:replace_phy_chunks` already states the whole design in one
sentence, for one format::

    Non-PHY chunks (MOTI, CAME, PTCL, ...) pass through untouched, which is
    what makes an import/export cycle safe: the addon only ever understands
    the geometry chunks.

This module is that sentence with "PHY" replaced by "the region a tool asked
for" and "the addon" replaced by "any external tool".  Three operations::

    EXPORT   asset bytes            -> (payload, manifest, carry)
    IMPORT   (payload, manifest, carry, the CURRENT on-disk original)
                                    -> new asset bytes
    VERIFY   import(export(x)) == x, byte for byte, or the tool is not wired in.

WHY THE CARRY IS NOT OPTIONAL -- THE MOTI LESSON, STATED ONCE
-------------------------------------------------------------
`effects.parse_moti` decodes a ZKEY chunk's quaternion+translation into a 4x4
matrix.  That matrix is **derived and lossy**: MEASURED on this corpus, 686 of
1,393 ZKEY chunks carry at least one quaternion with ``w < 0`` or ``|q| != 1``,
neither of which survives the trip through a matrix.  A writer working from the
matrices cannot reproduce the bytes it was handed, and `serialize_moti` is
right to REFUSE rather than guess.

Generalised, that is the one rule this module enforces:

    **Every field of an exported region is either EDITABLE or DERIVED.
    A DERIVED field is shipped in the payload for the tool's benefit and is
    RECONSTRUCTED FROM CARRY on import.  If the payload's value disagrees with
    the reconstruction, the import REFUSES.  Nothing is ever silently
    dropped.**

The mechanism is deliberately not a per-format special case.  `_check_derived`
re-decodes the bytes it is about to hand back and compares only the declared
derived paths.  That makes the check *tell the truth about what the bytes
mean*, and it makes the ZKEY/KKEY asymmetry fall out for free: editing
``keys[].matrices`` on a KKEY chunk is accepted, because the matrices ARE the
disk form there and the re-decode agrees; editing them on a ZKEY chunk is
refused, because the re-decode returns what the carried quaternions say.
One declaration, two correct behaviours, no branch.

WHAT A BUNDLE IS
----------------
Three artefacts, so a multi-megabyte verbatim block never has to pass through
JSON as base64::

    manifest.json   provenance + the region locator + hashes of the other two
    payload.json    the EDITABLE view -- this is the file a tool opens
    carry.bin       the verbatim original bytes of everything NOT exposed,
                    addressed by a named-slice map in the manifest

`EditBundle` holds all three in memory; `write_dir`/`read_dir` are the on-disk
form.  Nothing here writes to the game install: the output of `import_region`
is bytes, and `tools/comod.py`'s existing `stage`/`install`/`uninstall` is the
only thing that puts them anywhere.

PROVENANCE
----------
The manifest records the install by `coroot.base_id` (via `core/provenance.py`,
which already owns "which install did this come from") and the install's
FOLDER NAME, never its path -- same rule as `provenance._install_name`, and the
reason `tests/test_sanitization.py` stays green.  It records the container's
sha256 and the region's offset/length/sha256, and `import_region` validates all
of that against the original it is handed.  A bundle exported from one client
and imported onto another is REFUSED, not merged.

WHAT ROUND-TRIPS
----------------
Registered codecs, all gated by `tests/test_editbundle_roundtrip.py`:

    phy      PHY / PHY2 / PHY3 / PHY4 / PHY5 chunk bodies   (core/c3phy +
                                                             tools/c3write)
    moti     MOTI chunk bodies, all four encodings          (tools/effects)
    rsdb     RSDB / RSDC sections of `ini/c3.wdb`           (core/wdb + core/dbc)
    opaque   ANY region, exported verbatim, editable view empty

`opaque` is what makes the abstraction total rather than a list of three
formats: every CAME / PTCL / SHAP / SMOT / CCFL / MNEW chunk and every MATR /
EFFE / ROPT / SIMO section exports and imports today, and gains an editable
view the day a codec is registered for it -- with no change to the bundle
contract, the manifest schema, or the gate.  **An opaque region is counted in
its own column and is never summed with the decoded ones**, because "it came
back the same" is a much weaker statement when nothing decoded it.

CLI::

    py -3 tools/editbundle.py regions <asset>
    py -3 tools/editbundle.py export <asset> --index N --out DIR [--root R]
    py -3 tools/editbundle.py import DIR --original <asset> --out FILE
    py -3 tools/editbundle.py verify <asset> [...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

import c3phy                                                    # noqa: E402
import c3write                                                  # noqa: E402
import dbc                                                      # noqa: E402
import effects                                                  # noqa: E402

BUNDLE_VERSION = 1
PAYLOAD_VERSION = 1
TOOL = "editbundle"

__all__ = [
    "BundleError", "BundleRefusal", "UnknownContainer",
    "Region", "EditBundle",
    "open_container", "export_region", "import_region", "verify_asset",
    "CODECS", "CONTAINERS",
]


class BundleError(ValueError):
    """The bundle is malformed, or the region cannot be expressed."""


class BundleRefusal(BundleError):
    """A refusal, not a failure.

    Raised where carrying on would produce a plausible wrong answer: a
    manifest that does not match the original it was handed, an edit to a
    DERIVED field, a length change a container cannot express.  Every one of
    these has a silent-corruption twin that this module exists to not be.
    """


class UnknownContainer(BundleError):
    """No container model for these bytes.  Raised, never guessed past."""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ==========================================================================
# regions and containers
# ==========================================================================

@dataclass(frozen=True)
class Region:
    """One independently replaceable span of an asset.

    `offset`/`length` address the region's BODY -- not its header -- so a
    splice is a byte-slice substitution and everything outside it, header
    bytes included, is carried by construction rather than by rebuilding.
    """
    container: str
    index: int              # ordinal within the container, the bundle's key
    offset: int
    length: int
    tag: str                # "PHY3", "MOTI", "RSDB", "MATR", "-" for flat
    codec: str              # a key of CODECS; "opaque" when nothing decodes it

    def describe(self) -> str:
        return (f"[{self.index}] {self.tag} {self.codec} "
                f"@0x{self.offset:X} +{self.length}")


class Container:
    """A byte layout that can enumerate replaceable regions and splice one.

    Subclasses own exactly two things: where the regions are, and what has to
    be corrected when one changes length.  They never rebuild the file.
    """
    kind = "?"

    def __init__(self, data: bytes):
        self.data = bytes(data)

    @staticmethod
    def detect(data: bytes) -> bool:                      # pragma: no cover
        raise NotImplementedError

    def regions(self) -> list[Region]:                    # pragma: no cover
        raise NotImplementedError

    def body(self, region: Region) -> bytes:
        return self.data[region.offset:region.offset + region.length]

    def _fix_lengths(self, out: bytearray, region: Region, delta: int) -> None:
        raise BundleRefusal(
            f"{self.kind}: this container cannot express a length change "
            f"({delta:+d} bytes on region {region.index})")

    def splice(self, region: Region, new_body: bytes) -> bytes:
        """The whole asset with `region`'s body replaced.  Everything else is
        the ORIGINAL BYTES, sliced, not re-emitted."""
        if region.container != self.kind:
            raise BundleRefusal(
                f"region says container {region.container!r}, this is "
                f"{self.kind!r}")
        end = region.offset + region.length
        if end > len(self.data):
            raise BundleError(f"region {region.index} runs past EOF")
        out = bytearray(self.data[:region.offset])
        out += new_body
        out += self.data[end:]
        delta = len(new_body) - region.length
        if delta:
            self._fix_lengths(out, region, delta)
        return bytes(out)


class C3Container(Container):
    """MAXFILE C3: a 16-byte magic then ``{char[4] tag, u32 len, body}``."""
    kind = "c3"

    @staticmethod
    def detect(data: bytes) -> bool:
        return data.startswith(c3phy.C3_MAGIC)

    def regions(self) -> list[Region]:
        out: list[Region] = []
        p = len(c3phy.C3_MAGIC)
        d = self.data
        while p + 8 <= len(d):
            tag = d[p:p + 4]
            (ln,) = struct.unpack_from("<I", d, p + 4)
            if p + 8 + ln > len(d):
                raise BundleError(
                    f"chunk {tag!r} at 0x{p:X} declares {ln} bytes, "
                    f"{len(d) - p - 8} remain")
            out.append(Region(self.kind, len(out), p + 8, ln,
                              tag.decode("latin-1"), codec_for_tag(tag)))
            p += 8 + ln
        return out

    def _fix_lengths(self, out: bytearray, region: Region, delta: int) -> None:
        # The chunk's own u32 length sits 4 bytes before its body, and nothing
        # else in the container records a chunk position: the walk is
        # sequential, so later chunks simply move.
        struct.pack_into("<I", out, region.offset - 4,
                         region.length + delta)


class WdbContainer(Container):
    """``ini/c3.wdb`` -- BDMG header then `sectionCount` sections back to back.

    Two header fields depend on the total length (0x04 file size, 0x08 end of
    sections) and are corrected on a length change.  **Both are checked
    against the file at construction**, and a file where either does not hold
    refuses length-changing edits rather than writing a header it cannot
    justify -- `header_exact` is that answer, recorded in the manifest.

    Nothing else moves.  A section's position is implicit in the sequential
    walk and an RSDB string offset is relative to its OWN table's start (the
    2026-08-09 correction in `core/wdb.py`), so sections after an edited one
    shift without any field needing an update.
    """
    kind = "wdb"
    MAGIC = b"BDMG"
    MAX_PATH = 260

    def __init__(self, data: bytes):
        super().__init__(data)
        d = self.data
        if len(d) < 0x10 or not d.startswith(self.MAGIC):
            raise UnknownContainer("not a BDMG database")
        self.declared_size, self.end_of_sections, self.section_count = \
            struct.unpack_from("<III", d, 0x04)
        self._regions: Optional[list[Region]] = None
        self.walk_stopped: Optional[tuple] = None

    @staticmethod
    def detect(data: bytes) -> bool:
        return data.startswith(WdbContainer.MAGIC) and len(data) >= 0x10

    @property
    def header_exact(self) -> bool:
        """Do the two length-dependent header fields describe THIS file?

        `declared_size` must equal the file length and `end_of_sections` must
        equal the byte the section walk lands on.  Measured true on every
        shipped client (`core/wdb.py`, 30 of 30); a file where it is false is
        not refused for READING, only for length-changing writes.
        """
        rs = self.regions()
        if not rs or self.walk_stopped is not None:
            return False
        walked_to = rs[-1].offset + rs[-1].length
        return (self.declared_size == len(self.data)
                and self.end_of_sections == walked_to)

    def regions(self) -> list[Region]:
        if self._regions is not None:
            return self._regions
        d = self.data
        out: list[Region] = []
        pos = 0x10
        while pos + 8 <= len(d):
            if len(out) >= self.section_count:
                break
            tag = bytes(d[pos:pos + 4])
            if not all(32 <= c < 127 for c in tag):
                self.walk_stopped = (pos, repr(tag), "not an ascii section tag")
                break
            (n,) = struct.unpack_from("<I", d, pos + 4)
            if tag in ROW_TABLE_TAGS and n > 0:
                got = _rsdb_span(d, pos, n, self.MAX_PATH)
                if got is None:
                    self.walk_stopped = (
                        pos, tag.decode("latin-1"),
                        f"{n} rows declared, none resolved at stride 8 or 12")
                    break
                span = got["end"] - pos
                codec = "rsdb"
            else:
                try:
                    span = dbc.section_span(d, pos)
                except dbc.UnknownSection as e:
                    self.walk_stopped = (pos, tag.decode("latin-1"), str(e))
                    break
                except Exception as e:                          # noqa: BLE001
                    self.walk_stopped = (pos, tag.decode("latin-1"),
                                         f"{e.__class__.__name__}: {e}")
                    break
                codec = "opaque"
            # The BODY of a section is the whole section INCLUDING its
            # {magic, count} header: unlike a C3 chunk the header is part of
            # what a codec has to re-emit (the count changes when rows do).
            out.append(Region(self.kind, len(out), pos, span,
                              tag.decode("latin-1"), codec))
            pos += span
        if self.walk_stopped is None and len(out) != self.section_count:
            self.walk_stopped = (pos, "-",
                                 f"walked {len(out)} sections, header at 0x0c "
                                 f"declares {self.section_count}")
        self._regions = out
        return out

    def _fix_lengths(self, out: bytearray, region: Region, delta: int) -> None:
        if not self.header_exact:
            raise BundleRefusal(
                "c3.wdb header does not describe this file "
                f"(size field {self.declared_size} vs {len(self.data)}, "
                f"end-of-sections {self.end_of_sections}); refusing a "
                f"length-changing edit rather than writing a header that "
                f"cannot be justified")
        struct.pack_into("<I", out, 0x04, self.declared_size + delta)
        struct.pack_into("<I", out, 0x08, self.end_of_sections + delta)


class FlatContainer(Container):
    """The fallback: one region covering the whole file.

    Not a placeholder -- it is what makes every asset expressible.  A `.dds`,
    a `.dat`, an `.ini` has exactly one replaceable region, and the bundle
    contract (provenance, hashes, verbatim carry, verify) applies to it
    unchanged.
    """
    kind = "flat"

    @staticmethod
    def detect(data: bytes) -> bool:
        return True

    def regions(self) -> list[Region]:
        return [Region(self.kind, 0, 0, len(self.data), "-",
                       "opaque")]

    def _fix_lengths(self, out, region, delta) -> None:
        return  # the region IS the file; nothing records its length


#: Ordered.  `FlatContainer` accepts everything, so it must be last.
CONTAINERS = (C3Container, WdbContainer, FlatContainer)


def open_container(data: bytes) -> Container:
    for cls in CONTAINERS:
        try:
            if cls.detect(data):
                return cls(data)
        except UnknownContainer:
            continue
    raise UnknownContainer("no container model for these bytes")


# ==========================================================================
# codecs
# ==========================================================================

ROW_TABLE_TAGS = (b"RSDB", b"RSDC")


def codec_for_tag(tag: bytes) -> str:
    if tag in c3phy.VARIANTS:
        return "phy"
    if tag == b"MOTI":
        return "moti"
    if tag in ROW_TABLE_TAGS:
        return "rsdb"
    return "opaque"


class Codec:
    """Decode a region body into (EDITABLE view, CARRY blocks) and back.

    `DERIVED` names the payload paths that are reconstructed from carry rather
    than read from the payload.  They are checked by re-decoding the bytes the
    encoder produced -- see `_check_derived` -- so a codec never has to
    describe its own lossiness twice.
    """
    key = "?"
    DERIVED: tuple = ()

    def decode(self, body: bytes, tag: str) -> tuple[dict, dict]:
        raise NotImplementedError                          # pragma: no cover

    def encode(self, editable: dict, carry: dict, tag: str) -> bytes:
        raise NotImplementedError                          # pragma: no cover


class OpaqueCodec(Codec):
    """No editable view at all.  The body is carried whole.

    This is the honest answer for a format nothing here decodes, and it is
    distinct from "round-tripped": the gate counts opaque regions in their own
    column so a corpus of CAME and PTCL chunks can never inflate a claim about
    the codecs that actually parse.
    """
    key = "opaque"

    def decode(self, body, tag):
        return {}, {"body": bytes(body)}

    def encode(self, editable, carry, tag):
        if editable:
            raise BundleRefusal(
                f"the {tag!r} region has no editable view (codec 'opaque'); "
                f"the payload carries {sorted(editable)} which cannot be "
                f"written back. Register a codec for {tag!r} first.")
        return carry["body"]


# -- PHY -------------------------------------------------------------------

_VERT_FLOATS = ("px", "py", "pz", "u0", "v0", "weight0", "weight1",
                "nx", "ny", "nz", "u1", "v1")
_VERT_INTS = ("unknown4", "bone0", "bone1")


class PhyCodec(Codec):
    r"""PHY chunk body <-> `core/c3phy.PhyMesh`.

    Editable: the geometry and everything the engine reads -- vertices, faces,
    the A/B count splits, the bounding pair, the matrix, the frame count, the
    STEP/2SID/BILB flags.

    Derived: ``name`` and ``label``.  `parse_phy` splits the name at its first
    NUL and decodes latin-1, so the string is a VIEW of `name_raw` -- and
    `name_raw` is authoritative because the on-disk field is length-prefixed,
    not NUL-terminated, and can carry bytes past the NUL.  A container whose
    name reads ``"Box01"`` may hold six more bytes after it.  Editing the
    string would drop them, so the string is read-only and the raw bytes are
    carried.  (Renaming a mesh is a real want; it is a v2 feature that has to
    edit the CARRY, not a reason to make the drop silent.)

    Carried verbatim: `name_raw`, `label_raw`, the 36-byte legacy vertex gaps
    of PHY /PHY2, the three 16-byte C3Key channels, the PHY5 STEP1/STEP2
    region and any trailing bytes.  All of them are things `serialize_phy`
    re-emits unchanged and none of them are reachable from the decoded view.
    """
    key = "phy"
    DERIVED = ("name", "label")

    def decode(self, body, tag):
        m = c3phy.parse_phy(tag.encode("latin-1"), body)
        verts = []
        gaps = bytearray()
        for v in m.vertices:
            rec = {k: getattr(v, k) for k in _VERT_FLOATS}
            rec.update({k: int(getattr(v, k)) for k in _VERT_INTS})
            verts.append(rec)
            gaps += (v.gap or b"")
        keys = m.keys or c3phy.C3Key()
        keyblob = bytearray()
        for slot in ("alphas", "draws", "change_texs"):
            for rec in getattr(keys, slot) or []:
                keyblob += rec
        editable = {
            "tag": tag,
            "name": m.name,
            "label": m.label,
            "unknown0": int(m.unknown0),
            "vertex_count_a": int(m.vertex_count_a),
            "vertex_count_b": int(m.vertex_count_b),
            "face_count_a": int(m.face_count_a),
            "face_count_b": int(m.face_count_b),
            "vertices": verts,
            "faces": [list(f) for f in m.faces],
            "bbox_a": list(m.bbox_a) or list(m.bbox_min),
            "bbox_b": list(m.bbox_b) or list(m.bbox_max),
            "matrix": list(m.matrix),
            "frame_count": int(m.frame_count),
            "key_counts": [len(keys.alphas), len(keys.draws),
                           len(keys.change_texs)],
            # Two FLOATS since M8. This line did not change; what
            # changed is that a human editing a bundle now sees
            # -0.017 where it used to say 3163243414.
            "step": list(m.step) if m.step else None,
            "two_sided": bool(m.two_sided),
            "billboard": int(m.billboard),
            "gap_len": len(m.vertices[0].gap) if (m.vertices
                                                  and m.vertices[0].gap) else 0,
        }
        carry = {
            "name_raw": bytes(m.name_raw),
            "label_raw": bytes(m.label_raw),
            "vertex_gaps": bytes(gaps),
            "key_records": bytes(keyblob),
            "phy5_raw": bytes(m.phy5_raw),
            "tail_raw": bytes(m.tail_raw),
        }
        return editable, carry

    def encode(self, editable, carry, tag):
        m = c3phy.PhyMesh()
        m.tag = editable.get("tag", tag).encode("latin-1")
        m.name = editable["name"]
        m.label = editable["label"]
        m.name_raw = carry["name_raw"]
        m.label_raw = carry["label_raw"]
        m.unknown0 = int(editable["unknown0"])
        m.vertex_count_a = int(editable["vertex_count_a"])
        m.vertex_count_b = int(editable["vertex_count_b"])
        m.face_count_a = int(editable["face_count_a"])
        m.face_count_b = int(editable["face_count_b"])
        glen = int(editable.get("gap_len") or 0)
        gaps = carry["vertex_gaps"]
        if glen and len(gaps) != glen * len(editable["vertices"]):
            raise BundleRefusal(
                f"{len(editable['vertices'])} vertices need "
                f"{glen * len(editable['vertices'])} bytes of carried legacy "
                f"gap, carry holds {len(gaps)}. Adding vertices to a "
                f"PHY /PHY2 mesh needs the gap bytes for the new ones and "
                f"this bundle cannot invent them.")
        for i, rec in enumerate(editable["vertices"]):
            v = c3phy.Vertex()
            for k in _VERT_FLOATS:
                setattr(v, k, float(rec[k]))
            for k in _VERT_INTS:
                setattr(v, k, int(rec[k]))
            v.gap = gaps[i * glen:(i + 1) * glen] if glen else b""
            m.vertices.append(v)
        m.faces = [tuple(int(i) for i in f) for f in editable["faces"]]
        m.bbox_a = tuple(editable["bbox_a"])
        m.bbox_b = tuple(editable["bbox_b"])
        m.bbox_min, m.bbox_max = m.bbox_a, m.bbox_b
        m.matrix = tuple(editable["matrix"])
        m.frame_count = int(editable["frame_count"])
        blob = carry["key_records"]
        na, nd, nc = (int(x) for x in editable["key_counts"])
        need = (na + nd + nc) * 16
        if len(blob) != need:
            raise BundleRefusal(
                f"key_counts {na}/{nd}/{nc} need {need} carried bytes, "
                f"carry holds {len(blob)}")
        it = iter(blob[i:i + 16] for i in range(0, len(blob), 16))
        m.keys = c3phy.C3Key(
            alphas=[next(it) for _ in range(na)],
            draws=[next(it) for _ in range(nd)],
            change_texs=[next(it) for _ in range(nc)])
        # `float()` so a bundle edited by hand -- where JSON may have
        # written `-0.017` as an int-looking 0 or a string -- cannot
        # hand `c3write._f` something it will pack differently.
        m.step = (tuple(float(v) for v in editable["step"])
                  if editable["step"] else None)
        m.two_sided = bool(editable["two_sided"])
        m.billboard = int(editable["billboard"])
        m.phy5_raw = carry["phy5_raw"]
        m.tail_raw = carry["tail_raw"]
        return c3write.serialize_phy(m, tag=m.tag)


# -- MOTI ------------------------------------------------------------------

#: On-disk float width of one bone's key, per encoding.  KKEY and RAW store
#: the 16 matrix floats themselves, so there is nothing to carry; ZKEY stores
#: 7 (quaternion + translation) and XKEY 12 (the 4x3 rows before the
#: 0/0/0/1 column is injected), and BOTH are what `MotionKey.matrices` is a
#: lossy projection of.
_MOTI_SOURCE_WIDTH = {"ZKEY": 7, "XKEY": 12}


class MotiCodec(Codec):
    r"""MOTI chunk body <-> `tools/effects.Motion`.

    Editable: `bone_count`, `frame_count`, `encoding`, `extra_channels`, and
    each key's `frame`.

    Derived: ``keys[].matrices`` -- but *only where the bytes say so*, and
    that is the point of doing this check by re-decoding.  On KKEY and RAW the
    16 matrix floats ARE the disk form, the encoder writes them, the re-decode
    agrees, and an edit is accepted.  On ZKEY and XKEY the encoder writes the
    CARRIED source floats, the re-decode returns what those say, and an edit
    is refused.  One declaration; the corpus decides which behaviour applies.

    Carried verbatim: the ZKEY/XKEY source floats, the `extraChannels` block
    the engine seeks past (`parse_moti` used to DROP this), and anything past
    the modelled end of the chunk.
    """
    key = "moti"
    DERIVED = ("keys[].matrices",)

    def decode(self, body, tag):
        mo = effects.parse_moti(body)
        width = _MOTI_SOURCE_WIDTH.get(mo.encoding, 0)
        src = bytearray()
        for k in mo.keys:
            if width:
                if len(k.source) != mo.bone_count:
                    raise BundleError(
                        f"{mo.encoding} key at frame {k.frame} carries "
                        f"{len(k.source)} source tuples for "
                        f"{mo.bone_count} bones")
                for s in k.source:
                    src += struct.pack(f"<{width}f", *s)
        editable = {
            "bone_count": int(mo.bone_count),
            "frame_count": int(mo.frame_count),
            "encoding": mo.encoding,
            "extra_channels": int(mo.extra_channels),
            "keys": [{"frame": int(k.frame),
                      "matrices": [list(mm) for mm in k.matrices]}
                     for k in mo.keys],
        }
        carry = {
            "source": bytes(src),
            "extra_payload": bytes(mo.extra_payload),
            "trailing": bytes(mo.trailing),
        }
        return editable, carry

    def encode(self, editable, carry, tag):
        enc = editable["encoding"]
        bc = int(editable["bone_count"])
        width = _MOTI_SOURCE_WIDTH.get(enc, 0)
        src = carry["source"]
        need = width * 4 * bc * len(editable["keys"])
        if width and len(src) != need:
            raise BundleRefusal(
                f"{enc} needs {need} carried source bytes for "
                f"{len(editable['keys'])} keys x {bc} bones, carry holds "
                f"{len(src)}. The on-disk {enc} form is NOT recoverable from "
                f"the matrices -- see effects.serialize_moti.")
        keys, at = [], 0
        for kd in editable["keys"]:
            mats = [tuple(float(x) for x in mm) for mm in kd["matrices"]]
            source = []
            for _ in range(bc if width else 0):
                source.append(struct.unpack_from(f"<{width}f", src, at))
                at += width * 4
            keys.append(effects.MotionKey(int(kd["frame"]), mats, source))
        mo = effects.Motion(
            bc, int(editable["frame_count"]), enc, keys,
            int(editable["extra_channels"]), 0, 0,
            carry["extra_payload"], carry["trailing"])
        return effects.serialize_moti(mo)


# -- RSDB ------------------------------------------------------------------

def _rsdb_span(d: bytes, pos: int, n: int, max_path: int) -> Optional[dict]:
    """`_rsdb_walk`'s answer WITHOUT building the rows -- the same validation,
    none of the decoding.

    `regions()` needs the stride and the section end; it does not need a
    million decoded path strings, and building them made the walk over 30
    clients cost 111 s. Every acceptance rule below is the one `_rsdb_walk`
    applies, in the same order, so the two cannot disagree about whether a
    stride resolves -- `tests/test_editbundle_roundtrip.py` asserts they
    return the same stride and end on every section it visits, which is the
    check that stops this from becoming a second, looser description.
    """
    for stride in (8, 12):
        row_end = pos + 8 + n * stride
        if row_end > len(d):
            continue
        nu = stride // 4
        ok = True
        blob_end = row_end
        for i in range(n):
            soff = struct.unpack_from("<%dI" % nu, d, pos + 8 + i * stride)[-1]
            at = pos + soff
            if not (row_end <= at < len(d)):
                ok = False
                break
            end = d.find(b"\x00", at)
            if end < 0 or end - at > max_path or end == at:
                ok = False
                break
            if _has_control(d, at, end):
                ok = False
                break
            if end + 1 > blob_end:
                blob_end = end + 1
        if ok and n:
            return {"stride": stride, "row_end": row_end, "end": blob_end}
    return None


def _has_control(d: bytes, at: int, end: int) -> bool:
    """A path may hold HIGH bytes (real GBK vendor data) but never a CONTROL
    byte.  The 2026-09-05 correction in `core/wdb.py`, restated: requiring
    printable ASCII killed a 9,567-row table over one full-width hyphen."""
    seg = d[at:end]
    return any(c < 32 or c == 127 for c in seg)


def _rsdb_walk(d: bytes, pos: int, n: int, max_path: int) -> Optional[dict]:
    """One RSDB/RSDC section at `pos`, or None if no stride resolves it.

    The stride is DETECTED, not assumed -- 8 for ``{id, stringOffset}`` and 12
    for ``{id, extra, stringOffset}`` -- and a table is accepted whole or not
    at all.  Both rules are `core/wdb.py`'s, restated here because this module
    needs the ROW VALUES (which `ResourceDb` does not expose per section) and
    not just the span.  `tests/test_editbundle_roundtrip.py` asserts this walk
    lands on exactly the same section boundaries as `wdb.ResourceDb`, on every
    shipped client, so the two descriptions cannot drift apart in silence.

    A string offset is relative to the SECTION's start, not the file's.

    Returns COLUMNS, not row objects: the largest section on this corpus
    declares 1,043,138 rows and a dict per row is both the memory and most of
    the time.
    """
    for stride in (8, 12):
        row_end = pos + 8 + n * stride
        if row_end > len(d):
            continue
        ids: list = []
        extras: list = []
        offsets: list = []
        paths: list = []
        blob_end = row_end
        ok = True
        nu = stride // 4
        wide = nu > 2
        for i in range(n):
            fields = struct.unpack_from("<%dI" % nu, d, pos + 8 + i * stride)
            soff = fields[-1]
            at = pos + soff
            if not (row_end <= at < len(d)):
                ok = False
                break
            end = d.find(b"\x00", at)
            if end < 0 or end - at > max_path:
                ok = False
                break
            raw = d[at:end]
            if not raw or any(c < 32 or c == 127 for c in raw):
                ok = False
                break
            ids.append(fields[0])
            if wide:
                extras.append(list(fields[1:-1]))
            offsets.append(soff)
            paths.append(raw.decode("latin-1"))
            if end + 1 > blob_end:
                blob_end = end + 1
        if ok and ids:
            return {"stride": stride, "ids": ids, "extras": extras,
                    "offsets": offsets, "paths": paths,
                    "row_end": row_end, "end": blob_end}
    return None


class RsdbCodec(Codec):
    r"""An RSDB / RSDC section of ``ini/c3.wdb`` -- the asset id -> path index.

    Editable: each row's ``id``, its ``extra`` fields (stride-12 tables only;
    meaning unestablished, preserved not interpreted) and its ``path``.
    Rows may be added or removed.

    Derived: nothing.

    Carried verbatim: the string blob, byte for byte, and each row's string
    OFFSET.  The blob is carried rather than rebuilt because rebuilding it
    would have to guess at a layout the file does not state -- shared
    substrings, ordering, any bytes no row points at.  Editing works by
    APPENDING: a path whose bytes changed is written at the end of the blob
    and its row repointed, which is always legal because an offset is
    section-relative and nothing else indexes into the blob.  The old bytes
    stay where they were; a section only ever grows.

    Adding or removing rows shifts the whole blob, because the row table sits
    in front of it.  Every offset is rebased by that delta -- again legal for
    the same reason.

    THE PAYLOAD IS COLUMNAR (``ids`` / ``extra`` / ``paths`` as parallel
    lists) rather than a list of row objects, and that is a size decision, not
    a taste one: the largest section on this corpus declares **1,043,138 rows**
    (7878), and a row-object payload spends most of its bytes and most of its
    encode time repeating three key names a million times.  MEASURED: the
    columnar form cut the export of 7878's row tables from 76 s to the figure
    in `docs/edit_bundle_2026-09-06.md`.
    """
    key = "rsdb"
    DERIVED = ()

    def decode(self, body, tag):
        (n,) = struct.unpack_from("<I", body, 4)
        got = _rsdb_walk(body, 0, n, WdbContainer.MAX_PATH)
        if got is None:
            raise BundleError(f"{tag}: {n} rows resolve at neither stride")
        if got["end"] != len(body):
            raise BundleError(
                f"{tag}: walk ends at {got['end']}, section is "
                f"{len(body)} bytes")
        editable = {
            "tag": tag,
            "stride": got["stride"],
            "ids": got["ids"],
            "extra": got["extras"],
            "paths": got["paths"],
        }
        carry = {
            "blob": bytes(body[got["row_end"]:]),
            "offsets": struct.pack("<%dI" % len(got["offsets"]),
                                   *got["offsets"]),
        }
        return editable, carry

    def encode(self, editable, carry, tag):
        stride = int(editable["stride"])
        ids, paths = editable["ids"], editable["paths"]
        extra = editable["extra"] or [[]] * len(ids)
        if not (len(ids) == len(paths) == len(extra)):
            raise BundleRefusal(
                f"columnar payload is ragged: {len(ids)} ids, {len(paths)} "
                f"paths, {len(extra)} extra")
        old_off = struct.unpack("<%dI" % (len(carry["offsets"]) // 4),
                                carry["offsets"])
        if len(old_off) != len(ids):
            # rows added or removed: the carried offsets no longer line up
            # one-to-one, so every surviving row must say which one it kept.
            raise BundleRefusal(
                f"{len(ids)} rows in the payload but {len(old_off)} carried "
                f"offsets. Adding or removing rows needs each surviving row "
                f"to name its original offset; this bundle version does not "
                f"carry that mapping.")
        blob = bytearray(carry["blob"])
        old_base = _rsdb_base(len(old_off), stride)
        new_base = _rsdb_base(len(ids), stride)
        shift = new_base - old_base
        offsets = []
        for path, off in zip(paths, old_off):
            want = path.encode("latin-1")
            at = off - old_base
            end = blob.find(b"\x00", at) if 0 <= at < len(blob) else -1
            if end >= 0 and blob[at:end] == want:
                offsets.append(off + shift)
            else:
                # APPEND, never overwrite: another row may point at `at`, and
                # a new path is rarely the same length as the old one.
                offsets.append(new_base + len(blob))
                blob += want + b"\x00"
        out = bytearray(editable["tag"].encode("latin-1")[:4])
        out += struct.pack("<I", len(ids))
        nu = stride // 4
        if nu == 2:
            packer = struct.Struct("<2I")
            for ident, off in zip(ids, offsets):
                out += packer.pack(ident, off)
        else:
            for ident, e, off in zip(ids, extra, offsets):
                fields = [int(ident)] + [int(x) for x in e] + [int(off)]
                if len(fields) != nu:
                    raise BundleRefusal(
                        f"row id {ident} has {len(fields)} fields, stride "
                        f"{stride} wants {nu}")
                out += struct.pack("<%dI" % nu, *fields)
        out += blob
        return bytes(out)


def _rsdb_base(n_rows: int, stride: int) -> int:
    """Section-relative offset of the first string byte."""
    return 8 + n_rows * stride


CODECS: dict = {c.key: c() for c in (OpaqueCodec, PhyCodec, MotiCodec,
                                     RsdbCodec)}


# ==========================================================================
# derived-field enforcement
# ==========================================================================

def _pluck(doc, path: str) -> list:
    """Values at `path`.  Supports ``a.b`` and one ``list[].field`` hop."""
    head, _, tail = path.partition("[].")
    if not tail:
        cur = doc
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return []
            cur = cur[part]
        return [cur]
    node = doc
    for part in head.split("."):
        if not isinstance(node, dict) or part not in node:
            return []
        node = node[part]
    out = []
    for item in node or []:
        out.extend(_pluck(item, tail))
    return out


def _f32(v):
    """`v` as the nearest float32, the storage precision of every float here.

    THIS IS WHY THE DERIVED CHECK IS NOT A NAIVE EQUALITY, and the first draft
    got it wrong in the direction that looks safe. A payload edits a matrix
    element to `5.1`; the encoder writes it as an f32; the re-decode reads back
    5.099999904632568. Compared as doubles those differ, so a KKEY chunk --
    where the matrices ARE the disk form and the edit was written correctly --
    was REFUSED as if its matrices were derived. Every codec here stores its
    floats as f32, so the comparison is made in f32 and the refusal then means
    what it says: not "your edit was rounded" but "your edit is not in the
    bytes at all".

    An edit smaller than one f32 ULP is silently accepted and does not reach
    the disk. That is not a drop this module can prevent: such a value has no
    on-disk representation to be dropped from.
    """
    if isinstance(v, bool) or not isinstance(v, float):
        return v
    try:
        return struct.unpack("<f", struct.pack("<f", v))[0]
    except (OverflowError, ValueError):                     # pragma: no cover
        return v


def _norm(v):
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return _f32(v)


def _check_derived(codec: Codec, editable: dict, produced: bytes,
                   tag: str) -> None:
    """Re-decode what the encoder produced and compare the DERIVED paths.

    THE CHECK IS DELIBERATELY NOT "did the codec use the carry".  It is "does
    the payload's claim survive contact with the bytes", which is a stronger
    statement and needs no second description of the format: whatever the
    encoder actually wrote, the decoder reads it back, and any derived value
    the payload asserted that the bytes do not support is a REFUSAL.

    That is why the ZKEY/KKEY asymmetry needs no branch anywhere in this file.
    """
    if not codec.DERIVED:
        return
    back, _ = codec.decode(produced, tag)
    for path in codec.DERIVED:
        want = _norm(_pluck(back, path))
        got = _norm(_pluck(editable, path))
        if want != got:
            raise BundleRefusal(
                f"{tag}: the payload changed {path!r}, which is DERIVED from "
                f"bytes this bundle carries verbatim. Writing it would "
                f"either be ignored (a silent drop) or change bytes the "
                f"editor never saw. The bundle refuses instead. "
                f"(payload has {_short(got)}, the carried source says "
                f"{_short(want)})")


def _short(v) -> str:
    s = repr(v)
    return s if len(s) <= 120 else s[:117] + "..."


# ==========================================================================
# the bundle
# ==========================================================================

@dataclass
class EditBundle:
    """manifest + payload + carry, in memory.

    `payload` is JSON bytes -- the file a tool opens.  `carry` is one binary
    blob addressed by ``manifest["carry"]["blocks"]``, so a multi-megabyte
    verbatim block never becomes base64 in a JSON document.
    """
    manifest: dict
    payload: bytes
    carry: bytes

    MANIFEST_NAME = "manifest.json"
    PAYLOAD_NAME = "payload.json"
    CARRY_NAME = "carry.bin"

    @property
    def editable(self) -> dict:
        return json.loads(self.payload.decode("utf-8"))["editable"]

    def carry_blocks(self) -> dict:
        out = {}
        for name, (off, ln) in self.manifest["carry"]["blocks"].items():
            out[name] = self.carry[off:off + ln]
        return out

    def write_dir(self, path) -> Path:
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        (d / self.MANIFEST_NAME).write_text(
            json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")
        (d / self.PAYLOAD_NAME).write_bytes(self.payload)
        (d / self.CARRY_NAME).write_bytes(self.carry)
        return d

    @classmethod
    def read_dir(cls, path) -> "EditBundle":
        d = Path(path)
        man = json.loads((d / cls.MANIFEST_NAME).read_text(encoding="utf-8"))
        payload = (d / cls.PAYLOAD_NAME).read_bytes()
        carry = (d / cls.CARRY_NAME).read_bytes()
        b = cls(man, payload, carry)
        b.self_check()
        return b

    def self_check(self) -> None:
        """The bundle's three files must describe each other.

        Checked on every read, because the failure this prevents -- a payload
        edited beside a stale manifest -- looks exactly like a successful
        import until the client renders it.
        """
        m = self.manifest
        if m.get("bundle") != BUNDLE_VERSION:
            raise BundleRefusal(
                f"manifest says bundle version {m.get('bundle')!r}, this is "
                f"version {BUNDLE_VERSION}")
        if _sha(self.carry) != m["carry"]["sha256"]:
            raise BundleRefusal("carry.bin does not match the manifest hash")
        if len(self.carry) != m["carry"]["size"]:
            raise BundleRefusal("carry.bin length does not match the manifest")
        # payload.json is the ONE file a tool is meant to change, so its hash
        # is recorded and reported, never enforced.
        doc = json.loads(self.payload.decode("utf-8"))
        if doc.get("editbundle_payload") != PAYLOAD_VERSION:
            raise BundleRefusal("payload.json is not an editbundle payload")
        if doc.get("format") != m["region"]["codec"]:
            raise BundleRefusal(
                f"payload says format {doc.get('format')!r}, manifest says "
                f"{m['region']['codec']!r}")

    @property
    def payload_edited(self) -> bool:
        return _sha(self.payload) != self.manifest["payload"]["sha256"]


def _reuse(container: Optional[Container], data: bytes) -> Container:
    """`container` if it is the walk of exactly THESE bytes, else a fresh walk.

    The guard is `is`, not `==`, and that is deliberate: `==` on a 20 MB
    `c3.wdb` costs a full memcmp, and the only thing this optimisation is
    allowed to skip is a walk it can prove is the same one. A caller that
    passes a container for different bytes gets a correct answer and pays for
    the walk.

    Why it exists: walking a `c3.wdb` costs 0.4-4 s (the RSDB stride detection
    validates every row), and `verify_asset` called `open_container` twice per
    region -- 68 walks of one file. MEASURED: this is most of the wall clock
    on the wdb half of `tests/test_editbundle_roundtrip.py`.
    """
    if container is not None and container.data is data:
        return container
    return open_container(data)


def export_region(data: bytes, index: int, *, logical: str = "",
                  root=None, stamp: Optional[dict] = None,
                  container: Optional[Container] = None) -> EditBundle:
    """Region `index` of `data` as an edit bundle.

    `root` (or a precomputed `stamp`) records WHICH INSTALL this came from.
    `coroot.base_id` hashes ~37 MB of `ini/`, so a caller exporting in a loop
    should compute one stamp with `provenance.stamp` and pass it in -- exactly
    the accommodation `provenance.stamp` already makes for `base`.
    """
    con = _reuse(container, data)
    regions = con.regions()
    if not (0 <= index < len(regions)):
        raise BundleError(
            f"region {index} out of range; the container has {len(regions)}")
    region = regions[index]
    body = con.body(region)
    codec = CODECS[region.codec]
    editable, blocks = codec.decode(body, region.tag)

    carry = bytearray()
    block_map = {}
    for name in sorted(blocks):
        blob = blocks[name]
        block_map[name] = [len(carry), len(blob)]
        carry += blob

    payload_doc = {
        "editbundle_payload": PAYLOAD_VERSION,
        "format": region.codec,
        "tag": region.tag,
        #: The paths a tool MUST NOT change.  Present in the payload so a tool
        #: can grey them out rather than discover the refusal at import time.
        "derived": list(codec.DERIVED),
        "editable": editable,
    }
    # allow_nan=False ON PURPOSE: a NaN or an infinity would round-trip
    # through JSON as a non-standard token and back through struct.pack as a
    # CANONICAL NaN, i.e. different bytes with no error anywhere. Refusing is
    # the only honest option, and MEASURED on this corpus it never fires.
    payload = json.dumps(payload_doc, allow_nan=False).encode("utf-8")

    if stamp is None and root is not None:
        import provenance                                       # noqa: E402
        stamp = provenance.stamp(root, tool=TOOL)

    manifest = {
        "bundle": BUNDLE_VERSION,
        "tool": TOOL,
        "provenance": stamp,
        "asset": {
            "logical": logical,
            "container": con.kind,
            "size": len(data),
            "sha256": _sha(data),
            "regions": len(regions),
        },
        "region": {
            "index": region.index,
            "tag": region.tag,
            "codec": region.codec,
            "offset": region.offset,
            "length": region.length,
            "sha256": _sha(body),
        },
        "payload": {
            "media_type": "application/json",
            "size": len(payload),
            "sha256": _sha(payload),
        },
        "carry": {
            "size": len(carry),
            "sha256": _sha(bytes(carry)),
            "blocks": block_map,
        },
        "derived": list(codec.DERIVED),
    }
    if con.kind == "wdb":
        manifest["asset"]["header_exact"] = con.header_exact
    return EditBundle(manifest, payload, bytes(carry))


def import_region(bundle: EditBundle, original: bytes, *,
                  container: Optional[Container] = None) -> bytes:
    """New asset bytes: `original` with the bundle's region rewritten.

    `original` is the CURRENT bytes on disk, and every claim the manifest makes
    about it is checked before anything is written.  A bundle exported from a
    different install, or from a file that has since changed, is REFUSED --
    the alternative is splicing a region into an offset that now means
    something else, which does not fail, it produces a plausible file.
    """
    bundle.self_check()
    m = bundle.manifest
    a, r = m["asset"], m["region"]

    if len(original) != a["size"]:
        raise BundleRefusal(
            f"the original is {len(original)} bytes, the manifest was taken "
            f"from a {a['size']}-byte file")
    if _sha(original) != a["sha256"]:
        raise BundleRefusal(
            "the original's sha256 does not match the manifest. This bundle "
            "was exported from different bytes -- another install, another "
            "patch level, or a file edited since. Re-export.")

    con = _reuse(container, original)
    if con.kind != a["container"]:
        raise BundleRefusal(
            f"the original is a {con.kind!r} container, the manifest says "
            f"{a['container']!r}")
    regions = con.regions()
    if len(regions) != a["regions"]:
        raise BundleRefusal(
            f"the original has {len(regions)} regions, the manifest counted "
            f"{a['regions']}")
    region = regions[r["index"]]
    if (region.offset != r["offset"] or region.length != r["length"]
            or region.tag != r["tag"]):
        raise BundleRefusal(
            f"region {r['index']} is now {region.describe()}, the manifest "
            f"recorded {r['tag']} @0x{r['offset']:X} +{r['length']}")
    if _sha(con.body(region)) != r["sha256"]:
        raise BundleRefusal(
            f"region {r['index']} no longer hashes to what the manifest "
            f"recorded")

    codec = CODECS[r["codec"]]
    doc = json.loads(bundle.payload.decode("utf-8"))
    editable = doc["editable"]
    new_body = codec.encode(editable, bundle.carry_blocks(), region.tag)
    _check_derived(codec, editable, new_body, region.tag)
    return con.splice(region, new_body)


def one_per_shape(regions: list) -> list:
    """The SMALLEST region of each distinct (tag, codec) pair.

    A `c3.wdb` holds up to 34 sections and several of its RSDB tables declare
    hundreds of thousands of rows apiece; verifying every one of them re-proves
    the same codec on the same shape at a cost measured in minutes.  This is
    the selector a coverage-scoped sweep uses, and the claim it supports is
    "every section SHAPE round-trips", not "every section does" -- which is
    why the caller has to ask for it by name and `--all-wdb` exists.
    """
    best: dict = {}
    for r in regions:
        key = (r.tag, r.codec)
        if key not in best or r.length < best[key].length:
            best[key] = r
    return sorted(best.values(), key=lambda r: r.index)


def verify_asset(data: bytes, *, logical: str = "",
                 limit: Optional[int] = None, select=None) -> dict:
    """THE GATE, for one asset: export every region, import it unchanged, and
    require the result to be byte-identical to what we started with.

    Returns a report rather than raising, so a corpus sweep can COUNT.  The
    counts are split by codec and `opaque` is its own key: a run whose regions
    were all opaque has verified the container walk and the splice, and has
    verified no codec at all.

    `select` narrows which regions are checked (see `one_per_shape`).  The
    report says how many were SKIPPED so a narrowed run cannot read as a full
    one.
    """
    rep = {"regions": 0, "exact": 0, "skipped": 0, "by_codec": {},
           "problems": [], "container": "?"}
    try:
        con = open_container(data)
        regions = con.regions()
    except Exception as e:                                      # noqa: BLE001
        rep["problems"].append(f"{logical}: container: {e!r}")
        return rep
    rep["container"] = con.kind
    if con.kind == "wdb" and con.walk_stopped:
        rep["problems"].append(f"{logical}: walk stopped: {con.walk_stopped}")
    if select is not None:
        chosen = select(regions)
        rep["skipped"] = len(regions) - len(chosen)
        regions = chosen
    for region in regions[:limit] if limit else regions:
        rep["regions"] += 1
        slot = rep["by_codec"].setdefault(region.codec, {"seen": 0, "exact": 0})
        slot["seen"] += 1
        try:
            b = export_region(data, region.index, logical=logical,
                              container=con)
            got = import_region(b, data, container=con)
        except Exception as e:                                  # noqa: BLE001
            rep["problems"].append(
                f"{logical} {region.describe()}: {e.__class__.__name__}: {e}")
            continue
        if got == data:
            rep["exact"] += 1
            slot["exact"] += 1
        else:
            n = min(len(got), len(data))
            where = next((i for i in range(n) if got[i] != data[i]), n)
            rep["problems"].append(
                f"{logical} {region.describe()}: NOT byte-identical -- "
                f"len {len(got)}!={len(data)} first diff at 0x{where:X}")
    return rep


# ==========================================================================
# CLI
# ==========================================================================

def _read(p) -> bytes:
    return Path(p).read_bytes()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("regions", help="list an asset's replaceable regions")
    p.add_argument("asset")

    p = sub.add_parser("export", help="write an edit bundle for one region")
    p.add_argument("asset")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--logical", default="")
    p.add_argument("--root", default=None,
                   help="install root, to stamp the manifest with its base_id")

    p = sub.add_parser("import", help="apply an edit bundle to the original")
    p.add_argument("bundle")
    p.add_argument("--original", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("verify", help="export+import every region, byte-compare")
    p.add_argument("asset", nargs="+")

    a = ap.parse_args(argv)

    if a.cmd == "regions":
        data = _read(a.asset)
        con = open_container(data)
        rs = con.regions()
        print(f"{Path(a.asset).name}: {con.kind} container, {len(rs)} region(s)")
        if con.kind == "wdb" and con.walk_stopped:
            print(f"  WALK STOPPED: {con.walk_stopped}")
        for r in rs:
            print("  " + r.describe())
        return 0

    if a.cmd == "export":
        data = _read(a.asset)
        b = export_region(data, a.index, logical=a.logical or Path(a.asset).name,
                          root=a.root)
        d = b.write_dir(a.out)
        print(f"exported region {a.index} -> {d}")
        print(f"  payload {len(b.payload)} bytes, carry {len(b.carry)} bytes")
        print(f"  derived (read-only): {b.manifest['derived'] or 'none'}")
        return 0

    if a.cmd == "import":
        b = EditBundle.read_dir(a.bundle)
        orig = _read(a.original)
        out = import_region(b, orig)
        Path(a.out).write_bytes(out)
        same = "IDENTICAL to the original" if out == orig else \
               f"{len(out)} bytes ({len(out) - len(orig):+d})"
        print(f"wrote {a.out}: {same}")
        return 0

    if a.cmd == "verify":
        bad = 0
        for f in a.asset:
            rep = verify_asset(_read(f), logical=Path(f).name)
            cols = " ".join(f"{k}={v['exact']}/{v['seen']}"
                            for k, v in sorted(rep["by_codec"].items()))
            ok = rep["exact"] == rep["regions"] and not rep["problems"]
            print(f"{'OK  ' if ok else 'FAIL'} {Path(f).name}: "
                  f"{rep['exact']}/{rep['regions']} regions byte-identical "
                  f"[{rep['container']}]  {cols}")
            for pr in rep["problems"]:
                print(f"       {pr}")
            bad += 0 if ok else 1
        return 1 if bad else 0

    return 2                                                # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
