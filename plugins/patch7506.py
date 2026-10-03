#!/usr/bin/env python3
r"""
patch7506 -- official Conquer Online patch 7506: 7205's content layer inside
a `.tpd`-only install with no compiled tables.

7506 is in the `.tpd`-only era (7280, 7320, 7336, 7373, 7387, 7506, 7535,
...). **The archive switch is at 7275 -> 7280**: 7275 is the last client that
ships `c3.wdf` / `data.wdf` and twelve `ini/*.dbc`; 7280 is the first that
ships only `.tpd`/`.tpi` pairs and **zero** `.dbc`. The `.dbc` set goes from
12 to 0 at that same step (listings: wave A's `patch7280` docstring, and the
plan's inventory). 7506 is well inside the `.tpd` side. The listing, read
2026-09-19 (a directory listing only; no archive was opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7336   7336          0      2/2           0         509           160
    7387   7387          0      2/2           0         516           161
    7506   7506          0      2/2           0         523           163
    7535   7535          0      2/2           0         531           165

    7506/c3.tpd    1,076,058,840 B    7506/c3.tpi    3,306,363 B
    7506/data.tpd    896,279,080 B    7506/data.tpi  5,724,770 B

(`ini/*.dat` counted case-insensitively.) The four archive files have the
SAME SIZES as 7336's (and, per wave A, 7280's / 7320's / 7373's). Same size
is not the same bytes. They have NOT been hashed (that is in
`measure_7506.py`), so this plugin does not say they are shared.

WHY THE BASE IS `Patch7205`, AND WHAT IS OVERRIDDEN
----------------------------------------------------
The same decision wave A made for 7280 / 7320 / 7336, re-checked on this
install's files rather than copied:

  * **`Patch7878` (`PlaintextFamily`)** fits the art layer (no `.dbc`, a
    `.tpd` pair, the frozen plaintext lookups). It **must not be the base,
    because of its content layer**: `patch7878.derived_tables(root)` finds
    `<root>/../../derived/7878-dat-decrypted` for ANY root under `Clients/`,
    so a 7506 subclass would serve **7878's** decrypted tables as 7506's rows.
    Nothing would raise and every control would pass. Silently wrong.
  * **`Patch7205`** fits the content layer: its `ini/*.dat` specs use the
    `block96` codec, which decodes THIS client's own ciphertext through the
    collection's block dictionary. Checked by listing on 7506: all 23
    `SPECS_7205` files and all 85 `SPECS_7205_CENSUSED` files are on disk,
    none absent; 132 of 6609's 141 borrowed `.ini` specs are present
    (7205: 139); all 13 `Patch7205.UNDECLARED_DAT` names are present.

So the base is `Patch7205`, and the two art-layer hooks that name `.dbc`
files are overridden, because this install ships none:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`, not `PROFILE_OFFICIAL`,
    whose four lookup tables are all `.dbc` and none is on disk here
    (`tests/test_patch7506.TheArtProfileNamesFilesThatExist` is the arm that
    fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False. There is no twin to prefer.
  * `import_plan()` names the `.tpd` pair and drops 6090's "shared archives
    skipped, compiled .dbc tables read" note, false here on both counts.
  * `DAT_CENSUS_ERA = False`, set on this class, not inherited -- **even
    though the `.dat` ARE block96** (`measure_7506.py`: 261 block96 files,
    92.82% of blocks known, the 7205 census control reproducing). Adding
    `"7506"` to `tools/dat_census.BUILDS` REDS `tests/test_dat_census.py::
    TheVerdictIsPerBuild.test_a_file_on_one_build_is_reported_on_one_build`
    (run 2026-09-19 08:08Z): it pins `combat_gear.dat` to 7189 alone, and
    7506 ships `combat_gear.dat` too (`['7189', '7506'] != ['7189']`). That
    test's expectations belong to the census, not to this plugin, so the slot
    stays commented and the flag False until the census owner widens it.
  * `BLOCK96_CENSUS` is REPLACED by 7506's own measured census (same keys as
    7205's, minus the two 7878-identity keys, which were not measured here).

What is KEPT from the 6090 lineage, and on what evidence:

  * **Four-wide `Action3DEffect` action field** (`FIELD_WIDTHS`): MEASURED on
    7506. 2,954 keys; shapes (3,4,3,3) x1,924, (4,4,3,3) x803, (1,4,3,1)
    x194, (3,4,1,3) x24. The action field is 4 wide, as on 7205 (1,535 at
    (3,4,3,3)) and 7336. The `PlaintextFamily` value (3 wide) would be wrong.
  * **`aura_convention() == "table"`**: MEASURED. 2,517 of 2,954
    `Action3DEffect.ini` keys carry action `9999` (7205: 1,900 of 2,328; the
    same count reproduces 7336's 2,239).
  * **`ROW_COLUMNS`**: RE-MEASURED. `region.ini` 304 rows, 14 fields, column
    6 non-numeric on 304/304 (85 distinct); 7205 control 303/303, 84.
    `EventTypeName.ini`, `VipTrans.ini`, `restrain.ini` are byte-identical
    to 7205's (sha256).
  * `texture_for_mesh`, colour sets, sockets, `socket_correction`: INHERITED
    AND NOT MEASURED HERE. They are 6090 measurements, gated on `exists()`.

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 decoys, and nothing stands behind
them.** sha256 prefixes, identical to 7205's, 7336's and
`patch7878.FROZEN_LOOKUPS`:

    ini/3DSimpleObj.ini f34f56332383   ini/3dobj.ini    9202de93aa03
    ini/3dtexture.ini   6be2fece83fd   ini/3dmotion.ini 9951727aa1ed
    ini/armor.ini       8e64691e0f00

`npc.ini` (a different file from 7205's and 7336's) kept growing:

    build  npc.ini SimpleObjID rows  resolve via 3DSimpleObj.ini  unresolved
    7205         2,913                     1,600  (54.9%)           1,313
    7336         3,099                     1,723  (55.6%)           1,376
    7506         3,138                     1,748  (55.7%)           1,390

POSITIVE CONTROL: the same code reproduces
`patch7205.NPC_COVERAGE["resolved_ini_decoy"]` (1,600) and wave A's 7336
figure (1,723) exactly. On 7506, as on 7878, the decoy is the only answer:
1,390 npc rows name a simple object the frozen table does not carry, and an
unresolved row looks exactly like an npc with no art.

**`levexp.dat` is byte-identical to 7205's** (4,030 B, sha256
`4482cfaaec7d`), so the `SPECS_7205` refusal text is true here too.
**`ServerPlay.dat` is a different file** (5,953 B, `feedb89265b8`; 7205
5,696 B, 7336 5,860 B).

**`ItemtypeSub.dat` is 117,268 B.** Neighbours: 7205 207,838; 7336 381;
7387 463,165; 7535 374,431; 7878 464,778. Not monotonic, not decoded here;
an `item:sub` count on 7506 must not be compared with 7205's until
`measure_7506.py` has read it.

**`ini/` differs from 7205's by name** (case-insensitive):

    gone since 7205   the 12 .dbc; action3deffect1..5.ini, emoneyshop.ini,
                      strres.ini
    new since 7205    16 .dat (NEW_DAT_SINCE_7205, UNDECLARED -- not decoded);
                      6 .ini (actionrole3deffect, actionsoundex, appqdkey,
                      cq_remote_prize_cfg, operateweben, shaderparam); Lua
                      files and luagui/ luaui/ under ini/
    new since 7336    dict_collection.dat, dict_lottery.dat, renown.dat, and
                      11 .lua; nothing of 7336's is gone

**Script surface**: 369 `.lua` (14,625,583 B) and 27 root DLLs, against
7205's 224 (19,094,816 B) and 37. The same `find` reproduces 7205's recorded
224 / 19,094,816 / 37 exactly (the control). Globals were NOT counted.

**Launcher**: `Conquer.exe` is the 41-byte text stub; `play.exe` is present.

**Encoding.** `codepage.ini` reads `1256`, as on 7205 and 7878.
`SlotNpc.ini` is byte-identical (3,959 B, `c4e23de07332`) on 7205, 7506 and
7878, so 7878's GBK reading of that file carries by identity. That file is a
comment-bearing plaintext `.ini`, not a content table, so it does NOT settle
the encoding of the block96 tables' names. `TEXT_ENCODING` stays 7205's
latin1: INHERITED, NOT MEASURED. `measure_7506.py` section 5 checks it.

MEASURED BY `measure_7506.py` (run by the Director, 2026-09-19, head 3c162244)
------------------------------------------------------------------------------
Positive controls first, both PASS: `Patch7205.confidence(7205)` = 0.95, and
block96 coverage on 7205 reproduces all five recorded tables exactly
(itemtype 1,080,527 blocks, 100%, 44,684 whole rows; monster, magictype,
itemtypesub, mounttype likewise). The 7205 census reproduces 101 files /
1,488,616 blocks / 809 unknown.

**Detection, every install (48 folders + the live CCO):** the winner changed
on exactly one, `7506` (plaintext 0.50 -> patch7506 0.95, margin 0.45).
`patch7506` scored 0.00 on every other install. The other `.tpd` clients
(7280 ... 7867) still fall to `plaintext` at 0.50.

**Containers:** `_discover_archives(7506)` = `['c3.tpi', 'data.tpi']`
(7275 control: `['c3.wdf', 'data.wdf']`); 0 `.dbc`. `c3.tpi` and `data.tpi`
are blake2b-IDENTICAL to 7280's, 7336's, 7387's and 7535's. The `.tpd`
payloads match on size + head/tail 16 MB only (sampled, NOT a full hash).

**Appearance tables (`part_tables()`), 7506 vs 7205:**

    slot      7506 (frozen .ini)   7205 (.dbc twin)
    armet          1,168               2,793
    body             955               3,754
    weapon         4,828              13,292
    mount              1               1,223   <- Mount.ini holds ONE row
    head / misc        0                   0

With no `.dbc` there is no twin, and the frozen `.ini` is all there is.
**The mount appearance table is effectively empty on 7506** -- a FINDING,
not a parse defect (the `mount` CATALOG, from `mounttype.dat`, has 1,880
sections).

**`ini/*.dat` families (all of `ini/`, recursive, 314 files):** block96 261,
rsa-mysqldump 10, rar-mangled 11, empty 10, plaintext 7, binary-plain 6,
tq-stream 3, unknown 4, block96-candidate-refuted 2, shift-obfuscated 1.
0 CODEC MISMATCH against the declared specs. **block96: 1,937,382 blocks,
139,163 unknown (92.82% known); 164 fully covered, 96 partial, 1 zero.**
Split: top-level `ini/` 112 block96 (79 full, 32 partial, 1 zero =
`levexp.dat`); `ini/luagui/` 25 (12 / 13 / 0); `ini/luaui/` 124 (73 / 51 / 0).

Key tables on 7506 (blocks known, whole records kept / units):

    itemtype.dat       92.53%   12,859 / 18,440   (7205: 100%, 44,684)
    MagicType.dat      93.59%    1,858 /  1,961   (7205: 2,907)
    monster.dat        95.34%    1,691 sections, 1,351 catalog rows
                                                  (7205: 3,670)
    ItemtypeSub.dat    87.23%       15 /     35   (7205: 633)
    mounttype.dat     100.00%    1,880 sections   (7205: 1,819)
    magictypeop.dat    99.67%      947
    MapDestination.dat 97.04%    1,088 sections
    award_config.dat   28.62%        0 whole       <- LOST
    levexp.dat          0.00%        0             (by design, see above)

**KNOWN LIMITS (findings, not tuned away):**
  * the block dictionary does not know 7.5% of `itemtype.dat`'s blocks, so
    only 12,859 whole item rows come out (7205: 44,684);
  * `award_config` is refused: 28.6% of blocks known, not one whole record;
  * `item:sub` is 15 rows (7205: 633) from 87.23% coverage;
  * 32 top-level tables are partial (e.g. coat_storage_type 35.91%,
    battlepass_score_reward 38.41%, AutoUseMagic 64.98%, prof_lev_up
    68.13%);
  * among the UNDECLARED new tables: emoneyShopV2 91.82% (9,730 rows),
    speffect_attribute 79.55%, new_shop_goods 69.68%, cop_task_type 54.89%;
    coat_color_rule, daoqi_dict_type, dict_collection, dict_lottery,
    month_card_type, newslot_guanqia_cfg, renown decode 100%;
    cq_remote_prize_cfg and the three vendue_* are EMPTY (0 B);
    `ServerPlay.dat` is block96-candidate-refuted.

**`catalogs()`:** 240 subjects, 237 with a control (7205: 247 / 245). item
12,859; item:sub 15; magic 1,858; magic:op 947; monster 1,351; mount 1,880;
map:dest 1,067; gamemap 657. The item count is 0.09 rows per 100 bytes of
`itemtype.dat`, nowhere near the 394,269-row noise shape.

WHAT IS NOT MEASURED
--------------------
  * **The table text ENCODING.** Section 5 of `measure_7506.py` RAISED
    (`UnicodeEncodeError` printing a GBK sample to a cp1252 console) before
    printing a single table, so NOTHING was learned. `TEXT_ENCODING` stays
    7205's latin1, INHERITED. (`npc:terrainnpc.ini`'s first control prints
    `Name=\ufffd\ufffd\ufffd\ufffd` under latin1-with-replace, so non-latin
    text exists in at least one plaintext table.) The script is fixed to
    write UTF-8; the re-run is owed.
  * `ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`, `BROWSE_IMPACT`,
    `RUNEEFFECT_REPEATS`, per-table `BLOCK96_COVERAGE` in 7205's shape
    (block counts per table were not printed): `None`, not 7205's.
  * Whether the `.tpd` payloads are byte-identical to the neighbours'.
  * The npc art chain on screen.

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
RESULT on 7506: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1367    65    65     0      0     0    0
    Name=:npc:NpcX.ini              926     3     3     0      0     0    0
    Name=:npc:npc.ini              3138    43    22    21      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                253     3     3     0      0     0    0
    achievement                     388     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    map:dest                       1067     7     6     0      0     1    0
    npc:NpcX.ini                    926     3     3     0      0     0    0
    npc:npc.ini                    3114    22    22     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      253     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 207: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 839: `a1 a1` = '\u3000'

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
STAMP = "7506"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded. Most name a `.dbc`, and this install ships none. The last one
#: carries 7205's script counts.
DBC_ERA_QUIRKS = (
    "stale ini decoys",
    "a second motion reader",
    "unpadded ids in compiled tables",
    "u32-wrapped motion ids",
    "three fewer compiled tables",
    "mount.dbc shrank",
    "the compiled tables froze at 6609",
    "block coverage is not record coverage",   # 7205's itemtype numbers
    "the per-weapon motion aliases are gone",
    "224 Lua scripts and no Lua grammar",      # 7205's counts
)


def _version(root: Path) -> str:
    """`version.dat`, four bytes, `b'7506'` on this install (read
    2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7506(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1367, 65, 65, 0],
        'Name=:npc:NpcX.ini': [926, 3, 3, 0],
        'Name=:npc:npc.ini': [3138, 43, 22, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [253, 3, 3, 0],
        'achievement': [388, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'map:dest': [1067, 7, 6, 0],
        'npc:NpcX.ini': [926, 3, 3, 0],
        'npc:npc.ini': [3114, 22, 22, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [253, 3, 3, 0],
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

    name = "patch7506"
    label = "Official patch client 7506"
    origin = "official"
    aliases = ("7506",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables. The archive switch is at 7275 -> 7280, and the .dbc "
        "set went from 12 to 0 at the same step. Content tables are 7205's: "
        "ini/*.dat in the 96-bit block cipher, read through the collection's "
        "block dictionary with 7205's specs. The art lookups are the frozen "
        "2008 plaintext decoys, sha-identical to 7205's and 7878's, with no "
        "compiled twin behind them: 1,748 of 3,138 npc.ini rows (55.7%) "
        "resolve a simple object and 1,390 do not, and nothing raises for "
        "those. MEASURED: 92.82% of block96 blocks known; itemtype 92.53%, "
        "12,859 whole item rows (7205: 44,684); award_config lost; the "
        "Mount.ini appearance table holds 1 row. The table text encoding is "
        "GBK, measured per table on the name fields (0 strict failures)."
    )

    # -- what the listing measured -----------------------------------------
    #: The archive pairs on disk, `.tpd` payload + `.tpi` index, with sizes.
    #: No `.wdf`. Same sizes as 7336's, NOT hashed.
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: The WDF-to-TPD bracket, from listings of the neighbours.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: `ls ini | grep -ic '\.dbc$'` -> 0.
    FROZEN_COMPILED = ()

    #: The plaintext lookups: the LIVE tables here only because nothing else
    #: exists. sha256 prefixes, identical to `patch7878.FROZEN_LOOKUPS`.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: `npc.ini` `SimpleObjID=` rows against the frozen `3DSimpleObj.ini`.
    #: The instrument reproduces 7205's 1,600 exactly.
    NPC_COVERAGE = {"rows": 3138, "unique": 3135, "resolved_ini": 1748,
                    "unresolved": 1390, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    #: `find -iname '*.lua'` and root `*.dll`. The same commands reproduce
    #: 7205's `SCRIPT_SURFACE` exactly. Globals were not counted.
    SCRIPT_SURFACE = {"lua": 369, "dll": 27, "lua_bytes": 14625583,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: `.dat` in 7506's `ini/` that 7205 does not ship. UNDECLARED: none has
    #: been decoded, and a spec is a promise to parse.
    NEW_DAT_SINCE_7205 = (
        "coat_color_rule.dat", "cop_task_type.dat", "cq_remote_prize_cfg.dat",
        "daoqi_dict_type.dat", "dict_collection.dat", "dict_lottery.dat",
        "emoneyshopv2.dat", "month_card_type.dat", "new_shop_goods.dat",
        "new_shop_info.dat", "newslot_guanqia_cfg.dat", "renown.dat",
        "speffect_attribute.dat", "vendue_item.dat", "vendue_item_type.dat",
        "vendue_type.dat",
    )

    #: `ini/` entries 7205 ships and 7506 does not (besides the 12 `.dbc`).
    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    #: Size recorded so an `item:sub` comparison is not made blind.
    ITEMTYPESUB_BYTES = 117268

    # -- measured by measure_7506.py ---------------------------------------
    #: The census marker, set HERE, literal False. The `.dat` ARE block96
    #: (261 files, 92.82% known), but adding 7506 to `dat_census.BUILDS`
    #: reds `test_dat_census` (it pins `combat_gear.dat` to 7189 alone, and
    #: 7506 ships it). See the module docstring. Flip to True together with
    #: the BUILDS slot once that test's per-build pins cover 7506.
    DAT_CENSUS_ERA = False
    #: 7506's OWN census, replacing 7205's dict (same keys, minus the two
    #: 7878-identity keys, not measured here). `block96_census(7506)`.
    BLOCK96_CENSUS = {"files": 261, "blocks": 1937382, "unknown": 139163,
                      "fully_covered": 164, "partial": 96, "uncovered": 1}
    #: Key tables: blocks-known fraction and whole records (or sections).
    #: A DIFFERENT name from 7205's `BLOCK96_COVERAGE`, whose shape carries
    #: per-table block counts that were not printed for 7506.
    KEY_TABLE_COVERAGE = {
        "itemtype.dat": {"known": 0.9253, "intact": 12859, "units": 18440},
        "magictype.dat": {"known": 0.9359, "intact": 1858, "units": 1961},
        "monster.dat": {"known": 0.9534, "sections": 1691},
        "itemtypesub.dat": {"known": 0.8723, "intact": 15, "units": 35},
        "mounttype.dat": {"known": 1.0, "sections": 1880},
        "award_config.dat": {"known": 0.2862, "intact": 0, "units": 0},
        "levexp.dat": {"known": 0.0, "intact": 0, "units": 0},
    }
    #: `part_tables()` row counts on 7506 (frozen .ini; no twin).
    PART_TABLE_ROWS = {"armet": 1168, "body": 955, "l_weapon": 4828,
                       "r_weapon": 4828, "mount": 1, "head": 0, "misc": 0}

    # -- 7205 measurements that are NOT this build's ------------------------
    BLOCK96_COVERAGE = None
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    #: levexp by identity; award_config MEASURED lost on 7506.
    UNCOVERED_TABLES = {
        "award_config.dat": "MEASURED on 7506: 28.6% of blocks known and not "
                            "one record whole, so the catalog refuses it "
                            "(7205: 72 rows).",
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's, so 7205's finding carries by identity: held "
                      "out of the dictionary by design.",
    }
    #: 7205's refused names, all on disk here. The reasons are 7205's
    #: measurements, marked as such.
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7506] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7506` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. The formats cannot separate 7506
        from its `.tpd`-era neighbours (same archive sizes, no `.dbc`, same
        frozen lookups), so nothing but the stamp is read.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`. Every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install to prefer."""
        return False

    def import_plan(self, root, exists) -> dict:
        """The `.tpd` pair, and no claim that it is shared (sizes match the
        neighbours; nothing has been hashed)."""
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
                "on any of them, and 7506's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7506's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7506] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7506: Action3DEffect keys are (3,4,3,3) x1,924, "
            "(4,4,3,3) x803, (1,4,3,1) x194 -- the action field is four wide "
            "as on 6090 and 7205, not three as in the plaintext family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,138 SimpleObjID rows; 1,748 resolve a simple object and 1,390 "
            "do not. Nothing raises for an unresolved row.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear.")
        q["369 Lua scripts and no Lua grammar"] = (
            "369 .lua (14,625,583 B) and 27 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables; "
            "tools/clientscripts.py and tools/scriptreport.py read it.")
        q["ItemtypeSub.dat is 117,268 bytes"] = (
            "7205 207,838; 7336 381; 7387 463,165. Not yet decoded; an "
            "item:sub count here is not comparable to 7205's until it is.")
        return q


PLUGIN = Patch7506()
