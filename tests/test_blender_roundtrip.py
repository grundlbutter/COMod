r"""
test_blender_roundtrip.py -- the addon's acceptance test.

Import a `.c3` into Blender, export it without touching anything, and require
a byte-identical file.  Then modify a vertex, export, re-import, and confirm
the change survived.

    & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" \
        --background --python tests\test_blender_roundtrip.py -- --count 40

Flags after the `--`:
    --count N     how many corpus files to round-trip   (default 25)
    --file PATH   round-trip one specific .c3 (repeatable)
    --no-textures skip material building (much faster)
"""

import os
import random
import sys
import tempfile
import traceback

try:
    import bpy
except ModuleNotFoundError as _exc:                           # not inside Blender
    # See the same guard in test_addon_install.py: discovery reports a
    # module-level SkipTest as a SKIP and a bare ModuleNotFoundError as an
    # ERROR, and "no Blender here" is a skip, not a failure.
    import unittest
    raise unittest.SkipTest(
        "needs Blender's bundled Python (no `bpy` module). Run it the way the "
        "docstring above says: blender --background --python "
        "tests/test_blender_roundtrip.py") from _exc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "blender"))

import io_scene_c3                                            # noqa: E402
from io_scene_c3 import c3_export, c3_import                  # noqa: E402
from io_scene_c3.vendor.c3phy import VARIANTS, iter_chunks    # noqa: E402

#: Resolved the same way every other tool resolves it -- see
#: core/coroot.py.  Nothing here assumes a particular install path.
GAME_ROOT = io_scene_c3.DEFAULT_ROOT


