#!/usr/bin/env python3
"""otherdata.py -- the `.OtherData` sidecar: a per-cover TINT NOTHING DRAWS.

WHAT THIS FILE IS
-----------------
Every map in the 7878 tree ships a plaintext INI beside its `.DMap`::

    map/map/<name>.OtherData

It declares `SenceLayerAmount` / `TerrainLayerAmount` / `InteractiveLayerAmount`
and then, per section, a BASE tint followed by PER-OBJECT overrides::

    [TerrainLayer0]
    Alpha=255            <- the layer default
    Light=128
    Red=255
    Green=255
    Blue=255
    PuzzleAlpha=255      <- a SEPARATE tint for the puzzle (ground) plane
    ...
    MapObjAmount=96
    MapObjIndex0=230     <- a .DMap COVER index
    Alpha0=255
    Light0=128
    Red0=212             <- this cover is drawn at 83% red
    ...

WHY IT MATTERS: THE MAP DECLARES A TINT AND NOBODY DRAWS IT -- NOT THE GAME EITHER
----------------------------------------------------------------------------------
Nothing in `core/` or `tools/` read this file before this module -- a
`grep -rn OtherData` over both trees returned ZERO non-test hits. So every
tint in it was discarded HERE.

**RETRACTED 2026-09-16.  THIS FILE USED TO CALL THE PER-COVER TINT "a real
per-cover effect the client applies and we do not".  NO SHIPPED CLIENT APPLIES
IT.**  The Director of RE bisected all 38 installs under
`C:/COMod/ConquerAssets/Clients`, each pinned by `version.dat` rather than by
folder name, versions 1064 through 7952: the per-cover keys `Alpha%d`,
`Red%d`, `Green%d`, `Blue%d` have NO standalone format string in any of them,
and `GetPrivateProfileSection` -- the one path that could read them without
naming them -- is absent from 7065/7205/7878/7938/7952.  See
`docs/otherdata_percover_tint_inert_2026-09-16.md` on
`director/re-otherdata-light-inert`, commit `09ad1411`, which carries the
falsifier (`scratchpad/percover_bisect2.py`) and the substring trap that made
their own first pass untrustworthy (`Light%d` matching `SpotLight%d`).

So the owner's *"it is almost like there is a transparency effect that is not
being utilised"* was a true observation about OUR render and NOT a report of a
missing client effect.  The data is authored, the game ignores it, and since
2026-09-15 we draw it.  **With the tint on, the Map Editor shows something no
client shows.**  That is a legitimate thing for an editor to offer -- it is
what the MAP declares -- but it must never be described as matching the game.
Off with `MAPEDIT_COVER_TINT=0`, or the `.OtherData tint` checkbox.

**WHAT IS NOT RETRACTED, and RE drew this line themselves:** this is not "the
mechanism is fake", it is "the colour sub-keys of a real mechanism are
dormant".  `MapObjIndex%d` IS read, from 7065 up, and the
`MapObjIndex -> .DMap cover index` binding below stands at 321/323.  The
GROUND tint (`TerrainLayer0.Puzzle*`) is real and traced to `ApplyPuzzleTint`
at 0x89E890 with a single caller.  Only the per-cover colour and alpha are
dead data.

**THE EFFECT IS NOT THE SAME ON EVERY MAP, AND CALLING IT "TRANSPARENCY"
WHOLESALE IS WRONG.**  Measured over all 323 sidecars in the 7878 tree:

    Alpha != 255     3,636 entries on  43 maps   <- genuine transparency
    Alpha == 255    23,221 entries               <- fully opaque

    sary02_new    Alpha 255 on ALL 100 entries; RGB 143..255 (Blue down to 10)
    2024thx_new   Alpha 150/180/200/220 ...      <- IS in the 43

So on `sary02_new` -- the map the owner was looking at -- the unused effect is
a COLOUR/BRIGHTNESS tint with no transparency in it at all, while on
`2024thx_new` there is real per-cover alpha.  A fix shipped as "the
transparency fix" would show the owner a colour shift on sary02 and read as a
failure.  Both are real; they are different effects.

WHAT BINDS, AND WHAT DOES NOT
-----------------------------
`MapObjIndexK` in the **TerrainLayer** sections indexes the `.DMap` COVER
list.  Two independent methods agree:

  * overflow test (this seat):  65 in-range, 0 overflow, median density 0.996
  * exact-count (Director of RE): `TerrainLayerPicSize0.MapObjAmount` equals
    the `.DMap` cover count EXACTLY on 36/36 populated maps, 321/323 corpus.

Re-measured here before building on it, because in-range is not evidence when
the range covers everything::

    2024thx_new   covers=2089   entries=493   max index 2082   493/493 in range
    sary02_new    covers= 268   entries= 96   max index  237    96/96 in range

**THE `InteractiveLayer` SECTIONS ARE NOW APPLIED -- the index space was
settled on 2026-09-15.**  This paragraph used to say they were deliberately
excluded because the space was unresolved: they are NOT the main cover list
(1 overflow, median density 0.122) and NOT a global object dictionary (index 0
carries 14 different sizes across the corpus).  They are the **v1006 LATE
record list** -- the `.DMap`'s second counted record list, `DMap.late_layers`,
which `core/dmap.py` had parsed for months while every consumer passed only
`d.layers`.

Proven two ways, and the second is the stronger:

  * `InteractiveLayerPicSize0.MapObjAmount` equals the number of tag-4 records
    in that list on **152 of 152 v1006 maps**.  The 169 v1004 maps have no
    late list at all; their 13 "matches" are 0 == 0 and are excluded as
    VACUOUS rather than counted -- which is the difference between 152/152 and
    a misleading 165/321 over a population that cannot answer.
  * the index SETS are identical, not merely equal in size, on gsjx03_new
    (399), sary02_new (138) and 2024thx_new (10).

`interactive_tints()` reads them.  **It is a separate function from
`cover_tints()` on purpose: the two index spaces OVERLAP NUMERICALLY** (main
0..2969 against late 14..487 on gsjx03_new), so one dict keyed on a bare
integer would hand a main cover's tint to a late one and draw a plausible,
wrong picture.  The v1004 space is still unresolved -- 52,628 declared entries
with no late list -- and must not be keyed here.

THE INDEXING HAZARD THIS MODULE EXISTS TO MAKE HARD TO GET WRONG
----------------------------------------------------------------
`MapObjIndexK` is an index into `Scenery.covers` -- the `.DMap` ORDER.  The
renderer walks `sorted_covers()`, and `PlacedInfo.index` is the position in
THAT list.  Binding a tint by `PlacedInfo.index` gives every cover some other
cover's tint: the map still renders, the colours still vary, and nothing looks
broken.  `cover_tints` therefore returns a map keyed on the `.DMap` index and
the caller is expected to key it with `dmap_index_of`, which is built from
object identity against `Scenery.covers` rather than from any position.

**THOSE TWO NUMBERS BECAME EQUAL ON 2026-09-16 AND THIS PARAGRAPH STAYS.**
`sorted_covers()` now returns `.DMap` list order, because RE measured that the
client has no depth sort of covers (`scene.cover_paint_order`). So a mis-keyed
binding would currently be invisible -- which is a reason to keep the identity
key, not to drop it. The INTERACTIVE list still sorts, and any future cover
order would too; `tests/test_otherdata_cover_tint.py` carries the tripwire that
goes red if the two numbers diverge again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

#: A tint that changes nothing: opaque, unmodulated.
NEUTRAL = (255, 255, 255, 255)

#: `Light` is NOT applied, and that is now CORRECT-FROM-BINARY, not a guess.
#: The per-cover `Light%d` key (128 on 17,189 entries, 0 on 1,437) is **read by
#: nothing in the 7878 client**: the format string `"Light%d"` does not exist in
#: the image, no `"%s%d"` builder synthesises it from the bare `"Light"` key, and
#: `GetPrivateProfileSection` is absent so there is no dump-then-match path. The
#: per-cover loops read only `MapObjIndex%d`/`Width%d`/`Height%d` (geometry). So
#: `Light%d=0` means nothing to the client; the `128 == 1.0` question is moot.
#: See docs/otherdata_percover_tint_inert_2026-09-16.md (with falsifiers).
#: WARNING carried there: per-cover `Alpha%d/Red%d/Green%d/Blue%d` are ALSO not
#: read by 7878 -- which puts `cover_tints()` per-cover COLOUR in doubt (the
#: MapObjIndex binding is fine). Confirm on retail 6271 / 7939 before acting.
LIGHT_NEUTRAL = 128


def _sections(text: str) -> dict:
    """`{section_name: {key: value}}`, last duplicate key winning.

    Hand-rolled rather than `configparser` because these files carry repeated
    numeric keys in the thousands and we want them cheap, and because a
    malformed line in a sidecar must not raise inside a renderer.
    """
    out: dict = {}
    cur: Optional[dict] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = out.setdefault(line[1:-1], {})
            continue
        if cur is None or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cur[k.strip()] = v.strip()
    return out


def _int(d: dict, key: str, default: int = 0) -> int:
    try:
        return int(d.get(key, default))
    except (TypeError, ValueError):
        return default


def sidecar_path(root, name: str) -> Optional[Path]:
    """The `.OtherData` for map `name`, or None.

    `name` is the logical map name as `mapedit` uses it (`sary02_new`); the
    sidecar sits in `map/map/` beside the `.DMap`, not in `map/`.
    """
    p = Path(root) / "map" / "map" / (str(name) + ".OtherData")
    return p if p.is_file() else None


def read(path) -> dict:
    """Parse one sidecar.  Returns `{}` for anything unreadable.

    A renderer must not fail because a sidecar is missing or malformed: the
    map drew without this file for the whole life of the project, and the
    correct behaviour without it is the old behaviour, not an exception.
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return {}
    sec = _sections(text)
    head = sec.get("Header", {})
    return {
        "sections": sec,
        "sceneLayers": _int(head, "SenceLayerAmount"),      # sic, in the file
        "terrainLayers": _int(head, "TerrainLayerAmount"),
        "interactiveLayers": _int(head, "InteractiveLayerAmount"),
    }


