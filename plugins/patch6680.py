#!/usr/bin/env python3
r"""
patch6680 -- official Conquer Online patch 6680: 6609's art, 6609's cipher,
and the NARROW (65-column) `itemtype.dat`.

A THIN SUBCLASS OF `Patch6609`, AND WHY 6609 AND NOT 6090 OR 6907
------------------------------------------------------------------
Code-phase evidence only (2026-09-19): repo files, earlier measurements
already committed here, and `co-pnd/docs/patchnotes/fingerprints.json`
(blake2b + size of each install's significant files, measured before this
plugin existed). The install-bound numbers are in MEASURED BY
measure_6680.py below, and confirm every inference in this section.

* **Not `Patch6907`.** 6680 is on the TQ-cipher side of the block96
  boundary. `patch6907`'s own docstring MEASURED the 7878 dictionary at 0.0%
  on 6772 / 6805 / 6868 (38 of 6868's blocks, all from a poisoned pair), and
  `tests/test_patch6907.py::test_it_claims_nothing_on_the_old_side_of_the_
  boundary` pins `patch6907.confidence == 0.0` on 6680 with a real `exists`.
  6680 is two patches BELOW 6772, and its `itemtype.dat` is the SAME FILE as
  6772's (fingerprint blake2b `caf299cd`, 8,841,840 B, identical on 6680,
  6707 and 6772; `core/tqdat.py` records the same md5 identity with 6772).
  So `DAT_CENSUS_ERA = False` and the `tools/dat_census.py` slot for 6680
  stays commented. `measure_6680.py` re-checks the family with
  `inidat.classify` on every `ini/*.dat` (positive control first: 6609 must
  read tq, 6907 must read block96).
* **`Patch6609` over `Patch6090`**, from fingerprints.json:
    - all twelve `.dbc` are blake2b-IDENTICAL to 6609's (armet `7ba8ed8b`,
      armor `658ee820`, weapon `8c3b9e4f`, mount `61f406bb`, 3DSimpleObj
      `b92fbcff`, 3DObj `b91236bd`, 3DTexture `c4182f00`, 3dmotion
      `7e04df81`, 3DEffect `12a6c78b`, 3DEffectobj `7f508460`, mountmotion
      `5486e1c1`, weaponmotion `7690c6b4`) -- and DIFFERENT from 6090's on
      every one. The compiled art is 6609's by identity of bytes, so the
      6609 row counts (armet 2,793 · armor 3,754 · weapon 13,292 · mount
      1,223 · 3DSimpleObj 442) transfer by identity, not by argument.
    - `EmotionIco.dbc`, `misc.dbc`, `miscmotion.dbc`, `MagicType.dat` and
      `Monster.dat` are listed on 6090 and NOT on 6609 or 6680; 6680 ships
      the lowercase `monster.dat` / `magictype.dat` that `SPECS_6609`
      declares. (A missing fingerprint entry is not a measured absence --
      `measure_6680.py` checks `ABSENT_HERE` on disk.)
    - `c3.wdf` `0a534373` / `data.wdf` `2a85b65e`: identical to 6609's and to
      the whole official `.wdf` lineage.
    - root `Conquer.exe` is 41 B, blake2b `fabd4f4b`, identical to 6609's
      stub; `play.exe` identical to 6609's.
* **`version.dat` is 4 bytes, stamp `6680`** (fingerprints.json). Older
  docs in this repo (`patch6090`'s Action3DEffect note,
  `test_plugin_catalogs`) call 6652-6868 "unstamped"; the fingerprint says
  6680 IS stamped, and `patch6090.confidence` gives a stamped sibling 0.45
  -- which is what `detect` returns on 6680 today (per this wave's brief;
  `measure_6680.py` records the real before/after).

WHAT 6680 CHANGES FROM 6609 (the overrides, each with its evidence)
-------------------------------------------------------------------
1. **`confidence`**: 0.95 on the exact stamp `6680`, else 0.0. Every format
   probe answers the same on 6609 and 6680, so only the stamp discriminates.
2. **`table_specs` keeps 6090's per-install `Action3DEffect.ini` shape
   probe** (`patch6090.with_action3deffect_shape`), which `Patch6609`'s
   override dropped. MEASURED by others: 6680's file carries 4 section
   headers (`[wing]`), where 5517/6090's are flat keys
   (`test_plugin_catalogs.Action3DEffectShape.SECTIONED`). 6609's census
   already says `ini-sections`, so on 6680 the probe should be a no-op; it is
   kept because moving 6680 off `patch6090` must not lose the one table
   `patch6090` measures per install.
3. **`table_specs` borrows 6609's `.ini` census, FILTERED TO DISK.**
   `censused.extend(..., "patch6680")` would add nothing -- the generated
   census has no `patch6680` entry -- so 6680 would silently lose every
   censused `.ini` subject it had under `patch6090`. The donor is 6609 (the
   same art, the nearest census below); a donor spec whose file this install
   does not ship is dropped, so absence reads as absence. The borrowed
   grammar is INFERRED for 6680 until `measure_6680.py` has `catalogs()`
   results; that script compares against 6609's and 6090's own numbers.
4. **`itemtype.dat` is the 65-column `@@` layout** (MEASURED 2026-08-30,
   `test_plugin_catalogs.AT_WIDTHS["6680"] == 65`). NO override: `tqdat`
   picks `FIELDS_AT_NO_WEIGHT` by the file's own width, and the catalog's
   `{"id": 0, "name": 1}` sits below the dropped index 11. Declared here as
   a quirk so nobody "fixes" it with a patch-level rule (6716 ships 66
   again).
5. **`colour_provenance`, `table_quirks`, `notes`**: re-worded so no 6609
   figure is presented as a 6680 measurement.

MEASURED BY measure_6680.py (2026-09-19, EXIT 0, 26 s; every number is
Clients/6680's unless another install is named)
------------------------------------------------------------------------------
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: winner/margin
  changes on 6680 ONLY -- `patch6680` 0.95, margin 0.50, where it was
  `patch6090` 0.45. `patch6680` scores 0 on the other 45. CONTROL: 6609
  still `patch6609` 0.95. `version.dat` is `b'6680'`. (6652, 6707, 6772,
  6805, 6868 still fall to `patch6090` 0.45 -- their own plugins' job.)
* **Containers:** `c3.wdf` + `data.wdf`, sha256-IDENTICAL to 6609's
  (`ab68f57cc24ae100`, `fc628e4adeb7de48`). `part_tables()`: 9 slots,
  identical to 6609's. All twelve `.dbc` sha256-identical to 6609's (12/12);
  CONTROL: 6090's `armor.dbc` differs.
* **Codec, CONTROLS FIRST:** 6609 `itemtype.dat` tq-stream, 6907 block96 --
  PASS. 6680's top-level `ini/*.dat`: tq-stream 48, binary-plain 6,
  rar-mangled 11, plaintext 6, rsa-mysqldump 10, shift-obfuscated 1,
  unknown 1, empty 3. **Zero block96**, hence `DAT_CENSUS_ERA = False`.
  One file changed family from 6609: **`ItemtypeSub.dat` is EMPTY here**
  (tq-stream on 6609).
* **itemtype width:** 6609 mode 66 (control), 6680 mode 65 on all 31,371
  rows; 6772 identical. The file is byte-identical to 6707's and 6772's,
  different from 6609's and 6652's.
* **`catalogs()`: 166 subjects, 163 ok** (6609 on its own root: 164 / 162;
  6680 via `patch6090` before this plugin: 143 / 139). The measured run was
  164 / 161 ok; the two regressions it found (`emotionico`, `info`) are
  fixed by `SECOND_DONOR`, which adds both as ok subjects. Rows, 6609 -> 6680: item 24,270 -> 31,371 (CONTROL: 6772
  via Patch6609 = 31,371, EQUAL), monster 1,299 -> 1,518, magic 1,551 ->
  1,831, mount 1,764 -> 1,765, map:dest 371 -> 396, magic:op 800 -> 800,
  magic:auto 14 -> 14, action3deffect 4 -> 4, gamemap 358 -> 372,
  item:refine 92 -> 160, region 258 -> 276.
* **FINDING / KNOWN LIMIT: `item:sub` is 0 rows, not ok** -- the client ships
  `ItemtypeSub.dat` empty (6609: 7,402 rows). An absence of content, not a
  reader defect; pinned in the test.
* **FLAGGED, NOT NOISE:** the ">3x the predecessor" heuristic fired on
  `globallotterycondition` (6609 1 -> 22) and `weaponshow` (22 -> 97). Both
  are plaintext `.ini` with a RAW control (not a re-decode), and
  `weaponshow` reads the same 97 via `patch6090`; a wrong cipher cannot
  produce a raw-control count. Recorded as content growth.
* **Also noted:** `npc:npc.ini` FELL 2,750 -> 1,933 and `actionsound` 4,326
  -> 3,466 from 6609 (both raw control; same count via `patch6090`). Not
  investigated.
* **Inherited 6609 claims, checked here:** `ABSENT_HERE` all three absent;
  root `Conquer.exe` 41 B; `Env_DX8`/`Env_DX9` each carry `Conquer.exe`;
  every curated `SPECS_6609` file is on disk; `Action3DEffect.ini` is
  `ini-sections` and the spec agrees; `shlayout.dat` 37.9% / 38.1% and
  `shlayout800x600.dat` 38.7% / 38.5% printable at seeds 9527 / 1234 -- still
  unread; `levexp.dat` sha256 `df41c576aa47dda7` (6090's refusal holds by
  identity); `ROW_COLUMNS` region 276 / EventTypeName 24 / VipTrans 31 /
  restrain 5 rows, 0 numeric labels each.
* **Encoding: `TEXT_ENCODING` stays latin1 (inherited).** Measured only that
  `SlotNpc.ini` (102 bytes >= 0x80) and `npc.ini` (8) DECODE as GBK;
  region.ini and EventTypeName.ini are pure ASCII. That is not the positive
  proof the owner's ruling requires (no CJK check, no rejection probe, no
  7205 control), and this build is before the 7205 lineage.

NOT MEASURED
------------
* Monster colour sets on 6680 (inherited from the 6090 scan via 6609).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                    # noqa: E402
from plugins.patch6090 import with_action3deffect_shape  # noqa: E402
from plugins.patch6609 import Patch6609, SPECS_6609, _version  # noqa: E402

#: The census this build borrows (the generated census has no `patch6680`).
CENSUS_DONOR = "patch6609"

#: Subjects borrowed from `patch6090`'s census as well, because 6609's census
#: does not declare them and 6680 ships and reads them. MEASURED by
#: `measure_6680.py` (2026-09-19): `patch6090.catalogs(Clients/6680)` reads
#: `emotionico` (EmotionIco.ini) 108 rows and `info` (info.ini) 240 rows, both
#: ok with a raw control; the 6609-census-only draft of this plugin lost both
#: -- the only two subjects that moving 6680 off `patch6090` regressed.
SECOND_DONOR = "patch6090"
SECOND_DONOR_SUBJECTS = ("emotionico", "info")


class Patch6680(Patch6609):
    name = "patch6680"
    label = "Official patch client 6680"
    origin = "official"
    aliases = ("6680",)

    STAMP = "6680"

    notes = (
        "6609's layout: the same twelve compiled .dbc (blake2b-identical), "
        "the same c3.wdf/data.wdf, TQ-cipher ini/*.dat at seed 9527 (the "
        "block96 era starts at 6907). itemtype.dat is the 65-column @@ "
        "layout -- index 11 dropped -- which tqdat selects by width; the "
        "same file ships on 6707 and 6772. Action3DEffect.ini is sectioned. "
        "The .ini census and the monster colour sets are borrowed from "
        "6609 and are NOT yet verified on this install.")

    #: **False, literally.** 6680's `.dat` are the TQ stream cipher, not
    #: block96 (see the module docstring); `tools/dat_census.py` does not
    #: cover it and the slot stays commented. `test_dat_census` asks `is True`.
    DAT_CENSUS_ERA = False

    #: The `.dat` files fingerprints.json shows blake2b-identical to a
    #: neighbour, and so carrying that neighbour's measurements by identity.
    #: `itemtype.dat` is 6772's (and 6707's), NOT 6609's.
    ITEMTYPE_IDENTICAL_TO = ("6707", "6772")

    #: Curated `.dat` the client ships EMPTY (inidat family `empty`),
    #: MEASURED 2026-09-19. `ItemtypeSub.dat` is tq-stream with 7,402 rows on
    #: 6609 and 0 bytes of content here, so `item:sub` is not ok on 6680 --
    #: a known limit of the client, not of the reader.
    EMPTY_HERE = ("ItemtypeSub.dat",)

    #: The twelve compiled tables, blake2b-identical to 6609's
    #: (fingerprints.json). Named so the test can check the claim on disk.
    FROZEN_COMPILED = (
        "armet.dbc", "armor.dbc", "weapon.dbc", "mount.dbc",
        "3DSimpleObj.dbc", "3DObj.dbc", "3DTexture.dbc", "3dmotion.dbc",
        "3DEffect.dbc", "3DEffectobj.dbc", "mountmotion.dbc",
        "weaponmotion.dbc")

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `6680`, at 0.95, and nothing else.

        Same rule as `patch6609` / `patch7205`. 0.95 beats `patch6090`'s
        0.45 stamped-sibling bid, which is what 6680 falls to without this
        plugin; an unstamped root is never claimed.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """6609's curated specs, then 6609's `.ini` census filtered to what
        this install ships, then `Action3DEffect.ini` set to this install's
        measured shape. A curated spec wins on subject and filename."""
        ini = Path(root) / "ini"
        present = ({p.name.lower() for p in ini.iterdir() if p.is_file()}
                   if ini.is_dir() else set())
        taken = {s.subject for s in SPECS_6609}
        files = {s.filename.lower() for s in SPECS_6609}
        borrowed = tuple(
            s for s in censused.specs_for(CENSUS_DONOR)
            if s.filename.lower() in present
            and s.subject not in taken and s.filename.lower() not in files)
        taken |= {s.subject for s in borrowed}
        files |= {s.filename.lower() for s in borrowed}
        borrowed += tuple(
            s for s in censused.specs_for(SECOND_DONOR)
            if s.subject in SECOND_DONOR_SUBJECTS
            and s.filename.lower() in present
            and s.subject not in taken and s.filename.lower() not in files)
        return with_action3deffect_shape(
            censused.extend(SPECS_6609 + borrowed, self.name), root)

    def colour_provenance(self):
        """Inherited from the 6090 scan via 6609, and checked on neither."""
        return ("colourway inherited from 6090 via 6609 (unverified here)",
                "inferred")

    def table_quirks(self):
        """6609's quirks, flagged as 6609's figures, plus 6680's own."""
        q = super().table_quirks()
        q["the figures in the quirks above are 6609's"] = (
            "Every number quoted in the inherited 6609 quirks (shlayout "
            "printable %, mount.dbc 1,223 rows, the Env_DX8/Env_DX9 layout) "
            "was measured on Clients/6609. 6680's twelve .dbc and both .wdf "
            "are blake2b-identical to 6609's, so the .dbc/.wdf figures carry "
            "by identity of bytes; the others are pending measure_6680.py.")
        q["itemtype.dat is 65 columns wide"] = (
            "6680 ships the narrow @@ layout (65 fields, index 11 -- the one "
            "FIELDS_AT calls weight -- dropped), MEASURED 2026-08-30. tqdat "
            "picks FIELDS_AT_NO_WEIGHT by the file's own width; a rule keyed "
            "on patch level would be wrong because 6716 ships 66 again. The "
            "file is byte-identical to 6707's and 6772's.")
        q["the .ini census is borrowed from 6609"] = (
            "No census was generated for 6680; 6609's settled .ini grammars "
            "are applied to the files this install ships. Shape per file is "
            "INFERRED from 6609 until measured here.")
        return q


PLUGIN = Patch6680()
