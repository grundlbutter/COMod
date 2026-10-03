#!/usr/bin/env python3
r"""
dbc.py -- readers for the compiled ``ini/*.dbc`` tables of official clients.

Patch-6090-era clients ship the entity tables twice: the plaintext ``.ini``
files everyone knows, and a compiled ``.dbc`` twin.  **The `.ini` twins are
stale decoys** -- in the 6090 install they are stamped 2009 while the `.dbc`
files are stamped 2015, and the two disagree; the client reads the `.dbc`.
A parser that reaches for `3dobj.ini` because it exists is reading data six
years out of date.

Each compiled table opens with a four-byte magic naming its own layout:

    RSDB    id -> path rows          3DObj, 3DTexture, 3DEffectobj, 3dmotion,
                                     miscmotion, mountmotion, weaponmotion
    SIMO    simple-object records    3DSimpleObj
    MESH    appearance -> parts      armor, armet, weapon, misc, mount
    EFFE    effect definitions       3DEffect
    EMOI    emotion-icon names       EmotionIco

That list is exhaustive for the 15 ``.dbc`` files 6090 ships: every one opens
with one of these five magics.

**A missing ``.dbc`` is normal, not an error.** Compiled twins first appear
at **5517**; 5017, 5065, 5165 and CCO ship none at all, and on those clients
the plaintext ini *is* the live table -- the exact inverse of the 6090 trap
above. ``EmotionIco.dbc`` narrows it further: it exists **only on 6090**,
while 5517 ships the plaintext ``EmotionIco.ini`` alone. Callers pick the
form the install actually has (`npcart.detect_profile` is the pattern);
nothing here should be reached for unconditionally.

``RSDB`` is the same row-table format ``wdb.py`` documents inside
``ini/c3.wdb`` -- here it stands alone as a whole file (VERIFIED against the
6090 install: 3DObj 3,023 rows, 3DTexture 18,995, 3DEffectobj 9,539,
3dmotion 568,584; every string offset lands inside the file, zero bad rows):

    0x00  char[4]  "RSDB"
    0x04  u32      rowCount
    0x08  ...      rowCount x row
    ...            NUL-terminated latin-1 paths, offsets absolute from file
                   start, packed immediately after the row table

The row is ``{u32 id, u32 stringOffset}`` -- except ``3dmotion.dbc``, whose
rows are ``{u32 id, u32 extra, u32 stringOffset}``.  The stride is detected,
not assumed: for the right stride every row's offset lands inside the string
region, and for the wrong one the interleaved fields land outside it.

> **``extra`` IS NAMED NOW, 2026-09-06: it is the HIGH DWORD of a 64-bit id,**
> and the two-column row is the same key with the high half zero.  The client
> reads both shapes into one map in one function, writing the high dword as a
> literal ``0`` on the stride-8 branch; see `wdb.ROW_TABLES` for that read and
> the falsifiable data control, and `py -3 tools/matrloader.py key`.
> This note used to end *"the meaning of ``extra`` is unestablished (0 on role
> motions, 2 on NPC motions, 13634 on some late role rows); it is preserved,
> not interpreted"* -- and the very value that made it look like a category,
> ``2``, is a 10-digit id's top half: ``2 * 2**32 + 1405167408 ==
> 9995102000``, which is the number spelled in that row's own path.
> **A small-integer column read as an enum and the same column read as an
> arithmetic high half look identical until you do the arithmetic.**

**Parsing an ``RSDB`` is not the same as being able to KEY it, and one table
proved the gap.**  ``weaponmotion.dbc`` parses cleanly (130,049 rows at 5517,
169,968 at 6090, every offset in-file) and its ids do not join
``WeaponMotion.ini``'s under identity, the ``& 0xFFFFFFFF`` wrap that works
for ``3dmotion``, or truncation: **0 of 25,944**.

**SOLVED 2026-08-09 -- it was a key WIDTH, and the composition is now
implemented.**  See `weaponmotion_key` / `weaponmotion_join` below.  The ini
spells the action in a **3-digit** decimal field and the twin packs it in a
**4-digit** one, wrapping mod 2**32::

    id = (appearance * 10_000 + action) mod 2**32

Verified on 5517 at **25,944 of 25,944, 0 wrong, 0 absent**, with every
alternative width scoring **zero** on both compiled bases -- a wrong width
here cannot produce a plausible partial match, only total absence, which is
precisely why the old ``str(id)`` keying reported the whole table missing and
raised nothing (CORRECTIONS §2).

**What the join then exposed is a bigger finding than the key.**
``WeaponMotion.ini`` is **byte-identical on all five official clients**
(md5 ``edf77851440297a05dd3fcfb0d797a1e``, 5017 through 6090), while the twin
is not -- so the plaintext is frozen 2009 content and the compiled table has
moved on without it.  Measured coverage of the ini against its own base's
twin:

    5517   25,944 / 25,944 ini rows resolve   but name only  19.9% of the twin
    6090    2,808 / 25,944 ini rows resolve   and name only   1.7% of the twin

So on 6090 the plaintext is a **1.7% view** of the live table, and 2,133 of
the 2,375 appearances it lists are not in the compiled one at all.  Any claim
that 5517 and 6090 "agree" about ``WeaponMotion`` is a statement about one
frozen file, not about the two clients.

``SIMO`` (VERIFIED: 388 records in 6090, walks to exactly EOF):

    0x00  char[4]  "SIMO"
    0x04  u32      recordCount
    then per record:
        u32 id, u32 partAmount, partAmount x { u32 partId, u32 textureId }

Decoded against the stale 2009 ``3DSimpleObj.ini`` as a Rosetta: record 1 is
Part0=9990010 Texture0=9990010, matching ``[ObjIDType1]`` exactly -- down to
the truncated ``Texture0=999005`` on id 5, which both carry, so that value is
authored rather than a typo.  Four records are multi-part (ids 50, 57, 612,
626, two parts each); everything else is PartAmount=1.

**Ids that overflow overflow the way the client overflows them.**  The 6090
``npc.ini`` names ten-digit motion ids (``StandByMotion=9990010100``) that do
not fit in a u32, and ``3dmotion.dbc`` keys them by their low 32 bits:
``9990010100 & 0xFFFFFFFF == 1400075508`` is the row, and it resolves to
``c3/npc/999001100.c3`` -- the *old nine-digit filename*.  Two facts in one:
lookups must wrap to u32 or miss everything, and the motion table is a
renaming shim -- TQ renumbered the ini layer without renaming archive files,
which is consistent with all five official clients sharing ``c3.wdf`` byte
for byte.  Measured over the whole table: 2,165 of 2,232 standby motions
resolve through the wrapped key.

``EFFE`` (VERIFIED: 5517 3,391 records / 8,759 layers, 6090 4,483 records /
13,285 layers, both walking to exactly EOF, every name a clean NUL-terminated
printable string with no bytes after the terminator):

    0x00  char[4]  "EFFE"
    0x04  u32      recordCount
    then per record, a 66-byte header followed by ``amount`` x 16-byte layer:
        char[32] name          NUL-padded, latin-1
        u16      amount        layer count, 0..15 observed
        u32      delay         ms   (Delay)
        u32      loopTime           (LoopTime; >= 99999 means "forever")
        u32      frameInterval ms   (FrameInterval)
        u32      loopInterval  ms   (LoopInterval)
        f32      offsetX, offsetY, offsetZ
        u8       billboard, u8 colorEnable, u16 lev      (see below)
    layer:
        u32 effectId, u32 textureId, f32 scale,
        u8 asb, u8 adb, u8 zBuffer, u8 billboard         (see below)

**``amount`` is a u16, and reading it as a u32 is the trap.** Every record
whose ``Delay`` is 0 parses identically either way -- 4,411 of 6090's 4,483 --
so a u32 read survives 740 records before ``Blood`` (``Delay=500``) turns into
a layer count of 32,768,001. The width is decidable rather than assumed: only
the u16 form walks to exactly EOF on both clients.

Decoded against the 2009 ``3DEffect.ini`` as a Rosetta, the same method
``SIMO`` and ``MESH`` used. On **5517** the compiled table reproduces the
plaintext one almost exactly -- all 2,595 ini sections are present and
**2,590** match on every compared field -- which is what establishes the
field layout. The layer ids are then checked against the *other* compiled
tables, an independent source: 5517 resolves **8,759 of 8,759** effect ids in
``3DEffectobj.dbc`` and 8,759 of 8,759 texture ids in ``3DTexture.dbc``; 6090
resolves 13,276 of 13,285 in each. A misread field offset cannot score that.

A third check on the layer row, found while wiring the reader onto the
definitions path and worth recording because nobody fitted it: the layer's
``scale`` is a **f32 fraction** and the plaintext ``Scale<i>`` is the same
value as an **integer percent** (``red-flower-small``'s ``Scale0=78`` reads
``0.78``). On 5517, where the two tables otherwise agree, ``ini / 100``
matches the compiled float on **5,099 of 5,099** shared layers -- zero
exceptions. On 6090 five disagree and all five are inside the 56 records
``docs/effects.md`` §7a already reports as content edits. A stride or
field-offset error does not produce a clean unit relation across 5,099 rows.

THE LAST SIX BYTES ARE NAMED NOW, BY THE VENDOR'S OWN ini<->dbc CONVERTER
------------------------------------------------------------------------
The four trailing header bytes and the layer's two trailing bytes were
``unk62``/``unk63``/``unk64`` and ``pad`` until 2026-09-05. They are
``Billboard``/``ColorEnable``/``Lev`` and ``ZBuffer``/``Billboard``, and the
evidence is not another table fit -- it is the pair of DLLs that *write* and
*read* this exact file, shipped in the C3Tools authoring kit
(``docs/effe_writer_2026-09-05.md``, ``docs/c3tools_discovery_2026-09-05.md``).

``WdbGenerater.dll`` (``PackC3IniIntoWdb``) and ``WdbExtractor.dll``
(``UnpackC3IniFromWdb``) each carry one contiguous block of ``printf`` format
strings for ``ini/3DEffect.ini``. Laid out by descending address -- which is
emit order -- the block reads:

    Amount, EffectId%d, TextureId%d, Scale%d, ASB%d, ADB%d,
    ZBuffer%d, Billboard%d,
    Delay, LoopTime, FrameInterval, LoopInterval, OffsetX, OffsetY, OffsetZ,
    Billboard, ColorEnable, Lev

**THE INSTRUMENT HAS A POSITIVE CONTROL AND IT PASSES.** Twelve of those
eighteen name fields this reader already had, and all twelve appear in
exactly the struct order above -- ``amount`` then the layer's five, then the
header's seven. Twelve fields landing in the one right order out of 12!
orderings is not a coincidence, so the six leftovers land where the block
puts them: two bytes at the end of the layer, four at the end of the header.
``ini_backup_chinese/`` holds the pristine 2013 copies and both were read
there; the two DLLs are separately compiled and agree.

Corroboration and limits, per field:

* ``zBuffer`` (layer +14) -- **CONFIRMED by a differential.** MEASURED over
  every EFFE table in the corpus, 1,140,063 layers: the old ``pad`` u16 takes
  only 0 (1,135,522) and 1 (4,541), and its **high byte is 0 in 100%**.
  ``CCO-snapshot``'s plaintext ``[musicnote8]`` is the ONE section in 33
  clients' ``3DEffect.ini`` that sets a ``ZBuffer<i>`` key; it says
  ``Amount=8`` and ``ZBuffer0=1`` and nothing else per-layer. The compiled
  record of that name on 7632/7682/7867/7878 agrees on all eight layers'
  effect/texture/asb/adb and on every header field, and its pads are
  ``[1,0,0,0,0,0,0,0]``. So the flag is the LOW byte -- had the ini key order
  (LoopOnce, ZBuffer) been the struct order, ``ZBuffer0=1`` would have to
  read 0x0100, and 0x0100 never occurs.
* ``billboard`` (layer +15) -- named by position only. It is 0 on all
  1,140,063 layers, so nothing in the corpus exercises it.
* ``billboard`` (header +62), ``colorEnable`` (+63) -- position, plus a
  differential that agrees **within the method's own noise floor** (below).
* ``lev`` (header +64) -- **named by position, and its VALUES do not track
  the ini's ``Lev``.** With ``Lev`` defaulting to 1 the two agree on only
  47.87% of 54,559 name-matched pairs, against a 99.34-100% band for every
  field that is certainly right. That is 100x the noise floor, so something
  real differs -- most likely a value the compiler derives rather than
  copies. The NAME rests on the format-string block; the SEMANTICS do not.

**THE EARLIER NEGATIVES HERE WERE NOT VALID, AND THE REASON GENERALISES.**
This docstring used to say ``unk64`` is *not* ``Lev`` and ``unk62`` is *not*
``Billboard``, from exactly the differential above. Its premise -- that a
client's ``3DEffect.ini`` is what its ``3DEffect.dbc`` was compiled from --
**is false, and the file sizes say so**: 5517 ships 2,599 ini sections
against 3,391 compiled records, 6090 2,599 against 4,483, and 6609 onward
2,599 against 5,313. The plaintext is a frozen older authoring state.

So the differential's disagreements are not evidence of a misnamed field, and
the calibration proves it: run the SAME comparison on fields nobody doubts
and it still disagrees -- ``Amount`` 99.34%, ``LoopTime`` 99.66%,
``EffectId`` 99.91%, ``ADB`` 99.96%. Against that floor, ``Billboard``'s
99.96% is indistinguishable from a perfect field, and the whole population
that could ever discriminate it -- the ~50 pairs declaring ``Billboard=1`` --
is 0.09% of the sample, i.e. **smaller than the noise it would have to be
seen against**. A CONTROL THAT CANNOT RESOLVE THE THING IT IS POINTED AT
RETURNS A NEGATIVE, NOT A "NO ANSWER", and that is what happened.

``LoopOnce`` IS NOT A FIELD OF THIS TABLE. The C3Tools ``effect-template/``
INIs write ``LoopOnce<i>`` per layer and omit ``Scale<i>``, which is what
first suggested ``pad`` -> ``loopOnce, zBuffer``. Both DLLs put
``LoopOnce%d`` in a *different* block, beside ``FrameOffset%d``,
``Interval%d``, ``XSelf%d``, ``YSelf%d``, ``ZSelf%d``, and that block's
filename literal is ``ini/3DEffect2.ini`` / ``ini/3DEffect2.dbc``. **No
client in the corpus ships either**, and ``Scale%d`` is in the 3DEffect
block, so the templates describe a table this format is not.

``EMOI`` (VERIFIED on 6090, the only client that ships one: 70 records,
``8 + 70 x 36`` bytes exactly, tiling the file with no slack):

    0x00  char[4]  "EMOI"
    0x04  u32      recordCount
    0x08  ...      recordCount x { u32 id, char[32] name }

Ids are **not** dense: 6090 carries 0..67 plus 76 and 77. Verified against
the plaintext ``EmotionIco.ini`` (a bare ``<id> <name>`` list, not sections):
all 68 shared ids carry byte-identical names, and the compiled table adds
``76 Silver`` and ``77 CP``.

Usage::

    from dbc import Rsdb, read_simo, read_effe, read_emoi
    obj = Rsdb.parse(assets.read("ini/3DObj.dbc"))
    obj.get(9990010)                  -> 'c3/mesh/9990010.c3'
    motion = Rsdb.parse(assets.read("ini/3dmotion.dbc"))
    motion.get(9990010100 & 0xFFFFFFFF) -> 'c3/npc/999001100.c3'
    simo = read_simo(assets.read("ini/3DSimpleObj.dbc"))
    simo[211]                         -> [(9990010, 9990211)]
    effe = read_effe(assets.read("ini/3DEffect.dbc"))
    effe[0]["name"], effe[0]["layers"][0]["effect"]  -> ('M_Fire', 1066)
    read_emoi(assets.read("ini/EmotionIco.dbc"))[0]  -> 'Hoho'

``MATR`` and ``SIM6`` never ship as a standalone ``.dbc`` -- they exist only
as sections inside ``ini/c3.wdb`` -- so their readers take the container and
an offset instead of a whole file::

    from wdb import ResourceDb
    db = ResourceDb(root / "ini" / "c3.wdb")
    d = (root / "ini" / "c3.wdb").read_bytes()
    s = [x for x in db.section_table if x["tag"] == "MATR"][0]
    read_matr(d, s["offset"])[0]["name"]              -> 'default'

CLI::

    py -3 core/dbc.py show PATH [--limit N]
"""
from __future__ import annotations

import struct
from typing import Optional

