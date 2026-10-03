"""
C3 -> Blender.

One `.c3` file becomes one collection.  Each PHY chunk in it becomes one mesh
object; every other chunk (CAME, PTCL, SHAP, MNEW, CCFL, ...) is carried
through untouched as an opaque blob on the collection, so an export can rebuild
the whole container and not just the geometry.

MOTI is no longer in that list.  The i-th PHY chunk is animated by the i-th
MOTI chunk (docs/modding.md section 11), and with `import_animation` on that
chunk becomes a real armature action -- see `c3_anim`.  The container blob is
still stashed whole, so a track nobody opened is written back from the original
bytes and not from anything Blender held.

ONE ARMATURE PER MESH, and that is a change with a reason.  The importer used
to build a single armature from the UNION of every mesh's bone palette.  That
was harmless while the rest pose was a placeholder and nothing animated, and it
is not harmless now: bone 3 of mesh 0 and bone 3 of mesh 1 are indices into
DIFFERENT MOTI chunks, so one armature cannot hold both tracks -- the second
action would overwrite the first under the same bone names.  The 792 multi-mesh
containers in this install are exactly the ones that would have been silently
wrong.
"""

import os

import bpy
from mathutils import Matrix, Vector

from . import c3_anim, c3_common as K
from . import c3_material
from .vendor import bonejoint, bonelabel, boneplace, bonetree, c3phy
from .vendor import coassets, motionpool
from .vendor.c3phy import VARIANTS, iter_chunks, parse_phy

MOTI_TAG = b"MOTI"


# --------------------------------------------------------------------------

def _new_attr(me, name, atype, values, field="value"):
    a = me.attributes.get(name)
    if a is None:
        a = me.attributes.new(name, atype, 'POINT')
    a.data.foreach_set(field, values)
    return a


def build_mesh_object(mesh: "c3phy.PhyMesh", index: int, stem: str,
                      opts) -> bpy.types.Object:
    var = VARIANTS[mesh.tag]
    name = f"{stem}_{index}_{mesh.name or 'phy'}"

    me = bpy.data.meshes.new(name)
    verts = [K.to_blender(v.position) for v in mesh.vertices]
    # KEEP the original index order: the single-axis mirror above already
    # flipped handedness, which is what corrects D3D's clockwise winding.
    faces = [tuple(f) for f in mesh.faces]
    me.from_pydata(verts, [], faces)
    me.update()
    # NB: me.validate() is deliberately NOT called.  It deletes degenerate and
    # duplicate triangles, of which the shipped corpus has 244 and 5,838
    # respectively across 186 chunks; dropping them would break round-trip.

    n = len(mesh.vertices)

    # ---- UV sets ---------------------------------------------------------
    uv0 = me.uv_layers.new(name=K.UV0_NAME)
    flat = []
    for lp in me.loops:
        v = mesh.vertices[lp.vertex_index]
        flat += [v.u0, K.flip_v(v.v0)]
    uv0.uv.foreach_set("vector", flat)
    _new_attr(me, K.ATTR_UV0_SRC, 'FLOAT2',
              [c for v in mesh.vertices for c in (v.u0, v.v0)], "vector")

    if var["step"]:                      # only PHY5 carries a second UV set
        uv1 = me.uv_layers.new(name=K.UV1_NAME)
        flat = []
        for lp in me.loops:
            v = mesh.vertices[lp.vertex_index]
            flat += [v.u1, K.flip_v(v.v1)]
        uv1.uv.foreach_set("vector", flat)
        _new_attr(me, K.ATTR_UV1_SRC, 'FLOAT2',
                  [c for v in mesh.vertices for c in (v.u1, v.v1)], "vector")

    # ---- packed RGBA vertex colour --------------------------------------
    cols = []
    for v in mesh.vertices:
        cols.extend(K.unpack_rgba(v.unknown4))
    ca = me.color_attributes.new(K.ATTR_COLOR, 'BYTE_COLOR', 'POINT')
    ca.data.foreach_set("color", cols)
    _new_attr(me, K.ATTR_COLOR_SRC, 'INT',
              [K.as_signed32(v.unknown4) for v in mesh.vertices])

    # ---- skinning: exact source attributes ------------------------------
    _new_attr(me, K.ATTR_BONE0, 'INT', [v.bone0 for v in mesh.vertices])
    _new_attr(me, K.ATTR_BONE1, 'INT', [v.bone1 for v in mesh.vertices])
    _new_attr(me, K.ATTR_W0, 'FLOAT', [v.weight0 for v in mesh.vertices])
    _new_attr(me, K.ATTR_W1, 'FLOAT', [v.weight1 for v in mesh.vertices])

    # ---- normals ---------------------------------------------------------
    if var["has_normal"]:
        _new_attr(me, K.ATTR_NORMAL_SRC, 'FLOAT_VECTOR',
                  [c for v in mesh.vertices
                   for c in K.to_blender(v.normal)], "vector")
        try:
            me.normals_split_custom_set_from_vertices(
                [K.to_blender(v.normal) for v in mesh.vertices])
        except Exception:
            pass
    else:
        # "PHY " and "PHY4" do not store normals; the engine generates them
        # (RVA 0x5A8C0).  Smooth shading is the closest Blender equivalent.
        for p in me.polygons:
            p.use_smooth = True

    ob = bpy.data.objects.new(name, me)

    # ---- the chunk matrix becomes the object transform ------------------
    bm = K.c3_matrix_to_blender(mesh.matrix)
    ob.matrix_basis = Matrix(bm)
    # Read it BACK: Blender stores an object transform as loc/rot/scale and
    # recomposes matrix_basis from them, which is float32 and lossy (a real
    # corpus matrix with a 1.1367e-07 basis element comes back as 1.8327e-07).
    # The export-side "did the user move this object?" test has to compare
    # against what Blender actually holds, not against what we asked for.
    settled = [c for row in ob.matrix_basis for c in row]

    # ---- stash everything the engine reads but does not interpret -------
    ob["c3_tag"] = mesh.tag.decode("latin-1")
    ob["c3_node_name"] = mesh.name
    ob["c3_name_raw"] = K.b64(mesh.name_raw)
    ob["c3_unknown0"] = mesh.unknown0
    ob["c3_vcount_a"] = mesh.vertex_count_a
    ob["c3_vcount_b"] = mesh.vertex_count_b
    ob["c3_fcount_a"] = mesh.face_count_a
    ob["c3_fcount_b"] = mesh.face_count_b
    ob["c3_label_raw"] = K.b64(mesh.label_raw)
    ob["c3_label"] = K.decode_label(mesh.label_raw)
    ob["c3_bbox_a"] = list(mesh.bbox_a or mesh.bbox_min)
    ob["c3_bbox_b"] = list(mesh.bbox_b or mesh.bbox_max)
    ob["c3_matrix"] = list(mesh.matrix or K.IDENTITY16)
    ob["c3_matrix_blender"] = settled
    ob["c3_frame_count"] = mesh.frame_count
    ob["c3_keys"] = K.b64(K.pack_keys(mesh.keys))
    ob["c3_has_step"] = mesh.step is not None
    # STEP is TWO f32 since M8. This comment used to say "two raw u32s ...
    # they are float bit patterns", which was the old reading of the same
    # bytes: the dwords ARE floats, and `c3phy` now reads them as such.
    # `step_to_prop` is the single home for the conversion and is gated
    # outside Blender (`tests/test_blender_step_prop.py`).
    ob["c3_step"] = K.step_to_prop(mesh.step)
    ob["c3_two_sided"] = bool(mesh.two_sided)
    ob["c3_billboard"] = int(mesh.billboard)
    ob["c3_phy5_raw"] = K.b64(mesh.phy5_raw)
    ob["c3_tail_raw"] = K.b64(mesh.tail_raw)
    ob["c3_chunk_index"] = index
    return ob



