#!/usr/bin/env python3
r"""
patch7320 -- official Conquer Online patch 7320: 7205's tables in the
`.tpd`-only client, with no compiled `.dbc` behind the art lookups.

7320 is the THIRD client after the container switch. **7275 is the last
`.wdf` client and 7280 the first `.tpd`-only one**, and the twelve compiled
`.dbc` tables go from 12 to 0 at that same step. 7320 stays on the `.tpd` side
of it. This plugin claims exactly the stamp `7320` and nothing else.

WHAT IS MEASURED (directory listings and small plaintext files, 2026-09-19)
---------------------------------------------------------------------------
The machine was exclusive while this was written, so only listings and small
files were read. Every number below comes from those reads.

    install  archives                          ini/*.dbc  ini/*.dat  ini/*.lua
    7205     c3.wdf data.wdf                         12        147         99
    7275     c3.wdf data.wdf                         12        150        118
    7280     c3.tpd/.tpi data.tpd/.tpi                0        150        119
    7320     c3.tpd/.tpi data.tpd/.tpi                0        156        125
    7336     c3.tpd/.tpi data.tpd/.tpi                0        160        132
    7878     c3 data c31 data1 (.tpd/.tpi)            0        200          -

* **Archives: two DatPkg pairs, no WDF, no overlay.** 7320 ships `c3.tpd`
  (1,076,058,840 B) + `c3.tpi` (3,306,363 B) and `data.tpd` (896,279,080 B) +
  `data.tpi` (5,724,770 B). The `c31`/`data1` overlays arrive at 7867, not
  here. `AssetRoot._discover_archives` already finds `.tpd` pairs (it opened
  7878 first), so this is a fact to record, not code to write.
* **No compiled tables.** `ini/` has 0 `.dbc`, against 12 on 7205 and 7275.
  The 7205-only names in `ini/` are exactly the 12 `.dbc` plus
  `Action3DEffect1..5.ini`, `emoneyshop.ini` and `strres.ini`.
* **The plaintext art lookups are the frozen 2008 files.** sha256 prefixes,
  identical on 7205, 7275, 7280, 7320, 7336 and 7878:

      3DSimpleObj.ini f34f5633   3dobj.ini   9202de93   3dtexture.ini 6be2fece
      3dmotion.ini    9951727a   armor.ini   8e64691e   3DEffect.ini  aa059fc7
      3DEffectObj.ini 3ba0aa16   weapon.ini  11df4435   armet.ini     50e2e37f

  `npc.ini` did NOT freeze: 558,037 B at 7205, 665,948 B here (sha
  `fed39a53`), 900,246 B at 7878. That is `patch7878`'s shortfall arriving
  early: a growing npc table over frozen lookups, **with no compiled twin
  behind them.**
* **The `.dat` set is 7205's plus nine.** No `.dat` 7205 ships is missing
  here. New at 7320: `cq_remote_prize_cfg`, `emoneyshopv2`, `month_card_type`,
  `new_shop_goods`, `new_shop_info`, `speffect_attribute`, `vendue_item`,
  `vendue_item_type`, `vendue_type`. None of the nine is declared. The
  measurement found four of them are 0-byte files (`cq_remote_prize_cfg`,
  `vendue_item`, `vendue_item_type`, `vendue_type`). The other five are
  block96 and partly known (`emoneyShopV2` 94.29%, `new_shop_goods` 78.33%,
  `speffect_attribute` 77.59%, `new_shop_info` 66.67%, `month_card_type`
  60.71%).
* **Content tables changed.** `itemtype.dat` 12,966,331 B at 7205 ->
  13,895,418 B here; `monster.dat`, `MagicType.dat`, `mounttype.dat` and
  `ItemtypeSub.dat` all differ in size and hash from both 7205's and 7280's.
* **`Action3DEffect.ini` keys a four-wide action field on 2,621 of 2,621
  keyed rows** (2,186 of them `.9999.`). That matches `Patch6090.FIELD_WIDTHS`
  (`action: 4`), which is inherited here. `PlaintextFamily` declares 3, and
  that is one reason this is NOT a plaintext-family subclass. (7878's own
  file also measures 4,197 of 4,197 four-wide, while `patch7878` inherits the
  family's 3. That is recorded as a finding and not touched in this file.)
* `SlotNpc.ini` is byte-identical (sha `c4e23de07332`) on 7205, 7320 and
  7878, and `codepage.ini` is the same 4 bytes (`1256`) on all three.

WHY THE BASE CLASS IS `Patch7205`, AND WHAT IS OVERRIDDEN
-----------------------------------------------------------
7320 matches two predecessors on two different layers:

    layer                      7205 (Patch7205)          7878 (Patch7878)
    ini/*.dat reader           block96 specs, spec-      7878's own DERIVED
                               driven, rooted at the     tree (derived/7878-dat-
                               install                   decrypted), 7878's bytes
    art lookups                .dbc twin (PROFILE_       plaintext .ini (PROFILE_
                               OFFICIAL)                 PLAINTEXT)
    archives                   c3/data .wdf              .tpd pairs

**The table layer decides the base.** It is most of either class, and
7878's version cannot be reused: `Patch7878.catalogs()` reads 7878's own
decrypted files from `derived/7878-dat-decrypted/`, so on 7320 it would serve
**7878's** item/monster/mount rows as this client's. That would be a
confidently wrong answer. `Patch7205`'s specs read the install's own bytes
through `core/block96.py`, filtered to the files on disk. The art layer is
three hooks, so it is the part that gets overridden:

* `confidence` -- the exact stamp `7320` at 0.95, 0.0 otherwise. This is the
  `patch7205`/`patch6609` rule.
* `table_profile` -> `npcart.PROFILE_PLAINTEXT`, and
  `prefers_compiled_tables` -> False. `PROFILE_OFFICIAL` names
  `ini/3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc` and `3dmotion.dbc`, and
  this install ships none of them. On 5017-5165, which also ship no `.dbc`,
  `npcart`'s own table records the wrong profile at 0 resolved rows
  (`0 / 503` on 5017). So the inherited answer is not a weaker answer, it is
  no answer.
* `import_plan` -- archives `c3.tpi`/`data.tpi` (`assetdiff._iter_archive`
  opens either suffix). The inherited plan names `c3.wdf`/`data.wdf`, which
  do not exist here, and claims them "byte-identical across the lineage",
  which is a WDF-era fact.
* `table_quirks` -- drops the inherited quirks that are about the `.dbc`
  twin or quote 7205's counts, and adds 7320's own.
* `colour_provenance` -- names the extra hop and the container change.
* `monster_colourways` -- declines. 0 of 6090's 115 colourway textures
  exist here (measured).
* The 7205 MEASUREMENT constants. `BLOCK96_COVERAGE`, `BLOCK96_CENSUS`,
  `NPC_COVERAGE` and `UNCOVERED_TABLES` carry 7320's own measured numbers.
  `SCRIPT_SURFACE`, `ITEM_COLUMN_AGREEMENT`, `BROWSE_IMPACT` and
  `RUNEEFFECT_REPEATS` are None: they are 7205's numbers and were not
  re-measured here. `FROZEN_COMPILED` is `()`, MEASURED: none ship.

MEASURED BY `measure_7320.py` (run by the Director, 2026-09-19, head
`7fe52c2d`, EXIT 0, 37 s). Every number is 7320's unless it names 7205.
---------------------------------------------------------------------------
**Detection, 48 installs** (every `Clients/*` folder plus the live CCO
install), `plugins.rank()` with and without this plugin: **only 7320
changes winner**, `plaintext (0.50, +0.50)` -> `patch7320 (0.95, +0.45)`.
patch7320 scores 0.00 on the other 47. rank() raised on none.

**Containers**: `_discover_archives` -> `c3.tpi` (3,306,363 B), `data.tpi`
(5,724,770 B). 7280's `.tpi` pair has the same two sizes. 7275's `c3.wdf`
is 359,069,116 B, the same size as 7205's.

**`ini/*.dat` IS block96, MEASURED** (`core/inidat.classify` on every file):
106 block96, 11 rar-mangled, 10 rsa-mysqldump, 10 empty, 7 plaintext, 6
binary-plain, 3 tq-stream, 2 unknown, 1 shift-obfuscated.

**block96 coverage. The positive control ran first**: 7205 read 101 files,
1,488,616 blocks, 809 unknown (99.95%), 99 fully covered, 0 partial, 2
uncovered. That reproduces `Patch7205.BLOCK96_CENSUS` exactly, so the control
HOLDS. On 7320:

    106 files, 1,711,966 blocks, 88,969 unknown -> 94.80% known
     70 fully covered   34 PARTIAL   2 uncovered (levexp.dat, ServerPlay.dat)

    file              blocks     known    lines   intact   units   shape
    itemtype.dat   1,157,951    94.84%   39,341   26,101  29,063   rows
    monster.dat      116,930    94.45%  103,633   99,488   1,398   sections
    MagicType.dat     54,160    91.89%    2,003    1,022   1,117   rows
    ItemtypeSub.dat   20,709    88.74%      516      130     191   rows
    mounttype.dat     37,271   100.00%   25,961   25,961   1,846   sections

**Block coverage is not record coverage, and here the gap is real**:
94.84% of `itemtype.dat`'s blocks give 26,101 whole rows, against 44,684 on
7205's fully-known file. `monster` gives 1,028 against 7205's 3,670. The
7878-derived dictionary has not seen every block 7320 carries. These are
KNOWN LIMITS of the dictionary, recorded as floors, not tuned away.

**catalogs()**, Patch7205 on 7205 (control) against Patch7320 on 7320:

    subject       7205 rows   7320 rows
    item             44,684      26,101    partial dictionary, see above
    monster           3,670       1,028    partial
    magic             2,907       1,022    partial
    item:sub            633         130    partial
    mount             1,819       1,846    whole file
    map:dest            936         968
    magic:op            979         947
    item:value        1,745       1,808
    gamemap             578         609    raw control

240 subjects, 237 ok. The three that are not: `action` (binary record
layout, as on 7205), `levexp` (refused by name, as on 7205) and
**`award_config` -- LOST**: 28.6% of its 304 blocks are known and not one
record comes out whole. 7205 reads 72 rows from its copy.

**Appearance tables (`part_tables`) COLLAPSE TO THE 2008 FILES.** With no
`.dbc` twin, every slot reads its plaintext `.ini`:

    slot       7205 (from .dbc)   7320 (from .ini)
    body            3,754               955
    armet           2,793             1,168
    weapon         13,292             4,828
    mount           1,223                 1   (ini/Mount.ini, a 49-byte stub)

That is the install's own content, and the reader serves what the client
ships. 7320 carries no newer appearance table in `ini/`. Whether the
client keeps one somewhere else (for example inside the `.tpd`) is NOT
MEASURED.

**The npc chain, both profiles** (the predecessor-fails arm, measured):

    7205  PROFILE_OFFICIAL   2,466 / 2,904 resolved   (control)
    7205  PROFILE_PLAINTEXT  1,601 / 2,904
    7320  PROFILE_OFFICIAL       0 / 3,080 resolved   <- Patch7205's profile
    7320  PROFILE_PLAINTEXT  1,714 / 3,080, 1,197 name geometry the
                                             archives do not ship

**The inherited 6090 colourways do not hit**: 0 of `Patch6090.MONSTERS`'s
115 textures exist in 7320's archives, against 115/115 on 7205. So
`monster_colourways` is overridden to decline. The other 6090 art hooks are
exists-guarded and still INHERITED, NOT MEASURED.

STILL INFERRED
--------------
* 7205's spec shapes and name columns (`SPECS_7205`, `SPECS_7205_CENSUSED`),
  filtered to the files 7320 ships. The tables that read whole read under
  them, but no column was re-measured on 7320's bytes.
* `TEXT_ENCODING` latin1, inherited. `SlotNpc.ini` is byte-identical to
  7878's, and that file measured GBK there.
* 6609's `.ini` grammar census, borrowed through `Patch7205.table_specs`.

**`DAT_CENSUS_ERA = False`, although the cipher IS block96.** The census
does not cover this build yet, for two reasons. 34 of its 106 block96 files
are partial, and `tests/test_dat_census.
test_a_file_on_one_build_is_reported_on_one_build` asserts that
`combat_gear.dat` ships on 7189 alone among the non-7205 builds, and 7320
ships it too (measured, 2,840 blocks). Adding "7320" to
`dat_census.BUILDS` would break that shared test, and this PR does not edit
it. The Director decides.

WHAT THIS PLUGIN DOES NOT CLAIM
-------------------------------
* No colour, name or socket evidence of its own. Nobody has looked at 7320
  on screen.
* Nothing about the nine new `.dat`, the 125 `.lua` or the new `.ini`
  (`ActionRole3DEffect.ini`, `ActionSoundEx.ini`, `ShaderParam.ini`,
  `AppQDKey.ini`, `operateweben.ini`, `cq_remote_prize_cfg.ini`).
* No membership. The install is undeclared and stays that way until the
  owner decides.

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
RESULT on 7320: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1033     6     6     0      0     0    0
    Name=:npc:NpcX.ini              875     3     3     0      0     0    0
    Name=:npc:npc.ini              3085    48    23    25      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                211     3     3     0      0     0    0
    achievement                     166     1     0     0      0     1    0
    classdesc                       123    25    25     0      0     0    0
    map:dest                        968     7     6     0      0     1    0
    npc:NpcX.ini                    875     3     3     0      0     0    0
    npc:npc.ini                    3055    23    23     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      211     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 959: `bf f1 b1 a9 c5 a3 c4 a7 cd f5` = '狂暴牛魔王'
    Name=:npc:npc.ini 812: `a1 a1` = '\u3000'

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

from plugins import Plugin                             # noqa: E402
from plugins.patch7205 import Patch7205, _version      # noqa: E402


#: The twelve compiled tables 7205 ships and this build does not. The listing
#: is the evidence: `ls <Clients>/7320/ini/*.dbc` is empty.
DBC_7205 = ("armet.dbc", "armor.dbc", "weapon.dbc", "mount.dbc",
            "3DSimpleObj.dbc", "3DObj.dbc", "3DTexture.dbc", "3dmotion.dbc",
            "3DEffect.dbc", "3DEffectobj.dbc", "mountmotion.dbc",
            "weaponmotion.dbc")


class Patch7320(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1033, 6, 6, 0],
        'Name=:npc:NpcX.ini': [875, 3, 3, 0],
        'Name=:npc:npc.ini': [3085, 48, 23, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [211, 3, 3, 0],
        'achievement': [166, 1, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'map:dest': [968, 7, 6, 0],
        'npc:NpcX.ini': [875, 3, 3, 0],
        'npc:npc.ini': [3055, 23, 23, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [211, 3, 3, 0],
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

    name = "patch7320"
    label = "Official patch client 7320"
    origin = "official"
    aliases = ("7320",)
    STAMP = "7320"

    notes = (
        "Third .tpd-only official client (7275 is the last .wdf one, 7280 the "
        "first .tpd-only one). Ships two DatPkg pairs (c3, data), no WDF, and "
        "0 compiled .dbc tables against 7205's 12. So the npc chain runs "
        "through the plaintext lookups, which are the frozen 2008 files, "
        "byte-identical to 7205's and 7878's, while npc.ini grew to 665,948 "
        "bytes. ini/*.dat is block96 (106 files) and is read through 7205's "
        "specs from this install's own bytes. The 7878-derived dictionary "
        "knows 94.80% of its blocks, with 34 files partial: itemtype gives "
        "26,101 whole rows (7205: 44,684), monster 1,028 (3,670), and "
        "award_config nothing. The appearance tables fall back to the 2008 "
        ".ini (body 955 rows against 7205's 3,754 from .dbc; mount 1)."
    )

    #: DatPkg, not WDF. MEASURED by listing: `c3.tpd`/`c3.tpi` and
    #: `data.tpd`/`data.tpi`, and no `c31`/`data1` overlay (those arrive at
    #: 7867).
    ARCHIVE_PAIRS = ("c3", "data")

    #: The inherited plaintext lookups, byte-identical (sha256, first 8 hex) on
    #: 7205, 7275, 7280, 7320, 7336 and 7878. They are the same 2008 files
    #: `patch7878.FROZEN_LOOKUPS` records, and those 12-hex prefixes start with
    #: these 8. Here there is no compiled twin behind them.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f5633",
        "ini/3dobj.ini": "9202de93",
        "ini/3dtexture.ini": "6be2fece",
        "ini/3dmotion.ini": "9951727a",
        "ini/armor.ini": "8e64691e",
    }

    #: MEASURED: none of 7205's twelve `.dbc` ship. Also absent: the five
    #: `Action3DEffect<n>.ini` splits, `emoneyshop.ini` and `strres.ini`.
    FROZEN_COMPILED = ()
    ABSENT_HERE = tuple(f"ini/{n}" for n in DBC_7205) + tuple(
        f"ini/{n}" for n in ("Action3DEffect1.ini", "Action3DEffect2.ini",
                             "Action3DEffect3.ini", "Action3DEffect4.ini",
                             "Action3DEffect5.ini", "emoneyshop.ini",
                             "strres.ini"))

    #: New `.dat` at 7320 against 7205, MEASURED by listing. Undeclared, and
    #: named here so they are not silent.
    NEW_DAT_SINCE_7205 = ("cq_remote_prize_cfg.dat", "emoneyshopv2.dat",
                          "month_card_type.dat", "new_shop_goods.dat",
                          "new_shop_info.dat", "speffect_attribute.dat",
                          "vendue_item.dat", "vendue_item_type.dat",
                          "vendue_type.dat")

    # -- 7205's measurements are NOT this build's -------------------------
    #: The census MARKER (`tests/test_dat_census.py` checks `is True`).
    #: False although the cipher IS block96 -- see the module docstring: 34
    #: partial files, and a shared census test pins `combat_gear.dat` to
    #: 7189, while 7320 ships it too. The Director decides whether the
    #: census covers this build.
    DAT_CENSUS_ERA = False

    #: MEASURED on 7320 (`measure_7320.py`), same keys as 7205's.
    #: `known` is rounded to the 4 decimals the run printed.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1157951, "known": 0.9484, "lines": 39341,
                         "intact": 26101, "units": 29063, "shape": "rows"},
        "monster.dat": {"blocks": 116930, "known": 0.9445, "lines": 103633,
                        "intact": 99488, "units": 1398, "shape": "sections"},
        "magictype.dat": {"blocks": 54160, "known": 0.9189, "lines": 2003,
                          "intact": 1022, "units": 1117, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 20709, "known": 0.8874, "lines": 516,
                            "intact": 130, "units": 191, "shape": "rows"},
        "mounttype.dat": {"blocks": 37271, "known": 1.0, "lines": 25961,
                          "intact": 25961, "units": 1846,
                          "shape": "sections"},
    }
    #: MEASURED on 7320, same shape as 7205's. The two identical-to-7878
    #: splits were NOT measured here: None.
    BLOCK96_CENSUS = {"files": 106, "blocks": 1711966, "unknown": 88969,
                      "fully_covered": 70, "partial": 34, "uncovered": 2,
                      "fully_covered_identical_to_7878": None,
                      "fully_covered_and_different": None}
    #: MEASURED: the npc chain through `npcart.audit`, both profiles.
    NPC_COVERAGE = {"rows": 3080, "resolved_plaintext": 1714,
                    "resolved_official_profile": 0,
                    "geometry_missing_on_disk": 1197}
    #: MEASURED: not one block of either is known.
    UNCOVERED_TABLES = {
        "levexp.dat": "335 blocks, 0 known -- the same block count as "
                      "7205's copy; byte identity NOT measured here.",
        "ServerPlay.dat": "485 blocks, 0 known (7205's copy is 474, so "
                          "this is a different file).",
    }
    #: MEASURED: record-level losses the dictionary causes on this build.
    LOST_SUBJECTS = {
        "award_config": "28.6% of 304 blocks known, 0 whole records "
                        "(7205: 72 rows).",
    }
    #: MEASURED `part_tables()` row counts: the 2008 `.ini`, no twin.
    PART_TABLE_ROWS = {"body": 955, "armet": 1168, "r_weapon": 4828,
                       "l_weapon": 4828, "mount": 1, "head": 0, "misc": 0}
    SCRIPT_SURFACE = None
    ITEM_COLUMN_AGREEMENT = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7320`, and nothing else.

        The format probes cannot separate 7280..7589: all ship the same two
        `.tpd` pairs, no `.dbc`, and the same frozen plaintext chain. So the
        stamp decides, as for 6609 and 7205. 0.95 beats the `plaintext`
        family's 0.5 bid for an unrecognised plaintext chain. This plugin does
        NOT reach the other `.tpd`-only siblings: none of them is measured
        here.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- art tables --------------------------------------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`, because the install ships no `.dbc`.

        The inherited `PROFILE_OFFICIAL` reads the npc chain from
        `ini/3DSimpleObj.dbc` / `3DObj.dbc` / `3DTexture.dbc` /
        `3dmotion.dbc`, and 7320 has none of the four. `npcart`'s own record
        for that mismatch is `0 / 503` resolved on 5017. The plaintext profile
        reads the `.ini` chain the install does ship. Those files are frozen
        (see `FROZEN_LOOKUPS`), so expect `patch7878`-shaped shortfalls, not
        7205's.
        """
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin to prefer. See `table_profile`."""
        return False

    # -- library import ----------------------------------------------------
    def import_plan(self, root, exists) -> dict:
        """The DatPkg pair, not the WDF pair.

        `Patch6090.import_plan` names `c3.wdf`/`data.wdf` and marks them
        shared across the lineage. Both halves are false here: neither file
        exists, and nothing has measured whether 7320's `.tpd` pair is shared
        with any sibling. So this starts from `Plugin`'s default and replaces
        the archive list. `tools/assetdiff._iter_archive` opens a `.tpi` by
        suffix.
        """
        plan = Plugin.import_plan(self, root, exists)
        plan.update({
            "archives": [f"{s}.tpi" for s in self.ARCHIVE_PAIRS],
            "tables": ["ini/"],
            "note": f"{self.label}: DatPkg archives c3/data indexed, loose "
                    f"layer imported, plaintext ini lookups read (no "
                    f"compiled .dbc ships), ini/*.dat via block96",
        })
        return plan

    # -- quirks ------------------------------------------------------------
    #: Inherited quirks that are FALSE on this build, or that quote 7205's
    #: counts as if they were this build's. Each one is about the compiled
    #: twin, which is absent here, or about 7205's measurements.
    _QUIRKS_NOT_HERE = (
        "stale ini decoys",                 # the .ini IS the live table here
        "unpadded ids in compiled tables",  # no compiled tables
        "u32-wrapped motion ids",           # 3dmotion.dbc does not ship
        "three fewer compiled tables",      # 12 -> 0, not 15 -> 12
        "mount.dbc shrank",
        "the compiled tables froze at 6609",
        "ini/*.dat is the 6907-7878 block cipher, not TQ",  # 7205's counts
        "block coverage is not record coverage",            # 7205's counts
        "224 Lua scripts and no Lua grammar",               # 7205's counts
    )

    def table_quirks(self):
        q = super().table_quirks()
        for k in self._QUIRKS_NOT_HERE:
            q.pop(k, None)
        q["no compiled tables: the frozen plaintext lookups are live"] = (
            "0 .dbc in ini/, against 12 at 7205 and 7275; they vanish at "
            "7280, the same step where .wdf becomes .tpd. 3DSimpleObj.ini, "
            "3dobj.ini, 3dtexture.ini, 3dmotion.ini and armor.ini are "
            "byte-identical to 7205's and 7878's (2008 files), while npc.ini "
            "grew to 665,948 bytes. Expect npc rows that name objects the "
            "frozen table never heard of; nothing raises for them.")
        q["DatPkg archives, not WDF"] = (
            "c3.tpd/.tpi and data.tpd/.tpi, no c3.wdf/data.wdf and no "
            "c31/data1 overlay. Names resolve through the .tpi index, not "
            "through a recovered hash table.")
        q["the block dictionary knows 94.80% of this build's blocks"] = (
            "106 block96 .dat, 1,711,966 blocks, 88,969 unknown; 70 whole, "
            "34 partial, 2 unreadable (levexp, ServerPlay). A record is "
            "served only if EVERY block it spans is known, so itemtype.dat "
            "at 94.84% of blocks gives 26,101 whole rows where 7205 gives "
            "44,684, monster 1,028 against 3,670, and award_config nothing. "
            "Every count here is a FLOOR set by the dictionary, not by the "
            "client.")
        q["appearance tables are the 2008 .ini"] = (
            "With no .dbc twin, part_tables() reads armor.ini (955 rows; "
            "7205's armor.dbc has 3,754), armet.ini 1,168 (2,793), "
            "weapon.ini 4,828 (13,292) and Mount.ini, a 49-byte stub with 1 "
            "row (1,223).")
        return q

    def monster_colourways(self, ident):
        """Declines. MEASURED: 0 of `Patch6090.MONSTERS`'s 115 textures exist
        in 7320's archives (115/115 on 7205), so the inherited sets name
        files this client does not ship."""
        return None

    def colour_provenance(self):
        """Two hops further than 6609, and a container change.

        The sets are 6090's, scanned on WDF archives that stay byte-identical
        up to 7275. 7320's art sits in `.tpd` archives with different bytes,
        and nobody has looked at it on screen.
        """
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any; 7320's art is a .tpd container)", "inferred")


PLUGIN = Patch7320()
