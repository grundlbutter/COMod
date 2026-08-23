#!/usr/bin/env python3
r"""
zephyr1057 -- the "Zephyr Conquer" private-server client.

**This plugin exists to stop a confident wrong answer, not to produce a
complete one.** Zephyr is the first client here that resolves **zero** npcs
under every parse profile the project has, and the honest declaration of that
is more useful than a profile picked to make the number non-zero.

WHAT IT IS, MEASURED
--------------------
`version.dat` stamps **b'1064'** -- *not* 1057. The directory it ships in is
called `Zephyr-1057-local`, which is exactly the case
`docs/parser_plugins.md` means by *"judge by contents, never the folder's
name"*, and the folder name is the one most likely to be quoted.

    ini/                233 files, **0 .dbc**
    containers          c3.tpi/c3.tpd + data.tpi/data.tpd AND garments0-4.wdf
    archive entries     130,532  (98,812 .dds, 29,717 .c3, 1,510 .jpg, 355 .msk)
    tables in archives  0 -- pure art, plus 9 incidental desktop.ini/img.dat

It is a **mixed-container root**: DatPkg *and* WDF in one install. That is the
case `coassets.AssetRoot._discover_archives` was written for, and Zephyr is the
reason a root is modelled as a set of archives rather than a pair.

IT HAS NO ART LOOKUP CHAIN, IN ANY CONTAINER
--------------------------------------------
`3DSimpleObj.ini`, `3dobj.ini`, `3dtexture.ini`, `3dmotion.ini` -- **all
absent**, on disk and inside both archives. `ini/3DSimpleObjEx.ini` exists and
is **0 bytes** (and is 0 bytes on 6090 and 7878 too): a file that any
existence check counts as present and any reader gets nothing from.

So `npc.ini` ships 2,785 rows carrying `SimpleObjID` and `StandByMotion`, and
nothing in the install says what those ids point at.

MEASURED WITH `npcart.audit()`, THE PROJECT'S OWN TWO-SIDED INSTRUMENT
-----------------------------------------------------------------------
Controls first, because a number from a new client is worth nothing unless the
recorded ones reproduce:

    base      profile      npcs  resolved       %
    6090      official     2251      2243   99.6%   (docs record 2256/2264)
    6090      plaintext    2251      1802   80.1%   (docs record 1815/2264)
    7878      plaintext    4084      2246   55.0%
    ------------------------------------------------
    Zephyr    cco             0         0    0.0%   <- loads NO rows at all
    Zephyr    official     2760         0    0.0%
    Zephyr    plaintext    2760         0    0.0%

`npcart.detect_profile()` answers **`cco`** for this client, and the CCO
profile **loads zero rows** -- it wants `npc.json`, which Zephyr does not
ship. That is an answer by elimination, not by evidence, and it is exactly the
*"the wrong profile never raises -- it answers"* failure the contract warns
about. **`table_profile()` therefore returns None rather than dressing that up
as a decision.**

TWO CANDIDATE RULES THAT DID NOT SURVIVE THEIR CONTROLS
--------------------------------------------------------
Recorded because both looked like findings, and the second one was reported as
one before the control was fixed:

* `c3/npc/<StandByMotion>.c3` (the CCO direct-file rule) resolves **0.8% on
  Zephyr and 0.8% on 6090** -- identical on the client it should fit and the
  one it should not, so it measures nothing.
* `c3/npc/<SimpleObjID>/1.c3` resolves 42.6% here -- and **14.5% on 6090 and
  61.3% on 7878**, where 7878 scores highest. The `999<id>101.c3` form is
  24.5% here against **27.5% on 6090**. So the directory-per-id layout is a
  **shared official convention, not a Zephyr discriminator.** The first
  measurement that said otherwise scored candidate rules against the archive
  *name list*; WDF is hash-keyed and exposes no names, so 6090 scored 0 on
  every rule **by construction** -- a control that cannot fire, which reads as
  a perfect discriminator. `AssetRoot.locate()` asks both container kinds the
  same question and is what the numbers above use.

WHAT IS ACTUALLY DECIDABLE
--------------------------
Zephyr's art is laid out like an official client's; what it lacks is the
*tables*. So the open question is not "what convention does this client use"
but "where did its lookup tables go", and nothing in the install answers it.

`confidence()` is therefore structural and positive: the stamp, plus the
DatPkg container, plus `npc.ini` present, plus **both** the compiled and the
plaintext chains absent. No official client satisfies that conjunction --
5017/5065/5165 have the plaintext chain, 5517/6090/6609 have the compiled one,
and 7878 has the plaintext chain frozen but present.
"""
from __future__ import annotations

