#!/usr/bin/env python3
r"""boneplace.py -- where a C3 bone belongs, derived from the skin.

A `.c3` stores NO REST SKELETON.  A `MOTI` matrix is a *skinning* matrix, so
the rest pose it implies is the identity for every bone, which would stack all
84 of a character body's bones on the origin -- coincident, unselectable and
unpaintable.  The importer's answer has been a labelled ladder up +Z, which is
correct and unusable: at the shipped 4.0 spacing an 84-bone body is a 335-metre
column of identical bones, and picking "the left forearm" out of it is not a
thing a person can do.

THE FILE DOES NOT STORE BONE POSITIONS, BUT THE SKIN KNOWS THEM.  Bone N's
weighted vertices *are* the body part it drives.  So each bone is placed along
the principal axis of its own vertex cloud, head and tail at the extremes of
that cloud, which puts a forearm bone ALONG the forearm instead of across it.

WHY MOVING THE REST POSE IS SAFE, WHICH IS THE WHOLE LICENCE FOR THIS FILE
--------------------------------------------------------------------------
Blender skins a vertex with ``P . L^-1`` where ``P = L . B``, and `c3_anim`
writes ``B = L^-1 . M . L``, so the transform actually applied is ``M`` for ANY
rest pose ``L`` -- the rest cancels.  Measured on `003188495.c3`, whose body
track declares 84 bones: two rest poses whose pose channels differ by **107.35**
produce skinning transforms differing by **4.4e-06**, which is float32 noise.
`tests/test_boneplace.py` pins that identity rather than leaving it as prose.

The corollary is the reason this belongs at IMPORT time and not in a script run
afterwards: `c3_anim.import_motion` stashes `PROP_REST` from whatever rest pose
it finds, and `export_motion` reuses the source keys verbatim when the live rest
still matches that stash.  Placing bones BEFORE the motion is imported therefore
keeps the export byte-exact.  Moving them AFTER makes the stash stale and forces
a re-bake of every key.

WHAT IT CANNOT DO, STATED RATHER THAN DISCOVERED
------------------------------------------------
A bone with no weighted vertices has no cloud and therefore no position that is
derived from anything.  It is REPORTED as unplaceable, never guessed at.  That
is not a rare corner: on `003188495`'s body the MOTI declares 84 bones and the
skin uses 25, so **59 of 84 cannot be placed**.  The caller parks and hides
them, and hiding them is what makes the other 25 selectable.

Measured on that body -- 25 placed, 25 distinct centroids, spanning X
-83.9..84.1 against mesh bounds +/-89.0 and Z -165.3..-1.9 against mesh
-178.4..0.0.  The arm chain reads 14.5 -> 29.0 -> 59.2 -> 76.9 -> 84.1 along X,
which is a shoulder-to-hand run and not a blob.
"""
from __future__ import annotations

import math

#: Blender deletes a zero-length bone, so a degenerate cloud still has to
#: produce something with extent.  Small enough to be invisible next to a
#: 180-unit character, large enough to survive.
MIN_LENGTH = 0.02

#: A weight at or below this contributes nothing a person could see, and
#: including it drags a centroid toward geometry the bone does not drive.
MIN_WEIGHT = 1e-4

#: Power-iteration steps for the dominant eigenvector.  32 is already past the
#: point where the axis stops moving on real clouds; 48 costs nothing on a
#: 846-vertex mesh and removes the question.
_ITERS = 48

#: A start vector that is not axis-aligned.  An axis-aligned start is a fixed
#: point of a diagonal covariance matrix, so it would return itself and the
#: placement would silently be "whatever I guessed" on any axis-aligned cloud.
_SEED = (0.31, 0.53, 0.79)


