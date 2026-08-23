"""The swap page's model: what you can swap, what comes with it, and what a
swap would actually do to the install.

This module is deliberately free of HTTP and of the catalog class, so the two
judgements that matter can be tested without a running server:

`diagnose_parts`
    why an animation list is empty.  **An empty parts list is a fault until
    proven otherwise**, and this returns which of the two it is.

`plan_swap`
    OVERWRITE versus SPLIT, and where each file lands.  The destination is
    derived from the *target*; the caller never types a path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# the six kinds the owner asked for
# ---------------------------------------------------------------------------
#
# These are not one enum in this codebase, and pretending they are is how a
# picker ends up offering a Body where only an NPC can go.  Three of them are
# `tools/models.py` model kinds -- a directory per look, actions as sibling
# files -- and three are `tools/builder.py` equipment slots, which are flat
# files named by appearance id.  Measured over `Clients/7878`:
#
#     c3/npc/      128 directories   DIR-FAMILY   models.py kind "npc"
#     c3/monster/  127 directories   DIR-FAMILY   models.py kind "monster"
#     c3/mount/     89 directories   DIR-FAMILY   models.py kind "mount"
#     c3/body/     601 flat files    FLAT-FAMILY  builder slot "body"
#     c3/head/       8 flat files    FLAT-FAMILY  builder slot "armet"
#     c3/weapon/   164 flat files    FLAT-FAMILY  builder slot "r_weapon"
#
# The split is the whole reason `skin_destination` exists: a FLAT donor staged
# over a DIR target has no sibling to put its texture beside, and guessing one
# is what produced `c3/npc/999001100.dds`.

@dataclass(frozen=True)
class Kind:
    """One entry in the left pane's kind picker."""
    key: str
    label: str
    family: str          # "dir" | "flat"
    source: str          # "models" | "builder"
    ref: str             # models.py kind key, or builder slot name
    tree: str            # the logical prefix its art lives under


KINDS: tuple[Kind, ...] = (
    Kind("npc", "NPC", "dir", "models", "npc", "c3/npc/"),
    Kind("monster", "Monster", "dir", "models", "monster", "c3/monster/"),
    Kind("mount", "Mount", "dir", "models", "mount", "c3/mount/"),
    Kind("body", "Body", "flat", "builder", "body", "c3/body/"),
    Kind("head", "Head", "flat", "builder", "armet", "c3/armet/"),
    Kind("weapon", "Weapon", "flat", "builder", "r_weapon", "c3/weapon/"),
)

KIND_BY_KEY = {k.key: k for k in KINDS}


# ---------------------------------------------------------------------------
# rule (a): an empty parts list is a fault until proven otherwise
# ---------------------------------------------------------------------------

#: What `diagnose_parts` can conclude.
#:
#: ``ok``       parts were found.
#: ``none``     **certain** there are none: the directory holds no other
#:              mesh file at all, so there is nothing that could have been an
#:              action.  A model with no animations is a real thing.
#: ``fault``    **certain** the resolver failed: files that are actions by
#:              every visible sign were found and then dropped, and the reason
#:              each was dropped is reported.
#: ``unknown``  **cannot tell.**  Sibling meshes exist but none of them is
#:              named like an action of this one.  That is either a layout
#:              this project has not learned yet (fault) or a directory of
#:              unrelated models (none), and nothing on disk separates them.
#:              Saying so is the honest answer; a tidy empty list is not.
STATES = ("ok", "none", "fault", "unknown")


@dataclass
class PartsReport:
    """Why `gather_parts` returned what it returned."""
    mesh: str
    parts: list = field(default_factory=list)
    state: str = "ok"
    fault: str = ""                       # machine-readable cause
    headline: str = ""                    # the sentence the UI shows
    detail: str = ""
    candidates: list = field(default_factory=list)
    dropped: dict = field(default_factory=dict)   # cause -> [paths]
    siblings: int = 0

    @property
    def certain(self) -> bool:
        """Whether 'has none' and 'resolver failed' could be told apart."""
        return self.state != "unknown"

    def to_json(self) -> dict:
        return {"mesh": self.mesh, "parts": self.parts, "state": self.state,
                "fault": self.fault, "headline": self.headline,
                "detail": self.detail, "candidates": self.candidates,
                "dropped": {k: list(v) for k, v in self.dropped.items()},
                "siblings": self.siblings, "certain": self.certain}


