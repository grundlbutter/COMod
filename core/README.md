# COre

The shared foundation under **COMod** (modding tools), **COmpanion**
(quality-of-life tools) and **VibeCo** (the open client). Install discovery,
path safety, and the Conquer Online asset and map formats.

**Not the place to start.** If you arrived here first, you probably want
VibeCo — the open client and the protocol work. COre is the layer underneath,
and on its own it does nothing you can look at.

## Why this exists

`coroot` is imported by 34 files, `coassets` by 21, `c3phy` by 18 — spread
across all three of the other projects. A three-way split does not divide that
code; every side needs it. So it gets its own home rather than being copied
three times and drifting.

See `docs/repo_split.md` in the main tree for the full reasoning.

## What is in it

| Module | Does |
|---|---|
| `coroot` | Finds the game install: `--root` → `CO_ROOT` → saved config → autodiscovery. **Never hardcode an install path**; ask this. |
| `safepath` | `confine(root, logical)` — join an untrusted relative path onto a trusted root, or refuse. The only sanctioned way to do that anywhere in the project. |
| `tqhash` | The TQ filename hash, for looking names up in WDF archives. |
| `wdf` | WDF archive reader. |
| `dds` | DDS / BC texture decoding. Uses numpy if present, correct without it. |
| `c3phy` | C3 / PHY mesh parsing. |
| `dmap` | `.DMap` map grids, passability and row checksums. |
| `tqdat` | The TQ File Cipher and the encrypted `ini/*.dat` tables official clients ship (itemtype.dat, Monster.dat). |
| `inidat` | **Which** treatment an `ini/*.dat` is under, by test rather than by filename — RSA (reads), TQ stream (reads), the 12-byte block cipher (tail only; key unknown), codepage-destroyed RAR (refuses with the reason), or never encrypted. `tqdat` knows one cipher; 7878 ships five. |
| `dbcshadow` | Is this `ini/*.ini` shadowed by a compiled `.dbc` twin on **this** base? From 5517 on the client reads the twin, so reading the ini raises an error naming it. Resolved per path, per call — never at import time. |
| `coassets` | The asset VFS — loose file, then overlay, then archive, in the order the game itself resolves them. |

Standard library only. `numpy` is an optional speed-up, never a requirement.

## Two properties that are enforced, not just intended

**The set is closed under its own imports.** `coassets` needs `coroot`,
`safepath`, `tqhash`, `wdf`, `tqdat` and `dbcshadow`;
`dmap` needs `coroot`; everything else imports
only the standard library. Nothing here reaches up into `tools/`, `client/` or
`capture/`. `tools/test_viewer.py::CoreBoundary` fails if that changes — which is
the property that makes COre extractable at all, and exactly the property that
rots silently without a test.

**The install is read-only.** Nothing in COre writes to a game directory.
`comod.py`, which lives in COMod, is the only writer in the whole project.

## Using it

From a checkout, the way the current tree does:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot
root = coroot.resolve().path
```

Once split into its own repository, as a pinned dependency:

```
co-core @ git+https://github.com/<owner>/COre@v0.1.0
```

Either way the import spelling is the same — `import coroot`, not
`from cocore import coroot`. `pyproject.toml` explains why, and what that costs.

## Status

Currently a **directory in the combined repository**, with its packaging and its
boundary test already in place, so extraction is a `git filter-repo --path core/`
away rather than a refactor. See `docs/repo_split.md` §5 for the order the rest
of the split should happen in.
