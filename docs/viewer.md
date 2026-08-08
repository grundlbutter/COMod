# CO Asset Viewer

A local web app for looking at Classic Conquer 2.0's textures and meshes,
seeing exactly which file on disk backs each one, and swapping a texture with a
live 3D preview before committing anything — **and, on its own page, building a
character out of them.**

```bash
py -3 tools/coviewer.py
```

That is the whole install procedure. It scans the install, starts a server on
`http://127.0.0.1:8731`, and opens your browser. Nothing to build, no packages
to add, no internet connection used or wanted.

Three pages, linked to each other:

| | |
|---|---|
| `/` | **the asset browser** — find things, see where they come from, swap a texture |
| `/builder` | **the builder** — two modes: **Character** (§5) and **Models** (§5.3) |
| `/mapedit` | **the MapEditor** (§10) — draw a world map as the game draws it, inspect any piece of it, and change its art |

The loadout is shared between the first two, so moving between them never costs
you your character.

The builder's two modes are the same stage with a different left rail.
**Character** assembles a player out of appearances: a body, then hair,
headgear and weapons that fit it. **Models** is everything that is not an
assembly — 64 monsters, 63 NPCs, 2 ghosts, the one shipped mount, the 8
character-select roles and 2,255 effects — because a monster is *one file per
action*, not one mesh with a track set, and has nothing to equip.

---

## 1. Requirements and options

| | |
|---|---|
| Python | 3.10+ (developed on 3.14.6) |
| Required package | **Pillow** — used to encode PNGs for the browser and to write DDS when you stage a swap |
| Optional | numpy — speeds a few array paths up; everything works without it |
| Browser | anything with WebGL 1: Edge, Chrome, Firefox |

```bash
py -3 tools/coviewer.py --port 9000       # different port
py -3 tools/coviewer.py --no-browser      # don't auto-open
py -3 tools/coviewer.py --root "D:\some\other\install"
py -3 tools/coviewer.py --health          # or --doctor: check and exit
py -3 tools/coviewer.py --health --json
```

It refuses to bind to anything except loopback. Press Ctrl-C to stop it.

**Start-up cost:** ~1 second. It walks the 53,675 loose files (0.2 s), maps the
two `.wdf` archives, loads the 24,426 recovered archive filenames, and parses
the appearance tables on a background thread.

### 1.1 Finding the game install

The viewer never assumes an install path. `core/coroot.py` resolves it, in
this order, and records **how** the answer was reached so the health panel can
tell you:

1. `--root DIR`
2. the `CO_ROOT` environment variable
3. `.co-root` in the repository (gitignored), then
   `%APPDATA%\co-client-re\config.json`
4. auto-discovery — conventional paths on every drive present, then the
   Windows uninstall registry (the client ships `unins000.exe`, so it
   registers), then Steam library folders
5. failure, listing everything it tried

A candidate is accepted only when it actually contains `c3.wdf`, `data.wdf`,
`ini/` and `bin/64/`. Validating the *contents* rather than the path string is
what makes a moved or half-deleted install fail loudly instead of producing
empty lists three screens later.

**If nothing is found the viewer still starts.** It comes up in *setup mode*:
no catalogue, one page that lists every location it checked and why each was
rejected, and a box to type a path into. The path is validated server-side by
the same content check and then remembered (per-user by default, or per-checkout
with the tick box). Restart and it has a catalogue.

### 1.2 First-run health check

On first launch — and any time from the **Health & thumbnails** button — the
viewer reports:

* where the install was found and **how** (registry / default path / `CO_ROOT` /
  saved config), and that each required file exists, is readable and is the
  size it should be;
* the Python version, and whether Pillow (required) and numpy (optional for
  browsing, required for thumbnails) import;
* **the derived data in `out/`** — a fresh clone has none, and until
  `tools/wdf_recover.py` has recovered the archived filenames the catalogue can
  only see the ~53,000 loose files, because the `.wdf` index stores a hash of
  each name rather than the name. One command builds the lot:
  `py -3 tools/health.py --bootstrap` (~8 minutes, once);
* the state of `out/thumbs/`;
* anything missing, **with the exact command that fixes it**.

The same report goes to the console at startup, is written to `out/health.json`,
and is available without a browser via `--health` / `--doctor`. It is generated
by `tools/health.py`; `/api/health` returns it as JSON.

### 1.3 Thumbnails are opt-in, never automatic

A fresh clone has no `out/thumbs/`. Filling it is ~637 MB and, depending
entirely on the machine, anywhere from about four minutes on a fast many-core
desktop to **15–30 minutes or more** on a laptop. The viewer therefore **asks**,
and never starts a run on its own.

The prompt states the real cost and offers:

| | |
|---|---|
| **Meshes only** | 4,950 images, 124 MB, roughly a third of the time. What the character builder and Models mode use. The better default. |
| **Everything** | adds 66,834 texture thumbnails, 486 MB — most of the time and nearly all of the disk. |
| **Not now** / **stop asking** | remembered, so it does not nag. |

Declining leaves the viewer fully working: assets without a thumbnail show a
placeholder tile, and search, the viewport, the builder, staging and installing
are all unaffected.

While a run is going the panel shows a live count, rate, ETA and megabytes
written, straight from `tools/thumbs.py`'s own progress line. You can close the
panel — the job is a separate process and keeps going — and stop it at any
time. Generation is content-hash resumable, so stopping costs you nothing:
restarting continues rather than re-rendering. The estimates shown are computed
for *your* core count, and the panel quotes the reference measurement it
extrapolates from rather than presenting a number as if it were universal.

`--jobs` and a "stop after N images" limit are both exposed, for trading wall
time against responsiveness or taking a taste of the run first. The same thing
on the command line:

```bash
py -3 tools/thumbs.py --all --resume                 # meshes
py -3 tools/thumbs.py --all --textures --resume      # everything
py -3 tools/thumbs.py --all --textures --dry-run     # what it would cost
```

The viewer drives that exact script as a child process. It does not
re-implement the renderer.

### What it will never do

* **It never writes to the game install.** The server writes only into
  `mods/stage/` and `out/viewer/`. The one action that touches the install —
  *Install for real* — shells out to `comod.py install --yes`, which makes the
  backups, writes `mods/manifest.json`, and gives you `uninstall`.
* **It makes no network connections.** Every asset is vendored; the page has a
  `connect-src 'self'` CSP so it cannot reach anywhere even if you asked it to.

---

## 2. The three panes and four tabs

```
┌────────────────────┬──────────────────────────┬──────────────────────┐
│  CATALOGUE         │      3D VIEWPORT         │   DETAIL             │
│ Categories         │                          │ Equip (slots)        │
│ Appearances        │     (WebGL, orbit)       │ What goes with this  │
│ Maps               │                          │ Effects (+ playback) │
│                    │                          │ Pieces on this map   │
│ Files              ├──────────────────────────┤ Where this comes from│
│                    │ view mode / culling /    │ Tags (yours+derived) │
│  + facet filters   │ alpha / shading / lock / │ Texture (+ swap)     │
│                    │ effect playback …        │ Mesh                 │
└────────────────────┴──────────────────────────┴──────────────────────┘
```

### Left — catalogue

Four tabs. **Categories** (§4) is the way in for browsing by kind, **Maps** (§4)
for browsing a world map and its art, **Files** for the raw path list, and
**Appearances** for the game-facing tables below.

**Appearances** is the game-facing view. Pick a part table (`body`,
`l_weapon`, `armet`, `mount`, …) and you get every appearance ID in it, with a
thumbnail of its texture. This is the right way in when you want to change how
a *thing in the game* looks, because it resolves the appearance ID through
`armor.ini` / `weapon.ini` / … to the actual mesh and texture files.

#### Body filters — class, gender, body size

On the `body` and `mix_body` tables (both backed by `armor.ini`, 3,418
appearances) three filter rows appear:

```
CLASS      [Warrior 352] [Trojan 408] [Taoist 352] [Archer 352] [Any class 1740] [Unclassified 214]
GENDER     [Female 1648] [Male 1585] [Other body 185]
BODY SIZE  [Small 1617] [Large 1616] [n/a 185]
YOUR TAGS  [your tags…] [untagged]
```

Click chips to toggle them. **Within a row the selection is OR; across rows it
is AND** — so Warrior + Female + Small gives the 88 Warrior armours that exist
for the small female body. The counts are *cross-filtered*: each number is how
many results you would get if you ticked that chip, given everything else you
have already ticked, so they fall as you narrow and you can never click your way
into an empty list by accident.

Where the three axes come from is worked out in full in
**`docs/appearance_ids.md`**. In one line: the first 3 digits of the appearance
ID are the body type (gender + size, verified against the game's own
gender-locked garments and by measuring the four base meshes), and the next 3
are the armour series, which maps to `itemtype.json`'s `requiredProfession` for
99 % of appearances.

Nothing is ever hidden for failing to classify. Appearances with no class
evidence land in **Unclassified** and NPC/monster base bodies in **Other body**;
both are selectable chips, not silent drops.

**group by mesh** collapses the list to one row per distinct body mesh with its
colour variants as a thumbnail strip underneath. Since the mesh is shared across
colourways and only the texture differs, this turns ~800 appearances per body
type into ~170 actual garments — much the better browsing unit when you are
looking for something to retexture.

The free-text box searches appearance ID, item name, mesh path **and your tags**
at once.

**Files** is the raw view: every logical path the client can resolve — 77,514 of
them — filtered by directory and extension. Each row is labelled `loose` or
`c3.wdf` / `data.wdf` so you can see at a glance which layer it lives in.

> Typing anything containing a `/` in the filter searches **all** directories,
> ignoring the directory dropdown. There is also an explicit
> *(all directories)* entry.

#### One row per asset, not per file

A garment is one thing but two files, and a mesh shared by twelve colourways is
thirteen rows of which twelve are duplicates of something you can already see.
So **a mesh and the textures that belong to it are one entry**, and the row says
how many it absorbed (`+ 12 skins in this entry`).

The rule, in `tools/unify.py`:

> A mesh is always an entry. A texture is an entry **only when nothing owns it**.
> A texture is owned when the pairing is either **authored** — a shipped data
> file states it — or the mesh's **rank-1** candidate, which is how the
> same-stem convention (`c3/npc/185.c3` + `c3/npc/185.dds`) folds in.

Anything weaker collapses nothing. `dir_sibling` alone has 8,004 loose any-rank
hits and folding a map tile under some unrelated mesh would be worse than the
duplication it removes. Measured over this install:

```
.c3 with geometry      4,964     (1,426 more are pure MOTI/PTCL — no texture to have)
  ... with a texture   4,950     99.7%, of which 85.5% authored
.dds                  66,832
  ... owned by a mesh   5,799    these stop being their own row
  ... standalone       61,033    map tiles, icons, faces, UI
```

Three things the collapse deliberately does **not** do:

* **It never hides anything.** A texture folds away only when its owner is
  *in the same view*, so narrowing to one directory cannot make it vanish, and
  choosing "textures (.dds) only" in the Type dropdown turns the merge off.
* **It does not assume 1:1.** A mesh can have twelve textures and **a texture can
  belong to several meshes** — 1,527 of the 5,808 do, e.g.
  `c3/texture/105000000.dds` is worn by thirteen monster motion meshes. It is
  filed under its highest-confidence owner and still listed under every other.
* **It does not break search.** The merged row is searchable by *either* name:
  typing `002135300` (a texture id) finds the `002135000.c3` entry it lives in.

The relation is `tools/meshtex.py`'s and is imported, never re-derived. It is
read from `out/meshtex/coverage.json` when that exists (instant) and built live
otherwise.

#### …and the entry taken apart again

**What goes with this** leads with **This entry: mesh and textures** — the mesh
and every texture as separate, independently clickable lines, each carrying the
rule that linked them:

```
002135000.c3    MESH      the geometry
002135300.dds   TEXTURE   [authored] appearance_table 0.95
                          armor.ini [002135300] Mesh0=002135000 Texture0=002135300
002135310.dds   TEXTURE   [authored] appearance_table 0.95
...
```

The green **authored** / amber **inferred** badge is the load-bearing part.
"`armor.ini` says so" and "there is a `.dds` with the same stem" are not the
same claim and are not shown as though they were.

#### Thumbnails

Every list picture comes from `/api/thumb`, which tries three things in order:
task #21's pre-rendered PNG from `out/thumbs/`, the decoded texture (which is
what the lists used before), then nothing — the checkerboard placeholder. Batch
rendering is incremental and resumable, so "no thumbnail yet" is a normal state
and the list must not look broken in it.

**4,950 of 4,950 meshes and 66,834 of 66,834 textures are rendered, 0 failed.**
The viewer reads the 3.5 MB mesh-only slice (`manifest_meshes.json`) eagerly —
every list row wants a mesh picture — and the 23 MB full manifest only when a
texture thumbnail is actually asked for. This is what turns a list of texture
atlases into a list of *garments*: a slot card, a picker row and a related-asset
cell now show a render of the item rather than its skin sheet.

Two flags are honoured rather than ignored:

* **`low_coverage` (19 meshes)** — real geometry that occupies well under a
  percent of the frame: sub-pixel ribbons and slivers. Their render is a
  near-empty square, which reads as *broken* rather than as *thin*, so it is
  withheld and the texture is shown instead. Task #21 recommends exactly this.
* **`fallback` (42 meshes)** — `no_cull` (25 single-sided planes), `uv_cell` (10
  effect flipbooks blank at frame 0), `no_motion` (4), `alpha_from_luma` (3).
  Honest renders taken off the default path, badged on hover rather than passed
  off as ordinary.

The manifest reader is deliberately tolerant of shapes that schema could have
taken (a mapping, or `entries`/`thumbs`/`items` holding a mapping or a list; the
file key can be `thumb`/`thumbnail`/`file`/…) and re-reads on demand, because
that file is another workstream's to design and this one only consumes it.

