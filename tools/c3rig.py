#!/usr/bin/env python3
r"""c3rig.py -- edit a body family's rig ONCE and apply it to every animation.

    py -3 tools/c3rig.py files  --shape 3
    py -3 tools/c3rig.py solve  --shape 3                 # writes the rig cache
    py -3 tools/c3rig.py verify --shape 3                 # identity must be a no-op
    py -3 tools/c3rig.py apply  --shape 3 --bone 9 --axis y --rotate-deg 10

`solve` writes `out/indexes/<base-id>/rig/p84-shape3.json` for the install
`--root` names (default: the configured one) -- the same path `verify`, `apply`
and `c3retarget` read for that root and shape. `--out` names the FILE instead.
Keyed per SHAPE since 2026-10-01 (`bonerig.rig_path`): shapes 2/3/4 share the
p84 space and used to overwrite one `p84.json`; the old name is still READ
through `bonerig.find_rig` when it holds the shape asked for.

    py -3 tools/c3rig.py solve --root <snapshot> --shape 900      # a MONSTER family
    py -3 tools/c3rig.py solve --root <snapshot> --npc Pharmacist # an NPC family

A 3-digit `--shape` is a monster shape and `--npc` an NPC family
(`tools/rigtarget.py`, 2026-10-01): the clips come from `ini/3dmotion.ini`'s
`c3/monster/<dir>/` rows or from `ini/npc.json` through `core/npcart`, the
reference body from the DRAWN mesh (`ini/3dobj.ini`) when no clip carries
one, and the rig is cached as `out/indexes/<base-id>/rig/<family>.json`
(`monster-900.json`, `npc-9992720.json`). `--mesh` overrides the body.

WHAT THIS IS FOR
----------------
A shape's animations are ~290 separate `.c3` files that share one skeleton by
CONVENTION and reference nothing. So "rotate the upper arm a little" has
meant opening 290 files, or accepting that the change applies to one clip and
the character now moves differently depending on what it is doing.

`tools/bonerig.py` recovers and records the shared hierarchy. This applies an
edit expressed against that hierarchy to every clip the family names, and
writes the result **outside the game tree** with a manifest saying exactly
what changed.

WHY THE EDIT IS THIRTY LINES AND NOT A MATRIX PIPELINE
-------------------------------------------------------
`c3anim.moti_to_view(motion, rest, parents=...)` already hands out each
bone's PARENT-RELATIVE basis, and `build_motion` already walks root-first and
carries a changed parent into every descendant's absolute matrix. So a local
delta is exactly "change these ten floats on this bone in every key" and the
existing, gated code does the rest. Nothing here re-implements the algebra;
`bonerig.apply_delta` edits the view and `c3anim` rebuilds.

THE REST POSE IS IDENTITY, DELIBERATELY
----------------------------------------
`B = L^-1 . M . L` applies `M` for ANY rest `L` -- the rest cancels. Blender
keeps a placeholder ladder so bones are selectable; a batch tool has no one
to select anything, and the measured re-bake error is an order of magnitude
lower without it (identity 8.8e-07 against the 4.0-spaced ladder's 1.7e-04,
because the ladder's translation multiplies relative error). So this uses
identity and says so, rather than inheriting a choice made for a UI.

WHAT IT WILL NOT DO
-------------------
* It never writes to the game install. Output goes where `--out` says, and
  the default is `coroot.export_dir()/rig/<run>`. Loose files shadow the
  archive, so installing IS copying the tree in -- and reverting is deleting
  it -- but that is the owner's action and not this tool's.
* It refuses a clip whose body track is not the family's, by name and with
  the reason; `bonerig.accept` decides and this only reports.
* It does not change an encoding. `build_motion` writes back whatever it
  read and has no parameter to choose otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bonerig                                               # noqa: E402
import c3anim                                                # noqa: E402
import c3phy                                                 # noqa: E402
import c3write                                               # noqa: E402
import coassets                                              # noqa: E402
import coroot                                                # noqa: E402
import effects                                               # noqa: E402
import rigtarget                                             # noqa: E402

MOTI_TAG = b"MOTI"


def identity_rest(bone_count: int) -> list:
    """One identity 4x4 per bone. See the module docstring: the rest cancels."""
    ident = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
             (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    return [ident] * int(bone_count)


def _moti_slots(chunks) -> list:
    """Indices into `chunks` of the MOTI chunks, in ordinal order."""
    return [i for i, (t, _b) in enumerate(chunks) if t == MOTI_TAG]


def _socket_view(motion, target_abs):
    """The 10-float view for a 1-bone track whose absolute matrix is given."""
    b = c3anim.c3_matrix_to_blender(target_abs)
    loc, quat, scale = c3anim.decompose(b)
    return list(loc) + list(quat) + list(scale)


def _flat16(m4) -> list:
    """The row-of-rows 4x4 back to the 16 C3 floats `_as4` read."""
    return [m4[0][0], m4[0][1], m4[0][2], 0.0,
            m4[1][0], m4[1][1], m4[1][2], 0.0,
            m4[2][0], m4[2][1], m4[2][2], 0.0,
            m4[3][0], m4[3][1], m4[3][2], 1.0]


def edit_clip(data: bytes, rig, bone: int, *, rotate=None, translate=None,
              scale=None) -> dict:
    """One clip, edited. Returns a report; `bytes` is None when nothing moved.

    The socket tracks are REGENERATED from the edited body track wherever
    their host bone moved -- they are a constant rigid offset of one body
    bone, so leaving them behind is what makes a helmet ride off a head.
    Where a socket's host did not move it is carried through verbatim, which
    is also what keeps the unchanged clips byte-identical.
    """
    rep = {"changed": [], "reused": 0, "rebaked": 0, "sockets": [],
           "warnings": [], "bytes": None}
    chunks = list(c3phy.iter_chunks(data))
    slots = _moti_slots(chunks)
    if rig.body_ordinal >= len(slots):
        rep["warnings"].append(
            "body ordinal %d but only %d MOTI chunk(s)"
            % (rig.body_ordinal, len(slots)))
        return rep

    body_slot = slots[rig.body_ordinal]
    src = effects.parse_moti(chunks[body_slot][1])
    if int(src.bone_count) != int(rig.bone_count):
        rep["warnings"].append(
            "body track is %d bones, the rig is %d"
            % (src.bone_count, rig.bone_count))
        return rep

    rest = identity_rest(int(src.bone_count))
    parents = {int(k): v for k, v in rig.parents.items()}
    frames, view = c3anim.moti_to_view(src, rest, parents=parents)
    # THE ROTATION TURNS ABOUT THE JOINT, not the model origin. The joint
    # the bone shares with its parent is a fixed point of the bone's
    # parent-relative basis, and `jointPoints` holds it in the MOTI's own
    # space; the view is in Blender axes, so it crosses `c3anim.AXIS` on the
    # way (a point flips as the translation column does). A root bone has
    # no such joint and keeps the old behaviour, said in the report.
    pivot = None
    parent = parents.get(int(bone))
    if rotate is not None:
        key = "%d-%d" % (min(int(bone), int(parent)), max(int(bone), int(parent)))             if parent is not None else None
        p = (rig.joint_points or {}).get(key) if key else None
        if p is not None:
            pivot = [float(p[i]) * c3anim.AXIS[i] for i in range(3)]
        else:
            rep["warnings"].append(
                "bone %d has no joint with a parent in this rig; the rotation "
                "turns about the model origin" % int(bone))
    view2 = bonerig.apply_delta(view, int(src.bone_count), int(bone),
                                rotate=rotate, translate=translate,
                                scale=scale, pivot=pivot)
    warn: dict = {}
    motion, stats = c3anim.build_motion(
        src, rest, frames, view2, src_frames=frames, src_view=view,
        src_rest=rest, parents=parents, warn=warn)
    rep["reused"] = int(stats.get("reused", 0))
    rep["rebaked"] = int(stats.get("rebaked", 0))
    if warn:
        rep["warnings"].extend("%s: %s" % (k, v) for k, v in warn.items())

    new_bodies = {body_slot: effects.serialize_moti(motion)}
    touched = bonerig.descendants(parents, int(bone))
    rep["changed"] = sorted(touched)

    # The sockets whose host moved.
    s_bodies, s_rows, s_warns = regenerate_sockets(chunks, slots, rig, motion,
                                                   only_hosts=touched)
    new_bodies.update(s_bodies)
    rep["sockets"].extend(s_rows)
    rep["warnings"].extend(s_warns)

    out = [(t, new_bodies.get(i, b)) for i, (t, b) in enumerate(chunks)]
    rep["bytes"] = c3write.build_c3(out)
    return rep


def regenerate_sockets(chunks, slots, rig, motion, *, only_hosts=None,
                       body_ordinal=None):
    """The 1-bone socket tracks, re-derived from a (new) body track.

    -> ``(new_bodies, sockets, warnings)``: ``{chunk index: bytes}`` for every
    socket slot that was regenerated, one report row per socket
    (``{"ordinal", "host", "keys"}``), and the warnings.

    A socket track is not independent data. MEASURED (`bonerig`): every key
    of `v_armet` / `v_l_weapon` / `v_r_weapon` is a CONSTANT rigid offset of
    one body bone -- spread <= 2.2e-5 against 6.7-25.8 for the wrong host --
    so ``socket = constant . host`` per key (row-of-rows 4x4s, the
    `bonerig._as4` shape) reproduces them from whatever the body now does.
    That is what keeps a helmet on a head that turned, and it is why this is
    ONE function used by both callers: `edit_clip` (a delta on a family)
    and `c3retarget` (a whole new body track). Two copies of a regeneration
    loop would be two places for the transpose to go wrong, and a socket
    regenerated in the wrong convention lands somewhere plausible.

    `only_hosts` limits the work to sockets whose host is in the set --
    `edit_clip` passes the edited subtree so untouched sockets stay
    byte-identical (its verify control depends on that). None regenerates
    every socket with a solved host.

    `motion` is the NEW body `effects.Motion`; keys are matched by index,
    so a socket with more keys than the body keeps its tail. The sockets
    are read from `chunks[slots[ordinal]]` and written back through the
    same `c3anim.build_motion` path as the body, so a RAW socket stays RAW
    and a ZKEY one is re-baked in its own encoding.

    `body_ordinal` is the ordinal of the body track IN THIS CONTAINER,
    defaulting to the rig's. They differ when a p84 rig is applied to a
    container laid out otherwise (NPC 901 Sage's standby holds the 84-bone
    body at ordinal 0 with no sockets), and the body slot must never be
    "regenerated" as a socket.
    """
    new_bodies: dict = {}
    rows: list = []
    warnings: list = []
    body_ord = int(rig.body_ordinal if body_ordinal is None else body_ordinal)
    for ordinal_s, meta in sorted((rig.socket_constants or {}).items()):
        ordinal = int(ordinal_s)
        host = meta.get("host")
        if host is None or ordinal >= len(slots) or ordinal == body_ord:
            continue
        if only_hosts is not None and int(host) not in only_hosts:
            continue                                  # its host did not move
        if int(host) >= int(motion.bone_count):
            warnings.append("socket %d rides bone %d but the body track has "
                            "%d bones" % (ordinal, int(host), motion.bone_count))
            continue
        slot = slots[ordinal]
        try:
            sock = effects.parse_moti(chunks[slot][1])
        except Exception as e:                        # noqa: BLE001
            warnings.append("socket %d unreadable: %s" % (ordinal, e))
            continue
        if int(sock.bone_count) != 1:
            continue
        const = meta.get("constant") or []
        if len(const) != 16:
            warnings.append("socket %d has no constant" % ordinal)
            continue
        c4 = ((const[0], const[1], const[2], const[3]),
              (const[4], const[5], const[6], const[7]),
              (const[8], const[9], const[10], const[11]),
              (const[12], const[13], const[14], const[15]))
        n = min(len(sock.keys), len(motion.keys))
        s_frames, s_view = c3anim.moti_to_view(sock, identity_rest(1))
        s_new = list(s_view)
        for k in range(n):
            h4 = bonerig._as4(motion.keys[k].matrices[int(host)])
            tgt = c3anim.mat_mul4(c4, h4)
            s_new[k * 10:(k + 1) * 10] = _socket_view(sock, _flat16(tgt))
        s_warn: dict = {}
        s_motion, _s_stats = c3anim.build_motion(
            sock, identity_rest(1), s_frames, s_new, src_frames=s_frames,
            src_view=s_view, src_rest=identity_rest(1), warn=s_warn)
        new_bodies[slot] = effects.serialize_moti(s_motion)
        rows.append({"ordinal": ordinal, "host": int(host), "keys": n})
        if s_warn:
            warnings.extend("socket %d %s: %s" % (ordinal, k, v)
                            for k, v in s_warn.items())
    return new_bodies, rows, warnings


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _rig_path(rig, out_dir=None, root=None) -> Path:
    if out_dir:
        return Path(out_dir) / ("%s-shape%s.json" % (rig.space, rig.shape))
    return bonerig.rig_path(rig, root)


def _solve_dest(a, rig, root, target=None) -> Path:
    """Where `solve` writes: `--out` (a FILE) if given, else the rig cache for
    the install `--root` names -- the path `_load_or_solve` and c3retarget
    read for that root. Keyed without `root`, a `--root` solve wrote under the
    CONFIGURED install's base-id and never refreshed the cache its readers
    use, so a stale rig there kept being served. `root` is positional so a
    call that drops it fails instead of quietly falling back.

    A monster or NPC family (`target`, a `rigtarget.RigTarget`) caches under
    its own name, ``out/indexes/<base-id>/rig/<family>.json``, never under
    the player space's."""
    if a.out:
        return Path(a.out)
    if target is not None and target.kind != rigtarget.PLAYER:
        return rigtarget.rig_path(target, root)
    return _rig_path(rig, root=root)