def skin_clouds(vertices, *, min_weight: float = MIN_WEIGHT,
                transform=None) -> dict:
    """``{bone_index: [(position, weight), ...]}`` from PHY vertices.

    Duck-typed on `c3phy.PhyVertex` -- `position`, `bone0`, `weight0`,
    `bone1`, `weight1` -- so a test can pass a tuple and this module needs no
    import from the project.

    `transform` MAPS A PHY POSITION INTO THE SPACE THE BONES LIVE IN, and
    passing it is not optional in practice.  A PHY position is not where the
    vertex ends up: `c3_import` builds the mesh through `K.to_blender`, which
    NEGATES Z, and then puts the chunk's own matrix on the object as its
    transform.  Bones placed from raw positions therefore land mirrored in Z
    and un-rotated -- visibly below the mesh and facing the wrong way, which
    is exactly what shipping without this produced.

    Slot 1 contributes only when it names a DIFFERENT bone, mirroring
    `Phy_Load`'s palette rule (RVA 0x5A1A0) and `attach_skin`.  27% of corpus
    vertices have ``bone0 == bone1``; counting such a vertex twice would weight
    its own bone's centroid toward itself for no reason.
    """
    out: dict = {}
    for v in vertices:
        p = tuple(v.position)
        if transform is not None:
            p = tuple(transform(p))
        w0 = float(v.weight0)
        if w0 > min_weight:
            out.setdefault(int(v.bone0), []).append((p, w0))
        w1 = float(v.weight1)
        if int(v.bone1) != int(v.bone0) and w1 > min_weight:
            out.setdefault(int(v.bone1), []).append((p, w1))
    return out


def _centroid(cloud):
    tw = sum(w for _p, w in cloud)
    if tw <= 0.0:
        return None
    return tuple(sum(p[i] * w for p, w in cloud) / tw for i in range(3))


def principal_axis(cloud, centroid):
    """Unit dominant eigenvector of the weighted covariance, by power iteration.

    Deliberately dependency-free: the addon must not acquire numpy, and this is
    ~15 lines of arithmetic.  Returns +Z for a cloud with no spread, which is
    the only honest answer when every point is the same point.
    """
    xx = xy = xz = yy = yz = zz = 0.0
    for p, w in cloud:
        dx = p[0] - centroid[0]
        dy = p[1] - centroid[1]
        dz = p[2] - centroid[2]
        xx += w * dx * dx
        xy += w * dx * dy
        xz += w * dx * dz
        yy += w * dy * dy
        yz += w * dy * dz
        zz += w * dz * dz
    v = _SEED
    for _ in range(_ITERS):
        nv = (xx * v[0] + xy * v[1] + xz * v[2],
              xy * v[0] + yy * v[1] + yz * v[2],
              xz * v[0] + yz * v[1] + zz * v[2])
        n = math.sqrt(nv[0] * nv[0] + nv[1] * nv[1] + nv[2] * nv[2])
        if n < 1e-12:
            return (0.0, 0.0, 1.0)
        v = (nv[0] / n, nv[1] / n, nv[2] / n)
    return v


def place_bone(cloud, *, min_length: float = MIN_LENGTH):
    """One cloud -> ``(head, tail)``, or ``None`` when it cannot be placed."""
    if not cloud:
        return None
    c = _centroid(cloud)
    if c is None:
        return None
    a = principal_axis(cloud, c)
    proj = [sum((p[i] - c[i]) * a[i] for i in range(3)) for p, _w in cloud]
    lo, hi = min(proj), max(proj)
    if hi - lo < min_length:
        lo, hi = -min_length * 0.5, min_length * 0.5
    head = tuple(c[i] + a[i] * lo for i in range(3))
    tail = tuple(c[i] + a[i] * hi for i in range(3))
    return head, tail


