#!/usr/bin/env python3
r"""
scene.py -- the map's *placed* art: TERRAIN scene objects and COVER sprites.

    py -3 tools/scene.py newbie                    # what is placed, and where
    py -3 tools/scene.py --verify                  # the whole corpus
    py -3 tools/scene.py newbie --connect          # what the scenes do to passability
    py -3 tools/scene.py newbie --png out/x.png    # the map, drawn

WHY THIS MODULE EXISTS
----------------------
`docs/ground_art.md` placed the painted background. It ended with

    "Scene / cover / effect layer placement, which is the next piece of map
     work now that this one is done."

and with a warning that a large minority of every map is off the art. On
`newbie` that shows as two islands with a black gap between them -- and the
gap is exactly where the game draws a chain of stepping stones you can walk
across. Those stones are `TERRAIN` layers, and **they carry their own
passability**, which is why the base cell grid calls the gap blocked.

    base cell grid          1,561 walkable cells, 5 components, largest 1,388
    + scene layers          1,672 walkable cells, 8 components, largest 1,604

1,604 = the village (1,388) + the starting island (130) + the bridge. The two
islands are one component only once the scene layers are applied.

THE THREE RULES, AND WHAT PROVES EACH
-------------------------------------
Written as VERIFIED (proved against the shipped corpus, or corroborated by an
independent implementation) or INFERRED (reproduces the data; ground truth is
inside the Themida-packed exe -- see `docs/CONTEXT.md`).

1. **Footprint** -- VERIFIED.  A scene layer names a `.scene`, a cell origin
   `(lx, ly)`, and each part inside carries a cell offset `(cx, cy)`, a
   `w x h` cell array, and the array runs **backwards** from the anchor:

       A          = (lx + cx, ly + cy)                # the anchor cell
       part(i, j) -> map cell (A.x - i, A.y - j)      # i fast, row-major

   Proof: `--connect` over the corpus. Under this rule `p-arena` and `task07`
   become a **single** walkable component (they are three without it), `task08`
   goes 3 -> 2, `newplain`'s largest component goes 199,247 -> 340,796 of
   341,976, and `newbie`'s two islands join. Under the forward reading
   `(A.x + i, A.y + j)` every one of those maps stays split and `newbie` grows
   three orphan fragments in the water. CoEmu's own floor loader
   (`server/game/src/systems/floor.rs`, an independent codebase) computes the
   same `location + start - x`.

2. **Passability is overwritten, not unioned** -- VERIFIED against CoEmu,
   INFERRED against the retail client.  The scene cell's first `u32` is the
   same "invalid movement" flag the DMap cell carries, and CoEmu assigns it:
   `coordinates[i].access = access`. Corpus-wide the scene layers open 42,295
   cells the base grid blocks and block 2,448 cells the base grid opens, so
   the direction matters and both directions are used.

3. **Sprite placement** -- INFERRED, but tight.  In the painted image's pixel
   space (`tools/puzzle.py`),

       scene sprite top-left = cell_px(A)  +  part.pixel_offset
       cover sprite top-left = cell_px(A)  -  cover.offset

   Note the sign flip: `.scene` files store the offset negated relative to the
   `COVER` layer's. Evidence: the sprite's opaque bounding box lands centred on
   its declared cell footprint. Over 1,504 covers on four maps the horizontal
   error has median +2.5 px and the sprite's bottom edge lands within 4 px of
   the footprint's bottom corner. For the six `newbie` scene sprites the
   anchor-is-the-cell-centre variant has a mean vertical error of +0.2 px where
   anchor-is-the-cell-corner is biased -14 px on all six.

WHAT THE PART HEADER FIELDS ACTUALLY ARE
----------------------------------------
`refs/conquer-online-wiki/Files/Scene.md` labels the eight `u32` as
OriginX, OriginY, FrameInterval, Width, Height, Thickness, OffsetX, OffsetY.
Matching 105 `map/ScenePart/*.Part` text files against the `.scene` binaries
they were compiled into shows the first two labels are wrong:

    field[0..1]   the **pixel** draw offset -- equals the .Part's `OffsetX=` /
                  `OffsetY=` exactly on 76 of the 105 pairs (the other 29 are
                  titles shared by several .Part variants)
    field[2]      frame interval, = the .Part's `AniInterval=`
    field[3..4]   width, height in cells
    field[5]      thickness, = the .Part's `Thick=`
    field[6..7]   the part's **cell** offset inside the scene. No counterpart
                  in the .Part text -- it is assigned when parts are composed
                  into a scene. `bridgeA-1.scene` places bridge01 at (0,0),
                  bridge02 at (-2,-9) and bridge03 at (0,-37): a long bridge
                  built from three reusable pieces.
    then i32      offset elevation. Uninitialised on ~12% of parts (values like
                  1446641773 are ASCII debris), so it is read and reported but
                  never applied.

Read-only. Nothing here writes to the game install.
"""
from __future__ import annotations

import os

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                              # noqa: E402

try:
    import numpy as _np                                    # noqa: N812
except Exception:                                          # noqa: BLE001
    _np = None

#: Layer tags as this build emits them. `core/dmap.py` documents why they do
#: not line up with the wiki's enum names.
LAYER_TERRAIN = 1
LAYER_SCENE = 3
LAYER_COVER = 4

#: WHICH WAY THE CELL WALK RUNS ACROSS ONE DEPTH LINE. **A COIN FLIP, AND
#: LABELLED AS ONE.** Set to "+x" or "-x"; `CO_COVER_TIE` overrides at runtime.
#:
#: Two covers with the same `x + y` sit at the SAME screenY (the affine in
#: `Placed.depth`) and `64 * dx` apart horizontally, so which is drawn second
#: is visible wherever their sprites are wider than that gap. Measured on
#: 7878: **361 such overlapping pairs on `2024thx_new`, 231 on
#: `2024xmas_new`.**
#:
#: There are exactly TWO candidates and they are REVERSES of each other:
#: because `y = sum - x`, ordering equal-sum cells by increasing x IS ordering
#: them by decreasing y, so "increasing x", "decreasing x" and "row-major in
#: y" are one axis and its reverse rather than three options. Equal sum AND
#: equal x is the same cell. **So for every equal-sum pair in different cells
#: the two candidates disagree, and no amount of analysis of OUR data can
#: choose between them** -- the client's walk direction decides, and ~63
#: static probes did not isolate it (class `CTerrainLayer`, vtable
#: `0x15f74e8`, no method-name anchors the way `.pux`'s `AlphaAt` had).
#:
#: **THE FALSIFIER, and it is one observation.** Watch the actual draw order
#: of two overlapping equal-sum covers in a running client. If the one with
#: the LARGER cellX is drawn second, "+x" is right; if the smaller, "-x" is.
#: That is a rig action and belongs to the owner -- `launchgate` is per binary
#: by path, and a PASS there is evidence, never permission. Until then this
#: constant is a guess with a known exposure, not a finding.
#:
#: Chosen by the owner on 2026-09-14 from a side-by-side render of both.
COVER_TIE_BREAK = os.environ.get("CO_COVER_TIE", "+x")


