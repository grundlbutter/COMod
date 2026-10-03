#!/usr/bin/env python3
r"""
wdbpack.py -- the ``ini/c3.wdb`` CONTAINER: its file-entry index, and how to
write one.

`core/wdb.py` reads c3.wdb as a header plus a run of back-to-back sections.
That is true, and it is half the file. The other half is the **file-entry
index** that starts at the u32 at offset 0x08 -- the byte the section walk
lands on -- and it is **obfuscated**, which is why nothing had read it::

    0x00  char[4]  "BDMG"
    0x04  u32      file size
    0x08  u32      index offset   == end of the section area
    0x0c  u32      entry count
    0x10  ...      the members, back to back, no padding
    [0x08] entryCount x 84 bytes, EACH ENCIPHERED SEPARATELY:
              0x00  u32       name hash (the map key; entries are in
                              ascending hash order)
              0x04  u32       offset of the member from the start of the file
              0x08  u32       size
              0x0c  u32       size again -- equal in 33 of 33 files
              0x10  u32       zero in 33 of 33
              0x14  char[64]  member name, e.g. "ini/RolePart.dbc"

**Every "section" is a named member.** The magic the walk sees is the
member's own first four bytes, so the index is what says ``MATR`` came from
``ini/Material.dbc`` and ``SIM6`` from ``ini/3DSimpleObjEx.dbc``. That
mapping was previously inferred from filename similarity; here it is read
out of the file.

MEASURED, ``py -3 tools/wdbwrite.py verify`` -- 30 client ``ini/c3.wdb`` plus
the vendor tool's three (``C3Tools/ini/c3.wdb``, its ``.old``, and
``empty-env-template``), **33 files, and every column clean on all 33**:
the name hash recomputes from the decoded name (that is the anti-vacuity
check -- a wrong key or a wrong cipher gives 0 of 33, not 33 of 33), every
name is printable ASCII, ``size == size2``, the fifth u32 is zero, the index
is exactly ``count * 84`` bytes long, the members tile ``0x10 ..
indexOffset`` with **no gap and no overlap**, and ``sum(size)`` equals
``indexOffset - 16``.

**THE FORMAT DID NOT CHANGE IN TEN YEARS.** The codec below is lifted from a
2013 binary and decodes 7878's 2023 index unaltered. That is the reason a
writer is possible at all.

WHERE IT CAME FROM, so the claims are re-runnable:

    crypt table   WdbExtractor.dll RVA 0x14260 -- the Storm/MPQ
                  ``PrepareCryptTable``: 0x500 entries, five rows of 0x100,
                  seed 0x00100001, ``seed = (seed*125 + 3) % 0x2AAAAB``.
                  **The table is in BSS, not on disk** -- reading it out of
                  the file image gets whatever follows the raw .data, which
                  is how a first attempt decoded exactly one dword per entry
                  correctly and looked half-right.
    name hash     WdbExtractor.dll RVA 0x14350, called with seed offset 0,
                  i.e. ``cryptTable[0x000 + toupper(ch)]``.
    decode        WdbExtractor.dll RVA 0x142e0 == WdbGenerater.dll 0x160d0.
    encode        WdbGenerater.dll RVA 0x16060 -- the same loop with the
                  state fed the PLAINTEXT instead of the output, which makes
                  the pair exact inverses.
    loader        WdbExtractor.dll RVA 0x145d0 -- 16-byte header, ``fseek``
                  to header+8, then ``fread`` of 0x54 per entry followed by
                  the decode call with n = 0x15 dwords.

The binaries are ``C3Tools/ini_backup_chinese/`` (md5
``c7bac099f0ac7da5e2cfc9d8bce104d3`` and ``77f003c03ef1d63dfa999b8e46bb588b``),
which are the pristine 2013 vendor copies; the folder-root copies were
patched by a third party in 2026 and differ. Neither was executed.

See ``docs/wdb_packer_re_2026-09-06.md``.
"""
from __future__ import annotations

import struct
from typing import Iterable, NamedTuple

MAGIC = b"BDMG"
HEADER = struct.Struct("<4sIII")
ENTRY_SIZE = 84
ENTRY_HEAD = struct.Struct("<5I")
NAME_OFF = 0x14
NAME_LEN = 64
BODY_OFF = HEADER.size          # members start immediately after the header
_M = 0xFFFFFFFF

#: Cipher initial state, from the two ``mov`` at the top of both codecs.
_KEY0 = 0xEFFEAABB
_ACC0 = 0xEEEEEEEE
#: Hash initial state (Storm's).
_H1 = 0x7FED7FED
_H2 = 0xEEEEEEEE


