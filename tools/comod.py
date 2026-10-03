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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                     # noqa: E402
import cosettings                 # noqa: E402
import portage                    # noqa: E402
import safepath                   # noqa: E402
import tags as tagstore           # noqa: E402
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
def _installs_root() -> Path:
    """Where install records live. The local tree unless one is configured.

    See `coroot.installs_root` for why this is a setting and not a search:
    on a box with linked worktrees the records sit in a linked one, so there
    is no checkout the code can infer speaks for the machine. A shipped tree
    has exactly one checkout and never sets it.
    """
    shared, _why = coroot.installs_root_why()
    if shared is not None:
        return shared
    return PROJECT / "Installed" / "installs"


def installs_why() -> str:
    """Why the record root is where it is -- for a surface to PRINT.

    `coroot.installs_root_why` distinguishes "not set" from "set to a folder
    that is not there", and the second silently re-homes every install record
    into whichever checkout is running. `cmd_installs` prints this so the
    fallback stops being invisible.
    """
    _p, why = coroot.installs_root_why()
    return why


INSTALLS = _installs_root()

#: What `_installs_root()` answered at import. Three test files rebind
#: `INSTALLS` directly (tests/test_comod_amend.py, tests/test_swap_npclist.py,
#: tools/test_viewer.py) because a module constant was the only hook they
#: had; `installs_dir()` honours that rebind rather than breaking them, and
#: re-resolves when nobody has chosen.
_INSTALLS_AT_IMPORT = INSTALLS


def installs_dir():
    """The record root, resolved per call unless a caller pinned `INSTALLS`.

    The constant was frozen at import, so `coroot.set_installs_root()` had no
    effect in the same process -- a setting that does nothing until you
    restart is a setting that reads as broken.
    """
    if INSTALLS != _INSTALLS_AT_IMPORT:
        return INSTALLS
    return _installs_root()

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
    return installs_dir() / install_slug(root)


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
    root_dir = installs_dir()
    if not root_dir.is_dir():
        return out
    for d in sorted(root_dir.iterdir()):
        mp = d / "manifest.json"
        if not mp.is_file():
            continue
        try:
            man = json.loads(mp.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        # Read through the v1 -> v2 normaliser. This read the v1 keys
        # (`files`, `installed_utc`) straight off the JSON, so every record
        # written since amendments existed -- all of them v2, with `entries`
        # -- listed as "0 file(s)" with no date, in `installs`, in
        # uninstall's "name one", and in the viewer's install picker.
        man = normalise_manifest(man, man.get("root", ""))
        entries = man.get("entries", [])
        # Distinct paths, because an amendment that re-installs a file an
        # earlier entry put there is one file on disk, not two.
        logicals = {f.get("logical") for e in entries
                    for f in e.get("files", [])}
        out.append({"root": man.get("root", ""), "slug": d.name,
                    "files": len(logicals),
                    "entries": len(entries),
                    # The NEWEST entry: when this install was last written to.
                    "installedUtc": entries[-1].get("at", "") if entries else "",
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
        print("    ship for MOST rows, so `show` often resolves the row "
              "and reports NOT")
        print("    FOUND. RE-MEASURED 2026-08-27 over ALL seven tables "
              "and every part, not a")
        print("    sample: 1,614 of 13,902 parts resolve a mesh (11.6%) "
              "and 2,148 a texture")
        print("    (15.5%). This said 0 of 421 until today, before "
              "`armet` was in either")
        print("    search list and before the garment rule "
              "(<body type><item id>) existed.")
        print("    The rest is the INSTALL's gap, not the tool's: its "
              "tables are dated")
        print("    2008-2009 and name a largely different item set from "
              "the art it ships.")
        print("    `info <path>` and `extract <path>` work; the archives hold "
              "146,194 entries.")
    _recommend("\nNote: `browse <subject> [query]` lists rows; `show <id>` "
               "resolves an appearance\nto mesh and texture files.")
    return 0


def _browse_art(root, rows) -> dict:
    """`{row id: where its 3D art lives}` for the rows on screen.

    THE LINK THIS USES DID NOT EXIST UNTIL 7878.  On older clients a
    gameplay id and an appearance id are separate spaces and the only
    bridge is `armor.ini`/`armet.ini`/`weapon.ini` -- which is what the
    note under `browse` has always said, and it is still true there.

    From 7878 the garment id IS the art key: the last six digits of the
    file stem are the item id, with a BODY TYPE in front.  Item 101000
    "MysticWindrobe" is `c3/body/7101000.c3` and `8101000.c3`.  So a row
    can be answered directly, without going through an appearance table
    that on this install is dated 2008-2009 and names a largely different
    item set.

    Rows with no art print `-`, and that is a real answer rather than a
    gap in the tool: 7878 ships art for a fraction of its 55,420-row item
    catalogue because the rest arrives by patch.  `AssetRoot.resolve_garment`
    only ever returns stems that EXIST, so a `-` cannot be a near miss
    silently rendered as a hit.

    REJECTED, and recorded so it is not re-derived: an obvious-looking
    "tier" rule -- zero the last digit and retry, so 101003 borrows
    101000's art -- takes the linked rows from 374 to 2,003, a 5.4x gain.
    It is WRONG.  Tested against a property it does not control, the item
    NAME, on the 1,629 rows it would newly link:

        name agrees with the base row     574   35.2%
        name DISAGREES                    165   10.1%
        base id not in itemtype at all    890   54.6%

    `350011 Broom` borrows from `350010 IceStick`.  A broom would render
    as an ice stick, and a browse column asserting it would be believed.
    **A rule is not validated by producing more hits, which is the only
    evidence this one has.**  Wrong art is worse than a dash.
    """
    try:
        with AssetRoot(root) as R:
            if R._c3_names() is None:
                # A hash-indexed .wdf cannot be enumerated, so the garment
                # index cannot be built and this column would be a row of
                # dashes that MEANT "unknown" while READING as "no art".
                return {str(i): "(not enumerable on this layout)"
                        for i, _ in rows}
            # `ini/c3.wdb` FIRST, and by the row's LABEL rather than its id.
            # It is the client's own mesh index -- a table that names both
            # sides -- so a hit is confirmed by something outside the id
            # space rather than by a pattern that fits it. Measured on
            # 7878's mounts: 108 of 1,399 distinct labels resolve, ZERO are
            # named-but-not-shipped, and 0 row IDS are wdb keys. The id is
            # not the art key and the label is.
            #
            # CROSS-VALIDATED, and this is better evidence than either
            # route's hit count. Over 55,420 item rows the two rules --
            # c3.wdb (the client's index) and the garment convention
            # (discovered from the id space) -- overlap on 308 ids and
            # name the SAME FILE on all 308. Zero disagreements. Two
            # methods derived from unrelated sources agreeing exactly
            # where they meet is the check neither could run on itself.
            #
            # They are also complementary, so both are kept: c3.wdb
            # alone finds 27 the garment rule misses, the garment rule
            # alone finds 67 c3.wdb misses, union 402.
            #
            # It gives FEWER hits than the prefix guess it replaced -- 108
            # against 272 -- and that is the point: the 272 counted a
            # 3-character directory like `801` prefixing any label that
            # happened to start with it.
            live = None
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                                       / "core"))
                import wdb                                  # noqa: PLC0415
                wp = Path(root) / "ini" / "c3.wdb"
                if wp.is_file():
                    live = wdb.ResourceDb(wp)
            except Exception:                               # noqa: BLE001
                live = None

            out = {}
            for ident, label in rows:
                where = "-"
                if live is not None:
                    for key in (str(label), str(ident)):
                        try:
                            q = live.path_for(key)
                        except Exception:                   # noqa: BLE001
                            q = None
                        if q:
                            loc = R.locate(q.replace(chr(92), "/"))
                            if loc:
                                where = loc.logical
                                break
                if where == "-":
                    for kind in ("body", "armet", "weapon", "hair", "mount"):
                        hit = R.resolve_garment(str(ident), (kind,), ".c3")
                        if hit:
                            where = hit[0].logical
                            break
                out[str(ident)] = where
            return out
    except Exception:                                    # noqa: BLE001
        return {str(i): "?" for i, _ in rows}


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
    art = _browse_art(root, rows) if getattr(args, "art", False) else None
    for ident, label in rows:
        if art is None:
            print(f"  {ident:>12}  {label}")
        else:
            print(f"  {ident:>12}  {label:<34} {art.get(str(ident), '-')}")
    if art is not None:
        # Only a real logical path counts as a hit. "-" is a MISS and
        # "(not enumerable...)" is UNKNOWN, and collapsing those two into
        # "present" produces a summary that contradicts the column printed
        # directly above it -- which is how a tool gets believed over its
        # own output. Measured on a WDF root, where every row is unknown:
        # the first version of this line said "art present for 4 of the 4".
        have = sum(1 for v in art.values() if "/" in v)
        unknown = sum(1 for v in art.values() if v.startswith("("))
        if unknown:
            print(f"\n  art: NOT KNOWABLE for {unknown} of the {len(rows)} "
                  f"row(s) shown -- this layout cannot be enumerated")
        else:
            print(f"\n  art present for {have} of the {len(rows)} row(s) shown")
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
        _recommend("\nNote: these are gameplay ids. On clients before 7878 "
                   "the 3D look is keyed\nby appearance id in armor.ini / "
                   "weapon.ini / armet.ini -- `show <id>`\nresolves one, "
                   "`tables` lists them. From 7878 the row id IS the art "
                   "key:\n`--art` answers each row directly.")
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
        "  On this install `show` resolves a MINORITY of rows to files. "
          "RE-MEASURED\n  2026-08-27 over all seven part tables and every "
          "part: 1,614 of 13,902\n  resolve a mesh (11.6%), 2,148 a texture "
          "(15.5%). This said 0 of 421 until\n  today, before `armet` was in "
          "either search list and before the garment\n  rule "
          "(<body type><item id>) existed. The rest is the INSTALL's gap:\n"
          "  its tables are dated 2008-2009 and name a largely different "
          "item\n  set from the art it ships. `--art` answers a row "
          "directly; `info <path>`\n  and `extract <path>` reach anything "
          "by logical path.")


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
# animation
# ---------------------------------------------------------------------------

