"""Collecting a weapon: the plan, the target verdict, and the resolver's absence.

This module is the COLLECT PATH for weapons.  It owns none of the weapon
tables.  The table question -- "what does id 800915 resolve to on this
install" -- is `core/weaponparts.weapon_parts`, owned by the COMod Parser
team, and is reached here **only** through an injected provider (see
`WEAPON_PARTS_CONTRACT`).  Until Parser's module lands, that provider is
absent and every function here says so out loud rather than inventing an
answer.


WHY THE DIRECTORY SWEEP IS NOT USED FOR WEAPONS
-----------------------------------------------
`collection.gather_parts` finds an NPC's animations by listing the mesh's own
directory (`collection.actions_beside`).  That rule is correct for NPCs and
**silently wrong for weapons**, in the opposite direction from the one the
brief for this work predicted.

Measured on 6609, 2026-08-17 (`c3/weapon/`, this box):

    files under c3/weapon/          633        (the brief said 1,363)
    .c3 meshes among them           331
    weapon meshes with >=1 action found by actions_beside()      0
    meshes where the content-folder classifier fired             0

So the hazard is not over-collection.  `c3/weapon/` is one flat directory of
331 single-file meshes with no per-action siblings at all, so the sweep
returns `[]` for **every weapon on the install** and the `MAX_ACTIONS`
content-folder classifier never even gets the chance to fire.  A weapon
collect built on the NPC path would file an entry with zero motions, report
success, and be wrong 100% of the time without one line of complaint.

Weapon motions therefore come from `weaponmotion.dbc`/`.ini` -- by TABLE --
and are handed to `gather_parts` explicitly through its ``motions=``
parameter.  `plan_files` below is the only thing that decides which files
travel, and it reads them off the resolver's facets.


THE TARGET SIDE HAS THREE STATES, NOT TWO
-----------------------------------------
Collecting writes into the COmmunity Library's Collection, not into an
install; the write to CCO happens later, at `Collection.stage` and
`tools/comod.py install`.  The verdict below is therefore about what the
collected entry would DO when it lands, and it is computed at collect time
because that is when the owner is looking at the weapon and deciding.

The owner's workflow is read-from-anywhere, write-to-CCO.  The question the
warning has to answer is exactly one: **will writing this overwrite art that
is already there?**  That question has FOUR answers, and the fourth is the
one that is easiest to leave out:

    not_on_target   the id is free on the target.  SAFE -- and the point.
    no_op           the resolved mesh is BYTE-IDENTICAL.  Changes nothing.
    replaces        the resolved mesh DIFFERS.  Destroys distinct art.  WARN.
    undetermined    one or both sides could not be read.  NOT reassurance.

`undetermined` is load-bearing and is not merged into either neighbour, for
the same reason `unavailable` is not merged into `empty` on a facet: "could
not look" and "looked, and it is fine" are opposite instructions to a person
about to overwrite something.

WHY BYTE IDENTITY AND NOT THE TABLE KEYS
----------------------------------------
This panel first shipped keyed on whether the two installs' appearance tables
NAME the same mesh id.  That is a real question and it was measured
correctly, but it is a different question from the one above, and keying the
warning to it made the panel actively lie.

Measured on this box 2026-08-17, 6609 -> CCO, over the 4,718 shared weapon
ids, by hashing the resolved mesh bytes on each install:

    shared ids                                  4,718
      comparable (both meshes readable)         4,710
        resolved mesh BYTES differ              4,147   88.0%
        genuinely identical                       563   12.0%

    ...against the table-key question on the same set:
        tables name different ids                 384    8.2%
        tables AGREE but bytes differ           3,763   <- rendered GREEN
        tables disagree but bytes are same          0

So table-key disagreement is a strict SUBSET of byte difference, with zero
counterexamples: it never catches an overwrite that byte comparison misses,
and it misses 3,763 that byte comparison catches.  Keyed on the tables, the
warning was silent on 91% of real overwrites **and told the user they were
safe** -- an affirmative false reassurance, which is worse than showing
nothing.

Two further measurements, recorded because both are the kind of "cheaper
predicate" that would reintroduce the defect:

  * **File SIZE is not sufficient.**  It agrees with the hash on 4,137 of the
    4,147, and calls the other 10 identical.  Ten silent overwrites is not a
    rounding error when the whole feature exists to prevent them.
  * Including the texture files raises the difference rate to 90.8% but
    raises the *uncomparable* count from 8 to 175, so the mesh is the better
    single discriminator.

The byte comparison itself is **not this module's**.  Resolving a mesh
through `AssetRoot` across two installs is COMod Parser's surface; a second
implementation that disagreed with theirs would be worse than either.  This
module consumes `displacement(donor_root, target_root, weapon_id)` through an
injected provider and, when it is absent, renders that absence rather than
falling back to the table keys.  **The fallback is the defect**, so there
isn't one.
"""

