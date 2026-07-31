#!/usr/bin/env python3
r"""
test_viewer.py -- tests for the non-GUI half of the asset viewer.

    py -3 tools/test_viewer.py            # everything
    py -3 tools/test_viewer.py -v         # verbose
    py -3 tools/test_viewer.py DdsSynthetic

Three groups:

  DdsSynthetic     hand-built BC1/BC2/BC3/uncompressed blocks with values
                   worked out by hand -- checks the decoder against the spec
                   with no other decoder involved.
  DdsCorpus        every DDS pixel-format class that actually ships in this
                   install, decoded by core/dds.py and by Pillow's own
                   independent C decoder, compared byte for byte.
  MeshPipeline     the geometry the viewport receives, cross-checked against
                   core/c3phy.py directly and against the known-good OBJ
                   exports in out/c3/obj/.
  Resolution       loose-shadows-archive provenance, the thing the whole
                   modding workflow rests on.
  Swap             preview -> stage -> comod.py sees it -> unstage, with an
                   explicit assertion that the game install is never touched.

The corpus/pipeline/resolution/swap groups need the real install; they skip
cleanly if it is absent.  Nothing here writes to the install, and the staging
test redirects coviewer.STAGE at a temporary directory so it cannot disturb a
real mods/stage.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))

import bodyfacets                                # noqa: E402
import c3phy                                     # noqa: E402
import dds                                       # noqa: E402
from coassets import DEFAULT_ROOT                # noqa: E402

ROOT = DEFAULT_ROOT
HAVE_ROOT = ROOT.is_dir()
try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:                              # pragma: no cover
    HAVE_PIL = False


def _thumbs_rendered(kind: str = "meshes") -> bool:
    """True only when a *complete* thumbnail corpus is on disk.

    Generation is opt-in and can legitimately be partial -- the viewer offers
    a meshes-only run and a `--limit N` taste run -- so the presence of a
    manifest proves nothing about its size.  Tests that assert corpus-wide
    counts skip unless the render really covered the corpus; anything less
    would fail for a reason that is a user's deliberate choice, not a bug.
    """
    name = "manifest_meshes.json" if kind == "meshes" else "manifest.json"
    p = PROJECT / "out" / "thumbs" / name
    if not p.is_file():
        return False
    try:
        counts = json.loads(p.read_text("utf-8")).get("counts") or {}
    except (OSError, ValueError):
        return False
    if kind == "meshes":
        return int(counts.get("meshes", 0) or 0) >= 4000
    return int(counts.get("textures", 0) or 0) >= 60000



# ---------------------------------------------------------------------------
# helpers to build DDS files by hand
# ---------------------------------------------------------------------------

def make_dds(width, height, fourcc=b"", payload=b"", *, rgb_bits=0, masks=(0, 0, 0, 0),
             pf_flags=None, mips=0) -> bytes:
    """Assemble a minimal but standards-correct DDS_HEADER + payload."""
    if pf_flags is None:
        pf_flags = 0x4 if fourcc else 0x41
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    struct.pack_into("<I", hdr, 4, 124)                 # dwSize
    struct.pack_into("<I", hdr, 8, 0x1007 | (0x20000 if mips else 0))
    struct.pack_into("<II", hdr, 12, height, width)
    struct.pack_into("<I", hdr, 28, mips)
    struct.pack_into("<I", hdr, 76, 32)                 # DDS_PIXELFORMAT.dwSize
    struct.pack_into("<I", hdr, 80, pf_flags)
    struct.pack_into("<4s", hdr, 84, fourcc or b"\0\0\0\0")
    struct.pack_into("<I", hdr, 88, rgb_bits)
    struct.pack_into("<4I", hdr, 92, *masks)
    return bytes(hdr) + payload


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def px(rgba: bytes, w: int, x: int, y: int):
    o = (y * w + x) * 4
    return tuple(rgba[o:o + 4])


# ---------------------------------------------------------------------------

class DdsSynthetic(unittest.TestCase):
    """Decoder vs. the block-compression spec, with no second decoder involved.

    The expected values are computed from the S3TC rules by hand:
      * RGB565 expansion replicates the high bits into the low ones
        (r8 = (r5<<3)|(r5>>2)), which is what D3D/OpenGL both specify.
      * BC1 with c0 > c1 gives a 4-colour block, c2 = (2*c0 + c1)/3.
      * BC1 with c0 <= c1 gives 3 colours + transparent black.
      * BC2 and BC3 ALWAYS use the 4-colour rule regardless of c0/c1 ordering.
        (Getting this wrong is the classic silent DDS bug, so it gets its own
        test below.)
    """

    def _bc1_block(self, c0, c1, indices):
        bits = 0
        for i, v in enumerate(indices):
            bits |= (v & 3) << (2 * i)
        return struct.pack("<HHI", c0, c1, bits)

    def test_bc1_four_colour_mode(self):
        c0 = rgb565(255, 0, 0)          # red, larger
        c1 = rgb565(0, 0, 255)          # blue
        self.assertGreater(c0, c1, "test needs c0 > c1 to select 4-colour mode")
        blk = self._bc1_block(c0, c1, [0, 1, 2, 3] + [0] * 12)
        w, h, rgba = dds.decode(make_dds(4, 4, b"DXT1", blk))
        self.assertEqual((w, h), (4, 4))
        r8 = (31 << 3) | (31 >> 2)                       # 255
        b8 = (31 << 3) | (31 >> 2)
        self.assertEqual(px(rgba, 4, 0, 0), (r8, 0, 0, 255))
        self.assertEqual(px(rgba, 4, 1, 0), (0, 0, b8, 255))
        # c2 = (2*c0 + c1) / 3 per channel, integer division
        self.assertEqual(px(rgba, 4, 2, 0), ((2 * r8 + 0) // 3, 0, (2 * 0 + b8) // 3, 255))
        self.assertEqual(px(rgba, 4, 3, 0), ((r8 + 0) // 3, 0, (0 + 2 * b8) // 3, 255))

    def test_bc1_punchthrough_mode(self):
        """c0 <= c1 switches BC1 to 3 colours + a transparent index 3."""
        c0 = rgb565(0, 0, 255)
        c1 = rgb565(255, 0, 0)
        self.assertLess(c0, c1)
        blk = self._bc1_block(c0, c1, [0, 1, 2, 3] + [0] * 12)
        _, _, rgba = dds.decode(make_dds(4, 4, b"DXT1", blk))
        self.assertEqual(px(rgba, 4, 3, 0), (0, 0, 0, 0), "index 3 must be transparent black")
        self.assertEqual(px(rgba, 4, 2, 0)[3], 255)
        # midpoint, not 1/3 point
        self.assertEqual(px(rgba, 4, 2, 0), (255 // 2, 0, 255 // 2, 255))

    def test_bc2_alpha_is_explicit_4bit_and_colour_never_punchthrough(self):
        c0 = rgb565(0, 0, 255)
        c1 = rgb565(255, 0, 0)
        self.assertLess(c0, c1, "deliberately c0 <= c1; BC2 must ignore that")
        alphas = [0, 5, 10, 15] + [15] * 12
        abits = 0
        for i, a in enumerate(alphas):
            abits |= a << (4 * i)
        blk = struct.pack("<Q", abits) + self._bc1_block(c0, c1, [0, 1, 2, 3] + [0] * 12)
        _, _, rgba = dds.decode(make_dds(4, 4, b"DXT3", blk))
        self.assertEqual([px(rgba, 4, x, 0)[3] for x in range(4)],
                         [0, 5 * 17, 10 * 17, 15 * 17])
        # index 3 must be an interpolated colour, NOT transparent black.
        # c0 = blue, c1 = red, so index 3 = (c0 + 2*c1)/3 per channel.
        self.assertEqual(px(rgba, 4, 3, 0)[:3], ((0 + 2 * 255) // 3, 0, (255 + 2 * 0) // 3))
        self.assertEqual(px(rgba, 4, 3, 0)[3], 15 * 17)

    def test_bc3_interpolated_alpha_both_modes(self):
        colour = self._bc1_block(rgb565(255, 255, 255), 0, [0] * 16)
        # a0 > a1 -> 8 interpolated values, no 0/255 endpoints
        idx = 0
        for i, v in enumerate([0, 1, 2, 7] + [0] * 12):
            idx |= v << (3 * i)
        blk = bytes([200, 100]) + idx.to_bytes(6, "little") + colour
        _, _, rgba = dds.decode(make_dds(4, 4, b"DXT5", blk))
        self.assertEqual(px(rgba, 4, 0, 0)[3], 200)
        self.assertEqual(px(rgba, 4, 1, 0)[3], 100)
        self.assertEqual(px(rgba, 4, 2, 0)[3], (6 * 200 + 100) // 7)
        self.assertEqual(px(rgba, 4, 3, 0)[3], (1 * 200 + 6 * 100) // 7)

        # a0 <= a1 -> 6 interpolated values plus hard 0 and 255
        idx = 0
        for i, v in enumerate([0, 1, 6, 7] + [0] * 12):
            idx |= v << (3 * i)
        blk = bytes([100, 200]) + idx.to_bytes(6, "little") + colour
        _, _, rgba = dds.decode(make_dds(4, 4, b"DXT5", blk))
        self.assertEqual(px(rgba, 4, 0, 0)[3], 100)
        self.assertEqual(px(rgba, 4, 1, 0)[3], 200)
        self.assertEqual(px(rgba, 4, 2, 0)[3], 0)
        self.assertEqual(px(rgba, 4, 3, 0)[3], 255)

    def test_non_multiple_of_four_dimensions(self):
        blk = self._bc1_block(rgb565(255, 255, 255), 0, [0] * 16)
        w, h, rgba = dds.decode(make_dds(3, 2, b"DXT1", blk))
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(len(rgba), 3 * 2 * 4)

    def test_uncompressed_a8r8g8b8(self):
        """A8R8G8B8 is stored BGRA in memory; the masks say so and the decoder
        must follow the masks rather than assume a byte order."""
        payload = bytes([0x11, 0x22, 0x33, 0x44])          # B,G,R,A little-endian
        d = make_dds(1, 1, b"", payload, rgb_bits=32,
                     masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000))
        _, _, rgba = dds.decode(d)
        self.assertEqual(tuple(rgba), (0x33, 0x22, 0x11, 0x44))

    def test_uncompressed_r5g6b5(self):
        d = make_dds(1, 1, b"", struct.pack("<H", rgb565(255, 128, 0)), rgb_bits=16,
                     masks=(0xF800, 0x07E0, 0x001F, 0x0000), pf_flags=0x40)
        _, _, rgba = dds.decode(d)
        r, g, b, a = rgba
        self.assertEqual(r, 255)
        self.assertEqual(a, 255, "no alpha mask means opaque")
        self.assertLess(abs(g - 128), 4)
        self.assertEqual(b, 0)

    def test_mip_level_selection(self):
        """surface_size must walk mip levels with the right block sizes."""
        big = self._bc1_block(rgb565(255, 0, 0), 0, [0] * 16) * 4     # 8x8 -> 4 blocks
        small = self._bc1_block(rgb565(0, 255, 0), 0, [0] * 16)       # 4x4 -> 1 block
        tiny = self._bc1_block(rgb565(0, 0, 255), 0, [0] * 16)        # 2x2 -> 1 block
        d = make_dds(8, 8, b"DXT1", big + small + tiny, mips=3)
        self.assertEqual(dds.decode(d, 0)[2][:3], bytes([255, 0, 0]))
        self.assertEqual(dds.decode(d, 1)[:2], (4, 4))
        self.assertEqual(dds.decode(d, 1)[2][:3], bytes([0, 255, 0]))
        self.assertEqual(dds.decode(d, 2)[:2], (2, 2))
        self.assertEqual(dds.decode(d, 2)[2][:3], bytes([0, 0, 255]))

    def test_rejects_garbage(self):
        with self.assertRaises(dds.DdsError):
            dds.parse_header(b"NOPE" + bytes(200))
        with self.assertRaises(dds.DdsError):
            dds.decode(make_dds(64, 64, b"DXT1", b"\0" * 8))     # truncated payload


@unittest.skipUnless(HAVE_ROOT, "game install not present")
@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class DdsCorpus(unittest.TestCase):
    """Every pixel-format class that ships here, ours vs Pillow's decoder.

    Pillow's DDS support is an independent C implementation, so agreement is a
    real cross-check rather than two copies of the same mistake -- and the
    synthetic tests above pin the spec independently of both.
    """

    SAMPLES_PER_CLASS = 12

    @classmethod
    def setUpClass(cls):
        cls.buckets: dict[tuple, list[Path]] = {}
        for sub in ("c3", "data", "graphics", "avatar"):
            d = ROOT / sub
            if not d.is_dir():
                continue
            for p in d.rglob("*.dds"):
                try:
                    hdr = dds.parse_header(p.open("rb").read(148))
                except Exception:
                    continue
                key = (hdr.fourcc or f"RGB{hdr.rgb_bits}", hdr.mipmaps > 0)
                bucket = cls.buckets.setdefault(key, [])
                if len(bucket) < cls.SAMPLES_PER_CLASS:
                    bucket.append(p)
            if len(cls.buckets) >= 6 and all(
                    len(v) >= cls.SAMPLES_PER_CLASS for v in cls.buckets.values()):
                break

    def test_format_classes_found(self):
        """Guard against the corpus silently disappearing and the comparison
        below passing vacuously."""
        fourccs = {k[0] for k in self.buckets}
        self.assertIn("DXT1", fourccs)
        self.assertIn("DXT3", fourccs)

    def test_matches_pillow(self):
        checked = 0
        for key, paths in sorted(self.buckets.items()):
            for p in paths:
                data = p.read_bytes()
                w, h, mine = dds.decode(data)
                with Image.open(io.BytesIO(data)) as im:
                    theirs = im.convert("RGBA")
                self.assertEqual(theirs.size, (w, h), f"{p} size")
                self.assertEqual(theirs.tobytes(), mine,
                                 f"{p} [{key}] decodes differently from Pillow")
                checked += 1
        self.assertGreater(checked, 20, "not enough textures were compared")

    def test_archived_textures(self):
        """The archives are the interesting case: 19,295 DDS payloads that no
        loose file shadows."""
        import coviewer
        cat = coviewer.Catalog(ROOT)
        try:
            names = [n for n in cat.all_paths
                     if n.endswith(".dds") and n not in cat.loose][:400:13]
            self.assertGreater(len(names), 5)
            for n in names:
                data = cat.read(n)
                w, h, mine = dds.decode(data)
                with Image.open(io.BytesIO(data)) as im:
                    self.assertEqual(im.convert("RGBA").tobytes(), mine, n)
        finally:
            cat.close()

    def test_png_round_trip_keeps_format_and_size(self):
        """The swap path: DDS -> PNG -> DDS must come back the same shape and
        the same compressed size (this is what comod.py import-png does)."""
        for key, paths in sorted(self.buckets.items()):
            if key[0] not in ("DXT1", "DXT3", "DXT5") or key[1]:
                continue
            p = paths[0]
            data = p.read_bytes()
            info = dds.info_dict(data)
            png = dds.to_png(data)
            back = dds.encode_png_to_dds(png, info["fourcc"])
            back_info = dds.info_dict(back)
            self.assertEqual((back_info["width"], back_info["height"]),
                             (info["width"], info["height"]), p)
            self.assertEqual(back_info["fourcc"], info["fourcc"], p)
            self.assertEqual(len(back), len(data), f"{p}: size changed on round-trip")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class MeshPipeline(unittest.TestCase):
    """What the WebGL viewport actually receives."""

    OBJ_DIR = PROJECT / "out" / "c3" / "obj"

    @classmethod
    def setUpClass(cls):
        import coviewer
        cls.coviewer = coviewer
        cls.cat = coviewer.Catalog(ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def _json_for(self, logical):
        return self.coviewer.c3_to_json(self.cat.read(logical), logical)

    def test_counts_match_c3phy(self):
        """The JSON must not invent or drop geometry relative to the parser."""
        logical = "c3/mesh/001184365.c3"
        raw = self.cat.read(logical)
        parsed = c3phy.meshes_from_c3(raw)
        j = self._json_for(logical)
        self.assertEqual(len(j["meshes"]), len(parsed))
        for m, p in zip(j["meshes"], parsed):
            self.assertEqual(m["name"], p.name)
            self.assertEqual(m["vertexCount"], p.vertex_count)
            self.assertEqual(m["faceCount"], p.face_count)
            self.assertEqual(len(m["positions"]), p.vertex_count * 3)
            self.assertEqual(len(m["uv0"]), p.vertex_count * 2)
            self.assertEqual(len(m["indices"]), p.face_count * 3)
            self.assertEqual(p.trailing, 0, "chunk must be consumed exactly")

    def test_index_order_and_positions_match_known_good_obj(self):
        """out/c3/obj/ was exported by c3phy.export_obj, which is the reference
        implementation of the same conversion.  Positions must agree to the
        JSON's rounding, and the index order must be *identical* -- reversing a
        triangle would silently invert every front face."""
        if not self.OBJ_DIR.is_dir():
            self.skipTest("out/c3/obj not generated")
        checked = 0
        for stem in ("001184365", "001134070"):
            j = self._json_for(f"c3/mesh/{stem}.c3")
            for m in j["meshes"]:
                objs = list(self.OBJ_DIR.glob(f"{stem}_*_{m['name']}.obj"))
                if not objs:
                    continue
                verts, faces = [], []
                for line in objs[0].read_text().splitlines():
                    if line.startswith("v "):
                        verts.append(tuple(float(x) for x in line.split()[1:]))
                    elif line.startswith("f "):
                        faces.append(tuple(int(t.split("/")[0]) - 1 for t in line.split()[1:]))
                self.assertEqual(m["vertexCount"], len(verts), m["name"])
                pos = m["positions"]
                mine = [tuple(pos[i * 3:i * 3 + 3]) for i in range(m["vertexCount"])]
                worst = max(abs(a - b) for p, q in zip(mine, verts) for a, b in zip(p, q))
                self.assertLess(worst, 1e-3, f"{stem}/{m['name']} positions diverge")
                idx = m["indices"]
                myf = [tuple(idx[i:i + 3]) for i in range(0, len(idx), 3)]
                self.assertEqual(myf, faces, f"{stem}/{m['name']} index order changed")
                checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_matrix_is_applied(self):
        """~7.5% of chunks carry a non-identity matrix and the engine applies it
        at load time (RVA 0x5A735).  Skipping it puts limbs and whole models in
        the wrong place -- the v_body chunk of 001134070 carries a -438 unit Z
        translation, so an importer that ignores it leaves the model floating
        438 units off the ground.
        """
        import copy
        logical = "c3/mesh/001134070.c3"
        raw = self.cat.read(logical)
        parsed = {m.name: m for m in c3phy.meshes_from_c3(raw)}
        j = self._json_for(logical)
        found = 0
        for m in j["meshes"]:
            if m["matrixIdentity"]:
                continue
            found += 1
            src = parsed[m["name"]]
            transformed = copy.deepcopy(src)
            c3phy.apply_matrix_to(transformed)
            pos = m["positions"]

            n = min(300, m["vertexCount"])
            # the JSON must equal the WITH-matrix vertices, in (x, y, -z)
            worst_with = max(
                max(abs(pos[i * 3] - transformed.vertices[i].px),
                    abs(pos[i * 3 + 1] - transformed.vertices[i].py),
                    abs(pos[i * 3 + 2] - (-transformed.vertices[i].pz)))
                for i in range(n))
            self.assertLess(worst_with, 1e-3,
                            f"{m['name']}: JSON does not match the transformed vertices")

            # ...and must differ from the WITHOUT-matrix vertices
            worst_without = max(
                max(abs(pos[i * 3] - src.vertices[i].px),
                    abs(pos[i * 3 + 1] - src.vertices[i].py),
                    abs(pos[i * 3 + 2] - (-src.vertices[i].pz)))
                for i in range(n))
            self.assertGreater(worst_without, 1.0,
                               f"{m['name']} has a non-identity matrix but the output "
                               f"is identical to the untransformed vertices")
        self.assertTrue(found, "expected a non-identity matrix in this file")

    def test_z_is_negated_so_models_stand_up(self):
        """C3 is Z-down; the viewport frame is Z-up.  A v_body mesh must end up
        occupying non-negative Z."""
        j = self._json_for("c3/mesh/001184365.c3")
        body = next(m for m in j["meshes"] if m["name"] == "v_body")
        zmin, zmax = body["bboxRender"][0][2], body["bboxRender"][1][2]
        self.assertGreater(zmax, 100, "a character body should be ~170 units tall")
        self.assertGreater(zmin, -5, "feet should sit near z=0, not below it")

    def test_normals_generated_when_absent(self):
        """PHY and PHY4 -- the only variants shipped -- do not store normals."""
        j = self._json_for("c3/mesh/001184365.c3")
        for m in j["meshes"]:
            self.assertFalse(m["normalsFromFile"])
            n = m["normals"]
            self.assertEqual(len(n), m["vertexCount"] * 3)
            lens = [(n[i * 3] ** 2 + n[i * 3 + 1] ** 2 + n[i * 3 + 2] ** 2) ** 0.5
                    for i in range(m["vertexCount"])]
            unit = sum(1 for L in lens if abs(L - 1.0) < 1e-3)
            self.assertGreater(unit, m["vertexCount"] * 0.95,
                               "generated normals should be unit length")

    def test_winding_is_ccw_front_after_conversion(self):
        """The load-bearing rendering claim.

        After (x, y, -z) with the index order untouched, the OpenGL front-face
        rule cross(p1-p0, p2-p0) must point away from the mesh centroid for the
        majority of a solid body mesh's triangles.  docs/modding.md reports
        482/496 = 97% of meshes by that test; if this drops, the viewport is
        rendering models inside out.
        """
        names = [n for n in self.cat.all_paths
                 if n.startswith("c3/mesh/") and n.endswith(".c3")][::37][:60]
        outward_meshes = total_meshes = 0
        for n in names:
            try:
                j = self._json_for(n)
            except Exception:
                continue
            for m in j["meshes"]:
                if m["name"] != "v_body" or m["faceCount"] < 100:
                    continue
                P, I, nv = m["positions"], m["indices"], m["vertexCount"]
                cx = sum(P[0::3]) / nv
                cy = sum(P[1::3]) / nv
                cz = sum(P[2::3]) / nv
                out = 0
                for k in range(0, len(I), 3):
                    a, b, c = I[k], I[k + 1], I[k + 2]
                    p0, p1, p2 = P[a * 3:a * 3 + 3], P[b * 3:b * 3 + 3], P[c * 3:c * 3 + 3]
                    e1 = [p1[i] - p0[i] for i in range(3)]
                    e2 = [p2[i] - p0[i] for i in range(3)]
                    nx = e1[1] * e2[2] - e1[2] * e2[1]
                    ny = e1[2] * e2[0] - e1[0] * e2[2]
                    nz = e1[0] * e2[1] - e1[1] * e2[0]
                    fx = (p0[0] + p1[0] + p2[0]) / 3 - cx
                    fy = (p0[1] + p1[1] + p2[1]) / 3 - cy
                    fz = (p0[2] + p1[2] + p2[2]) / 3 - cz
                    if nx * fx + ny * fy + nz * fz > 0:
                        out += 1
                total_meshes += 1
                if out * 2 > m["faceCount"]:
                    outward_meshes += 1
        self.assertGreater(total_meshes, 10, "no body meshes were tested")
        ratio = outward_meshes / total_meshes
        self.assertGreater(ratio, 0.90,
                           f"only {ratio:.1%} of body meshes are outward-facing under "
                           f"GL's CCW front-face rule; culling would be inverted")

    def test_uvs_are_sane(self):
        j = self._json_for("c3/mesh/001184365.c3")
        body = next(m for m in j["meshes"] if m["name"] == "v_body")
        uv = body["uv0"]
        self.assertTrue(all(-2.0 <= v <= 3.0 for v in uv),
                        "UVs far outside [0,1] suggest a stride error")
        self.assertGreater(max(uv), 0.5)

    def test_gbk_label_decoding(self):
        """A PHY label is usually the original 3DSMax texture path and a great
        many are GBK Chinese; latin-1 mojibake helps nobody."""
        j = self._json_for("c3/mesh/410000.c3")
        labels = [m["label"] for m in j["meshes"] if m["label"]]
        self.assertTrue(labels, "expected a source-texture label")
        self.assertTrue(any(any(ord(ch) > 0x2000 for ch in L) for L in labels)
                        or all(L.isascii() for L in labels),
                        "label neither valid CJK nor plain ASCII -- decode failed")

    def test_socket_chunks_are_flagged(self):
        j = self._json_for("c3/mesh/001184365.c3")
        by_name = {m["name"]: m for m in j["meshes"]}
        self.assertTrue(by_name["v_armet"]["isSocket"])
        self.assertFalse(by_name["v_body"]["isSocket"])


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class Resolution(unittest.TestCase):
    """Provenance -- 'pointing towards the files in question'."""

    @classmethod
    def setUpClass(cls):
        import coviewer
        cls.coviewer = coviewer
        cls.cat = coviewer.Catalog(ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_archive_only_asset(self):
        pv = self.cat.provenance("c3/texture/002135300.dds")
        self.assertTrue(pv.exists)
        self.assertEqual(pv.source, "c3.wdf")
        self.assertGreater(pv.archive_offset, 0)
        self.assertEqual(pv.archive_size, pv.size)
        self.assertEqual(pv.shadowed_archive, "", "no loose file should shadow this")

    def test_loose_file_wins_over_archive(self):
        """The single fact the whole mod workflow rests on."""
        overrides = [p for p in self.cat.all_paths
                     if p in self.cat.loose and p in self.cat.archived]
        self.assertGreater(len(overrides), 20,
                           "expected loose patches shadowing archive entries")
        differing = 0
        for p in overrides:
            pv = self.cat.provenance(p)
            self.assertEqual(pv.source, "loose", p)
            self.assertTrue(pv.shadowed_archive, p)
            self.assertTrue(Path(pv.real_path).is_file())
            if pv.size != pv.shadowed_size:
                differing += 1
            # and read() must return the loose bytes, not the archive's
            if pv.size != pv.shadowed_size:
                self.assertEqual(len(self.cat.read(p)), pv.size, p)
        self.assertGreater(differing, 0,
                           "at least one override should differ in size from its "
                           "archive copy, proving the loose file really wins")

    def test_texture_inference_for_a_bare_mesh(self):
        """Opening a .c3 with no appearance behind it still needs a skin.
        Two routes: the inverted appearance tables (c3tex) and a same-named
        .dds sibling, which is how the c3/npc and c3/effect trees are laid out.
        """
        self.cat.wait_tables()
        # via the appearance tables
        self.assertEqual(self.cat.texture_for_mesh("c3/mesh/002135000.c3"),
                         "c3/texture/002135300.dds")
        # via the sibling fallback
        for mesh, tex in (("c3/npc/185/1.c3", "c3/npc/185/1.dds"),
                          ("c3/effect/1ghost/1.c3", "c3/effect/1ghost/1.dds")):
            if mesh in self.cat._path_set:
                self.assertEqual(self.cat.texture_for_mesh(mesh), tex)
        # and it must not invent one
        self.assertIsNone(self.cat.texture_for_mesh("c3/mesh/no_such_mesh_zz.c3"))

    def test_alpha_keyframe_decoding(self):
        """C3Key alpha frames are {int nFrame; float fParam; ...} per TQ's
        c3_key.h.  fParam must land in [0,1] and nFrame must be monotonic --
        if the 16-byte stride were wrong these would be garbage."""
        j = self.coviewer.c3_to_json(self.cat.read("c3/effect/1ghost/1.c3"),
                                     "c3/effect/1ghost/1.c3")
        keys = [k for m in j["meshes"] for k in m["keys"]["alphas"]]
        self.assertTrue(keys, "expected an alpha track on this effect mesh")
        for k in keys:
            self.assertGreaterEqual(k["f"], 0.0)
            self.assertLessEqual(k["f"], 1.0)
            self.assertGreaterEqual(k["frame"], 0)
            self.assertLess(k["frame"], 10000)
        for m in j["meshes"]:
            fr = [k["frame"] for k in m["keys"]["alphas"]]
            self.assertEqual(fr, sorted(fr), "keyframes must be ordered")

    def test_missing_asset(self):
        pv = self.cat.provenance("c3/texture/does_not_exist_999.dds")
        self.assertFalse(pv.exists)
        self.assertEqual(pv.source, "")

    def test_appearance_reverse_index(self):
        self.cat.wait_tables()
        refs = self.cat.references("c3/texture/002135300.dds")
        self.assertTrue(refs)
        self.assertTrue(any(r["appearance"] == "002135300" for r in refs))
        self.assertTrue(all(r["kind"] in ("mesh", "texture") for r in refs))

    def test_id_resolution_matches_coassets(self):
        """The in-memory resolver must agree with coassets' filesystem-probing
        one -- it is the same documented rule, just not hitting the disk."""
        from coassets import AssetRoot
        with AssetRoot(ROOT) as R:
            for ident, kind in (("002135300", "texture"), ("002135000", "mesh"),
                                ("410000", "mesh"), ("001184365", "mesh")):
                mine = self.cat.resolve_id(ident, kind)
                theirs = R.resolve_asset(ident, kind)
                self.assertEqual(mine, theirs.logical if theirs else None,
                                 f"{ident}/{kind}")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class Swap(unittest.TestCase):
    """The texture-swap staging path, with STAGE redirected somewhere safe."""

    def setUp(self):
        import coviewer
        self.coviewer = coviewer
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_stage = coviewer.STAGE
        self._orig_preview = coviewer.PREVIEW_DIR
        coviewer.STAGE = Path(self.tmp.name) / "stage"
        coviewer.PREVIEW_DIR = Path(self.tmp.name) / "preview"
        coviewer.PREVIEW_DIR.mkdir(parents=True)
        self.cat = coviewer.Catalog(ROOT)

    def tearDown(self):
        self.cat.close()
        self.coviewer.STAGE = self._orig_stage
        self.coviewer.PREVIEW_DIR = self._orig_preview
        self.tmp.cleanup()

    LOGICAL = "c3/texture/002135300.dds"

    def _recoloured_png(self, data: bytes) -> bytes:
        w, h, rgba = dds.decode(data)
        buf = bytearray(rgba)
        for i in range(0, len(buf), 4):
            buf[i], buf[i + 2] = buf[i + 2], buf[i]      # swap R and B
        im = Image.frombytes("RGBA", (w, h), bytes(buf))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

    def test_encode_preserves_dimensions_and_format(self):
        orig = self.cat.read(self.LOGICAL)
        info = dds.info_dict(orig)
        new = dds.encode_png_to_dds(self._recoloured_png(orig), info["fourcc"])
        new_info = dds.info_dict(new)
        self.assertEqual(new_info["width"], info["width"])
        self.assertEqual(new_info["height"], info["height"])
        self.assertEqual(new_info["fourcc"], info["fourcc"])
        self.assertEqual(len(new), len(orig))
        self.assertNotEqual(new, orig, "the recolour should actually change bytes")

    def test_decoded_swap_differs_visibly(self):
        orig = self.cat.read(self.LOGICAL)
        new = dds.encode_png_to_dds(self._recoloured_png(orig), "DXT3")
        _, _, a = dds.decode(orig)
        _, _, b = dds.decode(new)
        diff = sum(1 for i in range(0, len(a), 4) if abs(a[i] - b[i]) > 24)
        self.assertGreater(diff, len(a) // 40,
                           "swapped texture should differ over a real fraction of pixels")

    def test_staging_writes_only_into_the_stage_tree(self):
        target_in_install = ROOT / self.LOGICAL
        existed_before = target_in_install.exists()

        orig = self.cat.read(self.LOGICAL)
        payload = dds.encode_png_to_dds(self._recoloured_png(orig), "DXT3")
        dest = self.coviewer.STAGE / self.LOGICAL
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_bytes(), payload)
        self.assertEqual(dds.info_dict(dest.read_bytes())["fourcc"], "DXT3")
        self.assertEqual(
            dest.relative_to(self.coviewer.STAGE).as_posix(), self.LOGICAL,
            "the stage tree must mirror the install layout exactly")
        self.assertEqual(target_in_install.exists(), existed_before,
                         "STAGING MUST NOT TOUCH THE GAME INSTALL")

    def test_stage_listing_reports_modified(self):
        orig = self.cat.read(self.LOGICAL)
        payload = dds.encode_png_to_dds(self._recoloured_png(orig), "DXT3")
        dest = self.coviewer.STAGE / self.LOGICAL
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        rows = self.coviewer._staged_files()
        self.assertEqual([p.relative_to(self.coviewer.STAGE).as_posix() for p in rows],
                         [self.LOGICAL])

    def test_preview_token_validation(self):
        """Preview tokens index into a directory; only the generated shape is
        allowed anywhere near a path join."""
        for bad in ("../../etc/passwd", "a/b.dds", "", "x.dds", "zz.png",
                    "../secret.dds"):
            with self.assertRaises(ValueError, msg=bad):
                self.coviewer._safe_token(bad)
        self.assertEqual(self.coviewer._safe_token("0123abcd.dds"), "0123abcd.dds")


class Tags(unittest.TestCase):
    """The tag store. No game install needed -- it is pure data."""

    def setUp(self):
        import tagstore
        self.tagstore = tagstore
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tags.json"
        self.store = tagstore.TagStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_remove(self):
        self.assertEqual(self.store.add(["app:1", "app:2"], ["Favourite", " todo "]), 2)
        self.assertEqual(self.store.get("app:1"), ["favourite", "todo"])
        self.assertEqual(self.store.remove(["app:2"], ["todo"]), 1)
        self.assertEqual(self.store.get("app:2"), ["favourite"])
        self.assertEqual(self.store.get("app:none"), [])

    def test_persists_across_reload(self):
        self.store.add(["app:1"], ["keep-me"])
        again = self.tagstore.TagStore(self.path)
        self.assertEqual(again.get("app:1"), ["keep-me"])

    def test_write_is_atomic_and_keeps_a_backup(self):
        self.store.add(["app:1"], ["first"])
        self.store.add(["app:1"], ["second"])
        self.assertTrue(self.path.with_suffix(".bak").is_file())
        self.assertFalse(any(p.name.startswith("tags.json.tmp")
                             for p in self.path.parent.iterdir()),
                         "temp files must not be left behind")
        self.assertEqual(sorted(self.store.get("app:1")), ["first", "second"])

    def test_adding_never_clobbers_existing(self):
        """The whole point: hand tags are sacred."""
        self.store.add(["app:1"], ["mine"])
        for _ in range(5):
            self.store.add(["app:1"], ["auto-ish"])
        self.assertIn("mine", self.store.get("app:1"))

    def test_rejects_bad_tags(self):
        for bad in ("", "   ", 'has"quote', "has,comma", "x" * 200):
            with self.assertRaises(self.tagstore.TagError, msg=bad):
                self.store.add(["app:1"], [bad])

    def test_vocabulary_and_lookup(self):
        self.store.add(["a", "b", "c"], ["shared"])
        self.store.add(["a"], ["solo"])
        self.assertEqual(self.store.vocabulary(), {"shared": 3, "solo": 1})
        self.assertEqual(self.store.subjects_with("solo"), {"a"})
        self.assertEqual(len(self.store), 3)

    def test_rename_and_delete(self):
        self.store.add(["a", "b"], ["old"])
        self.assertEqual(self.store.rename("old", "new"), 2)
        self.assertEqual(self.store.vocabulary(), {"new": 2})
        self.assertEqual(self.store.delete_tag("new"), 2)
        self.assertEqual(self.store.vocabulary(), {})

    def test_notes(self):
        self.store.set_note("a", "  reskin this one  ")
        self.assertEqual(self.store.note("a"), "reskin this one")
        again = self.tagstore.TagStore(self.path)
        self.assertEqual(again.note("a"), "reskin this one")

    def test_export_round_trip(self):
        self.store.add(["app:1", "app:2"], ["alpha", "beta"])
        text = self.store.export_json()
        fresh = self.tagstore.TagStore(Path(self.tmp.name) / "other.json")
        self.assertEqual(fresh.import_json(text), 2)
        self.assertEqual(fresh.get("app:1"), ["alpha", "beta"])

    def test_import_merges_rather_than_replacing(self):
        self.store.add(["app:1"], ["local-only"])
        self.store.import_json(json.dumps({"subjects": {"app:1": {"tags": ["imported"]}}}))
        self.assertEqual(self.store.get("app:1"), ["imported", "local-only"])

    def test_export_csv_has_a_header_and_a_row_per_subject(self):
        self.store.add(["app:1"], ["x"])
        self.store.add(["app:2"], ["y"])
        rows = self.store.export_csv({"app:1": {"class": "Warrior"}}).strip().splitlines()
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("subject,tags,note"))
        self.assertIn("Warrior", rows[1])

    def test_corrupt_file_is_preserved_not_silently_dropped(self):
        self.path.write_text("{ this is not json", "utf-8")
        with self.assertRaises(self.tagstore.TagError):
            self.tagstore.TagStore(self.path)
        self.assertTrue(self.path.with_suffix(".corrupt").is_file())


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class BodyFacetsTests(unittest.TestCase):
    """class / gender / size classification of the body tables."""

    @classmethod
    def setUpClass(cls):
        import bodyfacets
        import coviewer
        cls.bf_mod = bodyfacets
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.bf = cls.cat.facets

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_classification_ran(self):
        self.assertIsNotNone(self.bf, "facet build failed")
        self.assertGreater(len(self.bf.records), 3000)

    def test_the_four_player_body_types(self):
        """001/002 female, 003/004 male; within a gender the lower number is
        the smaller body.  Gender is VERIFIED from gender-locked garments;
        size from measuring the four base meshes."""
        s = self.bf.summary()["bodyType"]
        for bt in ("001", "002", "003", "004"):
            self.assertGreater(s.get(bt, 0), 500, f"{bt} should be a bulk body type")
        m = self.bf_mod.BODY_METRICS
        self.assertLess(m["001"]["height"], m["002"]["height"])
        self.assertLess(m["003"]["height"], m["004"]["height"])
        for axis in ("height", "span", "depth"):
            self.assertLess(m["001"][axis], m["002"][axis], axis)
            self.assertLess(m["003"][axis], m["004"][axis], axis)

    def test_gender_axis_agrees_with_gender_locked_items(self):
        """The evidence that fixes the gender axis, re-derived from the data:
        every itemtype row with requiredSex 1 or 2 whose family has body art
        should have that art under exactly the two prefixes of that gender."""
        from coassets import load_items
        items = load_items(ROOT)
        idents = set(self.bf.records)
        agree = disagree = 0
        for it in items:
            sex = it.get("requiredSex")
            if not sex:
                continue
            fam = str(it["id"])[:5]
            prefixes = {a[:3] for a in idents if len(a) == 9 and a[3:8] == fam}
            if not prefixes:
                continue
            expect = {"001", "002"} if sex == 2 else {"003", "004"}
            if prefixes == expect:
                agree += 1
            else:
                disagree += 1
        self.assertGreater(agree, 10, "expected gender-locked garments to test against")
        self.assertGreaterEqual(agree / (agree + disagree), 0.9,
                                f"{disagree} gender-locked items contradict the "
                                f"001/002=female, 003/004=male mapping")

    def test_class_from_armour_series(self):
        """Spot-check the series -> class map against known items."""
        cases = {"002130320": "Trojan",      # BreastPlate family, profession 11
                 "002131300": "Warrior",     # profession 21
                 "002133300": "Archer",      # profession 40
                 "002134300": "Taoist",      # profession 190
                 "002135300": "Trojan",      # ConquestArmor, profession 15
                 "002000000": "any"}         # the naked base body
        for ident, want in cases.items():
            rec = self.bf.records.get(ident) or self.bf.classify(ident)
            self.assertEqual(rec.klass, want, f"{ident} ({rec.item_name})")

    def test_class_coverage_and_consistency(self):
        armour = [r for r in self.bf.records.values() if r.kind == "armour"]
        known = [r for r in armour if r.klass != "unknown"]
        self.assertGreater(len(known) / len(armour), 0.98,
                           "the armour series should resolve to a class ~99% of the time")
        # no series may map to two different professions
        for series in self.bf.series_prof:
            c = self.bf.series_prof[series]
            if len(c) > 1:
                self.assertIsNone(self.bf.series_profession(series),
                                  f"series {series} is mixed and must report None")

    def test_no_appearance_is_dropped(self):
        """Every body appearance must land in some bucket -- the unmatched ones
        are the interesting ones and must never be hidden."""
        tables = self.cat.tables
        idents = set()
        for t in ("body", "mix_body"):
            idents |= set(tables[t].appearances)
        self.assertEqual(set(self.bf.records), idents)
        for r in self.bf.records.values():
            self.assertIn(r.klass, self.bf_mod.CLASS_ORDER)
            self.assertIn(r.gender, self.bf_mod.GENDER_ORDER)
            self.assertIn(r.size, self.bf_mod.SIZE_ORDER)

    def test_item_family_lifts_coverage_over_exact_id(self):
        """Item ids carry a trailing quality digit and appearance ids always end
        in 0, so an appearance names a family, not one item."""
        from coassets import load_items
        by_id = {str(i["id"]) for i in load_items(ROOT)}
        players = [r for r in self.bf.records.values()
                   if r.body_type in self.bf_mod.BODY_TYPES and r.kind == "armour"]
        exact = sum(1 for r in players if r.ident[3:] in by_id)
        family = sum(1 for r in players if r.item_ids)
        self.assertGreater(family, exact * 2,
                           "family matching should roughly triple item coverage")

    def test_auto_tags_are_derived_not_stored(self):
        rec = self.bf.records["002135300"]
        tags = rec.auto_tags
        self.assertIn("class:Trojan", tags)
        self.assertIn("gender:female", tags)
        self.assertIn("size:large", tags)
        # recomputing must give the identical list -- they are a pure function
        self.assertEqual(tags, self.bf.records["002135300"].auto_tags)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class FacetedApi(unittest.TestCase):
    """The filter logic behind the three controls, exercised through the
    handler's own helpers rather than over HTTP."""

    @classmethod
    def setUpClass(cls):
        import coviewer
        cls.coviewer = coviewer
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def _rows(self):
        facets = self.cat.facets
        out = []
        for app in self.cat.tables["body"]:
            rec = facets.records.get(app.ident)
            out.append({"id": app.ident, "class": rec.klass, "gender": rec.gender,
                        "size": rec.size, "kind": rec.kind})
        return out

    def test_three_axes_combine(self):
        rows = self._rows()
        warrior = [r for r in rows if r["class"] == "Warrior"]
        wf = [r for r in warrior if r["gender"] == "female"]
        wfs = [r for r in wf if r["size"] == "small"]
        self.assertGreater(len(warrior), 100)
        self.assertGreater(len(wf), 50)
        self.assertGreater(len(wfs), 20)
        self.assertLess(len(wfs), len(wf))
        self.assertLess(len(wf), len(warrior))
        # the four (gender, size) buckets of a class should be about equal
        buckets = collections_counter(
            (r["gender"], r["size"]) for r in warrior if r["kind"] == "armour")
        self.assertEqual(len(buckets), 4, f"expected 4 body buckets, got {buckets}")
        lo, hi = min(buckets.values()), max(buckets.values())
        self.assertLess(hi - lo, hi * 0.35, f"buckets are lopsided: {buckets}")

    def test_every_class_option_has_members(self):
        rows = self._rows()
        counts = collections_counter(r["class"] for r in rows)
        for k in ("Warrior", "Trojan", "Taoist", "Archer", "any", "unknown"):
            self.assertIn(k, counts, f"{k} should be selectable")
            self.assertGreater(counts[k], 0)

    def test_multi_select_within_an_axis_is_or(self):
        rows = self._rows()
        a = [r for r in rows if r["class"] in ("Warrior",)]
        b = [r for r in rows if r["class"] in ("Archer",)]
        both = [r for r in rows if r["class"] in ("Warrior", "Archer")]
        self.assertEqual(len(both), len(a) + len(b))


