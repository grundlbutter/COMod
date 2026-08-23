#!/usr/bin/env python3
r"""
weaponswap.py -- if the owner brings this donor weapon to the target, what happens?

`displacement(donor_root, target_root, weapon_id)` answers that in one of four
words, and it is a **safety control**: its job is to stop a swap that would
destroy art the target already has.

    not_on_target   nothing on the target stands where this would land.
                    Safe, and "bring it" is the whole point.
    no_op           the art the target already shows is BYTE-IDENTICAL to the
                    art this would bring.  Bringing it changes nothing.
    replaces        the art differs.  This DESTROYS distinct target art.  WARN.
    undetermined    one or both sides could not be read.  NOT safe, and not a
                    warning-suppressor either.

WHY THIS EXISTS: TABLE AGREEMENT IS NOT ASSET IDENTITY
------------------------------------------------------
The swap page's first displacement warning triggered on **table-key equality**
-- does the target's `weapon` row name the same mesh id as the donor's.
`docs/donor_overlap_6609_to_cco_2026-08-17.md` §8 measured that trigger and it
is silent on nine tenths of the real overwrites.  **Independently re-derived
here, 2026-08-17, 6609 -> CCO, over all 4,718 shared weapon ids, comparing each
side's OWN resolved mesh:**

    tables disagree, bytes DIFFER             384     the old warning fires
    tables disagree, bytes SAME                 0     <- no counterexamples
    tables AGREE,    bytes DIFFER           3,762     the old warning is SILENT
    tables agree,    bytes same               362     the only genuine no-ops
    unreadable on one side                    210     undetermined
                                            -----
    comparable                              4,508
    real overwrites (bytes differ)          4,146

    a table-key trigger misses 3,762 of 4,146  =  90.7%

Both installs say *use mesh 1050000* and the file called `1050000.c3` is
different art on each (`37c0fbe5...` vs `5c6bdcc0...`).
`tests/test_weaponswap.py::TableKeyRegression` pins the 3,762, the 4,146 and
the 90.7% so that "optimising" this back to table keys states its own cost.

**The empty bucket is the stronger property and it is asserted too.**  Table
disagreement is a strict SUBSET of byte difference on this pair: there is no id
where the two tables name different meshes and the two meshes are the same art.
So the table-key predicate can never catch an overwrite this one misses -- it is
dominated, not merely coarser.  A non-zero count there would mean mesh
resolution had changed under someone, which is worth a red test.

(The document's §8 as first published split the same 4,718 as 351 / 3,762 /
362 / 243.  It compared the DONOR's mesh id on BOTH installs, which answers
nothing where the tables disagree because the target renders its own `Mesh0`.
The corrected decomposition is the one above and the 90.7% supersedes the
91.5% derived from it.)

**One id where this module's own answer differs from the table above, named
because a residual disagreement should be findable: `410302`.**  CCO's
`weapon.ini` gives it `Mesh0=0`, and `c3/mesh/0.c3` does not exist -- so a
resolved-mesh-only comparison excludes it as unreadable (it is one of the 210).
This module calls it `replaces`, because `c3/mesh/410302.c3` DOES exist on CCO
(4,453 B against the donor's 4,385 B) and that is where the write lands.  It is
the whole of the difference between 4,146 and this module's 4,147 shared
`replaces`, and it is a treatment decision about a row naming mesh `0`, not an
arithmetic slip.  Both counts are pinned separately in the tests, each in its
own frame.

Seven shared ids run the other way -- the tables disagree and this module says
no warning.  Five are `undetermined` and block anyway; the two that read as safe
(`800260`, `800270`) are honestly safe: the target's row names a mesh the target
does not ship, so there is nothing on screen to displace and nothing at the
write path either.

THE SECOND FALSE-SAFE, WHICH THE FOUR STATUSES DO NOT NAME
-----------------------------------------------------------
"The id is free on the target" is the obvious reading of `not_on_target`, and
it is **not sufficient**.  A donor weapon the target has never heard of still
writes its mesh file at `c3/mesh/<mesh>.c3`, and that path can already be
occupied by art some *other* target weapon uses.  **Measured over the 8,574
donor ids absent from CCO's appearance table:**

    not_on_target   8,346    the path is free too. genuinely safe.
    replaces          140    the write lands on different target art
    undetermined       78    the donor's own mesh could not be read
    no_op              10    the path is occupied by byte-identical art

An id-presence rule reports all 8,574 as safe.  **140 of them destroy target
art and 78 are unknowable.**  So `not_on_target` is decided on the ASSET, never
on the id:

    target side  =  the art the target's own row resolves to, when it has a row
                    (that is what the swap displaces on screen), AND
                    whatever sits at the path the write lands on, when the
                    donor's mesh id differs from the target's
    not_on_target  <=>  neither of those exists

`weapon_id` presence is carried on the result as `weapon_on_target` for the UI,
and it decides nothing.  The 10 `no_op`s above are the case that shows the two
apart: the id is new to the target and the art is already there, so the swap
adds a row and overwrites nothing -- `no_op` at the asset layer, with
`weapon_on_target` False so a page can still say "new id, art already present".

WHY MESHES ONLY -- MEASURED, NOT INHERITED
-------------------------------------------
The Director ruled *byte-identity of the resolved mesh, nothing cheaper*.
Adding the resolved TEXTURE would be stricter, not cheaper, so the ruling does
not forbid it and `docs/donor_overlap...` §6 warns that a shared mesh with a
differing texture is not a no-op.  **Run both ways over the 4,718 shared ids,
on the resolved-mesh comparison:**

    predicate                  no_op    replaces   undetermined
    mesh only                    362       4,146          210
    mesh + texture               218       4,124          376

**Including textures produced ZERO additional warnings.**  All 144 ids it moved
out of `no_op` went to `undetermined` -- 6609 cannot read the texture (23 of 200
sampled shared ids), not because the texture differs.  There is no id on this
pair whose mesh is byte-identical and whose texture is not.  So textures cost
144 honest answers and bought nothing, and this module compares meshes.

**That is a fact about one install pair and it is the weak claim here.**  A pair
where mesh-identical/texture-different weapons exist would make this predicate
report a false `no_op`, and nothing in this module would notice.  If textures
are ever added, `no_op` can only shrink -- the change is monotone in the safe
direction, which is why it is safe to defer and not safe to forget.

WHY A STATUS OBJECT AND NOT A BOOLEAN
--------------------------------------
`undetermined` is the load-bearing status and it is not hypothetical: 210 of
4,718 shared ids on this pair have a mesh that cannot be read on one side.  A
control whose failure mode is "reads as safe" is worse than no control, so
every route by which `undetermined` could be mistaken for safe is closed
structurally rather than by convention:

1. `Displacement.__bool__` **raises**.  `if d:` cannot stand in for a check.
2. `status` must be read before `donor`/`target`/`comparisons` (`StatusNotRead`),
   the same arming discipline `weaponparts.Facet` uses and for the same reason.
3. `MeshRef.digest` **raises `Undetermined`** on a side that was not read.
   There is no `None` to compare, so `a.digest == b.digest` cannot return True
   for two failures.  This is the one that matters: `None == None` is the
   classic vacuous safe.
4. `as_dict()` serialises an unread side as `unreadable/<side>/<logical>` --
   a string that **carries the side's name**, so the donor's and the target's
   can never be equal to each other in any language, and neither can ever equal
   a 64-character hex digest.  The `null == null` trap does not survive into
   the UI.
5. `BLOCKING` -- the only grouping constant this module publishes -- contains
   `undetermined`.  **There is deliberately no `SAFE` tuple**: a caller writing
   `status in SAFE` fails closed on any status added later, and a caller writing
   `status in WARN` would not.  `blocks_swap` is the accessor to use.
6. `as_dict()` always emits `blocksSwap`, so a UI that reads one key gets the
   fail-closed one.

`undetermined` also cannot be reached by accident from the constructor: like
`Facet`, a non-decided status must carry a `note` saying why.

PERFORMANCE, WITHOUT WEAKENING THE PREDICATE
---------------------------------------------
Hashing thousands of meshes per page render is too slow, so digests are cached
-- and a cache is the only concession made.  Entries live in
``out/weaponswap/meshdigests.json``, which `coroot.PER_BASE` rewrites into
``out/indexes/<kind>-<fingerprint>/``, so an entry cannot be served for the
install it did not come from.  **There is no unkeyed fallback**: when
`coroot.base_id` degrades to ``unkeyed`` the cache is switched OFF rather than
written to a shared directory, because two broken installs sharing one namespace
is the failure the key exists to prevent.

`base_fingerprint` hashes only the top level of ``ini/``, and **a mesh write does
not move it** -- which matters here more than anywhere, because the swap page
writes meshes.  So each entry additionally carries the size and mtime of the
file that backs it (the loose file, or the archive), exactly as `dcache.key_for`
does, and a mismatch is a miss.  Validating an entry costs one `AssetRoot.locate`
and one `stat`; it never costs a read.

**MEASURED, 2026-08-17, 6609 -> CCO, medians of five ALTERNATING cold/warm runs
in fresh processes** -- alternating rather than one-then-the-other, because
`dcache`'s docstring records a 5.6x that was really 2.4x for exactly that
mistake:

    SwapView build (both appearance tables)   cold  611 ms   warm  608 ms
    first displacement                        cold   87 ms   warm  112 ms
    500 ids                                   cold   86 ms   warm   65 ms
    all 13,292 donor ids                      cold  682 ms   warm  410 ms
    the same 500 again, in process            cold  3.6 ms   warm  3.6 ms
    flush                                     cold   93 ms   warm    0 ms

    full sweep, end to end                    cold 1,386 ms  warm 1,018 ms  1.36x

**Read the small numbers, not the ratio.**  What the disk cache actually buys is
the sha256 of the **998 distinct meshes** the 13,292 ids resolve to -- 272 ms,
0.27 ms each.  Everything else is unchanged, and the first displacement is
*slower* warm because it reads the 138 KB index.  **What REMAINS is now the
larger half**: 610 ms of appearance-table load and ~410 ms of
`AssetRoot.locate` across 13,292 ids.  Anyone chasing this further should start
there and not here.

The in-process memo is the one that matters for a page that asks twice: a
repeated id is 0.007 ms, and `tests/test_weaponswap.py::Timing` asserts it costs
no additional read rather than asserting a duration, because a threshold here
would be a flake.

A cache is allowed to be faster and never to be a second predicate:
`CacheKeying.test_a_hit_and_a_miss_produce_the_same_answer` compares a cold view
against a warm one field for field.

READ-ONLY ON EVERY INSTALL
---------------------------
Both installs are opened through `coassets.AssetRoot` and only `locate` and
`read` are ever called.  The single path this module writes to is
`coroot.derived_path`, inside the checkout.  `tests/test_weaponswap.py::ReadOnly`
asserts both.

Pure COre: imports `coassets`, `coroot` and `weaponparts` and the standard
library.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coroot                                            # noqa: E402
import weaponparts                                       # noqa: E402
from weaponparts import StatusNotRead                    # noqa: E402,F401

__all__ = [
    "StatusNotRead", "Undetermined", "NotADonor",
    "MeshRef", "Comparison", "Displacement", "MeshDigests", "SwapView",
    "displacement", "MESH_LOGICAL", "REL",
    "ROLE_RESOLVED", "ROLE_WRITE_PATH",
]

#: Where a weapon mesh lives, on every install this project has opened.  The
#: `AssetRoot` layer resolves it through loose files first and the archives
#: after, so the same string works whether the art was modded in or shipped.
MESH_LOGICAL = "c3/mesh/{}.c3"

#: The cache, relative to the checkout.  Under ``out/`` so `.gitignore` covers
#: it, and declared in `coroot.PER_BASE` so `coroot.derived_rel` rewrites it
#: into ``out/indexes/<base-id>/``.  Both halves are load-bearing -- see
#: `dcache`'s docstring for the 10.4 MB commit that established the first and
#: the module docstring above for the second.
REL = "out/weaponswap/meshdigests.json"

#: The target-side art the swap displaces **on screen**: what the target's own
#: appearance row resolves to today.
ROLE_RESOLVED = "resolved"
#: The target-side file the swap displaces **on disk**: whatever already sits at
#: the logical path the donor's mesh is written to.  Only ever compared when it
#: is a different path from `ROLE_RESOLVED`; it is the 92-id false-safe the
#: module docstring measures.
ROLE_WRITE_PATH = "write_path"


class Undetermined(LookupError):
    """A digest was reached on a side that could not be read.

    Raised rather than returning ``None``, because ``None == None`` is ``True``
    and a caller comparing two failed reads would conclude the art matches --
    a false `no_op` produced by the very failure that should have blocked the
    swap.  There is no value here to compare, so there is nothing to get wrong.
    """


class NotADonor(LookupError):
    """This id is not a weapon on the DONOR install; there is nothing to bring.

    Deliberately **not** a fifth status.  The four statuses answer *what happens
    to the target*, and this is a fact about the donor -- folding it in would
    let "there is nothing to copy" and "there is nothing in the way" share a
    word, which is the exact conflation `weaponparts` was written to end.

    A page that lists the donor's own weapons never reaches this.  A page that
    takes a typed id does, and it should say so rather than render a status.
    """


# --------------------------------------------------------------------------
# one side of one comparison
# --------------------------------------------------------------------------

class MeshRef:
    """One install's answer for one mesh: which file, and what it hashes to.

    Frozen, slotted, and `digest` is a property with a guard -- the same
    construction `weaponparts.Facet` uses, for the same reason: the attribute a
    caller most wants is the one that is meaningless in three of the four
    worlds this object models.

        read     the file was located and hashed.  `digest` is a sha256 hex.
        absent   `AssetRoot.locate` found nothing at this logical path.
        error    it located and could not be read.  `reason` says what happened.

    `absent` and `error` are separate because they mean opposite things on the
    two sides: an absent file on the TARGET is the good case (nothing to
    displace) and an absent file on the DONOR means the swap has nothing to
    write.  A single "no digest" would have merged them.
    """

    __slots__ = ("_side", "_install", "_mesh_id", "_logical", "_state",
                 "_source", "_size", "_digest", "_reason")

    READ = "read"
    ABSENT = "absent"
    ERROR = "error"
    STATES = (READ, ABSENT, ERROR)

    DONOR = "donor"
    TARGET = "target"

    def __init__(self, side: str, install: str, mesh_id: Optional[str],
                 logical: str, state: str, source: str = "", size: int = -1,
                 digest: Optional[str] = None, reason: str = ""):
        if side not in (self.DONOR, self.TARGET):
            raise ValueError(f"{side!r} is not {self.DONOR!r} or {self.TARGET!r}")
        if state not in self.STATES:
            raise ValueError(f"{state!r} is not one of {self.STATES}")
        if state == self.READ and not digest:
            raise ValueError(
                "a 'read' MeshRef must carry a digest; without one it is an "
                "'error' and calling it 'read' is how an unread side becomes a "
                "silent match")
        if state != self.READ and digest:
            raise ValueError(
                f"a {state!r} MeshRef must not carry a digest -- there is "
                f"nothing it could be the digest of")
        if state != self.READ and not reason:
            raise ValueError(
                f"a {state!r} MeshRef must say why in `reason`; that sentence "
                f"is what stops 'could not look' from reading as 'nothing "
                f"there'")
        object.__setattr__(self, "_side", side)
        object.__setattr__(self, "_install", str(install))
        object.__setattr__(self, "_mesh_id",
                           None if mesh_id is None else str(mesh_id))
        object.__setattr__(self, "_logical", str(logical))
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_source", str(source))
        object.__setattr__(self, "_size", int(size))
        object.__setattr__(self, "_digest", digest)
        object.__setattr__(self, "_reason", str(reason))

    def __setattr__(self, name, value):
        raise AttributeError(f"MeshRef is frozen; {name!r} cannot be reassigned")

    def __delattr__(self, name):
        raise AttributeError("MeshRef is frozen")

    # -- identity, always readable ----------------------------------------
    @property
    def side(self) -> str:
        """`donor` or `target`.  Part of the unreadable sentinel, so that two
        failed reads can never serialise to the same string."""
        return self._side

    @property
    def install(self) -> str:
        return self._install

    @property
    def mesh_id(self) -> Optional[str]:
        """The mesh id this side's table named, or None when it named none."""
        return self._mesh_id

    @property
    def logical(self) -> str:
        """The logical path that was asked for -- which mesh was compared."""
        return self._logical

    @property
    def state(self) -> str:
        return self._state

    @property
    def readable(self) -> bool:
        return self._state == self.READ

    @property
    def source(self) -> str:
        """Which file answered: ``loose``, ``overlay``, or an archive name.

        The UI question "which file answered on each side" -- on this pair the
        same logical path is served from a loose file on one install and out of
        `c3.wdf` on the other, and that difference is most of the story.
        """
        return self._source

    @property
    def size(self) -> int:
        """Bytes as `AssetRoot.locate` reported them, or -1 when absent."""
        return self._size

    @property
    def reason(self) -> str:
        return self._reason

    # -- the guarded one ---------------------------------------------------
    @property
    def digest(self) -> str:
        """sha256 of the mesh bytes.  Raises `Undetermined` when unread.

        Never ``None``.  See `Undetermined`.
        """
        if self._state != self.READ:
            raise Undetermined(
                f"the {self._side} side of {self._logical} is {self._state!r} "
                f"({self._reason}) -- there is no digest. Comparing a missing "
                f"digest to another missing digest would report a match, which "
                f"is why this raises instead of returning None. Check "
                f".readable, or read the Displacement's .status.")
        return self._digest

    def unreadable_token(self) -> str:
        """The serialised stand-in for a digest that does not exist.

        Carries the side, so the donor's and the target's are never equal to
        each other; contains ``/``, so neither can ever equal a hex digest.
        """
        return f"unreadable/{self._side}/{self._logical}"

    def as_dict(self) -> dict:
        return {"side": self._side, "install": self._install,
                "meshId": self._mesh_id, "logical": self._logical,
                "state": self._state, "readable": self._state == self.READ,
                "source": self._source, "size": self._size,
                "reason": self._reason,
                "digest": (self._digest if self._state == self.READ
                           else self.unreadable_token())}

    def __repr__(self) -> str:
        return (f"MeshRef({self._side!r}, {self._logical!r}, "
                f"state={self._state!r}, source={self._source!r})")

    def __eq__(self, other) -> bool:
        if not isinstance(other, MeshRef):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.as_dict().items(), key=lambda kv: kv[0])))