> **Why the collapse is not built on `used_by_mesh`.** The manifest flags 2,782
> textures as skinning a model and 64,052 as not, which looks like exactly the
> partition the merge needs. It is not: the flag marks each mesh's *chosen*
> texture, so `002135300` is true and its eleven colourways `002135310`,
> `002135400` … are false. Folding on it would merge one skin into the garment
> and leave the other eleven as their own rows — the clutter this change exists
> to remove. The collapse uses every **authored** pairing; `used_by_mesh` is
> kept as the quick "does this texture skin anything at all" answer and shown in
> the panel as an *alt skin* badge, which is a genuinely different fact.

Selecting a `.c3` on its own still gets you a textured model: the server infers
a texture by inverting the appearance tables (`c3tex.py`'s rule) and falls back
to a same-named `.dds` beside the mesh, which is how the `c3/npc/`, `c3/effect/`
and `c3/mount/` trees are laid out. The panel says when a texture was inferred
rather than looked up, because many appearances share one mesh with different
skins.

### Centre — viewport

Left-drag orbits, wheel zooms, right-drag (or shift-drag) pans.

#### Which way you are looking — FIXED

**The corpus is authored facing −Y, and the default yaw was +0.9, which is
behind the model.** Every freshly-opened character showed its back, and had done
since the beginning. It went unnoticed because every saved screenshot was taken
from a camera that had already been orbited by hand, so the proof images hid the
bug rather than exposing it.

The default is now **−0.9** — the same elevation on the front side, and the exact
yaw `tools/thumbs.py` renders all 4,950 mesh thumbnails from, so a thumbnail and
the viewport agree about which side is the front.
`out/thumbs/_selftest/yaw_grid.png` is the four cardinal views of one body, and
only −Y has a face.

A returning user whose `localStorage` already holds the bad default is migrated
to the new one; a camera they actually orbited to is left alone. The test for
this is precise about the difference: only a yaw sitting on `0.9` to seven
decimal places — i.e. the old default written through, never touched — is
replaced.

#### The camera does not reset between assets

Loading a new asset **keeps the angle you were looking from**. That is what
makes two assets comparable — turn a model to see its back, arrow down the
list, and every one arrives already turned around.

What does not carry over **in asset view** is the *distance*, and deliberately
so: a character body is ~170 units tall and a weapon socket mesh is ~5, so a
fully persisted camera would leave you staring at empty space every time you
crossed a scale boundary. Instead the distance re-fits to each model's bounds,
so whatever you pick fills the frame.

**In character view the camera never re-fits for an attachment** — the body is
the subject and the framing stays on it, so arrowing through helmets compares
them instead of slamming the camera in and out. See §5.

**Lock camera** (checkbox, or <kbd>L</kbd>) freezes the view completely —
angle, zoom, pan and the orbit centre. Use it when you are flipping through
colour variants of one mesh and want the framing pixel-identical, where even a
re-fit drifts enough to read as the model changing. While locked, loading a
differently-scaled asset genuinely can put it off-screen; that is the point.

<kbd>R</kbd> / **Reset view** always re-frames whatever is currently loaded,
lock or no lock. It is the way back from a frozen camera.

The angle and the lock flag survive a page reload (`localStorage`), so a
refresh does not cost you your viewpoint.

| Control | What it does |
|---|---|
| **Culling** | `game` is the engine's own setting (see §7). `none` draws both sides. `inverted` deliberately flips it — a useful "am I looking at the inside?" check. |
| **Alpha** | `blend`, `alpha test` (discard below the cutoff), or `ignore alpha`. |
| **Shading** | `unlit` shows the texture exactly as authored. `lit`, `normals` and `uv0 checker` are inspection aids, not the game's look. |
| **wireframe** | overlays the triangle edges |
| **show sockets** | reveals the tiny attachment-point meshes (`v_armet`, `v_l_weapon`, …), hidden by default because they are not visible geometry |
| **vertex colour** | modulate by the packed per-vertex colour (see §8 — it carries no information in this build) |
| **C3Key frame** | scrubs the per-mesh alpha keyframe track, when the mesh has one |
| **effect** | play / pause (<kbd>Space</kbd>), a millisecond scrubber, and the **swing preview** — appears only when an effect is loaded (§4.5) |
| **lock camera** | freeze the whole view across asset loads — see above (<kbd>L</kbd>) |
| **Save PNG** | writes the current frame to `out/viewer/shots/` |
| **?** | the keyboard reference below, as an overlay |

#### Keyboard

Arrow keys walk the asset list with the preview following, so you can flip
through a filtered set without touching the mouse.

| key | |
|---|---|
| <kbd>↑</kbd> <kbd>↓</kbd> | previous / next entry |
| <kbd>←</kbd> <kbd>→</kbd> | previous / next **colour variant of the current mesh** |
| <kbd>PgUp</kbd> <kbd>PgDn</kbd> | jump 10 entries |
| <kbd>Home</kbd> <kbd>End</kbd> | first / last entry |
| <kbd>/</kbd> | jump to the search box |
| <kbd>Esc</kbd> | leave the search box, or close the help overlay |
| <kbd>L</kbd> | lock / unlock the camera |
| <kbd>R</kbd> | reset the view to fit the current model |
| <kbd>V</kbd> | switch character view / asset view |
| <kbd>F</kbd> | zoom to the head socket |
| <kbd>W</kbd> <kbd>G</kbd> <kbd>S</kbd> | wireframe · grid · sockets |
| <kbd>Space</kbd> | play / pause the loaded effect |
| <kbd>C</kbd> | collapse / expand every panel on the right |
| <kbd>?</kbd> | keyboard overlay |

Four details that make it usable rather than merely present:

* **Arrows never fire while you are typing.** Any focused input, textarea or
  select swallows every key; only <kbd>Esc</kbd> is honoured, and it blurs the
  field so the arrows start working again. The search box behaves exactly as a
  search box should.
* **Navigation follows what is on screen.** The key handler walks the rendered
  rows, so it respects the class/gender/size/tag filters and the grouped-by-mesh
  mode. In grouped mode <kbd>↑</kbd><kbd>↓</kbd> moves between meshes and
  <kbd>←</kbd><kbd>→</kbd> between that mesh's colourways, expanding the group
  and highlighting the swatch as it goes. In flat mode <kbd>←</kbd><kbd>→</kbd>
  hops to the next row sharing the same mesh — in the real ID ordering those
  are 10 rows apart, so it is a real shortcut, not a duplicate of <kbd>↓</kbd>.
* **The selected row scrolls into view**, so the list and the viewport never
  disagree.
* **Holding a key does not queue up loads.** The highlight moves instantly but
  the *load* is debounced by 130 ms, so a 15-key burst issues exactly one
  request — for the row you stopped on. Every load also carries a token that
  async work re-checks before touching the viewport, so a slow response for an
  asset you have already left can never overwrite the current one. Measured in
  the browser: 15 rapid presses → 0 requests during the burst, 1 after,
  landing on the correct asset.

### Right — detail

**Every card here collapses.** Click its heading, or **Collapse panels** in the
header, or press <kbd>C</kbd>; the state is remembered across reloads. This is
the same `cards.js` the builder uses — see §5, which is also where the story of
why these headers used to change the cursor and do nothing lives.

**Where this comes from** is requirement (c). The headline is a badge:

* **`LOOSE FILE WINS`** — a file on disk is what the game reads. If it is also
  in an archive, the card says which archive it is overriding.
* **`FROM C3.WDF` / `FROM DATA.WDF`** — no loose file shadows it, so the archive
  copy wins, and dropping a file at that path would override it.
* **`STAGED MOD PENDING`** — you have staged a replacement that is not installed.

Below that: the logical path, the real path on disk, the byte size, the TQ name
hash, the archive slice (offset + length) when it comes from an archive, the DDS
format, every appearance ID that references the file, and a set of
copy-pasteable `comod.py` commands — click one to copy it.

**Effects** appears for weapons and shields: the three effect roles, each
resolved to real meshes and textures with its timing, its layers' blend state
and a *play* button, plus the per-action mesh dropdown. See §4.5 and §4.6.

**Tags** is your own curation layer — see §6.

**Texture** shows the decoded image over a checkerboard (so you can see the
alpha), and holds the swap controls.

**Mesh** lists every `PHY` chunk with a visibility checkbox and its flags:
vertex/face counts and the A/B split, bone count, `2SID`, billboard mode,
whether the 4×4 matrix was non-identity, whether normals were generated, and
the number of C3Key frames. It also shows the chunk's **label**, which is
normally the source texture path from the original 3DSMax export
(`texture\armet001.tga`, and a great many GBK-encoded Chinese paths, decoded
here rather than left as mojibake). That tells you what the mesh was authored
against.

---

## 3. Swapping a texture

1. Select an appearance (or a texture file).
2. In the **Texture** card, choose a replacement — PNG, BMP, JPEG, or a
   ready-made DDS.
3. The model **re-renders immediately** with the new skin. Nothing has been
   written anywhere except `out/viewer/preview/`. Warnings appear inline if the
   size differs from the original, the format differs, or the image is not
   power-of-two.
4. **Discard preview** puts it back. **Stage this swap** writes it into
   `mods/stage/<logical path>`.
5. The **Mod staging** drawer lists everything staged with its status
   (`NEW` / `MODIFIED` / `same`), and gives you *Install (dry run)*,
   *Install for real* and *Uninstall / revert*. All three run `comod.py`.

The encode defaults to the original's DXT format, so a 128×128 DXT3 texture
comes back as a 128×128 DXT3 texture of exactly the same byte length. You can
override the format in the dropdown.

Nothing about this bypasses the CLI: `mods/stage/` is the same tree
`comod.py diff` reads, and the install is the same command with the same
backups and the same `uninstall`.

---

## 4. Browsing by category, and by map

The left pane has four tabs. **Categories** is the way in.

### Categories

The taxonomy is *derived*, not declared — `tools/catalog.py` classifies every
one of the 77,508 resolvable paths from three kinds of evidence, strongest
first:

1. **Appearance-table membership.** If `weapon.ini` names a mesh, that mesh is a
   weapon — the client's own ini says so, and it overrides everything below.
2. **Directory layout**, as a table of rules that each record *why* they fired.
   Hover any row to see the reason it landed where it did.
3. **Extension**, which gives the file's `role`: mesh, texture, map, mapdata…

| | count | |
|---|---:|---|
| Characters | 6,362 | bodies, armour, helmets, hair, per-body-type motion sets |
| Weapons | 1,741 | weapon and shield meshes and skins |
| Monsters | 1,107 | |
| NPCs | 314 | |
| Mounts | 4 | |
| Effects | 5,594 | effect meshes, weather, fireworks |
| Maps | 55,728 | world grids and every piece of art that draws them |
| UI & icons | 5,545 | |
| Sound | 457 | |
| Not art | 656 | config, logs, binaries, the archives themselves |
| Unclassified | 0 | art that matched no rule — **the bucket still exists** |

`c3/0001`–`c3/0004` are not four mystery folders: they are the per-body-type
motion sets, so they fold into Characters (see `docs/appearance_ids.md`).

Inside a category you get **kind of file** chips (mesh / texture / …) and a
**group** axis — for map art that group is the region folder, so you can go
straight to `newplain` (13,730 tiles) or `island` (474).

### Maps

The **Maps** tab lists all 136 world maps largest first, with a bar showing
relative size, because the user's distinction between "really large maps" and
"the sprites that go on them" is a real one: they run from 40×40 to 1440×1440.

Picking a map resolves the whole chain and shows every piece under it:

```
map/map/island.DMap                     the world grid  (1024 × 1024)
   puzzle_path  -> map/puzzle/island.pul
                     ani_path -> ani/island.json
                                   -> data/map/puzzle/island/lake/lake000.dds  ... 465 ground tiles
   layer table  -> 597 layers, all decoded
                     cover  -> 108 animated sprites (data/map/mapobj/island/...)
                     effect -> 8 keys into 3DEffect.ini
                     scene  -> map/Scene/*.scene, each made of sprite parts
```

Every tile is a thumbnail you can click straight through to. Verified end to
end on `island`, `desert` and `newplain`; the layer walk consumes every declared
layer, which is what proves the body sizes are right.

### What goes with this

Any selection gets a **related assets** card: its mesh and skin, the extra
`MixTex`/`ThirdTex` skins, **other colourways on the same mesh** (deduped by
texture, so the one you are looking at is not listed as a variant of itself),
its item icons, and for weapons every mesh it swaps to per action.
The **Effects** group shows a weapon's three *separate* effects, correctly
distinguished:

* **aura** — always on, the `999` wildcard action
* **attack trail** — one per attack action (401/402/403). **Only quality 6–9
  weapons have one**, so "no attack trail" on most weapons is the data, not a
  bug, and the panel says so rather than looking empty.
* **impact spark** — labelled *drawn at the TARGET*, because it is not rendered
  on the character. It is keyed by the 3-digit weapon *type*, so a quality-5
  blade with no trail still has one — the panel shows that combination rather
  than looking empty.

---

## 4.5 Effects — resolved and played

An effect *name* is a `3DEffect.ini` section key, **not a folder name**.
`Flash4102` lives in `c3/effect/flash/` and `m-b02` in
`c3/effect/Monster-bomb/m-b02/`; guessing the folder from the name fails on
both. The real chain is `3DEffect.ini` → `EffectId`/`TextureId` →
`3DEffectObj.ini` / `3dtexture.ini`, and the viewer walks it through
`effects.EffectDB` (task #13's module, imported unchanged).

```bash
py -3 tools/effectplay.py --effect m-b02        # one effect, fully resolved
py -3 tools/effectplay.py --weapon 510000       # its per-action meshes
py -3 tools/effectplay.py --coverage
```

```
effect names        2255
  resolve           2255      every name is a real 3DEffect.ini section
  playable geometry 2152      at least one PHY+MOTI or SHAP+SMOT part
  needing particles  464      some layer is PTCL/PTC3 — see below
```

(`docs/effects.md` §9 reports the stricter metric — **2,207 of 2,255** effects
where *every* layer's file exists on disk. The 41 with no layer resolving and
the 4 undefined names are data bugs in the ini, not lookup failures.)

### Playing one

Select a weapon appearance and the **Effects** card lists each role with its
timing, its layers and a **play** button. Playback controls appear under the
viewport: play/pause (<kbd>Space</kbd>), a millisecond scrubber, and a **swing
preview** toggle.

The renderer is `tools/webui/fx.js`, implementing `docs/effects.md` §8. The
reference implementation lives in `tools/effectplay.py` and **`fx.js` is a
line-for-line mirror of it**, so the algorithm is covered by
`tools/test_viewer.py` against real assets rather than by looking at the
viewport and deciding it seems right.

| What | Status |
|---|---|
| **Timing from the alpha envelope** | Effect meshes habitually declare a 101-frame `MOTI` and fade out after ten. The viewer plays the *effective* length. `m-b02` = **11 frames × 33 ms = 363 ms**; the declared length would have run that impact spark for **3.3 s**. INFERRED (`docs/effects.md` §6.5) but unambiguous in the data. |
| **Flipbook atlas** | `uv = ((n % N)/N, (n // N)/N)` where **N is the PHY chunk's own `frameCount`**, not the animation length. Applied as a UV *offset* with no scale, which is correct because the quad's own UVs already span one cell — checked: `m-b02`'s `Plane02` has N=2 and UVs authored 0…0.5. Its keys `(0→0) (2→1) (4→2) (6→3)` walk all four cells over frames 0–6, exactly the worked example in `docs/effects.md` §6.4. VERIFIED. |
| **The three `C3Key` channels** | alpha is **lerped** between bracketing keys, draw is an **exact frame match only**, changeTex is a **step function**. Implemented separately and tested separately, because treating them alike is silently wrong. VERIFIED. |
| **UV scroll** | From the `STEP` chunk, used only when there is no changeTex channel, wrapped into `[-1.1, 1.1]`. The multiplier is the animation frame — INFERRED (`C3Phy+0x194` is never written in `graphic.dll`). |
| **`SHAP` ribbons** | The two-point blade line, transformed by `SMOT[frame] × world`, smeared into 5 interpolated pairs per tick, capped at `min(segments × 5, 800) + 1` pairs and emitted as a triangle strip. `Flash4102`: 7 segments → **36 pairs → 72 strip vertices**. VERIFIED against `Shape_Draw` / `Shape_SetSegment`. |
| **Blend state** | Straight from the layer's `ASB`/`ADB`, which are `D3DBLEND`, mapped to the GL factor. `5,2` (SRCALPHA/ONE) is the additive glow, `5,6` ordinary alpha. Per layer, not a global setting. |
| **Depth** | Effects are depth-*tested* but do not write depth, so they never occlude each other. |
| **Anchoring** | Aura and trail ride the weapon socket's world matrix **as it is on the frame being drawn** — `setEffectTime`'s parent matrix reaches the static quads, not only the ribbon history (§5.2; it used to reach only the ribbons, and the glow hung where the hand had been). The impact spark is anchored to a **separate dummy target** in front of the figure, because that is where the engine draws it. INFERRED (`docs/effects.md` §8). |

### The swing preview, and why it exists

A `SHAP` trail is driven by the **parent's** motion, not its own — the `SMOT` on
the shipped flash meshes is a constant matrix on all 101 frames. So a static
weapon produces a static line, and that is *correct*. The **swing preview**
checkbox sweeps the parent transform through a 600 ms arc so the ribbon has
something to smear along.

**That arc is the viewer's, not the game's.** `3dmotion.ini` is not wired in, so
this is not the attack animation; it exists to exercise the ribbon. The tooltip
says so. (600 ms rather than the effect's own 4.1 s length because the ribbon
only holds ~7 ticks of history; sweeping over 4 s would smear across 0.06 rad
and look like nothing happened.)

### What effect playback does *not* do

* **`PTCL` / `PTC3` particle systems are not decoded** (`docs/effects.md` §6.6).
  Layers that are pure particles draw **nothing** and the panel says so, rather
  than substituting something plausible. 464 of 2,255 effects need particles for
  at least one layer; for effects reachable from a weapon it is 29 of 1,134.
* **A blend mode whose source factor is `SRCCOLOR` ignores the alpha envelope.**
  The `410009` aura is authored `ASB=3 (SRCCOLOR), ADB=7 (DESTALPHA)`, so the
  per-frame alpha never reaches the blend equation. That is faithful to the
  fields; it is not the viewer discarding the alpha track. Additionally the
  canvas is created `alpha:false`, so **`DESTALPHA` reads as 1.0** — on a render
  target with a real alpha channel this layer would look different. Stated
  rather than worked around.
* **No spawn/despawn logic.** What triggers an effect, and the exact hit moment
  inside an attack motion, live in the packed `ImConquer.exe`. The viewer
  spawns on a button press.
* Scrubbing the slider **replays from zero** rather than sampling, because a
  ribbon is path-dependent: it is built by advancing, not evaluated at a frame.

## 4.6 The weapon swaps mesh per action

`ini/WeaponMotion.ini` is 1,863 flat rows keyed **`<appearance><action>`** — a
9-digit string. Looking it up with the bare 6-digit appearance matches nothing,
which is how the *Motion mesh* panel came to be permanently empty; there is now
a test that pins the key format.

The lookup order is `docs/effects.md` §8 step 3 verbatim:

```
WeaponMotion[A + action]  ->  WeaponMotion[A + "999"]  ->  weapon.ini[A].Mesh0
```

The Effects card carries a **per-action mesh** dropdown; picking an action loads
that action's mesh into the viewport. `510000` (a bow) shows
`c3/mesh/510000.c3` by default and `c3/mesh/510000401.c3` for all eleven
non-default actions. `410005` has **no `WeaponMotion` rows at all**, so
`weapon.ini`'s `Mesh0` stands for every action — reported as such rather than as
a lookup failure.

---

## 5. The character builder — `/builder`

Its own page, because the browser and the builder need different first screens:
one is for *finding* things, the other for *making* a character. Header links go
both ways and the loadout is shared through `localStorage`, so moving between
them never costs you your character.

```
┌──────────────────┬────────────────────────────┬──────────────────────┐
│ YOUR CHARACTER   │          STAGE             │  ▾ Colour & variants │
│  Body & armour   │                            │  ▾ Animation         │
│  Head            │      (WebGL, orbit)        │  ▾ Weapon effects    │
│  Left hand       │                            │  ▾ How this is built │
│  Right hand      ├────────────────────────────┤                      │
│  More slots ▾    │ action · play · frame ·    │  (each collapses,    │
│                  │ speed · lock · grid · …    │   and remembers)     │
└──────────────────┴────────────────────────────┴──────────────────────┘
```

### The design correction: appearances, not mesh × texture

The old equip panel let you pick a *mesh* and then hunt for a *texture*. That is
a cross-product, and most of it does not exist — **it permitted states the game
never ships.**

**The appearance tables already are the valid combinations.** `armor.ini`'s row
`002135300` says `Mesh0=002135000, Texture0=002135300`: one row, one pair,
shipped. So the builder offers appearances and only ever those whose two halves
both resolve to a file on disk. There is no code path that pairs an arbitrary
mesh with an arbitrary texture, and `tools/test_viewer.py:CharacterBuilder`
asserts it over the whole catalogue — every option's mesh and texture must be
the exact ids of an ini row, and both must exist.

That collapses into **two obvious choices instead of one list of 800**:

```
ITEM      one distinct mesh          167 garments for a large female body
  COLOUR    its texture variants       8 typically, sharing that mesh
```

Grouping on the mesh is the data's own shape, not a presentation trick
(`docs/appearance_ids.md` §1: digit 7 selects the colour, digit 8 the cut, and
the mesh id is the appearance id with the colour digit zeroed).

**Looks that are literally identical are shown once.** 1,082 of the 3,082 usable
`armor.ini` rows are a *different appearance id resolving to the same mesh AND
the same texture* — `002182320`, `002182420` … `002186320` are eight ids for one
garment. One stands for the look; the rest ride along as `aliases`, recorded
rather than repeated eight times.

### Click a slot → a searchable window

Clicking any slot opens a picker over the left of the screen, with the character
still visible behind it:

* a **search box** matching name, id, colour and your tags
* the **chips that apply to that slot**, cross-filtered — each count is "how many
  would I get if I ticked this", so no chip can lead to an empty list. An axis
  with only one value is not shown at all: filtering a head picker by
  "Gender: female" when the body already fixed it is noise.
* **items with their colours underneath**, thumbnails throughout
* live counts: `11 items · 88 colour variants (of 799 that fit)`
* **Leave this slot empty**

Keyboard: <kbd>↑</kbd><kbd>↓</kbd> item, <kbd>←</kbd><kbd>→</kbd> colour,
<kbd>PgUp</kbd>/<kbd>PgDn</kbd>/<kbd>Home</kbd>/<kbd>End</kbd>,
<kbd>Enter</kbd> keep it, <kbd>Esc</kbd> close. <kbd>B</kbd> <kbd>H</kbd>
<kbd>R</kbd> or <kbd>1</kbd>–<kbd>4</kbd> open a picker directly.
<kbd>Q</kbd> steps the equipped weapon up a quality (shift-<kbd>Q</kbd> down)
and <kbd>A</kbd> toggles its Super aura — see §5.2.

**Arrowing previews on the character**, using the same 130 ms debounce and
monotonic load token as the browser (§2): the highlight moves on every press,
one request fires for the row you stopped on, and a slow response for a row you
have left can never land. There is no way to end up wearing something you did
not see.

### Compatibility — decided by the ids, not by a list

| slot | ini | rows | usable | garments | body-specific |
|---|---|---:|---:|---:|---|
| Body & armour | `armor.ini` | 3,418 | 3,082 | 656 | **yes** (94.6% prefixed) |
| Head | `armet.ini` | 2,918 | 1,798 | 262 | **yes** (100% prefixed) |
| Left / Right hand | `weapon.ini` | 5,384 | 5,214 | 437 | no (**0%** prefixed) |
| Head (legacy DX8) | `armet1.ini` | 950 | **8** | 5 | no |
| Bare head | `head.ini` | 4 | **0** | — | — |
| Accessory | `misc.ini` | 272 | **0** | — | — |
| Mount | `mount.ini` | 1,317 | **0** | — | — |
| Shield | `shield.ini` | — | — | — | ini not shipped |
| Pelvis | `pelvis.ini` | — | — | — | ini not shipped |

Once a body is chosen, a body-specific slot offers **only** art carrying that
body's prefix — and body-specificity is *measured off the ids*, not declared, so
a slot cannot be mislabelled and then filter everything away. The old viewer
listed `armet_dx8`, `head` and `misc` as body-specific; none of them is, and
that would have emptied all three.

Changing the body drops anything cut for the old one, and says so. Weapons stay.

**Slots with nothing usable are greyed with the reason, not opened empty.**
`shield` and `pelvis` do not ship at all; `head`, `misc` and `mount` ship an ini
whose meshes resolve to *nothing*, so equipping any of them would draw nothing —
that is now stated instead of discovered. `armet_dx8` says only 8 of its 950
entries have art.

### Hair — CORRECTED

Earlier revisions of this document said hair is `armet.ini` series **111**. The
data says otherwise, and `tools/parts.py:HAIR_SERIES` has been corrected:

```
series 111  IronHelmet, BronzeHelmet, SilverHelmet, ...      10 item names
series 112  ConquestHelmet, PhoenixHat, UltimateCap, ...
series 113  BadgerHat, CatHat, JackalHat, MartenHat, ...     10 item names
series 114  DestinyCap, LacyCap, VeinCap, CloudCap, ...      10 item names
series 119  ** no items at all **, 515 of the 707 armet meshes, mostly c3/hair/
```

`docs/attachment.md` §8.3 agrees from the other direction: series 119 sits a
median **+4.0** units above the skull — hair lying on the scalp — against 111's
+18.1, 113's +9.4 and 114's +11.0, which are hats. Series 111 also carries
`requiredProfession 21` (Warrior), which a hairstyle could not.

So **hair is series 119**; 111–116 are helmets and hats. Both families still
share the head slot, so equipping a helmet still replaces the hair — the UI
enforces it and the head picker has a hair/headgear chip with both always
selectable. The colour digit and the shared-mesh/one-texture-per-colour
structure are identical in both, which is why the old reading looked plausible.

### Plain language over ids

The picker shows the item's name from `itemtype.json`, spaced into English
(`OxhideArmor` → `Oxhide Armor`), with the id secondary. Where no item backs an
appearance the label is constructed rather than falling back to digits:
`Hairstyle 10 · black`, `Amethyst Blade · Blade · quality 9`. 95% of weapons and
two-thirds of garments have a real name; every hairstyle has a built one.

A loadout restored from a link or from `localStorage` is only ids, so the names
are fetched back before anything is drawn. **Nothing on this page requires
knowing what an appearance ID is.**

### Collapsible panels — FIXED, and it was the browser page that was broken

Each right-hand panel collapses on its own and remembers across reloads, exactly
as the camera and the lock flag already do. **Collapse panels** in the header (or
<kbd>C</kbd>) does the lot: with anything open it collapses everything, with
nothing open it expands everything. **Both pages now have this. Only one of them
used to.**

The report was *"the menu collapse option doesnt work — I can see the cursor
change, but it doesnt collaps when clicked."* The obvious suspect was a dead
listener on the builder, and that was wrong: driving the builder in a browser,
every one of its four headers toggles, the header button toggles, <kbd>C</kbd>
toggles, and the state survives a reload. Nothing there was broken.

**The dead one is the asset browser.** `style.css` styled `.card h2` — an
unscoped rule in the *shared* stylesheet — with `cursor: pointer` and
`user-select: none`. The builder's four cards carried the machinery that rule
implies (`data-card`, a `.cardtoggle` button, a `.cardbody` wrapper, listeners in
`builder.js`). The browser's **eight** cards carried none of it and `app.js`
bound nothing, so every header on that page changed the cursor under the mouse
and then did absolutely nothing. Measured on the running page before the fix: all
eight reported `cursor: pointer`, zero had `data-card`, zero had a `.cardbody`,
and `h2.click()` left the class list untouched.

A cursor is a *promise*. Three things now keep it:

* the cursor rule is scoped to **`.card[data-card] h2`**, so a card the code
  cannot operate is not allowed to look operable;
* the behaviour moved into **`tools/webui/cards.js`**, loaded by both pages —
  having the CSS shared and the JS not is exactly how the two drifted apart;
* `initCards()` **`console.warn`s** for any `.card[data-card]` with no
  `.cardbody`, because that failure collapses to nothing visible and reads
  identically to a dead listener.

The regression test reads the shipped files rather than re-implementing the
logic, because the logic was never the problem: *every* `<section class="card">`
on either page must have `data-card`, a `.cardbody`, unique names, and the page
must load `cards.js` and offer `#btn-collapse-all`. The old test asserted a
pure-Python mirror of `toggleAll()` and passed happily throughout.

The page **opens on a real character** — `armor.ini` series 132 is `Coat` /
`Dress`, the starting clothes with no class requirement, plus a hairstyle — not
an empty stage. **Reset** puts it back. **Copy link** gives a URL that restores
the character exactly:

```
http://127.0.0.1:8731/builder#body=002132300&armet=002119310&r_weapon=410089
```

### Animation — the game's own motion, played

`tools/anim.py` (task #20) resolves the clip; this page plays it. The poses are
`ini/3dmotion.ini`'s, evaluated per frame server-side and uploaded as one
position buffer per chunk, so a frame costs a `bufferSubData` rather than a
rebuild and the camera is never touched. Equipped parts ride the socket matrix
*for that frame*, so a weapon travels with the hand.

Four things the action panel states rather than papering over:

* **The weapon is part of the lookup.** `3dmotion.ini` is keyed
  `<shape><weaponset><action>`, and a `410` swing differs from the unarmed one by
  up to 115 units of pose on a 170-unit body. The action list is re-derived
  whenever a hand changes.
* **Run is two clips.** `120` and `121` are the left and right halves of the
  stride (`runL.wav` / `runR.wav`) and *chain* rather than self-loop, so playback
  walks the pair and the frame counter says `clip 1/2`. Walk `110` self-loops;
  `115` is a separate style, not its partner.
* **Walk and run translate the root by 0.00.** They are in-place cycles and the
  client moves the character across the map, so running on the spot here is
  correct rather than a limitation. A jump *is* different — `131` lifts body 002
  by 85 units — and that arc is baked into the poses, so it plays as authored.
  The panel reports which of the two each clip is.
* **The frame rate is not in the data.** 41 ms is `anim.py`'s reading — every
  effect bound to a body action declares `FrameInterval=41` — but that is the
  *effect's* rate matched to the action; 33 ms and 50 ms are documented
  alternatives. So there is a **speed control**, and 41 is a starting point, not
  a claim.

75 action codes are offered, grouped and named (`Standing`, `Moving`,
`Attacking`, `Casting`, `Being hit`, `Poses`, `Dying`). The 12 that `anim.py`
could not identify go in one bucket labelled **Unidentified codes**, last — not
hidden, not twelve bare numbers at the top of the menu. Only actions that
actually resolve are listed: **1,100 of the 3,260 motion files `3dmotion.ini`
names are absent from this install** (63% of all keys), though the four player
bodies are complete at 299/299, so the builder is unaffected.

---

## 5.2 Weapon quality, and the Super aura toggle

### The last digit is the quality — VERIFIED twice

A weapon appearance id is `TTTSSQ`: 3-digit weapon **type**, 2-digit **style**,
one **quality** digit. The first five are the family, and **every id in a family
is the same mesh** — only the texture moves.

| digit | quality | `41000` mesh | `41000` texture |
|---|---|---|---|
| 3 · 4 · 5 | **Normal** (three grades) | `410000` | `410005` |
| 6 | **Refined** | `410000` | `410006` |
| 7 | **Unique** | `410000` | `410006` |
| 8 | **Elite** | `410000` | `410008` |
| 9 | **Super** | `410000` | `410008` |

Five qualities, one mesh, **three distinct looks**. That is read off
`ini/weapon.ini`, and then confirmed from a completely different file:
`ini/itemtype.json` has `410003`…`410009` as one item, `SteelBlade`, at one
price and one level, with `attackMax` climbing **9 · 10 · 10 · 11 · 12 · 13 ·
14**. The item database states the ladder; the art only implies it. `410000`,
`410001` and `410002` have no item row at all.

Corpus-wide, over the **828** six-digit weapon families:

```
distinct textures per family     3 -> 538    1 -> 216    2 -> 5    4..10 -> 69
the 3-texture split              (345)(67)(89)  on 533 of those 538
                                   350 of them also carry a ...0 row
digits 3..9                      643 itemtype.json rows each
digits 1, 2                      4 and 1 item rows, in 31 families
```

### `410000` is not "Normal", and that mattered

`410000` resolves to texture `410006` — the **Refined** skin. It is not an
outlier: over every family that has a `…0` row, **350** put it on the 6/7
texture against a handful anywhere else, and in 139 families it is the only id
there is. So the `…0` id is the family's *base row*, not a quality, and calling
it Normal would have shown the wrong picture under a right-looking name on 350
weapons. It is listed separately, as **`…0 base row`**, with the reason on hover.

### The selector

The **Colour & variants** panel grows a **Quality** row for any weapon slot:
five named buttons, each showing the id it will equip and a **✦ aura** flag when
that id carries one. Picking one equips that appearance — same mesh, same pose,
same camera, different skin. Under it, stated rather than implied:

```
one mesh (c3/mesh/410000.c3) · 3 textures
sharing a look: Normal · Refined + Unique · Elite + Super
…0 base row · 410000
```

<kbd>Q</kbd> / shift-<kbd>Q</kbd> steps up and down the ladder without opening
anything.

**Outliers are reported, not forced into five.** A quality that is not offered
says which of *three different things* is true, because they are not the same
answer:

* `weapon.ini` has no `…6` id in this family;
* it lists one, but its mesh or texture does not ship, so it would draw nothing;
* it lists one and it is **a different mesh** — family `35004` is ten meshes
  that happen to share five digits, not ten qualities of one weapon, and the
  selector excludes them rather than offering a "quality" that swaps the object
  in your hand.

Every id the selector offers comes from the same vetted `options` list the
picker uses, so it is not a second, weaker path to equipping something broken.
Measured over the whole Super catalogue: **553** of the 646 usable `…9` weapons
sit in a clean three-tier family, **73** in a family with one look, **20** in a
family with no ladder at all.

The raw variant strip is still underneath, and it is worth knowing why the named
buttons are better than it: that strip is "every appearance sharing this mesh",
and for `410009` that is nine cells — the eight family ids **plus `800000`**, a
different weapon type that reuses the blade mesh. The quality selector is keyed
on the family, so it cannot wander like that.

### The aura toggle — the actual ask

*"Super items get an aura applied to them, visible on the character. Can you make
it so I can toggle that aura?"*

There **was** a "Show weapon effect" button, and — checked by driving the page,
not by reading it — it did work. It was just not findable: a small ghost button
in the fourth panel down, ~1,230 px into a 670 px scroller, and it only ever
appeared if you had already hunted `410009` out of a list of 5,384. So:

* a **Super aura** checkbox sits in the viewport control bar, next to grid and
  wireframe, visible whenever a weapon is equipped, with <kbd>A</kbd> as its key;
* the panel switch is a real labelled toggle reading *showing on the weapon* /
  *this weapon has one* / *not on this quality*;
* the intent is **sticky** — persisted in `localStorage`, and kept across weapon
  changes, so stepping Elite → Super lights the glow back up by itself;
* when there is no aura the panel says **why, in this weapon's own terms**
  (*"Steel Blade carries no aura at this quality. Super (410009) does — switch
  quality and it lights up."*) and offers a one-click **Switch to Super
  (410009)** rather than a greyed control. Clicking the greyed checkbox says the
  same thing instead of doing nothing.

**The rule is asked, not assumed.** `superfx.SuperFxDB` is a table lookup per
appearance id, and the corpus says it should stay one:

```
families whose ...9 id has an aura        646
  aura on ...9 ALONE                      575
  aura on EVERY quality                    70   the cosmetic weapon sets
  one-offs (...6, ...7, ...2, mixtures)    ~11
families with an aura on some non-Super id 213
```

So "only Super glows" is true 575 times out of 646 and the UI must not hardcode
it. 796 weapon appearances carry an aura across 29 families — `blade`,
`bigblade`, `lance`, `halberd`, `bow`, shields — not just `blade/`.

### The glow did not follow the swing — FIXED

Setting this up exposed a second, real bug. `EffectInstance.tick()` accepted a
parent matrix but passed it **only to the ribbon history**; `_drawEffects` drew
the static `PHY` quads from `inst.anchor`, which was whatever had been handed to
`setEffects()` at attach time. `placeSuperFx()` dutifully recomputed the socket
matrix on every animation frame and it went nowhere.

So the aura hung at the point the hand occupied when you switched it on, and the
sword swung out from under it — visually the *same* "off in space" symptom as the
child-instead-of-sibling composition, arriving from the opposite direction. On
`002132300` swinging `410009` through action `401`, frame 9 puts the socket at
`(28.1, −76.1, 72.5)` while the frozen anchor was still at `(-10.4, 18.7, 81.3)`
— about 100 units apart on a 170-unit body.

`Viewer.setEffectTime()` now adopts the parent matrix as the instance anchor,
which is what *"`parentFor(role)` supplies the world matrix the effect rides"*
was always supposed to mean, and `placeSuperFx()` pushes it straight through as
well so a *paused* or scrubbed frame is anchored too. Verified in the browser at
frame 9: effect anchor, `B.superfx.anchor` and the weapon's own model matrix are
the same 16 numbers, and the effect's world bounding box
(`10.9,−155.4,31.2 … 57.3,−66.0,108.9`) envelopes the weapon's
(`22.5,−151.3,63.3 … 48.7,−63.5,80.2`) along the blade axis.

The test asserts the socket genuinely moves >50 units over the swing, because if
it barely moved, freezing the anchor would be invisible and the test would be
theatre.

One small thing fell out of it: `#fx-wrap` was allowed to wrap freely, so ticking
the aura on grew the control bar by 98 px and visibly shrank the character. It is
one row now, and costs 43.

```bash
py -3 tools/builder.py --quality 410009    # the ladder, with the aura flags
py -3 tools/builder.py --quality 510000    # and a family that has no ladder
```

---

### The weapon effect, on the weapon

The user's complaint was that the super weapon effect drew "off in space".
`tools/superfx.py` settled why, and two things it found changed this UI:

* **It is a table lookup, not a filename convention.**
  `Action3DEffect[999.999.<type>.<sub>]` → `3DEffect.ini` → `EffectId0` →
  `3DEffectObj.ini` → the mesh. `blade/` is one of **29** families; guessing
  `c3/effect/<family>/<id>.c3` finds 194 and misses the rest. **796 weapon
  appearances carry an aura and 816 of 816 resolve**, so this is a general
  weapon-effect toggle, not a blade special case.
* **The effect is a *sibling* of the weapon at `v_r_weapon`, not its child.**

```
p_world = p_bind x M_fxBone(frame) x M_offset x M_socket x M_role
```

with the weapon's **own bone-0 matrix deliberately absent**. Composing it as well
is exactly the bug: on `410009` (bone-0 = 0.252 scale) the correct chain places
the glow at a span of 111.8 against the weapon's 90.0 with the hilts coinciding
within **0.4 units**; composing the weapon's matrix too gives **28.1** — buried
inside the grip. Verified across 359 effect/weapon pairs, with polearms as the
clincher. `tools/test_viewer.py` asserts the sibling composition against the
child one as a ratio, so a regression is a failing number.

Playback runs through `fx.js` — the same path the browser page uses — and the
anchor is re-evaluated per animation frame, so the glow follows the swing.
**That last sentence only became true in this round**: the re-evaluated matrix
reached the ribbon history and not the static quads, so the glow stood still
while the sword swung. See §5.2, which also covers the quality selector that
makes the aura findable in the first place.

---

## 5.1 Character assembly in the browser — equipping parts

The Equip panel on the browser page is unchanged and still there for equipping
whatever you happen to be looking at; the builder is the better way in for
assembling a whole character. Two explicit modes, switchable with the **View**
control or <kbd>V</kbd>:

* **Asset view** — whatever you pick is the subject; the camera fits it.
* **Character view** — the body is the subject; parts hang off named sockets and
  **the camera never re-frames when you change an attachment.** Only changing
  the body re-frames.

Each mode keeps its own camera *and its own lock flag*, persisted separately,
so inspecting a helmet on its own and coming back restores the character
framing rather than the helmet's zoom. The loadout survives the round trip
untouched — each equipped slot has a **view** button that jumps to asset view
with that part loaded, and the equip panel gets you back.

### The slots come from the client, not from us

`ini/RolePart.ini` `[Config] Count=13` is the character assembly manifest: every
part the client composes, each naming its mesh table *and* its motion table.
The equip panel is built from that file. Checked against this build:

| slot | table | entries | |
|---|---|---:|---|
| Body | `armor.ini` | 3,418 | the subject |
| Head | `armet.ini` | 2,918 | **hair and headgear share this slot** |
| Head (dx8) | `armet1.ini` | 950 | legacy DirectX 8 catalogue, a *disjoint* id set |
| Left / Right hand | `weapon.ini` | 5,384 | |
| Mount | `mount.ini` | 1,317 | |
| Misc | `misc.ini` | 272 | |
| Bare head | `head.ini` | 4 | one per body type |
| Shield | `shield.ini` | — | **declared but not shipped in this build** |
| Pelvis | `pelvis.ini` | — | **declared but not shipped in this build** |

`mix_body`, `mix_armet` and `mix_armet_dx8` point at the *same* ini files as
their non-mix counterparts, so they are aliases, not extra catalogues. Shield
and pelvis are shown greyed with "not shipped" rather than as empty lists.

### Hair is not a separate part — VERIFIED

The user's own knowledge ("hair only shows when another headgear is not
equipped") turned out to be exactly what the data says, and it explains three
things that looked like recovery failures:

* `head.ini` has only **4** entries — the four bare heads, not a hair library.
* **Nothing anywhere is named "hair"** across 24,757 recovered filenames.
* `HairMotion.ini` and `headmotion.ini` are **0 bytes**.

Hair lives inside `armet.ini`, in the head-covering slot, as series **`119`**:

```
002119310  = body 002, series 119, colour 3 (black), style 10
             mesh 2000010   texture 002000310
002119410  = ... colour 4 (white)
             mesh 2000010   texture 002000410       <- same mesh, different skin
```

Colours 3–9 are black / white / red / brown / green / blue / purple, exactly
matching the wiki's `Hairstyle Code = (Hair Colour × 100) + Hairstyle`, and the
whole colour family shares one mesh while only the texture changes — the same
mesh-shared / texture-per-colour pattern already proved for body armour. So
equipping a helmet *necessarily* replaces the hair, because it is the same slot.
The UI enforces that: equipping into either head slot clears the other.

> **This paragraph used to say series `111`, and that was wrong.** Series 111 is
> `IronHelmet` / `BronzeHelmet` / `SilverHelmet` …, 112–116 are likewise real
> hats with real item names, and **119 is the only armet series with no items at
> all** — which is what a hairstyle looks like in `itemtype.json`. 119 is also
> 515 of the 707 armet meshes and the family that resolves into `c3/hair/`, and
> `docs/attachment.md` §8.3 measures it sitting a median +4.0 units above the
> skull against 111's +18.1. See §5. The style number moved with the correction:
> series 111 puts the style in digit 7, series 119 in digits 7–8, which is why
> grouping hair by an id slice mixes ten styles together and grouping it **by
> mesh** does not.

### Where a part actually goes — VERIFIED

`RolePart.ini` has a second section, `[Dumy] Count=52`: the attachment points.
They are the same vocabulary the meshes themselves use (`v_armet`,
`v_l_weapon`, `v_r_weapon`, `v_head`, `v_l_shield`, `v_back`, `v_mantle`,
`v_pelvis`, `v_mount`, `v_rootloc`, `v_wsocket1..3`, `v_extend1..15`, …), and
two of them — **`V_ARMET_EFFECT01` / `V_ARMET_EFFECT02`** — exist purely to hang
effects off headgear.

A socket is a real submesh inside the body `.c3`, but measuring one shows it is
*not* pre-placed: every socket submesh sits at the origin and is skinned to
bone 0, which no body vertex uses. Its real transform comes from the `MOTI`
animation track — and `tools/effects.py` (task #13) decoded MOTI, so the viewer
reads it directly:

```
002135000  body 170.6 tall ->  v_armet at z = 164.4      on top of the head
001131000  body 168.0 tall ->  v_armet at z = 166.7
004134000  body 195.9 tall ->  v_armet at z = 190.8, hands at x = ±110, z = 152
```

That is the engine's own placement, position **and** orientation, applied as a
full 4×4. Where a body ships no per-socket MOTI (some do not), the anchor falls
back to an estimate measured from the body's own skin clusters — position only,
no rotation — and every part reports which of the two it got.

### The attachment transform — FIXED, and it took reading the code

Two symptoms — weapons attached at the right point at several times the right
size, and hair attached at the right point at the wrong orientation — and
**two separate bugs behind them**, both found by task #18's disassembly
(`docs/attachment.md`, `tools/attach.py`, imported here and never edited).

1. The **part's own `MOTI`** was not composed in. That matrix is where an
   equipped part's scale and axis correction live.
2. `PHY` and `MOTI` were paired **by adjacency** instead of by ordinal. On
   **403 of the 664 distinct body meshes** in `armor.ini` that produced *no
   sockets at all*, silently falling back to the rotation-less skin-cluster
   estimate. That is likely the larger half of the hair symptom. The four bodies
   the viewer was previously validated against are all in the interleaved 261 —
   a textbook unrepresentative sample, and there is now a corpus-wide census
   test so it cannot recur.

Putting an equipped part on a socket needs three transforms:

| stage | what | how |
|---|---|---|
| 1 `chunkMatrix` | the PHY chunk's own 4×4, applied to positions at load (RVA `0x5A735`) | **baked** into the emitted vertices. Applying it again is the 438-unit-offset bug. |
| 2 `partMotion` | the **part's own `MOTI`**, per vertex over both bone influences | **baked** server-side (`c3_to_json(bake_motion=True)`) |
| 3 `socketMatrix` | the socket's placement — position *and* orientation | sent to the client as the model matrix |

The composition is `tools/attach.py`'s, recovered from `Role3D.dll` and
`graphic.dll`:

```
idx   = mesh.FindPhyByName(dumy)                 graphic.dll 0x266D0
Msock = Motion_GetMatrix(bodyMotion[idx], 0, f)  graphic.dll 0x551A0, bone 0
Mbone = Motion_GetMatrix(partMotion[c], b, f)    the PART's own motion
world = Mbone x Msock x Mrole                    row-vector, D3DXMatrixMultiply
```

`Mbone` is indexed **per vertex**, so it cannot ride on a single model matrix
for a skinned part — the server bakes it into the emitted positions and hands
the client only `Msock` in render space. Same composition, split where the wire
format allows.

**The numbers, against the 170.5-unit `002135300` body:**

```
weapon 410009  mesh c3/mesh/410000.c3
   authored extent   70.4 x 359.3 x 17.7   longest 359.3  =  2.11x the body
   its own MOTI                            uniform scale 0.2515
   PLACED                                  longest  90.0  =  0.53x the body
                                                            (predicted 90.4)

armet 002111310  (a hairstyle)
   authored longest  40.9                                  =  0.24x the body
   its own MOTI      scale 0.940 / 0.912 / 0.940 — NON-uniform
   PLACED            38.4                                  =  0.23x the body
```

That is why hair looked wrong while its *size* looked fine: on headgear the
part motion is a near-unit matrix that carries **rotation**. Measured over the
whole `armet` table — **802 of the 1,830 armets that ship a `MOTI` carry a
rotation over 5°**, one family a full 180°, and 1,019 carry a scale ≠ 1. Drop
stage 2 and 44 % of all headgear sits at the wrong angle.

**`PHY` and `MOTI` are two ordinal lists, not adjacent pairs.** `MeshCreate`
(`0x28360`) builds the phy array and then calls `MotionCreate` on the same file
to build a separate motion array; `C3Mesh::SetMotion` (`0x27818`) does
`phy[i]->motion = motionSet->Get(i)`, and `Role3D!sub_86C0` uses the phy index
on the motion set with no remapping. Census of this install:

| table | meshes | grouped (adjacency FAILS) | interleaved |
|---|---:|---:|---:|
| `armor.ini` | 664 | **403** | 261 |
| `weapon.ini` | 438 | 10 | 3 |

`c3/mount/850/8500000.c3` is the clearest case: all eight `PHY` chunks first,
then all eight `MOTI` chunks. `attach.PartMesh.parse` is the authority and is
imported rather than re-derived.

### Three more things the disassembly settled

* **Socket chunks are identified by `RolePart.ini`'s `[Dumy]` list, not by a
  `v_` prefix.** The prefix test is wrong in both directions: hair meshes ship
  `v_armet01` / `v_armet02` chunks that are *real geometry* (46 and 20 meshes),
  and series-119 hair puts its visible geometry in a chunk named `v_body`.
  Hiding either renders the part empty.
* **The body is posed by its idle action motion**, `3dmotion.ini`
  `<bodyDigit>000100` frame 0, bound by ordinal — not by the `MOTI` embedded in
  the mesh. `Mesh::Draw` always applies a motion (`0x2607E`), so there is no
  bind pose to show, and the embedded track is whatever pose the artist saved.
  On `004134000` that is a **T-pose**: hands at x = ±98, up 154, against the
  idle pose's ±32, up 83. Bodies 001/002/003 agree between the two to within
  1.3 units — which is exactly why a four-body sample never noticed.
* **Both hair families are real.** Series `111` (40 meshes, chunk `v_armet01`)
  and series `119` (the larger family, mostly under `c3/hair/`, geometry in a
  chunk named `v_body`).

Placement, checked against `attach.py --validate`'s reference points:

| body | height | `v_armet` | hands (idle) |
|---|---:|---:|---|
| `001131000` | 167.2 | 156.7 | |
| `002135000` | **170.4** | **164.4** | |
| `003133000` | 176.2 | 172.7 | |
| `004134000` | 195.9 | 190.8 | x ±32, up 83 (was ±98, up 154) |

The chain is still reported **as separate stages**, each with its own
confidence, source note and translation/scale/rotation decomposition, and the
figure panel prints them on screen — so a regression reads as
`partMotion: scale 4.0` rather than "the sword looks big".

`tools/test_viewer.py`'s `AttachmentChain` class asserts all of this as numbers:
the render-space matrix conversion against an independent basis evaluation
(agreement to 5 places), the chunk matrix baked exactly once, socket transforms
carrying no scale, armets already head-sized, weapons landing under 1.6× body
height once placed, `v_armet` at 164.4 on the 170.4-tall body, the T-pose vs
idle difference on `004134000`, the `[Dumy]` socket rule in both directions,
**the corpus-wide chunk-layout census plus a check that all 403 grouped meshes
still yield sockets**, and that `bake_motion` is opt-in so **asset view still
shows a mesh exactly as authored**.

**Still unvalidated, stated because it was handed over that way:** `armet1.ini`
(254 meshes) and `misc.ini` (59) resolve almost nothing through `3dobj.ini`, so
those two slots have not been checked.

### The matrix coordinate conversion — VERIFIED

The project converts *positions* with `(x, y, −z)`. A *matrix* has to be
conjugated, not negated: with `S = diag(1, 1, −1)` and C3's row-vector
convention, the render-space column-major GL matrix is `S · Mᵀ · S`. Written in
flat array indices that collapses to **the same 16 numbers with the sign flipped
on the six entries where exactly one of (row, col) is 2** —
`effectplay.render_matrix()`. It agrees to 5 decimal places with
`parts.moti_sockets()`, which arrives at the same matrix the long way round by
evaluating the transform on basis vectors, and the test asserts that rather than
trusting the shortcut.

### Compatibility

Equipment art is body-type specific — armour and headgear IDs are prefixed
`001`–`004` (>90% measured) — while weapon IDs are not (<10%). So a helmet for
another body type produces a warning in the figure panel rather than a silently
broken render.

### Saving a loadout

The loadout persists in `localStorage` and **Copy link** gives you a URL that
restores it exactly:

```
http://127.0.0.1:8731/#body=002135300&armet=002111310&r_weapon=410005
```

---

## 5.3 Models — monsters, NPCs, ghosts, mounts, roles and effects

The builder has **two modes**, switched in the header or with <kbd>M</kbd>:

| | |
|---|---|
| **Character** | §5 — slots, appearances, equipment |
| **Models** | this section — one model, one action list, no slots |

The stage, the camera, the action dropdown, play/pause, the frame scrubber and
the speed control are **shared**, because that part of the job genuinely is the
same. The left rail is swapped wholesale, because the rest of it is not.

### Why a monster does not get slots

`c3/monster/103/` is sixteen files, one per action, and **thirteen of them
carry their own `PHY` as well as their own `MOTI`**:

```
c3/monster/103/100.c3   PHY + MOTI + CAME     idle
c3/monster/103/110.c3   PHY + MOTI + CAME     walk
c3/monster/103/401.c3   PHY + MOTI + CAME     attack
c3/monster/103/340.c3   MOTI only             a death that binds over the above
```

So "play the walk" means **load a different mesh**, not bind a different track
over one mesh. There is nothing to equip. Forcing that into the character
builder's slot model would have been wrong in both directions, so it is a
second mode rather than another table.

### Both layouts ship, and both are handled — VERIFIED

Measured over the 65 directories under `c3/monster/` and the 35 under
`c3/npc/`:

| layout | what it is | monster | npc |
|---|---|---:|---:|
| **per-action meshes** | every action file has `PHY` + `MOTI` | 15 | 11 |
| **skeleton + motion sets** | one geometry file (`1.c3`, or `<shape>000000.c3`) and motion-only action files | 21 | 17 |
| **mixed** | `103` ships `315`, `340` and `341` as motion-only alongside thirteen self-contained files | 28 | 7 |

The rule is therefore **per action, not per family**:

> If the action file has geometry it is both the mesh and the motion.
> Otherwise it binds over the family's base mesh.

Binding is ordinal either way (`C3Mesh::SetMotion`, `graphic.dll 0x277C0`),
which is why one code path serves both — the same one the player bodies use.
`c3/monster/198/1.c3` even ships `v_armet` and `v_r_weapon` sockets, so a few
monsters are assembled figures in the player's own sense.

### What ships here

```
kind                models  what it is
monster                 64  c3/monster/<dir>/
npc                     35  c3/npc/<dir>/
npc_simple              28  the flat c3/npc/999<look><action>.c3 set
ghost                    2  c3/ghost/098, 099  (shapes 98 / 99)
mount                    1  c3/mount/850 — the Christmas sleigh
role                     8  ini/3DsimpleRole.ini Role0..Role7
effect               2,255  ini/3DEffect.ini names, played through fx.js
```

`py -3 tools/models.py --kinds` recounts it, `--list monster` lists a kind,
`--model monster:103` prints one model's whole action list with the file each
code resolves to, and `--audit` regenerates the table above.

### The action list only ever offers files that exist

`ini/3dmotion.ini` declares **80 action codes for monster shape 103 and they
land on 16 files**. Both facts matter — the codes are what the server sends,
the files are what there is to look at — so nothing is dropped:

* every code the ini declares for the shape is listed, named through
  `anim.ACTIONS` (the vocabulary is the player's: `100` idle, `110` walk,
  `401` swing, `330` die — `docs/animation.md` §3, no second table here);
* a code whose file does not ship is listed **disabled, with the reason**, in a
  `Not in this install (n)` group — 1,100 of the 3,260 motion files
  `3dmotion.ini` names are absent, so this is a real filter;
* a code that repeats an earlier one's file is labelled *"same clip as N"* and
  hidden by the **distinct clips only** switch, which is on by default.

**Which code owns a shared file is decided, not left to sort order.** `140`,
`401`, `404`, `405`, `407` and `903` all resolve to `c3/monster/103/401.c3`;
`401.c3` belongs to action `401`, and calling 401 "the same clip as 140"
because 140 sorts first would be exactly backwards. The rule is *the code whose
own file is named after it wins*, and there is a test pinning it. The filter
also never moves you off the action you selected, alias or not.

`tools/test_viewer.py:ModelCatalogue` asserts over the whole catalogue that
every action marked available names a mesh **and** a motion the client can
open, that a motion-only action binds over a file that really does have
geometry, and that anything withheld carries a reason.

### Several shapes share one directory

`c3/monster/105/` is reached by shape `105` **and** by shape `249`; shape `104`
reaches directory `104n`. So the directory name and the `3dmotion.ini` shape
are not the same string, and the art is shared between creatures. The panel
names every shape that resolves into the directory and says whose action list
is being shown. The shape that matches the directory name wins; otherwise the
one with the most keys.

### `ini/monster.json` has no link to the art — and is still worth pairing

**Checked, and stated because the temptation to join on it is strong.** `type`
runs 1…9028 over 374 rows and only **14 of its values coincide with a directory
name** under `c3/monster/`; `bodyType` is `0` on every single row. There is no
join column: the server picks the appearance when it spawns the creature. So
monsters are browsed **by mesh directory**, and the panel says so in as many
words.

What the row *does* carry is real:

| field | range | why it matters |
|---|---|---|
| **`zoomPercent`** | **60 – 350** | monsters are scaled, sometimes drastically. A creature drawn at 100% can be nearly four times too small. |
| `bornAction` | `315` (353 rows) / `101` (21) | the action played on spawn — one click to play it |
| `bornEffect` | `MBStandard` / `MBGhost` | |
| `asb` / `adb` | `D3DBLEND` | the body's own blend factors |
| `sizeAdd`, `actResCtrl` | 0–5 / 0,1,24 | `actResCtrl` is probably the `ActionCtrl.ini` gate (`docs/animation.md` §7.3) — UNKNOWN |

So the **monster.json properties** panel lets you attach a row to whatever mesh
you are looking at and borrows its scale. The toast and the panel both say the
same thing: *"Your pairing, not the data's."* There is also a plain zoom slider
for when you just want to see it bigger. The zoom is applied as a uniform scale
on the model matrix — the field is VERIFIED, reading it as a percentage of the
authored size is INFERRED from its name and range.

### NPCs *do* have a real art link — through the motion, not the object

`npc.json` gives each of its 437 rows a `simple_object` and three motion keys.
The obvious route, `simple_object` → `3DSimpleObj.ini` → `Part0` → a mesh,
reaches **a file on disk for only 100 of the 437**. `standby_motion` is a
literal `3dmotion.ini` key (`999001100` = shape `999`, look `001`, action
`100`) and reaches a file that ships for **435 of 437**. So the motion path is
the link, and it is what carries `npc.json`'s names onto the families:
`Storekeeper`, `Blacksmith`, `Cerberus`, `Typhon`, `ChristmasReindeer` and 278
more land on 74 model families. Both routes are recorded; the motion wins.

### Textures come from `meshtex.py`, and they have to

`c3/monster/103/100.c3` has **no sibling `.dds` at all**, so the same-stem
convention the rest of the viewer leans on finds nothing. `tools/meshtex.py`
resolves it to `c3/texture/103000000.dds` through
`3dmotion.ini 103000100 → armor.ini [103000000] Texture0=103000000` and badges
it `authored`. Coverage over these trees is `c3/monster` 636/636, `c3/npc`
165/167, `c3/ghost` 4/4, `c3/mount` 1/1, and the panel prints the method and
whether it was authored or inferred.

### Effects, standalone

The effect library is a model kind. Picking one loads it through the same
`/api/effect` the asset browser uses and plays it with the same `fx.js`,
anchored at the origin because a standalone effect has no parent to ride, and
framed on its own quads' bounds so a 12-unit spark is not an invisible dot.
Effects whose every layer is `PTCL`/`PTC3` draw nothing and say so — particles
are still not decoded (§4.5).

### One family is listed and withheld

`npc_simple:217` ships two files, both motion-only, and its look id is not
referenced by `npc.json`, so there is no authored route to a base mesh to bind
them over. It stays in the catalogue with the reason recorded and `--audit`
counts it; the picker does not offer it, because a list row that draws nothing
is worse than no row.

### What could NOT be made previewable, and why

| family | verdict |
|---|---|
| **`ini/mount.ini` — 1,317 appearances** | **0 of its 1,317 `Mesh0` ids resolve to a file in this install** (recounted here, and it is the same finding §5's slot table already reports). The mount *slot* stays greyed for the same reason. The one mount family whose art does ship is `c3/mount/850`, which `mount.ini` does not name — it is reached as a directory, and it is in the list. |
| **`map/Scene` (364 `.scene`) and `map/ScenePart` (127)** | Not 3D models. A `.scene` is a composition of 2D sprite parts drawn onto the map, so there is no mesh, no motion and no action list to give them. They are already walked end to end by the **Maps** tab on the browser page (§4), which is the right place for them. |
| **`ini/3DSimpleObj.ini` — 137 sections** | 34 resolve to a mesh that ships, and every one of those meshes is already reachable as a role, an NPC or the mount, so a separate "simple objects" kind would have been the same 34 models under a second name. The table is still used, for the roles and as a texture source. |
| **262 of the 263 monster shapes `3dmotion.ini` names** | Only 65 directories under `c3/monster/` ship any art at all; the other shapes name files that are simply not in this install. They are not listed as empty models — there is nothing to show — but the per-action "not in this install" reporting inside a family that *is* here covers the same ground honestly. |
| **`npc_simple:217`** | Listed and withheld — see above. |
| **`PTCL` / `PTC3` effects** | Unchanged from §4.5: 464 of 2,255 effects need particles for at least one layer and particles are still not decoded. Those layers draw nothing and the panel says so. |

### Two lists, because one convention cannot reach everything

The **Catalogue** list is discovered from the client's own directory layout —
`c3/monster/<id>/`, `c3/npc/<id>/` and friends. That is what makes it a list of
*models* rather than of files, and it is also its limit: art filed any other
way is invisible to it. A recovered garment archive is. So is the Collection,
where a kept model lives at `collection/<category>/<entry>.c3`.

**By path** is the other list: every `.c3` under a prefix in the selected view,
with clickable folders, opening as an ad-hoc mesh — the same route a `#mesh=`
link takes, which is what puts geometry with no catalogue entry on the stage at
all. An entry's own action files are filtered out of it; listing three
collected NPCs alongside their eighteen motion files is a folder that reads as
noise.

The **Server** picker sits in the builder's header for the same reason it sits
in the browser's. The active view is one process-wide choice shared by both
pages, so this stage was always drawing from *some* namespace with no way to
say which, and no way to reach the Collection's without going via the other
page. Switching reloads: the model list, the appearance tables and every
cached clip are derived from it.

### Staging is on this page now

The builder could collect and nothing else — no replace, no remove, no
drawer. That is what "my mod staging is gone" describes from the page where
you actually look at a model and decide it should stand in for another one;
staging had only ever been built on the asset browser.

Both pages now load `tools/webui/swap.js`: the **Replace** panel (target
path, and tickboxes for whether the skin, the animations and the effects
travel with it) and the **Mod staging** drawer, one implementation. Porting
the drawer into `builder.js` instead would have made a second copy, and this
project has already paid for that once — see `action_code` below.

The panel asks where the skin goes only when that cannot be derived, because
for most entries it can, and a field that is usually noise stops being read
when it matters.

### A collected model's actions, and the two-frame pose

The client splits geometry from animation — `c3/npc/001/1.c3` is the model and
`100.c3`/`101.c3`/`190.c3` beside it are MOTI-only action files binding over it
by ordinal. **The Collection cannot keep those filenames.** One category folder
holds every entry on that shelf, so the second NPC you collect would overwrite
the first one's walk cycle; `collection.py` prefixes each part with the entry
it belongs to (`zephyr-npc-001__motion-100.c3`).

`_sibling_actions` therefore does not decide this at all: it asks
`collection.action_code`, which is the same function `gather_parts` asks, so
what the viewer offers to play is by construction what collecting would keep.
Three layouts, and the rule had been written twice — once here and once
there — which is exactly how one copy came to know about the Collection's
naming while the other still did not. `docs/assets.md` §8 has the table.

The match is anchored on *this* mesh's stem rather than merely stripping a
prefix, so a folder of six collected NPCs offers each model its own three
actions instead of all eighteen.

What was left when it found none was the container's **own MOTI track**, and
that is a bind pose: measured on a collected NPC, **2 frames against the 50,
120 and 120 of its three real actions**. It plays, it just has nothing to show
— which read as "animation is broken" rather than "the clips are in the ACTION
list". So the option now says what it holds (`this file's own track (2 frames —
a pose)`), and a mesh whose own track is a pose opens on its first real action.
Once, and only when nothing was chosen: selecting the pose back deliberately
has to stick.

### Keyboard, and links

<kbd>M</kbd> switches mode. In model mode <kbd>↑</kbd><kbd>↓</kbd> walks the
model list with the same 130 ms debounce and load token as everywhere else,
<kbd>[</kbd> and <kbd>]</kbd> step through the action list, and everything else
— <kbd>Space</kbd>, <kbd>L</kbd>, <kbd>R</kbd>, <kbd>G</kbd>, <kbd>W</kbd>,
<kbd>C</kbd> — behaves exactly as it does for a character. **Copy link** gives

```
http://127.0.0.1:8731/builder#model=monster:166&action=401&zoom=350
```

and the mode, the selected model, the action, the zoom and the paired row all
survive a reload.

### Changing action re-frames; changing character does not

The one deliberate difference from §5's rule. An equipped part must never yank
the camera, because the body is the subject — but a model's *geometry* changes
with the action on most families, and several clips carry real root
translation (monster 103's death slides 121 units). Holding the frame walks the
creature out of shot, so the frame is re-fitted on an action change.
**Lock camera** (<kbd>L</kbd>) still overrides, and that is the way to compare
two actions pixel for pixel.

### Two things this turned up in code that was already shipped

* **Whole-page shots have never included the control bar.** `pageshot.js`
  renames `<body>` to `<div>` for the SVG `foreignObject`, and with it every
  `body { … }` rule stops matching — including the `display:flex;height:100%`
  that bounds the page. The grid then grows to its tallest column (a 64-row
  model list is ~3,000 px), the viewport's `flex:1` grows with it, and the
  controls are pushed thousands of pixels off the bottom. Shots `21`–`27` all
  have that empty band. The layout is now restored explicitly. In the same
  pass: **Chrome does not paint native form controls inside a rasterised
  `foreignObject`**, so every `<button>`, `<select>`, checkbox and slider in the
  clone is swapped for plain markup that draws the same thing. Compare `30`
  with `21`.
* **`label.chk.hidden` never hid anything.** `label.chk` is element+class and
  `.hidden` is class alone, so the generic rule at the bottom of `style.css`
  lost on specificity and the **Super aura** toggle has been permanently
  visible since it was written — `classList.toggle('hidden', …)` on it did
  nothing at all. Same shape of failure as the collapsible headers in §5. Fixed,
  with a test that walks every `class="x hidden"` element on either page and
  checks that some rule can actually win.

---

## 6. Tagging

The **Tags** card has two halves, and the split is the important part.

**Derived (green, not editable).** `class:Warrior`, `gender:female`,
`size:small`, `body:001`, `kind:armour`, `series:131`. These are computed from
`bodyfacets.py` on every request and **are never written to disk**. There is no
code path that could overwrite a hand tag with a derived one, because derived
tags are not stored at all — they are a pure function of the appearance ID and
`itemtype.json`. If a future patch changes the data, they change with it.

**Yours (amber, editable).** Type space-separated tags and press Enter; click
the `×` on a chip to remove one. There is a free-text **note** field per subject
too, saved as you type. Tags are lowercased and trimmed; commas, quotes and
control characters are rejected so exports stay unambiguous.

**Bulk.** The *Tag all N…* button applies tags to **every** appearance matching
the current filters, not just the page you are looking at. Prefix a tag with `-`
to remove it instead. So "tag every small-female Warrior armour" is: tick three
chips, click the button, type a word.

**Filtering.** Every tag in use appears as a chip in the **Your tags** row with
a live count, alongside an **untagged** chip. Tag filters combine with the class
/ gender / size filters exactly like any other axis.

**Where it lives, and getting it out.**

```
out/viewer/tags.json        the store  (out/viewer/tags.bak keeps the previous version)
```

A single small, sorted, human-readable JSON file, outside the game install.
Writes go to a temp file and are then atomically renamed over the target, so an
interrupted write cannot corrupt it; if the file is ever unreadable it is moved
to `tags.corrupt` rather than silently replaced. **Export** gives you either the
raw JSON (re-importable, merge-only — an import can never delete work) or a CSV
enriched with the derived class/gender/size columns and the item name:

```csv
subject,tags,note,class,gender,item,kind,mesh,size,texture
app:001131300,favourite recolour-todo,,Warrior,female,OxhideArmor,armour,c3/mesh/001131000.c3,small,c3/texture/001131300.dds
```

Subjects are `app:<appearance id>` or `file:<logical path>`, so raw textures and
meshes picked from the **Files** tab can be tagged too. An appearance tagged
under `body` is the same subject under `mix_body` — they are the same
`armor.ini` row, so tagging it once is correct.

```bash
py -3 tools/tagstore.py            # list every tag and its count
py -3 tools/tagstore.py --csv      # dump the store as CSV
```

---

## 10. The MapEditor — `/mapedit`

Its own page, for the same reason the builder is: a map wants the whole window,
and this page's centre is flat art rather than a 3D viewport.

**Full write-up: `docs/mapeditor.md`.** In short:

```
┌──────────────┬──────────────────────────────┬──────────────────────┐
│ MAP          │                              │ ▾ This map           │
│  156 rows    │        THE MAP               │ ▾ Selected           │
│              │   (2D canvas, tiled,         │ ▾ Texture (+ swap)   │
│ LAYERS       │    the locked camera)        │ ▾ Passability ⚠      │
│  background  │                              │ ▾ What may I change? │
│  base puzzle │                              │                      │
│  TERRAIN     ├──────────────────────────────┤                      │
│  COVER       │ art px · cell · zoom level   │                      │
│  passability │                              │                      │
│ VIEW − % + fit                              │                      │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

* **It draws what the game draws**, in the game's own draw order — background
  planes, the painted `.pul` ground, `TERRAIN` scene objects, `COVER` sprites —
  through the locked camera `docs/map_scenery.md` §6 derives. Each layer is a
  separate image, so a toggle is instant and the ground is genuinely transparent
  where nothing is painted. A fifth layer draws the **passability grid**, with
  cells only a `TERRAIN` layer opens in a different colour from the base grid's.
* **Zoom is a scale on that image, never a projection.** There is no camera state
  in the page at all, and a test asserts there is no way to grow one.
* **Any of the 156 `GameMap.json` rows**, with the state of its art rather than a
  silent omission: 150 ok, 4 `.pux`, `sky`'s known mismatch, one row whose
  `.DMap` does not ship.
* **Click anything** — a puzzle tile, a scene part, a cover — and get the source
  file, the `.ani` key and frame, the cell origin, the pixel offset, the sprite
  size, the frame interval, the resolved texture path, whether it opens or blocks
  cells, and its `map/ScenePart/*.Part` source. Sprites are alpha-tested, so a
  click through a gap in a tree selects what is behind it.
* **Staging is the §3 flow.** The map reads through `mods/stage/` first, so a
  staged texture is on the map before anything is installed, and *Install* is
  `comod.py install --yes` exactly as everywhere else.

### The integrity line, shown always

`integrity.json` turns out to have **162 entries naming 143 distinct files: 136
`.DMap` and 7 `ini/*.json`** — the 19 extra rows are maps with more than one
`DocumentId`, listed once per id with the same hash. **Not one `.pul`, `.scene`,
`.Part`, `.ani`, `.pux` or `.dds` is in it.**

So a map's art is free (95 of `newbie`'s 96 files, 432 of `Gulf`'s 433) and its
cell grid is not. Every inspector panel leads with which side the selection is
on. Editing passability *is* possible — it byte-patches the mask and recomputes
the row checksum `core/dmap.py` verifies, changing 4 bytes of a 118,644-byte
file — but it is behind a checkbox, a confirmation and an acknowledgement string
the server refuses without.

---

## 7. What is exact, and what is approximated

This is the part that matters. A preview that quietly lies is worse than no
preview.

### Exact — reproduced from the engine's own code

| Thing | Why it is exact |
|---|---|
| **DDS decoding** | `core/dds.py` implements BC1/BC2/BC3 and mask-driven uncompressed formats from the S3TC spec. It is **byte-identical to Pillow's independent C decoder on every pixel-format class that ships here** — DXT1, DXT3, DXT5 (mipmapped and not) and 32-bit uncompressed — plus 120 randomly sampled archived textures. Synthetic hand-computed blocks pin the spec independently of both decoders. |
| **Geometry** | Parsed by `core/c3phy.py`, which was recovered by disassembling `graphic.dll!Phy_Load` and parses 15,103 / 15,103 PHY chunks to exactly their declared length. The viewer's JSON is cross-checked against the known-good OBJ exports in `out/c3/obj/`: identical vertex and face counts and an **identical index order**, positions agreeing to the JSON's 4-decimal rounding. |
| **The 4×4 chunk matrix** | Applied to positions at load, as the engine does at RVA `0x5A735`. ~7.5% of chunks are non-identity, and some of them matter a lot: `c3/mesh/001134070.c3`'s `v_body` carries a −438-unit Z translation, so ignoring it leaves the model floating 438 units off the ground. |
| **Normals** | `PHY ` and `PHY4` — the only two variants shipped in this build — do not store normals; the engine generates them (RVA `0x5A8C0`) and `c3phy.generate_normals` reproduces that accumulate-and-normalise exactly. |
| **Winding and culling** | C3 triangles are D3D clockwise-front in a left-handed, Z-down frame. The server emits `(x, y, −z)` with the index order **untouched**; a single-axis mirror flips handedness, so `gl.frontFace(CCW)` + `gl.cullFace(BACK)` is the engine's culling. Measured on 238 `v_body` meshes: **237 (99.6%)** have their majority of triangles facing outward under that rule (docs/modding.md reports 97% on a different sample). The `inverted` culling option exists so you can see the difference. |
| **Two-sided meshes** | The `2SID` chunk tag disables culling for that chunk, which is what the tag means. It is extremely common in the 2022 loose patches — 3,580 of 4,644 loose PHY chunks carry it. |
| **UV orientation** | D3D's V axis runs top-down and so does PNG row order, and `UNPACK_FLIP_Y_WEBGL` is left off, so there is no flip anywhere and none is needed. |
| **Asset resolution** | Loose file first, archive second — `TqPackage!TqFOpen`, VERIFIED. 587 loose files in this install shadow a same-named archive entry; 30 of them differ in size, which is the proof the loose copy is what runs. |

### Approximated — and why

| Thing | Status |
|---|---|
| **Lighting** | The engine's actual lighting model has not been recovered. Default is **unlit** — the texture exactly as authored, which is the honest choice. `lit` is a fixed headlight for reading shape and is explicitly *not* game-accurate. |
| **Alpha blend state** | The per-frame alpha value comes from the mesh's own C3Key track (below), but the D3D blend/z-write state the engine sets is not recovered. The viewer offers standard blend, alpha test and opaque, and depth-sorts blended chunks back-to-front by centroid. The appearance tables do carry `Asb`/`Adb`/`ZBuffer` fields per part (e.g. `Asb0=5, Adb0=6`) that almost certainly *are* the D3D blend factors — decoding those is the obvious next improvement. |
| **C3Key alpha interpolation** | The 16-byte `C3Frame` records decode cleanly — `fParam` lands in [0,1] and `nFrame` is monotonic, e.g. `c3/effect/1ghost/1.c3` runs 0→1 at frame 5, holds to 20, back to 0 at 30. The viewer **linearly interpolates** between keys. That is the obvious reading, but the engine's own interpolator (`Key_ProcessAlpha`) has not been disassembled. The `bParam`/`nParam` fields of these records are `0xCDCDCDCD` — MSVC uninitialised heap fill — so they carry nothing. |
| **Skeletal animation** | **Played, on the builder page** (§5): the poses are the shipped `3dmotion.ini` clips, resolved by `tools/anim.py` against the body *and the equipped weapon*, evaluated per frame and uploaded as position buffers. What is approximate is the **frame rate** — 41 ms is `anim.py`'s reading, not a recovered constant, hence the speed control — and the fact that two-weapon loadouts fall back to the right hand's type because the `6XY` combination codes are assigned by the packed exe. Root motion is not approximated: walk and run genuinely translate by ~0 and a jump's arc is baked into the poses. The browser page still shows a single idle frame. Equipped parts are skinned by their own `MOTI` at frame 0. |
| **Character assembly** | Now the engine's own composition (`tools/attach.py`, `docs/attachment.md`) — see §5. What remains approximate: the *choice* of idle action for a static preview (INFERRED, though `100` is the obvious one), and `armet1.ini` / `misc.ini`, which are unvalidated. |
| **Effect blend state** | The `ASB`/`ADB` fields are honoured per layer as `D3DBLEND`. Two honest gaps: a `SRCCOLOR` source factor makes the alpha envelope inert, and the canvas is `alpha:false` so `DESTALPHA` reads as 1.0. |
| **Effect duration** | From the alpha envelope, not the declared track length. INFERRED — `docs/effects.md` §6.5. It is what the data plainly shows (363 ms vs 3.3 s for `m-b02`), but the engine's own despawn logic is inside Themida. |
| **`changeTex` past the last key** | `effects.key_change_tex` returns "no cell" when no key sits past the current frame, so the flipbook falls back to the scroll path (cell 0) during a fade-out. `fx.js` mirrors that exactly rather than inventing a hold-last-key rule — one implementation, testable. Worth re-checking against `Key_ProcessChangeTex` (RVA `0x726B0`) if a burst ever looks like it snaps texture while fading. |
| **Mesh → texture for a bare `.c3`** | INFERRED. It inverts the appearance tables, then falls back to a same-named `.dds` sibling. The panel says when a texture was inferred. |
| **A monster's size** | The `zoomPercent` field is VERIFIED to exist and to run 60–350; reading it as a uniform percentage of the authored size is INFERRED from its name and range. And **which** row belongs to which mesh is not inferred at all — `monster.json` carries no join column, so the pairing is the user's and is labelled as such (§5.3). |
| **A model's action list** | The codes are `3dmotion.ini`'s for that shape — VERIFIED. Which code is the *canonical* owner of a file several codes share is this viewer's rule (the code the file is named after), not the engine's; it only affects which one the "distinct clips only" filter keeps. |
| **Which files collapse into one entry** | `tools/meshtex.py`'s relation, used only where it is **authored or rank-1** (§4). 5,799 of 66,832 textures fold; the other 61,033 keep their own row. Every component in *What goes with this* is badged `authored` or `inferred` with its method and confidence, so the two are never presented as the same claim. |
| **ID → file resolution** | INFERRED, inherited from `coassets.py`: reproduces 94% of `armor.ini`, 98% of `weapon.ini`, 79% of `armet.ini`. Unresolvable IDs are shown as `mesh NNN — unresolved` rather than hidden. |
| **Mip filtering** | The viewer generates mipmaps for power-of-two textures. The engine also generates its own (19,293 of 19,295 archived textures declare `mipmaps=0`), but the exact filter it uses is unknown. |

### Not implemented

* **`uv1` (the second UV set).** It exists in the vertex layout at offset `0x34`
  but is only written by the `PHY5` variant, and **no `PHY5` chunk ships in this
  build** — the install is `PHY ` (11,094 chunks) and `PHY4` (4,009). The
  pipeline carries `uv1` through and would show it; there is nothing to show.
  Verified: zero non-zero `uv1` values across the whole corpus.
* **`MNEW` and `CCFL` chunks** (31 and 26 in the 2022 patches) are undecoded —
  they are listed in the Mesh panel as "other chunks" and not drawn.
* **`PTCL` / `PTC3` particle systems.** 431 effect objects are pure particles.
  Their layers draw nothing and say so. See §4.5.
* ~~**Map viewing/editing.**~~ **Built, as of task #30 — see §10 and
  `docs/mapeditor.md`.** The old note here said maps were out of scope because
  `integrity.json` covers them. Measuring the manifest showed that it covers the
  `.DMap` files and *nothing else* — not one `.pul`, `.scene`, `.Part`, `.ani`
  or `.dds` — so a map's **art** was never covered and edits like any other
  texture. The cell grid still is, and the MapEditor gates it.
* **Mesh editing.** This is a viewer. Geometry editing goes through the Blender
  bridge.

---

## 8. Two findings worth knowing

**Packed vertex colour carries no information in this build.** The `u32` at
vertex offset `0x14` (GPU `COLOR0`) is `0x00000000` on all 1,359,179 vertices in
the archived corpus and `0xFFFFFFFF` on 56,870 of 741,411 loose-patch vertices —
those are the *only two values that occur anywhere*. So (a) the channel order is
not observable, and (b) modulating by it naively would render every 2017
archive mesh solid black. The viewer leaves the *vertex colour* toggle off by
default. Consistently, **not one mesh in the corpus carries the `C3EXP_COLOR`
label** that the engine looks for to enable per-vertex colour.

**`frameCount` is not the animation length.** The `u32` at `C3Phy+0x190` is 0, 1
or 2 on meshes whose alpha keyframes run out to frame 70. The viewer derives the
frame slider's range from the last keyframe instead.

---

## 9. Layout, and how to test it

```
tools/coviewer.py      the server: catalogue, provenance, geometry JSON, swap + tag API
core/dds.py           DDS reader + BC1/BC2/BC3 + uncompressed decoders
tools/bodyfacets.py    class / gender / size classification (docs/appearance_ids.md)
tools/catalog.py       the browsing taxonomy + "what goes with this"
tools/mapindex.py      a map and every piece of art that draws it
tools/parts.py         RolePart.ini manifest, the 52 sockets, the staged attach chain
tools/builder.py       the character builder's catalogue: valid options per slot
tools/models.py        the OTHER model families (§5.3): monsters, NPCs, ghosts,
                       mounts, the character-select roles and the effect library,
                       each with the action list its files actually support
tools/unify.py         mesh <-> texture, and the one-row-per-asset collapse
tools/attach.py        the engine's own attachment composition (task #18) — IMPORTED, never edited
tools/meshtex.py       mesh -> texture with a method and a confidence — IMPORTED, never edited
tools/anim.py          action motions: clips, chains, loop modes (task #20) — IMPORTED, never edited
tools/superfx.py       the always-on weapon effect and its anchor (task #20) — IMPORTED, never edited
tools/effectplay.py    3DEffect.ini name -> playable scene; the §8 reference impl
tools/tagstore.py      the user tag store: atomic writes, JSON + CSV export
tools/mapedit.py       the MapEditor's model (§10): per-layer tiles, hit testing,
                       integrity verdicts, the one gated .DMap patch
tools/webui/
  index.html           the asset browser — three-pane layout
  builder.html         the character builder — slots / stage / collapsible panels
  mapedit.html         the MapEditor — picker / canvas / inspector
  mapmodel.js          the MapEditor's state and geometry — NO DOM, NO fetch
  mapedit.js           the MapEditor's canvas, DOM and network
  mapedit.css          no literal colour; every value is a style.css token
  style.css            no external fonts or assets
  fx.js                effect playback — a mirror of effectplay.py's reference
  gl.js                the WebGL renderer, hand-written, no libraries
  cards.js             collapsible panels — ONE implementation, both pages (§5)
  app.js               browser UI wiring
  builder.js           builder UI wiring
  pageshot.js          whole-page PNG capture, shared, no library and no network
tools/test_viewer.py   326 tests
out/thumbs/            task #21's rendered thumbnails + manifest (read, never written)
out/viewer/preview/    un-staged texture previews (throwaway)
out/viewer/shots/      saved viewport frames
out/viewer/texcache/   c3tex's extraction cache
out/viewer/tags.json   your tags (+ .bak)
```

It builds on, and does not duplicate, the existing tools: `c3phy.py` for mesh
parsing, `coassets.py` for the asset VFS and ini tables, `c3tex.py` for the
mesh→texture inference, `wdf.py` for the archives, and `comod.py` — invoked as a
subprocess — for every write to the install.

```bash
py -3 tools/test_viewer.py            # 326 tests, ~65 s
py -3 tools/test_viewer.py -v
py -3 tools/test_viewer.py EffectPlayback AttachmentChain
py -3 tools/test_viewer.py CharacterBuilder UnifiedEntries BuilderUiModel
py -3 tools/test_viewer.py WeaponQuality SuperAura CollapsiblePanels
py -3 tools/test_viewer.py ModelCatalogue ModelModeUi
py -3 tools/test_viewer.py IntegrityManifest MapEditorModel MapEditorUi
py -3 core/dds.py "path/to/some.dds" # decode one file and report

py -3 tools/builder.py --slots        # what each slot can offer, and why not
py -3 tools/builder.py --quality 410009   # the quality ladder + aura flags
py -3 tools/builder.py --slot armet --body 002132300 --kind hair
py -3 tools/builder.py --audit        # the numbers §5 quotes, recomputed

py -3 tools/models.py --kinds         # every model family, counted
py -3 tools/models.py --list monster
py -3 tools/models.py --model monster:103   # its 80 codes over 16 files
py -3 tools/models.py --monsters      # ini/monster.json as render properties
py -3 tools/models.py --audit         # the numbers §5.3 quotes, recomputed
py -3 tools/unify.py --stats
py -3 tools/unify.py c3/mesh/002135000.c3
```

The tests cover synthetic BC blocks against the spec, the real corpus against
Pillow, the geometry pipeline against `c3phy` and the known-good OBJ exports,
the winding claim, loose-shadows-archive provenance, and the staging path —
including an explicit assertion that staging does not touch the game install.
They skip cleanly if the install is not present.

Two groups are new and are the ones that matter for this round:

* **`AttachmentChain`** — the transform chain as *numbers*, across all four body
  types. A weapon whose blade reads as twice the character's height is not a
  judgement call, so these assert bounds ratios, per-stage scale, and that the
  matrix conversion agrees with an independent derivation.
* **`EffectPlayback`** — `docs/effects.md` §8 against real assets: the flipbook
  walking `m-b02`'s 2×2 atlas cell by cell, the alpha envelope giving 363 ms and
  not 3.3 s, the three key channels behaving *differently*, `frame_at` despawning
  a `LoopTime=1` effect and never despawning an endless aura, and the ribbon
  producing zero smear on a still parent and a real one on a moving parent.

Three groups are new with the builder (§5), and two of them exist to make a
specific failure impossible rather than merely unlikely:

* **`CharacterBuilder`** — over the *whole* catalogue, every offered option's
  mesh and texture must be the exact ids of an ini row and both must exist, so
  **the builder cannot produce an invalid mesh+texture pair**; and for each of
  the four body types, a body-specific slot must offer nothing carrying another
  body's prefix, so **incompatible parts are never offered**. Plus: colour
  variants all share their garment's mesh; grouping really does turn ~800
  appearances into ~167 garments; identical looks are shown once with the rest
  as aliases; every facet chip's promised count equals what ticking it returns;
  single-valued axes are not offered; unusable slots report a reason and offer
  zero; the page opens on a character that resolves; options are named in words.
  Then the animation and effect seams: actions resolve against the *equipped
  weapon*, run chains 120↔121 while walk does not, walk and run translate the
  root by ~0 while a jump lifts 85 units, a missing motion returns None rather
  than a silent fallback, and the weapon aura anchors as a **sibling** of the
  weapon — asserted as the ratio between the correct composition and the wrong
  one, because "the glow is inside the grip" is not a judgement call.
* **`UnifiedEntries`** — the collapse is conservative and reversible: a garment
  and its skins are one row, a texture nothing owns keeps its own row, a texture
  folds only when its owner is in the same view, search by either name still
  finds the merged row, one texture can belong to several meshes with exactly
  one primary, authored and inferred stay apart, and over the whole corpus
  `rows_out == rows_in - (owned textures present alongside their owner)`.
  The thumbnail manifest is optional and its reader accepts four plausible
  shapes, because that file is another agent's to design.
* **`BuilderUiModel`** — the page logic that is pure logic: collapse-all is a
  single state, the head slot is exclusive, changing body type drops art cut for
  the old one while leaving weapons alone, and a builder link round-trips.

Four more come with §5.2, and three of the four deliberately read the *shipped
files* rather than a Python mirror of the logic:

* **`WeaponQuality`** — the digit→quality reading against `ini/weapon.ini` *and*
  against `ini/itemtype.json`'s rising attack, so it is corroborated rather than
  asserted; that `410000` maps to the **Refined** texture and is never offered
  as Normal, checked as a one-off and then as a corpus count (350 families track
  6/7, an order of magnitude more than track 3/4/5); that switching quality
  never changes the mesh, over the whole Super catalogue; that every id the
  selector offers was already vetted against disk; that a missing quality names
  which of the three reasons applies; and that the outlier families are counted
  and reported rather than swept into three tiers.
* **`SuperAura`** — Super glows and Normal/Refined/Unique/Elite do not in family
  `41000`; the aura is a *table* lookup reaching 20+ families, not a filename
  guess; **and the exception is asserted as hard as the rule** — the corpus must
  still contain families that glow at every quality, because a UI that hardcodes
  "only …9" would be wrong 70 times.
* **`CollapsiblePanels`** — the one that would have caught the reported bug. Not
  a logic mirror: it reads `style.css`, `index.html`, `builder.html`,
  `cards.js`, `app.js` and `builder.js` and asserts that the pointer cursor is
  scoped to `.card[data-card]`, that *every* `<section class="card">` on either
  page has `data-card` + a `.cardbody` + a unique name, that both pages load
  `cards.js` and expose `#btn-collapse-all`, and that neither page keeps a
  private second copy of the toggle logic.
* **`EffectAnchorFollowsTheHand`** — that `setEffectTime` feeds the moving parent
  matrix into `inst.anchor` (which is what actually draws the quads), plus the
  number that makes the test worth having: the `v_r_weapon` socket moves more
  than 50 units across a `401` swing, so a frozen anchor is genuinely visible.

Two come with §5.3, and the first of them is the invariant the whole mode
rests on:

* **`ModelCatalogue`** — over the *whole* catalogue, every action marked
  available must name a mesh **and** a motion the client can open, and anything
  withheld must carry a reason (1,100 motion files are missing from this
  install, so the filter is doing real work and the test checks that it fired).
  Then the layout claim from both sides: a motion-only action must bind over a
  file that genuinely has `PHY`, a self-contained one must genuinely carry
  `PHY` *and* `MOTI`, and all three layouts must be present in the corpus so
  neither is treated as an edge case. Plus: `monster.json` carries no join
  column and none is invented (`bodyType` is 0 on all 374 rows and only 14
  `type` values coincide with a directory name); `zoomPercent` really does span
  60–350; `npc.json`'s names reach the families through `standby_motion`;
  action names come from `anim.ACTIONS` rather than a second table; an alias
  points at the canonical code and not the reverse; every family the picker
  offers has a playable default; and the kind chips' counts are cross-filtered
  like every other facet.
* **`ModelModeUi`** — the shipped files, not a mirror: the mode switch and both
  rails exist, `#slots-rail.hidden` / `#model-rail.hidden` beat their own id
  selectors, model mode never opens a slot picker, there is exactly one action
  dropdown and one play button driven by both modes, the zoom really is a
  uniform scale, and — the one that found a live bug — **every
  `class="x hidden"` element on either page must have a rule specific enough to
  actually hide it.**

And one that exists because the bug it catches survived for the whole life of
this viewer:

* **`DefaultView`** — the default yaw, read out of `gl.js` and pushed through a
  mirror of `_basis()`: the eye's Y component must be **negative**, because the
  corpus faces −Y. It also asserts the viewport and `tools/thumbs.py` render
  from the same yaw, that `resetView` does not hardcode the old value, and that
  the `localStorage` migration replaces an untouched old default while leaving a
  camera the user actually orbited alone.

### Verified renders

`out/viewer/shots/` holds frames captured from the running viewport:

| File | What it proves |
|---|---|
| `01`–`02_body_002135300_*` | an archived `PHY ` character (504 v / 672 f, 26 bones), textured, upright, standing on the grid |
| `03_..._inverted_culling` | the same model with culling flipped — you see the far side, which is what a wrong winding setting looks like |
| `04_..._wireframe`, `05_..._uv_checker`, `06_..._normals` | the inspection modes |
| `08_..._TEXTURE_SWAPPED_preview` | the same model re-rendered live with an edited texture, before anything was staged |
| `10_weapon_410005` | a weapon mesh (`Box01`, GBK label `…\桌面\新建文件夹1\dao\04-1.tga`) |
| `11_npc185_loose_PHY4_textured` | a 2022 loose `PHY4` NPC: 2,240 v / 3,167 f, 79 bones, `2SID`, with correct alpha on the translucent ribbons |
| `12_effect_1ghost_alphakey_frame10` | an effect mesh drawn through its C3Key alpha track |
| `13_taoist_male_large` | the class/gender/size filters resolving to a real asset |
| `14_keynav_warrior_female_small` | arrow-key navigation inside a filtered set |
| `15_categories_related` | category browsing with the related-assets panel |
| `16_character_equipped` | **an assembled character** — body in its idle pose, hair on the head socket, and a correctly-scaled blade in the hand. Re-rendered after the §5 fix: the sword was 359 units (2.11× the body) and is now 90.0 (0.53×) |
| `17_character_weapon_aura` | the same figure with the `410009` aura playing along the blade. It only became visible once the scale was fixed — at 4× the aura quads were buried inside the sword |
| `18_impact_spark_at_target` | the `m-b02` impact spark drawn **away from the character**, on its target dummy, while the aura sits on the blade |
| `19_impact_mb02_f0/f3/f6/f9` | the same spark frame by frame: the 16 radial streaks expanding and the 2×2 flipbook stepping cells, then fading |
| `20_shap_trail_swing_preview` | a `Flash4102` `SHAP` ribbon — 36 pairs / 72 strip vertices swept through the viewer's own 600 ms arc |
| `21_builder_page` | **the character builder, populated** — `Coat`, `Hairstyle 10 · black` and an `Amethyst Blade` named in words with ids secondary, 28 colour swatches for the equipped garment, the animation panel showing `attack swing 1 (401)` resolved through weapon set `410`, and its own honesty notes on frame rate and root motion |
| `22_builder_swing_weapon_aura` | the same character mid-swing at frame 9 of `c3/0002/410/401.c3` with the `410089` aura **on the blade** — the sibling-not-child anchor, which is the fix for "off in space" |
| `23_builder_slot_picker` | **an open slot picker**: "Change body & armour / only art made for your large female body", a search box, cross-filtered class chips, 11 Warrior garments × 8 colours, the keyboard legend, and the character updating live behind it |
| `24_unified_mesh_texture_entry` | **one entry for a mesh and its skins** — `002135000.c3 · + 12 skins in this entry` as a single left-pane row, with *What goes with this* listing the mesh and all 12 textures independently selectable, each badged `authored` and citing the exact `armor.ini` row |
| `25_builder_super_aura_ON` / `26_..._OFF` | **the same Super blade with its aura on and off** — identical camera (locked), identical pose (`401` frame 9), identical `410009`. The pair shows the whole of §5.2 at once: the named **Quality** row with Super selected and flagged `✦ aura`, `one mesh · 3 textures` with `Refined + Unique` and `Elite + Super` sharing a look, the `…0 base row` chip kept out of the ladder, and the **Super aura** switch reading *showing on the weapon* / *this weapon has one*. The glow sits **along the blade**, not where the hand was when it was switched on — that is the `setEffectTime` fix in §5.2 |
| `27_browser_panels_collapse` | **the asset browser's panels actually collapsing** — seven of the eight headers rolled up to `▸` with only *Equip* open, the header reading **Collapse panels**. Every one of those headers has shown a hand cursor and done nothing since the page was written |
| `28_model_monster103_idle` | **a monster, textured and posed** — `c3/monster/103/100.c3`, its skin resolved through `3dmotion.ini` → `armor.ini` because there is no sibling `.dds` to guess from |
| `29_model_monster103_attack_f9` | the same creature at frame 9 of `401` — a **different mesh file**, which is the whole point of §5.3 |
| `30_builder_model_mode_monster` | **model mode, whole page** — the Character/Models switch, the kind chips with cross-filtered counts (`Monsters 64 · NPCs 35 · Ghosts 2 · Mounts 1 · Roles 8 · Effects 2255`), 64 monster rows with rendered thumbnails, *This model* naming the mesh, the texture and its method, the shape and the layout, and the `monster.json` pairing panel underneath. **This is also the first whole-page shot this project has taken that includes the control bar** — see §5.3 |
| `31_model_monster166_attack_f8` | monster `166` mid-thrust with its naginata, frame 8 of `c3/monster/166/401.c3` |
| `31b_model_monster166_walk_strip` | frames 0 / 8 / 16 / 23 of its 31-frame walk side by side — the legs and the body actually move |
| `32_model_effect_mb02` | an effect played on its own, framed on its own quads, through the same `fx.js` §4.5 documents |
| `33_model_monster_json_paired_zoom` | **the `monster.json` pairing** — `Siren` attached to monster `166`, `zoomPercent 350%` applied, `born action 315`, `blend 5 / 6`, and the panel and the toast both saying *"Your pairing, not the data's"* |
| `34_model_npc185_idle` · `35_model_ghost098_walk` · `36_model_role1_standby` · `37_model_mount850` | one from each of the other four mesh-backed kinds: an NPC, a ghost walking, a character-select role, and the mount family (a reindeer sleigh) |

The last six are whole-page captures rather than viewport frames (`pageshot.js`,
`window.pageShot()`), because what they have to prove is the *interface*.

`pageshot.js` gained one fix while these were taken: `cloneNode` copies
attributes, not form *properties*, so every checkbox, slider and `<select>` in a
saved page shot showed its authored markup rather than its live state — a ticked
box photographed as unticked. The clone's inputs are now written from the live
ones before serialisation, which is why the switch in `25`/`26` reads correctly.
