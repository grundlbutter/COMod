#!/usr/bin/env python3
r"""
inidat.py -- which cipher an official client's ``ini/*.dat`` is under, and
what can be read back out of it.

`tqdat` reads the ONE cipher it knows (the TQ File Cipher) and raises on
everything else.  That was the whole story for 5065 and 6090, where a handful
of tables are TQ and the rest are plain.  **7878 ships 200 `ini/*.dat` under six
different treatments**, and a reader that knows one of them reports the other
five as "wrong seed" -- which is how `docs/dat_family_7878.md` came to
record 139 tables as one undifferentiated opaque mass.  This module names the
treatment first, then reads what is readable, and REFUSES by name what is not.

WHAT A CALLER GETS
------------------
`classify(data, name)` -> a `Verdict`: the family, the evidence that put it
there, and nothing invented.  `read(data, name)` -> a `Read`: full content for
the two families that decrypt, the decoded tail for the block-cipher family,
and an explicit refusal carrying its reason for the family that is gone.

    from inidat import classify, read, Family
    v = classify(path.read_bytes(), path.name)
    v.family                      -> 'rsa-mysqldump'
    read(path.read_bytes()).content[:21]  -> b'<?xml version="1.0"?>'

THE FAMILIES, measured over 7878's `ini/` (200 files, 193 non-empty; the other
7 are ZERO BYTES, which is its own answer -- see `docs/dat_family_7878.md` §5,
where seven "byte-identical files" turned out to be `sha256(b"")`).  Counts
below are this module's own `classify` run over that directory and are locked
by `tests/test_inidat.py`:

    block96                153   the 12-byte block cipher, key unknown  TAIL ONLY
    rar-mangled             11   codepage-destroyed RAR archives        REFUSED
    rsa-mysqldump           10   decrypts, gunzips, and IS xml          READ
    plaintext                7   never encrypted, already text          READ
    empty                    7   zero bytes                             (named)
    binary-plain             6   never encrypted, binary records        READ (raw)
    tq-stream                2   the ordinary TQ cipher at seed 9527    READ
    unknown                  2   nothing here establishes what it is    (named)
    shift-obfuscated         1   every byte +k over an INI; k falls out READ
    block96-candidate-refuted 1  passed entry, tail refutes it          REFUSED

**`plaintext` was 8 until 2026-08-12, and the eighth was a MISTAKE this module
made rather than a file that changed.**  `Play.dat` is an INI with every byte
shifted by +6; the shift leaves it at 0.85 printable, so `looks_like_text`
claimed it and the module reported *"already text; no cipher applied"* -- a
false positive, where a consumer reads `aYzgxzc` and gets nothing.  On other
builds the same file fell through to `unknown`, which at least refuses
honestly.  See `SHIFTED` and the ordering comment in `classify`.

1. RSA -- SOLVED, and it is the wiki's published key
--------------------------------------------------
Ten files decrypt under the patch-5517 **public** modulus that
`refs/conquer-online-wiki-mdbook/src/security/rsa.md` publishes (extracted from
the shipped client binary; an RSA public key is public by design).  256-byte
blocks, `pow(c, e, n)`, strip PKCS#1 padding, concatenate, gunzip.

VERIFIED here, not assumed: all ten produce a gzip stream that inflates, and
every one of the ten begins ``<?xml version="1.0"?>\r\n<mysqldump``.  They are
mysqldump XML -- TQ shipped database dumps into the client.  Sizes 1,024 B to
6,656 B in, 6,724 B to 249,876 B out.

**The key is stable across patches**, measured rather than assumed: the same
modulus opens `MyAnimate.dat` on **5517, 6090, 6609 and 7878** (1,280 B in each
case, inflating to 6,867 / 6,869 / 6,905 / 6,901 B of XML -- four different
files, one key).  The three older clients here (5017, 5065, 5165) ship no
`MyAnimate.dat` at all, so this says nothing about them.

**Size alone does not classify it.**  Every RSA file's length is a multiple of
256, but so is that of `coat_storage_attr.dat`, `dict_lottery.dat` and
`gouyu_immortal.dat`, which are block96.  13 files match the size test and only
10 are RSA, so `classify` ATTEMPTS the decryption and believes the result,
rather than reading the length and guessing.

2. TQ stream cipher at seed 9527 -- two files, not one
-----------------------------------------------------
`UserHelpInfo.ini.dat` (559,414 B) decrypts under `tqdat` at the standard seed
to ``[Header]\r\nHelpInfoAmount=867\r\n\r\n[DefaultInfo]``.  It was never a new
cipher; it was simply never tried.

**`kok_roleview.dat` (129 B) is a SECOND file in this family** and was missed by
the classification this module was built from, which recorded one.  Found by
running the test over every file instead of the expected list.  It decrypts to a
four-column `@@` table with its own header row::

    nation@@face_female@@face_male@@armor
    1@@1501@@1001@@180010
    2@@1502@@1002@@180020

That is not a coincidence surviving a printability threshold -- it is a header
naming four columns and rows carrying four fields.  `kok_roleview.dat` does not
exist in 6609.

Note `UserHelpInfo.dat` (948,722 B, no `.ini`) is NOT in this family; it is
block96.  The two names differ by one infix and by their whole cipher.

3. block96 -- a 12-byte block cipher whose key is not known
----------------------------------------------------------
The bulk of the directory.  **The key is unknown and is not recoverable by
search** (measured elsewhere: full avalanche, non-affine, a 2^96 keyspace).  Two
other workstreams own that problem -- the binary RE of the cipher and the
plaintext dictionary -- and NOTHING in this module attempts cryptanalysis.

What this module can do is read the **tail**.  A file whose length is not a
multiple of 12 has `len % 12` bytes left over past the last whole block, and
those bytes are not enciphered at all -- they are only ``XOR 0x54``.  So the last
1-11 bytes of most of these tables are readable today:

    Monster.dat        -> b'Lock=-1\r\n\r\n'
    StageGoal.dat      -> b'E_12_8\r\n\r\n'
    Achievement.dat    -> b' null 0 1'
    ability_score.dat  -> b'00@@\r\n'

**The instrument was shown to fire before a count from it was reported.**  Of
the 154 block96 files, 140 have `len % 12 != 0`, and the XOR-0x54 tail is
printable on **140 of 140**.  The null: those same 140 tail LENGTHS filled with
random bytes, 300 trials, pass a mean of **5.7** times.  The other 14 files are
a whole number of blocks, so the test CANNOT fire -- they are recorded as
untested, not as negative (`Read.tail is None`, `Verdict.corroborated is
False`), and they are in this family BY ELIMINATION only.

**That bucket used to hold 15, and the fifteenth has now left it by a positive
test.**  The paragraph here used to say the bucket was *"known to contain at
least one file that does not belong: `LevelExp.dat`, which the wiki's 5517
table calls 'XOR with a hardcoded 27-byte key, unused remnant' -- a sixth
treatment this module does not implement and does not claim to."*  Naming it
was right; leaving it in `block96` was not, because `.family` returned the same
flat string for it as for a corroborated hit and **every consumer counted an
elimination as a measurement** -- on all seven builds and on CCO, one file
counted eight times across two separate lineages.

`_int32_run_rate` settles it without a key.  The file decodes as 129
little-endian int32s of which **119 of 128 adjacent pairs run the same
direction**, in two runs of 56 and 63; chance would be about `2**-119`.  No
block cipher emits that -- and neither does a repeating 27-byte XOR, which
would destroy the ordering just as thoroughly, so the wiki's line does not
describe these bytes as shipped either.  It is `binary-plain`.

What is NOT claimed: why the values are negative, or what the two runs mean.
The filename invites "experience curve" and that reading may well be right,
but this module establishes ORDER, not semantics, and stops there.

The remaining 14 are `itemtype.dat`, `instancetype.dat`, `award_config*.dat`
and similar tables whose block96 membership nothing here contradicts but
nothing here confirms.  They were re-tested for the same structure and sit at
**50-52%** monotonic, which is what random looks like, so the defect
`LevelExp.dat` exposed is one file wide rather than a property of the bucket.

SCOPE, stated: this tail test discriminates the block cipher from RANDOM.  It
does **not** discriminate it from PLAINTEXT -- ordinary text bytes XOR 0x54 land
in the printable band too (6 of the 7 plaintext files with `len % 12 != 0` also
"pass").  That is why plaintext is ruled out FIRST and the tail is only ever
corroboration for a file already known not to be text.

Its one measured false-positive mode is a **run of 0x00**, because
``0x00 ^ 0x54 == 'T'``.  `GameMap.dat` ends `.TUTT` for exactly that reason and
is not encrypted at all.  This is why the zero-rate test below runs first.

**A printable tail is necessary but NOT sufficient, and one file proves it.**
``ServerPlay.dat``'s 2-byte tail decodes to ``b',+'`` -- printable, and so it
was corroborated ``block96`` -- but it is bare punctuation, which a text table
never produces as content.  A real tail carries **characters**: an alnum byte
or a line-ending (``b'ide'``, ``b'com'``, ``b'@0@@@@'``, the ``\r\n`` rows).
So corroboration now requires that (`_tail_corroborates`), and a file that
passes the block96 entry test with a bare-punctuation tail is
``block96-candidate-refuted`` -- *called, and refuted by a positive test*,
rather than counted among the confirmed.  RE NewFunc's oracle independently
decrypts ``ServerPlay.dat`` to garbage (H=7.97), which is the refutation this
module cannot compute -- it holds no block96 key -- but can predict from a
corroboration too weak to be one.  ``LevelExp.dat`` is the OTHER file the
oracle rejects, but it has no tail (a whole number of blocks) and this module
has no in-charter test that refutes it, so it stays honestly ``block96,
uncorroborated, UNTESTED``; its refutation lives at the oracle layer, where the
key would live.  See ``docs/dat_family_7878.md``.

4. rar-mangled -- REFUSED, and the refusal is the finding
--------------------------------------------------------
Ten `c3*np.dat` files plus `effanl.dat`.  These were RAR archives and a
**codepage round-trip destroyed them**, so there is nothing to decode and the
honest answer is a refusal rather than a reader that fails.  The evidence:

* **`0x00` is entirely absent from all eleven** -- 0 zero bytes in 68,569.
  No binary container is zero-free; a text pipeline is what strips them.
* **`0x3F` `'?'` sits at 9.132%** across the eleven, against **0.259%** over the
  other 182 non-empty `.dat` in the same directory and 0.391% for uniform.  A
  35x enrichment of exactly the byte a codepage converter emits for
  "unrepresentable" is a substitution, not a distribution.
* All eleven are **byte-identical between 6609 and 7878**, so the damage is
  upstream of both and shipped that way.

**`c3aanp.dat` is an ELEVENTH member and was not in the ten-file list this
module was built from.**  It is found, not asserted: 0 zero bytes in 3,530, `'?'`
at 5.50% (14x the population), 6609-identical.  It was missed because it is the
smallest and least `'?'`-saturated of the family and sits below an entropy bar --
the same size confound described in §5.  A classifier that tested rather than
matched names is what surfaced it.

**CORRECTION to the finding this module was built from, which matters because it
was the load-bearing evidence.**  That finding said patching byte 6 from `0x20`
to `0x00` restores a valid RAR4 signature "on the family".  Measured across all
eleven: **exactly one, `c3bbnp.dat`, carries `Rar!\x1a\x07\x20` at offset 0**;
the other ten contain no `Rar!` at any offset.  So "these were RAR" rests on ONE
surviving header plus the shared fingerprint above -- still a good inference
[I], but one witness and a fingerprint, not eleven headers.  The refusal does
not depend on it either way: zero-free, `'?'`-saturated bytes are unrecoverable
whatever they once were.

5-6. Never encrypted -- and why entropy must not be the test
------------------------------------------------------------
8 files are already text (`Shop.dat` is plain INI, `Tips.dat`, `Play.dat`, ...).
5 more are plain BINARY records that no cipher ever touched, and they are told
apart by their **zero-byte rate**, which separates cleanly with a real gap:

    AutoAllot.dat 80.5% · Pet.Dat 70.3% · GameMap.dat 23.9% ·
    pna_swf.dat 17.1% · Action.dat 15.2%   |   next highest 1.9%

Everything enciphered sits near 1/256 (median 0.379%).  The threshold is 5%, in
the middle of a 15.2%-to-1.9% gap -- not tuned to a target.

**`docs/dat_family_7878.md` split this directory by Shannon entropy at H>7.5 and
got 118 opaque files.  That threshold is confounded by SIZE**: `renown.dat` is 32
bytes, so its entropy cannot exceed log2(32) = 5 no matter how random it is, and
it lands under the bar while being ordinary block96 (its tail decodes to
``0@@0@@\r\n``).  The 118 reconciles as "block96 files big enough to look random"
and undercounts the family by 37.  Nothing here uses entropy.

`unknown` (2 files) is the bucket this module refuses to guess about:
`WeaponActionData.dat` and `WeaponMotionData.dat` are not text, not zero-rich,
and their tails do NOT decode -- so the block96 test actively REFUTES them
rather than staying silent.  Named, not absorbed into the family they failed.

CLI::

    py -3 core/inidat.py census DIR          # classify every .dat under DIR
    py -3 core/inidat.py read PATH [-o OUT]  # decode one file, or refuse
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tqdat

#: The patch-5517 RSA **public** modulus, published in
#: `refs/conquer-online-wiki-mdbook/src/security/rsa.md` and reproduced here so
#: this module has no runtime dependency on the refs tree.  Extracted from the
#: shipped client binary by the wiki's authors; a public key is public by
#: design.  VERIFIED here against 7878 (10 files), and against 5517/6090/6609
#: `MyAnimate.dat`, so it is not patch-specific.
RSA_MODULUS = int(
    "bef5bd339b6bac0c957fa68ec010a7d7c38a2b03a9d2084f0e107b2644e246b3"
    "fab03ac76235ae40a0731714783d49caa99ac78a8a39d8b944f168b3aea3b216"
    "2220f2a7444735e07e70a66dd7843c899b64e6a5ee88e4f87b255c2395299899"
    "296e043ea19b6b9b38dfbf671a80a77693fedc7030be7b241726d208010a8dd3"
    "9780e60c2d47dedaa720f56517657eed9e88fe1c9b37591599210ab095e4c251"
    "bd9ea7faf4450cb15a5078a2093cf112e99f34648154d2cc94c38392d724f0fa"
    "fc629e70cd1b97ee4eb82c11c2b0954cef918560ef9f2c7da60b33e767f5d626"
    "cd3d0a6082a06650e54926be71b66e39c33b6e18b7c703830b87a2e4d8805409", 16)

#: The standard public exponent; the client uses no other.
RSA_EXPONENT = 65537

#: RSA ciphertext block size, in bytes (2048-bit).
RSA_BLOCK = 256

#: The block cipher's block size, in bytes.  Established elsewhere; used here
#: only to locate the unenciphered tail, never to attempt the cipher.
BLOCK96 = 12

#: The tail past the last whole block is XOR-ed with this and nothing else.
TAIL_XOR = 0x54

#: Zero-byte rate above which a file is plain binary records rather than
#: ciphertext.  Sits in the measured gap between 15.2% (`Action.dat`, the
#: lowest never-encrypted file) and 1.9% (the highest enciphered one).
ZERO_RATE_PLAIN = 0.05

#: `'?'` rate above which a file is codepage-mangled.  Measured: 8.2-10.0% on
#: the ten, 0.259% over the other 183.  Set at 4%, a factor of two below the
#: lowest member and an order of magnitude above the population.
QMARK_RATE_MANGLED = 0.04

#: Built once: `bytes.translate` is C-speed and `_shift_recovers_ini` runs on
#: every file in a census.
_SHIFT_TABLES = [bytes((b - k) & 0xFF for b in range(256)) for k in range(256)]
_INI_KEY = re.compile(rb"(?m)^[A-Za-z_][A-Za-z0-9_]{0,31}=")
_INI_SECTION = re.compile(rb"(?m)^\[[ -~]{1,40}\]\r$")


def _shift_recovers_ini(data: bytes, probe: int = 512):
    """`(k, plain, sections, keys)` if subtracting a constant yields a real
    INI, else None.

    SELF-VERIFYING, which is what earns it a place ahead of the eliminating
    tests: it does not ask *"does this look shifted"*, it asks *"does
    un-shifting produce an INI"* -- and requires STRUCTURE to say yes: a
    `[section]` at offset 0, two or more section headers, three or more
    `key=` lines.

    **Structure, deliberately NOT `looks_like_text`.**  Verifying with the
    printable-ratio heuristic would inherit the exact blind spot this family
    exists to fix, and measurably does: 5065's `Play.dat` is 634 B with a
    higher GBK density, so its CORRECTLY decoded INI scores 0.842 and the
    heuristic rejects it.  Four builds of five would have looked like a
    complete result.  **A fix verified by the instrument it repairs cannot
    detect the class of failure it was written for.**

    Cost is load-bearing too: `k` is found on a 512-byte prefix and only the
    surviving candidate is expanded over the whole file.  The naive
    all-shifts-over-all-bytes form took over two minutes on a census; this
    takes 0.7 s over 381 files.  **A classifier that is correct and slow gets
    deleted by whoever next runs the census in a loop, and the removal looks
    like a performance fix.**
    """
    head = data[:probe]
    for k in range(1, 256):
        if not head.translate(_SHIFT_TABLES[k]).startswith(b"["):
            continue
        plain = data.translate(_SHIFT_TABLES[k])
        secs = len(_INI_SECTION.findall(plain))
        keys = len(_INI_KEY.findall(plain))
        if secs >= 2 and keys >= 3:
            return k, plain, secs, keys
    return None


class Family:
    """The treatment a `.dat` is under.  String constants, not an enum, to
    match how the rest of COre spells its small closed sets."""

    RSA = "rsa-mysqldump"
    TQ = "tq-stream"
    BLOCK96 = "block96"
    #: Passed the block96 ENTRY test (opaque, not text/RSA/TQ/binary/mangled)
    #: and then REFUTED by a positive test -- the classifier saying "I called
    #: this and I was wrong", which is worth more than a family two files wide
    #: that never admits the miss.  Today's one member is `ServerPlay.dat`,
    #: whose 2-byte tail `,+` is printable but bare punctuation; RE NewFunc's
    #: oracle decrypts it to garbage (H=7.97), the refutation this module
    #: cannot compute (it holds no block96 key) but can PREDICT from a
    #: corroboration too weak to be one.  Distinct from `UNKNOWN`, whose tail
    #: does not decode at all, and from an uncorroborated `BLOCK96`, which
    #: could not be tested rather than tested and failed.
    BLOCK96_REFUTED = "block96-candidate-refuted"
    #: A byte-wise ADDITIVE shift over an ordinary INI -- `Play.dat` ships
    #: this on every build we hold, at k=6.  Named apart from PLAINTEXT
    #: because it is NOT plaintext (the raw bytes read `aYzgxzc`, not
    #: `[Start]`), and apart from the ciphers because there is nothing to
    #: recover: the key falls out in 255 tries and the result verifies itself.
    SHIFTED = "shift-obfuscated"
    MANGLED = "rar-mangled"
    PLAINTEXT = "plaintext"
    BINARY = "binary-plain"
    UNKNOWN = "unknown"
    EMPTY = "empty"


class Irrecoverable(Exception):
    """This file's content is destroyed, and here is why.

    Raised by `read_content` rather than returned, because a caller asking for
    bytes must not be able to mistake a refusal for an empty file.  The
    `Read` returned by `read()` carries the same reason without raising.
    """


@dataclass
class Verdict:
    """Which family, and the evidence that put it there."""

    family: str
    reason: str
    #: True only where a POSITIVE test fired (a decryption that produced its
    #: expected content, or a tail that decoded).  False means the family was
    #: reached by elimination or the confirming test could not run -- which is
    #: not the same as a negative and is not recorded as one.
    corroborated: bool = False
    evidence: dict = field(default_factory=dict)


@dataclass
class Read:
    """What could be got out of a file, and what could not."""

    family: str
    #: Full decoded content, for the families that decode.  None otherwise.
    content: Optional[bytes] = None
    #: The decoded trailing bytes, for `block96` where `len % 12 != 0`.  None
    #: where the file is a whole number of blocks -- the test cannot fire, and
    #: an empty bytes would read as "fired and found nothing".
    tail: Optional[bytes] = None
    refused: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# the two ciphers this module can actually undo
# ---------------------------------------------------------------------------

def rsa_decrypt(data: bytes, modulus: int = RSA_MODULUS,
                exponent: int = RSA_EXPONENT) -> bytes:
    """RSA-public-decrypt then gunzip, or `ValueError`.

    The wiki's procedure, with the error handling its deliberately minimal
    snippet omits: a length that is not a whole number of blocks, a block with
    no PKCS#1 padding terminator, and a payload that is not a gzip stream are
    each a refusal rather than a traceback or a plausible-looking wrong answer.
    That matters because `classify` uses this as its RSA TEST -- it must fail
    cleanly on the three block96 files whose length is also a multiple of 256.
    """
    if not data or len(data) % RSA_BLOCK:
        raise ValueError(
            f"not a whole number of {RSA_BLOCK}-byte RSA blocks "
            f"({len(data)} bytes)")
    payload = bytearray()
    for i in range(0, len(data), RSA_BLOCK):
        c = int.from_bytes(data[i:i + RSA_BLOCK], "big")
        chunk = pow(c, exponent, modulus).to_bytes(RSA_BLOCK, "big")
        end = chunk.find(b"\x00", 2)
        if end < 0:
            raise ValueError(f"block at {i}: no PKCS#1 padding terminator")
        payload += chunk[end + 1:]
    try:
        return zlib.decompress(bytes(payload), wbits=47)
    except zlib.error as e:
        raise ValueError(f"decrypted payload is not a gzip stream: {e}") from e


def tail_decode(data: bytes) -> Optional[bytes]:
    """The `len % 12` unenciphered trailing bytes, or None if there are none.

    None means the test CANNOT FIRE on this file, not that it fired and found
    nothing -- the distinction the block96 count depends on.
    """
    n = len(data) % BLOCK96
    if not n:
        return None
    return bytes(b ^ TAIL_XOR for b in data[-n:])


def _printable(data: bytes) -> bool:
    return bool(data) and all(32 <= b < 127 or b in (9, 10, 13) for b in data)


def _tail_corroborates(tail: bytes) -> bool:
    """Does a printable tail actually look like a text-table tail?

    Printability is necessary but not sufficient: the block96 tables are
    text (ids, ``@@`` delimiters, ``\\r\\n`` rows), so a genuine tail carries
    **characters** -- an alphanumeric byte, or a line-ending.  A tail that is
    all punctuation is the documented false-positive mode: short random bytes
    XOR ``0x54`` land in the printable band ~5.7 times in 300 (module
    docstring, section 3).

    Measured across four installs (7878/6609/6090/5517): of every corroborated
    block96 tail, exactly ONE carries neither an alnum nor a line-ending --
    ``ServerPlay.dat``'s ``b',+'`` -- and genuine short tails all pass
    (``b'ide'``, ``b'com'``, ``b'@0@@@@'`` with its digit, the ``\\r\\n`` rows).
    So this refutes one file and keeps every real one; it is a property of a
    text tail, not a hand-picked set of bytes.
    """
    return any(b in _ALNUM or b in b"\r\n" for b in tail)


#: Digits and ASCII letters, for `_tail_corroborates`.
_ALNUM = frozenset(bytes(range(48, 58)) + bytes(range(65, 91))
                   + bytes(range(97, 123)))


def _rate(data: bytes, byte: int) -> float:
    return data.count(byte) / len(data) if data else 0.0


#: Smallest int32 count `_int32_run_rate` will judge.  With `n` comparisons a
#: high monotonic rate by chance costs roughly `2**-n`, so this is well past
#: the point where the result could be luck; `LevelExp.dat` carries 129.
MIN_INT32_ROWS = 64

#: A file whose int32 decode runs this monotonic is an ordered numeric table,
#: not ciphertext.  **The threshold sits in a measured empty band, not near
#: either population.**  Over all 134 testable `ini/*.dat` on all seven builds
#: (a file is testable when `len % 4 == 0` and it holds at least
#: `MIN_INT32_ROWS` int32s):
#:
#:     LevelExp.dat                    0.9766   x7, byte-identical copies
#:     ---------------------------- empty from 0.5909 to 0.9766 ------------
#:     7878/newslot_roulette.dat       0.5909   block96, and STAYS block96
#:     6609/magictypeex.dat            0.5905   tq-stream
#:     Action.dat                      0.5698   x7, binary-plain
#:
#: The nearest thing below the band is itself a real `block96` file, which is
#: the discrimination that matters: this test must refuse the 139 files on
#: 7878 whose `block96` rests on the positive tail test, and it does -- the
#: closest any of them comes is 0.5909 against 0.9766.  A structure test that
#: promotes everything is the failure mode.
INT32_RUN_PLAIN = 0.90


def _int32_run_rate(data: bytes):
    """How monotonic the little-endian int32 decode is, or None if untestable.

    An ordered numeric table -- an experience curve, a level ladder -- reads as
    int32s that march in one direction.  A block cipher cannot produce that:
    `LevelExp.dat`'s 119 strictly-decreasing steps out of 128 would cost about
    `2**-119` by chance.

    This exists because `block96` was being reported for a file the cipher test
    could not fire on.  Its positive component is a `len % 12 != 0` tail, and
    `LevelExp.dat` is 516 bytes, so the family was reached by ELIMINATION and
    `.family` returned the same flat string it returns for a corroborated hit.
    Elimination against five other tests is not evidence for the sixth
    (`docs/CORRECTIONS.md` C-2026-08-13-inidat-block96-by-elimination).
    """
    if len(data) % 4 or len(data) // 4 < MIN_INT32_ROWS:
        return None
    n = len(data) // 4
    vals = struct.unpack(f"<{n}i", data)
    down = up = 0
    for i in range(n - 1):
        if vals[i] > vals[i + 1]:
            down += 1
        elif vals[i] < vals[i + 1]:
            up += 1
    return max(down, up) / (n - 1)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def classify(data: bytes, name: str = "<bytes>") -> Verdict:
    """Which family `data` belongs to, by TEST rather than by filename.

    `name` is carried into messages only.  No branch reads it: two files here
    differ by one infix and by their whole cipher (`UserHelpInfo.ini.dat` is
    the TQ stream, `UserHelpInfo.dat` is block96), and `emoneyShopV3.dat` and
    `emoneyShopV4.dat` are byte-identical at 1,399,671 B, so a name-keyed
    classifier would be wrong in both directions.

    Order is deliberate.  The self-verifying tests -- a decryption that must
    produce its own expected content -- run before the eliminating ones, and
    plaintext is ruled out before the tail test because the tail test does not
    discriminate against text (see the module docstring, §3).
    """
    if not data:
        return Verdict(Family.EMPTY, "zero bytes", True, {"size": 0})

    ev = {"size": len(data), "zero_rate": _rate(data, 0x00),
          "qmark_rate": _rate(data, 0x3F), "mod12": len(data) % BLOCK96}

    # A SHIFTED INI, and it MUST be tested before the plaintext branch.
    #
    # **The plaintext test cannot see this and will claim it.**  Shifting ASCII
    # by a small constant leaves almost every byte inside the printable band --
    # `Play.dat` sits at 0.85 raw -- so `looks_like_text` returns True on the
    # OBFUSCATED bytes and the file is reported as "already text; no cipher
    # applied".  **That is a FALSE POSITIVE, not a gap**: measured on 6609 and
    # 7878, where a consumer trusting the verdict reads `aYzgxzc` and
    # gets nothing.  On 5065/5517/6090 it fell through to `unknown`, which at
    # least refuses honestly.
    #
    # This module's own doctrine already asks for this order -- self-verifying
    # tests before eliminating ones -- and the docstring's reason for putting
    # plaintext first (the tail test does not discriminate against text) is
    # exactly why plaintext must not be first: **the test placed first to
    # prevent a misclassification is the one that cannot tell obfuscated text
    # from text.**
    #
    # It must also precede MANGLED, which would otherwise have a claim: these
    # files carry zero 0x00 bytes and a '?' rate of 0.79-0.84% against the
    # directory's 0.259% baseline, which is the mangled shape exactly.
    hit = _shift_recovers_ini(data)
    if hit is not None:
        k, plain, secs, keys = hit
        ev["shift"] = k
        return Verdict(
            Family.SHIFTED,
            f"every byte shifted by +{k}; subtracting it yields an INI with "
            f"{secs} sections and {keys} keys -- self-verified by structure, "
            f"not inferred from a printable ratio",
            True, ev)

    # Already text.  First among the ELIMINATING tests, because the tail test
    # would also "pass" on it -- but no longer first overall; see above.
    if tqdat.looks_like_text(data):
        return Verdict(Family.PLAINTEXT, "already text; no cipher applied",
                       True, ev)

    # Codepage-mangled.  Before RSA and TQ because it is a refusal, and a
    # refusal that runs after two failed decryptions reads as "we tried and
    # could not", which is a weaker and different claim than "this is gone".
    if data.count(0x00) == 0 and ev["qmark_rate"] >= QMARK_RATE_MANGLED:
        return Verdict(
            Family.MANGLED,
            f"codepage-mangled: no 0x00 in {len(data)} bytes and "
            f"'?' at {ev['qmark_rate']:.2%} against 0.259% over the rest of "
            f"the directory -- about 1 byte in "
            f"{round(1 / ev['qmark_rate'])} was replaced by '?'; "
            f"not recoverable",
            True, ev)

    # RSA: attempted, never inferred from the length.
    if len(data) % RSA_BLOCK == 0:
        try:
            out = rsa_decrypt(data)
        except ValueError as e:
            ev["rsa_rejected"] = str(e)
        else:
            ev["inflated"] = len(out)
            return Verdict(Family.RSA,
                           f"RSA-decrypts and gunzips to {len(out)} bytes",
                           True, ev)

    # The ordinary TQ stream cipher.
    if tqdat.looks_like_text(tqdat.decrypt(data, tqdat.SEED)):
        return Verdict(Family.TQ,
                       f"TQ File Cipher at seed {tqdat.SEED} yields text",
                       True, ev)

    # Never enciphered, binary records.  Before the tail test, whose one
    # measured false-positive mode is a run of 0x00 (0x00 ^ 0x54 == 'T').
    if ev["zero_rate"] > ZERO_RATE_PLAIN:
        return Verdict(Family.BINARY,
                       f"{ev['zero_rate']:.1%} zero bytes -- plain binary "
                       f"records, not ciphertext", True, ev)

    # An ordered int32 table.  MUST come before the tail test, because the
    # tail test's `None` branch is an ELIMINATION and would claim this file
    # first -- which is exactly what it did to `LevelExp.dat` on all seven
    # builds, and on CCO, for as long as this module has existed.
    run = _int32_run_rate(data)
    if run is not None and run >= INT32_RUN_PLAIN:
        ev["int32_run"] = round(run, 4)
        return Verdict(Family.BINARY,
                       f"{run:.1%} of adjacent little-endian int32 pairs run "
                       f"the same direction over {len(data) // 4} values -- an "
                       f"ordered numeric table, which no block cipher emits",
                       True, ev)

    tail = tail_decode(data)
    if tail is None:
        return Verdict(
            Family.BLOCK96,
            f"opaque and a whole number of {BLOCK96}-byte blocks; the tail "
            f"test cannot fire on this file, so the family is by elimination",
            False, ev)
    if _printable(tail):
        ev["tail"] = tail
        if _tail_corroborates(tail):
            return Verdict(Family.BLOCK96,
                           f"tail of {len(tail)} bytes XOR 0x54 decodes to "
                           f"{tail!r}", True, ev)
        # Printable but bare punctuation: passes the entry test, and the one
        # thing that could confirm it -- a tail that reads like a text-table
        # row -- does not.  Called and refuted, not corroborated, and not
        # silently counted among the confirmed.
        return Verdict(
            Family.BLOCK96_REFUTED,
            f"passes the block96 entry test, but its {len(tail)}-byte tail "
            f"{tail!r} is printable-yet-bare-punctuation (no alnum, no "
            f"line-ending) -- the false-positive mode, not a text-table "
            f"tail; the oracle confirms it decrypts to garbage",
            False, ev)

    ev["tail_rejected"] = tail
    return Verdict(Family.UNKNOWN,
                   f"not text, not RSA, not TQ, not zero-rich, and its "
                   f"{len(tail)}-byte tail does NOT decode ({tail!r}) -- the "
                   f"block96 test refutes it rather than being silent",
                   False, ev)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def read(data: bytes, name: str = "<bytes>") -> Read:
    """Everything recoverable from `data`, and a reason where nothing is.

    Never raises for a refusal -- `Read.refused` carries it -- so a caller
    sweeping a directory gets one shape back for every file.  `read_content`
    is the strict twin for callers that need bytes or an exception.
    """
    v = classify(data, name)
    if v.family == Family.RSA:
        return Read(v.family, content=rsa_decrypt(data), reason=v.reason)
    if v.family == Family.TQ:
        return Read(v.family, content=tqdat.decrypt(data, tqdat.SEED),
                    reason=v.reason)
    if v.family in (Family.PLAINTEXT, Family.BINARY):
        return Read(v.family, content=data, reason=v.reason)
    if v.family == Family.MANGLED:
        return Read(v.family, refused=True, reason=v.reason)
    if v.family == Family.BLOCK96:
        why = ("the 12-byte block cipher's key is not known and is not "
               "recoverable by search; only the unenciphered tail past the "
               "last whole block is readable")
        if len(data) % BLOCK96 == 0:
            why += (" -- and this file is a whole number of blocks, so it has "
                    "no tail and nothing here confirms the family")
        return Read(v.family, tail=tail_decode(data), refused=True, reason=why)
    if v.family == Family.BLOCK96_REFUTED:
        # Refused like block96 (no key), but carrying the bare tail that
        # refuted it, so a reader sees the evidence rather than a bare verdict.
        return Read(v.family, tail=tail_decode(data), refused=True,
                    reason=v.reason)
    return Read(v.family, refused=True, reason=v.reason)


def read_content(data: bytes, name: str = "<bytes>") -> bytes:
    """Full decoded content, or `Irrecoverable` naming why there is none."""
    r = read(data, name)
    if r.content is None:
        raise Irrecoverable(f"{name}: {r.reason}")
    return r.content


def read_path(path: Path | str) -> Read:
    """`read` for a file on disk.  Never writes; COre does not touch installs."""
    p = Path(path)
    return read(p.read_bytes(), p.name)


def census(directory: Path | str) -> dict:
    """`{family: [filename, ...]}` over every `.dat` in `directory`."""
    out: dict = {}
    for p in sorted(Path(directory).glob("*.dat")):
        out.setdefault(classify(p.read_bytes(), p.name).family,
                       []).append(p.name)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Classify and read an official client's ini/*.dat")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("census", help="classify every .dat in a directory")
    c.add_argument("directory", type=Path)
    c.add_argument("--verbose", action="store_true",
                   help="one line per file with its reason")

    r = sub.add_parser("read", help="decode one file, or refuse with a reason")
    r.add_argument("path", type=Path)
    r.add_argument("-o", "--out", type=Path,
                   help="explicit output path; this tool never writes into a "
                        "game install on its own")
    r.add_argument("--limit", type=int, default=400)

    a = ap.parse_args(argv)

    if a.cmd == "census":
        table = census(a.directory)
        total = sum(len(v) for v in table.values())
        for fam in sorted(table):
            print(f"  {fam:<16} {len(table[fam]):>4}")
        print(f"  {'total':<16} {total:>4}")
        if a.verbose:
            for p in sorted(Path(a.directory).glob("*.dat")):
                v = classify(p.read_bytes(), p.name)
                mark = "+" if v.corroborated else "?"
                print(f"{mark} {p.name:<34} {v.family:<14} {v.reason}")
        return 0

    res = read_path(a.path)
    print(f"{a.path.name}: {res.family}")
    print(f"  {res.reason}")
    if res.tail is not None:
        print(f"  tail: {res.tail!r}")
    if res.refused:
        return 3
    if a.out:
        a.out.write_bytes(res.content)
        print(f"  wrote {len(res.content)} bytes to {a.out}")
    else:
        head = res.content[:a.limit]
        print(f"  {len(res.content)} bytes; first {len(head)}:")
        print(head.decode("latin-1"))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
