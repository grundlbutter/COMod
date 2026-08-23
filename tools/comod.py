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

import coroot                     # noqa: E402
import cosettings                 # noqa: E402
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
#: The mod tree. `Installed/` rather than `mods/` because that is the name
#: the shipped layout uses -- one folder holding what is staged and what has
#: been applied, beside COmmunityLibrary/ and ConquerAssets/. The dev checkout
#: and the shipped tree keep the SAME layout so a path that works in one is
#: not a different path in the other.
STAGE = PROJECT / "Installed" / "stage"
WORK = PROJECT / "Installed" / "work"

#: One slot per install root. Backups and the manifest used to be single
#: global paths, which was survivable only while there was exactly one
#: install to write to. With a second, installing to B overwrote A's
#: manifest, and `if not b.exists()` meant B's originals were never backed
#: up at all -- so a later uninstall restored *A's* files into B. Keyed by
#: root, none of that can happen: an install's originals and the record of
#: what displaced them live together, and neither can be reached by any
#: other root.
INSTALLS = PROJECT / "Installed" / "installs"

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


#: Manifest layout. v1 was one install: `{installed_utc, root, files}` with a
#: single shared `backup/`. v2 is a LIST of dated entries, each with its OWN
#: backup directory, which is what makes amending safe: when a later entry
#: overwrites a file an earlier one installed, the later entry's backup holds
#: the EARLIER version, not the original. Reverting therefore has to go
#: newest-first, and each entry restores exactly the state it displaced.
MANIFEST_VERSION = 2


def normalise_manifest(man: dict, root) -> dict:
    """A v1 manifest read as a v2 with one entry. Never written back blindly."""
    if man.get("version") == MANIFEST_VERSION:
        return man
    return {"version": MANIFEST_VERSION,
            "root": man.get("root", str(root)),
            "entries": [{"at": man.get("installed_utc", ""),
                         "label": "installed before amendments were recorded",
                         "backup": "backup",
                         "files": man.get("files", [])}]}


def load_manifest(root) -> Optional[dict]:
    mp = manifest_path(root)
    if not mp.is_file():
        return None
    return normalise_manifest(json.loads(mp.read_text("utf-8")), root)


def entry_backup(root, entry: dict) -> Path:
    return install_dir(root) / (entry.get("backup") or "backup")


def next_backup_name(man: dict) -> str:
    used = {e.get("backup") for e in man.get("entries", [])}
    n = len(man.get("entries", [])) + 1
    while f"backup-{n:04d}" in used:
        n += 1
    return f"backup-{n:04d}"


def select_entries(man: dict, since: str = "", index: int = 0,
                   last: bool = False) -> list:
    """Which entries a revert covers, as a NEWEST-FIRST suffix of the list.

    Only a suffix can be reverted. Entry 2's backup holds what entry 1 put
    there, so undoing 1 while 2 is still on top would restore a file that 2
    has since replaced -- and the install would end up in a state neither
    entry describes. Time-based selection is naturally a suffix because
    entries are appended in order; it is asserted rather than assumed.
    """
    entries = man.get("entries", [])
    if not entries:
        return []
    if last:
        picked = entries[-1:]
    elif index:
        if index < 1 or index > len(entries):
            raise ValueError(f"there is no entry {index}; the record has "
                             f"{len(entries)}")
        picked = entries[index - 1:]
    elif since:
        picked = [e for e in entries if (e.get("at") or "") >= since]
        if picked and entries[-len(picked):] != picked:
            raise ValueError(
                "the entries at or after that time are not the most recent "
                "ones on record; a revert can only peel back from the newest, "
                "because each entry's backup holds what the one before it "
                "left behind")
    else:
        picked = list(entries)
    return picked


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


#: Subjects `catalogs`/`browse` offer, in the order they are printed. The
#: `npc:` ones are separate files rather than one table, so they are listed
#: separately -- collapsing them would report 5,687 "npcs" that live in five
#: tables with different shapes.
_SUBJECT_ORDER = ("npc:npc.ini", "npc:NpcX.ini", "npc:terrainnpc.ini",
                  "npc:npcex.ini", "npc:SlotNpc.ini",
                  "monster", "mount", "item", "item:sub", "garment")