def _target(a, root):
    """The `rigtarget.RigTarget` the flags name: `--npc` an NPC family,
    `--shape` a player shape (1-2 digits, the unchanged path) or a 3-digit
    monster shape. Resolved ONCE per command."""
    return rigtarget.resolve(root, shape=a.shape, npc=getattr(a, "npc", None))


def _family_kw(target) -> dict:
    """The `bonerig.family_files` keywords for a target: nothing for the
    player path, the clip list otherwise."""
    if target is None or target.kind == rigtarget.PLAYER:
        return {}
    return {"paths": target.paths}


def _rig_for(a, root, warnings=None):
    """`_load_or_solve` for the player path; the family cache (solve and
    SAVE on a miss) for a monster or NPC target."""
    target = _target(a, root)
    if target.kind == rigtarget.PLAYER:
        return _load_or_solve(a, root, warnings=warnings)
    rig, _p, _how = rigtarget.load_or_solve(
        target, root, max_frames=a.max_frames, rig_file=a.rig,
        mesh_path=getattr(a, "mesh", None), warnings=warnings)
    return rig


def cmd_files(a) -> int:
    root = str(a.root or coroot.default_root())
    target = _target(a, root)
    rows = bonerig.family_files(root, target.rig_shape, **_family_kw(target))
    accepted, bc, ordinal = bonerig.accept(rows)
    print("%s  %s %s (%s)" % (root, target.kind, target.id, target.family))
    if target.kind != rigtarget.PLAYER:
        print("  clips under %s; idle %s; drawn mesh %s (%s)"
              % (target.prefix, target.idle, target.drawn_mesh or "-",
                 target.drawn_mesh_how))
    print("  declared %d   present %d   accepted %d"
          % (len(rows), sum(1 for r in rows if r.sha), len(accepted)))
    # `%d` of None is a TypeError: with nothing accepted the family has no
    # bone count, and that is the finding, not a crash (c3rig.py:288 before
    # 2026-10-01 on `files --shape 126`).
    print("  family: %s bones, body track at ordinal %s"
          % ("?" if bc is None else bc, "?" if ordinal is None else ordinal))
    src: dict = {}
    for r in accepted:
        src[r.source or "?"] = src.get(r.source or "?", 0) + 1
    print("  source: " + ", ".join("%s %d" % kv for kv in sorted(src.items())))
    refused = [r for r in rows if not r.ok and r.sha]
    if refused:
        print("  REFUSED (present but unusable):")
        for r in refused:
            print("    %-28s %s" % (r.path, r.why))
    absent = sum(1 for r in rows if not r.sha)
    if absent:
        print("  %d declared and not shipped by this install" % absent)
    return 0