def _tie(ax: int) -> int:
    """The secondary key. See `COVER_TIE_BREAK`."""
    return ax if COVER_TIE_BREAK == "+x" else -ax


def cover_paint_key(p) -> tuple:
    """The paint key for ONE cover: its position in the `.DMap` record list."""
    return (p.layer_index,)


def cover_paint_order(covers) -> list:
    r"""Covers in the order the client paints them -- **`.DMap` list order.**

    THE SINGLE HOME FOR COVER PAINT ORDER. Four call sites used to write
    `sorted(covers, key=lambda q: q.depth())` independently; a rule kept in
    four places is a rule that gets corrected in three.

    **WE INVENTED THE DEPTH SORT AND THE CLIENT HAS NONE.** Measured in
    `7878/Env_DX9/Conquer.exe` by the Director of RE, 2026-09-16:

    * Covers are `C2DMapTerrainObj` (RTTI ``0x01938148``), held by
      `CTerrainObjManager` (``0x01938578``).
    * There IS a comparator over them -- `sub_83E56C`, ``[a+0xC] - [b+0xC]``
      ascending, handed to CRT `qsort`. **It is installed at exactly one site**
      (``0x84368B``, inside the sort helper at ``0x843618``) **and that helper
      has exactly two callers, both protobuf serialisation** (``0x83DF02`` on
      `"pb.Buffer"`, ``0x83EC2F`` on `"invalid varint value at offset %d"`).
      So ``+0x0C`` is a SAVE-ordering key and never runs at draw time.
    * No other cover comparator exists. The render walks the container by
      index through a generic `std::vector` for-each (``0x869171``).

    **So there is nothing for an `x + y` key to be a version of.** Covers are
    painted in the order they sit in the list, and `layer_index` is that
    position -- measured strictly increasing in list order on every map
    checked, so this sort is a no-op reorder that states the rule explicitly
    rather than relying on the parser's insertion order.

    **THE OWNER'S CASE, which started this.** On `2024tsf_new`,
    `data/map/l/c/lc68.dds` and `lc69.dds` should draw over
    `data/map/l/f/lf32.dds` and did not: `lf32` has the larger `x + y` so the
    invented key put it on top, while it is declared at index 2,170 against
    5,880 and 5,886. Pinned by `tests/test_cover_paint_order.py`.

    **THE ONE THING THIS DOES NOT SETTLE, and RE said it plainly rather than
    letting it pass.** Whether the stored order the render walks is *pure
    declaration order* or a *load-time cell-bucketed order* is not readable off
    the disassembly -- the draw pass is templated and inlined past isolation.
    The two differ exactly on equal-`x+y` pairs. **It is provably NOT a depth
    sort, and empirically list order, with the within-tie case still open**;
    RE's item-37 runtime capture (`coverorder.py` + `clientshot.py`, read-only,
    one screenshot, owner client time) is what closes it.
    """
    return sorted(covers, key=cover_paint_key)


#: A part header's `offset elevation` is uninitialised on a minority of parts.
#: Anything outside this is debris rather than data.
ELEVATION_SANE = 4096


def _cstr(b: bytes) -> str:
    i = b.find(b"\x00")
    return (b[:i] if i >= 0 else b).decode("latin-1")


def norm(p: str) -> str:
    """A path as written in a binary field -> a logical asset path."""
    return p.replace("\\", "/").lstrip("/").lower()


# ---------------------------------------------------------------------------
# map/Scene/*.scene
# ---------------------------------------------------------------------------

@dataclass
class ScenePart:
    """One reusable piece of a scenery object, with its own passability.

    VERIFIED on all 299 shipped `.scene` files: every one is consumed to
    exactly its length by this layout.
    """

    ani: str                       # "ani\\MapScene.ani"
    title: str                     # "stand06.tga" -- the key inside that .ani
    pixel_offset: tuple[int, int]  # field[0..1]; == the .Part's OffsetX/OffsetY
    frame_interval: int            # field[2], milliseconds
    width: int                     # field[3], cells
    height: int                    # field[4], cells
    thickness: int                 # field[5]
    cell_offset: tuple[int, int]   # field[6..7], cells, inside the scene
    offset_elevation: int          # often uninitialised -- see the docstring
    #: `width * height` of (blocked, surface, elevation), row-major, x fastest.
    cells: list[tuple[int, int, int]] = field(default_factory=list, repr=False)

    @property
    def elevation_usable(self) -> bool:
        return abs(self.offset_elevation) < ELEVATION_SANE

    def cell(self, i: int, j: int) -> tuple[int, int, int]:
        return self.cells[j * self.width + i]


@dataclass
class SceneFile:
    path: str
    parts: list[ScenePart] = field(default_factory=list)
    bytes_unconsumed: int = 0


def parse_scene(data: bytes, path: str = "") -> SceneFile:
    (n,) = struct.unpack_from("<I", data, 0)
    off = 4
    out = SceneFile(path)
    for _ in range(n):
        ani = _cstr(data[off:off + 256]); off += 256
        title = _cstr(data[off:off + 64]); off += 64
        v = struct.unpack_from("<8i", data, off); off += 32
        (el,) = struct.unpack_from("<i", data, off); off += 4
        w, h = v[3], v[4]
        if w < 0 or h < 0 or off + w * h * 12 > len(data):
            raise ValueError(f"{path}: part {w}x{h} overruns the file")
        cells = [struct.unpack_from("<IIi", data, off + 12 * k) for k in range(w * h)]
        off += w * h * 12
        out.parts.append(ScenePart(ani, title, (v[0], v[1]), v[2], w, h, v[5],
                                   (v[6], v[7]), el, cells))
    out.bytes_unconsumed = len(data) - off
    return out


