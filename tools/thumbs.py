#!/usr/bin/env python3
r"""thumbs.py -- batch thumbnail renderer for the Classic Conquer 2.0 assets.

Renders a square PNG preview for every `.c3` mesh that `tools/meshtex.py`
resolves to a texture (4,950 of 4,964), plus optional decode-and-downscale
thumbnails for the `.dds` texture library.

Why a software rasterizer and not headless Chrome
-------------------------------------------------
The viewer (`tools/coviewer.py` + `tools/webui/gl.js`) can already screenshot a
model, and that is how `out/viewer/shots/` was made.  It is the wrong tool for
five thousand unattended renders: it needs a browser, a GPU driver and a live
HTTP server, it cannot be resumed mid-run, and the viewer UI is under active
change by another workstream.  So this module rasterizes in numpy instead, and
reuses the project's own verified modules for everything that could silently
diverge from what the viewer draws:

    core/c3phy.py    PHY chunk parsing + the chunk's own 4x4      (imported)
    tools/effects.py  MOTI decoding, Motion_GetMatrix              (imported)
    tools/attach.py   PHY<->MOTI ordinal pairing, [Dumy] socket test (imported)
    core/dds.py      the verified DDS/BC decoder                  (imported)
    core/coassets.py loose-file-shadows-WDF asset VFS             (imported)
    tools/meshtex.py  mesh -> texture, with method and confidence  (imported)

None of those files is modified.

What is drawn, and why
----------------------
For a *standalone asset preview* the transform chain of `docs/attachment.md`
degenerates to its first two stages, because there is no body to hang off:

    p_render = ( SUM_k w_k * ( p_bind x Mbone_k ) ) x I      then z -> -z

* `p_bind` is the file position **after** the chunk's own 4x4 (`Phy_Load`
  applies it at load time, graphic.dll `0x5A735`; `c3phy.apply_matrix_to`).
* `Mbone_k` is the chunk's **own MOTI**, bone `BLENDINDICES0/1`, frame 0, with
  `w1 = 1 - w0` (graphic.dll `0x5A869`).  `Mesh::Draw` always passes
  `useMotion = true` (`0x2607E`) -- the engine never draws a bind pose, so
  neither does this.
* `MOTI` *i* is bound to `PHY` *i* **by ordinal**, not by adjacency
  (`C3Mesh::SetMotion`, `0x27818`).  `attach.PartMesh.parse` does that pairing;
  447 of 2,002 loose `.c3` files group all their PHYs before all their MOTIs and
  an adjacency reader gets zero pairs on them.
* A chunk is skipped iff its name is in `ini/RolePart.ini [Dumy]`
  (`attach.is_socket_name`) -- **not** by a `v_` prefix.  66 armet meshes lead
  with a chunk called `v_armet01` that is real geometry.
* Winding: C3 is D3D clockwise-front in a Z-down left-handed frame; negating Z
  flips handedness, so in the render frame front faces are counter-clockwise and
  we cull the clockwise ones.  Chunks carrying `2SID` are not culled.
  `--selftest` proves the sign is right rather than asserting it (see below).

Deliberate deviations from the live viewer, all so a thumbnail is not blank:

* the `C3Key` alpha / draw tracks are ignored (many effect chunks are keyed to
  alpha 0 at frame 0 and would render empty);
* the camera is re-fitted to *this* mesh's own bounds, since a thumbnail is a
  standalone preview and there is no body to anchor framing to;
* the yaw is mirrored to -0.9 so the camera is in front of the subject rather
  than behind it -- see the comment on the `YAW` constant;
* four named fallbacks fire when a render would come out empty, each recorded
  in the manifest entry (`no_cull`, `uv_cell`, `no_motion`, `alpha_from_luma`).

Everything else -- 45 deg vertical FOV, pitch 0.28, unlit texture sampling,
bilinear + REPEAT wrap, top-row-first V axis (no flip anywhere) -- is copied
from `tools/webui/gl.js`.

Usage
-----
    py -3 tools/thumbs.py --all                  # meshes, resumable
    py -3 tools/thumbs.py --all --textures       # ... and the texture library
    py -3 tools/thumbs.py --all --dry-run        # plan + disk estimate only
    py -3 tools/thumbs.py --one c3/mesh/002135000.c3 --out /tmp/x.png
    py -3 tools/thumbs.py --selftest             # winding / orientation proof

Output: `out/thumbs/` (gitignored) + `out/thumbs/manifest.json`.
Schema and regeneration notes: `docs/thumbnails.md`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import c3phy                                                    # noqa: E402
import dds                                                      # noqa: E402
import effects as fx                                            # noqa: E402
import attach                                                   # noqa: E402
import coroot
import provenance                                                   # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot                    # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = coroot.derived_path("out/thumbs")

#: (library, server) when rendering a COmmunity Library server view instead
#: of the install; set once by main(), read by the worker initializer.
_LIB_SRV: tuple[str, str] = ("", "")


def _apply_output(server: str = "", root=None) -> None:
    """Per-server output tree: out/thumbs/servers/<name>/ with its own
    manifest, so a community client's renders never mix with the base
    install's (same logical path, different bytes).

    **Call this unconditionally, and pass the root.** It used to be called
    only `if server:`, so the *base-install* path -- the common one -- kept the
    values `OUT_DIR`/`MANIFEST`/`MESH_MANIFEST` were given at import, naming
    whichever install was configured then. `--root <other client>` therefore
    rendered into the configured client's namespace. Same shape as C21; the
    server path was correct only because it happened to re-resolve. C55.
    """
    global OUT_DIR, MANIFEST, MESH_MANIFEST
    base = coroot.derived_path("out/thumbs", root)
    OUT_DIR = (base / "servers" / server) if server else base
    MANIFEST = OUT_DIR / "manifest.json"
    MESH_MANIFEST = OUT_DIR / "manifest_meshes.json"

#: Bump when a change alters pixels.  It is part of every cache key, so a bump
#: makes `--resume` re-render everything instead of silently mixing versions.
RENDERER_VERSION = 1

# ---- camera / shading constants, all lifted from tools/webui/gl.js ---------
#
# gl.js's own default is yaw = +0.9, pitch = 0.28 -- but that puts the eye at
# (+X, +Y) and **this corpus is authored facing -Y**, so the viewer's default
# camera looks at the back of a character.  (Measured: rendering
# `c3/mesh/002135000.c3` from the four axis directions, only the -Y eye shows a
# face; `out/thumbs/_selftest/yaw_grid.png` from `--selftest` is that picture.
# The shots in `out/viewer/shots/` show fronts because the viewer persists an
# orbited camera, and one of them is literally named `..._front.png`.)
# A thumbnail sheet of 5,000 assets seen from behind is useless, so the yaw is
# mirrored across the X axis: same 3/4 elevation as the viewer, front side.
YAW = -0.9
PITCH = 0.28
FOV_DEG = 45.0                # gl.js draw(): M4.perspective(45 * PI/180, ...)
BG = (0.055, 0.06, 0.067)     # gl.js draw(): gl.clearColor(...)
MARGIN = 0.06                 # fraction of the frame left empty on each side
ALPHA_EPS = 0.004             # gl.js FS: `else if (c.a < 0.004) discard;`

#: Rasterizer working budget.  Candidate pixels are generated per triangle
#: bounding box; batching keeps peak memory bounded regardless of mesh size.
CANDIDATE_BUDGET = 4_000_000
FRAGMENT_CAP = 24_000_000


# ===========================================================================
# geometry
# ===========================================================================

@dataclass
class ChunkGeom:
    """One drawable PHY chunk, already posed and already in render space."""
    name: str
    pos: np.ndarray            # (n, 3) float32, render space (C3 with z negated)
    uv: np.ndarray             # (n, 2) float32
    nrm: np.ndarray            # (n, 3) float32, render space
    faces: np.ndarray          # (m, 3) int32
    two_sided: bool = False
    is_socket: bool = False


def _bone_matrices(motion, bones: Iterable[int], frame: int) -> dict[int, np.ndarray]:
    """`Motion_GetMatrix(motion, bone, frame)` for each bone, as 4x4 arrays.

    Argument order is settled in docs/attachment.md 2.4 -- arg 2 is the bone,
    arg 3 the frame -- and `effects.Motion.matrix` already has it that way.
    """
    out: dict[int, np.ndarray] = {}
    for b in bones:
        try:
            m = motion.matrix(int(b), frame)
        except Exception:
            m = fx.IDENTITY
        out[int(b)] = np.asarray(m, dtype=np.float64).reshape(4, 4)
    return out


def _skin(pos: np.ndarray, bone0, bone1, w0, has_w1, motion, frame: int) -> np.ndarray:
    """Row-vector skinning with at most two influences and `w1 = 1 - w0`."""
    if motion is None:
        return pos
    bones = set(int(b) for b in np.unique(bone0))
    if has_w1.any():
        bones |= set(int(b) for b in np.unique(bone1[has_w1]))
    mats = _bone_matrices(motion, sorted(bones), frame)
    order = sorted(mats)
    lut = {b: i for i, b in enumerate(order)}
    M = np.stack([mats[b] for b in order])                  # (nb, 4, 4)
    R = np.ascontiguousarray(M[:, :3, :3])
    T = np.ascontiguousarray(M[:, 3, :3])

    i0 = np.array([lut[int(b)] for b in bone0], dtype=np.intp)
    p0 = np.einsum("ni,nij->nj", pos, R[i0]) + T[i0]
    if not has_w1.any():
        return p0
    i1 = np.array([lut[int(b)] if bool(m) else 0
                   for b, m in zip(bone1, has_w1)], dtype=np.intp)
    p1 = np.einsum("ni,nij->nj", pos, R[i1]) + T[i1]
    w = np.where(has_w1, w0, 1.0)[:, None]
    return p0 * w + p1 * (1.0 - w)


def mesh_geometry(data: bytes, frame: int = 0, want_normals: bool = False,
                  include_sockets: bool = False,
                  apply_motion: bool = True) -> tuple[list[ChunkGeom], dict]:
    """Parse a `.c3` and return its drawable chunks, posed, in render space.

    `attach.PartMesh.parse` does the PHY/MOTI **ordinal** pairing; this function
    is the vectorised equivalent of `attach.PartMesh.world_vertices(IDENTITY)`
    and is checked against it by `--selftest`.
    """
    part = attach.PartMesh.parse(data)
    geoms: list[ChunkGeom] = []
    stats = {"chunks": len(part.chunks), "sockets": 0, "empty": 0,
             "other_tags": sorted({t.decode("latin-1", "replace")
                                   for t in part.other})}
    for ch in part.chunks:
        phy = getattr(ch, "phy", None)
        verts = getattr(phy, "vertices", None)
        if not verts or not getattr(phy, "faces", None):
            stats["empty"] += 1
            continue
        socket = attach.is_socket_name(phy.name)
        if socket:
            stats["sockets"] += 1
            if not include_sockets:
                continue

        n = len(verts)
        pos = np.empty((n, 3), dtype=np.float64)
        uv = np.empty((n, 2), dtype=np.float32)
        nrm = np.zeros((n, 3), dtype=np.float64)
        bone0 = np.empty(n, dtype=np.int32)
        bone1 = np.empty(n, dtype=np.int32)
        w0 = np.empty(n, dtype=np.float64)
        hw1 = np.empty(n, dtype=bool)
        for i, v in enumerate(verts):
            pos[i] = (v.px, v.py, v.pz)
            uv[i] = (v.u0, v.v0)
            nrm[i] = (v.nx, v.ny, v.nz)
            bone0[i] = v.bone0
            bone1[i] = v.bone1
            w0[i] = v.weight0
            hw1[i] = bool(v.weight1)

        # 1. the chunk's own 4x4, exactly as Phy_Load applies it at load time
        M = np.asarray(phy.matrix, dtype=np.float64)
        if M.size == 16:
            M = M.reshape(4, 4)
            pos = pos @ M[:3, :3] + M[3, :3]
            nrm = nrm @ M[:3, :3]

        # 2. the chunk's OWN MOTI, per vertex, at `frame`
        if apply_motion:
            pos = _skin(pos, bone0, bone1, w0, hw1, ch.motion, frame)
        if want_normals and apply_motion and ch.motion is not None:
            mats = _bone_matrices(ch.motion, sorted({int(b) for b in np.unique(bone0)}),
                                  frame)
            order = sorted(mats)
            lut = {b: i for i, b in enumerate(order)}
            R = np.stack([mats[b][:3, :3] for b in order])
            i0 = np.array([lut[int(b)] for b in bone0], dtype=np.intp)
            nrm = np.einsum("ni,nij->nj", nrm, R[i0])

        faces = np.asarray(phy.faces, dtype=np.int64)
        if faces.size == 0:
            stats["empty"] += 1
            continue
        faces = faces.reshape(-1, 3)
        faces = faces[(faces < n).all(axis=1)]              # 244 degenerate/bad
        if faces.size == 0:
            stats["empty"] += 1
            continue

        # 3. render space: C3 is Z-down; negate Z (docs/modding.md 9.7) and
        #    KEEP the index order -- the mirror is what re-winds the triangles.
        pos = pos.astype(np.float32)
        pos[:, 2] *= -1.0
        nrm = nrm.astype(np.float32)
        nrm[:, 2] *= -1.0
        geoms.append(ChunkGeom(phy.name, pos, uv, nrm, faces.astype(np.int32),
                               bool(phy.two_sided), socket))
    return geoms, stats


def generated_normals(geom: ChunkGeom) -> np.ndarray:
    """Area-weighted vertex normals, for `--shade lit` on PHY / PHY4 chunks."""
    p = geom.pos
    f = geom.faces
    e1 = p[f[:, 1]] - p[f[:, 0]]
    e2 = p[f[:, 2]] - p[f[:, 1]]
    fn = np.cross(e1, e2)
    acc = np.zeros_like(p)
    for k in range(3):
        np.add.at(acc, f[:, k], fn)
    ln = np.linalg.norm(acc, axis=1, keepdims=True)
    return np.divide(acc, np.where(ln > 0, ln, 1.0))


# ===========================================================================
# camera
# ===========================================================================

def project(pos: np.ndarray, yaw: float = YAW, pitch: float = PITCH,
            fov_deg: float = FOV_DEG, margin: float = MARGIN,
            bounds: Optional[tuple] = None):
    """Project render-space points to NDC + view depth, framed to `bounds`.

    Camera placement is gl.js's: orbit direction
    `(cos p cos y, cos p sin y, sin p)` in a Z-up world, eye at
    `centre + dir * 2.6 * radius`, `up = +Z`, 45 deg vertical FOV.  A final
    exact 2-D refit in NDC replaces gl.js's bounding-sphere fit, which leaves a
    lot of empty frame on a non-spherical asset; a uniform scale about the
    projected centre is still a valid perspective image, just a tighter FOV.
    """
    lo, hi = bounds if bounds is not None else (pos.min(0), pos.max(0))
    centre = (np.asarray(lo, np.float64) + np.asarray(hi, np.float64)) * 0.5
    radius = max(float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) * 0.5, 1e-4)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    d = np.array([cp * cy, cp * sy, sp], dtype=np.float64)
    eye = centre + d * radius * 2.6

    zax = d
    up = np.array([0.0, 0.0, 1.0])
    xax = np.cross(up, zax)
    if np.linalg.norm(xax) < 1e-6:
        xax = np.cross(np.array([0.0, 1.0, 0.0]), zax)
    xax /= np.linalg.norm(xax)
    yax = np.cross(zax, xax)

    rel = pos.astype(np.float64) - eye
    vx = rel @ xax
    vy = rel @ yax
    vz = rel @ zax
    w = np.maximum(-vz, 1e-6)                 # camera looks down -Z
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    nx = f * vx / w
    ny = f * vy / w

    # exact 2-D refit
    x0, x1 = float(nx.min()), float(nx.max())
    y0, y1 = float(ny.min()), float(ny.max())
    span = max(x1 - x0, y1 - y0, 1e-9)
    s = (2.0 - 2.0 * margin) / span
    nx = (nx - (x0 + x1) * 0.5) * s
    ny = (ny - (y0 + y1) * 0.5) * s
    return nx, ny, w


# ===========================================================================
# rasterizer
# ===========================================================================

def _sample(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear sample with REPEAT wrap -- gl.js uses LINEAR + REPEAT and every
    texture in this install is power-of-two, so REPEAT is always the live path.
    V is NOT flipped: D3D's V runs top-down and so does our row 0."""
    th, tw = tex.shape[0], tex.shape[1]
    x = u * tw - 0.5
    y = v * th - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0).astype(np.float32)[:, None]
    fy = (y - y0).astype(np.float32)[:, None]
    xi0 = np.mod(x0.astype(np.int64), tw)
    yi0 = np.mod(y0.astype(np.int64), th)
    xi1 = np.mod(xi0 + 1, tw)
    yi1 = np.mod(yi0 + 1, th)
    flat = tex.reshape(-1, 4)
    c00 = flat[yi0 * tw + xi0].astype(np.float32)
    c10 = flat[yi0 * tw + xi1].astype(np.float32)
    c01 = flat[yi1 * tw + xi0].astype(np.float32)
    c11 = flat[yi1 * tw + xi1].astype(np.float32)
    top = c00 + (c10 - c00) * fx
    bot = c01 + (c11 - c01) * fx
    return (top + (bot - top) * fy) * (1.0 / 255.0)