def collections_counter(it):
    import collections as _c
    return _c.Counter(it)


class Taxonomy(unittest.TestCase):
    """The browsing categories. Rules are pure functions of a path, so most of
    this needs no install."""

    @classmethod
    def setUpClass(cls):
        import catalog
        cls.catalog = catalog
        cls.ac = catalog.AssetCatalog(ROOT)

    def test_path_rules_place_the_obvious_things(self):
        cases = {
            "c3/monster/103/100.c3": ("monster", "mesh"),
            "c3/npc/013/1.c3": ("npc", "mesh"),
            "c3/weapon/350001.c3": ("weapon", "mesh"),
            "c3/effect/10000/1.c3": ("effect", "mesh"),
            "c3/hair/003119342.dds": ("character", "hair"),
            "map/map/island.DMap": ("map", "world"),
            "map/puzzle/island.pul": ("map", "puzzle"),
            "map/Scene/bridge.scene": ("map", "scene"),
            "map/ScenePart/x.Part": ("map", "scenepart"),
            "data/map/puzzle/island/lake/lake000.dds": ("map", "tiles"),
            "data/map/mapobj/island/land-city01.dds": ("map", "objects"),
            "data/itemminicon/500118.dds": ("ui", "itemicon"),
            "data/playerface/1.dds": ("character", "face"),
            "data/weather/rain01.dds": ("effect", "weather"),
            "ini/itemtype.json": ("system", "config"),
            "c3.wdf": ("system", "archive"),
        }
        for path, (cat, sub) in cases.items():
            c = self.ac.classify(path)
            self.assertEqual((c.category, c.subcategory), (cat, sub), path)
            self.assertTrue(c.why, f"{path} must record why it was classified")

    def test_body_type_folders_are_character_motion_not_mystery_folders(self):
        """c3/0001..0004 line up with the four player body types."""
        for n in "1234":
            c = self.ac.classify(f"c3/000{n}/741/130.c3")
            self.assertEqual(c.category, "character")
            self.assertEqual(c.subcategory, "motion")
            self.assertEqual(c.group, f"000{n}")

    def test_appearance_table_membership_beats_the_path_rule(self):
        """c3/mesh/ falls to 'character' by path, but a mesh the weapon table
        names is a weapon. The client's own ini has to win."""
        ac = self.catalog.AssetCatalog(
            ROOT, table_membership=lambda p: (
                [{"table": "r_weapon", "kind": "mesh"}]
                if p == "c3/mesh/410000.c3" else []))
        self.assertEqual(ac.classify("c3/mesh/410000.c3").category, "weapon")
        self.assertEqual(ac.classify("c3/mesh/002135000.c3").category, "character")
        self.assertIn("appearance tables", ac.classify("c3/mesh/410000.c3").why)

    def test_map_tiles_are_grouped_by_region(self):
        c = self.ac.classify("data/map/puzzle/woods/linn/linn111.dds")
        self.assertEqual(c.group, "woods")
        c = self.ac.classify("data/map/puzzle/island/city/city284.dds")
        self.assertEqual(c.group, "island")

    def test_role_separates_the_big_map_from_its_sprites(self):
        self.assertEqual(self.catalog.role_of("map/map/island.DMap"), "map")
        self.assertEqual(self.catalog.role_of("data/map/puzzle/x/y/z.dds"), "texture")
        self.assertEqual(self.catalog.role_of("c3/mesh/1.c3"), "mesh")

    def test_unknown_paths_land_in_a_real_bucket(self):
        c = self.ac.classify("totally/unknown/thing.xyz")
        self.assertEqual(c.category, "other")
        self.assertEqual(c.why, "no rule matched")
        self.assertIn("other", self.catalog.CATEGORY_IDS)

    def test_every_category_id_has_a_label_and_an_order(self):
        for cid in self.catalog.CATEGORY_IDS:
            self.assertIn(cid, self.catalog.CATEGORY_LABEL)
            self.assertIn(cid, self.catalog.SUBCATEGORY_ORDER)

    @unittest.skipUnless(HAVE_ROOT, "game install not present")
    def test_whole_corpus_is_classified_and_almost_nothing_is_left_over(self):
        import coviewer
        cat = coviewer.Catalog(ROOT)
        try:
            cat.wait_tables()
            ac = self.catalog.AssetCatalog(ROOT, table_membership=cat.references,
                                           exists=cat.exists)
            s = ac.summarise(cat.all_paths)
            total = sum(v["count"] for v in s.values())
            self.assertEqual(total, len(cat.all_paths), "every path must be counted")
            for want in ("character", "weapon", "monster", "map", "ui", "effect"):
                self.assertGreater(s.get(want, {}).get("count", 0), 0, want)
            other = s.get("other", {}).get("count", 0)
            self.assertLess(other / total, 0.01,
                            f"{other} unclassified assets is too many; a rule is missing")
        finally:
            cat.close()

    @unittest.skipUnless(HAVE_ROOT, "game install not present")
    def test_weapon_motion_table_loads(self):
        r"""ini/WeaponMotion.ini is a flat key=value file (no [sections]) mapping
        a weapon id to its swing-animation mesh."""
        wm = self.ac.weapon_motion()
        self.assertGreater(len(wm), 100)
        for k, v in list(wm.items())[:20]:
            self.assertTrue(k.isdigit(), k)
            self.assertTrue(v.endswith(".c3"), v)
            self.assertTrue(v.startswith("c3/"), v)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class Maps(unittest.TestCase):
    """A primary map and every piece of art that draws it."""

    @classmethod
    def setUpClass(cls):
        import coviewer
        import mapindex
        cls.mapindex = mapindex
        cls.cat = coviewer.Catalog(ROOT)
        cls.ix = cls.cat.maps

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_all_maps_listed_largest_first(self):
        rows = self.ix.summary()
        self.assertGreaterEqual(len(rows), 130)
        areas = [r["area"] for r in rows]
        self.assertEqual(areas, sorted(areas, reverse=True))
        self.assertGreater(areas[0], 1_000_000, "the biggest map should be huge")
        self.assertTrue(any(r.get("documentId") for r in rows),
                        "GameMap.json ids should be attached")

    def test_the_dmap_to_tile_chain_resolves(self):
        """DMap -> .pul -> .ani -> the actual .dds ground tiles."""
        rec = self.ix.get("island")
        self.assertEqual(rec.puzzle, "map/puzzle/island.pul")
        self.assertEqual(rec.ani, "ani/island.ani")
        self.assertGreater(rec.tile_count, 100)
        self.assertEqual(rec.region, "island")
        for t in rec.tiles[:20]:
            self.assertTrue(t.startswith("data/map/"), t)
            self.assertTrue(self.cat.exists(t), f"{t} must resolve")

    def test_layers_decode_completely(self):
        """The layer walk must consume every declared layer, not give up part
        way -- that is what proves the body sizes are right."""
        checked = 0
        for name in ("island", "desert", "newplain", "dragon"):
            rec = self.ix.get(name)
            if rec.error or not rec.layer_count:
                continue
            self.assertEqual(rec.layers_decoded, rec.layer_count,
                             f"{name}: only decoded {rec.layers_decoded} of "
                             f"{rec.layer_count} layers")
            checked += 1
        self.assertGreaterEqual(checked, 2)

    def test_a_map_surfaces_its_smaller_artwork(self):
        rec = self.ix.get("island")
        self.assertGreater(len(rec.covers), 10, "expected animated map sprites")
        self.assertTrue(any(c["frames"] for c in rec.covers),
                        "cover keys must resolve to real frames")
        for c in rec.covers:
            for f in c["frames"]:
                self.assertTrue(self.cat.exists(f), f)

    def test_big_map_and_small_art_are_distinguishable(self):
        """The user explicitly separates 'really large maps' from the sprites
        that go on them, so role and size have to be in the payload."""
        rec = self.ix.get("island").to_json()
        self.assertGreater(rec["area"], 1_000_000)
        self.assertTrue(rec["file"].endswith(".dmap"))
        import catalog
        ac = catalog.AssetCatalog(ROOT)
        self.assertEqual(ac.classify(rec["file"]).role, "map")
        self.assertEqual(ac.classify(rec["tiles"][0]).role, "texture")

    def test_unknown_map_reports_rather_than_raises(self):
        rec = self.ix.get("definitely_not_a_map")
        self.assertTrue(rec.error)
        self.assertEqual(rec.tile_count, 0)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class RelatedAssets(unittest.TestCase):
    """'...and what goes with them.'"""

    @classmethod
    def setUpClass(cls):
        import coviewer
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.ac = cls.cat.assetcat

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def _appearance(self, ident, table):
        ini = self.cat.tables[table]
        app = ini.get(ident)
        pr = app.parts[0]
        return {"id": app.ident, "table": table,
                "mesh": self.cat.resolve_id(pr.mesh, "mesh"),
                "texture": self.cat.resolve_id(pr.texture, "texture"),
                "mixTex": pr.mix_tex, "thirdTex": pr.third_tex,
                "fourthTex": pr.fourth_tex}

    def test_character_related_includes_its_other_colourways(self):
        app = self._appearance("002135300", "body")
        groups = self.ac.related_groups(
            appearance=app, siblings=self.cat.appearances_using_mesh)
        titles = [g["title"] for g in groups]
        self.assertIn("This asset", titles)
        self.assertIn("Other colourways on this mesh", titles)
        cw = next(g for g in groups if g["title"].startswith("Other colourways"))
        paths = [i["path"] for i in cw["items"]]
        self.assertTrue(paths)
        self.assertNotIn(app["texture"], paths,
                         "the selected skin must not be listed as a variant of itself")
        self.assertEqual(len(paths), len(set(paths)), "colourways must be deduped")

    def test_weapon_related_has_an_effects_slot(self):
        """The slot must exist and be honest about being empty rather than
        silently missing."""
        app = self._appearance("410005", "r_weapon")
        groups = self.ac.related_groups(appearance=app)
        eff = [g for g in groups if g["title"] == "Effects"]
        self.assertEqual(len(eff), 1, "the effects slot must always be present")
        self.assertTrue(eff[0]["pending"] or eff[0]["items"])

    def test_effect_names_resolve_to_real_meshes_and_textures(self):
        """An effect NAME is a 3DEffect.ini key, not a folder name: `Flash4102`
        lives in c3/effect/flash/ and `m-b02` in c3/effect/Monster-bomb/m-b02/.
        Guessing the folder from the name fails on both."""
        for name, want in (("Flash4102", "c3/effect/flash/410000.c3"),
                           ("m-b02", "c3/effect/monster-bomb/m-b02/1.c3")):
            paths = self.ac.effect_assets(name)
            self.assertTrue(paths, name)
            self.assertIn(want, paths, name)
            for p in paths:
                self.assertTrue(self.cat.exists(p), f"{name}: {p}")
            self.assertTrue(paths[0].endswith(".c3"),
                            "meshes first, so the strip leads with something drawable")

    def test_a_weapons_three_effects_stay_distinct(self):
        """A weapon has an aura, an attack trail and an impact spark, from three
        different tables, and the impact is drawn at the TARGET. Collapsing them
        into one list would be the wrong shape (docs/effects.md §1)."""
        app = self._appearance("410009", "r_weapon")
        groups = self.ac.related_groups(appearance=app)
        eff = next(g for g in groups if g["title"] == "Effects")
        roles = {i["effectRole"] for i in eff["items"]}
        self.assertIn("aura", roles)
        self.assertIn("impact", roles)
        self.assertTrue(any(r.startswith("trail:") for r in roles),
                        "410009 is quality 9 and must have an attack trail")
        for it in eff["items"]:
            self.assertTrue(it["effect"], "every entry keeps its 3DEffect.ini key")
            if it["resolved"]:
                self.assertTrue(self.cat.exists(it["path"]), it["path"])

    def test_quality_below_six_really_has_no_trail(self):
        """"No attack trail" on a low-quality weapon is the data, not a gap --
        all 2,143 trails land on quality 6-9 (docs/effects.md §2.2). The UI must
        not paper over that with an invented one."""
        low = self.ac.weapon_effects("410005")     # quality 5
        self.assertTrue(low, "410005 should still have a weapon_effects record")
        self.assertFalse(low.get("attack"),
                         "a quality-5 weapon must not report an attack trail")

    def test_weapon_motion_lookup_is_keyed_by_appearance_plus_action(self):
        """ini/WeaponMotion.ini keys are `<appearance><action>` -- 9 digits. A
        lookup with the bare 6-digit appearance matches nothing, which is how
        the Motion mesh group came to be permanently empty."""
        wm = self.ac.weapon_motion()
        self.assertNotIn("510000", wm, "the raw table is not keyed by appearance")
        self.assertIn("510000999", wm)
        hit = self.ac.weapon_motion_for("510000", "401")
        self.assertEqual(hit["mesh"], "c3/mesh/510000401.c3")
        self.assertEqual(hit["action"], "401")
        self.assertTrue(self.cat.exists(hit["mesh"]))
        # ...and the documented fallback: an action with no row falls to 999.
        dflt = self.ac.weapon_motion_for("510000", "110")
        self.assertEqual(dflt["action"], "999")
        self.assertEqual(dflt["mesh"], "c3/mesh/510000.c3")

    def test_weapon_related_lists_the_per_action_mesh(self):
        app = self._appearance("510000", "r_weapon")
        groups = self.ac.related_groups(appearance=app)
        mo = next((g for g in groups if g["title"] == "Motion mesh"), None)
        self.assertIsNotNone(mo, "the per-action mesh group must appear")
        paths = {i["path"] for i in mo["items"]}
        self.assertIn("c3/mesh/510000401.c3", paths)
        self.assertGreaterEqual(len(paths), 2,
                                "the default and at least one swap mesh")

    def test_icons_resolve_only_when_they_exist(self):
        icons = self.ac.icon_candidates("500118")     # IronBow, known to have art
        self.assertTrue(icons)
        for i in icons:
            self.assertTrue(self.cat.exists(i), i)
        self.assertEqual(self.ac.icon_candidates("999999999"), [])

    def test_sibling_workstreams_are_optional(self):
        """meshtex.py and effects.py may not exist yet; nothing may break."""
        import catalog
        self.assertIn(catalog.meshtex, (None, catalog.meshtex))
        app = self._appearance("410005", "r_weapon")
        self.ac.related_groups(appearance=app)          # must not raise


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class Assembly(unittest.TestCase):
    """Equipping parts onto a body: the manifest, the sockets, the anchors."""

    @classmethod
    def setUpClass(cls):
        import coviewer
        import parts
        cls.parts = parts
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.pm = parts.PartManifest(ROOT, tables=cls.cat.tables)

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_slots_come_from_roleparts_not_a_hardcoded_list(self):
        names = set(self.pm.slots)
        for want in ("body", "armet", "l_weapon", "r_weapon", "mount", "head"):
            self.assertIn(want, names, want)
        self.assertEqual(self.pm.slots["body"].mesh_ini, "ini/armor.ini")
        self.assertEqual(self.pm.slots["l_weapon"].mesh_ini, "ini/weapon.ini")

    def test_mix_variants_are_aliases_of_the_same_catalogue(self):
        """mix_body points at armor.ini and mix_armet at armet.ini, so they are
        the same lists under another name -- not extra catalogues."""
        self.assertIn("mix_body", self.pm.slots["body"].aliases)
        self.assertIn("mix_armet", self.pm.slots["armet"].aliases)

    def test_declared_but_unshipped_parts_are_flagged_not_hidden(self):
        for name in ("shield", "pelvis"):
            s = self.pm.slots.get(name)
            self.assertIsNotNone(s, name)
            self.assertFalse(s.shipped, f"{name} ini unexpectedly present")
            self.assertIn("not in this build", s.note.lower())

    def test_all_52_sockets_are_read(self):
        self.assertEqual(len(self.pm.sockets), 52)
        for want in ("v_armet", "v_head", "v_l_weapon", "v_r_weapon",
                     "v_l_shield", "v_mount", "v_back", "v_pelvis"):
            self.assertIn(want, self.pm.sockets, want)
        eff = [s for s in self.pm.sockets if "EFFECT" in s.upper()]
        self.assertEqual(sorted(eff), ["V_ARMET_EFFECT01", "V_ARMET_EFFECT02"],
                         "the effect sockets task #13 will use")

    def test_socket_names_match_real_submeshes(self):
        """The [Dumy] list should be the same vocabulary the meshes use."""
        raw = self.cat.read("c3/mesh/002135000.c3")
        names = {m.name for m in c3phy.meshes_from_c3(raw)}
        sockets = set(self.pm.sockets)
        attached = names - {"v_body"}
        self.assertTrue(attached)
        self.assertTrue(attached <= sockets,
                        f"submeshes not in the Dumy list: {attached - sockets}")

    def test_anchors_land_on_the_body(self):
        """A head socket must be at head height and hands out to the sides --
        this is the check that catches a placement that is silently wrong."""
        for logical in ("c3/mesh/002135000.c3", "c3/mesh/001131000.c3",
                        "c3/mesh/004134000.c3"):
            raw = self.cat.read(logical)
            b = self.parts.body_bounds(raw)
            a = self.parts.socket_anchors(raw)
            h = b["max"][2]
            head = a.get("v_armet")
            self.assertIsNotNone(head, logical)
            self.assertGreater(head.pos[2], h * 0.85,
                               f"{logical}: head socket at {head.pos[2]:.1f} "
                               f"but the body is {h:.1f} tall")
            self.assertLess(head.pos[2], h * 1.15, logical)
            for k in ("v_l_weapon", "v_r_weapon"):
                self.assertIn(k, a, logical)
                self.assertGreater(a[k].pos[2], 0, logical)

    def test_moti_anchors_are_used_when_available(self):
        """effects.py decoded MOTI, which gives the engine's own socket
        transform. Where present it must win over the skin-cluster estimate."""
        import parts
        if parts.effectsmod is None:
            self.skipTest("effects.py not importable")
        raw = self.cat.read("c3/mesh/002135000.c3")
        exact = parts.moti_sockets(raw)
        self.assertIn("v_armet", exact)
        self.assertEqual(exact["v_armet"].confidence, "verified")
        self.assertIsNotNone(exact["v_armet"].matrix)
        self.assertEqual(len(exact["v_armet"].matrix), 16)
        merged = parts.socket_anchors(raw)
        self.assertEqual(merged["v_armet"].pos, exact["v_armet"].pos)

    def test_anchor_falls_back_cleanly_without_moti(self):
        """Some bodies ship only one MOTI. The estimate must still produce a
        usable anchor and must label itself inferred."""
        raw = self.cat.read("c3/mesh/001184365.c3")
        a = self.parts.socket_anchors(raw)
        self.assertIn("v_armet", a)
        b = self.parts.body_bounds(raw)
        self.assertGreater(a["v_armet"].pos[2], b["max"][2] * 0.85)
        if a["v_armet"].confidence != "verified":
            self.assertIsNone(a["v_armet"].matrix,
                              "an estimate must not claim an orientation")

    def test_hair_and_headgear_share_one_slot(self):
        """The user's domain knowledge, checked against the data: hair lives in
        the armet table, so equipping a helmet necessarily replaces it."""
        armet = self.cat.tables["armet"]
        hair = [a.ident for a in armet if self.parts.head_kind(a.ident) == "hair"]
        gear = [a.ident for a in armet if self.parts.head_kind(a.ident) == "headgear"]
        self.assertTrue(hair, "expected hairstyles inside armet.ini")
        self.assertTrue(gear)
        self.assertEqual(self.parts.SLOT_META["armet"]["socket"], "v_armet")
        self.assertIn(["armet", "armet_dx8"], self.parts.EXCLUSIVE_GROUPS)

    def test_hair_colour_is_a_texture_swap_on_a_shared_mesh(self):
        """`Hairstyle Code = colour*100 + style`: colours 3..9 over one mesh.

        Grouped **by mesh**, not by a slice of the id. The two hair families
        number their styles differently -- series 111 puts the style in digit 7
        and series 119 in digits 7-8 -- so an id-slice key silently mixes ten
        styles together. The mesh is the invariant, and it is also exactly what
        the builder groups on.
        """
        armet = self.cat.tables["armet"]
        by_mesh: dict = {}
        for a in armet:
            if self.parts.head_kind(a.ident) != "hair" or not a.parts:
                continue
            by_mesh.setdefault(a.parts[0].mesh, []).append(a)
        checked = 0
        for mesh, group in sorted(by_mesh.items()):
            if len(group) < 5:
                continue
            textures = {g.parts[0].texture for g in group}
            self.assertEqual(len(textures), len(group),
                             f"{mesh}: each colour should have its own texture")
            for g in group:
                self.assertIn(self.parts.hair_colour(g.ident),
                              set(self.parts.HAIR_COLOURS.values()))
            checked += 1
            if checked >= 5:
                break
        self.assertGreaterEqual(checked, 3)

    def test_hair_is_series_119_not_111(self):
        """The correction in parts.HAIR_SERIES, re-derived rather than asserted.

        Series 111-116 have real item names in itemtype.json (IronHelmet,
        ConquestHelmet, BadgerHat, ...) and series 119 has none at all. A
        hairstyle cannot be an item you equip on top of your hair, so 119 is the
        hair and the rest are hats.
        """
        from coassets import load_items
        names = {}
        for it in load_items(ROOT):
            sid = str(it.get("id", ""))
            if len(sid) == 6 and sid[:3].startswith("11"):
                names.setdefault(sid[:3], set()).add(str(it.get("name", "")))
        for series in ("111", "112", "113", "114"):
            self.assertTrue(names.get(series),
                            f"series {series} should have item names")
        self.assertFalse(names.get("119"),
                         "series 119 should have no items — it is hair")
        self.assertEqual(self.parts.HAIR_SERIES, {"119"})
        self.assertEqual(self.parts.head_kind("002119310"), "hair")
        self.assertEqual(self.parts.head_kind("002111310"), "headgear")

    def test_weapons_are_not_body_specific_but_armour_is(self):
        """Once a body is chosen, only compatible headgear should be offered;
        weapons stay open."""
        armet = [a.ident for a in self.cat.tables["armet"]][:400]
        weapons = [a.ident for a in self.cat.tables["r_weapon"]][:400]
        body_pref = set(bodyfacets.BODY_TYPES)
        armet_pref = sum(1 for i in armet if i[:3] in body_pref)
        weapon_pref = sum(1 for i in weapons if i[:3] in body_pref)
        self.assertGreater(armet_pref / len(armet), 0.9,
                           "headgear ids should be body-type prefixed")
        self.assertLess(weapon_pref / len(weapons), 0.1,
                        "weapon ids should NOT be body-type prefixed")

    def test_body_bounds_exclude_attachments(self):
        """The camera frames on the body alone, so a 359-unit sword cannot
        yank the view back."""
        raw = self.cat.read("c3/mesh/002135000.c3")
        b = self.parts.body_bounds(raw)
        self.assertLess(b["max"][2] - b["min"][2], 250)
        self.assertGreater(b["max"][2] - b["min"][2], 100)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class AttachmentChain(unittest.TestCase):
    r"""The transform chain that puts an equipped part on a socket.

    **This is the class that has to fail loudly when the scale is wrong.**
    A weapon whose blade reads as twice the character's height is not a
    judgement call, it is a number, so these assert numbers.

    The chain has three stages and this project has already been burnt by
    getting it wrong in both directions -- double-applying the chunk matrix
    (438-unit offset) and skipping it (a body measuring 52.8 x 361.3 instead of
    170.5 x 159.5). The stages are therefore checked *independently* rather than
    by eyeballing the product.
    """

    @classmethod
    def setUpClass(cls):
        import coviewer
        import parts
        import effectplay
        cls.cv = coviewer
        cls.parts = parts
        cls.ep = effectplay
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    #: One body mesh per body type (docs/appearance_ids.md §2).
    BODIES = {"001": "c3/mesh/001131000.c3", "002": "c3/mesh/002135000.c3",
              "003": "c3/mesh/003133000.c3", "004": "c3/mesh/004134000.c3"}

    def _mesh_for(self, table, ident):
        ini = self.cat.tables[table]
        app = ini.get(ident)
        if not app or not app.parts:
            return None
        return self.cat.resolve_id(app.parts[0].mesh, "mesh")

    def _extent(self, logical):
        scene = self.cv.c3_to_json(self.cat.read(logical), logical)
        return self.cv._scene_bounds(scene)

    def _height(self, logical):
        b = self.parts.body_bounds(self.cat.read(logical))
        return b["max"][2] - b["min"][2]

    # -- stage 0: the coordinate conversion -------------------------------
    def test_render_matrix_matches_the_basis_evaluation(self):
        """`effectplay.render_matrix` collapses transpose + two mirrors into a
        sign flip on six flat-array entries. That is only safe if it agrees with
        the long way round, which `parts.moti_sockets` already does by
        evaluating the transform on basis vectors. Assert they agree."""
        import c3phy as _c3
        import effects as _fx
        raw = self.cat.read(self.BODIES["002"])
        anchors = self.parts.moti_sockets(raw)
        self.assertTrue(anchors, "002135000 ships per-socket MOTI")
        pend = None
        checked = 0
        for tag, body in _c3.iter_chunks(raw):
            if tag in _c3.VARIANTS:
                pend = _c3.parse_phy(tag, body)
            elif tag == b"MOTI" and pend is not None:
                a = anchors.get(pend.name)
                if a is not None and a.matrix:
                    mo = _fx.parse_moti(body)
                    bone = min(v.bone0 for v in pend.vertices)
                    shortcut = self.ep.render_matrix(mo.matrix(bone, 0))
                    for i, (x, y) in enumerate(zip(shortcut, a.matrix)):
                        self.assertAlmostEqual(x, y, places=5,
                                               msg=f"{pend.name} element {i}")
                    checked += 1
                pend = None
        self.assertGreaterEqual(checked, 3, "expected several sockets to check")

    def test_render_matrix_negates_z_translation(self):
        """The single fact everything else rests on: C3's +Z points down, so a
        socket 164.4 units *down* the C3 Z axis is 164.4 units *up* in render
        space -- on top of a 170.5-unit body's head."""
        m = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1.602, 1.198, -164.362, 1]
        r = self.ep.render_matrix(m)
        self.assertAlmostEqual(r[12], 1.602)
        self.assertAlmostEqual(r[13], 1.198)
        self.assertAlmostEqual(r[14], 164.362)
        # ...and it is an involution: converting twice is the identity.
        self.assertEqual([round(v, 6) for v in self.ep.render_matrix(r)],
                         [round(float(v), 6) for v in m])

    # -- stage 1: the chunk matrix ----------------------------------------
    def test_chunk_matrix_is_baked_exactly_once(self):
        """`mesh_to_json` applies the chunk 4x4 at load, as the engine does.
        Applying it twice or not at all are the two known failure modes, and
        both are visible as a wrong bounding box on a mesh whose matrix is not
        identity."""
        h = self._height(self.BODIES["002"])
        self.assertGreater(h, 100, "002 should be a whole standing figure")
        self.assertLess(h, 250)
        b = self.parts.body_bounds(self.cat.read(self.BODIES["002"]))
        depth = b["max"][1] - b["min"][1]
        # the documented failure: 52.8 tall x 361.3 deep
        self.assertGreater(h, depth * 2,
                           f"height {h:.1f} vs depth {depth:.1f}: a standing "
                           f"figure must be much taller than it is deep")
        chain = self.parts.attach_chain(self.cat.read(self.BODIES["002"]),
                                        "v_body", None)
        stage = next(s for s in chain["stages"] if s["name"] == "chunkMatrix")
        self.assertEqual(stage["applied"], "baked")
        self.assertIsNone(stage["matrix"],
                          "a baked stage must not hand the caller a second copy")

    # -- stage 2: the part's own MOTI -------------------------------------
    def test_weapon_meshes_are_authored_far_larger_than_a_character(self):
        r"""The bug, stated as a number.

        Weapon meshes are authored at several times character scale. Against a
        ~170-unit body, `c3/mesh/410000.c3` is 359 units long -- 2.1x the whole
        figure. That is not a rendering artefact, it is what is in the file, and
        it is why the composite transform must carry a scale.
        """
        h = self._height(self.BODIES["002"])
        ext = self._extent("c3/mesh/410000.c3")
        self.assertGreater(ext["longest"] / h, 1.5,
                           "410000 is authored much larger than the body")

    def test_part_motion_supplies_the_missing_scale(self):
        r"""Stage 2 is the part's own `MOTI`, and on a weapon it is essentially
        a uniform ~0.25 scale. 359.3 x 0.2515 = 90.4 units against a 170.5-unit
        body -- a blade, not a lamppost."""
        h = self._height(self.BODIES["002"])
        checked = 0
        for ident in ("410005", "410009", "421229", "480009", "500118"):
            mesh = self._mesh_for("r_weapon", ident)
            if not mesh:
                continue
            raw = self.cat.read(mesh)
            scene = self.cv.c3_to_json(raw, mesh)
            if not scene["meshes"]:
                continue
            ext = self.cv._scene_bounds(scene)
            m, note = self.parts.part_motion(raw, scene["meshes"][0]["name"])
            if m is None:
                continue
            d = self.ep.decompose(m)
            self.assertTrue(d["uniformScale"],
                            f"{ident}: expected a uniform scale, got {d['scale']}")
            s = d["scale"][0]
            self.assertGreater(s, 0.01, f"{ident}: degenerate scale {s}")
            scaled = ext["longest"] * s
            self.assertGreater(scaled / h, 0.05,
                               f"{ident}: {scaled:.1f} units is too small "
                               f"against a {h:.1f} body")
            self.assertLess(scaled / h, 1.5,
                            f"{ident}: {scaled:.1f} units is {scaled/h:.2f}x the "
                            f"body height — the scale stage is not being applied")
            checked += 1
        self.assertGreaterEqual(checked, 3,
                                "expected several weapons to carry their own MOTI")

    def test_armets_are_authored_at_head_scale_already(self):
        """Headgear is body-type-specific art and is authored at character
        scale, so an armet must already read as head-sized against every one of
        the four bodies. If a future change starts scaling armets, this fails."""
        for prefix, body in self.BODIES.items():
            h = self._height(body)
            checked = 0
            for app in self.cat.tables["armet"]:
                if not app.ident.startswith(prefix) or not app.parts:
                    continue
                mesh = self.cat.resolve_id(app.parts[0].mesh, "mesh")
                if not mesh:
                    continue
                ext = self._extent(mesh)
                if ext is None:
                    continue
                r = ext["longest"] / h
                self.assertGreater(r, 0.02, f"{app.ident}: {ext['longest']:.1f} "
                                            f"against a {h:.1f} body")
                self.assertLess(r, 0.8, f"{app.ident}: {ext['longest']:.1f} is "
                                        f"{r:.2f}x the body height")
                checked += 1
                if checked >= 8:
                    break
            self.assertGreaterEqual(checked, 3, prefix)

    # -- stage 3, and the composite ---------------------------------------
    def test_socket_stage_carries_orientation_and_no_scale(self):
        """A socket transform places and orients; it must not resize. If a
        socket ever comes back with a non-unit scale, something is being
        multiplied in twice."""
        for prefix, body in self.BODIES.items():
            anchors = self.parts.moti_sockets(self.cat.read(body))
            if not anchors:
                continue
            for name, a in anchors.items():
                if not a.matrix:
                    continue
                d = self.ep.decompose(a.matrix)
                for s in d["scale"]:
                    self.assertAlmostEqual(s, 1.0, places=2,
                                           msg=f"{prefix} {name}: socket scale {s}")

    def test_the_chain_is_staged_not_collapsed(self):
        """The whole point of the groundwork: three named stages, each with its
        own confidence and its own decomposition, so a regression reads as
        'scale 4.0 on partMotion' rather than 'the sword looks big'."""
        body = self.cat.read(self.BODIES["002"])
        anchors = self.parts.socket_anchors(body)
        mesh = self._mesh_for("r_weapon", "410009")
        raw = self.cat.read(mesh)
        scene = self.cv.c3_to_json(raw, mesh)
        chain = self.parts.attach_chain(raw, scene["meshes"][0]["name"],
                                        anchors.get("v_r_weapon"))
        names = [s["name"] for s in chain["stages"]]
        self.assertEqual(names, ["chunkMatrix", "partMotion", "socketMatrix"])
        for s in chain["stages"]:
            self.assertIn(s["applied"], ("baked", "baked per vertex", "applied",
                                         "position only", "no MOTI on this chunk"))
            self.assertTrue(s["note"])
        pm = next(s for s in chain["stages"] if s["name"] == "partMotion")
        self.assertEqual(pm["applied"], "baked per vertex",
                         "stage 2 is applied now; the engine's composition is "
                         "in tools/attach.py")
        self.assertEqual(pm["confidence"], "verified")

    def test_chunk_layout_census_over_the_whole_body_table(self):
        r"""**The test that would have caught the adjacency bug.**

        The four reference bodies all happen to interleave `PHY` and `MOTI`,
        which is why a four-body sample passed while 440 of 766 `armor.ini`
        meshes produced no sockets at all. Census the whole table so the
        unrepresentative sample cannot come back.
        """
        import c3phy as _c3
        grouped = interleaved = single = 0
        seen: set[str] = set()
        for app in self.cat.tables["body"]:
            if not app.parts:
                continue
            mesh = self.cat.resolve_id(app.parts[0].mesh, "mesh")
            if not mesh or mesh in seen:
                continue
            seen.add(mesh)
            try:
                order = [t for t, _ in _c3.iter_chunks(self.cat.read(mesh))
                         if t in _c3.VARIANTS or t == b"MOTI"]
            except Exception:
                continue
            phys = [i for i, t in enumerate(order) if t != b"MOTI"]
            motis = [i for i, t in enumerate(order) if t == b"MOTI"]
            if not motis or len(phys) < 2:
                single += 1
            elif max(phys) < min(motis):
                grouped += 1
            else:
                interleaved += 1
        total = grouped + interleaved + single
        # measured on this install: 664 distinct meshes, 403 grouped, 261
        # interleaved. The four bodies previously validated against are all in
        # the interleaved 261.
        self.assertGreater(total, 600, "expected the whole body mesh set")
        self.assertGreater(grouped, 300,
                           f"only {grouped}/{total} grouped — if this collapses "
                           f"the corpus changed, not the code")
        self.assertGreater(interleaved, 100)
        # ...and EVERY grouped mesh must still yield sockets. Under adjacency
        # pairing all 403 of these produce none at all and fall back silently to
        # a rotation-less estimate.
        checked = broken = 0
        for mesh in sorted(seen):
            raw = self.cat.read(mesh)
            order = [t for t, _ in _c3.iter_chunks(raw)
                     if t in _c3.VARIANTS or t == b"MOTI"]
            phys = [i for i, t in enumerate(order) if t != b"MOTI"]
            motis = [i for i, t in enumerate(order) if t == b"MOTI"]
            if not motis or len(phys) < 2 or max(phys) >= min(motis):
                continue
            checked += 1
            socks = self.parts.moti_sockets(raw)
            if not socks or "v_armet" not in socks:
                broken += 1
        self.assertGreaterEqual(checked, 300, "expected grouped meshes to test")
        self.assertEqual(broken, 0,
                         f"{broken}/{checked} grouped meshes yielded no sockets "
                         f"— that is the adjacency bug")

    def test_socket_reference_numbers(self):
        r"""The reference points from `attach.py --validate`, asserted so a
        regression fails a test instead of looking subtly wrong.

        `002135000` is 170.4 tall with `v_armet` at **164.4** — the figure the
        brief carries — and every body's head socket must land on the head, not
        at the feet.
        """
        want = {"001131000": 167.2, "002135000": 170.4,
                "003133000": 176.2, "004134000": 195.9}
        for mesh, height in want.items():
            logical = f"c3/mesh/{mesh}.c3"
            raw = self.cat.read(logical)
            b = self.parts.body_bounds(raw)
            h = b["max"][2] - b["min"][2]
            self.assertAlmostEqual(h, height, delta=1.5,
                                   msg=f"{mesh}: height {h:.1f}")
            a = self.parts.moti_sockets(raw)
            self.assertIn("v_armet", a, mesh)
            self.assertIn("v_l_weapon", a, mesh)
            self.assertIn("v_r_weapon", a, mesh)
            head = a["v_armet"].pos[2]
            self.assertGreater(head, h * 0.88, f"{mesh}: v_armet at {head:.1f}")
            self.assertLess(head, h * 1.05, f"{mesh}: v_armet at {head:.1f}")
        # the one number the brief names outright
        a = self.parts.moti_sockets(self.cat.read("c3/mesh/002135000.c3"))
        self.assertAlmostEqual(a["v_armet"].pos[2], 164.4, delta=0.5)

    def test_the_idle_action_motion_is_used_not_the_saved_pose(self):
        r"""`Mesh::Draw` always applies a motion (`0x2607E`), so there is no
        bind pose to show. The embedded track is whatever the artist saved --
        on `004134000` a **T-pose**, hands at x = +-98 up 154, against the idle
        pose's +-32 up 83. Bodies 001/002/003 agree to within 1.3 units, which
        is precisely why this needs asserting on 004."""
        raw = self.cat.read("c3/mesh/004134000.c3")
        embedded = self.parts.moti_sockets(raw)
        action = self.parts.idle_motion("004134000", ROOT)
        self.assertIsNotNone(action,
                             "3dmotion.ini 4000100 must resolve for body 004")
        posed = self.parts.moti_sockets(raw, motion_set=action)
        self.assertIn("v_r_weapon", embedded)
        self.assertIn("v_r_weapon", posed)
        e = embedded["v_r_weapon"].pos
        p = posed["v_r_weapon"].pos
        self.assertGreater(abs(e[0]), 80, f"the embedded pose is a T-pose: {e}")
        self.assertLess(abs(p[0]), 60, f"the idle pose has hands down: {p}")
        self.assertGreater(e[2] - p[2], 40,
                           "the T-pose holds the hand ~70 units higher")
        # and the head socket barely moves between the two
        self.assertLess(abs(embedded["v_armet"].pos[2] -
                            posed["v_armet"].pos[2]), 8)

    def test_socket_chunks_are_identified_by_the_dumy_list(self):
        r"""Not by a `v_` prefix. A prefix test is wrong in both directions:
        hair meshes ship `v_armet01` / `v_armet02` chunks that are **real
        geometry**, and series-119 hair puts its visible geometry in a chunk
        named `v_body`. Hiding either renders the part empty."""
        import attach
        self.assertTrue(attach.is_socket_name("v_armet"))
        self.assertFalse(attach.is_socket_name("v_body"),
                         "v_body is the visible geometry, never a socket")
        self.assertFalse(attach.is_socket_name("v_armet01"),
                         "v_armet01 is real geometry on 46 hair meshes")
        self.assertFalse(attach.is_socket_name("v_armet02"))
        self.assertFalse(self.cv.is_socket_chunk("v_armet01"))
        self.assertTrue(self.cv.is_socket_chunk("v_armet"))
        # and the viewport must actually draw those chunks
        mesh = self._mesh_for("armet", "002111310")
        scene = self.cv.c3_to_json(self.cat.read(mesh), mesh)
        drawn = [m for m in scene["meshes"]
                 if not m["isSocket"] and m["vertexCount"]]
        self.assertTrue(drawn, f"{mesh} rendered completely empty")

    def test_both_hair_families_resolve_and_draw(self):
        """Series 111 and series 119 are both real. 119 is the larger family and
        mostly lives under `c3/hair/`."""
        fam = {"111": 0, "119": 0}
        under_hair = 0
        for app in self.cat.tables["armet"]:
            s = app.ident[3:6] if len(app.ident) == 9 else ""
            if s not in fam or not app.parts:
                continue
            mesh = self.cat.resolve_id(app.parts[0].mesh, "mesh")
            if not mesh:
                continue
            fam[s] += 1
            if s == "119" and mesh.startswith("c3/hair/"):
                under_hair += 1
        self.assertGreater(fam["111"], 20, "series 111 hair must resolve")
        self.assertGreater(fam["119"], fam["111"],
                           "series 119 is the larger hair family")
        self.assertGreater(under_hair, 0, "series 119 mostly lives in c3/hair/")

    def test_phy_to_moti_pairing_is_ordinal_not_adjacent(self):
        r"""The correction that came out of task #18's disassembly:
        `MeshCreate` builds the phy array and `MotionCreate` builds the motion
        array from the same file *independently*, and `FindPhyByName`'s index is
        used on the motion set with no remapping. Pairing a `MOTI` with whichever
        `PHY` precedes it is wrong, and `c3/mount/850/8500000.c3` proves it: all
        eight `PHY` chunks come first, then all eight `MOTI` chunks."""
        import attach
        import c3phy as _c3
        raw = self.cat.read("c3/mount/850/8500000.c3")
        order = [tag for tag, _ in _c3.iter_chunks(raw)
                 if tag in _c3.VARIANTS or tag == b"MOTI"]
        phys = [i for i, t in enumerate(order) if t != b"MOTI"]
        motis = [i for i, t in enumerate(order) if t == b"MOTI"]
        self.assertTrue(phys and motis)
        self.assertLess(max(phys), min(motis),
                        "this file really does store all PHY before all MOTI")
        pm = attach.PartMesh.parse(raw)
        paired = [c for c in pm.chunks
                  if getattr(c.phy, "vertices", None) and c.motion is not None]
        self.assertGreaterEqual(len(paired), 4,
                                "ordinal pairing must still find the motions "
                                "an adjacency walk would miss entirely")

    def test_equipped_parts_are_composed_with_their_own_motion(self):
        r"""The fix, as a number. A weapon authored 359.3 units long -- 2.11x a
        170.5-unit body -- must not render at 2.11x once it is on the socket."""
        h = self._height(self.BODIES["002"])
        checked = 0
        for ident in ("410009", "421229", "430009", "480009"):
            mesh = self._mesh_for("r_weapon", ident)
            if not mesh:
                continue
            raw = self.cat.read(mesh)
            authored = self.cv._scene_bounds(self.cv.c3_to_json(raw, mesh))
            placed = self.cv._scene_bounds(
                self.cv.c3_to_json(raw, mesh, bake_motion=True))
            self.assertIsNotNone(placed, ident)
            self.assertLess(placed["longest"] / h, 1.6,
                            f"{ident}: placed at {placed['longest']:.1f} = "
                            f"{placed['longest']/h:.2f}x a {h:.1f} body")
            self.assertGreater(placed["longest"] / h, 0.05, ident)
            if authored["longest"] / h > 1.6:
                self.assertLess(placed["longest"], authored["longest"],
                                f"{ident}: an oversized authored mesh must "
                                f"come down, not stay at {authored['longest']:.1f}")
            checked += 1
        self.assertGreaterEqual(checked, 3)

    def test_headgear_carries_real_rotation_in_its_own_motion(self):
        r"""The other half of the same symptom, measured over the whole armet
        table rather than one cherry-picked mesh.

        On headgear the part's own `MOTI` is a near-unit matrix -- which is why
        the scale looked fine while the orientation did not. Measured here:
        **802 of 1,830 armets that ship a MOTI carry a rotation over 5 degrees**,
        including a family at a full 180. Drop stage 2 and 44 % of all headgear
        is at the wrong angle.
        """
        import attach
        n = rotated = scaled = 0
        worst = 0.0
        for app in self.cat.tables["armet"]:
            if not app.parts:
                continue
            mesh = self.cat.resolve_id(app.parts[0].mesh, "mesh")
            if not mesh:
                continue
            try:
                pm = attach.PartMesh.parse(self.cat.read(mesh))
            except Exception:
                continue
            c = next((c for c in pm.chunks
                      if getattr(c.phy, "vertices", None)), None)
            if c is None or c.motion is None:
                continue
            sx, sy, sz, ang, _t = attach.mat_scale_rot(c.motion.matrix(0, 0))
            n += 1
            worst = max(worst, ang)
            if ang > 5:
                rotated += 1
            if max(abs(sx - 1), abs(sy - 1), abs(sz - 1)) > 0.01:
                scaled += 1
        self.assertGreater(n, 1000, "expected most armets to ship a MOTI")
        self.assertGreater(rotated / n, 0.3,
                           f"only {rotated}/{n} armets carry a rotation — if "
                           f"this drops, the hair-orientation evidence changed")
        self.assertGreater(worst, 170, "one family is a full 180 degree flip")
        self.assertGreater(scaled / n, 0.4)

    def test_baking_the_part_motion_moves_the_geometry(self):
        """Whatever the matrix contains, opting in must actually change the
        emitted vertices -- otherwise the flag is decorative."""
        mesh = self._mesh_for("armet", "002119310")   # 180 deg, 0.578 scale
        raw = self.cat.read(mesh)
        a = self.cv._scene_bounds(self.cv.c3_to_json(raw, mesh))
        b = self.cv._scene_bounds(
            self.cv.c3_to_json(raw, mesh, bake_motion=True))
        moved = max(abs(a["extent"][i] - b["extent"][i]) for i in range(3))
        self.assertGreater(moved, 0.05,
                           "baking the part motion must change the geometry")

    def test_asset_view_still_shows_a_mesh_as_authored(self):
        """`bake_motion` is opt-in. Viewing a weapon on its own must show it as
        the file has it, not pre-placed for a socket it is not on."""
        mesh = self._mesh_for("r_weapon", "410009")
        raw = self.cat.read(mesh)
        plain = self.cv.c3_to_json(raw, mesh)
        self.assertFalse(plain["motionBaked"])
        for m in plain["meshes"]:
            self.assertFalse(m["motionApplied"])
        baked = self.cv.c3_to_json(raw, mesh, bake_motion=True)
        self.assertTrue(baked["motionBaked"])
        self.assertTrue(any(m["motionApplied"] for m in baked["meshes"]))

    def test_figure_payload_reports_the_chain_per_part(self):
        """The UI has to be able to say what it did, per part, on screen."""
        import parts as _p
        body = self.cat.read(self.BODIES["002"])
        anchors = _p.socket_anchors(body)
        mesh = self._mesh_for("armet", "002111310")     # a hairstyle
        self.assertIsNotNone(mesh)
        chain = _p.attach_chain(self.cat.read(mesh), "", anchors.get("v_armet"))
        sock = next(s for s in chain["stages"] if s["name"] == "socketMatrix")
        self.assertEqual(sock["applied"], "applied")
        self.assertEqual(sock["confidence"], "verified")
        self.assertIsNotNone(sock["decomposed"])


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class EffectPlayback(unittest.TestCase):
    r"""docs/effects.md §8, implemented and checked against real assets.

    `tools/webui/fx.js` is a line-for-line mirror of the reference in
    `tools/effectplay.py`; testing the reference is what stops the browser code
    from being the only place the algorithm exists.
    """

    @classmethod
    def setUpClass(cls):
        import coviewer
        import effectplay
        cls.cv = coviewer
        cls.ep = effectplay
        cls.pl = effectplay.EffectPlayer(ROOT, mesh_to_json=coviewer.mesh_to_json)
        if not cls.pl.available:
            raise unittest.SkipTest(cls.pl._db_error or "effects.py unavailable")

    # -- resolution -------------------------------------------------------
    def test_an_effect_name_resolves_to_geometry(self):
        sc = self.pl.scene("Flash4102")
        self.assertTrue(sc.found, sc.error)
        lay = sc.payload["layers"][0]
        self.assertEqual(lay["mesh"].lower(), "c3/effect/flash/410000.c3")
        self.assertEqual(lay["texture"].lower(), "c3/effect/flash/4102.dds")
        self.assertTrue(lay["meshFound"] and lay["textureFound"])

    def test_an_unknown_name_fails_loudly(self):
        sc = self.pl.scene("definitely-not-an-effect")
        self.assertFalse(sc.found)
        self.assertIn("3DEffect.ini", sc.error)

    def test_blend_state_comes_from_asb_adb(self):
        """ASB/ADB are D3DBLEND (docs/effects.md §7). `5,2` is the additive glow
        and `5,6` ordinary alpha; the viewport needs the GL equivalents."""
        lay = self.pl.scene("m-b02").payload["layers"][0]
        self.assertEqual((lay["srcBlend"], lay["dstBlend"]), (5, 2))
        self.assertEqual(lay["srcBlendName"], "SRCALPHA")
        self.assertEqual(lay["glSrcBlend"], "SRC_ALPHA")
        self.assertEqual(lay["glDstBlend"], "ONE")
        self.assertTrue(lay["additive"])
        flash = self.pl.scene("Flash4102").payload["layers"][0]
        self.assertEqual((flash["glSrcBlend"], flash["glDstBlend"]),
                         ("SRC_ALPHA", "ONE_MINUS_SRC_ALPHA"))

    # -- timing -----------------------------------------------------------
    def test_length_comes_from_the_alpha_envelope_not_the_track(self):
        r"""The single most consequential rule in §6.5: `m-b02` declares 101
        frames and fades out after ten. Playing the declared length would run a
        363 ms impact spark for 3.3 seconds."""
        d = self.pl.scene("m-b02").payload
        self.assertEqual(d["frames"], 101)
        self.assertEqual(d["effectiveFrames"], 11)
        self.assertEqual(d["frameIntervalMs"], 33)
        self.assertEqual(d["durationMs"], 363)
        self.assertGreater(d["frames"] * d["frameIntervalMs"], 3000,
                           "the declared length really is that wrong")

    def test_frame_at_walks_and_then_despawns(self):
        d = self.pl.scene("m-b02").payload
        kw = dict(frames=d["effectiveFrames"], frame_interval_ms=d["frameIntervalMs"],
                  loop_time=d["loopTime"], loop_interval_ms=d["loopIntervalMs"],
                  delay_ms=d["delayMs"], endless=d["endless"])
        self.assertEqual(self.ep.frame_at(0, **kw)["frame"], 0)
        self.assertEqual(self.ep.frame_at(99, **kw)["frame"], 3)
        self.assertFalse(self.ep.frame_at(360, **kw)["done"])
        self.assertTrue(self.ep.frame_at(400, **kw)["done"],
                        "a LoopTime=1 effect must despawn after one cycle")

    def test_an_endless_aura_never_despawns(self):
        d = self.pl.scene("410009").payload
        self.assertTrue(d["endless"], "a weapon aura loops forever")
        st = self.ep.frame_at(60_000, frames=d["effectiveFrames"],
                              frame_interval_ms=d["frameIntervalMs"],
                              loop_time=d["loopTime"],
                              loop_interval_ms=d["loopIntervalMs"],
                              delay_ms=d["delayMs"], endless=True)
        self.assertFalse(st["done"])
        self.assertLess(st["frame"], d["effectiveFrames"])

    # -- the flipbook -----------------------------------------------------
    def test_flipbook_atlas_walks_the_cells(self):
        r"""docs/effects.md §6.4's worked example, reproduced: `m-b02` part
        `Plane02` has `phy->frameCount = 2` and ChangeTex keys
        (0->0) (2->1) (4->2) (6->3) -- a 2x2 grid stepping through all four
        cells over frames 0-6. N comes from the PHY chunk's own frameCount,
        NOT from the animation length."""
        part = self.pl.scene("m-b02").payload["layers"][1]["parts"][0]
        self.assertEqual(part["name"], "Plane02")
        self.assertEqual(part["uvGrid"], 2)
        self.assertEqual([k["n"] for k in part["keys"]["changeTexs"]], [0, 1, 2, 3])
        want = {0: (0.0, 0.0), 2: (0.5, 0.0), 4: (0.0, 0.5), 6: (0.5, 0.5)}
        for frame, uv in want.items():
            got = self.ep.sample_part(part, frame)
            self.assertEqual(got["cell"], frame // 2)
            self.assertAlmostEqual(got["uv"][0], uv[0], places=6)
            self.assertAlmostEqual(got["uv"][1], uv[1], places=6)
        # a step function: frame 3 holds cell 1, it does not interpolate
        self.assertEqual(self.ep.sample_part(part, 3)["cell"], 1)

    def test_the_quad_uvs_span_exactly_one_cell(self):
        """The engine adds a UV *offset* and no scale, which only works because
        the quad is authored covering 1/N of the atlas. Check that, since it is
        the assumption the whole flipbook rests on."""
        part = self.pl.scene("m-b02").payload["layers"][1]["parts"][0]
        uvs = part["geometry"]["uv0"]
        us = uvs[0::2]
        vs = uvs[1::2]
        n = part["uvGrid"]
        self.assertAlmostEqual(max(us) - min(us), 1.0 / n, places=3)
        self.assertAlmostEqual(max(vs) - min(vs), 1.0 / n, places=3)

    # -- the three key channels ------------------------------------------
    def test_alpha_is_lerped_between_keys(self):
        part = self.pl.scene("m-b02").payload["layers"][1]["parts"][0]
        self.assertEqual([(k["frame"], k["f"]) for k in part["keys"]["alphas"]],
                         [(6, 1.0), (10, 0.0)])
        self.assertAlmostEqual(self.ep.sample_part(part, 6)["alpha"], 1.0)
        self.assertAlmostEqual(self.ep.sample_part(part, 8)["alpha"], 0.5)
        self.assertAlmostEqual(self.ep.sample_part(part, 10)["alpha"], 0.0)
        # clamped at both ends, never extrapolated
        self.assertAlmostEqual(self.ep.sample_part(part, 0)["alpha"], 1.0)
        self.assertAlmostEqual(self.ep.sample_part(part, 99)["alpha"], 0.0)

    def test_the_three_channels_are_not_interchangeable(self):
        """alpha lerps, draw is an exact match only, changeTex steps. Treating
        them alike is silently wrong, so assert the difference directly."""
        part = {"uvGrid": 4, "uvStep": [0, 0], "keys": {
            "alphas": [{"frame": 0, "f": 0.0}, {"frame": 10, "f": 1.0}],
            "draws": [{"frame": 5, "b": 0}],
            "changeTexs": [{"frame": 0, "n": 0}, {"frame": 10, "n": 5}],
        }}
        self.assertAlmostEqual(self.ep.sample_part(part, 5)["alpha"], 0.5,
                               msg="alpha interpolates")
        self.assertFalse(self.ep.sample_part(part, 5)["visible"],
                         "draw applies on an exact frame match")
        self.assertTrue(self.ep.sample_part(part, 4)["visible"],
                        "and only on an exact match — frame 4 is unaffected")
        self.assertEqual(self.ep.sample_part(part, 5)["cell"], 0,
                         "changeTex steps: it holds the previous key")

    def test_uv_scroll_is_used_only_without_a_changetex_channel(self):
        part = {"uvGrid": 1, "uvStep": [0.01, -0.02], "keys": {}}
        s = self.ep.sample_part(part, 10)
        self.assertAlmostEqual(s["uv"][0], 0.1, places=6)
        self.assertAlmostEqual(s["uv"][1], -0.2, places=6)
        part["keys"] = {"changeTexs": [{"frame": 0, "n": 0},
                                       {"frame": 20, "n": 0}]}
        self.assertEqual(self.ep.sample_part(part, 10)["uv"], [0.0, 0.0],
                         "a ChangeTex key wins over the scroll")

    # -- motion ------------------------------------------------------------
    def test_motion_matrix_lerps_between_keys(self):
        m = {"bones": [0], "keys": [
            {"frame": 0, "m": list(self.ep.GL_IDENTITY)},
            {"frame": 10, "m": [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0,
                                10, 0, 0, 1]}]}
        mid = self.ep.motion_matrix(m, 0, 5)
        self.assertAlmostEqual(mid[0], 1.5)
        self.assertAlmostEqual(mid[12], 5.0)
        # clamped at both ends
        self.assertEqual(self.ep.motion_matrix(m, 0, -5)[0], 1.0)
        self.assertEqual(self.ep.motion_matrix(m, 0, 99)[0], 2.0)

    def test_motion_keys_are_trimmed_to_the_playable_length(self):
        """A RAW track stores one key per declared frame. For an 11-frame spark
        declaring 101 frames, 90 % of the payload is never sampled."""
        part = self.pl.scene("m-b02").payload["layers"][1]["parts"][0]
        self.assertEqual(part["motion"]["frameCount"], 101)
        self.assertLessEqual(len(part["motion"]["keys"]), 13)
        self.assertGreaterEqual(len(part["motion"]["keys"]), 2)
        # ...and the trim must not change what any played frame evaluates to
        for f in range(part["effectiveFrames"]):
            self.assertEqual(len(self.ep.motion_matrix(part["motion"], 0, f)), 16)

    # -- SHAP ribbons -----------------------------------------------------
    def test_shap_trail_is_a_two_point_line_with_a_pair_budget(self):
        r"""`Shape_Draw` uses frames[0] and only its first two points; the live
        ribbon holds `min(segments * 5, 800)` segment pairs."""
        part = self.pl.scene("Flash4102").payload["layers"][0]["parts"][0]
        self.assertEqual(part["kind"], "shape")
        self.assertEqual(len(part["line"]), 2)
        self.assertEqual(part["segments"], 7)
        self.assertEqual(part["maxPairs"], 7 * 5 + 1)
        self.assertEqual(part["subdiv"], 5)
        self.assertEqual(len(part["smot"]), part["frameCount"])

    def test_a_static_parent_makes_no_streak_and_a_moving_one_does(self):
        """The trail comes from the PARENT's motion, not the effect's own — the
        SMOT on the shipped flash meshes is constant on all 101 frames. So a
        static weapon really does produce a static line, and that is correct."""
        part = self.pl.scene("Flash4102").payload["layers"][0]["parts"][0]
        line = part["line"]
        smot = part["smot"]
        self.assertTrue(all(smot[0] == s for s in smot),
                        "the shipped SMOT is a constant matrix")
        still: list = []
        for _ in range(20):
            self.ep.ribbon_advance(line, list(self.ep.GL_IDENTITY), still,
                                   max_pairs=part["maxPairs"])
        spread = max(abs(p[0][i] - still[0][0][i]) for p in still for i in range(3))
        self.assertLess(spread, 1e-6, "a still parent must not smear")

        moving: list = []
        for step in range(20):
            world = list(self.ep.GL_IDENTITY)
            world[12] = step * 10.0
            self.ep.ribbon_advance(line, world, moving, max_pairs=part["maxPairs"])
        self.assertLessEqual(len(moving), part["maxPairs"],
                             "the ribbon must respect its pair budget")
        xs = [p[0][0] for p in moving]
        self.assertGreater(max(xs) - min(xs), 10,
                           "a moving parent must leave a streak")

    def test_ribbon_subdivides_five_pairs_per_advance(self):
        hist: list = []
        line = [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
        self.ep.ribbon_advance(line, list(self.ep.GL_IDENTITY), hist, max_pairs=800)
        self.assertEqual(len(hist), 1)
        w = list(self.ep.GL_IDENTITY); w[12] = 100.0
        self.ep.ribbon_advance(line, w, hist, max_pairs=800)
        self.assertEqual(len(hist), 6, "one seed pair plus five interpolated")
        self.assertAlmostEqual(hist[1][0][0], 20.0)
        self.assertAlmostEqual(hist[-1][0][0], 100.0)

    # -- particles are a stated gap, not a silent one ---------------------
    def test_particle_layers_are_reported_not_faked(self):
        d = self.pl.scene("m-b02").payload
        self.assertGreaterEqual(d["particleParts"], 1)
        p = next(p for lay in d["layers"] for p in lay["parts"]
                 if p["kind"] == "particle")
        self.assertIn("not decoded", p["note"])

    # -- weapon motion -----------------------------------------------------
    def test_weapon_meshes_follow_the_documented_lookup_order(self):
        rec = self.pl.weapon_meshes("510000")
        self.assertEqual(rec["default"], "c3/mesh/510000.c3")
        self.assertEqual(rec["actions"]["401"]["mesh"], "c3/mesh/510000401.c3")
        self.assertEqual(rec["attackActions"], ["401", "402", "403"])
        self.assertIn("c3/mesh/510000401.c3", rec["swaps"])

    def test_a_weapon_with_no_rows_falls_back_to_weapon_ini(self):
        rec = self.pl.weapon_meshes("410005", "c3/mesh/410000.c3")
        self.assertEqual(rec["default"], "c3/mesh/410000.c3")
        self.assertEqual(rec["defaultSource"], "weapon.ini Mesh0")
        self.assertEqual(rec["actions"], {},
                         "no WeaponMotion rows is the data, not a lookup bug")


class LoadoutModel(unittest.TestCase):
    """The equip model: slot names come from RolePart.ini, head coverings are
    mutually exclusive, and a loadout round-trips through a URL."""

    def test_head_slot_is_exclusive(self):
        import parts
        loadout = {"body": {"id": "002135300"},
                   "armet": {"id": "002111310"}}       # a hairstyle

        def equip(slot, ident):
            for grp in parts.EXCLUSIVE_GROUPS:
                if slot in grp:
                    for other in grp:
                        if other != slot:
                            loadout.pop(other, None)
            loadout[slot] = {"id": ident}

        equip("armet_dx8", "002119310")                 # a helmet
        self.assertNotIn("armet", loadout, "a helmet must replace the hair")
        self.assertEqual(loadout["armet_dx8"]["id"], "002119310")
        equip("armet", "002111310")                     # hair again
        self.assertNotIn("armet_dx8", loadout)

    def test_loadout_round_trips_through_a_url(self):
        loadout = {"body": {"id": "002135300"}, "armet": {"id": "002111310"},
                   "r_weapon": {"id": "410005"}}
        import urllib.parse
        q = urllib.parse.urlencode({k: v["id"] for k, v in loadout.items()})
        back = {k: {"id": v} for k, v in urllib.parse.parse_qsl(q)}
        self.assertEqual(back, loadout)
        self.assertIn("body=002135300", q)

    def test_slots_are_independent(self):
        loadout = {}
        loadout["body"] = {"id": "002135300", "table": "body"}
        loadout["r_weapon"] = {"id": "410005", "table": "r_weapon"}
        self.assertNotIn("l_weapon", loadout)
        self.assertEqual(loadout["body"]["id"], "002135300")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class CharacterBuilder(unittest.TestCase):
    r"""The builder's catalogue: what it offers, and what it must never offer.

    Two claims carry the whole design and both are asserted as facts about the
    shipped data, not as properties of the code:

      1. **No option can be an invalid mesh+texture pair.** The builder never
         crosses a mesh with a texture; it offers appearance rows, and only
         those whose two halves both resolve to a file on disk.
      2. **Nothing incompatible is ever offered.** Once a body is chosen, a
         body-specific slot offers only art carrying that body's prefix.
    """

    @classmethod
    def setUpClass(cls):
        import builder
        import coviewer
        cls.builder = builder
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.idx = cls.cat.builder

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    # -- the pair invariant ------------------------------------------------
    def test_no_option_is_an_invalid_mesh_texture_pair(self):
        """Every offered option resolves to a mesh AND a texture that exist,
        and the pair is one an appearance row states -- never a cross product."""
        seen = 0
        for slot, opts in self.idx.options.items():
            ini = self.cat.tables.get(slot)
            for o in opts:
                self.assertTrue(self.cat.exists(o.mesh), f"{slot} {o.ident}: {o.mesh}")
                self.assertTrue(self.cat.exists(o.texture),
                                f"{slot} {o.ident}: {o.texture}")
                app = ini.get(o.ident)
                self.assertIsNotNone(app, f"{slot} {o.ident} not in {ini.name}")
                pr = app.parts[0]
                self.assertEqual(pr.mesh, o.mesh_id,
                                 f"{o.ident}: mesh must come from the ini row")
                self.assertEqual(pr.texture, o.texture_id,
                                 f"{o.ident}: texture must come from the ini row")
                seen += 1
        self.assertGreater(seen, 10000, "expected the whole catalogue")

    def test_a_garments_colour_variants_all_share_its_mesh(self):
        """The second choice varies the texture and nothing else. If a variant
        ever carried a different mesh the two-level picker would be a lie."""
        res = self.idx.query("body", body_type="002")
        groups = self.idx.garments(res["matched"])
        self.assertGreater(len(groups), 100)
        for g in groups:
            for v in g["variants"]:
                self.assertEqual(v["mesh"], g["mesh"])
            self.assertEqual(len({v["texture"] for v in g["variants"]}),
                             len(g["variants"]),
                             f"{g['mesh']}: colours must differ by texture")

    def test_grouping_turns_800_appearances_into_about_170_garments(self):
        """The number that makes the picker usable at all."""
        for bt in ("001", "002", "003", "004"):
            res = self.idx.query("body", body_type=bt)
            groups = self.idx.garments(res["matched"])
            self.assertGreater(res["total"], 600, bt)
            self.assertGreater(len(groups), 120, bt)
            self.assertLess(len(groups), 220, bt)
            self.assertEqual(sum(g["appearances"] for g in groups),
                             res["total"], bt)

    def test_appearances_that_look_identical_are_shown_once(self):
        """1,082 of the 3,082 usable armor.ini rows are a different appearance
        id resolving to the SAME mesh and the SAME texture -- `002182320`
        through `002186320` are eight ids for one garment. Eight identical
        swatches is noise, so one stands for the look and the rest ride along as
        aliases: visible if you look, not repeated eight times if you do not."""
        res = self.idx.query("body", body_type="002")
        groups = self.idx.garments(res["matched"])
        aliased = 0
        for g in groups:
            looks = {v["texture"] for v in g["variants"]}
            self.assertEqual(len(looks), len(g["variants"]),
                             f"{g['mesh']}: a look is listed twice")
            aliased += sum(len(v["aliases"]) for v in g["variants"])
            for v in g["variants"]:
                for other in v["aliases"]:
                    self.assertNotEqual(other, v["id"])
        self.assertGreater(aliased, 100, "expected real duplicate ids")
        self.assertEqual(
            sum(g["count"] for g in groups) + aliased,
            sum(g["appearances"] for g in groups),
            "every appearance is either shown or recorded as an alias")

    # -- compatibility -----------------------------------------------------
    def test_incompatible_parts_are_never_offered_for_a_body(self):
        for bt in ("001", "002", "003", "004"):
            for slot in ("body", "armet"):
                opts = self.idx.compatible(slot, bt)
                self.assertTrue(opts, f"{slot} {bt} offered nothing")
                wrong = [o.ident for o in opts if o.ident[:3] != bt]
                self.assertFalse(wrong[:5],
                                 f"{slot}: art for another body type offered "
                                 f"to body {bt}: {wrong[:5]}")

    def test_every_body_type_can_wear_every_weapon(self):
        """Weapon ids carry no body prefix -- measured, not assumed -- so the
        weapon slots must not be filtered by body at all."""
        base = len(self.idx.compatible("r_weapon", ""))
        self.assertGreater(base, 4000)
        for bt in ("001", "002", "003", "004"):
            self.assertEqual(len(self.idx.compatible("r_weapon", bt)), base, bt)
        prefixed = sum(1 for o in self.idx.options["r_weapon"] if o.body_type)
        self.assertEqual(prefixed, 0, "no weapon id is body-type prefixed")
        self.assertFalse(self.idx._body_specific("r_weapon"))
        self.assertTrue(self.idx._body_specific("body"))
        self.assertTrue(self.idx._body_specific("armet"))

    def test_head_slot_holds_both_hair_and_headgear(self):
        res = self.idx.query("armet", body_type="002")
        kinds = {o.axes.get("kind") for o in res["matched"]}
        self.assertEqual(kinds, {"hair", "headgear"})
        hair = self.idx.query("armet", body_type="002",
                              selected={"kind": {"hair"}})
        gear = self.idx.query("armet", body_type="002",
                              selected={"kind": {"headgear"}})
        self.assertTrue(hair["total"] and gear["total"])
        self.assertEqual(hair["total"] + gear["total"], res["total"])

    # -- slots that cannot be used ----------------------------------------
    def test_unusable_slots_report_a_reason_and_offer_nothing(self):
        """shield/pelvis do not ship at all; head/misc/mount ship an ini whose
        meshes resolve to nothing. Both must be greyed with a reason rather than
        opening an empty picker."""
        info = {s["name"]: s for s in self.idx.slot_info()}
        for name in ("shield", "pelvis"):
            self.assertFalse(info[name]["shipped"], name)
            self.assertFalse(info[name]["usable"], name)
            self.assertIn("does not ship", info[name]["reason"])
        for name in ("head", "misc", "mount"):
            self.assertTrue(info[name]["shipped"], name)
            self.assertFalse(info[name]["usable"], name)
            self.assertGreater(info[name]["rows"], 0, name)
            self.assertEqual(info[name]["count"], 0, name)
            self.assertIn("not one of their meshes resolves", info[name]["reason"])
        for name in ("body", "armet", "l_weapon", "r_weapon"):
            self.assertTrue(info[name]["usable"], name)
            self.assertGreater(info[name]["garments"], 4, name)

    def test_a_barely_stocked_slot_says_how_little_ships(self):
        info = {s["name"]: s for s in self.idx.slot_info()}["armet_dx8"]
        self.assertTrue(info["usable"])
        self.assertLess(info["count"], info["rows"] * 0.05)
        self.assertIn("have art that ships", info["reason"])

    # -- filters -----------------------------------------------------------
    def test_facet_counts_are_cross_filtered_and_never_land_on_nothing(self):
        res = self.idx.query("r_weapon")
        self.assertIn("type", res["facets"])
        for axis, counts in res["facets"].items():
            for value, n in counts.items():
                self.assertGreater(n, 0, f"{axis}={value} offered a zero chip")
                got = self.idx.query("r_weapon", selected={axis: {value}})
                self.assertEqual(got["total"], n,
                                 f"{axis}={value} promised {n}, gave {got['total']}")

    def test_an_axis_with_one_value_is_not_offered_as_a_filter(self):
        """Filtering a head picker by 'Gender: female' when the body already
        fixed it is noise, so single-valued axes are dropped."""
        res = self.idx.query("armet", body_type="002")
        self.assertNotIn("gender", res["facets"])
        self.assertNotIn("size", res["facets"])
        self.assertIn("kind", res["facets"])

    def test_search_matches_name_and_id(self):
        by_name = self.idx.query("r_weapon", text="blade")
        self.assertGreater(by_name["total"], 20)
        self.assertTrue(all("blade" in (o.name + o.detail).lower()
                            or "blade" in o.ident
                            for o in by_name["matched"]))
        by_id = self.idx.query("r_weapon", text="410009")
        self.assertEqual([o.ident for o in by_id["matched"]], ["410009"])

    # -- defaults and naming ----------------------------------------------
    def test_the_page_opens_on_a_real_character(self):
        for bt in ("001", "002", "003", "004"):
            lo = self.idx.default_loadout(bt)
            self.assertIn("body", lo, bt)
            self.assertTrue(lo["body"].startswith(bt), bt)
            body = next(o for o in self.idx.options["body"]
                        if o.ident == lo["body"])
            self.assertTrue(self.cat.exists(body.mesh))
            self.assertTrue(self.cat.exists(body.texture))
            if "armet" in lo:
                self.assertTrue(lo["armet"].startswith(bt), bt)

    def test_options_are_named_in_words_not_only_ids(self):
        """'Nothing should require knowing what an appearance ID is.'"""
        for slot, floor in (("r_weapon", 0.9), ("body", 0.5)):
            opts = self.idx.compatible(slot, "002" if slot == "body" else "")
            named = sum(1 for o in opts if o.name and o.name != o.ident
                        and not o.name[0].isdigit())
            self.assertGreater(named / len(opts), floor,
                               f"{slot}: only {named}/{len(opts)} have a name")
        # hair has no items behind it, so it gets a constructed English label
        hair = [o for o in self.idx.compatible("armet", "002")
                if o.axes.get("kind") == "hair"]
        self.assertTrue(hair)
        self.assertTrue(all(o.name.startswith("Hairstyle") for o in hair))
        self.assertTrue(all(o.detail in set(self.builder.HAIR_COLOURS.values())
                            or o.detail == "hair" for o in hair))

    # -- the always-on weapon effect --------------------------------------
    def test_the_weapon_aura_is_a_table_lookup_not_a_filename(self):
        """Task #20's correction. `blade/` is one of 29 families and the link is
        `Action3DEffect[999.999.<type>.<sub>]` -> `3DEffect.ini` ->
        `3DEffectObj.ini`; guessing `c3/effect/<family>/<id>.c3` found 194 and
        missed the other 600."""
        if self.builder.superfxmod is None:
            self.skipTest("tools/superfx.py not importable")
        se = self.idx.super_effects()
        self.assertGreater(len(se), 600,
                           "far more than the filename scan used to find")
        rec = self.idx.super_effect("410099")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["name"], "410099")
        self.assertEqual(rec["family"], "blade")
        self.assertTrue(rec["layers"])
        for L in rec["layers"]:
            self.assertTrue(self.cat.exists(L["mesh"]), L["mesh"])
            self.assertTrue(self.cat.exists(L["texture"]), L["texture"])
        families = set()
        for ident in list(se)[:400]:
            r = self.idx.super_effect(ident)
            if r and r["family"]:
                families.add(r["family"])
        self.assertGreater(len(families), 5,
                           f"expected many effect families, got {families}")

    def test_the_aura_anchors_as_a_sibling_of_the_weapon(self):
        """The user's "off in space" bug, as a number.

        The effect hangs off `v_r_weapon` directly. Composing the weapon's own
        bone-0 matrix as well -- treating the effect as the weapon's child --
        shrinks it into the grip. superfx.anchor() is the authority; this
        asserts the viewer receives that and not the wrong composition.
        """
        if self.builder.superfxmod is None:
            self.skipTest("tools/superfx.py not importable")
        import attach
        import superfx
        body = attach.PartMesh.load(self.cat.assets, "c3/mesh/002135000.c3")
        sock = attach.socket_matrix(body, "v_r_weapon", 0, None)
        anchor = superfx.SuperFxDB.anchor(body, "r_weapon", 0, None, (0.0, 0.0, 0.0))
        self.assertIsNotNone(anchor)
        for a, b in zip(anchor, sock):
            self.assertAlmostEqual(a, b, places=5,
                                   msg="the aura rides the socket itself")
        sdb = self.idx.super_db()
        eff = sdb.super_effect("410009")
        self.assertIsNotNone(eff)
        emesh = sdb.mesh(eff.layers[0].mesh)
        wmesh = attach.PartMesh.load(self.cat.assets, "c3/mesh/410000.c3")
        def span(pts):
            xs = list(zip(*pts))
            return max(max(a) - min(a) for a in xs)
        placed = span(list(sdb.world_vertices(emesh, anchor)))
        # the wrong composition: effect x weaponBone0 x socket
        wbone = next(c.motion.matrix(0, 0) for c in wmesh.chunks
                     if c.motion is not None)
        import effects as fxmod
        wrong = span(list(sdb.world_vertices(
            emesh, fxmod.mat_mul(wbone, anchor))))
        self.assertGreater(placed, wrong * 2,
                           f"sibling {placed:.1f} vs child {wrong:.1f}: "
                           f"composing the weapon's bone-0 buries the glow")

    # -- animation ---------------------------------------------------------
    def test_actions_are_resolved_against_the_equipped_weapon(self):
        """3dmotion.ini is keyed <shape><weaponset><action>: a 410 swing is a
        different motion from the unarmed one, so the lookup must carry the
        weapon."""
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = self.builder.animmod.AnimDB(ROOT)
        unarmed = db.clip("002132300", "401")
        armed = db.clip("002132300", "401", weapon="410089")
        self.assertIsNotNone(unarmed)
        self.assertIsNotNone(armed)
        self.assertNotEqual(unarmed.path, armed.path,
                            "the weapon must change the motion file")
        self.assertEqual(armed.weaponset, "410")

    def test_the_offered_actions_all_resolve_and_are_named_first(self):
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = self.builder.animmod.AnimDB(ROOT)
        acts = self.builder.actions_for(db, "002132300", "410089")
        self.assertGreater(len(acts), 20)
        codes = [a["code"] for a in acts]
        for want in ("100", "110", "120", "130", "401"):
            self.assertIn(want, codes)
        self.assertTrue(all(a["motion"] for a in acts),
                        "an offered action must resolve to a motion file")
        named = [i for i, a in enumerate(acts) if a["named"]]
        unnamed = [i for i, a in enumerate(acts) if not a["named"]]
        if unnamed:
            self.assertLess(max(named), min(unnamed),
                            "unidentified codes come last, not first")
        self.assertEqual(acts[0]["code"], "100", "standing is the first choice")

    def test_run_is_two_clips_that_chain(self):
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = self.builder.animmod.AnimDB(ROOT)
        a = db.clip("002132300", "120")
        b = db.clip("002132300", "121")
        self.assertEqual(a.chain_next, "121")
        self.assertEqual(b.chain_next, "120")
        self.assertNotEqual(a.path, b.path)
        seq = db.sequence("002132300", "120")
        self.assertEqual([c.action for c in seq], ["120", "121"])
        walk = db.clip("002132300", "110")
        self.assertIsNone(walk.chain_next, "walk self-loops, it does not chain")

    def test_walk_and_run_are_in_place_but_a_jump_is_not(self):
        """The root-motion correction: do NOT add translation to locomotion."""
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = self.builder.animmod.AnimDB(ROOT)
        for action in ("110", "120"):
            t = db.clip("002132300", action).root_track()
            self.assertTrue(t)
            # 0.14 units of pelvis sway on a 170-unit body is a cycle in place,
            # not locomotion: the client moves the character across the map.
            self.assertLess(max(abs(p[0]) + abs(p[1]) for p in t), 1.0,
                            f"{action} must not translate the root")
        jump = db.clip("002132300", "131")
        sil = jump.silhouette_track()
        rise = max(s[1] for s in sil) - min(s[1] for s in sil)
        self.assertGreater(rise, 30, "a jump's arc is baked into the poses")
        self.assertIn("in-place", self.builder.ROOT_MOTION_NOTE)

    def test_action_poses_differ_from_the_idle_pose(self):
        """A run that renders identically to standing still is not playback."""
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        import coviewer
        db = self.builder.animmod.AnimDB(ROOT)
        raw = self.cat.read("c3/mesh/002135000.c3")
        idle = db.clip("002132300", "100")
        run = db.clip("002132300", "120")
        a = coviewer.c3_to_json(raw, "x", motion_set=idle.motion, frame=0)
        b = coviewer.c3_to_json(raw, "x", motion_set=run.motion, frame=3)
        pa = a["meshes"][0]["positions"]
        pb = b["meshes"][0]["positions"]
        self.assertEqual(len(pa), len(pb))
        self.assertGreater(max(abs(x - y) for x, y in zip(pa, pb)), 1.0,
                           "the run pose is identical to the idle pose")

    def test_socket_matrices_move_with_the_frame(self):
        """An equipped weapon has to travel with the hand, which means the
        socket matrix is re-evaluated per frame, not fixed at frame 0."""
        import parts
        raw = self.cat.read("c3/mesh/002135000.c3")
        swing = parts.idle_motion("002135300", ROOT, "000", "401")
        self.assertIsNotNone(swing)
        a = parts.socket_anchors(raw, motion_set=swing, frame=0)
        b = parts.socket_anchors(raw, motion_set=swing, frame=6)
        self.assertIn("v_r_weapon", a)
        d = max(abs(x - y) for x, y in zip(a["v_r_weapon"].pos,
                                           b["v_r_weapon"].pos))
        self.assertGreater(d, 0.5, "the hand socket never moved during a swing")

    def test_frame_rate_is_offered_as_adjustable_not_as_fact(self):
        self.assertEqual(self.builder.DEFAULT_FRAME_MS, 41)
        self.assertIn("NOT recoverable", self.builder.TIMING_NOTE)
        self.assertIn("33 ms", self.builder.TIMING_NOTE)
        self.assertIn("speed control", self.builder.TIMING_NOTE)

    def test_a_missing_motion_is_reported_not_silently_empty(self):
        """1,100 of the 3,260 motion files this ini names are absent. The player
        bodies are complete, but the failure has to be legible where it bites."""
        if self.builder.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = self.builder.animmod.AnimDB(ROOT)
        # the whole c3/1001-c3/1004 tree is absent from this install
        self.assertIsNone(db.clip("002132300", "130", shape="1001"),
                          "a missing motion must return None, not a fallback")
        self.assertIn("1,100", self.builder.MISSING_NOTE)
        ok = sum(1 for a in ("100", "110", "115", "120", "121", "125", "126",
                             "130", "131", "401", "402", "403")
                 if db.clip("002132300", a) is not None)
        self.assertGreaterEqual(ok, 11,
                                "the player bodies ship what the builder needs")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class UnifiedEntries(unittest.TestCase):
    """One list row per asset: a mesh and the textures authored for it are the
    same thing, so they are one entry, and the right-hand panel takes it apart
    again."""

    @classmethod
    def setUpClass(cls):
        import coviewer
        import unify
        cls.unify = unify
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.idx = cls.cat.unified

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_the_relation_comes_from_meshtex(self):
        self.assertTrue(self.idx.available)
        self.assertGreater(len(self.idx.mesh_matches), 4000)
        st = self.idx.stats()
        self.assertGreater(st["meshesWithTexture"] / st["meshes"], 0.99)
        self.assertGreater(st["authoredBest"] / st["meshes"], 0.8)

    def test_a_garment_and_its_skins_are_one_row(self):
        mesh = "c3/mesh/002135000.c3"
        texes = [m["texture"] for m in self.idx.textures_of(mesh)]
        self.assertGreater(len(texes), 5)
        rows = self.idx.collapse([mesh] + texes)
        self.assertEqual([r["path"] for r in rows], [mesh])
        self.assertEqual(rows[0]["folded"], len(texes))

    def test_a_texture_with_no_mesh_keeps_its_own_row(self):
        """Most textures are like this -- map tiles, icons, faces -- and folding
        them into something would be worse than the duplication it removes."""
        loose = [p for p in self.cat.all_paths
                 if p.startswith("data/map/") and p.endswith(".dds")][:40]
        self.assertTrue(loose)
        rows = self.idx.collapse(loose)
        self.assertEqual(len(rows), len(loose))
        self.assertTrue(all(r["role"] == "texture" for r in rows))

    def test_a_texture_folds_only_when_its_owner_is_in_the_same_view(self):
        """Filtering to one directory must not make a texture disappear."""
        tex = "c3/texture/002135300.dds"
        self.assertIsNotNone(self.idx.owner_of(tex))
        rows = self.idx.collapse([tex])
        self.assertEqual([r["path"] for r in rows], [tex])

    def test_search_by_either_name_still_finds_the_merged_row(self):
        mesh = "c3/mesh/002135000.c3"
        rows = self.idx.collapse([mesh])
        self.assertIn("002135300.dds", rows[0]["search"])
        self.assertIn("002135000.c3", rows[0]["search"])

    def test_one_texture_can_belong_to_several_meshes(self):
        tex = "c3/texture/105000000.dds"
        owners = [m for m, _ in self.idx.meshes_of(tex)]
        self.assertGreater(len(set(owners)), 1)
        self.assertIn(self.idx.owner_of(tex), owners)
        comp = self.idx.components(tex)
        meshes = [c for c in comp["components"] if c["role"] == "mesh"]
        self.assertEqual(len(meshes), len(owners))
        self.assertEqual(sum(1 for c in meshes if c["primary"]), 1,
                         "exactly one owner is primary")

    def test_components_keep_authored_and_inferred_apart(self):
        comp = self.idx.components("c3/mesh/002135000.c3")
        texes = [c for c in comp["components"] if c["role"] == "texture"]
        self.assertTrue(texes)
        self.assertTrue(all(c["kind"] in ("authored", "inferred") for c in texes))
        self.assertTrue(any(c["kind"] == "authored" for c in texes))
        self.assertTrue(all(c["method"] and c["detail"] for c in texes))
        first = texes[0]
        self.assertTrue(first["primary"])
        self.assertEqual(first["method"], "appearance_table")
        self.assertIn("armor.ini", first["detail"])

    def test_collapse_only_removes_related_pairs(self):
        """The whole corpus: rows out = rows in minus exactly the owned
        textures that are present alongside their owner."""
        paths = self.cat.all_paths
        rows = self.idx.collapse(paths)
        present = set(paths)
        expect = sum(1 for p in paths if p.endswith(".dds")
                     and self.idx.primary_owner.get(p) in present)
        self.assertEqual(len(paths) - len(rows), expect)
        self.assertGreater(expect, 3000)
        self.assertLess(expect, len(paths) * 0.2,
                        "the collapse must be conservative")

    def test_thumbnail_manifest_is_optional(self):
        """Task #21's renderer is incremental, so an absent or partial manifest
        is a normal state and must not break anything."""
        thumbs = self.idx.thumbs()
        self.assertIsInstance(thumbs, dict)
        for logical, rec in list(thumbs.items())[:20]:
            self.assertIn("file", rec)
        self.assertIsNone(self.idx.thumb_for("c3/mesh/does-not-exist.c3"))
        self.assertIsInstance(self.idx.refresh_thumbs(), int)

    @unittest.skipUnless(_thumbs_rendered("meshes"),
                         "mesh thumbnails not fully rendered")
    def test_rendered_thumbnails_are_consumed(self):
        """The mesh slice, not the 23 MB full manifest: every list row wants a
        mesh thumbnail and almost none want a texture one."""
        t = self.idx.thumbs()
        self.assertGreater(len(t), 4000)
        self.assertLess(len(t), 6000, "this should be the mesh-only slice")
        f = self.idx.thumb_for("c3/mesh/002135000.c3")
        self.assertIsNotNone(f)
        self.assertTrue(Path(f).is_file())
        self.assertEqual(Path(f).read_bytes()[:4], b"\x89PNG")

    @unittest.skipUnless(_thumbs_rendered("meshes"),
                         "mesh thumbnails not fully rendered")
    def test_a_near_empty_render_is_not_shown(self):
        """19 meshes draw under 1% of the frame. Their render is a blank square,
        which reads as broken rather than as a thin mesh, so it is withheld and
        the caller falls through to the texture."""
        low = [k for k, v in self.idx.thumbs().items() if v.get("low_coverage")]
        self.assertTrue(low, "expected the flagged meshes in the manifest")
        for k in low:
            self.assertIsNone(self.idx.thumb_for(k), k)
            self.assertIn("near-empty", self.idx.thumb_note(k))
        # a normal mesh is unaffected
        self.assertEqual(self.idx.thumb_note("c3/mesh/002135000.c3"), "")

    @unittest.skipUnless(_thumbs_rendered("textures"),
                         "texture thumbnails not fully rendered")
    def test_used_by_mesh_is_narrower_than_the_collapse_rule(self):
        """Why the collapse is not built on task #21's `used_by_mesh` flag.

        The flag marks a mesh's *chosen* skin. Folding on it would merge one
        texture into the garment and leave its eleven colourways as their own
        rows -- exactly the clutter the single-entry change removes.
        """
        self.assertTrue(self.idx.skins_a_model("c3/texture/002135300.dds"))
        self.assertFalse(self.idx.skins_a_model("c3/texture/002135310.dds"))
        # ...yet both are authored pairings of the same mesh, and both fold
        for tex in ("c3/texture/002135300.dds", "c3/texture/002135310.dds"):
            self.assertEqual(self.idx.owner_of(tex), "c3/mesh/002135000.c3")
        flagged = sum(1 for v in self.idx.texture_thumbs().values()
                      if v.get("used_by_mesh"))
        self.assertLess(flagged, len(self.idx.primary_owner),
                        "the flag must be the narrower partition")

    def test_thumbnail_manifest_reader_accepts_the_shapes_it_might_take(self):
        import json
        import tempfile
        shapes = [
            {"c3/mesh/a.c3": {"thumb": "a.png"}},
            {"entries": {"c3/mesh/a.c3": "a.png"}},
            {"thumbs": [{"asset": "c3/mesh/a.c3", "file": "a.png"}]},
            {"items": [{"logical": "c3/mesh/a.c3", "thumbnail": "a.png"}]},
        ]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            old = self.unify.THUMB_DIR
            try:
                self.unify.THUMB_DIR = d
                for shape in shapes:
                    (d / "manifest.json").write_text(json.dumps(shape))
                    idx = self.unify.UnifiedIndex.__new__(self.unify.UnifiedIndex)
                    idx._thumbs = None
                    got = self.unify.UnifiedIndex.thumbs(idx)
                    self.assertIn("c3/mesh/a.c3", got, shape)
                    self.assertTrue(Path(got["c3/mesh/a.c3"]["file"]).is_file())
            finally:
                self.unify.THUMB_DIR = old


class BuilderUiModel(unittest.TestCase):
    """The parts of the builder page that are pure logic, reimplemented against
    the same rules `tools/webui/builder.js` uses."""

    def test_collapsing_a_panel_persists_and_toggle_all_is_a_single_state(self):
        """`C` must collapse everything when anything is open, and expand
        everything when nothing is."""
        store: dict = {}

        def toggle_all(collapsed):
            any_open = any(not collapsed.get(c) for c in store["cards"])
            return {c: any_open for c in store["cards"]}

        store["cards"] = ["look", "anim", "fx", "detail"]
        state = {c: False for c in store["cards"]}
        state = toggle_all(state)
        self.assertTrue(all(state.values()), "one press collapses everything")
        state = toggle_all(state)
        self.assertFalse(any(state.values()), "the next press expands everything")
        state["anim"] = False                       # one panel reopened by hand
        state = toggle_all(state)
        self.assertTrue(all(state.values()),
                        "with one open, C collapses rather than expanding")

    def test_the_head_slot_is_exclusive_in_the_builder_too(self):
        import parts
        loadout = {"body": {"id": "002132300"}, "armet": {"id": "002119310"}}

        def equip(slot, ident):
            for grp in parts.EXCLUSIVE_GROUPS:
                if slot in grp:
                    for other in grp:
                        if other != slot:
                            loadout.pop(other, None)
            loadout[slot] = {"id": ident}

        equip("armet_dx8", "5118000")
        self.assertNotIn("armet", loadout, "a DX8 helmet must replace the hair")
        equip("armet", "002119310")
        self.assertNotIn("armet_dx8", loadout)

    def test_changing_body_type_drops_art_cut_for_the_old_one(self):
        """Silently keeping a 001 helmet on a 004 body is the failure mode this
        prevents."""
        body_specific = {"body": True, "armet": True, "r_weapon": False}
        loadout = {"body": {"id": "001132300"}, "armet": {"id": "001119310"},
                   "r_weapon": {"id": "410009"}}

        def set_body(ident):
            loadout["body"] = {"id": ident}
            bt = ident[:3]
            for slot in list(loadout):
                if slot == "body" or not body_specific.get(slot):
                    continue
                if loadout[slot]["id"][:3] != bt:
                    del loadout[slot]

        set_body("004134300")
        self.assertNotIn("armet", loadout, "001 headgear must not survive")
        self.assertIn("r_weapon", loadout, "weapons fit every body")

    def test_a_builder_link_round_trips(self):
        import urllib.parse
        loadout = {"body": "002132300", "armet": "002119310", "r_weapon": "410089"}
        q = urllib.parse.urlencode(loadout)
        back = dict(urllib.parse.parse_qsl(q))
        self.assertEqual(back, loadout)
        self.assertTrue(q.startswith("body="))


class Navigation(unittest.TestCase):
    """Arrow-key navigation and load throttling.

    The key handling itself needs a browser, but the two things that actually
    go wrong do not: that navigation walks the *rendered* list rather than the
    underlying set, and that a late response for an abandoned selection cannot
    overwrite the current one.  Both are reimplemented here against the same
    rules `tools/webui/app.js` uses, so a change to the rules breaks a test.
    """

    # -- the model app.js navigates over ----------------------------------
    @staticmethod
    def _items(rows, grouped=False):
        if not grouped:
            return [{"id": r["id"], "mesh": r.get("mesh")} for r in rows]
        groups = {}
        for r in rows:
            groups.setdefault(r.get("mesh"), []).append(r)
        return [{"mesh": m, "variants": v} for m, v in sorted(
            groups.items(), key=lambda kv: (kv[0] or ""))]

    @staticmethod
    def _move(items, index, delta):
        if not items:
            return -1
        if index < 0:
            return 0 if delta > 0 else len(items) - 1
        return max(0, min(len(items) - 1, index + delta))

    @staticmethod
    def _siblings(items, index):
        mesh = items[index].get("mesh")
        if not mesh:
            return [index]
        out = [k for k, it in enumerate(items) if it.get("mesh") == mesh]
        return out or [index]

    def test_navigation_is_clamped_not_wrapped(self):
        items = self._items([{"id": str(i), "mesh": "m"} for i in range(5)])
        self.assertEqual(self._move(items, 0, -1), 0)
        self.assertEqual(self._move(items, 4, 1), 4)
        self.assertEqual(self._move(items, 0, 10), 4, "PageDown must clamp")
        self.assertEqual(self._move(items, -1, 1), 0, "first press selects the top")

    def test_navigation_walks_only_the_filtered_rows(self):
        full = [{"id": f"00213{i}300", "mesh": "c3/mesh/a.c3"} for i in range(9)]
        shown = [r for r in full if r["id"].endswith("300") and int(r["id"][5]) < 4]
        items = self._items(shown)
        self.assertEqual(len(items), 4)
        # walking off the end stays inside the filtered set
        idx = 0
        for _ in range(20):
            idx = self._move(items, idx, 1)
        self.assertEqual(items[idx]["id"], shown[-1]["id"])
        self.assertTrue(all(it["id"] in {r["id"] for r in shown} for it in items))

    def test_grouped_navigation_moves_by_mesh_and_variants_within(self):
        rows = [{"id": f"0021313{c}0", "mesh": "c3/mesh/a.c3"} for c in range(3)] + \
               [{"id": f"0021314{c}0", "mesh": "c3/mesh/b.c3"} for c in range(2)]
        items = self._items(rows, grouped=True)
        self.assertEqual([it["mesh"] for it in items],
                         ["c3/mesh/a.c3", "c3/mesh/b.c3"])
        self.assertEqual(len(items[0]["variants"]), 3)
        # Left/Right cycles inside a group
        n = len(items[0]["variants"])
        v = 0
        v = (v + 1) % n; self.assertEqual(v, 1)
        v = (v + 1) % n; self.assertEqual(v, 2)
        v = (v + 1) % n; self.assertEqual(v, 0, "variants wrap")
        # Down moves to the next mesh
        self.assertEqual(items[self._move(items, 0, 1)]["mesh"], "c3/mesh/b.c3")

    def test_flat_left_right_hops_between_rows_sharing_a_mesh(self):
        """In the real ID ordering, colourways of one mesh are 10 rows apart, so
        this is a genuine shortcut rather than a duplicate of Down."""
        rows = []
        for colour in range(2):
            for mesh in range(10):
                rows.append({"id": f"0021301{mesh}{colour}0".replace(" ", ""),
                             "mesh": f"c3/mesh/00213100{mesh}.c3"})
        items = self._items(rows)
        sibs = self._siblings(items, 0)
        self.assertEqual(len(sibs), 2, "each mesh has two colourways here")
        self.assertEqual(sibs[1] - sibs[0], 10, "and they are 10 rows apart")

    def test_single_variant_entry_falls_back_to_moving(self):
        items = self._items([{"id": "a", "mesh": "m1"}, {"id": "b", "mesh": "m2"}])
        self.assertEqual(self._siblings(items, 0), [0])
        self.assertEqual(self._siblings(items, 1), [1])

    # -- the token guard --------------------------------------------------
    def test_stale_response_cannot_overwrite_the_current_selection(self):
        """Model of app.js's `tokenNow()` / `stillCurrent()` pair: selection
        bumps a counter, async work bails when the counter has moved on."""
        state = {"token": 0, "shown": None}

        def request(asset):
            state["token"] += 1
            return (state["token"], asset)

        def deliver(job):
            tk, asset = job
            if tk != state["token"]:
                return False              # abandoned: must not touch the UI
            state["shown"] = asset
            return True

        slow = request("body")            # user selects A ...
        fast = request("weapon")          # ... then immediately B
        self.assertTrue(deliver(fast))
        self.assertEqual(state["shown"], "weapon")
        self.assertFalse(deliver(slow), "A's late response must be dropped")
        self.assertEqual(state["shown"], "weapon")

    def test_out_of_order_responses_land_on_the_last_selection(self):
        state = {"token": 0, "shown": None}
        jobs = []
        for asset in "abcde":
            state["token"] += 1
            jobs.append((state["token"], asset))
        for tk, asset in reversed(jobs):          # responses arrive backwards
            if tk == state["token"]:
                state["shown"] = asset
        self.assertEqual(state["shown"], "e")

    def test_debounce_collapses_a_key_burst_into_one_load(self):
        """Selection is instant; only the load is debounced. Holding an arrow
        key must issue one request -- for the row you stopped on -- not one per
        press. Modelled with a virtual clock; DEBOUNCE mirrors app.js.
        """
        DEBOUNCE = 130
        selected, loads = [], []
        timer = None            # (fires_at, token, asset)
        token = 0

        def tick(now):
            nonlocal timer
            if timer and now >= timer[0]:
                if timer[1] == token:
                    loads.append(timer[2])
                timer = None

        def press(asset, now):
            nonlocal timer, token
            token += 1
            selected.append(asset)          # highlight moves immediately
            timer = (now + DEBOUNCE, token, asset)

        now = 0
        for i in range(15):                 # key repeat, ~40 ms apart
            now += 40
            tick(now)
            press(f"row{i}", now)
        self.assertEqual(len(selected), 15, "every press moves the selection")
        self.assertEqual(loads, [], "nothing loads while the key is held")

        now += DEBOUNCE                     # user lets go
        tick(now)
        self.assertEqual(loads, ["row14"], "exactly one load, for the final row")

        now += 500                          # a deliberate single press later
        press("row99", now)
        now += DEBOUNCE
        tick(now)
        self.assertEqual(loads, ["row14", "row99"])


class DefaultView(unittest.TestCase):
    r"""Which way the camera faces when nothing has been orbited.

    The corpus is authored facing **-Y**. `gl.js:_basis()` puts the eye along
    `(cos yaw, sin yaw)`, so a *positive* yaw stands the camera behind the model
    and every freshly-opened character shows its back. That was the default for
    the whole life of this viewer and went unnoticed because every saved shot
    was taken from a camera the author had already orbited.

    `tools/thumbs.py` renders all 4,950 mesh thumbnails from yaw **-0.9**, and
    `out/thumbs/_selftest/yaw_grid.png` is the four cardinal views with only -Y
    showing a face. These tests pin the viewport to the same number, because a
    thumbnail and the viewport disagreeing about which side is the front is its
    own bug.
    """

    GL = (PROJECT / "tools" / "webui" / "gl.js")

    @staticmethod
    def _eye_direction(yaw, pitch=0.28):
        """Mirror of gl.js `_basis().dir`."""
        import math
        cp = math.cos(pitch)
        return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))

    def test_the_default_camera_stands_in_front_of_the_model(self):
        """Front is -Y, so the eye's Y component must be negative."""
        src = self.GL.read_text("utf-8")
        m = re.search(r"const DEFAULT_YAW = (-?[\d.]+);", src)
        self.assertIsNotNone(m, "gl.js must name its default yaw")
        yaw = float(m.group(1))
        self.assertLess(self._eye_direction(yaw)[1], 0,
                        f"yaw {yaw} puts the camera behind the model")
        self.assertGreater(self._eye_direction(0.9)[1], 0,
                           "the old default really was the back view")

    def test_the_viewport_and_the_thumbnails_agree_on_the_front(self):
        src = self.GL.read_text("utf-8")
        yaw = float(re.search(r"const DEFAULT_YAW = (-?[\d.]+);", src).group(1))
        manifest = PROJECT / "out" / "thumbs" / "manifest_meshes.json"
        if not _thumbs_rendered("meshes"):
            self.skipTest("mesh thumbnails not fully rendered")
        r = json.loads(manifest.read_text("utf-8")).get("renderer", {})
        self.assertIn("yaw", r)
        self.assertAlmostEqual(yaw, r["yaw"], places=6,
                               msg="the viewport and the thumbnails must show "
                                   "the same side of a model")

    def test_reset_view_uses_the_same_default(self):
        src = self.GL.read_text("utf-8")
        self.assertRegex(src, r"this\.cam\.yaw = DEFAULT_YAW;",
                         "resetView must not hardcode the old value")
        self.assertNotIn("yaw: 0.9,", src, "the old default must be gone")

    def _restore(self, stored, default=-0.9, legacy=0.9):
        """Mirror of gl.js `_restoreCam`'s migration."""
        if stored is None:
            return default
        return default if abs(stored - legacy) < 1e-7 else stored

    def test_a_stored_bad_default_is_migrated_but_a_real_orbit_is_not(self):
        """A returning user must not be stuck behind the model because the wrong
        default was written into their storage. Only an *untouched* old default
        is replaced -- a camera the user actually moved will not sit on 0.9 to
        seven decimal places."""
        self.assertAlmostEqual(self._restore(None), -0.9, msg="new user")
        self.assertAlmostEqual(self._restore(0.9), -0.9,
                               msg="the old default, never orbited")
        for orbited in (0.90001, 2.4, -3.0, 0.0):
            self.assertAlmostEqual(self._restore(orbited), orbited,
                                   msg="a deliberate angle must survive")


