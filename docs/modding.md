# Graphics modding — Classic Conquer 2.0

How the client's visual assets are laid out, which file backs which in-game
thing, and how to change one. Everything below is marked **VERIFIED** (proven
against the real files or the real code) or **INFERRED** (a working rule that
reproduces most of the data but whose ground truth has not been read).

Companion tools:

| Tool | Purpose |
|---|---|
| `core/coassets.py` | library: asset VFS, C3 container, DDS, DMap/Pul/Scene, ini tables |
| `tools/comod.py` | CLI: find → inspect → extract → edit → stage → install |
| `tools/cobrowse.py` | visual HTML browser for textures and maps |
| `tools/coviewer.py` | **the GUI asset viewer** — browse, 3D preview, swap textures. See `docs/viewer.md` |
| `core/dds.py` | DDS reader + BC1/BC2/BC3 decoder used by the viewer |
| `tools/meshtex.py` | **mesh → ranked textures**, with a method + confidence per match. See `docs/meshtex.md` |
| `tools/bodyfacets.py` | body appearance → class / gender / size. See `docs/appearance_ids.md` |
| `tools/tagstore.py` | the viewer's user-tag store (JSON, atomic, exportable) |
| `core/wdf.py` | WDF archive reader (from the earlier workstream) |
| `core/tqhash.py` | TQ filename hash (from the earlier workstream) |

---

## 1. The one fact that makes modding easy

**VERIFIED.** The client resolves an asset by trying a **loose file on disk
first**, and only falls back to the `.wdf` archives if that misses.

Proven three ways:

1. **Code.** `TqPackage!TqFOpen` (RVA `0x10760`) tails into `sub_C020`, which
   calls handler A at `[this+0x8]`; if and only if that returns `3` does it call
   handler B at `[this+0x10]`. Handler A builds a path in a 0x140-byte stack
   buffer (native filesystem). Handler B (`sub_DB20`) is a pure thunk through a
   function-pointer table at `[rcx+0x38]` — the table populated from the
   dynamically loaded `TqPackageWdf.dll` (the strings `Loading TqPackageWdf.dll
   failed!!`, `TqFOpen`, `TqPackagesSetPriority` confirm the LoadLibrary +
   GetProcAddress path).
2. **Content.** Loose files shadow same-named archive entries with **different
   bytes**. If the archive won, shipping those would be pointless. First pass
   measured 28 such files in the loose `c3/` tree against `c3.wdf`; the fuller
   sweep across both archives finds **587 shadowed entries, 30 differing in
   size** (`docs/viewer.md`, `docs/assets.md` §1.4).
3. **Timestamps.** Those overrides are stamped `2022-12-29`; `c3.wdf` and
   `data.wdf` are stamped `2017-12-11`. They are patches, five years newer.

A further 3,868 loose files have no WDF entry at all.

**Consequence: a mod is just a file tree mirroring the install layout. You never
repack a `.wdf`.** Drop `c3/texture/002135300.dds` into the install root and it
wins over the archived copy.

`integrity.json` has 162 rows but only **143 distinct files: 136 `.DMap` + 7
`ini/*.json`** (19 rows are repeats — a map is listed once per `GameMap.json`
`DocumentId`). **No art file of any kind is covered** — no texture, no mesh, no
`.pul`, no `.scene`, no `.exe`, no `.dll`. Texture and mesh mods are outside the
manifest entirely. Only a map's *cell grid* is gated — see §6.

---

## 2. Asset layout

```
$ROOT/
  c3.wdf        534 MB, 10238 entries   DXT3 textures + MAXF models
  data.wdf      384 MB, 14519 entries   UI, icons, map art
  c3/           4263 loose files        overrides + newer content
    mesh/  texture/  weapon/  body/  hair/  monster/  npc/  mount/  effect/
    0001/ 0002/ 0003/ 0004/             per-body-type motion sets
  data/         ~46000 loose files      UI, map puzzle art
  ini/          the game database
  map/
    map/*.DMap        136 world maps
    puzzle/*.pul      381 background puzzles
    Scene/*.scene     299 scenery objects
    ScenePart/*.Part  126 scene parts — PLAIN TEXT
```

WDF filenames are not stored, only a 32-bit hash. The earlier workstream
recovered the hash function (`core/tqhash.py`). Name recovery now stands at
**24,426 of 24,757 distinct hashes = 98.66%** (c3 99.24%, data 98.26%) via
`tools/wdf_recover.py` — see `docs/assets.md`. `AssetRoot` loads the table
automatically. (The 10,126-name figure quoted in older notes and in
`out/dll/wdf_name_recovery.json` was the first pass; it is superseded.)

### WDF format — VERIFIED

```
u32 magic       = 0x57444650 "PFDW"
u32 fileCount
u32 indexOffset            -> index table near EOF, 16 bytes/entry
  u32 nameHash, u32 offset, u32 size, u32 space(always 0)
```
Both archives validate exactly: payload bytes account for the whole payload
region, hashes strictly ascending, zero out-of-range offsets.

---

## 3. Which file backs which in-game thing

The chain is:

```
ini/itemtype.json      gameplay item  (id, name, stats)   — 11142 entries
        |
        v  appearance ID
ini/RolePart.ini       13 body parts -> which table owns each
        |
        v
ini/armor.ini | weapon.ini | armet.ini | ...
        |
        v  Mesh<i> / Texture<i> numeric IDs
c3/mesh/<id>.c3        geometry
c3/texture/<id>.dds    skin
```

(Official clients ship the first link as `ini/itemtype.dat` — the same rows
behind the TQ File Cipher — and their monster table as `ini/Monster.dat`.
`core/tqdat.py` reads both, VERIFIED by re-encrypting the decryption back to
the shipped bytes, and `load_items` / `load_monster_rows` fall back to them,
so the chain starts the same way on either kind of root.)

**VERIFIED**: `RolePart.ini` `[Config]` lists 13 parts, each naming a mesh table
and a motion table. Sections in those tables are appearance IDs; `Part=N` gives
N sub-parts; each has `Mesh<i>` and `Texture<i>`.

```ini
[002135300]
Part=1
Mesh0=002135000        ; geometry, shared across a whole armour family
Texture0=002135300     ; skin, unique per colour variant
Material0=default
```

Note the pattern: **many appearances share one mesh and differ only by texture.**
That is why retexturing is the highest-leverage kind of mod — one `.dds` changes
one armour colourway without touching geometry at all.

Tables actually shipping in this install:

| Part | Table | Appearances |
|---|---|---|
| body / mix_body | `armor.ini` | 3418 |
| l_weapon / r_weapon | `weapon.ini` | 5384 |
| armet / mix_armet | `armet.ini` | 2918 |
| armet_dx8 | `armet1.ini` | 950 |
| mount | `mount.ini` | 1317 |
| misc | `misc.ini` | 272 |
| head | `head.ini` | 4 |

