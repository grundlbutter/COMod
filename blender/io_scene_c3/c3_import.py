"""
C3 -> Blender.

One `.c3` file becomes one collection.  Each PHY chunk in it becomes one mesh
object; every other chunk (MOTI, CAME, PTCL, SHAP, MNEW, CCFL, ...) is carried
through untouched as an opaque blob on the collection, so an export can rebuild
the whole container and not just the geometry.
"""

import os

import bpy
from mathutils import Matrix

from . import c3_common as K
from . import c3_material
from .vendor import c3phy
from .vendor.c3phy import VARIANTS, iter_chunks, parse_phy


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
    # STEP's payload is two raw u32s -- and 639 corpus chunks store values
    # above 0x7FFFFFFF there (they are float bit patterns, e.g. 0xBBE56042).
    # Blender IDProperty ints are int32, so keep them reinterpreted.
    ob["c3_step"] = [K.as_signed32(x) for x in (mesh.step or (0, 0))]
    ob["c3_two_sided"] = bool(mesh.two_sided)
    ob["c3_billboard"] = int(mesh.billboard)
    ob["c3_phy5_raw"] = K.b64(mesh.phy5_raw)
    ob["c3_tail_raw"] = K.b64(mesh.tail_raw)
    ob["c3_chunk_index"] = index
    return ob


def build_armature(all_bones, stem: str, opts) -> bpy.types.Object:
    """A placeholder skeleton for the recovered bone palette.

    The palette is not stored in the file -- `Phy_Load` derives it (RVA
    0x5A1A0) as the sorted unique set of every `boneIndex0`, plus `boneIndex1`
    wherever `weight1 != 0`.  `PhyMesh.bones` reproduces that.

    The bones' REST POSITIONS are unknown: they live in the `MOTI` chunks,
    which are still undecoded.  So the bones are laid out as a labelled ladder
    purely so they are selectable and weight-paintable.  In rest pose an
    armature modifier is the identity regardless of where the bones sit, so
    this placement is cosmetic and cannot corrupt the geometry.
    """
    arm = bpy.data.armatures.new(f"{stem}_skeleton")
    ao = bpy.data.objects.new(f"{stem}_skeleton", arm)
    bpy.context.collection.objects.link(ao)

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = ao
    bpy.ops.object.mode_set(mode='EDIT')
    step = max(opts.get("bone_spacing", 4.0), 0.01)
    for i, idx in enumerate(all_bones):
        b = arm.edit_bones.new(K.BONE_FMT % idx)
        b.head = (0.0, 0.0, i * step)
        b.tail = (0.0, 0.0, i * step + step * 0.8)
    bpy.ops.object.mode_set(mode='OBJECT')
    for idx in all_bones:
        bone = arm.bones.get(K.BONE_FMT % idx)
        if bone is not None:
            bone[K.BONE_INDEX_PROP] = idx
    ao["c3_bone_palette"] = list(all_bones)
    ao["c3_placeholder_rest_pose"] = True
    bpy.context.view_layer.objects.active = prev_active
    return ao


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

    palette = sorted({b for _, m in meshes for b in m.bones})
    arm_ob = None
    if opts.get("import_armature", True) and palette:
        arm_ob = build_armature(palette, stem, opts)

    objs = []
    report = []
    for order, m in meshes:
        ob = build_mesh_object(m, order, stem, opts)
        coll.objects.link(ob)
        if opts.get("import_armature", True):
            attach_skin(ob, m, arm_ob)
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
