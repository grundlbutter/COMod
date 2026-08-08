#!/usr/bin/env python3
r"""
coviewer.py -- the Classic Conquer 2.0 asset viewer.

    py -3 tools/coviewer.py

...and a browser opens on http://127.0.0.1:8731 with a searchable catalogue of
every texture and mesh the client can see, a WebGL viewport that draws meshes
with the engine's own winding / culling / matrix conventions, and a
texture-swap flow that previews live and commits through comod.py.

Design notes
------------
* **Local web app, not a native GUI.**  WebGL gives real-time rendering with
  exact control over face winding, culling and alpha blending -- the three
  things that make a mesh preview lie -- with no native dependency to install.
  Everything is vendored: no CDN, no network access beyond 127.0.0.1.
* **Nothing is written to the game install by this process.**  The only writes
  are into `mods/stage/` and `out/viewer/`.  Installing shells out to
  `comod.py install --yes`, which has the backup/manifest/uninstall machinery
  and is the only sanctioned writer.
* **numpy is optional.**  It is used only to speed geometry packing up.  If it
  is missing everything still works, a little slower.

Layers:
    dds.py         DDS/BC decoding (self-contained, cross-checked against PIL)
    c3phy.py       PHY mesh parsing (unmodified; imported)
    coassets.py    asset VFS + ini tables (unmodified; imported)
    comod.py       stage/install/uninstall (unmodified; invoked as a subprocess)
"""

from __future__ import annotations

import argparse
import html
import io
import json
import math
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import bodyfacets                                        # noqa: E402
import builder as builder_mod                            # noqa: E402
import c3phy                                             # noqa: E402
import catalog as assetcat                               # noqa: E402
import dds                                               # noqa: E402
import mapindex                                          # noqa: E402
import mapedit as mapedit_mod                            # noqa: E402
import models as models_mod                              # noqa: E402
import parts as partsmod                                 # noqa: E402
import effectplay                                        # noqa: E402
import unify                                             # noqa: E402

try:
    # task #18 recovered the client's own attachment composition out of
    # Role3D.dll / graphic.dll.  Import it; never edit it.
    import attach                                        # noqa: E402
except Exception:                                        # pragma: no cover
    attach = None
from tagstore import TagStore, TagError, normalise as norm_tag   # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot, C3File, parse_ini   # noqa: E402
from tqhash import tq_hash                               # noqa: E402
import coroot                                            # noqa: E402
import health                                            # noqa: E402
import safepath                                          # noqa: E402

try:
    # c3tex.py (owned by the Blender workstream) already encodes the
    # mesh-file -> texture-file inference by inverting the appearance tables.
    # Import it rather than re-deriving the same rule here.
    import c3tex                                         # noqa: E402
except Exception:                                        # pragma: no cover
    c3tex = None

WEBUI = HERE / "webui"
STAGE = PROJECT / "mods" / "stage"
OUTDIR = PROJECT / "out" / "viewer"
PREVIEW_DIR = OUTDIR / "preview"
TAGS_FILE = OUTDIR / "tags.json"

#: Tables whose appearances are player bodies, and so get the
#: class / gender / size facets.  Both are backed by armor.ini.
BODY_TABLES = ("body", "mix_body")

#: Recovered WDF filename tables, best first.  The `out/wdf/*_names.json` pair
#: carries 24,426 names (98.7%); `out/dll/wdf_name_recovery.json`, which
#: coassets loads by default, only has 10,126.
NAME_TABLES = ("out/wdf/c3_names.json", "out/wdf/data_names.json")


def _unit_rows(m):
    """The 3x3 rows of a 4x4 scaled to unit length, translation untouched.

    Direction is preserved exactly; only length changes. A row that is all
    zeros is left alone -- there is no direction to keep, and inventing one is
    how an earlier attempt at this ended up holding weapons at impossible
    angles.
    """
    out = list(m)
    for r in (0, 4, 8):
        n = math.sqrt(m[r] ** 2 + m[r + 1] ** 2 + m[r + 2] ** 2)
        if n > 1e-9:
            out[r], out[r + 1], out[r + 2] = (m[r] / n, m[r + 1] / n,
                                              m[r + 2] / n)
    return tuple(out)


#: `mode` from `Plugin.socket_correction` -> the transform that applies it.
_SOCKET_CORRECTIONS = {"unit-rows": _unit_rows}

#: (root, appearance, weapon set, action) -> (mesh bytes, motion) for a
#: reference client, so a per-frame borrow costs one parse rather than one
#: parse per frame.
_REF_MOTION_CACHE: dict = {}


def _reference_root(kind: str):
    """The declared install for a reference plugin name, or None.

    Reads the user's own `coroot.declare_kind` record. A reference client is
    one they added; this never goes looking for an install on its own.
    """
    for path, declared in (coroot.read_settings().get(coroot.KINDS_KEY)
                           or {}).items():
        if str(declared).strip().lower() == kind:
            p = Path(str(path))
            return p if p.is_dir() else None
    return None


def _reference_basis(kind, socket, body_appearance, weapon_set, action, frame):
    """The same socket's 3x3 from a reference client, or None.

    Exact rather than approximate, for a measured reason: the socket's
    **translation is identical** between CCO and this lineage -- 0.0000 across
    shapes, actions and frames, mid-swing included -- so the skeleton and the
    pose already agree and only the orientation track was rewritten. Taking
    the 3x3 and keeping our own translation restores the authored orientation
    without moving the hand.

    None whenever anything fails to line up: no such install declared, the
    motion absent, or the frame counts differing (CCO's action 130 is 25
    frames against this lineage's 20). The caller then falls back and says so.
    """
    root = _reference_root(kind)
    if root is None:
        return None
    key = (str(root), body_appearance, weapon_set, action)
    hit = _REF_MOTION_CACHE.get(key)
    if hit is None:
        try:
            cat = partsmod.action_catalogue(root)
            mesh = (cat.appearance_mesh("armor.ini", body_appearance)
                    or cat.mesh_path(body_appearance))
            motion = partsmod.idle_motion(body_appearance, root,
                                          weapon_set, action)
            hit = ((AssetRoot(root).read(mesh), motion)
                   if (mesh is not None and motion is not None)
                   else (None, None))
        except Exception:
            hit = (None, None)
        _REF_MOTION_CACHE[key] = hit
    raw, motion = hit
    if raw is None or motion is None:
        return None
    try:
        a = partsmod.socket_anchors(raw, motion_set=motion,
                                    frame=frame).get(socket)
    except Exception:                                    # pragma: no cover
        return None
    return a.matrix if (a and a.matrix) else None


def apply_socket_corrections(anchors, plugin, body_appearance, *,
                             weapon_set="000", action="100", frame=0):
    """Repair sockets the plugin declares broken. Returns `(anchors, notes)`.

    **This is the only place a correction is applied, and that is the point.**
    `tools/attach.py` and `tools/parts.py` stay a faithful reading of the
    shipped data, and they are what `client/` and any future engine port
    consume -- so a viewer deviation cannot leak into the compatible-client
    work by riding a shared code path. See `plugins.Plugin.socket_correction`.

    `notes` is `{socket: note}` and travels in the payload, because a
    correction the user cannot see is indistinguishable from a reader that is
    simply wrong.
    """
    notes: dict = {}
    if plugin is None or not anchors:
        return anchors, notes
    for name, anchor in list(anchors.items()):
        try:
            hit = plugin.socket_correction(name, body_appearance)
        except Exception:                                # pragma: no cover
            continue
        if not hit:
            continue
        mode, note = hit
        if not getattr(anchor, "matrix", None):
            continue
        if mode.startswith("reference-basis:"):
            ref = _reference_basis(mode.split(":", 1)[1], name,
                                   body_appearance, weapon_set, action, frame)
            if ref is not None:
                # The reference's orientation, our own translation. Those two
                # already agree to 0.0000, so this moves nothing -- it only
                # restores the 3x3 the shipped track lost.
                m = list(anchor.matrix)
                for r in (0, 4, 8):
                    m[r], m[r + 1], m[r + 2] = ref[r], ref[r + 1], ref[r + 2]
                anchor.matrix = tuple(m)
                notes[name] = note
                continue
            # No reference available, or it did not line up. Fall back to the
            # shape-preserving repair and say which one happened -- "corrected"
            # without saying how is the kind of half-truth that cost this
            # project two sessions.
            anchor.matrix = _unit_rows(anchor.matrix)
            notes[name] = (note + "  [FALLBACK: the reference client is not "
                           "available or its track does not line up, so only "
                           "the collapse is repaired -- the orientation is "
                           "still the one this client ships.]")
            continue
        fn = _SOCKET_CORRECTIONS.get(mode)
        if fn is None:
            continue
        anchor.matrix = fn(anchor.matrix)
        notes[name] = note
    return anchors, notes


def _core_colibrary():
    """`core/colibrary.py`, never `tools/colibrary.py`.

    Two modules share the name -- the library itself and a CLI over it -- and
    which one `import colibrary` finds depends on the order `sys.path`
    happens to be in when it runs. `coviewer` puts `core/` first, but every
    module under `tools/` inserts its own directory at position 0 on import,
    so by the time a request is served `tools/` is usually ahead. The CLI's
    own top-level `from colibrary import ...` then resolves to itself and
    raises an ImportError naming a symbol the caller never asked for --
    which is exactly how this surfaced.

    That is a coin-flip decided by import order, so it is resolved by file
    location instead. The CLI is fine when run as a script, which is what it
    is for.
    """
    import importlib.util
    mod = sys.modules.get("_co_core_colibrary")
    if mod is not None:
        return mod
    src = PROJECT / "core" / "colibrary.py"
    spec = importlib.util.spec_from_file_location("_co_core_colibrary", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_co_core_colibrary"] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_derived(rel: str, root=None) -> str:
    """Text of a derived artefact, resolved for the base at `root`.

    Raises when it is not there, because every caller of this already sits in
    a `try` that degrades to "no opinion" -- and a missing per-base index has
    to read as "not built for this client yet", never as an invitation to use
    the last client's copy.

    Pass the *catalogue's* root, not the configured one: while comparing two
    bases both catalogues are live in one process.
    """
    p = coroot.find_derived(rel, root)
    if p is None:
        raise FileNotFoundError(coroot.derived_rel(rel, root))
    return p.read_text("utf-8")

#: Attachment-point ("Dumy") chunk names from ini/RolePart.ini.  These PHY
#: chunks are sockets, not visible geometry -- the engine uses them to position
#: helmets and weapons.  Hidden by default in the viewport.
#:
#: **Membership of the `[Dumy]` list, NOT a `v_` prefix.**  A prefix test is
#: wrong in both directions: hair meshes ship chunks called `v_armet01` /
#: `v_armet02` that are *real geometry* (46 and 20 meshes), and series-119 hair
#: puts its visible geometry in a chunk named `v_body`.  Hiding either renders
#: the part empty.  docs/attachment.md §9.  `attach.is_socket_name` is the
#: authority; this literal set is only the fallback when it is not importable.
SOCKET_NAMES = {
    "v_armet", "v_head", "v_l_weapon", "v_r_weapon", "v_shield", "v_mount",
    "v_l_foot", "v_r_foot", "v_l_hand", "v_r_hand", "v_pelvis", "v_misc",
    "v_back", "v_wing", "v_tail", "v_effect",
}


def is_socket_chunk(name: str) -> bool:
    """True when a PHY chunk is an attachment point rather than geometry."""
    if attach is not None:
        try:
            return attach.is_socket_name(name)
        except Exception:                                # pragma: no cover
            pass
    return (name or "").lower() in SOCKET_NAMES


def _log(msg: str) -> None:
    print(f"[coviewer] {msg}", flush=True)


# ---------------------------------------------------------------------------
# thumbnail generation -- opt-in, never automatic
# ---------------------------------------------------------------------------

#: The one progress line tools/thumbs.py writes to stderr, carriage-return
#: updated.  Parsed rather than re-implemented so the numbers shown in the
#: browser are literally the renderer's own.
_THUMB_PROGRESS_RE = re.compile(
    r"(\w+)\s+(\d+)/(\d+)\s+ok\s+(\d+)\s+cached\s+(\d+)\s+failed\s+(\d+)\s+"
    r"([\d.]+)/s\s+eta\s+(\d+)s\s+([\d.]+)\s*MB")


class ThumbRunner:
    r"""Runs `tools/thumbs.py` in a child process, on request only.

    Three rules, all of them things a first-time user actually cares about:

    * **Nothing starts by itself.**  A cold clone has no `out/thumbs/`, and
      filling it is 15-30 minutes on a modest machine and ~637 MB of disk.
      That is not something to begin without being asked.
    * **The UI does not block on it.**  The job is a separate process; the
      HTTP server keeps serving, the page can be closed and reopened, and
      progress is polled.
    * **It is resumable.**  `thumbs.py --resume` skips anything whose content
      hash is unchanged, so an interrupted run continues instead of starting
      over.  That is what makes "yes" a low-stakes answer.

    The renderer itself is never reimplemented here -- this class only starts
    it, reads its progress line, and stops it.
    """

    #: mode -> the argv tools/thumbs.py already understands.
    MODES = {
        "meshes": ["--all", "--resume"],
        "all": ["--all", "--textures", "--resume"],
        "textures": ["--textures", "--resume"],
    }

    def __init__(self, project: Path, root: Path):
        self.project = project
        self.root = root
        self.lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.mode: Optional[str] = None
        self.started: float = 0.0
        self.finished: float = 0.0
        self.returncode: Optional[int] = None
        self.cancelled = False
        self.progress: dict = {}
        self.tail: list[str] = []
        #: "" = the base install; else the library server being rendered.
        self.target_server: str = ""

    # -- lifecycle ---------------------------------------------------------
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode: str, jobs: int = 0, limit: int = 0,
              server: str = "", library: str = "") -> dict:
        if mode not in self.MODES:
            raise ValueError(f"unknown mode {mode!r}; "
                             f"expected one of {sorted(self.MODES)}")
        with self.lock:
            if self.running():
                return {"started": False, "reason": "already running",
                        **self.status()}
            argv = [sys.executable, str(HERE / "thumbs.py"),
                    "--root", str(self.root), *self.MODES[mode]]
            self.target_server = server
            if server:
                argv += ["--library", str(library), "--server", server]
            if jobs:
                argv += ["--jobs", str(int(jobs))]
            if limit:
                argv += ["--limit", str(int(limit))]
            creation = 0
            if os.name == "nt":
                # Own process group: lets us kill the whole worker pool, not
                # just the parent, when someone hits Stop.
                creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.proc = subprocess.Popen(
                argv, cwd=str(self.project), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                errors="replace", creationflags=creation)
            self.mode = mode
            self.started = time.time()
            self.finished = 0.0
            self.returncode = None
            self.cancelled = False
            self.progress = {"label": mode, "done": 0, "total": 0}
            self.tail = []
            threading.Thread(target=self._pump, daemon=True).start()
            _log(f"thumbnails: started {' '.join(argv[1:])}")
            return {"started": True, "cmd": " ".join(argv), **self.status()}

    def cancel(self) -> dict:
        with self.lock:
            if not self.running():
                return {"cancelled": False, "reason": "not running",
                        **self.status()}
            self.cancelled = True
            pid = self.proc.pid                    # type: ignore[union-attr]
        if os.name == "nt":
            # terminate() only reaches the parent; the spawn Pool's children
            # would keep rendering.  taskkill /T takes the tree.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True)
        else:                                       # pragma: no cover
            self.proc.terminate()                   # type: ignore[union-attr]
        _log("thumbnails: cancelled")
        return {"cancelled": True, **self.status()}

    def _pump(self) -> None:
        """Read the child's output, keeping the last progress line and tail."""
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        buf = ""
        try:
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                if ch in ("\r", "\n"):
                    line = buf.strip()
                    buf = ""
                    if not line:
                        continue
                    m = _THUMB_PROGRESS_RE.search(line)
                    if m:
                        self.progress = {
                            "label": m.group(1), "done": int(m.group(2)),
                            "total": int(m.group(3)), "ok": int(m.group(4)),
                            "cached": int(m.group(5)),
                            "failed": int(m.group(6)),
                            "rate": float(m.group(7)),
                            "etaSeconds": int(m.group(8)),
                            "megabytes": float(m.group(9)),
                        }
                    else:
                        self.tail.append(line)
                        del self.tail[:-40]
                else:
                    buf += ch
        except Exception as e:                       # pragma: no cover
            self.tail.append(f"(reader stopped: {e})")
        self.returncode = proc.wait()
        self.finished = time.time()
        _log(f"thumbnails: finished, exit {self.returncode}")

    # -- reporting ---------------------------------------------------------
    def status(self) -> dict:
        running = self.running()
        elapsed = ((self.finished or time.time()) - self.started
                   if self.started else 0.0)
        return {
            "running": running,
            "mode": self.mode,
            "server": self.target_server,
            "elapsedSeconds": round(elapsed, 1),
            "returncode": self.returncode,
            "cancelled": self.cancelled,
            "progress": self.progress,
            "tail": self.tail[-12:],
        }


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

@dataclass
class Provenance:
    """Everything we can say about where one logical asset actually comes from.

    This is requirement (c) -- "pointing towards the files in question".  The
    single load-bearing fact is `source`: the client tries a loose file on disk
    FIRST and only falls back to the archives (coassets.AssetRoot, VERIFIED
    from TqPackage!TqFOpen).  So `source == "loose"` means the on-disk file is
    what the game will draw, and `source == "c3.wdf"` means the archive copy
    wins because no loose file shadows it.
    """
    logical: str
    exists: bool
    source: str = ""            # "loose" | "c3.wdf" | "data.wdf" | ""
    real_path: str = ""
    size: int = 0
    name_hash: str = ""
    archive_offset: int = -1
    archive_size: int = -1
    shadowed_archive: str = ""  # archive copy that a loose file is overriding
    shadowed_size: int = -1

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["overriding"] = bool(self.shadowed_archive)
        return d


