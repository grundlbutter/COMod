#!/usr/bin/env python3
r"""splitoffer.py -- "replace just this one, or the whole group?", and which
ids are free to split into.

    from splitoffer import cohort_offering, free_id_offering

    off = cohort_offering("c3/npc/999001100.c3", audit_result=audit(t, exists),
                          in_table=t.plan_for_mesh)
    ids = free_id_offering("cco", allocator=None)      # -> NOT WIRED IN

This module is WIRING. It owns no cohort computation and no free-set
computation, and that is the point: both already exist, both were measured by
somebody else, and the failure this page exists to prevent is a second,
locally-derived answer that looks the same and is wrong.

    cohort     core/npcart.audit()          on master, called here
    allocator  core/npcalloc.allocate()     NOT on master -- injected, stubbed
    selector   OVERWRITE vs SPLIT           here, SPLIT default
    identity   collection.stage()           byte-identity, MAP ROLES ONLY

THREE STATES, AND `undetermined` IS NOT A ROUNDING ERROR
--------------------------------------------------------
Every question here answers `shared`, `singleton` or `undetermined`, and the
three do not collapse.  `undetermined` means **nobody has looked**, and it is
the honest answer whenever a provider is absent or cannot see the question.
Folding it into "nothing else shares this" is the defect this module exists to
prevent: that sentence is the one that makes an OVERWRITE look safe.

`Offering.__bool__` **raises**, for the same reason `npcalloc.Answer.__bool__`
does -- ``if cohort_offering(...):`` is exactly the fold this contract forbids,
so it is a crash rather than a convention.

WHY ABSENCE FROM `sharedGeometry` IS NOT A COHORT OF ONE
--------------------------------------------------------
**This is the trap in the provider we were told to consume.**
`npcart.audit()` ends with::

    out["sharedGeometry"] = {k: v for k, v in geom_users.items() if len(v) > 1}
    out["sharedTexture"]  = {k: v for k, v in tex_users.items()  if len(v) > 1}

so a mesh used by **exactly one** row is not in the map at all.  A consumer
that reads ``audit["sharedGeometry"].get(mesh, [])`` gets ``[]`` and cannot
tell apart:

* a mesh with exactly one user -- SPLIT is a no-op, OVERWRITE touches one row;
* a mesh **not in the npc table at all** -- nobody has resolved it, and how
  far an overwrite reaches is unknown.

Those lead to opposite UI and `[]` renders as the reassuring one.  So absence
is `undetermined` **unless** an independent membership check confirms the mesh
resolves to a row, and `in_table` is that check -- `npcart.Tables.plan_for_mesh`,
which is on master and is not a second sharing computation.  With no
`in_table`, absence stays `undetermined` forever.  That costs a real singleton
an "only this one" badge and cannot promise solitude the data does not show.

THE OWNER ASKED ABOUT THREE THINGS AND THE PROVIDER REPORTS TWO
----------------------------------------------------------------
    "shared meshes / animations / textures"

`npcart.audit()` reports `sharedGeometry` and `sharedTexture`.  **It reports no
motion sharing at all.**  So of the three axes the owner named, one has no
provider, and `SELECTORS` carries it as `undetermined` with the reason -- not
omitted, and above all not rendered as "animations are not shared".  An axis
nobody measured must not read as an axis that came back clean.

THE FREE SET IS NOT `json - disk`
----------------------------------
The literal spelling of the request -- "free in both .json and disk" -- is not
the free set, and building it is the collision this page exists to prevent.
The free set is the complement of the UNION of every table addressing these
ids, plus disk, computed per install, and `core/npcalloc.occupied_groups`
already computes it fail-closed across both plausible group readings.

**This module therefore computes no free set.**  `free_id_offering` returns
only ids its injected allocator handed it.  With no allocator it returns none
and says `NOT WIRED IN` -- see `ALLOCATOR_CONTRACT`.  The check that keeps this
honest is `assert_offering_respects_allocator`, which re-asks the allocator
about every id already offered; it is the test the brief asked for and it
lives here, next to the thing it constrains, rather than only in the tests.

ENCODING
--------
Cohort member names are client data and **are not cp1252**.  A probe crashed
printing one (`UnicodeEncodeError: 'charmap' codec`) at the moment it reached
the interesting row.  A list that renders 38 names and dies on the 39th is
worse than one that renders none, because the failure looks like the end of
the list.  `render_lines` and `to_json` are text-domain only; `write_csv`
writes UTF-8 explicitly and never inherits the console codepage.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

__all__ = [
    "SHARED", "SINGLETON", "UNDETERMINED", "STATES", "OK",
    "OVERWRITE", "SPLIT", "DEFAULT_MODE", "MODES",
    "SELECTORS", "Selector", "Offering", "IdOffering",
    "ALLOCATOR_CONTRACT", "ALLOCATOR_OWNER", "COHORT_PROVIDER",
    "cohort_offering", "free_id_offering", "render_lines", "write_csv",
    "assert_offering_respects_allocator", "OfferingViolation",
]

# -- the three states -------------------------------------------------------

SHARED = "shared"
SINGLETON = "singleton"
UNDETERMINED = "undetermined"
STATES = (SHARED, SINGLETON, UNDETERMINED)

#: `IdOffering` only: the allocator answered and handed ids over. A cohort is
#: never `OK` -- it is one of the three states above.
OK = "ok"

# -- the operation selector -------------------------------------------------

OVERWRITE = "overwrite"
SPLIT = "split"

#: SPLIT is the default because the destructive option is the COMMON case.
#: Measured by Parser: 382 of 437 NPC rows (87%) share a mesh set with another
#: row, so an OVERWRITE picked by accident is more likely than not to change
#: something the user never looked at. The destructive option is the
#: deliberate click.
DEFAULT_MODE = SPLIT

MODES: dict = {
    SPLIT: {
        "key": SPLIT,
        "label": "Replace this NPC's own copy",
        "effect": "Mints a new art id and edits the npc table so only this row "
                  "points at it, then forks the action files. WHAT THAT FORKS "
                  "VARIES BY TARGET and is measured per swap -- about half of "
                  "NPC action files carry their own PHY geometry and about "
                  "half are motion-only (CCO 38/78, 5517 36/104, measured with "
                  "c3phy.iter_chunks). The skin is a separate .dds and is "
                  "never forked by this operation.",
        #: NOT a family-wide claim. `forks` for a REAL target comes from
        #: measure_fork_axes() reading that target's own files. These two keys
        #: record only what is invariant: the skin is never forked, and the
        #: geometry answer is per-target rather than constant.
        "forks": None,
        "forksAre": "per-target, see measure_fork_axes()",
        "stillShared": ("texture",),
        "editsTable": True,
        "destructive": False,
        "default": True,
    },
    OVERWRITE: {
        "key": OVERWRITE,
        "label": "Replace all of them",
        "effect": "Every row sharing this art changes. No table edit.",
        "editsTable": False,
        "destructive": True,
        "default": False,
    },
}


# -- what a fork actually forks, for THIS target ---------------------------
#
# There is no family-wide answer and asserting one is how this module was
# wrong TWICE in half an hour: first "Replace this NPC only" (overclaim,
# caught by COMod Parser), then "animations only, mesh stays shared"
# (underclaim, caught by measuring). Roughly half of NPC action files carry
# their own PHY geometry:
#
#     c3phy.iter_chunks, 182 files, 0 unparseable
#     CCO    78 files   38 PHY+MOTI (49%)   40 MOTI-only
#     5517  104 files   36 PHY+MOTI (35%)   68 MOTI-only
#
# So the honest answer is a measurement of the files this swap will touch.

FORK_MEASURED = "measured"
FORK_UNDETERMINED = "undetermined"


def measure_fork_axes(paths, reader=None):
    """Read the action files a SPLIT would fork and report what they carry.

    `reader(path) -> set of 4-byte chunk tags`. Defaults to
    ``c3phy.iter_chunks``. Returns a dict; `verdict` is FORK_MEASURED or
    FORK_UNDETERMINED and NEVER a bare guess. On an unreadable file the
    verdict is UNDETERMINED with the path named -- it does not fall back to
    the common case, because the common case is only 49%.
    """
    if reader is None:                                   # pragma: no cover
        import c3phy

        def reader(path):
            with open(path, "rb") as fh:
                return {t for t, _ in c3phy.iter_chunks(fh.read())}

    paths = list(paths)
    if not paths:
        return {"verdict": FORK_UNDETERMINED, "forks": None,
                "stillShared": ("texture",), "read": [],
                "reason": "no action files were given, so nothing was read"}
    seen, read = set(), []
    for path in paths:
        try:
            tags = reader(path)
        except Exception as exc:
            return {"verdict": FORK_UNDETERMINED, "forks": None,
                    "stillShared": ("texture",), "read": read,
                    "reason": f"could not read {path}: "
                              f"{type(exc).__name__}: {exc}"}
        read.append(str(path))
        seen |= {bytes(t)[:4] for t in tags}
    geom = any(t.startswith(b"PHY") for t in seen)
    motion = any(t.startswith(b"MOTI") for t in seen)
    forks = tuple(a for a, y in (("geometry", geom), ("motion", motion)) if y)
    shared = tuple(a for a in ("geometry", "texture")
                   if a not in forks)
    return {"verdict": FORK_MEASURED, "forks": forks, "stillShared": shared,
            "read": read,
            "reason": f"read {len(read)} file(s); "
                      f"PHY {'present' if geom else 'absent'}, "
                      f"MOTI {'present' if motion else 'absent'}"}

# -- who answers what -------------------------------------------------------

COHORT_PROVIDER = "core/npcart.audit() -> sharedGeometry / sharedTexture"

#: The allocator this module consumes. It is NOT on master: it lives on
#: `claude/parser-swap-allocator` as `core/npcalloc.py`, at `c65482a` as of
#: 2026-08-18 (an earlier draft of this comment cited `ba53af1`, which was
#: never on master and is now also not the tip -- do not chase either sha,
#: resolve the BRANCH). Until it lands, `free_id_offering` is handed nothing
#: and says so.
ALLOCATOR_CONTRACT = (
    "npcalloc.can_serve(target) -> Answer{served|UNSUPPORTED|UNKNOWN} and "
    "npcalloc.allocate(target, occupied) -> (group, width, layout)")
ALLOCATOR_OWNER = "COMod Parser"


@dataclass(frozen=True)
class Selector:
    """One axis a swap could be shared on."""
    key: str
    label: str
    #: The `audit()` key that answers it, or "" when nothing answers it.
    audit_key: str
    why_absent: str = ""


#: The owner named three. Two have a provider; the third does not, and it is
#: listed anyway so the UI shows a hole rather than a clean bill.
SELECTORS: tuple = (
    Selector("geometry", "Mesh", "sharedGeometry"),
    Selector("texture", "Texture", "sharedTexture"),
    Selector("motion", "Animations", "",
             "npcart.audit() reports sharedGeometry and sharedTexture only; "
             "no provider reports motion sharing, so whether animations are "
             "shared has not been measured"),
)


class OfferingViolation(AssertionError):
    """An offered id was not one the allocator would hand out."""


@dataclass
class Offering:
    """What the page may offer for one target, and how sure it is."""
    mesh: str = ""
    state: str = UNDETERMINED
    #: Rows sharing this art, INCLUDING the target. Never hand-enumerated.
    members: list = field(default_factory=list)
    #: `None` when undetermined. Never 0 as a stand-in for "unknown".
    count: Optional[int] = None
    #: {selector key: {"state":..., "count":..., "members":[...], "why":...}}
    axes: dict = field(default_factory=dict)
    wired: bool = True
    reason: str = ""
    provider: str = COHORT_PROVIDER

    def __bool__(self):                                    # noqa: D105
        raise TypeError(
            "splitoffer.Offering has three states and truth-testing collapses "
            "it to two. Ask for .shared / .singleton / .undetermined -- this "
            f"one is {self.state}: {self.reason}")

    @property
    def shared(self) -> bool:
        return self.state == SHARED

    @property
    def singleton(self) -> bool:
        return self.state == SINGLETON

    @property
    def undetermined(self) -> bool:
        return self.state == UNDETERMINED

    @property
    def headline(self) -> str:
        if self.state == SHARED:
            return f"This mesh set is used by {self.count} NPCs."
        if self.state == SINGLETON:
            return "Nothing else uses this mesh set."
        if not self.wired:
            return "NOT WIRED IN - cannot tell how many NPCs use this."
        return "Cannot tell how many NPCs use this mesh set."

    @property
    def offered_modes(self) -> list:
        """Both modes, always, with SPLIT first and default.

        OVERWRITE is not withheld when the count is unknown -- withholding it
        would be a second way of saying "this is safe". It is offered with the
        count rendered as unknown, which is the true thing.
        """
        return [dict(MODES[SPLIT]), dict(MODES[OVERWRITE])]

    def to_json(self) -> dict:
        return {"mesh": self.mesh, "state": self.state, "count": self.count,
                "members": list(self.members), "axes": self.axes,
                "wired": self.wired, "reason": self.reason,
                "provider": self.provider, "headline": self.headline,
                "defaultMode": DEFAULT_MODE, "modes": self.offered_modes}


@dataclass
class IdOffering:
    """Free ids the page may show. Every one came from the allocator."""
    target: str = ""
    ids: list = field(default_factory=list)
    state: str = UNDETERMINED
    wired: bool = False
    reason: str = ""
    contract: str = ALLOCATOR_CONTRACT
    owner: str = ALLOCATOR_OWNER
    width: Optional[int] = None
    layout: str = ""

    def __bool__(self):                                    # noqa: D105
        raise TypeError(
            "splitoffer.IdOffering has three states and truth-testing "
            f"collapses it to two. This one is {self.state}: {self.reason}")

    @property
    def headline(self) -> str:
        if not self.wired:
            return ("NOT WIRED IN - no free ids can be offered. "
                    f"{self.owner} owns the allocator.")
        if self.state == UNDETERMINED:
            return "Cannot tell which ids are free."
        return f"{len(self.ids)} free id(s) available."

    def to_json(self) -> dict:
        return {"target": self.target, "ids": list(self.ids),
                "state": self.state, "wired": self.wired,
                "reason": self.reason, "contract": self.contract,
                "owner": self.owner, "width": self.width,
                "layout": self.layout, "headline": self.headline}


# -- the cohort -------------------------------------------------------------

def _axis(mesh: str, sel: Selector, audit_result: Mapping,
          confirmed_member: Optional[bool]) -> dict:
    """One selector's answer, with absence handled honestly."""
    if not sel.audit_key:
        return {"state": UNDETERMINED, "count": None, "members": [],
                "why": sel.why_absent, "wired": False}
    shared_map = audit_result.get(sel.audit_key)
    if shared_map is None:
        return {"state": UNDETERMINED, "count": None, "members": [],
                "why": (f"the audit result carries no {sel.audit_key!r} key, "
                        "so this axis was not computed"), "wired": False}
    users = shared_map.get(mesh)
    if users:
        # audit() only keeps len>1, so anything present here is genuinely
        # shared. The count comes from the query, never from a literal.
        return {"state": SHARED, "count": len(users), "members": list(users),
                "why": "", "wired": True}
    # ABSENT. This is the trap: absence means "<=1 user" OR "not in the
    # table". Only an independent membership check separates them.
    if confirmed_member is True:
        return {"state": SINGLETON, "count": 1, "members": [],
                "why": ("not in the shared map, and the mesh resolves to a "
                        "row, so it has exactly one user"), "wired": True}
    if confirmed_member is False:
        return {"state": UNDETERMINED, "count": None, "members": [],
                "why": ("not in the shared map and the mesh resolves to no "
                        "npc row; how far an overwrite reaches is unknown"),
                "wired": True}
    return {"state": UNDETERMINED, "count": None, "members": [],
            "why": ("not in the shared map, and audit() drops every mesh with "
                    "one user, so absence here cannot be told apart from a "
                    "mesh that is not in the table; pass `in_table` to "
                    "separate them"), "wired": True}


