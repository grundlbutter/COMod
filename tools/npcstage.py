#!/usr/bin/env python3
r"""
npcstage.py -- apply an npcsplit plan into mods/stage/, and nowhere else.

The split is planned by `npcsplit.py` and applied here. That division is not
tidiness: `npcsplit` AST-scans its own source and asserts it contains no
filesystem-write call, so the tool that reads your install to decide what
should change is provably incapable of changing it. Putting the writer in the
same module would delete that property. One planner, one applier.

This module writes into the STAGE TREE only -- `mods/stage/`. It never touches
the game install. `comod.py install` is what copies the stage tree into the
install, and it owns the backups and the manifest that `comod.py uninstall`
reverts. Nothing here can install, and nothing here can be uninstalled,
because nothing here has been applied to anything you play.

Three refusals, all of them the difference between staging a split and
staging a mess:

  * A plan that did not come back clean is not staged. If npcsplit refused an
    instruction or failed a check, the plan is incomplete by its own account,
    and a partial split -- new art with no table edit, or a table edit
    pointing at art that was never copied -- is worse than no split.
  * Every OLD value is re-read here and compared before anything is written.
    npcsplit read them when it planned; if the file changed since, the plan
    describes an install that no longer exists.
  * Nothing is written until every write has been decided. The staged files
    appear together or not at all, so an interrupted run cannot leave the
    stage tree half-committed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT / "core"))

import coassets                                            # noqa: E402
import coroot                                              # noqa: E402
import safepath                                            # noqa: E402

STAGE = PROJECT / "mods" / "stage"


class Refused(Exception):
    """Raised instead of staging something this module cannot vouch for."""


def plan_for(root: str, npc: str, control: str = "",
             npc_type=None, fork: bool = False) -> dict:
    """Run npcsplit and return its plan.

    A subprocess, and deliberately so: npcsplit is the single planner, and
    running it rather than importing pieces of it means the page, the command
    line and this stager cannot drift into three different answers about which
    group is free.
    """
    cmd = [sys.executable, str(HERE / "npcsplit.py"), "--root", str(root),
           "--npc", npc or "", "--json"]
    if npc_type is not None:
        cmd += ["--type", str(int(npc_type))]
    if fork:
        cmd += ["--fork-appearance"]
    if control:
        cmd += ["--control", control]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    if not (r.stdout or "").strip():
        raise Refused("npcsplit produced no plan.\n" + (r.stderr or "")[:2000])
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise Refused("npcsplit's plan did not parse as JSON: %s" % e)


def check_plan(plan: dict) -> None:
    """Refuse a plan that is not complete by npcsplit's own account."""
    if plan.get("refusals"):
        raise Refused(
            "npcsplit refused %d instruction(s), so the split is incomplete "
            "and nothing is staged:\n  %s"
            % (len(plan["refusals"]),
               "\n  ".join("%s -- %s" % (r["what"], r["why"])
                           for r in plan["refusals"])))
    if plan.get("failures"):
        raise Refused(
            "npcsplit failed %d check(s); a plan that does not verify is not "
            "staged:\n  %s" % (len(plan["failures"]),
                               "\n  ".join(map(str, plan["failures"]))))
    if not plan.get("ok"):
        # npcsplit can refuse before it has a plan to refuse WITH -- an
        # ambiguous name is the common one ("Armorer" matches 2 rows). Its
        # account of why is in the report, and dropping it here would turn a
        # precise refusal into "exited 2", which is the kind of empty answer
        # this toolchain keeps being bitten by.
        tail = (plan.get("report") or "").strip().splitlines()
        why = "\n  ".join(tail[-12:]) if tail else "it printed no reason"
        raise Refused("npcsplit exited %s and did not produce a usable plan.\n"
                      "  %s" % (plan.get("exit"), why))
    if not plan.get("adds") and not plan.get("edits"):
        # An empty plan is not a no-op to be reported as success: it means the
        # planner found nothing to do, which for a split is a fault.
        raise Refused("the plan contains no art to copy and no field to "
                      "change; there is nothing that would constitute a split")