from __future__ import annotations

from typing import Callable, Optional

# ---------------------------------------------------------------------------
# the weapon_parts query -- COMod Parser's, stubbed here
# ---------------------------------------------------------------------------

#: The contract this page is built against, owned by the COMod Parser team.
#: Delivered on `claude/parser-weapon-resolver`; not merged when this landed.
WEAPON_PARTS_CONTRACT = (
    "weapon_parts(root, weapon_id) -> WeaponParts("
    "presence=on_install|not_on_install|undetermined, "
    "facets={appearance,motions,effects,skins}: "
    "Facet(status=present|empty|unavailable|partial, rows, source, note))")
WEAPON_PARTS_OWNER = "COMod Parser"
WEAPON_PARTS_MODULE = "core/weaponparts.py"

#: The four surfaces, in the order Parser's module introduces them.
SURFACES = ("appearance", "motions", "effects", "skins")

#: Facet statuses, mirrored here so this module can name them in prose
#: without importing Parser's module.  `_check_contract` asserts they still
#: agree once the real module is importable.
PRESENT, EMPTY, UNAVAILABLE, PARTIAL = (
    "present", "empty", "unavailable", "partial")
STATUSES = (PRESENT, EMPTY, UNAVAILABLE, PARTIAL)

ON_INSTALL, NOT_ON_INSTALL, UNDETERMINED = (
    "on_install", "not_on_install", "undetermined")


class WeaponPartsUnavailable(RuntimeError):
    """Raised when no weapon-parts provider is wired in.

    An exception rather than an empty result, and the distinction is the
    whole reason this class exists.  Parser's contract already gives a facet
    a way to say "a table was consulted and said nothing" (`empty`) and a way
    to say "no table was consulted" (`unavailable`).  A stub that returned
    four `unavailable` facets would be **indistinguishable from a real
    install that ships none of the weapon tables** -- and that is the one
    confusion the whole status contract was built to prevent.

    So the stub does not answer in the contract's vocabulary at all.  It
    fails, and `parts_report` turns the failure into a fifth, outer state
    (`resolverAvailable: False`) that sits ABOVE the facets rather than
    inside them.
    """


def weapon_parts(root, weapon_id: str, *,
                 provider: Optional[Callable] = None):
    """Resolve one weapon id against one install.

    ``provider`` is Parser's real `weapon_parts`, injected by the caller.
    Until it lands there is none and this raises rather than inventing a
    resolution.

    Returns whatever the provider returns -- a `WeaponParts`.  This function
    deliberately does **not** read `.presence` or any facet: arming Parser's
    read-order guards on the caller's behalf would disable them.
    """
    if provider is None:
        raise WeaponPartsUnavailable(
            f"no weapon-parts provider wired in: {WEAPON_PARTS_CONTRACT} is "
            f"owned by {WEAPON_PARTS_OWNER} ({WEAPON_PARTS_MODULE}) and has "
            f"not landed on this branch yet")
    res = provider(root, weapon_id)
    if not hasattr(res, "presence"):
        raise WeaponPartsUnavailable(
            f"weapon-parts provider returned {type(res).__name__} with no "
            f"`presence`; expected {WEAPON_PARTS_CONTRACT}")
    return res


# ---------------------------------------------------------------------------
# one facet, in a shape a page can render without ever counting rows
# ---------------------------------------------------------------------------

