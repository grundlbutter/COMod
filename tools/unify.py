#!/usr/bin/env python3
r"""
unify.py -- one list entry per *asset*, not per file.

    py -3 tools/unify.py --stats
    py -3 tools/unify.py c3/mesh/002135000.c3
    py -3 tools/unify.py c3/texture/002135300.dds

THE PROBLEM
-----------
A garment is one thing, but it occupies two rows in a file list: the `.c3` and
the `.dds`. Worse, a mesh shared by seven colourways occupies eight rows. The
list is then mostly duplicates of things the user already sees.

THE RULE
--------
A **mesh is an entry.** A texture is an entry *only when nothing owns it*.
A texture is owned by a mesh when the pairing is either

  a. **authored** -- a shipped data file states it (`armor.ini`, `3DEffect.ini`,
     `3DSimpleObj.ini`, `npc.json`, `3dmotion.ini` -> appearance), or
  b. the mesh's **rank-1** candidate, which is how the same-stem convention
     (`c3/npc/185.c3` + `c3/npc/185.dds`) folds in.

Everything weaker -- `dir_sibling` and friends, 8,004 loose any-rank hits --
does **not** collapse anything, because folding a map tile under some unrelated
mesh would be worse than the duplication it removes.

Measured over this install:

    .c3 files with geometry           4,964    (1,426 more are pure MOTI/PTCL)
    ... with at least one texture     4,950    99.7%   (85.5% authored)
    .dds files                       66,832
    ... owned by a mesh               5,799    these stop being their own row
    ... standalone                   61,033    map tiles, icons, faces, UI

So the collapse is deliberately conservative: it removes 5,799 duplicate rows
and touches nothing else.

NOT 1:1, AND THE UI MUST NOT PRETEND IT IS
------------------------------------------
* A mesh can have several textures. 7 colourways of one garment is the common
  case, and `docs/appearance_ids.md` §1 is why.
* **A texture can belong to several meshes** -- 1,527 of the 5,808 do, e.g.
  `c3/texture/105000000.dds` is worn by five body types' motion meshes. It is
  filed under its highest-confidence owner and still listed under every other.

So `components()` returns the parts of an entry as a list with each one's
**method, kind and confidence**, and the viewer shows "`armor.ini` says so"
differently from "there is a `.dds` with the same stem". That distinction is
`tools/meshtex.py`'s and is passed through unchanged.

WHERE THE RELATION COMES FROM
-----------------------------
`tools/meshtex.py` (imported, never edited). Its full run costs a few seconds,
so this module prefers the coverage file that run already produced --
`out/meshtex/coverage.json` -- and falls back to building the index live. Either
way the answers are meshtex's.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import DEFAULT_ROOT                        # noqa: E402
import coroot                                            # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: DELETED: `COVERAGE = coroot.derived_path("out/meshtex/coverage.json")`.
#: C21 removed the code path that read it, because a constant resolved at
#: import names whichever install was configured *then* -- 6090 and 5517 both
#: reported the same 5042 meshes out of `patch5517-.../coverage.json`. The
#: constant itself was left behind, referenced by nothing but the comment
#: saying not to use it, which is an invitation to the next person. Resolve
#: per call through `coroot.find_derived`; it is a SAME-BASE fallback.

#: DELETED: `THUMB_DIR = coroot.derived_path("out/thumbs")`. Where the batch
#: renderer puts its output is per-install, so it is resolved per instance from
#: `self.root` -- see `_thumb_dir` in `__init__`, which already did. The module
#: constant survived beside it and was still read in two places: a comparison
#: that was false for every base but the one configured at import (so a
#: worktree inheritance never ran), and a texture-manifest read that served one
#: base's manifest for another. C55.
#: `manifest_meshes.json` is the 3.5 MB mesh-only slice of the 23 MB
#: `manifest.json`, same schema. The mesh slice is loaded eagerly because every
#: list row wants it; the full one only when a *texture* thumbnail is asked for.
THUMB_MESH_MANIFEST = "manifest_meshes.json"
THUMB_MANIFEST_NAMES = (THUMB_MESH_MANIFEST, "manifest.json", "thumbs.json",
                        "index.json")


def _describe(p: Path) -> str:
    """`p` relative to this checkout, or its full path when it is elsewhere.

    `coroot.find_derived` **deliberately** falls back to the primary checkout
    when a linked worktree has not built an artefact of its own, so the path
    it hands back is routinely outside `REPO` -- and `relative_to` raises on
    exactly that case.  This is only a label, so it reports the absolute path
    rather than failing: a worktree that is reading the primary checkout's
    index should say so, not pretend it built one.
    """
    try:
        return p.relative_to(REPO).as_posix()
    except ValueError:
        return p.as_posix()


@dataclass
class Component:
    """One part of a unified entry: the mesh, or one of its textures."""
    path: str
    role: str                 # "mesh" | "texture"
    method: str = ""
    kind: str = ""            # "authored" | "inferred"
    confidence: float = 0.0
    detail: str = ""
    primary: bool = False

    def to_json(self) -> dict:
        return dict(self.__dict__)


class UnifiedIndex:
    """mesh <-> texture, and the collapse rule built on top of it."""

    def __init__(self, root: Path = DEFAULT_ROOT,
                 exists: Optional[Callable[[str], bool]] = None,
                 coverage: Optional[Path] = None,
                 thumb_dir: Optional[Path] = None,
                 build: bool = True,
                 progress: Optional[Callable[[int, int], None]] = None,
                 persist: bool = False):
        self.root = Path(root)
        #: where thumbnail manifests live; a server view passes its own
        #: out/thumbs/servers/<name>/ so the base install's renders (same
        #: logical path, different bytes) are never shown for it.
        #:
        #: The default keys to **this index's root**, not to the process's
        #: configured install: two catalogues are live at once whenever the
        #: viewer compares one base against another, and "same logical path,
        #: different bytes" is as true between 6090 and 5517 as it is for a
        #: library server.
        self._thumb_dir = (Path(thumb_dir) if thumb_dir
                           else coroot.thumbs_dir(self.root))
        self._exists = exists or (lambda p: True)
        self.mesh_matches: dict[str, list[dict]] = {}
        self.source = ""
        self.error = ""
        # A linked worktree reads the primary checkout's coverage.json --
        # `find_derived` handles that, and it is a SAME-BASE fallback.
        #
        # There is deliberately no fallback to a module-level constant, and
        # since 2026-08-09 there is no such constant left to fall back to --
        # it was deleted (see the note where it used to live, above). One
        # resolved at import names whichever install was configured *then*,
        # so a base with no coverage file of its own silently loaded ANOTHER
        # install's index: 6090 and 5517 both reported the same 5042 meshes
        # out of `patch5517-.../coverage.json`. MEASURED --
        # `docs/CORRECTIONS.md` C21.
        #
        # `_load(None)` builds live from `meshtex` for THIS root instead:
        # slower, and correct. A missing per-base index means "build me",
        # never "borrow another base's answers" (`coroot.base_id`'s own rule).
        #: `build=False` yields a **legitimately empty** index rather than a
        #: half-built one: `available` is False, so every consumer takes the
        #: branch it already takes when `meshtex` is unavailable and each file
        #: stays its own row. It exists so a caller that must answer NOW can
        #: hand back a real object while the true index is still being built
        #: elsewhere -- see `coviewer.Catalog.unified_now`. The distinction
        #: matters and is kept in `source`: "not built yet" is a state that
        #: will change, "unavailable" is one that will not.
        self.built = build
        if build:
            self._load(coverage
                       or coroot.find_derived("out/meshtex/coverage.json",
                                              self.root),
                       progress=progress, persist=persist)
        else:
            self.source = f"not built yet for {self._base()}"

        #: texture -> [(mesh, match)] over every *owning* pairing
        self.texture_owners: dict[str, list[tuple[str, dict]]] = {}
        for mesh, matches in self.mesh_matches.items():
            for i, m in enumerate(matches):
                if m.get("kind") == "authored" or i == 0:
                    self.texture_owners.setdefault(m["texture"], []).append((mesh, m))
        #: texture -> the single mesh it is filed under
        self.primary_owner: dict[str, str] = {}
        for tex, owners in self.texture_owners.items():
            best = max(owners, key=lambda om: (om[1].get("kind") == "authored",
                                               om[1].get("confidence", 0.0)))
            self.primary_owner[tex] = best[0]

        self._thumbs: Optional[dict] = None

    # -- loading -----------------------------------------------------------
    def _base(self) -> str:
        """This index's install, for the `source` readout. Never raises.

        `source` is what tells a caller *which* index answered -- it is
        served on `/api/unified` and printed in the viewer -- so two indexes
        over two installs must not describe themselves identically. The
        file-backed case already distinguishes them, because the path it
        names contains `out/indexes/<base-id>/`. The two fallbacks did not:
        both said "built live", which made the readout unable to tell 5517's
        answers from 6090's at exactly the moment there is no index file to
        name. A readout is an instrument (`docs/handoff_5517_base_prep.md`
        §8.5), and this one was blind in the case that needs it most.
        """
        try:
            return coroot.base_id(self.root)
        except Exception:                                 # pragma: no cover
            return "unkeyed"

    def _load(self, coverage: Path | None,
              progress: Optional[Callable[[int, int], None]] = None,
              persist: bool = False) -> None:
        """Load this base's coverage index, or build one for this base.

        ``None`` means no per-base index exists yet. That is a legitimate
        state -- it is what a freshly keyed install looks like -- and it must
        NOT be resolved by reading some other base's file (C21).
        """
        if coverage is not None and coverage.is_file():
            try:
                data = json.loads(coverage.read_text("utf-8"))
                self.mesh_matches = {k: v.get("matches", [])
                                     for k, v in (data.get("meshes") or {}).items()}
            except Exception as e:                        # pragma: no cover
                self.error = f"{coverage.name}: {e}"
            else:
                # Cosmetic, and deliberately outside the guard above: this
                # label must never be able to discard a parse that worked.
                self.source = (f"{_describe(coverage)} "
                               f"({len(self.mesh_matches)} meshes)")
                return
        try:
            import meshtex                                # imported, never edited
            idx = meshtex.MeshTextureIndex(self.root)
            # ONE WRITER. The live build goes through the SAME
            # `build_coverage` the CLI writes coverage.json with, so the
            # answer built here and the answer later loaded from the file
            # are the same data -- including its per-mesh cap
            # (`meshtex.COVERAGE_MATCHES`). Before this the live branch kept
            # every match and the file kept 12, and the two states of one
            # viewer disagreed about which mesh owns a texture.
            #
            # `progress(done, total)` exists because this loop is 30-60 s on
            # a large client and the only honest thing to show someone
            # waiting is the renderer's OWN count, not an animation.
            rep = meshtex.build_coverage(idx, progress=progress)
            self.mesh_matches = {k: v.get("matches", [])
                                 for k, v in rep["meshes"].items()}
            self.source = f"tools/meshtex.py, built live for {self._base()}"
            if persist:
                # Keep what was just paid for. A server patch changes the
                # base id, the old file stops matching, and without this
                # every launch after a patch rebuilt live (30-60 s) forever.
                # A failed write costs the NEXT launch its fast path, not
                # this one its answer -- so it is reported, not raised.
                #
                # THERE IS DELIBERATELY NO SWITCH TO TURN THIS OFF FOR TESTS
                # (SD ruling, 2026-09-18). A gate run that leaves the
                # checkout's out/ warm is product behaviour, not
                # contamination. A gate-only opt-out would make the suite
                # exercise a path no user runs -- the family that hid both
                # coverage bugs found that day, because tests took ONE path
                # by ambient box state and nobody knew which. The cure for
                # order-dependence is that no test DEPENDS on the state: a
                # test whose answer differs fast vs live SETS the state it
                # needs (a temp checkout with the fallback off, or an explicit
                # build) and ASSERTS which path it took (the `unified` field
                # test_viewer's motion-toggle test checks); everything else
                # passes in both states, which the one writer above makes true.
                try:
                    dest = meshtex.write_coverage(
                        rep, self.root, tool="unify.py live build")
                    self.source += f", persisted to {_describe(dest)}"
                except Exception as e:                    # pragma: no cover
                    self.error = f"coverage not persisted: {e}"
        except Exception as e:                            # pragma: no cover
            self.error = (self.error + "; " if self.error else "") + \
                f"meshtex unavailable: {e}"
            self.source = (f"unavailable for {self._base()} "
                           "— every file stays its own entry")

    @property
    def available(self) -> bool:
        return bool(self.mesh_matches)

    # -- the relation ------------------------------------------------------
    def textures_of(self, mesh: str) -> list[dict]:
        return self.mesh_matches.get(_norm(mesh), [])

    def meshes_of(self, texture: str) -> list[tuple[str, dict]]:
        return self.texture_owners.get(_norm(texture), [])

    def owner_of(self, texture: str) -> Optional[str]:
        """The mesh a texture is filed under, or None when it stands alone."""
        return self.primary_owner.get(_norm(texture))

    def is_folded(self, path: str) -> bool:
        p = _norm(path)
        return p.endswith(".dds") and p in self.primary_owner

    # -- the collapse ------------------------------------------------------
    def add_pairs(self, pairs: dict[str, str], method: str = "same-stem",
                  confidence: float = 0.9) -> int:
        """Teach the index mesh->texture pairs it could not know about.

        `coverage.json` describes the *install*.  A community archive
        recovered by write order (docs/assets.md section 7) has its own
        pairs, and without them every model and its skin stay two rows.
        Existing matches win; the owner maps are rebuilt so `collapse`
        folds the new pairs too.
        """
        added = 0
        for mesh, tex in pairs.items():
            if mesh in self.mesh_matches:
                continue
            self.mesh_matches[mesh] = [{"texture": tex, "method": method,
                                        "confidence": confidence,
                                        "kind": "authored"}]
            added += 1
        if not added:
            return 0
        self.texture_owners = {}
        for mesh, matches in self.mesh_matches.items():
            for i, m in enumerate(matches):
                if m.get("kind") == "authored" or i == 0:
                    self.texture_owners.setdefault(
                        m["texture"], []).append((mesh, m))
        self.primary_owner = {}
        for tex, owners in self.texture_owners.items():
            best = max(owners, key=lambda om: (om[1].get("kind") == "authored",
                                               om[1].get("confidence", 0.0)))
            self.primary_owner[tex] = best[0]
        return added

    def collapse(self, paths: Iterable[str]) -> list[dict]:
        """One row per asset.

        A mesh keeps its row and absorbs its textures. A texture keeps its row
        unless a mesh owns it. Order is preserved, so the caller's sort still
        holds. Nothing is invented: a texture whose owner is not in `paths`
        (a filtered view, a single directory) still folds away only if the
        owner is present -- otherwise it would vanish from the list entirely.
        """
        paths = list(paths)
        present = set(paths)
        out: list[dict] = []
        for p in paths:
            if p.endswith(".dds"):
                owner = self.primary_owner.get(p)
                if owner and owner in present:
                    continue                  # folded into the owning mesh's row
                out.append({"path": p, "role": "texture", "folded": 0,
                            "textures": [], "search": p})
                continue
            if p.endswith(".c3"):
                matches = self.mesh_matches.get(p, [])
                texes = [m["texture"] for m in matches]
                folded = [t for t in texes if self.primary_owner.get(t) == p]
                out.append({
                    "path": p, "role": "mesh",
                    "folded": len(folded),
                    "textures": texes[:24],
                    "best": (matches[0] if matches else None),
                    # searching a texture id must still find the merged row
                    "search": " ".join([p] + texes[:24]),
                })
                continue
            out.append({"path": p, "role": "other", "folded": 0,
                        "textures": [], "search": p})
        return out

    # -- components, for "what goes with this" -----------------------------
    def components(self, path: str) -> dict:
        """The independently selectable parts of one entry.

        Given a mesh: that mesh plus every texture it resolves to.
        Given a texture: that texture plus every mesh that uses it.
        Each part carries the rule that produced the link, so the UI can say
        "armor.ini says so" and "there is a .dds with the same stem" as the
        different claims they are.
        """
        p = _norm(path)
        parts: list[Component] = []
        note = ""
        if p.endswith(".c3"):
            parts.append(Component(p, "mesh", primary=True,
                                   method="", kind="", confidence=1.0,
                                   detail="the geometry"))
            for i, m in enumerate(self.mesh_matches.get(p, [])):
                if not self._exists(m["texture"]):
                    continue
                parts.append(Component(
                    m["texture"], "texture", method=m.get("method", ""),
                    kind=m.get("kind", ""), confidence=m.get("confidence", 0.0),
                    detail=m.get("detail", ""), primary=(i == 0)))
            n = len(parts) - 1
            note = (f"{n} texture{'' if n == 1 else 's'} resolve for this mesh. "
                    f"The first is the best-ranked one and is what the viewport "
                    f"shows; the rest are the other skins the same geometry is "
                    f"shipped with." if n else
                    "No texture resolves for this mesh — 14 of the 4,964 meshes "
                    "in this install are in that position.")
        elif p.endswith(".dds"):
            owners = self.meshes_of(p)
            for mesh, m in owners:
                if not self._exists(mesh):
                    continue
                parts.append(Component(
                    mesh, "mesh", method=m.get("method", ""),
                    kind=m.get("kind", ""), confidence=m.get("confidence", 0.0),
                    detail=m.get("detail", ""),
                    primary=(self.primary_owner.get(p) == mesh)))
            parts.insert(0, Component(p, "texture", primary=not owners,
                                      confidence=1.0, detail="the skin"))
            n = len(parts) - 1
            note = (f"Worn by {n} mesh{'' if n == 1 else 'es'}."
                    if n else
                    "No mesh uses this texture. Most textures are in that "
                    "position — map tiles, icons and faces are not skins for "
                    "any geometry.")
        else:
            parts.append(Component(p, "other", primary=True))
        return {"path": p,
                "components": [c.to_json() for c in parts],
                "note": note,
                "source": self.source}

    # -- thumbnails (task #21) ---------------------------------------------
    @staticmethod
    def _read_manifest(path: Path) -> dict[str, dict]:
        r"""One manifest file -> {logical: record}.

        Task #21's schema is `{"entries": {logical: {...,"thumb": "..."}}}`,
        thumbnail paths relative to `out/thumbs/`. The reader is tolerant of the
        variations that schema could plausibly have taken -- a top-level
        mapping, or `entries`/`thumbs`/`items` holding a mapping *or* a list --
        because that file is another agent's to design and this one only reads
        it. `tools/test_viewer.py` pins all four shapes.
        """
        out: dict[str, dict] = {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:                                 # pragma: no cover
            return out
        rows = data
        for key in ("entries", "thumbs", "items", "assets"):
            if isinstance(data, dict) and isinstance(data.get(key), (dict, list)):
                rows = data[key]
                break
        it = rows.items() if isinstance(rows, dict) else \
            ((r.get("asset") or r.get("logical") or r.get("source") or
              r.get("mesh") or r.get("path") or "", r)
             for r in rows if isinstance(r, dict))
        for logical, rec in it:
            if not logical:
                continue
            if isinstance(rec, str):
                rec = {"thumb": rec}
            if not isinstance(rec, dict):
                continue
            f = next((rec[k] for k in ("thumb", "thumbnail", "file", "png",
                                       "image", "path") if rec.get(k)), None)
            if not f:
                continue
            fp = Path(f)
            if not fp.is_absolute():
                # against the manifest's own directory, so a manifest read
                # from the primary checkout names that checkout's images
                fp = path.parent / f
            out[_norm(str(logical))] = {**rec, "file": str(fp)}
        return out

    def thumbs(self) -> dict[str, dict]:
        r"""The mesh thumbnails: `out/thumbs/manifest_meshes.json`.

        4,950 of 4,950 meshes rendered, 0 failed. The mesh slice is 3.5 MB
        against the full manifest's 23 MB and is what every list row wants; the
        66,834 *texture* thumbnails are loaded separately and only when one is
        actually asked for, because a texture already has a fast decode path.

        Re-read on demand (`refresh_thumbs`) so a page opened while generation
        is still running picks up what has landed since; anything missing simply
        has no thumbnail and the list falls back to the texture, then to the
        checkerboard placeholder.
        """
        if self._thumbs is None:
            self._thumbs = {}
            for name in THUMB_MANIFEST_NAMES:
                p = self._thumb_dir / name
                if p.is_file():
                    self._thumbs = self._read_manifest(p)
                    break
            else:
                # nothing local: a linked worktree inherits the primary
                # checkout's render, same name priority.  Never for a
                # server-specific dir -- another checkout's *base* renders
                # would be the wrong bytes.
                # Resolved for THIS instance's root, not at import. Against a
                # module constant this comparison was false for every base but
                # whichever was configured when the module loaded, so the
                # inheritance below silently never ran for the others. C55.
                # Also the READ-ONLY bridge from the old layout: a folder from
                # `coroot.thumbs_dir` that has no manifest yet still shows a
                # cache already rendered into this (or the primary)
                # checkout's `out/thumbs`, rather than going blank until a
                # full re-render. Images resolve against that manifest's own
                # folder. A generation run still writes to the new folder.
                if self._thumb_dir == coroot.thumbs_dir(self.root):
                    for name in THUMB_MANIFEST_NAMES:
                        p = coroot.find_derived("out/thumbs/" + name, self.root)
                        if p is not None and p.is_file():
                            self._thumbs = self._read_manifest(p)
                            break
        return self._thumbs

    _tex_thumbs: Optional[dict] = None

    def texture_thumbs(self) -> dict[str, dict]:
        """The texture half of the manifest, loaded lazily (23 MB, ~0.2 s)."""
        if self._tex_thumbs is None:
            # This instance's own thumb dir. It was `THUMB_DIR`, resolved at
            # import, so a viewer holding 6090 beside 5517 read ONE of them's
            # texture manifest for both -- the same shape as C21, in the same
            # file, one attribute over. C55.
            p = self._thumb_dir / "manifest.json"
            if (not p.is_file()
                    and self._thumb_dir == coroot.thumbs_dir(self.root)):
                p = coroot.find_derived("out/thumbs/manifest.json",
                                        self.root) or p
            self._tex_thumbs = self._read_manifest(p) if p.is_file() else {}
        return self._tex_thumbs

    def thumb_record(self, logical: str) -> Optional[dict]:
        key = _norm(logical)
        rec = self.thumbs().get(key)
        if rec is None and key.endswith(".dds"):
            rec = self.texture_thumbs().get(key)
        return rec

    def thumb_for(self, logical: str) -> Optional[str]:
        r"""The rendered thumbnail for an asset, or None.

        **`low_coverage` returns None on purpose.** 19 meshes are real geometry
        that occupies well under a percent of the frame -- sub-pixel ribbons and
        slivers -- and their render is a near-empty square. That reads as a
        broken thumbnail rather than as a thin mesh, so the caller falls through
        to the texture, which is at least informative. Task #21 recommends
        exactly this.
        """
        rec = self.thumb_record(logical)
        if not rec or rec.get("low_coverage"):
            return None
        p = Path(rec["file"])
        return str(p) if p.is_file() else None

    def thumb_note(self, logical: str) -> str:
        """How a thumbnail was produced, when it was not the default path.

        42 meshes needed a named fallback -- `no_cull` (25 single-sided planes),
        `uv_cell` (10 effect flipbooks that are blank at frame 0), `no_motion`
        (4), `alpha_from_luma` (3). They are honest renders, just not from the
        default settings, and the UI says so on hover rather than pretending.
        """
        rec = self.thumb_record(logical)
        if not rec:
            return ""
        bits = []
        if rec.get("low_coverage"):
            bits.append("draws under 1% of the frame — showing its texture "
                        "instead of a near-empty render")
        fb = rec.get("fallback")
        if fb:
            bits.append(f"rendered with the {fb} fallback")
        if rec.get("container") == "not-dds":
            bits.append("this .dds is not actually a DDS container")
        return "; ".join(bits)

    def refresh_thumbs(self) -> int:
        """Re-read the manifests -- generation is incremental, so a page opened
        early should pick up thumbnails rendered since."""
        self._thumbs = None
        self._tex_thumbs = None
        return len(self.thumbs())

    def skins_a_model(self, texture: str) -> Optional[bool]:
        r"""Task #21's `used_by_mesh` flag: is this texture some mesh's chosen
        skin? 2,782 of the 66,834 are; the other 64,052 are map art, UI and
        icons.

        **This is NOT the collapse rule**, and the difference matters. The flag
        marks a mesh's *primary* texture only -- `002135300` is true and its
        eleven colourways `002135310`, `002135400` ... are false -- so folding on
        it would merge one skin into the garment and leave the other eleven as
        their own rows, which is the clutter this whole change removes. The
        collapse uses every *authored* pairing (§`collapse`); this flag is kept
        as the quick "does this texture skin anything at all" answer and as a
        cross-check.
        """
        rec = self.texture_thumbs().get(_norm(texture))
        return None if rec is None else bool(rec.get("used_by_mesh"))

    # -- stats --------------------------------------------------------------
    def stats(self, paths: Optional[Iterable[str]] = None) -> dict:
        out = {
            "source": self.source,
            "error": self.error,
            "meshes": len(self.mesh_matches),
            "meshesWithTexture": sum(1 for v in self.mesh_matches.values() if v),
            "authoredBest": sum(1 for v in self.mesh_matches.values()
                                if v and v[0].get("kind") == "authored"),
            "ownedTextures": len(self.primary_owner),
            "texturesWithSeveralMeshes": sum(
                1 for v in self.texture_owners.values()
                if len({m for m, _ in v}) > 1),
            "thumbnails": len(self.thumbs()),
        }
        if paths is not None:
            rows = self.collapse(paths)
            out["rowsIn"] = len(list(paths))
            out["rowsOut"] = len(rows)
        return out


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").lstrip("/").lower()


def _cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    idx = UnifiedIndex(Path(a.root))
    if a.stats or not a.path:
        print(json.dumps(idx.stats(), indent=2))
        return 0
    rec = idx.components(a.path)
    print(rec["note"], "\n")
    for c in rec["components"]:
        flag = "*" if c["primary"] else " "
        print(f" {flag} {c['role']:<8} {c['path']:<44} "
              f"{c['kind']:<9} {c['confidence']:.2f}  {c['method']}")
        if c["detail"]:
            print(f"        {c['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