class SceneLibrary:
    """`map/Scene/*.scene`, parsed once and cached. Misses are cached too."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.reason = ""
        self._root: Optional[Path] = Path(root) if root else None
        if self._root is None:
            try:
                found = coroot.find()
                self._root = Path(found.path) if found else None
            except Exception as e:                          # noqa: BLE001
                self.reason = f"could not resolve the game install: {e}"
        if self._root is None:
            self.reason = self.reason or "no game install found (core/coroot.py)"
        self._cache: dict[str, Optional[SceneFile]] = {}

    @property
    def root(self) -> Optional[Path]:
        return self._root

    def get(self, logical: str) -> Optional[SceneFile]:
        key = norm(logical)
        if key in self._cache:
            return self._cache[key]
        out = None
        if self._root is not None:
            p = self._root / key
            if p.is_file():
                try:
                    out = parse_scene(p.read_bytes(), key)
                except Exception:                           # noqa: BLE001
                    out = None
        self._cache[key] = out
        return out


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------

@dataclass
class Placed:
    """One sprite put somewhere on a map.

    `anchor` is the cell whose *centre* the pixel offset is measured from, and
    -- for a scene part -- the cell the part's `(0, 0)` entry lands on. The
    footprint runs up and left from it.
    """

    kind: str                       # "scene" | "cover"
    ani: str                        # the .ani file
    title: str                      # the key inside it
    anchor: tuple[int, int]
    width: int
    height: int
    pixel_offset: tuple[int, int]   # already sign-normalised: top-left = centre + this
    frame_interval: int
    layer_index: int = 0
    thickness: int = 0
    scene_path: str = ""
    offset_elevation: int = 0
    cells: list[tuple[int, int, int]] = field(default_factory=list, repr=False)
    #: The DMap layer tag this record actually carried. v1006 renumbered
    #: COVER from 4 to 24, so the tag is NOT derivable from `kind` -- it has
    #: to travel with the record. 0 means it was not recorded.
    layer_tag: int = 0

    # -- geometry ----------------------------------------------------------

    @property
    def cell_bounds(self) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) inclusive cell range the footprint covers."""
        ax, ay = self.anchor
        return (ax - self.width + 1, ay - self.height + 1, ax, ay)

    def map_cells(self) -> Iterable[tuple[int, int, int, int, int]]:
        """(map_x, map_y, blocked, surface, elevation) for a scene part."""
        ax, ay = self.anchor
        w = self.width
        for k, (m, s, e) in enumerate(self.cells):
            yield ax - k % w, ay - k // w, m, s, e

    def sprite_origin(self, pm) -> tuple[float, float]:
        """Top-left of the sprite in the painted image's pixel space."""
        cx, cy = pm.cell_px(*self.anchor)
        return cx + self.pixel_offset[0], cy + self.pixel_offset[1]

    def depth(self) -> tuple:
        """Isometric key `(x + y, tie, declaration)`. **NOT the cover key.**

        **RETRACTED FOR COVERS, 2026-09-16: THE CLIENT DOES NOT DEPTH-SORT
        THEM, SO THIS KEY WAS AN INVENTION OF OURS.**  See
        `cover_paint_order()` below, which is what paints a cover layer now.
        Every word of the affine paragraph that follows is still true and it
        never implied a sort -- a projection that is monotonic in `x + y`
        says where a sprite LANDS, not which sprite is painted second.
        Inferring the second from the first is the error.

        Still the key for SCENE and INTERACTIVE placements, because RE's
        measurement named the cover container and nothing else. That is a
        scope limit, not a finding: see `cover_paint_order()`.

        The terrain-layer cell->screen projection at
        `Clients/7878/Env_DX9/Conquer.exe` RVA `0x86A8E2` computes::

            screenX = 32*(cellX - cellY) + originX
            screenY = 16*(cellX + cellY) + originY

        the exact 2:1 dimetric affine, so `screenY` is MONOTONIC in `x + y`.
        This used to say *"in a 2:1 isometric view screen-y grows with x+y"* as
        an inference; it is now evidence. `docs/comod_backlog.md` item 37.

        **THE TIE IS A LABELLED COIN FLIP.** `COVER_TIE_BREAK` below. It no
        longer reaches a cover.
        """
        ax, ay = self.anchor
        return (ax + ay, _tie(ax), self.layer_index)

    def to_json(self) -> dict:
        return {
            "kind": self.kind, "ani": norm(self.ani), "title": self.title,
            "anchor": list(self.anchor), "size": [self.width, self.height],
            "pixelOffset": list(self.pixel_offset),
            "frameInterval": self.frame_interval,
            "scene": norm(self.scene_path), "thickness": self.thickness,
            "layer": self.layer_index,
        }


@dataclass
class Scenery:
    """Everything a map places on top of its painted ground."""

    name: str = ""
    scenes: list[Placed] = field(default_factory=list)
    covers: list[Placed] = field(default_factory=list)
    #: v1006's SECOND record list -- real cover sprites that were never drawn.
    #:
    #: `core/dmap.py` has parsed this for months (`DMap.late_layers`) and every
    #: consumer called `gather(d.layers, ...)`, so the second list went in
    #: nobody's picture. The owner found it by eye: `gsjx03_new` shows walkable
    #: platforms on its right-hand side with no art under them.
    #:
    #: **IT IS THE `.OtherData` `InteractiveLayer`, PROVEN BY EXACT COUNT.**
    #: `InteractiveLayerPicSize0.MapObjAmount` equals the number of tag-4
    #: records in this list on **152 of 152 v1006 maps**. The v1004 maps have
    #: no late list at all, and their 13 "matches" are 0 == 0 -- excluded as
    #: vacuous rather than counted, which is the difference between 152/152 and
    #: a much worse-looking 165/321 over a population that cannot answer.
    #:
    #: Kept SEPARATE from `covers` rather than appended, for three reasons:
    #: `layer_tag()` documents that no map draws one shape from more than one
    #: tag (main covers are 24 here, these are 4) and merging would quietly
    #: falsify it; `mapedit`'s `.OtherData` TERRAIN tint binds by position in
    #: `covers` and appending would not move those but would invite someone to
    #: reorder; and the owner gets a layer they can toggle, which is how this
    #: was found in the first place.
    late_covers: list[Placed] = field(default_factory=list)
    missing_scenes: list[str] = field(default_factory=list)

    def sorted_late_covers(self) -> list[Placed]:
        # NOT `cover_paint_order`. RE measured the COVER container; the
        # interactive list is a different one and nobody has read its draw
        # site. Keeping the isometric key here is not a claim that it is
        # right -- it is a refusal to extend one measurement to two objects.
        return sorted(self.late_covers, key=lambda p: p.depth())

    def sorted_scenes(self) -> list[Placed]:
        return sorted(self.scenes, key=lambda p: p.depth())      # as above

    def sorted_covers(self) -> list[Placed]:
        return cover_paint_order(self.covers)

    def layer_tag(self, layer: str) -> Optional[int]:
        """The DMap tag the records in `layer` were actually read from.

        v1006 renumbered COVER from 4 to 24. Selection dispatches on `shape`,
        so this tag never picks anything -- but anything RECORDING the tag
        has to record the one in the file, not the one the format used when
        the recorder was written.

        Measured over 2,027 maps across all 12 install trees on this box:
        NO map draws one shape from more than one tag, so the first record
        with a tag speaks for the layer. *6609 ships both cover tags (4 on
        92,073 records, 24 on 375) but never both in the same map*, which is
        why the census has to be per map and not per install. Returns None
        when the layer holds no records and there is nothing to observe.
        """
        parts = self.covers if layer == "cover" else self.scenes
        for p in parts:
            if p.layer_tag:
                return p.layer_tag
        return None

    def to_json(self, *, with_items: bool = True) -> dict:
        d = {"name": self.name, "sceneParts": len(self.scenes),
             "covers": len(self.covers), "missingScenes": self.missing_scenes}
        if with_items:
            d["scenes"] = [p.to_json() for p in self.sorted_scenes()]
            d["covers"] = [p.to_json() for p in self.sorted_covers()]
        return d