def cohort_offering(mesh: str, *, audit_result: Optional[Mapping] = None,
                    in_table: Optional[Callable] = None) -> Offering:
    """Who else uses this art, per axis, and what may be offered.

    `audit_result` is the dict from `core/npcart.audit()`. `in_table` is an
    optional membership check -- `npcart.Tables.plan_for_mesh` -- used ONLY to
    tell "one user" apart from "not in the table". It is not a second sharing
    computation and nothing else here consults it.

    With no `audit_result` this is `undetermined` and `wired=False`; it never
    reports a count it was not given.
    """
    mesh = (mesh or "").replace("\\", "/").lstrip("/")
    if audit_result is None:
        return Offering(
            mesh=mesh, state=UNDETERMINED, count=None, wired=False,
            reason=(f"no cohort provider wired in: {COHORT_PROVIDER} was not "
                    "called, so the number of rows an overwrite would change "
                    "is unknown -- not zero"),
            axes={s.key: {"state": UNDETERMINED, "count": None,
                          "members": [], "wired": False,
                          "why": "no audit result supplied"}
                  for s in SELECTORS})

    confirmed: Optional[bool] = None
    if in_table is not None:
        try:
            confirmed = in_table(mesh) is not None
        except Exception as e:                             # pragma: no cover
            confirmed = None
            _ = e

    axes = {s.key: _axis(mesh, s, audit_result, confirmed) for s in SELECTORS}

    shared_axes = [k for k, a in axes.items() if a["state"] == SHARED]
    if shared_axes:
        # The cohort is the union over the axes that answered, and the count
        # is len() of that union -- derived from the query on every path.
        members: list = []
        for k in shared_axes:
            for m in axes[k]["members"]:
                if m not in members:
                    members.append(m)
        return Offering(mesh=mesh, state=SHARED, members=members,
                        count=len(members), axes=axes, wired=True,
                        reason=f"shared on: {', '.join(shared_axes)}")

    # No axis reports sharing. The whole mesh set may only be called unshared
    # when EVERY axis answered; one silent axis is enough to withhold that.
    # **In production today that is always the case**: the motion axis has no
    # provider, so `SINGLETON` is unreachable against a real `audit()` result
    # and the page will say "cannot tell" rather than "nothing else uses
    # this". That is the intended behaviour, not a gap to paper over -- the
    # cure is a motion-sharing provider, not a friendlier default.
    undet = [k for k, a in axes.items() if a["state"] == UNDETERMINED]
    singles = [k for k, a in axes.items() if a["state"] == SINGLETON]
    if undet:
        found = (("no sharing found on " + ", ".join(singles) + ", but ")
                 if singles else "")
        return Offering(
            mesh=mesh, state=UNDETERMINED, count=None, axes=axes, wired=True,
            reason=(found + ", ".join(undet) + " could not answer, so the "
                    "mesh set as a whole cannot be called unshared"))
    return Offering(mesh=mesh, state=SINGLETON, members=[], count=1,
                    axes=axes, wired=True,
                    reason="exactly one user on every axis measured")