def _shape_of(stem):
    """The body shape a player mesh is animated by, or None.

    Armour and headgear ids are prefixed by BODY TYPE (001-004), so
    `003188495` is body type 3 and its motion lives under `c3/0003`. Anything
    that is not a 9-digit id beginning 001-004 -- a monster, an NPC, an
    effect -- has no such mapping and gets `None`, which falls the caller back
    to the skin-derived tree.
    """
    if not stem.isdigit() or len(stem) != 9:
        return None
    body = stem[:3]
    return "0" + body if body in ("001", "002", "003", "004") else None


def _motion_dir_for(stem, root):
    """The LOOSE motion directory for a shape, or None -- the fallback only.

    Kept because an install with no archives at all has nothing else to offer
    (`_gather_pool`'s last branch). It is no longer the primary path: reading
    only the loose tree is the defect `core/motionpool.py` documents.
    """
    shape = _shape_of(stem)
    if not root or shape is None:
        return None
    d = os.path.join(root, "c3", shape)
    return d if os.path.isdir(d) else None


def _load_moti(data):
    """`.c3` bytes -> ``(bone_count, frames)`` for its RICHEST MOTI, or None.

    The richest, not the first: a motion file for a player body carries one
    MOTI per PHY slot and the slots run armet, l_weapon, r_weapon, body against
    bone counts of 1, 1, 1, 84. Taking the first would solve the skeleton
    against a one-bone helmet track.
    """
    best = None
    for tag, body in iter_chunks(data):
        if tag != MOTI_TAG:
            continue
        try:
            m = c3_anim.c3anim.parse_moti(bytes(body))
        except Exception:                                # noqa: BLE001
            continue
        if best is None or m.bone_count > best.bone_count:
            best = m
    if best is None:
        return None
    return int(best.bone_count), bonejoint.matrices_by_key(best)


class _Loose:
    """Stands in for `Located` on the archive-less fallback path."""
    source = "loose"


def _loose_io(root):
    """`(locate, read)` for the loose tree, addressed by ROOT-RELATIVE path.

    Relative, so a fallback pool reads the same way an index-resolved one
    does; an absolute path would put the machine's directory layout into a
    report meant to name game assets.

    Separate from `_loose_names` because the second read needs only these two
    -- listing the directory again to fetch a dozen known files would walk the
    whole of `c3/` for nothing.
    """
    def locate(p):
        return _Loose() if os.path.isfile(os.path.join(str(root), p)) else None

    def read(p):
        with open(os.path.join(str(root), p), "rb") as fh:
            return fh.read()
    return locate, read


def _loose_names(root, shape):
    """Every loose `.c3` under `c3/<shape>`, root-relative and sorted."""
    d = os.path.join(str(root), "c3", shape)
    names = []
    if os.path.isdir(d):
        for dirpath, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".c3"):
                    names.append(
                        os.path.relpath(os.path.join(dirpath, f),
                                        str(root)).replace(os.sep, "/"))
    return sorted(names)


