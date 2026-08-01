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

    # -- lifecycle ---------------------------------------------------------
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode: str, jobs: int = 0, limit: int = 0) -> dict:
        if mode not in self.MODES:
            raise ValueError(f"unknown mode {mode!r}; "
                             f"expected one of {sorted(self.MODES)}")
        with self.lock:
            if self.running():
                return {"started": False, "reason": "already running",
                        **self.status()}
            argv = [sys.executable, str(HERE / "thumbs.py"),
                    "--root", str(self.root), *self.MODES[mode]]
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
            _labels = {"l": "library", "b": "baseline", "w": "baseline"}
            for key, ref in self.server_view.filemap.items():
                self.archived[key] = _labels.get(ref[0], "baseline")
        else:
            for h, nm in self.names.items():
                hit = self.arc_by_hash.get(h)
                if hit:
                    self.archived[nm.lower()] = hit[0]

        self.all_paths: list[str] = sorted(set(self.loose) | set(self.archived))
        self._path_set = set(self.all_paths)

        self.tables: dict[str, object] = {}
        self.ref_index: dict[str, list[dict]] = {}
        self.facets: Optional[bodyfacets.BodyFacets] = None
        self._tables_ready = threading.Event()

        # Browsing taxonomy. `references` is empty until the appearance tables
        # finish loading, so classification of table-backed assets improves a
        # moment after start-up; the path rules work immediately.
        self.assetcat = assetcat.AssetCatalog(
            self.root, table_membership=self.references, exists=self.exists,
            list_under=self.list_under)
        self.maps = mapindex.MapIndex(self.root, exists=self.exists)
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
                self._mapedit = mapedit_mod.MapEditor(self.root, assets=self.assets)
            return self._mapedit

    @property
    def unified(self) -> unify.UnifiedIndex:
        """The mesh<->texture relation (tools/meshtex.py's answers) plus the
        rule that collapses a related pair into one list row."""
        with self._unified_lock:
            if self._unified is None:
                self._unified = unify.UnifiedIndex(self.root, exists=self.exists)
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

                self._models = models_mod.ModelCatalogue(
                    self.root, paths=self.all_paths, exists=self.exists,
                    read=self.read, has_geometry=geom, texture_for=tex,
                    effect_names=self.effects.names)
                _log(f"model catalogue: {len(self._models.models)} models "
                     f"across {len(self._models.kinds())} kinds")
            return self._models

    def texture_for_mesh(self, mesh_logical: str) -> Optional[str]:
        """Best-guess texture for a `.c3` opened on its own (no appearance).

        INFERRED, and the inference is c3tex's: invert the appearance tables to
        find every `Texture<i>` paired with this `Mesh<i>`, then fall back to
        the mesh's own id as a texture id.  A mesh usually has several valid
        textures -- this returns the first that resolves.
        """
        key = mesh_logical.replace("\\", "/").lstrip("/").lower()
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
        return None

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
    c3 = C3File(data)
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
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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
            "/api/monsterrows": self.api_monsterrows,
            "/api/effect": self.api_effect,
            "/api/effects": self.api_effects_for,
            "/api/weaponmotion": self.api_weaponmotion,
            "/api/tags": self.api_tags,
            "/api/tags/export": self.api_tags_export,
            "/api/health": self.api_health,
            "/api/thumbs/status": self.api_thumbs_status,
            "/api/token": self.api_token,
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

    # -- API: server views ---------------------------------------------------
    def api_servers(self, arg):
        """The selectable asset sources: the baseline install plus every
        server profile catalogued in the COmmunity Library."""
        srv = self.server
        lib = srv.library                              # type: ignore[attr-defined]
        servers = []
        if lib:
            from colibrary import list_servers
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
                        from colibrary import ServerView
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

    def post_setroot(self, body: bytes, arg):
        r"""Accept a path typed into the setup page and remember it.

        Validated by *contents*, not by the string: an install root has to
        hold c3.wdf, data.wdf, ini/ and bin/64/.  Saving a path that does not
        would only move the failure later.
        """
        try:
            doc = json.loads(body.decode("utf-8") or "{}")
        except ValueError as e:
            return self._error(400, f"bad JSON: {e}")
        raw = str(doc.get("path") or "").strip().strip('"')
        if not raw:
            return self._error(400, "no path given")
        missing = coroot.missing_parts(raw)
        if missing:
            return self._json({
                "ok": False, "path": raw, "missing": missing,
                "error": f"{raw} is not a Conquer Online install: missing "
                         + ", ".join(missing)}, 400)
        scope = "repo" if doc.get("scope") == "repo" else "user"
        saved = coroot.save_root(raw, scope)
        _log(f"install root set to {raw} (saved in {saved})")
        return self._json({"ok": True, "path": str(Path(raw)),
                           "savedTo": str(saved),
                           "restartRequired": self.cat is None})

    def api_thumbs_status(self, arg):
        runner = self.server.thumbs                    # type: ignore[attr-defined]
        state = health.thumbnail_state()
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
        try:
            res = runner.start(mode, int(doc.get("jobs") or arg("jobs", 0) or 0),
                               int(doc.get("limit") or arg("limit", 0) or 0))
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
            return not want_tags or want_tags.issubset(set(have))

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
            for t in tag_map.get(r["subject"], []):
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
                                      "tags": tag_map.get(r["subject"], [])})
            glist = sorted(groups.values(), key=lambda g: g["mesh"])
            for g in glist:
                g["texture"] = g["variants"][0]["texture"]
            payload["groupedTotal"] = len(glist)
            payload["groups"] = glist[offset:offset + limit]
            return self._json(payload)

        page = []
        for r in matched[offset:offset + limit]:
            row = dict(r)
            row["tags"] = tag_map.get(r["subject"], [])
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
        return self._json({"vocabulary": store.vocabulary(),
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
        tag_map = store.all_subjects()
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
            action = partsmod.idle_motion(body_id, c.root, "000", action_code)
        except Exception:                                # pragma: no cover
            action = None
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
        bounds = partsmod.body_bounds(raw, motion_set=action, frame=pose_frame)
        pm = partsmod.PartManifest(c.root, tables=c.tables)

        out = {
            "body": {**body,
                     "scene": c3_to_json(raw, body["mesh"], motion_set=action,
                                         frame=pose_frame),
                     "action": action_code, "frame": pose_frame,
                     "pose": (f"action motion 3dmotion.ini "
                              f"{int(body_id[:3])}000{action_code} "
                              f"frame {pose_frame}" if action
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
            a = anchors.get(slot.socket)
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
        sv = self.cat.server_view
        if f and sv is not None:
            ref = sv.filemap.get(sv._norm(path))
            if ref is not None and ref[0] == "l":
                # the pre-rendered thumbnail shows the *baseline* bytes; this
                # view's bytes differ. Fall through to decoding the real ones.
                f = None
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
        rows = sorted(({"dir": k, "count": v} for k, v in counts.items()),
                      key=lambda r: -r["count"])
        return self._json(rows[:4000])

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
        rows = []
        for p in c.all_paths:
            if ext and not p.endswith(ext):
                continue
            if src and (("loose" if p in c.loose
                         else c.archived.get(p, "?")) != src):
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
                c3 = C3File(data)
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

    def post_install(self, body: bytes, arg):
        dry = arg("dry", "1") != "0"
        return self._run_comod(["install", "--dry-run" if dry else "--yes"])

    def post_uninstall(self, body: bytes, arg):
        dry = arg("dry", "1") != "0"
        return self._run_comod(["uninstall", "--dry-run" if dry else "--yes"])

    def _run_comod(self, extra: list[str]):
        """Every write to the game install goes through comod.py, never through
        this process.  comod.py owns the backup + manifest + revert."""
        cmd = [sys.executable, str(HERE / "comod.py"), "--root",
               str(self.server.game_root), *extra]        # type: ignore[attr-defined]
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
        from colibrary import ServerView
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
