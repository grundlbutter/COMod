"""
Conquer Online C3 mesh and animation import/export for Blender.

Format work: docs/modding.md section 9 (`PHY`, from `graphic.dll!Phy_Load`) and
docs/effects.md 6.1 (`MOTI`, from `graphic.dll!Motion_Load`).  The C3
read/write code in `vendor/` is pure stdlib and carries no `bpy` dependency, so
it is unit-testable outside Blender; this package is only the glue.

The animation half is `c3_anim` here and `vendor/c3anim.py` there --
docs/blender_animation_2026-09-06.md, gated headlessly by
tests/test_moti_action.py.
"""

bl_info = {
    "name": "Conquer Online C3 (mesh + animation)",
    "author": "CO Client RE project",
    #: VERSIONING, an owner rule (2026-09-20):
    #:   * THIRD number: bump on EVERY build that leaves this repo. A fix,
    #:     a tweak, a rebuild -- if the user is handed a new zip, this moves.
    #:     A version that cannot move is indistinguishable from a failed
    #:     install, and that has already cost a round of uninstall/restart/
    #:     reinstall against a build that was correct.
    #:   * SECOND number: a major issue SOLVED **and confirmed by a human**.
    #:     Not self-awarded. "The skeleton imports correctly" is the owner's
    #:     call to make, not this file's.
    #: `tools/build_addon.py` reads this and writes blender_manifest.toml from
    #: it -- Blender shows the MANIFEST for an extension, so never edit that
    #: literal instead of this one.
    #: 1.2.15, and the third digit is the whole of the bump: `tools/effects.py`
    #: is VENDORED, so giving `EffectDB` a `close()` changes the shipped addon
    #: and `versioncheck.py` would call keeping 1.2.14 a NO BUMP defect. The
    #: second digit stays put -- that one is a human's to award, not a seat's.
    #:
    #: MASTER'S NUMBER IS THE FLOOR, NOT THE ANSWER. Two branches that both read
    #: master and both "bump the third digit" ship two different builds under one
    #: version, which is the exact failure this rule exists to prevent -- so the
    #: open PRs' claims are read first, not just master's file. RE-READ ACROSS
    #: THE SET 2026-10-01 with `tools/versioncheck.py`, which is the only thing
    #: that can see a collision at all -- `gh pr view --json files` lists what a
    #: ref CHANGES and would have reported these three as silent:
    #:   master 1.2.14 | #219 1.0.0 | #233 1.0.0 | #261 1.2.14 | here 1.2.15
    #: #219 and #233 carry 1.0.0 because they branched before the 1.2 line, not
    #: because they are neutral. 1.2.15 is unclaimed; `versioncheck` exits 0.
    "version": (1, 2, 15),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > Conquer Online Mesh (.c3)",
    "description": "Import and export Conquer Online PHY meshes and MOTI "
                   "animation tracks, round-tripping byte-exactly.",
    "category": "Import-Export",
    "doc_url": "",
}

