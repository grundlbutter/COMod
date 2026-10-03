#!/usr/bin/env python3
r"""
artrepair.py -- which NPCs no longer reach their art, and what would fix it.

Walks the client's own resolution chain for every row of `ini/npc.json`,
reports the exact STAGE at which a break happens, and -- where an installed
asset determines the answer -- emits the ini row that would repair it.

    py -3 tools/artrepair.py --root "C:/Program Files/Classic Conquer 2.0"
    py -3 tools/artrepair.py --root ... --apply        # writes, with a backup

THE CHAIN, AND WHY THE STAGE IS THE WHOLE ANSWER
------------------------------------------------
Four files deep, and a break at each depth looks identical from in-game -- the
NPC is invisible or untextured either way::

    ini/npc.json         "simple_object": 30052
    ini/3DSimpleObj.ini  [ObjIDType30052]  Part0=9990170  Texture0=9990170
    ini/3dobj.ini        9990170 = <path>          <- a MESH path
    ini/3dtexture.ini    9990170 = <path>          <- a TEXTURE path
    the asset            loose file, or an entry in an archive

So the stages this reports are, in order:

    NO_SIMPLE_BLOCK   npc.json names a selector with no [ObjIDTypeN]
    NO_OBJ_ROW        the Part id has no row in 3dobj.ini
    NO_TEX_ROW        the Texture id has no row in 3dtexture.ini
    ASSET_MISSING     the row exists and its path resolves to nothing

**`NO_*_ROW` and `ASSET_MISSING` want opposite repairs** and that is why they
are never merged into one "broken" count.  A missing ROW with the asset
installed is repairable here, mechanically.  A missing ASSET is not: no edit
to a table conjures a file, and a tool that offered one would be inviting the
owner to point a row at nothing.

WHAT THIS WAS BUILT AGAINST -- CCO, 2026-09-21
-----------------------------------------------
437 NPC rows: **433 resolve, 4 do not**, and all 4 break at the row stage::

    type   1  Storekeeper    selector 30051  ids 9990150
    type   8  Warehouseman   selector 30052  ids 9990170
    type  20  Warehouseman   selector 30053  ids 9990190
    type  21  Warehouseman   selector 30054  ids 9990260

Each names one id in BOTH slots, and that id has no row in either table.
Their `.dds` files are the four most recently installed textures on that
install -- 9990150 on 2026-08-25, the other three within three minutes of
each other on 2026-09-06 -- while every other loose texture stops at
2026-08-24.  **The skins were delivered and the rows that index them were
not.**  No mesh by any of those ids exists anywhere on the install.

A fifth block, `[ObjIDType472]` (id 9990472), is broken the same way and has
neither asset.  No `npc.json` row selects it, so nothing is visibly wrong;
it is reported as LATENT and is not counted among the affected NPCs.

AND `npc.json` WAS MODIFIED, WHICH THIS TOOL FIRST REPORTED THAT IT WAS NOT
---------------------------------------------------------------------------
The owner's account was that an update had changed the json so nothing
pointed at the new NPC asset.  The first pass here contradicted that -- every
row parsed, every `simple_object` found its block -- and that was a wrong
answer to a question nobody asked.  **"Resolves" is not "unchanged."**

Diffed against `Clients/CCO-snapshot-2026-08-24`, four rows differ, and they
are exactly the four broken ones::

    type   1  Storekeeper   simple_object  211 -> 30051, all three motions
    type   8  Warehouseman  simple_object    8 -> 30052, all three motions
    type  20  Warehouseman  simple_object    8 -> 30053, all three motions
    type  21  Warehouseman  simple_object    8 -> 30054, all three motions

The update re-pointed four NPCs at four new selectors, added the four blocks
to `3DSimpleObj.ini`, shipped four `.dds` skins -- and shipped no meshes and
no table rows.  Nothing else in either file changed.

That is why `--baseline` exists, and why it is the repair that works here:
every asset the OLD rows name is still installed, so restoring them brings
all four NPCs back with no missing assets at all.  `baseline_reverts`
resolves the baseline's targets **against the live install** rather than the
baseline, because an older row is not automatically safe -- it can name art
this install no longer has, and reverting to it would move the breakage
instead of repairing it.

THREE INSTRUMENT ERRORS THIS TOOL IS SHAPED BY
-----------------------------------------------
All three were made while diagnosing the four rows above, and each produced a
confident wrong number before it was caught.

1. **Loose-file existence is not asset presence.**  A first census reported
   408 of 437 NPCs broken.  CCO ships ~10k entries in `c3.wdf` and ~14.5k in
   `data.wdf` against only ~500 loose meshes, so a `os.path.isfile` check
   answers "missing" for almost everything the client draws every frame.
   Resolution here goes through `coassets.AssetRoot`, which implements the
   verified load order (a loose file shadows the archive entry, never the
   reverse) -- so `exists` means what the client means by it.

2. **Path conventions are not guessable, and the table already holds them.**
   `9990200`'s mesh is `c3/monster/105/100.c3`; `9990090`'s is
   `c3/npc/999009100.c3`; `9990010`'s is `c3/mesh/9990010.c3`.  Building
   candidate paths from an assumed layout produced three false "NOT FOUND"s
   against assets that were present.  `_templates` therefore DERIVES the
   candidate shapes from rows already in the table being repaired, and only
   ever proposes a path that `AssetRoot` confirms.

3. **`Part0 == Texture0` is the norm, not the defect.**  113 of CCO's 141
   blocks do it and 108 of those resolve perfectly.  It was briefly taken for
   the signature of the breakage; a check built on it would have flagged 108
   healthy blocks.  The discriminator is only ever *"the id has no row"*.

WHAT IT WILL NOT DO
--------------------
It will not invent a mesh.  For the four rows above the texture repair is
DETERMINED -- the `.dds` is installed and one template matches it -- and the
mesh repair is UNDETERMINED, because no file by that id exists to point at.

That gap is a content decision, not a lookup, and the install shows what the
decision looks like: `[ObjIDType30030]` is `Part0=9990010 Texture0=9990235`,
a new skin worn on an existing shared mesh.  `core/npcaltskin.py` documents
that pattern and the cohorts it forms.  Blocks 30051-30054 have the shape of
alt-skin blocks whose Part slot was filled with the skin id.  Which mesh each
of them SHOULD wear is for whoever authored the skins to say, so this prints
the resolving alt-skin blocks as context and stops there.

`--apply` appends rows to `ini/3dobj.ini` / `ini/3dtexture.ini` after copying
each to a timestamped `.bak`.  It writes DETERMINED repairs only, refuses any
root under `ConquerAssets/Clients` (that corpus is read-only and an install
written to stops being a control), and appends bare `key=value` lines --
neither table contains a single comment line in 2,269 and 8,943 rows
respectively, and this is not the place to discover whether the client's
parser tolerates one.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import assetdiff                                             # noqa: E402
import coassets                                              # noqa: E402
import npcart                                                # noqa: E402


def _tables(ar, root):
    """`npcart.Tables` with the profile RESOLVED, never inferred.

    `Tables(read)` with no profile falls back to `detect_profile(read)`, and
    for a `colibrary.ServerView` that asks the BASELINE rather than the
    client whose assets are on screen -- `docs/CORRECTIONS.md`
    C-2026-08-09-plugin-c-serverview-profile measured the cost at **25 of 397
    NPCs silently losing their art**. `assetdiff.table_profile_for` is the
    ONE definition of that rule, and its own docstring warns that a fifth
    spelling is how the four sites drift. This module had added three more.

    **STATED PLAINLY: THIS FIXES THE CALL SHAPE, NOT A LIVE DEFECT HERE.**
    Every entry point in this file takes a `root` PATH and constructs its own
    `AssetRoot`, so none of them can currently receive a `ServerView` and the
    baseline can never answer for them today. The gate
    (`test_viewer.py` `ServerViewParseProfile`) is aimed at the SHAPE because
    the shape is what drifts -- the day one of these grows a caller that
    passes a view, the bug arrives silently and costs art rather than an
    error. One helper so a fourth site cannot spell it differently.
    """
    prof, _report = assetdiff.table_profile_for(ar, root)
    return npcart.Tables(ar.read, prof)

#: Stages, in resolution order. The order is the report order.
NO_SIMPLE_BLOCK = "NO_SIMPLE_BLOCK"
NO_OBJ_ROW = "NO_OBJ_ROW"
NO_TEX_ROW = "NO_TEX_ROW"
ASSET_MISSING = "ASSET_MISSING"

DETERMINED = "DETERMINED"
UNDETERMINED = "UNDETERMINED"

#: Refuse to write anywhere under this. See the module docstring.
READ_ONLY_MARKERS = ("conquerassets/clients", "conquerassets\\clients")


def _templates(table: dict) -> list:
    """Candidate path shapes, derived from rows the table already contains.

    A row teaches a template only when its own key appears as the file stem --
    ``9990211 = c3/texture/9990211.dds`` gives ``c3/texture/{id}.dds``.  Rows
    whose stem differs (``9990220 = c3/npc/999220.c3``) teach nothing, because
    whatever maps 9990220 to 999220 is not visible in the row and guessing it
    is exactly error 2 in the docstring.

    Returned most-used first, so the shape the table actually prefers is
    proposed before a rare one.
    """
    counts: dict = {}
    for key, path in table.items():
        p = str(path).replace("\\", "/").strip()
        if not p:
            continue
        head, _, base = p.rpartition("/")
        stem, dot, ext = base.partition(".")
        if not dot or stem != str(key):
            continue
        counts["%s/{id}.%s" % (head, ext) if head else "{id}.%s" % ext] = \
            counts.get("%s/{id}.%s" % (head, ext) if head else "{id}.%s" % ext,
                       0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def _candidate(asset_id, templates, exists) -> str:
    """The first template that names an asset this install actually has.

    Never returns a path that does not resolve -- a proposed row pointing at
    nothing is worse than no proposal, because it looks repaired.
    """
    for t in templates:
        path = t.format(id=asset_id)
        if exists(path):
            return path
    return ""


class Break:
    """One NPC (or latent block) that does not reach its art."""

    def __init__(self, stage, npc_type, name, selector, slot, asset_id,
                 table, path="", candidate="", latent=False):
        self.stage = stage
        self.npc_type = npc_type
        self.name = name
        self.selector = selector
        self.slot = slot                  # "Part0" / "Texture0" / ""
        self.asset_id = asset_id
        self.table = table                # the ini a repair would be written to
        self.path = path                  # the declared path, when there is one
        self.candidate = candidate
        self.latent = latent

    @property
    def status(self) -> str:
        return DETERMINED if self.candidate else UNDETERMINED

    @property
    def row(self) -> str:
        return "%s=%s" % (self.asset_id, self.candidate) if self.candidate else ""

    def as_dict(self) -> dict:
        return {"stage": self.stage, "status": self.status,
                "npcType": self.npc_type, "name": self.name,
                "selector": self.selector, "slot": self.slot,
                "assetId": self.asset_id, "table": self.table,
                "declaredPath": self.path, "candidate": self.candidate,
                "repairRow": self.row, "latent": self.latent}


def scan(root, *, include_latent: bool = True) -> dict:
    """Resolve every NPC, and every simple-object block, against `root`."""
    ar = coassets.AssetRoot(root)
    tables = _tables(ar, root)
    obj_t = _templates(tables.objects)
    tex_t = _templates(tables.textures)

    breaks: list = []
    seen_blocks: set = set()

    def check_block(selector, npc_type, name, latent=False):
        block = tables.simple.get(selector)
        if block is None:
            breaks.append(Break(NO_SIMPLE_BLOCK, npc_type, name, selector,
                                "", selector, "ini/3DSimpleObj.ini",
                                latent=latent))
            return
        # `parts` on a Tables entry is the DECLARED COUNT, not the pairs --
        # the pairs are `part`/`texture` for slot 0 plus `extra` for the rest.
        # Reading it as a sequence raises rather than silently checking slot 0
        # only, which is the failure this comment exists to keep from coming
        # back as a quiet half-resolution of every multi-part block.
        parts = [(block.get("part"), block.get("texture"))] + \
            list(block.get("extra") or ())
        for i, (pid, tid) in enumerate(parts):
            for slot, aid, table, tmpl in (
                    ("Part%d" % i, pid, "ini/3dobj.ini", obj_t),
                    ("Texture%d" % i, tid, "ini/3dtexture.ini", tex_t)):
                if aid is None:
                    continue
                declared = (tables.objects if "3dobj" in table
                            else tables.textures).get(str(aid))
                if declared is None:
                    declared = (tables.objects if "3dobj" in table
                                else tables.textures).get(aid)
                if declared is None:
                    breaks.append(Break(
                        NO_OBJ_ROW if "3dobj" in table else NO_TEX_ROW,
                        npc_type, name, selector, slot, aid, table,
                        candidate=_candidate(aid, tmpl, ar.exists),
                        latent=latent))
                elif not ar.exists(str(declared).replace("\\", "/")):
                    breaks.append(Break(
                        ASSET_MISSING, npc_type, name, selector, slot, aid,
                        table, path=str(declared),
                        candidate=_candidate(aid, tmpl, ar.exists),
                        latent=latent))

    for row in tables.npcs:
        sel = row.get("simple_object")
        if sel is None:
            continue
        seen_blocks.add(sel)
        before = len(breaks)
        check_block(sel, row.get("type"), row.get("name", ""))
        del before

    npc_breaks = list(breaks)
    if include_latent:
        for sel in sorted(b for b in tables.simple if b not in seen_blocks):
            check_block(sel, None, "", latent=True)

    affected = sorted({(b.npc_type, b.name) for b in npc_breaks
                       if b.npc_type is not None})

    # WOULD APPLYING EVERY DETERMINED REPAIR ACTUALLY PUT THIS NPC BACK?
    #
    # For the four rows this tool was built against the answer is NO, and the
    # counts alone say the opposite: "4 repairable" against 4 broken NPCs reads
    # as a complete fix. Each of those NPCs has TWO breaks -- a texture row
    # that is determined and a mesh row that is not -- so writing the four
    # determined rows leaves all four NPCs exactly as invisible as before,
    # while every number in the report improves.
    #
    # A per-NPC verdict is the only honest unit, because the NPC is what the
    # owner sees. An NPC is restored only when NONE of its breaks survive.
    per_npc: dict = {}
    for b in npc_breaks:
        if b.npc_type is None:
            continue
        per_npc.setdefault((b.npc_type, b.name), []).append(b)
    restored, partial = [], []
    for key, rows in sorted(per_npc.items()):
        blocking = [r for r in rows if r.status != DETERMINED]
        (partial if blocking else restored).append((key, rows, blocking))
    return {"root": str(root), "npcs": len(tables.npcs),
            "blocks": len(tables.simple), "objRows": len(tables.objects),
            "texRows": len(tables.textures), "objTemplates": obj_t[:4],
            "texTemplates": tex_t[:4], "breaks": breaks,
            "affectedNpcs": affected,
            "resolvedNpcs": len(tables.npcs) - len(affected),
            "wouldRestore": [k for k, _, _ in restored],
            "wouldRemainBroken": [(k, [(r.slot, r.stage) for r in blocking])
                                  for k, _, blocking in partial]}


def alt_skin_context(root, limit: int = 8) -> list:
    """Resolving blocks that wear a shared mesh under their own skin.

    Context for an UNDETERMINED mesh, never a proposal. These are the blocks
    whose Part and Texture ids differ and both resolve -- the shape an
    alt-skin block has when it is authored correctly.
    """
    ar = coassets.AssetRoot(root)
    tables = _tables(ar, root)
    out = []
    for sel, block in sorted(tables.simple.items()):
        pid, tid = block.get("part"), block.get("texture")
        if pid is None or tid is None or pid == tid:
            continue
        pm = tables.objects.get(str(pid)) or tables.objects.get(pid)
        tm = tables.textures.get(str(tid)) or tables.textures.get(tid)
        if pm and tm and ar.exists(str(pm).replace("\\", "/")):
            out.append((sel, pid, tid, str(pm)))
    return out[:limit]


def baseline_reverts(root, baseline) -> list:
    """NPC rows a known-good install would restore, verified against `root`.

    The repair that actually works when an update re-points NPCs at art it
    forgot to ship. For each row whose content differs from `baseline`, this
    resolves the BASELINE's targets **against the live install** and offers
    the revert only if every one of them -- mesh, texture and each motion --
    is present there now.

    That last clause is the whole value. A baseline row is not automatically
    safe: the older install may name art this one no longer has, and a revert
    to a row that resolves on the baseline and not here just moves the
    breakage. Resolution is done on `root` for exactly that reason.

    Returns ``[(npc_type, name, baseline_row, live_row, ok, detail), ...]``
    for every differing row, `ok` being whether the revert is verified.
    """
    import json as _json
    ar = coassets.AssetRoot(root)
    tables = _tables(ar, root)
    base_rows = _json.loads(
        (Path(baseline) / "ini" / "npc.json").read_bytes()
        .decode("utf-8", "replace"))
    base = {r.get("type"): r for r in base_rows}
    live = {r.get("type"): r for r in tables.npcs}

    out = []
    for typ in sorted(set(base) & set(live), key=lambda x: (x is None, x)):
        if base[typ] == live[typ]:
            continue
        row = base[typ]
        detail, ok = [], True
        sel = row.get("simple_object")
        block = tables.simple.get(sel)
        if block is None:
            detail.append("selector %s has no block here" % sel)
            ok = False
        else:
            for slot, aid, table in (("Part0", block.get("part"), tables.objects),
                                     ("Texture0", block.get("texture"),
                                      tables.textures)):
                p = table.get(str(aid)) or table.get(aid)
                if not p:
                    detail.append("%s id %s has no row here" % (slot, aid))
                    ok = False
                elif not ar.exists(str(p).replace("\\", "/")):
                    detail.append("%s -> %s is not installed here" % (slot, p))
                    ok = False
        for role in npcart.MOTION_ROLES:
            m = row.get(role)
            if m is None:
                continue
            if not ar.exists("c3/npc/%s.c3" % m):
                detail.append("%s %s is not installed here" % (role, m))
                ok = False
        out.append((typ, row.get("name", ""), row, live[typ], ok, detail))
    return out


def _guard_writable(root) -> None:
    low = str(Path(root).resolve()).replace("\\", "/").lower()
    for marker in READ_ONLY_MARKERS:
        if marker.replace("\\", "/") in low:
            raise SystemExit(
                "REFUSED: %s is under the read-only client corpus.\n"
                "An install that gets written to is re-keyed and stops being "
                "a control." % root)


def apply_repairs(root, breaks, *, dry_run: bool = False) -> list:
    """Append DETERMINED rows to their tables, after backing each one up."""
    _guard_writable(root)
    by_table: dict = {}
    for b in breaks:
        if b.status == DETERMINED and b.stage in (NO_OBJ_ROW, NO_TEX_ROW):
            by_table.setdefault(b.table, []).append(b)
    done = []
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for table, rows in sorted(by_table.items()):
        path = Path(root) / table
        if not path.is_file():
            print("  skip %s -- not on this install" % table)
            continue
        raw = path.read_bytes()
        # Keep every byte that is already there. These tables are latin-1 with
        # CRLF endings; appending decoded text and rewriting the file whole is
        # how a table gets silently re-encoded.
        tail = b"" if raw.endswith(b"\n") else b"\r\n"
        add = b"".join(("%s\r\n" % b.row).encode("latin-1") for b in rows)
        if dry_run:
            print("  would append %d row(s) to %s" % (len(rows), table))
        else:
            bak = path.with_suffix(path.suffix + ".%s.bak" % stamp)
            shutil.copy2(str(path), str(bak))
            with open(path, "ab") as fh:
                fh.write(tail + add)
            print("  %s  +%d row(s)   backup: %s"
                  % (table, len(rows), bak.name))
        done.extend(rows)
    return done


def _report(res, root, show_context: bool) -> None:
    print("root   : %s" % res["root"])
    print("tables : %d npc rows, %d simple blocks, %d obj rows, %d texture rows"
          % (res["npcs"], res["blocks"], res["objRows"], res["texRows"]))
    print("npcs   : %d resolve, %d do not"
          % (res["resolvedNpcs"], len(res["affectedNpcs"])))

    live = [b for b in res["breaks"] if not b.latent]
    latent = [b for b in res["breaks"] if b.latent]

    if not live:
        print("\nNo NPC is missing its art.")
    else:
        print("\n=== NPCs that cannot reach their art ===")
        for b in live:
            print("  type %-6s %-24s selector %-7s %-9s id %-10s %s"
                  % (b.npc_type, (b.name or "")[:24], b.selector, b.slot,
                     b.asset_id, b.stage))
            if b.candidate:
                print("        REPAIR   %s  ->  %s" % (b.table, b.row))
            else:
                print("        NO CANDIDATE -- no installed asset carries id "
                      "%s under any shape %s uses" % (b.asset_id, b.table))

        det = [b for b in live if b.status == DETERMINED]
        und = [b for b in live if b.status == UNDETERMINED]
        print("\n  %d break(s) repairable from installed assets, %d not"
              % (len(det), len(und)))

        # THE COUNT ABOVE IS NOT THE ANSWER THE OWNER WANTS -- see `scan`.
        # An NPC with one determined and one undetermined break contributes
        # to the repairable tally and stays invisible in game.
        print("\n  Applying every repair above would:")
        if res["wouldRestore"]:
            for t, n in res["wouldRestore"]:
                print("     RESTORE  type %-6s %s" % (t, n))
        if res["wouldRemainBroken"]:
            for (t, n), blocking in res["wouldRemainBroken"]:
                why = ", ".join("%s %s" % (s, st) for s, st in blocking)
                print("     NOT FIX  type %-6s %-22s still blocked by %s"
                      % (t, n, why))
        if not res["wouldRestore"]:
            print("     -- restore nothing. Every affected NPC has at least "
                  "one break\n        that no installed asset can repair.")
        if und and show_context:
            print("\n  The undetermined slots are mesh slots. Blocks that wear "
                  "a shared mesh\n  under their own skin -- the shape an "
                  "alt-skin block has when it is\n  authored correctly -- look "
                  "like this on this install:")
            for sel, pid, tid, pm in alt_skin_context(root):
                print("     [ObjIDType%-6s] Part0=%-10s Texture0=%-10s  %s"
                      % (sel, pid, tid, pm))
            print("  Which mesh each broken block SHOULD wear is a content "
                  "decision, not a\n  lookup, so nothing above is proposed as "
                  "a repair.")

    if latent:
        print("\n=== LATENT: blocks no npc.json row selects ===")
        for b in latent:
            print("  selector %-7s %-9s id %-10s %s%s"
                  % (b.selector, b.slot, b.asset_id, b.stage,
                     "   REPAIR %s" % b.row if b.candidate else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Check the NPC art chain and propose repairs from "
                    "installed assets.")
    ap.add_argument("--root", help="client install root "
                                   "(default: the configured root)")
    ap.add_argument("--apply", action="store_true",
                    help="append DETERMINED repair rows, after a backup")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --apply, say what would be written")
    ap.add_argument("--json", metavar="PATH", help="write the findings as JSON")
    ap.add_argument("--no-latent", action="store_true",
                    help="skip blocks no npc.json row selects")
    ap.add_argument("--no-context", action="store_true",
                    help="skip the alt-skin context block")
    ap.add_argument("--baseline", metavar="DIR",
                    help="a known-good install; report npc.json rows whose "
                         "baseline targets still resolve here")
    args = ap.parse_args(argv)

    root = args.root
    if not root:
        import coroot
        found = coroot.find_root() if hasattr(coroot, "find_root") else None
        root = getattr(found, "path", found) or coassets.DEFAULT_ROOT
    if not Path(root).is_dir():
        print("not a directory: %s" % root, file=sys.stderr)
        return 2

    res = scan(root, include_latent=not args.no_latent)
    _report(res, root, show_context=not args.no_context)

    if args.baseline:
        if not Path(args.baseline).is_dir():
            print("baseline is not a directory: %s" % args.baseline,
                  file=sys.stderr)
            return 2
        rev = baseline_reverts(root, args.baseline)
        print("\n=== npc.json rows that differ from %s ==="
              % Path(args.baseline).name)
        if not rev:
            print("  none -- every row matches the baseline.")
        for typ, name, brow, lrow, ok, detail in rev:
            fields = {k: (brow.get(k), lrow.get(k))
                      for k in set(brow) | set(lrow)
                      if brow.get(k) != lrow.get(k)}
            print("  type %-6s %-18s %s"
                  % (typ, (name or "")[:18],
                     "REVERT VERIFIED" if ok else "REVERT NOT SAFE"))
            for k, (b, l) in sorted(fields.items()):
                print("        %-16s %s  ->  %s   (live has %s)" % (k, l, b, l))
            for d in detail:
                print("        blocked: %s" % d)
            if ok:
                print("        restore: %s" % json.dumps(brow,
                                                         separators=(", ", ": ")))
        good = [r for r in rev if r[4]]
        if good:
            print("\n  %d row(s) can be restored, every target verified "
                  "present on THIS install." % len(good))
            print("  Not written: npc.json is the owner's live table and a "
                  "revert is a content\n  decision. The rows above are exact.")

    if args.json:
        payload = dict(res)
        payload["breaks"] = [b.as_dict() for b in res["breaks"]]
        Path(args.json).write_text(json.dumps(payload, indent=2), "utf-8")
        print("\nwrote %s" % args.json)

    if args.apply:
        print("\n=== applying DETERMINED repairs ===")
        done = apply_repairs(root, [b for b in res["breaks"] if not b.latent],
                             dry_run=args.dry_run)
        print("  %d row(s) %s" % (len(done),
                                  "planned" if args.dry_run else "written"))
        if not args.dry_run and done:
            after = scan(root, include_latent=False)
            print("  re-scan: %d npcs resolve, %d do not"
                  % (after["resolvedNpcs"], len(after["affectedNpcs"])))

    return 1 if [b for b in res["breaks"] if not b.latent] else 0


if __name__ == "__main__":
    sys.exit(main())