def rasterize(geoms: list[ChunkGeom], tex: Optional[np.ndarray], size: int,
              *, shade: str = "unlit", yaw: float = YAW, pitch: float = PITCH,
              cull: int = -1, bounds: Optional[tuple] = None) -> tuple:
    """Render `geoms` to a straight-alpha RGBA float image of `size` x `size`.

    Returns `(rgb (h,w,3) float32, alpha (h,w) float32, info dict)`.

    Sorted per-pixel alpha compositing rather than a plain z-buffer: every
    surviving fragment is bucketed by pixel, ordered front-to-back by view
    depth and composited with accumulated transmittance.  That is exact for
    opaque geometry (the nearest fragment wins) *and* right for the 1,326
    effect meshes whose textures are soft alpha gradients, which a z-buffer
    plus an alpha test renders as holes.

    `cull` is -1 to drop clockwise triangles (the engine's back faces, see the
    module docstring), +1 to drop counter-clockwise ones, 0 for no culling.
    `--selftest` uses +1 to prove -1 is the right sign.
    """
    W = H = int(size)
    info = {"triangles": 0, "drawn_triangles": 0, "fragments": 0, "capped": False}
    if not geoms:
        return (np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32), info)

    allpos = np.concatenate([g.pos for g in geoms])
    if bounds is None:
        bounds = (allpos.min(0), allpos.max(0))
    nx, ny, w = project(allpos, yaw, pitch, bounds=bounds)
    px = (nx + 1.0) * 0.5 * W
    py = (1.0 - ny) * 0.5 * H                  # row 0 is the top of the image

    if shade == "lit":
        nrms = []
        for g in geoms:
            n = g.nrm
            if not np.any(n):
                n = generated_normals(g)
            nrms.append(n)
        allnrm = np.concatenate(nrms)
        ln = np.linalg.norm(allnrm, axis=1, keepdims=True)
        allnrm = allnrm / np.where(ln > 0, ln, 1.0)
        light = np.array([0.4, 0.75, 0.55], np.float32)
        light = light / np.linalg.norm(light)
        lam = 0.42 + 0.58 * np.maximum(allnrm @ light, 0.0)
    else:
        lam = None

    alluv = np.concatenate([g.uv for g in geoms]).astype(np.float64)

    # one flat triangle table, with per-triangle two-sidedness
    tris = []
    tflag = []
    base = 0
    for g in geoms:
        tris.append(g.faces.astype(np.int64) + base)
        tflag.append(np.full(len(g.faces), g.two_sided, dtype=bool))
        base += len(g.pos)
    tris = np.concatenate(tris)
    tflag = np.concatenate(tflag)
    info["triangles"] = int(len(tris))

    ax, ay = px[tris[:, 0]], py[tris[:, 0]]
    bx, by = px[tris[:, 1]], py[tris[:, 1]]
    cx, cy = px[tris[:, 2]], py[tris[:, 2]]
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)

    # Winding.  In this y-DOWN pixel frame a triangle that is counter-clockwise
    # in NDC (the engine's front face after the Z mirror) has NEGATIVE area.
    if cull < 0:
        keep = (area < 0) | (tflag & (np.abs(area) > 0))
    elif cull > 0:
        keep = (area > 0) | (tflag & (np.abs(area) > 0))
    else:
        keep = np.abs(area) > 0
    keep &= np.abs(area) > 1e-12
    tris, area = tris[keep], area[keep]
    if len(tris) == 0:
        return (np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32), info)
    info["drawn_triangles"] = int(len(tris))

    ax, ay = px[tris[:, 0]], py[tris[:, 0]]
    bx, by = px[tris[:, 1]], py[tris[:, 1]]
    cx, cy = px[tris[:, 2]], py[tris[:, 2]]
    iw = (1.0 / w)[tris]                                   # (T, 3)
    uvw = alluv[tris] * iw[:, :, None]                     # (T, 3, 2)
    shw = (lam[tris] * iw) if lam is not None else None

    x_lo = np.maximum(np.floor(np.minimum(np.minimum(ax, bx), cx)), 0).astype(np.int64)
    x_hi = np.minimum(np.ceil(np.maximum(np.maximum(ax, bx), cx)), W - 1).astype(np.int64)
    y_lo = np.maximum(np.floor(np.minimum(np.minimum(ay, by), cy)), 0).astype(np.int64)
    y_hi = np.minimum(np.ceil(np.maximum(np.maximum(ay, by), cy)), H - 1).astype(np.int64)
    bw = np.maximum(x_hi - x_lo + 1, 0)
    bh = np.maximum(y_hi - y_lo + 1, 0)
    cand = bw * bh
    on = cand > 0
    if not on.any():
        return (np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32), info)

    idx_all = np.nonzero(on)[0]
    f_pix: list[np.ndarray] = []
    f_dep: list[np.ndarray] = []
    f_rgba: list[np.ndarray] = []
    total_frags = 0

    # batch so peak memory does not scale with mesh size
    csum = np.cumsum(cand[idx_all])
    batch_edges = [0]
    while batch_edges[-1] < len(idx_all):
        start = batch_edges[-1]
        target = csum[start - 1] if start else 0
        nxt = int(np.searchsorted(csum, target + CANDIDATE_BUDGET, side="right"))
        batch_edges.append(max(nxt, start + 1))

    for bi in range(len(batch_edges) - 1):
        sel = idx_all[batch_edges[bi]:batch_edges[bi + 1]]
        n_c = cand[sel]
        ti = np.repeat(np.arange(len(sel), dtype=np.int64), n_c)
        starts = np.concatenate([[0], np.cumsum(n_c)[:-1]])
        off = np.arange(int(n_c.sum()), dtype=np.int64) - np.repeat(starts, n_c)
        bwl = bw[sel][ti]
        qx = (x_lo[sel][ti] + off % bwl).astype(np.float64)
        qy = (y_lo[sel][ti] + off // bwl).astype(np.float64)
        sx, sy = qx + 0.5, qy + 0.5

        a_x, a_y = ax[sel][ti], ay[sel][ti]
        b_x, b_y = bx[sel][ti], by[sel][ti]
        c_x, c_y = cx[sel][ti], cy[sel][ti]
        e0 = (c_x - b_x) * (sy - b_y) - (sx - b_x) * (c_y - b_y)
        e1 = (a_x - c_x) * (sy - c_y) - (sx - c_x) * (a_y - c_y)
        e2 = (b_x - a_x) * (sy - a_y) - (sx - a_x) * (b_y - a_y)
        ar = area[sel][ti]
        sgn = np.sign(ar)
        inside = (e0 * sgn >= 0) & (e1 * sgn >= 0) & (e2 * sgn >= 0)
        if not inside.any():
            continue
        ti = ti[inside]
        l0 = (e0[inside] / ar[inside])
        l1 = (e1[inside] / ar[inside])
        l2 = 1.0 - l0 - l1
        pix = (qy[inside].astype(np.int64) * W + qx[inside].astype(np.int64))

        iws = iw[sel]
        invw = l0 * iws[ti, 0] + l1 * iws[ti, 1] + l2 * iws[ti, 2]
        invw = np.where(np.abs(invw) < 1e-12, 1e-12, invw)
        depth = 1.0 / invw
        uvs = uvw[sel]
        u = (l0 * uvs[ti, 0, 0] + l1 * uvs[ti, 1, 0] + l2 * uvs[ti, 2, 0]) * depth
        v = (l0 * uvs[ti, 0, 1] + l1 * uvs[ti, 1, 1] + l2 * uvs[ti, 2, 1]) * depth

        if tex is not None:
            rgba = _sample(tex, u, v)
        else:
            rgba = np.ones((len(u), 4), np.float32)
        if shw is not None:
            sh = shw[sel]
            f = ((l0 * sh[ti, 0] + l1 * sh[ti, 1] + l2 * sh[ti, 2]) * depth)
            rgba[:, :3] *= f.astype(np.float32)[:, None]

        live = rgba[:, 3] >= ALPHA_EPS
        if not live.any():
            continue
        f_pix.append(pix[live])
        f_dep.append(depth[live].astype(np.float32))
        f_rgba.append(rgba[live])
        total_frags += int(live.sum())
        if total_frags > FRAGMENT_CAP:
            info["capped"] = True
            break

    if not f_pix:
        return (np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32), info)

    pix = np.concatenate(f_pix)
    depth = np.concatenate(f_dep)
    rgba = np.concatenate(f_rgba)
    info["fragments"] = int(len(pix))

    # front-to-back sorted compositing.  A single uint64 key (pixel, depth) is
    # sortable in one stable pass -- IEEE-754 bit order matches numeric order
    # for the positive floats depth always is here.
    key = (pix.astype(np.uint64) << np.uint64(32)) | depth.view(np.uint32).astype(np.uint64)
    order = np.argsort(key, kind="stable")
    pix = pix[order]
    rgba = rgba[order]

    # rank of each fragment within its pixel group
    n = len(pix)
    newgrp = np.empty(n, dtype=bool)
    newgrp[0] = True
    np.not_equal(pix[1:], pix[:-1], out=newgrp[1:])
    grp_start = np.nonzero(newgrp)[0]
    rank = np.arange(n, dtype=np.int64) - np.repeat(grp_start, np.diff(
        np.append(grp_start, n)))

    rgb = np.zeros(W * H * 3, np.float32).reshape(W * H, 3)
    trans = np.ones(W * H, np.float32)
    max_rank = int(rank.max())
    for r in range(max_rank + 1):
        sel = rank == r
        if not sel.any():
            continue
        p = pix[sel]
        c = rgba[sel]
        t = trans[p]
        if t.max() < 0.004:
            break
        a = c[:, 3]
        rgb[p] += (t * a)[:, None] * c[:, :3]
        trans[p] = t * (1.0 - a)

    alpha = (1.0 - trans).reshape(H, W)
    rgb = rgb.reshape(H, W, 3)
    return rgb, alpha, info


