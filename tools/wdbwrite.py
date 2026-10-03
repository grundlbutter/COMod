#!/usr/bin/env python3
r"""
wdbwrite.py -- read, verify and WRITE the ``ini/c3.wdb`` container.

Subcommands (all read-only except ``repack``, which writes only where you
point it)::

    py -3 tools/wdbwrite.py list <c3.wdb>
        the file-entry index: name, offset, size, magic.

    py -3 tools/wdbwrite.py verify [<c3.wdb> ...]
        the integrity columns for each file, plus the byte-identical
        round-trip. With no arguments it sweeps every client on this machine
        and the vendor tool's three copies. THIS IS THE GATE.

    py -3 tools/wdbwrite.py extract <c3.wdb> <dir>
        every member as a file, named after its index entry.

    py -3 tools/wdbwrite.py repack <c3.wdb> <out.wdb> [--replace name=path]...
                                                     [--add name=path]...
                                                     [--drop name]...
        rewrite the container. With no edits the output is byte-identical to
        the input, which is the same assertion ``verify`` makes.

**WHAT A WRITER IS ACTUALLY FOR, and it is worth being blunt.** The client
reads a LOOSE ``ini/*.ini`` before it reads the archive, so ordinary content
modding never repacks anything -- `tools/comod.py` installs and reverts
entirely by loose-file override, and that will keep being the right tool.
What this buys that the loose path cannot:

  * **adding a member** the index does not already name, or **dropping one**;
  * **changing a member's size** -- the index carries offset and size, so
    growing a section means rewriting every later offset and the whole
    obfuscated index, which is exactly the part that was not possible before;
  * a **byte-identical baseline**, so a diff of a rebuilt file against the
    shipped one has a known-zero floor and any difference is a real edit.

If you only want to change a value inside a table, use the loose file.

The container format and where every constant came from are documented in
`core/wdbpack.py`; the reverse engineering is in
``docs/wdb_packer_re_2026-09-06.md``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import wdbpack  # noqa: E402

VENDOR_RELPATHS = ("ini/c3.wdb", "ini/c3.wdb.old", "empty-env-template/ini/c3.wdb")


def _vendor_root():
    """The C3Tools directory, if it is where it has always been. Optional --
    the sweep reports it as absent rather than failing."""
    p = Path.home() / "Downloads" / "C3Tools"
    return p if p.is_dir() else None


def corpus():
    """Every ``ini/c3.wdb`` this machine has, as ``(label, Path)``."""
    import coroot
    out = []
    root = Path(coroot.clients_dir())
    if root.is_dir():
        for d in sorted(root.iterdir()):
            p = d / "ini" / "c3.wdb"
            if p.is_file():
                out.append((d.name, p))
    v = _vendor_root()
    if v:
        for rel in VENDOR_RELPATHS:
            p = v / rel
            if p.is_file():
                out.append(("C3Tools:" + rel, p))
    return out


def cmd_list(a):
    pk = wdbpack.Pack(a.path.read_bytes())
    print("%s  %d bytes  index@0x%x  %d members"
          % (a.path, len(pk.data), pk.index_off, pk.count))
    for m in pk.by_offset():
        print("  %08x  %-26s off=%9d size=%9d magic=%r"
              % (m.hash, m.name, m.off, m.size,
                 bytes(pk.data[m.off:m.off + 4])))
    return 0


def cmd_extract(a):
    pk = wdbpack.Pack(a.path.read_bytes())
    a.out.mkdir(parents=True, exist_ok=True)
    for m in pk.members:
        dest = a.out / Path(m.name).name
        dest.write_bytes(pk.blob(m))
        print("  %-26s -> %s (%d bytes)" % (m.name, dest, m.size))
    return 0


COLUMNS = ("hash_ok", "name_ok", "size_pair_ok", "zero_ok")
FLAGS = ("index_len_ok", "declared_size_ok", "tiles", "covered_ok", "sorted_ok")


def cmd_verify(a):
    files = [(str(p), p) for p in a.paths] if a.paths else corpus()
    if not files:
        print("no c3.wdb found -- nothing measured, which is NOT a pass")
        return 2
    print("%-26s %10s %5s %8s %8s %8s %8s  %s  %s"
          % ("file", "bytes", "n", "hash", "name", "sz==sz2", "5th==0",
             "flags", "roundtrip"))
    bad = 0
    total_members = 0
    for label, p in files:
        data = p.read_bytes()
        try:
            pk = wdbpack.Pack(data)
        except ValueError as e:
            print("%-26s %10d  PARSE FAILED: %s" % (label, len(data), e))
            bad += 1
            continue
        au = pk.audit()
        n = au["count"]
        total_members += n
        rt = wdbpack.rebuild(pk) == data
        flags = "".join("." if au[f] else "X" for f in FLAGS)
        ok = all(au[c] == n for c in COLUMNS) and all(au[f] for f in FLAGS) and rt
        print("%-26s %10d %5d %8s %8s %8s %8s  %-5s  %s%s"
              % (label, len(data), n,
                 "%d/%d" % (au["hash_ok"], n), "%d/%d" % (au["name_ok"], n),
                 "%d/%d" % (au["size_pair_ok"], n), "%d/%d" % (au["zero_ok"], n),
                 flags, rt, "" if ok else "   <== FAIL"))
        if not ok:
            bad += 1
    print("\nflags are %s" % " ".join(FLAGS))
    print("%d files, %d members, %d failing" % (len(files), total_members, bad))
    if total_members == 0:
        print("ZERO MEMBERS READ -- a clean sweep over nothing is not a result")
        return 2
    return 1 if bad else 0


def _pairs(items):
    out = []
    for it in items or ():
        name, _, path = it.partition("=")
        if not path:
            raise SystemExit("expected name=path, got %r" % it)
        out.append((name, Path(path)))
    return out


def cmd_repack(a):
    pk = wdbpack.Pack(a.path.read_bytes())
    repl = dict(_pairs(a.replace))
    add = _pairs(a.add)
    drop = set(a.drop or ())
    members = []
    for m in pk.by_offset():
        if m.name in drop:
            continue
        blob = repl[m.name].read_bytes() if m.name in repl else pk.blob(m)
        members.append((m.name, blob, m.name_pad))
    known = {m[0] for m in members}
    for name, path in add:
        if name in known:
            raise SystemExit("--add %s: already present; use --replace" % name)
        members.append((name, path.read_bytes(), b""))
    out = wdbpack.build(members)
    if not (repl or add or drop) and out != a.path.read_bytes():
        raise SystemExit("refusing to write: an edit-free repack did not "
                         "reproduce the input byte for byte")
    a.out.write_bytes(out)
    print("wrote %s: %d members, %d bytes" % (a.out, len(members), len(out)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list")
    s.add_argument("path", type=Path)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("verify")
    s.add_argument("paths", nargs="*", type=Path)
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("extract")
    s.add_argument("path", type=Path)
    s.add_argument("out", type=Path)
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("repack")
    s.add_argument("path", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--replace", action="append", metavar="NAME=PATH")
    s.add_argument("--add", action="append", metavar="NAME=PATH")
    s.add_argument("--drop", action="append", metavar="NAME")
    s.set_defaults(fn=cmd_repack)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