def _plugin_for(root):
    """The parser plugin for this root, or None. Declared kind beats detection.

    **Not wrapped in `except Exception`.** The first draft was, and it
    swallowed an `AttributeError` from calling a `coroot` function that does
    not exist (`declared_kind`; the name is `kind_for_root`) -- so a typo in
    first-party code surfaced as *"no plugin declares any catalogs for this
    install"*, which is a sentence about the client. That is `PartIni`'s
    narrowing, one tool over: a guard around a first-party module protects
    nothing and converts a bug into a fact about the user's data.
    """
    # `plugins` is a repo-root package and this module only puts `tools/` and
    # `core/` on the path -- the same insert `coviewer` does at its own
    # plugin lookup.
    sys.path.insert(0, str(PROJECT))
    import plugins as plugmod                                # noqa: PLC0415
    import coroot                                            # noqa: PLC0415
    root = Path(root)
    kind = coroot.kind_for_root(root)
    p = plugmod.for_kind(str(kind)) if kind else None
    if p is None:
        p = plugmod.detect(root, lambda rel: (root / rel).exists())
    return p


def cmd_settings(args) -> int:
    """Show or change user settings.

    No arguments lists everything with its value, whether that value is the
    default, and -- the part worth printing -- what observably changes when it
    is flipped. A settings list that shows only names and values leaves the
    reader guessing what a toggle does, which is how a knob nobody dares touch
    happens.
    """
    if args.reset:
        path = cosettings.reset(args.name)
        which = args.name or "every setting"
        print(f"  reset {which} to default")
        print(f"  {path}")
        return 0

    if args.name and args.value is not None:
        try:
            path = cosettings.set_value(args.name, args.value)
        except cosettings.UnknownSetting:
            print(f"  no setting called {args.name!r}")
            print(f"  declared: {', '.join(sorted(cosettings.SETTINGS))}")
            return 1
        except cosettings.BadValue as e:
            print(f"  REFUSED: {e}")
            return 1
        print(f"  {args.name} = {cosettings.get(args.name)!r}")
        print(f"  {path}")
        return 0

    if args.name:
        try:
            spec = cosettings.SETTINGS[args.name]
        except KeyError:
            print(f"  no setting called {args.name!r}")
            print(f"  declared: {', '.join(sorted(cosettings.SETTINGS))}")
            return 1
        value = cosettings.get(args.name)
        print(f"  {args.name} = {value!r}"
              f"{'  (default)' if value == spec.default else ''}")
        print(f"  {spec.help}")
        print(f"  changing it: {spec.effect}")
        return 0

    print(f"  settings file: {cosettings.store_path()}")
    print(f"  (created on first change; defaults apply until then)\n")
    for name, value, default, is_default, help_, effect in \
            cosettings.describe():
        mark = "" if is_default else f"   [default {default!r}]"
        print(f"  {name:<20} {value!r}{mark}")
        print(f"  {'':<20} {help_}")
    print("\n  `settings <name>` explains what one changes; "
          "`settings <name> <value>` sets it;\n  `settings --reset [name]` "
          "puts it back.")
    return 0


def _refusal(text: str) -> str:
    """A refusal, trimmed to taste but never to nothing.

    Under `refusal_detail=brief` this keeps the first sentence and says how
    much it dropped, so a reader can tell there IS more rather than believing
    they have the whole reason. The refusal itself is never suppressed: the
    setting trims an explanation, and an explanation that can silently become
    no explanation is how a refusal turns into a blank.
    """
    text = (text or "").strip()
    if cosettings.get("refusal_detail") != "brief" or len(text) < 90:
        return text
    head = text.split(" -- ")[0].split(". ")[0].rstrip(" .,;")
    if len(head) >= len(text) - 1:
        return text
    return f"{head}. [+{len(text) - len(head)} more chars; " \
           f"`settings refusal_detail full`]"