class Comparison:
    """One donor mesh against one thing on the target, and the verdict.

    `role` is `ROLE_RESOLVED` (what the target's weapon shows today) or
    `ROLE_WRITE_PATH` (what already sits where the file lands).  A weapon can
    produce both, and then the worst one decides -- see `Displacement`.
    """

    __slots__ = ("_role", "_part", "_donor", "_target", "_status", "_note")

    def __init__(self, role: str, part: int, donor: MeshRef,
                 target: Optional[MeshRef], status: str, note: str = ""):
        object.__setattr__(self, "_role", str(role))
        object.__setattr__(self, "_part", int(part))
        object.__setattr__(self, "_donor", donor)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_status", str(status))
        object.__setattr__(self, "_note", str(note))

    def __setattr__(self, name, value):
        raise AttributeError("Comparison is frozen")

    @property
    def role(self) -> str:
        return self._role

    @property
    def part(self) -> int:
        """Which appearance part this is.  Every weapon on both declared
        installs has exactly one (13,292/13,292 and 5,384/5,384, measured), so
        this is always 0 in practice -- and it is carried anyway, because
        reading `parts[0]` and calling it "the mesh" is how a second part gets
        silently dropped out of a safety check."""
        return self._part

    @property
    def donor(self) -> MeshRef:
        return self._donor

    @property
    def target(self) -> Optional[MeshRef]:
        """The target side, or None when nothing stands there at all."""
        return self._target

    @property
    def status(self) -> str:
        return self._status

    @property
    def note(self) -> str:
        return self._note

    def as_dict(self) -> dict:
        return {"role": self._role, "part": self._part,
                "status": self._status, "note": self._note,
                "donor": self._donor.as_dict(),
                "target": None if self._target is None
                          else self._target.as_dict()}

    def __repr__(self) -> str:
        return (f"Comparison({self._role!r}, part={self._part}, "
                f"status={self._status!r})")


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------