#: What each status means to a person, per surface.  Keyed by status FIRST,
#: because that is the discriminator -- there is no entry keyed by row count
#: and no code path here that consults `len(rows)` to choose one.
_FACET_PROSE = {
    PRESENT: ("{n} {surface} row(s), from {source}.", ""),
    PARTIAL: ("{n} {surface} row(s), from {source} — partial.",
              "Some rows for this weapon were addressable and some were not; "
              "what is listed is what could be resolved, not all there is."),
    EMPTY: ("No {surface}: {source} was consulted and has nothing for this "
            "weapon.",
            "This is an answer, not a gap. The table ships here and does not "
            "mention this weapon."),
    UNAVAILABLE: ("{surface} could not be looked up on this install.",
                  "No table was consulted, because none of the tables that "
                  "would answer this ship here. Unknown, not empty."),
}


def facet_view(surface: str, facet) -> dict:
    """One `Facet` -> a dict a page renders by reading ``status``.

    The status is read FIRST and is what every downstream choice switches
    on.  ``rows`` is carried alongside it and is never the discriminator:
    `empty` and `unavailable` both have zero rows and produce different
    headlines, so a renderer that keyed off ``rows.length`` could not tell
    them apart and would be visibly wrong here rather than subtly wrong in
    the browser.
    """
    status, rows = facet.unpack()      # status first, by construction
    head, detail = _FACET_PROSE[status]
    return {
        "surface": surface,
        "status": status,
        "rows": [dict(r) for r in rows],
        "source": facet.source,
        "note": facet.note,
        "headline": head.format(n=len(rows), surface=surface,
                                source=facet.source or "no table"),
        "detail": detail,
        # True only for the two statuses Parser calls ANSWERED. Carried so the
        # page can style "we have rows" without re-deriving it from a length.
        "answered": status in (PRESENT, PARTIAL),
    }


def parts_report(root, weapon_id: str, *, provider=None,
                 install: str = "") -> dict:
    """`weapon_parts` in a shape the page can render, including its own absence.

    The outer key is ``resolverAvailable``.  It is checked before ``presence``
    and before any facet, and when it is False there are **no facets at all**
    -- not four `unavailable` ones.  See `WeaponPartsUnavailable`.
    """
    try:
        parts = weapon_parts(root, weapon_id, provider=provider)
    except WeaponPartsUnavailable as e:
        return {
            "weaponId": str(weapon_id),
            "install": install,
            "resolverAvailable": False,
            "stub": True,
            "reason": str(e),
            "contract": WEAPON_PARTS_CONTRACT,
            "owner": WEAPON_PARTS_OWNER,
            "module": WEAPON_PARTS_MODULE,
            "presence": None,
            "appearanceId": None,
            "facets": {},
            "headline": "The weapon table resolver is not wired in.",
            "detail": (
                f"Nothing was looked up. This is not the same as a weapon "
                f"with no parts and not the same as an install with no "
                f"weapon tables — both of those are answers, and this is the "
                f"absence of the thing that answers. {WEAPON_PARTS_OWNER} "
                f"owns {WEAPON_PARTS_MODULE}; until it is merged here, no "
                f"weapon's appearance, motions, effects or skins can be "
                f"resolved at all."),
        }

    presence = parts.presence          # presence first, by construction
    out = {
        "weaponId": parts.weapon_id,
        "install": parts.install or install,
        "resolverAvailable": True,
        "stub": False,
        "reason": "",
        "contract": WEAPON_PARTS_CONTRACT,
        "owner": WEAPON_PARTS_OWNER,
        "module": WEAPON_PARTS_MODULE,
        "presence": presence,
        "appearanceId": parts.appearance_id,
        "facets": {},
        "headline": "",
        "detail": parts.note,
    }
    if presence != ON_INSTALL:
        # Deliberately NOT decomposed into four empty facets: that would be a
        # confident answer about the wrong subject. Parser's `facets` raises
        # `NotOnInstall` here and this respects that rather than working
        # around it.
        out["headline"] = (
            f"{parts.weapon_id} is not a weapon on "
            f"{out['install'] or 'this install'}."
            if presence == NOT_ON_INSTALL else
            f"Whether {parts.weapon_id} is a weapon on "
            f"{out['install'] or 'this install'} could not be established.")
        return out

    out["facets"] = {s: facet_view(s, f) for s, f in parts.facets.items()}
    out["headline"] = (
        f"{parts.weapon_id} resolves to appearance {parts.appearance_id} on "
        f"{out['install'] or 'this install'}.")
    return out