def cmd_solve(a) -> int:
    root = str(a.root or coroot.default_root())
    t0 = time.time()

    def prog(what, i, n):
        if i == 0:
            print("  %s (%d)..." % (what, n), flush=True)

    target = _target(a, root)
    if target.kind == rigtarget.PLAYER:
        rig = bonerig.solve_family(root, a.shape, max_frames=a.max_frames,
                                   progress=None if a.quiet else prog)
    else:
        rig = rigtarget.solve(target, root, max_frames=a.max_frames,
                              progress=None if a.quiet else prog,
                              mesh_path=getattr(a, "mesh", None))
    p = bonerig.save(rig, _solve_dest(a, rig, root, target))
    print("solved %s %s in %.1fs -> %s" % (target.kind, target.id,
                                           time.time() - t0, p))
    if target.kind != rigtarget.PLAYER:
        print("  reference mesh %s (%s)"
              % (rig.solved_from.get("referenceMesh") or "-",
                 rig.solved_from.get("referenceMeshHow")))
    # `clips` / `framesRead` replaced `framesPooled` when `bonerig` moved to
    # per-clip voting: nothing is pooled any more, and the count that matters
    # for a vote is how many clips voted, not how many frames were stacked.
    print("  %d bones, body ordinal %d, %d clips accepted, %d voted, "
          "%d frames read"
          % (rig.bone_count, rig.body_ordinal, rig.solved_from["accepted"],
             rig.solved_from.get("clips", 0),
             rig.solved_from.get("framesRead", 0)))
    roots = [b for b, par in rig.parents.items() if par is None]
    print("  %d joints, %d skinned bones, %d root(s): %s"
          % (len(rig.joint_points), len(rig.skinned), len(roots), roots))
    for k, v in sorted((rig.socket_constants or {}).items()):
        print("  socket ordinal %s -> host %s (spread %.1e)"
              % (k, v.get("host"), v.get("spread", -1)))
    if rig.welded:
        print("  WELDED, no determinable parent: %s" % sorted(rig.welded))
    return 0


