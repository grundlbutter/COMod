#!/usr/bin/env python3
r"""
patch7562 -- official Conquer Online patch 7562: 7205's content layer inside
a `.tpd`-only install with no compiled tables.

7562 is a client of the `.tpd`-only era (7280, 7320, 7336, 7373, 7387, 7506,
7535, **7562**, 7589, ...). **The archive switch is at 7275 -> 7280**: 7275
is the last client that ships `c3.wdf` / `data.wdf` and twelve `ini/*.dbc`;
7280 is the first that ships only `.tpd`/`.tpi` pairs and **zero** `.dbc`.
7562 is well inside the `.tpd` side. The listing, read 2026-09-19 (a
directory listing only; no archive was opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7336   7336          0      2/2           0         509           160
    7535   7535          0      2/2           0         531           165
    7562   7562          0      2/2           0         534           165
    7589   7589          0      2/2           0         539           165

    7562/c3.tpd    1,076,058,840 B    7562/c3.tpi    3,306,363 B
    7562/data.tpd    896,279,080 B    7562/data.tpi  5,724,770 B

(`ini/*.dat` counted case-insensitively.) **The two `.tpi` INDEXES are
byte-identical** (sha256 `4b4bde688f18` / `9096a9e0ed08`) to 7336's and
7589's, and `c3.tpi` to 7878's too. The `.tpd` payloads have the same sizes
as 7336's and 7589's but have NOT been hashed (1 GB each; that is in
`measure_7562.py`), so this plugin does not say the payloads are shared.

WHY THE BASE IS `Patch7205`, AND WHAT IS OVERRIDDEN
----------------------------------------------------
Wave A (7280 `8d610c4a`, 7320 `7fe52c2d`, 7336 `534734a6`) settled this and
it is not re-derived here, only re-checked against 7562's files:

  * **Not `Patch7878`.** `patch7878.derived_tables(root)` finds
    `<root>/../../derived/7878-dat-decrypted` for ANY root under `Clients/`,
    so a subclass would serve **7878's** decrypted tables as 7562's rows.
    Nothing would raise and every control would pass on real files.
  * **`Patch7205`** fits the content layer: its `ini/*.dat` specs decode a
    table from this client's own ciphertext through `core/block96.py`.
    Every file named by `SPECS_7205` (23) and `SPECS_7205_CENSUSED` (85) is
    on disk here, checked by listing at this base (`8c7b1e4a`).
  * It subclasses `Patch7205` directly, not the wave-A `Patch7336`, because
    that plugin is on an unmerged branch.

The three art-layer hooks that name `.dbc` files are overridden, exactly as
wave A did, because this install ships none:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
    names `3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc`, `3dmotion.dbc`,
    none on disk (`tests/test_patch7562.TheArtProfileNamesFilesThatExist` is
    the arm that fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False. There is no twin.
  * `import_plan()` names the `.tpd` pair and drops 6090's "shared archives
    skipped, compiled .dbc tables read" note, false here on both counts.

**`DAT_CENSUS_ERA = False`, although the cipher IS block96.** MEASURED
(`measure_7562.py`, 2026-09-19): 365 of 419 `ini/**/*.dat` on 7562 are
block96 and the dictionary knows 98.33% of their 2,195,969 blocks (7205
control PASS). But with "7562" added to `tools/dat_census.py` `BUILDS`,
`tests.test_dat_census.TheVerdictIsPerBuild.test_a_file_on_one_build_is_
reported_on_one_build` goes RED: it pins `combat_gear.dat` to 7189 only,
and 7562 ships it too (93,653 B, 100% known, 822 rows) -> `['7189',
'7562'] != ['7189']`. That test's expectations are not ours to edit, so
the slot stays commented and the flag False until the census owner
re-pins that fact. (The same run's other red, 7217/7250/7275 missing from
`BUILDS`, is not caused by 7562.) `BLOCK96_CENSUS` is set to None here:
on Patch7205 it is 7205's own census dict, and inherited unchanged it would
present 7205's numbers as 7562's (SD, W3 review). 7562's census figures are
`DAT_CENSUS_7562`.

What is KEPT from the 6090 lineage, and on what evidence (2026-09-19):

  * **Four-wide `Action3DEffect` action field**: MEASURED on 7562. Key
    shapes (split on `.`) are (3,4,3,3) x2,052, (4,4,3,3) x881, (1,4,3,1)
    x201, (3,4,1,3) x24, of 3,167 keys. CONTROL: the same count reproduces
    7336's recorded 1,767 / 712 / 166 / 24 exactly and 7205's (3,4,3,3)
    x1,535.
  * **`aura_convention() == "table"`**: MEASURED. 2,718 of 3,167 keys
    carry action `9999` (same code: 7336 2,239, 7205 1,900).
  * **`ROW_COLUMNS`**: RE-MEASURED. `region.ini` has 304 rows, 14 fields,
    column 6 non-numeric on 304/304 (85 distinct); byte-identical to 7336's
    (`318a8e4f3494`). `EventTypeName.ini`, `VipTrans.ini`, `restrain.ini`
    are byte-identical to 7205's.
  * `texture_for_mesh`, colour sets, sockets, `socket_correction`:
    INHERITED AND NOT MEASURED HERE. 6090 measurements, gated on `exists()`.

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 decoys.** sha256 prefixes, identical
to 7205's, 7336's, 7535's, 7589's and to `patch7878.FROZEN_LOOKUPS`:

    ini/3DSimpleObj.ini f34f56332383   ini/3dobj.ini    9202de93aa03
    ini/3dtexture.ini   6be2fece83fd   ini/3dmotion.ini 9951727aa1ed
    ini/armor.ini       8e64691e0f00

`npc.ini` kept growing against a table that did not:

    build  npc.ini rows  resolve via 3DSimpleObj.ini  unresolved
    7205        2,913          1,600  (54.9%)           1,313
    7336        3,099          1,723  (55.6%)           1,376
    7535        3,179          1,761  (55.4%)           1,418
    7562        3,260          1,802  (55.3%)           1,458
    7589        3,306          1,843  (55.7%)           1,463

(`SimpleObjID=N` -> `[ObjIDType<N>]`, one count per row. POSITIVE CONTROL:
the same code reproduces `patch7205.NPC_COVERAGE["resolved_ini_decoy"]`,
1,600, and 7336's recorded 1,723, exactly. On 7562 it is the only answer:
1,458 npc rows name a simple object the frozen table does not carry, and an
unresolved row looks exactly like an npc with no art.)

**`levexp.dat` is byte-identical to 7205's** (4,030 B, `4482cfaaec7d`), so
the `SPECS_7205` refusal text is true here too. **`ServerPlay.dat` is a
different file** (6,114 B, `9691a4329951`; 7205 5,696 B, 7336 5,860 B, 7535
6,015 B, 7589 6,144 B).

**`ItemtypeSub.dat` is 172,304 B** (7205: 207,838; 7535: 374,431; 7589:
407,962). The size swings non-monotonically across the whole era (0 B on
7189, 381 B on 7336), so it looks like a per-patch delta table, not a
cumulative one (INFERRED). See the measured `item:sub` below.

**`SlotNpc.ini` is byte-identical** (`c4e23de07332`, 3,959 B) on 7205, 7562
and 7878; it is the file whose GBK text was measured on 7878. `codepage.ini`
reads `1256`, as on 7205 and 7878. `TEXT_ENCODING` stays 7205's latin1;
see the ENCODING finding below.

**`ini/` differs from 7205's by name:**

    gone since 7205   the 12 .dbc; action3deffect1..5.ini, emoneyshop.ini,
                      strres.ini
    new since 7205    18 .dat (NEW_DAT_SINCE_7205, UNDECLARED -- not yet
                      decoded: 7336's 13 plus dict_collection, dict_lottery,
                      renown, vassal_war_agent, vassal_war_soldier);
                      actionrole3deffect.ini, actionsoundex.ini,
                      appqdkey.ini, cq_remote_prize_cfg.ini, operateweben.ini,
                      shaderparam.ini; ~60 .lua and luagui/, luaui/
    since 7535        + flowerrankshow.lua, innerwebdata.lua,
                      statuseffect.lua; nothing removed
    at 7589           + 5 .lua; nothing removed

**Script surface**: 413 `.lua` (16,102,639 B) and 28 root DLLs, against
7205's 224 (19,094,816 B) and 37 and 7336's 321 (13,905,069 B) and 27. The
same `find` reproduces 7205's recorded `SCRIPT_SURFACE` exactly (control).

WHAT measure_7562.py MEASURED (run by the Director, 2026-09-19, 27 s)
---------------------------------------------------------------------
**Positive controls first, both PASS:** on 7205 the coverage instrument
reproduces `Patch7205.BLOCK96_COVERAGE` exactly for itemtype / monster /
magictype / itemtypesub / mounttype, and `Patch7205` scores 7205 at 0.95.

**Detection over all 48 installs** (every `Clients/` folder, numeric and
named, plus `C:/Program Files/Classic Conquer 2.0`): the ONLY winner that
changes is 7562 (`plaintext` 0.50 -> `patch7562` 0.95, margin 0.45), and
`patch7562` scores 0.00 on every other install. The other `.tpd`-era
clients without a plugin of their own on this base (7280 .. 7589, 7632,
7682, 7867) read as `plaintext` 0.50 in both columns.

**Containers:** `_discover_archives` = `['c3.tpi', 'data.tpi']` on 7562
(7275: `['c3.wdf', 'data.wdf']`, the control). Both `.tpi` are IDENTICAL
to 7336's, 7535's and 7589's; both `.tpd` are equal in size and in their
first and last 16 MB to the same three (SAMPLED, not a full hash, so
`sharedArchives` stays False).

**Appearance tables (`part_tables()`):** 7562 reads the frozen `.ini`,
no twin: armet 1,168, body 955, weapon 4,828, **mount 1**, head 0, misc 0.
7205 reads `.dbc` twins: armet 2,793, body 3,754, weapon 13,292, mount
1,223. **FINDING:** the art layer on 7562 sees about a third of 7205's
appearances, and `Mount.ini` holds one row. Nothing raises.

**`ini/**/*.dat` (419, including `luagui/` and `luaui/`):** block96 365,
rar-mangled 11, empty 10, binary-plain 6, tq-stream 3, the rest plaintext
and other. block96: 2,195,969 blocks, 36,582 unknown (**98.33% known**);
288 fully covered, 75 partial, 2 at zero (`levexp.dat`, by design, and
`ServerPlay.dat`, undeclared). Every declared spec's codec matches
`inidat.classify` (0 CODEC MISMATCH). Key tables on 7562:

    table            known    lines    intact   units   7205 units
    itemtype.dat     100.00%  50,834   50,834  50,834   44,684
    MagicType.dat     96.90%   3,847    3,341   3,401    2,907
    monster.dat       95.78% 106,487  103,267   2,053    3,709 (sections)
    mounttype.dat    100.00%  26,495   26,495   1,884    1,819 (sections)
    ItemtypeSub.dat   87.85%     332       54     117      633
    MapDestination    97.13%   8,627    8,502   1,089  (sections)

**`catalogs()`:** 240 subjects, 238 with a control (7205: 247 / 245).
`item` 50,834 (7205 44,684), `magic` 3,341 (2,907), `mount` 1,884 (1,819),
`map:dest` 1,067 (936), `region` 304 (303), `gamemap` 409 (578; the binary
parse consumes all 13,390 bytes exactly). **Counts BELOW 7205's, recorded
as known limits in `SHORT_OF_7205` and the test, not tuned away:**

  * **`monster` 1,749 against 7205's 3,670.** `monster.dat` GREW
    (1,422,919 B vs 1,237,358 B; 106,487 lines vs 96,045), but 4.22% of its
    blocks are unknown and an unknown block destroys a section header, so
    only 2,053 sections survive and 1,749 are served. INFERRED to be
    dictionary loss, not content; novel blocks need the `ndac.dll` oracle.
  * **`item:sub` 54 against 7205's 633.** 87.85% known, 54 of 332 lines
    intact.
  * `prof_lev_up` 199 (549), `texas_match_type` 8 (82),
    `battlepass_score_reward` 73 (150), `coat_storage_type` 429 (765),
    `magic:auto` 1 (15): partial block96 tables. `activity` 35 (112) is
    plaintext, so that one is content. `npc` is None on both builds.

**ENCODING:** on 7562 every decoded line with a high byte decodes as
strict GBK: itemtype 26/26, monster 77/77, `SlotNpc.ini` 4/4. The 7205
CONTROL reads the same (itemtype 21/21, monster 86/86). So if latin1 is
wrong, it is wrong for 7205 too, and it is not changed here. Strict GBK
succeeding on so few lines is CONSISTENT WITH GBK, not proof of it.

WHAT IS STILL NOT MEASURED
--------------------------
`FORMERLY_UNCOVERED`, `ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`,
`BROWSE_IMPACT`, `RUNEEFFECT_REPEATS` (all None here, never 7205's); a
full hash of the `.tpd` payloads; and the 18 new `.dat` as catalog
subjects. Most of those decode (daoqi_dict_type 100%, vassal_war_agent
100% / 12,800 rows, emoneyShopV2 98.59%), but a spec is a promise to parse
and none is declared. `BLOCK96_COVERAGE` stays None because the run did not
print per-table block counts in 7205's shape; `TABLE_RECOVERY` holds what
it did print.

`UNDECLARED_DAT` keeps 7205's thirteen names, all on disk here, each reason
marked as 7205's measurement.

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
RESULT on 7562: **0 strict-GBK failures** in 351 subjects/name
sources (2 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1764    66    65     0      1     0    0
    Name=:npc:NpcX.ini              965     3     3     0      0     0    0
    Name=:npc:npc.ini              3260    26     5    21      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      165    67    67     0      0     0    0
    Name=:statustips                265     3     3     0      0     0    0
    achievement                     388     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    item                          50832     2     0     1      0     1    0
    map:dest                       1067     7     6     0      0     1    0
    npc:NpcX.ini                    965     3     3     0      0     0    0
    npc:npc.ini                    3236     5     5     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            165    67    67     0      0     0    0
    statustips                      265     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 207: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 857: `a1 a1` = '\u3000'
    Name=:monster 1435: `54 68 a8 a6 6f 64 72 65 64` = 'Théodred'

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
STAMP = "7562"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded. Most name a `.dbc`, and this install ships none; the last one
#: carries 7205's script counts. Same list as wave A's 7336.
DBC_ERA_QUIRKS = (
    "stale ini decoys",
    "a second motion reader",
    "unpadded ids in compiled tables",
    "u32-wrapped motion ids",
    "three fewer compiled tables",
    "mount.dbc shrank",
    "the compiled tables froze at 6609",
    "block coverage is not record coverage",
    "the per-weapon motion aliases are gone",
    "224 Lua scripts and no Lua grammar",
)


def _version(root: Path) -> str:
    """`version.dat`, four bytes, `b'7562'` on this install (read
    2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7562(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1764, 66, 65, 0],
        'Name=:npc:NpcX.ini': [965, 3, 3, 0],
        'Name=:npc:npc.ini': [3260, 26, 5, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [165, 67, 67, 0],
        'Name=:statustips': [265, 3, 3, 0],
        'achievement': [388, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'item': [50832, 2, 0, 0],
        'map:dest': [1067, 7, 6, 0],
        'npc:NpcX.ini': [965, 3, 3, 0],
        'npc:npc.ini': [3236, 5, 5, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [165, 67, 67, 0],
        'statustips': [265, 3, 3, 0],
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

    name = "patch7562"
    label = "Official patch client 7562"
    origin = "official"
    aliases = ("7562",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables. The archive switch is at 7275 -> 7280, and the .dbc "
        "set went from 12 to 0 at the same step. Content tables are 7205's: "
        "ini/*.dat in the 96-bit block cipher, read through the collection's "
        "block dictionary with 7205's specs. The art lookups are the frozen "
        "2008 plaintext decoys with no compiled twin behind them: 1,802 of "
        "3,260 npc.ini rows (55.3%) resolve a simple object and 1,458 do "
        "not, and nothing raises for those. The .tpi indexes are "
        "byte-identical to 7336's, 7535's and 7589's. MEASURED: block96 "
        "98.33% of blocks known; item 50,834 rows; monster 1,749 and "
        "item:sub 54 fall below 7205's 3,670 and 633 (unknown blocks "
        "destroy headers); Mount.ini holds 1 appearance row."
    )

    # -- what the listing measured -----------------------------------------
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}
    #: sha256 prefixes of the INDEXES, identical on 7336 and 7589 (c3.tpi
    #: also on 7878). The payloads are NOT hashed.
    ARCHIVE_INDEX_SHA = {"c3.tpi": "4b4bde688f18", "data.tpi": "9096a9e0ed08"}

    #: The WDF-to-TPD bracket, from listings of the neighbours.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: `ls ini/*.dbc` -> 0.
    FROZEN_COMPILED = ()

    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    NPC_COVERAGE = {"rows": 3260, "unique": 3257, "resolved_ini": 1802,
                    "unresolved": 1458, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    SCRIPT_SURFACE = {"lua": 413, "dll": 28, "lua_bytes": 16102639,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: `.dat` in 7562's `ini/` that 7205 does not ship. UNDECLARED: none has
    #: been decoded, and a spec is a promise to parse.
    NEW_DAT_SINCE_7205 = (
        "coat_color_rule.dat", "cop_task_type.dat", "cq_remote_prize_cfg.dat",
        "daoqi_dict_type.dat", "dict_collection.dat", "dict_lottery.dat",
        "emoneyshopv2.dat", "month_card_type.dat", "new_shop_goods.dat",
        "new_shop_info.dat", "newslot_guanqia_cfg.dat", "renown.dat",
        "speffect_attribute.dat", "vassal_war_agent.dat",
        "vassal_war_soldier.dat", "vendue_item.dat", "vendue_item_type.dat",
        "vendue_type.dat",
    )

    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    ITEMTYPESUB_BYTES = 172304

    # -- 7205 measurements that are NOT this build's: pending -------------
    BLOCK96_COVERAGE = None
    #: MEASURED by measure_7562.py on 7562: (known, lines, intact, units).
    TABLE_RECOVERY = {
        "itemtype.dat": (1.0, 50834, 50834, 50834),
        "magictype.dat": (0.9690, 3847, 3341, 3401),
        "monster.dat": (0.9578, 106487, 103267, 2053),
        "mounttype.dat": (1.0, 26495, 26495, 1884),
        "itemtypesub.dat": (0.8785, 332, 54, 117),
        "mapdestination.dat": (0.9713, 8627, 8502, 1089),
    }
    #: The whole-install block96 census, MEASURED over ini/**/*.dat.
    DAT_CENSUS_7562 = {"files": 419, "block96": 365, "blocks": 2195969,
                       "unknown": 36582, "fully_covered": 288,
                       "partial": 75, "zero": 2}
    #: Catalog counts BELOW 7205's (7562, 7205): known limits, not defects
    #: to tune away. monster/item:sub lose rows to unknown blocks.
    SHORT_OF_7205 = {"monster": (1749, 3670), "item:sub": (54, 633),
                     "prof_lev_up": (199, 549), "texas_match_type": (8, 82)}
    #: False BY RULE, not by cipher: 365/419 ini dat ARE block96 (98.33% of
    #: blocks known), but adding "7562" to dat_census.BUILDS reds
    #: test_dat_census (combat_gear.dat is pinned to 7189 alone; 7562 ships
    #: it). See the module docstring.
    DAT_CENSUS_ERA = False
    #: NOT 7205's census. `BLOCK96_CENSUS` on Patch7205 is 7205's own
    #: measured census dict; inherited unchanged it would present 7205's
    #: numbers as 7562's to any generic reader (SD, W3 review). None =
    #: no census in 7205's shape is recorded for this class.
    BLOCK96_CENSUS = None
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's and 7878's, so 7205's finding carries by "
                      "identity: held out of the dictionary by design.",
    }
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7562] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7562` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. The formats cannot separate 7562
        from 7336/7535/7589 (identical `.tpi`, no `.dbc`, the same frozen
        lookups), so nothing but the stamp is read. MEASURED: without this
        plugin 7562 falls to `plaintext` at 0.50; with it, `patch7562` wins
        at 0.95 by 0.45 and scores 0.00 on the other 47 installs.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`: every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install to prefer."""
        return False

    def import_plan(self, root, exists) -> dict:
        """The `.tpd` pair, and no claim that it is shared. The indexes match
        7336/7589 by hash but the payloads are unhashed, so `sharedArchives`
        stays False until a hash says otherwise."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": ["c3.tpd", "data.tpd"],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: .tpd archives (no .wdf), no compiled "
                     f".dbc -- the plaintext lookup chain is the only one, "
                     f"and ini/*.dat is block96"),
        })
        return plan

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7562's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7562's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7562] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7562: Action3DEffect keys are (3,4,3,3) x2,052, "
            "(4,4,3,3) x881, (1,4,3,1) x201 -- the action field is four wide "
            "as on 6090 and 7205, not three as in the plaintext family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,260 rows; 1,802 resolve a simple object and 1,458 do not. "
            "Nothing raises for an unresolved row.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear. Both .tpi indexes are "
            "byte-identical to 7336's and 7589's.")
        q["413 Lua scripts and no Lua grammar"] = (
            "413 .lua (16,102,639 B) and 28 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables.")
        return q


PLUGIN = Patch7562()
