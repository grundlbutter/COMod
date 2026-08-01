r"""
tqhash.py -- the TQ Digital string hash used to key WDF archive entries,
recovered by static analysis of `TqPackageWdf.dll` (Classic Conquer 2.0,
bin/64, timestamp 2026-07-11T03:44:33Z, linker 14.51, PE32+ AMD64).

=============================================================================
PROVENANCE  (verified -- transcribed instruction-by-instruction from the image)
=============================================================================

  TqPackageWdf.dll RVA 0x3C40   `wdf_hash_string(const char* s)`
        buffer preparation + terminator padding + SRW lock
  TqPackageWdf.dll RVA 0x3E42   the hash core, `hash_words(const u32* m, u32 n)`
        (a mid-function entry; the .pdata record covering it starts earlier)

  Supporting normalisation routines, all three of which case-fold inline
  (they do NOT use the CRT tables at .rdata 0x20C20 / 0x20DA0 -- those two
  arrays are the stock MSVC `_tolower_tab_` / `_toupper_tab_` and are
  referenced only by the statically-linked CRT):

  RVA 0x3370  first_segment_lower(dst, src)
        copy `src` up to (not including) the first '/', folding 'A'-'Z' to
        lower case.  Used to pick which open package a path belongs to.
  RVA 0x3B10  normalise_path(dst, src)
        fold 'A'-'Z' to lower case AND rewrite '\\' -> '/'.  Returns false on
        a NULL/empty input.  This is the canonical form of an asset path.
  RVA 0x3BA0  normalise_and_hash(src)
        same normalisation as 0x3B10 into a 0x100 stack buffer, then 0x3C40.

  The case-fold test is open-coded in all three:
        lea edx, [rax-0x41]     ; c - 'A'
        cmp dl, 0x19            ; unsigned <= 25 ?
        jbe -> add al, 0x20     ; then c |= 0x20
  and the separator swap is
        cmp al, 0x5C            ; '\\'
        cmove ecx, r11d         ; r11d = 0x2F  '/'

  Only TqPackageWdf.dll contains the magic constants 0x9BE74448 /
  0x66F42C48 -- verified absent from TqPackage.dll, graphic.dll,
  GraphicData.dll, Role3D.dll and ImLauncher.exe.  There is exactly one
  implementation in the shipped client.

=============================================================================
VALIDATION  (verified -- against the shipped archives, not against itself)
=============================================================================

CONFIDENCE: CERTAIN.  Run `py -3 tools/verify_tqhash.py` and
`py -3 tools/wdf_names.py` to reproduce.

  * c3.wdf declares 10238 entries; hashing the relative POSIX paths of the
    loose files under `<root>/c3/` and matching against the index resolved
    **9932 of 10238 = 97.0%** of them.  The ~3% residue is content that does
    not exist loose on disk, not a failure of the hash.
  * data.wdf resolved 194/14519 -- expected, because data.wdf's contents are
    largely NOT mirrored loose on disk, so the wordlist simply lacks them.
  * Across a 53013-path candidate set the false-positive expectation for a
    24757-entry 32-bit hash set is ~0.3 collisions.  Observed: 588 hits on a
    naive first pass, 10126 after wordlist expansion.  There is no way to get
    those numbers from a wrong algorithm.

  The input that matches is the FULL archive-relative path INCLUDING the
  package/top-level segment, normalised by `normalise_path`:

        "C3\Weapon\410.c3"  ->  "c3/weapon/410.c3"  ->  0xA1AE0B00

  Hashing the path with the leading segment stripped resolves essentially
  nothing (1/53013), so the package name IS part of the hashed string even
  though it is also what selects the archive.

=============================================================================
THE DISASSEMBLY THIS IS TRANSCRIBED FROM
=============================================================================

Buffer preparation -- TqPackageWdf.dll @ 0x3C40 :

    003C40  push rbx / sub rsp,0x30
    003C61  lea  rcx,[0x2A018]
    003C68  call KERNEL32!AcquireSRWLockExclusive
    003C6F  mov  r8d,0x100                ; n = 256
    003C75  mov  rdx,rbx                  ; src = the string
    003C78  lea  rbx,[0x2ADB0]            ; dst = a 0x100-byte GLOBAL buffer
    003C7F  mov  rcx,rbx
    003C82  call 0x1CE10                  ; strncpy(dst, src, 256) -- zero-pads
    003C87  xor  edx,edx                  ; j = 0
    003C90  cmp  dword ptr [rbx+rdx*4],0  ; \  find the first all-zero u32,
    003C94  je   0x3CC1                   ;  |  unrolled x4, bounded at
    003C96  cmp  dword ptr [rbx+rdx*4+4],0;  |  edx < 0x40  (64 dwords = 256 B)
    ...                                   ;  |
    003CAE  cmp  edx,0x40                 ; /
    003CC4  mov  dword ptr [rbx+rax*4],  0x9BE74448     ; m[j]   = MAGIC_A
    003CCB  mov  dword ptr [rbx+rax*4+4],0x66F42C48     ; m[j+1] = MAGIC_B
    003CD3  add  edx,0x2                  ; n = j + 2
    003CD6  mov  rcx,rbx
    003CD9  call 0x3E42                   ; -> hash core
    003CE0  lea  rcx,[0x2A018]
    003CE7  call KERNEL32!ReleaseSRWLockExclusive
    003CED  mov  eax,ebx / ret

Hash core -- TqPackageWdf.dll @ 0x3E42 :

    003E4F  mov  dword ptr [rbp-0x8],edx  ; n
    003E52  mov  r8,rcx                   ; m
    003E55  mov  dword ptr [rbp-0x4],0xF4FA8928     ; v
    003E5C  mov  esi,0x37A8470E                     ; h1
    003E61  mov  edi,0x7758B42B                     ; h2
    003E66  xor  rcx,rcx                            ; i = 0
  .loop:
    003E69  mov  ebx,0x267B0B11
    003E6E  rol  dword ptr [rbp-0x4],1              ; v = rol32(v,1)
    003E71  xor  ebx,dword ptr [rbp-0x4]            ; k  = 0x267B0B11 ^ v
    003E74  mov  eax,dword ptr [r8+rcx*4]           ; w  = m[i]
    003E78  mov  edx,ebx
    003E7A  xor  esi,eax                            ; h1 ^= w
    003E7C  xor  edi,eax                            ; h2 ^= w
    003E7E  add  edx,edi                            ; t = k + h2
    003E80  or   edx,0x02040801
    003E86  and  edx,0xBFEF7FDF
    003E8C  mov  eax,esi
    003E8E  mul  edx                 ; edx:eax = h1 * t ; CF = (hi != 0)
    003E90  adc  eax,edx             ; lo + hi + CF          -> CF out
    003E92  mov  edx,ebx             ; (does not touch flags)
    003E94  adc  eax,0x0             ; + CF
    003E97  add  edx,esi             ; t2 = k + h1   (h1 still pre-multiply)
    003E99  or   edx,0x00804021
    003E9F  and  edx,0x7DFEFBFF
    003EA5  mov  esi,eax             ; h1 = result
    003EA7  mov  eax,edi
    003EA9  mul  edx                 ; edx:eax = h2 * t2
    003EAB  add  edx,edx             ; hi *= 2               -> CF
    003EAD  adc  eax,edx             ; lo + 2*hi + CF        -> CF
    003EAF  jae  .skip
    003EB1  add  eax,0x2
  .skip:
    003EB4  inc  ecx
    003EB6  mov  edi,eax             ; h2 = result
    003EB8  cmp  ecx,dword ptr [rbp-0x8]
    003EBB  jne  .loop
    003EBD  xor  esi,edi
    003EBF  mov  eax,esi / ret       ; return h1 ^ h2

Note the two `mul` blocks are the classic "multiply modulo 2**32-1" folding
step: `adc eax,edx` / `adc eax,0` folds the 64-bit product's high half back
into the low half, and the second block does the same with the high half
doubled.  This is the same construction as the 32-bit TQ client's
`string_id` / `_ui_hash`, re-emitted by a modern MSVC for x64.

=============================================================================
"""

