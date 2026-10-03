#!/usr/bin/env python3
r"""
patch7170 -- official Conquer Online patch 7170, a block96-era client with
6907's art layer, claimed by its own `version.dat` stamp.

WHY THIS PLUGIN EXISTS
----------------------
Before it, `plugins.detect(Clients/7170)` returned **`patch6907` at 0.55** --
`Patch6907`'s bid for an UNSTAMPED sibling of its era (compiled tables present,
stamp above 6868, `itemtype.dat` refuses the TQ cipher). That bid was right
about the era and says so itself: "Their own numbers have NOT been curated: the
spec list, the item column index and the label keys are 6907's". This plugin
is the place 7170's own numbers go. It claims the exact stamp `7170` at 0.95
and nothing else, the same rule `patch7205` and `patch6609` follow, so it takes
7170 off the sibling bid without touching any other install.

PARENT: `Patch6907`, AND WHY
----------------------------
The parent is the plugin that already reads this client, measured, and is the
one whose profile matches the files:

  * **the art layer is 6907's byte for byte.** MEASURED from
    `docs/patchnotes/fingerprints.json` (the 2026-09-19 fingerprint pass,
    blake2b per file): all twelve `.dbc` carry the same digest and size on
    6907, 7135, 7170 and 7182 -- `3DEffect` 627,162 B, `3DEffectobj` 449,252,
    `3DObj` 83,208, `3DSimpleObj` 7,128, `3DTexture` 724,083, `3dmotion`
    3,159,493, `armet` 67,712, `armor` 90,704, `mount` 45,496, `mountmotion`
    293,601, `weapon` 319,520, `weaponmotion` 4,754,150. The `c3.wdf`
    (10,274 entries, 359,069,116 B) and `data.wdf` (14,739 entries,
    392,245,257 B) index digests are identical to 6907's too. So
    `Patch6090`'s art profile, which `Patch6907` inherits, applies for the
    same reason it applies to 6907: the files are the same files.
  * **the `.dat` layer is the block96 cipher.** INFERRED from the lineage and
    MEASURED by others, not by this seat: `Patch6907.confidence`'s sibling
    bid fires only when `itemtype.dat` refuses the TQ cipher, and it wins on
    7170 today; `tools/dat_census.py` lists 7170 in `BUILDS`; and
    `docs/dict_merge_2026-09-05.md` measured 7170's `itemtype.dat` at 86.1%
    -> 88.6% block coverage under the 7878 dictionary (5,336 -> 7,956 of
    ~25-26k lines). `measure_7170.py` re-measures coverage per file under the
    CURRENT dictionary, positive control on 6907 first.
  * NOT `Patch7205`: 7205 is a later build with its own measured census dict
    (`BLOCK96_CENSUS`), its own spec list and its own partial-table findings.
    Nothing says 7170 shares them, and inheriting them would publish 7205's
    measurements as 7170's.

`DAT_CENSUS_ERA = True`, set explicitly rather than inherited. 7170 is ALREADY
in `tools/dat_census.py` `BUILDS` and `tests/test_dat_census.py` asserts that
every build in `BUILDS` detects as a plugin whose marker `is True`. Setting it
False would turn that test red; it is only to change if measurement refutes
block96 on this build, and then the right move is to STOP and report.

WHAT IS MEASURED (fingerprints.json, before this plugin)
--------------------------------------------------------
    version.dat         4 B, stamp `7170`
    ini/itemtype.dat    12,545,914 B -- BYTE-IDENTICAL to 7182's (same
                        blake2b 2f2414ed..., same size). 7135's is 11,877,944
                        and 6907's 10,820,377.
    ini/MagicType.dat   551,527 B, **capitalised**. 6907 and 7135 ship
                        `magictype.dat` (lowercase); 7170 and 7182 ship
                        `MagicType.dat`. `Plugin._find_table` matches
                        case-insensitively (it was written for 6609's
                        rename the other way), so the inherited `magic` spec
                        still resolves -- asserted in the test, not assumed.
    ini/monster.dat     1,188,968 B   (6907 773,646; 7135 1,081,542)
    ini/mounttype.dat   439,130 B     (6907 433,367; 7135 438,089)
    ini/GameMap.dat     17,740 B      (6907 14,196 = 444 records)
    Server.dat          4,608 B, identical to 7182's
    play.exe            1,744,976 B, identical to 7182's

and from the repo's own earlier sweeps (named so they can be re-checked):
    * `regkey.dat` FIRST APPEARS at 7170 and is absent from every client
      6609..7135 (`docs/renderer_builds_and_isometry_2026-08-29.md` §1,
      correction). Not a table this plugin reads; recorded as a first.
    * `ServerPlay.dat` is 5,603 B here, byte-length-equal to 7182's
      (`docs/serverplay_is_text_2026-08-29.md`). It is in
      `Patch6907.SPECS_UNSETTLED` and refuses at 0 known blocks.
    * Seven `.dat` tables start at 7170 among the ten 6907-era builds:
      `godsoul_upgrade_config` (257 rows), `newslot_broadcast` (3),
      `newslot_line_probability` (49), `newstone_limit` (12),
      `syn_formtype` (187), `syn_skill_type` (1,400), `syndicate_level` (9)
      -- all "7170-7189" in `patch6907.SPECS_DAT_CENSUSED`'s census note, so
      the inherited disk-filtered spec list already declares them here.
    * `hairface_storage_type.dat`: 417 rows, (col0,col1) unique on all 417
      (`patch6907.HAIRFACE_COLUMNS`).

WHAT IS INFERRED, NOT MEASURED BY THIS SEAT
-------------------------------------------
    * The spec list, `ITEM_COLUMNS` (`itemClass` at column 52), `MAGIC_COLUMNS`
      and `ROW_LABEL_KEY` are 6907's, which `Patch6907` measured over "the
      same ten installs" for most of them (its docstring says so per
      constant). The item column was re-measured on 7170 (below).
    * `TEXT_ENCODING = latin1`, inherited; see the encoding line below.

MEASURED BY `measure_7170.py` (run 2026-09-19 by the Director, EXIT 0)
----------------------------------------------------------------------
Positive controls first, both PASS:
    detection   Clients/6907 -> patch6907 at 0.95;
    block96     Clients/6907 itemtype.dat 38,346 units, 0 unknown blocks
                (the `tests/test_patch6907.py` pin).

    detection   46 installs ranked; the winner changed on ['7170'] ONLY;
                patch7170 scores 0.0 on every other install. On 7170:
                patch7170 0.95, margin 0.40 over patch6907's 0.55 sibling
                bid (which was the winner without this plugin).
    containers  7170: c3.wdf, data.wdf. No .tpd/.tpi.
    archives    c3.wdf sha256 ab68f57cc24ae100, data.wdf fc628e4adeb7de48 --
                IDENTICAL to 6907's (whole-file hash, not just the index).
    part_tables 9 slots, source and row count identical to 6907's.
    block96     7170: 99 block96 files, 1,461,092 blocks, 335 unknown
                (99.98% known); 98 fully known, 0 partial, 1 zero-known
                (`levexp.dat`, 335 blocks -- held out of the dictionary by
                design, see `patch6907.SPECS_UNSETTLED`). 6907 for scale:
                70 files, 762 unknown. **block96 CONFIRMED on 7170**, so
                `DAT_CENSUS_ERA = True` stands.
    itemtype    1,045,492 blocks, 100.00% known, 43,404 lines, all intact,
                43,404 rows; every row 65 fields (6907: 64). Column 52 is
                still the item class: Gift 11,336, QuestItem 2,376,
                EpicWeapon 1,947, GiftPack 1,457, Garment 508, Pack 470 --
                so the inherited `ITEM_COLUMNS` holds. Byte-identical to
                7182's, and the catalog `item` count EQUALS 7182's (43,404).
    catalogs    248 subjects, 244 ok (6907: 222 / 218). patch7170 and
                patch6907 gave the SAME answer, subject for subject, on 7170
                before the codec corrections below -- the profile is
                inherited, so no subject is lost against today's winner.
                Headline rows on 7170 (6907 in brackets): item 43,404
                (38,346), monster 3,538 (2,199), magic 2,798 (2,409), mount
                1,813 (1,790), map:dest 871 (567), magic:op 979 (979), help
                1,508 (1,508), gamemap 550 (444), achievement 391 (390),
                title_type 168 (106), instancetype 276, hairface_storage_type
                417 (408), syn_skill_type 1,400, godsoul_upgrade_config 257.
    npc.ini     2,794 section headers, 531,173 B (6907: 2,325).
    regkey.dat  present at the root AND in Env_DX9/.
    encoding    codepage.ini says `1256`. SlotNpc.ini (3,959 B, 102 high
                bytes) and npc.ini (531,173 B, 574 high bytes) both decode
                strictly as GBK AND as cp1256, and both fail UTF-8. Passing
                two decoders is NOT a proof of GBK: nothing here showed the
                decoder rejecting bytes, checked for CJK, or ran a 7205
                control, all of which the owner's ruling requires.
                **`TEXT_ENCODING` stays `latin1`** (inherited; it round-trips
                every byte).

THE FINDINGS, RECORDED AS KNOWN LIMITS
--------------------------------------
    1. **Three inherited codecs are WRONG ON THIS DISK** (the same three
       6968 found); see `CODEC_ON_DISK`. One of them hid a readable table:
       `ItemtypeSub.dat` is 0 B on 6907 and declared `empty` with a refusal;
       on 7170 it is block96, 30,795 blocks 100.00% known, 1,094 rows
       intact. This plugin re-declares it and GAINS the `item:sub` subject
       over patch6907.
    2. **The measure script's "rows > 3x 6907's" noise heuristic flagged four
       subjects**: battlepass_season 1 -> 9, exchange_shop_goods 495 ->
       2,353, rune_storage_attr 22 -> 145, texas_match_prize 81 -> 5,441.
       Not tuned away and NOT treated as noise, for three measured reasons:
       every one of those files is block96 at 100.00% known on 7170 (0
       partial tables); each serves with a re-decode control; and
       `patch6907`'s own spec comments already record `texas_match_prize` as
       ~5,400 rows on "the other nine installs" against 81 on 6907. It is
       content growth after 6907. `GROWN_SINCE_6907` pins the four at their
       measured values, so a change in either direction is visible.
    3. Twelve subjects label some rows with numbers under `browse()` (e.g.
       `fuse` 3,109/3,109, `soulpart` 429/429, `npc:SlotNpc.ini` 93/93). All
       are INHERITED 6609/6907 census specs, not declarations made here;
       recorded, not changed by this plugin.

Nothing is written under `Clients/`, which stays vanilla.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from dataclasses import replace                          # noqa: E402

import inidat                                            # noqa: E402

from plugins.patch6907 import Patch6907, _version       # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "7170"

#: The twelve compiled tables, byte-identical to 6907's (and 7135's and
#: 7182's). MEASURED from `docs/patchnotes/fingerprints.json` blake2b digests;
#: `tests/test_patch7170.py` re-checks them against the 6907 install.
DBC_IDENTICAL_TO_6907 = (
    "3DEffect.dbc", "3DEffectobj.dbc", "3DObj.dbc", "3DSimpleObj.dbc",
    "3DTexture.dbc", "3dmotion.dbc", "armet.dbc", "armor.dbc", "mount.dbc",
    "mountmotion.dbc", "weapon.dbc", "weaponmotion.dbc",
)

#: The install whose `itemtype.dat` is byte-identical to this one's, MEASURED
#: from fingerprints.json (blake2b 2f2414ed..., 12,545,914 B on both). It is
#: the floor-free control for the `item` count: same ciphertext, same
#: dictionary, same reader, so the same number.
ITEMTYPE_TWIN = "7182"


class Patch7170(Patch6907):
    """Official patch 7170: 6907's art, the block96 `.dat` era, own stamp."""

    #: Explicit, not inherited: 7170 is in `tools/dat_census.py` `BUILDS` and
    #: `tests/test_dat_census.py` checks `is True`. NOT `BLOCK96_CENSUS`,
    #: which is `Patch7205`'s measured census dict.
    DAT_CENSUS_ERA = True

    name = "patch7170"
    label = "Official patch client 7170 (block96 .dat era)"
    origin = "official"
    aliases = ("7170",)
    STAMP = STAMP

    DBC_IDENTICAL_TO_6907 = DBC_IDENTICAL_TO_6907
    ITEMTYPE_TWIN = ITEMTYPE_TWIN

    notes = (
        "6907's art layer byte for byte -- all twelve .dbc and both .wdf "
        "archives are 6907's -- with block96 ini/*.dat read through the 7878 "
        "block dictionary: 99 files, 99.98% of 1,461,092 blocks known. "
        "item 43,404 rows (itemtype.dat byte-identical to 7182's), monster "
        "3,538, magic 2,798. ItemtypeSub.dat, empty on 6907, is read here "
        "(1,094 rows). Before this plugin 7170 fell to patch6907's 0.55 "
        "sibling bid; this plugin claims only the stamp 7170. Nothing is "
        "copied into Clients/, which stays vanilla."
    )

    #: **Three inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: by `measure_7170.py` (`inidat.classify` on Clients/7170, 2026-09-19);
    #: the same three `patch6968` found on its install.
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused. **7170: `block96`,
    #:                       30,795 blocks 100.00% known, 1,094 of 1,094
    #:                       lines intact.** Re-declared as `@@` rows with
    #:                       `ITEMTYPESUB_COLUMNS`: a subject GAINED.
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 7170:
    #:                       `block96`, 10,608 blocks 100.00% known, 873
    #:                       sections (871 served). Codec label only:
    #:                       `load_table` reads both families the same way.
    #:   ServerPlay.dat      6907: `block96`. 7170: `unknown` (5,603 B). The
    #:                       subject stays REFUSED; only the label changes.
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    #: `item:sub`'s columns: id 0, name 1 -- `patch7205`'s and `patch6968`'s
    #: declaration for the same file. MEASURED on 7170: 1,094 rows, no blank
    #: name, and exactly one all-digit name -- id 3316462 is named `777` in
    #: the file itself (100% known, every line intact). Pinned by
    #: `tests/test_patch7170.py:InheritedCodecsCorrectedToTheDisk`.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    #: The four subjects the measure script's ">3x 6907's rows" heuristic
    #: flagged, at their MEASURED 7170 counts (6907's in the comment). Content
    #: growth, not wrong-cipher noise: see the module docstring, finding 2.
    GROWN_SINCE_6907 = {
        "battlepass_season": 9,       # 6907: 1
        "exchange_shop_goods": 2353,  # 6907: 495
        "rune_storage_attr": 145,     # 6907: 22
        "texas_match_prize": 5441,    # 6907: 81
    }

    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the file on disk
        classifies that way TODAY. Conditional, so a different file under
        the same name falls back to 6907's declaration instead of inheriting
        a 7170 fact. Same mechanism as `patch6968`."""
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
        """0.95 on the exact stamp `7170`, else 0.0.

        **The sibling bid is deliberately NOT inherited.** `Patch6907` bids
        0.55 for every unstamped build of its era; a subclass that kept that
        bid would TIE with `patch6907` on 6968..7189 and be broken by
        registry order, which is not evidence. The stamp is the only thing
        that separates this build from its siblings -- the compiled tables
        and archives are literally 6907's files.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- quirks ------------------------------------------------------------
    def table_quirks(self):
        """6907's quirks, minus the one that stops being true on this build,
        plus 7170's own."""
        q = super().table_quirks()
        # 7170 is no longer an UNSTAMPED sibling claimed at 0.55: it has
        # this plugin. The rest of that quirk is about the other siblings.
        q.pop("ten unstamped siblings were in the same wrong state", None)
        q["7170 has its own plugin; it was a patch6907 sibling"] = (
            "Before patch7170, detect() returned patch6907 at 0.55 for this "
            "install -- the right era and 6907's uncurated spec list, column "
            "index and label keys. patch7170 claims only the stamp 7170 at "
            "0.95 and inherits that profile; measure_7170.py is where its own "
            "row counts come from.")
        q["MagicType.dat is capitalised on 7170"] = (
            "6907 and 7135 ship ini/magictype.dat; 7170 and 7182 ship "
            "ini/MagicType.dat (fingerprints.json). The inherited `magic` "
            "spec names magictype.dat and resolves through "
            "Plugin._find_table's case-insensitive match. An exact-case "
            "lookup would lose the table on this build silently.")
        q["itemtype.dat is byte-identical to 7182's"] = (
            "12,545,914 B, same blake2b on both (fingerprints.json). Its "
            "row count under a given dictionary is therefore 7182's, and "
            "that equality is the floor-free control the test uses.")
        q["regkey.dat first appears at 7170"] = (
            "Absent from 6609..7135, present from 7170 on "
            "(docs/renderer_builds_and_isometry_2026-08-29.md). Not a table "
            "this plugin reads; recorded because it is a first.")
        return q


PLUGIN = Patch7170()