class Displacement:
    """What happens to the target if this donor weapon is brought across.

    Read `status` first; it arms the payload.  `blocks_swap` is the accessor a
    destructive-action control should use, because it is True for
    `undetermined` as well as for `replaces`.

    See the module docstring for the six structural reasons `undetermined`
    cannot be mistaken for safe.
    """

    __slots__ = ("_weapon_id", "_status", "_donor_install", "_target_install",
                 "_comparisons", "_deciding", "_on_target", "_note", "_armed",
                 "_donor_appearance", "_target_appearance")

    NOT_ON_TARGET = "not_on_target"
    NO_OP = "no_op"
    REPLACES = "replaces"
    UNDETERMINED = "undetermined"
    #: The four, in the order the module docstring introduces them.
    STATUSES = (NOT_ON_TARGET, NO_OP, REPLACES, UNDETERMINED)

    #: Everything that must stop or qualify a swap.  **`undetermined` is in
    #: here and that is the point.**  There is deliberately no `SAFE` tuple:
    #: a caller who writes ``status in BLOCKING`` fails CLOSED when a fifth
    #: status is added, and one who writes ``status in SAFE`` would too --
    #: but one who writes ``status not in BLOCKING`` is at least visibly
    #: inverting a set named "blocking", where ``status in SAFE`` reads as
    #: correct while quietly omitting whatever is new.
    BLOCKING = (REPLACES, UNDETERMINED)

    #: Which status wins when a weapon produces several comparisons.  Worst
    #: first: an unreadable part must never be silenced by a readable one.
    PRECEDENCE = (UNDETERMINED, REPLACES, NO_OP, NOT_ON_TARGET)

    def __init__(self, weapon_id: str, status: str, donor_install: str = "",
                 target_install: str = "", comparisons: Iterable = (),
                 deciding: int = -1, on_target: Optional[bool] = None,
                 note: str = "", donor_appearance: Optional[str] = None,
                 target_appearance: Optional[str] = None):
        if status not in self.STATUSES:
            raise ValueError(
                f"{status!r} is not one of {self.STATUSES}. A fifth status is a "
                f"design change, not a spelling -- and it must be classified "
                f"into BLOCKING or not in the same edit.")
        comparisons = tuple(comparisons)
        if status != self.NOT_ON_TARGET and not comparisons:
            raise ValueError(
                f"a {status!r} result must carry the comparison it was decided "
                f"by; a verdict with no evidence is not checkable")
        if status != self.NO_OP and not note:
            raise ValueError(
                f"a {status!r} result must say why in `note` -- that sentence "
                f"is the difference between 'nothing is in the way' and 'I "
                f"could not look'")
        object.__setattr__(self, "_weapon_id", str(weapon_id))
        object.__setattr__(self, "_status", status)
        object.__setattr__(self, "_donor_install", str(donor_install))
        object.__setattr__(self, "_target_install", str(target_install))
        object.__setattr__(self, "_comparisons", comparisons)
        object.__setattr__(self, "_deciding", int(deciding))
        object.__setattr__(self, "_on_target", on_target)
        object.__setattr__(self, "_note", str(note))
        object.__setattr__(self, "_donor_appearance", donor_appearance)
        object.__setattr__(self, "_target_appearance", target_appearance)
        object.__setattr__(self, "_armed", False)

    def __setattr__(self, name, value):
        raise AttributeError(f"Displacement is frozen; {name!r} is fixed.")

    def __delattr__(self, name):
        raise AttributeError("Displacement is frozen")

    # -- identity, free to read -------------------------------------------
    @property
    def weapon_id(self) -> str:
        return self._weapon_id

    @property
    def donor_install(self) -> str:
        return self._donor_install

    @property
    def target_install(self) -> str:
        return self._target_install

    @property
    def note(self) -> str:
        return self._note

    @property
    def donor_appearance_id(self) -> Optional[str]:
        """The appearance the donor's item id resolves to, which is not always
        its own number: 6609's `SoulPart.ini` maps 244 soul items to somebody
        else's appearance (`800001` -> `410019`).  Carried so a page can show
        WHY a mesh id looks unrelated to the weapon it was asked about."""
        return self._donor_appearance

    @property
    def target_appearance_id(self) -> Optional[str]:
        return self._target_appearance

    @property
    def weapon_on_target(self) -> Optional[bool]:
        """Does the target's appearance table carry this weapon id at all?

        For the UI, and it **decides nothing**: 92 donor ids absent from CCO's
        table still land on an occupied mesh path whose bytes differ.  See the
        module docstring.  None when the target's table could not be read.
        """
        return self._on_target

    # -- the guarded pair --------------------------------------------------
    @property
    def status(self) -> str:
        """One of `STATUSES`.  Reading this arms the payload."""
        object.__setattr__(self, "_armed", True)
        return self._status

    @property
    def blocks_swap(self) -> bool:
        """True when the swap must be stopped or qualified.

        True for `replaces` AND for `undetermined`.  This is the accessor a
        control should use; `__bool__` raises precisely so that this one has to
        be named.
        """
        return self.status in self.BLOCKING

    def _payload(self, what: str):
        if not self._armed:
            raise StatusNotRead(
                f"read .status before .{what}. It is {self._status!r}"
                + (f" ({self._note})" if self._note else "")
                + " -- and on an 'undetermined' result the digests behind this "
                  "raise rather than compare. `unpack()` gives you the status "
                  "and both sides at once.")

    @property
    def comparisons(self) -> tuple:
        """Every comparison made, in the order they were made."""
        self._payload("comparisons")
        return self._comparisons

    @property
    def deciding(self) -> Optional[Comparison]:
        """The comparison the status came from, or None for `not_on_target`."""
        self._payload("deciding")
        if self._deciding < 0:
            return None
        return self._comparisons[self._deciding]

    @property
    def donor(self) -> Optional[MeshRef]:
        """The donor mesh the status was decided on."""
        self._payload("donor")
        d = self.deciding
        return None if d is None else d.donor

    @property
    def target(self) -> Optional[MeshRef]:
        """The target mesh the status was decided on, or None.

        None only when there is no deciding comparison at all -- the two
        `undetermined` results raised by an unreadable appearance table, where
        no mesh was ever looked up.  It never means "nothing was there" (that is
        a `MeshRef` in state `absent`) and it never means "could not read" (a
        `MeshRef` in state `error`, whose `digest` raises).
        """
        self._payload("target")
        d = self.deciding
        return None if d is None else d.target

    # -- the routes that cannot be got wrong -------------------------------
    def unpack(self) -> tuple:
        """`(status, donor, target)` in one call.  The recommended accessor."""
        st = self.status
        return st, self.donor, self.target

    def require(self, *statuses: str):
        """Return self, but only when the status is one you named."""
        st = self.status
        if st not in statuses:
            raise ValueError(
                f"{self._weapon_id} is {st!r}"
                + (f" ({self._note})" if self._note else "")
                + f", not one of {statuses!r}")
        return self

    def as_dict(self) -> dict:
        """A plain dict that always carries `status` and `blocksSwap`.

        Unreadable sides serialise as `MeshRef.unreadable_token`, never as
        ``null`` -- so the `null == null` match cannot be reconstructed in a
        consumer that never saw this class.
        """
        return {
            "weaponId": self._weapon_id,
            "status": self._status,
            "blocksSwap": self._status in self.BLOCKING,
            "donorInstall": self._donor_install,
            "targetInstall": self._target_install,
            "weaponOnTarget": self._on_target,
            "donorAppearanceId": self._donor_appearance,
            "targetAppearanceId": self._target_appearance,
            "note": self._note,
            "deciding": self._deciding,
            "comparisons": [c.as_dict() for c in self._comparisons],
        }

    # -- everything below must not arm, and must not be vacuous ------------
    def __repr__(self) -> str:
        return (f"Displacement({self._weapon_id!r}, status={self._status!r}, "
                f"{self._donor_install!r} -> {self._target_install!r})")

    def __bool__(self):
        raise StatusNotRead(
            "a Displacement has no truth value -- `if d:` would be true for an "
            "'undetermined' one, which is the exact false-safe this class "
            "exists to prevent. Read .status, or .blocks_swap.")

    def __eq__(self, other) -> bool:
        if not isinstance(other, Displacement):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.as_dict(), sort_keys=True))


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------

