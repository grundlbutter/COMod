#!/usr/bin/env python3
r"""
patch7622 -- official Conquer Online patch 7622: a `.tpd`-only install with
no compiled tables and no overlay pair, one patch step after 7589, whose
base archive INDEX and `itemtype.dat` are byte-identical to 7589's.

THE NAME
--------
`version.dat` reads `7622`. The owner renamed the folder to `Clients/7622`
on 2026-09-19. Every measurement quoted below that predates the rename was
taken on the SAME bytes under the old folder name: "measured as
Clients/7632 on 2026-09-19" (the 7589 plugin's listing, branch
`director/comod-plugin-7589`) and "measured as Clients/7632" in
`fingerprints.json` (`C:/Claude/co-pnd/docs/patchnotes/fingerprints.json`,
entry `7632`, stamp `7622`, build digest `045583971e83808a`). A second
folder, `Clients/7682`, also stamped `7622` (the two were one client shipped
twice: `fingerprints.json` `identical` lists `["7632", "7682"]`); the owner
deleted it on 2026-09-19. `measure_7622.py` section 1 re-counts how many
installs on disk stamp `7622` today, because this plugin claims them all.

WHY THE PARENT IS `Patch7589`
-----------------------------
Decided from `fingerprints.json` (blake2b of every significant file; no
install was read for this plugin's code phase). Of the three neighbours,
7622 agrees with 7589 on every file where 7589 and 7878 disagree:

    file                 7589          7622 (as 7632)  7878
    c3.tpi / data.tpi    c2b98d44 / 00964835 -- identical on all three
    c31.tpd/.tpi         absent        absent          present (overlay)
    data1.tpd/.tpi       absent        absent          present (overlay)
    ini/itemtype.dat     d51785eab3    d51785eab3      03813c7608 (16.9 MB)
    ini/monster.dat      lower-case    lower-case      `Monster.dat`
    ini/3dobj.ini        a37b352aeb    a37b352aeb      a37b352aeb
    ini/3dtexture.ini    1e639c2e5c    1e639c2e5c      1e639c2e5c
    ini/RolePart.ini     333d29c83c    333d29c83c      333d29c83c
    ini/3DObj.dbc        absent        absent          absent

and differs from 7589 exactly where a patch step would: `MagicType.dat`
(895,374 B vs 889,266), `monster.dat` (1,612,307 vs 1,567,629),
`mounttype.dat` (458,524 vs 457,533), `GameMap.dat` (14,997 vs 14,496),
`Server.dat` and `play.exe`.

  * **`Patch7878` is NOT the base**, for the reason every `.tpd`-era plugin
    records: `patch7878.derived_tables(root)` finds
    `derived/7878-dat-decrypted` for ANY root under `Clients/`, so a subclass
    would serve 7878's decrypted tables as 7622's rows. Here that is
    provably wrong: 7622's `itemtype.dat` is 14,970,839 B and 7878's is
    16,907,304 B, different files. 7622 also ships no overlay pair.
  * **`Patch7589`** already carries the right shape for this layout
    (inherited from `Patch7205` via 7589): the `block96` specs over
    `ini/*.dat`, `PROFILE_PLAINTEXT`, no compiled-table preference, and the
    `.tpd` import plan. Nothing in that shape is 7589-specific.

WHAT IS OVERRIDDEN, AND WHY
---------------------------
  * `confidence()`: the exact `version.dat` stamp `7622` at 0.95, else 0.0.
  * **Every 7589 MEASUREMENT is reset**, not inherited: `NPC_COVERAGE`,
    `SCRIPT_SURFACE`, `KNOWN_LIMITS`, `PART_TABLE_ROWS`, the census figures,
    `TABLE_IDENTITY`. A subclass that inherited them would present 7589's
    numbers as 7622's to any generic reader (the `BLOCK96_CENSUS` lesson,
    SD W3 review). Each is `None` until `measure_7622.py` has run.
  * `NEW_DAT_SINCE_7205` = 7589's 18 plus the two `.dat` the 7589 listing
    found in 7632 and not in 7589 (below). Still UNDECLARED: none is decoded.
  * `ITEMTYPESUB_BYTES`, `INDEX_IDENTICAL_TO`, `notes`, `table_quirks`,
    `colour_provenance`: the 7589 text names 7589's numbers.
  * `DAT_CENSUS_ERA = False` (literal), for NOW: 7589 is block96 and still
    stayed out, because joining `dat_census.BUILDS` reds
    `tests/test_dat_census`'s per-build pins (`combat_gear.dat` on 7189
    alone; 7589 ships it). The same pin is expected to bite 7622; NOT YET
    MEASURED, and the `7622` slot in `tools/dat_census.py` stays commented
    until it is. `BLOCK96_CENSUS` stays `None` (inherited from 7589).

WHAT IS MEASURED (before this plugin; attributed)
-------------------------------------------------
From `fingerprints.json`, as Clients/7632 (the table above), plus:

    c3.tpd 1,076,058,840 B   c3.tpi 3,306,363 B     (== 7589, == 7878)
    data.tpd 896,279,080 B   data.tpi 5,724,770 B   (== 7589, == 7878)

The `.tpd` payloads were NOT read; only sizes and the `.tpi` hashes.

From the 7589 plugin's listing and `measure_7589.py` (2026-09-19, as
Clients/7632):

    ini/ entries 543, ini/*.dat 167 (7589: 539 / 165), 0 `.dbc`
    ini/itemtype.dat      14,970,839 B  == 7562 == 7589; != 7205, 7878
    ini/ItemtypeSub.dat      645,463 B  (7589 407,962; 7878 464,778)
    region.ini            byte-identical to 7562's and 7589's
    in 7632, not in 7589  casual_game_cfg.ini, gouyu_xuzuo_type.dat,
                          rune_effect.dat, texasraffle.lua
    npc.ini               a DIFFERENT file from 7589's
    c3.tpi / data.tpi     IDENTICAL (full hash) to 7589's
    c3.tpd / data.tpd     EQUAL to 7589's by size + head/tail 16 MB
                          (sampled, NOT a full hash)
    detection             `plaintext` at 0.50, margin 0.50 -- no plugin
                          claimed this install before this one

The frozen art lookups (`3DSimpleObj.ini`, `3dobj.ini`, `3dtexture.ini`,
`3dmotion.ini`, `armor.ini`) were recorded by the 7589 plugin as identical on
7205, 7562, 7589, 7632 and 7878, so `FROZEN_LOOKUPS` is inherited by that
measured identity.

WHAT measure_7622.py MEASURED (run by the Director, 2026-09-19, head f4f41b5c)
-----------------------------------------------------------------------------
Output: `C:/Claude/co-p7622/measure_7622.out`, EXIT 0, 67 s. POSITIVE
CONTROLS PASSED first: `Patch7589.confidence` on 7589 = 0.95 (the parent's
own build); `Patch7205.confidence` on 7205 = 0.95; block96 coverage on 7205
reproduces `Patch7205.BLOCK96_COVERAGE` exactly; the npc-shortfall count on
7205 reproduces 7205's recorded 1,600.

**Detection (46 roots incl. CCO live):** the ONLY winner that changes is
7622 (`plaintext` 0.50 -> `patch7622` 0.95, margin 0.45). patch7622 scores
0.0 on every other install. Exactly one install stamps `7622` (the folder
`Clients/7622`); no `Clients/7632` folder remains. 7867, 4274, 6256, 6271,
DuueWanderer7952 and ThroneOfKings7939 still go to `plaintext` at 0.50 --
not this plugin's.

**No lost subjects (FINALIZE-C 5b), 2026-09-19 on 7622:** the previous
winner `plaintext` has 0 ok subjects (its one entry is the refusal "declares
no browsable tables"); patch7622 has 238 of 240. Set difference
(plaintext ok) - (patch7622 ok) = EMPTY.

**Containers:** `_discover_archives` = `['c3.tpi', 'data.tpi']` (CONTROLS:
7275 -> the WDF pair; 7878 -> four `.tpi` incl. `c31`/`data1`). 0 `.dbc`, no
overlay pair. `c3.tpi` sha256 `4b4bde688f18`, `data.tpi` `9096a9e0ed08`,
full-hash IDENTICAL to 7562's, 7589's and 7878's. `c3.tpd` 1,076,058,840 B
and `data.tpd` 896,279,080 B EQUAL to all three by size + head/tail 16 MB
(sampled, NOT a full hash).

**Tables by sha256[:12], 7622 against 7205 / 7589 / 7878:**

    itemtype.dat      aaf676c958fe 14,970,839  == 7589; != 7205, 7878
    ItemtypeSub.dat   ebbbe5811e71    645,463  differs from all three
    MagicType.dat     881853d9116c    895,374  differs from all three
    monster.dat       76bc5cecfe13  1,612,307  differs from all three
    mounttype.dat     2d8a0c650f17    458,524  differs from all three
    npc.ini           f0465c49df14    739,131  differs from all three
    levexp.dat        4482cfaaec7d      4,030  == all three
    SlotNpc.ini       c4e23de07332      3,959  == all three
    region.ini        318a8e4f3494     18,383  == 7589
    3DSimpleObj/3dobj/3dtexture/3dmotion/armor.ini == FROZEN_LOOKUPS (all OK)

`ini/` 543 entries against 7589's 539: new here `casual_game_cfg.ini`,
`gouyu_xuzuo_type.dat`, `rune_effect.dat`, `texasraffle.lua`; NOTHING in
7589 is missing here (so `GONE_SINCE_7205` carries by measurement).

**Appearance tables (`part_tables()`, bare), 7622 == 7589 row for row:**
armet 1,168, body 955, l/r_weapon 4,828, mount 1, head 0, misc 0 (the frozen
`.ini`; no twin). The mount slot is effectively empty: a known limit of the
client.

**KNOWN LIMIT, and a FINDING: the appearance-resolution hurdle.**
`AssetRoot.profile_match` on 7622: **INCONCLUSIVE** -- 8.33% of the 24
pinned rows bare, 70.83% with the 7878 profile's corpus (sample digest
`083c0a317b43`, 13,902 declared rows). 7589 measured identically. Before the
rename this install was recorded as QUALIFIES at 94-99% (`core/coassets.py`
`ProfileMatch`). The run was in the worktree `C:/Claude/co-p7622`, which
holds no `out/garment/overlay-7878*` (the profile's `overlay_globs`), so the
lower full rate is INFERRED to be the missing overlay, not the client; the
bare 8.33% is the client either way. CONTROLS: 7878 NAMED; 5065 NO_HURDLE
(95.83% bare).

**NPC shortfall:** 3,366 `SimpleObjID` rows; 1,888 resolve via the frozen
`3DSimpleObj.ini` (191 sections), 1,478 do not.

**Script surface:** 460 `.lua` (17,317,305 B), 31 root DLLs (7589: 446 /
28; 7205: 224 / 37).

**`ini/**/*.dat`:** block96 450, rar-mangled 11, empty 10, rsa-mysqldump 10,
plaintext 7, binary-plain 6, unknown 6, block96-candidate-refuted 3,
tq-stream 2, shift-obfuscated 1. 0 CODEC MISMATCH. Every declared `.dat`
spec is shipped (240 specs). **block96: 2,387,977 blocks, 335 unknown
(99.99% known); 449 files fully covered, 0 partial, 1 at zero** -- the 335
are ALL `levexp.dat` (byte-identical to 7205's, held out of the dictionary
by design). `ServerPlay.dat` (6,206 B) is block96-candidate-refuted,
undeclared.

**That 99.99% is SELF-REFERENTIAL: 93.10% leave-one-out.** The shipped
block dictionary already contains 7622's own decrypted corpus
(`derived/7622-dat-decrypted`); 152,469 of its blocks were taught only by
it. Rebuilt WITHOUT that corpus, 7622 reads 93.10% of its 2,387,977 blocks
-- the number comparable with any build that has no corpus of its own.
Worst files then: battlepass_score_reward 32.85%, luaui/* 34-50%,
client_config 46.42%. `py -3 tools/loo_coverage.py 7622 --control 7589`,
measured 2026-09-19.
 The collection dictionary knowing this build almost whole is
consistent with this client's own oracle output being in it (a
`derived/` directory under its pre-rename folder name exists) -- INFERRED
from the directory's existence, not traced. 7589 in the same run still
reads its old partial counts (monster 2,270, item:sub 103), so 7622's
numbers are NOT 7589's.

**catalogs():** 240 subjects, 238 with a control; refusals `action`
(binary layout not established) and `levexp` (held out), as on 7205 and
7589. Headline rows, 7622 (7589 / 7205): item 50,834 (50,834 / 44,684),
item:sub 1,878 (103 / 633), magic 4,532 (4,179 / 2,907), magic:op 980
(358 / 979), monster 4,780 (2,270 / 3,670), mount 1,892 (1,888 / 1,819),
npc:npc.ini 3,363 (3,303 / 2,904), map:dest 1,190 (1,068 / 936),
instancetype 540 (47 / 340), prof_lev_up 626 (199 / 549),
texas_match_type 77 (1 / 82). Item noise ceiling: 50,834 <= 149,708. None
of 7589's `KNOWN_LIMITS` is a limit on 7622. These counts were measured
under latin1; the test pins them under `gbk` (below).

**Census:** `DAT_CENSUS_ERA = False`, and the `7622` slot in
`tools/dat_census.py` stays commented. The `.dat` ARE block96 (450 files,
99.99% known), but 7622 ships `combat_gear.dat` (93,653 B, 822 rows), and
`tests/test_dat_census.py` pins that file to 7189 alone -- joining `BUILDS`
reds it, exactly as it did for 7589. Those pins are not this plugin's to
edit. Figures kept as `DAT_MEASURED_7622`.

TEXT ENCODING: `gbk`, PROVEN PER TABLE (owner's "fix lineage wide" ruling)
--------------------------------------------------------------------------
`measure_7622.py` section 4c read every declared text table's NAME field
through this plugin's own `browse()` with the encoding forced to latin1 (a
byte bijection, so `label.encode("latin1")` is the raw field) and decoded
each high-byte field with errors="strict" as GBK. PROBE: strict GBK rejects
`ff fe`, `81 20` and a lone `a1`, and accepts a known 3-CJK string. 7205
CONTROL, same instrument: 11 tables GBK, 0 strict failures. On 7622,
verdict **GBK PROVEN**: 8 tables GBK (strict, CJK in every high-byte field),
0 tables with a strict failure, 226 with no high byte at all (they say
nothing), 1 refused (levexp). Per table (fields / high-byte / GBK ok / CJK
fields):

    3dtexture            8,793 /  6 /  6 /  6   GBK
    classdesc              123 / 25 / 25 / 25   GBK
    npc:NpcX.ini         1,020 /  9 /  9 /  9   GBK
    npc:npc.ini          3,363 / 27 / 27 / 27   GBK
    npc:terrainnpc.ini     168 / 51 / 51 / 51   GBK
    shop                   165 / 67 / 67 / 67   GBK
    statustips             273 /  3 /  3 /  3   GBK
    txtemotion               9 /  9 /  9 /  9   GBK
    item                50,832 /  2 /  2 /  1   GBK-decodable, CJK in 1 of 2
    map:dest             1,190 /  7 /  7 /  6   GBK-decodable, CJK in 6 of 7
    achievement            388 /  2 /  2 /  0   GBK-decodable, no CJK

FINDINGS, not tuned away: `item`, `map:dest` and `achievement` have
high-byte name fields that decode as strict GBK but not all carry CJK, so
they are GBK-CONSISTENT, not GBK-PROVEN; 7205 shows the same shape for
`achievement`, `item:sub` and `map:dest`. `gamemapex` and `strres`, GBK on
7205, have no GBK-proven name field on 7622 (`strres.ini` is gone since
7205). The wider `ini/*.ini` sweep (section 4b: 112 ASCII, 16 strict UTF-8,
77 strict GBK) found 3 files that are NOT strict GBK: `ChatFilter.ini`,
`Eliteanpt.ini`, `UStrRes.ini` -- none is a declared table's name source,
and under `gbk` an undecodable byte there would read as U+FFFD. CONTROL:
7878's `SlotNpc.ini` decodes GBK.

STILL NOT MEASURED
------------------
Full hashes of the `.tpd` payloads; `ITEM_COLUMN_AGREEMENT`,
`UNOPENED_DAT_FAMILIES`, `BROWSE_IMPACT`, `RUNEEFFECT_REPEATS`,
`FORMERLY_UNCOVERED`; exported Lua globals; the hurdle with the 7878
overlay present.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch7205 import Patch7205                # noqa: E402
from plugins.patch7589 import Patch7589                # noqa: E402

#: The stamp this plugin owns, and the only thing `confidence` reads.
STAMP = "7622"


def _version(root: Path) -> str:
    """`version.dat`, `b'7622'` on this install (fingerprints.json, as 7632)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch7622(Patch7589):
    name = "patch7622"
    label = "Official patch client 7622"
    origin = "official"
    aliases = ("7622",)

    notes = (
        "A .tpd-only client (c3/data .tpd+.tpi, no .wdf, no overlay pair) "
        "with ZERO compiled .dbc tables, one patch step after 7589: the same "
        "c3.tpi/data.tpi and the same itemtype.dat as 7589 (fingerprints), "
        "newer MagicType/monster/mounttype/GameMap. Content tables are "
        "7205's block96 specs over ini/*.dat, via 7589; 7878's decrypted "
        "tables are never served. block96 99.99% known; item 50,834, monster "
        "4,780, item:sub 1,878 rows. Text is GBK (proven per table). "
        "Appearance hurdle: 8.33% of declared rows resolve bare. Its "
        "folder was Clients/7632 until 2026-09-19; version.dat says 7622."
    )

    #: sha256 prefixes of the two indices, INHERITED from 7589 by measured
    #: identity: fingerprints.json gives the same blake2b for both files on
    #: 7589 and 7622, and `measure_7589.py` full-hashed them equal.
    INDEX_IDENTICAL_TO = ("7280", "7336", "7562", "7589", "7878")

    # -- measured on 7622 by measure_7622.py (2026-09-19) -------------------
    #: `SimpleObjID=` rows in npc.ini against the frozen `3DSimpleObj.ini`.
    #: The instrument reproduces 7205's 1,600 exactly.
    NPC_COVERAGE = {"rows": 3366, "sections": 3366, "resolved_ini": 1888,
                    "unresolved": 1478, "resolved_compiled": None,
                    "simpleobj_sections": 191}
    SCRIPT_SURFACE = {"lua": 460, "dll": 31, "lua_bytes": 17317305,
                      "exported_globals": None,
                      "at_7205": {"lua": 224, "dll": 37,
                                  "lua_bytes": 19094816}}
    #: None of 7589's known limits is a limit on 7622 (monster 4,780,
    #: item:sub 1,878, magic:op 980 ...). Empty, measured -- not None.
    KNOWN_LIMITS = {}
    PART_TABLE_ROWS = {"armet": 1168, "body": 955, "l_weapon": 4828,
                       "r_weapon": 4828, "mount": 1, "head": 0, "misc": 0}
    DAT_MEASURED_7589 = None
    #: measure_7622.py section 4, over ini/** RECURSIVELY (not the scope of
    #: 7205's `BLOCK96_CENSUS`). The 335 unknown blocks are all levexp.dat.
    DAT_MEASURED_7622 = {"files": 450, "blocks": 2387977, "unknown": 335,
                         "fully_covered": 449, "partial": 0, "uncovered": 1}
    #: sha256[:12] measured on 7622, with the comparison.
    TABLE_IDENTITY = {
        "ini/itemtype.dat": ("aaf676c958fe", "== 7589; != 7205, 7878"),
        "ini/ItemtypeSub.dat": ("ebbbe5811e71", "differs from 7205/7589/7878"),
        "ini/MagicType.dat": ("881853d9116c", "differs from 7205/7589/7878"),
        "ini/monster.dat": ("76bc5cecfe13", "differs from 7205/7589/7878"),
        "ini/mounttype.dat": ("2d8a0c650f17", "differs from 7205/7589/7878"),
        "ini/npc.ini": ("f0465c49df14", "differs from 7205/7589/7878"),
        "ini/levexp.dat": ("4482cfaaec7d", "== 7205, 7589, 7878"),
        "ini/SlotNpc.ini": ("c4e23de07332", "== 7205, 7589, 7878"),
        "ini/region.ini": ("318a8e4f3494", "== 7589"),
    }

    #: 7589's 18, plus the two measured in 7622 and not in 7589.
    #: UNDECLARED: none has been given a spec.
    NEW_DAT_SINCE_7205 = Patch7589.NEW_DAT_SINCE_7205 + (
        "gouyu_xuzuo_type.dat", "rune_effect.dat")
    #: MEASURED: 543 ini/ entries vs 7589's 539; nothing of 7589's missing.
    NEW_SINCE_7589 = ("casual_game_cfg.ini", "gouyu_xuzuo_type.dat",
                      "rune_effect.dat", "texasraffle.lua")

    ITEMTYPESUB_BYTES = 645463

    #: MEASURED on 7622 (section 3b), in a worktree WITHOUT the profile's
    #: overlay: INCONCLUSIVE. Before the rename: QUALIFIES, 94-99% full.
    ASSET_PROFILE_HURDLE = {
        "verdict": "INCONCLUSIVE", "profile": "7878",
        "bare_rate": 0.0833, "full_rate": 0.7083, "sampled": 24,
        "population": 13902, "sample_sig": "083c0a317b43",
        "overlay_present": False,
        "recorded_before_rename": "QUALIFIES, 8-19% bare, 94-99% full",
    }

    #: The owner's "fix lineage wide" ruling, PROVEN on 7622 by section 4c
    #: (strict GBK, CJK present, reject probe, 7205 control). Per-table
    #: verdicts in GBK_BY_TABLE.
    TEXT_ENCODING = "gbk"
    #: subject -> (name fields, high-byte fields, strict-GBK ok, CJK fields,
    #: verdict). Every high-byte field decoded; 0 strict failures.
    GBK_BY_TABLE = {
        "3dtexture": (8793, 6, 6, 6, "GBK"),
        "classdesc": (123, 25, 25, 25, "GBK"),
        "npc:NpcX.ini": (1020, 9, 9, 9, "GBK"),
        "npc:npc.ini": (3363, 27, 27, 27, "GBK"),
        "npc:terrainnpc.ini": (168, 51, 51, 51, "GBK"),
        "shop": (165, 67, 67, 67, "GBK"),
        "statustips": (273, 3, 3, 3, "GBK"),
        "txtemotion": (9, 9, 9, 9, "GBK"),
        "item": (50832, 2, 2, 1, "GBK-decodable, CJK in some fields only"),
        "map:dest": (1190, 7, 7, 6, "GBK-decodable, CJK in some fields only"),
        "achievement": (388, 2, 2, 0,
                        "GBK-decodable, CJK in some fields only"),
    }
    #: `ini/*.ini` that are NOT strict GBK (section 4b). A finding.
    NOT_GBK_INI = ("ChatFilter.ini", "Eliteanpt.ini", "UStrRes.ini")

    #: Literal False: the test uses `is`. block96 (450 files, 99.99% known),
    #: but 7622 ships combat_gear.dat and test_dat_census pins it to 7189.
    DAT_CENSUS_ERA = False

    UNCOVERED_TABLES = {
        "levexp.dat": "byte-identical (sha256 4482cfaaec7d, 4,030 B) to "
                      "7205's, 7589's and 7878's, MEASURED on 7622: held "
                      "out of the dictionary by design (0 of 335 blocks).",
    }
    UNDECLARED_DAT = {
        k: "[measured on 7205, NOT re-taken on 7622] " + v
        for k, v in Patch7205.UNDECLARED_DAT.items()}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7622` at 0.95, else 0.0.

        Nothing but the stamp separates 7622 from 7589 / 7562: the same
        `.tpi` bytes, no `.dbc`, the same frozen lookups, the same
        `itemtype.dat`. A folder NAME is never read: this install was
        `Clients/7632` until 2026-09-19.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609, 7205 and 7589 "
                "(unverified on any of them, and 7622's archives are .tpd, "
                "not the WDF pair the sets were read from)", "inferred")

    def table_quirks(self):
        """7589's quirks, each marked as 7589's, plus 7622's own."""
        q = {k: ("[recorded for 7589, NOT re-measured on 7622] " + v)
             for k, v in super().table_quirks().items()}
        q["one patch step after 7589"] = (
            "fingerprints (as Clients/7632): c3.tpi, data.tpi, itemtype.dat, "
            "3dobj.ini, 3dtexture.ini, RolePart.ini identical to 7589's; "
            "MagicType.dat, monster.dat, mounttype.dat, GameMap.dat, "
            "Server.dat, play.exe differ. No overlay pair (7878 has one).")
        q["ItemtypeSub.dat is 645,463 bytes"] = (
            "7589 ships 407,962 B and 7878 464,778 B. Not yet decoded here; "
            "an item:sub count is not comparable to 7205's 633 until it is.")
        q["the appearance-resolution hurdle"] = (
            "MEASURED on 7622: 8.33% of 24 pinned declared appearance rows "
            "resolve bare; with the 7878 profile's corpus 70.83% "
            "(INCONCLUSIVE; measured without the profile's overlay). The art "
            "is not shipped in this install.")
        q["name fields are GBK"] = (
            "MEASURED per table: 8 tables strict GBK with CJK, 0 strict "
            "failures; item/map:dest/achievement GBK-decodable without CJK "
            "in every field. ChatFilter/Eliteanpt/UStrRes.ini are not GBK.")
        q["the folder name is not the patch"] = (
            "Clients/7632 until 2026-09-19 (and Clients/7682, deleted, was "
            "the same client); version.dat has always said 7622.")
        return q


PLUGIN = Patch7622()
