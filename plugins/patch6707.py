#!/usr/bin/env python3
r"""
patch6707 -- the official Conquer Online patch 6707 client.

**A thin `Patch6609` subclass, because 6707 is 6609's art with 6680's item
table and its own monster/magic/mount content, all still in the TQ-cipher
era.** Written 2026-09-19 (wave C) with the machine closed to install reads:
every number below says where it came from, and everything that needs an
install read is in `measure_6707.py` (worktree root, not committed) until the
Director runs it.

WHY THIS PARENT
---------------
Before this plugin, `plugins.detect` on a 6707 root falls to `patch6090`'s
0.45 "stamped sibling" bid (INFERRED from the code: `Patch6609.confidence`
claims only `6609`, `Patch6907.confidence` bids only above `LAST_TQ_BUILD`
6868, and `Patch6090.confidence` returns 0.45 for any stamped client carrying
`3DSimpleObj.dbc`/`3DObj.dbc`). `patch6090` serves 6090's census and 6090's
`.dbc` list, and 6090 ships three compiled tables 6707 does not. The nearest
plugin whose declarations are TRUE of this client is `Patch6609`:

* **The art layer is 6609's, byte for byte.** MEASURED, from
  `docs/patchnotes/fingerprints.json` (blake2b over each file, the
  2026-09-18 corpus pass): all twelve `.dbc` (`3DEffect`, `3DEffectobj`,
  `3DObj`, `3DSimpleObj`, `3DTexture`, `3dmotion`, `armet`, `armor`, `mount`,
  `mountmotion`, `weapon`, `weaponmotion`), `3dobj.ini`, `3dtexture.ini`,
  `RolePart.ini`, `c3.wdf` (359,069,116 B) and `data.wdf` (392,245,257 B)
  hash identically on 6609 and 6707 -- and on 6652, 6680, 6772 and 6868.
* **The `.dat` tables are still the TQ stream cipher, not block96.**
  MEASURED by fingerprint: 6707's `ini/itemtype.dat` (8,841,840 B, blake2b
  `caf299cd`) is byte-identical to 6680's and 6772's. `plugins/patch6907.py`
  measured 6772's `itemtype.dat` at **0.0%** block96-dictionary coverage and
  records that 6868 and earlier open with `core/tqdat.py` at seed 9527; and
  `tests/test_plugin_catalogs.py:AT_WIDTHS` measured this file's `@@` width
  at **65** (parsed, therefore decrypted by tqdat). The same bytes cannot be
  a different cipher on a different install.
* **The two lowercase names 6609 introduced are 6707's too.** MEASURED by
  fingerprint: `ini/monster.dat` and `ini/magictype.dat` (6609 renamed them
  from 6090's `Monster.dat`/`MagicType.dat`).

`Patch6907` is wrong for the opposite reason (block96 declarations on a TQ
build), which is why `DAT_CENSUS_ERA` is False here.

WHAT DIFFERS FROM 6609, MEASURED
--------------------------------
From fingerprints (sizes are bytes, 6609 -> 6707):

    itemtype.dat   6,706,668 -> 8,841,840   == 6680 == 6772 (byte-identical)
    monster.dat      462,441 ->   521,549   own bytes (6680 513,188)
    magictype.dat    302,581 ->   385,464   own bytes (6680 372,722)
    mounttype.dat    426,325 ->   428,690   own bytes (6680 426,372)
    GameMap.dat       11,305 ->    12,011   own bytes
    Server.dat         4,096 ->     4,096   own bytes (per-build, as always)

* `itemtype.dat` is **65 fields wide** where 6609's is 66 (MEASURED,
  `test_plugin_catalogs.AT_WIDTHS`): 66 with index 11 (`weight`) dropped.
  `tqdat` selects `FIELDS_AT_NO_WEIGHT` by measured width, per file, so the
  inherited `item` spec is right without an override -- but anything that
  names item columns by a fixed index is off by one from column 11 here.
* `Action3DEffect.ini` carries `[section]` headers here (MEASURED:
  `test_plugin_catalogs` lists 6707 in `SECTIONED`), as on 6609 and unlike
  6090. `table_specs` re-measures it per install through
  `patch6090.with_action3deffect_shape`, which `Patch6609.table_specs` skips.
* `.pux` terrain files: 24 here against 6609's 20; `map/map/*.7z` map
  archives 312 against 297 (MEASURED 2026-09-07, `core/dmap.py` per-install
  tallies from `scratchpad/cheapclaims.py`).
* `Conquer.exe` at the root is the same 41-byte stub (fingerprint), and the
  real clients are `Env_DX8/Conquer.exe` and `Env_DX9/Conquer.exe`, two
  DIFFERENT builds (MEASURED 2026-08-29, `capture/coprofile/builds.json`).
  `ENV_TREES` is inherited and true here.

MEASURED ON THE INSTALL (measure_6707.py, 2026-09-19, 5 FAIL / 25 PASS)
----------------------------------------------------------------------
Every instrument ran on 6609 first as the positive control, and every
control PASSED: detect(6609) -> patch6609 at 0.95; 6609 archives c3.wdf +
data.wdf; 6609's own census 141/141; 6609 item 50000 -> SpeedArrow, price
100, attackMax 110, attackMin 10; 6609 `catalogs()` item 24,270 rows.

* Detection: 6707 -> patch6707 at 0.95; WITHOUT this plugin -> patch6090.
  No other install (every Clients folder, plus Classic Conquer 2.0) changed
  its winner or margin, and patch6707 scores 0 on all of them.
* 6707 archives: c3.wdf, data.wdf. All 17 art files (12 `.dbc`, 3 `.ini`,
  both `.wdf`) sha256-identical to 6609's; `part_tables()` 9 slots, equal to
  6609's. 6707 ships 12 `.dbc`; ABSENT_HERE all absent; root Conquer.exe
  41 B, Env_DX8 and Env_DX9 both present.
* itemtype.dat sha256 == 6680's (58784821002a). tqdat parses item 50000 to
  SpeedArrow / 100 / 110 / 10 on 6707, as on 6609.
* `ini/**.dat` families on 6707: tq-stream 52, rsa-mysqldump 10,
  rar-mangled 11, binary-plain 6, plaintext 6, empty 3, unknown 1,
  shift-obfuscated 1 -- **block96 0**, so `DAT_CENSUS_ERA = False`. All 18
  declared `.dat` exist and classify as their declared codec (6609: 18/18).
* `catalogs()` on 6707 under patch6707 (6609 under patch6609 in brackets):
  item 31,371 (24,270) · monster 1,543 (1,299) · magic 1,891 (1,551) ·
  mount 1,773 (1,764) · item:sub 1,238 (7,402) · map:dest 401 (371) ·
  magic:op 810 (800) · item:refine 160 (92) · gamemap 379 (358) · region
  276 (258) · action3deffect 4 (4) · flashlogin 18 (18). First draft: 164
  subjects, 162 ok (the two refusals are `action` and `levexp`, 6090's own
  refusals); patch6090 on 6707: 143 subjects, 140 ok.
* `TEXT_ENCODING` stays latin1: 81 of 197 `ini/*.ini` carry non-ASCII
  bytes, but no GBK proof was taken (no strict decode, no reject probe, no
  7205 control), and builds before 7205 keep latin1 unless proven.

KNOWN LIMITS -- the five FAILs, recorded, not tuned away
-------------------------------------------------------
1. **REGRESSION, FIXED: three subjects patch6090 read that the first draft
   did not** -- emotionico (108 rows under patch6090, ok), goldenleagueshop
   (51 parsed rows, NOT ok), info (241, ok). 6609's census lacks all three,
   and on 6707 `ini_census` calls all three `partial`. Now:
   * emotionico and info are declared from 6090's census
     (`CENSUS_FROM_6090`): their residue is a third field on 10 of 108 rows
     and 5 comment lines of 2,533 respectively, so the rows read correctly.
   * goldenleagueshop is declared and REFUSED (`GOLDENLEAGUESHOP_REFUSAL`):
     6707's file mixes section headers with 5-field and 2-field rows under
     the same ids (best grammar 43.1% of lines). patch6090 did not pass it
     either: it LISTED 51 parsed rows with `ok` False ("no control could be
     checked back against the bytes"). So nothing that was read is lost; it
     stays listed, now with the reason.
2. **The borrowed census is 140/141 on 6707.** `flashLogin.ini` is declared
   ini-sections (6609's census) and the 6707 census verdict is `partial`;
   it still reads 18 rows, as on 6609. Four files are settled on 6707 and
   declared by nothing: `ar_res.ini` (flat-keys), `mount.ini` (sections --
   also undeclared on 6609), `runedesc.ini` (sections), `weapontrans.ini`
   (flat-keys). Declaring them needs the census regenerated for 6707.
3. and 4. **eventtypename "name column 2" FAILED on 6609 AND 6707.** The
   FAIL is the INSTRUMENT's: measure_6707.py split rows on whitespace, and
   `EventTypeName.ini` is comma-separated (`1,01,Pheasant`), so it saw one
   field per row. Column 2 of the comma split is the name on the bytes. Kept
   as recorded, inherited from 6609, and re-checked by the test with a comma
   split.
5. **Two subjects read >3x 6609's rows**: globallotterycondition 22 rows
   (6609: 1) and weaponshow 103 (6609: 22; patch6090 on 6707 also 103).
   Content growth or a grammar overcount -- not established. Recorded as
   limits; the test pins them so a change is noticed.

Also recorded, not a FAIL: item:sub falls 7,402 (6609) -> 1,238 (6707),
the same 1,238 under patch6090. Not explained here.

Still INFERRED: monster colourways (from the 6090 scan; `colour_provenance`
says so).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                      # noqa: E402
from plugins.catalog import TableSpec, KIND_SPACE_ROWS     # noqa: E402
from plugins.patch6090 import with_action3deffect_shape    # noqa: E402
from plugins.patch6609 import Patch6609, SPECS_6609       # noqa: E402

STAMP = "6707"

#: Whose `.ini` census this plugin borrows. Named, so the borrowing is a
#: grep-able fact and not a side effect of `self.name` -- see the docstring.
CENSUS_FROM = "patch6609"

#: Subjects 6609's census does not hold but 6090's does, and which
#: `patch6090` -- the winner on 6707 before this plugin -- READS on 6707.
#: MEASURED (measure_6707.py, 2026-09-19): `patch6090.catalogs(Clients/6707)`
#: returned emotionico 108 rows and info 241 (both ok, raw control) and
#: goldenleagueshop 51 parsed rows NOT ok (no control), and the first
#: draft of this plugin listed none of the three. A new plugin must not read
#: less than the plugin it replaces, so two are declared from 6090's census
#: (the same `TableSpec`s patch6090 serves today) and the third is declared
#: with a measured refusal -- see `GOLDENLEAGUESHOP_REFUSAL`.
#:
#: MEASURED on 6707 with `ini_census.classify_text` (verdict / best rate):
#:   EmotionIco.ini  partial; space-rows 98/108 lines. The 10 residual lines
#:                   are the SAME row with a third field (`98 Smile 1`), so
#:                   every row reads; the third field is extra, not lost.
#:   info.ini        partial; sections 2,528/2,533. The 5 residual lines are
#:                   non-latin1 comment text, not records.
CENSUS_FROM_6090 = ("emotionico", "info")

GOLDENLEAGUESHOP_REFUSAL = (
    "GoldenLeagueShop.ini is NOT the file 6090 censused. 6090's is 10 "
    "space-separated rows (809 B); 6707's is 2,501 B, 51 lines, and mixes "
    "grammars: `[Normal]`-style section headers, rows of five fields "
    "(`3306562 1200 item other new`) and rows of two where the second is a "
    "description (`3306562 Open~to~select~a~red~Rune~(B).`) -- the same id "
    "appears in both shapes. ini_census: partial, best grammar space-rows at "
    "43.1% of lines, sections at 11.8%. MEASURED 2026-09-19. patch6090 "
    "parsed 51 'rows' here as space-rows and did not pass them (no control "
    "checked back against the bytes); that is a line count, not a table. "
    "Declared and refused so the subject stays visible.")


def _version(root: Path) -> str:
    """`version.dat`, stripped. 6707's is the 4 bytes `6707` (fingerprint);
    `strip` also accepts a `\\r\\n` spelling, as later builds use."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch6707(Patch6609):
    """Official patch 6707: 6609's art, 6680's itemtype, TQ-cipher `.dat`."""

    name = "patch6707"
    label = "Official patch client 6707"
    origin = "official"
    aliases = ("6707",)
    STAMP = STAMP

    #: **False, deliberately and literally.** This client's `ini/*.dat` are
    #: the TQ stream cipher (itemtype.dat byte-identical to 6772's, which
    #: `patch6907` measured at 0.0% block96 coverage), so it is not in the
    #: block96 era and `tools/dat_census.py` must not census it. Set
    #: explicitly rather than inherited-by-absence, because
    #: `tests/test_dat_census.py` asks `is True` of whatever `detect` returns.
    DAT_CENSUS_ERA = False

    #: 6609's four, re-derived on 6707's bytes by measure_6707.py: region col
    #: 6, viptrans and restrain col 2 text on every row. eventtypename col 2
    #: FAILED in that run on 6609 and 6707 alike, because the instrument
    #: split on whitespace and the file is comma-separated (`1,01,Pheasant`);
    #: the test re-derives it with a comma split. No `goldenleagueshop`
    #: entry: that subject is refused here (`GOLDENLEAGUESHOP_REFUSAL`).
    ROW_COLUMNS = {
        "region": {"id": 0, "name": 6},
        "eventtypename": {"id": 1, "name": 2},
        "viptrans": {"id": 0, "name": 2},
        "restrain": {"id": 0, "name": 2},
    }

    notes = ("6609's art layer byte for byte (all 12 .dbc, c3.wdf, data.wdf "
             "-- fingerprint), 6680's itemtype.dat byte for byte (65 fields, "
             "weight dropped), and its own monster/magictype/mounttype "
             "content, all TQ-cipher at seed 9527 -- not block96 (measured: "
             "0 block96 .dat). The .ini census is 6609's plus three 6090 "
             "subjects; item 31,371 rows, monster 1,543, magic 1,891.")

    def confidence(self, root, exists) -> float:
        """0.95 on the exact stamp `6707`; 0.0 otherwise.

        Every format probe 6707 answers, 6609 and the rest of the 6090
        family answer identically (the `.dbc` files are the same files), so
        only `version.dat` can tell them apart. No sibling bid: an unstamped
        or foreign-stamped root is not this plugin's to claim.
        """
        return 0.95 if _version(root) == self.STAMP else 0.0

    def table_specs(self, root):
        """`SPECS_6609` + 6609's census, with `Action3DEffect.ini` measured.

        `censused.extend(SPECS_6609, self.name)` would look up `patch6707`,
        which the census does not hold, and return the curated specs alone:
        every censused `.ini` table would vanish from the listing. The
        borrowing is therefore explicit (`CENSUS_FROM`).
        """
        specs = censused.extend(SPECS_6609, CENSUS_FROM)
        taken = {x.subject for x in specs}
        files = {x.filename.lower() for x in specs}
        specs = specs + tuple(
            x for x in censused.specs_for("patch6090")
            if x.subject in CENSUS_FROM_6090 and x.subject not in taken
            and x.filename.lower() not in files)
        if "goldenleagueshop" not in taken:
            specs = specs + (TableSpec(
                "goldenleagueshop", "GoldenLeagueShop.ini", KIND_SPACE_ROWS,
                "plaintext", label_key=None,
                refusal=GOLDENLEAGUESHOP_REFUSAL),)
        return with_action3deffect_shape(specs, root)

    def table_quirks(self):
        """6609's quirks (and through it 6090's), plus 6707's own."""
        q = super().table_quirks()
        q["itemtype.dat is 6680's, 65 fields wide"] = (
            "Byte-identical to 6680's and 6772's (8,841,840 B; fingerprint "
            "blake2b caf299cd). 65 fields per @@ row where 6609's has 66: "
            "index 11 (weight) is dropped, so every column from 11 on sits "
            "one lower than 6609's. core/tqdat picks FIELDS_AT_NO_WEIGHT by "
            "measured width; a fixed-index reader does not.")
        q["TQ cipher, not block96"] = (
            "ini/*.dat open with core/tqdat at seed 9527 on this build. The "
            "block96 cipher starts at 6907 (plugins/patch6907.py); "
            "DAT_CENSUS_ERA is False and tools/dat_census.py does not "
            "census 6707.")
        q["ini census borrowed from 6609 (and three from 6090)"] = (
            "plugins/catalog/censused.py has no patch6707 entry, so the "
            "censused .ini specs are 6609's, plus emotionico and info from "
            "6090's (patch6090 read them on 6707) and goldenleagueshop "
            "declared and refused (mixed grammar here). MEASURED: 140/141 of 6609's agree with 6707's own files; "
            "flashLogin.ini is partial here; ar_res, mount, runedesc and "
            "weapontrans are settled and undeclared.")
        return q


PLUGIN = Patch6707()