def _base_tint(s: dict) -> tuple:
    return (_int(s, "Red", 255), _int(s, "Green", 255),
            _int(s, "Blue", 255), _int(s, "Alpha", 255))


def cover_tints(path) -> dict:
    """`{dmap_cover_index: (r, g, b, a)}` from the TerrainLayer sections.

    Only entries that actually MODULATE are returned.  A neutral entry is
    dropped rather than stored as `NEUTRAL`, so `len()` of the result is the
    number of covers this file changes -- which is the number a caller needs
    in order to tell whether a measurement built on it is vacuous.  See the
    module docstring for why `InteractiveLayer` is excluded and `Light` is
    not applied.
    """
    doc = read(path)
    sec = doc.get("sections") or {}
    out: dict = {}
    for name, s in sec.items():
        if not name.startswith("TerrainLayer"):
            continue
        if name.startswith("TerrainLayerPicSize"):          # sizes, not tints
            continue
        base = _base_tint(s)
        n = _int(s, "MapObjAmount")
        for k in range(n):
            raw = s.get("MapObjIndex%d" % k)
            if raw is None:
                continue
            try:
                idx = int(raw)
            except ValueError:
                continue
            t = (_int(s, "Red%d" % k, base[0]),
                 _int(s, "Green%d" % k, base[1]),
                 _int(s, "Blue%d" % k, base[2]),
                 _int(s, "Alpha%d" % k, base[3]))
            if t != NEUTRAL:
                out[idx] = t
    return out