#: `(root, shape, bones) -> (clips, off_rig, modal, missing, how)`, where the
#: clips carry their coverage and their key count but NOT their frames.
#:
#: Keyed on the bone list as well, because `Clip.moving` is a set of pairs of
#: THIS mesh's bones; a body and its armour do not always skin the same set.
#: Keyed on the root because two installs legitimately answer differently,
#: which is the whole point of the change.
#:
#: That extra key costs nothing on a player container and the corpus says so:
#: `003188495`, `001188495`, `004137020` and `002133010` are each 4 PHY with
#: bone-set sizes [1, 1, 1, N] -- the armet and the two weapon slots carry ONE
#: bone, so `solve_skeleton`'s `len(bones) < 2` guard returns before reaching
#: here and exactly one chunk per container is ever solved. The cache earns
#: its keep across SEPARATE imports of garments that skin the same set.
_CLIP_CACHE = {}


def _gather_pool(shape, root, pairs, bones_key):
    """Every on-rig candidate for a shape, PROBED AND RELEASED.

    Declared by `ini/3dmotion.ini` (plus its compiled `.dbc` twin) and read
    through `AssetRoot`, so a clip that lives only inside `c3.wdf` is a
    candidate -- on CCO that is 266 of body 0003's 287 present clips,
    including the whole unarmed set. `AssetRoot.bare` is deliberate: this asks
    what THIS install ships, never what a fallback install could supply,
    because a skeleton solved partly from another patch level would be
    unattributable.

    THE FRAMES ARE DROPPED AS EACH CLIP IS READ. A shape's candidate frames
    are ~526 MB of Python tuples on CCO and all four shapes are ~2.1 GB, to
    choose a pool of ~26 MB. What survives here is each clip's coverage set
    and key count; `_pool_frames` re-reads the dozen or so that are selected.

    Falls back to walking the loose directory when the table yields nothing --
    an install with no `ini/3dmotion.ini` and no archive, which is what Zephyr
    measures as. `how` says which happened so the report can too.
    """
    key = (str(root), shape, bones_key)
    if key in _CLIP_CACHE:
        return _CLIP_CACHE[key]
    clips, missing, how = [], [], "index"
    try:
        assets = coassets.AssetRoot.bare(root)
        rows = motionpool.motion_rows(root)
        declared = motionpool.clips_for_shape(rows, shape)
        if declared:
            clips, missing = motionpool.gather_clips(
                declared, assets.locate, assets.read, _load_moti,
                pairs=pairs, keep_frames=False)
    except Exception:                                    # noqa: BLE001
        clips, missing = [], []
    if not clips:
        how = "loose"
        locate, read = _loose_io(root)
        clips, missing = motionpool.gather_clips(
            _loose_names(root, shape), locate, read, _load_moti,
            pairs=pairs, keep_frames=False)
    kept, off_rig, modal = motionpool.on_rig(clips)
    out = (kept, off_rig, modal, missing, how)
    _CLIP_CACHE[key] = out
    return out


def _pool_frames(pool, root, how):
    """Re-read the SELECTED clips' frames. -> True if every one came back.

    The second read is what pays for not holding 526 MB through the selection.
    A clip that vanished between the two reads is a changed install mid-import,
    so the solve is abandoned rather than run on a pool that is not the one
    that was chosen and reported.
    """
    if how == "loose":
        locate, read = _loose_io(root)
    else:
        assets = coassets.AssetRoot.bare(root)
        locate, read = assets.locate, assets.read
    for c in pool.clips:
        if locate(c.logical) is None:
            return False
        got = _load_moti(read(c.logical))
        if got is None or len(got[1]) != c.key_count:
            return False
        c.frames = got[1]
    return True