# ---------------------------------------------------------------------------
# the displacement query -- COMod Parser's, stubbed here
# ---------------------------------------------------------------------------

#: The contract this page is built against, owned by the COMod Parser team.
#: Not on a ref yet; this module is designed against it and stubbed until it
#: lands, the same way `weapon_parts` is above.
DISPLACEMENT_CONTRACT = (
    "displacement(donor_root, target_root, weapon_id) -> Displacement("
    "state=not_on_target|no_op|replaces|undetermined)")
DISPLACEMENT_OWNER = "COMod Parser"

#: The id is free on the target.  Bringing it across is the point of the tool.
NOT_ON_TARGET = "not_on_target"
#: The resolved mesh is BYTE-IDENTICAL.  Bringing it across changes nothing.
NO_OP = "no_op"
#: The resolved mesh DIFFERS.  Writing it destroys distinct art on the target.
REPLACES = "replaces"
#: One or both sides could not be read.  Not reassuring, and not a failure.
DISPLACEMENT_UNDETERMINED = "undetermined"

DISPLACEMENT_STATES = (NOT_ON_TARGET, NO_OP, REPLACES,
                       DISPLACEMENT_UNDETERMINED)

#: The two states that mean "writing this is not destructive".  `undetermined`
#: is deliberately NOT among them: "we could not read it" must never render as
#: reassurance.  Same rule as `unavailable` versus `empty` on a facet.
DISPLACEMENT_SAFE = (NOT_ON_TARGET, NO_OP)


class DisplacementUnavailable(RuntimeError):
    """Raised when no displacement provider is wired in.

    An exception, not a state, and for the reason the whole four-state design
    exists: a stub that returned `undetermined` would be **indistinguishable
    from a real install whose mesh could not be read**, and a stub that
    returned `not_on_target` or `no_op` would be an affirmative lie. The
    absence of the resolver is a fact about our build and belongs outside the
    contract's own vocabulary.
    """


#: Measured on this box 2026-08-17, 6609 -> CCO, over the 4,718 shared weapon
#: ids, by hashing the resolved mesh bytes on each install. Recorded here
#: because the UI quotes a distribution to the owner and prose that is not
#: measured is prose that goes stale silently.
#:
#: The history is worth keeping. This panel first shipped keyed on whether the
#: two TABLES named the same mesh id, which differs on 392 of 4,718 (8.3%).
#: That predicate is not wrong about tables -- it is answering a different
#: question from the one the warning asks. Keyed on it, the warning was silent
#: on 3,763 real overwrites and, worse, actively rendered them green.
DISPLACEMENT_STAT = {
    "shared": 4718, "comparable": 4710,
    "bytesDiffer": 4147, "bytesSame": 563,
    "tablesAgreeBytesDiffer": 3763,
    "tablesDisagreeBytesSame": 0,
    "measured": "2026-08-17", "donor": "6609", "target": "CCO",
}


def displacement(donor_root, target_root, weapon_id: str, *,
                 provider: Optional[Callable] = None):
    """What writing this weapon to the target would DO, by byte identity.

    ``provider`` is Parser's real `displacement`, injected by the caller.
    Until it lands there is none and this raises rather than answering.

    **There is no fallback predicate here on purpose.** Comparing table keys
    instead of bytes is cheap, is already available from the two
    `parts_report`s this module has in hand, and is exactly the mistake this
    function replaces. A warning that is silent on the common case is worse
    than no warning, so when the real answer is unavailable this refuses and
    the page renders that refusal.
    """
    if provider is None:
        raise DisplacementUnavailable(
            f"no displacement provider wired in: {DISPLACEMENT_CONTRACT} is "
            f"owned by {DISPLACEMENT_OWNER} and has not landed yet")
    res = provider(donor_root, target_root, weapon_id)
    state = getattr(res, "state", None)
    if state is None and isinstance(res, dict):
        state = res.get("state")
    if state not in DISPLACEMENT_STATES:
        raise DisplacementUnavailable(
            f"displacement provider returned state {state!r}, expected one "
            f"of {DISPLACEMENT_STATES}")
    return res


