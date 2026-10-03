#!/usr/bin/env python3
r"""
patch7083 -- official Conquer Online patch 7083: a block96-era client of the
6907 family, four patches after 6907's sibling 7065, with 7065's `itemtype.dat`
byte for byte.

A THIN SUBCLASS OF `Patch6907`, AND WHY THAT PARENT
----------------------------------------------------
The first sections were written 2026-09-19 WITHOUT reading `Clients/7083`
(the machine was gated): each "MEASURED" there is a fingerprint in
`docs/patchnotes/fingerprints.json` or a number an existing test or doc
already measured on 7083, and says which. The section MEASURED BY
measure_7083.py holds the install's own numbers (script in the worktree
root, not committed).

* **Who claimed 7083 before this file: `patch6907`, at its 0.55 sibling bid.**
  `tests/test_patch6907.TheTenUnstampedSiblingsAreClaimedToo.SIBLINGS` lists
  7083 and asserts exactly that. So 7083 already opens through 6907's specs,
  and `tools/dat_census.BUILDS` already names it -- which is why this class
  keeps `DAT_CENSUS_ERA = True` (below) and takes no census slot.
* **The art layer is 6907's, which is 6609's.** MEASURED (fingerprints, b2):
  all twelve `.dbc`, `3dobj.ini`, `3dtexture.ini`, `RolePart.ini` and the
  `c3.wdf` / `data.wdf` header+index are identical to 6907's (c3.wdf
  359,069,116 B / 10,274 entries; data.wdf 392,245,257 B / 14,739 entries).
  The fingerprint covers the wdf HEADER AND INDEX, not the payload; payload
  identity is for `measure_7083.py`.
* **The content layer is the block96 era.** The four headline `.dat`
  (`itemtype`, `magictype`, `monster`, `mounttype`) are fingerprinted; that
  they classify `block96` under `core/inidat.py` is INFERRED from the era
  (6907..7189 are all block96 per `patch6907`'s docstring, which measured the
  ten installs including this one) and re-measured by the script.
* **Why not `Patch7205`:** 7205's specs are 7205's census, and 7083 is not in
  it; `patch6907`'s `SPECS_DAT_CENSUSED`, `HAIRFACE_COLUMNS`, `TITLE_COLUMNS`
  and the rest were measured on all ten of its installs, 7083 among them
  (e.g. `hairface_storage_type` 415 rows / 340 distinct col-1 ids on 7083;
  `gouyu_immortal` 10 rows "from 7083"). Those are 7083's own numbers
  already, which is the strongest reason for the parent.

MEASURED -- 7083 against its neighbours (fingerprints, b2 identity)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    file              6907        7065         7083         7110
    itemtype.dat   10,820,377  11,877,944 == 11,877,944 == 11,877,944 (7135 too)
    magictype.dat     472,252     531,896      538,608      538,787
    monster.dat       773,646   1,000,325    1,039,006    1,043,429
    mounttype.dat     433,367     437,346      437,596      438,091
    GameMap.dat        14,196      16,165       16,406       16,509
    play.exe        1,737,264   1,744,976 == 1,744,976 == 1,744,976
    Server.dat          4,352       4,608        4,608        4,608 (bytes differ)

So **7083 differs from 7065 in exactly five fingerprinted files** (the three
`.dat` above other than itemtype, `GameMap.dat`, `Server.dat`) and
`version.dat`. `itemtype.dat` is the SAME FILE on 7065, 7083, 7110 and 7135
(`docs/dict_merge_2026-09-05.md` groups them the same way), which gives this
plugin a floor-free control: its `item` row count must EQUAL what the same
reader returns on 7065.

MEASURED ELSEWHERE IN THIS REPO, ON 7083
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* `CCFL` effect bodies first appear at 7083: 19 bodies in 2 files
  (`docs/ccfl_kind_14_2026-09-16.md`, `tests/test_claim_enumeration.py`).
  Nothing in this plugin reads them; recorded as the era marker it is.
* `.OtherData` map sidecars: 9 files at 7083 (`docs/comod_backlog.md`).
* `ServerPlay.dat` on 7083 is byte-identical to 7065's
  (`tests/test_serverplay_family.py`: "7083, 7182 and 7682 repeat their
  predecessor"), so `patch6907`'s 0%-coverage refusal carries by identity of
  bytes, not by argument.
* Two clients in one install (`Env_DX8`, `Env_DX9`), DIFFERENT builds
  (`capture/coprofile/builds.json`); no memory layout recovered for either.

INFERRED (not measured, and said so)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* The grammar and columns of every 6907 spec other than the four in
  `CODEC_ON_DISK` and `item`: 6907's census measured them on 7083 among its
  ten installs, and this run confirms the row counts, not each column.

MEASURED BY measure_7083.py (2026-09-19, EXIT 0; every number is
Clients/7083's unless another install is named)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Detection, 46 installs** (`plugins._rank`, with and without this
  plugin): winner or margin changes on 7083 ONLY -- `patch7083` 0.95,
  margin 0.40, where it was `patch6907` 0.55. `patch7083` scores 0 on the
  other 45. Controls: 6907 -> `patch6907` 0.95, 7205 -> `patch7205` 0.95.
* **Containers:** `c3.wdf` + `data.wdf`, payload sha256-IDENTICAL to 6907's
  (`ab68f57cc24ae100`, `fc628e4adeb7de48`). `part_tables()`: nine slots,
  identical to 6907's. The 15 `IDENTICAL_TO_6907` files: all identical.
* **block96, POSITIVE CONTROL FIRST:** 7205 read 101 files / 809 unknown /
  99 full, matching `Patch7205.BLOCK96_CENSUS` -> PASS. **7083: block96
  CONFIRMED -- 89 files, 1,377,680 blocks, 6,822 unknown (99.50% known);
  74 full, 14 PARTIAL, 1 at zero (`levexp.dat`).** 6907 for context: 70
  files, 762 unknown of 1,184,672, 68 full. Headline tables:
      itemtype.dat     989,828 blocks 100.00%   41,323 rows intact
      monster.dat       86,583 blocks 100.00%    3,134 sections
      magictype.dat     44,884 blocks  99.23%    2,540 of 2,635 lines
      mounttype.dat     36,466 blocks 100.00%    1,807 sections
      MapDestination     9,581 blocks 100.00%      756 sections
  The 14 partial tables are `PARTIAL_TABLES` -- a KNOWN LIMIT of the
  7878-derived dictionary, recorded, not tuned away.
* **`catalogs()`: 240 subjects, 236 ok** (6907: 222 / 218). The same reader
  on 7065 returns item **41,323** -- EQUAL to 7083's, as the identical
  `itemtype.dat` requires (PASS). Those are the measure run's numbers,
  taken before `CODEC_ON_DISK` existed. **After it (finalize step 5b, a
  set difference on Clients/7083):** 240 subjects, **237 ok**; `patch6907`'s
  ok set minus this plugin's is EMPTY, GAINED `item:sub` (1,077 rows, a
  FLOOR from >=1,528 lines; the name in column 1 on every row --
  `SageModeExclusive` -- 0 numeric, 0 blank), and no row count differs on
  any shared subject. Still refused, deliberately: `levexp` (0% coverage),
  `serverplay` (no route) and `action` (binary, columns unknown).
  Rows (6907 -> 7083): item 38,346 -> 41,323, monster 2,199 -> 3,102,
  magic 2,409 -> 2,557, mount 1,790 -> 1,807, map:dest 567 -> 754,
  magic:op 979 -> 979, achievement 390 -> 391, title_type 106 -> 150,
  hairface_storage_type 408 -> 415, instancetype (absent on 6907) 42,
  gouyu_immortal 10.
* **The ">3x the parent" flag fired twice and both are CONTENT, not
  wrong-cipher noise:** `battlepass_season` 1 -> 4 (4 of 4 lines intact,
  28 blocks at 100%) and `texas_match_prize` 81 -> 5,407. The latter's file
  grew 14,584 B (6907) -> 228,647 B (7065) -> 228,397 B (7083); on 7083 it
  decodes at 99.66% of 19,033 blocks into 5,420 lines, 5,361 of them 6
  fields wide, column 0 numeric on 5,407/5,407 (`2000@@1090@@3500000000@@
  0@@0@@[item Cash,PokerKing]`). 7065 reads 5,437 under the same reader.
* **FINDING -- four inherited codecs are wrong on this disk**
  (`inidat.classify`, phase 4c): `ItemtypeSub.dat` is `block96` (6907
  declares `empty` and REFUSES it), `MapDestination.dat` is `block96`,
  `ServerPlay.dat` is `unknown`, and `texas_match_prize.dat` is
  `block96-candidate-refuted` (7065's is `block96`). Corrected in
  `CODEC_ON_DISK` / `table_specs`; `item:sub` becomes READABLE here, a
  subject gained over `patch6907` (numbers in `tests/test_patch7083.py`).
* **Item columns:** 41,323 rows, ALL 66 fields wide (6907: 64). Column 52
  is still the item class -- `Gift` 9,815, `QuestItem` 2,354, `EpicWeapon`
  1,945, `GiftPack` 1,438, `Garment` 491, `Pack` 467 -- and column 1 is
  non-numeric on 41,322/41,323, so `ITEM_COLUMNS` holds here as measured.
* **Encoding: latin1 kept.** `SlotNpc.ini` (3,959 B, 102 high bytes)
  decodes strictly as GBK, and `codepage.ini` says `1256`. That is ONE
  table with no CJK count, no rejection probe and no 7205 control, which is
  short of the per-table proof the owner's GBK ruling requires, and 7083 is
  before 7205. Recorded, not acted on.
* Script surface: Lua 148, DLL 35 (6907: 47 / 30). `npc.ini` 2,647 section
  headers, 497,626 B (6907: 2,325).
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import inidat                                           # noqa: E402

from plugins.patch6907 import Patch6907, _version      # noqa: E402


class Patch7083(Patch6907):
    """Official patch 7083: 6907's profile, 7065's itemtype.dat."""

    name = "patch7083"
    label = "Official patch client 7083 (block96 .dat era)"
    origin = "official"
    aliases = ("7083",)
    STAMP = "7083"

    #: Stated, not merely inherited: `tools/dat_census.BUILDS` already names
    #: 7083 and `tests/test_dat_census.py` asserts every BUILDS entry detects
    #: as a plugin whose `DAT_CENSUS_ERA is True`. The block96 cipher on
    #: 7083 is recorded by `patch6907` over its ten installs; if
    #: `measure_7083.py` refutes it, this becomes a STOP, not an edit.
    DAT_CENSUS_ERA = True

    notes = (
        "6907's art layer (all twelve .dbc and the .wdf indexes identical by "
        "fingerprint) with the block96 .dat cipher, read through patch6907's "
        "profile, whose census already measured this install. itemtype.dat "
        "is byte-identical to 7065's, 7110's and 7135's; magictype, monster, "
        "mounttype and GameMap grew from 7065. First build carrying CCFL "
        "effect bodies (19, in 2 files). Rows with an unknown block are "
        "dropped, never guessed; nothing is copied into Clients/."
    )

    #: `itemtype.dat` is one file across these four stamps (MEASURED,
    #: fingerprints b2 `581cf9eb...`, 11,877,944 B). The basis of the
    #: floor-free item control in `tests/test_patch7083.py`.
    ITEMTYPE_SHARED_WITH = ("7065", "7110", "7135")

    #: Files MEASURED (fingerprints) identical to 6907's: the art layer.
    IDENTICAL_TO_6907 = (
        "ini/3DEffect.dbc", "ini/3DEffectobj.dbc", "ini/3DObj.dbc",
        "ini/3DSimpleObj.dbc", "ini/3DTexture.dbc", "ini/3dmotion.dbc",
        "ini/armet.dbc", "ini/armor.dbc", "ini/mount.dbc",
        "ini/mountmotion.dbc", "ini/weapon.dbc", "ini/weaponmotion.dbc",
        "ini/3dobj.ini", "ini/3dtexture.ini", "ini/RolePart.ini")

    #: The fingerprinted files whose bytes moved from 7065 (plus
    #: `version.dat`). Their declared grammar and columns are 6907's census,
    #: which measured 7083 -- but `measure_7083.py` re-reads them.
    CHANGED_FROM_7065 = ("ini/GameMap.dat", "ini/magictype.dat",
                         "ini/monster.dat", "ini/mounttype.dat", "Server.dat")

    # No `BLOCK96_CENSUS` here: that name is Patch7205's measured census DICT
    # and Patch6907 does not define it. 7083's own census numbers are below.

    #: MEASURED (measure_7083.py phase 4c): the ini/**.dat block96 surface.
    BLOCK96_SURFACE_7083 = {"files": 89, "blocks": 1377680, "unknown": 6822,
                            "fully_covered": 74, "partial": 14,
                            "uncovered": 1}

    #: `{file: (blocks known %, units served)}`, MEASURED on 7083. A KNOWN
    #: LIMIT of the 7878-derived dictionary: records with an unknown block
    #: are dropped, never guessed, so these subjects serve fewer rows than
    #: the file holds.
    PARTIAL_TABLES = {
        "AutoUseMagic.dat": (84.42, 14),
        "battlepass_score_reward.dat": (86.29, 258),
        "coat_storage_type.dat": (90.46, 572),
        "exchange_shop_goods.dat": (85.25, 915),
        "instance_enter_condition.dat": (91.02, 117),
        "instancetype.dat": (76.56, 42),
        "ItemtypeSub.dat": (94.19, 1077),
        "MagicType.dat": (99.23, 2557),
        "Operating_Prize.dat": (93.43, 509),
        "task_reward_type.dat": (81.31, 140),
        "texas_match_condition.dat": (98.58, 55),
        "texas_match_stage.dat": (99.91, 55),
        "texas_match_type.dat": (99.58, 154),
        "title_type.dat": (98.40, 150),
    }

    #: **Four inherited specs whose codec is WRONG ON THIS DISK**, MEASURED
    #: by `measure_7083.py` phase 4c (`inidat.classify` on Clients/7083,
    #: 2026-09-19). 6907's list declares each from 6907's bytes. The first
    #: three are the same correction `patch6968.CODEC_ON_DISK` made on 6968;
    #: the fourth goes the other way and is 7083's own:
    #:
    #:   ItemtypeSub.dat     6907: 0 B, `empty`, refused. **7083: `block96`,
    #:                       48,090 blocks, 94.19% known, 881 of 1,528 lines
    #:                       intact, 1,077 units.** The inherited refusal is
    #:                       FALSE here, so the subject is re-declared as `@@`
    #:                       rows (7205's and 6968's grammar for the file) --
    #:                       a subject GAINED over `patch6907` on this
    #:                       install, and a PARTIAL one (`PARTIAL_TABLES`).
    #:   MapDestination.dat  6907: `block96-candidate-refuted`. 7083:
    #:                       `block96`, 9,581 blocks 100.00%, 756 sections.
    #:   ServerPlay.dat      6907: `block96`. 7083: `unknown`. Stays REFUSED;
    #:                       only the label moves. Byte-identical to 7065's.
    #:   texas_match_prize   6907 and 7065: `block96`. **7083:
    #:                       `block96-candidate-refuted`** (228,397 B), and
    #:                       it still opens: 5,407 rows, 99.66% of 19,033
    #:                       blocks known.
    #:
    #: Codec labels only for three of the four: `Patch6907.load_table` reads
    #: `block96` and `block96-candidate-refuted` the same way. Each correction
    #: applies only while the classifier agrees TODAY (see `table_specs`).
    CODEC_ON_DISK = {
        "itemtypesub.dat": inidat.Family.BLOCK96,
        "mapdestination.dat": inidat.Family.BLOCK96,
        "serverplay.dat": inidat.Family.UNKNOWN,
        "texas_match_prize.dat": inidat.Family.BLOCK96_REFUTED,
    }

    #: `item:sub`'s columns: id 0, name 1 -- `patch7205`'s and `patch6968`'s
    #: declaration for the same file. Checked on 7083 by
    #: `tests/test_patch7083.py:ItemSubIsReadHere`.
    ITEMTYPESUB_COLUMNS = {"id": 0, "name": 1}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `7083`, at 0.95, and nothing else.

        Deliberately NOT `Patch6907.confidence`: that one also makes the
        0.55 sibling bid for every unstamped block96 client, and a subclass
        inheriting it would bid for 6968..7189 under this plugin's name.
        Every format probe answers the same on 7065 and 7083 (identical
        `.dbc`, identical `itemtype.dat`), so only the stamp discriminates.
        """
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """6907's specs, with `CODEC_ON_DISK` applied where the classifier
        agrees on the file on disk TODAY -- a different file under the same
        name falls back to 6907's declaration rather than inheriting a 7083
        fact. Same mechanism as `patch6968.table_specs`."""
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
    def table_quirks(self):
        q = super().table_quirks()
        q["the figures in the quirks above are 6907's"] = (
            "Every number in the inherited quirks was measured on "
            "Clients/6907 unless it names another build. 7083 shares the "
            "mechanism (identical .dbc, block96 .dat) and patch6907's "
            "per-table census already covered it, but its itemtype.dat is a "
            "different, larger file (7065's, 11,877,944 B against 6907's "
            "10,820,377 B), so 6907's item and monster figures are not "
            "7083's.")
        q["CCFL starts here"] = (
            "7083 is the first build carrying CCFL effect bodies: 19, in 2 "
            "files (docs/ccfl_kind_14_2026-09-16.md). No reader in this "
            "plugin consumes them.")
        return q

    def colour_provenance(self):
        """Inherited through 6907 from the 6090 scan; unverified here."""
        return ("colourway inherited from 6090 via 6609 and 6907 "
                "(unverified on any of them)", "inferred")


PLUGIN = Patch7083()