### ID → file resolution — INFERRED

Bare numeric IDs are resolved by probing `c3/{mesh,texture,weapon,body,hair,
mount,npc,monster}/` with the ID as written, zero-padded to 9 digits, and
stripped of leading zeros. This reproduces:

* `armor.ini` — **94%** of references
* `weapon.ini` — **98%**
* `armet.ini` — **79%**

The client's real rule lives inside the Themida-packed `ImConquer.exe` and has
not been read. `head.ini`, `misc.ini` and `mount.ini` resolve at 0% because
those assets are genuinely **not shipped** in this build — legacy leftovers, not
a resolver bug.

---

## 4. Textures — fully solved

**VERIFIED.** All textures are standard DDS.

| Where | Formats |
|---|---|
| `c3.wdf` | DXT3 ×5694 |
| `data.wdf` | DXT3 ×7382, DXT1 ×6219 |
| loose `data/` | DXT1 ×32667, DXT3 ×13022, DXT5 ×9 |
| loose `c3/` | DXT3 ×1884, DXT1 ×304, DXT5 ×71 |

Power-of-two, overwhelmingly 256×256 / 64×64 / 128×128, and **mipmap count 0 on
19,293 of 19,295 archived textures** — the engine generates mips itself, so you
do not need to author them.

Pillow 12.3 (installed) reads and writes DXT1/DXT3/DXT5 directly, so the whole
round-trip is native:

```bash
py -3 tools/comod.py extract c3/texture/002135300.dds --png
```
```bash
py -3 tools/comod.py import-png mods/work/edited.png c3/texture/002135300.dds
```

`import-png` defaults to the **original's** DXT format and warns on a dimension
mismatch. Verified round-trip: a 128×128 DXT3 texture extracted from `c3.wdf`,
recoloured, and re-encoded came back byte-identical in size (16,512) and
re-decodes cleanly.

**Rules of thumb**
- Keep the original dimensions and power-of-two.
- DXT1 has 1-bit alpha; DXT3 has 4-bit explicit alpha. If the original is DXT3,
  it probably needs that alpha — don't downgrade it to DXT1.
- Don't add mipmaps.

---

## 5. Meshes — container solved; geometry solved in §9

> **Read §9 first if you want the geometry.** This section is kept as written
> during the first pass, when the vertex layout was still open. Its container
> findings remain correct; its "PARTIAL" verdict on the PHY body does not.

### Container — VERIFIED, exhaustively

```
char[16] "MAXFILE C3 00001"
repeat until EOF:
    char[4] tag
    u32     length
    byte[length] body
```

Walked cleanly over **all 4,538 archived and all 2,002 loose C3 files** — zero
size mismatches, zero trailing bytes. `C3File.to_bytes()` round-trips **all
6,540 files byte-exactly**, so chunk-level edits (reorder, drop, swap, replace a
whole chunk) are lossless and safe today.

| Tag | Meaning | Archive | Loose |
|---|---|---|---|
| `PHY ` | physique/mesh, original encoding | 10356 | 613 |
| `PHY4` | physique/mesh, newer encoding | — | 4009 |
| `MOTI` | motion track | 10657 | 5892 |
| `CAME` | camera | 3743 | 48 |
| `PTCL` / `PTC3` | particle system | 430 | 645 |
| `SHAP` / `SMOT` | shape + shape motion | 477+477 | 85+85 |
| `MNEW` | newer mesh variant | — | 31 |
| `CCFL` | undecoded | — | 26 |

The 2022 loose patches use `PHY4`/`PTC3`/`MNEW`/`CCFL`; the 2017 archives use
only the older tags.

### PHY body — PARTIAL

Established:

* `u32 nameLen` + name — **VERIFIED**. Names are exactly the `Dumy` attachment
  points from `RolePart.ini` (`v_body`, `v_armet`, `v_l_weapon`, `v_l_foot`, …).
* Two `u32` follow. The first is 0 or 2 corpus-wide — **2 on skinned `v_body`
  meshes, 0 on rigid attachments** — so it reads as a skinning flag.
* The second is the **vertex count**, and a third is a bone/blend count.
* The **index block** is locatable: a `u32 faceCount` followed by
  `faceCount × 3` `u16` indices, all `< vertexCount`, ending 140–230 bytes
  before the chunk end. On `c3/mesh/002135000.c3`: 495 vertices, 665 faces,
  max index 494 — an exact fit.

What the engine says the data is (from `graphic.dll`'s **unmangled** export
signatures, which are reliable):

```cpp
Phy_DynamicCreateEx(u32, u32, int,
    D3DXVECTOR3* pos, D3DXVECTOR2* uv0, D3DXVECTOR2* uv1,
    D3DXVECTOR3* normal, u32* color, u16* index, bool);
```

so a physique is positions + two UV sets + normals + packed colours + 16-bit
indices. `Phy_Load` additionally reads 60-byte matrix records and two optional
**named** blocks, `C3EXP_COLOR` and `C3EXP_STRETCH`.

**Not yet solved: the byte offsets of the vertex arrays.** Two rigid meshes fit a
clean per-vertex stride exactly (`350×40` bytes, `24×76` bytes, both matching
`12K + 28` for K frames), but **917 of 1,110** PHY chunks do not fit any flat
stride — the region between header and index block also carries bone matrices,
per-frame data and the optional named blocks. Curve-fitting past this point
starts inventing structure.

> **How that guess scored, now that §9 has the answer.** The two strides were
> real: `PHY` is **76 bytes/vertex** and `PHY4` is **40 bytes/vertex**, fixed
> per tag. But `12K + 28` for K frames was a coincidence, not a structure —
> there is no frame term in the stride. The 917 "misfits" were an artefact of
> pooling both tags together and of a naive index-block search, not evidence of
> a variable layout. A textbook case of the method note in `STATUS.md`:
> curve-fitting produced a plausible formula that was wrong, and reading
> `Phy_Load` produced the right one.

**The honest next step** is disassembling `graphic.dll!Phy_Load`
(RVA `0x59CB0` / `0x5B5E0`) read-call by read-call. It reads through a
`C3DataLoader` vtable where `[rax+0x18]` reads a `u32` and `[rax+0x20]` reads
N bytes, so the exact field order is recoverable — it is a few hours of careful
work with `tools/disfn.py`, not a research problem. **This is the gate for
Blender import/export**, and it is the top of the queue.

> **UPDATE — this is now DONE.** `Phy_Load` was disassembled and the full PHY
> vertex/index layout recovered from the code. See **§9 below**, which
> supersedes the "PARTIAL" status of this section. Parser: `core/c3phy.py`;
> corpus validation: `tools/validate_phy.py` (15,103 / 15,103 chunks parse
> byte-exactly).