from __future__ import annotations

import struct

MASK32 = 0xFFFFFFFF

# padding words appended after the string (verified, RVA 0x3CC4 / 0x3CCB)
MAGIC_A = 0x9BE74448
MAGIC_B = 0x66F42C48

# hash core seeds (verified, RVA 0x3E55 / 0x3E5C / 0x3E61)
SEED_V = 0xF4FA8928
SEED_H1 = 0x37A8470E
SEED_H2 = 0x7758B42B

# per-round key (verified, RVA 0x3E69)
ROUND_K = 0x267B0B11

# operand-conditioning masks (verified, RVA 0x3E80/0x3E86 and 0x3E99/0x3E9F)
OR_A, AND_A = 0x02040801, 0xBFEF7FDF
OR_B, AND_B = 0x00804021, 0x7DFEFBFF

# the fixed-size staging buffer strncpy'd into at RVA 0x3C82
BUFFER_BYTES = 0x100
MAX_WORDS = 0x40


def _rol32(x: int, n: int) -> int:
    x &= MASK32
    n &= 31
    return ((x << n) | (x >> (32 - n))) & MASK32


def normalise_path(s: str) -> str:
    """Canonical asset-path form: 'A'-'Z' folded to lower case and '\\'
    rewritten to '/'.  Nothing else is touched -- no trimming, no collapsing
    of duplicate separators, no drive handling.

    verified: TqPackageWdf.dll RVA 0x3B10 and 0x3BA0.
    """
    out = []
    for ch in s:
        c = ord(ch)
        if 0x41 <= c <= 0x5A:          # 'A'..'Z'
            out.append(chr(c + 0x20))
        elif c == 0x5C:                # '\\'
            out.append("/")
        else:
            out.append(ch)
    return "".join(out)