def table_key_signal(donor_report: dict, target_report: dict) -> dict:
    """Do the two installs' tables NAME the same mesh id?

    A real and separate question, reported beside the verdict and **never**
    driving it. Measured 2026-08-17: table-key disagreement is a strict
    SUBSET of byte difference -- 384 of 4,710 comparable ids disagree on keys
    and every one of them also differs in bytes, with zero counterexamples.
    So this signal adds nothing to the safety question and is carried only
    because "the tables disagree about which mesh id" is worth seeing when
    you are looking at why two installs diverge.
    """
    d, t = _art_key_of(donor_report), _art_key_of(target_report)
    if d is None or t is None:
        return {"comparable": False, "differ": None,
                "note": "the appearance tables did not both answer"}
    if d == t:
        return {"comparable": True, "differ": False,
                "note": "both tables name the same mesh and texture ids"}
    return {"comparable": True, "differ": True,
            "note": (f"the tables name different ids: "
                     f"{_fmt_art(d, join=True)} here, "
                     f"{_fmt_art(t, join=True)} on the target")}


def displacement_report(donor_root, target_root, weapon_id: str, *,
                        provider=None, donor_report: Optional[dict] = None,
                        target_report: Optional[dict] = None,
                        target_install: str = "") -> dict:
    """`displacement` in a shape the page can render, including its own absence.

    ``donor_report``/``target_report`` are `parts_report` dicts, used only for
    the secondary table-key signal and for naming the install in prose. They
    are NOT consulted to decide the state.

    **ID PRESENCE MUST NEVER PRODUCE A SAFE STATE.** The tempting shortcut is
    the line ``if target_report["presence"] == NOT_ON_INSTALL: return
    NOT_ON_TARGET`` -- free, obvious, and wrong in the same way the table-key
    trigger was wrong. "The id is free on the target" is not "nothing is in
    the way": the write lands by INDIRECTION, so an id the target's
    `weapon.ini` has never heard of can still resolve to an appearance the
    target does have. Measured by COMod Parser 2026-08-17 over the 8,574
    donor ids absent from CCO entirely -- 140 of them `replaces`, 78
    `undetermined`, 10 `no_op`; 6609's `800001` soul-resolves to appearance
    `410019`, which CCO ships. A presence rule calls all 8,574 safe and
    silently destroys art in 140.

    So the state is whatever `displacement` returned, verbatim, and the stub
    path answers `undetermined`. A stub is entitled to say "I cannot tell";
    it is not entitled to say "safe". Pinned by
    `SafeStatesComeOnlyFromTheProvider`, which fails on 6 of 8 assertions if
    the shortcut above is reintroduced.
    """
    stat = DISPLACEMENT_STAT
    wid = str(weapon_id)
    tinstall = (target_install
                or (target_report or {}).get("install") or "the target")
    keys = (table_key_signal(donor_report or {}, target_report or {})
            if donor_report is not None and target_report is not None
            else {"comparable": False, "differ": None, "note": ""})

    try:
        res = displacement(donor_root, target_root, wid, provider=provider)
    except DisplacementUnavailable as e:
        return {
            "state": DISPLACEMENT_UNDETERMINED, "safe": False,
            "available": False, "stub": True, "reason": str(e),
            "contract": DISPLACEMENT_CONTRACT, "owner": DISPLACEMENT_OWNER,
            "weaponId": wid, "tableKeys": keys, "stat": stat,
            "headline": "Cannot tell whether this would replace art on "
                        f"{tinstall}.",
            "detail": (
                f"The check that answers this compares the resolved mesh "
                f"BYTES on both installs, and it is not wired in yet "
                f"({DISPLACEMENT_OWNER} owns it). It is left unanswered "
                f"rather than approximated: the cheap stand-in is to ask "
                f"whether the two tables name the same mesh id, and on this "
                f"pair that agrees while the bytes differ "
                f"{stat['tablesAgreeBytesDiffer']:,} times, so it would call "
                f"the common case safe."),
        }

    state = getattr(res, "state", None) or res.get("state")
    note = (getattr(res, "note", "") or
            (res.get("note", "") if isinstance(res, dict) else "")) or ""
    out = {
        "state": state, "safe": state in DISPLACEMENT_SAFE,
        "available": True, "stub": False, "reason": "",
        "contract": DISPLACEMENT_CONTRACT, "owner": DISPLACEMENT_OWNER,
        "weaponId": wid, "tableKeys": keys, "stat": stat,
        "headline": "", "detail": note,
    }
    if state == NOT_ON_TARGET:
        out["headline"] = f"Not on {tinstall} — this is the normal case."
        out["detail"] = (
            f"{tinstall} has never heard of {wid}. Bringing it across ADDS a "
            f"weapon and overwrites nothing. Most of what you would want to "
            f"bring is in this state; it is the workflow, not a warning.")
    elif state == NO_OP:
        out["headline"] = f"Already on {tinstall}, byte for byte."
        out["detail"] = (
            f"The mesh {tinstall} already has is IDENTICAL to this one, so "
            f"bringing it across would change nothing at all. This is the "
            f"uncommon case: only {stat['bytesSame']:,} of "
            f"{stat['comparable']:,} shared ids "
            f"({stat['bytesSame'] / stat['comparable'] * 100:.0f}%) are "
            f"genuinely the same art.")
    elif state == REPLACES:
        out["headline"] = f"This REPLACES different art already on {tinstall}."
        out["detail"] = (
            f"{tinstall} has its own weapon under id {wid} and its mesh is "
            f"not this mesh. Writing this destroys it. On this pair that is "
            f"the COMMON case, not the exception: "
            f"{stat['bytesDiffer']:,} of {stat['comparable']:,} shared ids "
            f"({stat['bytesDiffer'] / stat['comparable'] * 100:.0f}%) resolve "
            f"to different bytes — and in "
            f"{stat['tablesAgreeBytesDiffer']:,} of those the two tables "
            f"still name the same mesh id, so nothing about the numbers "
            f"looks wrong until the art is gone.")
    else:
        out["headline"] = (f"Cannot tell whether this would replace art on "
                           f"{tinstall}.")
        out["detail"] = note or (
            "One or both meshes could not be read, so whether writing this "
            "destroys anything is unknown — which is not the same as safe.")
    return out