def best_uv_cell(geoms: list[ChunkGeom], tex: np.ndarray) -> Optional[tuple]:
    """Pick the flipbook cell with the most ink, for a mesh whose UVs address
    only a sub-rectangle of its texture.

    `docs/effects.md` 6.4: an effect sheet is a grid of animation cells and the
    engine scrolls the UVs to select one (gl.js's `uUVOffset`).  At the frame-0
    offset of 0 the cell is frequently blank -- 16 of the 4,950 meshes render to
    a completely transparent image for exactly this reason.  Rather than emit
    nothing, offset the UVs onto whichever cell of the sheet carries the most
    alpha.  Returns `(du, dv, cell_i, cell_j)` or None if the mesh is not a
    sub-rectangle sampler.
    """
    uv = np.concatenate([g.uv for g in geoms]).astype(np.float64)
    u0, v0 = uv.min(0)
    u1, v1 = uv.max(0)
    du, dv = u1 - u0, v1 - v0
    if not (1e-6 < du <= 0.55 and 1e-6 < dv <= 0.55):
        return None
    nu = max(1, int(round(1.0 / du)))
    nv = max(1, int(round(1.0 / dv)))
    if nu * nv > 256:
        return None
    th, tw = tex.shape[0], tex.shape[1]
    a = tex[:, :, 3].astype(np.float32)
    best = (-1.0, 0, 0)
    for j in range(nv):
        for i in range(nu):
            us = np.arange(int((u0 + i * du) * tw), int((u1 + i * du) * tw) + 1) % tw
            vs = np.arange(int((v0 + j * dv) * th), int((v1 + j * dv) * th) + 1) % th
            m = float(a[np.ix_(vs, us)].mean())
            if m > best[0]:
                best = (m, i, j)
    if best[0] <= 0.0:
        return None
    return (du, dv, best[1], best[2])