def gather(layers: Iterable[dict], lib: SceneLibrary,
           late: Iterable[dict] = ()) -> Scenery:
    """DMap layer records (`core/dmap.py`) -> placed sprites.

    Accepts the `shape`-tagged dicts `dmap.parse()` produces, so the layer
    table is decoded in exactly one place.

    `late` is v1006's second record list (`DMap.late_layers`). Its records are
    the SAME SHAPE as the main table's -- `shape`, `path`, `key`, `origin`,
    `offset`, `frame_interval` -- so they are gathered by the same code and
    land in `Scenery.late_covers`. See that field for why they are separate
    and for the 152/152 binding to `.OtherData`'s `InteractiveLayer`.
    """
    out = Scenery()
    for L in list(layers) + [dict(r, _late=True) for r in late]:
        shape = L.get("shape")
        idx = int(L.get("index", 0))
        if shape == "scene":
            sf = lib.get(L.get("path", ""))
            if sf is None:
                p = norm(L.get("path", ""))
                if p and p not in out.missing_scenes:
                    out.missing_scenes.append(p)
                continue
            lx, ly = L.get("origin", (0, 0))
            for part in sf.parts:
                out.scenes.append(Placed(
                    kind="scene", ani=part.ani, title=part.title,
                    anchor=(lx + part.cell_offset[0], ly + part.cell_offset[1]),
                    width=part.width, height=part.height,
                    # `.scene` stores the offset already negative; a COVER
                    # stores the same displacement positive. Both end up as
                    # "add this to the anchor cell's centre".
                    pixel_offset=part.pixel_offset,
                    frame_interval=part.frame_interval,
                    layer_index=idx, thickness=part.thickness,
                    scene_path=L.get("path", ""),
                    offset_elevation=part.offset_elevation,
                    cells=part.cells,
                    layer_tag=int(L.get("type", 0) or 0)))
        elif shape == "cover":
            ox, oy = L.get("origin", (0, 0))
            w, h = L.get("size", (1, 1))
            dx, dy = L.get("offset", (0, 0))
            dest = out.late_covers if L.get("_late") else out.covers
            dest.append(Placed(
                kind="cover", ani=L.get("path", ""), title=L.get("key", ""),
                anchor=(ox, oy), width=max(1, w), height=max(1, h),
                pixel_offset=(-dx, -dy),
                frame_interval=int(L.get("frame_interval", 0) or 0),
                layer_index=idx,
                layer_tag=int(L.get("type", 0) or 0)))
    return out


def for_map(name_or_dmap, *, root: Optional[Path] = None,
            lib: Optional[SceneLibrary] = None) -> Scenery:
    """Scenery for a map name, a `.DMap` path, or an already-parsed `DMap`."""
    lib = lib or SceneLibrary(root)
    d = name_or_dmap
    if isinstance(d, (str, Path)):
        import dmap as dmapmod                              # noqa: PLC0415
        p = Path(d)
        if p.is_file():
            d = dmapmod.parse(p, want_cells=False, verify=False)
        else:
            if lib.root is None:
                return Scenery(str(d))
            # By name: the registry decides which file, not the extension we
            # happen to guess.
            parsed, _why = dmapmod.parse_map(lib.root, Path(str(d)).stem,
                                             want_cells=False, verify=False)
            if parsed is None:
                return Scenery(str(d))
            d = parsed
    sc = gather(d.layers, lib, getattr(d, "late_layers", ()))
    sc.name = Path(str(getattr(d, "path", ""))).stem or str(name_or_dmap)
    return sc


# ---------------------------------------------------------------------------
# passability
# ---------------------------------------------------------------------------

@dataclass
class OverlayStats:
    applied: int = 0
    opened: int = 0            # blocked in the base grid, walkable after
    blocked: int = 0           # walkable in the base grid, blocked after
    off_map: int = 0

    def to_json(self) -> dict:
        return {"applied": self.applied, "opened": self.opened,
                "blocked": self.blocked, "offMap": self.off_map}


def apply_passability(walk: bytes | bytearray, width: int, height: int,
                      scenes: Iterable[Placed]) -> tuple[bytes, OverlayStats]:
    """Composite the scene layers' own passability onto a cell grid.

    `walk` is row-major, 1 = walkable. Returns a new grid and what changed.
    The scene cell **replaces** the base cell -- see rule 2 in the module
    docstring; a union would ignore the 2,448 cells the scenery walls off.
    """
    out = bytearray(walk)
    st = OverlayStats()
    for p in scenes:
        for x, y, blocked, _surface, _elev in p.map_cells():
            if not (0 <= x < width and 0 <= y < height):
                st.off_map += 1
                continue
            i = y * width + x
            new = 0 if blocked else 1
            if out[i] != new:
                if new:
                    st.opened += 1
                else:
                    st.blocked += 1
            out[i] = new
            st.applied += 1
    return bytes(out), st