def _recommend(*lines: str) -> None:
    """Print a trailing recommendation block, unless the user turned them off.

    Recommendations are advice about what to run next. **Refusals are not
    routed through here** -- a refusal is an answer about the client, and a
    setting that could hide one would turn a preference into a way to make the
    tool lie quietly.
    """
    if not cosettings.get("ui_recommendations"):
        return
    for line in lines:
        print(line)


def cmd_catalogs(args) -> int:
    """What this install can answer for, and what it cannot.

    A refusal prints its reason. A blank line reads as a bug; "no geometry
    ships for this row" reads as the truth, and the difference is the whole
    reason this command exists rather than a JSON dump.
    """
    root = Path(args.root)
    plug = _plugin_for(root)
    cats = getattr(plug, "catalogs", None)
    if cats is None:
        print(f"  {getattr(plug, 'name', 'no plugin')} declares no catalogs "
              f"for this install.")
        print("  `tables` lists the appearance tables it does resolve.")
        return 0
    table = cats(root)
    print(f"  install: {root}")
    print(f"  plugin : {plug.name}\n")
    print(f"  {'subject':<20} {'rows':>7}  source")
    print(f"  {'-' * 20} {'-' * 7}  {'-' * 40}")
    # `_SUBJECT_ORDER` first for a stable, familiar top, then EVERYTHING ELSE
    # the plugin declares. Iterating the fixed list alone was right while 7878
    # was the only build with catalogs and silently dropped six of 6090's ten
    # subjects and eleven of 6609's fifteen the moment they had their own --
    # a listing that looks complete while omitting most of the client.
    ordered = [s for s in _SUBJECT_ORDER if s in table]
    ordered += [s for s in table if s not in _SUBJECT_ORDER]
    for subject in ordered:
        c = table[subject]
        if c.rows is None:
            print(f"  {subject:<20} {'--':>7}  REFUSED: "
                  f"{_refusal(c.refusal)}")
            continue
        extra = ""
        if getattr(c, "duplicated", None):
            present = c.rows + sum(c.duplicated.values()) - len(c.duplicated)
            # "headers" is only true of a sections table. A flat key map has
            # no headers at all, and `3dmotion.ini` was printing "64337
            # headers" over a file that contains none -- a small thing, but
            # the whole point of this column is to tell a reader that PRESENT
            # and UNIQUE differ, and it should not misname what it counted.
            what = "headers" if c.kind == "ini-sections" else "entries"
            extra = f"  ({present} {what}, {len(c.duplicated)} ids repeat)"
        print(f"  {subject:<20} {c.rows:>7}  {c.source}{extra}")

    # A re-decode control witnesses the PARSE, not the CIPHER: if the TQ
    # keystream were wrong, both decodes would be wrong together and agree.
    # Said once as a footer rather than tagged onto every row -- on these
    # builds every control is the same kind, and repeating it 15 times made
    # the distinction read as decoration instead of a caveat.
    kinds = {c.control_kind for c in table.values()
             if c.rows is not None and c.control_kind}
    if "cached" in kinds:
        print("\n  Some counts came from a CACHED decode (`cache_derived`): "
              "their control fired\n  once, when the entry was written, and is "
              "recorded rather than re-derived.\n  A cached decode cannot be "
              "its own independent witness -- re-checking it\n  against itself "
              "would witness nothing. `comod settings cache_derived false`\n  "
              "re-decodes every run.")
    elif kinds == {"re-decode"}:
        print("\n  Controls on this build are re-decodes: each count was "
              "checked against a\n  second independent decrypt from disk. That "
              "witnesses the parse. It does\n  NOT witness the cipher -- a "
              "wrong keystream would fail both alike. 7878's\n  tables are "
              "plaintext and its controls read the raw bytes, which is "
              "stronger.")
    elif len(kinds) > 1:
        for subject in ordered:
            c = table[subject]
            if c.rows is not None and c.control_kind:
                print(f"  control for {subject}: {c.control_kind}")

    cov = getattr(plug, "NPC_COVERAGE", None)
    if cov and cov.get("geometry_shipped") is not None:
        # `geometry_rows`, not `rows`: the two come from different instruments
        # and differ by the three duplicate section ids. Subtracting one from
        # the other prints a number neither measured.
        rows = cov.get("geometry_rows", cov["rows"])
        named, shipped = cov["geometry_named"], cov["geometry_shipped"]
        print("\n  NPC art -- a limit of the client, not of the reader:")
        print(f"    {rows} rows, {named} name geometry, {shipped} have a "
              f"file in the archives,")
        print(f"    so {rows - shipped} draw nothing. The lookup tables were "
              f"frozen in 2008")
        print("    and never updated. A blank model here is the client, not "
              "a failure to read.")
    if not getattr(plug, "APPEARANCE_ART_RESOLVES", True):
        print("\n  Art on this install is reached BY PATH, not by appearance "
              "id:")
        print("    the frozen lookup tables name mesh and texture ids the "
              "archives do not")
        print("    ship, so `show` resolves the row and reports NOT FOUND. "
              "Measured 0 of 421")
        print("    across all seven part tables, against 160 of 201 on 6090 "
              "and 5517.")
        print("    `info <path>` and `extract <path>` work; the archives hold "
              "146,194 entries.")
    _recommend("\nNote: `browse <subject> [query]` lists rows; `show <id>` "
               "resolves an appearance\nto mesh and texture files.")
    return 0


