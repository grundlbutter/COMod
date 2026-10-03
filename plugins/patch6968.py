#!/usr/bin/env python3
r"""
patch6968 -- official Conquer Online patch 6968: 6907's art byte for byte,
6907's block96 `.dat` cipher, and a later set of tables.

A THIN SUBCLASS OF `Patch6907`, AND THE PARENT IS A MEASUREMENT
---------------------------------------------------------------
Written 2026-09-19 (wave C) under a code-only rule: nothing here was read off
`Clients/6968` by this author. The evidence is the corpus fingerprint file
(`co-pnd/docs/patchnotes/fingerprints.json`, blake2b + size of each install's
significant files, measured before this plugin) and what the repo already
measured about 6968 as one of `patch6907`'s ten siblings. Everything that
needs the install is in `measure_6968.py` (worktree root, NOT committed), and
the section MEASURED BY measure_6968.py holds what it returned.

MEASURED (fingerprints.json, 6968 against 6907 and 7009)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **`version.dat` is the four bytes `6968`** (size 4). It is the only thing
  `confidence()` reads.
* **The art layer is 6907's, byte for byte.** All twelve `.dbc` carry the
  same blake2b and size as 6907's (and 7009's): `armet` 7ba8ed8b, `armor`
  658ee820, `weapon` 8c3b9e4f, `mount` 61f406bb, `3DSimpleObj` b92fbcff,
  `3DObj` b91236bd, `3DTexture` c4182f00, `3DEffect` 12a6c78b, `3DEffectobj`
  7f508460, `3dmotion` 7e04df81, `weaponmotion` 7690c6b4, `mountmotion`
  5486e1c1. `c3.wdf` (359,069,116 B, 10,274 entries) and `data.wdf`
  (392,245,257 B, 14,739 entries) have the same header+index digest as
  6907's -- the fingerprint hashes the index, NOT the payload, so archive
  PAYLOAD identity is for `measure_6968.py`. `play.exe`, `Conquer.exe`,
  `AutoPatch.dat`, `RolePart.ini`, `3dobj.ini`, `3dtexture.ini` identical.
* **The content moved.** Five fingerprinted `ini/*.dat` differ from 6907's:
  `itemtype.dat` 10,820,377 -> 10,822,489 B, `magictype.dat` 472,252 ->
  472,629, `monster.dat` 773,646 -> 814,771, `mounttype.dat` 433,367 ->
  435,615, `GameMap.dat` 14,196 -> 15,241. `Server.dat` differs too (same
  size). **`itemtype.dat` is byte-identical to 7009's** (blake2b afde1367) --
  the pairing `docs/dict_merge_2026-09-05.md` records ("6968 and 7009 ship
  byte-identical itemtype.dat while their ini/ trees differ, 125 vs 134").
* **Detection before this file** (repo record; re-measured below):
  `patch6907` claims 6968 at its 0.55 sibling bid (`tests/test_patch6907.py:
  TheTenUnstampedSiblingsAreClaimedToo`), so 6968 already opens through
  6907's spec list and block96 reader. This plugin changes WHO answers, not
  how the tables are read.

WHY `Patch6907` AND NOT `Patch6609` / `Patch7205`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
`Patch6609` is the art half only: its `.dat` specs are `tq-stream`, and
`tqdat` returns noise that parses on a block96 file -- the exact defect
`patch6907`'s docstring exists to record. `Patch7205` carries 7205's census,
curated from a different `ini/` tree. `Patch6907`'s spec list was measured
over the ten installs INCLUDING this one (`SPECS_DAT_CENSUSED`,
`HAIRFACE_COLUMNS`: 6968 408 rows, 0 short), and `table_specs` filters it to
the files this build ships. `tools/dat_census.py` already lists 6968 in
`BUILDS`, and `tests/test_dat_census.py` asserts that list against
`detect()`, so `DAT_CENSUS_ERA` stays `True` -- set explicitly below.

WHAT THIS CLASS OVERRIDES, AND WHY
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* `confidence()` -- 0.95 on the exact stamp `6968`, 0.0 otherwise. It does
  NOT inherit `Patch6907`'s 0.55 sibling bid: that bid belongs to the era's
  generic plugin, and a second copy of it would tie with 6907's on every
  unstamped sibling and be broken by name order.
* `table_specs()` -- 6907's list with THREE codecs corrected to what
  `inidat` classifies on 6968's disk (`CODEC_ON_DISK`), and `item:sub`
  re-declared as readable because 6968 ships `ItemtypeSub.dat` with content
  where 6907 ships 0 bytes. Everything else (spec list, column maps, label
  keys) is 6907's.
* identity (`name`, `label`, `aliases`, `STAMP`, `notes`) and one quirk
  saying the inherited figures are 6907's.

MEASURED BY measure_6968.py (2026-09-19, EXIT 0, 64.5 s; every number is
Clients/6968's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: winner or margin
  changes on 6968 ONLY -- `patch6968` 0.95, margin 0.40, where it was
  `patch6907` 0.55. `patch6968` scores 0 on the other 45. Controls: 6907
  still `patch6907` 0.95; 7009 still `patch6907` 0.55 (the sibling bid).
* **Byte identity** (sha256, top-level `ini/*.dat` + `.dbc`): 137 files;
  97 identical to 6907's, 33 changed (`CHANGED_FROM_6907`), 7 new
  (`NEW_SINCE_6907`), 0 gone. The twelve `.dbc` 12/12 identical to 6907's.
  109 identical to 7009's, `itemtype.dat` among them. **`c3.wdf` and
  `data.wdf` PAYLOADS are sha256-identical to 6907's** (`ab68f57cc24ae100`,
  `fc628e4adeb7de48`). 208 top-level `.ini`.
* **Containers:** `c3.wdf` + `data.wdf` (same as 6907). `part_tables()`:
  nine slots, identical to 6907's row for row.
* **block96, POSITIVE CONTROL FIRST:** 7205 reproduced its pin (101 files /
  809 unknown / 99 fully covered) -> PASS. 6907: 70 files, 762 unknown, 68
  full, 2 zero. **6968: 78 block96 files, 1,292,020 blocks, 335 unknown
  (99.97% known); 77 fully covered, 0 partial, 1 at zero (`levexp.dat`,
  held out of the dictionary by design).** `itemtype.dat` 901,874 blocks
  100.00% known, 38,352 of 38,352 lines intact. block96 is CONFIRMED, so
  `DAT_CENSUS_ERA = True` stands. Families on disk: block96 78, rar-mangled
  11, rsa-mysqldump 10, plaintext 7, binary-plain 6, empty 5, tq-stream 4,
  unknown 3, shift-obfuscated 1.
* **`catalogs()`: 229 subjects, 226 ok** (6907 on its own install: 222 /
  218). Controls: `Patch6907` on 6968 equals this plugin subject for
  subject apart from `item:sub` (PASS); `item` = 38,352 on 6968 and on 7009,
  the byte-identical file (PASS). Rows (6907 -> 6968): item 38,346 ->
  38,352, monster 2,199 -> 2,308, magic 2,409 -> 2,413, mount 1,790 ->
  1,799, map:dest 567 -> 643, magic:op 979 -> 979, help 1,508 -> 1,508,
  achievement 390 -> 390, hairface_storage_type 408 -> 408. `recovery`:
  item, monster (2,447 units from 63,113 lines), magic and mount all at
  100% of blocks known. Not ok: `action`, `levexp`, `serverplay` -- all
  three deliberate refusals inherited from 6907.
* **No subject lost against today's winner** (finalize step 5b, a set
  difference on Clients/6968): `patch6907`'s ok set minus this plugin's ok
  set is EMPTY; GAINED `item:sub` (2,693 rows, the name in column 1 on
  every row -- `MysticWindrobe`, 0 numeric, 0 blank); no row count differs
  on a shared subject.
* **FINDING -- three inherited codecs were wrong on this disk** (measure
  phase 7, `inidat.classify`): `ItemtypeSub.dat` declared `empty` and is
  `block96` (838,909 B); `MapDestination.dat` declared
  `block96-candidate-refuted` and is `block96`; `ServerPlay.dat` declared
  `block96` and is `unknown` (5,267 B). Corrected here, see `CODEC_ON_DISK`.
* **FINDING -- `texas_match_prize` 81 -> 5,524 rows, flagged by the
  >3x-noise check and NOT noise:** the file changed (6907 1,215 blocks,
  6968 19,545 blocks, both 100.00% known, 5,524 of 5,524 lines intact) and
  7205's copy reads 5,441 the same way. `patch6907`'s own spec comment
  already records "~5,400 rows of 6" on the nine later builds. Recorded as
  a real content change; the check's threshold assumes a stable table.
* **Encoding: `latin1` KEPT.** `codepage.ini` says `1256`. A strict GBK
  decode of the WHOLE recovered text succeeded on `AutoUseMagic.dat`
  (6,177 B, 100 high bytes, 41 CJK chars) and `UserHelpInfo.dat` (920,793
  B, 336 high bytes, 125 CJK chars). That is suggestive, not the proof the
  lineage ruling asks for: it was not a NAME-FIELD decode, there was no
  probe showing the decoder can reject bytes, and no 7205 control was run
  the same way. A build before 7205 keeps latin1 without that proof.
* `npc.ini` 2,325 -> 2,426 section headers; Lua 47 -> 58, top-level DLL
  30 -> 30.

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* That the inherited `ROW_LABEL_KEY` / `ROW_COLUMNS` columns hold on the 33
  changed tables (the row counts above say the grammars still parse; the
  label columns were not swept).
* `item:sub`'s grammar and columns come from 7205's declaration for the
  same file; the parse and the text in column 1 were measured here.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                          # noqa: E402

from plugins.patch6907 import Patch6907, _version      # noqa: E402


class Patch6968(Patch6907):
    """Official patch 6968: 6907's art and cipher, later content."""

    name = "patch6968"
    label = "Official patch client 6968 (block96 .dat era)"
    origin = "official"
    aliases = ("6968",)
    STAMP = "6968"

    #: Marker for `tools/dat_census.py`. **True, literally, and it is not a
    #: new decision**: "6968" has been in `dat_census.BUILDS` since the
    #: census existed, and `tests/test_dat_census.py` requires the plugin
    #: that `detect()` returns for every listed build to say `True`. Stated
    #: here rather than inherited so the answer names the build it is for.
    #: If `measure_6968.py` refutes block96 on this install, this is the
    #: line to revisit -- and the report stops there.
    DAT_CENSUS_ERA = True

    #: `latin1`, inherited and KEPT. measure_6968.py decoded AutoUseMagic.dat
    #: and UserHelpInfo.dat as strict GBK with CJK present, but not name
    #: fields, with no rejection probe and no 7205 control -- short of the
    #: proof the lineage ruling requires (see the module docstring).
    TEXT_ENCODING = "latin1"

    notes = (
        "6907's art layer byte for byte -- the twelve .dbc and both .wdf "
        "indexes carry 6907's digests -- with the same block96 .dat cipher "
        "and later content: itemtype, magictype, monster, mounttype and "
        "GameMap all changed from 6907, and itemtype.dat is byte-identical "
        "to 7009's. Read through patch6907's spec list and block dictionary: "
        "99.97% of 1,292,020 blocks known, 229 subjects / 226 ok, item "
        "38,352 rows. Three inherited codecs corrected to the disk, and "
        "ItemtypeSub.dat -- 0 bytes on 6907 -- is read here (2,693 rows)."
    )

    #: The `.dbc` MEASURED identical to 6907's (fingerprints.json, blake2b
    #: and size). Re-stated so the claim names the build it was checked on.
    FROZEN_COMPILED = (
        "armet.dbc", "armor.dbc", "weapon.dbc", "mount.dbc",
        "3DSimpleObj.dbc", "3DObj.dbc", "3DTexture.dbc", "3DEffect.dbc",
        "3DEffectobj.dbc", "3dmotion.dbc", "weaponmotion.dbc",
        "mountmotion.dbc")

    #: The 33 top-level `ini/*.dat` MEASURED (sha256, measure_6968.py) to
    #: differ from 6907's. 97 others are identical; 7 are new.
    CHANGED_FROM_6907 = (
        "ability_score.dat", "award_config.dat", "battlepass_score_reward.dat",
        "battlepass_season.dat", "battlepass_task.dat",
        "cards_lottery_pool.dat", "coat_storage_type.dat",
        "exchange_shop_goods.dat", "exchange_shop_goods_ex.dat",
        "GameMap.dat", "item_refine_effect_ex.dat", "item_value_type.dat",
        "itemtype.dat", "ItemtypeSub.dat", "magictype.dat",
        "MapDestination.dat", "monster.dat", "mounttype.dat",
        "operating_prize.dat", "prof_lev_benefit.dat", "prof_lev_up.dat",
        "quickpay.dat", "rune_storage_attr.dat", "runeeffect.dat",
        "ServerPlay.dat", "Shop.dat", "spirit_rate.dat",
        "task_reward_type.dat", "texas_match_condition.dat",
        "texas_match_prize.dat", "texas_match_stage.dat",
        "texas_match_type.dat", "title_type.dat")

    #: New at 6968 (absent on 6907), MEASURED.
    NEW_SINCE_6907 = (
        "exchange_shop_lev.dat", "marketing_active.dat", "newslot_line.dat",
        "newslot_roulette.dat", "newslot_type.dat",
        "xuanbao_addition_attr.dat", "xuanbao_compose_attr_limit.dat")

    #: MEASURED byte-identical to 7009's (blake2b afde1367, 10,822,489 B;
    #: sha256 re-confirmed by measure_6968.py, 109 files shared with 7009).
    IDENTICAL_TO_7009 = ("itemtype.dat",)

    #: **Three inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: by `measure_6968.py` (`inidat.classify` on Clients/6968, 2026-09-19).
    #: `{filename_lower: (codec, grammar change)}`. 6907's list declares each
    #: from 6907's bytes; the files changed at 6968:
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused ("ships it as a
    #:                       0-byte file"). **6968: 838,909 B, `block96`,
    #:                       69,909 blocks 100.00% known, 2,693 of 2,693 lines
    #:                       intact.** The inherited refusal is FALSE here, so
    #:                       the subject is re-declared as `@@` rows (7205's
    #:                       grammar for the same file, `patch7205.SPECS_7205`
    #:                       `item:sub`, 633 rows there) -- a subject GAINED
    #:                       over `patch6907` on this install.
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 6968:
    #:                       `block96`, 8,610 blocks 100.00% known, 645
    #:                       sections. Codec label only: `load_table` reads
    #:                       both families the same way.
    #:   ServerPlay.dat      6907: `block96`. 6968: `unknown` (5,267 B). The
    #:                       subject stays REFUSED; only the codec label is
    #:                       corrected, since the refusal already says no
    #:                       route opens this file.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    #: `item:sub`'s columns: id 0, name 1 -- the `patch7205` declaration for
    #: the same file. MEASURED on 6968: 2,693 rows, column 1 non-numeric and
    #: non-blank on every one (`MysticWindrobe`).
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the file on disk
        is the one measured. Each correction is conditional on the classifier
        agreeing TODAY, so a different file under the same name falls back to
        6907's declaration (and its own codec gate) instead of inheriting a
        6968 fact."""
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
        """The exact `version.dat` stamp `6968`, at 0.95, and nothing else.

        Every format probe answers the same on 6907 and 6968 (identical
        `.dbc`, identical archive indexes, same cipher), so only the stamp
        discriminates. 0.95 outranks `patch6907`'s 0.55 sibling bid, which is
        what 6968 fell to before this plugin -- and deliberately does NOT
        repeat that bid on other builds (see the module docstring).
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def table_quirks(self):
        """6907's quirks, with the one thing a reader must know first."""
        q = super().table_quirks()
        q["the figures in the quirks above are 6907's"] = (
            "Every number quoted in the inherited quirks was measured on "
            "Clients/6907. 6968 shares the mechanism -- the twelve .dbc and "
            "both .wdf indexes carry 6907's digests and the .dat are the same "
            "block96 cipher -- but itemtype, magictype, monster, mounttype and "
            "GameMap changed, so the row counts are 6968's own and come from "
            "measure_6968.py, not from these sentences.")
        return q

    def colour_provenance(self):
        """One patch further from the 6090 scan than 6907's own answer."""
        return ("colourway inherited from 6090 via 6609 and 6907 "
                "(unverified on any of them)", "inferred")


PLUGIN = Patch6968()
