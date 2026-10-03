#!/usr/bin/env python3
r"""
patch6271 -- official Conquer Online patch 6271: 6090's content family with
EVERY compiled table removed.

6271 is the client this project captured its wire traffic from
(`client/patches.P6271`, `tests/test_patch6271_profile.py`); this file is
its ASSET side, which had no plugin until now.

THE ONE FACT THAT DECIDES THIS FILE: 6271 SHIPS NO `.dbc`
---------------------------------------------------------
The lineage's compiled twins go 14 (5517) -> 15 (6090) -> **0 (6256, 6271)**
-> 12 (6609). So 6256 and 6271 are a two-build GAP in the compiled-table
era, not a point on it. Evidence, none of it read off the install by this
agent (the box was closed to installs while this was written):

* `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json`, install `6271`:
  `ini/3DObj.dbc` is recorded **absent** (it is present on 5517, 6090 and
  6609 in the same file). `tools/patchdict.py`'s draft `6256.md` records
  "compiled `.dbc` tables: 15 -> 0" for 6090 -> 6256, and its `6271.md`
  (vs 6256) records no `.dbc` change, i.e. still 0.
* `docs/comod_backlog.md` (2026-09-10, measured): "The 6256/6271/6716 group
  has no compiled tables at all"; `weapon.dbc` NONE on 6256/6271/6716; the
  mount table census puts 6256 and 6271 in the "no `mount.dbc`" row.

WHY `Patch6090` AND NOT `PlaintextFamily`, AND WHAT THAT COSTS
--------------------------------------------------------------
Both are half right, which is why the choice is written down.

* **The content layer is 6090's.** `ini/itemtype.dat` is byte-identical to
  6090's (fingerprints: 6,706,668 B, blake2b `25e83327aac2...` on 6090,
  6256, 6271 and 6609), i.e. the `@@` record form under TQ seed 9527 that
  `SPECS_6090` declares. `PlaintextFamily`'s quirks say itemtype is the
  SPACE-separated 5017-5165 form, which is false of these bytes. The WDF
  pair is 6090's too: `c3.wdf` 359,069,116 B / `data.wdf` 392,245,257 B,
  header+index blake2b identical to 6090's (fingerprints hash header+index,
  NOT the payload -- full identity is for `measure_6271.py`). 6271's `.dat`
  are pre-block96: `docs/client_6271_2026-08-27.md` measured 77 `ini/*.dat`,
  **0 block96**, 41 tq-stream.
* **The art-lookup layer is the plaintext family's.** With no twin, the
  plaintext `ini/*.ini` chain is the only table there is, so the two hooks
  of `Patch6090` that name `.dbc` files are overridden:
    - `table_profile()` -> `npcart.PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL`
      names `ini/3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc` and
      `3dmotion.dbc`; the one of them fingerprinted here is absent, and
      `npcart`'s own measurement shows what the official profile does on a
      twin-less client: 0/503 NPCs on 5017.
    - `prefers_compiled_tables()` -> False. There is no twin to prefer.
  This is the same pair of overrides `patch7336` makes for a `.tpd` client.

**THE PRE-PLUGIN WINNER, MEASURED: `plaintext` at 0.5.** The wave brief
said 6271 falls to `patch6090` at 0.45; it cannot, and does not.
`Patch6090.confidence` returns 0.0 unless BOTH `ini/3DSimpleObj.dbc` and
`ini/3DObj.dbc` exist, and `patch6907` gates on the compiled tables too.
`measure_6271.py` (2026-09-19, EXIT 0) on Clients/6271 without this plugin:
`plaintext` 0.50, `patch6090` 0.0, `patch6907` 0.0. `PlaintextFamily` would
serve the 5017-5165 quirks (three-wide action keys, space-separated
itemtype) to a 6090-content client, and it declares 1 catalog subject here
against this plugin's 143.

WHAT IS OVERRIDDEN, AND WHY
---------------------------
* `confidence()`: 0.95 on the exact `version.dat` stamp `6271`, else 0.0.
  Stamp from fingerprints: 4 bytes, `6271`.
* `table_profile()` / `prefers_compiled_tables()`: above.
* `table_specs()`: `SPECS_6090` with 6609's two lowercase filenames
  (`monster.dat`, `magictype.dat` -- the fingerprinted names here; the
  capitalised `Monster.dat` / `MagicType.dat` are "gone" at 6256 per its
  draft notes), plus 6090's `ini/` grammar census BORROWED by name
  (`CENSUS_DONOR`), because no census was ever run on 6271. The
  `Action3DEffect.ini` shape is still measured per install
  (`with_action3deffect_shape`), exactly as `Patch6090` does.
* `import_plan()`: drops "compiled .dbc tables read", which is false here.
* `table_quirks()`: removes the three 6090 quirks that are properties of
  the compiled twin ("stale ini decoys", "unpadded ids in compiled tables",
  "u32-wrapped motion ids") and adds 6271's own.
* `colour_provenance()`: inherited from the 6090 scan, unverified here.
* `DAT_CENSUS_ERA = False`: no block96 `.dat` on this client (measured
  2026-08-27, 0 of 77). The census slot in `tools/dat_census.py` stays
  commented.

MEASURED ELSEWHERE, CITED HERE (not by this agent)
--------------------------------------------------
* Effect names (`docs/comod_backlog.md`, 2026-09-10): 1,757 distinct
  `Action3DEffect` values, 92.1% resolve with the value-grammar parser;
  **137 resolve to nothing**, 136 of them because they exist only in the
  compiled `3DEffect.dbc` this client does not ship.
* Weapon/action split: 4,639 appearances, 48.6% ini-hit, and the compiled-
  twin fix (item 25) gains nothing here because there is no twin.
* `Clients/6716` (deleted by the owner 2026-09-19) was stamped 6271 and had
  the same fingerprint build digest (`ff0daed2a4010e34`) -- a copy, not a
  sibling patch.

MEASURED BY measure_6271.py (2026-09-19, EXIT 0, 18 s; every number is
Clients/6271's unless another install is named)
---------------------------------------------------------------------------
* **Controls first, all PASS:** Patch6090 on 6090 = 0.95; the plaintext NPC
  profile on 5165 finds 1,250/1,250 named paths (100.0%, live tables) and on
  6090 576/1,250 (46.1%, stale tables).
* **Detection, 47 installs** (every `Clients/` folder plus the live CCO
  install): the winner/margin changes on 6271 ONLY -- `patch6271` 0.95,
  margin 0.45 over `plaintext` 0.50. `patch6271` scores 0 on the other 46.
* **Containers:** `c3.wdf` + `data.wdf`, **full-payload blake2b IDENTICAL to
  6090's**, so `sharedArchives` holds by measurement. 0 `.dbc` in `ini/`
  (6090: 15, 6256: 0, 6609: 12). `version.dat` is `b'6271'` at the folder
  root; the old nested `Conquer Online 3.0/` is gone.
* **Byte identity (blake2b):** `itemtype.dat` = 6090's = 6256's = 6609's.
  `3dobj.ini`, `3dtexture.ini`, `3DSimpleObj.ini`, `3dmotion.ini` and
  `3DEffect.ini` are the SAME bytes on 5165, 6090, 6256, 6271 and 6609: the
  whole plaintext lookup chain is the frozen 5165 file set. `npc.ini` and
  `Action3DEffect.ini` differ on all five.
* **`part_tables()`**, all from plaintext `.ini` (no twin): armet 1,168,
  body 955, l/r_weapon 4,828, mount 1 (the 49-byte stub), head 0, misc 0.
  6090 reads 2,609 / 3,326 / 11,164 / 1,651 from its twins.
* **KNOWN LIMIT -- NPC resolution:** the plaintext profile plans 1,901 of
  2,497 npc rows and only **576 of 1,250 named paths (46.1%) exist** -- the
  SAME figure as the stale-table control on 6090. The official profile plans
  0 of 2,497 (its tables are absent). So plaintext is the only profile that
  resolves anything, and the frozen tables it reads are as stale here as on
  6090. Recorded in `NPC_COVERAGE`; not tuned.
* **`ini/*.dat`:** 78 files -- tq-stream 41, binary-plain 6, rar-mangled 11,
  plaintext 6, rsa-mysqldump 10, shift-obfuscated 1, empty 1, unknown 1
  (`levexp.dat`, refused as on 6090). **block96: 0.** Every `SPECS_6271`
  `.dat` is on disk with its declared codec; no mismatch.
* **Action3DEffect.ini:** 10,946 keys, 0 section headers (flat keys);
  shape (3,4,3,3) on 10,416, so the action field is FOUR wide as
  `FIELD_WIDTHS` inherits; 3,179 always-on (`9999`) rows, so
  `aura_convention() == "table"` holds. (6090: 10,267 keys, 10,113 at
  (3,4,3,3), 2,604 always-on.)
* **ROW_COLUMNS:** `region.ini` column 6 non-numeric on 245/245,
  `VipTrans.ini` 31/31, `restrain.ini` 5/5, `GoldenLeagueShop.ini` 17/17.
  `EventTypeName.ini` read 0 rows under the script's whitespace split (an
  instrument limit; `catalogs()` reads it as 24 rows, `Pheasant`).
* **Borrowed census:** 125 of 125 `patch6090` census filenames present.
* **`catalogs()`: 143 subjects, 140 ok** (6090: 143 / 141). Rows (6090 ->
  6271): item 24,270 -> 24,270 (byte-identical file), item:sub 271 ->
  3,094, magic 1,266 -> 1,341, monster 982 -> 1,184, mount 2,574 -> 2,886,
  map:dest 322 -> 345, magic:op 647 -> 647, npc.ini 2,251 -> 2,497,
  region 190 -> 245. Not ok: `action` and `levexp` (refused on 6090 too)
  and **`goldenleagueshop` -- FINDING: 40 rows parsed, no control could be
  checked back against the bytes** (ok on 6090 at 10 rows). A known limit.
* **No subject lost (set check, 2026-09-19):** ok-subjects of the measured
  pre-plugin winner `plaintext` on 6271 are NONE (it declares
  `(no tables)`); `Patch6090().catalogs(Clients/6271)` has 140 ok; both
  minus this plugin's ok set are EMPTY.
* **Over 3x 6090's rows, checked against the noise shape:** item:sub (3,094
  vs 271) tracks the file, which grew 84,265 -> 971,840 B (11.5x, stat), and
  its control row is real text (`CombatSuit(Lv.15)`). The others
  (action3deffect3/4/5, npcex, goldenleagueshop) are plaintext `.ini` with
  no cipher that could make noise. Content growth, not a wrong cipher.
* **Encoding:** `npc.ini` (82 high bytes), `SlotNpc.ini` (102) and
  `terrainnpc.ini` (254) decode under GBK without error. That is NOT the
  positive proof the owner's rule requires (no rejection probe, no CJK
  count, no 7205 control), so `TEXT_ENCODING` stays latin1 (inherited) for
  this pre-7205 build, and the high bytes are recorded as open.

STILL INFERRED: the magic name column 3, `texture_for_mesh`'s NPC rule,
sockets and `socket_correction` (6090's, inherited; the archives are
payload-identical, the loose layer was not compared).
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                  # noqa: E402
from plugins.patch6090 import (Patch6090, SPECS_6090,  # noqa: E402
                               with_action3deffect_shape)


def _version(root: Path) -> str:
    """`version.dat`'s stamp, whitespace and NULs stripped (a later build
    writes `7217\\r\\n`; 6271's is the four bytes `6271`)."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip().strip("\x00")
    except Exception:
        return ""


