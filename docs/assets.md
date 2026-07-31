# Asset format specs — WDF, TQ name hash, DMap

Workstream: offline asset-format toolchain. All findings below come from parsing
the real files in `$ROOT` -- the install root, located by
`core/coroot.py` (read-only) -- and from
static disassembly of `bin/64/TqPackageWdf.dll`.

Every field is marked **verified** or **inferred**:

- **verified** — parsed out of real data and cross-checked by something
  independent: an arithmetic identity, a checksum that reproduces, a parse that
  lands exactly on EOF, or a byte-for-byte content match.
- **inferred** — plausible and consistent with the data, but nothing yet forces
  it to be true.

## Headline results

| Result | Value |
|---|---|
| WDF name-hash recovered | yes — exact, from `TqPackageWdf.dll` |
| Filenames recovered, `c3.wdf` | **10,160 / 10,238 = 99.24 %** |
| Filenames recovered, `data.wdf` | **14,266 / 14,519 = 98.26 %** |
| Filenames recovered, combined | **24,426 / 24,757 = 98.66 %** |
| DMap files parsed | 136 / 136 header+grid+portals |
| DMap cell-grid row checksums reproduced | 58,245 / 58,898 = **98.89 %** (133/136 files at 100 %) |
| DMap files consuming every byte to EOF | 119 / 136 |

## Tools

| Tool | What it does |
|---|---|
| `core/wdf.py` | WDF reader: module + CLI (`index`, `stats`, `list`, `extract`, `extract-type`, `extract-all`) |
| `tools/wdf_recover.py` | Filename recovery: wordlist harvest, prefix-cached hashing, pattern enumeration |
| `core/tqhash.py` | The TQ name hash (written by the DLL workstream, validated here) |
| `core/dmap.py` | DMap parser + PNG/PGM renderer |
| `tools/inidb.py` | `ini/` loader and schema profiler |

Generated output lives in `out/wdf/` and `out/ini/`.

---

## 1. WDF archive

Two archives: `c3.wdf` (534,811,690 bytes, 10,238 entries) and `data.wdf`
(383,863,672 bytes, 14,519 entries). Both dated 2017-12-11.

### 1.1 Header — all **verified**

```
off 0   u32 magic        0x57444650   ('PFDW' as little-endian bytes)
off 4   u32 fileCount
off 8   u32 indexOffset  absolute byte offset of the index table
off 12  ...payload blob...
indexOffset .. EOF : fileCount * 16 bytes of index entries
```

Confirmed twice over. Arithmetically: `534,811,690 − 534,647,882 = 163,808 =
10,238 × 16` exactly, and the same identity holds for `data.wdf`. And from code
— `TqPackageWdf.dll` rva `0x2C90` does `fread(hdr, 12, 1, fp)`, reads the count
from `hdr+4`, the index offset from `hdr+8`, then `shl rcx, 4` to size the index
allocation.

**The magic is never validated.** The constant `0x57444650` does not appear as
an immediate anywhere in any shipped binary (checked all of `bin/64`). The
reader trusts the header. *(verified — absence confirmed by byte search.)*

### 1.2 Index entry — 16 bytes, all fields **verified**

```
u32 nameHash    TQ hash of the lowercased, '/'-normalised, package-qualified path
u32 offset      absolute byte offset of the payload
u32 size        payload length in bytes
u32 space       0 in every entry of both archives
```

The field *order* was originally inferred; it is now verified four ways:

1. **Contiguity** — `offset[i] + size[i] == offset[i+1]` for every adjacent pair:
   10,237/10,237 in `c3.wdf` and 14,518/14,518 in `data.wdf`.
2. **Exact accounting** — `sum(size) == indexOffset − 12` exactly in both
   archives, i.e. zero padding and zero slack anywhere in the payload region.
3. **Sort order** — hashes are *strictly* ascending, and all are unique
   (10,238 and 14,519 distinct). This is what makes the first field a lookup key.
4. **From code** — rva `0x2C90` walks the index with a 16-byte stride
   (`shl r14, 4`) and inserts `dword [r14]`, the *first* u32, as the `std::map`
   key, storing the entry pointer as the value.

`space` is 0 everywhere, so its purpose is **inferred**: most likely an
"allocated size" slot for in-place rewriting that the shipping packer never uses.

Entries are sorted by hash but the shipped reader loads the whole index into a
`std::map` rather than binary-searching in place. *(verified from code.)*

### 1.3 Payload type histogram — **verified** by magic sniffing

`c3.wdf` (10,238):