def solve_skeleton(mesh, stem, opts, report):
    """-> ``(parents, joints)`` solved from the shared motion sets.

    ``({}, {})`` when there is nothing to solve against, which is the normal
    answer for a monster, an NPC, or an effect.

    WHAT CHANGED, AND WHY THE OLD VERSION COULD BE RIGHT BY LUCK
    -----------------------------------------------------------
    This used to `os.walk` the loose `c3/000N` directory and stack the first
    ten files' frames. Both halves were wrong and the failure was invisible:

    * The pool was whatever the install left unpacked. `core/motionpool.py`
      has the census -- on CCO body 0003, 21 loose clips against 266 that
      exist only inside `c3.wdf`, and the other way round on 5517.
    * A POOLED residual is one RMS over every frame against ONE point and the
      cut is absolute, so it grows as clips are stacked until a pair every
      clip calls a clean joint is rejected. `bonejoint.solve_from_clips` has
      that measurement.

    **On CCO body 0003 the old pool got the fingers RIGHT**, because the loose
    weapon sets happen to include attacks that bend them. That is luck, not
    correctness: the same code on the unarmed set -- the first thing an
    index-ordered enumeration reaches -- rejects 11-12, 11-14 and 19-22 as
    rigid and attaches 10-12, 10-14 and 18-22 instead, every one at residual
    0.0000. Reaching more clips WITHOUT choosing them for coverage would have
    turned a lucky right answer into a confident wrong one, which is why the
    two changes ship together and not one at a time.
    """
    shape = _shape_of(stem)
    root = opts.get("game_root") or ""
    if shape is None or not root:
        return {}, {}
    bones = sorted(set(mesh.bones))
    if len(bones) < 2:
        return {}, {}
    pairs = [(a, b) for i, a in enumerate(bones) for b in bones[i + 1:]]
    pool = None
    try:
        clips, off_rig, modal, missing, how = _gather_pool(
            shape, root, pairs, tuple(bones))
        if not clips:
            return {}, {}
        pool = motionpool.select_pool(clips, bones)
        pool.modal_bone_count = modal
        pool.off_rig = off_rig
        pool.missing = missing
        if not pool.clips or not _pool_frames(pool, root, how):
            return {}, {}
        # Priors in the space the MOTI matrices operate in, which is the
        # mesh's positions AFTER the chunk matrix -- not the raw ones. They
        # resolve only the axis a hinge leaves free; see
        # `bonejoint.solve_joint`.
        #
        # FROM #166 (`30b3113b`), AND THIS BRANCH HAD IT WRONG: it carried
        # the old comment claiming raw positions were that space. They are,
        # for the 331 of 490 skinned `v_body` chunks in this install whose
        # chunk matrix is the identity, and for no other -- `Phy_Load`
        # applies that matrix before a bone matrix ever sees a vertex (RVA
        # 0x5A735, and `tools/attach.py`'s `world_vertices` composes the
        # two in that order), so a raw position is one transform short.
        #
        # MEASURED there on `c3/mesh/002188495.c3`, whose matrix is a
        # 90-degree Y/Z swap with scale 0.5838 and translation z=-156.8:
        # the elbow (9-10) solved to z=-0.7 -- the floor -- and the right
        # knee (42-43) to x=-23.6, across the body, with ALL 23 solved
        # joints landing OUTSIDE the skin of the two bones they join, by
        # up to 234 units. The residuals stayed 0.0000 either way and were
        # right to, so no residual check could have caught it -- which is
        # also why NOTHING IN THIS BRANCH'S OWN GATES would have: every
        # one of them is phrased on a residual, a vote or a tree.
        #
        # `apply_matrix_copy` rather than a local multiply: the row-vector
        # convention already has two homes in `c3phy` and a third spelling
        # here would be a third chance to transpose it. The copy is ~0.5 ms
        # against a solve that has just read and probed ~290 clips.
        cent = bonetree.bone_centroids(c3phy.apply_matrix_copy(mesh).vertices)
        graph, census = bonejoint.solve_from_clips(
            [c.frames for c in pool.clips], bones, centroids=cent)
    except Exception:                                    # noqa: BLE001
        return {}, {}
    finally:
        # The cached clips are SHARED with the next mesh of this shape, so the
        # frames this solve attached must not outlive it -- that is the 526 MB
        # the two-pass read exists to avoid, let back in through the side door
        # if a solve leaves them hanging off the cache.
        for c in (pool.clips if pool is not None else ()):
            c.frames = []
    if not graph:
        return {}, {}
    parents = bonejoint.tree_from_joints(bones, graph)
    joints = {k: v[1] for k, v in graph.items()}
    roots = sum(1 for p in parents.values() if p is None)
    report.append(
        f"{stem}: skeleton SOLVED from {len(pool.clips)} motion file(s), "
        f"{pool.frame_count} frame(s) -- {len(graph)} articulated pair(s), "
        f"{roots} root(s)")
    # PROVENANCE, because "solved from 16 files" does not say WHICH 16 and the
    # answer depends on them. `digest` identifies the pool in one token so two
    # runs, or two installs, can be compared without re-listing it; the counts
    # say how much of the shape's declared motion this install could offer.
    by_src = {}
    for c in pool.clips:
        by_src[c.source] = by_src.get(c.source, 0) + 1
    report.append(
        f"{stem}: pool {pool.digest()} via {how} -- "
        + ", ".join(f"{n} from {s}" for s, n in sorted(by_src.items()))
        + f"; {pool.considered} on-rig candidate(s) at {modal} bone(s)"
        # Weapon set AND action, not just the basename: every shape numbers its
        # actions the same way, so `305.c3=85, 305.c3=85` names two different
        # clips identically and reads as a duplicated line.
        + (f", {len(off_rig)} off-rig dropped ("
           + ", ".join("%s=%d" % ("/".join(p.replace(os.sep, "/")
                                           .split("/")[-2:]), bc)
                       for p, bc in off_rig[:4])
           + ")" if off_rig else "")
        + (f", {len(missing)} declared but not shipped" if missing else ""))
    for c in pool.clips:
        report.append(f"{stem}:   {c.logical} [{c.source}] "
                      f"{c.key_count} frame(s)")
    # UNRESOLVED IS NOT "NOT A JOINT". A pair every clip holds rigid is a pair
    # nothing was measured about, and `docs/motion_policy_2026-09-21.md`
    # section 2 says 30 of body 0003's bones are WELDED and will read that way
    # in every clip ever authored. Naming them is the difference between a
    # coverage figure and a silent gap.
    unres = bonejoint.unresolved_pairs(census)
    if unres or pool.uncovered:
        shown = unres[:8]
        report.append(
            f"{stem}: {len(unres)} pair(s) UNRESOLVED -- rigid in every "
            f"pooled clip, so neither jointed nor rejected"
            + (": " + ", ".join(f"{a}-{b}" for a, b in shown)
               + (" ..." if len(unres) > len(shown) else "") if shown else "")
            + (f"; {len(pool.uncovered)} of them no candidate clip in this "
               f"install articulates at all" if pool.uncovered else ""))
    # Labelled here only to REPORT the count; `build_armature` derives them
    # again from the same inputs. Duplicating a few hundred float comparisons
    # is cheaper than threading a third value through two call sites, and it
    # keeps the count beside the solve it describes -- a coverage figure that
    # names what it missed is the acceptance criterion for phase 1a, so it
    # belongs in the user's report and not only in a test.
    lab = bonelabel.label_tree(parents, joints)
    got, total, missing = bonelabel.coverage(lab, bones)
    report.append(
        f"{stem}: labelled {got}/{total} bone(s)"
        + (f" -- unlabelled: "
           + ", ".join(K.BONE_FMT % b for b in missing) if missing else ""))
    return parents, joints

