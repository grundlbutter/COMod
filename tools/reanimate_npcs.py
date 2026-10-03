#!/usr/bin/env python3
r"""reanimate_npcs.py -- the donor clip on EVERY humanoid monster and NPC
idle, written where the client reads them, with a catalog that says what
changed in each file.

    py -3 tools/reanimate_npcs.py --root <snapshot> --gltf <scene.gltf> \
        --out <export dir>/rig/gangnam-npcs-2026-10-03b --frames 302 --tail loop --ground smooth
    py -3 tools/reanimate_npcs.py ... --only monster-900,monster-126,npc-9992720
    py -3 tools/reanimate_npcs.py ... --dry-run          # the plan, nothing written

WHAT IT ITERATES
----------------
Every monster DIRECTORY that ships a clip (`rigtarget.shipping_monster_dirs`:
59 on the CCO snapshot, each resolved as a directory --
`rigtarget.resolve_monster_dir` -- because a directory is not a shape: shape
125's rows point at 120/ and 125/) and every NPC geometry family with a
shipped standby (`rigtarget.npc_families`: 24, keyed by the FULL geometry
path). Per family: the rig is solved or loaded from
``out/indexes/<base-id>/rig/<family>.json`` (`rigtarget.load_or_solve`, which
refuses a cached rig that is another body's), labelled (`core/bonelabel`),
put through the HUMANOID GATE (`c3retarget.derive_tables` ->
`humanoid_verdict` on the labels, then `anatomy_gate` on the geometry), and
the donor is retargeted onto its idle clip(s) with the derived tables --
``<dir>/100.c3`` for a monster, ``c3/npc/<standby id>.c3`` for every standby
file the family's npc.json rows name and the install ships -- at ``--frames``
keys, the tail looped, GROUNDED (``--ground smooth``: the feet never sit
below the donor's clearance; `c3retarget.GROUNDS` has the measurements).
Each output lands at the SAME relative path under ``--out``, so the folder
installs with comod as it stands.

WHICH FILE IS AN NPC'S IDLE
---------------------------
An npc.json row's standby id is resolved through `ini/3dmotion.ini` FIRST
and the ``c3/npc/<id>.c3`` stem second (`rigtarget.resolve_motion_id`):
164 of the CCO snapshot's 437 rows have no stem file and every one of
their ids has a table row naming a shipped file -- 96 the geometry
container itself (TaoistMoon plays ``c3/npc/9990111100.c3``, the Sage's),
35 a monster directory's ``100.c3`` (TerminalGuard and Soldier play the
Guard's). An NPC family whose idle is a file ANOTHER family writes is
FOLDED into that family (`families`: the monster directory owns
``c3/monster/<dir>/``, and of two NPC families naming one file the one
whose own directory holds it): its rows become aliases of that file
(``npcRows``), and the row's ``merged`` lists what was folded. One file,
one writer, one skeleton. The table route is TABLE EVIDENCE -- not
live-verified -- and every such file says so (``idleHow``).

THE ROUTES, IN ORDER (per family; the catalog row's ``route`` names the one taken)
---------------------------------------------------------------------------------
1. ``own rig`` -- solved from the family's own clips (`rigtarget.load_or_solve`),
   labelled, gated (`c3retarget.derive_tables`: `humanoid_verdict` on the
   labels, `anatomy_gate` on the geometry).
2. ``borrowed: <lender family>`` -- when the own rig is refused and the
   family is in `rigtarget.LENDERS` (two on the CCO snapshot: Maud/Pedlar
   wear the Pharmacist's rig, Bruce/Evan/Mike/Shelby/Watson/William the
   Boxer's). The lender's rig is taken VERBATIM and RE-MEASURED on the
   borrower's own clips at build time (`rigtarget.borrow_lender`: every
   articulated skinned edge must stay within 0.5% of the borrower's height;
   a wrong-skeleton lender reads 16% and up) and then gated like an own rig.
   Never taken on the table's word.
3. ``borrowed: player shape N`` -- a PLAYER-BODIED NPC (84 bones on a player
   shape's own joints: the Sage's file, 18 names; WarriorGod's, 12; shape 2's
   TrojanStar, 6, and ArcherHerald, 2) runs the player route
   (`rigtarget.player_body` -- the test and its cuts; `c3retarget.borrowed_route`)
   on the player's rig and tables, every extra bone it skins hosted
   (``followerHosts``).
4. ``skin tree`` -- when all three refuse (2026-10-03, pass 3b): the family's
   OWN skeleton read off its DRAWN MESH's face adjacency (two bones are
   adjacent when a face, a weld or a blended vertex joins vertices they
   skin), every seam CONFIRMED, left UNCONFIRMED or VETOED by the family's
   own clips with the shipped solver's per-clip vote (`rigtarget.skin_tree`),
   labelled by GEOMETRY (`c3retarget.geo_labels`: the pelvis is the hub with
   a floor-reaching chain a side, the chest the hub with the widest mirrored
   pair, an arm whose first link sits over 0.30 of its reach has no clavicle
   bone and its shoulder label is optional) and put through the SAME gate
   (`c3retarget.skin_tree_route`), plus the lender route's separation
   re-check on the family's own clips and a refusal when a mapped bone hangs
   on a proximity bridge. The catalog row says ``skin tree`` and carries
   ``skinTree`` -- the seam counts and, per mapped bone whose joint no clip
   ever moved, the seam centroid it sits at (``unconfirmedMapped``): those
   are the joints only an eye can judge. ``--skin-tree npc`` (default) tries
   it on NPC families only; ``all`` on monster directories too (the
   rig-library analysis measured every monster beyond the built ones over
   tolerance); ``off`` is the pass-3a builder. The two-weight skin tree
   (`core/bonetree` + `core/boneplace`) is still NOT a route: on 102 skinned
   bodies it admits 0 families within tolerance (median worst-edge
   separation 41% of height) -- 11 of 23 NPC body meshes carry no blended
   vertex at all, which is why the FACES are read instead.

A family every route refuses is written to ``refused.json`` with EVERY
route's reason, in order.

WHAT IT REFUSES, OUT LOUD
-------------------------
A family whose labels miss a core label the mapping needs or carry a
duplicate (a tail labelled as a third leg, extra `head_N` chains), or whose
geometry fails the anatomy gate (a "thigh" more sideways than down, a
skinned bone the rig leaves carried, a pelvis without two leg chains --
each with its measurement), is written to ``refused.json`` with the reason
and its labels. So is a family that could not be RESOLVED (the resolver's
own message is the reason), one whose rig cannot be solved or whose cached
rig is another body's, and one whose idle clip the retarget refuses (a body
track that is not the family's bone count). "No idle clip" is said only of
a family that has none, with the reason the tables give. Nothing is skipped
silently: the catalog plus the refusals account for every family the tables
declare.

THE CATALOG
-----------
``catalog.json`` has one row per written file: family, the names the tables
give it (npc.json names; monster.json rows whose TYPE equals a served shape
-- a CANDIDATE, live-verified for 900/126/111 only), relative path, the
ROUTE taken (``route``, ``lender``, ``transform``, ``residuals`` -- the
lender re-check's numbers; ``playerBody`` -- the player test's), the
humanoid verdict, the labels used and the Mixamo-name -> bone map, sha256
and byte length before and after, frames before and after, encoding before
and after (a ZKEY source comes back RAW: `effects.serialize_moti` cannot
write a quaternion track from matrices bit-exact; KKEY and XKEY round-trip
and keep their encoding), the ``--ground`` offset applied, sockets
regenerated and carried, ``meshContainer`` (the file carries a PHY chunk:
the drawn mesh rides in it, untouched), the npc.json rows and names SERVED
by the file (``npcRows``, ``npcNames``, ``npcRowCount`` -- its own family's
and every family folded into it), the ``3dmotion.ini`` rows naming the
path (``motionRows``), how the idle was resolved (``idleHow``), and
``engineVerified`` -- True only for a RAW -> RAW body, the one arm the
owner's Guard 900 kill test proved (2026-10-01). Everything else the
install plays is an unverified engine acceptance and the catalog says so
per file. ``README.md`` is generated from the catalog on every run.

IDEMPOTENT. The retarget is deterministic (same inputs, same bytes); a rerun
rewrites identical files and regenerates the catalog. ``--resume`` skips a
file whose catalog row already matches the source sha and whose output is
on disk with the recorded sha.

It never writes into an install, and it never launches anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bonerig                                               # noqa: E402
import bonetree                                              # noqa: E402
import c3phy                                                 # noqa: E402
import c3retarget as R                                       # noqa: E402
import coassets                                              # noqa: E402
import coroot                                                # noqa: E402
import gltfread                                              # noqa: E402
import rigtarget                                             # noqa: E402

#: The run name under `coroot.export_dir()/rig/` (the configured export
#: base; the same place `c3retarget` and `c3rig apply` write), never a
#: drive-rooted literal. Bumped with the rules: the 2026-10-03 set was
#: built before the sole rule, the hop cap and the player's garment on the
#: player route (section 13 of the doc), and a default run must not
#: overwrite the set the geometry pass measured.
DEFAULT_RUN = "gangnam-npcs-2026-10-03b"
DONOR_ENV = "C3RETARGET_DONOR"


def default_out() -> Path:
    return Path(coroot.export_dir()) / "rig" / DEFAULT_RUN


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _default_donor():
    import os                                                # noqa: PLC0415
    env = os.environ.get(DONOR_ENV)
    if env:
        return env
    try:
        return str(coroot.assets_dir().joinpath("Animations", "gangnam_style_converted",
                                                "scene.gltf"))
    except Exception:                                        # noqa: BLE001
        return None


def _fold(owner, t, path) -> None:
    """Fold NPC family `t`'s claim on idle `path` into `owner`: the rows
    become aliases of the owner's file, the names and types are added, the
    resolution record travels, and `owner.merged` says what came from
    where. Nothing of `t` is built on its own."""
    rows = list(t.aliases.get(path, []))
    owner.aliases.setdefault(path, [])
    owner.aliases[path] = sorted(set(owner.aliases[path]) | set(rows))
    if path not in owner.idle:
        owner.idle.append(path)
        owner.idle.sort()
        owner.notes.append("%s is written for the folded family %s: it lies under %s and the "
                           "skeleton is the directory's" % (path, t.family, owner.prefix))
    owner.names = sorted(set(owner.names) | set(t.names))
    owner.members = list(owner.members) + [m for m in t.members if m not in owner.members]
    if path in t.idle_how:
        owner.idle_how[path] = dict(t.idle_how[path])
    owner.merged.append({"family": t.family, "geometry": t.drawn_mesh, "idle": path,
                         "names": list(t.names), "members": list(t.members), "rows": rows,
                         "idleHow": dict(t.idle_how.get(path, {}))})
    owner.notes.append("npc family %s (%s; %d row(s): %s) names %s as its standby through "
                       "%s; folded into this family -- one file, one writer"
                       % (t.family, t.drawn_mesh, len(rows), ", ".join(t.names[:4]), path,
                          t.idle_how.get(path, {}).get("how", "?")))


def merge_shared_idles(targets: list) -> tuple:
    """``(targets, merges)``: one writer per idle file. An NPC family whose
    idle lies under a monster directory is folded into that directory's
    family; of two NPC families naming one file, the owner is the one whose
    own directory holds it (else the first by key). `merges` lists
    ``(folded family, owner family, path)``."""
    monsters = [t for t in targets if t.kind == rigtarget.MONSTER and not t.error]
    by_family = {t.family: t for t in targets}
    merges = []
    folded: set = set()
    for t in sorted((t for t in targets if t.kind == rigtarget.NPC and not t.error),
                    key=lambda t: t.family):
        for p in list(t.idle):
            owner = next((m for m in monsters if p.startswith(m.prefix)), None)
            if owner is None:
                continue
            _fold(owner, t, p)
            merges.append((t.family, owner.family, p))
            t.idle.remove(p)
            t.aliases.pop(p, None)
            t.idle_how.pop(p, None)
        if not t.idle:
            folded.add(t.family)
    claims: dict = {}
    for t in targets:
        if t.kind == rigtarget.NPC and t.family not in folded and not t.error:
            for p in t.idle:
                claims.setdefault(p, []).append(t)
    for p, ts in sorted(claims.items()):
        if len(ts) < 2:
            continue
        own = [t for t in ts if p.startswith(t.prefix)]
        owner = own[0] if own else sorted(ts, key=lambda t: t.family)[0]
        for t in ts:
            if t is owner:
                continue
            _fold(owner, t, p)
            merges.append((t.family, owner.family, p))
            t.idle.remove(p)
            t.aliases.pop(p, None)
            t.idle_how.pop(p, None)
            if not t.idle:
                folded.add(t.family)
    return [t for t in targets if t.family not in folded], merges


def families(root, ar, kinds=("monster", "npc"), merge=True) -> list:
    """Every `RigTarget` this build iterates: the shipping monster
    directories, then the NPC geometry families with a shipped standby --
    with shared idle files folded into one writer (`merge_shared_idles`;
    the list's ``.merges`` attribute is unavailable, so `families_merged`
    returns both)."""
    return families_merged(root, ar, kinds, merge)[0]


def families_merged(root, ar, kinds=("monster", "npc"), merge=True) -> tuple:
    """``(targets, merges)`` -- see `families`."""
    out = []
    if "monster" in kinds:
        rows = rigtarget.motion_rows(root)
        for d in rigtarget.shipping_monster_dirs(root, ar):
            name = d.rsplit("/", 1)[1]
            try:
                # the DIRECTORY, never "the shape of that number": shape
                # 125's rows point at two directories and `resolve_monster`
                # answers which one the SHAPE stands in (120)
                out.append(rigtarget.resolve_monster_dir(root, d, assets=ar, rows=rows))
            except rigtarget.TargetError as e:
                t = rigtarget.RigTarget(kind=rigtarget.MONSTER, id=name,
                                        family="monster-%s" % name, paths=[], prefix=d + "/")
                t.error = str(e)
                t.notes.append(str(e))
                out.append(t)
    if "npc" in kinds:
        fams = rigtarget.npc_families(root, ar)
        for g in sorted(fams):
            if not g:
                continue
            try:
                t = rigtarget.resolve_npc(root, g, assets=ar, families=fams)
            except rigtarget.TargetError as e:
                key = rigtarget.npc_key(g)
                t = rigtarget.RigTarget(kind=rigtarget.NPC, id=key, family="npc-%s" % key,
                                        paths=[], drawn_mesh=g)
                t.error = str(e)
                t.notes.append(str(e))
            if not t.idle and not t.error:
                t.notes.append("no shipped standby file; nothing to retarget")
            out.append(t)
    merges = []
    if merge:
        out, merges = merge_shared_idles(out)
    return out, merges


def _motion_rows_naming(rows: dict, path: str) -> list:
    """The `ini/3dmotion.ini` keys whose path is `path`."""
    p = str(path).replace("\\", "/").lower()
    return sorted(k for k, v in rows.items() if v == p)


def _try_own(target, root, ar, a, fam, log) -> tuple:
    """Route 1. ``(chosen or None, attempt)``; a `RigCacheMismatch` is
    re-raised (two families on one cache name is the finding)."""
    warn: list = []
    t0 = time.time()
    try:
        rig, rig_file, how = rigtarget.load_or_solve(
            target, root, max_frames=a.max_frames, assets=ar, warnings=warn)
    except rigtarget.RigCacheMismatch:
        raise
    except Exception as e:                                   # noqa: BLE001
        return None, {"route": "own rig", "ok": False,
                      "why": "rig could not be solved: %s: %s" % (e.__class__.__name__, e)}
    fam["rig"] = _rig_row(rig, rig_file, how, time.time() - t0)
    data0 = ar.read(target.idle[0])
    try:
        mesh, mesh_how = R.reference_mesh_for(ar, target, data0, None)
    except R.RetargetError as e:
        return None, {"route": "own rig", "ok": False, "why": str(e)}
    if mesh is None:
        return None, {"route": "own rig", "ok": False, "why": mesh_how}
    tables, verdict, labels = R.derive_tables(rig, garment=None, mesh=mesh)
    fam["humanoid"] = verdict
    fam["labels"] = {str(b): l for b, l in sorted(labels.items())}
    fam["referenceMesh"] = mesh_how
    if not verdict["ok"]:
        return None, {"route": "own rig", "ok": False, "why": verdict["reason"],
                      "tables": tables.describe()}
    return {"route": "own rig", "rig": rig, "rigFile": rig_file, "how": how, "mesh": mesh,
            "meshHow": mesh_how, "garment": None, "drawn": None, "follow": None, "tables": tables,
            "labels": labels, "verdict": verdict, "lender": None,
            "transform": None, "residuals": None}, {"route": "own rig", "ok": True,
                                                    "why": verdict["reason"]}


def _try_lender(target, root, ar, a, fam, log) -> tuple:
    """Route 2. The lender table, re-measured (`rigtarget.borrow_lender`),
    then the same gate an own rig passes."""
    test = rigtarget.borrow_lender(target, root, assets=ar, max_frames=a.max_frames)
    rec = {k: v for k, v in test.items() if k != "rig"}
    if rec.get("residuals"):
        rec["residuals"] = {k: v for k, v in rec["residuals"].items() if k != "edges"}
    fam["lenderTest"] = rec
    if not test.get("borrow"):
        return None, {"route": "borrowed: lender", "ok": False, "why": test["why"]}
    rig = test["rig"]
    data0 = ar.read(target.idle[0])
    try:
        mesh, mesh_how = R.reference_mesh_for(ar, target, data0, None)
    except R.RetargetError as e:
        return None, {"route": "borrowed: %s" % test["lender"], "ok": False, "why": str(e)}
    if mesh is None:
        return None, {"route": "borrowed: %s" % test["lender"], "ok": False, "why": mesh_how}
    tables, verdict, labels = R.derive_tables(rig, garment=None, mesh=mesh)
    fam["humanoid"] = verdict
    fam["labels"] = {str(b): l for b, l in sorted(labels.items())}
    fam["referenceMesh"] = mesh_how
    route = "borrowed: %s" % test["lender"]
    if not verdict["ok"]:
        return None, {"route": route, "ok": False,
                      "why": "%s; then the gate: %s" % (test["why"], verdict["reason"]),
                      "tables": tables.describe()}
    fam["rig"] = _rig_row(rig, test.get("lenderRig"), "borrowed verbatim from %s (%s)"
                          % (test["lender"], test.get("lenderRigHow")), 0.0)
    return {"route": route, "rig": rig, "rigFile": test.get("lenderRig"),
            "how": "borrowed", "mesh": mesh, "meshHow": mesh_how, "garment": None, "drawn": None,
            "follow": None, "tables": tables, "labels": labels, "verdict": verdict,
            "lender": test["lender"], "transform": test["transform"],
            "residuals": rec["residuals"]}, {"route": route, "ok": True, "why": test["why"]}


def _try_player(target, root, ar, a, fam, log) -> tuple:
    """Route 3. The player test and the player route."""
    test = rigtarget.player_body(target, root, assets=ar, max_frames=a.max_frames)
    fam["playerBody"] = {k: v for k, v in test.items() if k != "rig"}
    if not test.get("borrow"):
        return None, {"route": "borrowed: player", "ok": False, "why": test["why"]}
    route = "borrowed: player shape %s" % test["borrow"]
    try:
        b = R.borrowed_route(ar, target, root, max_frames=a.max_frames, test=test)
    except R.RetargetError as e:
        return None, {"route": route, "ok": False, "why": str(e)}
    if b["unresolved"]:
        return None, {"route": route, "ok": False,
                      "why": ("%s, but skinned bone(s) %s of %s are outside the player rig "
                              "and no host could be derived for them"
                              % (b["why"], b["unresolved"], target.drawn_mesh)),
                      "followerHosts": b["followers"]}
    fam["rig"] = _rig_row(b["rig"], b["rigFile"], b["why"], 0.0)
    fam["humanoid"] = {"ok": True, "reason": b["why"],
                       "borrowed": "player shape %s" % b["shape"]}
    fam["labels"] = {}
    fam["referenceMesh"] = b["meshHow"]
    fam["garment"] = b["garmentHow"]
    fam["garmentPath"] = b.get("garmentPath")
    fam["drawnMeshHow"] = b.get("drawnHow")
    fam["followerHosts"] = b["followers"]
    return {"route": route, "rig": b["rig"], "rigFile": b["rigFile"], "how": "borrowed",
            "mesh": b["mesh"], "meshHow": b["meshHow"], "garment": b["garment"],
            "drawn": b.get("drawn"),
            "follow": b["follow"], "tables": None, "labels": {}, "verdict": fam["humanoid"],
            "lender": "player shape %s" % b["shape"],
            "transform": {"scale": 1.0, "translate": [0.0, 0.0, 0.0], "how": "verbatim"},
            "residuals": {"jointResidualMean": (test.get("candidates", {}).get(str(b["shape"]), {})
                                                .get("jointResidualMean")),
                          "jointResidualMax": (test.get("candidates", {}).get(str(b["shape"]), {})
                                               .get("jointResidualMax")),
                          "coverage": (test.get("candidates", {}).get(str(b["shape"]), {})
                                       .get("coverage"))}}, {"route": route, "ok": True,
                                                             "why": b["why"]}


def _try_skin_tree(target, root, ar, a, fam, log) -> tuple:
    """Route 4. The drawn mesh's face adjacency confirmed by the family's
    own clips, geometric labels, the same gate (`c3retarget.skin_tree_route`)."""
    t0 = time.time()
    try:
        b = R.skin_tree_route(ar, target, root, stitched_tol=getattr(a, "stitched_tol", None))
    except R.RetargetError as e:
        return None, {"route": "skin tree", "ok": False, "why": str(e)}
    test = b.get("test") or {}
    rec = {k: v for k, v in test.items()
           if k not in ("rig", "mesh", "seams", "edges", "edgeHow", "residuals", "skinned")}
    if test.get("residuals"):
        rec["residuals"] = {k: v for k, v in test["residuals"].items() if k != "edges"}
    fam["skinTreeTest"] = rec
    if b.get("labels"):
        # A row's `labels` and `humanoid` describe ONE route. The skin tree
        # replaces both or neither: a verdict with no labels (the geometric
        # labelling found no legs) would otherwise sit on top of the own-rig
        # route's labels and contradict them (npc-479_1, pass-3 audit).
        fam["labels"] = {str(k): l for k, l in sorted(b["labels"].items())}
        if b.get("verdict") is not None:
            fam["humanoid"] = b["verdict"]
    elif b.get("verdict") is not None:
        fam["skinTreeHumanoid"] = b["verdict"]
    if b.get("geo") is not None:
        fam["skinTreeLabels"] = {"geo": b["geo"], "notes": b["notes"], "optional": b["optional"]}
    if not b["ok"]:
        att = {"route": "skin tree", "ok": False, "why": b["why"]}
        if b.get("tables") is not None:
            att["tables"] = b["tables"].describe()
        return None, att
    fam["rig"] = _rig_row(b["rig"], None, test.get("why") or "skin tree", time.time() - t0)
    fam["referenceMesh"] = b["meshHow"]
    fam["skinTree"] = {
        "confirmed": test.get("confirmed"), "unconfirmed": test.get("unconfirmed"),
        "vetoed": test.get("vetoed"), "vetoedEdges": test.get("vetoedEdges"),
        "bridged": test.get("bridged"), "cycleEdges": test.get("cycleEdges"),
        "componentsBeforeBridging": test.get("componentsBeforeBridging"),
        "seams": len(test.get("seams") or {}), "keys": test.get("keys"), "clips": test.get("clips"),
        "edges": test.get("edges"),
        "unconfirmedMapped": b["unconfirmedMapped"],
        "shouldersOptional": b["optional"], "clavicle": (b["geo"] or {}).get("clavicle"),
        "labelNotes": b["notes"], "cuts": test.get("cuts"),
    }
    return {"route": "skin tree", "rig": b["rig"], "rigFile": None, "how": "skin tree",
            "mesh": b["mesh"], "meshHow": b["meshHow"], "garment": None, "drawn": None,
            "follow": None, "tables": b["tables"], "labels": b["labels"],
            "verdict": b["verdict"], "lender": None, "transform": None,
            "residuals": rec.get("residuals")}, {"route": "skin tree", "ok": True,
                                                 "why": b["why"]}


#: ``--skin-tree``: which families route 4 may try after the three others
#: refuse. ``npc`` (default): NPC geometry families; ``all``: monster
#: directories too; ``off``: never (the pass-3a builder, byte for byte).
SKIN_TREE_MODES = ("npc", "all", "off")


def hop_cap_of(value):
    """``--hop-cap``: a float in c3 units, or None for ``off``/``none``/``""``
    (and for None). 0 is a value: the frame's own gap (`c3retarget.ground_cap`)."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("off", "none", ""):
            return None
        try:
            value = float(s)
        except ValueError:
            raise SystemExit("--hop-cap must be a number of c3 units or 'off', got %r" % value)
    cap = float(value)
    if cap < 0.0:
        raise SystemExit("--hop-cap must be >= 0 or 'off', got %r" % value)
    return cap


