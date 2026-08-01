#!/usr/bin/env python3
r"""
coroot.py -- the single source of truth for *where the game is installed*.

Every tool in this repository reads assets out of one directory: the Conquer
Online client install root.  Nothing in the repo ships game data, and no two
machines put the client in the same place, so the path can never be a literal.
This module is the only thing allowed to decide it.

Resolution order (first hit wins, and every answer records *how* it was found):

    1. explicit      an argument passed in code, or ``--root`` on any CLI
    2. environment   the ``CO_ROOT`` environment variable
    3. config        ``<repo>/.co-root`` (gitignored) then the per-user config
                     at ``%APPDATA%\\co-client-re\\config.json``
    4. discovery     conventional install paths, then the Windows uninstall
                     registry, then Steam library folders
    5. failure       ``RootNotFound``, carrying the full list of what was tried

A candidate is only accepted if it *contains the files that must exist* --
``c3.wdf``, ``data.wdf``, ``ini/`` and ``bin/64/``.  A path is never trusted
because it looks right.

Nothing here writes to the game install.  The only file this module ever
writes is its own config, and only when something explicitly asks it to.

One repo-side question is also answered here: where a derived artefact
(``out/...``, gitignored, built from the install) can be *read* from.
``find_derived`` looks in this checkout first, then -- in a linked ``git
worktree`` -- in the primary checkout, so a fresh worktree inherits the
``out/`` tree it cannot have yet instead of silently starting without it.

CLI::

    py -3 core/coroot.py                  # where is it, and how was it found
    py -3 core/coroot.py --json
    py -3 core/coroot.py --search         # show every candidate and its verdict
    py -3 core/coroot.py --set "D:\\Games\\Classic Conquer 2.0"
    py -3 core/coroot.py --forget

Pure stdlib, no imports from the rest of the project: it is vendored verbatim
into the Blender addon by ``tools/build_addon.py``.
"""

from __future__ import annotations

import json
import os
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

__all__ = [
    "ENV_VAR", "CONVENTIONAL_ROOT", "REQUIRED", "RootNotFound", "Found",
    "missing_parts", "looks_like_root", "describe_root",
    "user_config_path", "repo_config_path", "config_root", "save_root",
    "forget_root", "primary_checkout", "find_derived", "DERIVED_FALLBACK_VAR",
    "iter_candidates", "discover", "search_report",
    "find", "resolve", "game_root", "default_root", "bin_dir",
    "add_root_argument", "root_from_args", "invalidate_cache",
]

#: Environment variable checked before any config file or discovery.
ENV_VAR = "CO_ROOT"

#: The most common install location.  A *starting guess* for discovery and the
#: string shown in help text -- never assumed to be correct.
CONVENTIONAL_ROOT = r"C:\Program Files\Classic Conquer 2.0"

#: What a real install must contain.  ``(relative path, kind)`` where kind is
#: "file" or "dir".  These four are what every tool in the repo actually opens:
#: the two WDF archives, the ini/ game database, and the 64-bit binaries.
REQUIRED: tuple[tuple[str, str], ...] = (
    ("c3.wdf", "file"),
    ("data.wdf", "file"),
    ("ini", "dir"),
    ("bin/64", "dir"),
)

#: Directory names an install has been seen under, or plausibly could be.
#: Only used to *generate* candidates; each is still validated.
_INSTALL_DIR_NAMES = (
    "Classic Conquer 2.0",
    "Classic Conquer",
    "ClassicConquer",
    "Conquer Online 2.0",
    "Conquer Online",
    "ConquerOnline",
)

#: Parent directories to try each of the above under, per drive.
_PARENTS = (
    "Program Files",
    "Program Files (x86)",
    "Games",
    "Program Files/Games",
    "",                       # e.g. D:\Classic Conquer 2.0
)

_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    """A resolved install root and the provenance of that answer."""

    path: Path
    #: machine token: explicit | env | repo-config | user-config |
    #: default-path | registry | steam
    source: str
    #: one human sentence naming what was consulted
    detail: str

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return f"{self.path}  ({self.detail})"

    def as_dict(self) -> dict:
        return {"path": str(self.path), "source": self.source,
                "detail": self.detail}