#: Which collection a label belongs in.  `sockets` is deliberately ABSENT:
#: identifying a socket needs the rigid-weld measurement in
#: `docs/motion_policy_2026-09-21.md`, not a name, and an empty collection
#: called "sockets" would read as "this body has none" rather than "this
#: importer does not compute them yet".
_GROUPS = (
    ("arm.R", ("shoulder.R", "upper_arm.R", "forearm.R", "hand.R", "finger")),
    ("arm.L", ("shoulder.L", "upper_arm.L", "forearm.L", "hand.L", "finger")),
    ("leg.R", ("thigh.R", "shin.R", "foot.R", "toe.R")),
    ("leg.L", ("thigh.L", "shin.L", "foot.L", "toe.L")),
    ("head", ("neck", "head")),
    ("spine", ("chest", "spine_", "pelvis")),
)


def _group_of(label):
    if not label:
        return None
    for name, keys in _GROUPS:
        if name.endswith((".R", ".L")) and not label.endswith(name[-2:]):
            continue
        for k in keys:
            if label.startswith(k):
                return name
    return None


def _group_bones(arm, labels, placed):
    """Put each named bone in a bone collection, so a person can find a limb.

    Blender 4.x only; `arm.collections` does not exist before it, and a
    failure to group must never cost the import -- the names are already on
    the bones either way.
    """
    if not labels or not hasattr(arm, "collections"):
        return
    want = {}
    for idx, label in labels.items():
        g = _group_of(label)
        if g:
            want.setdefault(g, []).append(int(idx))
    unused = [int(b) for b in placed or ()
              if not _group_of(labels.get(int(b)))]
    if unused:
        want["unused"] = unused
    for name, idxs in want.items():
        try:
            coll = arm.collections.get(name) or arm.collections.new(name)
            for i in idxs:
                bone = arm.bones.get(K.BONE_FMT % i)
                if bone is not None:
                    coll.assign(bone)
        except Exception:                            # noqa: BLE001
            return

