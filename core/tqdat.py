#!/usr/bin/env python3
r"""
tqdat.py -- the TQ File Cipher and the encrypted ``ini/*.dat`` tables.

Official clients ship their item and monster tables encrypted; CCO ships the
same data as plain ``ini/itemtype.json`` / ``ini/monster.json``.  This module
reads the encrypted twins so the tools work on an official root too.

THE CIPHER (documented in refs/conquer-online-wiki-mdbook/src/security/
tqfile.md, VERIFIED here: ``encrypt(decrypt(x)) == x`` byte-for-byte on all
four .dat files below, and every decryption yields the expected text).  A
128-byte key is drawn from Microsoft's classic ``rand()`` LCG:

    n      = seed * 0x343FD + 0x269EC3          (seed <- n & 0xFFFFFFFF)
    key[i] = ((n >> 16) & 0x7FFF) % 0x100

then per byte i:  decrypt = rotate-right(c ^ key[i % 128], i % 8)
                  encrypt = rotate-left(p, i % 8) ^ key[i % 128]

Every TQ-cipher table in the wiki's 5517 manifest uses seed 9527 (0x2537)
except levexp.dat (1234), and 9527 opens all four files verified here:

    6090 ini/itemtype.dat   24,270 rows        6090 ini/Monster.dat  1,008 sections
    5065 ini/itemtype.dat    6,865 rows        5065 ini/Monster.dat    418 sections

``Server.dat`` is NOT this cipher -- the wiki marks it RSA-encrypted with a
key baked into the client, and seed 9527 turns the 6090 copy into noise.  It
is out of scope here.

ITEMTYPE.DAT decrypts to CRLF text, one item per line, in one of two layouts
told apart by their separator:

* ``@@``-separated with a trailing ``@@`` (5517+; the 6090 file): 66 fields
  on 24,269 of 24,270 rows.  The first 59 are the wiki's 5517 field list;
  the last 7 are a 6090 addition, meaning unestablished, preserved unnamed.
* space-separated under an ``Amount=N`` header line (classic; the 5065
  file): 39 fields on 6,864 of 6,865 rows, and N matches the row count
  exactly.  This layout has NO ``data`` column -- the magic block is one
  field shorter than 5517's -- and its four fields after attackSpeed are
  unestablished, preserved unnamed.

Rows are returned as dicts keyed like CCO's ``itemtype.json`` so they are a
drop-in for ``coassets.load_items``.  Column meanings were VERIFIED against
that file, not assumed: over the 5,711 item ids the 6090 table shares with
CCO, every informative column agrees at its named position (profession
5429, weaponSkill 5711, level 5318, sex 5709, strength 5711, monopoly 5599,
price 5274, defense 5333, magic1..3 5697+, magicAttack 5612, attackRange
5524, attackSpeed 5487, description 340 of the 676 with one) and the
disagreements are patch-era data changes (e.g. 111303 "IronHelmet" renamed
"SteelHelmet"), not shifted columns.  The same sweep over the 5,281 shared
5065 ids pins that layout's shifted tail (magicAttack at 29, magicDefense
30, attackRange 31, attackSpeed 32 -- each 5,276+ of 5,281).  For columns
that are near-constant zero the sweep cannot distinguish neighbours, so
their names rest on the anchored order, and the block between attackSpeed
and the verified description column carries the wiki's names on positional
grounds alone.

A row whose field count is not the layout's is malformed at the source (one
per file: 6090's 3005368 carries a stray newline mid-description, 5065's
725015 a stray space) and contributes ``id`` and ``name`` only -- those two
columns lead every line -- because positions past the damage are unreliable.

MONSTER.DAT decrypts to TQ-ini text: ``[Name]`` sections of ``key=value``
render properties (TypeID, ZoomPercent, SizeAdd, MaxLife, BornAction, ...),
GBK comment lines starting ``//`` in the 6090 file.  Sections are returned
shaped like CCO's ``monster.json`` rows (``type`` = TypeID, ``name`` = the
section name) with the full section preserved under ``raw``.  VERIFIED: all
1,008 6090 sections carry a TypeID, and of the 374 CCO rows sharing one,
355 name the same monster (the 19 others are renames like StoneMonster ->
StoneGrinder).  26 duplicate section names exist in 6090 (22 duplicate
TypeIDs); the first occurrence wins, which is what kept those 355 in
agreement.  Two sections claim a comma-separated *list* of 15 TypeIDs each
and become one row per id; a handful of values are empty strings at the
source (``Level=``) and stay that way rather than being invented as zeros.

Text is decoded latin-1 like ``coassets.parse_ini``: GBK stays as raw bytes
round-trippable through latin-1 rather than being guessed at.

Usage::

    from tqdat import decrypt, read_itemtype, read_monster
    items = read_itemtype(root / "ini" / "itemtype.dat")
    items[0]["id"], items[0]["name"]        -> 50000, 'SpeedArrow'
    mons = read_monster(root / "ini" / "Monster.dat")
    mons[0]["type"], mons[0]["name"]        -> 7714, 'Ripper'

CLI (never writes into a game install -- output paths are explicit)::

    py -3 core/tqdat.py show PATH [--limit N]
    py -3 core/tqdat.py decrypt PATH -o OUT [--seed 9527]
    py -3 core/tqdat.py encrypt PATH -o OUT [--seed 9527]
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

#: The one seed every TQ-cipher table in these installs uses (wiki: all of
#: the 5517 manifest except levexp.dat, which takes 1234).
SEED = 9527


# ---------------------------------------------------------------------------
# cipher
# ---------------------------------------------------------------------------

def keystream(seed: int = SEED, size: int = 128) -> bytes:
    """The key bytes Microsoft's classic ``rand()`` yields from ``seed``."""
    out = bytearray(size)
    for i in range(size):
        n = seed * 0x343FD + 0x269EC3
        seed = n & 0xFFFFFFFF
        out[i] = ((n >> 16) & 0x7FFF) % 0x100
    return bytes(out)


