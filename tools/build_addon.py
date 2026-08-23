r"""
build_addon.py -- vendor the pure-stdlib C3 modules into the Blender addon and
zip it.

`core/` and `tools/` are the single source of truth for the format code.  A
Blender addon has to be self-contained, so the build copies those modules into
`blender/io_scene_c3/vendor/` and rewrites their imports into package-relative
ones.  Every rewrite is asserted, so a source change that invalidates one
breaks the build loudly.

**That is not, by itself, protection against shipping a stale copy, and this
docstring used to claim it was** -- see `docs/CORRECTIONS.md`
`C-2026-08-09-comod-vendor-drift-gate`.  The vendored copies are committed, so
a refusing build leaves the previous ones in place; `coroot.py` sat 496 lines
behind its source while this file exited 1 on every run, because nothing ran
it.  What actually protects the tree is `tests/test_vendor_sync.py`, which
re-runs `vendor()` into a temp dir and diffs.  **If you add a rewrite here, or
a module to `VENDORED`, that test is what proves it landed.**

Note that the assertions only cover *declared* rewrites.  A lazy import in a
function body is not in this table and will not break the build -- it fails
inside the addon, and at two sites in `coassets`/`dbcshadow` it is swallowed
by a bare `except`.  `test_vendor_sync` walks the AST for exactly that.

    py -3 tools/build_addon.py            # vendor + zip
    py -3 tools/build_addon.py --no-zip   # vendor only (for live editing)

Output: build/io_scene_c3.zip
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
#: The shared foundation. Half the vendored modules live here now -- see
#: `core/README.md` and `docs/repo_split.md`. Blender ships its own Python and
#: cannot see this tree at all, which is why the addon vendors copies rather
#: than depending on COre like every other consumer does.
CORE = REPO / "core"
ADDON = REPO / "blender" / "io_scene_c3"
VENDOR = ADDON / "vendor"
BUILD = REPO / "build"


def _source_of(name: str) -> Path:
    """Where a vendored module actually lives, `core/` before `tools/`.

    Searched rather than hardcoded per module so that moving one more file into
    COre does not silently produce a stale vendored copy -- the failure mode
    would be an addon that works today and breaks after the split.
    """
    for cand in (CORE / name, TOOLS / name):
        if cand.is_file():
            return cand
    raise SystemExit(f"build_addon: {name} is in neither core/ nor tools/")

# module -> list of (old, new) import rewrites that MUST all apply
VENDORED = {
    "c3phy.py": [],
    # The install-root resolver.  Vendored so the addon locates the game the
    # same way every CLI does, instead of carrying its own hardcoded path.
    "coroot.py": [],
    "c3write.py": [
        # Both bootstrap lines go: inside the addon these are a package, so
        # there is no sys.path to fix up and nothing named `core/` to find.
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n\n'
         'from c3phy import (',
         'from .c3phy import ('),
    ],
    "tqhash.py": [],
    "wdf.py": [
        # `read_by_name` imports this lazily, so the unrewritten form does not
        # break the addon at import time -- it raises ImportError on the first
        # name-keyed archive read instead, which is much later and reads as a
        # missing asset rather than a build fault.
        ('        from tqhash import tq_hash  # local import; optional dependency',
         '        from .tqhash import tq_hash  # local import; optional dependency'),
    ],
    # The compiled-table reader (MESH / RSDB). Only ever imported lazily, and
    # at both call sites the failure is swallowed -- see the rewrites below --
    # so leaving it unvendored does not raise anywhere; it just quietly costs
    # the addon every compiled table on 5517+.
    "dbc.py": [],
    # Path confinement. Vendored because `coassets.locate()` calls it on every
    # lookup, so the addon would not import without it.
    "safepath.py": [],
    # The compiled-twin guard. `coassets.parse_ini()` calls it on every ini
    # read, so the addon would not import without it -- and without it the
    # addon would read 5517+ plaintext inis the client itself ignores.
    "dbcshadow.py": [
        # Swallowed by `except Exception: return None` -- unrewritten, every
        # RSDB path table silently reads as absent.
        ('        import dbc                                        '
         '# noqa: E402,PLC0415',
         '        from . import dbc                                 '
         '# noqa: E402,PLC0415'),
    ],
    # The TQ-cipher .dat reader. Official clients ship `ini/itemtype.dat`
    # rather than the community client's `ini/itemtype.json`, and
    # `coassets.load_items()` falls back to it, so the addon needs it to see
    # item names on the five official roots.
    "tqdat.py": [],
    # The DatPkg (.tpd/.tpi) reader. Pure stdlib, no rewrites needed. Vendored
    # since `coassets.AssetRoot` learned to discover archives by suffix
    # (Parser's _discover_archives): without it the vendored coassets fails at
    # import on `from .tpd import TpdArchive`, which is how master went red on
    # test_vendor_sync the day the discovery landed.
    "tpd.py": [],
    "coassets.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n\n'
         'import coroot                       # noqa: E402\n'
         'import dbcshadow                    # noqa: E402\n'
         'import safepath                     # noqa: E402\n'
         'import tqdat                        # noqa: E402\n'
         'from tqhash import tq_hash          # noqa: E402\n'
         'from tpd import TpdArchive          # noqa: E402\n'
         'from wdf import WdfArchive          # noqa: E402',
         'from . import coroot                 # noqa: E402\n'
         'from . import dbcshadow              # noqa: E402\n'
         'from . import safepath               # noqa: E402\n'
         'from . import tqdat                  # noqa: E402\n'
         'from .tqhash import tq_hash          # noqa: E402\n'
         'from .tpd import TpdArchive          # noqa: E402\n'
         'from .wdf import WdfArchive          # noqa: E402'),
        # `PartIni.__init__` swallows this one with `except Exception: pass`
        # and falls through to the stale plaintext ini. Unrewritten, the addon
        # silently reads the 2008-era table on every 5517+ client, with no
        # error to notice. MEASURED on 5517 through the vendored package:
        # armet 1168 appearances unrewritten vs 1909 from the twin, armor 955
        # vs 2357. This is the rewrite whose absence is least visible.
        ('        import dbc\n',
         '        from . import dbc\n'),
    ],
    "c3tex.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n\n'
         'from coassets import AssetRoot, Located, DEFAULT_ROOT, dds_info'
         '   # noqa: E402',
         'from .coassets import AssetRoot, Located, DEFAULT_ROOT, dds_info'
         '   # noqa: E402'),
    ],
}

VENDOR_INIT = '''"""
Vendored from the CO Client RE project's `tools/` directory by
`tools/build_addon.py`.  **Do not edit these files here** -- edit them in
`tools/` and re-run the build; the only difference is that their top-level
imports were rewritten to be package-relative.

