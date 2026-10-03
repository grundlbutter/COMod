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

import math
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT, AssetRoot          # noqa: E402
import coroot                                        # noqa: E402

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


#: What a `CAME` is and is not, said once so every surface says the same thing.
CAMERA_NOTE = (
    "This is the effect's OWN camera, baked into the .c3 by whoever authored "
    "it (a CAME chunk), not a framing we fitted. Eye and target are converted "
    "to render space with the project-wide (x, y, -z); the fov is the chunk's "
    "own, in radians. MEASURED over 13,935 CAME bodies -- the six installs "
    "4274/5065/5517/6090/6805/7632, NOT the corpus, which holds 79,522 on "
    "all 34 (corrected 2026-09-07): the eye track is constant in 13,935 of "
    "13,935 and the target track moves in 42, so a still camera is what "
    "those six installs ship. The other 65,587 bodies are unmeasured on "
    "this question."
)

#: An effect with no CAME gets a camera fitted to its bounds instead, and the
#: player must say which it is showing.
FITTED_NOTE = (
    "No layer of this effect carries a CAME chunk, so the view is FITTED to "
    "the geometry's bounding box. That framing is OURS. It is not wrong, but "
    "it is not what the artist chose either."
)


def _camera_json(cam, *, mesh: str, layer: int) -> dict:
    r"""One decoded `effects.Camera` in the render space the viewer uses.

    The whole conversion is the project-wide position rule `(x, y, -z)`
    (docs/modding.md §9.7) applied to both tracks. There is no matrix here to
    conjugate -- `render_matrix` exists because a *matrix* has to be
    `S . M^T . S` and a *point* does not, and using the matrix rule on a point
    is the mistake this function is written out longhand to avoid.

    `static` is the file's own answer, not a tolerance we picked: `Camera.static`
    is exact set equality over the tracks.
    """
    eye = [[x, y, -z] for (x, y, z) in cam.eye]
    tgt = [[x, y, -z] for (x, y, z) in cam.target]
    # A still camera's tracks are 101 copies of one point. Sending them would
    # add ~5 KB to every scene response to say nothing, so a static camera
    # sends `eye0`/`target0` only and `eye`/`target` come back null -- the
    # consumer reads `static` and does not index. This is a TRANSPORT choice,
    # not a decode one: `Camera.static` is exact set equality over the decoded
    # tracks, so nothing that varies can take this branch.
    still = cam.static
    return {
        "name": cam.name,
        "mesh": mesh,
        "layer": layer,
        "fov": cam.fov,
        "fovDegrees": round(cam.fov_degrees, 4),
        "frameCount": cam.frame_count,
        "static": cam.static,
        "exact": cam.exact,
        # The whole track, so a scrubber can follow a panning camera -- but
        # only for the 42 of 13,935 that actually move.
        "eye": None if still else eye,
        "target": None if still else tgt,
        # The single sample a still camera reduces to, so the common consumer
        # never has to index a track.
        "eye0": eye[0] if eye else None,
        "target0": tgt[0] if tgt else None,
        "note": CAMERA_NOTE,
    }


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
        # Keyed on (name, map_fx): the same name resolves DIFFERENTLY under
        # the two populations -- `horse_grid` is absent from `3DEffect.ini`
        # on 7878 and present-with-0-layers in `c3.wdb` -- so a name-only
        # key would serve whichever was asked for first.
        self._cache: dict[tuple[str, bool], EffectScene] = {}
        self._mesh_to_json = mesh_to_json
        self._weapon_motion: Optional[dict[str, str]] = None
        # -- the animation-form index (see `form_index_now`) ---------------
        self._forms: Optional[dict[str, list[str]]] = None
        self._forms_lock = threading.Lock()
        self._forms_thread: Optional[threading.Thread] = None
        self._forms_started: float = 0.0
        self._forms_finished: float = 0.0
        self._forms_progress: dict = {"done": 0, "total": 0}
        self._forms_error: str = ""
        # -- the breakage census (see `census_now`) -------------------------
        self._census: Optional[dict] = None
        self._census_states: Optional[dict[str, str]] = None
        self._census_lock = threading.Lock()
        self._census_thread: Optional[threading.Thread] = None
        self._census_started: float = 0.0
        self._census_finished: float = 0.0
        self._census_progress: dict = {"done": 0, "total": 0}
        self._census_error: str = ""

    # -- which of the three animation forms each effect carries -------------
    #
    # The classification is `tools/effects.py`'s and is not repeated here.
    # What is here is the only thing the web UI needs that the CLI does not:
    # a way to have the answer WITHOUT blocking the page for it.
    #
    # The index costs one read of every container the effect tables name.
    # MEASURED 2026-09-07, cold: 5017 12.5 s / 1,791 containers, 5517 13.0 s /
    # 4,584, 6090 25.7 s / 7,350, 6609 107.5 s / 10,390, 7205 89.6 s /
    # 10,390, 7878 16.9 s / 2,380.  A synchronous endpoint would hold a
    # request open for a minute and a half on 6609, so this follows the same
    # background-build shape `coviewer.Catalog.unified_now` already uses:
    # start it, serve what exists, and report the state so a page can say
    # *building* rather than *this client has no ribbons*.

    def _build_form_index(self) -> None:
        db = self.db
        out: dict[str, list[str]] = {}
        try:
            if db is None:
                raise RuntimeError(self._db_error or "no effect table")
            names = sorted(db.effects)
            self._forms_progress = {"done": 0, "total": len(names)}
            for i, n in enumerate(names, 1):
                rec = db.forms_for(n)
                out[n] = list(rec["forms"]) if rec else []
                if i % 50 == 0 or i == len(names):
                    self._forms_progress = {"done": i, "total": len(names)}
        except Exception as exc:                           # pragma: no cover
            # A failed build must publish SOMETHING, or every later reader
            # waits on a state that will never change. Same rule as
            # `Catalog._build_unified`.
            self._forms_error = f"{type(exc).__name__}: {exc}"
            out = {}
        with self._forms_lock:
            self._forms = out
            self._forms_finished = time.time()

    def _start_form_index(self) -> None:
        """Ensure exactly one build is running or finished. Idempotent."""
        with self._forms_lock:
            if self._forms is not None or self._forms_thread is not None:
                return
            self._forms_started = time.time()
            self._forms_thread = threading.Thread(
                target=self._build_form_index, daemon=True,
                name="effect-form-index")
            self._forms_thread.start()

    def form_index_now(self) -> dict[str, list[str]]:
        """effect name -> its form list, or ``{}`` while the build is in
        flight.  **Never blocks.**

        Empty is not "no effect carries a form": pair every use with
        `form_index_status`, whose ``state`` distinguishes *building* from
        *ready* from *failed*.  A page that cannot tell those apart shows an
        empty filter and reads as a fact about the client.
        """
        self._start_form_index()
        return self._forms if self._forms is not None else {}

    def form_index_status(self) -> dict:
        """``state`` is cold / building / ready / failed, with progress."""
        f = self._forms
        if f is not None:
            state = "failed" if self._forms_error else "ready"
        elif self._forms_thread is not None:
            state = "building"
        else:
            state = "cold"
        el = ((self._forms_finished or time.time()) - self._forms_started
              if self._forms_started else 0.0)
        counts = {}
        if effectsmod is not None:
            counts = {form: 0 for form in effectsmod.FORMS}
            for got in (f or {}).values():
                for form in got:
                    if form in counts:
                        counts[form] += 1
        return {
            "state": state,
            "error": self._forms_error,
            "effects": len(f) if f is not None else 0,
            # All three rows, always: an absent form is a measured zero.
            "byForm": counts,
            "multiForm": sum(1 for v in (f or {}).values() if len(v) > 1),
            "elapsedSeconds": round(el, 1),
            "progress": dict(self._forms_progress),
        }

    # -- how whole every effect on this base is, and the census that sums it -
    #
    # Same shape as the form index above and for the same reason: classifying
    # every effect means RESOLVING every effect, and that is a pass, not a
    # lookup.  MEASURED 2026-09-07, cold, this seat: **5517 10.1 s over 3,391
    # effects; 6609 33.7 s over 5,290** (plus the `EffectDB` build itself,
    # 1.3 s and 4.9 s).  Cheaper than the form index because it never opens a
    # container -- it is table lookups and an `assets.exists` per layer path
    # -- and still far too slow to serve inline.
    #
    # The classification is `effects.effect_state_index` and is not repeated
    # here; this adds only the background-build wrapper.  `coverage()` calls
    # the same classifier, so the census a page renders and the numbers
    # docs/effects.md quotes cannot drift apart.

    def _build_census(self) -> None:
        db = self.db
        got: Optional[dict] = None
        try:
            if db is None:
                raise RuntimeError(self._db_error or "no effect table")
            if effectsmod is None:                         # pragma: no cover
                raise RuntimeError("tools/effects.py is not importable")

            def report(done: int, total: int) -> None:
                self._census_progress = {"done": done, "total": total}

            got = effectsmod.effect_state_index(db, progress=report)
        except Exception as exc:                           # pragma: no cover
            # A failed build must publish SOMETHING or every later reader
            # waits on a state that never changes -- but what it publishes
            # must be distinguishable from a real answer, which is what
            # `_census_error` is for. `census_status` reports `failed` and
            # the states map stays EMPTY rather than becoming "nothing is
            # broken on this install".
            self._census_error = f"{type(exc).__name__}: {exc}"
            got = {"states": {}, "census": {}}
        with self._census_lock:
            self._census_states = dict(got.get("states") or {})
            self._census = dict(got.get("census") or {})
            self._census_finished = time.time()

    def _start_census(self) -> None:
        """Ensure exactly one census build is running or finished. Idempotent."""
        with self._census_lock:
            if self._census is not None or self._census_thread is not None:
                return
            self._census_started = time.time()
            self._census_thread = threading.Thread(
                target=self._build_census, daemon=True, name="effect-census")
            self._census_thread.start()

    def census_now(self) -> dict:
        """``{"states": {name: state}, "census": {...}}``, or empty while the
        build is in flight.  **Never blocks.**

        Empty is NOT "this client ships no broken effects" and it is NOT "no
        rule names an undefined effect".  Pair every use with
        `census_status`, whose ``state`` distinguishes *building* from *ready*
        from *failed*.  A consumer that renders this without checking that
        state publishes an unmeasured install as a clean one, which is the
        single worst thing this panel can do -- it is a claim of completeness
        made out of an absence of data.
        """
        self._start_census()
        with self._census_lock:
            return {"states": dict(self._census_states or {}),
                    "census": dict(self._census or {})}

    def census_status(self) -> dict:
        """``state`` is cold / building / ready / failed, with progress.

        **STARTS THE BUILD**, exactly as `census_now` does. A status call that
        only observed would let a caller which reads the state first and the
        data second -- the natural order, and the one the endpoint uses --
        poll a `cold` that nothing ever moves off. That is not a hang: it is a
        page saying "still building" forever with nothing building, which
        reads as slow rather than as broken. Measured here on 5517 before it
        shipped: the build never started and the poller never terminated.
        """
        self._start_census()
        c = self._census
        if c is not None:
            state = "failed" if self._census_error else "ready"
        elif self._census_thread is not None:
            state = "building"
        else:
            state = "cold"
        el = ((self._census_finished or time.time()) - self._census_started
              if self._census_started else 0.0)
        return {
            "state": state,
            "error": self._census_error,
            "classified": len(self._census_states or {}),
            "allStates": (list(effectsmod.EFFECT_STATES)
                          if effectsmod is not None else []),
            "elapsedSeconds": round(el, 1),
            "progress": dict(self._census_progress),
        }

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
    def scene(self, name: str, *, map_fx: bool = False) -> EffectScene:
        """One effect name resolved to playable geometry.

        `map_fx` opts in to the MAP effect population -- `c3.wdb`'s EFFE
        section -- and defaults OFF so every existing caller keeps the
        `3DEffect.ini` population it was measured against.  See
        `EffectDB.resolve` for the corpus measurement behind that default.
        """
        if not name:
            return EffectScene("", False, "no effect name given")
        key = (name, bool(map_fx))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        sc = self._build(name, map_fx=bool(map_fx))
        self._cache[key] = sc
        return sc

    def _build(self, name: str, *, map_fx: bool = False) -> EffectScene:
        db = self.db
        if db is None:
            return EffectScene(name, False, self._db_error)
        try:
            eff = db.resolve(name, map_fx=map_fx)
        except Exception as exc:                           # pragma: no cover
            return EffectScene(name, False, f"resolve failed: {exc}")
        if eff is None:
            # Name the file that was actually consulted, not the one the
            # plaintext lineage happens to use: on 5517/6090 that is
            # `3DEffect.dbc`, and "not a section of ini/3DEffect.ini" would
            # send the reader to a file the client ignores (C47).
            src = getattr(db.sources.get("3DEffect"), "path", None)
            where = f"ini/{src.name}" if src is not None else "ini/3DEffect.ini"
            if map_fx:
                # Two files were consulted, so naming one of them would send
                # the reader to look in a place that was already checked.
                where += " or ini/c3.wdb:EFFE"
            return EffectScene(name, False,
                               f"{name!r} is not defined in {where}")

        layers = []
        playable_parts = 0
        particle_parts = 0
        undecoded_parts = 0
        max_eff_frames = 0
        max_frames = 0
        cameras: list[dict] = []
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
                for cam in getattr(obj, "cameras", ()):
                    cameras.append(_camera_json(
                        cam, mesh=(lay.mesh_path or "").replace("\\", "/"),
                        layer=lay.index))
                for p in obj.parts:
                    j = self._part_json(p)
                    if j is None:
                        continue
                    if j["kind"] == "particle":
                        particle_parts += 1
                        if not j.get("decoded"):
                            undecoded_parts += 1
                            parts.append(j)
                            continue
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
            # Non-zero means this build met a chunk it cannot play. It stays a
            # separate number from `particleParts` on purpose: particles are no
            # longer a gap, so counting them as one would misreport the scene.
            "undecodedParts": undecoded_parts,
            # The authoring cameras this effect's own containers carry. Empty
            # is the honest answer for a bit under half the corpus and the
            # player says "fitted" rather than pretending otherwise --
            # MEASURED per install (effects whose layer meshes read):
            # 5165 1,807/2,569 (70.3%), 5517 1,890/3,339 (56.6%),
            # 6090 1,964/4,417 (44.5%). The trend is real: newer content ships
            # fewer cameras, which is consistent with nothing having consumed
            # one since 5165.
            "cameras": cameras,
            "cameraCount": len(cameras),
            "camera": cameras[0] if cameras else None,
            "cameraSource": "CAME" if cameras else "fitted",
            "cameraNote": CAMERA_NOTE if cameras else FITTED_NOTE,
            "layers": layers,
            "timingNote": (
                "Length comes from the alpha envelope, not from the declared "
                "MOTI frame count: effect meshes habitually declare 101 frames "
                "and fade out after ten (docs/effects.md §6.5). INFERRED."),
        }
        return EffectScene(name, True, "", payload)

    def _part_json(self, p) -> Optional[dict]:
        if p.kind == effectsmod.PART_PARTICLE:
            q = p.particle
            if q is None:
                return {"kind": "particle", "name": p.name,
                        "rawSize": p.raw_size, "decoded": False,
                        "frameCount": 0, "effectiveFrames": 0,
                        "note": "this particle chunk did not decode -- the "
                                "layer is skipped, not faked."}
            # The simulation is baked: one solved particle set per frame, so
            # there is nothing to integrate. Positions go to render space with
            # the project-wide (x, y, -z); the per-frame matrix goes through
            # `render_matrix` like every other C3 matrix.
            frames = []
            for f in q.frames:
                if not f.count:
                    frames.append({"n": 0})
                    continue
                pos = []
                for (x, y, z) in f.positions:
                    pos += [round(x, 4), round(y, 4), round(-z, 4)]
                frames.append({
                    "n": f.count,
                    "p": pos,
                    "c": [round(v, 5) for v in f.cells],
                    "s": [round(v, 4) for v in f.sizes],
                    "m": [round(v, 5) for v in render_matrix(list(f.matrix))],
                })
            out = {
                "kind": "particle",
                "name": p.name,                    # the chunk tag
                "objectName": q.name,
                "generation": q.generation,
                "rawSize": p.raw_size,
                "decoded": True,
                "frameCount": q.frame_count,
                "effectiveFrames": q.effective_frames,
                "atlas": q.tex_grid,               # ROWS, and the default cols
                # CCFL KIND 3: the atlas COLUMN count. 0 means square, which
                # is what `atlas` alone used to mean everywhere. `totalCells`
                # becomes cols*atlas, the modulus becomes cols, u steps by
                # 1/cols and V STILL STEPS BY 1/atlas -- a consumer that uses
                # one number for both axes is the defect this field exists to
                # remove. See `effects.Particle.cell`.
                "atlasCols": p.atlas_cols or 0,
                # CCFL kind 10: (off_u, off_v, ext_u, ext_v), [] when absent.
                # `particle_quads` and `fx.js particleQuads` apply it as the
                # traced hybrid (offset added to the cell origin, extent
                # multiplied into the cell size);
                # `tests/test_ccfl_kind10_applied.py` gates both.
                "uvRect": list(p.uv_rect) if p.uv_rect else [],
                "maxParticles": q.max_particles,
                "peakParticles": q.peak_particles,
                "vertsPerParticle": effectsmod.PTCL_VERTS_PER_PARTICLE,
                "frames": frames,
                "note": "Baked simulation: frames[i] is the solved particle "
                        "set for frame i (docs/effects.md §6.6). Draw one "
                        "camera-facing quad of half-size s per particle. UV "
                        "cell: K = atlasCols or atlas; "
                        "i = floor(c*K*atlas); col = i % K, row = i // K; "
                        "u steps 1/K and v steps 1/atlas.",
            }
            if q.envelope is not None:
                e = q.envelope
                out["envelope"] = {
                    "billboard": e.billboard,
                    "worldSpace": e.world_space,
                    "alpha": list(e.alpha),
                    "fadeFrames": list(e.fade_frames),
                    "particleAlpha": list(e.particle_alpha),
                    "particleLife": list(e.particle_life),
                    "roll": list(e.roll),
                }
                out["systemAlpha"] = [round(q.system_alpha(i), 5)
                                      for i in range(q.frame_count)]
            if q.stretch is not None:
                out["stretch"] = q.stretch
            # Same key and same shape a PHY part's geometry uses, so the
            # viewer's camera fit does not need to know which kind it got.
            # Without it a pure-particle effect has no bounds at all and the
            # view keeps whatever the last model left it at -- a 12-unit burst
            # in a frame sized for a 400-unit aura is an invisible dot.
            b = particle_bounds(out)
            if b is not None:
                out["bboxRender"] = [b["min"], b["max"]]
            return out
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
        # `include_skin` -- a PHY part is posed by its MOTI, and a
        # multi-bone part cannot be posed by one matrix. See
        # `mesh_to_json`; the flag is why this is the only caller.
        try:
            geo = self._mesh_to_json(p.mesh, 0, include_skin=True)
        except TypeError:                                  # pragma: no cover
            geo = self._mesh_to_json(p.mesh, 0)   # an older injected fn
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
            # CCFL kind 13. 0 means NO kind 13, which means drive the scroll
            # from `frame_at` exactly as before; a renderer must not read this
            # as a period of zero. See `wall_counter`.
            "periodMs": int(getattr(p, "period_ms", 0) or 0),
            # CCFL kind 9: the `(rate_u, rate_v)` UV-animation step. `[]` means
            # NO kind 9 -- distinct from `[0, 0]`, which two shipped meshes
            # actually carry (a kind-9 annotation whose rate IS zero), so a
            # renderer must not read absent and zero as the same thing.
            #
            # Added to the PRIMARY UV as `rate * per-effect-counter`: the
            # shipped shader is `PixelTexCoord0 = c3_TexCoord0 +
            # c3_UVAnimStep`. NOT a second UV set -- see
            # `core/c3ccfl.KIND_UV_ANIM_STEP`.
            "uvAnimStep": list(getattr(p, "uv_anim_step", ()) or ()),
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
# `tools/webui/fx.js` is a line-for-line mirror of the functions below --
# `frame_at`, `sample_part`, `motion_matrix`, `ribbon_advance`, and (further
# down) `particle_scale` / `particle_frame` / `particle_quads`.
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


