#!/usr/bin/env python3
r"""
patch5165 -- official Conquer Online patch 5165, the boundary client.

The family's parse profile lives in `plugins/plaintext.py` and is not
restated: **the plaintext ini tables are the live ones**, because this client
ships no compiled `.dbc` at all. Read that module first.

5165 is the most interesting of the three, and the reason is a single
measurement: **its plaintext lookup tables are byte-identical to 5517's and
6090's.** Nine of them, by md5 -- ``3dobj.ini``, ``3dtexture.ini``,
``3dmotion.ini``, ``3DSimpleObj.ini``, ``armor.ini``, ``armet.ini``,
``weapon.ini``, ``3DEffect.ini``, ``3DEffectObj.ini``.

So the file `core/dbc.py` calls a *"stale decoy"* on 6090 is **this client's
live table**, unchanged. The plaintext layer did not decay over three patches;
it stopped dead the moment a compiled twin appeared beside it at 5517, and
5165 is the last patch at which it was still being read. That is the
strongest single piece of evidence that the family's inverted assumption is
right rather than merely convenient.

THE FREEZE HAD ALREADY BEGUN HERE, AND YOU CAN SEE IT
------------------------------------------------------
5165's own ``npc.ini`` **outran its own lookup tables by 25 rows**. 880 npc
sections, 855 of which resolve completely; the 25 that do not are mounts and
looks the frozen ``3DSimpleObj.ini`` and ``3dmotion.ini`` never received::

    Zebra, Frostbite, DiamondSteed, Passion (simple objects 50, 53, 55, 56)
    IliadBull, Trampler (234, 235)
    looks 373 (10 rows) and 813 (4 rows)

**That is content, not a parse failure**, and it is the only place in the
family where the plaintext profile does not resolve 100%. 5017 and 5065 are
503/503 and 575/575. Do not "fix" 5165's 25; there is nothing behind them.

WHAT 5165 SHIPS, MEASURED
-------------------------
12,845 files on disk, 119 entries directly in ``ini/``: 97 ``.ini``, 18
``.dat``, **0 ``.dbc``**. Archives byte-identical to the rest of the lineage.

    npc.ini            880 npcs, 855/880 resolve, 1970/1970 paths present
    3DSimpleObj.ini    191 sections        3dobj.ini       1,472 rows
    3dtexture.ini    9,115 rows            3dmotion.ini   64,337 rows
    itemtype.dat     8,308 rows (40 cols)  Monster.dat       568 sections
    armor.ini          955 sections        armet.ini       1,168
    weapon.ini       4,828 sections        Action3DEffect  9,772 rows, all 3.3.3.3
    ItemTexture.ini    542 sections

WHAT IT ADDS OVER 5065
----------------------
* **A 40th ``itemtype.dat`` column, and it is ``qualityColor``.** Named by
  joining to 5517's ``@@`` table on shared item ids: 8,157 of 8,177 agree,
  against **0 of 8,177** for each of three neighbouring columns tried as
  controls. `core/tqdat.py` reads it under that name.
* ``ini/c3.wdb``, ``mounttype.dat``, ``suittype.dat`` and ``silent.dat`` --
  none of which 5017 or 5065 ship.
* Action codes 915, 916 and 917 in ``Action3DEffect.ini`` (still three wide).
"""
from __future__ import annotations

from .catalog import censused

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.plaintext import PlaintextFamily          # noqa: E402
from plugins.catalog import MEASURED_SECTION_LABELS    # noqa: E402
from plugins.patch5017 import SPECS_5017               # noqa: E402
from plugins.catalog import (                          # noqa: E402
    TableSpec, KIND_SECTIONS, KIND_SPACE_ROWS)

#: 5165 is where `mounttype.dat` first appears, and where `MagicType.dat`
#: changes families: `binary-plain` on 5017/5065, `tq-stream` from here on.
#: The filename is identical across all of them, so a spec that carried the
#: name and not the codec would decode 5165's copy as raw bytes and produce a
#: table of noise without raising.
SPECS_5165 = tuple(
    s for s in SPECS_5017 if s.filename != "MagicType.dat"
) + (
    TableSpec("magic", "MagicType.dat", KIND_SPACE_ROWS, "tq-stream"),
    TableSpec("mount", "mounttype.dat", KIND_SECTIONS, "tq-stream"),
)



