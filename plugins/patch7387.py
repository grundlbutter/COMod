#!/usr/bin/env python3
r"""
patch7387 -- official Conquer Online patch 7387: 7205's content layer inside a
`.tpd`-only install with no compiled tables. Wave B; the base was settled by
wave A (7280 / 7320 / 7336) and is RE-CHECKED here against 7387's own files,
not copied.

THE ARCHIVE SWITCH IS BEHIND THIS BUILD
---------------------------------------
7275 is the LAST `.wdf` client (c3.wdf/data.wdf, twelve `ini/*.dbc`); 7280 is
the FIRST `.tpd`-only one, and the `.dbc` set goes from 12 to 0 at that same
step (`patch7280`'s header carries the listing of both sides). 7387 is on the
`.tpd` side. Read 2026-09-19 (07:07Z) by directory listing only; no archive
was opened:

    build  version.dat   *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat top / recursive
    7336   `7336`         0      2/2          0          509           160 / 218
    7373   `7373\r\n`     0      2/2          0          516           161 / 271
    7387   `7387`         0      2/2          0          516           161 / 276
    7506   `7506`         0      2/2          0          523           163 / 315

    7387/c3.tpd    1,076,058,840 B    7387/c3.tpi    3,306,363 B
    7387/data.tpd    896,279,080 B    7387/data.tpi  5,724,770 B

The four archive files have the SAME SIZES (and the same 2021-07-23 dates) on
7280 through 7506. Same size is not same bytes; they are NOT hashed (that is
in `measure_7387.py`), so `sharedArchives` stays False.

**7387's `ini/` NAME SET IS IDENTICAL TO 7373's** (`comm -3`, case-folded:
empty), and so is the top-level listing. The five `.dat` 7387 adds are all
under `ini/luagui/` and `ini/luaui/` (`1249.dat`, `1257.dat`,
`dlgbeachmusic/beachmusicfunc.dat`, `dlgbeachmusic/dlgbeachmusicmainui.dat`,
`publicdlg/dlgfairydragontop.dat`); none is a top-level table.

`Conquer.exe` is the 41-byte text stub; `play.exe` (1,860,864 B) is the
launcher. `version.dat` is exactly four bytes, `7387`, no newline (7373's
carries `\r\n`; `_version` strips either).

WHY THE BASE IS `Patch7205`
---------------------------
Same decision as 7280/7320/7336, and it rests on the same two facts, both
re-read on 7387:

  * **`Patch7878` is REJECTED because of its content layer.**
    `patch7878.derived_tables(root)` resolves
    `<root>/../../derived/7878-dat-decrypted` for ANY root under `Clients/`,
    so a 7387 subclass would serve 7878's decrypted tables as 7387's rows,
    and its raw-byte controls would check them against 7878's files. Nothing
    raises; every control agrees with itself. And the two builds' tables are
    NOT the same: 7387's `itemtype.dat` is 14,136,592 B against 7878's
    16,907,304 B, `monster.dat` 1,384,713 against 1,748,363, `MagicType.dat`
    750,886 against 1,036,458.
  * **`Patch7205` fits the content layer.** Every file named by `SPECS_7205`
    (23) and `SPECS_7205_CENSUSED` (85) is on disk here -- checked by name,
    none absent -- and so is every name in `UNDECLARED_DAT` (14). 132 of
    6609's 141 borrowed `.ini` specs name a file 7387 ships (7205: 139).
  * `PlaintextFamily` (what 7387 detects as today, INFERRED from the code
    and the listing: no `.dbc`, plaintext chain present, an unlisted stamp
    -> 0.5) declares no `table_specs` at all. `measure_7387.py` section A
    prints the real ranking.

Its ART layer is wrong here, and that is what is overridden:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
    names `ini/3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc` and
    `3dmotion.dbc`; 0 of the 4 ship. `PROFILE_PLAINTEXT` names the four
    `.ini`; 4 of 4 ship. This is the arm `tests/test_patch7387.py
    ::ThePredecessorIsWrongHere` pins, measured on both plugins.
  * `prefers_compiled_tables()` -> False: no twin exists.
  * `import_plan()` names `c3.tpi`/`data.tpi`, and `sharedArchives` is False.
    The inherited plan names `c3.wdf`/`data.wdf`, which are ABSENT, and
    `tools/assetdiff.catalog_baseline` skips an absent archive without a
    word, so an import would index zero archive entries and report success.
  * `table_quirks()` drops the `.dbc`-premised quirks by name
    (`DBC_ERA_QUIRKS`) and marks the rest as not re-measured here.

WHAT IS MEASURED HERE (listing and small plaintext reads, 2026-09-19)
---------------------------------------------------------------------
**The art lookups are the frozen 2008 files and nothing stands behind them.**
sha256 prefix, identical on 7205, 7336, 7373, 7387 and 7878: see
`FROZEN_LOOKUPS` and `FROZEN_PART_TABLES`. `RolePart.ini` is 1,804 B
`dcfc67be` -- 7336's and 7373's file, not 7205's (1,471 B) or 7878's
(1,870 B). `head.ini` and `misc.ini` are 0 bytes; `armet1.ini`,
`shield.ini`, `pelvis.ini` are NOT SHIPPED (nor on 7205/7336/7373/7878).

**npc.ini against the frozen `3DSimpleObj.ini`**, `SimpleObjID=N` ->
`[ObjIDType<N>]`, one count per header:

    build  npc.ini headers  resolve    unresolved   (3DSimpleObj sections)
    7205         2,913       1,600        1,313          191
    7336         3,099       1,723        1,376          191
    7373         3,158       1,762        1,396          191
    7387         3,166       1,764 (55.7%) 1,402         191

POSITIVE CONTROLS: the same code reproduces `patch7205.NPC_COVERAGE
["resolved_ini_decoy"]` (1,600) and `patch7336`'s recorded 1,723 exactly. On
7205 the decoy's figure stood beside a 2,466-row compiled answer; **on 7387 it
is the only answer**, and 1,402 npc rows name a simple object the frozen
table does not carry. An unresolved row looks exactly like an npc with no art.

**Byte identity of every `ini/**.dat` under 1 MB** (273 of 276; the three
over are `itemtype.dat`, `monster.dat`, `emoneyShopV2.dat`):

    7387 vs 7373   231 identical, 37 differ,  5 absent there
    7387 vs 7336   155 identical, 60 differ, 58 absent there
    7387 vs 7205    89 identical, 56 differ, 128 absent there
    7387 vs 7878   132 identical, 138 differ, 3 absent there

`itemtype.dat` is byte-identical to 7336's and 7373's (14,136,592 B, sha256
`bb137e03444b`), so an `item` count here should equal theirs -- a check
`measure_7387.py` makes rather than assumes. `levexp.dat` is `4482cfaaec7d`
(4,030 B) on 7205, 7336, 7373, 7387 and 7878, so 7205's refusal text for it
is true of these bytes. `ServerPlay.dat` is a different file on every
neighbour (7387: 5,922 B `4ec7fbba4cb6`). `ItemtypeSub.dat` is 463,165 B
(`12f6c1be119d`): 7336's anomalous 381-byte copy is NOT repeated here.
`GameMap.dat` is byte-identical to 7373's (20,794 B, `3dc98cfdf19e`).

**Row-column tables** (`ROW_COLUMNS`, inherited): `EventTypeName.ini`,
`VipTrans.ini` and `restrain.ini` are byte-identical to 7205's. `region.ini`
differs from 7205's and is byte-identical to 7336's/7373's (`318a8e4f3494`):
304 rows, all 14 fields, column 6 non-numeric on 304/304 (85 distinct),
column 1 numeric on 304/304. The declared `{id 0, name 6}` holds.

**`Action3DEffect.ini`**: 2,860 keys; shapes (3,4,3,3) x1,867, (4,4,3,3)
x777, (1,4,3,1) x184, (3,4,1,3) x24. The action field is four wide, as on
6090/7205/7336, so the "four-wide action fields" quirk is KEPT. 2,423 keys
carry action `9999`, so `aura_convention() == "table"` is supported.

**Script surface**: 346 `.lua` (13,835,390 B), 27 top-level DLLs (7205: 224 /
19,094,816 / 37; 7336: 321 / 13,905,069 / 27). Exported globals NOT counted.

**Encoding.** `codepage.ini` = `1256`, as on 7205 and 7878. `SlotNpc.ini` is
**byte-identical to 7878's** (3,959 B, `c4e23de07332`) -- the very file on
which `patch7878` MEASURED GBK -- so that finding holds for this file's bytes
by identity. It is also identical to 7205's, which keeps latin1. So GBK is
MEASURED FOR ONE FILE and INFERRED for the rest; `TEXT_ENCODING` stays the
inherited `latin1` (it round-trips every byte and cannot break a control) and
`measure_7387.py` section I tests GBK on the other plaintext tables.

WHAT IS INFERRED
----------------
  * That the unknown 6.9% of 7387's blocks are content newer than the
    7878-derived dictionary, and not a different cipher. The evidence: 127
    files decode entire, and every served subject carries a positive control.
    The reason for any single missing block is not measured.
  * That the small tables ABOVE 7205's count (coat_storage_attr,
    newslot_broadcast, msgboxex) grew for real. Each has a control, and none
    was checked row by row.

MEASURED BY `measure_7387.py` (run by the Director 2026-09-19, EXIT 0, 175 s)
------------------------------------------------------------------------------
Every number carries its install; every control ran before its number.

**A. Detection, 48 installs.** CONTROL: `patch7205` on 7205 = 0.95. The
winner changes on exactly one install, 7387 (`plaintext` 0.50 ->
`patch7387` 0.95, margin 0.45). `patch7387` scores 0.00 on the other 47,
including the unplugged `.tpd` siblings 7280..7867, which stay `plaintext`
0.50.

**B. Containers.** CONTROL: 7275 discovers `c3.wdf`, `data.wdf`. 7387
discovers `c3.tpi`, `data.tpi`. 7387's `c3.tpi` (`4b4bde688f1844de`) and
`data.tpi` (`9096a9e0ed081a57`) are byte-identical to 7373's and 7336's.
The `.tpd` payloads were NOT hashed, so `sharedArchives` stays False.

**C. Appearances.** CONTROL: 7205 resolves 41,743 of 41,905 part meshes
(99.6%). 7387: 13,902 parts, mesh 1,556 (11.2%), texture 1,072 (7.7%),
identical to 7373 and below 7878's 15.5% / 12.2%. A KNOWN LIMIT OF THE
CLIENT: the frozen 2008 lookups name ids the archives do not ship.

**D. `ini/**.dat`.** CONTROL: 7205 reproduces `Patch7205.BLOCK96_CENSUS`
exactly (101 files, 1,488,616 blocks, 809 unknown, 99/0/2). 7387: 225
block96 files, 1,872,422 blocks, 128,815 unknown (93.12% known); 127 fully
covered, 96 partial, 2 at zero (`levexp.dat`, `ServerPlay.dat`); 182 of the
225 byte-identical to 7373's. `block96_census()` reproduces the same six
numbers. Families: block96 225, binary-plain 6, empty 10, rar-mangled 11,
tq-stream 3 (`client_config.dat`, `kok_roleview.dat`,
`UserHelpInfo.ini.dat`, all =7373), plaintext 7, unknown 3, rsa-mysqldump
10, shift-obfuscated 1. Per table (blocks known, whole units):

    itemtype.dat      93.61%  22,621 of 38,430 lines  (7205: 100%, 44,684)
    monster.dat       95.81%   2,001 sections          (7205: 100%, 3,709)
    MagicType.dat     92.82%   1,364 of 2,406 lines    (7205: 100%, 2,907)
    ItemtypeSub.dat   90.05%     158 of 899 lines      (7205: 100%, 633)
    mounttype.dat    100.00%   1,875 sections          (7205: 100%, 1,819)
    award_config.dat  28.62%       0 -- REFUSED
    coat_storage_type.dat 34.50%  18 of 227 lines

**The dictionary is 7878-derived, and 7387's newer blocks are not in it.**
That is a limit of the dictionary, not a wrong cipher. Every count below is
a FLOOR. `item` is half of 7205's because a ~27-block row needs every one of
its blocks, so 6.4% of blocks unknown costs 41% of the rows.

**E. `catalogs()`.** 240 subjects, 237 readable, 3 refused: `action` (by
design), `award_config` (28.6% of blocks), `levexp` (0%). Today's winner,
`plaintext`, reads 0 of 1. item 22,621, monster 1,680, magic 1,364, mount
1,875, item:sub 158, each with a positive control. 18 subjects fall outside
[0.5, 2.0] of 7205's count. All but two are BELOW it: partial-coverage
floors, not cipher noise (noise lands far ABOVE the real count; the 7205
failure shape was 394,269 against ~39,771). The two above,
coat_storage_attr 157/14 and newslot_broadcast 9/3 (msgboxex 5/2 too), are
small tables that grew; NOT separately verified. The full list, 7387/7205:
activity 36/112, battlepass_score_reward 12/150, coat_storage_attr 157/14,
coat_storage_type 12/765, combat_gear 3/150, exchange_shop_goods_ex 17/63,
gamemapex 105/287, hairface_storage_type 128/418,
instance_enter_condition 125/351, item:sub 158/633, magic 1,364/2,907,
magic:auto 1/15, map:dest 228/936, monster 1,680/3,670, msgboxex 5/2,
newslot_broadcast 9/3, prof_lev_up 199/549, texas_match_type 5/82.

**F. `itemtype.dat`.** Modal width 66 fields on 7205, 7373 and 7387.
CONTROL: 7373's byte-identical file gives the same 25,847 whole lines as
7387's.

**G. npc chain** (`npcart.audit`). CONTROL: 7205 official 2,466 of 2,904.
7387 plaintext 1,766 of 3,163; 7387 official **0 of 3,163**. The
predecessor's profile resolves NOTHING here, which is the failure
`table_profile` overrides.

**H. Labels.** 237 subjects browsed; none blank without `label_key=None`.

**I. Encoding.** CONTROL: 7878's `SlotNpc.ini` decodes strict GBK with 51
CJK characters and names `cq_npc`; 7387's is byte-identical. Of 7387's 215
top-level plaintext `ini/` files, 116 are ASCII-only, 77 decode strict GBK
with CJK, and 22 carry high bytes that are NOT GBK-with-CJK. So the content
is MOSTLY GBK but not uniformly. `TEXT_ENCODING` stays latin1, which
round-trips every byte and cannot break a control, and Chinese labels
display as mojibake. A KNOWN LIMIT, recorded rather than switched blind.

`DAT_CENSUS_ERA = False`, and the `tools/dat_census.py` slot stays
commented. That is NOT because the cipher is wrong: section D's control
reproduced, and 225 of 7387's `ini/**.dat` are block96. With `"7387"` in
BUILDS, `tests/test_dat_census.py` goes red (run 2026-09-19 08:08Z):
`test_a_file_on_one_build_is_reported_on_one_build` pins `combat_gear.dat`
to `['7189']`, and 7387 ships it too (`['7189', '7387']`). That test's
expectations are not edited here, so 7387 stays out of the census until the
pin is revisited.

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
RESULT on 7387: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 13 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1700    59    59     0      0     0    0
    Name=:npc:NpcX.ini              918     3     3     0      0     0    0
    Name=:npc:npc.ini              3166    44    22    22      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                233     3     3     0      0     0    0
    achievement                     371     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    npc:NpcX.ini                    918     3     3     0      0     0    0
    npc:npc.ini                    3141    22    22     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      233     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 18: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 844: `a1 a1` = '\u3000'

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
#: `version.dat` is exactly these four bytes (read 2026-09-19).
STAMP = "7387"

#: Inherited quirks that are FALSE on this install and are dropped, each with
#: the premise that fails. Most name a `.dbc`, and this install ships none.
DBC_ERA_QUIRKS = {
    "stale ini decoys": "premise is a compiled twin beside each ini; 0 .dbc",
    "a second motion reader": "premise is a 3dmotion.dbc twin; absent",
    "unpadded ids in compiled tables": "no compiled tables ship",
    "u32-wrapped motion ids": "the u32 keying is 3dmotion.dbc's; absent",
    "three fewer compiled tables": "6609's count against 6090; here 0",
    "mount.dbc shrank": "no mount.dbc ships",
    "the compiled tables froze at 6609": "there are none to freeze",
    "the per-weapon motion aliases are gone": "about 3dmotion.dbc; absent",
    "block coverage is not record coverage": "carries 7205's 86.1% / 2,068 "
                                             "figures; restated without them",
    "224 Lua scripts and no Lua grammar": "7205's counts; restated below",
    "shlayout.dat does not open": "a 6609 TQ-cipher statement; this file's "
                                  "family here is NOT MEASURED",
}


def _version(root: Path) -> str:
    """`version.dat`, stripped. `b'7387'` on this install."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7387(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1700, 59, 59, 0],
        'Name=:npc:NpcX.ini': [918, 3, 3, 0],
        'Name=:npc:npc.ini': [3166, 44, 22, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [233, 3, 3, 0],
        'achievement': [371, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'npc:NpcX.ini': [918, 3, 3, 0],
        'npc:npc.ini': [3141, 22, 22, 0],
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

    name = "patch7387"
    label = "Official patch client 7387"
    origin = "official"
    aliases = ("7387",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables; the archive switch and the 12 -> 0 .dbc step are at "
        "7275 -> 7280. Content is 7205's lineage: ini/*.dat in the 96-bit "
        "block cipher, read through the collection's block dictionary with "
        "7205's specs (every declared file ships). The ini/ name set is "
        "identical to 7373's. The art lookups are the frozen 2008 plaintext "
        "files with no compiled twin: 1,764 of 3,166 npc.ini rows (55.7%) "
        "resolve a simple object and 1,402 do not. MEASURED: 93.12% of "
        "1,872,422 block96 blocks are known, so every count is a floor: item "
        "22,621 (7205: 44,684), monster 1,680, magic 1,364, mount 1,875. "
        "award_config and levexp are refused."
    )

    # -- what the listing measured -----------------------------------------
    ARCHIVE_PAIRS = ("c3", "data")
    #: Sizes by listing. Same on 7280..7506; NOT hashed.
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: The WDF-to-TPD bracket, from `patch7280`'s listing of both sides.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables: `find ini -iname '*.dbc'` -> 0. The twelve 7205
    #: names are kept so the change is legible.
    FROZEN_COMPILED = ()
    COMPILED_GONE_SINCE_7275 = Patch7205.FROZEN_COMPILED

    #: The plaintext lookups, sha256 prefix, identical on 7205/7336/7373/
    #: 7387/7878. Live here only because nothing else exists.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }
    FROZEN_PART_TABLES = {
        "ini/armet.ini": "50e2e37ff080", "ini/weapon.ini": "11df4435553d",
        "ini/mount.ini": "aad7f0df81db", "ini/3DEffect.ini": "aa059fc7a8fc",
        "ini/3DEffectObj.ini": "3ba0aa169631",
    }
    PART_TABLES_DECLARED_NOT_SHIPPED = ("ini/armet1.ini", "ini/shield.ini",
                                        "ini/pelvis.ini")

    #: `npc.ini` headers against the frozen `3DSimpleObj.ini`. The instrument
    #: reproduces 7205's 1,600 and 7336's 1,723 exactly. No compiled figure,
    #: because there is no compiled table.
    NPC_COVERAGE = {"rows": 3166, "unique": 3163, "resolved_ini": 1764,
                    "unresolved": 1402, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    #: `find -iname '*.lua'` (recursive) and top-level `*.dll`.
    SCRIPT_SURFACE = {"lua": 346, "dll": 27, "lua_bytes": 13835390,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: Top-level `.dat` 7387 ships and 7205 does not. UNDECLARED: none has
    #: been decoded, and a spec is a promise to parse. (`renown.dat` is the
    #: one 7336 did not have.)
    NEW_DAT_SINCE_7205 = (
        "coat_color_rule.dat", "cop_task_type.dat", "cq_remote_prize_cfg.dat",
        "daoqi_dict_type.dat", "emoneyshopv2.dat", "month_card_type.dat",
        "new_shop_goods.dat", "new_shop_info.dat",
        "newslot_guanqia_cfg.dat", "renown.dat", "speffect_attribute.dat",
        "vendue_item.dat", "vendue_item_type.dat", "vendue_type.dat",
    )

    #: `ini/` entries 7205 ships and 7387 does not, besides the 12 `.dbc`.
    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    #: Files byte-identical to a neighbour's, sha256 prefix. Evidence that
    #: a measurement on the neighbour carries by identity, nothing more.
    IDENTICAL_TO = {
        "ini/itemtype.dat": ("bb137e03444b", ("7336", "7373")),
        "ini/levexp.dat": ("4482cfaaec7d", ("7205", "7336", "7373", "7878")),
        "ini/SlotNpc.ini": ("c4e23de07332", ("7205", "7336", "7373", "7878")),
        "ini/GameMap.dat": ("3dc98cfdf19e", ("7373",)),
        "ini/region.ini": ("318a8e4f3494", ("7336", "7373")),
    }

    # -- MEASURED by measure_7387.py (2026-09-19) --------------------------
    #: The census-era marker `tests/test_dat_census.py` asks (`is True`).
    #: FALSE, and the `tools/dat_census.py` slot stays commented, although
    #: 225 `ini/**.dat` ARE block96 and the census control reproduced. With
    #: "7387" in BUILDS, `tests.test_dat_census.TheVerdictIsPerBuild.
    #: test_a_file_on_one_build_is_reported_on_one_build` goes red:
    #: `combat_gear.dat` is on ['7189', '7387'], and the test pins it to
    #: ['7189'] only. That pin is the census's fact, not ours to edit
    #: (coordinator rule, 2026-09-19). Lifting it is a census decision.
    DAT_CENSUS_ERA = False
    #: THIS client's census, in the core six keys of
    #: `Patch7205.BLOCK96_CENSUS`. `block96_census(<7387>)` reproduced it.
    BLOCK96_CENSUS = {"files": 225, "blocks": 1872422, "unknown": 128815,
                      "fully_covered": 127, "partial": 96, "uncovered": 2}
    #: Of those 225 block96 files, byte-identical to 7373's.
    BLOCK96_IDENTICAL_TO_7373 = 182
    #: Same shape as `Patch7205.BLOCK96_COVERAGE`. `known` is the block
    #: fraction and `units` is what the reader serves. For sections the
    #: script printed units only, so `intact` is None there.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1178049, "known": 0.9361, "lines": 38430,
                         "intact": 22621, "units": 22621, "shape": "rows"},
        "monster.dat": {"blocks": 115392, "known": 0.9581, "lines": 103590,
                        "intact": None, "units": 2001, "shape": "sections"},
        "magictype.dat": {"blocks": 62573, "known": 0.9282, "lines": 2406,
                          "intact": 1364, "units": 1364, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 38597, "known": 0.9005, "lines": 899,
                            "intact": 158, "units": 158, "shape": "rows"},
        "mounttype.dat": {"blocks": 37861, "known": 1.0, "lines": 26367,
                          "intact": None, "units": 1875, "shape": "sections"},
    }
    #: `npcart.audit` (section G): (resolved, npcs). A different instrument
    #: from `NPC_COVERAGE`'s per-header count, so both are kept.
    NPC_AUDIT = {"plaintext": (1766, 3163), "official": (0, 3163)}
    #: Section C: appearance parts and how many resolve in the archives.
    APPEARANCE_RESOLUTION = {"parts": 13902, "mesh": 1556, "texture": 1072}
    #: Section I. Mostly GBK, not uniformly, over ALL ini/ files. SUPERSEDED
    #: for the declared tables' NAME fields: 0 strict failures, so
    #: TEXT_ENCODING is gbk (see the module docstring's last section).
    ENCODING_SURVEY = {"plaintext_ini": 215, "ascii_only": 116,
                       "gbk_with_cjk": 77, "high_bytes_not_gbk_cjk": 22}
    # -- 7205 measurements NOT re-taken on this build ----------------------
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    CENSUS_NAME_FALSE_POSITIVES = None
    #: MEASURED: the block96 files the dictionary serves no record of.
    UNCOVERED_TABLES = {
        "levexp.dat": "0.0% of 335 blocks known. Byte-identical (sha256 "
                      "4482cfaaec7d, 4,030 B) to 7205's and 7878's, whose "
                      "plaintext is held out of the dictionary by design.",
        "ServerPlay.dat": "0.0% of 493 blocks known (5,922 B, sha256 "
                          "4ec7fbba4cb6, a different file from 7205's and "
                          "7878's).",
        "award_config.dat": "28.6% of 304 blocks known, 0 whole rows: "
                            "REFUSED (7205 reads 72 rows). Byte-identical "
                            "to 7373's.",
    }
    #: 7205's fourteen refused names; all fourteen ship here. The reasons are
    #: 7205's measurements and are marked so.
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7387] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7387` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. No format probe separates 7387
        from 7280..7506: same `.tpd` sizes, no `.dbc`, the same frozen
        lookups, and an `ini/` name set identical to 7373's. A format claim
        would take every unplugged sibling silently; the stamp takes one.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`: every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none. The same answer `patch7878`
        gives for the same shape."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install to prefer."""
        return False

    def import_plan(self, root, exists) -> dict:
        """The DatPkg indexes by name, and no claim that they are shared.

        The inherited WDF names are absent here, and
        `assetdiff.catalog_baseline` skips an absent archive silently.
        `sharedArchives` stood for "the byte-identical 2005 WDF pair", which
        is not this install; whether 7387's `.tpd` pair equals its siblings'
        is NOT MEASURED (same sizes is not a hash).
        """
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": [f"{s}.tpi" for s in self.ARCHIVE_PAIRS],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: DatPkg archives (c3/data .tpi+.tpd, no "
                     f".wdf) indexed, loose layer imported, block96 "
                     f"ini/*.dat read through the dictionary; no compiled "
                     f"tables exist"),
        })
        return plan

    def colour_provenance(self):
        """The colour sets are 6090's, via 6609 and 7205, read on the WDF
        pair. This install ships `.tpd`, and nobody has looked at it on
        screen."""
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7387's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7387's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7387] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7387: Action3DEffect keys are (3,4,3,3) x1,867, "
            "(4,4,3,3) x777, (1,4,3,1) x184 -- the action field is four wide "
            "as on 6090, 7205 and 7336, not three as in the plaintext family.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear. core/coassets."
            "AssetRoot discovers both containers; a plan or tool that names "
            "c3.wdf finds nothing and may not say so.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,166 headers; 1,764 resolve a simple object and 1,402 do not. "
            "Nothing raises for an unresolved row.")
        q["block coverage is not record coverage"] = (
            "A block96 record is served only if every 12-byte block it spans "
            "is in the dictionary, so block coverage is a ceiling on record "
            "coverage, never the same number. MEASURED on 7387: itemtype.dat "
            "93.61% of blocks known, 22,621 of 38,430 lines whole (59%). "
            "Every count this plugin serves for a block96 table is a FLOOR.")
        q["346 Lua scripts and no Lua grammar"] = (
            "346 .lua (13,835,390 B) and 27 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables; "
            "tools/clientscripts.py and tools/scriptreport.py read it.")
        return q

    def why_no_tables(self) -> str:                       # pragma: no cover
        return ("7387 declares tables; if you are reading this the install "
                "has no ini/ directory")


PLUGIN = Patch7387()
