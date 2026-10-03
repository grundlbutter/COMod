#!/usr/bin/env python3
r"""
coroot.py -- the single source of truth for *where the game is installed*.

Every tool in this repository reads assets out of one directory: the Conquer
Online client install root.  Nothing in the repo ships game data, and no two
machines put the client in the same place, so the path can never be a literal.
This module is the only thing allowed to decide it.

Resolution order (first hit wins, and every answer records *how* it was found):

    1. explicit      an argument passed in code, or ``--root`` on any CLI
    2. environment   the ``CO_ROOT`` environment variable.  Set but not a
                     valid install is a **refusal** (`RootNotHonoured`), never
                     a fall-through to step 3 -- naming a base and being
                     answered about a different one is worse than no answer.
    3. config        ``<repo>/.co-root`` (gitignored) then the per-user config
                     at ``%APPDATA%\\co-client-re\\config.json``
    4. discovery     conventional install paths, then the Windows uninstall
                     registry, then Steam library folders
    5. failure       ``RootNotFound``, carrying the full list of what was tried

A candidate is only accepted if it *contains the files that must exist* --
the asset archives (``c3.wdf``/``c3.tpd``, ``data.wdf``/``data.tpd``) and
``ini/`` (see `REQUIRED`, and the notes there about the ``bin/64/`` entry
that used to be in it and refused every client but one, and about the TPD
alternatives that keep 7878 from being the next).  A path is never trusted
because it looks right.

Nothing here writes to the game install.  The only file this module ever
writes is its own config, and only when something explicitly asks it to.

Two repo-side questions are also answered here, both about derived artefacts
(``out/...``, gitignored, built from the install):

*Where can one be read from?*  ``find_derived`` looks in this checkout first,
then -- in a linked ``git worktree`` -- in the primary checkout, so a fresh
worktree inherits the ``out/`` tree it cannot have yet instead of silently
starting without it.

*Which install does one belong to?*  A derived index is only valid for the
client it was built from, and this box holds **33** (measured
2026-08-29; the docstring said *eight* from 2026-08 until then, which is
retest row 9 -- a count in the most-read docstring here, wrong on import).
``base_id`` names
that client and ``find_derived`` resolves per-base trees (`PER_BASE`) inside
``out/indexes/<base-id>/``, so pointing the tools at another install cannot
serve the previous one's facts.  Nothing recorded this before, and it showed:
``out/dll/rtti.md`` is still titled for one client over a body describing
another.

CLI::

    py -3 core/coroot.py                  # where is it, and how was it found
    py -3 core/coroot.py --json
    py -3 core/coroot.py --search         # show every candidate and its verdict
    py -3 core/coroot.py --set "D:\\Games\\Classic Conquer 2.0"
    py -3 core/coroot.py --forget
    py -3 core/coroot.py --print WHAT     # ONE path, nothing else, for .cmd/.ps1:
                                          # root|clients|assets|library|installs|export
    py -3 core/coroot.py --print export --json   # {path, why} -- see `export_root`

Pure stdlib but for two project modules, both vendored alongside it into the
Blender addon by ``tools/build_addon.py``: ``core/verdict.py`` for the
three-state answer the override gate returns, and -- reached lazily, and only
when a user-named artefact is actually read -- ``tools/profilecheck.py``, the
verifier that gate consults.  See *the override gate*, below.


WHICH RESOLVER ANSWERS WHICH QUESTION -- added 2026-08-27
=========================================================
"Resolve it through coroot" is NOT sufficient guidance and this table is why.
These are DIFFERENT QUESTIONS and picking the wrong one returns a real,
existing, wrong path -- which then SKIPS rather than fails.

    game_root() / find()   the ONE PLAYABLE install this machine is
                           configured for            -> C:/Program Files/...
    clients_dir()          the folder of SHIPPED BUILDS (5017..7878, Zephyr)
                           -> <collection>/Clients
    assets_dir()           the COLLECTION: Clients/ + derived/ + _variants/
    installs_root()        where COMod records what it INSTALLED
    community_library()    the user-curated library

MEASURED COST, 2026-08-26: a test helper called `find()` -- a real resolver,
no literal anywhere, passing every hardcoded-path audit -- and still SKIPPED
15 METHODS on a box where the 7878 install was present, because it asked for
the playable install and the suite wanted the 7878 build in the collection.

A suite that wants a specific BUILD wants `clients_dir() / "<build>"`.
It does not want `find()`. The audit that says "no literal" cannot tell them
apart, so the audit passing is not evidence the caller is correct.
"""

from __future__ import annotations

import json
import os
import string
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterator, Optional


def _sibling(name: str, extra_dirs=()):
    """Import a project module without assuming how THIS one was imported.

    Three import styles are live in this tree and the override gate has to
    work under all three::

        import coroot                # `core/` on sys.path -- most callers
        from core import coroot      # the repo root on it -- e.g.
                                     # tests/test_backup_suffixes.py
        from .vendor import coroot   # the Blender addon's package

    A module-scope ``from verdict import ...`` resolves under the FIRST and
    under neither of the other two.  Measured, not guessed: it took
    `test_backup_suffixes` red with ``ModuleNotFoundError: No module named
    'verdict'`` -- and it did so from a test that has nothing to do with this
    gate, which is the point.  So the import is spelled through `importlib`,
    which has one spelling covering all three, and falls back to loading the
    file that sits beside this one.

    Spelled this way it is invisible to `test_vendor_sync`'s
    unrewritten-import walk, which can only see import *statements*.  The
    addon shape is covered instead by `tests/test_override_gate.VendoredAddon`,
    which imports the vendored package the way Blender does -- with neither
    `core/` nor `tools/` on `sys.path` -- and drives the gate through it.
    """
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    import importlib                                     # noqa: PLC0415
    pkg = __package__ or ""
    if pkg:
        try:
            return importlib.import_module("." + name, pkg)
        except ImportError:
            pass
    try:
        return importlib.import_module(name)
    except ImportError:
        pass
    import importlib.util                                # noqa: PLC0415
    for d in (Path(__file__).resolve().parent, *extra_dirs):
        f = Path(d) / f"{name}.py"
        if not f.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_coroot_{name}", f)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)                   # type: ignore[union-attr]
            return m
        except Exception:                                # noqa: BLE001
            continue
    return None


_verdict = _sibling("verdict")
if _verdict is None:                                     # pragma: no cover
    raise ImportError(
        "coroot's derived-override gate needs core/verdict.py and could not "
        "find it beside this file. Do not degrade to a two-state answer here: "
        "that is the defect verdict.py exists to make unrepresentable.")
Verdict, PERMIT, REFUSE = _verdict.Verdict, _verdict.PERMIT, _verdict.REFUSE

__all__ = [
    "ENV_VAR", "CONVENTIONAL_ROOT", "REQUIRED", "RootNotFound",
    "RootNotHonoured", "env_refusal", "Found",
    "missing_parts", "looks_like_root", "describe_root",
    "user_config_path", "repo_config_path", "config_root", "save_root",
    "forget_root", "primary_checkout", "find_derived", "DERIVED_FALLBACK_VAR",
    "installs_root", "set_installs_root",
    "CLIENT_RELPATHS", "ClientBinary", "client_binaries",
    "install_root_of",
    "derived_overrides", "derived_override", "set_derived_override",
    "broken_derived_overrides", "VERIFIED_DERIVED", "is_verifiable",
    "override_verdict", "refused_derived_overrides",
    "unverified_derived_overrides",
    # Re-exported from `verdict` so a caller of `override_verdict` can state
    # its `on_unknown` policy without a second import.
    "PERMIT", "REFUSE",
    "PER_BASE", "GLOBAL", "GLOBAL_EXCEPTIONS", "UndeclaredDerived",
    "INDEX_ROOT", "base_fingerprint",
    "DERIVED_ROOT_VAR", "derived_root", "derived_root_is_overridden",
    "base_id", "derived_rel", "derived_path", "declare_kind", "KINDS_KEY",
    "forget_kind", "declared_kinds",
    "iter_candidates", "discover", "search_report",
    "find", "resolve", "game_root", "default_root", "bin_dir",
    "binaries", "THIRD_PARTY_BINARIES",
    "add_root_argument", "root_from_args", "invalidate_cache",
]

#: Environment variable checked before any config file or discovery.
ENV_VAR = "CO_ROOT"

#: The most common install location.  A *starting guess* for discovery and the
#: string shown in help text -- never assumed to be correct.
CONVENTIONAL_ROOT = r"C:\Program Files\Classic Conquer 2.0"

