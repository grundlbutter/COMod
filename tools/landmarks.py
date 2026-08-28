#!/usr/bin/env python3
r"""landmarks.py -- read `MapDestination.dat`, the in-client landmark/guide tree.

WHAT THIS TABLE ACTUALLY IS.  The name invites the wrong reading and I made it
first: `MapDestination` is **not** a portal table.  Section `[1010-1]` carries
`mapid=1010` -- the section id and the destination are the SAME map, so there is
no source->destination edge to build.  It is a **landmark / in-game-guide index**:
named points of interest, each with map, coordinates, a radius, an NPC, and the
help text the client shows for it.

    [1010-1]
      title  = New~Skill~Learning          `~` is the space escape
      mapid  = 1010   posx = 97  posy = 45   radius = 2
      npcid  = 425
      mark   = Trojans~and~warriors~may~learn~skills~from~Old~General~Yang...

MEASURED on 7878 (`derived/7878-dat-decrypted/MapDestination.dat.out`):

    entries              1,244
    with posx/posy       1,092
    with npcid           1,091   (351 non-zero)
    with `mark` text     1,019
    with `parentid`        722   <- the table is a TREE, not a flat list
    distinct mapid         167
    distinct npcid         267

`parentid` is the part worth having: the guide is a two-level menu (category ->
entries), which is why 722 rows carry one and the rest are roots.
"""
from __future__ import annotations
import re, pathlib, collections
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "core"))
import coroot                                              # noqa: E402
from typing import Optional

#: `~` stands in for a space throughout this table.  Titles and `mark` bodies
#: both use it; nothing else in the file does.
SPACE = "~"


def _unescape(s: str) -> str:
    return s.replace(SPACE, " ").strip()


class Landmark:
    __slots__ = ("key", "title", "mapid", "posx", "posy", "radius",
                 "npcid", "mark", "parentid", "raw")

    def __init__(self, key: str, kv: dict):
        self.key = key
        self.raw = kv
        self.title = _unescape(kv.get("title", ""))
        self.mark = _unescape(kv.get("mark", ""))
        self.mapid = _int(kv.get("mapid"))
        self.posx = _int(kv.get("posx"))
        self.posy = _int(kv.get("posy"))
        self.radius = _int(kv.get("radius"))
        self.npcid = _int(kv.get("npcid"))
        self.parentid = kv.get("parentid") or None

    def __repr__(self) -> str:
        where = f" map {self.mapid} @{self.posx},{self.posy}" if self.mapid else ""
        return f"<Landmark {self.key} {self.title!r}{where}>"


def _int(v) -> Optional[int]:
    if v is None:
        return None
    v = v.strip()
    # ids are zero-padded in places ("0125"); int() handles that, isdigit gates it
    return int(v) if v.isdigit() else None