def cmd_browse(args) -> int:
    """List rows for one subject, `id  label`, like `find-item`."""
    root = Path(args.root)
    plug = _plugin_for(root)
    browse = getattr(plug, "browse", None)
    if browse is None:
        print(f"  {getattr(plug, 'name', 'no plugin')} does not offer browsing "
              f"for this install.")
        print("  try: py -3 tools/comod.py find-item <name>")
        return 1
    # `--limit` wins; with none given the user's `page_size` applies, so a
    # 55,420-row table does not fill a terminal by default.
    limit = args.limit or cosettings.get("page_size")
    rows, total, refusal = browse(args.subject, root, args.query or "",
                                  limit=limit)
    if refusal:
        print(f"  {args.subject}: REFUSED")
        print(f"  {_refusal(refusal)}")
        return 1
    for ident, label in rows:
        print(f"  {ident:>12}  {label}")
    shown = f", showing {len(rows)}" if total > len(rows) else ""
    print(f"\n{total} row(s){shown}")
    if not total:
        return 0
    # Subject-specific next step, and it says nothing it cannot back.
    if args.subject == "monster" and getattr(plug, "MONSTER_ART_LINK",
                                             "unset") is None:
        _recommend(
            "\nNote: this table lists monsters; it does not reach their art.",
            "  MEASURED on this client: BodyType is 0 on all rows, and 1 of",
            "  5,234 row ids matches one of the 371 c3/monster/<N>/ "
            "directories.",
            "  So the row -> model link is not established here. `browse` "
            "is the table.")
    elif args.subject in ("item", "item:sub", "garment"):
        _recommend("\nNote: these are gameplay ids. The 3D look is keyed by "
                   "appearance id in\narmor.ini / weapon.ini / armet.ini -- "
                   "`show <id>` resolves one, `tables` lists them.")
        _print_art_caveat(plug)
    else:
        _recommend("\nNote: `show <id>` resolves an appearance ID to "
                   "mesh+texture files.")
        _print_art_caveat(plug)
    return 0


def _print_art_caveat(plug) -> None:
    """Say so where `show` cannot reach art on this client.

    Without this the previous line is a dead end: `show` resolves the row and
    prints NOT FOUND for both halves, and a reader reasonably concludes the
    tool is broken rather than that the client's tables are frozen.
    """
    if getattr(plug, "APPEARANCE_ART_RESOLVES", True):
        return
    # Follows the recommendation toggle because it is a CORRECTION to a
    # recommendation -- it opens by describing what `show` does, and with the
    # `show` suggestion suppressed it would arrive answering a question nobody
    # was asked. The underlying MEASUREMENT is not lost: `cmd_catalogs` prints
    # the 0-of-421 figure unconditionally, because that is a fact about the
    # client rather than advice about what to run next.
    _recommend(
        "  On this install `show` will resolve the row and report NOT FOUND "
          "for the\n  mesh and texture: the lookup tables are the frozen 2008 "
          "files and name ids\n  the archives do not ship. MEASURED 0 of 421 "
          "across all seven part tables,\n  against 160 of 201 on 6090 and "
          "5517. Reach 7878 art by logical path\n  instead -- `info <path>` "
          "and `extract <path>` work; the archives hold 146,194 entries.")


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