def build_armature(all_bones, name: str, opts, mesh=None,
                   solved=None) -> bpy.types.Object:
    """A skeleton for one mesh's bone palette (plus its MOTI's bone range).

    The palette is not stored in the file -- `Phy_Load` derives it (RVA
    0x5A1A0) as the sorted unique set of every `boneIndex0`, plus `boneIndex1`
    wherever `weight1 != 0`.  `PhyMesh.bones` reproduces that.  When a motion
    track is imported the caller widens it to every index the MOTI declares, so
    each animated bone has somewhere to live.

    A MOTI matrix is a *skinning* matrix, so the rest pose it implies is the
    identity for every bone -- which would stack them all on the origin,
    unselectable and unpaintable.  `c3anim` conjugates each pose by the bone's
    own `matrix_local` (``B = L^-1 . M . L``), which makes the DEFORMATION
    exactly the engine's **for any rest pose at all**.  In rest pose an armature
    modifier is still the identity wherever the bones sit, so the geometry round
    trip is untouched whatever we choose here.

    THAT FREEDOM IS WHY THERE ARE TWO MODES, and why the default changed.

    ``'SKIN'`` (default) asks `boneplace` where each bone's weighted vertices
    actually are, so a forearm bone lies along the forearm.  Bones no vertex
    references cannot be placed from anything and are NOT guessed at: they are
    parked in a tight ladder off to one side and hidden, which is what makes
    the placed ones selectable.  On `003188495`'s body that is 59 of 84 hidden
    and 25 placed.

    ``'LADDER'`` is the previous behaviour, kept because it is the only mode
    that is defined when a mesh has no skin to read -- and because a placement
    derived from data is a thing a user may want to opt out of.

    Placing here, BEFORE `import_motion` runs, is what keeps an export
    byte-exact: `import_motion` stashes `PROP_REST` from the rest pose it finds
    and `export_motion` reuses the source keys verbatim while the live rest
    still matches that stash.  Moving bones afterwards invalidates it and
    re-bakes every key instead.
    """
    arm = bpy.data.armatures.new(name)
    ao = bpy.data.objects.new(name, arm)
    bpy.context.collection.objects.link(ao)

    placed, unplaced, tree = {}, list(all_bones), {}
    mode = str(opts.get("bone_placement", "SKIN")).upper()
    if mode == "SKIN" and mesh is not None:
        try:
            # THE BONES MUST LAND IN THE SAME SPACE AS THE GEOMETRY, and a PHY
            # position is two steps away from it.  `build_mesh_object` builds
            # the mesh through `K.to_blender` (which NEGATES Z) and then puts
            # the chunk's own matrix on the object as its transform, while the
            # armature stays at the identity and the mesh is parented to it.
            # So a vertex sits at `chunk_matrix @ to_blender(position)` in
            # armature space.  Placing from raw positions put every bone
            # mirrored in Z and unrotated -- below the mesh and facing wrong.
            bm = Matrix(K.c3_matrix_to_blender(mesh.matrix))

            def _to_arm(p, _m=bm):
                return tuple(_m @ Vector(K.to_blender(p)))

            # THE TREE COMES FIRST, because the placement uses it. A bone
            # runs from the joint with its PARENT to the joint with its
            # CHILDREN, and a joint is where the skin blends two bones
            # together -- measured as a far better estimator than the
            # principal axis of a bone's whole cloud, which is a horizontal
            # chord across the body for anything that is not a limb.
            if opts.get("bone_hierarchy", True):
                sp, sj = solved or ({}, {})
                labels = {}
                if sp:
                    # SOLVED wins. Its edges are measured articulations, not
                    # blend-weight proxies, and it reaches joints the skin
                    # cannot see across a seam.
                    tree = dict(sp)
                    for b in all_bones:
                        tree.setdefault(int(b), None)
                    # `sj` IS ALREADY POST-CHUNK-MATRIX -- `solve_skeleton`
                    # solves it against centroids that went through the
                    # chunk matrix -- so it must NOT go through `_to_arm`,
                    # which would apply that matrix a SECOND time.
                    #
                    # `bm @ to_blender(p)` is exactly `to_blender(p * M)`:
                    # `c3_matrix_to_blender` builds `S*transpose(M)*S` with
                    # translation `S*t`, and `to_blender` is that same `S`
                    # with `S*S == I`. Not merely close -- bit-identical on
                    # all 923 vertices of `002188495`. So the armature-space
                    # position of a point that ALREADY carries the matrix is
                    # just `to_blender` of it.
                    #
                    # MEASURED on `002188495`: through `_to_arm`, ALL 23
                    # solved joints land outside the armature-space skin of
                    # the two bones they join, by up to 136.61 (pair 44-45);
                    # through `to_blender`, all 23 land inside it. On the
                    # identity-matrix body both spellings agree at 0.00.
                    #
                    # This is not a tolerable offset: `place_from_tree` MERGES
                    # these into one dict with `joint_positions`' skin joints,
                    # which are armature-space by construction, so a solved
                    # joint in the other space puts two coordinate systems in
                    # a single dict and the bones placed from it are wrong
                    # wherever the chunk matrix is not the identity.
                    joints = {k: K.to_blender(v) for k, v in sj.items()}
                    # Labels are derived in the MESH'S OWN SPACE, from `sj`
                    # itself rather than the converted copy, because the
                    # classifier's one geometric assumption is mirror
                    # symmetry across a LATERAL AXIS -- and which axis that
                    # is survives only as long as nobody swizzles the
                    # coordinates. `to_blender` negates Z and the object
                    # matrix rotates again; deriving names downstream of
                    # both would make the side of a bone depend on the
                    # chunk's transform.
                    labels = bonelabel.label_tree(sp, sj)
                else:
                    tree = bonetree.build_tree(all_bones, mesh.vertices,
                                               transform=_to_arm)
                    joints = None
                placed, unplaced = boneplace.place_from_tree(
                    all_bones, mesh.vertices, tree, transform=_to_arm,
                    joints=joints)
            else:
                placed, unplaced = boneplace.place_from_skin(
                    all_bones, mesh.vertices, transform=_to_arm)
        except Exception:                            # noqa: BLE001
            # A placement that cannot be computed must not cost the user their
            # import; the ladder is always available and always valid. The
            # tree goes with it -- parenting bones that were not placed from
            # the skin would be a hierarchy over a ladder, which is worse than
            # no hierarchy because it LOOKS derived.
            placed, unplaced, tree = {}, list(all_bones), {}

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = ao
    bpy.ops.object.mode_set(mode='EDIT')
    step = max(opts.get("bone_spacing", 4.0), 0.01)
    park = _park_origin(placed, step, tree)
    for i, idx in enumerate(all_bones):
        b = arm.edit_bones.new(K.BONE_FMT % idx)
        got = placed.get(idx)
        if got is not None:
            b.head, b.tail = got
        elif placed:
            # Unplaceable, but SOME bone was placed -- park it compactly beside
            # the mesh rather than at the origin, where it would sit inside the
            # geometry the user is trying to look at.
            j = unplaced.index(idx)
            b.head = (park[0], park[1], park[2] + j * boneplace.MIN_LENGTH * 2)
            b.tail = (park[0], park[1],
                      park[2] + j * boneplace.MIN_LENGTH * 2
                      + boneplace.MIN_LENGTH)
        else:
            b.head = (0.0, 0.0, i * step)
            b.tail = (0.0, 0.0, i * step + step * 0.8)

    # ---- the hierarchy, DERIVED from the same skin -----------------------
    # A .c3 stores no parent indices; a vertex blended between two bones sits
    # at the JOINT between them, so the two-influence skin is an adjacency
    # graph and the tree is its maximum-weight spanning forest. Applied here,
    # in edit mode and BEFORE `import_motion` runs, because the conjugation
    # that follows reads the live tree and stashes it.
    #
    # `use_connect` stays False: connecting would drag each bone's head onto
    # its parent's tail and throw away the placement we just derived.
    if tree:
        for idx in bonetree.order_root_first(tree):
            par = tree.get(idx)
            if par is None:
                continue
            child = arm.edit_bones.get(K.BONE_FMT % idx)
            parent = arm.edit_bones.get(K.BONE_FMT % par)
            if child is None or parent is None or child is parent:
                continue
            child.use_connect = False
            child.parent = parent
    bpy.ops.object.mode_set(mode='OBJECT')
    for idx in all_bones:
        bone = arm.bones.get(K.BONE_FMT % idx)
        if bone is not None:
            bone[K.BONE_INDEX_PROP] = idx
            if labels.get(int(idx)):
                bone[K.BONE_LABEL_PROP] = labels[int(idx)]
            # Hidden only when something else WAS placed: hiding every bone of
            # a fully unplaceable armature would leave nothing to select.
            bone.hide = bool(placed) and idx not in placed
    _group_bones(arm, labels, placed)
    ao["c3_bone_labels"] = [labels.get(int(b), "") for b in all_bones]
    ao["c3_bone_palette"] = list(all_bones)
    ao["c3_placeholder_rest_pose"] = not placed
    ao["c3_bones_placed"] = sorted(placed)
    ao["c3_bone_parents"] = [(-1 if tree.get(b) is None else int(tree[b]))
                             for b in all_bones] if tree else []
    ao["c3_bones_unplaced"] = list(unplaced) if placed else []
    bpy.context.view_layer.objects.active = prev_active
    return ao