def shift_uv(geoms: list[ChunkGeom], du: float, dv: float, i: int, j: int) -> list[ChunkGeom]:
    out = []
    for g in geoms:
        uv = g.uv.copy()
        uv[:, 0] += float(i * du)
        uv[:, 1] += float(j * dv)
        out.append(ChunkGeom(g.name, g.pos, uv, g.nrm, g.faces, g.two_sided,
                             g.is_socket))
    return out


def to_image(rgb: np.ndarray, alpha: np.ndarray, size: int, ss: int,
             bg: Optional[tuple]) -> np.ndarray:
    """Downsample the supersampled buffer and pack to uint8 RGBA."""
    if ss > 1:
        h, w = alpha.shape
        rgb = rgb.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
        alpha = alpha.reshape(size, ss, size, ss).mean(axis=(1, 3))
    if bg is None:
        # straight (un-premultiplied) alpha
        a = np.maximum(alpha, 1e-6)[:, :, None]
        out = np.clip(rgb / a, 0.0, 1.0)
        arr = np.empty((size, size, 4), np.uint8)
        arr[:, :, :3] = (out * 255.0 + 0.5).astype(np.uint8)
        arr[:, :, 3] = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
    else:
        b = np.asarray(bg, np.float32)
        out = np.clip(rgb + b * (1.0 - alpha)[:, :, None], 0.0, 1.0)
        arr = np.empty((size, size, 4), np.uint8)
        arr[:, :, :3] = (out * 255.0 + 0.5).astype(np.uint8)
        arr[:, :, 3] = 255
    return arr


def save_png(arr: np.ndarray, path: Path) -> int:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(path, format="PNG", compress_level=6)
    return path.stat().st_size


# ===========================================================================
# work items
# ===========================================================================

@dataclass
class Job:
    logical: str
    kind: str                       # "mesh" | "texture"
    texture: Optional[str] = None
    method: str = ""
    confidence: float = 0.0
    match_kind: str = ""
    detail: str = ""
    old_key: str = ""


def out_path(kind: str, logical: str) -> Path:
    stem = logical.rsplit(".", 1)[0]
    return OUT_DIR / kind / (stem + ".png")


