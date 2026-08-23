"""Catalog machinery shared by every parser plugin.

This was written inside `plugins/patch7878.py`, because 7878 was the client
that needed it first.  It is not 7878 machinery.  Every build has subjects it
can enumerate and subjects it cannot, and the useful part -- *a count is not
evidence; name a row you actually opened* -- is the same argument on all of
them.

The plugin hierarchy forks below `Plugin` (`PlaintextFamily` for 5017/5065/
5165/7878, `Patch6090` for 6090/6609/5517, and `Plugin` directly for CCO and
Zephyr), so there is no shared subclass to hang this on.  It lands here, and
`Plugin` grows the four methods that drive it.

WHAT A BUILD DECLARES, AND WHAT IT MUST NOT INHERIT
---------------------------------------------------
Each plugin returns its own `TableSpec` tuple, censused from its own `ini/`.
The builds genuinely differ, and the differences are not a gradient -- MEASURED
with `core/inidat.py` over all seven installs, counting only files that
classify into a family we can actually read:

    5017, 5065   Action, GameMap, itemtype, MagicType, MapDestination, Monster
    5165         + mounttype
    5517         + magictypeop
    6090         + AutoUseMagic, item_value_type, ItemtypeSub, magictypeex
    6609         + item_refine_*  (five), and it RENAMES two of them to
                   lowercase `monster.dat` / `magictype.dat`
    7878         Action and GameMap ONLY -- everything else in `ini/` is
                   block96, so its content arrives through the derived corpus

The one genuine constant is that `Action.dat` and `GameMap.dat` are
`binary-plain` on all seven, 7878 included.  Nothing else survives the whole
range, which is why a spec is declared per build rather than defaulted and
patched.

TWO KINDS OF CONTROL, AND WHY THEY ARE NOT INTERCHANGEABLE
-----------------------------------------------------------
7878's tables are plaintext, so its control could do the strong thing: search
the *undecoded* bytes for the section header and one `key=value` the parse
claims.  The reader is then not its own witness.

The older builds' tables are `tq-stream`.  There are no plaintext bytes on disk
to search, so the strongest available control is a second, independent decode
from disk, checked against the first.  That witnesses the PARSE.  It does not
witness the CIPHER -- if `tqdat.decrypt` were wrong, both decodes would be
wrong together and agree.

Those are different amounts of evidence and this module refuses to record them
under one word.  `Catalog.control_kind` is `raw` or `re-decode`, and a caller
reporting a `re-decode` control as though it proved the bytes is overstating
what happened.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

__all__ = [
    "Catalog", "TableSpec", "CONTROL_RAW", "CONTROL_REDECODE",
    "CONTROL_CACHED",
    "sections_from_text", "rows_from_text", "at_rows_from_text",
    "read_sections", "read_at_rows",
    "KIND_SECTIONS", "KIND_AT_ROWS", "KIND_CSV_ROWS", "KIND_SPACE_ROWS",
    "KIND_FLAT_KEYS", "KIND_LIST", "flat_keys_from_text", "list_from_text",
    "control_flat_key", "control_list_value",
    "ROW_KINDS",
    "control_section", "control_at_row",
    "build_catalogs", "no_tables_declared",
    "KIND_GAMEMAP", "gamemap_records",
    "KIND_JSON_ROWS", "json_rows", "control_json_row",
]

#: The probe was found in the file's UNDECODED bytes.  Only reachable for a
#: plaintext table, and the only control that witnesses the bytes themselves.
CONTROL_RAW = "raw"

#: The probe was found in a second, independent decode from disk.  Witnesses
#: the parse; the cipher is assumed.  Weaker than `CONTROL_RAW` and recorded
#: separately so the difference cannot be lost in a summary.
CONTROL_REDECODE = "re-decode"

#: The decode came from `core/dcache.py`, so the control fired ONCE when the
#: entry was written and is recorded, not re-derived.  **A cached decode cannot
#: witness itself**: serving both the parse and its "independent re-decode"
#: from one cache entry makes the witness the same bytes read twice, which
#: witnesses nothing -- and the count would look identical with the evidence
#: gone.  Weaker than `CONTROL_REDECODE`, which is already weaker than
#: `CONTROL_RAW`, and kept apart from both for the same reason they are kept
#: apart from each other.
CONTROL_CACHED = "cached"

#: `ini-sections` -- `[id]` headers with `key=value` bodies.
#: The other three are positional rows differing only in delimiter, and the
#: delimiter is NOT a property of the filename -- MEASURED across the range:
#:
#:     itemtype.dat        space-rows on 5017/5065/5165, at-rows from 5517
#:     MagicType.dat       binary on 5017/5065, space-rows on 5165,
#:                         at-rows from 5517
#:     magictypeop.dat     csv-rows on every build that ships it
#:     MapDestination.dat  ini-sections on ALL of them, never rows
#:     AutoUseMagic.dat    ini-sections, not rows
#:
#: The first three of those were declared wrong from the filename in this
#: file's first draft, and the positive controls refused all three rather than
#: reporting a line count as a row count. That is the control working: 5017's
#: `itemtype.dat` "parsed 11,256 rows" while actually being one field per line.
KIND_SECTIONS = "ini-sections"
KIND_AT_ROWS = "at-rows"
KIND_CSV_ROWS = "csv-rows"
KIND_SPACE_ROWS = "space-rows"

#: `key=value` with NO section headers anywhere -- 102 files across the eight
#: installs, including the two largest tables the `.ini` census found:
#:
#:     3DMotion.ini      2.0 MB   64,337 keys   0999001100=c3/npc/999001100.c3
#:     WeaponMotion.ini  881 KB   25,944 keys   1050000999300=c3/mesh/...c3
#:
#: These are appearance-to-mesh maps -- what the effect chain walks to turn an
#: id into geometry -- so the largest thing nothing could read was also among
#: the most load-bearing.
#:
#: It is a SEPARATE kind rather than a lenient `KIND_SECTIONS`, because a
#: sections reader given a headerless file returns zero sections and a
#: sections reader made lenient enough to accept one would merge every real
#: table's sections into a single namespace.
KIND_FLAT_KEYS = "flat-keys"

#: One bare value per line: no key, no delimiter.  `immediate.ini` is 56,985
#: of them.  Kept distinct from `KIND_SPACE_ROWS` with one field because a
#: list is not a degenerate table -- calling it one reports 56,985 "rows" and
#: implies a column, and a schema, that nobody established.
KIND_LIST = "single-column"

#: Kind -> delimiter. `None` means split on runs of whitespace.
ROW_KINDS = {KIND_AT_ROWS: "@@", KIND_CSV_ROWS: ",", KIND_SPACE_ROWS: None}

#: `GameMap.dat` -- a binary record table, and the ONLY content table whose
#: grammar holds unchanged across the whole 5017-to-7878 range.
#:
#:     u32           count
#:     count times:  u32 map_id, u32 path_len, char[path_len] path, u32 flag
#:
#: MEASURED on all seven builds, and the control is the strongest kind
#: available for a binary format: the declared count and the parsed count
#: agree, and the parse consumes the file EXACTLY -- no leftover byte, no
#: short read, on any of them.
#:
#:     5017  145 maps, 4,698 bytes    5517  262 maps,  8,131
#:     5065  153 maps, 4,985          6090  303 maps,  9,520
#:     5165  179 maps, 5,911          6609  358 maps, 11,305
#:                                    7878  730 maps, 24,413
#:
#: The paths change extension at 5517 -- `.DMap` on the three oldest builds and
#: `.7z` from 5517 on -- which is the same split `client/gamemap.py` handles
#: when it prefers the registry's archive over a loose `.DMap`.
#:
#: `flag` takes exactly two values on every build, 128 and 256, and nothing
#: else. What it selects is NOT established, so it is carried and not named.
KIND_GAMEMAP = "gamemap-records"

#: **CCO ships its content as JSON, and a census that globs `.dat` and `.ini`
#: cannot see it.**  That is how this kind came to exist: the first census of
#: Classic Conquer 2.0 counted 2 `.dat` and 48 `.ini`, found no item, monster
#: or npc table among them, and was one sentence away from reporting that the
#: client ships no content tables at all.  It ships six, as JSON lists of
#: dicts with named fields -- richer than any official build's, because the
#: columns are already named:
#:
#:     ini/itemtype.json    11,142 rows   id / name     8.3 MB
#:     ini/magictype.json      610        MagicType / Name
#:     ini/npc.json            437        type / name
#:     ini/monster.json        374        type / name
#:     ini/TerrainNpc.json      35        type / name
#:     ini/WeaponSkillLevelExp   20       level / exp
#:
#: `plugins/__init__.py`'s `table_profile` docstring already said NPCs come
#: from *"`npc.json` or `npc.ini`"*, so the codebase knew.  The census
#: instrument did not, and an instrument that cannot see a format reports its
#: absence in exactly the same words as a client that does not ship it.
KIND_JSON_ROWS = "json-rows"

def npc_specs(present) -> tuple:
    r"""The npc-family `.ini` tables, for the builds that ship them.

    **These were missing from every build but 7878, and the reason is worth
    keeping.** The brief said *"census each build's `ini/`"*; I censused the
    `.dat` files IN `ini/` and never looked at the `.ini` files themselves.
    So `npc.ini` -- the largest content table on six of these clients -- was
    absent from the surface whose entire purpose is browsing content:

        5017    510      5517  1,108      7878  4,084   (declared)
        5065    582      6090  2,251      Zephyr 2,760
        5165    893      6609  2,750

    The asymmetry was visible in the code the whole time: `patch7878` declared
    five npc subjects and no other plugin declared any. Nothing asked why.
    Same family as CCO's JSON -- an instrument that never looks at a format
    reports its absence exactly as a real absence would read.

    `present` is the filenames this build actually ships with content, because
    they are NOT uniform: `NpcX.ini` exists but holds **zero sections** on
    5017, 5065 and 5165, and `SlotNpc.ini` arrives only at 6090. Labels are
    measured per file -- `Name` is present in every section of `npc.ini`,
    `NpcX.ini` and `terrainnpc.ini`, while `npcex.ini` carries `Amount` and
    `SlotNpc.ini` carries `Data0` and no name of any kind.
    """
    label = {"npc.ini": "Name", "NpcX.ini": "Name", "terrainnpc.ini": "Name",
             "npcex.ini": "Amount", "SlotNpc.ini": "Data0"}
    return tuple(
        TableSpec(f"npc:{name}", name, KIND_SECTIONS, "plaintext",
                  label_key=label[name])
        for name in present)


#: Which key labels a row, per subject -- **MEASURED, not assumed**, by taking
#: the keys present in EVERY section of each table on each build that ships it:
#:
#:     monster     id IS the name (`GuildBeast`, `Ripper`, `LuckyCat`), so the
#:                 useful second column is `Level`. There is no `Name` key.
#:     mount       no name at all; `armor` is the appearance it wears, which
#:                 is the thing a modder goes on to `show`.
#:     map:dest    `title`
#:     magic:auto  `amount`
#:
#: This exists because the default `Name` printed a BLANK label on every row of
#: `monster`, `mount` and `map:dest` on 5017, 5065, 5165, 5517, 6090 and 6609
#: -- the identical defect already documented for 7878's mounts ("assuming a
#: `Name` key would have printed 1,937 blank cells and read as a bug"), made
#: again on six builds by a base class default that was never checked against
#: the tables it would be applied to.
#:
#: Plugins **opt in** by assigning this to `ROW_LABEL_KEY` rather than
#: inheriting it, so a build that measures something different says so.
MEASURED_SECTION_LABELS = {
    "monster": "Level",
    "mount": "armor",
    "map:dest": "title",
    "magic:auto": "amount",
}

#: A leading `Amount=11255` line, which the space-row tables carry and the
#: `@@` ones do not. Counted as a header rather than a row: 5017's
#: `itemtype.dat` declares 11,255 and has 11,256 non-blank lines.
_COUNT_HEADER = re.compile(r"^\s*\w+\s*=\s*\d+\s*$")


@dataclass(frozen=True)
class Catalog:
    """One subject a client can answer for, or say why it cannot.

    `rows` is a count.  `control` is the **positive control**: a specific row
    opened by the reader and checked back against a source the reader did not
    produce.  A count on its own is not evidence -- four of this sprint's wrong
    answers were instruments that could not have fired -- so a catalog that
    cannot name a row it opened reports `rows=None` and a refusal instead.
    """
    subject: str
    source: str
    kind: str
    rows: Optional[int] = None      # unique ids; see `duplicated`
    control: Optional[str] = None
    refusal: Optional[str] = None
    #: `CONTROL_RAW`, `CONTROL_REDECODE` or `CONTROL_CACHED`.  None when
    #: there is no control.  Three words for three different amounts of
    #: evidence; collapsing any pair of them overstates the weaker.
    control_kind: Optional[str] = None
    #: ids that appear more than once.  `rows + sum(duplicated.values()) -
    #: len(duplicated)` is how many headers the file actually carries, and
    #: reporting only `rows` is how a count goes quietly short.
    duplicated: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rows is not None and self.control is not None

    @property
    def present(self) -> Optional[int]:
        """Headers actually in the file, duplicates included."""
        if self.rows is None:
            return None
        return self.rows + sum(self.duplicated.values()) - len(self.duplicated)

    def as_dict(self) -> dict:
        d = {"subject": self.subject, "source": self.source, "kind": self.kind,
             "rows": self.rows}
        if self.duplicated:
            d["duplicated"] = len(self.duplicated)
            d["present"] = self.present
        if self.control:
            d["control"] = self.control
            d["control_kind"] = self.control_kind
        if self.refusal:
            d["refusal"] = self.refusal
        return d


@dataclass(frozen=True)
class TableSpec:
    """One table a build declares, and how to get at it.

    `codec` is what `core/inidat.py` classified the file as, not a guess:

      * ``plaintext``    -- read as text, strong (`raw`) control available
      * ``binary-plain`` -- readable bytes, but not sections or rows; declared
                            so the subject is NAMED rather than missing, with a
                            refusal saying the shape is not yet parsed here
      * ``tq-stream``    -- `core/tqdat.py`, `re-decode` control only
      * ``derived``      -- the plugin resolves the path itself (7878's
                            decrypted corpus); already plaintext once located
    """
    subject: str
    filename: str
    kind: str
    codec: str = "plaintext"
    #: Display path.  Defaults to `ini/<filename>`.
    source: str = ""
    #: The key that labels a row in `browse`.
    #:
    #:   ``""``    unset -- fall back to the plugin's `ROW_LABEL_KEY`.
    #:   ``None``  the build has MEASURED that this table has no label, and
    #:             the id stands alone. The blank-label guard permits this
    #:             and only this.
    #:   a string  that key, for this table specifically.
    label_key: Optional[str] = ""
    #: For `json-rows`: the field carrying the row id. Varies by table --
    #: `itemtype.json` uses `id`, `monster.json` and `npc.json` use `type`,
    #: and `magictype.json` has no unique key at all.
    id_key: str = "id"
    #: Set when the build has measured that this table cannot be enumerated,
    #: so the subject is still named and the reason travels with it.
    refusal: Optional[str] = None

    @property
    def display(self) -> str:
        return self.source or f"ini/{self.filename}"


_SECTION = re.compile(r"^\s*\[([^\]\r\n]+)\]\s*$")


def sections_from_text(text: str) -> tuple:
    """`({section: {key: value}}, {duplicated_id: count})`, FIRST wins.

    **These tables really do repeat section ids**, and a dict keyed by id
    silently keeps one of each and reports a count that is short:

        ini/npc.ini      4,087 present, 4,084 unique   (3 ids twice)
        Monster.dat      5,271 present, 5,234 unique  (32 ids repeated)
        mounttype.dat    1,939 present, 1,937 unique   (2 ids twice)

    **First wins, because that is what Win32 does** -- `GetPrivateProfileString`
    returns the FIRST value, which `docs/CORRECTIONS.md`
    `C-2026-08-10-asstdir-inidb-duplicate-policy` established after the code
    claimed last-wins parity and did neither.  Duplicates are returned rather
    than dropped so a caller can report *present* and *unique* instead of
    quietly serving the smaller number.
    """
    out: dict = {}
    dupes: dict = {}
    cur: Optional[dict] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ";#":
            continue
        m = _SECTION.match(line)
        if m:
            ident = m.group(1)
            if ident in out:                      # first wins
                dupes[ident] = dupes.get(ident, 1) + 1
                cur = None
                continue
            cur = {}
            out[ident] = cur
            continue
        if cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.strip()
    return out, dupes


def rows_from_text(text: str, delimiter: Optional[str] = "@@") -> list:
    """Positional rows, split and nothing more.

    Deliberately returns lists rather than dicts: naming 68 columns from a
    59-name table is what `tqdat.parse_itemtype` does to `itemtype.dat`, and it
    does it without raising.  A caller wanting named columns has to establish
    the names first.

    `delimiter=None` splits on whitespace, for the pre-5517 tables.  A leading
    `Amount=N` line is dropped -- it is a count, not a row.
    """
    rows = []
    first = True
    for line in text.splitlines():
        if not line.strip():
            continue
        # **A comment is not a row.** These files comment with `//`, and this
        # reader did not know it: `Nationality.ini` "parsed 54 rows" over 52
        # real ones, and -- worse -- row[0] WAS the comment, so `control_at_row`
        # probed the comment's tokens, failed to find them, and refused the
        # whole table. The count was inflated and the control was the thing
        # that said so, which is the control working.
        #
        # `//` `;` `#` only. `/` and `--` are comment prefixes in the `.ini`
        # grammar census, but a positional row may legitimately open with a
        # path or a negative number, and eating a data row to tidy a count is
        # the worse error of the two.
        if line.lstrip().startswith(("//", ";", "#")):
            continue
        if first:
            first = False
            if _COUNT_HEADER.match(line):
                continue
        if delimiter is None:
            fields = line.split()
        else:
            fields = line.split(delimiter)
            if fields and fields[-1] == "":
                fields = fields[:-1]
        rows.append(fields)
    return rows


def at_rows_from_text(text: str) -> list:
    """`@@` rows.  Kept as the name the 7878 reader established."""
    return rows_from_text(text, "@@")


def json_rows(raw: bytes, id_key: str = "id", name_key: str = "name") -> tuple:
    """`(rows, note)` from a JSON list of dicts, or raise `ValueError`.

    `rows` is `(id, label)` pairs in file order.  **Rows, not unique ids** --
    `magictype.json` carries 610 rows over 289 distinct `MagicType` values
    because a skill has one row per level, so deduplicating by id would report
    289 and lose more than half the table.  Where a table does have unique
    ids the two numbers coincide and nothing is lost either way.

    Fails CLOSED on a document that is not a list of objects: a JSON file that
    parses but is not this shape would otherwise yield an empty list, which
    reads as an empty table.
    """
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}") from None
    if not isinstance(doc, list):
        raise ValueError(f"expected a JSON list, got {type(doc).__name__}")
    if doc and not isinstance(doc[0], dict):
        raise ValueError(f"expected a list of objects, first entry is "
                         f"{type(doc[0]).__name__}")
    rows = []
    missing = 0
    for entry in doc:
        if not isinstance(entry, dict):
            continue
        if id_key not in entry:
            missing += 1
            continue
        rows.append((str(entry[id_key]), str(entry.get(name_key, ""))))
    if missing:
        raise ValueError(f"{missing} of {len(doc)} entries carry no "
                         f"{id_key!r} field")
    uniq = len({r[0] for r in rows})
    note = f"{len(rows)} rows, {uniq} distinct {id_key}"
    # The first ENTRY travels with the rows so the control can probe the
    # original typed values. Stringified ids and labels are not enough: a
    # numeric label of 0 stringifies to "0", which occurs everywhere in a JSON
    # table, so a control built from it would pass on any file at all.
    return rows, note, (doc[0] if doc and isinstance(doc[0], dict) else None)


def control_json_row(entry: Optional[dict], id_key: str, name_key: str,
                     witness: bytes) -> tuple:
    """Find the first row's id and label back in the undecoded file.

    JSON is plaintext, so this is the strong control -- but only if the probe
    is strong. Two ways it degrades, both measured on CCO:

      * `json.dumps` escapes non-ASCII by default, so `TerrainNpc.json`'s
        first name (Chinese) became `长灯` and matched nothing in a
        UTF-8 file. `ensure_ascii=False`.
      * A numeric value stringifies to something like `0`, which appears
        everywhere in a JSON table. Probing for it alone would pass on any
        file, which is a control that cannot fail.

    So the probe is the KEY AND VALUE together -- `"level": 1` with flexible
    whitespace -- which can only match if the file really carries that pair.
    """
    if not entry:
        return None, None

    def pair(key: str):
        if key not in entry:
            return None
        val = json.dumps(entry[key], ensure_ascii=False)
        return re.compile(
            re.escape(f'"{key}"').encode("utf-8") + rb"\s*:\s*"
            + re.escape(val).encode("utf-8"))

    probes = [p for p in (pair(id_key), pair(name_key)) if p is not None]
    if not probes:
        return None, None
    for rx in probes:
        if not rx.search(witness):
            return None, None
    ident = entry.get(id_key)
    label = entry.get(name_key, "")
    return (f"{id_key}={ident} {name_key}={label!r} -- the key/value pair is "
            f"in the raw bytes"), CONTROL_RAW


def gamemap_records(raw: bytes) -> tuple:
    """`(rows, note)` from a `GameMap.dat`, or raise `ValueError`.

    Fails CLOSED on any mismatch: a declared count the file does not carry, a
    length that runs past the end, or a single leftover byte all raise rather
    than returning the records it managed to read. A binary reader that
    returns a short list looks exactly like a small table, and there is no
    second instrument here to notice.
    """
    if len(raw) < 4:
        raise ValueError("shorter than its own count field")
    n = struct.unpack_from("<I", raw, 0)[0]
    pos = 4
    rows = []
    flags = {}
    for i in range(n):
        if pos + 8 > len(raw):
            raise ValueError(f"record {i} of {n} runs past the end")
        ident, length = struct.unpack_from("<II", raw, pos)
        pos += 8
        if pos + length + 4 > len(raw):
            raise ValueError(f"record {i} path length {length} runs past "
                             f"the end")
        path = raw[pos:pos + length].decode("latin1")
        pos += length
        flag = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
        flags[flag] = flags.get(flag, 0) + 1
        rows.append((str(ident), path))
    if pos != len(raw):
        raise ValueError(f"parsed {n} records and left {len(raw) - pos} "
                         f"byte(s) unconsumed")
    note = ", ".join(f"flag {k}: {v}" for k, v in sorted(flags.items()))
    return rows, note


def read_sections(path: Path, encoding: str = "gbk") -> tuple:
    """`sections_from_text` over a plaintext file on disk."""
    return sections_from_text(Path(path).read_text(encoding, errors="replace"))


def read_at_rows(path: Path, encoding: str = "gbk") -> list:
    """`at_rows_from_text` over a plaintext file on disk."""
    return at_rows_from_text(Path(path).read_text(encoding, errors="replace"))


def control_section(secs: dict, witness: bytes, encoding: str,
                    kind: str) -> tuple:
    """Open one section and find it again in bytes the parse did not produce.

    Returns `(text, control_kind)` or `(None, None)`.  `witness` is the raw
    file for a plaintext table and an independent re-decode for an enciphered
    one; `kind` says which, and travels onto the `Catalog` so the difference
    survives into the report.
    """
    if not secs:
        return None, None
    name = next(iter(secs))
    body = secs[name]
    if not body:
        return f"[{name}] (no keys)", kind
    key = next(iter(body))
    val = body[key]
    header = f"[{name}]".encode(encoding, "replace")
    pair = f"{key}={val}".encode(encoding, "replace")
    if header not in witness or pair not in witness:
        return None, None
    where = ("the raw bytes" if kind == CONTROL_RAW
             else "an independent re-decode")
    return f"[{name}] {key}={val} -- both found in {where}", kind


def flat_keys_from_text(text: str) -> tuple:
    """`({key: value}, {duplicated_key: count})` for a headerless key map.

    `[section]` lines are NOT tolerated here.  A file with headers is a
    `KIND_SECTIONS` table and reading it flat would silently merge every
    section's keys into one namespace, so a header is a parse error rather
    than a line to skip.

    First wins, as in `sections_from_text`, and for the same reason: Win32's
    `GetPrivateProfileString` returns the first, and duplicates are counted
    and returned rather than dropped so a caller can report *present* and
    *unique* instead of quietly serving the smaller number.
    """
    out: dict = {}
    dupes: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "//", "/", "--")):
            continue
        if _SECTION.match(line):
            raise ValueError(
                f"[{_SECTION.match(line).group(1)}] -- this file has section "
                f"headers, so it is not a flat key map; reading it flat would "
                f"merge every section's keys into one namespace")
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k:
            continue
        if k in out:
            dupes[k] = dupes.get(k, 1) + 1
            continue
        out[k] = v.strip()
    return out, dupes


def list_from_text(text: str) -> list:
    """Bare values, one per line, for a single-column list.

    A list is not a one-column table.  `immediate.ini` is 56,985 bare ids;
    calling those "rows" of a table implies a column that was never there and
    a schema nobody established.
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "//", "/", "--")):
            continue
        if _SECTION.match(line) or "=" in line:
            raise ValueError(
                "this file has headers or key=value lines, so it is not a "
                "bare list")
        out.append(line)
    return out


