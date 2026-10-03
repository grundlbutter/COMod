#!/usr/bin/env python3
r"""bonerig.py -- ONE rig per bone-index space, solved from a whole shape and
RECORDED, so an edit can be expressed once and applied to every animation.

    from bonerig import solve_family, save, load, apply_delta
    rig = solve_family(root, "3")          # every present clip of shape 3
    save(rig, root=root)                   # out/indexes/<base-id>/rig/p84-shape3.json

WHY A FAMILY RIG IS POSSIBLE AT ALL
------------------------------------
A `.c3` stores no skeleton: `MOTI` carries no bone name, no parent index and
no bind matrix, and a motion set binds to a mesh **by ordinal** --
`C3Mesh::SetMotion` (graphic.dll 0x277C0) assigns `motionSet.Get(i)` to
`phy[i]`, with one guard that rejects a set shorter than the mesh. So bone
`k` of a clip means bone `k` of the mesh only because the artist exported the
same rig every time.

The corpus honours that convention almost perfectly. MEASURED on CCO and
re-measured here every run:

    shape 1   81 bones, body track at ordinal 3, on 294 of 294 present clips
    shape 2   84 bones, body track at ordinal 0, on 259 of 263
    shape 3   84 bones, body track at ordinal 3, on 286 of 287
    shape 4   84 bones, body track at ordinal 3, on 287 of 290

and the exceptions are a short named list, not a spread. 5517 gives the same
numbers and the same files.

WHAT IS SOLVED, AND WHY IT CANNOT COME FROM ONE CLIP
-----------------------------------------------------
`core/bonejoint` recovers a joint exactly: two bones are articulated if a
point maps to the same place under both their matrices **in every frame**.
That needs motion, and a single clip does not have enough of it:

  * shape 3's idle moves below `MIN_RELATIVE_MOTION` on all 300 pairs and
    yields ZERO joints;
  * shape 2's idle accepts 41 pairs of which 17 are nonsense, because
    `MAX_RESIDUAL` is absolute while residuals scale with how far bones move;
  * hand-to-finger joints are RIGID across all ten unarmed actions at once
    and articulate only in the attacks.

So the solve reads every accepted clip, and it spends its frame budget by
**coverage** rather than by name order -- frames are taken round-robin
across every accepted clip, so a budget spends itself on variety instead of
on the first long clip in sorted order. `blender/io_scene_c3/c3_import` once
took the first ten LOOSE files in name order, which is 8% of the clips on
CCO and a different 8% on another install; that is the instability this
module exists to remove.

BUT THE CLIPS ARE VOTED, NOT POOLED. This module first stacked every clip's
frames into one list and solved it with `bonejoint.joint_graph`, and that is
backlog item 40 (`docs/comod_backlog.md`): one RMS over every frame against
an ABSOLUTE cut grows with the clip count until a pair every clip accepts
singly is rejected. `bonejoint.solve_from_clips` carries the measurement
(5517 body 004137050: pair 4-8 at 0.0000 for one clip, 0.3097 for 28,
against `MAX_RESIDUAL` 0.25; pooling took that skeleton from 9 roots to 12
where voting gives 3). The add-on's `solve_skeleton` was ported first; this
module now calls the same `solve_from_clips`, one verdict per clip and a
majority, and RECORDS the census so a joint carried 10-9 can be told from
one carried 19-0. Measured in the port on shape 3 of the configured install,
old solver against new through this function at budgets 400 and 3,000
alike: the forest went from 2 roots to 1, the two trees agree on 27 of the
28 skinned bones (the 28th is bone 65 -- a root pooled, a child of 64 per
clip), the three socket hosts are identical, and the graph carries edges the
tree never shows the margin of: pair 20-22 accepted 24 yes to 23 no at 3,000
frames, pair 3-16 a 43-43 tie at 400.

THE PRIORS MUST BE IN THE MOTION'S OWN SPACE
---------------------------------------------
`solve_from_clips` (as `joint_graph` before it) takes centroid priors to
resolve a hinge's free axis. They have
to be in the space the MOTI matrices operate in -- which is AFTER the PHY
chunk matrix, because `Phy_Load` applies it at load (RVA 0x5A735) and
`tools/attach.py` does the same before skinning. The chunk matrix is NOT
identity on about half of body meshes, and with raw positions the elbow
solves to the floor and the knees cross the body. `_reference_mesh` pushes
the vertices through `c3phy.apply_matrix_copy` for exactly this reason.

WHAT IS RECORDED, AND WHY IT IS RECORDED
-----------------------------------------
`core/bonetree`'s docstring already argues it: a stored tree cannot drift and
a re-derived one can, because re-deriving runs the rule again against data
the user may have edited. A `FamilyRig` therefore carries its PROVENANCE --
which files, which shas, how many frames, which clips were refused and why --
so two people can tell whether they are holding the same rig.

WHY THIS IS IN tools/ AND NOT core/
------------------------------------
It was written as `core/bonerig.py` and the COre boundary gate refused it,
correctly: this imports `anim`, `effects` and `c3anim`, all of which live in
`tools/`. COre ships as a self-contained package, so a core module that
reaches into tools is a package that fails its own boundary test. The rule
is not a registry chore -- it is the thing that keeps the shipped COre
importable -- and the dependency genuinely points this way, because the
population (`3dmotion.ini`) and the MOTI codec both live in tools.

What stays in core is what was already there and already gated:
`bonejoint` solves the joints, `bonetree` the centroids and spanning forest,
`c3phy` the chunk matrix. This module decides none of that; it decides the
POPULATION, the REFUSALS, the prior's space and the socket constants.

WHAT THIS MODULE DOES NOT DO
-----------------------------
It does not write anything. `tools/c3rig.py` is the driver that applies a
delta across a family and stages the result; the split is the same one
`c3anim`/`c3_anim` already have, and for the same reason: the part where the
decisions live is the part that must run under a gate.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bonejoint                                             # noqa: E402
import bonetree                                              # noqa: E402
import c3phy                                                 # noqa: E402
import coroot                                                # noqa: E402

#: Total frames a solve will read. The budget is spread ROUND-ROBIN across the
#: accepted clips, so this is a budget on cost and never a choice of which
#: clips matter -- the first clip in sorted order does not get to spend it.
#:
#: 3,000 is ~20x the 172-frame pool the shipped importer uses and still solves
#: in seconds; the memory, not the arithmetic, is what bounds it (a frame is
#: bone_count 3x4 tuples).
#:
#: SINCE THE PER-CLIP PORT THE BUDGET ALSO SETS HOW DEEP EACH CLIP'S VOTE IS.
#: `solve_from_clips` gives every clip ONE verdict per pair from its own
#: frames, so a clip's share of this budget is the evidence behind its vote.
#: Measured on shape 3 of the configured install during the port: 286
#: accepted clips holding 10,294 frames (shortest 1, median 21, longest 601),
#: and budgets of 400 and 200 -- what the two corpus arms of
#: `tests/test_bonerig.py` used against the POOLED solver -- spread to 1-2
#: frames per clip. A clip holding one frame abstains on every pair. Pooled,
#: 400 frames (286 of them frame-0 poses, one per clip) were plenty; per clip
#: they are 286 clips that cannot vote. See `_clip_frames`.
DEFAULT_MAX_FRAMES = 3000

#: A socket track is accepted as a rigid child of a body bone only if the
#: offset is this stable across every key. The real ones measure <= 2.2e-5 and
#: the nearest wrong host measures 6.7, so the gap is five orders wide and the
#: exact cut is not delicate.
SOCKET_TOLERANCE = 1e-3

#: Bones that never move relative to each other in ANY clip have no
#: determinable parent. They are REPORTED, never resolved to a spurious edge:
#: `bonejoint` refuses them one clip at a time -- it ABSTAINS, which is not a
#: vote against -- and the rig records that abstention rather than inventing
#: a parent from it. The rule is the census's: a pair is welded when every
#: clip that examined it abstained (`bonejoint.unresolved_pairs`). See
#: `solve_joints` for how that relates to the pooled `relative_motion` test
#: it replaced.
WELDED = "welded"


@dataclass
class ClipInfo:
    """One motion file, and whether the family can use it."""
    path: str
    source: str = ""
    ok: bool = False
    why: str = ""
    moti_count: int = 0
    body_ordinal: int = -1
    bone_count: int = 0
    keys: int = 0
    encoding: str = ""
    sha: str = ""
    #: `phy_names(data)`: the container's PHY names in chunk order, read
    #: while the census has the bytes in hand. Not in `as_dict` -- the rig
    #: file carries the FAMILY's modal order (`solved_from.phyOrder`), not
    #: one tuple per refused row.
    phy_names: tuple = ()

    def as_dict(self) -> dict:
        return {"path": self.path, "source": self.source, "ok": self.ok,
                "why": self.why, "motiCount": self.moti_count,
                "bodyOrdinal": self.body_ordinal, "boneCount": self.bone_count,
                "keys": self.keys, "encoding": self.encoding, "sha": self.sha}


@dataclass
class FamilyRig:
    """One bone-index space, with everything needed to edit it.

    `parents` is the hierarchy, `joint_points` the articulation points that
    justify it, `census` the vote behind each of them, `socket_constants` the
    rigid offsets that let the 1-bone socket tracks be REGENERATED from an
    edited body track rather than authored.
    """
    space: str
    shape: str
    bone_count: int
    body_ordinal: int
    parents: dict = field(default_factory=dict)
    joint_points: dict = field(default_factory=dict)
    welded: dict = field(default_factory=dict)
    socket_constants: dict = field(default_factory=dict)
    skinned: list = field(default_factory=list)
    #: ``{"lo-hi": (yes, no, abstain)}`` -- `bonejoint.solve_from_clips`'s
    #: census, one verdict per clip per pair, for EVERY pair a clip examined
    #: and not only the accepted ones. Recorded because `parents` cannot carry
    #: it: a joint accepted 19-0 and one accepted 10-9 are the same edge in the
    #: tree, and `core/bonejoint.MAX_RESIDUAL`'s comment has the case where
    #: that difference was the whole finding -- body 001's pelvis -> spine_2
    #: sits at 10-9 out of 19 clips, one dissent from a two-root spine, while
    #: its neighbours are unanimous. A rig file that shows only the tree hides
    #: which of its edges are one clip from flipping.
    census: dict = field(default_factory=dict)
    #: How this rig was solved: the population, the refusals, the files. NOT
    #: called `provenance`, and that is not a style choice -- `provenance` is
    #: a RESERVED top-level key in a derived JSON artefact. `core/provenance`
    #: puts the stamp there and tells a stamp from a payload section by the
    #: presence of `schema`, a check that exists because `out/health.json`
    #: already shadowed it once and was reported FOREIGN to every other
    #: install for it. `provenance.wrap` raises outright on a payload that
    #: carries the key, so a rig file spelled that way could never be
    #: stamped. Caught by `TheLiveTreeIsCleanOfThisFault`, which scans the
    #: live `out/` tree -- so it can only fire in a worktree where this tool
    #: has actually been RUN, and the gate worktree never runs it.
    solved_from: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        # STILL "version": 1 with `census` added. The version exists so a
        # reader refuses a file it would MISREAD (`load` raises on anything
        # else, and `test_a_future_format_is_refused_rather_than_misread`
        # pins that). A field that is additive both ways does not qualify: a
        # pre-census reader ignores keys it never asks for, and this reader
        # takes a pre-census file with `d.get` and an empty census. Bumping
        # would make every rig solved before this port unloadable for a field
        # it never needed, which is the downgrade `solved_from`'s two
        # spellings in `load` exist to avoid.
        return {
            "version": 1,
            "space": self.space,
            "shape": self.shape,
            "boneCount": self.bone_count,
            "bodyOrdinal": self.body_ordinal,
            "parents": {str(k): v for k, v in sorted(self.parents.items())},
            "jointPoints": {k: list(v) for k, v in sorted(self.joint_points.items())},
            "census": {k: [int(x) for x in v]
                       for k, v in sorted(self.census.items())},
            "welded": {str(k): v for k, v in sorted(self.welded.items())},
            "socketConstants": self.socket_constants,
            "skinned": list(self.skinned),
            "solvedFrom": self.solved_from,
        }


# ---------------------------------------------------------------------------
# enumeration
# ---------------------------------------------------------------------------

def _sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()[:16]


def _moti_headers(data: bytes) -> list:
    """`[(bone_count, frame_count, encoding)]` per MOTI, in ordinal order.

    Read from the header rather than through `parse_moti`, because a census
    over ~1,100 files only needs the first twelve bytes of each chunk. The
    full parse runs on the clips a solve actually uses, and
    `tests/test_bonerig.py` asserts the two agree on a sample -- a fast reader
    that disagreed with the real parser would be a census of nothing.
    """
    out = []
    for tag, body in c3phy.iter_chunks(data):
        if tag[:3] == b"MOT" and len(body) >= 12:
            bc, fc = struct.unpack_from("<II", body, 0)
            out.append((int(bc), int(fc), body[8:12].decode("latin-1")))
    return out


def phy_names(data: bytes) -> tuple:
    """The PHY chunk names of a container, lower-cased, in chunk order.

    The name is the first field of every PHY variant (`c3phy.parse_phy`:
    u32 length, then the bytes, NUL-terminated), so this reads twelve bytes
    plus the name per chunk and never decodes a vertex. The ORDER is the
    point: the PHY<->MOTI binding is positional (docs/modding.md 11.1), so
    the index of `v_l_weapon` in this tuple is the MOTI ordinal of the
    left-weapon socket track. MEASURED on the CCO snapshot, 2026-10-01:
    shapes 1/3/4 order `v_armet, v_l_weapon, v_r_weapon, v_body` (270/271,
    261/269, 263/263 PHY-carrying clips); shape 2 puts `v_body` FIRST
    (234/238), so its sockets are ordinals 1/2/3. A chunk whose name field
    is malformed contributes '' rather than raising -- the caller counts
    names, and a census must not die on one odd file.
    """
    out = []
    for tag, body in c3phy.iter_chunks(data):
        if tag[:3] != b"PHY":
            continue
        try:
            (n,) = struct.unpack_from("<I", body, 0)
            raw = body[4:4 + n]
        except struct.error:
            out.append("")
            continue
        out.append(raw.split(b"\x00")[0].decode("latin-1", "replace").lower())
    return tuple(out)


def _modal_seq(values) -> Optional[tuple]:
    """The most common tuple, a tie broken by the tuples' own sort order
    (deterministic, as `_modal` is); None for no values."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: (counts[k], k))


