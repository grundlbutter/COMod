#!/usr/bin/env python3
r"""
swapsides.py -- what LEFT and RIGHT MEAN on the swap page.

The page already had two panes.  This module re-keys what they *are*::

    LEFT    the COmmunity Library.  Read from anywhere.  Filtered by
            CATEGORY, not by install.  Its contents are the owner's own
            decisions, collected over time, and they outlive any one client.

    RIGHT   the install the library is being applied TO.  Exactly one install
            is `offered` -- CCO.  Every other install this box can see is
            `served` (it can be read, browsed and collected FROM) and is
            **not offered** as a right-hand side, and the pane says which it
            is looking at and why.

The owner's sentence, which is the whole specification:

    "you would always build your library how you want it, and apply it to
     individual private servers."

THIS MODULE DESCRIBES.  IT DOES NOT EDIT.
-----------------------------------------
Standing owner ruling: COMod describes and instructs; direct editing is a
much smaller future spinoff for validated features.  Nothing here writes a
table, a mesh or a texture, and `Offering.OFFERED` is **not a write
permission** -- it is a statement that this install is the one the page is
allowed to *describe a swap against*.  There is deliberately no disabled
writer here to be re-enabled later; a disabled writer is precisely the TODO
the ruling names.

WHY THE RIGHT PANE PINS AN IDENTITY INSTEAD OF USING "THE CONFIGURED ROOT"
--------------------------------------------------------------------------
Measured on this box, 2026-08-17, three different installs are live at once:

    coroot.default_root()      C:\Program Files\Classic Conquer 2.0
    the running viewer (8731)  ...\ConquerAssets\Clients\7878  (--root)
    CCO                        C:\Program Files\Classic Conquer 2.0

`coassets.DEFAULT_ROOT` is ``coroot.default_root()`` evaluated **at import
time**, so it is whatever discovery settled on in this process -- it moves.
A right pane built as "the install the viewer is pointed at" renders 7878,
labels it CCO, and looks completely normal doing it.  So the pane asks for an
IDENTITY and refuses when it cannot get one.

`coroot.kind_for_root(root)` is the identity, and **the explicit argument is
load-bearing**.  `tests/test_kind_agreement.py` exists because the no-argument
form resolves the *configured* root and not the root the catalogue was built
for; started with ``--root Clients/7878`` against a config naming 5517, the
bare call answers ``patch5517`` about a catalogue parsing 7878.  Every call
here passes the root explicitly, and `offering_for` has no default.

WHAT `kind_for_root` ACTUALLY IS, STATED PLAINLY
------------------------------------------------
It is a lookup of **the user's own declaration**, stored per-root in the
settings map, not a measurement of the bytes on disk.  Measured both
directions on this box:

    C:\Program Files\Classic Conquer 2.0   -> 'cco'
    ...\ConquerAssets\Clients\7878         -> 'patch7878'
    ...\ConquerAssets\Clients\5517         -> 'patch5517'

That makes the check correct in the direction that matters -- an *undeclared*
or *differently declared* root answers something that is not ``'cco'`` and is
refused -- and it fails safe when the declaration is missing, because the
absence spells ``""`` and `UNIDENTIFIED` refuses.  The hole it does not close
is a **mis**-declaration: a user who declares 7878 as ``cco`` gets 7878
offered.  `Offering.basis` says so on every single result rather than letting
the page imply a byte-level proof it never performed.  Closing that needs a
content fingerprint pinned to the declaration, which is not built and is
named here rather than assumed away.

THE LIBRARY HAS FOUR STATES AND THREE OF THEM ARE NOT "EMPTY"
--------------------------------------------------------------
The first render is the one that sets the owner's expectation, and the three
non-stocked states are three different sentences:

    STOCKED       read, N > 0 entries.
    EMPTY         read, 0 entries.  **Empty by choice** -- an invitation,
                  not a fault: "collect something to begin".
    UNREADABLE    a FAULT, loud, and it names the path it tried.
    UNCONFIGURED  no COmmunity Library is set at all.  A THIRD thing, and
                  neither of the above.

`tools/coviewer.py::api_collection` collapsed two of these before this
module: `_collection()` returns ``None`` when no library is configured, and
`Collection.__init__` *raises* `CollectionError` when the index is corrupt --
so the handler answered ``why: "no COmmunity Library configured"`` for the
first and threw for the second, and the page could render neither
distinguishably.  `read_library` separates them.

HOW THE PAYLOAD IS MADE UNREACHABLE
-----------------------------------
The same construction `core.npcaltskin.Membership` uses, for the same reason:
an empty sequence is produced by more than one world here, and only the
status separates them.  Reading `status` arms the instance; `entries` and
`count` raise `StatusNotRead` until it has been, and then raise
`LibraryUnavailable` for the two states that have no count.  `__bool__`
raises, so ``if lib:`` is a crash rather than a silent wrong branch, and
there is no ``__len__`` or ``__iter__``, so ``for e in lib`` is a `TypeError`
rather than a silent empty loop.

    count: None   is correct for UNREADABLE and UNCONFIGURED
    count: 0      is a lie there, and the truth for EMPTY

Both spellings appear in `to_json`, which is what the page renders.

    py -3 core/swapsides.py                     # describe this box
    py -3 core/swapsides.py --root <path>       # describe one install
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

__all__ = [
    "StatusNotRead", "LibraryUnavailable", "NotOffered",
    "STOCKED", "EMPTY", "UNREADABLE", "UNCONFIGURED", "LIBRARY_STATES",
    "OFFERED", "SERVED_NOT_OFFERED", "UNIDENTIFIED", "OFFERING_VERDICTS",
    "OFFERED_KIND", "LibraryView", "Offering",
    "read_library", "offering_for", "cohort_line", "displacement_note",
    "NPC_DISPLACEMENT_OWNER", "COHORT_OWNER",
]


class StatusNotRead(RuntimeError):
    """A guarded payload was reached before the status that interprets it.

    Not a defensive nicety.  Every guarded value in this module is produced
    by more than one world -- an empty entry list means "collect something"
    or "I could not look", and a refused install means "this is 7878" or
    "I have no idea what this is" -- and only the status separates them.
    """


class LibraryUnavailable(LookupError):
    """The library's contents were reached in a state that has no contents.

    Raised rather than returning ``[]``, because ``[]`` is also the honest
    answer for `EMPTY`, and that conflation is the one this module exists to
    break.  A page that renders "your library is empty -- collect something
    to begin" over an unreadable folder has told the owner their deliberate
    state is fine when in fact nothing was read.
    """


class NotOffered(LookupError):
    """A target-side value was reached on an install that is not offered.

    `served`-but-not-`offered` is a normal, useful answer -- 7878 is a real
    install the owner reads from every day -- and it must not decay into
    "this install is fine to describe a swap against".
    """


# --------------------------------------------------------------------------
# the library: four states, and never a bool
# --------------------------------------------------------------------------

#: Read, and it has entries.
STOCKED = "stocked"
#: Read, and it has none.  **Empty by choice.**  An invitation, not a fault.
EMPTY = "empty"
#: A fault.  The folder or its index could not be read; `path` names what was
#: tried and `note` carries the underlying error.
UNREADABLE = "unreadable"
#: No COmmunity Library is configured at all.  A third thing.
UNCONFIGURED = "unconfigured"

LIBRARY_STATES = (STOCKED, EMPTY, UNREADABLE, UNCONFIGURED)

#: The two states that actually read a folder, so a count exists.
_COUNTED = (STOCKED, EMPTY)


class LibraryView:
    """The COmmunity Library as the left pane sees it.

    Read `status` before `entries` or `count`; the two unread states raise.
    """

    __slots__ = ("_status", "_entries", "_path", "_note", "_categories",
                 "_counts", "_armed")

    def __init__(self, status: str, *, path: str = "", note: str = "",
                 entries: Iterable[dict] = (), categories: Optional[dict] = None,
                 counts: Optional[dict] = None):
        if status not in LIBRARY_STATES:
            raise ValueError(f"{status!r} is not one of {LIBRARY_STATES}")
        entries = tuple(entries)
        if status == STOCKED and not entries:
            raise ValueError(
                f"a {STOCKED!r} view with no entries is the conflation this "
                f"class exists to break -- use {EMPTY!r}.")
        if status != STOCKED and entries:
            raise ValueError(f"a {status!r} view must carry no entries.")
        if status in (UNREADABLE, UNCONFIGURED) and not note:
            raise ValueError(
                f"a {status!r} view must say why in `note`; that sentence is "
                f"the difference between 'your library is empty' and 'I "
                f"could not read your library'.")
        if status == UNREADABLE and not path:
            raise ValueError(
                f"a {UNREADABLE!r} view must name the path it tried -- a "
                f"fault the owner cannot locate is not actionable.")
        object.__setattr__(self, "_status", status)
        object.__setattr__(self, "_entries", entries)
        object.__setattr__(self, "_path", str(path or ""))
        object.__setattr__(self, "_note", str(note or ""))
        object.__setattr__(self, "_categories", dict(categories or {}))
        object.__setattr__(self, "_counts", dict(counts or {}))
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"LibraryView is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("LibraryView is frozen")

    # -- identity, readable without arming ---------------------------------
    @property
    def path(self) -> str:
        """The folder that was tried.  Always present for `UNREADABLE`."""
        return self._path

    @property
    def note(self) -> str:
        return self._note

    @property
    def categories(self) -> dict:
        """The shelves that exist, regardless of what is on them.

        Readable without arming: the category list is a property of the
        Collection format, not of this library's contents, so the left pane
        can draw its filter row even in the fault state.
        """
        return dict(self._categories)

    # -- the guarded pair --------------------------------------------------
    @property
    def status(self) -> str:
        """One of `LIBRARY_STATES`.  Reading this arms the payload."""
        object.__setattr__(self, "_armed", True)
        return self._status

    def _check(self) -> None:
        if not self._armed:
            raise StatusNotRead(
                f"read .status before the payload. It is {self._status!r}"
                + (f" ({self._note})" if self._note else "")
                + ". `unpack()` gives you both at once.")
        if self._status not in _COUNTED:
            raise LibraryUnavailable(
                f"the library is {self._status!r}, so it has no entries to "
                f"return and no count to report: {self._note}")

    @property
    def entries(self) -> tuple:
        """The collected entries.  Empty ONLY for `EMPTY`; otherwise raises."""
        self._check()
        return self._entries

    @property
    def count(self) -> int:
        """How many entries -- **0 is a real answer here, and only here.**"""
        self._check()
        return len(self._entries)

    @property
    def counts(self) -> dict:
        """Per-category counts.  Raises for the two states that read nothing."""
        self._check()
        return dict(self._counts)

    # -- the routes that cannot be got wrong -------------------------------
    def unpack(self) -> tuple:
        """`(status, entries_or_None)`.

        **None, not ``()``** for the two unread states -- an empty tuple
        would be iterable and would put the conflation straight back.
        """
        s = self.status
        return s, (self._entries if s in _COUNTED else None)

    @property
    def headline(self) -> str:
        """The one sentence the left pane shows.  Four, and they differ."""
        s = self.status
        if s == STOCKED:
            n = len(self._entries)
            return f"{n} entr{'y' if n == 1 else 'ies'} in your COmmunity Library"
        if s == EMPTY:
            return "Your library is empty -- collect something to begin"
        if s == UNREADABLE:
            return f"Your COmmunity Library could not be read: {self._path}"
        return "No COmmunity Library configured"

    #: `stocked` and `empty` are quiet; `unreadable` is loud; `unconfigured`
    #: is neither -- it is a setup step, not a fault and not a state of the
    #: library's contents.  The page styles on this, so the three non-stocked
    #: states cannot be given one appearance by accident.
    TONE = {STOCKED: "ok", EMPTY: "invite", UNREADABLE: "fault",
            UNCONFIGURED: "setup"}

    @property
    def tone(self) -> str:
        return self.TONE[self.status]

    def to_json(self) -> dict:
        s = self._status
        counted = s in _COUNTED
        return {
            "status": s,
            "tone": self.TONE[s],
            "headline": self.headline,
            "path": self._path,
            "note": self._note,
            "categories": dict(self._categories),
            # None, not 0. `count: 0` here would be a lie for the two states
            # that never read a folder, and 0 is exactly the reassuring one.
            "count": (len(self._entries) if counted else None),
            "counts": (dict(self._counts) if counted else None),
            "entries": ([dict(e) for e in self._entries] if counted else None),
        }

    # -- everything below must not arm -------------------------------------
    def __repr__(self) -> str:
        return (f"LibraryView({self._status!r}, path={self._path!r}, "
                f"entries={len(self._entries)})")

    def __bool__(self):
        raise StatusNotRead(
            "a LibraryView has no truth value -- `if lib:` would be true for "
            f"an {UNREADABLE!r} one, which is how a fault renders as a "
            "stocked shelf. Read .status, or .unpack().")


# --------------------------------------------------------------------------
# the target install: one is offered, the rest are served
# --------------------------------------------------------------------------

#: The declared kind that is offered as a right-hand side.  One value, here,
#: rather than a string literal in the handler and another in the page.
OFFERED_KIND = "cco"

#: This install is CCO.  The page may describe a swap against it.
OFFERED = "offered"
#: A real install, readable and browsable, that is NOT offered as a target.
#: 7878 and 5517 are this.  It is not an error and the pane says so plainly.
SERVED_NOT_OFFERED = "served_not_offered"
#: Nothing could establish what this folder is.  **Refuse, never guess** --
#: this is the state a "well, it's probably the right one" would have spelled
#: `OFFERED`.
UNIDENTIFIED = "unidentified"

OFFERING_VERDICTS = (OFFERED, SERVED_NOT_OFFERED, UNIDENTIFIED)

#: What `kind_for_root` actually consults, said on every result.  The pane
#: prints it, so the owner is never shown a byte-level proof that was not
#: performed.  See the module docstring for the mis-declaration hole.
DECLARATION_BASIS = ("the per-root declaration you saved in settings, "
                     "not a measurement of the bytes on disk")


class Offering:
    """Is this install offered as the right-hand side, and which install is it.

    **The pane must always display which install it is showing.**  `install`
    and `kind` are readable without arming for exactly that reason: the
    identity line is drawn even in the refusal, because a refusal that does
    not say what it refused is indistinguishable from a page that is broken.

    `verdict` arms the payload.  There is no payload that means "you may
    write" -- see the module docstring -- only `describable`, which raises
    for the two non-answers rather than returning False, because False and
    "I could not tell" lead to opposite copy on the page.
    """

    __slots__ = ("_verdict", "_install", "_kind", "_note", "_armed")

    def __init__(self, verdict: str, *, install: str, kind: str = "",
                 note: str = ""):
        if verdict not in OFFERING_VERDICTS:
            raise ValueError(f"{verdict!r} is not one of {OFFERING_VERDICTS}")
        if verdict == OFFERED and kind != OFFERED_KIND:
            raise ValueError(
                f"an {OFFERED!r} verdict requires kind == {OFFERED_KIND!r}; "
                f"got {kind!r}. This is the check that stops a pane built as "
                f"'the configured root' from labelling 7878 as CCO.")
        if verdict != OFFERED and kind == OFFERED_KIND:
            raise ValueError(
                f"kind {OFFERED_KIND!r} cannot carry a {verdict!r} verdict.")
        if verdict == UNIDENTIFIED and kind:
            raise ValueError(
                f"an {UNIDENTIFIED!r} verdict must carry no kind -- a kind IS "
                f"an identification.")
        if verdict != OFFERED and not note:
            raise ValueError(
                f"a {verdict!r} verdict must say why in `note`; that sentence "
                f"is the difference between 'this is 7878' and 'I do not know "
                f"what this is'.")
        if not install:
            raise ValueError(
                "an Offering must name the install it is about, in every "
                "verdict -- the pane displays it even when refusing.")
        object.__setattr__(self, "_verdict", verdict)
        object.__setattr__(self, "_install", str(install))
        object.__setattr__(self, "_kind", str(kind or ""))
        object.__setattr__(self, "_note", str(note or ""))
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"Offering is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("Offering is frozen")

    # -- identity, readable without arming: the pane ALWAYS shows this -----
    @property
    def install(self) -> str:
        """The absolute path this verdict is about.  Never empty."""
        return self._install

    @property
    def kind(self) -> str:
        """The declared kind, or ``""`` when nothing identified it."""
        return self._kind

    @property
    def note(self) -> str:
        return self._note

    @property
    def basis(self) -> str:
        return DECLARATION_BASIS

    @property
    def label(self) -> str:
        """What the pane header says.  Names the install in all three."""
        name = Path(self._install).name or self._install
        if self._verdict == OFFERED:
            return f"CCO -- {name}"
        if self._verdict == SERVED_NOT_OFFERED:
            return f"{self._kind} -- {name} (served, not offered)"
        return f"unidentified -- {name}"

    # -- the guarded pair --------------------------------------------------
    @property
    def verdict(self) -> str:
        """One of `OFFERING_VERDICTS`.  Reading this arms the payload."""
        object.__setattr__(self, "_armed", True)
        return self._verdict

    @property
    def describable(self) -> bool:
        """True only for `OFFERED`.  Raises for both non-answers.

        Deliberately not "writable": nothing in COMod writes.  This says the
        page may compute and DESCRIBE a swap landing on this install.
        """
        if not self._armed:
            raise StatusNotRead(
                f"read .verdict before .describable. It is {self._verdict!r} "
                f"({self._note or 'offered'}).")
        if self._verdict != OFFERED:
            raise NotOffered(
                f"{self._install} is {self._verdict!r}, not {OFFERED!r}: "
                f"{self._note}")
        return True

    def to_json(self) -> dict:
        return {"verdict": self._verdict, "install": self._install,
                "kind": self._kind, "label": self.label, "note": self._note,
                "basis": DECLARATION_BASIS, "offeredKind": OFFERED_KIND,
                # None for the two non-answers, never False: "not offered"
                # and "could not tell" must not share a rendering.
                "describable": (True if self._verdict == OFFERED else None)}

    def __repr__(self) -> str:
        return (f"Offering({self._verdict!r}, install={self._install!r}, "
                f"kind={self._kind!r})")

    def __bool__(self):
        raise StatusNotRead(
            "an Offering has no truth value -- `if offer:` would be true for "
            f"an {UNIDENTIFIED!r} one, which is exactly the guess this class "
            "refuses to make. Read .verdict.")


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------

def read_library(library_path=None, *, _collection_cls=None) -> LibraryView:
    r"""The COmmunity Library, in one of four states, never collapsed.

    ``library_path`` of ``None`` or ``""`` is `UNCONFIGURED` -- the caller
    passes what the settings hold, and "nothing" is a real answer with its
    own copy, not a fault.

    Everything that can go wrong reading the folder becomes `UNREADABLE`
    naming the path: a missing directory, a corrupt ``collection.json``
    (which `Collection.__init__` raises `CollectionError` for), a permission
    error.  The owner gets the path they need to go look at.
    """
    try:
        from collection import CATEGORIES
    except Exception as e:                                # pragma: no cover
        return LibraryView(UNREADABLE, path=str(library_path or ""),
                           note=f"core.collection did not import: {e}")

    raw = "" if library_path is None else str(library_path).strip()
    if not raw:
        return LibraryView(
            UNCONFIGURED, categories=CATEGORIES,
            note="Point COMod at a COmmunity Library folder to begin. This "
                 "is a setup step, not a problem with your library.")

    if _collection_cls is None:
        from collection import Collection as _collection_cls   # noqa: N806

    path = str(raw)
    try:
        if not Path(path).is_dir():
            return LibraryView(
                UNREADABLE, path=path, categories=CATEGORIES,
                note="the configured COmmunity Library folder does not exist "
                     "or is not a directory")
        col = _collection_cls(path)
        entries = list(col.entries)
        counts = col.counts()
    except Exception as e:
        # Deliberately broad. `Collection` raises `CollectionError` for a
        # corrupt index and OSError for a folder that vanished mid-read, and
        # both are the same thing to the owner: *nothing was read*. What must
        # never happen is either becoming an empty shelf.
        return LibraryView(
            UNREADABLE, path=path, categories=CATEGORIES,
            note=f"{type(e).__name__}: {e}")

    if not entries:
        return LibraryView(EMPTY, path=path, categories=CATEGORIES,
                           counts=counts)
    return LibraryView(STOCKED, path=path, entries=entries,
                       categories=CATEGORIES, counts=counts)


def offering_for(root, *, kind_of=None) -> Offering:
    r"""Is ``root`` the install COMod offers as a right-hand side.

    **``root`` is required and is never defaulted.**  A default would resolve
    the configured root, which is the exact bug `tests/test_kind_agreement.py`
    was written for: the answer would be about a different folder than the
    catalogue the page is rendering.

    ``kind_of`` is the identity function, injected so the tests can drive all
    three verdicts without touching the real settings store; it defaults to
    `coroot.kind_for_root`, always called with the explicit root.
    """
    if root is None or not str(root).strip():
        raise ValueError(
            "offering_for needs an explicit root; there is no default. See "
            "tests/test_kind_agreement.py for what a defaulted root answers.")
    install = str(root)
    try:
        install = str(Path(install).resolve())
    except OSError:                                       # pragma: no cover
        pass

    if kind_of is None:
        import coroot
        kind_of = coroot.kind_for_root

    try:
        kind = (kind_of(root) or "").strip().lower()
    except Exception as e:
        return Offering(
            UNIDENTIFIED, install=install,
            note=f"the install's identity could not be established "
                 f"({type(e).__name__}: {e}), so it is refused as a target. "
                 f"COMod will not guess which client it is about to describe "
                 f"a change to.")

    if not kind:
        return Offering(
            UNIDENTIFIED, install=install,
            note="this folder has no declared kind, so COMod cannot tell "
                 "which client it is. Declare it on the setup page; an "
                 "undeclared install is refused rather than assumed.")
    if kind == OFFERED_KIND:
        return Offering(OFFERED, install=install, kind=kind)
    return Offering(
        SERVED_NOT_OFFERED, install=install, kind=kind,
        note=f"COMod serves {kind} -- you can browse it and collect FROM it "
             f"-- but only {OFFERED_KIND} is offered as a swap target, so no "
             f"swap is described against this install.")


# --------------------------------------------------------------------------
# the cohort line: a solo mesh is a POSITIVE answer, not an absence
# --------------------------------------------------------------------------

COHORT_OWNER = "COMod Parser (core/npcaltskin.py)"


def cohort_line(membership) -> dict:
    r"""The sentence the page shows about who else draws from a mesh.

    Takes a `core.npcaltskin.Membership`.  It exists because the obvious
    renderer is wrong in a way that reads as reassurance:

        users = audit(...)["sharedGeometry"].get(mesh, [])
        if not users: "nothing else uses this -- safe to overwrite"

    `npcart.audit`'s last two lines filter both maps to ``len(v) > 1``, so a
    mesh with **exactly one** user is absent from the map entirely and
    ``.get(mesh, [])`` returns ``[]`` for it and for a mesh that does not
    exist.  Measured on CCO, 2026-08-17: 86 distinct NPC geometries, 38 in
    ``sharedGeometry``, **48 solo and invisible to it** -- 56%, the majority.

    So the three verdicts get three sentences, and `SOLE` is one of them::

        in_table, count > 1   "N other NPCs draw from this mesh"
        in_table, count == 1  "one NPC draws from this mesh -- it is not
                               unused"                      <- the 48
        not_in_table          "no NPC draws its geometry from this"
        undetermined          "the npc table could not be read -- nothing is
                               known about what uses this"
    """
    import npcaltskin as _alt

    verdict, users = membership.unpack()
    out = {"geometry": membership.geometry, "verdict": verdict,
           "install": membership.install, "note": membership.note,
           "otherRole": membership.other_role, "owner": COHORT_OWNER,
           "users": None, "count": None, "sharing": None}

    if verdict == _alt.Membership.UNDETERMINED:
        out["state"] = "unknown"
        out["headline"] = ("The NPC table could not be read, so nothing is "
                           "known about what uses this mesh.")
        return out

    if verdict == _alt.Membership.NOT_IN_TABLE:
        out["state"] = "none"
        role = membership.other_role
        if role:
            # A motion path is not anybody's geometry and answering a plain
            # "nothing uses this" about it is true and badly misleading.
            out["headline"] = (
                f"No NPC draws its GEOMETRY from this, but it is a "
                f"{role.replace('_', ' ')} for at least one NPC -- replacing "
                f"it still changes the client.")
        else:
            out["headline"] = "No NPC on this install draws its geometry from this."
        return out

    n = len(users)
    out["users"] = list(users)
    out["count"] = n
    out["sharing"] = _alt.SHARED if n > 1 else _alt.SOLE
    if n == 1:
        out["state"] = "sole"
        # The sentence the 48 exist to get. It must never be a synonym of
        # the `not_in_table` one; `tests/test_swap_sides.py` asserts that.
        #
        # Phrased POSITIVELY on purpose. The first draft read "it is used,
        # not unused", and the test caught it: a skimmed negation is read as
        # the word it negates, so a sentence whose reassuring reading is one
        # dropped "not" away is the same defect in slower motion. This one
        # names the consequence and contains no negation at all.
        out["headline"] = (f"Exactly one NPC draws from this mesh (type "
                           f"{users[0]}) -- replacing it changes that NPC.")
    else:
        out["state"] = "shared"
        out["headline"] = (f"{n} NPCs draw from this mesh; a change here "
                           f"reaches all {n}.")
    return out


# --------------------------------------------------------------------------
# the displacement gap, kept visible
# --------------------------------------------------------------------------

NPC_DISPLACEMENT_OWNER = "COMod Explorer -- unassigned"

#: `core/collection.py:84`, quoted so the gate below fails loudly if the
#: constant is ever widened to include NPC roles and this note goes stale.
_MAP_ROLES_AT_WRITING = ("puzzle", "ani", "art")


def displacement_note(role: str = "npc") -> dict:
    r"""Whether the byte-identity displacement check covers this role.

    It does not cover NPCs, and this says so rather than letting the page
    imply a safety it never computed.  `core/collection.py:839` gates the
    check on ``role in MAP_ROLES``::

        MAP_ROLES = ("puzzle", "ani", "art")            # collection.py:84
        if role in MAP_ROLES and prec.get("shared"):    # collection.py:839

    An NPC mesh, motion or skin swap never reaches it.  `covered: None` for
    an uncovered role, never `False` and never `True`: "the check said this
    is safe" and "the check does not run here" are opposite facts and the
    second must not be rendered as the first.
    """
    import collection as _col
    covered = str(role) in _col.MAP_ROLES
    live = tuple(_col.MAP_ROLES)
    out = {"role": str(role), "owner": NPC_DISPLACEMENT_OWNER,
           "gate": f"core/collection.py:839 -- role in MAP_ROLES {live}"}
    if covered:
        out["covered"] = True
        out["headline"] = (f"Byte-identity displacement is computed for "
                           f"{role}: an overwrite with identical bytes is "
                           f"skipped rather than staged.")
        return out
    out["covered"] = None
    out["headline"] = (
        f"NOT WIRED IN: no displacement check runs for {role!r}. The "
        f"byte-identity check is gated on {live}, so nothing here has "
        f"measured what this would displace.")
    out["staleWarning"] = ("" if live == _MAP_ROLES_AT_WRITING else
                           f"MAP_ROLES has changed since this note was "
                           f"written ({_MAP_ROLES_AT_WRITING} -> {live}); "
                           f"re-read core/collection.py before trusting it.")
    return out


# --------------------------------------------------------------------------
# CLI -- describe this box
# --------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", help="the install to describe as a target")
    ap.add_argument("--library", help="the COmmunity Library folder")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import coroot
    lib = args.library
    if lib is None:
        lib = coroot.read_settings().get("community_library") or ""
    root = args.root or coroot.default_root()

    view = read_library(lib)
    offer = offering_for(root)
    if args.json:
        print(json.dumps({"left": view.to_json(), "right": offer.to_json()},
                         indent=2))
        return 0

    print("LEFT   the COmmunity Library")
    print(f"       status   {view.status}   (tone: {view.tone})")
    print(f"       path     {view.path or '(none configured)'}")
    print(f"       {view.headline}")
    if view.note:
        print(f"       note     {view.note}")
    j = view.to_json()
    print(f"       count    {j['count']!r}")
    print()
    print("RIGHT  the install being applied to")
    print(f"       verdict  {offer.verdict}")
    print(f"       install  {offer.install}")
    print(f"       kind     {offer.kind or '(none)'}")
    print(f"       label    {offer.label}")
    if offer.note:
        print(f"       note     {offer.note}")
    print(f"       basis    {offer.basis}")
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