def decrypt(data: bytes, seed: int = SEED) -> bytes:
    key = keystream(seed)
    out = bytearray(len(data))
    for i, b in enumerate(data):
        x = b ^ key[i % 128]
        r = i % 8
        out[i] = ((x >> r) | (x << (8 - r))) & 0xFF
    return bytes(out)


def encrypt(data: bytes, seed: int = SEED) -> bytes:
    key = keystream(seed)
    out = bytearray(len(data))
    for i, b in enumerate(data):
        r = i % 8
        out[i] = (((b << r) | (b >> (8 - r))) & 0xFF) ^ key[i % 128]
    return bytes(out)


def looks_like_text(data: bytes, sample: int = 4096) -> bool:
    """Whether a decryption came out as the text these tables are.

    The GBK comment lines in 6090's Monster.dat still leave its first 4 KiB
    98.7% printable, while a wrong-seed decryption sits near 40%, so the
    threshold has room on both sides.
    """
    head = data[:sample]
    if not head:
        return False
    ok = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    return ok / len(head) >= 0.85


# ---------------------------------------------------------------------------
# itemtype.dat
# ---------------------------------------------------------------------------

#: Columns 0..27, shared by both layouts and named as CCO's itemtype.json
#: keys.  ``idAction`` is the one column CCO has no twin for (its json drops
#: it); the name is the wiki's.
_FIELDS_COMMON = (
    "id", "name", "requiredProfession", "requiredWeaponSkill",
    "requiredLevel", "requiredSex", "requiredStrength", "requiredAgility",
    "requiredVitality", "requiredSpirit", "monopoly", "weight", "price",
    "idAction", "attackMax", "attackMin", "defense", "dexterity", "dodge",
    "life", "mana", "amount", "amountLimit", "ident", "gem1", "gem2",
    "magic1", "magic2",
)

