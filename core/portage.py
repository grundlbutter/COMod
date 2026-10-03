#!/usr/bin/env python3
r"""portage.py -- the export/import FOUNDATION for COMod (backlog item 5).

Two verbs COMod did not have, and one manifest that makes the round trip safe:

    export   put an asset where the right program opens it -- a `.c3` as-is for
             Blender, a `.dds` as PNG for an image editor, a table as CSV, an
             `.ani` as its ordered frame set -- and record what it was.
    import   read the manifest, ENFORCE the per-format rules, and land the
             result in the stage tree so `diff` / `impact` / `install` still
             apply.

This module is the machinery; `tools/comod.py`'s `export` / `import` verbs are
its command line, and the pop-out in backlog item 6 is its UI. It dispatches
over the pieces that already exist (`extract`, `import-png`, `stage`) rather
than reinventing them; what it adds is one dispatching pair with the format
rules attached, and a manifest that carries enough to reimport WITHOUT guessing.

THE THREE RULES THAT LIVE HERE, NOT IN THE USER'S HEAD
------------------------------------------------------
1. A DDS re-encode is LOSSY (`w`, not `W`): an untouched texture does NOT come
   back byte-identical. So "modified?" must NOT be a byte comparison of the
   re-encoded DDS against the original. The manifest records the hash of the
   EXPORTED working file (the .png), and import compares the working file to
   that -- an untouched file is SKIPPED and never re-encoded. See
   `dds_untouched`.
2. A `.dat` edit on the block96 families (6907-7878) must PRESERVE LENGTH: the
   cipher is 12-byte ECB cut at fixed offsets, so a field that changes byte
   length re-cuts every later block and `datdict.cmd_encode` FAILS CLOSED on
   it. A spreadsheet turns `100` into `1000`; import REFUSES the row here,
   before any encode, with a message naming the row -- friendlier than the
   downstream "uncovered block at offset N". See `length_violations`.
3. An `.ani` frame list is ORDERED and the order is the animation. A renumber
   inconsistent with `FrameAmount`, or a gap in `Frame0..Frame{N-1}`, is a
   DIFFERENT animation, not a broken one -- a silent wrong answer. Import
   validates the reassembled manifest against its own `FrameAmount`. See
   `validate_ani`.

THE MANIFEST (`comod-manifest.json`)
------------------------------------
A JSON ARRAY, so it diffs and hand-inspects (consistent with the tag-layer
decision in item 5). Element 0 is the batch record (`record":"batch"`) carrying
provenance; every other element is a file record (`record":"file"`). Per file:

    dest          logical destination, forward slashes -- where import lands it
    type          c3 / dds / table / ani / audio / other
    transform     copy / dds-png / table-csv / ani-frames (import inverts this)
    export        path of the exported working file INSIDE the zip/workdir
    original_sha  sha256 of the ORIGINAL asset bytes in the source install
    original_len  byte length of that original, AS IT SITS ON DISK
    original_plain_len
                  a decoded table's PLAINTEXT length; None for every other
                  record. NOT the same number as `original_len` on a block96
                  family -- 7205 `ini/item_refine_cost.dat` is 66 bytes
                  encrypted and 62 decoded -- and the length rule wants THIS
                  one, because it length-checks the edited plaintext. See
                  `table_plaintext` for how it is derived and
                  `_recorded_length_violation` for the rule that reads it.
                  A bundle exported before this field existed carries only
                  `original_len`; that path still works and warns, and is the
                  one case the fix cannot make exact.
    export_sha    sha256 of the exported working file (edit detection reads this)
    shared        True when this file appears under more than one asset
    absent        True for a declared-but-absent satellite (no file to export)
    export_refusal
                  why a file that IS present got no export (a block96 table
                  that would not decode). None otherwise. `export` is None in
                  that case and `absent` stays False: the file is there, it
                  just could not be turned into anything editable. Exporting
                  the ciphertext instead is the bug this key exists to make
                  visible rather than plausible.

A THIRD RECORD KIND, BECAUSE NOT EVERY SUBJECT IS A FILE (`record":"definition"`).
An EFFECT's definition is one ROW of `3DEffect` -- the compiled `3DEffect.dbc`
on 5517/6090/6609/7205, the plaintext `.ini` where no twin exists -- and a
bundle that carried only its meshes and textures would arrive with the art
present and the effect UNDEFINED. So the row travels as a row: `dest` names the
file that ANSWERED, `form` is `dbc`/`ini`, and the exported JSON holds the
decoded record. On import the row is merged into the TARGET's own table, never
shipped over it. See `DefinitionItem` and `_import_definition`.

Provenance is a COMPATIBILITY CHECK, not decoration: a batch bound for another
machine on the same client family (mod staging for a private server) must
refuse to apply to a family MISMATCH, and must warn when a target's "original"
file does not hash to what the manifest recorded (that install is already
modified). Cross-client-family (a 7205 zip into a 6609 tree) is OUT of scope and
is refused, not attempted.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# The manifest's own schema version. Bumped when a field's MEANING changes, so
# a newer import can tell an old zip from a malformed one.
MANIFEST_SCHEMA = 1

# What COMod version wrote a batch. Recorded for provenance; not a gate today.
COMOD_VERSION = "1"

MANIFEST_NAME = "comod-manifest.json"
README_NAME = "README.txt"

# ---------------------------------------------------------------------------
# asset type <-> transform <-> tool
# ---------------------------------------------------------------------------
# BROWSE by meaning, DISPATCH by format (asset_categories_and_tools §0). The
# transform is chosen by the FILE, never by the tool the user named -- `--for`
# only validates that the tool actually opens this kind of file.

# Transform names. `copy` means the file leaves and returns unchanged (Blender
# opens `.c3` natively through our addon; audio needs no bridge).
T_COPY = "copy"
T_DDS_PNG = "dds-png"
T_TABLE_CSV = "table-csv"
T_ANI_FRAMES = "ani-frames"
#: A SUBJECT THAT IS NOT A FILE. An effect's definition is one ROW of
#: `3DEffect` -- `3DEffect.dbc` on 5517/6090/6609/7205, the `.ini` beside it
#: where there is no compiled twin -- and the row travels as a row. See
#: `DefinitionItem` for why that is the only honest shape available.
T_TABLE_ROW = "table-row"

# Asset type by extension. Deliberately extension-driven and small: a body
# mesh, a monster mesh and an effect mesh are all `.c3` -> Blender, and the
# grid that separates them is the browse tree's job (item 6), not this table's.
_TYPE_BY_EXT = {
    ".c3": "c3",
    ".dds": "dds",
    ".ani": "ani",
    ".dat": "table",
    ".wav": "audio",
    ".mp3": "audio",
}

_TRANSFORM_BY_TYPE = {
    "c3": T_COPY,
    "dds": T_DDS_PNG,
    "table": T_TABLE_CSV,
    "ani": T_ANI_FRAMES,
    "audio": T_COPY,
    "other": T_COPY,
}

# The tool for each format, from asset_categories_and_tools_2026-09-06 §2.
# `_TOOL_TYPES` is the inverse used to validate `--for`: which types a tool
# actually opens. A `--for blender` on a `.dds` is a mismatch we name rather
# than silently exporting the wrong transform.
_DEFAULT_TOOL = {
    "c3": "blender",
    "dds": "gimp",
    "table": "calc",
    "ani": "text",
    "audio": "audacity",
    "other": "text",
}
_TOOL_TYPES = {
    "blender": {"c3"},
    "gimp": {"dds"},
    "paint.net": {"dds"},
    "krita": {"dds"},
    "calc": {"table"},
    "excel": {"table"},
    "libreoffice": {"table"},
    "audacity": {"audio"},
    "text": {"ani", "table", "other"},
    "vscode": {"ani", "table", "other"},
}

# The block96 `.dat` era -- the families whose tables re-encode length-
# preservingly and therefore CANNOT change a field's byte length. From the
# capability matrix: `rwI` on 6907+, byte-safe `RWI` at 6868 and below. The
# range the owner named for the rule is 6907-7878 inclusive.
LENGTH_CONSTRAINED_LOW = 6907
LENGTH_CONSTRAINED_HIGH = 7878

# The field delimiter inside a decoded block96 table row (`block96.recover_rows`
# splits on this). CSV columns are the fields between these markers.
TABLE_FIELD_DELIM = "@@"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(logical: str) -> str:
    """Asset type for a logical path, by extension. Unknown -> ``"other"``."""
    return _TYPE_BY_EXT.get(Path(logical).suffix.lower(), "other")


def transform_for(asset_type: str) -> str:
    return _TRANSFORM_BY_TYPE.get(asset_type, T_COPY)


def default_tool(asset_type: str) -> str:
    return _DEFAULT_TOOL.get(asset_type, "text")


def tool_mismatch(tool: str, asset_type: str) -> Optional[str]:
    """Why ``tool`` is the wrong program for ``asset_type``, or None.

    Returns None for an unknown tool: a private setup may use an editor we do
    not enumerate, and refusing it would be worse than silence. The check
    exists to catch the confident-but-wrong case (`--for blender` on a texture),
    not to police the tool list.
    """
    types = _TOOL_TYPES.get(tool.lower())
    if types is None:
        return None
    if asset_type in types:
        return None
    opens = ", ".join(sorted(types))
    return (f"--for {tool} opens {opens}, not a {asset_type} asset; "
            f"the tool for {asset_type} is {default_tool(asset_type)}")


def _client_version(client_family: str) -> Optional[int]:
    """The numeric patch of a plugin name (``patch7878`` -> 7878), or None.

    None for CCO / Zephyr / a private repack whose name carries no patch
    number -- those are not in the length-constrained range by construction,
    and the caller treats a None as "not constrained".
    """
    m = re.search(r"(\d{4,})", client_family or "")
    return int(m.group(1)) if m else None


def is_length_constrained(client_family: str) -> bool:
    """Do this family's `.dat` tables re-encode length-preservingly (rule 2)?"""
    v = _client_version(client_family)
    return v is not None and LENGTH_CONSTRAINED_LOW <= v <= LENGTH_CONSTRAINED_HIGH


