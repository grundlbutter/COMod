"""
Blender -> C3.

The rule throughout is **"prefer the source when the view is unchanged"**: for
every field whose Blender representation is a lossy or ambiguous projection of
the file field (UVs, skinning slots, the chunk matrix, vertex colour), the
importer stashed the exact original alongside the editable view.  On export we
reconstruct what the view *would* be for that original; if it still matches,
the original wins and the bytes are identical.  If it does not match, the user
edited it, and the edit wins.

That is what makes "import, export, byte-identical" true without the exporter
simply blitting back a cached copy of the file -- every field really is read
back out of the Blender datablock.

**Animation is the same rule one level up.**  Each armature that carries an
imported `MOTI` track is re-serialized from its action, per bone key, reusing
the on-disk floats wherever the pose Blender holds still matches what the
importer recorded -- see `c3_anim` and `vendor/c3anim.py`.  An armature with no
imported track contributes no override and its chunk is written back verbatim,
so a user who never opened the animation cannot perturb it.
"""

import os

import bpy

from . import c3_anim
from . import c3_common as K
from .vendor import c3phy, c3write
from .vendor.c3phy import VARIANTS, C3Key, PhyMesh, Vertex, iter_chunks
from .vendor.c3write import build_c3, serialize_phy


class ExportError(RuntimeError):
    pass


# -- reading attributes back ----------------------------------------------

def _get(me, name, field="value", width=1):
    a = me.attributes.get(name)
    if a is None:
        return None
    buf = [0.0 if field != "value" or 'INT' not in a.data_type else 0] * \
        (len(me.vertices) * width)
    if a.data_type == 'INT':
        buf = [0] * (len(me.vertices) * width)
    a.data.foreach_get(field, buf)
    if width == 1:
        return buf
    return [tuple(buf[i * width:(i + 1) * width])
            for i in range(len(me.vertices))]


def _prop(ob, key, default=None):
    v = ob.get(key)
    return default if v is None else v


# -- per-field reconstruction ---------------------------------------------

def _uvs(me, layer_name, src_name, nverts):
    """-> list[(u, v)] in C3 convention, one per vertex.

    Blender UVs are per-loop; C3 UVs are per-vertex.  We take each vertex's
    first loop.  If other loops on the same vertex disagree the UV island was
    split, which C3 cannot represent -- reported, first loop wins.
    """
    layer = me.uv_layers.get(layer_name)
    src = _get(me, src_name, "vector", 2)
    if layer is None:
        return (src if src else [(0.0, 0.0)] * nverts), 0

    flat = [0.0] * (len(me.loops) * 2)
    layer.uv.foreach_get("vector", flat)
    view = [None] * nverts
    split = 0
    for li, lp in enumerate(me.loops):
        vi = lp.vertex_index
        uv = (flat[li * 2], flat[li * 2 + 1])
        if view[vi] is None:
            view[vi] = uv
        elif view[vi] != uv:
            split += 1
    out = []
    for i in range(nverts):
        vw = view[i]
        if vw is None:
            vw = (src[i] if src else (0.0, 0.0))
            out.append((vw[0], vw[1]) if src else (0.0, 0.0))
            continue
        if src is not None:
            su, sv = src[i]
            # what the importer would have written for this source
            if vw[0] == K.f32(su) and vw[1] == K.f32(K.flip_v(sv)):
                out.append((su, sv))
                continue
        out.append((vw[0], K.flip_v(vw[1])))
    return out, split


def _colors(me, nverts):
    src = _get(me, K.ATTR_COLOR_SRC)
    ca = me.color_attributes.get(K.ATTR_COLOR) if hasattr(
        me, "color_attributes") else None
    if ca is None or ca.domain != 'POINT':
        if src is not None:
            return [K.as_unsigned32(int(x)) for x in src]
        return [0] * nverts
    buf = [0.0] * (nverts * 4)
    ca.data.foreach_get("color", buf)
    out = []
    for i in range(nverts):
        packed = K.pack_rgba(*buf[i * 4:i * 4 + 4])
        if src is not None and packed == K.as_unsigned32(int(src[i])):
            out.append(K.as_unsigned32(int(src[i])))
        else:
            out.append(packed)
    return out