def stale_rig_note(rig, path, root=None, cache=True):
    """A warning naming `path` when `rig` has no census, else None.

    No census means the pre-#200 POOLED solver wrote it (`solvedFrom`
    carries `framesPooled`, not `clips`). The file is still version 1 --
    `FamilyRig.as_dict` says why that is deliberate -- so `bonerig.load`
    takes it and nothing else would say it is not what today's solver
    gives: on the CCO snapshot that solver keys bone 65 as a root where the
    per-clip one makes it a child of 64, and `c3retarget` on the old file
    carries the flap through the original dance."""
    if rig.census:
        return None
    # `solve --out` takes the rig FILE, not its directory (`bonerig.save`).
    # A CACHE is rewritten in place; a file the user named with `--rig` may
    # be hand-made (the flap build's was), so the command writes beside it.
    pooled = (rig.solved_from or {}).get("framesPooled")
    out = (Path(path) if cache
           else Path(path).with_name(Path(path).stem + ".resolved.json"))
    return ("rig %s has no census: solved before the per-clip solver (#200)%s, "
            "so its tree may not be today's. %s: py -3 tools/c3rig.py "
            "solve --shape %s%s --out \"%s\""
            % (path, " (solvedFrom.framesPooled %s)" % pooled if pooled else "",
               "Re-solve it" if cache else "Solve a current one beside it",
               rig.shape, ' --root "%s"' % root if root else "", out))