def diagnose_parts(read, list_under, mesh: str,
                   effects: Iterable[str] = ()) -> PartsReport:
    """Run `gather_parts` and, when it comes back empty, say *why*.

    The reason this exists rather than a `len(parts) == 0` check in the page:
    `core.collection.gather_parts` drops candidates in four different places
    and reports none of them.  It calls `actions_beside` **without** the
    ``record`` dict that function offers (`collection.py:356`), so even the
    one discard the codebase built a channel for arrives silent.

    Measured over `Clients/7878`, every empty-but-should-not-be case in
    `c3/npc/`, `c3/monster/` and `c3/mount/` is the *same* drop -- the
    unanchored-plus-geometry test at `collection.py:369` -- and the
    content-folder classifier that ``record`` reports never fires once:

        npc      178 ok    96 fault   137 none   27 no mesh in directory
        monster  198 ok    81 fault    47 none  134 no mesh in directory
        mount     36 ok    55 fault    55 none   17 no mesh in directory

    `c3/npc/2231/` is one of the 96: `1.C3`, `100.c3`, `101.c3`, `190.c3` on
    disk, three of them recognised as actions, all three then dropped because
    they carry geometry as well as MOTI.  This function reports that as a
    fault and names the three files, which is the whole point.
    """
    from collection import gather_parts, actions_beside, _has_geometry

    rep = PartsReport(mesh=mesh)
    parts = gather_parts(read, list_under, mesh, effects=effects)
    rep.parts = [{"role": r, "name": n, "path": p, "bytes": len(b)}
                 for r, n, p, b in parts]
    if parts:
        rep.state = "ok"
        rep.headline = f"{len(parts)} part(s) travel with this asset."
        return rep

    key = (mesh or "").replace("\\", "/").lower()
    if not key.endswith(".c3"):
        rep.state = "fault"
        rep.fault = "not-a-mesh"
        rep.headline = ("No animations found — this asset was not resolved to "
                        "a mesh file.")
        rep.detail = (f"{mesh!r} does not end in .c3, so the animation lookup "
                      f"returned before it began. This is a fault in what was "
                      f"passed here, not a model without animations.")
        return rep

    # What is actually in the directory, by the same listing the resolver saw.
    d = key.rsplit("/", 1)[0] + "/"
    depth = key.count("/")
    sibs = [p for p in list_under(d)
            if p.endswith(".c3") and p != key and p.count("/") == depth]
    rep.siblings = len(sibs)

    record: dict = {}
    cand = actions_beside(list_under, mesh, record=record)
    rep.candidates = [p for _c, p, _a in cand]

    if record:
        # The one discard the codebase already knew how to report. It has
        # never been observed firing on this corpus, but it is the loudest
        # possible failure when it does, so it is checked first.
        rep.state = "fault"
        rep.fault = "content-folder"
        n = record.get("discarded", 0)
        rep.headline = (f"No animations found — {n} candidate(s) were "
                        f"discarded, not absent.")
        rep.detail = (
            f"{record.get('directory', d)} holds more than "
            f"{record.get('threshold')} loose action-shaped files, so it was "
            f"judged a content folder rather than one model's action set and "
            f"all {n} unanchored matches were dropped. This is a fault: the "
            f"files are there.")
        rep.dropped["content-folder"] = list(record.get("candidates", []))[:64]
        return rep

    if not cand:
        if not sibs:
            rep.state = "none"
            rep.headline = "This model genuinely has no animations."
            rep.detail = (
                f"{d} holds no mesh file other than the model itself, so "
                f"there is nothing that could be an action. This is a real "
                f"model without animations, not a failed lookup.")
        else:
            rep.state = "unknown"
            rep.fault = "no-anchor"
            rep.headline = ("Cannot tell whether this has no animations or "
                            "whether the lookup failed.")
            rep.detail = (
                f"{d} holds {len(sibs)} other mesh file(s), but not one of "
                f"them is named like an action of this model. That is either "
                f"a naming layout this project has not learned yet, or a "
                f"directory of unrelated models. Nothing on disk separates "
                f"the two, so neither answer is claimed here.")
            rep.dropped["unmatched-siblings"] = sibs[:64]
        return rep

    # Candidates were found and then every one of them was dropped. Re-run the
    # same three tests gather_parts applies so the UI can name the cause.
    for _code, p, anchored in cand:
        try:
            blob = read(p)
        except Exception as e:
            rep.dropped.setdefault("read-failed", []).append(f"{p}: {e!r}")
            continue
        if b"MOTI" not in blob:
            rep.dropped.setdefault("no-MOTI", []).append(p)
        elif not anchored and _has_geometry(blob):
            rep.dropped.setdefault("unanchored-with-geometry", []).append(p)
        else:                                            # pragma: no cover
            rep.dropped.setdefault("kept-but-absent", []).append(p)

    rep.state = "fault"
    causes = sorted(rep.dropped)
    rep.fault = causes[0] if len(causes) == 1 else "mixed"
    rep.headline = (f"No animations found for this asset — this is a fault, "
                    f"not a model without animations.")
    if rep.fault == "unanchored-with-geometry":
        rep.detail = (
            f"{len(cand)} file(s) beside this mesh are named like its actions "
            f"and carry animation data, but each also carries geometry, and "
            f"the resolver drops an unanchored match that has geometry "
            f"(core/collection.py:369). The animations exist on disk.")
    elif rep.fault == "no-MOTI":
        rep.detail = (
            f"{len(cand)} file(s) are named like this model's actions but "
            f"hold no MOTI chunk, so none was accepted as animation.")
    elif rep.fault == "read-failed":
        rep.detail = (f"{len(cand)} action file(s) were found and none could "
                      f"be read from the active view.")
    else:
        rep.detail = (f"{len(cand)} action file(s) were found and every one "
                      f"was dropped, for more than one reason.")
    return rep


