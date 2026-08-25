# COMod — Conquer Online asset viewer & modding tools

HUMAN NOTE - This is 100% vibe coded. I know a little bit about computers,
programming, troubleshooting, etc... but I am not a software developer, or a
game developer, or a 3d artist or animator. Maybe thats a bad thing, maybe its
not. The world is weird right now. Lets learn as much as we can, and see how
far we can get, no matter what tools we use.

Tools for reading, viewing and modding the assets of **classic-era Conquer
Online clients** — any client built on TQ Digital's classic formats: `.wdf` archives,
`.c3`/PHY meshes, DDS textures, `.DMap` worlds and the `ini/` game database.

What is in the box:

* a **browser-based Asset Viewer** — every texture, mesh and map the client
  can see, with a WebGL viewport that draws meshes with the engine's own
  winding, culling and matrix conventions;
* a **character builder** — assemble a character from body, face, hair, armor
  and weapons, with the game's own attachment sockets and animations;
* a **MapEditor** — draws any world map the way the game does, layer by
  layer, and lets you inspect and change its art;
* a **texture-swap modding flow** — preview live in the viewer, install
  through one deliberate, backed-up, uninstallable command;
* a **Blender addon** that imports and exports the game's `.c3` meshes
  **byte-exactly** — the round trip is a gate in the test suite, not a claim;
* the format parsers all of that is built on (`core/`), standard library
  only, importable on their own.

Everything is Python 3 and the standard library, plus two packages (`pillow`,
`numpy`). There is no build step, no service to install, and nothing that
phones home.

```
git clone https://github.com/grundlbutter/COMod
cd COMod
pip install -r requirements.txt
py -3 tools/health.py --bootstrap      # one-off, ~8 minutes
"Asset Viewer.cmd"
```

The viewer finds your game install by itself, opens
<http://127.0.0.1:8731>, and runs a health check on first launch that tells
you about anything missing — with the exact command to fix it.

**You need your own copy of the game.** This repository ships no game assets,
by design: no textures, no meshes, no maps, no archives. It reads them out of
your install, read-only.

---

## The boundaries

These are not aspirations; they are enforced in code and in the test suite.

* **The game install is READ-ONLY.** Every tool reads from it. Exactly one
  writes to it — `tools/comod.py install`, which you have to invoke
  deliberately, which takes a backup and writes a manifest first, and which
  has an `uninstall` that puts everything back. Nothing else, ever. The Asset
  Viewer stages mods into `mods/stage/` and shells out to `comod.py` for the
  actual write.
* **Nothing here talks to a game server.** There is no network client in this
  repository at all: the only sockets any tool opens are the viewer's own
  HTTP server, bound to `127.0.0.1` and nothing else.
* **No game process is read, launched, or debugged.** These tools work on the
  files on disk. The game does not need to be running, and nothing here
  attaches to it if it is.

## Modding a live game may get you banned

The modding flow edits the client's art files on *your* disk. On an online
server, client modification may be against the rules of whatever server you
play on — check before you install a mod, use `comod.py uninstall` before
assuming you are clean, and understand that a file-integrity check on the
server's side can see a modified client. That risk is yours.

---

## Security — worth two minutes before you run the viewer

The Asset Viewer binds `127.0.0.1` only, and that is test-enforced. **Public
Wi-Fi is therefore not the threat** — nobody on the network can reach it.

The threat model is a tab in your own browser. Any page on any origin can
make *your* browser POST to `127.0.0.1:8731`, and the viewer is an admin UI
for your game install. Two independent layers deal with that:

* **Path confinement** (`core/safepath.py`) — every join of an untrusted path
  onto a trusted root is checked to land inside it. Covers `..`, absolute
  paths, drive letters, UNC paths, Windows reserved device names like `NUL`,
  and symlinks pointing out of the root.
* **CSRF protection** — state-changing POSTs need a per-run token, and a
  request whose `Origin`/`Referer` is foreign is refused whatever else it
  carries. The token is new on every start and never written to disk.

Both layers are covered by the test suite (`SafePath`, `Csrf`,
`ConfinementIsActuallyWired` in `tools/test_viewer.py`).

Still, until you have a reason to trust every tab you have open: **shut the
viewer down when you are not using it.**

> **If you script the viewer:** POSTs need the token. Read it from
> `GET /api/token` and send it as the `X-CO-Token` header. It is also printed
> in the server's startup log. The browser UI handles this automatically.

Known and accepted: HTTP 500 responses include tracebacks. Intentional in a
dev tool; it does leak local path layout to anything that can reach the port
(which is loopback only).

### Protecting your own identifiers

