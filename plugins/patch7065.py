#!/usr/bin/env python3
r"""
patch7065 -- official Conquer Online patch 7065: a block96-era client of the
`patch6907` family, with 6609's art and a new `play.exe`.

A THIN SUBCLASS OF `Patch6907`, AND WHY THAT PARENT
----------------------------------------------------
7065 is one of the nine unstamped siblings `patch6907` already bids 0.55 for
(`Patch6907.confidence`, `tests/test_patch6907.py:
TheTenUnstampedSiblingsAreClaimedToo.SIBLINGS`), it is in
`tools/dat_census.BUILDS`, and `patch6907`'s curated specs were MEASURED over
it by name on 2026-09-07 (the "SAME TEN INSTALLS" tables in
`plugins/patch6907.py`). Every alternative parent is refuted by a file:

* `Patch6090` / `Patch6609` -- their `.dat` specs are `tq-stream`, and
  7065's `ini/*.dat` are block96 (it is in `dat_census.BUILDS`, and
  `Patch6907._dat_is_not_tq` is the third leg of the bid that already
  claims it). `tqdat` does not raise on a block96 file; it returns noise.
* `Patch7205` -- a different census (7205's curated `SPECS_7205`), measured
  on 7205's bytes. 7065's tables were measured under `patch6907`'s, not
  7205's, and nothing has been measured of 7205's specs on 7065.
* `Patch7878` / plaintext `.tpd` profile -- 7065 ships the `.wdf` pair and
  the twelve `.dbc`; there is no `.tpd`.

So this class changes IDENTIFICATION ONLY (the exact stamp), plus its own
name, label, notes and quirks. Every spec, codec, column map and the
`DAT_CENSUS_ERA = True` marker are `Patch6907`'s, restated where they are a
claim about THIS build.

MEASURED (without opening an install; code-phase rule)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
From `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` (blake2b of every
significant file, measured before 2026-09-19; `version` 1, entry `7065`,
build digest `350900d8e033a036`, `stamp` `7065`):

* **`version.dat` is 4 bytes, stamp `7065`.** The only discriminator
  `confidence()` uses.
* **All twelve `.dbc` are byte-identical to 6907's, 6968's, 7009's, 7083's
  and 7205's** (same blake2b and size on every one: `3DEffect` 627,162 B,
  `3DEffectobj`, `3DObj`, `3DSimpleObj` 7,128 B, `3DTexture`, `3dmotion`,
  `armet`, `armor`, `mount`, `mountmotion`, `weapon`, `weaponmotion`). The
  compiled art froze at 6609 and is still frozen here, so `Patch6090`'s art
  profile carries by identity of bytes, as it does for `patch6907`.
* **`c3.wdf` (359,069,116 B, 10,274 entries) and `data.wdf` (392,245,257 B,
  14,739 entries)**: header + index identical to 6907's and 7205's. The
  payloads were then MEASURED sha256-identical by `measure_7065.py`.
* **`ini/itemtype.dat` is 11,877,944 B and BYTE-IDENTICAL TO 7083's**
  (blake2b `581cf9...`), different from 7009's (10,822,489 B) and 6907's
  (10,820,377 B). So 7065 and 7083 must serve the SAME `item` row count
  under the same dictionary -- a floor-free control `measure_7065.py`
  prints and `tests/test_patch7065.py` pins.
* **`MagicType`/`magictype.dat` 531,896 B, `monster.dat` 1,000,325 B,
  `mounttype.dat` 437,346 B, `GameMap.dat` 16,165 B** -- each a distinct
  file from 7009's and 7083's (content moves every patch in this era).
* **`play.exe` CHANGES AT 7065**: 1,744,976 B (blake2b `6f0282...`),
  identical to 7083's and 7205's, where 6907, 6968 and 7009 ship
  1,737,264 B (`c8c2f2...`). `Server.dat` also moves (4,608 B).
  `RolePart.ini` is 6907's byte for byte.

From measurements already in the repo that name 7065 (all under
`patch6907`'s profile, 2026-09-07 unless noted):

* `hairface_storage_type.dat`: 415 rows, column 0 2 distinct, column 1
  340 distinct, the pair unique (`patch6907.HAIRFACE_COLUMNS`); 0 rows too
  short to reach the name (`test_patch6907.py` `HAIRFACE["7065"]`).
* **7065 is the FIRST build of the ten to ship** `gouyu_immortal.dat` (7
  rows; 10 from 7083), `gouyu_type.dat` (315), `random_task_cost.dat` (9),
  `AutoUseImmortal.dat`, and `instance_prize.dat` /
  `instance_star_condition.dat` WITH content (7009 ships both at 0 bytes)
  -- `patch6907.SPECS_DAT_CENSUSED`'s census table.
* Map archives 374 (`map/map/*.7z`), `.pux` 61, `effect.ini` 193 sections,
  MNEW chunk instances 625 (`tests/test_claim_enumeration.py`).
* Numeric-label subjects before the column work: 66, as on nine of the ten
  (`test_patch6907.py`, 2026-09-07).

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **That `patch6907`'s spec list is still the right list for 7065 today.**
  It was measured over 7065 on 2026-09-07 (199-201 readable subjects per
  sibling); the block dictionary and readers may have moved since.
* **The encoding** stays `latin1` (see the measurement below: the probe
  run could not tell GBK from cp1256 from latin1).
* **The colourway**, inherited from the 6090 scan via 6609 and 6907.

MEASURED BY measure_7065.py (2026-09-19, EXIT 0, 60 s; every number is
Clients/7065's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: winner or margin
  changes on 7065 ONLY -- `patch7065` 0.95, margin 0.40, where it was
  `patch6907` 0.55. `patch7065` scores 0 on the other 45. Controls: 6907
  still `patch6907` 0.95, 7205 still `patch7205` 0.95 -> PASS.
* **Containers:** `c3.wdf` + `data.wdf`, no `.tpd`/`.tpi`; both archives
  sha256-IDENTICAL to 6907's (`ab68f57cc24ae100`, `fc628e4adeb7de48`).
  `part_tables()`: nine slots identical to 6907's (armet 2,793, armor
  3,754, weapon 13,292, Mount 1,223; head/misc 0). The twelve `.dbc`
  sha256-identical to 6907's.
* **block96, POSITIVE CONTROL FIRST:** 7205 read 101 files / 809 unknown /
  99 full, matching `Patch7205.BLOCK96_CENSUS` -> PASS. 6907 (parent's own
  build): 70 files, 1,184,672 blocks, 762 unknown (99.94%). **7065: 90
  block96 files, 1,383,236 blocks, 335 unknown (99.98% known); 89 fully
  covered, 0 partial, 1 at zero (`levexp.dat`, 335 blocks -- the held-out
  seed-1234 table, as on 6907).** `DAT_CENSUS_ERA = True` is therefore
  SUPPORTED, not merely inherited. Families over `ini/*.dat`: block96 90,
  rar-mangled 11, rsa-mysqldump 10, plaintext 7, binary-plain 6, empty 6,
  unknown 4, tq-stream 3, shift-obfuscated 1. `ServerPlay.dat` classifies
  `unknown` here (not block96), one of the three families
  `patch6907.SPECS_UNSETTLED` records for it across the ten.
  Headline tables all 100.00% known: itemtype 989,828 blocks / 41,323 rows,
  monster 83,360 / 3,022 sections, magictype 44,324 / 2,697,
  mounttype 36,445 / 1,806, ItemtypeSub 37,471 / 1,499.
* **`catalogs()`: 241 subjects, 237 ok** as measured (before the
  `CODEC_ON_DISK` correction; 238 ok after it, `item:sub` 1,499 rows, per
  `tests/test_patch7065.py`) (6907 under Patch6907: 222 / 218).
  Patch7065 and Patch6907 on the same 7065 root agree subject for subject
  and row for row (0 differences), and the ok-subject SET difference
  (Patch6907 minus Patch7065) is EMPTY -- no subject lost. Rows (6907 ->
  7065): item 38,346 -> 41,323, monster 2,199 -> 2,990, magic 2,409 ->
  2,697, mount 1,790 -> 1,806, map:dest 567 -> 740, magic:op 979 -> 979,
  help 1,508 -> 1,508, achievement 390 -> 391, hairface_storage_type 408
  -> 415, title_type 106 -> 150; new on 7065: instancetype 309, gouyu_type
  315, gouyu_immortal 7, instance_prize 480, random_task_cost 9.
  **`item` CONTROL: itemtype.dat 7065 == 7083 byte for byte, and both read
  41,323 rows -> PASS.**
* **Refused under `patch6907` (4):** `action` (binary, columns unknown),
  `levexp` (0 blocks known), `serverplay` (no route), and `item:sub`,
  which `patch6907` declares `empty` because 6907 ships it at 0 bytes --
  FALSE on 7065, where `ItemtypeSub.dat` is 100.00% known with 1,499 intact
  rows. This plugin corrects that one (below); the other three still refuse.
* **THREE INHERITED CODECS ARE 6907's, NOT 7065's** (found by
  `tests/test_patch7065.py`, `inidat.classify` on 7065's files):
  `ItemtypeSub.dat` declared `empty`, classifies `block96`;
  `MapDestination.dat` declared `block96-candidate-refuted`, classifies
  `block96`; `ServerPlay.dat` declared `block96`, classifies `unknown`.
  CORRECTED in `table_specs` (`CODEC_ON_DISK`), so `item:sub` now OPENS on
  7065 -- a subject gained over `patch6907` -- see `CODEC_ON_DISK`.
* **Identity:** vs 7009, 97 `ini/*.dat` identical, 37 differ, 4 new
  (`AutoUseImmortal`, `gouyu_immortal`, `gouyu_type`, `random_task_cost`);
  vs 7083, 111 identical, 27 differ, same set of names.
* **Encoding: NOT PROVEN, so `TEXT_ENCODING` stays `latin1`.**
  `codepage.ini` is `1256`. `SlotNpc.ini` (3,959 B, 102 high bytes) decodes
  strictly as gbk AND cp1256 AND latin1 -- a probe that cannot discriminate,
  so it is not evidence for GBK. No two-table GBK proof exists for 7065.
* `npc.ini`: 2,602 section headers (487,299 B).

Nothing here says "unchanged" for a thing that was not compared.

KNOWN CONSEQUENCE OUTSIDE THIS FILE
-----------------------------------
`tests/test_patch6907.py:TheTenUnstampedSiblingsAreClaimedToo.
test_each_sibling_is_claimed_at_the_sibling_confidence` asserts
the detected winner for each sibling. SETTLED by the slots base
`d705373c` (merged here): the winner must be `patch6907` or `patch<build>`
AND an instance of `Patch6907`. This class satisfies both (`patch7065`,
0.95); `patch6907`'s own 0.55 bid on 7065 is unchanged, so that test's
score assertion still holds. This branch does not edit that file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from dataclasses import replace                        # noqa: E402

import inidat                                          # noqa: E402

from plugins.patch6907 import Patch6907, _version      # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "7065"


class Patch7065(Patch6907):
    """Official patch 7065: 6907's profile, claimed by exact stamp."""

    name = "patch7065"
    label = "Official patch client 7065 (block96 .dat era)"
    origin = "official"
    aliases = ("7065",)
    STAMP = STAMP

    #: Restated, not merely inherited: 7065 IS in `tools/dat_census.BUILDS`
    #: and `tests/test_dat_census.py` asks the DETECTED plugin for this marker
    #: `is True`. Its `ini/*.dat` were measured block96 by that census
    #: (2026-09-07), and `measure_7065.py` CONFIRMED it on 2026-09-19: 90
    #: block96 files, 99.98% of 1,383,236 blocks known.
    DAT_CENSUS_ERA = True

    #: `ini/*.dat` MEASURED byte-identical to another install's (fingerprints
    #: blake2b). Any claim that rests on one of these carries by identity of
    #: bytes, not by argument.
    IDENTICAL_TO = {"itemtype.dat": "7083"}

    #: The twelve `.dbc`, byte-identical to 6907's (fingerprints blake2b), so
    #: to 6609's. Re-stated so the claim names the build it was checked on.
    FROZEN_COMPILED = ("3DEffect.dbc", "3DEffectobj.dbc", "3DObj.dbc",
                       "3DSimpleObj.dbc", "3DTexture.dbc", "3dmotion.dbc",
                       "armet.dbc", "armor.dbc", "mount.dbc",
                       "mountmotion.dbc", "weapon.dbc", "weaponmotion.dbc")

    #: **Three inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: 2026-09-19 (`inidat.classify` on Clients/7065, found by
    #: `tests/test_patch7065.py` and matching 6968's finding). 6907's list
    #: declares each from 6907's bytes:
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused. **7065: `block96`,
    #:                       37,471 blocks 100.00% known, 1,499 of 1,499 lines
    #:                       intact** (measure_7065.py). The inherited refusal
    #:                       is FALSE here, so the subject is re-declared as
    #:                       `@@` rows with 7205's grammar for the same file
    #:                       (`patch7205.SPECS_7205` `item:sub`) -- a subject
    #:                       GAINED over `patch6907` on this install.
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 7065:
    #:                       `block96`, 740 sections. Label only: `load_table`
    #:                       reads both families the same way.
    #:   ServerPlay.dat      6907: `block96`. 7065: `unknown`. Stays REFUSED;
    #:                       only the codec label is corrected.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    #: `item:sub`'s columns, `patch7205`'s declaration for the same file;
    #: that column 1 is a name on 7065 is asserted row by row in
    #: `tests/test_patch7065.py:Catalogs`.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    notes = (
        "A patch6907-era client: the twelve .dbc and the c3.wdf/data.wdf "
        "index are 6609's byte for byte, and ini/*.dat are the 96-bit block "
        "cipher read through the 7878 block dictionary. Read with patch6907's "
        "specs, which were measured over this install; this plugin claims it "
        "by its exact version.dat stamp instead of 6907's 0.55 sibling bid. "
        "itemtype.dat is byte-identical to 7083's. New since 7009: play.exe "
        "(1,744,976 B, 7083's and 7205's), and the first gouyu_*, "
        "random_task_cost and AutoUseImmortal tables. MEASURED: 99.98% of "
        "1,383,236 blocks known (levexp.dat alone at 0), 238 of 241 subjects "
        "open -- item 41,323 rows (same as 7083, same file), monster 2,990, "
        "magic 2,697. item:sub opens here (1,499 fully-known rows) where "
        "patch6907 declares it empty from 6907's 0-byte file."
    )

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7065`, at 0.95, and nothing else.

        **No sibling bid.** `Patch6907.confidence` bids 0.55 for any unstamped
        block96-era client; inheriting that would tie this plugin with
        `patch6907` on 6968..7189 and let dictionary order pick. Same rule as
        `patch7205` / `patch7217`: only the stamp discriminates, because every
        format probe answers the same on 6907..7189.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees TODAY -- a different file under the same name falls back to
        6907's declaration (and its codec gate) instead of inheriting a 7065
        fact. Same mechanism as `patch6968`, measured independently here."""
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

    # -- what is inherited, and what is not --------------------------------
    def colour_provenance(self):
        """One more patch from the afternoon than `patch6907`'s answer."""
        return ("colourway inherited from 6090 via 6609 and 6907 "
                "(unverified on any of them)", "inferred")

    def table_quirks(self):
        """6907's quirks, with the one thing a reader must know first: their
        FIGURES are 6907's."""
        q = super().table_quirks()
        q["the figures in the quirks above are 6907's"] = (
            "Every number quoted in the inherited quirks was measured on "
            "Clients/6907 unless it names another build. 7065 shares the "
            "mechanism -- the same twelve .dbc bytes, block96 ini/*.dat, and "
            "patch6907's spec list measured over this install on 2026-09-07 "
            "-- but its tables are its own files: itemtype.dat is 11,877,944 B "
            "here (6907: 10,820,377 B) and byte-identical to 7083's. 7065's "
            "own numbers (measure_7065.py): 99.98% of 1,383,236 blocks known, "
            "0 partial tables, levexp.dat alone at 0; item 41,323 rows, "
            "monster 2,990, magic 2,697; 238 of 241 subjects open (237 under "
            "patch6907).")
        q["three of 6907's codecs are wrong on 7065's files"] = (
            "ItemtypeSub.dat (6907: 0 B, `empty`) is block96 here, 37,471 "
            "blocks 100.00% known, 1,499 intact rows; MapDestination.dat is "
            "block96, not candidate-refuted; ServerPlay.dat is `unknown`, not "
            "block96. Corrected per file, only where the classifier agrees on "
            "the disk (CODEC_ON_DISK) -- item:sub opens here and refuses "
            "under patch6907.")
        q["play.exe changes at 7065"] = (
            "1,744,976 B, byte-identical to 7083's and 7205's; 6907, 6968 and "
            "7009 ship a 1,737,264 B play.exe. Recorded from the fingerprint "
            "set (blake2b); what changed inside it is not examined here.")
        return q


PLUGIN = Patch7065()