---

## 6. Maps

### DMap — VERIFIED on all 136 files

```
8-byte header, one of two forms:
    u32 version, u32 0          versions 1003/1004/1005/1006   (115 files)
    "DMAP" + 3-char ver + NUL   versions "100"/"101"           (21 files)
char[260] puzzle path           FIXED WIDTH, not null-terminated
u32 width, u32 height
for each row y:
    for each col x:  u16 blocked, u16 surface, i16 elevation
    u32 rowChecksum
u32 passagewayCount; { u32 x, u32 y, u32 index } * count
u32 layerCount; { u32 layerType, ...body } * count
```

> **Correction to the earlier session's note.** `docs/CONTEXT.md` recorded the
> puzzle path as null-terminated. It is not — it is a fixed `char[260]`. Reading
> it as null-terminated yields width/height of 0×0 on every file.

Validated by rendering: `comod.py map arena.DMap --ascii 60` produces a coherent
arena with a walled boundary and an entrance corridor, not noise.

Layer *bodies* are typed by a leading `u32` (cover / effect / scene / sound) and
are documented in `refs/conquer-online-wiki/Files/DMap.md`; `coassets.DMap`
locates the layer table but does not yet decode bodies.

### Pul (background puzzle) — VERIFIED on all 381 files

```
char[8] version ("PUZZLE2" ×350, "PUZZLE" ×31)
char[256] ani path        e.g. ani\skybg.ani
u32 width, u32 height
u16 aniIndex[height*width]
if PUZZLE2: u32 rollSpeedX, u32 rollSpeedY
```
Every file's length matched exactly. Tile indices key into the named `.ani` file
in `$ROOT/ani/` (which is JSON in this build), which in turn names the actual
`data/map/puzzle/**.dds` art. **So changing a map's background is a texture
edit, not a binary edit.**

#### Where the puzzle sits on the cell grid — VERIFIED on 131 of 132 maps

```
G   = ini/GameMap.json[mapId].PuzzleGridSize   (256 on 134 rows, 128 on 22)
K   = pul.width * G / 64

lattice point (gx, gy)  ->  px = (gx - gy + K) * 32
                            py = (gx + gy - K) * 16
```

One map cell is a **64 × 32 pixel isometric diamond**; cell `(x, y)` is the
diamond with corners `(x,y)`, `(x+1,y)`, `(x+1,y+1)`, `(x,y+1)`. The origin `K`
is forced by the DMap's own dimensions, because the cell grid is the bounding
box of the rotated image: `W = H = pul.width*G/64 + pul.height*G/32`. That
identity is exact on 131 of the 132 maps with a `.pul` (`sky` is the exception),
which is also why **every** shipped `.DMap` is square.

Full derivation, corpus evidence, RVA citations and the four `map/PuzzleSave/*.pux`
(`TqTerrain`) maps that use a different, undecoded format: **`docs/ground_art.md`**.
Tooling: `py -3 tools/puzzle.py --verify`.

### Scene — VERIFIED on all 299 `.scene` files

`u32 partCount`, then per part: `char[256] path`, `char[64] title`, eight `u32`,
`i32` offsetElevation, then `width*height` cells of 12 bytes
(`u32 blocked, u32 surface, i32 elevation`). Every file consumed to exactly its
length.

The eight `u32` are, in order — and note the wiki's first two labels are
**wrong**, as shown by matching 105 `map/ScenePart/*.Part` text files against
the binaries compiled from them:

| field | meaning |
|---|---|
| 0..1 | the sprite's **pixel** draw offset (`= .Part OffsetX= / OffsetY=`) |
| 2 | frame interval, ms (`= AniInterval=`) |
| 3..4 | width, height, in **cells** |
| 5 | thickness (`= Thick=`) |
| 6..7 | the part's **cell** offset inside the scene |

**A scene carries passability.** Its per-cell `blocked` flag replaces the
`.DMap` cell underneath, and the cells run *backwards* from
`layer.origin + part.cellOffset`. This is what makes `newbie`'s stepping-stone
bridge walkable at all. Full rule, corpus evidence and the camera that follows
from it: **`docs/map_scenery.md`**. Tooling: `py -3 tools/scene.py --verify`.

### ScenePart — PLAIN TEXT

`map/ScenePart/*.Part` are **not** binary. All 126 are CRLF text:

```ini
AniFile=ani\MapScene.ani
AniTitle=bridge01.tga
OffsetX=-223
Width=9
Cell[0,0]={0,0,0}
```

Easiest entry point in the whole map system — edit with any text editor.

### ⚠️ The DMap cell grid is integrity-checked — the art is not

`integrity.json` covers **all 136 distinct `.DMap` files** (16 hex chars,
64-bit, algorithm not yet identified) across 162 rows. Editing a `.DMap` may
trip a client-side check.

**Nothing else in the map system is covered.** Puzzle `.pul`, `.scene`,
`.Part`, and every `.dds` a map references sit outside the manifest, so tiles,
scenery and backgrounds are free to edit. Only the cell grid is gated. See
`docs/mapeditor.md` for the measured breakdown.

---

## 7. Workflow

```bash
py -3 tools/comod.py find-item "IronHelmet"
```
```bash
py -3 tools/comod.py show 002135300
```
```bash
py -3 tools/comod.py extract c3/texture/002135300.dds --png
```
```bash
py -3 tools/comod.py import-png mods/work/edited.png c3/texture/002135300.dds
```
```bash
py -3 tools/comod.py diff
```
```bash
py -3 tools/comod.py install --dry-run
```

Staging lives in `mods/stage/`, mirroring the install layout. `install` requires
`--yes` and backs up any loose file it displaces, so `uninstall` reverts exactly
what was added.

### One slot per install, because there is rarely one install

`comod.py installs` prints the stage tree and every client something has been
installed to. **Backups and the manifest are keyed by install root** —
`mods/installs/<slug>/` — rather than the single `mods/backup/` +
`mods/manifest.json` they used to share. That was survivable while there was
one place to install to. With two it was not:

* installing to B overwrote A's manifest, so A could no longer be reverted;
* `if not b.exists()` meant B's originals were **never backed up**, because A's
  file was already sitting at that path in the shared backup tree — so
  reverting B restored *A's* files into B;
* `uninstall` never read the root the manifest recorded. `--root` carried a
  default, so `args.root or man["root"]` always took the default and reverted
  against whichever install was conventional.