# ---------------------------------------------------------------------------
# the three format rules, as pure functions
# ---------------------------------------------------------------------------

def dds_untouched(entry: dict, working_bytes: bytes) -> bool:
    r"""RULE 1. Was the exported working file left UNtouched?

    True when the working file still hashes to what export wrote, so import
    must SKIP it and never re-encode -- a lossy DDS round trip on an untouched
    texture would otherwise rewrite it for no reason. This is a hash comparison
    of the WORKING file against `export_sha`, NOT a byte comparison of a
    re-encoded DDS against the original: the latter is always "different"
    because the encode is lossy, which is exactly the trap the rule names.
    """
    exp = entry.get("export_sha")
    return bool(exp) and sha256_hex(working_bytes) == exp


def _split_rows(plaintext: bytes) -> list[bytes]:
    r"""A decoded table's rows. Split on newline, keeping no trailing empty.

    Bytes, not text: a table's fields are latin-1-ish and the length rule is
    about BYTES, so decoding to `str` and back could change a length without
    the guard seeing it.
    """
    body = plaintext.replace(b"\r\n", b"\n")
    rows = body.split(b"\n")
    if rows and rows[-1] == b"":
        rows.pop()
    return rows


def length_violations(original: bytes, edited: bytes,
                      constrained: bool) -> list[str]:
    r"""RULE 2. Which rows changed BYTE LENGTH on a length-constrained family?

    Returns a human list of offending rows (empty when safe, or when the family
    is not constrained). The comparison is per row and by byte length, because
    that is the unit `datdict.cmd_encode` fails closed on: a same-length value
    edit re-encodes in place, a length change re-cuts every later block. Rows
    added or removed also change the file length, so a row-count change is
    itself a violation on a constrained family.

    This is the FRIENDLY pre-check. The downstream encoder fails closed too, so
    a bug here cannot silently ship a corrupt table -- but a message naming the
    row is worth more to a modder than an offset into ciphertext.
    """
    if not constrained:
        return []
    orig_rows = _split_rows(original)
    new_rows = _split_rows(edited)
    out: list[str] = []
    if len(orig_rows) != len(new_rows):
        out.append(
            f"row COUNT changed {len(orig_rows)} -> {len(new_rows)}: a "
            f"length-preserving table cannot gain or lose rows (the cipher "
            f"re-cuts every later block). Edit values in place only.")
        # Still report per-row length changes for the rows that line up.
    for i, (o, n) in enumerate(zip(orig_rows, new_rows)):
        if len(o) != len(n):
            preview = n.decode("latin-1", "replace")[:48]
            out.append(
                f"row {i}: {len(o)} -> {len(n)} bytes. A spreadsheet turning "
                f"`100` into `1000` is the usual cause; pad the field back to "
                f"{len(o)} bytes. New row starts: {preview!r}")
    return out


@dataclass
class AniSection:
    name: str
    frame_amount: Optional[int]      #: the declared FrameAmount, or None if absent
    frames: dict[int, str]           #: index -> logical frame path, as written
    order: list[str] = field(default_factory=list)  #: keys in file order


def parse_ani(text: str) -> list[AniSection]:
    r"""Parse an `.ani` INI into ordered sections.

    An `.ani` is a plain-text INI: ``[Section]`` then ``FrameAmount=N`` and
    ``Frame0..Frame{N-1}=<logical .dds path>``. This keeps each section's
    declared count and the Frame<index> map so `validate_ani` can check that
    the order the file encodes matches the count it claims.
    """
    sections: list[AniSection] = []
    cur: Optional[AniSection] = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = AniSection(line[1:-1].strip(), None, {})
            sections.append(cur)
            continue
        if "=" not in line or cur is None:
            continue
        key, val = (x.strip() for x in line.split("=", 1))
        cur.order.append(key)
        low = key.lower()
        if low == "frameamount":
            try:
                cur.frame_amount = int(val)
            except ValueError:
                cur.frame_amount = None
            continue
        m = re.fullmatch(r"[Ff]rame(\d+)", key)
        if m:
            cur.frames[int(m.group(1))] = val
    return sections


def validate_ani(sections: list[AniSection]) -> list[str]:
    r"""RULE 3. Where does a section's frame set contradict its FrameAmount?

    A frame list is ordered and the order IS the animation, so:
      * `FrameAmount` must equal the number of `Frame<i>` keys;
      * the indices must be exactly `0..FrameAmount-1` with no gap and no
        duplicate spelling.
    A renumber that leaves `FrameAmount=4` but lists `Frame0,Frame1,Frame3`
    is a different animation, not a broken file -- the client would read three
    frames and a hole. Returns a human list of contradictions (empty when the
    manifest is internally consistent).
    """
    out: list[str] = []
    for s in sections:
        n = s.frame_amount
        idx = sorted(s.frames)
        if n is None:
            # A section with frames but no FrameAmount is a silent 0/omitted;
            # a section with neither is a header we do not police.
            if s.frames:
                out.append(f"[{s.name}] lists {len(idx)} Frame keys but "
                           f"declares no FrameAmount")
            continue
        if len(idx) != n:
            out.append(f"[{s.name}] FrameAmount={n} but {len(idx)} Frame "
                       f"key(s) present ({', '.join('Frame%d' % i for i in idx) or 'none'})")
        expected = list(range(n))
        if idx != expected and not (len(idx) != n):
            missing = [i for i in expected if i not in s.frames]
            extra = [i for i in idx if i >= n]
            detail = []
            if missing:
                detail.append("missing " + ", ".join("Frame%d" % i for i in missing))
            if extra:
                detail.append("out-of-range " + ", ".join("Frame%d" % i for i in extra))
            out.append(f"[{s.name}] frame order broken: {'; '.join(detail)}")
        elif idx and idx != expected:
            # count matches but indices are not 0..n-1: a gap balanced by an
            # out-of-range index -- the exact silent renumber the rule names.
            out.append(f"[{s.name}] frames are numbered {idx}, not "
                       f"{expected}: a renumber inconsistent with FrameAmount "
                       f"is a different animation")
    return out


