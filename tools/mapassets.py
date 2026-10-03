#!/usr/bin/env python3
r"""
mapassets.py -- find map satellites, and say where each one can actually go.

    py -3 tools/mapassets.py --covers                # every cover, with its
                                                     # placeable target maps
    py -3 tools/mapassets.py --covers --key wall18a.tga
    py -3 tools/mapassets.py --shared                # art used by >1 map
    py -3 tools/mapassets.py --covers --json out/mapassets.json

WHAT THIS IS FOR
----------------
The owner asked for "a Map Asset Viewer page that helps people find, and
collect map satellite files", and then for a UI to use collected assets in
the Map Editor. This is the data layer for both, and it is a PROJECTION --
`tools/mapparts.py` already owns the closure (`_closure`, `shared_art_index`)
and its answer is measured exact. **There is no second closure here**; adding
one is the mistake `portageplan` names in its own docstring and it applies
the same way.

THE ONE THING THIS ADDS, AND IT IS THE THING T5 CANNOT SHIP WITHOUT
--------------------------------------------------------------------
**A cover cannot be placed on an arbitrary map.** A COVER record carries a
`key` (``wall18a.tga``) that the client resolves through an INDEX the map
loads -- `ani/mapscene-new.json` and friends. Place a cover whose key only
lives in `ani/ninja.json` onto a map that does not load `ninja.json` and the
record is valid, the file re-parses, the editor draws it from its own
resolution... **and the client shows nothing.** It fails in exactly the place
nobody is looking.

So every cover here carries `placeable_on`: the maps that load an index which
resolves its key AND can hold a cover record at all (`mapcover.capability`).
MEASURED on the owner's install, 2026-09-22:

    4 cover-bearing maps, 82 distinct keys, 207 records
    all four load ani/mapscene-new.json  -> 6 maps can draw those keys
    ani/ZF.json, ani/cartoonwater.json   -> 3 maps each
    ani/ninja.json, ani/n-newplain.json  -> ONE map each

**Zero of the 82 keys is used by more than one map today.** That is not the
same as "cannot be", and the difference is the whole feature: the index says
where a key CAN go, the corpus only says where it currently IS.

THE SPELLING TRAP THIS INHERITS AND DOES NOT REPEAT
-----------------------------------------------------
A DMap names ``ani/MapScene.ani``; CCO ships ``ani/MapScene.json`` and no
`.ani` at all, **and the miss is silent** -- every key lands unresolved and a
map "collects" with no scenery (C-2026-08-09-ani-json-spelling). Resolution
goes through `mapparts._ani_table`, which owns it. Nothing here filters on a
file extension: while writing this I filtered `shared_art_index` for `.ani`,
got **0 of 16,327 entries**, and would have reported "no shared index" --
the real answer is `ani/mapscene.json`, shared by 93 maps. **A filter's zero
is not a measurement.**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmap                                                  # noqa: E402
import mapcover                                              # noqa: E402
import mapparts                                              # noqa: E402


class CoverAsset:
    """One distinct cover, wherever it is drawn.

    `sources` is what `mapedit.stage_covers`'s `add` consumes: a (map, record
    index) pair naming a record that already exists, because `add` COPIES and
    never authors -- and it copies WITHIN one map, so only a source whose map
    is the open map can be handed to it. `placeable_on` is what a palette may
    offer, and it is decided by the INDEX a map loads, not by `sources`: the
    two can disagree on every entry (luckytree01_new, 2026-09-25: 72 of 82
    usable covers have no source there).
    """

    __slots__ = ("key", "path", "size", "frame_interval", "sources",
                 "indexes", "placeable_on", "records")

    def __init__(self, key):
        self.key = key
        self.path = ""
        self.size = None
        self.frame_interval = None
        self.sources = []          # [(map, record index)]
        self.indexes = set()       # index rels that resolve this key
        self.placeable_on = []
        self.records = 0

    def as_dict(self) -> dict:
        return {
            "key": self.key, "path": self.path, "size": self.size,
            "frameInterval": self.frame_interval, "records": self.records,
            "sources": [{"map": m, "index": i} for m, i in self.sources],
            "maps": sorted({m for m, _ in self.sources}),
            "indexes": sorted(self.indexes),
            "placeableOn": sorted(self.placeable_on),
            "placeableCount": len(self.placeable_on),
        }


def map_indexes(root, name) -> list:
    """The index files a map loads, as `mapparts` resolves them."""
    try:
        by_role, _eff, _m, _keys = mapparts._closure(Path(root), name)
    except Exception:
        return []
    return [str(r) for r in by_role.get("ani", ())]


def index_has_key(root, rel, key) -> bool:
    """Does `rel` resolve `key`? Through `mapparts._ani_table`, never a stat.

    The `.ani` / `.json` spelling difference is exactly the silent miss this
    module's docstring records, and `_ani_table` is where that is settled.
    """
    try:
        _answered, table = mapparts._ani_table(Path(root), rel)
    except Exception:
        return False
    if not table:
        return False
    k = str(key or "").lower()
    try:
        return any(str(t).lower() == k for t in table)
    except TypeError:                                        # pragma: no cover
        return False


def cover_catalogue(root, *, progress=None) -> dict:
    """`{key: CoverAsset}` over every map this install ships.

    Walks the maps once. `placeable_on` is computed per key from the index
    that carries it crossed with the maps that both load that index and can
    hold a cover record -- never from "which maps use it today", which is a
    strictly smaller and differently-shaped answer.
    """
    root = str(root)
    out: dict = {}
    map_idx: dict = {}
    capable: dict = {}

    names = dmap.map_names(root)
    for n, nm in enumerate(names):
        if progress and n % 25 == 0:
            progress(nm, n, len(names))
        try:
            d, _how = dmap.parse_map(root, nm, want_cells=False)
        except Exception:
            continue
        if d is None:
            continue
        capable[nm] = mapcover.capability(d)["capable"]
        if not d.late_layers:
            continue
        for i, rec in enumerate(d.late_layers):
            if rec.get("shape") != "cover":
                continue
            key = str(rec.get("key") or "")
            a = out.get(key.lower())
            if a is None:
                a = out[key.lower()] = CoverAsset(key)
                a.path = str(rec.get("path") or "")
                a.size = rec.get("size")
                a.frame_interval = rec.get("frame_interval")
            a.records += 1
            a.sources.append((nm, i))
        map_idx[nm] = map_indexes(root, nm)

    # Which index resolves each key, then which maps load that index.
    all_idx = sorted({r for rels in map_idx.values() for r in rels})
    for a in out.values():
        for rel in all_idx:
            if index_has_key(root, rel, a.key):
                a.indexes.add(rel)
        a.placeable_on = placeable_maps(
            a.indexes, map_idx, capable, {m for m, _ in a.sources})
    return out


def placeable_maps(indexes, map_idx: dict, capable: dict, drawn_on) -> list:
    """Maps a cover with these `indexes` may be placed on.

    Two conditions, and BOTH are required:

    * the map loads an index that resolves the key -- otherwise the record is
      valid, the file re-parses, the editor draws it, **and the client shows
      nothing**; and
    * the map can hold a cover record at all (`mapcover.capability`) -- a
      v1004 map cannot, whatever it loads.

    **With no identified index the answer is the maps that ALREADY draw it,
    not the empty list.** Those are the only ones that can be vouched for,
    and an empty list would read as "this cover can go nowhere" when what is
    true is "I could not establish where". Same shape as the `.get()` trap in
    `npcart.audit`: an empty answer that reads as the reassuring one.
    """
    if not indexes:
        return sorted(drawn_on)
    return sorted(m for m, rels in map_idx.items()
                  if capable.get(m) and any(r in indexes for r in rels))


def palette_reason(cap: dict, row: dict, map_name: str,
                   min_version: int = 0) -> tuple:
    """`(usable, why)` for one cover against one open map.

    THE FOUR STATES ARE DELIBERATELY DISTINCT and none of them is silence:

    * this EDITOR cannot write a cover to the map -- version, not art, and
      a limit of the write path, not of the map (see below);
    * the map is in this cover's `placeableOn` -- usable;
    * the cover's index was never identified -- **UNKNOWN, not impossible**,
      and the wording says so, because a user told "no" about something that
      might work will stop looking;
    * the map loads no resolving index -- the record would be valid and the
      client would draw nothing, which is the failure this whole field exists
      to prevent, so the reason names the indexes that DO resolve it.

    Pure, so the palette's decisions can be tested without a server.
    """
    if not cap.get("capable"):
        # THE LIMIT IS THE EDITOR'S, NOT THE MAP'S. Pre-1006 maps carry their
        # covers in the FIRST layer table (17,397 of them on the live install,
        # measured 2026-09-24 -- HANDOFF-2026-09-24 §5.3); this editor writes
        # only the v1006 second record list. The earlier wording said the map
        # "cannot hold a cover record at all", which is false and sends a user
        # away from a map that is full of them. `mapcovers.js` says the same
        # sentence; change both or neither.
        return False, (
            "this editor cannot yet write a cover to this map: it is version "
            "%s, and the editor writes only the second record list that v%d "
            "and later carry. The map itself may already hold covers in its "
            "first layer table."
            % (cap.get("version"), min_version))
    if map_name in (row.get("placeableOn") or ()):
        return True, ""
    if not (row.get("indexes") or ()):
        return False, ("the index that resolves this key was not identified, "
                       "so whether this map can draw it is UNKNOWN -- not "
                       "proven impossible")
    return False, ("this map loads no index that resolves the key; the record "
                   "would be valid and the client would draw nothing. "
                   "Resolved by: %s" % ", ".join(row["indexes"]))


def shared_assets(root) -> list:
    """Art used by more than one map: `[(rel, [maps])]`, widest first.

    Straight from `mapparts.shared_art_index` -- the browse half of the
    Viewer, and the half that tells a user an edit here changes several maps.
    """
    idx = mapparts.shared_art_index(str(root))
    rows = [(rel, list(maps)) for rel, maps in idx.items() if len(maps) > 1]
    rows.sort(key=lambda r: (-len(r[1]), r[0]))
    return rows


def _report_covers(cat: dict, key_filter: str = "") -> None:
    rows = sorted(cat.values(), key=lambda a: (-a.records, a.key))
    if key_filter:
        rows = [a for a in rows if key_filter.lower() in a.key.lower()]
    print("%-24s %7s %6s %-9s %s"
          % ("cover key", "records", "maps", "placeable", "indexes"))
    for a in rows:
        print("  %-22s %7d %6d %-9d %s"
              % (a.key[:22], a.records, len({m for m, _ in a.sources}),
                 len(a.placeable_on),
                 ", ".join(sorted(a.indexes)) or "(index not identified)"))
    unknown = [a for a in rows if not a.indexes]
    print("\n%d distinct cover(s), %d record(s)."
          % (len(rows), sum(a.records for a in rows)))
    if unknown:
        print("%d have no identified index: where they can go is UNKNOWN, "
              "so only the maps already drawing them are offered."
              % len(unknown))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Find map satellites and where each can be placed.")
    ap.add_argument("--root", default=None,
                    help="install to read (default: the configured root)")
    ap.add_argument("--covers", action="store_true")
    ap.add_argument("--shared", action="store_true")
    ap.add_argument("--key", default="", help="filter covers by key substring")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = args.root
    if not root:
        import coroot                                        # noqa: PLC0415
        found = coroot.config_root()
        root = found[0] if found else None
    if not root or not Path(root).is_dir():
        print("no install: pass --root", file=sys.stderr)
        return 2

    def progress(nm, i, n):
        if not args.quiet:
            print("  ...%-24s %d/%d" % (nm[:24], i, n), file=sys.stderr)

    payload: dict = {"root": str(root)}
    if args.covers or not args.shared:
        cat = cover_catalogue(root, progress=progress)
        _report_covers(cat, args.key)
        payload["covers"] = [a.as_dict() for a in cat.values()]
    if args.shared:
        rows = shared_assets(root)
        print("\n%-52s %s" % ("shared art", "maps"))
        for rel, maps in rows[:40]:
            print("  %-50s %d" % (rel[:50], len(maps)))
        print("\n%d asset(s) used by more than one map." % len(rows))
        payload["shared"] = [{"rel": r, "maps": m, "mapCount": len(m)}
                             for r, m in rows]
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2), "utf-8")
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
