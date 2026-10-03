#!/usr/bin/env python3
r"""
patch7110 -- official Conquer Online patch 7110: a block96-era sibling of
6907, 6609's art with the 96-bit block cipher on `ini/*.dat`.

A THIN SUBCLASS OF `Patch6907`, AND WHY THAT PARENT
----------------------------------------------------
Before this file, `plugins.detect(Clients/7110)` fell to `patch6907`'s 0.55
SIBLING bid -- 7110 is one of the ten unstamped builds `Patch6907.confidence`
names, and `tools/dat_census.py` `BUILDS` already lists it. So 7110 has been
read through the 6907 profile all along; this plugin makes that claim
stamp-exact (0.95) and gives the build a name. The ONLY change to reading
is `CODEC_ON_DISK`: three of 6907's codec declarations are wrong on 7110's
disk (measured), and correcting them opens `item:sub`. The alternatives were weighed and refused:

* `Patch7205` -- the next official plugin UP. It carries 7205's own census
  (`SPECS_7205_CENSUSED`, `BLOCK96_CENSUS`, `IDENTICAL_TO_...`) pinned from
  7205's bytes; 7110 shares neither `itemtype.dat` nor `monster.dat` with
  7205 (fingerprints below), so every one of those constants would be a
  7205 number published under a 7110 name.
* `Patch6090` / `Patch6609` -- the TQ-stream era. Their `.dat` specs are
  `tq-stream`, and `tqdat` does not raise on a block96 file; it returns noise
  that parses (the `patch6907` docstring has the 0.45 before-state).

`Patch6907` is the one whose `table_specs()` is FILTERED TO DISK, so a
spec 6907 declares and 7110 does not ship reads as "not present" rather
than as a borrowed claim. That filter is what makes a thin subclass safe.

MEASURED (code phase, 2026-09-19 -- from
`C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` only; blake2b-128 of
each significant file, taken before the owner's 2026-09-19 deletions/rename,
which did not touch 7110 or its neighbours)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **`version.dat` is 4 bytes, `7110`** (no CRLF). The stamp is the only
  discriminator `confidence()` uses.
* **The art layer is 6907's, which is 6609's.** All twelve `.dbc`
  (`3DEffect`, `3DEffectobj`, `3DObj`, `3DSimpleObj`, `3DTexture`,
  `3dmotion`, `armet`, `armor`, `mount`, `mountmotion`, `weapon`,
  `weaponmotion`) hash identical to 6907's -- the same digest on 20
  installs each. `c3.wdf` (359,069,116 B, 10,274 entries) and `data.wdf`
  (392,245,257 B, 14,739 entries): header+index digest identical to 6907's
  (28 installs; the fingerprint hashes header and index, NOT the payload).
  `3dobj.ini` / `3dtexture.ini` identical (41 installs); `RolePart.ini`
  identical to 6907 6968 7009 7065 7083 7135 7170 7182 (not 7205).
* **`ini/itemtype.dat` is byte-identical (digest) to 7065's, 7083's and
  7135's** -- 11,877,944 B; NOT 6907's (10,820,377 B). That is the group
  `docs/dict_merge_2026-09-05.md` names (7065, 7083, 7110, 7135 at 89.9%
  block coverage under the merged dictionary of that date; the dictionary
  has grown since, so that figure is history, not a pin).
* **Unique to 7110 in the whole corpus**: `monster.dat` (1,043,429 B),
  `magictype.dat` (538,787 B), `mounttype.dat` (438,091 B), `GameMap.dat`
  (16,509 B), `Server.dat` (4,608 B). The filename is lowercase
  `magictype.dat`, as on 6907..7135; 7205 ships `MagicType.dat`.
* `play.exe` (1,744,976 B) is identical to 7065 7083 7170 7182 7189 7205
  7217's; 6907's and 7135's differ.
* **Detection before this file** (code-read, not run): `Patch6907.
  confidence` returns `SIBLING_CONFIDENCE` 0.55 on a 7110 root (compiled
  tables present, stamp > 6868, itemtype refuses the TQ cipher);
  `Patch6090` 0.45. The measured confirmation is `measure_7110.py`'s job.

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The `.dat` codecs.** `Patch6907.SPECS_*` were measured over the SAME
  ten installs 6907's docstring names, 7110 among them (`TITLE_COLUMNS`,
  `hairface_storage_type` 415 rows on 7110). Every column declaration that
  names 7110 there is inherited as a MEASUREMENT; the rest are inferred.
* **`item` row count.** Same ciphertext as 7083/7065/7135, same reader and
  dictionary, so the same count -- the test pins that as a floor-free
  control rather than a number.

MEASURED BY measure_7110.py (2026-09-19, run by the Director, EXIT 0;
every number is Clients/7110's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: the winner or
  margin changes on 7110 ONLY -- `patch7110` 0.95, margin 0.40, where it
  was `patch6907` 0.55. `patch7110` scores 0 on the other 45. Controls:
  6907 -> `patch6907` 0.95 PASS; 7205 -> `patch7205` 0.95 PASS.
* **Containers:** `c3.wdf` + `data.wdf`, whole-file sha256 IDENTICAL to
  6907's (c3 ab68f57cc24ae100, data fc628e4adeb7de48). The twelve `.dbc`
  sha256-identical to 6907's (12/12). `part_tables()`: nine slots, identical
  to 6907's row for row (armet 2,793, armor 3,754, weapon 13,292, Mount
  1,223; head/misc 0).
* **`itemtype.dat` IDENTICAL to 7065's, 7083's and 7135's; DIFFERENT from
  6907's** (whole-file compare).
* **block96, POSITIVE CONTROLS FIRST:** 6907's `itemtype.dat` read
  901,698 blocks / 0 unknown / 38,346 rows = `test_patch6907`'s pins, PASS;
  7205 read 101 files / 809 unknown / 99 full = `Patch7205.BLOCK96_CENSUS`,
  PASS. **7110: families** block96 90, rar-mangled 11, rsa-mysqldump 10,
  plaintext 7, binary-plain 6, empty 6, unknown 4, tq-stream 3,
  shift-obfuscated 1. **block96: 90 files, 1,409,684 blocks, 10,315
  unknown (99.27% known); 71 fully covered, 18 PARTIAL, 1 at zero**
  (`levexp.dat`). 6907 in the same run: 70 files, 762 unknown, 68 full,
  0 partial, 2 zero. block96 is CONFIRMED, so `DAT_CENSUS_ERA` stays True.
  Headline tables: itemtype 989,828 blocks 1.0000 known, 41,323 rows
  intact; monster 86,952 blocks 0.9986, 3,108 sections; magictype 44,898
  blocks 0.9496, 1,544 of 2,068 lines intact; mounttype 36,507 blocks
  0.9997, 1,808 sections; ItemtypeSub 59,506 blocks 0.9290, 930 of 1,825
  lines intact. The 18 partial tables are `PARTIAL_TABLES` below -- **a
  KNOWN LIMIT of the dictionary, not a reader defect: rows with an unknown
  block are dropped, never guessed.**
* **`catalogs()`, as measured (before the codec fix below): 240 subjects,
  236 ok** (6907: 222 / 218; 7083 under patch6907: 240 / 236). Refused:
  `action`, `item:sub`, `levexp`, `serverplay` -- the same four on 7083.
  `patch7110` and `patch6907` returned the SAME subject set, ok flags and
  row counts on this root (PASS). **After `CODEC_ON_DISK` (finalize,
  2026-09-19, one ~20 s script on Clients/7110 only): 240 subjects, 237
  ok. ok-set(patch6907) minus ok-set(patch7110) = EMPTY; the reverse is
  `{item:sub}` (1,122 rows, labels like `SageModeExclusive`); every other
  shared subject's row count is unchanged.** Refused: `action`, `levexp`,
  `serverplay`. `item` 41,323 = 7083's 41,323 on the
  byte-identical file (PASS). No subject ok on 6907 is refused on 7110.
  Row counts (6907 / 7083 / 7110): item 38,346 / 41,323 / 41,323; monster
  2,199 / 3,102 / 3,077; magic 2,409 / 2,557 / **1,590**; mount 1,790 /
  1,807 / 1,808; map:dest 567 / 754 / 777; magic:op 979 x3; achievement
  390 / 391 / 391; title_type 106 / 150 / 150; hairface_storage_type 408 /
  415 / 415; instancetype - / 42 / **14**; npc:npc.ini 2,318 / 2,637 /
  2,651; texas_match_prize 81 / 5,407 / 5,413.
* **FINDING (the ">3x the parent" noise check FAILED on two subjects, and
  it is not noise):** `texas_match_prize` 81 on 6907 -> 5,413 on 7110 and
  `battlepass_season` 1 -> 5. Both are the FILE growing, not a wrong
  cipher: 7110's `texas_match_prize.dat` is 19,045 blocks at 99.76% known
  with 5,410 of 5,425 lines intact, `battlepass_season.dat` is 100% known,
  and 7083 -- a different file under the same reader -- reads 5,407 and 4.
  Recorded, not tuned.
* **FINDING: `magic` (1,590) and `instancetype` (14) read FEWER rows than
  on 7083** (2,557 and 42) because 7110's `magictype.dat` (94.96%) and
  `instancetype.dat` (73.70%) are partial in the dictionary. Known limit.
* **Encoding:** `codepage.ini` = `1256` on 6907 and 7110; `SlotNpc.ini`
  3,959 B, 102 high bytes, decodes strict as GBK on both. ONE table, no
  rejection probe, no 7205 control -- not the per-table proof the lineage
  ruling asks for, so `TEXT_ENCODING` stays latin1 (inherited).
* `npc.ini`: 2,661 section headers (500,659 B) against 6907's 2,325.

NOT MEASURED
~~~~~~~~~~~~
* GBK as a per-table proof (see Encoding); the name columns of the 18
  partial tables beyond what `patch6907` already measured on all ten
  siblings.

`DAT_CENSUS_ERA = True`, stated rather than inherited silently: 7110 is
already in `tools/dat_census.BUILDS`, and `tests/test_dat_census.py` asserts
every name there detects as a plugin whose marker `is True`. If the
measurement refutes block96 on 7110, the answer is to STOP and report, not
to flip this.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                          # noqa: E402
from plugins.patch6907 import Patch6907, _version      # noqa: E402
from plugins.patch6090 import Patch6090                # noqa: E402


class Patch7110(Patch6907):
    """Official patch 7110: a stamp-exact name for a 6907-era sibling."""

    name = "patch7110"
    label = "Official patch client 7110 (block96 .dat era)"
    origin = "official"
    aliases = ("7110",)

    STAMP = "7110"

    #: Explicit, not merely inherited. 7110 is in `dat_census.BUILDS`, and
    #: measure_7110.py CONFIRMED block96: 90 files, 99.27% of 1,409,684
    #: blocks known. A literal, because the census test uses `is True`.
    DAT_CENSUS_ERA = True

    #: `latin1`, inherited and KEPT. measure_7110.py: `SlotNpc.ini` decodes
    #: strict GBK on 7110 and 6907 alike (102 high bytes), `codepage.ini` is
    #: 1256 -- one table, no rejection probe, no 7205 control, which is short
    #: of the per-table proof the lineage ruling requires.
    TEXT_ENCODING = "latin1"

    #: The twelve compiled art tables, each digest-identical to 6907's (and
    #: 6609's) in `fingerprints.json`. `measure_7110.py` re-checks with
    #: sha256 against 6907.
    FROZEN_COMPILED = (
        "3DEffect.dbc", "3DEffectobj.dbc", "3DObj.dbc", "3DSimpleObj.dbc",
        "3DTexture.dbc", "3dmotion.dbc", "armet.dbc", "armor.dbc",
        "mount.dbc", "mountmotion.dbc", "weapon.dbc", "weaponmotion.dbc",
    )

    #: Builds whose `ini/itemtype.dat` has the SAME digest as 7110's
    #: (fingerprints.json). 6907's does not.
    ITEMTYPE_SHARED_WITH = ("7065", "7083", "7135")

    #: `(blocks known %, rows/lines intact)` per partially-covered block96
    #: table on 7110, MEASURED by measure_7110.py (2026-09-19). A KNOWN
    #: LIMIT of the block dictionary: rows with an unknown block are
    #: dropped, never guessed. `levexp.dat` is at 0% and refused.
    PARTIAL_TABLES = {
        "battlepass_score_reward.dat": (40.88, 83),
        "coat_storage_type.dat": (92.72, 555),
        "exchange_shop_goods.dat": (84.42, 902),
        "exchange_shop_goods_ex.dat": (90.98, 24),
        "instance_enter_condition.dat": (91.11, 109),
        "instancetype.dat": (73.70, 6),
        "itemtypesub.dat": (92.90, 930),
        "magictype.dat": (94.96, 1544),
        "monster.dat": (99.86, 81103),
        "mounttype.dat": (99.97, 25428),
        "newslot_type.dat": (93.55, 40),
        "operating_prize.dat": (93.15, 508),
        "quickpay.dat": (99.08, 320),
        "task_reward_type.dat": (99.70, 217),
        "texas_match_condition.dat": (96.93, 54),
        "texas_match_prize.dat": (99.76, 5410),
        "texas_match_type.dat": (99.69, 156),
        "title_type.dat": (98.40, 148),
    }
    #: MEASURED: block96 census of Clients/7110 (measure_7110.py).
    BLOCK96_TOTALS = {"files": 90, "blocks": 1409684, "unknown": 10315,
                      "fully_covered": 71, "partial": 18, "uncovered": 1}

    #: **Three inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: by `measure_7110.py` (`inidat.classify` on Clients/7110, 2026-09-19);
    #: the same three 6968's finalize found. 6907's list declares each from
    #: 6907's bytes:
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused. **7110: `block96`,
    #:                       59,506 blocks 92.90% known, 930 of 1,825 lines
    #:                       intact.** The inherited refusal is FALSE here, so
    #:                       the subject is re-declared as `@@` rows (7205's
    #:                       grammar for the same file, `SPECS_7205`
    #:                       `item:sub`) -- a subject GAINED over `patch6907`,
    #:                       which refused it on 7110 (and on 7083).
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 7110:
    #:                       `block96`, 9,791 blocks 100.00% known, 779
    #:                       sections. Codec label only: `load_table` reads
    #:                       both families the same way (777 map:dest rows
    #:                       before and after).
    #:   ServerPlay.dat      6907: `block96`. 7110: `unknown`. The subject stays
    #:                       REFUSED; only the codec label is corrected.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    #: `item:sub` columns: id 0, name 1 -- `patch7205`'s declaration for the
    #: same file, and `patch6968`'s. The test checks the labels on 7110.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    #: Headline `.dat` whose digest is unique to 7110 in the corpus.
    UNIQUE_DAT = ("monster.dat", "magictype.dat", "mounttype.dat",
                  "GameMap.dat")

    notes = (
        "A block96-era sibling of 6907: the twelve .dbc and the c3.wdf/"
        "data.wdf index are 6907's (which are 6609's), and ini/*.dat are the "
        "96-bit block cipher read through the block dictionary. Before this "
        "plugin 7110 was read by patch6907's 0.55 sibling bid; this plugin "
        "claims the stamp at 0.95 and reads it with 6907's profile. itemtype.dat is "
        "byte-identical to 7065/7083/7135's; monster, magictype, mounttype "
        "and GameMap .dat are 7110's own. The block dictionary knows 99.27% "
        "of 7110's 1,409,684 block96 blocks: 18 tables are partial (magic "
        "1,590 rows vs 2,557 on 7083, instancetype 14 vs 42) and levexp.dat "
        "is unknown; damaged rows are dropped, never guessed. Three "
        "inherited codecs corrected to the disk, which makes ItemtypeSub.dat "
        "readable (1,122 rows; patch6907 refuses it). 240 subjects, 237 ok, "
        "item 41,323. Nothing is copied into Clients/."
    )

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees TODAY on the file on disk. A different file under the same
        name falls back to 6907's declaration instead of inheriting a 7110
        fact."""
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
        """The exact `version.dat` stamp `7110`, at 0.95, and nothing else.

        Every format probe answers the same on 6907 and 7110 (identical
        `.dbc`, identical archive indexes, same cipher), so only the stamp
        discriminates. No sibling bid: `Patch6907` keeps that for the era,
        and a second era-bidder would tie with it on every other sibling.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- inherited 6907 numbers are labelled as 6907's ---------------------
    def table_quirks(self):
        """The parent's quirks, with every 6907-specific one marked as
        measured on 6907. Its text says "here" about 6907's numbers (npc.ini
        2,325 sections, itemtype 86.4% ...), and under a 7110 name "here"
        would read as a 7110 measurement. The 6090 art quirks are inherited
        unmarked: 7110's art files are digest-identical to 6907's."""
        q = super().table_quirks()
        art = set(Patch6090.table_quirks(self))
        out = {}
        for k, v in q.items():
            if k in art:
                out[k] = v
            else:
                out[k] = ("[inherited from patch6907; MEASURED ON 6907, not "
                          "re-measured on 7110] " + v)
        out["7110 is read through the 6907 profile, unchanged"] = (
            "7110 fell to patch6907's 0.55 sibling bid before this plugin and "
            "is in tools/dat_census.BUILDS. This plugin claims the stamp at "
            "0.95 and corrects three codecs to the disk (ItemtypeSub, "
            "MapDestination, ServerPlay); nothing else about reading. itemtype.dat is "
            "digest-identical to 7065/7083/7135's (not 6907's); monster.dat, "
            "magictype.dat, mounttype.dat and GameMap.dat are unique to 7110 "
            "in the corpus. Measured: 240 subjects, 237 ok; patch6907's ok set plus item:sub, which the CODEC_ON_DISK correction opens.")
        return out


PLUGIN = Patch7110()
