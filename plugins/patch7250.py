#!/usr/bin/env python3
r"""
patch7250 -- official Conquer Online patch 7250: 7205's client, three stamps
later, still `.wdf`, still twelve `.dbc`, still the 96-bit block cipher.

A THIN SUBCLASS OF `Patch7205`, and the reason is the FILE LISTING, not the
patch number. Everything below is labelled MEASURED (read off the install on
2026-09-19, by listing, size, or sha256 of small files), INFERRED (follows
from a measurement plus a stated argument), or NOT MEASURED. The heavy half
came from `measure_7250.py` (archives, decodes, rank over every install),
run once on a quiet machine; its section below carries those numbers. **Nothing here says "unchanged" about a
thing that was not compared.**

MEASURED -- identification
--------------------------
* `version.dat` is `b"7250\r\n"`, SIX bytes. 7205's is four bytes, `b"7205"`,
  with no line ending. `_version()` strips, so both read as their stamp; the
  test pins the raw bytes so a later "clean-up" of `_version` that stops
  stripping is caught.

MEASURED -- containers (directory listing and file sizes)
---------------------------------------------------------
* Top level ships exactly `c3.wdf` (359,069,116 B) and `data.wdf`
  (392,245,257 B) -- **byte-for-byte the same SIZES as 7205's**, both dated
  2005-10-18. No `.tpi` / `.tpd` anywhere at the top level. 7250 is a
  `.wdf`-only client; the `.wdf` -> `.tpd` switch is later (7275 is the last
  `.wdf` client, 7280 the first `.tpd`-only one, per the plan's inventory --
  not re-measured here).
* **The twelve `ini/*.dbc` are sha256-identical to 7205's** (and therefore to
  6609's, which 7205's docstring measured): armet 9415fee5e4b7, armor
  d50f726935fa, weapon a07bae8d701d, mount ebc89e4ece32, 3DSimpleObj
  7159b91c0eb7, 3DObj 041b1060bd18, 3DTexture 0882236874e5, 3dmotion
  565a3c34e4bd, 3DEffect 29e5ab3aed58, 3DEffectobj 063b4121696c, mountmotion
  a2509814d75b, weaponmotion 5791de723722. `FROZEN_COMPILED` is inherited
  because it is TRUE here, not because it was assumed.

MEASURED -- `ini/` against 7205, file by file (sha256, 0.36 s CPU)
------------------------------------------------------------------
    488 files in 7250/ini (471 in 7205/ini, 0 removed)
    352 byte-identical to 7205's   (111 .dat, 157 .ini, 12 .dbc, 68 .lua)
    119 differ                     ( 36 .dat,  51 .ini,  0 .dbc, 31 .lua)
     17 new                        (  2 .dat,   3 .ini,  12 .lua)

* **`itemtype.dat` is byte-identical to 7205's** (sha256 d9da243380c7), as
  are `levexp.dat` (4482cfaaec7d, 7878's copy too), `texas_match_prize.dat`,
  `Action.dat`, `region.ini`, `EventTypeName.ini`, `VipTrans.ini`,
  `restrain.ini`, `npcex.ini`, `SlotNpc.ini` and three of the four plaintext
  section tables (`SkinVersion.dat`, `GetPlatformInfo.dat`,
  `ndloginServer.dat`). The fourth, `Shop.dat`, DIFFERS.
* The 36 `.dat` that differ include most of the content tables the base
  declares: `monster.dat` (1,237,358 -> 1,250,060 B), `mounttype.dat`
  (440,631 -> 445,053), `MagicType.dat` (572,761 -> 578,308),
  `MapDestination.dat`, `AutoUseMagic.dat`, `GameMap.dat` (binary-plain),
  `Achievement.dat` (34,202 -> 34,114, SMALLER), `ServerPlay.dat`
  (5,696 -> 5,757), `GameLoadInfo.dat` (991 -> 1,016) and
  **`ItemtypeSub.dat`, 207,838 -> 709,372 bytes -- 3.4x**, the largest move
  in the listing and the first table to look at when the measurement lands.
* The two new `.dat` -- `cq_remote_prize_cfg.dat` and `month_card_type.dat`
  -- are **0-byte files**, like the six 7205 already ships. The new `.ini`
  are `cq_remote_prize_cfg.ini`, `Appqdkey.ini` and `ShaderParam.ini`; none
  is declared (no census of this build exists).
* `npc.ini`, `NpcX.ini`, `terrainnpc.ini`, `RolePart.ini` and `c3.wdb`
  differ, so appearance tables (`part_tables()`) and the npc resolution rate
  against the frozen `3DSimpleObj.dbc` are NOT 7205's numbers.
* Script surface: **241 `.lua`** anywhere under the install (7205: 224), and
  **38 top-level `.dll`** (7205: 37; the new one is `WordsCheck.dll`).
  `ini/` grew two new sub-folders, `luagui/` and `luaui/`.

INFERRED
--------
* **The cipher.** Every block96 `.dat` that is byte-identical to 7205's
  (111 of them, `itemtype.dat` among them) decodes EXACTLY as 7205's does
  under the same dictionary -- same bytes, same deterministic reader. So
  `item` here is 7205's 44,684 rows, and `levexp.dat` is refused for the same
  reason. For the 36 that differ, the family is inferred from 7205 (the era
  is 6907..7878) and `DeclaredCodecs` in the test re-checks it per file
  through `core/inidat.classify` rather than trusting the inheritance.
* **The specs.** `SPECS_7205` + `SPECS_7205_CENSUSED` are inherited,
  filtered to disk by `Patch7205.table_specs` (7250 removes no file, so the
  filter drops nothing). Their `columns` were measured on 7205's bytes; for
  the 111 identical `.dat` they are 7250's by identity, for the changed ones
  they are an inference `measure_7250.py` checks by catalog row counts.
* **`ROW_COLUMNS`** (region/eventtypename/viptrans/restrain) -- all four
  source files are sha256-identical to 7205's, so 7205's measured columns
  hold by identity.

MEASURED 2026-09-19 by `measure_7250.py` (run 07:52Z, EXIT 0, 26 s)
-------------------------------------------------------------------
POSITIVE CONTROL FIRST: the same census on 7205 reproduced
`Patch7205.BLOCK96_CENSUS` exactly (101 files, 1,488,616 blocks, 809 unknown,
99 full / 0 partial / 2 zero; itemtype 44,684 of 44,684) against the
4,059,815-block dictionary. So the low numbers below are about 7250, not a
broken instrument.

* **Detection, every install on disk (45, incl. `Classic Conquer 2.0`):
  ONLY 7250 changes winner** -- `patch6907` (margin 0.100) without this
  plugin, `patch7250` (margin 0.400) with it. `patch7250` scores 0.00 on the
  other 44. No `rank()` raised.
* **Containers:** `_discover_archives(7250)` = `c3.wdf`, `data.wdf`, and both
  are **byte-identical to 7205's** (blake2b-128 3c1c592e... and 121c76ac...).
* **block96 on 7250, TWO SCOPES -- do not mix them.**
  - `ini/*.dat` (top level, `measure_7250.py` section 4): 100 files,
    1,534,975 blocks, 21,113 unknown; 78 full, 21 partial, 1 at zero.
  - `ini/**.dat` (recursive, `block96_census()`, the SAME scope as 7205's
    pinned 101 -- measured by `tests/test_patch7250.Block96Census` on its
    first run, 08:07Z): **121 files, 1,541,007 blocks, 22,463 unknown;
    87 full, 33 partial, 1 at zero.** The extra 21 files and 12 partials
    are in `ini/` sub-folders. `BLOCK96_CENSUS` carries THIS scope.
  The one at zero is `levexp.dat`, byte-identical to 7205's and held out by
  design. 7205 had NO partial table; 7250 has 33. The top-level 21 are all
  among the 36 `.dat` whose bytes changed. Blocks known is NOT rows kept:

      7250 file                   blocks   known   lines  intact   units
      itemtype.dat             1,080,527 100.00%  44,684  44,684  44,684
      ItemtypeSub.dat             59,114  90.43%   1,587     476     733
      MagicType.dat               48,192  96.15%   2,405   2,003   2,052
      monster.dat                104,171  94.64%  92,526  88,947   1,197 sec
      mounttype.dat               37,087 100.00%  25,835  25,835   1,837 sec
      coat_storage_type.dat        2,727  61.06%     586     107     278
      exchange_shop_goods.dat     11,050  80.72%     801     262     478
      battlepass_score_reward        414  47.83%     101      28      72
      process_task.dat               992  83.17%     101      32      67

  **`ItemtypeSub.dat` shows the block-vs-record gap: 90.4% of blocks, 30.0%
  of lines whole.**
* **`ServerPlay.dat` classifies `unknown` here** (7205's copy: `block96`,
  0 of 474 blocks known), so it is no longer in the block96 count at all.
* **catalogs() on 7250 against 7205's real rows** -- 247 subjects, 245 with
  rows. KNOWN LIMITS (dictionary gaps on changed files; records spanning an
  unknown block are dropped rather than guessed -- the opposite of
  noise-as-rows: every one is BELOW 7205, none above):

      subject                  7205    7250      subject                7205  7250
      item                   44,684  44,684      monster               3,670   846
      item:sub                  633     476      magic                 2,907 2,003
      mount                   1,819   1,837      map:dest                936   935
      achievement               391     166      magic:auto               15     1
      coat_storage_type         765     107      exchange_shop_goods   1,706   257
      instancetype              340      72      instance_enter_cond     351    91
      hairface_storage_type     418     147      process_task            191    32
      prof_lev_up               549     199      battlepass_score_rew    150    28

  Two PLAINTEXT subjects also fell and are NOT a cipher question (control
  `raw`): `activity` 112 -> 29 and `gamemapex` 287 -> 67. Both files
  changed; why the row count fell is NOT established.
* **npc:** `npc.ini` 2,992 rows; the frozen `3DSimpleObj.dbc` (442 rows)
  resolves 2,534 (84.7%), the `.ini` decoy 1,654, 458 unresolved.

NOT MEASURED, AND WHY
---------------------
* `part_tables()` row counts: the measure script read `len(PartIni.sections)`
  and got **0 on every part on 7205 AND 7250** -- the control is 0 too, so
  that instrument could not fire (the rows live in the `.dbc` twin). The
  parts list itself is the same nine on both. Not a finding about 7250.
* lua bytes / exported globals, BROWSE_IMPACT, UNOPENED_DAT_FAMILIES, and
  the 6868 cross-build cipher agreement (it holds for `itemtype.dat` by byte
  identity with 7205's; not re-run).

WHAT OVERRIDES, AND WHY EACH ONE
--------------------------------
* `confidence`       -- claims exactly the stamp `7250`, at 0.95. The
                        predecessor claims only `7205`, so without this
                        plugin 7250 falls to `patch6907` (MEASURED, margin
                        0.100).
* `name/label/aliases/notes` -- this build's identity and summary.
* `UNDECLARED_DAT`   -- 7205's refusals plus the two new 0-byte files; the
                        entries for the two files whose bytes CHANGED and
                        whose reason quotes 7205's sizes (`ServerPlay.dat`,
                        `GameLoadInfo.dat`) are restated as not measured.
* `SCRIPT_SURFACE`   -- measured counts (241 lua / 38 dll).
* content constants  -- 7250's own measured values, or `None` where not
                        measured (`NOT_YET_MEASURED`).
* `DAT_CENSUS_ERA`   -- False, deliberately; see the attribute.
* `table_quirks` / `colour_provenance` -- restated without 7205's numbers.

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
RESULT on 7250: **0 strict-GBK failures** in 359 subjects/name
sources (2 refused, not read); 15 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                   855    11    11     0      0     0    0
    Name=:npc:NpcX.ini              810     3     3     0      0     0    0
    Name=:npc:npc.ini              2992    50    22    28      0     0    0
    Name=:npc:terrainnpc.ini        167    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                168     3     3     0      0     0    0
    achievement                     166     1     0     0      0     1    0
    classdesc                       116    25    25     0      0     0    0
    item                          44682     1     0     1      0     0    0
    item:sub                        476     1     0     0      0     1    0
    map:dest                        935     7     6     0      0     1    0
    npc:NpcX.ini                    808     3     3     0      0     0    0
    npc:npc.ini                    2956    22    22     0      0     0    0
    npc:terrainnpc.ini              165    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      168     3     3     0      0     0    0
    strres                         1189    20    20     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 219: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 757: `a1 a1` = '\u3000'

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

from plugins.patch6609 import Patch6609               # noqa: E402
from plugins.patch7205 import Patch7205, _version     # noqa: E402

STAMP = "7250"


class Patch7250(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [855, 11, 11, 0],
        'Name=:npc:NpcX.ini': [810, 3, 3, 0],
        'Name=:npc:npc.ini': [2992, 50, 22, 0],
        'Name=:npc:terrainnpc.ini': [167, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [168, 3, 3, 0],
        'achievement': [166, 1, 0, 0],
        'classdesc': [116, 25, 25, 0],
        'item': [44682, 1, 0, 0],
        'item:sub': [476, 1, 0, 0],
        'map:dest': [935, 7, 6, 0],
        'npc:NpcX.ini': [808, 3, 3, 0],
        'npc:npc.ini': [2956, 22, 22, 0],
        'npc:terrainnpc.ini': [165, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [168, 3, 3, 0],
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

    name = "patch7250"
    label = "Official patch client 7250"
    origin = "official"
    aliases = ("7250",)

    notes = (
        "7205's layout three stamps later: c3.wdf and data.wdf at 7205's "
        "exact sizes, the twelve compiled .dbc sha256-identical to 7205's "
        "(and 6609's), ini/*.dat in the 6907-7878 96-bit block cipher read "
        "through core/block96.py's dictionary. 352 of 488 ini files are "
        "byte-identical to 7205's, itemtype.dat among them; 119 differ -- "
        "36 .dat, including monster, mounttype, MagicType and a 3.4x larger "
        "ItemtypeSub.dat -- and 17 are new. 21 changed block96 tables are "
        "only partly in the dictionary: records spanning an unknown block "
        "are dropped, so monster reads 846 sections (7205: 3,670), magic "
        "2,003 rows (2,907), item:sub 476 (633) while item is complete at "
        "44,684. Counts are floors, never guesses."
    )

    #: The class attributes `Patch7205` carries as MEASURED-ON-7205 content
    #: figures and that were NOT re-measured here. Each is `None`: inheriting
    #: them would publish 7205's numbers under 7250's name.
    NOT_YET_MEASURED = ("BROWSE_IMPACT", "ITEM_COLUMN_AGREEMENT",
                        "FORMERLY_UNCOVERED", "UNOPENED_DAT_FAMILIES")
    BROWSE_IMPACT = None
    ITEM_COLUMN_AGREEMENT = None
    FORMERLY_UNCOVERED = None
    UNOPENED_DAT_FAMILIES = None

    #: **FALSE ON PURPOSE, and it overrides 7205's `True`.** 7250's `ini/*.dat`
    #: ARE the block96 cipher (100 files, measured), so on the cipher alone it
    #: belongs in `tools/dat_census.BUILDS`. It is held out because joining
    #: turns `tests/test_dat_census.py::
    #: test_a_file_on_one_build_is_reported_on_one_build` RED: that test takes
    #: `INSTALLED` minus "7205" as "the ten patch6907 builds", and 7250 ships
    #: both `combat_gear.dat` and `exchange_shop_lev.dat` (listed 2026-09-19),
    #: so it would read ["7189", "7250"] and 10. Fixing that test is outside
    #: this plugin's slot; the Director decides. Flip this to True AND
    #: uncomment `"7250",` in `dat_census.BUILDS` together, once that test
    #: keys on the plugin rather than on the 7205 name.
    #:
    #: TRIED, 2026-09-19 08:08Z: slot uncommented and this set True, then
    #: `py -3 -m unittest tests.test_dat_census` -> `Ran 12 ... FAILED
    #: (failures=2)`: `test_a_file_on_one_build_is_reported_on_one_build`
    #: ['7189', '7250'] != ['7189'] (caused by 7250), and
    #: `test_no_block96_era_install_is_missing_from_the_list` ['7217', '7275']
    #: (NOT caused by 7250 -- those two still detect as patch6907). Reverted.
    DAT_CENSUS_ERA = False

    #: MEASURED by `measure_7250.py`, `Recovery` vocabulary as in 7205.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1080527, "known": 1.0, "lines": 44684,
                         "intact": 44684, "units": 44684, "shape": "rows"},
        "monster.dat": {"blocks": 104171, "known": 0.9464, "lines": 92526,
                        "intact": 88947, "units": 1197, "shape": "sections"},
        "magictype.dat": {"blocks": 48192, "known": 0.9615, "lines": 2405,
                          "intact": 2003, "units": 2052, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 59114, "known": 0.9043, "lines": 1587,
                            "intact": 476, "units": 733, "shape": "rows"},
        "mounttype.dat": {"blocks": 37087, "known": 1.0, "lines": 25835,
                          "intact": 25835, "units": 1837, "shape": "sections"},
    }

    #: MEASURED by `block96_census()` (recursive `ini/**.dat`, the scope of
    #: 7205's pin). Top-level only is 100 / 1,534,975 / 21,113 / 78 / 21 / 1.
    #: The 7878-identity split was not taken.
    BLOCK96_CENSUS = {"files": 121, "blocks": 1541007, "unknown": 22463,
                      "fully_covered": 87, "partial": 33, "uncovered": 1,
                      "fully_covered_identical_to_7878": None,
                      "fully_covered_and_different": None}

    #: MEASURED: only `levexp.dat` has zero known blocks. `ServerPlay.dat`
    #: classifies `unknown` on 7250, not block96, so it is outside the count.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical to 7205's (and 7878's); held out of the "
                      "dictionary by design -- see Patch7205.UNCOVERED_TABLES.",
    }

    #: MEASURED: `npc.ini` against the frozen `3DSimpleObj.dbc`.
    NPC_COVERAGE = {"rows": 2992, "resolved_compiled": 2534,
                    "resolved_ini_decoy": 1654, "unresolved": 458,
                    "simpleobj_dbc_rows": 442}

    #: MEASURED: `runeeffect` browses 293 sections (7205: 275). Header and
    #: repeat counts were not taken.
    RUNEEFFECT_REPEATS = {"headers": None, "distinct": 293, "repeats": None}

    #: MEASURED 2026-09-19 by listing: `.lua` anywhere under the install, and
    #: `*.dll` at the top level -- the same two counts `script_surface()`
    #: re-takes. Lua bytes and exported globals are NOT measured here.
    SCRIPT_SURFACE = {"lua": 241, "dll": 38, "lua_bytes": None,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37}}

    #: MEASURED 2026-09-19: sha256 of every `ini/` file against 7205's.
    INI_DELTA_VS_7205 = {"files": 488, "identical": 352, "differ": 119,
                         "new": 17, "removed": 0,
                         "identical_dat": 111, "differ_dat": 36,
                         "new_dat": 2}

    #: MEASURED: in 7250 and not in 7205, both 0 bytes.
    NEW_EMPTY_DAT = ("cq_remote_prize_cfg.dat", "month_card_type.dat")

    UNDECLARED_DAT = {
        **Patch7205.UNDECLARED_DAT,
        "serverplay.dat": (
            "5,757 bytes here against 7205's 5,696 -- a DIFFERENT file from "
            "7205's (and from 7878's, whose copy is the corpus's one genuine "
            "residue). MEASURED 2026-09-19: core/inidat classifies THIS "
            "copy `unknown`, not block96 (7205's: block96, 0 of 474 blocks "
            "known), so no reader here opens it."),
        "gameloadinfo.dat": (
            "1,016 bytes here against 7205's 991, so 7205's line count does "
            "not transfer. On 7205 it is a BYTE-SHIFTED ini that "
            "core/inidat classifies `plaintext` by printability; this copy "
            "also classifies `plaintext` (measured); its grammar is not."),
        "cq_remote_prize_cfg.dat": "new at 7250; the client ships it as a "
                                   "0-byte file.",
        "month_card_type.dat": "new at 7250; the client ships it as a "
                               "0-byte file.",
    }

    def confidence(self, root, exists) -> float:
        """Exactly the stamp `7250`, at 0.95; 0.0 for anything else.

        The `patch7205` / `patch6609` rule, for the same reason: 7250
        answers yes to every format probe 7205 does (same compiled magics,
        same-size archives), so only `version.dat` separates them. Not
        claiming 7217 or 7275 is deliberate -- each has its own plugin slot,
        and a neighbour claimed from here would be wrong about its content
        silently.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    def why_no_tables(self) -> str:                       # pragma: no cover
        return ("7250 declares tables; if you are reading this the install "
                "has no ini/ directory")

    def table_quirks(self):
        """6609's art quirks (the `.dbc` are identical) plus 7250's own,
        stated without any 7205 count."""
        q = Patch6609.table_quirks(self)
        q["ini/*.dat is the 6907-7878 block cipher, not TQ"] = (
            "Inherited from 7205 and re-checked per declared file by "
            "core/inidat.classify in tests/test_patch7250.py. core/tqdat.py "
            "must not be pointed at these files: a TQ decrypt of block96 "
            "bytes returns noise that parses (394,269 'item rows' on 7205).")
        q["block coverage is not record coverage"] = (
            "A record is served only if every block it spans is known. The "
            "111 .dat byte-identical to 7205's decode exactly as 7205's do; "
            "21 of the 36 that differ are only partly in the dictionary. "
            "ItemtypeSub.dat: 90.4% of blocks known, 30.0% of lines whole. "
            "A block percentage must never be read as a row percentage.")
        q["the compiled tables froze at 6609"] = (
            "All twelve .dbc are sha256-identical to 7205's and 6609's, while "
            "npc.ini, NpcX.ini and terrainnpc.ini changed again. The frozen "
            "3DSimpleObj.dbc resolves 2,534 of 2,992 npc rows (84.7%); 458 "
            "name a simple object it does not carry.")
        q["a re-decode control cannot refuse a wrong cipher"] = (
            "CONTROL_REDECODE witnesses the parse, not the cipher. The "
            "cipher-level evidence for this era is 7205's cross-build "
            "agreement with 6868; for 7250 it holds by identity on "
            "itemtype.dat, which is byte-identical to 7205's.")
        q["script surface"] = (
            "241 .lua and 38 top-level DLLs (7205: 224 and 37), plus new "
            "ini/luagui and ini/luaui folders. Declared as a surface; no "
            "TableSpec is offered for a .lua file.")
        return q

    def colour_provenance(self):
        """Two hops further than 6609's: 6090 -> 6609 -> 7205 -> 7250. The
        archives are byte-identical to 7205's (blake2b, measured), so the art
        is 7205's; nobody has looked at this client on screen."""
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any; archives byte-identical to 7205's)", "inferred")


PLUGIN = Patch7250()