def control_flat_key(pairs: dict, witness: bytes, encoding: str,
                     kind: str) -> tuple:
    """Find one `key=value` the parse claims, back in `witness`."""
    if not pairs:
        return None, None
    key = next(iter(pairs))
    val = pairs[key]
    probe = f"{key}={val}".encode(encoding, "replace")
    if probe not in witness:
        return None, None
    where = ("the raw bytes" if kind == CONTROL_RAW
             else "an independent re-decode")
    return f"{key}={val} -- found in {where}", kind


def control_list_value(values: list, witness: bytes, encoding: str,
                       kind: str) -> tuple:
    """Find one listed value back in `witness`.

    Weaker than the other controls and SAID to be, because a bare value
    carries no structure to check: a short id like ``100`` occurs by chance in
    plenty of files, so finding it proves the line was read and little more.
    It is still worth taking -- it catches a wholly wrong decode -- but it must
    not be reported as though it were `control_section`'s evidence.
    """
    if not values:
        return None, None
    val = values[0]
    if val.encode(encoding, "replace") not in witness:
        return None, None
    where = ("the raw bytes" if kind == CONTROL_RAW
             else "an independent re-decode")
    return (f"{val} -- found in {where}; a bare value carries no structure, "
            f"so this witnesses the read and not the shape"), kind


