#!/usr/bin/env python3
r"""
patch7867 -- official Conquer Online patch 7867: the first client that ships
the `c31`/`data1` overlay pairs, eleven builds before 7878, and whose two
biggest content tables are ALREADY 7878's bytes.

STATUS: FINALIZED 2026-09-19. The first half was written from repository
files and `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` while
`Clients/` was off-limits; words like PENDING there are resolved in "WHAT
measure_7867.py MEASURED" below, which is the authority.

WHAT IS MEASURED (fingerprints.json, blake2b-128, measured before the owner's
2026-09-19 deletions; the 7867 entry is unaffected by them)
-------------------------------------------------------------------------------
`version.dat` = `7867` (4 B), build digest `e6684b92543fd24d`. Against the
`.tpd`-era neighbours (`7632` there is today's `7622` install):

    file              7589          7632(=7622)   7867          7878
    c3.tpi            c2b98d44      c2b98d44      c2b98d44      c2b98d44
    data.tpi          00964835      00964835      00964835      00964835
    c31.tpi  (5,697)  -             -             e9099fb3      e9099fb3
    data1.tpi  (118)  -             -             5c10f04c      5c10f04c
    ini/itemtype.dat  d51785ea      d51785ea      03813c76 16.9MB  03813c76
    ini/MagicType.dat 24fcbb50      04dc5461      86767d79      86767d79
    ini/mounttype.dat 623d70c2      1e36f8ab      33a0829d      e49b4275
    ini/monster.dat   f60ca38a      91f77bde      4069e29e      (Monster.dat
                                                                 f9b3d02f)
    ini/GameMap.dat   d0170d51      c698fefd      eda97cac      636fa412
    ini/3dobj.ini     a37b352a      a37b352a      a37b352a      a37b352a
    ini/3dtexture.ini 1e639c2e      1e639c2e      1e639c2e      1e639c2e
    ini/3DObj.dbc     absent        absent        absent        absent
    play.exe          c894c588      55ec7d23      b6a7a13e      b6a7a13e
    Server.dat        e28cc0c0      04d6641a      60c7c0c0      dc0547c8

Payload sizes (not hashed): `c3.tpd` 1,076,058,840 B, `data.tpd` 896,279,080
B, `c31.tpd` 1,156,978 B, `data1.tpd` 145,649 B -- equal to 7878's on all
four.

What that says, and what it does NOT:

  * **The base archive did not change** (7589's plugin found `c3.tpi` /
    `data.tpi` byte-identical from 7280 to 7878; 7867 is in that set).
  * **7867 is the first client in the fingerprints with the overlay pairs**
    `c31` and `data1`, and they are byte-identical (index) to 7878's.
    7280, 7320, 7589 and 7632 ship none (their plugins' listings).
  * **`itemtype.dat` and `MagicType.dat` are 7878's files, byte for byte.**
    The block dictionary is built from 7878's decrypted corpus, so on these
    two tables 100% coverage is the dictionary QUOTING ITSELF and is not
    evidence the dictionary knows 7867. `mounttype.dat`, `monster.dat` and
    `GameMap.dat` DIFFER from 7878's and are the only independent tests of
    the dictionary on this install. `measure_7867.py` reports the two
    groups separately.
  * `monster.dat` is LOWER-case here and `Monster.dat` on 7878 (the 6609
    case flip again); `Plugin._find_table` matches case-insensitively.
  * The art lookups `3dobj.ini` / `3dtexture.ini` are the frozen 2008 files
    (same digest as 7589's, which 7589's plugin sha256-matched to 7205's and
    7878's). `3DSimpleObj.ini` and `3dmotion.ini` are NOT in the fingerprints:
    PENDING.

MEASURED ELSEWHERE IN THE REPO, cited not re-derived
----------------------------------------------------
  * `docs/capability_matrix_2026-09-06.md`: 7867/7878 are "tpd x4 pairs",
    `.dbc` = **0**, `.dat` 200-203, `.ini` 216-218, `.lua` 198 (top of
    `ini/`); 146 `.fui` (FGUI) and 394 Spine skeletons arrive at 7867/7878;
    **`ndac.dll` ships only on 7867 and 7878** -- the oracle that decrypted
    7878's tables is on this install. It is NOT run by this plugin or its
    measure script.
  * `tests/test_patch7205.CoverageArithmetic` pins **`ini/ft/itemtype.dat`**,
    a regional variant 7867 ships beside its own: 1,399,730 blocks, 180,973
    unknown (87.1% of blocks) and 16.6% of whole rows. So `ini/` has at
    least one subfolder of tables; the inherited specs read `ini/<name>`
    only and serve the top-level file.
  * `tests/test_asset_profile_hurdle.py` / `core/coassets.py`: 7867 is one of
    the three installs (with 7632 and 7682) that sat at a **13%
    appearance-resolution rate** bare and QUALIFY for the 7878
    `AssetProfile`. KNOWN LIMIT of the bare install; the profile is an
    AssetRoot decision, not this plugin's. `measure_7867.py` prints the
    verdict, bare rate and profiled rate.

THE PARENT IS `Patch7205`, NOT `Patch7878` AND NOT `Patch7589`
---------------------------------------------------------------
  * **Not `Patch7878`.** `patch7878.derived_tables(root)` finds
    `derived/7878-dat-decrypted` for ANY root under `Clients/`, so a subclass
    would serve 7878's decrypted tables as 7867's. For `itemtype` and
    `MagicType` that would even be the right bytes -- which is exactly what
    makes it dangerous: `mounttype`, `monster` and `GameMap` differ, and the
    same mechanism would serve 7878's rows for those with no control able to
    tell. The block96 route decodes THIS install's ciphertext.
  * **Not `Patch7589`.** It is the nearest sibling in shape (the same
    plaintext-profile overrides) but it carries ~20 class attributes that
    are 7589's measurements (`NPC_COVERAGE`, `KNOWN_LIMITS`,
    `PART_TABLE_ROWS`, `DAT_MEASURED_7589`, `TABLE_IDENTITY` ...). Inherited,
    each would present a 7589 number as 7867's. The three art-layer overrides
    are small, so they are restated here instead.
  * **`Patch7205`** fits the content layer: `SPECS_7205` /
    `SPECS_7205_CENSUSED` use the `block96` codec over this client's own
    `ini/*.dat` through the collection's dictionary. PENDING: that every
    declared file ships here and classifies `block96` (measure section 4).

WHAT IS OVERRIDDEN, AND WHY
---------------------------
  * `confidence()`: the exact `version.dat` stamp `7867` at 0.95, else 0.0.
    Today 7867 detects as `plaintext` at 0.50 (recorded in the patch7589 and
    patch7562 docstrings' every-install tables).
  * `table_profile()` -> `PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL` names four
    `.dbc`; the capability matrix measures 0 `.dbc` on 7867 and the
    fingerprints measure `ini/3DObj.dbc` absent.
  * `prefers_compiled_tables()` -> False: there is no twin.
  * `import_plan()` names all FOUR `.tpi` indices, base pair first, then the
    overlays. Without the overlays an import would miss the 7867-era patch
    content (on 7878, `c31` holds 82 entries and `data1` 1).
  * Every 7205-measured class attribute that would otherwise be read as
    7867's is set to None (or relabelled), per the patch7589 precedent.
  * `DAT_CENSUS_ERA = False` UNTIL MEASURED. The literal the census test
    reads. It becomes True only if measure section 4 shows the `.dat` are
    block96 AND joining `tools/dat_census.BUILDS` does not red
    `tests/test_dat_census` (7589 could not join: `combat_gear.dat` is
    pinned to 7189 alone).
  * `TEXT_ENCODING` = "gbk" (MEASURED; see below). Code-phase text: The owner has
    ruled the 7205 lineage GBK; this plugin switches to "gbk" only if
    measure section 4c's POSITIVE test passes: a strict GBK decode of the
    NAME fields (itemtype col 1, MagicType col 3, monster `Name=`) with a
    count of high-byte fields, zero failures, CJK code points in >= 90% of
    them, in at least two tables -- and 7205 passing the same test as the
    control. `plugins/__init__.py` says why "it decoded" is not evidence.
    Note itemtype/MagicType are 7878's bytes here, so `monster.dat` is the
    one independent table in that test.

WHAT measure_7867.py MEASURED (Director's run, 2026-09-19, head cad2a048)
-------------------------------------------------------------------------
POSITIVE CONTROLS PASSED first, on 7205: `Patch7205.confidence` 0.95;
block96 coverage reproduces `Patch7205.BLOCK96_COVERAGE` exactly on all five
tables (itemtype 1,080,527 blocks / 44,684 rows ...); the npc shortfall
reproduces 7205's recorded 2,913 / 1,600.

**Detection (46 roots incl. CCO live):** the ONLY winner that changes is
7867 (`plaintext` 0.50 -> `patch7867` 0.95, margin 0.45). patch7867 scores
0.0 on every other install. **No lost subjects (FINALIZE-C 5b):** the
previous winner `plaintext` declares no tables on 7867 (one "(no tables)"
refusal, 0 ok subjects), so {plaintext ok} - {patch7867 ok} is EMPTY;
patch7867 reads 238 subjects with a control.

**Containers:** `_discover_archives` on 7867 = `c3.tpi, data.tpi, c31.tpi,
data1.tpi` -- the overlays ARE found (7878: the same four; 7589: two; 7275:
the WDF pair). All four `.tpi` are byte-identical (b2) to 7878's; the four
`.tpd` equal 7878's by size + head/tail 16 MB (sampled, not a full hash).
`import_plan` names all four, none missing.

**ini/:** 634 entries, 625 files; `.dbc` 0, `.dat` 203, `.ini` 218, `.lua`
198; subfolders `Script TerrainMagic ft luacfg luagui luaui skill sp tme`.
`ft/` and `sp/` are regional table sets (10 `.dat` each: itemtype,
monster, ItemtypeSub, MapDestination, Achievement, Tips, RaceTrackProp,
emoneyShopV2-4). 689 `.lua` under the root, 35 root DLLs, `ndac.dll` at the
root. Every declared spec file ships. Against 7878's `ini/`: 5 names only
on 7867 (`head.ini`, `misc.ini`, `promotion_activity.dat`,
`vendue_item.dat`, `vendue_item_type.dat`), 0 only on 7878.

**Frozen lookups (sha256):** `3DSimpleObj.ini f34f56332383`, `3dobj.ini
9202de93aa03`, `3dtexture.ini 6be2fece83fd`, `3dmotion.ini 9951727aa1ed`,
`armor.ini 8e64691e0f00` -- all == 7205, 7589, 7878. `npc.ini` (882,854 B)
differs from all of them: 4,005 `SimpleObjID` rows, **2,215 resolve (55.3%),
1,790 do not** (191 simple-object sections). `codepage.ini` = `1256`.

**Appearance (bare `part_tables`), 7867 (7205):** armet 1,168 (2,793 from
armet.dbc), body 955 (3,754), l/r_weapon 4,828 (13,292), **mount 1 (1,223
from Mount.dbc)**, head 0, misc 0. Every slot reads the frozen `.ini`.

**KNOWN LIMIT, and a FINDING against the repo's record: the asset-profile
hurdle.** `AssetRoot.profile_match(7867)` = **INCONCLUSIVE**, no profile,
bare rate 8.3%, profiled rate **70.8%** (control: 7878 = NAMED). The repo's
`tests/test_asset_profile_hurdle.Corpus` expects QUALIFIES for 7867; on
this run the 7878 corpus did NOT clear the hurdle. Not this plugin's to
fix (it is an AssetRoot decision) -- reported to the Director.

**`ini/**/*.dat` (1,606 files):** block96 1,515, block96-candidate-refuted
27, rsa-mysqldump 12, empty 11, rar-mangled 11, unknown 11, plaintext 9,
binary-plain 6, tq-stream 2, shift-obfuscated 1. **0 CODEC MISMATCH.**
Split by identity with 7878's file:

    byte-identical to 7878 (NOT evidence)  1,412 files  3,276,030 blocks
                                           335 unknown (99.99%); 1 at zero
                                           (levexp.dat, held out by design)
    INDEPENDENT of 7878 (the real test)      101 files  7,648,595 blocks
                                           1,409,167 unknown (81.58%);
                                           24 full, 77 partial, 0 at zero

Most of the top-level declared tables turned out to be 7878's bytes
(itemtype, MagicType, magictypeop, MapDestination, prof_lev_up,
AutoUseMagic ...). The independent declared tables: **`monster.dat` 100%
(5,201 sections) and `mounttype.dat` 100% (1,934)** -- the dictionary
genuinely knows those -- `texas_match_type` 100%, `instancetype` 99.98%,
`exchange_shop_goods` 97.86%, and the partial ones below.

**KNOWN LIMITS (FAILs of the dictionary, recorded, not tuned away)** --
independent tables whose whole-record count falls below 7205's:

    item:sub (ItemtypeSub.dat)    84.71% of blocks   168 rows  (7205: 633)
    achievement                   48.61%              84 rows  (7205: 391)
    coat_storage_type             59.96%             466 rows  (7205: 765)
    hairface_storage_type         72.85%             236 rows  (7205: 418)

`ini/ft/itemtype.dat` 87.07% (9,448 of 37,461 lines) and
`ini/sp/itemtype.dat` 85.53% (11,024 of 39,757) are UNDECLARED regional
variants. `itemtexture` is refused on 7867 (7205: 236) because
**`ini/ItemTexture.ini` is a 0-byte file here, as on 7878** -- a client
fact, not a reader loss.

**`catalogs()`:** 241 subjects, 238 with a control (7205: 247 / 245).
Refusals: `action` (binary layout not established), `levexp` (held out),
`itemtexture` (0-byte file). Headline rows, 7867 (7205): item **55,420**
(44,684) -- the prediction from 7878 identity holds exactly; magic 5,240
(2,907); monster 5,164 (3,670); mount 1,932 (1,819); map:dest 1,244 (936);
npc:npc.ini 4,002 (2,904); magic:op 981 (979); instancetype 568 (340);
prof_lev_up 626 (549); item:sub 168 (633).

**`ROW_COLUMNS` RE-MEASURED:** region.ini 317 rows x 14, col 6 non-numeric
317/317 (86 distinct), col 0 numeric 317/317; eventtypename 24 x 3, col 2
text 24/24, col 1 numeric; viptrans 31 x 3, col 2 text 31/31; restrain 5 x 3
(byte-identical to 7205's). The inherited map holds.

**Census: `DAT_CENSUS_ERA = False`.** The `.dat` ARE block96 (1,515 of
1,606), but 7867 ships `combat_gear.dat` AND `exchange_shop_lev.dat`, which
`tests/test_dat_census.TheVerdictIsPerBuild` pins to 7189 alone and to
exactly nine builds. Joining `BUILDS` reds that test -- the same reason
7589 stayed out. The slot stays commented.

**Text encoding: GBK, MEASURED (`measure_7867.py --gbk-only`, Director's
run 2026-09-19; owner's lineage ruling).** First run: section 4c died
printing a CJK example to a cp1252 stdout -- fixed. Second run, strict GBK
over the NAME fields, 7205 as the control. REJECTION PROBE PASS (strict GBK
rejects `81 20`, so the decoder can say no). Per table, high-byte name
fields / strict-GBK failures / fields with CJK:

    table           7205 (control)          7867
    itemtype.dat    1 of 44,684 / 0 / 0     4 of 55,420 / 0 / 0  (7878's bytes)
    MagicType.dat   0 of 2,907              0 of 5,240           (7878's bytes)
    monster.dat     66 of 3,709 / 0 / 65    66 of 5,201 / 0 / 66  INDEPENDENT
    npc.ini         40 of 2,913 / 0 / 20    29 of 4,005 / 0 / 6   plaintext

Verdicts: **monster.dat GBK** (CJK names, e.g. U+865A U+7A7A U+7075 U+517D;
63 of 66 are NOT valid UTF-8); **npc.ini GBK** (CJK names, and every
high-byte name WITHOUT CJK is `A1 A1`, GBK's IDEOGRAPHIC SPACE U+3000 --
e.g. `4c 61 6e 74 65 72 6e a1 a1` is "Lantern" + U+3000 under GBK and
"Lantern" + two inverted exclamation marks under latin1; checked on every
such field on both builds by the Director); **itemtype.dat and
MagicType.dat NO EVIDENCE** (name columns ASCII but for 1 / 4 fields, and on
7867 they are 7878's bytes anyway). 0 strict-GBK failures anywhere, on both
builds. The one 7205 monster name without CJK is `54 68 a8 a6 6f 64 72 65
64`: GBK row-A8 pinyin, "Th" + U+00E9 + "odred" (Theodred, acute e) under
GBK and "Th" + U+00A8 U+00A6 + "odred" under latin1 -- GBK again the reading
that is a name; `tests/test_patch7867.TextEncodingIsGbk` counts that row
too. The first criterion (CJK in >= 90% of high-byte fields) MISFIRED
on the U+3000 padding and printed NOT PROVEN; the revised criterion counts
CJK OR GBK full-width punctuation (U+3000-U+303F, U+FF00-U+FFEF) OR GBK
pinyin (U+00C0-U+01DC), keeps 0
strict failures, CJK present, the 7205 control and the probe, and passes on
both builds. **`TEXT_ENCODING = "gbk"`.** `ini/*.ini` overall: 124 pure
ASCII, 15 strict UTF-8, 76 strict GBK, 3 NOT GBK (`ChatFilter.ini`,
`Eliteanpt.ini`, `UStrRes.ini`) -- a finding; those three are not name
tables and decode with "replace" like everything else.

STILL NOT MEASURED
------------------
Full hashes of the `.tpd` payloads;
`ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`, `BROWSE_IMPACT`,
`RUNEEFFECT_REPEATS`, exported Lua globals; per-table block totals
(`BLOCK96_COVERAGE` stays None).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch7205 import Patch7205                # noqa: E402

#: The stamp this plugin owns, and the only thing `confidence` reads.
STAMP = "7867"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded: most name a `.dbc` (this install ships none), the last carries
#: 7205's script counts. The same list patch7589 drops, for the same reasons.
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
    """`version.dat`, `b'7867'` on this install (fingerprints.json)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7867(Patch7205):
    name = "patch7867"
    label = "Official patch client 7867"
    origin = "official"
    aliases = ("7867",)

    notes = (
        "A .tpd-only client with ZERO compiled .dbc tables and the first "
        "with the c31/data1 overlay pairs (index byte-identical to 7878's); "
        "the base c3/data index is the one every .tpd client since 7280 "
        "ships. itemtype.dat and MagicType.dat are 7878's files byte for "
        "byte, so their block96 coverage is the dictionary quoting itself; "
        "mounttype, monster and GameMap differ and are the real test. "
        "Content tables are 7205's specs over this client's own ciphertext, "
        "never 7878's decrypted rows. The art lookups are the frozen 2008 "
        "plaintext files: 2,215 of 4,005 npc.ini rows resolve a simple "
        "object. Bare appearance resolution is 8.3%, and on 2026-09-19 the "
        "7878 asset profile reached only 70.8% (INCONCLUSIVE) -- a known "
        "limit. monster (5,164) and mounttype (1,932) decode whole; item:sub "
        "168 (7205: 633), achievement 84 (391) are dictionary shortfalls. "
        "Text is GBK, measured on name fields against a 7205 control."
    )

    # -- what fingerprints.json measured ---------------------------------
    #: Base pair then overlays, the import order.
    ARCHIVE_PAIRS = ("c3", "data", "c31", "data1")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770,
                     "c31.tpd": 1156978, "c31.tpi": 5697,
                     "data1.tpd": 145649, "data1.tpi": 118}
    #: blake2b-128 prefixes from fingerprints.json (NOT sha256: the patch7589
    #: ARCHIVE_INDEX_SHA values are a different hash of the same bytes).
    ARCHIVE_INDEX_B2 = {"c3.tpi": "c2b98d44e378", "data.tpi": "00964835b31c",
                        "c31.tpi": "e9099fb3b275", "data1.tpi": "5c10f04cfa8e"}
    #: The four indices are byte-identical to 7878's (fingerprints.json).
    INDEX_IDENTICAL_TO = ("7878",)
    FIRST_OVERLAY_BUILD = 7867

    #: blake2b-128 prefixes. `same_as_7878` is the fingerprints comparison.
    TABLE_IDENTITY = {
        "ini/itemtype.dat": ("03813c760807", 16907304, True),
        "ini/MagicType.dat": ("86767d798492", 1036458, True),
        "ini/mounttype.dat": ("33a0829d3146", 468909, False),
        "ini/monster.dat": ("4069e29e74d7", 1724664, False),
        "ini/GameMap.dat": ("eda97cac9dd1", 24346, False),
    }
    #: Tables whose block96 coverage IS evidence about the dictionary on
    #: this install (not 7878's bytes). Derived from TABLE_IDENTITY.
    INDEPENDENT_TABLES = tuple(k for k, v in TABLE_IDENTITY.items()
                               if not v[2])

    #: No compiled tables (capability matrix; `ini/3DObj.dbc` absent).
    FROZEN_COMPILED = ()

    # -- 7205 measurements, NOT 7867's: blanked until measured ----------
    BLOCK96_COVERAGE = None
    #: NOT 7205's census dict (SD, W3 review, patch7589 precedent).
    BLOCK96_CENSUS = None
    #: MEASURED False: the ini/**/*.dat ARE block96 (1,515 of 1,606), but 7867
    #: ships combat_gear.dat and exchange_shop_lev.dat, which
    #: tests/test_dat_census pins to 7189 alone / to nine builds, so joining
    #: `dat_census.BUILDS` reds it (the 7589 precedent). Literal: `is` test.
    DAT_CENSUS_ERA = False
    #: measure_7867.py section 4, over ini/** RECURSIVELY (not the scope of
    #: 7205's `BLOCK96_CENSUS`), split by byte-identity with 7878's file.
    DAT_MEASURED_7867 = {
        "files": 1513, "blocks": 10924625, "unknown": 1409502,
        "fully_covered": 1435, "partial": 77, "uncovered": 1,
        "identical_to_7878": {"files": 1412, "blocks": 3276030,
                              "unknown": 335},
        "independent": {"files": 101, "blocks": 7648595,
                        "unknown": 1409167, "full": 24, "partial": 77},
    }
    #: Independent tables the dictionary only partly knows: whole rows read
    #: on 7867 against 7205's. Known limits, not targets.
    KNOWN_LIMITS = {
        "item:sub": {"rows": 168, "at_7205": 633, "blocks_known": 0.8471},
        "achievement": {"rows": 84, "at_7205": 391, "blocks_known": 0.4861},
        "coat_storage_type": {"rows": 466, "at_7205": 765,
                              "blocks_known": 0.5996},
        "hairface_storage_type": {"rows": 236, "at_7205": 418,
                                  "blocks_known": 0.7285},
    }
    #: Refused on 7867, each for a measured reason.
    REFUSED_SUBJECTS = {
        "action": "binary record layout not established (as on 7205)",
        "levexp": "held out of the dictionary by design (== 7205's bytes)",
        "itemtexture": "ini/ItemTexture.ini is a 0-byte file (as on 7878)",
    }
    #: `SimpleObjID=` rows in npc.ini against the frozen 3DSimpleObj.ini.
    NPC_COVERAGE = {"rows": 4005, "resolved_ini": 2215, "unresolved": 1790,
                    "resolved_compiled": None, "simpleobj_sections": 191}
    #: bare `part_tables()` on 7867: the frozen .ini rows, no twin.
    PART_TABLE_ROWS = {"armet": 1168, "body": 955, "l_weapon": 4828,
                       "r_weapon": 4828, "mount": 1, "head": 0, "misc": 0}
    #: `AssetRoot.profile_match` on 2026-09-19: INCONCLUSIVE.
    ASSET_PROFILE_HURDLE = {"verdict": "INCONCLUSIVE", "bare_rate": 0.0833,
                            "full_rate": 0.7083}
    SCRIPT_SURFACE = {"lua": 689, "dll": 35, "lua_bytes": None,
                      "lua_in_ini_top": 198, "exported_globals": None}
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    UNCOVERED_TABLES = None
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7867] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}
    #: MEASURED GBK on 7867 and on the 7205 control (docstring, "Text
    #: encoding"): strict GBK decodes every high-byte name field in
    #: monster.dat and npc.ini to CJK or full-width punctuation, 0 failures.
    TEXT_ENCODING = "gbk"

    #: `ROW_COLUMNS` is inherited from 7205 (region / eventtypename /
    #: viptrans / restrain) and RE-MEASURED on 7867: region 317 rows, col 6
    #: text 317/317; eventtypename col 2 text 24/24; viptrans col 2 text
    #: 31/31; restrain 5/5. The inherited map holds.

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7867` at 0.95, else 0.0.

        The `patch7205` / `patch6609` rule. Nothing cheap separates 7867 from
        7878: the same four `.tpi`, the same `itemtype.dat`, no `.dbc`. Only
        the stamp does, and `measure_7867.py` prints the ranking over every
        install with and without this plugin.
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
        """All four DatPkg indices, base pair then the overlays.
        `sharedArchives` stays False: the indices equal 7878's, the payloads
        are equal by SIZE only."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": [f"{s}.tpi" for s in self.ARCHIVE_PAIRS],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": (f"{self.label}: four DatPkg pairs (c3/data base, "
                     f"c31/data1 overlays; indices identical to 7878's), no "
                     f"compiled .dbc -- the plaintext lookup chain is the "
                     f"only one, and ini/*.dat is read as block96"),
        })
        return plan

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7867's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7867's own."""
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7867] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc (capability matrix; 3DObj.dbc absent in fingerprints). "
            "3dobj.ini and 3dtexture.ini are the 2008 files 7205 and 7878 "
            "ship. The npc.ini shortfall is PENDING measure_7867.py.")
        q["the first overlay pairs"] = (
            "c31.tpd/.tpi and data1.tpd/.tpi arrive here, index "
            "byte-identical to 7878's; the base c3/data index is the one "
            "every .tpd client since 7280 ships.")
        q["two tables are 7878's bytes"] = (
            "itemtype.dat and MagicType.dat are byte-identical to 7878's, "
            "whose decrypted corpus built the block dictionary. Their "
            "coverage is not evidence; mounttype/monster/GameMap differ "
            "and are.")
        q["a regional itemtype in ini/ft/"] = (
            "ini/ft/itemtype.dat ships beside ini/itemtype.dat (87.1% of "
            "blocks known, 16.6% of rows whole: tests/test_patch7205). The "
            "specs read the top-level file only.")
        q["ndac.dll ships here"] = (
            "The oracle that decrypted 7878's tables ships on 7867 and 7878 "
            "only (capability matrix). Not run by this plugin.")
        return q


PLUGIN = Patch7867()