def _bone_index(ob, group_index):
    """Map a vertex-group index to a C3 bone index."""
    try:
        vg = ob.vertex_groups[group_index]
    except (IndexError, KeyError):
        return None
    arm = ob.find_armature()
    if arm is not None:
        b = arm.data.bones.get(vg.name)
        if b is not None and K.BONE_INDEX_PROP in b:
            return int(b[K.BONE_INDEX_PROP])
    digits = ""
    for ch in reversed(vg.name):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return int(digits) if digits else None


def _skin(ob, me, nverts, warn):
    """-> list[(bone0, bone1, w0, w1)]."""
    b0 = _get(me, K.ATTR_BONE0)
    b1 = _get(me, K.ATTR_BONE1)
    w0 = _get(me, K.ATTR_W0)
    w1 = _get(me, K.ATTR_W1)
    have_src = None not in (b0, b1, w0, w1)

    # The bone palette exactly as `Phy_Load` builds it (RVA 0x5A1A0) and as
    # `attach_skin` mirrors it: every bone0, plus bone1 only where weight1 is
    # non-zero.  A bone1 that is not in the palette therefore has NO vertex
    # group, so the "unchanged view" we compare against must not expect one.
    palette = set()
    if have_src:
        for i in range(nverts):
            palette.add(int(b0[i]))
            if w1[i] != 0.0:
                palette.add(int(b1[i]))

    gmap = {}
    for gi in range(len(ob.vertex_groups)):
        bi = _bone_index(ob, gi)
        if bi is not None:
            gmap[gi] = bi

    out = []
    for i in range(nverts):
        groups = [(gmap.get(g.group), g.weight) for g in me.vertices[i].groups]
        groups = [g for g in groups if g[0] is not None]

        if have_src:
            sb0, sb1 = int(b0[i]), int(b1[i])
            sw0, sw1 = w0[i], w1[i]
            # what attach_skin() would have produced for this source
            expect = [(sb0, K.f32(sw0))]
            if sb1 != sb0 and sb1 in palette:
                expect.append((sb1, K.f32(sw1)))
            got = [(b, K.f32(w)) for b, w in groups]
            if got == expect:
                out.append((sb0, sb1, sw0, sw1))
                continue

        if not groups:
            out.append((0, 0, 1.0, 0.0))
            warn["vertices with no bone weight"] = \
                warn.get("vertices with no bone weight", 0) + 1
            continue
        groups.sort(key=lambda t: -t[1])
        if len(groups) > 2:
            warn["vertices with >2 influences (truncated)"] = \
                warn.get("vertices with >2 influences (truncated)", 0) + 1
        (nb0, nw0) = groups[0]
        if len(groups) >= 2:
            nb1, nw1 = groups[1]
        else:
            nb1, nw1 = nb0, 0.0
        # The pair is constrained to sum to 1, so normalise rather than ship
        # something the renderer will silently contradict.
        #
        # WHY -- and this is TWO FACTS FORCED INTO AGREEMENT rather than one
        # address.  `c3phy` records that the FILE's `weight1` at 0x24 is only
        # ever tested against 0: its VALUE is never read.  The vertex shader
        # in `Env_DX9/graphic.dll` consumes `.w` as a real independent lane
        # (`fWeight2 = inVert.c3_BoneIndexWeight.w * 0.0039215686`, i.e.
        # 1/255).  Both can only be true if the CPU SYNTHESISES that lane
        # when packing the vertex -- which is `255 - weight0`.
        #
        # THIS REPLACES A CITATION OF `RVA 0x5A869`, and the replacement is
        # the point.  That address family does not match 7878's
        # `Env_DX9/graphic.dll` -- `0x5A774` there is a byte compare, not the
        # store it was cited for -- so it named one of the four `graphic.dll`
        # builds in this corpus and nobody recorded which.  **A reader who
        # checked it found the wrong instruction and no way to tell whether
        # the claim or the address was wrong.**  The conclusion was right and
        # its provenance was unresolvable; the argument above is checkable
        # from files anyone has.  (VibeCO, 2026-09-21, from the shipped HLSL.)
        s = nw0 + nw1
        if s > 0:
            nw0, nw1 = nw0 / s, nw1 / s
        else:
            nw0, nw1 = 1.0, 0.0
        out.append((nb0, nb1, nw0, nw1))

    # THE PALETTE THIS EXPORT ACTUALLY SHIPS -- not the source `palette`
    # above, which describes the file we started from.  A plain round trip
    # cannot grow it, but a body mesh DECLARES 84 bones while its skin uses
    # 25, and phase 1a named those spare bones and put them in collections so
    # they could be found and painted.  That feature working as intended is
    # also what makes a palette larger than anything shipped easy to produce.
    over = K.palette_warning(len(K.exported_palette(out)))
    if over:
        warn[over] = 1
    return out