def _print_contested(view, logical: str) -> None:
    """Say so when this path's name hash is one the recovery runs disagreed on.

    Gated on `developer_notes` because a modder wants the name, not the
    argument behind it. **Not gated the way a recommendation is**: this is a
    fact about how much to trust the name, not advice about what to run next,
    and it is the only place the losing candidate survives at all. Someone
    chasing a wrong name has nowhere else to learn a second candidate existed.
    """
    if not cosettings.get("developer_notes"):
        return
    contested = getattr(view, "contested_names", None)
    if contested is None:
        return
    try:
        from tqhash import tq_hash
        row = contested().get(tq_hash(logical.replace("\\", "/").lstrip("/")))
    except Exception:
        return
    if not row:
        return
    print("   CONTESTED NAME -- two recovery runs disagreed on this hash:")
    print(f"     in force  {row.get('kept')!r}  ({row.get('kept_from')})")
    print(f"     rejected  {row.get('rejected')!r}  "
          f"({row.get('rejected_from')})")
    print("     One hash is one archive entry, so both cannot be right. The "
          "committed\n     name won by policy, not by measurement -- an "
          "observed string outranks a\n     brute-forced candidate, and the "
          "recovery's only check on an enumerated\n     name is that the "
          "payload magic matches the extension.")


def cmd_info(args) -> int:
    with open_view(args) as R:
        loc = R.locate(args.logical)
        if not loc:
            print(f"not found: {args.logical}")
            return 1
        data = R.read(args.logical)
        print(loc)
        _print_contested(R, args.logical)
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
    r"""Every staged file `install` may copy into a game install.

    Routed through `safepath.confined_files` because the old
    `STAGE.rglob("*")` handed `cmd_install` entries whose BYTES live outside
    the stage tree -- a directory junction (`mklink /J`, no privilege needed)
    is walked straight through, and `shutil.copy2` then copies the target's
    content into the install. `cmd_install` confines its *destination*, which
    is inside the install by construction and was never the exposed side.

    A refusal is printed rather than counted silently: a staged file that
    vanishes from this list without a word is the "shorter list" failure the
    guard exists to prevent. Hard links are NOT caught and cannot be -- see
    `confined_files`.
    """
    kept, refused = safepath.confined_files(STAGE)
    for p, why in refused:
        print(f"  SKIPPED  {p.relative_to(STAGE).as_posix()} -- {why}",
              file=sys.stderr)
    return kept


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
        if not getattr(args, "amend", False):
            sys.exit(
                f"{root} already has an install recorded ({mpath}).\n"
                f"Either add to it:   py -3 tools/comod.py install --amend "
                f"--root \"{root}\" --yes\n"
                f"or revert it first: py -3 tools/comod.py uninstall "
                f"--root \"{root}\" --yes\n"
                "An amendment is recorded as its own dated entry with its own "
                "backups, so it can be peeled back on its own.")
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

    # An amendment appends a dated entry with its OWN backup directory, so
    # the file it displaces -- which may be a file an earlier entry installed
    # -- is preserved separately and can be put back without disturbing the
    # entries under it.
    existing = load_manifest(root)
    amending = bool(existing) and getattr(args, "amend", False)
    manifest = existing if amending else {"version": MANIFEST_VERSION,
                                          "root": str(root), "entries": []}
    entry = {"at": datetime.now(timezone.utc).isoformat(),
             "label": str(getattr(args, "label", "") or ""),
             "backup": next_backup_name(manifest) if amending else "backup",
             "files": []}
    backup = install_dir(root) / entry["backup"]
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
            entry["files"].append({"logical": logical,
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

    manifest["entries"].append(entry)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2), "utf-8")
    print(f"\n{'amended' if amending else 'installed'} {len(files)} file(s) "
          f"as entry {len(manifest['entries'])} of {len(manifest['entries'])}"
          f" ({entry['at']}). manifest: {mpath}")
    print(f"originals that were displaced are backed up under {backup}")
    print(f"revert everything:  py -3 tools/comod.py uninstall "
          f"--root \"{root}\" --yes")
    print(f"revert just this:   py -3 tools/comod.py uninstall "
          f"--root \"{root}\" --last --yes")
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
    man = normalise_manifest(json.loads(mpath.read_text("utf-8")), root)
    entries = man.get("entries", [])
    if getattr(args, "list", False):
        # The record, so a date-based revert can be aimed at something the
        # owner has actually seen rather than guessed at.
        print(f"install record for {root}  ({len(entries)} entr"
              f"{'y' if len(entries) == 1 else 'ies'}, oldest first)")
        for i, e in enumerate(entries, 1):
            print(f"  {i}. {e.get('at', '(undated)')}  "
                  f"{len(e.get('files', []))} file(s)"
                  + (f"  {e['label']}" if e.get("label") else ""))
            for f_ in e.get("files", [])[:6]:
                print(f"       {f_['logical']}")
            if len(e.get("files", [])) > 6:
                print(f"       ... {len(e['files']) - 6} more")
        print("\nrevert the newest:      --last")
        print("revert entry N upward:  --entry N")
        print("revert on or after T:   --since 2026-08-22T05:00")
        return 0
    try:
        picked = select_entries(man, since=getattr(args, "since", "") or "",
                                index=int(getattr(args, "entry", 0) or 0),
                                last=bool(getattr(args, "last", False)))
    except ValueError as e:
        sys.exit(str(e))
    if not picked:
        print(f"nothing on record at {root} matches that selection.")
        print("`uninstall --list` shows the entries and their dates.")
        return 1
    keep = entries[:len(entries) - len(picked)]
    print(f"reverting install at: {root}")
    print(f"  {len(picked)} of {len(entries)} entr"
          f"{'y' if len(entries) == 1 else 'ies'}, newest first"
          + (f"; {len(keep)} left in place" if keep else ""))
    reverted = 0
    # NEWEST FIRST, and that is the whole correctness argument: a later
    # entry's backup holds what the entry before it left behind, so peeling
    # them off in reverse is what returns each file to the state its own
    # entry displaced.
    for e in reversed(picked):
        ebackup = entry_backup(root, e)
        print(f"  entry {e.get('at', '(undated)')}")
        for f_ in e.get("files", []):
            # `logical` comes out of the manifest JSON on disk, which anything
            # the user can write may have edited. This is the more exposed of
            # the two joins: uninstall both deletes and restores through it.
            logical = f_["logical"]
            target = safepath.confine(root, logical)
            saved = safepath.confine(ebackup, logical)
            if f_["displaced_loose"] and saved.is_file():
                print(f"    restore  {logical}")
                if not args.dry_run:
                    shutil.copy2(saved, target)
            else:
                print(f"    remove   {logical}")
                if not args.dry_run and target.is_file():
                    target.unlink()
            reverted += 1
    if args.dry_run:
        print("\nDRY RUN -- nothing changed.")
        return 0
    for e in picked:
        eb = entry_backup(root, e)
        if eb.is_dir():
            shutil.rmtree(eb, ignore_errors=True)
    if keep:
        man["entries"] = keep
        mpath.write_text(json.dumps(man, indent=2), "utf-8")
        print(f"\nreverted {reverted} file(s) at {root}; "
              f"{len(keep)} earlier entr"
              f"{'y' if len(keep) == 1 else 'ies'} still installed.")
    else:
        mpath.unlink()
        print(f"\nreverted {reverted} file(s) at {root}.")
    return 0