#: What a real install must contain.  ``(relative path, kind)`` where kind is
#: "file" or "dir".
#:
#: **`bin/64/` used to be here and was wrong.** It was described as "what
#: every tool in the repo actually opens", and nothing opens it -- a grep for
#: it across every `.py` finds only this table and its own error message. It
#: is an artefact of the one install this project started from: measured over
#: the official lineage, *none* of 5017, 5065, 5165, 5517 or 6090 has it, and
#: neither does Zephyr. So it rejected every client except the one it was
#: written from, which is the third gate this project has had tuned to that
#: install and the third to refuse a legitimate client.
#:
#: What is left is what a client genuinely cannot work without and what the
#: tools genuinely read: the asset archives and the ini/ database.
#:
#: **An entry may list alternatives separated by ``|``, and an alternative
#: may join co-required files with ``+`` -- any one alternative, ALL of its
#: parts.**  ``|`` cannot appear in a Windows filename so that split is
#: always safe; ``+`` can, but no entry here names a file containing one.
#: Added 2026-08-10 for 7878, which ships ``c3.tpd`` (1,076,058,840 bytes)
#: and ``data.tpd`` (896,279,080) and **no ``.wdf`` archives at all** -- a
#: later archive format whose grammar `core/tpd.py` already reads, not a
#: broken install.
#:
#: **The TPD alternative requires the PAIR** (``.tpd`` payload + ``.tpi``
#: index) because the two always ship together -- measured on 7878
#: (``c3/c31/data/data1``, all paired) and Zephyr (both pairs) -- and a
#: ``.tpd`` with no ``.tpi`` is a payload with no index, which a
#: marker-only check would admit as a complete install.  A check that
#: passes because it cannot express the distinction is the defect class
#: `docs/quickfix_false_claim_sweep_2026-08-10.md` indexes.
#:
#: **A root is a SET of archives, each with its own reader -- not a WDF
#: root or a TPD root.**  Zephyr carries ``c3.tpd``/``c3.tpi`` AND five
#: ``garments*.wdf`` in one install.  This table is a presence check and
#: handles that fine; do not rebuild it as a two-way container switch,
#: because the mixed client exists and will refuse to be either.
#:
#: Without the alternatives this table would have been the FOURTH gate
#: tuned to a narrow corpus refusing correct input -- the first three are
#: tabulated in `docs/handoff_6090_and_first_run.md` §4: this table's own
#: ``bin/64/`` (fixed `b66cd5b`), `comod` refusing Zephyr via
#: `looks_like_root` (fixed `4d1fa20`), and `coassets.AssetRoot` hardcoding
#: ``ARCHIVES = ("c3.wdf", "data.wdf")`` (still open, and older than it
#: looks: `core/tpd.py` was verified against Zephyr's own TPD archives, so
#: **the reader has existed all along for a container the asset layer will
#: not open** -- that fix is scoped to `coassets.py` and is Parser's, not
#: this file's).  `C-2026-08-10-quickfix-required-tpd`.
REQUIRED: tuple[tuple[str, str], ...] = (
    ("c3.wdf|c3.tpd+c3.tpi", "file"),
    ("data.wdf|data.tpd+data.tpi", "file"),
    ("ini", "dir"),
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


class RootNotHonoured(RuntimeError):
    r"""``CO_ROOT`` was set to something that is not an install.

    **This used to fall through, and falling through is the worst available
    answer.**  Resolution tries the environment, then config, then discovery,
    so a ``CO_ROOT`` naming a path that does not validate quietly handed back
    *the configured install* -- and every tool then reported real, plausible,
    entirely wrong numbers for a base nobody was looking at.

    Measured 2026-08-09 on this machine::

        CO_ROOT="C:/definitely/not/a/real/path"  ->  patch5517-76c7f4499934

    Not hypothetical.  A quoting slip in a six-base gate audit sent four of the
    runs to the configured install; each printed a plausible pass/fail split
    under another base's name, and the only reason it was caught is that two
    bases produced byte-identical numbers.  ``--root`` is discarded outright by
    several tools (C22), which makes ``CO_ROOT`` *the* documented way to target
    a base -- so this silently invalidated base-targeted measurement in
    general, not merely that one audit.

    An explicitly requested root that cannot be honoured is an **error**, not a
    default.  Same rule as ``offsets_cache.json`` refusing a foreign cache and
    `derived_rel` raising `UndeclaredDerived`: where the honest answer is
    unavailable, raise rather than substitute a different one in silence.
    """

    def __init__(self, value, missing):
        self.value = str(value)
        self.missing = list(missing)
        super().__init__(self.message())

    def message(self) -> str:
        what = (", ".join(self.missing) if self.missing
                else "it is not a readable directory")
        return (
            f"{ENV_VAR} is set to {self.value!r}, which is not a Conquer "
            f"Online install: missing {what}.\n\n"
            f"Refusing to fall back to the configured install. {ENV_VAR} is an "
            f"explicit request for ONE base, and answering it with a different "
            f"base is how a measurement gets filed under the wrong client with "
            f"numbers that look right.\n\n"
            f"Fix the path, or unset {ENV_VAR} to use the configured install.\n"
            f"  py -3 core/coroot.py       # what is configured now\n"
            f"A valid install root contains: "
            + ", ".join(n + ("/" if k == "dir" else "") for n, k in REQUIRED))


def env_refusal() -> Optional[RootNotHonoured]:
    """The refusal owed to an unusable ``CO_ROOT``, or None.

    One predicate so `find`, `search_report` and every caller agree about what
    "set but unusable" means; two implementations of this rule is how the
    diagnostic ends up describing a root the resolver did not return.  An unset
    or empty variable is not a request and is not refused.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().strip('"')
    if not value:
        return None
    missing = missing_parts(value)
    return RootNotHonoured(value, missing) if missing else None


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
    def _present(name: str, kind: str) -> bool:
        p = base.joinpath(*name.split("/"))
        try:
            return p.is_dir() if kind == "dir" else p.is_file()
        except OSError:
            return False

    missing = []
    for rel, kind in REQUIRED:
        # any one |-alternative, ALL of its +-parts
        if not any(all(_present(part, kind) for part in alt.split("+"))
                   for alt in rel.split("|")):
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
        # Alternatives ("a|b+c"): report the first alternative whose parts
        # ALL exist, else the first alternative, under the requirement's
        # full name -- one row per requirement, not one per spelling.  For
        # a multi-part alternative the probed path is its first absent part
        # (so the row points at what is wrong), else its first part.
        def _hit(name: str) -> bool:
            q = base.joinpath(*name.split("/"))
            try:
                return q.is_dir() if kind == "dir" else q.is_file()
            except OSError:
                return False
        alts = [a.split("+") for a in rel.split("|")]
        chosen = next((a for a in alts if all(_hit(part) for part in a)),
                      alts[0])
        probe = next((part for part in chosen if not _hit(part)), chosen[0])
        p = base.joinpath(*probe.split("/"))
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


#: The capture corpus home, as an owner ruling rather than a derivation.
#: See `capture_home` for why this is a literal and why it is not under
#: ``%LOCALAPPDATA%`` any more.
_CAPTURE_HOME_DEFAULT = r"C:/Claude/capture"


def capture_home() -> Path:
    """The capture corpus HOME: ``C:/Claude/capture``.

    **Owner ruling 2026-09-14**, replacing the 2026-09-10 ruling that put this
    under ``%LOCALAPPDATA%/co-client-re/capture``. The requirement is unchanged
    and is the reason this function exists at all: captured sessions are
    CREDENTIAL-BEARING -- they hold whatever the client sent -- so they live
    OUTSIDE the checkout, never in ``out/``. ``C:/Claude/capture`` satisfies
    that: it sits BESIDE the checkout, not inside it.

    WHY ``%LOCALAPPDATA%`` WAS ABANDONED, and it is worth stating because the
    old form looks more portable and was strictly worse here. **The Claude
    desktop app is MSIX-packaged**, so every process it starts -- which is
    every seat -- gets ``%LOCALAPPDATA%`` REDIRECTED into
    ``.../Packages/Claude_<id>/LocalCache/Local/``. Two consequences,
    both measured on 2026-09-14:

      * The owner could not see the corpus. Explorer, running unpackaged,
        resolves the documented path to a directory that does not exist, while
        a seat reading the same string reaches the redirected one. *Identical
        NTFS file id at both paths proved it was one directory reached two
        ways, not two copies.*
      * **The corpus was inside a per-app cache.** An app reset or uninstall
        clears ``LocalCache``. The whole point of moving captures out of
        ``out/`` was durability, and the new home was less durable than the
        old one.

    **So an environment variable is not automatically the portable choice: it
    is portable only if it means the same thing to every process that reads
    it.** ``%LOCALAPPDATA%`` did not.

    ``$CO_CAPTURE_HOME`` overrides, for a second machine or a test. It is read
    at call time, never cached, so a test can set it and restore it.

    This raises rather than falling back to a repo path if the home cannot be
    created: a silent fallback would put credential-bearing captures back
    inside the checkout, which is exactly what this exists to prevent.
    """
    override = os.environ.get("CO_CAPTURE_HOME")
    home = Path(override) if override else Path(_CAPTURE_HOME_DEFAULT)
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            "the capture corpus home (%s) cannot be created: %s. Refusing to "
            "fall back to a repo path: captures are credential-bearing and "
            "must stay outside the checkout. Set $CO_CAPTURE_HOME to place it "
            "elsewhere." % (home, exc)) from exc
    return home


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


def _save_user_config(doc: dict) -> Path:
    """Write the per-user config ATOMICALLY.  The ONE writer of that file.

    `forget_root` used to `write_text` straight onto it, and that file holds
    every declared install kind, `installs_root`, `export_dir` and the UI
    namespace: a crash between truncate and write loses all of them, not the
    one key the caller meant to drop.  tmp-then-`replace` cannot leave a
    half-written document.

    THE TEMP NAME CARRIES THE PID BECAUSE THIS BOX IS SHARED.  A fixed
    `config.json.tmp` is a race between any two processes saving settings at
    once -- the viewer and a CLI, or two seats -- and the loser's `replace`
    publishes the winner's partial bytes.  Per-pid names cannot collide.
    """
    p = user_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    try:
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
        tmp.replace(p)
    finally:
        # A failed write leaves no litter beside the config on a shared box.
        # After a successful `replace` there is nothing left to unlink.
        try:
            tmp.unlink()
        except OSError:
            pass
    return p


def write_settings(**kw) -> Path:
    """Merge ``kw`` into the per-user config.  Returns the file written."""
    doc = _read_user_config()
    doc.update(kw)
    return _save_user_config(doc)


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
            # Through the one atomic writer: this used to `write_text` onto
            # the live file, so a crash mid-write dropped every OTHER key too.
            gone.append(_save_user_config(doc))
    return gone


# ---------------------------------------------------------------------------
# derived artifacts (repo-side, gitignored)
# ---------------------------------------------------------------------------

#: Environment kill switch: set to ``0`` (or ``off``/``no``) to make
#: ``find_derived`` look only at this checkout.  Exists so tests can simulate
#: a checkout with no derived data; never needed in normal use.
#:
#: **It is not isolation, and it was read as isolation.**  It suppresses the
#: *primary-checkout* arm of `find_derived` only; the arm above it still reads
#: ``<this checkout>/out/``, and `derived_path` still writes there.  See
#: `derived_root` for the knob that actually moves the tree.
DERIVED_FALLBACK_VAR = "CO_DERIVED_FALLBACK"

#: Environment override for the DERIVED ROOT -- the directory ``out/`` lives
#: in.  Process-scoped and inherited by children, which matters because
#: several tools build artefacts in subprocesses (`thumbs.py`): an in-process
#: patch of `_repo_dir` does not reach them and this does.
DERIVED_ROOT_VAR = "COMOD_DERIVED_ROOT"


def derived_root() -> Path:
    """The directory ``out/`` lives in: this checkout, or an override.

    Everything under ``out/`` is derived and gitignored, so WHERE it lives is
    a policy, not a fact about the repo -- and until this existed the policy
    was hardcoded to `_repo_dir`.  A test that wanted its own derived tree had
    to patch `_repo_dir`, which also moves `repo_config_path`,
    `primary_checkout` and the `profilecheck` import path, and which a
    subprocess never sees.

    THE FAILURE THIS CLOSES, MEASURED on master ``77a0dffa``: a synthetic
    install with an empty ``ini/`` fingerprints to ``e3b0c44298fc`` -- *the
    sha256 of the empty string* -- so every featureless synthetic install on
    the box keys to one namespace, ``out/indexes/unknown-e3b0c44298fc/``.
    Two unrelated installs, in two separate ``TemporaryDirectory`` trees,
    were given one ``meshtex/mesh_index.json``: the second was served the
    first's census, `provenance.verdict` said ``match`` (the stamps are
    identical, because the ids are), and the file outlived both temp trees
    because it was never in either.  A must-fire arm aimed at
    `meshtex.all_meshes` reported GREEN off that cache -- failing open, in
    the direction that says *your change is unnecessary*.

    So: isolation means a derived root of one's own.  `tests/derivedroot.py`
    is how a test asks for one, and that helper sets this variable.

    **THE VARIABLE OUTRANKS A PATCHED `_repo_dir`, AND THAT ORDER BITES.**
    Both idioms isolate a derived tree and they do not compose: the
    environment is read first, so a process that sets this while some inner
    scope has patched `_repo_dir` sends that scope's artefacts to the
    environment's tree, and the scope then cannot find what it wrote.
    MEASURED: an audit gate that set this variable in order to watch where
    other gates write reddened `tests/test_coverage_one_writer.py`, whose
    three tests are correct -- it patches `_repo_dir` and had its output
    redirected out from under it.  Pick one per process.

    Not validated and not resolved: a caller naming a derived root is naming
    a place to build, which may not exist yet.  Unset or blank means this
    checkout, exactly as before.
    """
    raw = os.environ.get(DERIVED_ROOT_VAR, "").strip()
    if raw:
        return Path(raw)
    repo = _repo_dir()
    return repo if repo is not None else Path.cwd()


def derived_root_is_overridden() -> bool:
    """True when `DERIVED_ROOT_VAR` names a derived root.

    `find_derived` asks, because an *isolated* derived root must not fall
    back to the primary checkout's: "my own tree" that reads someone else's
    answers is the contamination this closes, one directory along.
    """
    return bool(os.environ.get(DERIVED_ROOT_VAR, "").strip())


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


#: Derived trees that are **built from one install and valid only for it**.
#: A path under any of these is rewritten into ``out/indexes/<base-id>/...``
#: so two clients cannot share an answer.
#:
#: Everything not listed stays where it is, and each omission is a claim:
#:
#: * ``out/wdf/`` -- hash-to-name maps.  The key *is* the archive content, so
#:   an entry recovered from one client cannot be served for another unless
#:   the archives are the same file, which across the official lineage they
#:   are (identical md5 in all five).
#: * ``out/offsets_cache.json`` -- already refuses a foreign cache; it keys on
#:   the sha256 of the module bytes.  This is the pattern, not the exception.
#: * ``out/opcodes.json`` -- protocol, not assets.
#: * ``out/health.json``, ``out/client/``, ``out/recon/``, ``out/sessions/``,
#:   ``out/clientdiff/`` -- reports and captures, named for what they describe.
#: * ``out/dll/`` -- **KEYED, as of `comod/known-debt`.**  It is in
#:   ``PER_BASE`` below, with ``out/dll/wdf_name_recovery`` carved out as the
#:   one global exception (the TQ hash is a pure function of the filename, so
#:   that table is true in any install and keying it would strand `meshtex`
#:   and `health`).  The worked example that motivated it: `rtti.md` sat
#:   titled "Classic Conquer 2.0 game DLLs" over a body describing 6090's,
#:   because a second client overwrote it in place and nothing recorded which
#:   install either version came from.
#:
#:   This bullet previously read *"not keyed, and it should be ... left alone
#:   deliberately"*, and stayed that way **after the code was changed to key
#:   it** -- prose contradicting the list ten lines below it, on both sides of
#:   a merge, which is why neither side's diff showed it.  Corrected
#:   2026-08-09.  See ``docs/handoff_5517_base_prep.md``.
#: ``out/thumbs/servers/<name>/`` is exempt and stays global: those are
#: COmmunity Library server views, which have nothing to do with whichever
#: install happens to be configured.  They were already namespaced by hand --
#: the ad-hoc version of this key.
PER_BASE: tuple[str, ...] = (
    "out/meshtex/", "out/artcrawl/", "out/thumbs/", "out/effects/",
    "out/skins/", "out/browse/", "out/c3/", "out/ini/",
    # Static analysis of the install's own binaries. Keyed for the same
    # reason as the rest, and with a worked example of the cost: `rtti.md`
    # sat titled "Classic Conquer 2.0 game DLLs" over a body describing
    # 6090's, because a second client overwrote it in place and nothing
    # recorded which install either version came from.
    "out/dll/",
    # The viewer's own derived art. Declarative for now: `coviewer` builds
    # these from a literal (`OUTDIR = PROJECT / "out" / "viewer"`) and does
    # not route them through here, so nothing changes today -- but the
    # decision is recorded, so whoever routes them finds an answer instead of
    # making one up.
    "out/viewer/texcache/",     # decoded textures for ONE install's bytes
    "out/viewer/terrain",       # terrain.json + terrain_<map>.png, per install
    # `core/dcache.py` -- decoded `tq-stream` tables. Per-install for the same
    # reason as texcache above: these are one client's BYTES, not a fact about
    # the format. Two builds ship an `itemtype.dat` that differ in content
    # while agreeing in grammar.
    #
    # It is declared here rather than in GLOBAL for a second reason worth
    # keeping: dcache's key already carries the resolved source path, so
    # correctness never depended on this line. But its docstring CLAIMED
    # per-install separation, and a cache whose correctness rests on one of two
    # mechanisms while its documentation credits the other is one nobody can
    # reason about. The claim is now true where it says it is.
    "out/cache/",
    # `core/weaponswap.py` -- sha256 of one install's weapon MESH FILES, so the
    # displacement warning can compare art without re-hashing thousands of
    # meshes per page render. Per-install by definition: the whole finding it
    # serves is that two installs ship DIFFERENT BYTES under the same
    # `c3/mesh/<id>.c3` -- 3,762 of 4,718 shared weapon ids, 6609 vs CCO -- so a
    # digest served across bases would answer the exact question it exists to
    # ask, wrongly, in the direction that destroys art.
    #
    # `base_id` alone is NOT sufficient here, and the module does not rely on it
    # alone: `base_fingerprint` hashes only the top level of `ini/`, and the swap
    # page this serves WRITES MESHES -- so an install can change the very bytes
    # this index describes without moving its key. Each entry therefore also
    # carries the size and mtime of the file backing it (the loose file, or the
    # archive), and a mismatch is a miss. This line is the outer lock and the
    # stamp is the inner one; neither is redundant.
    "out/weaponswap/",
    # `tools/bonerig.py` -- the solved family rig for one bone-index space.
    #
    # PER-INSTALL, and the reason is the whole point of the artefact: a rig is
    # solved FROM the clips a particular install ships, and those differ. On
    # CCO shape 2 has 263 present clips and on 5517 it has 412; only 125 of
    # the ones present on both are byte-identical, and the loose and archived
    # copies of a single path can differ in ENCODING. The provenance block
    # inside the file records which files and which shas it was solved from,
    # so two rigs from two installs are genuinely different documents -- and
    # writing both to `out/rig/p84.json` would be the `out/dll/rtti.md` bug
    # again, one install's answer sitting under another's name with nothing
    # recording which.
    "out/rig/",
)

#: Derived paths that are **not about one install**, each with the reason it
#: is not.  Checked before `PER_BASE`, so a longer path opts back out.
#:
#: This list and `PER_BASE` together must cover every ``out/`` path: anything
#: in neither raises `UndeclaredDerived` rather than being guessed at in
#: either direction.  See `derived_rel`, and `docs/derived_classification.md`
#: for how each entry below was decided.
GLOBAL: tuple[str, ...] = (
    "out/thumbs/servers/",
    # Name recovery for the WDF archives: `{hash: name}`, and the TQ hash is
    # a pure function of the filename string -- so an entry is true in *any*
    # install, whatever that install's archives contain. Same reason
    # `out/wdf/` is global, and by the same rule: **the sharing boundary is
    # whatever the artefact is about, and a name table is about strings.**
    # (`f0328a2` measures it: 5,000 sampled entries recomputed, 5,000 agree.)
    #
    # It sits under `out/dll/` only because the wordlist came from DLL
    # strings. Keying it strands `meshtex` and `health`, which resolve it
    # through `find_derived` and get None rather than a fallback.
    "out/dll/wdf_name_recovery",

    # -- the twelve that used to reach the fallthrough ---------------------
    # Classified by reading what writes each one and what it contains;
    # `docs/derived_classification.md` carries the evidence. Eleven were
    # already correct by luck, and three would be actively damaged by keying.

    # Name recovery: `{hash: name}`, and the TQ hash is a pure function of the
    # filename string, so an entry is true in ANY install. Keying costs a
    # ~380 s rebuild each AND degrades coverage.
    "out/wdf/",
    # The protocol, built from refs/, not from an install.
    "out/opcodes.json",
    # Self-guarding: keyed on the sha256 of the module bytes, and returns None
    # on mismatch. The shape everything else is being moved towards.
    "out/offsets_cache.json",
    # Deliberately CROSS-patch: msglayouts.json is keyed by patch inside the
    # file -- {"1001": {"5017": ..., "5065": ...}} -- so keying it would
    # shatter one table into five partial copies, each missing the comparison
    # it exists to make. walkprobe.json is a server probe, not an install.
    "out/client/",
    # The string-table index (`tools/strindex.py`) and the page rendered from
    # it (`tools/strreport.py`). GLOBAL for the same reason as `out/client/`
    # directly above, and it is the strong form of that reason rather than a
    # borrowed one: the schema is `strings(build, file, key, value)` -- the
    # build is a COLUMN. One file holds all 38 of them, and every question it
    # exists to answer is a comparison BETWEEN builds: what text changed
    # between A and B, which keys first appear at 7867, which keys move their
    # printf slots across the lineage. Keying it per-base would shatter one
    # table into 38 partial copies, each holding exactly one build and
    # therefore unable to answer a single one of those.
    #
    # `strindex` prefers `<assets>/derived/strindex/` when that exists and
    # only falls back to this path, so in practice this entry is the
    # no-assets-configured case -- which is precisely when it must still
    # resolve rather than raise.
    "out/strindex/",
    # The report is the same artefact one step further on: it renders the
    # drift table across all 38 builds plus one target build's key search, so
    # its content spans the lineage even though a reader picks a target.
    #
    # Declared because it is the `--out` DEFAULT literal in
    # `tools/strreport.py`, which is what the declaration test walks for. A
    # caller-supplied `--out` is written straight to disk and never passes
    # through `derived_path`, so this entry governs the default and nothing
    # else -- said plainly because a reader could otherwise assume the whole
    # option is resolved here, and it is not.
    "out/strings.html",
    # About a PAIR of installs, and it says which in its own body (`old`,
    # `new`). A pair has no single base_id.
    "out/clientdiff/",
    # The garment survey and the materialised garment OVERLAYS
    # (`tools/garment_missing_survey.py`, `tools/garment_overlay_build.py`).
    #
    # GLOBAL because each artefact already carries its own key IN ITS NAME and
    # a second key would double-key it: `overlay-7878/` is 7878's missing art,
    # `overlay-7878-armet/` the same install's headgear, and `survey.json` is a
    # census ACROSS 33 installs, which has no single base_id at all. The
    # builder writes the unkeyed literal, so keying the prefix here would move
    # only the READER and `find_derived` would return None for a directory
    # that is there.
    #
    # `core/coassets.py`'s `AssetProfile.overlay_dirs` reads through
    # `find_derived("out/garment")`, which is what lets a fresh worktree
    # inherit the primary checkout's 35 MB build instead of resolving a tenth
    # of 7878's rows in silence.
    "out/garment/",
    # About a named library measured against a baseline, both recorded inside.
    "out/zephyr/",
    # A running process, NOT the configured asset root -- heropath.json
    # targets `imconquer.exe`, structfind.json records a pid, and the RE
    # target has been a different client from `game_root` throughout. Keying
    # would file ImConquer pointer paths under `patch5517-...`: authoritative
    # and wrong. Its own key would be the target module's hash, not base_id.
    "out/recon/",
    # Runtime, per run rather than per install.
    #
    # NEW captures no longer land here: the corpus HOME moved OUTSIDE the repo
    # to `capture_home()` (C:/Claude/capture) -- owner
    # ruling confirmed directly to the General Manager and the Senior Director,
    # 2026-09-10, because captures are
    # credential-bearing and `out/` is inside the checkout. This entry stays
    # only to classify the PRESERVED legacy copy that still sits in a checkout's
    # out/sessions/ (gitignored) until the General Manager retires it.
    "out/sessions/",
    "out/companion-logs/",
    # Runtime, and it holds character names. `out/` is gitignored so
    # `tests/test_sanitization.py` never scans it -- which is correct, and is
    # also why this must never move anywhere publishable.
    "out/companion-arenas.json",
    # A report about this checkout, rewritten on every run.
    "out/health.json",
    # `tools/companionci.py` reports. About a RUNNING CLIENT PROCESS and a
    # server row, not about the configured asset root -- same reasoning as
    # `out/client/` below, and the same as `out/offsets_cache.json`: each report
    # names the build it verified inside its own body, keyed on the sha256 of
    # the module bytes. Keying the directory by `base_id` would file a 5065
    # memory verdict under whichever install happened to be configured when the
    # harness ran, which is authoritative and wrong.
    "out/ci/",
    # The viewer's runtime output and the user's OWN data. `tags.json` is not
    # derived at all: keying it would fragment one person's tags across bases
    # and lose them on a base switch -- data loss, not a rebuild, and the only
    # unrecoverable case in this list.
    "out/viewer/tags.json",
    "out/viewer/shots/",
    "out/viewer/preview/",
    "out/viewer/serverviews/",
    # Per-LIBRARY texture caches, already namespaced by hand in the directory
    # name (`texcache-<server>`) -- the ad-hoc version of this key, same as
    # `out/thumbs/servers/`.
    "out/viewer/texcache-",

    # `tools/profilepack.py` -- packed pre-bootstrap name profiles, and the
    # tables unpacked from an applied one. GLOBAL, and the reason is the
    # rule this list states: **the sharing boundary is whatever the artefact
    # is about.** A profile is *about* the `out/wdf/` name tables, which are
    # themselves GLOBAL a few lines above, for the same reason -- the TQ hash
    # is a pure function of the filename string, so a recovered pair is true
    # in any install.
    #
    # It is also the directory where profiles for SEVERAL clients coexist:
    # each carries its base id in its own filename
    # (`patch5517-<fingerprint>-<producer>.zip`) and in its manifest, so the
    # base keying is inside the artefact rather than in the path above it.
    # Keying the directory as well would bury one client's profile under
    # another client's index and defeat the one thing a profile is for --
    # being handed to somebody who does not have that client yet.
    "out/profiles/",

    # The keyed tree itself. Without this, resolving an already-keyed path
    # would raise, and a caller that round-trips one would key it twice.
    "out/indexes/",

    # Action codes named by eye (`tools/actionnames.py`), and the strong form
    # of the rule this list states: the sharing boundary is whatever the
    # artefact is about, and this one is about ACTION CODES. The install is a
    # COLUMN -- each observation carries its own `install` and the sha256 of
    # the clip that was watched -- exactly as `out/client/` carries the patch
    # inside the file and the string index carries `build`.
    #
    # And every question it exists to answer is a comparison BETWEEN installs:
    # does 7878 play 290 the way 5517 does, and did the two people who named
    # it watch the same bytes? Keying it would shatter one naming effort into
    # 36 partial copies, each missing the comparison. The disagreement is the
    # evidence, so it has to sit in one file.
    "out/action_names.json",
)

#: Historical name.  `GLOBAL` is no longer a list of *exceptions* -- it is
#: half of a complete declaration -- but the old name is kept because other
#: worktrees and docs refer to it.
GLOBAL_EXCEPTIONS = GLOBAL


class UndeclaredDerived(KeyError):
    """An ``out/`` path that is in neither `PER_BASE` nor `GLOBAL`.

    Raised rather than guessed, because the two possible guesses fail in
    opposite and unequal ways: sharing an install-specific artefact serves one
    client's facts as another's *silently*, and keying a genuinely shared one
    costs a rebuild and can make it worse (`out/wdf/`) or lose user data
    (`out/viewer/tags.json`).  Neither default is safe, so the answer has to
    be written down.

    The fix is one line in whichever list is right, **with the reason** --
    that is the whole point of the change.  `docs/derived_classification.md`
    shows the form.
    """


#: Where keyed artefacts live.
INDEX_ROOT = "out/indexes"

#: Files in ``ini/`` the **client itself rewrites**, excluded from
#: `base_fingerprint`.  Lower-case, matched by exact name.
#:
#: The fingerprint answers *"which client is this?"*.  These files answer
#: *"what did the user last do?"* -- window positions, resolution, the last
#: account typed -- so including them made the answer change every time
#: anyone shut a client down.
#:
#: **Measured, not guessed** (2026-08-09).  Three CCO index namespaces existed
#: simultaneously: ``cco-a1954ac41d00`` (13 MB, dead), ``cco-ff8ca45988fc``
#: (400 KB, dead), and the live id with no index at all -- each stranded by a
#: shutdown.  ``GameSetUp.ini`` is the newest file in *five* installs
#: (5017, 5065, 5165, 5517, Zephyr); CCO uses ``cqgui.ini`` + ``setup.json``;
#: 5065 also writes ``GUI.ini``.  ``GameSetUp.ini`` additionally stores
#: ``AccountRecord``, so hashing it mixed a personal identifier into a key.
#:
#: **This is a denylist, and that is a real weakness**: a client that writes a
#: runtime file not named here re-keys its install once, before someone adds
#: it.  Chosen knowingly -- the failure mode is a rebuild, never a wrong
#: answer, which is the direction `docs/derived_classification.md` argues for.
#: Add a name here with its evidence rather than widening this to a pattern;
#: `*setup*` and `*gui*` would swallow real tables.
VOLATILE_INI: frozenset[str] = frozenset({
    "gamesetup.ini",   # TQ client settings; also holds AccountRecord (PII)
    "gui.ini",         # UI layout state (5065 and siblings)
    "cqgui.ini",       # the same, in the modern CCO client
    "setup.json",      # CCO client settings
})

#: Files in ``ini/`` **our own tooling rewrites** to prepare an install for the
#: rig, excluded from `base_fingerprint`.  Lower-case, matched by exact name.
#:
#: `VOLATILE_INI` answers *"what did the user last do?"*.  These answer
#: *"what did we do to make this client usable?"* -- a different question with
#: the same consequence, and one `VOLATILE_INI` deliberately does not cover
#: because its own docstring scopes it to what **the client itself** rewrites.
#:
#: **Measured, not guessed** (2026-08-09).  `tools/clientsidecar.py` rewrote
#: ``ini/StartGame.ini`` on the 5517 install at 15:01 -- correctly, with a
#: backup, its reason in the file and a restore command -- and that single
#: sanctioned edit moved the install's key, stranding **346 MB** under
#: ``patch5517-d3ba8e7aa082`` while the live namespace held nothing.  Four
#: sessions then measured four different viewer failure sets on nominally one
#: root, and two of them diagnosed the same three reds as *"never built"* and
#: *"orphaned"* -- both correct, for their own tree.
#:
#: **This is the second instance of that coupling, not a recurrence.**  The
#: first (`docs/handoff_fingerprint_stability.md`) excluded what the *client*
#: writes.  Client modification is an owner-granted, scoped exception for local
#: test clients, so the rig is *expected* to patch these -- which makes an
#: identity that changes when it does the wrong identity, not a misuse.
#:
#: Same denylist weakness as `VOLATILE_INI`, same knowing trade: a tool that
#: rewrites a table not named here re-keys its install once.  Add a name with
#: its writer, not a pattern.
TOOL_WRITTEN_INI: frozenset[str] = frozenset({
    "startgame.ini",    # tools/clientsidecar.py -- disables the TQAT sidecar
    "gui800x600.ini",   # tools/clientdisplay.py --rescale-gui, low screen mode
})

#: Suffixes our client-modifying tools leave beside a file they patch.
#:
#: Excluded because **creating a backup adds a file to ``ini/``**, so a tool
#: that carefully preserves the original re-keys the install by doing so --
#: the exclusion of the patched file alone would not have helped.
#:
#: Safe as a pattern where `VOLATILE_INI`'s docstring warns against one:
#: ``*setup*``/``*gui*`` would swallow real tables, but no shipped table uses
#: these.  Verified across the corpus -- 5017 and CCO, the two installs our
#: tools have not patched, carry **zero** backup-suffixed files in ``ini/``,
#: while every install the rig has touched carries one or two.
#:
#: **Every entry names its writer, and the list is not a pattern.**
#: ``.oracle-orig`` was missed for exactly one character: it ends in ``-orig``,
#: not ``.orig``, so ``str.endswith((".orig", ...))`` did not match it and
#: `tools/datoracle.py`'s two backups became the ENTIRE fingerprint difference
#: between the two 5065 lineages -- 101 common ``ini/`` files, zero differing
#: in content.  Widening to a bare ``-orig`` suffix is refused on the same
#: grounds this docstring already gives for ``*setup*``: it would swallow a
#: real table the day one is named that way.  ``tests/test_backup_suffixes.py``
#: discovers writers by the AST idiom ``with_suffix(suffix + SUFFIX)`` and
#: fails closed on any it cannot account for.
#:
#:   ``.bak``          ``tools/clientdisplay.py``  (``.res.bak``)
#:   ``.orig``         ``tools/clientsidecar.py``
#:   ``.oracle-orig``  ``tools/datoracle.py``      (``BACKUP_SUFFIX``, line 158)
TOOL_BACKUP_SUFFIXES: tuple[str, ...] = (".orig", ".bak", ".oracle-orig")


def fingerprint_skips(name: str) -> bool:
    """Is ``name`` excluded from `base_fingerprint`?

    One predicate, because `base_fingerprint` and `fingerprint_inputs` must
    agree about this and previously each restated it.  Two implementations of
    one rule is how a diagnostic ends up describing a hash it did not take.
    """
    low = name.lower()
    return (low in VOLATILE_INI
            or low in TOOL_WRITTEN_INI
            or low.endswith(TOOL_BACKUP_SUFFIXES))


def fingerprint_inputs(root=None) -> tuple[list[str], list[str]]:
    """``(hashed, skipped)`` file names for `base_fingerprint`, for diagnosis.

    Exposed because a fingerprint is twelve opaque characters, and the first
    question anyone asks when one moves unexpectedly is *what went into it*.
    """
    try:
        d = Path(root) if root is not None else game_root()
    except RootNotHonoured:
        # A refused CO_ROOT must not degrade into a blank fingerprint: that
        # keys the whole derived tree as `unkeyed` and shares one namespace
        # between every install anyone mistypes. Refusal propagates.
        raise
    except Exception:
        return ([], [])
    ini = Path(d) / "ini"
    if not ini.is_dir():
        return ([], [])
    try:
        names = sorted((p.name for p in ini.iterdir() if p.is_file()),
                       key=str.lower)
    except OSError:
        return ([], [])
    return ([n for n in names if not fingerprint_skips(n)],
            [n for n in names if fingerprint_skips(n)])


#: `base_fingerprint`'s memo, keyed on the ini/ directory AND every
#: non-skipped file's (name, size, mtime_ns). Consulted ONLY inside
#: `stable_fingerprints()` -- see the comment in `base_fingerprint` for the
#: measurement that says why a stat signature alone is not sufficient.
#: Cleared by `invalidate_cache()`.
_FINGERPRINT_MEMO: dict = {}

#: True only inside `stable_fingerprints()`. Module-level rather than a
#: parameter because the callers that benefit -- `health.collect` and the
#: provenance audit -- reach `base_fingerprint` through four or five layers
#: that have no business growing a flag.
_FINGERPRINT_STABLE = False

#: How many installs' fingerprints to keep. A viewer serving two bases plus
#: the fx-compare views touches a handful; 64 is far past any real session
#: and stops a long-lived process growing a listing per install forever.
_FINGERPRINT_MEMO_MAX = 64


def base_fingerprint(root=None) -> str:
    """A short content hash of the install's table layer, or ``""``.

    **What it is:** sha256 over every file directly in ``ini/``, in
    name order, contents included -- 250 files and 37 MB at 6090, about
    50 ms.  Cheap enough to ask for on demand and decisive enough to
    separate clients that no other cheap test can: all five official patch
    clients ship byte-identical ``c3.wdf`` and ``data.wdf``, so the archives
    discriminate nothing, and ``version.dat`` is absent from private-server
    repacks.  The table layer is what a parser plugin is *about*, and it is
    where the clients actually differ (91 of 176 shared ``ini/`` files differ
    between 5517 and 6090).

    **What it is not:** proof of identity.  It reads only the top level of
    ``ini/``, so two installs differing solely in loose art or in a
    subdirectory hash the same.  That is the right trade for choosing an
    index namespace -- the cost of a collision is a shared index between two
    installs whose tables agree exactly, and the cost of hashing the loose
    layer instead would be minutes per call.

    **An ``ini/`` with no hashable file returns ``""``, not a hash.**  It used
    to fall through and come out ``e3b0c44298fc`` -- the sha256 of the empty
    string -- which `base_id` then dressed up as ``unknown-e3b0c44298fc``, a
    namespace shaped exactly like a measured one and shared by every
    featureless directory anyone points the tools at.  That is NOT the trade
    above: those two installs' tables do not "agree exactly", they have no
    tables.  The branch below says what it cost.  A real install cannot reach
    it -- `missing_parts` refuses a root whose ``ini/`` holds no tables -- so
    this is about synthetic roots, which is where it bit.

    **What it deliberately ignores**, via `fingerprint_skips`:

    * the files the client rewrites at shutdown (`VOLATILE_INI`).  Hashing
      those made the identity of an install depend on whether anyone had *run*
      it, which orphaned the whole index namespace on every launch -- three
      dead CCO namespaces before it was caught, and getting worse as unattended
      launches became routine.  An install is the same client before and after
      you play it.
    * the files our own tooling rewrites to prepare a client for the rig
      (`TOOL_WRITTEN_INI`), and the backups it leaves beside them
      (`TOOL_BACKUP_SUFFIXES`).  Client modification is a sanctioned exception
      for local test clients, so an identity that moves when we exercise it is
      the wrong identity.  One correct, backed-up, documented edit to
      ``StartGame.ini`` stranded 346 MB of 5517 index and left four sessions
      measuring four different failure sets on one root.  **An install is the
      same client before and after we prepare it.**

    Both are the same rule seen twice: *the fingerprint answers "which client
    is this?", and neither playing it nor preparing it changes the answer.*

    **Changing this function re-keys every install**, so every existing
    `out/indexes/<base-id>/` is orphaned and rebuilds.  Nothing is lost that
    cannot be rebuilt, but it is not a quiet change; see
    `docs/handoff_fingerprint_stability.md`.
    """
    try:
        d = Path(root) if root is not None else game_root()
    except RootNotHonoured:
        # A refused CO_ROOT must not degrade into a blank fingerprint: that
        # keys the whole derived tree as `unkeyed` and shares one namespace
        # between every install anyone mistypes. Refusal propagates.
        raise
    except Exception:
        return ""
    ini = Path(d) / "ini"
    if not ini.is_dir():
        return ""

    # THE STAT SIGNATURE IS THE MEMO KEY -- AND IT IS NOT ENOUGH ON ITS OWN,
    # WHICH IS WHY THE MEMO IS OPT-IN.
    #
    # The first version of this change memoised unconditionally on
    # (name, size, mtime_ns) and argued that a rewrite moves the key. MEASURED:
    # `test_a_sanctioned_tool_edit_does_not_re_key_the_install` went red one
    # run in three. It rewrites a table to the SAME SIZE, and Windows updates
    # a file's mtime on a ~15.6 ms system-clock tick, so two writes inside one
    # tick are indistinguishable by stat. The tools the old docstring named --
    # `clientsidecar`, `clientdisplay`, `datoracle` -- are exactly the ones
    # that rewrite tables in place, so that is not a corner case for them.
    #
    # A stat signature therefore cannot decide this on its own, and the
    # knowledge that nothing is rewriting an install lives at the CALL SITE,
    # not here. `stable_fingerprints()` is how a caller says so: inside it the
    # memo is consulted, outside it every call re-hashes exactly as before.
    # `health.collect` takes 4-15 fingerprints of one unchanging install per
    # report and is the case this exists for; `datoracle` must never use it.
    #
    # `os.scandir`, not `Path.iterdir` + `stat`: MEASURED 0.09-0.12 ms against
    # 4.6-5.8 ms warm -- cheap enough to take on every call even when the memo
    # is off, which keeps this one code path instead of two.
    try:
        sig = []
        with os.scandir(ini) as it:
            for e in it:
                if not e.is_file() or fingerprint_skips(e.name):
                    continue
                st = e.stat()
                sig.append((e.name.lower(), st.st_size, st.st_mtime_ns))
        sig.sort()
    except OSError:
        return ""

    # SHA256 OF NOTHING IS NOT AN IDENTITY, AND IT WAS BEING SERVED AS ONE.
    #
    # An `ini/` that exists and holds no hashable file used to fall through to
    # the hash below and come out `e3b0c44298fc` -- the empty-string digest.
    # `base_id` then read `unknown-e3b0c44298fc`, which is shaped exactly like
    # a measured namespace (`unknown-7f17d552d4b2` is one) and is shared by
    # EVERY featureless directory anyone ever points the tools at. MEASURED on
    # master `77a0dffa`: two unrelated synthetic installs in two separate
    # `TemporaryDirectory` trees were handed one `meshtex/mesh_index.json`,
    # the second was served the first's census, and `provenance.verdict` said
    # `match` -- because the stamps really were identical.
    #
    # `""` is what the rest of this function already returns for "no answer",
    # and it is the honest one: no file, no content, no content hash. The
    # consequences are already designed for -- `base_id` reads `unkeyed`,
    # `provenance.verdict_for_stamp` downgrades that to UNKNOWN instead of
    # MATCH, and `provenance.optional_stamp` refuses to embed it.
    #
    # This does NOT on its own stop two featureless roots sharing a directory
    # (`out/indexes/unkeyed/` is one directory too). It stops them sharing one
    # that LOOKS measured, which is what defeated every reader above. The
    # sharing is closed by giving each test its own derived root --
    # `coroot.derived_root`, `tests/derivedroot.py`.
    #
    # An install is not affected: `missing_parts` refuses a root whose `ini/`
    # does not hold tables, so a real client never reaches this branch.
    if not sig:
        return ""

    key = (str(ini).lower(), tuple(sig))
    if _FINGERPRINT_STABLE:
        hit = _FINGERPRINT_MEMO.get(key)
        if hit is not None:
            return hit

    import hashlib
    h = hashlib.sha256()
    try:
        for name, _size, _mtime in sig:
            h.update(name.encode("utf-8"))
            h.update((ini / name).read_bytes())
    except OSError:
        return ""
    out = h.hexdigest()[:12]
    # Bounded, because a long-lived viewer can see many installs and each
    # entry holds a whole directory listing. FIFO by insertion order: this
    # is a cost cache, not a correctness one -- a miss re-hashes, which is
    # exactly what happened before.
    if _FINGERPRINT_STABLE:
        if len(_FINGERPRINT_MEMO) >= _FINGERPRINT_MEMO_MAX:
            for k in list(_FINGERPRINT_MEMO)[:len(_FINGERPRINT_MEMO) // 2]:
                _FINGERPRINT_MEMO.pop(k, None)
        _FINGERPRINT_MEMO[key] = out
    return out


def stable_fingerprints():
    """Memoise `base_fingerprint` for the duration of this block.

        with coroot.stable_fingerprints():
            rep = health.collect(root)

    **The caller is asserting that no install is being rewritten inside the
    block**, and that assertion is the whole mechanism. A stat signature
    cannot detect a same-size write inside one ~15.6 ms mtime tick, so
    `base_fingerprint` cannot decide on its own whether caching is safe --
    but a health report, a provenance audit or one HTTP request can, because
    they do not write to installs.

    MEASURED on the configured install: `base_fingerprint` is 31.8 ms cold
    and 0.277 ms memoised, 115x. `health.collect` takes 4-15 of them per
    report and the first-run card polls it every 1.5 s.

    Re-entrant, and it does NOT clear the memo on exit: a nested block must
    not switch caching off for the outer one, and the entries are keyed on a
    stat signature so they stay usable for a later block until something
    changes or `invalidate_cache()` runs.

    Never wrap `clientsidecar`, `clientdisplay` or `datoracle` in this: they
    rewrite tables in place, which is the exact case the memo cannot see.
    """
    import contextlib                                       # noqa: PLC0415

    @contextlib.contextmanager
    def _scope():
        global _FINGERPRINT_STABLE
        prev = _FINGERPRINT_STABLE
        _FINGERPRINT_STABLE = True
        try:
            yield
        finally:
            _FINGERPRINT_STABLE = prev

    return _scope()


def cover_manifest(root, covers) -> list:
    r"""``(relative posix path, size)`` lines under each name in ``covers``.

    The SECOND half of `pin_fingerprint`, exposed for the same reason
    `fingerprint_inputs` is: when a pin's digest moves, the first question is
    *what moved*, and a twelve-character hash cannot answer it.

    **Metadata, not contents, and that is the whole trade.**  ``ini/`` is
    28-103 MB and hashing it whole costs 28-124 ms warm; ``map/map`` is
    402 MB on 5517 and ``c3/`` is 5.7 GB on 7878, so the same treatment there
    is minutes, not milliseconds.  A ``(path, size)`` manifest over those
    trees costs 9-28 ms MEASURED and still moves when a file is **added,
    removed, renamed, or resized** -- which is every shape of drift this
    corpus has actually suffered: a mod dropping files in, a repack shipping
    a shorter table, an extraction leaving half a tree behind.

    **What it does NOT catch, stated rather than implied:** an edit that
    changes bytes and keeps the byte COUNT.  A one-character hex tweak inside
    a ``.DMap`` cell, a re-saved mesh of identical length, a patched opcode.
    For a subject where that is the realistic threat, hash the contents --
    put the table in ``ini/`` where `base_fingerprint` already does, or do not
    claim the pin covers it.

    A missing covered directory is a DIFFERENCE, recorded as ``MISSING``,
    never a silent empty list: a pin whose subject tree has vanished must fail
    on the same terms as one whose subject tree has changed.
    """
    out = []
    for name in covers:
        rel = str(name).replace("\\", "/").strip("/")
        try:
            d = Path(root) / rel
        except Exception:
            out.append(f"{rel.lower()}\tMISSING")
            continue
        if not d.is_dir():
            out.append(f"{rel.lower()}\tMISSING")
            continue
        rows = []
        try:
            for dirpath, dirnames, filenames in os.walk(d):
                dirnames.sort()
                for fn in filenames:
                    # Our own tooling's backups, excluded here for exactly the
                    # reason `TOOL_BACKUP_SUFFIXES` excludes them from
                    # `base_fingerprint`: preparing a client for the rig must
                    # not re-key it.  `VOLATILE_INI`/`TOOL_WRITTEN_INI` are
                    # deliberately NOT applied -- those name files by their
                    # bare `ini/` name, and a `map/` file that happens to share
                    # one of those names is a different file.
                    if fn.lower().endswith(TOOL_BACKUP_SUFFIXES):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        size = -1
                    r = os.path.relpath(p, d).replace("\\", "/").lower()
                    rows.append(f"{rel.lower()}/{r}\t{size}")
        except OSError:
            out.append(f"{rel.lower()}\tUNREADABLE")
            continue
        out.append(f"{rel.lower()}\t{len(rows)} file(s)")
        out.extend(sorted(rows))
    return out


def pin_fingerprint(root, covers=()) -> str:
    r"""Content identity of a PINNED install root, or ``""``.

    **The defect this closes.**  `tools/test_viewer.py`'s `DeclaredInstall`
    and `tests/test_npcaltskin.py`'s `_pinned` both resolved a pin by matching
    a directory NAME.  A directory called ``5517`` whose CONTENT had drifted
    satisfied both of them, while `routeb/corpus.py`'s `RECORDED_FINGERPRINT`
    -- one directory away, over the same install -- would have refused it.
    Two mechanisms disagreed and only one of them was checking anything.
    Recorded as RESIDUAL in `MeshCopyEquivalence`'s docstring and in
    CORRECTIONS ``C-2026-08-26-claude-vibeco-dx-camera-ambient-install``.

    **Why the name is not enough, in one sentence:** *a pin exists to say the
    numbers in this test are THIS install's, and a name is a label somebody
    typed while the numbers are a fact about bytes.*

    FORM
    ----
    With no ``covers``, this is `base_fingerprint` VERBATIM -- deliberately,
    so a pin's recorded value and `routeb.corpus.RECORDED_FINGERPRINT` are
    the same twelve characters for the same install and can be cross-checked
    by eye and by test.  With ``covers``, a suffix is appended:

        ``76c7f4499934-1a2b3c4d``     ini/ content digest + covers manifest

    The base half stays legible, so "which client" is still readable off the
    front of a compound pin.

    WHAT IT COVERS, AND THE JUSTIFICATION
    -------------------------------------
    * ``ini/`` -- every byte, through `base_fingerprint`, with that function's
      volatility exclusions inherited unchanged.  This is the table layer,
      which is where the clients actually differ (91 of 176 shared ``ini/``
      files differ between 5517 and 6090) and what most pinned numbers are
      about.  MEASURED 28-124 ms warm, 0.5-3.6 s cold, per install.
    * each name in ``covers`` -- a ``(path, size)`` manifest, NOT contents.
      See `cover_manifest` for the cost measurements and for the one thing it
      misses.

    WHAT SLIPS PAST IT
    ------------------
    Say this out loud rather than discovering it: **an equal-size in-place
    byte edit inside a ``covers`` tree, and ANY change to a subtree the pin
    did not declare** -- loose art, archives (``c3.wdf``/``data.wdf``), and
    anything under ``c3/`` unless a pin names it.  Hashing 5.7 GB of ``c3/``
    per test run is not viable and pretending otherwise would buy a slower
    suite and the same blind spot at a different boundary.  A pin whose
    subject lives inside an archive is honestly only NAME-pinned for that
    part, and `docs/pin_fingerprinting_2026-09-07.md` names which ones.

    NOT MEMOISED HERE, and that is still true of THIS function.
    `base_fingerprint` below IS memoised now, and the objection that used to
    be recorded here -- that a cache would hand a stale identity to the
    client-modifying tools (`clientsidecar`, `clientdisplay`, `datoracle`)
    which legitimately rewrite an install mid-process -- is answered rather
    than overruled: the memo is keyed on every non-skipped `ini/` file's
    (name, size, mtime_ns), so a rewrite MOVES THE KEY and the hash is taken
    again. A cache keyed on the root would indeed be wrong; that is not what
    it is keyed on. `invalidate_cache()` clears it as well.
    """
    base = base_fingerprint(root)
    if not base:
        return ""
    if not covers:
        return base
    import hashlib
    h = hashlib.sha256()
    for line in cover_manifest(root, covers):
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return f"{base}-{h.hexdigest()[:8]}"


def base_id(root=None) -> str:
    """The namespace a derived artefact belongs to: ``<kind>-<fingerprint>``.

    The kind half is the declared ``game_kind`` -- the user's own statement
    of what this folder is -- so the directory name is readable
    (``patch5517-113d8413ee90``) rather than opaque.  The fingerprint half is
    what makes it *correct*: a declaration can be stale or absent, and a
    repack of 6090 declares itself 6090 while shipping different tables.

    **Re-declaring a folder deliberately changes the namespace**, even though
    the bytes did not move.  An index is what a *plugin* concluded *about* an
    install, so a different parse profile is a different index -- declaring a
    6090 repack "myserver" instead of "patch6090" must not keep serving
    answers derived under the other one's conventions.  The rebuild is the
    point, not a cost.

    Store the plugin's canonical ``name`` here, not an alias: ``for_kind``
    accepts both and they would key two directories from one install.  The
    setup page already writes ``plug.name``.

    A missing directory means "build me", never "borrow another base's
    answers".  Falls back to ``unknown-<fingerprint>`` with no declaration,
    and to ``unkeyed`` when there is no fingerprint to be had -- no ``ini/``,
    an unreadable one, or one with nothing hashable in it.

    **``unkeyed`` IS ONE DIRECTORY, SHARED, AND THAT IS NOT AN ACCIDENT TO
    RELY ON.**  This docstring used to claim it "keeps a broken install from
    silently sharing whatever was built last"; it does not -- every unkeyed
    root resolves to ``out/indexes/unkeyed/``.  What it does buy is that the
    sharing is LEGIBLE: `provenance.verdict_for_stamp` downgrades ``unkeyed``
    to UNKNOWN instead of MATCH, and `provenance.optional_stamp` refuses to
    embed it, neither of which an ``unknown-<12 hex>`` id ever triggered.
    Anything that must not share -- a test, above all -- needs a derived root
    of its own: `derived_root`, and `tests/derivedroot.py`.
    """
    fp = base_fingerprint(root)
    if not fp:
        return "unkeyed"
    return f"{_declared_kind(root)}-{fp}"


#: Settings key: ``{absolute root: plugin name}``, every folder the user has
#: ever declared.  ``game_kind`` alone cannot answer for more than one
#: install, and this machine has eight.
KINDS_KEY = "kinds"


def declare_kind(root, kind: str) -> Path:
    """Remember that ``root`` is a ``kind`` of client.  Returns the file written.

    Writes both the single ``game_kind`` (what the app reads for the *current*
    install) and an entry in `KINDS_KEY`, so the declaration survives pointing
    the tools somewhere else and back.  Without the map, switching roots with
    ``CO_ROOT`` or ``--root`` drops to ``unknown`` and silently opens a second,
    empty index namespace for a client you already declared.
    """
    doc = read_settings()
    kinds = dict(doc.get(KINDS_KEY) or {})
    try:
        kinds[str(Path(root).resolve())] = str(kind)
    except OSError:
        kinds[str(root)] = str(kind)
    return write_settings(game_kind=str(kind), **{KINDS_KEY: kinds})


def forget_kind(root) -> bool:
    """Drop ``root``'s declaration.  True if there was one to drop.

    The counterpart `declare_kind` never had. Without it a mistyped or moved
    path stays in the map forever, and every consumer that walks declared
    installs -- `tools/wdf_recover.py`'s wordlist discovery, the picker --
    keeps opening a directory that is not there.

    Deliberately does NOT touch ``game_kind``: that is which client the tools
    are pointed at now, and forgetting what a folder *is* should not silently
    change what you are working on.
    """
    doc = read_settings()
    kinds = dict(doc.get(KINDS_KEY) or {})
    try:
        key = str(Path(root).resolve())
    except OSError:
        key = str(root)
    hit = kinds.pop(key, None)
    if hit is None:
        # Tolerate a path given in a different spelling to the one stored.
        want = key.lower().replace("\\", "/")
        for k in list(kinds):
            if k.lower().replace("\\", "/") == want:
                hit = kinds.pop(k)
                break
    if hit is None:
        return False
    write_settings(**{KINDS_KEY: kinds})
    return True


def declared_kinds() -> dict:
    """``{root: kind}`` for every declaration, as stored."""
    return dict(read_settings().get(KINDS_KEY) or {})


#: Settings key: ``{absolute root: ["c3/mesh", ...] | "all"}`` -- which
#: logical groups the user wants thumbnails rendered for, PER CLIENT.
#:
#: Per client and not global for the reason the whole thumbnail estimator was
#: just rewritten: the corpora differ ~30x and they do not even hold the same
#: folders, so one list applied to every install is a fact about one client
#: printed as a fact about all of them.
THUMB_PATHS_KEY = "thumbnail_paths"


def _root_key(root=None) -> str:
    """The spelling `THUMB_PATHS_KEY` and `KINDS_KEY` are keyed by.

    Resolved and normalised, because the same install arrives here spelled
    three ways -- `CO_ROOT` with forward slashes, `--root` with backslashes,
    and the picker's already-resolved path -- and a selection saved under one
    spelling and read under another is a selection that silently reverts to
    "every group", i.e. to the bill the user was avoiding.
    """
    if root is None:
        root = read_settings().get("game_root")
    if not root:
        return ""
    try:
        return str(Path(root).resolve()).replace("\\", "/").rstrip("/").lower()
    except OSError:
        return str(root).replace("\\", "/").rstrip("/").lower()


def thumbnail_paths(root=None):
    """The saved group selection for ``root``, or **None** if there is none.

    ``None`` and ``[]`` are different answers and the caller must keep them
    apart: nothing saved means "every group", an empty list means the user
    unticked everything.  Returning ``[]`` for "nothing saved" would start a
    full-corpus run for someone who had chosen the opposite.
    """
    saved = read_settings().get(THUMB_PATHS_KEY) or {}
    if not isinstance(saved, dict):
        return None
    key = _root_key(root)
    if key in saved:
        v = saved[key]
        return v if isinstance(v, str) else list(v or [])
    # Tolerate an entry stored before this normalisation existed.
    for k, v in saved.items():
        if str(k).replace("\\", "/").rstrip("/").lower() == key:
            return v if isinstance(v, str) else list(v or [])
    return None


def set_thumbnail_paths(root, groups) -> Path:
    """Remember which groups ``root`` should render.  Returns the file written.

    ``groups`` is a list of `thumbs.logical_group` names, the string ``"all"``
    for every group, or ``None`` to forget the selection (which is not the
    same as ``[]``; see `thumbnail_paths`).
    """
    doc = read_settings()
    saved = dict(doc.get(THUMB_PATHS_KEY) or {})
    key = _root_key(root)
    if not key:
        raise ValueError("no install named, so there is nothing to key the "
                         "thumbnail selection to")
    if groups is None:
        saved.pop(key, None)
    elif isinstance(groups, str):
        saved[key] = groups
    else:
        saved[key] = sorted({str(g).strip().lower()
                             for g in groups if str(g).strip()})
    return write_settings(**{THUMB_PATHS_KEY: saved})


def kind_for_root(root=None) -> str:
    """The plugin name the user declared for ``root``, or ``""``.

    Ask this rather than reading ``game_kind`` directly.  ``game_kind`` is a
    single value and this box has 33 installs (measured 2026-08-29), so it
    answers for
    whichever root the config names and for no other: resolve a different one
    and it hands back a plugin for a client you are not looking at.  That is
    not theoretical -- running the suite with ``CO_ROOT`` pointed at 6090
    while the config named 5517 loaded the 5517 plugin against 6090's assets,
    and the only reason it surfaced was a provenance label changing.
    """
    kind = _declared_kind(root)
    return "" if kind == "unknown" else kind


def _declared_kind(root=None) -> str:
    """What the user said this root is, or ``unknown``.

    The per-root map (`KINDS_KEY`) answers first, because it is the only
    record that can be about more than one install.  ``game_kind`` is the
    fallback and is only evidence for the root it was saved beside: resolve a
    different one -- via ``CO_ROOT``, ``--root``, or an explicit argument --
    and it describes some other folder, so using it would file one client's
    index under another's name.

    Never guesses.  Detection is the app's job and the user's declaration
    outranks it; a wrong name here would be baked into a directory.
    """
    doc = read_settings()
    try:
        here = Path(Path(root) if root is not None else game_root()).resolve()
    except RootNotHonoured:
        raise                      # same rule as `base_fingerprint`
    except Exception:
        return "unknown"

    def clean(k: str) -> str:
        k = str(k).strip().lower()
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in k)

    for path, kind in (doc.get(KINDS_KEY) or {}).items():
        try:
            if Path(str(path)).resolve() == here and str(kind).strip():
                return clean(kind)
        except OSError:
            continue
    kind = str(doc.get("game_kind", "")).strip()
    declared_for = doc.get("game_root")
    if not kind or not declared_for:
        return "unknown"
    try:
        if Path(str(declared_for)).resolve() != here:
            return "unknown"
    except OSError:
        return "unknown"
    return clean(kind)


def derived_rel(rel: str, root=None) -> str:
    """Rewrite a derived path into its per-base namespace, if it has one.

    ``out/meshtex/coverage.json`` -> ``out/indexes/<base-id>/meshtex/coverage.json``
    ``out/wdf/c3_names.json``     -> unchanged

    Callers keep writing the plain literal they always wrote; this is the one
    place that knows which trees are per-install.

    ``root`` names *which* install to resolve for, and defaults to the
    configured one.  It matters whenever a process holds more than one
    catalogue at a time -- the viewer serving 6090 and 5517 side by side is
    the case this exists for.  Without it the answer would be per-process,
    and the second base would silently read the first's index, which is the
    whole failure this key was built to stop.
    """
    r = str(rel).replace("\\", "/")
    for pref in GLOBAL:
        if r == pref.rstrip("/") or r.startswith(pref):
            return r
    for pref in PER_BASE:
        if r == pref.rstrip("/") or r.startswith(pref):
            return f"{INDEX_ROOT}/{base_id(root)}/{r[len('out/'):]}"
    if r.startswith("out/") and len(r) > len("out/"):
        raise UndeclaredDerived(
            f"{r!r} is under out/ but is in neither coroot.PER_BASE nor "
            f"coroot.GLOBAL. Decide which it is and add the prefix there, "
            f"with a comment saying why -- see docs/derived_classification.md. "
            f"It is not guessed at: sharing an install-specific artefact is "
            f"silently wrong, and keying a shared one costs a rebuild or "
            f"loses data.")
    return r


def derived_path(rel: str, root=None) -> Path:
    """Where a **writer** should put ``rel``, in this derived root, keyed.

    Never falls back to another checkout: everything that builds an artefact
    builds it here.  Creates no directories -- the caller decides when.

    "Here" is `derived_root`, which is this checkout unless something moved
    it.  A test's own derived tree is the case that exists for.
    """
    return derived_root() / derived_rel(rel, root)


#: Environment override for the thumbnail folder. Process-scoped, so a viewer
#: started with ``--thumbs-dir`` hands it to the `thumbs.py` children it
#: spawns. For trying a rendering branch without touching the shared renders.
THUMBS_DIR_VAR = "COMOD_THUMBS_DIR"
#: Settings key for a persistent thumbnail folder chosen by the user.
THUMBS_DIR_KEY = "thumbs_dir"


def thumbs_base() -> tuple:
    """``(Path, source)`` -- the folder thumbnails are rendered into and read
    from, and which rule chose it.

    Thumbnails are expensive (tens of minutes per client) and change only when
    the renderer does, so they are NOT a per-checkout artefact any more: every
    worktree used to start from an empty ``out/thumbs/`` and regenerate. Most
    specific first:

    1. ``$COMOD_THUMBS_DIR`` -- ``"env"``. A throwaway folder for a branch that
       changes pixels, so its renders never land in the shared set.
    2. ``thumbs_dir`` in the per-user settings -- ``"setting"``.
    3. ``<assets_dir()>/derived/thumbs`` -- ``"shared"``, the default, beside
       the other expensive artefacts built from ``Clients/`` (never inside it).
    4. this checkout's ``out/thumbs`` -- ``"checkout"``, the old behaviour, only
       when there is no asset collection to hold a shared folder.

    Invalidation does not depend on the folder: `thumbs.RENDERER_VERSION` is in
    every cache key, so bumping it makes ``--resume`` re-render everything
    wherever the folder is.
    """
    raw = os.environ.get(THUMBS_DIR_VAR, "").strip()
    if raw:
        return Path(raw), "env"
    raw = str(read_settings().get(THUMBS_DIR_KEY) or "").strip()
    if raw:
        return Path(raw), "setting"
    derived = assets_dir() / "derived"
    if derived.is_dir():
        return derived / "thumbs", "shared"
    return derived_root() / "out" / "thumbs", "checkout"


#: Plugin `origin` values whose installs are LIVE: patched by their own
#: launcher, or edited by the owner, while we hold artefacts about them.
LIVE_ORIGINS = ("server",)


#: The declared kinds whose installs are LIVE -- patched by their own
#: launcher or edited by the owner while we hold artefacts about them.
#:
#: **WHY A LITERAL IN COre, AND NOT ASKED OF `plugins` AT RUNTIME.** Three
#: designs were tried in one night and the first two were both wrong:
#:
#: 1. `is_live_install` imported `plugins` -- `test_viewer.CoreBoundary`
#:    red: `core/` must import only itself and the stdlib, or it stops
#:    being extractable. A lazy import does not help; the gate reads source.
#: 2. `plugins` registered the set with COre at discovery, cached in the
#:    settings -- `SettingsDirectoryManagement.test_the_cost_read_computes_
#:    and_writes_nothing` red: discovery happens inside READ paths, and a
#:    read must leave the config byte-identical.
#: 3. registration in memory only -- no gate caught it, and the SD named
#:    the defect anyway: **the answer then depended on whether the process
#:    had imported `plugins`.** `tools/health.py` and `tools/unify.py`
#:    never import it, and `tools/coviewer.py` imports it only inside
#:    functions. So the viewer could resolve a CONTENT-keyed folder while
#:    `thumbs.py` wrote the LIVE-keyed one: one install, two folders, which
#:    is the same class of bug as the orphaned 636 MB this feature exists
#:    to prevent.
#:
#: A literal is import-order independent and readable at the point of use.
#: The cost is drift -- a new private-server plugin would not appear here --
#: and `tests/test_thumbs_dir.py` closes that: an arm asserts this set
#: equals the plugin-derived one and names what to add when it does not.
LIVE_KINDS = frozenset({"cco", "zephyr1057"})


def live_kinds() -> frozenset:
    """`LIVE_KINDS`. A function because callers and tests patch this, and
    because the source of the answer has changed twice already."""
    return LIVE_KINDS


def is_live_install(root=None) -> bool:
    """Is this install one that gets PATCHED under us?

    True for a declared kind that `plugins` registered as live -- the
    private-server clients (Classic Conquer 2.0, Zephyr), whose plugin
    `origin` is in `LIVE_ORIGINS`. False for the official patch clients,
    which are frozen copies, and false whenever the answer cannot be
    established: the conservative direction is content keying, which is what
    everything did before.
    """
    try:
        return (_declared_kind(root) or "").strip().lower() in live_kinds()
    except Exception:                                       # noqa: BLE001
        return False


def thumbs_key(root=None) -> str:
    """The thumbnail namespace for ``root``: STABLE for a live install.

    `base_id` keys by the CONTENT of ``ini/``, which is right for an index:
    a repack of 6090 that declares itself 6090 must not inherit the real
    6090's answers. But a LIVE install is patched under us -- the owner's
    Classic Conquer 2.0 went 1074 -> 1076 on 2026-09-17 and re-keyed -- and
    every re-key orphans that client's whole thumbnail set.

    **Thumbnails do not need content keying, because each entry is already
    content-addressed**: `thumbs._key` hashes the mesh and texture bytes with
    the render options, so an asset the patch changed re-renders and one it
    did not is reused. Keying the FOLDER by content therefore throws away
    renders that are still correct -- 636 MB of CCO renders for a patch that
    changed four models.

    So for a live install the folder is keyed by WHICH INSTALL it is
    (declared kind + a hash of the resolved path) and not by what it
    currently contains. Two live installs never collide; the same install
    keeps its folder across patches; and a folder that is replaced by a
    different client re-renders anyway, because every entry key changes.

    Offline installs keep `base_id` unchanged.
    """
    if not is_live_install(root):
        return base_id(root)
    try:
        p = str(Path(root).resolve() if root is not None else game_root())
    except Exception:                                       # noqa: BLE001
        return base_id(root)
    import hashlib                                          # noqa: PLC0415
    h = hashlib.sha256(p.lower().encode("utf-8")).hexdigest()[:12]
    return f"{_declared_kind(root)}-live-{h}"


def thumbs_dir(root=None, server: str = "") -> Path:
    """Where the thumbnails for one install (or one library server) live.

    The same folder for writing and reading: ``tools/thumbs.py`` renders into
    it, and the viewer's index reads the manifest from it.

    Keyed exactly as before under whichever base `thumbs_base` picks:
    ``<base-id>/`` for an install, because two clients hold different bytes
    under the same logical path, and ``servers/<name>/`` for a library server.
    The ``"checkout"`` source keeps the old ``out/indexes/<base-id>/thumbs``
    spelling so existing per-checkout renders are still found.
    """
    base, source = thumbs_base()
    if source == "checkout":
        if server:
            return derived_root() / "out" / "thumbs" / "servers" / server
        return derived_path("out/thumbs", root)
    if server:
        return base / "servers" / server
    # `thumbs_key`, not `base_id`: a LIVE install keeps its folder across a
    # patch, because every entry inside is content-addressed already.
    return base / thumbs_key(root)


def set_thumbs_dir(path) -> Path:
    """Persist a thumbnail folder, or clear it (back to the shared default)
    with ``None``."""
    return write_settings(**{THUMBS_DIR_KEY: ("" if path is None
                                              else str(Path(path).resolve()))})


def installs_root() -> Optional[Path]:
    """Where comod keeps install records, when it is not the local tree.

    An install record describes a GAME INSTALL. There is one game install per
    machine, so there should be one record for it -- but `comod` derives the
    location from its own `PROJECT`, which is per-CHECKOUT. On a box with
    linked worktrees that means one record per worktree, and two consequences:

      * a worktree can install over a game another checkout already modified,
        and neither record knows, so `uninstall` restores a file to a state
        the other install has since replaced;
      * the guard that stops the CCO tests asserting STOCK facts reads this
        location to decide whether anything is installed. In a worktree with
        no records it answered "nothing is installed" and the tests pinned
        stock values against a modded game.

    A *setting* rather than a search, because the search has no honest answer:
    the records here live in a LINKED worktree, not the primary, so
    `primary_checkout()` finds nothing and any "look around for one" rule is
    guessing which checkout speaks for the machine. Unset -- which is the
    shipped case, where there is exactly one tree -- means the local one.
    """
    return installs_root_why()[0]


def installs_root_why() -> tuple[Optional[Path], str]:
    """`(path_or_None, why)` -- and the three cases are NOT the same fact.

    `installs_root()` collapses them all to `None`, and `comod` then falls
    back to its own per-checkout tree. That is right for "unset" and WRONG
    for "set but missing": a renamed or unmounted `C:/COMod` silently
    re-homes every install record into whichever worktree is running, and
    this module's own docstring above says what that costs -- `uninstall`
    restoring a file to a state another install has since replaced.

    A setting that names a folder which is not there is a thing the user
    wants to hear about, not a thing to quietly route around. The `why` is
    what a surface prints so the fallback stops being invisible.
    """
    raw = _read_user_config().get("installs_root")
    if not raw:
        return None, "not set -- each checkout keeps its own records"
    p = Path(str(raw))
    if p.is_dir():
        return p, "set in config.json (installs_root)"
    return None, ("set in config.json to %s, which is NOT a directory -- "
                  "records fall back to this checkout's own tree" % p)


def set_installs_root(path) -> Path:
    """Name the shared install-record directory, or clear it with ``None``."""
    return write_settings(installs_root=("" if path is None
                                         else str(Path(path).resolve())))


#: Settings key holding the folder new clients are scanned for.  Stored in the
#: same per-user document as everything else -- there is one config store.
CLIENTS_ROOT_KEY = "clients_root"

#: Settings key holding the community-library folder.
COMMUNITY_LIBRARY_KEY = "community_library"


def clients_root() -> tuple:
    """``(Path|None, why)`` -- the folder holding the client collection.

    Resolved **through the settings this module already owns** and never
    written anywhere as a literal.  Three sources, most specific first:

    1. ``clients_root`` in the per-user config, if the user has set one.  An
       explicit answer outranks any derivation.
    2. The folder that most of the *declared* installs already live in.  The
       declarations are the user's own (`declare_kind`), so this is still
       their answer -- read back rather than asked again.  Modal, not
       common-ancestor: a machine with seven clients under one folder and one
       in Program Files has a common ancestor of ``C:\\``, which is not a
       place to scan.
    3. The parent of the configured install root, as a last resort.

    This lived in `tools/coviewer.py` until the install-path gate was widened
    (`tests/test_sanitization.py`, check 3).  Its own docstring said why it
    belongs here: the old gate matched only the Program Files path, "so
    writing the asset-tree path here would sail past the gate and still be the
    same defect".  Now that the gate covers the class rather than one prefix,
    every tool and test that used to carry ``<somewhere>/ConquerAssets/
    Clients`` needs one obvious function to call, and a resolver that reads
    this module's config belongs in this module.
    """
    doc = read_settings()
    explicit = str(doc.get(CLIENTS_ROOT_KEY) or "").strip()
    if explicit:
        p = Path(explicit)
        return (p, f"set in {user_config_path()} ({CLIENTS_ROOT_KEY})")

    counts: dict = {}
    for path in (doc.get(KINDS_KEY) or {}):
        try:
            parent = Path(str(path)).resolve().parent
        except OSError:                              # pragma: no cover
            continue
        counts[parent] = counts.get(parent, 0) + 1
    if counts:
        # Ties broken by path text so the answer does not depend on dict order.
        best = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0]
        if best[1] >= 2 and best[0].is_dir():
            return (best[0], f"where {best[1]} of your declared clients live")

    try:
        cfg = config_root()
    except Exception:                                # pragma: no cover
        cfg = None
    if cfg:
        p = Path(cfg[0]).parent
        if p.is_dir():
            return (p, f"the folder holding the configured install ({cfg[1]})")
    return (None, "no clients declared yet, and no folder set -- set one below")


EXPORT_DIR_KEY = "export_dir"

#: Where bundles land when the user has not said otherwise.  OWNER'S CHOICE,
#: 2026-09-23: *"by default, I want C:\\COMod\\Export as the standard base"*.
#:
#: **A LITERAL, and this is one of the three files allowed to hold one**
#: (`tests/test_sanitization.INSTALL_PATH_ALLOWED`).  That allowance is the
#: whole reason this resolver lives here rather than in `comod` or the
#: viewer: those are the callers, and a caller that spells the path itself is
#: the defect the gate exists to catch.
#:
#: It is a DEFAULT, not a constant -- `EXPORT_DIR_KEY` in the per-user config
#: outranks it, exactly as `clients_root` outranks its own derivation.
_DEFAULT_EXPORT_DIR = r"C:\COMod\Export"


def export_root() -> tuple:
    """``(Path, why)`` -- where import/export bundles are kept.  Never None.

    Two sources, most specific first, and the `why` is returned rather than
    inferred because **the panel shows this path to a human** and "where did
    it come from" is the question a path on screen immediately raises.

    1. ``export_dir`` in the per-user config, if set.
    2. The default above.

    WHY THIS MOVED OUT OF `comod.WORK`.  It used to be
    ``<repo>/Installed/work/export``, which put the user's bundles **inside a
    git worktree** -- per-checkout, invisible from another clone, and removed
    by anything that cleans the tree.  The bundles are the user's own work and
    outlive any checkout, so they do not belong under one.

    Both callers resolve through here -- `comod`'s CLI and the viewer's
    pop-out -- because the viewer having its own bundle directory is exactly
    how `comod import` comes to not see what the panel just wrote.  That
    invariant predates this change and is preserved by it.
    """
    explicit = str(read_settings().get(EXPORT_DIR_KEY) or "").strip()
    if explicit:
        return (Path(explicit),
                f"set in {user_config_path()} ({EXPORT_DIR_KEY})")
    return (Path(_DEFAULT_EXPORT_DIR), "the default export folder")


def export_dir() -> Path:
    """`export_root()[0]`.  Never None, and **not created here.**

    Creation belongs to whoever is about to write, so a read-only caller
    asking where bundles live does not bring a directory into existence as a
    side effect -- and a tool that only lists cannot make an empty folder
    appear and report "no bundles" as though it had looked.
    """
    return export_root()[0]


def save_export_dir(path) -> Path:
    """Remember ``path`` as the export base.  Returns the config file."""
    return write_settings(**{EXPORT_DIR_KEY: str(Path(path))})


#: What `clients_dir` returns when nothing is configured.  A path *under a
#: file* -- this module -- so it can never be a directory and can never be
#: created by accident, on any platform.
_NO_CLIENTS = Path(__file__).resolve() / "_no_clients_root_configured"


def clients_dir() -> Path:
    """`clients_root()[0]`, or a path that cannot exist.  **Never None.**

    Almost every caller wants ``clients_dir() / "5517"`` followed by
    ``.is_dir()``, and an `Optional` forces each of them to write the same
    two-line guard -- which is how one of them ends up written wrong and a
    test that meant to skip raises `TypeError` instead.  The sentinel is a
    child of *this file*, so the join is legal, the `is_dir()` is False, and
    nothing can ever make it true.

    Use `clients_root` when you need to tell the person *why* there is no
    answer; use this when "then there is nothing to measure" is the whole
    handling.
    """
    p, _why = clients_root()
    return p if p is not None else _NO_CLIENTS


def assets_dir() -> Path:
    """The asset collection -- the folder `Clients/` sits in.  **Never None.**

    ``ConquerAssets/`` holds ``Clients/`` beside ``derived/`` and
    ``_variants/``: the read-only installs, the expensive artefacts built from
    them, and the deliberately-modified copies used as controls.  Everything
    outside ``Clients/`` is addressed *relative to the collection*, which is
    the relationship `plugins/patch7878.derived_tables` already encodes, so
    this is the one place that has to be resolved.

    Derived from `clients_dir`, not configured separately -- two settings for
    a parent and its child is two ways to disagree.
    """
    return clients_dir().parent


#: Corpus locations this project has used, OLDEST-ONLY -- the current one comes
#: from `clients_dir()` and is never repeated here.  `d294e1c4` moved the asset
#: tree out of `C:\Claude`; receipts and figures recorded before that move still
#: name the old place.
#:
#: This file is the one place install-path literals are allowed to live
#: (`tests/test_sanitization.INSTALL_PATH_ALLOWED`), which is exactly why a
#: historical path belongs HERE rather than inlined at each caller that has to
#: cope with it.
_HISTORICAL_CLIENTS_DIRS = (
    r"C:\Claude\ConquerAssets\Clients",
)


#: The declared baseline set, beside this file. Data, not code: edited by a
#: human recording an owner declaration, never by a tool.
BASELINE_MEMBERS_PATH = Path(__file__).resolve().parent / "baseline_members.json"


def baseline_members() -> list:
    """The directory NAMES the owner has declared baseline members. Sorted.

    **PRESENCE ON DISK IS NOT MEMBERSHIP.** `clients_dir()` is a WORKING
    DIRECTORY -- the owner adds to it, patches in it, and replaces trees
    inside it -- so a frozen population measured by walking it goes stale with
    no signal. Membership is DECLARED, in `baseline_members.json`, and the
    owner's rule is recorded there verbatim:

        A baseline member is when the OWNER says it is.
        ASK, NEVER DECIDE THE OWNER IS DONE WITH A DIRECTORY.

    That second clause is the operative one for a program. A directory that
    looks complete, settled, or finished patching is **still undeclared**, and
    "it has stopped changing" is exactly the inference this forbids -- on
    2026-09-15 an install sat apparently settled for nine hours and was then
    replaced in place with its timestamps preserved from the source.

    Returns names, not paths, because the question "is this one in scope" is
    asked about a name far more often than a path. See `baseline_installs`
    for the paths that actually exist.
    """
    doc = json.loads(BASELINE_MEMBERS_PATH.read_text("utf-8"))
    return sorted(doc["members"])


def baseline_installs(root=None) -> list:
    """Declared baseline members that are present on disk, as paths. Sorted.

    **THE ONE ENUMERATION A CORPUS-WIDE CLAIM MAY MEASURE.** A test that walks
    `clients_dir()` directly is asserting over whatever the owner happens to
    have on the box this minute, which is how five frozen populations went red
    in one day without a commit: two installs arrived, and every "N of M"
    quoted against the old M became false.

    Undeclared directories are **out of scope, not rejected** -- they are not
    an error, not a finding, and not something a program may promote. Use
    `undeclared_installs` to surface them as an ASK.
    """
    base = Path(root) if root is not None else clients_dir()
    return sorted(p for n in baseline_members()
                  if (p := base / n).is_dir())


def undeclared_installs(root=None) -> list:
    """Directories present under `clients_dir()` that nobody has declared.

    **THIS EXISTS SO AN ARRIVAL IS AN ASK RATHER THAN A SILENCE.** Excluding
    undeclared trees from every population would otherwise make a new install
    invisible, and invisible is the one outcome the owner's rule rules out:
    the answer to "is this a member?" is theirs, so the tooling's job is to
    put the question in front of them, not to answer it either way.
    """
    base = Path(root) if root is not None else clients_dir()
    if not base.is_dir():
        return []
    declared = set(baseline_members())
    return sorted(p for p in base.iterdir()
                  if p.is_dir() and p.name not in declared)


def clients_search_dirs() -> list:
    """Every place a `Clients/` tree has lived, current first.

    **A SEARCH LIST, NEVER AN IDENTITY.**  The caller decides which entry (if
    any) is really the corpus -- by fingerprint, not by position -- so a stale
    entry costs one `is_dir()` and cannot produce a wrong answer.  Callers that
    want *the* corpus want `clients_dir()`; this is for readers of records
    written before a move.
    """
    out = [clients_dir()]
    for h in _HISTORICAL_CLIENTS_DIRS:
        q = Path(h)
        if q not in out:
            out.append(q)
    return out


def community_library() -> Optional[Path]:
    """The COmmunity Library folder the user pointed us at, or None.

    A setting, not a search: the library is a collection the person curates
    somewhere of their choosing, and there is no marker that makes guessing
    safe.  Unset means "not set up", which callers must report rather than
    substitute for.
    """
    raw = read_settings().get(COMMUNITY_LIBRARY_KEY)
    if not raw:
        return None
    return Path(str(raw))


#: Directory names this project **resolves at runtime instead of hardcoding**.
#:
#: Read by `tests/test_sanitization.py` check 3, which fails any shipped
#: absolute path naming one of them.  It lives here, next to the resolvers,
#: for one reason: the gate must describe the class by the vocabulary of the
#: thing that resolves it, not by a list of the machine locations people have
#: happened to use.  ``C:\\COMod``, ``D:\\Games`` and ``C:\\Program Files``
#: are all somebody's answer to "where"; these names are the *what*, and they
#: are stable across every machine.
#:
#: Add a name here when you add a tree that `coroot` can find -- the gate
#: starts guarding it in the same commit.
RESOLVED_TREE_NAMES: tuple[str, ...] = (
    "ConquerAssets",        # the asset collection: Clients/, derived/, _variants/
    "Clients",              # `clients_root` / `clients_dir`
    "COmmunityLibrary",     # `community_library`
    "COmmunity Library",    # ...and the spelling with the space
    "Installed",            # `installs_root`
)


def derived_overrides() -> dict:
    """``{out/-relative path: absolute path}`` the user has pointed us at.

    Saved in the same per-user config as everything else, so it survives a
    pull and is not a per-run flag somebody has to remember.
    """
    raw = _read_user_config().get("derived_overrides") or {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def derived_override(rel: str) -> Optional[Path]:
    """The named copy of one artefact, if it is named AND actually there.

    A configured path that does not exist returns None rather than a missing
    Path: the caller falls through to the normal search and the artefact reads
    as absent, which is true. `health` reports the broken setting separately --
    silently honouring a path that is not there would turn "you pointed me at
    the wrong file" into "the artefact does not exist".
    """
    named = derived_overrides().get(rel)
    if not named:
        return None
    p = Path(named)
    return p if p.exists() else None


def set_derived_override(rel: str, path) -> Path:
    """Point one artefact at an existing file, or clear it with ``None``."""
    cur = dict(derived_overrides())
    if path is None:
        cur.pop(rel, None)
    else:
        cur[rel] = str(Path(path).resolve())
    return write_settings(derived_overrides=cur)


def broken_derived_overrides() -> dict:
    """Configured artefact paths that are NOT on disk. Reported, never used."""
    return {k: v for k, v in derived_overrides().items()
            if not Path(v).exists()}


# ---------------------------------------------------------------------------
# the override gate
# ---------------------------------------------------------------------------
#
# WHY AN OVERRIDE NEEDS A GATE AT ALL.  `derived_override` was a developer
# convenience: a tree that cannot BUILD an artefact can be pointed at a copy
# (`health.py --use REL=PATH`), and COMod -- which ships the asset subset and
# no `build_opcodes.py` -- is the reason it exists.  Harmless, because the only
# thing on the other end was a file the same developer had just built.
#
# `tools/profilecheck.py` changes what is on the other end.  A pre-bootstrap
# profile is precomputed derived data the user did NOT derive, and shipping it
# is worth ~2,000 s of bootstrap.  Once profiles arrive that way, an override
# that returns whatever path it was handed is an **injection path for
# unverified derived data**, and it bypasses the entire verification design --
# which exists precisely because a stamp is not a check: `profilecheck` reads
# the user's own `c3.wdf`/`data.wdf` and refuses a sound-but-wrong-client table
# at 0% applicability, and nothing in it reads a provenance claim.
#
# THE SPLIT THIS GATE HAS TO GET RIGHT.  Not every derived artefact is a name
# table.  A verifier that refuses everything it does not understand breaks
# `--use` for the five artefacts `profilecheck` has no opinion about, and that
# flag has an owner ruling behind it: an artefact this tree cannot build must
# remain suppliable.  One that passes everything it does not understand is the
# old defect wearing a check.  So the two questions are kept apart, and the
# answer is a `verdict.Verdict` rather than a bool so that "I could not look"
# cannot collapse into "nothing to worry about":
#
#   * **Is a verifier's DOMAIN this artefact?**  `is_verifiable`, answered from
#     `VERIFIED_DERIVED` below.  Outside the domain the gate returns UNKNOWN
#     and `find_derived` honours the override anyway -- see the policy comment
#     at that call site for why, and `unverified_derived_overrides` for how it
#     stays visible rather than silent.
#   * **Inside the domain, what does the verifier say?**  PERMIT or REFUSE
#     from `profilecheck`, or UNKNOWN when the check could not be RUN at all
#     (no reachable verifier, no install to check against).  Inside the domain
#     UNKNOWN fails closed: being unable to check is not a pass.

#: Derived artefacts a verifier exists for, **by filename**.
#:
#: Held here rather than imported from `profilecheck` on purpose.  The domain
#: question must stay answerable in a tree where the verifier cannot be
#: reached -- otherwise "I could not import the verifier" would silently shrink
#: the guarded set to nothing, which is exactly the failure the gate exists to
#: prevent, arriving through the gate itself.  A filename, not an `out/...`
#: path, because the same table is legitimately supplied from anywhere.
#:
#: `tests/test_override_gate.py` pins this equal to `profilecheck.ARCHIVES`, so
#: teaching that module a new table extends the gate in the same commit or goes
#: red.  Add a name here only with the verifier that judges it.
VERIFIED_DERIVED: frozenset = frozenset({"c3_names.json", "data_names.json"})

_VERIFIER_UNSET = object()
_verifier: object = _VERIFIER_UNSET
#: ``{cache key: Verdict}``.  The check costs a WDF index read plus one forward
#: hash per shipped name -- MEASURED 0.42 s for 24,431 entries on 5517 -- and
#: `find_derived` is called per request by the viewer.  Keyed on the file's
#: identity (path, size, mtime) so replacing the file re-checks it.
_override_verdicts: dict = {}
_refused_overrides: dict = {}
_unverified_overrides: dict = {}
_refusals_announced: set = set()


def is_verifiable(rel: str) -> bool:
    """Whether any verifier has an opinion about this artefact's KIND.

    False is not "safe"; it is "unexamined".  The caller decides what to do
    with that, and `find_derived` writes down which it chose.
    """
    return PurePath(str(rel)).name in VERIFIED_DERIVED


def _profilecheck():
    """`tools/profilecheck`, or None if it cannot be reached from here.

    Reached through `_sibling` because the verifier sits beside this file in
    the Blender addon and one directory across in a checkout, and because no
    single import statement covers both.  A miss is None, and None fails
    CLOSED inside a verifier's domain -- never a quiet pass.
    """
    global _verifier
    if _verifier is not _VERIFIER_UNSET:
        return _verifier
    repo = _repo_dir()
    extra = [repo / "tools"] if repo is not None else []
    try:
        _verifier = _sibling("profilecheck", extra)
    except Exception:                                    # noqa: BLE001
        _verifier = None
    return _verifier


def _name_table_stem(pc, rel: str) -> Optional[str]:
    """Which archive of `pc.ARCHIVES` this filename is the table for."""
    name = PurePath(str(rel)).name
    for stem, fn in pc.ARCHIVES:
        if fn == name:
            return stem
    return None


def _load_name_table(path) -> tuple:
    """``(table, why_not)`` -- the first-pass dump's nested shape included.

    `coassets._read_name_table` accepts ``{"resolved": {...}}`` as well as the
    flat map, so the gate must too; refusing a shape the reader accepts would
    make the gate, not the artefact, the thing that broke.
    """
    try:
        raw = json.loads(Path(path).read_text("utf-8"))
    except OSError as e:
        return None, f"cannot be read: {e}"
    except ValueError as e:
        return None, f"is not JSON: {e}"
    if isinstance(raw, dict) and "resolved" in raw:
        raw = raw["resolved"]
    if not isinstance(raw, dict) or not raw:
        return None, ("is not a name table: expected a non-empty JSON object "
                      'of {"<hex hash>": "<asset path>"}')
    return raw, ""


def _refusal_text(rel, path, why, clauses=()) -> str:
    """What failed, and what to do about it.

    A bare "refused" turns a safety feature into a mystery and the user's next
    move is to delete the setting -- so every refusal names the artefact, the
    path, the clause that failed, and the two commands that resolve it.
    """
    lines = [f"refusing the derived override for {rel}",
             f"    -> {path}",
             f"  {why}"]
    for clause, says in clauses:
        lines.append(f"  {clause}: {says}")
    lines += [
        "  This artefact is verified before it is used, because a supplied "
        "name table is derived data you did not derive.",
        "  To see the full check:  py -3 tools/profilecheck.py --out-dir "
        f"\"{Path(path).parent}\"",
        f"  To clear the setting:   py -3 tools/health.py --use \"{rel}=\"",
    ]
    return "\n".join(lines)


def _override_verdict_uncached(rel, path, root):
    pc = _profilecheck()
    if pc is None:
        # Inside the domain this is UNKNOWN, not permit. The tree that cannot
        # reach the verifier is exactly the tree an unverified profile would
        # land in unnoticed, which is why `profilecheck.py` ships with the
        # gate rather than after it.
        return Verdict.unknown(
            ["tools/profilecheck.py could not be imported from here"],
            _refusal_text(rel, path,
                          "the verifier for this artefact could not be "
                          "reached, so it was not checked"))
    stem = _name_table_stem(pc, rel)
    if stem is None:
        return Verdict.unknown(
            [f"no verifier in this tree judges {PurePath(str(rel)).name}"],
            f"{rel} is outside every verifier's domain")
    table, why_not = _load_name_table(path)
    if table is None:
        return Verdict.refuse(_refusal_text(rel, path, f"the file {why_not}"))
    try:
        r = Path(root) if root else game_root()
    except Exception:                                    # noqa: BLE001
        r = None
    if r is None or not Path(r).is_dir():
        return Verdict.unknown(
            ["no install is configured, so applicability could not be read "
             "from this user's own archives"],
            _refusal_text(rel, path,
                          "there is no install to check it against. "
                          "Set one (py -3 tools/health.py --set <path>) or "
                          "pass --root, then try again"))
    try:
        rep = pc.check(r, {stem: table})
        arc = rep["archives"].get(stem) or {}
        if not arc.get("checked"):
            return Verdict.unknown(
                [f"{arc.get('why') or stem + '.wdf could not be read'}"],
                _refusal_text(rel, path,
                              f"it could not be checked against {r}: "
                              f"{arc.get('why') or 'archive unreadable'}"))
        v = pc.verdict(rep, {}, r)
    except Exception as e:                               # noqa: BLE001
        # A verifier that crashed did not clear anything. UNKNOWN, and the
        # call site inside the domain turns that into a refusal.
        return Verdict.unknown(
            [f"the verifier raised {type(e).__name__}: {e}"],
            _refusal_text(rel, path,
                          f"the verifier raised {type(e).__name__}: {e}"))
    if v.get("accept"):
        return Verdict.permit(
            f"{rel}: {v['verified_named']} of {v['archive_entries']} entries "
            f"named, {v['coverage_pct']}% coverage, 0 unsound "
            f"(tools/profilecheck.py, re-derived against {r})")
    bad = [(c["clause"], c["says"]) for c in v["clauses"]
           if "REFUSED" in c["says"] or c["clause"] == "blocked"]
    return Verdict.refuse(_refusal_text(
        rel, path, f"tools/profilecheck.py refuses it against {r}:", bad))


def override_verdict(rel: str, path, root=None):
    """`verdict.Verdict` on a user-named copy of one derived artefact.

    PERMIT only when a verifier looked and accepted.  REFUSE when one looked
    and rejected.  UNKNOWN when none looked -- and UNKNOWN carries *which*
    kind of blindness it was in `.unseen`, because the two mean opposite
    things: outside the domain nothing was ever going to look, while inside it
    something should have and could not.
    """
    p = Path(path)
    try:
        st = p.stat()
        ident = (str(p), st.st_size, st.st_mtime_ns)
    except OSError:
        ident = (str(p), -1, -1)
    key = (str(rel), ident, str(root or ""))
    hit = _override_verdicts.get(key)
    if hit is None:
        hit = _override_verdict_uncached(rel, p, root)
        _override_verdicts[key] = hit
    return hit


def _note_override(rel, path, v) -> None:
    """Record -- and, once, announce -- what the gate did with an override."""
    if v.is_permit:
        _refused_overrides.pop(str(rel), None)
        _unverified_overrides.pop(str(rel), None)
        return
    if v.is_unknown and not is_verifiable(rel):
        _unverified_overrides[str(rel)] = str(path)
        return
    _refused_overrides[str(rel)] = v.detail
    key = (str(rel), str(path), v.state)
    if key not in _refusals_announced:
        _refusals_announced.add(key)
        # stderr, once per process per setting. A refusal the user never sees
        # reads as "the artefact does not exist", which is the report that
        # gets the setting deleted instead of fixed.
        print(f"coroot: {v.detail}", file=sys.stderr)


def refused_derived_overrides() -> dict:
    """``{rel: why}`` for overrides the gate has refused **this process**.

    Populated by `find_derived`, so it answers for the artefacts that were
    actually asked for.  `health.py` walks all of `DERIVED`, which is what
    makes it a complete report there.
    """
    return dict(_refused_overrides)


def unverified_derived_overrides() -> dict:
    """``{rel: path}`` honoured with **no verification**, because no verifier
    in this tree has an opinion about that artefact.

    Not a fault and not a warning to act on -- it is the honest name for the
    residual the gate deliberately leaves, kept reportable so that "we check
    supplied artefacts" is never read as covering these.
    """
    return dict(_unverified_overrides)


def locate_table(root, logical: str) -> Optional[Path]:
    """The real path of an install-relative table, honouring an overlay.

    `root` is either a plain path -- joined directly, exactly as it always
    was -- **or an `AssetRoot`**, in which case the lookup goes through its
    `locate()` and obeys the install's real precedence: overlay, then loose,
    then archives. Passing the AssetRoot is what lets a staged overlay reach a
    table; passing a Path keeps every existing caller behaving identically.

    Duck-typed on `locate` rather than imported, because `coassets` imports
    THIS module -- naming `AssetRoot` here would be a cycle.

    None means the table is not in this install, which several callers treat
    as "no rows" rather than an error, and that leniency is preserved.

    A table resolving INSIDE an archive raises instead. Every reader of these
    tables takes a filesystem path (`json.loads(p.read_text())`,
    `tqdat.read_itemtype(p)`, `read_gamemap_dat(p)`), so an archive hit cannot
    be served, and returning None for it would report a table the install DOES
    ship as absent. MEASURED 2026-08-29 across all 31 installs: itemtype and
    GameMap are loose `.dat` on every one of them, archive-resident on none --
    so this refusal guards a case no install has, loudly rather than silently.

    RE-MEASURED 2026-09-07 across the **34** installs `clients_dir()` now
    holds, because "all 31" is a denominator and a denominator rots when a
    client is added. **Archive-resident on none: still 0**, which is the half
    this refusal rests on. But "loose on every one of them" is now 33 of 34:
    **CCO-snapshot-2026-08-24 ships NEITHER table** -- Classic Conquer 2.0 is
    a different layout, and absent is a third answer the original binary
    framing (loose / archive-resident) had no room for. It does not weaken the
    guard, which is about the archive case; it does mean a caller that reads
    "every install ships these loose" and skips its own existence check is
    wrong on one install. See `docs/claim_enumeration_audit_2026-09-07.md`.
    """
    find = getattr(root, "locate", None)
    if find is None:
        p = Path(root) / logical
        return p if p.is_file() else None
    loc = find(logical)
    if loc is None:
        return None
    if loc.real_path is None:
        raise NotImplementedError(
            f"table {logical!r} resolves inside {loc.source}; the readers for "
            f"these tables take a filesystem path. No measured install ships "
            f"one this way -- if you are seeing this, the layout changed.")
    return loc.real_path


def find_derived(rel: str, root=None) -> Optional[Path]:
    """Locate a derived artefact (an ``out/...`` path) for **reading**.

    This checkout first; failing that, the primary checkout when running in
    a linked worktree.  None when neither has it -- and a caller that would
    degrade without the artefact must then say so out loud rather than carry
    on with less (``meshtex.scan_meshes`` is the cautionary tale).  Never
    used for writing: everything that builds an artefact writes into its own
    checkout.

    Per-base trees (`PER_BASE`) resolve inside ``out/indexes/<base-id>/``, so
    switching installs cannot serve one client's facts as another's -- the
    failure that cost four visible bugs in the 6090 rebase.  **There is no
    fallback to the unkeyed path**: a missing index must read as "build me".

    Pass ``root`` when the caller knows which install it is asking about;
    anything holding two catalogues at once must.
    """
    rel = derived_rel(rel, root)
    # A path the user pointed us at, first. Some artefacts cannot be built in
    # every tree -- COMod ships the asset subset, so `out/opcodes.json` has no
    # builder there and "build me" is advice it cannot take. Naming an
    # existing copy is the way out, and it has to win over the search below or
    # it would only work when it was not needed.
    named = derived_override(rel)
    if named is not None:
        # THE GATE. `on_unknown` is stated here rather than defaulted because
        # the two UNKNOWNs mean opposite things and only this call site knows
        # which one it is holding:
        #
        #   * inside a verifier's domain -> REFUSE. A name table that could
        #     not be checked has not been checked, and the whole point of
        #     `profilecheck` is that a supplied table is not trusted on a
        #     claim. Failing closed costs a user who cannot reach the verifier
        #     the override; the message says how to proceed.
        #   * outside every domain -> PERMIT. `out/opcodes.json` is not a name
        #     table and nothing in this tree can judge one, so refusing it
        #     would delete a working feature (owner ruling: an artefact this
        #     tree cannot build must remain suppliable) in exchange for no
        #     verification at all. The residual is REPORTED rather than
        #     assumed away -- `unverified_derived_overrides`.
        v = override_verdict(rel, named, root)
        _note_override(rel, named, v)
        if v.permits(on_unknown=(REFUSE if is_verifiable(rel) else PERMIT)):
            return named
        # Fall through rather than return None: a refused override must not
        # also hide a copy this checkout legitimately holds. Same reason
        # `derived_override` falls through on a path that is not there.
    # `_repo_dir() is not None or overridden`, and not just `derived_root()`:
    # `derived_root` falls back to `Path.cwd()` when there is no checkout to
    # find, which is right for a WRITER (that is what `derived_path` has
    # always done) and wrong for a reader. In the vendored Blender copy
    # `_repo_dir()` is None and this arm used to be skipped entirely; reading
    # `<whatever Blender's cwd happens to be>/out/...` would be a new and
    # unasked-for lookup. Keep the old answer where nothing says otherwise.
    if derived_root_is_overridden() or _repo_dir() is not None:
        here = derived_root()
        if (here / rel).exists():
            return here / rel
    if os.environ.get(DERIVED_FALLBACK_VAR, "").strip().lower() in ("0", "off", "no"):
        return None
    # An OVERRIDDEN derived root is an isolated one, and an isolated tree
    # that reads the primary checkout's answers is the same contamination one
    # directory along. The caller said where its derived data lives; there is
    # no "and also over there".
    if derived_root_is_overridden():
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
        # Set but unusable: STOP.  Continuing to config and discovery is what
        # made the report agree with a resolver that had silently answered for
        # a different install -- the diagnostic must not describe a fallback
        # that no longer happens.
        refusal = env_refusal()
        if refusal is not None:
            return {"found": None, "tried": tried, "refused": {
                "var": ENV_VAR, "value": refusal.value,
                "missing": refusal.missing, "why": refusal.message()}}

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
    # The override gate's answers are about a file AND the install it was
    # checked against; changing the root changes the second half.
    _override_verdicts.clear()
    _refused_overrides.clear()
    _unverified_overrides.clear()
    _refusals_announced.clear()
    # The fingerprint memo is keyed on a stat signature, so it self-
    # invalidates when an install changes -- but a caller reaching for this
    # function is saying "forget what you think you know", and leaving one
    # cache behind would make that only mostly true.
    _FINGERPRINT_MEMO.clear()


def find(explicit=None, *, use_cache: bool = True) -> Optional[Found]:
    """Resolve the install root, or None.  Never prompts.

    Raises `RootNotHonoured` -- and *only* that -- when ``CO_ROOT`` is set to
    something that is not an install.  "Returns None on failure" is still the
    contract for *not finding* a root; this is the different case of having
    been told exactly which root to use and being unable to comply, where the
    old behaviour was to answer for a different install without saying so.
    An explicit argument still wins over the environment, because a caller
    that names a path is not asking about ``CO_ROOT`` at all.
    """
    if explicit:
        p = Path(explicit)
        if looks_like_root(p):
            return Found(p.resolve() if p.exists() else p, "explicit",
                         "path given on the command line or in code")
        return None
    refusal = env_refusal()
    if refusal is not None:
        raise refusal
    if use_cache and "found" in _cache:
        return _cache["found"]
    rep = search_report()
    got = rep["found"]
    out = Found(Path(got["path"]), got["source"], got["detail"]) if got else None
    _cache["found"] = out
    _cache["report"] = rep
    return out


def last_report(explicit=None) -> dict:
    """The search report for the current process, computing it if needed.

    Never raises: this is the *diagnostic*, and the health check calls it
    precisely when resolution has gone wrong.  A refused ``CO_ROOT`` comes
    back as ``report["refused"]`` rather than an exception, so the page that
    exists to explain the failure can still render it.
    """
    if explicit:
        return search_report(explicit)
    if "report" not in _cache:
        try:
            find()
        except RootNotHonoured:
            return search_report()
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
    """A Path that is safe to use as a default argument value.

    Returns the discovered root when there is one, and the conventional path
    otherwise -- so importing a module never fails on a machine without the
    game, and the failure instead lands where the assets are actually opened,
    with a message naming the missing files.

    **`RootNotHonoured` deliberately propagates**, including out of the
    import-time ``DEFAULT_ROOT = coroot.default_root()`` in `coassets`.  The
    "never fails on import" guarantee is about a machine that has *no* install;
    a ``CO_ROOT`` pointing at nothing is the opposite case -- somebody named a
    base -- and quietly substituting the conventional path there would re-open
    the fall-through this refusal exists to close, one layer further out.
    """
    got = find()
    return got.path if got else Path(CONVENTIONAL_ROOT)


#: Redistributables that ship with any Windows build and answer no question
#: about the game.  Dropped by `binaries` so a discovered set stays signal.
THIRD_PARTY_BINARIES: frozenset = frozenset({
    "d3dcompiler_47.dll", "d3dx10_43.dll", "d3dx9_43.dll",
    "xaudio2_9redist.dll", "discord_game_sdk.dll", "crashpad_handler.exe",
    "mfc42.dll", "msvcp60.dll", "msvcrt.dll", "msvcp90.dll", "msvcr90.dll",
})


def bin_dir(root=None) -> Path:
    """Where this install keeps its executables and engine DLLs.

    **Discovered, not assumed.**  The two families disagree: Classic Conquer
    2.0 keeps them under ``bin/64``, and *no* official patch client
    (5017-6090) has that directory at all -- theirs sit at the install root.

    Returning ``bin/64`` unconditionally is why the whole `dump_*` family
    analysed nothing under a patch client while reporting success six times,
    and why ``out/dll/exports.json`` was once truncated from 273,983 bytes to
    ``{}`` by a run whose only mistake was that the configured root had moved
    on.  Thirteen tools resolve their target directory through this function,
    so it is the one place worth teaching.
    """
    base = Path(root) if root else default_root()
    nested = base / "bin" / "64"
    return nested if nested.is_dir() else base


def binaries(root=None) -> list:
    """Every PE worth analysing in this install, name-sorted.

    Discovered rather than enumerated.  A hardcoded target list is the other
    half of what made `dump_*` CCO-only: it named six modules, and five of
    the six official installs ship none of them.  Discovery also picks up the
    ones that matter per client -- ``Conquer.exe``, ``RoleView.dll``,
    ``C3_CORE_DLL.dll`` -- which no CCO-era list would ever have mentioned.
    """
    d = bin_dir(root)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir()
                   if p.is_file()
                   and p.suffix.lower() in (".dll", ".exe")
                   and p.name.lower() not in THIRD_PARTY_BINARIES),
                  key=lambda p: p.name.lower())


#: Where the game client sits relative to an install root.  Used only to
#: GENERATE candidates -- every one is validated as a real PE by
#: `client_binaries`, which is the whole point (see its docstring: at the root
#: of 22 installs this name is a 41-byte text file).  Same contract as
#: `_INSTALL_DIR_NAMES` above.
#:
#: MEASURED 2026-08-29 over 36 directories under `clients_dir()`; the families
#: and their counts are in `client_binaries`.
CLIENT_RELPATHS: tuple[str, ...] = (
    "Conquer.exe",              # official patch clients, 4274-6090 era (+6716)
    "Env_DX8/Conquer.exe",      # the 6609+ renderer split -- TWO per install
    "Env_DX9/Conquer.exe",
    "bin/64/ImConquer.exe",     # Classic Conquer 2.0
)


@dataclass(frozen=True)
class ClientBinary:
    """One game-client PE inside one install.

    `relpath` is POSIX-form and relative to the install root, so it is the same
    string on every machine and can be recorded.  `renderer` is `"DX8"`,
    `"DX9"` or `""` -- it is a real distinction and not cosmetic: the two
    `Env_DX*` clients of a single install are DIFFERENT BUILDS with different
    build keys, measured, not assumed.
    """

    path: Path
    install: Path
    relpath: str
    renderer: str = ""

    @property
    def process(self) -> str:
        return self.path.name


def _is_pe(p: Path) -> bool:
    """True if `p` starts `MZ` and its `e_lfanew` points at `PE\\0\\0`.

    The header, not the extension.  This is the discriminator the whole
    function turns on and it must be evidence: the thing being rejected is
    named `Conquer.exe` and would pass any name test ever written.
    """
    try:
        with open(p, "rb") as fh:
            head = fh.read(0x40)
            if len(head) < 0x40 or head[:2] != b"MZ":
                return False
            off = int.from_bytes(head[0x3C:0x40], "little")
            fh.seek(off)
            return fh.read(4) == b"PE\0\0"
    except OSError:
        return False


def client_binaries(root=None) -> list:
    """Every real game-client PE in this install.  **Validated, not named.**

    Returns `ClientBinary` records, `relpath`-sorted.  Empty is a real answer:
    two directories under `clients_dir()` hold no client at all.

    WHY THIS EXISTS.  Ten tools and the build registry look for
    ``<install>/Conquer.exe``.  MEASURED 2026-08-29 across 36 directories:

    * **9** installs keep the client there -- 4274 5017 5065 5165 5517 6090
      6256 6271 **6716**;
    * **23** put it in ``Env_DX8/`` *and* ``Env_DX9/`` -- 6609 onwards, plus
      Zephyr -- and **22 of those ship a 41-byte ASCII file at the old path**
      reading *"Click on Player.exe to log into the game."*;
    * **1** is Classic Conquer 2.0 at ``bin/64/ImConquer.exe``.

    So the old lookup saw 9 of 33 installs and **56 client PEs collapse to 53
    distinct build keys**, of which the registry knew 6.  The failure was
    silent in the worst way: the path existed and opened, so nothing raised --
    it just was not a program.  *The name is not the program.*

    THE 41-BYTE FILE IS WHY `_is_pe` READS THE HEADER.  A suffix check, a size
    check or any name rule accepts it; only asking whether it is a PE does not.
    And the note it contains is itself wrong -- it says ``Player.exe`` and no
    install has one -- so parsing the declaration would have been worse than
    ignoring it.

    TWO CLIENTS PER INSTALL IS THE NORMAL CASE HERE, not a duplicate to
    de-duplicate: ``Env_DX8/Conquer.exe`` and ``Env_DX9/Conquer.exe`` have
    different build keys in all 23 installs.  A caller that takes ``[0]`` and
    calls it "the client" has silently picked the DX8 renderer.

    **A DIRECTORY NAME IS NOT A PATCH NUMBER.**  ``6716`` looked like an
    out-of-order anomaly -- first family, sitting between 6707 and 6772 which
    are second family -- until its own ``version.dat`` was read: it says
    **6271**, and its client is byte-identical to ``6271``'s
    (md5 ``18c230409353847b4e9362e3c14ad189``, 79 root entries each).  It is
    not a 6716-era install.  ``7632`` and ``7682`` both declare **7622** and
    likewise ship one identical client.  Two independent pairs, so this is the
    rule and not an accident: resolve the patch by reading
    ``<install>/version.dat`` -- which is what
    `coprofile.identity.read_patch_marker` does -- and never by parsing the
    folder name.  A build keyed on a directory name would have recorded three
    patches that do not exist.
    """
    base = Path(root) if root else default_root()
    out = []
    for rel in CLIENT_RELPATHS:
        p = base / rel
        if not p.is_file() or not _is_pe(p):
            continue
        parent = PurePath(rel).parent.name
        out.append(ClientBinary(
            path=p, install=base, relpath=rel,
            renderer=parent[4:] if parent.startswith("Env_") else ""))
    return sorted(out, key=lambda c: c.relpath)


def install_root_of(exe) -> Optional[Path]:
    """The install `exe` is the client of, or None.  **Verified, not guessed.**

    Walks up at most as far as `CLIENT_RELPATHS` is deep and returns the first
    ancestor that `client_binaries` **agrees** owns this exact file.  That
    agreement is the whole point and it is why this is not the upward search
    `coprofile.identity.identify_path` refuses to do internally: it does not
    look for the nearest ``version.dat`` and hope, it asks the resolver whether
    the candidate root really resolves to this path.  A directory that merely
    sits above the file, or one that has a ``version.dat`` of its own for some
    other install, is not accepted.

    **Both conditions are load-bearing, and the second was added because the
    first alone got it wrong.**  Agreement from `client_binaries` is not enough
    on its own: ``Env_DX9/`` *contains* a file called ``Conquer.exe``, so it
    answers to the flat-family candidate and the nearest ancestor of
    ``7878/Env_DX9/Conquer.exe`` resolved to ``7878/Env_DX9`` -- which has no
    ``ini/`` and no archive and is not an install.  So the candidate must also
    satisfy `looks_like_root`, which is this module's existing answer to "is
    this an install" and was already right about it.

    None is a real answer: a client outside the corpus layout has no install
    root that can be established, and a caller must then leave the patch
    unread rather than substitute a plausible one.
    """
    p = Path(exe).resolve()
    depth = max(len(PurePath(r).parts) for r in CLIENT_RELPATHS)
    for up in range(1, depth + 1):
        if up >= len(p.parts):
            break
        cand = p.parents[up - 1]
        if not looks_like_root(cand):
            continue
        if any(c.path.resolve() == p for c in client_binaries(cand)):
            return cand
    return None


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
    ap.add_argument("--print", dest="print_what", metavar="WHAT",
                    choices=("root", "clients", "assets", "library",
                             "installs", "export"),
                    help="print one resolved path and nothing else, for shell "
                         "scripts: root | clients | assets | library | "
                         "installs | export. Exit 3 and print nothing if it is "
                         "not configured, so `if not defined` is a real answer "
                         "(export always resolves -- it has a default). With "
                         "--json, export prints {path, why} instead, so a "
                         "script can also say where the folder came from")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    # Before anything else: a caller asking for one path wants one line on
    # stdout and no report around it.  A .cmd or .ps1 has no other way in --
    # without this they hardcode the path, which is what check 3 exists to
    # stop, and telling somebody "resolve it through coroot" is empty if
    # coroot only speaks Python.
    if a.print_what:
        why = None
        if a.print_what == "clients":
            p, _why = clients_root()
        elif a.print_what == "assets":
            p, _why = clients_root()
            p = p.parent if p is not None else None
        elif a.print_what == "library":
            p = community_library()
        elif a.print_what == "installs":
            p = installs_root()
        elif a.print_what == "export":
            # Never None: `export_root` falls back to the one default this
            # file is allowed to spell, so there is no exit-3 branch here.
            # The `why` rides along for --json because this is the path a
            # human is SHOWN and asked to trust (see `export_root`), and a
            # .ps1 relaying it has no other way to say where it came from.
            p, why = export_root()
        else:
            got = find()
            p = got.path if got else None
        if p is None:
            return 3
        if a.json and why is not None:
            print(json.dumps({"path": str(p), "why": why}))
        else:
            print(p)
        return 0

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


# BASELINE RE-SCOPE, 2026-09-19. The owner removed 6609.cn, CCO and Installers
# from core/baseline_members.json -- "remove the three from baseline members,
# then land the windows" -- so the baseline is 33 members. The dated figures
# above were measured over the earlier population and stand as records of that
# measurement. Over the 33, as re-derived by tests/test_claim_enumeration.py:
#   itemtype.dat and GameMap.dat ship loose on 32 of the 33;
#   archive-resident on none, still 0 over the 33
#
# BASELINE RE-SCOPE (2), 2026-09-19. Later the same day the owner deleted 6716
# and 7682 (byte-level copies of 6271 and 7632), renamed 7632 to 7622 (its
# build stamp), and declared 7217 7250 7275 7280 7320 7336 7373 7387 7506 7535
# 7562 7589 baseline members, so core/baseline_members.json holds 43. The
# 33-member block above is kept as the record of that measurement. Over the
# 43, re-measured per install by tests/test_claim_enumeration.py (7622 measures
# exactly what 7632 recorded; the twelve new members were walked, not assumed):
#   itemtype.dat and GameMap.dat ship loose on 42 of the 43;
#   CCO-snapshot-2026-08-24 still ships neither. Archive-resident on none,
#   still 0 over the 43 -- AssetRoot.in_archives() on both tables for every
#   member, an instrument shown to fire on both the TPD and WDF containers.
