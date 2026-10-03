"""MOTI <-> Blender armature action -- the `bpy` half, and only the `bpy` half.

Every line of format knowledge, matrix algebra and byte-exactness logic is in
`vendor/c3anim.py` (source: `tools/c3anim.py`), which imports no `bpy` and is
gated headlessly by `tests/test_moti_action.py` -- 16,600 MOTI chunks, all four
encodings, 100% byte-exact, MEASURED 2026-09-06.  **Blender cannot be launched
on this rig**, so the split is not a style preference: what lives here is what
could not be tested, and it is kept to reading and writing pose values.

WHAT THIS FILE IS ALLOWED TO KNOW
---------------------------------
  * how to make an action out of (frames, per-bone loc/quat/scale)
  * how to read those values back out of Blender at a given frame
  * where the ID properties live -- `PROPS`, the five that carry the round
    trip, plus the two read-only `PROPS_INFO` ones the N-panel shows

It must NOT know what a ZKEY is, how a matrix is converted, or when a source
tuple may be reused.  If a change here needs one of those, it belongs in
`c3anim` where a test can reach it.

THE READ-BACK, AND WHY IT IS NOT PARANOIA
-----------------------------------------
`c3_import` already learned this once for the chunk matrix (see the comment in
`build_mesh_object`): Blender stores a pose bone's channels as float32 and
recomposes them, so what you asked for and what it holds are different numbers.
The exporter's "did the user touch this?" test compares against what Blender
ACTUALLY HOLDS -- read back through `frame_set` right after the keys are
inserted -- not against what the importer computed.  Comparing against the
computed values would mark every bone key as edited and silently re-bake the
whole install's animation on the first export.

Frames are read through `scene.frame_set` rather than the fcurve API on
purpose: `action.fcurves` moved under slots and channelbags in Blender 4.4, and
evaluating the pose is the one route that has meant the same thing since 2.8.
`_iter_fcurves` is the exception -- discovering keys the USER added needs the
curve list -- and it tries both shapes and degrades to "no new frames" with a
warning rather than raising.
"""

from mathutils import Matrix

from . import c3_common as K
from .vendor import c3anim

#: on the armature object
PROP_SRC = "c3_moti_src"          # base64 of the original MOTI chunk body
PROP_FRAMES = "c3_moti_frames"    # the source key frames, in order
PROP_VIEW = "c3_moti_view"        # settled loc/quat/scale, 10 per (key, bone)
PROP_REST = "c3_moti_rest"        # settled bone.matrix_local, 16 per bone
PROP_INDEX = "c3_moti_index"      # which PHY/MOTI slot this armature animates
#: Everything `import_motion` writes, so a failed import can take all of it
#: back off in one line.  A HALF-written record is the dangerous state: the
#: exporter would read a source blob with no view beside it and treat every
#: bone key as edited.
#: The bone hierarchy this track was conjugated against, flat and aligned to
#: bone index, -1 for a root. STASHED RATHER THAN RE-DERIVED: the importer
#: conjugates by the PARENT's matrix and the exporter undoes that walking
#: root-first, so both ends must use the SAME tree. Re-deriving at export
#: would re-run the rule against a mesh the user may have edited, and a tree
#: that drifts between the two ends reconstructs silently wrong matrices.
#: Absent on anything imported before parenting existed, which reads as flat.
PROP_PARENTS = "c3_moti_parents"

PROPS = (PROP_SRC, PROP_FRAMES, PROP_VIEW, PROP_REST, PROP_PARENTS)
#: Read-only, for the N-panel.  NOT part of the round trip -- the encoding that
#: gets written is the one in the stashed chunk, never one of these.
PROP_ENC = "c3_moti_encoding"
PROP_FRAMECOUNT = "c3_moti_frame_count"
PROPS_INFO = (PROP_ENC, PROP_FRAMECOUNT)

CHANNELS = (("location", 3), ("rotation_quaternion", 4), ("scale", 3))


