#!/usr/bin/env python3
r"""
monsterart.py -- which geometry and which skin a monster body id resolves to.

**A monster body id is not a directory.** It is an id in the same space as the
texture, and the directory is a separate question with three possible answers:
the id has a directory of its own, or it is a *colour morph* that borrows
another family's geometry, or nothing ships for it at all.

    body 0103  ThunderApe   c3/monster/103/  own dir     c3/texture/103000000.dds
    body 0303  SnowApe      c3/monster/103/  BORROWED    c3/texture/303000000.dds
    body 0403  FireSnake    c3/monster/203/  BORROWED    c3/texture/403000000.dds

A colour morph ships **a texture and no geometry**.  `SnowApe` really is the
ape wearing a different skin, which its name says out loud once you know to
read it that way.  Measured on 5517: of the ids a server can spawn, 48 have
their own directory and the rest resolve through the rule below.

WHY THIS IS IN COre
-------------------
The rule lived as a private method on `tools/artcrawl.py`'s crawler, which
meant only the index builder could resolve a colour variant -- so a viewer
handed body `0303` had no route to SnowApe's geometry at all, and anything
else needing the answer would have had to copy the rule.  It is a pure
function of an id and the set of shipped directories, it has nothing to do
with crawling, and three different callers want it.  Same argument that moved
`read_gamemap_dat` and `read_ani` here.

THE RULE, AND THE PART THAT IS NOT OBVIOUS
------------------------------------------
The trailing two digits name a shipped directory and the leading digit(s) are
the colour, counted in **hundreds** above that directory.  The subtlety is
that two families can share a tail -- 117 is BullMonster and 217 is NightDevil
-- and their morphs **interleave by 200**: Bull's recolours are 317/517/717
and NightDevil's are the even hundreds above 217.  So when both bases match,
the one an even number of hundreds below wins.

Verified across the entity dump by the names, which is what makes the
interleave rule more than a tidy story:

    FireSnake 0403 -> snake dir 203, not ape dir 103
    SnowApe   0303 -> ape dir 103,   not snake dir 203
    BullDevil 0317 -> 117,           not 217

**Ask `split_colour` only for an id with no directory of its own.** `resolve`
does that ordering for you, and it matters: 217 ships its own art *and* also
satisfies the arithmetic as a morph of 117, so applying the rule first would
dress NightDevil in BullMonster's mesh.

WHAT THIS DOES NOT ANSWER
-------------------------
* **The monster's name.**  Nothing client-side links art to a name -- the
  entity tables carry no mesh column and `bodyType` is 0 on every row of
  `Monster.dat` at 5517 and 6090 alike.  The link is the server's
  `monstertype.Mesh`, and one directory commonly serves several monsters
  (`0103` serves seven), so there is no single right label.
* **Ids the arithmetic cannot place.**  `0145` is a real spawnable body with
  no directory and no base an exact multiple of 100 below it, so `resolve`
  reports it unresolved rather than inventing a home.  Deliberate: a wrong
  directory renders the wrong monster, and silence about it is worse.

Pure stdlib, and imports nothing outside COre.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

__all__ = [
    "MESH_DIR", "TEXTURE", "MonsterArt",
    "texture_for", "mesh_dir_for", "split_colour", "resolve",
]

#: Where a monster family's geometry lives, by directory id.
MESH_DIR = "c3/monster/{dir}/"

#: A monster body's skin.  The id is the body id itself, padded with the six
#: zeros every shipped monster texture carries.
TEXTURE = "c3/texture/{body}000000.dds"


def texture_for(body: int | str) -> str:
    """The skin for ``body``.  Always its own, morph or not."""
    return TEXTURE.format(body=int(body))


def mesh_dir_for(dir_id: int | str) -> str:
    """The directory holding a family's geometry.

    Takes the directory's **name**, not its number, because two of them are
    not numbers: 5517 ships ``c3/monster/104n/`` and ``c3/monster/132n/``.
    Formatting `int(104)` would name a directory that does not exist, so
    callers pass the spelling through `dir_map` rather than rebuilding it.
    """
    return MESH_DIR.format(dir=dir_id)


def dir_map(dirs) -> dict[int, str]:
    """Normalise the shipped directories to ``{number: name}``.

    Accepts a mapping already in that shape (what `artcrawl` keeps), or any
    iterable of names or numbers.  A trailing ``n`` is part of the name and
    not of the number -- see `mesh_dir_for`.
    """
    if hasattr(dirs, "items"):
        return {int(k): str(v) for k, v in dirs.items()}
    out: dict[int, str] = {}
    for d in dirs:
        name = str(d)
        try:
            out[int(name.rstrip("n"))] = name
        except ValueError:
            continue
    return out


def split_colour(body: int, dirs: Iterable[int]) -> Optional[tuple[int, int]]:
    """``(base directory, colour index)`` for a colour morph, or ``None``.

    Only meaningful for a ``body`` with **no directory of its own** -- see the
    module docstring on 217.  ``dirs`` is the set of shipped directory ids.
    """
    body = int(body)
    have = set(dir_map(dirs))
    cands = [b for b in have if b < body and (body - b) % 100 == 0]
    if len(cands) > 1:
        # Two families sharing a tail interleave by 200; prefer the base an
        # even number of hundreds below.  Fall back rather than return
        # nothing, so a family with no even-hundred base still resolves.
        cands = [b for b in cands if (body - b) % 200 == 0] or cands
    if not cands:
        return None
    base = max(cands)
    return base, (body - base) // 100


@dataclass(frozen=True)
class MonsterArt:
    """Where one monster body's art actually is."""

    body: int
    #: Directory whose geometry to draw, by number, or ``None`` when nothing ships.
    mesh_dir: Optional[int]
    #: That directory's **name** -- not always ``str(mesh_dir)``: 104 is ``104n``.
    mesh_dir_name: str
    #: 0 when the body has its own directory, else how many hundreds above it.
    colour: int
    #: The body's own skin, whether or not the geometry is borrowed.
    texture: str
    #: True when the geometry belongs to another family.
    borrowed: bool
    #: Why this answer, in one phrase, for a readout that has to explain itself.
    why: str

    @property
    def resolved(self) -> bool:
        return self.mesh_dir is not None

    @property
    def mesh_path(self) -> str:
        """Directory prefix for the geometry, or ``""`` when unresolved."""
        return mesh_dir_for(self.mesh_dir_name) if self.mesh_dir_name else ""

    def as_dict(self) -> dict:
        return {"body": self.body, "meshDir": self.mesh_dir,
                "meshDirName": self.mesh_dir_name,
                "colour": self.colour, "texture": self.texture,
                "borrowed": self.borrowed, "resolved": self.resolved,
                "meshPath": self.mesh_path, "why": self.why}


def resolve(body: int | str, dirs: Iterable[int]) -> MonsterArt:
    """Resolve a monster body id to its geometry and skin.

    Ordering is load-bearing: a body with its own directory uses it, and the
    colour rule is consulted **only** when there is none.  An id the rule
    cannot place comes back unresolved rather than pointed at a guess.
    """
    body = int(body)
    have = dir_map(dirs)
    tex = texture_for(body)
    if body in have:
        return MonsterArt(body, body, have[body], 0, tex, False,
                          "ships its own directory")
    split = split_colour(body, have)
    if split is None:
        return MonsterArt(body, None, "", 0, tex, False,
                          "no directory of its own, and no base an exact "
                          "multiple of 100 below it")
    base, colour = split
    return MonsterArt(body, base, have[base], colour, tex, True,
                      f"colour {colour} of the family in "
                      f"{mesh_dir_for(have[base])}")