def reassemble_ani(sections: list[AniSection]) -> str:
    """Render sections back to `.ani` text, frames in index order."""
    lines: list[str] = []
    for s in sections:
        lines.append(f"[{s.name}]")
        if s.frame_amount is not None:
            lines.append(f"FrameAmount={s.frame_amount}")
        for i in sorted(s.frames):
            lines.append(f"Frame{i}={s.frames[i]}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# table <-> CSV
# ---------------------------------------------------------------------------

def table_to_csv(plaintext: bytes) -> str:
    r"""A decoded table's plaintext -> CSV text.

    Rows are lines; columns are the `@@`-delimited fields `block96.recover_rows`
    recognises. A line with no delimiter becomes a single-column row, so a
    header or a comment survives the round trip. latin-1 throughout: the length
    rule is about bytes and a lossy re-encoding of the text would defeat it.
    """
    import csv
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in _split_rows(plaintext):
        s = row.decode("latin-1")
        w.writerow(s.split(TABLE_FIELD_DELIM))
    return buf.getvalue()


def csv_to_table(csv_text: str) -> bytes:
    """CSV text -> decoded-table plaintext bytes (inverse of `table_to_csv`)."""
    import csv
    rows_out: list[bytes] = []
    for cells in csv.reader(io.StringIO(csv_text)):
        rows_out.append(TABLE_FIELD_DELIM.join(cells).encode("latin-1"))
    return b"\n".join(rows_out) + (b"\n" if rows_out else b"")


# ---------------------------------------------------------------------------
# a table's plaintext
# ---------------------------------------------------------------------------

def table_plaintext(reader, dest: str,
                    raw: bytes) -> tuple[Optional[bytes], Optional[str]]:
    r"""A table's DECODED plaintext, or `(None, why)`. NEVER the ciphertext.

    `raw` is what `reader.read(dest)` handed back. For a plaintext/JSON family
    that IS the plaintext and comes straight back out; for a block96 family it
    is the ENCRYPTED file and has to be decoded before anything may treat it as
    rows.

    ADDED 2026-09-07 as the EXPORT half of the defect `_decoded_original` had
    on the import side (fixed in `claude/portage-decoded-original-plaintext`,
    d87d71cb). `build_manifest` read the file and handed the bytes straight to
    `table_to_csv`, which splits on `b"\n"` and joins on `@@`, so a block96
    table exported as a CSV of whatever its ciphertext happened to contain.
    Measured on `Clients/7205`, `ini/item_refine_cost.dat`:

        plaintext        62 bytes  3009000@@10@@ / 3009001@@100@@ / ...
        ciphertext       66 bytes
        exported CSV    109 bytes  starting `"\xc2\x9e\xc3\x86\r...`

    THE TWO DEFECTS PARTIALLY MASKED EACH OTHER, and the masking was not
    merely cosmetic -- MEASURED end to end on master, a length-preserving
    one-byte edit to the exported CSV was ACCEPTED and STAGED AS 67 BYTES for
    a table whose real plaintext is 62. RULE 2 waved it through because both
    sides of the comparison were ciphertext-derived. So the bug did not just
    export something unreadable; it let a user edit land corrupt bytes in the
    stage tree. With both halves fixed the same edit stages 62.

    Note precisely which round trip that is. An UNTOUCHED one never reaches
    RULE 2 in any of these states, because RULE 1 (`dds_untouched`, a hash of
    the working file against `export_sha`) skips it first -- so "untouched
    passes" is not evidence about this defect either way. It is the first
    EDITED round trip that tells you anything.

    Decoding is also what makes the round trip lossless at all. CSV quoting of
    arbitrary binary does not invert, so on the ciphertext path
    `csv_to_table(exported) != raw` (66 bytes in, 67 out); on the decoded path
    it is exact at 62.

    The decisions are d87d71cb's, deliberately, so the two sides cannot drift:

      * `block96.read_table` is given `reader.locate(dest).real_path` -- a
        FILESYSTEM PATH, not the logical `dest`, which was the original bug --
        rather than a re-decode of the bytes already in hand. It reuses
        `read_table`'s coverage AND entropy guards, and the entropy one is
        load-bearing: a poisoned dictionary entry reads as 100% coverage and
        only the entropy check sees it.
      * `kind` stays the `read_table` default `"rows"`. Re-measured here over
        `Clients/7205`'s 101 block96 tables: `"rows"` decodes 99, and the two
        it will not decode (`levexp.dat`, `ServerPlay.dat`) are the same two
        d87d71cb names.
      * A block96 table that will not decode yields **None and a reason, never
        `raw`**. The caller drops the export rather than writing mojibake.

    The decode is per FILE, not per family: `Clients/7205` ships 147
    `ini/*.dat` of which only 101 are block96, so `is_block96` is asked about
    each one. `Clients/6090` has 0 of 61 and is untouched by this path.

    VERIFIED after the fix, by exact identity rather than by eye: all 99 of
    7205's decodable block96 tables export to a CSV that `csv_to_table` turns
    back into `read_table(...).text` BYTE FOR BYTE, and the other 2 export
    nothing. The eyeball check ("does the CSV look like text?") disagrees on
    `StageGoal.dat` and the eyeball check is the one that is wrong -- that
    file decodes to real rows whose comments are GBK Chinese, which is
    high-bit nonsense read as latin-1. A printability heuristic is not an
    instrument for this; identity against the decode is.
    """
    try:
        import block96                # noqa: PLC0415
    except ImportError:
        return raw, None              # no decoder on this box; families differ
    if not block96.is_block96(raw, dest):
        # NOT "plaintext, nothing to do" -- that phrasing was inherited from
        # the import side and it is wrong. `is_block96` asks one question:
        # is this the family the block96 dictionary decodes? An `ini/*.dat`
        # that answers no may still be ciphered by some OTHER scheme, and
        # `inidat.classify` names several. Counted 2026-09-07 over two of
        # the installs on this box, one either side of the block96 boundary
        # (tq-stream / rar-mangled / rsa-mysqldump / shift-obfuscated /
        # binary-plain, in that order):
        #
        #   6090     0 block96 of 61, and 26 / 11 / 8 / 1 / 6
        #   7205   101 block96 of 147, and  3 / 11 / 10 / 1 / 6
        #
        # Those still export as mojibake CSV, exactly as they did before this
        # fix -- SAME SHAPE OF DEFECT, DIFFERENT CIPHER, and out of scope here
        # because there is no decoder for them to call. Deliberately left
        # passing `raw` through rather than refused: refusing would drop 53 of
        # 6090's 61 tables from every bundle, which is an owner-facing
        # behaviour change and not this fix's to make. Recorded so the next
        # reader measures it instead of believing this branch is clean.
        return raw, None

    # From here `raw` is CIPHERTEXT and must not reach a caller that asked for
    # plaintext. Every exit below is either the decode or None.
    loc = reader.locate(dest) if reader is not None else None
    real = getattr(loc, "real_path", None) if loc is not None else None
    if real is None:
        return None, (
            f"it resolves inside {getattr(loc, 'source', 'an archive')!r}, "
            f"which has no filesystem path for the decoder to read")
    # ROOTED off the located file, not off the configured install.
    # `dictionary_path` walks the root up to the directory holding `Clients/`,
    # so the root has to be a client dir in the assets tree -- `coroot.find()`
    # is the CONFIGURED install and its `.parent.parent` is a drive letter,
    # which answers None.
    root = block96.root_for_table(real)
    try:
        d = block96.load_dictionary(root)
    except Exception as e:            # noqa: BLE001 -- I/O + unpickle of ~103 MB
        # Scoped to this one call on purpose: it opens and unpickles a large
        # file, so a truncated or half-written dictionary is a real thing that
        # happens. Anything raised elsewhere in this function is a bug and is
        # left to propagate rather than being turned into a silent `raw`.
        return None, f"the block96 dictionary would not load: {e}"
    if d is None:
        return None, block96.why_no_dictionary(root)
    t = block96.read_table(str(real), d)
    if t.ok and t.text:
        return t.text, None
    return None, (t.refusal or "the decode recovered no rows")


def _recorded_length_violation(rec: dict, new_plain: bytes,
                               res: "ImportResult") -> list[str]:
    r"""RULE 2 when there is no original to compare against, file length only.

    Reached from `_import_table` when `_decoded_original` answers None -- the
    TARGET does not ship this file (or, once the import-side fix lands, ships
    it but cannot decode it). There is nothing to diff row by row, so the only
    check left is the whole-file byte length against what the manifest
    recorded, and the whole question is WHICH RECORDED NUMBER.

    IT USED TO READ `original_len`, AND THAT IS THE ON-DISK LENGTH. For a
    block96 family the file on disk is CIPHERTEXT, so the check compared the
    edited PLAINTEXT against a CIPHERTEXT length -- 62 against 66 on 7205
    `ini/item_refine_cost.dat` -- and refused a table the user had not touched.
    The two lengths differ on essentially every ciphered table (they agree only
    by coincidence), so this was wrong by construction rather than by accident:
    the third and last part of the defect d87d71cb and the export-side fix
    split between them.

    `original_plain_len` is the DECODED length, recorded by `build_manifest`
    for exactly this. It equals `original_len` for a plaintext-family table,
    so preferring it is not a special case for block96 -- it is simply the
    right field, and the old one was never a plaintext length except by luck.

    THE ONE CASE THAT STAYS IMPERFECT, stated rather than papered over: a
    bundle exported BEFORE `original_plain_len` existed carries only
    `original_len`, and nothing in the record says whether that file was
    ciphered. The target does not ship it -- that is why we are here -- so
    there is no third source to consult and no discriminator to invent. The
    check still runs against `original_len`, because silently skipping it
    would weaken RULE 2 on every old bundle to fix a subset, but the message
    says the number may be an on-disk length and that re-exporting settles it.
    A refusal here is not the last gate anyway: `length_violations` is
    documented as the FRIENDLY pre-check and `datdict.cmd_encode` fails closed
    behind it.
    """
    want = rec.get("original_plain_len")
    if want is not None:
        if len(new_plain) == want:
            return []
        return [f"file length {len(new_plain)} != the {want} bytes recorded "
                f"for the original's DECODED text; a length-preserving table "
                f"must keep its byte length"]

    want = rec["original_len"]
    if len(new_plain) == want:
        return []
    res.warnings.append(
        f"{rec['dest']}: this bundle predates `original_plain_len`, so the "
        f"length check below is against `original_len` -- the length of the "
        f"file AS IT SITS ON DISK. If this family ciphers its `.dat`, that is "
        f"the CIPHERTEXT length and the refusal may be spurious. Re-export "
        f"the bundle to get the decoded length recorded.")
    return [f"file length {len(new_plain)} != recorded {want} bytes; a "
            f"length-preserving table must keep its byte length. NOTE: that "
            f"figure is the original's ON-DISK length, which is not its "
            f"decoded length on a ciphered family -- see the warning above"]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@dataclass
class ExportItem:
    """One thing to export: a logical asset, its group, its shared flag."""
    logical: str
    group: str = ""                  #: tier-1 grouping key; default derived
    shared: bool = False
    #: A human-readable sub-path for the READABLE layout, e.g.
    #: ``combat/attack (410).c3``. Empty means "no researched name", and the
    #: readable layout then falls back to the file's own name -- it never
    #: invents one. **This affects `export_path` only and never `dest`**: the
    #: manifest's `dest` stays the logical path, so a bundle exported in the
    #: readable layout reimports to exactly the same places as one exported in
    #: the original layout. The zip is a VIEW; the manifest is the truth.
    readable: str = ""


@dataclass
class DefinitionItem:
    r"""ONE TABLE ROW, travelling as a row rather than as a file.

    THE PROBLEM THIS EXISTS FOR. An EFFECT is not a file. Its meshes and its
    textures are files; its DEFINITION -- the layer list, the timing, the
    blend modes -- is a single row of `3DEffect`, which on 5517/6090/6609/7205
    is the compiled `3DEffect.dbc` and NOT the `.ini` decoy beside it. A
    bundle carrying only the meshes and textures arrives at the far end with
    the art present and the effect UNDEFINED, and nothing in the zip says so.
    Backlog section 6 names that class of omission as the trap the whole
    Import/Export feature exists to refuse.

    WHY THE ROW AND NOT THE TABLE. Shipping the whole `3DEffect` file would
    "work" and would also overwrite all 3,391 (5517) / 5,313 (6609) other
    definitions on the target with the SOURCE install's -- a mod bundle for
    one effect silently reverting every other effect on a private server's
    client. So `_import_definition` reads the TARGET's own live table, merges
    THIS row into it, and stages the result. Every other row on the target is
    left exactly as the target had it.

    WHY IT CAN BE DONE AT ALL, MEASURED. `dbc.read_effe` / `dbc.serialize_effe`
    round-trip the compiled table BYTE-EXACT on every base that ships one --
    5517 3,391 rows / 363,958 B, 6090 4,483 / 508,446 B, 6609 and 7205 5,313 /
    627,162 B, all four `serialize(read(blob)) == blob`. So a merged table
    differs from the target's original in exactly the bytes of the row that
    changed. On a base with no compiled twin the live table is the plaintext
    `.ini` and the row is a `[Name]` section, carried as its raw text.

    Fields:
        table       the table's stem, e.g. ``3DEffect``
        key         the row's key -- the effect name
        dest        LOGICAL path of the file that ANSWERED, e.g.
                    ``ini/3DEffect.dbc``. Never the sibling that was not read.
        form        ``dbc`` | ``ini`` -- which reader/writer applies
        row         the decoded row: `read_effe`'s dict for ``dbc``, the raw
                    section text for ``ini``
        group       tier-1 bundle folder, as for a file item
    """
    table: str
    key: str
    dest: str
    form: str
    row: object = None
    group: str = ""
    note: str = ""


def definition_form(dest: str) -> str:
    """``dbc`` or ``ini`` for a table path -- by EXTENSION, never by guess.

    The caller passes the file that answered, so this only has to name the
    reader for it. An extension neither reader handles returns ``""`` and
    every path below refuses rather than picking one.
    """
    ext = Path(dest).suffix.lower()
    return {".dbc": "dbc", ".ini": "ini"}.get(ext, "")


_SECTION_RE = re.compile(r"^\[([^\]\r\n]+)\]", re.M)


def ini_sections(text: str) -> tuple:
    """``(preamble, [(name, raw_block), ...])`` for a sectioned ini.

    RAW BLOCKS, not parsed keys. Re-emitting a parsed section would rewrite
    every line of it -- key order, spacing, comments and all -- so a one-row
    edit would arrive as a whole-file diff and `comod diff` would be useless
    for reviewing it. Carrying the bytes means the merged file differs from
    the target's original in exactly the section that changed.
    """
    marks = list(_SECTION_RE.finditer(text))
    if not marks:
        return text, []
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1), text[m.start():end]))
    return text[:marks[0].start()], out


