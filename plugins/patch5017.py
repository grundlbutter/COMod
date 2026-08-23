#!/usr/bin/env python3
r"""
patch5017 -- official Conquer Online patch 5017, the oldest client here.

The family's parse profile lives in `plugins/plaintext.py` and is not
restated: **the plaintext ini tables are the live ones**, because this client
ships no compiled `.dbc` at all. Read that module first; this one adds only
identity and what 5017 does not have.

WHAT 5017 SHIPS, MEASURED
-------------------------
5,430 files on disk (against 7,807 at 5065 and 54,214 at 6090), 91 entries
directly in ``ini/``: 74 ``.ini``, 14 ``.dat``, **0 ``.dbc``**. Both archives
are byte-identical to every other official client's.

Every reader works on it unchanged, verified by running them:

    npc.ini            503 npcs, 503/503 resolve, 2000/2000 paths present
    3DSimpleObj.ini    155 sections        3dobj.ini       1,062 rows
    3dtexture.ini    7,153 rows            3dmotion.ini   56,047 rows
    itemtype.dat    11,255 rows (39 cols)  Monster.dat       411 sections
    armor.ini        2,396 sections        armet.ini       1,708
    weapon.ini       5,021 sections        Action3DEffect  8,685 rows, all 3.3.3.3

WHAT IS ABSENT HERE, AND IT IS NOT THE SAME LIST AS ITS SIBLINGS
-----------------------------------------------------------------
This is why 5017 is its own plugin rather than a row in a table:

* **``ini/ItemTexture.ini`` does not exist.** 5065 introduces it with 454
  sections. So the whole `items.Color -> ItemTexture[appearance] -> texture`
  chain that C20 established has no table to consult on 5017, and an item
  colour here can only come from the appearance's own authored texture.
* **``RolePart.ini`` declares seven part slots, not eight** -- no ``shield``.
  Every other official client from 5065 on declares eight. `AssetRoot.
  part_tables` walks that declaration, so a shield table is not merely empty
  here, it is not asked for.
* **``Monster.dat`` carries no ``ArmetColor`` and no ``LWeaponColor``.** Every
  other client does. `tqdat.parse_monster` is tolerant and just omits the
  keys, so a monster's extra art is a field shorter with nothing to notice.

ITS TABLES ARE OLDER *AND* BIGGER, WHICH IS NOT A CONTRADICTION
----------------------------------------------------------------
``armor.ini`` here has **2,396 sections** where 5065 has 868 and 5165 onward
955 -- and 5017's is stamped 2008-01-10 against 5065's 2008-06-13. The
appearance tables were cut down between these two patches and then grew again;
the file is not a subset of its successor in either direction. Recording it
because the natural assumption -- older client, fewer rows -- is wrong here,
and because `docs/handoff_5517_plugin.md` §3's *"all five ship the same
955-section armor.ini"* is refuted by exactly this (see CORRECTIONS C33).
"""
from __future__ import annotations

from .catalog import censused

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.plaintext import PlaintextFamily          # noqa: E402
from plugins.catalog import (  # noqa: E402
    npc_specs,                          # noqa: E402
    TableSpec, KIND_SPACE_ROWS, KIND_SECTIONS, KIND_GAMEMAP,
    MEASURED_SECTION_LABELS)

#: The oldest build's census, and the smallest: six readable tables out of
#: fourteen `.dat`. 5017 is also the only one of the seven with no
#: `shift-obfuscated` file at all -- every other build ships exactly one
#: (`Play.dat`) -- and the only `block96` verdict anywhere below 7878 is
#: `LevelExp.dat`, which is not enciphered: it decodes as 129 little-endian
#: int32s with 119 of its 128 adjacent comparisons strictly decreasing, in two
#: runs of 56 and 63. That is two exp curves, and a 12-byte block cipher cannot
#: emit it. `core/inidat.py` reaches `block96` there by elimination, not by its
#: positive tail test, which cannot fire when `len % 12 == 0`.
SPECS_5017 = (
    TableSpec("monster", "Monster.dat", KIND_SECTIONS, "tq-stream"),
    TableSpec("item", "itemtype.dat", KIND_SPACE_ROWS, "tq-stream"),
    TableSpec("magic", "MagicType.dat", KIND_SPACE_ROWS, "binary-plain"),
    TableSpec("map:dest", "MapDestination.dat", KIND_SECTIONS, "tq-stream"),
    TableSpec("action", "Action.dat", KIND_SECTIONS, "binary-plain"),
    TableSpec("gamemap", "GameMap.dat", KIND_GAMEMAP, "binary-plain"),
)