def _park_origin(placed, step, tree=None):
    """Where to stand the unplaceable bones: AT THE ROOT'S HEAD.

    They used to go one `step` clear of the widest placed extent, at the
    lowest placed height -- which is a spot on the FLOOR beside the model,
    and the owner reported exactly that: "a list of bones that exist on the
    floor". They are hidden, but a hidden bone that reappears the moment
    anything unhides it should not be somewhere baffling.

    Inside the torso they are invisible when hidden and unremarkable when
    not. Nothing depends on the position: these are bones the MOTI declares
    and the skin never references, so they deform nothing.
    """
    if not placed:
        return (0.0, 0.0, 0.0)
    if tree:
        roots = [b for b, par in tree.items() if par is None and b in placed]
        if roots:
            return tuple(placed[sorted(roots)[0]][0])
    xs = [p[i] for head, tail in placed.values() for p in (head, tail)
          for i in (0,)]
    ys = [p[1] for head, tail in placed.values() for p in (head, tail)]
    zs = [p[2] for head, tail in placed.values() for p in (head, tail)]
    return (sum(xs) / len(xs), sum(ys) / len(ys),
            (min(zs) + max(zs)) * 0.5)


def apply_bone_naming(arm_ob, ob, opts, report):
    """Rename bones to their labels -- AND the vertex groups with them.

    **Renaming a bone does not move the vertex group that drives it, and the
    Armature modifier binds group to bone BY NAME.** So renaming bones alone
    silently unbinds the skin: the mesh keeps its weights, the armature keeps
    its bones, and nothing deforms. Both names move together or neither does.

    Safe whichever way Blender behaves: the group is looked up by its OLD name
    BEFORE the bone is renamed, so if Blender syncs the name itself the
    assignment is a no-op, and if it does not, this is the thing that does it.

    Only bones the classifier could name are touched. An unlabelled bone keeps
    `bone_%03d`, which is information -- it says the geometry did not support
    a name, and inventing one would be worse than leaving the index.

    None of this is what the pipeline resolves on. `c3_bone_index` is, on both
    the read and the write side, which is what makes renaming safe at all.
    """
    if arm_ob is None:
        return
    if str(opts.get("bone_naming", "LABEL")).upper() != "LABEL":
        return
    used = {b.name for b in arm_ob.data.bones}
    n_renamed = 0
    for b in list(arm_ob.data.bones):
        label = b.get(K.BONE_LABEL_PROP)
        if not label:
            continue
        want = str(label)
        suffix = 2
        while want in used and want != b.name:
            want = "%s_%d" % (label, suffix)
            suffix += 1
        old = b.name
        if want == old:
            continue
        vg = ob.vertex_groups.get(old) if ob is not None else None
        b.name = want
        used.discard(old)
        used.add(want)
        if vg is not None:
            vg.name = want
        n_renamed += 1
    if n_renamed:
        report.append(f"{arm_ob.name}: renamed {n_renamed} bone(s) to their "
                      f"anatomical labels (vertex groups moved with them)")


def attach_skin(ob, mesh, arm_ob):
    """Create the editable vertex-group view of the exact skin attributes."""
    for idx in mesh.bones:
        ob.vertex_groups.new(name=K.BONE_FMT % idx)
    groups = {vg.name: vg for vg in ob.vertex_groups}
    for i, v in enumerate(mesh.vertices):
        g0 = groups.get(K.BONE_FMT % v.bone0)
        if g0 is not None:
            g0.add([i], v.weight0, 'REPLACE')
        # Slot 1 only exists as a distinct group when it names a different
        # bone; `bone0 == bone1` on 27% of corpus vertices and Blender cannot
        # hold the same group twice.  The exact slots live in c3_bone0/1.
        if v.bone1 != v.bone0:
            g1 = groups.get(K.BONE_FMT % v.bone1)
            if g1 is not None:
                g1.add([i], v.weight1, 'REPLACE')
    if arm_ob is not None:
        md = ob.modifiers.new("Armature", 'ARMATURE')
        md.object = arm_ob
        ob.parent = arm_ob