`tests/test_sanitization.py` fails the build if personal data is about to be
published — names, absolute user paths, hardcoded install paths. The
committed list protects the original author, as *hashes*.

**Your identifiers must not be added to that list, hashed or otherwise.**
Names are short and guessable, so a published hash is not a shield: it is a
verification oracle that anyone can test guesses against, and a wordlist
cracks a four-letter handle instantly.

Instead, keep your own list locally. One command:

```bash
py -3 tests/test_sanitization.py --init-local
```

That reads `login_history.json` from your install and writes
`.sanitize-local`, which is gitignored. It prints **only counts**, never the
names — so it is safe to run with someone watching your screen. Then check
the tree as usual before you open a pull request:

```bash
py -3 tests/test_sanitization.py
```

Findings from your local list say *where* the identifier appeared and never
*what* it was, so a failing run is safe to paste into a chat.

---

## Prerequisites

Verified by parsing every `.py` in the repository for imports and subtracting
the standard library. The list really is this short.

| | Needed for | Notes |
|---|---|---|
| **Python 3.11+** | everything | Developed and verified on **3.14.6**. Invoked as `py -3` on Windows. |
| **Pillow** | **required** | `pip install pillow`. Every texture the viewer shows is decoded by the pure-python `core/dds.py` and re-encoded to PNG by Pillow. Also writes staged `.dds` files. Tested with 12.3.0. |
| **numpy** | optional, strongly recommended | `pip install numpy`. Browsing works without it (there is a pure-python path for everything, just slower). **Required** to generate thumbnails — `tools/thumbs.py` is a numpy software rasteriser. Tested with 2.5.1. |
| **git** | cloning | Also used by `tests/test_sanitization.py` to enumerate publishable files. |
| **Blender 4.2+** | the addon only | Nothing else needs it. Developed against 5.2. |

```
pip install -r requirements.txt
```

**Nothing else is required.** In particular: no browser driver (the viewer is
a plain local web app), no C compiler, no 7-Zip, no Node.

---

## Quick start

```
git clone https://github.com/grundlbutter/COMod
cd COMod
pip install -r requirements.txt
```

### Build the derived data — once, about 8 minutes

```
py -3 tools/health.py --bootstrap
```

`out/` is generated and gitignored, so a fresh clone starts empty. Most of
what goes in it is optional, but one part is not: **the `.wdf` archives store
a 64-bit hash of each filename, not the filename.** Until those names are
recovered the catalogue can only see the loose files on disk (~53,000 in a
typical install) and none of the ~25,000 inside the archives. `--bootstrap`
runs the four generators in the order they depend on each other:

| | | |
|---|---|---|
| `tools/wdf_recover.py` | ~8 min | recovers the archived filenames (24,426 of 24,757 in the reference install) |
| `tools/wdf_names.py` | 20 s | the smaller name table `coassets.py` loads by default |
| `tools/meshtex.py --coverage` | 20 s | which texture belongs to which mesh — **must run after `wdf_recover`** |
| `tools/effects.py --linkage` | 7 s | weapon/action → 3D effect |

The ordering is load-bearing: `meshtex.py` caches a mesh index built from
whatever name tables existed when it ran, so running it first silently gives
you a half-sized index that looks perfectly healthy. The health check reports
which artefacts are missing and what builds each.

Then either double-click **`Asset Viewer.cmd`**, or:

```
py -3 tools/coviewer.py
```

On first run it will:

1. **Find your game install** and tell you how it found it.
2. **Check everything else** — Python version, Pillow, numpy, the required
   game files, the derived data above — and show anything missing with the
   command that fixes it.
3. **Ask** whether you want thumbnails generated. It never starts that by
   itself; see below.

Check without a browser at any time:

```
py -3 tools/coviewer.py --health        # or --doctor, same thing
py -3 tools/coviewer.py --health --json
```

The report is also written to `out/health.json`.

### If it cannot find your install

The viewer still starts and serves a setup page that lists everywhere it
looked and takes a path, which it validates and remembers. Or set it from the
command line:

```
py -3 core/coroot.py                                   # where is it now?
py -3 core/coroot.py --search                          # everywhere it looked
py -3 core/coroot.py --set "D:\Games\Conquer Online"
```

The install root is resolved in this order, and every answer records **how**
it was found:

1. `--root DIR` passed to any tool
2. the `CO_ROOT` environment variable
3. `.co-root` in the repository (gitignored), then
   `%APPDATA%\co-client-re\config.json`
4. auto-discovery: the conventional paths (`Program Files`,
   `Program Files (x86)`, `Games`, drive roots — on every drive letter
   present, including any folder there whose name mentions conquer), then the
   **Windows uninstall registry**, then Steam library folders