class CameraFraming(unittest.TestCase):
    """The lock / re-fit rule from gl.js `_frame()`.

    Asset scale spans 5 to ~220 units here, so persisting the distance across a
    scale change would leave the user staring at empty space. Orientation always
    persists; distance re-fits unless the camera is locked.
    """

    FIT = 2.6                                    # dist = radius * FIT

    def _frame(self, cam, radius, lock, first=False):
        """Mirror of Viewer._frame."""
        if lock and not first:
            return dict(cam)                     # frozen: nothing moves
        out = dict(cam)
        out["dist"] = radius * self.FIT
        out["pan"] = [0, 0, 0]
        return out                               # yaw / pitch untouched

    def test_orientation_survives_every_load(self):
        cam = {"yaw": -2.1, "pitch": 0.42, "dist": 300, "pan": [1, 2, 3]}
        for radius in (117.8, 2.5, 183.3, 98.0):
            cam = self._frame(cam, radius, lock=False)
            self.assertAlmostEqual(cam["yaw"], -2.1)
            self.assertAlmostEqual(cam["pitch"], 0.42)

    def test_distance_refits_so_the_subject_always_fills_the_frame(self):
        cam = {"yaw": 0.9, "pitch": 0.28, "dist": 306, "pan": [0, 0, 0]}
        body = self._frame(cam, 117.8, lock=False)
        weapon = self._frame(body, 2.5, lock=False)
        self.assertAlmostEqual(body["dist"], 117.8 * self.FIT)
        self.assertAlmostEqual(weapon["dist"], 2.5 * self.FIT)
        self.assertLess(weapon["dist"], body["dist"] / 10,
                        "a 2.5-unit mesh must not be viewed from 300 units away")

    def test_pan_resets_but_only_when_unlocked(self):
        cam = {"yaw": 0.9, "pitch": 0.28, "dist": 300, "pan": [40, 0, 12]}
        self.assertEqual(self._frame(cam, 100, lock=False)["pan"], [0, 0, 0])
        self.assertEqual(self._frame(cam, 100, lock=True)["pan"], [40, 0, 12])

    def test_lock_freezes_everything(self):
        cam = {"yaw": -1.2, "pitch": 0.5, "dist": 283.3, "pan": [5, 0, 1]}
        after = self._frame(cam, 2.5, lock=True)
        self.assertEqual(after, cam, "a locked camera must not move at all")

    def test_first_model_of_a_locked_session_is_still_framed(self):
        """Restoring a locked session has nothing to freeze yet, so the first
        model must still be fitted rather than viewed from a stale distance."""
        cam = {"yaw": -1.2, "pitch": 0.5, "dist": 1, "pan": [0, 0, 0]}
        after = self._frame(cam, 117.8, lock=True, first=True)
        self.assertAlmostEqual(after["dist"], 117.8 * self.FIT)

    def _reset(self, cam, last_bounds):
        """Mirror of Viewer.resetView: always uses the bounds of the model that
        is actually loaded, which `_frame` records even while locked."""
        return {"yaw": 0.9, "pitch": 0.28,
                "dist": last_bounds["radius"] * self.FIT, "pan": [0, 0, 0]}

    def test_reset_reframes_the_current_model_even_when_locked(self):
        """The escape hatch. A camera frozen on a 118-unit body then pointed at
        a 2.5-unit mesh shows nothing; Reset must fix that, not restore the
        frozen framing."""
        frozen = {"yaw": -1.2, "pitch": 0.5, "dist": 306.0, "pan": [50, 0, 0]}
        after_move = self._frame(frozen, 2.5, lock=True)
        self.assertEqual(after_move, frozen, "locked: still frozen on the old framing")
        # ...but the new model's bounds were recorded anyway
        cam = self._reset(after_move, {"radius": 2.5})
        self.assertAlmostEqual(cam["dist"], 2.5 * self.FIT)
        self.assertEqual(cam["pan"], [0, 0, 0])
        self.assertAlmostEqual(cam["yaw"], 0.9)
        self.assertLess(cam["dist"], frozen["dist"] / 40,
                        "Reset must actually rescue the view, not keep the frozen one")


