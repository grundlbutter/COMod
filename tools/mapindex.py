#!/usr/bin/env python3
r"""
mapindex.py -- "a primary map, and every piece of art that belongs to it".

The world is 136 `.DMap` files, but a map's *art* lives in four other places,
reached by a chain that is entirely recoverable from the files:

    map/map/<name>.DMap
        |  puzzle_path  (embedded char[260], e.g. "map\puzzle\island.pul")
        v
    map/puzzle/<name>.pul
        |  ani_path (char[256], e.g. "ani\island.ani") + uint16 tile indices
        v
    ani/<name>.json          {"Puzzle0": ["data/map/puzzle/island/lake/lake000.dds"], ...}
        v
    data/map/puzzle/<region>/<group>/*.dds        <- the ground tiles

and, from the DMap's own layer table:

    scene layers  -> map/Scene/*.scene  -> per-part ani file + title
    cover layers  -> an ani file + a key, i.e. an animated sprite on the map
    effect layers -> a 3DEffect.ini key
    sound layers  -> a sound path

VERIFIED end to end on real files: `island.DMap` -> `island.pul`
(PUZZLE2, 78x89, 465 distinct tiles) -> `ani/island.json` (538 entries) ->
`data/map/puzzle/island/lake/lake000.dds`. Same for `desert` and `newplain`.

The layer *body* shapes are those documented in docs/assets.md 3.3, which were
verified by the earlier workstream against all 136 files (they are what makes
the layer walk land on EOF). `coassets.DMap` locates the layer table but does
not decode the bodies; this module walks it, and `dmap.decode_layer` decides
what each record's bytes MEAN -- one home for the layout, shared with
`core/dmap`'s own walk, because while they were separate they drifted and one
of them read 0 of a map's 2,923 layers.

AND WHAT THE CHAIN DOES NOT REACH.  Every step above can fail, and each used
to fail by dropping the reference where it stood -- `if self._exists(f)`, in
three places.  So the record described the art that happened to be present and
said nothing at all about the rest: 5517's `hq` names 1,364 pieces, ships 56,
and reported 56 with no error.  `MapRecord.unresolved` is the other half, and
`UNRESOLVED_REASONS` keeps the kinds of failure apart -- a key the index does
not define is not the same answer as a file the install does not carry, and
only one of them is worth going to look for.

Read-only. Nothing here writes anything.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import (DEFAULT_ROOT, DMap, PUL_EMPTY,   # noqa: E402
                      Pul, Scene)

# --- layer tags.  THE TABLE IS `core/dmap`'s, imported rather than copied.
#
# This module used to carry its own -- `{1: 268, 4: 416, 10: 72, 15: 276}`,
# the pre-1005 shape -- and the copies drifted.  Version 1005 widened the
# cover record to 420 bytes and added a tag 0; version 1006 renumbered cover
# from 4 to 24.  `core/dmap` learned both; this file did not, so it met the
# unmodelled tag, returned cleanly, and reported a map with no art at all.
#
# MEASURED on the CCO 2.0 install, layers read of layers declared:
#
#     ninja01_new (1006)   0 of 2,923      2020love01_new (1006)  0 of 1,170
#     bp-flandlords-y_new  0 of    79      magictower01_new (1005) 1 of   34
#
# Neither walk raised.  The short one just described a smaller map, which is
# indistinguishable from a map that is small.  See `dmap.decode_layer`.
from dmap import UNINIT, decode_layer, layer_payload_for   # noqa: E402


def norm(p: str) -> str:
    """A path as written in a binary field -> a logical asset path."""
    return p.replace("\\", "/").lstrip("/").lower()


@dataclass
class MapLayer:
    kind: str
    x: int = 0
    y: int = 0
    path: str = ""          # scene / sound: the referenced file
    ani: str = ""           # cover: the .ani file
    key: str = ""           # cover: the frame key inside it
    name: str = ""          # effect: a 3DEffect.ini key
    width: int = 0
    height: int = 0
    frame_interval: int = 0


def iter_layers(m: DMap) -> Iterator[MapLayer]:
    """Decode a DMap's layer table. Stops cleanly at the first unmodelled tag
    or at MSVC uninitialised fill rather than inventing structure.

    The walk is here; the *record layout* is `core/dmap`'s, chosen by the
    map's own version (`layer_payload_for`) rather than by a table this file
    keeps.  `coassets.DMap.version` is the string the header carried, which
    is why the `_for` spelling exists -- see its docstring.
    """
    d = m.data
    off = m.layers_offset
    payload = layer_payload_for(m.version)
    for i in range(m.layer_count):
        if off + 4 > len(d):
            return
        (tag,) = struct.unpack_from("<I", d, off)
        if tag == UNINIT:
            return
        size = payload.get(tag)
        if size is None or off + 4 + size > len(d):
            return
        body = d[off + 4:off + 4 + size]
        off += 4 + size
        rec = decode_layer(tag, body, i)
        shape = rec["shape"]
        ox, oy = rec.get("origin", (0, 0))
        if shape == "scene":
            yield MapLayer("scene", ox, oy, path=rec["path"])
        elif shape == "cover":
            w, h = rec.get("size", (0, 0))
            yield MapLayer("cover", ox, oy, ani=rec["path"], key=rec["key"],
                           width=w, height=h,
                           frame_interval=rec.get("frame_interval", 0))
        elif shape == "effect":
            yield MapLayer("effect", ox, oy, name=rec["name"])
        elif shape == "sound":
            yield MapLayer("sound", ox, oy, path=rec["path"])
        else:
            # 1005's tag 0, whose two u32s `core/dmap` deliberately does not
            # name.  It is a layer that was READ -- so it counts toward
            # `layers_decoded` -- and it references no art.  Yielding it as
            # its own kind keeps those two facts apart; dropping it here
            # would make the walk under-report what it consumed.
            yield MapLayer(shape or "unknown", ox, oy)


#: Why a reference the map makes did not end at a file this install ships.
#:
#: FIVE REASONS AND NOT ONE, because the states are not the same state and
#: this codebase has been burnt by collapsing them.  "UNKNOWN" and "NOT
#: THERE" are different answers and a reader has to be able to act on the
#: difference:
#:
#:   unknown    -- the map named a KEY (a `.pul` tile index, a cover key, a
#:                 scene part title) and the tile index does not define it.
#:                 We cannot even say which file the map wanted.  Looking for
#:                 the art is pointless; the index is what is short.
#:   absent     -- the map named a FILE and the install does not ship it.  We
#:                 know exactly what is missing and could go and find it.
#:   unreadable -- a container on the way (the `.pul`, a `.scene`) is here and
#:                 did not parse.  Everything behind it is unknown, and the
#:                 count of what is behind it is unknown too.
#:   loose-only -- the file ships, but inside an archive, and the reader for
#:                 this step only reads loose files.  A tooling limit, not a
#:                 fact about the install -- labelled as such so it is never
#:                 read as "the game is missing art".
#:   unsupported -- the file ships and is readable and this reader does not
#:                 know its format (a `.pux` where a `.pul` was expected).
#:                 Also a tooling limit, and NOT the same one as `loose-only`:
#:                 that one is fixed by reaching into an archive, this one by
#:                 writing a reader.
#:
#: The rule the labels exist for: an asset the map names but the install does
#: not ship is shown as UNRESOLVED, never omitted.  Dropping it makes the map
#: look smaller than it is, and a smaller map is indistinguishable from a map
#: that is small.
#:
#: And the converse duty, learnt the same afternoon: a reference that is NOT
#: unresolved must not appear here either.  `PUL_EMPTY` put a phantom
#: `Puzzle65535` on 27 of CCO's 136 maps until the sentinel was recognised.
#: A list that cries wolf is read as carefully as a list that says nothing.
UNRESOLVED_REASONS = ("unknown", "absent", "unreadable", "loose-only",
                      "unsupported")

REASON_TEXT = {
    "unknown": "the tile index does not define this key — which file the map "
               "wants is unknown",
    "absent": "named by the map, and this install ships no such file",
    "unreadable": "present, but it did not parse — what is behind it is unknown",
    "loose-only": "ships only inside an archive; this reader reads loose files",
    "unsupported": "ships, and is in a format this reader does not read",
}


def printable(s: str) -> str:
    r"""A binary field's text, safe to put in a list a human reads.

    Layer records carry fixed `char[]` fields and some of them hold junk --
    `icecrypt-lev2` has cover keys of ``"\x01"`` and ``"l"``.  Those are worth
    SHOWING, because a map naming a garbage key is a real thing to know, but a
    raw control byte in the middle of a panel row is invisible: the row looks
    empty and the entry reads as a bug in the panel rather than as what the
    file says.  Escaped, it is legible and still literal.
    """
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in s)


@dataclass
class MapRef:
    """One reference a map makes that did not end at a shipped file.

    `ref` is what the map actually said -- a logical path when the chain got
    far enough to name one, otherwise the bare key it asked for (``Puzzle44``,
    a cover key, a scene part title) -- and `via` is the index or container it
    asked through.  Together they keep the entry actionable instead of a tally.
    """
    group: str          # ground | cover | scene | sound  (the panel's sections)
    ref: str
    reason: str         # one of UNRESOLVED_REASONS
    via: str = ""       # the index or container the reference went through

    def to_json(self) -> dict:
        return {"group": self.group, "ref": printable(self.ref),
                "reason": self.reason,
                "why": REASON_TEXT.get(self.reason, self.reason),
                "via": printable(self.via)}


@dataclass
class MapRecord:
    """One world map and everything that draws it."""
    name: str                       # "island"
    file: str                       # logical path of the .DMap
    version: str = ""
    width: int = 0
    height: int = 0
    cells: int = 0
    document_id: Optional[int] = None
    puzzle: str = ""                # logical path of the .pul
    ani: str = ""                   # logical path of the .ani/.json
    region: str = ""                # "island" -- the data/map/puzzle/<region> folder
    #: What `dmap.open_map` actually read, and which rule chose it --
    #: "desert.7z (named by the registry)". `file` stays the logical .DMap.
    source: str = ""
    tile_count: int = 0             # distinct tiles actually placed
    tiles: list[str] = field(default_factory=list)      # logical .dds paths
    scenes: list[str] = field(default_factory=list)     # map/Scene/*.scene
    covers: list[dict] = field(default_factory=list)    # animated sprites
    effects: list[str] = field(default_factory=list)    # 3DEffect.ini keys
    sounds: list[str] = field(default_factory=list)
    layer_count: int = 0
    layers_decoded: int = 0
    error: str = ""
    #: Every reference that did NOT end at a shipped file, with its reason.
    #: These used to be dropped where they were found -- three separate
    #: `if self._exists(f)` filters -- so the panel showed a map's resolved
    #: art and never said anything had gone missing.
    unresolved: list[MapRef] = field(default_factory=list)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def resolved_count(self) -> int:
        """Distinct shipped files this map draws with.

        Deliberately a set over all three groups: the ground tiles, the cover
        frames and the scene-part frames are drawn from the same shared art
        pool and a file that appears in two of them is one file.
        """
        return len(self.resolved_paths)

    @property
    def resolved_paths(self) -> set:
        out = set(self.tiles)
        for c in self.covers:
            out.update(c.get("frames", ()))
        return out

    def to_json(self, *, with_tiles: bool = True) -> dict:
        d = {
            "name": self.name, "file": self.file, "version": self.version,
            "width": self.width, "height": self.height, "area": self.area,
            "documentId": self.document_id, "puzzle": self.puzzle, "ani": self.ani,
            "region": self.region, "source": self.source,
            "tileCount": self.tile_count,
            "sceneCount": len(self.scenes), "coverCount": len(self.covers),
            "effectCount": len(self.effects), "soundCount": len(self.sounds),
            "layerCount": self.layer_count, "layersDecoded": self.layers_decoded,
            "error": self.error,
            # The two numbers the panel leads with. `resolvedCount` counts the
            # DMap's own art (ground + covers); scene parts are fetched a level
            # down by `scene_parts` and are added there, which is why the
            # payload carries `sceneCount` beside it rather than folding the
            # two into a single total the server cannot yet justify.
            "resolvedCount": self.resolved_count,
            "unresolvedCount": len(self.unresolved),
            "unresolved": [u.to_json() for u in self.unresolved],
        }
        if with_tiles:
            d |= {"tiles": self.tiles, "scenes": self.scenes,
                  "covers": self.covers, "effects": self.effects,
                  "sounds": self.sounds}
        return d


class MapIndex:
    """Every world map, with its art resolved. Built lazily: the map list is
    cheap, the per-map art walk is done on demand and cached."""

    def __init__(self, root: Path = DEFAULT_ROOT, exists=None):
        self.root = Path(root)
        #: Predicate telling us whether a logical path resolves at all, so the
        #: viewer never offers a tile it cannot read. Almost all map art lives
        #: in data.wdf rather than loose on disk, so a bare filesystem check
        #: would report every map as having zero tiles -- the caller normally
        #: passes the viewer's catalogue here.
        self._own_assets = None
        if exists is None:
            def exists(p):
                if (self.root / p).is_file():
                    return True
                if self._own_assets is None:
                    from coassets import AssetRoot
                    self._own_assets = AssetRoot(self.root)
                    try:
                        self._own_assets.load_names()
                    except Exception:
                        pass
                    import coroot
                    for rel in ("out/wdf/c3_names.json", "out/wdf/data_names.json"):
                        f = coroot.find_derived(rel)
                        if f is not None:
                            tbl = json.loads(f.read_text("utf-8"))
                            (self._own_assets._names or {}).update(
                                {int(k, 16): v for k, v in tbl.items()})
                return self._own_assets.exists(p)
        self._exists = exists
        self._ani_cache: dict[str, dict] = {}
        self._maps: dict[str, MapRecord] = {}
        self._doc_ids: dict[str, int] = {}
        self._load_gamemap()

    # -- the map list ------------------------------------------------------
    def _load_gamemap(self) -> None:
        """The map registry: DocumentId + FileName, in whichever form ships.

        Rows that match no shipped `.DMap` are normal -- they name maps this
        build does not carry (20 of CCO's 156).

        **Both forms, because the client decides which one you get.**
        `ini/GameMap.json` is the *community* client's pre-parsed form; every
        official client ships the binary `ini/GameMap.dat` and no `.json` at
        all. Reading only the JSON left this index with no DocumentIds on any
        official client -- the map list simply had none attached, with no
        error, which is how it went unnoticed.

        Same JSON-vs-original gap `docs/handoff_oracle_and_readers.md` §1
        records for four other readers. `client/gamemap.py` and
        `tools/puzzle.py` were both fixed; this one was missed. The parser is
        COre's, imported rather than copied -- a fourth copy of a binary
        layout is how one of them ends up subtly wrong and stays wrong.
        """
        from dmap import load_gamemap                     # noqa: PLC0415
        _rel, rows = load_gamemap(self.root)
        if not rows:
            return
        for r in rows:
            fn = str(r.get("FileName", "")).replace("\\", "/").lower()
            if fn:
                self._doc_ids[Path(fn).stem] = r.get("DocumentId")

    def map_files(self) -> list[Path]:
        """The loose `.DMap` files, which is **not** the map list.

        Kept for callers that genuinely want files on disk; `names()` is the
        map list and it is a different, larger set.  See `dmap.map_names`.
        """
        d = self.root / "map" / "map"
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() == ".dmap")

    def names(self) -> list[str]:
        """Every map the install has, however it ships.

        A `*.DMap` walk misses 20 of 5517's maps, 72 of 6090's and **122 of
        6609's** -- 113 that ship only as a `.7z` plus nine registry rows
        naming a map that ships in neither form.  RE-DERIVED 2026-08-11; see
        `dmap.map_names`, which is the one place the union is computed.
        """
        from dmap import map_names                          # noqa: PLC0415
        return map_names(self.root)

    # -- ani resolution ----------------------------------------------------
    def _ani(self, ani_path: str) -> dict:
        """Load an `.ani` tile index: tile key -> list of frame paths.

        Two forms, same fact. The community client ships `ani/<stem>.json`;
        every official client ships `ani/<stem>.ani`, an INI-flavoured text
        file. Reading only the JSON gave every official client an empty tile
        index -- so a map resolved its `.pul` and then reported **zero
        tiles**, with no error, and the ground simply had no art.

        Third instance of the JSON-vs-original split in this one file after
        the map registry; `read_ani` is COre's for that reason -- and so, now,
        is choosing between the two spellings (`dmap.load_ani`). This function
        held the second hand-rolled copy of that choice while `mapparts` held
        none and collected every CCO map with no scenery at all (C-2026-08-09-ani-json-spelling).
        """
        key = norm(ani_path)
        if key in self._ani_cache:
            return self._ani_cache[key]
        from dmap import load_ani                     # noqa: PLC0415
        _rel, out = load_ani(self.root, key)
        self._ani_cache[key] = out
        return out

    def _frames(self, ani_path: str, key: str) -> list[str]:
        d = self._ani(ani_path)
        v = d.get(key)
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [norm(x) for x in v if isinstance(x, str)]

    # -- one map -----------------------------------------------------------
    def get(self, name: str) -> MapRecord:
        key = name.lower()
        if key in self._maps:
            return self._maps[key]
        rec = self._build(name)
        self._maps[key] = rec
        return rec

    def _build(self, name: str) -> MapRecord:
        stem = Path(name).stem
        # The file the REGISTRY names, not the loose leftover beside it: on
        # 6090 and 6609, 74 and 77 loose `.DMap` files disagree with their own
        # archive, and 113 maps on 6609 have no loose file at all.
        from dmap import open_map                           # noqa: PLC0415
        raw, src = open_map(self.root, stem)
        # `file` keeps its old meaning -- the logical `.DMap` path -- because
        # consumers key on it and one of them asserts the extension. What is
        # NEW is `source`, which says what was actually read and why. Widening
        # an existing field's meaning as a side effect of a fix is how a value
        # stops answering the question it was defined for.
        rec = MapRecord(name=stem,
                        file=f"map/map/{stem}.DMap".lower(),
                        document_id=self._doc_ids.get(stem.lower()))
        rec.source = src
        if raw is None:
            rec.error = src
            return rec
        try:
            m = DMap.parse(raw)
        except Exception as e:
            rec.error = f"DMap parse failed: {e}"
            return rec
        rec.version = m.version
        rec.width, rec.height = m.width, m.height
        rec.cells = m.width * m.height
        rec.layer_count = m.layer_count
        rec.puzzle = norm(m.puzzle_path)

        # ---- ground tiles, via the .pul and its .ani ----
        pul_path = self.root / rec.puzzle
        if not pul_path.is_file():
            # Not silence.  A map whose `.pul` this reader cannot open has an
            # unknown number of ground tiles, and reporting zero of them is a
            # claim -- the wrong one.  Which of the two states it is decides
            # whether anyone should go looking for a file.
            if rec.puzzle:
                rec.unresolved.append(MapRef(
                    "ground", rec.puzzle,
                    "loose-only" if self._exists(rec.puzzle) else "absent",
                    via="the .DMap header names it"))
        elif rec.puzzle.endswith(".pux"):
            # A `.pux` is `TqTerrain` -- the *PuzzleSave* format, not a
            # compiled `.pul`, and far richer than one; `dmap.read_pux` reads
            # its header and nothing here reads its tiles.  Feeding it to
            # `Pul.parse` produces a spectacular struct error whose text
            # ("requires a buffer of at least 1,909,642,683,969,878,288
            # bytes") reads exactly like a corrupt file and would send someone
            # hunting one.  135 of 7878's 470 maps and 4 of CCO's name a
            # `.pux`, so this is a population, not an oddity -- and it is a
            # limit of this reader, which is a different fact about the world
            # from a file the install does not ship.
            rec.unresolved.append(MapRef(
                "ground", rec.puzzle, "unsupported",
                via="TqTerrain (.pux); this reader reads compiled .pul"))
        else:
            try:
                pz = Pul.load(pul_path)
            except Exception as e:
                rec.error = f"pul: {e}"
                rec.unresolved.append(MapRef("ground", rec.puzzle, "unreadable",
                                             via=str(e)[:120]))
                pz = None
            if pz is not None:
                rec.ani = norm(pz.ani_path)
                # `PUL_EMPTY` is "nothing painted here", not tile 65535.
                # Without this line 27 of CCO's 136 maps grew a phantom
                # `Puzzle65535` in their unresolved list -- and a false entry
                # in the honesty mechanism costs what a dropped one does: it
                # stops being read.
                used = sorted(set(pz.tiles) - {PUL_EMPTY})
                seen: list[str] = []
                for idx in used:
                    frames = self._frames(pz.ani_path, f"Puzzle{idx}")
                    if not frames:
                        # The `.pul` placed this tile index and the index has
                        # no entry for it: UNKNOWN, not missing art.
                        rec.unresolved.append(MapRef(
                            "ground", f"Puzzle{idx}", "unknown", via=rec.ani))
                        continue
                    for f in frames:
                        if f in seen:
                            continue
                        if self._exists(f):
                            seen.append(f)
                        else:
                            rec.unresolved.append(
                                MapRef("ground", f, "absent", via=rec.ani))
                rec.tiles = seen
                rec.tile_count = len(seen)
                rec.region = self._region_of(seen)

        # ---- layers: scenery, animated covers, effects, sounds ----
        try:
            n = 0
            for L in iter_layers(m):
                n += 1
                if L.kind == "scene":
                    p = norm(L.path)
                    if p and p not in rec.scenes:
                        rec.scenes.append(p)
                elif L.kind == "cover":
                    if any(c["key"] == L.key and c["ani"] == norm(L.ani)
                           for c in rec.covers):
                        continue
                    all_frames = self._frames(L.ani, L.key)
                    frames = [f for f in all_frames if self._exists(f)]
                    if not all_frames:
                        # The layer names a key its own index does not carry.
                        rec.unresolved.append(MapRef(
                            "cover", L.key or "(no key)", "unknown",
                            via=norm(L.ani)))
                    for f in all_frames:
                        if not self._exists(f):
                            rec.unresolved.append(
                                MapRef("cover", f, "absent", via=norm(L.ani)))
                    rec.covers.append({
                        "ani": norm(L.ani), "key": L.key, "frames": frames,
                        # Kept per cover as well as in `rec.unresolved`: a
                        # sprite that resolves 2 of its 8 frames is a
                        # different thing from one that resolves all 2, and
                        # the strip in the panel is drawn per cover.
                        "frameCount": len(all_frames),
                        "missingFrames": len(all_frames) - len(frames),
                        "width": L.width, "height": L.height,
                        "frameInterval": L.frame_interval,
                    })
                elif L.kind == "effect":
                    if L.name and L.name not in rec.effects:
                        rec.effects.append(L.name)
                elif L.kind == "sound":
                    p = norm(L.path)
                    if p and p not in rec.sounds:
                        rec.sounds.append(p)
                        if not self._exists(p):
                            rec.unresolved.append(MapRef(
                                "sound", p, "absent", via="a SOUND layer"))
            rec.layers_decoded = n
        except Exception as e:
            rec.error = (rec.error + "; " if rec.error else "") + f"layers: {e}"

        if not rec.region:
            rec.region = self._region_of([c for cov in rec.covers
                                          for c in cov["frames"]])
        # One reference, one row.  Shared art means the same absent file is
        # reached from many tile indices and many cover layers; listing it
        # eleven times would make the unresolved count a measure of how often
        # the map draws it rather than of how much is missing.
        seen_refs: set = set()
        deduped: list[MapRef] = []
        for u in rec.unresolved:
            k = (u.group, u.ref, u.reason)
            if k in seen_refs:
                continue
            seen_refs.add(k)
            deduped.append(u)
        rec.unresolved = deduped
        return rec

    @staticmethod
    def _region_of(paths: list[str]) -> str:
        """`data/map/puzzle/<region>/...` -- the folder the art is filed under."""
        for p in paths:
            parts = p.split("/")
            if len(parts) >= 4 and parts[0] == "data" and parts[1] == "map":
                return parts[3]
        return ""

    # -- scene parts -------------------------------------------------------
    def scene_parts(self, scene_logical: str) -> dict:
        """The pieces of one `map/Scene/*.scene`, each naming an ani file and a
        title, which resolve to real sprite frames.

        Returns ``{"parts": [...], "unresolved": [...], "error": str}`` and
        not a bare list.  It used to return the list, and its **three**
        early exits all returned ``[]``: the file is not on disk, the file
        did not parse, and every part resolved to nothing.  A caller could
        not tell those apart, and the panel rendered all three as a scene
        with no pieces.  `mapparts`'s own docstring records the first of the
        three as the trap that once collected every CCO map with no scenery
        at all -- the art lives in the archives and a `.is_file()` test on a
        logical path answers a different question.
        """
        out: dict = {"parts": [], "unresolved": [], "error": ""}

        def bad(ref, reason, via=""):
            out["unresolved"].append(
                MapRef("scene", ref, reason, via=via).to_json())

        p = self.root / scene_logical
        if not p.is_file():
            loose = self._exists(scene_logical)
            out["error"] = ("ships only inside an archive; this reader reads "
                            "loose files" if loose else "not shipped")
            bad(scene_logical, "loose-only" if loose else "absent",
                via="a SCENE layer")
            return out
        try:
            sc = Scene.load(p)
        except Exception as e:
            out["error"] = f"did not parse: {e}"
            bad(scene_logical, "unreadable", via=str(e)[:120])
            return out
        for part in sc.parts:
            named = self._frames(part.path, part.title)
            frames = [f for f in named if self._exists(f)]
            if not named:
                bad(part.title or "(no title)", "unknown", via=norm(part.path))
            for f in named:
                if not self._exists(f):
                    bad(f, "absent", via=norm(part.path))
            out["parts"].append({"ani": norm(part.path), "title": part.title,
                                 "width": part.width, "height": part.height,
                                 "frames": frames,
                                 "frameCount": len(named),
                                 "missingFrames": len(named) - len(frames)})
        return out

    # -- the whole list ----------------------------------------------------
    def summary(self) -> list[dict]:
        """One row per map, cheap enough to build for all 136 at once: header
        only, no art walk."""
        rows = []
        for p in self.map_files():
            try:
                m = DMap.load(p)
            except Exception as e:
                rows.append({"name": p.stem, "file": f"map/map/{p.name}".lower(),
                             "error": str(e)[:80], "width": 0, "height": 0,
                             "area": 0, "layerCount": 0})
                continue
            rows.append({
                "name": p.stem, "file": f"map/map/{p.name}".lower(),
                "version": m.version, "width": m.width, "height": m.height,
                "area": m.width * m.height, "layerCount": m.layer_count,
                "puzzle": norm(m.puzzle_path),
                "documentId": self._doc_ids.get(p.stem.lower()),
                "error": "",
            })
        rows.sort(key=lambda r: (-r["area"], r["name"]))
        return rows


def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("map", nargs="?", help="a map name, e.g. island")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ix = MapIndex(Path(a.root))
    if not a.map:
        rows = ix.summary()
        print(f"{len(rows)} world maps, largest first\n")
        print(f"  {'map':<26}{'size':>12}{'layers':>8}{'id':>7}  puzzle")
        for r in rows:
            print(f"  {r['name']:<26}{r['width']}x{r['height']:<7}"
                  f"{r['layerCount']:>8}{str(r.get('documentId') or ''):>7}  "
                  f"{r.get('puzzle','')}")
        return 0
    rec = ix.get(a.map)
    if a.json:
        print(json.dumps(rec.to_json(), indent=1))
        return 0
    print(f"{rec.name}  v{rec.version}  {rec.width}x{rec.height}  "
          f"region={rec.region or '?'}  id={rec.document_id}")
    print(f"  puzzle : {rec.puzzle}")
    print(f"  ani    : {rec.ani}")
    print(f"  tiles  : {rec.tile_count}")
    for t in rec.tiles[:6]:
        print(f"             {t}")
    if rec.tile_count > 6:
        print(f"             ... {rec.tile_count - 6} more")
    print(f"  layers : {rec.layers_decoded}/{rec.layer_count} decoded")
    print(f"  scenes : {len(rec.scenes)}  {rec.scenes[:3]}")
    print(f"  covers : {len(rec.covers)}  "
          f"{[c['key'] for c in rec.covers[:4]]}")
    print(f"  effects: {len(rec.effects)}  {rec.effects[:4]}")
    print(f"  sounds : {len(rec.sounds)}  {rec.sounds[:2]}")
    print(f"  art set: {rec.resolved_count} files resolve, "
          f"{len(rec.unresolved)} references do not")
    if rec.unresolved:
        by_reason: dict = {}
        for u in rec.unresolved:
            by_reason.setdefault(u.reason, []).append(u)
        for reason in UNRESOLVED_REASONS:
            group = by_reason.get(reason)
            if not group:
                continue
            print(f"    {reason:<11}{len(group):>5}  -- {REASON_TEXT[reason]}")
            for u in group[:5]:
                print(f"                    {u.group}: {printable(u.ref)}"
                      + (f"   (via {printable(u.via)})" if u.via else ""))
            if len(group) > 5:
                print(f"                    ... {len(group) - 5} more")
    if rec.error:
        print(f"  ERROR  : {rec.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