class Patch5165(PlaintextFamily):
    name = "patch5165"
    label = "Official patch client 5165"
    origin = "official"
    aliases = ("5165",)

    STAMP = "5165"
    notes = ("The plaintext-table family's last member, and the boundary: no "
             "compiled .dbc, so the ini tables are LIVE -- and nine of them "
             "are byte-identical to the files 5517 and 6090 ship as stale "
             "decoys. 855 of 880 npcs resolve; the 25 that do not are "
             "content its own frozen lookup tables never received, not a "
             "parse failure. No visual pass has been done on this client.")

    #: Assigned here TOO, not inherited: this class derives from
    #: `PlaintextFamily`, not from `Patch5017`, so setting it there
    #: reached neither of these two builds. The blank-label guard in
    #: tests/test_plugin_catalogs.py is what noticed.
    ROW_LABEL_KEY = MEASURED_SECTION_LABELS

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_5165, self.name)

    ABSENT_HERE = ()

    #: The plaintext tables 5517 and 6090 inherit from this client
    #: BYTE-IDENTICALLY and then stop reading. Verified by md5 this session.
    #: This is the list that makes "6090's decoy is 5165's live file" a
    #: measurement rather than a story.
    FROZEN_AT_5165 = (
        "ini/3dobj.ini", "ini/3dtexture.ini", "ini/3dmotion.ini",
        "ini/3DSimpleObj.ini", "ini/armor.ini", "ini/armet.ini",
        "ini/weapon.ini", "ini/3DEffect.ini", "ini/3DEffectObj.ini",
    )

    #: **The one place in this family where the plaintext table is
    #: INCOMPLETE, and there is no twin to correct it.** MEASURED on the
    #: filesystem: 5165 ships **204 loose `.c3` motion files under
    #: `c3/1001` .. `c3/1004`** -- groups 000, 410 and 611, and all 120
    #: sampled parse as pure motion sets -- while its own `3dmotion.ini`
    #: names only `0001`..`0004`. 5017 and 5065 ship four and name four.
    #:
    #: On 5517 and 6090 the same ini is equally short and the compiled twin
    #: names sixteen, so the shortfall is invisible there. Here nothing is
    #: authoritative but the filesystem, which is a materially nastier case
    #: than 6090's: an under-reporting table with no shadow behind it.
    UNNAMED_MOTION_FAMILIES = ("1001", "1002", "1003", "1004")

    MOTION_FAMILIES_ON_DISK = ("0001", "0002", "0003", "0004",
                               "1001", "1002", "1003", "1004")

    #: NPC types whose art this client names and does not ship. Content, not
    #: resolution -- see the module docstring. Recorded so the 25 are a known
    #: quantity and nobody spends a session on them.
    UNRESOLVED_SIMPLE_OBJECTS = (50, 53, 55, 56, 234, 235, 373, 813)

    def table_quirks(self):
        q = super().table_quirks()
        q["this client's live tables ARE 6090's decoys"] = (
            "Nine plaintext tables are byte-identical in 5165, 5517 and 6090 "
            "(3dobj, 3dtexture, 3dmotion, 3DSimpleObj, armor, armet, weapon, "
            "3DEffect, 3DEffectObj -- by md5). They are live here and stale "
            "there, and the same bytes are both. Whichever way round you are "
            "reading them, the file cannot tell you: only the presence of a "
            "compiled twin can, which is what prefers_compiled_tables() "
            "exists to record.")
        q["npc.ini already outran the frozen lookup tables"] = (
            "880 npc sections, 855 resolving. The 25 that do not name simple "
            "objects 50/53/55/56/234/235 and looks 373/813, which this "
            "client's own 3DSimpleObj.ini and 3dmotion.ini -- already frozen "
            "at their final content -- never received. It is content, and it "
            "is the only place in the family below 100%: 5017 is 503/503 and "
            "5065 is 575/575. There is nothing behind the 25 to find.")
        q["3dmotion.ini names four motion families and eight are on disk"] = (
            "The family-level quirk covers the shape; this is the instance, "
            "and 5165 is the only member it applies to. 204 loose .c3 files "
            "under c3/1001..c3/1004 (groups 000, 410, 611) that this "
            "client's own 3dmotion.ini never names -- and unlike 5517 and "
            "6090, which are equally short in the ini, there is no compiled "
            "twin here to name them. Anything enumerating this client's body "
            "motions from the table alone sees three quarters of the "
            "families and misses a quarter, with nothing raised. "
            "UNNAMED_MOTION_FAMILIES lists them; the filesystem is the only "
            "authority.")
        q["itemtype.dat grows a 40th column"] = (
            "Still the space-separated form, but 40 columns against 5017's "
            "and 5065's 39. The extra one is qualityColor, named by joining "
            "to 5517's @@ table on shared item ids -- 8,157 of 8,177 agree, "
            "and each of three neighbouring columns tried as a control "
            "agrees on 0 of 8,177. core/tqdat.FIELDS_SPACE_QC carries it.")
        return q


PLUGIN = Patch5165()