#: The PHY chunk tags. Spelled here rather than imported at module scope so
#: `comod --help` does not pay for `c3phy`; `_c3_forms` imports the real
#: `c3phy.VARIANTS` and asserts against this, so the copy cannot drift.
_PHY_TAGS = (b"PHY ", b"PHY2", b"PHY3", b"PHY4", b"PHY5")

#: The particle-system generations, oldest first (effects.PTCL_TAGS).
_PTCL_TAGS = (b"PTCL", b"PTCX", b"PTC3")

#: How far bone 0 may travel, and how far the silhouette may rise, before the
#: clip is reported as carrying root motion. Both thresholds are lifted from
#: `tools/anim.py` rather than invented here: `validate()` section 5 states
#: "walk and run translate the root by less than a unit", and section 6 tests
#: a jump arc with `rise > 5`. MEASURED on 5517 body 002135000, bone-0 net
#: displacement over the whole clip: walk 110 = 0.00, run 120 = 0.00,
#: run 121 = 0.00, idle 100 = 0.07, swing 401 = 1.28, jump 130 = 8.45;
#: silhouette centroid rise: walk 0.89, run 4.31, jump 130 = 22.81,
#: jump 131 = 70.58, death 330 = 101.04. **INFERRED** -- the client's own
#: rule for advancing a character is in the packed exe and is not readable
#: from the assets, so this classifies the DATA, not the engine.
ROOT_MOTION_UNITS = 1.0
ROOT_MOTION_RISE = 5.0


def _c3_forms(data: bytes) -> dict:
    r"""Which of the three animation forms one C3 container carries.

    `tools/effects.py`'s module docstring names them: ``PHY``+``MOTI`` (a
    node/bone matrix track), ``SHAP``+``SMOT`` (a two-point blade line smeared
    into a ribbon trail) and ``PTCL``/``PTCX``/``PTC3`` (a particle system).

    Counted, never paired by adjacency. `attach.PartMesh.parse` is explicit
    that the reader builds **two independent ordinal lists**: `MeshCreate`
    (graphic.dll 0x28360) walks the file for PHY chunks and then walks it
    again for MOTI chunks, so `c3/mount/850/8500000.c3` -- eight PHY followed
    by eight MOTI -- pairs ordinal-to-ordinal and not neighbour-to-neighbour.
    A reader that paired neighbours would report that file as unanimated.
    """
    from c3phy import VARIANTS, iter_chunks
    # `raise`, not `assert`: `python -O` strips an assert, and a drift guard
    # that the interpreter can delete is a guard that cannot fire.
    if set(_PHY_TAGS) != set(VARIANTS):
        raise RuntimeError(
            "the PHY tag list in comod.py has drifted from c3phy.VARIANTS: "
            f"{sorted(_PHY_TAGS)} vs {sorted(VARIANTS)}")
    tags = [t for t, _ in iter_chunks(data)]
    n = {t: tags.count(t) for t in set(tags)}
    phy = sum(n.get(t, 0) for t in _PHY_TAGS)
    ptcl = {t.decode("latin-1"): n.get(t, 0) for t in _PTCL_TAGS if n.get(t)}
    out = {
        "tags": [t.decode("latin-1") for t in tags],
        "phy": phy,
        "moti": n.get(b"MOTI", 0),
        "shap": n.get(b"SHAP", 0),
        "smot": n.get(b"SMOT", 0),
        "ptcl": ptcl,
        "forms": [],
    }
    if phy or out["moti"]:
        out["forms"].append("PHY+MOTI")
    if out["shap"] or out["smot"]:
        out["forms"].append("SHAP+SMOT")
    if ptcl:
        out["forms"].append("/".join(sorted(ptcl)))
    return out


def _pairing_verdict(f: dict) -> tuple[str, str]:
    """`(verdict, why)` for the PHY/MOTI pairing INSIDE one container.

    This is the half `stage-mesh` already gates on: "Every shipped container
    pairs them one-to-one (5,083 of 5,083); the engine binds them by ordinal,
    so an unpaired mesh has no animation."  It answers only the container's
    own question -- whether an EXTERNAL motion set can drive it is a separate
    verdict, computed against that set's chunk count.
    """
    phy, moti = f["phy"], f["moti"]
    if not phy and not moti:
        return "none", "no PHY and no MOTI chunk -- nothing to animate"
    if phy and not moti:
        return "unpaired", (f"{phy} PHY, 0 MOTI -- no embedded track; this "
                            f"mesh animates only if an external motion set "
                            f"covers all {phy} ordinals")
    if moti and not phy:
        return "unpaired", (f"0 PHY, {moti} MOTI -- a motion-only container "
                            f"(this is what a motion set looks like)")
    if phy == moti:
        return "paired", f"{phy} PHY : {moti} MOTI, one-to-one by ordinal"
    return "unpaired", (f"{phy} PHY but {moti} MOTI -- ordinals "
                        f"{min(phy, moti)}..{max(phy, moti) - 1} have no "
                        f"partner, so those meshes do not animate")


def _forms_lines(f: dict, indent: str = "  ") -> list[str]:
    """The three-form block, always all three rows: an absent form is a
    measured 'none', not a missing line."""
    ptcl = ", ".join(f"{k} x{v}" for k, v in sorted(f["ptcl"].items()))
    verdict, why = _pairing_verdict(f)
    return [
        f"{indent}PHY + MOTI    {f['phy']} PHY, {f['moti']} MOTI"
        f"{'' if not (f['phy'] or f['moti']) else '   -> ' + verdict}",
        f"{indent}              {why}",
        f"{indent}SHAP + SMOT   " + (f"{f['shap']} SHAP, {f['smot']} SMOT "
                                     f"(ribbon trail)" if f["shap"] or f["smot"]
                                     else "none"),
        f"{indent}PTCL / PTC3   " + (ptcl if ptcl else "none"),
    ]


def _padded9(ident: str) -> str:
    r"""An appearance id in the nine-wide spelling `anim.AnimDB.shape_of`
    needs, or the id unchanged when it is not a bare number.

    `shape_of` takes `s[:3]` and strips leading zeros, and it guards on
    `len(s) >= 9`.  Half this corpus does not spell ids that wide: 5517,
    6609 and 7205 write `002135000` in `armor.ini` while 5017, 5065 and
    7878 write `2135000` for the SAME appearance.  Fed the seven-wide form,
    `shape_of` falls through to `str(int(s))` and returns the whole id as
    the shape -- MEASURED on 7878 id `1000000`, key `1000000410401`,
    "UNRESOLVED".  Not a crash, and not a right answer either: a reader who
    saw that line would conclude 7878 ships no motion for the body, when
    what it ships is `c3/0002/410/401.c3` under a shape it was never asked
    for.  Padding first gives shape `1`, which is the same answer the
    nine-wide spelling gives on 5517.

    Only digits are padded, and only upward: a nine-or-wider id is returned
    untouched, so no base that already works can change.

    Since 2026-09-25 this is a name for `attach.pad9`, the rule's one home
    (the armet readers and `Catalogue.idle_motion` had grown the same defect
    with no copy of the guard at all).  Kept under this name because
    `tests/test_comod_anim.py` pins it and its mutation control -- `_padded9`
    made the identity function -- still bites through the delegation.
    """
    import attach as attachmod        # noqa: PLC0415 -- lazy, like anim below
    return attachmod.pad9(ident)


def _staged_path(logical: str) -> Optional[Path]:
    p = STAGE / logical
    return p if p.is_file() else None


