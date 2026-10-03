#!/usr/bin/env python3
r"""garment_overlay_build.py -- materialise 7878's missing body art into an
OVERLAY DIRECTORY, from one measured donor install, at 7878's own path shape.

    py -3 tools/garment_overlay_build.py --survey            # pick the donor
    py -3 tools/garment_overlay_build.py --dry-run           # screen only
    py -3 tools/garment_overlay_build.py --report out/garment/overlay.json
    py -3 tools/garment_overlay_build.py --table all --clean # all three tables

THREE TABLES, THREE SHAPES.  `armor.ini` landed first and is unchanged here.
`armet.ini` and `weapon.ini` were added 2026-08-29 and they are NOT the same
job with a different filename -- each differs from armor in a way that would
have produced a wrong build if assumed:

    table        rows   shape      writes                 collapse
    armor.ini     955   slotted    c3/body/<slot><g6>     4 body types -> 2 slots
    armet.ini    1168   slotted    c3/armet/<slot><g6>    4 body types -> 2 slots
    weapon.ini   4828   FLAT       c3/weapon/<stem>       4828 rows -> 308 meshes

`weapon.ini` has NO body-type dimension at all: its ids are six digits, the id
IS the mesh name, and grouping by last-six gives 4828 singleton groups.  What
it has instead is a colour/plus-level collapse -- 4354 unresolved rows declare
only 308 distinct `Mesh0` -- so it is keyed on the DECLARED mesh and writes 308
files, not 4354.  See `_plan_flat` for why that is safe here and where it is
not.

------------------------------------------------------------------------------
WHAT THIS IS
------------------------------------------------------------------------------

WHAT THIS IS.  `docs/garment_missing_art_2026-08-28.md` establishes that 7878
declares 955 `armor.ini` rows, 579 of which name a mesh 7878 never shipped, and
that the geometry for every one of them is on this box in a pre-7878 install.
The merged `resolve_garment` fallback READS that art where it lies.  This tool
COPIES it -- mesh and texture -- to the paths 7878 itself would use, into a new
directory that `AssetRoot(root, overlay=...)` honours and that the real client
honours as loose files.  **No archive is repacked and no install is written.**

------------------------------------------------------------------------------
WHAT IS WRITTEN, AND WHY THAT SHAPE
------------------------------------------------------------------------------

7878 names a body garment `c3/body/<bodytype><garment6>.c3`, where `<garment6>`
is the last six digits of the `armor.ini` SECTION id -- not of `Mesh0`.  Read
off 7878's own 376 resolving rows rather than assumed:

    resolved-row (dir, prefix)   ('head','7') 219   ('body','7') 157
    body types present per garment   ('7','8') 343   ('7',) 12

So the overlay writes `c3/body/7<garment6>.c3` and `c3/body/8<garment6>.c3`.
That shape is what makes the TEXTURE work: 7878 ships `c3/body/7<garment6>.dds`
for 38 of the units below -- the "re-skin" case in the doc, a new texture over
geometry TQ never re-shipped -- and `resolve_appearance` looks for the texture
beside the mesh under the mesh's own stem.  Writing the mesh under the DECLARED
`Mesh0` instead would put it beside a name 7878 has no texture for, and would
discard 7878's own newer skin.  A shipped 7878 `.dds` is NEVER overwritten.

THE DONOR HAS FOUR BODY TYPES AND 7878 HAS TWO.  This is the trap in the job
and it does not appear anywhere in the doc.  The old corpus stores
`c3/mesh/00<bodytype><garment6>.c3` for bodytype 1..4, and they are genuinely
different files -- 48 of 48 sampled garments differ across all four.  7878 has
only `7` and `8`.  The 4->2 mapping is MEASURED, by byte-identity between
7878's shipped `c3/body/<slot><g>.dds` and the donor's `c3/texture/00<bt><t6>.dds`
named by each body type's own `Texture0`:

    slot 7 <- bodytype 1 : 17 garments      slot 7 <- bodytype 2 : 0
    slot 8 <- bodytype 3 :  4 garments      slot 8 <- bodytype 4 : 0

counting only garments where the two candidate textures DIFFER, so the vote is
between distinguishable alternatives.  Zero contradictions.  Over the whole
population (including indistinguishable pairs) it is 15/1 for slot 7 and 27/6
for slot 8, and there is not one cross vote -- no 1->8, no 3->7.  SIZE identity
was run as the negative control and votes for everything (every one of these is
a 256x256 DXT3 at the same byte count), which is what tells you the byte test
is the arm carrying the signal.  `--survey` re-runs this.

**THE SAME MAP FOR `armet.ini`, ON A DIFFERENT ARM, BECAUSE THE ARMOUR ARM
CANNOT FIRE THERE.**  Re-running the texture-byte vote on `armet.ini` returns
NOTHING, and the reason is measurable rather than mysterious: of the 9 armet
garments where 7878 ships a slot `.dds` and the donor has all four body types'
textures, all 9 have the four donor textures BYTE-IDENTICAL to each other --
the donor's hair texture does not vary with body type -- so there is nothing
for a byte vote to discriminate, and 7878's own armet skins match none of them
(re-authored, 18 of 18).  Mesh bytes and PHY count-vectors match nothing either,
on `armor.ini` as well as `armet.ini`, so those are not instruments, they are
a re-authoring boundary.  Reporting `armet <- 1,3` on the strength of a vote
that returned an empty dict would be an assumption wearing a measurement's
clothes.

What DOES fire on `armet.ini` is the PARTITION, and it is the half of the claim
that carries the risk (a female mesh on a male character).  Over the 222 armet
garments where the donor holds all four body types, the donor's own meshes fall
into two groups and the grouping is the gender split:

    donor armet meshes, equal PHY signature   1==2  53    3==4  91
                                              1==3   0    1==4   0
                                              2==3   1    2==4   0

144 within-pair agreements against 1 cross-pair.  `armor.ini` measured through
the same instrument shows `3==4 15` and no `1==3`/`1==4`/`2==3`/`2==4` at all,
so the instrument is not simply calling everything equal.  Body types {1,2} and
{3,4} are therefore the same two families in `armet.ini` that they are in
`armor.ini`, and armour's byte vote already named which family each 7878 slot
draws from -- 7 from {1,2}, taking 1; 8 from {3,4}, taking 3.  `--survey` prints
both arms, and prints the armet byte vote AS AN EMPTY RESULT rather than
omitting it, because a vote that could not fire and a vote that was never run
look identical in a report that only lists what it found.

------------------------------------------------------------------------------
THE SCREEN IS PER SLOT, AND THE ARMOUR SCREEN IS WRONG FOR THE OTHER TWO
------------------------------------------------------------------------------

An `armor.ini` mesh is a BODY: it carries the rig every other worn item hangs
off, and the doc's hazard is a body that carries `v_body` and nothing else.  An
`armet.ini` or `weapon.ini` mesh is an ATTACHMENT -- it hangs off that rig and
carries no rig of its own.  Measured over 7205's donors for 7878's unresolved
rows:

    armet   236 distinct meshes   236 carry exactly ONE node   0 carry a rig node
    weapon  308 distinct meshes   307 carry one, 1 carries two 0 carry a rig node

and the node names are whatever the artist left in 3ds Max -- `Cylinder02`,
`Box06`, `Line01`, `cap`, `v_armet01`, `v_body`.  Holding those to
`REQUIRED_NODES` would refuse 544 of 544.  So `screen_mesh` takes the slot's
rig: `body` requires the three attachment points, `attachment` requires that
the file parse and carry AT LEAST ONE named renderable node, and REFUSES one
that carries a body rig node (that mesh is a body, and shipping it as a hat is
the mirror of the hazard the armour screen exists for).

A GEOMETRY EMPTINESS TEST WAS BUILT AND THEN THROWN AWAY, and the reason is
worth keeping.  `C3File.phy_header`'s `count_raw` reads like a vertex count and
25 armet + 22 weapon donor meshes come back zero on it -- an attractive 47-mesh
refusal.  It is not one: those files' PHY bodies run 2602..49588 bytes, the same
range as the nonzero ones, and 7878 SHIPS a `weapon.ini` mesh of its own with
`count_raw == 0`.  The field is not a vertex count on those chunks, `coassets`
says so in `phy_header`'s own docstring, and refusing 47 good meshes on it would
have been a screen that fires confidently at nothing.

------------------------------------------------------------------------------
THE SCREEN.  Every donor mesh is censused BEFORE it is written.
------------------------------------------------------------------------------

`docs/garment_missing_art_2026-08-28.md` records COMod Explorer's hazard: some
old donor meshes carry `v_body` and NOTHING ELSE, and a character wearing one
loses the hat and both weapons.  It is per-mesh, so it is screenable.

    SHIP    the mesh parses and carries the nodes ITS SLOT needs
    REFUSE  no source row for the slot        (reason "no source row")
    REFUSE  the donor has no such mesh        (reason "donor mesh absent")
    REFUSE  the file will not parse           (reason "unreadable", file named)
    REFUSE  v_body-only / missing a node      (reason "rig", nodes listed)
    REFUSE  an attachment with no node at all (reason "rig", "bare")
    REFUSE  an attachment carrying a body rig (reason "rig", node named)
    REFUSE  no texture from 7878 or the donor (reason "no texture")
    REFUSE  the key is a resolving row's key  (reason "would redirect ...")

An undeclared or unreadable donor FAILS.  It is never skipped silently, and the
refusal list is written to the report with the file name in it.  **The refusal
count is printed even when it is zero, and zero is stated as a result rather
than left as an absence** -- a screen that never rejects anything is one nobody
can tell is running, which is why `--selftest` drives every arm of it against
synthetic inputs on every run.

The cost this accepts, from the doc and re-confirmed here: the donors that HAVE
this art are 4-node everywhere.  The hat and both weapons survive; the back
item, the pelvis piece, boots and the slot accessories have nowhere to hang.

------------------------------------------------------------------------------
WHERE IT WRITES, AND WHAT IT REFUSES TO WRITE OVER
------------------------------------------------------------------------------

Default `out/garment/overlay-<install>/`, which is gitignored.  `_refuse_target`
REFUSES any destination inside the clients tree, inside an install, or under a
Program Files directory: an install that is written to is re-keyed and stops
being a control, and that has already happened once on this box (5065).  The
guard is tested in BOTH directions -- `tests/test_garment_overlay.py` checks it
rejects an install path and ACCEPTS an ordinary scratch directory, because a
guard tested only on the case it must catch is untested on the case it must
allow.

No install path is written down anywhere in this file: `core/coroot.py`
resolves the clients tree and `tests/test_sanitization.py` refuses a literal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "core"))

import coroot                                              # noqa: E402
from coassets import AssetRoot, parse_ini, C3File          # noqa: E402

#: Attachment nodes a BODY must carry to be worth shipping.  These are the
#: equipment EVERY player is wearing; the doc's 28% hazard is exactly the
#: bodies that have none of them.  An armet or a weapon carries NONE of these
#: -- it hangs off them -- so this is the `body` rig only, and a mesh bound for
#: an attachment slot that DOES carry one is refused for the opposite reason.
REQUIRED_NODES = ("v_armet", "v_l_weapon", "v_r_weapon")
BODY_RIG = REQUIRED_NODES

#: 7878 body type <- old-corpus body type.  MEASURED, see the module docstring,
#: `survey_bodytypes` (the armour byte vote) and `survey_bodytype_partition`
#: (the armet arm).  Order matters only for reporting.
SLOT_SOURCE = (("7", "1"), ("8", "3"))

#: The old-corpus body types each 7878 slot stands for.  `SLOT_SOURCE` names
#: which ONE of them the art is cut from; this names the whole family, and it
#: is the half `survey_bodytype_partition` measures directly.
FAMILY = {"7": ("1", "2"), "8": ("3", "4")}

#: The directory 7878 keeps body garments in.  `armor.ini` is RolePart.ini's
#: `Part2=body`, and 157 of 7878's own resolving armor rows land here.
WRITE_DIR = "body"

#: PER-TABLE SHAPE.  Nothing below is a default: each field was measured on
#: 7878 and the value differs between tables in a way that would have built the
#: wrong thing if carried over from `armor.ini`.
#:
#:   shape       "slotted"  one file per (garment, 7878 slot), body types folded
#:                          4 -> 2 by SLOT_SOURCE
#:               "flat"     no body-type dimension; one file per DECLARED mesh
#:   write_dir   where 7878 itself keeps that table's art.  Read off 7878's own
#:               resolving rows: armor -> body (157), armet -> armet (27 stems
#:               `7<g6>` + 26 `8<g6>`, every one with a `.dds` beside it),
#:               weapon -> weapon (456 six-digit stems).
#:   rig         which screen the donor meshes are held to.  See `screen_mesh`.
TABLE_PROFILES = {
    "armor.ini":  {"shape": "slotted", "write_dir": "body",
                   "slots": SLOT_SOURCE, "rig": "body"},
    "armet.ini":  {"shape": "slotted", "write_dir": "armet",
                   "slots": SLOT_SOURCE, "rig": "attachment"},
    "weapon.ini": {"shape": "flat", "write_dir": "weapon",
                   "slots": (), "rig": "attachment"},
}

#: Every table whose rows share the resolver's key space.  `_protected_keys`
#: is computed over ALL of them, not just the table being built: `resolve_asset`
#: searches `mesh, weapon, body, hair, ... armet, head, ...` IN THAT ORDER for
#: every row of every table, so a stem written into `c3/weapon/` is reachable by
#: an `armor.ini` row and is reached BEFORE `c3/body/`.  Measured on 7878: the
#: global set costs armor nothing (the same 6 garments are refused either way)
#: and the three tables' candidate key sets do not overlap each other at all
#: (armor-weapon 0, armet-armor 0, armet-weapon 0) -- so the rule is free here
#: and it is the one that stays true if the corpus moves.
KEY_SPACE_TABLES = ("armor.ini", "armet.ini", "weapon.ini")

#: Donors worth surveying, newest first.  A NAME under the clients tree, never
#: a path.  `--survey` measures all of them; nothing here is a conclusion.
#: 7867/7682/7632 were added when armet and weapon were done: they are NEWER
#: than 7205 and were missing from the armour survey, so "the newest install
#: that has the art" had never actually been asked of them.  7682 left on
#: 2026-09-19 (a copy of 7632, deleted by the owner) and 7632 was renamed 7622,
#: its build stamp.
SURVEY_DONORS = ("7867", "7622",
                 "7205", "7189", "7182", "7170", "7135", "7110", "7083",
                 "7065", "7009", "6968", "6090", "4274",
                 "CCO-snapshot-2026-08-24", "Zephyr")


def profile(table: str) -> dict:
    """The shape record for `table`, or REFUSE by name.

    A table this tool has not measured is not built with armour's shape and a
    shrug: `weapon.ini` under armour's shape would have keyed on a body type
    that does not exist and planned nothing, which reads exactly like a table
    with no missing art.
    """
    p = TABLE_PROFILES.get(table)
    if p is None:
        raise SystemExit(
            "REFUSED: no measured shape for %r. This tool knows %s. Each one's "
            "write directory, body-type collapse and rig screen were measured "
            "on 7878 separately and they differ; guessing one from another "
            "builds the wrong files or silently builds none."
            % (table, ", ".join(sorted(TABLE_PROFILES))))
    return p

#: Chosen by `--survey`, recorded here so a plain run is reproducible.  7205
#: is the NEWEST install that has the art: coverage ties with everything from
#: 6968 down to 6090 at 211/211, and 7205's bytes differ from 6090's on 9 of
#: the 211, so the tie is broken toward the revision nearest 7878.
#:
#: RE-SURVEYED PER TABLE 2026-08-29, because "the winner may differ" is a
#: question, not a formality.  It does not differ, and the tie-break is worth
#: less than it is on armour -- which is the honest way to put it:
#:
#:   table       donors tied at the top          7205 vs 6090 on the meshes needed
#:   armor.ini   11 installs, 284 Mesh0          9 of 211 differ
#:   armet.ini   11 installs, 780 of 1060 rows   0 of 236 differ -- IDENTICAL
#:   weapon.ini  11 installs, 308/308 = 100%     1 of 308 differ
#:
#: So for `armet.ini` the eleven tied donors are interchangeable byte for byte
#: and the choice of 7205 is measurably immaterial; for `weapon.ini` it decides
#: exactly one mesh.  Saying "7205, chosen by measurement" without that would
#: dress a coin-flip up as a finding.
#:
#: The three installs NEWER than 7205 were added to the survey for this and
#: are not donors: 7867/7682/7632 cover 0 of 516 armet Mesh0 and 2 of 308
#: weapon ones.  They are 7878's own family and share its gap, which is a
#: result -- "newest that has the art" had never actually been asked of them.
DEFAULT_DONOR = "7205"


# ---------------------------------------------------------------------------
# the write target
# ---------------------------------------------------------------------------

def _forbidden_roots(clients: Path) -> list:
    """Directories nothing here may ever write inside.

    The clients tree is a set of READ-ONLY CONTROLS.  An install that is
    written to is re-keyed and stops being a control -- that is not a
    hypothetical, it happened to 5065.  Program Files is added because the
    live client install lives there on this box and the same argument applies
    with a launcher attached to it.
    """
    out = [Path(clients).resolve()]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            out.append(Path(v).resolve())
    try:
        found = coroot.resolve()
        if found and found.path:
            out.append(Path(found.path).resolve())
    except Exception:
        # No configured install is not an error here: the clients tree and
        # Program Files are already covered, and refusing to run because the
        # DEFAULT root is unset would make the guard the thing that breaks.
        pass
    return out


def _refuse_target(target: Path, clients: Path) -> None:
    """Raise unless `target` is somewhere it is safe to materialise art.

    Refuses by NAME and says which protected root it is under.  A silent
    `return` here would be a tool that quietly declines to do its job, which
    is indistinguishable from one that did it.
    """
    t = Path(target).resolve()
    for bad in _forbidden_roots(clients):
        if t == bad or bad in t.parents:
            raise SystemExit(
                "REFUSED: the overlay would be written at\n"
                "    %s\n"
                "which is inside\n"
                "    %s\n"
                "Nothing here writes into an install or the clients tree -- an "
                "install that is written to is re-keyed and stops being a "
                "read-only control. Pass --out somewhere else." % (t, bad))


# ---------------------------------------------------------------------------
# the screen
# ---------------------------------------------------------------------------

def mesh_nodes(data: bytes) -> set:
    """Attachment-node names in a `.c3`, or raise.

    `C3File` is strict on purpose here: a file that does not walk cleanly is
    a file this tool must not copy, and `strict=False` would turn a truncated
    donor into a partial node set that passes the rig check.
    """
    c3 = C3File(data)
    ns = {c3.node_name(c) for c in c3.chunks}
    ns.discard(None)
    return ns


def screen_mesh(logical: str, data: bytes, rig: str = "body") -> tuple:
    """`(ok, reason, nodes)` for one donor mesh, held to its SLOT's rig.

    `rig="body"`        an `armor.ini` mesh.  It carries the rig, so it must
                        carry all of `REQUIRED_NODES`; the doc's hazard is the
                        body that carries `v_body` and nothing else.
    `rig="attachment"`  an `armet.ini` or `weapon.ini` mesh.  It HANGS OFF that
                        rig and carries none of it, so requiring the rig nodes
                        would refuse 544 of 544 on this box.  What is still
                        checkable is that the file parses, that it has at least
                        one named renderable node (a C3 of pure MOTI animation
                        with no PHY draws nothing), and that it is NOT a body
                        that wandered into an attachment slot.

    Nothing stronger is claimed for an attachment, and the temptation is named
    in the module docstring: `phy_header`'s `count_raw` is not a vertex count
    on these chunks and 7878 ships a weapon of its own where it reads zero.
    """
    try:
        ns = mesh_nodes(data)
    except Exception as exc:
        return False, "unreadable: %s: %s [%s]" % (
            type(exc).__name__, exc, logical), set()
    if rig == "body":
        missing = [n for n in REQUIRED_NODES if n not in ns]
        if missing:
            return False, "rig: missing %s (has %s) [%s]" % (
                ",".join(missing), ",".join(sorted(ns)) or "nothing",
                logical), ns
        return True, "", ns
    if rig == "attachment":
        if not ns:
            return False, ("rig: bare -- no named renderable node, this file "
                           "draws nothing [%s]" % logical), ns
        worn = [n for n in BODY_RIG if n in ns]
        if worn:
            return False, ("rig: carries the BODY rig (%s) in an attachment "
                           "slot -- this is a body, not a hat or a weapon [%s]"
                           % (",".join(worn), logical)), ns
        return True, "", ns
    # An unknown rig name is a programming error, and defaulting it to "ship"
    # would make the screen silently stop screening.
    return False, "rig: unknown rig %r [%s]" % (rig, logical), ns


def selftest() -> int:
    """Drive every arm of the screen against synthetic input, BOTH rigs.

    THIS RUNS ON EVERY BUILD, not only under `--selftest`.  Measured on this
    box: `armor.ini` donors pass the body rig 290 of 290, and `armet.ini` /
    `weapon.ini` donors pass the attachment rig 236 of 236 and 308 of 308 --
    ZERO bare, ZERO carrying a body rig node.  So on real data no reject path
    executes for any table, and an unexercised reject path is a screen nobody
    can tell is running.  Each arm below is constructed to FAIL a different
    way, and the build refuses to start if any of them comes back the wrong
    answer.
    """
    magic = b"MAXFILE C3 00001"

    def phy(name: str) -> bytes:
        n = name.encode("ascii")
        body = len(n).to_bytes(4, "little") + n + b"\0" * 8
        return b"PHY " + len(body).to_bytes(4, "little") + body

    def moti() -> bytes:
        # A chunk `node_name` deliberately declines to name -- see coassets.
        body = (3).to_bytes(4, "little") + (7).to_bytes(4, "little") + b"\0" * 8
        return b"MOTI" + len(body).to_bytes(4, "little") + body

    full = magic + phy("v_body") + phy("v_armet") + phy("v_l_weapon") + phy("v_r_weapon")
    bodyonly = magic + phy("v_body")
    truncated = magic + b"PHY " + (9999).to_bytes(4, "little") + b"\0\0\0\0"
    notc3 = b"this is not a c3 file at all...."
    attach = magic + phy("Cylinder02") + moti()
    bare = magic + moti()

    cases = [
        ("body: must SHIP", "body", full, True, ""),
        ("body: REFUSE v_body-only", "body", bodyonly, False, "rig"),
        ("body: REFUSE truncated", "body", truncated, False, "unreadable"),
        ("body: REFUSE non-c3", "body", notc3, False, "unreadable"),
        # The attachment arms. `attach` is the real shape of an armet/weapon
        # donor on this box -- one PHY with an artist's node name, plus MOTI.
        ("attach: must SHIP", "attachment", attach, True, ""),
        ("attach: SHIP an artist name", "attachment",
         magic + phy("Box06"), True, ""),
        ("attach: REFUSE bare (MOTI only)", "attachment", bare, False, "rig"),
        ("attach: REFUSE a body rig", "attachment", full, False, "rig"),
        ("attach: REFUSE truncated", "attachment", truncated, False,
         "unreadable"),
        ("attach: REFUSE non-c3", "attachment", notc3, False, "unreadable"),
        # And the case that would turn the screen off by typo.
        ("REFUSE an unknown rig", "hat", attach, False, "rig"),
    ]
    bad = 0
    for label, rig, data, want_ok, want_reason in cases:
        ok, reason, _ns = screen_mesh("SELFTEST/" + label, data, rig)
        good = (ok == want_ok) and (want_ok or reason.startswith(want_reason))
        if not good:
            bad += 1
        print("    selftest %-32s %s   %s"
              % (label, "ok" if good else "WRONG ANSWER", reason[:60]))
    if bad:
        print("    SELFTEST FAILED -- the screen does not reject what it must. "
              "Refusing to build.")
    return bad


# ---------------------------------------------------------------------------
# measurement: which donor
# ---------------------------------------------------------------------------

def unresolved_rows(ar: AssetRoot, table: str) -> list:
    """`[(section id, Mesh0, Texture0)]` for rows this install cannot resolve.

    `resolve_asset` with no `fallback_roots` configured is the install's own
    answer and nothing else -- `_old_corpus_garment` returns None on an empty
    fallback list.
    """
    secs = _table_sections(ar.root, table)
    return [(i, kv.get("Mesh0"), kv.get("Texture0"))
            for i, kv in secs.items() if not ar.resolve_asset(i, "mesh")]


def survey_donors(clients: Path, install: str, table: str) -> dict:
    """Coverage + rig + texture for every candidate donor.

    The negative control is 200 ids of the same SHAPE that no table declares.
    A donor that answers those is a candidate generator, not a corpus.
    """
    import random
    rig = profile(table)["rig"]
    # BARE, and every `AssetRoot` in this file is. This tool's whole input is
    # "which rows does the install NOT resolve", so a root that answers them
    # out of a fallback install or an existing overlay plans ZERO units and
    # prints a clean run. Measured the moment `ASSET_PROFILES` landed:
    # `units planned 0, REFUSED 0` against an install missing 556 meshes.
    # The donor is bare for the mirror-image reason -- art the donor itself
    # borrowed is not art the donor can donate.
    ar = AssetRoot.bare(str(clients / install))
    rows = unresolved_rows(ar, table)
    need = sorted({m for _i, m, _t in rows if m})
    # ROWS ARE COUNTED PER DISTINCT MESH, NOT PER ROW. `weapon.ini` has 4354
    # unresolved rows over 308 distinct `Mesh0`, and probing all 4354 against
    # 17 donors is 14x the work for the same answer -- an unenumerable (WDF)
    # donor costs a real filesystem stat per candidate directory.
    per_mesh: dict = {}
    for _i, m, t in rows:
        if m:
            per_mesh.setdefault(m, [0, t])[0] += 1
    random.seed(7878)
    control = ["1%06d" % random.randrange(900000, 999999) for _ in range(200)]
    print("  %s %s: %d rows unresolved, %d distinct Mesh0 needed, rig %s"
          % (install, table, len(rows), len(need), rig))
    print("  %-24s %11s %8s %8s %10s %9s" %
          ("donor", "covers", "SHIP", "REFUSE", "texture", "control"))
    out = {}
    for name in SURVEY_DONORS:
        p = clients / name
        if not p.is_dir():
            print("  %-24s ABSENT" % name)
            continue
        try:
            da = AssetRoot.bare(str(p))
        except Exception as exc:
            # An install that will not open is REPORTED, not skipped: a donor
            # missing from the table because it errored looks exactly like a
            # donor that covered nothing.
            print("  %-24s WILL NOT OPEN: %s" % (name, str(exc).splitlines()[0]))
            out[name] = {"error": str(exc).splitlines()[0]}
            continue
        cover = ship = refuse = tex = 0
        for m, (n, t) in per_mesh.items():
            loc = da.resolve_asset(m, "mesh")
            if loc is None:
                continue
            cover += n
            if screen_mesh(loc.logical, da.read(loc.logical), rig)[0]:
                ship += n
            else:
                refuse += n
            stem = loc.logical.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            sub = loc.logical.split("/")[1]
            if da.locate("c3/%s/%s.dds" % (sub, stem)) or (
                    t and da.resolve_asset(t, "texture")):
                tex += n
        bg = sum(1 for c in control if da.resolve_asset(c, "mesh"))
        covd = sum(1 for m in need if da.resolve_asset(m, "mesh"))
        print("  %-24s %4d/%-4d %5.1f%% %8d %8d %6d/%-4d %6d/200"
              % (name, covd, len(need), 100.0 * covd / max(1, len(need)),
                 ship, refuse, tex, len(rows), bg))
        out[name] = {"covers": covd, "need": len(need), "ship": ship,
                     "refuse": refuse, "texture": tex, "control": bg}
        da.close()
    ar.close()
    print("  control column is 200 ids of the same SHAPE that no table "
          "declares: a nonzero there means the probe is a candidate generator, "
          "not a corpus.")
    return out


def survey_bodytypes(clients: Path, install: str, donor: str,
                     table: str = "armor.ini") -> dict:
    """Re-measure the 4->2 body-type mapping.  See the module docstring.

    Counts only garments where the two candidate donor textures DIFFER, so the
    vote is between distinguishable alternatives, and prints the SIZE vote
    beside it as the negative control -- size matches everything here.

    ON `armet.ini` THIS ARM CANNOT FIRE AND SAYS SO.  The donor's hair textures
    do not vary with body type (9 of 9 discriminable garments have all four
    candidates byte-identical), so every garment lands in `indistinguishable`
    and the vote is an empty dict.  That empty dict is PRINTED.  A report that
    only lists what it found cannot be told apart from one that found nothing,
    and the partition arm below is what carries armet's half of the claim.
    """
    wd = profile(table)["write_dir"]
    ar = AssetRoot.bare(str(clients / install))
    da = AssetRoot.bare(str(clients / donor))
    secs = _table_sections(ar.root, table)
    byg: dict = defaultdict(dict)
    for i, kv in secs.items():
        byg[i[-6:]][i[0]] = kv
    votes: Counter = Counter()
    sizevotes: Counter = Counter()
    dup: Counter = Counter()
    for g, rows in byg.items():
        for slot, bts in (("7", ("1", "2")), ("8", ("3", "4"))):
            t = ar.locate("c3/%s/%s%s.dds" % (wd, slot, g))
            if not t:
                continue
            want = hashlib.sha1(ar.read(t.logical)).hexdigest()
            hs, sz = {}, {}
            for bt in bts:
                kv = rows.get(bt)
                dl = da.resolve_asset(kv.get("Texture0"), "texture") if kv else None
                if dl:
                    hs[bt] = hashlib.sha1(da.read(dl.logical)).hexdigest()
                    sz[bt] = dl.size
            if len(hs) < 2:
                continue
            if hs[bts[0]] == hs[bts[1]]:
                dup[slot] += 1
                continue
            for bt in bts:
                if hs[bt] == want:
                    votes[(slot, bt)] += 1
                if sz[bt] == t.size:
                    sizevotes[(slot, bt)] += 1
    print("  BYTE vote (discriminating garments only):",
          {"%s<-%s" % k: v for k, v in sorted(votes.items())})
    print("  SIZE vote, the NEGATIVE CONTROL   :",
          {"%s<-%s" % k: v for k, v in sorted(sizevotes.items())})
    print("  indistinguishable pairs, not counted:", dict(dup))
    print("  mapping in use:", dict(SLOT_SOURCE))
    ar.close()
    da.close()
    return {"byte": {"%s<-%s" % k: v for k, v in votes.items()},
            "size": {"%s<-%s" % k: v for k, v in sizevotes.items()},
            "indistinguishable": dict(dup)}


def _phy_signature(data: bytes) -> tuple:
    """`((node, count, body_len), ...)` for one mesh, sorted.

    A cheap fingerprint of what a mesh IS, used only to ask whether two donor
    meshes are the same asset.  `count_raw` is not trusted as a vertex count
    anywhere else in this file -- here it is only ever compared to another
    file's, never thresholded, so what it means does not matter.
    """
    c3 = C3File(data)
    out = []
    for ch in c3.chunks:
        h = c3.phy_header(ch)
        if h:
            out.append((h["name"], h["count_raw"], h["body_len"]))
    return tuple(sorted(out))


def survey_bodytype_partition(clients: Path, install: str, donor: str,
                              table: str) -> dict:
    """Which donor body types are the SAME FAMILY, measured on the donor alone.

    THE ARM THAT CARRIES `armet.ini`.  `survey_bodytypes` votes by matching
    7878's shipped skin against a donor texture, and on armet there is nothing
    to match: the donor's four candidate textures are byte-identical to each
    other and 7878's own are re-authored.  This asks a question that does not
    need 7878 at all -- do the donor's own four body-type meshes fall into two
    families, and are they {1,2} and {3,4}?

    That is the half of the mapping that carries the risk.  `armor.ini`'s byte
    vote already named WHICH member of each family a 7878 slot draws from (7
    from 1, 8 from 3, zero cross votes).  What would put a female mesh on a
    male character is the FAMILIES being different in this table, and this
    measures exactly that.

    Its own negative control is built in: if the instrument simply called
    everything equal, the cross pairs (1-3, 1-4, 2-3, 2-4) would score too.
    They are printed beside the within-pairs for that reason.
    """
    # BARE for the same reason every other root here is: this vote asks which
    # FAMILY the donor's own art belongs to, and a root that answers out of a
    # fallback install votes on art the donor cannot donate.
    ar = AssetRoot.bare(str(clients / install))
    da = AssetRoot.bare(str(clients / donor))
    secs = _table_sections(ar.root, table)
    byg: dict = defaultdict(dict)
    for i, kv in secs.items():
        byg[i[-6:]][i[0]] = kv
    pairs: Counter = Counter()
    n = 0
    for _g, rows in byg.items():
        sig: dict = {}
        for bt in "1234":
            kv = rows.get(bt)
            loc = da.resolve_asset(kv.get("Mesh0"), "mesh") if kv else None
            if loc:
                try:
                    sig[bt] = _phy_signature(da.read(loc.logical))
                except Exception:
                    pass
        if len(sig) < 4:
            continue
        n += 1
        for a in "1234":
            for b in "1234":
                if a < b and sig[a] == sig[b]:
                    pairs[a + b] += 1
    within = sum(pairs[k] for k in ("12", "34"))
    cross = sum(pairs[k] for k in ("13", "14", "23", "24"))
    print("  garments with all four donor body types : %d" % n)
    print("  donor meshes EQUAL, within family {1,2}/{3,4}: %s"
          % {k: pairs[k] for k in ("12", "34")})
    print("  donor meshes EQUAL, ACROSS families (control): %s"
          % {k: pairs[k] for k in ("13", "14", "23", "24")})
    print("  within %d vs across %d -- %s"
          % (within, cross,
             "the {1,2}|{3,4} split holds" if within > 4 * max(1, cross)
             else "NOT a clean split; the mapping is NOT established here"))
    ar.close()
    da.close()
    return {"garments": n, "pairs": dict(pairs),
            "within": within, "across": cross}


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------

def lookup_keys(ar: AssetRoot, ident: str, kv: dict) -> list:
    """The garment keys `resolve_garment` will try for `ident`, IN ORDER.

    Mirrors the resolver exactly: the DECLARED `Mesh0` (via
    `AssetRoot._declared_mesh`, which is the function the resolver itself
    calls) first, then the section id.  `kv["Mesh0"]` is folded in as well and
    only ever WIDENS the set -- for `protected` a superset is the safe error,
    and where the two disagree the row is treated as reaching both.
    """
    keys = []
    for dm in (ar._declared_mesh(ident), (kv or {}).get("Mesh0")):
        if dm and len(dm) >= 6 and dm[-6:] != ident[-6:] and dm[-6:] not in keys:
            keys.append(dm[-6:])
    keys.append(ident[-6:])
    return keys


def _protected_keys(ar: AssetRoot, tables=KEY_SPACE_TABLES) -> dict:
    """`{garment key: [rows that already resolve and would look under it]}`.

    THE KEYS A ROW THAT ALREADY WORKS WOULD LOOK UNDER.  Measured the hard
    way: the first build of this overlay came back GAINED 579, LOST 0,
    CHANGED 24, and the 24 are the defect this exists for.

    `resolve_garment` keys on the last six digits and tries the DECLARED
    `Mesh0` before the section id.  `armor.ini [1181300]` resolves inside 7878
    today, to its own `c3/body/7181300.c3`, because its declared key `137020`
    currently misses.  Garment `137020` is ALSO one this overlay fills, for its
    own rows `[x137020]` -- and the moment `c3/body/7137020.c3` exists,
    `[1181300]` finds it FIRST and silently changes what it wears.

    The collision is in the resolver's KEY SPACE, not in the path shape, so
    there is no filename that only the intended rows reach: `[1137020]` and
    `[1181300]` look under the same key by construction.  The only honest move
    is to REFUSE the garment and say which rows it would have redirected.

    ACROSS TABLES, not within one.  `resolve_asset` walks `mesh, weapon, body,
    hair, ... armet, ...` for EVERY row of every table, so a six-digit stem
    written into `c3/weapon/` is reached by an `armor.ini` row, and reached
    BEFORE `c3/body/`.  Building this per-table would leave that unguarded.
    Measured on 7878, the global set refuses armor exactly the same 6 garments
    the per-table set did, and the three tables' candidate keys do not collide
    with each other at all -- so on this corpus it is free, and it is the rule
    that survives the corpus changing.
    """
    prot: dict = defaultdict(list)
    for t in tables:
        p = Path(ar.root) / "ini" / t
        if not p.is_file():
            continue
        for i, kv in parse_ini(p).items():
            if ar.resolve_asset(i, "mesh") is None:
                continue
            for k in lookup_keys(ar, i, kv):
                prot[k].append("%s[%s]" % (t, i))
    return prot


def _redirect_refusal(key: str, who: list, out: str, ar: AssetRoot,
                      extra: dict) -> dict:
    return dict(extra, garment=key, mesh_out=out,
                reason="would redirect a resolving row",
                detail="key %s is the lookup key of %d row(s) that already "
                       "resolve inside %s (%s%s) -- writing it would CHANGE "
                       "what they wear"
                       % (key, len(who), Path(ar.root).name,
                          ", ".join(sorted(who)[:6]),
                          " ..." if len(who) > 6 else ""))


def _attach_texture(ar: AssetRoot, da: AssetRoot, u: dict, loc, who: str) -> str:
    """Resolve the texture for one unit.  `""` on success, else a refusal detail.

    7878's own wins -- that is the re-skin case, and it is the appearance TQ
    intended.  Only where 7878 has none is the donor's copied in, and it is
    copied to the MESH'S OWN STEM because that is where `resolve_appearance`
    looks on this client.

    THE TABLE'S `Texture0` IS NOT REVIVED.  On `weapon.ini` the colour and
    plus-level variants each declare their own `Texture0` and 4354 unresolved
    rows name 832 distinct ones -- of which 15 resolve inside 7878.  That id is
    legacy on this client (`resolve_appearance` says so and measures it), so
    materialising 817 textures under it would be inventing a mechanism 7878
    does not use, on the axis where a wrong write is invisible.  The donor's
    `Texture0` art is used only as a SOURCE, written out under the mesh's stem.
    """
    if ar.locate(u["tex_out"]):
        u["tex_src"] = None
        u["tex_from"] = Path(ar.root).name
        return ""
    stem = loc.logical.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    sub = loc.logical.split("/")[1]
    tl = da.locate("c3/%s/%s.dds" % (sub, stem))
    if tl is None and u.get("texture_id"):
        tl = da.resolve_asset(u["texture_id"], "texture")
    if tl is None:
        return ("%s: neither %s (%s) nor %s (Texture0=%s, or beside the mesh) "
                "has a texture -- a mesh with no texture is not a delivered "
                "garment" % (who, Path(ar.root).name, u["tex_out"],
                             Path(da.root).name, u.get("texture_id")))
    u["tex_src"] = tl.logical
    u["tex_from"] = Path(da.root).name
    return ""


def plan(ar: AssetRoot, da: AssetRoot, table: str = "armor.ini") -> tuple:
    """`(units, refusals)` for one table, by its measured shape.

    A unit is one FILE this overlay will write, carrying the donor mesh and
    texture it is built from.  Every rejected unit lands in `refusals` with a
    reason; nothing is dropped silently.
    """
    prof = profile(table)
    prot = _protected_keys(ar)
    if prof["shape"] == "slotted":
        units, refusals = _plan_slotted(ar, da, table, prof, prot)
    else:
        units, refusals = _plan_flat(ar, da, table, prof, prot)
    # THE WRITTEN PATHS MUST BE UNIQUE, and this is CHECKED rather than argued.
    # Two units sharing an output would mean one row silently wearing another's
    # mesh -- an error that produces no exception, no LOST and no CHANGED,
    # because both rows were unresolved before and both come back resolving.
    seen: dict = {}
    for u in units:
        seen.setdefault(u["mesh_out"], []).append(u)
    dup = sorted(k for k, v in seen.items() if len(v) > 1)
    if dup:
        raise SystemExit(
            "REFUSED: %d output paths are claimed by more than one unit, e.g. "
            "%s. Two rows would share one file and nothing downstream would "
            "report it." % (len(dup), dup[:4]))
    return units, refusals


def _plan_slotted(ar: AssetRoot, da: AssetRoot, table: str, prof: dict,
                  prot: dict) -> tuple:
    """`armor.ini` and `armet.ini`: 4 body types folded into 2 7878 slots.

    A unit is one `(garment6, slot)` -- the file 7878 would name
    `c3/<write_dir>/<slot><garment6>.c3`.
    """
    wd, rig = prof["write_dir"], prof["rig"]
    secs = _table_sections(ar.root, table)
    unres = {i for i in secs if not ar.resolve_asset(i, "mesh")}
    byg: dict = defaultdict(dict)
    for i, kv in secs.items():
        byg[i[-6:]][i[0]] = (i, kv)

    units, refusals = [], []
    cache: dict = {}
    for g in sorted({i[-6:] for i in unres}):
        rows = byg[g]
        if g in prot:
            for slot, _bt in prof["slots"]:
                refusals.append(_redirect_refusal(
                    g, prot[g], "c3/%s/%s%s.c3" % (wd, slot, g), ar,
                    {"slot": slot, "table": table}))
            continue
        for slot, bt in prof["slots"]:
            u = {"table": table, "garment": g, "slot": slot, "bodytype": bt,
                 "mesh_out": "c3/%s/%s%s.c3" % (wd, slot, g),
                 "tex_out": "c3/%s/%s%s.dds" % (wd, slot, g),
                 # The rows this file is FOR: the garment's rows whose body
                 # type is in this slot's family. Declared intent, not a
                 # measurement -- `resolve_garment` returns the lowest stem for
                 # every body type, so what each row actually gets is settled
                 # by the before/after sweep and nothing here.
                 "serves": sum(1 for b in FAMILY.get(slot, (bt,))
                               if b in rows)}
            if bt not in rows:
                refusals.append(dict(u, reason="no source row",
                                     detail="%s has no body-type-%s row in %s"
                                            % (g, bt, table)))
                continue
            ident, kv = rows[bt]
            u["ident"] = ident
            u["mesh_id"] = kv.get("Mesh0")
            u["texture_id"] = kv.get("Texture0")
            loc = da.resolve_asset(u["mesh_id"], "mesh") if u["mesh_id"] else None
            if loc is None:
                refusals.append(dict(u, reason="donor mesh absent",
                                     detail="%s declares Mesh0=%s; %s has no "
                                            "mesh for it"
                                            % (ident, u["mesh_id"],
                                               Path(da.root).name)))
                continue
            u["mesh_src"] = loc.logical
            if loc.logical not in cache:
                cache[loc.logical] = screen_mesh(
                    loc.logical, da.read(loc.logical), rig)
            ok, reason, nodes = cache[loc.logical]
            u["nodes"] = sorted(nodes)
            if not ok:
                refusals.append(dict(u, reason=reason.split(":")[0],
                                     detail=reason))
                continue
            bad = _attach_texture(ar, da, u, loc, ident)
            if bad:
                refusals.append(dict(u, reason="no texture", detail=bad))
                continue
            units.append(u)
    return units, refusals


def _plan_flat(ar: AssetRoot, da: AssetRoot, table: str, prof: dict,
               prot: dict) -> tuple:
    """`weapon.ini`: no body types, and a 14:1 collapse onto the declared mesh.

    WHY THIS SHAPE EXISTS.  Weapon ids are six digits, so a row's garment key
    IS its id and grouping by key gives 4828 groups of one -- there is no
    body-type fold to make, and `_plan_slotted` run over this table would look
    for body-type rows that do not exist and plan NOTHING, which reads exactly
    like a table with no missing art.

    What it has instead is colour and plus-level: 4354 unresolved rows declare
    308 distinct `Mesh0`.  `[410000]`, `[410003]`, `[410034]` ... all declare
    `Mesh0=410000`.  So the unit is keyed on the DECLARED mesh and 308 files
    serve 4340 rows; under the section id it would be 4354 files of the same
    308 meshes.

    ISN'T "UNDER `Mesh0`" THE MISTAKE THAT COST THE ARMOUR BUILD A REWRITE?  It
    is the same key space, and it is guarded the same way and no other way:
    `prot` holds every key a currently-resolving row looks under, and a group
    whose key is in it is REFUSED.  What made the armour build's first attempt
    wrong was not the choice of name, it was writing keys `prot` covers -- and
    `prot` did not exist yet.  On 7878's `weapon.ini` it covers 2 groups,
    14 rows.

    AND THOSE 14 ARE NOT SIMPLY LOST.  A row whose GROUP key is protected can
    still be served under its own section id, provided THAT key is unprotected:
    its declared key is tried first, misses (nothing is written there), and the
    section id is reached.  Measured on 7878, 0 of those 14 section ids is
    protected, so all 14 rows are delivered at 14 files instead of 2.  The
    per-row path is only reached after the group path is refused, so the common
    case stays at one file per mesh.
    """
    wd, rig = prof["write_dir"], prof["rig"]
    secs = _table_sections(ar.root, table)
    unres = [i for i in secs if not ar.resolve_asset(i, "mesh")]

    groups: dict = defaultdict(list)
    for i in unres:
        # The key the RESOLVER will try first, from the resolver's own
        # function -- not from `kv["Mesh0"]`, which `resolve_garment` never
        # reads.  Where the two disagree the resolver's answer is the one that
        # decides which file a row finds.
        groups[lookup_keys(ar, i, secs[i])[0]].append(i)

    units, refusals = [], []
    cache: dict = {}

    def one(stem: str, mesh_id: str, tex_row: str, serves: list) -> None:
        u = {"table": table, "garment": stem[-6:], "slot": "", "stem": stem,
             "mesh_out": "c3/%s/%s.c3" % (wd, stem),
             "tex_out": "c3/%s/%s.dds" % (wd, stem),
             "ident": serves[0], "serves": len(serves), "mesh_id": mesh_id,
             "texture_id": secs.get(tex_row, {}).get("Texture0")}
        loc = da.resolve_asset(mesh_id, "mesh") if mesh_id else None
        if loc is None:
            refusals.append(dict(u, reason="donor mesh absent",
                                 detail="%s declares Mesh0=%s; %s has no mesh "
                                        "for it (%d row(s) affected)"
                                        % (serves[0], mesh_id,
                                           Path(da.root).name, len(serves))))
            return
        u["mesh_src"] = loc.logical
        if loc.logical not in cache:
            cache[loc.logical] = screen_mesh(
                loc.logical, da.read(loc.logical), rig)
        ok, reason, nodes = cache[loc.logical]
        u["nodes"] = sorted(nodes)
        if not ok:
            refusals.append(dict(u, reason=reason.split(":")[0], detail=reason))
            return
        bad = _attach_texture(ar, da, u, loc, serves[0])
        if bad:
            refusals.append(dict(u, reason="no texture", detail=bad))
            return
        units.append(u)

    for key in sorted(groups):
        rows = sorted(groups[key])
        mesh_id = (ar._declared_mesh(rows[0])
                   or secs[rows[0]].get("Mesh0") or rows[0])
        # The texture belongs to the mesh, so take it from the row that IS the
        # mesh where there is one (276 of 308 groups); otherwise from the first
        # row, recorded in the unit so the choice is not silent.
        tex_row = mesh_id if mesh_id in secs else rows[0]
        if key not in prot:
            one(mesh_id, mesh_id, tex_row, rows)
            continue
        for i in rows:
            if i[-6:] in prot:
                refusals.append(_redirect_refusal(
                    i[-6:], prot[i[-6:]], "c3/%s/%s.c3" % (wd, i), ar,
                    {"slot": "", "table": table, "ident": i}))
                continue
            refusals.append(_redirect_refusal(
                key, prot[key], "c3/%s/%s.c3" % (wd, mesh_id), ar,
                {"slot": "", "table": table, "ident": i,
                 "recovered_as": "c3/%s/%s.c3" % (wd, i)}))
            # THE GROUP'S texture row, not the row's own. All rows in a group
            # share one mesh, so they share its skin; taking `i`'s own
            # `Texture0` here refused 13 of these 14 rows for "no texture"
            # while the mesh they were being cut from had one all along.
            one(i, mesh_id, tex_row, [i])
    return units, refusals


#: The overlay's verification status, written INTO the artefact by `build()`.
#:
#: OWNER RULING 2026-08-30: "ship it labelled verified-resolving,
#: unverified-drawing".  The two halves are different claims and only one of
#: them has been measured:
#:
#:   RESOLVING   tests/test_garment_overlay.py, 66 tests OK -- the rows 7878
#:               could not resolve DO resolve out of this directory, and the
#:               ablation shows they stop when it is removed.
#:   DRAWING     NOT MEASURED.  Donor bodies carry 15 of 7878's 18 attachment
#:               nodes.  A body CARRYING v_back is not proof a cape lands in
#:               the right place on a 7878 skeleton.  Answering it needs a
#:               client launch, which is gated on two owner rulings.
#:
#: It is written by `build()` rather than by hand because `--clean` would erase
#: a hand-placed file: a label a rebuild deletes is not a label on the artefact.
STATUS_NAME = "OVERLAY-STATUS.md"

STATUS_TEXT = """# This overlay is VERIFIED-RESOLVING and UNVERIFIED-DRAWING