def control_at_row(rows: list, witness: bytes, encoding: str, kind: str,
                   columns: dict, delimiter: Optional[str] = "@@") -> tuple:
    """Open one row and find its id and label again in `witness`.

    The probe is rebuilt with the table's own delimiter.  Searching for
    ``id@@name@@`` in a space-separated table finds nothing, and "nothing
    found" would then mean "wrong delimiter" while reading as "bad data" --
    which is how a control stops being a control and becomes noise.
    """
    if not rows:
        return None, None
    r = rows[0]
    i_id = columns.get("id", 0)
    i_name = columns.get("name", 1)
    if len(r) <= max(i_id, i_name):
        return None, None
    # **A text row's fields must be text.** Zephyr's `Tips.dat` is a binary
    # record table (a 32-byte name header, then {u32 id, u32 len, bytes}) that
    # `inidat` classifies `plaintext` because most of its bytes are printable.
    # Declared as space-rows it "parsed 2 rows" and the control PASSED --
    # because the two garbage tokens really are adjacent in the file, so
    # checking adjacency proved exactly nothing. Control characters in a field
    # are the positive evidence that this is not a text table at all.
    for field in (r[i_id], r[i_name]):
        if any(ord(c) < 0x20 and c not in "\t" for c in field):
            return None, None
    sep = " " if delimiter is None else delimiter
    # **The trailing separator is checked as a BOUNDARY, not required as a
    # literal.**
    #
    # This appended `sep` to the probe -- `id@@name@@` -- which is right for
    # 7878's `@@` tables, where every field including the last is terminated.
    # It is wrong for every space- or comma-separated table, where the last
    # field ends at the newline: the probe for `TxtColor.ini` was
    # `"<name> 0xffff0000 "` with a trailing space that is not in the file.
    #
    # So the control refused tables it should have passed -- 7 to 10 per build
    # (Cursor, Font, Nationality, TxtColor, TxtEmotion, WeaponSkillName,
    # ItemSolidify, ClassDesc, EmotionIco), every one of them a perfectly
    # readable table. A control that cries wolf that often stops being read,
    # which is worse than one that is merely strict.
    #
    # The boundary still has to hold, because that is what makes this a
    # control rather than a substring search: what follows the match must be
    # the delimiter or the end of the line, so `id name` cannot match inside
    # `id nameplate`.
    if delimiter is None:
        # **A whitespace run is not reconstructible, so it is MATCHED, not
        # rebuilt.** `line.split()` collapses any mix of spaces and tabs, so
        # a probe joined with one space cannot be found in a file that used
        # something else: `TxtEmotion.ini` separates with a TAB then a space
        # (`b"\xce\xde\t \xce\xde"`), and the single-space probe refused a
        # table that reads perfectly. Rejoining on the first run's exact
        # bytes would be worse -- it would pass this file and fail the next
        # one that varies its spacing, which most of these do.
        pat = (re.escape(r[i_id].encode(encoding, "replace")) + rb"[ \t]+" +
               re.escape(r[i_name].encode(encoding, "replace")) +
               rb"(?=[ \t\r\n]|$)")
        if not re.search(pat, witness):
            return None, None
    else:
        probe = f"{r[i_id]}{sep}{r[i_name]}".encode(encoding, "replace")
        at = witness.find(probe)
        if at < 0:
            return None, None
        tail = witness[at + len(probe):at + len(probe) + len(sep)]
        if tail and not (tail.startswith((b"\r", b"\n")) or
                         tail == sep.encode(encoding, "replace")):
            return None, None
    where = ("the raw bytes" if kind == CONTROL_RAW
             else "an independent re-decode")
    return (f"id={r[i_id]} name={r[i_name]!r} ({len(r)} fields) "
            f"-- found in {where}"), kind