# ---------------------------------------------------------------------------
# the cohort query -- COMod Parser's, stubbed here
# ---------------------------------------------------------------------------

#: The contract this page is built against, owned by the COMod Parser team:
#: ``cohort(kind, id) -> {"members": [...], "count": int, "shares_on": [...]}``
COHORT_CONTRACT = 'cohort(kind, id) -> {members, count, shares_on}'
COHORT_OWNER = "COMod Parser"


class CohortUnavailable(RuntimeError):
    """Raised when no cohort provider is wired in.

    This is an exception rather than an empty result on purpose. A cohort of
    zero and an unanswered cohort question lead to opposite UI: the first says
    "nothing else shares this, overwriting is safe", the second says "nobody
    knows what else this would change". Returning `{}` would let the page
    quietly render the reassuring one.
    """


def cohort(kind: str, ident: str, *, provider: Optional[Callable] = None) -> dict:
    """Who else shares this asset's mesh set.

    ``provider`` is the real implementation, injected by the caller. Until
    Parser lands it there is none, and this raises `CohortUnavailable` rather
    than inventing a count.

    Measured by the Parser team and quoted in the brief this was built from:
    437 NPC rows, 382 of them (87%) share a mesh set with at least one other
    row, and the Storekeeper's motion triple is shared by 38 NPCs including
    Mark.Controller. Those numbers are the *reason* for the question, not an
    answer this function may return on Parser's behalf.
    """
    if provider is None:
        raise CohortUnavailable(
            f"no cohort provider wired in: {COHORT_CONTRACT} is owned by "
            f"{COHORT_OWNER} and has not landed yet")
    res = provider(kind, ident)
    if not isinstance(res, dict) or "count" not in res:
        raise CohortUnavailable(
            f"cohort provider returned {type(res).__name__}, expected "
            f"{COHORT_CONTRACT}")
    return res


def cohort_report(kind: str, ident: str, *, provider=None) -> dict:
    """`cohort` in a shape the page can render, including its own absence.

    Never returns a count it does not have. ``available: False`` carries the
    contract and the owner so the reader learns *what* is missing and *whose*
    it is, which is what tells a stub apart from a real zero.
    """
    try:
        res = cohort(kind, ident, provider=provider)
    except CohortUnavailable as e:
        return {"available": False, "stub": True, "reason": str(e),
                "contract": COHORT_CONTRACT, "owner": COHORT_OWNER,
                "count": None, "members": [], "sharesOn": [],
                "headline": "Cannot tell how many other rows share this art.",
                "detail": (
                    f"The cohort query ({COHORT_CONTRACT}) is {COHORT_OWNER}'s "
                    f"and is not wired in yet, so the number of rows an "
                    f"overwrite would change is unknown — not zero. Overwrite "
                    f"is offered, but it cannot be told how far it reaches.")}
    count = int(res.get("count") or 0)
    members = list(res.get("members") or [])
    shares = list(res.get("shares_on") or [])
    return {"available": True, "stub": False, "reason": "",
            "contract": COHORT_CONTRACT, "owner": COHORT_OWNER,
            "count": count, "members": members, "sharesOn": shares,
            "headline": (f"{count} rows share this art."
                         if count > 1 else "Nothing else shares this art."),
            "detail": ""}


# ---------------------------------------------------------------------------
# rule (b): OVERWRITE and SPLIT are different operations
# ---------------------------------------------------------------------------