# ---------------------------------------------------------------------------
# the table-key helpers, kept for the secondary signal only
# ---------------------------------------------------------------------------

def art_key(facet) -> Optional[tuple]:
    """The comparable identity of an appearance facet, or None.

    ``(mesh, texture, mixTex)`` per part, in order -- the ids the TABLE names,
    not the art behind them. **Not** the raw record: the two installs answer
    from a compiled table and a plaintext one whose field names do not even
    overlap, so a raw comparison reports every shared id as different
    (measured: 100.0%) and means nothing.

    This is the secondary signal and must not drive the verdict; see
    `table_key_signal`.

    Returns None when the facet did not answer -- a third thing, distinct
    from "no parts", which is why it is None rather than ``()``.
    """
    status, rows = facet.unpack()      # status first
    if status not in (PRESENT, PARTIAL):
        return None
    return tuple((str(r.get("mesh", "")), str(r.get("texture", "")),
                  str(r.get("mixTex", ""))) for r in rows)


def _art_key_of(report: dict) -> Optional[tuple]:
    """`art_key` out of a rendered report, or None if it did not answer."""
    if not report.get("resolverAvailable"):
        return None
    if report.get("presence") != ON_INSTALL:
        return None
    fv = (report.get("facets") or {}).get("appearance")
    # `facet_view` already read the status; switching on it here keeps the
    # discriminator the status rather than the row count.
    if not fv or fv.get("status") not in (PRESENT, PARTIAL):
        return None
    return tuple((str(r.get("mesh", "")), str(r.get("texture", "")),
                  str(r.get("mixTex", ""))) for r in fv.get("rows") or ())