#: The @@ layout (5517+): the wiki's 59 columns.  A ``None`` entry is a
#: column whose meaning is unestablished; its value is preserved under the
#: row's ``unnamed`` dict rather than given a guessed name.
FIELDS_AT = _FIELDS_COMMON + (
    "magic3", "data", "magicAttack", "magicDefense", "attackRange",
    "attackSpeed", "frayMode", "repairMode", "typeMask", "emoneyPrice",
    "emoneyBoundPrice", "expiryTime", "soulAtk1", "soulAtk2", "soulDef1",
    "soulAtk3", "soulDef2", "soulAtk4", "soulDef3", "maxStackSize",
    "elemResMetal", "elemResWood", "elemResWater", "elemResFire",
    "elemResEarth", "itemType", "description", "qualityColor",
    "dragonsoulPhase", "dragonsoulReq", "cropQuality",
)

#: The classic space layout (5065): no ``data`` column, and the four fields
#: after attackSpeed are unestablished.
FIELDS_SPACE = _FIELDS_COMMON + (
    "magic3", "magicAttack", "magicDefense", "attackRange", "attackSpeed",
    None, None, None, None, "itemType", "description",
)

#: Fields that stay strings even when their value happens to be digits.
_STRING_FIELDS = {"name", "itemType", "description"}


def _row(fields: list, names: tuple) -> dict:
    out: dict = {}
    unnamed: dict = {}
    for i, v in enumerate(fields):
        name = names[i] if i < len(names) else None
        if name is None:
            unnamed[i] = v
            continue
        if name in _STRING_FIELDS:
            out[name] = v
        else:
            try:
                out[name] = int(v)
            except ValueError:
                out[name] = v
    if unnamed:
        out["unnamed"] = unnamed
    return out


def parse_itemtype(text: str) -> list[dict]:
    """Decrypted itemtype text -> rows keyed like CCO's itemtype.json.

    The layout is told from the text itself: lines carrying ``@@`` are the
    5517+ record form; otherwise it is the classic space-separated form,
    whose ``Amount=N`` header is checked against the row count rather than
    trusted or ignored.

    The file's record width is whatever most of its rows agree on; a row of
    any other width is malformed at the source (see the module docstring)
    and positions past the damage are unreliable, so it contributes only
    the leading id/name pair.
    """
    split_rows: list[list] = []
    names: tuple = ()
    amount: Optional[int] = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if "@@" in line:
            fields = line.split("@@")
            if fields and fields[-1] == "":
                fields = fields[:-1]
            split_rows.append(fields)
            names = FIELDS_AT
        elif amount is None and not split_rows and line.startswith("Amount="):
            amount = int(line.split("=", 1)[1] or 0)
        else:
            split_rows.append(line.split())
            names = FIELDS_SPACE
    if amount is not None and amount != len(split_rows):
        raise ValueError(
            f"header says Amount={amount}, file carries {len(split_rows)} rows")
    widths = Counter(len(f) for f in split_rows)
    mode = widths.most_common(1)[0][0] if widths else 0
    return [_row(f, names if len(f) == mode else names[:2])
            for f in split_rows]


# ---------------------------------------------------------------------------
# Monster.dat
# ---------------------------------------------------------------------------

#: Monster.dat key -> monster.json key, the shape tools/models.py reads.
_MONSTER_KEYS = (
    ("TypeID", "type"), ("SizeAdd", "sizeAdd"), ("ZoomPercent", "zoomPercent"),
    ("MaxLife", "maxLife"), ("Level", "level"), ("BornAction", "bornAction"),
    ("BornEffect", "bornEffect"), ("BornSound", "bornSound"),
    ("ActResCtrl", "actResCtrl"), ("ASB", "asb"), ("ADB", "adb"),
    ("BodyType", "bodyType"),
    # The part slots. These are the monster's ADDITIONAL ART: per
    # docs/appearance_path.md the client feeds them to SetPart 5/6/7/9/8, and
    # each resolves through armet/weapon/armor.ini to a Mesh and a Texture. They
    # were reaching callers only inside `raw`, so anything reading the json
    # shape saw a monster as a bare body.
    ("Armet", "armet"), ("ArmetColor", "armetColor"),
    ("RWeapon", "rWeapon"), ("LWeapon", "lWeapon"),
    ("LWeaponColor", "lWeaponColor"),
    ("Misc", "misc"), ("Mount", "mount"),
)