def family_files(root, shape: str, *, assets=None, prefix=None, paths=None) -> list:
    """Every clip `ini/3dmotion.ini` names for `shape`, with its verdict.

    Read through a BARE `AssetRoot`, so a loose file shadows the archive copy
    exactly as the client resolves it. That matters here beyond provenance:
    on CCO the loose and archived copies of `c3/0003/500/100.c3` differ in
    ENCODING (ZKEY/27 keys loose, RAW/21 frames archived), so a driver that
    solved from one and wrote the other would silently re-encode.

    `prefix` overrides the player path's ``c3/000<shape>/`` (a monster
    family is ``c3/monster/<dir>/``); `paths` hands the clip list in
    directly (an NPC family's standby/rest/blaze files come from
    `ini/npc.json`, not from the motion table). `tools/rigtarget.py`
    resolves both; everything after the listing is the same verdict.
    """
    import anim                                              # noqa: PLC0415
    import coassets                                          # noqa: PLC0415

    root = str(root)
    ar = assets if assets is not None else coassets.AssetRoot.bare(root)
    if paths is None:
        idx = anim.MotionIndex(root)
        if prefix is None:
            prefix = "c3/000%s/" % str(shape).strip()
        prefix = str(prefix).replace("\\", "/").lower()
        paths = sorted({str(v).replace("\\", "/")
                        for v in idx.raw.values()
                        if str(v).replace("\\", "/").lower().startswith(prefix)})
    else:
        paths = sorted({str(p).replace("\\", "/") for p in paths})

    rows = []
    for p in paths:
        info = ClipInfo(path=p)
        try:
            if not ar.exists(p):
                info.why = "declared and not shipped by this install"
                rows.append(info)
                continue
            data = ar.read(p)
        except Exception as e:                               # noqa: BLE001
            info.why = "unreadable: %s: %s" % (e.__class__.__name__, e)
            rows.append(info)
            continue
        info.sha = _sha(data)
        try:
            info.phy_names = phy_names(data)
        except Exception:                                    # noqa: BLE001
            info.phy_names = ()
        try:
            loc = ar.locate(p)
            info.source = getattr(loc, "source", "") or ""
        except Exception:                                    # noqa: BLE001
            info.source = ""
        try:
            hdr = _moti_headers(data)
        except Exception as e:                               # noqa: BLE001
            info.why = "not a C3 container: %s" % e
            rows.append(info)
            continue
        info.moti_count = len(hdr)
        if not hdr:
            info.why = "no MOTI chunk at all"
            rows.append(info)
            continue
        big = max(range(len(hdr)), key=lambda i: hdr[i][0])
        info.body_ordinal = big
        info.bone_count = hdr[big][0]
        info.keys = hdr[big][1]
        info.encoding = hdr[big][2]
        info.ok = True
        rows.append(info)
    return rows