def _fmt_art(art: Optional[tuple], join: bool = False):
    if art is None:
        return "art that could not be resolved" if join else []
    pretty = [f"mesh {m}/texture {t}" + (f" (mixTex {x})" if x and x != "0"
                                         else "")
              for m, t, x in art]
    if join:
        return ", ".join(pretty) if pretty else "no parts at all"
    return pretty


# ---------------------------------------------------------------------------
# which files actually travel
# ---------------------------------------------------------------------------

def plan_files(report: dict, locate) -> dict:
    """The explicit file list for a weapon collect, decided by TABLE.

    ``locate(kind, name)`` is the caller's resolver from a table row's name to
    a logical path, ``""``, or -- for an effect, which is often a whole folder
    on disk -- a list of logical paths.

    That list is NOT the directory rule coming back in.  The distinction is
    which side names the directory.  `actions_beside` takes a mesh and guesses
    that its neighbours belong to it, which is how 455 unrelated models were
    once collected as one entry.  Here the TABLE names ``m-b02``, ``m-b02``
    is a folder (`c3/effect/monster-bomb/m-b02/1.c3` and five more on 6609),
    and taking its contents is following the table to its referent rather than
    guessing from adjacency.  Nothing is expanded that a table did not name.

    Returns ``{"mesh", "texture", "motions", "effects", "extraParts",
    "skipped", "conflicts", "surfaces"}`` where ``surfaces`` records, per
    surface, the status that decided it -- so a caller can report "no motions
    travelled because weaponmotion.ini has nothing for this weapon" instead of
    shipping a silent empty list.  ``skipped`` carries every file a table
    NAMED and the install did not have, and ``conflicts`` every key whose
    compiled and plaintext tables disagree; neither is dropped silently.
    """
    out: dict = {"mesh": "", "texture": "", "motions": [], "effects": [],
                 "extraParts": [], "skipped": [], "conflicts": [],
                 "surfaces": {}}
    if not report.get("resolverAvailable"):
        out["surfaces"] = {s: {"status": None, "reason": "resolver not wired in"}
                           for s in SURFACES}
        return out
    if report.get("presence") != ON_INSTALL:
        out["surfaces"] = {s: {"status": None,
                               "reason": f"presence is {report.get('presence')}"}
                           for s in SURFACES}
        return out

    facets = report.get("facets") or {}
    for surface in SURFACES:
        fv = facets.get(surface)
        if fv is None:
            out["surfaces"][surface] = {"status": None,
                                        "reason": "surface not reported"}
            continue
        status = fv.get("status")
        out["surfaces"][surface] = {"status": status,
                                    "source": fv.get("source", ""),
                                    "reason": fv.get("note", "")}
        # Switch on the status, never on whether the row list is empty.
        if status not in (PRESENT, PARTIAL):
            continue
        for row in fv.get("rows") or ():
            _plan_row(surface, row, locate, out)
    return out