5. otherwise, a failure that names everything it tried

A candidate is only accepted if it *contains* `c3.wdf`, `data.wdf`, `ini/`
and `bin/64/`. A path is never trusted because of its name.

### Thumbnails are opt-in

A fresh clone has no thumbnails. Generating the full set is **~637 MB** and
takes anywhere from about four minutes on a fast many-core desktop to
**15–30 minutes or more** on a laptop. That is not something to start on
someone's behalf, so the viewer asks, and remembers the answer.

The prompt offers:

* **Meshes only** — ~5,000 model thumbnails, 124 MB, about a third of the
  time. This is the half that matters: it is what the character builder and
  model mode use. Recommended.
* **Everything** — adds ~67,000 texture thumbnails, 486 MB. Most of the time
  and nearly all of the disk.
* **Not now**, or **not now and stop asking**.

Declining leaves a completely working viewer — assets without a thumbnail
show a placeholder tile and everything else is unaffected. You can start it
later from the **Health & thumbnails** button, or on the command line:

```
py -3 tools/thumbs.py --all --resume                # meshes
py -3 tools/thumbs.py --all --textures --resume     # everything
py -3 tools/thumbs.py --all --textures --dry-run    # costs, without doing it
```

Generation is incremental and content-hash resumable: an interrupted run
continues where it stopped rather than starting over, and a re-run when
nothing has changed takes seconds. `--jobs N` trades wall time for
responsiveness; `--limit N` renders a taste of it first.

---

## Entry points

### The launcher

**`Asset Viewer.cmd`** — double-clickable, and safe to run from anywhere (it
`cd`s to its own directory first). Runs `tools/coviewer.py` on
<http://127.0.0.1:8731>: the asset browser, the WebGL model viewport, the
character builder, the **MapEditor** (`/mapedit`), and the texture-swap flow
that previews live and installs through `comod.py`.

### The Blender addon

```
py -3 tools/build_addon.py          # -> build/io_scene_c3.zip
```

Then Blender → Edit → Preferences → Add-ons → ▾ → Install from Disk. Imports
and exports `.c3` meshes **byte-exactly** — that round trip is a gate in the
test suite. The addon finds the game install the same way everything else
does; its preference is pre-filled from auto-detection. Details in
`docs/blender_addon.md`.

### The main command-line tools

Every one of these takes `--root DIR` and otherwise auto-detects.

| Tool | What it does |
|---|---|
| `core/coroot.py` | Where is the game installed, and how was that decided. |
| `tools/health.py` | The prerequisite check, and `--bootstrap`. |
| `tools/comod.py` | The modding workbench — stage, diff, install (with backup + manifest), uninstall. **The only sanctioned writer to the game install.** |
| `tools/thumbs.py` | The thumbnail renderer. Pure numpy; matches the viewer's own camera. |
| `core/wdf.py` | Read the `.wdf` archives: list, stat, extract. |
| `core/c3phy.py` | Parse `.c3` `PHY`/`PHY4` meshes; export OBJ. |
| `tools/c3write.py` | Re-serialise them, byte-exactly. |
| `tools/inidb.py` | Load and profile `$ROOT/ini/`, the client-side game database. |
| `core/dmap.py` / `tools/mapindex.py` | World maps, and every piece of art that belongs to one. |
| `tools/models.py`, `tools/builder.py`, `tools/catalog.py` | The catalogues behind the viewer: models, character slots, browsing taxonomy. |
| `tools/anim.py`, `tools/attach.py`, `tools/effects.py` | Action animation, socket attachment, and the weapon/action → effect linkage. |
| `tools/terrain.py` | A `.DMap` cell grid → a mesh in the shape `gl.js` already draws, plus a ground texture. |
| `tools/puzzle.py` | Where a map's painted background sits on its cell grid — one cell is a 64×32px isometric diamond. `--verify` checks the rule against the whole corpus; `docs/ground_art.md` has the evidence. |
| `tools/mapedit.py` | **The MapEditor's model.** Per-layer map tiles you can toggle, hit testing that alpha-tests a sprite, and a verdict on which side of the install's `integrity.json` anything falls. `--list` shows every map and the state of its art; `docs/mapeditor.md` has the rest. |
| `tools/scene.py` | **The scenery layers.** Places `map/Scene/*.scene` objects and `COVER` sprites, and applies the passability a scene carries. `--verify` over the corpus; `docs/map_scenery.md` has the evidence. |
| `tools/wdf_recover.py`, `tools/wdf_names.py` | Recover the filenames behind the hashes in the `.wdf` archives. |
| `core/tpd.py` | Read NetDragon "DatPkg" archives — a plaintext `.tpi` index beside a `.tpd` payload. Some community clients ship these instead of `.wdf`. |
| `tools/assetdiff.py` | What does another client's asset set add or change, against a baseline install. Extracts only what is genuinely new. |
| `tools/garment_recover.py` | Recover garment names by numeric-id corroboration — an id that hits in two sibling slots (`c3/mesh/<id>.c3` + `c3/texture/<id>.dds`) is certain, where either slot alone is mostly birthday collisions. `docs/assets.md` §6 has the arithmetic. |
| `tools/apply_recovered_names.py` | Give the extracted unnamed files their recovered names. Dry-run by default; `--apply` moves files and rewrites the manifest. |
| `core/colibrary.py` / `tools/colibrary.py` | Per-server views over a COmmunity Library checkout — resolve linkages the way a given client would. Reads a library; does not contain one. |