def _modal(values) -> Optional[int]:
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: (counts[k], -k))


def accept(rows: list) -> tuple:
    """`(accepted, bone_count, body_ordinal)` -- the family's own shape.

    The modal bone count decides, not the first file. `c3_import` pins its
    expectation to whichever file sorted first, so a 26-bone clip appearing
    at the head of the list would silently reduce the whole solve to one
    file; on this corpus `c3/0002/000/451.c3` IS a 26-bone clip.
    """
    usable = [r for r in rows if r.ok]
    bc = _modal([r.bone_count for r in usable])
    ordinal = _modal([r.body_ordinal for r in usable if r.bone_count == bc])
    for r in usable:
        if r.bone_count != bc:
            r.ok = False
            r.why = ("body track declares %d bones, the family is %d -- a "
                     "different rig under this shape's key space"
                     % (r.bone_count, bc))
        elif r.body_ordinal != ordinal:
            r.ok = False
            r.why = ("body track is at ordinal %d, the family binds it at %d;"
                     " binding is BY ORDINAL so this clip would drive the "
                     "wrong parts" % (r.body_ordinal, ordinal))
    return [r for r in rows if r.ok], bc, ordinal


# ---------------------------------------------------------------------------
# the reference mesh -- priors in the motion's own space
# ---------------------------------------------------------------------------

