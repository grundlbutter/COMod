#!/usr/bin/env python3
"""
comod.py -- graphics modding workbench for Conquer Online.

The whole workflow rests on one verified fact: the client resolves an asset by
trying a loose file on disk *first*, and only falls back to the .wdf archives if
that misses (TqPackage!TqFOpen, see coassets.AssetRoot). So a mod is just a file
tree that mirrors the install layout. The archives are never modified.

    stage/                       <- your mod, built here
      c3/texture/001130200.dds
      c3/mesh/422000.c3

Installing copies that tree into the install root. Uninstalling removes exactly
the files the manifest recorded, restoring any original loose file it displaced.

Typical session:

    py -3 tools/comod.py find-item "IronHelmet"
    py -3 tools/comod.py show 002135300
    py -3 tools/comod.py extract c3/texture/002135300.dds --png
    #   ... edit the .png in any image editor ...
    py -3 tools/comod.py import-png work/002135300.png c3/texture/002135300.dds
    py -3 tools/comod.py diff
    py -3 tools/comod.py install --dry-run

Nothing writes to the game install except `install` and `uninstall`, and both
refuse to run without --yes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import safepath                   # noqa: E402
from coassets import (            # noqa: E402
    DEFAULT_ROOT, AssetRoot, C3File, DMap, dds_info, find_items,
)

PROJECT = Path(__file__).resolve().parent.parent
STAGE = PROJECT / "mods" / "stage"
WORK = PROJECT / "mods" / "work"
BACKUP = PROJECT / "mods" / "backup"
MANIFEST = PROJECT / "mods" / "manifest.json"


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        sys.exit("Pillow is required for image conversion (py -3 -m pip install Pillow)")


# ---------------------------------------------------------------------------
# inspection
# ---------------------------------------------------------------------------

def cmd_find_item(args) -> int:
    hits = find_items(args.query, Path(args.root))
    for it in hits[:args.limit]:
        print(f"  {it.get('id'):>10}  {it.get('name','')}")
    print(f"\n{len(hits)} match(es)" + (f", showing {args.limit}" if len(hits) > args.limit else ""))
    if hits:
        print("\nNote: itemtype.json IDs are gameplay IDs. The 3D look is keyed by")
        print("appearance ID in armor.ini / weapon.ini / armet.ini -- use `show <id>`")
        print("to resolve one, and `tables` to list what is available.")
    return 0


def cmd_tables(args) -> int:
    with AssetRoot(args.root) as R:
        for part, ini in sorted(R.part_tables().items()):
            print(f"  {part:16s} {ini.name:14s} {len(ini):6d} appearances")
    return 0


def cmd_show(args) -> int:
    with AssetRoot(args.root) as R:
        res = R.resolve_appearance(args.ident, args.table)
        if not res:
            print(f"appearance {args.ident!r} not found in any part table.")
            print("try: py -3 tools/comod.py tables")
            return 1
        for r in res:
            print(f"\n[{r['ident']}] in {r['ini']}  (part: {r['part']})")
            for p in r["parts"]:
                print(f"  part {p['index']}  material={p['material']}")
                for kind in ("mesh", "texture"):
                    loc = p[kind]
                    ident = p[f"{kind}_id"]
                    if loc is None:
                        print(f"     {kind:8s} {ident:>10}  -> NOT FOUND")
                    else:
                        extra = ""
                        if kind == "texture":
                            try:
                                info = dds_info(R.read(loc.logical))
                                extra = f"  {info}"
                            except Exception:
                                pass
                        print(f"     {kind:8s} {ident:>10}  -> {loc}{extra}")
    return 0


def cmd_info(args) -> int:
    with AssetRoot(args.root) as R:
        loc = R.locate(args.logical)
        if not loc:
            print(f"not found: {args.logical}")
            return 1
        data = R.read(args.logical)
        print(loc)
        if data[:4] == b"DDS ":
            print("  ", dds_info(data))
        elif data.startswith(b"MAXFILE"):
            c3 = C3File(data)
            print(f"   C3 container, {len(c3)} chunks")
            for i, ch in enumerate(c3.chunks):
                nm = c3.node_name(ch)
                nm = f" name={nm!r}" if nm else ""
                print(f"     [{i}] {ch.name!r} {ch.size:>8} bytes{nm}   {ch.described}")
        elif args.logical.lower().endswith(("dmap", ".map")):
            m = DMap.parse(data)
            print(f"   DMap v{m.version} {m.width}x{m.height} puzzle={m.puzzle_path!r} "
                  f"layers={m.layer_count} passageways={len(m.passageways)}")
    return 0


def cmd_map(args) -> int:
    p = Path(args.path)
    if not p.is_absolute():
        p = Path(args.root) / "map" / "map" / args.path
    m = DMap.load(p)
    print(f"{p.name}: version {m.version}, {m.width}x{m.height}")
    print(f"  puzzle    : {m.puzzle_path}")
    print(f"  passageways: {len(m.passageways)}")
    print(f"  layers     : {m.layer_count} (bodies not decoded; see docs/modding.md)")
    if args.ascii:
        mask = m.walkable_mask()
        step = max(1, max(m.width, m.height) // args.ascii)
        for y in range(0, m.height, step):
            print("".join("." if mask[y][x] else "#" for x in range(0, m.width, step)))
    return 0


# ---------------------------------------------------------------------------
# extract / convert
# ---------------------------------------------------------------------------

def cmd_extract(args) -> int:
    out = Path(args.out) if args.out else WORK
    out.mkdir(parents=True, exist_ok=True)
    with AssetRoot(args.root) as R:
        loc = R.locate(args.logical)
        if not loc:
            print(f"not found: {args.logical}")
            return 1
        data = R.read(args.logical)
        name = Path(args.logical).name
        dest = out / name
        dest.write_bytes(data)
        print(f"extracted {loc}  ->  {dest}")
        if args.png:
            if data[:4] != b"DDS ":
                print("  --png ignored: not a DDS")
                return 0
            Image = _pil()
            im = Image.open(dest)
            png = dest.with_suffix(".png")
            im.convert("RGBA").save(png)
            info = dds_info(data)
            print(f"  decoded -> {png}   ({info})")
            print(f"  when re-importing, keep {info.width}x{info.height} and "
                  f"use --format {info.fourcc or 'DXT3'}")
    return 0


def cmd_import_png(args) -> int:
    Image = _pil()
    src = Path(args.png)
    if not src.is_file():
        print(f"no such file: {src}")
        return 1
    dest = STAGE / args.logical
    dest.parent.mkdir(parents=True, exist_ok=True)

    fmt = args.format
    with AssetRoot(args.root) as R:
        orig = R.locate(args.logical)
        if orig and not fmt:
            info = dds_info(R.read(args.logical))
            if info:
                fmt = info.fourcc or "DXT3"
        if orig:
            info = dds_info(R.read(args.logical))
            im_probe = Image.open(src)
            if info and im_probe.size != (info.width, info.height):
                print(f"WARNING: {src.name} is {im_probe.size[0]}x{im_probe.size[1]} but the "
                      f"original is {info.width}x{info.height}.")
                print("         The engine expects power-of-two textures; a mismatch usually "
                      "renders wrong rather than crashing, but it is not verified safe.")
                if not args.force:
                    print("         Re-run with --force to do it anyway.")
                    return 1
    fmt = fmt or "DXT3"
    im = Image.open(src).convert("RGBA")
    im.save(dest, format="DDS", pixel_format=fmt)
    print(f"{src}  ->  {dest}   ({im.size[0]}x{im.size[1]} {fmt})")
    print(f"staged. run `comod.py diff` to review, `comod.py install` to apply.")
    return 0


def cmd_stage_mesh(args) -> int:
    """Stage a .c3 exported from the Blender addon.

    Validates it before it can reach the install: the container must walk
    cleanly and every PHY chunk must re-parse to exactly its declared length,
    which is the same gate tests/test_roundtrip.py applies to the corpus. A
    mesh that fails here would make the client read garbage.
    """
    from c3phy import VARIANTS, iter_chunks, parse_phy

    src = Path(args.c3)
    if not src.is_file():
        print(f"not found: {src}")
        return 1
    data = src.read_bytes()

    try:
        chunks = list(iter_chunks(data))
    except Exception as e:
        print(f"REJECTED: not a valid MAXFILE C3 container -- {e}")
        return 1
    phy = [(t, b) for t, b in chunks if t in VARIANTS]
    if not phy:
        print("REJECTED: no PHY chunk in the file")
        return 1
    for tag, body in phy:
        try:
            m = parse_phy(tag, body)
        except Exception as e:
            print(f"REJECTED: {tag.decode('latin-1')} chunk will not parse -- {e}")
            return 1
        if m.trailing:
            print(f"REJECTED: {tag.decode('latin-1')} {m.name!r} has "
                  f"{m.trailing} unconsumed trailing bytes")
            return 1
        if m.faces and max(max(f) for f in m.faces) >= m.vertex_count:
            print(f"REJECTED: {tag.decode('latin-1')} {m.name!r} has a "
                  f"triangle index past the end of the vertex array")
            return 1
    print(f"{src.name}: {len(chunks)} chunks, {len(phy)} PHY, validated")

    logical = args.logical
    with AssetRoot(args.root) as R:
        if not logical:
            loc = R.resolve_asset(src.stem, "mesh")
            if loc is None:
                print("could not infer the logical path for "
                      f"{src.stem!r}; pass it explicitly, e.g.\n"
                      f"  comod.py stage-mesh {src} c3/mesh/{src.stem}.c3")
                return 1
            logical = loc.logical
            print(f"inferred logical path: {logical}")
        orig = R.read(logical) if R.exists(logical) else None

    if orig is not None:
        if orig == data:
            print("identical to the original -- nothing would change.")
            if not args.force:
                return 0
        else:
            o_tags = [t for t, _ in iter_chunks(orig)]
            n_tags = [t for t, _ in chunks]
            print(f"differs from the original: {len(orig)} -> {len(data)} bytes")
            o_phy = sum(1 for t in o_tags if t in VARIANTS)
            n_phy = len(phy)
            o_moti = o_tags.count(b"MOTI")
            n_moti = n_tags.count(b"MOTI")

            if n_phy != o_phy or n_moti != o_moti:
                # A PHY is bound to a MOTI by position (docs/modding.md 11).
                # Both halves must move together...
                if n_phy != n_moti and o_moti:
                    print(f"  REJECTED: {n_phy} PHY but {n_moti} MOTI chunks. "
                          f"Every shipped container pairs them one-to-one "
                          f"(5,083 of 5,083); the engine binds them by "
                          f"ordinal, so an unpaired mesh has no animation.")
                    return 1
                # ...and the container must not be driven by a SHARED external
                # motion set, which a mod cannot re-cut.
                from c3tex import MotionBinding
                with MotionBinding(args.root) as MB:
                    cls, why = MB.classify(logical)
                print(f"  mesh count changed {o_phy} -> {n_phy} "
                      f"(MOTI {o_moti} -> {n_moti})")
                print(f"  motion binding: {cls.upper()} -- {why}")
                if cls != MB.FREE and not args.force:
                    print("  refusing without --force. Adding or removing a "
                          "mesh renumbers the ordinals that an external "
                          "motion set relies on, and those sets are shared by "
                          "thousands of appearances.")
                    return 1
            elif o_tags != n_tags:
                print(f"  WARNING: chunk layout changed\n"
                      f"    was {[t.decode('latin-1') for t in o_tags]}\n"
                      f"    now {[t.decode('latin-1') for t in n_tags]}")
                if not args.force:
                    print("  refusing without --force: the client expects the "
                          "MOTI/CAME chunks that went with this mesh.")
                    return 1
    else:
        print(f"no original at {logical} -- this is a pure addition")

    dest = STAGE / logical
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"\nstaged: {dest}")
    print("run `comod.py diff` to review, `comod.py install --dry-run` to preview.")
    return 0


def cmd_stage(args) -> int:
    """Copy an original asset into the stage tree unchanged, as a starting point."""
    with AssetRoot(args.root) as R:
        loc = R.locate(args.logical)
        if not loc:
            print(f"not found: {args.logical}")
            return 1
        dest = STAGE / args.logical
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(R.read(args.logical))
        print(f"staged unchanged copy: {loc}  ->  {dest}")
    return 0


# ---------------------------------------------------------------------------
# stage / install
# ---------------------------------------------------------------------------

def _staged_files() -> list[Path]:
    if not STAGE.is_dir():
        return []
    return sorted(p for p in STAGE.rglob("*") if p.is_file())


def cmd_diff(args) -> int:
    files = _staged_files()
    if not files:
        print(f"nothing staged. stage tree: {STAGE}")
        return 0
    with AssetRoot(args.root) as R:
        for p in files:
            logical = p.relative_to(STAGE).as_posix()
            loc = R.locate(logical)
            new = p.read_bytes()
            if loc is None:
                print(f"  NEW      {logical}  ({len(new)} bytes) -- no original, pure addition")
                continue
            old = R.read(logical)
            if old == new:
                print(f"  same     {logical}")
            else:
                print(f"  MODIFIED {logical}  {len(old)} -> {len(new)} bytes "
                      f"(original in {loc.source})")
    print(f"\n{len(files)} staged file(s) in {STAGE}")
    return 0


def cmd_install(args) -> int:
    root = Path(args.root)
    files = _staged_files()
    if not files:
        print("nothing staged.")
        return 0
    BACKUP.mkdir(parents=True, exist_ok=True)
    manifest = {"installed_utc": datetime.now(timezone.utc).isoformat(),
                "root": str(root), "files": []}

    print(f"target install root: {root}")
    for p in files:
        logical = p.relative_to(STAGE).as_posix()
        # Confined even though `logical` was just derived from `relative_to`:
        # a symlink inside the staged tree would produce a clean-looking
        # relative path that still resolves out of the install.
        target = safepath.confine(root, logical)
        had_loose = target.is_file()
        print(f"  {'overwrite' if had_loose else 'add      '} {logical}")
        if not args.dry_run:
            if had_loose:
                b = safepath.confine(BACKUP, logical)
                b.parent.mkdir(parents=True, exist_ok=True)
                if not b.exists():
                    shutil.copy2(target, b)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        manifest["files"].append({"logical": logical, "displaced_loose": had_loose})

    if args.dry_run:
        print(f"\nDRY RUN -- nothing written. {len(files)} file(s) would be installed.")
        return 0

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), "utf-8")
    print(f"\ninstalled {len(files)} file(s). manifest: {MANIFEST}")
    print("originals that were displaced are backed up under mods/backup/.")
    print("run `comod.py uninstall --yes` to revert.")
    return 0


def cmd_uninstall(args) -> int:
    if not MANIFEST.is_file():
        print("no manifest -- nothing recorded as installed.")
        return 1
    man = json.loads(MANIFEST.read_text("utf-8"))
    root = Path(args.root or man["root"])
    for entry in man["files"]:
        # `logical` comes out of the manifest JSON on disk, which anything the
        # user can write may have edited. This is the more exposed of the two
        # joins: uninstall both deletes and restores through it.
        logical = entry["logical"]
        target = safepath.confine(root, logical)
        backup = safepath.confine(BACKUP, logical)
        if entry["displaced_loose"] and backup.is_file():
            print(f"  restore  {logical}")
            if not args.dry_run:
                shutil.copy2(backup, target)
        else:
            print(f"  remove   {logical}")
            if not args.dry_run and target.is_file():
                target.unlink()
    if args.dry_run:
        print("\nDRY RUN -- nothing changed.")
        return 0
    MANIFEST.unlink()
    print(f"\nreverted {len(man['files'])} file(s).")
    return 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Graphics modding workbench for Conquer Online",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="game install root")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("find-item", help="search itemtype.json by name")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_find_item)

    p = sub.add_parser("tables", help="list appearance tables")
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("show", help="resolve an appearance ID to mesh+texture files")
    p.add_argument("ident"); p.add_argument("--table")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("info", help="inspect one logical asset path")
    p.add_argument("logical")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("map", help="inspect a .DMap world map")
    p.add_argument("path"); p.add_argument("--ascii", type=int, metavar="COLS",
                                           help="print a walkability sketch this many columns wide")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("extract", help="extract an asset to mods/work/")
    p.add_argument("logical"); p.add_argument("--out")
    p.add_argument("--png", action="store_true", help="also decode DDS to PNG")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("import-png", help="encode a PNG into the stage tree as DDS")
    p.add_argument("png"); p.add_argument("logical")
    p.add_argument("--format", choices=["DXT1", "DXT3", "DXT5"],
                   help="default: match the original")
    p.add_argument("--force", action="store_true", help="allow a size mismatch")
    p.set_defaults(func=cmd_import_png)

    p = sub.add_parser("stage", help="copy an original asset into the stage tree unchanged")
    p.add_argument("logical")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("stage-mesh",
                       help="validate and stage a .c3 exported from Blender")
    p.add_argument("c3", help="the exported .c3 file")
    p.add_argument("logical", nargs="?",
                   help="target path, e.g. c3/mesh/002135000.c3 "
                        "(inferred from the filename when omitted)")
    p.add_argument("--force", action="store_true",
                   help="stage even if the chunk layout changed")
    p.set_defaults(func=cmd_stage_mesh)

    p = sub.add_parser("diff", help="show what the stage tree changes")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("install", help="copy the stage tree into the game install")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="required to actually write")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="revert a previous install")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="required to actually write")
    p.set_defaults(func=cmd_uninstall)

    args = ap.parse_args(argv)

    if args.cmd in ("install", "uninstall") and not args.yes and not args.dry_run:
        print(f"`{args.cmd}` writes to the game install at {args.root}.")
        print("Re-run with --dry-run to preview, or --yes to proceed.")
        return 1

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
