#!/usr/bin/env python3
r"""
weaponparts.py -- a weapon's motions, effects and alt skins, per install.

`weapon_parts(root, weapon_id)` answers four questions about one weapon --
what it looks like, how it swings, what it hits with, and what alternate
skins exist -- against an install whose tables may be spelled completely
differently from the last one you looked at.

Built on `docs/weapon_parts_measurement_2026-08-17.md`, which is the
measurement; this is the resolver.  Every number quoted below is from that
document or was re-measured while writing this one, on the two installs it
names (tagged **6609** and **CCO**, never by path).

WHY A STATUS AND NOT A LIST
---------------------------
The consumer question is not "what are the motions" but "does this weapon
have motions, and can I trust the answer".  Four different worlds produce an
empty list, and three of them are lies if reported as the fourth:

    present      the table shipped, was read, and named rows for this weapon
    empty        the table shipped, was read, and named NO rows for it.
                 A real, quotable "this weapon has no effects."
    unavailable  the table DOES NOT SHIP on this install.  `rows` is
                 meaningless and iterating it says nothing about the weapon.
    partial      the table shipped and answered, and a MEASURED part of it is
                 not addressable, so the rows returned are a floor and not a
                 count.  Exists for exactly one surface -- 6609's motions.

`{"motions": []}` is the shape this module exists to make impossible.  If a
tool says "no effects" and the owner cannot tell that apart from "I could not
look", the statuses bought nothing.  So:

* **`status` is never inferred from `len(rows)`.**  It is decided from what
  happened during the lookup -- which table located, which one answered --
  and a `present` facet with zero rows is a constructor error here.
* **`rows` is unreachable until `status` has been read on that same facet**
  (`Facet.rows` raises `StatusNotRead`).  See `Facet` for why that cannot be
  tripped by accident.
* **`source` is mandatory on `present` and `partial`.**  On 6609 `weapon.ini`
  is a stale twin holding 4,828 of `weapon.dbc`'s 13,292 rows under the same
  stem; a consumer that cannot see which one answered would be confidently
  wrong about 99.3% of the id space.

THE PER-INSTALL KEY GRAMMAR, WHICH IS NOT OPTIONAL
--------------------------------------------------
`weaponmotion.ini` keys an appearance to a motion `.c3`, and the two installs
spell that key differently::

    CCO    510000300     = c3/mesh/510000401.c3    <appearance:6><action:3>
    6609   1050000999300 = c3/mesh/1050000401.c3   <appearance:7><999><action:3>

A resolver written to one grammar and pointed at the other returns a
confident, silent, total zero.  Re-measured 2026-08-17 against each install's
own live appearance table:

    probe                          6609              CCO
    key[:-3]   (app+action)        0 / 25,944        1,863 / 1,863
    key[:-6]   (app+mid+action)    19,908 / 25,944   0 / 1,863

So `detect_motion_grammar` scores **both** candidates and requires a decisive
winner.  It does not fall back: an install where neither grammar clears the
floor raises `MotionGrammarUndecided` carrying both scores, because a wrong
grammar here is indistinguishable from "this weapon has no motions" and that
is the one confusion this module was written to end.  Unreachable on both
declared installs, by the margins above; stated as a refusal path, not as a
tested one.

6609'S LIVE MOTION TABLE, AND THE MODULUS TRAP
----------------------------------------------
6609's `weaponmotion.ini` is the frozen 2009 plaintext -- a 0.7% view of the
live `weaponmotion.dbc` (392,368 rows).  `dbc.weaponmotion_key` re-keys an
ini spelling into the twin's id, and **it wraps mod 2**32**, so a wide sweep
over the middle field measures the modulus rather than the data (a previous
sweep scored 81/200 real against 58/200 on a negative control).

This module therefore probes only what the install's own ini demonstrates:
the middle field values and the action values that actually occur in
`weaponmotion.ini` on THIS install -- on 6609 a single middle (`999`) and
twelve actions.  Re-measured 2026-08-17 with that restriction:

    composition                         real appearances     negative control
    mid=999, the ini's 12 actions       605 / 13,292         3 / 4,828
    mid=999, actions 000..999           615 / 13,292         4 / 4,828

The negative control (random 6-7 digit ids that are not in `weapon.dbc`) sits
at **0.06%** against a real-appearance rate of 4.6% -- a 73x separation, and
that is what makes the forward probe usable at all.  The wide sweep that
refuted itself is not implemented here and must not be added.

**384,861 of the 392,368 `.dbc` rows remain unaddressable** by every key this
resolver can compose -- the plaintext's own 25,944 keys plus each of the
13,292 live appearances at each demonstrated action.  That is 98.1%, and it
is why `partial` exists.  (The measurement document's 389,560 is the same
table counted against the plaintext's keys alone; see
`WeaponTables.unaddressable_motion_rows`.)

WHY A ZERO IS `empty` AND NOT `partial`
---------------------------------------
`partial` is a statement about the rows returned being an incomplete view.
With zero rows there is no view to be incomplete -- there is only the
question of whether the zero is bounded, and that is what `note` is for.  So
a 6609 motion lookup that finds rows is `partial` (there may be more, keyed
by something nobody here can spell) and one that finds none is `empty`, with
a note naming exactly what was probed and the measured bound on it.  Stated
because the measurement document's wording admits the other reading.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
* **No `Mesh0`-head effect fallback.**  On CCO the 16 cosmetic weapons at
  heads 800-804 have no effect section while their `Mesh0` heads do.  That
  the client falls back that way is plausible and UNVERIFIED, so it is not
  shipped -- an unmeasured fallback would turn twelve honest `empty` answers
  into twelve confident guesses.
* **No `Mesh0` grouping as an alt-skin source.**  It reproduces on both
  installs and it conflates the item quality ladder with alternate skins
  (the largest group is 149 appearances over 3 textures).  A heuristic that
  cannot separate those two is not a skins table.
* **`ItemTexture.ini` is not treated as 6609's skins table.**  1,966
  sections, of which 94 are weapon appearances; it is a garment colour
  table.  6609 ships no dedicated weapon alt-skin table, and reporting
  `unavailable` is the accurate answer rather than a thin `empty`.
* **The item table is not consulted.**  "Is this a weapon on this install"
  is a question the appearance table answers directly, and decrypting
  6609's 6.7 MB `itemtype.dat` to corroborate it would cost a second per
  call.  Checked once by hand on 2026-08-17: for `800915` the two agree on
  both installs (6609 itemtype HIT + `weapon.dbc` HIT; CCO itemtype MISS +
  `weapon.ini` MISS).

Pure COre: imports `coassets`, `dbc` and the standard library.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

__all__ = [
    "StatusNotRead", "NotOnInstall", "MotionGrammarUndecided",
    "Facet", "WeaponParts", "MotionGrammar", "WeaponTables",
    "APPEARANCE_TABLES", "MOTION_TABLES", "EFFECT_TABLES", "SKIN_TABLES",
    "SOUL_TABLE", "detect_motion_grammar", "weapon_parts",
]

# --------------------------------------------------------------------------
# tables, in the order a live-first reader must try them
# --------------------------------------------------------------------------

#: Appearance (mesh + texture).  The compiled MESH twin wins wherever one
#: exists -- same rule `coassets.PartIni` applies, restated at the logical
#: path layer so a table living inside a container is still reachable.
APPEARANCE_TABLES = ("ini/weapon.dbc", "ini/weapon.ini")

#: Motions.  On 6609 both ship and the `.dbc` is live; on CCO only the ini
#: ships and it IS live.  Both are read when both are present -- the ini
#: names 0.7% of the twin but it names them exactly.
MOTION_TABLES = ("ini/weaponmotion.dbc", "ini/weaponmotion.ini")

#: Effects.  Same shape on both installs: sections keyed by the 3-digit
#: weapon TYPE, never by the weapon.
EFFECT_TABLES = ("ini/weaponeffect.ini",)

#: Alt skins.  CCO-only, and deliberately a one-element tuple -- see the
#: module docstring on `ItemTexture.ini`.
SKIN_TABLES = ("ini/cosmetics.json",)

#: 6609's soul-item indirection: `soulItemId=appearanceId`, 331 rows, 244 of
#: them non-identity.  Absent on CCO, where the join is identity throughout.
SOUL_TABLE = "ini/SoulPart.ini"

#: Length of the action field in a `weaponmotion.ini` key, both grammars.
#: `dbc.WEAPONMOTION_INI_ACTION_DIGITS` is the same constant; kept as a local
#: name so this module reads without a cross-file hop, and asserted equal at
#: import so the two cannot drift.
ACTION_DIGITS = 3

#: How many digits of an appearance name the weapon TYPE that `weaponeffect`
#: keys on.  111 / 111 sections on 6609 and 109 / 109 on CCO are 3 digits.
EFFECT_HEAD_DIGITS = 3


class StatusNotRead(RuntimeError):
    """`rows` was reached before `status`.

    Not a defensive nicety.  `rows == ()` is produced by three different
    worlds (`empty`, `unavailable`, `partial`) and only `status` separates
    them, so a caller who has not read the status cannot correctly interpret
    an empty sequence -- and an empty sequence is the most likely thing they
    are about to get.
    """


class NotOnInstall(LookupError):
    """Per-surface facets were reached on a result that has none.

    **This is not raised by the resolver and does not mean anything went
    wrong.**  `weapon_parts` returns normally for an id this install has
    never heard of; the result simply carries no per-surface facets, because
    "this id is not a weapon here" is one fact about the install and not four
    facts about four tables.  Reaching for `.motions` anyway is a caller
    reading the result out of order -- `presence` first, always.
    """


class MotionGrammarUndecided(ValueError):
    """Neither motion-key grammar scored decisively on this install.

    A refusal, on purpose.  Guessing here does not degrade the answer, it
    inverts it: the losing grammar returns zero rows for every weapon on the
    install, which is indistinguishable from "this client has no motions".
    """


# --------------------------------------------------------------------------
# Facet
# --------------------------------------------------------------------------

class Facet:
    """One surface of one weapon: what happened, and what came back.

    Frozen in the sense that matters -- every value attribute is set once in
    `__init__` and `__setattr__` refuses afterwards.  It is NOT a dataclass,
    and that is the point: a dataclass field named ``rows`` is readable
    without reading ``status``, and every mechanism that would forbid it
    (`asdict`, `vars`, attribute access) has to go through the field.

    HOW ``rows`` IS MADE UNREACHABLE, AND WHY IT DOES NOT TRIP BY ACCIDENT
    ---------------------------------------------------------------------
    Reading ``status`` arms this instance; ``rows`` raises `StatusNotRead`
    until it has been.  The routes that could quietly arm or bypass it are
    each closed:

    1. There is no ``rows`` field -- it is a property with the guard, and
       ``__slots__`` means there is no ``__dict__`` for `vars()` or
       `dataclasses.asdict` to walk around it.
    2. ``__repr__`` reads the private ``_status`` and does **not** arm, so
       printing a facet in a log or a debugger cannot silently unlock it.
    3. `as_dict` is the only serialisation and always emits ``status``
       beside ``rows``.  There is no call that produces ``{"motions": []}``.
    4. ``__bool__`` raises rather than being vacuously true, so
       ``if facet:`` cannot stand in for a status check.
    5. There is no ``__iter__`` and no ``__len__``, so ``for m in facet``
       and ``len(facet)`` are `TypeError`, not a silent empty loop.
    6. Arming is per instance, and `weapon_parts` builds fresh facets on
       every call, so an armed facet never leaks into another lookup.

    What is deliberately NOT closed: ``facet._rows`` reaches the tuple.
    Writing an underscore name is a decision, not an accident, and a guard
    that tried to stop it would only be a guard against typing.

    `unpack()` is the recommended route -- it returns ``(status, rows)`` in
    one call, so the status is in the caller's hand by construction.
    """

    __slots__ = ("_status", "_rows", "_source", "_note", "_armed")

    PRESENT = "present"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    #: The four, in the order the docstring introduces them.
    STATUSES = (PRESENT, EMPTY, UNAVAILABLE, PARTIAL)
    #: The two that carry meaningful rows, and so must name their source.
    ANSWERED = (PRESENT, PARTIAL)

    def __init__(self, status: str, rows: Iterable = (), source: str = "",
                 note: str = ""):
        if status not in self.STATUSES:
            raise ValueError(
                f"{status!r} is not one of {self.STATUSES}. A fifth status is "
                f"a design change, not a spelling -- see this module's "
                f"docstring for what each of the four means.")
        rows = tuple(rows)
        if status in self.ANSWERED and not source:
            raise ValueError(
                f"a {status!r} facet must name the file that answered. On "
                f"6609 weapon.ini and weapon.dbc are a stale twin and a live "
                f"table under the same stem; a caller that cannot see which "
                f"one spoke cannot check the answer.")
        if status in self.ANSWERED and not rows:
            raise ValueError(
                f"a {status!r} facet with no rows is the ambiguity this class "
                f"exists to forbid -- use {self.EMPTY!r} (the table shipped "
                f"and said nothing) or {self.UNAVAILABLE!r} (the table does "
                f"not ship here).")
        if status != self.PRESENT and not note:
            raise ValueError(
                f"a {status!r} facet must say why in `note`; that sentence is "
                f"the whole difference between 'no motions' and 'I could not "
                f"look'.")
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
        """The rows, once `status` has been read on this facet.

        Raises `StatusNotRead` otherwise -- see the class docstring.
        """
        if not self._armed:
            raise StatusNotRead(
                f"read .status before .rows on this facet. It is "
                f"{self._status!r}"
                + (f" ({self._note})" if self._note else "")
                + f", and an empty .rows means something different under "
                f"each of {Facet.STATUSES}. `unpack()` gives you both at "
                f"once.")
        return self._rows

    @property
    def source(self) -> str:
        """The file that answered -- mandatory on `present` and `partial`.

        On `empty` it names the file that was **consulted** and said nothing,
        which is the sentence an owner actually wants ("weaponeffect.ini has
        no section for this type").  On `unavailable` it is empty, because
        nothing was consulted: no such table ships here.
        """
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
        """The rows, but only when the status is one you named.

        ``facet.rows_if("present", "partial")`` reads as the assertion it is.
        Raises `ValueError` when the status is something else, naming what it
        actually was -- so the mismatch is reported rather than iterated.
        """
        st = self.status
        if st not in statuses:
            raise ValueError(
                f"facet is {st!r}"
                + (f" ({self._note})" if self._note else "")
                + f", not one of {statuses!r}")
        return self._rows

    def as_dict(self) -> dict:
        """A plain dict that always carries the status beside the rows."""
        return {"status": self._status, "rows": list(self._rows),
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
# WeaponParts
# --------------------------------------------------------------------------

class WeaponParts:
    """Everything one weapon id resolves to on one install.

    Read `presence` first.  It is a fact about the install's inventory, not
    about whether the lookup worked:

        on_install      this install ships this id as a weapon
        not_on_install  this install has never heard of this id
        undetermined    no appearance table could be read here at all

    **`not_on_install` is normal and is not an error.**  The owner's workflow
    is read-from-anywhere, write-to-CCO: a donor asset worth bringing across
    is one the target does not already have, so "not on this install" is the
    expected state of nearly every id they will look up against the target.
    A UI renders it as "not on this install" and moves on.  It is also the
    fact an id allocator will want later -- an id absent here is a candidate
    free id -- which is why it is a value on the result and not prose in a
    note.

    A `not_on_install` result carries **no per-surface facets**.  Four
    `empty` facets would say "the tables have nothing to say about this
    weapon", which is a confident answer about the wrong subject.
    """

    __slots__ = ("_weapon_id", "_presence", "_install", "_appearance_id",
                 "_facets", "_note", "_armed")

    ON_INSTALL = "on_install"
    NOT_ON_INSTALL = "not_on_install"
    UNDETERMINED = "undetermined"
    PRESENCES = (ON_INSTALL, NOT_ON_INSTALL, UNDETERMINED)
    #: The four surfaces, in the order the module docstring introduces them.
    SURFACES = ("appearance", "motions", "effects", "skins")

    def __init__(self, weapon_id: str, presence: str, install: str = "",
                 appearance_id: Optional[str] = None,
                 facets: Optional[dict] = None, note: str = ""):
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
        object.__setattr__(self, "_weapon_id", str(weapon_id))
        object.__setattr__(self, "_presence", presence)
        object.__setattr__(self, "_install", str(install))
        object.__setattr__(
            self, "_appearance_id",
            None if appearance_id is None else str(appearance_id))
        object.__setattr__(self, "_facets", facets)
        object.__setattr__(self, "_note", str(note))
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"WeaponParts is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("WeaponParts is frozen")

    # -- identity ----------------------------------------------------------
    @property
    def weapon_id(self) -> str:
        return self._weapon_id

    @property
    def install(self) -> str:
        """The install this answer is about, by `coroot.base_id` or tag.

        A fact about 6609 is not a fact about CCO, and every one of these
        four surfaces differs between them.
        """
        return self._install

    @property
    def appearance_id(self) -> Optional[str]:
        """The appearance the item id resolved to, or None.

        Separate from the `appearance` facet on purpose: "I could not resolve
        the item to an appearance" and "the appearance table has no row for
        it" are different answers, and 6609's soul items make the first one
        real (`SoulPart.ini` maps 244 ids to an appearance that is not their
        own number).
        """
        return self._appearance_id

    @property
    def note(self) -> str:
        return self._note

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
                f"read .presence before the facets. It is {self._presence!r}"
                + (f" ({self._note})" if self._note else "")
                + f", and only {self.ON_INSTALL!r} carries per-surface "
                  f"answers at all -- the other two are one fact about the "
                  f"install, not four facts about four tables.")
        if self._presence != self.ON_INSTALL:
            raise NotOnInstall(
                f"{self._weapon_id} is {self._presence.replace('_', ' ')} "
                f"{self._install or 'this install'}"
                + (f": {self._note}" if self._note else "")
                + " -- there are no per-surface facets to read. This is a "
                  "normal state, not a failure.")
        return dict(self._facets)

    def _facet(self, surface: str) -> Facet:
        return self.facets[surface]

    @property
    def appearance(self) -> Facet:
        """The mesh/texture row itself."""
        return self._facet("appearance")

    @property
    def motions(self) -> Facet:
        return self._facet("motions")

    @property
    def effects(self) -> Facet:
        return self._facet("effects")

    @property
    def skins(self) -> Facet:
        return self._facet("skins")

    # -- serialisation, which can never drop the presence ------------------
    def as_dict(self) -> dict:
        return {
            "weaponId": self._weapon_id,
            "presence": self._presence,
            "install": self._install,
            "appearanceId": self._appearance_id,
            "note": self._note,
            "facets": {k: v.as_dict() for k, v in self._facets.items()},
        }

    def __repr__(self) -> str:
        return (f"WeaponParts({self._weapon_id!r}, presence="
                f"{self._presence!r}, install={self._install!r}, "
                f"appearance={self._appearance_id!r})")


# --------------------------------------------------------------------------
# motion key grammar
# --------------------------------------------------------------------------

class MotionGrammar:
    """How THIS install spells a `weaponmotion` key, and the scores that say so.

    `middle` is the extra field 6609 puts between the appearance and the
    action -- ``"999"`` there, empty on CCO.  `actions` is the set of action
    values the install's own ini demonstrates, and it is used rather than a
    0..999 sweep because the compiled twin's key composition wraps mod 2**32
    and a wide sweep measures the wrap (see the module docstring).

    `scores` is kept so the decision is observable after the fact -- a
    grammar that was picked is only as good as the margin it was picked by.
    """

    __slots__ = ("name", "middle", "action_digits", "actions", "scores",
                 "rows")

    APP_ACTION = "app+action"
    APP_MID_ACTION = "app+mid+action"

    def __init__(self, name, middle, actions, scores, rows,
                 action_digits=ACTION_DIGITS):
        self.name = name
        self.middle = middle
        self.action_digits = action_digits
        self.actions = tuple(sorted(actions))
        self.scores = dict(scores)
        self.rows = rows

    def key(self, appearance: str, action: str) -> str:
        """The ini's own spelling of one (appearance, action) pair."""
        return f"{appearance}{self.middle}{action}"

    def appearance_of(self, ini_key: str) -> str:
        """The appearance an ini key names, under this grammar."""
        cut = self.action_digits + len(self.middle)
        return ini_key[:-cut]

    def as_dict(self) -> dict:
        return {"name": self.name, "middle": self.middle,
                "actionDigits": self.action_digits,
                "actions": list(self.actions), "scores": dict(self.scores),
                "rows": self.rows}

    def __repr__(self) -> str:
        return (f"MotionGrammar({self.name!r}, middle={self.middle!r}, "
                f"actions={len(self.actions)}, scores={self.scores})")


#: A grammar must name this fraction of the ini's rows to win...
GRAMMAR_FLOOR = 0.50
#: ...and the loser must stay under this fraction, or the two are not
#: separated and the install is refused rather than guessed at.  Measured
#: margins: 6609 0.767 vs 0.000, CCO 1.000 vs 0.000.
GRAMMAR_CEILING = 0.05


def detect_motion_grammar(ini_keys: Iterable[str],
                          appearances: Iterable[str]) -> MotionGrammar:
    """Decide how this install spells a `weaponmotion` key, or refuse.

    Both candidate grammars are scored against the install's own appearance
    set; the winner must clear `GRAMMAR_FLOOR` and the loser must stay under
    `GRAMMAR_CEILING`.  Anything else raises `MotionGrammarUndecided` with
    both scores in the message -- a fallback to either one would return a
    total, silent zero for every weapon on the install.
    """
    keys = [str(k) for k in ini_keys]
    apps = {str(a) for a in appearances}
    if not keys:
        raise MotionGrammarUndecided(
            "the motion table has no rows to detect a grammar from")

    plain = [k for k in keys if len(k) > ACTION_DIGITS]
    hits_a = sum(1 for k in plain if k[:-ACTION_DIGITS] in apps)

    # The middle field is measured, not assumed to be "999": take the value
    # the keys actually carry, and only if it is effectively constant.
    mid_cut = ACTION_DIGITS * 2
    wide = [k for k in keys if len(k) > mid_cut]
    mids: dict = {}
    for k in wide:
        m = k[-mid_cut:-ACTION_DIGITS]
        mids[m] = mids.get(m, 0) + 1
    middle = max(mids, key=mids.get) if mids else ""
    hits_b = (sum(1 for k in wide
                  if k[-mid_cut:-ACTION_DIGITS] == middle
                  and k[:-mid_cut] in apps) if middle else 0)

    n = len(keys)
    scores = {MotionGrammar.APP_ACTION: hits_a,
              MotionGrammar.APP_MID_ACTION: hits_b}
    top, low = ((MotionGrammar.APP_MID_ACTION, MotionGrammar.APP_ACTION)
                if hits_b > hits_a else
                (MotionGrammar.APP_ACTION, MotionGrammar.APP_MID_ACTION))
    if scores[top] < GRAMMAR_FLOOR * n or scores[low] > GRAMMAR_CEILING * n:
        raise MotionGrammarUndecided(
            f"neither motion-key grammar is decisive over {n} rows: "
            f"{MotionGrammar.APP_ACTION}={hits_a}, "
            f"{MotionGrammar.APP_MID_ACTION}={hits_b} "
            f"(need >= {GRAMMAR_FLOOR:.0%} for the winner and "
            f"<= {GRAMMAR_CEILING:.0%} for the loser). Refusing rather than "
            f"picking one: the losing grammar returns zero rows for every "
            f"weapon on this install, which reads exactly like a client that "
            f"ships no motions.")

    if top == MotionGrammar.APP_ACTION:
        actions = {k[-ACTION_DIGITS:] for k in plain}
        return MotionGrammar(top, "", actions, scores, n)
    actions = {k[-ACTION_DIGITS:] for k in wide
               if k[-mid_cut:-ACTION_DIGITS] == middle}
    return MotionGrammar(top, middle, actions, scores, n)


# --------------------------------------------------------------------------
# reading the tables
# --------------------------------------------------------------------------

_SECTION = re.compile(r"^\[([^\]]+)\]([^\[]*)", re.M)
_KV = re.compile(r"^(\w+)\s*=\s*(.*?)\s*$", re.M)


def _sections(text: str) -> dict:
    """`{section: {key: value}}` for a TQ ini, from TEXT rather than a path.

    `coassets.parse_ini` takes a `Path` and cannot read a table that lives
    inside a container; the presence criterion this module uses is
    `AssetRoot.locate`, which can.  Same grammar, different input.
    """
    return {name.strip(): dict(_KV.findall(body))
            for name, body in _SECTION.findall(text)}


def _flat(text: str) -> dict:
    """`{key: value}` for a flat TQ table -- `weaponmotion.ini`, `SoulPart.ini`."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ";#[":
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _parts_from_mesh(parts) -> tuple:
    """One compiled MESH record -> the common appearance-part shape."""
    return tuple({"index": i, "mesh": str(p["mesh"]),
                  "texture": str(p["texture"]),
                  "mixTex": str(p.get("mixtex", 0)), "raw": dict(p)}
                 for i, p in enumerate(parts))


def _parts_from_ini(kv: dict) -> tuple:
    """One plaintext `weapon.ini` section -> the same shape.

    The two installs' appearance tables are a compiled twin and a plaintext,
    and a caller must not have to branch on which one answered -- that is
    what `source` is for, not what the rows are for.  Same normalisation
    `coassets.PartIni` performs; done here because this module reaches the
    table through `AssetRoot.locate`/`read` rather than a filesystem path,
    so a table inside a container is still readable.
    """
    try:
        n = int(kv.get("Part", "0") or 0)
    except ValueError:
        n = 0
    out = []
    for i in range(n):
        mesh, tex = kv.get(f"Mesh{i}", ""), kv.get(f"Texture{i}", "")
        if not mesh and not tex:
            continue
        out.append({"index": i, "mesh": mesh, "texture": tex,
                    "mixTex": kv.get(f"MixTex{i}", "0"), "raw": dict(kv)})
    return tuple(out)


class WeaponTables:
    """Every weapon table this install ships, loaded once.

    Loading 6609's `weaponmotion.dbc` is 392,368 rows and about a third of a
    second, so a caller resolving many weapons should build this once and
    pass it to `weapon_parts`.  There is no module-level cache on purpose: a
    cache keyed on a root would go stale the moment a mod is applied, and
    this project's whole workflow is applying mods.

    Which table is LIVE is decided by presence, not by preference: the
    compiled twin wins where one exists (`dbc.py`'s rule, from 5517 onward),
    and where there is none the plaintext is not a decoy but the table
    itself -- the exact inverse, and the reason `source` is mandatory.
    """

    def __init__(self, assets, install: str = ""):
        self.assets = assets
        self.install = install
        #: logical path -> True/False, exactly as `AssetRoot.locate` saw it.
        self.located: dict = {}
        self.appearance_source = ""
        self.appearances: dict = {}          # ident -> tuple of part dicts
        self.motion_ini: dict = {}           # ini key -> logical path
        self.motion_dbc = None               # dbc.Rsdb or None
        self.effects: dict = {}              # 3-digit head -> row dict
        self.skins: list = []                # cosmetics.json rows
        self.souls: dict = {}                # soul item id -> appearance id
        self._grammar = None
        self._unaddressable = None
        self._load()

    # -- helpers -----------------------------------------------------------
    def _has(self, logical: str) -> bool:
        if logical not in self.located:
            try:
                self.located[logical] = self.assets.locate(logical) is not None
            except Exception:
                self.located[logical] = False
        return self.located[logical]

    def _text(self, logical: str) -> str:
        return self.assets.read(logical).decode("latin-1", "replace")

    def ships(self, candidates) -> tuple:
        """The candidates this install actually ships, in preference order."""
        return tuple(c for c in candidates if self._has(c))

    # -- loading -----------------------------------------------------------
    def _load(self) -> None:
        import dbc

        for c in APPEARANCE_TABLES:
            if not self._has(c):
                continue
            if c.endswith(".dbc"):
                rows = dbc.read_mesh(self.assets.read(c))
                self.appearances = {str(k): _parts_from_mesh(v)
                                    for k, v in rows.items()}
            else:
                self.appearances = {
                    name: _parts_from_ini(kv) for name, kv in
                    _sections(self._text(c)).items()}
            self.appearance_source = c
            break

        if self._has(MOTION_TABLES[1]):
            self.motion_ini = _flat(self._text(MOTION_TABLES[1]))
        if self._has(MOTION_TABLES[0]):
            self.motion_dbc = dbc.Rsdb.parse(self.assets.read(MOTION_TABLES[0]))

        if self._has(EFFECT_TABLES[0]):
            self.effects = _sections(self._text(EFFECT_TABLES[0]))

        if self._has(SKIN_TABLES[0]):
            doc = json.loads(self.assets.read(SKIN_TABLES[0])
                             .decode("utf-8", "replace"))
            self.skins = doc if isinstance(doc, list) else []

        if self._has(SOUL_TABLE):
            self.souls = _flat(self._text(SOUL_TABLE))

    # -- the detected grammar, computed once and kept for inspection -------
    @property
    def grammar(self) -> Optional[MotionGrammar]:
        """The detected key grammar, or None when no motion table ships.

        Raises `MotionGrammarUndecided` if the install ships a motion table
        whose keys neither grammar explains.
        """
        if self._grammar is None and self.motion_ini:
            self._grammar = detect_motion_grammar(self.motion_ini,
                                                  self.appearances)
        return self._grammar

    @property
    def unaddressable_motion_rows(self) -> int:
        """Compiled motion rows this resolver cannot key.  0 when no twin.

        Counted, not assumed: every key the resolver could ever compose --
        each appearance in the live table, the detected middle field, each
        action the plaintext demonstrates -- plus every key the plaintext
        itself spells, re-keyed through `dbc.weaponmotion_key`.  What is left
        over is the part of the twin that no validated composition reaches,
        and it is the reason `partial` exists.

        MEASURED on 6609, 2026-08-17: **384,861 of 392,368 rows (98.1%)** --
        7,507 reachable.  The measurement document's 389,560 is the narrower
        ini-keys-only view (2,808 reachable); this counts the appearance
        sweep as well, which is what the resolver actually does, so it is the
        larger reachable set and the smaller remainder.  Both are true of the
        same table; they answer different questions and neither is the other.
        """
        if self.motion_dbc is None:
            return 0
        if self._unaddressable is None:
            import dbc
            g = self.grammar
            reached = set()
            keys = list(self.motion_ini)
            if g is not None:
                keys += [g.key(a, act) for a in self.appearances
                         for act in g.actions]
            for k in keys:
                try:
                    i = dbc.weaponmotion_key(k)
                except ValueError:
                    continue
                if i in self.motion_dbc.paths:
                    reached.add(i)
            self._unaddressable = len(self.motion_dbc) - len(reached)
        return self._unaddressable


# --------------------------------------------------------------------------
# the resolver
# --------------------------------------------------------------------------

def _appearance_facet(t: WeaponTables, app: Optional[str]) -> Facet:
    shipped = t.ships(APPEARANCE_TABLES)
    if not shipped:
        return Facet(Facet.UNAVAILABLE, note=(
            "this install ships none of " + ", ".join(APPEARANCE_TABLES)))
    rows = t.appearances.get(app) if app else None
    if not rows:
        return Facet(Facet.EMPTY, source=t.appearance_source, note=(
            f"{t.appearance_source} ships and carries "
            f"{len(t.appearances)} appearances; none of them is {app}"))
    return Facet(Facet.PRESENT, rows, source=t.appearance_source)


def _motion_facet(t: WeaponTables, app: Optional[str]) -> Facet:
    shipped = t.ships(MOTION_TABLES)
    if not shipped:
        return Facet(Facet.UNAVAILABLE, note=(
            "this install ships none of " + ", ".join(MOTION_TABLES)))
    if not t.motion_ini:
        # A compiled twin with no plaintext beside it: nothing demonstrates
        # the key grammar, so there is no validated way to address it.
        # No install reachable here is in this shape; stated as a refusal
        # path rather than a tested one.
        return Facet(Facet.UNAVAILABLE, note=(
            f"{MOTION_TABLES[0]} ships but {MOTION_TABLES[1]} does not, and "
            f"the plaintext is the only thing that demonstrates this "
            f"install's key grammar -- the compiled table cannot be "
            f"addressed without it"))
    import dbc

    g = t.grammar
    keys = [g.key(app, a) for a in g.actions] if app else []
    rows: list = []
    for k in keys:
        # Both tables are asked for every key, INDEPENDENTLY. Asking the
        # plaintext first and skipping the twin where it answered would let
        # a 2009 path shadow the live one and would under-report `source`.
        ini_v = t.motion_ini.get(k)
        dbc_v = (t.motion_dbc.paths.get(dbc.weaponmotion_key(k))
                 if t.motion_dbc is not None else None)
        if ini_v is None and dbc_v is None:
            continue
        held = tuple(c for c, v in ((MOTION_TABLES[0], dbc_v),
                                    (MOTION_TABLES[1], ini_v)) if v is not None)
        rows.append({"key": k,
                     # The compiled twin is the live table wherever one
                     # ships; the frozen plaintext is a 0.7% view of it.
                     "path": dbc_v if dbc_v is not None else ini_v,
                     "tables": held,
                     # MEASURED 2026-08-17, and it has never been true here.
                     # 6609 is the only declared install shipping both tables:
                     # 2,808 keys are answered by both, and they AGREE on all
                     # 2,808 -- zero disagreements. CCO ships no motion `.dbc`
                     # at all, so on the only deployment target this flag
                     # cannot fire by construction.
                     #
                     # Kept rather than deleted because a future install may
                     # ship a genuinely divergent twin, and the flag is how
                     # anyone would find out. But a reader must not take a
                     # False here as evidence the twins were compared and
                     # found equal on THIS install -- on CCO nothing was
                     # compared. Scope: this is ADDRESSABLE overlap only;
                     # 384,861 of the dbc's 392,368 rows are not reachable by
                     # any key composition validated on this project, and this
                     # measurement says nothing about them.
                     "twinDisagrees": bool(ini_v and dbc_v and ini_v != dbc_v)})
    answered = [c for c in shipped if any(c in r["tables"] for r in rows)]

    unaddressable = t.unaddressable_motion_rows
    probed = (f"probed {len(keys)} keys -- grammar {g.name} "
              f"(middle {g.middle!r}, {len(g.actions)} actions this install's "
              f"own {MOTION_TABLES[1]} demonstrates) -- against "
              + " and ".join(shipped))
    if not rows:
        note = probed + "; no row for this weapon"
        if unaddressable > 0:
            note += (f". Bounded: {unaddressable:,} of "
                     f"{len(t.motion_dbc):,} compiled rows are not "
                     f"addressable by any composition validated on this "
                     f"project, so this zero covers the addressable space "
                     f"only")
        return Facet(Facet.EMPTY, source=" + ".join(shipped), note=note)
    # Every table that actually spoke, not just the preferred one: on 6609
    # both can, and "which file answered" is the whole point of `source`.
    src = " + ".join(answered) if answered else shipped[0]
    if unaddressable > 0:
        return Facet(Facet.PARTIAL, rows, source=src, note=(
            f"{len(rows)} rows found; {unaddressable:,} of "
            f"{len(t.motion_dbc):,} compiled rows are not addressable by any "
            f"composition validated on this project, so this is a floor and "
            f"not a count"))
    return Facet(Facet.PRESENT, rows, source=src)


def _effect_facet(t: WeaponTables, app: Optional[str]) -> Facet:
    shipped = t.ships(EFFECT_TABLES)
    if not shipped:
        return Facet(Facet.UNAVAILABLE, note=(
            "this install ships none of " + ", ".join(EFFECT_TABLES)))
    head = (app or "")[:EFFECT_HEAD_DIGITS]
    row = t.effects.get(head)
    if not row:
        return Facet(Facet.EMPTY, source=shipped[0], note=(
            f"{shipped[0]} ships and carries {len(t.effects)} weapon-type "
            f"sections; none of them is {head!r}, the type this weapon's "
            f"appearance names. It is a per-TYPE table, never per-weapon"))
    return Facet(Facet.PRESENT, ({"head": head, **row},), source=shipped[0])


def _skin_facet(t: WeaponTables, weapon_id: str, app: Optional[str]) -> Facet:
    shipped = t.ships(SKIN_TABLES)
    if not shipped:
        return Facet(Facet.UNAVAILABLE, note=(
            f"this install ships no {SKIN_TABLES[0]}, and it has no other "
            f"dedicated weapon alt-skin table -- ItemTexture.ini is a garment "
            f"colour table (94 of its 1,966 sections are weapon appearances) "
            f"and Mesh0 grouping conflates the item quality ladder with "
            f"alternate skins"))
    want = {str(weapon_id)}
    if app:
        want.add(str(app))
    rows = tuple(r for r in t.skins if str(r.get("ItemId")) in want)
    if not rows:
        return Facet(Facet.EMPTY, source=shipped[0], note=(
            f"{shipped[0]} ships and carries {len(t.skins)} rows; none of "
            f"them names this weapon"))
    return Facet(Facet.PRESENT, rows, source=shipped[0])


def weapon_parts(root, weapon_id, *, tables: Optional[WeaponTables] = None,
                 install: str = "") -> WeaponParts:
    """Resolve one weapon id against one install.

    ``root`` is an install root (`Path` or `str`) or anything with
    `AssetRoot`'s `locate`/`read` pair -- an already-open `AssetRoot`, most
    usefully.  ``tables`` lets a batch caller load `WeaponTables` once and
    reuse it; when it is given, ``root`` is not consulted at all and may be
    None, because the tables already carry the install they came from.

    Read `WeaponParts.presence` first, then each facet's `status` before its
    `rows`.  Both guards raise rather than handing back an ambiguous empty
    sequence; see `Facet`.
    """
    if tables is None:
        assets = root
        if not (hasattr(root, "locate") and hasattr(root, "read")):
            import coassets
            assets = coassets.AssetRoot(root)
        if not install:
            try:
                import coroot
                install = coroot.base_id(getattr(assets, "root", root))
            except Exception:
                install = ""
        tables = WeaponTables(assets, install)
    install = install or tables.install

    wid = str(weapon_id).strip()
    # 6609's soul items: the id and its appearance genuinely differ on 244 of
    # 331 rows. Identity everywhere else, including all of CCO.
    app = tables.souls.get(wid, wid)

    known = app in tables.appearances or wid in tables.souls
    if not tables.appearances and not tables.souls:
        return WeaponParts(
            wid, WeaponParts.UNDETERMINED, install, None, None,
            note=("no appearance table could be read here, so whether this "
                  "id is a weapon on this install is not established"))
    if not known:
        return WeaponParts(
            wid, WeaponParts.NOT_ON_INSTALL, install, None, None,
            note=(f"{tables.appearance_source or 'the appearance table'} "
                  f"carries {len(tables.appearances)} appearances and this "
                  f"id is not among them"))

    facets = {
        "appearance": _appearance_facet(tables, app),
        "motions": _motion_facet(tables, app),
        "effects": _effect_facet(tables, app),
        "skins": _skin_facet(tables, wid, app),
    }
    return WeaponParts(wid, WeaponParts.ON_INSTALL, install, app, facets)


def _check_constants() -> None:
    """`ACTION_DIGITS` must agree with `dbc`'s, or the two silently diverge."""
    try:
        import dbc
    except ImportError:                                       # pragma: no cover
        return
    if dbc.WEAPONMOTION_INI_ACTION_DIGITS != ACTION_DIGITS:
        raise ImportError(
            f"weaponparts.ACTION_DIGITS is {ACTION_DIGITS} but "
            f"dbc.WEAPONMOTION_INI_ACTION_DIGITS is "
            f"{dbc.WEAPONMOTION_INI_ACTION_DIGITS}. These name the same field "
            f"and a disagreement makes every motion key wrong.")


_check_constants()