def load_worklist(idx_path: Optional[Path] = None) -> tuple[list[Job], list[str]]:
    """Mesh work list, straight out of `out/meshtex/coverage.json` (a linked
    worktree reads the primary checkout's copy).

    Falls back to building a `meshtex.MeshTextureIndex` live (~2 min, it has to
    parse every `.c3`) if that file has not been generated.
    """
    if idx_path is None:
        import coroot
        # NO FALLBACK to a module-level constant. `derived_path` resolved at
        # import names whichever install was configured *then*, so a base with
        # no coverage file of its own would silently load ANOTHER install's
        # index -- measured on exactly this pattern in `unify`, where 6090 and
        # 5517 both reported the same 5042 meshes. `docs/CORRECTIONS.md` C21.
        # `find_derived` returning None is the honest answer, and it lands on
        # the live rebuild this function's own docstring promises.
        idx_path = coroot.find_derived("out/meshtex/coverage.json")
    if idx_path is not None and idx_path.is_file():
        doc = json.loads(idx_path.read_text("utf-8"))
        jobs, unmatched = [], []
        for logical, rec in doc["meshes"].items():
            ms = rec.get("matches") or []
            if not ms:
                unmatched.append(logical)
                continue
            m = ms[0]
            jobs.append(Job(logical, "mesh", m["texture"], m["method"],
                            float(m.get("confidence", 0.0)),
                            m.get("kind", ""), m.get("detail", "")))
        jobs.sort(key=lambda j: (j.texture or "", j.logical))
        return jobs, unmatched

    import meshtex
    jobs, unmatched = [], []
    with meshtex.MeshTextureIndex() as mi:
        for logical in mi.all_meshes():
            best = mi.best(logical)
            if best is None:
                unmatched.append(logical)
                continue
            jobs.append(Job(logical, "mesh", best.texture, best.method,
                            best.confidence, best.kind, best.detail))
    jobs.sort(key=lambda j: (j.texture or "", j.logical))
    return jobs, unmatched


def texture_universe() -> list[str]:
    """Every `.dds` the client can open (loose + recovered archive names)."""
    import meshtex
    with meshtex.MeshTextureIndex() as mi:
        return sorted(mi.textures)


def server_worklist(view) -> tuple[list["Job"], list[str]]:
    """Mesh work list for a COmmunity Library server view.

    `tools/meshtex.py` indexes the *install*, so a server view pairs its own
    way, strongest evidence first: the server's appearance tables (including
    the tables `colibrary` synthesizes from old-client conventions), then a
    same-stem texture beside the mesh, then any same-directory texture.
    Motion-only action files (MOTI, no PHY) have nothing to render and land
    in `unmatched` -- listed, not silently dropped.
    """
    fm = view.filemap
    meshes = sorted(k for k in fm if k.endswith(".c3"))
    texset = {k for k in fm if k.endswith(".dds")}
    paired: dict[str, tuple[str, str, float]] = {}
    from coassets import AssetRoot as _AR
    all_tables = list(view.part_tables().items())
    try:
        # baseline tables as a second source (unbound call bypasses the
        # ServerView override); a pairing only sticks if both halves
        # resolve in the view, so this cannot invent art the server lacks.
        all_tables += [(f"base:{k}", v)
                       for k, v in _AR.part_tables(view).items()]
    except Exception:
        pass
    for part, ini in all_tables:
        for app in ini:
            for pr in app.parts:
                mloc = view.resolve_asset(pr.mesh, "mesh")
                tloc = view.resolve_asset(pr.texture, "texture")
                if mloc and tloc:
                    paired.setdefault(
                        mloc.logical,
                        (tloc.logical, f"table:{ini.name}", 1.0))
    by_dir: dict[str, list[str]] = {}
    for t in sorted(texset):
        by_dir.setdefault(t.rsplit("/", 1)[0], []).append(t)
    by_stem: dict[str, str] = {}
    for t in sorted(texset):
        tstem = t.rsplit("/", 1)[-1][:-4]
        if tstem.isdigit() and len(tstem) < 5:
            continue
        by_stem.setdefault(tstem, t)
    for m in meshes:
        if m in paired:
            continue
        stem = m[:-3]
        if stem + ".dds" in texset:
            paired[m] = (stem + ".dds", "same-stem", 0.9)
            continue
        mstem = m.rsplit("/", 1)[-1][:-3]
        hit = None if (mstem.isdigit() and len(mstem) < 5)             else by_stem.get(mstem)
        if hit:
            paired[m] = (hit, "stem-anywhere", 0.7)
            continue
        sibs = by_dir.get(m.rsplit("/", 1)[0])
        if sibs:
            paired[m] = (sibs[0], "same-dir", 0.5)
    jobs, unmatched = [], []
    for m in meshes:
        hit = paired.get(m)
        if hit is None:
            unmatched.append(m)
            continue
        # a container with no PHY chunk is motion data, not a model
        try:
            from coassets import C3File
            from c3phy import VARIANTS
            c3 = C3File(view.read(m), strict=False)
            if not any(ch.tag in VARIANTS for ch in c3.chunks):
                unmatched.append(m)
                continue
        except Exception:
            unmatched.append(m)
            continue
        jobs.append(Job(m, "mesh", hit[0], hit[1], hit[2], "server", ""))
    jobs.sort(key=lambda j: (j.texture or "", j.logical))
    return jobs, unmatched


def server_texture_universe(view) -> list[str]:
    return sorted(k for k in view.filemap if k.endswith(".dds"))


# ===========================================================================
# worker
# ===========================================================================

_W: dict = {}


def _init_worker(root: str, opts: dict, library: str = "",
                 server: str = "") -> None:
    _apply_output(server, root)
    if server:
        from colibrary import ServerView
        _W["assets"] = ServerView(library, server, root)
    else:
        _W["assets"] = AssetRoot(root)
    _W["opts"] = opts
    _W["tex_cache"] = {}


def _texture_rgba(logical: str):
    """Decode a DDS to an (h, w, 4) uint8 array, cached per worker process.

    Jobs are ordered by texture so the cache of one is enough: 4,950 meshes
    share 2,786 distinct textures, and `dds._decode_bc` is pure python.
    """
    cache = _W["tex_cache"]
    hit = cache.get(logical)
    if hit is not None:
        return hit
    raw = _W["assets"].read(logical)
    w, h, rgba = dds.decode(raw)
    arr = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)
    cache.clear()                     # one entry: the work list is texture-major
    cache[logical] = (arr, raw)
    return arr, raw


#: Which options actually change the pixels of each kind of output.  Keeping
#: these disjoint means changing a texture-pass setting does not invalidate
#: 4,950 mesh renders, and vice versa.
KEY_OPTS = {"mesh": ("size", "ss", "shade", "frame", "bg"),
            "texture": ("tex_size", "tex_quantize")}


def _key(mesh_bytes: bytes, tex_bytes: Optional[bytes], opts: dict,
         kind: str = "mesh") -> str:
    h = hashlib.sha1()
    h.update(b"v%d;%s;" % (RENDERER_VERSION, kind.encode()))
    h.update(json.dumps({k: opts.get(k) for k in KEY_OPTS[kind]},
                        sort_keys=True).encode())
    h.update(hashlib.sha1(mesh_bytes).digest())
    if tex_bytes is not None:
        h.update(hashlib.sha1(tex_bytes).digest())
    return h.hexdigest()