def first_segment(s: str) -> str:
    """The package selector: everything before the first '/', case-folded.

    verified: TqPackageWdf.dll RVA 0x3370, called from 0x33D0 which then
    hashes the result and looks it up in the open-package vector (0x3440,
    comparing against `package->id` at offset +8).
    """
    out = []
    for ch in s:
        c = ord(ch)
        if c == 0x2F or c == 0:        # '/'
            break
        out.append(chr(c + 0x20) if 0x41 <= c <= 0x5A else ch)
    return "".join(out)


def _prepare_words(data: bytes) -> list[int]:
    """Reproduce the staging buffer of RVA 0x3C40.

    strncpy(buf, s, 256) -- so the copy stops at the first NUL and the rest of
    the 256-byte buffer is zero-filled.  Then scan for the first all-zero u32
    and append the two magic words there.
    """
    if len(data) > BUFFER_BYTES:
        data = data[:BUFFER_BYTES]     # strncpy truncation, no NUL guaranteed
    buf = bytearray(BUFFER_BYTES)
    buf[:len(data)] = data
    words = list(struct.unpack("<%dI" % MAX_WORDS, bytes(buf)))
    j = MAX_WORDS
    for i in range(MAX_WORDS):
        if words[i] == 0:
            j = i
            break
    words = words[:j] + [MAGIC_A, MAGIC_B]
    return words


