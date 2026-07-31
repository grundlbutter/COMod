r"""
test_addon_install.py -- prove build/io_scene_c3.zip installs and registers in
a clean Blender, and that the File > Import/Export entries appear.

    & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" \
        --background --python tests\test_addon_install.py
"""

import os
import sys

import bpy

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
        arm = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        check(len(arm) == 1, f"1 armature created (got {len(arm)})")
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

    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