def _rig_row(rig, rig_file, how, seconds) -> dict:
    sf = rig.solved_from or {}
    return {"file": str(rig_file) if rig_file else None, "how": how, "seconds": round(seconds, 1),
            "boneCount": rig.bone_count, "bodyOrdinal": rig.body_ordinal,
            "skinned": len(rig.skinned), "joints": len(rig.joint_points),
            "referenceMesh": sf.get("referenceMesh"),
            "referenceMeshHow": sf.get("referenceMeshHow"),
            "accepted": sf.get("accepted"),
            "refused": [(r["path"], r["why"]) for r in sf.get("refused", [])
                        if isinstance(r, dict) and r.get("sha")]}


def build_family(target, root, ar, donor_gltf, a, log, motion_rows=None) -> tuple:
    """``(rows, refusal)``: the catalog rows written (or planned) for one
    family, or ONE refusal dict. Never raises for a data condition.

    The routes run in order -- own rig, the lender table, the player rig,
    the skin tree -- and the first one whose rig passes the gate builds the
    family; a family none takes is refused with every route's reason.
    ``--borrow off`` is own rigs only (no lender, no player, no skin tree);
    ``--skin-tree`` (`SKIN_TREE_MODES`) says which kinds route 4 may try."""
    fam = {"family": target.family, "kind": target.kind, "id": target.id,
           "names": target.names, "members": target.members,
           "idle": target.idle, "notes": target.notes,
           "drawnMesh": target.drawn_mesh, "foreign": target.foreign,
           "idleActions": target.idle_actions,
           "idleHow": {k: dict(v) for k, v in sorted(target.idle_how.items())},
           "merged": [dict(m) for m in target.merged]}
    if target.error:
        # the resolver's OWN message, verbatim -- never a stock "no idle
        # clip" for a family the resolver refused for another reason
        # (monster 125 and 130, 2026-10-02: "points at 2 directories")
        log("  %-16s REFUSED %s" % (target.family, target.error))
        return [], dict(fam, reason=target.error, labels={})
    if not target.idle or not target.paths:
        why = [n for n in target.notes if "idle" in n or "standby" in n or "shipped" in n]
        reason = "no idle clip to retarget: %s" % ("; ".join(why) or "the tables name none")
        log("  %-16s REFUSED %s" % (target.family, reason))
        return [], dict(fam, reason=reason, labels={})
    missing = [p for p in target.idle if not rigtarget._exists(ar, p)]
    if missing:
        reason = ("idle clip(s) %s are declared (%s) and not shipped by this install"
                  % (missing, "; ".join("%d row(s)" % len(target.aliases.get(p, []))
                                        for p in missing)))
        log("  %-16s REFUSED %s" % (target.family, reason))
        return [], dict(fam, reason=reason, labels={})
    # -- the routes, in order ------------------------------------------------
    borrow = getattr(a, "borrow", "auto")
    attempts: list = []
    chosen = None
    try:
        chosen, att = _try_own(target, root, ar, a, fam, log)
    except rigtarget.RigCacheMismatch as e:
        log("  %-16s REFUSED %s" % (target.family, e))
        return [], dict(fam, reason="RigCacheMismatch: %s" % e, labels={})
    attempts.append(att)
    if chosen is None and borrow in ("auto", "lender"):
        if target.kind == rigtarget.NPC:
            chosen, att = _try_lender(target, root, ar, a, fam, log)
        else:
            att = {"route": "borrowed: lender", "ok": False,
                   "why": "no lender: the lender table (rigtarget.LENDERS) lists NPC "
                          "borrowers only; a monster directory's skeleton is its own"}
        attempts.append(att)
    if chosen is None and borrow in ("auto", "player") and target.kind == rigtarget.NPC:
        chosen, att = _try_player(target, root, ar, a, fam, log)
        attempts.append(att)
    skin_mode = getattr(a, "skin_tree", "npc") or "npc"
    if skin_mode not in SKIN_TREE_MODES:
        raise ValueError("--skin-tree must be one of %s, got %r" % (SKIN_TREE_MODES, skin_mode))
    if chosen is None and borrow != "off" and skin_mode != "off":
        if skin_mode == "all" or target.kind == rigtarget.NPC:
            chosen, att = _try_skin_tree(target, root, ar, a, fam, log)
        else:
            att = {"route": "skin tree", "ok": False,
                   "why": "not tried on a monster directory (--skin-tree npc: the rig-library "
                          "analysis measured every monster beyond the built ones over the "
                          "separation tolerance, 2.9-12% of height); --skin-tree all tries it"}
        attempts.append(att)
    fam["routes"] = [{k: v for k, v in x.items() if k not in ("tables", "followerHosts")}
                     for x in attempts]
    if chosen is None:
        reason = " || ".join("%s: %s" % (x["route"], x["why"]) for x in attempts)
        log("  %-16s REFUSED %s" % (target.family, reason[:200]))
        extra = {}
        for x in attempts:
            if "tables" in x:
                extra["tables"] = x["tables"]
            if "followerHosts" in x:
                extra["followerHosts"] = x["followerHosts"]
        return [], dict(fam, reason=reason, **extra)
    fam["route"] = chosen["route"]
    fam["lender"] = chosen["lender"]
    fam["transform"] = chosen["transform"]
    fam["residuals"] = chosen["residuals"]
    rig, mesh, garment, follow = chosen["rig"], chosen["mesh"], chosen["garment"], chosen["follow"]
    drawn = chosen.get("drawn")
    tables = chosen["tables"]
    table_desc = tables.describe() if tables is not None else R.P84_TABLES.describe()
    donor = (R.Donor(donor_gltf, a.animation, mapping=tables.mapping) if tables is not None
             else R.Donor(donor_gltf, a.animation))
    ground = None if a.ground in (None, "", "off") else a.ground
    hop_cap = hop_cap_of(getattr(a, "hop_cap", R.GROUND_HOP_CAP)) if ground else None
    rows = []
    for path in target.idle:
        data = ar.read(path)
        src_chunks = list(c3phy.iter_chunks(data))
        npc_rows = sorted(target.aliases.get(path, [])) if target.kind == rigtarget.NPC else []
        for m in target.merged:
            if m.get("idle") == path:
                npc_rows = sorted(set(npc_rows) | set(m.get("rows", [])))
        npc_names = sorted({r.split(" ", 3)[3] for r in npc_rows if r.count(" ") >= 3})
        row = dict(fam, path=path, shaBefore=_sha(data), bytesBefore=len(data),
                   aliases=target.aliases.get(path, []),
                   npcRows=npc_rows, npcRowCount=len(npc_rows), npcNames=npc_names,
                   motionRows=_motion_rows_naming(motion_rows or {}, path),
                   idleHowFile=target.idle_how.get(path),
                   meshContainer=any(tag[:3] == b"PHY" for tag, _b in src_chunks),
                   chunkTags=[tag.decode("latin-1").strip() for tag, _b in src_chunks],
                   tables=table_desc)
        try:
            res = R.retarget_clip(data, rig, donor, fps_ms=a.fps_ms, tail=a.tail,
                                  root_motion=a.root_motion, rest_offset=a.rest_offset,
                                  attach=None, fingers=False, mesh=mesh, garment=garment,
                                  tables=tables, frames=a.frames, encoding=a.encoding,
                                  ground=ground, follow=follow, assets=ar,
                                  hop_cap=hop_cap, drawn=drawn)
        except R.RetargetError as e:
            row.update(written=False, reason=str(e))
            log("  %-16s %-28s REFUSED %s" % (target.family, path, e))
            rows.append(row)
            continue
        m = res.manifest
        row.update({
            "shaAfter": _sha(res.bytes), "bytesAfter": len(res.bytes),
            "frames": m["frames"], "framesBefore": m["frames"]["source"],
            "framesAfter": m["frames"]["written"],
            "encodingBefore": m["encoding"]["source"], "encodingAfter": m["encoding"]["written"],
            "encodingNote": m["encoding"]["note"], "encodingChanged": m["encoding"]["changed"],
            "engineVerified": m["frames"]["engineVerified"],
            "bodyOrdinal": m["bodyOrdinal"],
            "socketsRegenerated": m["sockets"], "socketsCarried": m["socketsCarried"],
            "counts": m["counts"], "seams": m["seams"],
            "connectivityMaxResidual": m["connectivity"]["maxResidual"],
            "restOffsetWorst": m["restOffset"]["worst"],
            "anatomy": {"front": m["anatomy"]["front"]["ok"],
                        "sideCarried": m["anatomy"]["side"].get("carried"),
                        "sideAgree": m["anatomy"]["side"]["agree"],
                        "motionXSignAgreement": m["anatomy"]["motion"]["xSignAgreement"]},
            "hover": m["hover"]["c3"],
            "hoverDonor": m["hover"]["donor"],
            # the DRAWN mesh's own feet above its own sole, when the drawn
            # mesh is neither the reference body nor the garment (the
            # player route); None otherwise -- `hover` is then the drawn
            # mesh's already
            "hoverDrawn": m["hover"].get("drawn"),
            # per mapped foot: the own-centroid proxy kept, or the sole rule
            # (`c3retarget.foot_rest_direction`) with its numbers
            "footRest": m.get("footRest"),
            # the `--ground` term applied (None: not grounded): mode, the
            # offset added to the root's z, the hover before it
            "ground": m["ground"],
            "groundOffset": (m["ground"] or {}).get("offset"),
            "hopCap": (m["ground"] or {}).get("hopCap"),
            "followers": m["followers"],
            "rerooted": m["rerooted"],
            "warnings": res.warnings,
            "written": False,
        })
        if not a.dry_run:
            dst = Path(a.out) / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(res.bytes)
            row["written"] = True
            row["out"] = str(dst)
        log("  %-16s %-28s %s %d -> %d frames, %s -> %s, %d -> %d bytes  [%s; %d npc row(s)]%s%s"
            % (target.family, path, "wrote" if row["written"] else "would write",
               row["framesBefore"], row["framesAfter"], row["encodingBefore"],
               row["encodingAfter"], row["bytesBefore"], row["bytesAfter"],
               fam["route"], len(npc_rows),
               "" if row["engineVerified"] else "  [engine acceptance UNVERIFIED]",
               "" if not m["socketsCarried"] else "  sockets carried %d" % len(m["socketsCarried"])))
        rows.append(row)
    return rows, None


