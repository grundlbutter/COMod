#!/usr/bin/env python3
r"""
patch7535 -- official Conquer Online patch 7535: 7205's content layer inside
a `.tpd`-only install with no compiled tables.

7535 is a client of the `.tpd`-only era (7280, 7320, 7336, 7373, 7387, 7506,
7535, 7562, 7589). **The archive switch is at 7275 -> 7280**: 7275 is the
last client that ships `c3.wdf` / `data.wdf` and twelve `ini/*.dbc`; 7280 is
the first that ships only `.tpd`/`.tpi` pairs and **zero** `.dbc`. The
`.dbc` set goes from 12 to 0 at that same step (wave A's listings, on
`director/comod-plugin-7280` and `-7336`). 7535 is well on the `.tpd` side.
The listing, read 2026-09-19 (a directory listing only; no archive opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7506   7506          0      2/2           0         523           163
    7535   7535          0      2/2           0         531           165
    7562   7562          0      2/2           0         534           165

    7535/c3.tpd    1,076,058,840 B    7535/c3.tpi    3,306,363 B
    7535/data.tpd    896,279,080 B    7535/data.tpi  5,724,770 B

The four archive files have the SAME SIZES (and the same 2021-07-23 mtime)
on 7506, 7535 and 7562, and the same sizes wave A recorded for 7280-7373.
Same size is not the same bytes. They have NOT been hashed here (that is in
`measure_7535.py`), so this plugin does not say they are shared.
`Conquer.exe` is the 41-byte text stub; the launcher is `play.exe`.

WHY THE BASE IS `Patch7205`, AND WHAT IS OVERRIDDEN
----------------------------------------------------
Wave A (7280, 7320, 7336) decided this and the reason holds here unchanged,
because it is about the candidate bases, not the install:

  * **`Patch7878` (`PlaintextFamily`)** fits the art layer (no `.dbc`, a
    `.tpd` pair, the frozen plaintext chain) and **must not be the base**:
    `patch7878.derived_tables(root)` finds
    `<root>/../../derived/7878-dat-decrypted` for ANY root under `Clients/`,
    so a subclass would serve **7878's** decrypted tables as 7535's. Nothing
    would raise, and every control would pass on real files.
  * **`Patch7205`** fits the content layer: its `ini/*.dat` specs read the
    `block96` codec through the collection's block dictionary, which decodes
    a table from THIS client's own ciphertext. Checked by listing on 7535:
    every file named by `SPECS_7205` (23) and `SPECS_7205_CENSUSED` (85) is
    on disk, and every key of `Patch7205.UNDECLARED_DAT` too. 132 of 6609's
    141 borrowed `.ini` census specs name a file 7535 ships (7205: 139).

The art-layer hooks that name `.dbc` files are overridden, because this
install ships none:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
    names four `.dbc` and none is on disk (`tests/test_patch7535.
    TheArtProfileNamesFilesThatExist` is the arm that fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False. There is no twin to prefer.
  * `import_plan()` names the `.tpd` pair and drops 6090's "shared archives
    skipped, compiled .dbc tables read" note, false here on both counts.

**`DAT_CENSUS_ERA = False`, and the `tools/dat_census.py` slot stays
commented -- although the cipher says yes.** `measure_7535.py` (run
2026-09-19, head `2afaa9ee`, EXIT 0) classified 317 `.dat` under 7535's
`ini/` (recursive, so `luaui/` counts) as block96 against 56 of every other
family combined (binary-plain 6, empty 10, rar-mangled 11, tq-stream 3,
plaintext 7, unknown 5, block96-candidate-refuted 3, rsa-mysqldump 10,
shift-obfuscated 1), with 0 codec mismatches against the declared specs.
BUT with `"7535"` in `BUILDS`, `tests/test_dat_census.py` goes red:
`TheVerdictIsPerBuild.test_a_file_on_one_build_is_reported_on_one_build`
pins `combat_gear.dat` to `['7189']` only, and 7535 ships `combat_gear.dat`
too (`['7189', '7535']`). That test's per-build facts are not ours to
edit, so 7535 is NOT censused until that pin is re-decided. The census
numbers below are this build's own and stand regardless.
`BLOCK96_CENSUS` is 7205's census DICT, so it is REPLACED here with 7535's
own measurement in the same shape, never inherited.

What is KEPT from the 6090 lineage, and on what evidence (MEASURED on 7535
by plaintext reads, each instrument reproducing 7205's recorded figure):

  * **Four-wide `Action3DEffect` action field** (`FIELD_WIDTHS`): 3,106 keys,
    shapes (3,4,3,3) x2,005, (4,4,3,3) x868, (1,4,3,1) x200, (3,4,1,3) x24.
    The action field is 4 wide on every dominant shape. CONTROL: the same
    count on 7205 gives 2,328 keys, (3,4,3,3) x1,535 -- 6090's width.
  * **`aura_convention() == "table"`**: 2,660 of 3,106 keys carry action
    `9999` (same instrument on 7205: 1,900 of 2,328, as recorded).
  * **`ROW_COLUMNS`**: `region.ini` 304 rows, all 14 fields, column 6
    non-numeric on 304/304 (85 distinct); CONTROL 7205 reads 303/84 as
    recorded. `EventTypeName.ini`, `VipTrans.ini` and `restrain.ini` are
    sha256-identical to 7205's.
  * `texture_for_mesh`, colour sets, sockets, `socket_correction`: INHERITED
    AND NOT MEASURED HERE. 6090 measurements, read on the WDF pair.

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 decoys, and nothing stands behind
them.** sha256 prefixes, identical to 7205's, 7336's, 7506's, 7562's and
7878's and to `patch7878.FROZEN_LOOKUPS`:

    ini/3DSimpleObj.ini f34f56332383   ini/3dobj.ini    9202de93aa03
    ini/3dtexture.ini   6be2fece83fd   ini/3dmotion.ini 9951727aa1ed
    ini/armor.ini       8e64691e0f00

`npc.ini` kept growing (`SimpleObjID=N` -> `[ObjIDType<N>]`, per header):

    build  npc.ini headers  resolve via 3DSimpleObj.ini  unresolved
    7205         2,913             1,600  (54.9%)          1,313
    7506         3,138             1,748  (55.7%)          1,390
    7535         3,179             1,761  (55.4%)          1,418
    7562         3,260             1,802  (55.3%)          1,458

POSITIVE CONTROL: the same code reproduces
`patch7205.NPC_COVERAGE["resolved_ini_decoy"]`, 1,600, exactly. On 7205
that was the decoy beside a 2,466-row compiled answer; **on 7535 it is the
only answer**, and 1,418 npc rows name a simple object the frozen table does
not carry. Nothing raises for them.

**`levexp.dat` is byte-identical to 7205's and 7878's** (4,030 B, sha256
`4482cfaaec7d`), so 7205's refusal text is true here by identity; measured,
0.00% of its blocks are known (held out of the dictionary by design).
**`ServerPlay.dat` is a different file** (6,015 B, `dd2fe439c028`; 7205
5,696 B, 7506 5,953 B, 7878 6,650 B), classified
`block96-candidate-refuted`. Not decoded.

BLOCK96 COVERAGE ON 7535 (measure_7535.py section 4)
----------------------------------------------------
POSITIVE CONTROL FIRST: the same reader on 7205 reproduced all five of
`Patch7205.BLOCK96_COVERAGE` exactly (100% known) and `block96_census`
reproduced 7205's recorded census exactly. So the low numbers below are
7535's, not the instrument's.

    7535 ini/**.dat census: 317 files, 2,124,496 blocks, 142,193 unknown
    (93.31% known); 229 fully covered, 87 partial, 1 at zero (levexp.dat)

    table            known    lines    intact   units   (7205 intact/units)
    itemtype.dat     92.53%   35,678   12,859   18,440  (44,684 / 44,684)
    ItemtypeSub.dat  89.98%      816      300      382  (   633 /    633)
    MagicType.dat    93.53%    2,882    1,790    1,923  ( 2,907 /  2,907)
    monster.dat      94.68%  105,715  101,611    1,548  (96,045 /  3,709)
    mounttype.dat   100.00%   26,467   26,467    1,882  (25,583 /  1,819)
    award_config.dat 27.12%        7        0        0  (7205: 72 rows)

**KNOWN LIMIT, NOT A DEFECT TO TUNE AWAY: the 7878-derived dictionary does
not know ~7.5% of 7535's `itemtype.dat` blocks**, so 12,859 item rows come
out whole where 7205 gives 44,684, and **`award_config` is lost entirely**
(27.1% of blocks known, not one whole record; it is served as a refusal).
`monster` gives 1,548 whole sections (catalog 1,216 rows) against 7205's
3,709 / 3,670. `mounttype` is entire. Every count is a FLOOR.

The new tables decode entire: `vassal_war_agent.dat` 12,800 rows,
`vassal_war_soldier.dat` 60, `daoqi_dict_type.dat` 326, `dict_collection`
23, `dict_lottery` 31, `renown` 1 -- all 100% known, all still UNDECLARED
(a spec is a promise to parse, and none has a measured column map).

CATALOGS (section 5): 240 subjects, 237 with a control (7205: 247 / 245).
The three without: `action` (binary layout, as on 7205), `levexp` (held
out), `award_config` (above). Headline rows, 7535 vs 7205: item 12,859 vs
44,684; item:sub 300 vs 633; magic 1,790 vs 2,907; monster 1,216 vs 3,670;
mount 1,882 vs 1,819; map:dest 1,067 vs 936. None is the wrong-cipher noise
shape (7205's pre-fix 394,269 item rows).

CONTAINERS AND APPEARANCE (sections 2, 3): `_discover_archives` returns
`['c3.tpi', 'data.tpi']` (7275, the control, returns the WDF pair).
`c3.tpi` and `data.tpi` are blake2b-IDENTICAL to 7506's, 7562's and 7280's;
the `.tpd` payloads match them on size plus head+tail 16 MB (SAMPLED, not a
full hash). `part_tables()` reads the frozen `.ini` only: body 955 rows,
armet 1,168, weapon 4,828, and **mount 1 row** from `Mount.ini` -- against
7205's compiled 3,754 / 2,793 / 13,292 / 1,223. The mount appearance table
is effectively empty on this client, and nothing raises.

DETECTION (section 1, every install on disk plus the live CCO): only 7535
changes its winner (`plaintext` 0.50 -> `patch7535` 0.95, margin 0.45), and
patch7535 scores 0.00 on every other install.

**`ini/` differs from 7205's by name** (7506 -> 7535 adds two `.dat` and six
`.lua`, and drops nothing):

    gone since 7205   the 12 .dbc; action3deffect1..5.ini, emoneyshop.ini,
                      strres.ini
    new since 7205    18 .dat (NEW_DAT_SINCE_7205, UNDECLARED -- not
                      decoded); actionrole3deffect.ini, actionsoundex.ini,
                      appqdkey.ini, cq_remote_prize_cfg.ini, operateweben.ini,
                      shaderparam.ini; and .lua files plus luagui/ luaui/
    new since 7506    vassal_war_agent.dat, vassal_war_soldier.dat, and six
                      .lua

**Script surface**: 393 `.lua` (15,276,048 B) and 27 root DLLs, against
7205's 224 (19,094,816 B) and 37. The same `rglob` reproduces 7205's
recorded 224 / 19,094,816 / 37 exactly, which is the control. Exported
globals were NOT counted.

**Encoding.** `codepage.ini` reads `1256`, as on 7205 and 7878.
`ini/SlotNpc.ini` is sha256-identical (`c4e23de07332`, 3,959 B) to 7878's,
which measured GBK. Section 4b re-measured it on 7535: 102 high bytes,
strict GBK decodes with 51 CJK characters, UTF-8 fails, cp1256 "decodes"
with 0 (the 7878 lesson: decoding is not evidence). (The file is ALSO
identical on 7205, 7336, 7506 and 7562, so 7205's own latin1 is
contradicted by the same file; a 7205 finding, not acted on here.)
`TEXT_ENCODING` stays 7205's latin1, INHERITED. **NOT MEASURED: every other
text table.** Section 4b raised `UnicodeEncodeError` printing a GBK sample
to the console (a bug in the measure script, since fixed in it), so the
per-file sweep never ran.

WHAT IS INFERRED
----------------
  * That each `.tpd` payload is the same bytes as 7506's (sampled only).
  * That the inherited art conventions (texture, colour, sockets) hold.

WHAT IS NOT MEASURED
--------------------
`FORMERLY_UNCOVERED`, `ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`,
`BROWSE_IMPACT`, `RUNEEFFECT_REPEATS` stay `None`, never a number carried
over from 7205; nor is the encoding of any table but `SlotNpc.ini`.

`UNDECLARED_DAT` keeps 7205's names (all on disk here), each reason marked
as 7205's measurement.

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
RESULT on 7535: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1224    12    12     0      0     0    0
    Name=:npc:NpcX.ini              938     3     3     0      0     0    0
    Name=:npc:npc.ini              3179    28     7    21      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                255     3     3     0      0     0    0
    achievement                     388     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    map:dest                       1067     7     6     0      0     1    0
    npc:NpcX.ini                    938     3     3     0      0     0    0
    npc:npc.ini                    3155     7     7     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      255     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 207: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 851: `a1 a1` = '\u3000'

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
STAMP = "7535"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded. Most name a `.dbc`, and this install ships none; the last two
#: carry 7205's measurements of its own content layer and script counts.
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
    """`version.dat`, four bytes, `b'7535'` on this install (read
    2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7535(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1224, 12, 12, 0],
        'Name=:npc:NpcX.ini': [938, 3, 3, 0],
        'Name=:npc:npc.ini': [3179, 28, 7, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [255, 3, 3, 0],
        'achievement': [388, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'map:dest': [1067, 7, 6, 0],
        'npc:NpcX.ini': [938, 3, 3, 0],
        'npc:npc.ini': [3155, 7, 7, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [255, 3, 3, 0],
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

    name = "patch7535"
    label = "Official patch client 7535"
    origin = "official"
    aliases = ("7535",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables. The archive switch is at 7275 -> 7280, and the .dbc "
        "set went from 12 to 0 at the same step. Content tables are read "
        "with 7205's specs: ini/*.dat through the collection's block "
        "dictionary. The art lookups are the frozen 2008 plaintext decoys, "
        "sha-identical to 7205's and 7878's, with no compiled twin behind "
        "them: 1,761 of 3,179 npc.ini rows resolve a simple object and "
        "1,418 do not, and nothing raises for those. The block dictionary "
        "knows 93.3% of this build's blocks: itemtype.dat 92.5%, so 12,859 "
        "whole item rows against 7205's 44,684, and award_config is lost. "
        "The Mount.ini appearance table has 1 row."
    )

    # -- what the listing measured -----------------------------------------
    #: The archive pairs on disk, `.tpd` payload + `.tpi` index, with sizes.
    #: No `.wdf`. Same sizes on 7506/7562 (and 7280-7373), NOT hashed.
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: The WDF-to-TPD bracket, from listings of the neighbours.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: `find ini -iname '*.dbc'` -> 0.
    FROZEN_COMPILED = ()

    #: The plaintext lookups, LIVE here only because nothing else exists.
    #: sha256 prefixes, identical to `patch7878.FROZEN_LOOKUPS`.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: `npc.ini` headers against the frozen `3DSimpleObj.ini`, per header.
    #: The instrument reproduces 7205's 1,600 exactly.
    NPC_COVERAGE = {"rows": 3179, "unique": 3176, "resolved_ini": 1761,
                    "unresolved": 1418, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    #: Counted by `rglob('*.lua')` and `glob('*.dll')`; the same calls
    #: reproduce 7205's `SCRIPT_SURFACE` exactly. Globals were not counted.
    SCRIPT_SURFACE = {"lua": 393, "dll": 27, "lua_bytes": 15276048,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: `.dat` in 7535's `ini/` that 7205 does not ship. UNDECLARED: none has
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

    #: `ini/` entries 7205 ships and 7535 does not (besides the 12 `.dbc`).
    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    #: Byte-identical to 7878's copy, which measured GBK. Identity, not a
    #: new decode.
    GBK_BY_IDENTITY = {"ini/SlotNpc.ini": "c4e23de07332"}

    # -- this build's own measurements (measure_7535.py, 2026-09-19) ------
    #: The dat-census marker. FALSE although 317 `ini/**.dat` classify
    #: block96: listing 7535 in `tools/dat_census.BUILDS` reds
    #: `test_dat_census` (combat_gear.dat is pinned to 7189 alone, and 7535
    #: ships it). See the module docstring.
    DAT_CENSUS_ERA = False
    #: 7205's census DICT, replaced with 7535's in the same shape (the two
    #: `_identical_to_7878` keys were not measured here and are omitted).
    BLOCK96_CENSUS = {"files": 317, "blocks": 2124496, "unknown": 142193,
                      "fully_covered": 229, "partial": 87, "uncovered": 1}
    #: Per table, `Recovery`'s vocabulary. `blocks` was not printed per file
    #: by the measure script, so it is None rather than guessed.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": None, "known": 0.9253, "lines": 35678,
                         "intact": 12859, "units": 18440, "shape": "rows"},
        "monster.dat": {"blocks": None, "known": 0.9468, "lines": 105715,
                        "intact": 101611, "units": 1548,
                        "shape": "sections"},
        "magictype.dat": {"blocks": None, "known": 0.9353, "lines": 2882,
                          "intact": 1790, "units": 1923, "shape": "rows"},
        "itemtypesub.dat": {"blocks": None, "known": 0.8998, "lines": 816,
                            "intact": 300, "units": 382, "shape": "rows"},
        "mounttype.dat": {"blocks": None, "known": 1.0, "lines": 26467,
                          "intact": 26467, "units": 1882,
                          "shape": "sections"},
        "award_config.dat": {"blocks": None, "known": 0.2712, "lines": 7,
                             "intact": 0, "units": 0, "shape": "rows"},
    }
    #: `part_tables()` rows on 7535: the frozen `.ini` only.
    PART_TABLE_ROWS = {"body": 955, "armet": 1168, "l_weapon": 4828,
                       "mount": 1}

    # -- 7205 measurements NOT re-taken on this build --------------------
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    #: Only the one fact measured here.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's and 7878's, so 7205's finding carries by "
                      "identity: held out of the dictionary by design. "
                      "MEASURED 0.00% known on 7535.",
        "award_config.dat": "27.1% of blocks known, 0 whole records: the "
                            "7878-derived dictionary does not reach this "
                            "build's copy. 7205 serves 72 rows.",
    }
    #: 7205's refused names, all on disk here. The reasons are 7205's
    #: measurements, marked as such.
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7535] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7535` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. The formats cannot separate 7535
        from 7506/7562: the same `.tpd` sizes, no `.dbc`, the same frozen
        lookups. So nothing but the stamp is read and no sibling is claimed.
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
        """The `.tpd` pair, and no claim that it is shared (the pair matches
        7506/7562 by SIZE only)."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": ["c3.tpd", "data.tpd"],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: .tpd archives (no .wdf), no compiled "
                     f".dbc -- the plaintext lookup chain is the only one; "
                     f"ini/*.dat read with 7205's block96 specs"),
        })
        return plan

    def colour_provenance(self):
        """Inherited from 6090 through 6609 and 7205, and across a container
        change: the sets were read on the WDF pair; this install is `.tpd`."""
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7535's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc`-era ones, plus 7535's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7535] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7535: 3,106 Action3DEffect keys, (3,4,3,3) "
            "x2,005, (4,4,3,3) x868, (1,4,3,1) x200 -- the action field is "
            "four wide as on 6090 and 7205, not three as in the plaintext "
            "family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,179 headers; 1,761 resolve a simple object and 1,418 do not. "
            "Nothing raises for an unresolved row.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear.")
        q["393 Lua scripts and no Lua grammar"] = (
            "393 .lua (15,276,048 B) and 27 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables; "
            "tools/clientscripts.py and tools/scriptreport.py read it.")
        return q

    def why_no_tables(self) -> str:                       # pragma: no cover
        return ("7535 declares tables; if you are reading this the install "
                "has no ini/ directory")


PLUGIN = Patch7535()