def wall_counter(wall_ms, period_ms) -> Optional[int]:
    r"""The kind-13 scroll counter: **`(wall_ms & 0xFFFFFFFF) // period_ms`**.

    THE REFERENCE IMPLEMENTATION. `tools/webui/gl.js` and Route B's
    `render.cpp` must produce this integer EXACTLY, and the parity gate
    compares the INTEGER rather than the pixels -- two renderers can disagree
    by a counter tick and both still look like a scrolling texture.

    **WHAT THE CLIENT DOES.** CCFL kind 13 is an i32 period in ms at the CCFL
    object's ``+0x6C``, and the client drives the PRECEDING mesh's scrolling
    UVs as ``timeGetTime() / period`` -- an unsigned division of a DWORD.
    `timeGetTime` counts milliseconds since system start, so that clock is
    **absolute and shared by every effect on screen**, where `frame_at` is
    per-instance and restarts when an effect spawns. Those are different
    animations, not different constants, which is why this is a decision for
    a renderer rather than a tuning value.

    **THREE THINGS DECIDE PARITY, and each is a way two renderers disagree
    while both look right:**

    1. **`wall_ms` IS SUPPLIED, NEVER READ HERE.** Every consumer takes one
       number from one place per frame. A renderer that calls its own clock
       inside the draw loop disagrees with itself between two parts of one
       effect, and with this gate always.
    2. **THE MASK IS SEMANTIC, NOT HYGIENE.** The client divides a DWORD, so
       the wrap at 2^32 is part of the answer rather than an artefact.
       `Date.now()` is ~1.76e12 and Route B's clock has its own epoch; the
       mask is what makes three different epochs agree on one integer. The
       absolute PHASE is unknowable anyway -- the client's epoch is boot.
    3. **MULTIPLY IN DOUBLE, NEVER IN FLOAT.** The counter reaches ~1.3e8
       (2^32 / 33), so `counter * step` lands near 1e6 with the FRACTION --
       the whole visible quantity -- in the low bits. A float32 multiply
       throws that away and the two renderers diverge exactly where the
       animation lives. The counter itself is safe in a JS double: 1.3e8 is
       far under 2^53.

    **AND THE STEP DELTAS MUST BE THE FLOAT READING.** What this multiplies is
    ``+0x1B4``/``+0x1B8``, the mesh's ``STEP`` -- two dwords that ARE two f32.
    `EffectPart.uv_step` reinterprets them; `PhyMesh.step` does not. A
    renderer multiplying this counter by the RAW dwords gets a number near
    1e9 where 0.01 was meant, and `_wrap11` folds it into noise that looks
    like nothing in particular rather than like a bug. That is M8 in the
    charter, and it sits one file away from every consumer that has it wrong.

    Returns `None` when there is no kind 13 -- absent, `NO_PERIOD`, or
    non-positive -- which means **use `frame_at`**. The default path is
    unchanged and must stay byte-identical for every mesh without a kind 13;
    that is the cheapest regression check this change has.
    """
    if period_ms is None:
        return None
    period = int(period_ms)
    if period <= 0:
        return None
    return (int(math.floor(wall_ms)) & 0xFFFFFFFF) // period


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
# particles -- docs/effects.md 6.6 "Rendering, in the same form as 8"
# ---------------------------------------------------------------------------
#
# The simulation is BAKED. `frames[i]` is the solved particle set for frame i,
# so there is no integrator here and there is not supposed to be one: this
# turns one already-solved frame into camera-facing quads and nothing else.
#
# What the engine does, and what each line below is:
#
#     M      = frame.matrix x world              premultiply, then transform
#     p      = M * frame.position[i]             D3DXVec3TransformCoordArray
#     s      = frame.size[i] * scale_of(M)       half-extent
#     K      = atlasCols or N                   CCFL kind 3, 0 = square
#     c      = int(frame.cellPhase[i] * K*N)     N = `texGrid` (ROWS)
#     uv0    = (c % N / N, c // N / N)           cell size 1/N
#
# The one thing this file decides that graphic.dll does not hand over is WHICH
# PLANE the quad lies in. `Ptcl_Draw` subtracts the half-extent from the quad's
# x and y (0x60808) after the position array has been transformed, which is the
# ordinary billboard idiom -- the caller is expected to have supplied a world
# matrix whose x/y already face the camera. Our viewport has no such caller, so
# `particle_quads` takes the camera's right/up explicitly and the *caller*
# decides. gl.js takes them out of the view matrix it is already building, the
# way `screenToGround` re-derives its basis from the render path rather than
# borrowing `_basis()` -- the two differ in sign on X and that bug looks like
# a mirrored world, not like a crash.

