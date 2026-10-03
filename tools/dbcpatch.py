#!/usr/bin/env python3
r"""
dbcpatch.py -- change ONE field of ONE part element inside a ``ini/c3.wdb``
member, and prove the change is the only difference in the file.

This is the smallest edit the container supports, and it is deliberately the
smallest: `tools/wdbwrite.py` can add, drop and resize members, which moves
every later member and rewrites the whole obfuscated index. Nothing here
resizes anything -- an element is replaced in place, the member keeps its
length, every offset in the index keeps its value, and the output therefore
differs from the input in **exactly the dwords named on the command line**.
That property is the deliverable, not a side effect: it is what makes a launch
of the patched tree an experiment with one variable.

    py -3 tools/dbcpatch.py show <c3.wdb> <member> [<id> ...]
    py -3 tools/dbcpatch.py roundtrip <c3.wdb>
    py -3 tools/dbcpatch.py set <c3.wdb> <out.wdb>
            --member ini/armor.dbc --id 1130000 --set texture=1130390
    py -3 tools/dbcpatch.py setfile <in.dbc> <out.dbc>
            --id 20001 --set part=9998810 --set texture=9998810

THE THREE CLAIMS, PROVED SEPARATELY, because a later one standing in for an
earlier one is how a decode that never worked ships:

  1. **the member decodes to records** -- `dbc.read_parts` walks it and lands
     on the member's own end, and the decoded ids agree with the client's own
     loose ``.ini`` twin (`--oracle`);
  2. **one element re-encodes to bytes we PREDICT** -- ``set`` prints the
     before/after element bytes and the container offset of every changed
     dword, computed from the field layout rather than read back from the
     output;
  3. **the container repacks byte-exactly around it** -- ``roundtrip``, and
     `tests/test_wdb_container.py` already gates the general case.

WHY THE CONTAINER IS THE THING TO EDIT -- and the one case where editing it
alone is NOT enough.  Read out of ``6090/GraphicData.dll`` by static
disassembly; no binary was executed or loaded.

**The loaders do not fall back to the loose file.**  The MESH loader (RVA
0x1d5a1) and the SIMO loader (0x1eb75) each call
``GameDBPack::FindFileEntry`` (0x2a7f0), which hashes the name and looks it up
in the member map and nothing else; on a miss the SIMO loader prints
``open file %s failed.`` and returns, and the MESH loader returns. So the
loose ``ini/armor.dbc`` and ``ini/3DSimpleObj.dbc`` that 6090 also ships are
never *read*, and the compiled table the client draws from is the c3.wdb
member.  The name ``ini/armor.dbc`` appears in no binary on the install: it
comes out of ``ini/RolePart.dbc`` (``part "body" -> mesh_ini
"ini/armor.dbc"``), which is itself a c3.wdb member.

**But a startup pass REWRITES c3.wdb from the loose files, for twelve names
only.**  ``GameDBPack::Update`` (0x29c30, reached through the module's
GameData interface, no direct caller) walks the table at RVA **0x32388** --
twelve ``{char* name, u32 flag}`` entries -- and for each one calls
``GetFileAttributesA`` on the loose path (0x29c10).  Where the file EXISTS it
is read whole and written into ``ini/c3.wdb.tmp`` as that member (0x2a166);
where it does not, the old member's bytes are carried over.  The tmp is then
moved over ``c3.wdb`` (the old one to ``c3.wdb.sav``) and **every merged loose
file is deleted** (``DeleteFileA``, 0x2a65e).

    the twelve: 3DObj, 3deffectobj, 3DTexture, 3dscene, sound, 3DObjProp,
    3DEffect, 3DSimpleObj, 3DSimpleObjEx, Material, EmotionIco, RolePart

``ini/armor.dbc`` is NOT among them, so a MESH edit stands.
``ini/3DSimpleObj.dbc`` IS, and 6090 ships that loose file (6,248 B, 388
records, against the member's 3,440 B and 210), so an edit to the SIMO member
alone is overwritten the first time the client starts.  **Patch the loose twin
too** (``setfile``) whenever the member is one of the twelve.  Corroborating,
not proof: of the **30** installed builds whose ``ini/`` holds a ``c3.wdb``,
**21** also ship five or six of those twelve loose ``.dbc`` (6090 is the only
one with six) and **9** ship none -- the shape a merge-and-delete leaves.

Read-only except for the output path.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import dbc              # noqa: E402
import wdbpack          # noqa: E402


class PatchError(ValueError):
    """A refusal. Every one of these is a case where guessing would produce a
    file that still parses and no longer means what the caller asked for."""


def member_blob(pack: wdbpack.Pack, name: str):
    """``(Member, blob)`` for a member, by its index name."""
    by = pack.by_name()
    if name not in by:
        raise PatchError(
            "%s holds no member named %r. It holds: %s"
            % ("this container", name, ", ".join(sorted(by))))
    m = by[name]
    return m, pack.blob(m)


def record_index(table: dict, rid: int) -> int:
    """The one record with id `rid`, or a refusal.

    **Ids are not unique in these tables** -- `dbc.read_parts`' docstring
    measures 12 MESH sections and 2 SIMO sections with a duplicate id, and the
    dict-shaped readers silently keep the last. Editing "the" record of a
    duplicated id is not a defined operation, so this refuses instead of
    picking one; a caller that means a specific one can index the list itself.
    """
    hits = [i for i, r in enumerate(table["records"]) if r["id"] == rid]
    if not hits:
        raise PatchError("no record with id %d in this %s member (%d records)"
                         % (rid, table["tag"], len(table["records"])))
    if len(hits) > 1:
        raise PatchError(
            "id %d appears %d times (records %s). A duplicated id has no "
            "single record to edit; name the record index instead."
            % (rid, len(hits), hits))
    return hits[0]


def element_offset(table: dict, index: int, element: int) -> int:
    """Byte offset of one element from the START OF THE MEMBER.

    Recomputed by walking the record headers rather than remembered from the
    read, so the number the diff is checked against comes from the format and
    not from the reader's bookkeeping.
    """
    pos = 8
    for i, r in enumerate(table["records"]):
        if i == index:
            if not 0 <= element < len(r["elements"]):
                raise PatchError(
                    "record %d (id %d) has %d element(s); asked for %d"
                    % (i, r["id"], len(r["elements"]), element))
            return pos + 8 + element * table["element"]
        pos += 8 + len(r["elements"]) * table["element"]
    raise PatchError("record index %d past the %d records"
                     % (index, len(table["records"])))


def patch_element(blob: bytes, rid: int, assignments: dict, element: int = 0):
    """Replace named fields of one part element. Returns ``(blob, report)``.

    `report` carries, per changed field, the offset **within the member**, the
    old and the new little-endian dword -- computed from `dbc.PART_FIELDS`
    before the new blob exists, so comparing it against the actual diff is a
    real check and not a restatement.
    """
    table = dbc.read_parts(blob)
    if table["span"] != len(blob):
        raise PatchError(
            "%s member: the record walk ends at %d of %d bytes. A table that "
            "does not tile its own member was misread, and every offset below "
            "would be wrong." % (table["tag"], table["span"], len(blob)))
    idx = record_index(table, rid)
    size = table["element"]
    # BOUNDS AND RANGE FIRST, and both as refusals rather than as whatever
    # Python raises three lines later. `struct.error` and `IndexError` carry
    # nothing about which record or which field, and a caller catching
    # `PatchError` would let them through as a crash.
    base = element_offset(table, idx, element)
    raw = table["records"][idx]["elements"][element]
    fields = dbc.part_fields(raw)
    unknown = sorted(set(assignments) - set(fields))
    if unknown:
        raise PatchError(
            "no field %s in a %d-byte %s element. Its fields are: %s"
            % (", ".join(repr(u) for u in unknown), size, table["tag"],
               ", ".join(fields)))
    layout = dbc.PART_FIELDS[size]
    kinds = dict(layout)
    for name, value in assignments.items():
        if kinds[name] == "I" and not 0 <= int(value) <= 0xFFFFFFFF:
            raise PatchError("%s id %d field %s: %d does not fit a u32"
                             % (table["tag"], rid, name, value))
    report = []
    for pos, (name, kind) in enumerate(layout):
        if name not in assignments:
            continue
        old, new = fields[name], assignments[name]
        if old == new:
            raise PatchError(
                "%s id %d field %s is already %d. An edit that changes nothing "
                "cannot be told from an edit that never landed."
                % (table["tag"], rid, name, new))
        fields[name] = new
        report.append({"field": name, "member_off": base + pos * 4,
                       # defaults to the member offset so a STANDALONE .dbc
                       # needs no fixup; `apply` rebases it onto the container.
                       "file_off": base + pos * 4,
                       "old": old, "new": new,
                       "old_bytes": struct.pack("<" + kind, old),
                       "new_bytes": struct.pack("<" + kind, new)})
    table["records"][idx]["elements"][element] = dbc.part_bytes(fields, size)
    out = dbc.serialize_parts(table)
    if len(out) != len(blob):
        raise PatchError(
            "the rewritten member is %d bytes against %d. An in-place field "
            "edit must not change the length; something resized a record."
            % (len(out), len(blob)))
    return out, report


def replace_member(pack: wdbpack.Pack, name: str, blob: bytes) -> bytes:
    """Rebuild the container with one member's bytes replaced.

    Physical order and every member's ``name_pad`` are preserved, which is
    what `wdbpack.rebuild` does for an unchanged file; with `blob` equal to
    the member's own bytes this IS `wdbpack.rebuild`.
    """
    return wdbpack.build([(m.name, blob if m.name == name else pack.blob(m),
                           m.name_pad) for m in pack.by_offset()])


def byte_diff(a: bytes, b: bytes):
    """``[(offset, len)]`` of the runs where two equal-length buffers differ."""
    if len(a) != len(b):
        raise PatchError("lengths differ: %d against %d" % (len(a), len(b)))
    runs, start = [], None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            runs.append((start, i - start))
            start = None
    if start is not None:
        runs.append((start, len(a) - start))
    return runs


def predicted_runs(report):
    """``[(offset, len)]`` the file MUST differ in, from the report alone.

    **Predicted per BYTE, not per dword, and the difference is the whole
    point.** ``1130300 -> 1130390`` moves one byte of a four-byte field: a
    prediction of "the dword differs" is wrong on three of those bytes and
    would have to be relaxed to "the diff is *inside* the dword" to pass --
    which is a weaker claim that a neighbouring corruption could also satisfy.
    Predicting the exact byte set makes the check tight in both directions: a
    byte that should have changed and did not fails it just as a byte that
    should not have changed and did.

    Derived from the old and new field values through the field layout,
    before the patched file exists; nothing here reads the output.
    """
    want = set()
    for r in report:
        for i, (o, n) in enumerate(zip(r["old_bytes"], r["new_bytes"])):
            if o != n:
                want.add(r["file_off"] + i)
    runs, cur = [], None
    for off in sorted(want):
        if cur and cur[0] + cur[1] == off:
            cur = (cur[0], cur[1] + 1)
        else:
            if cur:
                runs.append(cur)
            cur = (off, 1)
    if cur:
        runs.append(cur)
    return runs


def apply(data: bytes, name: str, rid: int, assignments: dict,
          element: int = 0):
    """The whole operation: ``(new container, report, diff runs)``.

    The diff is taken against the input file, so it includes anything the
    container rebuild itself perturbs. On a shipped file that is nothing --
    `tests/test_wdb_container.py` gates the byte-identical rebuild -- which is
    exactly why the diff can be read as "the edit and nothing else".
    """
    pack = wdbpack.Pack(data)
    m, blob = member_blob(pack, name)
    new_blob, report = patch_element(blob, rid, assignments, element)
    out = replace_member(pack, name, new_blob)
    for r in report:
        r["file_off"] = m.off + r["member_off"]
    return out, report, byte_diff(data, out)


# -- the loose-ini oracle ---------------------------------------------------

def ini_rows(path: Path, prefix: str = "") -> dict:
    """``{section id: {key: value}}`` from one of the client's plain inis.

    `prefix` strips ``ObjIDType`` off ``3DSimpleObj.ini``'s section names;
    ``armor.ini`` names its sections with the bare id.
    """
    out, cur = {}, None
    for line in path.read_text("latin-1").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            body = line[1:-1]
            if prefix and body.startswith(prefix):
                body = body[len(prefix):]
            cur = out.setdefault(int(body), {}) if body.isdigit() else None
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur[k.strip()] = v.strip()
    return out


#: ``member magic -> (loose ini filename, prefix, {element field: ini key})``.
#: The ini is the client's own uncompiled statement of the same table, so
#: agreement is evidence from a source the decoder never touched.
ORACLES = {
    "MESH": ("armor.ini", "", {"mesh": "Mesh0", "texture": "Texture0",
                               "mixtex": "MixTex0"}),
    "SIMO": ("3DSimpleObj.ini", "ObjIDType", {"part": "Part0",
                                              "texture": "Texture0"}),
}


def oracle_check(table: dict, ini_path: Path):
    """Compare every single-element record against the loose ini.

    Returns ``(shared, agree, disagree, table_only, ini_only)``. A member and
    its ini need not cover the same ids -- on 6090 ``armor.ini`` holds 2,349
    rows the compiled member does not -- so the number that matters is
    disagreement on the SHARED ids, and `shared` being non-zero is what keeps
    a 0-disagreement answer from being vacuous.
    """
    fname, prefix, keymap = ORACLES[table["tag"]]
    rows = ini_rows(ini_path, prefix)
    tab = {}
    for r in table["records"]:
        if len(r["elements"]) == 1:
            tab[r["id"]] = dbc.part_fields(r["elements"][0])
    shared = sorted(set(tab) & set(rows))
    agree, disagree = 0, []
    for rid in shared:
        f, row = tab[rid], rows[rid]
        bad = [(k, f[k], row.get(v)) for k, v in keymap.items()
               if v in row and int(row[v]) != f[k]]
        if bad:
            disagree.append((rid, bad))
        else:
            agree += 1
    return (len(shared), agree, disagree,
            len(set(tab) - set(rows)), len(set(rows) - set(tab)))


# -- CLI --------------------------------------------------------------------

def _fmt(report):
    for r in report:
        print("    %-8s %10d -> %-10d  member+0x%05x  file+0x%06x  "
              "%s -> %s" % (r["field"], r["old"], r["new"], r["member_off"],
                            r["file_off"], r["old_bytes"].hex(),
                            r["new_bytes"].hex()))


def cmd_show(a):
    pack = wdbpack.Pack(a.path.read_bytes())
    m, blob = member_blob(pack, a.member)
    table = dbc.read_parts(blob)
    print("%s  %s  %d records  element %d B  span %d/%d"
          % (a.member, table["tag"], len(table["records"]), table["element"],
             table["span"], len(blob)))
    for rid in a.ids:
        i = record_index(table, rid)
        for j, e in enumerate(table["records"][i]["elements"]):
            print("  id %d element %d @ member+0x%05x  %s"
                  % (rid, j, element_offset(table, i, j), e.hex()))
            print("     ", dbc.part_fields(e))
    if a.oracle and table["tag"] in ORACLES:
        ini = a.path.parent / ORACLES[table["tag"]][0]
        if ini.is_file():
            sh, ag, dis, to, io = oracle_check(table, ini)
            print("  oracle %s: %d shared, %d agree, %d disagree, "
                  "%d table-only, %d ini-only"
                  % (ini.name, sh, ag, len(dis), to, io))
    return 0


def cmd_roundtrip(a):
    data = a.path.read_bytes()
    pack = wdbpack.Pack(data)
    bad = 0
    for m in pack.by_offset():
        blob = pack.blob(m)
        tag = bytes(blob[:4])
        if tag not in dbc.PART_ELEMENT_SIZES:
            continue
        t = dbc.read_parts(blob)
        ok = dbc.serialize_parts(t) == blob and t["span"] == len(blob)
        bad += not ok
        print("  %-24s %s %5d rec  %s" % (m.name, t["tag"],
                                          len(t["records"]),
                                          "OK" if ok else "MISMATCH"))
    same = wdbpack.rebuild(pack) == data
    print("  container rebuild byte-identical: %s" % same)
    return 0 if not bad and same else 1


def cmd_set(a):
    assignments = {}
    for item in a.set or []:
        k, _, v = item.partition("=")
        assignments[k.strip()] = int(v, 0)
    if not assignments:
        raise PatchError("--set is required")
    data = a.path.read_bytes()
    out, report, runs = apply(data, a.member, a.id, assignments, a.element)
    print("%s  id %d  element %d" % (a.member, a.id, a.element))
    _fmt(report)
    print("  container: %d bytes in, %d out" % (len(data), len(out)))
    print("  differing runs: %s"
          % ", ".join("0x%06x+%d" % r for r in runs))
    merged = predicted_runs(report)
    ok = runs == merged
    print("  predicted runs: %s -- %s"
          % (", ".join("0x%06x+%d" % r for r in merged),
             "MATCHES" if ok else "DOES NOT MATCH"))
    if not ok:
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(out)
    print("  wrote %s" % a.out)
    return 0


def cmd_setfile(a):
    """The same edit against a STANDALONE ``.dbc``, not a container member.

    Needed because the client merges twelve named loose ``ini/*.dbc`` into
    ``c3.wdb`` at startup and then deletes them -- the table at
    ``GraphicData.dll`` RVA 0x32388, described in this module's header. A tree
    that patches only the container is silently reverted by that pass for any
    member on that list, so the loose twin has to be reachable by the same
    writer.
    """
    assignments = {}
    for item in a.set or []:
        k, _, v = item.partition("=")
        assignments[k.strip()] = int(v, 0)
    if not assignments:
        raise PatchError("--set is required")
    data = a.path.read_bytes()
    out, report = patch_element(data, a.id, assignments, a.element)
    runs = byte_diff(data, out)
    print("%s  id %d  element %d" % (a.path.name, a.id, a.element))
    _fmt(report)
    merged = predicted_runs(report)
    print("  differing runs: %s" % ", ".join("0x%06x+%d" % r for r in runs))
    print("  predicted runs: %s -- %s"
          % (", ".join("0x%06x+%d" % r for r in merged),
             "MATCHES" if runs == merged else "DOES NOT MATCH"))
    if runs != merged:
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(out)
    print("  wrote %s" % a.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show")
    s.add_argument("path", type=Path)
    s.add_argument("member")
    s.add_argument("ids", nargs="*", type=lambda x: int(x, 0))
    s.add_argument("--oracle", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("roundtrip")
    s.add_argument("path", type=Path)
    s.set_defaults(fn=cmd_roundtrip)

    s = sub.add_parser("set")
    s.add_argument("path", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--member", required=True)
    s.add_argument("--id", required=True, type=lambda x: int(x, 0))
    s.add_argument("--element", type=int, default=0)
    s.add_argument("--set", action="append", metavar="FIELD=VALUE")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("setfile")
    s.add_argument("path", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--id", required=True, type=lambda x: int(x, 0))
    s.add_argument("--element", type=int, default=0)
    s.add_argument("--set", action="append", metavar="FIELD=VALUE")
    s.set_defaults(fn=cmd_setfile)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except PatchError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