class MeshDigests:
    """sha256 of one install's mesh files, memoised in process and on disk.

    The disk half lives at `coroot.derived_path(REL, root)`, which
    `coroot.PER_BASE` rewrites into ``out/indexes/<base-id>/weaponswap/``.  See
    the module docstring: there is no unkeyed fallback, and an entry is
    additionally stamped with the size and mtime of the file that backs it,
    because `base_fingerprint` reads only ``ini/`` and a mesh write does not
    move it.

    Nothing here is load-bearing.  Every failure -- unwritable checkout, corrupt
    JSON, an install with no fingerprint -- degrades to recomputing, never to a
    wrong answer.
    """

    #: Schema marker.  A file written by an older shape is discarded rather
    #: than half-read; the whole thing is a cache and rebuilding is free.
    VERSION = 1

    def __init__(self, assets, root=None, base_id: Optional[str] = None,
                 use_disk: bool = True):
        self.assets = assets
        self.root = Path(root if root is not None
                         else getattr(assets, "root", "."))
        if base_id is None:
            try:
                base_id = coroot.base_id(self.root)
            except Exception:
                base_id = "unkeyed"
        self.base_id = str(base_id)
        #: **No unkeyed fallback.**  `coroot.base_id` degrades to ``unkeyed``
        #: when the fingerprint cannot be taken, and every install that
        #: degrades would share that one directory.  A cache that can serve one
        #: install's digests for another is worse than no cache at all here, so
        #: this switches the disk half off instead.
        self.disk_ok = bool(use_disk) and self.base_id != "unkeyed"
        self._mem: dict = {}          # logical -> MeshRef payload tuple
        self._disk: Optional[dict] = None
        self._dirty = False
        self._stamps: dict = {}       # backing file -> "size:mtime_ns"
        self.hits = 0
        self.misses = 0

    # -- where -------------------------------------------------------------
    def path(self) -> Optional[Path]:
        """The keyed cache file, or None when it must not be written."""
        if not self.disk_ok:
            return None
        try:
            return coroot.derived_path(REL, self.root)
        except Exception:
            return None

    # -- staleness ---------------------------------------------------------
    def _stamp(self, loc) -> str:
        """``"<size>:<mtime_ns>"`` of the file backing `loc`, or ``""``.

        A loose or overlay hit is backed by itself; an archive hit is backed by
        the archive, which is named by `Located.source` and sits at the install
        root.  An empty stamp means the entry cannot be validated, and an entry
        that cannot be validated is never stored -- fail closed, recompute.
        """
        p = loc.real_path if loc.real_path is not None \
            else (self.root / loc.source)
        key = str(p)
        if key not in self._stamps:
            try:
                st = p.stat()
                self._stamps[key] = f"{st.st_size}:{st.st_mtime_ns}"
            except OSError:
                self._stamps[key] = ""
        return self._stamps[key]

    def _load(self) -> dict:
        if self._disk is not None:
            return self._disk
        self._disk = {}
        p = self.path()
        if p is None:
            return self._disk
        try:
            doc = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            return self._disk
        # A file that does not name THIS base is discarded rather than trusted.
        # `derived_path` already keys the directory; this is the second lock,
        # and it is the one that survives someone moving the tree by hand.
        if (not isinstance(doc, dict)
                or doc.get("version") != self.VERSION
                or doc.get("baseId") != self.base_id):
            return self._disk
        entries = doc.get("entries")
        if isinstance(entries, dict):
            self._disk = {k: v for k, v in entries.items()
                          if isinstance(v, list) and len(v) == 4}
        return self._disk

    def flush(self) -> Optional[Path]:
        """Write the disk half if anything changed.  Returns the path, or None.

        Written to a temp name and renamed, so an interrupted run never leaves
        a half file for the next one to read as complete -- `dcache.put`'s rule.
        """
        if not self._dirty:
            return None
        p = self.path()
        if p is None:
            return None
        doc = {"version": self.VERSION, "baseId": self.base_id,
               "entries": self._load()}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc), "utf-8")
            tmp.replace(p)
            self._dirty = False
            return p
        except OSError:
            return None

    # -- the one thing this class does ------------------------------------
    def ref(self, side: str, install: str, mesh_id: Optional[str],
            logical: str) -> MeshRef:
        """Locate, hash (or recall), and describe one mesh on this install.

        The memo holds the FACTS about the file; the sentence is composed per
        call, because it has to name the side.  Two absent meshes at the same
        path on the two installs otherwise produce the identical reason twice,
        and the `undetermined` note reads as one failure repeated rather than
        as two -- which is the same "neither side can be told apart" the digest
        sentinel is guarding against, one layer up in the prose.
        """
        cached = self._mem.get(logical)
        if cached is None:
            cached = self._resolve(logical)
            self._mem[logical] = cached
        state, source, size, dig, detail = cached
        return MeshRef(side, install, mesh_id, logical, state, source, size,
                       dig, self._reason(side, install, logical, state, detail))

    @staticmethod
    def _reason(side: str, install: str, logical: str, state: str,
                detail: str) -> str:
        if state == MeshRef.READ:
            return ""
        where = f"the {side} install" + (f" ({install})" if install else "")
        if state == MeshRef.ABSENT:
            return f"no {logical} on {where}"
        return f"{logical} on {where} could not be read: {detail}"

    def _resolve(self, logical: str) -> tuple:
        """`(state, source, size, digest, detail)` -- the facts, no prose."""
        try:
            loc = self.assets.locate(logical)
        except Exception as exc:                          # pragma: no cover
            return (MeshRef.ERROR, "", -1, None,
                    f"locate failed, {type(exc).__name__}: {exc}")
        if loc is None:
            return (MeshRef.ABSENT, "", -1, None, "")

        stamp = self._stamp(loc)
        entry = self._load().get(logical) if stamp else None
        if entry is not None and entry[0] == loc.source \
                and entry[1] == loc.size and entry[2] == stamp:
            self.hits += 1
            return (MeshRef.READ, loc.source, loc.size, entry[3], "")

        self.misses += 1
        try:
            dig = hashlib.sha256(self.assets.read(logical)).hexdigest()
        except Exception as exc:
            return (MeshRef.ERROR, loc.source, loc.size, None,
                    f"located in {loc.source}, {type(exc).__name__}: {exc}")

        if stamp:
            self._load()[logical] = [loc.source, loc.size, stamp, dig]
            self._dirty = True
        return (MeshRef.READ, loc.source, loc.size, dig, "")


