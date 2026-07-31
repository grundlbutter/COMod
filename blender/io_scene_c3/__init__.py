"""
Conquer Online C3 mesh import/export for Blender.

Format work: docs/modding.md section 9, recovered by disassembling
`graphic.dll!Phy_Load`.  The C3 read/write code in `vendor/` is pure stdlib and
carries no `bpy` dependency, so it is unit-testable outside Blender; this
package is only the glue.
"""

bl_info = {
    "name": "Conquer Online C3 (mesh)",
    "author": "CO Client RE project",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > Conquer Online Mesh (.c3)",
    "description": "Import and export Conquer Online PHY meshes, "
                   "round-tripping byte-exactly.",
    "category": "Import-Export",
    "doc_url": "",
}

import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty,
                       StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import c3_common as K       # noqa: F401  (re-exported for scripting)
from . import c3_export, c3_import, c3_material
from .vendor import coroot

#: Where the game is installed, worked out the same way every command-line
#: tool in this project works it out (`core/coroot.py`): CO_ROOT, then a
#: saved config, then the conventional paths, then the Windows uninstall
#: registry.  Only ever a *starting value* for the preference below -- the
#: user's own setting wins once they type one.
DEFAULT_ROOT = str(coroot.default_root())


# --------------------------------------------------------------------------
# preferences
# --------------------------------------------------------------------------

class C3AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    game_root: StringProperty(
        name="Game install root",
        description="Used READ-ONLY, to resolve textures out of the loose "
                    "tree and the .wdf archives. Nothing is ever written here. "
                    "Leave blank to auto-detect",
        subtype='DIR_PATH',
        default=DEFAULT_ROOT,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "game_root")
        resolved = game_root(context)
        missing = coroot.missing_parts(resolved) if resolved else ["everything"]
        if resolved and not missing:
            col.label(text=f"found: {resolved}", icon='CHECKMARK')
        elif resolved:
            col.label(text="incomplete - textures will be grey", icon='ERROR')
            col.label(text="missing: " + ", ".join(missing))
        else:
            col.label(text="not found - textures will be grey", icon='ERROR')
            col.label(text="Set the path above, or the CO_ROOT "
                           "environment variable.")
        col.operator("c3.forget_textures", icon='FILE_REFRESH')


def prefs(context):
    try:
        return context.preferences.addons[__name__].preferences
    except KeyError:
        return None


def game_root(context):
    """The install root to read textures from.

    The addon preference wins when it names a real install.  When it is blank
    -- or points at an install that has since moved -- fall back to the shared
    auto-detection so a fresh Blender profile on someone else's machine still
    finds the game.
    """
    p = prefs(context)
    if p is not None and p.game_root:
        path = bpy.path.abspath(p.game_root)
        if coroot.looks_like_root(path):
            return path
        found = coroot.find()
        return str(found.path) if found else path
    found = coroot.find()
    return str(found.path) if found else ""


class C3_OT_forget_textures(bpy.types.Operator):
    bl_idname = "c3.forget_textures"
    bl_label = "Reset texture lookup cache"
    bl_description = "Re-read the appearance tables and archives on next import"

    def execute(self, context):
        c3_material.reset_resolver()
        self.report({'INFO'}, "C3 texture cache reset")
        return {'FINISHED'}


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

class C3_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.co_c3"
    bl_label = "Import C3"
    bl_description = "Import a Conquer Online .c3 mesh"
    bl_options = {'UNDO', 'PRESET'}

    filename_ext = ".c3"
    filter_glob: StringProperty(default="*.c3;*.C3", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    import_materials: BoolProperty(
        name="Materials and textures", default=True,
        description="Resolve the mesh's DDS through the appearance tables "
                    "and wire it into a Principled BSDF")
    import_armature: BoolProperty(
        name="Armature and vertex groups", default=True,
        description="Rebuild the 2-bone skin as a real armature. The bone "
                    "REST POSE is a placeholder - the real skeleton lives in "
                    "the still-undecoded MOTI chunks")
    bone_spacing: FloatProperty(
        name="Bone spacing", default=4.0, min=0.01, soft_max=50.0,
        description="Vertical spacing of the placeholder bone ladder")

    def draw(self, context):
        c = self.layout.column()
        c.prop(self, "import_materials")
        c.prop(self, "import_armature")
        sub = c.column()
        sub.enabled = self.import_armature
        sub.prop(self, "bone_spacing")

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files] \
            if self.files else [self.filepath]
        opts = dict(import_materials=self.import_materials,
                    import_armature=self.import_armature,
                    bone_spacing=self.bone_spacing,
                    game_root=game_root(context))
        n = 0
        for p in paths:
            try:
                coll, objs, rep = c3_import.import_c3(context, p, **opts)
            except Exception as e:                      # noqa: BLE001
                self.report({'ERROR'}, f"{os.path.basename(p)}: {e}")
                continue
            n += len(objs)
            for line in rep:
                print(f"[c3] {os.path.basename(p)}: {line}")
        if not n:
            return {'CANCELLED'}
        self.report({'INFO'}, f"imported {n} C3 mesh object(s)")
        return {'FINISHED'}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