_SQRT3 = 3.0 ** 0.5


def particle_scale(glm) -> float:
    """`Ptcl_Draw`'s size multiplier (RVA 0x1EC968).

    The length of the matrix's transformed ``(1,1,1)`` direction times
    ``1/sqrt(3)`` -- i.e. 1.0 for a rotation, and the uniform factor for a
    uniform scale. Translation is deliberately not included: this is a
    direction, not a point.

    >>> round(particle_scale(GL_IDENTITY), 6)
    1.0
    """
    x = glm[0] + glm[4] + glm[8]
    y = glm[1] + glm[5] + glm[9]
    z = glm[2] + glm[6] + glm[10]
    return ((x * x + y * y + z * z) ** 0.5) / _SQRT3


def particle_frame(part: dict, frame: int) -> Optional[dict]:
    """The solved set for `frame`, or None when nothing is alive.

    `Ptcl_SetFrame` / `Ptcl_NextFrame` are ``frame % frameCount`` (RVA 0x61570
    / 0x613B0), the same clock discipline as `Phy_NextFrame`, and `Ptcl_Draw`
    returns immediately on ``count == 0`` (RVA 0x605CF) -- 47 % of patch5517's
    particle frames are empty, so the empty case is the common one.
    """
    frames = (part or {}).get("frames") or []
    if not frames:
        return None
    f = frames[int(frame) % len(frames)]
    return f if f.get("n") else None