SPECS_5017 = SPECS_5017 + npc_specs(('npc.ini', 'terrainnpc.ini', 'npcex.ini'))


class Patch5017(PlaintextFamily):
    name = "patch5017"
    label = "Official patch client 5017"
    origin = "official"
    aliases = ("5017",)

    STAMP = "5017"
    notes = ("The plaintext-table family's oldest member: no compiled .dbc, "
             "so the ini tables are LIVE. Ships no ItemTexture.ini, declares "
             "seven part slots instead of eight (no shield), and its "
             "Monster.dat has no ArmetColor/LWeaponColor. 503/503 npcs "
             "resolve with every named path present. No visual pass has been "
             "done on this client.")

    #: Measured on this build: `monster` sections are keyed by NAME and
    #: carry no `Name` key, `map:dest` carries `title`. The base default of
    #: `Name` printed a blank label on every row of both.
    ROW_LABEL_KEY = MEASURED_SECTION_LABELS

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_5017, self.name)

    #: Tables its siblings ship and this one does not. A declared absence,
    #: so a caller can say "this client has none" instead of "the lookup
    #: returned nothing".
    ABSENT_HERE = ("ini/ItemTexture.ini",)

    #: `RolePart.ini` Count=7. 5065 onwards declare eight, adding `shield`.
    #: Verified against the file on all six declared installs: 5017 is 7, the
    #: other four official clients are 8 and their `RolePart.ini` is
    #: byte-identical to each other, CCO is 13.
    #:
    #: **This tuple was read by nothing in production** -- only by two
    #: assertions in `tools/test_viewer.py`. A declared fact with a passing
    #: test and no consumer is the `C46-dds-numpy` shape, and it had the
    #: concrete consequence below: `slot_socket("shield")` returned `None`
    #: ("no opinion, use the app default") on the one client in the lineage
    #: that has no shield part slot at all. `SLOT_SOCKETS` now overrides it.
    PART_SLOTS = ("body", "armet", "r_weapon", "l_weapon", "mount", "misc",
                  "head")

    #: The family maps `shield` to `v_l_weapon`, which is right wherever a
    #: shield is a part. Here it is not one.
    #:
    #: The SOCKETS are present -- 5017's `[Dumy]` list is the same seven as
    #: every other official client, `v_l_shield` and `v_r_shield` included --
    #: so this is not "the art is missing". It is that `[Config] Count=7`
    #: names no `shield` part, `AssetRoot.part_tables` walks that
    #: declaration, and no shield table is ever asked for. Equipping one is
    #: not a thing this client does, and `""` says so where `None` would have
    #: let the app attach it to the left hand and draw a shield the engine
    #: has no slot for.
    SLOT_SOCKETS = dict(PlaintextFamily.SLOT_SOCKETS, shield="")

    def table_quirks(self):
        q = super().table_quirks()
        q["no ItemTexture.ini at all"] = (
            "5065 introduces ini/ItemTexture.ini with 454 sections and 5017 "
            "ships none, so the items.Color -> ItemTexture[appearance] -> "
            "texture chain (CORRECTIONS C20) has no table here and an item "
            "colour can only come from the appearance's own authored "
            "Texture0. A colour lookup that returns nothing on 5017 is the "
            "client, not a bug.")
        q["seven part slots, no shield"] = (
            "RolePart.ini declares Count=7 here -- body, armet, r_weapon, "
            "l_weapon, mount, misc, head -- where every client from 5065 on "
            "declares eight by adding shield. AssetRoot.part_tables walks "
            "that declaration, so the shield table is never even asked for "
            "on this install.")
        q["Monster.dat is two keys short"] = (
            "No ArmetColor and no LWeaponColor, which every other official "
            "client carries. tqdat.parse_monster omits absent keys rather "
            "than failing, so a monster resolves with one less piece of "
            "extra art and nothing reports it.")
        return q


PLUGIN = Patch5017()