def _reference_mesh(ar, rows: list, bone_count: int):
    """A skinned `v_body` PHY for this shape, with the chunk matrix APPLIED.

    On CCO the clips themselves carry the reference body as PHY chunks --
    1,043 of 1,120 present sets do -- so the priors can come out of the same
    file the motion does and no `armor.ini` lookup is needed. Where a clip is
    motion-only (5517 re-encoded most of its sets that way) the caller
    supplies a mesh instead; this returns None rather than guessing.
    """
    for r in rows:
        try:
            data = ar.read(r.path)
        except Exception:                                    # noqa: BLE001
            continue
        try:
            chunks = list(c3phy.iter_chunks(data))
        except Exception:                                    # noqa: BLE001
            continue
        for tag, body in chunks:
            if tag[:3] != b"PHY":
                continue
            try:
                m = c3phy.parse_phy(tag, body)
            except Exception:                                # noqa: BLE001
                continue
            name = (m.name or "").lower()
            if not name.startswith("v_body"):
                continue
            if max((int(v.bone0) for v in m.vertices), default=0) + 1 < 2:
                continue
            # THE CHUNK MATRIX, APPLIED. `Phy_Load` applies it at load and
            # `attach.py` applies it before skinning, so MOTI space is
            # post-matrix. It is not identity on about half of body meshes,
            # and raw positions put the elbow on the floor.
            return c3phy.apply_matrix_copy(m), r.path
    return None, ""


def reference_mesh_from_file(ar, path: str):
    """The skinned PHY of ONE container -- the DRAWN mesh `ini/3dobj.ini`
    names for a monster family or an NPC geometry -- chunk matrix applied,
    or None.

    `_reference_mesh` looks INSIDE the clips, which is where the player
    shapes keep their body (1,043 of 1,120 CCO sets). 29 of the 59 shipping
    monster families are motion-only (every ZKEY/XKEY family: 126, 120,
    125, 129, 151 ...) and their mesh is the separate ``<dir>/1.c3`` or
    ``<dir>/<id>000000.c3``; an NPC's standby clip does carry a PHY, but it
    is a copy the client never draws (`core/npcart`). The `v_body` PHY is
    preferred; a mesh whose PHY is named otherwise (``<dir>/1.c3`` meshes
    name theirs after the monster) falls back to the LARGEST skinned PHY --
    the 1-bone effect sockets on 120/1.c3 are 12-vertex placeholders, never
    the body.
    """
    try:
        data = ar.read(path)
        chunks = list(c3phy.iter_chunks(data))
    except Exception:                                        # noqa: BLE001
        return None
    best = None
    for tag, body in chunks:
        if tag[:3] != b"PHY":
            continue
        try:
            m = c3phy.parse_phy(tag, body)
        except Exception:                                    # noqa: BLE001
            continue
        if max((int(v.bone0) for v in m.vertices), default=0) + 1 < 2:
            continue
        if (m.name or "").lower().startswith("v_body"):
            return c3phy.apply_matrix_copy(m)
        if best is None or len(m.vertices) > len(best.vertices):
            best = m
    return c3phy.apply_matrix_copy(best) if best is not None else None


# ---------------------------------------------------------------------------
# the solve
# ---------------------------------------------------------------------------

#: The fewest frames a clip is handed, budget or no budget, when it has them.
#: `bonejoint.relative_motion` is deviation from a clip's FRAME 0, so a clip
#: holding one frame measures zero motion on every pair and abstains on all
#: of them: it costs a read and casts no vote. Two frames is the least that
#: can vote at all. Below the budget that funds two for every clip the floor
#: wins and `framesRead` overruns `maxFrames`; both are recorded in
#: `solved_from` so the overrun is visible rather than silent.
MIN_CLIP_FRAMES = 2


def _spaced(frames: list, count: int) -> list:
    """`count` frames spread EVENLY across `frames`, first and last included.

    Why not the leading `count`, which is what the round-robin stack used to
    take from each clip: a clip's first frames are its wind-up, and a per-clip
    verdict is only as wide as the motion its frames span. MEASURED 2026-09-29
    on shape 3 of the configured install (286 clips), the SHIPPED share rule
    (`_shares`, floor on) both ways, `solve_joints` on the result:

        budget 400   567 frames   leading: 49 joints   spaced: 44 joints
        budget 3000  3000 frames  leading: 40 joints   spaced: 38 joints

    One root and the ten labelled chain edges intact in all four arms, so at
    this budget the difference is which junction coincidences survive, not
    the anatomy. The census on the ten chain edges from the spaced 3000-frame
    solve was IDENTICAL to a solve over every frame of every clip (10,294
    frames, 66.5s against 14.2s, measured during the port): the spaced share
    reads the verdict the whole clip would give, at the budget's cost.
    """
    n = len(frames)
    if count >= n:
        return list(frames)
    if count <= 1:
        return [frames[0]]
    idx = sorted({round(i * (n - 1) / (count - 1)) for i in range(count)})
    return [frames[i] for i in idx]


