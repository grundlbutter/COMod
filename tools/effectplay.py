#!/usr/bin/env python3
r"""effectplay.py -- 3DEffect.ini names resolved to something a renderer can play.

`tools/effects.py` (task #13) decoded the formats; this module is the bridge
between it and the viewer: it turns an effect *name* -- a `3DEffect.ini` section
key such as `Flash4102`, `m-b02` or `410009` -- into a JSON-serialisable scene
containing real geometry, real motion tracks and real timing.

**It implements `docs/effects.md` §8 and nothing else.** Every non-obvious step
below cites the section of that document (and through it the RVA) that says so.

Three things live here:

1. `render_matrix()` -- the coordinate conversion for a *matrix*.
   The rest of the project converts *positions* with `(x, y, -z)`
   (docs/modding.md §9.7). A matrix has to be conjugated, not negated:

       p_render = S * p_c            with S = diag(1, 1, -1)
       p_c' = p_c . M                (row-vector, docs/effects.md §6.2)
       => R = S . transpose(M) . S   as a column-vector GL matrix

   Written out in flat array indices this is startlingly simple: the GL
   column-major array is the *same 16 numbers* as the C3 row-major array with
   the sign flipped on exactly those entries where precisely one of (row, col)
   is 2. `parts.moti_sockets()` arrives at the same matrix the long way round by
   evaluating the transform on basis vectors; `test_viewer.py` asserts the two
   agree, so this shortcut is checked rather than asserted.

2. `EffectScene` -- one effect, fully resolved: layers -> parts -> geometry +
   `MOTI` keys + `C3Key` channels + flipbook grid + UV scroll + blend state.
   Timing uses the **alpha envelope**, not the declared track length
   (docs/effects.md §6.5): meshes routinely declare 101 frames and fade out
   after ten, so the declared length would play a 363 ms spark for 3.3 s.

3. `weapon_motion()` -- `ini/WeaponMotion.ini`, the per-action mesh swap
   (docs/effects.md §4.5). A weapon does not deform; it is *replaced* by a
   different mesh for each action, so an attack trail animating against a static
   weapon is wrong.

Nothing here writes anything, and nothing here touches the game install except
to read.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT, AssetRoot          # noqa: E402

try:                                                   # task #13 owns this
    import effects as effectsmod                       # noqa: E402
except Exception:                                      # pragma: no cover
    effectsmod = None


#: `Phy_Load` stores the ribbon segment count; `Shape_SetSegment` (RVA 0x5D8C0)
#: caps the live ribbon at `min(segments * 5, 800)` segment pairs.
RIBBON_SUBDIV = 5
RIBBON_MAX_SEGMENTS = 800

#: Actions `ini/WeaponMotion.ini` keys on, plus the `999` default.
WEAPON_ACTIONS = ("999", "300", "305", "310", "320", "330", "331", "340",
                  "341", "401", "402", "403")

#: The three attack swings -- the only actions that carry a trail (§2.2).
ATTACK_ACTIONS = ("401", "402", "403")

#: D3DBLEND -> the WebGL blend factor name. `blend_name()` in effects.py gives
#: the D3D spelling; this is the GL constant the viewport needs. VERIFIED as a
#: mapping of the enum, not of the engine's state block -- docs/effects.md §7
#: establishes ASB/ADB are D3DBLEND, and these are the GL equivalents.
D3DBLEND_TO_GL = {
    1: "ZERO", 2: "ONE",
    3: "SRC_COLOR", 4: "ONE_MINUS_SRC_COLOR",
    5: "SRC_ALPHA", 6: "ONE_MINUS_SRC_ALPHA",
    7: "DST_ALPHA", 8: "ONE_MINUS_DST_ALPHA",
    9: "DST_COLOR", 10: "ONE_MINUS_DST_COLOR",
    11: "SRC_ALPHA_SATURATE",
    # 12/13 (BOTHSRCALPHA / BOTHINVSRCALPHA) are legacy fixed-function forms
    # with no single GL factor; the caller falls back to ordinary alpha.
    12: "SRC_ALPHA", 13: "ONE_MINUS_SRC_ALPHA",
    14: "CONSTANT_COLOR", 15: "ONE_MINUS_CONSTANT_COLOR",
}


def gl_blend(v: int) -> str:
    return D3DBLEND_TO_GL.get(int(v or 0), "ONE")


# ---------------------------------------------------------------------------
# coordinate conversion for matrices
# ---------------------------------------------------------------------------

#: Flat-array indices whose sign flips when a C3 row-vector matrix is converted
#: to a render-space column-major GL matrix: exactly those where precisely one
#: of (row, col) equals 2. Derived, not tabulated by hand -- see below.
_FLIP = tuple(i for i in range(16)
              if ((i % 4) == 2) != ((i // 4) == 2))


def render_matrix(m) -> list:
    """A C3 4x4 (row-vector, row-major) -> a render-space GL 4x4 (column-major).

    See the module docstring for the derivation. Because
    ``glm[c*4+r] = s(r) * M[c*4+r] * s(c)`` and the C3 array is row-major with
    the same flat index, the transpose and the two mirrors collapse into a sign
    flip on six entries.

    >>> render_matrix([1,0,0,0, 0,1,0,0, 0,0,1,0, 1.6,1.2,-164.4,1])[14]
    164.4
    """
    if not m or len(m) != 16:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    out = [float(v) for v in m]
    for i in _FLIP:
        out[i] = -out[i]
    return out


def decompose(glm) -> dict:
    """Translation / per-axis scale / rotation of a column-major GL 4x4.

    Deliberately *not* a full polar decomposition: the point is to be able to
    check the three components of an attachment transform independently, which
    is exactly the failure mode this project has already hit twice (a
    double-applied chunk matrix producing a 438-unit offset, and a skipped one
    turning a 170.5 x 159.5 body into 52.8 x 361.3).
    """
    cols = [glm[0:3], glm[4:7], glm[8:11]]
    scale = [ (c[0] ** 2 + c[1] ** 2 + c[2] ** 2) ** 0.5 for c in cols ]
    det = (cols[0][0] * (cols[1][1] * cols[2][2] - cols[1][2] * cols[2][1])
           - cols[1][0] * (cols[0][1] * cols[2][2] - cols[0][2] * cols[2][1])
           + cols[2][0] * (cols[0][1] * cols[1][2] - cols[0][2] * cols[1][1]))
    rot = []
    for c, s in zip(cols, scale):
        rot.append([v / s for v in c] if s > 1e-9 else list(c))
    return {
        "translate": [glm[12], glm[13], glm[14]],
        "scale": scale,
        "uniformScale": (max(scale) - min(scale)) < 1e-4 * max(1.0, max(scale)),
        "rotation": rot,
        "determinant": det,
        "mirrored": det < 0,
    }


def mat_mul_gl(a, b) -> list:
    """Column-major 4x4 product ``a * b`` (apply b first, then a)."""
    o = [0.0] * 16
    for c in range(4):
        for r in range(4):
            o[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return o


GL_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


# ---------------------------------------------------------------------------
# the effect scene
# ---------------------------------------------------------------------------

@dataclass
class EffectScene:
    name: str
    found: bool = False
    error: str = ""
    payload: dict = field(default_factory=dict)


def _motion_json(motion, bones_used: Optional[set] = None, *, ndigits: int = 5,
                 last_frame: Optional[int] = None) -> dict:
    """A `MOTI` track as keys the client can interpolate itself.

    Keys rather than baked frames: `Motion_GetMatrix` (RVA 0x551A0) clamps to
    the first/last key and element-wise lerps between the bracketing pair
    (docs/effects.md §6.2), which is ten lines of JS. Sending keys keeps a
    `KKEY`/`ZKEY` track compact instead of expanding it to its frame count.

    Only the bones the mesh actually references are emitted; effect parts are
    one-bone quads, so this is normally 1 of 1, but a body's 84-bone track would
    otherwise be 84x larger than anything needs.
    """
    if motion is None:
        return {}
    n = motion.bone_count
    keep = sorted(b for b in (bones_used or range(n)) if 0 <= b < n)
    if not keep:
        keep = [0] if n else []
    idx = {b: i for i, b in enumerate(keep)}
    # Keys past the end of the alpha envelope are never sampled: playback stops
    # at `effective_frames` (docs/effects.md §6.5) and `Motion_GetMatrix` clamps
    # to the last key anyway. A RAW track stores one key per frame, so for an
    # 11-frame spark declaring 101 frames this drops 90 % of the payload.
    src = motion.keys
    if last_frame is not None and len(src) > 2:
        trimmed = [k for k in src if k.frame <= last_frame]
        if len(trimmed) >= 2:
            src = trimmed
    keys = []
    for k in src:
        row = []
        for b in keep:
            row.extend(round(v, ndigits) for v in render_matrix(k.matrices[b]))
        keys.append({"frame": k.frame, "m": row})
    return {
        "boneCount": n,
        "bones": keep,
        "boneIndex": {str(b): i for b, i in idx.items()},
        "frameCount": motion.frame_count,
        "encoding": motion.encoding,
        "keys": keys,
        "keysTrimmedTo": last_frame if len(src) != len(motion.keys) else None,
        "exact": motion.exact,
    }


def _keys_json(frames) -> list:
    return [{"frame": k.frame, "f": round(k.f_param, 6),
             "b": k.b_param, "n": k.n_param} for k in frames]


class EffectPlayer:
    """Resolves effect names to playable scenes. One instance per server.

    `EffectDB` costs ~0.25 s to build and `load_geometry` a few ms per effect,
    so the database is built once, lazily, and geometry is cached per name.
    """

    def __init__(self, root: Path = DEFAULT_ROOT,
                 mesh_to_json: Optional[Callable] = None,
                 assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        self._assets = assets
        self._db = None
        self._db_error = ""
        self._cache: dict[str, EffectScene] = {}
        self._mesh_to_json = mesh_to_json
        self._weapon_motion: Optional[dict[str, str]] = None

    # -- the database ------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.db is not None

    @property
    def db(self):
        if self._db is None and not self._db_error:
            if effectsmod is None:
                self._db_error = "tools/effects.py is not importable"
                return None
            try:
                self._db = effectsmod.EffectDB(self.root, assets=self._assets)
            except Exception as exc:                       # pragma: no cover
                self._db_error = f"EffectDB failed to load: {exc}"
        return self._db

    def names(self) -> list[str]:
        db = self.db
        return sorted(db.effects) if db else []

    # -- name -> scene -----------------------------------------------------
    def scene(self, name: str) -> EffectScene:
        if not name:
            return EffectScene("", False, "no effect name given")
        hit = self._cache.get(name)
        if hit is not None:
            return hit
        sc = self._build(name)
        self._cache[name] = sc
        return sc

    def _build(self, name: str) -> EffectScene:
        db = self.db
        if db is None:
            return EffectScene(name, False, self._db_error)
        try:
            eff = db.resolve(name)
        except Exception as exc:                           # pragma: no cover
            return EffectScene(name, False, f"resolve failed: {exc}")
        if eff is None:
            return EffectScene(name, False,
                               f"{name!r} is not a section of ini/3DEffect.ini")

        layers = []
        playable_parts = 0
        particle_parts = 0
        max_eff_frames = 0
        max_frames = 0
        for lay in eff.layers:
            obj = None
            load_error = ""
            if lay.mesh_path:
                try:
                    obj = effectsmod.load_effect_object(db.assets, lay.effect_id,
                                                        lay.mesh_path)
                except Exception as exc:                   # pragma: no cover
                    obj, load_error = None, str(exc)
            else:
                load_error = f"EffectId {lay.effect_id!r} is not in 3DEffectObj.ini"
            parts = []
            if obj is not None:
                for p in obj.parts:
                    j = self._part_json(p)
                    if j is None:
                        continue
                    if j["kind"] == "particle":
                        particle_parts += 1
                    else:
                        playable_parts += 1
                        max_eff_frames = max(max_eff_frames, j.get("effectiveFrames") or 0)
                        max_frames = max(max_frames, j.get("frameCount") or 0)
                    parts.append(j)
            layers.append({
                "index": lay.index,
                "effectId": lay.effect_id,
                "textureId": lay.texture_id,
                "mesh": (lay.mesh_path or "").replace("\\", "/"),
                "meshFound": bool(lay.mesh_found),
                "texture": (lay.texture_path or "").replace("\\", "/"),
                "textureFound": bool(lay.texture_found),
                "srcBlend": lay.asb, "dstBlend": lay.adb,
                "srcBlendName": effectsmod.blend_name(lay.asb),
                "dstBlendName": effectsmod.blend_name(lay.adb),
                "glSrcBlend": gl_blend(lay.asb), "glDstBlend": gl_blend(lay.adb),
                "additive": int(lay.adb) == 2,
                "scale": lay.scale,
                "zbuffer": lay.zbuffer, "ztest": lay.ztest,
                "error": (obj.error if obj is not None else load_error),
                "parts": parts,
            })

        frames = max_frames or 0
        effective = max_eff_frames or frames
        duration = (effective * eff.frame_interval) if effective else None
        payload = {
            "name": eff.name,
            "source": eff.source,
            "frameIntervalMs": eff.frame_interval,
            "fps": round(eff.fps, 3),
            "loopTime": eff.loop_time,
            "endless": eff.endless,
            "loopIntervalMs": eff.loop_interval,
            "delayMs": eff.delay,
            "offset": list(eff.offset),
            # The effect offset is authored in C3 space like everything else.
            "offsetRender": [eff.offset[0], eff.offset[1], -eff.offset[2]],
            "colorEnable": eff.color_enable,
            "frames": frames,
            "effectiveFrames": effective,
            "durationMs": duration,
            "playableParts": playable_parts,
            "particleParts": particle_parts,
            "layers": layers,
            "timingNote": (
                "Length comes from the alpha envelope, not from the declared "
                "MOTI frame count: effect meshes habitually declare 101 frames "
                "and fade out after ten (docs/effects.md §6.5). INFERRED."),
        }
        return EffectScene(name, True, "", payload)

    def _part_json(self, p) -> Optional[dict]:
        if p.kind == effectsmod.PART_PARTICLE:
            return {"kind": "particle", "name": p.name, "rawSize": p.raw_size,
                    "note": "PTCL/PTC3 particle systems are not decoded "
                            "(docs/effects.md §6.6) — this layer is skipped, "
                            "not faked."}
        if p.kind == effectsmod.PART_SHAPE:
            if p.shape is None:
                return None
            line = [[x, y, -z] for (x, y, z) in p.shape.line]
            smot = p.smotion
            return {
                "kind": "shape",
                "name": p.name,
                "line": line,
                "segments": p.shape.segments,
                # Shape_SetSegment, RVA 0x5D8C0
                "maxPairs": min(p.shape.segments * RIBBON_SUBDIV,
                                RIBBON_MAX_SEGMENTS) + 1,
                "subdiv": RIBBON_SUBDIV,
                "frameCount": smot.frame_count if smot else 0,
                "effectiveFrames": smot.frame_count if smot else 0,
                "smot": [[round(v, 5) for v in render_matrix(m)]
                         for m in (smot.matrices if smot else [])],
            }
        # phy_motion
        if p.mesh is None:
            return None
        if self._mesh_to_json is None:                     # pragma: no cover
            return None
        geo = self._mesh_to_json(p.mesh, 0)
        bones_used = {v.bone0 for v in p.mesh.vertices}
        bones_used |= {v.bone1 for v in p.mesh.vertices if v.weight1}
        step = p.uv_step
        return {
            "kind": "phy",
            "name": p.name,
            "geometry": geo,
            # docs/effects.md §6.4: the flipbook is N x N where N is the PHY's
            # own frameCount field at C3Phy+0x190, and the ChangeTex key value
            # is the cell index, row-major.
            "uvGrid": p.uv_grid,
            "uvStep": list(step) if step else [0.0, 0.0],
            "frameCount": p.frame_count,
            "effectiveFrames": p.effective_frames,
            "alphaEnd": p.alpha_end,
            "motion": _motion_json(p.motion, bones_used,
                                   last_frame=p.effective_frames),
            "keys": {
                "alphas": _keys_json(p.alpha_keys),
                "draws": _keys_json(p.draw_keys),
                "changeTexs": _keys_json(p.tex_keys),
            },
        }

    # -- ini/WeaponMotion.ini ---------------------------------------------
    def weapon_motion(self) -> dict[str, str]:
        """The raw table, keyed ``<appearance><action>``.

        VERIFIED (docs/effects.md §4.5): 1,863 flat rows, 100 % of the
        appearance prefixes are literal `weapon.ini` section names and 100 % of
        the mesh paths resolve.
        """
        if self._weapon_motion is not None:
            return self._weapon_motion
        db = self.db
        out: dict[str, str] = {}
        if db is not None and getattr(db, "weapon_motion", None):
            out = {k: str(v).replace("\\", "/").lower()
                   for k, v in db.weapon_motion.items()}
        else:                                              # pragma: no cover
            p = self.root / "ini" / "WeaponMotion.ini"
            if p.is_file():
                for line in p.read_text("latin-1", errors="replace").splitlines():
                    line = line.strip()
                    if not line or "=" not in line or line[0] in ";#[":
                        continue
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().replace("\\", "/").lower()
        self._weapon_motion = out
        return out

    def weapon_meshes(self, appearance: str,
                      default_mesh: str = "") -> dict:
        """Every per-action mesh for one weapon appearance.

        The lookup order is docs/effects.md §8 step 3, verbatim::

            WeaponMotion[A + action]  ->  WeaponMotion[A + "999"]
                                      ->  weapon.ini[A].Mesh0

        `default_mesh` is the caller's already-resolved `Mesh0`, passed in so
        this module does not need a second copy of the appearance tables.
        """
        wm = self.weapon_motion()
        ident = str(appearance or "")
        forms = [ident, ident.lstrip("0")]
        base = ""
        for f in forms:
            if not f:
                continue
            base = wm.get(f + "999", "")
            if base:
                break
        base = base or (default_mesh or "")
        actions = {}
        for act in WEAPON_ACTIONS:
            hit = ""
            for f in forms:
                if not f:
                    continue
                hit = wm.get(f + act, "")
                if hit:
                    break
            if not hit:
                continue
            actions[act] = {"mesh": hit, "source": "WeaponMotion.ini"}
        out = {
            "appearance": ident,
            "default": base,
            "defaultSource": ("WeaponMotion.ini 999" if wm.get(ident + "999")
                              or wm.get(ident.lstrip("0") + "999")
                              else ("weapon.ini Mesh0" if default_mesh else "none")),
            "actions": actions,
            "attackActions": [a for a in ATTACK_ACTIONS if a in actions],
            "swaps": sorted({v["mesh"] for v in actions.values()} - {base}),
        }
        return out


# ---------------------------------------------------------------------------
# playback -- the reference implementation of docs/effects.md §8
# ---------------------------------------------------------------------------
#
# `tools/webui/fx.js` is a line-for-line mirror of the four functions below.
# They live here as well as there so the algorithm is covered by
# `tools/test_viewer.py` against real assets rather than only by looking at the
# viewport, which is exactly the "looks subtly off" failure mode this project
# keeps trying to avoid.


def frame_at(elapsed_ms: float, *, frames: int, frame_interval_ms: int,
             loop_time: int, loop_interval_ms: int = 0, delay_ms: int = 0,
             endless: bool = False) -> dict:
    """Where an effect instance is `elapsed_ms` after it spawned.

    docs/effects.md §8, verbatim::

        t     = elapsed - Delay
        cycle = frames * FrameInterval + LoopInterval
        loop  = t / cycle
        if LoopTime < 99999 and loop >= LoopTime: despawn
        frame = (t % cycle) / FrameInterval        # >= frames -> in the gap

    `frames` is the **effective** length (the alpha envelope, §6.5), not the
    declared `MOTI` frame count -- that is the whole point of §6.5 and the
    difference between a 363 ms spark and a 3.3 s one.
    """
    fi = max(1, int(frame_interval_ms or 1))
    n = max(1, int(frames or 1))
    if elapsed_ms < delay_ms:
        return {"waiting": True, "done": False, "frame": 0, "loop": 0, "gap": True}
    t = elapsed_ms - delay_ms
    cycle = n * fi + max(0, int(loop_interval_ms or 0))
    loop = int(t // cycle)
    if not endless and loop >= max(1, int(loop_time or 1)):
        return {"waiting": False, "done": True, "frame": n - 1, "loop": loop,
                "gap": False}
    frame = int((t % cycle) // fi)
    return {"waiting": False, "done": False, "frame": min(frame, n - 1),
            "loop": loop, "gap": frame >= n}


def sample_part(part: dict, frame: int) -> dict:
    """Alpha / visibility / UV offset for one `PHY`+`MOTI` part at `frame`.

    The three `C3Key` channels behave differently and the difference matters
    (docs/effects.md §6.4): alpha is **lerped** between bracketing keys, draw is
    an **exact frame match only**, changeTex is a **step function**.

    `part` is the JSON this module emits, so the JS mirror consumes the same
    shape.
    """
    keys = part.get("keys") or {}
    alphas = keys.get("alphas") or []
    draws = keys.get("draws") or []
    texs = keys.get("changeTexs") or []

    # -- draw: exact match only; no key at this frame means "unchanged", which
    #    at spawn time is visible.
    visible = True
    for k in draws:
        if k["frame"] == frame:
            visible = bool(k["b"])
            break

    # -- alpha: lerp between bracketing keys, clamped at both ends
    alpha = 1.0
    if alphas:
        prev = None
        nxt = None
        for k in alphas:
            if k["frame"] <= frame and (prev is None or k["frame"] > prev["frame"]):
                prev = k
            if k["frame"] > frame and (nxt is None or k["frame"] < nxt["frame"]):
                nxt = k
        if prev is None:
            alpha = nxt["f"]
        elif nxt is None:
            alpha = prev["f"]
        else:
            span = nxt["frame"] - prev["frame"]
            t = 0.0 if span == 0 else (frame - prev["frame"]) / span
            alpha = prev["f"] + (nxt["f"] - prev["f"]) * t
        alpha = max(0.0, min(1.0, alpha))

    # -- changeTex: step. Mirrors effects.key_change_tex exactly, including its
    #    "no key past this frame -> no cell" tail behaviour.
    cell = None
    for i, k in enumerate(texs):
        if k["frame"] == frame:
            cell = k["n"]
            break
        if i and frame < k["frame"]:
            cell = texs[i - 1]["n"]
            break

    if cell is not None and cell >= 0:
        n = max(1, int(part.get("uvGrid") or 1))
        # The engine adds an OFFSET and does not scale: the quad's own UVs
        # already span one cell (Plane02 of m-b02 is authored 0..0.5 with N=2).
        uv = [(cell % n) / n, (cell // n) / n]
    else:
        step = part.get("uvStep") or [0.0, 0.0]
        uv = [frame * step[0], frame * step[1]]
    # graphic.dll wraps both offsets into [-1.1, 1.1]
    uv = [_wrap11(uv[0]), _wrap11(uv[1])]
    return {"visible": visible, "alpha": alpha, "cell": cell, "uv": uv}


def _wrap11(v: float) -> float:
    import math
    while v > 1.1:
        v -= math.ceil(v)
    while v < -1.1:
        v -= math.floor(v)
    return v


def motion_matrix(motion: dict, bone: int, frame: int) -> list:
    """`Motion_GetMatrix` (RVA 0x551A0) over the JSON emitted by `_motion_json`.

    Clamp to the first/last key, otherwise element-wise lerp all 16 elements
    between the bracketing pair. Element-wise, not slerp — that is literally
    what the engine does, because `ZKEY` quaternions were baked at load time.
    """
    keys = (motion or {}).get("keys") or []
    if not keys:
        return list(GL_IDENTITY)
    bones = motion.get("bones") or [0]
    try:
        bi = bones.index(bone)
    except ValueError:
        bi = 0
    def at(k):
        return k["m"][bi * 16:bi * 16 + 16]
    if frame <= keys[0]["frame"]:
        return list(at(keys[0]))
    if frame >= keys[-1]["frame"]:
        return list(at(keys[-1]))
    for i in range(1, len(keys)):
        if frame < keys[i]["frame"]:
            a, b = keys[i - 1], keys[i]
            span = b["frame"] - a["frame"]
            t = 0.0 if span == 0 else (frame - a["frame"]) / span
            ma, mb = at(a), at(b)
            return [ma[k] + (mb[k] - ma[k]) * t for k in range(16)]
    return list(at(keys[-1]))


def ribbon_advance(line, world, history, *, max_pairs: int,
                   subdiv: int = RIBBON_SUBDIV) -> list:
    """Smear a two-point blade line into a ribbon as its parent moves.

    `Shape_Draw` (RVA 0x5CF60) uses `frames[0]` and only its first two points,
    transforms them by `SMOT[frame] x world`, then pushes `subdiv` linearly
    interpolated pairs between the previous transformed line and the new one.
    Old pairs fall off the end at `min(segments * 5, 800) + 1`.

    `history` is the caller's rolling list and is mutated in place, exactly as
    `effects.shape_ribbon` does.
    """
    a = _xform(world, line[0])
    b = _xform(world, line[1])
    if not history:
        history.append((a, b))
    else:
        pa, pb = history[-1]
        for s in range(1, subdiv + 1):
            t = s / subdiv
            history.append((
                tuple(pa[i] + (a[i] - pa[i]) * t for i in range(3)),
                tuple(pb[i] + (b[i] - pb[i]) * t for i in range(3)),
            ))
    while len(history) > max_pairs:
        history.pop(0)
    return history


def _xform(glm, p):
    """Column-major GL 4x4 applied to a column vector."""
    x, y, z = p
    return (glm[0] * x + glm[4] * y + glm[8] * z + glm[12],
            glm[1] * x + glm[5] * y + glm[9] * z + glm[13],
            glm[2] * x + glm[6] * y + glm[10] * z + glm[14])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--effect", help="resolve one 3DEffect.ini name to a scene")
    ap.add_argument("--weapon", help="per-action meshes for one weapon appearance")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="how many effect names resolve to playable geometry")
    a = ap.parse_args(argv)

    import coviewer
    pl = EffectPlayer(Path(a.root), mesh_to_json=coviewer.mesh_to_json)

    if a.weapon:
        rec = pl.weapon_meshes(a.weapon)
        print(json.dumps(rec, indent=2) if a.json else
              f"{a.weapon}: default {rec['default']}\n" +
              "\n".join(f"  action {k}: {v['mesh']}"
                        for k, v in sorted(rec["actions"].items())))
        return 0

    if a.effect:
        sc = pl.scene(a.effect)
        if not sc.found:
            print(f"{a.effect}: {sc.error}")
            return 1
        if a.json:
            print(json.dumps(sc.payload, indent=1))
            return 0
        d = sc.payload
        print(f"{d['name']}  {d['frameIntervalMs']} ms/frame  "
              f"{d['frames']} frames declared, {d['effectiveFrames']} effective "
              f"({d['durationMs']} ms)  loop {d['loopTime']}"
              f"{' (endless)' if d['endless'] else ''}")
        for lay in d["layers"]:
            print(f"  layer {lay['index']}  {lay['mesh']}  tex {lay['texture']}  "
                  f"blend {lay['srcBlendName']}/{lay['dstBlendName']}")
            for p in lay["parts"]:
                if p["kind"] == "phy":
                    print(f"     phy   {p['name']:<10} v={p['geometry']['vertexCount']:<4} "
                          f"grid {p['uvGrid']}x{p['uvGrid']}  step {p['uvStep']}  "
                          f"frames {p['frameCount']}->{p['effectiveFrames']}  "
                          f"keys a/d/t "
                          f"{len(p['keys']['alphas'])}/{len(p['keys']['draws'])}/"
                          f"{len(p['keys']['changeTexs'])}")
                elif p["kind"] == "shape":
                    print(f"     shape {p['name']:<10} line {p['line']}  "
                          f"segments {p['segments']} -> {p['maxPairs']} pairs  "
                          f"smot {p['frameCount']}")
                else:
                    print(f"     {p['kind']} {p['name']} ({p['rawSize']} bytes, not decoded)")
        return 0

    if a.coverage:
        names = pl.names()
        ok = playable = particles = 0
        for n in names:
            sc = pl.scene(n)
            if not sc.found:
                continue
            ok += 1
            if sc.payload["playableParts"]:
                playable += 1
            if sc.payload["particleParts"]:
                particles += 1
        print(f"effect names        {len(names)}")
        print(f"  resolve           {ok}")
        print(f"  playable geometry {playable}")
        print(f"  needing particles {particles}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