def _plan_row(surface: str, row: dict, locate, out: dict) -> None:
    if surface == "appearance":
        mesh = locate("mesh", str(row.get("mesh", "")))
        tex = locate("texture", str(row.get("texture", "")))
        # A weapon's appearance can be several parts (blade, guard, glow). The
        # first resolvable one is the entry's own mesh; the rest travel beside
        # it, because a two-part weapon collected as one part is a weapon that
        # renders with a piece missing.
        if mesh:
            if not out["mesh"]:
                out["mesh"] = mesh
            else:
                out["extraParts"].append(mesh)
        if tex:
            if not out["texture"]:
                out["texture"] = tex
            else:
                out["extraParts"].append(tex)
        mix = str(row.get("mixTex", "") or "")
        if mix and mix != "0":
            p = locate("texture", mix)
            (out["extraParts"].append(p) if p else out["skipped"].append(
                {"surface": surface, "role": "mixTex", "name": mix,
                 "reason": "named by the table, not found on the install"}))
        for label, name, got in (("mesh", row.get("mesh"), mesh),
                                 ("texture", row.get("texture"), tex)):
            if name and not got:
                out["skipped"].append(
                    {"surface": surface, "role": label, "name": str(name),
                     "reason": "named by the table, not found on the install"})
        return
    if surface == "motions":
        # A motion row is `{key, path, tables, twinDisagrees}` and `path` is
        # ALREADY a logical path -- resolved by the table reader, which knows
        # the install's key grammar. Re-deriving it from the key here would be
        # a second, worse copy of that grammar.
        #
        # Many action keys legitimately share one file (measured on 6609:
        # weapon 1050000's six addressable keys all name
        # c3/mesh/1050000401.c3), so this dedupes. Six copies of one blob is
        # not six motions, and an entry claiming six would be lying in the
        # manifest.
        if row.get("twinDisagrees"):
            out["conflicts"].append(
                {"surface": surface, "key": str(row.get("key", "")),
                 "path": str(row.get("path", "")),
                 "tables": list(row.get("tables") or ()),
                 "reason": "the compiled table and its plaintext twin name "
                           "different files for this key"})
        p = str(row.get("path") or "")
        if not p:
            for key in ("file", "motion", "value"):
                if row.get(key):
                    p = locate("motion", str(row[key]))
                    break
        if not p:
            out["skipped"].append(
                {"surface": surface, "role": "motion", "name": repr(row),
                 "reason": "row carries no file name this planner recognises"})
        elif p not in out["motions"]:
            out["motions"].append(p)
        return
    if surface == "effects":
        # An effect row is not one file. On 6609 it is
        # `{head, HitEffect, HitSound, BlkEffect, BlkSound}` -- a hit effect,
        # a hit sound, a block effect and a block sound, all at once. Taking
        # the first recognised key and returning (which this did in its first
        # draft) collected the weapon's hit effect and silently dropped its
        # sounds and its block effect. The owner asked for "all of their
        # linked effects"; that means every one of these.
        #
        # `head` is skipped: it is the appearance head digits the row is keyed
        # by, not an asset.
        found = False
        for key, val in row.items():
            if key in ("head", "key", "tables", "twinDisagrees"):
                continue
            name = str(val or "").strip()
            if not name:
                continue
            found = True
            is_sound = name.rsplit(".", 1)[-1].lower() in ("wav", "mp3", "ogg")
            got = locate("effect", name)
            paths = [got] if isinstance(got, str) else list(got or ())
            paths = [p for p in paths if p]
            if paths:
                for p in paths:
                    if p not in out["effects"]:
                        out["effects"].append(p)
            else:
                out["skipped"].append(
                    {"surface": surface,
                     "role": "sound" if is_sound else "effect",
                     "name": name, "field": key,
                     "reason": "named by the table, not found on the install"})
        if not found:
            out["skipped"].append(
                {"surface": surface, "role": "effect", "name": repr(row),
                 "reason": "row carries no file name this planner recognises"})
        return
    # skins: alternate textures. Measured 2026-08-17: `ini/cosmetics.json`
    # ships on NEITHER 6609 nor CCO, so this surface is `unavailable` for all
    # 13,439 donor ids and this loop does not run on either install today.
    # Written anyway, because `unavailable` is a fact about the install and
    # not about the code, and an install that ships the table must not need a
    # code change to be read.
    for key in ("texture", "file", "path", "value", "name"):
        name = row.get(key)
        if name:
            p = locate("texture", str(name))
            (out["effects"].append(p) if p else out["skipped"].append(
                {"surface": surface, "role": "skin", "name": str(name),
                 "reason": "named by the table, not found on the install"}))
            return


def _check_contract() -> None:
    """Assert this module's mirrored vocabulary still matches Parser's.

    Runs only when Parser's module is importable; a no-op on a branch where
    it has not landed.  This is what stops the two drifting silently once the
    resolver is merged.
    """
    try:
        import weaponparts
    except Exception:
        return
    assert tuple(weaponparts.Facet.STATUSES) == STATUSES, (
        f"Facet.STATUSES changed: {weaponparts.Facet.STATUSES} != {STATUSES}")
    assert tuple(weaponparts.WeaponParts.PRESENCES) == (
        ON_INSTALL, NOT_ON_INSTALL, UNDETERMINED)
    assert tuple(weaponparts.WeaponParts.SURFACES) == SURFACES


_check_contract()