def _clip_frames(ar, rows: list, ordinal: int, max_frames: int,
                 progress=None) -> tuple:
    """-> ``(clips, used)``: frames GROUPED PER CLIP, the budget spread
    round-robin. `clips[i]` is the frames read from `used[i]`.

    Round-robin rather than clip-by-clip: a budget spent in sorted order is a
    budget spent on whichever clips happen to sort first, and the measured
    failure of that rule is that ten unarmed actions never bend a wrist. That
    reasoning is unchanged; what changed is what the frames are FOR.

    This used to FLATTEN the round-robin -- frame k of every clip, then k+1
    -- into one list for the pooled `joint_graph`, and that pooling is the
    defect `core/bonejoint.solve_from_clips` records: one RMS over every
    clip's frames against an absolute cut inflates with the clip count until
    a pair every clip accepts singly is rejected (its docstring's 5517 body
    004137050 pair 4-8: 0.0000 at one clip, 0.3097 at 28, against
    `MAX_RESIDUAL` 0.25; 9 roots pooled became 12 where per-clip voting gives
    3). Per-clip voting needs the clips kept apart, so this returns them
    apart. The budget still decides how many frames each clip contributes --
    exactly the count the round-robin would have taken from it -- and
    `_spaced` decides which of the clip's frames those are.

    WHAT THE BUDGET MEANS NOW. Pooled, the budget bought POSES: frame 0 of
    286 clips is 286 different postures and solved the anatomy on 400 frames
    (`tests/test_bonerig.TheRealFamily`). Per clip, the budget buys DEPTH: a
    clip's share is the evidence behind its one vote per pair, and a share of
    one frame is no vote (`MIN_CLIP_FRAMES`). Measured on the same shape,
    400 spreads to 1-2 frames per clip and 3,000 to 1-12.
    """
    import effects                                           # noqa: PLC0415

    per_clip = []
    used = []
    for i, r in enumerate(rows):
        if progress and i % 25 == 0:
            progress("reading", i, len(rows))
        try:
            data = ar.read(r.path)
            hdr = [(t, b) for t, b in c3phy.iter_chunks(data) if t[:3] == b"MOT"]
            if ordinal >= len(hdr):
                continue
            motion = effects.parse_moti(hdr[ordinal][1])
            frames = bonejoint.matrices_by_key(motion)
        except Exception:                                    # noqa: BLE001
            continue
        if frames:
            per_clip.append(frames)
            used.append(r)
    if not per_clip:
        return [], []
    shares = _shares([len(f) for f in per_clip], max_frames)
    return [_spaced(f, s) for f, s in zip(per_clip, shares)], used


def _shares(lengths: list, max_frames: int) -> list:
    """How many frames each clip contributes: the round-robin's count, then
    the `MIN_CLIP_FRAMES` floor. Pure, so `tests/test_bonerig.py` can pin the
    arithmetic without an install.

    Round-robin: frame k of every clip that has one, then k+1, until the
    budget is met -- so a 601-frame clip and a 21-frame clip are handed the
    same share until the short one runs out, and the long one only then
    spends what is left. The floor lifts a share of one to two wherever the
    clip has two, which is what lets the total exceed `max_frames`.
    """
    shares = [0] * len(lengths)
    total = 0
    for k in range(max(lengths, default=0)):
        if total >= max_frames:
            break
        for i, n in enumerate(lengths):
            if k < n:
                shares[i] += 1
                total += 1
                if total >= max_frames:
                    break
    return [max(s, min(MIN_CLIP_FRAMES, n)) for s, n in zip(shares, lengths)]


def solve_joints(clips: list, bones: list, *, priors=None, mass=None) -> tuple:
    """-> ``(graph, census, parents, welded)`` from clips KEPT APART.

    The solve step of `solve_family`, on its own so it can be run against
    synthetic clips without an install (`tests/test_bonerig.py` does). `clips`
    is ``[frames, ...]`` as `_clip_frames` returns them; `graph` and `census`
    are `bonejoint.solve_from_clips`'s; `parents` is
    `bonejoint.tree_from_joints`'s spanning forest over `graph`.

    WELDED, BY THE CENSUS AND NOT BY THE POOL
    -----------------------------------------
    A bone with no parent in the forest is reported WELDED to the first other
    bone (in `bones` order) whose pair abstained in every clip that examined
    it -- `yes == no == 0, abstain > 0`, which is `bonejoint.unresolved_pairs`.
    Before the port the test was `relative_motion(pooled, b, other) <
    MIN_RELATIVE_MOTION` over the flattened pool. The two agree wherever a
    welded bone can actually occur and differ in one case, stated here so the
    change is a decision and not a drift:

    * AGREE on a pair rigid in every clip at the SAME offset -- the real
      welded bone, whose relation to its host is fixed at bind and so is the
      same in every clip ever authored (`bonejoint.unresolved_pairs` cites
      `docs/motion_policy_2026-09-21.md` section 2 for 30 such bones on body
      0003). The pooled deviation is zero and every clip abstains.
    * AGREE on a pair that moves in any clip. The pool sees that clip's
      deviation; the census gets a yes or a no from it. Neither calls it
      welded.
    * DIFFER on a pair rigid WITHIN each clip at a DIFFERENT offset per clip.
      Pooled, the offsets disagree across the concatenation, the deviation
      clears the floor and the old rule said "moves". Per clip, no clip can
      articulate it, none votes, and the census says welded. The census is
      the reading the tree can act on: under per-clip voting such a pair can
      never become a joint, and a parentless bone whose every pair is
      unresolved is exactly what `welded` exists to report.

    Measured on shape 3 of the configured install during the port, every
    frame of every clip: 0 all-abstain pairs among the skinned bones, so this
    family reports no welded bone under the new rule.
    """
    graph, census = bonejoint.solve_from_clips(clips, bones, centroids=priors)
    parents = bonejoint.tree_from_joints(bones, graph, mass=mass)
    rigid = set(bonejoint.unresolved_pairs(census))
    welded: dict = {}
    for b in bones:
        if parents.get(b) is not None:
            continue
        for other in bones:
            if other == b:
                continue
            if (min(b, other), max(b, other)) in rigid:
                welded[b] = other
                break
    return graph, census, parents, welded