# -- the free ids -----------------------------------------------------------

def free_id_offering(target: str, *, allocator=None, occupied=None,
                     want: int = 5) -> IdOffering:
    """Free ids for THIS install, straight from the allocator.

    **Computes nothing.** Every id returned was handed over by `allocator`,
    which must expose `can_serve` and `allocate` as `npcalloc` does. With no
    allocator the offering is empty and `NOT WIRED IN`; there is deliberately
    no fallback path, because the fallback everyone reaches for is
    `free = json - disk` and that set contains ids four other tables hold.
    """
    if allocator is None:
        return IdOffering(
            target=target, ids=[], state=UNDETERMINED, wired=False,
            reason=(f"no allocator wired in: {ALLOCATOR_CONTRACT} is "
                    f"{ALLOCATOR_OWNER}'s (core/npcalloc.py, branch "
                    "claude/parser-swap-allocator) and is not on master. No "
                    "free id can be shown, and none is guessed."))
    try:
        answer = allocator.can_serve(target)
    except Exception as e:
        return IdOffering(target=target, ids=[], state=UNDETERMINED,
                          wired=True,
                          reason=f"allocator raised on can_serve: {e!r}")
    if not answer.served:
        return IdOffering(
            target=target, ids=[], state=UNDETERMINED, wired=True,
            reason=(f"allocator will not serve {target!r}: {answer.state} -- "
                    f"{answer.reason}"))

    taken = set(occupied.get("union", ())) if isinstance(occupied, Mapping) \
        else set(occupied or ())
    taken = {str(g).zfill(3) for g in taken}

    ids: list = []
    width = layout = None
    for _ in range(max(0, int(want))):
        try:
            group, width, layout = allocator.allocate(target,
                                                      {"union": set(taken)})
        except Exception:
            break
        ids.append(group)
        taken.add(str(group)[:3].zfill(3))
    return IdOffering(
        target=target, ids=ids,
        state=OK if ids else UNDETERMINED, wired=True,
        reason="" if ids else ("the allocator served this target and then "
                               "handed out no id"),
        width=width, layout=layout or "")