#: Magic -> what it is. The unparsed members are listed so a caller probing a
#: directory can name what it found instead of guessing.
MAGICS = {
    b"RSDB": "id -> path row table",
    b"SIMO": "simple-object records (3DSimpleObj)",
    b"MESH": "appearance records (armor/armet/weapon/... .dbc)",
    b"EFFE": "effect definitions (3DEffect)",
    b"EMOI": "emotion-icon names (EmotionIco)",
}


def magic(data: bytes) -> Optional[bytes]:
    """The table's four-byte magic, if it is one we know of."""
    return data[:4] if data[:4] in MAGICS else None


class Rsdb:
    """A standalone ``RSDB`` file: ids to NUL-terminated path strings.

    ``paths`` maps id -> path exactly as stored (latin-1, unnormalised).
    ``extra`` is populated only for stride-12 tables (``3dmotion.dbc``) and
    carries that row's middle u32, meaning unknown.
    """

    def __init__(self, paths: dict, extra: dict, stride: int):
        self.paths = paths
        self.extra = extra
        self.stride = stride

    def get(self, rid: int) -> Optional[str]:
        return self.paths.get(rid)

    def __contains__(self, rid: int) -> bool:
        return rid in self.paths

    def __len__(self) -> int:
        return len(self.paths)

    @classmethod
    def parse(cls, data: bytes) -> "Rsdb":
        if data[:4] != b"RSDB":
            raise ValueError(f"not an RSDB table: magic {data[:4]!r}")
        (count,) = struct.unpack_from("<I", data, 4)
        stride = cls._detect_stride(data, count)
        paths: dict = {}
        extra: dict = {}
        n = stride // 4
        for i in range(count):
            fields = struct.unpack_from(f"<{n}I", data, 8 + i * stride)
            rid, off = fields[0], fields[-1]
            end = data.find(b"\0", off)
            paths[rid] = data[off:end].decode("latin-1")
            if n == 3:
                extra[rid] = fields[1]
        return cls(paths, extra, stride)

    @staticmethod
    def _detect_stride(data: bytes, count: int) -> int:
        """The stride under which every row's last field is a valid string
        offset. Interleaved fields of the wrong stride land outside the
        string region, which is what makes this decidable."""
        if count == 0:
            return 8
        for stride in (8, 12):
            table_end = 8 + count * stride
            if table_end > len(data):
                continue
            ok = all(
                table_end <= struct.unpack_from(
                    "<I", data, 8 + i * stride + stride - 4)[0] < len(data)
                for i in range(count))
            if ok:
                return stride
        raise ValueError("no stride in (8, 12) makes every offset valid")


#: ``WeaponMotion``'s two spellings of one key, and the width between them.
#:
#: SOLVED 2026-08-09; this file previously said the composition was unknown.
#: The ini spells a key as ``appearance`` then a **3-digit** action field, in
#: decimal (``1050000999`` + ``300`` -> ``"1050000999300"``).  The compiled
#: twin packs the same pair with a **4-digit** action field and lets the
#: product wrap::
#:
#:     id = (appearance * 10_000 + action) mod 2**32
#:
#: MEASURED on 5517: **25,944 of 25,944 ini rows resolve, 0 wrong, 0 absent.**
#: The controls are what make that a result rather than a coincidence -- the
#: same sweep with ``10**3`` or ``10**5``, with the whole key taken ``mod
#: 2**32``, and with ``<<12`` / ``<<16`` bit-packings, scores **zero hits on
#: both compiled bases**.  A wrong width here reads as total absence, never as
#: a plausible partial match, which is exactly why the old ``str(id)`` keying
#: reported a whole table missing with total confidence (CORRECTIONS §2).
WEAPONMOTION_INI_ACTION_DIGITS = 3
WEAPONMOTION_DBC_ACTION_DIGITS = 4


def weaponmotion_key(ini_key) -> int:
    """One ``WeaponMotion.ini`` key -> the id its compiled twin stores.

    ``ini_key`` is the ini's own spelling, 12 or 13 digits.  The last
    `WEAPONMOTION_INI_ACTION_DIGITS` are the action; everything before is the
    appearance.
    """
    s = str(ini_key).strip()
    if not s.isdigit() or len(s) <= WEAPONMOTION_INI_ACTION_DIGITS:
        raise ValueError(f"not a WeaponMotion ini key: {ini_key!r}")
    app = int(s[:-WEAPONMOTION_INI_ACTION_DIGITS])
    act = int(s[-WEAPONMOTION_INI_ACTION_DIGITS:])
    return (app * 10 ** WEAPONMOTION_DBC_ACTION_DIGITS + act) % (1 << 32)


def weaponmotion_join(rsdb, ini_keys):
    """Re-key a parsed ``weaponmotion.dbc`` into the ini's spelling.

    Returns ``(joined, stats)`` -- ``joined`` maps the ini key to the twin's
    path for every key the twin carries, and ``stats`` reports what happened.

    **It refuses rather than returning an empty dict.**  Zero matches is the
    signature of a wrong key width, not of an empty table, and the whole
    reason this function exists is that the previous keying returned ``{}``
    and no error.  If nothing joins, that is a defect in the caller or in this
    composition, and it is raised.
    """
    paths = rsdb.paths if hasattr(rsdb, "paths") else rsdb
    joined, absent, bad = {}, [], []
    for k in ini_keys:
        try:
            i = weaponmotion_key(k)
        except ValueError:
            bad.append(k)
            continue
        if i in paths:
            joined[str(k)] = paths[i]
        else:
            absent.append(str(k))
    if ini_keys and not joined:
        raise ValueError(
            f"weaponmotion_join matched 0 of {len(ini_keys)} ini keys against "
            f"{len(paths)} twin rows. That is the key-width signature, not an "
            f"empty table -- see WEAPONMOTION_DBC_ACTION_DIGITS. Refusing to "
            f"return an empty map, because reporting absence with confidence "
            f"is the failure this join was written to end.")
    stats = {"ini_keys": len(ini_keys), "joined": len(joined),
             "absent_from_twin": len(absent), "unparsable": len(bad),
             "twin_rows": len(paths),
             "twin_rows_the_ini_cannot_name": len(paths) - len(joined)}
    return joined, stats