from .catalog import censused

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                              # noqa: E402
from plugins.catalog import (  # noqa: E402
    npc_specs,                           # noqa: E402
    TableSpec, KIND_AT_ROWS, KIND_CSV_ROWS, KIND_SECTIONS, KIND_GAMEMAP,
    MEASURED_SECTION_LABELS)

#: Zephyr's own census -- 58 `.dat` in `ini/`, of which 37 are readable:
#: 30 `tq-stream`, 4 `binary-plain`, 3 `plaintext`. It is the richest content
#: surface outside 7878's derived corpus, and it is a private server's client
#: rather than an official patch, so nothing here is inherited from the
#: official lineage.
#:
#: **Two tables are shipped EMPTY, at zero bytes**, and they are the two that
#: would otherwise look like an oversight: `ItemtypeSub.dat` and
#: `item_refine_effect_ex.dat`. 6090 and 6609 both ship real content in
#: `ItemtypeSub.dat` (271 and 7,402 rows), so its absence here is a fact about
#: Zephyr rather than a gap in this list. They are not declared -- an empty
#: file yields a permanent refusal, and "this client has none" is better said
#: once here than printed on every listing.
#:
#: **`Tips.dat` is NOT declared, and the reason is worth reading.** It is a
#: BINARY record table -- a 32-byte name header, then `{u32 id, u32 len,
#: char[len]}` records carrying the loading-screen hints -- the same shape as
#: `GameMap.dat`. `core/inidat.py` classifies it `plaintext` because most of
#: its bytes are printable English, and this list first declared it as
#: space-separated rows. It "parsed 2 rows" and **the positive control
#: PASSED**, because the two garbage tokens it produced genuinely are adjacent
#: in the file, so checking adjacency proved nothing at all. It was caught by
#: reading the printed row, which was visibly junk. `control_at_row` now
#: refuses a field containing control characters, which is the positive
#: evidence that a "text table" is not text; the layout itself is not
#: established here, so the table is left out rather than half-read.
#:
#: `Shop.dat` is `plaintext`, not `tq-stream`, alone among the section tables.
#: A spec that carried the family from its neighbours would decrypt a file
#: that was never encrypted, and `tqdat.decrypt` does not raise -- it would
#: have produced a table of noise with a count.
SPECS_ZEPHYR = (
    TableSpec("monster", "Monster.dat", KIND_SECTIONS, "tq-stream"),
    TableSpec("mount", "mounttype.dat", KIND_SECTIONS, "tq-stream"),
    TableSpec("item", "itemtype.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("item:value", "item_value_type.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("item:refine", "item_refine_attr.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:cost", "item_refine_cost.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:effect", "item_refine_effect.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:upgrade", "item_refine_upgrade.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("magic", "MagicType.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("magic:ex", "magictypeex.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("magic:op", "magictypeop.dat", KIND_CSV_ROWS, "tq-stream"),
    # No key appears in every section of either (both open with a
    # `[Header]`-style block that shares nothing with the rows), so the
    # id stands alone. Declared, not accidental.
    TableSpec("shop", "Shop.dat", KIND_SECTIONS, "plaintext",
              label_key=None),
    TableSpec("stagegoal", "StageGoal.dat", KIND_SECTIONS, "tq-stream",
              label_key=None),
    TableSpec("title", "title_type.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("task:reward", "task_reward_type.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("shop:exchange", "exchange_shop_goods.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("award", "award_config.dat", KIND_AT_ROWS, "tq-stream"),
    TableSpec("gamemap", "GameMap.dat", KIND_GAMEMAP, "binary-plain"),
)
from plugins.plaintext import (COMPILED_MARKERS,        # noqa: E402
                               PLAINTEXT_CHAIN, version_stamp)

SPECS_ZEPHYR = SPECS_ZEPHYR + npc_specs(('npc.ini', 'NpcX.ini', 'terrainnpc.ini', 'npcex.ini', 'SlotNpc.ini'))


class Zephyr1057(Plugin):
    name = "zephyr1057"
    label = "Zephyr Conquer (private server)"
    origin = "server"
    aliases = ("zephyr", "1064")

    #: Same measurement as the official builds for the tables it shares.
    ROW_LABEL_KEY = MEASURED_SECTION_LABELS

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_ZEPHYR, self.name)
    notes = (
        "Private-server client stamped b'1064' despite shipping in a folder "
        "named 1057. Mixed containers: a DatPkg pair AND five garment WDFs. "
        "Ships NO art lookup chain in any container -- 3DSimpleObj/3dobj/"
        "3dtexture/3dmotion all absent, 3DSimpleObjEx.ini is 0 bytes -- so "
        "its 2,785 npc.ini rows resolve 0% under every profile the project "
        "has, measured with npcart.audit(). No colour or name data is "
        "claimed; nobody has looked at this client on screen."
    )

    #: `version.dat`. The folder says 1057 and the bytes say 1064; the bytes win.
    STAMP = "1064"

    #: MEASURED with `npcart.audit()`, all three profiles. Declared so a caller
    #: can tell "this client resolves nothing" from "our resolver broke".
    NPC_COVERAGE = {"rows": 2760, "resolved": 0,
                    "profiles_tried": ("cco", "official", "plaintext")}

    #: Present, and empty. Any existence check counts it; no reader gets a row.
    EMPTY_TABLES = ("ini/3DSimpleObjEx.ini",)

    def confidence(self, root, exists) -> float:
        """Positive and structural, and it needs every clause.

        The stamp alone would claim any repack that copied `version.dat`; the
        absences alone would claim CCO, which also ships no `.dbc`. The
        conjunction -- stamped 1064, DatPkg container, `npc.ini` present, and
        **both** lookup chains absent -- is satisfied by no official client and
        by no other install here.
        """
        if version_stamp(root) != self.STAMP:
            return 0.0
        if not exists("c3.tpi") or not exists("ini/npc.ini"):
            return 0.0
        if exists("ini/npc.json"):                 # that would be CCO's shape
            return 0.0
        if any(exists(m) for m in COMPILED_MARKERS):
            return 0.0
        if any(exists(t) for t in PLAINTEXT_CHAIN):
            return 0.0
        return 0.95

    def table_profile(self):
        """None -- no opinion, deliberately.

        Every profile resolves 0% here, and `detect_profile` returns `cco` by
        elimination while loading zero rows. Naming a profile would convert
        "we cannot resolve this client" into "this client is CCO-shaped",
        which is the confident wrong answer this plugin exists to prevent.
        Returning None leaves the app's own inference in charge and labelled.
        """
        return None

    def prefers_compiled_tables(self) -> bool:
        """False as a statement, not as a default: this client ships no
        compiled twin anywhere, so there is nothing for a twin to shadow."""
        return False

    def colour_provenance(self):
        """Nothing authored and nothing inherited -- no colour set is shipped
        here, so whatever `default_colour` returns is the app's convention."""
        return "app convention (no scan on this client)", "inferred"

    def table_quirks(self) -> dict:
        """Everything below is surfaced through `/api/plugins`, which is what
        makes these declarations channels with a reader rather than comments.
        `tests/test_zephyr1057.py` re-derives them from the install."""
        return {
            "the folder name is 1057 and the client is 1064": (
                "version.dat stamps b'1064'. The install directory is named "
                "Zephyr-1057-local. Judge by contents, never by the folder -- "
                "and quote the stamp, because the folder name is what ends up "
                "in messages."),
            "no art lookup chain, in any container": (
                "3DSimpleObj.ini, 3dobj.ini, 3dtexture.ini and 3dmotion.ini "
                "are absent from ini/ AND from both archives, which hold 0 "
                "table entries between them. ini/3DSimpleObjEx.ini exists and "
                "is 0 BYTES -- present to any existence check, empty to any "
                "reader. So npc.ini's 2,785 SimpleObjID references have "
                "nothing in the install to resolve against."),
            "zero npcs resolve under every known profile": (
                "npcart.audit(): cco loads 0 rows (it wants npc.json), "
                "official 0/2760, plaintext 0/2760. Controls in the same run "
                "reproduce the recorded figures -- 6090 official 99.6%, 6090 "
                "plaintext 80.1% -- so the instrument is sound and the zero "
                "is the client. A short answer here is the expected answer."),
            "detect_profile answers by elimination": (
                "It returns 'cco' for this client and the CCO profile then "
                "loads zero rows. table_profile() returns None rather than "
                "adopting it: 'we cannot resolve this' and 'this is CCO' are "
                "different claims and only one of them is true."),
            "the npc directory layout is NOT a discriminator": (
                "c3/npc/<SimpleObjID>/1.c3 resolves 42.6% here, 14.5% on 6090 "
                "and 61.3% on 7878; the 999<id>101.c3 form is 24.5% here "
                "against 27.5% on 6090. It is a shared official convention. "
                "An earlier measurement showed 6090 at 0.0% because it scored "
                "rules against the archive NAME LIST, and WDF is hash-keyed "
                "with no names -- a control that could not fire. Use "
                "AssetRoot.locate(), which asks both container kinds the same "
                "question."),
        }


PLUGIN = Zephyr1057()
