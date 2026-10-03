#!/usr/bin/env python3
r"""
patch6256 -- the official Conquer Online patch 6256 client.

**The first of the three no-twin builds (6256 / 6271 / 6716-stamped-6271)**,
sitting between 6090 (15 compiled `.dbc`) and 6609 (12). It ships **zero**
compiled tables, and that one fact decides everything below.

EVIDENCE BASE, AND WHAT KIND OF EVIDENCE IT IS
----------------------------------------------
Written with `Clients/` OFF-LIMITS (wave C rule, 2026-09-19): nothing here
opened the install. Three sources, in falling order of strength:

  [F] `docs/patchnotes/fingerprints.json` (co-pnd), MEASURED by
      `tools/patchnotes.significant_files` -- every `ini/*.dbc` it finds plus
      the key tables, blake2b-128 and size, with explicit ``absent`` records.
  [P] `docs/patchnotes/6256.md` (`tools/patchdict.py` v1 vs 6090), every line
      `[DRAFT]`: measured, not approved.
  [B] `docs/comod_backlog.md` item 31 and `docs/capability_matrix_2026-09-06.md`
      §6.3, both MEASURED on this install on earlier dates.

`measure_6256.py` (kept out of the commit, run by the Director) has since
measured most of the rest; see MEASURED BY below.

MEASURED
--------
* **No compiled tables.** [F] lists no `ini/*.dbc` at all for 6256 and records
  ``ini/3DObj.dbc: absent``; [P] "compiled .dbc tables: 15 -> 0" vs 6090;
  [B] 6.3 "6256 / 6271 / 6716 | 0 .dbc | 70-77 .dat | ~178 .ini"; item 31
  "files with a readable .dbc twin (of 13 checked): 6256 0".
* **Archives are 6090's.** [F] `c3.wdf` 359,069,116 B, 10,274 entries,
  header+index b2 `0a534373...`; `data.wdf` 392,245,257 B, 14,739 entries, b2
  `2a85b65e...` -- equal to 6090's, 6271's and 6609's. [P] "Assets in the
  archives: no change". (Header+index identity, not a payload hash; the
  lineage-wide payload identity is `patch6090`'s md5 claim.)
* **`itemtype.dat` is byte-identical to 6090's** ([F] b2 `25e83327...`,
  6,706,668 B, same on 6271 and 6609), and 6090's copy is TQ-stream seed 9527
  (`patch6090`, `patch6609` docstrings). So the item table is NOT block96.
* **Lower-case `monster.dat` and `magictype.dat`**, as 6609 spells them ([F];
  [P] "`ini/Monster.dat` is gone / `ini/monster.dat` appears"). Sizes 381,636
  and 250,794 B, between 6090's and 6609's.
* **The plaintext lookups are 6090's decoy bytes.** [F] `ini/3dobj.ini`
  (43,017 B, b2 `a37b352a...`) and `ini/3dtexture.ini` (312,407 B,
  `1e639c2e...`) are identical on 6090, 6256, 6271 and 6609; `RolePart.ini`
  too. On 6090/6609 they sit beside a compiled twin; here nothing does.
* **Item-25 split and item-27 effects** ([B] item 31): 4,626 appearances,
  48.8% ini-hit, unchanged by the twin-reading fix (no `weapon.dbc` to read);
  effect values 1,755, 65.9% naive -> 92.1% parsed, 0 comma-lists.
* **No `.pux`, no v1006 maps.** `core/dmap.py`: ".pux starts at 6271"; corpus
  census 2026-08-29: "6256 453 maps, 0 _new".
* Loose layer vs 6090 [P]: 7,540 added, 695 changed (73 in `ini/`, including
  `Action3DEffect.ini`), 38 removed (16 in `ini/`, the 15 `.dbc` among them).

WHY THE PARENT IS `Patch6090`, AND WHY THE BRIEF'S PREMISE DOES NOT HOLD
------------------------------------------------------------------------
The wave-C brief says `patch6090` wins 6256 today at 0.45 (its stamped-sibling
bid). **It cannot**: `Patch6090.confidence` returns 0.0 unless
`ini/3DSimpleObj.dbc` AND `ini/3DObj.dbc` exist, and [F] records 3DObj.dbc
absent. INFERRED from code (measure_6256.py prints the real ranking): the
winner today is the `plaintext` family class at 0.5 -- npc.ini plus the four
plaintext lookups, no compiled marker, a stamp outside 5017/5065/5165.

That winner is wrong about the TABLES, which is what a plugin describes:
`PlaintextFamily` serves 5017's `.dat` specs (binary-plain `MagicType.dat`,
space-row `itemtype.dat`), and 6256's `itemtype.dat` is 6090's `@@` TQ-stream
file byte for byte. So the parent is `Patch6090` -- the `.dat` family, the
archives and the loose-art conventions are 6090's -- and the ONE thing 6090
has that 6256 lacks, the compiled art layer, is overridden positively:

OVERRIDES, EACH FOR A MEASURED REASON
-------------------------------------
* `confidence`       0.95 on the exact stamp `6256`, else 0.0 (patch6609 rule).
* `table_profile`    `PROFILE_PLAINTEXT`. `PROFILE_OFFICIAL` names
                     `3DSimpleObj.dbc`, `3DObj.dbc`, `3DTexture.dbc`,
                     `3dmotion.dbc`, none shipped here -- it would load zero
                     simple objects and not raise. (Same shape as `patch7589`.)
* `prefers_compiled_tables`  False: there is no twin to prefer.
* `table_specs`      6609's curated specs (its lower-case filenames match
                     6256's disk), then 6090's `.ini` census, BOTH filtered
                     to files this root ships (the patch7205 pattern: a spec
                     for an absent file renders as "we could not read it"),
                     then 6090's per-install `Action3DEffect.ini` shape.
* `table_quirks`     drops the four 6090 quirks that are statements ABOUT the
                     compiled tables (stale decoys, second motion reader,
                     unpadded compiled ids, u32 motion wrap via
                     `3dmotion.dbc`) -- each describes a file 6256 does not
                     ship -- and adds 6256's own.
* `colour_provenance`  inherited from the 6090 scan, NOT verified here.
* `import_plan`      same shared archives; the note no longer says ".dbc read".
* `DAT_CENSUS_ERA = False`  -- literal. `itemtype.dat` is 6090's TQ-stream file
                     [F], not block96; the `tools/dat_census.py` slot for 6256
                     stays commented. measure_6256.py classifies every `.dat`.

MEASURED BY `measure_6256.py` (Director's run, 2026-09-19, EXIT 0)
-----------------------------------------------------------------
Every figure below carries its install. [M] = that run.

* **Detection** [M], 46 installs ranked: the winner changes on `6256` ONLY;
  `patch6256` scores 0 everywhere else. 6256: `patch6256` 0.95, margin 0.45;
  without it `plaintext` 0.50 (the inference above, now measured). CONTROL
  6090 -> `patch6090` 0.95, PASS.
* **Containers** [M]: 6256 `c3.wdf` + `data.wdf`, 0 `.dbc` (6090: same pair,
  15 `.dbc`). None of `ABSENT_COMPILED` present. Identity split vs 6090
  (`IDENTICAL_TO_6090` / `CHANGED_FROM_6090`): 0 mismatches.
* **Plaintext lookups are 6090's decoys** [M], sha256 equal on 6090 and 6256:
  3DSimpleObj.ini f34f5633, 3dobj.ini 9202de93, 3dtexture.ini 6be2fece,
  3dmotion.ini 9951727a, armor.ini 8e64691e. `c3.wdb` DIFFERS (6090 207324bb,
  215,611 ids; 6256 1d292b95, 1,152,706 ids).
* **Codecs** [M]: 6256 tq-stream 36, binary-plain 6, rar-mangled 11,
  plaintext 6, rsa-mysqldump 8, shift-obfuscated 1, unknown 1, empty 1 --
  **block96 0**. itemtype / monster / magictype / mounttype / ItemtypeSub all
  tq-stream on both 6090 (CONTROL, PASS) and 6256. Hence
  `DAT_CENSUS_ERA = False`.
* **levexp.dat** [M]: 6256 4,030 B, sha256 df41c576 -- the very bytes 6090's
  refusal describes, so the inherited refusal is true of this file.
* **NPC chain** [M] (`npcart.audit`, c3.wdb supplied as mesh index):
      6090 OFFICIAL   2,243 / 2,251  (CONTROL; npcart records 2,256/2,264 --
                                      13 rows of counting difference, the
                                      same gap patch7878 records)
      6090 PLAINTEXT  1,802 / 2,251
      6256 OFFICIAL       0 / 2,431  (0 simple objects: no .dbc to read)
      6256 PLAINTEXT  1,867 / 2,431  geometry missing on disk 0
  **KNOWN LIMIT: 564 of 6256's 2,431 npc rows (23.2%) do not resolve** --
  1,686 motion misses and 552 unknown simple objects among the reasons. The
  frozen chain never heard of them and nothing compiled stands behind it.
* **Appearance tables** [M] (`AssetRoot.bare(...).part_tables()`): 6256 reads
  the plaintext ones -- armor.ini 955, armet.ini 1,168, weapon.ini 4,828 --
  where 6090 reads armor.dbc 3,326 / armet.dbc 2,609 / weapon.dbc 11,164.
  **KNOWN LIMIT: `mount` is Mount.ini with 1 row** (6090 Mount.dbc 1,651) and
  `misc` 0 rows: the mount appearance table is a stub on this build.
* **Catalogs** [M]: 143 subjects, 141 ok on 6256 (6090: 143 / 141); the two
  refused on both are `action` and `levexp`. Under `plaintext` (today's
  winner without this plugin) 6256 yields 1 subject, 0 ok.
  item 24,270 (= 6090's, byte-identical file: EQUALITY CONTROL PASS);
  monster 1,122 (6090 982); magic 1,299 (1,266); mount 2,860 (2,574);
  map:dest 327 (322); magic:op 647 (647); item:value 1,094 (1,094);
  gamemap 326 (303); npc.ini 2,431.
  **The ">3x 6090" noise check FIRED on four subjects, and each is growth,
  not noise**: `item:sub` 2,847 vs 271 tracks the file, 890,706 B vs 84,265
  B, classified tq-stream (control is re-decode, the weaker kind -- stated);
  `action3deffect3/4/5` 66 / 53 / 27 vs 1, raw-controlled.
* **No lost subjects** (finalize step 5b, one small script on 6256 only):
  ok subjects of `plaintext` (1 subject, 0 ok) minus ours = EMPTY; of
  `patch6090` read on this root (143 / 141 ok) minus ours = EMPTY.
* **Action3DEffect.ini** [M]: 6256 311,879 B, 0 section headers (flat keys),
  key widths 3.4.3.3 x10,402, 4.4.3.3 x246, 1.4.3.1 x170, 1.4.3.2 x51,
  5.4.3.3 x30 -- the action field is four wide on every form counted, so
  the inherited `FIELD_WIDTHS` holds. 6090: 289,537 B, 3.4.3.3 x10,113.
* **ROW_COLUMNS** [M], all five present and the name column non-numeric on
  every row: region 231, EventTypeName 24, VipTrans 31, restrain 5,
  GoldenLeagueShop 15.
* **Encoding** [M]: npc.ini (13 high-byte lines), SlotNpc.ini (4),
  terrainnpc.ini (53) decode under strict GBK; NpcX.ini / npcex.ini are
  pure ASCII. **That is NOT the owner's positive proof** -- no CJK check, no
  probe that the decoder can reject, no 7205 control -- and 6256 predates
  7205, so `TEXT_ENCODING` stays latin1 (inherited).
* **Coverage** [M]: 177 `.ini`, 130 declared, 47 undeclared; 70 `.dat`, 57
  not declared by a curated or censused spec.

STILL NOT MEASURED (never "unchanged")
--------------------------------------
* `texture_for_mesh` (999<dir>0 NPC rule). The instrument FAILED ITS OWN
  CONTROL: it read 1/9 on 6090 where 6090's docstring records 85/95 (that
  figure came from the compiled chain; mine enumerated `3dobj.ini`). A
  control that does not reproduce means the 6256 reading (also 1/9) says
  nothing. Recorded, not tuned.
* `socket_correction` / `BROKEN_LWEAPON_SHAPES`: measured on 6090's
  `c3/0001/410/100.c3`; [P] shows 41-44 changed loose files under each of
  `c3/0001`..`c3/0004`.
* Colour sets and name pins: inherited from the 6090 scan, see
  `colour_provenance`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.catalog import censused                   # noqa: E402
from plugins.patch6090 import (Patch6090,              # noqa: E402
                               with_action3deffect_shape)
from plugins.patch6609 import SPECS_6609                # noqa: E402

STAMP = "6256"

#: The compiled tables 6090 ships and 6256 does not, MEASURED ([F]: no
#: `ini/*.dbc` listed for 6256; [P]: "15 -> 0" with this exact list). Named so
#: "this client has no such table" is distinguishable from "we failed to read
#: it".
ABSENT_COMPILED = (
    "ini/3DEffect.dbc", "ini/3DEffectobj.dbc", "ini/3DObj.dbc",
    "ini/3DSimpleObj.dbc", "ini/3DTexture.dbc", "ini/3dmotion.dbc",
    "ini/EmotionIco.dbc", "ini/armet.dbc", "ini/armor.dbc", "ini/misc.dbc",
    "ini/miscmotion.dbc", "ini/mount.dbc", "ini/mountmotion.dbc",
    "ini/weapon.dbc", "ini/weaponmotion.dbc",
)

#: Byte-identical to 6090's, MEASURED by blake2b in fingerprints.json (co-pnd,
#: measured before 2026-09-19). The claims this plugin inherits from 6090
#: about these files rest on this list; `measure_6256.py` re-hashes them.
IDENTICAL_TO_6090 = (
    "ini/itemtype.dat", "ini/3dobj.ini", "ini/3dtexture.ini",
    "ini/RolePart.ini", "play.exe", "AutoPatch.dat",
)

#: Different from 6090's in the same fingerprint -- the control that makes the
#: list above mean something (a comparator that always said "same" fails it).
CHANGED_FROM_6090 = (
    "ini/GameMap.dat", "ini/mounttype.dat", "Conquer.exe", "Server.dat",
    "version.dat",
)

#: 6090 quirks that describe the COMPILED tables. Each is about a file 6256
#: does not ship, so serving it here would be a statement about nothing.
COMPILED_ONLY_QUIRKS = (
    "stale ini decoys",
    "a second motion reader",
    "unpadded ids in compiled tables",
    "u32-wrapped motion ids",
)


def _version(root: Path) -> str:
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class Patch6256(Patch6090):
    name = "patch6256"
    label = "Official patch client 6256"
    origin = "official"
    aliases = ("6256",)

    notes = ("6090's .dat family and archives (itemtype.dat and the .wdf "
             "indexes byte-identical to 6090's) with NO compiled .dbc tables: "
             "the first of the 6256/6271/6716 no-twin group. Art resolves "
             "through the plaintext chain (6090's frozen decoy bytes) and "
             "ini/c3.wdb; nothing compiled stands behind them. monster.dat "
             "and magictype.dat are lower-case, as on 6609. Colour and name "
             "data is inherited from the 6090 scan and NOT verified here.")

    #: Literal, and MEASURED from the fingerprint: `itemtype.dat` is 6090's
    #: TQ-stream file byte for byte, not block96. The census slot stays
    #: commented.
    DAT_CENSUS_ERA = False

    ABSENT_HERE = ABSENT_COMPILED

    #: MEASURED by measure_6256.py on Clients/6256: `npcart.audit` under
    #: PROFILE_PLAINTEXT with `ini/c3.wdb` as mesh index. A KNOWN LIMIT, not
    #: a target: the frozen chain cannot name the other 564.
    NPC_COVERAGE = {"rows": 2431, "resolved": 1867, "unresolved": 564,
                    "geometry_missing_on_disk": 0}

    #: MEASURED: the appearance slot tables `part_tables()` serves here, with
    #: rows. `mount` is a 1-row stub (6090's Mount.dbc has 1,651).
    PART_TABLE_ROWS = {"body": ("armor.ini", 955), "armet": ("armet.ini", 1168),
                       "r_weapon": ("weapon.ini", 4828),
                       "mount": ("Mount.ini", 1), "misc": ("misc.ini", 0)}

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The exact `version.dat` stamp `6256` at 0.95, else 0.0.

        Required, not stylistic: `Patch6090.confidence` returns 0.0 on this
        install (no `.dbc`), so without this plugin 6256 falls to a family
        whose `.dat` specs are 5017's.
        """
        return 0.95 if _version(Path(root)) == STAMP else 0.0

    # -- the art layer: no compiled tables here -----------------------------
    def table_profile(self):
        """`PROFILE_PLAINTEXT`: every table `PROFILE_OFFICIAL` names is a
        `.dbc`, and this install ships none (`ABSENT_COMPILED`)."""
        import npcart                                    # noqa: PLC0415
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """False: there is no compiled twin on this install to prefer."""
        return False

    # -- tables ------------------------------------------------------------
    def table_specs(self, root):
        """6609's curated specs, then 6090's `.ini` census, both filtered to
        files this root ships; then the per-install Action3DEffect shape.

        6609's, not 6090's, because they name `monster.dat` / `magictype.dat`
        in lower case, which is what 6256 ships ([F]); `_find_table` is
        case-insensitive anyway, so the choice is about the listing showing
        the real name. The `item_refine_*` specs 6609 adds drop out by the
        disk filter if 6256 does not ship them (NOT YET MEASURED).

        6090's census rather than 6609's: 6256's `ini/` is 6090's plus 73
        changed files [P], and nobody has censused this build.
        `measure_6256.py` prints which of the build's `ini/*.ini` neither
        census covers.
        """
        root = Path(root)
        ini = root / "ini"
        present = ({p.name.lower() for p in ini.iterdir()} if ini.is_dir()
                   else set())
        curated = tuple(s for s in SPECS_6609
                        if s.filename.lower() in present)
        taken = {s.subject for s in curated}
        files = {s.filename.lower() for s in curated}
        borrowed = tuple(s for s in censused.specs_for("patch6090")
                         if s.filename.lower() in present
                         and s.subject not in taken
                         and s.filename.lower() not in files)
        return with_action3deffect_shape(
            censused.extend(curated + borrowed, self.name), root)

    def table_quirks(self):
        q = super().table_quirks()
        for k in COMPILED_ONLY_QUIRKS:
            q.pop(k, None)
        q["no compiled tables at all"] = (
            "6090 ships 15 .dbc and 6609 ships 12; 6256 ships 0 (as do 6271 "
            "and the 6716 folder, stamped 6271). The plaintext 3dobj.ini and "
            "3dtexture.ini are byte-identical to 6090's decoys, so on this "
            "build the frozen 2008-era chain plus ini/c3.wdb is all there is. "
            "An npc the frozen chain never heard of is indistinguishable from "
            "an npc with no art: nothing raises.")
        q["the weapon split has no twin to read"] = (
            "4,626 appearances, 48.8% ini-hit, and the twin-reading fix "
            "(backlog item 25) gains nothing here: there is no weapon.dbc. "
            "Measured, backlog item 31.")
        q["the mount appearance table is a stub"] = (
            "part_tables() serves Mount.ini with 1 row here, against 6090's "
            "Mount.dbc with 1,651 (measured). mounttype.dat still lists "
            "2,860 mounts; their appearance rows are simply not in any "
            "table this build ships.")
        q["564 npc rows cannot resolve"] = (
            "npcart.audit under the plaintext chain: 1,867 of 2,431 npc.ini "
            "rows resolve (6090's same frozen chain: 1,802 of 2,251). The "
            "564 name motions or simple objects the 2008-era lookups never "
            "received, and nothing raises for them.")
        q["monster.dat and magictype.dat are lower-case"] = (
            "As on 6609; 6090 spells them Monster.dat / MagicType.dat. The "
            "lookup is case-insensitive, an exact-case one is not.")
        return q

    def colour_provenance(self):
        """Inherited from the 6090 scan and **not checked here** -- the
        archives are 6090's by index, but the loose layer adds 7,540 files
        (301 under `c3/Mount`) that may re-skin what the sets name."""
        return "colourway inherited from 6090 (unverified here)", "inferred"

    # -- library import ----------------------------------------------------
    def import_plan(self, root, exists) -> dict:
        plan = super().import_plan(root, exists)
        plan.update({
            "note": "patch 6256: shared archives skipped (c3.wdf/data.wdf "
                    "indexes identical to 6090's), loose layer imported, "
                    "plaintext tables and c3.wdb read -- no .dbc on this "
                    "build",
        })
        return plan


PLUGIN = Patch6256()