def _socket_constants(ar, rows: list, rig_parents: dict, bone_count: int,
                      body_ordinal: int, moti_count: int) -> dict:
    """For each 1-bone ordinal, the body bone it rides and the constant offset.

    A socket track is not independent data: MEASURED, every key of
    `v_armet` / `v_l_weapon` / `v_r_weapon` is a CONSTANT rigid offset of one
    body bone (spread <= 2.2e-5, against 6.7-25.8 for the wrong host). So a
    tool that re-bakes the body track can REGENERATE them instead of asking a
    user to author three more tracks by hand.

    Solved here, refused rather than guessed: a socket whose offset is not
    stable is reported with its spread and no host.
    """
    import c3anim                                            # noqa: PLC0415
    import effects                                           # noqa: PLC0415

    out: dict = {}
    for r in rows[:6]:                                       # a handful is plenty
        try:
            data = ar.read(r.path)
            chunks = [(t, b) for t, b in c3phy.iter_chunks(data) if t[:3] == b"MOT"]
            if len(chunks) != moti_count:
                continue
            body = effects.parse_moti(chunks[body_ordinal][1])
        except Exception:                                    # noqa: BLE001
            continue
        for ordinal, (tag, chunk) in enumerate(chunks):
            if ordinal == body_ordinal or str(ordinal) in out:
                continue
            try:
                sock = effects.parse_moti(chunk)
            except Exception:                                # noqa: BLE001
                continue
            if int(sock.bone_count) != 1:
                continue
            n = min(len(sock.keys), len(body.keys))
            if n < 2:
                continue
            best = None
            for host in range(min(bone_count, int(body.bone_count))):
                consts = []
                for k in range(n):
                    s4 = _as4(sock.keys[k].matrices[0])
                    h4 = _as4(body.keys[k].matrices[host])
                    try:
                        consts.append(c3anim.mat_mul4(s4, c3anim.mat_inv4(h4)))
                    except (c3anim.AnimEditError, ZeroDivisionError):
                        # A SINGULAR host matrix: a bone this clip collapses,
                        # which many of the 84 are -- only ~28 are skinned.
                        # That is a fact about the candidate, so drop the
                        # candidate and try the next bone; it is not a fact
                        # about the socket and it is not an error.
                        #
                        # NAMED, not broad, and named by READING what the
                        # callee raises. This except was `Exception` first,
                        # which swallowed a shape bug and reported "no
                        # sockets found" -- a defect wearing the clothes of a
                        # finding. Narrowing it to ZeroDivisionError alone
                        # then crashed three of the four shapes, because
                        # `mat_inv4` raises `AnimEditError`. Too broad hides a
                        # bug; too narrow turns a data condition into a
                        # crash; the cut is whatever the callee documents.
                        consts = []
                        break
                if not consts:
                    continue
                spread = max(
                    max(abs(consts[k][r][c] - consts[0][r][c])
                        for r in range(4) for c in range(4))
                    for k in range(1, len(consts)))
                if best is None or spread < best[0]:
                    best = (spread, host, consts[0])
            if best is None:
                continue
            spread, host, const = best
            out[str(ordinal)] = {
                "host": host if spread <= SOCKET_TOLERANCE else None,
                "spread": round(float(spread), 9),
                "constant": [round(float(const[r][c]), 7)
                             for r in range(4) for c in range(4)],
                "from": r.path,
                "why": ("rigid child of bone %d" % host
                        if spread <= SOCKET_TOLERANCE else
                        "no stable host: best spread %.6f on bone %d"
                        % (spread, host)),
            }
        if len(out) >= moti_count - 1:
            break
    return out


def _as4(v):
    """A MOTI's 16 floats as the ROW-OF-ROWS 4x4 `c3anim` multiplies.

    `mat_mul4` unpacks `a0, a1, a2, a3 = a` and then indexes `b[0]` as a row,
    so a flat 16 raises a `TypeError` two lines in. The first draft of this
    module handed it a flat list; the broad `except` around the host search
    caught that, `consts` came back empty, and the module reported **no
    sockets found** -- a defect wearing the clothes of a finding. The except
    below is narrowed for the same reason.

    C3 stores column-major (`c3_matrix_to_blender` reads element `c * 4 + r`
    for row `r`, column `c`), so the transpose is the conversion, and the
    translation is elements 12..14 -- row-vector convention, as
    `bonejoint.matrices_by_key` documents.
    """
    f = [float(x) for x in v]
    if len(f) != 16:
        raise ValueError("a MOTI matrix is 16 floats, got %d" % len(f))
    return ((f[0], f[1], f[2], 0.0),
            (f[4], f[5], f[6], 0.0),
            (f[8], f[9], f[10], 0.0),
            (f[12], f[13], f[14], 1.0))