OVERWRITE = "overwrite"
SPLIT = "split"

#: SPLIT is the default because the destructive option is the common case:
#: 382 of 437 NPC rows (87%) share their mesh set with another row, so an
#: OVERWRITE picked by accident is more likely than not to change something
#: the user never looked at.
DEFAULT_MODE = SPLIT

MODES = {
    OVERWRITE: {
        "key": OVERWRITE,
        "label": "Change all of them",
        "summary": "Write the new art where this row already points.",
        "effect": "Every row sharing this art changes. No table edit.",
        "editsTable": False,
        "destructive": True,
    },
    SPLIT: {
        "key": SPLIT,
        "label": "Change this one only",
        "summary": "Give this row its own copy of the art and point it there.",
        "effect": "Only this row changes. Adds new mesh ids and edits npc.json.",
        "editsTable": True,
        "destructive": False,
    },
}


@dataclass
class SwapPlan:
    """What a swap would do, before it does it."""
    donor: str = ""
    target: str = ""
    kind: str = ""
    mode: str = DEFAULT_MODE
    skin: bool = True
    writes: list = field(default_factory=list)   # [{role, from, to}]
    unresolved: list = field(default_factory=list)
    cohort: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {"donor": self.donor, "target": self.target, "kind": self.kind,
                "mode": self.mode, "skin": self.skin, "writes": self.writes,
                "unresolved": self.unresolved, "cohort": self.cohort,
                "notes": self.notes,
                "modes": [MODES[OVERWRITE], MODES[SPLIT]],
                "defaultMode": DEFAULT_MODE}


def plan_swap(donor_mesh: str, target_mesh: str, *, kind: str = "",
              exists=None, authored=None, tables=None, skin: bool = True,
              mode: str = DEFAULT_MODE, parts: Iterable[dict] = (),
              cohort_provider=None, target_ident: str = "") -> SwapPlan:
    """Where every file in this swap lands.

    **Rule (c): the alt skin follows the target.** The donor's texture bytes
    are written to the *target's* skin path, which `skin_destination` derives
    from the target and only ever returns if it exists. Deriving it from the
    donor is what put a Zephyr NPC's texture at `c3/npc/999001100.dds`, a path
    nothing reads -- see `core/collection.py:390`.
    """
    from collection import skin_destination, action_target_name

    plan = SwapPlan(donor=donor_mesh, target=target_mesh, kind=kind,
                    mode=mode if mode in MODES else DEFAULT_MODE, skin=skin)

    plan.writes.append({"role": "mesh", "from": donor_mesh, "to": target_mesh,
                        "why": "the target's own mesh path"})

    if skin:
        dest = ""
        if exists is not None:
            dest = skin_destination(target_mesh, exists, authored=authored,
                                    tables=tables)
        if dest:
            plan.writes.append({
                "role": "skin", "from": "", "to": dest,
                "why": "the target's skin path, derived from the target"})
        else:
            plan.unresolved.append({
                "role": "skin", "target": target_mesh,
                "why": ("No skin path could be worked out for the target. "
                        "Nothing is written rather than guessing a path the "
                        "client does not read.")})

    t_stem = target_mesh.replace("\\", "/").rsplit("/", 1)[-1]
    t_stem = t_stem[:-3] if t_stem.lower().endswith(".c3") else t_stem
    t_dir = target_mesh.replace("\\", "/").rsplit("/", 1)[0] + "/"
    for p in parts:
        role = p.get("role", "motion")
        if role != "motion":
            continue
        src = p.get("path", "")
        code = p.get("code") or _action_code_of(src)
        if not code:
            plan.unresolved.append({"role": role, "target": src,
                                    "why": "no action code could be read "
                                           "from this part's name"})
            continue
        plan.writes.append({
            "role": "motion", "from": src,
            "to": t_dir + action_target_name(t_stem, code),
            "why": "named in the target's own action naming"})

    plan.cohort = cohort_report(kind or "npc", target_ident or t_stem,
                                provider=cohort_provider)
    if plan.mode == OVERWRITE and not plan.cohort.get("available"):
        plan.notes.append(
            "Overwrite was chosen but the number of rows it would change is "
            "unknown, because the cohort query has not landed.")
    return plan


_CODE = re.compile(r"(\d{1,4})$")


def _action_code_of(path: str) -> str:
    stem = path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = stem[:-3] if stem.lower().endswith(".c3") else stem
    m = _CODE.search(stem)
    return m.group(1) if m else ""
