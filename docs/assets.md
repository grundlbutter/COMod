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
| Filenames recovered, `c3.wdf` | **10,206 / 10,238 = 99.69 %** |
| Filenames recovered, `data.wdf` | **14,435 / 14,519 = 99.42 %** |
| Filenames recovered, combined | **24,641 / 24,757 = 99.53 %** |
| DMap files parsed | 136 / 136 header+grid+portals |
| DMap cell-grid row checksums reproduced | 58,245 / 58,898 = **98.89 %** (133/136 files at 100 %) |
| DMap files consuming every byte to EOF | 119 / 136 |

The last ~0.9 % of WDF names came from a **DatPkg community client as a
wordlist**: its `.tpi` indexes (§6) store ~130k plaintext paths in the same
TQ-hash namespace, so `wdf_recover.py --tpi <client>` hashes them directly and
mines their numbering conventions for the enumerator. Of the 24,641 names,
24,415 are now confirmed by a real observed string (was ~24,043); enumeration
contributes the rest, and enumerated candidates whose payload magic disagreed
with the extension are dropped (`out/wdf/*_rejected_names.json`), not counted.

## Tools

| Tool | What it does |
|---|---|
| `core/wdf.py` | WDF reader: module + CLI (`index`, `stats`, `list`, `extract`, `extract-type`, `extract-all`) |
| `tools/wdf_recover.py` | Filename recovery: wordlist harvest (loose files, embedded strings, DatPkg `.tpi` indexes), prefix-cached hashing, pattern enumeration. `--archives` targets any WDF set; `--tpi` adds a DatPkg client as a wordlist |
| `tools/garment_recover.py` | Corroborated numeric-ID recovery for community `garments*.wdf` (mesh↔texture pairing + run detection) — the guard that keeps birthday collisions out of the name tables |
| `tools/apply_recovered_names.py` | Renames the extracted `garments-unnamed/` library files to their recovered names and rewrites the COmmunity Library manifest (dry-run by default) |
| `core/tpd.py` | NetDragonDatPkg `.tpi/.tpd` reader (§6); `read_index()` supplies the wordlist |
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
py -3 core/wdf.py stats  "$ROOT/c3.wdf"       # $ROOT: py -3 core/coroot.py
py -3 tools/wdf_recover.py                    # baseline, writes out/wdf/
py -3 tools/wdf_recover.py --tpi "$ZEPHYR"    # + DatPkg wordlist -> 99.53%
py -3 tools/garment_recover.py --root "$ZEPHYR" --out out/garments  # 0 confirmed
py -3 core/dmap.py summary                    # writes out/wdf/dmap_summary.json
py -3 core/dmap.py render "…/map/map/dragon.DMap" -o out/wdf/render/dragon.png
py -3 tools/inidb.py schemas                  # writes out/ini/schemas.json
```

`out/wdf/sample/` holds 387 extracted payloads — up to 50 per detected type,
written under their recovered paths. Full extraction is available via
`wdf.py extract-all --yes` but is deliberately not run by default.

---

## 6. NetDragonDatPkg — .tpi / .tpd (community clients)

Older and community clients ship `c3.tpi/c3.tpd` and `data.tpi/data.tpd`
instead of the WDF pair. Seen on "Zephyr Conquer" (`version.dat` = 1064) and
on the 7878 client, which also ships the patch overlays `c31` and `data1`.

**Reader: `core/tpd.py`, and that module is the authoritative statement of
this grammar.** The structural constants in §6.1–§6.4 below are held to the
code by `tests/test_tpd_doc_sync.py` — if this section and the reader
disagree about an offset, a flag value or a tail length, that gate goes red
and names the disagreement. Prose that only *describes* a parser drifts from
it; §6 drifted for four months (below), which is what bought the gate.

Measured against the six archives on this machine — 7878's `c3`, `c31`,
`data`, `data1` (59,964 + 82 + 86,149 + 1 entries) and both Zephyr pairs
(53,609 + 76,923). Everything below is **verified** unless labelled
otherwise, and the label is not decoration: `0x28` and the free-list grammar
are the two places this section is weaker than it looks.

> **This section documented a refuted grammar from `c743111` until
> 2026-08-15.** It described the 3rd and 4th `u32` of a record as duplicates
> of the 1st and 2nd, and `flag` as always 1. Both are false; see §6.2. The
> reader was corrected on 2026-08-10 (`f6243e5`, `0667d6a`, and
> `docs/CORRECTIONS.md` C-2026-08-10-parser-tpd-grammar) and this prose was
> not, because nothing connected the two. The reason the wrong grammar
> survived verification is worth keeping: **both Zephyr archives contain
> flag-1 records and nothing else** (53,609 and 76,923 of them), so on the
> only client that had been read, the duplicate fields genuinely are
> duplicates. It was a true statement about two archives, written down as a
> property of the format.

### 6.1 `.tpi` header — `0x30` bytes, **verified**

| offset | field |
|---|---|
| `0x00` | `char[16]` magic — `"NetDragonDatPkg\0"` |
| `0x10` | `u32` = 1000 — identical in all six archives; a version, presumably |
| `0x14` | `u32` = 0 |
| `0x18` | `u32` = 1 |
| `0x1C` | `u32` = 3 |
| `0x20` | `u32` indexOffset — `0x30` in all six; the first entry starts there |
| `0x24` | `u32` fileCount |
| `0x28` | `u32` entry-region byte length — **approximate; never seek by it** |
| `0x2C` | `u32` freeCount — free-list records following the entries |

`0x28` is the field to distrust, and the reader deliberately ignores it: it
never reads offset `0x28` at all, and walking the records is the only
reliable measure of where the index ends. What it actually holds, measured:

* On all four 7878 archives it is **exactly** the byte length of the entry
  region **excluding** the free list. (Measure the region *including* the
  free list instead and 7878 `c31.tpi` comes out 24 bytes short — which is
  3 × 8, its three free-list records. That is the same fact seen from the
  wrong end, not a second discrepancy.)
* On Zephyr it is simply **wrong**: `c3.tpi` states 2,653,672 against an
  actual 2,653,681, and `data.tpi` states 5,176,898 against 5,176,899 —
  short by 9 and by 1, with no free list to account for either.

So there is no reading of `0x28` that is correct on all six archives. Treat
it as **inferred and unreliable**.

### 6.2 Index entry — the tail length depends on `flag`, **verified**

`fileCount` variable-length entries follow the header back to back. Every
entry starts the same way:

```
u8   nameLen
char name[nameLen]      forward-slash path, no NUL terminator
u16  flag               0, 1 or 2 — and NOTHING ELSE is accepted
```

`flag` then decides how many bytes follow. This is the whole of the refuted
grammar's error, and it is unforgiving: a walk that assumes one tail length
reads every subsequent entry at the wrong offset.

**`flag` 1 — one zlib stream. A 22-byte tail** (the `u16 flag` plus five
`u32`):

```
u32  uncompressedSize    the whole file
u32  compressedSize      the whole file
u32  compressedSize      of the FIRST CHUNK  — equal to the above only
u32  uncompressedSize    of the FIRST CHUNK  — because there is one chunk
u32  offset              absolute offset of the payload in the .tpd
```

The 3rd and 4th `u32` are **not duplicates**. They are the first row of a
chunk table, and they coincide with the 1st and 2nd exactly when the file
fits in one chunk — which is every record in both Zephyr archives.

**`flag` 2 — chunked. The same 22-byte tail, then a chunk table** of
`chunkCount − 1` further descriptors, 12 bytes each, in order:

```
u32  compressedSize      of this chunk
u32  uncompressedSize    of this chunk
u32  offset              of this chunk in the .tpd
```

`chunkCount` is **derived, never stored**:
`ceil(uncompressedSize / firstChunkUncompressedSize)`. The chunk size is
**2 MiB (2,097,152 bytes)** — verified as the first chunk's uncompressed size
on all 14 flag-2 entries across 7878's `c3.tpi` (6) and `data.tpi` (8), with
no other value appearing, and the derived `chunkCount` matching the actual
descriptor count on all 14. Each chunk is its own zlib stream; concatenating
the inflated chunks gives the file.

**`flag` 0 — an empty file. A 10-byte tail**, and this is the load-bearing
one:

```
u32  uncompressedSize    (0)
u32  compressedSize      (0)
```

**There is no `offset` field** — an empty file has no payload to point at —
so the record is **12 bytes shorter** than every other record. Rare (4
entries in 7878 `c3.tpi`, 1 in its `data.tpi`, 0 in either Zephyr archive)
and expensive: a walk that reads the 22-byte tail here over-reads by 12 and
desynchronises everything after it, so the damage surfaces thousands of
entries later as drifting garbage and reads like a chunk-size problem. The
reader refuses an unknown `flag` **at the record that carries it** rather
than guessing a tail length, and checks that the walk consumed the index
exactly.

### 6.3 The free list — **verified, but on n = 1**

`freeCount` records of `{u32 size, u32 offset}` follow the last entry, 8
bytes each, naming space in the `.tpd` that no entry references any more
because a patch replaced a file in place and orphaned its old payload.

> **Read this before relying on the free list.** It rests on a **single
> archive**: 7878 `c31.tpi` is the only file on this machine with a non-zero
> `0x2C` (it ships 3; the other five archives ship 0). It is confirmed twice
> *within* that file — the three records `{1262, 350434}`, `{633, 364254}`,
> `{3819, 698252}` are exactly, and only, the three gaps left in `c31.tpd`
> by the 82 entries' payload coverage — but one file is the same evidence
> base that produced the refuted grammar in §6.2. A second patch overlay
> could show the field order is `{offset, size}` on a file where the two
> happen not to be distinguishable here, or that `size` excludes something.
> **n = 1.**

### 6.4 `.tpd` data file — **verified**

The same 16-byte magic in a `0x20`-byte header, then zlib streams (`78 DA`).
Payloads are laid out contiguously — `offset[i] + compressedSize[i] ==
offset[i+1]` for every `i`, first payload at `0x20`, and a file's chunks are
contiguous with each other. **A chunk, not a file, is the unit that gets an
offset**, which is why §6.2's chunk table is the thing the layout is built
out of.

**The size identity, with the clause the old text was missing.**
`sum(compressed) + 0x20 == tpd file size` holds **exactly on full
archives** — 7878 `c3` (1,076,058,840), `data` (896,279,080), `data1`
(145,649) and both Zephyr pairs (998,813,460 and 1,075,033,824). On a
**patch overlay it is short by the free-list bytes**, because those bytes are
still in the file and no entry claims them: 7878 `c31.tpd` is 1,156,978
bytes against a sum of 1,151,264, a shortfall of **5,714 — exactly its three
free-list records** (1,262 + 633 + 3,819). So the identity to use is

```
sum(compressed) + 0x20 + sum(freeList.size) == tpd file size
```

which holds on all six, and reduces to the old form when the free list is
empty. Every payload decompresses to the size the index promises on all six.

### 6.5 Plaintext names, and the tooling built on this format

Unlike WDF, which stores only a 32-bit hash, **DatPkg names are plaintext**
forward-slash paths — and they are in the same logical namespace the WDF
clients hash (`data/arrow.dds`, `c3/0001/000/001.c3`). So `tq_hash(name)`
matches entries across packaging schemes, and a DatPkg client is a free
wordlist for WDF name recovery (§6.6). That property, not the compression,
is what makes these archives worth reading.

`tools/assetdiff.py` builds on this: content-hash diff of a second client
install against the baseline (both packagings + loose files), with optional
extraction of everything unique into a library tree.

With `--server NAME` it also writes `<library>/servers/NAME/` — a **server
profile**: `filemap.json` mapping every logical path the client ships to
where its bytes now live (library, baseline path, or baseline archive entry
by hash — content dedup means 6,892 Zephyr paths resolve to a *different*
baseline path), plus a verbatim snapshot of the client's `ini/` linkage
tables. `core/colibrary.py::ServerView` subclasses `AssetRoot` over that
profile, so `resolve_appearance`, the viewer and `comod` resolve
mesh/texture/motion linkages exactly as that server's client would:

```bash
py -3 tools/coviewer.py --library "D:\COmmunity Library" --server zephyr
py -3 tools/comod.py   --library "D:\COmmunity Library" --server zephyr show 900119
```

The viewer needs no flag at all: on start it looks for a library in the
obvious places (`discover_libraries`, bounded — a few parents, one level
deep) and adopts one if exactly one is found, saying so in the log. The
header's **Asset library** button opens a chooser that lists what was
discovered as one-click buttons, with pasting a path as the fallback;
`GET /api/library` reports it, `POST /api/setlibrary` sets or clears it
(validated by contents: `servers/<name>/filemap.json` must exist) and
remembers it in the per-user config. `--library DIR` still works and is
remembered the same way.

A **Server** selector then sits in the header — baseline install or any
catalogued profile. `GET /api/servers` lists views, `POST /api/server`
switches; catalogues are built lazily and cached per view, and the texture
cache is kept per view so the same path never shows another server's bytes.

**Server tags.** Every asset a server does *not* share with the baseline
carries that server's tag ("Zephyr", from `profile.json`'s `tag` or the
title-cased directory name). It is derived from the filemap on catalogue
build, never stored per file — 100k rows in the tag store would be a copy of
the filemap that can go stale. The tag shows on file rows and on appearance
rows (an appearance is tagged when the art it resolves to is), appears in
the tag vocabulary with its count, and filters like any hand-written tag:
`/api/files?tag=Zephyr`, the Appearances tag chip, and the Files pane's
**Origin** filter, whose options are named after the server ("Zephyr only").
`untagged` still means "not labelled by you" — a server tag does not make a
row count as tagged.

`tools/colibrary.py materialize NAME --to DIR` writes a server's combined
tree (every logical path, bytes pulled from library or baseline) as plain
files. The destination is guarded: never under Program Files (the baseline
install is a read-only source), never inside the baseline root or the
library repo.
**Deep dedup.** `tools/deepdedup.py` goes below bytes: it decodes every
texture to RGBA (mip 0) and every mesh to geometry (positions + UV0 + faces +
placement matrix, normals excluded since PHY /PHY4 generate them) on both
sides and removes library files whose *decoded* content matches the baseline
— re-encodes and PHY-variant repacks. Zephyr result: 92,233 library files vs
77,958 baseline assets → 997 removed (21 textures, 978 meshes), filemap
entries rewritten to kind `"e"` (serves the baseline's visually identical
bytes, not counted as server-unique). Only 3 files in the library fail to
decode: two effects with garbage after their last valid chunk (the viewer now
parses those leniently, like the engine) and one truncated garment mesh.

**Old-client conventions, synthesized.** Clients that ship no armor.ini
resolve bodies as `c3/<type>/<look>/<action>.c3` + `c3/texture/<look><var>.dds`,
and mounts as `c3/mount/<look>/<look><var>00.*`; 3-digit `.c3` files in look
directories are MOTI-only motion (the viewer says so and names the model).
`colibrary.py rebuild-tables <server>` derives real PartIni tables from those
conventions into the profile snapshot (Zephyr: 2,229 body + 160 mount
appearances), which is what makes the character builder work for such
clients. Map support materializes the server's `map/` + `ani/*.ani` into
`out/viewer/serverviews/<name>/` (old `.7z` DMaps decompressed — same v1004
format inside) and converts the binary `ini/GameMap.dat` registry
(`u32 count; {u32 id, u32 len, path, u32 gridSize}` — verified to EOF) to
GameMap.json, so the MapEditor and Maps tab list and open the server's own
maps (Zephyr: 313 of 360 fully drawable).

**Per-library thumbnails.** `thumbs.py --library DIR --server NAME` renders a
server view's own assets into `out/thumbs/servers/<name>/` (own manifest;
meshes paired via the server's tables, then same-stem, then same-dir; MOTI-only
action files listed as unmatched). The viewer's Health & thumbnails panel
targets the *active* view: with a server selected it shows that library's
cache state and generates into it; the base install's renders are never
reused for a server (same path, different bytes).

### 6.6 Using a DatPkg client as a WDF wordlist

`wdf_recover.py --tpi <client-dir-or-.tpi>` mines those ~130k plaintext paths.
Against the baseline this lifted combined recovery from 24,426 → **24,641 /
24,757 (99.53 %)** and, more usefully, moved ~370 names from "guessed by
enumeration" to "confirmed by an observed string" (24,415 dictionary-certain,
up from ~24,043). Enumerated candidates whose payload magic contradicts the
extension are now dropped rather than counted (`out/wdf/*_rejected_names.json`).

### 6.7 The community garments\*.wdf names are **not** recoverable

Zephyr ships five `garments*.wdf` archives (14,051 unique-hash entries, custom
fashion) keyed by the same `tq_hash` — one `TqPackageWdf.dll`, and `package.ini`
lists them beside `c3.tpd`. `assetdiff.py` extracted every unique payload to
`<library>/garments-unnamed/`, but the *names* resist every recovery avenue,
and this was checked rather than assumed:

- **Wordlist** — the 130k DatPkg paths plus every loose/embedded string: **0**
  of 14,051 hit. The garments share **no** path (zero hash overlap) with the
  baseline c3/data indexes.
- **Numeric enumeration** — `c3/{mesh,texture,hair,weapon,npc}/<5–7 digits>`.
  The hit count tracks the birthday-collision expectation ~1:1 (e.g. one 100-dir
  block tested 161 M candidates for +519 "hits" against +527 expected false
  positives), i.e. essentially all noise. `tools/garment_recover.py` guards
  against trusting these by requiring **corroboration** — an id that hashes to a
  real entry in *two* sibling slots (`c3/mesh/<id>.c3` **and**
  `c3/texture/<id>.dds`), or a contiguous ≥3 run — since two independent hashes
  colliding at one id by chance is negligible. Corroborated names found: **0**.
- **Hierarchical `c3/GGGG/MMM/NNN`** (the body-mesh layout): 48 magic-plausible
  hits vs 59 expected by chance — noise, no clusters.
- **Payload inspection** — `.c3` meshes embed only a 3ds object name
  (`v_body`, `Cylinder02`), never an asset path.

Conclusion: these paths are computed in client code from item IDs and stored as
strings nowhere on disk, so they cannot be dictionary-recovered, and a 32-bit
hash against 14 k targets makes blind enumeration birthday-limited. The extracted
payloads keep their content-hash filenames; `apply_recovered_names.py` (dry-run
by default) is ready to rename any that a future signal *does* resolve, but as of
this pass it renames nothing — the honest result is 0, not a table of guesses.

---

## 7. Community `garments*.wdf` — pairing without names

The five `garments*.wdf` a private server adds to an old client are **not
TQ-packed archives**, and that is why the earlier name-recovery effort
returned zero. Two verified facts:

* A genuine WDF index is sorted strictly ascending by name-hash (shipped
  `c3.wdf`: all 10,238 entries). **Every `garments*.wdf` index is unsorted**,
  so nothing binary-searches it — the client scans.
* The name-hash field matches no path under any transformation tried:
  every plaintext path in this client's own `ini/c3.wdb` (72,626 names,
  6,682 of them under `garments/`) and `c3.tpi` (53,609), under `tq_hash`,
  `str2id`, `crc32` and `adler32`, with and without package prefixes,
  slashes flipped, case folded. Zero hits in all combinations.

The hash *function* is not the problem: Zephyr's `TqPackageWdf.dll` contains
all six of the constants `core/tqhash.py` was recovered from
(`0x9BE74448`, `0x66F42C48`, `0x267B0B11` and the three seeds), so the
algorithm is identical — these archives were simply written by someone
else's packer, keyed on something that is not a path we can observe.

**What survives is the authoring order.** The packer wrote each model
immediately followed by its skin, so payloads in offset order run
`M T M T …` — 1,634 adjacent mesh/texture pairs in `garments4.wdf` alone.
`tools/garmentpair.py` groups on that and reports what it cannot explain:

| archive | entries | paired | multiskin | orphan | explained |
|---|---|---|---|---|---|
| `garments.wdf`  | 5,253 | 1,967 | 5 | 503 | 90.4 % |
| `garments1.wdf` | 2,341 | 958 | 1 | 318 | 86.3 % |
| `garments2.wdf` | 1,943 | 784 | 2 | 279 | 85.4 % |
| `garments3.wdf` | 2,493 | 1,000 | 3 | 381 | 84.6 % |
| `garments4.wdf` | 2,021 | 816 | 2 | 151 | 92.5 % |

Two independent checks that the rule is real rather than convenient:

1. **Ground truth.** Applied blind to `c3.tpd`, whose names *are* known, the
   same rule picks the correct texture for 367 of 417 meshes (88 %); every
   mismatch is in `c3/effect/flash`, an alphabetically-ordered region where
   adjacency is coincidence rather than authorship.
2. **Rendered.** `tools/garmentrender.py` renders a pair straight out of the
   archive through `tools/thumbs.py`'s renderer. The results are coherent
   textured weapons and effect auras (`out/zephyr/g4_contact*.png`); a wrong
   pairing renders as garbled UV noise, which none of them do.

`garments4.wdf` is mostly weapons and particle effects rather than clothing —
several groups are `PTC3`/`CCFL` particle chunks with no drawable geometry,
reported as such rather than rendered blank.

### 7.1 Extracting them

`tools/garmentextract.py` writes the verified groups into the library, one
folder per source archive, `<library>/<Server>/<Archive>/`:

| archive | folder | models | of which particle-only |
|---|---|---|---|
| `garments.wdf`  | `Zephyr/Garments`  | 897 | 259 |
| `garments1.wdf` | `Zephyr/Garments1` | 410 | 111 |
| `garments2.wdf` | `Zephyr/Garments2` | 323 | 105 |
| `garments3.wdf` | `Zephyr/Garments3` | 370 | 126 |
| `garments4.wdf` | `Zephyr/Garments4` | 299 | 102 |

2,299 models / 7,877 files, one group rejected. Model and skin are written
with the **same stem** (`0042.c3` + `0042.dds`), which is exactly what the
viewer's same-stem pairing rule wants, and a model that recurs with a new
skin folds it in as `0042_s1.dds` rather than being dropped as a duplicate —
those repeats are the archive's colourways, 2,053 of them. Each folder keeps
a `manifest.json` recording every check per group.

`--emit-filemap` registers them in the server profile, so they carry the
server tag **and** a per-archive tag (`Garments4`) that appears in the tag
vocabulary — one archive can be browsed on its own.


---

## 8. The curated Collection

The library holds everything an imported client shipped. The **Collection**
is the subset that has been *chosen* -- `core/collection.py`, driven by
`tools/collect.py` and the viewer's Collection card.

    <library>/Collection/collection.json   the index
    <library>/Collection/Weapons/          one folder per category
        zephyr-frost-katana.c3             the mesh
        zephyr-frost-katana.dds            its skin (same stem: the viewer
        zephyr-frost-katana.json           pairs them for free)

Categories are the game's own vocabulary rather than a models/textures split,
because the question being asked is "what can go in this slot": Characters,
Garments, Weapons, Shields, Headgear, Mounts, NPCs, Monsters, Effects, Maps,
UI, Other.

Three properties make it a repository rather than a folder of copies:

* **Self-contained pairs.** Collecting copies the mesh *and* the skin
  currently resolved for it, so an entry survives the archive it came from.
* **Provenance.** Every entry records the server, the source paths, content
  hashes and when it was collected; a per-entry `.json` sits beside the files
  so a folder is self-describing even away from the index.
* **Usable as a swap.** `collect.py stage <id> --swap-for <logical>` writes
  the entry into `mods/stage/` under the path it replaces -- and the skin
  follows the *target*, not the source, or the game would draw the old
  texture on the new geometry. `comod.py install` remains the only thing
  that touches the game, with its own backups and revert.

The Collection appears in the viewer's Server menu as a profile of its own
(`<library>/servers/collection/`), so it gets the same folder tree,
thumbnails, tags and model viewer as any imported client -- the category
becomes its tag.

That profile is **derived from the index and rewritten on every change**, by
`Collection.save`. It used to be written only by `collect.py publish`, which
made the library show the Collection as of the last time someone ran that
command: entries removed weeks earlier still listed, entries since kept
absent entirely. Two details follow from fixing it that way:

* The filemap is rewritten **wholesale**, never appended to, so a removed
  entry leaves the library by construction rather than by remembering to
  delete it. It also names an entry's **motion and effect files**, which the
  first version omitted -- they were collected into the library folder but
  left outside its namespace, so they could not be browsed.
* The viewer caches one built catalogue per server for the life of the
  process, and the Collection is the one view it also *writes*. So a change
  drops that cached catalogue, and rebuilds it in place when it is the view
  you are looking at (`coviewer.py::_refresh_collection_view`). Without that
  the profile was correct on disk and stale on screen until a restart.

`collect.py publish` still exists, now as a repair command: reach for it when
the profile was lost, or when the Collection folder was edited by hand.

### Which files are a model's actions — one rule, three layouts

`collection.action_code` answers this for everything that asks: what the
viewer offers to play, and what collecting keeps, are the same question and
were being answered by two separate copies of the rule. One of them learned
about the Collection's naming and the other did not.

| layout | example | actions | anchored |
|---|---|---|---|
| one directory per look | `c3/npc/013/1.c3` | `100.c3`, `110.c3` beside it | no |
| the flat family | `c3/npc/999001100.c3` | `999001101.c3`, `999001190.c3` | yes |
| collected | `collection/npcs/zephyr-npc-001.c3` | `…__motion-100.c3` | yes |

**Anchored** means the match is tied to this model's own name, and it decides
two things. `MAX_ACTIONS` — the cap that stops a directory of hundreds of
4-digit models being read as one model's action set — applies only to the
unanchored layout, because only it can make that mistake. And the
"MOTI *without* geometry" test, which is what a motion-only file looks like,
applies only there too: the flat family is per-action **meshes**, every file
`PHY` + `MOTI`, so requiring no geometry rejected every action it had.

That is the whole of the `base-storekeeper-36` report — collected with no
animation. Nine digits is not "four or fewer", `c3/npc/` holds 127 files so
the cap would have discarded anything that survived, and each of its action
files carries geometry so the motion test would have rejected the rest.

Knowing the layout also has to switch the guess off rather than sit beside
it: `c3/npc/1.c3` really exists, and the short-numeric rule offered it as
"action 1" of every NPC in the directory.

### Confidence, and the 26% that never had a single answer

`meshtex` scores each pairing rule, and the score was a claim about the
*rule* rather than about the answer. A rule that names three textures for one
mesh has not identified it — but each candidate was reported at the rule's
full confidence, and `best()` returned whichever sorted first.

Measured over this install: **1,290 of 4,964 meshes (26%)** had an
ambiguous authored best presented as a definite one.

| rule | meshes it could not disambiguate |
|---|---|
| `appearance_table` | 728 |
| `motion_appearance` | 338 |
| `effect_table` | 204 |
| `npc_table` | 13 |
| `simpleobj_table` | 7 |

`matches` now divides the confidence by the number of distinct textures the
rule produced — picking blind among *n* is right 1/n of the time — and sets
`Match.alternatives`, so `ambiguous` is visible to callers. The kind stays
`authored`: the table really does say all three, and the ambiguity is a fact
about shared art rather than a failure to read it.

**Why it mattered.** `c3/npc/999001100.c3` is the standby motion of thirteen
NPCs, with two different skins between them. `npc_table` yielded three
textures at 0.95 each and `best()` chose `9990010` — a real file that
nothing loads for the Storekeeper, which uses `9990211`. Every swap built on
it failed while looking like a plumbing problem, because the answer existed
and carried a high score.

**Where one answer is genuinely needed, resolve the entity, not the mesh.** A
shared mesh has no texture of its own; the NPC does. That is what
`core/npcart.py` is for.

### An NPC is assembled from three tables — VERIFIED in the running game

`npc.json` names `999001100`, and `c3/npc/999001100.c3` exists, so it reads
like the whole model. **It is the motion and nothing else.** The client
assembles a Storekeeper from three separate lookups:

```
ini/npc.json          type 1 "Storekeeper"
                        simple_object   211
                        standby_motion  999001100    ← motion
                        rest_motion     999001101
                        blaze_motion    999001190
ini/3DSimpleObj.ini   [ObjIDType211]
                        Part0    = 9990010           ← geometry
                        Texture0 = 9990211           ← texture
ini/3dobj.ini         9990010 → c3/mesh/9990010.c3
ini/3dtexture.ini     9990211 → c3/texture/9990211.dds
```

`core/npcart.py` walks that chain. **437 of 437 NPCs resolve**; geometry and
texture exist on disk for all but one outlier row (type 10999, which points
at `c3/npc/999/1.tga`). 34 looks are referenced and not shipped in this
build, so 165 NPCs have no motion files — a fact about the content, not the
resolver. 38 geometries and 47 textures serve more than one NPC, so a swap
is told when it will change others.

**How this was found, because the manner matters.** `meshtex` inferred the
texture by transposing the mesh id — `999001100` → `c3/texture/9990010.dds`
— and reported it as an **authored** pairing at **0.95**. It names a real
file, which is what made it survive scrutiny; nothing loads it for this NPC.
Every swap built on it failed, and the confidence score is what stopped the
inference being questioned. Two experiments settled it: replacing that one
texture changed nothing, tinting all 70 in `c3/texture/999*` turned every NPC
red. That bracketed the answer to "right namespace, wrong file" and sent us
to the tables.

The same mistake, in geometry, produced the T-pose. The Storekeeper's own
skeleton is **30 PHY bones**; the donor's is **54**. Writing 55-bone motion
into `c3/npc/999001*.c3` while the client kept drawing its own 30-bone body
from `c3/mesh/` is a rig disagreeing with itself. **Resolve all three roles
together or none of them are right.**

`skin_destination` and `Catalog.texture_for_mesh` now ask the tables before
any inference, and `Collection.stage` takes an `art_plan` so each role is
written where the client reads it.

### Replacing something with a collected entry

`Collection.stage` writes an entry into `mods/stage/` under the path it
replaces. Three things make that more than a file copy:

* **The skin follows the target.** Staging a model over `c3/npc/002/1.c3`
  while its skin lands back at `c3/npc/001/1.dds` leaves the game drawing
  002's texture on 001's geometry.

  Where the skin does not sit beside its mesh — the flat family keeps
  textures in `c3/texture/` — there is nothing for `Collection.stage` to
  derive, and `skin_to` says where. **That destination is no longer typed.**
  `coviewer._skin_target` asks where the *target's own* texture lives, which
  is the path the client actually reads; the resolver answers first, and for
  the flat family a second pass reaches it from the filename. Measured over
  this install: of 41 looks, **36 ship `c3/texture/999<look>0.dds` and the
  rest ship `c3/texture/999<look>.dds`**. Two forms, so both are tried — and
  only a path that **exists** is returned, because a derived name that is
  not on disk is a guess about where the client looks, and a texture written
  to a guessed path is read by nothing. Look 002 resolves to
  `c3/texture/999002.dds`; `c3/texture/9990020.dds` does not exist.
* **The actions take the target's name.** In the flat family the filename
  carries the look as well as the action, so staging look 001 over look 002
  with the actions keeping their own names replaces the *donor's* walk cycle
  and leaves the target's untouched — the swap then plays the old animation,
  or none. `action_target_name` maps the code onto the target's spelling.
  Where no code can be derived the part is skipped and said so, because the
  fallback of writing the donor's filename into the target's directory
  overwrites a third model.
* **`skin` and `roles` choose what travels.** Everything by default, because
  a model swapped without its animation is a statue; but "new geometry, the
  target's own colours and actions" is a real edit and used to require
  staging by hand.

The UI for this is `tools/webui/swap.js`, loaded by **both** pages — the
asset browser had a `prompt()` that asked for a path and then wrote whatever
the entry had, and the builder, which is where you are when you decide one
model should replace another, had no staging at all.