class Catalog:
    """In-memory index over the install: every logical path the client can
    resolve, plus the appearance tables and a reverse map from asset file back
    to the appearance IDs that reference it."""

    def __init__(self, root: Path, server_view=None):
        self.root = Path(root)
        #: a colibrary.ServerView when browsing an imported community client;
        #: the namespace and the appearance tables then come from that
        #: client's profile instead of the install.
        self.server_view = server_view
        self.assets = server_view or AssetRoot(self.root)
        self.names: dict[int, str] = {}
        for rel in NAME_TABLES:
            p = coroot.find_derived(rel)
            if p is not None:
                self.names.update({int(k, 16): v
                                   for k, v in json.loads(p.read_text("utf-8")).items()})
        if not self.names:
            # fall back to whatever coassets can find
            try:
                self.assets.load_names()
                self.names = dict(self.assets._names or {})
            except Exception:
                pass
        # AssetRoot.name_for() consults this table; give it the better one.
        self.assets._names = self.names

        self.loose: set[str] = set()
        if self.server_view is None:
            self._scan_loose()

        # archive entry lookup: logical path -> (archive, entry)
        self.arc_by_hash = {}
        for aname, arc in self.assets._archives.items():
            for e in arc.entries:
                self.arc_by_hash.setdefault(e.hash, (aname, e))

        self.archived: dict[str, str] = {}       # logical -> archive name
        if self.server_view is not None:
            _labels = {"l": "library", "b": "baseline", "w": "baseline",
                       "e": "baseline"}
            for key, ref in self.server_view.filemap.items():
                self.archived[key] = _labels.get(ref[0], "baseline")
        else:
            for h, nm in self.names.items():
                hit = self.arc_by_hash.get(h)
                if hit:
                    self.archived[nm.lower()] = hit[0]

        self.all_paths: list[str] = sorted(set(self.loose) | set(self.archived))
        self._path_set = set(self.all_paths)

        # Assets this server does not share with the baseline get its tag
        # ("Zephyr"), derived from the filemap rather than stored per file:
        # 100k rows in the tag store would be a copy of the filemap that can
        # go stale, and this cannot.
        self.server_tag: str = (self.server_view.tag
                                if self.server_view is not None else "")
        self.unique_paths: set[str] = (self.server_view.unique_paths()
                                       if self.server_view is not None
                                       else set())
        #: logical -> the archive folder it was recovered into, e.g.
        #: "Garments4"; a second tag so one archive can be browsed alone.
        self.group_tags: dict[str, str] = (self.server_view.group_tags()
                                           if self.server_view is not None
                                           else {})

        self.tables: dict[str, object] = {}
        self.ref_index: dict[str, list[dict]] = {}
        self.facets: Optional[bodyfacets.BodyFacets] = None
        self._tables_ready = threading.Event()

        # Browsing taxonomy. `references` is empty until the appearance tables
        # finish loading, so classification of table-backed assets improves a
        # moment after start-up; the path rules work immediately.
        self.assetcat = assetcat.AssetCatalog(
            self.root, table_membership=self.references, exists=self.exists,
            list_under=self.list_under, npc_membership=self.npc_art_member)
        # Map data comes from real directories (the DMap/puzzle/scene stack
        # walks disk), so a server view gets a *materialized* map root: its
        # map/ and ani/ placement files written out once, old-client .7z
        # DMaps decompressed. Idempotent and cached under out/.
        self._map_root = self.root
        if self.server_view is not None:
            dest = OUTDIR / "serverviews" / self.server_view.server
            t0 = time.time()
            stats = self.server_view.materialize_maproot(dest, log=_log)
            if stats["copied"] or stats["extracted"]:
                _log(f"materialized {self.server_view.server} map root: "
                     f"{stats['copied']} copied, {stats['extracted']} "
                     f"DMaps extracted, {stats['failed']} failed "
                     f"({time.time()-t0:.0f}s) -> {dest}")
            self._map_root = dest
        self.maps = mapindex.MapIndex(self._map_root, exists=self.exists)
        self._cat_summary: Optional[dict] = None
        self._cat_index: Optional[dict] = None

        # The MapEditor's model (tools/mapedit.py). Lazy for the same reason as
        # the builder: it owns a PuzzleLibrary, a SceneLibrary and a
        # SpriteCache, and only one page wants them.
        self._mapedit: Optional[mapedit_mod.MapEditor] = None
        self._mapedit_lock = threading.Lock()

        # Reuse this Catalog's AssetRoot rather than letting c3tex mmap both
        # archives a second time; the resolution *logic* is c3tex's.
        self.texres = None
        if c3tex is not None:
            try:
                self.texres = c3tex.TextureResolver.__new__(c3tex.TextureResolver)
                self.texres.root = self.root
                self.texres.assets = self.assets
                self.texres.cache = OUTDIR / (
                    "texcache-" + server_view.server if server_view
                    else "texcache")
                self.texres.cache.mkdir(parents=True, exist_ok=True)
                self.texres._mesh2tex = None
                self.texres.extra_tables = []
                if server_view is not None:
                    # unbound call = the BASELINE tables off the same object
                    # (ServerView overrides part_tables with the server's)
                    self.texres.extra_tables = [
                        lambda sv=server_view: AssetRoot.part_tables(sv)]
            except Exception as e:                       # pragma: no cover
                _log(f"c3tex unavailable ({e}); standalone meshes will be untextured")
                self.texres = None

        # Effect playback (docs/effects.md §8). Built lazily on first use --
        # EffectDB costs ~0.25 s and most sessions never open an effect.
        self._effects: Optional[effectplay.EffectPlayer] = None
        self._effects_lock = threading.Lock()

        # The character builder's catalogue (tools/builder.py). Lazy for the
        # same reason: it costs ~1.5 s to check 20k appearance rows against
        # disk, and only the builder page needs it.
        self._builder: Optional[builder_mod.BuilderIndex] = None
        self._builder_lock = threading.Lock()

        # mesh <-> texture, so the file lists show one row per asset instead of
        # one per file. Cheap when out/meshtex/coverage.json exists.
        self._unified: Optional[unify.UnifiedIndex] = None
        self._unified_lock = threading.Lock()

        # The non-character model families (tools/models.py): monsters, NPCs,
        # ghosts, the mount family, the character-select roles, effects. Lazy
        # for the same reason as the builder -- only one page mode wants it.
        self._models: Optional[models_mod.ModelCatalogue] = None
        self._models_lock = threading.Lock()

        # Cached posed animations, keyed (body, action, weaponType). One action
        # is 8-31 frames and each frame costs ~9 ms to pose, so the first
        # request for an action is ~0.3 s and every replay after it is free.
        self._anim_cache: dict[tuple, dict] = {}
        #: the same, for /api/modelanim -- keyed (model key, action). A monster
        #: action can be a different *mesh* as well as a different motion, so
        #: it does not share the body cache's key shape.
        self._model_anim_cache: dict[tuple, dict] = {}
        self._anim_lock = threading.RLock()
        self._animdb = None

        threading.Thread(target=self._load_tables, daemon=True).start()

    @property
    def effects(self) -> effectplay.EffectPlayer:
        """The effect resolver, shared and lazy. Reuses this Catalog's
        AssetRoot so the two archives are not mmap'd a second time."""
        with self._effects_lock:
            if self._effects is None:
                self._effects = effectplay.EffectPlayer(
                    self.root, mesh_to_json=mesh_to_json, assets=self.assets)
            return self._effects

    @property
    def builder(self) -> builder_mod.BuilderIndex:
        """The character builder's catalogue, shared and lazy.

        Every option it offers is an appearance whose mesh AND texture both
        resolve, so the builder cannot construct a mesh+texture pair the game
        does not ship -- see tools/builder.py.
        """
        with self._builder_lock:
            if self._builder is None:
                self.wait_tables()
                self._builder = builder_mod.BuilderIndex(
                    self.tables, self.resolve_id, self.root,
                    facets=self.facets, exists=self.exists)
            return self._builder

    @property
    def mapedit(self) -> mapedit_mod.MapEditor:
        """The MapEditor's map library, shared and lazy.

        Reuses this Catalog's AssetRoot, so opening a map does not mmap
        `data.wdf` a third time to read its ground tiles.
        """
        with self._mapedit_lock:
            if self._mapedit is None:
                self._mapedit = mapedit_mod.MapEditor(self._map_root,
                                                      assets=self.assets)
            return self._mapedit

    @property
    def unified(self) -> unify.UnifiedIndex:
        """The mesh<->texture relation (tools/meshtex.py's answers) plus the
        rule that collapses a related pair into one list row."""
        with self._unified_lock:
            if self._unified is None:
                tdir = (PROJECT / "out" / "thumbs" / "servers"
                        / self.server_view.server
                        if self.server_view is not None else None)
                self._unified = unify.UnifiedIndex(self.root, exists=self.exists,
                                                   thumb_dir=tdir)
                if self.server_view is not None:
                    # recovered-archive pairs: model and skin share a stem
                    pairs = {}
                    for q in self.all_paths:
                        if q.endswith(".c3") and (q[:-3] + ".dds") in self._path_set:
                            pairs[q] = q[:-3] + ".dds"
                    n_add = self._unified.add_pairs(pairs)
                    if n_add:
                        _log(f"unified index: +{n_add} same-stem pairs "
                             f"from the {self.server_view.server} library")
                _log(f"unified index: {self._unified.source}"
                     f"{' — ' + self._unified.error if self._unified.error else ''}")
                n = len(self._unified.thumbs())
                if n:
                    _log(f"thumbnail manifest: {n} entries from out/thumbs/")
            return self._unified

    @property
    def models(self) -> models_mod.ModelCatalogue:
        r"""Monsters, NPCs, ghosts, the mount family, the character-select
        roles and the effect library -- everything that is a *model* rather
        than an assembled character (`tools/models.py`).

        Lazy: it walks 3dmotion.ini's 229k keys and the path set, which costs
        ~1.5 s, and only the builder's model mode asks for it.

        `has_geometry` is answered out of the mesh<->texture index rather than
        by opening 1,300 `.c3` files, and the texture comes from the same
        place -- `tools/meshtex.py` reaches `c3/monster/103/100.c3`, which has
        no sibling `.dds` at all, through `3dmotion.ini` -> `armor.ini`.
        """
        with self._models_lock:
            if self._models is None:
                u = self.unified

                def geom(p: str) -> Optional[bool]:
                    if not u.available:
                        return None
                    return p in u.mesh_matches or p.lower() in u.mesh_matches

                def tex(mesh: str):
                    ms = u.textures_of(mesh)
                    return ms[0] if ms else None

                npc_tex, flat_art = self._npc_plan_art()
                # Monsters render in their pinned default skin (the first
                # colourway), not meshtex's guess -- 109 was showing 108's.
                self._models = models_mod.ModelCatalogue(
                    self.root, paths=self.all_paths, exists=self.exists,
                    read=self.read, has_geometry=geom, texture_for=tex,
                    effect_names=self.effects.names,
                    entity_names=self._entity_name_map(),
                    entity_textures=npc_tex, flat_npc_art=flat_art,
                    plugin=self.plugin)
                # A pinned monster wears its default skin -- the first of
                # its verified colourways. meshtex had 109 in 108's clothes
                # and four dirs in a non-default colour; the pin decides,
                # whatever the model's representative mesh is named.
                # The label is the PLUGIN's claim, not this function's: a
                # subclass inheriting another client's colour sets must not
                # inherit its evidence too. patch5517 says "inherited,
                # unverified here" over the same table 6090 calls authored.
                method, kind = self.plugin.colour_provenance()
                for m in self._models.models:
                    if m.kind != "monster":
                        continue
                    tp = self.plugin.default_colour(m.ident)
                    if tp and self.assets.exists(tp):
                        m.texture = tp
                        m.texture_method = method
                        m.texture_kind = kind
                _log(f"model catalogue: {len(self._models.models)} models "
                     f"across {len(self._models.kinds())} kinds")
            return self._models

    @property
    def npc_tables(self):
        """`npc.json` -> `3DSimpleObj.ini` -> `3dobj.ini`/`3dtexture.ini`.

        Built lazily and once: three small tables, but the answer they give
        is the client's own rather than an inference about it, so anything
        deciding where an NPC's art lives should ask them first.
        """
        cached = getattr(self, "_npc_tables", "unset")
        if cached == "unset":
            try:
                import npcart
                cached = npcart.Tables(self.read)
            except Exception:                            # pragma: no cover
                cached = None
            self._npc_tables = cached
        return cached

    def npc_art_member(self, p: str) -> bool:
        """Whether the NPC tables reference this path -- geometry, skin,
        extra parts or a motion. Built once from every NPC's plan; this is
        what files `c3/mesh/9990010.c3` under NPCs in the category counts
        instead of leaving it in the shared character bucket."""
        paths = getattr(self, "_npc_art_paths", None)
        if paths is None:
            paths = set()
            t = self.npc_tables
            if t is not None:
                for row in t.npcs:
                    for q in t.plan_for_npc(row).paths():
                        paths.add(q.lower())
            self._npc_art_paths = paths
        return p in paths

    def texture_for_mesh(self, mesh_logical: str) -> Optional[str]:
        """Best-guess texture for a `.c3` opened on its own (no appearance).

        INFERRED, and the inference is c3tex's: invert the appearance tables to
        find every `Texture<i>` paired with this `Mesh<i>`, then fall back to
        the mesh's own id as a texture id.  A mesh usually has several valid
        textures -- this returns the first that resolves.
        """
        key = mesh_logical.replace("\\", "/").lstrip("/").lower()
        # The client's own tables first, because they are the answer and
        # everything below is an inference about it.
        #
        # This comment used to say `c3/npc/999001100.c3` is paired to
        # `c3/texture/9990010.dds` "by npc.json at 0.95 -- an *authored*
        # link". That was wrong on both counts. `npc.json` says no such
        # thing; the pairing came from `meshtex` transposing the mesh id,
        # and the client actually loads `c3/texture/9990211.dds`, reached
        # through `simple_object -> Texture0 -> 3dtexture.ini`. Verified in
        # the running game: replacing 9990010 changed nothing, replacing
        # 9990211 changed the NPC. Calling an inference "authored" and
        # scoring it 0.95 is what kept it from being questioned.
        t = self.npc_tables
        if t is not None:
            try:
                plan = t.plan_for_mesh(key)
            except Exception:                            # pragma: no cover
                plan = None
            if plan is not None and plan.texture:
                return plan.texture
        u = self.unified
        if u is not None and getattr(u, "available", False):
            try:
                rows = [r for r in u.textures_of(key)
                        if r.get("kind") == "authored" and r.get("texture")]
            except Exception:                            # pragma: no cover
                rows = []
            rows.sort(key=lambda r: -(r.get("confidence") or 0))
            for r in rows:
                if r["texture"] in self._path_set:
                    return r["texture"]
        if self.texres is not None:
            self.wait_tables(30)
            try:
                loc = self.texres.locate_texture(key)
                if loc:
                    return loc.logical
            except Exception:
                pass
        # Extra fallback c3tex does not cover: a same-named .dds sitting right
        # beside the mesh.  This is how the c3/npc/<id>/, c3/effect/<name>/ and
        # c3/mount/<id>/ trees are laid out -- 1,775 of the 6,390 .c3 files in
        # this install have exactly that sibling.
        sib = key[:-3] + ".dds" if key.endswith(".c3") else None
        if sib and sib in self._path_set:
            return sib
        if key.endswith(".c3"):
            # Same stem *anywhere*: an asset id names both halves even when
            # they live in different subtrees (c3/mesh/x/garment/X.c3 <->
            # c3/texture/garment/custom/X.dds in community clients).
            #
            # Only for stems that are actually asset ids. Inside a look
            # directory (c3/0004/650/100.c3, c3/mount/801/110.c3) the stem is
            # an ACTION NUMBER, and matching it globally paired bodies with
            # whatever happened to be called 100.dds -- an emoticon, in the
            # case that surfaced this. Ids are >=5 digits; actions are 3.
            stem = key.rsplit("/", 1)[-1][:-3]
            if not (stem.isdigit() and len(stem) < 5):
                hit = self._texture_stems().get(stem)
                if hit:
                    return hit
            # Last: any texture in the mesh's own directory (mount look
            # dirs keep one skin beside a dozen geometry/action files).
            sibs = self._textures_by_dir().get(key.rsplit("/", 1)[0])
            if sibs:
                return sibs[0]
        return None

    def _texture_stems(self) -> dict[str, str]:
        """stem -> first texture path, over the whole namespace. Lazy."""
        if getattr(self, "_tex_stems", None) is None:
            idx: dict[str, str] = {}
            for p in self.all_paths:
                if p.endswith(".dds"):
                    st = p.rsplit("/", 1)[-1][:-4]
                    # a short numeric name is a frame/action number, not an
                    # id; it must never be reachable as a global target
                    if st.isdigit() and len(st) < 5:
                        continue
                    idx.setdefault(st, p)
            self._tex_stems = idx
        return self._tex_stems

    def _textures_by_dir(self) -> dict[str, list[str]]:
        """directory -> its textures, sorted. Lazy."""
        if getattr(self, "_tex_dirs", None) is None:
            idx: dict[str, list[str]] = {}
            for p in self.all_paths:
                if p.endswith(".dds"):
                    idx.setdefault(p.rsplit("/", 1)[0], []).append(p)
            for lst in idx.values():
                lst.sort()
            self._tex_dirs = idx
        return self._tex_dirs

    # -- filesystem --------------------------------------------------------
    def _scan_loose(self) -> None:
        t0 = time.time()
        rootlen = len(str(self.root)) + 1
        for dirpath, _dirnames, filenames in os.walk(self.root):
            rel = dirpath[rootlen:].replace("\\", "/")
            prefix = (rel + "/") if rel else ""
            for f in filenames:
                self.loose.add((prefix + f).lower())
        _log(f"scanned {len(self.loose)} loose files in {time.time()-t0:.1f}s")

    # -- appearance tables --------------------------------------------------
    def _load_tables(self) -> None:
        t0 = time.time()
        try:
            self.tables = self.assets.part_tables()
        except Exception as e:                       # pragma: no cover
            _log(f"part table load failed: {e}")
            self.tables = {}
        idx: dict[str, list[dict]] = {}
        for part, ini in self.tables.items():
            for app in ini:
                for pr in app.parts:
                    for kind, ident in (("mesh", pr.mesh), ("texture", pr.texture)):
                        logical = self.resolve_id(ident, kind)
                        if not logical:
                            continue
                        idx.setdefault(logical, []).append(
                            {"table": part, "ini": ini.name, "appearance": app.ident,
                             "part": pr.index, "kind": kind})
        self.ref_index = idx
        _log(f"loaded {len(self.tables)} appearance tables, "
             f"{len(idx)} referenced assets in {time.time()-t0:.1f}s")
        try:
            t1 = time.time()
            bf = bodyfacets.BodyFacets(self.root, resolve=self.resolve_id)
            bf.build(self.tables)
            self.facets = bf
            _log(f"classified {len(bf.records)} body appearances "
                 f"(class/gender/size) in {time.time()-t1:.1f}s")
        except Exception as e:                           # pragma: no cover
            _log(f"body facet classification failed: {e}")
            self.facets = None
        # table evidence is available now, so any classification cached from the
        # path rules alone must be redone
        self.assetcat._cache.clear()
        self._cat_summary = None
        self._cat_index = None
        self._tables_ready.set()

    # -- category index ----------------------------------------------------
    def category_index(self) -> dict:
        """category -> subcategory -> sorted paths. Built once (~0.5 s over
        77k paths) and reused for every browse request."""
        if self._cat_index is not None:
            return self._cat_index
        idx: dict[str, dict[str, list[str]]] = {}
        for p in self.all_paths:
            c = self.assetcat.classify(p)
            idx.setdefault(c.category, {}).setdefault(c.subcategory, []).append(p)
        self._cat_index = idx
        return idx

    def category_summary(self) -> dict:
        if self._cat_summary is None:
            self._cat_summary = self.assetcat.summarise(self.all_paths)
        return self._cat_summary

    def _npc_plan_art(self) -> tuple[dict, dict]:
        """The NPC tables' resolved art, in the two shapes models.py needs.

        (mesh path -> texture path) for every plan that resolved both, and
        (flat look "001" -> (geometry, texture)) read off the plans' motion
        ids. This is the authored chain -- npc table -> simple object ->
        obj/texture tables -- answering for the model page, where meshtex
        could only guess: 78 of 228 npc meshes had no guess at all and a
        further set guessed wrong, which is exactly the dirs reported
        textureless or mismatched.
        """
        by_mesh: dict = {}
        flat: dict = {}
        t = self.npc_tables
        if t is None:
            return {}, {}
        pat = re.compile(r"c3/npc/999(\d{3})\d{3}\.c3$", re.I)
        for row in t.npcs:
            plan = t.plan_for_npc(row)
            if not (plan.geometry and plan.texture):
                continue
            by_mesh.setdefault(plan.geometry.lower(), plan.texture)
            for g2, t2 in plan.extra_parts:
                if g2 and t2:
                    by_mesh.setdefault(g2.lower(), t2)
            for m in plan.motions.values():
                mm = pat.match(m)
                if mm:
                    # Entities pair all sorts of geometry with a look's
                    # motions -- effect props (c3/effect/zf2-e181, which
                    # broke look 010) and the family's own short-stem mesh
                    # (999118.c3, which the family scan handles as base).
                    # Only a c3/mesh body may stand in for the look from
                    # here; everything else resolves inside the family.
                    if not plan.geometry.lower().startswith("c3/mesh/"):
                        continue
                    flat.setdefault(mm.group(1),
                                    (plan.geometry, plan.texture))
        return by_mesh, flat

    @property
    def plugin(self):
        """The parser plugin for this install: what tells the app how to
        read this flavour of client (`plugins/`).

        Chosen from what the user declared **for this root** -- they said
        what the folder was when they added it -- and only detected when
        nothing was declared. A declaration outranks a heuristic: a
        private-server repack of 6090 looks like 6090 to any test of the
        bytes, and the person who added it knows better.

        `coroot.kind_for_root`, not the bare `game_kind`: that is one value
        for eight installs, so pointing the tools at a different root with
        `--root` or `CO_ROOT` used to keep the previous client's plugin and
        parse the new client under the old one's conventions.
        """
        p = getattr(self, "_plugin", None)
        if p is None:
            sys.path.insert(0, str(PROJECT))
            import plugins as plugmod
            kind = coroot.kind_for_root(self.root)
            p = plugmod.for_kind(kind) if kind else None
            how = f"declared kind {kind!r}"
            if p is None:
                p = plugmod.detect(self.root, exists=self.assets.exists)
                how = "detected"
            _log(f"parser plugin: {p.name} ({how})")
            self._plugin = p
        return p

    #: LEGACY: the 6090 scan now lives in `plugins/patch6090.py`, where a
    #: contributor can read it, correct it, or write the equivalent for
    #: their own client. Kept as an alias so nothing that referenced it
    #: breaks mid-rewrite; ask `self.plugin` instead.
    @property
    def MONSTER_COLOURWAYS_6090(self) -> dict:
        return getattr(self.plugin, "MONSTERS", {})

    def monster_colourways(self, ident: str, base_texture: str) -> list[str]:
        """The shipped skins of one monster dir: the plugin's verified set
        first, then textures the entity crawl observed. Never a digit probe
        -- conventions cross families and dress monsters in each other's
        skins, which is why the plugin holds scanned sets at all."""
        # `exists` is name-based, and 24,426 of 24,757 archive names are
        # recovered -- 109000000.dds is one of the rest: the renderer loads
        # it by hash all day, while the path set has never heard of it.
        # Colourway probing has to ask the archive, or a real skin reads as
        # missing (which is what emptied 109's strip).
        have = self.assets.exists
        pinned = self.plugin.monster_colourways(ident)
        if pinned:
            return [p for p in pinned if have(p)]
        seen = self._monster_dir_textures().get(ident, set())
        if base_texture:
            seen = seen | {base_texture}
        return sorted(p for p in seen if have(p))

    def _monster_dir_textures(self) -> dict:
        cached = getattr(self, "_mdir_tex", None)
        if cached is not None:
            return cached
        out: dict = {}
        try:
            raw = json.loads(_read_derived("out/artcrawl/entities.json", self.root))
            ents = raw.get("entities", raw) if isinstance(raw, dict) else raw
            pat = re.compile(r"c3/monster/([^/]+)/", re.I)
            for e in ents:
                mm = pat.match(str(e.get("geometry", "")).lower())
                tex = str(e.get("texture", "") or "")
                if mm and tex:
                    out.setdefault(mm.group(1), set()).add(tex)
        except Exception:
            pass
        self._mdir_tex = out
        return out

    def _entity_name_map(self) -> dict:
        """(kind, dir) -> name, from the entity crawl (out/artcrawl).

        The names are the server dump's and npc.ini's -- ThunderApe is
        monster type 0012 wearing c3/monster/103/, so dir 103 is labelled
        ThunderApe. Where several entities share a dir, the lowest type id
        of the matching kind wins (the base monster, not its Msgr/Aide
        derivatives). Absent crawl output -> empty map, numeric labels.
        """
        ents = []
        try:
            raw = json.loads(_read_derived("out/artcrawl/entities.json", self.root))
            ents = raw.get("entities", raw) if isinstance(raw, dict) else raw
        except Exception:
            return {}
        out: dict = {}
        best: dict = {}
        pat = re.compile(r"c3/(monster|npc)/([^/]+)/", re.I)
        for e in ents:
            if not isinstance(e, dict):
                continue
            mm = pat.match(str(e.get("geometry", "")).lower())
            name = str(e.get("name", "") or "")
            if not mm or not name:
                continue
            key = (mm.group(1), mm.group(2))
            try:
                rank = (0 if str(e.get("kind")) == key[0] else 1,
                        int(str(e.get("id"))))
            except (TypeError, ValueError):
                rank = (2, 1 << 30)
            if key not in best or rank < best[key]:
                best[key] = rank
                out[key] = name
        pins, drops = self.plugin.entity_name_overrides()
        for k in drops:
            out.pop(k, None)
        out.update(pins)
        return out

    def texture_colourways(self, texture_logical: str) -> list[str]:
        """Every shipped colour of one texture, per the plugin's rules.

        Colour lives in the id and the position varies by client and by
        family, so the app does not guess: `Plugin.colourways` owns it and
        an empty answer means "this client has no such convention" (CCO
        ships one appearance row per colour instead).
        """
        return list(self.plugin.colourways(texture_logical,
                                          self.assets.exists))

    def appearances_using_mesh(self, mesh_logical: str) -> list[dict]:
        """Other colourways: every appearance whose mesh is this one."""
        out = []
        if not self.facets or not mesh_logical:
            return out
        for rec in self.facets.records.values():
            if rec.mesh == mesh_logical:
                out.append({"id": rec.ident, "texture": rec.texture,
                            "item": rec.item_name})
        out.sort(key=lambda r: r["id"])
        return out

    def wait_tables(self, timeout: float = 60.0) -> bool:
        return self._tables_ready.wait(timeout)

    # -- resolution ---------------------------------------------------------
    #: Same rule as coassets.AssetRoot.resolve_asset (INFERRED: reproduces 94%
    #: of armor.ini, 98% of weapon.ini, 79% of armet.ini) but resolved against
    #: the in-memory path set instead of hitting the filesystem 21 times.
    MESH_DIRS = ("mesh", "weapon", "body", "hair", "mount", "npc", "monster")
    TEX_DIRS = ("texture", "weapon", "body", "hair", "mount", "npc", "monster")

    def resolve_id(self, asset_id: str, kind: str = "texture") -> Optional[str]:
        if not asset_id or asset_id == "0":
            return None
        # full-path references from synthesised old-client tables
        if "/" in asset_id or "\\" in asset_id:
            key = asset_id.replace("\\", "/").lstrip("/").lower()
            return key if key in self._path_set else None
        dirs = self.TEX_DIRS if kind == "texture" else self.MESH_DIRS
        ext = ".dds" if kind == "texture" else ".c3"
        ids = []
        for cand in (asset_id, asset_id.zfill(9), asset_id.lstrip("0")):
            if cand and cand not in ids:
                ids.append(cand)
        for sub in dirs:
            for i in ids:
                p = f"c3/{sub}/{i}{ext}"
                if p in self._path_set:
                    return p
        return None

    def exists(self, logical: str) -> bool:
        return logical.lower().replace("\\", "/").lstrip("/") in self._path_set

    def list_under(self, prefix: str) -> list[str]:
        p = prefix.lower().replace("\\", "/").lstrip("/")
        return [x for x in self.all_paths if x.startswith(p)]

    def read(self, logical: str) -> bytes:
        return self.assets.read(logical)

    def provenance(self, logical: str) -> Provenance:
        logical = logical.replace("\\", "/").lstrip("/")
        key = logical.lower()
        h = tq_hash(key)
        pv = Provenance(logical=key, exists=False, name_hash=f"0x{h:08x}")

        if self.server_view is not None:
            loc = self.assets.locate(key)
            if loc is not None:
                pv.exists = True
                pv.source = loc.source
                if loc.real_path:
                    pv.real_path = str(loc.real_path)
                pv.size = loc.size
            return pv

        arc_hit = self.arc_by_hash.get(h)
        real = self.root / key
        if key in self.loose and real.is_file():
            pv.exists = True
            pv.source = "loose"
            pv.real_path = str(real)
            pv.size = real.stat().st_size
            if arc_hit:
                pv.shadowed_archive = arc_hit[0]
                pv.shadowed_size = arc_hit[1].size
                pv.archive_offset = arc_hit[1].offset
                pv.archive_size = arc_hit[1].size
        elif arc_hit:
            aname, e = arc_hit
            pv.exists = True
            pv.source = aname
            pv.real_path = str(self.root / aname)
            pv.size = e.size
            pv.archive_offset = e.offset
            pv.archive_size = e.size
        return pv

    def references(self, logical: str) -> list[dict]:
        return self.ref_index.get(logical.lower(), [])

    def auto_tags(self, logical: str) -> list[str]:
        """Tags the catalogue itself asserts, as opposed to ones you wrote.

        Today that is exactly one: the server tag on assets unique to the
        selected community client.
        """
        key = logical.lower()
        out = []
        if self.server_tag and key in self.unique_paths:
            out.append(self.server_tag)
        grp = self.group_tags.get(key)
        if grp:
            out.append(grp)
        return out

    def close(self) -> None:
        self.assets.close()


# ---------------------------------------------------------------------------
# geometry -> JSON
# ---------------------------------------------------------------------------

