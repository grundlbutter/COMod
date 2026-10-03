#!/usr/bin/env python3
r"""actionnames.py -- naming an action code by eye, with the refusal on the server.

    py -3 tools/actionnames.py list
    py -3 tools/actionnames.py fold          # ready `_A(...)` lines for anim.ACTIONS

WHAT THIS IS FOR
----------------
`anim.ACTIONS` names 75 of the action codes a player body ships. The rest are
real motions with no established meaning, and the only way to establish one is
to watch the clip and say what it is. That is the owner's highest-value
backlog item (handoff 4.1) and there has been nowhere to put the answer:
`grep` finds 0 hits for api_note / label / name / annot / pending / actionname
against 106 `def api_` methods, and the anim panel never calls `/api/tags`.

So this is the store and the rule. The browser half -- the filter, the
next/previous keys, the input in the anim panel -- goes on top of it.

THE RULE, AND IT IS ENFORCED HERE RATHER THAN DOCUMENTED
---------------------------------------------------------
**A name may only be attached to a motion the loadout OWNS.** A body with a
sword that has no motion set of its own animates out of the unarmed set; the
file it plays is somebody else's, and a name written against it is a claim
about the wrong animation. So `check_namable` re-resolves the route from
`(body, action, right, left)` **server-side** and raises `Refused` -- HTTP 409
-- when the route is not own. The client's own opinion is never trusted,
because the client is where the filter that picked the row lives and a filter
is exactly the thing that can be wrong.

`anim.route_is_own` is the one predicate, and using it here rather than
re-spelling it is deliberate: four callers had spelled it `how == "exact"`,
and every one of them read an ALIASED exact hit -- `exact (weapon set 580
animates from set 560)` -- as a fallback. Measured on 5517, body 002135000 +
weapon 580001: 40 of 157 rows were labelled "no own motion" and all 40 were
own. A fifth spelling here would have been a fifth instance of that bug, and
this module would have REFUSED 40 nameable rows while reporting a rule.

WHY THE STORE IS A LOG OF OBSERVATIONS AND NOT A `{code: name}` MAP
--------------------------------------------------------------------
Two installs can disagree about what action 290 looks like, and **the
disagreement is the evidence**, not a conflict to resolve. A map would take
whichever write landed last and silently discard the other; a log keeps both,
with the install and the motion file's sha256 beside each, so the reader can
see that two people watched two different clips. `pending_actions` reports a
code as `conflict` rather than picking a winner.

WHY IT IS GLOBAL AND NOT PER-BASE
-----------------------------------
The sharing boundary is whatever the artefact is about, and this one is about
ACTION CODES. The install is a COLUMN -- the same reasoning `coroot.GLOBAL`
gives for `out/client/` and the string index, and its strong form: every
question this file exists to answer is a comparison BETWEEN installs ("does
7878 play 290 the way 5517 does?"). Keying it per base would shatter one
naming effort into 36 partial copies, each missing the comparison.

It is therefore declared in BOTH places, because they answer different
questions: `coroot.GLOBAL` decides where the path resolves, and
`provenance.GLOBAL_OK` tells the audit the file is legitimately
install-independent -- an artefact in neither list is reported unclassified,
and that is the fail-open case the audit exists to close.

A PENDING NAME IS NEVER A CURATED ONE
---------------------------------------
Nothing here writes `anim.ACTIONS`. A pending name is reported with
confidence `pending` and the evidence string that says who named it, on which
install, from which file; `fold_lines` prints ready `_A(...)` lines and
promotion into the curated table stays a reviewed code change. A UI write that
became curated truth would be the whole point of the confidence column,
undone.

    localStorage was considered for the store and rejected: per-browser,
    invisible to the repo, and lost the first time the owner opens the page in
    a different window. The point of naming an action is that somebody else
    can read it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
for _sub in (_HERE, _HERE.parent / "core"):
    if str(_sub) not in sys.path:
        sys.path.insert(0, str(_sub))

import anim                                                  # noqa: E402
import coroot                                                # noqa: E402
import provenance                                            # noqa: E402

#: Declared GLOBAL in `coroot.GLOBAL` and `provenance.GLOBAL_OK`. Read this
#: name from here rather than spelling the path again -- one literal, so the
#: two declarations and the writer cannot drift.
REL = "out/action_names.json"

#: The confidence a named-by-eye row carries. Deliberately not one of
#: `anim.VERIFIED` / `INFERRED` / `UNKNOWN`: those three describe how well the
#: CURATED table is evidenced, and a pending row is not in that table at all.
PENDING = "pending"

#: What a code looks like when two installs were named differently.
CONFLICT = "conflict"

TOOL = "tools/actionnames.py"


class Refused(Exception):
    """The server declining to record a name, with the status it answers.

    Carries `status` so a handler does not re-derive it from the message --
    the reason and the code are one decision and they travel together.
    """

    def __init__(self, why: str, status: int = 409):
        super().__init__(why)
        self.why = why
        self.status = status


@dataclass
class Observation:
    """One person naming one action code, on one install, from one file.

    `motion` and `sha256` are the point of the record rather than decoration:
    a name is a claim about a CLIP, and two installs can ship different bytes
    at the same logical path. Without the hash, "5517 and 7878 agree" and
    "5517 and 7878 were shown different animations" are the same row.
    """
    code: str
    name: str
    install: str = ""
    motion: str = ""
    sha256: str = ""
    frames: int = 0
    aligned: bool = False
    body: str = ""
    weaponset: str = ""
    how: str = ""
    who: str = ""
    when: str = ""

    def stamped(self) -> "Observation":
        """A copy with `when` filled if the caller left it empty."""
        if self.when:
            return self
        d = asdict(self)
        d["when"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return Observation(**d)


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------

def route_for(db, body: str, action: str, *, right: str = "", left: str = "",
              shape: str = "") -> tuple[Optional[str], str, bool]:
    """`(path, how, own)` for one action of one loadout.

    The same three questions `anim.loadout_motions` asks per file, asked for a
    single code, and asked through the same functions so the two cannot
    disagree: `shape_of` / `weaponset` supply the halves of the key,
    `AnimDB.resolve` walks the documented fallback chain, and
    `anim.route_is_own` reads the route off `how`.
    """
    sh = shape or db.shape_of(body)
    ws = db.weaponset(right=right, left=left)
    path, how = db.resolve(sh, ws, action)
    return path, how, bool(path) and anim.route_is_own(how)


def check_namable(db, body: str, action: str, *, right: str = "",
                  left: str = "", shape: str = "") -> tuple[str, str]:
    """`(motion_path, how)`, or raise `Refused`.

    Four refusals, and each one is a different wrong claim:

    * **not a 3-digit code** -- 400. Nothing resolves and the store would grow
      a key no reader can look up.
    * **unresolved** -- 409. There is no clip to have watched.
    * **not own** -- 409. The clip plays out of a set this loadout does not
      own, so the name would be attached to another weapon's animation. This
      is the rule this module exists for.
    * **already curated** -- 409. `anim.ACTIONS` is the reviewed table; an
      overwrite from a web form is precisely what the confidence column
      exists to prevent. Naming over it has to be a code change.
    """
    code = (action or "").strip()
    if not (len(code) == 3 and code.isdigit()):
        raise Refused("%r is not a 3-digit action code" % action, 400)
    if code in anim.ACTIONS:
        a = anim.ACTIONS[code]
        raise Refused(
            "%s is already named %r in anim.ACTIONS (confidence %s). Changing "
            "a curated name is a reviewed code change, not a form post."
            % (code, a.name, a.confidence))
    path, how, own = route_for(db, body, code, right=right, left=left,
                               shape=shape)
    if not path:
        raise Refused("%s resolves to no motion for this loadout (%s)"
                      % (code, how))
    if not own:
        raise Refused(
            "%s plays %s by the route %r, which is not this loadout's own "
            "motion set. Naming it would attach the name to another weapon's "
            "animation." % (code, path, how))
    return path, how


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

def path_for(root=None) -> Path:
    """Where the log lives. Through `coroot.derived_path`, never the literal.

    `REL` is declared GLOBAL, so this resolves to the unkeyed location -- but
    it is resolved rather than hardcoded so that a change to the declaration
    moves the file instead of silently splitting readers from writers, which
    is the `out/dll/rtti.md` failure.
    """
    return coroot.derived_path(REL, root)


def load(root=None) -> list[dict]:
    """Every observation, oldest first. `[]` when there is nothing yet.

    Tolerates the envelope being absent: `provenance.unwrap` returns the
    document unchanged for an unstamped file, so a log written before this
    module stamped anything still reads.
    """
    p = path_for(root)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    _, payload = provenance.unwrap(doc)
    rows = payload.get("observations") if isinstance(payload, dict) else payload
    return [r for r in (rows or []) if isinstance(r, dict)]


def add(obs: Observation, root=None) -> Path:
    """Append one observation and return the file's path.

    ATOMIC, and it has to be: a viewer handler writes this while the same
    process may be reading it to render the panel, and a half-written JSON
    file is indistinguishable from an empty one to `load` -- which would
    report every earlier name as gone rather than as unreadable.

    The temp name carries the pid so two processes cannot collide on it, the
    same shape `coroot._save_user_config` uses.
    """
    rows = load(root)
    rows.append(asdict(obs.stamped()))
    p = path_for(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = provenance.wrap({"observations": rows}, root, TOOL)
    tmp = p.with_suffix(p.suffix + ".%d.tmp" % os.getpid())
    try:
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return p


def record(db, *, code: str, name: str, body: str, right: str = "",
           left: str = "", shape: str = "", who: str = "", root=None,
           frames: int = 0, aligned: bool = False,
           sha256: str = "") -> Observation:
    """Check the rule, then write. The one entry point a handler should call.

    Check and write are together on purpose. Two callers doing
    `check_namable` and `add` in sequence is two places for the order to be
    got wrong, and the wrong order writes first and refuses afterwards.
    """
    label = (name or "").strip()
    if not label:
        raise Refused("a name is required", 400)
    path, how = check_namable(db, body, code, right=right, left=left,
                              shape=shape)
    obs = Observation(
        code=code.strip(), name=label,
        install=coroot.base_id(root) or "",
        motion=path, sha256=sha256, frames=frames, aligned=aligned,
        body=body, weaponset=db.weaponset(right=right, left=left),
        how=how, who=who or "",
    ).stamped()
    add(obs, root)
    return obs


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------

def pending_actions(root=None) -> dict[str, anim.Action]:
    """`{code: Action}` for every pending name, ready to merge beside `ACTIONS`.

    A code named twice with the SAME name collapses to one row whose evidence
    lists both installs. Named twice DIFFERENTLY it comes back with confidence
    `conflict` and both names in the evidence -- reported, never resolved. The
    module has no basis for preferring one person's eyes over another's, and
    picking silently is how a wrong name becomes settled.

    A code that reached `anim.ACTIONS` since it was named is dropped: the
    curated table wins, and the log keeps the observation as history.
    """
    by_code: dict[str, list[dict]] = {}
    for r in load(root):
        c = str(r.get("code") or "")
        if c and c not in anim.ACTIONS:
            by_code.setdefault(c, []).append(r)
    out: dict[str, anim.Action] = {}
    for code, rows in sorted(by_code.items()):
        names = list(dict.fromkeys(str(r.get("name") or "").strip()
                                   for r in rows if (r.get("name") or "").strip()))
        if not names:
            continue
        ev = "; ".join(
            "named by eye on %s from %s%s" % (
                r.get("install") or "an unnamed install",
                r.get("motion") or "an unrecorded path",
                (" as %r" % r["name"]) if len(names) > 1 else "")
            for r in rows)
        out[code] = anim.Action(
            code=code,
            name=names[0] if len(names) == 1 else " / ".join(names),
            group="pending",
            confidence=PENDING if len(names) == 1 else CONFLICT,
            evidence=ev,
        )
    return out


def fold_lines(root=None) -> list[str]:
    """Ready `_A(...)` lines for `anim.ACTIONS`, one per settled pending name.

    A CONFLICT is not printed -- there is nothing to promote until somebody
    decides which name is right, and emitting one of them would make the
    decision by printing order.

    The group is left as the literal `"pending"` for the reviewer to replace:
    the curated groups are a taxonomy this module has no way to place a new
    code in, and guessing one would look reviewed.
    """
    out = []
    for code, a in sorted(pending_actions(root).items()):
        if a.confidence != PENDING:
            continue
        out.append('    _A(%r, %r, "pending", UNKNOWN,\n       %r),'
                   % (code, a.name, a.evidence))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        rows = load()
        if not rows:
            print("no names yet (%s)" % path_for())
            return 0
        print("%-5s %-28s %-18s %s" % ("code", "name", "install", "motion"))
        for r in rows:
            print("%-5s %-28s %-18s %s" % (r.get("code", ""), r.get("name", ""),
                                           r.get("install", ""), r.get("motion", "")))
        pend = pending_actions()
        n_conf = sum(1 for a in pend.values() if a.confidence == CONFLICT)
        print("\n%d observation(s), %d code(s) pending, %d in conflict"
              % (len(rows), len(pend), n_conf))
        return 0
    if cmd == "fold":
        lines = fold_lines()
        if not lines:
            print("# nothing settled to fold")
            return 0
        print("# paste into anim.ACTIONS and replace the placeholder group")
        for ln in lines:
            print(ln)
        return 0
    print(__doc__.strip().splitlines()[0])
    print("usage: actionnames.py [list|fold]")
    return 2


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