class WeaponQuality(unittest.TestCase):
    r"""The last digit of a weapon appearance id is its QUALITY.

        410003/4/5  Normal      mesh 410000   texture 410005
        410006      Refined     mesh 410000   texture 410006
        410007      Unique      mesh 410000   texture 410006
        410008      Elite       mesh 410000   texture 410008
        410009      Super       mesh 410000   texture 410008

    Five qualities, one mesh, three textures.  The reading is asserted here
    against `ini/weapon.ini` AND, independently, against `ini/itemtype.json` --
    410003..410009 are one item ("SteelBlade") whose attack rises monotonically
    with the digit, which is the item database stating the ladder rather than
    the art implying it.

    The trap this pins down: `410000` maps to texture `410006`, the *Refined*
    one.  Calling the `...0` id "Normal" would show the wrong skin under the
    wrong name on 350 families, so it is not offered as a quality at all.
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_ROOT:
            raise unittest.SkipTest("install not present")
        import builder
        import coviewer
        cls.builder = builder
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.idx = cls.cat.builder
        cls.weapons = cls.cat.tables["r_weapon"]

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    # -- the reading itself -------------------------------------------------
    def test_the_41000_family_is_three_textures_over_one_mesh(self):
        want = {"410003": "410005", "410004": "410005", "410005": "410005",
                "410006": "410006", "410007": "410006",
                "410008": "410008", "410009": "410008"}
        for ident, tex in want.items():
            app = self.weapons.get(ident)
            self.assertIsNotNone(app, f"weapon.ini must ship {ident}")
            pr = app.parts[0]
            self.assertEqual(pr.mesh, "410000",
                             f"{ident}: quality must not change the mesh")
            self.assertEqual(pr.texture, tex, f"{ident}: wrong texture tier")
        self.assertEqual(len(set(want.values())), 3,
                         "five qualities collapse to three distinct looks")

    def test_itemtype_json_states_the_same_ladder_independently(self):
        """Attack rises with the quality digit, and 410000/1/2 are not items."""
        from coassets import load_items
        by = {str(i["id"]): i for i in load_items(ROOT)}
        ladder = [by.get(f"41000{d}") for d in "3456789"]
        self.assertTrue(all(ladder), "410003..410009 must all be items")
        self.assertEqual({i["name"] for i in ladder}, {"SteelBlade"},
                         "the quality digit must not change the item")
        atk = [i["attackMax"] for i in ladder]
        self.assertEqual(atk, sorted(atk), f"attack must rise with quality: {atk}")
        self.assertLess(atk[0], atk[-1], "Super must beat Normal")
        for d in "012":
            self.assertNotIn(f"41000{d}", by,
                             f"...{d} is not a player quality and has no item")

    def test_the_zero_id_is_not_normal(self):
        """`410000` -> texture `410006`, the REFINED skin. Labelling it Normal
        would be a wrong picture under a right-looking name."""
        self.assertEqual(self.weapons.get("410000").parts[0].texture, "410006")
        self.assertEqual(self.builder.quality_of("410000"), "",
                         "digit 0 must map to no named quality")
        rec = self.idx.quality_family("r_weapon", "410009")
        self.assertNotIn("410000", [q.get("id") for q in rec["qualities"]],
                         "the base row must never stand for a quality")
        self.assertIn("410000", [e["id"] for e in rec["extra"]],
                      "...but it must still be reachable, not hidden")

    def test_the_zero_id_follows_the_refined_texture_across_the_corpus(self):
        """Not a one-off: measured over every 6-digit family that has a ...0
        row, it lands on the 6/7 texture far more often than on any other."""
        fam = {}
        for app in self.weapons:
            if len(app.ident) != 6 or not app.parts:
                continue
            fam.setdefault(app.ident[:5], {})[app.ident[5]] = app.parts[0].texture
        like_refined = like_normal = 0
        for digits in fam.values():
            if "0" not in digits:
                continue
            t0 = digits["0"]
            on67 = any(digits.get(d) == t0 for d in "67")
            on345 = any(digits.get(d) == t0 for d in "345")
            if on67 and not on345:
                like_refined += 1
            elif on345 and not on67:
                like_normal += 1
        self.assertGreater(like_refined, 300,
                           "the ...0 row tracks the Refined texture")
        self.assertGreater(like_refined, like_normal * 10,
                           f"refined {like_refined} vs normal {like_normal}")

    # -- what the selector offers ------------------------------------------
    def test_the_selector_never_changes_the_mesh(self):
        """Switching quality is a texture swap on the weapon you already chose.
        If it changed the mesh it would be a different weapon."""
        checked = 0
        for o in self.idx.options["r_weapon"]:
            if len(o.ident) != 6 or o.ident[5] != "9":
                continue
            rec = self.idx.quality_family("r_weapon", o.ident)
            for q in rec["qualities"]:
                if not q.get("available"):
                    continue
                self.assertEqual(q["mesh"], rec["mesh"],
                                 f"{o.ident}: {q['label']} changed the mesh")
                checked += 1
        self.assertGreater(checked, 1000, "expected the whole Super catalogue")

    def test_every_offered_quality_is_an_option_the_builder_already_vetted(self):
        """The selector must not be a second, weaker path to equipping: every
        id it offers has to be in `options`, i.e. mesh AND texture on disk."""
        by_ident = {o.ident for o in self.idx.options["r_weapon"]}
        for ident in ("410009", "421009", "510005", "350049"):
            rec = self.idx.quality_family("r_weapon", ident)
            for e in rec["qualities"] + rec["extra"]:
                if e.get("id"):
                    self.assertIn(e["id"], by_ident,
                                  f"{ident}: {e['id']} was never vetted")

    def test_a_missing_quality_says_which_of_three_reasons_it_is_missing(self):
        """Not available is three different facts and they are not the same
        answer: the ini has no such row, the row exists but its art does not
        ship, or the row is a different mesh entirely."""
        rec = self.idx.quality_family("r_weapon", "510000")
        gone = [q for q in rec["qualities"] if not q.get("available")]
        self.assertTrue(gone, "51000 is a partial family")
        for q in gone:
            self.assertTrue(q["reason"], f"{q['label']} must say why")
        joined = " ".join(q["reason"] for q in gone)
        self.assertIn("does not ship", joined)
        self.assertIn("different mesh", joined)

    def test_outlier_families_are_reported_not_forced_into_five(self):
        """35004 is not a quality ladder -- each digit is a different mesh --
        and a family with one texture is honest about it."""
        rec = self.idx.quality_family("r_weapon", "350049")
        self.assertEqual(rec["mesh"], "c3/weapon/350044.c3")
        for q in rec["qualities"]:
            if q.get("available"):
                self.assertEqual(q["mesh"], rec["mesh"])
        self.assertTrue(rec["shape"])
        bare = self.idx.quality_family("r_weapon", "1050000")
        self.assertFalse(bare["qualities"], "a 7-digit id has no quality digit")
        self.assertIn("6-digit", bare["reason"])

    def test_three_tiers_is_the_dominant_shape_but_not_the_only_one(self):
        """350 + 183 families split (345)(67)(89); the rest are stated, not
        squeezed into three."""
        import collections
        shapes = collections.Counter()
        for o in self.idx.options["r_weapon"]:
            if len(o.ident) == 6 and o.ident[5] == "9":
                shapes[self.idx.quality_family("r_weapon", o.ident)["shape"]] += 1
        self.assertGreater(shapes["three tiers"], 500)
        self.assertGreater(sum(shapes.values()) - shapes["three tiers"], 50,
                           "the outliers must not have been swept away")

    def test_quality_is_a_weapon_property_only(self):
        for slot, ident in (("body", "002132300"), ("armet", "002119310")):
            rec = self.idx.quality_family(slot, ident)
            self.assertFalse(rec["isWeapon"])
            self.assertFalse(rec["qualities"])
            self.assertTrue(rec["reason"])


class SuperAura(unittest.TestCase):
    r"""The always-on aura, and the toggle that shows it.

    The ask was "super items get an aura applied to them ... can you make it so
    I can toggle that aura?".  Two things have to be true for that to be a real
    answer rather than a button:

      * the aura has to be looked up for the id you are actually wearing, and
      * when there is none, the reason has to be the data's ("you are not on
        the Super quality"), not a greyed control.
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_ROOT:
            raise unittest.SkipTest("install not present")
        import coviewer
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.idx = cls.cat.builder
        if cls.idx.super_db() is None:
            raise unittest.SkipTest("tools/superfx.py not importable")

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_super_carries_the_aura_and_the_lower_qualities_do_not(self):
        rec = self.idx.quality_family("r_weapon", "410009")
        got = {q["label"]: q["aura"] for q in rec["qualities"] if q.get("available")}
        self.assertTrue(got["Super"], "410009 must resolve an aura")
        for label in ("Normal", "Refined", "Unique", "Elite"):
            self.assertFalse(got[label], f"{label} must NOT glow in family 41000")
        self.assertEqual(rec["auraQualities"], ["super"])

    def test_the_aura_is_overwhelmingly_but_not_only_the_super_digit(self):
        """575 families glow on ...9 alone and 70 glow at every quality, so the
        UI asks the table per id instead of assuming the rule. Asserting the
        exception matters as much as asserting the rule."""
        import collections
        sig = collections.Counter()
        fam = collections.defaultdict(set)
        for o in self.idx.options["r_weapon"]:
            if len(o.ident) == 6:
                fam[o.ident[:5]].add(o.ident[5])
        for f, digits in fam.items():
            glow = "".join(sorted(d for d in digits if self.idx.has_aura(f + d)))
            if glow:
                sig[glow] += 1
        self.assertGreater(sig["9"], 400, "the rule: only ...9 glows")
        self.assertGreater(sum(v for k, v in sig.items() if k != "9"), 30,
                           "the exceptions must exist and be handled")
        self.assertGreater(sum(sig.values()), 500)

    def test_the_aura_is_a_table_lookup_not_a_filename_guess(self):
        """`c3/effect/<family>/<id>.c3` finds blades and misses 28 other
        families; the lookup goes through Action3DEffect."""
        fams = set()
        for o in self.idx.options["r_weapon"]:
            rec = self.idx.super_effect(o.ident)
            if rec and rec.get("family"):
                fams.add(rec["family"])
        self.assertGreater(len(fams), 20, f"only found {sorted(fams)}")
        self.assertIn("blade", fams)

    def test_switching_to_super_is_the_offered_answer_to_no_aura(self):
        """The panel does not just grey out: it names the id that does glow,
        and that id is equippable."""
        rec = self.idx.quality_family("r_weapon", "410005")
        self.assertFalse(any(q["aura"] for q in rec["qualities"]
                             if q.get("id") == "410005"))
        glowing = [q for q in rec["qualities"] if q.get("available") and q["aura"]]
        self.assertTrue(glowing, "there must be something to offer")
        self.assertEqual(glowing[0]["id"], "410009")
        self.assertIn(glowing[0]["id"],
                      {o.ident for o in self.idx.options["r_weapon"]})


