#!/usr/bin/env python3
r"""
colibrary.py -- CLI over the COmmunity Library's server profiles.

    py -3 tools/colibrary.py servers
    py -3 tools/colibrary.py show zephyr
    py -3 tools/colibrary.py materialize zephyr --to "D:\CO Servers\zephyr"

`materialize` writes a server's **combined asset tree** -- every logical path
its client ships, with the bytes pulled from wherever they live now (library
or baseline) -- as plain files, so anything that expects a full client-shaped
directory can be pointed at it.

The destination is guarded:

  * never anywhere under a "Program Files" directory -- the baseline install
    is a read-only *source*; a combined tree is a local working copy and
    lives in a normal local directory;
  * never inside the baseline install or the library repo itself (the first
    is the read-only boundary, the second would bloat the repo with
    duplicates of what it already stores once).

--library defaults to the path the viewer remembered (per-user config).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                     # noqa: E402
from colibrary import ServerView, list_servers    # noqa: E402
from assetdiff import fs_safe                     # noqa: E402


def _default_library() -> str | None:
    return coroot.read_settings().get("community_library")


def _library(args) -> Path:
    lib = args.library or _default_library()
    if not lib:
        sys.exit("no --library given and none remembered "
                 "(run the viewer once with --library DIR)")
    p = Path(lib)
    if not (p / "servers").is_dir():
        sys.exit(f"{p} has no servers/ directory -- not a COmmunity Library, "
                 "or nothing has been imported with assetdiff --server yet")
    return p


def cmd_servers(args) -> int:
    lib = _library(args)
    names = list_servers(lib)
    if not names:
        print("no server profiles catalogued")
        return 1
    for n in names:
        prof = {}
        pf = lib / "servers" / n / "profile.json"
        if pf.is_file():
            prof = json.loads(pf.read_text("utf-8"))
        print(f"  {n:<16} v{prof.get('clientVersion', '?'):<6} "
              f"{prof.get('files', '?'):>8} paths   "
              f"imported {prof.get('importedAt', '?')}")
    return 0


def cmd_show(args) -> int:
    lib = _library(args)
    pf = lib / "servers" / args.name / "profile.json"
    if not pf.is_file():
        sys.exit(f"no profile {args.name!r} (have: "
                 + (", ".join(list_servers(lib)) or "none") + ")")
    print(pf.read_text("utf-8"))
    return 0


def _refuse_destination(dest: Path, base_root: Path, library: Path) -> None:
    d = dest.resolve()
    for part in d.parts:
        if part.lower().startswith("program files"):
            sys.exit(f"refusing to materialize under {d}:\n"
                     "  a combined tree never goes inside Program Files -- "
                     "the install there is a read-only source.\n"
                     "  Pick a local directory, e.g. under your user profile "
                     "or a data drive.")
    for guard, why in ((base_root, "that is the baseline install "
                                   "(read-only boundary)"),
                       (library, "that is the library repo itself -- it "
                                 "already stores these bytes once")):
        g = guard.resolve()
        if d == g or g in d.parents or d in g.parents:
            sys.exit(f"refusing to materialize at {d}: {why}")


def cmd_rebuild_tables(args) -> int:
    lib = _library(args)
    view = ServerView(lib, args.name, args.root)
    wrote = view.write_synthesized_tables()
    for w in wrote:
        print(f"wrote {w}")
    if not wrote:
        print("nothing to synthesize (real tables present, or no "
              "recognisable convention)")
    return 0


def cmd_materialize(args) -> int:
    lib = _library(args)
    dest = Path(args.to)
    view = ServerView(lib, args.name, args.root)
    _refuse_destination(dest, view.root, lib)
    if dest.exists() and any(dest.iterdir()) and not args.force:
        sys.exit(f"{dest} exists and is not empty (pass --force to merge "
                 "into it)")
    paths = view.logical_paths()
    print(f"materializing {len(paths)} files -> {dest}")
    t0 = time.time()
    written = missing = 0
    for i, logical in enumerate(paths, 1):
        try:
            data = view.read(logical)
        except (FileNotFoundError, KeyError):
            missing += 1
            continue
        out = dest / fs_safe(logical)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        written += 1
        if i % 5000 == 0:
            print(f"  {i}/{len(paths)}  ({time.time()-t0:.0f}s)", flush=True)
    print(f"done: {written} written, {missing} unresolvable, "
          f"{time.time()-t0:.0f}s")
    return 0 if missing == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--library", metavar="DIR",
                    help="COmmunity Library root (default: the one the "
                         "viewer remembered)")
    coroot.add_root_argument(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("servers", help="list catalogued server profiles")
    p.set_defaults(func=cmd_servers)

    p = sub.add_parser("show", help="print one profile")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("rebuild-tables",
                       help="derive missing linkage tables (old-client body "
                            "convention -> armor.ini) into the profile")
    p.add_argument("name")
    p.set_defaults(func=cmd_rebuild_tables)

    p = sub.add_parser("materialize",
                       help="write a server's combined asset tree as plain "
                            "files (guarded: local directories only)")
    p.add_argument("name")
    p.add_argument("--to", required=True, metavar="DIR")
    p.add_argument("--force", action="store_true",
                   help="merge into a non-empty destination")
    p.set_defaults(func=cmd_materialize)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