def cmd_anim(args) -> int:
    r"""Report how one appearance -- or one .c3 -- animates in THIS install.

    The question this exists to answer is `stage-mesh`'s follow-up: a modder
    stages a mesh, and until now had to install it and log in to find out
    whether it still moves.  Three things decide that and all three are
    readable off disk:

      1. the motion set the action resolves to, and whether the file is even
         present in this install (1,100 of 3,260 named motions are absent on
         some bases -- docs/animation.md 2.1);
      2. the container's own PHY/MOTI pairing, by ordinal;
      3. whether that external motion set has at least as many chunks as the
         mesh has PHY.  `C3Mesh::SetMotion` (graphic.dll 0x277C0) REJECTS a
         short set outright, so one extra PHY does not cost you one limb --
         it costs the whole animation.
    """
    if getattr(args, "server", None):
        print("`anim` reads this install's own ini/3dmotion table and c3 "
              "tree, which a --server view does not provide.")
        print("Run it with --root <install> instead.")
        return 1

    import anim as animmod

    ident = args.ident
    looks_like_path = ("/" in ident or "\\" in ident
                       or ident.lower().endswith(".c3"))

    with AssetRoot(args.root) as R:
        # -- a bare container: forms only, no action to resolve -----------
        if looks_like_path:
            logical = ident.replace("\\", "/")
            loc = R.locate(logical)
            if not loc:
                print(f"not found: {logical}")
                return 1
            print(f"{loc}")
            print("\nanimation forms carried by this container")
            for line in _forms_lines(_c3_forms(R.read_located(loc))):
                print(line)
            st = _staged_path(loc.logical)
            if st:
                print(f"\nSTAGED copy at {st}")
                for line in _forms_lines(_c3_forms(st.read_bytes())):
                    print(line)
            return 0

        res = R.resolve_appearance(ident, args.table)
        if not res:
            print(f"appearance {ident!r} not found in any part table.")
            print("try: py -3 tools/comod.py tables")
            return 1
        # Deduplicated by logical path, NOT by row: on 7878 appearance
        # 2135000 matches armor.ini twice (part `body` and part `mix_body`)
        # and both rows name the same file. Reporting one container twice
        # reads as two meshes to check.
        meshes: list[tuple[str, str, object]] = []
        seen_logical: set[str] = set()
        for r in res:
            for p in r["parts"]:
                loc = p["mesh"]
                if loc is None or loc.logical in seen_logical:
                    continue
                seen_logical.add(loc.logical)
                meshes.append((r["part"], p["mesh_id"], loc))
        print(f"[{res[0]['ident']}] in " +
              ", ".join(f"{r['ini']} (part: {r['part']})" for r in res))
        for part, mid, loc in meshes:
            print(f"  mesh  {mid:>10}  -> {loc}")
        if not meshes:
            print("  no mesh reference in any matching row -- nothing to "
                  "animate")

        # -- the motion set -----------------------------------------------
        interval = (args.interval if getattr(args, "interval", None)
                    else animmod.DEFAULT_FRAME_INTERVAL_MS)
        db = animmod.AnimDB(args.root, interval)
        try:
            return _anim_motion(args, db, ident, meshes, R)
        finally:
            # `AnimDB` holds an `AssetRoot` of its own (through
            # `attach.Catalogue`) and has no close, so every invocation left
            # this install's .wdf handles open. Harmless in a one-shot CLI
            # run, not harmless in a suite that calls this thirty times.
            try:
                db.cat.assets.close()
            except Exception:
                pass


def _anim_motion(args, db, ident: str, meshes: list, R) -> int:
    """The motion half of `cmd_anim`, split out only so the AssetRoot that
    `AnimDB` opens is closed on every exit path."""
    import anim as animmod
    idx = db.index
    act = animmod.action_of(args.action)
    shape = args.as_shape or db.shape_of(_padded9(ident))
    ws = db.weaponset(args.weapon, args.off_hand)

    print("\nmotion table")
    print(f"  ini/3dmotion.ini   " +
          ("present" if idx.ini_file.is_file() else "ABSENT"))
    print(f"  compiled twin      " +
          (idx.dbc_file.name if idx.dbc_file is not None
           else "none (the .ini is the live table on this client)"))
    print(f"  keys indexed       {len(idx.raw)}")
    if not idx.raw:
        print("  this client ships no motion table -- no action can be "
              "resolved here.")
        _forms_only(R, meshes, None, args.root)
        return 0

    asked = idx.key(shape, ws, act, args.distance)
    path, how = db.resolve(shape, ws, act, args.distance)
    a = animmod.ACTIONS.get(act)
    print("\nmotion set")
    print(f"  shape {shape}  weaponset {ws}"
          + (f" (weapon {args.weapon})" if args.weapon else " (unarmed)")
          + f"  action {act}" + (f"  ({a.name})" if a else ""))
    print(f"  key asked {asked}")
    if not path:
        print("  UNRESOLVED -- no row for this shape/weaponset/action, and "
              "no fallback reached one.")
        _forms_only(R, meshes, None, args.root)
        return 0
    print(f"  resolves  {path}   [{how}]")
    # The key that ANSWERED, not the one that was asked. `AnimDB.resolve`
    # walks a fallback chain (jump-distance key -> exact -> unarmed set ->
    # that set's idle -> the universal idle) and reports the path and a
    # description, but not which key it landed on. Printing the asked-for
    # key beside a provenance read off a DIFFERENT key is the kind of
    # juxtaposition that manufactures a claim: `--distance 60` asks
    # `602000130`, which is not in the table, and `2000130` is what answers.
    # Re-walking the same chain here is the only way to name it.
    answered = None
    for k in (asked, idx.key(shape, ws, act),
              idx.key(shape, animmod.WEAPONSET_UNARMED, act),
              idx.key(shape, ws, "100"),
              idx.key(shape, animmod.WEAPONSET_UNARMED, "100")):
        if k in idx.raw and idx.raw[k] == path:
            answered = k
            break
    if answered and answered != asked:
        print(f"  key used  {answered}")
    print(f"  from      "
          f"{idx.source.get(answered or asked) or 'unknown'}")

    # `shape=shape`, not `shape=args.as_shape`: the shape is already derived
    # above from the PADDED id, and `ident` is left in the table's own
    # spelling because that is what `clip` looks the body mesh up with.
    clip = db.clip(ident, act, weapon=args.weapon, off_hand=args.off_hand,
                   distance=args.distance, shape=shape)
    if clip is None:
        print(f"  NOT IN THIS INSTALL -- the table names {path} but the file "
              "is absent")
        print("  (docs/animation.md 2.1: named motions that do not ship are "
              "normal on some bases)")
        _forms_only(R, meshes, None, args.root)
        return 0

    kind, wrap, step = clip.classify()
    print(f"  frames    {clip.frame_count}   motion-set chunks "
          f"{clip.chunk_count}")
    print(f"  timing    {clip.interval_ms} ms/frame = "
          f"{1000.0 / clip.interval_ms:.1f} fps   play {clip.play_length} "
          f"frames = {clip.duration_ms:.0f} ms")
    print(f"  loop      {clip.loop}   (measured {kind}: wrap {wrap:.2f} vs "
          f"max step {step:.2f})")
    if clip.chain_next:
        print(f"  chains to action {clip.chain_next}")
    if clip.ctrl:
        print(f"  ActionCtrl {clip.ctrl.shape}{clip.ctrl.weaponset}"
              f"{clip.ctrl.action}  points {clip.ctrl.points}")

    # -- root motion ------------------------------------------------------
    root = clip.root_track()
    if not root:
        print("  root motion  unknown -- the motion set has no readable "
              "bone track")
    else:
        import math as _math
        net = _math.dist(root[0][:2], root[-1][:2])
        span = max(_math.dist(p[:2], root[0][:2]) for p in root)
        sil = clip.silhouette_track()
        rise = (max(s[2] for s in sil) - min(s[2] for s in sil)) if sil else 0.0
        moves = net > ROOT_MOTION_UNITS
        arcs = rise > ROOT_MOTION_RISE
        print(f"  root motion  bone-0 travel: net {net:.2f} units end to "
              f"end, furthest {span:.2f}")
        print(f"               silhouette centroid rise {rise:.2f} units"
              + ("" if sil else "   (no body mesh to pose)"))
        if not moves and not arcs:
            print(f"               NONE: an in-place cycle. Both readings "
                  f"are under the thresholds ({ROOT_MOTION_UNITS} units, "
                  f"{ROOT_MOTION_RISE} rise), which is what every walk and "
                  f"run in this data measures -- the client moves the "
                  f"character externally.")
        else:
            # No word for the vertical case that fits both a jump and a
            # death: 331's silhouette drops 101 units and "leaves the
            # ground" reads as the opposite of what happened. The neutral
            # phrasing is the honest one, and the sign is in the numbers
            # two lines up.
            print("               PRESENT: the clip does not stay put -- "
                  + ("it travels horizontally; " if moves else "")
                  + ("its height changes; " if arcs else "")
                  + "a renderer that pins the model to one spot will "
                    "misplace it.")
            print("               Bone 0 is the pelvis, NOT a root locator, "
                  "so a swing or a death leans it without moving the "
                  "character. Read the negative as strong and the positive "
                  "as 'not a flat cycle' -- INFERRED, the client's own "
                  "advance rule is in the packed exe.")

    # -- does it still animate? -------------------------------------------
    print("\ndoes this mesh still animate?")
    print(f"  the motion set {path} carries {clip.chunk_count} chunks;")
    print(f"  C3Mesh::SetMotion (graphic.dll 0x277C0) refuses a set with "
          f"fewer entries than the mesh has PHY, then assigns "
          f"phy[i]->motion = set[i].")
    for part, mid, loc in meshes:
        _report_binding(R, part, mid, loc, clip.chunk_count, args.root)
    return 0


def _forms_only(R, meshes, set_chunks: Optional[int], root) -> None:
    """The container half of the report when no motion set could be loaded.

    A client that ships no motion table, or names a motion file it does not
    ship, still has a mesh whose PHY/MOTI pairing a modder can check. Cutting
    the report off at the missing table would answer "does my swap still
    animate?" with silence on exactly the bases where the answer is hardest
    to get any other way.
    """
    if not meshes:
        return
    print("\nanimation forms carried by the mesh (no motion set to bind)")
    for part, mid, loc in meshes:
        _report_binding(R, part, mid, loc, set_chunks, root)