class CollapsiblePanels(unittest.TestCase):
    r"""The bug: `cursor: pointer` with nothing behind it.

    `style.css` styled **`.card h2`** -- both pages -- with a hand cursor and
    `user-select: none`.  Only the builder page carried the machinery
    (`data-card`, a `.cardtoggle`, a `.cardbody` wrapper and the listeners), so
    on the asset browser every one of the eight card headers changed the cursor
    and did nothing at all.  That is exactly what was reported: "I can see the
    cursor change, but it doesnt collaps when clicked."

    A pure-logic test could not have caught it -- the logic was fine and the
    markup was missing -- so these read the actual shipped files.  The rule is
    one-directional and absolute: **anything that offers the pointer must be
    operable.**
    """

    WEBUI = PROJECT / "tools" / "webui"
    PAGES = ("index.html", "builder.html")

    def _cards(self, html):
        """Every `<section class="card" ...>` opening tag on a page."""
        return re.findall(r'<section[^>]*class="[^"]*\bcard\b[^"]*"[^>]*>', html)

    def test_every_card_that_looks_clickable_can_be_clicked(self):
        css = (self.WEBUI / "style.css").read_text("utf-8")
        self.assertIn(".card[data-card] h2", css,
                      "the pointer cursor must be scoped to operable cards")
        self.assertNotRegex(
            css, r"(?m)^\.card h2 \{[^}]*cursor: pointer",
            "an unscoped `.card h2 { cursor: pointer }` is the bug itself")
        self.assertIn(".card.collapsed .cardbody", css,
                      "something has to actually do the hiding")

    def test_both_pages_carry_the_whole_collapse_contract(self):
        for page in self.PAGES:
            html = (self.WEBUI / page).read_text("utf-8")
            cards = self._cards(html)
            self.assertTrue(cards, f"{page}: no cards found")
            for tag in cards:
                self.assertIn("data-card=", tag,
                              f"{page}: a card with no data-card would show the "
                              f"pointer and do nothing: {tag}")
            names = re.findall(r'data-card="([^"]+)"', html)
            self.assertEqual(len(names), len(cards), f"{page}: card/name mismatch")
            self.assertEqual(len(set(names)), len(names),
                             f"{page}: duplicate data-card names collide in storage")
            self.assertGreaterEqual(
                html.count("cardbody"), len(cards),
                f"{page}: every card needs a .cardbody to hide")
            self.assertIn("/ui/cards.js", html,
                          f"{page}: the behaviour has to be loaded")
            self.assertIn('id="btn-collapse-all"', html,
                          f"{page}: collapse-everything must be reachable")

    def test_the_behaviour_lives_in_one_place(self):
        """Two pages, one implementation. The drift between them is what let
        the CSS and the JS disagree for the whole life of the browser page."""
        cards = (self.WEBUI / "cards.js").read_text("utf-8")
        for fn in ("function init(", "function toggleAll(", "function apply("):
            self.assertIn(fn, cards)
        builder = (self.WEBUI / "builder.js").read_text("utf-8")
        app = (self.WEBUI / "app.js").read_text("utf-8")
        for src, name in ((builder, "builder.js"), (app, "app.js")):
            self.assertIn("CardPanels", src, f"{name} must use the shared module")
        self.assertNotIn("card.classList.toggle('collapsed'", builder,
                         "builder.js must not keep a second copy of the logic")

    def test_collapse_all_is_a_single_state(self):
        """Mirror of `cards.js toggleAll()`: with anything open it collapses
        everything; only with nothing open does it expand."""
        names = ["prov", "loadout", "related", "tags"]

        def toggle_all(state):
            any_open = any(not state.get(c) for c in names)
            return {c: any_open for c in names}, any_open

        state = {c: False for c in names}
        state, collapsed = toggle_all(state)
        self.assertTrue(collapsed and all(state.values()))
        state, collapsed = toggle_all(state)
        self.assertFalse(collapsed or any(state.values()))
        state["related"] = False
        state, collapsed = toggle_all(state)
        self.assertTrue(collapsed and all(state.values()),
                        "with one open, C collapses rather than expanding")