def merge_ini_section(text: str, key: str, block: str) -> tuple:
    """Replace or append ``[key]`` in `text`. Returns ``(new_text, what)``.

    `what` is ``replaced`` or ``added`` so the import log can say which
    happened -- landing a NEW effect on a target and overwriting an existing
    one are different acts and the user should not have to diff to find out.
    A key present more than once is REFUSED by the caller, not silently
    resolved: which of two rows the client honours is not measured here.
    """
    pre, secs = ini_sections(text)
    hits = [i for i, (name, _) in enumerate(secs) if name == key]
    if len(hits) > 1:
        raise ValueError(
            f"{key!r} appears {len(hits)} times in this table; which row the "
            f"client honours is not measured, so merging is refused rather "
            f"than guessing")
    if hits:
        secs[hits[0]] = (key, block)
        what = "replaced"
    else:
        secs.append((key, block))
        what = "added"
    return pre + "".join(b for _, b in secs), what


def _group_key(logical: str) -> str:
    """Default tier-1 group for an asset: `<parent-ish>_<stem>`.

    `c3/body/7130030.c3` -> `body_7130030`, so a mesh and its same-stem texture
    land under one folder without the caller having to name the group.
    """
    p = Path(logical)
    parent = p.parent.name or "asset"
    return f"{parent}_{p.stem}"


def _export_transform(asset_type: str, data: bytes) -> tuple[str, bytes, str]:
    r"""Apply the export transform. Returns (transform, working_bytes, ext).

    `ext` is the extension the working file should carry so the right program
    opens it (`.png` for a texture, `.csv` for a table, the original for a
    copy). The bytes are what gets written into the zip/workdir.

    `data` IS NOT ALWAYS `reader.read(dest)`. For `T_TABLE_CSV` the caller has
    to hand in the DECODED plaintext -- `table_to_csv` splits on `b"\n"`
    and joins on `@@`, so a block96 table passed through raw exports as
    mojibake. That was the bug; `build_manifest` decodes via `table_plaintext`
    first. The parameter is named `data` rather than `raw` to say so.
    """
    transform = transform_for(asset_type)
    if transform == T_DDS_PNG:
        return transform, _dds_to_png(data), ".png"
    if transform == T_TABLE_CSV:
        return transform, table_to_csv(data).encode("utf-8"), ".csv"
    # copy / ani-frames: the file leaves as-is (an .ani is edited as text).
    return transform, data, ""


def _dds_to_png(raw: bytes) -> bytes:
    from PIL import Image           # noqa: PLC0415
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _png_to_dds(png: bytes, fourcc: str, size: Optional[tuple[int, int]]) -> bytes:
    from PIL import Image           # noqa: PLC0415
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    out = io.BytesIO()
    im.save(out, format="DDS", pixel_format=fourcc)
    return out.getvalue()