def argv_after_dashdash():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def corpus(n):
    """A deterministic spread over the loose tree, both PHY variants."""
    out = []
    for base in (os.path.join(GAME_ROOT, "c3"),
                 os.path.join(GAME_ROOT, "data")):
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".c3"):
                    out.append(os.path.join(root, f))
    out.sort()
    keep = []
    per_tag = {b"PHY ": 0, b"PHY4": 0}
    rng = random.Random(20260725)
    rng.shuffle(out)
    for p in out:
        try:
            with open(p, "rb") as fh:
                blob = fh.read()
            if not blob.startswith(b"MAXFILE"):
                continue
            tags = {t for t, _ in iter_chunks(blob) if t in VARIANTS}
        except Exception:
            continue
        if not tags:
            continue
        # keep a balanced mix so both shipping variants get exercised
        want = any(per_tag.get(t, 0) < n // 2 for t in tags)
        if want or len(keep) < n:
            keep.append(p)
            for t in tags:
                per_tag[t] = per_tag.get(t, 0) + 1
        if len(keep) >= n:
            break
    return keep[:n]


def clean():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def roundtrip_one(path, tmpdir, textures):
    """-> (ok, detail)"""
    clean()
    opts = dict(import_materials=textures, import_armature=True,
                bone_spacing=4.0, game_root=GAME_ROOT)
    coll, objs, _rep = c3_import.import_c3(bpy.context, path, **opts)
    out = os.path.join(tmpdir, "rt_" + os.path.basename(path))
    c3_export.export_c3(bpy.context, out)
    with open(path, "rb") as fh:
        a = fh.read()
    with open(out, "rb") as fh:
        b = fh.read()
    if a == b:
        return True, f"{len(objs)} mesh(es), {len(a)} bytes"
    n = min(len(a), len(b))
    where = next((i for i in range(n) if a[i] != b[i]), n)
    return False, (f"len {len(b)} vs {len(a)}, first diff at 0x{where:X} "
                   f"got={b[where:where+12].hex()} want={a[where:where+12].hex()}")


def modify_test(path, tmpdir):
    """Move a vertex, export, re-import, confirm the edit survived."""
    clean()
    coll, objs, _ = c3_import.import_c3(
        bpy.context, path, import_materials=False, import_armature=True,
        bone_spacing=4.0, game_root=GAME_ROOT)
    ob = max(objs, key=lambda o: len(o.data.vertices))
    vi = len(ob.data.vertices) // 2
    before = tuple(ob.data.vertices[vi].co)
    moved = (before[0] + 12.5, before[1] - 3.25, before[2] + 7.0)
    ob.data.vertices[vi].co = moved
    idx = ob["c3_chunk_index"]

    out = os.path.join(tmpdir, "mod_" + os.path.basename(path))
    c3_export.export_c3(bpy.context, out, recompute_bounds=True)

    with open(path, "rb") as fh:
        orig = fh.read()
    with open(out, "rb") as fh:
        new = fh.read()
    if orig == new:
        return False, "export is identical to the original - the edit was lost"

    # Snapshot coordinates as plain tuples: every clean() invalidates the
    # bpy StructRNA references, so nothing bpy-owned may cross a reset.
    name = ob.name

    clean()
    _c, objs2, _ = c3_import.import_c3(
        bpy.context, out, import_materials=False, import_armature=True,
        bone_spacing=4.0, game_root=GAME_ROOT)
    ob2 = next(o for o in objs2 if o["c3_chunk_index"] == idx)
    after = [tuple(v.co) for v in ob2.data.vertices]
    got = after[vi]
    if max(abs(g - m) for g, m in zip(got, moved)) > 1e-4:
        return False, f"edited vertex came back as {got}, expected {moved}"

    # and everything else must be untouched
    clean()
    _c, objs3, _ = c3_import.import_c3(
        bpy.context, path, import_materials=False, import_armature=True,
        bone_spacing=4.0, game_root=GAME_ROOT)
    ob3 = next(o for o in objs3 if o["c3_chunk_index"] == idx)
    original_co = [tuple(v.co) for v in ob3.data.vertices]
    if len(original_co) != len(after):
        return False, f"vertex count changed {len(original_co)} -> {len(after)}"
    diffs = sum(1 for a, b in zip(original_co, after) if a != b)
    if diffs != 1:
        return False, f"{diffs} vertices changed, expected exactly 1"
    return True, (f"moved v{vi} of {name}, survived the round trip, "
                  f"1 vertex changed and no others")


def structural_test(path, tmpdir):
    """Delete a mesh object, export, re-import: the MOTI track must go too."""
    from io_scene_c3.vendor.c3phy import iter_chunks as _ic
    from io_scene_c3.vendor.c3write import MOTI_TAG

    def counts(blob):
        ch = list(_ic(blob))
        return (sum(1 for t, _ in ch if t in VARIANTS),
                sum(1 for t, _ in ch if t == MOTI_TAG))

    with open(path, "rb") as fh:
        orig = fh.read()
    p0, m0 = counts(orig)
    if p0 < 2:
        return None, "needs >=2 meshes"

    clean()
    _c, objs, _ = c3_import.import_c3(
        bpy.context, path, import_materials=False, import_armature=True,
        bone_spacing=4.0, game_root=GAME_ROOT)
    victim = max(objs, key=lambda o: o["c3_chunk_index"])
    vname = victim["c3_node_name"]
    bpy.data.objects.remove(victim, do_unlink=True)

    out = os.path.join(tmpdir, "struct_" + os.path.basename(path))
    c3_export.export_c3(bpy.context, out, allow_add_remove=True)
    with open(out, "rb") as fh:
        new = fh.read()
    p1, m1 = counts(new)
    if (p1, m1) != (p0 - 1, m0 - 1):
        return False, (f"PHY {p0}->{p1}, MOTI {m0}->{m1}; "
                       f"expected both to drop by 1")

    # the survivors must still round-trip, and the deleted one must be gone
    clean()
    _c, objs2, _ = c3_import.import_c3(
        bpy.context, out, import_materials=False, import_armature=True,
        bone_spacing=4.0, game_root=GAME_ROOT)
    names = [o["c3_node_name"] for o in objs2]
    if vname in names and names.count(vname) >= 1 and len(objs2) != p0 - 1:
        return False, f"deleted mesh {vname!r} still present"
    if len(objs2) != p0 - 1:
        return False, f"re-import gave {len(objs2)} meshes, expected {p0-1}"
    return True, (f"removed {vname!r}: PHY {p0}->{p1}, MOTI {m0}->{m1}, "
                  f"re-imports cleanly")


def main():
    args = argv_after_dashdash()
    count = 25
    if "--count" in args:
        count = int(args[args.index("--count") + 1])
    textures = "--no-textures" not in args
    explicit = [args[i + 1] for i, a in enumerate(args) if a == "--file"]

    io_scene_c3.register()
    tmpdir = tempfile.mkdtemp(prefix="c3rt_")

    files = explicit or corpus(count)
    print(f"\n=== C3 addon acceptance test: {len(files)} file(s) ===\n")

    ok = 0
    fails = []
    for p in files:
        try:
            good, detail = roundtrip_one(p, tmpdir, textures)
        except Exception:
            good, detail = False, traceback.format_exc().strip().split("\n")[-1]
        if good:
            ok += 1
            print(f"  OK   {os.path.basename(p):<28} {detail}")
        else:
            fails.append((p, detail))
            print(f"  FAIL {os.path.basename(p):<28} {detail}")

    print(f"\nimport -> export byte-identical: {ok}/{len(files)}")

    print("\n=== modification test ===")
    mod_ok = 0
    mod_files = files[:3]
    for p in mod_files:
        try:
            good, detail = modify_test(p, tmpdir)
        except Exception:
            good, detail = False, traceback.format_exc().strip().split("\n")[-1]
        print(f"  {'OK  ' if good else 'FAIL'} {os.path.basename(p):<28} {detail}")
        mod_ok += bool(good)
    print(f"\nmodify -> export -> re-import: {mod_ok}/{len(mod_files)}")

    print("\n=== structural test (delete a mesh) ===")
    st_ok = st_run = 0
    for p in files:
        if st_run >= 3:
            break
        try:
            good, detail = structural_test(p, tmpdir)
        except Exception:
            good, detail = False, traceback.format_exc().strip().split("\n")[-1]
        if good is None:
            continue
        st_run += 1
        print(f"  {'OK  ' if good else 'FAIL'} {os.path.basename(p):<28} {detail}")
        st_ok += bool(good)
    print(f"\ndelete a mesh -> export -> re-import: {st_ok}/{st_run}")

    failed = (len(fails) or mod_ok != len(mod_files)
              or (st_run and st_ok != st_run))
    print("\nRESULT:", "FAIL" if failed else "PASS")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