def interactive_tints(path) -> dict:
    """`{late_record_index: (r, g, b, a)}` from the InteractiveLayer sections.

    A SECOND, SEPARATE INDEX SPACE, and that is why this is its own function
    rather than a flag on `cover_tints`. `TerrainLayer*` indexes the `.DMap`'s
    MAIN cover list by position (0..2969 on gsjx03_new); `InteractiveLayer*`
    indexes the v1006 LATE record list by the record's own `index` field
    (14..487 on the same map). **The ranges overlap**, so a single dict keyed
    on a bare integer would silently hand a main cover's tint to a late one.

    Proven binding, at ELEMENT level rather than by count: the set of indices
    declared here equals the set of `index` values on the late tag-4 records
    exactly, on gsjx03_new (399), sary02_new (138) and 2024thx_new (10); and
    `InteractiveLayerPicSize0.MapObjAmount` equals that count on **152 of 152
    v1006 maps**. The 169 v1004 maps have no late list at all and are a
    different, still-unresolved question -- do not key them here.

    On gsjx03_new all 103 declared entries resolve to tag-4 COVER records,
    none to tag-19 effects, and none to nothing. Effects carry no tint.
    """
    doc = read(path)
    sec = doc.get("sections") or {}
    out: dict = {}
    for name, s in sec.items():
        if not name.startswith("InteractiveLayer"):
            continue
        if name.startswith("InteractiveLayerPicSize"):        # sizes, not tints
            continue
        base = _base_tint(s)
        n = _int(s, "MapObjAmount")
        for k in range(n):
            raw = s.get("MapObjIndex%d" % k)
            if raw is None:
                continue
            try:
                idx = int(raw)
            except ValueError:
                continue
            t = (_int(s, "Red%d" % k, base[0]),
                 _int(s, "Green%d" % k, base[1]),
                 _int(s, "Blue%d" % k, base[2]),
                 _int(s, "Alpha%d" % k, base[3]))
            if t != NEUTRAL:
                out[idx] = t
    return out


