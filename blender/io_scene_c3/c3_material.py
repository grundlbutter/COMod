"""
Wire an imported mesh to its real DDS texture, so the user sees the model as
the game draws it instead of grey clay.

Resolution is done by the bpy-free `c3tex.TextureResolver` (tools/c3tex.py),
which inverts the appearance tables to get mesh-id -> texture-id and then goes
through the same loose-then-archive lookup order the client uses.  Blender
loads DDS natively, so an archived texture only has to be copied into a cache
directory first -- **nothing is ever written into the game install.**
"""

import os

import bpy

from . import c3_common as K

_RESOLVER = None
_RESOLVER_ROOT = None


def get_resolver(root):
    global _RESOLVER, _RESOLVER_ROOT
    if _RESOLVER is not None and _RESOLVER_ROOT == root:
        return _RESOLVER
    from .vendor.c3tex import TextureResolver
    if _RESOLVER is not None:
        try:
            _RESOLVER.close()
        except Exception:
            pass
    _RESOLVER = TextureResolver(root)
    _RESOLVER_ROOT = root
    return _RESOLVER


def reset_resolver():
    global _RESOLVER, _RESOLVER_ROOT
    if _RESOLVER is not None:
        try:
            _RESOLVER.close()
        except Exception:
            pass
    _RESOLVER = None
    _RESOLVER_ROOT = None


def _logical_for(filepath, root):
    """Best-effort logical asset path, e.g. 'c3/mesh/002135000.c3'."""
    try:
        rel = os.path.relpath(filepath, root)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return os.path.basename(filepath)


def build_material(name, image_path, two_sided=False, use_vcol=False):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_backface_culling = not two_sided
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.85
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.1

    out = bsdf
    if image_path:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (-420, 260)
        tex.image = bpy.data.images.load(image_path, check_existing=True)
        tex.interpolation = 'Closest' if False else 'Linear'
        base = tex.outputs["Color"]
        if use_vcol:
            attr = nt.nodes.new("ShaderNodeVertexColor")
            attr.layer_name = K.ATTR_COLOR
            attr.location = (-420, -40)
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = 'RGBA'
            mix.blend_type = 'MULTIPLY'
            mix.inputs["Factor"].default_value = 1.0
            mix.location = (-200, 160)
            nt.links.new(mix.inputs[6], base)
            nt.links.new(mix.inputs[7], attr.outputs["Color"])
            base = mix.outputs[2]
        nt.links.new(bsdf.inputs["Base Color"], base)
        # DXT3 carries explicit 4-bit alpha and the engine uses it for cutout
        # foliage/effects; wire it up but leave the material opaque-blended.
        nt.links.new(bsdf.inputs["Alpha"], tex.outputs["Alpha"])
        mat.blend_method = 'BLEND' if hasattr(mat, "blend_method") else \
            mat.blend_method
    else:
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.62, 1.0)
    return mat


def assign_material(ob, mesh, filepath, opts):
    root = opts.get("game_root") or ""
    img = None
    loc = None
    if root and os.path.isdir(root):
        tr = get_resolver(root)
        logical = _logical_for(filepath, root)
        p, loc = tr.texture_for_mesh(logical)
        if p is None:
            # the file may be outside the install (an extracted copy)
            p, loc = tr.texture_for_mesh(os.path.basename(filepath))
        img = str(p) if p else None

    mat = build_material(
        f"{ob.name}_mat", img,
        two_sided=bool(ob.get("c3_two_sided", False)),
        use_vcol=bool(getattr(mesh, "is_c3exp_color", False)))
    ob.data.materials.append(mat)
    if loc is not None:
        ob["c3_texture"] = loc.logical
        ob["c3_texture_source"] = loc.source
    return mat