def crypt_table() -> list:
    """Storm's 0x500-entry table. WdbExtractor.dll RVA 0x14260."""
    t = [0] * 0x500
    seed = 0x00100001
    for i1 in range(0x100):
        i2 = i1
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            hi = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            t[i2] = hi | (seed & 0xFFFF)
            i2 += 0x100
    return t


_TABLE = crypt_table()
#: The cipher indexes row 4 (RVA 0x1ac10 == the table base 0x19c10 + 0x1000).
_CIPHER_ROW = _TABLE[0x400:0x500]


def name_hash(name: str) -> int:
    """The index key: Storm's string hash, uppercased, seed offset 0.

    WdbExtractor.dll RVA 0x14350; both call sites (0x14806, 0x148db) push 0
    for the seed argument, so only row 0 of the table is used.

    The uppercasing is done on BYTES, not on the Python string: the routine
    calls C ``toupper``, which in the C locale touches ``a``-``z`` and nothing
    else, whereas ``str.upper()`` would fold ``\xb5`` to a Greek capital Mu
    and then fail to encode it. Every real member name is ASCII, so the two
    agree on the corpus -- but the byte form is the one that matches the
    binary, and it is also what keeps this from raising on a name that came
    out of a bad decode.
    """
    s1, s2 = _H1, _H2
    raw = name.encode("latin-1", "replace") if isinstance(name, str) else bytes(name)
    for ch in raw.upper():
        s1 = _TABLE[ch] ^ ((s1 + s2) & _M)
        s2 = (ch + s1 + s2 + ((s2 << 5) & _M) + 3) & _M
    return s1


def _cipher(dwords: Iterable[int], feed_output: bool) -> list:
    """The shared loop. ``feed_output`` selects decode (RVA 0x142e0) over
    encode (RVA 0x16060); both feed the PLAINTEXT into the state, which for
    the decoder is its output and for the encoder is its input."""
    key, acc = _KEY0, _ACC0
    out = []
    for c in dwords:
        acc = (acc + _CIPHER_ROW[key & 0xFF]) & _M
        o = ((acc + key) & _M) ^ c
        out.append(o)
        plain = o if feed_output else c
        key = ((key >> 11) | ((((~key & _M) << 21) & _M) + 0x11111111)) & _M
        acc = (acc + plain + ((acc << 5) & _M) + 3) & _M
    return out


def decode_entry(raw: bytes) -> bytes:
    """Decipher one 84-byte index entry."""
    if len(raw) != ENTRY_SIZE:
        raise ValueError(f"entry must be {ENTRY_SIZE} bytes, got {len(raw)}")
    return struct.pack("<21I", *_cipher(struct.unpack("<21I", raw), True))


def encode_entry(plain: bytes) -> bytes:
    """Encipher one 84-byte index entry. Exact inverse of `decode_entry`."""
    if len(plain) != ENTRY_SIZE:
        raise ValueError(f"entry must be {ENTRY_SIZE} bytes, got {len(plain)}")
    return struct.pack("<21I", *_cipher(struct.unpack("<21I", plain), False))


class Member(NamedTuple):
    """One packed ``.dbc``, as the index describes it."""
    name: str
    off: int
    size: int
    hash: int
    #: The index stores the size twice and then a zero. Both are carried so a
    #: rewrite is byte-exact even if a file ever disagrees with itself; the
    #: 64-byte name field's bytes after the NUL are carried for the same
    #: reason -- the vendor tool leaves uninitialised stack there and it is
    #: NOT all zero.
    size2: int
    zero: int
    name_pad: bytes