def hash_words(words) -> int:
    """The hash core, RVA 0x3E42.  `words` is the prepared u32 array."""
    v = SEED_V
    h1 = SEED_H1
    h2 = SEED_H2
    for w in words:
        k = ROUND_K
        v = _rol32(v, 1)
        k ^= v
        w &= MASK32
        h1 ^= w
        h2 ^= w

        # --- block A:  h1 = fold32(h1 * ((k + h2 | OR_A) & AND_A)) ---
        t = ((k + h2) & MASK32 | OR_A) & AND_A
        prod = h1 * t
        lo, hi = prod & MASK32, (prod >> 32) & MASK32
        cf = 1 if hi else 0                       # MUL sets CF = (hi != 0)
        s = lo + hi + cf                          # adc eax, edx
        acc = s & MASK32
        cf = 1 if s > MASK32 else 0
        acc = (acc + cf) & MASK32                 # adc eax, 0

        # --- block B:  h2 = fold32(h2 * ((k + h1_old | OR_B) & AND_B)) ---
        # `add edx,esi` at 0x3E97 runs BEFORE `mov esi,eax` at 0x3EA5, so the
        # h1 used here is the post-XOR / pre-block-A value.
        t2 = (((k + h1) & MASK32) | OR_B) & AND_B
        h1 = acc
        prod = h2 * t2
        lo, hi = prod & MASK32, (prod >> 32) & MASK32
        d = hi * 2                                # add edx, edx
        cf = 1 if d > MASK32 else 0
        d &= MASK32
        s = lo + d + cf                           # adc eax, edx
        acc2 = s & MASK32
        if s > MASK32:                            # jae .skip / add eax, 2
            acc2 = (acc2 + 2) & MASK32
        h2 = acc2
    return (h1 ^ h2) & MASK32


def tq_hash_raw(data) -> int:
    """Hash exactly these bytes -- NO normalisation.  Use this when you have
    already normalised, or when probing what the caller actually feeds in."""
    if isinstance(data, str):
        data = data.encode("latin-1", "replace")
    return hash_words(_prepare_words(data))


def tq_hash(path: str) -> int:
    """Normalise (lower-case, '\\'->'/') then hash.  This is the composition
    the client performs at TqPackageWdf.dll RVA 0x3BA0."""
    return tq_hash_raw(normalise_path(path).encode("latin-1", "replace"))


# --------------------------------------------------------------------------
# A SECOND, UNRELATED HASH -- do not confuse the two.
# --------------------------------------------------------------------------

def str2id(s) -> int:
    r"""`Str2ID` -- graphic.dll export, RVA 0x6DDC0.  **verified**, 10 bytes of
    code, transcribed in full:

        06DDC0  xor  eax, eax
        06DDC2  cmp  byte ptr [rcx], al
        06DDC4  je   .done
      .loop:
        06DDD0  movsx edx, byte ptr [rcx]     ; SIGNED char extension
        06DDD3  lea  rcx, [rcx+1]
        06DDD7  imul eax, eax, 0x21           ; * 33
        06DDDA  add  eax, edx
        06DDDC  cmp  byte ptr [rcx], 0
        06DDDF  jne  .loop
      .done:
        06DDE1  ret

    i.e. the classic `h = h * 33 + c` (a djb2 variant with seed 0).

    This is NOT the WDF name hash.  It is a separate 32-bit id used inside the
    C3 engine (string resources / effect ids).  Note two traps:
      * the seed is 0, not 5381;
      * `movsx` means bytes >= 0x80 contribute a NEGATIVE value, so this is
        NOT equivalent to the usual unsigned-char djb2 for non-ASCII input.
      * there is no case folding and no separator rewriting.
    """
    if isinstance(s, str):
        s = s.encode("latin-1", "replace")
    h = 0
    for b in s:
        if b == 0:
            break
        sb = b - 256 if b > 127 else b          # movsx
        h = (h * 33 + sb) & MASK32
    return h


# --------------------------------------------------------------------------
# self-test -- values below were produced by this implementation; they are a
# regression guard, not independent ground truth.  For independent validation
# run tools/verify_tqhash.py, which checks hashes against the real index
# tables in c3.wdf / data.wdf.
# --------------------------------------------------------------------------

def _demo():
    samples = [
        "",
        "a",
        "c3",
        "data",
        "c3/",
        "ani/",
        "C3\\Weapon\\410.c3",
        "c3/weapon/410.c3",
        "data/ItemType.dat",
    ]
    for s in samples:
        print(f"{s!r:32s} norm={normalise_path(s)!r:32s} "
              f"0x{tq_hash(s):08X}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            print(f"0x{tq_hash(a):08X}  {a}")
    else:
        _demo()
