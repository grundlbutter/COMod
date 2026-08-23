#!/usr/bin/env python3
r"""
npcalloc.py -- may we mint an NPC art id for THIS client, and which one.

    from npcalloc import can_serve, can_serve_row, allocate

    a = can_serve("cco")
    if a.served:
        grp, width, layout = allocate("cco", occupied=census(read, listdir))

**THREE ANSWERS, NEVER TWO.**  Every question here is answered with `served`,
`UNSUPPORTED(reason)` or `UNKNOWN(reason)`, and the three do not collapse:

* ``UNSUPPORTED`` -- we looked, and we cannot serve it.
* ``UNKNOWN``     -- **nobody has looked.**  This is the honest default for
  anything unmeasured, and folding it into either of the other two is the
  defect this module exists to prevent.

`Answer.__bool__` **raises**.  ``if can_serve(t):`` is exactly the fold this
contract forbids, so it is a crash rather than a convention -- the same
construction-not-convention bar the boundary guards are held to.

WHY `allocate` TAKES A TARGET
-----------------------------
Because a caveat in prose does not constrain the code written next to it.
The free-set measurement below was reported twice with "CCO only" attached as
a sentence, and a single global `allocate()` would still have shipped, because
``999 + group + action`` *feels* like the format rather than one client's
spelling of it.  **What enforces a scope is a parameter.**

The cost of getting it wrong is silent.  The swap page defaults to SPLIT and
SPLIT allocates, so on a client we cannot serve, the default operation would
mint a well-formed-looking id, write it to a path the client never reads, and
the page would look like it worked.  An allocator that always returns a
plausible id is the same object as a cohort stub that returns zero.

MEASURED, 2026-08-16, from the installs on disk (read-only)
-----------------------------------------------------------
**The id width differs by client, and that is the whole reason for `target`.**

    CCO   ini/npc.json   nine characters   999001100   390 of 437 rows
    7878  ini/npc.ini    ten  characters   9990010100  3,284 of 4,087 rows

    nine-character 999* ids anywhere in 7878's three motion fields:  ZERO

So an allocator that returns group ``015`` and mints ``999015100`` is
well-formed on CCO and malformed on 7878.  `allocate` therefore returns the
group **already spelled at the target's write width** -- ``"015"`` on CCO,
``"0150"`` on 7878 -- so a caller cannot mint the wrong width by accident.

**WHAT THE GROUP FIELD MEANS IS NOT FULLY SETTLED, and this module does not
pretend otherwise.**  Both a 3-wide and a 4-wide reading of the group survive
the evidence:

    9995230100  ->  c3/npc/523/100.c3       [3:6]="523"  or  [3:7]="5230"
    9990010100  ->  SimpleObjID 1           [3:6]="001"  or  [3:7]="0010"
    9990111100  ->  keyed 999011100         [3:6]="011"  but [3:7]="0111"
    99922760100 ->  SimpleObjID 2276                         [3:7]="2276"

The third and fourth rows disagree about which side the padding is on, and
nobody has measured the client's own parse.  **`occupied_groups` is therefore
fail-closed: every observed id contributes EVERY plausible group reading, and
a group is free only when it is free under all of them.**  That costs a few
groups out of a thousand and cannot mint a collision by picking the wrong
reading.

THE FREE SET IS THE COMPLEMENT OF A UNION -- NEVER OF ONE TABLE
---------------------------------------------------------------
Measured on CCO (2026-08-16), 999-family groups occupied per source::

    ini/npc.json          81
    ini/3dmotion.ini     126
    ini/3dobj.ini         60
    ini/3dtexture.ini     61
    ini/3DSimpleObj.ini   59
    c3/npc/ on disk       41
    ------------------------
    UNION                175

    first free by npc.json ALONE : 015  -- see below, this is the trap
    first free by the UNION      : 015

and the trap, which is the finding this rule was bought with::

    first free by npc.json alone, 3-wide reading only : 002
      -> 002 is occupied in 3dmotion.ini, 3dobj.ini, 3dtexture.ini
         and 3DSimpleObj.ini.  Four tables, none of them the one asked.

`occupied_groups` takes the sources it is given and unions them; there is no
single-table entry point, on purpose.

**`000` is unoccupied in every source measured and is still not handed out.**
`FLOOR` starts the scan at 1 because whether ``999000<act>`` resolves at all
is unmeasured, and an allocator must not hand out an id nobody has tested.
That is a policy, it is stated, and it costs one group in a thousand.

A SLOT IS A ROLE; THE SUFFIX IS A FILE
---------------------------------------
Do not build a map keyed on the action number.  Measured on CCO's 437 rows::

    blaze_motion : {190: 299, 100: 49, 102: 40, 101: 2}
    rest_motion  : {101: 341, 100: 47, 102: 2}

``action 190 -> blaze`` is wrong on 91 of 390 blaze rows, and ``100`` appears
in all three roles.  `MOTION_ROLES` is a role tuple and nothing here maps a
suffix to a role.

WHAT A ROW CAN BE
-----------------
Measured on 7878's ``ini/npc.ini``, 4,087 sections, exhaustive -- every row
lands in exactly one class and the classes add to 4,087::

    3,284  ten-character 999* in all three motion fields   -> served
      108  StandBy/Blaze/Rest AND SimpleObjID all "0"      -> served, no art
      420  ten-character, another namespace  1340000100    -> UNKNOWN
      155  eleven-character, 999*            09997840100   -> UNKNOWN
       55  eleven-character, another namespace 02850000100 -> UNKNOWN
       28  eight-character                   10000001      -> UNKNOWN
       21  nine-character, all 980*          980000001     -> UNKNOWN
       16  the three motion fields ABSENT entirely         -> UNKNOWN

A prior count of the ragged set read 238 rows in two widths (210 + 28). It is
259 in three: the 210 eleven-character rows split 155 `999*` / 55 not, plus
the 28 eight-character rows, plus **21 nine-character `980*` rows nobody had
listed**. The 16 field-less sections had also been counted with the 108 that
declare zero -- `4,087 - 4,071 = 16` is exactly that gap, and *absent* is not
*declared zero*.

The **108** are a DECLARATION, not missing data: the zero is uniform across
four fields on exactly those rows and no others, while every non-zero row
carries a varied `SimpleObjID` (9970 x 756, 15 x 97, 744 x 65).  "This NPC
declares no art" is a measurement, and returning `UNKNOWN` for it would
launder a measurement into a shrug.  It is `served`, with a reason that says
so, and `Answer.no_art` distinguishes it from an ordinary `served`.

The **259** ragged rows (210 + 28 + 21) are `UNKNOWN`.  Nobody has looked at
whether they are a second convention, a bleed, or malformed, and they must
stay that way until somebody does.

The **16** absent rows are `UNKNOWN` too, and separately: they are empty
sections carrying no `Name` either.  Whether they are padding or a reader
artefact is not known.

SEVEN OF NINE CLIENTS ARE UNMEASURED
-------------------------------------
5017, 5065, 5165, 5517, 6090, 6609 and Zephyr-1057 have not been measured for
this question and `can_serve` returns `UNKNOWN` for every one of them.  Do not
assume the CCO or 7878 convention generalises -- that assumption is the exact
bug this contract exists to prevent, and 6090 is already known to spell its
motion ids differently again (`npcart.py`, ten digits keyed by low 32 bits).

Numbers travel with the client they were measured on.  "First free is 015" is
a CCO fact and is spelled that way everywhere in this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = [
    "SERVED", "UNSUPPORTED", "UNKNOWN", "Answer", "Convention",
    "CONVENTIONS", "UNMEASURED", "MOTION_ROLES", "FLOOR",
    "can_serve", "can_serve_row", "allocate",
    "group_candidates", "occupied_groups", "NotServed",
]

#: The three answers. They are strings so a report can print one, and they
#: are compared by identity of value nowhere -- use `Answer.served` etc.
SERVED = "served"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"

#: The 999 family. Everything here is scoped to it; a non-999 id belongs to
#: another namespace and this module says so rather than guessing.
PREFIX = "999"

#: The scan floor. `000` is measured unoccupied on CCO and still not handed
#: out -- see the module docstring.
FLOOR = 1

#: Both measured clients expose exactly three motion roles, and a role is not
#: an action suffix. CCO spells them lowercase, 7878 CamelCase; the tuple
#: order is the same and callers should zip against `Convention.motion_fields`.
MOTION_ROLES = ("standby", "rest", "blaze")


class NotServed(RuntimeError):
    """`allocate` was called for a target that is not `served`.

    Raised rather than returning a plausible id, because a plausible id is
    indistinguishable from a correct one at the call site.
    """


@dataclass(frozen=True)
class Answer:
    """One of three states, and it refuses to become one of two.

    `__bool__` raises. That is not decoration: ``if can_serve(t): allocate(t)``
    reads as correct, compiles, and silently treats `UNKNOWN` as "no" -- which
    on the swap page means an unmeasured client quietly takes the SPLIT path
    that mints an unresolvable mesh. Making the fold a `TypeError` is the only
    version of this rule that a reviewer cannot forget to apply.
    """
    state: str
    reason: str = ""
    #: Free-form measured detail. Never load-bearing for control flow.
    detail: Mapping = field(default_factory=dict)

    def __bool__(self):                                    # noqa: D105
        raise TypeError(
            "npcalloc.Answer has three states and truth-testing collapses it "
            "to two. Ask for .served / .unsupported / .unknown explicitly -- "
            f"this one is {self.state}: {self.reason}")

    @property
    def served(self) -> bool:
        return self.state == SERVED

    @property
    def unsupported(self) -> bool:
        return self.state == UNSUPPORTED

    @property
    def unknown(self) -> bool:
        return self.state == UNKNOWN

    @property
    def no_art(self) -> bool:
        """`served`, and the row itself declares it has no art.

        Distinct from `served` (there is art to swap) and from `UNKNOWN`
        (nobody looked). The 108 all-zero 7878 rows are this.
        """
        return self.served and bool(self.detail.get("declares_no_art"))

    def __str__(self) -> str:                              # noqa: D105
        return f"{self.state}({self.reason})" if self.reason else self.state


def served(reason: str = "", **detail) -> Answer:
    return Answer(SERVED, reason, detail)


def unsupported(reason: str, **detail) -> Answer:
    return Answer(UNSUPPORTED, reason, detail)


def unknown(reason: str, **detail) -> Answer:
    return Answer(UNKNOWN, reason, detail)


@dataclass(frozen=True)
class Convention:
    """How ONE client spells a 999-family NPC art id. Measured, per client.

    `group_width` is the width `allocate` **writes**, not a claim that the
    client parses the field that way -- see the module docstring on the
    unresolved 3-vs-4 reading. Writing a 3-digit group followed by a `0`
    filler satisfies both readings, which is why the 7878 spelling of group
    `015` is `"0150"`.
    """
    target: str
    #: Total characters in a minted id. 9 on CCO, 10 on 7878.
    id_width: int
    #: Characters `allocate` writes for the group.
    group_width: int
    #: Characters the action occupies. 3 on both measured clients.
    action_width: int
    #: The npc table this client's rows come from.
    npc_table: str
    #: The three motion field names, in `MOTION_ROLES` order.
    motion_fields: tuple
    #: The layout `allocate` declares it will WRITE. **Not** a claim that the
    #: client only understands one -- both measured clients resolve flat and
    #: nested paths from the same table (CCO: 138 flat / 122 nested of 260
    #: 999 motion entries; 7878: 169 flat / 23 nested of 192). Flat is the
    #: plurality on both and is the one a swap can create without inventing a
    #: directory the client was never told about.
    write_layout: str
    #: One line of provenance, printed by reports.
    evidence: str

    def __post_init__(self):
        w = len(PREFIX) + self.group_width + self.action_width
        if w != self.id_width:
            raise ValueError(
                f"{self.target}: prefix+group+action is {w} characters but "
                f"id_width says {self.id_width}")

    def spell_group(self, group3: str) -> str:
        """A three-digit group, spelled at this client's group width.

        **Padded on the RIGHT, and that is measured rather than obvious.**
        `zfill` would give 7878 group `015` as ``"0015"``; the install says
        ``"0150"``::

            7878 NpcType1   Storekeeper  SimpleObjID 1    9990010100
                                                          [3:6]="001" +"0"
            7878 NpcType2   Sassoon      SimpleObjID 266  9992660100
                                                          [3:6]="266" +"0"

        so the fourth character is a trailing filler and the group itself
        still reads at [3:6]. Left-padding here would mint group `001` while
        claiming to mint `015`, and every such id is well-formed -- the swap
        would resolve, to the wrong NPC.
        """
        return str(group3).zfill(3).ljust(self.group_width, "0")

    def spell(self, group3: str, action: str = "100") -> str:
        """The full id this client would write for `group3` and `action`."""
        return (PREFIX + self.spell_group(group3)
                + str(action).zfill(self.action_width))


CONVENTIONS: dict = {
    "cco": Convention(
        target="cco",
        id_width=9,
        group_width=3,
        action_width=3,
        npc_table="ini/npc.json",
        motion_fields=("standby_motion", "rest_motion", "blaze_motion"),
        write_layout="flat",
        evidence=("ini/npc.json 2026-08-16: 390 of 437 rows spell all three "
                  "motions as nine characters 999<grp:3><act:3>; 27 rows use "
                  "the ten-character wide form, 20 sit in other namespaces"),
    ),
    "patch7878": Convention(
        target="patch7878",
        id_width=10,
        group_width=4,
        action_width=3,
        npc_table="ini/npc.ini",
        motion_fields=("StandByMotion", "RestMotion", "BlazeMotion"),
        write_layout="flat",
        evidence=("ini/npc.ini 2026-08-16: 3,284 of 4,087 sections spell all "
                  "three motions as ten characters 999<grp:4><act:3>; ZERO "
                  "nine-character 999* ids appear in any motion field"),
    ),
}

#: Declared, and deliberately not measured for THIS question. `can_serve`
#: returns `UNKNOWN` for each. Adding a name here is not a downgrade -- it is
#: the honest state until somebody runs the census.
UNMEASURED: dict = {
    "patch5017": "no npc-id width census has been run on 5017",
    "patch5065": "no npc-id width census has been run on 5065",
    "patch5165": "no npc-id width census has been run on 5165",
    "patch5517": "no npc-id width census has been run on 5517",
    "patch6090": ("no npc-id width census has been run on 6090; npcart.py "
                  "already records that it keys 3dmotion.dbc by the id's low "
                  "32 bits, so its convention is known to differ again"),
    "patch6609": "no npc-id width census has been run on 6609",
    "zephyr1057": "no npc-id width census has been run on Zephyr-1057",
}


# -- the client-level answer ------------------------------------------------

def can_serve(target: str) -> Answer:
    """Can we mint a 999-family NPC art id for this CLIENT's convention?

    This is a question about the client's spelling, not about any one row --
    `can_serve_row` answers that. `served` here means only that we know what a
    well-formed id looks like here and what is already taken.

    Three answers:

    * `served`      -- the convention is measured (`CONVENTIONS`).
    * `UNKNOWN`     -- the client is declared but unmeasured, or is not a name
                       we have ever seen. **Not looked at.**
    * `UNSUPPORTED` -- measured, and we cannot serve it. No client is in this
                       state today, and that is stated rather than implied:
                       the branch exists and nothing reaches it yet.
    """
    if not target or not isinstance(target, str):
        return unknown("no target named; an allocator with no client is the "
                       "bug this contract exists to prevent",
                       target=target)
    key = target.strip().lower()
    conv = CONVENTIONS.get(key)
    if conv is not None:
        return served(conv.evidence, target=key, convention=conv,
                      id_width=conv.id_width, group_width=conv.group_width,
                      layout=conv.write_layout)
    why = UNMEASURED.get(key)
    if why is not None:
        return unknown(why, target=key, declared=True)
    return unknown(f"{target!r} names no plugin this module has heard of; "
                   "nobody has measured it", target=key, declared=False)


# -- the row-level answer ---------------------------------------------------

_ZEROS = {"", "0"}


def _widths(values) -> set:
    return {len(v) for v in values}


def can_serve_row(target: str, row: Mapping) -> Answer:
    """Can we swap THIS row's art?

    `row` is one npc-table record, keyed as the client spells it -- a dict
    from `npc.json` on CCO, a parsed `[NpcTypeN]` section on 7878. Missing
    keys are treated as absent, not as zero; those are different states and
    conflating them is how the 16 field-less 7878 sections got counted with
    the 108 that declare no art.

    Answers:

    * `served`                     -- all three motion fields carry a
      999-family id at this client's width.
    * `served` with `no_art`       -- the row DECLARES no art (all three
      motions and `SimpleObjID` are `"0"`). Measured on exactly 108 of
      7878's rows and no others. This is data, not a gap.
    * `UNKNOWN`                    -- ragged width, another namespace, or the
      fields are absent. Nobody has looked at what these mean.
    * `UNSUPPORTED`                -- reached only when the client itself is
      unserved; a row cannot be served by a convention we do not have.
    """
    client = can_serve(target)
    if not client.served:
        return Answer(client.state,
                      f"row not reachable: client {target!r} is "
                      f"{client.state} -- {client.reason}",
                      dict(client.detail, row_deferred_to_client=True))
    conv: Convention = client.detail["convention"]

    present = [f for f in conv.motion_fields if row.get(f) is not None]
    if len(present) < len(conv.motion_fields):
        return unknown(
            f"row carries {len(present)} of {len(conv.motion_fields)} motion "
            "fields; whether an absent field is padding or a reader artefact "
            "has not been measured (16 such sections on 7878)",
            present=present)

    vals = [str(row[f]).strip() for f in conv.motion_fields]
    simple = str(row.get("SimpleObjID", row.get("simple_object", ""))).strip()

    if all(v in _ZEROS for v in vals):
        if simple in _ZEROS:
            return served(
                "row DECLARES no art: all three motion fields and the simple "
                "object are 0, uniformly, on exactly the 108 rows that do "
                "this and no others -- a measurement, not a gap",
                declares_no_art=True)
        return unknown(
            f"all three motions are 0 but SimpleObjID is {simple!r}; that "
            "combination has not been measured", simple=simple)

    live = [v for v in vals if v not in _ZEROS]
    if not all(v.isdigit() for v in live):
        return unknown("a motion field is not a decimal id; not measured",
                       values=tuple(live))

    # The namespace test reads the id with leading zeros stripped; the width
    # test reads it EXACTLY AS THE FILE WRITES IT. Those are two different
    # questions and running both off the same string got the reason wrong:
    # 7878 writes `09997840100`, which a raw prefix test calls "another
    # namespace" when it is plainly 999-family with a pad in front.
    stripped = [v.lstrip("0") or "0" for v in live]
    off = [v for v, s in zip(live, stripped) if not s.startswith(PREFIX)]
    if off:
        return unknown(
            f"motion ids sit outside the {PREFIX} family (e.g. {off[0]!r}); "
            "this module allocates in the 999 family only and has not "
            "measured what the other namespaces mean here",
            values=tuple(live))

    widths = _widths(live)
    if widths != {conv.id_width}:
        note = ""
        if _widths(stripped) == {conv.id_width}:
            note = (" -- they reach the target width once leading zeros come "
                    "off, and whether the client strips them is UNMEASURED, "
                    "so this stays UNKNOWN rather than being talked into "
                    "served")
        return unknown(
            f"ragged id width {sorted(widths)} where {target} writes "
            f"{conv.id_width}; nobody has looked at whether these are a "
            "second convention, a bleed, or malformed" + note,
            widths=sorted(widths), stripped_widths=sorted(_widths(stripped)),
            values=tuple(live))

    return served(
        f"all three motions are {conv.id_width}-character {PREFIX}-family ids",
        groups=tuple(sorted({v[3:3 + conv.group_width] for v in live})))


# -- occupancy --------------------------------------------------------------

_ID = re.compile(r"^(\d+)$")


def group_candidates(token, group_widths: Iterable[int] = (3, 4)) -> set:
    """Every group a 999-family id could plausibly be claiming.

    **Fail-closed on purpose.** The 3-wide and 4-wide readings of the group
    field both survive the evidence (module docstring), so an id contributes
    BOTH, normalised to three digits. A group is free only when it is free
    under every reading, which costs a handful of groups out of a thousand
    and cannot mint a collision by having picked the wrong reading.

    Leading zeros are stripped first: 7878's `3dmotion.ini` keys every id
    zero-padded to ten characters (`0999001100`), so the raw key does not
    start with `999` at all and a naive prefix test finds none of them.
    """
    s = str(token).strip()
    if not _ID.match(s):
        return set()
    s = s.lstrip("0") or "0"
    if not s.startswith(PREFIX):
        return set()
    body = s[len(PREFIX):]
    out = set()
    for w in group_widths:
        if len(body) > w:
            out.add(body[:w][:3].zfill(3))
        elif len(body) == w:
            out.add(body[:3].zfill(3))
    # the bare `999<grp:3>` form used as a section/marker key
    if len(body) == 3:
        out.add(body)
    return {g for g in out if len(g) == 3 and g.isdigit()}


def occupied_groups(sources: Mapping) -> dict:
    """Union the 999-family groups claimed by every source it is handed.

    `sources` maps a source name to an iterable of id-ish tokens. **There is
    deliberately no single-source entry point**: the free set is the
    complement of the UNION, and on CCO a `npc.json`-only computation picks
    `002`, which four other tables already hold. The rule is bought by that
    collision and the API is shaped so the rule cannot be skipped by
    accident.

    Returns ``{"union": set, "by_source": {name: set}}`` -- both, so a report
    can show its work rather than asserting a total.
    """
    by_source = {}
    union = set()
    for name, tokens in sources.items():
        s = set()
        for t in tokens:
            s |= group_candidates(t)
        by_source[name] = s
        union |= s
    return {"union": union, "by_source": by_source}


# -- allocation -------------------------------------------------------------

def allocate(target: str, occupied, *, floor: int = FLOOR) -> tuple:
    """The first free group for THIS client, spelled at THIS client's width.

    Returns ``(group, width, layout)``:

    * ``group``  -- the group already zero-padded to the target's write
      width. CCO ``"015"``, 7878 ``"0150"``. A caller cannot mint the wrong
      width by pasting a number in.
    * ``width``  -- that width, so a caller can assert on it.
    * ``layout`` -- the path layout this allocation will WRITE.

    `occupied` is the result of `occupied_groups`, or any set of three-digit
    group strings. **Passing one table's groups is not prevented here** --
    it cannot be, a set is a set -- which is why `occupied_groups` refuses to
    take a single source instead.

    Raises `NotServed` unless `can_serve(target).served`. It does not return a
    sentinel and it does not return a plausible id: an allocator that always
    returns something well-formed-looking is the same object as a cohort stub
    that returns zero, and the swap page's default path would consume either
    without noticing.
    """
    answer = can_serve(target)
    if not answer.served:
        raise NotServed(
            f"allocate({target!r}) refused: {answer.state} -- {answer.reason}")
    conv: Convention = answer.detail["convention"]

    taken = occupied["union"] if isinstance(occupied, Mapping) else set(occupied)
    taken = {str(g).zfill(3) for g in taken}

    for i in range(max(0, int(floor)), 1000):
        g = f"{i:03d}"
        if g not in taken:
            return conv.spell_group(g), conv.group_width, conv.write_layout
    raise NotServed(
        f"allocate({target!r}) refused: every group from {floor:03d} to 999 "
        "is occupied under at least one reading")


# -- convenience: build the sources for a real install ----------------------

_SECTION = re.compile(r"^\[([^\]]+)\]([^\[]*)", re.M)
_KV = re.compile(r"^(\w+)\s*=\s*(.*?)\s*$", re.M)


def parse_ini_sections(text: str) -> list:
    """`[Section]` blocks as ``(name, {key: value})``, in file order.

    Local because this module must run against a string in a test with no
    install present; `npcart.Tables` owns the same grammar for the resolution
    chain and neither should grow a dependency on the other for two regexes.
    """
    return [(n, dict(_KV.findall(b))) for n, b in _SECTION.findall(text)]


def ini_keys(text: str) -> list:
    """Left-hand sides of a flat ``key=value`` table, unstripped of zeros."""
    out = []
    for line in text.splitlines():
        if "=" in line:
            out.append(line.split("=", 1)[0].strip())
    return out
