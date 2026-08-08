#!/usr/bin/env python3
"""
comod.py -- graphics modding workbench for Classic Conquer 2.0.

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
import errno
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import safepath                   # noqa: E402
from coassets import (            # noqa: E402
    DEFAULT_ROOT, AssetRoot, C3File, DMap, dds_info, find_items,
)
from colibrary import ServerView  # noqa: E402


def open_view(args) -> AssetRoot:
    """The asset namespace a read-side command works against: the baseline
    install, or -- with --library/--server -- one imported community client,
    resolved through its own tables and filemap (core/colibrary.py)."""
    if getattr(args, "server", None):
        if not getattr(args, "library", None):
            sys.exit("--server needs --library DIR (the COmmunity Library)")
        return ServerView(args.library, args.server, args.root)
    return AssetRoot(args.root)

PROJECT = Path(__file__).resolve().parent.parent
STAGE = PROJECT / "mods" / "stage"
WORK = PROJECT / "mods" / "work"

#: One slot per install root. Backups and the manifest used to be single
#: global paths, which was survivable only while there was exactly one
#: install to write to. With a second, installing to B overwrote A's
#: manifest, and `if not b.exists()` meant B's originals were never backed
#: up at all -- so a later uninstall restored *A's* files into B. Keyed by
#: root, none of that can happen: an install's originals and the record of
#: what displaced them live together, and neither can be reached by any
#: other root.
INSTALLS = PROJECT / "mods" / "installs"

#: Where the single-install layout kept them. Read once, to migrate.
LEGACY_BACKUP = PROJECT / "mods" / "backup"
LEGACY_MANIFEST = PROJECT / "mods" / "manifest.json"


def install_slug(root) -> str:
    """A stable, readable directory name for one install root.

    The folder name alone is not unique -- two clients are often both called
    "Conquer" -- so the full path is hashed and the readable part is a
    convenience for anyone looking in `mods/installs/`.
    """
    p = Path(root).resolve()
    stem = re.sub(r"[^A-Za-z0-9]+", "-", p.name).strip("-").lower() or "install"
    h = hashlib.blake2b(str(p).lower().encode("utf-8"), digest_size=4).hexdigest()
    return f"{stem}-{h}"


def install_dir(root) -> Path:
    return INSTALLS / install_slug(root)


def backup_dir(root) -> Path:
    return install_dir(root) / "backup"


def manifest_path(root) -> Path:
    return install_dir(root) / "manifest.json"


def migrate_legacy() -> Optional[Path]:
    """Move a pre-split manifest and its backups into their install's slot.

    Done in place and once: the old layout recorded the root it installed
    to, so the slot it belongs in is known rather than guessed. Without this
    an upgrade would silently strand a live install with no way to revert it.
    """
    if not LEGACY_MANIFEST.is_file():
        return None
    try:
        man = json.loads(LEGACY_MANIFEST.read_text("utf-8"))
        root = man.get("root")
    except (OSError, ValueError):
        return None
    if not root:
        return None
    dest = install_dir(root)
    if manifest_path(root).exists():
        return None                       # already migrated; leave both alone
    dest.mkdir(parents=True, exist_ok=True)
    if LEGACY_BACKUP.is_dir() and not backup_dir(root).exists():
        shutil.move(str(LEGACY_BACKUP), str(backup_dir(root)))
    shutil.move(str(LEGACY_MANIFEST), str(manifest_path(root)))
    print(f"migrated the previous manifest for {root}\n  -> {dest}")
    return dest


#: What has to be there for a folder to be a Conquer client at all: the ini
#: tables every build ships, plus somewhere its art lives.
_CLIENT_TABLES = "ini"
_CLIENT_ASSETS = ("c3.wdf", "c3.tpi", "c3.tpd", "c3")


def moddable_install(path) -> dict:
    """Is ``path`` a client a mod can be installed into, and why not?

    Deliberately weaker than `coroot.looks_like_root`, which asks a different
    question: that one decides whether the *viewer* can browse a baseline,
    and demands `c3.wdf`, `data.wdf`, `ini/` and `bin/64/`. A community
    client satisfies none of the first two -- Zephyr ships `c3.tpi`/`c3.tpd`
    instead, and has no `bin/64/` -- so using that gate here refused to
    install into exactly the second client this workbench exists to support.

    Installing needs much less to be true, because the mechanism is just a
    loose file the client reads before its archive: the ini tables, and
    somewhere art lives. `C:\\Windows` fails on the first.
    """
    p = Path(path)
    out = {"root": str(p), "ok": False, "missing": [], "isDir": p.is_dir()}
    if not p.is_dir():
        out["missing"].append("not a directory")
        return out
    if not (p / _CLIENT_TABLES).is_dir():
        out["missing"].append(f"{_CLIENT_TABLES}/")
    if not any((p / a).exists() for a in _CLIENT_ASSETS):
        out["missing"].append(" or ".join(_CLIENT_ASSETS))
    out["ok"] = not out["missing"]
    return out


def _unwritable(root) -> str:
    """Why this install cannot be written to, or "" if it can.

    Probed by actually writing, because that is the only question that
    matters and Windows answers it by ACL rather than by anything visible on
    the directory. `C:\\Program Files` is the case everyone hits: an
    unelevated shell can read every byte of it and write none.
    """
    p = Path(root)
    if not p.is_dir():
        return f"{p} is not a directory."
    probe = p / f".comod-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return ""
    except OSError as e:
        hint = ""
        if getattr(e, "errno", None) in (errno.EACCES, errno.EPERM):
            hint = (
                "\n\nThis is a permissions refusal, not a missing file. On "
                "Windows, anything under \\Program Files needs an "
                "Administrator shell to write to.\n"
                "Either:\n"
                "  * re-run this from a terminal started with \"Run as "
                "administrator\", or\n"
                "  * install into a copy of the client somewhere you own "
                "(your home directory), which is also the safer way to try "
                "a swap out."
            )
        return f"cannot write to {p}: {e}{hint}"


def _prune_install_dir(root) -> None:
    """Drop an install's slot if nothing is recorded in it.

    A failed or dry run would otherwise leave an empty `backup/` behind, and
    an empty slot reads as "something is installed here" everywhere that
    lists them.
    """
    d = install_dir(root)
    if (d / "manifest.json").exists():
        return
    b = d / "backup"
    try:
        if b.is_dir() and not any(b.rglob("*")):
            shutil.rmtree(b, ignore_errors=True)
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    except OSError:                                      # pragma: no cover
        pass


def installed_roots() -> list[dict]:
    """Every install this workbench has something recorded against."""
    out = []
    if not INSTALLS.is_dir():
        return out
    for d in sorted(INSTALLS.iterdir()):
        mp = d / "manifest.json"
        if not mp.is_file():
            continue
        try:
            man = json.loads(mp.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"root": man.get("root", ""), "slug": d.name,
                    "files": len(man.get("files", [])),
                    "installedUtc": man.get("installed_utc", ""),
                    "manifest": str(mp)})
    return out


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
    with open_view(args) as R:
        for part, ini in sorted(R.part_tables().items()):
            print(f"  {part:16s} {ini.name:14s} {len(ini):6d} appearances")
    return 0


def cmd_show(args) -> int:
    with open_view(args) as R:
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
    with open_view(args) as R:
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
    with open_view(args) as R:
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
    migrate_legacy()
    root = Path(args.root).resolve()
    files = _staged_files()
    if not files:
        print("nothing staged.")
        return 0
    backup = backup_dir(root)
    mpath = manifest_path(root)
    if mpath.is_file() and not args.dry_run:
        # Installing twice without reverting would record the SECOND set of
        # originals as the originals -- which by then are the first install's
        # files. The backups are kept (`if not b.exists()` below), so the true
        # originals survive; refusing here keeps the manifest honest too.
        sys.exit(f"{root} already has an install recorded ({mpath}).\n"
                 f"revert it first:  py -3 tools/comod.py uninstall "
                 f"--root \"{root}\" --yes")
    print(f"target install root: {root}")
    if not args.dry_run:
        # Asked BEFORE anything is written. `C:\Program Files` is not
        # writable without an elevated shell, and finding that out from a
        # PermissionError halfway through leaves files installed with no
        # manifest -- installed, and unrevertable by the tool that put them
        # there. One probe answers it for the whole run.
        why = _unwritable(root)
        if why:
            sys.exit(why)

    manifest = {"installed_utc": datetime.now(timezone.utc).isoformat(),
                "root": str(root), "files": []}
    if not args.dry_run:
        backup.mkdir(parents=True, exist_ok=True)
    done: list[tuple[Path, Path, bool]] = []      # (target, backup, had_loose)
    try:
        for p in files:
            logical = p.relative_to(STAGE).as_posix()
            # Confined even though `logical` was just derived from
            # `relative_to`: a symlink inside the staged tree would produce a
            # clean-looking relative path that still resolves out of the
            # install.
            target = safepath.confine(root, logical)
            had_loose = target.is_file()
            print(f"  {'overwrite' if had_loose else 'add      '} {logical}")
            if not args.dry_run:
                b = safepath.confine(backup, logical)
                if had_loose:
                    b.parent.mkdir(parents=True, exist_ok=True)
                    if not b.exists():
                        shutil.copy2(target, b)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
                done.append((target, b, had_loose))
            manifest["files"].append({"logical": logical,
                                      "displaced_loose": had_loose})
    except OSError as e:
        # A half-written install with no manifest cannot be reverted by
        # `uninstall`, so it is undone here instead of left for someone to
        # unpick by hand. Rolling back is the same two operations uninstall
        # performs, against the files this run actually touched.
        print(f"\nfailed on {logical}: {e}", file=sys.stderr)
        for target, b, had_loose in reversed(done):
            try:
                if had_loose and b.is_file():
                    shutil.copy2(b, target)
                elif target.is_file():
                    target.unlink()
            except OSError as undo:                      # pragma: no cover
                print(f"  could not roll back {target}: {undo}", file=sys.stderr)
        _prune_install_dir(root)
        print(f"rolled back {len(done)} file(s); the install is as it was.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\nDRY RUN -- nothing written. {len(files)} file(s) would be installed.")
        _prune_install_dir(root)
        return 0

    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2), "utf-8")
    print(f"\ninstalled {len(files)} file(s). manifest: {mpath}")
    print(f"originals that were displaced are backed up under {backup}")
    print(f"revert with:  py -3 tools/comod.py uninstall "
          f"--root \"{root}\" --yes")
    return 0


def cmd_uninstall(args) -> int:
    migrate_legacy()
    known = installed_roots()
    if args.root_explicit:
        root = Path(args.root).resolve()
    elif len(known) == 1:
        # The unambiguous case, and the common one. Reverting the only
        # install on record needs no ceremony.
        root = Path(known[0]["root"]).resolve()
    elif not known:
        print("no manifest -- nothing recorded as installed.")
        return 1
    else:
        # `--root` used to carry a default, so `args.root or man["root"]`
        # always took the default and this command reverted against whichever
        # install happened to be conventional -- deleting files there that
        # were installed somewhere else, and restoring them from the other
        # install's backups. With more than one on record, say which.
        print("more than one install has something recorded. Name one:")
        for k in known:
            print(f"  --root \"{k['root']}\"   ({k['files']} file(s), "
                  f"{k['installedUtc']})")
        return 1
    mpath = manifest_path(root)
    if not mpath.is_file():
        print(f"nothing recorded as installed at {root}.")
        if known:
            print("on record:")
            for k in known:
                print(f"  {k['root']}")
        return 1
    man = json.loads(mpath.read_text("utf-8"))
    backup = backup_dir(root)
    print(f"reverting install at: {root}")
    for entry in man["files"]:
        # `logical` comes out of the manifest JSON on disk, which anything the
        # user can write may have edited. This is the more exposed of the two
        # joins: uninstall both deletes and restores through it.
        logical = entry["logical"]
        target = safepath.confine(root, logical)
        saved = safepath.confine(backup, logical)
        if entry["displaced_loose"] and saved.is_file():
            print(f"  restore  {logical}")
            if not args.dry_run:
                shutil.copy2(saved, target)
        else:
            print(f"  remove   {logical}")
            if not args.dry_run and target.is_file():
                target.unlink()
    if args.dry_run:
        print("\nDRY RUN -- nothing changed.")
        return 0
    mpath.unlink()
    if backup.is_dir():
        shutil.rmtree(backup, ignore_errors=True)
    print(f"\nreverted {len(man['files'])} file(s) at {root}.")
    return 0


def cmd_installs(args) -> int:
    """Which installs this workbench has written to, and where the record is.

    "I had no idea where it went" is answerable from here as well as from the
    viewer: the stage tree, and every install with something on record.
    """
    migrate_legacy()
    print(f"stage tree:  {STAGE}")
    n = len(_staged_files())
    print(f"             {n} file(s) staged and not yet installed")
    known = installed_roots()
    if not known:
        print("\nno install has anything recorded.")
        return 0
    print("\ninstalled:")
    for k in known:
        print(f"  {k['root']}")
        print(f"      {k['files']} file(s), {k['installedUtc']}")
        print(f"      record: {Path(k['manifest']).parent}")
    return 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Graphics modding workbench for Classic Conquer 2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    # Default applied AFTER parsing, so `uninstall` can tell "you named an
    # install" from "you said nothing" -- the distinction the old default
    # erased, which is what made it revert against the wrong one.
    ap.add_argument("--root", default=None, help="game install root")
    ap.add_argument("--library", metavar="DIR",
                    help="COmmunity Library root (for --server)")
    ap.add_argument("--server", metavar="NAME",
                    help="browse an imported community client by its server "
                         "profile (tables/show/info/extract only)")
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

    p = sub.add_parser("installs",
                       help="where the stage tree is, and which installs have "
                            "something installed from it")
    p.set_defaults(func=cmd_installs)

    args = ap.parse_args(argv)
    args.root_explicit = args.root is not None
    if args.root is None:
        args.root = str(DEFAULT_ROOT)

    if args.cmd in ("install", "uninstall") and not args.yes and not args.dry_run:
        print(f"`{args.cmd}` writes to the game install at {args.root}.")
        print("Re-run with --dry-run to preview, or --yes to proceed.")
        return 1

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