#: The three bundle layouts.
#:
#:   grouped   `<asset>/<type>/<file>`     the shipped layout, still the
#:                                          default -- flat under its type
#:   source    `<asset>/<logical path>`     the client's OWN folders, kept
#:   readable  `<asset>/<type>/<group>/<name> (<number>).<ext>`
#:
#: **THE LAYOUT MOVES FILES INSIDE THE ZIP AND NOTHING ELSE.** Every record's
#: `dest` is the logical path in all three, so the bundles reimport
#: identically; `import_batch` reads `dest` and never the export path. A
#: layout that changed where a file came BACK to would be a different feature
#: and a dangerous one.
#:
#: `source` exists because `grouped` is NOT "the original folder structure"
#: -- it flattens it, and two files with one basename then land on one path.
#: That is not hypothetical: enumerating a loadout's motions puts
#: `c3/0001/410/401.c3` and `c3/0001/One-Handed/Sword/401.c3` in the same
#: bundle, and at least 5 of 124 (shape, weapon-type) loadouts on CCO carry
#: such a pair. `_unique_export_path` refuses the collision in EVERY layout,
#: because the damage is not cosmetic: the zip keeps one file while the
#: manifest keeps two records naming it, so a reimport writes one asset's
#: bytes to the other asset's destination.
LAYOUT_GROUPED = "grouped"
LAYOUT_SOURCE = "source"
LAYOUT_READABLE = "readable"
LAYOUTS = (LAYOUT_GROUPED, LAYOUT_SOURCE, LAYOUT_READABLE)

#: The default stays `grouped` -- it is the shipped layout every existing
#: caller and bundle already uses, and changing what an unqualified export
#: produces would rewrite the shape of bundles nobody asked to change.
LAYOUT_DEFAULT = LAYOUT_GROUPED


def layout_path(it: ExportItem, group: str, atype: str, ext: str,
                layout: str = LAYOUT_GROUPED) -> str:
    """Where one item sits inside the bundle, under `layout`.

    Shared by `build_manifest` and by the panel's own preview, so the
    structure drawn before Export is the structure on disk after it -- the
    equality `ZipLayoutIsWhatThePanelShowed` pins.

    An item with no `readable` name falls back to its own filename even in the
    readable layout. **That is deliberate: 126 of the 192 action codes a
    loadout resolves carry no researched name**, and inventing one would put a
    confident label on an unknown motion. An unnamed file keeps its number,
    which is the only thing actually known about it.
    """
    name = Path(it.logical).name
    if ext:
        name = Path(name).stem + ext
    if layout == LAYOUT_SOURCE:
        # The client's own path, kept whole. Logical paths are unique, so this
        # layout cannot collide -- it is the only one that is safe by
        # construction rather than by the guard below.
        rel = str(it.logical).replace("\\", "/").strip("/")
        if ext:
            rel = rel[:rel.rfind(".")] + ext if "." in Path(rel).name else rel + ext
        return f"{group}/{rel}"
    if layout == LAYOUT_READABLE and getattr(it, "readable", ""):
        rel = str(it.readable).replace("\\", "/").strip("/")
        # THE EXTENSION IS NOT OPTIONAL and the readable name does not carry
        # one. `ext` is set only when a transform changes the format (.dds ->
        # .png); with no transform the file keeps its own suffix, and dropping
        # it would put `attack (410)` in the zip where `attack (410).c3`
        # belongs -- a file nothing will open and Blender will not import.
        want = ext or Path(it.logical).suffix
        if want and not rel.lower().endswith(want.lower()):
            rel += want
        return f"{group}/{atype}/{rel}"
    return f"{group}/{atype}/{name}"


def _unique_export_path(path: str, logical: str, taken: dict) -> str:
    """`path`, or a disambiguated variant when another DEST already claimed it.

    THE COLLISION IS A CORRECTNESS BUG, NOT AN AESTHETIC ONE. Two logical
    files with the same basename flatten onto one export path; the zip then
    holds one of them while the manifest holds two records both naming it, and
    `import_batch` writes those bytes to BOTH `dest`s -- so one asset arrives
    at the far end as a copy of the other, with nothing in the bundle saying
    so. Silent, and exactly the class of omission this feature exists to
    refuse.

    Disambiguated by inserting the logical's parent directories, so the result
    is derived from the file's real path rather than from a counter: two runs
    over the same selection produce the same bundle, and the suffix tells a
    reader WHICH file it is. A counter would satisfy uniqueness and answer
    neither question.
    """
    prev = taken.get(path)
    if prev is None or prev == logical:
        taken[path] = logical
        return path
    head, _, tail = path.rpartition("/")
    parts = [p for p in str(logical).replace("\\", "/").strip("/").split("/")[:-1] if p]
    for depth in range(1, len(parts) + 1):
        cand = "%s/%s/%s" % (head, "/".join(parts[-depth:]), tail) if head else tail
        if taken.get(cand) in (None, logical):
            taken[cand] = logical
            return cand
    # Two identical logical paths would have returned at the top, so this is
    # unreachable for distinct dests; kept as a loud fallback rather than a
    # silent overwrite.
    raise ValueError("cannot disambiguate export path %r for %r" % (path, logical))


def build_manifest(reader, items: list[ExportItem],
                   provenance: dict,
                   definitions: Optional[list] = None,
                   layout: str = LAYOUT_DEFAULT
                   ) -> tuple[list[dict], dict[str, bytes]]:
    r"""Build the manifest ARRAY and the working files for a batch.

    Does not touch disk: returns the manifest (element 0 the batch record, the
    rest file records) and a `{export_path: bytes}` map the caller writes into
    a directory or a zip. Declared-but-absent satellites become records with
    `absent:true` and no export, so reimport can tell "never there" from
    "deleted". A file listed under more than one group is written once and
    marked `shared` on every record that names it.

    `definitions` is a list of `DefinitionItem` -- subjects that are TABLE
    ROWS rather than files (an effect's `3DEffect` row). They become
    `record:"definition"` elements. A caller that passes none gets exactly the
    manifest it got before this parameter existed, and the batch record's
    `definition_count` is 0, which is a NUMBER the far end can read rather
    than an absence it has to infer.
    """
    seen: dict[str, int] = {}        # export path -> how many groups claim it
    #: export path -> the logical that owns it, for `_unique_export_path`.
    claimed: dict = {}
    for it in items:
        if it.shared:
            seen[it.logical] = seen.get(it.logical, 0) + 1

    files: dict[str, bytes] = {}
    records: list[dict] = []
    # count how many distinct groups each logical appears under, to set shared
    groups_of: dict[str, set] = {}
    for it in items:
        groups_of.setdefault(it.logical, set()).add(it.group or _group_key(it.logical))

    for it in items:
        group = it.group or _group_key(it.logical)
        atype = classify(it.logical)
        shared = it.shared or len(groups_of[it.logical]) > 1
        loc = reader.locate(it.logical) if reader is not None else None
        if loc is None:
            records.append({
                "record": "file", "dest": it.logical, "type": atype,
                "transform": transform_for(atype), "group": group,
                "export": None, "original_sha": None, "original_len": None,
                "original_plain_len": None, "export_sha": None,
                "shared": shared, "absent": True, "export_refusal": None,
            })
            continue
        raw = reader.read(it.logical)
        # A block96 family ships its `ini/*.dat` ENCRYPTED, and `table_to_csv`
        # takes PLAINTEXT -- handing it `raw` exported a CSV of mojibake. See
        # `table_plaintext` for the measurement and for why an undecodable
        # table drops its export instead of falling back to the raw bytes.
        if transform_for(atype) == T_TABLE_CSV:
            data, why = table_plaintext(reader, it.logical, raw)
        else:
            data, why = raw, None
        if data is None:
            records.append({
                "record": "file", "dest": it.logical, "type": atype,
                "transform": transform_for(atype), "group": group,
                "export": None, "original_sha": sha256_hex(raw),
                "original_len": len(raw), "original_plain_len": None,
                "export_sha": None, "shared": shared, "absent": False,
                "export_refusal": why,
            })
            continue
        transform, working, ext = _export_transform(atype, data)
        export_path = _unique_export_path(
            layout_path(it, group, atype, ext, layout), it.logical, claimed)
        files[export_path] = working
        rec = {
            "record": "file", "dest": it.logical, "type": atype,
            "transform": transform, "group": group, "export": export_path,
            "original_sha": sha256_hex(raw), "original_len": len(raw),
            "original_plain_len": (len(data) if transform == T_TABLE_CSV
                                   else None),
            "export_sha": sha256_hex(working), "shared": shared,
            "absent": False, "export_refusal": None,
        }
        if atype == "ani":
            # carry the frame model so import can validate ordering even if the
            # user hand-edits the text (rule 3 does not need the source install)
            rec["ani"] = [
                {"name": s.name, "frame_amount": s.frame_amount,
                 "frames": s.frames}
                for s in parse_ani(raw.decode("latin-1"))
            ]
        records.append(rec)

    defs = _definition_records(reader, definitions or [], files)
    records.extend(defs)

    batch = {"record": "batch", "schema": MANIFEST_SCHEMA,
             "comod_version": COMOD_VERSION}
    batch.update(provenance)
    # what was actually EXPORTED. A block96 table that would not decode is
    # neither absent nor exported, so neither `absent` nor its negation counts
    # it correctly; `export` is the field that answers the question asked.
    batch["asset_count"] = sum(1 for r in records
                               if r["record"] == "file" and r["export"])
    batch["definition_count"] = len(defs)
    return [batch] + records, files


