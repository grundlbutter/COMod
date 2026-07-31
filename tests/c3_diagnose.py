r"""
c3_diagnose.py -- when a Blender round trip is not byte-identical, say WHICH
FIELD moved, not just which offset.

    & "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" \
        --background --python tests\c3_diagnose.py -- <file.c3> [...]
"""

import os
import struct
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "blender"))

import io_scene_c3                                            # noqa: E402
from io_scene_c3 import c3_export, c3_import                  # noqa: E402
from io_scene_c3.vendor.c3phy import (VARIANTS, iter_chunks,  # noqa: E402
                                      parse_phy)

#: Resolved the same way every other tool resolves it -- see
#: core/coroot.py.  Nothing here assumes a particular install path.
GAME_ROOT = io_scene_c3.DEFAULT_ROOT

VFIELDS = ("px", "py", "pz", "u0", "v0", "unknown4", "bone0", "bone1",
           "weight0", "weight1", "nx", "ny", "nz", "u1", "v1", "gap")
MFIELDS = ("name_raw", "unknown0", "vertex_count_a", "vertex_count_b",
           "face_count_a", "face_count_b", "label_raw", "bbox_a", "bbox_b",
           "matrix", "frame_count", "step", "two_sided", "billboard",
           "phy5_raw", "tail_raw")


def bits(x):
    return struct.pack("<f", x).hex() if isinstance(x, float) else x


def same(x, y):
    """Bitwise for floats, so -0.0 != 0.0 and NaN payloads show up."""
    if isinstance(x, float) and isinstance(y, float):
        return struct.pack("<f", x) == struct.pack("<f", y)
    if isinstance(x, (tuple, list)) and isinstance(y, (tuple, list)):
        return len(x) == len(y) and all(same(p, q) for p, q in zip(x, y))
    return x == y


def diff_mesh(a, b, tag, limit=8):
    out = []
    for f in MFIELDS:
        va, vb = getattr(a, f), getattr(b, f)
        if not same(va, vb):
            out.append(f"  header.{f}: {va!r} -> {vb!r}")
    ka = [(len(a.keys.alphas), len(a.keys.draws), len(a.keys.change_texs))]
    kb = [(len(b.keys.alphas), len(b.keys.draws), len(b.keys.change_texs))]
    if ka != kb:
        out.append(f"  keys: {ka} -> {kb}")
    if len(a.faces) != len(b.faces):
        out.append(f"  faces: {len(a.faces)} -> {len(b.faces)}")
    else:
        nf = sum(1 for x, y in zip(a.faces, b.faces) if x != y)
        if nf:
            i = next(i for i, (x, y) in enumerate(zip(a.faces, b.faces))
                     if x != y)
            out.append(f"  faces: {nf} differ, first #{i} "
                       f"{a.faces[i]} -> {b.faces[i]}")
    if len(a.vertices) != len(b.vertices):
        out.append(f"  vertices: {len(a.vertices)} -> {len(b.vertices)}")
        return out
    per = {}
    first = {}
    for i, (va, vb) in enumerate(zip(a.vertices, b.vertices)):
        for f in VFIELDS:
            xa, xb = getattr(va, f), getattr(vb, f)
            if not same(xa, xb):
                per[f] = per.get(f, 0) + 1
                first.setdefault(f, (i, xa, xb))
    for f, n in sorted(per.items(), key=lambda t: -t[1]):
        i, xa, xb = first[f]
        out.append(f"  vertex.{f}: {n}/{len(a.vertices)} differ; "
                   f"first v{i}: {xa!r} [{bits(xa)}] -> {xb!r} [{bits(xb)}]")
    return out


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    io_scene_c3.register()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="c3diag_")
    for path in args:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        c3_import.import_c3(bpy.context, path, import_materials=False,
                            import_armature=True, bone_spacing=4.0,
                            game_root=GAME_ROOT)
        out = os.path.join(tmp, os.path.basename(path))
        c3_export.export_c3(bpy.context, out)
        with open(path, "rb") as fh:
            A = fh.read()
        with open(out, "rb") as fh:
            B = fh.read()
        print(f"\n=== {path}")
        if A == B:
            print("  byte-identical")
            continue
        ca = [(t, b) for t, b in iter_chunks(A) if t in VARIANTS]
        cb = [(t, b) for t, b in iter_chunks(B) if t in VARIANTS]
        for i, ((ta, ba), (tb, bb)) in enumerate(zip(ca, cb)):
            if ba == bb:
                continue
            ma, mb = parse_phy(ta, ba), parse_phy(tb, bb)
            print(f"  chunk #{i} {ta.decode('latin-1')} {ma.name!r}"
                  f"  ({len(ba)} -> {len(bb)} bytes)")
            for line in diff_mesh(ma, mb, ta):
                print(line)


if __name__ == "__main__":
    main()
