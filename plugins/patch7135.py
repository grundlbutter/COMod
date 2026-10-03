#!/usr/bin/env python3
r"""
patch7135 -- official Conquer Online patch 7135: a 6907-era client (6609's
art, block96 `.dat`) whose ONLY new thing on the fingerprint surface is its
`play.exe` -- and on whose disk two of 6907's codec declarations are wrong.

A THIN SUBCLASS OF `Patch6907`, and the reason is the FILE FINGERPRINTS plus
the measurements `patch6907` already took over its ten-install era, not the
patch number. Everything below is labelled MEASURED (with where it was
measured), or INFERRED (follows from a measurement plus a stated argument);
the heavy half came from `measure_7135.py`, run once on a quiet machine.
**Nothing here says "unchanged" about a thing that was not
compared.**

WHY `Patch6907` AND NOT ANOTHER PARENT
--------------------------------------
* `patch6907` is what serves this install TODAY: `Patch6907.confidence` bids
  `SIBLING_CONFIDENCE` (0.55) on 7135 -- compiled `.dbc` present, stamp above
  6868, `itemtype.dat` refuses the TQ cipher -- and
  `tests/test_patch6907.py:TheTenUnstampedSiblingsAreClaimedToo` asserted
  `detect(Clients/7135) == patch6907` until this plugin took the stamp.
* 7135 is IN `tools/dat_census.py` `BUILDS`, and `tests/test_dat_census.py`
  asks the detected plugin for `DAT_CENSUS_ERA is True`. `Patch6907` is the
  plugin whose specs, column maps and census were MEASURED ON THIS INSTALL
  (its docstrings name 7135 among the ten it serves), so inheriting them is
  inheriting 7135's own measurements, not a neighbour's guess.
* Not `Patch7205`: 7205 is a later build with its own pinned census dict
  (`BLOCK96_CENSUS`), and none of its 7205-specific constants were measured
  here.

MEASURED -- identification (fingerprints.json, 2026-09-18 corpus pass)
----------------------------------------------------------------------
* `version.dat` is 4 bytes, blake2b-128 `ee4ee4ba85e3938d82431173499dde9d`,
  which is `blake2b(b"7135")` exactly -- so the stamp is the bare `7135` with
  no line ending (7250's is `7250\r\n`; both strip to their stamp).

MEASURED -- containers and art (fingerprints.json blake2b, vs its neighbours)
---------------------------------------------------------------------------
* `c3.wdf` (359,069,116 B, 10,274 entries) and `data.wdf` (392,245,257 B,
  14,739 entries): header+index hashes identical to 6609's, 6907's, 7110's,
  7170's and 7205's. `.wdf`-only; no `.tpd`/`.tpi` in the fingerprint set.
* **All twelve `ini/*.dbc` identical to 6907's (and 6609's)**, plus
  `3dobj.ini`, `3dtexture.ini`, `RolePart.ini` (1,441 B; 6907/7110/7170
  identical, 6609 and 7205 differ), `Conquer.exe` (41 B launcher stub) and
  `AutoPatch.dat`. So `Patch6090`'s art profile, which `Patch6907` inherits,
  holds here by byte identity.

MEASURED -- what differs (fingerprints.json)
--------------------------------------------
* **`ini/itemtype.dat` is byte-identical to 7065's, 7083's and 7110's**
  (11,877,944 B, b2 `581cf9eb...`). `docs/dict_merge_2026-09-05.md` measured
  that file group: item rows 7,007 / 25,045 lines under the 7878-only
  dictionary, 10,270 / 26,607 after the 2026-09-05 merge (block coverage
  87.1% -> 89.9%). The current dictionary is larger again; the number on
  THIS build under it is `measure_7135.py`'s to report.
* `ini/monster.dat` 1,081,542 B (7110: 1,043,429; 7170: 1,188,968),
  `ini/magictype.dat` 540,333 B (7110: 538,787), `ini/mounttype.dat`
  438,089 B (7110: 438,091), `ini/GameMap.dat` 17,236 B (7110: 16,509),
  `Server.dat` 4,608 B with its own hash.
* **`magictype.dat` is still lowercase here; 7170 onwards ship
  `MagicType.dat`.** `_find_table` matches case-insensitively, so this is a
  note, not an override.
* **`play.exe` is 1,837,656 B (b2 `558f7d32...`) -- UNIQUE in the era.**
  6907-7009 ship 1,737,264 B and 7065-7110 AND 7170-7205 all ship the SAME
  1,744,976 B file. 7135 sits between two identical launchers with a
  different, larger one. Recorded; nothing in the parser reads `play.exe`.
* `capture/coprofile/builds.json` (MEASURED 2026-08-29): this install ships
  TWO game clients, `Env_DX8/Conquer.exe` and `Env_DX9/Conquer.exe`, and they
  are different builds. Neither has a recovered memory layout.

MEASURED ON 7135 BY `patch6907`'s OWN SWEEPS (2026-09-07), inherited as such
--------------------------------------------------------------------------
* `hairface_storage_type.dat` recovers DAMAGED here: 195 rows (widths 2x10,
  3x4, 9x20, 10x11, 11x150), 14 too short to reach the name column, so 181
  carry a label. 7009 is the only other damaged install; the eight others
  recover 408-417 whole rows.
* `battlepass_level.dat` is 50 rows from 7135 (100 on 7009-7110).
* `Appkey.dat` first ships at 7135 (block96, 4 blocks, 0 unknown).
* `emotionico.dat`: `inidat` returns `unknown` on 7009-7135.
* 66 subjects whose labels were all numeric before the column pass; the
  same 66 as the other nine installs.

MEASURED 2026-09-19 by `measure_7135.py` (started 18:08:27Z, EXIT 0, 47 s)
--------------------------------------------------------------------------
POSITIVE CONTROLS FIRST, all PASS: (0a) `Clients/6907` detects as
`patch6907` @ 0.95; (0b) the block96 census on 7205 reproduces
`Patch7205.BLOCK96_CENSUS` exactly (101 files, 1,488,616 blocks, 809
unknown; itemtype 44,684 of 44,684) under the 4,059,815-block dictionary;
(0c) `part_tables()` on 6907 gives the pinned armet 2,793 / armor 3,754 /
weapon 13,292 / mount 1,223.

* **Detection, 46 installs** (every `Clients/` folder plus
  `Classic Conquer 2.0`): ONLY 7135 changes winner -- `patch7135` 0.95
  (margin 0.400) where it was `patch6907` 0.55 (margin 0.100).
  `patch7135` scores 0.00 on the other 45; no `rank()` raised. The other
  eight siblings still read `patch6907` 0.55.
* **Containers:** `_discover_archives(7135)` = `c3.wdf` (359,069,116 B) +
  `data.wdf` (392,245,257 B), no `.tpd`/`.tpi`. **Full PAYLOAD** blake2b-128
  identical to 6907's (`3c1c592e...`, `121c76ac...`). The twelve `.dbc` at
  6907's sha256 prefixes. `Env_DX8/Conquer.exe` 13,339,776 B and
  `Env_DX9/Conquer.exe` 13,337,216 B.
* **part_tables():** nine slots, appearance counts identical to 6907's
  (armet 2,793, armor 3,754, weapon 13,292, mount 1,223; head/misc 0).
* **block96 -- the era is CONFIRMED**: 92 top-level `ini/*.dat` classify
  `block96` (recursive `ini/**.dat` is the same 92): 1,430,838 blocks,
  12,990 unknown; 73 full, 17 partial, 2 at zero (`levexp.dat` held out by
  design; `ServerPlay.dat`). Other families: rar-mangled 11, binary-plain 6,
  empty 6, tq-stream 3, rsa-mysqldump 10, plaintext 7, unknown 3
  (`EmotionIco`, `WeaponActionData`, `WeaponMotionData`), shift 1.
  `itemtype.dat` 989,828 blocks at 100.00%, 41,323 of 41,323 rows whole.
  Block coverage is not row coverage on the partial ones:

      file                    blocks  known   lines  whole   served
      ItemtypeSub.dat         75,814  91.51%  2,194    930    1,104
      magictype.dat           45,027  95.46%  2,124  1,662    1,709
      monster.dat             90,128  99.41% 83,812 83,414    3,019 sec
      hairface_storage_type    2,159  85.64%    294    144      195
      battlepass_task             38  55.26%      7      1        7
      operating_prize          2,261  78.51%    489    369      370

* **FINDING -- two of 6907's codecs are wrong here**, see `CODEC_ON_DISK`:
  `ItemtypeSub.dat` (declared `empty`, is `block96`) and
  `MapDestination.dat` (declared `block96-candidate-refuted`, is `block96`).
  With the correction `item:sub` reads 1,104 rows (control: id 189885
  `SageModeExclusive`, re-decode; 0 blank and 0 numeric labels over all
  1,104) where `patch6907` REFUSED it on this install.
* **catalogs():** 241 subjects, 237 with rows under the measure run;
  `Patch7135` == `Patch6907` on 7135 subject for subject before the codec
  fix. Headline rows: item 41,323 · magic 1,709 · monster 2,988 · mount
  1,809 · map:dest 808 · help 1,508 · npc.ini 2,700 · NpcX.ini 768 ·
  gamemap 533 · achievement 391 · texas_match_prize 5,413.
* **No lost subjects (FINALIZE-C 5b)**, `check5b_7135.py` on 7135 after
  the fix: patch6907 ok-set 237, patch7135 ok-set 238; LOST = [] ;
  GAINED = [`item:sub`]; no shared subject changed its row count.
* **Two >2x flags against 7110, both real and both KNOWN LIMITS, not
  noise:** `hairface_storage_type` 195 here vs 7110's 415 (the damaged
  copy; `browse` confirms 195 rows, 14 unlabelled -- the patch6907 claim
  MATCHES), and `instancetype` 104 here vs 7110's 14 (7110 is the damaged
  one; 7135's copy is 88.58% known, 95 of 113 lines whole).
* **npc:** `npc.ini` 2,710 sections; the frozen `3DSimpleObj.dbc` (442
  rows) resolves 2,279 (84.1%), the `.ini` decoy 1,496, 431 unresolved.
  (7110: 2,661 / 2,229 / 83.8%.)
* **Encoding: `latin1` KEPT.** `codepage.ini` is `1256`. `SlotNpc.ini`
  (102 high bytes) and `npc.ini` (574) both decode strictly as GBK -- AND
  as cp1256, so that probe cannot discriminate; there was no name-field
  decode, no rejection probe and no 7205 control. A pre-7205 build keeps
  latin1 without that proof (owner's lineage ruling, FINALIZE-C step 4).

WHAT THIS PLUGIN OVERRIDES, AND WHY EACH ONE
--------------------------------------------
* `confidence`       -- claims EXACTLY the stamp `7135`, at 0.95, and 0.0
                        otherwise. It must NOT inherit `Patch6907`'s 0.55
                        sibling bid: that would make this plugin tie
                        `patch6907` on the other eight unstamped siblings and
                        move their `detect()` by dictionary order.
* `DAT_CENSUS_ERA`   -- `True`, written out rather than inherited, because
                        7135 is in `dat_census.BUILDS` and
                        `tests/test_dat_census.py` checks `is True` on the
                        detected plugin. If measurement refutes block96 here,
                        this is the line that has to change, and the
                        measurement is STOP-and-report, not a quiet edit.
* `name/label/aliases/notes/STAMP` -- this build's identity.
* `table_specs`      -- 6907's list with `CODEC_ON_DISK` applied (two codec
                        corrections, `item:sub` made readable), each
                        conditional on `inidat` agreeing on the file today.
* `table_quirks`     -- 6907's quirks are kept (they are about the era), the
                        "ten unstamped siblings" quirk is restated because
                        7135 is no longer one of them, and 7135's own
                        measured facts are added.

Everything else -- the rest of the spec lists, `ITEM_COLUMNS`, `ROW_COLUMNS`,
`ROW_LABEL_KEY`, `load_table`, `recovery`, `catalogs` -- is `Patch6907`'s,
and each of those was measured over the ten installs 7135 is one of.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                           # noqa: E402

from plugins.patch6907 import Patch6907, _version       # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "7135"


class Patch7135(Patch6907):
    """Official patch 7135: a 6907-era sibling with its own stamp."""

    #: Written out, not inherited: see the module docstring. A LITERAL True,
    #: because `tests/test_dat_census.py` checks `is True`.
    DAT_CENSUS_ERA = True

    name = "patch7135"
    label = "Official patch client 7135 (block96 .dat era)"
    origin = "official"
    aliases = ("7135",)
    STAMP = STAMP

    notes = (
        "A 6907-era client: 6609's art layer (all twelve .dbc and both .wdf "
        "indexes identical to 6907's) with ini/*.dat in the 96-bit block "
        "cipher, read through core/block96.py's dictionary. itemtype.dat is "
        "byte-identical to 7065's, 7083's and 7110's. The one new file on "
        "the fingerprint surface is play.exe (1,837,656 B, unique in the "
        "era). hairface_storage_type.dat recovers damaged here (195 rows, "
        "181 labelled). Before this plugin, patch6907 served it on a 0.55 "
        "sibling bid; this plugin claims the exact stamp and nothing else."
    )

    #: `latin1`, inherited and KEPT on measurement: see the docstring's
    #: encoding bullet. Written out so the decision is visible.
    TEXT_ENCODING = "latin1"

    #: The recursive `ini/**.dat` block96 census of 7135, MEASURED by
    #: `measure_7135.py` 2026-09-19 (the scope `Patch7205.block96_census`
    #: counts). NOT named `BLOCK96_CENSUS`: that is Patch7205's attribute and
    #: this class does not descend from it.
    CENSUS_7135 = {"files": 92, "blocks": 1430838, "unknown": 12990,
                   "fully_covered": 73, "partial": 17, "uncovered": 2}

    #: **Two of 6907's codecs are wrong on 7135's disk**, MEASURED by
    #: `measure_7135.py` section 4 (`inidat.classify`, 2026-09-19):
    #:
    #:   ItemtypeSub.dat     6907 declares `empty` (6907 ships 0 bytes) with a
    #:                       refusal. 7135 ships it as block96: 75,814 blocks,
    #:                       91.51% known, 930 of 2,194 lines whole, 1,104
    #:                       rows served. Under 6907's spec the subject was
    #:                       REFUSED on 7135 by `patch6907` too.
    #:   MapDestination.dat  6907 declares `block96-candidate-refuted` (its
    #:                       1-byte tail); 7135's copy classifies `block96`
    #:                       (10,096 blocks, 100.00% known, 810 sections).
    #:
    #: `ServerPlay.dat` classifies `block96` here (462 blocks, 0 known), which
    #: IS 6907's declaration -- unlike 6968, where it is `unknown` -- so it is
    #: not corrected, and the subject stays refused by its own refusal.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
    }

    #: `item:sub`'s columns: id 0, name 1 -- the `patch7205` / `patch6968`
    #: declaration for the same file, re-measured on 7135 by
    #: `ItemtypeSubReads` in the test.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees TODAY. A different file under the same name falls back to
        6907's declaration (and its codec gate) instead of inheriting a 7135
        fact. The same mechanism `patch6968` uses for the same two files."""
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
        """0.95 on the exact `version.dat` stamp `7135`; 0.0 otherwise.

        **No sibling bid.** `Patch6907.confidence` makes one at 0.55 for any
        unstamped build of its era; inheriting it would tie `patch6907` on
        6968, 7009, 7065, 7083, 7110, 7170, 7182 and 7189 and let dictionary
        order pick their winner. This plugin exists for one stamp."""
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- quirks ------------------------------------------------------------
    def table_quirks(self):
        q = super().table_quirks()
        q.pop("ten unstamped siblings were in the same wrong state", None)
        q["the 6907-era numbers above are 6907's, not 7135's"] = (
            "The inherited quirks quote 6907's measurements (86.4% of "
            "itemtype blocks, 5,387 rows, npc.ini 2,325 sections). They "
            "describe the era's mechanism, which applies here; 7135's own "
            "counts (itemtype 41,323 of 41,323 rows at 100% of blocks, "
            "npc.ini 2,710 sections) are in this plugin's docstring.")
        q["two of 6907's codecs are wrong on 7135"] = (
            "ItemtypeSub.dat is block96 here (6907 ships it 0 bytes) and "
            "MapDestination.dat is block96 (6907's is "
            "block96-candidate-refuted). Corrected by CODEC_ON_DISK; "
            "item:sub reads 1,104 rows where patch6907 refused it.")
        q["patch6907 served this install on a sibling bid until now"] = (
            "Patch6907.confidence bids 0.55 on unstamped builds of its era "
            "and 7135 was one of them. This plugin claims the exact stamp at "
            "0.95 and bids 0.0 on every other install, so no other "
            "sibling's detection moves.")
        q["hairface_storage_type.dat is damaged on this install"] = (
            "Measured by patch6907's sweep (2026-09-07): 195 rows at widths "
            "2x10, 3x4, 9x20, 10x11 and 11x150, so 14 rows cannot reach the "
            "name in column 3. browse keeps them with an empty label. 7009 "
            "is the only other install in the era with the same damage.")
        q["play.exe is unique in the era"] = (
            "1,837,656 B here; 6907-7009 ship 1,737,264 B and 7065-7110 and "
            "7170-7205 all ship one identical 1,744,976 B file "
            "(fingerprints.json). The install also carries two game "
            "clients, Env_DX8 and Env_DX9, which are different builds "
            "(capture/coprofile/builds.json). Neither fact touches the "
            "parser; both are recorded because they are what is new here.")
        return q


PLUGIN = Patch7135()