def assert_offering_respects_allocator(offering: IdOffering, allocator, *,
                                       occupied=None) -> None:
    """Every id offered must be one the allocator would still hand out.

    This is the guard the whole page hangs on: an id the allocator would
    refuse is an id that silently resolves to somebody else's mesh. It
    re-asks rather than trusting how the list was built, so a picker that
    ever computed its own free set -- `json - disk` being the one everybody
    reaches for -- trips it.

    Raises `OfferingViolation`. It does not return False: a caller that
    forgets to check a bool is the failure mode this replaces.
    """
    if not offering.ids:
        return
    if allocator is None:
        raise OfferingViolation(
            f"{len(offering.ids)} id(s) are offered with NO allocator wired "
            "in; nothing could have judged them free")
    taken = set(occupied.get("union", ())) if isinstance(occupied, Mapping) \
        else set(occupied or ())
    taken = {str(g).zfill(3) for g in taken}
    for offered in offering.ids:
        key = str(offered)[:3].zfill(3)
        if key in taken:
            raise OfferingViolation(
                f"offered id {offered!r} (group {key}) is OCCUPIED in the "
                "union the allocator was given; the allocator would refuse "
                "it. An offering built from one table alone does this.")
        group, _w, _l = allocator.allocate(offering.target,
                                           {"union": set(taken)})
        if str(group) != str(offered):
            raise OfferingViolation(
                f"offered id {offered!r} is not what the allocator hands out "
                f"next ({group!r}) for the same occupancy; the offering was "
                "not built from this allocator")
        taken.add(key)


