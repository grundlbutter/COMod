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
    KIND_MAGIC_RECORDS, MEASURED_SECTION_LABELS)

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
    # **PROMOTED 2026-09-07 (backlog §13), from `KIND_SPACE_ROWS` +
    # `binary-plain`, which refused it: this file is not rows at all.**
    #
    #     u32 count=648, then 648 u32 ids, then 648 records of 1,584 bytes
    #     record +0 u32 type · +4 u32 unnamed · +8 char[16] NUL-terminated name
    #
    # 4 + 648*4 + 648*1584 == 1,029,028, the whole file. The control is the
    # two arrays agreeing -- `record.type == id // 10` on 648 of 648 -- and
    # they are different regions of the file, neither derived from the other.
    # Shift the record array by one and it drops to 307/647. See
    # `plugins/catalog.KIND_MAGIC_RECORDS` for the layout and what is NOT
    # named: 1,560 of the 1,584 bytes, the field at +4 included.
    # **NAMED, MEASURED, AND NOT DECLARED.** An undeclared subject is
    # not merely unread, it is INVISIBLE -- the tool's silence about
    # levexp.dat read exactly like its silence about a file that is not
    # there. The refusal carries the measurement instead.
    # **The codec is `unknown` and that is `core/inidat.py`'s own word,
    # not a shrug.** The classifier tries the lineage's seed 9527 and
    # this file is the one table that takes 1234, so it recognises
    # nothing -- MEASURED, `inidat.classify(...).family == "unknown"` on
    # every tq-stream build that ships it. Declaring `tq-stream` here
    # would be a claim the classifier contradicts, and
    # `TheSpecsMatchTheDisk.test_the_declared_codec_is_what_inidat_
    # classifies` says so out loud.
    TableSpec("levexp", "levexp.dat", KIND_SPACE_ROWS,
              "unknown", label_key=None, refusal=(
        "levexp.dat OPENS on this install and its grammar is still not "
        "declared, and those are different sentences. "
        "core/tqdat.decrypt(raw, 1234) -- the one seed in the lineage that "
        "is not 9527 -- returns 100.0% printable text from this build's OWN "
        "bytes. **Two builds share this spec list and their counts "
        "differ**: 5017 is 134 rows of 3 tab-separated fields (2,363 "
        "B) and 5065, which reuses SPECS_5017, is 137 rows of the same "
        "3 (2,425 B). Every field is "
        "numeric. What is missing is not the cipher and not the row shape, "
        "it is the COLUMNS: the file carries no header, every one of the 3 "
        "fields is a bare number, "
        "refs/conquer-online-wiki-mdbook/src/files/content/levexp.dat.md is "
        "an empty stub, and no table on this install holds those values. "
        "That is the Action.dat refusal word for word -- a settled row shape "
        "whose row means nothing anything here can check -- so declaring it "
        "would hand a user 134 rows (137 on 5065) of anonymous numbers to "
        "edit with no control at all. Whoever settles the columns will "
        "also need a per-spec seed: Plugin.load_table's tq-stream branch "
        "calls tqdat.decrypt with the default 9527, and TableSpec has no "
        "seed field. This build's file is the NARROW one: three fields, "
        "tab-separated, where 5517 and up carry five space-separated. The "
        "shape changes twice inside the lineage, which is another reason not "
        "to borrow a grammar across it.")),
    TableSpec("magic", "MagicType.dat", KIND_MAGIC_RECORDS, "binary-plain"),
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

    #: The positional twin, measured on 5017's own `ini/`. Assigned here
    #: rather than on `PlaintextFamily` for the reason `ROW_LABEL_KEY` above
    #: is: this family's members do not derive from each other, so a fact set
    #: on one of them reaches exactly one build -- which is the property this
    #: project wants from a measurement.
    #:
    #:   region.ini         27 rows and THIRTEEN fields on this build (14
    #:                      from 5517). Column 6 is a place name on 27/27, 22
    #:                      distinct; column 1 is numeric on all 27. The field
    #:                      count changes across the range and the name column
    #:                      does not, which is why both were re-measured here.
    #:   EventTypeName.ini  24 rows; column 2 non-numeric on 24/24; column 0
    #:                      is `1` on every row and column 1 is `01`..`24`, so
    #:                      the id moves to column 1 as well.
    #:   restrain.ini       5 rows; column 2 is a description, non-numeric on
    #:                      5/5 -- GBK bytes read as latin1 on this build,
    #:                      still the only text in the row. Column 1 is the
    #:                      literal `2` five times.
    ROW_COLUMNS = {
        "region": {"id": 0, "name": 6},
        "eventtypename": {"id": 1, "name": 2},
        "restrain": {"id": 0, "name": 2},
    }

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