| Magic | Count |
|---|---|
| `DDS ` | 5,694 |
| `MAXF` | 4,538 |
| CUR | 6 |

`data.wdf` (14,519):

| Magic | Count |
|---|---|
| `DDS ` | 13,601 |
| PNG | 605 |
| JPEG | 117 |
| all-zero header | 96 |
| BMP | 69 |
| CUR | 17 |
| `RIFF` (wav) | 9 |
| unknown | 3 |
| `Fold` | 1 |
| ICO | 1 |

Combined: 19,295 DDS, 4,538 MAXF, 605 PNG, 117 JPEG, 69 BMP, 23 CUR, 9 RIFF.

The 96 all-zero entries in `data.wdf` are real entries with real sizes whose
first 4 bytes are zero — they are not holes (they participate in the contiguity
identity). Their actual content type is **not yet identified**.

### 1.4 Loose files shadow the archives — **verified**

`TqFOpen` (rva `0x36D0`) normalises the path, resolves the package by its first
segment, then tries the on-disk file *first* (`GetFileAttributesExA` on
`<root>\<path>`) and only falls back to the archive. `ini/package.ini` lists the
archive priority order, and its contents are literally:

```
data.wdf
c3.wdf
```

This is why 53,677 loose files ship alongside the archives: they are patch
overrides. It is also what made ground-truth recovery possible (§2.2).

---

## 2. The TQ name hash

Implemented in `core/tqhash.py`. Recovered from `TqPackageWdf.dll` by two
independent transcriptions of the same disassembly (this workstream and the DLL
workstream) that agree instruction for instruction.

### 2.1 Algorithm — **verified**

