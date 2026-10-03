#!/usr/bin/env python3
r"""
patch7589 -- official Conquer Online patch 7589: 7205's content layer inside
a `.tpd`-only install with no compiled tables, whose base archive INDEX is
byte-identical to 7878's.

7589 is in the `.tpd`-only era (7280 ... 7878). **The archive switch is at
7275 -> 7280**: 7275 is the last client that ships `c3.wdf` / `data.wdf` and
twelve `ini/*.dbc`; 7280 is the first that ships only `.tpd`/`.tpi` pairs
and **zero** `.dbc`. The `.dbc` set goes from 12 to 0 at that same step
(wave A's listings, `director/comod-plugin-7280` 8d610c4a). 7589 is on the
`.tpd` side. The listing, read 2026-09-19 (a directory listing and small
file reads only; no `.tpd` was opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7562   7562          0      2/2           0         534           165
    7589   7589          0      2/2           0         539           165
    7632   7622 (!)      0      2/2           0         543           167
    7878   7878          0      4/4           0         629           200

    7589/c3.tpd    1,076,058,840 B    7589/c3.tpi    3,306,363 B
    7589/data.tpd    896,279,080 B    7589/data.tpi  5,724,770 B

(The `Clients/7632` folder's `version.dat` reads `7622`. Not this plugin's
install; recorded because it is the neighbour.)

THE BASE ARCHIVE DID NOT CHANGE BETWEEN 7280 AND 7878
------------------------------------------------------
**MEASURED (sha256 of the two `.tpi` index files):** `c3.tpi`
(`4b4bde688f18`) and `data.tpi` (`9096a9e0ed08`) are byte-identical on
7280, 7336, 7562, 7589, 7632 and 7878. The `.tpd` payloads have identical
SIZES on all six, and were NOT hashed (1.9 GB per install; that is in
`measure_7589.py`, sampled head+tail). So the patches after 7280 changed
LOOSE files (`ini/`, scripts) and, by 7878, added OVERLAY pairs (`c31.tpd`,
`data1.tpd`), not the base archive. 7589 ships no overlay pair. An asset
read from `c3`/`data` on 7589 therefore resolves through the same index as
7878's; what differs between the two is the loose `ini/` layer.

WHY THE BASE IS `Patch7205`, AND WHAT IS OVERRIDDEN
----------------------------------------------------
Wave A (7280 / 7320 / 7336) settled this; the reasons were re-checked on
7589's own files, not copied:

  * **`Patch7878` must not be the base, because of its content layer.**
    `patch7878.derived_tables(root)` finds `derived/7878-dat-decrypted` for
    ANY root under `Clients/`, so a subclass would serve **7878's** decrypted
    tables as 7589's. On 7589 that would be wrong in a way no control
    catches: `itemtype.dat` here is 14,970,839 B (sha `aaf676c958fe`), 7878's
    is 16,907,304 B (`1404bac7c3f5`). The index sharing above makes this
    MORE tempting, not less, and it is still the wrong answer.
  * **`Patch7205`** fits the content layer: its `ini/*.dat` specs use the
    `block96` codec through the collection's block dictionary, decoding this
    client's own ciphertext. MEASURED by listing: every file named by
    `SPECS_7205` (23) and `SPECS_7205_CENSUSED` (85) is on disk here, and all
    13 `Patch7205.UNDECLARED_DAT` names are too.

The art-layer hooks that name `.dbc` files are overridden, because this
install ships none:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
    names `3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc`, `3dmotion.dbc`;
    none is on disk (`tests/test_patch7589.TheArtProfileNamesFilesThatExist`
    is the arm that fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False. There is no twin to prefer.
  * `import_plan()` names the `.tpd` pair and drops 6090's "shared archives
    skipped, compiled .dbc tables read" note, false here on both counts.

What is KEPT from the 6090 lineage, and on what evidence:

  * **Four-wide `Action3DEffect` action field**: MEASURED on 7589. 3,238
    numeric keys; shapes (3,4,3,3) x2,094, (4,4,3,3) x907, (1,4,3,1) x204,
    (3,4,1,3) x24. Every dominant shape has the action field 4 wide.
  * **`aura_convention() == "table"`**: MEASURED. 2,789 of 3,238 keys carry
    action `9999`. POSITIVE CONTROL: the same count on 7205 gives 1,900 of
    2,328, 7205's recorded figure.
  * **`ROW_COLUMNS`**: RE-MEASURED. `region.ini` has 304 rows, 14 fields,
    column 6 non-numeric on 304/304 (85 distinct); it is byte-identical to
    7562's and 7632's. `EventTypeName.ini`, `VipTrans.ini` and `restrain.ini`
    are byte-identical to 7205's (sha256).
  * `texture_for_mesh`, colour sets, sockets, `socket_correction`: INHERITED
    AND NOT MEASURED HERE (6090 measurements, gated on `exists()`).

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 files, with nothing behind them.**
`3DSimpleObj.ini f34f56332383`, `3dobj.ini 9202de93aa03`, `3dtexture.ini
6be2fece83fd`, `3dmotion.ini 9951727aa1ed`, `armor.ini 8e64691e0f00`:
identical to 7205's, 7562's, 7632's and 7878's. `npc.ini` kept growing
(725,040 B, a different file from 7562's and 7632's):

    build  npc.ini SimpleObjID rows  resolve via 3DSimpleObj.ini  unresolved
    7205         2,913                    1,600  (54.9%)            1,313
    7589         3,306                    1,843  (55.7%)            1,463

POSITIVE CONTROL: the same code reproduces
`patch7205.NPC_COVERAGE["resolved_ini_decoy"]`, 1,600, exactly. On 7589 it
is the only answer: 1,463 npc rows name a simple object the frozen table
does not carry, and an unresolved row looks exactly like an npc with no art.

**Tables, by hash against the neighbours:**

    ini/itemtype.dat      14,970,839 B  aaf676c958fe  == 7562 == 7632; != 7205, 7878
    ini/ItemtypeSub.dat      407,962 B  285411d9fab8  7562 172,304 B, 7632 645,463 B,
                                                      7878 464,778 B, 7205 207,838 B
    ini/levexp.dat             4,030 B  4482cfaaec7d  == 7205 == 7878 (held out by design)
    ini/ServerPlay.dat         6,144 B  ab999e35070d  a different file from every neighbour
    ini/SlotNpc.ini            3,959 B  c4e23de07332  == 7205 == 7878

`ItemtypeSub.dat` is not monotonic across the neighbours (172 KB -> 408 KB ->
645 KB -> 465 KB). Until it is decoded, an `item:sub` count on 7589 is not
comparable with 7205's 633.

**`SlotNpc.ini` is the SAME BYTES that measured GBK on 7878.** So the
inherited `TEXT_ENCODING = "latin1"` is applied here to a file whose
encoding was measured as something else. This plugin does not change the
declaration (7205 ships the same bytes under the same declaration, and one
file is not the whole `ini/`); `measure_7589.py` section 4b sweeps every text
`.ini` for strict-GBK decodability so the Director can decide.

`codepage.ini` reads `1256` (identical to 7205's and 7878's).

**`ini/` against 7205 and the neighbours, by name:**

    gone since 7205   the 12 .dbc; action3deffect1..5.ini, emoneyshop.ini,
                      strres.ini
    new since 7205    18 .dat (`NEW_DAT_SINCE_7205`, UNDECLARED -- not yet
                      decoded); 6 .ini (actionrole3deffect, actionsoundex,
                      appqdkey, cq_remote_prize_cfg, operateweben,
                      shaderparam); 58 .lua and the luagui/ luaui/ dirs
    since 7562        +5 .lua only (analytic, chatskin, newtexas, operateweb,
                      pctocopface); nothing removed
    in 7632, not here casual_game_cfg.ini, gouyu_xuzuo_type.dat,
                      rune_effect.dat, texasraffle.lua

**Script surface**: 446 `.lua` (17,235,315 B) and 28 root DLLs, against
7205's 224 (19,094,816 B) and 37. The same code reproduces 7205's recorded
figures exactly (the control). Exported globals were NOT counted.

WHAT IS INFERRED
----------------
  * That the `.tpd` payloads are identical to 7878's: identical index, same
    size and identical sampled head/tail make it likely; it is NOT claimed
    (`sharedArchives` stays False) without a full hash.

WHAT measure_7589.py MEASURED (run by the Director, 2026-09-19, head 6cdf915c)
------------------------------------------------------------------------------
POSITIVE CONTROLS PASSED first: on 7205, `Patch7205.confidence` = 0.95, and
block96 coverage reproduces 7205's recorded figures exactly for
itemtype/monster/magictype/itemtypesub/mounttype (all 100%).

**Detection over every install (48 roots incl. CCO live):** the ONLY winner
that changes is 7589 (plaintext 0.50 -> patch7589 0.95, margin 0.45 over
plaintext). patch7589 scores 0.0 on every other install. Not this plugin's
to fix, but visible in the table: every `.tpd`-era install without its own
plugin (7280 ... 7562, 7632, 7682, 7867) detects as `plaintext` at 0.50,
and 7217 / 7250 / 7275 as `patch6907` at 0.55.

**Containers:** 7589 `c3.tpi` and `data.tpi` IDENTICAL (full hash) to
7280's, 7562's, 7632's and 7878's. `c3.tpd` / `data.tpd` EQUAL to all four by
size + head/tail 16 MB (sampled, NOT a full hash). 0 `.dbc`.

**Appearance tables (`part_tables()`), 7589 vs 7205:** armet 1,168 (7205
2,793 from armet.dbc), body 955 (3,754), l/r_weapon 4,828 (13,292), **mount
1 (7205: 1,223 from Mount.dbc)**, head 0, misc 0. On 7589 every slot reads
the frozen `.ini` (no twin), so these are the 2008 decoys' row counts. The
mount slot is effectively empty: a KNOWN LIMIT of this install, not a parser
defect -- the compiled table that carried it is gone.

**`ini/**/*.dat` families on 7589 (recursive, 458 files):** block96 402,
rar-mangled 11, empty 10, rsa-mysqldump 10, plaintext 7, binary-plain 6,
unknown 5, tq-stream 3, block96-candidate-refuted 3, shift-obfuscated 1. No
declared spec's codec disagrees with `inidat.classify` (0 CODEC MISMATCH).

**block96 on 7589:** 2,293,701 blocks, 38,091 unknown (98.34% known); 352
files fully covered, 48 partial, 2 at zero (`levexp.dat`, byte-identical to
7205's and held out by design; `ServerPlay.dat`, 6,144 B, undeclared).
98.34% depends on 7622's corpus: 93.43% without it (shared key) --
`py -3 tools/loo_coverage.py 7622 --control 7589`, measured 2026-09-19. A
block learned from any build under the one key is true for all of them, so
the 98.34% is genuine, but it is not independent of `derived/7622-dat-decrypted`.

Declared tables, 7589:

    itemtype.dat     100.00%  50,834 whole rows   (7205: 44,684)
    mounttype.dat    100.00%   1,888 sections     (7205: 1,819)
    MagicType.dat     99.10%   4,179 of 4,306 rows
    MapDestination    96.50%   1,103 sections
    monster.dat       96.08%   2,507 whole sections, 113,989 of 117,425
                               lines intact  (7205: 100%, 3,709)
    ItemtypeSub.dat   87.02%     103 of 835 rows  (7205: 633)
    magictypeop.dat   92.74%     358 of 529 rows
    instancetype.dat  59.76%      47 of 245 rows
    prof_lev_up.dat   68.13%     199 of 284 rows
    hairface_storage  66.39%      86 of 367 rows
    texas_match_type  65.17%       1 of 35 rows
    AutoUseMagic.dat  64.98%      10 sections

**KNOWN LIMITS (FAILs of the dictionary, not tuned away):** the 7878-derived
dictionary does not know 1.66% of 7589's blocks, and whole-record filtering
turns that into large row losses on some tables: `monster` 2,270 catalog
rows against 7205's 3,670, `item:sub` 103 against 633, `magic:op` 358
against 979, `instancetype` 47 against 340, `prof_lev_up` 199 against 549,
`texas_match_type` 1 against 82. These counts are what this build READS,
not what it ships. Closing them needs the `ndac.dll` oracle.

**`catalogs()`:** 240 subjects on 7589, 238 with a control (7205: 247 /
245). The two without are `action` (binary layout not established) and
`levexp` (held out), both refusals, as on 7205. Headline rows, 7589 (7205):
item 50,834 (44,684), magic 4,179 (2,907), mount 1,888 (1,819), map:dest
1,068 (936), npc:npc.ini 3,303 (2,904), monster 2,270 (3,670), item:sub 103
(633).

**Encoding of `ini/*.ini` on 7589:** 110 pure ASCII, 16 strict UTF-8, 78
strict GBK (incl. 3DEffect.ini, 3dtexture.ini, Action3DEffect.ini ...), 3
not GBK (ChatFilter.ini, Eliteanpt.ini, UStrRes.ini). CONTROL: 7878's
`SlotNpc.ini` (the same bytes as 7589's) decodes as GBK. So the inherited
`TEXT_ENCODING = "latin1"` is contradicted for most non-ASCII `.ini` here.
The declaration is NOT changed by this plugin -- it is 7205's, 7878 shares
the question, and it is a collection-wide decision -- but it is recorded as
MEASURED-WRONG, not as unmeasured.

**Census:** `DAT_CENSUS_ERA = False`, and the `7589` slot in
`tools/dat_census.py` stays commented. The `.dat` files ARE the 96-bit block
cipher (402 of 458, 98.34% of blocks known), but adding 7589 to `BUILDS`
turns `tests/test_dat_census` red: its per-build facts pin
`combat_gear.dat` to 7189 alone, and 7589 ships it too
(`TheVerdictIsPerBuild.test_a_file_on_one_build_is_reported_on_one_build`:
`['7189', '7589'] != ['7189']`, run 2026-09-19 08:08Z). Those expectations
are not this plugin's to edit, so 7589 stays out of the census until they
are re-scoped. The 7336 plugin chose the same way. The figures are kept as
`DAT_MEASURED_7589` (over `ini/**` recursively, a different scope from
7205's 101-file dict). `BLOCK96_CENSUS` is set to None on this class, so
7205's inherited dict is never presented as 7589's (SD, W3 review); read
`DAT_MEASURED_7589` for 7589.

STILL NOT MEASURED
------------------
`ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`, `BROWSE_IMPACT`,
`RUNEEFFECT_REPEATS`, `FORMERLY_UNCOVERED`, per-table block counts (the
script printed %, not block totals, so `BLOCK96_COVERAGE` stays None), and
full hashes of the `.tpd` payloads.

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
RESULT on 7589: **0 strict-GBK failures** in 351 subjects/name
sources (2 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  2293    10    10     0      0     0    0
    Name=:npc:NpcX.ini              984     7     7     0      0     0    0
    Name=:npc:npc.ini              3306    28     7    21      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      165    67    67     0      0     0    0
    Name=:statustips                268     6     6     0      0     0    0
    achievement                     388     2     0     0      0     2    0
    classdesc                       123    25    25     0      0     0    0
    item                          50832     2     0     1      0     1    0
    map:dest                       1068     7     6     0      0     1    0
    npc:NpcX.ini                    984     7     7     0      0     0    0
    npc:npc.ini                    3282     7     7     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            165    67    67     0      0     0    0
    statustips                      268     6     6     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 207: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 895: `a1 a1` = '\u3000'

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
STAMP = "7589"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded: most name a `.dbc` (this install ships none), the last carries
#: 7205's script counts.
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
    """`version.dat`, `b'7589'` on this install (read 2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7589(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [2293, 10, 10, 0],
        'Name=:npc:NpcX.ini': [984, 7, 7, 0],
        'Name=:npc:npc.ini': [3306, 28, 7, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [165, 67, 67, 0],
        'Name=:statustips': [268, 6, 6, 0],
        'achievement': [388, 2, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'item': [50832, 2, 0, 0],
        'map:dest': [1068, 7, 6, 0],
        'npc:NpcX.ini': [984, 7, 7, 0],
        'npc:npc.ini': [3282, 7, 7, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [165, 67, 67, 0],
        'statustips': [268, 6, 6, 0],
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

    name = "patch7589"
    label = "Official patch client 7589"
    origin = "official"
    aliases = ("7589",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf, no overlay pair) "
        "with ZERO compiled .dbc tables; the archive switch and the 12 -> 0 "
        ".dbc drop are both at 7275 -> 7280. Its c3.tpi and data.tpi are "
        "byte-identical to 7280's and 7878's: later patches changed loose "
        "files and added overlays, not the base archive. Content tables are "
        "7205's specs over ini/*.dat in the 96-bit block cipher. The art "
        "lookups are the frozen 2008 plaintext files with no compiled twin: "
        "1,843 of 3,306 npc.ini rows resolve a simple object and 1,463 do "
        "not. The block dictionary knows 98.34% of ini/*.dat blocks; itemtype "
        "reads whole (50,834 rows) but monster reads 2,270 whole records "
        "(7205: 3,670) and item:sub 103 (633) -- known limits. Most "
        "non-ASCII ini/*.ini decode as GBK; TEXT_ENCODING is gbk, measured "
        "per table on the name fields (0 strict failures)."
    )

    # -- what the listing measured -----------------------------------------
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: sha256 prefixes of the two indices. MEASURED identical on 7280, 7336,
    #: 7562, 7589, 7632 and 7878. The `.tpd` payloads are NOT hashed.
    ARCHIVE_INDEX_SHA = {"c3.tpi": "4b4bde688f18",
                         "data.tpi": "9096a9e0ed08"}
    INDEX_IDENTICAL_TO = ("7280", "7336", "7562", "7632", "7878")

    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: 0 `ini/*.dbc`.
    FROZEN_COMPILED = ()

    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: `SimpleObjID=` rows in `npc.ini` against `[ObjIDType<N>]` in the frozen
    #: `3DSimpleObj.ini`. The instrument reproduces 7205's 1,600 exactly.
    NPC_COVERAGE = {"rows": 3306, "sections": 3303, "resolved_ini": 1843,
                    "unresolved": 1463, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    SCRIPT_SURFACE = {"lua": 446, "dll": 28, "lua_bytes": 17235315,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: sha256 prefixes of tables compared by identity with the neighbours.
    TABLE_IDENTITY = {
        "ini/itemtype.dat": ("aaf676c958fe", "== 7562, 7632; != 7205, 7878"),
        "ini/ItemtypeSub.dat": ("285411d9fab8", "differs from every neighbour"),
        "ini/levexp.dat": ("4482cfaaec7d", "== 7205, 7878"),
        "ini/ServerPlay.dat": ("ab999e35070d", "differs from every neighbour"),
        "ini/SlotNpc.ini": ("c4e23de07332", "== 7205, 7878 (GBK on 7878)"),
    }

    #: `.dat` in 7589's `ini/` that 7205 does not ship. UNDECLARED: none has
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

    ITEMTYPESUB_BYTES = 407962

    # -- 7205 measurements NOT re-taken on this build ---------------------
    BLOCK96_COVERAGE = None
    #: False, deliberately: the ini/*.dat ARE block96 (402 of 458, 98.34% of
    #: blocks known), but joining `dat_census.BUILDS` reds
    #: `tests/test_dat_census` (combat_gear.dat is pinned to 7189 alone and
    #: 7589 ships it). The literal False: the test uses `is`.
    DAT_CENSUS_ERA = False
    #: NOT 7205's census. `BLOCK96_CENSUS` on Patch7205 is 7205's own
    #: measured census dict; inherited unchanged it would present 7205's
    #: numbers as 7589's to any generic reader (SD, W3 review). None =
    #: no census in 7205's shape is recorded for this class (7589's own, subfolders included, is `DAT_MEASURED_7589`).
    BLOCK96_CENSUS = None
    #: measure_7589.py section 4, over ini/** RECURSIVELY -- NOT the scope of
    #: 7205's `BLOCK96_CENSUS`, hence a different name.
    DAT_MEASURED_7589 = {"files": 402, "blocks": 2293701, "unknown": 38091,
                         "fully_covered": 352, "partial": 48, "uncovered": 2}
    #: Whole rows/sections read on 7589, and 7205's, for the tables the
    #: dictionary only partly knows. Known limits, not targets.
    KNOWN_LIMITS = {
        "monster": {"rows": 2270, "at_7205": 3670, "blocks_known": 0.9608},
        "item:sub": {"rows": 103, "at_7205": 633, "blocks_known": 0.8702},
        "magic:op": {"rows": 358, "at_7205": 979, "blocks_known": 0.9274},
        "instancetype": {"rows": 47, "at_7205": 340,
                         "blocks_known": 0.5976},
        "prof_lev_up": {"rows": 199, "at_7205": 549,
                        "blocks_known": 0.6813},
        "texas_match_type": {"rows": 1, "at_7205": 82,
                             "blocks_known": 0.6517},
    }
    #: `part_tables()` on 7589: the frozen .ini rows, no twin.
    PART_TABLE_ROWS = {"armet": 1168, "body": 955, "l_weapon": 4828,
                       "r_weapon": 4828, "mount": 1, "head": 0, "misc": 0}
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
        k: "[measured on 7205, NOT re-taken on 7589] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7589` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. Nothing but the stamp separates
        7589 from 7562 / 7632: the same `.tpi` bytes, no `.dbc`, the same
        frozen lookups, even the same `itemtype.dat`. `measure_7589.py`
        prints the real ranking with and without this plugin.
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
        """The `.tpd` pair. `sharedArchives` stays False: the indices are
        byte-identical to 7878's, the payloads are equal by SIZE only."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": ["c3.tpd", "data.tpd"],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: .tpd archives (no .wdf, no overlay "
                     f"pair; c3.tpi/data.tpi identical to 7878's), no "
                     f"compiled .dbc -- the plaintext lookup chain is the "
                     f"only one, and ini/*.dat is block96"),
        })
        return plan

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7589's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7589's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7589] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7589: Action3DEffect keys are (3,4,3,3) x2,094, "
            "(4,4,3,3) x907, (1,4,3,1) x204 -- the action field is four wide "
            "as on 6090 and 7205, not three as in the plaintext family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,306 SimpleObjID rows; 1,843 resolve and 1,463 do not. Nothing "
            "raises for an unresolved row.")
        q[".tpd archives, and the base index never changed"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. c3.tpi and data.tpi are "
            "byte-identical on 7280..7878; later patches changed loose files "
            "and added overlay pairs (7878's c31/data1), not the base.")
        q["446 Lua scripts and no Lua grammar"] = (
            "446 .lua (17,235,315 B) and 28 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables.")
        q["ItemtypeSub.dat is 407,962 bytes"] = (
            "7562 ships 172,304 B and 7632 645,463 B. Not yet decoded; an "
            "item:sub count here is not comparable to 7205's 633 until it is.")
        q["SlotNpc.ini is 7878's GBK bytes"] = (
            "byte-identical to 7878's SlotNpc.ini, which measured GBK; this "
            "build's TEXT_ENCODING is gbk, measured per table 2026-09-19.")
        return q


PLUGIN = Patch7589()