def _row_span(text: str, row_type) -> tuple:
    """The character span of the row whose `type` is `row_type`.

    Located by `type`, which is unique per row, rather than by name -- names
    repeat in npc.json (there are two rows called Winni) and editing the wrong
    one would be silent.
    """
    hits = [m.start() for m in
            re.finditer(r'"type"\s*:\s*%d\s*[,}]' % int(row_type), text)]
    if len(hits) != 1:
        raise Refused('"type": %s matches %d rows in the table; it must match '
                      "exactly one for the edit to be unambiguous"
                      % (row_type, len(hits)))
    at = hits[0]
    start = text.rfind("{", 0, at)
    end = text.find("}", at)
    if start < 0 or end < 0:
        raise Refused("could not find the object braces around row type %s"
                      % row_type)
    return start, end + 1


def build_edits(table_text: str, plan: dict) -> str:
    """Apply the plan's field edits to the table text, OLD-checked.

    Compare-and-swap: each OLD was read when npcsplit planned, and is verified
    here against the bytes being edited now. Writing a NEW over a value that is
    no longer the OLD would be applying a plan to an install it did not
    describe.
    """
    start, end = _row_span(table_text, plan["rowType"])
    block = table_text[start:end]
    for e in plan["edits"]:
        pat = re.compile(r'("%s"\s*:\s*)([^,\r\n}]+)' % re.escape(e["field"]))
        m = pat.search(block)
        if not m:
            raise Refused("field %r is not present in row type %s"
                          % (e["field"], plan["rowType"]))
        have = m.group(2).strip()
        if have != str(e["old"]).strip():
            raise Refused(
                "field %r reads %s now, but the plan was written against %s. "
                "The table changed since it was planned; re-plan rather than "
                "write a value over something else."
                % (e["field"], have, e["old"]))
        block = block[:m.start(2)] + str(e["new"]) + block[m.end(2):]
    return table_text[:start] + block + table_text[end:]




def find_donor(library: Path, entry_id: str) -> dict:
    """The library entry with this id, read off disk.

    Read directly rather than through the viewer's collection object: this is
    a command, it runs without a server, and the entry's sidecar json already
    carries everything the swap needs.
    """
    base = Path(library) / "Collection"
    if not base.is_dir():
        raise Refused("no Collection under %s" % library)
    for p in sorted(base.rglob("*.json")):
        if p.name == "collection.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        if d.get("id") == entry_id:
            d["_dir"] = str(base)
            return d
    raise Refused("no library entry with id %r under %s" % (entry_id, base))


def donor_file_for(donor: dict, action: str) -> tuple:
    """(bytes, description) of the donor art for one action suffix.

    Matched on the ACTION the source path ends with -- the library keeps the
    donor's own `100`/`101`/`190` files, and CCO's freshly allocated ids end
    in the same three. When the donor has no file for an action its base mesh
    is used INSTEAD OF NOTHING, and the caller prints that it did: a silent
    substitution is how a swap comes out subtly wrong with nobody able to say
    where.
    """
    base = Path(donor["_dir"])
    for part in donor.get("parts") or []:
        src = str(part.get("source") or "")
        if src.rsplit("/", 1)[-1] == "%s.c3" % action:
            f = base / part["file"]
            if f.is_file():
                return f.read_bytes(), "%s (donor action %s)" % (part["file"],
                                                                 action)
    mesh = donor.get("mesh")
    if mesh and (base / mesh).is_file():
        return ((base / mesh).read_bytes(),
                "%s (donor has no action %s; its base mesh is used)"
                % (mesh, action))
    raise Refused("the donor has neither an action %s nor a base mesh on disk"
                  % action)


def donor_skin(donor: dict) -> tuple:
    base = Path(donor["_dir"])
    for rel in donor.get("skins") or []:
        f = base / rel
        if f.is_file():
            return f.read_bytes(), rel
    raise Refused("the donor entry lists no skin that is on disk")


def build_motion_rows(text: str, rows: list) -> str:
    """Add each new motion id to the motion table, beside its source row.

    The client resolves a motion id through this table. An id that is not in
    it resolves to nothing, and the client draws no NPC at all -- the npc.json
    row is intact, the art is on disk, and the character is simply absent from
    the world. That is what an unregistered split looks like from inside the
    game, and it is why this is not an optional polish step.

    Inserted beside the source id rather than appended: the file ends in
    `[Section]` blocks, and a bare `id=path` line appended after them would
    land inside the last section instead of the top-level table.
    """
    if not rows:
        return text
    lines = text.splitlines(keepends=True)
    for r in rows:
        want = "%s=" % r["id"]
        for ln in lines:
            if ln.startswith(want):
                raise Refused(
                    "%s already has a row for %s (%s); the allocator's group "
                    "is contradicted, so nothing is written rather than "
                    "overwriting it" % (r["path"], r["id"], ln.strip()))
        new_line = r["line"].rstrip("\r\n") + "\n"
        anchor = (r.get("anchor") or "").strip()
        at = None
        if anchor:
            for i, ln in enumerate(lines):
                if ln.strip() == anchor:
                    at = i + 1
                    break
        if at is None:
            # No anchor found: put it before the first [Section], which is
            # where the top-level id=path table ends.
            at = next((i for i, ln in enumerate(lines)
                       if ln.lstrip().startswith("[")), len(lines))
        lines.insert(at, new_line)
    return "".join(lines)

