# COre

The shared foundation under the COMod tools: install discovery, path safety,
and the Conquer Online asset and map formats. It is vendored into this
repository as `core/` so that a clone runs with no extra install step; it is
kept deliberately separable so it can also stand alone.

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
| `coassets` | The asset VFS — loose file, then overlay, then archive, in the order the game itself resolves them. |

Standard library only. `numpy` is an optional speed-up, never a requirement.

## Two properties that are enforced, not just intended

**The set is closed under its own imports.** `coassets` needs `coroot`,
`safepath`, `tqhash` and `wdf`; `dmap` needs `coroot`; everything else imports
only the standard library. Nothing here reaches up into `tools/`.
`tools/test_viewer.py::CoreBoundary` fails if that changes — which is the
property that keeps COre extractable, and exactly the property that rots
silently without a test.

**The install is read-only.** Nothing in COre writes to a game directory.
`tools/comod.py` is the only writer in the whole project.

## Using it

From a checkout, the way this tree does:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot
root = coroot.resolve().path
```

The import spelling is always the flat module name — `import coroot`, not
`from cocore import coroot`. `pyproject.toml` explains why, and what that
costs.