# --------------------------------------------------------------------------
# the predicate
# --------------------------------------------------------------------------

def _parts(tables, weapon_id: str):
    """`(appearance_id, parts, on_install)` for `weapon_id` on `tables`.

    `parts is None` means the install could not answer at all -- it ships no
    appearance table -- and is the `undetermined` shape.  An empty tuple means
    the table answered and names no art for this id.

    **This resolves the appearance directly rather than through
    `weaponparts.weapon_parts`, and that is a deliberate narrowing.**
    `weapon_parts` builds all four surfaces, and the motions one touches
    `WeaponTables.unaddressable_motion_rows` -- a lazy property that composes
    every key over 13,292 appearances and costs **124 ms, billed to whichever
    caller happens to be first**, plus ~16 us per call for three facets a
    displacement check never reads.  Measured 2026-08-17: dropping it takes a
    13,292-id sweep from ~1.31 s to ~0.97 s.  A safety control paying for a
    motion-table bound it does not look at is the shape the memory index calls
    *lazy properties bill the first one touched for the whole shared build*.

    The presence rule is `weapon_parts`'s own, restated: the soul indirection
    first (6609 maps 244 soul ids to an appearance that is not their own
    number), then the appearance table.  Restating it is a drift risk, and
    `tests/test_weaponswap.py::MatchesWeaponParts` closes it by asserting the
    two agree on every live id of both installs.
    """
    wid = str(weapon_id).strip()
    if not tables.ships(weaponparts.APPEARANCE_TABLES):
        return None, None, False
    if not tables.appearances and not tables.souls:
        return None, None, False
    app = tables.souls.get(wid, wid)
    on_install = app in tables.appearances or wid in tables.souls
    return app, tuple(tables.appearances.get(app) or ()), on_install