def joint_positions(vertices, *, min_weight: float = MIN_WEIGHT,
                    transform=None) -> dict:
    """``{(lo, hi): position}`` -- WHERE TWO BONES MEET.

    A vertex blended between bone A and bone B sits AT the joint between them,
    so the centroid of those vertices is a direct estimate of the joint's
    position.  This is a much better bone estimator than the principal axis of
    a bone's whole cloud, and the corpus says so rather than the argument:

    MEASURED on 003188495 v_body, the two arm chains the tree makes mirror
    images -- elbow 009-010 lands at x=+42.6 and 017-018 at x=-42.6, summing
    to **0.0**.  The same bones placed by principal axis sum to -38.7 and
    -30.5.  Knees 042-043 (+19.3) and 047-048 (-19.3) mirror the same way.

    PCA IS ONLY RIGHT FOR A LIMB.  For a skirt panel or a torso the widest
    axis of the cloud is a horizontal chord ACROSS the body, which is why
    those bones crossed the mesh instead of lying in it -- and each axis is
    solved independently, so nothing makes a left and a right agree.
    """
    acc: dict = {}
    for v in vertices:
        a, b = int(v.bone0), int(v.bone1)
        if a == b:
            continue
        if float(v.weight0) <= min_weight or float(v.weight1) <= min_weight:
            continue
        p = tuple(v.position)
        if transform is not None:
            p = tuple(transform(p))
        acc.setdefault((a, b) if a < b else (b, a), []).append(p)
    return {k: tuple(sum(q[i] for q in pts) / len(pts) for i in range(3))
            for k, pts in acc.items()}


def _d2(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3))


def _child_at(kids, b, joint, point):
    """Which child of `b` is the one whose joint IS `point`."""
    for c in kids.get(b, ()):
        j = joint(b, c)
        if j is not None and _d2(j, point) < 1e-12:
            return c
    return None


def _subtree_size(parents, root):
    """How many bones hang off `root`, itself included. Used only to decide
    which END of a root bone is its head."""
    if root is None:
        return 0
    kids = {}
    for b, p in parents.items():
        if p is not None:
            kids.setdefault(int(p), []).append(int(b))
    seen, stack, n = {int(root)}, [int(root)], 0
    while stack:
        x = stack.pop()
        n += 1
        for c in kids.get(x, ()):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return n


