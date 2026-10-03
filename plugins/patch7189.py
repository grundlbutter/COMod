#!/usr/bin/env python3
r"""
patch7189 -- official Conquer Online patch 7189, the LAST install of the
`patch6907` sibling era before 7205, and the one whose `itemtype.dat` IS
7205's.

A thin `Patch6907` subclass. What it adds is an exact-stamp claim, the
byte-identity facts that tell a reader which of its neighbours each file
belongs to, and a list of what is still NOT measured on this build.

WHY THE PARENT IS `Patch6907` AND NOT `Patch7205`
-------------------------------------------------
Both were candidates, and the file-level evidence points both ways, so the
choice is stated rather than implied.

  * **Art layer: 6907's, byte for byte.** MEASURED from
    `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` (blake2b per file,
    taken before 2026-09-19's owner renames; 7189 was not renamed): all twelve
    `ini/*.dbc`, `c3.wdf` and `data.wdf` (header + index) and the frozen
    `3dobj.ini` / `3dtexture.ini` carry the SAME digest on 6907, 7182, 7189
    and 7205. The art does not decide between the two parents.
  * **Content layer: this plugin's `.dat` spec list is the one MEASURED ON
    7189.** `patch6907.SPECS_DAT_CENSUSED` and `UNDECLARED_DAT` were built
    from a per-build census over ten installs that INCLUDES 7189
    (`combat_gear.dat` on 7189 alone; `hairface_storage_type` 417 rows /
    0 short on 7189; the `globallotterycondition` and `task_reward_type`
    `.ini`/`.dat` twin sizes were measured on 7189). `patch7205`'s specs
    were measured on 7205's files, of which 7189 shares only some.
  * **`tools/dat_census.py` `BUILDS` lists 7189** and
    `tests/test_dat_census.py` asserts every listed build detects as a
    plugin with `DAT_CENSUS_ERA is True`. Inherited from `Patch6907`, and set
    explicitly below.

So `Patch6907`'s profile is the MEASURED one for this client and `Patch7205`'s
would be an inference. `measure_7189.py` (not committed; worktree root) runs
BOTH profiles over this install side by side, so the choice can be refuted.

BYTE IDENTITY, FROM fingerprints.json (NOT a read of the install)
-----------------------------------------------------------------
blake2b prefixes, file size in bytes::

    file              6907            7182            7189            7205
    ini/itemtype.dat  45baf7e4485c    2f2414eded68    fc37ce2d1328 == fc37ce2d1328
                      10,820,377      12,545,914      12,966,331      12,966,331
    ini/RolePart.ini  3fd40231c7f6 == 3fd40231c7f6    c9d8064928bb == c9d8064928bb
    play.exe          c8c2f2ec66b3    6f0282b639cd == 6f0282b639cd == 6f0282b639cd
    ini/MagicType.dat 4039bc56e6ba    6089570bdb7b    9a7e5af60364    dc0f2c5f7f76
    ini/monster.dat   6263b07980fc    ad01a8d1df9c    96beede6c15d    5e3a4c4ebacf
    ini/mounttype.dat c53fb214993a    75a644ea7b91    96a611715e70    0f3fd4d85a49
    ini/GameMap.dat   3383bca18754    f277b317b766    4b1d66452ffc    7dd793ed1982

**THE ITEM TABLE IS 7205's.** `ini/itemtype.dat` is byte-identical across
7189, 7205, 7217 and 7250 in fingerprints.json, and differs from 7182's. So
the item catalogue this plugin serves must EQUAL the one `patch7205` serves on
7205 under the same dictionary -- a floor-free control that noise cannot
satisfy (`tests/test_patch7189.py:Catalogs`). 7205's is 44,684 rows,
re-measured 2026-09-07 (`patch7205` docstring), and `measure_7189.py`
MEASURED the same 44,684 on 7189 (below).

`itemtype.dat` on 7205 carries **65** fields per row (`patch7205`, against
6868's TQ file). `Patch6907.ITEM_COLUMNS` places `itemClass` at column 52,
measured on 6907's 64-field rows and on 7878's 68-field rows. **MEASURED on
7189: it holds here too** -- column 52 is non-numeric on 44,684 of 44,684
rows, top values `Gift` 12,065, `QuestItem` 2,376, `EpicWeapon` 1,947,
`GiftPack` 1,490, `Garment` 514, `Pack` 470 (every row splits into 66 `@@`
pieces: 65 fields and a trailing delimiter).

`RolePart.ini` also moves with 7205 (7189 = 7205 = 7217), not with 7182 --
so on these two files the 7182 -> 7189 step is where 7205's content arrived.
`MagicType.dat`, `monster.dat`, `mounttype.dat` and `GameMap.dat` are 7189's
own: different from both neighbours, sizes between theirs.

MEASURED 2026-09-19 (`measure_7189.py`, EXIT 0, Clients/7189)
-------------------------------------------------------------
Positive controls first, all PASS: 6907 detects as `patch6907` 0.95, 7205 as
`patch7205` 0.95; 7205's block96 census reproduces `Patch7205`'s pin (101
files / 809 unknown / 99 full).

* **Detection**, 46 installs ranked: ONLY 7189's winner moved (`patch6907`
  0.55 -> `patch7189` 0.95, margin 0.40); `patch7189` scores 0 on every
  other install. `detect(7189).DAT_CENSUS_ERA is True`.
* **Containers**: `c3.wdf`, `data.wdf`; 12 `ini/*.dbc`; no `.tpd`/`.tpi`.
  `part_tables()`: 9 slots, same source and row count as 6907's.
* **block96 is CONFIRMED, not refuted**: 99 block96 files, 1,465,491 blocks,
  335 unknown (99.98% known); 98 fully known, 0 partial, 1 at zero --
  `levexp.dat` (335 blocks, the held-out seed-1234 table, as on 6907/7205).
  `itemtype.dat` 1,080,527 blocks 100% / 44,684 rows; `monster.dat` 100,715
  100% / 3,628 sections; `magictype.dat` 47,694 100% / 2,905;
  `mounttype.dat` 36,636 100% / 1,815; `combat_gear.dat` 1,012 100% / 150.
  (Older-dictionary figures, `docs/dict_merge_2026-09-05.md` item 8,019 at
  88.6%, are superseded.)
* **Catalogs** (Patch7189 on 7189): **249 subjects, 245 ok**. Refused, all
  four also refused by `patch6907` on this install: `action` (binary,
  columns unknown), `item:sub` (0-byte file here -- see below), `levexp`
  (0% blocks), `serverplay` (`unknown`). Rows: item 44,684 (EQUAL to 7205's
  under `patch7205`, the byte-identity control), monster 3,589 (NOT refused
  here, unlike 6907), magic 2,905, mount 1,815, map:dest 936, magic:op 979,
  achievement 391, title_type 169, instancetype 276, combat_gear 150,
  hairface_storage_type 417. 6907 for scale: item 38,346, monster 2,199,
  magic 2,409, mount 1,790, map:dest 567 (222 subjects, 218 ok).
  The `Patch7205` profile on 7189 gives the same headline numbers but 247
  subjects / 244 ok -- fewer than this profile, which is the measured reason
  the parent stays `Patch6907`.
* **Five subjects over 3x 6907's rows are real growth, not noise**: each is
  100% block-known with lines == intact rows on 7189 -- `texas_match_prize`
  81 -> 5,441 (6907's docstring already records ~5,400 on the later nine),
  `exchange_shop_goods` 495 -> 1,612, `rune_storage_attr` 22 -> 145,
  `operateweb` 5 -> 17, `battlepass_season` 1 -> 11.
* **Codec labels that differ from 6907's on this disk** (inidat):
  `MapDestination.dat` is `block96` (6907: `block96-candidate-refuted`),
  `ServerPlay.dat` is `unknown` (6907: `block96`). Corrected by
  `CODEC_ON_DISK`, label only; both subjects read exactly as before.
  **`ItemtypeSub.dat` is 0 bytes (`empty`) on 7189**, unlike 7065 and 7205
  where it is a block96 table with rows, so 6907's `empty` declaration is
  RIGHT here and `item:sub` stays refused -- a KNOWN LIMIT of this client.
* **Encoding: NOT PROVEN, `TEXT_ENCODING` stays `latin1`.** Measured:
  `codepage.ini` says `1256`; `SlotNpc.ini` (102 bytes >= 0x80) and
  `npc.ini` (588) decode strict-GBK without error. That is compatibility,
  not the positive proof the owner's ruling requires (name fields of two
  tables, CJK present, a reject probe, a 7205 control), and 7189 precedes
  7205.

Still NOT measured: colour/name data (inherited from the 6090 scan).
Nothing here writes under `Clients/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from dataclasses import replace                         # noqa: E402

import inidat                                           # noqa: E402

from plugins.patch6907 import Patch6907, _version       # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "7189"

#: Files whose blake2b in `fingerprints.json` equals 7205's. Kept as data so
#: the install-bound test can re-check each by bytes AND check the control
#: (`CHANGED_FROM_7205` must differ), rather than trusting this docstring.
IDENTICAL_TO_7205 = ("itemtype.dat", "RolePart.ini")

#: Files whose blake2b in `fingerprints.json` differs from 7205's.
CHANGED_FROM_7205 = ("MagicType.dat", "monster.dat", "mounttype.dat",
                     "GameMap.dat")

#: The twelve compiled tables -- identical on 6907, 7182, 7189 and 7205 in
#: `fingerprints.json`.
FROZEN_COMPILED = ("3DEffect.dbc", "3DEffectobj.dbc", "3DObj.dbc",
                   "3DSimpleObj.dbc", "3DTexture.dbc", "3dmotion.dbc",
                   "armet.dbc", "armor.dbc", "mount.dbc", "mountmotion.dbc",
                   "weapon.dbc", "weaponmotion.dbc")


class Patch7189(Patch6907):
    """Official patch 7189: 6907's profile, measured on this build's census;
    7205's item table."""

    #: Literal True, and deliberately restated rather than left to
    #: inheritance: `tools/dat_census.py` BUILDS lists 7189 and
    #: `tests/test_dat_census.py` asserts `is True` against `detect`. NOT
    #: `BLOCK96_CENSUS`, which is Patch7205's measured census dict.
    DAT_CENSUS_ERA = True

    name = "patch7189"
    label = "Official patch client 7189 (block96 .dat era)"
    origin = "official"
    aliases = ("7189",)
    STAMP = STAMP

    IDENTICAL_TO_7205 = IDENTICAL_TO_7205
    CHANGED_FROM_7205 = CHANGED_FROM_7205
    FROZEN_COMPILED = FROZEN_COMPILED

    #: **Two inherited specs whose codec label is WRONG ON THIS DISK**,
    #: MEASURED 2026-09-19 (`inidat.classify` on Clients/7189,
    #: measure_7189.py): `MapDestination.dat` is `block96` (6907 declares
    #: `block96-candidate-refuted`) and `ServerPlay.dat` is `unknown` (6907
    #: declares `block96`). Label only -- `load_table` reads both block96
    #: families the same way and ServerPlay stays refused. Applied only where
    #: the classifier agrees TODAY, so a changed file falls back to 6907's
    #: declaration. `ItemtypeSub.dat` is deliberately ABSENT: it is 0 bytes
    #: (`empty`) on 7189, which is what 6907 already declares. Same mechanism
    #: as `patch6968` / `patch7065`, measured independently here.
    CODEC_ON_DISK = {
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
    }

    notes = (
        "6907's profile on 7189, whose .dat census already covered this "
        "build. Art layer byte-identical to 6907's and 7205's (twelve .dbc, "
        "both .wdf indexes). ini/itemtype.dat is byte-identical to 7205's "
        "and reads the same 44,684 rows. MEASURED: 99.98% of 1,465,491 "
        "block96 blocks known (levexp.dat alone at 0), 245 of 249 subjects "
        "open -- item 44,684, monster 3,589, magic 2,905, mount 1,815. "
        "ItemtypeSub.dat ships 0 bytes here. Claimed by its exact "
        "version.dat stamp at 0.95 instead of patch6907's 0.55 sibling bid."
    )

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """0.95 on the exact `version.dat` stamp `7189`; 0.0 otherwise.

        **No sibling bid.** `Patch6907.confidence` bids 0.55 for any unstamped
        sibling of its era; inheriting that would make this plugin tie with
        its parent on 6968..7182 and win by registration order. Overridden
        whole, so the only install whose winner moves is 7189.
        """
        return 0.95 if _version(root) == self.STAMP else 0.0

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees on this disk today."""
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
            out.append(replace(s, codec=want))
        return tuple(out)

    # -- quirks ------------------------------------------------------------
    def table_quirks(self):
        """6907's quirks, plus the two that are 7189's own."""
        q = super().table_quirks()
        q["itemtype.dat is 7205's file, not 7182's"] = (
            "blake2b fc37ce2d1328, 12,966,331 B, identical on 7189, 7205, "
            "7217 and 7250 in fingerprints.json; 7182 ships a different "
            "12,545,914 B file. RolePart.ini moved to 7205's at the same "
            "step. The item rows served here must therefore equal 7205's "
            "under the same dictionary, and they do: 44,684 on both "
            "(measure_7189.py, 2026-09-19).")
        q["ItemtypeSub.dat is 0 bytes on 7189"] = (
            "7065 and 7205 ship it as a block96 table with rows; 7189 ships "
            "it empty, so item:sub is refused here as it is on 6907 -- a "
            "fact about this client, not the reader.")
        q["the figures in the inherited quirks are 6907's"] = (
            "Every number quoted in patch6907's quirks was measured on "
            "Clients/6907. 7189's own (measure_7189.py): 99.98% of 1,465,491 "
            "block96 blocks known, 0 partial tables, levexp.dat alone at 0; "
            "item 44,684, monster 3,589 (not refused here), magic 2,905, "
            "mount 1,815.")
        return q


PLUGIN = Patch7189()
