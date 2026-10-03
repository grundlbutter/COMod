#!/usr/bin/env python3
r"""
patch6772 -- the official Conquer Online patch 6772 client.

**A thin `Patch6609` subclass: 6609's art, 6609's cipher, 6772's content.**
6772 sits inside the TQ-cipher era (B1, 5017..6868) and on the 6609 art
layer, three official patches before 6907 changes the `ini/*.dat` cipher.
Nothing here switches a 6609 answer off. What it changes is WHICH tables are
declared, because inheriting `Patch6609.table_specs` under a new plugin name
silently drops every censused `.ini` table (see `table_specs`).

WHY `Patch6609` AND NOT `Patch6090` OR `Patch6907`
--------------------------------------------------
Decided from code and from `co-pnd/docs/patchnotes/fingerprints.json` (every
install's significant files, blake2b prefix and size; measured 2026-09-18,
before 7632 was renamed 7622 -- which does not touch this build):

* **The art layer is 6609's, file for file.** All twelve `ini/*.dbc` carry
  the SAME blake2b and size on 6609 and 6772 (and on 6652..6907): 3DEffect
  12a6c7, 3DEffectobj 7f5084, 3DObj b91236, 3DSimpleObj b92fbc, 3DTexture
  c4182f, 3dmotion 7e04df, armet 7ba8ed, armor 658ee8, mount 61f406,
  mountmotion 5486e1, weapon 8c3b9e, weaponmotion 7690c6. So are
  `ini/3dobj.ini` (a37b35), `ini/3dtexture.ini` (1e639c) and
  `ini/RolePart.ini` (5ea9a8, 721 B -- it changes at 6907, not before).
* **The archives' header + index are 6609's**: `c3.wdf` 0a5343 at
  359,069,116 B and `data.wdf` 2a85b6 at 392,245,257 B. The fingerprint covers
  the header and index, NOT the payload; `docs/capability_matrix_2026-09-06.md`
  verified the full payload sha256 on 5017 / 6609 / 7205 only, so payload
  identity HERE is INFERRED (from equal size + equal index between two
  verified endpoints), and `measure_6772.py --wdf-hash` settles it.
* **The content cipher is still TQ at seed 9527, so `Patch6907` is wrong.**
  MEASURED 2026-08-30 (`core/block96.py`, `plugins/patch6907.py`): the 7878
  block dictionary knows **29 of 6772's 736,820** `itemtype.dat` blocks
  (0.0%), against 86.4% on 6907, and every such stray traces to a poisoned
  pair. `docs/capability_matrix_2026-09-06.md` §2.2 puts 6772 in era B1 and
  counts **55 of 93** `ini/*.dat` whose first 4 KiB decrypt to text at 9527.
  `Patch6907.confidence` already bids 0.0 here (6772 <= `LAST_TQ_BUILD`
  6868), and its `load_table` refuses every `tq-stream` spec by design.
* **`Patch6609` over `Patch6090`** because 6609 is the nearest official
  plugin below, and it shares 6772's shape where 6090's differs: the root
  `Conquer.exe` is the 41-byte stub (fabd4f) on both, with the real binaries
  under `Env_DX8/` / `Env_DX9/`; `monster.dat` and `magictype.dat` are the
  lowercase names on both; `play.exe` is byte-identical (4f9e4c,
  1,485,296 B -- identical 6609..6707, changes at 6805).

WHAT DIFFERS FROM 6609 (MEASURED, fingerprints.json)
----------------------------------------------------
    file                 6609                   6772
    ini/itemtype.dat     25e833  6,706,668 B    caf299  8,841,840 B
    ini/monster.dat      9bf10a    462,441 B    9da667    527,059 B
    ini/magictype.dat    7fcf8b    302,581 B    69c1da    385,777 B
    ini/mounttype.dat    bb394b    426,325 B    08c19a    429,513 B
    ini/GameMap.dat      b0fb3c     11,305 B    de2b53     12,409 B
    Server.dat           db3495      4,096 B    8a981d      4,096 B
    version.dat          "6609"                 "6772"

* **`itemtype.dat` is byte-identical to 6680's and 6707's** (caf299,
  8,841,840 B on all three; `core/tqdat.py` records the md5 identity with
  6680's, b64c7201). So 6772 shipped no new item table.
* **Its rows are 65 fields wide, not 6609's 66.** MEASURED by
  `core/tqdat.py` (`AT_WIDTH_NO_WEIGHT`, a measurement of 6680, 6707, 6772,
  6805, 6868): index 11 is dropped. `tqdat` selects the names by measured
  width, so `coassets.load_items` is right here; a caller that applies
  `FIELDS_AT` positionally to this file is off by one from column 11 on.
  The catalog's `ITEM_COLUMNS` (id 0, name 1) sits before the drop and is
  unaffected.
* **`Action3DEffect.ini`**: `docs/comod_backlog.md` calls it a declared-kind
  defect on 6652..6868 (`patch6090` declares it `flat-keys`, the file has
  `[wing]` headers). **REFUTED on 6772 as a reading failure by
  `measure_6772.py`**: `patch6090` and this plugin (6609's census, sections)
  both read it at 4 rows with a `raw` control -- `patch6090` already sniffs
  the file's shape (`with_action3deffect_shape`), so no fix is needed here.
* Recorded by other seats, not re-measured here: 29 `.pux` (6609 20),
  320 `.DMap` references (`core/dmap.py`), `c3.wdb` 26 sections MESX
  (6609 25), and the first appearance of the `TqPackage9` DLL
  (capability_matrix "from 6772").

DETECTION
---------
Before this plugin, `plugins.detect(Clients/6772)` returns **`patch6090` at
0.45** -- predicted from the code and then MEASURED (below): `Patch6090.confidence` bids 0.45
for any stamped client carrying `3DSimpleObj.dbc` + `3DObj.dbc`,
`Patch6609`'s claim is stamp-exact, and `Patch6907`'s sibling bid needs a
stamp above 6868. `confidence` here claims the exact stamp `6772` at 0.95 and
nothing else, so 6772 is the only install whose winner changes.
`measure_6772.py` showed that over every install on the box, with 6609 as
the positive control.

`DAT_CENSUS_ERA = False`, declared: this client's `.dat` are TQ-stream, not
block96, and `tools/dat_census.py` is a block96 census. Its slot stays
commented.

MEASURED 2026-09-19 (`measure_6772.py`, run by the Director, EXIT 0)
--------------------------------------------------------------------
* **Detection**, 46 installs: control 6609 -> `patch6609` 0.95 PASS. Only
  `6772` changes winner (`patch6090` 0.45 -> `patch6772` 0.95, margin 0.50);
  `patch6772` scores 0.0 on every other install.
* **Containers** 6772: `c3.wdf`, `data.wdf` (same as 6609). Root
  `Conquer.exe` 41 B; `Env_DX8/` and `Env_DX9/` both carry `Conquer.exe`.
* **`part_tables`** 6772 vs 6609: 9 slots, none differing.
* **`.dbc`** 6772: 12 files, all 12 sha256-identical to 6609's.
  `ABSENT_HERE` holds: EmotionIco/misc/miscmotion `.dbc` absent on 6772,
  present on 6090.
* **Codec census**, control first -- 6609: 83 `.dat`, declared == classified
  on every declared table, `itemtype` widths {66: 24,269, 55: 1} -> PASS.
  6772: **93 `.dat`**: tq-stream 55, rsa-mysqldump 10, rar-mangled 11,
  binary-plain 6, plaintext 6, empty 3, shift-obfuscated 1, unknown 1;
  **block96 0**; declared != classified: none. TQ text at 9527 on 55 (the
  capability_matrix's 55 of 93, reproduced). `itemtype` width {65: 31,371}.
* **Identity** (sha256[:12]): `itemtype.dat` 6772 = 6680 = 58784821002a
  (6090 = 6609 = 5a4d6f357659); `levexp.dat` 6090 = 6609 = 6680 = 6772 =
  df41c576aa47, so the inherited `levexp` refusal is about these very bytes;
  `shlayout.dat` 6772 = 6680 = 2d01014bf0b1 (6609 99d267db9ce6), 37.9%
  printable at 9527 -- the 6609 quirk holds on different bytes.
* **catalogs()**: 6772 162 subjects / 151 ok; 6609 on 6609 164 / 162;
  `patch6090` on 6772 143 / 131. Headline rows on 6772 (6609 on 6609):
  item 31,371 (24,270) · monster 1,558 (1,299) · magic 1,892 (1,551) ·
  item:sub 7,841 (7,402) · mount 1,776 (1,764) · map:dest 483 (371) ·
  magic:op 810 (800) · action3deffect 4 (4) · item:refine 160 (92).
  21 subjects read here and NOT under `patch6090` (all five `item:refine*`,
  codepage, dxinfo, emotionpackage, fontsetting, globallotterycondition,
  ignorenewitem, mountinfo, newserveraward, noviceguide, pushinfo,
  raiderconfig, raiderholdem, signin, startfail, turnoverlottery,
  weaponmatch).
* **REGRESSION FOUND AND FIXED: `emotionico`.** `patch6090` reads
  `EmotionIco.ini` on 6772 (108 rows); 6609's census does not declare it, so
  the first head (90789b23) lost it. `table_specs` now falls back to 6090's
  census for subjects 6609's does not cover (`FALLBACK_DONOR`).
* **Census donors** on 6772 (197 `.ini` in `ini/`): patch6090 declared 125,
  present 123, read with a control 114; **patch6609 141 / 139 / 130**;
  patch7878 154 / 136 / 122. 6609 wins, as it did on 6907.
* **KNOWN LIMIT -- 9 borrowed 6609 specs do not read on 6772** (each also
  fails under `patch6090` where it declares them, so nothing is lost against
  the previous winner): actionlee3deffect, chatsetup, controlaction,
  itemquenchtipinfo, msgboxex, storage, title, wraptypedata (0 sections
  parsed, no control) and kongfubourn (29 rows, no control). The grammar
  6609's census settled does not hold on these 6772 files. `gameinfo` and
  `gamesetup` (ok on 6609) are not shipped on 6772. `action` and `levexp`
  are refused on 6609 exactly as on 6772.
* **Noise-shape flag, checked and not noise**: globallotterycondition 22 rows
  (6609 1) and weaponshow 103 (6609 22) exceed 3x the predecessor, but both
  carry a `raw` control, and weaponshow reads 103 under `patch6090` too.
* **`ROW_COLUMNS`** on 6772: region 276 rows, eventtypename 24, viptrans 31,
  restrain 5 -- 0 numeric and 0 blank labels on each (`TwinCity`,
  `Pheasant`, ...). 6609's columns hold.
* **`TEXT_ENCODING` stays latin1 (inherited).** 92 of 6772's `ini/*.ini`
  carry bytes >= 0x80 and 64 of those decode as strict GBK, but that is not
  the owner's per-table proof (no name-field decode, no CJK check, no
  rejection probe, no 7205 control), and builds before 7205 keep latin1
  unless proven. `SlotNpc.ini` is present on 6772.

STILL NOT MEASURED
------------------
* archive PAYLOAD identity with 6609 (`--wdf-hash` was not run);
* monster colour sets -- inherited from the 6090 scan via 6609, and never
  looked at on this client (`colour_provenance` says "inferred").
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                     # noqa: E402
from plugins.patch6609 import Patch6609, SPECS_6609, _version  # noqa: E402

#: The build this plugin is for, as `version.dat` stamps it.
STAMP = "6772"

#: Whose `.ini` census this build borrows. `censused.CENSUSED` has no
#: `patch6772` entry (`tools/gen_ini_specs.BUILDS` is eight builds, and this
#: is not one), so the plugin name cannot be the key. 6609 is the donor for
#: the reason `Patch6907.CENSUS_DONOR` gives -- measured there 139/139 read
#: with a control on 6907 -- and because 6772 sits between the two.
#: `measure_6772.py` repeats that donor measurement on THIS build.
CENSUS_DONOR = "patch6609"

#: Second donor, for subjects the first does not declare at all. MEASURED
#: need: `patch6090` read `emotionico` (108 rows) on 6772 and 6609's census
#: has no such subject, so a 6609-only borrow read LESS than the plugin it
#: replaced. Only NEW subjects on files present on disk come from here.
FALLBACK_DONOR = "patch6090"


class Patch6772(Patch6609):
    name = "patch6772"
    label = "Official patch client 6772"
    origin = "official"
    aliases = ("6772",)

    #: TQ-stream `.dat` (era B1), not block96: 29 of 736,820 `itemtype.dat`
    #: blocks in the 7878 dictionary, all from a poisoned pair. The literal
    #: `False`, because `tests/test_dat_census.py` asks `is True`.
    DAT_CENSUS_ERA = False

    #: Instance-overridable so `measure_6772.py` can re-run the donor
    #: comparison without editing this file.
    CENSUS_DONOR = CENSUS_DONOR
    FALLBACK_DONOR = FALLBACK_DONOR

    notes = ("6609's parse family -- all twelve .dbc and both .wdf indexes "
             "byte-identical to 6609's, TQ-cipher .dat at seed 9527 (not "
             "6907's block96) -- with new monster, magictype, mounttype and "
             "GameMap tables. itemtype.dat is byte-identical to 6680's and "
             "6707's and is 65 fields wide (index 11 dropped), not 6609's 66. "
             "The .ini census is borrowed from 6609 and filtered to the files "
             "on disk. Monster colour data is inherited from the 6090 scan "
             "and has NOT been verified here.")

    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `6772`, at 0.95, and nothing else.

        6772 answers yes to every format probe 6609 and 6090 do -- its
        compiled tables are literally theirs -- so the stamp is the only
        discriminator. `Patch6090` bids 0.45 on a stamped sibling, which is
        what read 6772 before this plugin; 0.95 wins cleanly without weakening
        that bid anywhere else.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    def table_specs(self, root):
        """6609's curated specs, then 6609's censused `.ini` FILTERED TO DISK.

        **Inheriting `Patch6609.table_specs` would be wrong in a way nothing
        reports**: it calls `censused.extend(SPECS_6609, self.name)`, and
        `censused.CENSUSED` has no `"patch6772"` key, so every censused `.ini`
        subject would vanish and `catalogs()` could not even refuse them. So
        the donor is named (`CENSUS_DONOR`) instead of derived from the name.

        **Filtered to presence on disk**, as `Patch6907.table_specs` does:
        "declared and absent" and "declared and unreadable" are different
        findings, and only the second is interesting. The curated specs are
        NOT filtered -- a curated table missing here should read as a
        refusal, because someone decided it matters.

        Then `FALLBACK_DONOR` (6090) for subjects neither list declares:
        without it `emotionico`, which `patch6090` reads on 6772, was lost.
        """
        ini = Path(root) / "ini"
        have = set()
        if ini.is_dir():
            have = {p.name.lower() for p in ini.iterdir() if p.is_file()}
        out = tuple(SPECS_6609)
        for donor in (self.CENSUS_DONOR, self.FALLBACK_DONOR):
            if not donor:
                continue
            taken = {s.subject for s in out}
            files = {s.filename.lower() for s in out}
            out += tuple(
                s for s in censused.specs_for(donor)
                if s.filename.lower() in have
                and s.subject not in taken and s.filename.lower() not in files)
        return out

    def table_quirks(self):
        """6609's quirks, labelled as 6609's where 6772 has not been
        measured, plus 6772's own."""
        q = super().table_quirks()
        for k in ("three fewer compiled tables", "shlayout.dat does not open",
                  "mount.dbc shrank"):
            if k in q:
                q[k] = ("MEASURED ON 6609; on 6772 the twelve .dbc are "
                        "sha256-identical, the three named .dbc are absent, "
                        "and shlayout.dat reads 37.9% printable: " + q[k])
        q["itemtype.dat is 65 fields, and 6680's file"] = (
            "ini/itemtype.dat is byte-identical to 6680's and 6707's (blake2b "
            "caf299, 8,841,840 B) and its rows are 65 fields wide -- index 11 "
            "dropped against 6609's 66 (core/tqdat.AT_WIDTH_NO_WEIGHT). tqdat "
            "picks the names by width; FIELDS_AT applied positionally is off "
            "by one from column 11.")
        q["ini census is 6609's, borrowed"] = (
            "censused.py has no patch6772 entry. The .ini specs are 6609's "
            "census filtered to the files on disk here; tables 6772 ships "
            "that 6609 does not are invisible until gen_ini_specs covers it.")
        q["nine 6609 census tables do not read here"] = (
            "MEASURED on 6772: actionlee3deffect, chatsetup, controlaction, "
            "itemquenchtipinfo, msgboxex, storage, title, wraptypedata parse "
            "0 sections with no control, and kongfubourn 29 rows with no "
            "control. All read on 6609. Refused, not guessed.")
        return q


PLUGIN = Patch6772()