class Pack:
    """A parsed ``ini/c3.wdb`` container.

    ``members`` is in index order, which is ascending name hash -- the order
    a ``std::map<u32, entry>`` iterates, not the order the members sit in the
    file. Use `by_offset` for the physical order.
    """

    def __init__(self, data: bytes):
        if len(data) < HEADER.size:
            raise ValueError(f"too short to be a c3.wdb: {len(data)} bytes")
        magic, self.declared_size, self.index_off, self.count = \
            HEADER.unpack_from(data, 0)
        if magic != MAGIC:
            raise ValueError(f"not a c3.wdb: magic {magic!r}")
        need = self.index_off + self.count * ENTRY_SIZE
        if need > len(data):
            raise ValueError(
                f"index at 0x{self.index_off:x} declares {self.count} entries, "
                f"running to {need} past a {len(data)}-byte buffer")
        self.data = data
        self.members = []
        for i in range(self.count):
            b = decode_entry(data[self.index_off + i * ENTRY_SIZE:
                                  self.index_off + (i + 1) * ENTRY_SIZE])
            h, off, size, size2, zero = ENTRY_HEAD.unpack_from(b, 0)
            field = b[NAME_OFF:NAME_OFF + NAME_LEN]
            name, _, pad = field.partition(b"\0")
            self.members.append(Member(name.decode("latin-1"), off, size, h,
                                       size2, zero, pad))

    # -- reading -------------------------------------------------------
    def blob(self, m: Member) -> bytes:
        return self.data[m.off:m.off + m.size]

    def by_offset(self):
        return sorted(self.members, key=lambda m: m.off)

    def by_name(self):
        return {m.name: m for m in self.members}

    def magics(self) -> dict:
        return {m.name: bytes(self.data[m.off:m.off + 4]) for m in self.members}

    # -- integrity, and each of these is a column in the corpus table --
    def audit(self) -> dict:
        n = len(self.members)
        phys = self.by_offset()
        cur, tiles = BODY_OFF, True
        for m in phys:
            if m.off != cur:
                tiles = False
                break
            cur += m.size
        return dict(
            count=n,
            hash_ok=sum(1 for m in self.members if name_hash(m.name) == m.hash),
            name_ok=sum(1 for m in self.members
                        if m.name and all(32 <= c < 127
                                          for c in m.name.encode("latin-1"))),
            size_pair_ok=sum(1 for m in self.members if m.size == m.size2),
            zero_ok=sum(1 for m in self.members if m.zero == 0),
            index_len_ok=(len(self.data) - self.index_off) == n * ENTRY_SIZE,
            declared_size_ok=self.declared_size == len(self.data),
            tiles=tiles and cur == self.index_off,
            covered_ok=sum(m.size for m in self.members) == self.index_off - BODY_OFF,
            sorted_ok=all(self.members[i].hash < self.members[i + 1].hash
                          for i in range(n - 1)),
        )

    def clean(self) -> bool:
        a = self.audit()
        n = a["count"]
        return (a["hash_ok"] == n and a["name_ok"] == n and a["size_pair_ok"] == n
                and a["zero_ok"] == n and a["index_len_ok"] and a["tiles"]
                and a["covered_ok"] and a["sorted_ok"] and a["declared_size_ok"])


def build(members: Iterable[tuple]) -> bytes:
    """Serialise a container from ``(name, blob)`` pairs, or from
    ``(name, blob, pad)`` triples where ``pad`` is the 63-byte-or-less tail
    the vendor tool leaves after the name's NUL.

    Members are laid out in the order given, back to back from 0x10, with no
    alignment: the corpus shows 33 of 33 files tiling exactly, so there is no
    padding rule to reproduce. The index is then written in ascending name
    hash, which is the order the vendor tool's ``std::map`` produces.

    **A name collision in the hash is a hard error**, not a silent drop: the
    vendor tool keys its map on the hash, so two members hashing equal would
    lose one, and a writer that reproduced that would be reproducing a bug.
    """
    body = bytearray()
    entries = []
    seen = {}
    for item in members:
        if len(item) == 3:
            name, blob, pad = item
        else:
            (name, blob), pad = item, b""
        raw = name.encode("latin-1")
        if len(raw) >= NAME_LEN:
            raise ValueError(f"member name {name!r} does not fit in 63 bytes")
        h = name_hash(name)
        if h in seen:
            raise ValueError(f"name hash collision: {name!r} and {seen[h]!r} "
                             f"both hash to 0x{h:08x}")
        seen[h] = name
        off = BODY_OFF + len(body)
        body += blob
        field = raw + b"\0" + pad
        field = field[:NAME_LEN].ljust(NAME_LEN, b"\0")
        entries.append((h, ENTRY_HEAD.pack(h, off, len(blob), len(blob), 0) + field))

    index_off = BODY_OFF + len(body)
    total = index_off + len(entries) * ENTRY_SIZE
    out = bytearray(HEADER.pack(MAGIC, total, index_off, len(entries)))
    out += body
    for _, plain in sorted(entries, key=lambda e: e[0]):
        out += encode_entry(plain)
    return bytes(out)


def rebuild(pack: Pack) -> bytes:
    """Re-serialise a parsed container in its own physical order.

    This is the round-trip gate: for a file the vendor tool wrote,
    ``rebuild(Pack(data)) == data`` must hold byte for byte.
    """
    return build([(m.name, pack.blob(m), m.name_pad) for m in pack.by_offset()])