#: 6090's curated specs with the two tables renamed to the spelling on
#: disk here (fingerprints: `ini/monster.dat`, `ini/magictype.dat`). Same
#: rename 6609 makes; `_find_table` is case-insensitive either way, so this
#: is about the name a listing shows, not about finding the file.
SPECS_6271 = tuple(
    replace(s, filename=s.filename.lower())
    if s.filename.lower() in ("monster.dat", "magictype.dat") else s
    for s in SPECS_6090)

#: Whose `ini/` grammar census 6271 borrows. None was run on 6271; 6090 is
#: the parent and the nearest censused build below it. INFERRED -- the
#: measure script counts how many of the borrowed filenames exist here.
CENSUS_DONOR = "patch6090"


class Patch6271(Patch6090):
    name = "patch6271"
    label = "Official patch client 6271"
    origin = "official"
    aliases = ("6271",)

    STAMP = "6271"

    notes = (
        "6090's content family -- byte-identical itemtype.dat (TQ seed 9527, "
        "@@ records), same-size c3.wdf/data.wdf -- with NO compiled .dbc "
        "tables at all (15 at 6090, 0 at 6256 and 6271, 12 again at 6609). "
        "The plaintext ini lookup chain is therefore the only one and is "
        "read with the plaintext profile. No block96 .dat (0 of 77). 137 "
        "Action3DEffect values name effects that live only in the compiled "
        "3DEffect.dbc this client does not ship. Colour sets inherited from "
        "the 6090 scan, unverified here."
    )

    #: Not a block96 client: 0 of 77 `ini/*.dat` classify block96
    #: (`docs/client_6271_2026-08-27.md`). Literal False, as the census
    #: test requires.
    DAT_CENSUS_ERA = False

    #: The compiled tables 6090 ships and this client does not. All fifteen
    #: -- the 6256 draft notes list them as gone at 6090 -> 6256, and 6271's
    #: notes (vs 6256) record none returning. Named so "this client has no
    #: such table" is distinguishable from "we failed to read it".
    ABSENT_HERE = (
        "ini/3DEffect.dbc", "ini/3DEffectobj.dbc", "ini/3DObj.dbc",
        "ini/3DSimpleObj.dbc", "ini/3DTexture.dbc", "ini/3dmotion.dbc",
        "ini/EmotionIco.dbc", "ini/armet.dbc", "ini/armor.dbc",
        "ini/misc.dbc", "ini/miscmotion.dbc", "ini/mount.dbc",
        "ini/mountmotion.dbc", "ini/weapon.dbc", "ini/weaponmotion.dbc")

    #: MEASURED by measure_6271.py: npc rows, plans under the plaintext
    #: profile, and named paths present among the first 250 plans' paths.
    NPC_COVERAGE = {"rows": 2497, "plans": 1901, "paths_checked": 1250,
                    "paths_present": 576, "official_profile_plans": 0}

    #: MEASURED `AssetRoot.part_tables()` row counts, all from `.ini`.
    PART_TABLE_ROWS = {"armet": 1168, "body": 955, "l_weapon": 4828,
                       "r_weapon": 4828, "mount": 1, "head": 0, "misc": 0}

    #: The quirks `Patch6090` records that are properties of the compiled
    #: twin, and so describe nothing on a client without one.
    TWIN_ONLY_QUIRKS = ("stale ini decoys", "unpadded ids in compiled tables",
                        "u32-wrapped motion ids")

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `6271`, at 0.95; 0.0 otherwise.

        No format probe is used: the one that would discriminate (no `.dbc`
        beside a 6090-content `itemtype.dat`) is shared with 6256, so only
        the stamp separates the two. Unlike `Patch6090.confidence` this does
        NOT require compiled tables -- requiring them is exactly why the
        parent scores 0.0 here."""
        return 0.95 if _version(Path(root)) == self.STAMP else 0.0

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        return with_action3deffect_shape(
            censused.extend(SPECS_6271, CENSUS_DONOR), root)

    def table_profile(self):
        """PLAINTEXT, because the official profile's four tables are `.dbc`
        files this client does not ship (see the module docstring)."""
        import npcart
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin here to prefer."""
        return False

    def import_plan(self, root, exists) -> dict:
        plan = super().import_plan(root, exists)
        plan["note"] = ("patch 6271: WDF pair shared with 6090 (full payload "
                        "blake2b identical, measured), loose layer imported, "
                        "NO compiled .dbc tables -- plaintext ini tables read")
        return plan

    def table_quirks(self):
        q = {k: v for k, v in super().table_quirks().items()
             if k not in self.TWIN_ONLY_QUIRKS}
        q["no compiled tables at all"] = (
            "0 .dbc in ini/, where 6090 ships 15 and 6609 ships 12. 6256 and "
            "6271 are a gap in the compiled era, not a point on it: the "
            "plaintext ini files are the only lookup tables, so the plaintext "
            "profile is used and nothing here is a stale decoy of anything.")
        q["the plaintext lookup tables are the frozen 5165-era files"] = (
            "3dobj.ini, 3dtexture.ini, 3DSimpleObj.ini, 3dmotion.ini and "
            "3DEffect.ini are byte-identical on 5165, 6090, 6256, 6271 and "
            "6609 (blake2b, measured) -- the files that stopped being "
            "maintained when the twin appeared at 5517. They are read because "
            "they are all there is, and they are stale here: see the 46.1% "
            "npc quirk.")
        q["effects that live only in the missing twin"] = (
            "MEASURED 2026-09-10 (docs/comod_backlog.md): 137 of 1,757 "
            "distinct Action3DEffect values resolve to nothing, 136 of them "
            "because they exist only in a compiled 3DEffect.dbc, which this "
            "client does not ship. 22 are on disk and merely unindexed. The "
            "rule table was updated; the effect table shipped with it was "
            "not.")
        q["the figures in the inherited quirks are 6090's"] = (
            "Every number in the quirks kept from Patch6090 was measured on "
            "Clients/6090. The WDF archives match 6090's by size and index, "
            "so the art-level quirks (socket tracks, weapon flattening) are "
            "INFERRED to carry (the WDF payload is identical, measured). The "
            "four-wide Action3DEffect action field IS measured here: 10,416 "
            "of 10,946 keys are (3,4,3,3).")
        q["only 46.1% of npc art paths exist"] = (
            "MEASURED: the plaintext profile plans 1,901 of 2,497 npc rows "
            "and only 576 of 1,250 named paths are on disk -- the same "
            "figure as on 6090, where these tables are stale decoys. On 6271 "
            "there is no twin to fall back to, so this is the ceiling.")
        q["GoldenLeagueShop.ini has no control"] = (
            "MEASURED: 40 rows parsed and no control could be checked back "
            "against the bytes (6090: 10 rows, ok). A known limit.")
        return q

    def colour_provenance(self):
        """Inherited from the 6090 scan and not checked on this client."""
        return "colourway inherited from 6090 (unverified here)", "inferred"


PLUGIN = Patch6271()
