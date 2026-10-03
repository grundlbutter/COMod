#!/usr/bin/env python3
r"""mapfx.py -- the map's ANIMATED EFFECTS: placement, definition, extent.

WHAT THIS IS, AND WHY IT WAS NOWHERE
------------------------------------
The owner, looking at the new INTERACTIVE layer: *"I have a distinct feeling
that some of these additional features could be animated features."*

They are, and the records say so literally. The `.DMap`'s v1006 SECOND record
list (`DMap.late_layers`) carries **tag-19 EFFECT records: 10,172 placements
across 115 maps, 377 distinct names** -- and nothing drew or even named them,
because every consumer read `d.layers` and stopped.

    frame_interval   33 ms on 324 of 377 names   (~30 fps)
    loop_time        99999999                    (ambient, endless)

THREE FILES, AND THE FIRST TWO GUESSES WERE THE WRONG ONES
-----------------------------------------------------------
A map effect record names an effect. Resolving that name took three tries,
and the measurements are here so nobody repeats the first two:

    ini/3DEffect.ini       1 of 377 distinct    0.3%   WRONG NAMESPACE
    ini/C3DMapEffect.lua 153 of 377 distinct   40.6%   size/cull only
    ini/c3.wdb `EFFE`    325 of 377 distinct   86.2%   THE DEFINITIONS
                        9,915 of 10,172 placements     97.5%

**`3DEffect.ini` is the character/action effect namespace and map effects are
not in it.** Finding 1 of 377 there and reporting "the namespace is missing"
was the wrong conclusion from a right measurement -- it was not missing, it
was in a table nobody had looked in. **The denominator control matters and was
run**: `zzz_not_an_effect`, `cloudmist999` and `__nope__` all correctly fail to
resolve, so this is not a table that matches everything.

Both percentages are reported deliberately. **The 52 unresolved names are the
RARE ones** -- `Christmas_light1`, `floorwat`, `fulgurite`, `lightcage` -- so
86.2% understates what a renderer would draw and 97.5% hides that 52 names
have no definition at all. Either number alone erases the finding.

THE WHOLE CHAIN RESOLVES, AND IT IS ALREADY-WRITTEN CODE
--------------------------------------------------------
    map late record -> effect name -> EFFE record -> layer -> texture id
                    -> RSDB -> file on disk

`core/wdb.py` walks `c3.wdb`'s 34 sections; `core/dbc.py`'s `read_effe()`
decodes `EFFE` (verified against 5517's 3,391 records and 6090's 4,483) and
reads all 14,378 of 7878's. Measured end to end: **1,163 of 1,163 texture ids
resolve to a path (100%)**, and every sampled file is on disk.

So this module does no format work. It joins tables that already parse.

WHAT `C3DMapEffect.lua` ACTUALLY IS -- NOT a definition file
------------------------------------------------------------
7,296 bytes of `["name"] = { r=..., dz=... }`, and its own `GetSize` says what
they are: **the bounding cylinder used to CULL the effect against the screen**
-- radius `r` and height `dz`, in game units. Its comments warn that a value
set too small makes the effect vanish abruptly at the screen edge.

The defaults are in the file and are reproduced here rather than invented:
**`r` defaults to 363 and `dz` to 0**, and the file notes `363 = SQRT2*32*8`
-- derived from the tile geometry, not a magic number.

Nothing in this repository read it before this module.

Read-only. Nothing here writes to the game install.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
    sys.path.insert(0, str(_HERE.parent / "core"))

#: `GetSize`'s own fallbacks, from `ini/C3DMapEffect.lua`. A name absent from
#: the table is not an error -- 224 of the 377 map-effect names are absent and
#: the client gives them these.
DEFAULT_R = 363
DEFAULT_DZ = 0

#: `["name"] = { r=<n>, dz=<n> }`. Parsed with a regex rather than a Lua
#: interpreter because the file is one flat literal table and pulling in a Lua
#: runtime to read 158 pairs would be the larger dependency by far. If the
#: file ever grows control flow, this must become a real parse -- the gate
#: asserts the population so that shows up as a drop rather than silently.
_ROW = re.compile(r'\["([^"]+)"\]\s*=\s*\{([^}]*)\}')
_NUM = re.compile(r'\b(r|dz)\s*=\s*(-?\d+)')


#: **EFFECT ORIGINS ARE IN A 48-UNITS-PER-CELL SPACE, NOT THE COVER SPACE.**
#:
#: A cover's origin is painted-image pixels. An effect's is not: on
#: `gsjx03_new` the origins run x 15,910..27,986 against a map 17,920 px wide,
#: and **87.7% of all effect origins fall outside the map's pixel rect**. They
#: are not cell coordinates either -- 27,986 against a 680-cell grid.
#:
#: Bounded by an OVERFLOW test over 10,172 placements on 115 maps with **60
#: distinct grid sizes**, so it is not a coincidence of one map's dimensions:
#:
#:     divisor   overflow      median density   max density
#:        16       10,026
#:        32        4,528
#:        44          138
#:        48            0          0.908           0.981   <- TIGHTEST FIT
#:        56            0          0.777
#:        64            0          0.679
#:       128            0          0.340
#:
#: **The smallest divisor with zero overflow is 48**, and its density is the
#: highest -- the origins nearly span the grid. Any larger divisor also
#: "fits", because a too-large unit merely crowds every origin into a corner;
#: density is what separates them. **64 was the first POWER OF TWO that fit
#: and was very nearly recorded as the answer** -- it leaves 32% of the grid
#: unreachable, which is the tell.
#:
#: **INDEPENDENTLY REPLICATED on a corpus that did not exist when this was
#: measured.** Client `DuueWanderer7952` (`version.dat` says patch **7939**)
#: landed on this box at 00:55 on 2026-09-15, after the 7878 measurement
#: above. Re-run against its 331 sidecars and 161 v1006 maps:
#:
#:     overflow at 44      143   (7878: 138)   must be > 0
#:     overflow at 48        0   (7878:   0)   must be 0
#:     median density       0.904 (7878: 0.908)
#:
#: A second corpus, separately authored, giving the same bound and nearly the
#: same density is much stronger than the original measurement -- it is a
#: replication rather than a re-derivation from the same data.
#:
#: This is a BOUND from our data, not a constant read out of the client. The
#: true unit is in (44, 48].
#:
#: **SUPERSEDED AS A PLACEMENT RULE, 2026-09-15.** The line above said "if a
#: client-side trace ever names it, that supersedes this". One does, and it
#: does not name a divisor at all: the origins are SCREEN PIXELS in the
#: isometric diamond, and dividing them by any constant SHEARS the map.
#: `origin_to_cell` now uses the traced projection; see it for the evidence.
#: The table above is left standing because every number in it is true -- it
#: is a correct, replicated measurement OF THE WRONG RELATION, which is the
#: more useful thing to keep. Retained as a constant only so the arms in
#: `tests/test_mapfx.py` that pin the bound keep measuring what they measured.
EFFECT_UNITS_PER_CELL = 48


def origin_to_cell(origin, height: int) -> tuple:
    r"""An effect's `origin` as a fractional `(cellX, cellY)` pair.

    **THE ORIGIN IS A SCREEN PIXEL, NOT A SCALED CELL.** This is the inverse
    of the cell->screen projection traced out of the real client at
    `Clients/7878/Env_DX9/Conquer.exe` RVA `0x86A8E2`, the same affine
    `scene.Placed.depth` cites::

        screenX = 32*(cellX - cellY) + originX
        screenY = 16*(cellX + cellY) + originY

    With the diamond's left corner at `screenX = 0`, `originX` is `32*height`,
    so inverting gives the two lines below.

    HOW THE DIVISOR RULE THAT USED TO LIVE HERE WAS CAUGHT
    -------------------------------------------------------
    `origin // 48` fitted: 0 overflow on 10,172 placements over 115 maps, and
    it replicated on a second client. It was still wrong, because **every one
    of those 115 maps is SQUARE** and a square grid cannot tell a sheared
    mapping from a correct one -- an in-bounds test passes either way.

    Three measurements, each on a relation the fit test does not model:

      1. **ASPECT.** If the origins are pixels, `max(ox)/max(oy)` is ~2 on
         ANY map, because the diamond is twice as wide as it is tall. If they
         are cells*k, that ratio is `w/h`. Over the 89 maps with >=20
         effects the median is **1.902**, and the median `w/h` is **1.000**.

      2. **THE SAME SPACE AS THE COVERS.** Forward-project the late tag-4
         cover anchors -- whose cell coordinates are used directly, and draw
         correctly -- and their pixel box lands on the effect origins' box:
         gsjx03_new 15,232..27,424 against 15,910..27,986; 2024halloween_new
         6,144..16,960 against 6,143..16,935. Two independently authored
         record types landing in one pixel space is not something a wrong
         rule produces.

      3. **A CONTROL THAT FIRED, and it is why 2 is quoted rather than 3.**
         Scoring "an effect sits near a cover" ranked this rule (median 28.9
         cells) far above `//48` (61.0) -- but a control that keeps the same
         origins and SHUFFLES their `oy` scored 28.3, i.e. the same. That
         test cannot confirm this rule's pairing and is not offered as
         evidence for it. What it does establish is against the old one:
         `//48` scores WORSE than a deliberately scrambled mapping and close
         to uniform random (73.7).

    **`height`, NOT `width`, AND THIS CORPUS CANNOT TELL THEM APART.** All
    115 maps that place effects are square, so the two are numerically
    identical on every case available. `height` is what the derivation gives.
    A non-square map that places effects would discriminate; we have none,
    and this note is here so that nobody later reads agreement with the data
    as confirmation of the choice.

    Returns FRACTIONAL cells -- these are points, not cells, and rounding
    here would throw away the sub-cell placement that makes an effect sit on
    a doorway rather than beside it.
    """
    ox, oy = (list(origin) + [0, 0])[:2]
    a = float(ox) / 32.0 - float(height)          # cellX - cellY
    b = float(oy) / 16.0                          # cellX + cellY
    return ((a + b) / 2.0, (b - a) / 2.0)


def sizes_path(root) -> Optional[Path]:
    p = Path(root) / "ini" / "C3DMapEffect.lua"
    return p if p.is_file() else None


def read_sizes(path) -> dict:
    """`{effect_name_lower: (r, dz)}` from `C3DMapEffect.lua`, or `{}`.

    Keyed lower-case because the map records and this table do not agree on
    case -- `AirWall_gold` in a `.DMap`, `airwall_gold` here on some rows.

    **`None` IS AN ANSWER, NOT AN ERROR, and this used to raise on it.**
    `sizes_path` returns None when the install ships no `C3DMapEffect.lua`,
    and `Path(None)` raises TypeError -- which `except OSError` does not
    catch. 11 of 45 installs on this box lack the file, INCLUDING the
    owner's, so `/api/mapedit/effects` answered HTTP 500 there and the map's
    whole effect layer silently did not draw: the renderer was fine and the
    table lookup in front of it threw.

    Returning `{}` is not a workaround, it is this function's own design.
    A name absent from the table already falls back to `DEFAULT_R` /
    `DEFAULT_DZ`; a table absent entirely is the same case for every name,
    and the OSError branch below says so for the file-unreadable spelling of
    it. The caller is told which happened -- see `sizes_absent`.
    """
    if path is None:
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict = {}
    for m in _ROW.finditer(text):
        vals = dict((k, int(v)) for k, v in _NUM.findall(m.group(2)))
        out[m.group(1).lower()] = (vals.get("r", DEFAULT_R),
                                   vals.get("dz", DEFAULT_DZ))
    return out


def size_of(sizes: dict, name: str) -> tuple:
    """The bounding cylinder for `name`, with the client's own defaults.

    Mirrors `GetSize` in the Lua: an absent name gets `r=363, dz=0` rather
    than being refused. **A caller that treats absence as an error would drop
    224 of the 377 map-effect names**, which is the majority.
    """
    return sizes.get(str(name).lower(), (DEFAULT_R, DEFAULT_DZ))


def effect_defs(root) -> dict:
    """`{effect_name_lower: record}` from `ini/c3.wdb`'s `EFFE` section.

    Uses `core/wdb.py`'s section walk and `core/dbc.py`'s `read_effe`, both of
    which already existed. Returns `{}` rather than raising when the client
    ships no `c3.wdb`, because a map browser must still open.

    **Names are not unique** -- `read_effe`'s own docstring records 6090
    shipping 4,483 records under 4,472 names -- so this keeps the FIRST and
    says so, rather than pretending a mapping exists.
    """
    import dbc                                              # noqa: PLC0415
    import wdb                                              # noqa: PLC0415
    p = Path(root) / "ini" / "c3.wdb"
    if not p.is_file():
        return {}
    try:
        raw = p.read_bytes()
        db = wdb.ResourceDb(p)
        secs = [s for s in db.section_table if s.get("tag") == "EFFE"]
        if not secs:
            return {}
        s = secs[0]
        recs = dbc.read_effe(raw[int(s["offset"]):
                                 int(s["offset"]) + int(s["span"])])
    except Exception:                                       # noqa: BLE001
        return {}
    out: dict = {}
    for r in recs:
        out.setdefault(str(r.get("name", "")).lower(), r)
    return out


def map_effects(dmap) -> list:
    """The tag-19 EFFECT records of one parsed `.DMap`, as `{name, origin}`.

    They live in `late_layers`, v1006's second record list. **A v1004 map has
    none** -- the list does not exist there -- so an empty result is a fact
    about the format, not a failure.
    """
    out = []
    for r in getattr(dmap, "late_layers", ()) or ():
        if r.get("type") != 19:
            continue
        out.append({"name": str(r.get("name", "")),
                    "origin": list(r.get("origin") or (0, 0)),
                    "index": r.get("index")})
    return out


def resolve(dmap, defs: dict, sizes: dict) -> list:
    """Join the three tables: placement + definition + bounding cylinder.

    Every entry carries `resolved`, because **a placement whose name has no
    definition is still a real placement** -- the client would draw nothing
    there, and a caller counting "effects on this map" must be able to tell
    the two apart. 52 of the 377 names are in that state.
    """
    out = []
    for e in map_effects(dmap):
        d = defs.get(e["name"].lower())
        r, dz = size_of(sizes, e["name"])
        out.append({
            "name": e["name"], "origin": e["origin"], "index": e["index"],
            "resolved": d is not None,
            "interval": int((d or {}).get("frame_interval", 0) or 0),
            "loop": int((d or {}).get("loop_time", 0) or 0),
            "layers": len((d or {}).get("layers") or []),
            "r": r, "dz": dz,
        })
    return out