def components(walk: bytes, width: int, height: int) -> list[int]:
    """Sizes of the 8-connected walkable components, largest first. The test
    that told forward placement from backward."""
    seen = bytearray(width * height)
    sizes = []
    for s in range(width * height):
        if seen[s] or not walk[s]:
            continue
        stack = [s]
        seen[s] = 1
        n = 0
        while stack:
            i = stack.pop()
            n += 1
            x, y = i % width, i // width
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    j = ny * width + nx
                    if walk[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
        sizes.append(n)
    sizes.sort(reverse=True)
    return sizes


# ---------------------------------------------------------------------------
# art
# ---------------------------------------------------------------------------


def parse_ani_ini(path) -> dict:
    """A classic `.ani` -> the same {key: [frame, ...]} shape as the JSON.

    OFFICIAL clients ship `ani/MapScene.ani` in TQ's ini form; only the
    community build ships `ani/MapScene.json`. Reading just the JSON left the
    parser returning an empty dict on every official root, so every sprite
    lookup missed and `frames()` came back [] -- including all of the p-arena
    bridge pieces the .msk fallback exists for.

    This is the same split as monster.json/Monster.dat and
    GameMap.json/GameMap.dat: the community form is the exception, not the rule.

        [bridge06.tga]
        FrameAmount=1
        Frame0=data/map/mapobj/plain/plain/bridge06.msk

    Frames are ordered by their INDEX, not by the order they appear, because
    nothing guarantees the file lists Frame0 before Frame1. `FrameAmount` is
    deliberately ignored -- what is actually present wins over what the file
    claims, so a truncated section yields the frames it really has.
    """
    try:
        text = Path(path).read_text("latin-1", errors="replace")
    except OSError:
        return {}
    out: dict[str, list[str]] = {}
    cur: Optional[list[tuple[int, str]]] = None
    frames: dict[str, list[tuple[int, str]]] = {}
    key = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("//", ";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            key = line[1:-1].strip()
            cur = frames.setdefault(key, [])
            continue
        if cur is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k.lower().startswith("frame") or k.lower() == "frameamount":
            continue
        try:
            idx = int(k[5:])
        except ValueError:
            continue
        v = v.strip()
        if v:
            cur.append((idx, v))
    for k, pairs in frames.items():
        if pairs:
            out[k] = [v for _, v in sorted(pairs)]
    return out


class SpriteCache:
    """`.ani` key -> decoded RGBA frames, cached. Reads through an AssetRoot so
    the art is found whether it is loose or inside data.wdf."""

    def __init__(self, root: Optional[Path] = None, assets=None) -> None:
        self.reason = ""
        self._root = Path(root) if root else None
        if self._root is None:
            try:
                found = coroot.find()
                self._root = Path(found.path) if found else None
            except Exception:                               # noqa: BLE001
                self._root = None
        self._assets = assets
        self._ani: dict[str, dict] = {}
        self._frames: dict[tuple[str, str], list] = {}

    @property
    def assets(self):
        if self._assets is None and self._root is not None:
            try:
                from coassets import AssetRoot              # noqa: PLC0415
                self._assets = AssetRoot(self._root)
            except Exception as e:                          # noqa: BLE001
                self.reason = f"assets unavailable: {e}"
        return self._assets

    def ani(self, ani_path: str) -> dict:
        """An `.ani` definition. This build ships them as JSON under `ani/`."""
        stem = Path(norm(ani_path)).stem
        if stem in self._ani:
            return self._ani[stem]
        out: dict = {}
        if self._root is not None:
            p = self._root / "ani" / f"{stem}.json"
            q = self._root / "ani" / f"{stem}.ani"
            if p.is_file():
                try:
                    out = json.loads(p.read_text("utf-8", errors="replace"))
                except Exception:                           # noqa: BLE001
                    out = {}
            elif q.is_file():
                out = parse_ani_ini(q)
        self._ani[stem] = out
        return out

    def frame_paths(self, ani_path: str, key: str) -> list[str]:
        v = self.ani(ani_path).get(key)
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [norm(x) for x in v if isinstance(x, str)]

    @staticmethod
    def frame_candidates(rel: str) -> list[str]:
        """The asset paths to try for one `.ani` frame, best first.

        THE `.msk` SUBSTITUTION RULE, in one place. 14 of the 2,621
        `MapScene.ani` frames name a `.msk` rather than a `.dds` (see
        `frames()` for why the sibling `.dds` is the right sprite and not an
        approximation). Both `frames()`, which decodes, and `frame_blob()`,
        which deliberately does not, resolve through here.

        Kept as a separate rule rather than inlined for the reason
        `frame_index()` records: a second consumer that copies the arithmetic
        is how two callers end up disagreeing about the same asset.
        """
        return [rel] if not rel.endswith(".msk") else [rel[:-4] + ".dds", rel]

    def frame_blob(self, ani_path: str, key: str,
                   index: int = 0) -> Optional[tuple[str, bytes]]:
        """One frame's asset path and its bytes **as shipped** -- no decode.

        Returns `(logical_path, raw_bytes)`, or None if nothing resolves.

        This exists for consumers that want the texture GPU-ready. The shipped
        art is DXT and the engine uploads it compressed; `frames()` hands back
        decoded RGBA, which is right for compositing a PNG and wrong for a
        renderer that would only have to re-compress it -- throwing away the
        4-8x memory and bandwidth advantage the format is for.
        """
        ar = self.assets
        if ar is None:
            return None
        paths = self.frame_paths(ani_path, key)
        if not paths:
            return None
        rel = paths[index % len(paths)]
        for cand in self.frame_candidates(rel):
            try:
                return cand, ar.read(cand)
            except Exception:                               # noqa: BLE001
                continue
        return None

    def frames(self, ani_path: str, key: str) -> list:
        """[(w, h, rgba), ...] for every frame of one sprite.

        14 of the 2,621 `MapScene.ani` frames name a **`.msk`** rather than a
        `.dds`, and among them are every piece of the two bridges on `p-arena`.
        A `.msk` is a 1-bit-per-pixel mask -- `bridge05.msk` is exactly
        512*512/8 = 32,768 bytes -- and the colour lives in a `.dds` of the same
        stem. Unpacking the mask and comparing it with that `.dds`'s own alpha
        channel gives **97.0 %** agreement, so the `.dds` alone is already the
        right sprite and the `.msk` is a companion (a cheap hit mask, most
        likely). Falling back to the sibling is therefore a substitution, not an
        approximation. INFERRED; the 3 % are the DXT3 alpha's soft edge.
        """
        ck = (norm(ani_path), key)
        if ck in self._frames:
            return self._frames[ck]
        out = []
        ar = self.assets
        if ar is not None:
            import dds                                      # noqa: PLC0415
            for rel in self.frame_paths(ani_path, key):
                for cand in self.frame_candidates(rel):
                    try:
                        out.append(dds.decode(ar.read(cand)))
                        break
                    except Exception:                       # noqa: BLE001
                        continue
        self._frames[ck] = out
        return out

    def frame(self, placed: Placed, index: int = 0):
        fr = self.frames(placed.ani, placed.title)
        if not fr:
            return None
        return fr[index % len(fr)]


def blit(dst: bytearray, dw: int, dh: int, src, sw: int, sh: int,
         x: int, y: int) -> int:
    """Alpha-blend one RGBA sprite onto an RGB buffer. Returns pixels touched.

    Pure stdlib when numpy is absent; the numpy path is ~50x faster and is what
    makes drawing a thousand covers on `newplain` tolerable.
    """
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return 0
    if _np is not None:
        s = _np.frombuffer(src, dtype=_np.uint8).reshape(sh, sw, 4)
        s = s[y0 - y:y1 - y, x0 - x:x1 - x]
        a = s[:, :, 3:4].astype(_np.uint16)
        if not a.any():
            return 0
        d = _np.frombuffer(bytes(dst), dtype=_np.uint8).reshape(dh, dw, 3).copy()
        win = d[y0:y1, x0:x1].astype(_np.uint16)
        out = (s[:, :, :3].astype(_np.uint16) * a + win * (255 - a) + 127) // 255
        d[y0:y1, x0:x1] = out.astype(_np.uint8)
        dst[:] = d.tobytes()
        return (x1 - x0) * (y1 - y0)
    n = 0
    for yy in range(y0, y1):
        srow = ((yy - y) * sw) * 4
        drow = (yy * dw) * 3
        for xx in range(x0, x1):
            si = srow + (xx - x) * 4
            al = src[si + 3]
            if not al:
                continue
            di = drow + xx * 3
            if al == 255:
                dst[di:di + 3] = src[si:si + 3]
            else:
                for c in range(3):
                    dst[di + c] = (src[si + c] * al + dst[di + c] * (255 - al)) // 255
            n += 1
    return n


def composite(pm, rect: tuple[int, int, int, int], rgb: bytes, placed_items,
              cache: SpriteCache, *, scale: int = 1, frame: int = 0,
              time_ms: int = 0) -> tuple[bytes, int]:
    """Draw sprites onto an already-rendered crop of the painted ground.

    `rect` is the crop's pixel rectangle in the painted image, `rgb` the crop
    itself at `scale` decimation -- i.e. exactly what `PuzzleMap.render()`
    returns.

    **TWO PASSES, NOT ONE SORT.** A cover is defined by being drawn over
    everything, and since 2026-09-16 covers carry a different order from
    scenes -- `scene.cover_paint_order` has the measurement. A mixed list can
    no longer be ordered by one key, and it never should have been: the client
    draws the two containers in two passes. Scenes first on the isometric key,
    then covers in `.DMap` list order.
    """
    x0, y0, x1, y1 = rect
    ow = max(1, (x1 - x0 + scale - 1) // scale)
    oh = max(1, (y1 - y0 + scale - 1) // scale)
    buf = bytearray(rgb)
    drawn = 0
    _items = list(placed_items)
    _order = (sorted((q for q in _items if q.kind != "cover"),
                     key=lambda q: q.depth())
              + cover_paint_order([q for q in _items if q.kind == "cover"]))
    for p in _order:
        fr = cache.frames(p.ani, p.title)
        if not fr:
            continue
        idx = frame
        if p.frame_interval > 0 and len(fr) > 1 and time_ms:
            idx = time_ms // p.frame_interval
        sw, sh, rgba = fr[idx % len(fr)]
        sx, sy = p.sprite_origin(pm)
        if scale != 1:
            rgba, sw, sh = _decimate(rgba, sw, sh, scale)
        if blit(buf, ow, oh, rgba, sw, sh,
                int(round((sx - x0) / scale)), int(round((sy - y0) / scale))):
            drawn += 1
    return bytes(buf), drawn


def _decimate(rgba: bytes, w: int, h: int, scale: int):
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8).reshape(h, w, 4)[::scale, ::scale]
        return a.tobytes(), a.shape[1], a.shape[0]
    nw, nh = max(1, w // scale), max(1, h // scale)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            s = ((y * scale) * w + x * scale) * 4
            d = (y * nw + x) * 4
            out[d:d + 4] = rgba[s:s + 4]
    return bytes(out), nw, nh


def frame_index(p, frame_count: int, time_ms: int) -> int:
    """Which frame of placement `p` is on screen at `time_ms`.

    Extracted so that **the renderer and anyone caching the render agree by
    construction**.  A cover layer is not a function of `time_ms`; it is a
    function of this index, which is cyclic and small.  Caching on the raw
    clock instead is a cache that can never hit -- `tools/coplay.py`'s cover
    cache did exactly that, keyed on a free-running `Date.now() - start`
    against the single most expensive endpoint in the viewer.

    Splitting the rule out rather than copying it is the standing lesson from
    `read_ani`: that parser was moved into COre precisely to stop this shape,
    and it did not help, because only the parser moved and the choice of which
    file to open stayed in the callers.  So callers take the *rule*, not a
    copy of the arithmetic.
    """
    if p.frame_interval > 0 and frame_count > 1 and time_ms:
        return (time_ms // p.frame_interval) % frame_count
    return 0


def cover_frame_signature(covers, cache: SpriteCache, time_ms: int,
                          *, key=None) -> tuple:
    """Everything `cover_layer`'s output takes from `time_ms`, and no more.

    A correct cache key for a rendered cover layer.  Placements do NOT share
    one clock -- each carries its own `frame_interval` and its own frame
    count -- so quantising the millisecond value by any single interval would
    be wrong for every placement that disagrees with it.
    """
    return tuple(frame_index(p, len(cache.frames(p.ani, p.title) or ()), time_ms)
                 for p in (key(covers) if key else cover_paint_order(covers)))


def cover_layer(pm, rect: tuple[int, int, int, int], covers, cache: SpriteCache,
                *, scale: int = 1, time_ms: int = 0,
                key=None) -> tuple[int, int, bytes]:
    """The COVER sprites alone, as RGBA -- the layer that draws over the player.

    Kept separate from `composite()` on purpose: a cover is defined by being in
    front of everything, so it cannot be baked into the ground.

    **`key` EXISTS BECAUSE THIS FUNCTION IS ALSO CALLED WITH SCENES**
    (`coplay.py`), and scenes are NOT what RE measured. Default is
    `cover_paint_order`; a caller drawing something other than covers passes
    its own ordering and says so at the call site.
    """
    x0, y0, x1, y1 = rect
    ow = max(1, (x1 - x0 + scale - 1) // scale)
    oh = max(1, (y1 - y0 + scale - 1) // scale)
    buf = bytearray(ow * oh * 4)
    for p in (key(covers) if key else cover_paint_order(covers)):
        fr = cache.frames(p.ani, p.title)
        if not fr:
            continue
        sw, sh, rgba = fr[frame_index(p, len(fr), time_ms)]
        sx, sy = p.sprite_origin(pm)
        if scale != 1:
            rgba, sw, sh = _decimate(rgba, sw, sh, scale)
        _blit_rgba(buf, ow, oh, rgba, sw, sh,
                   int(round((sx - x0) / scale)), int(round((sy - y0) / scale)))
    return ow, oh, bytes(buf)


def _blit_rgba(dst: bytearray, dw: int, dh: int, src, sw: int, sh: int,
               x: int, y: int) -> None:
    r"""Source-over onto an RGBA buffer, in STRAIGHT alpha throughout.

    Both sides are straight (un-premultiplied) alpha and so is the result:
    `dds.decode` emits straight alpha, `encode_png_rgba` writes a PNG, and
    every consumer -- `drawImage`, a WebGL upload with
    `UNPACK_PREMULTIPLY_ALPHA_WEBGL` false, `tilebake.js`'s
    `SRC_ALPHA / ONE_MINUS_SRC_ALPHA` -- reads it as straight alpha.

    ⚠ **This used to composite with the PREMULTIPLIED formula** and it is worth
    knowing why that survived, because the shape recurs. The old line was::

        out.rgb = (src.rgb * a + dst.rgb * (255 - a)) // 255

    which is exactly right when the destination is OPAQUE -- and `blit()`
    above, whose destination is an opaque RGB ground, still uses it and is
    still correct. Over a TRANSPARENT destination the same line emits
    ``src.rgb * a / 255``: a straight-alpha pixel whose colour has been
    multiplied by its own alpha, i.e. darker than the asset by exactly its
    alpha. Opaque texels (a=255) and invisible ones (a=0) are unaffected, so
    the symptom is only soft edges being too dark -- and every sprite in this
    corpus is opaque in the middle. `CORRECTIONS.md`
    C-2026-08-09-comod-entity-followups has the arithmetic and the blast
    radius.

    The correct composite in straight alpha needs the destination's own alpha
    in the weights AND a division by the result's alpha, and it is that second
    term the premultiplied form gets to skip::

        out.a   = sa + da*(1 - sa)
        out.rgb = (src.rgb*sa + dst.rgb*da*(1 - sa)) / out.a

    `out.a` was already right; only the colour was wrong.
    """
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return
    if _np is not None:
        s = _np.frombuffer(src, dtype=_np.uint8).reshape(sh, sw, 4)
        s = s[y0 - y:y1 - y, x0 - x:x1 - x].astype(_np.uint16)
        d = _np.frombuffer(bytes(dst), dtype=_np.uint8).reshape(dh, dw, 4).copy()
        win = d[y0:y1, x0:x1].astype(_np.uint16)
        sa = s[:, :, 3:4]
        # The destination's surviving share, alpha included. Rounded the same
        # way the scalar branch rounds so the two agree bit for bit.
        keep = (win[:, :, 3:4] * (255 - sa) + 127) // 255
        oa = sa + keep
        den = _np.maximum(oa, 1)
        out = _np.empty_like(s)
        out[:, :, :3] = (s[:, :, :3] * sa + win[:, :, :3] * keep + den // 2) // den
        out[:, :, 3:4] = oa
        # A fully transparent result has no colour of its own. Keeping the
        # destination's is not cosmetic: a later blit reads it back.
        clear = (oa == 0)[:, :, 0]
        out[clear] = win[clear]
        d[y0:y1, x0:x1] = out.astype(_np.uint8)
        dst[:] = d.tobytes()
        return
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            si = ((yy - y) * sw + (xx - x)) * 4
            al = src[si + 3]
            if not al:
                continue
            di = (yy * dw + xx) * 4
            da = dst[di + 3]
            if al == 255 or not da:
                # Opaque source, or nothing underneath: the source colour IS
                # the answer. This is the case the premultiplied formula got
                # wrong -- over transparency it emitted `rgb * a / 255`.
                dst[di:di + 3] = src[si:si + 3]
                dst[di + 3] = al + (da * (255 - al) + 127) // 255
                continue
            keep = (da * (255 - al) + 127) // 255
            oa = al + keep
            for c in range(3):
                dst[di + c] = (src[si + c] * al + dst[di + c] * keep
                               + oa // 2) // oa
            dst[di + 3] = oa


def encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    import zlib                                             # noqa: PLC0415
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# corpus report
# ---------------------------------------------------------------------------

def survey(root: Optional[Path] = None) -> dict:
    """Every map's scene/cover usage and what the scenes do to passability."""
    import dmap as dmapmod                                  # noqa: PLC0415
    lib = SceneLibrary(root)
    if lib.root is None:
        return {"error": lib.reason, "maps": []}
    rows = []
    tot = {"maps": 0, "withScene": 0, "withCover": 0, "sceneParts": 0,
           "covers": 0, "opened": 0, "blocked": 0, "offMap": 0,
           "joinedMaps": 0}
    # The corpus is `dmap.map_names`, not a `*.DMap` glob, and each map is read
    # from the file its registry row names.  A glob surveys the LOOSE files,
    # which on 6090 and 6609 are byte-identical to each other -- so the survey
    # returned the same passability totals for two different clients and could
    # not have told them apart.  See `docs/map_twin_precedence.md`.
    for name in dmapmod.map_names(lib.root):
        d, why = dmapmod.parse_map(lib.root, name, want_cells=True,
                                   verify=False)
        if d is None:
            rows.append({"map": name, "error": why[:80]})
            continue
        p = Path(name)
        sc = gather(d.layers, lib, getattr(d, "late_layers", ()))
        tot["maps"] += 1
        tot["sceneParts"] += len(sc.scenes)
        tot["covers"] += len(sc.covers)
        if sc.scenes:
            tot["withScene"] += 1
        if sc.covers:
            tot["withCover"] += 1
        row = {"map": p.stem, "width": d.width, "height": d.height,
               "sceneParts": len(sc.scenes), "covers": len(sc.covers),
               "missingScenes": sc.missing_scenes}
        if sc.scenes:
            base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
            over, st = apply_passability(base, d.width, d.height, sc.scenes)
            cb = components(base, d.width, d.height)
            co = components(over, d.width, d.height)
            row |= {"baseWalkable": sum(base), "walkable": sum(over),
                    "baseComponents": len(cb), "components": len(co),
                    "baseLargest": cb[0] if cb else 0,
                    "largest": co[0] if co else 0, **st.to_json()}
            tot["opened"] += st.opened
            tot["blocked"] += st.blocked
            tot["offMap"] += st.off_map
            if row["largest"] > row["baseLargest"]:
                tot["joinedMaps"] += 1
        rows.append(row)
    return {"totals": tot, "maps": rows}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_verify(a) -> int:
    res = survey(a.root)
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    t = res["totals"]
    print(f"{t['maps']} maps: {t['withScene']} place TERRAIN scene objects, "
          f"{t['withCover']} place COVER sprites")
    print(f"{t['sceneParts']:,} scene parts, {t['covers']:,} covers")
    print(f"scene passability: {t['opened']:,} cells opened, "
          f"{t['blocked']:,} cells blocked, {t['offMap']:,} off the map")
    print(f"{t['joinedMaps']} maps gain a larger walkable component once the "
          f"scene layers are applied\n")
    print(f"  {'map':<18}{'scene':>7}{'cover':>7}{'walk':>10}{'+/-':>8}"
          f"{'components':>13}{'largest':>10}{'was':>10}")
    for r in sorted(res["maps"], key=lambda r: -r.get("sceneParts", 0)):
        if not r.get("sceneParts") and not r.get("covers"):
            continue
        if "largest" in r:
            delta = r["walkable"] - r["baseWalkable"]
            comps = f"{r['baseComponents']} -> {r['components']}"
            print(f"  {r['map']:<18}{r['sceneParts']:>7}{r['covers']:>7}"
                  f"{r['walkable']:>10,}{delta:>+8,}{comps:>13}"
                  f"{r['largest']:>10,}{r['baseLargest']:>10,}")
        else:
            print(f"  {r['map']:<18}{r['sceneParts']:>7}{r['covers']:>7}"
                  f"{'-':>10}{'-':>8}{'-':>13}{'-':>10}{'-':>10}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 0


def _cmd_map(a) -> int:
    import dmap as dmapmod                                  # noqa: PLC0415
    lib = SceneLibrary(a.root)
    if lib.root is None:
        print(lib.reason, file=sys.stderr)
        return 1
    d, why = dmapmod.parse_map(lib.root, a.map, want_cells=True, verify=False)
    if d is None:
        print(why, file=sys.stderr)
        return 1
    sc = gather(d.layers, lib, getattr(d, "late_layers", ()))
    sc.name = a.map
    print(f"{a.map}  {d.width}x{d.height}  {len(d.layers)} layers")
    print(f"  scene parts {len(sc.scenes)}   covers {len(sc.covers)}"
          + (f"   MISSING {sc.missing_scenes}" if sc.missing_scenes else ""))
    base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
    over, st = apply_passability(base, d.width, d.height, sc.scenes)
    cb = components(base, d.width, d.height)
    co = components(over, d.width, d.height)
    print(f"  base grid   {sum(base):,} walkable, {len(cb)} components, "
          f"largest {cb[0] if cb else 0:,}")
    print(f"  with scenes {sum(over):,} walkable, {len(co)} components, "
          f"largest {co[0] if co else 0:,}")
    print(f"  overlay     {st.applied:,} cells written, {st.opened:,} opened, "
          f"{st.blocked:,} blocked, {st.off_map:,} off-map")
    if a.list:
        for q in sc.sorted_scenes():
            x0, y0, x1, y1 = q.cell_bounds
            print(f"    scene {q.title:<18} anchor {q.anchor} "
                  f"cells [{x0}..{x1}]x[{y0}..{y1}] px{q.pixel_offset}")
        for q in sc.sorted_covers()[:40]:
            print(f"    cover {q.title:<18} anchor {q.anchor} "
                  f"{q.width}x{q.height} px{q.pixel_offset} "
                  f"every {q.frame_interval}ms")
    if a.json:
        print(json.dumps(sc.to_json(), indent=1))
    if a.png:
        return _render_png(a, d, sc, lib)
    return 0


def _render_png(a, d, sc: Scenery, lib: SceneLibrary) -> int:
    import puzzle as puzzlemod                              # noqa: PLC0415
    plib = puzzlemod.PuzzleLibrary(lib.root)
    pm = plib.get(a.map)
    if pm is None:
        print(f"no ground art for {a.map}: {plib.reason}", file=sys.stderr)
        return 1
    if a.at:
        cx, cy = (int(v) for v in a.at.split(","))
        r = a.radius
        rect = pm.window_rect(max(0, cx - r), max(0, cy - r),
                              min(d.width - 1, cx + r), min(d.height - 1, cy + r))
    else:
        rect = (0, 0, pm.px_w, pm.px_h)
    scale = max(1, a.scale)
    w, h, rgb = pm.render(rect, scale=scale)
    cache = SpriteCache(lib.root)
    items = list(sc.scenes) + ([] if a.no_covers else list(sc.covers))
    rgb, n = composite(pm, rect, rgb, items, cache, scale=scale)
    if a.overlay:
        rgb = _mark_walkable(pm, rect, rgb, w, h, d, sc, scale)
    a.png.parent.mkdir(parents=True, exist_ok=True)
    a.png.write_bytes(puzzlemod.encode_png(w, h, rgb))
    print(f"  wrote {a.png}  {w}x{h}  ({n} of {len(items)} sprites drawn)")
    return 0


def _mark_walkable(pm, rect, rgb: bytes, w: int, h: int, d, sc: Scenery,
                   scale: int) -> bytes:
    """A dot on every walkable cell: red where the base grid says so, cyan
    where only the scene layer does. If the placement rule were wrong the cyan
    dots would not land on the stepping stones."""
    base = bytes(1 if c[0] == 0 else 0 for c in d.cells)
    over, _ = apply_passability(base, d.width, d.height, sc.scenes)
    buf = bytearray(rgb)
    x0, y0 = rect[0], rect[1]
    for y in range(d.height):
        for x in range(d.width):
            i = y * d.width + x
            if not over[i]:
                continue
            col = (232, 64, 64) if base[i] else (64, 240, 232)
            px, py = pm.cell_px(x, y)
            ox = int((px - x0) / scale)
            oy = int((py - y0) / scale)
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    tx, ty = ox + dx, oy + dy
                    if 0 <= tx < w and 0 <= ty < h:
                        o = (ty * w + tx) * 3
                        buf[o:o + 3] = bytes(col)
    return bytes(buf)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    coroot.add_root_argument(ap)
    ap.add_argument("map", nargs="?", help="a map name, e.g. newbie")
    ap.add_argument("--verify", action="store_true",
                    help="corpus-wide scene/cover survey")
    ap.add_argument("--list", action="store_true", help="every placed sprite")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--png", type=Path, default=None, help="draw the map here")
    ap.add_argument("--at", default="", metavar="X,Y",
                    help="with --png: crop a window around this cell")
    ap.add_argument("--radius", type=int, default=24)
    ap.add_argument("--scale", type=int, default=1,
                    help="with --png: integer decimation")
    ap.add_argument("--no-covers", action="store_true",
                    help="with --png: ground + scenes only")
    ap.add_argument("--overlay", action="store_true",
                    help="with --png: dot every walkable cell -- red from the "
                         "base grid, cyan from a scene layer")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="with --verify: write the JSON report here")
    a = ap.parse_args(argv)
    if a.verify or not a.map:
        return _cmd_verify(a)
    return _cmd_map(a)


if __name__ == "__main__":
    raise SystemExit(main())
