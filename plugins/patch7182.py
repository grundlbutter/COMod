#!/usr/bin/env python3
r"""
patch7182 -- official Conquer Online patch 7182: a stamped member of the
`patch6907` era, one patch after 7170 and one before 7189.

A THIN SUBCLASS OF `Patch6907`, AND WHY THAT PARENT
----------------------------------------------------
Before this file, `plugins.detect(Clients/7182)` could only answer through
`Patch6907.confidence`'s SIBLING bid (0.55): the twelve `.dbc` are present,
the stamp is above 6868, and `ini/itemtype.dat` refuses the TQ cipher. That
bid is recorded against 7182 by name in `patch6907.py` (the "ten unstamped
siblings" quirk) and by `tests/test_patch6907.py:TheTenUnstampedSiblings
AreClaimedToo.SIBLINGS`. `tools/dat_census.py` lists "7182" in `BUILDS`, and
`tests/test_dat_census.py` asserts every name there detects as a plugin whose
`DAT_CENSUS_ERA is True` -- so the parent must be one that answers True, and
`Patch6907` is the plugin whose curated specs were MEASURED on this install
(the 2026-09-07 per-build census covers 7182 by name: `hairface_storage_type`
417 rows, 2 distinct col-0 values, 340 distinct col-1 values, row 2 reading
`BlossomYouth`; `SHIPPED_ON` pins which censused tables it ships).

`Patch7205` was the other candidate and is rejected: its curated specs and
its census dict are 7205's measurement, and 7182 is not byte-identical to
7205 in `itemtype.dat`, `MagicType.dat`, `monster.dat`, `mounttype.dat`,
`GameMap.dat` or `RolePart.ini` (fingerprints, below).

So the parent keeps serving 7182 EXACTLY what it served at 0.55. What this
class changes is the IDENTITY -- a 0.95 claim on the exact stamp, and a
name, label and notes that say 7182 -- not the reader.

MEASURED (from `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json`, taken
2026-09-19 before the owner's 6716/7682 deletions; blake2b of each file, or
of the header+index for the two `.wdf`, not of their payload)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **`version.dat` is the four bytes `7182`.** `patch6907._version` strips
  and reads it; it is the only discriminator `confidence()` uses.
* **Containers: the `.wdf` pair.** `c3.wdf` 359,069,116 B (10,274 index
  entries), `data.wdf` 392,245,257 B (14,739 entries); header+index digests
  EQUAL to 6907's. No `.tpd` in the fingerprint set.
* **All twelve `.dbc` equal 6907's** (armet, armor, weapon, mount,
  3DEffect, 3DEffectobj, 3DSimpleObj, 3DObj, 3DTexture, 3dmotion,
  weaponmotion, mountmotion) -- so equal to 6609's too, the art froze there.
* **Against 7170 (the previous patch):** 22 of 27 fingerprinted files
  equal, **`itemtype.dat` included** (12,545,914 B, blake2b 2f2414ed...).
  Changed: `GameMap.dat` 17,740 -> 17,932, `MagicType.dat` 551,527 ->
  551,723, `monster.dat` 1,188,968 -> 1,192,894, `mounttype.dat` 439,130
  -> 439,634 (and `version.dat`). `play.exe` and `Server.dat` equal 7170's.
* **Against 6907:** `itemtype.dat` 10,820,377 -> 12,545,914, `monster.dat`
  773,646 -> 1,192,894, `GameMap.dat` 14,196 -> 17,932 -- so every row
  count `patch6907.py` quotes is 6907's, not this build's.
* Recorded elsewhere in the repo for 7182 by name: MNEW 1,894 / CCFL 1,720
  / CAME 2,598 chunk instances (`core/coassets.py`); `.pux` 385 / 65
  (`core/dmap.py`).

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The name columns of the tables that changed since 6907** are 6907's
  declarations (and `patch7205`'s for `item:sub`); the labels checked here
  (item:sub, and the census the parent records for 7182) read as text, the
  rest are not re-measured on 7182.
* **Colour and name data**: inherited through 6907 from the 6090 scan.

MEASURED BY measure_7182.py (2026-09-19, EXIT 0; every number is
Clients/7182's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: winner/margin
  changes on 7182 ONLY -- `patch7182` 0.95, margin 0.40, where it was
  `patch6907` 0.55. `patch7182` scores 0 on the other 45. Controls: 6907
  stays `patch6907` 0.95 (PASS); 7170 stays `patch6907` 0.55 (PASS).
* **Containers:** `c3.wdf` + `data.wdf` on 6907, 7170 and 7182; both
  archives sha256-IDENTICAL to 6907's (`ab68f57cc24ae100`,
  `fc628e4adeb7de48`). 12 `.dbc`, none differing from 6907's.
  `part_tables()`: 9 slots, identical to 6907's row for row.
* **block96, POSITIVE CONTROL FIRST:** 7205 reproduced its pin (101 files /
  809 unknown / 99 full) -> PASS. **7182: 99 block96 files, 1,463,727
  blocks, 3,969 unknown (99.73% known); 89 full, 9 PARTIAL, 1 at zero
  (`levexp.dat`).** Families: block96 99, rar-mangled 11, rsa-mysqldump 10,
  plaintext 7, binary-plain 6, empty 6, tq-stream 3, unknown 3,
  shift-obfuscated 1. So block96 is CONFIRMED and `DAT_CENSUS_ERA` stays
  True. Control: `itemtype.dat` on 7170 (identical bytes) gives the same
  1,045,492 blocks / 43,404 rows -> PASS. See `PARTIAL_TABLES`.
* **Three inherited codecs are wrong on this disk** -- `CODEC_ON_DISK`.
* **`catalogs()`: 248 subjects, 245 ok** (measured 244 before the
  `CODEC_ON_DISK` fix, which is exactly `patch6907`'s 244 on this root;
  7170: 248 / 244; 6907: 222 / 218). Rows 6907 / 7170 / 7182: item 38,346 /
  43,404 / 43,404, monster 2,199 / 3,538 / 3,542, magic 2,409 / 2,798 /
  2,718, mount 1,790 / 1,813 / 1,815, map:dest 567 / 871 / 934, magic:op
  979 all three, achievement 390 / 391 / 391, title_type 106 / 168 / 169,
  instancetype - / 276 / 254, hairface_storage_type 408 / 417 / 417, help
  1,508 all three, item:sub refused / refused / **531** (this plugin; see
  `CODEC_ON_DISK`). No subject ok on 7170 fails here; none exceeds 3x
  7170's (the wrong-cipher noise shape).
* **No lost subjects** (the ok-subject SET of `patch6907.catalogs(7182)`,
  today's winner before this plugin, minus this plugin's): EMPTY. Gained:
  `item:sub` (531 rows; name column 1, blank on 0 and numeric on 1 of 531
  -- `BabyRat(Wealth)`, `FantasyBall(Diamond)`).
* `npc.ini` section headers: 6907 2,325, 7170 2,794, 7182 2,815.

FAILS AND GAPS, recorded as known limits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The encoding section RAISED** (`UnicodeEncodeError`: the console is
  cp1252 and could not print a CJK sample) while still on the 7205
  control, after 11 of its 12 name tables -- those 11 had 3 high-byte name
  fields in all (item 1, achievement 2), all strict-GBK-valid, 0 CJK. **7182
  was never measured.** So there is no positive proof, 7182 is pre-7205,
  and `TEXT_ENCODING` stays the parent's `latin1`. The script's print is
  fixed (`ascii()`) for a rerun.
* **`magic` serves 2,718 rows against 7170's 2,798**: `MagicType.dat`
  changed at 7182 and is 99.63% known; damaged rows are dropped, never
  guessed. `instancetype` likewise 254 against 276.
* `levexp.dat` 0 of 335 blocks, as on every build (held out by design);
  `ServerPlay.dat` classifies `unknown` and stays refused.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                           # noqa: E402

from plugins.patch6907 import Patch6907, _version       # noqa: E402


class Patch7182(Patch6907):
    """Official patch 7182: the 6907 profile, claimed by its own stamp."""

    name = "patch7182"
    label = "Official patch client 7182 (block96 .dat era)"
    origin = "official"
    aliases = ("7182",)
    STAMP = "7182"

    #: **True, explicitly, and not merely inherited.** 7182 is in
    #: `tools/dat_census.BUILDS` already, and `tests/test_dat_census.py`
    #: asserts every name there detects as a plugin whose marker `is True`.
    #: Its `ini/*.dat` were censused as block96 on 2026-09-07 with the other
    #: nine `patch6907` builds. If `measure_7182.py` refutes block96 here,
    #: this is wrong and the work STOPS (brief), it is not flipped quietly.
    DAT_CENSUS_ERA = True

    #: The parent's `latin1`. NOT PROVEN otherwise: `measure_7182.py`'s
    #: strict-GBK section raised before reaching 7182 (module docstring), so
    #: there is no positive proof, and 7182 is pre-7205.
    TEXT_ENCODING = "latin1"

    notes = (
        "6907's era, stamped 7182: the same twelve .dbc and .wdf index as "
        "6907 (and 6609), block96-enciphered ini/*.dat read through the 7878 "
        "block dictionary (99.73% of 1,463,727 blocks). itemtype.dat is byte-identical to 7170's (43,404 rows); "
        "GameMap, MagicType, monster and mounttype changed since 7170. "
        "Before this plugin 7182 was served the same profile through "
        "patch6907's 0.55 sibling bid; this plugin claims the exact stamp "
        "and also reads ItemtypeSub.dat (531 rows), which 6907 ships empty. "
        "Nothing is copied into Clients/, which stays vanilla."
    )

    #: The twelve compiled tables, fingerprint-equal to 6907's (MEASURED).
    FROZEN_COMPILED = (
        "armet.dbc", "armor.dbc", "weapon.dbc", "mount.dbc", "3DEffect.dbc",
        "3DEffectobj.dbc", "3DSimpleObj.dbc", "3DObj.dbc", "3DTexture.dbc",
        "3dmotion.dbc", "weaponmotion.dbc", "mountmotion.dbc")

    #: The previous patch, and the `.dat` fingerprint-equal to its copy. The
    #: `item` count is inherited BY IDENTITY OF BYTES from 7170, the only
    #: reason it may be.
    PREVIOUS = "7170"
    IDENTICAL_TO_7170 = ("itemtype.dat",)

    #: `{file: (blocks known %, whole rows/sections served)}` for the block96
    #: tables the 7878-derived dictionary only partly knows on 7182, MEASURED.
    #: A KNOWN LIMIT: records with an unknown block are dropped, never
    #: guessed. `levexp.dat` is the one at zero (see the parent).
    PARTIAL_TABLES = {
        "battlepass_score_reward.dat": (31.81, 44),
        "coat_storage_type.dat": (99.71, 749),
        "exchange_shop_goods_ex.dat": (87.85, 47),
        "instance_enter_condition.dat": (99.94, 277),
        "instancetype.dat": (97.22, 254),
        "ItemtypeSub.dat": (92.31, 531),
        "MagicType.dat": (99.63, 2718),
        "monster.dat": (99.99, 3580),
        "Operating_Prize.dat": (87.57, 314),
    }

    #: Fingerprinted `ini/` files whose bytes moved since 7170 (MEASURED).
    CHANGED_FROM_7170 = ("GameMap.dat", "MagicType.dat", "monster.dat",
                         "mounttype.dat")

    #: **Three inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: by `measure_7182.py` (`inidat.classify` on Clients/7182, 2026-09-19);
    #: the same three the 6968 plugin found on its install.
    #: `{filename_lower: codec}`. 6907's list declares each from 6907's bytes:
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused ("ships it as a
    #:                       0-byte file"). **7182: `block96`, 37,425 blocks,
    #:                       92.31% known, 446 of 998 lines intact, 531
    #:                       units.** The inherited refusal is FALSE here, so
    #:                       the subject is re-declared as `@@` rows with
    #:                       `patch7205`'s grammar for the same file -- a
    #:                       subject GAINED over `patch6907` on this install,
    #:                       served PARTIAL (see `PARTIAL_TABLES`).
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 7182:
    #:                       `block96`, 11,126 blocks 100.00% known, 936
    #:                       sections (934 served). Codec label only:
    #:                       `load_table` reads both families the same way.
    #:   ServerPlay.dat      6907: `block96`. 7182: `unknown`. The subject
    #:                       stays REFUSED; only the codec label is corrected.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    #: `item:sub`'s columns: id 0, name 1 -- `patch7205`'s declaration for
    #: the same file, checked on 7182 (see the module docstring).
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees TODAY on this root; a different file under the same name falls
        back to 6907's declaration instead of inheriting a 7182 fact."""
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
        """The exact `version.dat` stamp `7182`, at 0.95; 0.0 otherwise.

        **Deliberately NOT the parent's sibling bid.** `Patch6907.confidence`
        also answers 0.55 for any post-6868 compiled-table client whose
        itemtype refuses TQ; inheriting that would make this plugin a second
        bidder on nine other installs, and ties there are broken by
        registration order, which is not evidence. Same rule as `patch7205`,
        `patch6609`, `patch7217`.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def table_quirks(self):
        """The parent's quirks, headed by the one a reader needs first: their
        FIGURES are 6907's (itemtype 10.8 MB there, 12.5 MB here)."""
        q = super().table_quirks()
        q["the figures in the quirks above are 6907's, not 7182's"] = (
            "Every row and block count quoted in the inherited quirks was "
            "measured on Clients/6907. 7182 shares the mechanism -- the same "
            "twelve .dbc and .wdf index, the same block96 codec -- but its "
            "itemtype.dat (12,545,914 B, byte-identical to 7170's), "
            "monster.dat and GameMap.dat are larger files. 7182's own "
            "numbers come from measure_7182.py.")
        return q

    def colour_provenance(self):
        """Inherited through 6907 from the 6090 scan; not looked at here."""
        return ("colourway inherited from 6090 via 6609 and 6907 "
                "(unverified on any of them)", "inferred")


PLUGIN = Patch7182()
