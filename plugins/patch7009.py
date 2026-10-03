#!/usr/bin/env python3
r"""
patch7009 -- official Conquer Online patch 7009: a block96-era client with
6609's art, served until now by `patch6907`'s 0.55 sibling bid.

Facts come from three sources, each named where it is used:
`co-pnd/docs/patchnotes/fingerprints.json` (per-install blake2b), earlier
measurements this repo records against 7009, and `measure_7009.py`
(worktree root, not committed; run by the Director 2026-09-19, EXIT 0,
49.7 s) -- see MEASURED BY measure_7009.py below.

WHY THE PARENT IS `Patch6907`, AND WHY THE CLASS IS THIN
---------------------------------------------------------
7009 is one of the ten installs `Patch6907` was written for. Its docstring,
`SPECS_DAT_CENSUSED`, `HAIRFACE_COLUMNS` and `UNDECLARED_DAT` all name 7009
explicitly and carry 7009-specific measurements (listed under MEASURED
ELSEWHERE below), and `tools/dat_census.py` `BUILDS` already contains
"7009". The spec list, the 0-byte `_present` rule, the block96 `load_table`
and the recovery reporting were all written with this build in the room.

So this class overrides IDENTITY -- name, label, aliases, the stamp and a
stamp-only `confidence` -- plus the three codecs measured wrong for 7009's
disk (`CODEC_ON_DISK`, below), and states what it inherits. It declares no
new subjects: the parent already filters its censused lists to what is on
disk and re-declares a 0-byte file as `empty` per build.

`Patch6609` was the table's other candidate and is wrong for the content:
its `.dat` specs are `tq-stream`, and `tqdat` returns noise that parses on a
block96 file (`patch6907`'s docstring, the `mount` "1 row, CONTROL PASSED"
case).

MEASURED (fingerprints.json, blake2b, 27 significant files per install)
-----------------------------------------------------------------------
* **`version.dat`: 4 bytes, stamp `7009`.** The only discriminator.
* **The art layer is 6609's and 6907's, byte for byte.** All twelve `.dbc`
  (`3DEffect`, `3DEffectobj`, `3DObj`, `3DSimpleObj`, `3DTexture`,
  `3dmotion`, `armet`, `armor`, `mount`, `mountmotion`, `weapon`,
  `weaponmotion`), `3dobj.ini`, `3dtexture.ini`, and the `c3.wdf` / `data.wdf`
  header+index (10,274 and 14,739 entries; payload NOT hashed) are identical
  to 6609's, 6907's, 6968's and 7065's. `RolePart.ini` matches 6907/6968/7065
  and not 6609.
* **`ini/itemtype.dat` is byte-identical to 6968's** (b2 `afde1367...`,
  10,822,489 B) and differs from 6907's and 7065's. That is the strongest
  handle this build offers: whatever the block96 reader returns for `item`
  on 6968 it must return exactly on 7009. `tests/test_patch7009.py` uses it
  as a floor-free control.
* **`play.exe` is identical to 6907's and 6968's** (1,737,264 B); 7065's
  differs.
* **Changed from 6968, and from every neighbour**: `magictype.dat`
  (478,710 B), `monster.dat` (864,634 B), `mounttype.dat` (435,854 B),
  `GameMap.dat` (15,568 B), `Server.dat` (4,608 B).

MEASURED ELSEWHERE IN THIS REPO, AGAINST 7009 (cited, not re-measured)
-----------------------------------------------------------------------
* **Detection before this plugin: `patch6907` at 0.55**, the sibling bid
  (`patch6907.Patch6907.confidence`, `tests/test_patch6907.
  TheTenUnstampedSiblingsAreClaimedToo`).
* **`monster.dat` is the most damaged table in the era**: block coverage
  0.9393 against 0.3202 recovered, 914 units, 4,376 unknown blocks -- the
  ONE table on the box `test_patch6907` still uses to show that block
  coverage is not row recovery. A KNOWN LIMIT of the 7878-derived
  dictionary, not of this plugin.
* `award_config.dat` refuses at 29.1% block coverage (the only one of the
  ten `patch6907` builds where it does).
* `hairface_storage_type.dat`: 186 rows, 35 too short to reach the name
  column (widths 2x1, 3x34, 9x5, 10x7, 11x139).
* `instance_prize.dat` and `instance_star_condition.dat` ship as 0 bytes
  here while 7065-7189 carry 480-794 rows -- the case `Patch6907._present`
  was written for.
* `process_goal.dat` 9 rows and `process_task.dat` 85, both damaged (29 and
  191 on later builds).
* `silent.dat` classifies `block96` from 7009 (`tq-stream` on 6907/6968);
  `EmotionIco.dat` is `unknown` to `inidat` on 7009-7135.
* 65 subjects labelled with numbers before the column work, against 66 on
  the other nine.

INFERRED (not measured, and said so)
------------------------------------
* That every `SPECS_6907` codec is still the disk's codec on 7009. The
  parent's `test_plugin_catalogs` invariant enforces that per build and 7009
  was in its sweep, but that is someone else's run, not this plugin's.
* The column declarations of the tables that changed from 6968.

MEASURED BY measure_7009.py (2026-09-19; Clients/7009 unless named)
--------------------------------------------------------------------
* **Detection, 46 installs** (every `Clients/` folder after the owner's
  rename, plus the live CCO install), `plugins._rank` with and without this
  plugin: the winner or margin changes on 7009 ONLY -- `patch7009` 0.95,
  margin 0.40, where it was `patch6907` 0.55. `patch7009` scores 0 on the
  other 45. Controls: 6907 -> `patch6907` 0.95 PASS; 7205 -> `patch7205`
  0.95 PASS.
* **Containers:** `c3.wdf` + `data.wdf`, no `.tpd`. `part_tables()`: nine
  slots, identical to 6907's row for row.
* **block96, POSITIVE CONTROL FIRST.** 6907: 70 files, 762 unknown blocks,
  68 full, 0 partial, zero-known exactly `levexp.dat` + `ServerPlay.dat` ->
  PASS. Twin 6968: `itemtype.dat` (901,874 blocks, 0 unknown, 38,352 units)
  EQUALS 7009's -> PASS.
* **7009: 84 block96 files, 1,342,095 blocks, 17,100 unknown (98.73%
  known); 60 full, 22 PARTIAL, 2 zero-known** (`battlepass_season.dat`,
  `levexp.dat`). Families: block96 84, rar-mangled 11, rsa-mysqldump 10,
  empty 8, plaintext 7, binary-plain 6, tq-stream 3, unknown 3,
  shift-obfuscated 1, block96-candidate-refuted 1. block96 is NOT refuted,
  so `DAT_CENSUS_ERA` stays True.
* **The 22 partial tables** (% of blocks known, units): award_config 29.06
  (0), battlepass_score_reward 5.67 (18), battlepass_task 53.85 (8),
  coat_storage_type 99.18 (708), exchange_shop_goods 77.11 (448),
  exchange_shop_goods_ex 60.38 (9), hairface_storage_type 84.29 (186),
  instance_enter_condition 90.32 (57), instancetype 80.74 (43), ItemtypeSub
  93.68 (1,811), MagicType 98.66 (2,251), MapDestination 99.34 (680),
  marketing_active 54.29 (32), monster 93.93 (914), mounttype 99.99 (1,799),
  newslot_type 90.74 (29), nosuch_config 52.94 (8), Operating_Prize 70.12
  (249), process_goal 82.52 (11), process_task 92.20 (97),
  task_reward_type 98.19 (196), title_type 98.74 (128). A KNOWN LIMIT of
  the 7878-derived dictionary, recorded, not tuned.
* **`monster.dat`: coverage 0.9393, recovered 0.3202, 914 units, 4,376
  unknown blocks** -- the repo's figure reproduced exactly.
* **CODECS: three `SPECS_6907` declarations are wrong for 7009's files**
  (`CODEC_ON_DISK`): ItemtypeSub `block96` (declared `empty`),
  MapDestination `block96` (declared `block96-candidate-refuted`),
  ServerPlay `block96-candidate-refuted` (declared `block96`). Corrected;
  `item:sub` now READS 1,811 rows (names in column 1 on every row), so
  this plugin reads 229 ok subjects where `patch6907` reads 228 -- lost
  none, gained `item:sub`. The paragraph below is measure_7009.py's run,
  BEFORE that correction.
* **`catalogs()`: 236 subjects, 228 ok** (6907: 222 / 218). `patch7009`
  and `patch6907` on 7009 agree on every subject's ok flag and row count;
  the ok-subject SET difference (patch6907 minus patch7009) is EMPTY, 228
  vs 228 -- no subject is lost by claiming this build. Rows (6907 -> 7009):
  item 38,346 -> 38,352 (= 6968's), monster 2,199 -> 884, magic 2,409 ->
  2,251, mount 1,790 -> 1,799, map:dest 567 -> 678, magic:op 979 -> 979,
  achievement 390 -> 391, title_type 106 -> 128, hairface_storage_type 408
  -> 186, instancetype 43, process_goal 11, process_task 97.
* **FINDINGS, each a known limit** (`KNOWN_LIMITS`):
  - `award_config` is ok on 6907 (108 rows) and REFUSED here (29.1% of
    blocks);
  - `battlepass_season` is ok on 6907 (1 row) and REFUSED here -- 0 of its
    blocks are known on 7009;
  - `instance_prize` / `instance_star_condition` REFUSED as 0-byte files;
  - `item:sub`, `levexp`, `serverplay`, `action` refuse on 6907 too.
* **`texas_match_prize` 81 -> 5,524 rows tripped the ">3x = wrong-cipher
  noise" screen and is NOT noise**: `patch6907.SPECS_DAT` already records
  that 6907's copy is 81 rows of 12 and the other nine builds' is ~5,400
  rows of 6. A different file, read with a re-decode control.
* **Encoding: NOT PROVEN, `TEXT_ENCODING` stays latin1.** `codepage.ini` is
  `1256`. `SlotNpc.ini` (3,959 B, 102 high bytes) and `npc.ini` (472,472 B,
  644 high bytes) decode strictly as BOTH gbk and cp1256, so the probe
  discriminates nothing; there was no reject-capable probe, no CJK check
  and no 7205 control. The owner's GBK ruling is for the 7205 lineage; this
  build is before 7205 and keeps latin1 until a positive proof exists.
* `npc.ini` section headers: 6907 2,325 . 6968 2,426 . 7009 2,536.

"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                          # noqa: E402

from plugins.patch6907 import Patch6907, _version      # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "7009"


class Patch7009(Patch6907):
    """Official patch 7009: 6907's profile, claimed by stamp."""

    #: Explicit rather than inherited, so the answer names the build it is
    #: about. MEASURED block96 (84 `.dat`, 98.73% of 1,342,095 blocks known),
    #: and 7009 is in `tools/dat_census.py` `BUILDS` already, where
    #: `tests/test_dat_census` asserts the detected plugin's marker `is True`.
    DAT_CENSUS_ERA = True

    name = "patch7009"
    label = "Official patch client 7009 (block96 .dat era)"
    origin = "official"
    aliases = ("7009",)
    STAMP = STAMP

    notes = (
        "6609's art layer byte for byte (all twelve .dbc and the c3.wdf/"
        "data.wdf index, by fingerprint) with the block96 .dat cipher of the "
        "6907 era. itemtype.dat is byte-identical to 6968's. Read through "
        "patch6907's specs and the 7878-derived block dictionary; monster.dat "
        "is the most damaged table of the era here (93.9% of blocks known, "
        "<=32.0% recovered), and instance_prize/instance_star_condition ship "
        "as 0-byte files. The dictionary knows 98.73% of this build's blocks; "
        "award_config (29.1%) and battlepass_season (0%) refuse here though "
        "6907 reads them. Before this plugin 7009 was claimed by patch6907's "
        "0.55 sibling bid; this plugin reads all 228 subjects that did, plus "
        "item:sub (1,811 rows) through ItemtypeSub.dat's real block96 codec."
    )

    #: `ini/*` files MEASURED byte-identical (blake2b, fingerprints.json) to
    #: the named neighbour. Any inherited claim resting on one of these rests
    #: on identity of bytes, not on argument.
    IDENTICAL_TO = {
        "6968": ("itemtype.dat",),
        "6609": ("3DEffect.dbc", "3DEffectobj.dbc", "3DObj.dbc",
                 "3DSimpleObj.dbc", "3DTexture.dbc", "3dmotion.dbc",
                 "armet.dbc", "armor.dbc", "mount.dbc", "mountmotion.dbc",
                 "weapon.dbc", "weaponmotion.dbc", "3dobj.ini",
                 "3dtexture.ini"),
    }

    #: Headline `ini/*.dat` MEASURED different from 6968's (fingerprints).
    CHANGED_FROM_6968 = ("magictype.dat", "monster.dat", "mounttype.dat",
                         "GameMap.dat")

    #: `latin1`, inherited and NOT changed: MEASURED, `SlotNpc.ini` and
    #: `npc.ini` decode strictly as both gbk and cp1256, so there is no
    #: positive GBK proof on this build (see the module docstring).
    TEXT_ENCODING = "latin1"

    #: MEASURED by `measure_7009.py`, `block96.read_table(...).recovery`:
    #: `(blocks known %, units)` for the tables whose limits are findings.
    #: `monster.dat` reproduces `tests/test_patch6907`'s figure exactly.
    MONSTER_RECOVERY = {"coverage": 0.9393, "recovered": 0.3202,
                        "units": 914, "unknown_blocks": 4376}

    #: Subjects that refuse on 7009, MEASURED, with why. The first two READ
    #: on 6907 and refuse here: a finding about this build's bytes against
    #: the 7878-derived dictionary, not a reader defect -- `patch6907` refuses
    #: them on 7009 identically. Recorded, never tuned away.
    KNOWN_LIMITS = {
        "award_config": "29.06% of blocks known; 6907 reads 108 rows",
        "battlepass_season": "0% of blocks known; 6907 reads 1 row",
        "instance_prize": "0-byte file on this build",
        "instance_star_condition": "0-byte file on this build",
        "levexp": "0% of blocks known, held out of the dictionary",
        "serverplay": "no route opens it (also refused on 6907)",
        "action": "binary record table, columns unknown (as on 6907)",
    }

    #: **THREE `SPECS_6907` codecs are wrong for 7009's files**, MEASURED by
    #: `measure_7009.py` (`inidat.classify` on Clients/7009) against 6907's
    #: declarations -- the same three 6968's finalize found on its build,
    #: with ONE difference: ServerPlay here is `block96-candidate-refuted`,
    #: not `unknown`.
    #:
    #:   ItemtypeSub.dat     declared `empty` (6907 ships 0 bytes); 7009's is
    #:                       `block96`, 106,773 blocks, 93.68% known, 3,125
    #:                       lines, 1,330 intact, 1,811 units. So `item:sub`
    #:                       is READ here instead of refused as 0 bytes.
    #:   MapDestination.dat  declared `block96-candidate-refuted` (6907's
    #:                       1-byte-tail false negative); 7009's is `block96`,
    #:                       9,120 blocks, 99.34% known, 680 units. The reader
    #:                       path is the same for both codecs; only the label
    #:                       was wrong.
    #:   ServerPlay.dat      declared `block96`; 7009's is
    #:                       `block96-candidate-refuted` (5,329 B). Still
    #:                       refused with 6907's reason -- no route opens it.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.BLOCK96_REFUTED,
    }

    #: `item:sub`'s columns, id 0 / name 1: `patch7205`'s declaration for the
    #: same file, and what 6968 measured on its copy. Checked on 7009 by
    #: `tests/test_patch7009.CodecsAreWhatTheDiskIs`.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    def table_specs(self, root):
        """6907's specs with `CODEC_ON_DISK` applied -- each correction ONLY
        where `inidat` classifies the file on disk that way today, so a
        different file under the same name keeps 6907's declaration."""
        out = []
        for s in super().table_specs(root):
            want = self.CODEC_ON_DISK.get(s.filename.lower())
            if want is None or s.codec == want:
                out.append(s)
                continue
            p = self._find_table(s, root)
            if p is None or inidat.classify(p.read_bytes(),
                                            p.name).family != want:
                out.append(s)
                continue
            if s.filename.lower() == "itemtypesub.dat":
                out.append(replace(s, codec=want, refusal=None,
                                   columns=self.ITEMTYPESUB_COLUMNS))
            else:
                out.append(replace(s, codec=want))
        return tuple(out)

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7009`, at 0.95, and nothing else.

        **Overridden, not inherited, and it has to be.** `Patch6907.
        confidence` also makes a 0.55 SIBLING bid for any unstamped block96
        client above 6868 -- inherited here, it would put a second 0.55 on
        6907, 6968, 7065 ... and tie with the parent on every one of them.
        Same rule as `patch7205` / `patch7217`: the formats cannot separate
        these builds (identical `.dbc`, identical archives, same cipher), so
        only the stamp does. 0.95 beats the parent's 0.55 on 7009 alone.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def colour_provenance(self):
        return ("colourway inherited from 6090 via 6609 and 6907 "
                "(unverified on any of them)", "inferred")

    def table_quirks(self):
        q = super().table_quirks()
        q["the figures in the quirks above are 6907's"] = (
            "Every number quoted in the inherited quirks was measured on "
            "Clients/6907. 7009 shares the mechanism -- same .dbc bytes, same "
            "block96 codec -- and its itemtype.dat is byte-identical to "
            "6968's, not 6907's. 7009's own numbers (measure_7009.py) are in "
            "the module docstring, MONSTER_RECOVERY and KNOWN_LIMITS.")
        q["monster.dat on 7009 is the era's worst table"] = (
            "block coverage 0.9393 against at most 0.3202 recovered, 914 "
            "units, 4,376 unknown blocks (tests/test_patch6907, the one table "
            "on the box that still shows the coverage/recovery gap). A limit "
            "of the 7878-derived dictionary, recorded, not tuned.")
        return q


PLUGIN = Patch7009()