import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import c3_common as K       # noqa: F401  (re-exported for scripting)
from . import c3_anim, c3_export, c3_import, c3_material
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
        description="Rebuild the 2-bone skin as a real armature, one per mesh. "
                    "A .c3 stores NO rest skeleton -- a MOTI matrix is a "
                    "skinning matrix, so the rest pose it implies puts every "
                    "bone on the origin -- so the rest pose is chosen by "
                    "'Bone placement' below. Either choice deforms "
                    "identically: the animation is conjugated by each bone's "
                    "own rest matrix, so the rest pose cancels")
    import_animation: BoolProperty(
        name="Animation (MOTI)", default=True,
        description="Import the mesh's MOTI track as an armature action. "
                    "Keys are LINEAR because the engine lerps between them. "
                    "An action you do not touch is exported back byte-exactly; "
                    "a bone key you move is re-baked into the file's own "
                    "encoding")
    bone_placement: EnumProperty(
        name="Bone placement", default='SKIN',
        items=(('SKIN', "From the skin",
                "Put each bone where its own weighted vertices are, so a "
                "forearm bone lies along the forearm. Bones no vertex "
                "references cannot be placed from anything: they are parked "
                "beside the mesh and hidden, which is what makes the rest "
                "selectable. Deformation is unaffected -- the pose is "
                "conjugated by the rest matrix, so the rest pose cancels"),
               ('LADDER', "Labelled ladder",
                "The original placeholder: bones stacked up +Z in index "
                "order. Nothing is hidden. Use it when you want every bone "
                "visible, or when a mesh has no skin to read")),
        description="Where to put bones a .c3 does not store positions for")
    bone_naming: EnumProperty(
        name="Bone names", default='LABEL',
        items=(('LABEL', "Anatomical",
                "Name each bone for what it is -- forearm.R, pelvis, thigh.L "
                "-- derived from the skeleton's own shape. This is what a "
                "retargeter matches on; nothing can retarget onto bone_017. "
                "Bones the classifier cannot name keep their index name"),
               ('INDEX', "bone_000 style",
                "Keep the raw MOTI index as the name. Use it when you want "
                "the name to match the bone number in the file")),
        description="What to call the bones. Either way every bone carries "
                    "its index in c3_bone_index, and that -- not the name -- "
                    "is what import, export and the motion loader resolve on")
    bone_hierarchy: BoolProperty(
        name="Parent bones into a hierarchy", default=True,
        description="Derive a bone tree from the skin and parent the bones "
                    "with it, so rotating a shoulder carries the arm. A .c3 "
                    "stores no hierarchy, but a vertex blended between two "
                    "bones sits at the joint between them, so the skin is an "
                    "adjacency graph -- the tree is its maximum-weight "
                    "spanning forest, rooted at the heaviest bone. The tree "
                    "is stored on the armature and the animation is "
                    "conjugated by each bone's PARENT, so an untouched track "
                    "still exports byte-exactly")
    bone_spacing: FloatProperty(
        name="Bone spacing", default=4.0, min=0.01, soft_max=50.0,
        description="Ladder spacing, and the clearance used to park bones "
                    "the skin cannot place")

    def draw(self, context):
        c = self.layout.column()
        c.prop(self, "import_materials")
        c.prop(self, "import_armature")
        sub = c.column()
        sub.enabled = self.import_armature
        sub.prop(self, "import_animation")
        sub.prop(self, "bone_placement")
        row = sub.row()
        row.enabled = self.bone_placement == 'SKIN'
        row.prop(self, "bone_hierarchy")
        sub.prop(self, "bone_naming")
        sub.prop(self, "bone_spacing")

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files] \
            if self.files else [self.filepath]
        opts = dict(import_materials=self.import_materials,
                    import_armature=self.import_armature,
                    import_animation=self.import_animation,
                    bone_placement=self.bone_placement,
                    bone_hierarchy=self.bone_hierarchy,
                    bone_naming=self.bone_naming,
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
# load a shared motion set onto an imported character
# --------------------------------------------------------------------------

#: What a loaded motion set leaves on an armature so it can be written back.
#: The blob is the WHOLE original container; `export_motion` only ever
#: produces one MOTI body, and every other byte has to come from somewhere.
MOTION_BLOB = "c3_motion_container"
MOTION_PATH = "c3_motion_source"
MOTION_SLOT = "c3_motion_slot"


class C3_OT_load_motion(bpy.types.Operator, ImportHelper):
    """Play a real game action on an imported character.

    A character's own `.c3` carries a ONE-KEY MOTI -- a bind stub, not an
    animation. The animation lives in a separate motion-only file under
    `c3/0001`-`0004`, bound by `3dmotion.ini`, and that is what the engine
    plays. Those files hold no PHY at all, so they cannot be imported on
    their own; they are a track looking for a skeleton.

    This loads one over the character you already have. The binding is BY
    ORDINAL and it is exact: the motion file's i-th MOTI drives the mesh's
    i-th PHY, so slots run armet, l_weapon, r_weapon, body against MOTI bone
    counts of 1, 1, 1, 84 on a player body.
    """
    bl_idname = "import_scene.co_c3_motion"
    bl_label = "Load C3 Motion Set"
    bl_description = ("Play a shared motion set (c3/0001-0004) on the "
                      "selected C3 armatures. REPLACES the track each "
                      "armature would export")
    bl_options = {'UNDO'}

    filename_ext = ".c3"
    filter_glob: StringProperty(default="*.c3;*.C3", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return any(o.type == 'ARMATURE' and "c3_bone_palette" in o
                   for o in context.selected_objects)

    def execute(self, context):
        arms = [o for o in context.selected_objects
                if o.type == 'ARMATURE' and "c3_bone_palette" in o]
        if not arms:
            self.report({'ERROR'}, "select the C3 armature(s) first")
            return {'CANCELLED'}
        try:
            with open(self.filepath, "rb") as fh:
                raw = fh.read()
            chunks = list(c3_import.iter_chunks(raw))
        except OSError as e:
            self.report({'ERROR'}, f"{self.filepath}: {e}")
            return {'CANCELLED'}
        bodies = [b for t, b in chunks if t == c3_import.MOTI_TAG]
        if not bodies:
            self.report({'ERROR'},
                        f"{os.path.basename(self.filepath)} holds no MOTI "
                        f"chunk; it is not a motion set")
            return {'CANCELLED'}

        stem = os.path.splitext(os.path.basename(self.filepath))[0]
        report, done = [], 0
        for arm in sorted(arms, key=lambda o: o.get(c3_anim.PROP_INDEX, 0)):
            slot = int(arm.get(c3_anim.PROP_INDEX, 0))
            if slot >= len(bodies):
                report.append(f"{arm.name}: motion has no slot {slot}")
                continue
            # THE BONE COUNTS MUST AGREE. `import_motion` raises when a bone
            # the MOTI declares has no home, and a mismatch here means the
            # motion belongs to a different body type -- worth saying so
            # rather than letting the exception surface.
            try:
                need = c3_anim.moti_bone_count(bodies[slot])
            except Exception as e:                       # noqa: BLE001
                report.append(f"{arm.name}: {e}")
                continue
            have = len(arm.data.bones)
            if need > have:
                report.append(
                    f"{arm.name}: motion slot {slot} declares {need} bone(s) "
                    f"and this armature has {have} -- wrong body type?")
                continue
            if arm.animation_data:
                arm.animation_data.action = None
            try:
                c3_anim.import_motion(context, arm, bodies[slot],
                                      f"{stem}_{slot}_motion", report)
            except Exception as e:                       # noqa: BLE001
                report.append(f"{arm.name}: {e}")
                continue
            # STASH THE WHOLE CONTAINER, not just the track. An export has to
            # rebuild the file around the edited chunk, and a motion set is
            # not always four bare MOTI chunks -- some carry CAME and even a
            # PHY. Keeping the original bytes is what lets everything this
            # addon did not touch be written back verbatim.
            arm[MOTION_BLOB] = K.b64(raw)
            arm[MOTION_PATH] = self.filepath
            arm[MOTION_SLOT] = slot
            done += 1

        for line in report:
            print("[c3] " + line)
        if not done:
            self.report({'ERROR'}, report[-1] if report else "nothing loaded")
            return {'CANCELLED'}

        # Set the scene range to the motion, so pressing play shows the
        # action rather than 250 frames of which 30 are the animation.
        frames = []
        for arm in arms:
            frames += [int(f) for f in arm.get(c3_anim.PROP_FRAMES, ())]
        if frames:
            context.scene.frame_start = min(frames)
            context.scene.frame_end = max(frames)
            context.scene.frame_current = min(frames)
        self.report({'INFO'},
                    f"{stem}: loaded onto {done} armature(s), frames "
                    f"{context.scene.frame_start}-{context.scene.frame_end}. "
                    f"This REPLACES the track they would export")
        return {'FINISHED'}



class C3_OT_export_motion(bpy.types.Operator, ExportHelper):
    """Write an edited motion set back over the file the engine plays.

    THIS IS THE ONE THAT CHANGES WHAT THE GAME DOES. Exporting the MESH
    writes the animation into the mesh's own MOTI, and for a character that
    is dead weight: the shared external set overrides it, so the edit is
    invisible in game. A character's animation is only editable HERE.

    Byte-exactness is inherited, not re-implemented: `export_motion` reuses
    the source keys verbatim while the live rest pose and view still match
    what the import stashed, so a set you load and do not touch writes back
    identical bytes.
    """
    bl_idname = "export_scene.co_c3_motion"
    bl_label = "Export C3 Motion Set"
    bl_description = ("Write the edited action(s) back into a copy of the "
                      "motion set they came from")
    bl_options = {'PRESET'}

    filename_ext = ".c3"
    filter_glob: StringProperty(default="*.c3;*.C3", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return any(o.type == 'ARMATURE' and MOTION_BLOB in o
                   for o in context.selected_objects)

    def execute(self, context):
        arms = [o for o in context.selected_objects
                if o.type == 'ARMATURE' and MOTION_BLOB in o]
        if not arms:
            self.report({'ERROR'},
                        "select armature(s) that had a motion set loaded "
                        "onto them (File > Import > Conquer Online Motion "
                        "Set) -- there is nothing else to write back")
            return {'CANCELLED'}

        sources = {a.get(MOTION_PATH) for a in arms}
        if len(sources) > 1:
            self.report({'ERROR'},
                        f"the selected armatures came from {len(sources)} "
                        f"different motion sets; one file cannot hold them")
            return {'CANCELLED'}

        original = K.unb64(arms[0][MOTION_BLOB])
        chunks = list(c3_import.iter_chunks(original))
        by_slot = {}
        n = 0
        for i, (tag, body) in enumerate(chunks):
            if tag == c3_import.MOTI_TAG:
                by_slot[n] = i
                n += 1

        warn, report, rebuilt = {}, [], 0
        out = [[tag, body] for tag, body in chunks]
        for arm in arms:
            slot = int(arm.get(MOTION_SLOT, -1))
            if slot not in by_slot:
                report.append(f"{arm.name}: slot {slot} is not in the source")
                continue
            try:
                body = c3_anim.export_motion(context, arm, warn)
            except Exception as e:                       # noqa: BLE001
                report.append(f"{arm.name}: {e}")
                continue
            if body is None:
                report.append(f"{arm.name}: no imported track; carried over")
                continue
            out[by_slot[slot]][1] = body
            rebuilt += 1

        if not rebuilt:
            self.report({'ERROR'}, report[-1] if report else "nothing to write")
            return {'CANCELLED'}

        data = c3_export.c3write.build_c3([(t, b) for t, b in out])
        with open(self.filepath, "wb") as fh:
            fh.write(data)

        same = data == original
        for k in warn:
            report.append(k)
        for line in report:
            print("[c3] " + line)
        self.report(
            {'INFO'},
            f"{os.path.basename(self.filepath)}: {rebuilt} track(s), "
            f"{len(data)} bytes"
            + (" -- BYTE-IDENTICAL to the source" if same
               else " -- changed from the source"))
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
    export_animation: BoolProperty(
        name="Rebuild motion tracks", default=True,
        description="Write each imported MOTI track back from its armature "
                    "action, in the encoding it arrived in. Every bone key "
                    "whose pose is unchanged is re-emitted from the original "
                    "on-disk floats, so an untouched animation is "
                    "byte-identical. Turn OFF to carry every track through "
                    "verbatim and ignore any animation edits")
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
        c.prop(self, "export_animation")
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
                export_animation=self.export_animation,
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

class C3_OT_pick_texture(bpy.types.Operator):
    """Choose a different texture from the ranked candidates.

    The list is `meshtex`'s, best first, each row showing the RULE and its
    MEASURED confidence -- so picking is an informed choice rather than a
    guess between equal-looking paths. Assigning does not edit the `.c3`:
    a mesh does not name its texture, the appearance tables do. This changes
    what BLENDER shows, which is what you need to judge a swap before making
    one.
    """
    bl_idname = "c3.pick_texture"
    bl_label = "Texture candidates"
    bl_options = {'REGISTER', 'UNDO'}

    def _items(self, context):
        ob = context.object
        rows = (ob.get("c3_texture_ranked_by") or []) if ob else []
        out = []
        for i, row in enumerate(rows):
            parts = str(row).split(" ", 2)
            label = parts[2] if len(parts) == 3 else str(row)
            conf = parts[1] if len(parts) == 3 else "?"
            meth = parts[0] if len(parts) == 3 else "?"
            out.append((str(i), f"{label}  --  {meth} {conf}", ""))
        return out or [("0", "(none)", "")]

    choice: EnumProperty(name="Candidate", items=_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        ob = context.object
        alts = list(ob.get("c3_texture_alternatives") or [])
        rows = list(ob.get("c3_texture_ranked_by") or [])
        try:
            i = int(self.choice)
            logical = alts[i]
        except Exception:
            self.report({'ERROR'}, "no such candidate")
            return {'CANCELLED'}
        root = game_root(context)
        if not root:
            self.report({'ERROR'}, "no game install root configured")
            return {'CANCELLED'}
        tr = c3_material.get_resolver(root)
        loc = None
        try:
            loc = tr.assets.resolve_asset(logical, "texture")
        except Exception:
            loc = None
        if loc is None:
            self.report({'ERROR'}, f"cannot resolve {logical}")
            return {'CANCELLED'}
        try:
            path = tr.materialize(loc)
        except Exception as e:
            self.report({'ERROR'}, f"cannot load {logical}: {e}")
            return {'CANCELLED'}
        for mat in ob.data.materials:
            if not mat or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE':
                    node.image = bpy.data.images.load(str(path),
                                                      check_existing=True)
        ob["c3_texture"] = logical
        ob["c3_texture_source"] = getattr(loc, "source", "?")
        if 0 <= i < len(rows):
            parts = str(rows[i]).split(" ", 2)
            if len(parts) == 3:
                ob["c3_texture_method"] = parts[0]
                try:
                    ob["c3_texture_confidence"] = float(
                        parts[1].rstrip("%")) / (100.0 if "%" in parts[1]
                                                 else 1.0)
                except Exception:
                    pass
        # The user has overridden the ranking, so "do these agree" is no
        # longer a question about the two lookups. Recorded as chosen rather
        # than silently left reading as agreement.
        ob["c3_texture_agrees"] = True
        ob["c3_texture_chosen_by_user"] = True
        self.report({'INFO'}, f"texture set to {logical}")
        return {'FINISHED'}


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
        meth = ob.get("c3_texture_method")
        if meth:
            conf = float(ob.get("c3_texture_confidence", 0.0))
            kind = ob.get("c3_texture_kind", "?")
            # AUTHORED means a shipped table says so; INFERRED means a naming
            # convention whose precision was measured. The icon carries that
            # difference because it is the first thing to read.
            icon = 'CHECKMARK' if kind == "authored" else 'QUESTION'
            if conf < 0.5:
                icon = 'ERROR'
            col.label(text=f"{meth} {conf:.0%} ({kind})", icon=icon)
            if conf < 0.5:
                # meshtex's own docstring calls the 0.30 rule a shortlist
                # rather than an answer, and a panel that shows the texture
                # without that sentence is quietly overstating it.
                col.label(text="a shortlist, not an answer", icon='INFO')
            if ob.get("c3_texture_agrees") is False:
                col.label(text="ranking disagrees with the loaded file",
                          icon='ERROR')
            alts = ob.get("c3_texture_ranked_by") or []
            if len(alts) > 1:
                col.operator(C3_OT_pick_texture.bl_idname,
                             text=f"{len(alts)} candidates…", icon='TEXTURE')
        if ob.get("c3_two_sided"):
            col.label(text="2SID (two-sided)")
        if ob.get("c3_billboard"):
            col.label(text=f"billboard mode {ob['c3_billboard']}")
        arm = ob.find_armature()
        if arm is not None and c3_anim.PROP_ENC in arm:
            col.separator()
            col.label(text=f"MOTI {arm[c3_anim.PROP_ENC]}, "
                           f"{len(arm.get(c3_anim.PROP_FRAMES, []))} key(s)",
                      icon='ANIM_DATA')
            col.label(text=f"frameCount "
                           f"{arm.get(c3_anim.PROP_FRAMECOUNT, '?')}")
        col.separator()
        col.operator("export_scene.co_c3", icon='EXPORT')


# --------------------------------------------------------------------------

def menu_import(self, context):
    self.layout.operator(C3_OT_import.bl_idname,
                         text="Conquer Online Mesh (.c3)")
    self.layout.operator(C3_OT_load_motion.bl_idname,
                         text="Conquer Online Motion Set (.c3)")


def menu_export(self, context):
    self.layout.operator(C3_OT_export.bl_idname,
                         text="Conquer Online Mesh (.c3)")
    self.layout.operator(C3_OT_export_motion.bl_idname,
                         text="Conquer Online Motion Set (.c3)")


_classes = (C3AddonPreferences, C3_OT_forget_textures, C3_OT_pick_texture,
            C3_OT_import,
    C3_OT_load_motion, C3_OT_export, C3_OT_export_motion,
    C3_PT_mesh_info)


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