def _matrix(ob):
    stored = _prop(ob, "c3_matrix")
    stored_bl = _prop(ob, "c3_matrix_blender")
    cur = [c for row in ob.matrix_basis for c in row]
    if stored is not None and stored_bl is not None:
        if all(a == b for a, b in zip(cur, list(stored_bl))):
            return tuple(float(x) for x in stored)
    rows = [tuple(cur[r * 4:r * 4 + 4]) for r in range(4)]
    return K.blender_matrix_to_c3(rows)


# -- object -> PhyMesh -----------------------------------------------------

def object_to_phy(ob, warn) -> PhyMesh:
    if ob.type != 'MESH':
        raise ExportError(f"{ob.name} is not a mesh")
    me = ob.data
    tag = _prop(ob, "c3_tag", "PHY4")
    if isinstance(tag, str):
        tag = tag.encode("latin-1")
    if len(tag) == 3:
        tag = tag + b" "
    if tag not in VARIANTS:
        raise ExportError(f"{ob.name}: unknown PHY variant {tag!r}")
    var = VARIANTS[tag]

    m = PhyMesh(tag=tag)
    m.name_raw = K.unb64(_prop(ob, "c3_name_raw", ""))
    m.name = _prop(ob, "c3_node_name", "") or m.name_raw.split(b"\x00")[0]\
        .decode("latin-1", "replace")
    if not m.name_raw:
        m.name_raw = m.name.encode("latin-1", "replace")
    m.unknown0 = int(_prop(ob, "c3_unknown0", 0))

    nv = len(me.vertices)
    for p in me.polygons:
        if len(p.vertices) != 3:
            raise ExportError(
                f"{ob.name}: polygon {p.index} has {len(p.vertices)} sides; "
                f"C3 stores a triangle list. Triangulate before exporting.")
    m.faces = [tuple(p.vertices) for p in me.polygons]

    m.vertex_count_a = int(_prop(ob, "c3_vcount_a", nv))
    m.vertex_count_b = int(_prop(ob, "c3_vcount_b", 0))
    m.face_count_a = int(_prop(ob, "c3_fcount_a", len(m.faces)))
    m.face_count_b = int(_prop(ob, "c3_fcount_b", 0))

    uv0, split0 = _uvs(me, K.UV0_NAME, K.ATTR_UV0_SRC, nv)
    if split0:
        warn[f"{ob.name}: {split0} split UV corners collapsed"] = 1
    uv1 = [(0.0, 0.0)] * nv
    if var["step"]:
        uv1, _ = _uvs(me, K.UV1_NAME, K.ATTR_UV1_SRC, nv)

    cols = _colors(me, nv)
    skin = _skin(ob, me, nv, warn)
    nrm = _get(me, K.ATTR_NORMAL_SRC, "vector", 3) if var["has_normal"] else None

    co = [0.0] * (nv * 3)
    me.vertices.foreach_get("co", co)
    for i in range(nv):
        x, y, z = K.to_c3(co[i * 3:i * 3 + 3])
        b0, b1, w0, w1 = skin[i]
        v = Vertex(px=x, py=y, pz=z,
                   u0=uv0[i][0], v0=uv0[i][1],
                   unknown4=cols[i],
                   bone0=b0, bone1=b1, weight0=w0, weight1=w1,
                   u1=uv1[i][0], v1=uv1[i][1])
        if nrm is not None:
            v.nx, v.ny, v.nz = K.to_c3(nrm[i])
        m.vertices.append(v)

    ba = _prop(ob, "c3_bbox_a")
    bb = _prop(ob, "c3_bbox_b")
    if ba is not None and bb is not None:
        m.bbox_a = tuple(float(x) for x in ba)
        m.bbox_b = tuple(float(x) for x in bb)
        m.bbox_min = tuple(min(a, b) for a, b in zip(m.bbox_a, m.bbox_b))
        m.bbox_max = tuple(max(a, b) for a, b in zip(m.bbox_a, m.bbox_b))
    else:
        c3write.recompute_bounds(m)

    m.matrix = _matrix(ob)
    m.frame_count = int(_prop(ob, "c3_frame_count", 0))
    m.keys = K.unpack_keys(K.unb64(_prop(ob, "c3_keys", "")), C3Key)
    if _prop(ob, "c3_has_step", False):
        # TWO f32 since M8. This rebuilt u32 ints and handed them to the
        # writer, which now packs f32 -- so a Blender round trip wrote
        # 3163243414.0 where -0.017 was meant. `step_from_prop` also reads a
        # PRE-M8 .blend, whose ints are bit patterns; the two ranges are
        # disjoint by measurement, not by a tuned threshold.
        m.step = K.step_from_prop(_prop(ob, "c3_step", [0.0, 0.0]))
    m.two_sided = bool(_prop(ob, "c3_two_sided", False))
    m.billboard = int(_prop(ob, "c3_billboard", 0))
    m.label_raw = K.unb64(_prop(ob, "c3_label_raw", ""))
    m.label = m.label_raw.decode("latin-1", "replace")
    m.is_c3exp_color = (m.label_raw == b"C3EXP_COLOR")
    m.phy5_raw = K.unb64(_prop(ob, "c3_phy5_raw", ""))
    m.tail_raw = K.unb64(_prop(ob, "c3_tail_raw", ""))
    si = ob.get("c3_chunk_index")
    m.source_index = None if si is None else int(si)
    return m