#: Below this, detection is a guess rather than an identification. The stamped
#: plugins return 0.95 off `version.dat`; the family plugins return 0.5 for
#: "an unstamped repack of one of my three members", which is a deliberately
#: weak claim and reads identically through `plugins.detect`. Declaring on it
#: silently is how a repack of 6090 gets filed as vanilla and every table is
#: then read through the wrong profile.
CONFIDENT = 0.9

#: Two candidates within this of each other are a tie, and a tie is not an
#: answer. `detect` breaks it by score then name, which is dictionary order
#: wearing the clothes of evidence.
TIE = 0.05


def _client_report(root: Path) -> dict:
    """What is known about one folder, without declaring anything."""
    sys.path.insert(0, str(PROJECT))          # see `_plugin_for`
    import plugins as plugmod
    ranked = plugmod.rank(root)
    return {
        "root": str(root),
        "declared": coroot.kind_for_root(root),
        "ranked": [(p.name, c) for p, c in ranked],
        "moddable": moddable_install(root),
    }


def _print_candidates(ranked) -> None:
    if not ranked:
        print("    no plugin claims this folder at all")
        return
    for name, conf in ranked[:4]:
        bar = "certain" if conf >= CONFIDENT else "weak"
        print(f"    {conf:.2f}  {name:<14} ({bar})")


