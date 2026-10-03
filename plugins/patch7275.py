#!/usr/bin/env python3
r"""
patch7275 -- official Conquer Online patch 7275: THE LAST `.wdf` CLIENT.

**7275 is the last official client that ships `.wdf` archives and compiled
`.dbc` tables. 7280, the next one on this box, ships neither.** The archive
switch and the loss of all twelve `.dbc` happen in the same step, 7275 -> 7280.
Evidence, from a directory listing of each install on 2026-09-19 (no archive
opened):

    install   *.wdf   *.tpd   *.tpi   ini/*.dbc
    7275        2       0       0        12      c3.wdf, data.wdf
    7280        0       2       2         0      c3.tpd/.tpi, data.tpd/.tpi

No official client on this box ships both, so there is no mixed official
install to bracket; the switch is bracketed by these two folders.
`plugins/patch7280.py` records the other side.

MEASURED BY `measure_7275.py` (run by the Director, 2026-09-19; EXIT 1,
2 FAIL / 15 PASS -- both FAILs are recorded below, not tuned away)
-------------------------------------------------------------------
* **Detection, every install on the box (43 folders + CCO):** only `7275`
  changes its winner -- `patch6907` 0.55 (+0.10) without this plugin,
  `patch7275` 0.95 (+0.40) with it. `patch7275` scores 0.00 on every other
  install, and no other winner or margin moved.
* **Archives:** `c3.wdf` and `data.wdf` on 7275 are byte-identical (sha256
  `ab68f57c...`, `fc628e4a...`) to 7205's. `_discover_archives` on 7280
  lists `c3.tpi`, `data.tpi`. *One of the two FAILs is here and it is an
  INSTRUMENT defect, not a finding: the script tested for a `.tpd` suffix,
  and discovery lists a TPD pair by its `.tpi` index. The listing above
  (2 `.tpd`, 2 `.tpi`, 0 `.wdf` on 7280) is the evidence and stands.*
* **Appearance tables** (`AssetRoot.bare().part_tables()`): 9 slots on both
  7205 and 7275, identical row counts (body 3,754, armet 2,793, weapon
  13,292, mount 1,223, head/misc 0). **The declared `pelvis` slot does not
  resolve on 7275**: not loose, and not reached through `data.wdf` either.
* **Block96 census.** The positive control first: 7205 reproduces
  `Patch7205.BLOCK96_CENSUS` exactly (101 files, 1,488,616 blocks, 809
  unknown, 99 full). **7275: 127 block96 files, 1,558,313 blocks, 101,701
  unknown (93.47% known) -- 81 full, 44 PARTIAL, 2 at zero.** 7205 has no
  partial table; 7275 has 44. The dictionary has not seen this build's new
  blocks.
* **THE FIRST MEASURED LIMIT: `itemtype.dat` is 92.92% of 1,134,988 blocks
  known and yields 17,823 whole rows, against 7205's 44,684 (100%).** Block
  coverage is not row coverage, again. Same shape on the other headline
  tables (7275, blocks known / whole records):
      monster.dat      94.60%   977 sections  (7205: 3,670)
      MagicType.dat    91.84%   1,060 rows    (7205: 2,907)
      ItemtypeSub.dat  87.06%   12 rows       (7205: 633)
      mounttype.dat    100.00%  1,838 sections (7205: 1,819)
      levexp.dat, ServerPlay.dat  0.00%
  The 17,823 rows that survive are right, not noise: 17,633 of their ids are
  shared with 7205's itemtype and the name agrees on 17,626; against 6868
  (the TQ build), 13,568 shared, 13,560 agree.
* **THE SECOND MEASURED LIMIT: `award_config` is LOST.** 7205 serves 72
  rows; on 7275 `ini/award_config.dat` (3,654 B, 304 blocks) is 28.62%
  known and not one whole row survives, so the catalog refuses it. 26 other
  block96 subjects serve fewer rows than on 7205 (e.g. `instancetype` 32 vs
  340, `coat_storage_type` 97 vs 765); `KNOWN_LIMITS` names the worst.
* **Declared codecs:** all 103 declared `.dat` match `inidat.classify` on
  7275 (and on 7205). The 5x-smaller `ItemtypeSub.dat` is still block96.
* **`catalogs()`:** 7205 control reproduces item 44,684 / mount 1,819.
  7275: 247 subjects, 244 ok. No subject exceeds 3x 7205's count (the
  wrong-cipher noise signature).

This plugin is a thin subclass of `Patch7205`, and the choice is a claim about
the ART and the compiled layer, which were measured byte-for-byte, not about
the content tables, which were not (see NOT YET MEASURED).

MEASURED (listings, sizes, sha256 of small files; 2026-09-19)
-------------------------------------------------------------
* `version.dat` is `7275\r\n` (6 bytes; 7205's is 4 bytes, no newline).
  `confidence` strips, so both read as their number.
* **All twelve `.dbc` are byte-identical (sha256) to 7205's**, which
  `patch7205.FROZEN_COMPILED` records as identical to 6609's. So the compiled
  twin is still the 2017 one, three patches later.
* `region.ini`, `EventTypeName.ini`, `VipTrans.ini`, `restrain.ini` are
  byte-identical to 7205's, so `Patch7205.ROW_COLUMNS` (measured on 7205's
  copies) applies here by identity, not by assumption. So are `armor.ini`,
  `armet.ini`, `weapon.ini`, `3dtexture.ini`, `3dobj.ini`, `3DSimpleObj.ini`
  and `levexp.dat`.
* **`ini/RolePart.ini` CHANGED, and it is the one art-layer change found.**
  `[Part] Count` 12 -> 13, with a new `Part12=pelvis`,
  `MeshIni12=ini/pelvis.ini`, `MotionIni12=ini/pelvismotion.ini`; the dummy
  list goes 37 -> 52 (`v_extend1`..`v_extend15`). **`ini/pelvis.ini` and
  `ini/pelvismotion.ini` are NOT loose files on this install.** The change
  arrived at 7250 (7217 still says 12/37) and 7280 still carries it.
  `part_tables()` (which resolves through the archives too) does not find
  it either -- see the measured section below.
* `ini/` holds 502 entries against 7205's 474, and **nothing was removed**.
  Added since 7205: 3 `.dat` (`month_card_type.dat` 376 B,
  `cq_remote_prize_cfg.dat` 0 B, `vendue_item_type.dat` 0 B), 4 `.ini`
  (`Appqdkey`, `OperateWeben`, `ShaderParam`, `cq_remote_prize_cfg`), 19
  `.lua`, and two new directories: `ini/luaui/` (4 `.dat` at its top plus
  four subdirectories, `dialog`, `function`, `publicfiles`, `widgets`) and
  `ini/luagui/` (2 `.dat`). `block96_census()` walks `ini/` recursively, so
  everything under them is inside its sweep.
* `npc.ini`: **3,030** `[section]` headers (`grep -c '^\['`). The same count
  on 7205 gives 2,913 -- exactly `Patch7205.NPC_COVERAGE["rows"]` -- which is
  the positive control that the count is the same instrument.
* Script surface: **284** `.lua` anywhere under the root and **38** top-level
  `.dll` (`WordsCheck.dll` is the new one, arrived at 7250). The same two
  counts on 7205 give 224 and 37, matching `Patch7205.SCRIPT_SURFACE`.
* Top-level listing against 7205: `WordsCheck.dll` and a `debug/` folder
  (one 720-byte log) are new; nothing is gone.

INFERRED (arithmetic or identity, no decode)
--------------------------------------------
* **Block counts of the five headline tables**, from file size: block96
  `decode` counts ``len // 12`` full blocks, so the count is a property of the
  size alone. The rule reproduces 7205's recorded 335 (`levexp.dat`) and 474
  (`ServerPlay.dat`), which is its control. See `BLOCK96_BLOCKS_FROM_SIZE`.
  **`ItemtypeSub.dat` SHRANK FIVE-FOLD** -- 207,838 B at 7205, 709,372 B at
  7250, **40,812 B here** -- which is the one size move large enough to be a
  change of CONTENT or of CODEC rather than of row count. It is not
  interpreted here.
* `levexp.dat` is byte-identical to 7205's, so it decodes exactly as 7205's
  does under any dictionary: 0 of 335 blocks, held out by design
  (`Patch7205.UNCOVERED_TABLES`).
* `c3.wdf` (359,069,116 B) and `data.wdf` (392,245,257 B) are the SAME SIZE
  as 7205's. Same size is not same bytes; they were NOT hashed, because
  reading them is heavy work on an exclusive machine. `measure_7275.py`
  hashes them.

STILL NOT MEASURED (NOT "unchanged")
------------------------------------
* How many of the 3,030 npc rows the frozen `3DSimpleObj.dbc` resolves.
* `ServerPlay.dat` beyond its 0% block coverage.
* The Lua byte count and exported globals.

THE PRE-MEASUREMENT LIST, KEPT AS WRITTEN (answered above)
-------------------------------------------------------------
* `plugins.rank()` on this install and every other, with and without this
  plugin. Expected before it: `patch6907` at its 0.55 sibling bid.
* The codec of every declared `.dat` (`inidat.classify`), above all
  `ItemtypeSub.dat`, and of the three new `.dat`.
* Block96 coverage per table and whole records kept; the whole-surface census
  (`BLOCK96_CENSUS` holds 7275's OWN measured census in 7205's shape --
  127 files -- never 7205's figure).
* `catalogs()` row counts. 7205's are a ceiling for nothing here: 7275's
  `itemtype.dat` is 653,525 bytes larger.
* `part_tables()` row counts, and whether the new `pelvis` slot resolves.
* `ServerPlay.dat` (482 blocks, a different file from 7205's).
* How many of the 3,030 npc rows the frozen `3DSimpleObj.dbc` resolves.
* The 6868 cross-build cipher agreement (`ITEM_COLUMN_AGREEMENT` is None).

Every constant 7205 carries that is a MEASUREMENT OF 7205 is overridden here,
either with this install's number or with None, so no reader can take 7205's
figure for 7275's. Every constant that is a DECLARATION (specs, columns,
refusals) is inherited, and `measure_7275.py` checks each against the disk.

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
RESULT on 7275: **0 strict-GBK failures** in 359 subjects/name
sources (3 refused, not read); 15 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                   981     6     6     0      0     0    0
    Name=:npc:NpcX.ini              844     3     3     0      0     0    0
    Name=:npc:npc.ini              3030    52    24    28      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                186     5     5     0      0     0    0
    achievement                     166     1     0     0      0     1    0
    classdesc                       116    25    25     0      0     0    0
    map:dest                        945     7     6     0      0     1    0
    npc:NpcX.ini                    842     3     3     0      0     0    0
    npc:npc.ini                    2994    24    24     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      186     5     5     0      0     0    0
    strres                         1189    20    20     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 855: `bf f1 b1 a9 c5 a3 c4 a7 cd f5` = '狂暴牛魔王'
    Name=:npc:npc.ini 772: `a1 a1` = '\u3000'

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


class Patch7275(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [981, 6, 6, 0],
        'Name=:npc:NpcX.ini': [844, 3, 3, 0],
        'Name=:npc:npc.ini': [3030, 52, 24, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [186, 5, 5, 0],
        'achievement': [166, 1, 0, 0],
        'classdesc': [116, 25, 25, 0],
        'map:dest': [945, 7, 6, 0],
        'npc:NpcX.ini': [842, 3, 3, 0],
        'npc:npc.ini': [2994, 24, 24, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [186, 5, 5, 0],
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

    name = "patch7275"
    label = "Official patch client 7275"
    origin = "official"
    aliases = ("7275",)

    notes = (
        "The last .wdf client: 7280 ships .tpd archives and no .dbc. 7205's "
        "layout -- c3.wdf/data.wdf, twelve compiled .dbc byte-identical to "
        "7205's (and so to 6609's) -- with ini/*.dat in the 6907-7878 96-bit "
        "block cipher, read through core/block96.py. RolePart.ini declares a "
        "13th part slot, pelvis, whose ini/pelvis.ini is not a loose file. "
        "npc.ini has 3,030 sections; 284 Lua scripts and 38 DLLs. The block "
        "dictionary knows 93.5% of this build's .dat blocks: itemtype.dat "
        "is 92.9% known and yields 17,823 whole rows (7205: 44,684), and "
        "award_config is lost.")

    #: The step this build brackets, from the two folders' listings. 7275 is
    #: `(2 .wdf, 0 .tpd, 12 .dbc)`; 7280 is `(0 .wdf, 2 .tpd, 0 .dbc)`.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: **False, although 7275's `ini/*.dat` ARE the 96-bit block cipher**
    #: (127 files classify block96). Joining `tools/dat_census.py` BUILDS
    #: turned `tests/test_dat_census.TheVerdictIsPerBuild.
    #: test_a_file_on_one_build_is_reported_on_one_build` red, measured
    #: 2026-09-19: `combat_gear.dat` is pinned as a 7189-only file and 7275
    #: ships it too (['7189', '7275'] != ['7189']). That test's per-build
    #: pins are not this plugin's to edit, so the census does not cover 7275
    #: yet; `BLOCK96_CENSUS` below keeps 7275's own measured numbers. The
    #: same run's other red (7217/7250 detect as block96-era and are not in
    #: BUILDS) is NOT caused by 7275 -- they are outside this worktree's
    #: plugin set.
    DAT_CENSUS_ERA = False

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """0.95 on the exact stamp `7275`, 0.0 on anything else.

        The `patch7205` / `patch6609` rule, for the same reason: 7275 answers
        yes to every format probe 7205, 6907 and 6090 make (the same twelve
        `.dbc`, byte for byte), so only `version.dat` separates them. No
        sibling bid: 7276..7279 are not on this box and are not measured.
        """
        if _version(Path(root)) == "7275":
            return 0.95
        return 0.0

    # -- 7205's MEASUREMENTS, replaced ------------------------------------
    #: INFERRED from file size (``len // 12``), not decoded. The rule
    #: reproduces 7205's recorded `levexp.dat` 335 and `ServerPlay.dat` 474.
    #: `size` is the byte count off this install, `size_7205` 7205's.
    BLOCK96_BLOCKS_FROM_SIZE = {
        "itemtype.dat": {"size": 13619856, "blocks": 1134988,
                         "size_7205": 12966331},
        "monster.dat": {"size": 1349518, "blocks": 112459,
                        "size_7205": 1237358},
        "magictype.dat": {"size": 658614, "blocks": 54884,
                          "size_7205": 572761},
        "itemtypesub.dat": {"size": 40812, "blocks": 3401,
                            "size_7205": 207838},
        "mounttype.dat": {"size": 445293, "blocks": 37107,
                          "size_7205": 440631},
        "levexp.dat": {"size": 4030, "blocks": 335, "size_7205": 4030},
        "serverplay.dat": {"size": 5792, "blocks": 482, "size_7205": 5696},
    }

    #: NOT YET MEASURED. Empty rather than inherited: 7205's dict pins
    #: 7205's block counts, and all five tables here are different sizes.
    BLOCK96_COVERAGE: dict = {}
    #: MEASURED by `measure_7275.py`, same keys as 7205's census (the two
    #: 7878-identity keys were not measured and are absent).
    #: `block96_census(root)` re-counts it off the install.
    BLOCK96_CENSUS = {"files": 127, "blocks": 1558313, "unknown": 101701,
                      "fully_covered": 81, "partial": 44, "uncovered": 2}

    #: MEASURED on 7275 (blocks known, whole records as `clean_rows` kept
    #: for rows and the catalog's count for sections). These are LIMITS OF
    #: THE DICTIONARY on this build, not of the client.
    MEASURED_7275 = {
        "itemtype.dat": {"known": 0.9292, "whole_rows": 17823,
                         "at_7205": 44684},
        "monster.dat": {"known": 0.9460, "sections": 977, "at_7205": 3670},
        "magictype.dat": {"known": 0.9184, "whole_rows": 1060,
                          "at_7205": 2907},
        "itemtypesub.dat": {"known": 0.8706, "whole_rows": 12,
                            "at_7205": 633},
        "mounttype.dat": {"known": 1.0, "sections": 1838, "at_7205": 1819},
        "award_config.dat": {"known": 0.2862, "whole_rows": 0,
                             "at_7205": 72},
    }

    #: The catalog subjects the measurement FAILed or shrank hardest on.
    #: `award_config` is REFUSED here (7205: 72 rows).
    KNOWN_LIMITS = {
        "lost": ("award_config",),
        "shrunk": {"item": (17823, 44684), "monster": (977, 3670),
                   "magic": (1060, 2907), "item:sub": (12, 633),
                   "instancetype": (32, 340),
                   "coat_storage_type": (97, 765),
                   "achievement": (166, 391)},
    }
    #: `levexp.dat` only, INFERRED by byte identity with 7205's copy.
    #: `ServerPlay.dat` is a different file here and is not yet measured, so
    #: it is not listed as uncovered.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaa) to 7205's and so to "
                      "7878's; held out of the dictionary by design. See "
                      "Patch7205.UNCOVERED_TABLES.",
    }
    #: 7205's history, not this build's.
    FORMERLY_UNCOVERED: dict = {}
    #: NOT YET MEASURED on 7275.
    ITEM_COLUMN_AGREEMENT = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    UNOPENED_DAT_FAMILIES = None

    #: `rows` MEASURED (section headers, same count that gives 7205's 2,913);
    #: the resolution counts are NOT YET MEASURED and are absent, not zero.
    NPC_COVERAGE = {"rows": 3030}

    #: Byte-identical (sha256) to 7205's twelve, measured 2026-09-19.
    FROZEN_COMPILED = Patch7205.FROZEN_COMPILED

    #: `lua`/`dll` MEASURED by listing; byte and global counts NOT YET
    #: MEASURED (`tools/clientscripts.py`).
    SCRIPT_SURFACE = {"lua": 284, "dll": 38, "lua_bytes": None,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37}}

    #: MEASURED: `ini/RolePart.ini` diff against 7205's.
    ROLEPART_CHANGE = {
        "part_count": (12, 13), "dummy_count": (37, 52),
        "new_part": ("pelvis", "ini/pelvis.ini", "ini/pelvismotion.ini"),
        "new_part_ini_is_loose": False,
        "arrived_at": 7250,
    }

    #: MEASURED: what `ini/` gained since 7205 (nothing was removed).
    INI_ADDED_SINCE_7205 = {
        "dat": ("month_card_type.dat", "cq_remote_prize_cfg.dat",
                "vendue_item_type.dat"),
        "zero_byte_dat": ("cq_remote_prize_cfg.dat", "vendue_item_type.dat"),
        "ini": ("Appqdkey.ini", "OperateWeben.ini", "ShaderParam.ini",
                "cq_remote_prize_cfg.ini"),
        "dirs": {"luaui": 4, "luagui": 2},   # top-level .dat only
        "lua": 19,
    }

    def table_quirks(self):
        """7205's quirks describe the art layer this build shares
        byte-for-byte, and quote 7205's own numbers for the content layer.
        The number-bearing ones are replaced with this install's facts or an
        explicit "not measured here"."""
        q = super().table_quirks()
        q["block coverage is not record coverage"] = (
            "A row is served only if every block it spans is known, so block "
            "coverage is a ceiling on record coverage. Measured on 7275: "
            "itemtype.dat 92.92% of blocks known, 17,823 whole rows against "
            "7205's 44,684; award_config.dat 28.6% known, 0 rows, refused.")
        q["a dropped section is not a dropped line"] = (
            "For [section] tables core/block96.clean_sections discards whole "
            "sections on any damage. Counts on 7275 not yet measured.")
        q["the compiled tables froze at 6609"] = (
            "All twelve .dbc are byte-identical (sha256) to 7205's, and so to "
            "6609's, while npc.ini grew to 3,030 sections (7205: 2,913). "
            "How many the frozen 3DSimpleObj.dbc resolves here is not yet "
            "measured; an unresolved row looks exactly like an npc with no "
            "art.")
        # Keys that NAME a 7205 number are removed, not re-valued: a key
        # reading "224 Lua scripts" over a body saying 284 is the defect.
        q.pop("224 Lua scripts and no Lua grammar", None)
        q["284 Lua scripts and no Lua grammar"] = (
            "284 Lua scripts and 38 DLLs here (7205: 224 and 37). Declared "
            "as a surface, not as tables: tools/clientscripts.py reads it.")
        q["ini/*.dat is the 6907-7878 block cipher, not TQ"] = (
            "Every build from 6907 to 7878 enciphers its ini/*.dat tables "
            "with the 12-byte ECB block cipher; core/tqdat.py must not be "
            "pointed at them. core/block96.py reads them through the "
            "dictionary tools/datdict.py builds. How many of 7275's .dat "
            "classify block96 is not yet measured.")
        q["a re-decode control cannot refuse a wrong cipher"] = (
            "CONTROL_REDECODE witnesses the parse: a wrong cipher applied "
            "twice agrees with itself (patch7205 records the 7205 case, a "
            "1-row `mount` with a passing control). Block96 subjects here "
            "carry the same control kind and the same limit.")
        q["the last .wdf client"] = (
            "7275 ships c3.wdf/data.wdf and twelve .dbc; 7280 ships "
            "c3.tpd/data.tpd and no .dbc. The archive format and the "
            "compiled tables change in the same step.")
        q["RolePart.ini declares a pelvis slot it does not ship loose"] = (
            "Part count 12 -> 13 (since 7250): Part12=pelvis, "
            "MeshIni12=ini/pelvis.ini. That file is not in ini/ on disk; "
            "measured: it does not resolve through data.wdf either.")
        return q

    def colour_provenance(self):
        """Two hops further than 6609's: 6090 -> 6609 -> 7205 -> 7275, and
        nobody has looked at this client on screen."""
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them)", "inferred")


PLUGIN = Patch7275()