def parse_monster(text: str) -> list[dict]:
    """Decrypted Monster.dat text -> rows shaped like CCO's monster.json.

    ``//`` comment lines are skipped; the first of duplicate section names
    wins (see the module docstring); the full section survives under
    ``raw`` because the .dat carries fields (AntiType, Armet, Boss, ...)
    the json shape has no slot for.
    """
    sections: dict[str, dict[str, str]] = {}
    cur: Optional[dict[str, str]] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if name in sections:            # first occurrence wins
                cur = None
                continue
            cur = {}
            sections[name] = cur
        elif "=" in line and cur is not None:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.strip()
    out: list[dict] = []
    for name, kv in sections.items():
        row: dict = {"name": name, "raw": kv}
        for datkey, jsonkey in _MONSTER_KEYS:
            if datkey not in kv:
                continue
            v = kv[datkey]
            try:
                row[jsonkey] = int(v)
            except ValueError:
                row[jsonkey] = v
        # A TypeID may be a comma-separated list -- two 6090 sections
        # (UndeadAhriman and its lowercase twin) each claim 15 ids this way.
        # The section applies to every id it names, so it becomes one row
        # per id; anything else non-numeric stays as it was stored.
        t = row.get("type")
        if isinstance(t, str) and "," in t and all(
                p.strip().isdigit() for p in t.split(",")):
            for part in t.split(","):
                out.append({**row, "type": int(part)})
        else:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# file loaders
# ---------------------------------------------------------------------------

def _read_text(path: Path, seed: int) -> str:
    plain = decrypt(Path(path).read_bytes(), seed)
    if not looks_like_text(plain):
        raise ValueError(
            f"{path}: decryption with seed {seed} did not yield text -- "
            f"wrong seed, or not a TQ-cipher file (Server.dat is RSA)")
    return plain.decode("latin-1")


def read_itemtype(path: Path | str, seed: int = SEED) -> list[dict]:
    """Decrypt and parse an itemtype.dat."""
    return parse_itemtype(_read_text(Path(path), seed))


def read_monster(path: Path | str, seed: int = SEED) -> list[dict]:
    """Decrypt and parse a Monster.dat."""
    return parse_monster(_read_text(Path(path), seed))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="TQ File Cipher and the encrypted ini/*.dat tables")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="decrypt and preview a table")
    s.add_argument("path", type=Path)
    s.add_argument("--seed", type=lambda v: int(v, 0), default=SEED)
    s.add_argument("--limit", type=int, default=10)

    for name, help_ in (("decrypt", "decrypt PATH to OUT"),
                        ("encrypt", "encrypt PATH to OUT")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("path", type=Path)
        c.add_argument("-o", "--out", type=Path, required=True,
                       help="explicit output path; this tool never writes "
                            "next to (or into) a game install on its own")
        c.add_argument("--seed", type=lambda v: int(v, 0), default=SEED)

    a = ap.parse_args(argv)
    if a.cmd == "decrypt":
        a.out.write_bytes(decrypt(a.path.read_bytes(), a.seed))
        return 0
    if a.cmd == "encrypt":
        a.out.write_bytes(encrypt(a.path.read_bytes(), a.seed))
        return 0

    text = _read_text(a.path, a.seed)
    lname = a.path.name.lower()
    if "itemtype" in lname:
        rows = parse_itemtype(text)
        print(f"{a.path.name}: {len(rows)} items")
        for r in rows[:a.limit]:
            print(f"  {r.get('id'):>9}  {r.get('name', '')}")
    elif "monster" in lname:
        rows = parse_monster(text)
        print(f"{a.path.name}: {len(rows)} monsters")
        for r in rows[:a.limit]:
            print(f"  {r.get('type', '?'):>6}  {r['name']}")
    else:
        for line in text.splitlines()[:a.limit]:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