class RootNotFound(RuntimeError):
    """No install root could be resolved.  Carries the full search trail."""

    def __init__(self, report: Optional[dict] = None):
        self.report = report or {}
        super().__init__(self.message())

    def message(self) -> str:
        tried = self.report.get("tried") or []
        lines = [
            "Could not find the Conquer Online install.",
            "",
            "Tell the tools where it is, any one of these:",
            f"  py -3 core/coroot.py --set \"{CONVENTIONAL_ROOT}\"   "
            "(remembers it for next time)",
            "  set CO_ROOT=D:\\path\\to\\Classic Conquer 2.0",
            "  ...or pass --root \"D:\\path\\to\\Classic Conquer 2.0\" to any tool",
            "",
            "A valid install root contains: "
            + ", ".join(n + ("/" if k == "dir" else "") for n, k in REQUIRED),
        ]
        if tried:
            lines += ["", f"Searched {len(tried)} location(s):"]
            for t in tried[:24]:
                lines.append(f"  {t['path']}  -- {t['verdict']}")
            if len(tried) > 24:
                lines.append(f"  ... and {len(tried) - 24} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def missing_parts(path) -> list[str]:
    """Which of the REQUIRED entries are absent under ``path``.

    An empty list means the directory really is an install root.  This is a
    content check on purpose: a path string that merely *looks* like an install
    (right name, right drive) proves nothing, and every failure mode we have
    seen -- a moved install, a half-deleted one, a shortcut folder -- shows up
    here and nowhere else.
    """
    try:
        base = Path(path)
    except (TypeError, ValueError):
        return [n for n, _ in REQUIRED]
    if not base.is_dir():
        return [n for n, _ in REQUIRED]
    missing = []
    for rel, kind in REQUIRED:
        p = base.joinpath(*rel.split("/"))
        try:
            ok = p.is_dir() if kind == "dir" else p.is_file()
        except OSError:
            ok = False
        if not ok:
            missing.append(rel + ("/" if kind == "dir" else ""))
    return missing


def looks_like_root(path) -> bool:
    """True when ``path`` contains everything an install must contain."""
    return not missing_parts(path)


def describe_root(path) -> dict:
    """Per-requirement detail for the health check: exists, readable, size."""
    base = Path(path)
    out = {"root": str(base), "isDir": base.is_dir(), "parts": []}
    for rel, kind in REQUIRED:
        p = base.joinpath(*rel.split("/"))
        rec: dict = {"name": rel + ("/" if kind == "dir" else ""),
                     "kind": kind, "path": str(p), "exists": False,
                     "readable": False, "bytes": None, "error": None}
        try:
            rec["exists"] = p.is_dir() if kind == "dir" else p.is_file()
            if rec["exists"]:
                if kind == "file":
                    rec["bytes"] = p.stat().st_size
                    with open(p, "rb") as fh:      # prove it is readable
                        fh.read(16)
                    rec["readable"] = True
                else:
                    next(iter(p.iterdir()), None)
                    rec["readable"] = True
        except OSError as e:
            rec["error"] = str(e)
        out["parts"].append(rec)
    out["ok"] = all(r["exists"] and r["readable"] for r in out["parts"])
    return out


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def _repo_dir() -> Optional[Path]:
    """The checkout this module lives in, found by walking up for a marker.

    Walking beats ``parent.parent`` because this file is also vendored into
    the Blender addon (``blender/io_scene_c3/vendor/coroot.py``), where the
    repo root is three levels up, not one.
    """
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / "core" / "coroot.py").is_file() or (d / ".git").exists():
            return d
    return None


def repo_config_path() -> Path:
    """Gitignored, repo-local override.  One line: the path.

    Useful when one machine holds two clones pointed at two installs.  Takes
    precedence over the per-user config because it is the more specific of the
    two.
    """
    repo = _repo_dir()
    return (repo or Path.cwd()) / ".co-root"