def _report_binding(R, part: str, mid: str, loc, set_chunks: Optional[int],
                    root) -> None:
    """One mesh's verdict, original and staged, against one motion set.

    `read_located`, NOT `read(loc.logical)`. On 7878 appearance `1000000`
    resolves to `c3/mesh/001000000.c3` **out of the 6090 fallback install**
    -- `Located.origin_root` says so -- and re-resolving that logical path
    against 7878 raises `FileNotFoundError` for a file that plainly exists.
    The first draft did exactly that and the sweep across generations is
    what caught it.
    """
    try:
        orig = _c3_forms(R.read_located(loc))
    except Exception as e:
        print(f"\n  {part} mesh {mid}  {loc.logical}")
        print(f"    cannot be read from this install: {e}")
        return
    print(f"\n  {part} mesh {mid}  {loc}")
    for line in _forms_lines(orig, indent="    "):
        print(line)
    print("    " + _animates_line(orig, set_chunks))

    st = _staged_path(loc.logical)
    if st is None:
        print(f"    nothing staged at Installed/stage/{loc.logical}")
        return
    try:
        staged = _c3_forms(st.read_bytes())
    except Exception as e:
        print(f"    STAGED {st} -- will not parse as a C3 container: {e}")
        return
    print(f"    STAGED  {st}")
    for line in _forms_lines(staged, indent="      "):
        print(line)
    print("      " + _animates_line(staged, set_chunks))
    if staged["phy"] != orig["phy"] or staged["moti"] != orig["moti"]:
        print(f"      CHANGED from the original: PHY {orig['phy']} -> "
              f"{staged['phy']}, MOTI {orig['moti']} -> {staged['moti']}")
        from c3tex import MotionBinding
        with MotionBinding(root, assets=R) as MB:
            cls, why = MB.classify(loc.logical)
        print(f"      motion binding: {cls.upper()} -- {why}")


def _animates_line(f: dict, set_chunks: Optional[int]) -> str:
    """The one line the modder came for."""
    verdict, _ = _pairing_verdict(f)
    if f["phy"] == 0:
        return ("ANIMATES: no  -- no PHY chunk for a motion set to bind to"
                if not f["moti"] else
                "ANIMATES: n/a -- motion-only container, nothing to skin")
    if set_chunks is None:
        # Not "yes" and not "no": the external set is the half that decides,
        # and on this base there is no set to measure. Saying "yes" off the
        # container alone is the confident-wrong answer this report exists
        # to avoid.
        if verdict == "paired":
            return (f"ANIMATES: UNKNOWN -- {f['phy']} PHY paired 1:1 with its "
                    f"own MOTI, so it animates from the container; whether an "
                    f"external set would also bind cannot be checked here")
        return (f"ANIMATES: NO on its own -- {_pairing_verdict(f)[1]}; and no "
                f"external motion set is available on this client to supply "
                f"the missing tracks")
    if set_chunks < f["phy"]:
        return (f"ANIMATES: NO  -- {f['phy']} PHY against a {set_chunks}-chunk "
                f"motion set; SetMotion rejects the whole set, so NOTHING on "
                f"this mesh moves")
    tail = "" if verdict == "paired" else \
        "  (its own MOTI chunks are unpaired, but the external set overrides)"
    return (f"ANIMATES: yes -- {f['phy']} PHY <= {set_chunks} motion-set "
            f"chunks, bound ordinal by ordinal{tail}")


# ---------------------------------------------------------------------------
# effects -- filtering by animation form
# ---------------------------------------------------------------------------

def _cmd_effects_forms(args) -> int:
    r"""List this install's effects, filtered by which animation form they use.

    `comod anim` answers the form question for ONE container.  This answers it
    for the whole install and the other way round: *which effects are ribbon
    trails?*  That is the question a modder actually starts from, and until now
    the classification existed everywhere and was queryable nowhere.

    The filter is SET MEMBERSHIP.  A container can carry more than one form --
    MEASURED on 5517, `c3/effect/lance/560029.C3` holds 4 PHY + 4 MOTI **and**
    2 SHAP + 2 SMOT, and effect `560029` is returned by `--form phy` and by
    `--form ribbon` both.  1,222 of that install's 3,391 effects are
    multi-form, so a single-value category would misfile a third of the table.

    Everything here is `tools/effects.py`'s; this is the CLI surface, not a
    second implementation.

    The index costs one pass over every container the effect tables name (13 s
    on 5517, 108 s on 6609, MEASURED 2026-09-07 cold).  That is why it is this
    command and not a line in `comod catalogs`.
    """
    import effects as fx
    try:
        want = [fx.parse_form(f) for f in (args.form or [])]
    except fx.UnknownForm as exc:
        print(str(exc))
        return 2
    # `with`, and the THREE `return`s below are the reason: this opens its own
    # install and every way out of here used to drop it still open.
    with fx.EffectDB(args.root) as db:
        if args.census:
            print(json.dumps(fx.form_census(db), indent=2, ensure_ascii=False))
            return 0
        rows = fx.filter_effects(db, forms=want, mode=args.form_mode,
                                 match=args.match)
        if args.json:
            print(json.dumps({"root": str(db.root), "total": len(rows),
                              "filter": {"forms": want,
                                         "mode": args.form_mode,
                                         "match": args.match},
                              "effects": rows}, indent=2, ensure_ascii=False))
            return 0
        fx._print_effect_list(db, rows, want, args.form_mode, args.match,
                              args.limit)
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


# ---------------------------------------------------------------------------
# export / import -- the item-5 foundation (core/portage.py is the machinery)
# ---------------------------------------------------------------------------

