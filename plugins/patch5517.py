#!/usr/bin/env python3
r"""
patch5517 -- the official Conquer Online patch 5517 client.

**Deliberately thin, because the measurement says it should be.** A full
path-and-content diff against 6090 (`tools/clientdiff.py`, 12 workers over
both trees) found:

    5517          26,766 files
    6090          54,129 files
    identical     26,098 at an identical path, byte for byte
    modified         668 at an identical path
    moved              0
    removed            0

**Nothing moved and nothing was deleted.** 5517's layout is a strict subset
of 6090's: 6090 is 5517 plus 27,363 added files and 668 changed ones. So
there is no reorganisation to describe, and every convention the 6090 plugin
encodes -- compiled `.dbc` twins beside stale plaintext, TQ-cipher `.dat`
tables at seed 9527, four-wide `Action3DEffect` action fields, u32-wrapped
motion ids, the per-family colour rules, the missing weapon-set aliases --
holds here unchanged. Verified by running the readers directly:

    3DObj.dbc        2,125 rows        armor.dbc        2,357 rows
    3DSimpleObj.dbc    235 records     3dmotion.dbc   569,699 rows
    itemtype.dat    14,981 rows        Monster.dat        762 rows

Both archives are byte-identical to 6090's (`c3.wdf` 359,069,116 bytes,
`data.wdf` 392,245,257), as they are across the whole official lineage.

WHAT ACTUALLY DIFFERS
---------------------
Of the 176 `ini/` files both clients ship, 85 are byte-identical and 91
differ -- but they differ in **content, not format**: 5517 simply has fewer
items, monsters, armours and effects. Two tables are byte-identical even
there (`misc.dbc`, `miscmotion.dbc`).

Structurally, only two things are 6090-only:

* ``BodyMotionTrans.ini`` -- 6090 adds it; 5517 has no equivalent. Its
  `*WeaponMotion` section is undecoded in either case.
* ``EmotionIco.dbc`` -- 15 compiled tables in 6090 against 14 here.

WHAT IS INHERITED BUT NOT VERIFIED
----------------------------------
The monster colour sets and entity-name corrections come from a **visual
scan of the 6090 base**, and the archives the art lives in are shared, so
they are inherited rather than dropped -- an inherited set that resolves is
better than no answer. But **nobody has checked them on 5517**, and the
loose layer differs by 27,363 files, so a set may name a texture 5517 does
not ship. `Catalog.monster_colourways` filters by existence, so the failure
mode is a short strip rather than a wrong one. Treat them as inherited
defaults, not as measurements, until someone looks.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch6090 import Patch6090            # noqa: E402


def _version(root: Path, exists) -> str:
    """`version.dat`, which every official client stamps with its own patch
    number and nothing else -- b'5517', b'6090'. The cleanest discriminator
    in the lineage, and the reason detection can tell these apart at all."""
    try:
        return (root / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch5517(Patch6090):
    name = "patch5517"
    label = "Official patch client 5517"
    aliases = ("5517",)
    notes = ("Same parse family as 6090 -- compiled .dbc twins, TQ-cipher "
             "itemtype/Monster .dat, four-wide action fields -- with 26,098 "
             "of its 26,766 files byte-identical to 6090 at the same path "
             "and nothing moved or removed. Content differs, format does "
             "not. Monster colour and name data is inherited from the 6090 "
             "scan and has not been verified here.")

    #: 6090 adds these; 5517 has no equivalent.
    ABSENT_HERE = ("ini/BodyMotionTrans.ini", "ini/EmotionIco.dbc")

    #: MEASURED, and it decides what comparing these two clients can tell
    #: you: on weapon placement and armed motion, 5517 and 6090 are the same
    #: client. The armed idle is the same path AND the same bytes --
    #: `c3/0002/410/100.c3`, md5 d414db37d9 in both -- and the socket bases
    #: agree to three decimals (v_l_weapon 0.012, v_r_weapon 1.000, v_armet
    #: 1.000 as the shortest basis row over the 403 swing).
    #:
    #: So 5517 inherits 6090's rewritten `v_l_weapon` dummy track wholesale,
    #: including the mid-swing collapse. **CCO is the only contrast in the
    #: lineage for this**: it is where v_l_weapon is a clean unit rotation,
    #: and where `attach.WEAPON_MOTION_SET` was read from in the first place
    #: because neither official client ships the alias rows.
    SAME_AS_6090 = ("armed motion aliasing", "socket bases",
                    "v_l_weapon's rewritten track")

    #: The body motions that DO differ, so a comparison between these two
    #: clients has somewhere to look. MEASURED over all 716 body-motion files
    #: both ship (`c3/000<shape>/<set>/<action>.c3`): 140 differ, and they
    #: land in a strikingly narrow place --
    #:
    #:     35 action codes x 4 body shapes, and EVERY one is in set 000
    #:
    #: Every armed set (410, 500, 560, 611, 741, 756, ...) is byte-identical
    #: between 5517 and 6090. So an armed figure looks the same in both by
    #: construction, and switching clients to compare one will show nothing.
    #: Compare an UNARMED body on one of these actions instead.
    MOTIONS_DIFFERING_FROM_6090 = (
        "291", "292", "293", "294", "295", "296",
        "503", "504", "505", "514", "515", "516",
        "521", "522", "523", "524", "525", "526", "527", "528", "529",
        "598", "599",
        "632", "633", "634", "635", "636", "637", "638", "639",
        "640", "641", "650", "651",
    )

    def confidence(self, root, exists) -> float:
        """`version.dat` decides, because the table formats cannot: 5517 and
        6090 are the same parse family and both answer yes to every format
        probe. Falling back to the shared-format check would make this
        plugin and the 6090 one indistinguishable, and the tie would be
        broken by dictionary order -- which is not evidence."""
        if _version(Path(root), exists) == "5517":
            return 0.95
        return 0.0

    def table_quirks(self):
        """6090's quirks, plus what this client does not have. Every one of
        6090's applies -- verified by running the readers -- so they are
        inherited rather than restated."""
        q = super().table_quirks()
        q["no BodyMotionTrans.ini"] = (
            "6090 adds BodyMotionTrans.ini with a *WeaponMotion section; "
            "5517 ships no equivalent, so whatever that table governs is "
            "either hard-coded in this client's exe or absent. Both "
            "clients lack 3dmotion's per-weapon-set alias rows, so "
            "attach.WEAPON_MOTION_SET is needed here too.")
        q["one fewer compiled table"] = (
            "14 .dbc files against 6090's 15: EmotionIco.dbc is 6090-only. "
            "misc.dbc and miscmotion.dbc are byte-identical between the two "
            "patches, as are both .wdf archives.")
        return q

    def colour_provenance(self):
        """**Not** 6090's "verified / authored". The colour sets are
        inherited and every one of them is unconfirmed here.

        6090's claim rests on the author looking at 36 monster directories in
        *that* client. This client's loose layer differs by 27,363 files, so
        a set may name a texture 5517 does not ship, or ship a skin the
        creature does not wear. `Catalog.monster_colourways` filters by
        existence, so the first failure mode shows up as a short strip -- but
        the second does not show up at all, and calling it "authored" is what
        would stop anyone checking.

        Say inherited until someone does the same pass here; then this
        override comes off and the sets move into a `Patch5517.MONSTERS` of
        their own.
        """
        return "inherited colourway (6090 scan, unverified here)", "inferred"

    def import_plan(self, root, exists) -> dict:
        plan = super().import_plan(root, exists)
        plan["note"] = ("patch 5517: shared archives skipped, loose layer "
                        "imported (26,766 files, a strict subset of 6090's "
                        "layout), compiled .dbc tables read")
        return plan


PLUGIN = Patch5517()