def _decode_label(raw: str) -> str:
    r"""PHY chunk labels are usually the original 3DSMax source texture path
    (`texture\armet001.tga`).  A great many are GBK-encoded Chinese, and
    c3phy decodes the bytes as latin-1, so re-encode and try GBK."""
    if not raw:
        return ""
    try:
        b = raw.encode("latin-1")
    except UnicodeEncodeError:
        return raw
    if all(c < 0x80 for c in b):
        return raw
    for enc in ("gbk", "big5", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw


def _c3key_frames(records: list[bytes]) -> list[dict]:
    """Decode the 16-byte C3Frame records of a C3Key channel.

    struct C3Frame { int nFrame; float fParam[1]; BOOL bParam[1]; int nParam[1]; };
    -- from TQ's own c3_key.h, cross-validated against the disassembly
    (docs/modding.md 9.6).  The *meaning* per channel is inferred from the
    exported consumer names (Key_ProcessAlpha / Draw / ChangeTex); the layout
    is not.
    """
    import struct as _s
    out = []
    for r in records:
        if len(r) < 16:
            continue
        n, f, b, i = _s.unpack("<ifii", r[:16])
        out.append({"frame": n, "f": round(f, 6), "b": b, "n": i})
    return out


_IDENT = (1.0, 0.0, 0.0, 0.0,
          0.0, 1.0, 0.0, 0.0,
          0.0, 0.0, 1.0, 0.0,
          0.0, 0.0, 0.0, 1.0)


def mesh_to_json(m: "c3phy.PhyMesh", index: int, motion=None, frame: int = 0) -> dict:
    """Convert one PHY chunk to the viewport's wire format.

    What happens here, and why:

    * `apply_matrix_to` -- the engine applies the chunk's 4x4 matrix to every
      position at load time (RVA 0x5A735).  ~7.5% of chunks carry a non-identity
      matrix; skipping it puts limbs and weapons in the wrong place.
    * `generate_normals` -- "PHY " and "PHY4", the only two variants shipped in
      this build, do NOT store normals; the engine generates them by
      accumulating face normals (RVA 0x5A8C0).  c3phy reproduces that exactly.
    * `(x, y, -z)` with the index order UNCHANGED -- C3 is a left-handed,
      Z-points-down frame whose triangles are D3D clockwise-front.  Negating one
      axis stands the model upright AND flips handedness, so in WebGL's
      right-handed frame the shipped index order is correctly counter-clockwise
      front-facing.  Do exactly one of the two, never both (c3phy.to_blender).
    * `motion` -- OPTIONAL, used only when composing an equipped part onto a
      socket.  It is the chunk's own `MOTI`, applied per vertex with its two
      bone influences exactly as `attach.transform_vertex` does:
      `p' = sum_k w_k * (p x Mbone_k)`.  That is stage 2 of the attachment chain
      (tools/attach.py) and it is where an equipped part's scale and axis
      correction live -- dropping it renders a sword several times too long and
      puts headgear at the wrong angle.  It is deliberately NOT applied when a
      mesh is viewed on its own, because asset view shows a mesh as authored.
    """
    import copy
    mm = copy.deepcopy(m)
    matrix_identity = (len(mm.matrix) == 16 and
                       all(abs(a - b) < 1e-6 for a, b in zip(mm.matrix, _IDENT)))
    c3phy.apply_matrix_to(mm)
    motion_applied = False
    if motion is not None and mm.vertices and attach is not None:
        try:
            for v in mm.vertices:
                p = (v.px, v.py, v.pz)
                x, y, z = attach.fx.transform_point(motion.matrix(v.bone0, frame), p)
                if v.weight1:
                    x1, y1, z1 = attach.fx.transform_point(
                        motion.matrix(v.bone1, frame), p)
                    w = v.weight0
                    x = x * w + x1 * (1 - w)
                    y = y * w + y1 * (1 - w)
                    z = z * w + z1 * (1 - w)
                v.px, v.py, v.pz = x, y, z
            motion_applied = True
        except Exception:                                # pragma: no cover
            motion_applied = False
    has_file_normals = any((v.nx or v.ny or v.nz) for v in mm.vertices)
    if not has_file_normals or motion_applied:
        # Baking the motion moves the geometry, so any stored normals no longer
        # match it. Regenerate -- which is what the engine effectively has
        # anyway, since PHY /PHY4 store no normals at all.
        c3phy.generate_normals(mm)
        has_file_normals = has_file_normals and not motion_applied

    pos: list[float] = []
    nrm: list[float] = []
    uv0: list[float] = []
    uv1: list[float] = []
    col: list[int] = []
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    any_uv1 = False
    any_color = False
    for v in mm.vertices:
        x, y, z = v.px, v.py, -v.pz
        pos += [round(x, 4), round(y, 4), round(z, 4)]
        nrm += [round(v.nx, 4), round(v.ny, 4), round(-v.nz, 4)]
        uv0 += [round(v.u0, 6), round(v.v0, 6)]
        uv1 += [round(v.u1, 6), round(v.v1, 6)]
        if v.u1 or v.v1:
            any_uv1 = True
        c = v.unknown4 & 0xFFFFFFFF
        if c:
            any_color = True
        col.append(c)
        lo[0] = min(lo[0], x); hi[0] = max(hi[0], x)
        lo[1] = min(lo[1], y); hi[1] = max(hi[1], y)
        lo[2] = min(lo[2], z); hi[2] = max(hi[2], z)

    idx: list[int] = []
    nv = len(mm.vertices)
    for a, b, c in mm.faces:
        if a < nv and b < nv and c < nv:
            idx += [a, b, c]

    if not mm.vertices:
        lo = hi = [0.0, 0.0, 0.0]

    label = _decode_label(mm.label)
    return {
        "index": index,
        "name": mm.name,
        "tag": mm.tag.decode("latin-1").strip(),
        "vertexCount": mm.vertex_count,
        "vertexCountA": mm.vertex_count_a,
        "vertexCountB": mm.vertex_count_b,
        "faceCount": mm.face_count,
        "positions": pos,
        "normals": nrm,
        "uv0": uv0,
        "uv1": uv1 if any_uv1 else [],
        "colors": col if any_color else [],
        "indices": idx,
        "bboxRender": [lo, hi],
        "bboxDeclared": [list(mm.bbox_min), list(mm.bbox_max)],
        "label": label,
        "labelRaw": mm.label,
        "isC3ExpColor": mm.is_c3exp_color,
        "twoSided": mm.two_sided,
        "billboard": mm.billboard,
        "matrix": [round(x, 6) for x in mm.matrix],
        "matrixIdentity": matrix_identity,
        "motionApplied": motion_applied,
        "frameCount": mm.frame_count,
        "bones": mm.bones,
        "skinned": mm.skinned,
        "normalsFromFile": has_file_normals,
        "keys": {
            "alphas": _c3key_frames(mm.keys.alphas),
            "draws": _c3key_frames(mm.keys.draws),
            "changeTexs": _c3key_frames(mm.keys.change_texs),
        },
        "isSocket": is_socket_chunk(mm.name),
        "trailing": mm.trailing,
    }


def _scene_bounds(scene: dict) -> Optional[dict]:
    """Render-space bounds of every non-socket chunk of a decoded scene.

    Used to state an attachment's size *numerically* -- a sword should read as
    blade-length against a ~170-unit body, and a test can assert that.
    """
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    seen = False
    for m in scene.get("meshes", []):
        if m.get("isSocket") or not m.get("vertexCount"):
            continue
        a, b = m["bboxRender"]
        seen = True
        for i in range(3):
            lo[i] = min(lo[i], a[i])
            hi[i] = max(hi[i], b[i])
    if not seen:
        return None
    return {"min": lo, "max": hi,
            "extent": [hi[i] - lo[i] for i in range(3)],
            "longest": max(hi[i] - lo[i] for i in range(3))}


def c3_to_json(data: bytes, logical: str, *, bake_motion: bool = False,
               frame: int = 0, motion_set=None) -> dict:
    r"""Decode a `.c3` container into the viewport's wire format.

    `bake_motion` applies each chunk's own `MOTI` to its vertices -- stage 2 of
    the attachment chain.  Off by default: asset view shows a mesh as authored.

    `motion_set` is an external `attach.PartMesh` whose chunk *i* overrides the
    motion of chunk *i* -- `C3Mesh::SetMotion` (`0x277C0`), used to pose a body
    with its idle action motion from `ini/3dmotion.ini` instead of the pose the
    artist happened to save in the mesh.  `Mesh::Draw` always applies a motion
    (`0x2607E`), so there is no bind pose to show; see docs/attachment.md §8.2.

    **The `PHY` -> `MOTI` pairing is ORDINAL, not adjacency.**
    `graphic.dll!MeshCreate` (`0x28360`) builds the phy array and then calls
    `MotionCreate` on the *same file* to build a separate motion array; neither
    reader looks at the other's position, and `Role3D!sub_86C0` uses the phy
    index on the motion set with no remapping.  Pairing a `MOTI` with whichever
    `PHY` happens to precede it is therefore wrong and really does break --
    `c3/mount/850/8500000.c3` stores all eight `PHY` chunks first and all eight
    `MOTI` chunks after them.  `attach.PartMesh.parse` is the authority here and
    is used rather than re-derived.
    """
    c3 = C3File(data, strict=False)   # view like the engine: tolerate a garbage tail
    motions: dict[int, object] = {}
    if (bake_motion or motion_set is not None) and attach is not None:
        try:
            pm = attach.PartMesh.parse(data, logical)
            motions = {c.index: c.motion for c in pm.chunks if c.motion is not None}
            if motion_set is not None:
                for c in pm.chunks:
                    mo = motion_set.motion_for(c.index)
                    if mo is not None:
                        motions[c.index] = mo
        except Exception:                                # pragma: no cover
            motions = {}
    meshes = []
    others = []
    phy_ordinal = 0
    for i, ch in enumerate(c3.chunks):
        if ch.tag in c3phy.VARIANTS:
            try:
                meshes.append(mesh_to_json(c3phy.parse_phy(ch.tag, ch.body), i,
                                           motion=motions.get(phy_ordinal),
                                           frame=frame))
            except Exception as e:
                others.append({"index": i, "tag": ch.name, "size": ch.size,
                               "error": str(e)})
            phy_ordinal += 1
        else:
            others.append({"index": i, "tag": ch.name.strip(), "size": ch.size,
                           "described": ch.described})
    return {"path": logical, "meshes": meshes, "otherChunks": others,
            "chunkCount": len(c3.chunks), "motionBaked": bool(motions)}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, catalog: Optional[Catalog],
                 root: Optional[Path]):
        super().__init__(addr, handler)
        self.catalog = catalog
        self.game_root = root
        #: multi-view state: the COmmunity Library (if configured), which view
        #: is active ("" = the baseline install), and the built catalogues so
        #: switching back is instant. See /api/servers.
        self.library: Optional[Path] = None
        self.server_name: str = ""
        self.views: dict[str, Catalog] = {}
        self.view_lock = threading.Lock()
        #: Comparison bases: plugin name -> Catalog, one per *install the
        #: user has declared*, built on first use and then kept. Distinct
        #: from `views`, which is one install seen through different library
        #: servers; these are different installs entirely.
        #:
        #: Holding two at once is the point. "Is this a regression or a
        #: difference between clients" cannot be answered from one of them,
        #: and answering it by restarting the viewer against the other loses
        #: the model, the action and the frame you were looking at. See
        #: /api/bases.
        self.base_views: dict[str, Catalog] = {}
        self.base_name: str = ""
        self.tex_cache: dict[tuple, bytes] = {}
        self.rows_cache: dict[str, list] = {}
        self.cache_lock = threading.RLock()
        self.tags = TagStore(TAGS_FILE)
        #: None until an install is known -- the setup page runs without one.
        self.thumbs = ThumbRunner(PROJECT, root) if root else None
        #: Per-run CSRF token. New on every start, held only in memory, never
        #: written to disk -- a token in a file is a token that outlives the
        #: run it authorised. See `Handler._csrf_reason`.
        self.csrf_token = secrets.token_urlsafe(32)


