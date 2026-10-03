#!/usr/bin/env python3
r"""
patch7336 -- official Conquer Online patch 7336: 7205's content layer inside
a `.tpd`-only install with no compiled tables.

7336 is the fourth client of the `.tpd`-only era (7280, 7320, 7336, ...).
**The archive switch is at 7275 -> 7280**: 7275 is the last client that ships
`c3.wdf` / `data.wdf` and twelve `ini/*.dbc`; 7280 is the first that ships
only `.tpd`/`.tpi` pairs and **zero** `.dbc`. The `.dbc` set goes from 12 to
0 at that same step. 7336 is on the `.tpd` side of it. The listing, read
2026-09-19 (a directory listing only; no archive was opened):

    build  version.dat  *.wdf  *.tpd/*.tpi  ini/*.dbc  ini/ entries  ini/*.dat
    7275   7275          2      0/0          12         502           150
    7280   7280          0      2/2           0         484           150
    7320   7320          0      2/2           0         498           156
    7336   7336          0      2/2           0         509           160
    7373   7373          0      2/2           0         516           161

    7336/c3.tpd    1,076,058,840 B    7336/c3.tpi    3,306,363 B
    7336/data.tpd    896,279,080 B    7336/data.tpi  5,724,770 B

The four archive files have the SAME SIZES on 7280, 7320, 7336 and 7373.
Same size is not the same bytes. `measure_7336.py` found the `.tpi` indices
byte-identical, and a SAMPLED check of the `.tpd` payloads, so this plugin
still does not say they are shared (see below).

WHY THE BASE IS `Patch7205`, AND WHAT IS OVERRIDDEN
----------------------------------------------------
There are two candidate bases. Each one is right about one layer of this
install and wrong about the other:

  * **`Patch7878` (`PlaintextFamily`)** fits the art layer: no `.dbc`, a
    `.tpd` pair, and the frozen plaintext lookup chain. It **must not be the
    base, because of its content layer.** `patch7878.derived_tables(root)`
    finds `<root>/../../derived/7878-dat-decrypted` for ANY root under
    `Clients/`. So a 7336 subclass would serve **7878's** decrypted `itemtype`
    / `monster` / `mounttype` as if they were 7336's. Nothing would raise, and
    every control would pass, because the rows come from real files. This is
    the confidently-wrong failure the pi-planning skill's §0 describes.
  * **`Patch7205`** fits the content layer. Its `ini/*.dat` specs use the
    `block96` codec through the collection's own block dictionary
    (`core/block96.py`), which decodes a table from that client's own
    ciphertext. Every file named by `SPECS_7205` (19) and
    `SPECS_7205_CENSUSED` (85) is on disk here. Checked by listing: none
    absent. 132 of 6609's 141 borrowed `.ini` specs are present, against 139
    on 7205. Its ART layer is the 6090 lineage's, which assumes compiled
    twins.

So the base is `Patch7205`, and the two art-layer hooks that name `.dbc` files
are overridden, because this install ships none:

  * `table_profile()` -> `npcart.PROFILE_PLAINTEXT`, not
    `PROFILE_OFFICIAL`. `PROFILE_OFFICIAL` names `ini/3DSimpleObj.dbc`,
    `3DObj.dbc`, `3DTexture.dbc` and `3dmotion.dbc`, and **none of the four
    is on disk here** (`tests/test_patch7336.TheArtProfileNamesFilesThat
    Exist` is the arm that fails on `Patch7205`).
  * `prefers_compiled_tables()` -> False. There is no twin to prefer.
  * `import_plan()` names the `.tpd` pair and drops 6090's
    "shared archives skipped, compiled .dbc tables read" note, which is
    false here on both counts.

What is KEPT from the 6090 lineage, and on what evidence:

  * **Four-wide `Action3DEffect` action field** (`FIELD_WIDTHS`):
    MEASURED on 7336. Key shapes are (3,4,3,3) x1,767, (4,4,3,3) x712,
    (1,4,3,1) x166, (3,4,1,3) x24. Every dominant shape has the action field
    4 wide, the same as 6090 (10,113 at (3,4,3,3)) and 7205. The
    `PlaintextFamily` value (3 wide) would be wrong here.
  * **`aura_convention() == "table"`**: MEASURED. 2,239 of 2,675
    `Action3DEffect.ini` keys carry action `9999` (7205: 1,900 of 2,328).
  * **`ROW_COLUMNS`**: RE-MEASURED. `region.ini` has 304 rows, 14 fields, and
    column 6 is non-numeric on 304/304 (85 distinct). `EventTypeName.ini`,
    `VipTrans.ini` and `restrain.ini` are byte-identical to 7205's (`cmp`).
  * `texture_for_mesh`, colour sets, sockets, `socket_correction`: INHERITED
    AND NOT MEASURED HERE. They are 6090 measurements. The texture
    conventions are gated on `exists()`, so a wrong guess finds no file
    rather than a wrong one. `colour_provenance` says what that is worth.

WHAT IS MEASURED (listing and small plaintext reads, 2026-09-19)
----------------------------------------------------------------
**The art lookups are the frozen 2008 decoys, and nothing stands behind
them.** sha256 prefixes:

    ini/3DSimpleObj.ini f34f56332383   ini/3dobj.ini   9202de93aa03
    ini/3dtexture.ini   6be2fece83fd   ini/3dmotion.ini 9951727aa1ed
    ini/armor.ini       8e64691e0f00

These are identical to 7205's, 7275's, 7280's and 7878's, and to
`patch7878.FROZEN_LOOKUPS`. On 7205 a compiled twin covered for them; here,
as on 7878, they are all there is. `npc.ini` kept growing:

    build  npc.ini headers  resolve via 3DSimpleObj.ini  unresolved
    7205         2,913             1,600  (54.9%)          1,313
    7336         3,099             1,723  (55.6%)          1,376

(`SimpleObjID=N` -> `[ObjIDType<N>]`, one count per header. POSITIVE CONTROL:
the same code reproduces `patch7205.NPC_COVERAGE["resolved_ini_decoy"]`,
1,600, exactly. On 7205 that was the decoy's figure beside a 2,466-row
compiled answer. **On 7336 it is the only answer.** 1,376 npc rows name a
simple object the frozen table does not carry. Nothing raises for them; an
unresolved row looks exactly like an npc with no art.)

**`levexp.dat` is byte-identical to 7205's and 7878's** (4,030 B, sha256
`4482cfaaec7d`), so the `SPECS_7205` refusal text, which says exactly that,
is true on this install too. **`ServerPlay.dat` is a different file** (5,860
B, `a7c86532b3ef`; 7205's is 5,696 B and 7878's is 6,650 B).

**`ItemtypeSub.dat` is 381 bytes.** 7205 ships 207,838 B, 7275 40,812 B,
7280 127,325 B, 7320 248,518 B, 7373 364,014 B and 7878 464,778 B. A
381-byte copy between 248 KB and 364 KB is either a real near-empty revision
or something else. MEASURED: 58.06% of its blocks are known, it is ONE
line, and not one record is whole, so `item:sub` REFUSES on this build (7205
serves 633). The refusal is recorded as a known limit, not tuned away.

**`ini/` differs from 7205's by name:**

    gone since 7205   the 12 .dbc; action3deffect1..5.ini, emoneyshop.ini,
                      strres.ini
    new since 7205    13 .dat (NEW_DAT_SINCE_7205, UNDECLARED -- not yet
                      decoded); actionrole3deffect.ini, actionsoundex.ini,
                      appqdkey.ini, cq_remote_prize_cfg.ini, operateweben.ini,
                      shaderparam.ini

**Script surface**: 321 `.lua` (13,905,069 B) and 27 root DLLs, against
7205's 224 (19,094,816 B) and 37. That is more files and fewer bytes. The
same `find` reproduces 7205's recorded 224 / 19,094,816 / 37 exactly, which
is the control. Exported globals were NOT counted.

`codepage.ini` reads `1256`, the same as 7205's and 7878's.
`TEXT_ENCODING` stays 7205's latin1, and that is INHERITED, NOT MEASURED:
7878 has the same declaration and turned out to be GBK.

WHAT IS INFERRED
----------------
  * That the 7878-derived block dictionary decodes this build's `ini/*.dat`
    the way it decodes 7205's. It is the same cipher era (6907-7878, see
    `CIPHER_ERA`), and `inidat` classification should confirm it. **Neither
    has been run on 7336.**
  * That every codec in the inherited specs (block96 for the content tables;
    plaintext for `Shop.dat`, `SkinVersion.dat`, `GetPlatformInfo.dat` and
    `ndloginServer.dat`; binary-plain for `Action.dat` and `GameMap.dat`) is
    what `inidat.classify` says on THIS install. `measure_7336.py` checks
    all of them.

MEASURED BY `measure_7336.py` (run 2026-09-19 at 534734a6, EXIT 0, 22 s)
------------------------------------------------------------------------
**Positive controls first, and both PASSED.** On 7205, block96 coverage
reproduced all five `Patch7205.BLOCK96_COVERAGE` rows exactly (itemtype
1,080,527 blocks / 1.0 / 44,684 intact), and `Patch7205.confidence(7205)`
returned 0.95. So the low numbers below come from 7336's files, not from a
broken instrument.

**Detection, over all 48 installs (every `Clients/` folder plus the live CCO
install):** only 7336 changes its winner. It goes from `plaintext` 0.50 to
`patch7336` 0.95, with a margin of 0.45. patch7336 scores 0.00 on the other
47. 7280, 7320, 7373 and 7387-7867 still fall to `plaintext` at 0.50.

**Containers.** `_discover_archives` on 7336 returns `['c3.tpi',
'data.tpi']`. 7275 returns `['c3.wdf', 'data.wdf']` (the control) and 7280
returns the `.tpi` pair. `c3.tpi` and `data.tpi` are byte-IDENTICAL
(blake2b) to 7280's, 7320's and 7373's. The `.tpd` payloads match those
three on size plus the first and last 16 MB. That was a SAMPLED check, not a
full hash, so this plugin still does not say the archives are shared.

**Appearance tables (`part_tables()`): nine slots, every one read from the
frozen `.ini`, twin=None**:

    slot          7336 (.ini)    7205 (.dbc twin)
    body/mix_body        955         3,754
    armet/mix_armet    1,168         2,793
    l/r_weapon         4,828        13,292
    mount                  1         1,223
    head, misc             0             0

**`mount` has ONE row on 7336.** `Mount.ini` is all there is, and it is
effectively empty. The builder's mount slot is unusable on this build from
`part_tables()`. This is a FINDING about the build, not a reader defect.

**`ini/*.dat`:** 168 block96 files (with `ini/luaui/**` and
`ini/luagui/**`), 1,762,544 blocks, of which 115,502 are unknown, so 93.45%
are known. 92 files are fully covered, 74 partially and 2 not at all
(`levexp.dat`, `ServerPlay.dat`). NO codec mismatch: every declared `.dat`
is the family `inidat.classify` reports. Families: block96 168,
rar-mangled 11, rsa-mysqldump 10, empty 10, plaintext 7, binary-plain 6,
tq-stream 3, unknown 2, shift-obfuscated 1. **Unlike 7205 (0 partial), this
build has 74 partial tables. The 7878-derived dictionary does not know all of
7336's blocks, and block coverage is NOT record coverage:**

    file              known    lines    intact   units   (7205 units)
    itemtype.dat      93.61%  38,430    22,621  25,847    44,684
    monster.dat       95.56% 105,682   102,352   2,022     3,709
    MagicType.dat     91.65%   2,200     1,036   1,154     2,907
    mounttype.dat     99.99%  25,974    25,973   1,847     1,819
    MapDestination    70.88%   6,226     4,810     480   (catalog 936)
    ItemtypeSub.dat   58.06%       1         0       0       633
    award_config.dat  28.62%       3         0       0   (catalog 72)
    Achievement.dat   61.15%     201       168     168       391

**`catalogs()`:** 240 subjects and 236 with a control. 7205 has 247 and
245. The two NEW refusals are `item:sub` (`ItemtypeSub.dat`, 381 B, not one
record whole) and `award_config` (28.6% of blocks). Both are known limits,
recorded in the test. Headline rows, 7336 against 7205: item 22,621 / 44,684;
magic 1,036 / 2,907; monster 1,697 / 3,670; mount 1,846 / 1,819; map:dest
262 / 936; npc.ini 3,095 / 2,904. Every served count is a FLOOR on the
table, because a record needs every block it spans.

STILL NOT MEASURED: `ITEM_COLUMN_AGREEMENT`, `UNOPENED_DAT_FAMILIES`,
`BROWSE_IMPACT`, `RUNEEFFECT_REPEATS`, a full hash of the `.tpd` payloads,
and the 13 `NEW_DAT_SINCE_7205` grammars. They stay `None` or undeclared.

`UNDECLARED_DAT` keeps 7205's fourteen names, because all fourteen files are
on disk here. Each reason is marked as 7205's measurement, because none has
been re-taken on this build.

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
RESULT on 7336: **0 strict-GBK failures** in 351 subjects/name
sources (4 refused, not read); 13 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1721    58    58     0      0     0    0
    Name=:npc:NpcX.ini              885     3     3     0      0     0    0
    Name=:npc:npc.ini              3099    44    22    22      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                223     3     3     0      0     0    0
    achievement                     166     1     0     0      0     1    0
    classdesc                       123    25    25     0      0     0    0
    npc:NpcX.ini                    885     3     3     0      0     0    0
    npc:npc.ini                    3073    22    22     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      223     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 1542: `cc ec c4 a7 d1 fe bc a7` = '天魔瑶姬'
    Name=:npc:npc.ini 821: `a1 a1` = '\u3000'

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
STAMP = "7336"

#: Inherited quirks that are FALSE on this install and are dropped, not
#: re-worded. Most name a `.dbc`, and this install ships none. The last one
#: carries 7205's script counts. A quirk served by `/api/plugins` is read as
#: a fact about this client.
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
    """`version.dat`, four bytes, `b'7336'` on this install (read
    2026-09-19)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7336(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1721, 58, 58, 0],
        'Name=:npc:NpcX.ini': [885, 3, 3, 0],
        'Name=:npc:npc.ini': [3099, 44, 22, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [223, 3, 3, 0],
        'achievement': [166, 1, 0, 0],
        'classdesc': [123, 25, 25, 0],
        'npc:NpcX.ini': [885, 3, 3, 0],
        'npc:npc.ini': [3073, 22, 22, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [223, 3, 3, 0],
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

    #: **False, deliberately, though the `ini/*.dat` ARE block96** (168
    #: files, measured). `tools/dat_census.py` BUILDS is the eleven builds
    #: its tests pin per build (`combat_gear.dat` on 7189 alone of the ten
    #: non-7205 builds, `exchange_shop_lev.dat` on nine), and 7336 ships both,
    #: so adding it would break those pins. Its census would also be taken
    #: over 74 partially-decoded tables. Widening the census is a separate
    #: change, and until then this build says it is not covered, instead of
    #: inheriting 7205's `True`.
    DAT_CENSUS_ERA = False

    name = "patch7336"
    label = "Official patch client 7336"
    origin = "official"
    aliases = ("7336",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf) with ZERO compiled "
        ".dbc tables. The archive switch is at 7275 -> 7280, and the .dbc "
        "set went from 12 to 0 at the same step. Content tables are 7205's: "
        "ini/*.dat in the 96-bit block cipher, read through the collection's "
        "block dictionary with 7205's specs. The art lookups are the frozen "
        "2008 plaintext decoys, sha-identical to 7205's and 7878's, with no "
        "compiled twin behind them: 1,723 of 3,099 npc.ini rows (55.6%) "
        "resolve a simple object and 1,376 do not, and nothing raises for "
        "those. MEASURED: the 7878-derived block dictionary knows 93.45% "
        "of this build's .dat blocks (74 tables partial), so item serves "
        "22,621 whole rows against 7205's 44,684, magic 1,036 against "
        "2,907, and item:sub and award_config refuse. part_tables() reads "
        "the frozen .ini, and mount has 1 row."
    )

    # -- what the listing measured -----------------------------------------
    #: The archive pairs on disk, `.tpd` payload + `.tpi` index, with sizes.
    #: No `.wdf`. MEASURED: the `.tpi` indices are byte-identical to
    #: 7280/7320/7373; the `.tpd` payloads match on a sampled check only.
    ARCHIVE_PAIRS = ("c3", "data")
    ARCHIVE_SIZES = {"c3.tpd": 1076058840, "c3.tpi": 3306363,
                     "data.tpd": 896279080, "data.tpi": 5724770}

    #: The WDF-to-TPD bracket, from listings of the neighbours.
    LAST_WDF_BUILD = 7275
    FIRST_TPD_BUILD = 7280

    #: No compiled tables. MEASURED: `find ini -iname '*.dbc'` -> 0.
    FROZEN_COMPILED = ()

    #: The plaintext lookups, which are the LIVE tables here only because
    #: nothing else exists. sha256 prefixes, identical to
    #: `patch7878.FROZEN_LOOKUPS` and to 7205's decoys.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: `npc.ini` headers against the frozen `3DSimpleObj.ini`, per header.
    #: The instrument reproduces 7205's 1,600 exactly. There is no compiled
    #: figure because there is no compiled table.
    NPC_COVERAGE = {"rows": 3099, "unique": 3095, "resolved_ini": 1723,
                    "unresolved": 1376, "resolved_compiled": None,
                    "simpleobj_sections": 191}

    #: Counted by `find -iname '*.lua'` and `ls *.dll`. The same commands
    #: reproduce 7205's `SCRIPT_SURFACE` exactly. Globals were not counted.
    SCRIPT_SURFACE = {"lua": 321, "dll": 27, "lua_bytes": 13905069,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}

    #: `.dat` in 7336's `ini/` that 7205 does not ship. UNDECLARED: none has
    #: been decoded, and a spec is a promise to parse.
    NEW_DAT_SINCE_7205 = (
        "coat_color_rule.dat", "cop_task_type.dat", "cq_remote_prize_cfg.dat",
        "daoqi_dict_type.dat", "emoneyshopv2.dat", "month_card_type.dat",
        "new_shop_goods.dat", "new_shop_info.dat",
        "newslot_guanqia_cfg.dat", "speffect_attribute.dat",
        "vendue_item.dat", "vendue_item_type.dat", "vendue_type.dat",
    )

    #: `ini/` entries 7205 ships and 7336 does not (besides the 12 `.dbc`).
    GONE_SINCE_7205 = (
        "action3deffect1.ini", "action3deffect2.ini", "action3deffect3.ini",
        "action3deffect4.ini", "action3deffect5.ini", "emoneyshop.ini",
        "strres.ini",
    )

    #: Sizes that make a 7205 comparison unsafe until decoded.
    ITEMTYPESUB_BYTES = 381

    # -- 7205 measurements NOT re-taken on this build ---------------------
    #: MEASURED by measure_7336.py with `block96.read_table`, the same
    #: fields as `Patch7205.BLOCK96_COVERAGE`. `blocks` was not printed per
    #: file and is None rather than derived.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": None, "known": 0.9361, "lines": 38430,
                         "intact": 22621, "units": 25847, "shape": "rows"},
        "monster.dat": {"blocks": None, "known": 0.9556, "lines": 105682,
                        "intact": 102352, "units": 2022,
                        "shape": "sections"},
        "magictype.dat": {"blocks": None, "known": 0.9165, "lines": 2200,
                          "intact": 1036, "units": 1154, "shape": "rows"},
        "itemtypesub.dat": {"blocks": None, "known": 0.5806, "lines": 1,
                            "intact": 0, "units": 0, "shape": "rows"},
        "mounttype.dat": {"blocks": None, "known": 0.9999, "lines": 25974,
                          "intact": 25973, "units": 1847,
                          "shape": "sections"},
    }
    #: THIS client's measured census, in the shape of
    #: `Patch7205.BLOCK96_CENSUS` (`block96_census()` re-counts it). The two
    #: 7878-identity keys were not measured here and are None.
    BLOCK96_CENSUS = {"files": 168, "blocks": 1762544, "unknown": 115502,
                      "fully_covered": 92, "partial": 74, "uncovered": 2,
                      "fully_covered_identical_to_7878": None,
                      "fully_covered_and_different": None}
    #: Measured `part_tables()` rows. Every slot reads the frozen `.ini`,
    #: because there is no `.dbc` twin. `mount` is ONE row.
    PART_TABLE_ROWS = {"body": 955, "armet": 1168, "weapon": 4828,
                       "mount": 1, "head": 0, "misc": 0}
    #: Catalog subjects that open on 7205 and REFUSE here, measured.
    REFUSED_HERE = {
        "item:sub": "ItemtypeSub.dat is 381 B; 58.1% of blocks known, "
                    "not one record whole",
        "award_config": "28.6% of blocks known, 0 of 3 lines whole",
    }
    FORMERLY_UNCOVERED = None
    ITEM_COLUMN_AGREEMENT = None
    UNOPENED_DAT_FAMILIES = None
    BROWSE_IMPACT = None
    RUNEEFFECT_REPEATS = None
    #: Only the one fact measured here. `ServerPlay.dat` is a different file
    #: from 7205's and has not been decoded.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's and 7878's, so 7205's finding carries by "
                      "identity: held out of the dictionary by design.",
    }
    #: 7205's fourteen refused names, all on disk here. The reasons are 7205's
    #: measurements, marked as such. They have not been re-taken on 7336.
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7336] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7336` at 0.95, else 0.0.

        This is the `patch7205` / `patch6609` rule. The formats cannot
        separate 7336 from 7280/7320/7373: the same `.tpd` sizes, no `.dbc`,
        the same frozen lookups. So nothing but the stamp is read, and no
        sibling is claimed. Before this plugin, the listing predicts this
        install fell to `plaintext` at 0.5 (no `.dbc`, the plaintext chain
        present, an unlisted stamp). `measure_7336.py` prints the real
        ranking.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`. Every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none. The plaintext chain is present,
        and it is the frozen 2008 set (see `FROZEN_LOOKUPS`)."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install to prefer."""
        return False

    def import_plan(self, root, exists) -> dict:
        """The `.tpd` pair, and no claim that it is shared.

        `Patch6090.import_plan` says "shared archives skipped ... compiled
        .dbc tables read", which is true of the WDF pair on 5017-7275 and
        false here on both counts. The four archive files match 7280/7320/
        7373 by identical `.tpi` and a SAMPLED `.tpd` check (head and tail
        16 MB plus size). That is not a full hash, so `sharedArchives` stays
        False.
        """
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
        """Four hops from where anyone looked, and across a container change.
        The colour sets are 6090's, via 6609 and 7205, and were read on the
        WDF pair. This install ships `.tpd`, and nobody has looked at it on
        screen."""
        return ("colourway inherited from 6090 via 6609 and 7205 (unverified "
                "on any of them, and 7336's archives are .tpd, not the WDF "
                "pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7205's quirks minus the `.dbc` ones, plus 7336's own.

        What is left of the inherited set is ART behaviour measured on 6090.
        Those entries are kept and marked as not re-measured here.
        """
        inherited = super().table_quirks()
        q = {k: ("[inherited, not re-measured on 7336] " + v)
             for k, v in inherited.items() if k not in DBC_ERA_QUIRKS}
        q["four-wide action fields"] = (
            "RE-MEASURED on 7336: Action3DEffect keys are (3,4,3,3) x1,767, "
            "(4,4,3,3) x712, (1,4,3,1) x166 -- the action field is four wide "
            "as on 6090 and 7205, not three as in the plaintext family.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 .dbc. 3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini are "
            "sha-identical to the 2008 files 7205 and 7878 ship. npc.ini has "
            "3,099 headers; 1,723 resolve a simple object and 1,376 do not. "
            "Nothing raises for an unresolved row.")
        q[".tpd archives, not .wdf"] = (
            "c3.tpd/.tpi and data.tpd/.tpi only. The switch is at 7275 -> "
            "7280, the same step the 12 .dbc disappear.")
        q["321 Lua scripts and no Lua grammar"] = (
            "321 .lua (13,905,069 B) and 27 root DLLs, against 7205's 224 "
            "(19,094,816 B) and 37. Declared as a surface, not as tables; "
            "tools/clientscripts.py and tools/scriptreport.py read it.")
        q["ItemtypeSub.dat is 381 bytes"] = (
            "Neighbours ship 40-465 KB. Measured: 58.1% of blocks known, "
            "one line, no whole record, so item:sub refuses here.")
        q["block coverage is not record coverage, on THIS build"] = (
            "93.61% of itemtype.dat's blocks are known and 22,621 of 38,430 "
            "lines come out whole; 74 of 168 block96 tables are partial. "
            "Every block96 count this plugin serves is a FLOOR.")
        return q


PLUGIN = Patch7336()
