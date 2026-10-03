#!/usr/bin/env python3
r"""
patch7217 -- official Conquer Online patch 7217: 7205 plus twelve patches of
content, same containers, same compiled art, same cipher.

A THIN SUBCLASS OF `Patch7205`, AND THE SUBCLASS CHOICE IS A MEASUREMENT
-------------------------------------------------------------------------
Everything in this section was read off `<Clients>/7217` and `<Clients>/7205`
on 2026-09-19 with directory listings, file sizes, sha256 of small files and
`core/inidat.classify` on the 24 `.dat` whose size moved -- nothing that opens
an archive or decodes a table. The heavier numbers came later the same day
from `measure_7217.py` (worktree root, not committed; run by the Director,
EXIT 0, 36 s) and are in the section MEASURED BY measure_7217.py below.

MEASURED
~~~~~~~~
* **`version.dat` is `7217\r\n`** -- six bytes, CRLF-terminated, where 7205's
  is the four bytes `7205`. `patch7205._version` strips it, so the stamp reads
  `7217`. It is the only discriminator `confidence()` uses.
* **Containers: `.wdf`, the 6609 pair, and nothing else.** `c3.wdf`
  (359,069,116 B) and `data.wdf` (392,245,257 B), each the SAME SIZE as
  7205's, both stamped 2005-10-18, link count 1. No `.tpd` / `.tpi` in the
  root. The top-level listing is IDENTICAL to 7205's name for name (`diff` of
  the two `ls` outputs is empty). 7217 is therefore in the `.wdf` era; the
  switch to `.tpd`-only is at 7275 -> 7280 per the plan's inventory, not
  here. Byte identity of the two archives was measured later by
  `measure_7217.py`: both sha256-IDENTICAL to 7205's.
* **All twelve `.dbc` are byte-identical (sha256) to 7205's**, and so to
  6609's -- the same twelve prefixes `patch7205.FROZEN_COMPILED`'s header
  lists (`armet 9415fee5e4b7` ... `weaponmotion 5791de723722`). The compiled
  art froze at 6609 and is still frozen at 7217.
* **`ini/` is 7205's plus fourteen files, minus none.** 1,044 files -> 1,142
  (recursive; the `tme/` subdirectory adds 84 `.tme`). New at top level:
  twelve `.lua` (`basedef`, `MonthlyCard`, `TaskRank`, `TaskRankInfo`,
  `TreasureHunt`, `HeroReturn`, `EnemyInvade`, `CoatData`,
  `AnnualBenefitsAward`, `ForeignInvasionAward`, `PkDetainReward`,
  `TYXJ_LotteryAward`), `Appqdkey.ini` (29 B) and **`month_card_type.dat`,
  which is a 0-byte file** -- so the one new `.dat` is not a table, and is
  refused below in the words 7205 uses for its six 0-byte files.
* **148 top-level `ini/*.dat` (7205: 147). 123 of the 147 shared ones are
  byte-identical (sha256) to 7205's**, 15,765,882 bytes compared. That
  includes `itemtype.dat`, `levexp.dat`, `runeeffect.dat` and `Action.dat`.
  The other **24 changed size**, and every one of the 24 classifies the SAME
  family under `core/inidat.classify` on both builds (23 `block96`,
  `GameMap.dat` `binary-plain`):

      Achievement  AutoUseMagic  coat_storage_attr (640 -> 3,591 B)
      coat_storage_type  exchange_shop_goods  GameMap  hairface_storage_type
      instance_enter_condition  instancetype  item_refine_effect_ex
      item_value_type  ItemtypeSub (207,838 -> 341,929 B)  MagicType  monster
      marketing_active  mounttype  newslot_broadcast  newslot_type
      Operating_Prize  prof_lev_benefit  prof_lev_up  quickpay  ServerPlay
      title_type

  So **every codec `SPECS_7205` and `SPECS_7205_CENSUSED` declare is still the
  disk's codec here**, and every file they name is still on disk. That is why
  this class declares no specs of its own.
* **Detection before this file existed** (individual `confidence()` calls on
  the real install, not `rank()`): `patch6907` 0.55 (its sibling bid),
  `patch6090` 0.45, `patch7205` 0.0, `patch6609` 0.0. 7217 opened through
  `patch6907`'s specs -- the block96 era, but 6907's census, not 7205's.

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The column declarations of the 24 changed tables.** `SPECS_7205_CENSUSED`
  pins name columns (`instancetype` col 1, `title_type` col 2,
  `hairface_storage_type` id col 1 / name col 3) measured on 7205's BYTES.
  Same family and same file on a later build makes the same grammar likely;
  it is not shown. `coat_storage_attr` grew 5.6x and `ItemtypeSub` 1.6x --
  those are the two most worth re-reading.
* **The cipher-level evidence.** `patch7205.ITEM_COLUMN_AGREEMENT` (the 6868
  cross-build control) was measured on 7205's `itemtype.dat`, and 7217's is
  byte-identical, so it transfers by identity of bytes, not by argument.
  That is the strongest inherited claim here and it is the only one of its
  kind.

MEASURED BY measure_7217.py (2026-09-19, every number is Clients/7217's
unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 48 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: the winner or
  margin changes on 7217 ONLY -- `patch7217` 0.95, margin 0.40, where it was
  `patch6907` 0.55. `patch7217` scores 0 on the other 47. Control: 7205
  still `patch7205` 0.95.
* **Containers:** `c3.wdf` + `data.wdf`, sha256-identical to 7205's.
  `part_tables()`: nine slots, identical to 7205's row for row (armet
  2,793, armor 3,754, weapon 13,292, Mount 1,223; head/misc 0).
* **block96, POSITIVE CONTROL FIRST:** 7205 read 101 files / 809 unknown
  blocks / 99 fully covered, matching `Patch7205.BLOCK96_CENSUS` -> PASS.
  **7217: 101 files, 1,504,890 blocks, 15,028 unknown (99.00% known); 83
  fully covered, 16 PARTIAL, 2 at zero** (`levexp.dat`, `ServerPlay.dat`).
  The 16 partial tables are all among the 24 that changed since 7205; the
  7878-derived dictionary has never seen some of their new blocks. See
  `PARTIAL_TABLES`. **This is a known limit, recorded, not tuned away.**
* **`catalogs()`: 247 subjects, 245 ok -- same as 7205** (247 / 245); no
  subject that is ok on 7205 fails on 7217, and none counts > 3x 7205's (the
  wrong-cipher noise shape). Row counts (7205 -> 7217): item 44,684 ->
  44,684 (identical file), mount 1,819 -> 1,823, map:dest 936 -> 936,
  magic:op 979 -> 979, and the partial-table losses: monster 3,670 ->
  1,814, magic 2,907 -> 2,115, item:sub 633 -> 371, title_type 179 -> 36,
  exchange_shop_goods 1,706 -> 225, coat_storage_type 765 -> 107,
  prof_lev_up 549 -> 199, instancetype 340 -> 117, hairface_storage_type
  418 -> 137, achievement 391 -> 367, magic:auto 15 -> 1.
* **FINDING: `patch6907` reads MORE rows than this plugin on 7217 for 13
  of those partial subjects** (monster 2,096 vs 1,814, exchange_shop_goods
  412 vs 225, coat_storage_type 301 vs 107, magic 2,145 vs 2,115, ...),
  while refusing `item:sub` outright (this plugin: 371). Not investigated:
  the likely cause is the two plugins' different record-recovery rules
  (whole records only vs truncated rows served), which is a difference in
  what is served, not in what is decoded. Recorded, not changed.
* **Name columns of the changed tables** (INFERRED from 7205 above): the
  measured labels are text where 7205 declares a name column
  (`instancetype` `Exorcism`, `title_type` `Overlord`,
  `hairface_storage_type` `YouthSpark`, `achievement`, `item:sub`,
  `magic` `Thunder`). Numeric labels on `monster`, `mount`, `item:value`,
  `item:refine:effect:ex` and `magic:auto` come from `SPECS_7205`'s curated
  specs, identical on 7205; not compared against 7205 in this run.
* Script surface Lua 224 -> 238, DLL 37 -> 37. `npc.ini` 2,913 -> 2,956
  section headers.

NOT MEASURED
~~~~~~~~~~~~
* `npc.ini` resolution against the frozen `3DSimpleObj.dbc`;
* the browse-impact counts and the 7878-identity split of the census.

Every class constant `Patch7205` pins from 7205's bytes and that is NOT
carried by a byte-identical file is replaced below by 7217's measurement,
or by `None` where there is none, rather than inherited -- an inherited 7205
number on a 7217 class reads as a 7217 measurement.

TEXT ENCODING: `gbk`, MEASURED PER TABLE (owner ruling "fix lineage wide")
------------------------------------------------------------------------
SUPERSEDES every "stays latin1" above. 2026-09-19, branch
`director/comod-gbk-lineage`. INSTRUMENT: every declared subject's
`browse()` labels, plus every `Name=` value of every declared section
table (`Name=:` below), read through THIS plugin with its encoding
replaced by a probe codec: GBK with surrogateescape, so the parse is
GBK's (latin1 splits a line on a 0x85 trail byte and strips a 0xA0 one)
and any byte that is not strict GBK survives as a visible surrogate.
PASS = strict GBK to CJK (U+3400-9FFF), full-width punctuation
(U+3000-303F, U+FF00-FFEF) or GBK pinyin Latin (U+00C0-01DC).
PROBE: strict GBK rejects `81 20`, `ff fe` and a lone `a1`. CONTROL:
7205 by the same instrument, 21 tables with high-byte names, 0 failures.
RESULT on 7217: **0 strict-GBK failures** in 359 subjects/name
sources (2 refused, not read); 15 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1834    13    13     0      0     0    0
    Name=:npc:NpcX.ini              864     3     3     0      0     0    0
    Name=:npc:npc.ini              2956    40    20    20      0     0    0
    Name=:npc:terrainnpc.ini        167    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                166     3     3     0      0     0    0
    achievement                     367     2     0     0      0     2    0
    classdesc                       116    25    25     0      0     0    0
    item                          44682     1     0     1      0     0    0
    map:dest                        936     7     6     0      0     1    0
    npc:NpcX.ini                    859     3     3     0      0     0    0
    npc:npc.ini                    2927    20    20     0      0     0    0
    npc:terrainnpc.ini              165    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      166     3     3     0      0     0    0
    strres                         1189    20    20     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 219: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 754: `a1 a1` = '\u3000'

`other` is GBK row-A1 general punctuation (U+2019 `a1 af`, U+2026
`a1 ad`) inside English text, e.g. "Texas Hold’em" -- strict GBK and
only sensible as GBK (latin1 reads `¡¯`), so not a failure.
Name fields only. A WHOLE-TABLE strict decode found files that are not
GBK; they carry a per-table codec through `TABLE_ENCODING` (see the class)
and `TableSpec.encoding`, added to the base the same day.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch7205 import Patch7205, _version      # noqa: E402


class Patch7217(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1834, 13, 13, 0],
        'Name=:npc:NpcX.ini': [864, 3, 3, 0],
        'Name=:npc:npc.ini': [2956, 40, 20, 0],
        'Name=:npc:terrainnpc.ini': [167, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [166, 3, 3, 0],
        'achievement': [367, 2, 0, 0],
        'classdesc': [116, 25, 25, 0],
        'item': [44682, 1, 0, 0],
        'map:dest': [936, 7, 6, 0],
        'npc:NpcX.ini': [859, 3, 3, 0],
        'npc:npc.ini': [2927, 20, 20, 0],
        'npc:terrainnpc.ini': [165, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [166, 3, 3, 0],
        'strres': [1189, 20, 20, 0],
        'txtemotion': [9, 9, 9, 0],
    }

    #: PER-TABLE exceptions to GBK, MEASURED 2026-09-19 on this install by a
    #: strict decode of each declared table's whole text. Both are strict
    #: UTF-8 and fail strict GBK (Title.ini at byte 31; WrapTypeData.ini at
    #: byte 32, ~42 undecodable). No control was lost under gbk; they are
    #: set so their CJK reads as text instead of GBK mojibake. StrRes.ini
    #: (where declared) also fails strict GBK, but on 2 bytes in a header
    #: COMMENT with 588 GBK CJK elsewhere, so it stays GBK.
    #: ADDED the same day with the CR/LF line fix: `OperateActivity.ini`
    #: (43,680-66,813 high bytes, 260-563 of them 0x85 across 7205..7589)
    #: and both `TexasChatGUI*.ini` (1,551 high, 23 x 0x85) are strict UTF-8
    #: and fail strict GBK (at bytes 22 and 2) on every lineage build. They
    #: reach this build through 6609's census, which newly settles them.
    TABLE_ENCODING = {"title.ini": "utf-8", "wraptypedata.ini": "utf-8",
                      "operateactivity.ini": "utf-8",
                      "texaschatgui.ini": "utf-8",
                      "texaschatgui800x600.ini": "utf-8"}

    name = "patch7217"
    label = "Official patch client 7217"
    origin = "official"
    aliases = ("7217",)

    STAMP = "7217"

    notes = (
        "7205's layout twelve patches on: the same c3.wdf/data.wdf pair "
        "(same sizes), the same twelve .dbc (sha256-identical to 7205 and "
        "6609), and the same 96-bit block cipher on ini/*.dat. 123 of the "
        "147 .dat it shares with 7205 are byte-identical, itemtype.dat "
        "included; 24 changed and every one kept its codec. New: twelve "
        "Lua scripts, Appqdkey.ini, 84 .tme effects and a 0-byte "
        "month_card_type.dat. The 7878-derived block dictionary covers 99.0% "
        "of this build's blocks: 16 changed tables are partial (monster "
        "1,814 whole sections vs 3,670 on 7205, magic 2,115 vs 2,907, "
        "title_type 36 vs 179) and records with an unknown block are "
        "dropped, never guessed."
    )

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7217`, at 0.95, and nothing else.

        Same rule as `patch7205` / `patch6609`: every format probe answers the
        same on 7205 and 7217 (identical `.dbc`, same-size archives, same
        cipher), so only the stamp discriminates. 0.95 beats `patch6907`'s
        0.55 sibling bid, which is what 7217 fell to before this plugin.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- what 7217 ships that 7205 does not -------------------------------
    #: 7205's refusals, all still true of files that are byte-identical or
    #: absent-in-both here, **plus the one new `.dat`**. `serverplay.dat` is
    #: kept in the inherited wording but is a DIFFERENT file on 7217
    #: (5,696 -> 5,728 B); whether the dictionary still knows none of its
    #: blocks is for `measure_7217.py`.
    UNDECLARED_DAT = {
        **Patch7205.UNDECLARED_DAT,
        "month_card_type.dat": (
            "the client ships it as a 0-byte file. NEW at 7217 -- 7205 does "
            "not ship it at all -- and empty on arrival, so a new table NAME "
            "is all this build adds; there is no content to declare."),
    }

    # -- 7217's OWN measurements (measure_7217.py, 2026-09-19, 36 s) -------
    #: Marker for `tools/dat_census.py`. **False, and the reason is the
    #: census, not the cipher.** 7217's `ini/*.dat` ARE the 96-bit block
    #: cipher (101 files classify `block96`, MEASURED). Joining the census
    #: was tried 2026-09-19: with "7217" in `dat_census.BUILDS`,
    #: `tests/test_dat_census.TheVerdictIsPerBuild.
    #: test_a_file_on_one_build_is_reported_on_one_build` went red with
    #: `['7189', '7217'] != ['7189']` -- 7217 also ships `combat_gear.dat`,
    #: which that test pins to 7189 alone. The census's per-build
    #: expectations do not cover a new build yet, so 7217 stays out of
    #: `BUILDS` (slot left commented) until they do. 7217's own census
    #: numbers are kept in `BLOCK96_CENSUS` below.
    DAT_CENSUS_ERA = False

    #: MEASURED on `Clients/7217`, `block96.read_table(...).recovery`.
    #: `itemtype.dat` is byte-identical to 7205's and reads the same;
    #: `mounttype.dat` changed and still closes; the other three are
    #: PARTIAL -- the dictionary (built from 7878) has never seen some of
    #: 7217's new blocks. 7205 reads all five at 1.0.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1080527, "known": 1.0, "lines": 44684,
                         "intact": 44684, "units": 44684, "shape": "rows"},
        "monster.dat": {"blocks": 106226, "known": 0.9643, "lines": 95851,
                        "intact": 93353, "units": 2123, "shape": "sections"},
        "magictype.dat": {"blocks": 47840, "known": 0.966, "lines": 2466,
                          "intact": 2115, "units": 2145, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 28494, "known": 0.9065, "lines": 814,
                            "intact": 371, "units": 461, "shape": "rows"},
        "mounttype.dat": {"blocks": 36801, "known": 1.0, "lines": 25639,
                          "intact": 25639, "units": 1823,
                          "shape": "sections"},
    }
    #: MEASURED, `block96_census(Clients/7217)` -- REPLACES 7205's dict in
    #: the same shape. 7205 (control, same run): 101 / 809 unknown / 99 full
    #: / 0 partial / 2 zero, PASS against its pin. **7217 has 16 PARTIAL
    #: tables where 7205 has none** -- see `PARTIAL_TABLES`. The two
    #: 7878-identity split keys were not measured for 7217 and are None.
    BLOCK96_CENSUS = {"files": 101, "blocks": 1504890, "unknown": 15028,
                      "fully_covered": 83, "partial": 16, "uncovered": 2,
                      "fully_covered_identical_to_7878": None,
                      "fully_covered_and_different": None}
    #: `(blocks known %, whole rows kept)` per partially-covered table on
    #: 7217, MEASURED. A KNOWN LIMIT of the 7878-derived dictionary, not a
    #: reader defect: every one of these decoded at 100% on 7205, and every
    #: one is among `CHANGED_FROM_7205`. Rows with an unknown block are
    #: dropped, never guessed, so these subjects serve FEWER rows than 7205.
    PARTIAL_TABLES = {
        "Achievement.dat": (96.29, 370), "AutoUseMagic.dat": (64.98, 10),
        "coat_storage_attr.dat": (58.86, 4),
        "coat_storage_type.dat": (63.19, 107),
        "exchange_shop_goods.dat": (79.04, 228),
        "hairface_storage_type.dat": (81.28, 137),
        "instance_enter_condition.dat": (86.55, 139),
        "instancetype.dat": (81.31, 117), "ItemtypeSub.dat": (90.65, 371),
        "MagicType.dat": (96.60, 2115), "monster.dat": (96.43, 2123),
        "newslot_type.dat": (95.31, 109), "Operating_Prize.dat": (87.75, 373),
        "prof_lev_benefit.dat": (94.34, 513), "prof_lev_up.dat": (68.13, 199),
        "title_type.dat": (53.47, 36),
    }
    #: 7205's browse before/after counts. NOT MEASURED for 7217.
    BROWSE_IMPACT = None
    #: `npc.ini` grew 2,913 -> 2,956 section headers (MEASURED, header
    #: count only); resolution against the frozen `3DSimpleObj.dbc` is NOT
    #: MEASURED, so 7205's 447-unresolved figure is not carried.
    #: (`tools/comod.py` reads this with a truthiness guard; None is safe.)
    NPC_COVERAGE = None
    #: MEASURED by `script_surface()`: Lua 224 -> 238, DLL 37 -> 37.
    #: Lua bytes and exported globals are NOT measured for 7217.
    SCRIPT_SURFACE = {"lua": 238, "dll": 37, "lua_bytes": None,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37}}

    #: Both at 0 known blocks, MEASURED. `levexp.dat` is byte-identical to
    #: 7205's, so 7205's reason carries by identity. `ServerPlay.dat` is a
    #: different file (5,696 -> 5,728 B) and is also 0 of 477 blocks.
    UNCOVERED_TABLES = {
        "levexp.dat": Patch7205.UNCOVERED_TABLES["levexp.dat"],
        "ServerPlay.dat": (
            "a DIFFERENT file from 7205's (5,696 -> 5,728 B); MEASURED 0 of "
            "477 blocks known on 7217, as 7205's copy was 0 of 474. No "
            "route opens either."),
    }

    #: `c3.wdf` and `data.wdf` MEASURED sha256-identical to 7205's
    #: (`ab68f57cc24ae100`, `fc628e4adeb7de48`), so the art layer is 7205's.
    ARCHIVES_IDENTICAL_TO_7205 = ("c3.wdf", "data.wdf")

    #: The twelve `.dbc`, MEASURED sha256-identical to 7205's here (and so to
    #: 6609's). Re-stated rather than inherited so the claim names the build
    #: it was checked on.
    FROZEN_COMPILED = Patch7205.FROZEN_COMPILED

    #: The `.dat` MEASURED byte-identical to 7205's that carry an inherited
    #: claim: the cipher agreement with 6868 (`ITEM_COLUMN_AGREEMENT`), the
    #: runeeffect repeat count, and the levexp refusal. Identity of bytes is
    #: the ONLY reason those three inherited constants are left in force.
    IDENTICAL_TO_7205 = ("itemtype.dat", "runeeffect.dat", "levexp.dat",
                         "Action.dat")

    #: The 24 top-level `.dat` whose size moved from 7205, each classified
    #: the same family on both builds (MEASURED, `inidat.classify`). Their
    #: declared grammar and columns are INFERRED from 7205.
    CHANGED_FROM_7205 = (
        "Achievement.dat", "AutoUseMagic.dat", "coat_storage_attr.dat",
        "coat_storage_type.dat", "exchange_shop_goods.dat", "GameMap.dat",
        "hairface_storage_type.dat", "instance_enter_condition.dat",
        "instancetype.dat", "item_refine_effect_ex.dat",
        "item_value_type.dat", "ItemtypeSub.dat", "MagicType.dat",
        "marketing_active.dat", "monster.dat", "mounttype.dat",
        "newslot_broadcast.dat", "newslot_type.dat", "Operating_Prize.dat",
        "prof_lev_benefit.dat", "prof_lev_up.dat", "quickpay.dat",
        "ServerPlay.dat", "title_type.dat")

    def table_quirks(self):
        """7205's quirks, with the one thing a reader must know first: their
        FIGURES are 7205's. The mechanisms (block96, record-vs-block
        coverage, frozen `.dbc`, re-decode controls) are measured to apply
        here -- same codecs, identical `.dbc` -- and the numbers inside them
        are not re-measured on this install yet."""
        q = super().table_quirks()
        q["the figures in the quirks above are 7205's"] = (
            "Every number quoted in the inherited quirks was measured on "
            "Clients/7205. 7217 shares the mechanism (same .dbc bytes, same "
            "block96 codec on every declared .dat) and 123 of 147 .dat "
            "byte-for-byte, itemtype.dat included -- but 24 tables changed "
            "and 16 of them are only PARTLY covered by the dictionary here "
            "(99.0% of blocks overall). PARTIAL_TABLES and BLOCK96_CENSUS "
            "carry 7217's own numbers.")
        return q

    def colour_provenance(self):
        """Two patches further from the afternoon than 7205's own answer."""
        return ("colourway inherited from 6090 via 6609 and 7205 "
                "(unverified on any of them)", "inferred")


PLUGIN = Patch7217()
