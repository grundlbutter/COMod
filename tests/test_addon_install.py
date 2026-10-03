r"""
test_addon_install.py -- prove build/io_scene_c3.zip installs and registers in
a clean Blender, and that the File > Import/Export entries appear.

    & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" \
        --background --python tests\test_addon_install.py
"""

import os
import sys

try:
    import bpy
except ModuleNotFoundError as _exc:                           # not inside Blender
    # `unittest` discovery reports a module-level SkipTest as a SKIP; a bare
    # ModuleNotFoundError it reports as an ERROR. This file only ever runs under
    # Blender's bundled Python, so on a plain interpreter "absent" is the
    # correct answer and not a failure -- without this, adding `tests/__init__.py`
    # would have made `unittest discover -s tests` red for everyone, since
    # nobody's system Python has `bpy`.
    import unittest
    raise unittest.SkipTest(
        "needs Blender's bundled Python (no `bpy` module). Run it the way the "
        "docstring above says: blender --background --python "
        "tests/test_addon_install.py") from _exc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(REPO, "build", "io_scene_c3.zip")

ok = True


def check(cond, label):
    global ok
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")
    ok = ok and bool(cond)


def main():
    print(f"\n=== addon install test ===\n  zip: {ZIP}")
    check(os.path.isfile(ZIP), "zip exists (run tools/build_addon.py first)")
    if not os.path.isfile(ZIP):
        sys.exit(1)

    module = None
    # Blender 4.2+ installs zips with a blender_manifest.toml as extensions.
    try:
        bpy.ops.extensions.package_install_files(
            filepath=ZIP, repo='user_default', enable_on_install=True)
        module = "bl_ext.user_default.io_scene_c3"
        print("  installed as an extension")
    except Exception as e:
        print(f"  extension install unavailable ({e}); trying legacy")
        bpy.ops.preferences.addon_install(filepath=ZIP, overwrite=True)
        bpy.ops.preferences.addon_enable(module="io_scene_c3")
        module = "io_scene_c3"
        print("  installed as a legacy add-on")

    check(module in bpy.context.preferences.addons,
          f"registered as {module!r}")

    check(hasattr(bpy.ops.import_scene, "co_c3"), "import operator present")
    check(hasattr(bpy.ops.export_scene, "co_c3"), "export operator present")

    p = bpy.context.preferences.addons[module].preferences
    check(hasattr(p, "game_root"), "preferences expose game_root")
    print(f"       game_root default: {getattr(p, 'game_root', None)!r}")

    # A real import through the operator, not the python API.  The install
    # root comes from the add-on's own preference, whose default is whatever
    # vendor/coroot.py auto-detected -- so this also proves the installed
    # extension located the game without a hardcoded path.
    root = bpy.path.abspath(p.game_root) if getattr(p, "game_root", "") else ""
    sample = os.path.join(root, "c3", "mesh", "002192465.c3") if root else ""
    if not root:
        print("       no install root detected; skipping the import round trip")
    if os.path.isfile(sample):
        # NB: do NOT call read_factory_settings here -- it resets preferences,
        # which disables the add-on we just installed.
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        r = bpy.ops.import_scene.co_c3(filepath=sample,
                                       import_materials=True,
                                       import_armature=True)
        check(r == {'FINISHED'}, f"operator import of {os.path.basename(sample)}")
        objs = [o for o in bpy.data.objects if "c3_tag" in o]
        check(len(objs) == 4, f"4 mesh objects created (got {len(objs)})")
        # ONE ARMATURE PER MESH since e8d2441a (2026-09-06): bone i of mesh 0
        # and bone i of mesh 1 index DIFFERENT MOTI chunks, so a shared
        # armature would overwrite one action with the other. See the
        # c3_import module docstring. This line asserted 1 until 2026-09-16.
        # A count alone would pass if two meshes shared one armature while a
        # stray armature was also made, so also require that each mesh binds
        # its OWN armature.
        arm = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        check(len(arm) == len(objs),
              f"one armature per mesh: {len(objs)} (got {len(arm)})")
        bound = [md.object for o in objs for md in o.modifiers
                 if md.type == 'ARMATURE' and md.object is not None]
        check(len(bound) == len(objs) and len(set(bound)) == len(bound)
              and all(b in arm for b in bound),
              f"each mesh binds a distinct armature "
              f"({len(set(bound))} distinct of {len(bound)} bound)")
        if arm:
            print(f"       {len(arm[0].data.bones)} bones in the palette")
        mats = [m for o in objs for m in o.data.materials]
        imgs = [n.image.name for m in mats if m.node_tree
                for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
        check(bool(imgs), f"texture wired into a material: {imgs[:2]}")

        import tempfile
        out = os.path.join(tempfile.mkdtemp(), "out.c3")
        bpy.context.view_layer.objects.active = objs[0]
        r = bpy.ops.export_scene.co_c3(filepath=out)
        check(r == {'FINISHED'}, "operator export")
        with open(sample, "rb") as fh:
            a = fh.read()
        with open(out, "rb") as fh:
            b = fh.read()
        check(a == b, "operator round trip is byte-identical")
        # ---- phase 1b: do the lookups survive a RENAME? -----------------
        # Runs LAST on purpose: it renames every bone, so anything after it
        # would be testing a deliberately broken armature.
        #
        # WHY THIS ARM IS WRITTEN BEFORE THE FIX AND EXPECTED TO FAIL:
        # `c3_anim` resolves bones BY NAME (`bone_names()` -> `bone_%03d`).
        # A retargeter renames bones -- that is the whole point of phase 1b --
        # and the failure is SILENT, which is what makes this arm necessary
        # rather than optional:
        #
        #   _pose_bones   -> None                 (detectable)
        #   live_parents  -> every parent None    (SILENT: a flat skeleton)
        #   rest_locals   -> N x IDENTITY         (SILENT: `.get()` misses and
        #                                          the code substitutes
        #                                          `Matrix.Identity(4)`)
        #
        # So this asserts the CONTENT is preserved, not that nothing raised.
        # An arm checking only "no exception" passes against the broken code,
        # which is the family that was green-while-wrong seven times on
        # 2026-09-20.
        # THE MODULE NAME COMES FROM WHAT THIS TEST ACTUALLY INSTALLED.
        # `module` is `bl_ext.user_default.io_scene_c3` on the extension path
        # and `io_scene_c3` on the legacy one -- both are live above, so
        # hard-coding either is wrong on the other. `test_blender_roundtrip`
        # gets away with the bare name because it puts the package on
        # `sys.path` itself; an INSTALL test deliberately does not, which is
        # the whole point of it.
        #
        # AND THE IMPORT IS CAUGHT SEPARATELY, on purpose. A single broad
        # `except` around the whole arm turned "the arm could not run" into a
        # `check(False, ...)` that read like a result -- it produced a RED on
        # a missing module, which is true before the fix AND after it, so the
        # arm was not sensitive to the defect at all while looking exactly
        # like the evidence it was meant to be. A control that fires on the
        # wrong thing is worse than one that cannot fire, because it gets
        # filed as proof.
        c3_anim = None
        try:
            import importlib
            from mathutils import Matrix as _M
            c3_anim = importlib.import_module(module + ".c3_anim")
        except Exception as e:                                   # noqa: BLE001
            check(False, "ARM COULD NOT RUN (not a result): importing "
                         "%s.c3_anim raised %r" % (module, e))
        try:
            a_ob = bound[0] if bound else (arm[0] if arm else None)
            bc = len(a_ob.data.bones) if a_ob is not None else 0
            if c3_anim is not None and a_ob is not None and bc:
                before_rest = c3_anim.rest_locals(a_ob, bc)
                before_par = c3_anim.live_parents(a_ob, bc)
                ident = [float(v) for row in _M.Identity(4) for v in row] * bc
                # THE FIXTURE HAS TO BE ABLE TO SHOW A DIFFERENCE. If the rest
                # pose were already all-identity, "unchanged after rename"
                # would hold for the BROKEN code too and this whole arm would
                # be decorative.
                check(before_rest != ident,
                      "fixture is meaningful: rest pose is not all-identity")
                check(any(v is not None for v in before_par.values()),
                      "fixture is meaningful: the armature has real parenting")

                for i, b in enumerate(a_ob.data.bones):
                    b.name = "retargeted_zzz_%03d" % i

                after_rest = c3_anim.rest_locals(a_ob, bc)
                after_par = c3_anim.live_parents(a_ob, bc)
                after_pose = c3_anim._pose_bones(a_ob, bc)
                check(after_rest == before_rest,
                      "rest_locals resolves by index after a full rename")
                check(after_par == before_par,
                      "live_parents resolves by index after a full rename")
                check(after_pose is not None and len(after_pose) == bc,
                      "_pose_bones resolves by index after a full rename")
        except Exception as e:                                   # noqa: BLE001
            check(False, "ARM COULD NOT RUN (not a result): %r" % (e,))


    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