Owner ruling, 2026-08-30. Both halves are claims about this directory and only
one of them has been measured. Do not collapse them.

## VERIFIED: it resolves

`tests/test_garment_overlay.py` (66 tests, OK) asserts that appearance rows
7878 cannot resolve on its own DO resolve out of this directory, that removing
it puts them back (the ablation), that nothing already resolving is redirected
(LOST 0 / CHANGED 0), and that no key written here is a resolving row's lookup
key.

## NOT VERIFIED: that it DRAWS correctly

Donor bodies carry 15 of 7878's 18 attachment nodes. The five absent are slot
accessories, for which no art exists anywhere in 7878's 229,225-name namespace.
Every absent node that HAS shipped art -- `v_back` (capes) and `v_pelvis` --
is present on the donor at 60/60.

**But a body CARRYING `v_back` is not proof a cape lands in the right place on
a 7878 skeleton.** Nothing on disk can answer that. It needs one observation
from a running client, which is gated on two owner rulings because the
`Env_DX9` binary fails two of three launch checks.

## What that means for you

Use it: the art appears where 7878 asks for it, and nothing that worked before
is changed. Treat the geometry as UNCONFIRMED against the live renderer until
someone records that observation and replaces this section.
"""


def write_status(target: Path) -> dict:
    """Write `STATUS_NAME` into the overlay root.  Returns what it wrote.

    Called by `build()` on every build, so the label cannot drift from the
    artefact and `--clean` cannot orphan it.
    """
    p = Path(target) / STATUS_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(STATUS_TEXT, encoding="utf-8")
    return {"path": str(p), "bytes": len(STATUS_TEXT),
            "verified": "resolving", "unverified": "drawing"}


def build(ar: AssetRoot, da: AssetRoot, units: list, target: Path) -> dict:
    """Write the planned units.  Returns counts and a manifest."""
    written = {"mesh": 0, "texture": 0, "bytes": 0}
    manifest = []
    for u in units:
        for kind, src, out in (("mesh", u["mesh_src"], u["mesh_out"]),
                               ("texture", u.get("tex_src"), u["tex_out"])):
            if src is None:
                continue
            data = da.read(src)
            p = target / out
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            written[kind] += 1
            written["bytes"] += len(data)
            manifest.append({"kind": kind, "src": "%s:%s" % (Path(da.root).name, src),
                             "out": out, "bytes": len(data),
                             "sha1": hashlib.sha1(data).hexdigest()[:16]})
    status = write_status(target)
    return {"written": written, "manifest": manifest, "status": status}


def _table_stamp(root: Path, tables) -> dict:
    """`{table: {bytes, sha1, sections}}` for the tables this run planned from.

    See the call site: the clients root is resolved through a machine-global
    config another process can rewrite mid-run, so a report has to be able to
    say WHICH file it read, not merely which name.
    """
    out = {}
    for t in tables:
        p = Path(root) / "ini" / t
        if not p.is_file():
            out[t] = {"missing": str(p)}
            continue
        b = p.read_bytes()
        out[t] = {"bytes": len(b), "sha1": hashlib.sha1(b).hexdigest()[:16],
                  "sections": len(parse_ini(p))}
    return out


_SAID_TABLES: set = set()

def _table_sections(root, table: str) -> dict:
    """`ini/<table>` as sections, or `{}` when this install does not ship it.

    `coassets.parse_ini` opens the path, so ONE absent table raised out of
    every one of the five readers below. Zephyr ships `weapon.ini` and
    neither `armor.ini` nor `armet.ini` -- measured across the nine declared
    clients, where it is the only one missing either -- and a survey that
    cannot read one table should lose that table's rows, not the run.

    Reported rather than swallowed, and once per table: "this install has no
    armet table" and "its armet table is empty" are different facts.
    """
    p = Path(root) / "ini" / table
    if not p.is_file():
        if table not in _SAID_TABLES:
            _SAID_TABLES.add(table)
            print(f"[garment] {Path(root).name} does not ship ini/{table}; "
                  f"its rows are absent rather than empty.", file=sys.stderr)
        return {}
    return parse_ini(p)


def _tables(spec: str) -> list:
    """`--table` -> a list.  `all` means every table with a measured shape."""
    if spec.strip().lower() == "all":
        return [t for t in KEY_SPACE_TABLES if t in TABLE_PROFILES]
    return [t.strip() for t in spec.split(",") if t.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--install", default="7878")
    ap.add_argument("--donor", default=DEFAULT_DONOR)
    ap.add_argument("--clients", default="")
    ap.add_argument("--table", default="armor.ini",
                    help="one table, a comma list, or 'all' "
                         "(%s)" % ", ".join(sorted(TABLE_PROFILES)))
    ap.add_argument("--out", default="",
                    help="overlay directory (default out/garment/overlay-<install>)")
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="screen and plan, write nothing")
    ap.add_argument("--clean", action="store_true",
                    help="remove the overlay directory first")
    ap.add_argument("--survey", action="store_true",
                    help="measure every candidate donor and the body-type map")
    ap.add_argument("--selftest", action="store_true",
                    help="run the screen's own arms and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        print("screen selftest:")
        return 1 if selftest() else 0

    clients = Path(a.clients) if a.clients else coroot.clients_dir()
    root = clients / a.install
    if not root.is_dir():
        print("no install at %s -- nothing to do" % root)
        return 2
    tables = _tables(a.table)
    for t in tables:
        profile(t)                       # refuse an unmeasured table up front

    if a.survey:
        rep = {}
        for t in tables:
            print("DONOR SURVEY  %s  (clients tree via core/coroot.py)" % t)
            d = survey_donors(clients, a.install, t)
            print("\nBODY-TYPE MAP  %s  (donor %s)" % (t, a.donor))
            if TABLE_PROFILES[t]["shape"] == "flat":
                print("  NOT APPLICABLE: %s has no body-type dimension. Its "
                      "ids are six digits, the id IS the mesh name, and "
                      "grouping by garment key gives singletons -- there is "
                      "nothing to fold 4 -> 2." % t)
                b = {"applicable": False}
                part = {"applicable": False}
            else:
                b = survey_bodytypes(clients, a.install, a.donor, t)
                print("  BODY-TYPE FAMILIES (donor-only arm)")
                part = survey_bodytype_partition(clients, a.install, a.donor, t)
            rep[t] = {"donors": d, "bodytypes": b, "families": part}
            print("")
        if a.report:
            Path(a.report).parent.mkdir(parents=True, exist_ok=True)
            Path(a.report).write_text(json.dumps(rep, indent=1), "utf-8")
            print("wrote", a.report)
        return 0

    dp = clients / a.donor
    if not dp.is_dir():
        print("no donor install at %s" % dp)
        return 2

    print("screen selftest:")
    if selftest():
        return 1

    target = Path(a.out) if a.out else (HERE / "out" / "garment"
                                        / ("overlay-%s" % a.install))
    _refuse_target(target, clients)

    t0 = time.time()
    ar = AssetRoot.bare(str(root))
    da = AssetRoot.bare(str(dp))

    if not a.dry_run:
        # CLEANED ONCE, BEFORE THE FIRST TABLE.  Cleaning per table would make
        # `--table all --clean` delete the tables it had just written.
        if a.clean and target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

    rep = {"install": a.install, "donor": a.donor, "tables": tables,
           "clients": str(clients), "target": str(target),
           "required_nodes": list(REQUIRED_NODES),
           "slot_source": dict(SLOT_SOURCE),
           # WHICH CORPUS THIS RUN ACTUALLY READ, recorded rather than assumed.
           # `coroot.clients_dir()` resolves through a MACHINE-GLOBAL config
           # that any concurrent process can rewrite: on 2026-08-29 it was
           # rewritten at 14:21:58 while this build and its before/after sweep
           # were running, and for those minutes two independent readers in
           # this repo saw a `7878` whose `armor.ini` had 1217 sections
           # instead of 955. The unresolved row set happened to be identical
           # (579/1060/4354 either way) so the build was unaffected -- but
           # nothing in the report SAID which tree it had read, and a
           # measurement that cannot name its corpus cannot be re-checked.
           # Stamping the size and digest of every table planned from is the
           # cheapest thing that makes two runs comparable.
           "tables_seen": _table_stamp(root, tables),
           "per_table": {}}
    grand: Counter = Counter()
    for t in tables:
        prof = profile(t)
        units, refusals = plan(ar, da, t)
        by = Counter(r["reason"] for r in refusals)
        rows_served = sum(u.get("serves", 1) for u in units)
        meshes = len({u["mesh_src"] for u in units if u.get("mesh_src")})
        print("\n%s <- %s   table %s   [%s, c3/%s/, rig %s]"
              % (a.install, a.donor, t, prof["shape"], prof["write_dir"],
                 prof["rig"]))
        print("  files planned                             %d" % len(units))
        print("  rows those files serve                    %d" % rows_served)
        print("  REFUSED                                   %d%s"
              % (len(refusals),
                 "   <- ZERO, and that is a result, not an absence"
                 if not refusals else ""))
        for reason, n in sorted(by.items()):
            print("      %-28s %4d" % (reason, n))
        # THE RIG SCREEN'S OWN COUNT, PRINTED EVEN WHEN IT IS ZERO.  It is zero
        # on every table on this box, and a screen whose rejections are only
        # ever reported when there are some is one nobody can tell is running.
        rig_ref = by.get("rig", 0) + by.get("unreadable", 0)
        print("      %-28s %4d%s"
              % ("(of which the RIG SCREEN)", rig_ref,
                 "" if rig_ref else
                 "   <- zero rejections, over %d distinct donor meshes "
                 "actually screened" % meshes))
        for r in refusals[:6]:
            print("      e.g. %s" % r["detail"][:140])
        if len(refusals) > 6:
            print("      ... %d more in the report" % (len(refusals) - 6))
        nodes = Counter(len(u["nodes"]) for u in units)
        print("  node count over shipped meshes            %s"
              % sorted(nodes.items()))
        texsrc = Counter(u["tex_from"] for u in units)
        print("  texture from                              %s" % dict(texsrc))

        one = {"table": t, "shape": prof["shape"],
               "write_dir": prof["write_dir"], "rig": prof["rig"],
               "units": len(units), "rows_served": rows_served,
               "refusals": refusals, "refusals_by_reason": dict(by),
               "rig_refusals": rig_ref, "distinct_donor_meshes": meshes,
               "node_counts": dict(nodes), "texture_from": dict(texsrc)}
        if a.dry_run:
            print("  --dry-run: nothing written")
        else:
            res = build(ar, da, units, target)
            one.update(res)
            w = res["written"]
            grand.update(w)
            print("  WROTE  %d meshes + %d textures = %.1f MB  at %s"
                  % (w["mesh"], w["texture"], w["bytes"] / 1e6, target))
        rep["per_table"][t] = one

    if not a.dry_run and len(tables) > 1:
        print("\n  ALL TABLES  %d meshes + %d textures = %.1f MB"
              % (grand["mesh"], grand["texture"], grand["bytes"] / 1e6))
    rep["seconds"] = round(time.time() - t0, 1)
    print("  %.1fs" % rep["seconds"])
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(rep, indent=1), "utf-8")
        print("  wrote", a.report)
    ar.close()
    da.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