def cmd_clients(args) -> int:
    """Declare which folders are clients, and what patch each one is.

    `coroot.declare_kind` has existed as an API with no command behind it, so
    the only way to add an install was the viewer's setup page. That is why
    this box has twelve official installs declared and **not** Zephyr, and why
    `tools/wdf_recover.py`'s wordlist discovery -- which walks declared
    installs -- silently missed the richer of the two DatPkg clients it could
    have used.

    Four verbs, and the split the owner asked for is `add`'s: **discovery by
    default, manual pick with `--kind`.**

        comod clients                     what is declared, and does it still exist
        comod clients scan DIR            what looks like a client under DIR
        comod clients add PATH            detect, then declare -- or refuse
        comod clients add PATH --kind K   declare K, no detection
        comod clients forget PATH         drop a declaration

    `add` REFUSES rather than declaring on a weak or tied detection, and says
    what it saw. A wrong declaration is worse than none: the kind picks the
    parse profile, so a repack of 6090 filed as vanilla reads every table
    through the wrong reader and nothing raises.
    """
    # `plugins` is a repo-root package and this module puts only `tools/` and
    # `core/` on the path -- the same insert `_plugin_for` does.
    sys.path.insert(0, str(PROJECT))
    import plugins as plugmod
    verb = getattr(args, "verb", None) or "list"

    if verb == "list":
        declared = coroot.declared_kinds()
        if not declared:
            print("  no client is declared.")
            print("  add one: py -3 tools/comod.py clients add <path>")
            return 0
        print(f"  {len(declared)} declared client(s):\n")
        missing = 0
        for root in sorted(declared):
            p = Path(root)
            here = p.is_dir()
            if not here:
                missing += 1
            mark = " " if here else "  <- NOT ON DISK"
            print(f"  {declared[root]:<14} {root}{mark}")
        if missing:
            print(f"\n  {missing} declared path(s) are gone. They are still "
                  f"walked by anything that\n  reads declared installs -- "
                  f"`clients forget <path>` drops one.")
        return 0

    if verb == "scan":
        base = Path(args.path)
        if not base.is_dir():
            print(f"  {base} is not a directory")
            return 1
        declared = {k.lower(): v for k, v in coroot.declared_kinds().items()}
        found = 0
        # Nothing is declared here on purpose: scanning is for looking.
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            ok = moddable_install(child)
            if not ok["ok"]:
                continue
            found += 1
            already = declared.get(str(child.resolve()).lower(), "")
            print(f"  {child.name}"
                  f"{'   [declared ' + already + ']' if already else ''}")
            _print_candidates(_client_report(child)["ranked"])
        if not found:
            print(f"  nothing under {base} looks like a client "
                  f"(needs ini/ and one of c3.wdf / c3.tpi / c3/)")
        else:
            print(f"\n  {found} candidate(s). Nothing was declared -- "
                  f"`clients add <path>` does that.")
        return 0

    root = Path(args.path).resolve()

    if verb == "forget":
        if coroot.forget_kind(root):
            print(f"  forgot {root}")
            return 0
        print(f"  {root} was not declared")
        return 1

    # -- add ---------------------------------------------------------------
    ok = moddable_install(root)
    if not ok["ok"]:
        print(f"  {root} does not look like a client: missing "
              f"{', '.join(ok['missing'])}")
        print("  declare it anyway with --kind <plugin> if you know better.")
        if not args.kind:
            return 1

    if args.kind:
        chosen = plugmod.for_kind(args.kind)
        if chosen is None:
            print(f"  no plugin called {args.kind!r}")
            print(f"  known: {', '.join(sorted(p.name for p in plugmod.available()))}")
            return 1
        coroot.declare_kind(root, chosen.name)
        print(f"  declared {root}\n      as {chosen.name}  ({chosen.label})")
        print("  by hand -- detection was not consulted.")
        return 0

    ranked = plugmod.rank(root)
    print(f"  {root}")
    _print_candidates([(p.name, c) for p, c in ranked])
    if not ranked:
        print("\n  REFUSED: nothing claims it. Pick one with --kind <plugin>.")
        return 1
    best, score = ranked[0]
    if score < CONFIDENT:
        print(f"\n  REFUSED: the best claim is {score:.2f}, below {CONFIDENT}.")
        print("  That is a family guess, not an identification -- and the kind")
        print("  picks the parse profile, so a wrong one misreads every table")
        print("  without raising. Confirm with --kind " + best.name)
        return 1
    if len(ranked) > 1 and (score - ranked[1][1]) < TIE:
        print(f"\n  REFUSED: {best.name} and {ranked[1][0].name} are within "
              f"{TIE} of each other.")
        print("  A tie broken by name order is not evidence. Pick with --kind.")
        return 1
    coroot.declare_kind(root, best.name)
    print(f"\n  declared as {best.name} at {score:.2f}  ({best.label})")
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

    p = sub.add_parser("catalogs",
                       help="what this install can answer for, and what it cannot")
    p.set_defaults(func=cmd_catalogs)

    p = sub.add_parser("browse",
                       help="list rows for one subject (npc/monster/mount/garment)")
    p.add_argument("subject", help="e.g. monster, mount, garment, npc:npc.ini")
    p.add_argument("query", nargs="?", default="",
                   help="substring of the name or id")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_browse)

    p = sub.add_parser("settings",
                       help="show or change user settings (no args: list)")
    p.add_argument("name", nargs="?", help="setting to read or change")
    p.add_argument("value", nargs="?", help="new value; omit to read one")
    p.add_argument("--reset", action="store_true",
                   help="restore the default for NAME, or all with no NAME")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("clients",
                       help="declare which folders are clients and what patch "
                            "each is (list/scan/add/forget)")
    p.add_argument("verb", nargs="?", default="list",
                   choices=("list", "scan", "add", "forget"))
    p.add_argument("path", nargs="?", help="the folder (scan/add/forget)")
    p.add_argument("--kind", help="declare this plugin by hand instead of "
                                  "detecting (see `clients scan` for names)")
    p.set_defaults(func=cmd_clients)

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
    p.add_argument("--amend", action="store_true",
                   help="add to an existing install as a new dated entry, "
                        "with its own backups, instead of refusing")
    p.add_argument("--label", default="",
                   help="a note recorded with this entry")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="required to actually write")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="revert a previous install")
    p.add_argument("--list", action="store_true",
                   help="show the dated entries on record and stop")
    p.add_argument("--last", action="store_true",
                   help="revert only the most recent entry")
    p.add_argument("--entry", type=int, default=0,
                   help="revert entry N and everything after it (1-based)")
    p.add_argument("--since", default="",
                   help="revert every entry installed at or after this ISO "
                        "timestamp, e.g. 2026-08-22T05:00")
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

    # `uninstall --list` only reads the record, so it is not held behind the
    # write confirmation: being told to pass --yes in order to LOOK at what a
    # revert would target teaches the reflex this gate exists to prevent.
    listing = args.cmd == "uninstall" and getattr(args, "list", False)
    if (args.cmd in ("install", "uninstall") and not listing
            and not args.yes and not args.dry_run):
        print(f"`{args.cmd}` writes to the game install at {args.root}.")
        print("Re-run with --dry-run to preview, or --yes to proceed.")
        return 1

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
