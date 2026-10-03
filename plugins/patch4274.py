#!/usr/bin/env python3
r"""
patch4274 -- official Conquer Online patch 4274, the OLDEST official build
in the corpus and its BASE INSTALL: there is no earlier official client to
patch FROM, so every file here is first-appearance and no "changed since"
claim can be made about it. (Director's ruling, 2026-09-19: it is still a
plugin, and it is the base install.)

PARENT: `PlaintextFamily`, NOT `Patch5017`, AND WHY
---------------------------------------------------
The brief suggested the `Patch5017` family. The FAMILY is right; the concrete
class is not, for the reason `patch5017.ROW_COLUMNS`' own comment gives:
*"this family's members do not derive from each other, so a fact set on one
of them reaches exactly one build"*. `patch5065` and `patch5165` derive from
`PlaintextFamily` for the same reason. Subclassing `Patch5017` would inherit
`SPECS_5017` -- which declares `itemtype.dat` and `Monster.dat` as
`tq-stream` text, WRONG here (below) -- plus 5017's `ROW_COLUMNS`,
`PART_SLOTS` and `ABSENT_HERE`, all measured on 5017's bytes. Every one of
those would have to be switched off, and an override that switches an
inherited claim off is the shape `plaintext.py`'s header warns gets deleted
by a later tidy-up.

What makes 4274 a plaintext-family member at all: it ships **no compiled
twin** (`ini/3DObj.dbc` absent, fingerprints.json) and **no `ini/c3.wdb`**
(`docs/claim_enumeration_audit_2026-09-07.md`: the set with no `c3.wdb` is
exactly {4274, 5017, 5065, CCO-snapshot}), so whatever tables it has in
`ini/` are the live ones -- the family's one load-bearing statement
(`prefers_compiled_tables() is False`) is true here for the family's reason.

MEASURED ELSEWHERE, CITED (nothing below was measured by this plugin's
author; the machine was closed to install reads when this was written)
------------------------------------------------------------------------
From `C:/Claude/co-pnd/docs/patchnotes/fingerprints.json` (measured
2026-09-18, entry `4274`, `official: true`, stamp `4274`):

    version.dat        4 B, stamp "4274"
    c3.wdf       544,627,365 B, 7,148 entries   5017/5065: 359,069,116 B
    data.wdf     365,032,286 B, 11,869 entries  5017/5065: 392,245,257 B
    ini/itemtype.dat   2,133,044 B      ini/MagicType.dat   946,452 B
    ini/Monster.dat        9,967 B      ini/GameMap.dat       3,689 B
    ini/3dobj.ini         30,011 B      ini/3dtexture.ini   239,507 B
    ini/3DObj.dbc       ABSENT          ini/RolePart.ini    ABSENT
    ini/mounttype.dat   ABSENT          Conquer.exe, play.exe, Server.dat

* **Its archives are its OWN.** Different sizes, entry counts and blake2b
  from the `c3.wdf`/`data.wdf` pair that 5017 .. 7205 share byte for byte
  (`docs/capability_matrix_2026-09-06.md` §2.1: 4274's payload family
  `24c3b60ec153`, alone; no package DLL, no `c3.wdb`). So the family's
  `import_plan` -- `sharedArchives: True`, "shared with the whole official
  lineage" -- is FALSE here and is overridden.
* **Its `.dat` tables are NOT ENCRYPTED and are BINARY RECORDS.**
  capability_matrix §2.2 era **B0**: `itemtype.dat` entropy 4.59,
  `Monster.dat` 4.27 with `Pheasant` legible at offset 8.
  `docs/itemtype_4274_binary_2026-08-27.md`: `inidat.classify` calls
  `itemtype.dat` `binary-plain`; 1,004,112 B of undecoded id-list section,
  then 5,533 fixed 204-byte records, name at +0, and **no item id in the
  record**. `tests/test_patch7205.py::TheGuardRefusesTheWrongCipher` pins
  both the `binary-plain` verdict and `coassets.load_items(4274) == []`.
  So 5017's `tq-stream` specs for `item` and `monster` would be the wrong
  cipher -- `SPECS_4274` declares them as REFUSALS carrying these numbers.
* **It declares NO part slots.** No `ini/RolePart.ini` (fingerprints) and no
  `c3.wdb` ROPT to fall back on; `tools/mountart.py` records that
  `part_tables()` RAISES `FileNotFoundError` on 4274, the only such install.
  So `PART_SLOTS = ()` and every slot socket is "no opinion" (see below).
* `GameMap.dat` names `.DMap` x115 (`docs/map_twin_precedence.md`).

INFERRED WHEN WRITTEN -- ALL THREE NOW SETTLED BY measure_4274.py (below)
------------------------------------------------------------------------
* **`MagicType.dat` is 5017's record layout.** Arithmetic only:
  946,452 == 4 + 596 x (4 + 1,584), the 5017 layout exactly, with a count
  of 596. `docs/itemtype_4274_binary_2026-08-27.md` warns in bold that *"an
  exact integer division is not evidence of a record size"*, so this is
  declared ONLY because `catalog.magic_records` is fail-closed: it raises
  unless the header count reproduces the length AND `record.type == id//10`
  on every record, and both arrays live in different regions of the file.
  If the layout is wrong the catalog REFUSES, it does not count noise.
* **`GameMap.dat` is the shared binary map registry.** Declared on the same
  basis: `catalog.gamemap_records` raises on any leftover byte.
* **Which `ini/*.ini` tables exist, and their encoding.** Not listed:
  fingerprints.json records only the significant files. No npc spec and no
  census spec is declared until phase 5 lists `ini/`. `TEXT_ENCODING` is the
  inherited latin1, unmeasured.

NOT CLAIMED -- the family's measurements are 5017/5065/5165's, not 4274's
--------------------------------------------------------------------------
`PlaintextFamily` answers several hooks from a census of 5017..5165 bodies
and tables: the female `v_l_weapon` correction, `BODY_SOCKETS`, the slot
sockets, `sockets_present`, the always-on aura rows (646/586/622), the
three-wide `Action3DEffect` fields and ten-wide motion keys, and the motion
families on disk. None was counted on 4274, whose art is a different archive
payload. Each is reset here to the base `Plugin`'s "no opinion" answer
rather than inherited, because an inherited family number on a 4274 class
reads as a 4274 measurement. `table_quirks` carries only 4274's own.

MEASURED BY measure_4274.py (2026-09-19, run by the Director, EXIT 0,
1.9 s; every number is Clients/4274's unless another install is named)
-------------------------------------------------------------------------
* **Detection, 46 installs** (every `Clients/` folder plus the live
  `Classic Conquer 2.0`), `plugins._rank` with and without this plugin.
  CONTROL first: 5017 -> `patch5017` 0.95 (PASS). The winner or margin
  changes on **4274 ONLY**: `patch4274` 0.95, margin 0.45, where it was the
  family class `plaintext` 0.50. `patch4274` scores 0.00 on the other 45.
  So 4274 carries the full plaintext chain (npc.ini, 3DSimpleObj.ini,
  3dobj.ini, 3dtexture.ini, 3dmotion.ini present; npc.json absent) and was
  being served the FAMILY profile, unstamped.
* **Containers:** `AssetRoot._discover_archives` -> `c3.wdf`, `data.wdf`
  (the sizes above; 5017's pair is the other one).
* **Part tables:** CONTROL 5017 -> 7 slots (armet body head l_weapon misc
  mount r_weapon). 4274 -> RAISES `FileNotFoundError: ini/RolePart.ini (and
  no ROPT section in ini/c3.wdb)`. `ini/c3.wdb`, `ini/ItemTexture.ini`,
  `ini/3DObj.dbc`, `ini/3DSimpleObj.dbc`, `ini/mounttype.dat` all absent.
* **`ini/`: 108 files.** `.dat` by `inidat.classify`: 9 `binary-plain`
  (Action, AutoAllot, GameMap, itemtype, LevelExp, MagicType, Monster,
  Pet.Dat, Shop) and 2 `plaintext` (Tips.dat, UserHelpInfo.dat) -- **ZERO
  block96, zero tq-stream**. `.ini`: 40 plaintext, 4 empty, 1 `unknown`
  (TxtEmotion.ini, 146 B) and 2 `block96` -- `EmotionIco.ini` (336 B) and
  `TxtColor.ini` (43 B). Those two are FINDINGS about the classifier, not
  about a cipher: no block96 client exists within ~2,600 patches of this one
  and 43 B is under four 12-byte blocks; recorded, not acted on.
* **Only on 4274, against 5017:** `GameMap.ini`, `MagicType.ini` (118,135 B
  plaintext), `Monster.ini` (22,538 B plaintext), `MiniMap.Ani`. Plaintext
  twins of three binary tables -- declared later the same day (below);
  which of each pair the CLIENT reads is unmeasured.
  5017 ships 40+ files 4274 does not (RolePart.ini, levexp.dat,
  MapDestination.dat, gui.ini, head.ini, misc.ini ...).
* **catalogs(): 4 subjects, 2 ok** (5017 under Patch5017: 66 / 64).
  `magic` 590 rows (596 records parsed; 5017: 642 rows of 648), `gamemap`
  115 rows (5017: 145), all flag 256, every path `.DMap`. Both INFERRED
  layouts PASSED their fail-closed readers: MagicType consumes all 946,452
  B exactly with `record.type == id//10` on 596/596 (first row 10000
  `Thunder`, same as 5017's); GameMap parses 115 records, no leftover byte.
  `item` and `monster` refused by name, as declared then. (SUPERSEDED the
  same day: see PLAINTEXT TWINS DECLARED below -- now 10 subjects, 8 ok.
  Still a KNOWN LIMIT against 5017's 66: no generated `.ini` census covers
  this build.)
* **npc family:** `npc.ini` 48,006 B / 364 section headers, `terrainnpc.ini`
  2,862 B / 36, `npcex.ini` 113 B / 1. Declared below.
* **Encoding:** `npc.ini` has 218 bytes >= 0x80 and strict-decodes as GBK,
  as do terrainnpc.ini and npcex.ini -- which is NOT proof (latin1-only
  bytes strict-decode as GBK too), no CJK check and no 7205 control was
  run. So `TEXT_ENCODING` stays `latin1`, declared explicitly.
* **Motion families on disk:** `c3/0001`..`c3/0004` (same as 5017/5065).
  `3dmotion.ini` numeric keys: 54,749 ten wide, 17 seven wide, 2 three
  wide -- the family's ten-wide body-motion key holds here. The
  `Action3DEffect.ini` probe found NO pure-digit keys, so it measured
  nothing (the instrument could not fire); the aura and field widths stay
  unclaimed.
* `import_plan`: archives `c3.wdf`, `data.wdf`, `sharedArchives` False.
* **PLAINTEXT TWINS DECLARED (2026-09-19, Director's request; measured by
  a one-install catalogs() run on Clients/4274, every control PASSED):**

      subject             file             rows  (sections/lines)  control
      monster             Monster.ini       316  (329, 12 dup)     raw
      magic:ini           MagicType.ini     593  (Amount=593)      raw
      gamemap:ini         GameMap.ini       114  (114)             raw
      npc:npc.ini         npc.ini           360  (364, 4 dup)      raw
      npc:terrainnpc.ini  terrainnpc.ini     36  (36)              raw
      npc:npcex.ini       npcex.ini           1  (1)               raw

  Cross-table controls, independent of the reader: `MagicType.ini`'s 593
  rows equal its own `Amount=593` header (279 distinct types, 586 distinct
  type+level pairs); `GameMap.ini`'s 114 ids are all in `GameMap.dat` with
  the SAME path on 114/114, and the `.dat` has one more, map `1626`, which
  the `.ini` does not name -- a FINDING, recorded, not reconciled.
  `monster` now reads from `Monster.ini`; the binary `Monster.dat` keeps its
  refusal under `monster:dat`. **`item` stays refused: 4274 ships no
  plaintext item table** (`ItemAdd.ini` is 9-column additions keyed by item
  id, not the item table). catalogs(): **10 subjects, 8 ok.**
* **No lost subjects (step 5b):** the previous winner, family class
  `plaintext`, declares no tables on 4274 -- its `catalogs()` is the single
  refusal `(no tables)`, ok set EMPTY. `patch4274`'s ok set is {magic,
  gamemap} at first, and the eight above after the twins were declared.
  Lost: NONE (set difference, measured 2026-09-19).

DETECTION
---------
`confidence()` is the stamp and nothing else: 0.95 on `version.dat` ==
`4274`, 0.0 otherwise. It deliberately does NOT reuse the family's
plaintext-chain gate (npc.ini + 3DSimpleObj.ini + 3dmotion.ini + no .dbc),
because two of those files are unmeasured here -- a gate on an unmeasured
file could leave the one build this plugin exists for unclaimed.
`patch6090` returns 0.0 on 4274 (it requires `ini/3DObj.dbc`, absent). The
family class `plaintext` bids 0.50 (MEASURED above), so this plugin wins by
0.45.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                             # noqa: E402
from plugins.plaintext import PlaintextFamily, version_stamp  # noqa: E402
from plugins.catalog import censused                   # noqa: E402
from plugins.catalog import (                          # noqa: E402
    TableSpec, KIND_SECTIONS, KIND_SPACE_ROWS, KIND_GAMEMAP,
    KIND_MAGIC_RECORDS, npc_specs)

#: The two tables whose codec 5017's specs get wrong here, declared as NAMED
#: refusals so `catalogs()` says what is known instead of omitting them.
SPECS_4274 = (
    TableSpec("item", "itemtype.dat", KIND_SPACE_ROWS, "binary-plain",
              label_key=None, refusal=(
        "itemtype.dat on 4274 is UNENCRYPTED BINARY RECORDS (inidat: "
        "binary-plain), not the tq-stream text 5017 ships -- "
        "docs/itemtype_4274_binary_2026-08-27.md. 2,133,044 B: an undecoded "
        "1,004,112 B section of u32 runs that look like item-id lists, then "
        "5,533 fixed 204-byte records (name at +0, fields from +16, the same "
        "columns as 5017's text). The item ID IS NOT IN THE RECORD and the "
        "id-to-record binding is unestablished, so no row can be keyed; "
        "coassets.load_items returns [] on this install "
        "(tests/test_patch7205.py pins it).")),
    TableSpec("monster:dat", "Monster.dat", KIND_SECTIONS, "binary-plain",
              label_key=None, refusal=(
        "Monster.dat on 4274 is 9,967 B of UNENCRYPTED BINARY (entropy "
        "4.27, 'Pheasant' legible at offset 8 -- "
        "docs/capability_matrix_2026-09-06.md §2.2, era B0), where 5017's "
        "is 112,980 B of tq-stream sectioned text. Its record layout has "
        "not been established by anyone, so it is named, not parsed. "
        "4274 ALSO ships ini/Monster.ini (22,538 B, plaintext), which 5017 "
        "does not; that is served as the `monster` subject instead (which "
        "of the two the client itself reads is unmeasured).")),
    # Layout INFERRED, then MEASURED: 596/596 records agree on 4274 (see
    # the module docstring). `magic_records` fails
    # closed (count must reproduce the length, and record.type == id//10 on
    # every record), so a wrong guess refuses rather than counting noise.
    TableSpec("magic", "MagicType.dat", KIND_MAGIC_RECORDS, "binary-plain"),
    # Same basis, MEASURED: 115 records, no leftover byte, on 4274.
    TableSpec("gamemap", "GameMap.dat", KIND_GAMEMAP, "binary-plain"),
    # -- The PLAINTEXT TWINS 4274 ships and 5017 does not (measure_4274.py
    #    phase 4). Declared 2026-09-19 at the Director's request; the counts
    #    and control results are in the module docstring.
    # Sections keyed by monster NAME with no Name key, like 5017's
    # Monster.dat text -- so `Level` labels a row, as MEASURED_SECTION_LABELS
    # does on 5017.
    TableSpec("monster", "Monster.ini", KIND_SECTIONS, "plaintext",
              label_key="Level"),
    # `Amount=593` count header, then space rows: col 0 the magic TYPE
    # (repeated once per level, so duplicates are expected), col 2 the name.
    TableSpec("magic:ini", "MagicType.ini", KIND_SPACE_ROWS, "plaintext",
              columns={"id": 0, "name": 2}),
    # `[Map<id>]` sections with a single `File=` key.
    TableSpec("gamemap:ini", "GameMap.ini", KIND_SECTIONS, "plaintext",
              label_key="File"),
) + npc_specs(("npc.ini", "terrainnpc.ini", "npcex.ini"))


class Patch4274(PlaintextFamily):
    name = "patch4274"
    label = "Official patch client 4274 (base install)"
    origin = "official"
    aliases = ("4274",)

    STAMP = "4274"
    notes = ("The oldest official client and the BASE INSTALL (nothing to "
             "patch from). Its own c3.wdf/data.wdf -- not the pair 5017..7205 "
             "share -- no compiled twins and no c3.wdb, no RolePart.ini (so "
             "no declared part slots), and its itemtype.dat / Monster.dat "
             "are UNENCRYPTED BINARY; monsters, skills, maps and npcs are "
             "served from the plaintext .ini twins 4274 ships. "
             "No visual pass has been done on this client.")

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """0.95 on the exact stamp `4274`, 0.0 otherwise -- the same rule as
        every official plugin. Not gated on the family's plaintext chain;
        see the module docstring, DETECTION."""
        return 0.95 if version_stamp(root) == self.STAMP else 0.0

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        # No census entry exists for patch4274 (`tools/gen_ini_specs.BUILDS`
        # does not list it), so this is SPECS_4274 alone today; `extend` is
        # called so a later census reaches this build without a code change.
        return censused.extend(SPECS_4274, self.name)

    #: Declared absences, from fingerprints.json's `absent: true` rows.
    #: `ItemTexture.ini` and `c3.wdb` were added from measure_4274.py
    #: phase 2 (absent on 4274, as on 5017).
    ABSENT_HERE = ("ini/RolePart.ini", "ini/3DObj.dbc", "ini/mounttype.dat",
                   "ini/ItemTexture.ini", "ini/c3.wdb")

    #: No `RolePart.ini` and no `c3.wdb` ROPT: the install declares NO part
    #: slots, and `AssetRoot.part_tables()` raises on it (tools/mountart.py).
    PART_SLOTS: tuple = ()

    #: Every slot "no opinion". With no declaration there is no engine slot
    #: to say `""` about, and the family's sockets are a census of
    #: 5017..5165 body meshes, not of 4274's archive.
    SLOT_SOCKETS = {k: None for k in PlaintextFamily.SLOT_SOCKETS}
    BODY_SOCKETS: tuple = ()
    BROKEN_LWEAPON_SHAPES: tuple = ()

    #: MEASURED (measure_4274.py phase 6): `c3/0001`..`c3/0004` hold `.c3`.
    MOTION_FAMILIES_ON_DISK = ("0001", "0002", "0003", "0004")
    #: NOT MEASURED on 4274 (which families `3dmotion.ini` names) -- `None`,
    #: not `()`, because `()` is the family's word for "measured, and none".
    UNNAMED_MOTION_FAMILIES = None

    #: MEASURED: zero block96 (and zero tq-stream) `.dat` on this install --
    #: 9 binary-plain, 2 plaintext. Not the census's era. Literal False.
    DAT_CENSUS_ERA = False

    #: latin1, declared rather than inherited. GBK was NOT proven: the
    #: strict-decode result alone cannot tell GBK from latin1-only bytes,
    #: and no CJK check or 7205 control was run. Pre-7205 keeps latin1.
    TEXT_ENCODING = "latin1"

    #: The family's widths were measured on 5017..5165's Action3DEffect.ini.
    FIELD_WIDTHS: dict = {}

    def socket_correction(self, socket, body_appearance):
        return Plugin.socket_correction(self, socket, body_appearance)

    def sockets_present(self):
        return Plugin.sockets_present(self)

    def aura_convention(self) -> str:
        return Plugin.aura_convention(self)

    def table_quirks(self):
        return {
            "base install: nothing to patch from":
                "4274 is the oldest official build in the corpus. Every "
                "file is first-appearance; there is no predecessor to diff "
                "against, so patch notes for it are an inventory, not a "
                "change list.",
            "its archives are NOT the shared lineage pair":
                "c3.wdf 544,627,365 B / 7,148 entries and data.wdf "
                "365,032,286 B / 11,869 entries, against the 359,069,116 / "
                "392,245,257 B pair 5017..7205 share byte for byte. An "
                "importer that skips 'shared' archives would skip art that "
                "exists nowhere else (garment_missing_art_full_corpus: 496 "
                "hashes no other install has).",
            "the .dat tables are binary, not encrypted":
                "Era B0: itemtype.dat and Monster.dat are plain binary "
                "records. A tq-stream or block96 reader fails on them, and "
                "a row-counting text parser returns 0 rows instead of "
                "refusing -- the trap docs/itemtype_4274_binary_2026-08-27.md "
                "records.",
            "no RolePart.ini, so no part slots":
                "AssetRoot.part_tables() raises FileNotFoundError here; "
                "'declares no parts' is a different fact from 'declares "
                "parts and ships none' (tools/mountart.py).",
            "the family's socket/aura/width answers are not claimed":
                "PlaintextFamily's sockets, aura rows and key widths were "
                "measured on 5017..5165 and are reset to no-opinion here "
                "until someone measures 4274.",
        }

    def import_plan(self, root, exists) -> dict:
        """The base `Plugin` plan, with `sharedArchives` FALSE: 4274's
        `c3.wdf`/`data.wdf` are its own (fingerprints.json), so the family's
        "skip the shared archives" plan would drop 4274-only art."""
        plan = Plugin.import_plan(self, root, exists)
        plan.update({
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": f"{self.label}: its OWN archives indexed (not the "
                    f"5017..7205 shared pair), loose layer imported, binary "
                    f".dat tables not parsed",
        })
        return plan


PLUGIN = Patch4274()