Normalisation (rva `0x3B10` / `0x3BA0`): fold ASCII `A`–`Z` to lower case and
rewrite `\` (0x5C) to `/` (0x2F). Nothing else — no trimming, no separator
collapsing. The case fold is open-coded as a `lea edx,[rax-0x41]; cmp dl,0x19`
range test, **not** via the CRT tables.

Framing (rva `0x3C40`):

1. `strncpy` the normalised string into a zeroed 256-byte global buffer, so the
   tail is NUL-padded. Strings ≥ 256 bytes are rejected by the caller.
2. Reinterpret as `u32[64]` little-endian; find `j` = index of the first
   all-zero word (for a string of length L this is `ceil(L/4)`).
3. Append two terminator words: `word[j] = 0x9BE74448`, `word[j+1] = 0x66F42C48`.
   Word count fed to the core is `j + 2`.

Core (rva `0x3E42`): a two-register mixer over those words using 32×32→64
multiplies, the carry flag, and a round key rotated left by 1 each iteration.
Seeds `0xF4FA8928` / `0x37A8470E` / `0x7758B42B`, round constant `0x267B0B11`,
operand masks `|0x02040801 &0xBFEF7FDF` and `|0x00804021 &0x7DFEFBFF`.
Returns `h1 ^ h2`.

**The hashed string includes the leading package segment.** `C3\Weapon\410.c3`
→ `c3/weapon/410.c3` → `0xA1AE0B00`. Stripping the prefix resolves essentially
nothing. *(verified.)*

The function is deliberately non-linear. An independent GF(2) affinity test run
here — searching ground-truth pairs of equal-length paths for XOR quadruples
`a^b^c^d = 0` and checking `H(a)^H(b)^H(c)^H(d) = 0` — gave **3 consistent out
of 79,956**, i.e. chance. So it is *not* a CRC or any linear construction, which
is why the entire standard hash battery (CRC32 in 6 variants, djb2, djb2-xor,
sdbm, FNV-1/1a, ELF, PJW, BKDR, RS, JS, DEK, AP, Adler-32, MPQ ×3) scored
**0 hits out of 497** against ground truth before the DLL was read.

### 2.2 Independent validation — content-matched ground truth

Because loose files shadow the archives, some loose files are byte-identical to
the archive entry they override. Hashing every loose file's content and matching
against every archive payload's content yielded **497 unambiguous (path, hash)
pairs derived with no reference to the hash function at all**.

`tq_hash` reproduces **450 / 497** exactly. The 47 misses are artefacts of the
oracle, not the hash: they are duplicate-content textures (e.g. two different
`c3/weapon/*.dds` paths whose payloads are identical), so the content match
paired the file with the wrong entry. Two of the misses collide onto the same
"expected" hash, which is the signature of that failure mode. No miss is a case
where `tq_hash` produced a wrong value for a correctly-attributed name.

### 2.3 Name recovery — the headline

Wordlist sources:

- 53,677 loose file paths under `$ROOT`
- 102,778 path-looking tokens scraped from `ini/`, `ani/`, `map/`, `c3/`,
  `data/`, `graphics/`, `sound/`. **`ani/*.json` is by far the richest source**
  — 60 files yielded 81,000 paths, because the `.ani` animation definitions name
  every puzzle tile and cover frame explicitly. Scraping them is what took
  `data.wdf` from 1.3 % to 98 %.
- extension permutation of every candidate
- per-directory numeric pattern enumeration (`pic0000.dds` … `pic9999.dds` etc.)
  with the hash state cached after each directory prefix

| Archive | Recovered | Rate | From observed strings | From enumeration |
|---|---|---|---|---|
| `c3.wdf` | 10,160 / 10,238 | **99.24 %** | 9,938 | 222 |
| `data.wdf` | 14,266 / 14,519 | **98.26 %** | 14,105 | 161 |
| **Total** | **24,426 / 24,757** | **98.66 %** | 24,043 | 383 |

Confidence accounting, stated honestly: the dictionary phase tested 1,655,215
candidates against 24,757 target hashes, giving an expected false-positive count
of **9.5** — so the 24,043 dictionary-sourced names are effectively certain. The
enumeration phase tested a further ~37 M candidates (expected FP ≈ 213 in total);
those 383 enumerated names carry real collision risk and are flagged separately
in `out/wdf/name_recovery_summary.json`. A cross-check that every recovered
name's extension agrees with its payload's detected magic flags only **1**
inconsistency across all 24,426 names.

Unresolved residue: 78 in `c3.wdf` (40 MAXF, 35 DDS, 3 CUR) and 253 in
`data.wdf` (85 JPEG, 59 PNG, 57 BMP, 49 DDS, 1 CUR, 1 Fold, 1 unknown).

### 2.4 A second, unrelated hash

`graphic.dll!Str2ID` is `h = h*33 + (signed char)c`, seed 0, no folding. It is
**not** the WDF hash and must not be conflated with it.

---

## 3. DMap — world maps

136 `.DMap` files in `$ROOT/map/map/`. Reference:
`refs/conquer-online-wiki/Files/DMap.md`, re-verified against real bytes.

### 3.1 Header and cell grid — **verified**

```
off 0    8 bytes of version, two interchangeable forms (see below)
off 8    char[260] puzzle   fixed-width, NUL-terminated
                            e.g. "map\puzzle\arena.pul"
off 268  u32 width
off 272  u32 height
off 276  cell grid:
           for y in 0..height-1:
             for x in 0..width-1:
               u16 mask       0 = walkable, non-zero = blocked
               u16 surface    terrain/surface id
               i16 elevation  signed
             u32 rowChecksum
```

The version field comes in two forms occupying the same 8 bytes — the wiki hints
at this ("the version could be a string prefixed with DMAP") and it checks out
exactly:

| Form | Value | Files |
|---|---|---|
| `u64` numeric | 1003 | 47 |
| | 1004 | 61 |
| | 1005 | 1 |
| | 1006 | 6 |
| `char[8]` ASCII | `"DMAP101\0"` | 20 |
| | `"DMAP100\0"` | 1 |

Everything after offset 8 is identical between the two forms. *(verified — all
136 files decoded and counted.)*

**The cell layout is proved by the checksum.** Per row:

```
cs = 0
for x in row:
    cs += (0 if mask == 0 else 1) * (surface + y + 1)
        + (elevation + 2) * (surface + x + 1)
cs &= 0xFFFFFFFF
```

This reproduces **58,245 of 58,898 rows** across all 136 files, with 133 files at
100 %. Cell stride, field order, field widths, the signedness of `elevation`,
and the per-row (not per-file) placement of the checksum are all pinned by this
— get any one of them wrong and the checksum fails immediately.

The 653 failing rows are confined to **3 version-1006 files**
(`2020love01_new`, `bp-flandlords-y_new`, `ninja01_new`). Each matches for the
first 40–60 % of its rows then diverges, so v1006 inserts something mid-grid
that this parser does not model. **Open.**

All 136 maps are square (width == height), ranging 40×40 to 1440×1440.

### 3.2 Portals — **verified**

```
u32 portalCount
portalCount x { u32 x; u32 y; u32 id }
```

Parses cleanly in all 136 files. Per the wiki the `id` is a client-side index
only and is not globally meaningful. *(inferred — not checked against anything.)*

### 3.3 Layers — **verified shapes, corrected tags**

```
u32 layerCount
layerCount x { u32 type; payload }
```

30,173 layers decoded. Payload sizes:

| Tag | Shape | Payload | Fields |
|---|---|---|---|
| 1 | scene | 268 | `char[260]` path, `u32` x, `u32` y |
| 4 | cover | 416 | `char[260]` ani path, `char[128]` key, `u32` originX/originY/w/h, `i32` offsetX/offsetY, `u32` frameInterval |
| 10 | effect | 72 | `char[64]` name (a `3DEffect.ini` key), `u32` x, `u32` y |
| 15 | sound | 276 | `char[260]` path, `u32` x, `u32` y, `u32` range, `u32` volume |

Observed counts: cover in 110 files, effect in 45, scene in 27, sound in 18.

**The tag numbering in `refs/.../Enums/Dmap-Layer-Type.md` is misleading for this
client.** What the wiki documents as the "Scene Layer Type" is emitted with tag
1 (which the enum calls `TERRAIN`), and its "Effect Layer Type" with tag 10
(which the enum calls `ANIMATION`). The payload *shapes* are exactly as the wiki
describes; only the tag→name mapping differs. Assuming tag 10 was a 416-byte
COVER-shaped record is what broke the layer walk on 56 files; correcting it to
72 bytes fixed 52 of them. *(verified — the fix is what makes the files land on
EOF.)*

Values decode sensibly: `dragon.DMap` yields covers keyed `dragon01.tga` …
`dragon35.tga` against `ani\MapScene.ani`, with plausible origins, 1×1 or 2×2
sizes and 200–350 ms frame intervals.

Some maps declare more layers than they wrote, leaving the tail as MSVC
uninitialised heap fill (`0xCDCDCDCD`). The parser stops there rather than
inventing structure. *(verified — the fill byte is unambiguous.)*

### 3.4 Trailing section — **verified shape, inferred meaning**

```
u32 extraCount
extraCount x { u32 v0..v5; char[260] path }     // 284 bytes each
```

Example record: `0, 4, 30, 30, 1, 8, "map\puzzle\skybg-move.pul"`. The path is
always a `.pul`, and the names (`skybg-move`, `starbg01`, `bravebg-move`,
`beach-bg`) make these **inferred** to be scrolling sky/background puzzle planes.
The six leading integers are **not decoded**.

With this section modelled, **119 of 136 files consume every byte to EOF**.

### 3.5 What is still open

- The 3 version-1006 files diverge mid-grid. **Open.**
- 4 files have layer walks that hit an unmodelled tag (type 24 in three v1006
  files; type 0 in `magictower01_new.DMap`, the only v1005 file). **Open.**
- 13 files have residual bytes after the trailing section, mostly multiples of
  264 (= 260 + 4), suggesting one more record array of that stride. **Open.**
- Meaning of `surface` values and the `mask` bit layout beyond "non-zero blocks".
  **Open** — only `mask == 0 ⇒ walkable` is verified, via the checksum formula.

### 3.6 Cross-check against `ini/GameMap.json`

`GameMap.json` has 156 rows of `{DocumentId, FileName, PuzzleGridSize}`. **136 of
136** parsed `.DMap` files match a row by filename; the extra 20 rows reference
maps not shipped in this build. `PuzzleGridSize` is 256 for 114 maps and 128 for
22. Note the oracle carries no dimensions, so it validates the ID↔file mapping
but not width/height — the checksum does that instead.

### 3.7 Visual proof

`out/wdf/render/` holds passability and elevation renders (`--mode mask` /
`elevation`) for `arena`, `dragon`, `desert`, `newplain`. The passability images
show coherent island/room geometry with clean walls and circular obstacles,
which a mis-strided grid could not produce.

---

## 4. C3 / MAXF

Not started — 4,538 `MAXF` payloads in `c3.wdf` are extracted and named but
undecoded. `refs/conquer-online-wiki/Files/C3.md` is the reference to start from.

---

## 5. Reproducing

```bash
py -3 core/wdf.py stats  "$ROOT/c3.wdf"      # $ROOT: py -3 core/coroot.py
py -3 tools/wdf_recover.py                    # ~8 min, writes out/wdf/
py -3 core/dmap.py summary                   # writes out/wdf/dmap_summary.json
py -3 core/dmap.py render "…/map/map/dragon.DMap" -o out/wdf/render/dragon.png
py -3 tools/inidb.py schemas                  # writes out/ini/schemas.json
```

`out/wdf/sample/` holds 387 extracted payloads — up to 50 per detected type,
written under their recovered paths. Full extraction is available via
`wdf.py extract-all --yes` but is deliberately not run by default.
