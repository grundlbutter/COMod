#!/usr/bin/env python3
r"""
npcaltskin.py -- which NPCs share one NPC's mesh, and what skins that mesh wears.

`alt_skins(root, npc_type)` answers one question about one NPC: **who else is
drawn from my geometry, and what distinct textures does that shared geometry
get dressed in.**  It is a query over `npcart`; it parses nothing itself.

WHERE AN NPC'S ALT SKINS ACTUALLY ARE, AND WHERE THEY ARE NOT
-------------------------------------------------------------
They are **not sibling files on disk.**  The Storekeeper's texture is
``c3/texture/9990211.dds`` and this install ships no ``9990211_a.dds``, no
``9990211b.dds``, nothing beside it.  A sibling glob returns an empty list
*and reports success*, which is the worst available answer: it is a confident
"this NPC has no alternate skins" produced by looking in a place alt skins
were never kept.

An alt skin is a **row-level fact, not a file-level one**.  Two NPC rows name
the same geometry through different `simple_object` selectors, and the
selector is what picks the texture::

    ini/npc.json        type 1   "Storekeeper"   SimpleObjID 211
                        type 185 <a CJK name>    SimpleObjID 30030
    ini/3DSimpleObj.ini [ObjIDType211]    Part0=9990010  Texture0=9990211
                        [ObjIDType30030]  Part0=9990010  Texture0=9990235
    ini/3dobj.ini       9990010 -> c3/mesh/9990010.c3     <- the SAME mesh
    ini/3dtexture.ini   9990211 -> c3/texture/9990211.dds
                        9990235 -> c3/texture/9990235.dds  <- the alt skin

**``30030 -> 9990235`` is not arithmetic.**  Nothing in the selector predicts
the texture id; the simple-object table is the only thing that knows.  The
sibling module `npcart` records what deriving a texture from a mesh id cost
once already -- an inference reported at 0.95 confidence that was simply not
the file the client loads.  This module derives nothing.

THE MEASUREMENT, AND THE SINGLETON THAT A HAND COUNT DROPS
-----------------------------------------------------------
Measured 2026-08-17 on the install tagged **CCO**, geometry
``c3/mesh/9990010.c3``, reached from the Storekeeper (type 1, selector 211,
motions 999001100 / 999001101 / 999001190)::

    cohort sharing that geometry     39 rows, INCLUDING the Storekeeper

    simple_object     1 : 18 NPCs -> c3/texture/9990010.dds   ships
    simple_object   211 : 20 NPCs -> c3/texture/9990211.dds   ships
    simple_object 30030 :  1 NPC  -> c3/texture/9990235.dds   ships

A published account of this same cohort said **38 rows and two selectors**.
It dropped ``30030``, the selector held by exactly one NPC.  That is the
error a hand count makes and the error a "replace every user of this mesh"
operation destroys in silence, so `Skin.sole_holder` is a first-class value
on every skin rather than something a caller has to derive from a count -- and
`AltSkins.sole_holder_skins` exists so the question can be asked directly.

It is not a freak.  Of CCO's 86 distinct NPC geometries, **12 are worn by more
than one selector and 8 of those 12 carry at least one sole-holder selector**;
on 6609 it is 49 of 249 and 33 of the 49.  A sole-holder selector is the
common case among multi-skin cohorts, not the exception.

A NUMBER IS A FACT ABOUT THE INSTALL IT CAME FROM
--------------------------------------------------
The same geometry, re-measured on **6609** the same day, is the same
*structure* and different *numbers*::

                            CCO            6609
    rows on c3/mesh/9990010.c3     39             90
    selectors                       3              3   (1, 211, 30030)
    textures                same three .dds, all shipping on both
    simple_object 30030             1 NPC         15 NPCs

So ``30030`` is a sole holder on CCO and is **not one on 6609**.  Every count
this module returns carries the install it was measured on (`AltSkins.install`)
for exactly that reason; nothing here is a fact about "Conquer".

WHY A STATUS AND NOT A LIST
---------------------------
An empty alt-skin set has more than one cause, and reporting the wrong one is
worse than reporting nothing:

    present      the tables were read and this cohort has distinct skins
    empty        the tables were read and every row in the cohort selects the
                 SAME skin.  A real, quotable "no alternate skins."
    unavailable  no cohort could be established -- this NPC's geometry does
                 not resolve, so there is nothing to share.  35 of 6609's
                 2,750 rows are in this state and 0 of CCO's 437 are.

`{"altSkins": []}` is the shape this module exists to make impossible, so:

* **`status` is never inferred from `len(rows)`.**  A `present` facet with no
  rows is a constructor error.
* **`rows` is unreachable until `status` has been read on the same facet**
  (`Facet.rows` raises `StatusNotRead`), and `Facet.__bool__` raises rather
  than being vacuously true.
* **"does this texture ship" is a tri-state, never a bool.**  `Skin.ships` is
  one of `SHIPS` / `ABSENT` / `UNKNOWN`, and `UNKNOWN` is what a caller gets
  when this module was handed tables with no install behind them.  A bool
  would spell that `False`, i.e. "this texture is missing", which is a
  different and false claim.  `Skin.__bool__` raises for the same reason.

There is deliberately no `partial`.  The cohort is closed: it is every row of
the npc table whose geometry equals this one, over a table that was either
read whole or not read at all.  Nothing here returns a floor.

THE COHORT COMPUTATION IS `npcart.audit`'s, NOT A SECOND ONE
-------------------------------------------------------------
`npcart.audit(tables, exists)` already groups every resolved NPC by geometry
and returns it as ``sharedGeometry``.  This module **consumes** that map; it
does not re-derive it.  Two things about that map have to be said out loud,
because both are the difference between a right answer and a plausible one.

**1. ABSENCE FROM `sharedGeometry` IS EVIDENCE OF NOTHING.**  The map is
filtered to ``len(v) > 1``, so a mesh with exactly one user is dropped from it
entirely -- and so is a path that no NPC row names at all.  Re-measured on CCO
2026-08-17::

    meshes with exactly one user                            48
    of those present in sharedGeometry                       0
    sharedGeometry.get('c3/npc/9990115100.c3', [])          []   <- one user
    sharedGeometry.get('c3/mesh/NOT_A_REAL_MESH.c3', [])    []   <- no users
    INDISTINGUISHABLE                                     True

Those two ``[]`` mean opposite things -- "exactly one NPC uses this, a swap
touches one model" and "this mesh is not in the NPC table at all" -- and the
empty list renders as the reassuring one.  **48 real CCO NPCs sit on the wrong
side of that**, and 88 on 6609.

So `Cohorts.membership` never decides anything from absence.  It runs a
POSITIVE scan over the planned rows and returns a `Membership` -- `in_table`
with N users (**including N == 1**), `not_in_table`, or `undetermined` -- and
the two non-answers RAISE instead of yielding an empty sequence.
`mesh_membership(root, path)` is the same check as a one-line call.

`undetermined` is the third state and it is load-bearing: a `Cohorts` built
over an install whose npc table failed to load has an empty index, and
without that state every mesh in the game would come back `not_in_table`,
i.e. "nobody uses this, safe to overwrite".

This **builds on** `npcart.Tables.plan_for_mesh` rather than superseding it.
That function answers a broader question -- it matches motion files and
extra-part meshes as well as geometry -- so on a motion path like
``c3/npc/999001100.c3`` it matches and this returns `not_in_table`, which is
true about geometry and misleading on its own.  `Membership.other_role`
therefore names the role such a path does play, from an index of the same
three roles `plan_for_mesh` walks.

**2. `sharedTexture` IS GLOBAL AND THIS QUESTION IS COHORT-SCOPED.**  They are
different questions and the global map cannot answer this one.  Measured on
CCO, for the singleton's texture::

    c3/texture/9990235.dds  global users [183, 185, 312]
                            inside the c3/mesh/9990010.c3 cohort   [185]
                            outside it                        [183, 312]
    and 183 / 312 reach it through a DIFFERENT mesh
    (c3/npc/999006100.c3) and a DIFFERENT selector (30031)

Read globally, ``30030`` looks like a texture with three users and not like a
singleton at all.  It is a singleton **within this geometry's cohort**, which
is the fact a mesh swap needs.  So every count on a `Skin` is counted over
cohort members and nothing here reads `sharedTexture`.

The other fact is real and useful, so it is reported -- **under its own name**,
`Skin.also_worn_outside_cohort`, derived from a positive texture index rather
than from any map's omissions.  "Replacing selector 30030 changes 1 NPC" and
"replacing the file 9990235.dds changes 3 NPCs across 2 meshes" are both true
and a caller must be able to tell them apart.

NAMES ARE BYTES, AND ON ONE PROFILE THEY ARE MOJIBAKE
------------------------------------------------------
The sole holder of ``30030`` on CCO is named ``怜琴``.  A probe of this cohort
crashed printing it -- ``UnicodeEncodeError: 'charmap' codec``, cp1252 -- so
the row that matters most is also the row that breaks naive I/O.  Nothing in
this module encodes a name; `as_dict` keeps them as `str` and any caller
writing them to a file must say `encoding="utf-8"`.

On the `official` and `plaintext` profiles they are worse than awkward, and
this is measured rather than assumed: `npcart` decodes `npc.ini` as latin-1,
so 6609's 12 non-ASCII names arrive as mojibake (``ÀîÉÌÈË``).  All 12
round-trip ``.encode("latin-1").decode("gbk")`` to the CJK the CCO table
spells directly (type 30 -> ``李商人``, matching CCO's own row 30).  **That
re-decode is NOT applied here.**  It is a fix to `npcart`'s reader, on
`npcart`'s branch, and guessing a codec inside a query module would put a
second spelling of the same name into circulation.  It is recorded so that a
caller who sees ``ÀîÉÌÈË`` knows it is a known reader limitation and not a
corrupt table -- and so that `AltSkins.names_are_reliable` can say so.

Pure COre: imports `npcart` and the standard library.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

import npcart

__all__ = [
    "StatusNotRead", "NoCohort", "NotInTable", "Undetermined",
    "SHIPS", "ABSENT", "UNKNOWN", "SHIPS_STATES",
    "SHARED", "SOLE",
    "Membership", "Skin", "CohortMember", "Facet", "AltSkins", "Cohorts",
    "mesh_membership", "alt_skins",
]


class StatusNotRead(RuntimeError):
    """A facet's `rows` was reached before its `status`.

    Not a defensive nicety.  An empty `rows` is produced by two different
    worlds here (`empty` and `unavailable`) and only `status` separates them,
    so a caller who has not read the status cannot correctly interpret the
    empty sequence they are most likely about to get.
    """


class NotInTable(LookupError):
    """No NPC row draws from this mesh.

    Raised by `Membership.users` rather than returning an empty list,
    because `npcart.audit`'s `sharedGeometry` already spells this state and
    "exactly one user" identically -- see the module docstring -- and an
    empty list is how that conflation gets propagated one layer further.
    """


class Undetermined(LookupError):
    """The tables could not be read, so membership was never established.

    **The state that must never degrade to `NotInTable`.**  An install whose
    npc table did not load has nothing to say about any mesh, and answering
    "no NPC uses this" would be a confident licence to overwrite it.
    """


class NoCohort(LookupError):
    """A cohort-derived value was reached on a result that has no cohort.

    Raised when `presence` is not `ON_INSTALL`, or when the NPC's geometry did
    not resolve.  It is not a crash report: "this install has never heard of
    this npc type" is a normal, useful answer, and decomposing it into an
    empty cohort and an empty skin set would describe the wrong subject.
    """


# --------------------------------------------------------------------------
# does the texture ship -- three states, and never a bool
# --------------------------------------------------------------------------

#: The `exists` predicate said yes.
SHIPS = "ships"
#: The `exists` predicate said no.  The table names a path this install does
#: not carry -- a real finding, and the reason `dds` swaps go wrong.
ABSENT = "absent"
#: Nobody was in a position to ask.  `Cohorts` was built from tables with no
#: install handle behind them, so the question was never put to a filesystem.
#: This is the state a `bool` would have spelled `False`.
UNKNOWN = "unknown"

SHIPS_STATES = (SHIPS, ABSENT, UNKNOWN)


# --------------------------------------------------------------------------
# how many rows draw from a mesh, once it IS in the table
# --------------------------------------------------------------------------

#: More than one NPC row draws from this mesh.
SHARED = "shared"
#: Exactly one NPC row draws from it -- established by a positive scan, never
#: by absence from `npcart.audit`'s `sharedGeometry`.  This is the case those
#: maps drop entirely; see `Membership`.
SOLE = "sole"


# --------------------------------------------------------------------------
# membership -- the primary deliverable
# --------------------------------------------------------------------------

class Membership:
    """Does any NPC draw from this mesh, and how many -- or is it unknown.

    **This is the check `npcart.audit`'s maps cannot express.**  Those maps
    are filtered to ``len(v) > 1``, so on CCO ``sharedGeometry.get(mesh, [])``
    returns the same ``[]`` for a mesh with exactly one user (48 of them) and
    for a mesh that does not exist.  Here they are three different verdicts
    that a caller cannot conflate, because the payload is not reachable
    without the verdict and the two non-answers RAISE rather than yielding an
    empty sequence:

        in_table       N rows draw from it, **including N == 1**
        not_in_table   no row draws from it
        undetermined   the npc table could not be read; nothing is known

    `undetermined` is not decoration.  A `Cohorts` built over an install whose
    npc table failed to load has an empty geometry index, and *every* query
    against it would otherwise answer `not_in_table` -- "nobody uses this mesh,
    safe to overwrite" -- for every mesh in the game.

    HOW THE PAYLOAD IS MADE UNREACHABLE
    -----------------------------------
    Reading `verdict` arms this instance; `users`, `count` and `sharing`
    raise `StatusNotRead` until it has been.  Once armed, `users` still
    raises `NotInTable` or `Undetermined` for the two non-answers -- it never
    returns ``()``.  `__bool__` raises, so ``if membership:`` cannot stand in
    for reading the verdict, and there is no ``__len__`` or ``__iter__``, so
    ``for t in membership`` is a `TypeError` rather than a silent empty loop.
    `unpack()` returns ``(verdict, users_or_None)`` -- **None, not an empty
    tuple**, so the non-answers are not iterable even through the convenience
    accessor.

    THE ROLE CAVEAT, WHICH THIS DOES NOT PAPER OVER
    -----------------------------------------------
    The question is *"does an NPC draw its GEOMETRY from this path"*.
    `npcart.Tables.plan_for_mesh` asks a broader one -- it also matches
    motion files and extra-part meshes -- and the two therefore disagree on
    paths like ``c3/npc/999001100.c3``, which is a motion and not anybody's
    geometry.  Answering a plain `not_in_table` there would be true about
    geometry and badly misleading about the client, so `other_role` names the
    role the path does play, and `note` says it.  **This class builds on
    `plan_for_mesh`'s question rather than superseding it**; a caller keying
    on `plan_for_mesh` today keeps a correct membership answer and gains the
    one-vs-absent split it did not have.
    """

    __slots__ = ("_verdict", "_users", "_geometry", "_other_role", "_note",
                 "_install", "_armed")

    IN_TABLE = "in_table"
    NOT_IN_TABLE = "not_in_table"
    UNDETERMINED = "undetermined"
    #: The three, in the order the class docstring introduces them.
    VERDICTS = (IN_TABLE, NOT_IN_TABLE, UNDETERMINED)

    def __init__(self, geometry: str, verdict: str,
                 users: Iterable[int] = (), other_role: str = "",
                 note: str = "", install: str = ""):
        if verdict not in self.VERDICTS:
            raise ValueError(f"{verdict!r} is not one of {self.VERDICTS}")
        users = tuple(users)
        if verdict == self.IN_TABLE and not users:
            raise ValueError(
                f"an {self.IN_TABLE!r} verdict with no users is the "
                f"conflation this class exists to break -- use "
                f"{self.NOT_IN_TABLE!r} or {self.UNDETERMINED!r}.")
        if verdict != self.IN_TABLE and users:
            raise ValueError(
                f"a {verdict!r} verdict must carry no users.")
        if verdict != self.IN_TABLE and not note:
            raise ValueError(
                f"a {verdict!r} verdict must say why in `note`; that "
                f"sentence is the difference between 'nothing uses this' "
                f"and 'I could not look'.")
        object.__setattr__(self, "_geometry", str(geometry or ""))
        object.__setattr__(self, "_verdict", verdict)
        object.__setattr__(self, "_users", users)
        object.__setattr__(self, "_other_role", str(other_role))
        object.__setattr__(self, "_note", str(note))
        object.__setattr__(self, "_install", str(install))
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"Membership is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("Membership is frozen")

    # -- identity, readable without arming ---------------------------------
    @property
    def geometry(self) -> str:
        return self._geometry

    @property
    def install(self) -> str:
        return self._install

    @property
    def note(self) -> str:
        return self._note

    @property
    def other_role(self) -> str:
        """The role this path plays if it is not anybody's geometry.

        ``"motion"``, ``"extra_part"`` or ``""``.  Non-empty only alongside
        `NOT_IN_TABLE`, and the reason that verdict is not the whole truth
        for motion paths -- see the class docstring.
        """
        return self._other_role

    # -- the guarded pair --------------------------------------------------
    @property
    def verdict(self) -> str:
        """One of `VERDICTS`.  Reading this arms the payload."""
        object.__setattr__(self, "_armed", True)
        return self._verdict

    def _check(self) -> None:
        if not self._armed:
            raise StatusNotRead(
                f"read .verdict before the payload. It is {self._verdict!r}"
                + (f" ({self._note})" if self._note else "")
                + ". `unpack()` gives you both at once.")
        if self._verdict == self.NOT_IN_TABLE:
            raise NotInTable(
                f"no NPC on {self._install or 'this install'} draws its "
                f"geometry from {self._geometry!r}: {self._note}")
        if self._verdict == self.UNDETERMINED:
            raise Undetermined(
                f"membership of {self._geometry!r} on "
                f"{self._install or 'this install'} was never established: "
                f"{self._note}")

    @property
    def users(self) -> tuple:
        """The npc types that draw from this mesh.  Never empty; raises."""
        self._check()
        return self._users

    @property
    def count(self) -> int:
        """How many rows draw from it -- **1 is a real answer here.**"""
        self._check()
        return len(self._users)

    @property
    def sharing(self) -> str:
        """`SHARED` or `SOLE`.  Raises for the two non-answers."""
        self._check()
        return SHARED if len(self._users) > 1 else SOLE

    # -- the routes that cannot be got wrong -------------------------------
    def unpack(self) -> tuple:
        """`(verdict, users_or_None)`.

        **None, not ``()``** -- an empty tuple would be iterable and would
        put the conflation straight back.
        """
        v = self.verdict
        return v, (self._users if v == self.IN_TABLE else None)

    def users_or_raise(self) -> tuple:
        """`users`, with the verdict read for you.  The recommended call."""
        self.verdict
        return self.users

    def as_dict(self) -> dict:
        return {"geometry": self._geometry, "verdict": self._verdict,
                "install": self._install, "note": self._note,
                "otherRole": self._other_role,
                "users": (list(self._users)
                          if self._verdict == self.IN_TABLE else None),
                "count": (len(self._users)
                          if self._verdict == self.IN_TABLE else None)}

    # -- everything below must not arm -------------------------------------
    def __repr__(self) -> str:
        return (f"Membership({self._geometry!r}, verdict={self._verdict!r}, "
                f"users={len(self._users)})")

    def __bool__(self):
        raise StatusNotRead(
            "a Membership has no truth value -- `if m:` would be true for "
            f"an {self.UNDETERMINED!r} one. Read .verdict, or .unpack().")

    def __eq__(self, other) -> bool:
        if not isinstance(other, Membership):
            return NotImplemented
        return (self._geometry, self._verdict, self._users,
                self._other_role) == (other._geometry, other._verdict,
                                      other._users, other._other_role)

    def __hash__(self) -> int:
        return hash((self._geometry, self._verdict, self._users,
                     self._other_role))


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------

class CohortMember:
    """One NPC row that draws from the cohort's geometry.

    `name` is whatever the install's npc table spelled, undecoded -- see the
    module docstring on mojibake.  It may be empty (rows with no `Name`) and
    it may be non-ASCII; it is never encoded here.
    """

    __slots__ = ("npc_type", "name", "simple_object", "texture", "is_query")

    def __init__(self, npc_type: int, name: str,
                 simple_object: Optional[int], texture: str,
                 is_query: bool = False):
        self.npc_type = int(npc_type)
        self.name = str(name or "")
        self.simple_object = simple_object
        self.texture = str(texture or "")
        #: True on the one member that is the NPC the caller asked about, so
        #: "including itself" is visible rather than assumed.
        self.is_query = bool(is_query)

    def as_dict(self) -> dict:
        return {"npcType": self.npc_type, "name": self.name,
                "simpleObject": self.simple_object, "texture": self.texture,
                "isQuery": self.is_query}

    def __repr__(self) -> str:
        return (f"CohortMember({self.npc_type}, {self.name!r}, "
                f"simple_object={self.simple_object!r})")

    def __eq__(self, other) -> bool:
        if not isinstance(other, CohortMember):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash((self.npc_type, self.name, self.simple_object,
                     self.texture, self.is_query))


class Skin:
    """One distinct `simple_object` selector across a cohort, and its texture.

    `ships` is one of `SHIPS_STATES` and is the reason this is a class rather
    than a tuple: an `UNKNOWN` texture must not be readable as an absent one.
    `__bool__` raises for the same reason -- ``if skin:`` would be true for a
    skin whose texture nobody could look for.
    """

    __slots__ = ("simple_object", "texture", "ships", "npc_types",
                 "names", "is_query_skin", "also_worn_outside_cohort")

    def __init__(self, simple_object: Optional[int], texture: str,
                 ships: str, npc_types: Iterable[int],
                 names: Iterable[str] = (), is_query_skin: bool = False,
                 also_worn_outside_cohort: Iterable[int] = ()):
        if ships not in SHIPS_STATES:
            raise ValueError(
                f"{ships!r} is not one of {SHIPS_STATES}. A texture's "
                f"presence has three states here and 'unknown' is not "
                f"spelled False -- see this module's docstring.")
        self.simple_object = simple_object
        self.texture = str(texture or "")
        self.ships = ships
        self.npc_types = tuple(npc_types)
        self.names = tuple(names)
        #: True on the skin the queried NPC itself wears.
        self.is_query_skin = bool(is_query_skin)
        #: NPC types that load this same TEXTURE FILE from outside this
        #: cohort -- a different mesh, or a different selector, or both.
        #:
        #: A separate fact from `count`, deliberately under its own name.
        #: `count` answers "how many models does changing this SELECTOR
        #: change"; this answers "how many more does overwriting the FILE
        #: change". Measured on CCO: selector 30030 has count 1 and this
        #: list is [183, 312], who reach `c3/texture/9990235.dds` through
        #: `c3/npc/999006100.c3` and selector 30031. Conflating the two is
        #: how a singleton stops looking like one.
        self.also_worn_outside_cohort = tuple(also_worn_outside_cohort)
        if not self.npc_types:
            raise ValueError(
                "a Skin with no holders is not a skin -- a selector exists "
                "in a cohort only because some row selected it.")

    @property
    def count(self) -> int:
        """How many rows in the cohort select this skin."""
        return len(self.npc_types)

    @property
    def sole_holder(self) -> bool:
        """Exactly one NPC **in this cohort** wears this skin.

        The value the 38-and-two account lost.  A `True` here means a
        "replace every user of this mesh" operation silently destroys a skin
        that exactly one model was wearing.

        **Scoped to the cohort, and that is not a caveat -- it is the
        question.**  Read globally the same selector's texture can have
        several users: `c3/texture/9990235.dds` is loaded by 3 CCO NPCs, of
        which 1 is in this cohort.  `also_worn_outside_cohort` carries the
        other 2 under their own name.
        """
        return len(self.npc_types) == 1

    @property
    def file_users(self) -> int:
        """Everything that loads this texture file: cohort holders plus the
        rest.  The count an overwrite of the `.dds` would affect."""
        return len(self.npc_types) + len(self.also_worn_outside_cohort)

    def __bool__(self):
        raise StatusNotRead(
            "a Skin has no truth value -- `if skin:` would be true for one "
            f"whose texture is {UNKNOWN!r}. Read .ships, or .sole_holder.")

    def as_dict(self) -> dict:
        return {"simpleObject": self.simple_object, "texture": self.texture,
                "ships": self.ships, "count": self.count,
                "soleHolder": self.sole_holder,
                "npcTypes": list(self.npc_types), "names": list(self.names),
                "isQuerySkin": self.is_query_skin,
                "alsoWornOutsideCohort": list(self.also_worn_outside_cohort),
                "fileUsers": self.file_users}

    def __repr__(self) -> str:
        return (f"Skin(simple_object={self.simple_object!r}, "
                f"texture={self.texture!r}, ships={self.ships!r}, "
                f"count={self.count})")

    def __eq__(self, other) -> bool:
        if not isinstance(other, Skin):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash((self.simple_object, self.texture, self.ships,
                     self.npc_types, self.is_query_skin,
                     self.also_worn_outside_cohort))


# --------------------------------------------------------------------------
# Facet
# --------------------------------------------------------------------------

class Facet:
    """What happened during one lookup, and what came back.

    Frozen in the sense that matters -- every value is set once in `__init__`
    and `__setattr__` refuses afterwards.  It is not a dataclass, because a
    dataclass field named ``rows`` is readable without reading ``status`` and
    every mechanism that would forbid it has to go through the field.

    The routes that could quietly arm or bypass the guard are each closed:

    1. There is no ``rows`` field -- it is a property with the guard, and
       ``__slots__`` means there is no ``__dict__`` for `vars()` or
       `dataclasses.asdict` to walk around it.
    2. ``__repr__`` reads the private ``_status`` and does not arm, so
       printing a facet in a log cannot silently unlock it.
    3. `as_dict` is the only serialisation and always emits ``status``
       beside ``rows``.
    4. ``__bool__`` raises, so ``if facet:`` cannot stand in for a check.
    5. There is no ``__iter__`` and no ``__len__``, so ``for s in facet`` and
       ``len(facet)`` are `TypeError`, not a silent empty loop.
    6. Arming is per instance and `alt_skins` builds fresh facets per call.

    Deliberately not closed: ``facet._rows`` reaches the tuple.  Writing an
    underscore name is a decision, not an accident.  `unpack()` is the
    recommended accessor -- it puts the status in the caller's hand by
    construction.
    """

    __slots__ = ("_status", "_rows", "_source", "_note", "_armed")

    PRESENT = "present"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    #: The three, in the order the module docstring introduces them.  There
    #: is no `partial`; see the module docstring for why the cohort cannot be
    #: a floor.
    STATUSES = (PRESENT, EMPTY, UNAVAILABLE)

    def __init__(self, status: str, rows: Iterable = (), source: str = "",
                 note: str = ""):
        if status not in self.STATUSES:
            raise ValueError(
                f"{status!r} is not one of {self.STATUSES}. A fourth status "
                f"is a design change, not a spelling.")
        rows = tuple(rows)
        if status == self.PRESENT and not rows:
            raise ValueError(
                f"a {self.PRESENT!r} facet with no rows is the ambiguity "
                f"this class exists to forbid -- use {self.EMPTY!r} (the "
                f"tables were read and had nothing to add) or "
                f"{self.UNAVAILABLE!r} (no cohort could be established).")
        if status == self.PRESENT and not source:
            raise ValueError(
                f"a {self.PRESENT!r} facet must name what answered; a count "
                f"whose source is invisible cannot be re-measured.")
        if status != self.PRESENT and not note:
            raise ValueError(
                f"a {status!r} facet must say why in `note`; that sentence "
                f"is the whole difference between 'no alt skins' and 'I "
                f"could not look'.")
        object.__setattr__(self, "_status", status)
        object.__setattr__(self, "_rows", rows)
        object.__setattr__(self, "_source", str(source))
        object.__setattr__(self, "_note", str(note))
        object.__setattr__(self, "_armed", False)

    # -- immutability ------------------------------------------------------
    def __setattr__(self, name, value):
        raise AttributeError(
            f"Facet is frozen; {name!r} cannot be reassigned. Build a new one.")

    def __delattr__(self, name):
        raise AttributeError("Facet is frozen")

    # -- the guarded pair --------------------------------------------------
    @property
    def status(self) -> str:
        """One of `STATUSES`.  Reading this arms `rows` on this instance."""
        object.__setattr__(self, "_armed", True)
        return self._status

    @property
    def rows(self) -> tuple:
        """The rows, once `status` has been read on this facet."""
        if not self._armed:
            raise StatusNotRead(
                f"read .status before .rows on this facet. It is "
                f"{self._status!r}"
                + (f" ({self._note})" if self._note else "")
                + f", and an empty .rows means something different under "
                f"each of {Facet.STATUSES}. `unpack()` gives you both.")
        return self._rows

    @property
    def source(self) -> str:
        """What answered -- the table set, or the geometry the cohort is of."""
        return self._source

    @property
    def note(self) -> str:
        """Why, in one sentence, whenever the status is not `present`."""
        return self._note

    # -- the routes that cannot be got wrong -------------------------------
    def unpack(self) -> tuple:
        """`(status, rows)` in one call.  The recommended accessor."""
        return self.status, self._rows

    def rows_if(self, *statuses: str) -> tuple:
        """The rows, but only when the status is one you named."""
        st = self.status
        if st not in statuses:
            raise ValueError(
                f"facet is {st!r}"
                + (f" ({self._note})" if self._note else "")
                + f", not one of {statuses!r}")
        return self._rows

    def as_dict(self) -> dict:
        """A plain dict that always carries the status beside the rows."""
        return {"status": self._status,
                "rows": [r.as_dict() if hasattr(r, "as_dict") else r
                         for r in self._rows],
                "source": self._source, "note": self._note}

    # -- everything below must not arm -------------------------------------
    def __repr__(self) -> str:
        return (f"Facet(status={self._status!r}, rows={len(self._rows)}, "
                f"source={self._source!r})")

    def __bool__(self):
        raise StatusNotRead(
            "a Facet has no truth value -- `if facet:` would be true for an "
            "`unavailable` one. Read .status, or call .unpack().")

    def __eq__(self, other) -> bool:
        if not isinstance(other, Facet):
            return NotImplemented
        return (self._status, self._rows, self._source, self._note) == \
               (other._status, other._rows, other._source, other._note)

    def __hash__(self) -> int:
        return hash((self._status, self._rows, self._source, self._note))


# --------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------

class AltSkins:
    """Everything one NPC's mesh set answers on one install.

    Read `presence` first.  It is a fact about the install's npc table, not
    about whether the lookup worked:

        on_install      this install's npc table carries this type
        not_on_install  the npc table was read and does not carry it
        undetermined    **no npc table could be read here at all**

    `undetermined` is the state that exists so an unreadable install can never
    be reported as an NPC with no alt skins.  It carries no facets, so there
    is no empty list anywhere on the object for a caller to iterate.
    """

    __slots__ = ("_npc_type", "_presence", "_install", "_profile",
                 "_geometry", "_facets", "_note", "_names_reliable",
                 "_all_skins", "_armed")

    ON_INSTALL = "on_install"
    NOT_ON_INSTALL = "not_on_install"
    UNDETERMINED = "undetermined"
    PRESENCES = (ON_INSTALL, NOT_ON_INSTALL, UNDETERMINED)
    #: The two surfaces, in the order the module docstring introduces them.
    SURFACES = ("cohort", "skins")

    def __init__(self, npc_type, presence: str, install: str = "",
                 profile: str = "", geometry: str = "",
                 facets: Optional[dict] = None, note: str = "",
                 names_reliable: bool = True,
                 all_skins: Iterable["Skin"] = ()):
        if presence not in self.PRESENCES:
            raise ValueError(f"{presence!r} is not one of {self.PRESENCES}")
        facets = dict(facets or {})
        if presence == self.ON_INSTALL:
            missing = [s for s in self.SURFACES if s not in facets]
            if missing:
                raise ValueError(
                    f"an {presence!r} result must carry every surface; "
                    f"missing {missing}. A surface left out is a surface a "
                    f"caller silently reads as absent.")
        elif facets:
            raise ValueError(
                f"a {presence!r} result must carry no facets -- decomposing "
                f"it into per-surface answers describes the wrong subject.")
        if presence != self.ON_INSTALL and not note:
            raise ValueError(
                f"a {presence!r} result must say why in `note`.")
        object.__setattr__(self, "_npc_type", npc_type)
        object.__setattr__(self, "_presence", presence)
        object.__setattr__(self, "_install", str(install))
        object.__setattr__(self, "_profile", str(profile))
        object.__setattr__(self, "_geometry", str(geometry or ""))
        object.__setattr__(self, "_facets", facets)
        object.__setattr__(self, "_note", str(note))
        object.__setattr__(self, "_names_reliable", bool(names_reliable))
        # Every selector in the cohort, including the case where there is
        # exactly one. The `skins` facet reports ALTERNATES and is `empty`
        # in that case; `own_skin` still has to answer "what does this NPC
        # wear", and rebuilding it from the cohort would lose the measured
        # `ships` state and hand back UNKNOWN instead.
        object.__setattr__(self, "_all_skins", tuple(all_skins))
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"AltSkins is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("AltSkins is frozen")

    # -- identity ----------------------------------------------------------
    @property
    def npc_type(self):
        return self._npc_type

    @property
    def install(self) -> str:
        """The install this answer is about, by `coroot.base_id` or a tag.

        Present because the same geometry answers differently per install:
        CCO's ``c3/mesh/9990010.c3`` cohort is 39 rows and 6609's is 90, with
        one selector a sole holder on the first and not on the second.
        """
        return self._install

    @property
    def profile(self) -> str:
        """The `npcart` profile the tables were read under (`cco`, ...)."""
        return self._profile

    @property
    def geometry(self) -> str:
        """The logical mesh path the cohort is defined by, or ``""``."""
        return self._geometry

    @property
    def note(self) -> str:
        return self._note

    @property
    def names_are_reliable(self) -> bool:
        """False where the profile's reader mis-decodes non-ASCII names.

        `npcart` decodes `npc.ini` as latin-1, so the `official` and
        `plaintext` profiles hand back mojibake for CJK names -- measured on
        6609, where all 12 non-ASCII names round-trip latin-1 -> gbk to the
        CJK that CCO's UTF-8 `npc.json` spells directly.  The names are still
        returned exactly as the reader produced them; this flag is how a
        caller knows not to display or match on them.
        """
        return self._names_reliable

    # -- the guarded pair, one level up ------------------------------------
    @property
    def presence(self) -> str:
        """One of `PRESENCES`.  Reading it arms the facets."""
        object.__setattr__(self, "_armed", True)
        return self._presence

    @property
    def facets(self) -> dict:
        """`{surface: Facet}` -- empty unless `presence` is `on_install`."""
        if not self._armed:
            raise StatusNotRead(
                f"read .presence before the facets. It is "
                f"{self._presence!r}"
                + (f" ({self._note})" if self._note else "")
                + f", and only {self.ON_INSTALL!r} carries per-surface "
                  f"answers at all.")
        if self._presence != self.ON_INSTALL:
            raise NoCohort(
                f"npc type {self._npc_type} is "
                f"{self._presence.replace('_', ' ')} "
                f"{self._install or 'this install'}"
                + (f": {self._note}" if self._note else "")
                + " -- there are no per-surface facets to read. This is a "
                  "normal state, not a failure.")
        return dict(self._facets)

    @property
    def cohort(self) -> Facet:
        """`CohortMember` rows: every NPC sharing this geometry, itself first."""
        return self.facets["cohort"]

    @property
    def skins(self) -> Facet:
        """`Skin` rows: the distinct selectors across the cohort.

        `present` only when the cohort carries MORE THAN ONE selector -- a
        cohort where every row selects the same texture is `empty`, which is
        the honest "this NPC has no alternate skins".  The one selector is
        still reachable via `cohort`, and via `own_skin`.
        """
        return self.facets["skins"]

    # -- the questions a caller actually has -------------------------------
    @property
    def cohort_size(self) -> int:
        """How many rows share this geometry, including this NPC.

        Raises `NoCohort` when there is none, rather than returning 0 --
        "no cohort" and "a cohort of zero" are different, and only one of
        them can exist.
        """
        st, rows = self.cohort.unpack()
        if st == Facet.UNAVAILABLE:
            raise NoCohort(
                f"npc type {self._npc_type} has no cohort on "
                f"{self._install or 'this install'}: {self.cohort.note}")
        return len(rows)

    @property
    def own_skin(self) -> Optional[Skin]:
        """The `Skin` this NPC itself wears, or None when there is no cohort.

        Answers even when the `skins` facet is `empty` -- that status means
        "no ALTERNATES", not "no skin", and a caller who read it as the
        latter would be wrong about every single-skin mesh on the install
        (48 of CCO's 86 geometries, 88 of 6609's 249).
        """
        self.presence                       # the guard, read in order
        for s in self._all_skins:
            if s.is_query_skin:
                return s
        return None

    def sole_holder_skins(self) -> tuple:
        """The skins worn by exactly one NPC in the cohort.

        A direct answer to "which of these selectors would a bulk replace
        destroy", asked as a question rather than left to a caller to derive
        from counts -- which is precisely what the 38-and-two account failed
        to do.  Empty tuple when the skins facet is not `present`; the facet's
        own status remains the place to learn why.
        """
        st, rows = self.skins.unpack()
        if st != Facet.PRESENT:
            return ()
        return tuple(s for s in rows if s.sole_holder)

    # -- serialisation, which can never drop the presence ------------------
    def as_dict(self) -> dict:
        return {
            "npcType": self._npc_type,
            "presence": self._presence,
            "install": self._install,
            "profile": self._profile,
            "geometry": self._geometry,
            "namesAreReliable": self._names_reliable,
            "note": self._note,
            "facets": {k: v.as_dict() for k, v in self._facets.items()},
        }

    def __repr__(self) -> str:
        return (f"AltSkins({self._npc_type!r}, presence={self._presence!r}, "
                f"install={self._install!r}, geometry={self._geometry!r})")


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------

class Cohorts:
    """One install's npc table, indexed by type and grouped by geometry.

    Built once and reused: `npcart.audit` plans every row and stats every
    path it resolves, which is ~0.6 s on CCO's 437 rows and ~5 s on 6609's
    2,750.  Per-call that is a tax; per-batch it is nothing.

    `shared` is `npcart.audit`'s ``sharedGeometry`` verbatim -- **this class
    does not compute a second grouping.**  What it does add is a positive
    membership scan for the case that map cannot express: see the module
    docstring, because absence from `sharedGeometry` is evidence of nothing
    and 48 CCO meshes depend on that being handled.
    """

    __slots__ = ("tables", "install", "shared", "_by_type", "_dupe_types",
                 "_exists_known", "_ships", "_geom_owners", "_tex_users",
                 "_other_role")

    def __init__(self, tables: "npcart.Tables", install: str = "",
                 exists: Optional[Callable[[str], bool]] = None):
        self.tables = tables
        self.install = str(install)
        #: False when no `exists` was supplied, which is what makes every
        #: `Skin.ships` `UNKNOWN` instead of silently `absent`.
        self._exists_known = exists is not None
        probe = exists if exists is not None else (lambda p: False)
        self._ships: dict = {}

        def _seen(p: str) -> bool:
            hit = bool(probe(p))
            self._ships[p] = hit
            return hit

        # THE cohort computation. Not reimplemented here.
        self.shared = dict(npcart.audit(tables, _seen)["sharedGeometry"])

        by_type: dict = {}
        dupes: dict = {}
        #: geometry -> the types that draw from it. Built for EVERY geometry,
        #: including the ones with a single user, because that is the case
        #: `sharedGeometry` drops and cannot be recovered from it.
        #: Deliberately not a rival to audit's grouping -- audit stays the
        #: authority for `SHARED`; this only decides `SOLE` vs
        #: `NOT_IN_TABLE`, which audit cannot express at all.
        geom_owners: dict = {}
        #: texture path -> the types that load it. A DIFFERENT question from
        #: the geometry cohort; see `Skin.also_worn_outside_cohort`. Built
        #: here rather than read from audit's `sharedTexture` because that
        #: map drops single users in exactly the same way.
        tex_users: dict = {}
        #: path -> the non-geometry role it plays, so a `not_in_table`
        #: verdict about GEOMETRY can name what the path actually is.
        #: The same roles `npcart.Tables.plan_for_mesh` matches, indexed
        #: instead of rescanned -- see `Membership`.
        other_role: dict = {}
        for row in tables.npcs:
            plan = tables.plan_for_npc(row)
            if plan.npc_type in by_type:
                dupes[plan.npc_type] = dupes.get(plan.npc_type, 1) + 1
            by_type[plan.npc_type] = plan          # last wins, as the client
            if plan.geometry:
                geom_owners.setdefault(plan.geometry, []).append(plan.npc_type)
            if plan.texture:
                tex_users.setdefault(plan.texture, []).append(plan.npc_type)
            for path in plan.motions.values():
                other_role.setdefault(path, "motion")
            for g, _t in plan.extra_parts:
                if g:
                    other_role.setdefault(g, "extra_part")
        self._by_type = by_type
        self._geom_owners = geom_owners
        self._tex_users = tex_users
        self._other_role = other_role
        #: Types carried by more than one npc-table row. 0 on CCO and 0 on
        #: 6609, measured 2026-08-17; kept because `sharedGeometry` lists one
        #: entry per ROW and a duplicated type would make a cohort's row
        #: count exceed its distinct-plan count.
        self._dupe_types = dupes

    # -- lookups -----------------------------------------------------------
    @property
    def duplicate_types(self) -> dict:
        return dict(self._dupe_types)

    @property
    def ships_is_knowable(self) -> bool:
        """Whether an `exists` oracle was supplied at all."""
        return self._exists_known

    def plan(self, npc_type) -> Optional["npcart.ArtPlan"]:
        t = _int(npc_type)
        return self._by_type.get(t) if t is not None else None

    def ships(self, path: str) -> str:
        """One of `SHIPS_STATES` for a logical path."""
        if not self._exists_known:
            return UNKNOWN
        if not path:
            return UNKNOWN
        hit = self._ships.get(path)
        return UNKNOWN if hit is None else (SHIPS if hit else ABSENT)

    @property
    def tables_readable(self) -> bool:
        """Whether the npc table produced any rows at all.

        False is what makes every `membership` answer `undetermined` instead
        of `not_in_table`. Without it an install that failed to load would
        report "no NPC uses this mesh" for every mesh in the game.
        """
        return bool(self.tables.npcs)

    def membership(self, geometry: str) -> Membership:
        """Does any NPC draw its geometry from ``geometry``, and how many.

        **The primary check.**  Returns a `Membership`, which refuses to hand
        over a payload for the two non-answers instead of returning an empty
        sequence.  `SHARED` membership still comes from `npcart.audit`'s
        grouping verbatim -- audit answers WHO; this answers the
        one-versus-absent-versus-unknown split audit's ``len(v) > 1`` filter
        cannot express.
        """
        key = (geometry or "").replace("\\", "/").lstrip("/").lower()
        if not self.tables_readable:
            return Membership(
                geometry, Membership.UNDETERMINED, install=self.install,
                note=("the npc table on this install produced no rows, so "
                      "nothing is known about any mesh here -- this is NOT "
                      "'no NPC uses it'"))
        if not key:
            return Membership(
                geometry, Membership.UNDETERMINED, install=self.install,
                note="no mesh path was given, so nothing was looked up")
        hit = self.shared.get(key)
        owners = tuple(hit) if hit is not None else \
            tuple(self._geom_owners.get(key, ()))
        if owners:
            return Membership(key, Membership.IN_TABLE, owners,
                              install=self.install)
        role = self._other_role.get(key, "")
        return Membership(
            key, Membership.NOT_IN_TABLE, other_role=role,
            install=self.install,
            note=(f"no NPC row draws its geometry from this path; it is a "
                  f"{role} file for at least one NPC, which is why "
                  f"npcart.Tables.plan_for_mesh matches it and this does not"
                  if role else
                  "no NPC row names this path in any role -- not as "
                  "geometry, not as a motion, not as an extra part"))

    def texture_users(self, texture: str) -> tuple:
        """Every npc type that loads ``texture``, from a positive index.

        Not `npcart.audit`'s `sharedTexture`, which is global AND drops
        single users. An empty tuple here genuinely means no planned row
        names this path.
        """
        return tuple(self._tex_users.get(texture, ()))


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


#: The npcart profiles whose npc table is read through a latin-1 decode, so
#: non-ASCII names come back mojibake. `cco` reads UTF-8 JSON and is exempt.
_MOJIBAKE_PROFILES = ("official", "plaintext")


def alt_skins(root, npc_type, *, cohorts: Optional[Cohorts] = None,
              install: str = "") -> AltSkins:
    """Which NPCs share this NPC's mesh, and what skins that mesh wears.

    ``root`` is an install root (`Path` or `str`) or anything with
    `AssetRoot`'s `read`/`exists` pair -- an already-open `AssetRoot`, most
    usefully.  ``cohorts`` lets a batch caller build the index once and reuse
    it; when it is given, ``root`` is not consulted and may be None.

    Read `AltSkins.presence` first, then each facet's `status` before its
    `rows`.  Both guards raise rather than handing back an ambiguous empty
    sequence.

    Example, on the install tagged CCO::

        r = alt_skins(root, 1)                 # the Storekeeper
        assert r.presence == "on_install"
        assert r.cohort_size == 39             # including itself
        st, skins = r.skins.unpack()           # "present", 3 rows
        assert [s.simple_object for s in skins] == [1, 211, 30030]
        assert [s.sole_holder for s in skins] == [False, False, True]

        single = skins[-1]                     # selector 30030
        assert single.count == 1               # models this SELECTOR dresses
        assert single.file_users == 3          # models the FILE dresses
        assert single.also_worn_outside_cohort == (183, 312)
    """
    if cohorts is None:
        cohorts = _load(root, install)
    install = install or cohorts.install
    profile = cohorts.tables.profile.name
    reliable = profile not in _MOJIBAKE_PROFILES

    if not cohorts.tables.npcs:
        return AltSkins(
            npc_type, AltSkins.UNDETERMINED, install, profile,
            names_reliable=reliable,
            note=("no npc table could be read on this install "
                  f"(profile {profile!r}), so whether this type exists here "
                  "-- let alone what shares its mesh -- is not established. "
                  "This is NOT 'no alternate skins'."))

    plan = cohorts.plan(npc_type)
    if plan is None:
        return AltSkins(
            npc_type, AltSkins.NOT_ON_INSTALL, install, profile,
            names_reliable=reliable,
            note=(f"the npc table carries {len(cohorts.tables.npcs)} rows "
                  f"and none of them is type {npc_type}"))

    if not plan.geometry:
        why = "; ".join(plan.missing) or "no geometry resolved"
        unavailable = (
            f"npc type {plan.npc_type} resolves no geometry ({why}), so "
            f"there is no mesh for anything to share. Not a claim that this "
            f"NPC's mesh is unique.")
        return AltSkins(
            npc_type, AltSkins.ON_INSTALL, install, profile, "",
            names_reliable=reliable,
            facets={"cohort": Facet(Facet.UNAVAILABLE, note=unavailable),
                    "skins": Facet(Facet.UNAVAILABLE, note=unavailable)})

    member = cohorts.membership(plan.geometry)
    verdict, types = member.unpack()
    if verdict != Membership.IN_TABLE:                    # pragma: no cover
        # Unreachable from here: this NPC's own row named the geometry, so
        # it is in the table by construction. Handled rather than asserted
        # because `Cohorts` can be built by a caller and handed in.
        return AltSkins(
            npc_type, AltSkins.ON_INSTALL, install, profile, plan.geometry,
            names_reliable=reliable,
            facets={"cohort": Facet(Facet.UNAVAILABLE, note=member.note),
                    "skins": Facet(Facet.UNAVAILABLE, note=member.note)})
    source = f"{cohorts.tables.profile.npc_table} via {plan.geometry}"

    members = []
    for t in types:
        p = cohorts.plan(t)
        if p is None:                                     # pragma: no cover
            continue
        members.append(CohortMember(p.npc_type, p.name, p.simple_object,
                                    p.texture, p.npc_type == plan.npc_type))
    if not any(m.is_query for m in members):
        # Reached when membership is SOLE or when the shared map lists this
        # geometry without this row. The queried NPC is in its own cohort by
        # construction -- its own plan is what named the geometry.
        members.insert(0, CohortMember(plan.npc_type, plan.name,
                                       plan.simple_object, plan.texture, True))
    members.sort(key=lambda m: (not m.is_query, m.npc_type))

    cohort_facet = Facet(
        Facet.PRESENT, members, source,
        note=("" if member.sharing == SHARED else
              "sole user of this geometry, established by a positive scan of "
              "the planned rows -- NOT by absence from npcart.audit's "
              "sharedGeometry, which spells 'one user' and 'no such mesh' "
              "with the same empty list"))

    # Distinct selectors across the cohort, in first-seen order made stable
    # by sorting on the selector itself.
    order: dict = {}
    for m in members:
        order.setdefault(m.simple_object, []).append(m)
    in_cohort = {m.npc_type for m in members}
    rows = []
    for sel in sorted(order, key=lambda s: (s is None, s)):
        group = order[sel]
        tex = group[0].texture
        # The same FILE, reached from outside this mesh's cohort. A different
        # question from `count` and kept under a different name; see Skin.
        outside = tuple(t for t in cohorts.texture_users(tex)
                        if t not in in_cohort)
        rows.append(Skin(sel, tex, cohorts.ships(tex),
                         [m.npc_type for m in group],
                         [m.name for m in group],
                         any(m.is_query for m in group),
                         outside))

    if len(rows) > 1:
        skins_facet = Facet(Facet.PRESENT, rows, source)
    else:
        # `is not None`, not a truth test: `Skin.__bool__` raises on purpose,
        # and this line tripped it on the first run. Left as a comment
        # because it is the guard demonstrating itself on its own author.
        only = rows[0] if rows else None
        skins_facet = Facet(
            Facet.EMPTY, (), source,
            note=(f"every one of the {len(members)} rows sharing "
                  f"{plan.geometry} selects simple_object "
                  f"{only.simple_object if only is not None else None} -> "
                  f"{only.texture if only is not None else '(none)'}, so this mesh has "
                  f"exactly one skin on this install. The tables WERE read; "
                  f"this is a measured 'no alternates', not a failed look."))

    return AltSkins(npc_type, AltSkins.ON_INSTALL, install, profile,
                    plan.geometry,
                    facets={"cohort": cohort_facet, "skins": skins_facet},
                    names_reliable=reliable, all_skins=rows)


def mesh_membership(root, geometry, *, cohorts: Optional[Cohorts] = None,
                    install: str = "") -> Membership:
    """Does any NPC on this install draw its geometry from ``geometry``.

    The membership check on its own, for callers who want the one-user /
    absent / unknown split without building a cohort.  Read `verdict` before
    the payload; the two non-answers raise rather than returning empty.

    ``cohorts`` reuses a built index; without one this opens the install and
    plans every row, which is ~0.6 s on CCO and ~5 s on 6609.

        m = mesh_membership(root, "c3/mesh/9990010.c3")
        if m.verdict == Membership.IN_TABLE:
            print(m.count, "NPCs draw from it")      # 39 on CCO
    """
    if cohorts is None:
        cohorts = _load(root, install)
    return cohorts.membership(geometry)


def _load(root, install: str) -> Cohorts:
    """Open an install and index it.  Separated so `alt_skins` stays readable."""
    assets = root
    if not (hasattr(root, "read") and hasattr(root, "exists")):
        import coassets
        assets = coassets.AssetRoot(root)
    if not install:
        try:
            import coroot
            install = coroot.base_id(getattr(assets, "root", root))
        except Exception:
            install = ""
    tables = npcart.Tables(assets.read)
    return Cohorts(tables, install, assets.exists)