def _json_bytes(obj) -> bytes:
    r"""Serialise, and never emit a token JavaScript cannot read.

    Python writes `NaN` and `Infinity` for non-finite floats; **JSON has no
    such literals and `JSON.parse` throws on them**, so one degenerate
    vertex normal takes down a whole page -- the fetch rejects, the page
    keeps its old stage, and the user reports "no motion ships, no model
    displays" for a model the server rendered perfectly. Monsters 321, 322,
    323 and 808 were exactly that: 18 NaN in 321's normals out of 30 frames
    of otherwise sound geometry.

    Non-finite numbers become 0.0 rather than null: every consumer of these
    payloads is arithmetic (positions, normals, matrices) and a null would
    only move the failure into the renderer. A zero normal shades flat,
    which is what a degenerate normal deserves.
    """
    def clean(o):
        if isinstance(o, float):
            return o if math.isfinite(o) else 0.0
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        return o
    return json.dumps(clean(obj), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "coviewer"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):        # quieter default log
        if os.environ.get("COVIEWER_VERBOSE"):
            super().log_message(fmt, *args)

    @property
    def cat(self) -> Optional[Catalog]:
        """None in setup mode -- no install has been located yet."""
        return self.server.catalog                    # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Local-only tool; keep the browser from reaching anywhere else at all.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; "
                         "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'")
        # Never pool a POST connection.
        #
        # With HTTP/1.1 keep-alive the browser reuses idle connections. If one
        # has gone away, Chrome silently retries the request; Firefox does NOT
        # retry a non-idempotent method and surfaces it as a bare
        # "NetworkError when attempting to fetch resource". Every mutation
        # here is a POST, so in Firefox the failure lands exactly on the
        # buttons and nowhere else -- which is what was reported. Closing the
        # connection after a POST keeps it out of the pool entirely.
        if self.command == "POST":
            self.send_header("Connection", "close")
            self.close_connection = True
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

    def _error(self, code: int, msg: str):
        self._json({"error": msg}, code)

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        try:
            self._route()
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._error(500, traceback.format_exc(limit=3))
            except Exception:
                pass

    do_HEAD = do_GET

    # -- CSRF --------------------------------------------------------------
    #
    # WHY THIS EXISTS
    # ---------------
    # Every server in this project binds loopback only, and that is
    # test-enforced. It is easy to conclude from that that nothing hostile can
    # reach it, and that conclusion is wrong: the attacker is not on the
    # network, it is **a tab in your own browser**. A page on any origin can
    # cause your browser to POST to 127.0.0.1:8731, and before this gate that
    # was enough to stage a file and install it into the game directory.
    #
    # Note what this is NOT about: TLS. A certificate, self-signed or
    # otherwise, changes nothing here, because the attacking page never needs
    # to read the connection -- it only needs the browser to make the request.
    # So the defence is proving the request came from our own page, twice over:
    #
    #   1. `Origin` / `Referer` -- browsers attach `Origin` to cross-origin
    #      POSTs, and a page cannot forge it. A foreign origin is refused
    #      whatever else the request carries.
    #   2. A per-run token -- served only inside our own HTML, which a
    #      cross-origin page cannot read (the same-origin policy makes the
    #      response opaque even when the request succeeds).
    #
    # A request with no `Origin` and no `Referer` at all is not a browser doing
    # a cross-site POST, so it is allowed to pass on the token alone. That is
    # what keeps `curl` and the CLI usable; it is not a hole, because a local
    # process that wanted to write these files could do it directly and needs
    # no help from this HTTP server.

    #: POSTs allowed with no token. `/api/shot` only writes a PNG the page
    #: already rendered into `out/viewer/shots/`, and the page fetches it
    #: before the token script has necessarily run on the setup flow.
    CSRF_EXEMPT: tuple = ()

    def _allowed_origins(self) -> set:
        """The origins our own page can legitimately be served from.

        Built from the `Host` header rather than from configuration: a browser
        sets `Host` to whatever it connected to, and an attacking page cannot
        change it. So `Host` describes us, while `Origin` describes the caller
        -- comparing the two is the whole check.
        """
        host = self.headers.get("Host", "")
        out = {f"http://{host}", f"https://{host}"}
        # The same server reached as localhost and as 127.0.0.1 is the same
        # server; a page served from one must not be refused for naming the
        # other.
        if ":" in host:
            _, _, port = host.rpartition(":")
            for h in ("127.0.0.1", "localhost", "[::1]"):
                out.add(f"http://{h}:{port}")
        return out

    def _csrf_reason(self, path: str) -> Optional[str]:
        """None if this POST may proceed, else why it may not."""
        if path in self.CSRF_EXEMPT:
            return None
        origin = self.headers.get("Origin")
        if not origin:
            ref = self.headers.get("Referer")
            if ref:
                u = urllib.parse.urlparse(ref)
                origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None
        if origin is not None and origin not in self._allowed_origins():
            return (f"cross-origin POST from {origin} refused. This request did "
                    f"not come from the viewer's own page. If you are seeing "
                    f"this in normal use, report it -- it means a page you did "
                    f"not write tried to drive your local game tools.")

        want = getattr(self.server, "csrf_token", None)
        if not want:
            return None                     # server predates the token
        got = (self.headers.get("X-CO-Token")
               or urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
               .get("csrf", [""])[0])
        # Constant-time compare: the token is a secret and a length-or-prefix
        # timing signal is free to remove.
        if got and secrets.compare_digest(got, want):
            return None
        return ("missing or wrong CSRF token. The viewer's own pages send it "
                "automatically; a script should read it from GET /api/token "
                "and send it as the X-CO-Token header.")

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            bad = self._csrf_reason(path)
            if bad:
                _log(f"refused POST {path}: {bad.splitlines()[0]}")
                # Drain the body first, or HTTP/1.1 keep-alive desyncs and the
                # next request on this connection reads our leftovers.
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                return self._error(403, bad)
            self._route(post=True)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._error(500, traceback.format_exc(limit=3))
            except Exception:
                pass

    def _route(self, post: bool = False):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path

        def arg(name, default=""):
            return q.get(name, [default])[0]

        if post:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            handler = {
                "/api/preview": self.post_preview,
                "/api/stage": self.post_stage,
                "/api/unstage": self.post_unstage,
                "/api/install": self.post_install,
                "/api/uninstall": self.post_uninstall,
                "/api/snapshot": self.post_snapshot,
                "/api/tags": self.post_tags,
                "/api/server": self.post_server,
                "/api/base": self.post_base,
                "/api/setlibrary": self.post_setlibrary,
                "/api/ping": self.post_ping,
                # "/collect" is the Google Analytics Measurement Protocol
                # path, and ad/tracker blockers ship rules that match it by
                # name. A local tool asking to keep an asset was being
                # blocked as telemetry -- in Firefox with a blocker the
                # request never left the page, while /api/ping beside it
                # went through. The keep/* names are unremarkable to a
                # filter list. The old paths stay as aliases so an
                # already-open page keeps working.
                "/api/keep/add": self.post_collect,
                "/api/keep/remove": self.post_uncollect,
                "/api/keep/stage": self.post_collect_stage,
                "/api/collect": self.post_collect,
                "/api/uncollect": self.post_uncollect,
                "/api/collect/stage": self.post_collect_stage,
                "/api/setroot": self.post_setroot,
                "/api/thumbs/start": self.post_thumbs_start,
                "/api/thumbs/cancel": self.post_thumbs_cancel,
                "/api/thumbs/decision": self.post_thumbs_decision,
                "/api/mapedit/passability": self.post_mapedit_passability,
            }.get(path)
            if not handler:
                return self._error(404, f"no POST route {path}")
            return handler(body, arg)

        # Setup mode: no install was found, so the catalogue does not exist.
        # Everything except the setup page and the health API would 500, so
        # serve the page that explains it instead of failing obscurely.
        if self.cat is None:
            if path in ("/", "/index.html", "/setup", "/setup.html"):
                return self._static("setup.html")
            if path.startswith("/ui/"):
                return self._static(path[4:])
            if path == "/api/health":
                return self.api_health(arg)
            # The setup page POSTs /api/setroot, so its token must be reachable
            # before an install exists.
            if path == "/api/token":
                return self.api_token(arg)
            return self._error(503, "no game install configured yet -- "
                                    "open http://" + self.headers.get("Host", "")
                                    + "/setup")

        if path == "/" or path == "/index.html":
            return self._static("index.html")
        # The character builder is its own page, not a panel on the browser.
        if path in ("/builder", "/builder/", "/builder.html"):
            return self._static("builder.html")
        # ...and so is the MapEditor: a map wants the whole window, and the
        # browser's three-pane layout is built around a 3D viewport.
        if path in ("/mapedit", "/mapedit/", "/mapedit.html"):
            return self._static("mapedit.html")
        if path.startswith("/ui/"):
            return self._static(path[4:])

        routes = {
            "/api/status": self.api_status,
            "/api/servers": self.api_servers,
            "/api/bases": self.api_bases,
            "/api/library": self.api_library,
            "/api/keep": self.api_collection,
            "/api/collection": self.api_collection,
            "/api/tables": self.api_tables,
            "/api/appearances": self.api_appearances,
            "/api/appearance": self.api_appearance,
            "/api/dirs": self.api_dirs,
            "/api/files": self.api_files,
            "/api/provenance": self.api_provenance,
            "/api/mesh": self.api_mesh,
            "/api/texture": self.api_texture,
            "/api/rawinfo": self.api_rawinfo,
            "/api/stage": self.api_stage_list,
            "/api/installs": self.api_installs,
            "/api/skintarget": self.api_skintarget,
            "/api/diff": self.api_diff,
            "/api/bodyfacets": self.api_bodyfacets,
            "/api/categories": self.api_categories,
            "/api/catfiles": self.api_catfiles,
            "/api/maps": self.api_maps,
            "/api/map": self.api_map,
            "/api/mapedit/maps": self.api_mapedit_maps,
            "/api/mapedit/map": self.api_mapedit_map,
            "/api/mapedit/tile": self.api_mapedit_tile,
            "/api/mapedit/pick": self.api_mapedit_pick,
            "/api/mapedit/editable": self.api_mapedit_editable,
            "/api/related": self.api_related,
            "/api/components": self.api_components,
            "/api/thumb": self.api_thumb,
            "/api/parts": self.api_parts,
            "/api/figure": self.api_figure,
            "/api/builder": self.api_builder,
            "/api/options": self.api_options,
            "/api/option": self.api_option,
            "/api/superfx": self.api_superfx,
            "/api/quality": self.api_quality,
            "/api/anim": self.api_anim,
            "/api/actions": self.api_actions,
            "/api/models": self.api_models,
            "/api/model": self.api_model,
            "/api/modelanim": self.api_modelanim,
            "/api/meshanim": self.api_meshanim,
            "/api/monsterrows": self.api_monsterrows,
            "/api/effect": self.api_effect,
            "/api/effects": self.api_effects_for,
            "/api/weaponmotion": self.api_weaponmotion,
            "/api/tags": self.api_tags,
            "/api/tags/export": self.api_tags_export,
            "/api/health": self.api_health,
            "/api/thumbs/status": self.api_thumbs_status,
            "/api/token": self.api_token,
            "/api/plugins": self.api_plugins,
        }
        fn = routes.get(path)
        if not fn:
            return self._error(404, f"no route {path}")
        return fn(arg)

    def api_token(self, arg):
        """This run's CSRF token, for scripts and for `curl`.

        Safe to serve over GET despite being a secret, and the reason is worth
        stating because it looks wrong at first glance: a page on another origin
        *can* issue this request, but the same-origin policy makes the response
        **opaque** to it -- it cannot read the body. So a hostile page learns
        nothing, while `curl` and the operator's own scripts, which are not
        bound by that policy, read it fine.

        A local process could also read it -- and that is not a weakening,
        because a local process running as this user can already write every
        file the viewer could write, without asking the viewer.
        """
        return self._json({"token": getattr(self.server, "csrf_token", ""),
                           "header": "X-CO-Token",
                           "note": "send this as the X-CO-Token header on POSTs; "
                                   "it changes every time the server restarts"})

    # -- static ------------------------------------------------------------
    def _static(self, rel: str):
        try:
            p = safepath.confine(WEBUI, rel)
        except safepath.UnsafePath as e:
            return self._error(403, str(e))
        if not p.is_file():
            return self._error(404, f"missing {rel}")
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        body = p.read_bytes()
        if ctype.startswith("text/html"):
            body = self._inject_csrf(body)
        self._send(200, body, ctype)

    def _inject_csrf(self, page: bytes) -> bytes:
        """Put this run's token, and the script that uses it, into the page.

        Injected at serve time rather than written into the six `.html` files
        because the token changes every run and must never be on disk. The
        script tag is a *reference*, not inline code, so the existing
        `script-src 'self'` CSP still holds -- no `unsafe-inline`, no nonce.

        `csrf.js` wraps `fetch`, so every existing POST in the UI keeps working
        untouched. The alternative was editing every call site across five JS
        files, which is the version that misses one.
        """
        token = getattr(self.server, "csrf_token", "")
        if not token:
            return page
        tag = (f'<meta name="co-csrf" content="{html.escape(token, quote=True)}">'
               f'<script src="/ui/csrf.js"></script>').encode("utf-8")
        low = page.lower()
        at = low.find(b"<head>")
        if at >= 0:
            return page[:at + 6] + tag + page[at + 6:]
        # No <head>: put it before the first script so the wrapper is installed
        # before anything can call fetch.
        at = low.find(b"<script")
        if at >= 0:
            return page[:at] + tag + page[at:]
        return tag + page

    # -- API: status / catalogue -------------------------------------------
    def api_status(self, arg):
        c = self.cat
        arcs = {name: {"entries": len(a.entries), "bytes": a.file_size}
                for name, a in c.assets._archives.items()}
        return self._json({
            "root": str(c.root),
            "server": getattr(self.server, "server_name", ""),
            "serverTag": c.server_tag,
            "serverUnique": len(c.unique_paths),
            "library": (str(self.server.library)          # type: ignore[attr-defined]
                        if getattr(self.server, "library", None) else None),
            "archives": arcs,
            "looseFiles": len(c.loose),
            "recoveredNames": len(c.names),
            "knownPaths": len(c.all_paths),
            "tablesReady": c._tables_ready.is_set(),
            "tables": sorted(c.tables.keys()),
            "numpy": dds.HAVE_NUMPY,
            "stageDir": str(STAGE),
            "stagedCount": len(_staged_files()),
            "installed": (PROJECT / "mods" / "manifest.json").is_file(),
        })

    # -- API: comparison bases -----------------------------------------------
    def _declared_bases(self) -> list:
        """Every install the user has declared, newest declaration last.

        The list is the user's own `coroot.declare_kind` record, not a scan:
        a folder is a client because they said so, and the same rule that
        picks a parse profile decides what is worth comparing against. Add
        one by pointing the setup page at it; nothing here needs editing.

        Roots that have gone away are dropped rather than offered and then
        failing on click.
        """
        import plugins as plugmod
        out = []
        for path, kind in (coroot.read_settings().get(coroot.KINDS_KEY)
                           or {}).items():
            p = Path(str(path))
            if not p.is_dir() or coroot.missing_parts(p):
                continue
            plug = plugmod.for_kind(str(kind))
            out.append({
                "name": plug.name if plug else str(kind),
                "label": plug.label if plug else str(kind),
                "root": str(p),
                # Whether this base can answer at all yet. A base with no
                # index is not broken, it is unbuilt -- and saying which is
                # the difference between "go build it" and "go debug it".
                "indexed": coroot.find_derived(
                    "out/meshtex/coverage.json", p) is not None,
                "baseId": coroot.base_id(p),
            })
        out.sort(key=lambda d: d["name"])
        return out

    def api_bases(self, arg):
        """**The** selector: every asset path loaded, as one flat list.

        There is one question on screen -- *what am I looking at* -- so there
        is one control. An install and a COmmunity Library server are both
        just a path the viewer can draw from; splitting them into "client"
        and "server" made the user carry a distinction that is ours, not
        theirs, and left two dropdowns whose legal combinations they had to
        work out.

        The parser plugin is **not** in this list, because it is not a
        choice. It follows the path: `Catalog.plugin` resolves it through
        `coroot.kind_for_root`, and everything downstream -- which tables are
        authoritative, where colour lives in an id, which socket a slot hangs
        off, whether a colour set is measured or inherited -- follows from
        that. Picking a path is the only decision.

        Each entry carries `id` (what to POST back), `kind` (`install` or
        `library`), and enough to say what it is. Switching keeps the model,
        action and frame, which is what makes "regression, or difference
        between clients?" answerable at all.
        """
        srv = self.server
        entries = []
        for b in self._declared_bases():
            entries.append({
                "id": "install:" + b["name"], "kind": "install",
                "label": b["label"], "detail": b["root"],
                "plugin": b["name"], "ready": b["indexed"],
                "note": "" if b["indexed"] else "no index built yet",
            })
        lib = srv.library                              # type: ignore[attr-defined]
        if lib:
            list_servers = _core_colibrary().list_servers
            for name in list_servers(lib):
                prof = {}
                pf = Path(lib) / "servers" / name / "profile.json"
                try:
                    if pf.is_file():
                        prof = json.loads(pf.read_text("utf-8"))
                except ValueError:
                    pass
                bits = []
                if prof.get("clientVersion"):
                    bits.append("v" + str(prof["clientVersion"]))
                if prof.get("files"):
                    bits.append(f"{prof['files']:,} files")
                entries.append({
                    "id": "library:" + name, "kind": "library",
                    "label": name + " (library)",
                    "detail": ", ".join(bits) or str(Path(lib) / "servers" / name),
                    "plugin": "", "ready": True, "note": "",
                })
        if srv.server_name:                            # type: ignore[attr-defined]
            cur = "library:" + srv.server_name         # type: ignore[attr-defined]
        else:
            cur = "install:" + (srv.base_name or       # type: ignore[attr-defined]
                                coroot.kind_for_root(srv.game_root) or "")  # type: ignore[attr-defined]
        return self._json({"paths": entries, "current": cur})

    def post_base(self, body, arg):
        """Switch the active path, by the `id` `/api/bases` handed out.

        Takes `install:<plugin>` or `library:<name>` and dispatches; a
        library entry is delegated to `post_server`, which already knows how
        to open one over the current install. One control on screen, one
        endpoint behind it.

        Validated against the list rather than taking a directory: a POST
        that can open an arbitrary path as an install is a different and much
        larger thing than a picker.

        **Deliberately not persisted.** Flipping between paths to answer
        "regression, or difference between the clients?" is something you do
        several times a minute; writing the config on each flip would turn a
        comparison into a change of settings. The saved root stays whatever
        the setup page last stored, and a restart returns there.
        """
        srv = self.server
        try:
            req = json.loads(body.decode("utf-8") or "{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        ident = str(req.get("path") or req.get("base") or "").strip()
        if ident.startswith("library:"):
            return self.post_server(
                json.dumps({"server": ident[len("library:"):]}).encode(), arg)
        name = ident[len("install:"):] if ident.startswith("install:") else ident
        bases = {b["name"]: b for b in self._declared_bases()}
        if name not in bases:
            return self._json({
                "ok": False, "base": name,
                "error": f"no declared install named {name!r}. Declared: "
                         + (", ".join(sorted(bases)) or "none")
                         + ". Add one on the setup page."}, 400)
        target = bases[name]
        with srv.view_lock:                            # type: ignore[attr-defined]
            if name == (srv.base_name or ""):          # type: ignore[attr-defined]
                return self._json({"ok": True, "current": name,
                                   "unchanged": True})
            cat = srv.base_views.get(name)             # type: ignore[attr-defined]
            if cat is None:
                try:
                    cat = Catalog(Path(target["root"]))
                except Exception as e:                 # pragma: no cover
                    return self._error(500,
                                       f"could not open base {name!r}: {e}")
                srv.base_views[name] = cat             # type: ignore[attr-defined]
            srv.catalog = cat                          # type: ignore[attr-defined]
            srv.game_root = cat.root                   # type: ignore[attr-defined]
            srv.base_name = name                       # type: ignore[attr-defined]
            # Library views belong to the install they were opened over, and
            # the base install is now a different one.
            srv.views = {"": cat}                      # type: ignore[attr-defined]
            srv.server_name = ""                       # type: ignore[attr-defined]
            # Same logical path, different bytes -- exactly the reason these
            # caches are cleared when a library view changes.
            with srv.cache_lock:                       # type: ignore[attr-defined]
                srv.tex_cache.clear()                  # type: ignore[attr-defined]
                srv.rows_cache.clear()                 # type: ignore[attr-defined]
        _log(f"base -> {name} ({target['root']}, index {target['baseId']})")
        return self._json({"ok": True, "current": name,
                           "root": target["root"],
                           "plugin": cat.plugin.name,
                           "indexed": target["indexed"]})

    # -- API: server views ---------------------------------------------------
    def api_servers(self, arg):
        """The selectable asset sources: the baseline install plus every
        server profile catalogued in the COmmunity Library."""
        srv = self.server
        lib = srv.library                              # type: ignore[attr-defined]
        servers = []
        if lib:
            list_servers = _core_colibrary().list_servers
            for name in list_servers(lib):
                prof = {}
                pf = Path(lib) / "servers" / name / "profile.json"
                try:
                    if pf.is_file():
                        prof = json.loads(pf.read_text("utf-8"))
                except ValueError:
                    pass
                servers.append({"name": name,
                                "files": prof.get("files"),
                                "clientVersion": prof.get("clientVersion"),
                                "importedAt": prof.get("importedAt"),
                                "client": prof.get("client")})
        return self._json({
            "current": srv.server_name,                # type: ignore[attr-defined]
            "library": str(lib) if lib else None,
            "base": str(srv.game_root) if srv.game_root else None,
            "servers": servers})

    # -- API: the curated collection ---------------------------------------
    def _collection(self):
        lib = getattr(self.server, "library", None)      # type: ignore[attr-defined]
        if not lib:
            return None
        from collection import Collection
        return Collection(lib)

    def _refresh_collection_view(self) -> dict:
        """Make a change to the Collection visible without a restart.

        `Collection.save` rewrites the server profile, but the viewer caches
        one built `Catalog` per server for the life of the process -- that is
        what makes flipping between clients cheap. The Collection is the one
        view this process also *writes*, so its cached catalogue is the one
        that can be wrong, and it was: everything you kept landed on disk and
        none of it appeared in the library until the viewer was restarted.

        Dropped rather than rebuilt, unless you are looking at it. Rebuilding
        a view nobody has open spends a catalogue build on nothing.
        """
        from collection import PROFILE_NAME
        srv = self.server
        out = {"server": PROFILE_NAME, "rebuilt": False}
        if not getattr(srv, "library", None) or srv.game_root is None:  # type: ignore[attr-defined]
            return out
        with srv.view_lock:                            # type: ignore[attr-defined]
            srv.views.pop(PROFILE_NAME, None)          # type: ignore[attr-defined]
            if srv.server_name != PROFILE_NAME:        # type: ignore[attr-defined]
                return out
            try:
                ServerView = _core_colibrary().ServerView
                view = ServerView(srv.library, PROFILE_NAME, srv.game_root)
                cat = Catalog(srv.game_root, view)
            except Exception as e:                     # pragma: no cover
                # The old catalogue is stale but valid; keep serving it and
                # say so, rather than leaving the view with nothing.
                _log(f"collection view rebuild failed: {e}")
                out["error"] = str(e)
                return out
            srv.views[PROFILE_NAME] = cat              # type: ignore[attr-defined]
            srv.catalog = cat                          # type: ignore[attr-defined]
            with srv.cache_lock:                       # type: ignore[attr-defined]
                srv.tex_cache.clear()                  # type: ignore[attr-defined]
                srv.rows_cache.clear()                 # type: ignore[attr-defined]
            out["rebuilt"] = True
            out["knownPaths"] = len(cat.all_paths)
        return out

    def post_ping(self, body: bytes, arg):
        """A POST that does nothing, so a failing POST can be told apart
        from a failing feature.

        `collect failed: NetworkError` says the request never completed, and
        that has two very different causes: the server was not there, or
        something between the page and the server refused a POST to loopback
        (security software and some extensions do exactly that, and only to
        non-GET). This route answers the second question directly.
        """
        return self._json({"ok": True, "pong": True,
                           "origin": self.headers.get("Origin", ""),
                           "host": self.headers.get("Host", "")})

    def api_collection(self, arg):
        """What has been collected, and the shelves available."""
        from collection import CATEGORIES
        col = self._collection()
        if col is None:
            return self._json({"library": None, "categories": CATEGORIES,
                               "entries": [], "counts": {},
                               "why": "no COmmunity Library configured"})
        return self._json({"library": str(col.library),
                           "categories": CATEGORIES,
                           "counts": col.counts(),
                           "entries": col.entries})

    def post_collect(self, body: bytes, arg):
        r"""Copy one asset into the collection.

        The bytes come from the *active view*, so whatever the browser is
        showing is what gets collected -- including assets recovered from a
        community archive, which exist in no catalogue.
        """
        col = self._collection()
        if col is None:
            return self._error(400, "no COmmunity Library configured")
        try:
            doc = json.loads(body or b"{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        mesh = str(doc.get("path") or "").replace("\\", "/")
        if not mesh:
            return self._error(400, "path required")
        dry = bool(doc.get("dryRun"))
        # Logged on arrival, so a failure in the browser can be told apart
        # from a request that never got here. If this line is absent when a
        # Collect fails, nothing server-side is at fault.
        _log(f"collect{' (dry run)' if dry else ''}: {mesh} "
             f"from {self.headers.get('Origin', '?')}")
        category = str(doc.get("category") or "Other")
        c = self.cat
        try:
            mesh_bytes = c.read(mesh)
        except FileNotFoundError:
            return self._error(404, f"not found: {mesh}")

        tex = str(doc.get("texture") or "") or (c.texture_for_mesh(mesh) or "")
        skins = []
        if tex:
            try:
                skins.append((Path(tex).name, c.read(tex)))
            except FileNotFoundError:
                tex = ""
        from collection import gather_parts
        parts = []
        if doc.get("withParts", True):
            # Effects are resolved here rather than being left to the caller:
            # collecting a weapon without its aura gives you a weapon that
            # looks wrong in game, and the browser had no way to pass them.
            fx = [str(x) for x in (doc.get("effects") or [])]
            if not fx:
                fx = self._effects_for_asset(mesh)
            parts = gather_parts(c.read, c.list_under, mesh, effects=fx)
        if dry:
            # Everything the real path does except the writing, so a failing
            # Collect can be told apart from a failing *request*.
            return self._json({"ok": True, "dryRun": True, "mesh": mesh,
                               "texture": tex, "meshBytes": len(mesh_bytes),
                               "skins": len(skins), "parts": len(parts),
                               "category": category})
        try:
            entry = col.add(
                category=category, name=str(doc.get("name") or ""),
                mesh_bytes=mesh_bytes, mesh_name=Path(mesh).name, skins=skins,
                server=getattr(self.server, "server_name", ""),
                source_mesh=mesh, source_texture=tex,
                swap_for=str(doc.get("swapFor") or ""),
                note=str(doc.get("note") or ""), parts=parts)
        except Exception as e:
            return self._error(400, str(e))
        _log(f"collect: wrote {entry['id']} "
             f"({len(entry.get('parts', []))} part(s))")
        return self._json({"ok": True, "entry": entry,
                           "counts": col.counts(),
                           "profile": col.profile,
                           "view": self._refresh_collection_view()})

    def post_uncollect(self, body: bytes, arg):
        col = self._collection()
        if col is None:
            return self._error(400, "no COmmunity Library configured")
        try:
            doc = json.loads(body or b"{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        ident = str(doc.get("id") or "")
        ok = col.remove(ident)
        if not ok:
            return self._json({"ok": False, "counts": col.counts()})
        return self._json({"ok": True, "counts": col.counts(),
                           "profile": col.profile,
                           "view": self._refresh_collection_view()})

    def _base_cat(self):
        """The baseline catalogue, whatever view is being browsed.

        A swap TARGET is a path in the install being modded -- never in the
        view you happen to have open. Resolving it against the active view
        was wrong in exactly the case that matters: picking a collected entry
        requires browsing the Collection, where the target path does not
        exist, so the resolver found nothing and staging fell back to the
        DONOR's layout. A Zephyr NPC (skin beside its mesh) staged over the
        flat family then put its skin at `c3/npc/999001100.dds`, which
        nothing reads.
        """
        srv = self.server
        cat = srv.views.get("")                          # type: ignore[attr-defined]
        if cat is None and srv.game_root is not None:    # type: ignore[attr-defined]
            with srv.view_lock:                          # type: ignore[attr-defined]
                cat = srv.views.get("")                  # type: ignore[attr-defined]
                if cat is None:
                    cat = Catalog(srv.game_root)         # type: ignore[attr-defined]
                    srv.views[""] = cat                  # type: ignore[attr-defined]
        return cat if cat is not None else self.cat

    def _skin_target(self, target: str, ident: str = "") -> str:
        """Where the replacement skin belongs under ``target``.

        The rule itself lives in `collection.skin_destination`, so the CLI
        answers this identically. Writing it twice is how the action-code
        rule went wrong, and that was three commits ago. What this adds is
        the viewer's own resolver -- the authored pairings the CLI has no
        table for -- and the baseline catalogue to check existence against.
        """
        from collection import skin_destination
        cat = self._base_cat()
        if cat is None:
            return ""
        return skin_destination(target, cat.exists,
                                authored=cat.texture_for_mesh,
                                tables=cat.npc_tables)

    def api_skintarget(self, arg):
        """The destination the Replace panel pre-fills, so the derived answer
        is visible and editable rather than applied invisibly."""
        target = str(arg("target", "") or "")
        return self._json({"target": target,
                           "skinTo": self._skin_target(target)})

    def post_collect_stage(self, body: bytes, arg):
        """Stage a collected entry over the asset it replaces.

        `skin` and `roles` choose what travels with the geometry, because
        "replace this model" and "replace this model, its skin and its whole
        action set" are different edits and only the second was possible.

        Writes into `mods/stage/` only. `comod.py install` is still the one
        thing that touches the game, with its backups and revert manifest.
        """
        col = self._collection()
        if col is None:
            return self._error(400, "no COmmunity Library configured")
        try:
            doc = json.loads(body or b"{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        roles = doc.get("roles")
        if roles is None:
            roles = ["motion", "effect", "sound"]
        skin_to = str(doc.get("skinTo") or "")
        if not skin_to and doc.get("skin", True):
            # Where does the skin go? The answer that is always right is
            # "where the TARGET's own skin already is" -- the game looks
            # there for it, whatever the family's naming convention happens
            # to be. Deriving it by string arithmetic works only when the
            # skin sits beside the mesh; asking the resolver works for the
            # flat NPC family too, where the texture lives in c3/texture/
            # and is named by look, and where you previously had to type
            # the destination yourself.
            skin_to = self._skin_target(str(doc.get("swapFor") or ""),
                                        str(doc.get("id") or ""))
        # What the client currently keeps at each destination, read from the
        # INSTALL rather than the open view -- the same reason `_skin_target`
        # does. It decides whether an action file has to carry geometry.
        base = self._base_cat()

        def _read_target(logical):
            try:
                return base.read(logical) if base is not None else None
            except Exception:
                return None

        try:
            res = col.stage(str(doc.get("id") or ""), STAGE,
                            swap_for=str(doc.get("swapFor") or ""),
                            skin=bool(doc.get("skin", True)),
                            skin_to=skin_to,
                            roles=[str(r) for r in roles],
                            read_target=_read_target)
        except Exception as e:
            return self._error(400, str(e))
        _log(f"stage: {res['id']} -> {res['target']} "
             f"({len(res['wrote'])} file(s))")
        # The tree it landed in, returned with the result rather than only in
        # the drawer: a list of logical paths says where the GAME will look,
        # which is not the same question as where the files now are.
        return self._json({"ok": True, "stageDir": str(STAGE), **res})

    def api_library(self, arg):
        """The COmmunity Library: where it is, what is in it, and -- when
        none is set -- which folders on this machine look like one.

        Discovery is what makes the folder chooser usable: pasting a path is
        the fallback, not the first thing asked of you.
        """
        from colibrary import discover_libraries, library_info
        srv = self.server
        cur = getattr(srv, "library", None)
        info = library_info(cur) if cur else None
        found = []
        if arg("discover", "1") != "0":
            try:
                found = discover_libraries()
            except Exception:                            # pragma: no cover
                found = []
        if info and info["ok"]:
            found = [f for f in found
                     if f["path"].lower() != info["path"].lower()]
        return self._json({"library": info, "candidates": found,
                           "current": getattr(srv, "server_name", "")})

    def post_setlibrary(self, body: bytes, arg):
        r"""Point the viewer at a COmmunity Library folder, or clear it.

        Validated by contents (a `servers/<name>/filemap.json` must exist) and
        remembered in the per-user config, so this is a one-time step rather
        than a flag to retype on every launch.
        """
        from colibrary import library_info
        try:
            doc = json.loads(body.decode("utf-8") or "{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        srv = self.server
        raw = str(doc.get("path") or "").strip().strip('"')
        if not raw:                                   # explicit "forget it"
            coroot.write_settings(community_library="")
            with srv.view_lock:                       # type: ignore[attr-defined]
                srv.library = None                    # type: ignore[attr-defined]
                if srv.server_name:                   # type: ignore[attr-defined]
                    base = srv.views.get("")          # type: ignore[attr-defined]
                    if base is None and srv.game_root:
                        base = Catalog(srv.game_root)
                        srv.views[""] = base          # type: ignore[attr-defined]
                    srv.catalog = base                # type: ignore[attr-defined]
                    srv.server_name = ""              # type: ignore[attr-defined]
            return self._json({"ok": True, "library": None})
        info = library_info(raw)
        if not info["ok"]:
            return self._error(400,
                               f"{raw} is not a COmmunity Library: "
                               + (info["why"] or "no server profiles"))
        saved = coroot.write_settings(community_library=info["path"])
        with srv.view_lock:                           # type: ignore[attr-defined]
            srv.library = Path(info["path"])          # type: ignore[attr-defined]
            # Views built against the previous library are stale; the baseline
            # catalogue is not, so keep it and drop the rest.
            base = srv.views.get("")                  # type: ignore[attr-defined]
            srv.views = {"": base} if base else {}    # type: ignore[attr-defined]
            if srv.server_name and srv.game_root:     # type: ignore[attr-defined]
                if base is None:
                    base = Catalog(srv.game_root)
                    srv.views[""] = base              # type: ignore[attr-defined]
                srv.catalog = base                    # type: ignore[attr-defined]
                srv.server_name = ""                  # type: ignore[attr-defined]
        _log(f"library set to {info['path']} (remembered in {saved})")
        return self._json({"ok": True, "library": info})

    def post_server(self, body: bytes, arg):
        """Switch the active view. Builds a server catalogue on first use and
        caches it, so flipping back and forth costs one build each."""
        srv = self.server
        try:
            req = json.loads(body or b"{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        name = str(req.get("server") or "")
        if srv.game_root is None:                      # type: ignore[attr-defined]
            return self._error(503, "no install configured")
        if name and not srv.library:                   # type: ignore[attr-defined]
            return self._error(400, "no COmmunity Library configured -- "
                               "start the viewer with --library DIR once")
        with srv.view_lock:                            # type: ignore[attr-defined]
            if name == srv.server_name:                # type: ignore[attr-defined]
                return self._json({"ok": True, "current": name,
                                   "unchanged": True})
            cat = srv.views.get(name)                  # type: ignore[attr-defined]
            if cat is None:
                try:
                    if name:
                        ServerView = _core_colibrary().ServerView
                        view = ServerView(srv.library, name, srv.game_root)
                        cat = Catalog(srv.game_root, view)
                    else:
                        cat = Catalog(srv.game_root)
                except Exception as e:
                    return self._error(500,
                                       f"could not open view {name!r}: {e}")
                srv.views[name] = cat                  # type: ignore[attr-defined]
            srv.catalog = cat                          # type: ignore[attr-defined]
            srv.server_name = name                     # type: ignore[attr-defined]
            # decoded-texture and row caches are keyed by logical path, and
            # the same path can mean different bytes in a different view.
            with srv.cache_lock:                       # type: ignore[attr-defined]
                srv.tex_cache.clear()                  # type: ignore[attr-defined]
                srv.rows_cache.clear()                 # type: ignore[attr-defined]
        return self._json({"ok": True, "current": name,
                           "knownPaths": len(cat.all_paths)})

    # -- API: health / setup / thumbnails -----------------------------------
    def api_health(self, arg):
        r"""The first-run check, and the same report `--health` prints.

        Written to `out/health.json` on every call so it can be inspected
        after the fact -- including from a run that failed before the browser
        ever opened.
        """
        rep = health.collect(self.server.game_root)     # type: ignore[attr-defined]
        # The thumbnails block follows the *active view*: with a community
        # server selected, its own cache is what generation would fill.
        active = getattr(self.server, "server_name", "")
        rep["activeServer"] = active
        if active:
            rep["thumbnails"] = health.thumbnail_state(active)
        # Report the provenance of the root this process is *actually* using.
        found = getattr(self.server, "root_found", None)
        if found is not None and rep["install"].get("found"):
            rep["install"]["source"] = found.source
            rep["install"]["detail"] = found.detail
        rep["firstRunPrompt"] = health.should_prompt(rep.get("thumbnails"))
        runner = self.server.thumbs                    # type: ignore[attr-defined]
        rep["thumbnailRun"] = runner.status() if runner else None
        try:
            rep["writtenTo"] = str(health.write_report(rep))
        except OSError as e:
            rep["writtenTo"] = f"(could not write: {e})"
        return self._json(rep)

    #: What each declared client kind must actually contain before it is
    #: believed. The kind picks the parse profile (npcart, tqdat, dbc), so a
    #: wrong declaration would mis-parse every table -- the marker file is
    #: what stops that. Zephyr is declared but not yet importable; leading
    #: with the official 6090 profile is a decision, not an accident.
    #: LEGACY marker map, kept only so an old client of this API keeps
    #: working. The gate below asks the plugin instead: a plugin's
    #: `confidence` is its own claim about what its client looks like, and
    #: duplicating that here would be a second place to keep correct.
    KIND_MARKERS = {
        "official": ("ini/3DSimpleObj.dbc",
                     "official patch clients ship compiled .dbc tables"),
        "cco": ("ini/npc.json", "CCO ships plaintext JSON tables"),
    }

    def _plugins(self):
        sys.path.insert(0, str(PROJECT))
        import plugins as plugmod
        return plugmod

    def api_plugins(self, arg):
        """Every parser plugin the app can see, for the picker.

        Discovered, not listed: a contributor drops a module in `plugins/`
        and it appears here. `suggested` is what the install at `root=`
        looks like to their own `confidence` hooks, so the page can
        pre-select the likely answer without deciding for the user.
        """
        plugmod = self._plugins()
        out = []
        for p in sorted(plugmod.available(), key=lambda x: x.label):
            out.append({"name": p.name, "label": p.label,
                        "notes": getattr(p, "notes", ""),
                        "aliases": list(getattr(p, "aliases", ())),
                        # What differs in FORM rather than content. These are
                        # the failures that never raise, so they are worth
                        # showing rather than burying in a module docstring.
                        "quirks": p.table_quirks(),
                        "fieldWidths": p.key_field_widths(),
                        "auraConvention": p.aura_convention(),
                        "sockets": p.sockets_present() or {}})
        raw = arg("root", "")
        suggested = ""
        if raw:
            r = Path(raw)
            if r.is_dir():
                best = plugmod.detect(r)
                if best.name != plugmod.GENERIC.name:
                    suggested = best.name
        return self._json({"plugins": out, "suggested": suggested,
                           "current": coroot.read_settings().get(
                               "game_kind", "")})

    def post_setroot(self, body: bytes, arg):
        r"""Accept a path and kind from the setup page and remember both.

        Validated by *contents*, not by the string: an install root has to
        hold c3.wdf, data.wdf and ini/, and the declared kind's marker table
        must be present. Saving a path or kind that does not match would
        only move the failure later, into every parse.
        """
        try:
            doc = json.loads(body.decode("utf-8") or "{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        raw = str(doc.get("path") or "").strip().strip('"')
        if not raw:
            return self._error(400, "no path given")
        kind = str(doc.get("kind") or "")
        plugmod = self._plugins()
        plug = plugmod.for_kind(kind)
        if plug is None:
            names = ", ".join(sorted(p.name for p in plugmod.available()))
            return self._json({
                "ok": False, "path": raw,
                "error": f"no parser plugin named {kind!r}. Available: "
                         f"{names}. A plugin is a module in plugins/ -- see "
                         f"docs/parser_plugins.md to add one."}, 400)
        missing = coroot.missing_parts(raw)
        if missing:
            return self._json({
                "ok": False, "path": raw, "missing": missing,
                "error": f"{raw} is not a Conquer Online install: missing "
                         + ", ".join(missing)}, 400)
        # The plugin decides whether this folder is its kind. Asking it (and
        # not a marker list kept here) is the whole point: a plugin owns the
        # claim about what its client looks like, and it is the thing a
        # contributor writes.
        root_p = Path(raw)
        conf = plug.confidence(root_p, lambda q: (root_p / q).is_file())
        if conf <= 0.0:
            best = plugmod.detect(root_p)
            hint = (f" It looks like {best.label} ({best.name})."
                    if best.name != plugmod.GENERIC.name else "")
            return self._json({
                "ok": False, "path": raw,
                "error": f"{raw} does not look like {plug.label}.{hint}"},
                400)
        scope = "repo" if doc.get("scope") == "repo" else "user"
        saved = coroot.save_root(raw, scope)
        # Records the kind against *this* root as well as globally, so
        # switching installs and coming back finds the same index namespace
        # instead of opening a fresh empty one.
        coroot.declare_kind(raw, plug.name)
        _log(f"install root set to {raw} (plugin {plug.name}, "
             f"confidence {conf:.2f}, saved in {saved})")
        return self._json({"ok": True, "path": str(Path(raw)),
                           "kind": plug.name, "plugin": plug.label,
                           "confidence": conf, "savedTo": str(saved),
                           "restartRequired": self.cat is None})

    def api_thumbs_status(self, arg):
        runner = self.server.thumbs                    # type: ignore[attr-defined]
        state = health.thumbnail_state(
            getattr(self.server, "server_name", ""))
        out = {"state": state, "run": runner.status() if runner else None,
               "prompt": health.should_prompt(state)}
        # Generation is incremental: re-read the manifests so a page left open
        # during a run picks up what has landed.
        if self.cat is not None and runner and runner.running():
            try:
                out["manifestEntries"] = self.cat.unified.refresh_thumbs()
            except Exception:                          # pragma: no cover
                pass
        return self._json(out)

    def post_thumbs_start(self, body: bytes, arg):
        runner = self.server.thumbs                    # type: ignore[attr-defined]
        if runner is None:
            return self._error(503, "no install configured")
        try:
            doc = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            doc = {}
        mode = str(doc.get("mode") or arg("mode", "meshes"))
        srv_name = getattr(self.server, "server_name", "")
        srv_lib = getattr(self.server, "library", None)
        try:
            res = runner.start(mode, int(doc.get("jobs") or arg("jobs", 0) or 0),
                               int(doc.get("limit") or arg("limit", 0) or 0),
                               server=srv_name,
                               library=str(srv_lib) if srv_lib else "")
        except ValueError as e:
            return self._error(400, str(e))
        # Remember that they said yes, so the prompt does not come back.
        health.remember_thumbnail_choice(mode)
        return self._json(res)

    def post_thumbs_cancel(self, body: bytes, arg):
        runner = self.server.thumbs                    # type: ignore[attr-defined]
        if runner is None:
            return self._error(503, "no install configured")
        return self._json(runner.cancel())

    def post_thumbs_decision(self, body: bytes, arg):
        """Record "not now" or "never ask again" without starting anything."""
        try:
            doc = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            doc = {}
        choice = str(doc.get("choice") or arg("choice", "later"))
        if choice not in ("later", "never", "meshes", "all", "textures"):
            return self._error(400, f"unknown choice {choice!r}")
        return self._json({"ok": True,
                           "decision": health.remember_thumbnail_choice(choice)})

    def api_tables(self, arg):
        c = self.cat
        c.wait_tables()
        return self._json([{"name": k, "ini": v.name, "count": len(v)}
                           for k, v in sorted(c.tables.items())])

    # -- API: faceted appearance browsing ----------------------------------
    @staticmethod
    def _multi(raw: str) -> set[str]:
        return {x.strip() for x in raw.split(",") if x.strip()}

    def _appearance_rows(self, table: str) -> list[dict]:
        """Every appearance of a table, with its facets and user tags attached.

        Cached per table: this is the list the filters run over on every
        keystroke, so rebuilding it each time would make the UI feel slow.
        """
        c = self.cat
        cache = self.server.rows_cache                    # type: ignore[attr-defined]
        with self.server.cache_lock:                      # type: ignore[attr-defined]
            hit = cache.get(table)
        if hit is not None:
            return hit
        ini = c.tables.get(table)
        if ini is None:
            return []
        facets = c.facets
        out = []
        for app in ini:
            first = app.parts[0] if app.parts else None
            rec = facets.records.get(app.ident) if facets else None
            if rec is None and facets is not None:
                rec = facets.classify(app.ident)
            row = {
                "id": app.ident,
                "subject": f"app:{app.ident}",
                "parts": len(app.parts),
                "texture": c.resolve_id(first.texture, "texture") if first else None,
                "mesh": c.resolve_id(first.mesh, "mesh") if first else None,
                "textureId": first.texture if first else "",
                "meshId": first.mesh if first else "",
            }
            if rec is not None:
                row.update({"class": rec.klass, "gender": rec.gender,
                            "size": rec.size, "kind": rec.kind,
                            "series": rec.series, "bodyType": rec.body_type,
                            "itemName": rec.item_name, "autoTags": rec.auto_tags})
            # An appearance belongs to the community server when the art it
            # resolves to does: the table row is just a reference.
            srv_tags = []
            for logical in (row["mesh"], row["texture"]):
                if logical:
                    srv_tags = c.auto_tags(logical)
                    if srv_tags:
                        break
            row["serverTags"] = srv_tags
            out.append(row)
        with self.server.cache_lock:                      # type: ignore[attr-defined]
            cache[table] = out
        return out

    def api_appearances(self, arg):
        c = self.cat
        c.wait_tables()
        table = arg("table")
        if table not in c.tables:
            return self._error(404, f"unknown table {table!r}")
        rows = self._appearance_rows(table)
        store = self.server.tags                          # type: ignore[attr-defined]
        tag_map = store.all_subjects()

        query = arg("q", "").strip().lower()
        sel = {
            "class": self._multi(arg("class", "")),
            "gender": self._multi(arg("gender", "")),
            "size": self._multi(arg("size", "")),
            "kind": self._multi(arg("kind", "")),
        }
        want_tags = {t.lower() for t in self._multi(arg("tag", ""))}
        untagged = arg("untagged", "") == "1"
        offset = int(arg("offset", "0") or 0)
        limit = min(int(arg("limit", "300") or 300), 2000)
        group = arg("group", "")

        def text_ok(r):
            if not query:
                return True
            hay = f"{r['id']} {r.get('itemName','')} {r.get('mesh') or ''} " \
                  f"{' '.join(tag_map.get(r['subject'], []))}".lower()
            return query in hay

        def tag_ok(r):
            have = tag_map.get(r["subject"], [])
            if untagged and have:
                return False
            # The server tag ("Zephyr") is filterable exactly like a tag you
            # wrote, but it does not make a row count as "tagged" -- untagged
            # still means "I have not labelled this yet".
            searchable = {t.lower() for t in have}
            searchable |= {t.lower() for t in r.get("serverTags", [])}
            return not want_tags or want_tags.issubset(searchable)

        def axis_ok(r, skip=None):
            for axis, want in sel.items():
                if axis == skip or not want:
                    continue
                if r.get(axis) not in want:
                    return False
            return True

        base = [r for r in rows if text_ok(r) and tag_ok(r)]
        matched = [r for r in base if axis_ok(r)]

        # Facet counts: for each axis, count with the OTHER axes applied. That
        # is what makes the numbers next to each checkbox mean "how many would
        # I get if I ticked this", which is the whole point of showing them.
        facet_counts: dict[str, dict[str, int]] = {}
        for axis in sel:
            cnt: dict[str, int] = {}
            for r in base:
                if not axis_ok(r, skip=axis):
                    continue
                v = r.get(axis)
                if v is not None:
                    cnt[v] = cnt.get(v, 0) + 1
            facet_counts[axis] = cnt

        tag_counts: dict[str, int] = {}
        for r in matched:
            for t in (tag_map.get(r["subject"], [])
                      + r.get("serverTags", [])):
                tag_counts[t] = tag_counts.get(t, 0) + 1

        payload = {
            "table": table,
            "total": len(matched),
            "offset": offset,
            "facets": facet_counts,
            "facetOrder": {"class": bodyfacets.CLASS_ORDER,
                           "gender": bodyfacets.GENDER_ORDER,
                           "size": bodyfacets.SIZE_ORDER},
            "tagCounts": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "isBodyTable": table in BODY_TABLES,
            "bodyMetrics": bodyfacets.BODY_METRICS,
            # every matching subject, so "tag all of these" does not depend on
            # the page the user happens to be looking at
            "allSubjects": [r["subject"] for r in matched],
        }

        if group == "mesh":
            groups: dict[str, dict] = {}
            for r in matched:
                k = r.get("mesh") or f"(unresolved mesh {r.get('meshId','')})"
                g = groups.setdefault(k, {"mesh": k, "count": 0, "variants": [],
                                          "class": r.get("class"),
                                          "gender": r.get("gender"),
                                          "size": r.get("size"),
                                          "itemName": r.get("itemName", "")})
                g["count"] += 1
                g["variants"].append({"id": r["id"], "subject": r["subject"],
                                      "texture": r["texture"],
                                      "tags": (tag_map.get(r["subject"], [])
                                               + r.get("serverTags", []))})
            glist = sorted(groups.values(), key=lambda g: g["mesh"])
            for g in glist:
                g["texture"] = g["variants"][0]["texture"]
            payload["groupedTotal"] = len(glist)
            payload["groups"] = glist[offset:offset + limit]
            return self._json(payload)

        page = []
        for r in matched[offset:offset + limit]:
            row = dict(r)
            row["tags"] = (tag_map.get(r["subject"], [])
                           + r.get("serverTags", []))
            page.append(row)
        payload["rows"] = page
        return self._json(payload)

    def api_bodyfacets(self, arg):
        """The whole classification, unfiltered -- what the docs describe."""
        c = self.cat
        c.wait_tables()
        if not c.facets:
            return self._error(503, "facet classification unavailable")
        return self._json({
            "summary": c.facets.summary(),
            "bodyTypes": {k: {"gender": v[0], "size": v[1],
                              "metrics": bodyfacets.BODY_METRICS.get(k)}
                          for k, v in bodyfacets.BODY_TYPES.items()},
            "professionClass": {str(k): v
                                for k, v in bodyfacets.PROFESSION_CLASS.items()},
            "seriesProfession": {s: (c.facets.series_profession(s))
                                 for s in sorted(c.facets.series_prof)},
            "count": len(c.facets.records),
        })

    # -- API: tags ---------------------------------------------------------
    def api_tags(self, arg):
        store = self.server.tags                          # type: ignore[attr-defined]
        subject = arg("subject", "")
        if subject:
            return self._json({"subject": subject, "tags": store.get(subject),
                               "note": store.note(subject),
                               "auto": self._auto_tags_for(subject)})
        vocab = dict(store.vocabulary())
        # The server tag is part of the vocabulary you can filter by even
        # though nobody typed it: it is how you ask for "the Zephyr art".
        if self.cat is not None and self.cat.server_tag:
            vocab.setdefault(self.cat.server_tag, len(self.cat.unique_paths))
            # each recovered archive is its own browsable group
            groups: dict[str, int] = {}
            for g in self.cat.group_tags.values():
                groups[g] = groups.get(g, 0) + 1
            for g, n in sorted(groups.items()):
                vocab.setdefault(g, n)
        return self._json({"vocabulary": vocab,
                           "taggedSubjects": len(store),
                           "file": str(store.path)})

    def _auto_tags_for(self, subject: str) -> list[str]:
        if not subject.startswith("app:"):
            return []
        c = self.cat
        if not c.facets:
            return []
        rec = c.facets.records.get(subject[4:])
        return rec.auto_tags if rec else []

    def post_tags(self, body: bytes, arg):
        """add / remove / set / note, on one subject or a whole selection."""
        store = self.server.tags                          # type: ignore[attr-defined]
        try:
            req = json.loads(body or b"{}")
        except Exception as e:
            return self._error(400, f"bad JSON: {e}")
        action = req.get("action", "add")
        subjects = req.get("subjects") or ([req["subject"]] if req.get("subject") else [])
        tags = req.get("tags") or ([req["tag"]] if req.get("tag") else [])
        subjects = [s for s in subjects if isinstance(s, str) and s]
        if not subjects and action not in ("rename", "delete"):
            return self._error(400, "no subjects")
        try:
            if action == "add":
                n = store.add(subjects, tags)
            elif action == "remove":
                n = store.remove(subjects, tags)
            elif action == "set":
                store.set(subjects[0], tags)
                n = 1
            elif action == "note":
                store.set_note(subjects[0], req.get("note", ""))
                n = 1
            elif action == "rename":
                n = store.rename(req["from"], req["to"])
            elif action == "delete":
                n = store.delete_tag(req["tag"])
            elif action == "import":
                n = store.import_json(req.get("data", "{}"), merge=req.get("merge", True))
            else:
                return self._error(400, f"unknown action {action!r}")
        except TagError as e:
            return self._error(400, str(e))
        except KeyError as e:
            return self._error(400, f"missing field {e}")
        self._invalidate_rows()
        return self._json({"changed": n, "vocabulary": store.vocabulary(),
                           "taggedSubjects": len(store),
                           "tags": store.get(subjects[0]) if subjects else []})

    def _invalidate_rows(self):
        # tags are part of the cached row payload's search text
        with self.server.cache_lock:                      # type: ignore[attr-defined]
            self.server.rows_cache.clear()                # type: ignore[attr-defined]

    def api_tags_export(self, arg):
        store = self.server.tags                          # type: ignore[attr-defined]
        fmt = arg("format", "json")
        if fmt == "csv":
            extra = {}
            c = self.cat
            if c.facets:
                for subj in store.all_subjects():
                    if subj.startswith("app:"):
                        rec = c.facets.records.get(subj[4:])
                        if rec:
                            extra[subj] = {"class": rec.klass, "gender": rec.gender,
                                           "size": rec.size, "kind": rec.kind,
                                           "item": rec.item_name,
                                           "mesh": rec.mesh or "",
                                           "texture": rec.texture or ""}
            body = store.export_csv(extra).encode("utf-8")
            return self._send(200, body, "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="co-tags.csv"'})
        body = store.export_json().encode("utf-8")
        return self._send(200, body, "application/json; charset=utf-8",
                          {"Content-Disposition": 'attachment; filename="co-tags.json"'})

    def api_appearance(self, arg):
        c = self.cat
        c.wait_tables()
        ident = arg("id")
        table = arg("table")
        out = []
        for part, ini in sorted(c.tables.items()):
            if table and part != table:
                continue
            app = ini.get(ident)
            if not app:
                continue
            parts = []
            for pr in app.parts:
                mesh = c.resolve_id(pr.mesh, "mesh")
                tex = c.resolve_id(pr.texture, "texture")
                parts.append({
                    "index": pr.index, "material": pr.material,
                    "meshId": pr.mesh, "mesh": mesh,
                    "meshProv": c.provenance(mesh).to_json() if mesh else None,
                    "textureId": pr.texture, "texture": tex,
                    "texProv": c.provenance(tex).to_json() if tex else None,
                    "mixTex": pr.mix_tex, "thirdTex": pr.third_tex,
                    "fourthTex": pr.fourth_tex,
                })
            out.append({"table": part, "ini": ini.name, "id": app.ident,
                        "parts": parts, "raw": app.raw})
        if not out:
            return self._error(404, f"appearance {ident!r} not found")
        return self._json(out)

    # -- API: categories ---------------------------------------------------
    def api_categories(self, arg):
        """The browsing taxonomy with live counts. `why` travels with it so a
        misfiled asset is visible rather than silent."""
        c = self.cat
        c.wait_tables(20)
        s = c.category_summary()
        out = []
        for meta in assetcat.CATEGORIES:
            e = s.get(meta["id"])
            if not e:
                continue
            order = assetcat.SUBCATEGORY_ORDER.get(meta["id"], [])
            subs = sorted(e["subs"].items(),
                          key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99,
                                          -kv[1]))
            out.append({**meta, "count": e["count"],
                        "roles": e["roles"],
                        "subs": [{"id": k, "count": v} for k, v in subs]})
        return self._json({"categories": out,
                           "total": sum(v["count"] for v in s.values())})

    def api_catfiles(self, arg):
        """Files inside a category / subcategory, with the usual text filter,
        tag filter and paging."""
        c = self.cat
        c.wait_tables(20)
        cat_id = arg("category", "")
        sub = arg("sub", "")
        role = arg("role", "")
        group = arg("group", "")
        query = arg("q", "").strip().lower().replace("\\", "/")
        offset = int(arg("offset", "0") or 0)
        limit = min(int(arg("limit", "300") or 300), 2000)
        idx = c.category_index()
        buckets = idx.get(cat_id, {})
        paths = buckets.get(sub, []) if sub else [p for v in buckets.values() for p in v]

        store = self.server.tags                          # type: ignore[attr-defined]
        tag_map = store.all_subjects()
        want_tags = {t.lower() for t in self._multi(arg("tag", ""))}
        untagged = arg("untagged", "") == "1"

        # Same collapse as the Files tab -- see api_files. Folding is skipped
        # when the user has explicitly filtered to one *kind* of file, because
        # "show me only textures" must not then hide the textures.
        unified = arg("unified", "1") != "0" and not role
        u = c.unified if unified else None
        folded_away = set()
        if u:
            present = set(paths)
            folded_away = {p for p in paths if p.endswith(".dds")
                           and (u.primary_owner.get(p) in present)}

        rows = []
        role_counts: dict[str, int] = {}
        group_counts: dict[str, int] = {}
        for p in paths:
            cl = c.assetcat.classify(p)
            role_counts[cl.role] = role_counts.get(cl.role, 0) + 1
            if cl.group:
                group_counts[cl.group] = group_counts.get(cl.group, 0) + 1
            if role and cl.role != role:
                continue
            if group and cl.group != group:
                continue
            if p in folded_away:
                continue
            texes = [m["texture"] for m in u.textures_of(p)][:8] if (
                u and p.endswith(".c3")) else []
            if query and query not in p and not any(query in t for t in texes):
                continue
            subj = f"file:{p}"
            tags = tag_map.get(subj, [])
            if untagged and tags:
                continue
            if want_tags and not want_tags.issubset(set(tags)):
                continue
            rows.append({"path": p, "source": "loose" if p in c.loose
                         else c.archived.get(p, "?"),
                         "role": cl.role, "group": cl.group, "why": cl.why,
                         "subject": subj, "tags": tags,
                         "textures": texes,
                         "folded": sum(1 for t in texes
                                       if u and u.primary_owner.get(t) == p),
                         "thumb": (texes[0] if texes else
                                   (p if p.endswith(".dds") else None)),
                         "sub": cl.subcategory})
        top_groups = sorted(group_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:400]
        return self._json({
            "category": cat_id, "sub": sub, "total": len(rows), "offset": offset,
            "roles": role_counts,
            "groups": [{"id": k, "count": v} for k, v in top_groups],
            "rows": rows[offset:offset + limit],
            "allSubjects": [r["subject"] for r in rows],
            "unified": bool(u),
            "foldedAway": len(folded_away),
        })

    # -- API: maps ---------------------------------------------------------
    def api_maps(self, arg):
        """All 136 world maps, largest first — the user asked to browse *by*
        the primary map, so the map itself is the entry point."""
        rows = self.cat.maps.summary()
        q = arg("q", "").strip().lower()
        if q:
            rows = [r for r in rows if q in r["name"].lower()
                    or q in (r.get("puzzle") or "")]
        return self._json({"total": len(rows), "rows": rows})

    def api_map(self, arg):
        """One map and every piece of art that draws it."""
        name = arg("name", "")
        if not name:
            return self._error(400, "name required")
        rec = self.cat.maps.get(name)
        out = rec.to_json()
        # scene parts resolve to sprite frames one level further down
        parts = []
        for s in rec.scenes[:60]:
            parts.append({"scene": s, "parts": self.cat.maps.scene_parts(s)})
        out["scenePartDetail"] = parts
        return self._json(out)

    # -- API: the MapEditor ------------------------------------------------
    #
    # `tools/mapedit.py` owns every decision below; these routes only carry
    # its answers.  The one thing that lives here is the tile cache, because
    # caching is a property of the server rather than of the model.

    def _mapart(self, arg):
        name = arg("name", "") or arg("map", "")
        if not name:
            self._error(400, "name required")
            return None
        art = self.cat.mapedit.get(name)
        if art.header() is None:
            self._error(404, f"no map/map/{name}.DMap")
            return None
        return art

    def api_mapedit_maps(self, arg):
        """Every `ini/GameMap.json` row, with the state of its art.

        All 156 rows are listed, including the three `grocery` ids that name
        one file and the one row whose `.DMap` does not ship.  A map that
        cannot be drawn says why (`pux`, `mismatch`, `noart`) instead of
        vanishing from the list.
        """
        rows = self.cat.mapedit.rows()
        q = arg("q", "").strip().lower()
        if q:
            rows = [r for r in rows
                    if q in r["name"].lower() or q in (r.get("puzzle") or "")]
        states: dict[str, int] = {}
        for r in self.cat.mapedit.rows():
            states[r["state"]] = states.get(r["state"], 0) + 1
        return self._json({"total": len(rows), "rows": rows, "states": states,
                           "tile": mapedit_mod.TILE,
                           "zooms": list(mapedit_mod.ZOOMS),
                           "layers": [{"id": k, "title": mapedit_mod.LAYER_TITLE[k]}
                                      for k in mapedit_mod.LAYERS]})

    def api_mapedit_map(self, arg):
        art = self._mapart(arg)
        if art is None:
            return None
        return self._json(art.to_json())

    def api_mapedit_editable(self, arg):
        """Every file this map is made of, sorted onto the two sides of the
        integrity line. This is the answer to "what may I change here?"."""
        art = self._mapart(arg)
        if art is None:
            return None
        return self._json(art.editable_report())

    def api_mapedit_tile(self, arg):
        """One layer of one map tile, as an RGBA PNG.

        Layers are served separately rather than composited so the page can
        toggle one without refetching the rest -- and so that turning COVER
        off is instant instead of a round trip per tile.
        """
        art = self._mapart(arg)
        if art is None:
            return None
        layer = arg("layer", "ground")
        if layer not in mapedit_mod.LAYERS:
            return self._error(400, f"unknown layer {layer!r}")
        try:
            z = int(arg("z", "1") or 1)
            tx, ty = int(arg("tx", "0") or 0), int(arg("ty", "0") or 0)
        except ValueError:
            return self._error(400, "z, tx and ty must be integers")
        if z not in mapedit_mod.ZOOMS:
            return self._error(400, f"z must be one of {mapedit_mod.ZOOMS}")
        t = max(0, int(arg("t", "0") or 0))
        key = ("mapedit", art.name.lower(), layer, z, tx, ty, t)
        with self.server.cache_lock:                  # type: ignore[attr-defined]
            hit = self.server.tex_cache.get(key)      # type: ignore[attr-defined]
        if hit is None:
            try:
                hit = art.tile_png(layer, tx, ty, z, time_ms=t)
            except Exception as e:                    # noqa: BLE001
                return self._error(500, f"{layer} tile failed: {e}")
            with self.server.cache_lock:              # type: ignore[attr-defined]
                cache = self.server.tex_cache         # type: ignore[attr-defined]
                if len(cache) > 3000:
                    cache.clear()
                cache[key] = hit
        return self._send(200, hit, "image/png",
                          {"Cache-Control": "no-cache"})

    def api_mapedit_pick(self, arg):
        """What is drawn at an art pixel: a COVER, a TERRAIN part, or the
        puzzle cell underneath. Alpha-tested, so a click through a gap in a
        tree selects what is behind it."""
        art = self._mapart(arg)
        if art is None:
            return None
        try:
            px, py = float(arg("px", "0")), float(arg("py", "0"))
        except ValueError:
            return self._error(400, "px and py must be numbers")
        layers = [s for s in arg("layers", "cover,terrain,ground").split(",") if s]
        if art.pm is None:
            return self._json({"kind": "none", "why": art.reason})
        return self._json(art.pick(px, py, layers=layers))

    def post_mapedit_passability(self, body: bytes, arg):
        r"""Stage a passability edit -- the one route here that writes a file
        `integrity.json` lists.

        It refuses without the exact acknowledgement string, and the refusal
        carries the explanation rather than a bare 403, because the point of
        the gate is that the user understands what they are opting into.
        Nothing reaches the game install either way: this writes
        `mods/stage/map/map/<name>.DMap` and `comod.py` remains the only
        sanctioned writer.
        """
        art = self._mapart(arg)
        if art is None:
            return None
        try:
            req = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:                        # noqa: BLE001
            return self._error(400, f"bad JSON body: {e}")
        edits = req.get("cells") or []
        if not isinstance(edits, list) or not edits:
            return self._error(400, "cells: [{x, y, blocked}] required")
        try:
            res = mapedit_mod.stage_passability(
                art, edits, ack=str(req.get("ack", "")), stage=STAGE)
        except mapedit_mod.NotAcknowledged as e:
            return self._json({
                "ok": False, "needsAck": True, "ack": mapedit_mod.ACK,
                "why": str(e),
                "integrity": self.cat.mapedit.integrity.status(art.dmap_logical),
            }, 409)
        except Exception as e:                        # noqa: BLE001
            return self._error(400, f"could not patch the .DMap: {e}")
        # The editor reads the staged copy from now on, so drop the parse and
        # every passability tile that came from the old one.
        self._mapedit_invalidate(art.dmap_logical)
        res["ok"] = True
        return self._json(res)

    # -- API: character assembly -------------------------------------------
    def api_parts(self, arg):
        """The equip slots, straight from ini/RolePart.ini."""
        c = self.cat
        c.wait_tables(20)
        pm = partsmod.PartManifest(c.root, tables=c.tables)
        out = pm.to_json()
        for s in out["slots"]:
            name = s["name"]
            ini = c.tables.get(name)
            s["available"] = bool(ini) and s["shipped"]
            s["count"] = len(ini) if ini else 0
            # Equipment art is body-type specific only where the ids actually
            # carry a 001-004 prefix. Measured over this build: armor.ini
            # 94.6%, armet.ini 100%, and weapon.ini / armet1.ini / head.ini /
            # misc.ini / mount.ini 0% -- their ids are 6-8 digits with no body
            # prefix at all. The old list claimed armet_dx8, head and misc were
            # body-specific, which would have filtered every one of them away.
            s["bodySpecific"] = name in ("body", "mix_body", "armet", "mix_armet")
            s["headKindAware"] = name in ("armet", "armet_dx8")
        return self._json(out)

    # -- API: the character builder ----------------------------------------
    def api_builder(self, arg):
        r"""Everything the builder page needs to draw itself once.

        Slots come from `ini/RolePart.ini` as before, but each one now carries
        whether it is actually *usable* -- `head.ini`, `misc.ini` and
        `mount.ini` all ship but not one of their meshes resolves in this build,
        so they are greyed with a reason rather than opening an empty picker.
        """
        idx = self.cat.builder
        bt = arg("bodyType", "002")
        if bt not in bodyfacets.BODY_TYPES:
            bt = "002"
        return self._json({
            "slots": idx.slot_info(),
            "primary": list(builder_mod.PRIMARY_SLOTS),
            "exclusive": partsmod.EXCLUSIVE_GROUPS,
            "bodyTypes": [{"id": k, "gender": g, "size": s,
                           "label": f"{s.title()} {g}",
                           "metrics": bodyfacets.BODY_METRICS.get(k)}
                          for k, (g, s) in sorted(bodyfacets.BODY_TYPES.items())],
            "defaultLoadout": idx.default_loadout(bt),
            "animAvailable": builder_mod.anim_available(),
            "animNote": builder_mod.TIMING_NOTE,
            "rootMotionNote": builder_mod.ROOT_MOTION_NOTE,
            "missingNote": builder_mod.MISSING_NOTE,
            "defaultFrameMs": builder_mod.DEFAULT_FRAME_MS,
            "superAnchorNote": builder_mod.SUPER_ANCHOR_NOTE,
            "qualityOrder": list(builder_mod.QUALITY_ORDER),
            "qualityLabels": dict(builder_mod.QUALITY_LABEL),
            "qualityNote": builder_mod.QUALITY_NOTE,
            "qualityZeroNote": builder_mod.ZERO_DIGIT_NOTE,
            "auraQualityNote": builder_mod.AURA_QUALITY_NOTE,
            "weaponSlots": ["r_weapon", "l_weapon", "shield"],
            "hairSeries": sorted(builder_mod.HAIR_SERIES),
            "compatNote": (
                "Armour and headgear ids are prefixed by the body type "
                "(001-004), so only art made for the body you picked is "
                "offered. Weapon ids carry no body prefix at all — measured "
                "over all 5,384 rows of weapon.ini — so every weapon fits "
                "every body."),
        })

    def api_options(self, arg):
        r"""What can go in one slot, given the body already chosen.

        `/api/options?slot=armet&body=002135300&q=cap&kind=headgear`

        Returns *garments* (one row per distinct mesh) with their colour
        variants nested, because the mesh is shared across colourways and only
        the texture changes. Every variant is a shipped appearance: there is no
        path here that pairs a mesh with a texture of its own choosing.
        """
        idx = self.cat.builder
        slot = arg("slot", "")
        if slot not in idx.options:
            return self._error(404, f"unknown slot {slot!r}")
        body = arg("body", "")
        body_type = arg("bodyType", "") or (body[:3] if len(body) >= 3 else "")
        if body_type not in bodyfacets.BODY_TYPES:
            body_type = ""
        sel = {axis: self._multi(arg(axis, "")) for axis in builder_mod.AXES}
        store = self.server.tags                          # type: ignore[attr-defined]
        tag_map = store.all_subjects()
        res = idx.query(slot, body_type=body_type, text=arg("q", ""),
                        selected=sel, tag_map=tag_map,
                        want_tags=self._multi(arg("tag", "")),
                        untagged=arg("untagged", "") == "1")
        matched = res.pop("matched")
        offset = int(arg("offset", "0") or 0)
        limit = min(int(arg("limit", "400") or 400), 2000)
        groups = idx.garments(matched, tag_map)
        info = next((s for s in idx.slot_info() if s["name"] == slot), {})
        res.update({
            "garmentTotal": len(groups),
            "garments": groups[offset:offset + limit],
            "offset": offset,
            "slotInfo": info,
            "allSubjects": [f"app:{o.ident}" for o in matched],
            "note": ("Each row is one garment — a distinct mesh. The swatches "
                     "under it are its colour variants, which share that mesh "
                     "and differ only in texture. Both come straight from the "
                     "appearance table, so every combination shown is one the "
                     "game itself ships."),
        })
        return self._json(res)

    def api_option(self, arg):
        """One option, by slot and id -- the plain-language name for something
        restored from a link or from localStorage, where the picker's data is
        not in hand."""
        idx = self.cat.builder
        slot, ident = arg("slot", ""), arg("id", "")
        opts = idx.options.get(slot, [])
        hit = next((o for o in opts if o.ident == ident), None)
        if hit is None:
            return self._json({"id": ident, "slot": slot, "name": ident,
                               "detail": "", "unknown": True}, 404)
        # its colourways: the same mesh with a different texture, which is the
        # only axis a "variant" ever varies on.
        sibs = sorted((o for o in opts if o.mesh == hit.mesh),
                      key=lambda o: o.ident)
        out = hit.to_json()
        out["variants"] = [o.to_json() for o in sibs]
        out["variantIndex"] = [o.ident for o in sibs].index(ident)
        # 6090 encodes colours in texture files rather than appearance rows
        # (CCO's one-row-per-colour is why `variants` alone used to cover
        # this). Shipped texture siblings, for the colour panel.
        out["colourways"] = self.cat.texture_colourways(hit.texture)
        return self._json(out)

    def api_quality(self, arg):
        r"""The five named qualities of one weapon, and what each one changes.

        `/api/quality?slot=r_weapon&id=410009`

        The last digit of a weapon appearance id is its quality -- 3-5 Normal,
        6 Refined, 7 Unique, 8 Elite, 9 Super -- and the family (the first five
        digits) shares ONE mesh, so stepping through qualities is a texture
        change on the weapon you already chose.  `410000` is deliberately not
        called "Normal": it is the family's base row and in 350 families it
        points at the *Refined* texture, so it is listed separately.

        Everything here comes from `builder.BuilderIndex.quality_family()`, i.e.
        from options already checked against disk, and every quality that is
        missing says which of the three reasons it is missing for.
        """
        ident = arg("id", "")
        slot = arg("slot", "r_weapon")
        if not ident:
            return self._error(400, "id= (a weapon appearance) is required")
        return self._json(self.cat.builder.quality_family(slot, ident))

    def api_superfx(self, arg):
        r"""The always-on weapon effect, resolved and placed.

        `/api/superfx?id=410099&body=002132300&slot=r_weapon&action=401&frame=6`

        Two things, both task #20's and both imported rather than re-derived:

        * **which** effect -- `superfx.SuperFxDB`, a TABLE lookup
          (`Action3DEffect[999.999.<type>.<sub>]` -> `3DEffect.ini` ->
          `EffectId0` -> `3DEffectObj.ini`), not a filename convention. 796
          weapon appearances carry one and 816 of 816 resolve, across 29
          families -- `blade/` is just the one the user happened to name.
        * **where** it goes -- `SuperFxDB.anchor()`, which is
          `M_offset x M_socket x M_role` and pointedly does **not** compose the
          weapon's own bone-0 matrix. The effect is a sibling of the weapon at
          the socket, not its child; composing the weapon's matrix too is
          exactly the "off in space" complaint.

        The playable geometry comes back through `effectplay` in the same shape
        `fx.js` already consumes, so playback is the tested path.
        """
        ident = arg("id", "")
        slot = arg("slot", "r_weapon")
        idx = self.cat.builder
        out = {"id": ident, "slot": slot, "found": False,
               "available": builder_mod.superfxmod is not None,
               "anchorNote": builder_mod.SUPER_ANCHOR_NOTE}
        if not ident:
            return self._error(400, "id= (a weapon appearance) is required")
        rec = idx.super_effect(ident, slot)
        if not rec:
            out["note"] = (
                f"{ident} has no always-on effect. 796 of the 5,384 weapon "
                f"appearances carry one — mostly the higher qualities — so "
                f"this is the data rather than a lookup failure.")
            return self._json(out)
        out.update(rec)
        out["found"] = True

        # the playable scene, through the same resolver the browser page uses
        try:
            sc = self.cat.effects.scene(rec["name"])
            out["effect"] = sc.payload if sc.found else None
            if not sc.found:
                out["error"] = sc.error
        except Exception as e:                            # pragma: no cover
            out["error"] = str(e)

        # and where it sits on this body, at this action and frame
        body_id = arg("body", "")
        if body_id and builder_mod.superfxmod is not None and attach is not None:
            try:
                body = self._appearance_part("body", body_id)
                raw = self.cat.read(body["mesh"]) if body else None
                if raw:
                    pm = attach.PartMesh.parse(raw, body["mesh"])
                    clip = self._anim_clip(body_id, arg("action", "100"),
                                           arg("weapon", ident), arg("offHand", ""))
                    frame = max(0, int(arg("frame", "0") or 0))
                    m = builder_mod.superfxmod.SuperFxDB.anchor(
                        pm, slot, frame,
                        (clip.motion if clip is not None else None),
                        tuple(rec["offset"]))
                    if m is not None:
                        out["anchor"] = attach.to_render(m)
                        out["anchorSource"] = (
                            "superfx.SuperFxDB.anchor(): M_offset x M_socket x "
                            "M_role, evaluated on "
                            + (f"action {clip.action} frame {frame}"
                               if clip is not None else "the mesh's own MOTI"))
            except Exception as e:                        # pragma: no cover
                out["anchorError"] = str(e)
        return self._json(out)

    def _anim_db(self):
        """`anim.AnimDB`, shared. None when tools/anim.py is not importable."""
        c = self.cat
        with c._anim_lock:
            if getattr(c, "_animdb", None) is None:
                if builder_mod.animmod is None:
                    c._animdb = False
                else:
                    try:
                        c._animdb = builder_mod.animmod.AnimDB(c.root)
                    except Exception:                     # pragma: no cover
                        c._animdb = False
            return c._animdb or None

    def _anim_clip(self, body: str, action: str, weapon: str = "",
                   off_hand: str = ""):
        db = self._anim_db()
        if db is None:
            return None
        try:
            return db.clip(body, action, weapon=weapon, off_hand=off_hand)
        except Exception:                                 # pragma: no cover
            return None

    def api_actions(self, arg):
        r"""Every action this body-and-loadout can actually play.

        Resolution is `anim.AnimDB`'s, which means the **equipped weapon is
        part of the lookup**: `ini/3dmotion.ini` is keyed
        `<shape><weaponset><action>` and a `410` swing differs from the unarmed
        one by up to 115 units of pose on a 170-unit body, so asking for the
        body alone would animate an armed character empty-handed.

        Codes `anim.py` could not name are still listed, grouped under "Other"
        and last -- an average person does not want twelve unlabelled numbers
        at the top of the menu, and hiding them outright would be a lie.
        """
        db = self._anim_db()
        body = arg("body", "")
        acts = builder_mod.actions_for(db, body, arg("weapon", ""),
                                       arg("offHand", ""))
        return self._json({
            "body": body, "actions": acts,
            "available": db is not None,
            "named": sum(1 for a in acts if a["named"]),
            "defaultFrameMs": builder_mod.DEFAULT_FRAME_MS,
            "timingNote": builder_mod.TIMING_NOTE,
            "rootMotionNote": builder_mod.ROOT_MOTION_NOTE,
            "missingNote": builder_mod.MISSING_NOTE,
        })

    def api_anim(self, arg):
        r"""One action, posed frame by frame, from the game's own motion data.

        `/api/anim?body=002132300&action=120&weapon=410089`

        `tools/anim.py` resolves the clip (`AnimDB.clip`), which handles the
        weaponset, the alias chain and the 1,100 motion files this install does
        not ship; this then evaluates the body's skin at every frame of it --
        the same code path already used for the frame-0 idle pose, not stopped
        at frame 0. Positions only: uvs, indices and textures do not change
        between frames, so the client swaps one buffer per chunk.

        Three things worth knowing, all `docs/animation.md`'s:

        * **Run is two clips.** `120` and `121` are the left and right halves of
          the stride (`runL.wav` / `runR.wav`) and chain rather than self-loop,
          so `chain` comes back with the clip and the client plays the pair.
        * **Walk and run translate the root by exactly 0.00.** They are in-place
          cycles that the client moves across the map, so playing them on the
          spot is correct. A jump *does* bake its arc into the poses.
        * **The frame rate is not in the data.** 41 ms is anim.py's reading and
          the page ships a speed control rather than presenting it as fact.
        """
        c = self.cat
        c.wait_tables(20)
        body_id = arg("body", "")
        action = arg("action", "100")
        weapon = arg("weapon", "")
        off_hand = arg("offHand", "")
        if not body_id:
            return self._error(400, "body= (a body appearance) is required")
        key = (body_id, action, weapon, off_hand)
        with self.cat._anim_lock:
            hit = c._anim_cache.get(key)
        if hit is not None:
            return self._json(hit)

        body = self._appearance_part("body", body_id) or \
            self._appearance_part("mix_body", body_id)
        if not body or not body.get("mesh"):
            return self._error(404, f"body {body_id!r} has no resolvable mesh")

        clip = self._anim_clip(body_id, action, weapon, off_hand)
        meta = (builder_mod.animmod.ACTIONS.get(action)
                if builder_mod.animmod else None)
        out = {"body": body_id, "action": action, "weapon": weapon,
               "label": (meta.name if meta else f"action {action}"),
               "group": (meta.group if meta else ""),
               "confidence": (meta.confidence if meta else "unknown"),
               "evidence": (meta.evidence if meta else ""),
               "frameIntervalMs": builder_mod.DEFAULT_FRAME_MS,
               "animAvailable": builder_mod.anim_available(),
               "timingNote": builder_mod.TIMING_NOTE,
               "rootMotionNote": builder_mod.ROOT_MOTION_NOTE,
               "frames": 0, "chunks": [], "sockets": [],
               "loop": "cyclic", "chain": None, "rootMotion": None}
        if clip is None:
            out["error"] = (
                f"No motion ships for action {action} on this body and weapon. "
                + builder_mod.MISSING_NOTE)
            return self._json(out)

        out.update({"motion": clip.path, "how": clip.how,
                    "weaponset": clip.weaponset, "shape": clip.shape,
                    "loop": clip.loop, "chain": clip.chain_next,
                    "frameIntervalMs": clip.interval_ms})
        n = max(1, min(int(clip.play_length), 120))
        out["frames"] = n
        # In-place is correct for walk/run and the jump's arc is in the poses,
        # so nothing is added here. Reported so the panel can say which it is.
        try:
            track = clip.root_track()
            out["rootMotion"] = {
                "maxXY": round(max((abs(t[0]) + abs(t[1]) for t in track),
                                   default=0.0), 3),
                "riseUp": round(max((t[2] for t in track), default=0.0) -
                                min((t[2] for t in track), default=0.0), 3),
            }
        except Exception:                                 # pragma: no cover
            pass

        raw = c.read(body["mesh"])
        for f in range(n):
            scene = c3_to_json(raw, body["mesh"], motion_set=clip.motion, frame=f)
            if f == 0:
                out["chunks"] = [{"index": m["index"], "name": m["name"],
                                  "frames": []} for m in scene["meshes"]]
            for slot_i, m in enumerate(scene["meshes"]):
                if slot_i < len(out["chunks"]):
                    out["chunks"][slot_i]["frames"].append(m["positions"])
            anchors = partsmod.socket_anchors(raw, motion_set=clip.motion, frame=f)
            anchors, corr = apply_socket_corrections(
                anchors, self.cat.plugin, body_id,
                weapon_set=clip.weaponset, action=clip.action, frame=f)
            if corr:
                out["socketCorrections"] = corr
            out["sockets"].append({k: v.matrix for k, v in anchors.items()
                                   if v.matrix})
        with self.cat._anim_lock:
            if len(c._anim_cache) > 24:
                c._anim_cache.clear()
            c._anim_cache[key] = out
        return self._json(out)

    # -- API: models that are not assembled characters ---------------------
    #
    # A monster is not a character with different art. `c3/monster/103/` is
    # sixteen files, one per action, and most of them carry their OWN geometry
    # -- so "play the walk" means load a different mesh, not bind a different
    # track over one mesh. There are no equip slots to fill. That is why this
    # is a second mode rather than another table in the character builder.
    # tools/models.py owns the reasoning; these three routes are the seam.

    def _model_tag_map(self, mc, user_tags: dict) -> dict:
        """User tags plus the server/archive tags each model's art carries.

        A model is a reference to files; when those files are unique to the
        selected community server they already carry its tag in the Files
        pane, and the Models list should filter by exactly the same thing.
        Injecting them here means `ModelCatalogue.query` filters, and the row
        renders them, with no separate code path.
        """
        if not self.cat.server_tag:
            return user_tags
        merged = dict(user_tags)
        for m in mc.models:
            auto = []
            for logical in (m.mesh, m.texture):
                if logical:
                    auto = self.cat.auto_tags(logical)
                    if auto:
                        break
            if auto:
                subj = f"model:{m.key}"
                merged[subj] = list(merged.get(subj, [])) + auto
        return merged

    def api_models(self, arg):
        r"""The model families, filtered, with cross-filtered kind counts.

        `/api/models?kind=monster&q=1` ->
            {kinds: [...], kindCounts: {...}, models: [...]}

        Every model listed has at least one action whose file ships, unless
        `playable=0` is passed -- an entry that cannot draw anything is worse
        than no entry, and the count of what was withheld is reported.
        """
        mc = self.cat.models
        store = self.server.tags                          # type: ignore[attr-defined]
        tag_map = self._model_tag_map(mc, store.all_subjects())
        res = mc.query(kind=arg("kind", ""), text=arg("q", ""),
                       playable_only=arg("playable", "1") != "0",
                       tag_map=tag_map,
                       want_tags=self._multi(arg("tag", "")),
                       untagged=arg("untagged", "") == "1")
        matched = res.pop("matched")
        offset = int(arg("offset", "0") or 0)
        limit = min(int(arg("limit", "500") or 500), 2000)
        rows = []
        for m in matched[offset:offset + limit]:
            j = m.to_json()
            j["tags"] = tag_map.get(f"model:{m.key}", [])
            j["thumb"] = m.mesh or m.texture
            rows.append(j)
        res.update({
            "models": rows, "offset": offset,
            "kinds": mc.kinds(),
            "monsterRows": len(mc.monsters),
            "layoutNote": models_mod.LAYOUT_NOTE,
            "monsterLinkNote": models_mod.MONSTER_LINK_NOTE,
            "zoomNote": models_mod.ZOOM_NOTE,
            "missingNote": models_mod.MISSING_NOTE,
        })
        return self._json(res)

    def api_model(self, arg):
        """One model and its whole action list.

        `/api/model?key=monster:103` — actions are the union of what
        `ini/3dmotion.ini` declares for the shape and what the directory
        actually holds, each one marked `available` and, where it is a
        duplicate of an earlier code, `distinct: false` with `aliasOf`.
        """
        key = arg("key", "")
        m = self.cat.models.get(key)
        if m is None:
            return self._error(404, f"no model {key!r}")
        out = m.to_json(with_actions=True)
        out["layoutNote"] = models_mod.LAYOUT_NOTE
        out["missingNote"] = models_mod.MISSING_NOTE
        if m.kind == "monster":
            out["monsterLinkNote"] = models_mod.MONSTER_LINK_NOTE
            out["zoomNote"] = models_mod.ZOOM_NOTE
        store = self.server.tags                          # type: ignore[attr-defined]
        out["tags"] = store.all_subjects().get(f"model:{key}", [])
        out["colourways"] = (
            self.cat.monster_colourways(m.ident, m.texture)
            if m.kind == "monster"
            else self.cat.texture_colourways(m.texture))
        if m.kind == "effect":
            try:
                sc = self.cat.effects.scene(m.ident)
                out["effect"] = sc.payload if sc.found else None
                out["found"] = sc.found
                if not sc.found:
                    out["error"] = sc.error
            except Exception as e:                        # pragma: no cover
                out["error"] = str(e)
        return self._json(out)

    def api_monsterrows(self, arg):
        r"""`ini/monster.json`, offered for the user to pair with a mesh.

        **There is no link in the data and this route does not invent one.**
        `type` is a sequential index 1-374 and `bodyType` is 0 on every row, so
        the client's own choice of appearance is made by the server at spawn.
        What the row *does* carry is worth having: `zoomPercent` runs 60 to 350
        and a monster drawn at 100% is simply the wrong size.
        """
        mc = self.cat.models
        q = (arg("q", "") or "").strip().lower()
        rows = mc.monster_rows()
        if q:
            rows = [r for r in rows
                    if q in r["name"].lower() or q == str(r["type"])]
        return self._json({
            "rows": rows[:int(arg("limit", "500") or 500)],
            "total": len(rows), "all": len(mc.monsters),
            "note": models_mod.MONSTER_LINK_NOTE,
            "zoomNote": models_mod.ZOOM_NOTE,
        })

    def api_modelanim(self, arg):
        r"""One action of one model, posed frame by frame.

        `/api/modelanim?key=monster:103&action=110`

        The same evaluation `/api/anim` does for a player body, with one
        difference that is the whole point of this route: the **mesh** can
        change with the action. `models.ModelAction` says which file supplies
        the geometry and which supplies the motion; where they are the same
        file (69 of monster 103's 80 actions) the file's own `MOTI` is bound
        over its own `PHY`, and where they differ the motion set binds over
        the family's shared skeleton by ordinal, exactly as a player body's
        does (`C3Mesh::SetMotion`, graphic.dll 0x277C0).
        """
        c = self.cat
        key = arg("key", "")
        m = c.models.get(key)
        if m is None:
            return self._error(404, f"no model {key!r}")
        code = arg("action", "") or m.default_action()
        act = m.action(code)
        if act is None:
            return self._error(404, f"{key} has no action {code!r}")
        ck = (key, code)
        with c._anim_lock:
            hit = c._model_anim_cache.get(ck)
        if hit is not None:
            return self._json(hit)

        out = {"key": key, "kind": m.kind, "action": code,
               "label": act.label, "group": act.group,
               "confidence": act.confidence, "evidence": act.evidence,
               "motion": act.motion, "mesh": act.mesh,
               "selfContained": act.self_contained,
               "texture": m.texture, "textureMethod": m.texture_method,
               "textureKind": m.texture_kind,
               "frameIntervalMs": builder_mod.DEFAULT_FRAME_MS,
               "timingNote": builder_mod.TIMING_NOTE,
               "layoutNote": models_mod.LAYOUT_NOTE,
               "frames": 0, "chunks": [], "sockets": [], "bounds": None,
               "loop": "cyclic", "chain": None, "rootMotion": None,
               "alias": (not act.distinct), "aliasOf": act.alias_of}
        if not act.available or not act.mesh:
            out["error"] = act.reason or (
                f"action {code} does not ship for {key}. "
                + models_mod.MISSING_NOTE)
            return self._json(out)

        try:
            mesh_raw = c.read(act.mesh)
            motion_raw = (mesh_raw if act.motion == act.mesh
                          else c.read(act.motion))
        except FileNotFoundError as e:
            out["error"] = f"missing file: {e}"
            return self._json(out)
        mpm = attach.PartMesh.parse(motion_raw, act.motion) \
            if attach is not None else None
        bpm = (mpm if act.motion == act.mesh else
               (attach.PartMesh.parse(mesh_raw, act.mesh)
                if attach is not None else None))
        # The self-contained call is made from a geometry *hint* (the
        # mesh<->texture index), and a hint built on another install lies:
        # 6090 monster dirs ship one family mesh plus MOTI-only action files
        # where CCO put PHY in every action. The bytes are in hand here, so
        # believe them -- a "self-contained" file with no phy chunks renders
        # nothing, and the family's base mesh is what the motion binds over.
        # The self-contained call comes from a geometry *hint* (the
        # mesh<->texture index), and a hint built on another install lies:
        # 6090 monster dirs put geometry in the family mesh (1.c3) or the
        # standby file (100.c3) and ship the other actions MOTI-only, where
        # CCO put PHY in every action file. The bytes are in hand here, so
        # believe them -- rendering a motion-only file draws nothing, and
        # the fix is to draw a sibling that really has geometry.
        if not c3_to_json(mesh_raw, act.mesh, motion_set=None)["meshes"]:
            d = act.mesh.rsplit("/", 1)[0]
            for cand in (m.mesh, f"{d}/1.c3", f"{d}/100.c3"):
                if not cand or cand == act.mesh:
                    continue
                try:
                    raw = c.read(cand)
                except FileNotFoundError:
                    continue
                if c3_to_json(raw, cand, motion_set=None)["meshes"]:
                    mesh_raw = raw
                    bpm = (attach.PartMesh.parse(raw, cand)
                           if attach is not None else None)
                    out["mesh"] = cand
                    out["selfContained"] = False
                    break

        n = 1
        if builder_mod.animmod is not None and mpm is not None:
            clip = builder_mod.animmod.Clip(
                m.shape or "", "000", code, act.motion, act.source, mpm, bpm,
                builder_mod.DEFAULT_FRAME_MS)
            out.update({"loop": clip.loop, "chain": clip.chain_next,
                        "aligned": clip.aligned,
                        "declaredFrames": clip.frame_count})
            n = max(1, min(int(clip.play_length), 120))
            try:
                track = clip.root_track()
                out["rootMotion"] = {
                    "maxXY": round(max((abs(t[0]) + abs(t[1]) for t in track),
                                       default=0.0), 3),
                    "riseUp": round(max((t[2] for t in track), default=0.0) -
                                    min((t[2] for t in track), default=0.0), 3),
                }
            except Exception:                             # pragma: no cover
                pass
        out["frames"] = n

        for f in range(n):
            scene = c3_to_json(mesh_raw, act.mesh, motion_set=mpm, frame=f)
            if f == 0:
                out["chunks"] = [{"index": x["index"], "name": x["name"],
                                  "frames": []} for x in scene["meshes"]]
                out["scene"] = {**scene, "meshes": scene["meshes"]}
            for i, x in enumerate(scene["meshes"]):
                if i < len(out["chunks"]):
                    out["chunks"][i]["frames"].append(x["positions"])
            try:
                anchors = partsmod.socket_anchors(mesh_raw, motion_set=mpm,
                                                  frame=f)
                out["sockets"].append({k: v.matrix for k, v in anchors.items()
                                       if v.matrix})
            except Exception:                             # pragma: no cover
                out["sockets"].append({})
        try:
            out["bounds"] = partsmod.body_bounds(mesh_raw, motion_set=mpm,
                                                 frame=0)
        except Exception:                                 # pragma: no cover
            pass
        with c._anim_lock:
            if len(c._model_anim_cache) > 16:
                c._model_anim_cache.clear()
            c._model_anim_cache[ck] = out
        return self._json(out)

    #: Old-client action codes, for labelling sibling motion files. The
    #: numbering is the client's own (docs/animation.md); anything not listed
    #: still shows, by its bare code.
    ACTION_LABELS = {
        "100": "stand", "101": "stand (alt)", "110": "walk", "111": "walk 2",
        "120": "run", "121": "run 2", "130": "attack", "131": "attack 2",
        "150": "hurt", "160": "die", "170": "sit", "190": "special",
        "230": "cast", "250": "jump", "300": "idle", "310": "idle 2",
        "320": "idle 3", "330": "emote", "400": "dance", "410": "bow",
    }

    def _sibling_actions(self, logical: str) -> list[dict]:
        """The ``.c3`` files beside this mesh that are its action set.

        The old client splits a model from its animation: ``c3/npc/001/1.c3``
        holds the geometry, and ``100.c3``/``101.c3``/``190.c3`` beside it are
        MOTI-only containers, one per action, binding over that geometry by
        ordinal.  Without this the viewer plays the model's own idle track and
        looks like it has no animation at all.

        **Which files those are is `collection.action_code`'s question, not
        this method's.** Three layouts ship and the rule was written twice --
        here and in `gather_parts` -- which is how one of them ended up
        knowing about the Collection's naming while the other still did not.
        One rule, two callers, and what the viewer offers to play is by
        construction what collecting would keep.
        """
        from collection import actions_beside
        return [{"code": code, "path": p,
                 "label": self.ACTION_LABELS.get(code, "action " + code)}
                for code, p, _anchored in actions_beside(
                    self.cat.list_under, logical)]

    def api_meshanim(self, arg):
        r"""A container's own motion, posed frame by frame, by path.

        `/api/meshanim?path=zephyr/garments1/0959.c3`

        `/api/modelanim` answers the same question for a *catalogued* model.
        Assets recovered from a community archive are in no catalogue, but
        they carry their own motion the same way: MOTI chunk *i* binds to PHY
        chunk *i* by ordinal (`C3Mesh::SetMotion`), which is exactly the
        `act.motion == act.mesh` case there. So this evaluates the identical
        path -- `attach.PartMesh.parse` for the motion set, then `c3_to_json`
        per frame -- with the file supplying both halves.

        Motion can also come from a *sibling*: in a look directory the
        3-digit files are actions over a shared skeleton. Pass
        `motion=<path>` to bind one of those over this geometry.
        """
        c = self.cat
        logical = arg("path", "")
        if not logical:
            return self._error(400, "path required")
        motion_path = arg("motion", "") or logical
        ck = ("meshanim", logical.lower(), motion_path.lower())
        with c._anim_lock:
            hit = c._model_anim_cache.get(ck)
        if hit is not None:
            return self._json(hit)

        try:
            mesh_raw = c.read(logical)
            motion_raw = (mesh_raw if motion_path == logical
                          else c.read(motion_path))
        except FileNotFoundError as e:
            return self._error(404, f"missing file: {e}")
        if not mesh_raw.startswith(b"MAXFILE"):
            return self._error(400, f"not a C3 container: {logical}")

        out = {"path": logical, "motion": motion_path,
               "frameIntervalMs": builder_mod.DEFAULT_FRAME_MS,
               "timingNote": builder_mod.TIMING_NOTE,
               "frames": 0, "chunks": [], "sockets": [], "bounds": None,
               "loop": "cyclic", "chain": None,
               "actions": self._sibling_actions(logical)}
        mpm = attach.PartMesh.parse(motion_raw, motion_path)             if attach is not None else None
        # PartMesh pairs MOTI to PHY by ordinal on its chunk list; a
        # container with no motion at all has motion=None on every chunk.
        has_motion = mpm is not None and any(
            c.motion is not None for c in mpm.chunks)
        if not has_motion:
            out["error"] = ("this container carries no MOTI track, so there "
                            "is nothing to play")
            return self._json(out)
        bpm = (mpm if motion_path == logical else
               (attach.PartMesh.parse(mesh_raw, logical)
                if attach is not None else None))

        n = 1
        if builder_mod.animmod is not None:
            clip = builder_mod.animmod.Clip(
                "", "000", "self", motion_path, "container", mpm, bpm,
                builder_mod.DEFAULT_FRAME_MS)
            out.update({"loop": clip.loop, "chain": clip.chain_next,
                        "aligned": clip.aligned,
                        "declaredFrames": clip.frame_count})
            n = max(1, min(int(clip.play_length), 120))

        # Frames are baked positions, so cost is frames x vertices x 3. A
        # 9,656-vertex garment over 100 frames is a 24 MB response that
        # wedges the page, so budget on vertices rather than frame count and
        # say what was trimmed instead of silently truncating.
        verts = sum(len(c.phy.vertices) for c in mpm.chunks
                    if getattr(c.phy, "vertices", None))
        #: measured at ~25 bytes of JSON per vertex per frame
        budget = 160_000                      # vertex-frames, about 4 MB
        picks = list(range(n))
        if verts and n * verts > budget:
            cap = max(2, budget // max(1, verts))
            # Sample ACROSS the clip rather than truncating it: 16 frames
            # spread over a 100-frame cycle still shows the whole motion,
            # where the first 16 would only show its opening.
            picks = [round(i * (n - 1) / (cap - 1)) for i in range(cap)]
            picks = sorted(set(picks))
            out["framesSampled"] = {
                "declared": n, "kept": len(picks),
                "why": f"{verts} vertices per frame; sampled evenly to stay "
                       f"under ~{budget} vertex-frames"}
        out["frames"] = len(picks)
        out["vertices"] = verts
        out["frameIndices"] = picks

        for fi, f in enumerate(picks):
            scene = c3_to_json(mesh_raw, logical, motion_set=mpm, frame=f)
            if fi == 0:
                out["chunks"] = [{"index": x["index"], "name": x["name"],
                                  "frames": []} for x in scene["meshes"]]
                out["scene"] = scene
            for i, x in enumerate(scene["meshes"]):
                if i < len(out["chunks"]):
                    out["chunks"][i]["frames"].append(x["positions"])
        try:
            out["bounds"] = partsmod.body_bounds(mesh_raw, motion_set=mpm,
                                                 frame=0)
        except Exception:                                 # pragma: no cover
            pass
        with c._anim_lock:
            if len(c._model_anim_cache) > 16:
                c._model_anim_cache.clear()
            c._model_anim_cache[ck] = out
        return self._json(out)

    def _appearance_part(self, table: str, ident: str):
        ini = self.cat.tables.get(table)
        if not ini:
            return None
        app = ini.get(ident)
        if not app or not app.parts:
            return None
        pr = app.parts[0]
        return {
            "id": app.ident, "table": table,
            "meshId": pr.mesh, "textureId": pr.texture,
            "mesh": self.cat.resolve_id(pr.mesh, "mesh"),
            "texture": self.cat.resolve_id(pr.texture, "texture"),
            "material": pr.material,
        }

    def api_figure(self, arg):
        r"""Compose a loadout into one renderable figure.

        Query: `body=<id>` plus `<slot>=<id>` for each equipped part, e.g.
        `/api/figure?body=002135300&armet=002119310&r_weapon=410005`.

        The body is the subject; every other part is positioned at a socket
        anchor derived from the body's own skin clusters. That derivation is an
        approximation and says so -- see tools/parts.py. `attachConfidence` is
        carried in the payload so the UI can be honest on screen.
        """
        c = self.cat
        c.wait_tables(20)
        body_id = arg("body", "")
        body_table = arg("bodyTable", "body")
        if not body_id:
            return self._error(400, "a body is required: the character is the subject")
        body = self._appearance_part(body_table, body_id)
        if not body or not body.get("mesh"):
            return self._error(404, f"body {body_id!r} has no resolvable mesh")

        raw = self.cat.read(body["mesh"])
        # The idle action motion, not the mesh's embedded MOTI: the engine never
        # draws a bind pose (`Mesh::Draw` always applies a motion, 0x2607E) and
        # the embedded track is whatever the artist saved -- a T-pose on
        # 004134000. docs/attachment.md §8.2.
        #
        # `action=` / `frame=` let the builder pose the figure anywhere in a
        # shipped motion instead of only at idle frame 0. Same lookup, same
        # ordinal binding; only the 3dmotion.ini key changes.
        action_code = arg("action", "100") or "100"
        try:
            pose_frame = max(0, int(arg("frame", "0") or 0))
        except ValueError:
            pose_frame = 0
        try:
            # The equipped weapon decides which motion set the body plays:
            # an armed idle is a different pose, and asking for set 000 with
            # a club in hand is what left every armed character standing
            # empty-handed.
            wset = "000"
            for slot in ("r_weapon", "l_weapon"):
                wid = arg(slot, "")
                if wid and len(wid) > 3:
                    wset = wid[:-3]
                    break
            action = partsmod.idle_motion(body_id, c.root, wset, action_code)
            pose_set = wset
            if action is None and wset != "000":
                action = partsmod.idle_motion(body_id, c.root, "000",
                                              action_code)
                pose_set = "000"
        except Exception:                                # pragma: no cover
            action = None
            pose_set = "000"
        if action is None and action_code != "100":
            out_warn = f"no motion ships for action {action_code}; showing idle"
            try:
                action = partsmod.idle_motion(body_id, c.root)
            except Exception:                            # pragma: no cover
                action = None
            action_code = "100"
        else:
            out_warn = ""
        anchors = partsmod.socket_anchors(raw, motion_set=action, frame=pose_frame)
        anchors, socket_corr = apply_socket_corrections(
            anchors, c.plugin, body_id, weapon_set=pose_set,
            action=action_code, frame=pose_frame)
        bounds = partsmod.body_bounds(raw, motion_set=action, frame=pose_frame)
        pm = partsmod.PartManifest(c.root, tables=c.tables)

        out = {
            "body": {**body,
                     "scene": c3_to_json(raw, body["mesh"], motion_set=action,
                                         frame=pose_frame),
                     "action": action_code, "frame": pose_frame,
                     # The weapon set is part of the key and was hardcoded
                     # `000` here, so this line claimed the unarmed motion no
                     # matter what the body was actually posed with. It is
                     # the panel you would check to catch exactly that -- the
                     # previous armed-motion bug was found by reading it --
                     # so a wrong answer here is worse than none.
                     "pose": (f"action motion 3dmotion.ini "
                              f"{int(body_id[:3])}{pose_set}{action_code} "
                              + (f"(weapon set {wset} animates from "
                                 f"{attach.motion_set_for(wset)}) "
                                 if attach is not None and pose_set != "000"
                                 and attach.motion_set_for(wset) != wset
                                 else "")
                              + f"frame {pose_frame}" if action
                              else "the mesh's own embedded MOTI (no action "
                                   "motion resolved for this body)")},
            "bodyBounds": bounds,
            "anchors": {k: {"pos": list(v.pos), "source": v.source,
                            "bone": v.bone, "confidence": v.confidence,
                            "matrix": v.matrix}
                        for k, v in anchors.items()},
            "attachConfidence": (
                "verified" if any(v.confidence == "verified" and v.matrix
                                  for v in anchors.values()) else "inferred"),
            "attachNote": (
                "world = Mbone x Msock x Mrole (row-vector), recovered from "
                "Role3D.dll and graphic.dll by tools/attach.py. Msock is bone 0 of "
                "the motion at the socket chunk's ORDINAL index — PHY and MOTI are "
                "two independent lists, not adjacent pairs. Mbone is the equipped "
                "part's own motion, indexed per vertex, and is where its scale and "
                "axis correction live; it is baked into the emitted positions. The "
                "body is posed by its idle action motion from 3dmotion.ini, because "
                "the engine never draws a bind pose. Where a body ships no usable "
                "motion the anchor falls back to a skin-cluster estimate — position "
                "only, no rotation — and says so per socket."),
            # A correction is the app deliberately disagreeing with the
            # client, so it travels with the payload and is named per socket.
            # Silent would be indistinguishable from a broken reader.
            "socketCorrections": socket_corr,
            "parts": [],
            "warnings": ([out_warn] if out_warn else []),
        }

        for slot in pm.ordered():
            if slot.name == "body":
                continue
            ident = arg(slot.name, "")
            if not ident:
                continue
            rec = self._appearance_part(slot.name, ident)
            if not rec or not rec.get("mesh"):
                out["warnings"].append(
                    f"{slot.label}: {ident} has no resolvable mesh")
                continue
            # Body-specificity is decided by the ids themselves, not by a slot
            # whitelist: armor.ini and armet.ini ids carry a 001-004 prefix and
            # weapon.ini / armet1.ini / head.ini / misc.ini ids do not, so this
            # test never fires spuriously on a slot that is universal.
            if body_id[:3] in bodyfacets.BODY_TYPES and \
                    ident[:3] in bodyfacets.BODY_TYPES and ident[:3] != body_id[:3]:
                out["warnings"].append(
                    f"{slot.label} {ident} is art for body type {ident[:3]}, "
                    f"but the body is {body_id[:3]} — it will not fit properly")
            # The socket is the PLUGIN's answer, not RolePart.ini's. The ini
            # lists what the engine understands; the meshes decide what
            # exists, and 6090 declares four dummies no body carries. A
            # plugin returning "" means "this client cannot attach that
            # slot", which is why it is skipped rather than drawn at the body
            # origin -- a trinket in someone's navel is a worse answer than
            # an absent one.
            declared = self.cat.plugin.slot_socket(slot.name)
            if declared == "":
                out["warnings"].append(
                    f"{slot.label}: this client ships no socket for the "
                    f"{slot.name} slot, so {ident} cannot be attached")
                continue
            a = anchors.get(declared or slot.socket)
            try:
                # bake_motion: stage 2 of the attachment chain. Indexed per
                # vertex over both bone influences, so it cannot ride on a
                # single model matrix -- see the note in parts.py.
                pscene = c3_to_json(self.cat.read(rec["mesh"]), rec["mesh"],
                                    bake_motion=True)
            except Exception as e:
                out["warnings"].append(f"{slot.label}: {e}")
                continue
            off = [0.0, 0.0, 0.0]
            for i, k in enumerate(("dx", "dy", "dz")):
                try:
                    off[i] = float(arg(f"{slot.name}_{k}", "0") or 0)
                except ValueError:
                    pass
            pos = list(a.pos) if a else [0.0, 0.0, 0.0]
            # The transform chain, kept as separate inspectable stages rather
            # than one collapsed multiply -- see the long note in parts.py.
            # Stage 2 (the part's own MOTI) is decoded and reported but NOT
            # applied: the composition order has not been read out of the
            # engine yet (task #18), and guessing it is how this goes wrong in
            # the other direction.
            try:
                chain = partsmod.attach_chain(
                    self.cat.read(rec["mesh"]),
                    (pscene["meshes"][0]["name"] if pscene["meshes"] else ""),
                    a)
            except Exception as e:                        # pragma: no cover
                chain = {"stages": [], "note": f"chain unavailable: {e}"}
            out["parts"].append({
                "slot": slot.name, "label": slot.label, "socket": slot.socket,
                "id": rec["id"], "mesh": rec["mesh"], "texture": rec["texture"],
                "translate": [pos[0] + off[0], pos[1] + off[1], pos[2] + off[2]],
                "offset": off,
                "matrix": (a.matrix if a else None),
                "attachChain": chain,
                "bboxRender": _scene_bounds(pscene),
                "anchorSource": a.source if a else "no anchor for this socket",
                "anchorConfidence": a.confidence if a else "none",
                "headKind": partsmod.head_kind(ident)
                if slot.name in ("armet", "armet_dx8") else None,
                "hairColour": partsmod.hair_colour(ident)
                if slot.name in ("armet", "armet_dx8") else None,
                "scene": pscene,
            })
        return self._json(out)

    # -- API: effects ------------------------------------------------------
    def api_effect(self, arg):
        r"""One `3DEffect.ini` name, resolved to playable geometry.

        `/api/effect?name=m-b02` -> layers, each with its C3 parts decoded:
        PHY geometry + its `MOTI` keys + the three `C3Key` channels + the
        flipbook grid + the UV scroll, or a `SHAP` line + its `SMOT` track.
        The client plays it with the algorithm in docs/effects.md §8.
        """
        name = arg("name", "")
        sc = self.cat.effects.scene(name)
        if not sc.found:
            return self._json({"name": name, "found": False, "error": sc.error},
                              404 if name else 400)
        return self._json({"found": True, **sc.payload})

    def api_effects_for(self, arg):
        r"""Every effect attached to one weapon appearance, with each name
        already resolved to meshes and textures.

        Three *separate* things, and the distinction is the point
        (docs/effects.md §1): an always-on **aura**, a per-action **attack
        trail** that only quality 6-9 weapons have, and an **impact spark**
        that is drawn at the TARGET and never on the character.
        """
        ident = arg("id", "")
        if not ident:
            return self._error(400, "id= (a weapon appearance) is required")
        pl = self.cat.effects
        eff = self.cat.assetcat.weapon_effects(ident)
        if not eff:
            db = pl.db
            if db is not None:
                try:
                    es = db.effects_for_weapon(ident)
                    imp = es.impact
                    eff = {"aura": es.aura, "attack": dict(es.attack),
                           "type": es.weapon_type, "typeName": es.type_name,
                           "hitEffect": imp.hit_effect if imp else "",
                           "blockEffect": imp.blk_effect if imp else "",
                           "hitSound": imp.hit_sound if imp else ""}
                except Exception:
                    eff = {}
        out = {"id": ident, "type": eff.get("type", ""),
               "typeName": eff.get("typeName", ""),
               "available": pl.available, "error": pl._db_error,
               "roles": []}

        def add(role, name, note):
            if not name:
                return
            sc = pl.scene(name)
            out["roles"].append({
                "role": role, "name": name, "note": note,
                "found": sc.found, "error": sc.error,
                "effect": sc.payload if sc.found else None,
            })

        add("aura", eff.get("aura", ""), "always on — the 999 wildcard action")
        for action, nm in sorted((eff.get("attack") or {}).items()):
            add(f"trail:{action}", nm,
                "attack swing trail — only quality 6-9 weapons have one")
        add("impact", eff.get("hitEffect", ""),
            "impact spark — drawn at the TARGET, not on the character")
        add("block", eff.get("blockEffect", ""),
            "blocked-hit spark — drawn at the TARGET")
        out["hasTrail"] = any(r["role"].startswith("trail:") for r in out["roles"])
        out["trailNote"] = (
            "No attack trail is correct data, not a missing asset: only weapon "
            "quality 6-9 carries one (docs/effects.md §2.2).")
        return self._json(out)

    def api_weaponmotion(self, arg):
        r"""`ini/WeaponMotion.ini` for one weapon appearance.

        A weapon's animation is a **mesh swap**, not a deformation
        (docs/effects.md §4.5), so the displayed mesh changes with the action.
        Returns every action's mesh with the scene already decoded for the
        requested one, so the viewport can swap without a second round trip.
        """
        c = self.cat
        c.wait_tables(20)
        ident = arg("id", "")
        action = arg("action", "999")
        if not ident:
            return self._error(400, "id= (a weapon appearance) is required")
        default_mesh = ""
        rec = (self._appearance_part("r_weapon", ident)
               or self._appearance_part("l_weapon", ident))
        if rec:
            default_mesh = rec.get("mesh") or ""
        info = c.effects.weapon_meshes(ident, default_mesh)
        chosen = (info["actions"].get(action, {}).get("mesh")
                  or info["default"] or default_mesh)
        info["action"] = action
        info["chosen"] = chosen
        info["chosenSource"] = ("WeaponMotion.ini " + action
                                if action in info["actions"]
                                else info["defaultSource"])
        info["texture"] = rec.get("texture") if rec else None
        info["exists"] = bool(chosen) and c.exists(chosen)
        if chosen and c.exists(chosen):
            try:
                info["scene"] = c3_to_json(c.read(chosen), chosen)
            except Exception as e:                        # pragma: no cover
                info["error"] = str(e)
        info["note"] = (
            "A weapon does not deform; the client swaps in a different mesh "
            "per action (ini/WeaponMotion.ini, VERIFIED — 1,863 rows, all "
            "resolving). Without this a swing trail animates against a static "
            "weapon.")
        return self._json(info)

    # -- API: what goes with this -----------------------------------------
    def _companion_files(self, subject: str) -> list[dict]:
        """Motion files and effects that belong with ``subject``.

        These are the parts of an asset that have no picture: a MOTI-only
        action file and an effect scene are both real, both needed for the
        thing to behave, and neither renders as a thumbnail. They are listed
        by name and kind rather than being left out because they are not
        pretty.
        """
        out: list[dict] = []
        key = (subject or "").replace("\\", "/").lower()
        if not key.endswith(".c3"):
            return out
        for a in self._sibling_actions(key):
            out.append({"path": a["path"],
                        "label": a["path"].rsplit("/", 1)[-1],
                        "kind": "motion", "role": "motion",
                        "note": a["label"], "method": "sibling action file",
                        "detail": "MOTI over this model, bound by ordinal",
                        "primary": False, "noThumb": True})
        for e in self._effects_for_asset(key):
            # A weapon's entry names visual effects AND its sounds. Both
            # belong with the asset; calling a .wav an "effect" does not.
            snd = e.rsplit(".", 1)[-1] in ("wav", "mp3", "ogg")
            out.append({"path": e, "label": e.rsplit("/", 1)[-1],
                        "kind": "sound" if snd else "effect",
                        "role": "sound" if snd else "effect",
                        "note": "" if snd else (
                            e.rsplit("/", 2)[-2] if e.count("/") > 1 else ""),
                        "method": "effect table",
                        "detail": ("played by this asset" if snd
                                   else "drawn with this asset"),
                        "primary": False, "noThumb": True})
        return out

    def _effects_for_asset(self, subject: str) -> list[str]:
        """Every effect file that plays with this asset, best effort.

        Two routes, because the client has two: an appearance that names the
        asset can carry weapon effects (aura / attack trail / impact spark),
        and an effect directory can simply sit beside it. Anything that does
        not resolve is dropped rather than guessed at.
        """
        c = self.cat
        found: list[str] = []
        seen: set[str] = set()

        def take(p: str) -> None:
            k = (p or "").replace("\\", "/").lower()
            if k and k not in seen and c.exists(k):
                seen.add(k)
                found.append(k)

        try:
            for ref in c.references(subject):
                ident = ref.get("appearance")
                if not ident:
                    continue
                eff = c.assetcat.weapon_effects(ident) or {}
                for v in eff.values():
                    if isinstance(v, str):
                        take(v)
                    elif isinstance(v, (list, tuple)):
                        for x in v:
                            take(x if isinstance(x, str)
                                 else (x or {}).get("path", ""))
        except Exception:                                  # pragma: no cover
            pass
        return found

    def api_related(self, arg):
        c = self.cat
        c.wait_tables(20)
        ident = arg("id", "")
        table = arg("table", "")
        path = arg("path", "")
        appearance = None
        if ident:
            for part_name, ini in c.tables.items():
                if table and part_name != table:
                    continue
                app = ini.get(ident)
                if not app or not app.parts:
                    continue
                pr = app.parts[0]
                appearance = {
                    "id": app.ident, "table": part_name,
                    "mesh": c.resolve_id(pr.mesh, "mesh"),
                    "texture": c.resolve_id(pr.texture, "texture"),
                    "mixTex": pr.mix_tex, "thirdTex": pr.third_tex,
                    "fourthTex": pr.fourth_tex,
                }
                break
        groups = c.assetcat.related_groups(
            appearance=appearance, path=path,
            siblings=c.appearances_using_mesh)
        for g in groups:
            for it in g["items"]:
                it["source"] = ("loose" if it["path"] in c.loose
                                else c.archived.get(it["path"], "?"))
        # The left-hand list shows a mesh and its textures as ONE entry, so the
        # first thing this panel has to do is take that entry apart again: the
        # mesh and each texture, independently selectable, each labelled with
        # the rule that linked them and whether that rule is authored or
        # inferred. Put first because it is the entry the user just clicked.
        subject = path or (appearance or {}).get("mesh") or ""
        if subject:
            comp = c.unified.components(subject)
            items = []
            for cc in comp["components"]:
                if not c.exists(cc["path"]):
                    continue
                # role and kind are rendered as their own elements; `note` is
                # only what is left over, so nothing reads twice.
                note = f"{cc['method']} {cc['confidence']:.2f}" if cc["method"] else ""
                items.append({"path": cc["path"],
                              "label": cc["path"].rsplit("/", 1)[-1],
                              "note": note,
                              "kind": cc["kind"], "method": cc["method"],
                              "detail": cc["detail"], "role": cc["role"],
                              "primary": cc["primary"],
                              "thumbNote": c.unified.thumb_note(cc["path"]),
                              "primarySkin": (c.unified.skins_a_model(cc["path"])
                                              if cc["role"] == "texture" else None)})
            if len(items) > 1:
                groups.insert(0, {
                    "id": "components",
                    "title": "This entry: mesh and textures",
                    "note": comp["note"] + " Click either half to open it on "
                            "its own — the list keeps them as one row, this "
                            "panel keeps them separate.",
                    "items": items,
                    "components": True,
                })
        companions = self._companion_files(
            path or (appearance or {}).get("mesh") or "")
        if companions:
            for it in companions:
                it["source"] = ("loose" if it["path"] in c.loose
                                else c.archived.get(it["path"], "?"))
            groups.append({
                "id": "companions",
                "title": "Animation and effects",
                "note": "Files that belong with this asset but have nothing "
                        "to show: motion tracks and effect scenes. Listed by "
                        "name and kind.",
                "items": companions,
                "noThumbs": True,
            })
        return self._json({"id": ident, "table": table, "path": path,
                           "groups": groups,
                           "unifiedSource": c.unified.source,
                           "effectsAvailable": assetcat.effects is not None,
                           "meshtexAvailable": assetcat.meshtex is not None})

    def api_components(self, arg):
        r"""The independently selectable parts of one unified entry.

        A mesh and the textures that belong to it are ONE row in the left-hand
        list; this is what the right-hand panel puts back so either half can be
        opened, tagged or swapped on its own. Every part carries the rule that
        linked it and whether that rule is **authored** (a shipped ini says so)
        or **inferred** (a naming convention measured off the corpus) --
        tools/meshtex.py's distinction, passed through rather than flattened.
        """
        path = arg("path", "")
        if not path:
            return self._error(400, "path required")
        u = self.cat.unified
        out = u.components(path)
        for c in out["components"]:
            c["exists"] = self.cat.exists(c["path"])
            c["source"] = ("loose" if c["path"] in self.cat.loose
                           else self.cat.archived.get(c["path"], ""))
            c["hasThumb"] = bool(u.thumb_for(c["path"]))
            c["thumbNote"] = u.thumb_note(c["path"])
            if c["role"] == "texture":
                # task #21's used_by_mesh: is this the mesh's *chosen* skin?
                # A narrower question than "does it belong to this mesh", and
                # the panel shows both rather than conflating them.
                c["primarySkin"] = u.skins_a_model(c["path"])
        out["available"] = u.available
        return self._json(out)

    def api_thumb(self, arg):
        r"""A thumbnail for any logical asset.

        Three tiers, in order: task #21's pre-rendered PNG from
        `out/thumbs/` if it has got to this asset yet; the decoded texture
        itself, which is what the lists used before; then 404, which the page
        renders as its checkerboard placeholder. Generation is incremental, so
        a mesh with no thumbnail yet is expected rather than an error.
        """
        path = arg("path", "")
        if not path:
            return self._error(400, "path required")
        u = self.cat.unified
        if arg("refresh", "") == "1":
            u.refresh_thumbs()
        f = u.thumb_for(path)
        note = u.thumb_note(path)
        # under a server view the unified index reads that server's own
        # manifest (out/thumbs/servers/<name>/), so a hit here is always a
        # render of the right bytes.
        if f:
            try:
                data = Path(f).read_bytes()
                ctype = "image/png" if data[:4] == b"\x89PNG" else \
                    (mimetypes.guess_type(f)[0] or "application/octet-stream")
                # so the page can badge a render that took a fallback path
                return self._send(200, data, ctype,
                                  {"X-Thumb-Note": note} if note else None)
            except OSError:                              # pragma: no cover
                pass
        if path.lower().endswith(".dds"):
            return self.api_texture(arg)
        # a mesh with no rendered thumbnail: fall back to its best texture
        best = (u.textures_of(path) or [{}])[0].get("texture")
        if best and self.cat.exists(best):
            return self.api_texture(lambda k, d="": best if k == "path" else arg(k, d))
        return self._error(404, f"no thumbnail for {path}")

    def api_dirs(self, arg):
        """Logical directories with a file count, so the file browser has a
        tree to start from."""
        c = self.cat
        want_ext = arg("ext", "").lower()
        counts: dict[str, int] = {}
        for p in c.all_paths:
            if want_ext and not p.endswith(want_ext):
                continue
            d = p.rsplit("/", 1)[0] if "/" in p else ""
            counts[d] = counts.get(d, 0) + 1
        # Alphabetical, and complete. The old order (most files first) and
        # the 4,000 cap made sense for a flat dropdown; the browser builds a
        # folder tree now, and a tree missing 1,386 of its 5,386 folders --
        # exactly what the cap dropped on the Zephyr view -- is a tree that
        # cannot reach half the library.
        rows = sorted(({"dir": k, "count": v} for k, v in counts.items()),
                      key=lambda r: r["dir"])
        return self._json(rows)

    def api_files(self, arg):
        c = self.cat
        d = arg("dir", "").strip("/").lower()
        query = arg("q", "").strip().lower().replace("\\", "/")
        ext = arg("ext", "").lower()
        recursive = arg("recursive", "1") != "0"
        # Typing a path fragment means "find this anywhere" -- honouring the
        # directory filter as well just returns nothing and looks broken.
        if "/" in query or arg("all", "") == "1":
            d = ""
        offset = int(arg("offset", "0") or 0)
        limit = min(int(arg("limit", "300") or 300), 2000)
        # One row per ASSET, not per file: a mesh absorbs the textures that are
        # authored for it (or are its rank-1 candidate), so a garment and its
        # seven colourways stop being eight rows. Textures nothing owns -- map
        # tiles, icons, faces -- keep their own row. `unified=0` turns it off.
        unified = arg("unified", "1") != "0" and not ext
        u = c.unified if unified else None

        src = arg("source", "")
        want_tag = arg("tag", "").strip().lower()
        rows = []
        for p in c.all_paths:
            if ext and not p.endswith(ext):
                continue
            if src and (("loose" if p in c.loose
                         else c.archived.get(p, "?")) != src):
                continue
            if want_tag and want_tag not in [
                    t.lower() for t in c.auto_tags(p)]:
                continue
            if d:
                if recursive:
                    if not p.startswith(d + "/"):
                        continue
                elif p.rsplit("/", 1)[0] != d:
                    continue
            rows.append(p)

        merged = u.collapse(rows) if u else [
            {"path": p, "role": "", "folded": 0, "textures": [], "search": p}
            for p in rows]
        if query:
            # searching a texture id must still find the row it was merged into
            merged = [r for r in merged if query in r["search"]]
        total = len(merged)
        out = []
        for r in merged[offset:offset + limit]:
            p = r["path"]
            out.append({"path": p,
                        "source": "loose" if p in c.loose else c.archived.get(p, "?"),
                        "role": r["role"], "folded": r["folded"],
                        "tags": c.auto_tags(p),
                        "textures": r["textures"][:8],
                        "thumb": (r["textures"][0] if r["textures"] else
                                  (p if p.endswith(".dds") else None))})
        return self._json({"total": total, "offset": offset, "rows": out,
                           "unified": bool(u),
                           "unifiedSource": (u.source if u else ""),
                           "unifiedNote": (
                               "One row per asset: a mesh and the textures "
                               "authored for it are the same thing, so they are "
                               "one entry. Open it and the right-hand panel "
                               "lists the mesh and every texture separately."
                               if u else "")})

    # -- API: provenance ---------------------------------------------------
    def api_provenance(self, arg):
        c = self.cat
        logical = arg("path")
        if not logical:
            return self._error(400, "path required")
        pv = c.provenance(logical).to_json()
        c.wait_tables(5)
        refs = c.references(logical)
        pv["references"] = refs[:200]
        pv["referenceCount"] = len(refs)

        staged = STAGE / logical
        pv["staged"] = staged.is_file()
        pv["stagedPath"] = str(staged) if staged.is_file() else ""
        pv["stagedSize"] = staged.stat().st_size if staged.is_file() else 0

        # a copy-pasteable next action, exactly as cobrowse.py does
        cmds = []
        if pv["exists"]:
            if logical.lower().endswith(".dds"):
                cmds.append(f"py -3 tools/comod.py extract {logical} --png")
                cmds.append(f"py -3 tools/comod.py import-png mods/work/{Path(logical).stem}.png {logical}")
            else:
                cmds.append(f"py -3 tools/comod.py extract {logical}")
            cmds.append(f"py -3 tools/comod.py info {logical}")
        if pv["staged"]:
            cmds.append("py -3 tools/comod.py diff")
            cmds.append("py -3 tools/comod.py install --dry-run")
        pv["commands"] = cmds

        # format detail
        try:
            data = self._asset_bytes(logical, arg("preview", ""))
            if data[:4] == b"DDS ":
                pv["dds"] = dds.info_dict(data)
            elif data.startswith(b"MAXFILE"):
                c3 = C3File(data, strict=False)
                pv["c3"] = {"chunks": [{"tag": ch.name.strip(), "size": ch.size,
                                        "name": c3.node_name(ch)}
                                       for ch in c3.chunks]}
        except Exception as e:
            pv["formatError"] = str(e)
        return self._json(pv)

    def api_rawinfo(self, arg):
        logical = arg("path")
        data = self.cat.read(logical)
        return self._json({"path": logical, "size": len(data),
                           "head": data[:32].hex()})

    # -- API: geometry -----------------------------------------------------
    def api_mesh(self, arg):
        logical = arg("path")
        if not logical:
            return self._error(400, "path required")
        try:
            data = self._asset_bytes(logical, arg("preview", ""))
        except FileNotFoundError:
            return self._error(404, f"not found: {logical}")
        if not data.startswith(b"MAXFILE"):
            return self._error(400, f"not a C3 container: {logical}")
        out = c3_to_json(data, logical)
        if not out.get("meshes"):
            # Old-client convention: 3-digit action files in a look directory
            # are MOTI-only -- the model itself is the long-id sibling. Say
            # so, and where, instead of presenting an empty viewport.
            note = "no geometry in this container (motion/animation data)"
            key = logical.replace("\\", "/").lower()
            d = key.rsplit("/", 1)[0] + "/"
            sib = [p for p in self.cat.list_under(d)
                   if p.endswith(".c3") and p != key
                   and len(Path(p).stem) >= 6 and Path(p).stem.isdigit()]
            if sib:
                out["modelSibling"] = sib[0]
                note += f" — the model is {sib[0]}"
            out["note"] = note
        if arg("guesstex", "") == "1":
            out["guessedTexture"] = self.cat.texture_for_mesh(logical)
        return self._json(out)

    # -- API: textures -----------------------------------------------------
    def _asset_bytes(self, logical: str, preview_token: str = "") -> bytes:
        """Resolve a logical path to bytes, honouring (in order) an active
        preview override, the staged mod tree, then the game's own
        loose-before-archive rule."""
        if preview_token:
            p = PREVIEW_DIR / _safe_token(preview_token)
            if p.is_file():
                return p.read_bytes()
        return self.cat.read(logical)

    def api_texture(self, arg):
        logical = arg("path")
        if not logical:
            return self._error(400, "path required")
        size = int(arg("size", "0") or 0)
        level = int(arg("level", "0") or 0)
        token = arg("preview", "")
        src = arg("src", "")            # "stage" -> read from mods/stage
        key = (logical.lower(), size, level, token, src)
        with self.server.cache_lock:                  # type: ignore[attr-defined]
            hit = self.server.tex_cache.get(key)      # type: ignore[attr-defined]
        if hit:
            return self._send(200, hit, "image/png")
        try:
            if src == "stage":
                data = safepath.confine(STAGE, logical).read_bytes()
            else:
                data = self._asset_bytes(logical, token)
        except safepath.UnsafePath as e:
            return self._error(400, str(e))
        except (FileNotFoundError, OSError):
            return self._error(404, f"not found: {logical}")
        try:
            if data[:4] == b"DDS ":
                png = dds.to_png(data, level=level, max_size=size)
            else:
                from PIL import Image
                im = Image.open(io.BytesIO(data)).convert("RGBA")
                if size and max(im.size) > size:
                    im.thumbnail((size, size), Image.NEAREST)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                png = buf.getvalue()
        except Exception as e:
            return self._error(415, f"cannot decode {logical}: {e}")
        with self.server.cache_lock:                  # type: ignore[attr-defined]
            cache = self.server.tex_cache             # type: ignore[attr-defined]
            if len(cache) > 3000:
                cache.clear()
            cache[key] = png
        return self._send(200, png, "image/png")

    # -- API: preview / stage / install ------------------------------------
    def post_preview(self, body: bytes, arg):
        """Accept a replacement image and hold it in out/viewer/preview/.

        NON-DESTRUCTIVE: this writes only into the project's own out/ tree.
        Nothing reaches mods/stage until the user presses Stage, and nothing
        reaches the game install until they press Install.
        """
        logical = arg("path")
        name = arg("name", "upload")
        if not logical:
            return self._error(400, "path required")
        if not body:
            return self._error(400, "empty body")
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

        fourcc = arg("format", "")
        orig_info = None
        try:
            orig = self.cat.read(logical)
            if orig[:4] == b"DDS ":
                orig_info = dds.info_dict(orig)
                fourcc = fourcc or orig_info["fourcc"] or "DXT3"
        except Exception:
            pass
        fourcc = fourcc or "DXT3"

        warnings = []
        if body[:4] == b"DDS ":
            payload = body                              # user supplied a DDS
            try:
                new_info = dds.info_dict(payload)
            except Exception as e:
                return self._error(400, f"unreadable DDS: {e}")
        else:
            try:
                payload = dds.encode_png_to_dds(body, fourcc)
                new_info = dds.info_dict(payload)
            except Exception as e:
                return self._error(400, f"cannot encode image as {fourcc}: {e}")

        if orig_info:
            if (new_info["width"], new_info["height"]) != (orig_info["width"], orig_info["height"]):
                warnings.append(
                    f"size {new_info['width']}x{new_info['height']} differs from the "
                    f"original {orig_info['width']}x{orig_info['height']}. The engine "
                    f"expects power-of-two textures; a mismatch usually renders wrong "
                    f"rather than crashing, but that is not verified safe.")
            if new_info["fourcc"] != orig_info["fourcc"]:
                warnings.append(
                    f"format {new_info['fourcc']} differs from the original "
                    f"{orig_info['fourcc']}. DXT1 has 1-bit alpha; if the original is "
                    f"DXT3 it probably needs its 4-bit alpha.")
        w, h = new_info["width"], new_info["height"]
        if w & (w - 1) or h & (h - 1):
            warnings.append(f"{w}x{h} is not power-of-two.")

        # `secrets`, not `hash()`. The old form was
        # `abs(hash((logical, time.time(), name)))`, which is a *hash of
        # guessable inputs* -- the logical path is known, the name is known, and
        # `time.time()` narrows to whatever window the attacker cares to sweep.
        # 32 hex chars from a CSPRNG, matching `_TOKEN_RE`'s existing shape.
        token = f"{secrets.token_hex(16)}.dds"
        (PREVIEW_DIR / token).write_bytes(payload)
        return self._json({"token": token, "logical": logical,
                           "info": new_info, "warnings": warnings,
                           "bytes": len(payload),
                           "previewPath": str(PREVIEW_DIR / token)})

    def post_snapshot(self, body: bytes, arg):
        """Save a viewport frame (a `data:image/png;base64,...` URL) under
        out/viewer/shots/.  Only ever writes inside the project."""
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", arg("name", "viewport"))[:80] or "viewport"
        text = body.decode("ascii", "replace")
        marker = "base64,"
        if not text.startswith("data:image/png;") or marker not in text:
            return self._error(400, "expected a data:image/png;base64 URL")
        import base64
        try:
            png = base64.b64decode(text.split(marker, 1)[1])
        except Exception as e:
            return self._error(400, f"bad base64: {e}")
        shots = OUTDIR / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        dest = shots / f"{name}.png"
        dest.write_bytes(png)
        return self._json({"file": str(dest), "bytes": len(png)})

    def post_stage(self, body: bytes, arg):
        """Copy an active preview into mods/stage/<logical>.

        This is the same staged tree comod.py's diff/install/uninstall operate
        on, so the commit path, backups and revert are unchanged.

        `logical` comes off a query string, so it goes through
        `safepath.confine` before it becomes a destination. Without that,
        `path=..\\..\\..\\Program Files\\...` wrote straight into the game
        install and bypassed comod's backup and manifest entirely.
        """
        logical = arg("path")
        token = arg("token")
        if not logical or not token:
            return self._error(400, "path and token required")
        try:
            dest = safepath.confine(STAGE, logical)
        except safepath.UnsafePath as e:
            return self._error(400, str(e))
        src = PREVIEW_DIR / _safe_token(token)
        if not src.is_file():
            return self._error(404, "preview expired")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        self._mapedit_invalidate(logical)
        return self._json({"staged": str(dest), "bytes": dest.stat().st_size,
                           "logical": logical})

    def post_unstage(self, body: bytes, arg):
        logical = arg("path")
        try:
            dest = safepath.confine(STAGE, logical)
        except safepath.UnsafePath as e:
            return self._error(400, str(e))
        if dest.is_file():
            dest.unlink()
            for parent in dest.parents:
                if parent == STAGE:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        self._mapedit_invalidate(logical)
        return self._json({"unstaged": logical})

    def _mapedit_invalidate(self, logical: str) -> None:
        """Drop MapEditor art caches after a stage / unstage.

        The MapEditor draws through the stage tree (`mapedit.StageFirst`), so
        staging a `.dds` has to reach the tiles that use it. This is a no-op
        when nobody has opened the MapEditor -- building it just to invalidate
        an empty cache would cost every `.dds` swap a PuzzleLibrary.
        """
        if getattr(self.cat, "_mapedit", None) is None:
            return
        try:
            self.cat.mapedit.invalidate(logical)
        except Exception:                             # noqa: BLE001
            return
        with self.server.cache_lock:                  # type: ignore[attr-defined]
            cache = self.server.tex_cache             # type: ignore[attr-defined]
            for k in [k for k in cache
                      if isinstance(k, tuple) and k and k[0] == "mapedit"]:
                cache.pop(k, None)

    def api_stage_list(self, arg):
        c = self.cat
        rows = []
        for p in _staged_files():
            logical = p.relative_to(STAGE).as_posix()
            pv = c.provenance(logical)
            new = p.read_bytes()
            status = "NEW"
            old_size = 0
            if pv.exists:
                try:
                    old = c.read(logical)
                    old_size = len(old)
                    status = "same" if old == new else "MODIFIED"
                except Exception:
                    status = "MODIFIED"
            rows.append({"logical": logical, "status": status,
                         "newBytes": len(new), "oldBytes": old_size,
                         "originalSource": pv.source})
        return self._json({"stageDir": str(STAGE), "rows": rows})

    api_diff = api_stage_list

    def api_installs(self, arg):
        r"""Which game installs a staged mod could be written to.

        Three sources, because no one of them is complete: what discovery
        finds (conventional paths, the registry, Steam), what this workbench
        has already installed to, and the one the viewer is browsing. A
        second client sitting in a folder none of those know about is
        normal -- `check` validates a pasted path the same way the library
        chooser does, so typing one is a first-class answer rather than a
        fallback.
        """
        import comod as comod_mod
        probe = str(arg("check", "") or "").strip().strip('"')
        if probe:
            return self._json({"check": comod_mod.moddable_install(probe)})

        current = str(self.server.game_root or "")     # type: ignore[attr-defined]
        recorded = {r["root"].lower(): r for r in comod_mod.installed_roots()}
        seen: dict[str, dict] = {}

        def add(path, why, *, only_if_valid=False):
            p = str(path)
            key = p.lower()
            if key in seen:
                return
            # `iter_candidates` is a *discovery* generator -- it yields every
            # conventional path on every drive plus every uninstall entry in
            # the registry, ~160 of them here, and expects the caller to stop
            # at the first that validates. Listing them all put "Blender",
            # "Steam" and "Notepad++" in a menu of game installs. Discovery
            # is offered only where it actually found one; the current view
            # and anything already installed to are shown regardless, because
            # their absence is information.
            if only_if_valid and not coroot.looks_like_root(p):
                return
            rec = recorded.get(key)
            seen[key] = {
                "root": p, "why": why,
                "ok": comod_mod.moddable_install(p)["ok"],
                "isCurrent": key == current.lower(),
                # What is on record here, so "install" vs "already installed,
                # revert first" is answerable before you press anything.
                "installed": bool(rec),
                "installedFiles": rec["files"] if rec else 0,
                "installedUtc": rec["installedUtc"] if rec else "",
            }

        if current:
            add(current, "the view you are browsing")
        for r in comod_mod.installed_roots():
            add(r["root"], "has a mod installed from here")
        try:
            for path, source, detail in coroot.iter_candidates():
                add(path, detail or source, only_if_valid=True)
        except Exception:                                # pragma: no cover
            pass
        return self._json({
            "current": current,
            "stageDir": str(STAGE),
            "stagedFiles": len(_staged_files()),
            "installs": list(seen.values()),
        })

    def _install_root(self, body: bytes) -> tuple[str, Optional[str]]:
        """The install a write is aimed at: what was asked for, or the view.

        Validated here rather than trusted, because this is the one value in
        the request that decides which folder on the machine gets written to.
        """
        try:
            doc = json.loads(body or b"{}")
        except ValueError:
            doc = {}
        want = str(doc.get("root") or "").strip().strip('"')
        if not want:
            return str(self.server.game_root), None      # type: ignore[attr-defined]
        import comod as comod_mod
        chk = comod_mod.moddable_install(want)
        if not chk["ok"]:
            return "", (f"{want} is not a Conquer client: missing "
                        + ", ".join(chk["missing"]))
        return want, None

    def post_install(self, body: bytes, arg):
        dry = arg("dry", "1") != "0"
        root, why = self._install_root(body)
        if why:
            return self._error(400, why)
        return self._run_comod(["install", "--dry-run" if dry else "--yes"],
                               root=root)

    def post_uninstall(self, body: bytes, arg):
        dry = arg("dry", "1") != "0"
        root, why = self._install_root(body)
        if why:
            return self._error(400, why)
        return self._run_comod(["uninstall", "--dry-run" if dry else "--yes"],
                               root=root)

    def _run_comod(self, extra: list[str], root: str = ""):
        """Every write to the game install goes through comod.py, never through
        this process.  comod.py owns the backup + manifest + revert."""
        cmd = [sys.executable, str(HERE / "comod.py"), "--root",
               root or str(self.server.game_root), *extra]  # type: ignore[attr-defined]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               cwd=str(PROJECT))
        except Exception as e:
            return self._error(500, f"comod.py failed to launch: {e}")
        return self._json({"cmd": " ".join(cmd), "returncode": r.returncode,
                           "stdout": r.stdout, "stderr": r.stderr})


_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{1,32}\.dds$")


def _safe_token(t: str) -> str:
    if not _TOKEN_RE.match(t or ""):
        raise ValueError(f"bad preview token {t!r}")
    return t


def _staged_files() -> list[Path]:
    if not STAGE.is_dir():
        return []
    return sorted(p for p in STAGE.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------

def build_catalog(root: Path, server_view=None) -> Catalog:
    return Catalog(root, server_view)


def serve(root: Optional[Path], port: int, host: str = "127.0.0.1",
          open_browser: bool = True,
          found: "Optional[coroot.Found]" = None,
          server_view=None, library: "Optional[Path]" = None,
          server_name: str = "") -> None:
    r"""Serve the viewer.  ``root=None`` starts in **setup mode**.

    Setup mode is not an error path bolted on: a fresh clone on a machine
    where the client lives somewhere unusual is a completely ordinary first
    run.  Rather than exiting with a traceback, the server comes up with no
    catalogue and serves one page that says what was searched and takes a
    path, which it then validates and remembers.
    """
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind anywhere but loopback")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    cat = None
    if root is None:
        _log("no game install found -- starting in setup mode")
    else:
        _log(f"install root: {root}"
             + (f"   (found via {found.source}: {found.detail})" if found else ""))
        cat = build_catalog(root, server_view)

    httpd = ViewerServer((host, port), Handler, cat, root)
    httpd.library = Path(library) if library else None
    httpd.server_name = server_name if (server_name and cat) else ""
    if cat is not None:
        httpd.views[httpd.server_name] = cat
    #: How the root was resolved, so the health panel can say "registry" or
    #: "CO_ROOT" rather than re-deriving it and possibly disagreeing.
    httpd.root_found = found                       # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    _log(f"serving {url}   (Ctrl-C to stop)")
    if not dds.HAVE_NUMPY:
        _log("numpy not installed -- running the pure-python paths "
             "(everything works, texture decode is a little slower)")

    # First-run report on the console as well as in the browser, so the
    # person who started this from a .cmd window sees it even if the browser
    # never opens.
    try:
        rep = health.collect(root, with_thumbnails=root is not None)
        health.write_report(rep)
        for prob in rep["problems"]:
            _log(f"[{prob['severity']}] {prob['what']}")
        th = rep.get("thumbnails") or {}
        if health.should_prompt(th):
            _log("thumbnails: none generated. The viewer asks in the browser "
                 "before generating any -- nothing starts on its own.")
    except Exception as e:                              # pragma: no cover
        _log(f"health check failed: {type(e).__name__}: {e}")

    # State-changing POSTs need this. The browser gets it automatically; a
    # script or `curl` can read it from GET /api/token, which is printed here
    # so nobody has to go looking for why a POST returned 403.
    _log(f"CSRF token for this run: {httpd.csrf_token}")
    _log("  scripts: send it as the X-CO-Token header, or read GET /api/token")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down")
    finally:
        httpd.server_close()
        if cat is not None:
            cat.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    coroot.add_root_argument(ap)
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--library", metavar="DIR",
                    help="COmmunity Library root (for --server)")
    ap.add_argument("--server", metavar="NAME",
                    help="browse an imported community client through its "
                         "server profile: its own appearance/motion tables "
                         "and file namespace (see tools/assetdiff.py "
                         "--server)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--health", "--doctor", dest="health", action="store_true",
                    help="run the first-run health check on the console and "
                         "exit, without starting a server or a browser")
    ap.add_argument("--json", action="store_true",
                    help="with --health: machine-readable output")
    args = ap.parse_args(argv)

    if args.health:
        hargv: list[str] = []
        if args.json:
            hargv.append("--json")
        if args.root:
            hargv += ["--root", str(args.root)]
        return health.main(hargv)

    found = coroot.find(args.root)
    if found is None and args.root:
        # An explicit --root that is not an install is a mistake worth naming,
        # not something to paper over with the setup page.
        raise SystemExit(
            f"--root {args.root} is not a Conquer Online install: missing "
            + ", ".join(coroot.missing_parts(args.root)))
    # The COmmunity Library: given once with --library, remembered in the
    # per-user config after that, so the server picker in the UI just works.
    library = args.library or coroot.read_settings().get("community_library")
    if not library:
        # Nothing configured: if exactly one library is sitting in an obvious
        # place, use it and say so. A viewer that finds your asset library by
        # itself beats one that makes you discover a command-line flag.
        try:
            from colibrary import discover_libraries
            found_libs = discover_libraries()
        except Exception:                                  # pragma: no cover
            found_libs = []
        if len(found_libs) == 1:
            library = found_libs[0]["path"]
            _log(f"found a COmmunity Library at {library} -- using it "
                 f"(change it in the browser: 'Asset library')")
    if args.library:
        saved = coroot.write_settings(community_library=str(Path(args.library)))
        _log(f"library: {args.library}  (remembered in {saved})")
    server_view = None
    if args.server:
        if not library:
            raise SystemExit("--server needs --library DIR "
                             "(the COmmunity Library root)")
        if found is None:
            raise SystemExit("--server still needs the baseline install "
                             "(the library only stores what differs from it)")
        ServerView = _core_colibrary().ServerView
        server_view = ServerView(library, args.server, found.path)
        _log(f"server view: {args.server}  "
             f"({len(server_view.filemap)} paths from {library})")
    serve(found.path if found else None, args.port,
          open_browser=not args.no_browser, found=found,
          server_view=server_view, library=library,
          server_name=args.server or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
