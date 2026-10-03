#!/usr/bin/env python3
r"""
patch7205 -- official Conquer Online patch 7205: 6609's client wrapped around
7878's cipher, and the end state of the transition era.

7205 is the build where the two halves of this project's client knowledge stop
lining up. **Its asset layout is 6609's** -- WDF archives, twelve compiled
`.dbc` tables, `version.dat` stamped `7205` -- so `plugins.detect()` hands it
to `patch6090`, which is right about the archives and the art and **wrong
about every content table it declares**. **Its `ini/*.dat` are 7878's** -- the
96-bit ECB block cipher that arrived at 6907 -- so the specs `patch6090`
inherits, all of them `tq-stream`, point `core/tqdat.py` at a cipher it does
not read.

WHAT THAT COST, MEASURED BEFORE THIS FILE EXISTED
--------------------------------------------------
    py -3 -c "import sys;sys.path[:0]=['.','core','tools'];\
              from plugins.patch6090 import Patch6090;\
              print(Patch6090().catalogs(r'<Clients>/7205')['item'])"

        item   rows=394269   ok=False

**394,269 rows.** `itemtype.dat` carries about 39,771. That number is what a
TQ decryption of block-cipher bytes splits into -- noise counted as records --
and it is published on the `Catalog` because only the *control* refused, not
the count. Two subjects did worse and one differently:

    item        394,269 rows, no control   (real floor: 39,771)
    magic        17,620 rows, no control   (real floor:  3,602)
    item:sub      6,241 rows, no control   (real floor:    506)
    magic:op      4,549 rows, no control   (real:            77)
    monster           0 rows, no control   -- reported as an EMPTY TABLE
    map:dest          0 rows, no control   (real:           266)
    mount             1 row,  **ok=True**  (real: 1,819 sections)

**Read that last line again.** `mount` came back *readable*, one row, with a
positive control attached, from `ini/mounttype.dat` -- the right file, decoded
under the wrong cipher. The control it carried was::

    '[x\x16z\xd8\xdaLo\xc2\xa0... -- both found in an independent re-decode'

A section header of binary noise, endorsed. That is not the control failing;
it is the control **working exactly as documented and being the wrong kind**.
`CONTROL_REDECODE` witnesses the PARSE: it decodes the file a second time and
looks for the parse's output in the second decode. A wrong cipher applied
twice agrees with itself perfectly. **This plugin's block96 subjects report the
same control kind and inherit the same limit**, which is precisely why the
cipher-level evidence below comes from a different build entirely and not from
a second decode.

and `core/coassets.load_items()` returned **0 items** -- not for 7205 alone
but for **every official client from 6907 to 7867**, fourteen installs, because
`tqdat` raises on them and the caller turned that into `[]`. That is the gap
this plugin and `core/block96.py` close together.

WHAT IS ACTUALLY READABLE HERE, AND THE NUMBER THAT IS NOT THE COVERAGE
-----------------------------------------------------------------------
    d = block96.load_dictionary(<Clients>/7205)
    block96.read_table(<Clients>/7205/ini/itemtype.dat, d, "rows").recovery

**RE-MEASURED 2026-09-07, AND THIS SECTION'S ORIGINAL SUBJECT IS GONE.**
Under the dictionary these figures were first taken against (3,390,890
blocks) 7205's `itemtype.dat` read 86.1% of blocks and 2,068 whole rows out
of a ~39,771-row table -- a sixteen-fold overstatement, and the reason this
plugin exists. Under the current 4,059,815-block dictionary that same file
reads **100% of blocks and all 44,684 rows**, and so does every other table
below:
**THE NUMBERS BELOW ARE THE 2026-08-30 MEASUREMENT AND THEY ARE NO LONGER
THIS INSTALL'S. They are kept because the ARGUMENT is what this section is
for, and it is easiest to make on the build where the gap was 16-fold.**

The block dictionary built from 7878's decrypted corpus
(`tools/datdict.py build`, then 3,433,354 blocks) knew **86.1%** of this
file's 1,080,527 ciphertext blocks. It did **not** know 86.1% of the table:

    file                blocks   known    lines   intact   units   shape
    itemtype.dat     1,080,527  100.0%   44,684   44,684  44,684   rows
    monster.dat        103,113  100.0%   96,045   96,045   3,709   sections
    MagicType.dat       47,730  100.0%    2,907    2,907   2,907   rows
    ItemtypeSub.dat     17,319  100.0%      633      633     633   rows
    mounttype.dat       36,719  100.0%   25,583   25,583   1,819   sections

**THE ARGUMENT IS STILL TRUE AND ITS SUBJECT IS SOMEWHERE ELSE.** A record
is only served if *every* block it spans is known, so block coverage remains
a ceiling on record coverage and the gap remains enormous wherever coverage
is partial. 7205 no longer has a partial table -- 99 of its 101 block96
`.dat` are complete and the other 2 are at zero -- so
`tests/test_patch7205.CoverageArithmetic` measures the claim on 7867's
`ini/ft/itemtype.dat` instead: **87.1% of 1,399,730 blocks against 16.6% of
whole rows, a 5.25x overstatement.** Re-pinning that class to 1.0/1.0 on
this install would have left a gap-detector that cannot detect a gap.
A record is only served if **every** block it spans is known. An `itemtype`
row is ~324 bytes = ~27 blocks, so at 86.1% with independent damage
``0.861 ** 27 = 1.8%`` of rows survive; measured, 5.2% did, so damage was
somewhat clustered and the conclusion held. **Anyone reading "86.1%
coverage" as "86.1% of the table" is out by a factor of sixteen**, and this
plugin's job is to make sure nobody has to.

The columns above are `core/block96.Recovery` fields and nothing else, which
is deliberate. This section used to cite `py -3 core/block96.py coverage
<path>` and a `floor` column, and **neither exists**: there is no `__main__`
in that module and no module-level `coverage()` anywhere in this
repository's history. `lines` is NOT the old `floor` -- it under-counts rows,
because a marker that eats a `\r\n` merges two rows into one line, and for a
section table it counts lines and not sections (25,583 against 1,819 above).
`tests/test_patch7205.TheFloorThatRecoveryCannotExpress` states what that
costs rather than papering over it.

RE-MEASURED 2026-09-07 against a 4,059,815-block dictionary, **all five
close**: 1.000 coverage, `whole == floor`, `itemtype` at 44,684 rows. The
whole 101-file block96 surface on this install is 99.95% of blocks known,
with ZERO partially-covered files -- see `BLOCK96_COVERAGE` and
`BLOCK96_CENSUS`, both of which carry the before and after. The floor did
its job on the way past: it promised "at least 39,771" and the answer was
44,684.

**The gap argument is not retired, it has no subject HERE any more.** It is
about the block96 reader, not about 7205's files, and it is still
demonstrable on `7009/monster.dat` (93.93% of blocks, 32.02% of lines,
ratio 2.93) -- which is where `CoverageArithmetic` now measures it.

THE POSITIVE CONTROL FOR THE CIPHER, WHICH THE READER CANNOT PROVIDE
---------------------------------------------------------------------
A second decode through the same dictionary witnesses the PARSE, not the
CIPHER -- it asks one table twice. So the catalogs report
`CONTROL_REDECODE` and never `CONTROL_RAW`, and the evidence that these
plaintexts are *right* comes from outside the dictionary entirely:

**6868 is the last TQ-stream client and ships `itemtype.dat` with the same 65
fields per row.** `core/tqdat.py` opens it under a cipher this project
independently verified (``encrypt(decrypt(x)) == x``, seed 9527), and its ids
also appear among 7205's whole rows. RE-MEASURED 2026-09-07:

                                    was              now
    shared ids                    1,288           33,638
    name column agrees      1,288 /  1,288   33,594 / 33,638
    columns agreeing on every row  51 / 65       40 / 65
    worst column (59, unnamed)    113 /  1,288    2,334 / 33,638
independently verified (``encrypt(decrypt(x)) == x``, seed 9527), and
**33,638** of its ids also appear among 7205's 44,684 whole rows (it was
1,288 of 2,068 when this was written, before the dictionary closed the file):

    name column                   33,594 / 33,638 agree
    columns agreeing on every row     40 / 65

    py -3 -m unittest tests.test_patch7205.CipherAgreesWith6868

**BOTH HEADLINE NUMBERS FELL AND THE EVIDENCE GOT STRONGER. Read them with
the denominator or they say the opposite of what happened.** The overlap went
from a 1,288-id corner of the table (3.8% of 6868's 33,639 rows) to
essentially all of it (99.997%), because 7205's whole-row count went from
2,068 to 44,684. A column now has to agree on 26 times as many rows to
count at all, so 40 surviving that is a stronger statement than 51 surviving
the old sample. The negative control moved with it: a one-column shift
collapses 40 to 8 (+1) and 3 (-1).

All 44 name disagreements were read. Every one is a real rename across the
337 patch versions between the builds -- `ChristmasCostume`/
`ChristmasGarment`, `P1SomersaultSpirit`/`P1CloudSpirit`, `EliteGemBag`/
`RefinedGemBag` -- and both sides of all 44 are printable ASCII. That
printability is asserted in the test as the discriminator between "the
content changed" and "the decode is wrong", because a wrong decryption does
not produce two plausible English item names differing by a marketing edit.
`ITEM_COLUMN_AGREEMENT` records the figures.

*Two of the disagreeing columns are formatting, not data: 6868 writes `01`
where 7205 writes `1`. The measurement compares numerically where both sides
are numeric, having first been misled by the string form.*
40 of 65 columns identical on **every one of 33,638 shared ids** is stronger
evidence than the 51 of 65 over 1,288 that it replaced, not weaker: a column
is disqualified by a single disagreement anywhere, so widening the sample
26-fold can only lower the count. `ITEM_COLUMN_AGREEMENT` records it and
`tests/test_patch7205.py` re-measures it, with a negative control that shifts
the column index and watches the agreement collapse.

The 44 name disagreements were inspected rather than assumed and every one is
TQ renaming an item between the builds -- `ChristmasCostume` ->
`ChristmasGarment`, `P1..P9SomersaultSpirit` -> `P1..P9CloudSpirit`,
`EliteGemBag` -> `RefinedGemBag`. A wrong decryption does not produce spelled
English on both sides of the comparison.

*Some "disagreements" are formatting, not data: 6868 writes `01` where 7205
writes `1`. The measurement above compares numerically where both sides are
numeric, having first been misled by the string form.*

THE COMPILED TABLES FROZE AT 6609 AND THE CONTENT DID NOT
-----------------------------------------------------------
All **twelve** `.dbc` are byte-identical (sha256) to 6609's:

    sha256sum <Clients>/{6609,7205}/ini/*.dbc

    armet  9415fee5e4b7    3DSimpleObj  7159b91c0eb7    3DEffect     29e5ab3aed58
    armor  d50f726935fa    3DObj        041b1060bd18    3DEffectobj  063b4121696c
    weapon a07bae8d701d    3DTexture    0882236874e5    mountmotion  a2509814d75b
    mount  ebc89e4ece32    3dmotion     565a3c34e4bd    weaponmotion 5791de723722

`npc.ini` did not freeze -- 2,784 sections at 6609, **2,913** here -- so the
frozen twin loses ground, and the loss is silent in the way
`plugins/patch7878.py` documents for its own frozen lookups:

    py -3 -c "..."      # reproduced by tests/test_patch7205.py::FrozenTables

    build   npc.ini rows   3DSimpleObj.dbc resolves   .ini decoy resolves
    6090          2,277        2,195  (96.4%)            1,757  (77.2%)
    6609          2,784        2,672  (96.0%)            1,950  (70.0%)
    7205          2,913        2,466  (84.7%)            1,600  (54.9%)

**447 of 7205's npc rows (15.3%) name a simple object the compiled table has
never heard of**, against 112 (4.0%) at 6609 -- and in absolute terms 7205
resolves *fewer* rows than 6609 does from the identical table, so this is not
only new rows arriving: existing rows were re-pointed at ids the 2017 table
does not carry. Nothing raises for any of them; an unresolved row looks exactly
like an npc with no art. This plugin still prefers the compiled table (it is
2017 data against the `.ini`'s 2008 data, and it wins by 30 points) but the
number is declared rather than discovered later.

THE SCRIPT SURFACE IS THIS BUILD'S REAL NEW CONTENT
-----------------------------------------------------
    py -3 tools/clientscripts.py --clients "<Clients>" --lo 6609 --hi 7205

    6609     5 Lua      26 DLLs
    7205   224 Lua      37 DLLs,  19,094,816 Lua bytes,  1,106 exported globals

**It is declared here as a surface, not as tables.** `SCRIPT_SURFACE` records
the counts and `script_surface()` re-measures them from the install; no
`TableSpec` is offered for a `.lua` file, because this project has no Lua
grammar and a spec is a promise to parse. `tools/clientscripts.py` and
`tools/scriptreport.py` are the readers; naming them here is the whole of what
a plugin can honestly say about 224 files it cannot open.

WHAT THIS PLUGIN DOES NOT CLAIM
-------------------------------
* **No colour or name data of its own.** Inherited from the 6090 scan through
  6609, which already declares itself unverified -- and 7205 is one more patch
  away from the afternoon someone spent looking. `colour_provenance` says so.
* **No `ini/` grammar census of its own.** `plugins/catalog/censused.py` holds
  a measured label key per `.ini` for eight plugins and none for this one.
  7205's 208 `.ini` are the same lineage as 6609's, so 6609's census is
  inherited HERE -- 139 of its 141 specs name a file this build ships --
  filtered to those, and declared as inherited rather than measured. See
  `table_specs`.

  **THE GUARD THIS USED TO CITE DOES NOT COVER THIS BUILD, 2026-09-07.** The
  sentence here read "`tests/test_plugin_catalogs.py`'s blank-label guard,
  which now runs over 7205, is what keeps that from being a free assumption".
  It does not: `NoSubjectPrintsAColumnOfBlanks.ALL` is `list(BUILDS) + [cco,
  zephyr]`, and `BUILDS` is 5017/5065/5165/5517/6090/6609/6907/7878 -- **7205
  is not in it**, so the guard has never seen an inherited label key on this
  install. Reproduce with `python -c "import tests.test_plugin_catalogs as
  m; print(m.NoSubjectPrintsAColumnOfBlanks.ALL)"`. The claim is corrected
  here rather than in that file, which this pass does not hold; the
  equivalent guard for 7205 lives in `tests/test_patch7205.py::
  NoSubjectPrintsAColumnOfBlanksOn7205` and covers the 85 declared below and
  the inherited `.ini` alike. Adding 7205 to `BUILDS` would put it under
  ~15 other classes at once and is left as a separate, deliberate change.
* **No `ini/*.dat` census of its own -- SUPERSEDED 2026-09-07.** 7205 now has
  one: 129 undeclared `.dat` measured on this install, 85 declared in
  `SPECS_7205_CENSUSED` (the census's 84 plus `Achievement.dat`, promoted
  past a `partial` verdict on a direct column read), 13 refused by name in
  `UNDECLARED_DAT`, 31 identified as a non-table family in
  `UNOPENED_DAT_FAMILIES`.
* **No claim about the `.dat` the dictionary cannot touch at all.** It is TWO
  here (`levexp.dat`, `ServerPlay.dat`) and NONE are partly covered --
  `BLOCK96_CENSUS` carries the measurement and `block96_census()` re-counts
  it off the install. *This line said "the 4 tables ... or the 37 it only
  partly covers" until 2026-09-07; both numbers were the 3,390,890-block
  dictionary's and `BLOCK96_CENSUS`/`FORMERLY_UNCOVERED` had already been
  re-measured to 2 and 0 while this sentence was not.*
* **Nothing about the algorithm.** The cipher is still unrecovered; the
  dictionary is a lookup. `tools/datdict.py` has where that stands.

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
RESULT on 7205: **0 strict-GBK failures** in 359 subjects/name
sources (2 refused, not read); 17 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:gamemapex                 287     2     2     0      0     0    0
    Name=:monster                  3709    66    65     0      1     0    0
    Name=:npc:NpcX.ini              826     4     4     0      0     0    0
    Name=:npc:npc.ini              2913    40    20    20      0     0    0
    Name=:npc:terrainnpc.ini        167    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                164     3     3     0      0     0    0
    achievement                     391     2     0     0      0     2    0
    classdesc                       116    25    25     0      0     0    0
    gamemapex                       287     2     2     0      0     0    0
    item                          44682     1     0     1      0     0    0
    item:sub                        633     1     0     0      0     1    0
    map:dest                        936     7     6     0      0     1    0
    npc:NpcX.ini                    819     4     4     0      0     0    0
    npc:npc.ini                    2884    20    20     0      0     0    0
    npc:terrainnpc.ini              165    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      164     3     3     0      0     0    0
    strres                         1189    20    20     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 219: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 747: `a1 a1` = '\u3000'
    Name=:monster 3227: `54 68 a8 a6 6f 64 72 65 64` = 'Théodred'

`other` is GBK row-A1 general punctuation (U+2019 `a1 af`, U+2026
`a1 ad`) inside English text, e.g. "Texas Hold’em" -- strict GBK and
only sensible as GBK (latin1 reads `¡¯`), so not a failure.
Name fields only. A WHOLE-TABLE strict decode found files that are not
GBK; they carry a per-table codec through `TABLE_ENCODING` (see the class)
and `TableSpec.encoding`, added to the base the same day.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch6609 import Patch6609               # noqa: E402
from plugins.catalog import (                         # noqa: E402
    npc_specs, TableSpec, KIND_AT_ROWS, KIND_CSV_ROWS, KIND_SECTIONS,
    KIND_GAMEMAP, KIND_SPACE_ROWS)
from plugins.catalog import censused                  # noqa: E402


#: 7205's OWN census, and every codec below was taken from
#: `core/inidat.classify` run on THIS install rather than inherited:
#:
#:     py -3 -c "import sys;sys.path.insert(0,'core');import inidat;\
#:               print(inidat.census(r'<Clients>/7205/ini'))"
#:
#:     block96 101 · rar-mangled 11 · rsa-mysqldump 10 · plaintext 7 ·
#:     binary-plain 6 · empty 6 · tq-stream 3 · unknown 2 · shift-obfuscated 1
#:
#: **Every content table 6609 declares as `tq-stream` is `block96` here**, and
#: that is the single edit that separates this tuple from `SPECS_6609`. The
#: shapes (`KIND_*`) are 6609's and are unchanged -- the grammar inside the
#: cipher did not move, which is what makes `tqdat.parse_itemtype` the right
#: parser for a file `tqdat` cannot decrypt.
#:
#: Two filenames differ from 6609's and match what is on disk here:
#: `monster.dat` stays lowercase (6609's rename survived) but `MagicType.dat`
#: is back to 6090's capitalisation. `_find_table` matches case-insensitively
#: either way; the spellings are the disk's so a listing shows the real name.
SPECS_7205 = (
    TableSpec("monster", "monster.dat", KIND_SECTIONS, "block96"),
    TableSpec("mount", "mounttype.dat", KIND_SECTIONS, "block96"),
    TableSpec("item", "itemtype.dat", KIND_AT_ROWS, "block96"),
    TableSpec("item:sub", "ItemtypeSub.dat", KIND_AT_ROWS, "block96"),
    TableSpec("item:value", "item_value_type.dat", KIND_AT_ROWS, "block96"),
    # **The NAME is column 3, MEASURED, and the plugin-wide `ITEM_COLUMNS`
    # puts it on column 1.** Column 1 is a numeric group id, so `browse magic`
    # listed a column of numbers where the skill names are -- the same defect
    # `MEASURED_SECTION_LABELS` exists to prevent, one grammar over. Column 3
    # is the ONLY column of the 48-53 that is non-numeric on every row
    # (`Thunder`, `Fire`, `Tornado`), which is what makes this a measurement
    # rather than a preference. The 5165 file has one fewer leading id and
    # puts the same field at column 2; see `patch5165`.
    TableSpec("magic", "MagicType.dat", KIND_AT_ROWS, "block96",
              columns={"id": 0, "name": 3}),
    TableSpec("magic:ex", "magictypeex.dat", KIND_AT_ROWS, "block96"),
    TableSpec("magic:op", "magictypeop.dat", KIND_CSV_ROWS, "block96"),
    TableSpec("magic:auto", "AutoUseMagic.dat", KIND_SECTIONS, "block96"),
    TableSpec("map:dest", "MapDestination.dat", KIND_SECTIONS, "block96"),
    TableSpec("item:refine", "item_refine_attr.dat", KIND_AT_ROWS, "block96"),
    TableSpec("item:refine:cost", "item_refine_cost.dat", KIND_AT_ROWS,
              "block96"),
    TableSpec("item:refine:effect", "item_refine_effect.dat", KIND_AT_ROWS,
              "block96"),
    TableSpec("item:refine:effect:ex", "item_refine_effect_ex.dat",
              KIND_AT_ROWS, "block96"),
    TableSpec("item:refine:upgrade", "item_refine_upgrade.dat", KIND_AT_ROWS,
              "block96"),
    # **NAMED, MEASURED, AND NOT DECLARED.** An undeclared subject is
    # not merely unread, it is INVISIBLE -- the tool's silence about
    # levexp.dat read exactly like its silence about a file that is not
    # there. The refusal carries the measurement instead.
    # `block96`, which is what `core/inidat.py` classifies this build's
    # copy as -- and `DeclaredCodecs.test_no_spec_declares_tq_stream`
    # forbids the other answer outright, because `tqdat` on a block96
    # file returns noise that parses.
    TableSpec("levexp", "levexp.dat", KIND_SPACE_ROWS,
              "block96", label_key=None, refusal=(
        "levexp.dat is named and NOT readable on this build, and the reason "
        "is not the one the tq-stream builds have. Its 4,030 bytes are "
        "byte-identical to 7878's (sha256 4482cfaa) and are NOT the file "
        "5517-6868 ship (df41c576) -- same length, different ciphertext -- "
        "so core/tqdat at seed 1234, which opens the older copy into 203 "
        "rows of five numeric fields, returns 38.7% printable noise here. "
        "The block96 dictionary holds 0 of its 335 blocks and that is "
        "DELIBERATE: it is the corpus's one seed-1234 table, with no "
        "shared-block edge to attribute its key group, so tools/datdict.py "
        "holds it out. Its plaintext exists on a box with the derived corpus "
        "(derived/7878-dat-decrypted/levexp.dat.out) and is NOT wired in: "
        "this reader would be declaring a grammar for bytes it never opened. "
        "See UNCOVERED_TABLES.")),
    TableSpec("action", "Action.dat", KIND_SECTIONS, "binary-plain"),
    TableSpec("gamemap", "GameMap.dat", KIND_GAMEMAP, "binary-plain"),
) + npc_specs(("npc.ini", "NpcX.ini", "terrainnpc.ini", "npcex.ini",
               "SlotNpc.ini"))


#: **`columns={"id": 0, "name": None}` is a MEASUREMENT, not an omission**, and
#: it is the single most important line in the block below: 63 of the 71 `@@`
#: row tables declared here carry it.
#:
#: `Plugin.ITEM_COLUMNS` is `{"id": 0, "name": 1}` -- an `itemtype.dat` fact --
#: and `Plugin.browse` reads `cols = spec.columns or self.ITEM_COLUMNS`, so a
#: spec that says nothing about its columns inherits it. **`label_key=None`
#: does NOT suppress that**: a positional table takes its label from `columns`
#: and never consults `label_key`, which is exactly how the sibling pass found
#: 63 tables listing a column of NUMBERS beside their ids across ten installs.
#:
#: MEASURED here, on this install's own bytes: each of the 63 has NO column
#: that is non-numeric on every row. `None` says the id stands alone, `browse`
#: prints an empty label, and `label_key=None` is what lets the blank-label
#: guard permit that.
NO_NAME_COLUMN = {"id": 0, "name": None}

#: **The EIGHT `.dat` row tables on this build that carry a real name column,
#: each measured on 7205's own decoded bytes** (`py -3 tools/dat_census.py
#: --build 7205`, then the column read back out of the file):
#:
#:   award_config.dat        col 7, the LAST field, 72/72 rows
#:                           (`30-dayTaleofSwordsmanPack`, `+3StoneLuckyPack`)
#:   cards_lottery_pool.dat  col 1, 316/316 (`1-dayFoxSpirit(Charm)`)
#:   global_lottery_pool.dat col 3, 310/310 (`+6Stone(B)`, `+5Stone(B)`)
#:   hairface_storage_type   col 3, 418/418 (`YouthSpark`, `BlossomYouth`) --
#:                           and its ID is column 1, not column 0; see below
#:   instancetype.dat        col 1, 340/340 (`Exorcism`, `TwinCity\`sNight`)
#:   official_type.dat       col 3, 20/20 (`Emperor`, `LeftPre.`). Column 4 is
#:                           a SECOND copy, equal to column 3 on every row; 3
#:                           is declared as the first of the two.
#:   texas_match_type.dat    col 2, 82/82 (`No Limit 10KK(Min)`)
#:   title_type.dat          col 2, 179/179 (`Overlord`, `Chosen~One`)
#:
#: Six of the eight agree with `patch6907`'s independent measurement over its
#: own ten installs, which is a cross-build control on the column rather than a
#: borrowed constant -- and the two that patch6907 leaves at `columns=None`
#: (`cards_lottery_pool` col 1, which the default happens to get right, and
#: `hairface_storage_type`, which it gets wrong) are stated here rather than
#: inherited either way.
#:
#: **TWO MORE TABLES REPORT A NAME COLUMN AND DO NOT HAVE ONE**, and they are
#: refused above in `NO_NAME_COLUMN`:
#:
#:   texas_match_stage.dat   census says col 1. Column 1 is `120 1000 0 1 1`
#:                           -- a packed numeric tuple. It reads as "text"
#:                           only because `str.isdigit()` is False for a
#:                           string containing a space. Twenty of its 21
#:                           columns qualify by that rule.
#:   battlepass_season.dat   census says col 4. Column 4 is
#:                           `1:3327403 2:3327404 ...` -- a packed id map,
#:                           text by the same accident.
#:
#: The census's rule ("the first column non-numeric on every row") established
#: `MagicType.dat`'s column 3 and is the right rule; it has this one false
#: positive, and a table whose "name" is a run of digits and spaces is the
#: same wrong answer as a column of bare ids, one step further from being
#: obvious. `patch6907` declares neither column, but it also declares no
#: `columns` for either table, so `ITEM_COLUMNS` puts `texas_match_stage`'s
#: label on that packed tuple on all ten of its builds.
AWARD_COLUMNS = {"id": 0, "name": 7}
CARDS_LOTTERY_COLUMNS = {"id": 0, "name": 1}
LOTTERY_POOL_COLUMNS = {"id": 0, "name": 3}
INSTANCETYPE_COLUMNS = {"id": 0, "name": 1}
OFFICIAL_COLUMNS = {"id": 0, "name": 3}
TEXAS_TYPE_COLUMNS = {"id": 0, "name": 2}
TITLE_COLUMNS = {"id": 0, "name": 2}

#: **`Achievement.dat`'s columns -- the ONE space-separated row table this
#: build declares, and the name is in column 2, not column 1.**
#:
#: MEASURED HERE, on 7205's own decoded bytes, rather than inherited from
#: `patch6907.ACHIEVEMENT_COLUMNS`. All 391 data rows (394 lines, three
#: `;------- Empty Name -------` comments at lines 0, 17 and 210 that
#: `rows_from_text` already skips):
#:
#:     col 0   391 distinct of 391, all numeric -- the achievement id, UNIQUE
#:     col 1   347 distinct, all numeric; `0` on the category rows
#:     col 2   388 distinct, NONE numeric -- the name, `~` for space
#:             (`Good~taste!`, `You~got~a~tent!`, `You~got~a~house!`)
#:     col 3   380 distinct, none numeric -- the description, `~` for space
#:     col 4   9 distinct, numeric on 390 of 391
#:     col 5   343 distinct, `Achievement_<something>` or `null`
#:     col 6   `null` on all 391 -- one value, no exceptions
#:
#: Column 2 is the FIRST column that is non-numeric on every row, which is the
#: census's own name rule, and it is not a packed numeric tuple -- so this is
#: the one place on this build where that rule and a direct read agree without
#: the `CENSUS_NAME_FALSE_POSITIVES` caveat.
#:
#: **THE COUNT DIFFERS FROM `patch6907`'s AND THE COLUMNS DO NOT.** 6907
#: measured 390 rows over its ten builds; 7205 ships 391. Taking 6907's
#: constant would have been right by luck here and is the kind of borrowing
#: `MAGIC_COLUMNS` records going wrong by one column on 5165 -- so the read
#: above is this install's, and the agreement is a cross-build control on the
#: column rather than a shared constant.
#:
#: **THE ONE ROW THAT IS NOT 7 FIELDS IS A FACT ABOUT THE FILE, NOT A DECODE
#: FAILURE, and its name is TRUNCATED IN THE LISTING.** id 19999 writes its
#: name `Comprehensive Achievement` with a literal space where every other row
#: writes `~`, so a whitespace split gives it 8 fields and shifts every field
#: after column 2 one to the right. Its label reads `Comprehensive`. That is
#: what makes the census verdict `partial` with exactly 1 residual line, and
#: it is recorded rather than smoothed over: truncating one name is the
#: smallest wrong answer available, and the alternatives -- refusing the whole
#: table, or re-joining fields on a rule no other row exercises -- are both
#: worse for the other 390. `patch6907` documents the same row the same way.
ACHIEVEMENT_COLUMNS = {"id": 0, "name": 2}

#: **The one table here whose ID IS NOT COLUMN 0, declared explicitly because
#: it is a claim and not a default.** MEASURED over all 418 rows of
#: `hairface_storage_type.dat` on this install:
#:
#:     col 0    2 distinct of 418   -- `0` on 416 of them
#:     col 1  340 distinct of 418
#:     col 2    1 distinct of 418
#:     col 3  101 distinct of 418   -- the name (`YouthSpark`)
#:     cols 0+1 together: 418 distinct of 418
#:
#: So the key is COMPOSITE and `TableSpec.columns` cannot express one. Column 0
#: carries no information at all; column 1 does, and it repeats. **Declaring a
#: non-unique id is a claim, so it is made here in words**: `browse` will list
#: 418 rows against 340 distinct ids, the repeats belong on
#: `Catalog.duplicated`, and the alternative -- column 0 -- prints `0` beside
#: every one of the 418 names, which is the id half of the same defect
#: `NO_NAME_COLUMN` exists to prevent.
HAIRFACE_COLUMNS = {"id": 1, "name": 3}

#: **The `.dat` this build's own census settled, which no spec named before:
#: 71 `@@` row tables, 13 section tables, and ONE space-row table the census
#: itself did not call declarable.**
#:
#:     py -3 tools/dat_census.py --build 7205
#:
#:     129 undeclared .dat   84 declarable (at-rows 71, sections 13)
#:                           31 positively identified as NOT text tables
#:                           14 open, each with a reason -- UNDECLARED_DAT
#:
#: **THIS TUPLE HOLDS 85, NOT 84, AND THE EXTRA ONE IS NOT A CENSUS VERDICT.**
#: `Achievement.dat` classifies `partial` -- 1 residual line of 394 -- so the
#: census does not count it declarable and never will while that row is in the
#: file. It is declared anyway, on a direct read of its columns
#: (`ACHIEVEMENT_COLUMNS`) that the verdict does not carry, and the arithmetic
#: below is stated with the two sources kept apart rather than folded into one
#: number:
#:
#:     84 census-declarable + 1 promoted past a `partial` verdict
#:      + 13 refused by name + 31 non-table families = 129
#:
#: The `partial` verdict is right about the file and wrong about whether it is
#: readable, which is exactly the gap a residual COUNT cannot express: one
#: truncated label out of 391 and a whole table are the same verdict.
#:
#: MEASURED 2026-09-07 on `Clients/7205` against the 4,059,815-block
#: dictionary. **All 85 decode at 100%: 143,904 blocks, ZERO unknown**, which
#: is what makes the census's structural numbers also the READER's numbers on
#: this build -- `block96.clean_rows` (which drops a damaged line) and
#: `block96.recover_rows` (which serves it truncated) cannot differ on a file
#: with no damage. That equality is asserted rather than assumed; see
#: `tests/test_patch7205.CensusedDatTablesAreMeasuredNotAssumed`.
#:
#: THE STRUCTURAL EVIDENCE THAT `@@` IS THE REAL DELIMITER
#: -------------------------------------------------------
#: 64 of the 71 row tables come back from `ini_census.classify_text` with
#: `single-column` also consuming them exactly, and `single-column` consumes
#: ANY text at all -- so "the census said at-rows" is not on its own a
#: measurement. What settles it is that **every one of the 71 has UNIFORM ROW
#: WIDTH** -- not one ragged row among them -- column 0 is numeric on every row
#: of all 71, and `@@` occurs in all 71. A whole-line list does not produce 32
#: fields on all 261 of its rows by coincidence.
#: The split is 64 ambiguous with `single-column`, 2 with `space-rows`
#: (`battlepass_season` and `texas_match_stage` -- the two packed-numeric-tuple
#: tables named above, and the space in that packed field is why), and 5 with
#: no competing grammar at all.
#:
#: **THIS CLAIM IS ABOUT THE 71 `@@` TABLES AND NOT ABOUT THE TUPLE, and the
#: difference stopped being cosmetic when the 85th arrived.** `Achievement.dat`
#: IS ragged -- 390 rows of 7 fields and one of 8 -- and that is not a
#: counter-example, because uniform width is the evidence that `@@` is a real
#: delimiter rather than the tie-break order speaking, and `Achievement.dat`
#: is not split on `@@` at all. Its grammar is settled by a different argument
#: (`ACHIEVEMENT_COLUMNS`), and `texas_match_prize.dat` is refused on a third:
#: ragged, `@@`, and TWO grammars selected by the id. Ragged-and-declared,
#: ragged-and-refused, and uniform-and-declared all coexist here on purpose;
#: the width is evidence about a delimiter, never a quality bar on its own.
#: This paragraph used to attach a block count (`141,054`) to the word "table"
#: while that figure was the total over the 84, which invited exactly the
#: reading that one number governs both populations.
#:
#: The trailing figure on each line is `rows, fields` off this install, plus
#: `id0 d/n` wherever column 0 is not unique.
#:
#: FOUR SUBJECTS CARRY A `:dat` SUFFIX AND IT IS NOT COSMETIC
#: ----------------------------------------------------------
#: 7205 ships `ast_prof_inauguration`, `fate_rank`, `globallotterycondition`
#: and `task_reward_type` as BOTH a `.ini` and a `.dat`, and the inherited
#: 6609 census already declares the `.ini` half under the bare name. Taking the
#: bare name here would evict the `.ini` subject silently, which is what
#: happens on `patch6907`'s ten builds -- `fate_rank.ini` is unreachable there.
#: **And the `.dat` is not uniformly the better file**: `globallotterycondition
#: .ini` is 28,594 bytes stamped 2021 against a 12,131-byte 2019 `.dat`, while
#: `task_reward_type.dat` is 42,324 bytes against a 924-byte 2016 `.ini`. One
#: rule cannot pick correctly for both pairs, so neither is dropped.
SPECS_7205_CENSUSED = (
    TableSpec('ability_score',               'ability_score.dat',               KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 276 rows, 5 fields
    TableSpec('activity_reward_type',        'activity_reward_type.dat',        KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 11 rows, 13 fields
    # **THE ONE SPACE-SEPARATED ROW TABLE IN THIS LIST, and the one entry the
    # census did NOT call declarable.** Its verdict is `partial` -- 1 residual
    # line out of 394 -- so it is not one of the 84; it is promoted on
    # evidence the census's own verdict does not carry. 393 of 393 non-comment
    # lines whole, 2,850 blocks, ZERO unknown: damage was never its problem,
    # and what was missing was a MEASURED GRAMMAR, which is a different
    # sentence and the distinction `SPECS_UNSETTLED` was built on over in
    # `patch6907`. Split on whitespace it is 390 rows of 7 fields and one of
    # 8; the odd row is id 19999 and the reason is in `ACHIEVEMENT_COLUMNS`,
    # not a decode failure.
    #
    # `@@` DOES NOT OCCUR IN THIS FILE. Under `KIND_AT_ROWS` the whole table
    # splits to one field per line and `browse` returns an EMPTY list with no
    # refusal -- the defect `Plugin.browse`'s row branch documents by name.
    # That is why the kind is asserted from the bytes in
    # `test_the_achievement_grammar_is_space_rows_and_the_name_is_column_two`
    # rather than left to the reader, and it is must-fired both ways.
    TableSpec('achievement',                 'Achievement.dat',                 KIND_SPACE_ROWS, "block96", label_key=None, columns=ACHIEVEMENT_COLUMNS),  # 391 rows: 390 of 7 fields, 1 of 8; 394 lines, 2,850 blocks, 0 unknown
    TableSpec('ast_prof_inauguration:dat',   'ast_prof_inauguration.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 7 rows, 8 fields
    TableSpec('auction_buy_back',            'auction_buy_back.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 19 rows, 3 fields
    TableSpec('award_config',                'award_config.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=AWARD_COLUMNS),  # 72 rows, 8 fields, name col 7
    TableSpec('battlepass_level',            'battlepass_level.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 50 rows, 2 fields
    TableSpec('battlepass_score_reward',     'battlepass_score_reward.dat',     KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 150 rows, 7 fields
    TableSpec('battlepass_season',           'battlepass_season.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 12 rows, 5 fields -- col 4 is a PACKED tuple, not a name
    TableSpec('battlepass_task',             'battlepass_task.dat',             KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 14 rows, 10 fields
    TableSpec('beasts_attr',                 'beasts_attr.dat',                 KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 100 rows, 18 fields
    TableSpec('cards_lottery_pool',          'cards_lottery_pool.dat',          KIND_AT_ROWS, "block96", label_key=None, columns=CARDS_LOTTERY_COLUMNS),  # 316 rows, 8 fields, name col 1
    TableSpec('coat_storage_attr',           'coat_storage_attr.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 14 rows, 11 fields
    TableSpec('coat_storage_type',           'coat_storage_type.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 765 rows, 8 fields
    TableSpec('combat_gear',                 'combat_gear.dat',                 KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 150 rows, 13 fields
    TableSpec('exchange_shop_goods',         'exchange_shop_goods.dat',         KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 1,706 rows, 15 fields
    TableSpec('exchange_shop_goods_ex',      'exchange_shop_goods_ex.dat',      KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 63 rows, 11 fields
    TableSpec('exchange_shop_lev',           'exchange_shop_lev.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 16 rows, 5 fields
    TableSpec('fate_exp',                    'fate_exp.dat',                    KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 396 rows, 15 fields
    TableSpec('fate_rank:dat',               'fate_rank.dat',                   KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 200 rows, 7 fields
    TableSpec('global_lottery_pool',         'global_lottery_pool.dat',         KIND_AT_ROWS, "block96", label_key=None, columns=LOTTERY_POOL_COLUMNS),  # 310 rows, 12 fields, name col 3
    TableSpec('globallotterycondition:dat',  'globallotterycondition.dat',      KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 151 rows, 10 fields
    TableSpec('godsoul_upgrade_config',      'godsoul_upgrade_config.dat',      KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 257 rows, 7 fields
    TableSpec('golden_league_limit',         'golden_league_limit.dat',         KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 7 rows, 12 fields
    TableSpec('gouyu_immortal',              'gouyu_immortal.dat',              KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 10 rows, 16 fields
    TableSpec('gouyu_type',                  'gouyu_type.dat',                  KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 315 rows, 13 fields
    TableSpec('hair_color_type',             'hair_color_type.dat',             KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 275 rows, 5 fields, id0 46/275 -- composite (col0,col1) is unique
    TableSpec('hairface_storage_type',       'hairface_storage_type.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=HAIRFACE_COLUMNS),  # 418 rows, 11 fields, name col 3, id col 1 at 340/418
    TableSpec('hundred_weapon',              'hundred_weapon.dat',              KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 81 rows, 11 fields
    TableSpec('instance_enter_condition',    'instance_enter_condition.dat',    KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 351 rows, 17 fields
    TableSpec('instance_prize',              'instance_prize.dat',              KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 794 rows, 7 fields, id0 66/794 -- one row per prize of an instance
    TableSpec('instance_star_condition',     'instance_star_condition.dat',     KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 144 rows, 5 fields, id0 48/144 -- one row per star
    TableSpec('instancetype',                'instancetype.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=INSTANCETYPE_COLUMNS),  # 340 rows, 26 fields, name col 1
    TableSpec('jianghu_attribute_add',       'jianghu_attribute_add.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 90 rows, 4 fields
    TableSpec('jianghu_cultivate_condition', 'jianghu_cultivate_condition.dat', KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 9 rows, 7 fields
    TableSpec('kok_hall_map',                'kok_hall_map.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 4 rows, 10 fields
    TableSpec('kok_newbie_info',             'kok_newbie_info.dat',             KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 6 rows, 4 fields
    TableSpec('magic_group_rate',            'magic_group_rate.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 14 rows, 12 fields
    TableSpec('marketing_active',            'marketing_active.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 261 rows, 10 fields
    TableSpec('newslot_broadcast',           'newslot_broadcast.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 3 rows, 5 fields
    TableSpec('newslot_line',                'newslot_line.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 100 rows, 7 fields
    TableSpec('newslot_line_probability',    'newslot_line_probability.dat',    KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 147 rows, 6 fields
    TableSpec('newslot_roulette',            'newslot_roulette.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 8 rows, 3 fields
    TableSpec('newslot_type',                'newslot_type.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 110 rows, 13 fields
    TableSpec('newstone_limit',              'newstone_limit.dat',              KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 12 rows, 4 fields
    TableSpec('nosuch_config',               'nosuch_config.dat',               KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 24 rows, 6 fields
    TableSpec('official_type',               'official_type.dat',               KIND_AT_ROWS, "block96", label_key=None, columns=OFFICIAL_COLUMNS),  # 20 rows, 7 fields, name col 3 (col 4 is its twin)
    TableSpec('operating_prize',             'Operating_Prize.dat',             KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 481 rows, 8 fields
    TableSpec('point_extend',                'point_extend.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 10 rows, 7 fields
    TableSpec('process_goal',                'process_goal.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 29 rows, 15 fields
    TableSpec('process_task',                'process_task.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 191 rows, 17 fields
    TableSpec('prof_lev_benefit',            'prof_lev_benefit.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 549 rows, 8 fields
    TableSpec('prof_lev_up',                 'prof_lev_up.dat',                 KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 549 rows, 6 fields
    TableSpec('prof_title_benefit',          'prof_title_benefit.dat',          KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 33 rows, 8 fields
    TableSpec('random_task_cost',            'random_task_cost.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 9 rows, 4 fields
    TableSpec('recommend_magic_group',       'recommend_magic_group.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 9 rows, 11 fields
    TableSpec('roulette_chip_info',          'roulette_chip_info.dat',          KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 2 rows, 7 fields
    TableSpec('rune_levexp',                 'rune_levexp.dat',                 KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 40 rows, 4 fields
    TableSpec('rune_storage_attr',           'rune_storage_attr.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 145 rows, 4 fields
    TableSpec('spirit_rate',                 'spirit_rate.dat',                 KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 117 rows, 8 fields
    TableSpec('syn_formtype',                'syn_formtype.dat',                KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 187 rows, 8 fields
    TableSpec('syn_skill_type',              'syn_skill_type.dat',              KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 1,400 rows, 7 fields
    TableSpec('syndicate_level',             'syndicate_level.dat',             KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 9 rows, 12 fields
    TableSpec('task_reward_type:dat',        'task_reward_type.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 261 rows, 32 fields
    TableSpec('texas_match_condition',       'texas_match_condition.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 60 rows, 22 fields
    TableSpec('texas_match_stage',           'texas_match_stage.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 60 rows, 21 fields -- cols 1..20 are PACKED tuples, not names
    TableSpec('texas_match_type',            'texas_match_type.dat',            KIND_AT_ROWS, "block96", label_key=None, columns=TEXAS_TYPE_COLUMNS),  # 82 rows, 12 fields, name col 2
    TableSpec('title_type',                  'title_type.dat',                  KIND_AT_ROWS, "block96", label_key=None, columns=TITLE_COLUMNS),  # 179 rows, 8 fields, name col 2
    TableSpec('token_type',                  'token_type.dat',                  KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 7 rows, 9 fields
    TableSpec('xuanbao_addition_attr',       'xuanbao_addition_attr.dat',       KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 57 rows, 5 fields
    TableSpec('xuanbao_compose_attr_limit',  'xuanbao_compose_attr_limit.dat',  KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 13 rows, 4 fields
    TableSpec('xuanbao_rand_attr',           'xuanbao_rand_attr.dat',           KIND_AT_ROWS, "block96", label_key=None, columns=NO_NAME_COLUMN),  # 65 rows, 4 fields, id0 5/65 -- composite (col0,col1) is unique

    # -- the 13 section tables ---------------------------------------------
    #
    # **FOUR OF THE THIRTEEN ARE `plaintext`, NOT `block96`, and the codec is
    # `core/inidat.classify`'s verdict on THIS install rather than the era's.**
    # `Shop.dat`, `SkinVersion.dat`, `GetPlatformInfo.dat` and
    # `ndloginServer.dat` were never enciphered here. `DeclaredCodecs.
    # test_every_declared_dat_matches_inidat` is what enforces that, and a
    # spec carrying the family from its neighbours would point the block96
    # reader at a file that was never encrypted.
    #
    # **`Shop.dat` is the ONE label key measured on this build**: `Name`
    # carries a value on 160 of its 161 sections (99%), the odd one out being
    # the `[Header]` block, so it clears `gen_ini_specs`'s 90% bar. Every
    # other section table here answers `None` -- which is a MEASUREMENT and
    # not a shrug: `Plugin.ROW_LABEL_DEFAULT` is `"Name"` and would otherwise
    # print 1,508 blank cells beside `UserHelpInfo.dat`'s sections.
    #
    # `StageGoal.dat` is the near miss worth naming: `Title` carries a value
    # on 78 of its 89 sections, 87.6%, BELOW the bar. It is left at `None`
    # rather than rounded up, because the rule is what makes the other twelve
    # answers evidence.
    TableSpec('appkey',                'Appkey.dat',           KIND_SECTIONS, "block96",   label_key=None),  # 1 section, [Appkey]
    TableSpec('autouseimmortal',       'AutoUseImmortal.dat',  KIND_SECTIONS, "block96",   label_key=None),  # 1 section, [AutoLife]
    TableSpec('debugconfig',           'DebugConfig.dat',      KIND_SECTIONS, "block96",   label_key=None),  # 1 section, [PingTest]
    TableSpec('getplatforminfo',       'GetPlatformInfo.dat',  KIND_SECTIONS, "plaintext", label_key=None),  # 1 section, [INFO]
    TableSpec('kokquickpay',           'kokquickpay.dat',      KIND_SECTIONS, "block96",   label_key=None),  # 1 section, [Vegas]
    TableSpec('ndloginserver',         'ndloginServer.dat',    KIND_SECTIONS, "plaintext", label_key=None),  # 2 sections, [Default] and [Order]
    TableSpec('quickpay',              'quickpay.dat',         KIND_SECTIONS, "block96",   label_key=None),  # 114 sections, named by server (Dragon, Phoenix)
    TableSpec('runeeffect',            'runeeffect.dat',       KIND_SECTIONS, "block96",   label_key=None),  # 275 sections of 393 headers -- 118 repeats, see RUNEEFFECT_REPEATS
    TableSpec('shop',                  'Shop.dat',             KIND_SECTIONS, "plaintext", label_key='Name'),  # 161 sections, Name on 160
    TableSpec('skinversion',           'SkinVersion.dat',      KIND_SECTIONS, "plaintext", label_key=None),  # 1 section, [Conquer] Version=2001
    TableSpec('stagegoal',             'StageGoal.dat',        KIND_SECTIONS, "block96",   label_key=None),  # 89 sections; Title on 78 (87.6%), below the bar
    TableSpec('help',                  'UserHelpInfo.dat',     KIND_SECTIONS, "block96",   label_key=None),  # 1,508 sections
    TableSpec('webinfo',               'WebInfo.dat',          KIND_SECTIONS, "block96",   label_key=None),  # 2 sections, [WebBegin] and [SafeWeb]
)


def _version(root: Path) -> str:
    """`version.dat`, four bytes, `b'7205'`. The only clean discriminator in
    this lineage -- see `confidence`."""
    try:
        return (root / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7205(Patch6609):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:gamemapex': [287, 2, 2, 0],
        'Name=:monster': [3709, 66, 65, 0],
        'Name=:npc:NpcX.ini': [826, 4, 4, 0],
        'Name=:npc:npc.ini': [2913, 40, 20, 0],
        'Name=:npc:terrainnpc.ini': [167, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [164, 3, 3, 0],
        'achievement': [391, 2, 0, 0],
        'classdesc': [116, 25, 25, 0],
        'gamemapex': [287, 2, 2, 0],
        'item': [44682, 1, 0, 0],
        'item:sub': [633, 1, 0, 0],
        'map:dest': [936, 7, 6, 0],
        'npc:NpcX.ini': [819, 4, 4, 0],
        'npc:npc.ini': [2884, 20, 20, 0],
        'npc:terrainnpc.ini': [165, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [164, 3, 3, 0],
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

    #: This plugin reads a build whose `ini/*.dat` are the 96-bit block
    #: cipher and which `tools/dat_census.py` censuses. NOT `BLOCK96_CENSUS`,
    #: which is Patch7205's own measured census DICT (a first draft reused
    #: that name and the dict silently overwrote it). A CLASS attribute, so
    #: a subclass for a later patch (7217, 7250, 7275 extend Patch7205)
    #: inherits the answer, and `tests/test_dat_census.py` asks the plugin
    #: instead of matching a hand-kept tuple of names -- which is what every
    #: new plugin would otherwise have had to edit.
    DAT_CENSUS_ERA = True

    name = "patch7205"
    label = "Official patch client 7205"
    origin = "official"
    aliases = ("7205",)

    #: **Re-measured on 7205's own `ini/`.** Same four tables as 6609 and the
    #: same columns, on this build's own larger files:
    #:
    #:   region.ini         303 rows, 14 fields; column 6 non-numeric on
    #:                      303/303, 84 distinct; column 1 numeric on all 303.
    #:   EventTypeName.ini  24 rows; column 2 non-numeric on 24/24.
    #:   VipTrans.ini       31 rows; column 2 non-numeric on 31/31.
    #:   restrain.ini       5 rows; column 2 non-numeric on 5/5.
    ROW_COLUMNS = {
        "region": {"id": 0, "name": 6},
        "eventtypename": {"id": 1, "name": 2},
        "viptrans": {"id": 0, "name": 2},
        "restrain": {"id": 0, "name": 2},
    }

    #: **The subclass choice is 6609 and it is a claim about the ART, not the
    #: tables.** Every `.dbc` is byte-identical to 6609's, the WDF pair is the
    #: 2005 one the whole official lineage ships, and the twelve-table set
    #: (three fewer than 6090) is 6609's. What is NOT inherited is the content
    #: layer: `SPECS_7205` replaces every `tq-stream` codec with `block96`,
    #: because `SPECS_6609` on this install is the confidently-wrong answer
    #: this file's header measures.
    notes = (
        "6609's client with 7878's cipher. Compiled .dbc tables and both WDF "
        "archives are byte-identical to 6609's; ini/*.dat are the 96-bit ECB "
        "block cipher shared by every build from 6907 to 7878, which "
        "core/tqdat.py cannot read and which core/block96.py opens through "
        "the dictionary derived from 7878. Coverage is per RECORD, not per "
        "block: 86.1% of itemtype.dat's blocks are known and 2,068 of at "
        "least 39,771 rows come out whole, while mounttype.dat comes out "
        "entire. Blocks the dictionary lacks are never guessed -- the record "
        "containing one is dropped, counted, and reported. The compiled art "
        "tables froze at 6609 while npc.ini grew to 2,913 rows, so 447 "
        "(15.3%) name a simple object the twin does not carry. 224 Lua "
        "scripts and 37 DLLs: the largest script surface measured, and this "
        "plugin declares it as a surface rather than offering to parse it."
    )

    #: The cipher boundary, as a closed range measured over the 31 installs on
    #: this box. 6868 is the last TQ-stream client; 6907 the first block96 one.
    #: Recorded because the boundary is what makes this plugin necessary and
    #: `patch6090`'s inherited specs wrong.
    CIPHER_ERA = (6907, 7878)

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """`version.dat` decides, exactly as it does for 6609.

        7205 answers yes to every format probe 6090 and 6609 do -- the same
        compiled magics, the same byte-identical archives -- so a
        format-based claim here would tie with both and be broken by
        dictionary order, which is not evidence. The cipher WOULD discriminate
        (6609's `itemtype.dat` is tq-stream and this one is not) and is
        deliberately not used: classifying a 13 MB file costs a read, and
        `confidence` is called for every plugin on every root.

        `Patch6090.confidence` already returns 0.45 for a stamped sibling, so
        0.95 on our own stamp wins cleanly without weakening its claim on its
        own base -- and, critically, WITHOUT this plugin claiming 6907..7189
        or 7632..7867, which share 7205's cipher, have no plugin of their own,
        and are NOT measured here. An unclaimed build keeps falling to
        `patch6090` at 0.45, which is wrong about its tables in the way this
        header records; claiming them from here would be wrong about their
        content instead, and silently.
        """
        if _version(Path(root)) == "7205":
            return 0.95
        return 0.0

    #: **The `.dat` the 2026-09-07 census MEASURED AND STILL DID NOT DECLARE,
    #: and why each one.** `{filename_lower: reason}`, fourteen entries.
    #:
    #: This dict is as much the point of the census as `SPECS_7205_CENSUSED`
    #: is. A spec is a claim that this build knows the table's shape, and COMod
    #: hands a user an editor for every subject it declares -- so declaring on
    #: weak evidence is worse than leaving it out. `Action.dat` is the standing
    #: precedent and it is in `SPECS_7205` above: its record shape is settled
    #: EXACTLY (u32 count=53, 53 records of five little-endian u32, all 1,064
    #: bytes, byte-identical on 24 installs) and it is still refused, because a
    #: row of five anonymous numbers is not a table anyone should be handed an
    #: editor for. **A refusal naming what IS known beats a declaration that
    #: guesses.**
    #:
    #: `tests/test_patch7205.CensusedDatTablesAreMeasuredNotAssumed` asserts
    #: that every name here is still undeclared AND still fails the criterion
    #: named, so a build or a dictionary that changes one of these makes this
    #: list wrong out loud rather than quietly stale.
    UNDECLARED_DAT = {
        # -- the one that is CLOSE, and the distance is stated so the next
        #    pass does not have to re-measure it ---------------------------
        #
        # `achievement.dat` WAS THE OTHER ONE AND IT IS NOW DECLARED, in
        # `SPECS_7205_CENSUSED` as `KIND_SPACE_ROWS` with
        # `ACHIEVEMENT_COLUMNS`. Its entry here read "THE STRONGEST PROMOTION
        # CANDIDATE ON THIS BUILD ... left undeclared only because this pass's
        # scope was the 84", which is a scope note and not a finding -- so it
        # was promoted rather than re-explained.
        "texas_match_prize.dat": (
            "**TWO GRAMMARS IN ONE FILE, SELECTED BY THE MATCH ID -- not one "
            "ragged table.** 5,441 rows whole, 19,056 blocks, 0 unknown, so "
            "this is the file's real shape and not damage, at three widths: "
            "5,377 rows of 6 fields, 63 of 7, 1 of 9. **The wide rows carry "
            "an extra RANK-RANGE column at index 2** -- `1-1`, `2-2`, `4-8` "
            "-- present on all 63 and on none of the 5,377, so a 6-field row "
            "is a flat prize and a 7-field row is a per-rank prize. AND THE "
            "TWO SETS OF IDS ARE DISJOINT: 46 ids appear only 6-wide, 17 only "
            "7-wide, intersection EMPTY over all 5,441 rows. A file whose "
            "column meanings are a function of another column's value is not "
            "something `TableSpec.columns` can express -- one index names the "
            "reward on one half and a numeric flag on the other -- so "
            "declaring it means labelling 5,377 rows or 63 wrong, whichever "
            "index is chosen. Column 0 is not a browsable id either: 63 "
            "distinct over 5,441 rows, and it is a FOREIGN KEY into "
            "`texas_match_type.dat` (60 of the 63 resolve there; 3007, 3008 "
            "and 3009 have no row in it). The single 9-field row is id 3009 "
            "rank `1-1`, which appends `1` and `,1seasonPoint` to the ranked "
            "grammar -- a second reward on the top rank, one row of 5,441, "
            "and not a third grammar. patch6907 declares its own 81-row copy; "
            "this build's is 67x larger and is not the same table.\n"
            "\n"
            "**THE COMMA SPLITS NOTHING AND AN EARLIER VERSION OF THIS ENTRY "
            "SAID IT DID.** It read: the extra fields are a bracketed literal "
            "`[item Cash,Poker Master]` whose comma splits further. The "
            "delimiter is `@@`; commas sit INSIDE fields and are never a "
            "separator -- 506 of the 5,377 six-field rows carry one in their "
            "reward field and every one of those rows is 6 fields wide. That "
            "reading came from the census `residual` listing, which shows two "
            "comma-bearing lines and no width, and it pointed the next pass "
            "at a quoting problem this file does not have."),
        # -- four whose grammar is genuinely not settled -------------------
        "emotionico.dat": (
            "space-separated and ragged, 128 lines whole at 0 unknown "
            "blocks: 115 rows of 2 fields, 11 of 3, 2 of 4. Census verdict "
            "`partial` with 13 residual lines. The rows that fit read `98 "
            "Smile 1`, so there is a real table here -- what is NOT "
            "established is what the wider rows are, and 13 of 128 is too "
            "many to call a footnote."),
        "gameloadinfo.dat": (
            "1 of its 12 lines survives the grammars. The bytes are a "
            "BYTE-SHIFTED ini -- `DcuQsi:kpuu` where the plaintext reads "
            "`Game...`, one letter up throughout -- which core/inidat "
            "classifies `plaintext` because most of the result is printable. "
            "There is a real table under a transform nobody on this project "
            "has established, and `plaintext` is the classifier being fooled "
            "by printability, not a measurement."),
        "silent.dat": (
            "437 of 437 lines whole, 766 blocks, 0 unknown, and it is not a "
            "table: split on whitespace, 436 of the 437 lines are a SINGLE "
            "field, and the content is a spam/URL blacklist "
            "(`\\money-maker.mfbiz.co<br ...`). A list of whole lines is a "
            "grammar this project has (KIND_LIST) and the reason not to "
            "declare it is different: there is no id and no label in it, so "
            "`browse` would print 437 URL fragments in the id column. See "
            "`slient.dat` below, which is NOT this file."),
        "tqist.dat": (
            "one line, `partial`, and the bytes are not text at all -- a "
            "short binary head followed by a long run of `{` filler. No "
            "grammar accounts for it, and the census's `plaintext` family is "
            "the printability heuristic speaking."),
        # -- the one with a route and no bytes ----------------------------
        "serverplay.dat": (
            "5,696 bytes, 474 blocks, 474 UNKNOWN -- the dictionary holds not "
            "one of them, so nothing comes back and there is no grammar to "
            "measure. It is the corpus's one genuine residue "
            "(patch7878.NOT_CONTENT) and this build's copy is not even 7878's "
            "file, so 7878's answer would not be this file's answer. See "
            "UNCOVERED_TABLES, which carries the same finding. **patch6907 "
            "NAMES this subject and refuses it with a reason; patch7205 does "
            "not name it at all, so the tool is silent about it here in "
            "exactly the words it uses for a file that is not there.** That "
            "gap is recorded rather than closed, because closing it is a "
            "declaration and this pass's scope was the census's 84."),
        # -- the binary record table wearing a text verdict ---------------
        "tips.dat": (
            "a BINARY record table, not a text grammar, and it is the one "
            "here a careless reader would declare. The file contains no "
            "newline, so the census classifies ONE line -- and one line is "
            "consumed exactly by every grammar there is, which makes the "
            "`flat-keys` verdict the tie-break ORDER speaking and not the "
            "file. The decode is a 32-byte `Tips` header then {u32 id, u32 "
            "len, char[len]} records carrying the loading-screen hints. Same "
            "finding as zephyr1057's Tips.dat, where a space-rows "
            "declaration parsed 2 rows and THE POSITIVE CONTROL PASSED on "
            "garbage, because two adjacent tokens genuinely are adjacent."),
        # -- the six 0-byte files -----------------------------------------
        "blackjack_cfg.dat": "the client ships it as a 0-byte file.",
        "monster_live.dat": "the client ships it as a 0-byte file.",
        "new_battle_buff.dat": "the client ships it as a 0-byte file.",
        "promotion_activity.dat": "the client ships it as a 0-byte file.",
        "promotion_goods.dat": "the client ships it as a 0-byte file.",
        "slient.dat": (
            "a 0-byte file -- **AND NOTE THE SPELLING. IT IS NOT A TYPO IN "
            "THIS RECORD.** 7205 ships BOTH `slient.dat` (0 bytes, this "
            "entry) AND `silent.dat` (9,195 bytes, 437 lines, entry above). "
            "The client's own filename carries the transposition and both "
            "files are on disk; verified by listing `ini/` rather than "
            "inferred. Do not 'fix' either name."),
    }

    #: **The families the census could not open AT ALL, as a shape rather than
    #: 31 individual refusals.** Every one is a POSITIVE identification of
    #: something that is not a text table on this build, so `undeclared` here
    #: means "not this layer's subject", never "not yet measured" -- and that
    #: distinction is the whole reason this constant exists instead of the 31
    #: names sitting in `UNDECLARED_DAT` where a later pass would read them as
    #: unfinished work.
    #:
    #: MEASURED 2026-09-07 by `core/inidat.classify` on this install:
    #:
    #:     rar-mangled       11  c3anp, c3aanp, c3bbnp..c3llnp, effanl -- the
    #:                           `.c3` animation packs, an ARCHIVE format
    #:     rsa-mysqldump     10  RaceTrackProp, SHLayout(+800X600),
    #:                           ShowHandLayout(+800X600), ShowHandTable,
    #:                           ShowHandTableRace, suittype,
    #:                           MyAnimate(+800X600)
    #:     binary-plain       4  AutoAllot, LevelExp, Pet.Dat, pna_swf
    #:     tq-stream          3  client_config, kok_roleview,
    #:                           UserHelpInfo.ini.dat
    #:     unknown            2  WeaponActionData, WeaponMotionData
    #:     shift-obfuscated   1  Play.dat
    #:
    #: **`tq-stream` IS THREE HERE AND FOUR ON patch6907, and the difference is
    #: a finding, not a miscount**: `silent.dat` is tq-stream on 6907 and 6968
    #: and block96 from 7009 onward, so a file whose CIPHER changes mid-lineage
    #: lands in a different bucket on either side of the boundary.
    #:
    #: `LevelExp.dat` is `binary-plain` here and is a DIFFERENT file from the
    #: `levexp.dat` that `SPECS_7205` names and refuses -- same stem, different
    #: case, different family, different bytes. Both are on disk.
    UNOPENED_DAT_FAMILIES = {
        "rar-mangled": 11, "rsa-mysqldump": 10, "binary-plain": 4,
        "tq-stream": 3, "unknown": 2, "shift-obfuscated": 1,
    }

    #: **WHAT THE 85 ACTUALLY CHANGE FOR A USER, and the two halves are not
    #: the same win.** MEASURED by browsing every subject on this install
    #: before and after the declaration, whole listings rather than a 25-row
    #: page:
    #:
    #:     subjects        162 -> 247       browsable  160 -> 245
    #:
    #: **247 -> 250 and 245 -> 248 on 2026-09-19**, and NOT from this census:
    #: the CR/LF line-split fix let 6609's census settle `OperateActivity.ini`
    #: and both `TexasChatGUI*.ini` (0x85 NEL bytes read as phantom line
    #: breaks under `splitlines`), and 7205 borrows 6609's census.
    #:     rows added to the surface        15,690
    #:
    #:     CONTENT   21   the listing carries something a person can read and
    #:                    search BY -- a non-numeric string in either column
    #:                    on a majority of rows.
    #:     PRESENCE  64   a column of ids with an empty label. The subject is
    #:                    now named, counted and refusal-free, and nothing in
    #:                    the listing is legible.
    #:
    #: The 21 are the 8 `@@` row tables with a measured name column
    #: (`award_config` -> `30-dayTaleofSwordsmanPack`, `instancetype` ->
    #: `Exorcism`), `achievement` (space rows, `Good~taste!`), and the 12
    #: section tables whose SECTION HEADERS are words (`quickpay` -> `Dragon`,
    #: `[PingTest]`, `[AutoLife]`). `runeeffect` is a section table and lands
    #: in the 64, because its headers are numeric ids (`4030101`).
    #:
    #: **`achievement` MOVED THIS BY ONE AND THAT IS THE WHOLE POINT OF THE
    #: SPLIT.** It adds 391 rows out of 15,690 -- 2.5% of the row count -- and
    #: it is one of only 21 subjects whose listing a person can read. A count
    #: of rows would have called it a rounding error.
    #:
    #: **The second number is the one that is easy to overstate.** "85 tables
    #: now readable" and "21 tables a user can find something in" are both
    #: true, and only the first is what a subject count reports. Sixty-four of
    #: these are config tables that are numbers all the way across; declaring
    #: them makes the count honest and the row openable, and it does not make
    #: the listing informative. `tests/test_patch7205.
    #: CensusedDatTablesAreMeasuredNotAssumed.test_the_browse_impact_is_
    #: twenty_one_and_sixty_four` re-derives both off the install.
    BROWSE_IMPACT = {"subjects_before": 162, "subjects_after": 250,
                     "content": 21, "presence_only": 64, "rows_added": 15690}

    #: `runeeffect.dat` writes 393 `[section]` headers over 275 DISTINCT names
    #: -- 118 repeats. `catalog.sections_from_text` returns the dupes
    #: separately and `browse` lists the 275, which is why the spec is declared
    #: with the repeat count in its comment rather than a row count that would
    #: not match the file. Recorded because "275 sections" and "393 headers"
    #: are both true and only one of them is what the surface shows.
    RUNEEFFECT_REPEATS = {"headers": 393, "distinct": 275, "repeats": 118}

    #: **The census's own name-column rule has TWO false positives on this
    #: build**, and they are named because the rule is otherwise the best
    #: instrument here and will be used again.
    #:
    #: `dat_census.measure_columns` calls a column a name when it is
    #: non-numeric on every row, via `s.lstrip("-").isdigit()`. A field holding
    #: `120 1000 0 1 1` is non-numeric by that test -- the SPACE disqualifies
    #: it -- so a packed numeric tuple reads as text. On 7205:
    #:
    #:     texas_match_stage.dat   20 of its 21 columns "qualify"; column 1 is
    #:                             `120 1000 0 1 1`
    #:     battlepass_season.dat   column 4 is `1:3327403 2:3327404 ...`
    #:
    #: Both are declared with `NO_NAME_COLUMN`. A label made of digits and
    #: separators is the same wrong answer as a column of bare ids, one step
    #: further from being obvious in a listing.
    CENSUS_NAME_FALSE_POSITIVES = ("texas_match_stage.dat",
                                   "battlepass_season.dat")

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """7205's curated specs, then **6609's censused `.ini`, filtered**.

        `plugins/catalog/censused.py` carries a measured grammar and label key
        per `.ini` for eight plugins and none for `patch7205` -- nobody has run
        the census on this build. 6609's census is **141 specs, of which 139
        name a file 7205 ships**; declaring nothing would drop all of them,
        and with them most of what this install can be browsed for. MEASURED
        on the install:

            patch6090 on 7205    142 subjects, 128 readable  (pre-change)
            patch7205 on 7205    161 subjects, 160 readable

        So the sibling census is INHERITED rather than dropped, and two things
        keep that from being a free assumption:

          * **Filtered to files this build ships.** A censused entry naming a
            file 7205 does not have would otherwise render as a refusal about
            a table that never existed -- "we could not read it" in the words
            reserved for a real absence.
          * **Gated -- BY A DIFFERENT TEST THAN THIS DOCSTRING USED TO NAME.**
            It said `tests/test_plugin_catalogs.py`'s blank-label guard "runs
            over every build in its `BUILDS` table, 7205 included". **7205 is
            not in `BUILDS`** (5017, 5065, 5165, 5517, 6090, 6609, 6907, 7878)
            and `NoSubjectPrintsAColumnOfBlanks.ALL` is that tuple plus CCO
            and Zephyr, so the guard has never run here. The gate that does
            run is `tests/test_patch7205.py::
            NoSubjectPrintsAColumnOfBlanksOn7205`, which browses every subject
            this method returns -- inherited and censused alike -- and fails
            any whose listing carries not one label unless its spec says
            `label_key=None`.

        **`SPECS_7205_CENSUSED` IS JOINED BEFORE THE BORROWED CENSUS AND THE
        BORROWED SPECS ARE DE-DUPLICATED AGAINST IT.** 7205 ships four stems
        as both a `.dat` and an `.ini` -- `ast_prof_inauguration`,
        `fate_rank`, `globallotterycondition`, `task_reward_type` -- and on
        `patch6907` the `.dat` takes the bare subject and the `.ini` twin
        becomes unreachable with nothing saying so. The four `.dat` here carry
        a `:dat` subject instead, so both halves are browsable: measured, the
        `.dat` is not uniformly the better file
        (`globallotterycondition.ini` is 28,594 bytes stamped 2021 against a
        12,131-byte 2019 `.dat`).

        A curated spec still wins on subject and on filename, via
        `censused.extend`.
        """
        root = Path(root)
        ini = root / "ini"
        present = {p.name.lower() for p in ini.iterdir()} if ini.is_dir() \
            else set()
        # **`SPECS_7205_CENSUSED` IS FILTERED TO DISK, EXACTLY AS THE
        # BORROWED `.ini` CENSUS IS.** Every one of the 85 was measured on
        # THIS install, so on this install the filter removes nothing -- but a
        # spec tuple is a module constant and this method takes a root, and
        # `plugins.for_kind("patch7205")` can be pointed at any directory by a
        # caller (the catalogs suite does exactly that). A spec for a file the
        # root does not ship renders as "we could not read it", which is the
        # wording reserved for a real absence.
        censused_here = tuple(s for s in SPECS_7205_CENSUSED
                              if s.filename.lower() in present)
        inherited = tuple(s for s in censused.specs_for("patch6609")
                          if s.filename.lower() in present)
        # The curated tuples are joined FIRST and the borrowed census is then
        # de-duplicated against them on BOTH subject and filename -- the four
        # `:dat` subjects above exist so that de-duplication has nothing to do
        # for the `.ini` twins. `censused.extend` performs the same removal for
        # `specs_for("patch7205")`, which is empty; this one is for 6609's.
        here = SPECS_7205 + censused_here
        taken = {s.subject for s in here}
        files = {s.filename.lower() for s in here}
        borrowed = tuple(s for s in inherited
                         if s.subject not in taken
                         and s.filename.lower() not in files)
        return censused.extend(here + borrowed, self.name)

    def why_no_tables(self) -> str:                       # pragma: no cover
        return ("7205 declares tables; if you are reading this the install "
                "has no ini/ directory")

    #: MEASURED per table on the install, and recorded so a caller can compare
    #: what it got against what this client can possibly give. Keys are the
    #: shipped filenames, lowercased. Reproduce with the SHIPPING reader:
    #:
    #:     d = block96.load_dictionary(<Clients>/7205)
    #:     block96.read_table(<...>/ini/itemtype.dat, d, "rows").recovery
    #:
    #: **RE-WRITTEN IN `Recovery`'s VOCABULARY, 2026-09-07.** These keys used
    #: to be `whole`/`floor`, and the command above used to read
    #: `py -3 core/block96.py coverage <path>` -- a module-level
    #: `block96.coverage()` and a `__main__` that **have never existed in this
    #: repository**. `git log -S"def decode_file" -- core/block96.py` returns
    #: zero commits across the whole history and `coverage` is a property on
    #: `class Recovery`, so both the citation and the two callers in
    #: `tests/test_patch7205.py` were unrunnable from the day they landed
    #: (`187cbb64`, "ported, not merged"). A pin whose provenance command does
    #: not run is a pin nobody can re-measure, so the vocabulary is now the
    #: shipping reader's and every key below is a field of `Recovery`:
    #:
    #:     blocks  Recovery.blocks     lines   Recovery.lines
    #:     known   Recovery.coverage   intact  Recovery.intact
    #:     units   Recovery.units      shape   the `kind` passed to read_table
    #:
    #: `intact` is what `whole` meant for ROWS and `units` is what it meant
    #: for SECTIONS -- the record unit differs with the shape, so no single
    #: field carries it across both. `floor` is GONE and is not `lines`; see
    #: `tests/test_patch7205.TheFloorThatRecoveryCannotExpress`, which states
    #: plainly what that costs.
    #:
    #: RE-MEASURED against the 4,059,815-block dictionary, which reads every
    #: one of these five at 100% coverage. Under the dictionary these numbers
    #: were first taken against (3,390,890 blocks) the same call reproduces
    #: the old `whole` exactly -- 2,068 / 375 / 92 as `intact`, 1,819 as
    #: `units` -- so the change of vocabulary is verified, not asserted.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1080527, "known": 1.0, "lines": 44684,
                         "intact": 44684, "units": 44684, "shape": "rows"},
        "monster.dat": {"blocks": 103113, "known": 1.0, "lines": 96045,
                        "intact": 96045, "units": 3709, "shape": "sections"},
        "magictype.dat": {"blocks": 47730, "known": 1.0, "lines": 2907,
                          "intact": 2907, "units": 2907, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 17319, "known": 1.0, "lines": 633,
                            "intact": 633, "units": 633, "shape": "rows"},
        "mounttype.dat": {"blocks": 36719, "known": 1.0, "lines": 25583,
                          "intact": 25583, "units": 1819, "shape": "sections"},
    }

    #: The whole `ini/` `.dat` surface under this cipher, RE-MEASURED
    #: 2026-09-07 against the 4,059,815-block dictionary:
    #: `whole` is what the readers serve; `floor` is a LOWER BOUND on the
    #: table (see the header). The two are carried separately and never
    #: subtracted, because `floor - whole` is not "rows we lost" -- some of
    #: those records are counted twice by neither instrument and some are
    #: invisible to both.
    #: **RE-MEASURED 2026-09-07 AND EVERY ONE OF THEM CLOSED.** These were
    #: the numbers behind this plugin's headline claim -- 86.1% of blocks,
    #: 5.2% of rows -- and under the 4,059,815-block dictionary all five read
    #: 1.000 with `whole == floor`. `itemtype.dat` went 2,068 -> 44,684, past
    #: the 39,771 FLOOR the old measurement recorded, which is the floor
    #: doing its job: it said "at least", and the truth was above it.
    #:
    #: The old row is kept in the comment beside each so the movement is
    #: legible rather than overwritten:
    #:
    #:     itemtype     0.861  2,068 of >=39,771   ->  1.000  44,684
    #:     monster      0.926    558 of >=832      ->  1.000   3,709
    #:     magictype    0.841    375 of >=3,602    ->  1.000   2,907
    #:     ItemtypeSub  0.863     92 of >=506      ->  1.000     633
    #:     mounttype    1.000  1,819               ->  1.000   1,819  (was
    #:                                                already closed)
    #:
    #: `blocks` is unchanged on all five, which is the control: the block
    #: COUNT is a property of the ciphertext and cannot move with the
    #: dictionary, so a rebuild that changed it would mean the reader changed.

    #: The whole `ini/` `.dat` surface under this cipher, MEASURED 2026-09-07
    #: against the 4,059,815-block dictionary:
    #:
    #:     101 block96 files, 1,488,616 blocks, 809 unknown -> 99.95%
    #:      99 decode with ZERO unknown blocks
    #:       0 partial
    #:       2 with not one known block
    #:
    #: **THE `partial` COLUMN IS NOW EMPTY AND THAT IS A FINDING, NOT A
    #: TIDY-UP.** The previous figures -- 60/37/4 at 192,739 unknown -- were
    #: taken against a 3,390,890-block dictionary. Two rebuilds later this
    #: install has no in-between table left: every file is either wholly
    #: readable or wholly opaque. `tests/test_patch7205.CoverageArithmetic`
    #: measured its "block coverage is not row coverage" claim on this
    #: install's `itemtype.dat`, which now reads 100% on both axes, so that
    #: claim had no subject left here and MOVED to one that still shows it
    #: (7867's `ini/ft/itemtype.dat`, 87.1% of blocks against 16.6% of whole
    #: rows) rather than being re-pinned to 1.0 and left unable to fail.
    #:
    #: **46 of the 99 are byte-identical to 7878's file**, so their coverage
    #: is the dictionary quoting itself and is not evidence of anything. The
    #: other **53 are different files that still decode entire** -- that is
    #: the real gain, and it is the number to cite. It was 14 of 60.
    #: **809 is exactly `levexp.dat` (335) + `ServerPlay.dat` (474)**, the two
    #: `UNCOVERED_TABLES` below. There is no partially-covered file left on
    #: this install: every block96 table is either entirely readable or
    #: entirely absent from the dictionary, and nothing is in between.
    #:
    #: WHAT IT WAS, so the movement is legible rather than overwritten:
    #:
    #:     101 files, 192,739 unknown -> 87.1% · 60 whole · 37 partial · 4 at
    #:     zero, of which 46 of the 60 were byte-identical to 7878's
    #:
    #: **46 of the 99 are still byte-identical to 7878's file**, so their
    #: coverage is the dictionary quoting itself and is not evidence of
    #: anything. The other **53 are different files that decode entire** --
    #: that is the real gain, up from 14, and it is the number to cite. The
    #: fourteen used to be short enough to name; fifty-three are not, and
    #: `Block96Census` counts them off the install rather than from a list.
    #:
    #: Reproduced by `tests/test_patch7205.py::Block96Census`.
    BLOCK96_CENSUS = {"files": 101, "blocks": 1488616, "unknown": 809,
                      "fully_covered": 99, "partial": 0, "uncovered": 2,
                      "fully_covered_identical_to_7878": 46,
                      "fully_covered_and_different": 53}

    #: The `.dat` the dictionary cannot touch at all -- not one block of any
    #: of them is known -- with what is actually true of each. Named because
    #: "0 of 44 blocks" and "unreadable" are different statements and only one
    #: of them is about the cipher.
    #: The TWO `.dat` the dictionary cannot touch at all -- not one block of
    #: either is known -- with what is actually true of each. Named because
    #: "0 of 44 blocks" and "unreadable" are different statements and only
    #: one of them is about the cipher:
    #:
    #: **IT WAS FOUR AND IS NOW TWO, 2026-09-07.** `battlepass_level.dat`
    #: (537 B, 44 blocks) and `syndicate_level.dat` (407 B, 33 blocks) were
    #: named here as `tools/datdict.py`'s documented worst case -- "a wholly
    #: new table: low (~0-27%)" landing at exactly 0 -- and under the
    #: 4,059,815-block dictionary both decode ENTIRE, 0 unknown blocks, as
    #: `@@` rows. That was the right call on the evidence available and the
    #: evidence changed; the two names are recorded here rather than deleted
    #: so "a table 7878 never shipped is unreadable forever" cannot be
    #: inferred from their absence.
    #:
    #: The two left are not that case at all. Neither is a new table: both
    #: are files the corpus HAS and cannot open.
    #:
    #: **FOUR became TWO on 2026-09-07.** `battlepass_level.dat` and
    #: `syndicate_level.dat` now decode at 100%: the merged corpus reached
    #: them, which is exactly the outcome their entries predicted ("a wholly
    #: new table, at 0%" is a statement about the CORPUS, not the cipher).
    #: Their reasons are kept below the live two, struck through rather than
    #: deleted, because a prediction that came true is the evidence that the
    #: distinction this dict draws is a real one.
    #: Reproduced by `tests/test_patch7205.py::Block96Census`.
    #:
    #: `levexp.dat` is the interesting one. It is **byte-identical (sha256) to
    #: 7878's**, and `derived/7878-dat-decrypted/levexp.dat.out` is its
    #: plaintext -- 4,030 B, 100% printable. It is absent from the dictionary
    #: by DESIGN (`tools/datdict.EXCLUDE`, and `patch7878.HELD_OUT`): it is the
    #: only seed-1234 table in the corpus and has zero shared-block edges, so
    #: the data cannot decide its key group. Identical ciphertext under one key
    #: is identical plaintext, so the route exists; it is recorded and NOT
    #: wired, because wiring it means teaching the reader a second mechanism
    #: (read a sibling install's derived output when the bytes match) that
    #: nothing else here needs yet.
    #:
    #: Both of the two that remain are structural: one held out by design,
    #: one with no route at all. Neither is a corpus gap that a bigger
    #: dictionary can close, which is what distinguishes them from the pair
    #: that left.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical to 7878's, and its plaintext IS on this "
                      "box: derived/7878-dat-decrypted/levexp.dat.out. Held "
                      "out of the dictionary by design (seed-1234, zero "
                      "shared-block edges, key group unattributable), not "
                      "because it failed. Route recorded, not wired.",
        "ServerPlay.dat": "differs from 7878's, whose own copy is the corpus's "
                          "one genuine residue -- no route opens either. Not "
                          "RSA, not TQ at 9527 or 1234.",
    }

    #: RESOLVED, 2026-09-07 -- kept because the reason they were listed is the
    #: reason they left, and a prediction that came true is worth more on the
    #: page than off it. Both were `tools/datdict.py`'s documented worst case
    #: ("a wholly new table: low (~0-27%)") landing at exactly 0, i.e. a
    #: statement about corpus reach and not about the cipher. The merged
    #: corpus reached them and both now decode at 100%.
    FORMERLY_UNCOVERED = {
        "battlepass_level.dat": "537 B, 44 blocks, 0 known under the "
                                "3,390,890-block dictionary; 100% under "
                                "4,059,815.",
        "syndicate_level.dat": "407 B, 33 blocks, 0 known then; 100% now.",
    }

    #: The cross-build cipher control -- see the header. 6868 is TQ-stream and
    #: independently readable; 7205 is block96; both carry 65-field `itemtype`
    #: rows.
    #:
    #:     py -3 -m unittest tests.test_patch7205.CipherAgreesWith6868
    #:
    #: Compared NUMERICALLY where both sides are numeric. The string form says
    #: 49/65 rather than 51/65, because 6868 writes `01` where 7205 writes `1`
    #: on two columns -- a formatting change that reads as a decode failure if
    #: you compare the bytes.
    #:
    #: **RE-MEASURED 2026-09-07 ON A 26x LARGER SAMPLE, and the two headline
    #: numbers both fell. Read them together with the denominator or they say
    #: the opposite of what happened:**
    #:
    #:     shared ids           1,288 -> 33,638   (3.8% -> 99.997% of 6868's
    #:                                             33,639-row table)
    #:     columns all agree       51 -> 40  of 65
    #:     name column          1,288 -> 33,594 of 33,638  (99.87%)
    #:
    #: 7205's `itemtype.dat` used to yield 2,068 whole rows and now yields
    #: 44,684, so the overlap went from a 1,288-id corner of the table to
    #: essentially all of it. A column has to agree on 26 times as many rows
    #: to still count, so 40 surviving that is STRONGER evidence than 51
    #: surviving the old sample, not weaker -- and the negative control moved
    #: the right way with it: a one-column shift now collapses 40 to 8 (+1)
    #: and 3 (-1).
    #:
    #: The 44 name disagreements were read, all of them. Every one is a
    #: legitimate rename across the 337 patch versions between the builds --
    #: `ChristmasCostume`/`ChristmasGarment`, `P1SomersaultSpirit`/
    #: `P1CloudSpirit`, `EliteGemBag`/`RefinedGemBag` -- and BOTH sides of
    #: all 44 are printable ASCII. That printability is asserted in the test
    #: as the discriminator, because it is what separates "the content
    #: changed" from "the decode is wrong": a bad decode does not produce two
    #: plausible English item names that differ by a marketing edit.
    ITEM_COLUMN_AGREEMENT = {"control_build": "6868", "shared_ids": 33638,
                             "columns": 65, "columns_all_agree": 40,
                             "name_column_agrees": 33594,
                             "name_column_renames": 44}
    #: Compared NUMERICALLY where both sides are numeric. The string form is
    #: the misleading one, because 6868 writes `01` where 7205 writes `1` --
    #: a formatting change that reads as a decode failure if you compare the
    #: bytes.
    #:
    #: **RE-MEASURED 2026-09-07 OVER 26x MORE ROWS, AND THE TWO HEADLINE
    #: NUMBERS BOTH FELL. Neither fall is a decode getting worse.**
    #:
    #:     shared_ids          1,288 -> 33,638   7205 now recovers 44,684
    #:                                           item rows instead of 2,068
    #:     columns_all_agree      51 ->     40   of 65
    #:     name_column_agrees  1,288 -> 33,594   of 33,638
    #:
    #: `columns_all_agree` is a count of columns that agree on EVERY shared
    #: id, so widening the row set from 1,288 to 33,638 can only lower it --
    #: one disagreement anywhere disqualifies a column. Reading 51 -> 40 as a
    #: regression is the trap; it is a stricter test over a 26x larger
    #: sample, and the negative control (shift one column, agreement
    #: collapses) still separates them.
    #:
    #: The 44 name disagreements were INSPECTED rather than assumed, and they
    #: are TQ renaming items between the two builds:
    #:
    #:     184315   ChristmasCostume    -> ChristmasGarment
    #:     3003876  EliteGemBag         -> RefinedGemBag
    #:     3301296  P1SomersaultSpirit  -> P1CloudSpirit   (and P2..P9)
    #:     3301651  NewServerGiftCode   -> COMobileGiftCode
    #:
    #: Nine of the 44 are the one Somersault -> Cloud rename applied across a
    #: numbered family. A decode fault does not produce spelled English words
    #: in the same column on both sides, and 33,594 of 33,638 agreeing
    #: exactly is the evidence that the cipher is being read right.

    #: The compiled twin froze at 6609 and `npc.ini` did not. MEASURED (see
    #: the header for the 6090/6609 rows this sits in):
    #:
    #:     py -3 tests/test_patch7205.py FrozenTables
    NPC_COVERAGE = {"rows": 2913, "resolved_compiled": 2466,
                    "resolved_ini_decoy": 1600, "unresolved": 447,
                    "simpleobj_dbc_rows": 442}

    #: Byte-identical to 6609's, sha256, all twelve. Named individually so the
    #: claim is checkable rather than a sentence.
    FROZEN_COMPILED = ("armet.dbc", "armor.dbc", "weapon.dbc", "mount.dbc",
                       "3DSimpleObj.dbc", "3DObj.dbc", "3DTexture.dbc",
                       "3dmotion.dbc", "3DEffect.dbc", "3DEffectobj.dbc",
                       "mountmotion.dbc", "weaponmotion.dbc")

    #: `tools/clientscripts.py`, run over 6609..7205. Declared as a SURFACE:
    #: no `TableSpec` is offered for a `.lua` file, because a spec is a promise
    #: to parse and this project has no Lua grammar. The readers that do exist
    #: are named in `script_surface()`'s refusal.
    SCRIPT_SURFACE = {"lua": 224, "dll": 37, "lua_bytes": 19094816,
                      "exported_globals": 1106,
                      "at_6609": {"lua": 5, "dll": 26}}

    def script_surface(self, root) -> dict:
        """Re-measure the Lua/DLL surface off the install, with its refusal.

        Counts files; opens none. The `declared` half is
        `SCRIPT_SURFACE`, so a caller can see drift rather than trusting a
        constant, and `reader` names where an actual answer lives instead of
        this returning a number that looks like content.
        """
        root = Path(root)
        lua = sum(1 for _ in root.rglob("*.lua")) if root.is_dir() else 0
        dll = len(list(root.glob("*.dll"))) if root.is_dir() else 0
        return {
            "measured": {"lua": lua, "dll": dll},
            "declared": self.SCRIPT_SURFACE,
            "reader": ("tools/clientscripts.py and tools/scriptreport.py "
                       "read this surface; no TableSpec is offered for a "
                       ".lua file because this project has no Lua grammar "
                       "and a spec is a promise to parse."),
        }

    def block96_census(self, root) -> dict:
        """Re-measure the whole `ini/**.dat` block96 surface, or refuse.

        The refusal names the dictionary rather than the client: a box without
        `derived/7878-dat-decrypted/` cannot answer this, and that is a fact
        about the box.
        """
        try:
            from core import block96, inidat             # noqa: PLC0415
        except ImportError:                              # pragma: no cover
            import block96, inidat                       # noqa: PLC0415
        # **ROOTED AT THE INSTALL.** `dictionary_path(None)` can only answer
        # from `$CO_BLOCK96_DICT`; the on-disk dictionary is found by the
        # relationship `Clients/<build>` -> `../../derived/...`, which needs
        # a root. Un-rooted, this method returned the refusal below on a box
        # that HAS the dictionary, and `test_the_census_reproduces` never saw
        # it because its `need_dict()` guard asked the same broken question.
        table = block96.load_dictionary(root)
        if table is None:
            return {"refusal": block96.why_no_dictionary(root)}
        ini = Path(root) / "ini"
        if not ini.is_dir():
            return {"refusal": f"{ini} is not a directory"}
        out = {"files": 0, "blocks": 0, "unknown": 0,
               "fully_covered": 0, "partial": 0, "uncovered": 0}
        for p in sorted(ini.rglob("*")):
            if not p.is_file() or p.suffix.lower() != ".dat":
                continue
            raw = p.read_bytes()
            if inidat.classify(raw, p.name).family != "block96":
                continue
            _pt, total, unk = block96.decode(raw, table)
            if not total:
                continue
            out["files"] += 1
            out["blocks"] += total
            out["unknown"] += unk
            key = ("fully_covered" if unk == 0
                   else "uncovered" if unk == total else "partial")
            out[key] += 1
        return out

    def table_quirks(self):
        """6609's quirks all apply to the ART -- the archives and compiled
        tables are byte-identical -- so they are inherited. These are 7205's
        own, and every one of them is about the CONTENT layer 6609 has no
        knowledge of."""
        q = super().table_quirks()
        q["ini/*.dat is the 6907-7878 block cipher, not TQ"] = (
            "101 of the 146 ini/*.dat files classify block96 -- the 12-byte "
            "ECB cipher whose key is shared by every build from 6907 to 7878 "
            "and whose algorithm is unrecovered. core/tqdat.py must not be "
            "pointed at them: pre-change, patch6090's inherited tq-stream "
            "specs reported 394,269 item rows on a table with about 39,771, "
            "and only the control refused. core/block96.py reads them "
            "through the dictionary tools/datdict.py builds.")
        q["block coverage is not record coverage"] = (
            "86.1% of itemtype.dat's blocks are known and 5.2% of its rows "
            "come out whole, because a ~324-byte row spans ~27 blocks and "
            "needs all of them. 2,068 rows of at least 39,771. Every count "
            "this plugin serves for a block96 table is a FLOOR; "
            "BLOCK96_COVERAGE carries both numbers so neither can be read "
            "as the other.")
        q["a dropped section is not a dropped line"] = (
            "For @@ row tables a damaged line is dropped and the rest are "
            "sound. For [section] tables that is wrong twice over: a damaged "
            "key leaves a section short and silent, and a damaged HEADER "
            "donates every key below it to the section above. "
            "core/block96.clean_sections discards whole sections, which is "
            "why monster.dat yields 558 and not 85,219.")
        q["the compiled tables froze at 6609"] = (
            "All twelve .dbc are byte-identical (sha256) to 6609's while "
            "npc.ini grew from 2,784 to 2,913 rows. The frozen "
            "3DSimpleObj.dbc resolves 2,466 of them (84.7%) against 96.0% at "
            "6609, and 447 rows name a simple object it has never heard of. "
            "It is still preferred over the 2008 ini/3DSimpleObj.ini decoy, "
            "which resolves 54.9% -- 30 points worse. Nothing raises for an "
            "unresolved row; it looks exactly like an npc with no art.")
        q["a re-decode control cannot refuse a wrong cipher"] = (
            "Pre-change, patch6090 on this install reported `mount` as "
            "ok=True with 1 row and a POSITIVE control, from the right file "
            "(ini/mounttype.dat) under the wrong cipher -- the control text "
            "was a section header of binary noise, 'both found in an "
            "independent re-decode'. CONTROL_REDECODE witnesses the parse, "
            "and a wrong cipher applied twice agrees with itself. This "
            "plugin's block96 subjects carry the same control kind and the "
            "same limit; the cipher-level evidence is the cross-build "
            "agreement with 6868 in ITEM_COLUMN_AGREEMENT, which does not "
            "come from our dictionary at all.")
        q["224 Lua scripts and no Lua grammar"] = (
            "The script surface goes 5 files at 6609 to 224 here, with DLLs "
            "26 -> 37 and 1,106 exported globals. It is declared as a "
            "surface and not as tables: tools/clientscripts.py and "
            "tools/scriptreport.py read it, and offering a TableSpec for a "
            ".lua file would be a promise to parse that nothing here keeps.")
        return q

    def colour_provenance(self):
        """One patch further from the afternoon than 6609 is.

        `Patch6090.colour_provenance` returns "authored", meaning a person
        eyeballing 36 monster directories on THAT base. 6609 already
        downgraded it to "inherited from 6090 (unverified here)". 7205 ships
        c3.wdf and data.wdf byte-identical to both, so the sets are inherited
        rather than dropped -- an inherited set that resolves beats no answer
        -- but nobody has looked at this client on screen either, and saying
        "inherited from 6090" without naming the hop through 6609 would hide
        which of the three bases the evidence actually came from.
        """
        return ("colourway inherited from 6090 via 6609 (unverified on "
                "either)", "inferred")


PLUGIN = Patch7205()
