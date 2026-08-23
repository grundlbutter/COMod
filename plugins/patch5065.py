#!/usr/bin/env python3
r"""
patch5065 -- official Conquer Online patch 5065.

The family's parse profile lives in `plugins/plaintext.py` and is not
restated: **the plaintext ini tables are the live ones**, because this client
ships no compiled `.dbc` at all. Read that module first.

5065 is the middle member and the least eventful of the three, which is worth
saying plainly: it declares almost nothing of its own because there is almost
nothing to declare. It has 5017's table *formats* exactly -- 39-column
space-separated `itemtype.dat`, three-wide action fields, nine-digit npc
motion ids -- and 5165's table *set*.

WHAT 5065 SHIPS, MEASURED
-------------------------
7,807 files on disk, 101 entries directly in ``ini/``: 82 ``.ini``, 15
``.dat``, **0 ``.dbc``**. Archives byte-identical to the rest of the lineage.

    npc.ini            575 npcs, 575/575 resolve, 2000/2000 paths present
    3DSimpleObj.ini    156 sections        3dobj.ini       1,344 rows
    3dtexture.ini    7,460 rows            3dmotion.ini   56,056 rows
    itemtype.dat     6,865 rows (39 cols)  Monster.dat       418 sections
    armor.ini          868 sections        armet.ini       1,124
    weapon.ini       4,555 sections        Action3DEffect  8,128 rows, all 3.3.3.3
    ItemTexture.ini    454 sections

WHAT IT ADDS OVER 5017
----------------------
* **``ini/ItemTexture.ini``, 454 sections** -- the table 5017 has none of.
  This is the file CORRECTIONS C20 is about, and 5065 is where it starts. It
  is keyed by **appearance** id, not item StaticID, and mixes six-wide and
  nine-wide spellings (94 and 360 sections respectively) exactly as the later
  clients do.
* **A shield part slot.** `RolePart.ini` declares Count=8 here against 5017's
  7, and that declaration is byte-identical from 5065 all the way to 6090.
* **``Monster.dat`` gains ``ArmetColor`` and ``LWeaponColor``**, the two keys
  5017 lacks.

WHAT IT DOES NOT YET HAVE
-------------------------
The 40th `itemtype.dat` column (`qualityColor`) arrives at 5165, and this
client's `armor.ini`, `armet.ini`, `weapon.ini`, `3dobj.ini`, `3dtexture.ini`,
`3dmotion.ini` and `3DSimpleObj.ini` are all still its own -- the freeze that
makes 5165's copies identical to 6090's has not happened yet.

**This is the most-worked install on this machine** (nine 5065 clients, the
account-collision and hookgate work) and none of that touched its tables. The
`ini/GUI.ini.orig` sitting in `ini/` is from that work, not from TQ.
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

#: Byte-for-byte the same readable set as 5017 -- MEASURED, not assumed from
#: the version numbers being adjacent. 5065's one addition over 5017 is
#: `Play.dat` (`shift-obfuscated`), which is not a content table.
#:
#: **5065 is the install that was written to and re-keyed**, so it is no longer
#: a control for cipher questions. It is still a fine control for *shape*
#: questions like this one, which is why the spec is declared rather than the
#: build being skipped.
SPECS_5065 = SPECS_5017


class Patch5065(PlaintextFamily):
    name = "patch5065"
    label = "Official patch client 5065"
    origin = "official"
    aliases = ("5065",)

    STAMP = "5065"
    notes = ("The plaintext-table family's middle member: no compiled .dbc, "
             "so the ini tables are LIVE. 5017's formats with 5165's table "
             "set -- it adds ItemTexture.ini (454 sections) and the shield "
             "part slot, and still uses the 39-column space-separated "
             "itemtype.dat. 575/575 npcs resolve with every named path "
             "present. No visual pass has been done on this client.")

    #: Assigned here TOO, not inherited: this class derives from
    #: `PlaintextFamily`, not from `Patch5017`, so setting it there
    #: reached neither of these two builds. The blank-label guard in
    #: tests/test_plugin_catalogs.py is what noticed.
    ROW_LABEL_KEY = MEASURED_SECTION_LABELS

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_5065, self.name)

    #: Nothing its siblings have is missing here. Stated rather than left
    #: empty by default, because an empty tuple that nobody wrote is
    #: indistinguishable from one nobody checked.
    ABSENT_HERE = ()

    def table_quirks(self):
        q = super().table_quirks()
        q["where ItemTexture.ini begins"] = (
            "5017 ships no ItemTexture.ini and 5065 introduces it with 454 "
            "sections, so this is the earliest client on which the "
            "items.Color -> ItemTexture[appearance] -> texture chain "
            "(CORRECTIONS C20) exists at all. Keyed by APPEARANCE id and not "
            "item StaticID, and mixing six-wide with nine-wide spellings (94 "
            "sections and 360) -- the same normalisation trap C20 records, "
            "present from the table's first appearance.")
        return q


PLUGIN = Patch5065()