def _provenance(args) -> dict:
    """The batch record's provenance for the current install.

    The client family/version is the parser plugin's identity, which is the
    only client fingerprint the app trusts (see `_plugin_for`). It doubles as
    the compatibility key on import.
    """
    plug = _plugin_for(args.root)
    family = getattr(plug, "name", "") if plug else ""
    version = portage._client_version(family)
    return {
        "source_install": install_slug(args.root),
        "client_family": family,
        "client_version": version if version is not None else family,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cmd_export(args) -> int:
    r"""`comod export <asset> [<asset>...] --for <tool>` -- the item-5 export verb.

    Dispatches by asset TYPE to the right transform (`.c3` copied for Blender,
    `.dds` decoded to PNG, a table to CSV, an `.ani` to its ordered frame set)
    and writes the two-tier `<asset>/<type>/<file>` layout plus a diffable
    `comod-manifest.json` array and a README. `--zip` packs it; otherwise it
    lands in a directory. `--for` only validates that the named tool opens the
    kind of file being exported -- the transform is chosen by the file.
    """
    items: list = []
    shared = set(getattr(args, "shared", None) or [])
    for logical in args.asset:
        atype = portage.classify(logical)
        if args.for_tool:
            why = portage.tool_mismatch(args.for_tool, atype)
            if why:
                print(f"REFUSED {logical}: {why}")
                return 1
        items.append(portage.ExportItem(logical, shared=logical in shared))

    prov = _provenance(args)
    with AssetRoot(args.root) as R:
        manifest, files = portage.build_manifest(R, items, prov)

    absent = [r for r in manifest if r.get("record") == "file" and r["absent"]]
    if args.zip:
        dest = Path(args.zip)
        portage.write_batch_zip(dest, manifest, files)
    else:
        # `coroot.export_dir()`, not `WORK / "export"`: the old default put
        # the user's bundles INSIDE A GIT WORKTREE -- per-checkout, invisible
        # from another clone, removed by anything that cleans the tree. The
        # viewer's pop-out resolves through the same function, because the
        # two having separate bundle directories is exactly how `comod
        # import` comes to not see what the panel just wrote.
        dest = Path(args.out) if args.out else coroot.export_dir()
        dest.mkdir(parents=True, exist_ok=True)
        portage.write_batch_dir(dest, manifest, files)
    print(f"exported {len(files)} file(s) -> {dest}")
    for r in manifest:
        if r.get("record") != "file" or r["absent"]:
            continue
        tag = "  [SHARED -- edits it under every asset it appears in]" \
            if r["shared"] else ""
        tool = portage.default_tool(r["type"])
        print(f"  {r['export']}   ({r['type']} -> {tool}){tag}")
    for r in absent:
        print(f"  (absent) {r['dest']} -- declared but not in this install, "
              f"recorded as absent")
    print(f"\nreimport with:  py -3 tools/comod.py import {dest}")
    return 0


def cmd_import(args) -> int:
    r"""`comod import <workdir-or-zip>` -- the item-5 import verb.

    Reads the manifest, ENFORCES the per-format rules, and lands results in the
    stage tree so `diff` / `impact` / `install` still apply:

      * an untouched texture is SKIPPED, never re-encoded (lossy DDS rule);
      * a `.dat` row that changes byte length on a 6907-7878 client is REFUSED;
      * an `.ani` whose frame order contradicts its FrameAmount is REFUSED.

    Provenance is a compatibility check: a batch from another client family is
    refused, and a target whose original does not hash to the manifest's is
    flagged as already-modified before anything is overwritten.
    """
    plug = _plugin_for(args.root)
    target_family = getattr(plug, "name", "") if plug else ""
    with AssetRoot(args.root) as R:
        res = portage.import_batch(
            args.src, R, STAGE, target_family=target_family,
            png_to_dds=lambda png, fourcc, size: _encode_png_bytes(png, fourcc))
    for line in res.staged:
        print(f"  STAGED   {line}")
    for line in res.skipped:
        print(f"  skip     {line}")
    for line in res.absent:
        print(f"  absent   {line}")
    for line in res.warnings:
        print(f"  WARNING  {line}")
    for line in res.refused:
        print(f"  REFUSED  {line}")
    print(f"\n{len(res.staged)} staged, {len(res.skipped)} skipped, "
          f"{len(res.refused)} refused, into {STAGE}")
    if res.staged:
        print("review with `comod.py diff`, apply with `comod.py install`.")
    if not res.ok:
        print("one or more files were refused by a format rule; nothing "
              "refused was staged.")
        return 1
    return 0


def _encode_png_bytes(png: bytes, fourcc: str) -> bytes:
    """PIL PNG-bytes -> DDS-bytes, injected into portage so it stays PIL-free."""
    import io as _io
    Image = _pil()
    im = Image.open(_io.BytesIO(png)).convert("RGBA")
    out = _io.BytesIO()
    im.save(out, format="DDS", pixel_format=fourcc)
    return out.getvalue()


def _restore_motion(orig: bytes, data: bytes, logical: str,
                    source: str) -> tuple[int, bytes]:
    r"""`--restore-motion`: give a motion-less donor the original's MOTI.

    Returns `(0, spliced)` on success and `(1, data)` on a refusal, having
    printed the whole per-ordinal verdict either way.

    THIS IS NOT A GENERAL RETARGET AND MUST NOT BECOME ONE. A `.c3` carries no
    skeleton -- no bone names, no parent indices, no inverse-bind matrices --
    so nothing computable from a donor's geometry says which motion track
    belongs to it. MEASURED on `7205/c3/monster`: over pairs of shipped
    containers whose PHY chunks are BYTE-IDENTICAL slot for slot, swapping the
    MOTI moves the median vertex by more than 1% of the mesh's own bounding-box
    diagonal in 169 of 219 chunk comparisons (77.2%), worst 1.85x the diagonal.
    `c3write.motion_restore_report` therefore accepts only the case where the
    donor agrees with the original on every input the skinning path reads, so
    the posed result is bit-identical and nothing is being decided. The full
    evidence, including the two weaker rules that measured wrong, is
    `docs/moti_retarget_2026-09-06.md`.
    """
    from c3phy import iter_chunks
    from c3write import motion_restore_report, restore_motion
    ok, lines = motion_restore_report(orig, data)
    print(f"  --restore-motion against the {source} copy of {logical}:")
    for ln in lines:
        print(f"  {ln}")
    if not ok:
        print("  REJECTED: the donor cannot inherit this motion. Nothing "
              "staged. A mesh that animates wrongly is worse than one that "
              "does not install.")
        return 1, data
    spliced = restore_motion(orig, data)
    print(f"  restored {sum(1 for t, _ in iter_chunks(orig) if t == b'MOTI')} "
          f"MOTI chunk(s), each immediately after its own PHY "
          f"({len(data)} -> {len(spliced)} bytes)")
    return 0, spliced


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
        # WHICH COPY the original came from is recorded, not just its bytes.
        # `locate` resolves overlay -> loose -> archive, and on the shipped
        # clients those copies are NOT interchangeable: on 7205, 622 loose
        # `c3/**.c3` paths are also present in `c3.wdf`, 360 of them differ,
        # and 277 have the SAME chunk-tag sequence with DIFFERENT MOTI content
        # (5517: 18 of 18; 6609: 20 of 20). So "the original's motion" is a
        # different set depending on which copy answered, and a restore that
        # does not say which one it used cannot be reproduced.
        o_loc = R.locate(logical)
        orig = R.read(logical) if o_loc is not None else None
        R_src = o_loc.source if o_loc is not None else "?"

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

            # `--restore-motion` runs BEFORE the count checks and, when it
            # succeeds, satisfies them by construction: it only ever emits one
            # MOTI per PHY and it refuses any donor whose PHY count differs
            # from the original's. It is not a way around either check, so it
            # short-circuits both rather than being tested against them again.
            restored = False
            if (getattr(args, "restore_motion", False)
                    and n_moti == 0 and o_moti):
                rc, data = _restore_motion(orig, data, logical, R_src)
                if rc:
                    return rc
                chunks = list(iter_chunks(data))
                n_tags = [t for t, _ in chunks]
                n_moti = n_tags.count(b"MOTI")
                restored = True

            if restored:
                pass
            elif n_phy != o_phy or n_moti != o_moti:
                # A PHY is bound to a MOTI by position (docs/modding.md 11).
                # Both halves must move together...
                if n_phy != n_moti and o_moti:
                    print(f"  REJECTED: {n_phy} PHY but {n_moti} MOTI "
                          f"chunks. Every shipped container pairs them "
                          f"one-to-one (5,083 of 5,083); the engine binds "
                          f"them by ordinal, so an unpaired mesh has no "
                          f"animation.")
                    if n_moti == 0 and n_phy == o_phy:
                        print("  A donor with NO motion at all can borrow "
                              "the original's with --restore-motion, but "
                              "only where that is provable; see "
                              "docs/moti_retarget_2026-09-06.md.")
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
    _warn_effects(args, [logical])
    print("run `comod.py diff` to review, `comod.py install --dry-run` to preview.")
    return 0


def _warn_effects(args, logicals: list) -> None:
    r"""Say whether an effect layer points at what was just staged.

    THE WARNING THAT DID NOT EXIST. `stage` and `stage-mesh` validated the
    container and said nothing about consequences, so replacing
    `c3/effect/blade/410009.C3` -- a file a weapon's aura layer names -- was
    indistinguishable from replacing a file nothing draws.

    It is on by DEFAULT here, unlike `diff --impact`, because it costs only
    the effect tables: MEASURED 0.1s (5017) to 1.6s (7205) to build
    `EffectDB`, against the 34s `diff --impact` pays to resolve every
    appearance reference in the install. `--no-effect-check` turns it off,
    and turning it off PRINTS A LINE saying the question was not asked --
    silence would read as "no effect uses this".
    """
    if getattr(args, "no_effect_check", False):
        print("")
        print("EFFECT DEPENDENCIES   NOT CHECKED (--no-effect-check). "
              "Whether an effect layer names")
        print("   the staged file(s) is UNKNOWN, not 'no'.")
        return
    import depclose
    print("")
    try:
        depclose.effect_warning(args.root, logicals)
    except Exception as e:                                 # noqa: BLE001
        # A failure here must not read as "nothing found", and must not stop
        # a stage that has already succeeded.
        print("EFFECT DEPENDENCIES")
        print(f"   UNMEASURED -- the effect walk raised "
              f"{e.__class__.__name__}: {e}")
        print("   Whether an effect layer names the staged file(s) is "
              "UNKNOWN, not 'no'.")
def _map_archive(root: Path, target: str) -> tuple:
    """`(relpath, absolute path)` for a map archive named loosely.

    Accepts ``desert``, ``desert.7z`` or ``map/map/desert.7z``.  The registry
    is consulted where it parses, so the path this returns is the one
    ``ini/GameMap.dat`` actually names rather than one this function guessed --
    that distinction is the entire reason maps are hard to mod, and a
    stage-map that wrote to a plausible-looking path the client never opens
    would reproduce the original blocker with a green message on top.
    """
    from dmap import load_gamemap
    t = target.replace("\\", "/").strip("/")
    stem = Path(t).stem
    rel = None
    _, rows = load_gamemap(root)
    named = [r.get("FileName", "").replace("\\", "/") for r in rows]
    for fn in named:
        if fn.lower().endswith(".7z") and Path(fn).stem.lower() == stem.lower():
            rel = fn
            break
    if rel is None:
        rel = t if "/" in t else f"map/map/{stem}.7z"
        if not rel.lower().endswith(".7z"):
            rel += ".7z"
    return rel, (Path(root) / rel)


def cmd_stage_map(args) -> int:
    r"""Put a modified `.DMap` back INSIDE the `.7z` the map registry names.

    This is the one asset class loose-file override does not reach.  From 5517
    the registry names ``.7z`` on 100% of rows on every install, so dropping a
    ``.DMap`` next to the archive produces a file the client never opens --
    which is why `docs/capability_matrix_2026-09-06.md` grades the map grid
    modifiable NOWHERE.  The archive is rebuilt with `tools/sz7zwrite.py` and
    written into the SAME stage tree `install` and `uninstall` already drive,
    so this adds no second install mechanism and inherits the backups.

    Two ways to say what to change:

        --dmap FILE          use these bytes as the new payload
        --set X,Y,MASK       flip one cell's walkability (repeatable)

    `--set` patches the shipped `.DMap` in place -- the grid is fixed-stride,
    so every header, portal, layer and trailer byte this repo has not fully
    decoded survives by construction, and only the edited rows' checksums are
    recomputed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sz7z
    import sz7zwrite
    import dmap as dm

    root = Path(args.root).resolve()
    rel, src = _map_archive(root, args.map)
    if not src.is_file():
        loose = src.with_suffix(".DMap")
        if loose.is_file():
            print(f"{rel} is not present, but {loose.name} is.\n"
                  f"This install ships the map LOOSE, so it can be modified "
                  f"the ordinary way:\n"
                  f"  py -3 tools/comod.py stage "
                  f"{loose.relative_to(root).as_posix()}")
            return 1
        print(f"not found: {src}")
        return 1

    try:
        m = sz7z.read_archive(src)
        raw = sz7z.extract(src)
    except ValueError as e:
        print(f"{rel}: {e}")
        return 1
    inner = [f["name"] for f in sz7zwrite.decode_files(m)]
    print(f"source: {src}\n  registry path: {rel}\n  holds: {inner}")

    if args.dmap:
        payload = Path(args.dmap).read_bytes()
        print(f"  payload: {args.dmap} ({len(payload)} bytes)")
    elif args.set:
        edits = []
        for s in args.set:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) < 3:
                print(f"--set wants X,Y,MASK (got {s!r})")
                return 1
            edits.append((int(parts[0]), int(parts[1]), int(parts[2]),
                          int(parts[3]) if len(parts) > 3 and parts[3] else None,
                          int(parts[4]) if len(parts) > 4 and parts[4] else None))
        before = dm.parse(src, data=raw, want_cells=True, verify=True)
        if before.checksum_ok != before.height:
            print(f"  REFUSED: {before.checksum_ok}/{before.height} row "
                  f"checksums verify on the SHIPPED map, so a rewritten "
                  f"checksum cannot be told from a broken one.")
            return 1
        payload = sz7zwrite.patch_dmap_cells(raw, edits)
        after = dm.parse(src, data=payload, want_cells=True, verify=True)
        print(f"  {before.width}x{before.height}, {len(edits)} cell(s) edited; "
              f"row checksums {after.checksum_ok}/{after.height}")
        if after.checksum_ok != after.height:
            print("  REFUSED: the patched map's checksums do not verify.")
            return 1
    else:
        print("nothing to change: pass --dmap FILE or --set X,Y,MASK")
        return 1

    new = sz7zwrite.replace_payload(m, payload)
    dest = STAGE / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = sz7zwrite.serialize_archive(new)
    dest.write_bytes(blob)
    # READ IT BACK. The archive is only useful if it decompresses to what we
    # meant, and this is the last point at which anything here can check that
    # -- the client cannot be run from this seat.
    back = sz7z.extract(dest)
    ok = back == payload
    print(f"staged: {dest}\n  {len(blob)} bytes "
          f"({sum(m['packsizes'])} -> {sum(new['packsizes'])} packed), "
          f"re-extracts to the intended payload: {ok}")
    if not ok:
        dest.unlink()
        print("  REFUSED and removed: the staged archive does not read back.")
        return 1
    print("\nreview:  py -3 tools/comod.py diff")
    print(f"install: py -3 tools/comod.py --root \"{root}\" install --yes")
    print(f"revert:  py -3 tools/comod.py --root \"{root}\" uninstall --yes")
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
    _warn_effects(args, [args.logical])
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
    changed: list[str] = []
    with AssetRoot(args.root) as R:
        for p in files:
            logical = p.relative_to(STAGE).as_posix()
            loc = R.locate(logical)
            new = p.read_bytes()
            if loc is None:
                print(f"  NEW      {logical}  ({len(new)} bytes) -- no original, pure addition")
                changed.append(logical)
                continue
            old = R.read(logical)
            if old == new:
                print(f"  same     {logical}")
            else:
                print(f"  MODIFIED {logical}  {len(old)} -> {len(new)} bytes "
                      f"(original in {loc.source})")
                changed.append(logical)
    print(f"\n{len(files)} staged file(s) in {STAGE}")
    # THE EFFECT PASS IS NOT OPT-IN, and that is deliberate. `--impact` is a
    # flag because it resolves every appearance reference in the install (34s
    # on 7878); the effect walk needs only the effect tables (0.1-1.6s
    # measured across the corpus), so there is no reason for a modder to have
    # to know to ask for it. See `_warn_effects`.
    if changed:
        _warn_effects(args, changed)
    if getattr(args, "impact", False):
        # THE DEPENDENCY PASS IS OPT-IN AND SAYS SO WHEN IT IS OFF.
        #
        # `diff` answers "what files does this change"; it has never answered
        # "what does that BREAK". The pass costs one resolution per distinct
        # appearance reference in the install -- MEASURED 34s on 7878, 40s on
        # 7205 -- which is the wrong default for a command people run to see
        # a file list. So it is a flag, and the line below is printed when
        # the flag is absent so nobody reads a plain `diff` as a safety check.
        print("")
        _diff_impact(args, changed)
    elif changed:
        print("`diff` lists FILES, not consequences. `comod.py diff --impact` "
              "adds who references them.")
    return 0


def _diff_impact(args, changed: list) -> int:
    """The dependency closure of every staged file that actually changes."""
    import depclose

    if not changed:
        print("no staged file differs from the install, so nothing to check.")
        return 0
    with depclose.DepGraph(args.root, progress=True) as g:
        for logical in changed:
            imp = g.impact(logical)
            src = STAGE / logical
            if logical.lower().endswith(".c3"):
                try:
                    imp.staged = g.staged_change(logical, src.read_bytes())
                except OSError as e:
                    imp.staged = {"error": f"could not read {src}: {e}"}
            print("=" * 72)
            depclose.render(imp, limit=args.limit)
            print("")
    return 0


def cmd_impact(args) -> int:
    """`comod impact <path-or-id>` -- who references this asset?"""
    import depclose

    with depclose.DepGraph(args.root, progress=True) as g:
        path, note = depclose.resolve_target(g, args.target)
        if note:
            print(f"({note})")
        if not path:
            return 1
        imp = g.impact(path)
        staged = STAGE / path
        if staged.is_file() and path.lower().endswith(".c3"):
            imp.staged = g.staged_change(path, staged.read_bytes())
        depclose.render(imp, limit=args.limit)
    return 0


def cmd_asset_root(args) -> int:
    r"""`comod asset-root <path-or-id>` -- everything that goes with an asset.

    The UNFILTERED view of the one companion-set resolver (`tools/assetroot.py`):
    `impact` answers "who references this file"; this groups the same closure
    into the satellite types the panel shows -- geometry, textures including
    alternatives, animation binding, effect layers with their form, the binding
    rows, materials -- and it filters NOTHING, so a declared-but-absent
    companion prints AS absent and the closure's blind spots print on every run.
    That is the difference from the Model Viewer view, which hides absent
    satellites; `--json --view model` prints that projection instead.
    """
    import depclose
    import assetroot

    with depclose.DepGraph(args.root, progress=True) as g:
        path, note = depclose.resolve_target(g, args.target)
        if note:
            # stderr so `--json` stays a clean document on stdout.
            print(f"({note})", file=sys.stderr)
        if not path:
            return 1
        sat = assetroot.resolve(g, path)
        if args.json:
            print(json.dumps(assetroot.to_json(sat, view=args.view), indent=2))
        else:
            assetroot.render(sat, limit=args.limit)
    return 0


def cmd_effects(args) -> int:
    r"""`comod effects` -- what plays, and what it needs.

    The forward half of `comod impact`. `impact` answers "who reaches this
    file"; this answers "what does this effect need", which is the question a
    modder asks BEFORE editing a weapon rather than after.

    Every mode prints the TABLES READ block first, because the answer changes
    with the file that produced it: on 5517/6090/6609/7205 the effect
    definitions, the mesh id table and the texture id table are all read from
    compiled `.dbc` twins and the `.ini` beside each is a decoy, while on
    5017/5165/7878 (and CCO) there are no twins and the plaintext IS the live
    table. Both are normal; which one happened is not guessable from the
    numbers, so it is printed.
    """
    # --census, and --list with any form filtering, are answered by the
    # form index rather than the dependency graph. --list's optional
    # SUBSTRING and --match are the same slot; refuse rather than pick.
    if args.match and isinstance(args.list, str) and args.list:
        print("give the substring to --list OR to --match, not both")
        return 2
    if args.census or args.form or args.limit or args.match:
        if not args.match and isinstance(args.list, str):
            args.match = args.list
        return _cmd_effects_forms(args)

    import depclose

    with depclose.DepGraph(args.root, progress=True) as g:
        geometry = not args.no_geometry
        if args.tables:
            rows = g.effect_tables()
            if args.json:
                print(json.dumps({"root": str(g.root), "tables": rows,
                                  "limits": g.effect_table_limits()},
                                 indent=2, ensure_ascii=False))
                return 0
            print(f"install    {g.root}")
            print("")
            depclose.render_effect_tables(rows)
            print("")
            print("NOT ENUMERATED -- rule classes outside this report")
            for line in g.effect_table_limits():
                print(f"   * {line}")
            return 0 if rows else 1

        if args.list is not None:
            db = g.effect_db()
            if db is None:
                print("UNMEASURED -- the effect tables were not read on this "
                      "install; this is NOT 'no effects are defined'.")
                for line in g.build_limits:
                    print(f"   * {line}")
                return 1
            sub = args.list.lower()
            names = sorted(n for n in db.effects if sub in n.lower())
            if args.json:
                print(json.dumps(names, indent=2, ensure_ascii=False))
                return 0
            depclose.render_effect_tables(g.effect_tables())
            print("")
            for n in names:
                print(f"   {n}   ({len(db.effects[n].layers)} layer(s))")
            print(f"\n{len(names)} of {len(db.effects)} defined effect name(s)"
                  + (f" match {args.list!r}" if sub else ""))
            return 0

        if args.action:
            db = g.effect_db()
            if db is None:
                print("UNMEASURED -- the effect tables were not read on this "
                      "install; whether this action plays an effect is "
                      "UNKNOWN, not 'no'.")
                for line in g.build_limits:
                    print(f"   * {line}")
                return 1
            app, act = args.action
            name = db.lookup_action_effect(app, act)
            depclose.render_effect_tables(g.effect_tables())
            print("")
            if not name:
                print(f"no Action3DEffect row matches appearance {app} "
                      f"action {act}. The table WAS read "
                      f"({len(db.action_rules)} rows), so this is 'no rule', "
                      f"not 'unknown'.")
                for line in g.effect_table_limits():
                    print(f"   * {line}")
                return 1
            print(f"appearance {app} action {act} -> {name}")
            print("")
            depclose.render_effect(g.effect_closure(name, geometry=geometry),
                                   tables=False)
            return 0

        if args.weapon:
            w = g.weapon_effects(args.weapon, geometry=geometry)
            if args.json:
                print(json.dumps(asdict(w), indent=2, ensure_ascii=False,
                                 default=str))
            else:
                depclose.render_weapon(w)
            return 0 if w.measured else 1

        c = g.effect_closure(args.effect, geometry=geometry)
        if args.json:
            print(json.dumps(asdict(c), indent=2, ensure_ascii=False,
                             default=str))
        else:
            depclose.render_effect(c)
        return 0 if (c.measured and c.found) else 1


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
                f"Either add to it:   py -3 tools/comod.py --root \"{root}\" "
                f"install --amend --yes\n"
                f"or revert it first: py -3 tools/comod.py --root \"{root}\" "
                f"uninstall --yes\n"
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
    # `--root` is a TOP-LEVEL option: it goes before the subcommand, or
    # argparse answers "unrecognized arguments". The owner typed the old form
    # of this line on 2026-09-29 and got exactly that, with a game that would
    # not launch behind it. `tests/test_comod_amend.TheRevertHintParses`
    # feeds these two lines back through `build_parser()`.
    print(f"revert everything:  py -3 tools/comod.py --root \"{root}\" "
          f"uninstall --yes")
    print(f"revert just this:   py -3 tools/comod.py --root \"{root}\" "
          f"uninstall --last --yes")
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
#:
#: ONE HOME, in `plugins`: this constant and the viewer's `DETECT_CONFIDENT`
#: were the same number written twice, and the tie test beside it was written
#: twice with DIFFERENT boundaries (`<` here, `<=` there), so a gap of exactly
#: 0.05 was a tie on the picker and a clean identification at `clients add`.
CONFIDENT = 0.9

#: Two candidates within this of each other are a tie, and a tie is not an
#: answer. `detect` breaks it by score then name, which is dictionary order
#: wearing the clothes of evidence.
#:
#: The comparison is `<=`, from `plugins.verdict` -- see `plugins.TIE` for
#: why that boundary and not this file's old `<`.
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
    if len(ranked) > 1 and (score - ranked[1][1]) <= TIE:
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
    # WHERE the records live and WHY, always -- a reader who sees only the
    # records cannot tell a configured shared root from a silent fallback
    # into this checkout, and those differ by whether `uninstall` run from
    # another worktree can restore the wrong file.
    print(f"records:     {installs_dir()}")
    print(f"             {installs_why()}")
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
# .ani frame sequences
# ---------------------------------------------------------------------------
#
# An `.ani` is a plain-text INI naming an ORDERED set of separate `.dds` frames
# per section. These four verbs exist so a user meets the set as one unit --
# see it in order, take it out together, put it back together -- rather than as
# N unrelated textures whose relationship lives only in a text file they were
# never shown.
#
# THE ORDER IS THE ANIMATION. An export that lost it, or an import that
# renumbered `Frame0..N` inconsistently with `FrameAmount`, would produce a
# manifest the client reads as a DIFFERENT animation rather than a broken one:
# no crash, no dialog, no log line. The mechanics and every refusal live in
# `tools/aniset.py`; these are the surface.

def _aniset():
    import aniset  # noqa: PLC0415
    return aniset


def cmd_ani_list(args) -> int:
    return _aniset().list_files(Path(args.root))


def _ani_run(args, fn) -> int:
    a = _aniset()
    try:
        with open_view(args) as R:
            return fn(a, R)
    except a.SequenceError as e:
        print(f"REFUSED: {e}")
        return 1


def cmd_ani_show(args) -> int:
    return _ani_run(args, lambda a, R: a.show(R, args.ani, args.section,
                                              args.limit))


def cmd_ani_export(args) -> int:
    def go(a, R):
        d = a.export(R, args.ani, args.section, args.out, args.png)
        rows = json.loads((d / a.MANIFEST).read_text("utf-8"))["frames"]
        absent = sum(1 for r in rows if not r["present"])
        print(f"exported {len(rows)} ordinal(s) -> {d}")
        if absent:
            print(f"  {absent} of them are DECLARED BUT ABSENT on this "
                  f"install and are marked with a zero-byte .absent file at "
                  f"their own ordinal, not omitted")
        print(f"  the NNN_ prefix is the frame order and the only thing "
              f"`ani-import` reads it from")
        print(f"  re-import with:  py -3 tools/comod.py ani-import {d}")
        return 0
    return _ani_run(args, go)


def cmd_ani_import(args) -> int:
    def go(a, R):
        code, lines = a.import_set(Path(args.workdir), R, args.drop_missing,
                                   args.dry_run)
        for ln in lines:
            print(ln)
        return code
    return _ani_run(args, go)
# tag / bookmark / rename overlay (core/tags.py)
# ---------------------------------------------------------------------------

def _tag_store(args) -> "tagstore.TagStore":
    """The overlay store this invocation writes to: `--store` if given, else
    the per-user default beside the config file."""
    return tagstore.TagStore(getattr(args, "store", None) or None)


def _tag_key(args) -> tuple:
    """Resolve the asset named on the command line to its ``(path, hash)`` key.

    The path is the logical path as typed (normalised by the store); the hash
    is computed from the asset's CURRENT bytes in the active view. Recording
    both is the whole point -- a later patch or re-encode is then surfaced as
    MOVED / CHANGED rather than lost.
    """
    logical = args.logical
    with open_view(args) as R:
        if not R.locate(logical):
            print(f"not found: {logical}")
            return None
        blob = R.read(logical)
    return tagstore.norm_path(logical), tagstore.hash_bytes(blob)


def _print_record(r: dict) -> None:
    name = f"  \"{r['name']}\"" if r.get("name") else ""
    mark = " *" if r.get("bookmark") else "  "
    tags = ("  [" + ", ".join(r["tags"]) + "]") if r["tags"] else ""
    print(f"{mark}{r['path']}{name}{tags}")
    print(f"     hash {r['hash']}"
          + (f"   note: {r['note']}" if r.get("note") else ""))


def cmd_tag(args) -> int:
    store = _tag_store(args)
    verb = args.tagverb

    if verb == "list":
        recs = store.list(tag=getattr(args, "tag", None),
                          bookmarked=True if getattr(args, "bookmarked", False)
                          else None)
        if getattr(args, "json", False):
            print(json.dumps(recs, indent=1))
            return 0
        if not recs:
            print("no tags recorded"
                  + (f" for tag {args.tag!r}" if getattr(args, "tag", None)
                     else "") + f"  (store: {store.path})")
            return 0
        counts = store.all_tags()
        if counts and not getattr(args, "tag", None):
            print("tags in use: "
                  + ", ".join(f"{t} ({n})" for t, n in counts.items()))
        for r in recs:
            _print_record(r)
        return 0

    if verb == "export":
        data = store.export()
        Path(args.file).write_text(json.dumps(data, indent=1), "utf-8")
        print(f"exported {len(data)} record(s) to {args.file}")
        return 0

    if verb == "import":
        try:
            data = json.loads(Path(args.file).read_text("utf-8"))
        except (OSError, ValueError) as e:
            print(f"cannot read {args.file}: {e}")
            return 1
        if isinstance(data, dict):
            data = data.get("records", [])
        if not isinstance(data, list):
            print(f"{args.file} is not a tag array")
            return 1
        stats = store.import_records(data, replace=getattr(args, "replace", False))
        store.save()
        print(f"imported: {stats['added']} added, {stats['merged']} merged, "
              f"{stats['skipped']} skipped  (store: {store.path})")
        return 0

    # The mutating verbs all key on a resolved asset.
    key = _tag_key(args)
    if key is None:
        return 1
    path, h = key

    if verb == "add":
        r = store.add_tag(path, h, *args.tag)
    elif verb == "remove":
        r = store.remove_tag(path, h, args.tag)
        if r is None:
            print(f"no overlay for {path} at this content hash")
            return 1
    elif verb == "rename":
        r = store.set_name(path, h, args.name)
    elif verb == "bookmark":
        r = store.bookmark(path, h, on=not getattr(args, "off", False))
    else:                                                    # pragma: no cover
        print(f"unknown tag verb {verb!r}")
        return 2
    store.save()
    _print_record(r)
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """The CLI, as an object -- so a test can feed a printed command back
    through it. Split out of `main` on 2026-09-29 after `install`'s revert
    hint turned out to be unparseable (`--root` after the subcommand, where
    it is a top-level option) and nothing had ever tried to type it."""
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
    p.add_argument("--art", action="store_true",
                   help="also show which rows have 3D art in THIS install")
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

    p = sub.add_parser("anim",
                       help="how one appearance animates: motion set, timing, "
                            "loop, root motion, and whether a staged mesh "
                            "still binds")
    p.add_argument("ident", help="an appearance ID, or a logical .c3 path "
                                 "(then only the animation forms are shown)")
    p.add_argument("--action", default="100",
                   help="3-digit action code or alias "
                        "(idle/walk/run/jump/swing/cast/die; default 100)")
    p.add_argument("--weapon", default="",
                   help="right-hand weapon appearance, e.g. 410009")
    p.add_argument("--off-hand", dest="off_hand", default="")
    p.add_argument("--distance", type=int,
                   help="jump distance tier (10..120), for the 12/13-wide keys")
    p.add_argument("--as-shape", dest="as_shape",
                   help="override the shape derived from the appearance")
    # No literal default: 41 lives in `anim.DEFAULT_FRAME_INTERVAL_MS` with
    # its evidence and its two rejected candidates attached, and a second
    # copy here is how the two drift apart. `cmd_anim` fills it in.
    p.add_argument("--interval", type=int, default=None,
                   help="ms per frame (default: anim.py's measured 41)")
    p.add_argument("--table")
    p.set_defaults(func=cmd_anim)

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

    p = sub.add_parser("export",
                       help="export an asset to the right tool's format, with "
                            "a manifest (item 5: .c3 for Blender, .dds as PNG, "
                            "table as CSV, .ani as its frame set)")
    p.add_argument("asset", nargs="+",
                   help="one or more logical asset paths, e.g. "
                        "c3/body/7130030.c3")
    p.add_argument("--for", dest="for_tool", metavar="TOOL",
                   help="the tool you will open these in (blender/gimp/calc/"
                        "audacity/text); validated against the asset type")
    p.add_argument("--zip", metavar="FILE",
                   help="pack the bundle into this .zip instead of a folder")
    p.add_argument("--out", metavar="DIR",
                   help="write the bundle folder here (default: work/export)")
    p.add_argument("--shared", action="append", metavar="LOGICAL",
                   help="mark this asset as SHARED (appears under more than "
                        "one item; editing it edits both); repeatable")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import",
                       help="reimport an export bundle (folder or .zip): "
                            "enforce the format rules and land it in the stage "
                            "tree (item 5). Distinct from `import-png`.")
    p.add_argument("src", help="the export folder or .zip")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("stage", help="copy an original asset into the stage tree unchanged")
    p.add_argument("logical")
    p.add_argument("--no-effect-check", action="store_true",
                   help="skip the effect-dependency warning; the "
                        "report then SAYS the question was not asked "
                        "rather than going silent")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("stage-map",
                       help="rebuild a map .7z around a modified .DMap and "
                            "stage it (the ONE asset loose-file override "
                            "cannot reach)")
    p.add_argument("map", help="a map name (desert), or the registry path "
                               "(map/map/desert.7z)")
    p.add_argument("--dmap", help="use this file as the new .DMap payload")
    p.add_argument("--set", action="append", metavar="X,Y,MASK[,SURF[,ELEV]]",
                   help="edit one cell of the shipped map; repeatable")
    p.set_defaults(func=cmd_stage_map)

    p = sub.add_parser("stage-mesh",
                       help="validate and stage a .c3 exported from Blender")
    p.add_argument("c3", help="the exported .c3 file")
    p.add_argument("logical", nargs="?",
                   help="target path, e.g. c3/mesh/002135000.c3 "
                        "(inferred from the filename when omitted)")
    p.add_argument("--force", action="store_true",
                   help="stage even if the chunk layout changed")
    p.add_argument("--restore-motion", action="store_true",
                   help="give a donor that carries NO MOTI the original's "
                        "motion tracks, but only where that is provable -- "
                        "same PHY count and every position and bone binding "
                        "unchanged. Refuses otherwise; see "
                        "docs/moti_retarget_2026-09-06.md")
    p.add_argument("--no-effect-check", action="store_true",
                   help="skip the effect-dependency warning; the "
                        "report then SAYS the question was not asked "
                        "rather than going silent")
    p.set_defaults(func=cmd_stage_mesh)

    p = sub.add_parser("diff", help="show what the stage tree changes")
    p.add_argument("--impact", action="store_true",
                   help="also report who references each changed file "
                        "(slow: it resolves every appearance reference in "
                        "the install)")
    p.add_argument("--limit", type=int, default=12,
                   help="how many references to list per group")
    p.add_argument("--no-effect-check", action="store_true",
                   help="skip the effect-dependency warning; the "
                        "report then SAYS the question was not asked "
                        "rather than going silent")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("impact",
                       help="who references this asset, and what a change "
                            "to it would break")
    p.add_argument("target", help="a logical path (c3/mesh/440140.c3) or a "
                                  "bare asset id (440140)")
    p.add_argument("--limit", type=int, default=12,
                   help="how many references to list per group")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("asset-root",
                       help="everything that goes with an asset -- the "
                            "unfiltered companion-set view")
    p.add_argument("target", help="a logical path (c3/mesh/440140.c3) or a "
                                  "bare asset id (440140)")
    p.add_argument("--limit", type=int, default=24,
                   help="how many satellites to list per group")
    p.add_argument("--json", action="store_true",
                   help="emit the resolver's JSON instead of the text view")
    p.add_argument("--view", choices=("asset-root", "model"),
                   default="asset-root",
                   help="asset-root = unfiltered (default); model = the "
                        "present-only projection the Model Viewer would show")
    p.set_defaults(func=cmd_asset_root)

    p = sub.add_parser("effects",
                       help="what effect plays for a weapon or action, and "
                            "what assets it needs")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--weapon", metavar="APPEARANCE",
                   help="weapon appearance id, e.g. 410009")
    g.add_argument("--effect", metavar="NAME",
                   help="effect name, e.g. Flash4102")
    g.add_argument("--action", nargs=2, metavar=("APPEARANCE", "ACTION"),
                   help="the effect one action of one appearance plays")
    g.add_argument("--list", nargs="?", const="", metavar="SUBSTRING",
                   help="list defined effect names, optionally filtered")
    g.add_argument("--tables", action="store_true",
                   help="which file answered for each effect table")
    g.add_argument("--census", action="store_true",
                   help="how many effects carry each animation form "
                        "(all three rows always printed, so an absent "
                        "form is a measured 0)")
    p.add_argument("--no-geometry", action="store_true",
                   help="skip reading each layer's C3; the animation form "
                        "is then reported UNKNOWN rather than guessed")
    p.add_argument("--form", action="append", default=[], metavar="FORM",
                   help="with --list, keep only effects carrying this "
                        "form. Repeatable. phy|moti|PHY+MOTI, "
                        "ribbon|trail|shap|smot|SHAP+SMOT, "
                        "particle|ptcl|ptc3|PTCL/PTC3. An effect carrying "
                        "two forms is listed under BOTH.")
    p.add_argument("--form-mode", dest="form_mode",
                   choices=("any", "all", "none"), default="any",
                   help="with several --form: any (union, default), all "
                        "(carries every one), none")
    p.add_argument("--match", default="",
                   help="substring of the effect name, case-insensitive; "
                        "the same thing as the argument to --list")
    p.add_argument("--limit", type=int, default=0,
                   help="print at most N rows (0 = all); the total is "
                        "printed either way")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_effects)

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

    # -- .ani frame sequences -------------------------------------------------
    # Named `ani-*` and not `ani`: `anim` above is a DIFFERENT thing (how one
    # appearance's 3D motion set plays), and two commands whose first three
    # letters agree is how a user runs the wrong one. See tools/aniset.py.
    p = sub.add_parser("ani-list",
                       help="every .ani frame manifest on this install, with "
                            "its section, frame and finding counts")
    p.set_defaults(func=cmd_ani_list)

    p = sub.add_parser("ani-show",
                       help="one .ani sequence AS a sequence: its frames in "
                            "order, and which of them the install does not ship")
    p.add_argument("ani", help="e.g. ani/cartoon.ani")
    p.add_argument("section", nargs="?",
                   help="omit to list the multi-frame sections in the file")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_ani_show)

    p = sub.add_parser("ani-export",
                       help="write a sequence's whole ordered frame set to "
                            "Installed/work, ordinal-prefixed")
    p.add_argument("ani")
    p.add_argument("section")
    p.add_argument("--out")
    p.add_argument("--png", action="store_true",
                   help="also decode each frame to PNG for an image editor")
    p.set_defaults(func=cmd_ani_export)

    p = sub.add_parser("ani-import",
                       help="re-import an edited frame set and stage it, "
                            "rewriting Frame0..N and FrameAmount together")
    p.add_argument("workdir", help="the directory `ani-export` wrote")
    p.add_argument("--drop-missing", dest="drop_missing", action="store_true",
                   help="remove ordinals whose art this install does not "
                        "ship, renumbering the rest; the report says exactly "
                        "what moved. Without it they keep their slot and "
                        "their declared path.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.set_defaults(func=cmd_ani_import)
    # tag: the local overlay layer -- tag / bookmark / rename any asset, keyed
    # on BOTH path and content hash (core/tags.py). Rename is an overlay, never
    # a write to the asset.
    p = sub.add_parser("tag",
                       help="tag, bookmark or locally rename an asset "
                            "(a local overlay -- nothing is written to the "
                            "asset itself); export/import the tag set")
    p.add_argument("--store", metavar="FILE",
                   help="the overlay store JSON (default: beside your config)")
    tsub = p.add_subparsers(dest="tagverb", required=True)

    tp = tsub.add_parser("add", help="add one or more tags to an asset")
    tp.add_argument("logical", help="the logical asset path, e.g. "
                                    "c3/weapon/410009.dds")
    tp.add_argument("tag", nargs="+", help="one or more tags")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("remove", help="remove one tag from an asset")
    tp.add_argument("logical"); tp.add_argument("tag")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("rename",
                         help="set a local display name (overlay only -- the "
                              "file on disk is NOT renamed); empty clears it")
    tp.add_argument("logical"); tp.add_argument("name")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("bookmark", help="bookmark an asset (or --off)")
    tp.add_argument("logical")
    tp.add_argument("--off", action="store_true", help="clear the bookmark")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("list", help="list overlay records")
    tp.add_argument("--tag", help="only records carrying this tag")
    tp.add_argument("--bookmarked", action="store_true",
                    help="only bookmarked records")
    tp.add_argument("--json", action="store_true")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("export", help="write the tag set to a JSON array")
    tp.add_argument("file")
    tp.set_defaults(func=cmd_tag)

    tp = tsub.add_parser("import",
                         help="restore a tag set (merges by default)")
    tp.add_argument("file")
    tp.add_argument("--replace", action="store_true",
                    help="wipe the store first instead of merging")
    tp.set_defaults(func=cmd_tag)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
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
