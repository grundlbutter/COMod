#!/usr/bin/env python3
r"""
patch7878 -- official Conquer Online patch 7878, and the first client whose
plaintext tables are neither live nor shadowed.

The family's parse profile lives in `plugins/plaintext.py` and is not
restated. **But read the shortfall below before trusting anything this plugin
resolves**: 7878 passes the family's positive test and is still not the same
situation as 5017/5065/5165, and the difference is not visible from the test.

WHY IT NEEDS ITS OWN PLUGIN AT ALL -- IT IS ALREADY BEING CLAIMED
-----------------------------------------------------------------
MEASURED 2026-08-11, before this file existed: `plugins.detect()` on a 7878
root returns **`plaintext` at 0.5**. The family class deliberately claims an
unstamped install weakly *"instead of falling to GENERIC and resolving
nothing"*, which is right for a repack of one of its three members. 7878 is
not a repack of them -- it is stamped `b'7878'`, ships four DatPkg archive
pairs where they ship two WDFs, and its plaintext tables have been frozen for
years. **So the pre-existing answer was not "no plugin", it was a confident
wrong one**, and it becomes reachable the moment a DatPkg root is openable.

WHAT 7878 SHIPS, MEASURED
-------------------------
620 entries directly in `ini/`: 620 files, of which 200 are `.dat` and
**0 are `.dbc`**. 191,168 files on disk. Archives are **four DatPkg pairs**,
not WDF -- `c3` 59,964 entries, `data` 86,149, plus the patch overlays `c31`
82 and `data1` 1 (`core/tpd.py`, `C-2026-08-10-parser-tpd-grammar`).

The plaintext chain is present and complete, which is what puts it in this
family by the doc's positive test -- `npc.ini`, `3DSimpleObj.ini`,
`3dobj.ini`, `3dtexture.ini`, `3dmotion.ini`, all text, no compiled twin.

THE SHORTFALL, AND IT IS THE WHOLE POINT OF THIS FILE
------------------------------------------------------
**The four art lookup tables are byte-identical (sha256) to 6090's and
6609's.** The same 2008-era files:

    ini/3DSimpleObj.ini   f34f56332383      ini/3dtexture.ini   6be2fece83fd
    ini/3dobj.ini         9202de93aa03      ini/3dmotion.ini    9951727aa1ed

On 6090 and 6609 those are the stale decoys `core/dbc.py` warns about, and the
compiled twin carries the truth. **Here there is no twin, and no `.dat`
equivalent either** -- checked across all 200 `.dat` files: no
`3DSimpleObj.dat`, `3DObj.dat`, `3DTexture.dat`, `3dmotion.dat`. The frozen
table is all there is, while `npc.ini` went on growing:

    base    npc.ini rows   resolved via plaintext   unresolved   compiled twin
    6090         2,277         1,828  (80.3%)            449       present
    6609         2,784         2,031  (73.0%)            753       present
    7878         4,087         2,260  (55.3%)          1,827       ABSENT

(`npc.ini` `SimpleObjID=N` into `3DSimpleObj.ini` `[ObjIDType<N>]`. The 6090
row reproduces `core/npcart.py`'s recorded 1,815/2,264 to within 13 rows of
counting difference, which is what makes the 7878 row worth reading.)

**That is `docs/parser_plugins.md`'s 5165 trap -- "an under-reporting table
with no shadow behind it, where the filesystem is the only authority" -- at
1,827 rows instead of 204.** Nothing raises for any of them: an npc whose
`SimpleObjID` the frozen table never heard of is indistinguishable from an npc
with no art.

LIVE AND FROZEN IN THE SAME FAMILY, IN THE SAME `ini/`
-------------------------------------------------------
`plaintext.py` says of its three members that the plaintext tables are the
LIVE ones. **On 7878 that is true of some and false of others**, so the
family's declaration must not be read as covering this client's whole `ini/`:

    ini/npc.ini          376,991 -> 900,246 bytes across 6090..7878   MAINTAINED
    ini/EmotionIco.ini       712 -> 1,955                             MAINTAINED
    the four lookups     byte-identical since 2008                    ABANDONED

Liveness is per table here, not per family.

WHAT THIS PLUGIN DOES NOT CLAIM
-------------------------------
* **No colour or name data.** Inherited from the family, which claims none.
  Nobody has looked at 7878 on screen.
* **No entity-table completeness.** See the shortfall. Art resolution through
  the archives is sound; the npc chain is 55.3% and declared.
* **No column names for items beyond three.** See `ITEM_COLUMNS`.

THE `.dat` TABLES ARE READABLE NOW, FROM DERIVED DATA
------------------------------------------------------
This section used to say the encrypted tables were unread and the cipher was
not ours. **That is superseded**: 153 of 155 were decrypted by running the
client's own `ndac.dll` as an oracle, and they live in
``<ConquerAssets>/derived/7878-dat-decrypted/``. The algorithm is still
unrecovered, which matters only for *writing* tables back.

**The derived tree is not a convenience -- it is the only route.** Asked of
`core/inidat.py`, all four subjects classify `block96` in the shipped client:

    Monster.dat  block96      itemtype.dat     block96
    mounttype    block96      ItemtypeSub.dat  block96

`block96` is the 12-byte cipher whose key is unknown and not recoverable by
search, and `inidat` can read only its tail. So a client file is genuinely
unreadable here, and `derived_tables() -> None` means **"this subject is
unanswerable on this box"** rather than "we did not look" -- which is why the
refusals name the missing directory and the `$CO_7878_DAT` override instead of
returning an empty catalog.

**The derived tree is read-only and is never copied into `Clients/7878`.** An
install that gets written to is re-keyed and stops being a control; that has
already happened once, to 5065. `derived_tables()` locates it as a
*relationship* to the install root rather than an absolute path, so nothing
here is machine-specific.

**Two files in that tree are not content**, and neither absence nor extension
says so -- see `NOT_CONTENT`. They are the pair `inidat` over-collects into
`block96`, so the classifier defect and the corpus defect are one thing seen
from two sides.

What that buys, measured by `catalogs()` and checked by
`tests/test_patch7878_catalogs.py`:

    npc.ini        4,084 unique (4,087 present)   plaintext, no derived data
    NpcX.ini       1,320                          plaintext
    terrainnpc.ini   183                          plaintext, GBK
    Monster.dat    5,234 unique (5,271 present)   derived
    mounttype.dat  1,937 unique (1,939 present)   derived
    itemtype.dat  55,420 rows                     derived
    ItemtypeSub    1,279 rows                     derived
    garments         691 = 638 + 53               from column 52

Three things in that table are traps rather than results, and each is
documented where it bites: the **unique/present split** (`read_sections`),
the **encoding** (`TEXT_ENCODING`), and the **column count**
(`ITEM_COLUMNS`).

TEXT ENCODING: `gbk`, MEASURED PER TABLE (owner ruling "fix lineage wide")
------------------------------------------------------------------------
THE MODULE CONSTANT SAID gbk AND THE CLASS DECODED latin1: `Patch7878`
inherited the base `TEXT_ENCODING = "latin1"`, so the curated readers
(`read_sections`/`read_at_rows`, gbk by default) served GBK while every
censused subject (`load_table`, e.g. `terrainnpc`, `txtemotion`) served
latin1 mojibake. The class now declares the measured value. 2026-09-19, branch
`director/comod-gbk-lineage`. INSTRUMENT: every declared subject's
`browse()` labels, plus every `Name=` value of every declared section
table (`Name=:` below), read through THIS plugin with its encoding
replaced by a probe codec: GBK with surrogateescape, so the parse is
GBK's (latin1 splits a line on a 0x85 trail byte and strips a 0xA0 one)
and any byte that is not strict GBK survives as a visible surrogate.
PASS = strict GBK to CJK (U+3400-9FFF), full-width punctuation
(U+3000-303F, U+FF00-FFEF) or GBK pinyin Latin (U+00C0-01DC).
PROBE: strict GBK rejects `81 20`, `ff fe` and a lone `a1`. CONTROL:
7205 by the same instrument, 21 tables with high-byte names, 0 failures.
RESULT on 7878: **0 strict-GBK failures** in 270 subjects/name
sources (0 refused, not read); 17 tables carry CJK.

    table                        fields  high   CJK punct pinyin other FAIL
    3dtexture                      8793     6     0     6      0     0    0
    Name=:Monster.dat              5271    66    66     0      0     0    0
    Name=:magiceffect              1915     4     4     0      0     0    0
    Name=:mount                       1     1     1     0      0     0    0
    Name=:npc                      4087    29     6    23      0     0    0
    Name=:npc:NpcX.ini             1320    30    28     1      1     0    0
    Name=:npc:npc.ini              4087    29     6    23      0     0    0
    Name=:npc:terrainnpc.ini        183    51    48     3      0     0    0
    Name=:npcx                     1320    30    28     1      1     0    0
    Name=:terrainnpc                183    51    48     3      0     0    0
    item                          55418     4     0     1      0     3    0
    monster                        5234    55    55     0      0     0    0
    npc                            4062     6     6     0      0     0    0
    npc:NpcX.ini                   1320    30    28     1      1     0    0
    npc:npc.ini                    4062     6     6     0      0     0    0
    npc:terrainnpc.ini              181    48    48     0      0     0    0
    npcx                           1320    30    28     1      1     0    0
    terrainnpc                      181    48    48     0      0     0    0
    txtemotion                        9     9     9     0      0     0    0

EXAMPLES (hex, then GBK):
    npc:terrainnpc.ini NpcType1: `b3 a4 b5 c6` = '长灯'
    terrainnpc NpcType1: `b3 a4 b5 c6` = '长灯'
    Name=:Monster.dat 207: `d0 e9 bf d5 c1 e9 ca de` = '虚空灵兽'
    Name=:npc:npc.ini 681: `4c 61 6e 74 65 72 6e a1 a1` = 'Lantern\u3000'

`other` is GBK row-A1 general punctuation (U+2019 `a1 af`, U+2026
`a1 ad`) inside English text, e.g. "Texas Hold’em" -- strict GBK and
only sensible as GBK (latin1 reads `¡¯`), so not a failure.
Name fields only. A WHOLE-TABLE strict decode found files that are not
GBK; they carry a per-table codec through `TABLE_ENCODING` (see the class)
and `TableSpec.encoding`, added to the base the same day.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.plaintext import PlaintextFamily          # noqa: E402
from plugins.catalog import (                          # noqa: E402
    Catalog, CONTROL_RAW, KIND_GAMEMAP, gamemap_records, build_catalogs,
    read_sections, read_at_rows, sections_from_text, at_rows_from_text)


# ---------------------------------------------------------------------------
# Catalogs: how many rows, and can I open one
# ---------------------------------------------------------------------------

# `Catalog` now lives in `plugins/catalog.py`, with `read_sections`,
# `read_at_rows` and the positive controls. It was written here because 7878
# needed it first; it was never 7878 machinery, and every build has subjects
# it can enumerate and subjects it cannot. Re-exported above so the names
# this module established keep resolving.
#
# One thing did NOT survive the move unchanged: `Catalog.control_kind`. This
# client's tables are plaintext, so its controls search the file's UNDECODED
# bytes (`CONTROL_RAW`) and the reader is not its own witness. The 5017-6609
# tables are `tq-stream`, where the strongest available control is a second
# independent decode -- that witnesses the PARSE and not the CIPHER. Those are
# different amounts of evidence and the field keeps them apart.

#: **GBK, and the client's own `codepage.ini` disagrees.**
#:
#: 7878 ships `ini/codepage.ini` = ``1256`` (Windows-1256, Arabic). 6609 ships
#: ``0`` and 6090 ships none, which is the case `coassets.parse_ini`'s docstring
#: reasons from when it defaults to latin1. **On this client that reasoning
#: leads to the wrong answer**, and the failure is silent: every candidate
#: decodes 8-bit bytes without raising, so "it decoded" is not evidence.
#:
#: MEASURED on the content instead. `ini/SlotNpc.ini` under GBK:
#:
#:     ;中括号里的数字对应服务器cq_npc表中type为老虎机的id字段
#:
#: -- coherent Chinese saying the bracketed number is the `cq_npc` id whose
#: type is a slot machine, which is what the file is for and names the same
#: table the file names. Under cp1256 the same bytes are ``;ضذہ¨؛إہïµؤت``
#: and under big5 they are characters that are not words. **The control is
#: that the text explains the file, not that it decoded.**
TEXT_ENCODING = "gbk"

#: `ini/codepage.ini` on this client, recorded because it is contradicted.
DECLARED_CODEPAGE = "1256"

#: Held out by the decrypted corpus's own README, and **present on disk** --
#: `levexp.dat.out` sits beside `LevelExp.dat.out` and `rune_levexp.dat.out`,
#: so a directory glob picks it up silently. It decrypts cleanly; that is not
#: the point. It is the only seed-1234 table and has zero shared-block edges
#: with the corpus, so the data cannot decide its key group.
HELD_OUT = {"levexp.dat"}

#: **Present in the derived tree, named `.out`, and NOT CONTENT.** These two
#: did not decrypt: the oracle ran, a file was written, and the bytes are
#: still ciphertext. MEASURED here rather than taken on report:
#:
#:     levexp.dat.out      4,030 B   H=3.45   100.0% printable   <- real text
#:     LevelExp.dat.out      516 B   H=7.60    35.9%             <- failed
#:     ServerPlay.dat.out  6,650 B   H=7.97    37.6%             <- failed
#:     Monster.dat.out 1,748,363 B   H=5.06   100.0% printable   <- real content
#:
#: They are exactly the pair `core/inidat.py` over-collects into `block96`, so
#: **"153 of 155 validated" and "over-collects by 2" are one defect seen from
#: two sides**: classified block96, decrypted *as* block96, and not block96.
#: Quick Fix owns the classifier correction; this list is the reader's guard
#: so nothing here serves them as rows in the meantime.
#:
#: **This is a different refusal from `HELD_OUT` and must stay separate.**
#: `levexp.dat` decrypted and we cannot attribute its key; these did not
#: decrypt at all. Collapsing the two would make "we know what this is and
#: will not use it" indistinguishable from "this is garbage".
#: Tables the `ndac.dll` oracle did NOT decrypt. **The corpus README says
#: "153 of 155 validated" and names TWO. Re-measured 2026-08-28 over all 1,655
#: `.out` files in `derived/7878-dat-decrypted`, SIXTEEN carry the unambiguous
#: ciphertext signature -- Shannon H >= 7.2 with under 45% printable -- and
#: fourteen of them were undeclared.**
#:
#: This confirms `C-2026-08-14-claude-vibeco-content-2` on a corpus that has
#: since grown from 168 `.out` files to 1,655: the finding held for two weeks
#: while the guard did not. The README's own warning is the reason it matters --
#: *"a failed decryption in here looks exactly like a successful one"* -- so a
#: reader trusting this set served fourteen ciphertext tables as rows.
#:
#: The cluster is tight and self-similar: H 7.26-8.00, printable 35.9-40.3%.
#: Nothing sits near the boundary, so the threshold is not doing delicate work.
#: **CORRECTED 2026-08-28 (`docs/dat_residue_was_the_caller_2026-08-28.md`).**
#: The SET above is right and stays: every `.out` listed here is ciphertext and
#: must not be served. **The REASON was wrong for thirteen of the fourteen.**
#: "did not decrypt" was measured over ONE route -- the `ndac.dll` block96
#: oracle that wrote `derived/` -- and read back as though it meant *this
#: repository cannot open this file*. It never did. `core/inidat.py` carries
#: two more routes and they open thirteen of these fourteen:
#:
#:     RSA-2048, published 5517 modulus     10 files -> mysqldump XML
#:     TQ File Cipher, seed 9527             2 files -> INI / GBK text
#:     never encrypted at all                1 file  -> int32 array
#:     STILL UNDECRYPTED                     1 file  -> ServerPlay.dat
#:
#: So each value below now names the route that DOES open the file. The guard
#: is unchanged -- `_derived_file` still refuses the `.out` -- but a reader who
#: hits this list is now pointed at `inidat.rsa_decrypt` / `tqdat.decrypt`
#: instead of being told the table is unreadable.
#:
#: *`weaponactiondata.dat` is one of TWO new TQ-family members found by re-running
#: the family test with a GBK-aware acceptance criterion instead of an ASCII
#: printability one; the other is `WeaponMotionData.dat`, the residue of
#: `docs/m9_weaponmotiondata_residue_2026-08-27.md`. Both were refused by every
#: printability guard in the pipeline because their plaintext is Chinese.*
NOT_CONTENT = {
    # STILL UNDECRYPTED -- the only genuine residue in ini/
    "serverplay.dat": "the ndac oracle's .out is ciphertext -- 6,650 B, and no "
                      "route opens it yet: not RSA at any alignment, not TQ at "
                      "seed 9527 or 1234, block96-candidate-refuted",
    # NOT ENCRYPTED AT ALL -- read the raw file, not the .out
    "levelexp.dat": "the .out is ciphertext, but the RAW file is PLAIN: 129 "
                    "little-endian int32. Read <client>/ini/LevelExp.dat "
                    "directly; do not decrypt it",
    # TQ File Cipher, seed 9527 -- core/tqdat.py decrypt()
    "userhelpinfo.ini.dat": "the .out is ciphertext; the file opens with "
                            "tqdat.decrypt(data, 9527) -> 867-entry help INI. "
                            "A DIFFERENT REVISION from ini/UserHelpInfo.ini "
                            "beside it (268 differing hunks), not a duplicate",
    "weaponactiondata.dat": "the .out is ciphertext; opens with "
                            "tqdat.decrypt(data, 9527) -> GBK text, the "
                            "dual-wield weapon-animation key into 3DMotion.ini",
    # RSA-2048 under the published 5517 modulus -- core/inidat.py rsa_decrypt()
    "showhandlayout.dat": "the .out is ciphertext; opens with "
                          "inidat.rsa_decrypt() -> 77,389 B mysqldump XML",
    "showhandlayout800x600.dat": "the .out is ciphertext; opens with "
                                 "inidat.rsa_decrypt() -> 76,933 B XML",
    "showhandtable.dat": "the .out is ciphertext; opens with "
                         "inidat.rsa_decrypt() -> 249,876 B XML",
    "shlayout.dat": "the .out is ciphertext; opens with inidat.rsa_decrypt() "
                    "-> 61,417 B XML",
    "shlayout800x600.dat": "the .out is ciphertext; opens with "
                           "inidat.rsa_decrypt() -> 60,839 B XML",
    "racetrackprop.dat": "the .out is ciphertext; opens with "
                         "inidat.rsa_decrypt() -> 14,250 B XML",
    "suittype.dat": "the .out is ciphertext; opens with inidat.rsa_decrypt() "
                    "-> 41,576 B XML",
    "myanimate.dat": "the .out is ciphertext; opens with inidat.rsa_decrypt() "
                     "-> 6,901 B XML",
    "myanimate800x600.dat": "the .out is ciphertext; opens with "
                            "inidat.rsa_decrypt() -> 6,724 B XML",
    "showhandtablerace.dat": "the .out is ciphertext; opens with "
                             "inidat.rsa_decrypt() -> 6,914 B XML",
}

#: The thirteen of `NOT_CONTENT` that a caller CAN open, and how. Keyed the same
#: way, so `NOT_CONTENT.keys() - OPENS_VIA.keys()` is exactly the real residue.
#: Checked by `tests/test_patch7878_residue_routes.py` against the client on
#: disk, so this cannot drift from the files the way the old reason strings did.
OPENS_VIA = {
    "levelexp.dat": "plain",
    "userhelpinfo.ini.dat": "tq9527",
    "weaponactiondata.dat": "tq9527",
    "showhandlayout.dat": "rsa",
    "showhandlayout800x600.dat": "rsa",
    "showhandtable.dat": "rsa",
    "shlayout.dat": "rsa",
    "shlayout800x600.dat": "rsa",
    "racetrackprop.dat": "rsa",
    "suittype.dat": "rsa",
    "myanimate.dat": "rsa",
    "myanimate800x600.dat": "rsa",
    "showhandtablerace.dat": "rsa",
}

#: The item columns this project can justify, and **only** those.
#:
#: 7878's rows carry **68** fields. `tqdat.FIELDS_AT` carries **59** names, and
#: `tqdat.parse_itemtype` applies them to this file **without raising**: on a
#: Garment row it reports ``itemType='You~can~wear~it~over~your~armor.'``,
#: ``elemResEarth='Garment'`` and ``description='8'``. Every column past the
#: divergence is mis-named, silently, which is this project's signature
#: failure. So 7878 rows are read POSITIONALLY here and the other 65 columns
#: are left unnamed rather than borrowed from a narrower client.
ITEM_COLUMNS = {"id": 0, "name": 1, "itemClass": 52}

#: Where the decrypted tables live, as a RELATIONSHIP rather than a path.
#: `ConquerAssets/Clients/7878` -> `ConquerAssets/derived/7878-dat-decrypted`.
#: `CO_7878_DAT` overrides it. **Derived data is read-only and is never copied
#: back into `Clients/`** -- an install that gets written to is re-keyed and
#: stops being a control, which has already happened once, to 5065.
DERIVED_DIRNAME = "7878-dat-decrypted"


def derived_tables(root: Path) -> Optional[Path]:
    """The decrypted `ini/*.dat` tree for this install, or None."""
    env = os.environ.get("CO_7878_DAT")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    p = Path(root).resolve().parent.parent / "derived" / DERIVED_DIRNAME
    return p if p.is_dir() else None


def _derived_file(base: Optional[Path], name: str) -> Optional[Path]:
    """`Monster.dat` -> the `.out` beside it, or None for the two refusals.

    Refuses `HELD_OUT` (decrypted, key group unattributable) and `NOT_CONTENT`
    (the `ndac.dll` oracle's `.out` is ciphertext -- which is NOT the same as
    the table being unreadable: see `OPENS_VIA`, which names another route for
    thirteen of the fourteen). Both are present on disk with a `.out` suffix,
    so neither absence nor extension distinguishes them from real tables.
    """
    if base is None or name.lower() in HELD_OUT or name.lower() in NOT_CONTENT:
        return None
    want = {name.lower(), name.lower() + ".out"}
    for p in base.iterdir():
        if p.is_file() and p.name.lower() in want:
            return p
    return None


def _why_no_derived(base: Optional[Path], fname: str) -> str:
    """A refusal that says which of the three things went wrong."""
    if base is None:
        return (f"{fname}: the decrypted tables are not on this box. Expected "
                f"<ConquerAssets>/derived/{DERIVED_DIRNAME}/ beside Clients/, "
                f"or $CO_7878_DAT. This client ships it encrypted; nothing is "
                f"copied into Clients/, which stays vanilla.")
    if fname.lower() in HELD_OUT:
        return (f"{fname}: held out by the corpus README -- the only "
                f"seed-1234 table, with zero shared-block edges, so the data "
                f"cannot decide its key group. It decrypts; that is not the "
                f"same as being attributable.")
    if fname.lower() in NOT_CONTENT:
        route = OPENS_VIA.get(fname.lower())
        how = {
            "rsa": "core.inidat.rsa_decrypt(raw_bytes)",
            "tq9527": "core.tqdat.decrypt(raw_bytes, 9527)",
            "plain": "a plain read of the raw bytes -- it was never encrypted",
        }.get(route)
        tail = (f" THE TABLE IS READABLE, just not from `derived/`: open "
                f"<client>/ini/{fname} with {how}."
                if how else
                " No route opens this one yet; it is the real residue.")
        return (f"{fname}: {NOT_CONTENT[fname.lower()]}. A `.out` file was "
                f"written, so it looks like every other decrypted table; those "
                f"bytes are still ciphertext. This is NOT the held-out case "
                f"-- that one decrypted and cannot be attributed.{tail}")
    return f"{fname}: not present in {base}"


def _control_section(path: Path, secs: dict) -> Optional[str]:
    """Open one section and check it against a fresh read of the raw bytes.

    The point is that the reader is not its own witness: the section header
    and one `key=value` are searched for in the undecoded file, so a control
    can only pass if the bytes really carry what the parse claims.
    """
    if not secs:
        return None
    name = next(iter(secs))
    body = secs[name]
    if not body:
        return f"[{name}] (no keys)"
    key = next(iter(body))
    val = body[key]
    raw = path.read_bytes()
    header = f"[{name}]".encode(TEXT_ENCODING, "replace")
    pair = f"{key}={val}".encode(TEXT_ENCODING, "replace")
    if header not in raw or pair not in raw:
        return None
    return f"[{name}] {key}={val} -- both found in the raw bytes"


def _control_at_row(path: Path, rows: list) -> Optional[str]:
    """Open one row and find its id and name back in the raw bytes."""
    if not rows:
        return None
    r = rows[0]
    i_id, i_name = ITEM_COLUMNS["id"], ITEM_COLUMNS["name"]
    if len(r) <= i_name:
        return None
    raw = path.read_bytes()
    probe = f"{r[i_id]}@@{r[i_name]}@@".encode(TEXT_ENCODING, "replace")
    if probe not in raw:
        return None
    return (f"id={r[i_id]} name={r[i_name]!r} ({len(r)} fields) "
            f"-- found in the raw bytes")


class Patch7878(PlaintextFamily):
    #: GBK, MEASURED PER TABLE on this install 2026-09-19 (owner ruling
    #: "fix lineage wide"); evidence in the module docstring. Declared
    #: in THIS class body, not only inherited, so each build carries its
    #: own proof (`tests/gbk_lineage.py`).
    TEXT_ENCODING = TEXT_ENCODING
    #: {table: (name fields, high-byte fields, CJK fields, strict-GBK
    #: failures)} -- `Name=:` rows are every `Name=` of a section table.
    GBK_BY_TABLE = {
        '3dtexture': [8793, 6, 0, 0],
        'Name=:Monster.dat': [5271, 66, 66, 0],
        'Name=:magiceffect': [1915, 4, 4, 0],
        'Name=:mount': [1, 1, 1, 0],
        'Name=:npc': [4087, 29, 6, 0],
        'Name=:npc:NpcX.ini': [1320, 30, 28, 0],
        'Name=:npc:npc.ini': [4087, 29, 6, 0],
        'Name=:npc:terrainnpc.ini': [183, 51, 48, 0],
        'Name=:npcx': [1320, 30, 28, 0],
        'Name=:terrainnpc': [183, 51, 48, 0],
        'item': [55418, 4, 0, 0],
        'monster': [5234, 55, 55, 0],
        'npc': [4062, 6, 6, 0],
        'npc:NpcX.ini': [1320, 30, 28, 0],
        'npc:npc.ini': [4062, 6, 6, 0],
        'npc:terrainnpc.ini': [181, 48, 48, 0],
        'npcx': [1320, 30, 28, 0],
        'terrainnpc': [181, 48, 48, 0],
        'txtemotion': [9, 9, 9, 0],
    }

    #: PER-TABLE exceptions to GBK, MEASURED 2026-09-19 on this install by a
    #: strict decode of each declared table's whole text:
    #:   ChatFilter.ini  2 bytes, `ff fe` -- a UTF-16 BOM and NOTHING ELSE.
    #:                   GBK rejects both bytes, so under gbk its one "row"
    #:                   became U+FFFD and lost its control (caught by
    #:                   test_patch7878_catalogs). latin1 keeps the pre-GBK
    #:                   behaviour: one row reading `ÿþ`, which is a
    #:                   BOM counted as a row, NOT content -- recorded, not fixed.
    #:   Questinfo.ini   1,739,385 bytes, strict UTF-8 OK, strict GBK fails
    #:                   at byte 17,213 (12 undecodable), UTF-8 CJK text.
    #:
    #: NINE MORE, strict UTF-8 (strict decode of the whole file, 2026-09-19,
    #: bytes / high bytes / 0x85 bytes / strict GBK):
    #:   Ar_Res.ini                    3,755 /   1,128 /    21 / decodes, but
    #:                                 as GBK garbage; UTF-8 is 564 Arabic
    #:   Cn_Res.ini                3,058,954 /   8,209 /    98 / fails @133,374
    #:   Server_Cn_Res.ini         4,681,540 /  15,291 /    13 / fails @10,476
    #:   OperateActivity.ini          39,873 /  14,253 /   195 / fails @22
    #:   QuestinfoPassionServer.ini  714,950 / 312,634 / 3,809 / fails @228
    #:   TexasChatGUI.ini              6,062 /   1,551 /    23 / fails @2 (BOM)
    #:   TexasChatGUI800X600.ini       6,142 /   1,551 /    23 / fails @2 (BOM)
    #:   Title.ini                     2,086 /      78 /     1 / fails @31
    #:   WrapTypeData.ini             58,706 /     570 /     3 / fails @30
    #: The census could not settle them under latin1 (each 0x85 byte was a
    #: false line under `splitlines`); see tests/test_census_line_split.py.
    TABLE_ENCODING = {
        "chatfilter.ini": "latin1", "questinfo.ini": "utf-8",
        "ar_res.ini": "utf-8", "cn_res.ini": "utf-8",
        "server_cn_res.ini": "utf-8", "operateactivity.ini": "utf-8",
        "questinfopassionserver.ini": "utf-8", "texaschatgui.ini": "utf-8",
        "texaschatgui800x600.ini": "utf-8", "title.ini": "utf-8",
        "wraptypedata.ini": "utf-8",
    }

    #: FOUR-wide action field, like 6090 -- not the family's three.
    #: MEASURED 2026-09-19 over every keyed row of 7878's
    #: `ini/Action3DEffect.ini`: 4,193 of 4,193 have a four-digit action
    #: (shape.action.type.sub widths 3.4.3.3 x2,722, 4.4.3.3 x1,180,
    #: 1.4.3.1 x262, 3.4.1.3 x24, 3.4.1.1 x5); 5017 is 3.3.3.3 on all
    #: 8,685, which is why the family default stays three. The always-on
    #: action code is spelled from this width, so three-wide here looks up
    #: `999` against rows that say `9999` -- the defect C35 recorded on 6090.
    #: The SHAPE field is not a fixed width on 7878 (3, 4 or 1), so it keeps
    #: the family's declared 3; only the action width is a measured fact.
    #: Found by the patch7320 plugin agent.
    FIELD_WIDTHS = {
        "Action3DEffect.ini": {"shape": 3, "action": 4, "type": 3, "sub": 3},
    }

    name = "patch7878"
    label = "Official patch client 7878"
    origin = "official"
    aliases = ("7878",)
    STAMP = "7878"
    notes = (
        "Ships no compiled .dbc, so it passes the plaintext family's positive "
        "test -- but its FIVE art lookup tables are byte-identical to 6090's "
        "frozen 2008 decoys and there is no compiled twin behind them and no "
        ".dat equivalent. 1,827 of its 4,087 npc.ini rows (44.7%) cannot "
        "resolve, and of the rest only 998 have geometry the archives ship, "
        "so 3,086 of 4,084 rows draw nothing and nothing raises. Archives are "
        "four DatPkg pairs, not WDF. The ini/*.dat tables ARE readable: 153 "
        "of 155 were decrypted through the client's own ndac.dll and live in "
        "<ConquerAssets>/derived/7878-dat-decrypted/, which this plugin reads "
        "and never writes. Art resolution through the archives is sound; "
        "entity resolution is not, and the number is declared rather than "
        "discovered later."
    )

    #: The lookup tables that stopped being maintained, with the sha256 prefix
    #: they have carried since 2008. Byte-identical on 6090, 6609 and 7878 --
    #: so a diff against either of those bases reports "unchanged" and means
    #: "equally abandoned", not "still correct".
    FROZEN_LOOKUPS = {
        "ini/3DSimpleObj.ini": "f34f56332383",
        "ini/3dobj.ini": "9202de93aa03",
        "ini/3dtexture.ini": "6be2fece83fd",
        "ini/3dmotion.ini": "9951727aa1ed",
        # A FIFTH, and it was missing from this list until 2026-08-12.
        # 108,283 bytes, sha256 identical on 5517/6090/6609/7878. Its cost is
        # the same shape as the others and larger than most: `armor.ini`
        # carries 213 body prefixes against **367** `c3/monster/<N>/`
        # directories in 7878's DatPkg index, so 173 (47%) are unreachable and
        # report as "not a row in this install's armor.ini" -- which reads
        # like a data error rather than a table that stopped being written.
        "ini/armor.ini": "8e64691e0f00",
    }

    #: MEASURED: npc.ini rows, rows the frozen chain resolves, and the gap.
    #: Declared so a caller can compare what it got against what this client
    #: can possibly give, rather than reading a short answer as a complete one.
    #:
    #: **`resolved` is not `drawable`, and the difference is most of it.**
    #: A row resolves when the frozen chain NAMES geometry for it; the file
    #: then has to be in the archives. Measured with `npcart.audit`, which is
    #: the project's own resolver rather than a path built by hand:
    #:
    #:                  npc rows   geometry NAMED   geometry SHIPPED
    #:     5517            1,108            1,108            1,107   99.9%
    #:     7878            4,084            2,246              998   24.4%
    #:
    #: So **3,086 of 4,084 NPC rows draw nothing**, and nothing raises for any
    #: of them. `rows`/`resolved`/`unresolved` below are section counts from
    #: this plugin's own reader (4,087 headers, 4,084 unique -- see
    #: `read_sections`); `named`/`shipped` are `npcart.audit`'s, which counts
    #: unique rows. The two instruments are named because they differ by the
    #: three duplicate ids and that difference is not an error in either.
    #: `rows`/`resolved`/`unresolved` are section counts from `read_sections`
    #: (4,087 headers, 4,084 unique). `geometry_*` are `npcart.audit`'s, which
    #: counts the 4,084 unique rows -- hence `geometry_rows`, carried
    #: explicitly so a caller cannot subtract 998 from 4,087 and print a
    #: number neither instrument measured. That is exactly what the first
    #: draft of `comod catalogs` did.
    NPC_COVERAGE = {"rows": 4087, "resolved": 2260, "unresolved": 1827,
                    "geometry_rows": 4084,
                    "geometry_named": 2246, "geometry_shipped": 998}

    #: DatPkg, not WDF. The overlays carry a free list; see tpd.py.
    ARCHIVE_PAIRS = ("c3", "data", "c31", "data1")

    #: The NPC family, all plaintext in the shipped client -- no decrypted
    #: table is needed for any of them. `terrainnpc.ini` and `SlotNpc.ini`
    #: carry GBK text; `npc.ini` does too, in 34 of its rows.
    NPC_FILES = ("npc.ini", "NpcX.ini", "terrainnpc.ini", "npcex.ini",
                 "SlotNpc.ini")

    # -- catalogs ----------------------------------------------------------
    def catalogs(self, root) -> dict:
        """Per subject: how many rows, and a row actually opened.

        Every entry either carries a `control` -- a named row this reader
        opened and checked back against the raw bytes -- or a `refusal` saying
        what was missing. **Nothing returns an empty collection silently**:
        "this client has none" and "we could not read it" are different
        answers and only one of them is about the client.
        """
        root = Path(root)
        ini = root / "ini"
        der = derived_tables(root)
        out: dict = {}

        # -- NPC: plaintext, largest, no derived data involved -------------
        for name in self.NPC_FILES:
            p = ini / name
            subject = f"npc:{name}"
            if not p.is_file():
                out[subject] = Catalog(subject, f"ini/{name}", "ini-sections",
                                       refusal=f"ini/{name} is not present")
                continue
            try:
                secs, dupes = read_sections(p)
            except OSError as e:
                out[subject] = Catalog(subject, f"ini/{name}", "ini-sections",
                                       refusal=f"unreadable: {e}")
                continue
            out[subject] = Catalog(subject, f"ini/{name}", "ini-sections",
                                   rows=len(secs), duplicated=dupes,
                                   control=_control_section(p, secs),
                                   control_kind=CONTROL_RAW)

        # -- Monster and mount: same INI shape, one reader -----------------
        for subject, fname in (("monster", "Monster.dat"),
                               ("mount", "mounttype.dat")):
            p = _derived_file(der, fname)
            if p is None:
                out[subject] = Catalog(
                    subject, fname, "ini-sections",
                    refusal=_why_no_derived(der, fname))
                continue
            secs, dupes = read_sections(p)
            out[subject] = Catalog(subject, fname, "ini-sections",
                                   rows=len(secs), duplicated=dupes,
                                   control=_control_section(p, secs),
                                   control_kind=CONTROL_RAW)

        # -- items, and the garment view that falls out of them ------------
        item_rows: list = []
        for subject, fname in (("item", "itemtype.dat"),
                               ("item:sub", "ItemtypeSub.dat")):
            p = _derived_file(der, fname)
            if p is None:
                out[subject] = Catalog(subject, fname, "at-rows",
                                       refusal=_why_no_derived(der, fname))
                continue
            rows = read_at_rows(p)
            item_rows += rows
            out[subject] = Catalog(subject, fname, "at-rows", rows=len(rows),
                                   control=_control_at_row(p, rows),
                                   control_kind=CONTROL_RAW)

        # -- the one binary table whose grammar IS established, and it is
        # the only content table that reads identically on all seven builds.
        # 7878's `ini/` is otherwise 154 block96 files, so this and Action.dat
        # are the only two things it ships that any build can open the same
        # way -- worth having here rather than only on the older plugins.
        gm = ini / "GameMap.dat"
        if gm.is_file():
            raw = gm.read_bytes()
            try:
                maps, note = gamemap_records(raw)
                out["gamemap"] = Catalog(
                    "gamemap", "ini/GameMap.dat", KIND_GAMEMAP, rows=len(maps),
                    control=(f"{len(maps)} records, declared count matches and "
                             f"the parse consumes all {len(raw)} bytes "
                             f"exactly; id={maps[0][0]} path={maps[0][1]!r}; "
                             f"{note}"),
                    control_kind=CONTROL_RAW)
            except ValueError as e:
                out["gamemap"] = Catalog("gamemap", "ini/GameMap.dat",
                                         KIND_GAMEMAP, refusal=str(e))
        else:
            out["gamemap"] = Catalog("gamemap", "ini/GameMap.dat",
                                     KIND_GAMEMAP,
                                     refusal="ini/GameMap.dat is not present")

        cls_i = ITEM_COLUMNS["itemClass"]
        readable = [c for c in (out.get("item"), out.get("item:sub")) if c]
        if item_rows and all(c.ok for c in readable):
            garments = [r for r in item_rows
                        if len(r) > cls_i and r[cls_i].strip() == "Garment"]
            g = garments[0] if garments else None
            out["garment"] = Catalog(
                "garment", "itemtype.dat + ItemtypeSub.dat", "at-rows",
                rows=len(garments),
                control=(f"id={g[ITEM_COLUMNS['id']]} "
                         f"name={g[ITEM_COLUMNS['name']]!r} "
                         f"col{cls_i}='Garment'" if g else None),
                control_kind=CONTROL_RAW if g else None,
                refusal=None if g else "no row carries the Garment class")
        else:
            out["garment"] = Catalog(
                "garment", "itemtype.dat + ItemtypeSub.dat", "at-rows",
                refusal="derived from the item tables, which did not read")

        # -- the censused `.ini`, added UNDER the curated ones --------------
        #
        # This build's `catalogs` is bespoke because its content arrives
        # through the derived corpus rather than through `table_specs`, so the
        # base class's spec-driven path never runs here -- and the censused
        # tables would stay invisible on the ONE build with the largest gap:
        # 216 `.ini` present in `ini/`, none declared before this.
        #
        # `setdefault`, so a curated entry always wins, INCLUDING its
        # refusals. A refusal is an answer about the client; replacing one
        # with a generated row count would be a downgrade dressed as coverage.
        from .catalog import censused as _cens
        for subject, cat in build_catalogs(
                self.apply_table_encoding(_cens.specs_for(self.name)),
                lambda spec: self.load_table(spec, root),
                encoding=self.TEXT_ENCODING,
                columns=self.ITEM_COLUMNS).items():
            out.setdefault(subject, cat)
        return out

    #: **The monster row -> art link is NOT established on this client**, and
    #: the obvious candidates are measured dead ends rather than untried:
    #:
    #:   * `BodyType` is **0 on all 5,234 rows**. It is not a link here; it is
    #:     a column that is uniformly zero, and reading it as an id gives every
    #:     monster the same answer.
    #:   * The row id is not the directory either: **1 of 5,234** row ids
    #:     matches one of the 371 `c3/monster/<N>/` directories in the archive
    #:     index. Row ids are 4-digit zero-padded (`0001`); directories are
    #:     bare (`103`).
    #:
    #: So `browse monster` lists the table and stops there, and says so. A
    #: Note pointing at `show <BodyType>` was written before this was measured
    #: and would have sent every caller to the same wrong place.
    MONSTER_ART_LINK = None

    #: **The appearance -> art chain resolves NOTHING on this client**, and
    #: that is the client rather than the reader. MEASURED, with the control
    #: run first because a zero everywhere is what a broken instrument also
    #: produces:
    #:
    #:     6090   201 appearances sampled, 160 fully resolve
    #:     5517   201 sampled, 160 resolve          <- the instrument can say yes
    #:     7878   421 sampled across all seven part tables, **0** resolve
    #:
    #: Every part table is affected -- armet, body, l_weapon, r_weapon,
    #: mix_armet, mix_body, mount. The cause is the frozen lookups: 7878's
    #: `armor.ini` and friends are the 2008 files (see `FROZEN_LOOKUPS`) and
    #: name mesh and texture ids this client's archives do not ship.
    #:
    #: So `comod show <id>` prints `NOT FOUND` for both halves of every row it
    #: resolves, which is honest and is not a defect to chase. **What works
    #: instead**: the catalogs browse, and any asset reads by logical path --
    #: the archives hold 146,194 entries and `AssetRoot.read` returns them.
    #: A caller wanting art on 7878 addresses it by path, not by appearance.
    APPEARANCE_ART_RESOLVES = False

    #: The label column, per subject, because they do not agree.
    #:
    #: **`mounttype.dat` has no name.** MEASURED: all 1,937 rows carry exactly
    #: `Color1/2/3`, `armor`, `Showrate`, `Offset`, `Offset4Armor`, `Sound1`
    #: and nothing else. So a mount's identity is its id, and the useful second
    #: column is `armor` -- the appearance it wears, which is the thing a
    #: modder goes on to `show`. Labelling it "" because a `Name` key was
    #: assumed would have printed 1,937 blank cells and read as a bug.
    #: MEASURED per table, because these do not agree and the default is
    #: wrong for four of them:
    #:
    #:   mount              1,937 rows, no `Name` at all -- `armor` is the
    #:                      appearance it wears, which is what a modder goes
    #:                      on to `show`.
    #:   npc:npcex.ini      7 sections keyed `NpcType113`; carries `Amount`
    #:                      and `Var0/Look0` pairs, no `Name`.
    #:   npc:SlotNpc.ini    93 sections keyed by a bare id; carries `Data0..2`
    #:                      and no name of any kind.
    #:
    #: The first was found by looking at the output. The other two were found
    #: by a test asserting no subject prints a column of blanks, added after
    #: the same defect turned up on six more builds.
    ROW_LABEL_KEY = {"mount": "armor",
                     "npc:npcex.ini": "Amount",
                     "npc:SlotNpc.ini": "Data0"}
    ROW_LABEL_DEFAULT = "Name"

    def _label_key(self, subject: str) -> str:
        return self.ROW_LABEL_KEY.get(subject, self.ROW_LABEL_DEFAULT)

    def table_specs(self, root):
        """The censused `.ini` only.

        This build's CURATED content arrives through the derived corpus and is
        assembled by `catalogs()` by hand, so there is nothing hand-written to
        return here -- but the 154 `.ini` the grammar census settled are
        ordinary spec-driven tables, and declaring them is what lets the base
        class's readers and their measured label keys apply to them.
        """
        from .catalog import censused as _cens
        return _cens.specs_for(self.name)

    def _shadowed_by_catalog(self, spec, root) -> bool:
        """Does a curated catalog claim this subject from a DIFFERENT file?

        The censused sweep claims every `.ini` it can parse, vestigial ones
        included. `mount` was claimed from `ini/mount.ini` -- a 49-byte stub
        dated January 2003 with one section -- while `catalogs()` reported
        1,937 rows from `mounttype.dat`. So `comod catalogs` told the user
        1,937 and `comod browse mount` showed them 1, and neither output
        hinted that the two had read different files.

        THE DISCRIMINATOR IS THE FILENAME, and it had to be measured rather
        than guessed. My first attempt tested `spec.source` for emptiness:
        ALL 154 censused specs have an empty source, so it separated nothing
        and made every browse pay an uncached 0.7 s `catalogs()` call -- the
        agreement test went from 0.97 s to a timeout. Comparing
        `spec.filename` against `catalog.source` separates exactly one
        subject from 153, which is the shape a discriminator should have.

        `catalogs()` is not cached upstream and costs ~0.7 s, so it is
        memoised per root here; browse is called once per subject listed.
        """
        # Lazily, rather than in __init__: this class is constructed by a
        # registry and adding a constructor requirement would be a change
        # to how every plugin is built, for a cache.
        if getattr(self, "_cat_cache", None) is None:
            self._cat_cache = {}
        cats = self._cat_cache.get(str(root))
        if cats is None:
            cats = self.catalogs(root)
            self._cat_cache[str(root)] = cats
        cat = cats.get(spec.subject)
        if cat is None or not getattr(cat, "ok", False):
            return False
        fn = (getattr(spec, "filename", "") or "").lower().rsplit("/", 1)[-1]
        src = (getattr(cat, "source", "") or "").lower().rsplit("/", 1)[-1]
        return bool(fn and src and fn != src)

    def browse(self, subject: str, root, query: str = "",
               limit: int = 0) -> tuple:
        """`(rows, total, refusal)` -- `(id, name)` pairs for one subject.

        The listing half of `catalogs()`. Returns the refusal rather than an
        empty list when the subject cannot be read, so a caller can tell "this
        client has none" from "we could not look" without asking twice.
        """
        # A CENSUSED subject goes to the base implementation, which is the
        # only one that knows about the spec's MEASURED `label_key` and about
        # the flat-key and bare-list grammars.
        #
        # This override predates all of that: it labels every sections table
        # with `self._label_key(subject)`, the plugin-wide default, so
        # `GlobalLotteryCondition.ini` -- whose label was measured as `Desc`,
        # present on 109 of its 110 sections -- listed 110 blank labels. It
        # also reads with `read_sections`, which takes a `key=value` preamble
        # line for a section id (`ConditionAmount` appeared as a row).
        #
        # The curated subjects stay on the path below: their sources are
        # derived-corpus files that the base class cannot locate.
        # ...UNLESS a curated catalog claims the same subject and names a
        # source the censused spec does not. The censused sweep claims every
        # `.ini` it can parse, INCLUDING VESTIGIAL ONES: `mount` was claimed
        # from `ini/mount.ini`, a 49-byte stub dated January 2003 with one
        # section, while `catalogs()` reported 1,937 rows from
        # `mounttype.dat`. So `comod catalogs` told the user 1,937 and
        # `comod browse mount` showed them 1, and neither output hinted that
        # the two had read different files.
        #
        # The test is the spec having NO source of its own: a censused entry
        # that names nothing cannot outrank a curated one that names a file.
        # MEASURED on 7878 before changing this -- 154 censused subjects, 164
        # catalog subjects, all 154 in both, and exactly ONE disagreed. The
        # blast radius of this precedence is that one subject.
        spec = next((s for s in self.table_specs(root)
                     if s.subject == subject), None)
        if spec is not None and not self._shadowed_by_catalog(spec, root):
            return super().browse(subject, root, query, limit)

        cat = self.catalogs(root).get(subject)
        if cat is None:
            return [], 0, f"no catalog called {subject!r}"
        if not cat.ok:
            return [], 0, cat.refusal or "unreadable"
        root = Path(root)
        q = (query or "").lower()
        out: list = []

        if cat.kind == KIND_GAMEMAP:
            gm = root / "ini" / "GameMap.dat"
            if not gm.is_file():
                return [], 0, "ini/GameMap.dat is not present"
            try:
                recs, _note = gamemap_records(gm.read_bytes())
            except ValueError as e:
                return [], 0, f"ini/GameMap.dat: {e}"
            for ident, path in recs:
                if not q or q in path.lower() or q in ident.lower():
                    out.append((ident, path))
        elif cat.kind == "ini-sections":
            if cat.source.startswith("ini/"):
                p = root / cat.source
            else:
                p = _derived_file(derived_tables(root), cat.source)
            if p is None:
                return [], 0, cat.refusal or f"{cat.source} is unreadable"
            secs, _dupes = read_sections(p)
            key = self._label_key(subject)
            for ident, body in secs.items():
                name = body.get(key, "")
                if not q or q in name.lower() or q in ident.lower():
                    out.append((ident, name))
        else:
            i_id, i_name = ITEM_COLUMNS["id"], ITEM_COLUMNS["name"]
            cls_i = ITEM_COLUMNS["itemClass"]
            want_garment = subject == "garment"
            names = ("itemtype.dat", "ItemtypeSub.dat") if want_garment \
                else (cat.source,)
            opened = 0
            for fname in names:
                p = _derived_file(derived_tables(root), fname)
                if p is None:
                    continue
                opened += 1
                for r in read_at_rows(p):
                    if len(r) <= i_name:
                        continue
                    if want_garment and not (len(r) > cls_i
                                             and r[cls_i].strip() == "Garment"):
                        continue
                    if not q or q in r[i_name].lower() or q in r[i_id].lower():
                        out.append((r[i_id], r[i_name]))
            if not opened:
                # This branch is the fallback for every kind it does not
                # recognise, and it USED to return `([], 0, None)` -- a blank
                # with no refusal, which is the single outcome this whole
                # surface exists to prevent. It fired for real: `gamemap`
                # reached here, matched no derived filename, and reported
                # nothing at all rather than saying it had not looked.
                return [], 0, (f"{subject}: {cat.kind!r} has no reader in "
                               f"browse() on this client, and no derived table "
                               f"named {cat.source!r} was opened")

        total = len(out)
        return (out[:limit] if limit else out), total, None

    def open_row(self, subject: str, root, ident: str) -> Optional[dict]:
        """One row by id, or None. The "can I open one" half, for a caller."""
        cat = self.catalogs(root).get(subject)
        if cat is None or not cat.ok:
            return None
        root = Path(root)
        if cat.kind == "ini-sections":
            p = (root / "ini" / cat.source.split("/", 1)[-1]
                 if cat.source.startswith("ini/")
                 else _derived_file(derived_tables(root), cat.source))
            return read_sections(p)[0].get(ident) if p else None
        p = _derived_file(derived_tables(root), cat.source.split(" ")[0])
        if p is None:
            return None
        for r in read_at_rows(p):
            if r and r[0] == ident:
                return {k: r[i] for k, i in ITEM_COLUMNS.items() if len(r) > i}
        return None

    def table_quirks(self):
        """The family's quirks, plus the three that are 7878's own.

        These are surfaced through `/api/plugins`, which is what makes the
        shortfall a channel with a reader rather than a comment. `PartIni`'s
        `self.source` is the counter-example: written in both branches,
        **read by nothing**, and therefore not audibility at all.
        """
        q = super().table_quirks()
        q["the plaintext lookup tables are frozen, not live"] = (
            "3DSimpleObj.ini, 3dobj.ini, 3dtexture.ini and 3dmotion.ini are "
            "byte-identical (sha256) to 6090's and 6609's -- the same 2008 "
            "files. There they are stale decoys with a compiled twin behind "
            "them; here there is no twin and no .dat equivalent. The family's "
            "'plaintext tables are the LIVE ones' holds for npc.ini, which "
            "grew to 900,246 bytes, and NOT for these four.")
        q["44.7% of npc.ini cannot resolve, silently"] = (
            "npc.ini has 4,087 rows; the frozen 3DSimpleObj.ini resolves "
            "2,260 of them (55.3%) and 1,827 miss. 6090 resolves 80.3% of "
            "2,277 and 6609 73.0% of 2,784 -- both with a compiled twin to "
            "fall back to. An unresolved row is indistinguishable from an npc "
            "with no art, so a short answer here is the expected answer, not "
            "a fault to chase.")
        q["ini/*.dat is encrypted with a cipher that is not ours"] = (
            "200 .dat tables. The TQ File Cipher is ruled out across the "
            "whole 2^32 LCG seed space (16 printable bytes required, zero "
            "survivors) while the same code opens 6090's and 6609's tables at "
            "100%. Also not an 8-byte block cipher and not a period-128 "
            "keystream. tqdat must not be pointed at them: it will not raise, "
            "it will return noise.")
        return q


PLUGIN = Patch7878()