def _load_or_solve(a, root, warnings=None):
    """The rig `a.rig` names, else this install's saved rig for the shape,
    else a fresh solve. A loaded rig with no census is named
    (`stale_rig_note`): into `warnings` when the caller passes a list, on
    stderr otherwise."""
    def loaded(rig, path):
        note = stale_rig_note(rig, path, root, cache=not a.rig)
        if note:
            if warnings is not None:
                warnings.append(note)
            else:
                print("WARNING: " + note, file=sys.stderr)
        return rig

    if a.rig:
        return loaded(bonerig.load(a.rig), a.rig)
    # The per-shape file, else the pre-2026-10-01 space-only file when it
    # holds this shape (`find_rig` reads its `shape` field: a `p84.json`
    # holding shape 3 is not shape 2's rig, and that case re-solved here
    # for 41-59 s on every shape-2/4 run before the cache was keyed).
    found, _legacy = bonerig.find_rig(bonerig.space_for_shape(a.shape), a.shape, root)
    if found is not None:
        rig = bonerig.load(found)
        if str(rig.shape) == str(a.shape):
            return loaded(rig, found)
    return bonerig.solve_family(root, a.shape, max_frames=a.max_frames)


def cmd_verify(a) -> int:
    """An identity delta over the whole family must be a NO-OP, byte for byte.

    This is the control the write path rests on, and it is the same shape as
    the 396/396 the `.DMap` splice earned: if re-serialising an UNCHANGED
    family does not reproduce the original bytes, nothing this tool writes
    can be trusted, whatever the edit looked like.
    """
    root = str(a.root or coroot.default_root())
    ar = coassets.AssetRoot.bare(root)
    target = _target(a, root)
    rig = _rig_for(a, root)
    rows = bonerig.family_files(root, target.rig_shape, assets=ar,
                                **_family_kw(target))
    accepted, _bc, _ord = bonerig.accept(rows)
    if a.limit:
        accepted = accepted[:a.limit]

    same = diff = skipped = 0
    bad = []
    for i, r in enumerate(accepted):
        if not a.quiet and i % 50 == 0:
            print("  %d/%d..." % (i, len(accepted)), flush=True)
        data = ar.read(r.path)
        rep = edit_clip(data, rig, 0, rotate=[1.0, 0.0, 0.0, 0.0],
                        translate=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0))
        if rep["bytes"] is None:
            skipped += 1
            continue
        if rep["bytes"] == data:
            same += 1
        else:
            diff += 1
            if len(bad) < 8:
                bad.append((r.path, len(data), len(rep["bytes"]),
                            rep["rebaked"]))
    print("identity over shape %s: %d byte-identical, %d DIFFER, %d skipped"
          % (a.shape, same, diff, skipped))
    for p, n0, n1, rb in bad:
        print("    %-30s %d -> %d bytes, %d rebaked" % (p, n0, n1, rb))
    return 0 if diff == 0 else 1