def bone_names(bone_count):
    """The names the IMPORTER creates. Not a lookup key -- see `bones_by_index`."""
    return [K.BONE_FMT % i for i in range(bone_count)]


def bones_by_index(arm_ob, bone_count):
    """``[bone_or_None]`` for MOTI bones ``0..n-1``, resolved BY INDEX.

    **Every lookup in this module used to go through `bone_names()`**, which
    means a user who renames a bone -- exactly what retargeting requires, and
    what every retargeter does -- silently loses it. Silently is the operative
    word, and it is why this function exists rather than a bare `.get()`:

    * `_pose_bones` returned ``None``, which a caller can at least notice;
    * `live_parents` returned every parent ``None``, reading as a FLAT
      skeleton rather than a missing one;
    * `rest_locals` substituted ``Matrix.Identity(4)`` per miss, so a fully
      renamed armature produced a complete, plausible, entirely wrong rest
      pose and exported from it.

    `c3_bone_index` is written on every bone at import and is what the export
    path already trusts (`c3_export._bone_index`), so this makes the read side
    agree with the write side instead of being the one place still keyed on a
    label the user is invited to change.

    Built ONCE per call -- the callers loop over 84 bones and `data.bones` is a
    linear search, so a per-bone lookup is quadratic on the hot path.

    **The name is still the fallback, and deliberately:** a `.blend` saved
    before `c3_bone_index` existed has correct `bone_%03d` names and no
    property, and must keep working. Falling back cannot resurrect the bug --
    a renamed bone has no matching name either, so the fallback simply also
    misses and the caller sees the same ``None`` it would have seen.
    """
    n = int(bone_count)
    out = [None] * n
    for b in arm_ob.data.bones:
        idx = b.get(K.BONE_INDEX_PROP)
        if idx is None:
            continue
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and out[i] is None:
            out[i] = b
    if any(x is None for x in out):
        for i in range(n):
            if out[i] is None:
                out[i] = arm_ob.data.bones.get(K.BONE_FMT % i)
    return out


def moti_bone_count(body) -> int:
    """How many bones a MOTI chunk declares.

    The importer needs this BEFORE it builds the armature: a mesh's vertex
    palette is only the bones it is weighted to, and the track can name more.
    """
    return int(c3anim.parse_moti(bytes(body)).bone_count)


def _pose_bones(arm_ob, bone_count):
    """-> [pose_bone] for MOTI bones 0..n-1, or None if one is missing.

    `pose.bones` is keyed by name in Blender's API whatever we do, so the
    bone is found BY INDEX first and its current name is then used to reach
    the pose bone -- which is correct under any rename, because we ask the
    bone what it is called now rather than what it was called at import.
    """
    bones = bones_by_index(arm_ob, bone_count)
    out = []
    for b in bones:
        if b is None:
            return None
        pb = arm_ob.pose.bones.get(b.name)
        if pb is None:
            return None
        out.append(pb)
    return out



def live_parents(arm_ob, bone_count):
    """``{bone: parent_or_None}`` read from the ARMATURE AS IT STANDS NOW.

    The live tree, not the stashed one, because a user who re-parents a bone
    by hand has made an edit and it must reach the export. The stash is the
    "unchanged?" comparison, not the source of truth -- the same division
    `PROP_REST` already has against the live rest pose.
    """
    bones = bones_by_index(arm_ob, bone_count)
    out = {}
    for i, b in enumerate(bones):
        par = None
        if b is not None and b.parent is not None:
            idx = b.parent.get(K.BONE_INDEX_PROP)
            if idx is None:
                # A bone parented to something outside the palette cannot be
                # expressed as a MOTI relationship; treat it as a root rather
                # than guessing an index.
                idx = None
            par = None if idx is None else int(idx)
        out[i] = par if (par is None or 0 <= par < bone_count) else None
    return out