def solve_family(root, shape: str, *, max_frames: int = DEFAULT_MAX_FRAMES,
                 progress: Optional[Callable] = None,
                 assets=None, paths=None, prefix=None,
                 mesh_path=None) -> FamilyRig:
    """The rig for one shape, solved from every clip that install ships.

    `paths` / `prefix` name the clips for a monster or NPC family
    (`family_files`; `tools/rigtarget.py` resolves them); `mesh_path` is the
    DRAWN mesh to take the reference body from -- read FIRST when given,
    because a motion-only family (126's ZKEY clips) has no body in any clip,
    and an NPC clip's PHY is a copy the client never draws. The player path
    (no `paths`, no `mesh_path`) is unchanged: the body comes out of the
    clips, as before. `solved_from.referenceMeshHow` says which.
    """
    import coassets                                          # noqa: PLC0415

    root = str(root)
    ar = assets if assets is not None else coassets.AssetRoot.bare(root)
    rows = family_files(root, shape, assets=ar, prefix=prefix, paths=paths)
    accepted, bone_count, ordinal = accept(rows)
    if not accepted:
        raise ValueError(
            "no usable clip for shape %r under %s -- %d declared, %d present"
            % (shape, root, len(rows), sum(1 for r in rows if r.sha)))

    mesh = None
    mesh_from = ""
    mesh_how = ""
    if mesh_path:
        mesh = reference_mesh_from_file(ar, str(mesh_path))
        if mesh is not None:
            mesh_from = str(mesh_path).replace("\\", "/")
            mesh_how = "drawn mesh (mesh_path)"
    if mesh is None:
        mesh, mesh_from = _reference_mesh(ar, accepted, bone_count)
        mesh_how = ("skinned v_body PHY inside a clip" if mesh is not None
                    else "none: no skinned v_body in any clip%s"
                    % (" and %s has no skinned PHY" % mesh_path if mesh_path else ""))
    priors = None
    skinned: list = []
    if mesh is not None:
        priors = bonetree.bone_centroids(mesh.vertices)
        skinned = sorted(priors)

    clips, used = _clip_frames(ar, accepted, ordinal, max_frames, progress)
    if not clips:
        raise ValueError("no frames could be read for shape %r" % shape)

    bones = skinned or list(range(bone_count))
    if progress:
        progress("solving", 0, len(bones))
    # Per clip, not pooled: backlog item 40. `joint_graph` over the flattened
    # pool was one RMS against an absolute cut, and it grew with the clip
    # count until real joints fell off it -- `solve_from_clips`'s docstring
    # has the 5517 measurement. This module's whole reason to pool was
    # coverage (one clip has too little motion); voting per clip keeps the
    # coverage and drops the inflation.
    mass = bonetree.bone_mass(mesh.vertices) if mesh is not None else None
    graph, census, parents, welded = solve_joints(clips, bones, priors=priors,
                                                  mass=mass)

    moti_count = _modal([r.moti_count for r in accepted]) or 1
    sockets = _socket_constants(ar, accepted, parents, bone_count, ordinal,
                                moti_count)
    # The family's PHY-name order, so a target that carries no PHY (17-24
    # clips per shape on CCO) or misnames a socket (`v_r_weapon01` for the
    # left one on 1-4 per shape; two `v_r_weapon` on 4 shape-3 clips) can
    # still have its socket ordinals read by NAME through the family
    # (`c3retarget.socket_ordinal`). Only clips with as many PHY chunks as
    # the family has MOTI tracks vote: a 5-PHY oddball binds differently.
    with_phy = [r.phy_names for r in accepted
                if r.phy_names and len(r.phy_names) == moti_count]
    phy_order = _modal_seq(with_phy)

    rig = FamilyRig(
        space="p%d" % bone_count,
        shape=str(shape),
        bone_count=int(bone_count),
        body_ordinal=int(ordinal),
        parents={int(k): (None if v is None else int(v))
                 for k, v in parents.items()},
        joint_points={"%d-%d" % k: tuple(round(float(x), 6) for x in v[1])
                      for k, v in graph.items()},
        census={"%d-%d" % k: tuple(int(x) for x in v)
                for k, v in census.items()},
        welded={int(k): int(v) for k, v in welded.items()},
        socket_constants=sockets,
        skinned=skinned,
        solved_from={
            "root": root,
            "shape": str(shape),
            "declared": len(rows),
            "present": sum(1 for r in rows if r.sha),
            "accepted": len(accepted),
            # `clips` voted and `framesRead` is their evidence; `framesPooled`
            # went with the pooling. `framesRead` may exceed `maxFrames` by
            # design -- see `MIN_CLIP_FRAMES`.
            "clips": len(clips),
            "framesRead": sum(len(c) for c in clips),
            "minClipFrames": int(MIN_CLIP_FRAMES),
            "maxFrames": int(max_frames),
            "referenceMesh": mesh_from,
            "motiCount": int(moti_count),
            "phyOrder": list(phy_order) if phy_order else None,
            "phyOrderClips": sum(1 for n in with_phy if n == phy_order),
            "phyClips": len(with_phy),
            "refused": [r.as_dict() for r in rows if not r.ok],
            "files": [{"path": r.path, "sha": r.sha, "source": r.source,
                       "keys": r.keys, "encoding": r.encoding}
                      for r in used],
        },
    )
    # Recorded after construction so the dict literal above keeps its shape
    # (two branches add keys to it in the same week; appending here merges).
    rig.solved_from["referenceMeshHow"] = mesh_how
    rig.solved_from["clipPrefix"] = prefix or ("" if paths is not None
                                               else "c3/000%s/" % str(shape).strip())
    rig.solved_from["clipPaths"] = (sorted({str(p).replace("\\", "/") for p in paths})
                                    if paths is not None else None)
    return rig


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def space_for_shape(shape) -> str:
    """The bone-index space a player shape's clips live in: `p81` for shape
    1, `p84` for 2/3/4 (the module docstring's census). The one place this
    guess is spelled; `c3rig._load_or_solve` and `c3retarget` both read it."""
    return "p81" if str(shape).strip() == "1" else "p84"


def rig_path(rig_or_space, root=None, shape=None) -> Path:
    """Where THIS install's rig for a space AND SHAPE belongs.

    Through `coroot.derived_path`, so it lands in the per-install namespace:
    `out/indexes/<base-id>/rig/p84-shape3.json`, not a shared
    `out/rig/...`. A rig is solved from the clips one install ships and those
    differ between installs -- 263 present clips for shape 2 on CCO against
    412 on 5517, and only 125 of the shared ones byte-identical -- so a shared
    path would put one install's answer under another's name. That is the
    `out/dll/rtti.md` bug, and `coroot.PER_BASE` exists because it already
    happened once.

    Keyed on the SHAPE as well since 2026-10-01: shapes 2, 3 and 4 share the
    p84 space but not a geometry (shape 4's shoulder joint sits 7.1 units
    further out and 15.4 higher than shape 3's; `docs/c3retarget_2026-09-30.md`,
    'Shapes 2 and 4'), so one `p84.json` held whichever shape solved last and
    `c3rig._load_or_solve` re-solved 41-59 s on every other shape's run. The
    pre-2026-10-01 file name is `legacy_rig_path`; `find_rig` still reads it.

    `shape` comes from the rig when a rig is passed; a bare space string needs
    it spelled, and a missing shape is a `ValueError` rather than a path that
    two shapes would share.
    """
    space = getattr(rig_or_space, "space", None) or str(rig_or_space)
    if shape is None:
        shape = getattr(rig_or_space, "shape", None)
    if shape is None or str(shape).strip() == "":
        raise ValueError("rig_path needs the shape: the rig cache is keyed per "
                         "shape (%s is shared by shapes 2/3/4)" % space)
    return coroot.derived_path("out/rig/%s-shape%s.json" % (space, str(shape).strip()),
                               root)


def legacy_rig_path(space, root=None) -> Path:
    """The space-only cache name every shape wrote before 2026-10-01
    (`out/indexes/<base-id>/rig/p84.json`). Read by `find_rig` so a box that
    solved shape 3 under the old name is not re-solved; never written."""
    space = getattr(space, "space", None) or str(space)
    return coroot.derived_path("out/rig/%s.json" % space, root)


def find_rig(space, shape, root=None) -> tuple:
    """``(path, legacy)``: the saved rig file for `space`/`shape` under
    `root`'s namespace -- the per-shape file when it exists, else the legacy
    space-only file WHEN IT HOLDS THIS SHAPE (its `shape` field is read; a
    `p84.json` that holds shape 3 is not shape 2's rig), else ``(None,
    False)``. `legacy` says the second case happened, so a caller can
    migrate the file to its per-shape name (`c3retarget` does, and says so
    in its manifest)."""
    keyed = rig_path(space, root, shape)
    if keyed.is_file():
        return keyed, False
    old = legacy_rig_path(space, root)
    if old.is_file():
        try:
            if str(load(old).shape) == str(shape).strip():
                return old, True
        except (OSError, ValueError, KeyError):
            pass
    return None, False