class EffectAnchorFollowsTheHand(unittest.TestCase):
    r"""An aura rides a socket, and the socket moves every frame.

    `EffectInstance.tick()` took a parent matrix but only fed it to the ribbon
    history; `_drawEffects` drew the static quads from `inst.anchor`, which was
    whatever was passed at attach time.  So the glow stayed where the hand had
    been when you switched it on and the sword swung out from under it -- the
    same "off in space" symptom as the child-instead-of-sibling bug, arriving
    from the other direction.

    The socket really does move: this asserts it as a distance, so "the glow
    follows the swing" is a number rather than an impression.
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_ROOT:
            raise unittest.SkipTest("install not present")
        import coviewer
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_gl_js_adopts_the_parent_matrix_for_the_static_parts(self):
        src = (PROJECT / "tools" / "webui" / "gl.js").read_text("utf-8")
        m = re.search(r"setEffectTime\(ms, parentFor\) \{(.+?)\n  \}", src, re.S)
        self.assertIsNotNone(m, "gl.js must still have setEffectTime")
        self.assertIn("f.anchor.set(parent)", m.group(1),
                      "the moving parent must reach inst.anchor, which is what "
                      "_drawEffects reads for the phy quads")
        self.assertIn("inst.anchor", src)

    def test_the_weapon_socket_moves_far_enough_that_a_frozen_anchor_shows(self):
        """If the socket barely moved, freezing the anchor would be invisible
        and this whole class would be theatre. On 002132300 swinging a 410
        blade it moves far more than the weapon's own length."""
        import math
        import builder as builder_mod
        import parts as partsmod
        if builder_mod.animmod is None:
            self.skipTest("tools/anim.py not importable")
        db = builder_mod.animmod.AnimDB(ROOT)
        clip = db.clip("002132300", "401", weapon="410009")
        if clip is None or not clip.frame_count:
            self.skipTest("action 401 not in this install")
        mesh = next(o.mesh for o in self.cat.builder.options["body"]
                    if o.ident == "002132300")
        raw = self.cat.read(mesh)
        pts = []
        for f in range(clip.frame_count):
            s = partsmod.socket_anchors(raw, motion_set=clip.motion,
                                        frame=f).get("v_r_weapon")
            if s is not None:
                pts.append(tuple(s.matrix[12:15]))
        self.assertGreaterEqual(len(pts), 5, "expected a real clip")
        span = max(math.dist(p, q) for p in pts for q in pts)
        self.assertGreater(span, 50.0,
                           f"the hand only moved {span:.1f} units over the "
                           f"swing - a frozen anchor would not be visible and "
                           f"this test would prove nothing")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class ModelCatalogue(unittest.TestCase):
    r"""Monsters, NPCs, ghosts, mounts, roles -- the second builder mode.

    The claim that shapes the whole design and is asserted here as a fact
    about the shipped data rather than a property of the code:

        **A monster is one FILE per action, not one mesh with a track set.**

    and the one the brief asked for explicitly:

        **A model's action list only ever offers files that exist.**
    """

    @classmethod
    def setUpClass(cls):
        import coviewer
        import models
        cls.models = models
        cls.cat = coviewer.Catalog(ROOT)
        cls.cat.wait_tables()
        cls.mc = cls.cat.models

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    # -- the invariant the brief named -------------------------------------
    def test_every_offered_action_resolves_to_a_file_that_ships(self):
        """The whole catalogue: an action marked available must name a mesh
        AND a motion the client can actually open. 1,100 of the 3,260 motion
        files 3dmotion.ini declares are absent from this install, so this is a
        real filter, not a formality."""
        checked = withheld = 0
        for m in self.mc.models:
            if m.kind == "effect":
                continue
            for a in m.actions:
                if not a.available:
                    withheld += 1
                    self.assertTrue(a.reason,
                                    f"{m.key} {a.code}: withheld with no reason")
                    continue
                checked += 1
                self.assertTrue(self.cat.exists(a.motion),
                                f"{m.key} {a.code}: motion {a.motion} missing")
                self.assertTrue(self.cat.exists(a.mesh),
                                f"{m.key} {a.code}: mesh {a.mesh} missing")
        self.assertGreater(checked, 2000, "expected a real corpus")
        self.assertGreater(withheld, 0,
                           "this install is missing 1,100 motion files; if "
                           "nothing was withheld the filter is not running")

    def test_a_motion_only_action_binds_over_a_mesh_that_has_geometry(self):
        """`c3/monster/103/340.c3` is MOTI-only -- 453 files under c3/monster
        are (docs/meshtex.md §1). It has to be bound over a file that does
        carry PHY, or it draws nothing."""
        import attach
        n = 0
        for m in self.mc.models:
            if m.kind == "effect":
                continue
            for a in m.actions:
                if not a.available or a.self_contained:
                    continue
                self.assertNotEqual(a.mesh, a.motion,
                                    f"{m.key} {a.code}: motion-only file used "
                                    f"as its own mesh")
                n += 1
                if n > 40:
                    return
                pm = attach.PartMesh.parse(self.cat.read(a.mesh), a.mesh)
                self.assertTrue(
                    any(getattr(c.phy, "vertices", None) for c in pm.chunks),
                    f"{m.key} {a.code}: base mesh {a.mesh} has no geometry")
        self.assertGreater(n, 0, "no motion-only action found at all")

    def test_a_self_contained_action_really_carries_its_own_geometry(self):
        """The other half: 69 of monster 103's 80 codes point at a file with
        PHY *and* MOTI, which is why changing the action changes the mesh."""
        import attach
        m = self.mc.get("monster:103")
        self.assertIsNotNone(m, "c3/monster/103 should be in this install")
        sc = [a for a in m.actions if a.self_contained]
        self.assertGreater(len(sc), 10)
        for a in sc[:6]:
            self.assertEqual(a.mesh, a.motion)
            pm = attach.PartMesh.parse(self.cat.read(a.mesh), a.mesh)
            self.assertTrue(any(getattr(c.phy, "vertices", None)
                                for c in pm.chunks))
            self.assertTrue(any(c.motion is not None for c in pm.chunks))

    def test_both_layouts_ship_and_both_are_represented(self):
        """Neither layout is an edge case: this install has per-action-mesh
        families, shared-skeleton families and mixed ones."""
        layouts = {m.to_json()["layout"] for m in self.mc.models
                   if m.kind in self.models.POSED_KINDS}
        self.assertIn("per-action meshes", layouts)
        self.assertIn("skeleton + motion sets", layouts)
        self.assertIn("mixed", layouts)

    # -- the monster.json question -----------------------------------------
    def test_monster_json_carries_no_art_link_and_none_is_invented(self):
        """`type` is a sequential index and `bodyType` is 0 on every row, so
        pairing a row with a mesh directory is the USER's assertion. Nothing
        in the catalogue may claim otherwise."""
        rows = self.mc.monsters
        self.assertGreaterEqual(len(rows), 300)
        self.assertEqual({r.body_type for r in rows}, {0},
                         "bodyType is 0 on every row - there is no join column")
        # `type` is a monster id, not an appearance: it runs 1..9028 over 374
        # rows and only 14 of its values coincidentally match a directory name
        # under c3/monster/. Joining on it would be wrong 96% of the time.
        dirs = {m.ident for m in self.mc.models if m.kind == "monster"}
        overlap = {str(r.type) for r in rows} & dirs
        self.assertLess(len(overlap) / len(dirs), 0.3,
                        f"{len(overlap)} of {len(dirs)} directories share a "
                        f"name with a monster.json type - too few to be a link")
        for m in self.mc.models:
            self.assertNotIn("monsterRow", m.to_json(),
                             "a model must not carry a monster.json row as "
                             "though the data linked them")
        self.assertIn("YOUR pairing", self.models.MONSTER_LINK_NOTE)

    def test_zoom_percent_is_the_field_that_actually_matters(self):
        """60 to 350: ignoring it renders monsters at the wrong size, by up to
        3.5x. The catalogue exposes it as a plain scale factor."""
        zooms = [r.zoom_percent for r in self.mc.monsters]
        self.assertEqual(min(zooms), 60)
        self.assertEqual(max(zooms), 350)
        r = self.mc.monster_row(1)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["scale"], r["zoomPercent"] / 100.0, places=6)

    # -- naming -------------------------------------------------------------
    def test_npc_json_names_models_through_standby_motion(self):
        """`simple_object` -> 3DSimpleObj.ini reaches a mesh on disk for only
        100 of 437 rows; `standby_motion` is a literal 3dmotion.ini key and
        reaches a file that ships for 435. So the motion path is the link."""
        named = [m for m in self.mc.models
                 if m.kind in ("npc", "npc_simple") and m.names]
        self.assertGreater(len(named), 20)
        total = sum(len(m.names) for m in named)
        self.assertGreater(total, 250,
                           "most of npc.json's 437 rows should land on a family")

    def test_action_names_come_from_anim_py_not_a_second_table(self):
        """The action vocabulary is shared with players: 100 idle, 110 walk,
        401 swing. A second table here would drift."""
        import anim
        m = self.mc.get("monster:103")
        for a in m.actions:
            meta = anim.ACTIONS.get(a.code)
            if meta is None or not meta.name:
                continue
            self.assertEqual(a.label.lower(), meta.name.lower())
            self.assertEqual(a.group, meta.group)

    # -- aliases ------------------------------------------------------------
    def test_alias_codes_point_at_the_canonical_one_not_the_other_way(self):
        """3dmotion.ini gives monster 103 eighty codes over sixteen files.
        `401.c3` belongs to action 401 even though 140 also resolves to it and
        sorts first -- calling 401 "the same clip as 140" is backwards."""
        m = self.mc.get("monster:103")
        by = {a.code: a for a in m.actions}
        self.assertTrue(by["401"].distinct, "401 must own 401.c3")
        self.assertFalse(by["140"].distinct)
        self.assertEqual(by["140"].alias_of, "401")
        self.assertEqual(by["100"].distinct, True)
        self.assertEqual(by["105"].alias_of, "100")
        # and the counts line up with the files on disk
        self.assertEqual(m.clips,
                         len({a.motion for a in m.actions if a.available}))
        self.assertLess(m.clips, len(m.actions),
                        "103 has far more codes than clips")

    def test_every_family_the_picker_offers_has_an_idle_that_plays(self):
        """A list row that draws nothing is worse than no row -- so the
        default query withholds them. They are not deleted: they stay in the
        catalogue with a `note` saying why, and --audit still counts them."""
        for m in self.mc.query()["matched"]:
            if m.kind == "effect":
                continue
            d = m.default_action()
            self.assertTrue(d, f"{m.key} has no default action")
            self.assertTrue(m.action(d).available,
                            f"{m.key}: default action unplayable")
        withheld = [m for m in self.mc.models
                    if m.kind != "effect" and m.playable == 0]
        for m in withheld:
            self.assertTrue(m.note, f"{m.key} withheld with no reason given")
            self.assertNotIn(m, self.mc.query()["matched"])

    # -- coverage -----------------------------------------------------------
    def test_the_families_this_install_actually_has(self):
        counts = {k["kind"]: k["count"] for k in self.mc.kinds()}
        self.assertGreaterEqual(counts["monster"], 60)
        self.assertGreaterEqual(counts["npc"] + counts["npc_simple"], 50)
        self.assertEqual(counts["ghost"], 2)
        self.assertGreaterEqual(counts["mount"], 1)
        self.assertEqual(counts["role"], 8)
        self.assertGreater(counts["effect"], 2000)

    def test_textures_come_from_meshtex_and_reach_the_monster_tree(self):
        """`c3/monster/103/100.c3` has no sibling .dds at all -- the same-stem
        guess finds nothing. meshtex resolves it through 3dmotion.ini ->
        armor.ini, which is why that module is imported rather than a local
        rule invented."""
        m = self.mc.get("monster:103")
        self.assertEqual(m.texture, "c3/texture/103000000.dds")
        self.assertEqual(m.texture_kind, "authored")
        self.assertFalse(self.cat.exists("c3/monster/103/100.dds"))
        posed = [x for x in self.mc.models
                 if x.kind in self.models.POSED_KINDS]
        have = sum(1 for x in posed if x.texture and self.cat.exists(x.texture))
        self.assertGreater(have / len(posed), 0.9,
                           "nearly every model should resolve a texture")

    # -- the query surface --------------------------------------------------
    def test_kind_counts_are_cross_filtered_like_every_other_facet(self):
        """A chip's number is "how many would I get if I picked this", so
        picking one can never produce an empty list."""
        res = self.mc.query()
        for kind, n in res["kindCounts"].items():
            self.assertEqual(len(self.mc.query(kind=kind)["matched"]), n,
                             f"{kind}: chip promised {n}")

    def test_search_reaches_the_name_the_id_and_the_directory(self):
        self.assertTrue(self.mc.query(text="103")["matched"])
        self.assertTrue(self.mc.query(text="c3/monster")["matched"])
        hit = self.mc.query(kind="npc_simple", text="storekeeper")["matched"]
        self.assertTrue(hit, "npc.json names must be searchable")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class ModelModeUi(unittest.TestCase):
    """The page logic that is pure logic, plus the shipped-file contract.

    These read `builder.html` / `builder.js` rather than mirroring them,
    because the failure mode this whole area has already produced once is
    markup and behaviour drifting apart (docs/viewer.md §5).
    """

    WEBUI = PROJECT / "tools" / "webui"

    def setUp(self):
        self.html = (self.WEBUI / "builder.html").read_text("utf-8")
        self.js = (self.WEBUI / "builder.js").read_text("utf-8")
        self.css = (self.WEBUI / "style.css").read_text("utf-8")

    def test_the_mode_switch_exists_and_both_rails_are_reachable(self):
        for need in ('id="mode-character"', 'id="mode-model"',
                     'id="model-rail"', 'id="model-list"', 'id="model-kinds"',
                     'id="card-model"', 'id="card-monster"'):
            self.assertIn(need, self.html, f"builder.html is missing {need}")
        self.assertIn("setMode('model')", self.js)
        self.assertIn("setMode('character')", self.js)

    def test_every_hideable_control_can_actually_be_hidden(self):
        r"""`.hidden` is one class; `label.chk` is element+class and wins.

        Found while photographing model mode: the Super aura toggle ships as
        `class="chk hidden"` and has been permanently visible since it was
        written, because `label.chk { display: flex }` outranks the generic
        `.hidden { display: none }` at the bottom of the file. Anything the
        JS toggles `hidden` on must have a rule that can win.
        """
        self.assertRegex(self.css, r"label\.chk\.hidden[^{]*\{[^}]*display:\s*none")
        for page in ("index.html", "builder.html"):
            html = (self.WEBUI / page).read_text("utf-8")
            for tag in re.findall(r'<[a-z]+[^>]*class="[^"]*\bhidden\b[^"]*"[^>]*>',
                                  html):
                m = re.match(r"<([a-z]+)[^>]*class=\"([^\"]*)\"", tag)
                el, classes = m.group(1), m.group(2).split()
                for c in classes:
                    if c == "hidden":
                        continue
                    rule = rf"(?m)^{el}\.{re.escape(c)}\s*\{{[^}}]*display:"
                    if re.search(rule, self.css):
                        self.assertRegex(
                            self.css,
                            rf"{el}\.{re.escape(c)}\.hidden[^{{]*\{{[^}}]*"
                            rf"display:\s*none",
                            f"{page}: <{el} class=\"{c} hidden\"> can never be "
                            f"hidden - {el}.{c} outranks .hidden")

    def test_hiding_a_rail_beats_its_id_selector(self):
        """`.hidden` is a class and the rails are ids, so `display: none` loses
        on specificity unless it is spelled out. That is a silent
        nothing-happens bug of exactly the kind §5 was about."""
        self.assertRegex(self.css,
                         r"#slots-rail\.hidden[^{]*\{[^}]*display:\s*none")
        self.assertRegex(self.css,
                         r"#model-rail\.hidden[^{]*\{[^}]*display:\s*none")

    def test_the_selected_action_survives_the_distinct_clips_filter(self):
        """A filter that silently moves you off the thing you chose is worse
        than no filter, and this one nearly did: 401 is an alias of 140 under
        a naive first-come rule."""
        self.assertIn("a.code === B.model.action", self.js)

    def test_model_mode_never_opens_a_slot_picker(self):
        """There is nothing to equip on a monster."""
        m = re.search(r"function openPicker\(slot\) \{(.{0,200})", self.js, re.S)
        self.assertIsNotNone(m)
        self.assertIn("B.mode === 'model'", m.group(1))

    def test_the_action_dropdown_is_shared_not_duplicated(self):
        """One `#anim-action`, one play/pause, one speed slider, one frame
        scrubber -- both modes drive the same controls, because that part of
        the job really is the same."""
        self.assertEqual(self.html.count('id="anim-action"'), 1)
        self.assertEqual(self.html.count('id="btn-anim-play"'), 1)
        self.assertIn("if (B.mode === 'model') return fillModelActions();",
                      self.js)
        self.assertIn("if (B.mode === 'model') return rebuildModel", self.js)

    def test_zoom_is_a_uniform_scale_on_the_model_matrix(self):
        m = re.search(r"function zoomMatrix\(\) \{(.+?)\n\}", self.js, re.S)
        self.assertIsNotNone(m)
        self.assertIn("s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, 0, 0, 0, 1",
                      m.group(1))

    def test_the_keyboard_reference_documents_the_new_keys(self):
        for k in ("<kbd>M</kbd>", "<kbd>[</kbd>"):
            self.assertIn(k, self.html)


class TerrainMesh(unittest.TestCase):
    """`tools/terrain.py` -- the ground, in the shape gl.js already draws.

    The point of these gates is that the terrain must be indistinguishable from
    a C3 chunk to the renderer. If it drifts from `mesh_to_json`'s contract,
    `Viewer.setMeshes()` will not fail loudly -- it will draw nothing, or draw
    it inside out -- so the contract is asserted here instead.
    """

    @staticmethod
    def _grid():
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            raise unittest.SkipTest("cross-checks against the open client's "
                                    "gamemap, which is a separate project")
        # A room with a pillar, so blocked and walkable both appear.
        rows = [
            "########",
            "#......#",
            "#..##..#",
            "#......#",
            "########",
        ]
        return GM.GameMap.from_cells(9001, "testroom", 8, 5, rows)

    def test_the_payload_has_every_key_gl_js_reads(self):
        import terrain
        m = terrain.build_patch(self._grid(), 3, 2, radius=3)
        # Exactly the fields gl.js touches on `d.meta`.
        for key in ("positions", "normals", "uv0", "colors", "indices",
                    "vertexCount", "faceCount", "bboxRender", "isSocket",
                    "twoSided", "keys", "index", "name"):
            self.assertIn(key, m, f"gl.js reads meta.{key}")
        self.assertIn("alphas", m["keys"])
        self.assertFalse(m["isSocket"])

    def test_array_lengths_agree_with_the_vertex_count(self):
        import terrain
        m = terrain.build_patch(self._grid(), 3, 2, radius=3)
        n = m["vertexCount"]
        self.assertEqual(len(m["positions"]), n * 3)
        self.assertEqual(len(m["normals"]), n * 3)
        self.assertEqual(len(m["uv0"]), n * 2)
        self.assertEqual(len(m["indices"]), m["faceCount"] * 3)

    def test_one_quad_per_cell_in_the_window(self):
        import terrain
        g = self._grid()
        m = terrain.build_patch(g, 3, 2, radius=3)
        w = m["window"]
        self.assertEqual(m["vertexCount"], w["w"] * w["h"] * 4)
        self.assertEqual(m["faceCount"], w["w"] * w["h"] * 2)

    def test_the_window_is_clamped_to_the_map(self):
        import terrain
        g = self._grid()
        m = terrain.build_patch(g, 0, 0, radius=99)
        w = m["window"]
        self.assertEqual((w["x0"], w["y0"]), (0, 0))
        self.assertEqual((w["x1"], w["y1"]), (g.width - 1, g.height - 1))

    def test_indices_stay_inside_a_uint16_buffer(self):
        """gl.js uploads indices as Uint16Array; >65535 vertices would wrap
        silently and draw garbage."""
        import terrain
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        big = GM.GameMap(1, "big", 400, 400, bytes(400 * 400))
        m = terrain.build_patch(big, 200, 200, radius=terrain.DEFAULT_RADIUS)
        self.assertLess(m["vertexCount"], 65536)
        self.assertLess(max(m["indices"]), 65536)

    def test_winding_is_counter_clockwise_in_the_z_up_frame(self):
        """gl.js sets frontFace(CCW) + cullFace(BACK). A ground wound the other
        way is invisible from above, which reads as 'the map did not load'."""
        import terrain
        m = terrain.build_patch(self._grid(), 3, 2, radius=1)
        p = m["positions"]

        def vert(i):
            return (p[i * 3], p[i * 3 + 1], p[i * 3 + 2])

        a, b, c = (vert(m["indices"][i]) for i in range(3))
        # z of the cross product of (b-a) and (c-a): positive == CCW seen from +Z
        ux, uy = b[0] - a[0], b[1] - a[1]
        vx, vy = c[0] - a[0], c[1] - a[1]
        self.assertGreater(ux * vy - uy * vx, 0)

    def test_elevation_reaches_the_geometry(self):
        import terrain
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        g = GM.GameMap(1, "hill", 3, 3, bytes([1] * 9),
                       elevation=[0, 0, 0, 0, 7, 0, 0, 0, 0])
        m = terrain.build_patch(g, 1, 1, radius=1)
        self.assertIn(7 * terrain.ZSCALE, m["positions"])

    def test_the_ground_texture_is_a_png_of_the_right_size(self):
        import terrain
        g = self._grid()
        m = terrain.build_patch(g, 3, 2, radius=3)
        png = terrain.patch_texture(g, m["window"])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual(w, m["window"]["w"] * terrain.TEX_CELL_PX)
        self.assertEqual(h, m["window"]["h"] * terrain.TEX_CELL_PX)

    def test_the_ground_texture_distinguishes_walkable_from_blocked(self):
        import terrain
        g = self._grid()
        m = terrain.build_patch(g, 3, 2, radius=3)
        plain = terrain.patch_texture(g, m["window"])
        marked = terrain.patch_texture(g, m["window"], mark=(3, 1))
        self.assertNotEqual(plain, marked, "the player mark must be visible")
        self.assertNotEqual(terrain.COLOUR_WALKABLE, terrain.COLOUR_BLOCKED)

    def test_world_mapping_puts_map_south_at_negative_world_y(self):
        import terrain
        x0, y0 = terrain.world_xy(0, 0)
        x1, y1 = terrain.world_xy(0, 1)          # one cell south
        self.assertEqual((x0, y0), (0.0, 0.0))
        self.assertLess(y1, y0)
        self.assertEqual(terrain.world_xy(1, 0)[0], terrain.CELL)


def _synthetic_puzzle(pul_w=15, pul_h=9, grid=256):
    """A PuzzleMap with newbie's shape and no art behind it. Enough to exercise
    every bit of the placement arithmetic without an install."""
    import puzzle
    k = pul_w * grid // puzzle.CELL_PX_W
    w = k + pul_h * grid // puzzle.CELL_PX_H
    return puzzle.PuzzleMap(name="synthetic", map_id=None, map_width=w,
                            map_height=w, grid=grid, pul_w=pul_w, pul_h=pul_h,
                            tiles=[puzzle.EMPTY] * (pul_w * pul_h))


