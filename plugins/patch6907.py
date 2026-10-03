#!/usr/bin/env python3
r"""
patch6907 -- official Conquer Online patch 6907, the first client of the
`block96` cipher era, and the one that splits ART from CONTENT.

**6907's art layer IS 6609's, byte for byte. Its `.dat` tables are 7878's
cipher.** That is the whole client in one sentence, and neither half is
guessable from the other. Every previous plugin in this repo describes a build
whose two layers moved together; this one does not.

MEASURED 2026-08-30 (`sha256sum Clients/6609/ini/<f> Clients/6907/ini/<f>`):

    ALL TWELVE .dbc byte-identical to 6609's (sha256 prefixes):
        armet     9415fee5e4b7    3DSimpleObj  7159b91c0eb7
        armor     d50f726935fa    3DObj        041b1060bd18
        weapon    a07bae8d701d    3DTexture    0882236874e5
        mount     ebc89e4ece32    3dmotion     565a3c34e4bd
        3DEffect  29e5ab3aed58    weaponmotion 5791de723722
        3DEffectobj 063b4121696c  mountmotion  a2509814d75b
    c3.wdf ab68f57cc24ae100 and data.wdf -- identical to 6609's and to the
    whole official lineage's

    and the readers agree: armet 2,793 · armor 3,754 · weapon 13,292 ·
    mount 1,223 · 3DSimpleObj 442 -- the SAME five counts 6609 returns.

So this plugin inherits `Patch6090`'s art profile the way `patch6609` does, and
for a stronger reason than 6609 had: the files are the same files.

WHAT CHANGED, AND IT IS THE WHOLE REASON THIS FILE EXISTS
----------------------------------------------------------
`ini/*.dat` stopped being TQ-cipher text at this patch. MEASURED, coverage of
the 7878 ECB block dictionary against `itemtype.dat`
(`py -3 tools/datdict.py decode <client>/ini/itemtype.dat`):

    6772   0.0%      6805   0.0%      6868   0.0%      6907  86.4%   7878  100%

**The zeroes are rounded and the exact figure is sharper**: 38 of 6868's
797,654 blocks are in the dictionary, and every one of the 38 was traced to
`UserHelpInfo.ini.dat` -- a poisoned pair (see below) whose "plaintext" is
ciphertext. **Zero genuine entries cross the boundary**, against 779,199 on
6907. That is a key change, not a short dictionary. 6868 and earlier open with
`core/tqdat.py` at seed 9527; **6907 and later do not, and tqdat does not raise
on them** -- it returns noise that parses.

BEFORE THIS PLUGIN, 6907 WAS BEING CLAIMED, AND WRONGLY
--------------------------------------------------------
`plugins.detect()` on a 6907 root returned **`patch6090` at 0.45** -- the
"stamped sibling" bid `Patch6090.confidence` makes for any client carrying the
compiled tables. That bid is right about the art and wrong about the content:
`patch6090` declares `itemtype.dat`, `Monster.dat`, `mounttype.dat`,
`MagicType.dat` and the rest as `tq-stream`, and `tqdat` does not raise on a
block96 file -- it returns noise that parses.

**And one of those subjects came back with a control that PASSED.** MEASURED on
`origin/master`, `patch6090.catalogs(Clients/6907)`:

    item      328,686 "rows"   no control -> refused
    magic      14,548 "rows"   no control -> refused
    mount           1 row      CONTROL PASSED, control_kind=re-decode
                               "[x\x16z\xcb\xdcLo... " -- a garbage header
                               and a garbage key, both "found in an
                               independent re-decode"

The witness for a `tq-stream` table is a second run of the same decrypt, so on
a wrong-cipher file it agrees with the parse perfectly. That is the concrete
case behind `plugins/catalog.py`'s insistence that `CONTROL_REDECODE` and
`CONTROL_RAW` are different amounts of evidence, and it is why this plugin also
bids for the ten UNSTAMPED clients in the same era (see `confidence`) -- they
were all in that state and only one of them is stamped 6907.

Reproduce the before-state with

    py -3 -c "import sys;sys.path[:0]=['.','core','tools'];import plugins;
    print(plugins.detect(r'C:/COMod/ConquerAssets/Clients/6907'))"

and `tests/test_patch6907.py:TheDatTablesWereUnreadableBefore` holds the
must-fire cases.

`coassets.load_items()` returned **0 rows** for the same reason, on every
client from 6907 up.

THE BLOCK FIGURE IS NOT THE ROW FIGURE, AND THE GAP IS ENORMOUS
----------------------------------------------------------------
`tools/datdict.py` prints a coverage percentage. It is a percentage of 12-byte
BLOCKS, and reading it as readability is the single largest error available on
this client. MEASURED, both numbers, on the same files:

    file             blocks known      rows/sections recovered
    itemtype.dat        86.41%          5,387 of >=22,265 rows  (<=24.2%)
    Monster.dat         84.56%              1 of ~2,100 sections
    mounttype.dat       99.36%          1,742 sections
    MapDestination      53.31%            170 sections
    UserHelpInfo.dat   100.00%          1,508 sections (the file's own
                                        HelpInfoAmount says 1506)

**Every row count here is a FLOOR and every percentage a CEILING**, and the
`>=` is not hedging. The denominator is the number of lines the decode
produced, and a marker landing on a `\r\n` deletes that line break and merges
two rows into one -- so the count of lines under-reports the count of rows and
the ratio flatters. The scale is visible in the neighbour: 6868's `itemtype`,
one patch earlier and fully readable, carries **33,639** rows against these
22,265 lines. Some of that gap is content and some is merges, and nothing here
can say which. Another seat measured the same shape independently on 7205 --
86.1% of blocks, 5.2% of whole rows.

An unknown block becomes a 12-byte marker, and one marker anywhere in a row
damages that row. Rows are ~40 blocks long, so a 13.6% per-block miss lands on
three quarters of them. Section headers are worse again: `[MatureMindofEvil]`
is a short unique string appearing once, exactly what a dictionary built from a
DIFFERENT client does not hold -- and a marker that swallows the `\r\n` before
a header deletes the section outright. **`Monster.dat` is therefore REFUSED on
this build**: 84.6% of its blocks are known and 1 of its sections is, and
serving "monster: 1 row" would be a statement about the dictionary dressed as a
statement about the client. `core/block96.py` holds the recovery rules and why
each one is shaped the way it is.

TWO TRAPS THIS PLUGIN IS BUILT AROUND
--------------------------------------
1. **THE DICTIONARY IS POISONED AND ITS 100% FILES ARE THE TRAP.**
   `patch7878.NOT_CONTENT` names fourteen `.out` files that are still
   ciphertext; the `datdict.build_dict` that built the SHIPPED dictionary
   excluded three names, of which only two were `NOT_CONTENT` members, so
   **twelve** went in as plaintext. (Recorded as eleven until 2026-08-30 --
   that subtracted all three, but the third, `levexp.dat`, is the separate
   `HELD_OUT` case.) Four of 6907's tables are byte-identical to those and
   therefore decode at **100% coverage into ciphertext**:
   `UserHelpInfo.ini.dat` (H=8.00), `RaceTrackProp.dat` (7.89),
   `ShowHandTableRace.dat` (7.79), `WeaponActionData.dat` (7.26). Coverage
   cannot see it. Two independent guards in `core/block96.py` can, and both are
   needed -- the `inidat` family check, and an entropy check that does not
   consult the classifier because the classifier is the thing that can be
   wrong. **It already is**, on `MapDestination.dat`: `inidat` calls it
   `block96-candidate-refuted` on a 1-byte `.` tail, and the dictionary opens
   it into 170 real sections.

2. **THE COLUMN NAMES DO NOT APPLY.** 6907's item rows carry **64** fields.
   `tqdat.FIELDS_AT` carries 59 names and would place `elemResEarth` on the
   column that actually holds the item class -- the identical off-by-one
   `patch7878.ITEM_COLUMNS` records for its 68-field rows. Measured here rather
   than assumed: column 52 of 6907's complete rows holds `Gift` (733),
   `QuestItem` (224), `GiftPack` (143), `Pack` (54), `EpicWeapon` (42),
   `Halbert` (37) -- an item class. So rows are read POSITIONALLY and only the
   three columns this build has evidence for are named.

WHAT THIS PLUGIN DOES **NOT** CLAIM
------------------------------------
* **Monster rows.** Refused, with the numbers, above. Nothing else in this
  repo can read them either; `tqdat` will happily return noise if pointed here.
* **A grammar for two `.dat` tables.** `levexp.dat` and `ServerPlay.dat` are
  at ZERO block coverage -- not one of their blocks is in the dictionary, so
  no line comes back -- see `SPECS_UNSETTLED`. Declared with a refusal rather
  than a guessed kind, because a spec is a claim that the build knows the
  table's shape. (Thirteen when this file was written; ten moved 2026-09-06
  when the dictionary improved, and `Achievement.dat` moved 2026-09-07 when
  its grammar was measured rather than its damage repaired -- those are
  different events and the list keeps them apart.)
* **The text encoding.** `ini/codepage.ini` says `1256` here, exactly as it
  does on 7878, where it was MEASURED WRONG and the content says GBK. Nobody
  has repeated that measurement on 6907, so this plugin keeps `Patch6090`'s
  `latin1` -- which round-trips every byte and therefore cannot break a
  positive control -- and states the gap instead of inheriting 7878's answer.
  `AutoUseMagic.dat` here is Chinese and will display as mojibake.
* **Colour or name data.** Inherited from the 6090 scan, unverified here, same
  as 6609. The archives are byte-identical so the sets are not dropped, but
  nobody has looked at 6907 on screen.
* **A `.ini` census of its own.** 6907 ships 207 `.ini` in `ini/`; the census
  that `plugins/catalog/censused.py` holds was run on eight builds and 6907 was
  not one of them. This plugin BORROWS 6609's list and filters it to files
  6907 actually ships -- see `table_specs`, which explains why that is a
  measurement rather than an inheritance.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import block96                                          # noqa: E402
import inidat                                           # noqa: E402

from plugins.patch6090 import Patch6090                 # noqa: E402
from plugins.catalog import (                           # noqa: E402
    TableSpec, KIND_AT_ROWS, KIND_CSV_ROWS, KIND_SECTIONS, KIND_GAMEMAP,
    KIND_SPACE_ROWS, ROW_KINDS, CONTROL_REDECODE, npc_specs, censused)

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "6907"

#: **A spec's codec IS `core/inidat.py`'s verdict on the file, not a name this
#: plugin chose.** `tests/test_plugin_catalogs.py:
#: TheSpecsMatchTheDisk.test_the_declared_codec_is_what_inidat_classifies`
#: enforces exactly that for every declared `.dat` on every build, so these are
#: taken from `inidat.Family` rather than spelled out -- a literal here would
#: pass today and rot the moment the classifier renamed a family.
#:
#: The first draft called the codec `block96-dict`, on the reasoning that a
#: distinct name would fail closed if this plugin's `load_table` override were
#: ever dropped. That reasoning was wrong twice over: the invariant above
#: refuses any name but the classifier's, and 62 specs went red at once saying
#: so. It is also the name a parallel seat chose for the same codec on 7205,
#: so agreeing with the classifier and agreeing with them is one decision.
CODEC = inidat.Family.BLOCK96

#: `MapDestination.dat` and nothing else on this build. `inidat` puts it here
#: because its 1-byte `.` tail is bare punctuation rather than text -- a
#: refutation that is a FALSE NEGATIVE on this file, since the dictionary opens
#: 53.3% of its blocks into `[1010-1]\r\ntitle=New~Skil...` and recovers 170
#: sections. Declared with the classifier's own verdict rather than corrected
#: to `block96`, because the codec field records what the classifier SAYS; the
#: disagreement is recorded in `core/block96.CANDIDATE_FAMILIES`, which is
#: where the decision to read it anyway lives.
CODEC_REFUTED = inidat.Family.BLOCK96_REFUTED

#: A 0-byte file. Six of this build's `ini/*.dat` are one; only `ItemtypeSub`
#: is declared, because the other five have no counterpart on any other build
#: and a subject that can only ever refuse is noise in a listing.
CODEC_EMPTY = inidat.Family.EMPTY

#: The census this build borrows, and the two rival candidates it was measured
#: against. See `table_specs`.
#:
#: **2026-09-19: 7878 now SCORES HIGHER and the donor is deliberately not
#: changed.** Readable subjects on this install went 6090 121->122,
#: 6609 139->142, 7878 138->147 when nine of 7878's `.ini` settled in the
#: census (CR/LF line splitting plus their measured per-table encodings).
#: Re-pointing the donor would move every borrowed subject, label and control
#: on this build AND on the nine plugins that borrow the same way, so it is a
#: MEASURED FOLLOW-UP with its own re-measurement, not a one-line edit. The
#: scores are pinned in `tests/test_patch6907.py`.
CENSUS_DONOR = "patch6609"

#: The item columns this build has evidence for, and **only** those.
#:
#: 6907's rows carry 64 fields; `tqdat.FIELDS_AT` names 59 and diverges before
#: it runs out. Column 52 was MEASURED over the 2,161 undamaged rows -- `Gift`
#: 733, `QuestItem` 224, `GiftPack` 143, `Pack` 54, `EpicWeapon` 42 -- which is
#: an item class and is the same column `patch7878` measured for it. The other
#: 61 are left unnamed rather than borrowed from a narrower client.
ITEM_COLUMNS = {"id": 0, "name": 1, "itemClass": 52}

#: `Achievement.dat`'s columns, and it is the reason `TableSpec.columns`
#: exists: **the name is in column 2, not column 1.**
#:
#: MEASURED 2026-09-07 over all 390 data rows on 6907 (the same shape on all
#: ten builds this plugin serves; the counts differ, the columns do not):
#:
#:     col 0   390 distinct, all numeric -- the achievement id, UNIQUE
#:     col 1   346 distinct, all numeric; `0` on the 45 category headers
#:     col 2   387 distinct, NONE numeric -- the name, `~` for space
#:             (`Fantastic~Wardrobe`, `Great~Achievement`)
#:     col 3   379 distinct, none numeric -- the description, `~` for space,
#:             and `EmptyDesc<id>` on the header rows
#:     col 4   9 distinct: 10 x264, 50 x35, 30 x33, 128 x32, 20 x11, 0 x9
#:     col 5   343 distinct, `Achievement_<something>` or `null` x44
#:     col 6   `null` on all 390 -- one value, no exceptions
#:
#: Columns 4-6 are NOT named. `10/20/30/50` reads like a point value and
#: `128` like a flag, but nothing on this box consumes the table, so naming
#: them would be the guess `SPECS_UNSETTLED` exists to refuse. Column 1 is
#: numeric and in the 10000-11999 range and is NOT claimed as a foreign key
#: either: no table on this install was found holding those ids.
ACHIEVEMENT_COLUMNS = {"id": 0, "name": 2}

#: `magictype.dat`'s columns. **The name is column 3, not column 1.**
#:
#: MEASURED over all 2,409 recovered rows on 6907: column 3 is the ONLY one of
#: the 53 that is non-numeric on every row (`Thunder`, `Fire`, `Tornado`),
#: while column 1 -- where `ITEM_COLUMNS` puts the name -- is a numeric group
#: id. Under the plugin-wide map `browse magic` labelled every row `1`.
#:
#: The same column on 5517, 6090, 6609, 7205 and Zephyr; column 2 on 5165,
#: which has one fewer leading id. Declared per plugin rather than shared,
#: because that one-column shift is exactly what a borrowed constant gets
#: wrong.
MAGIC_COLUMNS = {"id": 0, "name": 3}

#: **Five more `.dat` tables whose name is not column 1**, measured the same
#: way and over the SAME TEN INSTALLS this plugin serves (6907, 6968, 7009,
#: 7065, 7083, 7110, 7135, 7170, 7182, 7189). The counts vary across those
#: ten; the column does not, and every one of these declares `label_key=None`
#: -- which suppressed nothing, because a positional table takes its label
#: from `columns`, so all five listed a column of numbers.
#:
#:   title_type.dat        col 2, non-numeric on every row of all ten
#:                         (106-169 rows: `Overlord`, `Chosen~One`). Widths
#:                         differ across the ten (6, 7 and 8 fields) and
#:                         column 2 is present in all of them.
#:   official_type.dat     col 3, 20/20 rows on all ten (`Emperor`,
#:                         `LeftPre.`). Column 4 is a SECOND copy of the same
#:                         name, equal to column 3 on every row measured; 3
#:                         is declared as the first.
#:   award_config.dat      col 7, the LAST field (72-108 rows:
#:                         `StonePack(M)`). Nine of the ten; 7009's file
#:                         refuses at 29.1% block coverage, so it is not
#:                         evidence either way rather than a counter-example.
#:   global_lottery_pool   col 3, 218-282 rows on all ten (`+6Stone(B)`).
#:   texas_match_type.dat  col 2, 82-174 rows on all ten
#:                         (`No Limit 10KK(Min)`).
#:
#: A fourth sibling was measured and IS declared as of 2026-09-07 -- see
#: `HAIRFACE_COLUMNS`. Two more were measured, have no name column at all and
#: now say so: `texas_match_prize` and `battlepass_season` carry
#: `NO_NAME_COLUMN`.
TITLE_COLUMNS = {"id": 0, "name": 2}
OFFICIAL_COLUMNS = {"id": 0, "name": 3}
AWARD_COLUMNS = {"id": 0, "name": 7}
LOTTERY_POOL_COLUMNS = {"id": 0, "name": 3}
TEXAS_TYPE_COLUMNS = {"id": 0, "name": 2}

#: **`columns={"id": 0, "name": None}` is the MEASUREMENT, not an omission.**
#:
#: `Plugin.ITEM_COLUMNS` is `{"id": 0, "name": 1}` -- an `itemtype.dat` fact --
#: and a positional spec that says nothing about its columns inherits it.
#: **`label_key=None` does NOT suppress that**: a row table takes its label
#: from `columns` and never consults `label_key`. So a `columns=None` row spec
#: lists whatever column 1 happens to hold, which for a config table is a
#: number.
#:
#: `None` says the id stands alone, `browse` prints an empty label, and
#: `label_key=None` is what lets the blank-label guard permit that. The
#: control still spans two columns, so the delimiter and the row's position
#: are still checked back against the bytes -- see `catalog.control_at_row`.
NO_NAME_COLUMN = {"id": 0, "name": None}

#: **The one table this plugin declares whose ID IS NOT COLUMN 0.** MEASURED
#: 2026-09-07 over `hairface_storage_type.dat` on all ten installs:
#:
#:     build  rows   col 0 distinct   col 1 distinct   (col0,col1) distinct
#:     6907    408        2               340                408
#:     6968    408        2               340                408
#:     7009    186        2               158                186
#:     7065    415        2               340                415
#:     7083    415        2               340                415
#:     7110    415        2               340                415
#:     7135    195        2               165                195
#:     7170    417        2               340                417
#:     7182    417        2               340                417
#:     7189    417        2               340                417
#:
#: Column 0 is a hair/face category and carries almost no information; column
#: 1 is the sub-id and REPEATS; the pair is unique on every one of the ten and
#: `TableSpec.columns` cannot express a composite key. **Declaring a non-unique
#: id is a claim, so it is made here in words**: `browse` lists 408 rows
#: against 340 distinct ids on 6907, the repeats belong on
#: `Catalog.duplicated`, and the alternative -- column 0 -- prints `0` beside
#: every row, which is the id half of the defect `NO_NAME_COLUMN` prevents.
#:
#: The NAME is column 3 (`YouthSpark`, `BloomingYouth`, `TenderLove`; on 7182
#: and 7189 row 2 reads `BlossomYouth`), non-numeric on every row that reaches
#: it. `patch7205` measured the same two columns independently on its own
#: install, which makes the column a cross-build reading rather than a
#: borrowed constant.
#:
#: **WHY THIS WAS HELD BACK UNTIL NOW, AND WHAT WAS WRONG WITH THE REASON.**
#: The refusal in place before this change said 7009 and 7135 recover "rows
#: TWO fields wide -- column 3 does not exist there at all", so declaring the
#: column "would return 0 rows against a catalogue counting 186 and 195". That
#: is a `min(len(row))` read reported as the table's width. MEASURED, the
#: widths on 7009 are 2x1, 3x34, 9x5, 10x7 and 11x139, and on 7135 2x10, 3x4,
#: 9x20, 10x11 and 11x150: **151 of 186 rows and 181 of 195 carry the name.**
#: The loss was real and it was never total. Both installs are handled rather
#: than excepted -- `Plugin.browse` now gives a row too short for its name
#: column an EMPTY label instead of dropping it, which the held-back comment
#: itself named as the alternative -- so the count `browse` returns still
#: equals the count `catalogs` reports on all ten.
HAIRFACE_COLUMNS = {"id": 1, "name": 3}

#: The `ini/*.dat` tables, with the grammar of each MEASURED FROM ITS UNDAMAGED
#: LINES rather than from the recovered text.
#:
#: **That distinction is not pedantry, it changed nine answers.** A recovered
#: row is truncated at the first damaged block, so a table of 64-field rows
#: comes back as rows of 2, 7, 31 and 64 fields -- and `ini_census.classify_text`
#: reads varying widths as `single-column`. Classifying the recovered text
#: declared `itemtype`, `magictype`, `item_value_type`, `prof_lev_benefit` and
#: five more as `KIND_LIST`, which would have listed each one as a column of
#: whole lines. Classifying only the lines that came back whole gives the
#: file's real shape.
#:
#: The trailing figure on each line is `recovered of lines, block coverage`,
#: measured 2026-08-30 against `Clients/6907` with the 3,433,354-block
#: dictionary at `derived/7878-dat-decrypted/block_dict.pkl`. Reproduce the
#: whole table with `py -3 -m unittest tests.test_patch6907 -v`.
#:
#: FOUR SUBJECTS CARRY A `:dat` SUFFIX AND IT IS NOT COSMETIC
#: ----------------------------------------------------------
#: All ten builds ship `ast_prof_inauguration`, `fate_rank`,
#: `globallotterycondition` and `task_reward_type` as BOTH a `.ini` and a
#: `.dat`, and the borrowed 6609 census already declares the `.ini` half under
#: the bare name. `table_specs` drops a borrowed spec whose SUBJECT is already
#: taken, so before this change the `.dat` won the bare name and the `.ini`
#: **became unreachable on all ten with nothing saying so** -- verifiable as::
#:
#:     any(f.lower() == "fate_rank.ini" for f in
#:         {s.subject: s.filename for s in plug.table_specs(root)}.values())
#:     -> False
#:
#: **And the `.dat` is not uniformly the better file.** MEASURED on 7189:
#: `globallotterycondition.ini` is 25,150 bytes stamped 2020-05-07 against a
#: 12,131-byte 2019-06-20 `.dat` -- and the `.dat` is byte-identical on all ten
#: builds while the `.ini` grows -- whereas `task_reward_type.dat` is 38,584
#: bytes stamped 2020-12-22 against a 924-byte 2016 `.ini`. One rule cannot
#: pick correctly for both pairs, so neither is dropped. `patch7205` made the
#: same call on the same four stems and named this plugin's defect while doing
#: it; this closes it.
SPECS_DAT = (
    # **PROMOTED FROM `SPECS_UNSETTLED` 2026-09-07, and it is the ONE table in
    # this list that is not `@@` rows.** It was held back for a reason the
    # other twelve never had -- see backlog §13: it decodes 393 of 393 lines
    # whole and always did, so "no undamaged line" was never its problem;
    # nobody had established what a ROW is. It is SPACE-separated, `~` stands
    # in for a space inside a field, and it opens with three
    # `;------- Empty Name -------` comment lines that `rows_from_text`
    # already skips. 390 rows of 7 fields, ids unique, columns in
    # `ACHIEVEMENT_COLUMNS`.
    #
    # **The one row that is not 7 fields is a fact about the file, not a
    # decode failure**: id 19999 writes its name as `Comprehensive
    # Achievement` with a literal space where every other row writes `~`, so
    # a whitespace split gives it 8. Recorded rather than smoothed over --
    # its name reads `Comprehensive` in a listing, and truncating a name is
    # the smallest wrong answer available here.
    TableSpec('achievement',               'Achievement.dat',                   KIND_SPACE_ROWS, CODEC, label_key=None,
              columns=ACHIEVEMENT_COLUMNS),  # 390 rows of 7 fields, 393 of 393 lines, 2,843 blocks, 0 unknown
    TableSpec('award_config',              'award_config.dat',                  KIND_AT_ROWS,   CODEC, label_key=None,
              columns=AWARD_COLUMNS),  # 108 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('battlepass_score_reward',   'battlepass_score_reward.dat',       KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 128 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    # **NO LABEL COLUMN, MEASURED, AND NOW SAID SO.** Column 4 is the only
    # non-numeric field and it is not a name: it is a reward MAP -- `1:0` on
    # 6907, `1:3327403 2:3327404 3:3327405 ...` on the later eight. It is a
    # PACKED NUMERIC TUPLE and reads as "text" only because `str.isdigit()` is
    # False for a string with a space or a colon in it; `dat_census`'s name
    # rule has exactly two false positives on this era and this is one.
    # Leaving it on the `ITEM_COLUMNS` default was not neutral -- it labelled
    # every row with column 1, a date-shaped id (`20190625`).
    TableSpec('battlepass_season',         'battlepass_season.dat',             KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 1 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('battlepass_task',           'battlepass_task.dat',               KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 40 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('coat_storage_attr',         'coat_storage_attr.dat',             KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 14 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('exchange_shop_goods_ex',    'exchange_shop_goods_ex.dat',        KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 37 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('nosuch_config',             'nosuch_config.dat',                 KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 19 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('rune_storage_attr',         'rune_storage_attr.dat',             KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 22 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    # **NO LABEL COLUMN, and the SHAPE is not settled across the ten either.**
    # On 6907 this is 81 rows of 12 whose column 2 is non-numeric on every row
    # -- and reading one shows why that is not a name: `1-1 : 3-565992 :
    # 5-3500000000`, a prize tuple. On the other nine installs the file
    # recovers as ~5,400 rows of 6 with no fully-text column at all. Declared
    # `NO_NAME_COLUMN` on all ten: the 6907 column is a packed tuple and the
    # other nine have nothing, so "no name" is right on every build and the
    # default was labelling all ten from a column of numbers.
    TableSpec('texas_match_prize',         'texas_match_prize.dat',             KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 81 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('texas_match_type',          'texas_match_type.dat',              KIND_AT_ROWS,   CODEC, label_key=None,
              columns=TEXAS_TYPE_COLUMNS),  # 172 rows, 100% of blocks, 0 unknown -- promoted from SPECS_UNSETTLED 2026-09-06
    TableSpec('item',                   'itemtype.dat',                   KIND_AT_ROWS,   CODEC, label_key=None),  # 5,387 of 22,265 lines, 86.4% blocks
    # **Declared `empty`, which is the classifier's own verdict, and NOT
    # `block96`.** 6090, 6609 and 7878 all ship this table with content; 6907
    # ships it as a 0-byte file, and that is a fact about the client rather
    # than a table we failed to read. Giving it the block96 codec would be a
    # claim about a cipher applied to zero bytes, and
    # `test_the_declared_codec_is_what_inidat_classifies` catches exactly that
    # -- it did, on the first draft of this list.
    TableSpec('item:sub',               'ItemtypeSub.dat',                KIND_AT_ROWS,   CODEC_EMPTY, label_key=None,
              refusal="the client ships ItemtypeSub.dat as a 0-byte file. "
                      "6090, 6609 and 7878 all carry it with content, so its "
                      "absence here is the client's, not the reader's."),
    TableSpec('item:value',             'item_value_type.dat',            KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 1,646 of 1,664 lines, 94.8% blocks
    TableSpec('item:refine',            'item_refine_attr.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 57 of 67 lines, 45.7% blocks
    TableSpec('item:refine:cost',       'item_refine_cost.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 4 of 4 lines, 100.0% blocks
    TableSpec('item:refine:effect',     'item_refine_effect.dat',         KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 648 of 648 lines, 100.0% blocks
    TableSpec('item:refine:effect:ex',  'item_refine_effect_ex.dat',      KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 132 of 132 lines, 100.0% blocks
    TableSpec('item:refine:upgrade',    'item_refine_upgrade.dat',        KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 702 of 702 lines, 100.0% blocks
    TableSpec('monster',                'Monster.dat',                    KIND_SECTIONS,  CODEC, label_key=None),  # 1 of 51,594 lines, 84.6% blocks -- REFUSED by the reader
    TableSpec('mount',                  'mounttype.dat',                  KIND_SECTIONS,  CODEC, label_key='Title'),  # 1,742 of 25,022 lines, 99.4% blocks
    # The NAME is column 3 here too -- the only non-numeric column of the
    # 53, on all 2,409 rows. `ITEM_COLUMNS` would label them `1`.
    TableSpec('magic',                  'magictype.dat',                  KIND_AT_ROWS,   CODEC, label_key=None,
              columns=MAGIC_COLUMNS),  # 2,409 rows, 100% of blocks
    TableSpec('magic:ex',               'magictypeex.dat',                KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 12 of 12 lines, 100.0% blocks
    TableSpec('magic:op',               'magictypeop.dat',                KIND_CSV_ROWS,  CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 103 of 303 lines, 84.5% blocks
    TableSpec('magic:auto',             'AutoUseMagic.dat',               KIND_SECTIONS,  CODEC, label_key=None),  # 5 of 75 lines, 20.6% blocks
    TableSpec('map:dest',               'MapDestination.dat',             KIND_SECTIONS,  CODEC_REFUTED, label_key='title'),  # 170 of 2,471 lines, 53.3% blocks
    TableSpec('help',                   'UserHelpInfo.dat',               KIND_SECTIONS,  CODEC, label_key=None),  # 1,508 of 24,885 lines, 100.0% blocks
    TableSpec('ability_score',          'ability_score.dat',              KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 258 of 258 lines, 98.1% blocks
    TableSpec('activity_reward_type',   'activity_reward_type.dat',       KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 6 of 6 lines, 93.8% blocks
    TableSpec('ast_prof_inauguration:dat',  'ast_prof_inauguration.dat',      KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 7 of 7 lines, 100.0% blocks
    TableSpec('auction_buy_back',       'auction_buy_back.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 19 of 19 lines, 100.0% blocks
    TableSpec('beasts_attr',            'beasts_attr.dat',                KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 100 of 100 lines, 100.0% blocks
    TableSpec('cards_lottery_pool',     'cards_lottery_pool.dat',         KIND_AT_ROWS,   CODEC, label_key=None),  # 309 of 309 lines, 100.0% blocks
    TableSpec('coat_storage_type',      'coat_storage_type.dat',          KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 191 of 274 lines, 57.0% blocks
    TableSpec('debugconfig',            'DebugConfig.dat',                KIND_SECTIONS,  CODEC, label_key=None),  # 1 of 13 lines, 100.0% blocks
    TableSpec('exchange_shop_goods',    'exchange_shop_goods.dat',        KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 7 of 82 lines, 41.1% blocks
    TableSpec('fate_exp',               'fate_exp.dat',                   KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 396 of 396 lines, 100.0% blocks
    TableSpec('fate_rank:dat',              'fate_rank.dat',                  KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 200 of 200 lines, 100.0% blocks
    TableSpec('global_lottery_pool',    'global_lottery_pool.dat',        KIND_AT_ROWS,   CODEC, label_key=None,
              columns=LOTTERY_POOL_COLUMNS),  # 80 of 96 lines, 75.8% blocks
    TableSpec('globallotterycondition:dat', 'globallotterycondition.dat',     KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 151 of 151 lines, 100.0% blocks
    TableSpec('golden_league_limit',    'golden_league_limit.dat',        KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 7 of 7 lines, 100.0% blocks
    TableSpec('hair_color_type',        'hair_color_type.dat',            KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 270 of 270 lines, 100.0% blocks
    # **THE NAME IS COLUMN 3 AND IT IS NOW DECLARED, WITH THE ID AT COLUMN 1.**
    # The full measurement, including what was wrong with the reason this was
    # held back on, is in `HAIRFACE_COLUMNS`. The short version: 7009 and 7135
    # recover the file damaged, the previous comment read `min(row width)` as
    # the table's width and concluded a declaration "would return 0 rows",
    # and the truth is 151 of 186 and 181 of 195 rows carry the name.
    TableSpec('hairface_storage_type',  'hairface_storage_type.dat',      KIND_AT_ROWS,   CODEC, label_key=None,
              columns=HAIRFACE_COLUMNS),  # 74 of 122 lines, 32.8% blocks
    TableSpec('hundred_weapon',         'hundred_weapon.dat',             KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 81 of 81 lines, 100.0% blocks
    TableSpec('jianghu_attribute_add',  'jianghu_attribute_add.dat',      KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 90 of 90 lines, 100.0% blocks
    TableSpec('jianghu_cultivate_condition', 'jianghu_cultivate_condition.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 9 of 9 lines, 100.0% blocks
    TableSpec('kok_hall_map',           'kok_hall_map.dat',               KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 4 of 4 lines, 100.0% blocks
    TableSpec('kok_newbie_info',        'kok_newbie_info.dat',            KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 6 of 6 lines, 100.0% blocks
    TableSpec('kokquickpay',            'kokquickpay.dat',                KIND_SECTIONS,  CODEC, label_key=None),  # 1 of 3 lines, 100.0% blocks
    TableSpec('magic_group_rate',       'magic_group_rate.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 14 of 14 lines, 100.0% blocks
    TableSpec('official_type',          'official_type.dat',              KIND_AT_ROWS,   CODEC, label_key=None,
              columns=OFFICIAL_COLUMNS),  # 20 of 20 lines, 100.0% blocks
    TableSpec('operating_prize',        'operating_prize.dat',            KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 79 of 153 lines, 47.3% blocks
    TableSpec('point_extend',           'point_extend.dat',               KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 10 of 10 lines, 100.0% blocks
    TableSpec('prof_lev_benefit',       'prof_lev_benefit.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 512 of 549 lines, 95.1% blocks
    TableSpec('prof_lev_up',            'prof_lev_up.dat',                KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 110 of 194 lines, 57.7% blocks
    TableSpec('prof_title_benefit',     'prof_title_benefit.dat',         KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 33 of 33 lines, 97.6% blocks
    TableSpec('quickpay',               'quickpay.dat',                   KIND_SECTIONS,  CODEC, label_key=None),  # 95 of 285 lines, 99.0% blocks
    TableSpec('recommend_magic_group',  'recommend_magic_group.dat',      KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 9 of 9 lines, 100.0% blocks
    TableSpec('roulette_chip_info',     'roulette_chip_info.dat',         KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 2 of 2 lines, 100.0% blocks
    TableSpec('rune_levexp',            'rune_levexp.dat',                KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 40 of 40 lines, 100.0% blocks
    TableSpec('runeeffect',             'runeeffect.dat',                 KIND_SECTIONS,  CODEC, label_key=None),  # 375 of 804 lines, 100.0% blocks
    TableSpec('spirit_rate',            'spirit_rate.dat',                KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 3 of 16 lines, 39.5% blocks
    TableSpec('stagegoal',              'StageGoal.dat',                  KIND_SECTIONS,  CODEC, label_key=None),  # 8 of 125 lines, 16.4% blocks
    TableSpec('task_reward_type:dat',       'task_reward_type.dat',           KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 87 of 102 lines, 67.3% blocks
    TableSpec('texas_match_condition',  'texas_match_condition.dat',      KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 44 of 44 lines, 76.7% blocks
    TableSpec('texas_match_stage',      'texas_match_stage.dat',          KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 47 of 54 lines, 97.7% blocks
    TableSpec('title_type',             'title_type.dat',                 KIND_AT_ROWS,   CODEC, label_key=None,
              columns=TITLE_COLUMNS),  # 24 of 34 lines, 53.6% blocks
    TableSpec('token_type',             'token_type.dat',                 KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 7 of 7 lines, 100.0% blocks
    TableSpec('webinfo',                'WebInfo.dat',                    KIND_SECTIONS,  CODEC, label_key=None),  # 2 of 14 lines, 100.0% blocks
    TableSpec('xuanbao_rand_attr',      'xuanbao_rand_attr.dat',          KIND_AT_ROWS,   CODEC, label_key=None, columns=NO_NAME_COLUMN),  # 65 of 65 lines, 100.0% blocks
)

#: `instancetype.dat`'s columns. **The one censused row table that HAS a
#: name**, and it is column 1: `2 Exorcism`, `11 EvilArray`,
#: `13 TwinCity\`sNight`. Measured, not inherited -- it happens to agree with
#: `ITEM_COLUMNS` and it is written out anyway, because the twenty-four
#: tables beside it show what the same inheritance costs when it is wrong.
INSTANCETYPE_COLUMNS = {"id": 0, "name": 1}

#: The `.dat` tables the 2026-09-07 per-build census settled, which no spec
#: named before. Twenty-five `@@` row tables and six section tables.
#:
#: **EACH IS PRESENT ON A DIFFERENT SUBSET OF THE TEN BUILDS**, which is why
#: this cannot be a second block inside `SPECS_DAT`: a spec for a file the
#: build does not ship produces a "not present" refusal on that build, so one
#: flat list would put 3-shipped `syn_skill_type` in front of 6907 as a
#: permanent error. `table_specs` filters this tuple TO DISK, the same
#: mechanism that already filters the borrowed 6609 census, and a table's
#: absence then reads as absence rather than as a failure.
#:
#: MEASURED 2026-09-07 with `py -3 tools/dat_census.py`, over all ten installs
#: this plugin serves, with the dictionary `DictionaryIdentity` pins. The
#: method is the one `SPECS_DAT` above records: **the grammar is classified
#: over the UNDAMAGED LINES**, because a recovered row is truncated at the
#: first damage and a table of 26-field rows comes back as rows of 2, 7 and 26
#: -- which reads as `single-column`.
#:
#: The builds and their row counts, from the census. The counts DIFFER per
#: build, which is the point of measuring each one:
#:
#:     battlepass_level          7009-7189   100 rows, then 50 from 7135
#:     combat_gear               7189 only   150
#:     exchange_shop_lev         6968-7189   16 on all nine
#:     godsoul_upgrade_config    7170-7189   257
#:     gouyu_immortal            7065-7189   7 on 7065, 10 from 7083
#:     gouyu_type                7065-7189   315
#:     instance_enter_condition  7009-7189   42..309 (damaged on five)
#:     instance_prize            7065-7189   480..794; 7009 ships it 0 bytes
#:     instance_star_condition   7065-7189   90..144; 7009 ships it 0 bytes
#:     instancetype              7009-7189   6..309 (damaged on five)
#:     marketing_active          6968-7189   24..235
#:     newslot_broadcast         7170-7189   3
#:     newslot_line              6968-7189   20
#:     newslot_line_probability  7170-7189   49
#:     newslot_roulette          6968-7189   8
#:     newslot_type              6968-7189   20..110
#:     newstone_limit            7170-7189   12
#:     process_goal              7009-7189   9 on 7009 (damaged), 29 after
#:     process_task              7009-7189   85 on 7009 (damaged), 191 after
#:     random_task_cost          7065-7189   9
#:     syn_formtype              7170-7189   187
#:     syn_skill_type            7170-7189   1,400
#:     syndicate_level           7170-7189   9
#:     xuanbao_addition_attr     6968-7189   57
#:     xuanbao_compose_attr_limit 6968-7189  13
#:
#: **Every one of the 25 has UNIFORM ROW WIDTH on every build** -- not one
#: ragged row anywhere -- and column 0 is numeric on every row. That is the
#: structural evidence that `@@` is the real delimiter and not a grammar that
#: merely consumed the file; `single-column` consumes any text at all, which
#: is why "the census said at-rows" on its own is not enough.
#:
#: Column 0 is UNIQUE on 23 of the 25. `instance_prize` and
#: `instance_star_condition` repeat it -- one row per prize and per star of an
#: instance -- so their id is a COMPOSITE key's first field. Recorded rather
#: than smoothed: `Catalog.duplicated` is where a repeat belongs, and a spec
#: claiming a unique id it does not have would be a claim nothing checked.
SPECS_DAT_CENSUSED = (
    TableSpec('battlepass_level',   'battlepass_level.dat',   KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('combat_gear',        'combat_gear.dat',        KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('exchange_shop_lev',  'exchange_shop_lev.dat',  KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('godsoul_upgrade_config', 'godsoul_upgrade_config.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('gouyu_immortal',     'gouyu_immortal.dat',     KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('gouyu_type',         'gouyu_type.dat',         KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('instance_enter_condition', 'instance_enter_condition.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('instance_prize',     'instance_prize.dat',     KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('instance_star_condition', 'instance_star_condition.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    # The one with a name, and it is measured rather than inherited.
    TableSpec('instancetype',       'instancetype.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=INSTANCETYPE_COLUMNS),
    TableSpec('marketing_active',   'marketing_active.dat',   KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newslot_broadcast',  'newslot_broadcast.dat',  KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newslot_line',       'newslot_line.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newslot_line_probability', 'newslot_line_probability.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newslot_roulette',   'newslot_roulette.dat',   KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newslot_type',       'newslot_type.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('newstone_limit',     'newstone_limit.dat',     KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('process_goal',       'process_goal.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('process_task',       'process_task.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('random_task_cost',   'random_task_cost.dat',   KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('syn_formtype',       'syn_formtype.dat',       KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('syn_skill_type',     'syn_skill_type.dat',     KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('syndicate_level',    'syndicate_level.dat',    KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('xuanbao_addition_attr', 'xuanbao_addition_attr.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),
    TableSpec('xuanbao_compose_attr_limit', 'xuanbao_compose_attr_limit.dat', KIND_AT_ROWS, CODEC, label_key=None, columns=NO_NAME_COLUMN),

    # -- the section tables ------------------------------------------------
    #
    # **FOUR OF THESE SIX ARE `plaintext`, NOT `block96`, and the codec is the
    # classifier's verdict rather than the era's.** `Shop.dat`,
    # `SkinVersion.dat`, `GetPlatformInfo.dat` and `ndloginServer.dat` were
    # never enciphered on any of the ten builds. `patch7878`'s Shop.dat is
    # plaintext too and `zephyr1057` records the same thing about its own
    # ("a spec that carried the family from its neighbours would decrypt a
    # file that was never encrypted, and tqdat.decrypt does not raise").
    # `test_the_declared_codec_is_what_inidat_classifies` enforces it.
    #
    # **`Shop.dat`'s label key is `Name`, MEASURED here, and `zephyr1057`
    # measured `label_key=None` for the same filename.** Both are right about
    # their own build: 160 of this file's 161 sections carry `Name=Drugstore`
    # and the odd one out is the `[Header]` block, which is 99% and clears the
    # 90% bar; Zephyr's copy carries no such key. This is the case the
    # "never declare a table's shape from its FILENAME" rule is about, and it
    # is the one place in this list where two builds genuinely disagree.
    TableSpec('shop',            'Shop.dat',            KIND_SECTIONS, "plaintext", label_key='Name'),
    # 153-161 sections on ten builds; the label is `Name` on 99% of them.
    TableSpec('skinversion',     'SkinVersion.dat',     KIND_SECTIONS, "plaintext", label_key=None),
    # `[Conquer]` / `Version=2001`, byte-for-byte the same 23-byte file on
    # all ten. One section, no label key -- MEASURED, and the note is that
    # this is a version stamp rather than a table. It is declared anyway
    # because a NAMED subject that refuses nothing is how `comod catalogs`
    # tells "this client has none" from "we never heard of it", which is
    # `censused.py`'s own founding argument.
    TableSpec('getplatforminfo', 'GetPlatformInfo.dat', KIND_SECTIONS, "plaintext", label_key=None),
    TableSpec('ndloginserver',   'ndloginServer.dat',   KIND_SECTIONS, "plaintext", label_key=None),
    # `[Default] GameName=Conquer` and `[Order] 0..7` -- two sections, 11
    # lines, exact consumption on all ten.
    TableSpec('appkey',          'Appkey.dat',          KIND_SECTIONS, CODEC, label_key=None),
    # block96, 4 blocks, 0 unknown, on 7135-7189 only. One section.
    TableSpec('autouseimmortal', 'AutoUseImmortal.dat', KIND_SECTIONS, CODEC, label_key=None),
    # block96, 0 unknown blocks, 7065-7189. One `[AutoLife]` section of
    # 18-19 keys. Named apart from `magic:auto` (`AutoUseMagic.dat`), which
    # is a different file with a different cipher outcome.
)

#: The head of every `SPECS_UNSETTLED` refusal. The TAIL is per table,
#: because the two survivors are not held back for the same reason and one
#: sentence covering both is how a list like this stops being actionable.
_UNSETTLED = (
    "the block dictionary holds NOT ONE of this file's blocks, so no line "
    "comes back at all and its grammar is not established on this build -- "
    "it is named rather than declared with a guessed kind, which would turn "
    "'we have not settled this' into a row count. `py -3 tools/datdict.py "
    "decode <client>/ini/{f}` prints the block coverage. ")

#: **Two `.dat` tables, both at 0% block coverage, for two DIFFERENT
#: reasons.** Declared so the subject is NAMED and the reason travels with
#: it, exactly as `plugins/catalog/censused.py` keeps `partial` and `mixed`
#: files out of the generated specs.
#:
#: THIRTEEN BECAME THREE (2026-09-06) AND THREE BECAME TWO (2026-09-07).
#: The ten that left first now decode whole as `@@` rows and are in
#: `SPECS_DAT`. `Achievement.dat` left second, and it is worth separating
#: from them: it never had the damage problem at all -- it decoded 393 of 393
#: lines whole under the old dictionary too -- it had an UNMEASURED GRAMMAR,
#: which is backlog §13's whole distinction. It is space-rows, and it is in
#: `SPECS_DAT` above.
#:
#: **What is left is damage only, and this list can no longer demonstrate the
#: other reason.** That is a real change to what
#: `tests/test_patch6907.TheUnsettledTablesAreNamedNotGuessed` can assert,
#: and it is recorded there rather than left for the next reader to discover
#: from a vacuous subtest.
#:
#: MEASURED 2026-09-07 against every one of the ten installs this plugin
#: serves, with the dictionary `DictionaryIdentity` pins:
#:
#:     levexp.dat        4,030 B    335 blocks, 335 unknown  (0.0%)
#:     ServerPlay.dat    5,128 B +  427 blocks, 427 unknown  (0.0%)
#:
#: -- 0.0% on all ten builds, not "too little": the dictionary holds nothing
#: of either file. `SPECS_DAT`'s worst survivor is 16.4%.
SPECS_UNSETTLED = (
    # **THE GRAMMAR IS KNOWN AND THE TABLE STILL CANNOT BE READ HERE, and
    # those are different sentences.** `levexp.dat` is byte-identical (sha256
    # 4482cfaa...) on 6907, 7205 and 7878, and its PLAINTEXT is on this box:
    # `derived/7878-dat-decrypted/levexp.dat.out`, 4,030 B, 100% printable,
    # space-separated rows of 5 numeric fields (`0 1 120 2 3`).
    #
    # So the shape is settled and the SUBJECT is still refused, because the
    # reader for this build reads through the dictionary and the dictionary
    # holds none of it -- held out BY DESIGN (`tools/datdict.EXCLUDE`,
    # `patch7878.HELD_OUT`): it is the corpus's only seed-1234 table and has
    # zero shared-block edges, so the data cannot attribute its key group.
    # Wiring the sibling's `.out` in would teach this reader a second
    # mechanism -- read another install's derived output when the bytes match
    # -- which `patch7205.UNCOVERED_TABLES` already recorded and declined for
    # the same reason. Declaring a kind on the strength of a file we do not
    # actually open here is exactly the move this list refuses.
    TableSpec('levexp', 'levexp.dat', KIND_AT_ROWS, CODEC, label_key=None,
              refusal=_UNSETTLED.format(f='levexp.dat') + (
                  "0 of 335 blocks are known, and that is DELIBERATE rather "
                  "than a failure: levexp.dat is the one seed-1234 table in "
                  "the 7878 corpus, with no shared-block edge to attribute "
                  "its key group, so tools/datdict.py holds it out of the "
                  "dictionary. Its plaintext DOES exist on a box with the "
                  "derived corpus -- derived/7878-dat-decrypted/"
                  "levexp.dat.out, which this build's file is byte-identical "
                  "to -- and it is space-separated rows of five numeric "
                  "fields. That route is recorded and NOT wired: this "
                  "reader would be declaring a grammar for bytes it never "
                  "opened.")),
    # The corpus's one genuine residue. `patch7878.NOT_CONTENT` names it as
    # the single file whose ndac-oracle `.out` came back as ciphertext, and
    # this build's copy is not even the same file (sha256 c1840af7 here
    # against 7878's b8874866), so 7878's plaintext would not apply if it
    # existed. It also grows on every build -- 5,128 B here, 5,696 B by 7205
    # -- and `core/inidat.py` puts it in three different families across the
    # ten installs (`block96`, `block96-candidate-refuted`, `unknown`), which
    # is what a file nobody can classify looks like from the outside.
    TableSpec('serverplay', 'ServerPlay.dat', KIND_AT_ROWS, CODEC,
              label_key=None,
              refusal=_UNSETTLED.format(f='ServerPlay.dat') + (
                  "0 of 427 blocks are known and no route opens this file at "
                  "all: not RSA at any alignment, not TQ at seed 9527 or "
                  "1234, block96-candidate-refuted. It is the one genuine "
                  "residue patch7878.NOT_CONTENT names, and this build's "
                  "copy differs from 7878's, so even 7878's answer would not "
                  "be this file's answer.")),
)

#: The two binary tables. `GameMap.dat` is the one binary grammar this project
#: has established, and it reads identically here: **444 records**, first
#: `id=1000 path='map/map/desert.7z'`, flag 128 x177 and flag 256 x267, the
#: parse consuming all 14,196 bytes exactly. `Action.dat` is named and refused
#: -- but its RECORD shape is settled now (2026-09-07) and its COLUMNS are
#: not, which is a different refusal from the one it used to carry; the spec
#: below says so in its own words.
SPECS_BINARY = (
    # **THE RECORD SHAPE IS MEASURED AND THE SUBJECT IS STILL REFUSED**, with
    # its own refusal rather than the base class's shared binary-plain one.
    # That shared sentence says "record shape not established", which stopped
    # being true of this file on 2026-09-07 -- but it is the DEFAULT for every
    # binary table nobody has measured, so it is sharpened per spec rather
    # than reworded for everyone. (`MagicType.dat` on 5017/5065 was the other
    # table under it; that one was measured AND promoted, to
    # `KIND_MAGIC_RECORDS`, because its columns resolve and these do not.)
    TableSpec("action", "Action.dat", KIND_SECTIONS, "binary-plain",
              refusal=(
                  "binary record layout, not sections or rows. The RECORD "
                  "SHAPE is settled -- u32 count=53, then 53 records of five "
                  "little-endian u32, consuming all 1,064 bytes exactly, the "
                  "same structural control GameMap.dat passes, and the file "
                  "is byte-identical on all 24 installs that ship it. THE "
                  "COLUMNS ARE NOT: a row is five anonymous 32-bit numbers "
                  "(col 0 in [1999998071, 1999999884], col 1 in [999999001, "
                  "999999903], cols 2 and 4 uniform over the 32-bit range, "
                  "col 3 an enum of exactly {10, 25, 33, 66}). No table in "
                  "this client's ini/ holds those ids and no binary in the "
                  "install names the file -- searched ascii and utf-16-le "
                  "over all 20 .exe/.dll, with the control that the same "
                  "search finds GameMap.dat and ActionCtrl.ini in "
                  "GameData.dll. Serving it would put 53 rows of unlabelled "
                  "numbers in front of a user who can edit them. See "
                  "tests/test_patch6907.py:"
                  "ActionDatRecordShapeIsMeasuredButNotItsColumns.")),
    TableSpec("gamemap", "GameMap.dat", KIND_GAMEMAP, "binary-plain"),
)

#: The npc family, plaintext and unencrypted on this build like every other.
#: All five files present. `npc.ini` carries **2,325** sections here against
#: 6609's 2,784 and 7878's 4,087 -- CONTENT GOING BACKWARDS across a patch,
#: the same shape as 6609's shrinking `mount.dbc`, and recorded rather than
#: smoothed over.
SPECS_NPC = npc_specs(("npc.ini", "NpcX.ini", "terrainnpc.ini", "npcex.ini",
                       "SlotNpc.ini"))

#: **The `.dat` the 2026-09-07 census MEASURED AND STILL DID NOT DECLARE, and
#: why each one.** `{filename_lower: reason}`.
#:
#: This list is the point of the exercise as much as `SPECS_DAT_CENSUSED` is.
#: A spec is a claim that the build knows the table's shape, and COMod hands a
#: user an editor for every subject it declares -- so declaring a table on weak
#: evidence is worse than leaving it out. `Action.dat` is the standing
#: precedent: its record shape is settled EXACTLY and it is still refused,
#: because a row of five anonymous numbers is not a table.
#:
#: `tests/test_patch6907.CensusedDatTablesAreMeasuredNotAssumed` asserts that
#: every name here is still undeclared AND still fails the criterion named,
#: so a build that changes one of these files makes this list wrong out loud.
UNDECLARED_DAT = {
    # **The one that would have been declared by a careless reader**, and the
    # reason it is here rather than above. The file has NO NEWLINE IN IT, so
    # the census sees ONE line -- and one line is consumed exactly by every
    # grammar there is, which is why the verdict came back `flat-keys` with
    # `csv-rows` and `space-rows` equally exact. The decode is a 32-byte
    # `Tips` header then `{u32 id, u32 len, char[len]}` records: a BINARY
    # record table with the same shape as `GameMap.dat`. `zephyr1057` already
    # holds this finding for its own copy, where declaring it as space-rows
    # "parsed 2 rows" and THE POSITIVE CONTROL PASSED, because two garbage
    # tokens genuinely are adjacent in the file.
    "tips.dat": (
        "a binary record table, not a text grammar. The file contains no "
        "newline, so the census classifies ONE line and every grammar "
        "consumes it exactly -- the verdict is the tie-break order speaking, "
        "not the file. The decode is a 32-byte name header then {u32 id, u32 "
        "len, char[len]} records carrying the loading-screen hints. Same "
        "finding as zephyr1057's Tips.dat, where a space-rows declaration "
        "parsed 2 rows and the control PASSED on garbage."),
    "gameloadinfo.dat": (
        "1 of its 12 lines survives the grammars: 11 residual lines, verdict "
        "`partial` on all ten builds. The bytes are a byte-shifted INI "
        "(`Fdjf` for `Game`) that inidat classifies `plaintext` because most "
        "of them are printable, so there is a real table here under a "
        "transform nobody has established. Not a grammar this build knows."),
    "tqist.dat": (
        "one line, `partial`, and the bytes are not text at all -- a run of "
        "`{` filler after a short binary head. No grammar accounts for it."),
    "silent.dat": (
        "TWO DIFFERENT ANSWERS ON ONE FLEET, and neither is a grammar. "
        "inidat calls it `tq-stream` on 6907 and 6968 and `block96` from "
        "7009, and where it does decode the verdict is `partial`. A file "
        "whose CIPHER changes mid-lineage is a finding; a spec would have to "
        "pick one build's answer for all ten."),
    "emotionico.dat": (
        "inidat cannot classify it at all on 7009-7135 (`unknown`) and the "
        "decode is `partial` on 7170-7189. Undeclared on the builds where it "
        "cannot be opened AND on the builds where it can, because the "
        "grammar is not settled on either."),
    "monster_live.dat": "the client ships it as a 0-byte file on all ten.",
    "new_battle_buff.dat": "the client ships it as a 0-byte file on all ten.",
    "promotion_activity.dat": "the client ships it as a 0-byte file on all ten.",
    "promotion_goods.dat": "the client ships it as a 0-byte file on all ten.",
    "slient.dat": (
        "a 0-byte file on all ten -- and note the spelling: it is NOT "
        "`silent.dat`, which is a separate 9 KB file beside it."),
    "blackjack_cfg.dat": "the client ships it as a 0-byte file on nine builds.",
}

#: The families the census could not open at all, with the count of distinct
#: filenames in each. Recorded as a SHAPE rather than as 40 individual
#: refusals: every one of these is a POSITIVE identification of something that
#: is not a text table on this build, so "undeclared" here means "not this
#: layer's subject", not "not yet measured".
#:
#: MEASURED 2026-09-07 over the ten installs:
#:
#:     rar-mangled       11  c3anp/c3aanp/c3bbnp..c3llnp, effanl -- the `.c3`
#:                           animation packs, an archive format
#:     rsa-mysqldump     10  RaceTrackProp, SHLayout(+800X600), ShowHandLayout
#:                           (+800X600), ShowHandTable, ShowHandTableRace,
#:                           suittype, MyAnimate(+800X600)
#:     binary-plain       4  autoallot, LevelExp, pet, pna_swf
#:     tq-stream          4  client_config, kok_roleview, UserHelpInfo.ini.dat,
#:                           silent (6907/6968 only)
#:     unknown            2  WeaponActionData, WeaponMotionData
#:     shift-obfuscated   1  Play.dat
UNOPENED_DAT_FAMILIES = {
    "rar-mangled": 11, "rsa-mysqldump": 10, "binary-plain": 4,
    "tq-stream": 4, "unknown": 2, "shift-obfuscated": 1,
}

SPECS_6907 = (SPECS_DAT + SPECS_DAT_CENSUSED + SPECS_UNSETTLED
              + SPECS_BINARY + SPECS_NPC)


def _version(root) -> str:
    """`version.dat`, which every official client stamps with its patch number
    and nothing else. The only thing that separates siblings in this lineage --
    6907 answers yes to every format probe 6090 and 6609 do, because its
    compiled tables and archives ARE theirs."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch6907(Patch6090):
    """Official patch 6907: 6609's art, 7878's cipher."""
    #: NOT inherited from `Patch6090`: its `{"operateactivity.ini": "gbk"}`
    #: was measured on 6090's file, not this build's (6907's is strict UTF-8
    #: and fails GBK at byte 2). Empty = plugin-wide encoding, as before.
    TABLE_ENCODING: dict = {}


    #: This plugin reads a build whose `ini/*.dat` are the 96-bit block
    #: cipher and which `tools/dat_census.py` censuses. NOT `BLOCK96_CENSUS`,
    #: which is Patch7205's own measured census DICT (a first draft reused
    #: that name and the dict silently overwrote it). A CLASS attribute, so
    #: a subclass for a later patch (7217, 7250, 7275 extend Patch7205)
    #: inherits the answer, and `tests/test_dat_census.py` asks the plugin
    #: instead of matching a hand-kept tuple of names -- which is what every
    #: new plugin would otherwise have had to edit.
    DAT_CENSUS_ERA = True

    name = "patch6907"
    label = "Official patch client 6907 (block96 .dat era)"
    origin = "official"
    aliases = ("6907",)
    STAMP = STAMP

    #: `latin1`, inherited and NOT re-measured -- see the module docstring.
    #: It round-trips every byte, so it cannot break a positive control; what
    #: it cannot do is display this build's Chinese tables correctly.
    TEXT_ENCODING = "latin1"

    ITEM_COLUMNS = ITEM_COLUMNS

    #: MEASURED on this build rather than inherited, and two of the three
    #: differ from `catalog.MEASURED_SECTION_LABELS`:
    #:
    #:   mount     `Title` is present with a value on >=90% of the 1,742
    #:             sections recovered here (`Maroon~Steed`, ...). 7878's
    #:             mounttype has no `Title` at all and had to fall back to
    #:             `armor`; using `armor` here would print an appearance id
    #:             where the build actually ships a name.
    #:   map:dest  `title`, lowercase, as on the older builds.
    #:   monster   refused on this build, so no label is claimed.
    ROW_LABEL_KEY = {"mount": "Title", "map:dest": "title"}

    notes = (
        "6609's art layer byte for byte -- all twelve .dbc, both .wdf, and the "
        "same five reader row counts -- with 7878's block96 .dat cipher. That "
        "split is the client: tqdat opens NOTHING here and does not raise, "
        "and plugins.detect() returned patch6090 at 0.45, whose tq-stream "
        "specs read 0 rows from every table. The .dat tables are read through "
        "the 7878 ECB block dictionary, which covers 86.4% of itemtype's "
        "BLOCKS and recovers 24.2% of its ROWS -- 5,387 of 22,265, with id "
        "and name intact. Monster.dat is REFUSED: 84.6% of blocks known, 1 "
        "section of ~2,100 recoverable, because section headers are unique "
        "short strings and a marker that eats the newline before one deletes "
        "it. Nothing is copied into Clients/, which stays vanilla."
    )

    #: The five frozen 2008 lookup tables are byte-identical here too
    #: (`3DSimpleObj.ini` f34f56332383, `3dobj.ini` 9202de93aa03,
    #: `3dtexture.ini` 6be2fece83fd, `3dmotion.ini` 9951727aa1ed, `armor.ini`
    #: 8e64691e0f00 -- the same prefixes `patch7878.FROZEN_LOOKUPS` records).
    #: **They matter LESS here than on 7878 and it is worth saying why**: 7878
    #: ships no compiled twin, so the frozen file is all there is and 44.7% of
    #: its npc rows cannot resolve. 6907 ships all twelve `.dbc`, so the stale
    #: `.ini` are the ordinary decoys `core/dbc.py` warns about and the
    #: compiled twin carries the truth. Same files, different consequence.
    FROZEN_LOOKUPS_HAVE_A_COMPILED_TWIN = True

    #: The four CENSUSED `.ini` whose label is not column 1. Those specs are
    #: generated tuples with no `columns` field, so `TableSpec.columns` cannot
    #: reach them and they took `ITEM_COLUMNS`; this is where a build says
    #: otherwise. The five `.dat` tables in the same state carry their
    #: measurement on the spec instead -- see `TITLE_COLUMNS` and its note.
    #:
    #: MEASURED over the SAME TEN INSTALLS as the `.dat` set, and every one of
    #: the four holds on all ten:
    #:
    #:   region.ini         301-303 rows of 14 fields; column 6 is a place
    #:                      name on every row (`TwinCity`), column 1 numeric
    #:                      on every row. Column 7 is a second name field;
    #:                      6 is declared as the finer of the two (82 distinct
    #:                      against 23 here). Column 0 is a MAP id and
    #:                      repeats -- a region row is a named rectangle
    #:                      within a map.
    #:   EventTypeName.ini  24 rows of 3; column 2 the name on 24/24, and
    #:                      column 0 is `1` on every row while column 1 is
    #:                      `01`..`24`, so the ID moves to column 1 as well.
    #:   VipTrans.ini       31 rows of 3; column 2 a city name on 31/31.
    #:   restrain.ini       5 rows of 3; column 2 a description on 5/5, the
    #:                      only text in the row.
    #:
    #: **THE ELEVEN BELOW ARE THE OTHER HALF OF THE SAME MECHANISM, added
    #: 2026-09-07: borrowed `.ini` row tables with NO name column at all.**
    #: The four above correct a label pointed at the wrong column; these
    #: withdraw a label that was never there. MEASURED on all ten installs --
    #: not one of these files has a column that is non-numeric on every row,
    #: so `ITEM_COLUMNS["name"] = 1` was listing a second column of numbers::
    #:
    #:   ast_prof_promote.ini     56 rows of 6
    #:   graphic.ini               1 row of 7 -- `0 1.0 1.0 0.5 ffffffff ...`,
    #:                             a settings row, and column 1 is `1.0`. It
    #:                             is a PACKED value, not a name, which is why
    #:                             the sibling sweep never flagged it: `1.0`
    #:                             is not `isdigit()`, so the label did not
    #:                             look numeric.
    #:   ItemAdd.ini          22,908 rows of 10
    #:   ItemSolidify.ini          2 rows of 2
    #:   tutorType.ini             5 rows of 4
    #:   WeaponShow.ini          109 rows of 3
    #:   WeaponSkillLevelExp.ini  21 rows of 3
    #:   weather.ini               7 rows of 5
    #:
    #: **The last three of the eleven are the `.ini` twins that this change
    #: made reachable in the first place** -- `ast_prof_inauguration.ini` (7
    #: rows of 8), `fate_rank.ini` (200 rows of 7) and `task_reward_type.ini`
    #: (6 space-rows of 32). They were evicted by the `.dat` of the same stem
    #: until the `:dat` suffix went on, and they are measured here on the same
    #: pass rather than left to arrive un-labelled -- an entry restored to a
    #: listing with the ITEM_COLUMNS default on it is the defect this file
    #: spent the change removing, and it would have been a NEW instance of it.
    #: `globallotterycondition.ini` is the fourth twin and is not here: it is
    #: `ini-sections`, not a row table, so `columns` does not reach it.
    #:
    #: `Dynarank.ini`, `Font.ini` and `Cursor.ini` are still NOT here, and
    #: they are the three that were measured and came out NEGATIVE -- see
    #: `PositionalLabelsAreMeasuredPerBuild.UNDECLARED` for Dynarank's five
    #: text columns and no label among them. `Font.ini` and `Cursor.ini` are
    #: `path -> value` rows where COLUMN 0 is the text and the default already
    #: prints the useful half; they read as numeric-labelled and are not the
    #: defect. Leaving Dynarank alone is also a scope decision: eight plugins
    #: share that refusal and changing it for one build only would make 6907
    #: disagree with seven neighbours about one file.
    ROW_COLUMNS = {
        "region": {"id": 0, "name": 6},
        "eventtypename": {"id": 1, "name": 2},
        "viptrans": {"id": 0, "name": 2},
        "restrain": {"id": 0, "name": 2},
        "ast_prof_promote": NO_NAME_COLUMN,
        "graphic": NO_NAME_COLUMN,
        "itemadd": NO_NAME_COLUMN,
        "itemsolidify": NO_NAME_COLUMN,
        "tutortype": NO_NAME_COLUMN,
        "weaponshow": NO_NAME_COLUMN,
        "weaponskilllevelexp": NO_NAME_COLUMN,
        "weather": NO_NAME_COLUMN,
        "ast_prof_inauguration": NO_NAME_COLUMN,
        "fate_rank": NO_NAME_COLUMN,
        "task_reward_type": NO_NAME_COLUMN,
    }

    #: The last build MEASURED to open with the TQ stream cipher. Anything
    #: above it whose compiled tables are present is in this plugin's era.
    LAST_TQ_BUILD = 6868

    #: The bid for an unstamped sibling. Above `Patch6090`'s 0.45 for a
    #: stamped sibling, which is the claim being taken away, and below the
    #: 0.95 any purpose-written plugin makes for its own stamp.
    SIBLING_CONFIDENCE = 0.55

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """0.95 on our own stamp; 0.55 for a sibling of the same era.

        **The stamp decides the first, because the formats cannot.** 6907
        answers yes to every probe `Patch6090.confidence` makes -- its
        compiled tables are literally 6609's files -- so a format-based claim
        would tie with `patch6090` and `patch6609` and be broken by dictionary
        order, which is not evidence.

        **The sibling bid exists because the alternative is a wrong answer,
        not no answer.** MEASURED on `Clients/6907` before this plugin:
        `plugins.detect` returned `patch6090` at 0.45, and its `mount` subject
        reported **1 row with a PASSING positive control** -- a garbage
        section header and a garbage key, "found in an independent re-decode",
        because the witness is a second run of the same wrong decrypt. That is
        a control firing on noise, and it is the concrete case that makes
        `CONTROL_REDECODE` weaker than `CONTROL_RAW`. **Eleven installs on
        this box are in that state** (6907, 6968, 7009, 7065, 7083, 7110,
        7135, 7170, 7182, 7189, 7205) and only one of them is stamped 6907.

        So the bid is made on THREE positive facts together, and it is
        measured rather than assumed:

          * the compiled tables are present -- this is the 6090 art family,
            which is what stops the bid reaching 7632/7682/7867/7878, where
            there is no `.dbc` and `plaintext`/`patch7878` are right;
          * `version.dat` is a number above the last build measured to open
            with the TQ cipher (6868);
          * **and `ini/itemtype.dat` does NOT open under the TQ cipher** --
            the format observation, taken on a 4 KB prefix so it costs
            nothing. A stream cipher decrypts a prefix correctly, so this is
            the real test and not a sample of one.

        Whether the 6907 profile actually READS those ten siblings was
        measured rather than argued -- 199-201 readable subjects each, 59-60
        of them `.dat`, `item` 4,968-7,007 rows and `mount` 1,742-1,819. The
        row counts differ per build, which is the point: they are that
        client's numbers, not 6907's repeated.
        """
        root = Path(root)
        ver = _version(root)
        if ver == self.STAMP:
            return 0.95
        if not (exists("ini/3DSimpleObj.dbc") and exists("ini/3DObj.dbc")):
            return 0.0
        if not (ver.isdigit() and int(ver) > self.LAST_TQ_BUILD):
            return 0.0
        return (self.SIBLING_CONFIDENCE
                if self._dat_is_not_tq(root) else 0.0)

    @staticmethod
    def _dat_is_not_tq(root: Path) -> bool:
        """Does `ini/itemtype.dat` refuse the OLD cipher?

        The positive half of the era test. `tqdat.decrypt` is a stream cipher
        keyed from offset 0, so decrypting the first 4 KB gives exactly the
        first 4 KB of the answer -- 98%+ printable on 6868 and below, ~40% on
        6907 and above. A whole-file read is not needed and would put a 10 MB
        read into every `plugins.detect()` call.
        """
        try:
            from core import tqdat                        # noqa: PLC0415
        except ImportError:                               # pragma: no cover
            import tqdat                                  # noqa: PLC0415
        p = root / "ini" / "itemtype.dat"
        try:
            with open(p, "rb") as fh:
                head = fh.read(4096)
        except OSError:
            return False
        if not head:
            return False
        return not tqdat.looks_like_text(tqdat.decrypt(head))

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """Curated 6907 specs, then 6609's censused `.ini` FILTERED TO DISK.

        **This build has no census of its own and borrowing one is a real
        risk**, so the choice was measured rather than argued. Each candidate
        donor's censused specs were run against 6907's `ini/` with
        `build_catalogs`, counting how many read with a positive control:

            donor       declared   present on 6907   read WITH A CONTROL
            patch6090      125            123               121
            patch6609      141            139               139
            patch7878      154            138               138

        6609 wins on both counts, which is what the lineage predicts -- 6907's
        compiled tables *are* 6609's -- and the 139/139 is the part that makes
        it a measurement: every borrowed spec that survives the filter opens
        with a row checked back against bytes the parse did not produce. A
        spec whose grammar had drifted would refuse, loudly, rather than
        report a wrong count.

        **The filter is presence on disk**, applied here rather than left to
        `_find_table`, because "declared and absent" and "declared and
        unreadable" are different findings and only the second is interesting.
        Two of 6609's 141 are simply not shipped here.

        The gap is declared rather than hidden: 6907 ships **207** `.ini` in
        `ini/` and this list reaches 139 of them. The remaining ~68 are
        invisible until someone runs `tools/ini_census.py` on this build and
        regenerates `censused.py` -- which is now possible: the generator was
        repaired on 2026-09-07 and `--write` reproduces its own output
        byte-for-byte (`tests/test_gen_ini_specs.py`). Adding these ten builds
        to its `BUILDS` list is the next step and is NOT taken here, because
        each would need its own donor measurement rather than 6609's.

        **`SPECS_DAT_CENSUSED` IS FILTERED THE SAME WAY, AND IT HAS TO BE.**
        Each of those 31 tables is on a DIFFERENT subset of the ten builds --
        `syn_skill_type` on three, `exchange_shop_lev` on nine, `combat_gear`
        on one -- so an unfiltered list would put a "not present" refusal in
        front of every build that does not ship a given file. Absence should
        read as absence.

        **A 0-BYTE FILE IS RE-DECLARED WITH THE `empty` CODEC, not dropped.**
        7009 ships `instance_prize.dat` and `instance_star_condition.dat` at
        zero bytes while the other seven builds carry 480-794 rows, so one
        codec cannot be right for all eight -- and
        `test_the_declared_codec_is_what_inidat_classifies` enforces exactly
        that, per build. Dropping the spec on 7009 would make the subject
        vanish there, which reads as "this client has no such table" when the
        truth is "this client ships it empty". The `ItemtypeSub.dat` entry in
        `SPECS_DAT` is the same decision made by hand for a file that is
        0 bytes on all ten; this is the general rule behind it.
        """
        ini = Path(root) / "ini"
        have, sizes = set(), {}
        if ini.is_dir():
            for p in ini.iterdir():
                if p.is_file():
                    have.add(p.name.lower())
                    sizes[p.name.lower()] = p.stat().st_size
        curated = SPECS_DAT + SPECS_UNSETTLED + SPECS_BINARY + SPECS_NPC
        censused_here = tuple(
            self._present(s, sizes) for s in SPECS_DAT_CENSUSED
            if s.filename.lower() in have)
        here = curated + censused_here
        taken = {s.subject for s in here}
        files = {s.filename.lower() for s in here}
        borrowed = tuple(
            s for s in censused.specs_for(CENSUS_DONOR)
            if s.filename.lower() in have
            and s.subject not in taken and s.filename.lower() not in files)
        return here + borrowed

    @staticmethod
    def _present(spec, sizes):
        """The spec as it applies to THIS build: `empty` where the file is."""
        if sizes.get(spec.filename.lower(), 1) != 0:
            return spec
        return replace(
            spec, codec=CODEC_EMPTY,
            refusal=(f"this build ships {spec.filename} as a 0-byte file. "
                     f"Other builds this plugin serves carry it with content, "
                     f"so its absence here is the client's and not the "
                     f"reader's."))

    def load_table(self, spec, root) -> tuple:
        """`(text, witness, control_kind, refusal)`, adding the block96 codec.

        Everything outside the block96 family -- the plaintext `.ini`, the two
        binary tables -- goes to the base class untouched. **No `tq-stream`
        spec reaches this build**, and that is deliberate: `tqdat.decrypt`
        does not raise on a block96 file, it returns noise that parses, so a
        stray inherited spec would report a table of garbage rather than an
        error. `tests/test_patch6907.py` asserts the spec list contains none.

        The control kind is `CONTROL_REDECODE`, the same word the TQ tables
        get and for the same reason: the witness is a second decode taken
        from disk, so it witnesses the PARSE and not the CIPHER. It cannot
        testify that the dictionary was right -- two runs of the same lookup
        agree whatever the lookup says -- and calling it a raw control would
        overstate exactly that.
        """
        if spec.codec not in (CODEC, CODEC_REFUTED):
            return super().load_table(spec, root)

        path = self._find_table(spec, root)
        if path is None:
            return None, b"", None, f"{spec.display} is not present"
        d = block96.load_dictionary(root)
        if d is None:
            return None, b"", None, block96.why_no_dictionary(root)

        kind = "sections" if spec.kind == KIND_SECTIONS else "rows"
        # **`or "@@"` catches `KIND_SPACE_ROWS`, whose delimiter is None, and
        # that fallback FAILS CLOSED rather than wrong.** `recover_rows` only
        # consults the delimiter on a line that carries a damage marker, to
        # keep the fields before it; a whitespace-separated line split on
        # `@@` yields one field, which is under `min_fields=2`, so the
        # damaged line is DROPPED instead of being served short. Undamaged
        # lines never reach that branch and come back byte-for-byte.
        #
        # It is not hypothetical and it is not currently reachable either:
        # `Achievement.dat` is the only space-rows table here and it decodes
        # with 0 unknown blocks on all ten installs, so no line has ever
        # taken this path. Written down because "it under-reports" and "it
        # mis-columns" are very different failures for a table a user can go
        # on to edit, and this one does the first.
        delim = ROW_KINDS.get(spec.kind, "@@") or "@@"
        table = block96.read_table(path, d, kind,
                                   delim.encode("latin-1"))
        if not table.ok:
            return None, table.witness, None, table.refusal
        return (table.text.decode(self.spec_encoding(spec), "replace"),
                table.witness, CONTROL_REDECODE, None)

    # -- what was recovered, and what was not ------------------------------
    def recovery(self, root) -> dict:
        """`{subject: Recovery}` for every block96 table that opened.

        **The catalog's `rows` is a served count, not a table size**, and on
        this build the two are far apart -- 5,387 against at least 22,265 for
        `item`, where "at least" is load-bearing (`Recovery.lines`).
        `Catalog` has no field for a shortfall, so it travels here instead of
        being dropped, and `catalogs()` folds the one-line summary into each
        control string so a reader of `comod catalogs` sees both numbers
        without having to know this method exists.
        """
        root = Path(root)
        d = block96.load_dictionary(root)
        out: dict = {}
        if d is None:
            return out
        for spec in self.table_specs(root):
            if spec.refusal or spec.codec not in (CODEC, CODEC_REFUTED):
                continue
            path = self._find_table(spec, root)
            if path is None:
                continue
            kind = "sections" if spec.kind == KIND_SECTIONS else "rows"
            delim = (ROW_KINDS.get(spec.kind, "@@") or "@@").encode("latin-1")
            table = block96.read_table(path, d, kind, delim)
            if table.recovery is not None:
                out[spec.subject] = table.recovery
        return out

    def catalogs(self, root) -> dict:
        """The base class's catalogs, with the shortfall attached to each
        block96 control.

        Without this, `item` reads `5,387 rows` beside `npc.ini`'s `2,325
        sections` and nothing distinguishes a complete table from a quarter of
        one. The summary is appended to the CONTROL rather than replacing the
        count, because the count is real -- those 5,387 rows were opened and
        one of them was checked back against a re-decode -- it is just not the
        whole table.
        """
        from plugins.catalog import Catalog                # noqa: PLC0415
        cats = super().catalogs(root)
        for subject, rec in self.recovery(root).items():
            c = cats.get(subject)
            if c is None or not c.ok:
                continue
            cats[subject] = Catalog(
                c.subject, c.source, c.kind, rows=c.rows,
                control=f"{c.control}; {rec.summary()}",
                control_kind=c.control_kind, duplicated=c.duplicated)
        return cats

    # -- what is inherited, and what is not --------------------------------
    def colour_provenance(self):
        """Inherited from the 6090 scan and **not checked here**, exactly as
        on 6609 -- with one difference in 6907's favour that is still not
        evidence: 6609 adds 23,811 loose `c3/` files over 6090 and this build's
        archives are byte-identical to both, so the inherited sets have no
        *new* room to be wrong in the archives. They have all the old room, and
        nobody has looked at 6907 on screen."""
        return "colourway inherited from 6090 (unverified here)", "inferred"

    def table_quirks(self):
        """6090's quirks about the ART all still apply -- the files are
        6609's, which are 6090's -- so they are inherited. These are the ones
        that are 6907's own, and every one of them is about the `.dat` layer."""
        q = super().table_quirks()
        q["the .dat cipher changes at 6868 -> 6907, and nothing says so"] = (
            "6868 and earlier open with core/tqdat.py at seed 9527. 6907 and "
            "later are block96, a 12-byte ECB cipher whose key is unknown. "
            "The 7878 block dictionary covers 0.0% of 6772's, 6805's and "
            "6868's ini/*.dat and 86.4% of 6907's -- no shared blocks at all "
            "on one side of the line and most of them on the other. tqdat "
            "must not be pointed at these files: it will not raise, it will "
            "return noise that parses.")
        q["block coverage is not row coverage, and the gap is 3.5x"] = (
            "itemtype.dat: 86.41% of BLOCKS known, at most 24.2% of ROWS "
            "recovered (5,387 of AT LEAST 22,265 -- a marker that eats a CRLF "
            "merges two rows into one line, so the denominator is a floor and "
            "the percentage a ceiling; 6868 one patch earlier carries 33,639 "
            "rows). One unknown 12-byte block damages a whole ~500-byte row, "
            "so a 13.6% per-block miss lands on three quarters of them. "
            "Reading datdict's coverage percentage as readability is the "
            "largest single error available on this client, and another seat "
            "measured the same gap independently on 7205 (86.1% blocks, 5.2% "
            "whole rows).")
        q["Monster.dat is refused, not empty"] = (
            "84.6% of its blocks are in the dictionary and ONE of its ~2,100 "
            "sections survives. Section headers are short unique strings the "
            "dictionary does not hold, and a marker that swallows the CRLF "
            "before a header deletes the section outright -- so the keys that "
            "follow would attach to the PREVIOUS monster. Dropping damaged "
            "lines and parsing the rest yields several hundred confidently "
            "mis-attributed monsters; core/block96.py drops the attribution "
            "instead and the subject refuses with the numbers.")
        q["four tables decode at 100% coverage into ciphertext"] = (
            "UserHelpInfo.ini.dat, RaceTrackProp.dat, ShowHandTableRace.dat "
            "and WeaponActionData.dat are byte-identical to 7878 files whose "
            "ndac.dll .out was never a valid decrypt (patch7878.NOT_CONTENT "
            "names fourteen; the build that produced the shipped dictionary "
            "excluded three names, only two of them from that set, so twelve "
            "went in), so their ciphertext blocks are in the dictionary "
            "mapped to ciphertext. "
            "Coverage reads 100% and entropy reads 7.26-8.00 bits/byte. Two "
            "independent guards refuse them -- inidat's family and an entropy "
            "check -- and both are needed, because inidat is itself wrong "
            "about MapDestination.dat, which it calls "
            "block96-candidate-refuted and which opens into 170 sections.")
        q["thirteen .dat tables have no undamaged line at all"] = (
            "Achievement, award_config, the three battlepass_*, "
            "coat_storage_attr, exchange_shop_goods_ex, levexp, "
            "nosuch_config, rune_storage_attr, ServerPlay, texas_match_prize "
            "and texas_match_type. Their grammar is therefore not established "
            "on this build and they are declared with a refusal rather than a "
            "guessed kind -- SPECS_UNSETTLED.")
        q["six .dat tables ship as 0 bytes"] = (
            "ItemtypeSub.dat, monster_live.dat, new_battle_buff.dat, "
            "Promotion_Activity.dat, Promotion_Goods.dat and slient.dat are "
            "0-byte files in a shipped client. ItemtypeSub is declared "
            "because 6090, 6609 and 7878 all carry it with content and its "
            "absence here is a fact about 6907; the other five are not "
            "declared at all.")
        q["ten unstamped siblings were in the same wrong state"] = (
            "6968, 7009, 7065, 7083, 7110, 7135, 7170, 7182, 7189 and 7205 "
            "all ship the twelve .dbc and block96 .dat, and all were claimed "
            "by patch6090 at 0.45 -- whose mount subject reported 1 row with "
            "a PASSING re-decode control over garbage. This plugin bids 0.55 "
            "for them on three positive facts (compiled tables present, stamp "
            "above 6868, itemtype.dat refuses the TQ cipher) and was measured "
            "to read them: 199-201 readable subjects each, item 4,968-7,007 "
            "rows, mount 1,742-1,819. The counts differ per build, which is "
            "what says the profile is reading each client rather than "
            "repeating 6907's. Their own numbers have NOT been curated: the "
            "spec list, the item column index and the label keys are 6907's, "
            "and a spec whose grammar drifted refuses rather than reporting a "
            "wrong count.")
        q["npc.ini went backwards"] = (
            "2,325 sections here against 6609's 2,784 and 7878's 4,087, and "
            "the file is not 6609's (sha256 0d0ba609 against af0b735a). A "
            "later patch shipping 459 fewer npc rows is odd enough that a "
            "count taken here should be confirmed before it is trusted -- the "
            "same shape as 6609's mount.dbc losing 428 rows to 6090.")
        q["ini/codepage.ini says 1256 and was not re-measured"] = (
            "6907 ships codepage.ini = 1256 (Windows-1256, Arabic), the same "
            "value 7878 ships and where the CONTENT was measured to be GBK. "
            "Nobody has repeated that measurement here, so this plugin keeps "
            "latin1: it round-trips every byte and so cannot break a control, "
            "but it will not display this build's Chinese tables correctly. "
            "AutoUseMagic.dat is the visible case.")
        return q


PLUGIN = Patch6907()
