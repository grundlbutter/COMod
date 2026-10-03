#!/usr/bin/env python3
r"""
patch6868 -- the official Conquer Online patch 6868 client: **the LAST client
of the TQ-stream `.dat` era**, one patch before `patch6907`'s block96 cipher.

A thin `Patch6609` subclass. 6868's art layer is 6609's file for file, its
`.dat` tables are still the TQ stream cipher at seed 9527, and the only things
that move between the two are CONTENT and one table-list rule this file has to
restate because it is keyed on the plugin's NAME (see `table_specs`).

WHY `Patch6609` AND NOT ANOTHER PARENT
--------------------------------------
* **Not `Patch6907`.** 6907 is where `ini/*.dat` became block96. 6868 is on
  the OTHER side of that boundary, and the boundary is measured, not read off a
  number: `core/block96.py` records 38 of 6868's 797,654 `itemtype.dat` blocks
  in the 7878 dictionary (0.005%), every one of them traced to the poisoned
  `UserHelpInfo.ini.dat` pair, so **zero genuine entries** cross it, against
  779,199 on 6907. `Patch6907.LAST_TQ_BUILD = 6868` is the same fact from the
  other side. A block96 profile here would read nothing.
* **Not `Patch6090` directly.** 6090 ships 15 `.dbc` and capitalised
  `Monster.dat`/`MagicType.dat`; 6609 ships 12 `.dbc` and lowercase
  `monster.dat`/`magictype.dat` -- and so does 6868 (fingerprints, below).
  `Patch6609` is the nearest official plugin BELOW 6868 and its curated specs
  already describe this naming.

MEASURED -- from files that were read, not from inference
---------------------------------------------------------
Source A: `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` (blake2b of
each install's significant files, measured 2026-09-19 before the owner's
renames; `6868` was not renamed). Prefixes are b2, 8 hex:

    version.dat         4 B, stamp "6868"
    c3.wdf  / data.wdf  index 0a534373 / 2a85b65e -- identical to 6609, 6805
                        and 6907 (header + index, not the payload)
    all 12 .dbc         identical to 6609's (3DEffect 12a6c78b, 3DEffectobj
                        7f508460, 3DObj b91236bd, 3DSimpleObj b92fbcff,
                        3DTexture c4182f00, 3dmotion 7e04df81, armet 7ba8ed8b,
                        armor 658ee820, mount 61f406bb, mountmotion 5486e1c1,
                        weapon 8c3b9e4f, weaponmotion 7690c6b4)
    3dobj.ini, 3dtexture.ini, RolePart.ini   identical to 6609's
    Conquer.exe         41 B stub, identical to 6609's (fabd4f4b)
    itemtype.dat        20bc4903, 9,571,858 B -- IDENTICAL to 6805's, and
                        differs from 6609's (25e83327, 6,706,668 B)
    monster.dat         b9fbb046, 707,675 B   (6805: 597,794; 6609: 462,441)
    magictype.dat       c1181486, 572,191 B   (6805: 385,778; 6609: 302,581)
    mounttype.dat       ec808767, 430,773 B
    GameMap.dat         221a1c69, 13,599 B
    play.exe            5dafeb4e, 833,296 B -- identical to 6805's

    The fingerprint set lists `ini/EmotionIco.dbc`, `misc.dbc`, `miscmotion.dbc`
    for 6090 and NOT for 6868, and spells the two tables in lowercase as 6609
    does.

Source B: measurements already in this repo, by other seats:

    * TQ stream at seed 9527 opens `itemtype.dat` -- `coassets.load_items`
      returned 33,639 rows on 6868 (`core/coassets.py`, 2026-08-30), and
      `tests/test_patch7205.py` pins that number as its regression control.
    * `itemtype.dat` rows are 65 `@@` fields, not 6090's 66 (index 11 dropped;
      `core/tqdat.py` `FIELDS_AT_NO_WEIGHT`, measured over 6680..6868).
      `tqdat` chooses the name list by the width the FILE has, so this plugin
      overrides nothing for it -- but a reader that assumed 6090's names would
      put every column from 11 on one to the right, silently.
    * block96 dictionary: 0.0% of `itemtype.dat` (38/797,654, all poisoned
      residue; `core/block96.py`, `plugins/patch6907.py`).
    * `levexp.dat` byte-identical to 5517/6090/6609 (sha256 df41c576), so
      6090's refusal is true of these bytes (`plugins/patch6090.py`).
    * `Action3DEffect.ini` is 45,060 B with 4 section headers -- SECTIONS, not
      6090's flat keys (`plugins/patch6090.py` A3DE table).
    * `RolePart.ini` is stale against the compiled `ROPT`: `mix_body`,
      `mix_armet`, `armet_dx8`, `mix_armet_dx8` are ROPT-only here
      (`core/coassets.py`, 2026-09-06). Handled by the base merge, not here.
    * 7205's item columns agree with 6868's on 33,638 shared ids
      (`Patch7205.ITEM_COLUMN_AGREEMENT`, control_build "6868").

MEASURED ON THE INSTALL -- `measure_6868.py`, run by the Director 2026-09-19
----------------------------------------------------------------------------
(worktree script, not committed; output `measure_6868.out`, EXIT 0.)

    detection     46 installs ranked; ONLY 6868 changes its winner:
                  patch6868 0.95 (margin 0.50), without it patch6090 0.45.
                  patch6868 is 0.0 on every other install. Controls PASS:
                  6609 -> patch6609 0.95, 6907 -> patch6907 0.95.
    containers    Clients/6868: c3.wdf, data.wdf. Full-file sha256 identical
                  to 6609's (c3 ab68f57cc24ae100, data fc628e4adeb7de48).
    part_tables   9 slots, identical sources and row counts to 6609's.
    .dbc          12 files, all 12 byte-identical to 6609's.
    ABSENT_HERE   EmotionIco.dbc, misc.dbc, miscmotion.dbc absent -- holds.
    ENV_TREES     root Conquer.exe 41 B; Env_DX8 (17 files) and Env_DX9 (18)
                  each carry Conquer.exe -- holds.
    cipher        CONTROLS PASS: 6609 itemtype opens under TQ 9527, 6907's
                  does not. 6868 ini/*.dat families: tq-stream 61,
                  rsa-mysqldump 10, rar-mangled 11, plaintext 7,
                  binary-plain 6, unknown 3, empty 2, shift-obfuscated 1 --
                  NO block96. block96 CONTROL 6907 itemtype 100.000% of
                  901,698 blocks; on 6868 block96 REFUSES itemtype (classified
                  tq-stream). Hence DAT_CENSUS_ERA = False.
    itemtype      byte-identical to 6805's (sha256 5cd359c0); 33,639 rows,
                  ALL 65 `@@` fields; coassets.load_items = 33,639.
    levexp.dat    byte-identical to 6805's (sha256 df41c576).
    ROW_COLUMNS   region 296 rows, eventtypename 24, viptrans 31, restrain 5,
                  0 numeric labels each -- 6609's columns hold here.
    encoding      SlotNpc.ini 3,918 B, 102 high bytes; npc.ini 401,944 B, 16
                  high bytes; both decode as GBK. That is NOT the per-table
                  proof the owner's ruling requires (no strict name-field
                  decode, no CJK check, no rejecting probe, no 7205 control),
                  so TEXT_ENCODING stays latin1 (inherited) on this pre-7205
                  build.
    ini/          204 .ini, 101 .dat, 12 .dbc (6609: 193 .ini, 83 .dat).

    catalogs()    Clients/6868 under patch6868: 166 subjects, 163 ok (first
                  draft 163/161); patch6090 (the winner before): 143 / 139 ok;
                  Patch6609 on Clients/6609: 164 / 162 ok.
                    item 33,639 · monster 2,049 · magic 2,216 · mount 1,781 ·
                    gamemap 424 · region 296 · item:refine 187 ·
                    emotionico 108 · info 263
                  (6609: item 24,270 · monster 1,299 · magic 1,551 · mount
                  1,764 · gamemap 358 · region 258 · item:refine 92).
                  LOST vs patch6090, as a SET of ok subjects: NONE (the first
                  draft lost `emotionico` and `info` -- see CENSUS_DONORS).
                  24 subjects ok now that patch6090 did not read (item:refine
                  x5, globallotterycondition, mountinfo, signin, ...).

FINDINGS AND KNOWN LIMITS (FAILs in the measurement, recorded not tuned)
------------------------------------------------------------------------
* **Two tq-stream tables under 95% printable** on their first 4 KB at seed
  9527: `AutoUseMagic.dat` 91.1% and `StageGoal.dat` 89.3%. Classified
  tq-stream and neither is declared here; not investigated further.
* **`shlayout.dat` / `shlayout800x600.dat` still do not open**: 5,632 B each,
  37.6%/38.9% printable at 9527 and 38.2%/38.6% at 1234 -- 6609's quirk,
  unchanged.
* **`gameinfo`** is ok on 6609 (1 row) and is not declared on 6868, because
  `GameInfo.ini` is not in 6868's `ini/`. `patch6090` declared it on 6868 and
  REFUSED it; absence now reads as absence. Not an ok subject lost.
* **`goldenleagueshop`**: 51 rows, NOT ok -- no control could be checked --
  exactly as under `patch6090` before. Declared, not fixed.
* `levexp` and `action` REFUSED, as on 6609 and 6090 (their refusals apply:
  levexp is byte-identical to theirs).
* Rows > 3x 6609's, all with RAW controls (content growth, not noise):
  globallotterycondition 1 -> 64, npc:NpcX.ini 146 -> 470, weaponshow
  22 -> 109.
* **139 `ini/*.ini|*.dat` are undeclared** on 6868 (142 before the second
  donor): no census has been run on this build.

INFERRED -- not read off this install
-------------------------------------
* Monster colour sets inherited from the 6090 scan -- `colour_provenance`
  already says "unverified here" and that stays true.

WHAT THIS PLUGIN OVERRIDES, AND WHY
-----------------------------------
* `confidence` -- 0.95 on the exact stamp `6868`, else 0.0. Before this
  plugin, 6868 fell to `patch6090`'s 0.45 "stamped sibling" bid;
  `Patch6609.confidence` answers 0.0 for it and `Patch6907`'s sibling bid
  requires a stamp ABOVE 6868.
* `table_specs` -- **the one override that is a fix and not a label.**
  `Patch6609.table_specs` calls `censused.extend(SPECS_6609, self.name)`, and
  the census is keyed by plugin name. Inherited unchanged, `self.name` would be
  `"patch6868"`, which has no census, and every censused `.ini` subject (6609
  declares 141) would vanish with no error. So the census is borrowed
  EXPLICITLY from `CENSUS_DONORS` -- 6609 first, then 6090, whose census
  covers `EmotionIco.ini` and `info.ini` that 6868 ships and 6609 does not --
  keeping only specs whose file `_find_table` finds on this install, and `Action3DEffect.ini` is shaped from the file on disk
  (`patch6090.with_action3deffect_shape`), which is what `patch6090` did for
  6868 before this plugin existed.
* `DAT_CENSUS_ERA = False`, literally -- this is not a block96 client (above),
  so `tools/dat_census.py` must not cover it and its `BUILDS` slot for 6868
  stays commented.
* `table_quirks` -- 6609's, plus 6868's own.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                       # noqa: E402
from plugins.patch6090 import with_action3deffect_shape    # noqa: E402
from plugins.patch6609 import Patch6609, SPECS_6609        # noqa: E402


def _version(root: Path) -> str:
    """`version.dat`, stripped -- the file carries the patch number only."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch6868(Patch6609):
    name = "patch6868"
    label = "Official patch client 6868"
    origin = "official"
    aliases = ("6868",)

    STAMP = "6868"

    #: Whose `.ini` census this build borrows. 6868 has none of its own (the
    #: census is keyed by plugin name), and its compiled tables are 6609's
    #: files, so 6609 is the donor -- the same choice `patch6907` measured
    #: (139/139 read with a control on 6907). Whether it holds on 6868 is
    #: `measure_6868.py` phase 5.
    CENSUS_DONOR = "patch6609"

    #: Donors in priority order. **6090 is the SECOND donor because 6609's
    #: census alone LOST SUBJECTS on this install**, MEASURED by
    #: `measure_6868.py` (2026-09-19): `patch6090` -- the winner on 6868
    #: before this plugin -- read `emotionico` (108 rows) and `info` (263)
    #: ok, and the 6609-only list did not declare them at all, because 6609
    #: does not ship `EmotionIco.ini`/`info.ini` and 6868 does. 6090's census
    #: also carries `goldenleagueshop` and `gameinfo`, which 6868 ships.
    #: 6609 wins where both declare a file.
    CENSUS_DONORS = ("patch6609", "patch6090")

    #: 6609's measured columns, re-measured on 6868 (`measure_6868.py`:
    #: region 296 rows, eventtypename 24, viptrans 31, restrain 5, ZERO
    #: numeric labels on each), plus 6090's `goldenleagueshop` column --
    #: the mapping `patch6090` served 6868 with before this plugin, kept so
    #: the subject reads exactly as it did.
    ROW_COLUMNS = dict(Patch6609.ROW_COLUMNS,
                       goldenleagueshop={"id": 0, "name": 2})

    #: NOT a block96 client: 6868 is the last TQ-stream build (38 of 797,654
    #: blocks in the dictionary, all poisoned residue). `tools/dat_census.py`
    #: asks this attribute with `is True`; literal False keeps 6868 out.
    DAT_CENSUS_ERA = False

    notes = ("The last TQ-stream client: ini/*.dat open with tqdat at seed "
             "9527 (itemtype 33,639 rows, 65 @@ fields), one patch before "
             "6907's block96 cipher. Art layer is 6609's -- all 12 .dbc and "
             "both .wdf indexes identical by fingerprint. itemtype.dat is "
             "byte-identical to 6805's. Borrows 6609's then 6090's .ini "
             "census, filtered to disk: 166 subjects, 163 ok on Clients/6868.")

    def confidence(self, root, exists) -> float:
        """0.95 on our own stamp, 0.0 otherwise -- the rule every official
        plugin in this lineage uses, because the format probes cannot tell
        6609, 6868 and 6907 apart (their compiled tables are the same
        files)."""
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    def table_specs(self, root):
        """6609's curated specs, then the censused `.ini` of each donor in
        `CENSUS_DONORS` order that this install actually ships, with
        `Action3DEffect.ini` shaped from the file.

        See the module docstring: inheriting `Patch6609.table_specs` would ask
        the census for `"patch6868"` and silently get nothing. A borrowed
        spec is kept when `_find_table` locates its file on THIS install --
        the same lookup `catalogs()` uses, so "declared" and "found" cannot
        disagree.
        """
        root = Path(root)
        here = tuple(SPECS_6609)
        taken = {s.subject for s in here}
        files = {s.filename.lower() for s in here}
        for donor in self.CENSUS_DONORS:
            for s in censused.specs_for(donor):
                if s.subject in taken or s.filename.lower() in files:
                    continue
                if self._find_table(s, root) is None:
                    continue
                here += (s,)
                taken.add(s.subject)
                files.add(s.filename.lower())
        return with_action3deffect_shape(here, root)

    def table_quirks(self):
        q = super().table_quirks()
        q["last TQ-stream client"] = (
            "ini/*.dat are the TQ stream cipher at seed 9527 -- core/tqdat.py "
            "reads them. 6907, the next official client, switched to the "
            "block96 cipher; 38 of 6868's 797,654 itemtype.dat blocks are in "
            "the 7878 dictionary and all 38 are poisoned residue from "
            "UserHelpInfo.ini.dat. Point block96 at this build and it reads "
            "nothing; point tqdat at 6907 and it returns noise that parses.")
        q["itemtype is 65 fields, not 6090's 66"] = (
            "The @@ rows drop index 11. tqdat picks FIELDS_AT_NO_WEIGHT by "
            "the width the file has; a reader using 6090's names shifts every "
            "column from 11 on by one and raises nothing.")
        q["census borrowed from 6609, then 6090"] = (
            "The .ini census is keyed by plugin name and 6868 has none of its "
            "own. 6609's is borrowed, then 6090's for files 6609 lacks "
            "(EmotionIco.ini, info.ini, GoldenLeagueShop.ini), filtered to "
            "files on disk. 139 ini/*.ini|*.dat stay undeclared until the "
            "census is run on this build.")
        q["two tq-stream tables read below 95% printable"] = (
            "AutoUseMagic.dat 91.1% and StageGoal.dat 89.3% on their first "
            "4 KB at seed 9527 (measure_6868.py). Neither is declared.")
        return q


PLUGIN = Patch6868()