Now: an ambiguous `uninstall` lists the candidates and refuses rather than
picking, a single install still needs no `--root`, installing twice without
reverting is refused (the second install's "originals" would be the first
install's files), and a pre-split manifest is migrated into its own slot on
first use rather than stranded.

### What counts as somewhere a mod can go

Not `coroot.looks_like_root`. That answers whether the **viewer** can browse a
baseline and demands `c3.wdf`, `data.wdf`, `ini/` and `bin/64/` — which the
community clients this project exists to support do not have. Zephyr ships
`c3.tpi`/`c3.tpd` and no `bin/64/`, so that gate refused to install into it.

`comod.moddable_install` asks the weaker question the mechanism actually
needs, since a swap is only a loose file read before the archive: the `ini/`
tables, and somewhere art lives (`c3.wdf`, `c3.tpi`, `c3.tpd` or `c3/`).
`C:\Windows` fails on the first.

Visual discovery:

```bash
py -3 tools/cobrowse.py textures --table body --limit 400
```

Writes a self-contained HTML contact sheet to `out/browse/` with thumbnails,
formats, logical paths and copy-to-clipboard commands.

---

## 8. Status and what's next

> **`docs/STATUS.md` is the authority on project state.** The list below was
> written during the first asset pass and is kept only to show what this
> document originally claimed. Everything in the "Open" list has since landed.
> Do not plan from it.

**Solved and usable now** *(as of the first pass)*
- Loose-over-archive override — the whole basis of modding
- WDF reading + recovered filenames (then 10,126; now 24,426 — see §2)
- Full DDS extract → PNG → edit → DDS → install round-trip
- C3 container: byte-exact round-trip on all 6,540 files
- Appearance ID → mesh/texture resolution (94/98/79%)
- DMap, Pul, Scene parsers verified against every file
- Staged install/uninstall with backup and manifest

**Open** *(all five have since been closed)*
1. ~~**`Phy_Load` disassembly → PHY vertex layout.**~~ **DONE** — §9/§10.
   `core/c3phy.py`; `tools/validate_phy.py` parses 15,103/15,103 chunks.
2. ~~**Blender bridge.**~~ **DONE** — `blender/io_scene_c3`, import→export
   byte-identical on 15,103/15,103 chunks. See `docs/blender_addon.md`.
3. ~~`MOTI` skeletal animation decode.~~ **DONE** — see `docs/animation.md`.
4. ~~DMap layer bodies~~ **DONE** — see `docs/map_scenery.md` and
   `docs/mapeditor.md`. The 64-bit `integrity.json` hash is still unidentified.
5. ~~A live 3D previewer.~~ **DONE** — `py -3 tools/coviewer.py`, see
   `docs/viewer.md`. Renders with the engine's winding/culling, applies the
   chunk matrix, decodes DDS independently of Pillow, shows loose-vs-archive
   provenance, and does texture swaps committed through `comod.py`.

**Constraints kept**: nothing here writes to the game install except
`comod.py install`/`uninstall`, both of which require `--yes`. No server contact.
No touching the Themida-protected binary.

---

## 9. PHY geometry — SOLVED, from the code

Recovered by disassembling `graphic.dll!Phy_Load` (RVA `0x59CB0`) instruction by
instruction. **No stride was guessed or curve-fitted.** Every offset, size and
loop bound below is stated by the machine code; the RVA of the relevant
instruction is cited so any claim can be re-checked with
`py -3 tools/disfn.py graphic.dll <rva>`.

**Tools**

| Tool | Purpose |
|---|---|
| `core/c3phy.py` | the parser + OBJ exporter (`--obj <dir>`) |
| `tools/validate_phy.py` | corpus validation |
| `tools/readtrace.py` | dumps the loader-call script of any function |
| `tools/vtable.py`, `tools/xref.py`, `tools/riprefs.py`, `tools/d3dlayout.py` | the RE helpers used to get here |

**Validation — VERIFIED.** `tools/validate_phy.py` parses **15,103 of 15,103**
PHY chunks across both archives, the loose `c3/` and `data/` trees and
`out/wdf/sample/`, and every single one is consumed to *exactly* its declared
chunk length (`trailing == 0`), with every triangle index `< vertexCount`.
That is the decisive test: a wrong stride cannot land on the exact end of
15,103 chunks of wildly varying size (3–2,483 vertices, 1–3,674 faces).

### 9.1 How the loader reads

`Phy_Load` never touches a file directly; it goes through a `C3DataLoader`
vtable (`.rdata 0x1EC320`, **VERIFIED** by dumping it):

| slot | offset | method |
|---|---|---|
| 2 | `[+0x10]` | `GetChunk(void* dst8)` — reads `char[4] tag; u32 length` |
| 3 | `[+0x18]` | `Read(void* dst, u32 count)` |
| 4 | `[+0x20]` | `Seek(int offset, int origin)` — origin 1 = current |
| 5 | `[+0x28]` | `Tell()` |
| 9 | `[+0x48]` | `GetName()` — used only for error logging |

There are two implementations, a `TqFile`-backed one and a memory-backed one
(the literal `MEMORY FILE` at `.rdata 0x1EC378`); both present the same API, so
the field order is identical whether a mesh comes from disk or from a WDF.

### 9.2 The five PHY variants — VERIFIED

The chunk tag selects the vertex encoding. The dispatcher is at RVA `0x25BEC`:
it compares the tag against `'P','H','Y'` and switches on the **4th byte**,
passing four bools to `Phy_Load` (RVA `0x25BF1`–`0x25C7A`).

| tag | file bytes / vertex | stores normals | 36-byte legacy gap | `STEP` blocks | in this build |
|---|---:|---|---|---|---|
| `PHY ` | **76** | no — generated | yes | no | 11,094 chunks |
| `PHY2` | **88** | yes | yes | no | none shipped |
| `PHY3` | **52** | yes | no | no | none shipped |
| `PHY4` | **40** | no — generated | no | no | 4,009 chunks |
| `PHY5` | **60** | yes | no | yes | none shipped |

The read pattern per vertex (RVA `0x59E71`–`0x59FD5`):

```
has_normal && legacy_gap :  Read(v+0x00, 0x0C)  Seek(+0x24)  Read(v+0x0C, 0x28)
has_normal && step       :  Read(whole array, 0x3C * n)          one block
has_normal               :  Read(v+0x00, 0x34)
!has_normal && legacy_gap:  Read(v+0x00, 0x0C)  Seek(+0x24)  Read(v+0x0C, 0x1C)
!has_normal              :  Read(v+0x00, 0x28)
```

> **Warning.** `PHY2` / `PHY3` / `PHY5` are VERIFIED from code but UNCONFIRMED
> against data — this install ships only `PHY ` and `PHY4`. The strides come
> from the same switch statement that produces the two verified ones, so
> confidence is high, but nothing has been round-tripped.

When a variant does not store normals, the engine **generates** them
(RVA `0x5A8C0`): accumulate the face normal onto each of the triangle's three
vertices, then normalise. `core/c3phy.generate_normals()` reproduces it.

### 9.3 The vertex record — VERIFIED

In memory every vertex is **0x3C = 60 bytes** (`malloc(0x3C * n)` +
`memset`, RVA `0x59E39`). The on-disk record is a prefix of that layout,
optionally with the 36-byte gap after the position.

| offset | size | field | evidence |
|---:|---:|---|---|
| `0x00` | 12 | `float3 position` | written by every variant, RVA `0x59EFD` |
| `0x0C` | 8 | `float2 uv0` | to GPU `TEXCOORD0`, RVA `0x5A774` |
| `0x14` | 4 | `u32 colour` (packed RGBA) | to GPU `COLOR0` at GPU+0x38, RVA `0x5A870` |
| `0x18` | 4 | `u32 boneIndex0` | to GPU `BLENDINDICES0`, **low byte only**, RVA `0x5A784` |
| `0x1C` | 4 | `u32 boneIndex1` | to GPU `BLENDINDICES1`, low byte only, RVA `0x5A78D` |
| `0x20` | 4 | `float weight0` | to GPU `BLENDWEIGHT0` as `round(w*255)`, RVA `0x5A796` |
| `0x24` | 4 | `float weight1` | only tested `!= 0`, RVA `0x5A284` — see below |
| `0x28` | 12 | `float3 normal` | absent in `PHY `/`PHY4` |
| `0x34` | 8 | `float2 uv1` | present only in `PHY5`, to GPU `TEXCOORD1` |

**Skinning is exactly two influences per vertex.** The GPU weight for the
second bone is *not* read from `0x24` — it is computed as `255 - weight0`
(`not al`, RVA `0x5A869`), so **the two weights are constrained to sum to 1**.
Field `0x24` is used only to decide whether bone1 participates at all when
building the bone palette. Observed `weight0` values are clean rationals
(1.0, 0.5, 2/3, 5/6, 1/3 …), consistent with 3DSMax skin weights.

**Bone indices are effectively 8-bit.** The engine truncates to a byte for the
GPU, and warns (incrementing the exported global `g_nOverBonePhyCount`) when a
mesh needs more than **200** bones (RVA `0x5A5C0`). Measured maximum in this
corpus: **97**. Treat 255 as the hard ceiling.

The bone palette is built by `Phy_Load` itself (RVA `0x5A1A0`): a sorted unique
set of every `boneIndex0`, plus `boneIndex1` where `weight1 != 0`. It is not
stored in the file. `PhyMesh.bones` reproduces it.

#### The GPU vertex is a *different* 60-byte packing

Recovered from the `D3D10_INPUT_ELEMENT_DESC` arrays built in
`Phy_SystemInit` (RVA `0x5C76A`–`0x5C8F0`) — **VERIFIED**, semantic names and
`DXGI_FORMAT` values read straight out of the code:

| offset | format | semantic |
|---:|---|---|
| 0 | `R32G32B32_FLOAT` | `POSITION0` |
| 12 | `R32G32B32_FLOAT` | `NORMAL0` |
| 24 | `R32G32_FLOAT` | `TEXCOORD0` |
| 32 | `R32G32_FLOAT` | `TEXCOORD1` |
| 40 | `R8_UINT` | `BLENDINDICES0` |
| 41 | `R8_UINT` | `BLENDINDICES1` |
| 42 | `R8_UNORM` | `BLENDWEIGHT0` |
| 43 | `R8_UNORM` | `BLENDWEIGHT1` |
| 56 | `R8G8B8A8_UNORM` | `COLOR0` |

Do not confuse the two layouts. The file uses the first table, the renderer
the second.

### 9.4 Chunk layout end to end — VERIFIED

```
u32     nameLen
char[nameLen] name              e.g. "v_body", "v_armet", "v_l_weapon"
u32     unknown0                READ AND IGNORED by the engine (RVA 0x59DEC)
u32     vertexCountA
u32     vertexCountB            vertexCount = A + B      (RVA 0x59E1F: add)
vertex[vertexCount]             stride per the table in 9.2
u32     faceCountA
u32     faceCountB              faceCount = A + B        (RVA 0x5A005: add)
u16     indices[faceCount * 3]  ONE contiguous block, triangle LIST
u32     labelLen
char[labelLen] label            if labelLen == 11 it is compared against
                                "C3EXP_COLOR"; otherwise simply skipped
float3  boundsA
float3  boundsB                 sorted componentwise into min/max (RVA 0x5A0EA)
float   matrix[16]              4x4, row-vector convention
u32     frameCount              -> C3Phy+0x190
u32     nAlpha ;  byte[16][nAlpha]      \
u32     nDraw  ;  byte[16][nDraw]        >  C3Key, see 9.6
u32     nChgTex;  byte[16][nChgTex]     /
[ "STEP" u32 u32 ]              optional, else the 4 bytes are un-read
[ "2SID" ]                      optional -> two-sided rendering
[ "BILB" | "BIB2" | "BIB3" | "BIB4" ]   optional -> billboard mode 1..4
PHY5 only:
  [ "STEP1" float f0, f1 ]      stored as f/33.0   -> C3Phy+0x1A0, +0x1A4
  [ "STEP2" float g0, g1 ]      stored as g/33.0   -> C3Phy+0x1A8, +0x1AC
  (or a "STEP2"-first form whose payload the engine reads and immediately frees)
```

Optional tags are probed by reading 4 (or 5) bytes and `Seek`-ing back by -4
(`0xFFFFFFFC`, RVA `0x5B24A`) or -5 (`0xFFFFFFFB`, RVA `0x5B507`) when they do
not match. A writer must therefore emit them in this exact order.

**There is no WDF-style magic and no checksum anywhere in a PHY chunk.**

### 9.5 Things a mesh exporter must get right

1. **Indices are `u16`, three per face, triangle list.** Not strips. The whole
   block is read in one `Read` of `faceCount*3*2` bytes (RVA `0x5A044`), so a
   mesh cannot exceed **65,535 vertices**. Largest observed: 2,483.

2. **The A/B count split is real but the loader discards it.** `Phy_Load`
   simply adds the two vertex counts and the two face counts. Empirically
   (629 meshes with a non-zero B count) **599 = 95% form a clean partition** —
   the first `faceCountA` triangles reference only the first `vertexCountA`
   vertices, the rest only the remainder. So the file records a two-group
   split that the engine ignores. Preserve it on export if you can; nothing
   breaks if you write everything as group A and B = 0.

3. **The 4x4 matrix is applied to positions at load time** (RVA `0x5A735`),
   row-vector convention — basis in rows 0-2, translation in row 3.
   Most chunks (2,467 of 2,667 sampled) carry identity; the rest carry a real
   rotation/translation/scale. `apply_matrix_to()` implements it. An importer
   that skips this will place limbs and weapons wrongly.

4. **`labelLen`/`label` is usually the source texture path from the original
   3DSMax export**, not `C3EXP_COLOR` — e.g. `texture\armet001.tga`,
   `c3\Effect\tt\1.tga`, and a great many GBK-encoded Chinese paths. Useful
   provenance for modding; the engine only ever looks for the exact
   11-character string `C3EXP_COLOR`, which flags the mesh as using its
   per-vertex colour.

5. **Alignment: none.** Every array is tightly packed; there is no padding
   between the header, the vertex array, the index array or the trailing
   blocks. Everything is little-endian.

6. **The declared bounding box is not reliable as a tight AABB.** It is read as
   two `float3`s and sorted componentwise (VERIFIED), and the engine uses it
   for frustum culling. Measured against 4,622 real meshes: the declared
   *extents* match the geometry in ~95% of cases, but the *origin* matches in
   only ~50% — the rest are the same size, translated. Recompute it for
   Blender; write back whatever you like, since nothing validates it.

### 9.6 C3Key — cross-validated against TQ's own header

The three 16-byte arrays land at `C3Phy` `+0xA0/+0xA8`, `+0xB0/+0xB8`,
`+0xC0/+0xC8` — count then pointer, three times. That is **exactly** the
`C3Key` struct in the leaked `c3_key.h` reproduced at
`refs/conquer-online-wiki/Files/C3.md`:

```cpp
struct C3Key {
    DWORD dwAlphas;      C3Frame* lpAlphas;
    DWORD dwDraws;       C3Frame* lpDraws;
    DWORD dwChangeTexs;  C3Frame* lpChangeTexs;
};
struct C3Frame { int nFrame; float fParam[1]; BOOL bParam[1]; int nParam[1]; };  // 16 bytes
```

and `Phy_Calculate` consumes them via the exported
`Key_ProcessAlpha` / `Key_ProcessDraw` / `Key_ProcessChangeTex`, which the wiki
header also declares. Independent agreement between the disassembly and TQ's
own source is about as strong as validation gets. `c3phy.py` keeps the 16-byte
records opaque — they are per-frame alpha / visibility / texture-swap tracks,
not geometry.

### 9.7 Coordinate system and winding — for Blender

**Winding — VERIFIED from code, confirmed on data.** The engine's generated
face normal is `cross(p2-p1, p1-p0)` (derived from the multiply/subtract order
at RVA `0x5A946`–`0x5A974`). Tested on 496 real character meshes, that normal
points **away from the mesh centroid on 482 of them (97%)**. So it is the
outward normal, which means the triangles are wound **clockwise as seen from
outside in a right-handed frame** — the Direct3D convention, as expected.

**Axes — INFERRED from measurement.** Median extents over 496 character
meshes: X ~ 178, Y ~ 46, Z ~ 176. Y is the thin axis (front-to-back), X is
width, Z is height. A `v_body` mesh occupies `z` in `[-height, 0]` once the
chunk matrix is applied, so **+Z points down**.

**Conversion — VERIFIED empirically.** Negating Z does both jobs at once: it
stands the model upright in Blender's Z-up frame, and because a single-axis
mirror flips handedness it simultaneously corrects the winding.

```
Blender position = (x, y, -z)        and KEEP the original index order
Blender normal   = (nx, ny, -nz)
Blender UV       = (u, 1 - v)        OBJ/Blender V runs opposite to D3D
```

The equivalent alternative is to keep the coordinates and reverse each
triangle. **Do exactly one of the two, never both.** This was checked by
brute force over seven candidate transforms; `to_blender()` in
`core/c3phy.py` documents the test.

### 9.8 Worked example

```bash
py -3 core/c3phy.py "$ROOT/c3/mesh/001134070.c3" --obj out/c3/obj
```
```
001134070.c3  PHY   'v_armet'     verts=24  (24+0)   faces=12   bones=1   trailing=0
001134070.c3  PHY   'v_l_weapon'  verts=6   (6+0)    faces=8    bones=1   trailing=0
001134070.c3  PHY   'v_r_weapon'  verts=6   (6+0)    faces=8    bones=1   trailing=0
001134070.c3  PHY   'v_body'      verts=636 (604+32) faces=834  bones=39  trailing=0
```

Note the shape of a character `.c3`: one skinned `v_body` mesh plus small rigid
**attachment-point** meshes (`v_armet` = helmet socket, `v_l_weapon` /
`v_r_weapon` = weapon sockets) whose names match the `Dumy` entries in
`RolePart.ini`. Those tiny meshes are sockets, not visible geometry.

Re-run the full corpus check any time with:

```bash
py -3 tools/validate_phy.py
```

### 9.9 What is still open

- `unknown0` (the `u32` right after the name) is **read and never used** by
  `Phy_Load`. Observed values: 0 (1,955), 2 (583), 1 (129). Whatever it meant,
  this engine ignores it — so an exporter can write 0 safely.
- The **36-byte legacy gap** in `PHY `/`PHY2` is skipped by `Seek`, never read.
  9 floats' worth of something the modern engine dropped. Harmless to preserve
  verbatim, which is what a round-tripping editor should do.
- `PHY2`/`PHY3`/`PHY5` strides are code-derived only (see the warning in 9.2).
- The `MNEW` (31 chunks) and `CCFL` (26 chunks) tags in the 2022 loose patches
  remain undecoded. **VERIFIED**: neither string appears anywhere in
  `graphic.dll`, `GraphicData.dll`, `Role3D.dll` or `TqPackage.dll`, and the
  `PHY` dispatcher at RVA `0x25BEC` rejects any tag that is not `PHY?`. So
  whatever consumes them lives inside the Themida-packed `ImConquer.exe` and
  is out of reach by static analysis.
- ~~`MOTI` skeletal animation is still undecoded~~ — **CORRECTED 2026-07-26.**
  `MOTI` is **decoded**, and it was never behind Themida: `graphic.dll` exports
  `Motion_Load` (RVA `0x557A0`) and `Motion_GetMatrix` (RVA `0x551A0`) as
  ordinary named functions. All four encodings (`RAW`, `KKEY`, `ZKEY`, `XKEY`)
  are read, plus `SHAP`/`SMOT`. Parsers: `parse_moti` / `parse_shap` /
  `parse_smot` in `tools/effects.py`; format in **`docs/effects.md` §6.2**;
  validation `py -3 tools/effects.py --validate` → **15,278 / 15,278** MOTI
  chunks parsed to exactly their declared length. This unblocks adding and
  removing whole meshes — see **§11** for what that does and does not permit.

---

## 10. PHY writing — round-trip proven, and what it took

§9 established that the layout **parses**. This section establishes that it
**round-trips**: `tools/c3write.py` re-serializes a parsed chunk and the bytes
come back identical. That is a strictly stronger claim than `trailing == 0`,
because it also pins down every field the parser was previously discarding.

**Validation — VERIFIED.** `tests/test_roundtrip.py` parses every PHY chunk
reachable in this install, re-serializes it, and compares:

| tag | chunks | byte-exact |
|---|---:|---:|
| `PHY ` | 11,094 | **11,094 (100%)** |
| `PHY4` | 4,009 | **4,009 (100%)** |
| **total** | **15,103** | **15,103 (100%)** |

Whole `.c3` containers rebuilt byte-exactly (PHY chunks re-serialized, all other
chunks passed through): **5,122 / 5,122**.

`PHY2` / `PHY3` / `PHY5` write paths are implemented from the same switch
statement, but **nothing ships them in this build, so they remain unvalidated
against data** — the same caveat §9.2 already carries for reading. The `PHY5`
`STEP1`/`STEP2` region is re-emitted verbatim rather than structurally, because
re-emitting bytes nobody has ever seen is the only honest option.

### 10.1 Four fields §9 recorded as "ignorable" that a writer must not drop

Getting from "parses" to "byte-exact" required capturing four things the
original parser threw away. All four are now kept by `c3phy.parse_phy`
(`name_raw`, `label_raw`, `bbox_a`/`bbox_b`, `Vertex.gap`) and written back
verbatim:

1. **The name is not NUL-terminated.** `nameLen` is authoritative and the
   parser's `split(b"\x00")[0]` silently discarded anything after an embedded
   NUL. Keep the raw `nameLen` bytes.
2. **The bounding-box pair is stored unsorted.** The loader sorts componentwise
   (RVA `0x5A0EA`), so writing back a sorted min/max loads identically but is
   not the same bytes. Keep the original `(boundsA, boundsB)` order.
3. **The label bytes must never go through a codec.** §9.5 note 4 already said
   these are usually GBK source-texture paths; decoding to `str` and re-encoding
   is not guaranteed lossless. Keep the raw `labelLen` bytes and decode only for
   display.
4. **`unknown0` must be preserved, not zeroed.** §9.9 said "an exporter can
   write 0 safely" — true for the *engine*, false for byte-exactness. Observed
   0 (3,892), 2 (601), 1 (129) in the loose tree.

### 10.2 NEW — the 36-byte legacy gap is entirely zero

§9.9 listed the `PHY `/`PHY2` legacy gap as "9 floats' worth of something the
modern engine dropped", to be preserved verbatim. **Measured: it is zero on all
1,426,831 legacy-gap vertices in this install** — 55,240 in the loose tree and
1,371,591 across `c3.wdf` + `data.wdf`, with zero exceptions.

So there is nothing to preserve. `c3write` emits 36 zero bytes and the corpus
round-trips exactly. The field is dead, not merely unused — whatever wrote these
files stopped filling it long before this build. `Vertex.gap` still carries the
original bytes so a future file that *does* use it round-trips anyway.

### 10.3 NEW — `STEP`'s two u32 are float bit patterns

§9.4 records `[ "STEP" u32 u32 ]` as two `u32`s, which is what the loader reads.
But **639 chunks store values above `0x7FFFFFFF` there** (164 in the first slot,
475 in the second) — e.g. `0xBBE56042`, `0xBC23D70A`. Reinterpreted as floats
those are `-0.0070` and `-0.0100`: small negative scalars, not counts.

**INFERRED**: the `STEP` payload is a pair of floats that the loader happens to
move as untyped 32-bit words. Nothing depends on the interpretation — a writer
must move them as raw `u32` either way — but it matters to any tool that stores
them in a signed 32-bit field. (This is exactly how it surfaced: Blender's
`IDProperty` ints are `int32` and overflowed.)

### 10.4 NEW — degenerate and duplicate triangles are real

The corpus is not clean geometry. In the loose tree alone: **244 degenerate
triangles** (a repeated index) and **5,838 duplicate triangles** (same three
vertices as an earlier face), spread over **186 of 4,622 chunks**. Every chunk
references all of its vertices — there are no orphans.

Consequence for any DCC round trip: mesh-cleaning passes destroy these. In
Blender specifically, `Mesh.validate()` deletes them, so the importer must not
call it. Verified: without `validate()`, Blender preserves degenerate and
duplicate faces exactly, including through a save/reload cycle.

### 10.5 NEW — the `(u, 1 − v)` UV convention is not byte-reversible

§9.7 gives the Blender UV conversion as `(u, 1 - v)`. It is the right
*convention*, but it is **not an involution in float32**: `1 - v` is computed
and stored at single precision, and flipping back does not always land on the
original bit pattern. Measured over every UV in the loose tree: **32,227 of
1,475,160 V coordinates (2.2%) do not survive a double flip.** The failures
cluster on small `|v|`, where `1 - v` sits near 1.0 and has a coarser ULP than
`v` does.

Positions are safe: negating Z is exact, with 0 failures over the same corpus.

So a byte-exact tool must keep the original `v` alongside the flipped one and
prefer it when the flipped value is untouched. `blender/io_scene_c3` does this
with a hidden per-vertex attribute; see `docs/blender_addon.md` §7.

### 10.6 The A/B count split on write

§9.5 note 2 says the split can be collapsed safely. `c3write.serialize_phy`
preserves it exactly when the geometry is unchanged; when the counts no longer
add up it keeps the partition if the edit is still expressible (group B absorbed
the change), and otherwise collapses to `A = n, B = 0`, which the loader treats
identically since it simply adds the two.

### 10.7 Tools

| Tool | Purpose |
|---|---|
| `tools/c3write.py` | PHY chunk + container serializer; `verify` subcommand |
| `tools/c3tex.py` | mesh id -> DDS resolution, by inverting the appearance tables |
| `tools/meshtex.py` | superset of the above: 13 rules, ranked + confidence-scored, 99.7% of meshes |
| `tools/build_addon.py` | vendors the stdlib format modules into the Blender addon |
| `tests/test_roundtrip.py` | the corpus byte-exactness gate |
| `tests/test_blender_roundtrip.py` | the addon acceptance test |
| `tests/c3_diagnose.py` | field-level diff when a round trip is not exact |
| `comod.py stage-mesh` | validate an exported `.c3` and stage it as a mod |

All of `c3phy` / `c3write` / `c3tex` are **pure stdlib and import no `bpy`**, so
the corpus gate runs on bare system Python without Blender.

---

## 11. Adding and removing whole meshes — the PHY↔MOTI pairing

§10 shipped a restriction: *"you cannot add or remove whole meshes, because the
`MOTI` chunks that ride along are still undecoded"*. `MOTI` is now decoded
(`docs/effects.md` §6.2), so this section re-examines that restriction and
lifts it **partially**. The boundary is precise and it is not conservatism for
its own sake — there is a concrete mechanism that breaks.

### 11.1 The binding is POSITIONAL — VERIFIED

A `MOTI` chunk contains **no name and no ID**. Its body starts straight in on
`u32 boneCount; u32 frameCount`. So nothing inside a motion track says which
mesh it belongs to; the association can only be positional. It is:

**the i-th `PHY` chunk in a container is animated by the i-th `MOTI` chunk.**

Three independent lines of evidence:

1. **Code.** `MeshCreate` (RVA `0x28360`) allocates the mesh object, loads the
   geometry, then calls `MotionCreate` (RVA `0x28470`) on the **same file** and
   stores the result at `mesh+0x2E0`. The combined walker `sub_28D5B` reads
   chunks with `Common_GetChunk` and dispatches each on its tag to either
   `Phy_Load` or `Motion_Load`, appending to that tag's own list. Only the
   relative order **within** a tag matters.
2. **Layout.** That is exactly why both container layouts work. Both
   `PHY MOTI PHY MOTI …` (interleaved) and `PHY PHY PHY PHY MOTI MOTI MOTI MOTI`
   (grouped) occur in bulk, and they are equivalent.
3. **Data.** On all **792** multi-mesh containers in the loose tree, the i-th
   `MOTI`'s `boneCount` covers the i-th `PHY`'s bone palette. A reversed or
   shifted pairing only fits **33.6%** — and the cases where it fails are
   exactly the discriminating ones.

### 11.2 NEW — every PHY has a MOTI, with no exceptions

Across the loose tree and both archives: **every container holding at least one
`PHY` chunk holds exactly the same number of `MOTI` chunks — 5,083 of 5,083.**
Not one `PHY` ships without a paired motion track.

So a writer that changes the mesh count **must** change the motion count
identically. `tools/c3write.rebuild_c3()` does: removing a mesh drops its
paired `MOTI`, and adding one synthesises a neutral track.

### 11.3 NEW — the binding also crosses file boundaries, and that is the limit

`ini/3dmotion.ini` maps action IDs to **motion-only** `.c3` files
(`c3/0001/000/001.c3` …) — containers with `MOTI` chunks and no `PHY` at all.
Those replace a character mesh's own tracks. Because `MOTI` has no name, they
too bind **by ordinal**, across the file boundary.

This is directly measurable. The 493 four-mesh character meshes in `c3/mesh/`
come in two name orders, and the shipped motion sets come in two matching
`boneCount` shapes:

| mesh order | files | motion set `boneCount` |
|---|---:|---|
| `[v_armet, v_l_weapon, v_r_weapon, v_body]` | 362 | `[1, 1, 1, 81]` / `[1, 1, 1, 84]` |
| `[v_body, v_armet, v_l_weapon, v_r_weapon]` | 126 | `[84, 1, 1, 1]` |

The skinned body — the only mesh with a real palette (~48–51 bones) — sits in
the slot whose motion track has the large bone count, in **both** families. The
motion sets are authored per mesh-order family. Add or remove a mesh and that
correspondence shears: the body would be driven by a one-bone socket track.

**Those motion sets are shared by thousands of appearances and live in the
archives. A mod cannot re-cut them.** That is the whole restriction, and it is
not liftable by better tooling.

### 11.4 What is now permitted

| container | add/remove | why |
|---|---|---|
| animated **only by its own `MOTI`** (effects) | **allowed** | the entire ordinal contract is inside the one file being edited |
| driven by a **shared external motion set** (body, armour, hair, weapon, mount, monster, npc) | **refused** | §11.3 |
| unclassifiable | **refused** | cannot prove no external set targets it |

`tools/c3tex.MotionBinding.classify(logical)` implements this and is
**default-deny**: it returns `FREE` only for a container named by
`ini/3DEffectObj.ini` that is *not* referenced as a `Mesh<i>` by any appearance
table; everything else is `LOCKED` or `UNKNOWN`. Over the loose tree that is
565 free, 655 locked, 796 unknown. It is deliberately biased toward
false-locked — an effect file whose numeric stem collides with a weapon mesh id
comes out `LOCKED`, which is the harmless direction.

Effect authoring is precisely where adding and removing quads is useful, so
this is not a token lift: `docs/effects.md` §6.1 counts 1,203 `PHY`+`MOTI`
effect containers.

### 11.5 The synthesised motion track

A mesh the user just added has no animation to inherit, so `synth_moti()`
writes a **`KKEY` track with a single key at frame 0 holding an identity matrix
per bone**. `Motion_GetMatrix` clamps to key 0 when there is only one key
(RVA `0x551A0`), so the new mesh is static at every frame. Its `frameCount` is
set to the maximum of the container's existing tracks, so the shared clock does
not change length. `boneCount` is `max(palette) + 1`, which satisfies the
covering property from §11.1.

Cross-checked with the **independent** parser: `tools/effects.py!parse_moti`
reads every synthesised track to exactly its declared length and reports
identity at every frame.

### 11.6 Gates

`rebuild_c3(..., allow_structural=True)` is required for any count change; the
default refuses. On top of that:

* **Blender** — the export option *"Allow adding/removing meshes"*, off by
  default, with the warning inline in the panel. A duplicated object (Blender
  copies custom properties, so it would claim its source's chunk slot) is
  detected and treated as a new mesh with its own new track.
* **`comod.py stage-mesh`** — refuses a count change unless
  `MotionBinding.classify()` says `FREE`, or `--force` is given. It also
  rejects outright any staged file where `nPHY != nMOTI`.

### 11.7 Tests

`tests/test_structural.py` exercises every PHY-bearing container in the loose
tree and asserts, for each:

* a no-op rebuild is still **byte-identical**
* removing mesh *k* drops exactly one `PHY` **and** one `MOTI`
* every surviving mesh keeps **the same motion track, byte for byte**, and its
  own `PHY` bytes are unchanged
* adding a mesh leaves all pre-existing pairs untouched and synthesises a track
  whose `boneCount` covers the new palette
* the rebuilt container re-parses cleanly — all `PHY` byte-stable, all `MOTI`
  consumed to their declared length (checked with `tools/effects.py`)

The §10 gate is unaffected: **15,103 / 15,103 PHY chunks and 5,122 / 5,122
containers still round-trip byte-exactly.** The structural path is additive and
never runs unless the mesh count actually changed.

`tests/test_blender_roundtrip.py` adds an end-to-end case: import, delete a
mesh object, export, re-import — `PHY` and `MOTI` both drop by one and the
result re-imports cleanly.