#: Corner offsets of a billboard quad as (right, up) multiples, and the (u, v)
#: corner of the atlas cell each one takes, in the two-triangle order the quad
#: is emitted in. V grows downward -- the viewer sets no UNPACK_FLIP_Y_WEBGL and
#: D3D's V axis runs top-down (gl.js header), so the cell's row index counts
#: from the top exactly as the PHY ChangeTex channel's does (docs/effects.md
#: 6.4). Screen-up is therefore the SMALL v.
_QUAD = (
    (-1.0,  1.0, 0.0, 0.0),
    (-1.0, -1.0, 0.0, 1.0),
    ( 1.0,  1.0, 1.0, 0.0),
    ( 1.0,  1.0, 1.0, 0.0),
    (-1.0, -1.0, 0.0, 1.0),
    ( 1.0, -1.0, 1.0, 1.0),
)

#: Vertices this module emits per particle. NOT `PTCL_VERTS_PER_PARTICLE` (4):
#: the engine draws a strip off a 4-vertex quad, we draw a triangle list, so
#: the two numbers describe different buffers and are not each other's bug.
QUAD_VERTS_PER_PARTICLE = 6


def particle_quads(part: dict, frame: int, world, right, up) -> dict:
    """One frame of a baked particle system as camera-facing quads.

    `world` is the effect's world matrix (column-major GL, the same one the
    PHY parts ride). `right` and `up` are the camera basis in world space.
    Returns flat arrays ready for a vertex buffer::

        {"n": particles, "pos": [x,y,z ...], "uv": [u,v ...], "alpha": float}

    with ``6 * n`` vertices in each. Returns ``n = 0`` for an empty frame.

    NOT APPLIED, and named rather than faked:

    * the PTC3 **per-particle** alpha envelope (`particleAlpha` /
      `particleLife`, RVA 0x5FCD3). It runs on a particle's *normalised life*,
      and a baked frame carries no life and no birth frame, so the input does
      not exist in this descriptor. 1,057 of patch5517's 2,211 PTC3 parts
      would be unaffected anyway (peak 1.0), but 1,154 would not.
    * `worldSpace` (+0x34, non-zero "skips the position transform") -- 165 of
      those 2,211 parts. One clause of prose is not enough to branch the
      transform on, and a wrong branch here puts a whole effect somewhere else
      in the world, which is the loudest failure available.
    * `billboard` (+0x30) and `roll` (+0x3C) -- 337 parts carry a non-zero
      billboard byte and 333 a non-zero roll. Both are unread here.

    The **system** alpha envelope (`systemAlpha`, RVA 0x5F5BA) IS applied, and
    on patch5517 it is inert on all 2,211 PTC3 parts: every one declares
    ``alpha = (1,1,1)`` with ``fadeFrame = (0, 0xFFFFFFFF)``. Applying it is
    therefore correct and invisible on this base, which is worth saying out
    loud so nobody reads a flat glow as proof the envelope works.
    """
    f = particle_frame(part, frame)
    out = {"n": 0, "pos": [], "uv": [], "alpha": 1.0}
    if f is None:
        return out
    fm = f.get("m") or GL_IDENTITY
    m = mat_mul_gl(list(world), list(fm))
    scale = particle_scale(m)
    # ROWS and COLUMNS, and they are only equal when there is no kind 3.
    n = int(max(1, part.get("atlas") or 1))
    k = int(part.get("atlasCols") or 0) or n
    k = max(1, k)
    cell_u, cell_v = 1.0 / k, 1.0 / n
    last = k * n - 1
    # CCFL KIND 10, AND IT IS A HYBRID: the offset pair is ADDED to the cell
    # UV and the extent pair is MULTIPLIED into the cell SIZE. Traced end to
    # end by the Director of RE in 7878/Env_DX9/graphic.dll --
    # `docs/ccfl_kind10_consumer_2026-09-16.md`:
    #
    #   offset  0x14DCA9  fld [eax+0x5C]; fadd [esp+0xC0]   (and +0x60)
    #   extent  0x14F554  fld [edi+0x64]; fmul [esp+0x14]   (and +0x68)
    #
    # and both sites load the annotation from `PTC3+0x6C` (0x14DC9B, 0x14F4AD),
    # which is what makes them provably the same record the reader populated
    # rather than a coincidental same-layout struct.
    #
    # **I ENUMERATED THREE READINGS AND THE ANSWER WAS A FOURTH.** REPLACE,
    # MULTIPLY-into-cell and OFFSET-within-cell were the candidates; the truth
    # takes the origin from one and the size from another. Enumerating the
    # candidates is itself a guess, and a gate asserting "one of these three"
    # would have been wrong in a way running it could never have shown.
    #
    # The engine gates this on a flag at +0x58 that the reader SETS whenever a
    # type-10 entry exists, and a clear flag draws the UNMODIFIED cell rect --
    # still drawn, not dormant. An absent `uvRect` here is exactly that case.
    rect = part.get("uvRect") or ()
    off_u, off_v = (float(rect[0]), float(rect[1])) if len(rect) == 4 else (0.0, 0.0)
    if len(rect) == 4:
        cell_u *= float(rect[2])
        cell_v *= float(rect[3])
    sa = part.get("systemAlpha") or []
    if sa:
        out["alpha"] = float(sa[min(int(frame), len(sa) - 1)])

    count = int(f["n"])
    pos = f.get("p") or []
    cells = f.get("c") or []
    sizes = f.get("s") or []
    vpos: list = []
    vuv: list = []
    for i in range(count):
        px, py, pz = _xform(m, pos[i * 3:i * 3 + 3])
        s = float(sizes[i]) * scale
        # int() truncates exactly as `Particle.cell` does; the clamp is for a
        # phase of exactly 1.0, which would index one cell past the atlas.
        c = min(int(float(cells[i]) * k * n), last)
        # The cell ORIGIN uses the unscaled cell pitch; only the SIZE is
        # scaled by the extent. Scaling the origin too would move every cell
        # but the first, which is the mistake the fixture's non-multiple
        # values were chosen to expose.
        u0 = (c % k) / k + off_u
        v0 = (c // k) / n + off_v
        for (dr, du, cu, cv) in _QUAD:
            vpos.append(px + right[0] * dr * s + up[0] * du * s)
            vpos.append(py + right[1] * dr * s + up[1] * du * s)
            vpos.append(pz + right[2] * dr * s + up[2] * du * s)
            vuv.append(u0 + cu * cell_u)
            vuv.append(v0 + cv * cell_v)
    out["n"] = count
    out["pos"] = vpos
    out["uv"] = vuv
    return out


def particle_bounds(part: dict) -> Optional[dict]:
    """The axis-aligned box every particle of every frame sits in, half-extent
    included, in the part's own (untransformed) space.

    A pure-particle effect has no `PHY` geometry, so the viewer's camera fit
    had nothing to frame on and a burst 12 units across came up as an
    invisible dot in a view last used for a 400-unit aura. Returns None when
    the system never has a live particle.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False
    for f in (part or {}).get("frames") or []:
        if not f.get("n"):
            continue
        m = f.get("m") or GL_IDENTITY
        scale = particle_scale(list(m))
        pos = f.get("p") or []
        sizes = f.get("s") or []
        for i in range(int(f["n"])):
            p = _xform(list(m), pos[i * 3:i * 3 + 3])
            s = float(sizes[i]) * scale
            seen = True
            for k in range(3):
                lo[k] = min(lo[k], p[k] - s)
                hi[k] = max(hi[k], p[k] + s)
    return {"min": lo, "max": hi} if seen else None


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
                elif p["kind"] == "particle" and p.get("decoded"):
                    print(f"     ptcl  {p['name']:<10} gen {p['generation']}  "
                          f"frames {p['frameCount']}  peak {p['peakParticles']}"
                          f"/{p['maxParticles']}  atlas "
                          f"{p.get('atlasCols') or p['atlas']}x{p['atlas']}"
                          + ("  (CCFL kind 3)" if p.get("atlasCols") else ""))
                else:
                    print(f"     {p['kind']} {p['name']} ({p['rawSize']} bytes, not decoded)")
        return 0

    if a.coverage:
        names = pl.names()
        ok = playable = particles = undecoded = 0
        for n in names:
            sc = pl.scene(n)
            if not sc.found:
                continue
            ok += 1
            if sc.payload["playableParts"]:
                playable += 1
            if sc.payload["particleParts"]:
                particles += 1
            if sc.payload.get("undecodedParts"):
                undecoded += 1
        print(f"base                {coroot.base_id(pl.root)}")
        print(f"effect names        {len(names)}")
        print(f"  resolve           {ok}")
        print(f"  playable parts    {playable}   (geometry and particles)")
        print(f"  using particles   {particles}")
        print(f"  undecoded chunks  {undecoded}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