def cmd_apply(a) -> int:
    root = str(a.root or coroot.default_root())
    ar = coassets.AssetRoot.bare(root)
    target = _target(a, root)
    rig = _rig_for(a, root)
    rows = bonerig.family_files(root, target.rig_shape, assets=ar,
                                **_family_kw(target))
    accepted, _bc, _ord = bonerig.accept(rows)
    if a.limit:
        accepted = accepted[:a.limit]

    rotate = (bonerig.quat_axis_angle(a.axis, a.rotate_deg)
              if a.rotate_deg else None)
    translate = tuple(a.translate) if a.translate else None
    scale = tuple(a.scale) if a.scale else None
    if rotate is None and translate is None and scale is None:
        print("nothing to do: give --rotate-deg, --translate or --scale")
        return 2

    run = a.name or ("shape%s-bone%d-%s" % (a.shape, a.bone,
                                            time.strftime("%Y%m%d-%H%M%S")))
    out_dir = Path(a.out) if a.out else (Path(coroot.export_dir()) / "rig" / run)
    entries = []
    written = 0
    for i, r in enumerate(accepted):
        if not a.quiet and i % 50 == 0:
            print("  %d/%d..." % (i, len(accepted)), flush=True)
        data = ar.read(r.path)
        rep = edit_clip(data, rig, a.bone, rotate=rotate, translate=translate,
                        scale=scale)
        row = {"path": r.path, "source": r.source, "shaBefore": r.sha,
               "changedBones": rep["changed"], "reused": rep["reused"],
               "rebaked": rep["rebaked"], "sockets": rep["sockets"],
               "warnings": rep["warnings"]}
        if rep["bytes"] is None:
            row["written"] = False
            row["why"] = "; ".join(rep["warnings"]) or "nothing to write"
        else:
            row["shaAfter"] = bonerig._sha(rep["bytes"])
            row["written"] = not a.dry_run
            if not a.dry_run:
                dst = out_dir / r.path
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(rep["bytes"])
                written += 1
        entries.append(row)

    manifest = {
        "tool": "c3rig",
        "root": root,
        "shape": str(a.shape),
        "bone": int(a.bone),
        "edit": {"axis": a.axis, "rotateDeg": a.rotate_deg,
                 "translate": list(translate) if translate else None,
                 "scale": list(scale) if scale else None},
        "rig": {"space": rig.space, "boneCount": rig.bone_count,
                "bodyOrdinal": rig.body_ordinal,
                "solvedFrom": rig.solved_from},
        "dryRun": bool(a.dry_run),
        "written": written,
        "clips": entries,
        "revert": ("delete this folder; nothing was written to the game "
                   "install, and a loose file only shadows the archive while "
                   "it is there"),
    }
    if not a.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True), "utf-8")
    print("%s %d clip(s) of shape %s, bone %d + descendants %s"
          % ("would write" if a.dry_run else "wrote", written or len(entries),
             a.shape, a.bone,
             entries[0]["changedBones"] if entries else []))
    nwarn = sum(1 for e in entries if e["warnings"])
    if nwarn:
        print("  %d clip(s) carry a warning -- see the manifest" % nwarn)
    if not a.dry_run:
        print("  -> %s" % out_dir)
        print("     revert: delete that folder")
    return 0