def user_config_path() -> Path:
    """Per-user config, **outside** the repo, so it survives re-cloning."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "co-client-re" / "config.json"
    return Path.home() / ".config" / "co-client-re" / "config.json"


def _read_repo_config() -> Optional[str]:
    p = repo_config_path()
    try:
        if p.is_file():
            text = p.read_text("utf-8").strip()
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return None


def _read_user_config() -> dict:
    p = user_config_path()
    try:
        if p.is_file():
            doc = json.loads(p.read_text("utf-8"))
            if isinstance(doc, dict):
                return doc
    except (OSError, ValueError):
        pass
    return {}


def config_root() -> Optional[tuple[str, str, Path]]:
    """``(path_string, source_token, config_file)`` from config, or None."""
    v = _read_repo_config()
    if v:
        return v, "repo-config", repo_config_path()
    v = _read_user_config().get("game_root")
    if v:
        return str(v), "user-config", user_config_path()
    return None


def read_settings() -> dict:
    """The whole per-user settings document (game root, UI preferences)."""
    return _read_user_config()


def write_settings(**kw) -> Path:
    """Merge ``kw`` into the per-user config.  Returns the file written."""
    p = user_config_path()
    doc = _read_user_config()
    doc.update(kw)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
    tmp.replace(p)
    return p


def save_root(path, scope: str = "user") -> Path:
    """Remember ``path`` as the install root.  Returns the file written.

    ``scope="user"`` (default) writes the per-user config, which survives a
    fresh clone.  ``scope="repo"`` writes the gitignored ``.co-root``.
    """
    path = str(Path(path))
    invalidate_cache()
    if scope == "repo":
        p = repo_config_path()
        p.write_text(path + "\n", "utf-8")
        return p
    return write_settings(game_root=path)


def forget_root(scope: str = "all") -> list[Path]:
    """Delete the remembered root.  Returns the files actually removed."""
    invalidate_cache()
    gone = []
    if scope in ("all", "repo"):
        p = repo_config_path()
        if p.is_file():
            p.unlink()
            gone.append(p)
    if scope in ("all", "user"):
        doc = _read_user_config()
        if "game_root" in doc:
            doc.pop("game_root")
            p = user_config_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
            gone.append(p)
    return gone


# ---------------------------------------------------------------------------
# derived artifacts (repo-side, gitignored)
# ---------------------------------------------------------------------------

#: Environment kill switch: set to ``0`` (or ``off``/``no``) to make
#: ``find_derived`` look only at this checkout.  Exists so tests can simulate
#: a checkout with no derived data; never needed in normal use.
DERIVED_FALLBACK_VAR = "CO_DERIVED_FALLBACK"

_PRIMARY_UNSET = object()
_primary_checkout: object = _PRIMARY_UNSET


def primary_checkout() -> Optional[Path]:
    """The primary checkout, when this one is a linked ``git worktree``.

    Everything under ``out/`` is derived from the user's install and
    gitignored, so a fresh worktree starts without it -- and the catalogue
    tools would quietly shrink to loose files (the failure ``health.py``
    documents).  A linked worktree can instead *read* the primary checkout's
    artefacts; this answers where that is.

    Pure stdlib, no ``git`` subprocess.  In a linked worktree ``<repo>/.git``
    is a *file*, ``gitdir: <primary>/.git/worktrees/<name>``, and that
    directory's ``commondir`` file points back at the shared ``.git``; the
    primary checkout is its parent.  The answer is validated the way install
    roots are -- the candidate must actually contain ``core/coroot.py`` -- a
    path is never trusted because it looks right.  None in a primary
    checkout, a bare repo, or the vendored Blender copy.
    """
    global _primary_checkout
    if _primary_checkout is _PRIMARY_UNSET:
        _primary_checkout = _find_primary_checkout()
    return _primary_checkout  # type: ignore[return-value]


def _find_primary_checkout() -> Optional[Path]:
    repo = _repo_dir()
    if repo is None:
        return None
    dotgit = repo / ".git"
    if not dotgit.is_file():          # a directory: this *is* the primary
        return None
    try:
        first = dotgit.read_text("utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = Path(first[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = (repo / gitdir).resolve()
    try:
        rel = (gitdir / "commondir").read_text("utf-8").strip()
    except OSError:
        return None
    common = Path(rel) if Path(rel).is_absolute() else (gitdir / rel).resolve()
    primary = common.parent
    if primary == repo or not (primary / "core" / "coroot.py").is_file():
        return None
    return primary


def find_derived(rel: str) -> Optional[Path]:
    """Locate a derived artefact (an ``out/...`` path) for **reading**.

    This checkout first; failing that, the primary checkout when running in
    a linked worktree.  None when neither has it -- and a caller that would
    degrade without the artefact must then say so out loud rather than carry
    on with less (``meshtex.scan_meshes`` is the cautionary tale).  Never
    used for writing: everything that builds an artefact writes into its own
    checkout.
    """
    repo = _repo_dir()
    if repo is not None and (repo / rel).exists():
        return repo / rel
    if os.environ.get(DERIVED_FALLBACK_VAR, "").strip().lower() in ("0", "off", "no"):
        return None
    primary = primary_checkout()
    if primary is not None and (primary / rel).exists():
        return primary / rel
    return None


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _drives() -> list[str]:
    if os.name != "nt":
        return []
    out = []
    for letter in string.ascii_uppercase:
        d = f"{letter}:\\"
        try:
            if os.path.isdir(d):
                out.append(d)
        except OSError:
            pass
    # C: first, then the rest in order -- the common case should cost nothing.
    out.sort(key=lambda d: (d[0] != "C", d))
    return out


def _conventional_candidates() -> Iterator[tuple[Path, str, str]]:
    """Cheapest first: the handful of paths an installer would pick."""
    seen = set()
    drives = _drives() or [""]
    for drive in drives:
        for parent in _PARENTS:
            for name in _INSTALL_DIR_NAMES:
                if drive:
                    p = Path(drive) / parent / name if parent else Path(drive) / name
                else:
                    p = Path(parent) / name if parent else Path(name)
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                yield p, "default-path", f"conventional install path {p}"


def _registry_candidates() -> Iterator[tuple[Path, str, str]]:
    r"""The Windows uninstall registry.

    The client ships ``unins000.exe`` (Inno Setup), so it registers itself
    under ``...\CurrentVersion\Uninstall\*`` with an ``InstallLocation``.  We
    read every entry -- name-matching first, then any entry at all whose
    InstallLocation validates -- because the display name is not something we
    control.
    """
    try:
        import winreg                                   # noqa: PLC0415
    except ImportError:
        return
    hives = [(winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")]
    views = [0]
    for flag in (getattr(winreg, "KEY_WOW64_64KEY", 0),
                 getattr(winreg, "KEY_WOW64_32KEY", 0)):
        if flag and flag not in views:
            views.append(flag)

    named: list[tuple[Path, str, str]] = []
    other: list[tuple[Path, str, str]] = []
    seen: set[str] = set()

    for hive, hive_name in hives:
        for subkey in _UNINSTALL_KEYS:
            for view in views:
                try:
                    root = winreg.OpenKey(hive, subkey, 0,
                                          winreg.KEY_READ | view)
                except OSError:
                    continue
                with root:
                    try:
                        count = winreg.QueryInfoKey(root)[0]
                    except OSError:
                        continue
                    for i in range(count):
                        try:
                            name = winreg.EnumKey(root, i)
                            with winreg.OpenKey(root, name, 0,
                                                winreg.KEY_READ | view) as k:
                                vals = {}
                                for v in ("DisplayName", "InstallLocation",
                                          "UninstallString", "DisplayIcon"):
                                    try:
                                        vals[v] = str(winreg.QueryValueEx(k, v)[0])
                                    except OSError:
                                        vals[v] = ""
                        except OSError:
                            continue
                        display = vals["DisplayName"]
                        paths = []
                        if vals["InstallLocation"]:
                            paths.append(vals["InstallLocation"].strip('" '))
                        for v in ("UninstallString", "DisplayIcon"):
                            s = vals[v].strip('" ').split(",")[0].strip('" ')
                            if s.lower().endswith(".exe"):
                                paths.append(str(Path(s).parent))
                        hit = "conquer" in display.lower()
                        for raw in paths:
                            if not raw:
                                continue
                            p = Path(raw)
                            key = str(p).lower()
                            if key in seen:
                                continue
                            seen.add(key)
                            what = (f"{hive_name} uninstall registry entry "
                                    f"{display!r}" if display
                                    else f"{hive_name} uninstall registry key {name}")
                            (named if hit else other).append(
                                (p, "registry", what))
    yield from named
    yield from other


def _steam_candidates() -> Iterator[tuple[Path, str, str]]:
    """Steam library folders, if Steam is installed.

    Kept last and cheap: we only look at ``steamapps/common/*`` directories
    whose name mentions conquer.  No evidence this client ships on Steam; the
    branch costs nothing when Steam is absent and covers the case if it does.
    """
    libs: list[Path] = []
    try:
        import winreg                                   # noqa: PLC0415
        for hive, sub in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for val in ("SteamPath", "InstallPath"):
                        try:
                            libs.append(Path(str(winreg.QueryValueEx(k, val)[0])))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    if not libs:
        return
    roots = set()
    for lib in libs:
        roots.add(lib)
        vdf = lib / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('"path"'):
                parts = line.split('"')
                if len(parts) >= 4:
                    roots.add(Path(parts[3].replace("\\\\", "\\")))
    for r in sorted(roots):
        common = r / "steamapps" / "common"
        try:
            entries = list(common.iterdir())
        except OSError:
            continue
        for d in entries:
            if d.is_dir() and "conquer" in d.name.lower():
                yield d, "steam", f"Steam library {common}"


def iter_candidates() -> Iterator[tuple[Path, str, str]]:
    """Every discovery candidate, cheapest and most likely first."""
    yield from _conventional_candidates()
    yield from _registry_candidates()
    yield from _steam_candidates()


def search_report(explicit=None) -> dict:
    """The whole resolution attempt, written down.

    Used by the health check and by the "we could not find it" page, so the
    person in front of the screen sees exactly what was looked at rather than
    a bare failure.  Discovery stops at the first valid hit; entries after it
    are not evaluated.
    """
    tried: list[dict] = []
    found: Optional[Found] = None

    def consider(path, source, detail) -> bool:
        nonlocal found
        if path is None:
            return False
        p = Path(path)
        missing = missing_parts(p)
        rec = {"path": str(p), "source": source, "detail": detail,
               "ok": not missing, "missing": missing}
        if not missing:
            rec["verdict"] = "OK"
            tried.append(rec)
            found = Found(p.resolve() if p.exists() else p, source, detail)
            return True
        rec["verdict"] = ("does not exist" if not p.is_dir()
                          else "missing " + ", ".join(missing))
        tried.append(rec)
        return False

    if explicit:
        consider(explicit, "explicit", "path given on the command line or in code")
        if found:
            return {"found": found.as_dict(), "tried": tried}

    env = os.environ.get(ENV_VAR)
    if env:
        if consider(env, "env", f"{ENV_VAR} environment variable"):
            return {"found": found.as_dict(), "tried": tried}

    cfg = config_root()
    if cfg:
        value, source, file = cfg
        if consider(value, source, f"remembered in {file}"):
            return {"found": found.as_dict(), "tried": tried}

    for path, source, detail in iter_candidates():
        if consider(path, source, detail):
            return {"found": found.as_dict(), "tried": tried}

    # Keep the trail short and useful: paths that do not exist at all are
    # noise once there are dozens of them.
    interesting = [t for t in tried if t["verdict"] != "does not exist"]
    return {"found": None, "tried": interesting or tried[:12],
            "triedCount": len(tried)}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

_cache: dict = {}


def invalidate_cache() -> None:
    """Forget the memoised answer (after --set, or a path typed in the UI)."""
    global _primary_checkout
    _cache.clear()
    _primary_checkout = _PRIMARY_UNSET


def find(explicit=None, *, use_cache: bool = True) -> Optional[Found]:
    """Resolve the install root, or None.  Never raises, never prompts."""
    if explicit:
        p = Path(explicit)
        if looks_like_root(p):
            return Found(p.resolve() if p.exists() else p, "explicit",
                         "path given on the command line or in code")
        return None
    if use_cache and "found" in _cache:
        return _cache["found"]
    rep = search_report()
    got = rep["found"]
    out = Found(Path(got["path"]), got["source"], got["detail"]) if got else None
    _cache["found"] = out
    _cache["report"] = rep
    return out


def last_report(explicit=None) -> dict:
    """The search report for the current process, computing it if needed."""
    if explicit:
        return search_report(explicit)
    if "report" not in _cache:
        find()
    return _cache.get("report") or search_report()


def resolve(explicit=None) -> Found:
    """Resolve or raise ``RootNotFound`` with an actionable message."""
    got = find(explicit)
    if got is None:
        raise RootNotFound(last_report(explicit))
    return got


def game_root(explicit=None) -> Path:
    """The install root as a Path, raising ``RootNotFound`` if there is none."""
    return resolve(explicit).path


def default_root() -> Path:
    """A Path that is always safe to use as a default argument value.

    Returns the discovered root when there is one, and the conventional path
    otherwise -- so importing a module never fails on a machine without the
    game, and the failure instead lands where the assets are actually opened,
    with a message naming the missing files.
    """
    got = find()
    return got.path if got else Path(CONVENTIONAL_ROOT)


def bin_dir(root=None) -> Path:
    """``$ROOT/bin/64`` -- where the game DLLs and executables live."""
    base = Path(root) if root else default_root()
    return base / "bin" / "64"


# ---------------------------------------------------------------------------
# argparse glue
# ---------------------------------------------------------------------------

def add_root_argument(parser, flag: str = "--root", **kw) -> None:
    """Add the standard ``--root`` option to any tool's ArgumentParser.

    The default is deliberately ``None``, not a resolved path: that way
    ``--help`` does not have to run discovery, and ``root_from_args`` can tell
    "the user asked for this path" apart from "nobody said".
    """
    kw.setdefault("default", None)
    kw.setdefault("metavar", "DIR")
    kw.setdefault("help", "game install root (default: auto-detect; see "
                          "CO_ROOT and `py -3 core/coroot.py --set`)")
    parser.add_argument(flag, **kw)


def root_from_args(args, attr: str = "root") -> Path:
    """Resolve the root for a CLI, honouring ``--root`` when it was given."""
    return game_root(getattr(args, attr, None))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(rep: dict) -> None:
    if rep.get("found"):
        f = rep["found"]
        print(f"install root : {f['path']}")
        print(f"found via    : {f['source']}  ({f['detail']})")
    else:
        print("install root : NOT FOUND")
    tried = rep.get("tried") or []
    if tried:
        print(f"\nchecked {rep.get('triedCount', len(tried))} location(s); "
              f"showing {len(tried)}:")
        for t in tried:
            print(f"  [{t['source']:<12}] {t['path']}")
            print(f"                 {t['verdict']}")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Locate the Conquer Online install root.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--set", metavar="DIR",
                    help="remember DIR as the install root")
    ap.add_argument("--scope", choices=("user", "repo"), default="user",
                    help="with --set/--forget: which config to write "
                         "(default: user, survives re-cloning)")
    ap.add_argument("--forget", action="store_true",
                    help="delete the remembered root")
    ap.add_argument("--search", action="store_true",
                    help="show every candidate considered and its verdict")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.forget:
        gone = forget_root(a.scope if a.scope else "all")
        for p in gone:
            print(f"forgot the remembered root in {p}")
        if not gone:
            print("nothing was remembered")

    if a.set:
        missing = missing_parts(a.set)
        if missing:
            print(f"refusing to save {a.set!r}: missing " + ", ".join(missing),
                  file=sys.stderr)
            print("A valid install root contains: "
                  + ", ".join(n + ("/" if k == "dir" else "")
                              for n, k in REQUIRED), file=sys.stderr)
            return 2
        p = save_root(a.set, a.scope)
        print(f"saved {a.set} -> {p}")

    invalidate_cache()
    rep = search_report() if a.search else None
    if rep is None:
        got = find()
        rep = {"found": got.as_dict() if got else None, "tried": []}
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0 if rep.get("found") else 1
    _print_report(rep)
    if not rep.get("found"):
        print()
        print(RootNotFound(last_report()).message())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