def ground_puzzle_tint(path):
    """`(r, g, b, a)` for the MAIN GROUND puzzle plane, or None if neutral.

    **`TerrainLayer0` -- ONE section, not per plane, and not `SceneLayer`.**
    Traced in `7878/Env_DX9/Conquer.exe` by the Director of RE:

      * `ApplyPuzzleTint` (RVA `0x89E890`) reads the five `Puzzle*` keys from
        a section handed to it as an argument
      * it has exactly ONE caller, `0x879D3D`, in the single hardcoded
        `TerrainLayer0` block
      * the section it receives is built at `0x879C58`
        (`mov esi,"TerrainLayer0"`, `movsd` run into `[ebp-0x20]`), so the one
        ground apply reads `TerrainLayer0`

    **THIS RULING WAS WRONG TWICE BEFORE IT WAS RIGHT, so the RVAs are here to
    be re-checked rather than trusted.** The first read said "SceneLayer
    governs, TerrainLayer's `Puzzle*` is inert" -- from the ABSENCE of a direct
    string push in the TerrainLayer reader, which could not see the indirect
    call. Nothing was built on either version.

    `TerrainLayer0`'s NON-Puzzle `Alpha/Light/RGB` and its `MapObjIndex` list
    are a different thing entirely -- they tint the terrain COVERS, and
    `cover_tints()` already applies them. The `Puzzle*` subset is the ground.
    """
    sec = (read(path).get("sections") or {}).get("TerrainLayer0")
    if not sec:
        return None
    t = (_int(sec, "PuzzleRed", 255), _int(sec, "PuzzleGreen", 255),
         _int(sec, "PuzzleBlue", 255), _int(sec, "PuzzleAlpha", 255))
    return None if t == NEUTRAL else t


def plane_puzzle_tints(path) -> dict:
    """`{plane_index: (r, g, b, a)}` for the BACKDROP planes.

    **`SceneLayerN` tints backdrop plane N.** `CSceneLayer::Load` (RVA
    `0x879937`, section `SceneLayer%d`) reads `PuzzleAlpha/Light/RGB` directly,
    once per scene layer -- a per-plane read, against the single hardcoded
    ground apply above.

    Measured here before RE traced it, and the two agree:

        SceneLayerN index vs the backdrop-plane count
          in range                          36
          OVERFLOW                           0
          median density (max+1)/planes   1.000
          maps with a section but 0 planes   0   <- in-range is not vacuous

    **The INDICES ARE NON-CONTIGUOUS and that is the tell** -- `sdragon01_new`
    carries `SceneLayer1, 2, 3, 5`, skipping 0 and 4, against 6 planes. A list
    does not have gaps; a numbered slot space does. An earlier pass tested this
    hypothesis by COUNT EQUALITY (12 of 176) and recorded it REFUTED, which was
    the wrong instrument: the count never had to match, the index had to fit.

    So the maps that looked like they CONFLICTED never did. `campfief_new` is
    ground `(190,190,195)` from `TerrainLayer0` and backdrop plane 0
    `(190,190,230)` from `SceneLayer0` -- two surfaces, two tints.
    """
    out: dict = {}
    for name, sec in (read(path).get("sections") or {}).items():
        if not name.startswith("SceneLayer"):
            continue
        try:
            idx = int(name[len("SceneLayer"):])
        except ValueError:
            continue
        t = (_int(sec, "PuzzleRed", 255), _int(sec, "PuzzleGreen", 255),
             _int(sec, "PuzzleBlue", 255), _int(sec, "PuzzleAlpha", 255))
        if t != NEUTRAL:
            out[idx] = t
    return out


def dmap_index_of(covers: Iterable) -> dict:
    """`{id(placed): dmap_index}` -- the ONLY correct key for `cover_tints`.

    Built from object identity against the unsorted `Scenery.covers`, because
    the renderer holds objects it got from `sorted_covers()` and their position
    in that list is a different number -- **or was until 2026-09-16, when
    covers began painting in `.DMap` order and the two numbers coincided.**
    Identity is still the only correct key: the coincidence is a property of
    today's cover order, not of the binding.  See the module docstring.
    """
    return {id(p): i for i, p in enumerate(covers)}


def apply_tint(rgba: bytes, tint) -> bytes:
    """Modulate an RGBA buffer by `(r, g, b, a)`, each 0..255 where 255 is 1.0.

    Alpha MULTIPLIES the sprite's own alpha rather than replacing it -- a
    cover sprite is mostly transparent, and replacing its alpha would paint
    its empty margin.  That mistake renders as a rectangle around every tinted
    sprite, which is the same shape as the red-rectangle defect pinned in
    `tests/test_pux_single_layer_mask.py` and comes from the same cause: a
    mask discarded rather than combined.
    """
    r, g, b, a = (max(0, min(255, int(v))) for v in tint)
    if (r, g, b, a) == NEUTRAL:
        return rgba
    buf = bytearray(rgba)
    for i in range(0, len(buf) - 3, 4):
        if not buf[i + 3]:                                  # fully clear: skip
            continue
        buf[i] = (buf[i] * r) // 255
        buf[i + 1] = (buf[i + 1] * g) // 255
        buf[i + 2] = (buf[i + 2] * b) // 255
        buf[i + 3] = (buf[i + 3] * a) // 255
    return bytes(buf)