# -- collecting the objects of one container ------------------------------

def _c3_collection_of(ob):
    for c in ob.users_collection:
        if "c3_container" in c or "c3_source" in c:
            return c
    return None


def find_container(context, objects=None):
    """-> (collection_or_None, [mesh objects in chunk order])

    Resolution order, most specific first:
      1. objects passed in explicitly (the "selected only" export option)
      2. the current selection
      3. the active object's C3 collection
      4. the scene's only C3 collection, if there is exactly one
    Step 4 is what makes headless scripting and the acceptance test work,
    where there is no selection and no active object at all.
    """
    obs = [o for o in (objects if objects is not None
                       else context.selected_objects) if o.type == 'MESH']
    coll = None
    if obs:
        coll = _c3_collection_of(obs[0])
    else:
        act = context.view_layer.objects.active
        if act is not None:
            coll = _c3_collection_of(act)
        if coll is None:
            found = [c for c in context.scene.collection.children_recursive
                     if "c3_container" in c or "c3_source" in c]
            if len(found) == 1:
                coll = found[0]
            elif len(found) > 1:
                raise ExportError(
                    f"{len(found)} C3 collections in the scene "
                    f"({', '.join(c.name for c in found[:4])}...). Select the "
                    f"objects you want, or make one of them active.")
    if coll is not None:
        obs = [o for o in coll.objects if o.type == 'MESH' and "c3_tag" in o]
    obs.sort(key=lambda o: (o.get("c3_chunk_index", 1 << 30), o.name))
    return coll, obs


