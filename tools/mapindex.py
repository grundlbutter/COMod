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
not decode the bodies; this module does.

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

from coassets import DEFAULT_ROOT, DMap, Pul, Scene   # noqa: E402

# --- layer tags. See docs/assets.md 3.3: the wiki's tag->name mapping is
#     misleading for this client; these are the values this build emits.
LAYER_SCENE = 1
LAYER_COVER = 4
LAYER_EFFECT = 10
LAYER_SOUND = 15

LAYER_BODY_SIZE = {
    LAYER_SCENE: 268,
    LAYER_COVER: 416,
    LAYER_EFFECT: 72,
    LAYER_SOUND: 276,
}
LAYER_NAME = {LAYER_SCENE: "scene", LAYER_COVER: "cover",
              LAYER_EFFECT: "effect", LAYER_SOUND: "sound"}

#: MSVC uninitialised heap fill -- some maps declare more layers than they wrote.
UNINIT = 0xCDCDCDCD


def _s(b: bytes) -> str:
    return b.split(b"\0")[0].decode("latin-1", "replace")


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
    or at MSVC uninitialised fill rather than inventing structure."""
    d = m.data
    off = m.layers_offset
    for _ in range(m.layer_count):
        if off + 4 > len(d):
            return
        (tag,) = struct.unpack_from("<I", d, off)
        if tag == UNINIT:
            return
        size = LAYER_BODY_SIZE.get(tag)
        if size is None or off + 4 + size > len(d):
            return
        body = d[off + 4:off + 4 + size]
        off += 4 + size
        if tag == LAYER_SCENE:
            x, y = struct.unpack_from("<II", body, 260)
            yield MapLayer("scene", x, y, path=_s(body[:260]))
        elif tag == LAYER_COVER:
            ox, oy, w, h = struct.unpack_from("<4I", body, 388)
            (fi,) = struct.unpack_from("<I", body, 412)
            yield MapLayer("cover", ox, oy, ani=_s(body[:260]),
                           key=_s(body[260:388]), width=w, height=h,
                           frame_interval=fi)
        elif tag == LAYER_EFFECT:
            x, y = struct.unpack_from("<II", body, 64)
            yield MapLayer("effect", x, y, name=_s(body[:64]))
        elif tag == LAYER_SOUND:
            x, y = struct.unpack_from("<II", body, 260)
            yield MapLayer("sound", x, y, path=_s(body[:260]))


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

    @property
    def area(self) -> int:
        return self.width * self.height

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
        if pul_path.is_file():
            try:
                pz = Pul.load(pul_path)
                rec.ani = norm(pz.ani_path)
                used = sorted(set(pz.tiles))
                seen: list[str] = []
                for idx in used:
                    for f in self._frames(pz.ani_path, f"Puzzle{idx}"):
                        if f not in seen and self._exists(f):
                            seen.append(f)
                rec.tiles = seen
                rec.tile_count = len(seen)
                rec.region = self._region_of(seen)
            except Exception as e:
                rec.error = f"pul: {e}"

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
                    frames = self._frames(L.ani, L.key)
                    frames = [f for f in frames if self._exists(f)]
                    if not any(c["key"] == L.key and c["ani"] == norm(L.ani)
                               for c in rec.covers):
                        rec.covers.append({
                            "ani": norm(L.ani), "key": L.key, "frames": frames,
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
            rec.layers_decoded = n
        except Exception as e:
            rec.error = (rec.error + "; " if rec.error else "") + f"layers: {e}"

        if not rec.region:
            rec.region = self._region_of([c for cov in rec.covers
                                          for c in cov["frames"]])
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
    def scene_parts(self, scene_logical: str) -> list[dict]:
        """The pieces of one `map/Scene/*.scene`, each naming an ani file and a
        title, which resolve to real sprite frames."""
        p = self.root / scene_logical
        if not p.is_file():
            return []
        try:
            sc = Scene.load(p)
        except Exception:
            return []
        out = []
        for part in sc.parts:
            frames = [f for f in self._frames(part.path, part.title)
                      if self._exists(f)]
            out.append({"ani": norm(part.path), "title": part.title,
                        "width": part.width, "height": part.height,
                        "frames": frames})
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
    if rec.error:
        print(f"  ERROR  : {rec.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
