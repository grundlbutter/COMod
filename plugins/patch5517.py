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

from .catalog import censused

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch6090 import Patch6090, SPECS_6090   # noqa: E402

#: 5517 ships FEWER tables than the 6090 it inherits code from, and this is the
#: direction that goes wrong quietly: inheriting 6090's spec would have made
#: this build claim four tables it does not have, and each would have surfaced
#: as "0 rows" -- a statement about the client that is really a statement about
#: us. MEASURED with `core/inidat.py` on 5517's own `ini/`: no `ItemtypeSub`,
#: no `item_value_type`, no `magictypeex`, no `AutoUseMagic`.
#: `SlotNpc.ini` joins the list because it arrives at 6090: 5517 does not
#: ship it at all. The npc-family tables it DOES have come through
#: `SPECS_6090` unchanged.
_ABSENT_ON_5517 = ("ItemtypeSub.dat", "item_value_type.dat",
                   "magictypeex.dat", "AutoUseMagic.dat", "SlotNpc.ini")
SPECS_5517 = tuple(s for s in SPECS_6090
                   if s.filename not in _ABSENT_ON_5517)


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
    #: NOT inherited from `Patch6090`: its `{"operateactivity.ini": "gbk"}`
    #: was measured on 6090's file, not this build's (6907's is strict UTF-8
    #: and fails GBK at byte 2). Empty = plugin-wide encoding, as before.
    TABLE_ENCODING: dict = {}

    name = "patch5517"
    label = "Official patch client 5517"
    origin = "official"
    aliases = ("5517",)

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_5517, self.name)
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
        """Inherited from 6090, and now **measured to resolve identically**
        here -- which is a weaker claim than "verified", deliberately.

        MEASURED 2026-08-09 via `Catalog.monster_colourways`, run per base in
        **separate processes**: in-process root switching silently keeps the
        first catalogue's plugin, and a run that does it compares a base with
        itself and reports a confident zero difference.

            38 of 39 directories resolve the IDENTICAL strip on 5517 and 6090
             1 differs -- `104n`, 4 stems here against 5, missing 906000000

        **What that buys, and what it does not.** It retires the first failure
        mode this docstring used to warn about -- a set naming a texture 5517
        does not ship now has exactly one instance, and it is named. It does
        **not** retire the second. If the 6090 scan put the wrong skins on a
        creature, 5517 resolves the *same wrong strip*: identical output proves
        the two clients agree, not that either is right. So this inherits
        6090's confidence exactly -- no more, and no longer any less.

        **The textures are the same bytes, and that is NOT evidence.** 114 of
        the 115 declared stems are byte-identical across the two installs --
        necessarily, because both read one `c3.wdf` (md5 `1c16683437bc`) and
        one `data.wdf` of identical size. That comparison could not have come
        out any other way, and it is recorded here only so the next reader does
        not mistake it for confirmation. What genuinely discriminates is
        *existence*: 906000000 is absent from 5517's loose layer, which is the
        only reason the one real difference surfaced at all.

        **Left inherited rather than copied into a `Patch5517.MONSTERS`.** The
        earlier plan was to copy the sets down once checked. Copying 39
        identical sets creates a second place to drift; the measurement says
        they are the same, so the honest encoding is to keep inheriting and
        assert the identity in a test. Only a set that genuinely diverges earns
        its own entry here.
        """
        # The word "unverified" is load-bearing and
        # `ParserPlugins::test_a_subclass_does_not_inherit_its_parents_evidence`
        # asserts it. It caught this label the first time it was rewritten:
        # the measurement below is real, and it tempted a shorter string that
        # dropped the hedge. Strips resolving identically is agreement, not a
        # check -- nobody has confirmed the 6090 scan's CHOICES against this
        # client. Keep both halves: the hedge, then what was measured.
        return ("inherited colourway (6090 scan, unverified here; strips "
                "measured identical, 38/39)", "inferred")

    def import_plan(self, root, exists) -> dict:
        plan = super().import_plan(root, exists)
        plan["note"] = ("patch 5517: shared archives skipped, loose layer "
                        "imported (26,766 files, a strict subset of 6090's "
                        "layout), compiled .dbc tables read")
        return plan


PLUGIN = Patch5517()