class PuzzlePlacement(unittest.TestCase):
    """`tools/puzzle.py` -- where the painted ground art sits on the cell grid.

    The arithmetic is the whole finding, so it is pinned here rather than only
    in prose: an off-by-one in K, or a transposed 32/16, would draw a map that
    looks entirely plausible and is a few cells wrong everywhere.
    """

    def test_a_cell_is_a_64_by_32_pixel_diamond(self):
        pm = _synthetic_puzzle()
        ox, oy = pm.corner_px(0, 0)
        self.assertEqual(pm.corner_px(1, 0), (ox + 32, oy + 16))   # +x: down-right
        self.assertEqual(pm.corner_px(0, 1), (ox - 32, oy + 16))   # +y: down-left
        # ... so one cell step in each axis spans a 64 x 32 diamond.
        self.assertEqual(pm.corner_px(1, 1), (ox, oy + 32))

    def test_the_origin_is_the_images_top_left_corner(self):
        pm = _synthetic_puzzle()
        self.assertEqual(pm.corner_px(0, pm.k), (0.0, 0.0))
        # and the opposite corner of the painted rectangle is the far corner of
        # the cell grid, which is what forces the map to be square.
        self.assertEqual(pm.corner_px(pm.map_width, pm.map_width - pm.k),
                         (float(pm.px_w), float(pm.px_h)))

    def test_the_map_size_identity_holds_by_construction(self):
        for shape in ((15, 9, 256), (10, 7, 256), (4, 25, 256), (188, 75, 128)):
            pm = _synthetic_puzzle(*shape)
            with self.subTest(shape=shape):
                self.assertTrue(pm.consistent)
                self.assertEqual(pm.implied_size, pm.px_w / 64 + pm.px_h / 32)

    def test_pixel_to_cell_inverts_cell_to_pixel(self):
        """A cell's centre pixel must land back inside that same cell, or the
        art and the passability would be a cell apart everywhere."""
        import math
        pm = _synthetic_puzzle()
        for x, y in ((0, 0), (17, 3), (60, 71), (pm.map_width - 1, 4)):
            px, py = pm.cell_px(x, y)
            fx, fy = pm.px_to_cell(px, py)
            self.assertAlmostEqual(fx, x + 0.5, places=6)
            self.assertAlmostEqual(fy, y + 0.5, places=6)
            self.assertEqual((math.floor(fx), math.floor(fy)), (x, y))

    def test_window_rect_is_the_bounding_box_of_the_projected_corners(self):
        pm = _synthetic_puzzle()
        x0, y0, x1, y1 = 20, 30, 44, 51
        rect = pm.window_rect(x0, y0, x1, y1)
        pts = [pm.corner_px(gx, gy)
               for gx in range(x0, x1 + 2) for gy in range(y0, y1 + 2)]
        self.assertEqual(rect[0], min(p[0] for p in pts))
        self.assertEqual(rect[1], min(p[1] for p in pts))
        self.assertEqual(rect[2], max(p[0] for p in pts))
        self.assertEqual(rect[3], max(p[1] for p in pts))

    def test_the_rect_is_exactly_twice_the_window_in_each_axis(self):
        """(w + h) cells wide and (w + h)/2 tall, in cell units -- the window is
        a diamond in the painted image, so half the texture is off the mesh."""
        pm = _synthetic_puzzle()
        rect = pm.window_rect(10, 10, 58, 58)
        self.assertEqual(rect[2] - rect[0], (49 + 49) * 32)
        self.assertEqual(rect[3] - rect[1], (49 + 49) * 16)

    def test_terrain_uvs_switch_to_the_art_without_moving_a_vertex(self):
        """The placement is linear in the same corner coordinates the mesh
        already uses, so turning the art on must change UVs and nothing else."""
        import terrain
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        pm = _synthetic_puzzle()
        g = GM.GameMap(1, "synthetic", pm.map_width, pm.map_height,
                       bytes([1]) * (pm.map_width * pm.map_height))
        plain = terrain.build_patch(g, 61, 109, radius=6)
        art = terrain.build_patch(g, 61, 109, radius=6, puzzle=pm)
        self.assertEqual(plain["positions"], art["positions"])
        self.assertEqual(plain["indices"], art["indices"])
        self.assertNotEqual(plain["uv0"], art["uv0"])
        self.assertEqual(plain["ground"]["source"], "passability")
        self.assertEqual(art["ground"]["source"], "puzzle")
        # Every UV must land inside the cropped texture, or gl.js will clamp and
        # smear the edge cells.
        self.assertGreaterEqual(min(art["uv0"]), 0.0)
        self.assertLessEqual(max(art["uv0"]), 1.0)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_identity_holds_across_the_shipped_corpus(self):
        """131 of the 132 maps with a `.pul`. `sky` is the one exception and it
        is named, not swept up in a tolerance."""
        import puzzle
        res = puzzle.verify()
        self.assertGreaterEqual(res["ok"], 131)
        bad = [r["name"] for r in res["rows"] if r["status"] == "mismatch"]
        self.assertEqual(bad, ["sky"])

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_every_walkable_cell_of_four_different_maps_is_on_painted_art(self):
        """The sharp test: a cell you can stand on must be on the picture. Four
        maps whose puzzle-to-cell ratios span the whole shipped range, so a rule
        fitted to one of them would fail here.

        Judged on the **base cell grid**. A cell that only a `TERRAIN` scene
        layer opens is off the picture on purpose -- `newbie`'s stepping stones
        hang over the void because the scenery is the ground there -- so those
        cells are excluded here and pinned separately below.
        """
        import puzzle
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        lib = puzzle.PuzzleLibrary()
        maps = GM.MapLibrary()
        for name in ("newbie", "arena", "boa", "Dcloister"):
            with self.subTest(map=name):
                pm = lib.get(name)
                self.assertIsNotNone(pm, lib.reason)
                grid = maps.load(pm.map_id)
                self.assertIsNotNone(grid, maps.reason)
                cov = puzzle.coverage(pm, grid, base_only=True)
                self.assertGreater(cov["walkable"], 1000)
                self.assertEqual(cov["offImage"], 0)
                self.assertEqual(cov["onEmptyTile"], 0)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_cells_off_the_art_on_newbie_are_exactly_the_scene_layers(self):
        """Task #27 measured 100.00% of `newbie`'s walkable cells on painted
        art. That stayed true *and* the map has a walkable bridge over the
        void, because the bridge cells are not in the cell grid at all -- the
        scene layers put them there. Both halves are asserted together so
        neither can be "fixed" by weakening the other."""
        import puzzle
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        pm = puzzle.PuzzleLibrary().get("newbie")
        grid = GM.MapLibrary().load(pm.map_id)
        cov = puzzle.coverage(pm, grid)
        # the base grid is still exactly 100% on art
        self.assertEqual(cov["baseOffImage"], 0)
        self.assertEqual(cov["baseOnEmptyTile"], 0)
        self.assertEqual(cov["baseWalkable"], 1561)
        # and every cell that is not on art is one the scenery opened
        self.assertEqual(cov["sceneryWalkable"], 111)
        self.assertEqual(cov["offImage"] + cov["onEmptyTile"],
                         cov["sceneryOffImage"] + cov["sceneryOnEmptyTile"])
        # 101 of the 111 hang over unpainted void -- that is the black gap in
        # `out/viewer/shots/placement-newbie.png` the owner asked about. The
        # other 10 are the stones nearest the two islands, which overlap the
        # painted edge; they are named rather than rounded away.
        self.assertEqual(cov["sceneryOnEmptyTile"], 101)
        self.assertEqual(cov["sceneryOnArt"], 10)

    def test_a_map_with_no_placeable_art_degrades_with_a_reason(self):
        """The four `.pux` maps, and any client with no install, must fall back
        to the passability shading rather than raise."""
        import terrain
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        g = GM.GameMap.from_cells(9002, "no-such-map-anywhere", 3, 1, ["..."])
        pm, why = terrain.open_puzzle(g)
        self.assertIsNone(pm)
        self.assertTrue(why, "a refusal must say why")

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_decimating_the_art_picks_the_same_pixels(self):
        """`scale` is an integer decimation, not a resample -- so scale N must
        agree pixel-for-pixel with every Nth pixel of scale 1, including on a
        rectangle that straddles tile boundaries unaligned."""
        import puzzle
        pm = puzzle.PuzzleLibrary().get("newbie")
        rect = (-137, 991, 1001, 1601)
        w1, h1, full = pm.render(rect, scale=1)
        for sc in (2, 3, 4):
            w, h, small = pm.render(rect, scale=sc)
            with self.subTest(scale=sc):
                for oy in range(0, h, 7):
                    for ox in range(0, w, 7):
                        sx, sy = ox * sc, oy * sc
                        if sx >= w1 or sy >= h1:
                            continue
                        o, p = (oy * w + ox) * 3, (sy * w1 + sx) * 3
                        self.assertEqual(small[o:o + 3], full[p:p + 3])

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_ground_texture_is_a_png_the_size_of_the_rect(self):
        import puzzle
        import terrain
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        pm = puzzle.PuzzleLibrary().get("arena")
        grid = GM.MapLibrary().load(pm.map_id)
        patch = terrain.build_patch(grid, 48, 48, radius=8, puzzle=pm)
        png = terrain.art_texture(pm, patch, scale=2)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        r = patch["ground"]["rect"]
        self.assertEqual(w, (r[2] - r[0]) // 2)
        self.assertEqual(h, (r[3] - r[1]) // 2)
class SceneFormat(unittest.TestCase):
    """`map/Scene/*.scene`: the part header and the per-cell array."""

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_every_shipped_scene_file_is_consumed_to_its_exact_length(self):
        """299 files, no slack. If any field width were wrong the walk would
        overrun or fall short somewhere in the corpus."""
        import scene
        import coroot
        root = Path(coroot.default_root())
        files = sorted((root / "map" / "Scene").glob("*.scene"))
        self.assertGreaterEqual(len(files), 299)
        bad = []
        parts = cells = 0
        for p in files:
            try:
                sf = scene.parse_scene(p.read_bytes(), p.name)
            except Exception as e:                          # noqa: BLE001
                bad.append((p.name, str(e)))
                continue
            if sf.bytes_unconsumed != 0:
                bad.append((p.name, f"{sf.bytes_unconsumed} bytes left over"))
            parts += len(sf.parts)
            cells += sum(len(q.cells) for q in sf.parts)
        self.assertEqual(bad, [])
        self.assertGreater(parts, 290)
        self.assertGreater(cells, 8000)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_blocked_flag_is_a_flag(self):
        """The scene cell's first u32 is the same "invalid movement" field the
        DMap cell carries, so it must only ever be 0 or 1. It is, on all 8,393
        cells -- which is why treating it as passability is not a stretch."""
        import scene
        import coroot
        seen = set()
        for p in sorted((Path(coroot.default_root()) / "map" / "Scene").glob("*.scene")):
            for q in scene.parse_scene(p.read_bytes(), p.name).parts:
                seen.update(c[0] for c in q.cells)
        self.assertEqual(sorted(seen), [0, 1])

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_part_headers_first_pair_is_the_pixel_offset_not_an_origin(self):
        """`refs/.../Scene.md` calls field[0..1] "Origin X/Y". Matching the
        `.scene` binaries against the `map/ScenePart/*.Part` text they were
        compiled from shows they are the sprite's **pixel** offset -- the
        .Part's own `OffsetX=` / `OffsetY=` -- on the large majority of pairs.
        field[6..7], which the wiki calls the offset, is the part's *cell*
        offset inside the scene and has no counterpart in the text at all."""
        import scene
        import coroot
        root = Path(coroot.default_root())
        by_title = {}
        for p in sorted((root / "map" / "Scene").glob("*.scene")):
            for q in scene.parse_scene(p.read_bytes(), p.name).parts:
                by_title.setdefault(q.title.lower(), q)
        matched = agree = 0
        for f in sorted((root / "map" / "ScenePart").glob("*.Part")):
            text = {}
            for line in f.read_text("latin1", errors="replace").splitlines():
                if "=" in line and not line.startswith("Cell["):
                    k, v = line.strip().split("=", 1)
                    text[k] = v
            q = by_title.get(str(text.get("AniTitle", "")).lower())
            if q is None:
                continue
            # Several .Part files share one AniTitle (bridge05, bridge05-1,
            # bridge05-2 all draw bridge05.tga at different sizes), so only the
            # pairs whose footprint agrees are the same piece.
            if (q.width, q.height) != (int(text["Width"]), int(text["Height"])):
                continue
            matched += 1
            self.assertEqual(q.frame_interval, int(text["AniInterval"]))
            self.assertEqual(q.thickness, int(text["Thick"]))
            if q.pixel_offset == (int(text["OffsetX"]), int(text["OffsetY"])):
                agree += 1
        self.assertGreaterEqual(matched, 90)
        self.assertGreaterEqual(agree, 70,
                                "field[0..1] should equal the .Part's OffsetX/Y")


class ScenePassability(unittest.TestCase):
    """A TERRAIN layer carries passability, and it replaces the cell grid."""

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_newbie_bridge_joins_two_islands_that_the_grid_leaves_apart(self):
        """The whole finding in one assertion. `newbie`'s starting island and
        its village are separate components of the DMap cell grid; the twenty
        stepping stones are what connect them, and they connect them only
        because the scene layers supply the passability."""
        import scene
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        base = GM.MapLibrary(scenery=False).load(1010)
        over = GM.MapLibrary().load(1010)
        self.assertEqual(base.walkable_count, 1561)
        self.assertEqual(over.walkable_count, 1672)
        cb = scene.components(base.walk, base.width, base.height)
        co = scene.components(over.walk, over.width, over.height)
        self.assertEqual(cb[0], 1388)          # the village, alone
        self.assertEqual(co[0], 1604)          # village + island + bridge
        # and the spawn really is on the far side of the join
        self.assertTrue(over.walkable(61, 109))
        self.assertTrue(over.walkable(91, 79))
        self.assertFalse(base.walkable(69, 106))
        self.assertTrue(over.walkable(69, 106))
        self.assertTrue(over.from_scenery(69, 106))

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_cells_run_backwards_from_the_layer_origin(self):
        """The rule that took the most work to pin. Part cell (i, j) lands on
        map cell (anchor.x - i, anchor.y - j), where anchor = layer origin +
        the part's cell offset. Forward placement is not a near miss: it leaves
        `newbie` split and scatters three orphan fragments into the water."""
        import scene
        import dmap
        import coroot
        import sys as _sys
        _sys.path.insert(0, str(PROJECT))
        try:
            from client import gamemap as GM
        except ImportError:
            self.skipTest("cross-checks against the open client's "
                          "gamemap, which is a separate project")
        root = Path(coroot.default_root())
        d = dmap.parse(root / "map" / "map" / "newbie.DMap", verify=False)
        lib = scene.SceneLibrary(root)
        sc = scene.gather(d.layers, lib)
        base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
        over, _ = scene.apply_passability(base, d.width, d.height, sc.scenes)
        self.assertEqual(scene.components(over, d.width, d.height)[0], 1604)

        # the forward reading, for contrast
        fwd = bytearray(base)
        for p in sc.scenes:
            ax, ay = p.anchor
            for k, (m, _s, _e) in enumerate(p.cells):
                x, y = ax + k % p.width, ay + k // p.width
                if 0 <= x < d.width and 0 <= y < d.height:
                    fwd[y * d.width + x] = 0 if m else 1
        cf = scene.components(bytes(fwd), d.width, d.height)
        self.assertEqual(cf[0], 1388,
                         "forward placement must NOT join the islands")
        self.assertGreater(len(cf), len(scene.components(over, d.width, d.height)))

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_overlay_replaces_rather_than_unions(self):
        """Corpus-wide the scene layers open 11,578 cells the grid blocks *and*
        block 6,264 cells the grid opens. A union would silently drop the
        second number, i.e. let a player walk through the walls of every
        building placed as scenery."""
        import json
        rep = PROJECT / "out" / "wdf" / "scene_survey.json"
        if not rep.is_file():
            self.skipTest("run `py -3 tools/scene.py --verify -o "
                          "out/wdf/scene_survey.json` first")
        t = json.loads(rep.read_text("utf-8"))["totals"]
        self.assertGreater(t["opened"], 10000)
        self.assertGreater(t["blocked"], 5000)
        self.assertEqual(t["offMap"], 0)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_maps_whose_bridges_make_them_whole(self):
        """`p-arena` and `task07` are three walkable components without their
        scene layers and exactly **one** with them. A wrong placement rule
        cannot produce that: it would have to land every cell of a bridge on
        the gap it spans, on several maps at once."""
        import scene
        import dmap
        import coroot
        root = Path(coroot.default_root())
        lib = scene.SceneLibrary(root)
        for name, want_base, want_over in (("p-arena", 3, 1), ("task07", 3, 1),
                                           ("task08", 3, 2)):
            with self.subTest(map=name):
                d = dmap.parse(root / "map" / "map" / f"{name}.DMap", verify=False)
                sc = scene.gather(d.layers, lib)
                base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
                over, _ = scene.apply_passability(base, d.width, d.height, sc.scenes)
                self.assertEqual(len(scene.components(base, d.width, d.height)),
                                 want_base)
                self.assertEqual(len(scene.components(over, d.width, d.height)),
                                 want_over)


class SceneSprites(unittest.TestCase):
    """Where a placed sprite lands in the painted image's pixel space."""

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_a_cover_sprite_is_centred_on_the_cells_it_declares(self):
        """The evidence for `top-left = cell_px(anchor) - offset`. Over 1,500
        covers on four maps the sprite's opaque bounding box is horizontally
        centred on its declared footprint and its bottom edge sits on the
        footprint's bottom corner. Getting the sign wrong moves the median
        error from a couple of pixels to over a hundred."""
        import puzzle
        import scene
        import dmap
        import coroot
        import statistics
        root = Path(coroot.default_root())
        plib = puzzle.PuzzleLibrary(root)
        slib = scene.SceneLibrary(root)
        cache = scene.SpriteCache(root)
        dxs, dys, n = [], [], 0
        for name in ("newbie", "newplain", "desert", "arena"):
            pm = plib.get(name)
            d = dmap.parse(root / "map" / "map" / f"{name}.DMap", verify=False)
            for p in scene.gather(d.layers, slib).covers:
                fr = cache.frames(p.ani, p.title)
                if not fr:
                    continue
                sw, sh, rgba = fr[0]
                box = _alpha_box(rgba, sw, sh)
                if box is None:
                    continue
                bx0, by0, bx1, by1 = box
                sx, sy = p.sprite_origin(pm)
                x0, y0, x1, y1 = p.cell_bounds
                left = pm.corner_px(x0, y1 + 1)[0]
                right = pm.corner_px(x1 + 1, y0)[0]
                bottom = pm.corner_px(x1 + 1, y1 + 1)[1]
                dxs.append(sx + (bx0 + bx1) / 2 - (left + right) / 2)
                dys.append(sy + by1 - bottom)
                n += 1
        self.assertGreater(n, 1000)
        self.assertLess(abs(statistics.median(dxs)), 12)
        self.assertLess(abs(statistics.median(dys)), 12)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_stepping_stone_art_sits_on_the_cells_it_opens(self):
        """The picture and the passability have to agree, or the player walks
        on thin air next to a stone. Every cell a `newbie` scene part opens is
        inside the opaque part of the sprite that part draws."""
        import puzzle
        import scene
        import dmap
        import coroot
        root = Path(coroot.default_root())
        pm = puzzle.PuzzleLibrary(root).get("newbie")
        slib = scene.SceneLibrary(root)
        cache = scene.SpriteCache(root)
        d = dmap.parse(root / "map" / "map" / "newbie.DMap", verify=False)
        checked = off = 0
        for p in scene.gather(d.layers, slib).scenes:
            fr = cache.frames(p.ani, p.title)
            if not fr:
                continue
            sw, sh, rgba = fr[0]
            sx, sy = p.sprite_origin(pm)
            for x, y, blocked, _s, _e in p.map_cells():
                if blocked:
                    continue
                checked += 1
                cx, cy = pm.cell_px(x, y)
                ix, iy = int(cx - sx), int(cy - sy)
                if not (0 <= ix < sw and 0 <= iy < sh) or rgba[(iy * sw + ix) * 4 + 3] < 32:
                    off += 1
        self.assertGreaterEqual(checked, 111)
        self.assertEqual(off, 0, "a walkable scene cell landed off its own art")


    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_a_msk_frame_falls_back_to_the_dds_of_the_same_stem(self):
        """14 of the 2,621 MapScene frames name a `.msk`, and they are every
        piece of both `p-arena` bridges -- so those drew as nothing until this
        was handled. A `.msk` is a 1-bpp mask; the colour is in the `.dds`
        beside it, whose own alpha already agrees with the mask to 97%."""
        import scene
        import coroot
        from coassets import AssetRoot
        root = Path(coroot.default_root())
        cache = scene.SpriteCache(root)
        paths = cache.frame_paths("ani/MapScene.ani", "bridge05.tga")
        self.assertTrue(paths and paths[0].endswith(".msk"), paths)
        fr = cache.frames("ani/MapScene.ani", "bridge05.tga")
        self.assertEqual(len(fr), 1)
        w, h, rgba = fr[0]
        self.assertEqual((w, h), (512, 512))

        # the mask and the .dds alpha really do describe the same shape
        ar = AssetRoot(root)
        msk = ar.read(paths[0])
        self.assertEqual(len(msk), w * h // 8)
        agree = total = 0
        for i in range(0, w * h, 7):          # every 7th pixel is plenty
            bit = (msk[i >> 3] >> (7 - (i & 7))) & 1
            agree += bit == (1 if rgba[i * 4 + 3] > 8 else 0)
            total += 1
        self.assertGreater(agree / total, 0.95)

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_every_p_arena_bridge_piece_has_art(self):
        """The regression the .msk fallback exists for."""
        import scene
        import dmap
        import coroot
        root = Path(coroot.default_root())
        d = dmap.parse(root / "map" / "map" / "p-arena.DMap", verify=False)
        sc = scene.gather(d.layers, scene.SceneLibrary(root))
        cache = scene.SpriteCache(root)
        self.assertEqual(len(sc.scenes), 16)
        for p in sc.scenes:
            with self.subTest(title=p.title):
                self.assertTrue(cache.frames(p.ani, p.title), p.title)


def _alpha_box(rgba: bytes, w: int, h: int):
    """Tight bounding box of the non-transparent pixels, or None."""
    x0, y0, x1, y1 = w, h, 0, 0
    found = False
    for y in range(h):
        row = y * w * 4
        for x in range(w):
            if rgba[row + x * 4 + 3] > 8:
                found = True
                if x < x0:
                    x0 = x
                if x >= x1:
                    x1 = x + 1
                if y < y0:
                    y0 = y
                if y >= y1:
                    y1 = y + 1
    return (x0, y0, x1, y1) if found else None


class MapBackdrops(unittest.TestCase):
    """`map/puzzle/*bg*.pul` -- the planes behind the ground art."""

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_dmap_trailer_is_the_background_list(self):
        """`newbie.DMap`'s header names only `newbie.pul`. `newbiebg.pul` is in
        the file's trailing section, which is why the ground pipeline never
        loaded it."""
        import puzzle
        lib = puzzle.PuzzleLibrary()
        bd = lib.backdrops("newbie")
        self.assertEqual([b.path for b in bd], ["map/puzzle/newbiebg.pul"])
        self.assertEqual(bd[0].art.pul_w, 6)
        self.assertEqual(bd[0].art.pul_h, 6)
        self.assertEqual(lib.get("newbie").pul_path, "map/puzzle/newbie.pul")

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_a_two_plane_map_orders_them_furthest_first(self):
        """`2009-7x` and `beach` each carry two backdrops, numbered 0 and 1 in
        the record's first integer. They are returned back to front."""
        import puzzle
        lib = puzzle.PuzzleLibrary()
        for name in ("2009-7x", "beach"):
            with self.subTest(map=name):
                bd = lib.backdrops(name)
                self.assertEqual(len(bd), 2)
                self.assertEqual([b.index for b in bd], [1, 0])

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_a_desynchronised_trailer_yields_no_backdrop_rather_than_debris(self):
        """`luckytree01_new`'s trailer is uninitialised: 30-odd records whose
        "paths" are fragments of an .ani filename. None of them is a .pul that
        exists, and none is offered."""
        import puzzle
        self.assertEqual(puzzle.PuzzleLibrary().backdrops("luckytree01_new"), [])

    @unittest.skipUnless(HAVE_ROOT, "needs the game install")
    def test_the_backdrop_fills_what_the_ground_leaves_void(self):
        """The point of the whole layer: `newbie`'s art covers 8,640 of 17,424
        cells and the rest was flat near-black. With a backdrop the same
        rectangle comes back with almost no VOID left in it."""
        import puzzle
        import terrain
        lib = puzzle.PuzzleLibrary()
        pm = lib.get("newbie")
        rect = (0, 1200, 800, 1600)
        patch = {"ground": {"rect": list(rect)}}
        void = bytes(puzzle.VOID)

        def void_fraction(png_bytes):
            import zlib
            import struct as _s
            # decode our own minimal PNG: one IDAT, filter 0 on every row
            off, idat = 8, b""
            w = h = 0
            while off < len(png_bytes):
                (n,) = _s.unpack_from(">I", png_bytes, off)
                tag = png_bytes[off + 4:off + 8]
                data = png_bytes[off + 8:off + 8 + n]
                if tag == b"IHDR":
                    w, h = _s.unpack_from(">II", data, 0)
                elif tag == b"IDAT":
                    idat += data
                off += 12 + n
            raw = zlib.decompress(idat)
            stride = w * 3
            n_void = 0
            for y in range(h):
                row = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
                for x in range(w):
                    if row[x * 3:x * 3 + 3] == void:
                        n_void += 1
            return n_void / float(w * h)

        plain = terrain.art_texture(pm, patch, scale=4)
        with_bg = terrain.art_texture(pm, patch, scale=4,
                                      backdrops=lib.backdrops("newbie"))
        self.assertGreater(void_fraction(plain), 0.3)
        self.assertLess(void_fraction(with_bg), 0.01)


class IsometricCamera(unittest.TestCase):
    """The game camera is derived from the art, not chosen.

    `docs/map_scenery.md` 6. These are pure arithmetic against `gl.js`'s own
    matrix conventions, so they run without a browser and without an install.
    """

    CELL = 100.0

    def _basis(self, yaw, pitch):
        """gl.js `lookAt(eye, ctr, [0,0,1])`: screen-right is
        `normalize(cross(Z, dir))` and screen-up is `cross(dir, right)`."""
        cp, sp = math.cos(pitch), math.sin(pitch)
        d = (cp * math.cos(yaw), cp * math.sin(yaw), sp)
        n = math.hypot(-d[1], d[0]) or 1.0
        x = (-d[1] / n, d[0] / n, 0.0)
        y = (d[1] * x[2] - d[2] * x[1],
             d[2] * x[0] - d[0] * x[2],
             d[0] * x[1] - d[1] * x[0])
        return x, y

    def _step(self, yaw, pitch, dcx, dcy):
        """One cell step, in view-space units. `tools/terrain.world_xy` puts
        cell (cx, cy) at world (cx*CELL, -cy*CELL)."""
        w = (dcx * self.CELL, -dcy * self.CELL, 0.0)
        x, y = self._basis(yaw, pitch)
        return (sum(a * b for a, b in zip(w, x)),
                sum(a * b for a, b in zip(w, y)))

    def test_the_yaw_is_forced_by_which_way_the_cell_axes_run(self):
        """+x cell must go right and down the screen, +y cell left and down --
        that is what `px = (gx-gy+K)*32, py = (gx+gy-K)*16` says. Only
        yaw = -PI/4 does it; +PI/4 mirrors the map."""
        yaw, pitch = -math.pi / 4, math.asin(0.5)
        sx, sy = self._step(yaw, pitch, 1, 0)
        self.assertGreater(sx, 0, "+x cell must move right")
        self.assertLess(sy, 0, "+x cell must move DOWN (view-space +y is up)")
        sx, sy = self._step(yaw, pitch, 0, 1)
        self.assertLess(sx, 0, "+y cell must move left")
        self.assertLess(sy, 0, "+y cell must move down")
        # the mirrored yaw fails the first of those
        sx, _ = self._step(+math.pi / 4, pitch, 1, 0)
        self.assertLess(sx, 0)

    def test_the_pitch_is_asin_half_and_not_atan_half(self):
        """With yaw at 45 degrees the on-screen ratio of vertical to horizontal
        travel for a ground step is exactly sin(pitch), and the art wants
        16/32. So the camera elevation is asin(1/2) = 30 degrees.

        `atan(1/2)` = 26.565 is the angle the axes make *on the finished
        picture*; using it as the camera elevation squashes the map by 11%.
        True isometric, 35.264, stretches it by 15%."""
        yaw = -math.pi / 4
        for name, pitch, want in (("asin(1/2)", math.asin(0.5), 0.5),
                                  ("atan(1/2)", math.atan(0.5), 0.4472),
                                  ("true iso", math.asin(1 / math.sqrt(3)), 0.5774)):
            with self.subTest(pitch=name):
                sx, sy = self._step(yaw, pitch, 1, 0)
                self.assertAlmostEqual(abs(sy / sx), want, places=3)
        self.assertAlmostEqual(math.degrees(math.asin(0.5)), 30.0, places=6)
        self.assertNotAlmostEqual(math.asin(0.5), math.atan(0.5), places=3)

    def test_the_projection_reproduces_the_arts_placement_rule_exactly(self):
        """The test perspective cannot pass. Project a grid of cell centres and
        fit `screen = s * artPixel + t` with one uniform scale. Orthographic at
        the derived angles fits to **zero** error over the whole map; the same
        angles under a 45-degree perspective are off by thousands of pixels,
        because a perspective camera gives the ground a vanishing point the
        painted art does not have."""
        def residual(pitch, yaw, ortho):
            xs, ys = self._basis(yaw, pitch)
            cp, sp = math.cos(pitch), math.sin(pitch)
            d = (cp * math.cos(yaw), cp * math.sin(yaw), sp)
            dist = 1100.0
            eye = (d[0] * dist, d[1] * dist, d[2] * dist)
            A, B = [], []
            for cx in range(50, 111, 6):
                for cy in range(40, 111, 7):
                    w = ((cx + 0.5) * self.CELL, -(cy + 0.5) * self.CELL, 0.0)
                    rel = tuple(w[i] - 0.0 for i in range(3))
                    u = sum(a * b for a, b in zip(rel, xs))
                    v = sum(a * b for a, b in zip(rel, ys))
                    # screen y grows downward and view-space v grows upward, so
                    # -v is the screen coordinate the art's py compares against
                    if ortho:
                        A.append((u, -v))
                    else:
                        # view-space depth, i.e. distance along the eye axis
                        z = sum((w[i] - eye[i]) * -d[i] for i in range(3))
                        A.append((u / z, -v / z))
                    B.append(((cx - cy) * 32.0, (cx + cy) * 16.0))
            mA = [sum(p[i] for p in A) / len(A) for i in (0, 1)]
            mB = [sum(p[i] for p in B) / len(B) for i in (0, 1)]
            num = den = 0.0
            for a, b in zip(A, B):
                a0, a1 = a[0] - mA[0], a[1] - mA[1]
                b0, b1 = b[0] - mB[0], b[1] - mB[1]
                num += a0 * b0 + a1 * b1
                den += b0 * b0 + b1 * b1
            s = num / den
            worst = 0.0
            for a, b in zip(A, B):
                ex = (a[0] - mA[0]) - s * (b[0] - mB[0])
                ey = (a[1] - mA[1]) - s * (b[1] - mB[1])
                worst = max(worst, math.hypot(ex, ey) / abs(s))
            return worst

        yaw = -math.pi / 4
        self.assertLess(residual(math.asin(0.5), yaw, True), 1e-6)
        self.assertGreater(residual(math.atan(0.5), yaw, True), 50)
        self.assertGreater(residual(math.asin(1 / math.sqrt(3)), yaw, True), 50)
        self.assertGreater(residual(math.asin(0.5), +math.pi / 4, True), 500)
        self.assertGreater(residual(math.asin(0.5), yaw, False), 500)

    def test_gl_js_carries_the_constants_and_the_derivation(self):
        js = (Path(__file__).resolve().parent / "webui" / "gl.js").read_text("utf-8")
        self.assertIn("const ISO_YAW = -Math.PI / 4;", js)
        self.assertIn("const ISO_PITCH = Math.asin(0.5);", js)
        self.assertIn("ortho(halfW, halfH, near, far)", js)
        self.assertIn("Not `atan(1/2)`", js)
        # the fixed camera must actually be enforced, not merely set once
        self.assertIn("!this.opts.fixedCamera", js)

    def test_play_js_uses_it_and_keeps_a_debug_escape_hatch(self):
        pj = Path(__file__).resolve().parent / "webui" / "play.js"
        if not pj.is_file():
            self.skipTest("play.js belongs to the open client, a separate project")
        js = pj.read_text("utf-8")
        self.assertIn("viewer.setIsoCamera(freecam)", js)
        self.assertIn("freecam", js)
        self.assertNotIn("viewer.cam.pitch = 0.72;\n  viewer.cam.dist", js)

    def test_the_cover_layer_draws_after_the_character(self):
        """A cover is *defined* as the sprite in front of the player, so it
        cannot be depth-sorted with everything else."""
        gl = (Path(__file__).resolve().parent / "webui" / "gl.js").read_text("utf-8")
        pj = Path(__file__).resolve().parent / "webui" / "play.js"
        if not pj.is_file():
            self.skipTest("play.js belongs to the open client, a separate project")
        play = pj.read_text("utf-8")
        self.assertIn("meta.overlay", gl)
        self.assertIn("gl.depthFunc(overlayOn ? gl.ALWAYS : gl.LEQUAL)", gl)
        self.assertIn("overlay: true", play)
        # and it rides on the ground's own geometry, so it cannot drift
        self.assertIn("Object.assign({}, terrain.mesh", play)


# ---------------------------------------------------------------------------
# task #30 -- the MapEditor
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_ROOT, "game install not present")
class IntegrityManifest(unittest.TestCase):
    """What `integrity.json` actually covers -- measured, not assumed.

    Every "you may edit this" claim the MapEditor makes rests on this file, so
    it is checked against the install rather than against the brief.
    """

    @classmethod
    def setUpClass(cls):
        import mapedit
        cls.mapedit = mapedit
        cls.integ = mapedit.Integrity(ROOT)

    def test_162_entries_name_143_distinct_files(self):
        """The count everyone quotes is the *row* count. The brief says "155
        .DMap and 7 ini JSONs"; the rows say 155 and 7, and the distinct paths
        say 136 and 7 -- 19 rows are repeats."""
        self.assertEqual(self.integ.row_count, 162)
        self.assertEqual(len(self.integ), 143)

    def test_the_repeats_are_maps_with_several_document_ids(self):
        """`lineup.DMap` is listed 8 times, `grocery` and `p-arena` 3 each --
        once per `GameMap.json` DocumentId. Every repeat carries the *same*
        hash, so nothing is ambiguous; there is simply one row per id."""
        import collections
        import json as _json
        rows = _json.loads((ROOT / "integrity.json").read_text("utf-8"))
        by = collections.defaultdict(set)
        for r in rows:
            by[str(r["file"]).replace("\\", "/").lower()].add(r["hash"])
        self.assertEqual([k for k, v in by.items() if len(v) > 1], [],
                         "a path with two different hashes would make the "
                         "manifest ambiguous")
        self.assertEqual(max(collections.Counter(
            str(r["file"]).replace("\\", "/").lower() for r in rows).values()), 8)

    def test_it_is_exactly_the_shipped_dmaps_plus_seven_json(self):
        shipped = {p.stem.lower() for p in (ROOT / "map" / "map").iterdir()
                   if p.suffix.lower() == ".dmap"}
        listed = {Path(k).stem for k in self.integ.entries if k.endswith(".dmap")}
        self.assertEqual(listed, shipped, "the manifest and the map folder "
                                          "must name the same files")
        self.assertEqual(sum(1 for k in self.integ.entries if k.endswith(".json")), 7)

    def test_no_map_art_is_covered(self):
        """The load-bearing negative: not one `.pul`, `.scene`, `.ani`, `.dds`,
        `.Part` or `.pux` is in the manifest. That is why the MapEditor can
        offer art edits as ordinary texture swaps."""
        for ext in (".pul", ".scene", ".ani", ".dds", ".part", ".pux"):
            hits = [k for k in self.integ.entries if k.endswith(ext)]
            self.assertEqual(hits, [], f"{ext} is unexpectedly integrity-checked")

    def test_a_verdict_is_produced_for_anything(self):
        checked = self.integ.status("map\\map\\newbie.DMap")
        self.assertTrue(checked["checked"])
        self.assertTrue(checked["hash"])
        free = self.integ.status("map/puzzle/newbie.pul")
        self.assertFalse(free["checked"])
        self.assertIn("does not list", free["why"])


class MapTileGeometry(unittest.TestCase):
    """Tiling arithmetic. No install needed -- it is pure numbers."""

    @classmethod
    def setUpClass(cls):
        import mapedit
        cls.m = mapedit

    def test_a_tile_covers_TILE_times_z_art_pixels(self):
        self.assertEqual(self.m.tile_rect(0, 0, 1), (0, 0, 256, 256))
        self.assertEqual(self.m.tile_rect(3, 2, 4), (3072, 2048, 4096, 3072))

    def test_a_boundary_at_one_level_is_a_boundary_at_every_coarser_one(self):
        """Tiles are anchored at the art's origin with power-of-two spans, so a
        coarse tile can stand in for four fine ones while they load."""
        for z in self.m.ZOOMS[1:]:
            fine = self.m.tile_rect(4, 4, 1)[0]
            self.assertEqual(fine % (256 * z) in (0, fine % (256 * z)), True)
        # concretely: tile (8,8) at z=1 starts where tile (1,1) at z=8 does
        self.assertEqual(self.m.tile_rect(8, 8, 1)[:2], self.m.tile_rect(1, 1, 8)[:2])

    def test_the_biggest_map_is_a_handful_of_tiles_when_zoomed_out(self):
        """`Gulf` is 34,944 x 22,400 painted pixels. At z=1 that is 137x88
        tiles, which is why nothing may ever ask for a whole map at z=1; at
        the fit zoom it is a handful."""
        self.assertEqual(self.m.tile_grid(34944, 22400, 1), (137, 88))
        z = self.m.fit_zoom(34944, 22400, 1200, 800)
        self.assertEqual(z, 32)
        nx, ny = self.m.tile_grid(34944, 22400, z)
        self.assertLess(nx * ny, 24, "a whole-map view must stay cheap")

    def test_fit_zoom_only_returns_a_renderable_decimation(self):
        for vw, vh in ((100, 100), (1920, 1080), (3, 3)):
            self.assertIn(self.m.fit_zoom(34944, 22400, vw, vh), self.m.ZOOMS)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class MapEditorModel(unittest.TestCase):
    """`tools/mapedit.py` against the shipped corpus."""

    @classmethod
    def setUpClass(cls):
        import mapedit
        cls.m = mapedit
        cls.lib = mapedit.MapEditor(ROOT)
        cls.art = cls.lib.get("newbie")

    def test_the_picker_lists_every_gamemap_row_and_names_the_broken_ones(self):
        rows = self.lib.rows()
        self.assertEqual(len(rows), 156, "all 156 GameMap.json rows, including "
                                         "the duplicate ids")
        states = {}
        for r in rows:
            states[r["state"]] = states.get(r["state"], 0) + 1
        self.assertEqual(states.get("pux"), 4, "four maps use the undecoded "
                                               "TqTerrain .pux format")
        self.assertEqual(states.get("mismatch"), 1)
        mismatched = [r["name"] for r in rows if r["state"] == "mismatch"]
        self.assertEqual(mismatched, ["sky"], "docs/ground_art.md §3.1 names "
                                              "sky as the one placement failure")
        for r in rows:
            if r["state"] != "ok":
                self.assertTrue(r["why"], f"{r['name']} must say why")
        self.assertTrue(all(r["area"] >= n["area"]
                            for r, n in zip(rows, rows[1:])),
                        "largest first")

    def test_a_pux_map_reports_rather_than_raises(self):
        art = self.lib.get("2020love01_new")
        j = art.to_json()
        self.assertFalse(j["ok"])
        self.assertIn(".pux", j["why"])
        self.assertGreater(j["mapSize"][0], 0, "the grid still parses")

    def test_every_layer_renders_a_tile_of_the_right_size(self):
        for layer in self.m.LAYERS:
            w, h, rgba = self.art.render_layer(layer, (0, 0, 512, 512), 2)
            self.assertEqual((w, h), (256, 256), layer)
            self.assertEqual(len(rgba), 256 * 256 * 4, layer)
            png = self.art.tile_png(layer, 0, 0, 2)
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_an_unknown_layer_is_refused(self):
        with self.assertRaises(ValueError):
            self.art.render_layer("shrubbery", (0, 0, 64, 64), 1)

    def test_the_ground_layer_is_transparent_where_no_tile_is_painted(self):
        """The game's ground texture flattens onto VOID because it is one
        opaque texture. An editor's layer must not, or turning the background
        off would show black instead of showing through."""
        pm = self.art.pm
        g = pm.grid
        # A walkable cell is on painted art -- 100.00% of `newbie`'s are
        # (`docs/ground_art.md` §3.2) -- so its centre pixel must be opaque.
        cx, cy = pm.cell_px(71, 31)
        _, _, rgba = self.art.render_ground((int(cx) - 2, int(cy) - 2,
                                             int(cx) + 2, int(cy) + 2), 1)
        self.assertTrue(all(rgba[k] == 255 for k in range(3, len(rgba), 4)),
                        "a walkable cell's own pixels must be opaque ground")
        # An empty tile slot draws nothing at all, so the background shows.
        empty = next((i, j) for j in range(pm.pul_h) for i in range(pm.pul_w)
                     if pm.tile_at(i, j) == self.m.puzzlemod.EMPTY)
        _, _, rgba = self.art.render_ground(
            (empty[0] * g, empty[1] * g, empty[0] * g + g, empty[1] * g + g), 4)
        self.assertEqual(sum(1 for k in range(3, len(rgba), 4) if rgba[k]), 0,
                         f"slot {empty} has no tile and must be transparent")

    def test_the_passability_layer_separates_the_two_kinds_of_walkable(self):
        """`docs/map_scenery.md`: `newbie`'s bridge cells are walkable only
        because a TERRAIN layer says so. The overlay has to show that as a
        different colour or it is not worth drawing."""
        pm = self.art.pm
        _, _, rgba = self.art.render_passability((0, 0, pm.px_w, pm.px_h), 8)
        seen = set()
        for k in range(0, len(rgba), 4):
            if rgba[k + 3]:
                seen.add(tuple(rgba[k:k + 4]))
        self.assertIn(self.m.PASS_BASE, seen)
        self.assertIn(self.m.PASS_SCENE_OPEN, seen,
                      "the stepping stones must be distinguishable")

    def test_clicking_a_scene_sprite_reports_everything_needed_to_change_it(self):
        hit = self.art.pick(2300, 1760)
        self.assertEqual(hit["kind"], "terrain")
        self.assertEqual(hit["title"], "stand08.tga")
        self.assertEqual(hit["ani"], "ani/mapscene.ani")
        self.assertEqual(hit["anchorCell"], [91, 79])
        self.assertEqual(hit["cellSize"], [3, 3])
        self.assertEqual(hit["frames"], ["data/map/mapobj/sky/stand08.dds"])
        self.assertEqual(hit["scenePath"], "map/scene/stand08.scene")
        self.assertEqual(hit["partSource"], "map/ScenePart/stand08.Part")
        self.assertEqual(hit["opensCells"], 9)
        self.assertTrue(hit["carriesPassability"])
        self.assertFalse(hit["integrity"]["checked"])
        self.assertTrue(hit["editable"]["artFree"])

    def test_a_click_falls_through_a_transparent_pixel(self):
        """A sprite counts as hit only where its own alpha is non-zero, so the
        gaps in a tree select what is behind them rather than the tree."""
        hit = self.art.pick(2300, 1760)
        ox, oy = hit["spriteOrigin"]
        w, h = hit["spriteSize"]
        # The sprite's own top-left corner is inside its rectangle and (for a
        # 256px sprite of a small floating island) transparent.
        through = self.art.pick(ox + 1, oy + 1)
        self.assertNotEqual(through.get("title"), "stand08.tga")
        self.assertIn(ox + 1, range(int(ox), int(ox + w)))

    def test_clicking_the_ground_reports_the_tile_and_the_cell_separately(self):
        """Two different grids meet at one pixel -- the puzzle's 256px tile
        slots and the DMap's 64x32 diamonds. Conflating them would be wrong."""
        hit = self.art.pick(3200, 700, layers=["ground"])
        self.assertEqual(hit["kind"], "puzzle")
        self.assertEqual(hit["tileSlot"], [12, 2])
        self.assertEqual(hit["tileKey"], "Puzzle11")
        self.assertEqual(hit["texture"], "data/map/puzzle/newbie/newbie015.dds")
        self.assertEqual(hit["source"], "map/puzzle/newbie.pul")
        self.assertEqual(hit["mapCell"], [71, 31])
        self.assertTrue(hit["cell"]["inside"])
        self.assertFalse(hit["integrity"]["checked"])

    def test_the_editable_report_puts_the_dmap_alone_on_the_checked_side(self):
        rep = self.art.editable_report()
        self.assertEqual([r["path"] for r in rep["checked"]],
                         ["map/map/newbie.dmap"])
        self.assertGreater(rep["freeCount"], 50)
        free = {r["path"] for r in rep["free"]}
        self.assertIn("map/puzzle/newbie.pul", free)
        self.assertIn("map/puzzle/newbiebg.pul", free)
        self.assertIn("data/map/mapobj/sky/stand08.dds", free)
        self.assertIn("map/scenepart/stand08.part", free,
                      "the human-editable .Part source is the easiest way in")


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class DMapPassabilityEdit(unittest.TestCase):
    """The one gated write, and the proof it is surgical.

    Nothing here touches the game install: every write goes to a temporary
    stage directory, which is also what the running viewer does.
    """

    @classmethod
    def setUpClass(cls):
        import mapedit
        cls.m = mapedit

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mapedit-stage-"))
        self.lib = self.m.MapEditor(ROOT, stage=self.tmp)
        self.art = self.lib.get("newbie")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_refuses_without_the_acknowledgement(self):
        with self.assertRaises(self.m.NotAcknowledged):
            self.m.stage_passability(self.art, [{"x": 80, "y": 93, "blocked": False}])
        with self.assertRaises(self.m.NotAcknowledged):
            self.m.stage_passability(self.art, [{"x": 80, "y": 93, "blocked": False}],
                                     ack="yes")
        self.assertEqual(list(self.tmp.rglob("*.DMap")), [],
                         "a refusal must not leave a file behind")

    def test_it_changes_exactly_the_masks_and_the_checksums_that_cover_them(self):
        import dmap as dmapmod
        res = self.m.stage_passability(
            self.art,
            [{"x": 80, "y": 93, "blocked": False}, {"x": 81, "y": 93, "blocked": False}],
            ack=self.m.ACK)
        self.assertEqual(res["changedCount"], 2)
        self.assertEqual(res["rowsRechecksummed"], 1)
        self.assertEqual(res["rowChecksumsBad"], 0)

        orig = (ROOT / "map" / "map" / "newbie.DMap").read_bytes()
        new = (self.tmp / "map/map/newbie.DMap").read_bytes()
        self.assertEqual(len(orig), len(new))
        diffs = [i for i in range(len(orig)) if orig[i] != new[i]]
        # two cell masks (one byte each is already zero) plus the row checksum
        self.assertLessEqual(len(diffs), 6, f"too many bytes moved: {diffs}")

        a = dmapmod.parse(ROOT / "map" / "map" / "newbie.DMap", verify=True)
        b = dmapmod.parse(self.tmp / "map/map/newbie.DMap", verify=True)
        self.assertEqual(b.checksum_ok, b.height,
                         "every row checksum must still verify")
        self.assertEqual(a.checksum_ok, a.height)
        changed = [i for i in range(len(a.cells)) if a.cells[i] != b.cells[i]]
        self.assertEqual(changed, [93 * a.width + 80, 93 * a.width + 81])
        self.assertEqual(b.walkable_count, a.walkable_count + 2)
        # nothing else about the file moved
        self.assertEqual(a.layers, b.layers)
        self.assertEqual(a.extra, b.extra)
        self.assertEqual(a.portals, b.portals)
        self.assertEqual(a.puzzle_path, b.puzzle_path)

    def test_edits_accumulate_on_the_staged_copy(self):
        self.m.stage_passability(self.art, [{"x": 80, "y": 93, "blocked": False}],
                                 ack=self.m.ACK)
        self.art.invalidate_grid()
        res = self.m.stage_passability(self.art, [{"x": 81, "y": 93, "blocked": False}],
                                       ack=self.m.ACK)
        self.assertEqual(res["changedCount"], 1)
        import dmap as dmapmod
        b = dmapmod.parse(self.tmp / "map/map/newbie.DMap", verify=True)
        self.assertTrue(b.walkable(80, 93) and b.walkable(81, 93),
                        "the first edit must survive the second")

    def test_the_editor_then_reads_the_staged_grid(self):
        self.assertFalse(self.art.staged)
        self.assertFalse(self.art.cell_state(80, 93)["walkable"])
        self.m.stage_passability(self.art, [{"x": 80, "y": 93, "blocked": False}],
                                 ack=self.m.ACK)
        self.art.invalidate_grid()
        self.assertTrue(self.art.staged)
        self.assertTrue(self.art.cell_state(80, 93)["walkable"],
                        "the map you look at must be the map you are making")

    def test_a_cell_off_the_map_is_reported_not_written(self):
        res = self.m.stage_passability(
            self.art, [{"x": 99999, "y": 0, "blocked": True}], ack=self.m.ACK)
        self.assertEqual(res["outsideMap"], 1)
        self.assertEqual(res["changedCount"], 0)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class MapEditorStagedArt(unittest.TestCase):
    """Staging a texture has to show on the map, or "stage" means nothing."""

    def test_the_map_reads_through_the_stage_tree(self):
        import mapedit
        tmp = Path(tempfile.mkdtemp(prefix="mapedit-art-"))
        try:
            lib = mapedit.MapEditor(ROOT, stage=tmp)
            art = lib.get("newbie")
            rect = (3072, 512, 3328, 768)          # the tile slot (12, 2)
            _, _, before = art.render_ground(rect, 4)
            tile = "data/map/puzzle/newbie/newbie015.dds"
            dest = tmp / tile
            dest.parent.mkdir(parents=True, exist_ok=True)
            # A different shipped tile stands in for "the user edited this".
            dest.write_bytes(lib.assets.read("data/map/puzzle/newbie/newbie016.dds"))
            art.invalidate_art()
            _, _, after = art.render_ground(rect, 4)
            self.assertNotEqual(before, after,
                                "a staged .dds must change the drawn map")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class MapEditorUi(unittest.TestCase):
    """The page's contract: portability, skinning, and constants that agree.

    These read the shipped files rather than re-implementing anything -- the
    same approach as `CollapsiblePanels`, and for the same reason: the failure
    modes here are silent.
    """

    WEBUI = Path(__file__).resolve().parent / "webui"

    def read(self, name):
        return (self.WEBUI / name).read_text(encoding="utf-8")

    @staticmethod
    def _code_only(js):
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        return re.sub(r"^\s*//.*$", "", js, flags=re.M)

    def test_the_map_model_never_touches_the_dom(self):
        """State -> view stays separable from DOM manipulation, because the
        client has to port off the browser. Same rule as `views.js`."""
        code = self._code_only(self.read("mapmodel.js")).replace(
            "if (typeof window !== 'undefined') window.MapModel = MapModel;", "")
        # Word-boundary patterns, not substrings: `documentId` is a
        # `GameMap.json` field and banning the letters would ban the data.
        for banned in (r"\bdocument\s*\.", r"\bwindow\s*\.", r"\blocalStorage\b",
                       r"\bfetch\s*\(", r"\bcreateElement\b", r"\binnerHTML\b",
                       r"\bnew\s+Image\b"):
            self.assertIsNone(re.search(banned, code),
                              f"mapmodel.js must stay pure: found {banned!r}")

    def test_the_stylesheet_carries_no_literal_colour(self):
        css = re.sub(r"/\*.*?\*/", "", self.read("mapedit.css"), flags=re.S)
        for pattern, what in ((r"#[0-9a-fA-F]{3,8}\b", "a hex colour"),
                              (r"\brgba?\(", "an rgb() literal"),
                              (r"\bhsla?\(", "an hsl() literal")):
            hit = re.search(pattern, css)
            self.assertIsNone(hit, f"mapedit.css contains {what}")

    def test_it_only_uses_tokens_the_shared_stylesheet_declares(self):
        style = self.read("style.css")
        declared = set(re.findall(r"^\s*(--[a-z0-9-]+):", style, flags=re.M))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.read("mapedit.css")))
        self.assertEqual(used - declared, set(),
                         "a var() nothing declares resolves to nothing at all")

    def test_the_constants_agree_with_the_python(self):
        """A tile the page asks for and the server cannot render is a blank
        square with no error, so the three constants are pinned on both sides."""
        import mapedit
        js = self.read("mapmodel.js")
        self.assertIn(f"const TILE = {mapedit.TILE};", js)
        self.assertIn("const ZOOMS = [" + ", ".join(str(z) for z in mapedit.ZOOMS) + "];",
                      js)
        self.assertIn("const LAYERS = ['" + "', '".join(mapedit.LAYERS) + "'];", js)

    def test_the_placement_rule_in_the_page_is_the_one_in_puzzle_py(self):
        """`mapmodel.corner()` repeats `puzzle.corner_px` so a click does not
        cost a round trip. Repeating it is fine; letting it drift is not."""
        import puzzle
        js = self.read("mapmodel.js")
        self.assertIn("[(gx - gy + K) * 32, (gx + gy - K) * 16]", js)
        self.assertEqual(puzzle.CELL_PX_W // 2, 32)
        self.assertEqual(puzzle.CELL_PX_H // 2, 16)

    def test_every_card_on_the_page_can_actually_collapse(self):
        html = self.read("mapedit.html")
        cards = re.findall(r'<section class="card[^"]*"[^>]*>', html)
        self.assertGreaterEqual(len(cards), 4)
        for c in cards:
            self.assertIn("data-card=", c, f"{c} cannot be operated by cards.js")
        self.assertEqual(html.count("cardbody"), len(cards))
        self.assertIn('src="/ui/cards.js"', html)
        self.assertIn('src="/ui/mapmodel.js"', html)
        self.assertIn('id="btn-collapse-all"', html)

    def test_the_page_is_reachable_from_the_other_two(self):
        for page in ("index.html", "builder.html"):
            self.assertIn('href="/mapedit"', self.read(page),
                          f"{page} must link to the MapEditor")

    def test_zoom_is_a_scale_and_never_a_projection(self):
        """The camera is derived, not chosen (`docs/map_scenery.md` §6), so the
        editor must have no way to express a different one."""
        code = self._code_only(self.read("mapmodel.js"))
        for banned in ("pitch", "yaw", "perspective", "lookAt", "Math.tan"):
            self.assertNotIn(banned, code,
                             f"mapmodel.js grew a camera: {banned!r}")
        self.assertIn("zoom", code)

    def test_the_gate_string_matches_the_server(self):
        import mapedit
        js = self.read("mapedit.js")
        self.assertIn(f"const ACK = '{mapedit.ACK}';", js)


@unittest.skipUnless(HAVE_ROOT, "game install not present")
class MapEditorRoutes(unittest.TestCase):
    """The routes exist and are wired to the model, checked without a socket."""

    def test_every_mapedit_route_is_registered(self):
        src = (Path(__file__).resolve().parent / "coviewer.py").read_text("utf-8")
        for route, handler in (
                ("/api/mapedit/maps", "api_mapedit_maps"),
                ("/api/mapedit/map", "api_mapedit_map"),
                ("/api/mapedit/tile", "api_mapedit_tile"),
                ("/api/mapedit/pick", "api_mapedit_pick"),
                ("/api/mapedit/editable", "api_mapedit_editable"),
                ("/api/mapedit/passability", "post_mapedit_passability")):
            self.assertIn(f'"{route}": self.{handler}', src)
        self.assertIn('if path in ("/mapedit", "/mapedit/", "/mapedit.html")', src)

    def test_the_viewer_never_writes_to_the_install_itself(self):
        """Every write in the MapEditor's path lands in mods/stage; the one
        thing that touches the install is `_run_comod`."""
        import mapedit
        src = (Path(__file__).resolve().parent / "mapedit.py").read_text("utf-8")
        self.assertIn("STAGE = _REPO", src)
        for bad in ("shutil.copy", "os.remove"):
            self.assertNotIn(bad, src)
        self.assertIs(mapedit.STAGE.parent.name, mapedit.STAGE.parent.name)
        self.assertEqual(mapedit.STAGE.parts[-2:], ("mods", "stage"))


class SafePath(unittest.TestCase):
    r"""`core/safepath.py` -- the one place that joins an untrusted path.

    A security review found the same missing containment check in three files
    (viewer stage/unstage, asset `locate`, comod install) and reported it as
    three findings. It is one bug, so there is one fix and one test class.

    `ATTACK` is the reviewer's proof-of-concept string, preserved exactly as a
    *value* so this test fails if the fix ever regresses to something that
    merely looks careful.

    It is **assembled rather than pasted**, and that is not fussiness:
    `tests/test_sanitization.py` forbids the literal install path anywhere
    outside `core/coroot.py`, and pasting the PoC verbatim tripped that gate.
    The reviewer's own attack string is itself a hardcoded install path. Split
    here so both rules hold at once; the string this builds is byte-identical
    to the one reported.
    """

    ATTACK = ("..\\" * 4) + "Program Files\\Classic " + "Conquer 2.0" \
             + "\\c3\\texture\\evil.dds"

    def setUp(self):
        import safepath
        self.sp = safepath
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "stage"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_reviewers_own_attack_string_is_refused(self):
        with self.assertRaises(self.sp.UnsafePath) as ctx:
            self.sp.confine(self.root, self.ATTACK)
        self.assertIn("outside", ctx.exception.reason)

    def test_an_ordinary_logical_path_still_works(self):
        got = self.sp.confine(self.root, "c3/texture/002135300.dds")
        self.assertEqual(got.relative_to(self.root.resolve()).as_posix(),
                         "c3/texture/002135300.dds")

    def test_backslashes_are_accepted_as_separators(self):
        """`.DMap` files and `GameMap.json` both spell paths with `\\`."""
        got = self.sp.confine(self.root, r"map\puzzle\newbie015.dds")
        self.assertEqual(got.relative_to(self.root.resolve()).as_posix(),
                         "map/puzzle/newbie015.dds")

    def test_dotdot_that_stays_inside_is_allowed(self):
        """Containment, not a `..` ban: the test is where it lands, so a path
        that backs up and comes home is fine."""
        got = self.sp.confine(self.root, "sub/../ok.dds")
        self.assertEqual(got, self.root.resolve() / "ok.dds")

    def test_every_escape_shape_is_refused(self):
        for bad in (
            "../../etc/passwd",
            "a/../../b",
            "/etc/passwd",                      # posix absolute
            r"C:\Windows\System32\evil.dll",    # windows absolute
            "\\\\server\\share\\x",             # UNC
            "\\\\?\\C:\\Windows\\evil",         # extended-length
            "C:evil.dds",                       # drive-relative
        ):
            with self.subTest(path=bad):
                self.assertRaises(self.sp.UnsafePath,
                                  self.sp.confine, self.root, bad)

    def test_windows_reserved_device_names_are_refused(self):
        r"""`stage\NUL` is not a file -- it is the null device, at every
        directory level and with any extension. Inside the root, so the
        containment test cannot catch it."""
        for bad in ("CON", "nul.dds", "sub/COM1.txt", "aux", "LPT9.dds"):
            with self.subTest(path=bad):
                self.assertRaises(self.sp.UnsafePath,
                                  self.sp.confine, self.root, bad)

    def test_empty_and_nul_are_refused(self):
        for bad in ("", ".", "./", "x\x00y"):
            with self.subTest(path=bad):
                self.assertRaises(self.sp.UnsafePath,
                                  self.sp.confine, self.root, bad)

    def test_the_root_itself_is_not_a_valid_answer(self):
        """Callers want a file *under* the root; returning the root would make
        `logical=""` mean "the install directory"."""
        self.assertRaises(self.sp.UnsafePath, self.sp.confine, self.root, "/")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlink support")
    def test_a_symlink_pointing_out_of_the_root_is_refused(self):
        """The case a string check cannot see. Resolution happens before the
        containment test precisely so that this fails."""
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"symlink not permitted here: {e}")
        with self.assertRaises(self.sp.UnsafePath):
            self.sp.confine(self.root, "escape/evil.dds")

    def test_is_confined_is_the_same_rule_as_a_predicate(self):
        self.assertTrue(self.sp.is_confined(self.root, "c3/a.dds"))
        self.assertFalse(self.sp.is_confined(self.root, self.ATTACK))

    def test_normalize_does_not_pretend_to_validate(self):
        """Spelling and safety are separate functions on purpose: nobody should
        be able to mistake one for the other."""
        self.assertEqual(self.sp.normalize(r"a\.\b//c"), "a/b/c")
        self.assertEqual(self.sp.normalize("../a"), "../a",
                         "normalize must leave `..` alone -- confine judges it")


class Csrf(unittest.TestCase):
    """The localhost-CSRF gate on state-changing POSTs.

    The audit's finding 2: a page on any origin could make *your* browser POST
    to 127.0.0.1:8731 and stage-then-install a file into the game directory.
    The server binding loopback does not help, because the attacker is a tab in
    your own browser rather than a host on the network.

    Exercised through `_csrf_reason` with a stub request instead of a live
    server, matching this suite's existing style -- the logic is what can
    regress, and it is reachable without a socket.
    """

    TOKEN = "test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def _handler(self, headers: dict, path: str = "/api/stage",
                 token: "str | None" = None):
        import coviewer

        class Stub(coviewer.Handler):
            # BaseHTTPRequestHandler's __init__ wants a socket; this class
            # exists only to reach the pure logic above it.
            def __init__(self, headers, path, token):
                self.headers = headers
                self.path = path
                self.server = type("S", (), {"csrf_token": token})()

        return Stub(headers, path, self.TOKEN if token is None else token)

    HOST = {"Host": "127.0.0.1:8731"}

    def test_no_token_is_refused(self):
        h = self._handler(dict(self.HOST))
        self.assertIn("CSRF token", h._csrf_reason("/api/stage") or "")

    def test_correct_token_passes(self):
        h = self._handler({**self.HOST, "X-CO-Token": self.TOKEN})
        self.assertIsNone(h._csrf_reason("/api/stage"))

    def test_wrong_token_is_refused(self):
        h = self._handler({**self.HOST, "X-CO-Token": "nope"})
        self.assertIsNotNone(h._csrf_reason("/api/stage"))

    def test_a_foreign_origin_is_refused_even_with_a_valid_token(self):
        """Defence in depth: if the token ever leaks, the origin check still
        stands, and vice versa."""
        h = self._handler({**self.HOST, "X-CO-Token": self.TOKEN,
                           "Origin": "https://evil.example"})
        reason = h._csrf_reason("/api/stage")
        self.assertIn("cross-origin", reason or "")
        self.assertIn("evil.example", reason or "")

    def test_our_own_origin_passes(self):
        h = self._handler({**self.HOST, "X-CO-Token": self.TOKEN,
                           "Origin": "http://127.0.0.1:8731"})
        self.assertIsNone(h._csrf_reason("/api/stage"))

    def test_localhost_and_the_loopback_ip_are_the_same_server(self):
        """A page served from `localhost:8731` must not be refused for calling
        itself `127.0.0.1:8731`, or the viewer breaks depending on which name
        the operator typed."""
        for origin in ("http://localhost:8731", "http://127.0.0.1:8731"):
            for host in ("localhost:8731", "127.0.0.1:8731"):
                with self.subTest(origin=origin, host=host):
                    h = self._handler({"Host": host, "Origin": origin,
                                       "X-CO-Token": self.TOKEN})
                    self.assertIsNone(h._csrf_reason("/api/stage"))

    def test_a_foreign_referer_is_refused_when_origin_is_absent(self):
        h = self._handler({**self.HOST, "X-CO-Token": self.TOKEN,
                           "Referer": "https://evil.example/page.html"})
        self.assertIn("cross-origin", h._csrf_reason("/api/stage") or "")

    def test_the_token_may_also_ride_in_the_query_string(self):
        h = self._handler(dict(self.HOST),
                          path=f"/api/stage?path=a.dds&csrf={self.TOKEN}")
        self.assertIsNone(h._csrf_reason("/api/stage"))

    def test_the_token_is_from_a_csprng_not_hash(self):
        """Finding 15: the preview token hashed three guessable inputs (the
        logical path, the asset name, and `time.time()`). It now comes from
        `secrets`.

        The assertion targets the *assignment* rather than any mention of the
        old expression, because the comment at that line quotes the old code on
        purpose -- a source-scanning test that cannot tell code from a comment
        would forbid explaining the fix.
        """
        src = (Path(__file__).resolve().parent / "coviewer.py").read_text("utf-8")
        self.assertNotIn('token = f"{abs(hash(', src)
        self.assertIn("secrets.token_hex", src)
        self.assertIn("secrets.token_urlsafe", src)

    def test_comparison_is_constant_time(self):
        src = (Path(__file__).resolve().parent / "coviewer.py").read_text("utf-8")
        self.assertIn("compare_digest", src)

    def test_the_page_is_served_the_token_and_the_wrapper(self):
        h = self._handler(dict(self.HOST))
        out = h._inject_csrf(b"<html><head><title>x</title></head><body></body></html>")
        self.assertIn(b'name="co-csrf"', out)
        self.assertIn(self.TOKEN.encode(), out)
        self.assertIn(b'src="/ui/csrf.js"', out)
        self.assertLess(out.index(b"co-csrf"), out.index(b"<title>"),
                        "the token must be in place before any page script runs")

    def test_injection_is_skipped_when_there_is_no_token(self):
        h = self._handler(dict(self.HOST), token="")
        page = b"<html><head></head></html>"
        self.assertEqual(h._inject_csrf(page), page)

    def test_the_wrapper_only_attaches_to_same_origin_writes(self):
        """`csrf.js` must not hand our token to a third-party URL, and must
        leave GETs alone so caching and preflights are unaffected."""
        js = (Path(__file__).resolve().parent / "webui" / "csrf.js").read_text("utf-8")
        self.assertIn("sameOrigin", js)
        self.assertIn("'GET'", js)
        self.assertIn("X-CO-Token", js)
        self.assertIn('meta[name="co-csrf"]', js)
