#!/usr/bin/env python3
r"""
patch6652 -- the official Conquer Online patch 6652 client: 6609's compiled
art, byte for byte, with a newer TQ-cipher content layer on top.

A THIN SUBCLASS OF `Patch6609`, AND WHY 6609 AND NOT 6090 OR 6907
------------------------------------------------------------------
Written 2026-09-19 under a code-only rule: nothing under `Clients/` was opened
for this file. The evidence is the corpus fingerprint file
(`co-pnd/docs/patchnotes/fingerprints.json`, measured before the owner's
2026-09-19 renames; 6652 was not renamed), `docs/patchnotes/6652.md`, and
earlier repo measurements that already name 6652. Everything the fingerprints
cannot show is in `measure_6652.py` (worktree root, not committed).

MEASURED (fingerprints.json, blake2b of each file, 6652 against 6609)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **`version.dat` is `6652`** (4 bytes). It is the only discriminator
  `confidence()` uses.
* **All twelve `.dbc` are byte-identical to 6609's**: 3DEffect, 3DEffectobj,
  3DObj, 3DSimpleObj, 3DTexture, 3dmotion, armet, armor, mount, mountmotion,
  weapon, weaponmotion. So is `3dobj.ini`, `3dtexture.ini`, `RolePart.ini`,
  `play.exe`, `AutoPatch.dat` and the 41-byte root `Conquer.exe` stub.
* **`c3.wdf` (359,069,116 B, 10,274 entries) and `data.wdf` (392,245,257 B,
  14,739 entries) have byte-identical header+index to 6609's.** (The
  fingerprint hashes the header and index, not the payload.)
* **`EmotionIco.dbc`, `misc.dbc` and `miscmotion.dbc` are absent**, as on
  6609. The fingerprint's significant-file set lists them on 6090, where they
  exist, and not here -- so `ABSENT_HERE` is inherited as a measured fact, not
  as a default.
* **Five content tables differ from 6609's**: `itemtype.dat` 6,706,668 ->
  8,468,678 B, `monster.dat` 462,441 -> 448,530, `magictype.dat` 302,581 ->
  308,400, `mounttype.dat` 426,325 -> 426,372, `GameMap.dat` 11,305 ->
  11,513. `Server.dat` differs too (same 4,096 B).
* **The two renamed tables keep 6609's lowercase names**: `monster.dat` and
  `magictype.dat`, not 6090's `Monster.dat` / `MagicType.dat`. That is the
  one filename fact `SPECS_6609` exists for, and it holds here.

MEASURED EARLIER IN THIS REPO, ON 6652 BY NAME
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **`itemtype.dat` is 66 `@@` fields wide** -- `tests/test_plugin_catalogs.py`
  `AT_WIDTHS` (2026-08-30, all 31 installs), and `core/tqdat.py`'s note:
  "6652 ships 66 and 6680 ships 65". So 6652 is on 6609's side of the
  `weight`-column drop, and `tqdat` selects the layout by measured width, not
  by patch -- nothing to override.
* **`levexp.dat` opens at seed 1234 as 203 rows x 5 fields**, same as
  6090/6609/6868 (`test_plugin_catalogs.py` `SHAPE`).
* **`Action3DEffect.ini` is SECTIONED** (`[wing]` headers) on 6652..6868
  (`test_plugin_catalogs.py` `Action3DEffectChangesShapeInsideOnePlugin`).
  6609's census declares it `ini-sections` too; this class still re-measures
  it on the install through `patch6090.with_action3deffect_shape`.
* **12 readable `.dbc` twins** (`docs/comod_backlog.md`), and **two client
  builds**, `Env_DX8/Conquer.exe` and `Env_DX9/Conquer.exe`, which are
  different builds with different keys (`capture/coprofile/builds.json`,
  2026-08-29). The 6609 `ENV_TREES` tuple is therefore true here.
* **`ini/*.dat` are NOT block96.** `patch6907.LAST_TQ_BUILD` is 6868 and
  `tests/test_patch6907.py` asserts `patch6907` bids 0.0 on 6652 with a real
  `exists` -- that bid is 0.0 only because 6652's `itemtype.dat` opens under
  the TQ cipher. Hence `DAT_CENSUS_ERA = False`.

MEASURED BY measure_6652.py (2026-09-19, EXIT 0; every number is
Clients/6652's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (every `Clients/` folder plus the live CCO
  install), `plugins._rank` with and without this plugin: winner/margin
  changes on 6652 ONLY -- `patch6652` 0.95, margin 0.50, where it was
  `patch6090` 0.45 (the code's prediction). `patch6652` scores 0 on the other
  45. Control: 6609 still `patch6609` 0.95 -> PASS.
* **Identity:** the `IDENTICAL_TO_6609` / `CHANGED_FROM_6609` split is
  CONFIRMED by sha256, and the PAYLOADS of `c3.wdf` (ab68f57cc24ae100) and
  `data.wdf` (fc628e4adeb7de48) are sha256-identical to 6609's.
  `part_tables()`: 9 slots, identical to 6609's row for row. `ABSENT_HERE`:
  all three absent on 6652, present on 6090. `Env_DX8`/`Env_DX9` both carry
  `Conquer.exe`; the root one is 41 B.
* **`ini/`: 305 top-level files** (12 `.dbc`, 196 `.ini`, 83 `.dat`, 10
  `.lua`, 1 `.wdb`, 3 `.rgn`). Against 6609: 8 added (`ar_res.ini`,
  `texasmovieweb.ini`, `weapontrans.ini`, `c3dmapeffect.lua`, `camera.lua`,
  `domino.lua`, `hero.lua`, `player.lua`), none removed.
* **Cipher, POSITIVE CONTROLS FIRST:** 6609's 46 tq-stream tables all open
  >=95% printable -> PASS; 6907's `itemtype.dat` classifies `block96` ->
  PASS. **6652: tq-stream 46, binary-plain 6, rar-mangled 11, plaintext 6,
  rsa-mysqldump 10, shift-obfuscated 1, empty 2, unknown 1 -- block96 NONE.**
  Hence `DAT_CENSUS_ERA = False`.
* **All five `item_refine_*.dat` are shipped and classify tq-stream**, so the
  curated `SPECS_6609` entries read (item:refine 133, cost 4, effect 648,
  effect:ex 86, upgrade 702).
* **itemtype width:** 6652 and 6609 have the same `@@` count per row (the
  script counted 67 = separators+1 on 30,029 / 24,269 rows); 6680 is one
  narrower (66 on 31,371). Consistent with `AT_WIDTHS` (6652 66, 6680 65).
* **`shlayout.dat` / `shlayout800x600.dat` are NOT 6609's bytes** and
  classify `rsa-mysqldump` (88.5% / 89.4% printable at 9527) -- the 6609
  finding does not transfer and is recorded as such in `table_quirks`.
* **catalogs(): 166 subjects, 164 ok** (6609 under Patch6609: 164 / 162;
  6652 under patch6090 before this plugin: 143 / 140). The two non-ok are
  `action` and `levexp`, refused on 6609 for the same reasons. Rows
  (6609 -> 6652): item 24,270 -> 30,029, monster 1,299 -> 1,376, magic
  1,551 -> 1,581, mount 1,764 -> 1,765, map:dest 371 -> 386, magic:op 800,
  magic:ex 10, magic:auto 14, item:value 1,094 (all four unchanged),
  action3deffect 4 (ini-sections on both), region 258 -> 259. No subject ok
  on 6609 fails here.
* **Census borrow:** 6609's 141 censused specs are all present on 6652 and
  all 141 served with a control (6090's: 125 present; 7878's: 136 of 154).
  196 `.ini` shipped, so ~55 remain uncensused.
* **`ROW_COLUMNS` hold:** region 259 / eventtypename 24 / viptrans 31 /
  restrain 5 / magic 1,581 rows, zero numeric labels on each.

FINDINGS (recorded, not tuned away)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **REGRESSION THE FIRST DRAFT HAD, FIXED BY MEASUREMENT:** `emotionico`
  (108 rows) and `info` (237) were served on 6652 by `patch6090`'s census
  and are absent from 6609's, so the first draft lost them. They are now
  borrowed from 6090's census (`SECONDARY_BORROW`), those two only --
  `goldenleagueshop` was not ok under `patch6090` either and is not taken.
* **`weaponshow` 22 -> 97 rows**, tripping the script's ">3x predecessor"
  noise alarm. It is a plaintext `.ini` with a RAW control and patch6090
  reads the same 97, so it is content growth, not cipher noise.
* **Large DROPS, all shared with patch6090's read of the same bytes (so
  the file, not the plugin):** `item:sub` 7,402 -> 527 (`ItemtypeSub.dat`
  2,238,432 -> 164,944 B), `3dflyingobj` 240 -> 3, `npc:npc.ini` 2,750 ->
  1,839, `actionsound` 4,326 -> 3,466. Not investigated further.
* **Encoding stays latin1.** The only GBK evidence is one table:
  `itemtype.dat` decrypted holds 338 bytes >=0x80 and decodes as strict GBK.
  That is one table, no rejection probe and no 7205 control -- short of the
  owner's positive-proof bar. `codepage.ini` says `1256`, which was measured
  WRONG on 7878 and is not evidence either way.

STILL INFERRED
~~~~~~~~~~~~~~
* **Colour and monster-name data**: inherited from the 6090 scan through
  6609, unverified here. The archive payloads are identical, so the sets are
  not dropped.
* The ~55 uncensused `.ini`, the loose-`.DMap` decoy claim (6609's).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                    # noqa: E402
from plugins.patch6090 import with_action3deffect_shape  # noqa: E402
from plugins.patch6609 import Patch6609, SPECS_6609, _version  # noqa: E402

#: The plugin whose `.ini` census this build borrows. 6652's compiled tables
#: ARE 6609's files (fingerprints.json), which is the same argument
#: `patch6907.CENSUS_DONOR` makes and measured for its own build.
CENSUS_DONOR = "patch6609"

#: Subjects taken from `patch6090`'s census because 6609's lacks them and
#: 6652 ships them. MEASURED (measure_6652.py): before this plugin,
#: `patch6090.catalogs(Clients/6652)` served both with a control --
#: `emotionico` 108 rows, `info` 237 -- so leaving them out would make this
#: plugin read LESS of 6652 than the plugin it replaces. Named one by one:
#: 6090's census also carries `goldenleagueshop`, which patch6090 did NOT
#: serve ok on 6652, so it is not borrowed.
SECONDARY_DONOR = "patch6090"
SECONDARY_BORROW = ("emotionico", "info")

#: The files fingerprints.json shows byte-identical to 6609's. The inherited
#: claims that rest on 6609's bytes (the twelve `.dbc` row counts, the mount
#: shrink) rest on THESE, and on nothing else.
IDENTICAL_TO_6609 = (
    "c3.wdf", "data.wdf", "Conquer.exe", "play.exe", "AutoPatch.dat",
    "ini/3DEffect.dbc", "ini/3DEffectobj.dbc", "ini/3DObj.dbc",
    "ini/3DSimpleObj.dbc", "ini/3DTexture.dbc", "ini/3dmotion.dbc",
    "ini/armet.dbc", "ini/armor.dbc", "ini/mount.dbc", "ini/mountmotion.dbc",
    "ini/weapon.dbc", "ini/weaponmotion.dbc",
    "ini/3dobj.ini", "ini/3dtexture.ini", "ini/RolePart.ini",
)

#: The fingerprinted files that DIFFER from 6609's. Nothing measured on
#: 6609's copy transfers to these.
CHANGED_FROM_6609 = (
    "Server.dat", "version.dat", "ini/itemtype.dat", "ini/monster.dat",
    "ini/magictype.dat", "ini/mounttype.dat", "ini/GameMap.dat",
)


class Patch6652(Patch6609):
    name = "patch6652"
    label = "Official patch client 6652"
    origin = "official"
    aliases = ("6652",)

    STAMP = "6652"

    #: **False, and it is a statement about the cipher.** 6652's `ini/*.dat`
    #: are the TQ stream cipher at seed 9527 (1234 for `levexp.dat`), the
    #: family `core/tqdat.py` reads -- not the 96-bit block cipher that starts
    #: at 6907. `tools/dat_census.py` measures block96 tables only, so 6652's
    #: slot there stays commented. `measure_6652.py` phase 4 classifies every
    #: `ini/*.dat` to confirm; a single `block96` verdict on this build would
    #: refute this line.
    DAT_CENSUS_ERA = False

    notes = (
        "6609's compiled art byte for byte -- all twelve .dbc, the .wdf "
        "indexes, the Env_DX8/Env_DX9 binary trees -- with a newer TQ-cipher "
        "content layer: itemtype (66 @@ fields, +1.76 MB), monster, "
        "magictype, mounttype and GameMap differ. Same parse family as 6609; "
        "6609's .ini census (141/141 read) plus 6090's emotionico and info "
        "are borrowed and filtered to the files on disk. "
        "Colour and name data inherited from the 6090 scan, unverified here.")

    def confidence(self, root, exists) -> float:
        """0.95 on the exact stamp `6652`, 0.0 on anything else.

        The formats cannot separate this build from 6609 -- its compiled tables
        ARE 6609's files -- so the stamp decides. `Patch6090.confidence` bids
        0.45 for a stamped sibling and keeps doing so; 0.95 wins cleanly over
        it without weakening its claim on its own base.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def table_specs(self, root):
        """Curated 6609 specs, then 6609's censused `.ini` FILTERED TO DISK,
        then the two `SECONDARY_BORROW` subjects from 6090's census, then
        `Action3DEffect.ini`'s kind measured on the install.

        MEASURED: 141 of 141 borrowed 6609 specs are present on 6652 and all
        141 read with a control (measure_6652.py).

        **Why an override at all:** `Patch6609.table_specs` calls
        `censused.extend(SPECS_6609, self.name)`, and there is no census under
        `patch6652` -- inheriting it unchanged would silently drop every
        `.ini` table 6609 declares. So the donor is named (`CENSUS_DONOR`).

        **Why filter:** "declared and absent" and "declared and unreadable"
        are different findings, and only the second is interesting; a borrowed
        list should say what THIS build has. The curated specs are NOT
        filtered: an absent curated table refuses as absent, which is the
        answer a caller needs about e.g. `item_refine_*.dat`.
        """
        ini = Path(root) / "ini"
        have = set()
        if ini.is_dir():
            have = {p.name.lower() for p in ini.iterdir() if p.is_file()}
        curated = tuple(SPECS_6609)
        taken = {s.subject for s in curated}
        files = {s.filename.lower() for s in curated}
        borrowed = tuple(
            s for s in censused.specs_for(CENSUS_DONOR)
            if s.filename.lower() in have
            and s.subject not in taken and s.filename.lower() not in files)
        taken |= {s.subject for s in borrowed}
        files |= {s.filename.lower() for s in borrowed}
        secondary = tuple(
            s for s in censused.specs_for(SECONDARY_DONOR)
            if s.subject in SECONDARY_BORROW
            and s.filename.lower() in have
            and s.subject not in taken and s.filename.lower() not in files)
        return with_action3deffect_shape(curated + borrowed + secondary, root)

    def colour_provenance(self):
        """Inherited from the 6090 scan through 6609 and **not checked here**."""
        return ("colourway inherited from 6090 via 6609 (unverified here)",
                "inferred")

    def table_quirks(self):
        """6609's quirks, with the ones measured on 6609's OWN bytes relabelled
        where 6652's bytes differ or were not looked at."""
        q = super().table_quirks()
        q["shlayout.dat does not open"] = (
            "6609's finding does NOT transfer: 6652's shlayout.dat and "
            "shlayout800x600.dat are different bytes from 6609's and "
            "core/inidat classifies both rsa-mysqldump (88.5% / 89.4% "
            "printable at seed 9527, measure_6652.py). Neither is declared "
            "as a table here; unread.")
        q["weaponshow grew, item:sub shrank"] = (
            "MEASURED against 6609: weaponshow 22 -> 97 rows (plaintext, "
            "raw control -- content, not noise), item:sub 7,402 -> 527 "
            "(ItemtypeSub.dat 2,238,432 -> 164,944 B), 3dflyingobj 240 -> 3, "
            "npc.ini 2,750 -> 1,839, actionsound 4,326 -> 3,466. patch6090 "
            "reads the same numbers from the same bytes.")
        q["mount.dbc shrank"] = (
            "1,223 rows against 6090's 1,651. Holds here BY IDENTITY: 6652's "
            "mount.dbc is byte-identical (blake2b) to 6609's.")
        q["loose .DMap files are decoys (Explorer, 2026-08-10)"] = (
            "MEASURED ON 6609, NOT ON 6652. On 6609 the 184 loose "
            "map/map/*.DMap were byte-identical to 6090's and the real map "
            "delta was inside .7z archives. 6652 adds newplain01_new.7z, "
            "newplain02_new.7z and poker-y.7z (docs/patchnotes/6652.md), so "
            "a loose-file walk still under-reports this client's maps.")
        q["compiled art is 6609's, content is not"] = (
            "All twelve .dbc and both .wdf indexes are byte-identical to "
            "6609's; itemtype, monster, magictype, mounttype and GameMap .dat "
            "differ. Numbers measured on 6609's art transfer; numbers "
            "measured on 6609's .dat do not.")
        q["itemtype is 66 fields wide"] = (
            "66 @@ fields, like 6609 and unlike 6680..6868 (65). tqdat picks "
            "the layout by measured width, so this is read correctly; a rule "
            "of the form 'from 6652 onward' would be wrong one build later.")
        return q


PLUGIN = Patch6652()