def render_job(job: Job) -> dict:
    opts = _W["opts"]
    res = {"logical": job.logical, "kind": job.kind, "status": "ok"}
    try:
        if job.kind == "texture":
            raw = _W["assets"].read(job.logical)
            k = _key(raw, None, opts, "texture")
            path = out_path("texture", job.logical)
            if k == job.old_key and path.is_file():
                res["status"] = "cached"
                res["key"] = k
                res["bytes"] = path.stat().st_size
                return res
            from PIL import Image
            try:
                w, h, rgba = dds.decode(raw)
                arr = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()
                im = Image.fromarray(arr, "RGBA")
            except dds.DdsError:
                # two files in this install are a JPEG and a BMP wearing a
                # .dds extension; Pillow reads them fine
                import io
                im = Image.open(io.BytesIO(raw)).convert("RGBA")
                w, h = im.size
                res["container"] = "not-dds"
            size = opts["tex_size"]
            im.thumbnail((size, size), Image.LANCZOS)
            path.parent.mkdir(parents=True, exist_ok=True)
            if opts["tex_quantize"]:
                # 255-colour octree with the alpha channel carried through.
                # Measured over a 300-file sample: 26% of the truecolour size,
                # 1.35 GB -> 349 GB over the whole library, and at 128 px the
                # difference is not visible.  --tex-truecolor turns it off.
                im.quantize(colors=255, method=Image.FASTOCTREE).save(
                    path, format="PNG", optimize=True)
            else:
                im.save(path, format="PNG", compress_level=6)
            res.update(key=k, bytes=path.stat().st_size,
                       width=im.width, height=im.height,
                       source_size=[w, h])
            return res

        data = _W["assets"].read(job.logical)
        tex_arr = tex_raw = None
        if job.texture:
            tex_arr, tex_raw = _texture_rgba(job.texture)
        k = _key(data, tex_raw, opts)
        path = out_path("mesh", job.logical)
        if k == job.old_key and path.is_file():
            res["status"] = "cached"
            res["key"] = k
            res["bytes"] = path.stat().st_size
            return res

        size, ss = opts["size"], opts["ss"]
        lit = opts["shade"] == "lit"
        fallbacks: list[str] = []

        geoms, stats = mesh_geometry(data, frame=opts["frame"], want_normals=lit)
        sockets_only = False
        if not geoms:
            geoms, stats = mesh_geometry(data, frame=opts["frame"],
                                         want_normals=lit, include_sockets=True)
            sockets_only = bool(geoms)
        if not geoms:
            res["status"] = "failed"
            res["error"] = "no drawable geometry (%d chunks, %d sockets, tags %s)" % (
                stats["chunks"], stats["sockets"], ",".join(stats["other_tags"]))
            return res

        # Some chunks are keyed to a zero-scale bone-0 matrix at frame 0 -- an
        # effect that grows from nothing, or a weapon that appears mid-swing.
        # The engine is right to draw nothing; a thumbnail is not.  Fall back to
        # the stored pose (chunk matrix only, no MOTI).
        allpos = np.concatenate([g.pos for g in geoms])
        if float((allpos.max(0) - allpos.min(0)).max()) < 1e-4:
            g2, s2 = mesh_geometry(data, frame=opts["frame"], want_normals=lit,
                                   include_sockets=sockets_only,
                                   apply_motion=False)
            p2 = np.concatenate([g.pos for g in g2]) if g2 else None
            if p2 is not None and float((p2.max(0) - p2.min(0)).max()) >= 1e-4:
                geoms, stats, allpos = g2, s2, p2
                fallbacks.append("no_motion")

        # Fallback chain.  Each step exists because "nothing at all" is a worse
        # thumbnail than "the right asset drawn slightly unlike the engine would
        # draw it at this exact instant", and each records what it did.
        rgb, alpha, info = rasterize(geoms, tex_arr, size * ss, shade=opts["shade"])
        arr = to_image(rgb, alpha, size, ss, opts["bg"])

        def empty() -> bool:
            return not arr[:, :, 3].any()

        if empty() and info["drawn_triangles"] < info["triangles"]:
            # a single-sided flat plane whose front happens to face away
            rgb, alpha, info = rasterize(geoms, tex_arr, size * ss,
                                         shade=opts["shade"], cull=0)
            arr = to_image(rgb, alpha, size, ss, opts["bg"])
            if not empty():
                fallbacks.append("no_cull")
        if empty() and tex_arr is not None:
            cell = best_uv_cell(geoms, tex_arr)
            if cell is not None:
                du, dv, ci, cj = cell
                rgb, alpha, info = rasterize(shift_uv(geoms, du, dv, ci, cj),
                                             tex_arr, size * ss,
                                             shade=opts["shade"], cull=0)
                arr = to_image(rgb, alpha, size, ss, opts["bg"])
                if not empty():
                    fallbacks.append("uv_cell")
                    res["uv_cell"] = [ci, cj]
        if empty() and tex_arr is not None and not tex_arr[:, :, 3].any():
            # a sheet with an all-zero alpha channel is an additive effect
            # texture: its RGB *is* the intensity.  Key on luminance instead.
            t2 = tex_arr.copy()
            t2[:, :, 3] = tex_arr[:, :, :3].max(axis=2)
            rgb, alpha, info = rasterize(geoms, t2, size * ss,
                                         shade=opts["shade"], cull=0)
            arr = to_image(rgb, alpha, size, ss, opts["bg"])
            if not empty():
                fallbacks.append("alpha_from_luma")
        if empty():
            res["status"] = "failed"
            res["error"] = (
                "rendered empty (%d/%d triangles survived culling; the chosen "
                "texture has no visible texel under this mesh's UVs)"
                % (info["drawn_triangles"], info["triangles"]))
            return res
        if fallbacks:
            res["fallbacks"] = fallbacks
        cov = float((arr[:, :, 3] > 0).mean())
        if cov < 0.002:
            # sub-pixel geometry: ribbons and slivers whose triangles are ~1 px
            # of area even framed to their own bounds.  Not a failure -- this is
            # what the engine's own pixel-centre coverage rule gives -- but the
            # thumbnail is not worth showing, so say so.
            res["low_coverage"] = True
        nbytes = save_png(arr, path)

        res.update(key=k, bytes=nbytes, width=size, height=size,
                   chunks=stats["chunks"], drawn_chunks=len(geoms),
                   sockets=stats["sockets"], triangles=info["triangles"],
                   drawn_triangles=info["drawn_triangles"],
                   coverage=round(float((arr[:, :, 3] > 0).mean()), 4),
                   bounds=[[round(float(x), 3) for x in allpos.min(0)],
                           [round(float(x), 3) for x in allpos.max(0)]])
        if sockets_only:
            res["sockets_only"] = True
        if info["capped"]:
            res["capped"] = True
        return res
    except Exception as e:                       # one bad file must not stop 5k
        res["status"] = "failed"
        res["error"] = f"{type(e).__name__}: {e}"
        return res


# ===========================================================================
# manifest
# ===========================================================================

MANIFEST = OUT_DIR / "manifest.json"
MESH_MANIFEST = OUT_DIR / "manifest_meshes.json"


def load_manifest() -> dict:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text("utf-8"))
        except Exception:
            pass
    return {"schema": 1, "entries": {}}