def build_catalogs(specs, load: Callable, *, encoding: str,
                   columns: dict) -> dict:
    """Run every spec through `load` and turn each into a `Catalog`.

    `load(spec)` returns `(text, witness, control_kind, refusal)`:
    the decoded text, the bytes a control may search, which kind of control
    that makes it, and a refusal if the table could not be reached at all.
    Exactly one of `text` and `refusal` is expected to be set.

    **Nothing returns an empty collection silently.**  "this client has none"
    and "we could not read it" are different answers and only one of them is
    about the client, so a spec that cannot be read yields a `Catalog` with a
    refusal rather than a zero.
    """
    out: dict = {}
    for spec in specs:
        if spec.refusal:
            out[spec.subject] = Catalog(spec.subject, spec.display, spec.kind,
                                        refusal=spec.refusal)
            continue
        try:
            text, witness, ckind, refusal = load(spec)
        except OSError as e:
            out[spec.subject] = Catalog(spec.subject, spec.display, spec.kind,
                                        refusal=f"unreadable: {e}")
            continue
        if refusal is not None:
            out[spec.subject] = Catalog(spec.subject, spec.display, spec.kind,
                                        refusal=refusal)
            continue

        if spec.kind == KIND_JSON_ROWS:
            name_key = spec.label_key or "name"
            try:
                rows, note, first = json_rows(witness, spec.id_key, name_key)
            except ValueError as e:
                out[spec.subject] = Catalog(
                    spec.subject, spec.display, spec.kind,
                    refusal=f"{spec.display}: {e}")
                continue
            ctl, got = control_json_row(first, spec.id_key, name_key, witness)
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(rows),
                control=(f"{ctl}; {note}" if ctl else None),
                control_kind=got,
                refusal=None if ctl else
                        (f"{spec.display}: parsed {len(rows)} rows but no "
                         f"control could be checked back against the bytes, "
                         f"so the count is not evidence"))
            continue

        if spec.kind == KIND_GAMEMAP:
            # Reads the WITNESS, because the records are bytes rather than
            # text. The control is structural and it is a strong one: the
            # declared count, the parsed count and exact consumption of the
            # file all have to agree, and `gamemap_records` raises instead of
            # returning a short list if any of them does not.
            try:
                rows, note = gamemap_records(witness)
            except ValueError as e:
                out[spec.subject] = Catalog(
                    spec.subject, spec.display, spec.kind,
                    refusal=f"{spec.display}: {e}")
                continue
            first = f"id={rows[0][0]} path={rows[0][1]!r}" if rows else "empty"
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(rows),
                control=(f"{len(rows)} records, declared count matches and the "
                         f"parse consumes all {len(witness)} bytes exactly; "
                         f"{first}; {note}"),
                control_kind=ckind)
            continue

        if text is None:
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind,
                refusal=f"{spec.display}: no reader produced text")
            continue

        if spec.kind == KIND_SECTIONS:
            secs, dupes = sections_from_text(text)
            ctl, got = control_section(secs, witness, encoding, ckind)
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(secs),
                duplicated=dupes, control=ctl, control_kind=got,
                refusal=None if ctl else
                        (f"{spec.display}: parsed {len(secs)} sections but no "
                         f"control could be checked back against the bytes, "
                         f"so the count is not evidence"))
        elif spec.kind in ROW_KINDS:
            delim = ROW_KINDS[spec.kind]
            rows = rows_from_text(text, delim)
            ctl, got = control_at_row(rows, witness, encoding, ckind, columns,
                                      delim)
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(rows),
                control=ctl, control_kind=got,
                refusal=None if ctl else
                        (f"{spec.display}: parsed {len(rows)} rows but no "
                         f"control could be checked back against the bytes, "
                         f"so the count is not evidence"))
        elif spec.kind == KIND_FLAT_KEYS:
            try:
                pairs, dupes = flat_keys_from_text(text)
            except ValueError as e:
                # A header appearing in a table declared flat is a REAL
                # finding, not noise to skip: it means the build changed the
                # file's shape. `action3deffect.ini` is flat-keys on 5017-6090
                # and sections from 6609, and this is what says so out loud
                # instead of returning a plausible smaller number.
                out[spec.subject] = Catalog(
                    spec.subject, spec.display, spec.kind,
                    refusal=f"{spec.display}: {e}")
                continue
            ctl, got = control_flat_key(pairs, witness, encoding, ckind)
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(pairs),
                duplicated=dupes, control=ctl, control_kind=got,
                refusal=None if ctl else
                        (f"{spec.display}: parsed {len(pairs)} keys but no "
                         f"control could be checked back against the bytes, "
                         f"so the count is not evidence"))
        elif spec.kind == KIND_LIST:
            try:
                values = list_from_text(text)
            except ValueError as e:
                out[spec.subject] = Catalog(
                    spec.subject, spec.display, spec.kind,
                    refusal=f"{spec.display}: {e}")
                continue
            ctl, got = control_list_value(values, witness, encoding, ckind)
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind, rows=len(values),
                control=ctl, control_kind=got,
                refusal=None if ctl else
                        (f"{spec.display}: parsed {len(values)} values but no "
                         f"control could be checked back against the bytes, "
                         f"so the count is not evidence"))
        else:
            out[spec.subject] = Catalog(
                spec.subject, spec.display, spec.kind,
                refusal=f"{spec.display}: kind {spec.kind!r} has no reader here")
    return out


def no_tables_declared(plugin_name: str, why: str) -> dict:
    """The refusal a plugin that declares no tables returns.

    A blank dict is what a caller renders as "nothing here", which reads as a
    statement about the client.  It is a statement about US.  This says so,
    and names the plugin, because the last time a catalog surface went empty
    the message blamed the owner's install for a bug in `_plugin_for`.
    """
    return {"(no tables)": Catalog(
        "(no tables)", plugin_name, "none",
        refusal=f"{plugin_name} declares no browsable tables: {why}")}
