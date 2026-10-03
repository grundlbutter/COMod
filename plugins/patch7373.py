#!/usr/bin/env python3
r"""
patch7373 -- official Conquer Online patch 7373: 7205's content layer inside
a `.tpd`-only install with no compiled tables (wave B, after 7280/7320/7336).

**The archive switch is at 7275 -> 7280**: 7275 is the LAST client that ships
`c3.wdf` / `data.wdf` and twelve `ini/*.dbc`; 7280 is the FIRST that ships
only `.tpd`/`.tpi` pairs and **zero** `.dbc`. The `.dbc` set goes from 12 to
0 at that same step. 7373 is well inside the `.tpd` side. Listing read
2026-09-19 (directory listings and sizes only; no archive was opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7275   7275          2      0/0          12         502           150
    7280   7280          0      2/2           0         484           150
    7336   7336          0      2/2           0         509           160
    7373   7373          0      2/2           0         516           161
    7387   7387          0      2/2           0         516           161

    7373/c3.tpd    1,076,058,840 B    7373/c3.tpi    3,306,363 B
    7373/data.tpd    896,279,080 B    7373/data.tpi  5,724,770 B

`version.dat` is the six bytes `7373\r\n`. MEASURED (`measure_7373.py`,
2026-09-19): `c3.tpi` and `data.tpi` are byte-identical (full blake2b) to
7280's, 7320's, 7336's and 7387's; CONTROL: the same hash says 7589 and
7878's `c3.tpi` are identical, as known. The `.tpd` payloads match those
four on size plus head and tail 16 MB each: SAMPLED, not a full hash. So
`sharedArchives` stays False. `AssetRoot._discover_archives(7373)` is
`['c3.tpi', 'data.tpi']`, while the same call on 7275 returns the WDF pair.

WHY THE BASE IS `Patch7205` (as wave A's 7280 / 7320 / 7336 decided)
---------------------------------------------------------------------
  * **`Patch7878` must not be the base.** Its `derived_tables(root)` finds
    `<root>/../../derived/7878-dat-decrypted` for ANY root under `Clients/`,
    so a 7373 subclass would serve 7878's decrypted `itemtype` / `monster` /
    `mounttype` as this client's rows -- real files, passing controls, the
    wrong build. Silently wrong.
  * **`Patch7205`** fits the content layer: `ini/*.dat` specs in the
    `block96` codec, decoded from THIS client's ciphertext through the
    collection's block dictionary. Every file `SPECS_7205` (23) and
    `SPECS_7205_CENSUSED` (85) names is on disk here (listing: none absent),
    and all 13 `UNDECLARED_DAT` names are present. 137 of 6609's 146
    borrowed `.ini` specs are present (7205: 144); the nine absent are
    `Action3DEffect1..5.ini`, `StrRes.ini`, `emoneyshop.ini`, `GameInfo.ini`,
    `GameSetup.Ini`.

The two art-layer hooks that name `.dbc` files are overridden, because this
install ships none (the same overrides as 7280 / 7320 / 7336):

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
    names `3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc`, `3dmotion.dbc`;
    none is on disk (`tests/test_patch7373.TheArtProfileNamesFilesThatExist`
    is the arm that fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False.
  * `import_plan()` names the `c3.tpi` / `data.tpi` indices (as 7280 does;
    `assetdiff._iter_archive` opens a `.tpi` through `TpdArchive`), and
    drops 6090's "shared archives skipped, compiled .dbc tables read" note,
    which is false here on both counts.

KEPT from the 6090 lineage, and on what evidence:

  * **Four-wide `Action3DEffect` action field**: MEASURED on 7373. 2,851 keys
    in `Action3DEffect.ini`; shapes (3,4,3,3) x1,865, (4,4,3,3) x777,
    (1,4,3,1) x180, (3,4,1,3) x24. Action four wide, as on 6090/7205/7336;
    `PlaintextFamily`'s three would be wrong. CONTROL: the same count gives
    7205's recorded 2,328 keys / 1,900 at action 9999 exactly.
  * **`aura_convention() == "table"`**: MEASURED. 2,414 of 2,851 keys carry
    action `9999` (7336: 2,239 of 2,674).
  * **`ROW_COLUMNS`**: RE-MEASURED. `region.ini` 304 rows, 14 fields, column
    6 non-numeric on 304/304 (85 distinct) -- and byte-identical to 7336's
    (sha256 `318a8e4f3494`). `EventTypeName.ini`, `VipTrans.ini`,
    `restrain.ini` are byte-identical to 7205's.
  * `texture_for_mesh`, colour sets, sockets: INHERITED, NOT MEASURED HERE.

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 decoys, nothing behind them.** sha256
prefixes, identical to 7205's, 7336's, 7878's and `patch7878.FROZEN_LOOKUPS`:

    ini/3DSimpleObj.ini f34f56332383   ini/3dobj.ini    9202de93aa03
    ini/3dtexture.ini   6be2fece83fd   ini/3dmotion.ini 9951727aa1ed
    ini/armor.ini       8e64691e0f00

    build  npc.ini SimpleObjID rows  resolve via 3DSimpleObj.ini  unresolved
    7205         2,913                 1,600  (54.9%)               1,313
    7336         3,099                 1,723  (55.6%)               1,376
    7373         3,158                 1,762  (55.8%)               1,396

POSITIVE CONTROL: the same count reproduces `patch7205.NPC_COVERAGE
["resolved_ini_decoy"]` (1,600) and 7336's recorded 1,723 exactly. On 7373
the decoy is the only answer; 1,396 rows name a simple object it lacks, and
an unresolved row looks exactly like an npc with no art.

**`levexp.dat` is byte-identical to 7205's** (4,030 B, `4482cfaaec7d`), so
7205's refusal text carries by identity. **`ServerPlay.dat` is a third
different file** (5,894 B, `d3ad238f0063`; 7336 5,860 B, 7205 5,696 B).

**`ItemtypeSub.dat` is 364,014 B** -- a normal size between 7320's 248,518
and 7878's 464,778. 7336's 381-byte copy is 7336's anomaly, not a trend.
`itemtype.dat` is 14,136,592 B, the same SIZE as 7336's (not hashed).

**`ini/` against 7336, by name:** nothing gone; new are `renown.dat`
(UNDECLARED, not decoded) and six `.lua` (`climbdragonhill`, `equipeffsys`,
`familymatchbattlemsg`, `familyrace`, `multiplayergame`, `singleemotion`).
Against 7205: the 12 `.dbc`, `action3deffect1..5.ini`, `emoneyshop.ini`,
`strres.ini` gone; 14 `.dat` new (`NEW_DAT_SINCE_7205`).

**Script surface**: 340 `.lua` (13,942,981 B) and 27 root DLLs. The same
walk reproduces 7205's recorded 224 / 19,094,816 / 37 exactly (control).

**Encoding.** `codepage.ini` reads `1256`, as on 7205 and 7878.
`ini/SlotNpc.ini` is byte-identical to 7878's (sha256 `c4e23de07332`),
whose content 7878 MEASURED as GBK, so that file is GBK by identity (and it
is identical on 7205 too). `ini/npc.ini` (plaintext) has 92 runs of high
bytes and all 92 are even-length GB2312 pairs -- GBK-shaped. The same test
scores 1.0 on 7878's SlotNpc.ini, 0.14 on seeded random bytes and 0.38 on
synthetic cp1256 Arabic, so it can say "not GBK". `TEXT_ENCODING` stays
7205's latin1: INHERITED, NOT MEASURED on the decoded `.dat` text, which
`measure_7373.py` section 5 tests with the same instrument. **MEASURED: the
decoded text is GBK-shaped on 7373 AND on 7205.** 7373 `itemtype.dat` 7/7
high-byte runs GBK (6/6 high lines strict-GBK), `monster.dat` 70/70 (70/70).
7205 gives `itemtype.dat` 24/24 and `monster.dat` 95/95. The catalog shows
what latin1 does to it: `raiderconfig`'s control prints GBK as latin1
mojibake. This is a LINEAGE finding (7205 has it too), not something 7373
changed, so `TEXT_ENCODING` is left latin1 here and the finding goes to the
owner of `Patch7205`. Flipping only 7373 would make two builds that share
bytes decode differently.

MEASURED BY `measure_7373.py` (EXIT 0, 36 s, 2026-09-19, head 5ea89182)
------------------------------------------------------------------------
**Positive controls first, both PASS:** the 7205 coverage instrument
reproduces `Patch7205.BLOCK96_COVERAGE` on all five pinned tables, 7205
detects as `patch7205` at 0.95, and `block96_census(7205)` reproduces
`Patch7205.BLOCK96_CENSUS`.

**Detection over all 48 installs (every `Clients/` folder plus the live CCO
install):** only 7373 changes winner, from `plaintext` 0.50 to `patch7373`
0.95 (margin 0.45). `patch7373` scores 0.00 on every other install. 7280,
7320, 7336, 7387 and later still fall to `plaintext` 0.50 on this base.

**Every `ini/**.dat` on 7373, by `inidat` family:** block96 219, empty 10,
rar-mangled 11, rsa-mysqldump 10, plaintext 7, binary-plain 6, tq-stream 3,
unknown 3, shift-obfuscated 1, block96-candidate-refuted 1. No declared
spec's codec disagrees with `inidat` (0 CODEC MISMATCH). **block96 on 7373:
1,858,111 blocks, 126,465 unknown -> 93.19% known; 127 files fully covered,
91 partial, 1 at zero** (`levexp.dat`, by design). 7205, same dictionary:
99 full, 0 partial.

**THE DICTIONARY IS SHORT ON 7373, AND IT COSTS WHOLE RECORDS** (a FINDING,
recorded as a known limit, not tuned away). `COVERAGE_7373`:

    7373 table        known    lines   intact  units     7205 units
    itemtype.dat      93.61%   38,430  22,621  25,847    44,684
    ItemtypeSub.dat   89.76%      728     111     190       633
    MagicType.dat     92.83%    2,405   1,364   1,477     2,907
    monster.dat       95.68%  101,933  98,691   1,933     3,709 (sections)
    mounttype.dat    100.00%   26,297  26,297   1,870     1,819 (sections)
    award_config.dat  28.62%        3       0       0        72

`catalogs()` on 7373: 240 subjects, 237 with a control (7205: 247 / 245).
Refused: `action` (as on 7205), `levexp` (as on 7205), and **`award_config`,
lost: 28.6% of blocks known, not one whole record** (7205 reads 72).
`item` 22,621 (noise ceiling `itemtype.dat`/100 = 141,365: not the cipher-
noise shape). Other large drops against 7205 are the same short dictionary:
`magic` 1,364 / 2,907, `monster` 1,628 / 3,670, `map:dest` 279 / 936,
`item:sub` 111 / 633, `gamemapex` 104 / 287, `coat_storage_type` 12 / 765,
`battlepass_score_reward` 6 / 150, `combat_gear` 3 / 150. `mount` is 1,870
(7205 1,819) and `gamemap` 642 (7205 578), both fully read. A row count
here is a floor on the client's table, not its size.

**Appearance tables (`part_tables`)** read the frozen `.ini`, no twin: armet
1,168, armor 955, weapon 4,828, **Mount.ini 1** (7205 via `.dbc` twins:
2,793 / 3,754 / 13,292 / 1,223). The mount slot is effectively empty here.

`renown.dat` (new since 7336) is 32 B of block96, 100% known, one row.

**`DAT_CENSUS_ERA = False`, although the cipher qualifies.** 219 files are
block96 and the four content tables are among them, but adding "7373" to
`tools/dat_census.py` `BUILDS` turns `tests/test_dat_census.py` red:
`TheVerdictIsPerBuild.test_a_file_on_one_build_is_reported_on_one_build`
pins `combat_gear.dat` to 7189 ALONE among the non-7205 census builds, and
7373 ships `combat_gear.dat` too (it got `['7189', '7373']`, run
2026-09-19 08:08Z). That test's expectations are not this plugin's to edit,
so 7373 stays out of the census (as 7320 and 7336 chose) until the census
test is made patch-aware. `BLOCK96_CENSUS` still carries 7373's OWN census
in 7205's shape; the two 7878-identity fields are None (not measured).
(The same run also reds on `['7217', '7250', '7275']` missing from BUILDS;
that is independent of 7373, which is not in that list.)

STILL NOT MEASURED: `ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`,
`BROWSE_IMPACT`, `RUNEEFFECT_REPEATS`, `FORMERLY_UNCOVERED` (None, never
7205's); a full hash of the `.tpd` payloads.

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
RESULT on 7373: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 13 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1644    59    59     0      0     0    0
    Name=:npc:NpcX.ini              903     4     4     0      0     0    0
    Name=:npc:npc.ini              3158    43    22    21      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                233     3     3     0      0     0    0
    achievement                     371     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    npc:NpcX.ini                    903     4     4     0      0     0    0
    npc:npc.ini                    3134    22    22     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      233     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 18: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 840: `a1 a1` = '\u3000'

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

from plugins.patch7205 import Patch7205                # noqa: E402

#: The stamp this plugin owns, and the only thing `confidence` reads.
STAMP = "7373"

#: Inherited quirks that are FALSE on this install and are dropped. Most name
#: a `.dbc`, and this install ships none; the last carries 7205's script
#: counts. A quirk served by `/api/plugins` is read as a fact about the client.
DBC_ERA_QUIRKS = (
    "stale ini decoys",                  # 6090: "the client reads the twin"
    "a second motion reader",            # 6090: 3dmotion.dbc twin
    "unpadded ids in compiled tables",   # 6090: .dbc integer sections
    "u32-wrapped motion ids",            # 6090: keyed in 3dmotion.dbc
    "three fewer compiled tables",       # 6609: 12 .dbc vs 15
    "mount.dbc shrank",                  # 6609
    "the compiled tables froze at 6609",  # 7205
    "block coverage is not record coverage",  # 7205's itemtype numbers
    "the per-weapon motion aliases are gone",  # 6090: about 3dmotion.dbc
    "224 Lua scripts and no Lua grammar",      # 7205's counts
)


def _version(root: Path) -> str:
    """`version.dat`: `b'7373\\r\\n'` on this install (read 2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7373(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1644, 59, 59, 0],
        'Name=:npc:NpcX.ini': [903, 4, 4, 0],
        'Name=:npc:npc.ini': [3158, 43, 22, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [233, 3, 3, 0],
        'achievement': [371, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'npc:NpcX.ini': [903, 4, 4, 0],
        'npc:npc.ini': [3134, 22, 22, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [233, 3, 3, 0],
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

    name = "patch7373"
    label = "Official patch client 7373"
    origin = "official"
    aliases = ("7373",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables. The archive switch is at 7275 -> 7280, and the .dbc "
        "set went from 12 to 0 at the same step. Content tables are 7205's: "
        "ini/*.dat in the 96-bit block cipher, read through the collection's "
        "block dictionary with 7205's specs. The art lookups are the frozen "
        "2008 plaintext decoys, sha-identical to 7205's and 7878's, with no "
        "compiled twin: 1,762 of 3,158 npc.ini rows (55.8%) resolve a simple "
        "object and 1,396 do not, and nothing raises for those. The block "
        "dictionary knows 93.19% of this build's blocks (7205: ~100%), so "
        "whole records are lost: 22,621 item rows against 7205's 44,684, "
        "and award_config reads nothing. Counts here are floors."
    )

    # -- what the listing measured -----------------------------------------
    #: `.tpd` payload + `.tpi` index pairs, with sizes. No `.wdf`. Same sizes
    #: as 7280/7320/7336, NOT hashed.
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: The WDF-to-TPD bracket, from listings of the neighbours.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: `ini/*.dbc` -> 0.
    FROZEN_COMPILED = ()

    #: The plaintext lookups, live here only because nothing else exists.
    #: sha256 prefixes, identical to `patch7878.FROZEN_LOOKUPS` and 7205's.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: `npc.ini` `SimpleObjID=` rows against the frozen `3DSimpleObj.ini`.
    #: The instrument reproduces 7205's 1,600 and 7336's 1,723 exactly.
    NPC_COVERAGE = {"rows": 3158, "unique_ids": 424, "resolved_ini": 1762,
                    "unresolved": 1396, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    #: `Action3DEffect.ini` key census. Control: 7205 -> 2,328 / 1,900.
    ACTION3DEFFECT_KEYS = {"keys": 2851, "action_9999": 2414,
                           "shapes": {(3, 4, 3, 3): 1865, (4, 4, 3, 3): 777,
                                      (1, 4, 3, 1): 180, (3, 4, 1, 3): 24}}

    #: `.lua` under the install and root DLLs. The same walk reproduces
    #: 7205's `SCRIPT_SURFACE`. Globals were not counted.
    SCRIPT_SURFACE = {"lua": 340, "dll": 27, "lua_bytes": 13942981,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: `.dat` in 7373's `ini/` that 7205 does not ship. UNDECLARED: none has
    #: been decoded, and a spec is a promise to parse. `renown.dat` is new
    #: since 7336.
    NEW_DAT_SINCE_7205 = (
        "coat_color_rule.dat", "cop_task_type.dat", "cq_remote_prize_cfg.dat",
        "daoqi_dict_type.dat", "emoneyshopv2.dat", "month_card_type.dat",
        "new_shop_goods.dat", "new_shop_info.dat",
        "newslot_guanqia_cfg.dat", "renown.dat", "speffect_attribute.dat",
        "vendue_item.dat", "vendue_item_type.dat", "vendue_type.dat",
    )

    #: `ini/` entries 7205 ships and 7373 does not (besides the 12 `.dbc`).
    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    #: `ini/` entries new since 7336 (nothing is gone since 7336).
    NEW_SINCE_7336 = (
        "climbdragonhill.lua", "equipeffsys.lua", "familymatchbattlemsg.lua",
        "familyrace.lua", "multiplayergame.lua", "renown.dat",
        "singleemotion.lua",
    )

    ITEMTYPESUB_BYTES = 364014

    # -- measure_7373.py, 2026-09-19 ----------------------------------------
    #: False although 219 `ini/**.dat` are block96: joining
    #: `dat_census.BUILDS` reds `test_dat_census`'s combat_gear.dat-on-7189-
    #: only pin (7373 ships it too). See the docstring. Literal False.
    DAT_CENSUS_ERA = False
    #: 7373's OWN census, in `Patch7205.BLOCK96_CENSUS`'s shape, so 7205's is
    #: never served as this build's. 7878-identity split NOT measured: None.
    BLOCK96_CENSUS = {"files": 219, "blocks": 1858111, "unknown": 126465,
                      "fully_covered": 127, "partial": 91, "uncovered": 1,
                      "fully_covered_identical_to_7878": None,
                      "fully_covered_and_different": None}
    #: Per-table coverage on 7373. `known` is the printed percentage;
    #: per-table block totals were not printed, so this is NOT
    #: `BLOCK96_COVERAGE`'s shape and that attribute stays None.
    COVERAGE_7373 = {
        "itemtype.dat": {"known": 93.61, "lines": 38430, "intact": 22621,
                         "units": 25847, "shape": "rows"},
        "itemtypesub.dat": {"known": 89.76, "lines": 728, "intact": 111,
                            "units": 190, "shape": "rows"},
        "magictype.dat": {"known": 92.83, "lines": 2405, "intact": 1364,
                          "units": 1477, "shape": "rows"},
        "monster.dat": {"known": 95.68, "lines": 101933, "intact": 98691,
                        "units": 1933, "shape": "sections"},
        "mounttype.dat": {"known": 100.0, "lines": 26297, "intact": 26297,
                          "units": 1870, "shape": "sections"},
        "award_config.dat": {"known": 28.62, "lines": 3, "intact": 0,
                             "units": 0, "shape": "rows"},
    }
    #: `catalogs()` rows on 7373 (and 7205's, same run).
    CATALOG_ROWS = {"item": (22621, 44684), "item:sub": (111, 633),
                    "magic": (1364, 2907), "monster": (1628, 3670),
                    "mount": (1870, 1819), "map:dest": (279, 936),
                    "gamemap": (642, 578)}
    #: Catalog subjects that refuse on 7373 and why.
    CATALOG_REFUSED = {
        "action": "binary record layout (refuses on 7205 too)",
        "levexp": "held out of the dictionary by design (7205 too)",
        "award_config": "28.6% of blocks known, not one whole record; "
                        "7205 reads 72 -- LOST on 7373",
    }
    BLOCK96_COVERAGE = None
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's, so 7205's finding carries by identity: held "
                      "out of the dictionary by design. 0.00% known.",
        "award_config.dat": "MEASURED on 7373: 3,654 B, 28.62% of blocks "
                            "known, 0 whole records. 7205 reads 72 rows.",
    }
    #: 7205's refused names, all on disk here; the reasons are 7205's.
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7373] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7373` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. Formats cannot separate 7373 from
        7336/7387 (same `.tpd` sizes, no `.dbc`, same frozen lookups; 7387
        even has the same `ini/` entry counts), so only the stamp is read.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`: every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install."""
        return False

    def import_plan(self, root, exists) -> dict:
        """The `.tpi` indices of the `.tpd` pair. The `.tpi` files are
        byte-identical to 7280/7320/7336/7387's; the `.tpd` payloads were
        only sampled, so `sharedArchives` stays False."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": [f"{s}.tpi" for s in self.ARCHIVE_PAIRS],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: .tpd archives (no .wdf), no compiled "
                     f".dbc -- the plaintext lookup chain is the only one, "
                     f"and ini/*.dat is block96"),
        })
        return plan

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7373's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones (each kept one marked as not
        re-measured here), plus 7373's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7373] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7373: Action3DEffect keys are (3,4,3,3) x1,865, "
            "(4,4,3,3) x777, (1,4,3,1) x180 -- the action field is four wide "
            "as on 6090 and 7205, not three as in the plaintext family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,158 SimpleObjID rows; 1,762 resolve a simple object and 1,396 "
            "do not. Nothing raises for an unresolved row.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear.")
        q["340 Lua scripts and no Lua grammar"] = (
            "340 .lua (13,942,981 B) and 27 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables.")
        return q


PLUGIN = Patch7373()
