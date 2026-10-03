#!/usr/bin/env python3
r"""
patch7280 -- official Conquer Online patch 7280: THE FIRST `.tpd`-ONLY CLIENT.
7275 is the last `.wdf` client; at 7280 the WDF pair goes and so do all
twelve compiled `.dbc` tables. Both at the same step.

THE SWITCH, WITH THE FILE LISTING AS EVIDENCE (2026-09-19)
----------------------------------------------------------
Read off `Clients/7275` and `Clients/7280` by directory listing. `version.dat`
holds exactly the four bytes `7275` / `7280`.

                         7275                          7280
    archives             c3.wdf   359,069,116          c3.tpd 1,076,058,840 + c3.tpi 3,306,363
                         data.wdf 392,245,257          data.tpd  896,279,080 + data.tpi 5,724,770
    ini/*.dbc            12                            0
    ini/ entries         502                           484
    ini/**.dat           177                           177   (the SAME names)
    top-level *.dll      38                            27

The twelve `.dbc` that leave are 6609's frozen set (`Patch7205.
FROZEN_COMPILED`): armet, armor, weapon, mount, 3DSimpleObj, 3DObj,
3DTexture, 3dmotion, 3DEffect, 3DEffectobj, mountmotion, weaponmotion. The
other `ini/` names 7275 has and 7280 does not are `Action3DEffect1..5.ini`,
`emoneyshop.ini` and `StrRes.ini`. 7280 adds one, `InitTimer.lua`. No
official client ships both containers (plan §0), so 7280 is not a mixed root
and Zephyr stays the only one.

Also leaving at this step, top level: `C3_CORE_DLL.dll`, `Chat.dll`,
`DataThread.dll`, `GameData.dll`, `GraphicData.dll`, `Role3D.dll`,
`RoleView.dll`, `TqPackage.dll`, `TqPackageWdf.dll`, `graphic.dll`,
`mfc42.dll`. `TqPackageWdf.dll` going in the same patch as the `.wdf` is the
consistent reading. That the others moved into `Env_DX8/`/`Env_DX9/` is
INFERRED, not listed.

WHY THIS SUBCLASSES `Patch7205`: THE MAIN DESIGN DECISION
---------------------------------------------------------
7280 splits across two existing plugins. Each is right about one half and
wrong about the other, and the listing shows which half is which.

**CONTENT (`ini/*.dat`) continues 7205 -> 7275 -> 7280.** The `.dat` name
set is IDENTICAL to 7275's: 177 files, `comm -3` empty. It is a strict
superset of 7205's 147 (+`cq_remote_prize_cfg`, `month_card_type`,
`vendue_item_type` and 27 under `ini/luaui/` and `ini/luagui/`). By `cmp` on
every top-level `.dat` under 1 MB:

    7280 vs 7275   135 byte-identical, 13 differ   (148 compared)
    7280 vs 7205   102 byte-identical, 43 differ, 3 new

`levexp.dat` is sha256 `4482cfaa` on 7205, 7275, 7280 and 7878, so the
inherited `levexp` refusal is true of these bytes as written.
`ServerPlay.dat` is 7275's file (5,792 B, `dfa28a7d`). It is neither 7205's
(5,696 B) nor 7878's (6,650 B).

**ART (the lookup chain) is 7878's shape, not 7205's.** There is no compiled
twin, and the plaintext tables left are the frozen 2008 files. sha256 prefix,
identical on 6609, 7205, 7275, 7280 and 7878:

    3DSimpleObj.ini f34f5633   3dobj.ini 9202de93   3dtexture.ini 6be2fece
    3dmotion.ini    9951727a   armor.ini 8e64691e   armet.ini     50e2e37f
    weapon.ini      11df4435   mount.ini aad7f0df   3DEffect.ini  aa059fc7
    3DEffectObj.ini 3ba0aa16

`npc.ini` is not frozen: 653,194 B here, 651,787 at 7275, 558,037 at 7205.
On 7205 these ini files are decoys with a compiled twin behind them. On 7280,
as on 7878, they are all there is.

So the three candidates are:

  * **`Patch7878`: REJECTED, AND IT WOULD BE CONFIDENTLY WRONG ABOUT EVERY
    CONTENT ROW.** Its `catalogs()` reads monster/mount/item from
    `derived_tables(root)`, which resolves as a RELATIONSHIP:
    `Path(root).resolve().parent.parent / "derived" / "7878-dat-decrypted"`.
    For `Clients/7280` that is the same directory it finds for
    `Clients/7878`. A subclass would serve 7878's decrypted tables as 7280's
    content, and its `CONTROL_RAW` would check them against the `.out`
    files, which are 7878's bytes again. The control agrees with itself,
    which is the `mount ok=True` failure `patch7205`'s header documents,
    one level up. Its `ITEM_COLUMNS` (68 fields) are 7878's too.
  * **`PlaintextFamily`: what 7280 detects as today, and REJECTED.** Its
    `confidence` sees the lookup chain present and no `.dbc`, and the stamp
    is not one of 5017/5065/5165, so it scores 0.5 (MEASURED, see below).
    It declares NO `table_specs` and reads 0 of 1 catalog subjects here.
  * **`Patch7205`: CHOSEN.** It reads each `.dat` from THIS install's bytes,
    through the one block96 dictionary shared by the collection. It declares
    the 7205 lineage's `.dat` table list, and that list is on disk here (see
    above). What it gets wrong is the ART layer: `Patch6090`'s answers, and
    each is a claim about a `.dbc` or a `.wdf` this install does not ship.
    Those are the overrides below, one per file-listing fact.

WHAT IS OVERRIDDEN, AND THE FILE-LISTING FACT BEHIND EACH
----------------------------------------------------------
* `confidence`: the exact stamp `7280` at 0.95, else 0.0 (the 7205/6609
  rule).
* `table_profile` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL` names
  `ini/3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc` and `3dmotion.dbc`,
  and 0 of those 4 ship here. `PROFILE_PLAINTEXT` names the four `.ini`,
  and all 4 ship. This is the answer `patch7878` gives for the same shape.
* `prefers_compiled_tables` -> False: there is no compiled table to prefer.
* `import_plan`: archives `c3.tpi`/`data.tpi`, and `sharedArchives` False.
  The inherited plan names `c3.wdf`/`data.wdf`, which are ABSENT, and
  `tools/assetdiff.catalog_baseline` SKIPS an absent archive without a word
  (`if not p.is_file(): continue`). An import would index zero archive
  entries and report success. `sharedArchives` was the claim "byte-identical
  to the 2005 WDF pair every official client shipped", and that pair is not
  here.
* `table_quirks`: the `.dbc`-premised quirks inherited from 6090/6609/7205
  are REMOVED BY NAME (`DROPPED_QUIRKS`, each with its false premise) and
  7280's own are added.
* `colour_provenance`: the colour sets are still inherited. The reason given
  for inheriting them was "archives byte-identical to 6090's", and on a
  `.tpd` install that basis is gone.
* 7205's MEASUREMENT constants describe 7205's bytes. Inheriting them
  would publish 7205's numbers under 7280's name (`tools/comod.py` prints
  `NPC_COVERAGE`). `BLOCK96_COVERAGE`, `BLOCK96_CENSUS` and `NPC_COVERAGE`
  now hold 7280's own measured values. The rest (`ITEM_COLUMN_AGREEMENT`,
  `BROWSE_IMPACT`, ...) are `None`: NOT MEASURED on this install.
* `DAT_CENSUS_ERA = False`, declared explicitly, although 127 `ini/*.dat`
  classify `block96`. Joining `dat_census.BUILDS` turns
  `test_dat_census.TheVerdictIsPerBuild` red, because `combat_gear.dat` is
  pinned there as 7189-only and 7280 ships it. Re-pinning is the census
  owner's call. This plugin's own census is `BLOCK96_CENSUS`.

WHAT IS INHERITED, AND ON WHAT EVIDENCE
---------------------------------------
* `SPECS_7205`, `SPECS_7205_CENSUSED` and 6609's `.ini` census, filtered to
  disk by `Patch7205.table_specs`. The EVIDENCE is the file-name continuity
  above. The codec of each declared `.dat` on 7280 is checked by
  `tests/test_patch7280.DeclaredCodecs`.
* `ROW_COLUMNS`, re-read on 7280. `EventTypeName.ini`, `VipTrans.ini` and
  `restrain.ini` are byte-identical to 7205's (`cmp`). `region.ini` differs:
  323 rows, all 14 fields wide, column 6 non-numeric on 323/323 with 104
  distinct values, column 1 numeric on all 323. The declared `{id 0,
  name 6}` holds.
* `key_field_widths` / `aura_convention`: `Action3DEffect.ini` here opens
  with the same four-wide dotted keys as 7205's (`1999.9999.204.009=
  _p_24_wing1_open110`, first lines identical), so the 6090 "four-wide action
  fields" quirk is KEPT. The aura convention on this file is NOT MEASURED.
* Everything socket- and mesh-related (`slot_socket`, `socket_correction`,
  `texture_for_mesh`, `colourways`, `entity_name_overrides`) comes from
  6090's WDF art. On 7280's `.tpd` art it is NOT MEASURED. `texture_for_mesh`
  guards itself with `exists()`; the others do not.

MEASURED ON THE INSTALL, `measure_7280.py`, 2026-09-19 (07:53-07:57Z, EXIT 0)
------------------------------------------------------------------------------
**Detection, 48 installs** (every `Clients/` folder plus CCO), ranked with
and without this plugin. ONLY `7280` changes its winner: `plaintext` 0.50
becomes `patch7280` 0.95, a margin of 0.45. `patch7280` scores 0.00 on the
other 47. 7275 stays `patch6907` 0.55, and 7320 stays `plaintext` 0.50.

**Containers:** `AssetRoot._discover_archives(7280)` returns `c3.tpi`,
`data.tpi`.

**Appearance tables** (`part_tables()`, every part, mesh/texture resolved
through `AssetRoot.resolve_asset`):

    7275   41,905 parts   mesh 99.6%   texture 95.9%   (.dbc twins)
    7280   13,902 parts   mesh 95.6%   texture 98.7%   (frozen .ini only)
    7878   13,902 parts   mesh 99.7%   texture 98.7%

7280's 13,902 is exactly 7878's count, because the tables are the same
frozen files.

**`ini/**.dat`.** The POSITIVE CONTROL ran first: the same census on 7205
reproduces `Patch7205.BLOCK96_CENSUS` exactly. On 7280: 127 block96 files,
1,566,297 blocks, 93.51% known. 80 files are fully covered, **45 partial**
and 2 at zero (`levexp`, `ServerPlay`). 116 of the 127 are byte-identical to
7275's. Families: block96 127, rar-mangled 11, rsa-mysqldump 10, empty 8,
plaintext 7, binary-plain 6, tq-stream 3, unknown 3,
block96-candidate-refuted 1, shift-obfuscated 1. See `BLOCK96_COVERAGE` /
`BLOCK96_CENSUS`.

**`catalogs()`: 240 subjects, 237 readable, 3 refused.** Today's winner
`plaintext` reads 0 of 1. Against the real floors (7205 via `Patch7205`,
7275 via `Patch7205`'s specs):

    subject        7205     7275     7280
    item         44,684   17,823   17,823   itemtype.dat 92.92% of blocks known
    magic         2,907    1,060    1,060   MagicType.dat 91.84%
    monster       3,670      977    1,509   monster.dat 95.33% (differs from 7275's)
    mount         1,819    1,838    1,838   mounttype.dat 100%
    item:sub        633       12       98   ItemtypeSub.dat 90.12%, NOT 7275's file
    texas_match_type 82       82       12   72.69% known, NOT 7275's file
    npc:npc.ini   2,904    3,022    3,029   plaintext
    gamemap         578      598      600   binary-plain

**KNOWN LIMITS, found by measurement and stated rather than tuned away:**
* **`award_config` is LOST**: 28.6% of its 304 blocks are known, so it is
  refused. 7205 reads it at 72 rows.
* `item` 17,823 against 7205's 44,684; `magic` 1,060 against 2,907. The
  dictionary is short on the 7275/7280 copies. It is not a wrong cipher: the
  controls open named rows (`id=50000 SpeedArrow`, `id=1000 Thunder`).
* `texas_match_type` 12 of at least 82: the file changed, and 27% of its
  blocks are unknown.
* `itemtype.dat` splits at 66 `@@` fields on 17,824 lines, the same modal
  width as 7205's 44,684. The item row shape did not change at this patch.

**NPC chain** (`npcart.audit`): under `PROFILE_PLAINTEXT` 1,701 of 3,029
rows resolve; under `PROFILE_OFFICIAL`, **0 of 3,029**, which is the reason
for the profile override. The controls: 7205 resolves 2,466/2,904 under
official and 1,601 under plaintext; 7878 resolves 2,246/4,084 under
plaintext. See `NPC_COVERAGE`.

**Labels:** 237 subjects browsed, 0 all-blank without `label_key=None`. 27
subjects label rows only with numbers, among them `monster`, `mount` and
`magic:*`. They are inherited label keys, NOT fixed here; recorded as a
finding.

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
RESULT on 7280: **0 strict-GBK failures** in 351 subjects/name
sources (3 refused, not read); 14 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:monster                  1524     4     4     0      0     0    0
    Name=:npc:NpcX.ini              851     3     3     0      0     0    0
    Name=:npc:npc.ini              3037    51    23    28      0     0    0
    Name=:npc:terrainnpc.ini        168    51    47     4      0     0    0
    Name=:shop                      160    67    67     0      0     0    0
    Name=:statustips                186     3     3     0      0     0    0
    achievement                     166     1     0     0      0     1    0
    classdesc                       116    25    25     0      0     0    0
    map:dest                        948     7     6     0      0     1    0
    npc:NpcX.ini                    849     3     3     0      0     0    0
    npc:npc.ini                    3001    23    23     0      0     0    0
    npc:terrainnpc.ini              166    47    47     0      0     0    0
    shop                            160    67    67     0      0     0    0
    statustips                      186     3     3     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    shop Shop7: `cb ab c1 fa b3 c7 ce e4 c6 f7 b5 ea` = '双龙城武器店'
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:monster 1388: `bf f1 b1 a9 c5 a3 c4 a7 cd f5` = '狂暴牛魔王'
    Name=:npc:npc.ini 778: `a1 a1` = '\u3000'

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

from plugins.patch7205 import Patch7205               # noqa: E402

#: This build's stamp, exactly as `version.dat` carries it (4 bytes, `7280`).
STAMP = "7280"


def _version(root: Path) -> str:
    """`version.dat`, four bytes, `b'7280'`. Same reader as 7205's."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7280(Patch7205):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = "gbk"
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:monster': [1524, 4, 4, 0],
        'Name=:npc:NpcX.ini': [851, 3, 3, 0],
        'Name=:npc:npc.ini': [3037, 51, 23, 0],
        'Name=:npc:terrainnpc.ini': [168, 51, 47, 0],
        'Name=:shop': [160, 67, 67, 0],
        'Name=:statustips': [186, 3, 3, 0],
        'achievement': [166, 1, 0, 0],
        'classdesc': [116, 25, 25, 0],
        'map:dest': [948, 7, 6, 0],
        'npc:NpcX.ini': [849, 3, 3, 0],
        'npc:npc.ini': [3001, 23, 23, 0],
        'npc:terrainnpc.ini': [166, 47, 47, 0],
        'shop': [160, 67, 67, 0],
        'statustips': [186, 3, 3, 0],
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

    name = "patch7280"
    label = "Official patch client 7280"
    origin = "official"
    aliases = ("7280",)

    notes = (
        "The first .tpd-only official client: 7275 is the last with c3.wdf/"
        "data.wdf, and 7280 ships c3.tpd/tpi and data.tpd/tpi instead. The "
        "twelve compiled .dbc tables go at the same step (12 -> 0). Content "
        "(ini/*.dat) continues the 7205 lineage: the same 177 .dat names as "
        "7275, 135 of 148 small ones byte-identical to it, read through the "
        "block96 dictionary from this install's own bytes. Art is 7878's "
        "shape: no compiled twin, and the plaintext lookup tables (3dobj, "
        "3DSimpleObj, 3dtexture, 3dmotion, armor, armet, weapon, mount) are "
        "the frozen 2008 files, byte-identical to 7878's. Measured: 93.5% of "
        "block96 blocks known, item 17,823 rows (7205: 44,684), award_config "
        "lost at 28.6% of blocks, npc chain 1,701 of 3,029 under the "
        "plaintext profile and 0 under the official one."
    )

    #: The archive pairs, by directory listing. DatPkg, like 7878's primary
    #: pair. There are no `c31`/`data1` overlays; those arrive at 7867.
    ARCHIVE_PAIRS = ("c3", "data")

    #: WAS twelve on 7205/7275; NONE ship here. The empty tuple is a
    #: measurement (the listing counts 0 `ini/*.dbc`), and the twelve names
    #: are kept in `COMPILED_GONE_AT_THIS_STEP` so the change is legible.
    FROZEN_COMPILED = ()
    COMPILED_GONE_AT_THIS_STEP = Patch7205.FROZEN_COMPILED

    #: The plaintext lookup tables, sha256 prefix, IDENTICAL on 6609, 7205,
    #: 7275, 7280 and 7878. On 7205/7275 they are decoys with a `.dbc` twin
    #: behind them; here, as on 7878, nothing else exists.
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        "ini/armor.ini": "8e64691e0f00",
    }

    #: The other appearance tables `RolePart.ini` names, byte-identical on
    #: the same five builds (sha256 prefix, 8 hex). `head.ini` and `misc.ini`
    #: are 0-byte files (`e3b0c442`). `armet1.ini`, `shield.ini` and
    #: `pelvis.ini` are DECLARED by RolePart.ini and NOT SHIPPED.
    #: `RolePart.ini` itself is byte-identical to 7275's (`dcfc67be`), not
    #: to 7205's.
    FROZEN_PART_TABLES = {
        "ini/armet.ini": "50e2e37f", "ini/weapon.ini": "11df4435",
        "ini/mount.ini": "aad7f0df", "ini/3DEffect.ini": "aa059fc7",
        "ini/3DEffectObj.ini": "3ba0aa16",
    }
    PART_TABLES_DECLARED_NOT_SHIPPED = ("ini/armet1.ini", "ini/shield.ini",
                                        "ini/pelvis.ini")

    # -- 7205's measurements, which are NOT this install's -----------------
    #
    # Each of these is a number taken on 7205's bytes. Inheriting one would
    # publish it under 7280's name; `tools/comod.py` reads `NPC_COVERAGE`
    # off whatever plugin it is handed. The first three are REPLACED by
    # 7280's own values from `measure_7280.py` (2026-09-19), in 7205's
    # shape. `None` means the script did not measure it on this install.

    #: **False, although 127 of this build's `ini/*.dat` ARE block96.**
    #: MEASURED 2026-09-19: with `"7280"` in `dat_census.BUILDS`,
    #: `tests/test_dat_census.TheVerdictIsPerBuild.test_a_file_on_one_build_
    #: is_reported_on_one_build` fails with `['7189', '7280'] != ['7189']`.
    #: `combat_gear.dat` is pinned there as a one-build file, and 7280 ships
    #: it too. Joining the census means re-pinning that test's per-build
    #: facts, and that is the census owner's change, not this plugin's. So
    #: 7280 stays out of `BUILDS`. Its own census is in `BLOCK96_CENSUS`
    #: below. Declared explicitly because the inherited `True` was 7205's
    #: answer.
    DAT_CENSUS_ERA = False

    #: `block96.read_table(<7280>/ini/<f>, d, shape).recovery`, measured.
    #: `intact`/`units` are whole rows/sections served.
    BLOCK96_COVERAGE = {
        "itemtype.dat": {"blocks": 1134988, "known": 0.9292, "lines": 35708,
                         "intact": 17824, "shape": "rows"},
        "monster.dat": {"blocks": 112642, "known": 0.9533, "lines": 100533,
                        "units": 1782, "shape": "sections"},
        "magictype.dat": {"blocks": 54884, "known": 0.9184, "lines": 2063,
                          "intact": 1060, "shape": "rows"},
        "itemtypesub.dat": {"blocks": 10610, "known": 0.9012, "lines": 282,
                            "intact": 98, "shape": "rows"},
        "mounttype.dat": {"blocks": 37107, "known": 1.0, "lines": 25849,
                          "units": 1838, "shape": "sections"},
        "award_config.dat": {"blocks": 304, "known": 0.2862, "lines": 3,
                             "intact": 0, "shape": "rows"},
    }

    #: The whole block96 `.dat` surface on 7280, same shape as 7205's dict.
    #: Reproduced by `Patch7280.block96_census(root)`. The positive control
    #: (7205 reproducing its own recorded census) passed in the same run.
    BLOCK96_CENSUS = {"files": 127, "blocks": 1566297, "unknown": 101701,
                      "fully_covered": 80, "partial": 45, "uncovered": 2,
                      "identical_to_7275": 116}

    #: `npcart.audit` on the install. Under `PROFILE_OFFICIAL` 0 of 3,029
    #: resolve, which is the measured case for `table_profile`.
    NPC_COVERAGE = {"rows": 3029, "resolved": 1701, "unresolved": 1328,
                    "resolved_official_profile": 0}

    ITEM_COLUMN_AGREEMENT = None
    BROWSE_IMPACT = None
    UNDECLARED_DAT = None
    UNOPENED_DAT_FAMILIES = None
    RUNEEFFECT_REPEATS = None
    FORMERLY_UNCOVERED = None
    CENSUS_NAME_FALSE_POSITIVES = None

    #: Both at 0% of blocks, measured. `levexp.dat` is sha256
    #: `4482cfaa` here, as on 7205/7275/7878, so 7205's reason is true of
    #: these bytes. `ServerPlay.dat` is 7275's file, which is neither 7205's
    #: nor 7878's.
    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaa, 4,030 B) to 7878's, "
                      "whose plaintext is held out of the block dictionary by "
                      "design (seed-1234, key group unattributable). Measured: "
                      "0 of 335 blocks known.",
        "ServerPlay.dat": "5,792 B, sha256 dfa28a7d -- byte-identical to "
                          "7275's, and different from 7205's (5,696 B) and "
                          "7878's (6,650 B). Measured: 0 of 482 blocks known.",
    }

    #: Counted by directory listing, 2026-09-19. `lua` is a recursive walk
    #: (`find -iname '*.lua'`); `dll` is top level only, the same scope as
    #: `script_surface()`. 7205's figures were 224 / 37 and 7275's are
    #: 284 / 38. Bytes and exported globals are NOT MEASURED.
    SCRIPT_SURFACE = {"lua": 287, "dll": 27, "lua_bytes": None,
                      "exported_globals": None,
                      "at_7275": {"lua": 284, "dll": 38}}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7280` at 0.95, else 0.0.

        The same rule as `patch7205`/`patch6609`, and for the same reason.
        Here the format probes COULD tell this build apart from 7205 (no
        `.dbc`, `.tpd` present), but they cannot tell it apart from 7320,
        7336 ... 7589, which ship the same shape and have no plugin of their
        own. A format claim would take all of those silently. The stamp takes
        only this one.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- tables: the art layer is the plaintext family's -------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`, because `PROFILE_OFFICIAL` names four `.dbc`
        files this install does not ship and `PROFILE_PLAINTEXT` names four
        `.ini` it does. It is the profile `patch7878` uses for the same
        shape."""
        import npcart
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: 0 `ini/*.dbc` on this install, so no twin exists."""
        return False

    def import_plan(self, root, exists) -> dict:
        """DatPkg archives by name, because the inherited WDF names are
        absent here and `assetdiff.catalog_baseline` skips an absent archive
        without saying so. `sharedArchives` is False because the claim it
        stood for (the byte-identical 2005 WDF pair) is not true of this
        install. Whether 7280's `.tpd` pair is shared with its `.tpd`
        siblings is NOT MEASURED (7320's pair has the same sizes and dates,
        which is not a hash)."""
        plan = super().import_plan(root, exists)
        plan.update({
            "archives": [f"{s}.tpi" for s in self.ARCHIVE_PAIRS],
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": f"{self.label}: DatPkg archives (c3/data .tpi+.tpd) "
                    f"indexed, loose layer imported, block96 ini/*.dat read "
                    f"through the dictionary; no compiled tables exist",
        })
        return plan

    #: Quirks inherited from 6090/6609/7205 that are FALSE on 7280, each
    #: with the premise that fails. Removed by name so a reader can see what
    #: was dropped and why, rather than meet a silently shorter dict.
    DROPPED_QUIRKS = {
        "stale ini decoys": "premise is a compiled twin beside each ini; "
                            "0 .dbc ship here, so the ini is the only table",
        "a second motion reader": "premise is a 3dmotion.dbc twin; absent",
        "unpadded ids in compiled tables": "no compiled tables ship",
        "u32-wrapped motion ids": "the u32 keying is 3dmotion.dbc's; absent",
        "three fewer compiled tables": "6609's count against 6090; here 0",
        "mount.dbc shrank": "no mount.dbc ships",
        "shlayout.dat does not open": "a 6609 TQ-cipher statement; this "
                                      "file's family here is NOT MEASURED",
        "the compiled tables froze at 6609": "there are none to freeze",
        "block coverage is not record coverage": "carries 7205's 86.1% / "
                                                 "2,068-row figures; the "
                                                 "principle is restated "
                                                 "below without them",
        "224 Lua scripts and no Lua grammar": "7205's count; 7280's is "
                                              "restated below",
    }

    def table_quirks(self):
        q = super().table_quirks()
        for k in self.DROPPED_QUIRKS:
            q.pop(k, None)
        q["the archive container changed at this patch"] = (
            "7275 ships c3.wdf/data.wdf and 7280 ships c3.tpd+c3.tpi and "
            "data.tpd+data.tpi, with no .wdf. 7280 is the first .tpd-only "
            "official client and 7275 the last .wdf one. core/coassets."
            "AssetRoot discovers both containers, so reading works; a plan "
            "or tool that names c3.wdf finds nothing and may not say so.")
        q["no compiled tables, and the plaintext lookups are frozen"] = (
            "0 ini/*.dbc (7275 has 12). 3DSimpleObj.ini, 3dobj.ini, "
            "3dtexture.ini, 3dmotion.ini, armor.ini, armet.ini, weapon.ini "
            "and mount.ini are byte-identical (sha256) to 6609's, 7205's "
            "and 7878's 2008 files. On 7205 a compiled twin stood behind "
            "them; here nothing does, the same situation patch7878 records. "
            "An npc or appearance the frozen table does not name looks "
            "exactly like one with no art. Measured on 7280: 1,701 of 3,029 "
            "npc rows resolve under the plaintext profile, 0 under the "
            "official one.")
        q["ini/*.dat is the 6907-7878 block cipher, not TQ"] = (
            "127 of the ini/**.dat files on 7280 classify block96 (7205: "
            "101 of 146). core/tqdat.py must not be pointed at them; "
            "core/block96.py reads them through the dictionary "
            "tools/datdict.py builds, 93.51% of blocks known here.")
        q["block coverage is not record coverage, on 7280"] = (
            "A block96 record is served only if every 12-byte block it "
            "spans is in the dictionary, so a file's block coverage is a "
            "ceiling on its record coverage, never the same number. On "
            "7280, itemtype.dat has 92.92% of its blocks known and serves "
            "17,824 whole rows of 35,708 lines. 45 of the 127 block96 files "
            "are partial. See BLOCK96_COVERAGE.")
        q["award_config.dat is lost"] = (
            "28.6% of its 304 blocks are known, so the table is refused. "
            "7205 reads it at 72 rows. A dictionary gap, not a code "
            "defect.")
        q["287 Lua scripts and no Lua grammar"] = (
            "287 .lua files under the install (7275: 284, 7205: 224) and 27 "
            "top-level DLLs (7275: 38). Declared as a surface, not as "
            "tables; tools/clientscripts.py and tools/scriptreport.py read "
            "it.")
        return q

    def colour_provenance(self):
        """The sets are still 6090's, reached through 6609 and 7205. The
        basis for carrying them this far was "archives byte-identical to
        6090's", and on a `.tpd` install that basis is gone. Nobody has
        looked at 7280 on screen."""
        return ("colourway inherited from 6090 via 6609 and 7205 "
                "(unverified; this install's .tpd archives are NOT the WDF "
                "pair that inheritance rested on)", "inferred")

    def why_no_tables(self) -> str:                       # pragma: no cover
        return ("7280 declares tables; if you are reading this the install "
                "has no ini/ directory")


PLUGIN = Patch7280()