def write_readme(out: Path, catalog: dict, refused: dict, head: str = "") -> Path:
    """``README.md`` from the catalog: what the set is, how it was built,
    the file table, the refusals by reason, and the install-ONE-file-first
    procedure in the owner's shell (PowerShell, absolute paths)."""
    files = [r for r in catalog["files"] if "shaAfter" in r]
    name = out.name
    L = []
    L.append("# %s" % name)
    L.append("")
    L.append("The Mixamo \"Gangnam Style\" donor clip retargeted onto the IDLE of every monster")
    L.append("and NPC family on the CCO snapshot that a route accepts (own rig, a same-skeleton")
    L.append("lender's rig, or the player's rig), written at %d frames (%g ms, ~%.1f s per"
             % (catalog["frames"], catalog["fpsMs"], catalog["frames"] * catalog["fpsMs"] / 1000.0))
    L.append("cycle), tail %s, grounded (`--ground %s`), at the SAME relative paths the client"
             % (catalog["tail"], catalog["ground"]))
    L.append("reads them from. %d files for %d families, serving %d npc.json rows (%d names)."
             % (len(files), len({r["family"] for r in files}),
                sum(r.get("npcRowCount", 0) for r in files),
                len({n for r in files for n in r.get("npcNames", [])})))
    L.append("Everything else is in `refused.json` with EVERY route's reason -- nothing was")
    L.append("skipped silently.")
    L.append("")
    L.append("Built %s from `C:/Claude/co-npcs`%s:" % (time.strftime("%Y-%m-%d"),
                                                      (" at commit `%s`" % head) if head else ""))
    L.append("")
    L.append("    %s" % catalog["commandLine"])
    L.append("")
    L.append("Sources: the VANILLA snapshot `%s` (loose first, then `c3.wdf`). Nothing was read"
             % catalog["root"])
    L.append("from or written to the live install. Donor: `%s`, sha256 `%s`."
             % (catalog["donor"]["path"], catalog["donor"]["sha256"]))
    L.append("")
    L.append("## The owner's kill-test premise (measured 2026-10-01 on the live client)")
    L.append("")
    L.append("The engine HONOURS a clip's `frameCount` and plays at 41 ms per frame: Guard 900's")
    L.append("standby (20 RAW frames) resampled to 302 frames played one idle cycle in ~12 s.")
    L.append("Only a RAW body written RAW at a new frameCount was proved (`engineVerified` true")
    L.append("in the catalog). Every other file carries `engineVerified: false`. The owner then")
    L.append("VERIFIED shapes 2 and 4 of the player route in the viewer (Group A, 2026-10-02).")
    L.append("")
    L.append("## Which file is an NPC's idle (table evidence, not live-verified)")
    L.append("")
    L.append("An npc.json row's standby id is resolved through `ini/3dmotion.ini` first and the")
    L.append("`c3/npc/<id>.c3` stem second. 164 of 437 rows have no stem file and every one of")
    L.append("their ids has a table row naming a shipped file -- most often the geometry")
    L.append("container itself (TaoistMoon plays `c3/npc/9990111100.c3`, the Sage's) or a")
    L.append("monster directory's `100.c3` (TerminalGuard and Soldier play the Guard's). Files")
    L.append("resolved that way carry `idleHow.how = \"ini/3dmotion.ini\"` with `stemAgrees`")
    L.append("false; that the client consults the table for an NPC standby is NOT live-verified.")
    L.append("Cheapest test: install the Sage's file and look at TaoistMoon (type 12).")
    L.append("")
    L.append("## The files")
    L.append("")
    L.append("| path | family | route | npc.json rows served (names) | frames | encoding | engine | hover c3 min/mean/max | ground offset min/mean/max | mesh container |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in files:
        names = r.get("npcNames") or []
        n_rows = r.get("npcRowCount", 0)
        if names:
            who = "%d (%s%s)" % (n_rows, ", ".join(names[:4]), " ..." if len(names) > 4 else "")
        else:
            # a monster directory no npc.json row names: monster.json's
            # candidate names (type == a served shape; live-verified for
            # 900/126/111 only), or none
            cand = r.get("names") or []
            who = ("0; monster.json candidate %s" % ", ".join(cand[:3])) if cand                 else "0; no monster.json name"
        hv = r.get("hover") or {}
        route = r.get("route", "?")
        st = r.get("skinTree")
        if st:
            route = "%s (%d unconfirmed edge(s), %d mapped)" % (
                route, st.get("unconfirmed") or 0, len(st.get("unconfirmedMapped") or []))
        L.append("| `%s` | %s | %s | %s | %d -> %d | %s -> %s | %s | %s / %s / %s | %s | %s |" % (
            r["path"], r["family"], route, who,
            r["framesBefore"], r["framesAfter"], r["encodingBefore"], r["encodingAfter"],
            "**verified**" if r.get("engineVerified") else "unverified",
            _fmt(hv.get("min")), _fmt(hv.get("mean")), _fmt(hv.get("max")),
            _fmt(r.get("groundOffset")), "PHY" if r.get("meshContainer") else "MOTI-only"))
    L.append("")
    L.append("Per-file sha256 before/after, byte lengths, the route's measurements (`residuals`,")
    L.append("`playerBody`, `lenderTest`), the labels, the Mixamo-name -> bone map, seams,")
    L.append("connectivity, rest offsets, root motion, the `--ground` offset, every npc.json row")
    L.append("and 3dmotion.ini row naming the file, and every warning are in `catalog.json`")
    L.append("(`files[]`). In every file the ONLY chunk that changed is the body MOTI; every PHY")
    L.append("and CAME chunk and the chunk order are byte-identical to the source.")
    L.append("")
    enc = catalog.get("encodingChanged") or []
    L.append("## Caveats")
    L.append("")
    L.append("- Encoding: RAW -> RAW is the proven arm. KKEY -> KKEY at a new frameCount is")
    L.append("  kept and unverified. %d file(s) CHANGE encoding (ZKEY -> RAW: a quaternion track"
             % len(enc))
    L.append("  cannot be written back bit-exact): %s." % (", ".join("`%s`" % p for p in enc) or "none"))
    L.append("- Mesh containers: a file marked PHY carries the drawn mesh (or a placeholder);")
    L.append("  the mesh bytes are untouched, but the file IS the one the client draws from.")
    L.append("- Side convention: +x is the character's LEFT, carried from the player rig; the")
    L.append("  owner decides by eye.")
    L.append("- A borrowed lender rig was re-measured on the borrower's own clips at build time")
    L.append("  (`residuals.worstOverHeight`, cut 0.5%); the lender's edges the borrower's clips")
    L.append("  never move are listed as `untestedEdges`.")
    L.append("- A player-bodied NPC rides the player's rig and tables; its extra bones (hair,")
    L.append("  cloak, toes) follow a host (`followerHosts`). The owner verified shapes 2 and 4")
    L.append("  in the viewer; shape 3 is the Sage's and is new here.")
    front = [r["path"] for r in files if r.get("anatomy", {}).get("front") is False]
    if front:
        L.append("- FRONT check (foot rest direction within 45 deg of the donor's) fails on %s:"
                 % ", ".join("`%s`" % p for p in front))
        L.append("  recorded, not refused -- judge the feet by eye.")
    sole = [(r["path"], [f for f in (r.get("footRest") or {}).values() if f.get("kept") is False])
            for r in files]
    sole = [(p, fs) for p, fs in sole if fs]
    if sole:
        L.append("- FEET BY THE SOLE on %d file(s): a toeless foot whose own-centroid direction sits"
                 % len(sole))
        L.append("  more than %g deg off the donor's foot pitch takes the donor's pitch on its"
                 % R.FOOT_PROXY_PITCH_TOL_DEG)
        L.append("  sole-tip heading instead (`footRest` per foot; c3retarget.FOOT_PROXY_PITCH_TOL_DEG")
        L.append("  has the measurements -- the 2026-10-03 set's toes sat 16-29 units up on these):")
        for p, fs in sole:
            L.append("  `%s`: %s" % (p, "; ".join(
                "%s proxy %+.1f deg off" % (f.get("name"), f.get("proxyPitchMinusDonorDeg", 0.0))
                for f in fs)))
    capped = [(r["path"], r["hopCap"]) for r in files
              if (r.get("hopCap") or {}).get("framesClamped")]
    if capped:
        L.append("- HOP CAP (`--hop-cap %s`): on %d file(s) the hover excess over the donor's"
                 % (_fmt(catalog.get("hopCap")), len(capped)))
        L.append("  clearance was clamped (`hopCap` per file: frames, excess before -> after); the")
        L.append("  price is a root step of the difference on those frames (c3retarget.GROUND_HOP_CAP).")
        for p, h in capped:
            L.append("  `%s`: %d frame(s), excess %.1f -> %.1f" % (
                p, h["framesClamped"], h["excessMaxBefore"], h["excessMaxAfter"]))
    player = [r for r in files if r.get("garmentPath")]
    if player:
        L.append("- The %d player-bodied file(s) take their foot direction from the PLAYER's garment"
                 % len(player))
        L.append("  for the shape (`garmentPath`), the body the owner-verified shape build wore; the")
        L.append("  NPC's own mesh rides the result and its feet are measured as `hoverDrawn`")
        L.append("  (its own sole). A floor-length robe rigid with the pelvis dips below the")
        L.append("  soles by the knee bend (the Sage: by design, as the player's robes do).")
    skin = [r for r in files if r.get("skinTree")]
    if skin:
        L.append("- SKIN TREE on %d file(s): the family's skeleton is its drawn mesh's face adjacency"
                 % len(skin))
        L.append("  confirmed by its own clips (`skinTree`: seams confirmed / unconfirmed / vetoed),")
        L.append("  labelled by geometry; where the arm has no clavicle bone the shoulder labels are")
        L.append("  optional and the donor's upper arm drives the first link. A MAPPED joint no clip")
        L.append("  ever moved sits at the mesh seam centroid (`skinTree.unconfirmedMapped`) and only")
        L.append("  an eye can judge it:")
        for r in skin:
            st = r["skinTree"]
            um = st.get("unconfirmedMapped") or []
            L.append("  `%s`: %d confirmed / %d unconfirmed / %d vetoed; unconfirmed mapped: %s" % (
                r["path"], st.get("confirmed") or 0, st.get("unconfirmed") or 0,
                st.get("vetoed") or 0,
                ", ".join("%s (bone %s, edge %s)" % (u.get("label"), u.get("bone"), u.get("edge"))
                          for u in um) or "none"))
    L.append("- Every file is GROUNDED (`groundOffset`: the vertical root term that keeps the feet")
    L.append("  at the donor's clearance), so a file also in an earlier set has different bytes")
    L.append("  (the Guard's 2026-10-02 file was ungrounded).")
    L.append("")
    fams = refused.get("families", [])
    L.append("## What was refused (%d families in `refused.json`)" % len(fams))
    L.append("")
    groups: dict = {}
    for r in fams:
        groups.setdefault(_reason_class(r.get("reason", "")), []).append(r)
    for k, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        L.append("- %d: %s -- %s%s" % (len(rs), k, ", ".join(r["family"] for r in rs[:8]),
                                      " ..." if len(rs) > 8 else ""))
    L.append("")
    L.append("Each row carries every route's reason in order (`routes[]`), the labels and the")
    L.append("partial tables where a rig was solved, and the names the tables give the family.")
    L.append("")
    L.append("## How to install ONE file first, and how to revert")
    L.append("")
    first = next((r for r in files if r.get("engineVerified")), files[0] if files else None)
    L.append("Start with %s, then one borrowed file. Run these in PowerShell from"
             % ("the engine-verified `%s`" % first["path"] if first else "any file"))
    L.append("`C:\\Claude\\co-client-re`; comod's stage tree is `<checkout>\\Installed\\stage` and")
    L.append("`install` takes the WHOLE stage, so `diff` must list exactly the file you mean.")
    L.append("")
    if first:
        rel = first["path"].replace("/", "\\")
        L.append("```powershell")
        L.append("cd C:\\Claude\\co-client-re")
        L.append("py -3 tools\\comod.py diff")
        L.append("#   -> must say nothing is staged; if it lists anything, move it out of Installed\\stage first")
        L.append("New-Item -ItemType Directory -Force Installed\\stage\\%s | Out-Null" % rel.rsplit("\\", 1)[0])
        L.append("Copy-Item %s\\%s Installed\\stage\\%s" % (str(out).replace("/", "\\"), rel, rel))
        L.append("py -3 tools\\comod.py diff")
        L.append("#   -> must list exactly %s" % first["path"])
        L.append("py -3 tools\\comod.py --root \"C:\\Program Files\\Classic Conquer 2.0\" install --dry-run --label \"%s %s\"" % (name, first["family"]))
        L.append("py -3 tools\\comod.py --root \"C:\\Program Files\\Classic Conquer 2.0\" install --yes --label \"%s %s\"" % (name, first["family"]))
        L.append("```")
        L.append("")
    L.append("Revert:")
    L.append("")
    L.append("```powershell")
    L.append("cd C:\\Claude\\co-client-re")
    L.append("py -3 tools\\comod.py --root \"C:\\Program Files\\Classic Conquer 2.0\" uninstall --list")
    L.append("#   -> read WHICH entry is the one you just made before --yes")
    L.append("py -3 tools\\comod.py --root \"C:\\Program Files\\Classic Conquer 2.0\" uninstall --last --dry-run")
    L.append("py -3 tools\\comod.py --root \"C:\\Program Files\\Classic Conquer 2.0\" uninstall --last --yes")
    L.append("```")
    L.append("")
    L.append("Deleting this export folder reverts nothing in the game; nothing here was written")
    L.append("to an install. Files in this folder: `c3/...` (the %d outputs), `catalog.json`,"
             % len(files))
    L.append("`refused.json` (`families[]` %d rows, `files[]` %d), this README."
             % (len(fams), len(refused.get("files", []))))
    L.append("")
    p = out / "README.md"
    text = "\n".join(L)
    assert not any(ord(c) < 32 and c not in "\n" for c in text), "control byte in README"
    # LF, explicitly: `write_text` would hand Windows a CRLF file
    p.write_bytes(text.encode("utf-8"))
    return p


def _fmt(x) -> str:
    """A number, or a `--ground` offset's min/mean/max dict, for the table."""
    if x is None:
        return "-"
    if isinstance(x, dict):
        return "%s / %s / %s" % (_fmt(x.get("min")), _fmt(x.get("mean")), _fmt(x.get("max")))
    return "%.2f" % x


def _reason_class(reason: str) -> str:
    r = reason or ""
    if "no idle clip" in r:
        return "no idle clip"
    if "ships none of them" in r:
        return "declared motion files not shipped"
    if "RigCacheMismatch" in r:
        return "cached rig is another body's"
    if "not a humanoid" in r or "anatomy" in r or "thigh" in r or "pelvis" in r:
        return ("not a humanoid by the gate (labels or geometry), and no route lends a rig "
                "or reads one off the mesh")
    if "could not be solved" in r or "no skinned" in r:
        return "no skinned body / rig unsolvable"
    return "other"


def _git_head() -> str:
    """The checkout's HEAD sha for the README, or ''."""
    import subprocess                                        # noqa: PLC0415
    try:
        return subprocess.run(["git", "-C", str(_HERE.parent), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              errors="backslashreplace",
                              timeout=20).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Retarget the donor onto every humanoid monster and NPC idle.")
    ap.add_argument("--root", default=None, help="the VANILLA snapshot (never the live install)")
    ap.add_argument("--gltf", default=_default_donor())
    ap.add_argument("--animation", type=int, default=0)
    ap.add_argument("--out", default=None,
                    help="output folder; default <export dir>/rig/%s" % DEFAULT_RUN)
    ap.add_argument("--frames", type=int, default=302)
    ap.add_argument("--fps-ms", type=float, default=R.DEFAULT_FPS_MS)
    ap.add_argument("--tail", choices=R.TAILS, default="loop")
    ap.add_argument("--root-motion", choices=R.ROOT_MOTIONS, default="vertical")
    ap.add_argument("--rest-offset", choices=R.REST_OFFSETS, default="direction")
    ap.add_argument("--encoding", choices=("keep", "raw"), default="keep")
    ap.add_argument("--ground", choices=("off",) + tuple(R.GROUNDS), default="smooth",
                    help="the vertical root term that keeps the feet at the donor's "
                         "clearance (c3retarget --ground; `GROUNDS` has the "
                         "measurements). smooth (default): never below it, no hip "
                         "pop; frame: exactly it; mean: one number; off: none "
                         "(monster 129 then sinks 6-23 units)")
    ap.add_argument("--hop-cap", default=str(R.GROUND_HOP_CAP), metavar="UNITS|off",
                    help="with --ground: the most a frame's hover may exceed the donor's "
                         "clearance, in c3 units (c3retarget.ground_cap; the frames over it "
                         "take a root step of the difference instead of a hop). Default %g "
                         "(c3retarget.GROUND_HOP_CAP has the measurements: monster 129 hopped "
                         "16.85 against the donor's 4.56); 'off' for the plain envelope"
                         % R.GROUND_HOP_CAP)
    ap.add_argument("--borrow", choices=("auto", "lender", "player", "off"), default="auto",
                    help="which borrowed routes may follow a refused own rig: auto "
                         "(the lender table, then the player rig), lender only, player "
                         "only, off (own rigs only: no lender, no player, no skin tree)")
    ap.add_argument("--skin-tree", choices=SKIN_TREE_MODES, default="npc",
                    help="route 4, after the three others refuse: the drawn mesh's face "
                         "adjacency confirmed by the family's own clips, geometric labels, "
                         "the same gate (c3retarget.skin_tree_route). npc (default): NPC "
                         "families only; all: monster directories too; off: the pass-3a "
                         "builder")
    ap.add_argument("--stitched-tol", type=float, default=None, metavar="FRACTION",
                    help="route 4 only: readmit a STITCHED seam the per-clip vote dropped when "
                         "its joint, solved over every clip, holds within this fraction of the "
                         "height (default: the seam tolerance, 0.005). Opt-in and recorded per "
                         "edge; the town shoulders drift 0.014-0.022 of height, and what that "
                         "stretches is measured in rigtarget's comment above JOINT_TIE_UNITS")
    ap.add_argument("--no-merge", action="store_true",
                    help="do not fold NPC families onto the monster directory (or NPC "
                         "family) that writes their idle file -- two writers per file; "
                         "for diagnosis only")
    ap.add_argument("--max-frames", type=int, default=bonerig.DEFAULT_MAX_FRAMES,
                    help="the solver's frame budget per family")
    ap.add_argument("--only", default=None,
                    help="comma-separated family keys (monster-900, npc-9992720) "
                         "or ids (900, 9992720, Pharmacist)")
    ap.add_argument("--kinds", default="monster,npc")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true",
                    help="skip files whose catalog row and output already match")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    root = str(a.root or coroot.default_root())
    if not a.gltf or not Path(a.gltf).is_file():
        print("no donor glTF: give --gltf or set %s" % DONOR_ENV)
        return 2
    ar = coassets.AssetRoot.bare(root)
    out = Path(a.out) if a.out else default_out()
    a.out = str(out)
    log = (lambda s: None) if a.quiet else print
    t_all = time.time()
    donor_bytes = Path(a.gltf).read_bytes()
    donor_gltf = gltfread.Gltf.load(Path(a.gltf))

    rows_3dmotion = rigtarget.motion_rows(root)
    targets, merges = families_merged(root, ar, tuple(k.strip() for k in a.kinds.split(",") if k.strip()),
                                      merge=not a.no_merge)
    declared = len(targets) + len({m[0] for m in merges})
    if a.only:
        keys = {k.strip() for k in a.only.split(",") if k.strip()}
        sel = []
        owner_of = {f: o for f, o, _p in merges}
        for t in targets:
            if t.family in keys or t.id in keys or (set(t.names) & keys)                     or any(owner_of.get(k) == t.family for k in keys):
                sel.append(t)
        missing = (keys - {t.family for t in sel} - {t.id for t in sel}
                   - {n for t in sel for n in t.names}
                   - {f for f, o in owner_of.items() if o in {t.family for t in sel}})
        if missing:
            # a key naming no iterated family: resolve it directly (an NPC name, say)
            for k in sorted(missing):
                try:
                    sel.append(rigtarget.resolve(root, npc=k, assets=ar)
                               if not k.isdigit() or len(k) > 3
                               else rigtarget.resolve(root, shape=k, assets=ar))
                except rigtarget.TargetError as e:
                    print("--only %s: %s" % (k, e))
        targets = sel
    if a.limit:
        targets = targets[:a.limit]
    log("%s: %d families (%s)%s%s" % (root, len(targets),
                                        ", ".join(sorted({t.kind for t in targets})),
                                        "  DRY RUN" if a.dry_run else "",
                                        ("; %d folded onto the family that writes their idle"
                                         % len({m[0] for m in merges})) if merges else ""))

    # an earlier catalog, for --resume and so a partial rerun keeps its rows
    old_rows: dict = {}
    cat_path = out / "catalog.json"
    if cat_path.is_file():
        try:
            for r in json.loads(cat_path.read_text("utf-8")).get("files", []):
                old_rows[r["path"]] = r
        except Exception:                                    # noqa: BLE001
            old_rows = {}

    rows: list = []
    refused: list = []
    skipped = 0
    for i, t in enumerate(targets):
        if a.resume and t.idle and all(
                p in old_rows and old_rows[p].get("written")
                and (out / p).is_file() and _sha((out / p).read_bytes()) == old_rows[p].get("shaAfter")
                and old_rows[p].get("shaBefore") == _sha(ar.read(p)) for p in t.idle):
            rows.extend(old_rows[p] for p in t.idle)
            skipped += 1
            log("  %-16s resumed (%d file(s) already match)" % (t.family, len(t.idle)))
            continue
        try:
            fam_rows, refusal = build_family(t, root, ar, donor_gltf, a, log,
                                             motion_rows=rows_3dmotion)
        except Exception as e:                               # noqa: BLE001
            refusal = {"family": t.family, "kind": t.kind, "id": t.id, "names": t.names,
                       "reason": "unexpected %s: %s" % (e.__class__.__name__, e),
                       "trace": traceback.format_exc()[-800:], "labels": {}}
            fam_rows = []
            log("  %-16s ERROR %s: %s" % (t.family, e.__class__.__name__, e))
        rows.extend(fam_rows)
        if refusal is not None:
            refused.append(refusal)
    written = [r for r in rows if r.get("written")]
    planned = [r for r in rows if "shaAfter" in r]
    enc_changed = [r for r in planned if r.get("encodingChanged")]
    unverified = [r for r in planned if not r.get("engineVerified")]
    file_refused = [r for r in rows if "reason" in r]
    summary = {
        "tool": "reanimate_npcs",
        "commandLine": " ".join(["py -3 tools/reanimate_npcs.py"] + list(argv if argv is not None else sys.argv[1:])),
        "root": root,
        "donor": {"path": str(a.gltf), "sha256": _sha(donor_bytes)},
        "out": str(out),
        "frames": a.frames, "fpsMs": a.fps_ms, "tail": a.tail,
        "rootMotion": a.root_motion, "restOffset": a.rest_offset, "encoding": a.encoding,
        "ground": a.ground, "hopCap": hop_cap_of(getattr(a, "hop_cap", R.GROUND_HOP_CAP)),
        "borrow": a.borrow, "skinTree": getattr(a, "skin_tree", "npc"),
        "dryRun": bool(a.dry_run),
        "families": len(targets), "familiesRefused": len(refused),
        "familiesDeclared": declared,
        "familiesMerged": [{"family": f, "into": o, "idle": p} for f, o, p in merges],
        "familiesResumed": skipped,
        "byRoute": {k: sorted({r["family"] for r in planned if r.get("route") == k})
                    for k in sorted({r.get("route") for r in planned if r.get("route")})},
        "npcRowsServed": sum(r.get("npcRowCount", 0) for r in planned),
        "npcNamesServed": sorted({n for r in planned for n in r.get("npcNames", [])}),
        "idleResolution": ("ini/3dmotion.ini row first, c3/npc/<id>.c3 stem second "
                           "(rigtarget.resolve_motion_id); the table route is TABLE EVIDENCE, "
                           "not live-verified -- see idleHow per file"),
        "routeOrder": ["own rig", "borrowed: <lender family> (rigtarget.LENDERS, re-measured)",
                       "borrowed: player shape N (rigtarget.player_body)",
                       "skin tree (rigtarget.skin_tree + c3retarget.geo_labels, the same gate; "
                       "--skin-tree %s)" % getattr(a, "skin_tree", "npc"),
                       "two-weight skin-only: not a route (admits 0 families, measured 2026-10-02)"],
        "files": len(planned), "filesWritten": len(written),
        "filesRefused": len(file_refused),
        "encodingChanged": [r["path"] for r in enc_changed],
        "engineUnverified": [r["path"] for r in unverified],
        "elapsedS": round(time.time() - t_all, 1),
        "sideConvention": ("+x is the character's LEFT, CARRIED from the player rig: 46 of "
                           "59 monster families have no weapon socket to key it on; the "
                           "owner decides by eye on the first install"),
        "engineVerifiedMeans": ("RAW -> RAW body at a new frameCount, the arm the Guard 900 "
                                "kill test proved 2026-10-01; anything else is an unverified "
                                "engine acceptance"),
        "revert": "delete this folder / comod uninstall; nothing was written to an install",
    }
    catalog = dict(summary, files=rows)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        cat_path.write_bytes(json.dumps(catalog, indent=1, sort_keys=True).encode("utf-8"))
        (out / "refused.json").write_bytes(
            json.dumps({"families": refused, "files": file_refused}, indent=1,
                       sort_keys=True).encode("utf-8"))
        try:
            write_readme(out, catalog, {"families": refused, "files": file_refused},
                         head=_git_head())
        except Exception as e:                               # noqa: BLE001
            print("README.md not written: %s: %s" % (e.__class__.__name__, e))
    print("%s %d file(s) for %d of %d families (%d declared, %d folded); %d npc.json row(s) "
          "served; %d families refused, %d files refused; %d encoding change(s), "
          "%d engine-unverified; %.0fs"
          % ("would write" if a.dry_run else "wrote", len(planned),
             len({r["family"] for r in planned}), len(targets), declared,
             len({m[0] for m in merges}), summary["npcRowsServed"], len(refused),
             len(file_refused), len(enc_changed), len(unverified), time.time() - t_all))
    for k, fs in summary["byRoute"].items():
        print("  route %-32s %d famil%s: %s" % (k, len(fs), "y" if len(fs) == 1 else "ies",
                                                ", ".join(fs)))
    for r in refused:
        print("  REFUSED %-16s %s" % (r["family"], r["reason"][:160]))
    if not a.dry_run:
        print("  -> %s (catalog.json, refused.json)" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