---

## Tests

```
py -3 tools/test_viewer.py                     # viewer, DDS, meshes, terrain,
                                               #   scenery, the isometric camera,
                                               #   path confinement and CSRF
py -3 tests/test_sanitization.py               # no PII, no machine-specific paths
py -3 tests/test_sanitization.py --init-local  # set up your own identifier list
                                               #   first; see "Protecting your
                                               #   own identifiers"
py -3 tests/test_structural.py                 # mesh structural edits
py -3 tests/test_roundtrip.py                  # byte-exact PHY round trip, whole corpus
```

The Blender gates need Blender:

```
blender --background --python tests\test_blender_roundtrip.py
blender --background --python tests\test_addon_install.py
```

Suites that need the game install skip cleanly when it is absent — CI runs
them on a runner with no game.

---

## Layout

```
core/         COre — the shared foundation: install discovery, path safety,
              and the asset/map formats. Standard library only. See core/README.md
tools/        the tools — importable modules with CLIs
tools/webui/  the viewer's front end (vendored; no CDN, no external fonts)
blender/      the Blender addon (vendors its format modules from core/ and tools/)
tests/        round-trip gates, structural tests, the sanitization check
docs/         format specs and findings
out/          GENERATED. Gitignored. ~637 MB once thumbnails exist.
mods/         your working mod tree. Gitignored (it holds game art).
```

Anything under `core/` is imported by its plain name (`import coroot`).

This repository is **extracted from a larger private development tree** where
the reverse-engineering work happens; findings land there first and are
published here. That is also why a few documents reference files that are not
part of this repository — each of those carries a note saying so.

## Docs

| | |
|---|---|
| `docs/viewer.md` | The Asset Viewer, in detail |
| `docs/modding.md` | Formats, and how to actually change something |
| `docs/blender_addon.md` | Install and use the addon |
| `docs/mapeditor.md` | **The MapEditor** — the layers, the tiling, the inspector, and exactly what the install's `integrity.json` does and does not cover |
| `docs/ground_art.md` | **Where a map's painted ground art sits on the cell grid** — the `.pul` placement rule, exact on 131 of 132 maps |
| `docs/map_scenery.md` | **The layers on top of it** — scene objects, cover sprites, background planes, the passability a scene carries, and the fixed isometric camera the art forces |
| `docs/thumbnails.md` | The thumbnail renderer |
| `docs/assets.md`, `docs/gamedata.md` | The archives and the ini database |
| `docs/meshtex.md` | Which texture belongs to which mesh |
| `docs/animation.md`, `docs/attachment.md`, `docs/effects.md` | Action animation, sockets, 3D effects |
| `docs/appearance_ids.md` | How the game numbers bodies, faces and gear |

Every format spec states what is **verified** against real files and what is
**inferred**. That distinction is load-bearing — believe the verified parts.

## License

**MIT.** See [`LICENSE`](LICENSE). Use it, fork it, ship it, sell it — the
only obligation is to keep the copyright notice with the copy.

MIT rather than a copyleft licence because this is a library other people's
modding tools are meant to build on, and the point is to be easy to build on.

**What that covers and what it does not.** The licence covers the code in this
repository and nothing else. It grants you no rights whatsoever in Conquer
Online's assets, tables or client binaries — those belong to their owners, and
this tool ships none of them. Every path it reads points into an install you
already have. The separation is not a promise, it is mechanical: `TOOLS`,
`CORE` and `DOCS` in `tools/extract_comod.py` name what may be published, and
`tests/test_sanitization.py` refuses a build that carries a personal
identifier or a hardcoded install path.

## Not for use against live servers, and not for cheating

These tools read the files of a client on your own machine. Nothing here
connects to a server, reads game memory, or automates play — and nothing here
should be turned into something that does against a service you do not
operate.