def read_simo(data: bytes) -> dict:
    """``3DSimpleObj.dbc``: id -> [(partId, textureId), ...].

    Records are variable-length, so the file must be walked; walking must
    land exactly on EOF or the record shapes were misread, and that is an
    error rather than a truncation to tolerate silently.
    """
    if data[:4] != b"SIMO":
        raise ValueError(f"not a SIMO table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    pos, out = 8, {}
    for _ in range(count):
        rid, amount = struct.unpack_from("<II", data, pos)
        pos += 8
        parts = []
        for _ in range(amount):
            parts.append(struct.unpack_from("<II", data, pos))
            pos += 8
        out[rid] = parts
    # a record with a wild partAmount walks past EOF and raises struct.error
    # above; this catches the quieter failure of a short or padded file
    if pos != len(data):
        raise ValueError(f"SIMO walk ended at {pos}, file is {len(data)}")
    return out


def read_mesh(data: bytes) -> dict:
    """A ``MESH`` appearance table: id -> ordered part rows.

    The compiled twin of armor.ini / armet.ini / weapon.ini.  Records are
    **variable length**, ``SIMO``-style -- a count followed by that many
    16-byte parts::

        0x00  char[4]  "MESH"
        0x04  u32      recordCount
        then per record:
            u32 id, u32 partCount,
            partCount x { u32 meshId, u32 textureId, u32 mixTexId,
                          u32 packed -- bytes [mixOpt, asb, adb, 0] }

    VERIFIED: all five 6090 tables walk to **exactly EOF** -- armor 3,338
    records / 80,120 B, armet 2,609 / 62,624, weapon 11,185 / 268,448,
    misc 1 / 32, mount 1,677 / 56,320.

    > **CORRECTED -- this was read as a FIXED 24-byte record, and four of
    > the five tables cannot tell the difference.**  ``partCount`` is 1 on
    > every row of armor, armet, weapon and misc, and a 1-part record *is*
    > 24 bytes, so the fixed model tiled those four files exactly and
    > looked verified.  **``mount.dbc`` has 1,004 multi-part records**; under
    > the fixed model it does not tile (``8 + 1677*24 = 40,256`` against
    > 56,320) and was refused outright -- the only reason the error surfaced
    > as a refusal rather than as 2,346 rows of drift is that the stride
    > check happened to be a whole-file one.  The docstring's claim that "a
    > multi-part appearance repeats its id on consecutive rows" described
    > behaviour **no shipped table ever exercised**: the fixed model had zero
    > multi-part records by construction.  `docs/CORRECTIONS.md`
    > `C-2026-08-09-claude-elastic-elion-0da45c`.

    Cross-checked against the stale inis as Rosetta: every one of armet's
    1,168 shared ids matches Mesh0/Texture0 exactly, and armor's 955 shared
    ids differ on one.  Ids are the ini section numbers stored as ints, so
    the CCO form ``002135000`` appears here as ``2135000`` -- ``str(id)``
    matches the 6090 inis' own unpadded convention and `coassets.PartIni.get`
    bridges the zero-padded spelling.  **Ids are not unique**: armor
    declares 3,338 records over 3,326 ids and weapon 11,185 over 11,164, so
    a later record replaces an earlier one under the same key.
    """
    if data[:4] != b"MESH":
        raise ValueError(f"not a MESH table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    out: dict = {}
    pos = 8
    for i in range(count):
        rid, parts = struct.unpack_from("<II", data, pos)
        pos += 8
        # A wild partCount walks past EOF and raises struct.error, which is
        # this format's only integrity check: there is no string region whose
        # offsets must land in-file, so nothing else can catch a bad stride.
        rows = []
        for _ in range(parts):
            mesh, tex, mixtex, packed = struct.unpack_from("<4I", data, pos)
            pos += 16
            b = packed.to_bytes(4, "little")
            rows.append({"mesh": mesh, "texture": tex, "mixtex": mixtex,
                         "mixopt": b[0], "asb": b[1], "adb": b[2]})
        out[rid] = rows
    if pos != len(data):
        raise ValueError(
            f"MESH walk ended at {pos} of {len(data)} bytes -- "
            f"{count} records do not tile the file")
    return out


#: The ``EFFE`` per-record header and per-layer row. Sizes are asserted
#: rather than commented because every count in the docstring above depends
#: on them: 66 and 16 are what make the walk land on EOF.
EFFE_HEADER = struct.Struct("<32sHIIII3fBBH")
EFFE_LAYER = struct.Struct("<IIfBBBB")
assert EFFE_HEADER.size == 66 and EFFE_LAYER.size == 16

#: ``FE32``'s layer. **The header is EFFE's, byte for byte; the layer is NOT
#: EFFE's with a tail bolted on**, and assuming it was is the trap this
#: constant exists to stop. MEASURED over all 6,315 FE32 layers in the corpus
#: (7632/7682 43 records each, 7867 271, 7878 329):
#:
#:   * +8, where EFFE keeps ``scale``, is 0.0 in 6,315 of 6,315. EFFE's own
#:     scale is 1.0 in 91% of layers; a scale of 0 draws nothing.
#:   * +12..+15, where EFFE keeps ``asb, adb, zBuffer, billboard``, is four
#:     zero bytes in 6,315 of 6,315. EFFE has 13 distinct (asb, adb) pairs.
#:   * the byte quad that LOOKS like ``asb, adb, zBuffer, billboard`` is at
#:     **+36**: four distinct values, ``05 02 00 00`` (4,564), ``05 06 00 00``
#:     (1,442), ``05 06 01 00`` (291), and EFFE's own top pairs are (5,2) and
#:     (5,6). A scale-shaped f32 sits at +20 (1.0 in 6,027, 0.78 in 288, and
#:     0.78 is a value EFFE's ``scale`` also takes).
#:
#: So the fields are shifted, not appended, and no offset here is verified.
#: `read_fe32` therefore carries the whole 84-byte row VERBATIM and names
#: nothing. FE32's record names are also DISJOINT from EFFE's -- 0 of 329
#: 7878 FE32 names occur among its 14,378 EFFE names -- so it is a separate
#: namespace, not a newer generation of the same records, and no client ships
#: a plaintext table whose sections match it.
FE32_LAYER_SIZE = 84

#: ``EMOI`` row: ``{u32 id, char[32] name}``.
EMOI_ROW = struct.Struct("<I32s")
assert EMOI_ROW.size == 36

#: ``MATR`` record -- ``char[32] name`` then FIVE u32, and the five are the
#: five columns of ``ini/Material.ini`` in that order. See `read_matr` and the
#: `section_span` note for the evidence; 52 is what makes the section tile.
MATR_RECORD = struct.Struct("<32s5I")
assert MATR_RECORD.size == 52

#: The five ``MATR`` columns, in FILE order.  **DECODED from the client
#: loader** (``Env_DX9/GraphicData.dll``, the function that pushes
#: ``ini/Material.dbc``): it does not store the five u32, it expands each
#: record into a 68-byte ``D3DMATERIAL9`` and ``push_back``s that.  Four of
#: the columns go through the ``D3DCOLOR``-to-``D3DCOLORVALUE`` conversion
#: (``(v>>16)/255 -> .r``, ``(v>>8)/255 -> .g``, ``(v&0xff)/255 -> .b``,
#: ``(v>>24)/255 -> .a``) and land at the member offsets below; the fifth is
#: converted with ``fild`` plus the ``+2**32`` unsigned fixup, i.e. as an
#: UNSIGNED INTEGER, and lands on ``Power``::
#:
#:     col0  +0x20 -> block +0x10   Ambient
#:     col1  +0x24 -> block +0x00   Diffuse
#:     col2  +0x28 -> block +0x20   Specular
#:     col3  +0x2c -> block +0x30   Emissive
#:     col4  +0x30 -> block +0x40   Power       (u32 -> f32, NOT a bit copy)
#:
#: Reproduce with ``py -3 tools/matrloader.py matr``.  The block is 68 bytes
#: laid out as four 16-byte colour quads and a trailing scalar, which is
#: ``D3DMATERIAL9`` exactly, and the type name is not inferred from the
#: shape: the sibling renderer's own decorated exports carry it --
#: ``?Phy_DrawNormal@@YA_NPAUC3Phy@@ABU_D3DMATERIAL9@@...`` in
#: ``Env_DX9/graphic.dll``, ``_D3DMATERIAL8`` in the DX8 twin.  That renderer
#: feeds ``material+0x00`` and ``material+0x20`` straight into the shader
#: constants its own HLSL calls ``c3_Material.vDiffuse`` and
#: ``c3_Material.vSpecular``.
#:
#: **This also settles §1's open note the other way round:** the fifth column
#: being the integer ``1`` rather than ``1.0f`` does NOT rule out ``Power`` --
#: the loader converts it.  "The bytes are not a float" was true and the
#: conclusion drawn from it was wrong.
#:
#: **THE SECOND, INDEPENDENT CONFIRMATION -- the vendor PACKER, not the
#: loader.**  ``WdbGenerater.dll`` RVA 0x8820 parses ``ini/Material.ini`` with
#: the format ``"%s %x %x %x %x %u"`` and scatters the five results into a
#: 68-byte local (``rep movsd`` of 0x11 dwords = ``sizeof(D3DMATERIAL9)``),
#: converting each of the four hex columns from D3DCOLOR ARGB to four floats
#: via ``* (1/255.0f)``.  The writer and the reader agree on all five, which
#: is the pair `read_matr` calls "the two independent confirmations".
#:
#:     MERGED 2026-09-10, backlog item 26.  This constant was BOUND TWICE,
#:     ~14 lines apart, with these two comment blocks above the two bindings
#:     and IDENTICAL tuples in them -- take-both merge residue.  Nothing was
#:     ever wrong: the values agreed, so no reader could hold a stale one.
#:     The cost was entirely in the future, and it is the expensive kind --
#:     an edit to the first binding would have been silently discarded, with
#:     no error and no warning, and the surviving value would still have
#:     looked plausible.  Both comment blocks are kept because each records a
#:     DIFFERENT measurement and dropping either would lose evidence.
MATR_FIELDS = ("ambient", "diffuse", "specular", "emissive", "power")
assert len(MATR_FIELDS) == 5

#: ``SIM6`` element -- 60 bytes, fifteen u32/f32 slots. **DECODED**, see
#: `read_sim6` and `SIM6_FIELDS`; the reader that names it is the client's own
#: ``Env_DX9/GraphicData.dll`` (7878, RVA 0x1bd47), not this file's data, and
#: **sixty is what that loader's own dispatch table says** rather than what
#: the records happen to tile to.  The LAYOUT is decoded; what each field
#: MEANS is a mix of measured and carried, and `SIM6_FIELDS` marks which.
#:
#:     The sentence above about the dispatch table came off an ORPHANED
#:     comment block (backlog item 26): the take-both merge that duplicated
#:     `MATR_FIELDS` left a `#:` block documenting SIM6 stranded between the
#:     two MATR bindings, where it described nothing.  Moved rather than
#:     deleted -- it was the only place that claim was written down.
SIM6_ELEMENT = struct.Struct("<15I")
assert SIM6_ELEMENT.size == 60

#: `SIM6`'s fifteen slots, named. The element is **MESZ's 48-byte part with
#: three id fields widened from u32 to u64** -- 48 + 3*4 = 60 -- NOT "MESZ
#: plus one unknown u32 and an 8-byte tail".  The client reader loads
#: ``mixtex``/``id2``/``id3`` as ``{lo, hi}`` pairs into the same 8-byte
#: destination slots that the MESZ path fills with ``{lo, 0}``.
#:
#: **DECODED from ``Env_DX9/GraphicData.dll``'s loader**, which dispatches the
#: whole family through one function -- ``SIMX``/``SIM3``/``SIM4``/``SIM5``/
#: ``SIM6`` to versions 2/3/4/5/6 and element sizes 16/32/40/48/60 -- and then
#: reads each version's element into the SAME in-memory part struct.  Fields
#: that share a destination across versions are the same field, which is what
#: fixed the three slots ``SIM6`` added:
#:
#: The ladder is CUMULATIVE -- each version appends, and the head never
#: moves until SIM6 breaks it:
#:
#:     SIMX  mesh tex mixtex packed                              16
#:     SIM3  mesh tex mixtex packed 4xf32                        32
#:     SIM4  mesh tex mixtex packed 4xf32 t2 t3                  40
#:     SIM5  mesh tex mixtex packed 4xf32 t2 t3 u u              48
#:     SIM6  mesh tex mixtex X packed 4xf32 t2 X t3 X u u        60
#:
#: * ``mixtex_hi`` (+12) is the slot ``SIM6`` gained, and it is the one the
#:   data could never place: it holds ZERO in the only sample that exists, so
#:   indices 0-3 all reproduce it.  The loader settles it -- ``SIM6`` +0x00,
#:   +0x04 and +0x08 land in the destinations ``SIM5`` fills from +0x00,
#:   +0x04 and +0x08, and +0x0c lands in one every earlier version hard-fills
#:   with zero.  ``py -3 tools/sim6loader.py`` prints the table and its plants.
#: * ``mixtex_hi`` / ``tex2_hi`` / ``tex3_hi`` are each the second dword of an
#:   8-byte pair the loader keeps per part, and the pair is a **64-bit asset
#:   id: the preceding dword is the LOW half and this one is the HIGH half.**
#:   DECODED; ``py -3 tools/matrloader.py key`` prints it with its controls,
#:   and the three legs are:
#:
#:   1. **The consumer treats the pair as one value.**  ``GraphicData.dll``
#:      rva ``0x12d0e`` (7878 DX9) loads ``[p]`` and ``[p+4]``, ``or``s them
#:      together and skips the part when the result is zero -- a 64-bit zero
#:      test -- then passes BOTH to the resource-name lookup as an 8-byte key.
#:      The record's ``mesh`` and its PRIMARY ``texture`` go through the same
#:      lookup with the second dword hard-coded ``0`` (rva ``0x12c84`` and
#:      ``0x12e9e``: ``push 0; push <id>``).
#:   2. **The index that key addresses splits ids the same way.**  One
#:      function in the same module reads ``RSDB`` rows at stride 8 with the
#:      key's high dword written as the literal ``0``, and ``RSDC`` rows at
#:      stride 12 with it taken from the row's middle dword.  Both branches
#:      insert into the same map.  ``RSDC`` is `wdb.ROW_TABLES`' second magic.
#:   3. **The data agrees, falsifiably.**  Over the 34 ``RSDC`` rows in the
#:      corpus whose high dword is non-zero, ``(hi << 32) | lo`` equals the
#:      number spelled in the row's own path on **30**; the same arithmetic on
#:      the 150,021 rows whose high dword is zero matches the filename only
#:      **4.0%** of the time, so the pass is not something the test gives away.
#:
#:   They are zero in the only ``SIM6`` sample because that asset's ids fit in
#:   32 bits, as do all but 34 rows in the whole corpus.  **``RSDC`` and
#:   ``SIM6`` ship on exactly the same two builds, 7867 and 7878** -- the
#:   build that widened resource ids is the build that widened the part
#:   struct's secondary texture slots.
#: * ``mix0..mix3`` are FOUR f32, not three plus a u32: the loader
#:   default-fills them ``fld1/fst/fst/fstp`` then ``fldz/fstp``, i.e.
#:   ``(1, 1, 1, 0)``, which is why the fourth reads as an integer zero
#:   everywhere.  The vendor packer writes a four-float ``MixData%d=`` key for
#:   the same record (``%f,%f,%f,%f`` in ``WdbGenerater.dll``), so ``MixData``
#:   is the likely name -- that part is INFERRED, the count and the type are
#:   not.
#: ``packed`` is the byte quad ``{mixOpt, asb, adb, materialIndex}``; the
#: fourth byte indexes an array of 68-byte ``D3DMATERIAL9`` (the client
#: divides the array's byte span by 68 to bounds-check it), which is the
#: MATR table.  The 2013 packer wrote that byte as a literal 0.
#:
#:     MERGED 2026-09-10, backlog item 26 -- the same take-both residue as
#:     `MATR_FIELDS` above, nine lines apart, identical fifteen names in both
#:     bindings and only the line-wrapping different.  Latent, never live.
SIM6_FIELDS = ("mesh", "texture", "mixtex", "mixtex_hi", "packed",
               "mix0", "mix1", "mix2", "mix3", "tex2", "tex2_hi",
               "tex3", "tex3_hi", "unk52", "unk56")
assert len(SIM6_FIELDS) == 15

#: The same fifteen slots grouped the way the client reads them: the three
#: 64-bit ids reassembled. Keys are `read_sim6`'s ``wide`` sub-dict.
SIM6_WIDE = (("mixtex64", "mixtex", "mixtex_hi"),
             ("tex2_64", "tex2", "tex2_hi"),
             ("tex3_64", "tex3", "tex3_hi"))

#: magic -> per-part/per-element size, for the two versioned member families,
#: read off the client dispatcher's own size table (7878 ``Env_DX9/
#: GraphicData.dll``: mesh members at RVA 0x209c0, 3DSimpleObjEx members at
#: RVA 0x1bbe3). Only the four magics with no sample in the corpus are routed
#: here; MESH/MESX/MESZ/SIM6 keep their own documented branches, and their
#: sizes agreeing with this table is what says the table was read right.
_VERSIONED_ELEMENT = {b"SIMX": 0x10, b"MESY": 0x28, b"SIM3": 0x20,
                      b"SIM4": 0x28, b"SIM5": 0x30}

#: The same table including the four magics that already had their own
#: branch, so a caller can check the dispatcher against the walk. Their sizes
#: agreeing is the positive control on the dispatcher read.
VERSIONED_ELEMENT_SIZES = dict(_VERSIONED_ELEMENT)
VERSIONED_ELEMENT_SIZES.update({b"MESH": 0x10, b"MESX": 0x20,
                                b"MESZ": 0x30, b"SIM6": 0x3c})

#: Legacy alias for the ``SIMO``-family half of `VERSIONED_ELEMENT_SIZES`.
#: KEPT because `tools/sim6loader.py`, `tests/test_wdb_sections.py` and
#: `scratchpad/sim6/plantcheck.py` call it by this name; the assert below is
#: what stops the two tables drifting apart.
SIM_ELEMENT_SIZE = {b"SIMX": 16, b"SIM3": 32, b"SIM4": 40,
                    b"SIM5": 48, b"SIM6": 60}
assert SIM_ELEMENT_SIZE[b"SIM6"] == SIM6_ELEMENT.size
assert all(VERSIONED_ELEMENT_SIZES[k] == v for k, v in SIM_ELEMENT_SIZE.items()), \
    "SIM_ELEMENT_SIZE and VERSIONED_ELEMENT_SIZES disagree"


def _fixed_name(raw: bytes, what: str, allow_high: bool = False) -> str:
    r"""A NUL-terminated string in a fixed-width field, or an error.

    The integrity check that a fixed-record format offers in place of
    ``RSDB``'s "every offset lands in-file": if the record stride were wrong
    the name field would slide onto numeric fields, and both the printable
    test and the clean-padding test would fail immediately. Measured over
    every record of both compiled tables -- 3,391 at 5517 and 4,483 at 6090 --
    zero names carry an unprintable byte and zero carry anything but NULs
    after the terminator.

    > **`allow_high` EXISTS BECAUSE ONE TABLE'S NAMES ARE GBK, AND THIS IS THE
    > SAME CORRECTION `wdb._read_rows` TOOK ON 2026-09-05.**  CONTROL bytes
    > refuse; HIGH bytes do not.  MEASURED 2026-09-06 over every fixed-width
    > name field in every ``c3.wdb`` and ``ini/*.dbc`` on this machine:
    >
    >     EMOI   3,824 fields   **651 carry a high byte** (17.0%)
    >     MATR      33 fields   0
    >     ROPT   1,951 fields   0  (352 part + 352 mesh + 352 motion + 895 dumy)
    >
    > and across all 5,808 fields, **zero control bytes, zero bytes after the
    > NUL, zero missing terminators**.  ``\xb5\xaf\xc4\xbb-\xb3\xc1\xcb\xbc``
    > is CP936, an emotion name in Chinese -- vendor data, not corruption.
    > Refusing it made 13 of the 34 ``EMOI`` sections in this corpus
    > unreadable, and the one standalone ``EmotionIco.dbc`` (6090) is
    > all-ASCII, which is why the strict rule survived: the only sample the
    > reader was pointed at could not exercise it.
    >
    > The default stays STRICT.  A caller opts in per format, so the writer's
    > strictness matches its reader's per magic -- a writer looser than its own
    > reader would emit a section nothing here could read back.
    >
    > Decoding is latin-1 ON PURPOSE, `wdb`'s reason: it round-trips every
    > byte, so the string re-encodes to the bytes it came from.  Decoding CP936
    > here would make the name unwritable.
    """
    name, _, pad = raw.partition(b"\0")
    if pad.strip(b"\0"):
        raise ValueError(f"{what}: bytes after the NUL terminator: {raw!r}")
    bad = (any(c < 32 or c == 127 for c in name) if allow_high
           else not all(32 <= c < 127 for c in name))
    if bad:
        raise ValueError(f"{what}: name is not printable: {raw!r}")
    return name.decode("latin-1")


def read_effe(data: bytes) -> list:
    """``3DEffect.dbc``: the compiled effect definitions, in file order.

    Returns a list of dicts -- **not** a name-keyed mapping, because names
    are not unique: 6090 ships 4,483 records under 4,472 distinct names
    (``FF03_1``, ``zf2-e222``, ``elf-follow`` and eight others appear twice).
    5517's 3,391 names are all distinct, so a dict would have looked correct
    on the client its author had configured. Callers that want a lookup
    should build one and decide for themselves which duplicate wins.

    Records are variable-length, so the file is walked, and the walk must
    land exactly on EOF -- see ``read_simo`` for why that is an error rather
    than a truncation to tolerate.
    """
    if data[:4] != b"EFFE":
        raise ValueError(f"not an EFFE table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    pos, out = 8, []
    for i in range(count):
        (raw, amount, delay, loop, frame, interval,
         ox, oy, oz, billboard, color, lev) = EFFE_HEADER.unpack_from(data, pos)
        pos += EFFE_HEADER.size
        layers = []
        for _ in range(amount):
            (eid, tid, scale, asb, adb,
             zbuf, lbill) = EFFE_LAYER.unpack_from(data, pos)
            pos += EFFE_LAYER.size
            layers.append({"effect": eid, "texture": tid, "scale": scale,
                           "asb": asb, "adb": adb,
                           "z_buffer": zbuf, "billboard": lbill})
        out.append({
            "name": _fixed_name(raw, f"EFFE record {i}"),
            "amount": amount, "delay": delay, "loop_time": loop,
            "frame_interval": frame, "loop_interval": interval,
            "offset": (ox, oy, oz),
            "billboard": billboard, "color_enable": color, "lev": lev,
            "layers": layers})
    if pos != len(data):
        raise ValueError(f"EFFE walk ended at {pos}, file is {len(data)}")
    return out


def read_fe32(data: bytes) -> list:
    """``FE32``: EFFE's sibling table, header decoded and layers carried raw.

    Ships only inside ``ini/c3.wdb`` on 7632/7682/7867/7878 -- there is no
    standalone ``.dbc`` -- so callers slice the section out with
    `section_span` first.

    The 66-byte record header is EFFE's, and that much is verified the same
    way EFFE's is: every walk lands exactly on the end of the section on all
    four clients, and every name is a clean NUL-terminated printable string.
    **The 84-byte layer is not decoded** -- see `FE32_LAYER_SIZE` for the
    measurement that refutes the obvious "EFFE's 16 plus a tail" reading --
    so each layer is returned as its ``raw`` bytes and nothing is named.
    Guessing an offset here would produce plausible numbers, which is the
    failure mode this reader is shaped to avoid.
    """
    if data[:4] != b"FE32":
        raise ValueError(f"not an FE32 table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    pos, out = 8, []
    for i in range(count):
        (raw, amount, delay, loop, frame, interval,
         ox, oy, oz, billboard, color, lev) = EFFE_HEADER.unpack_from(data, pos)
        pos += EFFE_HEADER.size
        layers = []
        for _ in range(amount):
            layers.append({"raw": bytes(data[pos:pos + FE32_LAYER_SIZE])})
            pos += FE32_LAYER_SIZE
        out.append({
            "name": _fixed_name(raw, f"FE32 record {i}"),
            "amount": amount, "delay": delay, "loop_time": loop,
            "frame_interval": frame, "loop_interval": interval,
            "offset": (ox, oy, oz),
            "billboard": billboard, "color_enable": color, "lev": lev,
            "layers": layers})
    if pos != len(data):
        raise ValueError(f"FE32 walk ended at {pos}, file is {len(data)}")
    return out


class EffeWriteError(ValueError):
    """A record that cannot be expressed as an EFFE/FE32 table.

    Raised rather than coerced. Every field below has a width the format
    fixes, and silently truncating one produces a table that still parses --
    the whole class of defect `read_effe`'s EOF check exists to catch on the
    way in, arriving instead on the way out.
    """


def _effe_name_bytes(name, what: str) -> bytes:
    """`name` in EFFE's 32-byte NUL-padded field, or a refusal.

    `_fixed_name` accepts only printable ASCII with clean NUL padding, so a
    writer that emitted anything else would produce a table its own reader
    rejects. 31 is the cap, not 32: the field is NUL-*terminated* on every
    record of every shipped table (measured -- `_fixed_name` raises on bytes
    after the terminator, and no client trips it).
    """
    b = name.encode("latin-1") if isinstance(name, str) else bytes(name)
    if len(b) > 31:
        raise EffeWriteError(
            f"{what}: name is {len(b)} bytes, the field holds 31 plus a NUL")
    if not all(32 <= c < 127 for c in b):
        raise EffeWriteError(f"{what}: name is not printable ASCII: {b!r}")
    return b + b"\0" * (32 - len(b))


def _effe_header_bytes(r: dict, what: str, n_layers: int) -> bytes:
    """The shared 66-byte record header of EFFE and FE32."""
    amount = int(r["amount"])
    if amount != n_layers:
        raise EffeWriteError(
            f"{what}: amount says {amount} but {n_layers} layer(s) are "
            f"present. The count is what the next record's offset is "
            f"computed from, so writing the declared value would move every "
            f"record after this one.")
    fields = (("amount", amount, 0xFFFF), ("delay", int(r["delay"]), 0xFFFFFFFF),
              ("loop_time", int(r["loop_time"]), 0xFFFFFFFF),
              ("frame_interval", int(r["frame_interval"]), 0xFFFFFFFF),
              ("loop_interval", int(r["loop_interval"]), 0xFFFFFFFF),
              ("billboard", int(r["billboard"]), 0xFF),
              ("color_enable", int(r["color_enable"]), 0xFF),
              ("lev", int(r["lev"]), 0xFFFF))
    for fname, v, hi in fields:
        if not 0 <= v <= hi:
            raise EffeWriteError(
                f"{what}: {fname}={v} does not fit the field (0..{hi})")
    ox, oy, oz = r["offset"]
    return EFFE_HEADER.pack(
        _effe_name_bytes(r["name"], what), amount, int(r["delay"]),
        int(r["loop_time"]), int(r["frame_interval"]), int(r["loop_interval"]),
        ox, oy, oz, int(r["billboard"]), int(r["color_enable"]), int(r["lev"]))


def serialize_effe(records: list) -> bytes:
    """Encode `read_effe`'s output back to an ``EFFE`` table -- its inverse.

    **Byte-exactness first**, in the shape `tools/effects.py:serialize_moti`
    and `tools/c3write.py` use::

        assert serialize_effe(read_effe(blob)) == blob

    The gate is `tests/test_effe_roundtrip.py`, which asserts that over every
    EFFE table in the corpus and refuses to pass on an empty one.

    Nothing is derived and nothing is defaulted: every field is written from
    the value the reader read, including the two layer bytes and the four
    header bytes that were unnamed until 2026-09-05. That matters more than
    it sounds -- ``billboard`` at layer +15 is 0 on all 1,140,063 layers in
    the corpus, so a writer that "helpfully" omitted it would round-trip
    every shipped file and still be wrong.

    Out-of-range values are REFUSED, not masked. ``amount`` in particular is
    checked against the actual layer count, because the two disagreeing is
    the one error this format cannot survive: the count is the stride, so a
    wrong one silently reinterprets the whole rest of the table.
    """
    return _serialize(records, b"EFFE", None)


def serialize_fe32(records: list) -> bytes:
    """Encode `read_fe32`'s output back to an ``FE32`` table -- its inverse.

    Same contract as `serialize_effe`. Because `read_fe32` names nothing
    inside the 84-byte layer, this writes each layer's ``raw`` bytes
    verbatim, and refuses a layer whose ``raw`` is not exactly 84 bytes --
    the one check available when the content is opaque.
    """
    return _serialize(records, b"FE32", FE32_LAYER_SIZE)


def _serialize(records: list, tag: bytes, raw_layer: int | None) -> bytes:
    out = bytearray(tag + struct.pack("<I", len(records)))
    for i, r in enumerate(records):
        what = f"{tag.decode()} record {i} ({r.get('name', '?')!r})"
        layers = r["layers"]
        out += _effe_header_bytes(r, what, len(layers))
        for j, L in enumerate(layers):
            if raw_layer is None:
                for fname, hi in (("effect", 0xFFFFFFFF),
                                  ("texture", 0xFFFFFFFF), ("asb", 0xFF),
                                  ("adb", 0xFF), ("z_buffer", 0xFF),
                                  ("billboard", 0xFF)):
                    if not 0 <= int(L[fname]) <= hi:
                        raise EffeWriteError(
                            f"{what} layer {j}: {fname}={L[fname]} does not "
                            f"fit the field (0..{hi})")
                out += EFFE_LAYER.pack(
                    int(L["effect"]), int(L["texture"]), L["scale"],
                    int(L["asb"]), int(L["adb"]),
                    int(L["z_buffer"]), int(L["billboard"]))
            else:
                blob = L["raw"]
                if len(blob) != raw_layer:
                    raise EffeWriteError(
                        f"{what} layer {j}: raw is {len(blob)} bytes, the "
                        f"stride is {raw_layer}. Nothing in this layer is "
                        f"decoded, so its length is the only thing that can "
                        f"be checked -- and a short one would shift every "
                        f"record after it.")
                out += blob
    return bytes(out)


def read_emoi(data: bytes) -> dict:
    """``EmotionIco.dbc``: emotion id -> icon name.

    **6090 is the only client that ships one.** 5517 has compiled twins for
    fourteen other tables and still reads the plaintext ``EmotionIco.ini``,
    so a caller must handle absence rather than treat it as a broken install.

    Fixed 36-byte rows, so the count has to tile the file exactly, and a
    stride error cannot pass silently.  ``read_mesh`` and ``read_simo`` reach
    the same guarantee the other way round -- their records are variable
    length, so they walk and then require the walk to **end on EOF**.
    """
    if data[:4] != b"EMOI":
        raise ValueError(f"not an EMOI table: magic {data[:4]!r}")
    (count,) = struct.unpack_from("<I", data, 4)
    if 8 + count * EMOI_ROW.size != len(data):
        raise ValueError(
            f"EMOI row count {count} does not tile the file "
            f"({len(data)} bytes, expected {8 + count * EMOI_ROW.size})")
    out: dict = {}
    for i in range(count):
        eid, raw = EMOI_ROW.unpack_from(data, 8 + i * EMOI_ROW.size)
        # HIGH BYTES ARE DATA HERE. 651 of the 3,824 EMOI name fields in this
        # corpus are GBK; see `_fixed_name`. Control bytes and a dirty pad --
        # the two things a slid stride actually produces -- stay refused, and
        # neither occurs in any shipped table.
        out[eid] = _fixed_name(raw, f"EMOI row {i}", allow_high=True)
    return out


# --------------------------------------------------------------------------
# MATR and SIM6 -- sections that never ship as a standalone .dbc
# --------------------------------------------------------------------------
#: **THESE TWO TAKE AN OFFSET AND DO NOT REQUIRE EOF, AND THAT IS DELIBERATE.**
#: Every other reader above is handed a whole ``.dbc`` file and asserts that
#: its walk lands on the last byte, which is that format's only integrity
#: check. `MATR` and `SIM6` have no standalone form -- MEASURED: a scan of
#: 14,514 ``.dbc``/``.wdb``/``.dat``/``.ini`` files under the client corpus
#: found **zero** files whose first four bytes are either magic, while the
#: same scan reads `BDMG`, `RSDB`, `MESH`, `SIMO`, `EFFE` and `EMOI` heads
#: normally, so the zero is a result and not a broken scan. They exist only
#: as sections inside ``ini/c3.wdb``, where the end of the section is
#: `section_span`'s arithmetic and not EOF.


def read_matr(data: bytes, off: int = 0) -> list:
    """``MATR``: the compiled twin of ``ini/Material.ini``.

    Returns a list of dicts in file order::

        {"name": str, "fields": (u32, u32, u32, u32, u32)}

    **DECODED -- the record is ``char[32] name`` then five u32, and the five
    are Material.ini's five columns in file order.**  What settles it is that
    the plaintext twin ships beside the binary on every client and the two
    carry the same numbers::

        material=1
        default ffffffff ffffffff ffffffff 0 0     <- 30 clients
        default 0        FFFFFFFF 0        0 1     <- the vendor tool's own

    MEASURED over every ``ini/c3.wdb`` on this machine plus the three the
    vendor authoring tool ships (``C3Tools/ini/c3.wdb``, its ``.old``, and
    ``empty-env-template``): **33 MATR sections, 32 of them pairable with a
    non-empty Material.ini, and 32 of 32 match -- name and all five values,
    in order, zero mismatches.**  Padding after the name's NUL is clean in
    all 33.

    > **THE VENDOR FILE IS THE ONLY SAMPLE THAT CAN TELL THE COLUMNS APART,
    > and this is the ROPT trap avoided rather than survived.**  Every one of
    > the 30 clients stores ``FFFFFFFF FFFFFFFF FFFFFFFF 0 0``: columns 0-2
    > are equal and columns 3-4 are equal, so swapping any two of them
    > reproduces the file exactly.  MEASURED: with columns 0 and 1 swapped,
    > or 1 and 2 swapped, **all 30 client pairs still "match"** -- a control
    > that cannot fail is not evidence.  The vendor's ``0 FFFFFFFF 0 0 1``
    > is asymmetric and refuses both swaps.  One file with different content
    > did what twenty-six more copies of the same content could not.

    The name field is 32 bytes.  MEASURED: at 24, 28, 30 or 31 the five u32
    land on the wrong bytes and **0 of 32 pairs match**; at 33 or wider there
    is no room left for five u32 inside a 52-byte record, so the width is
    fixed by arithmetic on that side rather than by a comparison.

    **THE FIVE FIELDS ARE NOW NAMED, FROM THE LOADER** -- see `MATR_FIELDS`
    for the offsets and the controls.  ``ambient diffuse specular emissive
    power``, in file order, because the loader expands each record into a
    68-byte ``D3DMATERIAL9`` and the five columns land on
    ``+0x10 +0x00 +0x20 +0x30 +0x40``.  Both are returned: ``fields`` is the
    five u32 verbatim, in file order, and ``material`` is the same five keyed
    by name, so a caller that disagrees with the naming still has the bytes.

    > **THE ARGUMENT THAT CLOSED THIS FILE'S PREVIOUS PARAGRAPH WAS BACKWARDS,
    > and it is worth keeping because it reads as careful.**  That paragraph
    > said the vendor row's fifth column being the integer ``1`` and not
    > ``1.0f`` "rules out the naive ``Power`` reading".  The observation is
    > right; the inference is not.  The loader reads that column with ``fild``
    > and an unsigned fixup -- it CONVERTS the integer -- and stores the float
    > on ``D3DMATERIAL9::Power``.  **A field's on-disk type does not have to
    > be its in-memory type, and "the bytes are not a float" is evidence
    > about the packer, not about the member.**

    The four colours are ``D3DCOLOR`` (``0xAARRGGBB``): the loader takes
    ``>>16``, ``>>8``, ``>>0`` and ``>>24``, masks each to a byte, and divides
    by 255 into the four floats of a ``D3DCOLORVALUE``.  That is what makes
    "these are colours" a measurement rather than a plausible shape --
    ``py -3 tools/matrloader.py matr`` fails its channel control if any
    colour does not use all four shifts exactly once, in that order.

    **THE FIVE FIELDS ARE NAMED, 2026-09-06, FROM THE VENDOR PACKER.**  The
    data could not name them and said so; the binary can.
    ``C3Tools/ini_backup_chinese/WdbGenerater.dll`` (md5
    ``77f003c03ef1d63dfa999b8e46bb588b`` -- the pristine 2013 copy, NOT the
    patched folder-root one) parses ``ini/Material.ini`` at RVA 0x8820 with::

        fscanf(f, "%s %x %x %x %x %u", name, &a, &b, &c, &d, &e)

    and scatters the results into a 68-byte local that is then copied out
    with ``rep movsd`` of **0x11 dwords = 68 = sizeof(D3DMATERIAL9)**.  Each
    of the four hex columns is split ``>>16 & 0xff`` / ``>>8 & 0xff`` /
    ``& 0xff`` / ``>>24`` and each byte is ``fild``-ed and multiplied by the
    constant at RVA 0x1d0e0, which is ``0x3B808081`` = 1/255.0f -- i.e. each
    is a ``D3DCOLOR`` ARGB expanded into a ``D3DCOLORVALUE {r,g,b,a}``.  The
    fifth column takes a different path: it is loaded with ``fild qword``
    (zero-extended integer) and stored at struct+0x40 -- ``Power``.  Column
    to member, read off the stack offsets::

        col 1 (%x) -> struct+0x10   Ambient
        col 2 (%x) -> struct+0x00   Diffuse
        col 3 (%x) -> struct+0x20   Specular
        col 4 (%x) -> struct+0x30   Emissive
        col 5 (%u) -> struct+0x40   Power

    **TWO INDEPENDENT CONFIRMATIONS, neither of which is the offsets again.**
    (1) The same function's *fallback* material, built when Material.ini is
    missing, writes ``1.0f`` to struct+0x00..0x0c, zero to +0x10..0x3c and
    ``1.0f`` to +0x40 -- white Diffuse, everything else zero, Power 1 -- and
    that is byte-for-byte the vendor's own ``default 0 FFFFFFFF 0 0 1`` row
    under this mapping and under no other.  (2) The client's own reader
    (7878 ``Env_DX9/GraphicData.dll``) indexes the material array by
    ``base + i*68`` and bounds-checks it by dividing the array's byte span
    by 68, so the record really is a ``D3DMATERIAL9``.

    The ordering is ``Ambient`` before ``Diffuse``, which is *not* the
    ``D3DMATERIAL9`` declaration order -- the ini column order and the struct
    order genuinely differ, and reading the columns as declaration order
    would swap the first two.

    ``power`` is an integer in the file (the vendor row's ``1`` is
    ``01 00 00 00``, not ``0x3F800000``); the loader converts it to float on
    read, so a writer must emit the integer.
    """
    tag = bytes(data[off:off + 4])
    if tag != b"MATR":
        raise ValueError(f"not a MATR section: magic {tag!r}")
    (count,) = struct.unpack_from("<I", data, off + 4)
    end = off + 8 + count * MATR_RECORD.size
    if end > len(data):
        raise ValueError(f"MATR at 0x{off:x} declares {count} records, which "
                         f"runs to {end} past a {len(data)}-byte buffer")
    out = []
    for i in range(count):
        raw, *fields = MATR_RECORD.unpack_from(data, off + 8 + i * 52)
        rec = {"name": _fixed_name(raw, f"MATR record {i}"),
               "fields": tuple(fields),
               # `fields` is the verbatim five u32 in file order.  The
               # five names are offered BOTH flat and under "material":
               # the packer-side and loader-side derivations landed on
               # the same MATR_FIELDS by different routes and each left
               # callers behind, so both shapes are kept.
               "material": dict(zip(MATR_FIELDS, fields))}
        rec.update(zip(MATR_FIELDS, fields))
        out.append(rec)
    return out


def read_sim6(data: bytes, off: int = 0) -> dict:
    """``SIM6``: ``ini/3DSimpleObjEx.dbc`` version 6. **DECODED 2026-09-06.**

    Returns ``{id: [element, ...]}`` where each element is a dict keyed by
    `SIM6_FIELDS` plus the derived ``mixopt``/``asb``/``adb``/``material``
    bytes, the four ``mix*`` slots as floats, and a ``wide`` sub-dict holding
    the three reassembled 64-bit ids.

    The record framing is measured: ``{u32 id, u32 amount}`` then ``amount``
    elements of 60 bytes, which is what `section_span` walks, what lands on
    the next known magic, AND what the client loader's own dispatch table
    declares (``SIM6 -> version 6, element 0x3c``).

    > **CORRECTED 2026-09-06, AND THE SUPERSEDED READING IS BELOW BECAUSE IT
    > WAS RIGHT ABOUT THE BYTES AND WRONG ABOUT THE FIELDS.**  This reader
    > used to describe the element as "MESZ's 48-byte part plus one unknown
    > u32 ahead of ``packed`` plus 8 bytes of tail", with the unknown parked
    > at index 3 on parsimony.  **Index 3 was correct; "unknown" and "tail"
    > were not.**  The element is MESZ's part with **three id fields widened
    > from u32 to u64** -- 48 + 3*4 = 60 -- so the slot at index 3 is
    > ``mixtex``'s high dword, and the two "tail" u32 are the high dwords of
    > the two ids at +0x24 and +0x2c, which sit in the middle of the record
    > and not at the end.
    >
    > **THE EVIDENCE IS THE CLIENT'S OWN READER, not this file's data.**
    > 7878 ``Env_DX9/GraphicData.dll`` dispatches ``ini/3DSimpleObjEx.dbc``
    > at RVA 0x1bbe3 on the magic, into a version code and an element size::
    >
    >     SIMX 2  0x10   SIM3 3  0x20   SIM4 4  0x28
    >     SIM5 5  0x30   SIM6 6  0x3c
    >
    > and the same binary dispatches the mesh members at RVA 0x209c0::
    >
    >     MESH 1  0x10   MESX 2  0x20   MESY 3  0x28   MESZ 4  0x30
    >
    > The version-6 body (RVA 0x1bd47) and the MESZ body (RVA 0x20b42) load
    > the same destination slots in the same order, and the ONLY difference
    > is where the high halves come from -- MESZ writes a literal zero into
    > each, SIM6 reads one from the file::
    >
    >     role         MESZ src            SIM6 src
    >     mesh   u32   +0x00               +0x00
    >     texture u32  +0x04               +0x04
    >     mixtex u64   +0x08 / literal 0   +0x08 / +0x0c
    >     packed quad  +0x0c..0x0f         +0x10..0x13
    >     mix[4] f32   +0x10..0x1f         +0x14..0x23
    >     id2    u64   +0x20 / literal 0   +0x24 / +0x28
    >     id3    u64   +0x24 / literal 0   +0x2c / +0x30
    >     unk52    u32   +0x28               +0x34
    >     unk56    u32   +0x2c               +0x38
    >
    > Every one of the 60 bytes is accounted for, which the old reading could
    > not claim.  The version-2 (SIMX, 16-byte) body at RVA 0x1bf74 is the
    > positive control: it reads mesh/texture/mixtex/packed at +0/+4/+8/+0xc,
    > which is **byte-for-byte the element the 2013 vendor packer writes**
    > (``WdbGenerater.dll``'s SIMX writer, RVA 0xd5c0, element loop at
    > 0xd684 -- see ``docs/wdb_packer_re_2026-09-06.md``).  Two binaries a decade apart,
    > read independently, agree.
    >
    > ``packed``'s fourth byte is **not** a pad.  Both readers use it as an
    > index into an array of 68-byte ``D3DMATERIAL9`` (the bounds check
    > divides the array's byte span by 68), i.e. into the MATR table.  The
    > 2013 packer stores a literal 0 there, which is why every MESZ part in
    > the corpus has a zero top byte -- an absence produced by the writer,
    > not by the format.

    **THE ELEMENT'S FIELDS COME FROM A TWIN, NOT FROM THE SECTION.**  Only
    7867 and 7878 ship a SIM6 and both declare **one record with one
    element**, byte-identical between the two clients, so the section itself
    can never show that a field is a field.  What makes it readable at all is
    that the same asset is described a second time in the same file, by a
    format that has thousands of records::

    **THE FIELD LAYOUT COMES FROM THE LOADER; THE DATA COULD NOT SUPPLY IT
    AND THAT IS RECORDED HERE BECAUSE IT NEARLY WENT THE OTHER WAY.**  Only
    7867 and 7878 ship a SIM6, both declare one record with one element, and
    the two elements are byte-identical -- MEASURED over every ``c3.wdb`` on
    the corpus machine plus the vendor tool's three: **35 files, 2 non-empty
    SIM6 sections, 1 distinct element body.**  ``SIMX`` is present in six
    files and **empty in all six**; ``SIM3``, ``SIM4``, ``SIM5`` and ``MESY``
    appear in none.  So the section cannot testify about itself, and the
    predecessor it might have been diffed against has no rows either.

    What settles it is ``Env_DX9/GraphicData.dll`` (and its ``Env_DX8``
    twin).  The function is identified by an xref rather than by the module
    knowing the magic: it pushes the literal ``ini/3DSimpleObjEx.dbc`` at its
    own first instruction, and the ``SIMO`` reader is a DIFFERENT function a
    few hundred bytes earlier pushing ``ini/3DSimpleObj.dbc``.  ONE function
    handles the whole family -- ``SIMX``/``SIM3``/``SIM4``/``SIM5``/``SIM6``
    to versions 2/3/4/5/6 at 16/32/40/48/60 bytes -- reading every version
    into the SAME in-memory part struct, so a destination two versions share
    is a field two versions share::

        SIM5 (48)   mesh tex mixtex   packed 4xf32 t2   t3   u u
        SIM6 (60)   mesh tex mixtex X packed 4xf32 t2 X t3 X u u

    (the ladder below SIM5 is cumulative -- SIMX stops after ``packed``,
    SIM3 after the floats, SIM4 after ``t3``; see `SIM6_FIELDS`)

    ``SIM6`` +0x00/+0x04/+0x08 land in the destinations ``SIM5`` fills from
    +0x00/+0x04/+0x08; +0x0c lands in one every earlier version fills with a
    hard-coded zero.  **THE EXTRA SLOT IS AT INDEX 3, MEASURED.**  Reproduce
    with ``py -3 tools/sim6loader.py`` -- it prints the size arithmetic (each
    version's recovered reads must account for exactly the size the same
    function declares: 5 of 5 OK), the four-build agreement, the pre-SIM6
    builds 7632/7682 yielding versions 2-5 and no 6, and two plants that
    prove the table follows the bytes rather than being printed from a
    constant.

    The twin below is now CORROBORATION rather than the basis.  The same
    asset is described a second time in the same file by ``MESZ``, whose
    loader decodes the same way (``MESH``/``MESX``/``MESY``/``MESZ`` to
    versions 1/2/3/4 at 16/32/40/48) and whose columns have thousands of
    rows behind them::

        SIM6  id=2270  amount=1
          element  89000000 89000000 89000001 0 394539 16.0f 0 0 0
                   89000002 0 0 0 0 0
        MESZ  id=89000000  parts=1
          part     89000000 89000000 89000001   394539 16.0f 0 0 0
                   89000002 0 0 0

    The MESZ part is 48 bytes and its columns are established over **9,841
    parts on 7878, 9,716 on 7867 and 9,021 on each of 7632/7682**: col0 is a
    mesh id (9,703 of 9,841 resolve, every one to a ``.c3``), col1 a texture
    id, col2 a mixtex id (166 of 169 non-zero values resolve, every one to a
    ``.dds``), col3 MESH's ``packed`` byte quad (asb is 5 in 9,841 of 9,841
    and adb 6 in 9,836; the top byte is 0 in all), col4-7 a float quad,
    col8/col9 further texture ids (106 and 9 non-zero, **100%** ``.dds``).
    The controls are what make that a measurement: the same lookup on col3
    and col4 resolves **0 of 9,841** -- they are not ids -- and on 4,000
    random 7-digit ids it resolves 3 or fewer.

    Two details of that reading are CORRECTED by the loader, and both were
    the kind of error a corpus of identical values cannot show:

      * col4-7 is a **four**-float group, not a float triple plus a u32.  The
        loader default-fills it ``fld1/fst/fst/fstp`` then ``fldz/fstp`` --
        ``(1, 1, 1, 0)`` -- so the fourth float's default is ``0.0f``, whose
        bit pattern is an integer zero.  9,841 parts all carrying the default
        made it look like a separate always-zero u32.
      * the TOP byte of ``packed`` is an **index, not padding**.  MEASURED:
        the loader bounds-checks it against ``(end - begin) / 68`` of an
        array on the owning object and ``lea``s an element of that array; out
        of range takes an error path.  **That the array is the MATERIAL table
        is no longer only an argument from "MATR is the only material
        table".**  The array the ``SIM``/``MES`` loader bounds-checks lives at
        ``+0x84``/``+0x88`` of its owning object, and those are the *same two
        member offsets* the ``MATR`` loader's own vector occupies -- its
        container is at ``this+0x78`` and a ``std::vector``'s ``begin``/``end``
        sit at ``+0xc``/``+0x10`` of that, i.e. ``this+0x84``/``+0x88`` -- with
        the name map at ``+0x90`` in both the ``MATR`` loader and the
        lookup-by-name accessor at rva ``0x18170``.  ``68`` is
        ``sizeof(D3DMATERIAL9)``, which is why the divisor is 68.  What is
        NOT separately shown is that the two functions hold the same ``this``;
        three matching member offsets in one module is strong and is not the
        same as reading it.  The consequence holds regardless, and it is a
        PREDICTION rather
        than a restatement: every ``MATR`` in the corpus declares exactly ONE
        record (33 of 33), so 0 is the only legal index, which is why the
        byte reads 0 in 9,841 of 9,841 parts.

    The four ids in the SIM6 element resolve the same way: ``89000000`` to
    ``c3/spirit/8900/1.c3`` (and ``1.dds`` -- the mesh and texture spaces
    share the number, exactly as ``SIMO``'s ``partId == textureId`` rows do),
    ``89000001`` to ``1_1.dds`` and ``89000002`` to ``1_2.dds``.

    **THE THREE FIELDS ``SIM6`` ADDED ARE THE HIGH HALVES OF 64-BIT ASSET
    IDS** -- see `SIM6_FIELDS` for the three legs of evidence and the
    controls.  Each is the second dword of an 8-byte pair whose first dword is
    the id's LOW half: ``mixtex_hi`` beside ``mixtex``, ``tex2_hi`` beside
    ``tex2``, ``tex3_hi`` beside ``tex3``.  ``read_sim6`` returns the raw
    halves AND the joined value as ``mixtex64`` / ``tex2_64`` / ``tex3_64``,
    so a caller can use either.  All three highs are zero in the only sample,
    which is the ordinary case: over the whole corpus only 34 resource rows
    have a non-zero high half.

    ``unk52`` and ``unk56`` are still carried, not named: they are ``MESZ``'s
    col10/col11, zero in 9,841 of 9,841 parts.

    **A CLIENT SHIPPING A SIM6 WITH A SECOND ELEMENT IS STILL THE FIRST TEST
    OF THE SPAN**, which the field decode does not upgrade -- the span needs
    the element size to be constant, and one element cannot show that.

    One value in the sample is a trap and is called out so nobody quotes it:
    the record id 2270 "resolves" to ``c3/effect/egg/2-7.dds``.  It is four
    digits, and `wdb.ResourceDb`'s own measurement puts the false-positive
    rate for a random 4-digit id at **20.9%**.  That lookup is not evidence
    of anything.
    """
    tag = bytes(data[off:off + 4])
    if tag != b"SIM6":
        raise ValueError(f"not a SIM6 section: magic {tag!r}")
    (count,) = struct.unpack_from("<I", data, off + 4)
    pos = off + 8
    out: dict = {}
    for _ in range(count):
        rid, amount = struct.unpack_from("<II", data, pos)
        pos += 8
        end = pos + amount * SIM6_ELEMENT.size
        if end > len(data):
            raise ValueError(
                f"SIM6 record {rid} declares {amount} elements, which runs to "
                f"{end} past a {len(data)}-byte buffer")
        elements = []
        for _ in range(amount):
            vals = SIM6_ELEMENT.unpack_from(data, pos)
            pos += SIM6_ELEMENT.size
            e = dict(zip(SIM6_FIELDS, vals))
            b = e["packed"].to_bytes(4, "little")
            # The fourth byte is the material index, not a pad -- both the
            # client reader and the vendor packer treat it as one.
            e["mixopt"], e["asb"], e["adb"], e["material"] = b
            # MESX's float quad, so the four are floats together or not at
            # all; presenting one as a float and its neighbours as u32 would
            # invite exactly the misreading this module keeps warning about.
            for k in ("mix0", "mix1", "mix2", "mix3"):
                e[k] = struct.unpack("<f", struct.pack("<I", e[k]))[0]
                # The three slots SIM6 added are the HIGH dwords of 64-bit
                # asset ids; the raw halves stay, and the join is offered
                # BOTH flat (e["mixtex64"]) and nested (e["wide"]["mixtex64"]).
                e["wide"] = {name: e[lo] | (e[hi] << 32)
                             for name, lo, hi in SIM6_WIDE}
                e.update(e["wide"])
            elements.append(e)
        out[rid] = elements
    return out


#: One ``ROPT`` record: the part's name, the mesh table that backs it, and the
#: motion table that backs it.  Sizes come from `section_span`'s ROPT branch and
#: are the same three fields it steps over; see `read_ropt` for the evidence.
ROPT_RECORD = struct.Struct("<32s256s256s")
assert ROPT_RECORD.size == 544
ROPT_DUMY = struct.Struct("<I32s")
assert ROPT_DUMY.size == 36


def read_ropt(data: bytes, off: int = 0) -> dict:
    r"""``ROPT``: **which parts exist and which table backs each one.**

    Returns::

        {"parts":  [{"part": str, "mesh_ini": str, "motion_ini": str}, ...],
         "dumies": [{"index": int, "name": str}, ...]}

    in file order, both lists.  The record shape is `section_span`'s ROPT
    branch -- ``char[32] partName``, ``char[256] meshIni``, ``char[256]
    motionIni``, then a SECTION-level dumy table of ``{u32, char[32]}`` -- and
    that branch's docstring holds why the section-level reading of the field at
    0x08 is the right one.  This function reads the fields that branch steps
    over; the two must be corrected together.

    **WHAT THIS SETTLES, AND IT IS A SEMANTIC POINT THE REST OF THE TREE GETS
    WRONG BY INFERENCE.**  ``armor.ini`` is not "the armor table"; it is *the
    mesh table for the part named* ``body``.  5517's ROPT says so in ten
    records::

        body       ini/armor.dbc    ini/3dmotion.dbc
        armet      ini/armet.dbc    ini/armetmotion.dbc
        r_weapon   ini/weapon.dbc   ini/weaponmotion.dbc
        l_weapon   ini/weapon.dbc   ini/weaponmotion.dbc
        mount      ini/mount.dbc    ini/mountmotion.dbc
        misc       ini/misc.dbc     ini/miscmotion.dbc
        head       ini/head.dbc     ini/headmotion.dbc
        shield     ini/shield.dbc   ini/weaponmotion.dbc
        mix_body   ini/armor.dbc    ini/3dmotion.dbc
        mix_armet  ini/armet.dbc    ini/armetmotion.dbc

    The list is DECLARATIVE and it GROWS: 8 records on Zephyr, 10 on
    5165-6716, 12 on 6652-7205, 14 on 7632/7682, 15 on 7867/7878 (which adds
    ``spirit``).  Nothing about "armor", "armet" or "weapon" is a fixed
    vocabulary -- the *part* names are, and the tables are whatever the record
    says.

    MEASURED 2026-09-06 over the 30 installs under `coroot.clients_dir()` that
    ship an ``ini/c3.wdb`` with a ROPT section: **350 records, 350 decoded, 0
    refusals** -- every ``partName``, ``meshIni`` and ``motionIni`` printable
    with clean NUL padding behind it, which is `_fixed_name`'s integrity check
    and the thing a wrong stride cannot survive.

    **THE `.dbc` IN THE STRING BUYS NOTHING ON DISK, and that is a measured
    negative worth carrying** rather than an assumption.  ROPT names
    ``ini/armor.dbc`` where the plaintext ``ini/RolePart.ini`` names
    ``ini/armor.ini``, so it is tempting to read the compiled string as
    pointing at a file the plaintext cannot reach.  Over those same 350
    declarations: 149 ship BOTH extensions, 113 ship only the ``.ini``, 88
    ship NEITHER, and **``.dbc``-only is 0**.  So resolving ROPT's string
    verbatim would lose 113 tables and gain none; a caller wanting a file
    should try the ``.ini`` sibling first, which is what
    `coassets.AssetRoot.part_tables` does.  (Loose files only -- no install
    measured resolves an appearance table out of an archive, and
    `AssetRoot._table_path` refuses that case loudly.)
    """
    tag = bytes(data[off:off + 4])
    if tag != b"ROPT":
        raise ValueError(f"not a ROPT section: magic {tag!r}")
    count, dumy_count = struct.unpack_from("<II", data, off + 4)
    end = off + 12 + count * ROPT_RECORD.size + dumy_count * ROPT_DUMY.size
    if end > len(data):
        raise ValueError(f"ROPT at 0x{off:x} declares {count} records and "
                         f"{dumy_count} dumies, which runs to {end} past a "
                         f"{len(data)}-byte buffer")
    base = off + 12
    parts = []
    for i in range(count):
        name, mesh, motion = ROPT_RECORD.unpack_from(
            data, base + i * ROPT_RECORD.size)
        parts.append({
            "part": _fixed_name(name, f"ROPT record {i} partName"),
            "mesh_ini": _fixed_name(mesh, f"ROPT record {i} meshIni"),
            "motion_ini": _fixed_name(motion, f"ROPT record {i} motionIni"),
        })
    dbase = base + count * ROPT_RECORD.size
    dumies = []
    for i in range(dumy_count):
        idx, name = ROPT_DUMY.unpack_from(data, dbase + i * ROPT_DUMY.size)
        dumies.append({"index": idx,
                       "name": _fixed_name(name, f"ROPT dumy {i}")})
    return {"parts": parts, "dumies": dumies}


def _main(argv=None) -> int:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="dump a .dbc table's rows")
    s.add_argument("path", type=Path)
    s.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    data = a.path.read_bytes()
    m = magic(data)
    if m is None:
        print(f"unknown magic {data[:4]!r}")
        return 1
    print(f"{a.path.name}: {m.decode()} -- {MAGICS[m]}")
    if m == b"RSDB":
        t = Rsdb.parse(data)
        print(f"{len(t)} rows, stride {t.stride}")
        for rid in list(t.paths)[:a.limit]:
            e = f"  extra={t.extra[rid]}" if t.extra else ""
            print(f"  {rid:>12}  {t.paths[rid]}{e}")
    elif m == b"SIMO":
        t = read_simo(data)
        print(f"{len(t)} records")
        for rid in list(t)[:a.limit]:
            print(f"  {rid:>6}  {t[rid]}")
    elif m == b"EFFE":
        t = read_effe(data)
        layers = sum(len(r["layers"]) for r in t)
        print(f"{len(t)} records, {len(set(r['name'] for r in t))} distinct "
              f"names, {layers} layers")
        for r in t[:a.limit]:
            print(f"  {r['name']:<32} amount={r['amount']} "
                  f"loop={r['loop_time']} frame={r['frame_interval']}ms "
                  f"offset={r['offset']}")
            for L in r["layers"]:
                print(f"      effect={L['effect']:<8} texture={L['texture']:<8}"
                      f" scale={L['scale']:g} asb={L['asb']} adb={L['adb']}"
                      f" zbuf={L['z_buffer']} bill={L['billboard']}")
    elif m == b"EMOI":
        t = read_emoi(data)
        print(f"{len(t)} records")
        for eid in list(t)[:a.limit]:
            print(f"  {eid:>4}  {t[eid]}")
    else:
        print("recognised but not parsed here")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# --------------------------------------------------------------------------
# Section spans -- how many bytes one embedded table occupies
# --------------------------------------------------------------------------
#: WHY THIS LIVES HERE AND NOT IN `wdb.py`.
#:
#: `ini/c3.wdb` is these same tables concatenated behind a header, so walking it
#: needs each table's LENGTH. The record shapes that determine that length are
#: already defined in this file and verified against shipped clients; copying
#: them into the container reader would create two descriptions of one format,
#: and the copy is the one that goes stale. `wdb.py` owns the container, this
#: module owns the records, and `section_span` is the seam.
#:
#: The `read_*` functions above cannot serve: each requires an exact slice and
#: asserts it lands on EOF, which is the right check for a standalone `.dbc`
#: and unusable when the end is the thing being computed.


class UnknownSection(ValueError):
    """A section magic whose record shape this module does not know.

    Raised rather than skipped ON PURPOSE. A container walk that guesses past
    an unknown table lands mid-record and then reads whatever is there as the
    next header -- which does not fail, it produces plausible garbage. The
    caller must stop and say what stopped it.
    """


def section_span(data: bytes, off: int = 0) -> int:
    """Bytes occupied by the table starting at `off`, header included.

    Every branch mirrors the corresponding `read_*` walk above and nothing else;
    if one of those is corrected, this must be corrected with it, and
    `tests/test_wdb_sections.py` asserts the two agree on real files.
    """
    tag = bytes(data[off:off + 4])
    (count,) = struct.unpack_from("<I", data, off + 4)
    pos = off + 8
    if count == 0 and tag != b"ROPT":
        # An empty table is a header and nothing else. This is the case the old
        # wdb walk treated as end-of-file, which is why it reported two tables
        # on files that declare twenty-two.
        #
        # > **`ROPT` IS THE ONE EXCEPTION AND IT COST 580 BYTES, MEASURED
        # > 2026-09-06.**  A `ROPT`'s SECOND count sits at 0x08, ahead of the
        # > records, so a `ROPT` with zero records is NOT an empty section: it
        # > still carries `dumyCount` and the whole dumy table.  This early-out
        # > returned **8** for `C3Tools/ini/c3.wdb.old`'s `ini/RolePart.dbc`,
        # > whose container index says **588** -- and `12 + 0*544 + 16*36` is
        # > 588 exactly, so the ROPT branch below was right and never ran.
        # >
        # > **NOTHING CAUGHT IT BECAUSE THAT MEMBER IS LAST IN ITS FILE.**  The
        # > walk consumed its twelve declared sections and stopped before it
        # > could land 580 bytes short, so `wdb.ResourceDb` reported 12 of 12
        # > and `walk_stopped` None on a file it had mis-spanned.  A wrong span
        # > on the final section is invisible to a walk that terminates on the
        # > header's count.
        # >
        # > It was found by comparing this walk against the CONTAINER INDEX's
        # > own member size -- two independent descriptions of one length, one
        # > computed from records and one read out of an enciphered entry.
        # > `tests/test_wdb_section_write.py:span_control` asserts it per
        # > member.  No shipped client has an empty ROPT (0 of 30), so the
        # > defect lived only in the vendor file.
        return 8
    if tag == b"EFFE":
        for _ in range(count):
            amount = EFFE_HEADER.unpack_from(data, pos)[1]
            pos += EFFE_HEADER.size + amount * EFFE_LAYER.size
    elif tag == b"FE32":
        #: EFFE's newer sibling, on 7632/7682/7867/7878 only. IDENTICAL 66-byte
        #: header -- same char[32] name, same u16 layer count at offset 32 --
        #: with an 84-byte layer in place of EFFE's 16.
        #:
        #:     8 + sum over records of (66 + amount * 84)
        #:
        #: Found from the layer stride, not from the header: the 84-byte
        #: repeat is visible in the raw bytes (a record's blocks recur at
        #: +476, +560, +644) and 66 + 13*84 = 1158 is exactly the distance
        #: between the first two record names. The u16 at +32 reads 13.
        #:
        #: A FIRST SWEEP OVER (header, layer) PAIRS FOUND NOTHING, and the
        #: reason is worth keeping: it tried layer sizes 16/20/24/28/32 by
        #: analogy with EFFE and 84 was not in the list. The search was sound
        #: and its candidate set was too narrow -- an absence over a set you
        #: chose is not an absence.
        #:
        #: VALIDATED on all four clients that ship one; every walk lands on
        #: RSDB, and all four then complete and land on the 0x08 field.
        for _ in range(count):
            amount = EFFE_HEADER.unpack_from(data, pos)[1]
            pos += EFFE_HEADER.size + amount * FE32_LAYER_SIZE
    elif tag == b"SIMO":
        for _ in range(count):
            amount = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + amount * 8
    elif tag == b"MESH":
        for _ in range(count):
            parts = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + parts * 16
    elif tag == b"MESZ":
        #: The third MESH generation, on 7632/7682 only. Same
        #: {u32 id, u32 partCount} header; the part element widens again.
        #:     MESH 16 bytes  ->  MESX 32  ->  MESZ 48
        #: Validated by walking every record and landing on a known magic.
        for _ in range(count):
            parts = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + parts * 48
    elif tag in (b"SIMX", b"SIM3", b"SIM4", b"SIM5", b"SIM6"):
        #: The SIMO family above generation 1. Same {u32 id, u32 amount}
        #: header throughout; only the element widens.
        #:
        #:     SIMO 8 -> SIMX 16 -> SIM3 32 -> SIM4 40 -> SIM5 48 -> SIM6 60
        #:
        #: MEASURED, and not from the files. The client module that parses
        #: `ini/3DSimpleObjEx.dbc` -- `Env_DX9/GraphicData.dll`, and its
        #: `Env_DX8` twin -- dispatches all five magics in ONE function to a
        #: {version, elementSize} pair, and those are the sizes it declares.
        #: Reproduce with `py -3 tools/sim6loader.py`; it prints the table
        #: for four independently compiled modules plus 7632/7682, which
        #: agree, and it checks each size against the fields that version
        #: actually reads (5 of 5 account for every byte).
        #:
        #: THE CORPUS CANNOT CONFIRM FOUR OF THESE FIVE AND THAT IS THE
        #: POINT. MEASURED over the 35 c3.wdb-shaped files on the corpus
        #: machine: `SIMX` appears in six and is EMPTY in all six; `SIM3`,
        #: `SIM4` and `SIM5` appear in none; `SIM6` appears in two, each
        #: with ONE record of ONE element, and the two elements are
        #: byte-identical. A single-element sample cannot show that a
        #: stride is a stride -- that is what made `ROPT` read wrong twice
        #: -- so for `SIM6` the walk landing on a known magic and the
        #: loader's declared 0x3c are two independent facts that agree,
        #: rather than one fact quoted twice. The other four arms have never
        #: executed against a non-empty section at all.
        for _ in range(count):
            amount = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + amount * SIM_ELEMENT_SIZE[tag]
    elif tag == b"MESY":
        #: MESH generation 3, between MESX (32) and MESZ (48) at 40 bytes.
        #:
        #: NO FILE IN THE CORPUS CONTAINS ONE -- 0 of 35. This arm exists
        #: only because the loader that gives `MESZ` its 48 also gives
        #: `MESY` its 40, from the same dispatch table, and raising
        #: `UnknownSection` on a magic whose stride is known would be
        #: throwing away a measurement. It has never executed.
        for _ in range(count):
            parts = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + parts * 40
    elif tag == b"MESX":
        #: MESH's wider sibling: the same {u32 id, u32 partCount} header and the
        #: same leading 16-byte part quad {mesh, tex, mixtex, packed}, followed
        #: by SIXTEEN MORE BYTES per part -- FOUR f32.
        #:
        #:     8 + sum over records of (8 + partCount * 32)
        #:
        #: Read off the bytes rather than guessed: record 0 of 6609's section is
        #: id=1111000, parts=1, then 1111000 / 2111300 / 0 / 394496 -- a MESH
        #: quad -- then 0x3F800000 three times and a zero, and record 1 begins
        #: exactly 40 bytes in.
        #:
        #: THE FOURTH FLOAT WAS READ AS A U32 UNTIL THE LOADER SAID
        #: OTHERWISE, and the reason it survived is worth keeping: the loader
        #: default-fills the group `fld1/fst/fst/fstp` then `fldz/fstp`, i.e.
        #: (1, 1, 1, 0), so the fourth's default bit pattern is an integer
        #: zero. Every sample in the corpus carries the default, and a column
        #: that is always zero looks equally like an unused u32 and like a
        #: float at 0.0f. See `tools/sim6loader.py --family MES` and the
        #: correction recorded in `read_sim6`.
        #:
        #: VALIDATED on all 19 clients that ship one: every walk lands on a
        #: KNOWN section magic. Sizes 16/24/28/36 were tried and all fail --
        #: 24 lands on 0x3F800000, i.e. mid-float, which is the reminder that
        #: "the walk did not crash" is not the test.
        for _ in range(count):
            parts = struct.unpack_from("<II", data, pos)[1]
            pos += 8 + parts * 32
    elif tag == b"EMOI":
        pos += count * EMOI_ROW.size
    elif tag == b"ROPT":
        #: RolePart: which body parts exist and which tables back them.
        #:
        #:     0x00  char[4]  "ROPT"
        #:     0x04  u32      recordCount
        #:     0x08  u32      dumyCount          <- SECTION level, not per record
        #:     0x0c  recordCount x 544:
        #:               char[32]   partName
        #:               char[256]  meshIni
        #:               char[256]  motionIni
        #:           then dumyCount x 36:
        #:               u32        index
        #:               char[32]   name         ("v_armet", "v_r_weapon", ...)
        #:
        #: so the span is 12 + recordCount*544 + dumyCount*36.
        #:
        #: THE FIELD AT 0x08 IS WHY THIS TOOK TWO ATTEMPTS, and the reason is
        #: worth keeping. `docs/c3tools_discovery_2026-09-05.md` 4.3 decoded it
        #: from the vendor authoring tool's own c3.wdb as the FIRST FIELD OF THE
        #: RECORD, giving 548 + dumyCount*36 -- which closes on that file to the
        #: byte. **That file has recordCount = 1.** With one record, a
        #: section-level field sitting before it and a record-level field
        #: sitting inside it occupy the same address and no arithmetic can tell
        #: them apart. The vendor sample was structurally incapable of
        #: distinguishing the two readings, so it confirmed the wrong one.
        #:
        #: Clients settle it: 5517 declares TEN records and the per-record
        #: reading desynchronises at record 1. Decoded instead from the string
        #: positions -- meshIni to motionIni is a constant 256 and record to
        #: record a constant 544 -- giving ten clean parts (body, armet,
        #: r_weapon, l_weapon, mount, misc, head, shield, mix_body, mix_armet)
        #: and a trailing dumy table read once.
        #:
        #: VALIDATED on 26 shipped clients plus the vendor file. recordCount
        #: varies 8/10/12 and dumyCount 7/16/37, so the formula is exercised
        #: rather than fitted, and every one lands on a KNOWN section magic.
        #: The same formula now explains the vendor file too -- one layout, not
        #: two.
        rec_count = count
        (dumy_count,) = struct.unpack_from("<I", data, off + 8)
        pos = off + 12 + rec_count * 544 + dumy_count * 36
    elif tag == b"MATR":
        #: 52 bytes, and the record is now DECODED: `char[32] name` then five
        #: u32, which are the five columns of `ini/Material.ini` in order.
        #: `read_matr` holds the evidence and the controls -- 32 of 32
        #: binary/plaintext pairs match across the corpus and the vendor tool's
        #: own files, and only the vendor's asymmetric row can tell the five
        #: columns apart at all.
        #:
        #: The five are NAMED as well now (`MATR_FIELDS`): the client loader
        #: expands each 52-byte record into a 68-byte `D3DMATERIAL9`, so the
        #: columns are `ambient diffuse specular emissive power` in file
        #: order.  `py -3 tools/matrloader.py matr`.
        #:
        #: THE SIZE was derived first, from the gap between the MATR header and
        #: the next section in the vendor authoring tool's c3.wdb, then checked
        #: on 5517, 6609 and Zephyr: at 52 the walk lands exactly on EMOI with
        #: a plausible count on all three, and at 48/56/60 it lands on zero
        #: bytes. 32 + 5*4 = 52 now explains WHY 52, which it did not before.
        #:
        #: AND THE 52 IS NOW STATED BY THE PRODUCER, NOT INFERRED (#240). Everything
        #: above derives the size: the gap to the next section, the walk landing on EMOI
        #: at 52 and on zero bytes at 48/56/60, and 32 + 5*4. The vendor's writer says
        #: it outright -- `WdbGenerater.dll` zeroes the record, copies the name at
        #: offset 0, packs the five columns, and calls `fwrite(rec, 0x34, 1, f)` in
        #: `PackC3IniIntoWdb` at RVA 0xd7b8. **0x34 is 52.**
        #:
        #: IT IS THE SAME BINARY AS THE FIELD NAMING ABOVE, NOT A SECOND SOURCE -- md5
        #: 77f003c03ef1, the pristine 2013 copy. The naming came off its READER path
        #: (`fscanf` over `ini/Material.ini` at RVA 0x8820); this is its WRITER path. So
        #: the two halves of one authoring tool agree, which is weaker than two
        #: independent tools agreeing and stronger than either half alone.
        #:
        #: DO NOT READ THE WRITER'S STRUCT OFFSETS AS A CORRECTION TO `MATR_FIELDS`.
        #: The source struct is a D3DMATERIAL9, whose colours sit at
        #: +0x1c/+0x2c/+0x3c/+0x4c/+0x5c in the order `diffuse ambient specular emissive
        #: power` -- the first two swapped relative to `MATR_FIELDS`. That is the
        #: DESTINATION layout in memory; `MATR_FIELDS` names the INI COLUMNS, which is
        #: what this record holds. Both orders are correct about different things, and
        #: reading one as a rival claim about the other is the mistake this paragraph
        #: exists to stop. (Within each u32 the channel order is a separate open point:
        #: every corpus MATR channel is 0x00 or 0xff, so the bytes are symmetric.)
        #:
        #:     0x00  char[32]  name              ("default")
        #:     0x20  u32       ambient  RGBA     (each channel = float * 255.0)
        #:     0x24  u32       diffuse  RGBA
        #:     0x28  u32       specular RGBA
        #:     0x2c  u32       emissive RGBA
        #:     0x30  u32       power             (a float rounded to int)
        #:
        #: EVERY MATR SEEN SO FAR DECLARES EXACTLY ONE RECORD -- 33 of 33,
        #: counting the three vendor files -- so the stride has never been
        #: multiplied and this arm of the span is still INFERRED. What the
        #: decode adds is that a second record would have to be 52 bytes for
        #: the ini's row-per-record shape to hold; it does not measure it.
        #: `walk_stopped` reporting a bad next tag is how a wrong stride would
        #: surface rather than silently mis-walking.
        pos += count * MATR_RECORD.size
    elif tag in ROW_TABLE_MAGICS:
        #: The id -> path row tables, added 2026-09-06 so this walk is TOTAL
        #: over every magic the corpus contains.
        #:
        #:     8 + rowCount*stride  +  the NUL-terminated path blob
        #:
        #: `wdb.ResourceDb` never reaches this arm -- it parses these two
        #: magics itself, because it wants the rows and not just the length --
        #: so nothing in the container walk changed when it was added. What it
        #: buys is that `section_span` and `parse_section` now cover the SAME
        #: set of magics, which the gate asserts; a magic that can be stepped
        #: over but not authored is exactly the gap the section writers close.
        #:
        #: The stride is PER TABLE and detected, not assumed (5517's ninth
        #: table is stride 12 and exactly a third of it resolves under 8 --
        #: a third resolving is the signature of a wrong stride, not of a
        #: corrupt table), and the blob's end is the span: MEASURED equal to
        #: the container's own next-section offset on 227 of 227 non-empty row
        #: tables.
        pos = off + read_row_table(data, off)["span"]
    else:
        raise UnknownSection(
            "no record shape for %r (%d records at 0x%x)" % (tag, count, off))
    if pos > len(data):
        raise ValueError("%r at 0x%x walks past EOF (%d > %d)"
                         % (tag, off, pos, len(data)))
    return pos - off


# --------------------------------------------------------------------------
# THE SECTION WRITERS -- authoring a table, not just copying a member
# --------------------------------------------------------------------------
#: WHAT THIS HALF IS FOR, AND WHAT IT IS NOT.
#:
#: `core/wdbpack.py` can add, replace, drop and re-index the MEMBERS of an
#: `ini/c3.wdb` byte-exactly.  It moves blobs; it does not understand one.  A
#: member's CONTENT is a section in one of the formats above, and until
#: 2026-09-06 the only two that could be written back were `EFFE` and `FE32`.
#: Growing a member without a serializer for its magic produces a structurally
#: valid container whose contents are malformed -- MEASURED: a 64-byte edit
#: into the middle of 7878's section stream re-indexed correctly and then made
#: `wdb.ResourceDb` stop at "not an ascii section tag" after 3,106 ids.  That
#: is the reader being right, and it is the hole these functions close.
#:
#: The contract is `serialize_effe`'s, and it is the only one worth having:
#:
#:     assert serialize_section(parse_section(blob)) == blob
#:
#: over every section in the corpus, per magic.  `tests/test_wdb_section_write.py`
#: is the gate and it refuses to pass on a magic it never saw.
#:
#: **AUTHORING CHAIN.**  There is no new tool: a standalone `.dbc` IS one
#: section, and a member of `c3.wdb` is the same bytes, so
#:
#:     t = parse_section(pack.blob(member))     # or Path(x.dbc).read_bytes()
#:     ...edit t...
#:     out.write_bytes(serialize_section(t))
#:     py -3 tools/wdbwrite.py repack in.wdb out.wdb --replace ini/3DObj.dbc=out
#:
#: is the whole of it, and `wdbwrite repack` re-lays every later member offset
#: and rebuilds the obfuscated index for the new size.


class SectionWriteError(ValueError):
    """A table that cannot be expressed in its own format.

    Raised rather than coerced, for the reason `EffeWriteError` gives: every
    field here has a width the format fixes, and a silently truncated one
    produces a section that still PARSES -- as a different table.  A section
    whose length changed by one byte also moves every section after it inside
    a container, so a quiet coercion is not a local error.
    """


# --------------------------------------------------------------------------
# RSDB / RSDC -- the id -> path row tables
# --------------------------------------------------------------------------
#: The two row-table magics.  `wdb.ROW_TABLES` is the same tuple; it is spelled
#: there because the container walk parses these itself rather than spanning
#: them, and `tests/test_wdb_section_write.py` asserts the two agree.
ROW_TABLE_MAGICS = (b"RSDB", b"RSDC")

#: Longest path this writer will emit, mirroring `wdb.ResourceDb.MAX_PATH`.
#: The real maximum in the corpus is far below it; the cap exists so a wrong
#: base cannot scan a 12 MB file looking for a NUL.  The two constants are
#: asserted equal by the gate rather than imported, because `dbc` must not
#: depend on `wdb` (the dependency runs the other way).
ROW_PATH_MAX = 260


def read_row_table(data: bytes, off: int = 0) -> dict:
    r"""One ``RSDB``/``RSDC`` section, in FILE ORDER and losslessly.

    Returns::

        {"tag": "RSDB"|"RSDC", "stride": 8|12, "span": int,
         "interned": bool,
         "rows": [{"id": int, "hi": int|None, "path": bytes}, ...]}

    **WHY NOT `Rsdb.parse` OR `wdb.ResourceDb`.**  Both exist and neither can
    feed a writer:

      * `Rsdb.parse` returns ``{id: path}``.  Ids are NOT unique -- MEASURED
        over the 227 non-empty row tables in this corpus, 53 sections declare
        a duplicate -- so a dict silently drops rows, and row ORDER is what
        the string blob's layout is computed from.
      * `wdb.ResourceDb` decodes latin-1 and rewrites ``\`` to ``/``.  That
        normalisation is right for lookups and destroys a round trip.

    So `path` is the raw bytes between the offset and its NUL.

    **THE STRING BLOB IS DEDUPLICATED, and that is the finding that makes a
    writer possible at all.**  Offsets are NOT monotonic in row order -- 226
    of 227 sections have at least one row pointing back at a string an earlier
    row already wrote.  A writer that simply appended each path would produce
    a longer blob with different offsets: still parseable, byte-different, and
    every later section in the container displaced.

    MEASURED, and it is a single rule with no exceptions: walk the rows in
    file order interning the raw byte strings, FIRST OCCURRENCE WINS, and the
    offsets that falls out are the file's own on **227 of 227 sections,
    21,571,394 rows, 30 clients**.  `interned` records that check for this
    particular section; `serialize_row_table` refuses a table where it is
    False, because reproducing such a file is exactly what this model cannot
    do and returning plausible bytes would be worse than refusing.

    The first string always begins at the byte the row table ends on -- zero
    gap, 227 of 227 -- and the blob's end is the section's span, 227 of 227.
    """
    tag = bytes(data[off:off + 4])
    if tag not in ROW_TABLE_MAGICS:
        raise ValueError(f"not a row table: magic {tag!r}")
    (count,) = struct.unpack_from("<I", data, off + 4)
    if count == 0:
        # An empty table is a header and nothing else, and its stride is
        # unobservable: there are no rows to detect it from.  The two are
        # reported honestly rather than defaulted silently -- with no rows the
        # stride does not appear in the bytes either, so the writer does not
        # need one.
        return {"tag": tag.decode(), "stride": None, "span": 8,
                "interned": True, "rows": []}
    for stride in (8, 12):
        got = _row_table_at(data, off, count, stride)
        if got is not None:
            return got
    raise ValueError(
        f"{tag!r} at 0x{off:x}: {count} rows resolve at neither stride 8 nor "
        f"12. A THIRD of the rows resolving is the signature of a wrong "
        f"stride, so this accepts a stride whole or not at all.")


def _row_table_at(data: bytes, off: int, count: int, stride: int):
    """The table read at `stride`, or None if any row fails.

    All or nothing, for `wdb.ResourceDb._read_rows`' reason: under the wrong
    stride every (stride/4)th row still lands on a real string, so a partial
    success is the failure mode, not evidence.
    """
    nu = stride // 4
    row_end = 8 + count * stride
    if off + row_end > len(data):
        return None
    rows = []
    file_offs = []
    blob_end = row_end
    for i in range(count):
        fields = struct.unpack_from(f"<{nu}I", data, off + 8 + i * stride)
        soff = fields[-1]
        file_offs.append(soff)
        at = off + soff
        if not (row_end <= soff) or at >= len(data):
            return None
        end = data.find(b"\0", at)
        if end < 0 or end - at > ROW_PATH_MAX:
            return None
        raw = bytes(data[at:end])
        # Control bytes refuse and high bytes do not -- real paths carry GBK
        # (5517 row 3890 is `c3/effect/laser2/1\xa3\xad1.dds`) and one such row
        # once killed a 9,567-row table.  See `wdb.ResourceDb._read_rows`.
        if not raw or any(c < 32 or c == 127 for c in raw):
            return None
        rows.append({"id": fields[0],
                     "hi": fields[1] if nu == 3 else None,
                     "path": raw})
        blob_end = max(blob_end, (end + 1) - off)
    # `file_offs` is collected in the loop above rather than re-unpacked here.
    # It is the same list either way; the row loop is this gate's hottest --
    # 36.4 million rows over the corpus -- and unpacking each row twice was
    # measurably the dominant cost.
    interned, _ = _intern_offsets(rows, 8 + count * stride)
    return {"tag": bytes(data[off:off + 4]).decode(), "stride": stride,
            "span": blob_end, "interned": interned == file_offs, "rows": rows}


def _intern_offsets(rows: list, base: int):
    """(offset per row, the packed blob) under first-occurrence interning."""
    pos = base
    seen: dict = {}
    offs = []
    blob = bytearray()
    for r in rows:
        p = r["path"]
        at = seen.get(p)
        if at is None:
            at = seen[p] = pos
            blob += p + b"\0"
            pos += len(p) + 1
        offs.append(at)
    return offs, bytes(blob)


def serialize_row_table(table: dict) -> bytes:
    """Encode `read_row_table`'s output back to an ``RSDB``/``RSDC`` section.

    The inverse, byte for byte, over the whole corpus -- see `read_row_table`
    for the interning rule and the measurement behind it.

    **A ROW CAN BE ADDED, and that is the point of the whole exercise**: the
    writer's original justification was ids the index does not contain, which
    is precisely an `RSDB` row that is not there.  Append to ``rows`` and the
    blob, every offset and the section's length are recomputed; hand the
    result to ``tools/wdbwrite.py repack --replace`` and the container's later
    member offsets and its obfuscated index are recomputed too.

    Refusals, each of which would otherwise produce a table that still parses:

      * a `stride` that is not 8 or 12, or a row carrying an `hi` under
        stride 8 (or missing one under 12) -- the stride is the row width, so
        a mismatch reinterprets the entire table;
      * an id, `hi` or a resulting offset outside u32;
      * a path that is empty, carries a control byte or a NUL, or exceeds
        `ROW_PATH_MAX` -- every one of those is refused by the reader, so
        emitting it would build a section this module cannot read back;
      * `interned` False, i.e. a source table whose own offsets this model did
        not reproduce.  MEASURED 0 of 227 in this corpus; if one ever appears
        the honest answer is a refusal, not a re-laid blob.
    """
    tag = table["tag"]
    tagb = tag.encode("latin-1") if isinstance(tag, str) else bytes(tag)
    if tagb not in ROW_TABLE_MAGICS:
        raise SectionWriteError(f"not a row-table magic: {tagb!r}")
    rows = table["rows"]
    if not table.get("interned", True):
        raise SectionWriteError(
            f"{tag}: this table's own string offsets are NOT the first-wins "
            f"intern of its rows, so re-laying the blob would move every "
            f"offset in it. Refusing rather than writing a different file "
            f"that happens to parse.")
    if not rows:
        return tagb + struct.pack("<I", 0)
    stride = table.get("stride")
    if stride not in (8, 12):
        raise SectionWriteError(
            f"{tag}: stride {stride!r}; the row is {{id, offset}} at 8 or "
            f"{{id, hi, offset}} at 12 and nothing else")
    nu = stride // 4
    clean: list = []
    for i, r in enumerate(rows):
        hi = r.get("hi")
        if (hi is None) != (nu == 2):
            raise SectionWriteError(
                f"{tag} row {i}: stride {stride} and hi={hi!r} disagree. The "
                f"middle column is the HIGH DWORD of a 64-bit id (see "
                f"`wdb.ROW_TABLES`); dropping or inventing it silently "
                f"rewrites every row's width.")
        for fname, v in (("id", r["id"]),) + ((("hi", hi),) if hi is not None else ()):
            if not 0 <= int(v) <= 0xFFFFFFFF:
                raise SectionWriteError(
                    f"{tag} row {i}: {fname}={v} does not fit a u32")
        p = r["path"]
        p = p.encode("latin-1") if isinstance(p, str) else bytes(p)
        if not p or len(p) > ROW_PATH_MAX:
            raise SectionWriteError(
                f"{tag} row {i}: path is {len(p)} bytes; the reader accepts "
                f"1..{ROW_PATH_MAX}")
        if any(c < 32 or c == 127 for c in p):
            raise SectionWriteError(
                f"{tag} row {i}: path carries a control byte: {p!r}. High "
                f"bytes are fine -- shipped paths carry GBK -- control bytes "
                f"are not, and a NUL would terminate the string early.")
        clean.append(dict(r, path=p))
    rows = clean
    base = 8 + len(rows) * stride
    offs, blob = _intern_offsets(rows, base)
    out = bytearray(tagb + struct.pack("<I", len(rows)))
    for r, o in zip(rows, offs):
        if o > 0xFFFFFFFF:
            raise SectionWriteError(
                f"{tag}: a string offset reached {o}, past u32. The table is "
                f"too large to address.")
        out += (struct.pack("<3I", r["id"], r["hi"], o) if nu == 3
                else struct.pack("<2I", r["id"], o))
    out += blob
    return bytes(out)


# --------------------------------------------------------------------------
# The versioned part tables -- SIMO/SIMX/SIM3..6 and MESH/MESX/MESY/MESZ
# --------------------------------------------------------------------------
#: magic -> element size for every table shaped ``{u32 id, u32 count}`` then
#: `count` fixed-size elements.  `SIMO` is generation 1 of the SIM family at 8
#: bytes and predates the version dispatch; the other nine come from the
#: client loader's own two size tables (`VERSIONED_ELEMENT_SIZES`).
#:
#: **THE ONE SHAPE IS NOT AN INFERENCE FROM THE NAMES.**  `Env_DX9/
#: GraphicData.dll` reads all five SIM magics in ONE function and all four MES
#: magics in another, each dispatching on the magic into a {version, element
#: size} pair and then reading every version into the same in-memory struct.
#: `py -3 tools/sim6loader.py` prints both tables.
PART_ELEMENT_SIZES = {b"SIMO": 8}
PART_ELEMENT_SIZES.update(VERSIONED_ELEMENT_SIZES)
assert PART_ELEMENT_SIZES[b"MESH"] == 16 and PART_ELEMENT_SIZES[b"MESZ"] == 48
assert PART_ELEMENT_SIZES[b"SIM6"] == SIM6_ELEMENT.size


def read_parts(data: bytes, off: int = 0) -> dict:
    r"""Any ``{u32 id, u32 count}`` + fixed-element table, losslessly.

    Returns::

        {"tag": str, "element": int, "span": int,
         "records": [{"id": int, "elements": [bytes, ...]}, ...]}

    in FILE ORDER, with each element carried as its raw bytes.

    **THE DECODED READERS ABOVE CANNOT FEED A WRITER AND THE REASON IS
    MEASURED, not stylistic.**  `read_mesh`, `read_simo` and `read_sim6` all
    return ``{id: ...}``.  Ids are not unique: over this corpus **12 MESH
    sections and 2 SIMO sections declare a duplicate id**, and a later record
    under the same key replaces an earlier one -- so a dict-shaped reader
    loses records, and a writer fed from one would emit a shorter table.  The
    docstrings already say ids are not unique; this is the function that acts
    on it.

    Elements are RAW because the round trip must not depend on the field
    decode being complete.  `part_fields` / `part_bytes` name them for a
    caller that wants to edit one, and the gate checks that pair separately,
    so a gap in the naming cannot silently corrupt a table that is merely
    being copied.  That is `serialize_fe32`'s discipline applied to nine more
    magics.
    """
    tag = bytes(data[off:off + 4])
    if tag not in PART_ELEMENT_SIZES:
        raise UnknownSection(f"not a part table: magic {tag!r}")
    size = PART_ELEMENT_SIZES[tag]
    (count,) = struct.unpack_from("<I", data, off + 4)
    pos = off + 8
    records = []
    for i in range(count):
        rid, amount = struct.unpack_from("<II", data, pos)
        pos += 8
        end = pos + amount * size
        if end > len(data):
            raise ValueError(
                f"{tag!r} record {i} (id {rid}) declares {amount} elements, "
                f"which runs to {end} past a {len(data)}-byte buffer")
        records.append({"id": rid,
                        "elements": [bytes(data[pos + j * size:
                                                pos + (j + 1) * size])
                                     for j in range(amount)]})
        pos = end
    return {"tag": tag.decode(), "element": size, "span": pos - off,
            "records": records}


def serialize_parts(table: dict) -> bytes:
    """Encode `read_parts`' output back to its section -- the inverse.

    Refuses an element whose length is not the magic's stride.  That is the
    only check available on an opaque element and it is the one that matters:
    the element size IS the stride, so a short one shifts every record after
    it and the table still parses.
    """
    tag = table["tag"]
    tagb = tag.encode("latin-1") if isinstance(tag, str) else bytes(tag)
    if tagb not in PART_ELEMENT_SIZES:
        raise SectionWriteError(f"not a part-table magic: {tagb!r}")
    size = PART_ELEMENT_SIZES[tagb]
    if table.get("element", size) != size:
        raise SectionWriteError(
            f"{tag}: table says element={table['element']} but the client "
            f"loader's own dispatch table says {size} for this magic")
    records = table["records"]
    out = bytearray(tagb + struct.pack("<I", len(records)))
    for i, r in enumerate(records):
        rid = int(r["id"])
        if not 0 <= rid <= 0xFFFFFFFF:
            raise SectionWriteError(f"{tag} record {i}: id={rid} past u32")
        els = r["elements"]
        if len(els) > 0xFFFFFFFF:
            raise SectionWriteError(f"{tag} record {i}: too many elements")
        out += struct.pack("<II", rid, len(els))
        for j, e in enumerate(els):
            b = bytes(e)
            if len(b) != size:
                raise SectionWriteError(
                    f"{tag} record {i} element {j}: {len(b)} bytes against a "
                    f"stride of {size}. The stride is what the next record's "
                    f"offset is computed from, so a wrong one silently "
                    f"reinterprets the rest of the table.")
            out += b
    return bytes(out)


#: The part element's fields BY SIZE, as (name, 'I' u32 | 'f' f32) in file
#: order.  Every entry restates a layout established above, and nothing new is
#: named here:
#:
#:    8   `read_simo`'s ``{partId, textureId}``
#:   16   `read_mesh`'s ``{mesh, texture, mixtex, packed}``
#:   32   +the four-float ``mix`` group (`section_span`'s MESX branch, and the
#:        loader's ``fld1/fst/fst/fstp`` + ``fldz/fstp`` default that says the
#:        fourth is a float at 0.0f and not an always-zero u32)
#:   40   +``tex2``/``tex3``      (SIM4/MESY in the loader's ladder)
#:   48   +``unk40``/``unk44``    (SIM5/MESZ; zero in 9,841 of 9,841 parts)
#:   60   `SIM6_FIELDS` -- NOT the 48 with a tail bolted on; three ids widen
#:        to 64 bits and their high halves sit INSIDE the record
#:
#: **SIZES 40 AND 48-AS-SIM5 HAVE NO SAMPLE IN THIS CORPUS** (0 of 35 files
#: carry a `MESY`, `SIM3`, `SIM4` or `SIM5`), so those names come from the
#: loader ladder alone.  The 48-byte row IS exercised, by `MESZ` -- 38
#: sections, 33,556 records.
_PART_16 = (("mesh", "I"), ("texture", "I"), ("mixtex", "I"), ("packed", "I"))
_PART_MIX = (("mix0", "f"), ("mix1", "f"), ("mix2", "f"), ("mix3", "f"))
PART_FIELDS = {
    8: (("part", "I"), ("texture", "I")),
    16: _PART_16,
    32: _PART_16 + _PART_MIX,
    40: _PART_16 + _PART_MIX + (("tex2", "I"), ("tex3", "I")),
    48: _PART_16 + _PART_MIX + (("tex2", "I"), ("tex3", "I"),
                                ("unk40", "I"), ("unk44", "I")),
    60: (("mesh", "I"), ("texture", "I"), ("mixtex", "I"), ("mixtex_hi", "I"),
         ("packed", "I")) + _PART_MIX + (
        ("tex2", "I"), ("tex2_hi", "I"), ("tex3", "I"), ("tex3_hi", "I"),
        ("unk52", "I"), ("unk56", "I")),
}
#: The 60-byte layout is `SIM6_FIELDS` and must stay it: `read_sim6` names the
#: same slots and the two drifting apart is how one reader and one writer come
#: to disagree about the same bytes.
assert tuple(n for n, _ in PART_FIELDS[60]) == SIM6_FIELDS
assert set(PART_FIELDS) == set(PART_ELEMENT_SIZES.values())
for _sz, _f in PART_FIELDS.items():
    assert len(_f) * 4 == _sz, (_sz, _f)
del _sz, _f

#: One `struct.Struct` per element size, built from `PART_FIELDS`.
PART_STRUCTS = {sz: struct.Struct("<" + "".join(t for _, t in f))
                for sz, f in PART_FIELDS.items()}


def part_fields(raw: bytes) -> dict:
    """One raw part element -> its named fields, keyed by `PART_FIELDS`.

    The size selects the layout, which is what the client loader does: one
    function, one destination struct, the magic choosing only how many bytes
    to read.  `packed` is left as its u32 -- `read_sim6` splits it into
    ``{mixOpt, asb, adb, materialIndex}`` and that split is a view, not a
    field, so it is not round-tripped through here.
    """
    n = len(raw)
    if n not in PART_STRUCTS:
        raise ValueError(f"no part layout for a {n}-byte element")
    return dict(zip((k for k, _ in PART_FIELDS[n]), PART_STRUCTS[n].unpack(raw)))


def part_bytes(fields: dict, size: int) -> bytes:
    """`part_fields`' inverse: named fields -> the `size`-byte element.

    Refuses a u32 field out of range, and refuses a missing one rather than
    defaulting it -- a defaulted field is how an always-zero column becomes
    permanently zero in everything this writes.
    """
    if size not in PART_STRUCTS:
        raise SectionWriteError(f"no part layout for a {size}-byte element")
    vals = []
    for name, kind in PART_FIELDS[size]:
        if name not in fields:
            raise SectionWriteError(
                f"part element ({size} bytes): field {name!r} is missing. "
                f"Defaulting it would write a zero into a column nothing in "
                f"this corpus exercises.")
        v = fields[name]
        if kind == "I":
            v = int(v)
            if not 0 <= v <= 0xFFFFFFFF:
                raise SectionWriteError(
                    f"part element: {name}={v} does not fit a u32")
        vals.append(v)
    return PART_STRUCTS[size].pack(*vals)


# --------------------------------------------------------------------------
# EMOI, MATR, ROPT -- the fixed-record sections
# --------------------------------------------------------------------------

def _fixed_field(name, width: int, what: str, allow_high: bool = False) -> bytes:
    """`name` in a `width`-byte NUL-padded field, or a refusal.

    Mirrors `_fixed_name`, which is what reads these back, INCLUDING its
    `allow_high` split -- the writer's strictness has to match its own
    reader's per format, because a writer looser than its reader emits a
    section nothing here can read back and the rejection then arrives at the
    far end of a container rebuild.  `serialize_emoi` passes True (651 of
    3,824 shipped EMOI names are GBK); `serialize_matr` and `serialize_ropt`
    do not (0 of 1,984 shipped names carry a high byte).

    `width` bytes exactly are allowed, not `width - 1`.  `_fixed_name` accepts
    a field with no terminator at all -- ``partition`` simply finds no NUL --
    so refusing the full-width case here would make a shipped name
    unwritable.  MEASURED: no name in this corpus reaches the cap, so nothing
    currently depends on it either way; the two ends agreeing is the point.
    """
    b = name.encode("latin-1") if isinstance(name, str) else bytes(name)
    if len(b) > width:
        raise SectionWriteError(
            f"{what}: {len(b)} bytes for a {width}-byte field")
    bad = (any(c < 32 or c == 127 for c in b) if allow_high
           else not all(32 <= c < 127 for c in b))
    if bad:
        raise SectionWriteError(f"{what}: not printable ASCII: {b!r}")
    return b + b"\0" * (width - len(b))


def read_emoi_rows(data: bytes, off: int = 0) -> dict:
    """``EMOI`` in FILE ORDER, and readable at an offset inside a container.

    `read_emoi` returns ``{id: name}`` and requires the section to tile the
    whole file, which is right for a standalone ``EmotionIco.dbc`` and unusable
    for the same table sitting inside ``ini/c3.wdb`` -- where it does ship, on
    all 30 clients here, and where the end of the section is arithmetic rather
    than EOF.  This returns ``{"tag", "span", "rows": [{"id", "name"}]}``.

    Ids are not dense (6090 carries 0..67 plus 76 and 77) and nothing
    guarantees they are unique, so the order is kept.

    **NAMES ARE GBK ON HALF THE CORPUS AND THAT WAS FOUND HERE.**  651 of the
    3,824 EMOI name fields on this machine carry a high byte, and the
    printable-ASCII rule refused 13 of the 34 sections outright.  The one
    STANDALONE ``EmotionIco.dbc`` -- 6090's, the only sample `read_emoi` was
    ever pointed at -- is all-ASCII, which is exactly why the rule survived:
    the sample could not exercise it.  See `_fixed_name`.
    """
    tag = bytes(data[off:off + 4])
    if tag != b"EMOI":
        raise ValueError(f"not an EMOI section: magic {tag!r}")
    (count,) = struct.unpack_from("<I", data, off + 4)
    end = off + 8 + count * EMOI_ROW.size
    if end > len(data):
        raise ValueError(f"EMOI at 0x{off:x} declares {count} rows, which runs "
                         f"to {end} past a {len(data)}-byte buffer")
    rows = []
    for i in range(count):
        eid, raw = EMOI_ROW.unpack_from(data, off + 8 + i * EMOI_ROW.size)
        rows.append({"id": eid,
                     "name": _fixed_name(raw, f"EMOI row {i}",
                                         allow_high=True)})
    return {"tag": "EMOI", "span": end - off, "rows": rows}


def serialize_emoi(table: dict) -> bytes:
    """Encode `read_emoi_rows`' output back to an ``EMOI`` section."""
    rows = table["rows"] if isinstance(table, dict) else table
    out = bytearray(b"EMOI" + struct.pack("<I", len(rows)))
    for i, r in enumerate(rows):
        eid = int(r["id"])
        if not 0 <= eid <= 0xFFFFFFFF:
            raise SectionWriteError(f"EMOI row {i}: id={eid} past u32")
        out += EMOI_ROW.pack(eid, _fixed_field(r["name"], 32, f"EMOI row {i}",
                                               allow_high=True))
    return bytes(out)


def serialize_matr(table) -> bytes:
    """Encode `read_matr`'s output back to a ``MATR`` section.

    Takes the reader's list of records, or ``{"records": [...]}``.  The five
    columns are written from ``fields`` -- the verbatim five u32 in FILE
    order -- and NOT from the named ``ambient``/``diffuse``/... keys, even
    though `read_matr` offers both.  The naming is a decode of what the client
    does with the columns; the file's contract is their order, and a writer
    that went through the names would silently reorder them if the naming were
    ever corrected.  A caller editing by name should write back through
    ``fields`` in `MATR_FIELDS` order.

    **THE MULTI-RECORD CASE IS UNMEASURED AND THIS WRITER SAYS SO RATHER THAN
    REFUSING.**  Every MATR in the corpus declares exactly ONE record, 33 of
    33 including the vendor tool's files, so 52 has never been multiplied by
    anything but 1 and the stride is INFERRED from ``32 + 5*4``.  A second
    record is the first test of it -- see the `section_span` MATR branch.
    """
    recs = table["records"] if isinstance(table, dict) else table
    out = bytearray(b"MATR" + struct.pack("<I", len(recs)))
    for i, r in enumerate(recs):
        fields = tuple(r["fields"])
        if len(fields) != 5:
            raise SectionWriteError(
                f"MATR record {i}: {len(fields)} columns, the record is "
                f"char[32] plus exactly five u32")
        for j, v in enumerate(fields):
            if not 0 <= int(v) <= 0xFFFFFFFF:
                raise SectionWriteError(
                    f"MATR record {i} column {j} ({MATR_FIELDS[j]}): {v} does "
                    f"not fit a u32")
        out += MATR_RECORD.pack(
            _fixed_field(r["name"], 32, f"MATR record {i}"),
            *(int(v) for v in fields))
    return bytes(out)


def serialize_ropt(table: dict) -> bytes:
    """Encode `read_ropt`'s output back to a ``ROPT`` section.

    ``dumyCount`` is written from the length of ``dumies``, not from any
    stored value, for `_effe_header_bytes`' reason: it is a SECTION-level
    count sitting at 0x08, ahead of the records, so a declared value that
    disagreed with the list would move the record table's end and desynchronise
    the dumy table -- the exact defect that made this section read wrong twice
    (see the `section_span` ROPT branch).

    This arm is exercised: 30 sections, 350 records, ``recordCount`` taking
    8/10/12/14/15 and ``dumyCount`` 7/29/30/35/37/55, so neither count is a
    constant the writer could be accidentally fitting.
    """
    parts = table["parts"]
    dumies = table["dumies"]
    out = bytearray(b"ROPT" + struct.pack("<II", len(parts), len(dumies)))
    for i, p in enumerate(parts):
        out += _fixed_field(p["part"], 32, f"ROPT record {i} partName")
        out += _fixed_field(p["mesh_ini"], 256, f"ROPT record {i} meshIni")
        out += _fixed_field(p["motion_ini"], 256, f"ROPT record {i} motionIni")
    for i, d in enumerate(dumies):
        idx = int(d["index"])
        if not 0 <= idx <= 0xFFFFFFFF:
            raise SectionWriteError(f"ROPT dumy {i}: index={idx} past u32")
        out += ROPT_DUMY.pack(idx, _fixed_field(d["name"], 32,
                                                f"ROPT dumy {i}"))
    return bytes(out)


# --------------------------------------------------------------------------
# The dispatcher -- one section in, one section out
# --------------------------------------------------------------------------
#: magic -> (parse, serialize).  A standalone ``.dbc`` file IS one section, so
#: this pair is also the whole-member reader/writer: ``serialize_section(
#: parse_section(Path(x).read_bytes()))`` must reproduce the file.
_SECTION_CODECS = {
    b"RSDB": (read_row_table, serialize_row_table),
    b"RSDC": (read_row_table, serialize_row_table),
    b"EMOI": (read_emoi_rows, serialize_emoi),
    b"MATR": (lambda d, off=0: {"tag": "MATR", "records": read_matr(d, off)},
              serialize_matr),
    b"ROPT": (lambda d, off=0: dict(read_ropt(d, off), tag="ROPT"),
              serialize_ropt),
    b"EFFE": (lambda d, off=0: {"tag": "EFFE",
                                "records": read_effe(_slice(d, off))},
              lambda t: serialize_effe(t["records"])),
    b"FE32": (lambda d, off=0: {"tag": "FE32",
                                "records": read_fe32(_slice(d, off))},
              lambda t: serialize_fe32(t["records"])),
}
for _tag in PART_ELEMENT_SIZES:
    _SECTION_CODECS[_tag] = (read_parts, serialize_parts)
del _tag

#: Every magic that can be WRITTEN, i.e. every magic `section_span` can walk.
#: The two sets being equal is asserted by the gate: a magic the container walk
#: steps over but nothing can author is exactly the hole this half closes, and
#: it would otherwise reappear silently the next time a branch is added.
SECTION_MAGICS = tuple(sorted(_SECTION_CODECS))
#: The same set as ``str``, which is what a section table's ``tag`` field and
#: every report column carry.
SECTION_MAGICS_STR = tuple(t.decode() for t in SECTION_MAGICS)


def _slice(data: bytes, off: int) -> bytes:
    """The EFFE/FE32 readers take a whole table and assert it ends on EOF.
    Inside a container the end is `section_span`'s arithmetic, so cut it out
    first.  This is a seam, not a second parser: the span comes from the one
    walk everything else uses.
    """
    return data[off:off + section_span(data, off)] if off else data


def parse_section(data: bytes, off: int = 0) -> dict:
    """One section at `off` -> a dict carrying its magic under ``"tag"``.

    Raises `UnknownSection` for a magic with no codec, for the reason
    `section_span` does: a caller that guessed past one would land mid-record
    and read plausible garbage.
    """
    tag = bytes(data[off:off + 4])
    codec = _SECTION_CODECS.get(tag)
    if codec is None:
        raise UnknownSection(f"no section codec for {tag!r} at 0x{off:x}")
    return codec[0](data, off)


def serialize_section(table: dict) -> bytes:
    """`parse_section`' inverse -- the bytes of one section, header included.

    ``assert serialize_section(parse_section(blob)) == blob`` is the whole
    contract, and `tests/test_wdb_section_write.py` asserts it per magic over
    every section in the corpus, refusing a magic it never met.
    """
    tag = table["tag"]
    tagb = tag.encode("latin-1") if isinstance(tag, str) else bytes(tag)
    codec = _SECTION_CODECS.get(tagb)
    if codec is None:
        raise SectionWriteError(f"no section codec for {tagb!r}")
    return codec[1](table)