def _definition_records(reader, definitions: list,
                        files: dict[str, bytes]) -> list[dict]:
    """`DefinitionItem`s -> manifest records, writing their JSON into `files`.

    `table_sha` / `table_len` are the hash and length of the WHOLE source
    table, not of the row. They serve the same purpose the file records'
    `original_sha` serves: on someone else's install the live table should
    hash to what the manifest recorded, and when it does not that install is
    already modified and the user is told BEFORE the merge writes anything.
    They are not a gate -- a target whose table differs is the normal case for
    mod staging -- so a mismatch is a warning, and it is a warning that names
    the table.
    """
    out: list[dict] = []
    for d in definitions:
        group = d.group or slug_key(d.key)
        export_path = f"{group}/definition/{_safe_name(d.key)}.json"
        payload = {
            "table": d.table, "key": d.key, "dest": d.dest, "form": d.form,
            "row": d.row,
        }
        files[export_path] = (json.dumps(payload, indent=2, sort_keys=True)
                              + "\n").encode("utf-8")
        raw = None
        if reader is not None:
            try:
                if reader.locate(d.dest) is not None:
                    raw = reader.read(d.dest)
            except (FileNotFoundError, OSError):
                raw = None
        out.append({
            "record": "definition", "table": d.table, "key": d.key,
            "dest": d.dest, "form": d.form, "group": group,
            "type": "table-row", "transform": T_TABLE_ROW,
            "export": export_path,
            "table_sha": sha256_hex(raw) if raw is not None else None,
            "table_len": len(raw) if raw is not None else None,
            "absent": False, "shared": False, "note": d.note,
        })
    return out


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(key: str) -> str:
    """A filesystem-safe file stem for a row key.

    Effect names carry characters a path cannot (`m-b02` is fine, `zf2/e222`
    would not be), and the KEY itself is inside the JSON, so the filename need
    only be unique and legal -- it is never parsed back into the key.
    """
    s = _SAFE_NAME_RE.sub("_", str(key or "")).strip("_")
    return s or "row"


def slug_key(key: str) -> str:
    """Default tier-1 group for a row subject, when the caller names none."""
    return _safe_name(key).lower() or "definition"


def build_readme(manifest: list[dict]) -> str:
    batch = manifest[0]
    files = [r for r in manifest if r.get("record") == "file"]
    defs = [r for r in manifest if r.get("record") == "definition"]
    present = [r for r in files if r.get("export")]
    absent = [r for r in files if r["absent"]]
    # On disk, but nothing editable came out of it. Its own bucket because it
    # belongs in neither of the other two, and silently folding it into
    # "exported" would list it with an empty path.
    undecoded = [r for r in files
                 if not r["absent"] and not r.get("export")]
    lines = [
        "COMod export bundle",
        "===================",
        "",
        f"Source install : {batch.get('source_install', '?')}",
        f"Client family  : {batch.get('client_family', '?')}"
        f"  (version {batch.get('client_version', '?')})",
        f"Exported       : {batch.get('timestamp', '?')}",
        f"COMod version  : {batch.get('comod_version', '?')}",
        "",
        f"{len(present)} file(s) exported, {len(absent)} declared-but-absent"
        + (f", {len(undecoded)} present but NOT exported" if undecoded else "")
        + ",",
        f"{len(defs)} table row(s) carried as DEFINITIONS.",
        "",
        "To reimport:  py -3 tools/comod.py import <this-folder-or-zip>",
        "",
        "It lands in the stage tree; review with `comod diff` and apply with",
        "`comod install`. Import ENFORCES the format rules -- a texture you did",
        "not touch is skipped (never re-encoded), a table row that changes byte",
        "length on a 6907-7878 client is refused, and an .ani whose frame order",
        "contradicts its FrameAmount is refused.",
        "",
        "IMPORTANT: this bundle applies only to a "
        f"{batch.get('client_family', '?')} install. Applying it to a different",
        "client family is refused. Shared files (marked in the manifest) appear",
        "under more than one asset -- editing one edits both.",
        "",
        "Layout:  <asset>/<type>/<file>   +   " + MANIFEST_NAME,
        "",
    ]
    for r in present:
        tag = "  [shared]" if r["shared"] else ""
        lines.append(f"  {r['export']}   <- {r['dest']}{tag}")
    for r in absent:
        lines.append(f"  (absent)   {r['dest']}  -- declared, no file to export")
    if defs:
        # SAID IN THE README, not only in the manifest. A person opening the
        # zip to see what it holds must be able to tell that the effect's
        # DEFINITION is in here -- and, just as important, that importing it
        # rewrites one row of their own table rather than replacing it.
        lines += [
            "",
            "TABLE ROWS (definitions) -- these are NOT files:",
        ]
        for r in defs:
            lines.append(f"  {r['export']}   -> row {r['key']!r} of "
                         f"{r['table']} ({r['dest']})")
        lines += [
            "",
            "  A definition is one ROW. On import COMod reads YOUR install's",
            "  own copy of that table, merges this row into it, and stages the",
            "  result -- every other row on your install is left as it was.",
            "  The whole table is never shipped, so importing an effect cannot",
            "  revert the rest of your effects to the source install's.",
        ]
    for r in undecoded:
        lines.append(f"  (NOT exported)   {r['dest']}  -- "
                     f"{r.get('export_refusal') or 'no reason recorded'}")
    return "\n".join(lines) + "\n"