# --------------------------------------------------------------------------

def import_c3(context, filepath, **opts):
    """Import one .c3.  Returns (collection, [objects], report_lines)."""
    filepath = str(filepath)
    with open(filepath, "rb") as fh:
        data = fh.read()

    stem = os.path.splitext(os.path.basename(filepath))[0]
    chunks = list(iter_chunks(data))
    phy = [(i, tag, body) for i, (tag, body) in enumerate(chunks)
           if tag in VARIANTS]
    if not phy:
        raise RuntimeError(f"{stem}.c3 contains no PHY chunk "
                           f"(tags: {sorted({t.decode('latin-1') for t, _ in chunks})})")

    coll = bpy.data.collections.new(stem)
    context.scene.collection.children.link(coll)
    coll["c3_source"] = filepath
    coll["c3_container"] = K.b64(data)
    coll["c3_phy_count"] = len(phy)

    prev = context.view_layer.active_layer_collection
    lc = context.view_layer.layer_collection.children.get(coll.name)
    if lc is not None:
        context.view_layer.active_layer_collection = lc

    meshes = []
    for order, (chunk_i, tag, body) in enumerate(phy):
        meshes.append((order, parse_phy(tag, body)))

    # The i-th PHY is animated by the i-th MOTI -- established both in
    # `graphic.dll!MeshCreate` and over the corpus; see the derivation above
    # `c3write._slot_plan`.  Only the order WITHIN each tag matters, so this
    # filter is the pairing.
    moti_bodies = [b for t, b in chunks if t == MOTI_TAG]
    want_arm = opts.get("import_armature", True)
    want_anim = opts.get("import_animation", True) and want_arm

    objs = []
    report = []
    for order, m in meshes:
        ob = build_mesh_object(m, order, stem, opts)
        coll.objects.link(ob)

        arm_ob = None
        if want_arm:
            bones = set(m.bones)
            body = moti_bodies[order] if order < len(moti_bodies) else None
            if want_anim and body is not None:
                try:
                    n = c3_anim.moti_bone_count(body)
                except Exception as e:              # noqa: BLE001
                    report.append(f"MOTI {order}: {e}")
                    body = None
                else:
                    bones |= set(range(n))
            if bones:
                # Solve the real skeleton from the shared motion sets before
                # building anything: a .c3's own MOTI has ONE key, and with
                # one frame every bone pair solves perfectly, so the mesh
                # alone cannot tell an articulation from a coincidence.
                solved_p, solved_j = ({}, {})
                if (opts.get("bone_hierarchy", True)
                        and str(opts.get("bone_placement", "SKIN")).upper()
                        == "SKIN"):
                    solved_p, solved_j = solve_skeleton(m, stem, opts, report)
                arm_ob = build_armature(sorted(bones),
                                        f"{stem}_{order}_skeleton", opts,
                                        mesh=m, solved=(solved_p, solved_j))
                arm_ob[c3_anim.PROP_INDEX] = order
                # SAY WHICH PLACEMENT RAN.  Installing an addon over a loaded
                # one leaves the old modules in memory until Blender restarts,
                # and the symptom of that -- bones still in a ladder -- looks
                # exactly like the placement having failed.  One report line
                # separates "did not reload" from "could not place".
                _np = len(arm_ob.get("c3_bones_placed", ()))
                _nu = len(arm_ob.get("c3_bones_unplaced", ()))
                report.append(
                    f"{stem}_{order}_skeleton: {_np} bone(s) placed from the "
                    f"skin, {_nu} hidden" if _np else
                    f"{stem}_{order}_skeleton: ladder, {len(bones)} bone(s) "
                    f"-- no bone could be placed from the skin")
                if want_anim and body is not None:
                    try:
                        c3_anim.import_motion(
                            context, arm_ob, body,
                            f"{stem}_{order}_motion", report)
                    except Exception as e:          # noqa: BLE001
                        # An animation that will not decode must not cost the
                        # user their mesh: the container blob is still stashed,
                        # so the export writes the original track back.
                        report.append(f"MOTI {order}: {e}")
                        for p in c3_anim.PROPS + c3_anim.PROPS_INFO:
                            if p in arm_ob:
                                del arm_ob[p]
            attach_skin(ob, m, arm_ob)
            # AFTER the skin is attached, never before: the
            # vertex groups do not exist until `attach_skin`
            # makes them, and they have to be renamed in the
            # same breath as the bones.
            apply_bone_naming(arm_ob, ob, opts, report)
        if opts.get("import_materials", True):
            try:
                c3_material.assign_material(ob, m, filepath, opts)
            except Exception as e:            # never fail an import on this
                report.append(f"material for {m.name!r}: {e}")
        objs.append(ob)
        report.append(
            f"{m.tag.decode('latin-1')} {m.name!r}: {m.vertex_count} verts "
            f"({m.vertex_count_a}+{m.vertex_count_b}), {m.face_count} tris, "
            f"{len(m.bones)} bones")

    if lc is not None:
        context.view_layer.active_layer_collection = prev
    return coll, objs, report
