#!/usr/bin/env python3
r"""
mapsnip.py -- the SNIPPET of a shared plaintext table that one map needs.

WHY A SNIPPET AND NOT THE FILE
------------------------------
A map's scenery is indexed by `ani/<name>.ani`, an INI-flavoured text file of
``[key]`` sections.  On 7878 ``ani/mapscene-new.ani`` is **1.1 MB, 13,516
sections, and referenced by 180 maps**.  One map uses a few hundred of them::

    sary02_new       161 sections
    newplain13_new   337
    2024tsf_new      646

Collecting the whole file is wrong twice over.  It carries a megabyte of
other maps' art index into an entry that needs 1 % of it, and -- much worse
-- staging that entry OVERWRITES the index for the other 179 maps.  The
collector already flags the file as shared; a flag you have to override on
every single map collect is a flag nobody reads.

So the collected part is the **sections this map names**, written at the same
logical path, and it is no longer shared with anything: it is this map's own
index and it is complete for this map.

THE SLICE IS TEXTUAL, ON PURPOSE.  Re-emitting a parsed table would be the
obvious implementation and it silently drops whatever the parser does not
model.  `dmap.read_ani` deliberately ignores ``FrameAmount`` -- correctly, for
reading -- so a synthesised section would lose it.  These functions cut the
ORIGINAL BYTES between section headers and concatenate them, so a round trip
through `read_ani` is identical by construction and anything the parser never
looked at still travels.

**A REPEATED SECTION NAME IS KEPT IN FULL, EVERY OCCURRENCE.**  The client
reads these through the Win32 profile API, which returns the FIRST match, and
`read_ani` reproduces that (see its CORRECTED note: 3,641 sections on the
nine vanilla installs are declared more than once).  Keeping every occurrence
in original order means the snippet resolves the same way under first-wins
AND under last-wins, so the snippet cannot change an answer by being a
snippet.

THE COMMUNITY SPELLING.  CCO ships these indexes as ``ani/<name>.json`` and
no ``.ani`` at all (60/0 there against 0/51..56 on the official clients), so
`snippet_for` dispatches on the path that actually answered -- the one
`dmap.load_ani` returns -- and subsets the JSON object instead of slicing
text.  Same rule, two spellings, one caller.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

#: A section header at the start of a line: `[key]`, with anything after the
#: closing bracket on that line belonging to the header line.  Multiline so a
#: header is recognised only at a line start -- a `[` inside a value is not a
#: section, and `ani/Control1.ani` (3.7 MB) has plenty of them.
_SECTION = re.compile(rb"^[ \t]*\[([^\]\r\n]*)\][^\r\n]*", re.M)

#: How many bytes of leading matter (a comment header, say) to keep ahead of
#: the first section.  Kept so a file that carries a provenance comment does
#: not lose it; capped so a file whose first section is at byte 900,000
#: cannot smuggle the whole thing in.
PREAMBLE_MAX = 4096


def _norm_key(k) -> str:
    return str(k).strip().lower()


def ani_sections(raw: bytes) -> list[tuple[str, int, int]]:
    """``[(key, start, end)]`` over the original bytes, in file order.

    `end` is the start of the next header (or EOF), so the slices tile the
    whole file after the preamble and concatenating a subset of them is a
    valid file.
    """
    hits = list(_SECTION.finditer(raw))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(raw)
        out.append((_norm_key(m.group(1).decode("latin-1")), m.start(), end))
    return out


def ani_snippet(raw: bytes, keys: Iterable[str]) -> tuple[bytes, set]:
    """``(snippet bytes, keys that were NOT found)``.

    Every occurrence of every wanted key, in original order, with the file's
    preamble in front of them.
    """
    want = {_norm_key(k) for k in keys}
    secs = ani_sections(raw)
    parts = []
    first = secs[0][1] if secs else 0
    if 0 < first <= PREAMBLE_MAX:
        parts.append(raw[:first])
    seen = set()
    for key, a, b in secs:
        if key in want:
            parts.append(raw[a:b])
            seen.add(key)
    return b"".join(parts), want - seen


def json_index_snippet(raw: bytes, keys: Iterable[str]) -> tuple[bytes, set]:
    """The community client's `ani/<name>.json` spelling, subsetted.

    Keys are matched case-insensitively, as the INI side is, and written back
    with their ORIGINAL spelling so a reader that is case-sensitive still
    finds what the DMap asks for.
    """
    want = {_norm_key(k) for k in keys}
    try:
        doc = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except ValueError:
        return b"", set(want)
    if not isinstance(doc, dict):
        return b"", set(want)
    out, seen = {}, set()
    for k, v in doc.items():
        if _norm_key(k) in want:
            out[k] = v
            seen.add(_norm_key(k))
    return (json.dumps(out, indent=1).encode("utf-8"), want - seen)


def snippet_for(rel: str, raw: bytes, keys: Iterable[str]) -> tuple[bytes, set]:
    """Dispatch on the spelling that actually answered. See the module note."""
    if str(rel).lower().endswith(".json"):
        return json_index_snippet(raw, keys)
    return ani_snippet(raw, keys)


# ----------------------------------------------------------------- GameMap
#
# The registry is `ini/GameMap.json` on the community client and the binary
# `ini/GameMap.dat` on every official one (`dmap.load_gamemap` owns that
# split).  A collected map has to carry its own rows or nothing can OPEN it:
# `MapEditor.rows()` lists the registry, not the directory, so a map with no
# row is a map the editor does not know exists.
#
# **IT IS WRITTEN AS `.json` WHATEVER IT WAS READ FROM**, and that is the one
# place here that does not preserve the original form.  The binary `.dat` is
# not a plaintext table and there is no partial-`.dat` to cut; the `.json`
# spelling is a real thing the client reads, `load_gamemap` prefers it where
# it exists, and `read_gamemap_dat` already normalises the binary rows into
# exactly this shape -- so the snippet is the rows, in the form that can hold
# them.

GAMEMAP_JSON = "ini/GameMap.json"


def gamemap_snippet(rows: Iterable[dict]) -> bytes:
    """The registry rows for one map, as `ini/GameMap.json`."""
    return json.dumps(list(rows), indent=1).encode("utf-8")


def rows_for_map(all_rows: Iterable[dict], stem: str) -> list[dict]:
    r"""Every registry row naming this map, by STEM.

    Matching on the stem and not on `FileName` is not a shortcut: 5017/5065/
    5165 name ``map/map/desert.DMap`` and 5517/6090 name ``map/map/desert.7z``
    for the same map (`dmap.load_gamemap`'s note).  And a map is reused under
    several DocumentIds -- rows outnumber maps on every base -- so this
    returns a LIST and the caller carries all of them; dropping the duplicates
    would drop the ids half the game's portals point at.
    """
    want = str(stem).lower()
    out = []
    for r in all_rows or ():
        fn = str(r.get("FileName") or r.get("fileName") or "")
        if fn and Path(fn.replace("\\", "/")).stem.lower() == want:
            out.append(r)
    return out


# --------------------------------------------------------------- ini sections
#
# `ini/3DEffect.ini` is the same shape as an `.ani` -- `[name]` sections --
# so the same slicer answers, and for the same reason: it is a shared table
# (2,595 sections on 7878) and a map names a handful.

def ini_section_snippet(raw: bytes, names: Iterable[str]) -> tuple[bytes, set]:
    """Sections of a `[name]`-shaped plaintext table, by name."""
    return ani_snippet(raw, names)


def read_logical(assets, root: Optional[Path], rel: str) -> Optional[bytes]:
    """Bytes for a logical path, through the asset root when there is one.

    The tables these snippets come from are `ini/` and `ani/` files, which
    live loose on every install measured -- but "measured" is not "always",
    and an `AssetRoot` reads loose files too, so preferring it costs nothing
    and covers the archived case.
    """
    if assets is not None:
        try:
            b = assets.read(rel)
            if b:
                return b
        except Exception:                                # noqa: BLE001
            pass
    if root is not None:
        p = Path(root) / str(rel).replace("\\", "/")
        if p.is_file():
            return p.read_bytes()
    return None
