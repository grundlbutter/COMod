"""
Wire an imported mesh to its real DDS texture, so the user sees the model as
the game draws it instead of grey clay.

Resolution is done by the bpy-free `meshtex.MeshTextureIndex`
(tools/meshtex.py) -- **the same ranking the CLI answers with**, so the addon
and `py -3 tools/meshtex.py` cannot disagree about which texture a mesh wears.
It returns ranked `Match`es carrying the RULE that fired and a MEASURED
confidence, not a flat first-hit list: `bodytype_dir` 0.98, `appearance_table`
0.95, down to `dir_sibling` at **0.30**, which meshtex's own docstring calls a
shortlist rather than an answer.  `c3tex.TextureResolver` stays as the
fallback for the case meshtex cannot serve (a mesh outside the install), and
Blender loads DDS natively, so an archived texture only has to be copied into
a cache directory first -- **nothing is ever written into the game install.**

WHAT THE VENDORED COPY CANNOT DO, STATED HERE RATHER THAN DISCOVERED
-------------------------------------------------------------------
Two of `meshtex`'s imports are deliberately absent from the addon, and both
were read at their call site rather than assumed:

  * **`dds`** imports PIL and numpy.  The addon is pure stdlib plus what
    Blender ships, and Blender decodes DDS itself, so vendoring it would trade
    that promise for nothing.  Its one call site is a texture-VERIFICATION
    helper (`width`/`height`/`format`/non-power-of-two notes), **not the
    matching path**.  Texture verification is unavailable here; matching is
    unaffected.
  * **`assetdiff`** imports the entire `plugins` package.  Its call site is
    already `try/except` -> `prof = None`, which falls back to the default
    table profile, so `npc_table` still answers -- from the default profile
    rather than a base-specific one.

Both are rewritten to `raise ImportError` with that reason by
`tools/build_addon.py`, so the vendored file explains itself at the point of
failure.
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


_INDEX = None
_INDEX_ROOT = None


def get_index(root):
    """The `meshtex` index for `root`, or None if it cannot be built.

    Construction walks the install tree once (~1 s) and then loads ini tables
    lazily, so it is worth keeping.  Returning None rather than raising is
    deliberate: a missing index must degrade to the old resolver, never break
    an import the user is halfway through.
    """
    global _INDEX, _INDEX_ROOT
    if _INDEX is not None and _INDEX_ROOT == root:
        return _INDEX
    try:
        from .vendor.meshtex import MeshTextureIndex
    except Exception:
        return None
    if _INDEX is not None:
        try:
            _INDEX.close()
        except Exception:
            pass
        _INDEX = None
    try:
        _INDEX = MeshTextureIndex(root)
        _INDEX_ROOT = root
    except Exception:
        _INDEX, _INDEX_ROOT = None, None
    return _INDEX


def ranked_matches(root, logical):
    """Ranked `meshtex` matches for one mesh, best first.  `[]` if unavailable.

    Each item is `(texture, method, confidence, kind, detail)`.  The caller
    decides what to do with a low confidence; this function does not filter,
    because a filter would hide the one number that tells you how much to
    trust the answer.
    """
    idx = get_index(root)
    if idx is None:
        return []
    try:
        out = []
        for m in idx.matches(logical):
            out.append((m.texture, m.method, float(m.confidence),
                        m.kind, m.detail))
        return out
    except Exception:
        return []


def reset_resolver():
    global _RESOLVER, _RESOLVER_ROOT, _INDEX, _INDEX_ROOT
    if _RESOLVER is not None:
        try:
            _RESOLVER.close()
        except Exception:
            pass
    _RESOLVER = None
    _RESOLVER_ROOT = None
    # The index caches the same tables keyed by the same root, so a reset that
    # cleared only one of them would leave the panel showing a rule derived
    # from the install the user just stopped pointing at.
    if _INDEX is not None:
        try:
            _INDEX.close()
        except Exception:
            pass
    _INDEX = None
    _INDEX_ROOT = None


def _logical_for(filepath, root):
    """Best-effort logical asset path, e.g. 'c3/mesh/002135000.c3'."""
    try:
        rel = os.path.relpath(filepath, root)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return os.path.basename(filepath)


#: Cutout modes in order of preference. Every one of them WRITES DEPTH
#: except 'BLEND', which is the whole problem, so 'BLEND' is a last resort
#: and gets a hard threshold node to behave as much like a cutout as it can.
#:
#: The available set is queried from the RNA rather than assumed: Blender
#: 4.2's EEVEE Next reworked this enum, so a name that exists in one version
#: is absent in another and a hardcoded literal raises on assignment. The
#: owner is on 5.0.1 and the gate rig is 5.2; neither is the version this
#: file was written against.
_CUTOUT_PREFERENCE = ("CLIP", "HASHED", "DITHERED", "BLEND")


def _blend_modes(mat):
    try:
        return set(mat.bl_rna.properties["blend_method"].enum_items.keys())
    except Exception:                                        # noqa: BLE001
        return set()


def _set_cutout(mat, nt, bsdf, tex):
    """Make an alpha-tested material behave like one on THIS Blender.

    Returns the mode chosen, or None when the build has no `blend_method` at
    all -- then the alpha link alone is the best available and nothing here
    can improve on it.
    """
    if not hasattr(mat, "blend_method"):
        return None
    have = _blend_modes(mat)
    chosen = None
    for want in _CUTOUT_PREFERENCE:
        if have and want not in have:
            continue
        try:
            mat.blend_method = want
        except (TypeError, ValueError):
            continue
        chosen = want
        break
    if hasattr(mat, "alpha_threshold"):
        mat.alpha_threshold = 0.5
    if chosen == "BLEND":
        # Only reachable when the build offers nothing that writes depth.
        # Binarising alpha does not restore depth sorting, but it removes the
        # ~1% of partially-transparent texels that make the failure obvious.
        thr = nt.nodes.new("ShaderNodeMath")
        thr.operation = 'GREATER_THAN'
        thr.inputs[1].default_value = 0.5
        thr.location = (-200, -220)
        nt.links.new(thr.inputs[0], tex.outputs["Alpha"])
        nt.links.new(bsdf.inputs["Alpha"], thr.outputs["Value"])
    return chosen


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
        # DXT3 carries explicit 4-bit alpha and THE ENGINE ALPHA-TESTS IT --
        # a cutout, not a blend. MEASURED on 003188495's four skins: 73-96%
        # of texels are fully opaque, 3-25% fully transparent, and only ~1.2%
        # anywhere in between, which is DXT3's 4-bit quantisation at the
        # cutout edge rather than real translucency.
        #
        # THIS USED TO SET 'BLEND', WHICH IS WHY MESHES LOOKED INSIDE-OUT.
        # A blended surface does not write depth in EEVEE, so the far side of
        # a closed mesh draws over the near side and the texture appears to be
        # painted on the inside. The comment beside it claimed the material
        # was left "opaque-blended"; it was not, and the code won.
        nt.links.new(bsdf.inputs["Alpha"], tex.outputs["Alpha"])
        _set_cutout(mat, nt, bsdf, tex)
    else:
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.62, 1.0)
    return mat


def assign_material(ob, mesh, filepath, opts):
    root = opts.get("game_root") or ""
    img = None
    loc = None
    ranked = []
    if root and os.path.isdir(root):
        tr = get_resolver(root)
        logical = _logical_for(filepath, root)
        ranked = ranked_matches(root, logical)
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
    if ranked:
        # The rule and its confidence travel WITH the pick. Without them the
        # panel shows a texture and the reader cannot tell a 0.98 authored
        # table lookup from a 0.30 "some .dds in the same folder".
        chosen = None
        if loc is not None:
            for tex, meth, conf, kind, detail in ranked:
                if tex == loc.logical:
                    chosen = (tex, meth, conf, kind, detail)
                    break
        if chosen is None:
            chosen = ranked[0]
        ob["c3_texture_method"] = chosen[1]
        ob["c3_texture_confidence"] = chosen[2]
        ob["c3_texture_kind"] = chosen[3]
        ob["c3_texture_detail"] = chosen[4] or ""
        # Every candidate, best first, so the user can pick a different one
        # without re-importing. Stored as a flat list because Blender ID
        # properties do not hold tuples.
        ob["c3_texture_alternatives"] = [t for t, _m, _c, _k, _d in ranked]
        ob["c3_texture_ranked_by"] = [
            "%s %.2f %s" % (m, c, t) for t, m, c, _k, _d in ranked]
        # meshtex disagreeing with the resolver that actually loaded the image
        # is worth surfacing rather than smoothing over: it means the two
        # lookups have diverged and one of them is wrong.
        ob["c3_texture_agrees"] = bool(
            loc is not None and ranked and ranked[0][0] == loc.logical)
    return mat