def rest_locals(arm_ob, bone_count):
    """The flat `bone.matrix_local` list `c3anim` compares rest poses in.

    **The identity substitution below is why this had to move.** A missed
    lookup does not raise and does not return a short list -- it contributes
    a perfectly well-formed identity matrix, so a fully renamed armature
    yielded 84 identities: a complete, plausible rest pose that is wrong in
    every entry, which `c3anim` would then compare against the stash, find
    changed, and re-bake every key from. Resolving by index removes the miss;
    the substitution stays for a bone that genuinely is absent.
    """
    flat = []
    for b in bones_by_index(arm_ob, bone_count):
        m = b.matrix_local if b is not None else Matrix.Identity(4)
        flat += [float(v) for row in m for v in row]
    return flat


def _nested_rest(flat, bone_count):
    return [tuple(tuple(flat[b * 16 + r * 4 + c] for c in range(4))
                  for r in range(4)) for b in range(bone_count)]


def _read_view(context, arm_ob, frames, bone_count):
    """Sample the armature's evaluated pose at each frame -> the flat view.

    This is the only measurement in the file, and both sides of the
    byte-exactness comparison come through it -- the importer records what it
    reads here, the exporter re-reads it the same way.  A discrepancy between
    the two would therefore have to be a real edit.
    """
    scene = context.scene
    keep = scene.frame_current
    pbs = _pose_bones(arm_ob, bone_count)
    view = []
    try:
        for f in frames:
            scene.frame_set(int(f))
            for pb in pbs:
                view += [float(v) for v in pb.location]
                view += [float(v) for v in pb.rotation_quaternion]
                view += [float(v) for v in pb.scale]
    finally:
        scene.frame_set(keep)
    return view


def _iter_fcurves(action):
    """The action's F-curves, across the pre-4.4 and slotted layouts.

    Returns None when neither shape is readable, which the caller reports as
    "added keyframes were not detected" rather than failing the export -- the
    source key frames still round-trip, so the damage of a wrong answer here is
    bounded and visible.
    """
    try:
        fcurves = list(action.fcurves)
        if fcurves:
            return fcurves
    except Exception:                                        # noqa: BLE001
        fcurves = None
    try:                                    # Blender 4.4+ slotted actions
        out = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", ()):
                    out += list(bag.fcurves)
        return out
    except Exception:                                        # noqa: BLE001
        return fcurves


def _user_frames(action):
    """Integer frames the user has keyframes on.  `None` if undiscoverable."""
    fcurves = _iter_fcurves(action)
    if fcurves is None:
        return None
    out = set()
    for fc in fcurves:
        for kp in fc.keyframe_points:
            out.add(int(round(kp.co[0])))
    return out


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