def _verdict(donor: MeshRef, target: Optional[MeshRef]) -> str:
    if not donor.readable:
        return Displacement.UNDETERMINED
    if target is None or target.state == MeshRef.ABSENT:
        return Displacement.NOT_ON_TARGET
    if not target.readable:
        return Displacement.UNDETERMINED
    return (Displacement.NO_OP if donor.digest == target.digest
            else Displacement.REPLACES)


class SwapView:
    """Both installs, opened once, for a page that asks about many weapons.

    The appearance tables are the fixed cost of a swap page -- 6609's compiled
    `weapon.dbc` is 13,292 rows -- so a caller resolving more than one weapon
    builds this once.  `displacement()` (module level) is the one-shot form and
    builds a throwaway view.

    Call `flush()` when the page is done, or use it as a context manager; the
    disk cache is only written there, so an interrupted run costs a rebuild and
    never a stale entry.
    """

    def __init__(self, donor_root, target_root, *, donor_install: str = "",
                 target_install: str = "", use_disk: bool = True):
        self.donor_assets = _assets(donor_root)
        self.target_assets = _assets(target_root)
        self.donor_install = donor_install or _install_id(self.donor_assets)
        self.target_install = target_install or _install_id(self.target_assets)
        self.donor_tables = weaponparts.WeaponTables(self.donor_assets,
                                                     self.donor_install)
        self.target_tables = weaponparts.WeaponTables(self.target_assets,
                                                      self.target_install)
        self.donor_digests = MeshDigests(self.donor_assets, use_disk=use_disk)
        self.target_digests = MeshDigests(self.target_assets, use_disk=use_disk)

    # -- lifecycle ---------------------------------------------------------
    def flush(self) -> tuple:
        """Write both installs' caches.  `(donor path or None, target path)`."""
        return (self.donor_digests.flush(), self.target_digests.flush())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.flush()
        return False

    # -- the predicate -----------------------------------------------------
    def displacement(self, weapon_id) -> Displacement:
        """What happens to the target if this donor weapon is brought across."""
        wid = str(weapon_id).strip()
        d_app, d_parts, _on_donor = _parts(self.donor_tables, wid)
        if d_parts is None:
            return Displacement(
                wid, Displacement.UNDETERMINED, self.donor_install,
                self.target_install,
                comparisons=(), deciding=-1, on_target=None,
                note=("no appearance table could be read on the donor, so what "
                      "this swap would write is not established"),
                donor_appearance=d_app)
        if not d_parts:
            raise NotADonor(
                f"{wid} is not a weapon on {self.donor_install or 'the donor'} "
                f"-- there is nothing to bring. This is a fact about the donor, "
                f"not about what would happen to the target, which is why it is "
                f"not one of {Displacement.STATUSES}.")

        t_app, t_parts, on_target = _parts(self.target_tables, wid)
        if t_parts is None:
            return Displacement(
                wid, Displacement.UNDETERMINED, self.donor_install,
                self.target_install,
                comparisons=(), deciding=-1, on_target=None,
                note=("no appearance table could be read on the target, so "
                      "whether anything would be displaced is not established"),
                donor_appearance=d_app, target_appearance=t_app)

        comparisons: list = []
        for i, part in enumerate(d_parts):
            dm = str(part["mesh"])
            d_logical = MESH_LOGICAL.format(dm)
            donor_ref = self.donor_digests.ref(MeshRef.DONOR,
                                               self.donor_install, dm, d_logical)
            tm = (str(t_parts[i]["mesh"])
                  if t_parts and i < len(t_parts) else None)

            if tm is not None:
                t_logical = MESH_LOGICAL.format(tm)
                target_ref = self.target_digests.ref(
                    MeshRef.TARGET, self.target_install, tm, t_logical)
                comparisons.append(Comparison(
                    ROLE_RESOLVED, i, donor_ref, target_ref,
                    _verdict(donor_ref, target_ref),
                    note=(f"the target's own row for {wid} resolves to "
                          f"{t_logical}, which is the art this swap displaces "
                          f"on screen")))

            # The path the file actually lands on.  Skipped when it IS the
            # resolved path (the overwhelming case: the tables agree), asked
            # otherwise -- 92 donor ids absent from CCO's table land on an
            # occupied CCO mesh path with different bytes, and an id-presence
            # rule calls every one of them safe.
            if tm != dm:
                write_ref = self.target_digests.ref(
                    MeshRef.TARGET, self.target_install, dm, d_logical)
                comparisons.append(Comparison(
                    ROLE_WRITE_PATH, i, donor_ref, write_ref,
                    _verdict(donor_ref, write_ref),
                    note=(f"{d_logical} is where this write lands on the "
                          f"target, whatever the target's own row says")))

        if not comparisons:                                # pragma: no cover
            raise RuntimeError(
                f"{wid} produced no comparison from {len(d_parts)} donor "
                f"part(s); this is a bug in weaponswap, not a state of the "
                f"installs -- refusing rather than reporting a status.")

        # Worst wins. An unreadable part must never be silenced by a readable
        # one, and a replaced part must never be silenced by an absent one.
        order = {s: i for i, s in enumerate(Displacement.PRECEDENCE)}
        idx = min(range(len(comparisons)),
                  key=lambda i: (order[comparisons[i].status], i))
        best = comparisons[idx]
        status = best.status

        if status == Displacement.NOT_ON_TARGET:
            note = (f"nothing on {self.target_install or 'the target'} stands "
                    f"where this would land: the weapon id is "
                    f"{'present' if on_target else 'absent'} there and "
                    f"{best.donor.logical} does not exist on it")
            return Displacement(wid, status, self.donor_install,
                                self.target_install, comparisons, idx,
                                on_target, note, d_app, t_app)
        if status == Displacement.NO_OP:
            return Displacement(wid, status, self.donor_install,
                                self.target_install, comparisons, idx,
                                on_target, "", d_app, t_app)
        if status == Displacement.REPLACES:
            note = (f"{best.donor.logical} on the donor and "
                    f"{best.target.logical} on the target are different art "
                    f"({best.donor.source} {best.donor.size} B vs "
                    f"{best.target.source} {best.target.size} B); bringing "
                    f"this destroys the target's")
            return Displacement(wid, status, self.donor_install,
                                self.target_install, comparisons, idx,
                                on_target, note, d_app, t_app)
        unread = [r for r in (best.donor, best.target)
                  if r is not None and not r.readable]
        return Displacement(
            wid, status, self.donor_install, self.target_install, comparisons,
            idx, on_target,
            note="; ".join(r.reason for r in unread) or "a side was not read")


def _assets(root):
    if hasattr(root, "locate") and hasattr(root, "read"):
        return root
    import coassets
    return coassets.AssetRoot(root)


def _install_id(assets) -> str:
    try:
        return coroot.base_id(getattr(assets, "root", None))
    except Exception:
        return ""


def displacement(donor_root, target_root, weapon_id, *,
                 view: Optional[SwapView] = None) -> Displacement:
    """If the owner brings this donor weapon to the target, what happens?

    Returns a `Displacement`; read its `status` first, and use `blocks_swap`
    rather than a truth test.  Raises `NotADonor` when the id is not a weapon
    on the donor at all -- there is nothing to bring, which is a fact about the
    donor and deliberately not one of the four statuses.

    ``donor_root``/``target_root`` are install roots (`Path` or `str`) or
    anything with `AssetRoot`'s `locate`/`read` pair.  Pass ``view`` -- a
    `SwapView` built once -- whenever more than one weapon is being asked
    about; the appearance tables are the fixed cost and this one-shot form pays
    them every call.  When ``view`` is given, the two roots are not consulted.
    """
    if view is not None:
        return view.displacement(weapon_id)
    with SwapView(donor_root, target_root) as v:
        return v.displacement(weapon_id)