def main(argv=None) -> int:
    # The common flags are declared ONCE and attached to every subcommand as
    # well as to the top level, so `c3rig.py files --shape 3` and
    # `c3rig.py --shape 3 files` both work. Accepting only the second is the
    # kind of argument-order rule nobody remembers at 3am.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None)
    common.add_argument("--shape", default="3",
                        help="a player shape (1-2 digits) or a 3-digit MONSTER "
                             "shape, resolved through ini/3dmotion.ini to its "
                             "c3/monster/<dir>/ family (colour morphs share it)")
    common.add_argument("--npc", default=None,
                        help="an NPC family: geometry id (9992720), npc.json "
                             "type (3) or name (Pharmacist); its standby/rest/"
                             "blaze files through core/npcart")
    common.add_argument("--mesh", default=None,
                        help="the reference (drawn) mesh for the solve, an "
                             "asset path; default: the family's ini/3dobj.ini "
                             "row when no clip carries a skinned v_body")
    common.add_argument("--rig", default=None, help="a saved rig json")
    common.add_argument("--max-frames", type=int,
                        default=bonerig.DEFAULT_MAX_FRAMES)
    common.add_argument("--limit", type=int, default=0,
                        help="only the first N clips (for a quick look)")
    common.add_argument("--quiet", action="store_true")

    ap = argparse.ArgumentParser(
        parents=[common],
        description="Edit a body family's rig once, apply it to every clip.")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("files", parents=[common])
    s = sub.add_parser("solve", parents=[common])
    s.add_argument("--out", default=None)
    sub.add_parser("verify", parents=[common])
    p = sub.add_parser("apply", parents=[common])
    p.add_argument("--bone", type=int, required=True)
    p.add_argument("--axis", default="y", choices=("x", "y", "z"))
    p.add_argument("--rotate-deg", type=float, default=0.0)
    p.add_argument("--translate", type=float, nargs=3, default=None)
    p.add_argument("--scale", type=float, nargs=3, default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--dry-run", action="store_true")

    a = ap.parse_args(argv)
    if not getattr(a, "out", None):
        a.out = None
    cmds = {"files": cmd_files, "solve": cmd_solve, "verify": cmd_verify,
            "apply": cmd_apply}
    fn = cmds.get(a.cmd)
    if fn is None:
        ap.print_help()
        return 2
    try:
        return fn(a)
    except (rigtarget.TargetError, ValueError) as e:
        # A shape with no rows, a family with nothing accepted ("no usable
        # clip ... 0 declared, 0 present"): findings, printed as one line
        # with exit 1 -- never a traceback.
        print("REFUSED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