def write_batch_dir(dest: Path, manifest: list[dict],
                    files: dict[str, bytes]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (dest / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (dest / README_NAME).write_text(build_readme(manifest), encoding="utf-8")


def write_batch_zip(dest: Path, manifest: list[dict],
                    files: dict[str, bytes]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files.items():
            z.writestr(rel, data)
        z.writestr(MANIFEST_NAME,
                   json.dumps(manifest, indent=2) + "\n")
        z.writestr(README_NAME, build_readme(manifest))


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    staged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # (dest, why)
    refused: list[str] = field(default_factory=list)      # (dest, why)
    warnings: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused


class _Bundle:
    """A workdir OR a zip, read the same way."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._zip = None
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self._zip = zipfile.ZipFile(self.path, "r")
        elif not self.path.is_dir():
            raise FileNotFoundError(f"not a folder or zip: {self.path}")

    def read(self, rel: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(rel)
        return (self.path / rel).read_bytes()

    def read_text(self, rel: str) -> str:
        return self.read(rel).decode("utf-8")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def load_manifest(bundle: "_Bundle") -> list[dict]:
    man = json.loads(bundle.read_text(MANIFEST_NAME))
    if not isinstance(man, list) or not man or man[0].get("record") != "batch":
        raise ValueError(
            f"{MANIFEST_NAME} is not a COMod manifest array (element 0 must be "
            f'a "batch" record)')
    return man


def check_compatibility(manifest: list[dict], target_family: str) -> Optional[str]:
    r"""Provenance gate. Why this batch must NOT apply to `target_family`, or None.

    Same-family staging (destination 2) is in scope; cross-family (a 7205 zip
    into a 6609 tree) is deferred, so a mismatch is refused rather than
    attempted. `target_family` is the plugin name of the install being applied
    to; a batch with no recorded family cannot be checked and is refused loudly.
    """
    fam = manifest[0].get("client_family")
    if not fam:
        return ("this batch records no client family, so it cannot be checked "
                "for compatibility; refusing rather than guessing")
    if target_family and fam != target_family:
        return (f"this batch was exported from {fam}; the target install is "
                f"{target_family}. Cross-client-family import is deferred "
                f"(backlog item 5) -- refusing rather than corrupting assets")
    return None


def import_batch(bundle_path, reader, stage_dir: Path,
                 target_family: str = "",
                 png_to_dds: Optional[Callable] = None) -> ImportResult:
    r"""Read a bundle, ENFORCE the rules, land results in the stage tree.

    `reader` is an AssetRoot open on the TARGET install -- needed to recover a
    texture's original DDS format, to length-check a table against its shipped
    original, and to warn when the target's "original" no longer hashes to what
    the manifest recorded (that install is already modified). `png_to_dds` is
    injected so the encoder (PIL, in comod.py) is not a hard dependency of this
    module or its tests.
    """
    res = ImportResult()
    png_to_dds = png_to_dds or _png_to_dds
    with _Bundle(bundle_path) as bundle:
        manifest = load_manifest(bundle)
        why = check_compatibility(manifest, target_family)
        if why:
            res.refused.append(f"(batch): {why}")
            return res
        constrained = is_length_constrained(manifest[0].get("client_family", ""))
        for rec in manifest[1:]:
            if rec.get("record") == "definition":
                _import_definition(rec, bundle, reader, stage_dir, res)
                continue
            if rec.get("record") != "file":
                continue
            _import_one(rec, bundle, reader, stage_dir, constrained,
                        png_to_dds, res)
    return res


def _import_definition(rec: dict, bundle: "_Bundle", reader,
                       stage_dir: Path, res: ImportResult) -> None:
    r"""Land ONE table row: merge it into the TARGET's own table, stage that.

    THE TARGET'S TABLE IS THE BASE, NOT THE SOURCE'S. The bundle carries a
    row; this reads the install being applied to, replaces or appends that one
    row, and writes the whole merged table into the STAGE tree -- so `comod
    diff` shows one row's worth of change and `comod install` applies it. The
    source install's other 3,390 definitions never travel and can never
    clobber the target's.

    EVERY WAY THIS CAN GO WRONG IS A REFUSAL, NOT A BEST EFFORT:

    * the table the row came out of is not in the target install -- refuse;
    * its extension names no reader we have -- refuse (`definition_form`);
    * the target's table holds the key MORE THAN ONCE -- refuse. 6090 ships
      4,483 EFFE records under 4,472 distinct names, so this is a real state,
      and which of two rows the client honours is NOT measured. Picking one
      would be a guess the stage tree would then carry as fact;
    * the reader or the writer raises -- refuse, naming the exception.

    A refusal stages nothing, and `ImportResult.ok` is False, so the caller's
    "one or more files were refused; nothing refused was staged" holds for
    definitions exactly as it holds for files.
    """
    key = rec.get("key") or ""
    dest = rec.get("dest") or ""
    table = rec.get("table") or "(unnamed table)"
    what = f"{table} row {key!r}"
    export = rec.get("export")
    if not export:
        res.refused.append(f"{what}: no exported row recorded in the bundle")
        return
    try:
        payload = json.loads(bundle.read(export).decode("utf-8"))
    except KeyError:
        res.refused.append(f"{what}: {export} missing from the bundle")
        return
    except Exception as e:                                   # noqa: BLE001
        res.refused.append(f"{what}: {export} is not readable JSON: {e}")
        return
    row = payload.get("row")
    if row is None:
        res.refused.append(f"{what}: {export} carries no row")
        return

    form = rec.get("form") or definition_form(dest)
    if form not in ("dbc", "ini"):
        res.refused.append(
            f"{what}: {dest} is neither a .dbc nor an .ini, so there is no "
            f"reader for it here; refusing rather than writing bytes into a "
            f"format this module cannot parse")
        return

    # THE TABLE IS READ FROM THE STAGE FIRST. Two definitions in one bundle
    # both land in the same file, and reading the install both times would
    # make the second merge discard the first -- a silent half-import. The
    # stage copy, when there is one, already carries the earlier row.
    import safepath                                          # noqa: PLC0415
    staged = safepath.confine(stage_dir, dest)
    if staged.is_file():
        raw = staged.read_bytes()
        base = "the staged copy from earlier in this batch"
    else:
        raw = _target_original(reader, dest)
        base = "this install's own table"
        if raw is None:
            res.refused.append(
                f"{what}: this install has no {dest}, so there is no table to "
                f"merge the row into. The bundle carries a ROW; it does not "
                f"carry the table, by design.")
            return
        if rec.get("table_sha") and sha256_hex(raw) != rec["table_sha"]:
            res.warnings.append(
                f"{dest}: this install's {table} does NOT match what the "
                f"manifest recorded -- it is already modified. The row is "
                f"merged into YOUR table as it stands, so nothing else of "
                f"yours is lost, but the row you get may sit beside edits the "
                f"exporter never saw.")

    try:
        merged, action = (_merge_dbc_row(raw, key, row) if form == "dbc"
                          else _merge_ini_row(raw, key, row))
    except Exception as e:                                   # noqa: BLE001
        res.refused.append(f"{what}: {e.__class__.__name__}: {e}")
        return
    if merged == raw:
        # RULE 1'S SPIRIT, APPLIED TO A ROW. An exported-and-reimported row
        # that nobody edited merges to a table byte-identical to the one it
        # was merged into -- measured on 5517: 363,958 bytes in, 363,958 out,
        # every byte equal. Staging that would put a no-op file in front of
        # `comod diff` and teach the user that a definition import always
        # "changes" the table. Edit one field and it stages: one byte moves,
        # in the one row, out of 3,391.
        res.skipped.append(
            f"{dest}: {what} is unchanged from {base}, so nothing was staged")
        return
    _stage_write(stage_dir, dest, merged)
    res.staged.append(
        f"{dest}: {what} {action} into {base} "
        f"({len(raw)} -> {len(merged)} bytes; every other row untouched)")


def _merge_dbc_row(raw: bytes, key: str, row) -> tuple:
    """Merge one EFFE record into a compiled table. Returns (bytes, action).

    `dbc.serialize_effe(dbc.read_effe(blob)) == blob` on every base that ships
    one (5517/6090/6609/7205, measured), so the merged table differs from the
    one read in exactly the bytes of this row.
    """
    import dbc                                               # noqa: PLC0415
    if not isinstance(row, dict):
        raise ValueError("a dbc definition row must be an object, not "
                         + type(row).__name__)
    records = dbc.read_effe(raw)
    # JSON has no tuples: `offset` and each layer's floats come back as lists.
    # `serialize_effe` unpacks `offset` positionally, so a list is fine -- but
    # normalising here keeps the value identical in shape to what the reader
    # produced, which is what makes a re-read of the merged table compare
    # equal to this row rather than merely equivalent.
    row = dict(row)
    if isinstance(row.get("offset"), list):
        row["offset"] = tuple(row["offset"])
    hits = [i for i, r in enumerate(records) if r.get("name") == key]
    if len(hits) > 1:
        raise ValueError(
            f"{key!r} appears {len(hits)} times in this install's table; "
            f"which row the client honours is not measured, so merging is "
            f"refused rather than guessing")
    if hits:
        records[hits[0]] = row
        action = "replaced"
    else:
        records.append(row)
        action = "added"
    return dbc.serialize_effe(records), action


def _merge_ini_row(raw: bytes, key: str, row) -> tuple:
    """Merge one `[key]` section into a plaintext table. Returns (bytes, action).

    The row is carried as RAW TEXT, so the merged file differs from the one
    read in exactly that section -- key order, spacing and comments elsewhere
    are byte-identical.
    """
    if not isinstance(row, str):
        raise ValueError("an ini definition row must be the section's raw "
                         "text, not " + type(row).__name__)
    text = raw.decode("latin-1")
    merged, action = merge_ini_section(text, key, row)
    return merged.encode("latin-1"), action


def _target_original(reader, dest: str) -> Optional[bytes]:
    if reader is None:
        return None
    try:
        if reader.locate(dest) is None:
            return None
        return reader.read(dest)
    except (FileNotFoundError, OSError):
        return None


def _warn_target_drift(rec: dict, reader, res: ImportResult) -> None:
    """Rule-5 compatibility: the target's original must hash to the manifest's."""
    orig = _target_original(reader, rec["dest"])
    if orig is None or rec.get("original_sha") is None:
        return
    if sha256_hex(orig) != rec["original_sha"]:
        res.warnings.append(
            f"{rec['dest']}: the target install's original does NOT match what "
            f"the manifest recorded -- this install is already modified. "
            f"Importing will overwrite the existing change.")


def _stage_write(stage_dir: Path, dest: str, data: bytes) -> None:
    import safepath                  # noqa: PLC0415
    p = safepath.confine(stage_dir, dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _import_one(rec: dict, bundle: "_Bundle", reader, stage_dir: Path,
                constrained: bool, png_to_dds: Callable,
                res: ImportResult) -> None:
    dest = rec["dest"]
    if rec.get("absent"):
        res.absent.append(f"{dest}: declared-but-absent, nothing to import")
        return
    export = rec.get("export")
    if not export:
        res.refused.append(f"{dest}: no exported file recorded")
        return
    try:
        working = bundle.read(export)
    except KeyError:
        res.refused.append(f"{dest}: {export} missing from the bundle")
        return

    transform = rec.get("transform")
    _warn_target_drift(rec, reader, res)

    # RULE 1 and the general skip: a working file that still hashes to what was
    # exported was not touched, so it must NOT be re-encoded or re-staged.
    if dds_untouched(rec, working):
        res.skipped.append(f"{dest}: unchanged since export, not re-encoded")
        return

    if transform == T_DDS_PNG:
        orig = _target_original(reader, dest)
        fourcc, size = "DXT3", None
        if orig is not None:
            try:
                from coassets import dds_info   # noqa: PLC0415
                info = dds_info(orig)
                if info:
                    fourcc = info.fourcc or "DXT3"
                    size = (info.width, info.height)
            except Exception:
                pass
        try:
            dds = png_to_dds(working, fourcc, size)
        except Exception as e:
            res.refused.append(f"{dest}: could not encode PNG -> DDS: {e}")
            return
        _stage_write(stage_dir, dest, dds)
        res.staged.append(f"{dest}: re-encoded from PNG ({fourcc})")
        return

    if transform == T_TABLE_CSV:
        _import_table(rec, working, reader, stage_dir, constrained, res)
        return

    if transform == T_ANI_FRAMES:
        _import_ani(rec, working, stage_dir, res)
        return

    # copy: c3, audio, other. A .c3 is validated so a malformed mesh cannot
    # reach the install; anything else lands as-is.
    if classify(dest) == "c3":
        why = _validate_c3(working)
        if why:
            res.refused.append(f"{dest}: {why}")
            return
    _stage_write(stage_dir, dest, working)
    res.staged.append(f"{dest}: staged ({len(working)} bytes)")


def _validate_c3(data: bytes) -> Optional[str]:
    """Walk a .c3 the way stage-mesh does; return a refusal reason or None."""
    try:
        from c3phy import VARIANTS, iter_chunks, parse_phy  # noqa: PLC0415
    except Exception:
        return None                  # validator unavailable -> do not block
    try:
        chunks = list(iter_chunks(data))
    except Exception as e:
        return f"not a valid MAXFILE C3 container -- {e}"
    phy = [(t, b) for t, b in chunks if t in VARIANTS]
    if not phy:
        return "no PHY chunk in the file"
    for tag, body in phy:
        try:
            parse_phy(tag, body)
        except Exception as e:
            return f"PHY chunk failed to re-parse -- {e}"
    return None


def _import_table(rec: dict, working: bytes, reader, stage_dir: Path,
                  constrained: bool, res: ImportResult) -> None:
    dest = rec["dest"]
    try:
        new_plain = csv_to_table(working.decode("utf-8"))
    except Exception as e:
        res.refused.append(f"{dest}: CSV would not parse: {e}")
        return
    # The original decoded plaintext to length-check against. Prefer the target
    # install's own copy so the check is against what is really there; fall
    # back to the manifest's original_len for a file the target does not ship.
    orig_plain = _decoded_original(reader, dest, res)
    if orig_plain is not None:
        viol = length_violations(orig_plain, new_plain, constrained)
    elif constrained and rec.get("original_len") is not None:
        viol = _recorded_length_violation(rec, new_plain, res)
    else:
        viol = []
    if viol:
        res.refused.append(f"{dest}: RULE 2 (length) -- " + "; ".join(viol))
        return
    # Landed as the decoded plaintext. Encrypting back to a block96 `.dat` is
    # delegated to `datdict` (length rule already satisfied here); a family
    # whose tables are plaintext/JSON needs no further step. Recorded honestly
    # so the caller knows what still has to happen.
    _stage_write(stage_dir, dest, new_plain)
    res.staged.append(f"{dest}: table staged as decoded rows "
                      f"({len(new_plain)} bytes); encrypt-back via datdict if "
                      f"this family ciphers its .dat")


def _decoded_original(reader, dest: str,
                      res: Optional[ImportResult] = None) -> Optional[bytes]:
    """The target's original table as decoded plaintext, or None.

    Uses `block96` when a dictionary is available; otherwise returns the raw
    bytes (a plaintext/JSON family needs no decode). None when the target does
    not ship the file at all, AND now also when the file IS block96 but could
    not be decoded -- see below for why that is not the same as "no file".

    FIXED 2026-09-07, and the bug was in the argument, not the rooting.
    `read_table` takes a FILESYSTEM PATH and does `Path(path).read_bytes()`;
    this passed `dest`, a LOGICAL asset name like `"ini/itemtype.dat"`, which
    resolves against the process cwd. `read_table` catches the OSError and
    reports it as a refusal rather than raising, so `t.ok` was False on every
    cwd but the install root and the branch fell through to `return raw`.

    `raw` FOR A BLOCK96 TABLE IS CIPHERTEXT, and that is what made this worse
    than dead code. `_import_table` length-checks the value against the edited
    plaintext row by row, and ciphertext splits on stray `0x0A` bytes into a
    row count that has nothing to do with the table's:

        7205 ini/itemtype.dat        ciphertext 39,854 "rows" / plaintext 44,684
        7205 ini/item_refine_cost.dat            1 "row"  /             4

    so RULE 2 refused an edit that changed nothing. A previous note here called
    the branch "INERT rather than wrong"; it was measurably wrong.

    Two further decisions, both measured on 7205's 101 block96 tables:

      * `kind` stays the `read_table` default of `"rows"`. `"rows"` decodes 99
        of the 101; `"sections"` fails on 88 of them, and on the table usually
        named as section-shaped (`Monster.dat`) the two agree byte for byte.
        Rule 2 is a per-ROW byte-length rule, so rows is also the right unit.
      * A block96 table that will not decode returns **None, never `raw`**.
        None sends `_import_table` to its `original_len` fallback, which is a
        real check; ciphertext sends it to a fabricated one. The two tables
        that decode under neither kind (`levexp.dat`, `ServerPlay.dat`) take
        this path, and `res` carries the reason out instead of a bare `pass`.
    """
    raw = _target_original(reader, dest)
    if raw is None:
        return None
    try:
        import block96                # noqa: PLC0415
    except ImportError:
        return raw                    # no decoder on this box; families differ
    if not block96.is_block96(raw, dest):
        return raw                    # plaintext/JSON family: nothing to do

    # From here `raw` is CIPHERTEXT and must not be returned to a caller that
    # asked for plaintext. Every exit below is either the decode or None.
    def _give_up(why: str) -> None:
        if res is not None:
            res.warnings.append(
                f"{dest}: the target install's copy is block96-encrypted and "
                f"could not be decoded, so the length check falls back to the "
                f"manifest's recorded length -- {why}")
        return None

    loc = reader.locate(dest)
    real = getattr(loc, "real_path", None) if loc is not None else None
    if real is None:
        return _give_up(
            f"it resolves inside {getattr(loc, 'source', 'an archive')!r}, "
            f"which has no filesystem path for the decoder to read")
    # ROOTED off the located file, not off the configured install.
    # `dictionary_path` walks the root up to the directory holding `Clients/`,
    # so the root has to be a client dir in the assets tree -- `coroot.find()`
    # is the CONFIGURED install and its `.parent.parent` is a drive letter,
    # which answers None.
    root = block96.root_for_table(real)
    try:
        d = block96.load_dictionary(root)
    except Exception as e:            # noqa: BLE001 -- I/O + unpickle of ~103 MB
        # Scoped to this one call on purpose. It opens and unpickles a large
        # file, so a truncated or half-written dictionary is a real thing that
        # happens; anything raised anywhere else in this function is a bug and
        # is left to propagate rather than being turned into a silent `raw`.
        return _give_up(f"the block96 dictionary would not load: {e}")
    if d is None:
        return _give_up(block96.why_no_dictionary(root))
    t = block96.read_table(str(real), d)
    if t.ok and t.text:
        return t.text
    return _give_up(t.refusal or "the decode recovered no rows")


def _import_ani(rec: dict, working: bytes, stage_dir: Path,
                res: ImportResult) -> None:
    dest = rec["dest"]
    sections = parse_ani(working.decode("latin-1"))
    viol = validate_ani(sections)
    if viol:
        res.refused.append(f"{dest}: RULE 3 (frame order) -- " + "; ".join(viol))
        return
    # Re-render from the parsed model so the staged file is canonical order.
    _stage_write(stage_dir, dest, reassemble_ani(sections).encode("latin-1"))
    res.staged.append(f"{dest}: .ani staged ({len(sections)} section(s))")