def write_manifest(doc: dict) -> None:
    """Write the full manifest, plus a mesh-only slice of the same schema.

    The full file is ~23 MB because the texture library is 66,834 entries; a
    consumer that only wants model previews should read `manifest_meshes.json`
    (~1.5 MB, identical schema, `entries` filtered to `kind == "mesh"`).
    Both are written atomically so an interrupted run cannot leave a torn file.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")), "utf-8")
    tmp.replace(MANIFEST)

    slim = dict(doc)
    slim["entries"] = {k: v for k, v in doc["entries"].items()
                       if v.get("kind") == "mesh"}
    slim["subset"] = "meshes only; see manifest.json for the texture library"
    tmp = MESH_MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(slim, indent=1, sort_keys=True), "utf-8")
    tmp.replace(MESH_MANIFEST)

    # Sidecars rather than envelopes: eleven modules read this manifest's
    # shape. `out/thumbs/` is per-base, and the entries are additionally
    # content-addressed (the key hashes the mesh and texture bytes), which is
    # why a foreign manifest was safe by accident rather than by design --
    # the stamp makes it safe on purpose.
    for path in (MANIFEST, MESH_MANIFEST):
        try:
            provenance.stamp_file(path, tool="thumbs.py")
        except Exception:                                # pragma: no cover
            pass


# ===========================================================================
# driver
# ===========================================================================

def run(jobs: list[Job], opts: dict, root: str, jobs_n: int,
        manifest: dict, label: str) -> dict:
    import multiprocessing as mp

    tally = {"ok": 0, "cached": 0, "failed": 0, "bytes": 0}
    failures: list[tuple[str, str]] = []
    t0 = time.time()
    total = len(jobs)
    entries = manifest["entries"]

    def absorb(r: dict) -> None:
        st = r["status"]
        tally[st] = tally.get(st, 0) + 1
        tally["bytes"] += int(r.get("bytes", 0) or 0)
        if st == "failed":
            failures.append((r["logical"], r.get("error", "")))
            entries.pop(r["logical"], None)
            return
        e = entries.setdefault(r["logical"], {})
        e.update({k: v for k, v in r.items() if k not in ("status",)})
        e["thumb"] = out_path(r["kind"], r["logical"]).relative_to(
            OUT_DIR).as_posix()

    if jobs_n <= 1:
        _init_worker(root, opts, *_LIB_SRV)
        for i, j in enumerate(jobs, 1):
            absorb(render_job(j))
            _progress(label, i, total, t0, tally)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(jobs_n, initializer=_init_worker,
                      initargs=(root, opts, *_LIB_SRV)) as pool:
            for i, r in enumerate(pool.imap(render_job, jobs, chunksize=8), 1):
                absorb(r)
                _progress(label, i, total, t0, tally)
    sys.stderr.write("\n")
    tally["seconds"] = round(time.time() - t0, 1)
    tally["failures"] = failures
    return tally


_last_print = [0.0]


def _progress(label: str, i: int, total: int, t0: float, tally: dict) -> None:
    now = time.time()
    if i != total and now - _last_print[0] < 1.0:
        return
    _last_print[0] = now
    el = now - t0
    rate = i / el if el > 0 else 0
    eta = (total - i) / rate if rate > 0 else 0
    sys.stderr.write(
        "\r%s %6d/%-6d  ok %-5d cached %-5d failed %-4d  %5.1f/s  eta %4.0fs  "
        "%6.1f MB" % (label, i, total, tally["ok"], tally["cached"],
                      tally["failed"], rate, eta, tally["bytes"] / 1e6))
    sys.stderr.flush()


# ===========================================================================
# self-test
# ===========================================================================

REFERENCE = "c3/mesh/002135000.c3"


def outward_normals(geom: "ChunkGeom") -> np.ndarray:
    """Per-face **outward** normal, in render space.

    The engine's own generated face normal is `cross(p2-p1, p1-p0)` in C3 space
    (docs/modding.md 9.7, RVA `0x5A8C0`), and that is the outward one -- it
    points away from the mesh centroid on a majority of faces on 482 of 496
    character meshes.  Under the render-space mirror `S = diag(1,1,-1)` a normal
    maps as `n -> S n`, and `cross(S a, S b) = -S cross(a, b)` because
    `det S = -1`.  So in render space the outward normal is
    `cross(q1-q0, q2-q1)` -- the *other* edge order.  (Note that
    `c3phy.generate_normals` uses that same order, which means it produces
    inward normals when handed C3-space positions and outward ones when handed
    render-space positions.  This module only ever hands it render space.)
    """
    p, f = geom.pos, geom.faces
    return np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 1]])


def winding_agreement(geoms: list["ChunkGeom"], size: int = 512) -> float:
    """Fraction of triangles where "screen area < 0" == "outward normal faces
    the eye".  This is the decisive culling test: 1.0 means `cull=-1` keeps
    exactly the front faces, 0.0 means the sign is backwards."""
    allpos = np.concatenate([g.pos for g in geoms])
    lo, hi = allpos.min(0), allpos.max(0)
    centre = (lo + hi) * 0.5
    radius = max(float(np.linalg.norm(hi - lo)) * 0.5, 1e-4)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    cy, sy = math.cos(YAW), math.sin(YAW)
    eye = centre + np.array([cp * cy, cp * sy, sp]) * radius * 2.6

    nx, ny, _ = project(allpos)
    px = (nx + 1.0) * 0.5 * size
    py = (1.0 - ny) * 0.5 * size
    ok = tot = 0
    base = 0
    for g in geoms:
        f = g.faces.astype(np.int64) + base
        base += len(g.pos)
        n = outward_normals(g)
        cent = (allpos[f[:, 0]] + allpos[f[:, 1]] + allpos[f[:, 2]]) / 3.0
        faces_eye = (n * (eye - cent)).sum(1) > 0
        ax, ay = px[f[:, 0]], py[f[:, 0]]
        bx, by = px[f[:, 1]], py[f[:, 1]]
        cx, cy2 = px[f[:, 2]], py[f[:, 2]]
        area = (bx - ax) * (cy2 - ay) - (cx - ax) * (by - ay)
        good = np.abs(area) > 1e-9
        ok += int(((area < 0) == faces_eye)[good].sum())
        tot += int(good.sum())
    return ok / max(tot, 1)


def selftest(root: str) -> int:
    """Prove the things that fail silently: PHY/MOTI pairing, the transform
    chain, and above all the winding sign.

    The winding check is not "does the picture look plausible" -- an inside-out
    model looks entirely plausible at 128 px, which is the whole danger.  It
    compares the screen-space signed area against the engine's *own* generated
    outward normal, face by face, and demands they agree on every triangle.
    """
    bad = 0
    assets = AssetRoot(root)
    print(f"reference: {REFERENCE}")
    data = assets.read(REFERENCE)

    geoms, stats = mesh_geometry(data)
    names = [g.name for g in geoms]
    print(f"  chunks {stats['chunks']}, sockets skipped {stats['sockets']}, "
          f"drawn {names}")
    if names != ["v_body"]:
        print("  !! expected exactly the v_body chunk to be drawn")
        bad += 1

    # 1. agreement with tools/attach.py's own scalar implementation
    part = attach.PartMesh.parse(data)
    ref = np.array(list(part.world_vertices(attach.IDENTITY, 0)), np.float64)
    ref[:, 2] *= -1.0                                    # attach stays in C3 space
    mine = np.concatenate([g.pos for g in geoms]).astype(np.float64)
    if ref.shape != mine.shape:
        print(f"  !! vertex count differs: attach {ref.shape} vs here {mine.shape}")
        bad += 1
    else:
        d = float(np.abs(ref - mine).max())
        print(f"  transform chain vs attach.world_vertices: max |delta| = {d:.6g}")
        if d > 1e-3:
            print("  !! transform chain disagrees with tools/attach.py")
            bad += 1

    # 2. upright and the right size
    lo, hi = mine.min(0), mine.max(0)
    height = float(hi[2] - lo[2])
    print(f"  render-space bounds  x[{lo[0]:.1f},{hi[0]:.1f}] "
          f"y[{lo[1]:.1f},{hi[1]:.1f}] z[{lo[2]:.1f},{hi[2]:.1f}]  height {height:.1f}")
    if not (168.0 <= height <= 173.0):
        print("  !! docs/attachment.md 8.2 measures this body at 170.4 units")
        bad += 1
    if lo[2] < -2.0:
        print("  !! the model should stand on z=0 with +Z up after the mirror")
        bad += 1

    # 3. winding
    import meshtex
    with meshtex.MeshTextureIndex() as mi:
        best = mi.best(REFERENCE)
    print(f"  texture: {best.texture}  ({best.method}, {best.kind}, "
          f"conf {best.confidence})")
    w, h, rgba = dds.decode(assets.read(best.texture))
    tex = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)

    agree = winding_agreement(geoms)
    print(f"  winding: 'screen area < 0' == 'engine outward normal faces the "
          f"eye' on {agree*100:.2f}% of triangles")
    if agree < 0.999:
        print("  !! culling sign is backwards -- the model would render inside out")
        bad += 1

    outp = OUT_DIR / "_selftest"
    for sign, name in ((-1, "reference_correct.png"), (1, "reference_inverted.png")):
        rgb, alpha, info = rasterize(geoms, tex, 512, cull=sign)
        save_png(to_image(rgb, alpha, 256, 2, BG), outp / name)
        print(f"  cull {'correct' if sign < 0 else 'INVERTED':<9} "
              f"{info['drawn_triangles']:5d}/{info['triangles']} tris, "
              f"{info['fragments']} fragments -> {name}")

    # which way does the corpus face?  the picture behind the YAW constant.
    tiles = []
    for yaw in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
        rgb, alpha, info = rasterize(geoms, tex, 384, yaw=yaw, pitch=0.15)
        tiles.append(to_image(rgb, alpha, 192, 2, BG))
    save_png(np.concatenate(tiles, axis=1), outp / "yaw_grid.png")
    print(f"  wrote {outp}/yaw_grid.png "
          f"(eye at +X | +Y | -X | -Y -- only -Y shows a face)")
    print("failed checks:", bad)
    return 1 if bad else 0


# ===========================================================================
# CLI
# ===========================================================================

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--library", default="",
                    help="COmmunity Library root (with --server)")
    ap.add_argument("--server", default="",
                    help="render a library server view's assets instead of "
                         "the install; output goes to out/thumbs/servers/"
                         "<name>/ with its own manifest")
    ap.add_argument("--all", action="store_true",
                    help="render every mesh with a matched texture")
    ap.add_argument("--textures", action="store_true",
                    help="second pass: decode-and-downscale every .dds")
    ap.add_argument("--only-unmatched-textures", action="store_true",
                    help="with --textures, skip textures already used by a mesh")
    ap.add_argument("--resume", action="store_true",
                    help="skip entries whose content hash is unchanged "
                         "(this is the default; --no-resume forces a re-render)")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--size", type=int, default=256, help="output edge, px")
    ap.add_argument("--tex-size", type=int, default=128,
                    help="output edge for texture thumbnails, px")
    ap.add_argument("--tex-truecolor", action="store_true",
                    help="keep texture thumbnails 24-bit instead of palettising "
                         "them (about 4x the disk)")
    ap.add_argument("--ss", type=int, default=2, help="supersampling factor")
    ap.add_argument("--jobs", type=int, default=0, help="0 = cpu_count - 1")
    ap.add_argument("--shade", choices=("unlit", "lit"), default="unlit")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--bg", default="transparent",
                    help="'transparent' (default) or 'viewer' or '#rrggbb'")
    ap.add_argument("--limit", type=int, help="stop after N work items")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--one", metavar="LOGICAL", help="render a single asset")
    ap.add_argument("--out", help="with --one, the output png path")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.bg == "transparent":
        bg = None
    elif a.bg == "viewer":
        bg = list(BG)
    else:
        s = a.bg.lstrip("#")
        bg = [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    opts = {"size": a.size, "ss": a.ss, "shade": a.shade, "frame": a.frame,
            "bg": bg, "tex_size": a.tex_size,
            "tex_quantize": not a.tex_truecolor}

    if a.server and not a.library:
        ap.error("--server needs --library")
    global _LIB_SRV
    _LIB_SRV = (a.library, a.server)
    _apply_output(a.server or "", a.root)
    view = None
    if a.server:
        from colibrary import ServerView
        view = ServerView(a.library, a.server, a.root)

    if a.selftest:
        return selftest(a.root)

    if a.one:
        _init_worker(a.root, opts)
        import meshtex
        with meshtex.MeshTextureIndex() as mi:
            best = mi.best(a.one)
        job = Job(a.one, "mesh", best.texture if best else None,
                  best.method if best else "", best.confidence if best else 0.0,
                  best.kind if best else "", best.detail if best else "")
        r = render_job(job)
        if a.out and r["status"] == "ok":
            import shutil
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_path("mesh", a.one), a.out)
            r["copied_to"] = a.out
        print(json.dumps(r, indent=1))
        return 0 if r["status"] != "failed" else 1

    if not (a.all or a.textures):
        ap.print_help()
        return 1

    jobs_n = a.jobs or max(1, (os.cpu_count() or 2) - 1)
    manifest = load_manifest()
    if a.no_resume:
        manifest["entries"] = {}
    entries = manifest["entries"]

    manifest.update({
        "schema": 1,
        "renderer": {"version": RENDERER_VERSION, "tool": "tools/thumbs.py",
                     "size": a.size, "supersample": a.ss, "shade": a.shade,
                     "frame": a.frame, "yaw": YAW, "pitch": PITCH,
                     "fov_deg": FOV_DEG,
                     "background": "transparent" if bg is None else bg,
                     "texture_size": a.tex_size,
                     "texture_palettised": not a.tex_truecolor},
    })

    report: dict = {}
    mesh_jobs: list[Job] = []
    unmatched: list[str] = []
    if a.all:
        mesh_jobs, unmatched = (server_worklist(view) if view
                                else load_worklist())
        for j in mesh_jobs:
            j.old_key = (entries.get(j.logical) or {}).get("key", "")
        if a.limit:
            mesh_jobs = mesh_jobs[:a.limit]

    tex_jobs: list[Job] = []
    if a.textures:
        used = {j.texture for j in mesh_jobs if j.texture}
        if not used:
            wl, _ = server_worklist(view) if view else load_worklist()
            used = {j.texture for j in wl if j.texture}
        for t in (server_texture_universe(view) if view
                  else texture_universe()):
            if a.only_unmatched_textures and t in used:
                continue
            tex_jobs.append(Job(t, "texture",
                                old_key=(entries.get(t) or {}).get("key", "")))
        if a.limit:
            tex_jobs = tex_jobs[:a.limit]

    if a.dry_run:
        print(f"mesh thumbnails to consider : {len(mesh_jobs)}")
        print(f"  of which already in manifest with a key: "
              f"{sum(1 for j in mesh_jobs if j.old_key)}")
        print(f"  meshes with no texture match (skipped)  : {len(unmatched)}")
        print(f"texture thumbnails to consider: {len(tex_jobs)}")
        # bytes per output pixel, measured over the full corpus:
        # mesh RGBA 0.365, texture palettised 0.33, texture truecolour 1.27
        est_mesh = len(mesh_jobs) * (a.size * a.size * 0.365)
        est_tex = len(tex_jobs) * (a.tex_size * a.tex_size *
                                   (1.27 if a.tex_truecolor else 0.33))
        print(f"estimated disk: meshes {est_mesh/1e6:.0f} MB, "
              f"textures {est_tex/1e6:.0f} MB, "
              f"total {(est_mesh+est_tex)/1e6:.0f} MB")
        print(f"jobs={jobs_n} size={a.size} ss={a.ss} shade={a.shade} bg={a.bg}")
        return 0

    if mesh_jobs:
        report["meshes"] = run(mesh_jobs, opts, a.root, jobs_n, manifest, "meshes  ")
        report["meshes"]["unmatched_meshes"] = len(unmatched)
    if tex_jobs:
        report["textures"] = run(tex_jobs, opts, a.root, jobs_n, manifest, "textures")

    by_logical = {j.logical: j for j in mesh_jobs}
    used_textures = {j.texture for j in mesh_jobs if j.texture}
    for logical, e in entries.items():
        j = by_logical.get(logical)
        if j is not None:
            e.update({"texture": j.texture, "method": j.method,
                      "confidence": j.confidence, "match_kind": j.match_kind,
                      "detail": j.detail})
        elif e.get("kind") == "texture" and used_textures:
            # tells a browser which of these are skins for a model in the mesh
            # set and which are the map art / icons / UI that no mesh claims
            e["used_by_mesh"] = logical in used_textures

    if unmatched:
        manifest["unmatched_meshes"] = sorted(unmatched)
    manifest["counts"] = {
        "entries": len(entries),
        "meshes": sum(1 for e in entries.values() if e.get("kind") == "mesh"),
        "textures": sum(1 for e in entries.values() if e.get("kind") == "texture"),
        "low_coverage": sum(1 for e in entries.values() if e.get("low_coverage")),
        "fallback_rendered": sum(1 for e in entries.values() if e.get("fallbacks")),
        "unmatched_meshes": len(unmatched),
        "bytes": sum(int(e.get("bytes", 0) or 0) for e in entries.values()),
    }
    manifest["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_manifest(manifest)

    print()
    for name, t in report.items():
        print(f"{name}: ok {t['ok']}  cached {t['cached']}  failed {t['failed']}  "
              f"{t['bytes']/1e6:.1f} MB  {t['seconds']}s")
        for logical, err in t["failures"][:20]:
            print(f"    FAIL {logical}: {err}")
        if len(t["failures"]) > 20:
            print(f"    ... {len(t['failures']) - 20} more")
    c = manifest["counts"]
    print(f"manifest: {MANIFEST}  ({c['entries']} entries, "
          f"{c['bytes']/1e6:.1f} MB of PNG)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