class C3_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.co_c3"
    bl_label = "Export C3"
    bl_description = "Write a Conquer Online .c3 mesh"
    bl_options = {'PRESET'}

    filename_ext = ".c3"
    filter_glob: StringProperty(default="*.c3;*.C3", options={'HIDDEN'})

    use_selection: BoolProperty(
        name="Selected objects only", default=False,
        description="Otherwise every C3 mesh in the active object's "
                    "collection is exported, in its original chunk order")
    recompute_bounds: BoolProperty(
        name="Recompute bounding box", default=False,
        description="Rebuild the declared AABB from the geometry. Leave OFF "
                    "to keep the original bytes; turn ON after moving "
                    "vertices, since the engine frustum-culls against it")
    allow_add_remove: BoolProperty(
        name="Allow adding/removing meshes", default=False,
        description="Let the mesh COUNT change. Each PHY chunk is paired with "
                    "a MOTI animation track BY POSITION, so the pair is moved "
                    "together and new meshes get a static track. Safe for "
                    "self-animated meshes (effects); NOT safe for body, "
                    "armour or weapon meshes, which a shared external motion "
                    "set animates by ordinal")

    def draw(self, context):
        c = self.layout.column()
        c.prop(self, "use_selection")
        c.prop(self, "recompute_bounds")
        c.prop(self, "allow_add_remove")
        if self.allow_add_remove:
            box = c.box().column(align=True)
            box.label(text="Only for self-animated meshes", icon='ERROR')
            box.label(text="(effects). Body/armour/weapon")
            box.label(text="meshes will animate wrongly.")

    def execute(self, context):
        objs = [o for o in context.selected_objects] \
            if self.use_selection else None
        try:
            n, rep = c3_export.export_c3(
                context, self.filepath, objects=objs,
                recompute_bounds=self.recompute_bounds,
                allow_add_remove=self.allow_add_remove)
        except Exception as e:                          # noqa: BLE001
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for line in rep:
            print(f"[c3] export: {line}")
        self.report({'INFO'}, f"wrote {n} bytes - {rep[0]}")
        return {'FINISHED'}


# --------------------------------------------------------------------------
# a small N-panel showing what was preserved
# --------------------------------------------------------------------------

class C3_PT_mesh_info(bpy.types.Panel):
    bl_label = "C3 Mesh"
    bl_idname = "C3_PT_mesh_info"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "C3"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and "c3_tag" in ob

    def draw(self, context):
        ob = context.active_object
        col = self.layout.column(align=True)
        col.label(text=f"variant: {ob.get('c3_tag', '?')}")
        col.label(text=f"node: {ob.get('c3_node_name', '?')}")
        col.label(text=f"verts: {len(ob.data.vertices)} "
                       f"({ob.get('c3_vcount_a', 0)}+"
                       f"{ob.get('c3_vcount_b', 0)})")
        col.label(text=f"tris: {len(ob.data.polygons)} "
                       f"({ob.get('c3_fcount_a', 0)}+"
                       f"{ob.get('c3_fcount_b', 0)})")
        lab = ob.get("c3_label", "")
        if lab:
            col.label(text=f"label: {lab}", icon='TEXT')
        tex = ob.get("c3_texture")
        if tex:
            col.label(text=f"{tex}", icon='TEXTURE')
            col.label(text=f"from {ob.get('c3_texture_source', '?')}")
        if ob.get("c3_two_sided"):
            col.label(text="2SID (two-sided)")
        if ob.get("c3_billboard"):
            col.label(text=f"billboard mode {ob['c3_billboard']}")
        col.separator()
        col.operator("export_scene.co_c3", icon='EXPORT')


# --------------------------------------------------------------------------

def menu_import(self, context):
    self.layout.operator(C3_OT_import.bl_idname,
                         text="Conquer Online Mesh (.c3)")


def menu_export(self, context):
    self.layout.operator(C3_OT_export.bl_idname,
                         text="Conquer Online Mesh (.c3)")


_classes = (C3AddonPreferences, C3_OT_forget_textures,
            C3_OT_import, C3_OT_export, C3_PT_mesh_info)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    c3_material.reset_resolver()


if __name__ == "__main__":
    register()