def collect_motions(context, meshes, obs, warn, enabled=True):
    """-> ``{source_index: moti_body}`` for the armatures that carry a track.

    Keyed by the mesh's ORIGINAL chunk slot, because that is what `rebuild_c3`
    pairs a MOTI with.  A mesh that has been duplicated has had its
    `source_index` cleared by the caller and is a new mesh with a new
    (synthesised) track, so it is skipped here rather than overwriting the slot
    it was copied from.

    Nothing is emitted for an armature the importer did not attach a track to,
    and that is the safe direction: an absent key means "write the original
    bytes", which is what every export before this feature existed did.
    """
    if not enabled:
        return {}
    out = {}
    for m, ob in zip(meshes, obs):
        if m.source_index is None:
            continue
        arm = ob.find_armature()
        if arm is None or c3_anim.PROP_SRC not in arm:
            continue
        if m.source_index in out:
            warn[f"two meshes share armature {arm.name}; only the first "
                 f"motion track was written"] = 1
            continue
        try:
            body = c3_anim.export_motion(context, arm, warn)
        except Exception as e:                              # noqa: BLE001
            raise ExportError(
                f"{arm.name}: the motion track could not be written back "
                f"({e}). Nothing was saved -- the original file is "
                f"untouched.") from e
        if body is not None:
            out[m.source_index] = body
    return out


def export_c3(context, filepath, objects=None, **opts):
    """Write a .c3.  Returns (bytes_written, report_lines)."""
    coll, obs = find_container(context, objects)
    if not obs:
        raise ExportError(
            "nothing to export: select the imported mesh objects, or make one "
            "of them active so the addon can find its collection")

    warn = {}
    meshes = [object_to_phy(o, warn) for o in obs]

    # Duplicating an object in Blender copies its custom properties, so two
    # objects can claim the same original chunk slot.  A duplicate is a NEW
    # mesh and needs its own motion track, so only the first keeps the slot.
    seen = set()
    for m in meshes:
        if m.source_index is None:
            continue
        if m.source_index in seen:
            m.source_index = None
            warn["duplicated object treated as a new mesh"] = 1
        else:
            seen.add(m.source_index)

    if opts.get("recompute_bounds", False):
        for m in meshes:
            c3write.recompute_bounds(m)

    original = None
    if coll is not None:
        blob = coll.get("c3_container")
        if blob:
            original = K.unb64(blob)
        elif coll.get("c3_source") and os.path.isfile(coll["c3_source"]):
            with open(coll["c3_source"], "rb") as fh:
                original = fh.read()

    if original is not None:
        n_phy = sum(1 for t, _ in iter_chunks(original) if t in VARIANTS)
        structural = opts.get("allow_add_remove", False)
        if n_phy != len(meshes) and not structural:
            raise ExportError(
                f"the source container has {n_phy} PHY chunks but "
                f"{len(meshes)} mesh objects were collected. Enable "
                f"\"Allow adding/removing meshes\" to restructure it -- but "
                f"read the warning first: a PHY chunk is paired with a MOTI "
                f"chunk by POSITION, and character meshes are animated by a "
                f"shared external motion set that assumes the original "
                f"layout. It is safe for self-animated meshes (effects), not "
                f"for body/armour/weapon meshes.")
        motions = collect_motions(context, meshes, obs, warn,
                                  opts.get("export_animation", True))
        data = c3write.rebuild_c3(original, meshes,
                                  allow_structural=structural,
                                  moti_override=motions)
        if motions:
            report_motions = f"{len(motions)} motion track(s) rebuilt"
        else:
            report_motions = "motion tracks carried through verbatim"
        if n_phy != len(meshes):
            warn[f"mesh count changed {n_phy} -> {len(meshes)}; MOTI tracks "
                 f"were added/removed to match"] = 1
    else:
        report_motions = "no source container: no motion tracks"
        data = build_c3([(m.tag, serialize_phy(m)) for m in meshes])
        warn["no source container: non-PHY chunks (MOTI etc.) are absent"] = 1

    with open(filepath, "wb") as fh:
        fh.write(data)

    report = [f"{len(meshes)} PHY chunk(s), {len(data)} bytes"]
    if original is not None:
        report.append("byte-identical to source"
                      if data == original else "differs from source")
    report.append(report_motions)
    report += [f"warning: {k}" for k in warn]
    return len(data), report