def stage(root: str, npc: str, control: str = "",
          stage_dir: Path = STAGE, dry: bool = False, npc_type=None,
          fork: bool = False, donor_id: str = "",
          library: str = "") -> dict:
    """Plan the split and write it into the stage tree. Returns what it did."""
    plan = plan_for(root, npc, control, npc_type, fork)
    check_plan(plan)
    view = coassets.AssetRoot(Path(root))
    donor = None
    if donor_id:
        lib = library or coroot.read_settings().get("community_library") or ""
        if not lib:
            raise Refused("a donor was named but no COmmunity Library is "
                          "configured; pass --library DIR")
        donor = find_donor(Path(lib), donor_id)

    # -- decide every write first -----------------------------------------
    # Collected in full before anything lands, so an interrupted run cannot
    # leave the stage tree holding half a split.
    pending: list[tuple[Path, bytes, str]] = []

    for a in plan["adds"]:
        if donor is not None:
            # A SWAP: the new id gets the donor's art, not a copy of the art
            # being split away from. Copying the original is what a split
            # alone does, and it is why an installed split leaves the NPC
            # looking exactly as it did.
            action = Path(a["path"]).stem[-3:]
            data, why = donor_file_for(donor, action)
            label = "%s  <- %s" % (a["path"], why)
        else:
            try:
                data = view.read(a["source"])
            except Exception as exc:
                raise Refused("the source %s no longer reads from this "
                              "install: %s" % (a["source"], exc))
            label = "%s  <- %s" % (a["path"], a["source"])
        if not data:
            raise Refused("the art for %s read as empty" % a["path"])
        dest = safepath.confine(stage_dir, a["path"])
        pending.append((dest, data, label))

    # The donor's skin, at the path the appearance fork registered for it.
    if donor is not None:
        texpath = ""
        for r in (plan.get("rows") or []):
            if r["path"].endswith("3dtexture.ini") and "=" in r["line"]:
                texpath = r["line"].split("=", 1)[1].strip()
        if not texpath:
            raise Refused(
                "a donor was given but the plan registers no texture row, so "
                "there is nowhere to put its skin. --fork-appearance is what "
                "allocates that row; without it the donor's mesh would be "
                "installed under an id nothing points at.")
        skin, srel = donor_skin(donor)
        pending.append((safepath.confine(stage_dir, texpath), skin,
                        "%s  <- %s" % (texpath, srel)))

    table = plan["table"]
    try:
        table_text = view.read(table).decode("utf-8", "replace")
    except Exception as exc:
        raise Refused("could not read %s: %s" % (table, exc))
    # An already-staged table is the one that gets edited, so two splits
    # accumulate instead of the second discarding the first.
    staged_table = safepath.confine(stage_dir, table)
    if staged_table.is_file():
        table_text = staged_table.read_text(encoding="utf-8", errors="replace")
    new_text = build_edits(table_text, plan)
    if new_text == table_text:
        raise Refused("applying the edits changed nothing in %s" % table)
    pending.append((staged_table, new_text.encode("utf-8"),
                    "%s  (%d field(s) in row type %s)"
                    % (table, len(plan["edits"]), plan["rowType"])))

    # The motion table. Without these rows the split installs cleanly and the
    # NPC disappears from the world, so a plan that carries none for a split
    # that adds art is refused rather than staged half-done.
    mrows = plan.get("rows") or []
    if plan.get("adds") and not mrows:
        raise Refused(
            "this plan copies %d art file(s) but registers none of them in "
            "the motion table. An unregistered motion id resolves to nothing "
            "and the client draws no NPC -- re-plan with a build that emits "
            "the registration rows." % len(plan["adds"]))
    # Rows can span several tables now -- the motion table registers the new
    # motion ids, and the appearance fork adds an object row and a texture
    # row in two more. Grouped by path so each file is read, edited and
    # staged once; the earlier version took `rows[0]["path"]` as THE table and
    # would have written every row into whichever came first.
    by_table: dict = {}
    for r in mrows:
        by_table.setdefault(r["path"], []).append(r)
    for tbl, rws in by_table.items():
        try:
            ttext = view.read(tbl).decode("latin-1", "replace")
        except Exception as exc:
            raise Refused("could not read %s: %s" % (tbl, exc))
        staged_t = safepath.confine(stage_dir, tbl)
        if staged_t.is_file():
            ttext = staged_t.read_text(encoding="latin-1", errors="replace")
        tnew = build_motion_rows(ttext, rws)
        if tnew == ttext:
            raise Refused("adding the rows changed nothing in %s" % tbl)
        pending.append((staged_t, tnew.encode("latin-1", "replace"),
                        "%s  (+%d row(s))" % (tbl, len(rws))))

    # The appearance SLOT. A whole `[ObjIDTypeN]` block, appended, because a
    # section belongs with the other sections rather than beside a key.
    for sec in (plan.get("sections") or []):
        tbl = sec["path"]
        try:
            ttext = view.read(tbl).decode("latin-1", "replace")
        except Exception as exc:
            raise Refused("could not read %s: %s" % (tbl, exc))
        staged_t = safepath.confine(stage_dir, tbl)
        if staged_t.is_file():
            ttext = staged_t.read_text(encoding="latin-1", errors="replace")
        header = "[%s]" % sec["name"]
        if header in ttext:
            raise Refused("%s already defines %s; the allocator's slot is "
                          "contradicted and nothing is written"
                          % (tbl, header))
        body = sec["body"].rstrip("\r\n")
        tnew = ttext.rstrip("\r\n") + "\n\n" + body + "\n"
        pending.append((staged_t, tnew.encode("latin-1", "replace"),
                        "%s  (+%s)" % (tbl, header)))

    wrote = []
    if not dry:
        for dest, data, label in pending:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            wrote.append(label)
    return {"ok": True, "dry": dry, "plan": plan,
            "donor": (donor or {}).get("name", ""),
            "staged": [lbl for _, _, lbl in pending],
            "wrote": wrote, "stageDir": str(stage_dir),
            "group": plan["group"], "npc": plan.get("rowName") or npc,
            "cohort": plan.get("cohort", []),
            "report": plan.get("report", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    coroot.add_root_argument(ap)
    ap.add_argument("--npc", default="", help="the npc.json row to split, "
                                            "by name (must be unique)")
    ap.add_argument("--type", type=int, default=None,
                    help="the row's npc.json `type` -- unique, unlike names")
    ap.add_argument("--control", default="",
                    help="a cohort member npcsplit checks by name")
    ap.add_argument("--donor", default="",
                    help="library entry id whose mesh, skin and animations "
                         "are put on this NPC. Needs --fork-appearance.")
    ap.add_argument("--library", default="",
                    help="COmmunity Library root (default: the saved one)")
    ap.add_argument("--fork-appearance", action="store_true",
                    help="also fork the simple_object chain, so a new LOOK "
                         "lands on this NPC alone")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide every write and print it, writing nothing")
    args = ap.parse_args(argv)
    root = getattr(args, "root", None) or coroot.game_root()
    try:
        if not args.npc and args.type is None:
            print("REFUSED -- give --npc NAME or --type N.")
            return 2
        res = stage(str(root), args.npc, args.control, dry=args.dry_run,
                    npc_type=args.type, fork=args.fork_appearance,
                    donor_id=args.donor, library=args.library)
    except Refused as e:
        print("REFUSED -- nothing was staged.\n\n%s" % e)
        return 2
    verb = "would stage" if args.dry_run else "staged into"
    print("%s %s" % (verb, res["stageDir"]))
    for line in res["staged"]:
        print("    " + line)
    print("\n%s splits off group %s; the other %d NPC(s) on its old group are "
          "untouched." % (res["npc"], res["group"], len(res["cohort"])))
    print("\nNothing has been applied to the game install. To review and "
          "apply:")
    print("    py -3 tools/comod.py diff")
    print("    py -3 tools/comod.py install --dry-run")
    print("    py -3 tools/comod.py install --yes")
    print("    py -3 tools/comod.py uninstall --yes    # reverts it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