def import_motion(context, arm_ob, body, name, report):
    """Build an action on `arm_ob` from one MOTI chunk body.

    Returns the `Motion` that was decoded, so the caller can report on it, and
    stashes everything the exporter needs on the armature object.
    """
    motion = c3anim.parse_moti(bytes(body))
    bc = int(motion.bone_count)
    arm_ob[PROP_SRC] = K.b64(bytes(body))
    arm_ob[PROP_ENC] = motion.encoding
    arm_ob[PROP_FRAMECOUNT] = int(motion.frame_count)

    if not bc or not motion.keys:
        # A boneless or keyless track still has to survive an export, and it
        # has nothing to show in Blender.  Recording an empty view is what
        # tells the exporter "nothing here was edited" instead of "no record".
        arm_ob[PROP_FRAMES] = list(c3anim.required_frames(motion))
        arm_ob[PROP_VIEW] = []
        arm_ob[PROP_REST] = []
        arm_ob[PROP_PARENTS] = []
        report.append(f"{name}: {motion.encoding} {bc} bone(s), "
                      f"{len(motion.keys)} key(s) -- nothing to animate")
        return motion

    pbs = _pose_bones(arm_ob, bc)
    if pbs is None:
        raise RuntimeError(
            f"{name}: the armature is missing bone {K.BONE_FMT % (bc - 1)}; "
            f"the MOTI declares {bc} bones and every one needs a home")

    rest_flat = rest_locals(arm_ob, bc)
    parents = live_parents(arm_ob, bc)
    frames, view = c3anim.moti_to_view(motion, _nested_rest(rest_flat, bc),
                                       parents=parents)

    for pb in pbs:
        pb.rotation_mode = 'QUATERNION'
    arm_ob.animation_data_create()
    prev = arm_ob.animation_data.action

    for i, f in enumerate(frames):
        for b, pb in enumerate(pbs):
            o = (i * bc + b) * c3anim.FLOATS_PER_BONE
            pb.location = view[o:o + 3]
            pb.rotation_quaternion = view[o + 3:o + 7]
            pb.scale = view[o + 7:o + 10]
        for pb in pbs:
            for path, _n in CHANNELS:
                pb.keyframe_insert(data_path=path, frame=int(f),
                                   group=pb.name)

    action = arm_ob.animation_data.action
    if action is not None and action is not prev:
        action.name = name
        # LINEAR, because `Motion_GetMatrix` (RVA 0x551A0) lerps the 16 matrix
        # elements between bracketing keys.  Blender's default Bezier ease
        # would play back something the engine never shows -- and, worse, would
        # bake different values into any frame the user later keys.
        for fc in (_iter_fcurves(action) or ()):
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'

    arm_ob[PROP_FRAMES] = [int(f) for f in frames]
    arm_ob[PROP_VIEW] = _read_view(context, arm_ob, frames, bc)
    arm_ob[PROP_REST] = rest_flat
    arm_ob[PROP_PARENTS] = [(-1 if parents.get(b) is None else int(parents[b]))
                            for b in range(bc)]
    report.append(f"{name}: {motion.encoding} {bc} bone(s), "
                  f"{len(frames)} key(s), frameCount {motion.frame_count}")
    return motion


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def export_motion(context, arm_ob, warn):
    """-> the MOTI body this armature's action should be written as.

    `None` when the armature carries no imported track, which means the caller
    must leave the original chunk alone.
    """
    blob = arm_ob.get(PROP_SRC)
    if not blob:
        return None
    src = c3anim.parse_moti(K.unb64(blob))
    bc = int(src.bone_count)
    if not bc or not src.keys:
        return c3anim.motion_to_bytes(src)

    rest_flat = rest_locals(arm_ob, bc)
    rest = _nested_rest(rest_flat, bc)
    parents = live_parents(arm_ob, bc)
    # A RE-PARENTED BONE IS AN EDIT, exactly as a moved rest position is. The
    # stored matrices are absolute, so a changed tree changes what every
    # descendant's matrix should be while its ten view floats stand still --
    # `build_motion` cannot see that from the view alone, so say it here.
    stashed = [int(x) for x in arm_ob.get(PROP_PARENTS, ())]
    live = [(-1 if parents.get(b) is None else int(parents[b]))
            for b in range(bc)]
    if stashed and stashed != live:
        warn[f"{arm_ob.name}: the bone hierarchy changed since import; "
             f"every affected key was re-baked"] = 1

    action = None
    if arm_ob.animation_data is not None:
        action = arm_ob.animation_data.action
    tool_frames = ()
    if action is not None:
        found = _user_frames(action)
        if found is None:
            warn[f"{arm_ob.name}: could not read the action's F-curves, so "
                 f"keyframes added on new frames were not exported"] = 1
        else:
            tool_frames = found

    frames = c3anim.required_frames(src, tool_frames)
    view = _read_view(context, arm_ob, frames, bc)

    motion, stats = c3anim.build_motion(
        src, rest, frames, view,
        src_frames=list(arm_ob.get(PROP_FRAMES, ())),
        src_view=list(arm_ob.get(PROP_VIEW, ())),
        src_rest=list(arm_ob.get(PROP_REST, ())),
        warn=warn, parents=parents)
    if stats["rebaked"]:
        warn[f"{arm_ob.name}: {stats['rebaked']} edited bone key(s) re-baked "
             f"as {stats['encoding']} ({stats['reused']} unchanged)"] = 1
    return c3anim.motion_to_bytes(motion)
