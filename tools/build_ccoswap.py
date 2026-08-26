#!/usr/bin/env python3
r"""Build the two CCO Swap release assets.

    asset 1  CCO-Swap-<ver>.zip        code + bundled runtime + licences
    asset 2  cco-name-profile-<id>.zip the pre-built name tables

They are separate because they are different KINDS of thing. Asset 1 is this
project's own MIT code plus three redistributable runtimes. Asset 2 is a
compilation of ~24,000 asset paths recovered from strings the game client
itself ships -- not this project's to license, and the reason the repo's
one-sentence claim ("ships no game data") is true without qualification.
Bundling it into asset 1 would make it non-optional, which is exactly what
"separate opt-in artefact" rules out.

Asset 1 deliberately carries NO derived data. In particular it does not carry
the mesh<->texture index: the viewer rebuilds that in memory when it is
absent, and `py -3 tools/meshtex.py --coverage` writes it once (measured at
46 s on CCO) and it then persists. That is a cost the user pays on their own
install rather than a file extracted from someone else's.
"""
import argparse
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in ("core", "tools"):
    _d = str(REPO / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import coroot                                              # noqa: E402

#: Copied from the source tree. Everything else is skipped.
SKIP_TOP = {
    ".git", ".github", "ConquerAssets", "COmmunityLibrary", "Installed",
    "out", "__pycache__", "config", "profile", "python",
}
#: Launchers that belong to the source repo and NOT to this package. They do
#: not redirect APPDATA, so double-clicking one silently uses the settings of
#: every other copy on the machine -- the trap this package exists to avoid.
SKIP_FILES = {"Asset Viewer.cmd", "COmpanion Demo.cmd", "Companion.cmd",
              "Local Server.cmd", "Test Client.cmd"}

#: Only what the project actually requires. PySide6 alone was 643 MB of the
#: 1.1 GB source install and nothing here imports it.
KEEP_PACKAGES = ("PIL", "numpy", "numpy.libs")
#: Trimmed from the stdlib: the CPython test suite, the Tk bindings (the UI
#: is a browser) and IDLE.
SKIP_STDLIB = {"site-packages", "test", "idlelib", "tkinter", "__pycache__"}


def copy_source(src: Path, dst: Path) -> int:
    n = 0
    for item in sorted(src.iterdir()):
        if item.name in SKIP_TOP or item.name in SKIP_FILES:
            continue
        if item.is_dir():
            shutil.copytree(item, dst / item.name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            n += sum(1 for _ in (dst / item.name).rglob("*") if _.is_file())
        elif item.suffix.lower() not in (".png", ".jpg", ".zip"):
            shutil.copy2(item, dst / item.name)
            n += 1
    return n


def bundle_python(py_home: Path, dst: Path) -> None:
    """A runtime that travels with the package.

    Python on the build machine was a PER-USER install: another Windows
    account had no `py` on PATH and could not read the one inside another
    user's profile. Anyone who unzips this has the same problem.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("python.exe", "pythonw.exe", "python314.dll", "LICENSE.txt"):
        p = py_home / f
        if p.is_file():
            shutil.copy2(p, dst / f)
    for p in py_home.glob("vcruntime*.dll"):
        shutil.copy2(p, dst / p.name)
    for p in py_home.glob("python3*.dll"):
        shutil.copy2(p, dst / p.name)
    shutil.copytree(py_home / "DLLs", dst / "DLLs",
                    ignore=shutil.ignore_patterns("__pycache__"))
    lib = dst / "Lib"
    lib.mkdir(exist_ok=True)
    for item in (py_home / "Lib").iterdir():
        if item.name in SKIP_STDLIB:
            continue
        if item.is_dir():
            shutil.copytree(item, lib / item.name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, lib / item.name)
    sp = lib / "site-packages"
    sp.mkdir(exist_ok=True)
    src_sp = py_home / "Lib" / "site-packages"
    for name in KEEP_PACKAGES:
        p = src_sp / name
        if p.is_dir():
            shutil.copytree(p, sp / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
    # The licence metadata for what we redistribute. Without these the zip
    # ships three projects with no attribution.
    for p in src_sp.glob("*.dist-info"):
        if p.name.split("-")[0].lower() in ("pillow", "numpy"):
            shutil.copytree(p, sp / p.name)


def third_party_text(py_home: Path) -> str:
    return """\
# What travels inside this package, and under whose terms

CCO Swap is MIT (see LICENSE). It bundles three other projects so that it
runs on a machine with no Python installed. Their licences are included in
full at the paths named below; nothing here supersedes them.

    python/LICENSE.txt
        CPython 3.14.6 -- Python Software Foundation License, version 2.
        The interpreter, its standard library, and the DLLs beside it.

    python/Lib/site-packages/pillow-*.dist-info/
        Pillow -- MIT-CMU licence. Decodes and writes the image formats the
        Asset Viewer displays and the staging flow produces.

    python/Lib/site-packages/numpy-*.dist-info/
        NumPy -- BSD 3-Clause. The thumbnail renderer is a numpy software
        rasteriser.

The CPython standard library was trimmed: its own test suite, the Tk
bindings and IDLE are not included. Nothing was modified.

# What is NOT in this package

No game data. Every path this tool reads points into a Conquer Online
install you already have; it ships no assets, no archives and no tables.

The pre-built name profile is a SEPARATE download. It is a compilation of
asset paths recovered from strings the game client itself ships -- not this
project's to license, and it carries its own TERMS.md stating so. Without
it the tool still runs; it will search for those names itself, which is
slow, or browse loose files only.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="a COMod checkout")
    ap.add_argument("--python-home", required=True)
    ap.add_argument("--profile", required=True, help="the .zip from profilepack pack")
    ap.add_argument("--out", required=True, help="directory for the two assets")
    ap.add_argument("--version", default="1.0.0")
    a = ap.parse_args()

    src, out = Path(a.source), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stage = out / "_stage" / "CCO Swap"
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)

    n = copy_source(src, stage)
    print("  source files      %d" % n)

    bundle_python(Path(a.python_home), stage / "python")
    print("  runtime bundled   %.0f MB"
          % (sum(f.stat().st_size for f in (stage / "python").rglob("*")
                 if f.is_file()) / 1e6))

    # The launcher lives in the repo so a release is reproducible from a
    # checkout. It was hand-assembled for v1.0.0, which is exactly the state
    # this file exists to end.
    launcher = REPO / "dist" / "ccoswap"
    for f in ("launch.py", "CCO Swap.cmd"):
        src_f = launcher / f
        if not src_f.is_file():
            raise SystemExit("missing launcher file: %s" % src_f)
        shutil.copy2(src_f, stage / f)

    cfgdir = stage / "config" / "co-client-re"
    cfgdir.mkdir(parents=True)
    io.open(cfgdir / "config.json", "w", encoding="utf-8").write(json.dumps({
        "ui": {"ui_mode": "cco"},
        # coroot's, never a copy. tests/test_sanitization.py refuses a
        # drive-rooted literal naming anything coroot resolves -- and the
        # deeper reason is the same one that refusal exists for: a second
        # copy of this path is what keeps saying the old one after a rename.
        "game_root": coroot.CONVENTIONAL_ROOT,
        "game_kind": "cco",
    }, indent=2))

    io.open(stage / "THIRD-PARTY.md", "w", encoding="utf-8").write(
        third_party_text(Path(a.python_home)))
    (stage / "profile").mkdir()
    io.open(stage / "profile" / "PUT-THE-PROFILE-HERE.txt", "w",
            encoding="utf-8").write(
        "Drop the cco-name-profile-*.zip from the Releases page into THIS\n"
        "folder, then run 'CCO Swap.cmd'. It is applied automatically and\n"
        "verified against your own install on first run.\n"
        "\n"
        "Without it the tool still works. It will not be able to name the\n"
        "assets inside the .wdf archives, because those store a hash of each\n"
        "filename rather than the name, so grids show what is on disk loose\n"
        "and little else.\n")

    zip1 = out / ("CCO-Swap-%s.zip" % a.version)
    with zipfile.ZipFile(zip1, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, Path("CCO Swap") / f.relative_to(stage))
    print("  asset 1           %s  %.1f MB" % (zip1.name, zip1.stat().st_size / 1e6))

    prof = Path(a.profile)
    # Named from the profile's OWN manifest, not by editing its filename.
    # The filename version was not idempotent: feeding it an already-named
    # asset produced "cco-name-profile-name-profile-...", because the answer
    # depended on the shape of the input rather than on what the file IS.
    with zipfile.ZipFile(prof) as z:
        man = json.loads(z.read("manifest.json").decode("utf-8"))
    base = man.get("base_id") or "unknown"
    producer = man.get("producer") or "unknown"
    # base_id is "<kind>-<fingerprint>"; the asset name puts the kind first
    # so a reader can tell at a glance which client it belongs to. Splitting
    # rather than prefixing avoids "cco-name-profile-cco-...".
    kind, _, fingerprint = base.partition("-")
    zip2 = out / ("%s-name-profile-%s-%s.zip"
                  % (kind, fingerprint or base, producer))
    shutil.copy2(prof, zip2)
    print("  asset 2           %s  %.0f KB" % (zip2.name, zip2.stat().st_size / 1e3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