# -- rendering (text domain only; never the console codepage) ---------------

def render_lines(offering: Offering, ids: Optional[IdOffering] = None,
                 limit: Optional[int] = None) -> list:
    """The panel, as lines of text. `str` throughout -- no encode happens here.

    Member names are client data and are not cp1252. Nothing in this function
    encodes; callers that print must set their own stream encoding, and
    `write_csv` is the supported file path.

    **The members are listed, not just counted.** An earlier draft of this
    function rendered `count` alone, which meant the CJK row never reached
    the UI at all and the encoding claim was never exercised end to end -- the
    test caught it. `limit` elides, and when it does it SAYS how many it
    elided: a list that stops silently is indistinguishable from a list that
    ended, which is the whole failure mode here.
    """
    out = [offering.headline]
    if not offering.wired:
        out.append(f"  ! {offering.reason}")
    for sel in SELECTORS:
        a = offering.axes.get(sel.key, {})
        st = a.get("state", UNDETERMINED)
        if st == SHARED:
            out.append(f"  {sel.label}: shared by {a['count']}")
        elif st == SINGLETON:
            out.append(f"  {sel.label}: this row only")
        else:
            mark = "NOT WIRED IN" if not a.get("wired", True) else st
            out.append(f"  {sel.label}: {mark} - {a.get('why', '')}")
    if offering.members:
        shown = offering.members if limit is None \
            else offering.members[:max(0, int(limit))]
        for m in shown:
            out.append(f"    - {m}")
        rest = len(offering.members) - len(shown)
        if rest > 0:
            out.append(f"    ... and {rest} more (not shown, not gone)")
    for mode in offering.offered_modes:
        mark = "(o)" if mode["default"] else "( )"
        label = mode["label"]
        if mode["key"] == OVERWRITE and offering.state == SHARED:
            label = f"Replace all {offering.count}"
        out.append(f"  {mark} {label}")
    if ids is not None:
        out.append(ids.headline)
        if not ids.wired:
            out.append(f"  ! {ids.reason}")
        for i in ids.ids:
            out.append(f"  free: {i}")
    return out


def write_csv(path, offering: Offering) -> str:
    """Cohort members to a UTF-8 CSV. **Encoding is explicit, always.**

    A member name can be CJK; a probe already crashed on one under cp1252 at
    the moment it reached the interesting row. `newline=""` and
    `encoding="utf-8"` are both load-bearing and neither is a default on
    Windows.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["mesh", "state", "count", "member"])
    if offering.members:
        for m in offering.members:
            w.writerow([offering.mesh, offering.state,
                        "" if offering.count is None else offering.count, m])
    else:
        w.writerow([offering.mesh, offering.state,
                    "" if offering.count is None else offering.count, ""])
    text = buf.getvalue()
    if path is not None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return text