def place_from_tree(bone_indices, vertices, parents, *,
                    min_length: float = MIN_LENGTH,
                    min_weight: float = MIN_WEIGHT, transform=None,
                    joints=None):
    """Bones that run JOINT TO JOINT, the way a real rig is shaped.

    ``head`` is where the bone meets its parent; ``tail`` is where it meets
    its children.  A bone with neither is placed by `place_from_skin`'s
    principal axis, which is still the best available answer when the skin
    declares no joints for it at all.

    -> ``(placed, unplaced)``, same shape as `place_from_skin`.
    """
    bones = [int(b) for b in bone_indices]
    # SOLVED joints win over skin-derived ones wherever they exist. The skin
    # can only see a joint that vertices blend across, and it invents a few
    # that are not joints at all -- 043-048 is one knee to the OTHER knee,
    # which `bonejoint` rejects at a residual of 12.9. Skin joints remain the
    # fallback for a mesh with no motion set to solve against.
    skin_j = joint_positions(vertices, min_weight=min_weight,
                             transform=transform)
    if joints:
        merged = dict(skin_j)
        merged.update({k: v for k, v in joints.items() if v is not None})
        joints = merged
    else:
        joints = skin_j
    clouds = skin_clouds(vertices, min_weight=min_weight, transform=transform)

    kids: dict = {}
    for b in bones:
        p = parents.get(b)
        if p is not None:
            kids.setdefault(int(p), []).append(b)

    def joint(a, b):
        return joints.get((a, b) if a < b else (b, a))

    def mean(pts):
        return tuple(sum(q[i] for q in pts) / len(pts) for i in range(3))

    placed, unplaced = {}, []
    for b in bones:
        cloud = clouds.get(b) or []
        par = parents.get(b)
        head = joint(b, int(par)) if par is not None else None
        child_js = [j for c in kids.get(b, ())
                    if (j := joint(b, c)) is not None]

        # DO NOT AVERAGE CHILD JOINTS. A bone whose children go in OPPOSING
        # directions collapses: the chest (004) has joints at z=130.1 to the
        # spine below, 148.9 to the neck above, and 143.2 to each shoulder.
        # Their mean is z=141.4, which sits 0.7 units from the chest's own
        # centroid -- so the chest came out as a dot at an arbitrary angle.
        #
        # The chain continues toward the FARTHEST child, so that is the tail.
        if len(child_js) > 1 and head is not None:
            tail = max(child_js, key=lambda j: _d2(j, head))
        elif child_js:
            tail = child_js[0] if head is not None else None
        else:
            tail = None

        if head is None and len(child_js) > 1:
            # A ROOT has no parent joint to anchor its head, so it takes the
            # two most-separated child joints and spans them: 004 becomes
            # 130.1 -> 148.9, a chest bone rather than a dot.
            best = None
            for i, ja in enumerate(child_js):
                for jb in child_js[i + 1:]:
                    d = _d2(ja, jb)
                    if best is None or d > best[0]:
                        best = (d, ja, jb)
            if best is not None:
                _d, ja, jb = best
                # Point AWAY from the heavier half of the skeleton: the end
                # whose child leads to more bones is the body's continuation
                # and belongs at the head, so the bone reads like a spine
                # rather than an upside-down one.
                head, tail = ja, jb
                ca = _subtree_size(parents, _child_at(kids, b, joint, ja))
                cb = _subtree_size(parents, _child_at(kids, b, joint, jb))
                if cb > ca:
                    head, tail = jb, ja
        elif head is None and len(child_js) == 1:
            head = _centroid(cloud) or child_js[0]
            tail = child_js[0]

        if head is None and tail is None:
            got = place_bone(cloud, min_length=min_length)
            if got is None:
                unplaced.append(b)
            else:
                placed[b] = got
            continue

        if head is None:                       # a root: start at its own mass
            head = _centroid(cloud) or tail
        if tail is None:
            # A leaf: run from the joint out through the bone's own mass, as
            # far as that mass actually reaches. A fixed length would make
            # every fingertip the same size as every thigh.
            c = _centroid(cloud)
            if c is None:
                tail = tuple(head[i] + (min_length if i == 2 else 0.0)
                             for i in range(3))
            else:
                d = tuple(c[i] - head[i] for i in range(3))
                n = math.sqrt(sum(x * x for x in d))
                if n < 1e-9:
                    tail = tuple(head[i] + (min_length if i == 2 else 0.0)
                                 for i in range(3))
                else:
                    u = tuple(x / n for x in d)
                    far = max((sum((q[i] - head[i]) * u[i] for i in range(3))
                               for q, _w in cloud), default=n)
                    far = max(far, min_length)
                    tail = tuple(head[i] + u[i] * far for i in range(3))

        if math.dist(head, tail) < min_length:
            tail = tuple(tail[i] + (min_length if i == 2 else 0.0)
                         for i in range(3))
        placed[b] = (tuple(head), tuple(tail))
    return placed, sorted(unplaced)


def place_from_skin(bone_indices, vertices, *, min_length: float = MIN_LENGTH,
                    min_weight: float = MIN_WEIGHT, transform=None):
    """-> ``(placed, unplaced)``.

    `placed` is ``{bone_index: (head, tail)}`` for every bone the skin can
    locate; `unplaced` is the sorted list of the rest.  The caller decides what
    to do with those -- this module will not invent a position for a bone no
    vertex references.
    """
    clouds = skin_clouds(vertices, min_weight=min_weight, transform=transform)
    placed, unplaced = {}, []
    for idx in bone_indices:
        got = place_bone(clouds.get(int(idx)) or [], min_length=min_length)
        if got is None:
            unplaced.append(int(idx))
        else:
            placed[int(idx)] = got
    return placed, sorted(unplaced)