def save(rig: FamilyRig, path=None, *, root=None) -> Path:
    p = Path(path) if path is not None else rig_path(rig, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    try:
        tmp.write_text(json.dumps(rig.as_dict(), indent=1, sort_keys=True),
                       "utf-8")
        tmp.replace(p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return p


def load(path) -> FamilyRig:
    d = json.loads(Path(path).read_text("utf-8"))
    if int(d.get("version", 0)) != 1:
        raise ValueError("rig file version %r is not 1" % d.get("version"))
    return FamilyRig(
        space=d["space"], shape=d["shape"], bone_count=int(d["boneCount"]),
        body_ordinal=int(d["bodyOrdinal"]),
        parents={int(k): v for k, v in d["parents"].items()},
        joint_points={k: tuple(v) for k, v in d.get("jointPoints", {}).items()},
        # `d.get`: a rig solved before the per-clip port has no census and is
        # still a version-1 file -- see `as_dict` for why that is not a bump.
        census={k: tuple(int(x) for x in v)
                for k, v in (d.get("census") or {}).items()},
        welded={int(k): int(v) for k, v in d.get("welded", {}).items()},
        socket_constants=d.get("socketConstants", {}),
        skinned=list(d.get("skinned", [])),
        # Either spelling: a rig solved before the rename is still on
        # somebody's disk, and losing its solve record on load would be a
        # silent downgrade of the thing this field exists to preserve.
        solved_from=d.get("solvedFrom") or d.get("provenance") or {},
    )


# ---------------------------------------------------------------------------
# the edit
# ---------------------------------------------------------------------------

def descendants(parents: dict, bone: int) -> set:
    """`bone` and everything under it, by the rig's own tree."""
    kids: dict = {}
    for b, p in parents.items():
        if p is not None:
            kids.setdefault(int(p), []).append(int(b))
    out, stack = set(), [int(bone)]
    while stack:
        b = stack.pop()
        if b in out:
            continue
        out.add(b)
        stack.extend(kids.get(b, ()))
    return out


def apply_delta(view: list, bone_count: int, bone: int, *,
                rotate=None, translate=None, scale=None, pivot=None) -> list:
    """A local delta on ONE bone, on EVERY key -- a new `view` list.

    `pivot` is the point the rotation turns about, in the view's own space
    (the bone's parent-relative basis, Blender axes). Without it a rotation
    turns about the basis ORIGIN, and with the identity rest `c3rig` uses
    that origin is the model's: MEASURED 2026-09-29 on the live client, a
    30-degree edit of `upper_arm.R` moved the shoulder joint 72.7 units --
    two fifths of the body -- and the arm read as sticking straight out
    from the ribs. With the joint the bone shares with its parent as the
    pivot the joint stays put (0.00) and only the direction changes. The
    basis is ``T(loc) . R . S``; turning it about ``p`` by ``D`` gives
    ``T(p + D(loc - p)) . (D R) . S``, so the quaternion is premultiplied as
    before and the location moves by ``D(loc - p) - (loc - p)`` -- written
    that way so an identity rotation leaves every float bit-identical, which
    `c3rig verify`'s byte-for-byte control depends on.

    `view` is exactly what `c3anim.moti_to_view` produces: `len(frames) *
    bone_count * 10` floats, laid out (key, bone) with each bone's ten being
    location xyz, quaternion wxyz, scale xyz. Under a parented view those ten
    are the bone's LOCAL basis, so changing them here is the local edit -- and
    `c3anim.build_motion` walks root-first and carries the change into every
    descendant's absolute matrix on its own. That is why this is thirty lines
    and not a matrix pipeline.

    Nothing is edited in place: a re-bake decides what to reuse by comparing
    the caller's view against the importer's, and mutating the one it was
    given would make every bone look edited.
    """
    out = list(view)
    per = 10
    stride = bone_count * per
    if stride <= 0 or len(out) % stride:
        raise ValueError("view of %d floats is not a whole number of keys at "
                         "%d bones" % (len(out), bone_count))
    if not (0 <= int(bone) < bone_count):
        raise ValueError("bone %r outside 0..%d" % (bone, bone_count - 1))
    keys = len(out) // stride
    for k in range(keys):
        base = k * stride + int(bone) * per
        if translate is not None:
            for i in range(3):
                out[base + i] = float(out[base + i]) + float(translate[i])
        if rotate is not None:
            q = out[base + 3:base + 7]
            out[base + 3:base + 7] = _qmul(rotate, q)
            if pivot is not None and any(float(c) != 0.0 for c in rotate[1:]):
                d = [float(out[base + i]) - float(pivot[i]) for i in range(3)]
                rd = _qrot(rotate, d)
                for i in range(3):
                    out[base + i] = float(out[base + i]) + (rd[i] - d[i])
        if scale is not None:
            for i in range(3):
                out[base + 7 + i] = float(out[base + 7 + i]) * float(scale[i])
    return out


def _qrot(q, v) -> list:
    """`v` rotated by the unit quaternion `q` (wxyz): ``v + 2w(u x v) +
    2u x (u x v)`` with ``u`` the vector part. Exact for the identity."""
    w, ux, uy, uz = (float(x) for x in q)
    vx, vy, vz = (float(x) for x in v)
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    dx, dy, dz = uy * cz - uz * cy, uz * cx - ux * cz, ux * cy - uy * cx
    return [vx + 2.0 * (w * cx + dx), vy + 2.0 * (w * cy + dy),
            vz + 2.0 * (w * cz + dz)]


def _qmul(a, b) -> list:
    """Quaternion product, wxyz -- the order `c3anim.decompose` hands out."""
    aw, ax, ay, az = (float(x) for x in a)
    bw, bx, by, bz = (float(x) for x in b)
    return [aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw]


def quat_axis_angle(axis: str, degrees: float) -> list:
    """A wxyz quaternion for a rotation about x, y or z."""
    import math                                              # noqa: PLC0415
    a = {"x": 0, "y": 1, "z": 2}.get(str(axis).lower())
    if a is None:
        raise ValueError("axis must be x, y or z, not %r" % axis)
    h = math.radians(float(degrees)) * 0.5
    q = [math.cos(h), 0.0, 0.0, 0.0]
    q[1 + a] = math.sin(h)
    return q