Every module in this package is pure stdlib and imports no `bpy`, which is what
lets the corpus round-trip test (`tests/test_roundtrip.py`) run the exact same
code on bare system Python.
"""
'''


def vendor(dest: Path = VENDOR) -> list[str]:
    """Write the vendored tree into `dest`, returning the names written.

    `dest` is a parameter so `tests/test_vendor_sync.py` can generate into a
    temp dir and compare against the committed tree without reaching in and
    rebinding this module's globals.  Everything else should call it with no
    argument.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "__init__.py").write_text(VENDOR_INIT, encoding="utf-8")
    written = ["vendor/__init__.py"]
    for name, rewrites in VENDORED.items():
        source = _source_of(name)
        src = source.read_text(encoding="utf-8")
        for old, new in rewrites:
            if old not in src:
                raise SystemExit(
                    f"build_addon: rewrite for {name} no longer matches.\n"
                    f"  looked for:\n{old!r}\n"
                    f"  Fix the table in tools/build_addon.py.")
            src = src.replace(old, new, 1)
        rel = source.relative_to(REPO).as_posix()
        banner = (f"# VENDORED COPY -- generated by tools/build_addon.py "
                  f"from {rel}\n# Edit {rel}, not this file.\n")
        (dest / name).write_text(banner + src, encoding="utf-8")
        written.append(f"vendor/{name}")
    return written


MANIFEST = '''schema_version = "1.0.0"

id = "io_scene_c3"
version = "1.0.0"
name = "Conquer Online C3 (mesh)"
tagline = "Import and export Conquer Online PHY meshes, byte-exactly"
maintainer = "CO Client RE project"
type = "add-on"

blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]

[permissions]
files = "Read Conquer Online .c3 meshes and .wdf archives, write .c3 exports"
'''


def build_zip() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    (ADDON / "blender_manifest.toml").write_text(MANIFEST, encoding="utf-8")
    out = BUILD / "io_scene_c3.zip"
    files = sorted(p for p in ADDON.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path("io_scene_c3") / p.relative_to(ADDON))
    return out


def main(argv):
    written = vendor()
    print(f"vendored {len(written)} module(s) into "
          f"{VENDOR.relative_to(REPO)}:")
    for w in written:
        print(f"  {w}")
    if "--no-zip" in argv:
        return 0
    out = build_zip()
    size = out.stat().st_size
    print(f"\nwrote {out.relative_to(REPO)}  ({size:,} bytes)")
    print("\nInstall:  Blender > Edit > Preferences > Add-ons > "
          "(v) > Install from Disk...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