def parse(text: str) -> dict:
    """`{section key -> Landmark}` from the decrypted table's text."""
    parts = re.split(r"^\[([^\]]+)\]\s*$", text, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        kv = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", parts[i + 1], re.M))
        out[parts[i]] = Landmark(parts[i], kv)
    return out


def read(path) -> dict:
    """Parse a *decrypted* `MapDestination.dat`.

    This deliberately does NOT decrypt.  On 7878 the table is `block96` and the
    plaintext already exists under `derived/7878-dat-decrypted`; on 6271 the file
    is readable through `core.inidat.read`.  Keeping decryption out means this
    module works on either without knowing which it was handed.
    """
    return parse(pathlib.Path(path).read_bytes().decode("latin-1"))


def by_map(marks: dict) -> dict:
    """`{mapid -> [Landmark]}`, skipping rows with no map."""
    out = collections.defaultdict(list)
    for m in marks.values():
        if m.mapid is not None:
            out[m.mapid].append(m)
    return dict(out)


def roots_and_children(marks: dict) -> tuple:
    """`(roots, {root key -> [Landmark]})` -- the tree, joined on the KEY PREFIX.

    THE HIERARCHY IS IN THE SECTION KEY, NOT IN `parentid`.  A key has two or
    three dash-separated parts and the depth IS the level::

        [1039-1]      'Training Area'          depth 2 -> a root
        [1039-1-1]    'Lv.25 Training Area'    depth 3 -> its child
        [1039-1-2]    'Lv.35 Training Area'

    MEASURED on 7878: 522 keys at depth 2, 722 at depth 3, and every depth-3 key
    strips to a depth-2 key that exists.

    **The first version of this function joined on `parentid` and returned a map
    nothing could look up.**  `parentid` holds an `id` FIELD value (`'03'`,
    `'14'`), not a section key -- 147 of its 152 distinct values match some
    row's `id`, and NONE match any section key.  The grouping was well-formed
    and unusable: `kids[root.key]` was empty for all 522 roots.  The selftest
    passed because it asserted `kids` was non-empty rather than that a root
    could RESOLVE its children.  A container test is not an attachment test.
    """
    kids = collections.defaultdict(list)
    roots = []
    for key, m in marks.items():
        parts = key.split("-")
        if len(parts) > 2:
            parent = "-".join(parts[:-1])
            if parent in marks:
                kids[parent].append(m)
                continue
        roots.append(m)
    return roots, dict(kids)


def _selftest() -> int:
    """Runs on the real table if present; refuses to pass on a stub."""
    # Resolved, never written as a literal. tests/test_sanitization.py
    # caught this as a hardcoded install path on 2026-08-27; the four
    # sibling dat tools (datdict, datbulk, datmanifest, datpairs) all
    # already used this exact form and I wrote the literal anyway.
    p = coroot.assets_dir() / "derived" / "7878-dat-decrypted" / "MapDestination.dat.out"
    if not p.exists():
        print("  SKIP: no decrypted MapDestination on this box")
        return 0
    m = read(p)
    assert len(m) > 1000, f"expected >1000 entries, got {len(m)}"
    # POSITIVE: the documented sample row parses with every field
    k = "1010-1"
    assert k in m, f"{k} absent"
    e = m[k]
    assert e.mapid == 1010 and e.posx == 97 and e.posy == 45, repr(e)
    assert e.npcid == 425, e.npcid
    assert "Old General Yang" in e.mark, e.mark[:60]
    assert " " in e.title and SPACE not in e.title, "tilde not unescaped"
    # NEGATIVE CONTROL: a key that cannot exist must not resolve, and the tree
    # must not silently return zero children (the parentid-as-int trap).
    assert "no-such-section" not in m
    roots, kids = roots_and_children(m)
    assert kids, "no children at all"
    assert roots, "every row claimed a parent"
    # ATTACHMENT, not containment.  The previous selftest asserted only that
    # `kids` was non-empty, which passed while `kids[root.key]` was empty for
    # every root -- the map was well-formed and joined to nothing.
    resolving = [r for r in roots if kids.get(r.key)]
    assert resolving, "NO root resolves any child -- the join key is wrong"
    known = m.get("1039-1")
    assert known is not None, "1039-1 absent"
    ch = kids.get("1039-1") or []
    assert len(ch) >= 3, f"1039-1 'Training Area' resolved {len(ch)} children"
    assert any("Training Area" in c.title for c in ch), [c.title for c in ch[:3]]
    # NEGATIVE: a depth-3 key must never be a root
    assert not any(len(r.key.split("-")) > 2 and
                   "-".join(r.key.split("-")[:-1]) in m for r in roots), \
        "a child with a present parent was classified as a root"
    print(f"  OK  entries={len(m)}  roots={len(roots)}  parents={len(kids)}  "
          f"maps={len(by_map(m))}")
    return 0


def _cli(argv) -> int:
    import sys
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 0
    m = read(args[0])
    roots, kids = roots_and_children(m)
    print(f"{len(m)} landmarks, {len(roots)} roots, {len(by_map(m))} maps")
    for r in roots[:40]:
        print(f"  {r.title}")
        for c in kids.get(r.key, [])[:6]:
            loc = f"  ({c.mapid} @{c.posx},{c.posy})" if c.mapid else ""
            print(f"      {c.title}{loc}")
    return 0


if __name__ == "__main__":
    # NOTE: this block must stay LAST.  It sat above `_cli` in the first draft
    # and `--selftest` passed while every other invocation raised NameError --
    # the selftest exercised the one arm that did not touch the bug.
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else _cli(sys.argv))