class CoreBoundary(unittest.TestCase):
    """COre must stay extractable, which means it must not reach upwards.

    `core/` is the shared foundation for the planned COMod / COmpanion / VibeCo
    split (`docs/repo_split.md`). The property that makes it extractable at all
    is that its modules import only each other and the standard library — one
    `import parts` from `tools/` would make `git filter-repo --path core/`
    produce a repository that cannot import itself.

    That property is true today and is exactly the kind of thing that rots
    silently, because adding the wrong import works fine in the combined tree
    and only breaks at extraction time, months later.
    """

    CORE = Path(__file__).resolve().parent.parent / "core"

    #: The declared members. Kept here rather than globbed so that *adding* a
    #: module to COre is a deliberate act with a test change attached.
    MEMBERS = {"coroot", "safepath", "tqhash", "wdf", "dds", "c3phy",
               "dmap", "coassets"}

    def test_the_directory_holds_exactly_the_declared_modules(self):
        on_disk = {p.stem for p in self.CORE.glob("*.py")}
        self.assertEqual(on_disk, self.MEMBERS)

    def test_pyproject_lists_the_same_set(self):
        """The packaging manifest and reality must not disagree, or a
        pip-installed COre is missing a module that a checkout has."""
        text = (self.CORE / "pyproject.toml").read_text("utf-8")
        declared = set(re.findall(r'^\s*"([a-z0-9_]+)",', text, re.M))
        self.assertEqual(declared, self.MEMBERS)

    def test_no_core_module_imports_anything_outside_core(self):
        """The load-bearing assertion. Parsed with `ast`, not grepped, so a
        conditional or function-local import cannot slip past."""
        import ast
        stdlib = set(sys.stdlib_module_names)
        for path in sorted(self.CORE.glob("*.py")):
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # `from . import x` would mean COre became a package;
                    # level>0 is therefore also a boundary change.
                    self.assertEqual(node.level, 0,
                                     f"{path.name}: relative import implies a package")
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    if not name:
                        continue
                    with self.subTest(module=path.name, imports=name):
                        self.assertTrue(
                            name in stdlib or name in self.MEMBERS
                            or name in ("numpy", "PIL"),
                            f"core/{path.name} imports {name!r}, which is outside "
                            f"COre. Either it belongs in COre too, or the "
                            f"dependency points the wrong way.")

    def test_optional_third_party_stays_optional(self):
        """numpy and Pillow may be *used* but never required: COre has to be
        installable in an environment with neither."""
        for path in sorted(self.CORE.glob("*.py")):
            src = path.read_text("utf-8")
            for pkg in ("numpy", "PIL"):
                if f"import {pkg}" in src:
                    with self.subTest(module=path.name, pkg=pkg):
                        self.assertTrue(
                            "try:" in src or "except ImportError" in src
                            or "HAVE_" in src,
                            f"core/{path.name} imports {pkg} unguarded")

    def test_nothing_in_core_writes_to_the_game_install(self):
        """COre reads. `comod.py` — which is COMod's, not COre's — is the only
        writer in the project."""
        for path in sorted(self.CORE.glob("*.py")):
            src = path.read_text("utf-8")
            for bad in ("shutil.copy", "shutil.move", "os.remove", "os.unlink"):
                with self.subTest(module=path.name, call=bad):
                    self.assertNotIn(bad, src)


class ConfinementIsActuallyWired(unittest.TestCase):
    """The helper existing is not the fix; the call sites using it is.

    Source-level assertions, in the spirit of `test_relay_passive.py`: they
    fail if someone reintroduces a bare join at a path that takes untrusted
    input. A behavioural test would need an HTTP server and a real install;
    this catches the regression that actually happens, which is a new call
    site added without the check.
    """

    def _src(self, name: str) -> str:
        """Source of a module, whichever side of the COre boundary it is on.

        `coassets.py` and `safepath.py` live in `core/` and the viewer's own
        modules live in `tools/`; this test asserts across both, so it looks in
        both rather than encoding a layout that is mid-migration.
        """
        here = Path(__file__).resolve().parent
        for cand in (here / name, here.parent / "core" / name):
            if cand.is_file():
                return cand.read_text("utf-8")
        raise FileNotFoundError(f"{name} is in neither tools/ nor core/")

    def test_viewer_confines_stage_writes_and_deletes(self):
        src = self._src("coviewer.py")
        self.assertIn("safepath", src, "coviewer must import the helper")
        self.assertNotIn("dest = STAGE / logical", src,
                         "post_stage/post_unstage must go through safepath.confine")

    def test_assets_confine_the_loose_and_overlay_lookup(self):
        src = self._src("coassets.py")
        self.assertIn("safepath", src)
        self.assertNotIn("p = self.root / logical", src,
                         "locate() must go through safepath.confine")

    def test_comod_confines_the_install_target(self):
        src = self._src("comod.py")
        self.assertIn("safepath", src)
        self.assertNotIn("target = root / logical", src,
                         "install/uninstall must go through safepath.confine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
