#!/usr/bin/env python3
r"""
mapparts.py -- what a map is made of, so it can be collected.

`tools/parts.py` answers this for a figure: given a body, which mesh, skin
and motion files does it actually need?  This is the same question for a
**map**, and the Collection needs it for the same reason -- an entry that
lists only the thing you clicked is not usable as a swap.

A map is not a mesh plus a skin.  It is:

    map/map/<name>.DMap        the cell grid: walkability, portals, layers
    map/puzzle/<name>.pul      the painted background, named by the DMap
    ani/<something>.ani        a *shared* index: logical key -> art path
    data/map/.../<tile>.dds    the art those keys resolve to
    <effect names>             ANIMATION layers, resolved by 3DEffect

MEASURED, and it is the finding that makes this practical: **a map uses a
small named subset of a large shared index.**  Six maps sampled on 5517:

    09christmas01   15 art files      from an .ani holding   363
    09Christmas02   56 art files      from .ani holding    1,508
    2009-7x         34 art files      from .ani holding    1,508
    arena-none       0                a bare map, no layers

Every COVER layer carries its own ``key`` (``dcity11.tga``) alongside the
``.ani`` that resolves it, so the closure is exact rather than "ship the
whole library": 0 unresolved keys across every map sampled.  Collecting a
map therefore costs tens of files, not thousands.

TWO PROPERTIES THAT ARE NOT LIKE A FIGURE'S
-------------------------------------------
**Art is shared between maps.**  The `.ani` index certainly is, and so are
many of the tiles it names.  So a collected map staged over an install can
change *other* maps.  `shared_art_index` says which files are implicated
and by whom, and `MapPart.shared` carries it per file, so the decision is
made with the facts rather than discovered afterwards.

**The art is in the archive, and a collector that stats the filesystem
gets a map with no scenery.**  Measured on 5517, `09Christmas02`: all 56
art files resolve inside `c3.wdf`/`data.wdf` and **none** is loose, while
the DMap, the `.pul` and the `.ani` all are.  So filtering the closure with
``(root / rel).is_file()`` silently drops the entire scenery layer and
yields a plausible-looking three-file entry.  Read these through
`core/coassets.AssetRoot` (or any archive-aware reader), the way
`collection.gather_parts` takes a ``read`` callable rather than a path.
The paths this module returns are **logical**, not filesystem paths.

**The index is not always spelled `.ani`.**  Every DMap names one --
``ani/MapScene.ani`` -- and CCO ships that index as ``ani/MapScene.json``
and no ``.ani`` at all (60 `.json` / 0 `.ani` there, against 0/51 to 0/56 on
the five official clients).  Statting the path the DMap asked for therefore
resolved **nothing** on that base: all 35 of ``2009-7x``'s keys landed in
`unresolved` and the map collected with no scenery -- the failure above,
reached through the index rather than through the archive.  `dmap.load_ani`
resolves the two spellings for every reader that needs it and returns the
path that *answered*, which is what the collected part carries.  See
``docs/CORRECTIONS.md`` C-2026-08-09-ani-json-spelling.

**The DMap is integrity-checked, and nothing else here is.**  Measured on
the CCO 2.0 install: `integrity.json` sits at the **install root** (not
under `ini/`), and its 162 rows cover 143 distinct files -- 136 `.DMap`
plus 7 `.json`, and **no art file of any kind**.  It is absent from all
five official patch clients (5017-6090), so on those nothing is checked at
all.  `MapPart.integrity` marks the files that would trip it, which is why
the flag exists at collect time instead of surfacing at install time.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                                       # noqa: E402
import dmap as dmapmod                              # noqa: E402
import mapsnip                                      # noqa: E402

#: Roles a map's files play. `dmap` is the identity of the map; everything
#: else supports it.
#:
#: `scene`, `sound` and `otherdata` joined late, and what they cost is worth
#: recording: the closure followed only the FIRST record list, and only
#: records carrying BOTH a `path` and a `key`. Everything else in the file
#: was walked and then dropped on the floor. MEASURED over 7878's 470 maps:
#:
#:     record kind                      in the files   followed (before)
#:     early cover  (tag 4 / 24)             244,086   yes
#:     early effect (tag 10)                   2,941   yes, by NAME
#:     LATE cover   (tag 4)                   24,524   no
#:     LATE effect  (tag 19)                  10,632   no
#:     scene        (tag 1 / 3)                2,298   no
#:     sound        (tag 15)                   1,412   no
#:     .OtherData sidecar                        323   no
#:
#: The late cover list is the expensive one. Its two `.ani` indices are the
#: same two the early list already names, so the miss was invisible at the
#: index level -- but its KEYS are different, and they resolve to 1,126
#: distinct art files the early closure never reaches. On `newplain13_new`
#: that is 245 files against the 92 the collector was bringing.
#: `effectart` and `config` joined last, and they are what makes a collected
#: map a map rather than a pile of tiles:
#:
#:   `effectart`  the `.c3` meshes and `.dds` textures of every effect the map
#:                names. `effect` records the NAME -- which is the right thing
#:                to record, and is not a file -- so without this the animated
#:                layer travelled as a string and drew nothing anywhere else.
#:   `config`     a SNIPPET of a shared plaintext table, cut to this map. See
#:                `tools/mapsnip.py` for why a snippet and not the file: the
#:                `.ani` index one map needs is 1 % of a 1.1 MB file that 180
#:                other maps also read, so collecting the file both bloats the
#:                entry and makes staging it an edit to every other map.
ROLES = ("dmap", "puzzle", "plane", "ani", "art", "scene", "sound",
         "otherdata", "config", "effect", "effectart")

#: Roles whose bytes are CUT rather than copied -- see `MapPart.data`.
SNIPPET_ROLES = ("ani", "config")


@dataclass(frozen=True)
class MapPart:
    """One file (or named effect) a map needs."""

    role: str
    rel: str
    #: other maps that reference this same file, if it was asked for
    shared_with: tuple = ()
    #: would staging this file trip the install's integrity manifest?
    integrity: bool = False
    #: set when a layer named a key its `.ani` does not resolve
    missing: bool = False
    #: **The bytes, when this part is a SNIPPET rather than a whole file.**
    #:
    #: A part normally names a logical path and the collector reads it. These
    #: name a logical path whose content is CUT to this map -- the `.ani`
    #: sections it uses, the `GameMap` rows that name it -- so the bytes are
    #: produced here and there is nothing on disk to read. `collect_map`
    #: prefers `data` when it is set and reads the path when it is not.
    #:
    #: Compared and hashed on purpose-free: two parts are the same part when
    #: they name the same path in the same role, whatever the cut produced.
    data: Optional[bytes] = field(default=None, compare=False, repr=False)
    #: **HOW THIS PART GOES BACK**, which is the half `rel` cannot say.
    #:
    #: `"replace"` -- `rel` is the whole file; write these bytes there.
    #: `"merge"`   -- `rel` is a SHARED table and these bytes are the rows
    #:               or sections this map owns. Writing them over the
    #:               target's copy would delete every other map's. A tool
    #:               reintroducing this has to splice by key, and
    #:               `merge_keys` says which keys are ours to replace.
    #:
    #: Carried on the record rather than inferred from the role, because the
    #: same role is both: an `ani` part is a cut where the index could be
    #: read and the whole file where it could not.
    apply: str = "replace"
    #: The section names (`ani`, `3DEffect.ini`) or row ids (`GameMap.json`)
    #: this cut owns, for a merge. Sorted, so two collects of one map produce
    #: the same record.
    merge_keys: tuple = ()
    #: How to find those keys in the target file: `"ini-sections"` for a
    #: `[name]`-shaped table, `"json-rows"` for `GameMap.json`, `"json-keys"`
    #: for the community client's pre-parsed `ani/<name>.json`.
    merge_format: str = ""

    @property
    def shared(self) -> bool:
        return bool(self.shared_with)

    @property
    def snippet(self) -> bool:
        return self.data is not None

    def to_json(self) -> dict:
        d = {"role": self.role, "rel": self.rel}
        if self.shared_with:
            d["sharedWith"] = list(self.shared_with)
        if self.integrity:
            d["integrity"] = True
        if self.missing:
            d["missing"] = True
        if self.data is not None:
            # The bytes never go in the JSON -- this is what the dry run and
            # the UI render -- but the fact that this part is a cut, and how
            # big the cut is, is exactly what a reader wants to see.
            d["snippet"] = True
            d["bytes"] = len(self.data)
        if self.apply != "replace":
            d["apply"] = self.apply
            d["mergeFormat"] = self.merge_format
            d["mergeKeys"] = list(self.merge_keys)
        return d

    def record(self) -> dict:
        r"""The part's own JSON, as it lands on a collected entry.

        `source` is the path IN THE CLIENT'S OWN FOLDER STRUCTURE -- the same
        string the `.DMap`, the `.ani` or the registry names -- because that
        is the only address a tool putting edited content back can use. The
        library filename beside it is storage and means nothing to a client.
        """
        d = {"apply": self.apply}
        if self.merge_keys:
            d["mergeFormat"] = self.merge_format
            d["mergeKeys"] = list(self.merge_keys)
        if self.shared_with:
            d["shared"] = True
            d["sharedWith"] = list(self.shared_with)
        if self.integrity:
            d["integrity"] = True
        if self.data is not None:
            d["snippet"] = True
        return d


def _norm(rel: str) -> str:
    return str(rel).replace("\\", "/").lstrip("./")


def _fspath(root):
    """A filesystem path from whatever a caller holds -- a str, a Path, or an
    `AssetRoot` (whose install path is its ``.root``).

    `coviewer` passes `Catalog.root`, a plain Path, so the common case is
    identity. But an `AssetRoot` reaches this module by other routes and
    `dmap.map_names` does a bare ``Path(root)`` that raises on one -- the same
    ``not 'AssetRoot'`` TypeError coviewer logs at startup from a sibling call.
    Unwrapping it here keeps that failure from reaching the map list. A plain
    path is returned unchanged.

    A `Path` is returned BEFORE looking at ``.root`` on purpose: `Path` itself
    has a ``.root`` attribute -- the drive anchor, ``'\\'`` -- so unwrapping a
    Path would hand `dmap` the filesystem root and quietly find zero maps. Only
    a non-path object (the AssetRoot) is unwrapped."""
    if isinstance(root, (str, Path)):
        return root
    inner = getattr(root, "root", None)
    return inner if isinstance(inner, (str, Path)) else root


@lru_cache(maxsize=8)
def integrity_manifest(root: str) -> frozenset:
    """Lowercased logical paths the install's manifest hashes, or empty.

    At the **install root**, not under `ini/` -- looking in `ini/` finds
    nothing and reads as "no manifest", which would silently drop the one
    warning this module exists to raise.  Absent from every official patch
    client, so an empty set is the normal answer rather than a failure.
    """
    p = Path(root) / "integrity.json"
    if not p.is_file():
        return frozenset()
    try:
        rows = json.loads(p.read_text("utf-8", errors="replace"))
    except ValueError:
        return frozenset()
    out = set()
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            f = r.get("file") or r.get("path") or r.get("name")
            if f:
                out.add(_norm(f).lower())
    return frozenset(out)


@lru_cache(maxsize=64)
def _ani_table_cached(root: str, rel: str) -> tuple:
    try:
        return dmapmod.load_ani(Path(root), rel)
    except Exception:                                 # pragma: no cover
        return "", {}


def _ani_table(root: Path, rel: str) -> tuple:
    """``(rel_that_answered, table)`` for one of a DMap's `.ani` references.

    **CACHED, and the cache is what makes the whole-install walk possible.**
    `shared_art_index` runs the closure over every map, and a map's ground
    resolves through indexes like `ani/mapscene-new.ani` -- 1.1 MB and 13,516
    sections. Re-reading and re-parsing that once per map is 470 parses of the
    same file, and the collect panel's "resolve sharing" went from ~13 s to
    minutes the moment the ground was followed. Keyed on `(root, rel)` so two
    installs in one process do not share an answer.

    Statting ``root / rel`` is the obvious implementation and it is wrong on
    any client that ships the index pre-parsed: a DMap names
    ``ani/MapScene.ani`` everywhere, CCO ships ``ani/MapScene.json`` and no
    ``.ani``, and the miss is silent -- every key lands in `unresolved` and
    the map collects with no scenery at all. `dmap.load_ani` owns the
    resolution for all four readers that need it (C-2026-08-09-ani-json-spelling).
    """
    return _ani_table_cached(str(root), str(rel))


def map_names(root=None) -> list:
    r"""Every map this install ships, by DMap stem -- LOOSE OR ARCHIVED.

    **THIS GLOBBED `*.DMap` AND RETURNED ZERO ON 7878**, where all 470 maps
    ship as `map/map/<name>.7z` and not one `.DMap` is loose. `dmap_bytes`
    already knew that and every caller of THIS function did not, so on that
    install:

      * the map list was empty, and
      * `shared_art_index` -- which walks `map_names` to find out which other
        maps reference a file -- returned an EMPTY index, so every collected
        part came back with `shared_with = ()`.

    The second one is the dangerous half. An empty `sharedWith` does not look
    like a broken query; it looks like a clean answer, and the whole point of
    that flag is to say "staging this file changes other maps too". A map
    collected on 7878 asserted that nothing it carried was shared with
    anything, on an install where the `.ani` index and most tiles are shared
    by construction.

    Both spellings are folded to one stem list, and the stem is the map's
    identity either way -- `dmap_bytes` decides which of the two to read.
    """
    r = Path(root) if root is not None else Path(coroot.game_root())
    d = r / "map" / "map"
    return sorted({p.stem for p in d.glob("*.DMap")}
                  | {p.stem for p in d.glob("*.7z")})


def dmap_bytes(root, name: str) -> tuple:
    """`(logical path, bytes)` for one map's `.DMap`, loose OR in its archive.

    **ON 7878 THERE IS NOT ONE LOOSE `.DMap`.** All 323 maps ship as
    `map/map/<name>.7z`, so every path in this module that stated a
    `.DMap` and asked the filesystem about it answered "no such map" for
    the whole install -- which is what "Collect this map" did for every
    map on it.

    The logical path is returned unchanged, because that is the map's
    identity and what every consumer keys on; only the BYTES come from
    somewhere else. `dmap.archive_bytes` verifies its output against the
    CRC32 the archive carries, so this returns correct bytes or none --
    never plausible wrong ones. `mapedit.MapArt.dmap_source` has read maps
    this way all along; this is the same primitive, reached by the
    collector too.
    """
    root = Path(root)
    f = root / "map" / "map" / f"{name}.DMap"
    if f.is_file():
        return f, f.read_bytes()
    a = root / "map" / "map" / f"{name}.7z"
    if a.is_file():
        try:
            return f, dmapmod.archive_bytes(a)
        except Exception:                                 # noqa: BLE001
            return f, None
    return f, None


def _closure(root: Path, name: str) -> tuple:
    """(rels_by_role, effects, missing, ani_keys) -- the raw resolution.

    `ani_keys` is `{index rel: {keys this map asks that index for}}`, which is
    what `mapsnip` needs to cut the index down to this map. Returned rather
    than recomputed because the walk that knows the keys is this one, and a
    second walk that had to agree with it is a second walk that can disagree.
    """
    f, raw = dmap_bytes(root, name)
    if raw is None:
        raise FileNotFoundError(f"no such map: {f} (and no readable archive "
                                f"beside it)")
    d = dmapmod.parse(f, want_cells=False, verify=False, data=raw)

    by_role: dict[str, list] = {r: [] for r in ROLES}
    by_role["dmap"].append(_norm(f.relative_to(root)))
    if d.puzzle_path:
        by_role["puzzle"].append(_norm(d.puzzle_path))

    seen_ani: dict[str, dict] = {}
    art: list[str] = []
    missing: list[str] = []
    effects: list[str] = []
    ani_keys: dict[str, set] = {}

    def take(L):
        """Follow one layer record out to whatever file it names."""
        # An EFFECT record names an effect, not a file -- see `gather_map_parts`.
        if L.get("name"):
            effects.append(L["name"])
            return
        rel = L.get("path")
        if not rel:
            return
        shape = L.get("shape")
        if shape == "scene":
            # `map/Scene/<name>.scene`. 41 distinct files across the corpus,
            # on 69 maps, and every one of them resolves.
            by_role["scene"].append(_norm(rel))
            return
        if shape == "sound":
            # `sound/<name>.wav`. Positional audio -- the braziers on the
            # torch maps are the loudest example, at 268 placements.
            by_role["sound"].append(_norm(rel))
            return
        key = L.get("key")
        if not key:
            return
        rel = _norm(rel)
        if rel not in seen_ani:
            # Keyed by what the DMap ASKED FOR, so two layers naming the same
            # index share one entry; the part carries what ANSWERED, so the
            # collected entry names a file this install actually has. When
            # nothing answered, the declared path is kept -- an index that is
            # simply absent belongs in `unread`, not silently renamed.
            got_rel, table = _ani_table(root, rel)
            seen_ani[rel] = (got_rel or rel, table)
            by_role["ani"].append(got_rel or rel)
        ani_rel, table = seen_ani[rel]
        ani_keys.setdefault(ani_rel, set()).add(key)
        got = table.get(key)
        if got:
            art.extend(_norm(g) for g in got)
        else:
            missing.append(f"{ani_rel}#{key}")

    # BOTH RECORD LISTS. v1006 writes a second one after the plane groups,
    # and it is not a tail or a duplicate: it holds 24,524 more covers and
    # every one of the 10,632 ANIMATED effects the Map Editor draws. See
    # `ROLES` for what following only the first list was costing.
    for L in d.layers or ():
        take(L)
    for L in d.late_layers or ():
        take(L)

    # -- THE BACKGROUND PLANES ---------------------------------------------
    #
    # A third record list, after the layers: the group array, whose tag-8
    # items are the backdrop planes. Each names its OWN `.pul` -- and it is
    # NOT the one `puzzle_path` names. `sary02_new` declares
    # `map/PuzzleSave/sary02.pux` as its puzzle and its two planes are
    # `map/puzzle/sary02.pul` and `map/puzzle/sary02-bg01.pul`; collecting
    # the first and not the other two gave a map that opened, listed its
    # layers and drew its ground at 44x23 slots where the original draws a
    # backdrop at 88x46. That is the map with its sky missing, and it is
    # exactly the kind of difference that survives a collect looking fine.
    #
    # Caught by rendering the collected map beside the original rather than
    # by reading the closure: /api/mapedit/map came back with `backdrops: []`
    # against two. See tests/test_map_roundtrip.py.
    for item in (d.extra_items or ()):
        q = item.get("path")
        if q:
            by_role["plane"].append(_norm(q))
        # AND THE GROUP ARRAY CARRIES EFFECTS TOO -- 232 tag-19 records
        # across the corpus, decoded with the same `shape`/`name` a layer
        # record has. `2019xmas_new` keeps four `cloudmist1` placements
        # here and none in either layer list, so a map's whole animated
        # weather can live in the one list nothing was reading.
        if item.get("shape") == "effect" and item.get("name"):
            effects.append(item["name"])

    # The sidecar. `mapedit.cover_tints` / `puzzle_tints` read it for the
    # per-cover and ground colour, so a map collected without it stages at
    # full brightness and full opacity -- a different-looking map, with no
    # file missing and nothing to notice.
    od = root / "map" / "map" / f"{name}.OtherData"
    if od.is_file():
        by_role["otherdata"].append(_norm(od.relative_to(root)))

    # The GROUND, which is not in any layer record -- see `ground_closure`.
    gkeys, gart = ground_closure(
        root, [q for q in by_role["puzzle"] + by_role["plane"]])
    for rel, keys in gkeys.items():
        if rel not in ani_keys:
            by_role["ani"].append(rel)
        ani_keys.setdefault(rel, set()).update(keys)
    art.extend(gart)

    by_role["art"] = art
    return by_role, effects, missing, ani_keys


@lru_cache(maxsize=4)
def shared_art_index(root: str) -> dict:
    """``rel -> (map names that reference it)`` across every map.

    Walks all the DMaps once, which is why it is cached: the answer is a
    property of the install, and `stage` needs it for every entry.  This is
    what turns "shared art might collide" into a list of the maps that
    would actually be affected.
    """
    r = Path(root)
    out: dict[str, set] = {}
    for nm in map_names(r):
        try:
            by_role, _eff, _m, _k = _closure(r, nm)
        except Exception:                             # pragma: no cover
            continue
        # `otherdata` is deliberately NOT here: it is per-map by construction
        # (`<name>.OtherData`), so it can never be shared and asking would
        # only make the index bigger.
        for role in ("puzzle", "plane", "ani", "art", "scene", "sound"):
            for rel in by_role.get(role, ()):
                out.setdefault(rel.lower(), set()).add(nm)
    return {k: tuple(sorted(v)) for k, v in out.items()}


#: Whole files the closure copies. `ani` is NOT here -- it is CUT, see
#: `map_snippets` -- and `effect` is a name rather than a file.
_FILE_ROLES = ("dmap", "puzzle", "plane", "art", "scene", "sound",
               "otherdata")


def ground_closure(root: Path, pul_rels, assets=None) -> tuple:
    r"""``({ani rel: {keys}}, [art paths])`` for a map's GROUND and BACKDROPS.

    THE HOLE THIS FILLS, AND HOW IT WAS FOUND. The layer records name the
    scenery -- covers, sprites, effects -- and that is what `_closure` walks.
    The GROUND is somewhere else entirely: the `.DMap` names a `.pul`/`.pux`,
    that file names its OWN `.ani`, and its tile values index into that index
    under `Puzzle<N>` keys. None of it appears in a layer record, so a
    collected map carried every sprite on the map and none of the ground
    underneath them.

    It survived the API comparison -- `/api/mapedit/map` matched key for key,
    because the map still KNOWS about its ground -- and died on the pixels:
    the ground tiles came back as 334-byte blank PNGs against 37-89 KB of
    real art. Comparing what a thing says about itself is not comparing the
    thing. See `tests/test_map_roundtrip.py`.

    **A `.pux` and a `.pul` reach their art differently** and both are here
    because both ship: a `.pul` names one `.ani` for the whole surface and
    its tiles index into it, while a `.pux` (TqTerrain) carries a terrain
    TABLE whose every row names its own `.ani` and its own key. `sary02_new`
    is a `.pux` ground with two `.pul` backdrop planes, so it exercises both.

    The readers are the ones `tools/puzzle.py` uses -- `coassets.Pul` and
    `dmap.read_pux_full` -- and not a second decode of either format. A third
    copy is how one of them stays wrong (C-2026-08-09-ani-json-spelling).
    """
    want: dict[str, set] = {}
    art: list[str] = []
    try:
        from coassets import Pul                      # noqa: PLC0415
        from dmap import read_pux_full                # noqa: PLC0415
    except Exception:                                 # noqa: BLE001
        return want, art

    def note(ani_rel: str, keys):
        ani_rel = _norm(ani_rel)
        if not ani_rel.lower().startswith("ani/"):
            ani_rel = "ani/" + Path(ani_rel).name
        # The `.ani` a `.pul` names may be spelled `.json` on this install;
        # `load_ani` is what knows, and it also hands back the table.
        got_rel, table = _ani_table(root, ani_rel)
        rel = got_rel or ani_rel
        for k in keys:
            want.setdefault(rel, set()).add(k)
            for q in (table.get(k) or ()):
                art.append(_norm(q))

    for rel in pul_rels:
        p = root / _norm(rel)
        if not p.is_file():
            continue
        if p.suffix.lower() == ".pux":
            try:
                px = read_pux_full(p)
            except Exception:                         # noqa: BLE001
                px = None
            if not px:
                continue
            # The terrain-row walk lives in `dmap.pux_terrain_refs` -- one
            # home, because `puzzle.py` needs the same answer to RENDER the
            # ground and `mapindex.py` needs it to REPORT on it.
            by_ani: dict[str, set] = {}
            for a, k in dmapmod.pux_terrain_refs(px):
                by_ani.setdefault(a, set()).add(k)
            for a, keys in by_ani.items():
                note(a, keys)
            continue
        try:
            z = Pul.load(p)
        except Exception:                             # noqa: BLE001
            continue
        keys = {"Puzzle%d" % i for i in set(z.tiles) if i >= 0}
        if keys:
            note(z.ani_path, keys)
    return want, art


def map_snippets(root: Path, name: str, by_role: dict, ani_keys: dict,
                 effect_names, assets=None) -> list:
    r"""The CUT parts: shared plaintext tables, reduced to this map.

    Three tables, each shared enough that carrying the whole thing would be
    both waste and collateral damage:

      * the **scenery index** the DMap names. `ani/mapscene-new.ani` is
        1.1 MB and 13,516 sections on 7878, read by 180 maps; `sary02_new`
        uses 161 of them and the cut is 14 KB. Staging the whole file is an
        edit to 179 other maps.
      * the **map registry**, `ini/GameMap.json`. Not shared so much as
        CENTRAL: one file names every map on the install. A collected map
        that does not carry its own rows cannot be OPENED from the library,
        because `MapEditor.rows()` lists the registry and not the directory
        -- and a map the editor does not know exists is indistinguishable
        from one that failed to collect. A map is reused under several
        DocumentIds, so every row travels (`sary02_new` has two).
      * `ini/3DEffect.ini`, for effects whose definition lives there. 2,595
        sections on 7878; a map names a handful.

    `tools/mapsnip.py` owns the cutting and says why it is textual rather
    than a re-emit. Nothing here is marked shared: a cut is this map own
    file and is complete for it.
    """
    out: list[MapPart] = []

    # -- the scenery index/indices -----------------------------------------
    for rel in by_role.get("ani", ()):
        keys = ani_keys.get(rel) or set()
        raw = mapsnip.read_logical(assets, root, rel)
        blob = b""
        if raw is not None and keys:
            blob, _absent = mapsnip.snippet_for(rel, raw, keys)
        if blob:
            out.append(MapPart(
                role="ani", rel=rel, data=blob, apply="merge",
                merge_format=("json-keys" if rel.lower().endswith(".json")
                              else "ini-sections"),
                merge_keys=tuple(sorted(str(k) for k in keys))))
        else:
            # Nothing to cut from, or nothing came out. Name the whole file,
            # which is what the collector did before cutting existed: a
            # snippet we could not produce must not become a part that is
            # simply absent.
            out.append(MapPart(role="ani", rel=rel))

    # -- the registry rows --------------------------------------------------
    try:
        _rel, rows = dmapmod.load_gamemap(assets if assets is not None else root)
    except Exception:                                    # noqa: BLE001
        rows = []
    mine = mapsnip.rows_for_map(rows, name)
    if mine:
        out.append(MapPart(
            role="config", rel=mapsnip.GAMEMAP_JSON,
            data=mapsnip.gamemap_snippet(mine), apply="merge",
            merge_format="json-rows",
            merge_keys=tuple(sorted(str(r.get("DocumentId")) for r in mine))))

    # -- 3DEffect.ini, for the names that live there ------------------------
    names = [n for n in dict.fromkeys(effect_names) if n]
    if names:
        raw = mapsnip.read_logical(assets, root, "ini/3DEffect.ini")
        if raw:
            blob, _absent = mapsnip.ini_section_snippet(raw, names)
            if blob.strip():
                out.append(MapPart(
                    role="config", rel="ini/3DEffect.ini", data=blob,
                    apply="merge", merge_format="ini-sections",
                    merge_keys=tuple(sorted(names))))
    return out


def effect_art(root: Path, effect_names, assets=None) -> tuple:
    r"""``(parts, records)`` -- the FILES an effect draws, and its definition.

    An `effect` part records a NAME, which is the right thing to record and
    is not a file, so a collected map used to carry its animated layers as
    strings -- 22 of them on `2024tsf_new`, drawing nothing anywhere the name
    did not already resolve.

    `records` is ``{name: the resolved definition}``: `EffectDef` and its
    `EffectLayer`s as plain data. That is the half a plaintext snippet cannot
    carry. 338 of the 436 effect names the corpus maps use resolve ONLY
    through `ini/c3.wdb`'s EFFE table, which is binary; the 41 that live in
    `ini/3DEffect.ini` also get their section cut (`map_snippets`), so the
    plaintext form travels as plaintext wherever there is one.

    Resolution is `map_fx=True`, the MAP effect population. Without it a name
    like `lhd_lw3` does not resolve at all -- it is not in `3DEffect.ini`.
    """
    import dataclasses
    parts: list[MapPart] = []
    records: dict = {}
    names = [n for n in dict.fromkeys(effect_names) if n]
    if not names:
        return parts, records
    try:
        import effects as effectsmod
        db = effectsmod.EffectDB(root, assets=assets)
    except Exception:                                    # noqa: BLE001
        return parts, records
    # `with db`, not `with EffectDB(...)`, so the construction-failure return
    # above keeps its own shape.  `assets` IS OFTEN None -- `effect_records`
    # and `gather_map_parts` both default it -- and then this owns the install
    # it just opened.  Where one WAS handed in, `close()` leaves it alone.
    with db:
        seen = set()
        for nm in names:
            try:
                e = db.resolve(nm, map_fx=True)
            except Exception:                            # noqa: BLE001
                e = None
            if e is None:
                continue
            try:
                records[nm] = dataclasses.asdict(e)
            except Exception:                            # noqa: BLE001
                pass
            for L in e.layers:
                for rel in (getattr(L, "mesh_path", ""),
                            getattr(L, "texture_path", "")):
                    rel = _norm(rel or "")
                    if not rel or rel.lower() in seen:
                        continue
                    seen.add(rel.lower())
                    parts.append(MapPart(role="effectart", rel=rel))
    return parts, records


def effect_records(name: str, root=None, assets=None) -> dict:
    """The resolved definition of every effect one map names.

    Separate from `gather_map_parts` because it is DATA and not a part: it
    goes on the entry, beside the names, so an entry says what its animated
    layers ARE and not only what they are called.
    """
    r = Path(root) if root is not None else Path(coroot.game_root())
    _by_role, effects, _missing, _keys = _closure(r, name)
    return effect_art(r, effects, assets=assets)[1]


def gather_map_parts(name: str, root=None, *,
                     with_shared: bool = True, assets=None,
                     with_effect_art: bool = True) -> list:
    """Every file a map needs, each flagged with what makes it risky.

    `with_shared=False` skips the whole-install walk when the caller only
    wants the file list -- the flags are the expensive half, not the
    closure. `with_effect_art=False` skips building the effect database,
    which is the other expensive half and is exactly what the shared-art
    walk does not want, since it runs this once per map over the install.
    """
    r = Path(root) if root is not None else Path(coroot.game_root())
    by_role, effects, missing, ani_keys = _closure(r, name)
    covered = integrity_manifest(str(r))
    shared = shared_art_index(str(r)) if with_shared else {}

    parts: list[MapPart] = []
    emitted = set()
    for role in _FILE_ROLES:
        for rel in by_role.get(role, ()):
            if rel.lower() in emitted:
                continue
            emitted.add(rel.lower())
            others = tuple(m for m in shared.get(rel.lower(), ())
                           if m.lower() != name.lower())
            parts.append(MapPart(role=role, rel=rel, shared_with=others,
                                 integrity=rel.lower() in covered))
    # SNIPPETS AND EFFECT ART GET THE SHARED LOOKUP TOO, and until 2026-09-16
    # they did not.
    #
    # `shared_with` was assigned only in the `_FILE_ROLES` loop above, so when
    # `ani` became a CUT rather than a copied file it moved out of that loop
    # and stopped being flagged -- while `shared_art_index` went on indexing
    # `ani` rels perfectly well. Measured over 12 maps on 7878: **35 of 35
    # `ani` parts are in the shared index and NOT ONE carried the flag**, the
    # worst being `ani/MapScene.ani` at 208 other maps.
    #
    # `core/collection.py:1699` gates its shared-art decision AND its
    # byte-identity skip on that flag, so for the single most-shared file in
    # the install neither ran. `core/swapsides.displacement_note()` was
    # meanwhile rendering `covered=True` for `ani` -- "the check said this is
    # safe" standing in for "the check does not run here", which that
    # function's own docstring calls opposite facts.
    #
    # Done as one pass over everything rather than repeated per producer:
    # three producers each remembering to look the flag up is three places for
    # the next one to be forgotten, which is how this happened.
    def _with_shared(q):
        if q.shared_with or not shared:
            return q
        others = tuple(m for m in shared.get((q.rel or "").lower(), ())
                       if m.lower() != name.lower())
        return replace(q, shared_with=others) if others else q

    for q in map_snippets(r, name, by_role, ani_keys, effects, assets=assets):
        parts.append(_with_shared(q))
    if with_effect_art:
        for q in effect_art(r, effects, assets=assets)[0]:
            if q.rel.lower() in emitted:
                continue
            emitted.add(q.rel.lower())
            parts.append(_with_shared(q))
    for e in dict.fromkeys(effects):
        parts.append(MapPart(role="effect", rel=e))
    for m in dict.fromkeys(missing):
        parts.append(MapPart(role="art", rel=m, missing=True))
    return parts


def summarise(parts) -> dict:
    counts: dict[str, int] = {}
    for p in parts:
        counts[p.role] = counts.get(p.role, 0) + 1
    return {"counts": counts,
            "files": sum(1 for p in parts if p.role != "effect" and not p.missing),
            "snippets": sum(1 for p in parts if p.snippet),
            "snippetBytes": sum(len(p.data) for p in parts if p.snippet),
            "shared": sum(1 for p in parts if p.shared),
            "integrity": sum(1 for p in parts if p.integrity),
            "missing": sum(1 for p in parts if p.missing)}


def default_reader(root):
    """A ``read(logical) -> bytes | None`` that sees inside the archives.

    This is the whole reason `collect_map` takes a reader rather than
    touching the filesystem: on 5517 every one of `09Christmas02`'s 56 art
    files is inside `c3.wdf`/`data.wdf` and none is loose, so a collector
    that stats paths silently produces a map with no scenery.
    """
    from coassets import AssetRoot
    ar = AssetRoot(Path(root))

    def read(rel: str):
        try:
            return ar.read(rel)
        except Exception:
            pass
        p = Path(root) / rel
        if p.is_file():
            return p.read_bytes()
        # THE `.DMap` ITSELF LIVES IN A `.7z` ON THE MODERN INSTALLS, and
        # `AssetRoot` does not look inside those -- it knows the `.wdf`/TPD
        # containers. Without this the collector resolved every OTHER part
        # of a map and then had nothing to collect it under.
        q = _norm(rel).lower()
        if q.startswith("map/map/") and q.endswith(".dmap"):
            _f, raw = dmap_bytes(root, Path(rel).stem)
            return raw
        return None
    return read


def collect_map(col, name: str, root=None, *, read=None,
                server: str = "", with_shared: bool = True,
                keep_only=None, assets=None) -> dict:
    """Add one map to a `Collection`, whole, with its risks recorded.

    The DMap is the entry's identity -- it is what the map *is*, the way a
    mesh is what a model is -- and everything else rides as a part under
    `collection.MAP_ROLES`, each keeping the logical path that makes it
    reachable. The shared and integrity flags measured here are written onto
    the parts so `stage` can act on them without re-deriving them (and
    without `core/` needing to import any of this).

    `keep_only` narrows the closure to a chosen set of logical paths -- the
    UI's role and shared-art toggles arrive here. The DMap is never in it
    and never dropped: it is what the entry *is*, so a map without it is
    not a smaller map, it is not a map.

    Unresolved keys are recorded on the entry rather than dropped: the same
    map name resolves differently on different clients, and a short closure
    that looks complete is the failure mode worth refusing. What a caller
    *chose* to leave behind is recorded separately, as `omitted` -- a gap
    you asked for and a gap you did not are different facts.
    """
    r = Path(root) if root is not None else Path(coroot.game_root())
    read = read or default_reader(r)
    parts = gather_map_parts(name, r, with_shared=with_shared, assets=assets)

    head = next((p for p in parts if p.role == "dmap"), None)
    if head is None:
        raise FileNotFoundError(f"{name}: no DMap")
    dmap_bytes = read(head.rel)
    if not dmap_bytes:
        raise FileNotFoundError(f"{name}: could not read {head.rel}")

    allow = None if keep_only is None else {str(r).lower() for r in keep_only}
    payload, unread, missing, omitted = [], [], [], []
    for p in parts:
        if p.role == "dmap":
            continue
        if p.missing:
            missing.append(p.rel)
            continue
        if p.role == "effect":
            continue                      # named, not a file: recorded below
        if allow is not None and p.rel.lower() not in allow:
            omitted.append(p.rel)
            continue
        # A SNIPPET BRINGS ITS OWN BYTES. `p.rel` is still the logical path --
        # that is where it stages, and what makes the reference in the DMap
        # resolve -- but its CONTENT is the cut `map_snippets` made, not the
        # install's 1.1 MB original. Reading the path here would silently
        # collect the whole shared table and undo the cut.
        blob = p.data if p.data is not None else read(p.rel)
        if not blob:
            unread.append(p.rel)
            continue
        payload.append((p.role, Path(p.rel).name, p.rel, blob, p.record()))

    # Provenance for the install these bytes came from. A map is collected
    # from an install exactly as a mesh is, so it records the same thing --
    # leaving one collector unstamped is how a field ends up meaning
    # "sometimes".
    try:
        import provenance as _prov
        _stamp = _prov.optional_stamp(root, "mapparts.py")
    except Exception:                                     # pragma: no cover
        _stamp = None
    entry = col.add(category="Maps", name=name, mesh_bytes=dmap_bytes,
                    mesh_name=Path(head.rel).name, server=server,
                    source_mesh=head.rel, swap_for=head.rel, parts=payload,
                    provenance=_stamp)
    entry["integrity"] = bool(head.integrity)
    eff = [p.rel for p in parts if p.role == "effect"]
    if eff:
        entry["effects"] = eff
        # WHAT THE NAMES MEAN, not just what they are called. 338 of the 436
        # effect names the corpus maps use resolve only through the binary
        # EFFE table in `ini/c3.wdb`; there is no plaintext section to cut for
        # those, so the resolved `EffectDef` -- layers, blend modes, offsets,
        # intervals, and the ids its art hangs off -- rides as data. The files
        # themselves are the `effectart` parts.
        try:
            defs = effect_records(name, r, assets=assets)
        except Exception:                                # noqa: BLE001
            defs = {}
        if defs:
            entry["effectDefs"] = defs
    if missing:
        entry["unresolved"] = missing
    if unread:
        entry["unread"] = unread
    if omitted:
        # Deliberate, so it reads differently from `unresolved`/`unread`:
        # this map is incomplete because you said so, and `stage` will draw
        # the rest from the install's own copies.
        entry["omitted"] = omitted
    return entry


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    coroot.add_root_argument(ap)
    ap.add_argument("map", nargs="?", help="map name (DMap stem)")
    ap.add_argument("--list", action="store_true", help="list every map")
    ap.add_argument("--fast", action="store_true",
                    help="skip the shared-art walk")
    a = ap.parse_args(argv)
    root = coroot.root_from_args(a)
    if a.list or not a.map:
        for n in map_names(root):
            print(n)
        return 0
    parts = gather_map_parts(a.map, root, with_shared=not a.fast)
    s = summarise(parts)
    print(f"{a.map}: {s['files']} files, {s['counts'].get('effect', 0)} effects, "
          f"{s['shared']} shared, {s['integrity']} integrity-covered, "
          f"{s['missing']} unresolved")
    for p in parts:
        flag = ("  [integrity]" if p.integrity else "") + \
               (f"  [shared with {len(p.shared_with)}]" if p.shared else "") + \
               ("  [UNRESOLVED]" if p.missing else "")
        print(f"  {p.role:7} {p.rel}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
