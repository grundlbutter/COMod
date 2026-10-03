#!/usr/bin/env python3
r"""
patch6805 -- the official Conquer Online patch 6805 client.

**A thin `Patch6609` subclass, because 6805 is 6609's art layer with later
TQ-cipher content, and nothing else was found to differ.** It sits in the
last stretch of the TQ-stream era: 6772, 6805 and 6868 are the three builds
`core/block96.py` records as `KEY_ERA_BEFORE`, and 6907 is where the `.dat`
cipher changes. So this is NOT a `Patch6907` child: that parent's `.dat`
specs are `block96`, and on this build they would decode to noise.

WHY `Patch6609` AND NOT `Patch6090` OR `Patch6907`
-------------------------------------------------
Decided from `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` (blake2b of
every significant file per install, measured before 2026-09-19), NOT from the
install itself -- the install was off-limits while this was written:

    vs 6609   19 of 27 significant files byte-identical: ALL TWELVE `.dbc`,
              both `.wdf` headers+indexes (c3 10,274 entries, data 14,739),
              `3dobj.ini`, `3dtexture.ini`, `RolePart.ini`, the 41-byte root
              `Conquer.exe` stub, `AutoPatch.dat`. Same file SET: no file in
              either that the other lacks.
    vs 6090    6 of 27 identical; 13 `.dbc`/`.dat` differ; 6090 ships
              `EmotionIco.dbc`, `misc.dbc`, `miscmotion.dbc` and the
              capitalised `Monster.dat`/`MagicType.dat`, which 6805 does not.
    vs 6907   18 of 27 identical -- the `.dbc`/`.wdf` art layer is the same
              one again -- but `itemtype.dat`, `monster.dat`, `magictype.dat`
              and `mounttype.dat` all differ, and 6907's are `block96`.

6805 ships lowercase `monster.dat` and `magictype.dat`, exactly the 6609
renames `SPECS_6609` exists for. So `SPECS_6609` fits the file set as-is.

MEASURED (by others, in this repo, before this plugin)
------------------------------------------------------
* **The `.dat` cipher is TQ-stream, not `block96`.** `core/block96.py`:
  coverage of the 7878 block dictionary on 6805's `itemtype.dat` is 0.0% --
  38 of 797,654 blocks, all 38 traced to the poisoned `UserHelpInfo.ini.dat`
  pair. Zero genuine entries cross. Hence `DAT_CENSUS_ERA = False`.
* **`itemtype.dat` is 65 fields wide** (`core/tqdat.py`, `AT_WIDTH_NO_WEIGHT`,
  measured on 6680/6707/6772/6805/6868), against 6609's 66. `tqdat` picks the
  names by the MEASURED width of each file, so no override is needed here --
  but a caller that hard-codes 6609's column indexes past 11 is off by one.
* **`itemtype.dat` is byte-identical to 6868's** (fingerprints: b2 `20bc4903`,
  9,571,858 bytes; 6609's is 6,706,668, 6772's 8,841,840). 6868's reads
  33,639 items (`tests/test_patch7205.py`, the regression control), so 6805's
  `item` count is EXPECTED to equal it -- a control that can fail, checked in
  `measure_6805.py`, not asserted here.
* `play.exe` is also byte-identical to 6868's (833,296 bytes) and differs from
  6609's (1,485,296). Recorded; nothing here reads it.
* **Two client environments.** `capture/coprofile/builds.json` measured
  `6805/Env_DX8/Conquer.exe` and `6805/Env_DX9/Conquer.exe` as DIFFERENT
  binaries (2026-08-29); the root `Conquer.exe` is the 41-byte stub (the
  fingerprint agrees). `ENV_TREES` is therefore inherited, not re-declared.

MEASURED ON Clients/6805 (`measure_6805.py`, 2026-09-19T18:02Z, EXIT 0,
16 PASS / 0 FAIL; plus one 5b set-difference script the same evening)
--------------------------------------------------------------------------
* **Detection**, every install on disk (37 Clients folders plus
  `C:/Program Files/Classic Conquer 2.0`): ONLY 6805 changes its winner --
  `patch6090` 0.45 -> `patch6805` 0.95, margin 0.50 -- and `patch6805` scores
  0.00 on every other install. Positive control first: 6609 -> `patch6609`.
* **Container layout**: `AssetRoot._discover_archives` = `c3.wdf`
  359,069,116 B + `data.wdf` 392,245,257 B, identical to 6609's; no `.tpd`.
  `version.dat` = `b'6805'`. Root `Conquer.exe` 41 B; `Env_DX8/Conquer.exe`
  10,760,592 B and `Env_DX9/Conquer.exe` 10,758,032 B. ABSENT_HERE: all three
  absent. **All 12 `.dbc` byte-identical to 6609's** (sha256, 12/12);
  `itemtype.dat` byte-identical to 6868's.
* **`ini/`**: 325 files (6609: 297) -- 197 `.ini`, 95 `.dat`, 16 `.lua`,
  12 `.dbc`, 3 `.rgn`, 1 `.wdb`, 1 `.txt`.
* **Appearance (`part_tables()`)**, identical to 6609's: armet 2,793 ·
  armor 3,754 · weapon 13,292 · Mount 1,223 (all from the `.dbc` twins);
  `head.ini` and `misc.ini` 0 rows on both builds.
* **`.dat` families (`inidat.classify`, 95 files)**: tq-stream 56 ·
  rar-mangled 11 · rsa-mysqldump 10 · plaintext 7 · binary-plain 6 · empty 3 ·
  shift-obfuscated 1 · unknown 1 (`levexp.dat`). **No block96**: every
  tq-stream file is 0.00% in the block dictionary, against the positive
  control 6907 `itemtype.dat` at 100.00% of 901,698 blocks.
* **`itemtype.dat`**: 33,639 rows by `tqdat`; 65 fields per row in the
  catalog control (`id=50000 name='SpeedArrow' (65 fields)`).
* **`shlayout.dat` still does not open**: 37.9% / 38.1% printable at seeds
  9527 / 1234 (`shlayout800x600.dat` 38.7% / 38.5%) -- same shape as 6609.
* **`catalogs()`**: 165 subjects, **152 ok** (after the 6090 donor fix below;
  162 / 151 in the measure run). 6609 on its own install: 164 / 162. Selected
  counts, 6805 (6609): item 33,639 (24,270) re-decode, and **equal to 6868's
  33,639 through the same reader**; magic 1,892 (1,551); monster 1,770
  (1,299); mount 1,777 (1,764); map:dest 483 (371); itemadd 20,736 (19,680);
  item:refine 160 (92) and the other four `item_refine_*` as on 6609; region
  296 (258); npc:npc.ini 2,132 (2,750); emotionico 108 (6609 does not ship it).
* **5b, subjects lost against the previous winner** (`patch6090` on 6805, 143
  subjects / 131 ok): **`emotionico` was lost** by the pre-fix plugin, because
  6609's census does not declare `EmotionIco.ini` and 6805 ships it. Fixed by
  `CENSUS_DONORS`; the difference is now EMPTY. 21 subjects gained.
* `ROW_COLUMNS` hold on 6805: region col 6 non-numeric 296/296, VipTrans col 2
  31/31, restrain col 2 5/5; EventTypeName reads with control `id=01
  name='Pheasant'`. (The measure script's whitespace probe reported 0/24 for
  EventTypeName; that probe split on spaces where the table does not, so it
  is a defect in the probe, not in the column.)

KNOWN LIMITS (findings, measured, not tuned away)
-------------------------------------------------
* **TEN `.ini` ARE UTF-16LE WITH A BOM on 6805** and refuse (`parsed 0
  sections but no control`): ActionLee3DEffect, chatSetup, ControlAction,
  ItemQuenchTipInfo, KongfuBourn, MsgBoxEx, Storage, Title, WrapTypeData and
  info.ini. 6609 ships the first nine as single-byte text and reads them
  (e.g. Storage 141, WrapTypeData 300, ItemQuenchTipInfo 415 rows); on 6805
  each file is ~2x the size and starts `FF FE`. `patch6090` refuses them here
  too, so this is not a regression -- but it is 10 tables nobody reads on
  this build. Pinned in `tests/test_patch6805.py::KnownLimits`.
* `goldenleagueshop` (`GoldenLeagueShop.ini`, borrowed from 6090's census)
  parses 51 rows with no control: refused, as `patch6090` also refuses it.
* `action` (binary record layout) and `levexp` (grammar undeclared): refused
  exactly as on 6609.
* **Real content shrinkage, not reader loss** (file sizes, 6609 -> 6805):
  `ItemtypeSub.dat` 2,238,432 -> 85,098 B (item:sub 7,402 -> 251),
  `ItemTexture.ini` 472,808 -> 22,847 B (1,966 -> 116), `3DFlyingObj.ini`
  27,685 -> 336 B (240 -> 3), `npc.ini` 477,453 -> 387,015 B (2,750 -> 2,132),
  `ActionSound.ini` 141,811 -> 112,039 B (4,326 -> 3,466).

INFERRED (lineage, not a measurement of 6805)
---------------------------------------------
* Monster colourways and entity-name corrections: 6090's visual scan, via
  6609. The `.wdf` archives are the same, but nobody has looked on 6805, so
  `colour_provenance` says "inferred".
* **`TEXT_ENCODING = latin1`, declared, and NOT proven either way.**
  `codepage.ini` is present and reads `1256` (as on 6609); `SlotNpc.ini`
  (3,918 B, 102 high bytes) decodes as GBK, but no rejection probe, no second
  table and no 7205 control were run, so that is not the owner's GBK proof.
  Builds before 7205 keep latin1 unless proven otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch6609 import Patch6609, SPECS_6609, _version   # noqa: E402
from plugins.catalog import censused                           # noqa: E402


#: Whose `.ini` census this build borrows, in priority order. 6609 first
#: (the art layer is 6609's); **6090 second, and it is not optional**:
#: MEASURED 2026-09-19 on Clients/6805, `Patch6090().catalogs()` -- the
#: detection winner before this plugin -- read `emotionico`
#: (`ini/EmotionIco.ini`, 108 rows) which a 6609-only census does not declare,
#: because 6609 does not ship that file. 6805 does. Without the 6090 donor this
#: plugin read LESS than its predecessor on its own install.
CENSUS_DONORS = ("patch6609", "patch6090")


class Patch6805(Patch6609):
    name = "patch6805"
    label = "Official patch client 6805"
    origin = "official"
    aliases = ("6805",)

    #: **Literal False, and measured, not a default.** 6805's `ini/*.dat` are
    #: TQ-stream at seed 9527, not `block96`: 0.0% of `itemtype.dat`'s blocks
    #: are in the 7878 dictionary (`core/block96.KEY_ERA_BEFORE`), so
    #: `tools/dat_census.py`'s block96 census has nothing to count here and
    #: its `# slot "6805",` line stays commented. `tests/test_dat_census.py`
    #: checks `is True`, so this must stay the literal. NOT `BLOCK96_CENSUS`,
    #: which is Patch7205's measured census dict.
    DAT_CENSUS_ERA = False

    #: Declared rather than inherited, so it is visibly a decision: the
    #: owner's GBK ruling applies to the 7205 lineage on PROOF, and no proof
    #: was run here (see the module docstring, INFERRED).
    TEXT_ENCODING = "latin1"

    notes = ("6609's art layer (all twelve .dbc and both .wdf byte-identical "
             "to 6609's by fingerprint) with later content: TQ-cipher .dat at "
             "seed 9527, before the 6907 block96 switch; itemtype.dat is "
             "65 fields wide and byte-identical to 6868's (33,639 items). Real "
             "binaries in Env_DX8/ and Env_DX9/. 6609's and 6090's .ini "
             "census borrowed, filtered to disk: 165 subjects, 152 ok. Ten "
             ".ini ship as UTF-16LE and are refused.")

    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `6805`, at 0.95; 0.0 otherwise.

        Every format probe answers the same on 6805 as on 6609 (same compiled
        tables, same cipher, same archives), so a format-based bid would tie
        with the lineage. Before this plugin a 6805 root fell to
        `Patch6090`'s 0.45 stamped-sibling bid -- the `Patch6609` parent
        claims only `6609`, so it scores 0.0 here.
        """
        if _version(Path(root)) == "6805":
            return 0.95
        return 0.0

    def table_specs(self, root):
        """`SPECS_6609`, then 6609's censused `.ini` FILTERED TO DISK.

        `self.name` is `patch6805`, and `censused.py` has no entry under that
        name, so the inherited `Patch6609.table_specs` would silently drop
        the whole `.ini` census on this build. 6609's is the right donor:
        6805's compiled layer IS 6609's by fingerprint, and `patch6907`
        measured 6609 as the best donor one era later (139 of 139 present
        specs read with a control). The filter is presence on disk, so a
        6609 table this client does not ship is not rendered as a refusal
        about a table that never existed. A curated spec still wins on
        subject and filename. MEASURED on 6805: 139 of 6609's
        censused specs are on disk, plus 6090's `emotionico` (see
        `CENSUS_DONORS`); 165 subjects, 152 read with a control.
        """
        root = Path(root)
        out = list(SPECS_6609)
        taken = {s.subject for s in out}
        files = {s.filename.lower() for s in out}
        for donor in CENSUS_DONORS:
            for s in censused.specs_for(donor):
                if (s.subject in taken or s.filename.lower() in files
                        or not (root / "ini" / s.filename).is_file()):
                    continue
                out.append(s)
                taken.add(s.subject)
                files.add(s.filename.lower())
        return tuple(out)

    def colour_provenance(self):
        """Inherited from 6090's scan through 6609, and not checked here."""
        return ("colourway inherited from 6090 via 6609 (unverified here)",
                "inferred")

    def table_quirks(self):
        """6609's quirks (and 6090's under them), plus 6805's own."""
        q = super().table_quirks()
        q["itemtype.dat is 65 fields wide"] = (
            "65 fields against 6609's 66 -- 66 with index 11 dropped "
            "(core/tqdat.py AT_WIDTH_NO_WEIGHT, measured on 6680..6868). "
            "tqdat picks names by each file's measured width; a caller that "
            "reuses 6609's column indexes past 11 reads the wrong column.")
        q["itemtype.dat is 6868's"] = (
            "Byte-identical to 6868's (fingerprints.json, 9,571,858 bytes), "
            "and the item count MEASURED equal to 6868's, 33,639 "
            "through the same reader.")
        q["last TQ-stream era"] = (
            "6772, 6805 and 6868 are core/block96.KEY_ERA_BEFORE: 0.0% of "
            "itemtype.dat's blocks are in the 7878 dictionary. A block96 "
            "reader pointed here returns markers, not rows; tqdat at seed "
            "9527 is the reader.")
        q["ten .ini are UTF-16LE"] = (
            "ActionLee3DEffect, chatSetup, ControlAction, ItemQuenchTipInfo, "
            "KongfuBourn, MsgBoxEx, Storage, Title, WrapTypeData and info.ini "
            "start FF FE on 6805 and parse to 0 sections with no control, so "
            "they are refused. 6609 ships the first nine as single-byte text "
            "and reads them. Measured 2026-09-19.")
        return q


PLUGIN = Patch6805()
