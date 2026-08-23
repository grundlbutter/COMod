#!/usr/bin/env python3
r"""
health.py -- "will this actually run here?", answered in one place.

The Asset Viewer calls this on first run and shows the result; you can also
run it without a browser:

    py -3 tools/coviewer.py --health        # same report, on the console
    py -3 tools/health.py                   # ditto
    py -3 tools/health.py --json
    py -3 tools/health.py --bootstrap       # build the missing data in out/

It checks, and says how to fix, every prerequisite the repository actually
has:

  * **the game install** -- where it was found and *how* (registry, default
    path, `CO_ROOT`, saved config), and that `c3.wdf`, `data.wdf`, `ini/` and
    `bin/64/` all exist and can be opened;
  * **Python** -- 3.11 or newer;
  * **Pillow** (required: every texture the viewer shows is re-encoded to PNG
    through it) and **numpy** (optional for browsing, required to *generate*
    thumbnails);
  * **derived data** -- `out/` is generated and gitignored, so a fresh clone
    starts empty, and until the WDF filename recovery has run the catalogue
    can only see loose files.  `--bootstrap` runs the generators in the order
    they depend on each other;
  * **thumbnails** -- whether `out/thumbs/` has been generated, how complete
    it is, and what generating it would cost in time and disk on *this*
    machine.  Generating them is **opt-in**: nothing here starts a render.

Nothing here writes to the game install.  The report is written to
`out/health.json` so it can be inspected after the fact.

Design note: this module imports `tools/thumbs.py` for the thumbnail facts
rather than restating them, and never renders anything itself.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                      # noqa: E402
import provenance                                  # noqa: E402

REPORT_PATH = REPO / "out" / "health.json"

#: Minimum Python this codebase is known to work on.  Everything here is
#: stdlib plus numpy/Pillow; the syntax floor is `X | Y` unions in annotations
#: with `from __future__ import annotations`, plus `dict[str, int]` builtins.
#: Developed and verified on 3.14.
MIN_PYTHON = (3, 11)
VERIFIED_PYTHON = "3.14.6"

#: Measured on the full corpus of this install, `--size 256 --ss 2`.
#: Used to tell someone what they are agreeing to *before* they agree.
THUMB_FACTS = {
    "meshes": {"count": 4950, "megabytes": 124},
    "textures": {"count": 66834, "megabytes": 486},
    "manifests": {"count": 2, "megabytes": 27},
}
#: Wall-clock x worker-count from the reference run: 82 s for meshes and 144 s
#: for textures at 20 workers.  Divided by the worker count to estimate a
#: different machine.  Scaling is not perfectly linear -- disk and the serial
#: manifest write do not parallelise -- so the estimate is reported as a range
#: with the low end at the linear figure.
MESH_CORE_SECONDS = 82 * 20
TEXTURE_CORE_SECONDS = 144 * 20
#: The reference run itself, quoted so nobody has to trust the extrapolation.
REFERENCE_RUN = ("226 s total (82 s meshes + 144 s textures) with 20 worker "
                 "processes on a 24-core desktop; ~14 s for a warm re-run")


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------

def check_python() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return {
        "name": "Python",
        "ok": ok,
        "value": f"{v.major}.{v.minor}.{v.micro}",
        "detail": f"{sys.executable}  ({platform.python_implementation()})",
        "requirement": f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
                       f"(developed and verified on {VERIFIED_PYTHON})",
        "fix": None if ok else
               "Install Python "
               f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer from python.org, "
               "then re-run.",
    }


def _package(name: str, import_name: str, required: bool, why: str,
             fix: str) -> dict:
    rec = {"name": name, "required": required, "present": False,
           "version": None, "why": why, "fix": fix}
    try:
        mod = __import__(import_name)
        rec["present"] = True
        rec["version"] = getattr(mod, "__version__", "unknown")
        rec["fix"] = None
    except Exception as e:                          # pragma: no cover
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["ok"] = rec["present"] or not required
    return rec


def check_packages() -> list[dict]:
    return [
        _package("Pillow", "PIL", True,
                 "every texture the viewer displays is re-encoded to PNG "
                 "through Pillow, and staged .dds files are written with it",
                 "pip install pillow"),
        _package("numpy", "numpy", False,
                 "optional for browsing (texture decode is just slower "
                 "without it) but REQUIRED to generate thumbnails -- "
                 "tools/thumbs.py is a pure-numpy renderer",
                 "pip install numpy"),
    ]


def check_install(explicit=None) -> dict:
    """Where the game is, how that was decided, and whether it is complete."""
    rep = coroot.search_report(explicit) if explicit else coroot.last_report()
    out: dict = {
        "found": bool(rep.get("found")),
        "searched": rep.get("tried") or [],
        "searchedCount": rep.get("triedCount", len(rep.get("tried") or [])),
    }
    if rep.get("found"):
        f = rep["found"]
        out.update({"path": f["path"], "source": f["source"],
                    "detail": f["detail"]})
        out["files"] = coroot.describe_root(f["path"])
        out["ok"] = out["files"]["ok"]
        if not out["ok"]:
            bad = [p["name"] for p in out["files"]["parts"]
                   if not (p["exists"] and p["readable"])]
            out["fix"] = ("These are missing or unreadable: "
                          + ", ".join(bad)
                          + ". Repair or reinstall the client, or point the "
                            "tools at a different copy with "
                            "`py -3 core/coroot.py --set DIR`.")
    else:
        out.update({"path": None, "source": None, "ok": False,
                    "detail": "no install found",
                    "fix": coroot.RootNotFound(rep).message()})
    # An explicitly-configured path that got rejected is worth saying out
    # loud: silently falling through to auto-discovery looks like the setting
    # was ignored.
    out["rejectedOverrides"] = [
        t for t in out["searched"]
        if not t["ok"] and t["source"] in ("explicit", "env", "repo-config",
                                           "user-config")
    ]
    #: How the person can override whatever was decided.
    out["overrides"] = {
        "cli": "--root \"D:\\path\\to\\install\"  (any tool)",
        "env": f"{coroot.ENV_VAR}=D:\\path\\to\\install",
        "save": "py -3 core/coroot.py --set \"D:\\path\\to\\install\"",
        "repoFile": str(coroot.repo_config_path()),
        "userFile": str(coroot.user_config_path()),
    }
    return out


# ---------------------------------------------------------------------------
# derived data -- the one-off build a fresh clone needs
# ---------------------------------------------------------------------------

#: `out/` is generated and gitignored, so a fresh clone starts empty.  Most of
#: it is optional, but the WDF filename tables are not: the archives store a
#: *hash* of each filename, not the name, so until they are recovered the
#: catalogue can only see the ~53,000 loose files and none of the ~25,000
#: archived ones.
#:
#: Order matters.  `wdf_recover.py` must run before `meshtex.py`, because
#: meshtex caches a mesh index built from whatever name tables existed at the
#: time.  Running them out of order used to leave a silently half-sized index
#: that looked fine (found the hard way while verifying a relocated checkout);
#: meshtex now refuses to persist a census taken without the name tables and
#: discards a cache that no longer covers the universe, so the order is
#: enforced rather than merely documented.  Linked git worktrees inherit all
#: of these artefacts from the primary checkout (`coroot.find_derived`).
#:
#: (relative artefact, what produces it, roughly how long, why it matters)
DERIVED = [
    ("out/wdf/c3_names.json",
     ["tools/wdf_recover.py"], "5-9 min",
     "recovers 24,426 of the 24,757 filenames hashed in the two .wdf "
     "archives. Without it nothing inside the archives can be named, "
     "browsed or exported."),
    ("out/dll/wdf_name_recovery.json",
     ["tools/wdf_names.py"], "20 s",
     "the smaller name table coassets.py loads by default."),
    ("out/meshtex/coverage.json",
     ["tools/meshtex.py", "--coverage"], "15 s",
     "which texture belongs to which mesh. Drives the merged catalogue rows "
     "and the thumbnail work list. MUST be built after wdf_recover."),
    ("out/effects/linkage.json",
     ["tools/effects.py", "--linkage"], "6 s",
     "weapon/action to 3D effect linkage."),
    ("out/opcodes.json",
     ["tools/build_opcodes.py"], "2 s",
     "the protocol opcode table."),
    # Genuinely optional, and reported for the same reason CoEmu is: a
    # gitignored dependency is otherwise invisible. Skipping it costs the
    # pictures and nothing else -- skins resolve token by token, so an unbuilt
    # `classic` falls back to the `plain` skin's value for each missing image
    # and the in-game UI draws a flat gold frame instead of a bitmap one.
    ("out/skins/classic/manifest.json",
     ["tools/skinbuild.py"], "1 s",
     "the `classic` UI skin: the 9-slice frame and window captions from the "
     "game's own data/Interface/. Optional -- docs/ui.md §3."),
]


def check_local_server() -> dict:
    """Rust and CoEmu -- the local test server. **Entirely optional.**

    Reported because a gitignored dependency is otherwise invisible: a fresh
    clone has no `server/` directory and nothing tells you a toolchain exists.
    Nothing here being present is a perfectly healthy state -- `client/` and its
    whole test suite run on Python alone against `client/simserver.py`. CoEmu
    only buys you a second, independent peer to send real packets at.

    CoEmu is GPL-3 and lives in the gitignored `server/`; see
    docs/server_setup.md for why it is not vendored.
    """
    coemu = REPO / "server" / "coemu"
    exe = ".exe" if os.name == "nt" else ""
    cargo = shutil.which("cargo")
    if cargo is None:
        candidate = Path.home() / ".cargo" / "bin" / f"cargo{exe}"
        cargo = str(candidate) if candidate.exists() else None

    built = [n for n in ("auth-server", "game-server")
             if (coemu / "target" / "debug" / f"{n}{exe}").exists()]
    cloned = (coemu / "Cargo.toml").exists()
    db = coemu / "data" / "coemu.db"
    maps = coemu / "data" / "GameMaps" / "map"
    dmaps = len(list(maps.glob("*.DMap"))) if maps.exists() else 0

    ready = bool(cargo and cloned and len(built) == 2 and db.exists())
    parts = [
        {"name": "Rust (cargo)", "present": bool(cargo),
         "detail": cargo or "not installed",
         "fix": "https://rustup.rs, then `rustup default nightly`. On Windows, "
                "`rustup default nightly-x86_64-pc-windows-gnu` avoids a "
                "multi-GB Visual Studio Build Tools install if you already "
                "have MinGW."},
        {"name": "CoEmu clone", "present": cloned,
         "detail": "server/coemu" if cloned else "not cloned",
         "fix": "py -3 tools/setup_coemu.py --step clone"},
        {"name": "CoEmu binaries", "present": len(built) == 2,
         "detail": ", ".join(built) if built else "not built",
         "fix": "py -3 tools/setup_coemu.py --step patch --step build"},
        {"name": "CoEmu database", "present": db.exists(),
         "detail": "data/coemu.db" if db.exists() else "not created",
         "fix": "py -3 tools/setup_coemu.py --step db"},
        {"name": "CoEmu map data", "present": dmaps > 0,
         "detail": f"{dmaps} .DMap file(s)" if dmaps else "none copied",
         "fix": "py -3 tools/setup_coemu.py --step maps  "
                "(reads your game install; login works without it, "
                "entering the world does not)"},
    ]
    return {
        "optional": True,
        "ok": True,                       # never fails the health check
        "ready": ready,
        "parts": parts,
        "why": "an optional local Conquer Online 5017 server (CoEmu, GPL-3, "
               "kept in the gitignored server/) for client/ to talk to. "
               "Not needed: py -3 tests/test_client.py and "
               "py -3 -m client selftest run on Python alone.",
        "fix": "py -3 tools/setup_coemu.py --check",
        "docs": "docs/server_setup.md",
    }


def check_derived(root=None) -> dict:
    """Which generated artefacts a fresh clone is still missing.

    An artefact counts as present when `coroot.find_derived` can read it --
    from this checkout, or from the primary checkout when this is a linked
    git worktree.  Inherited artefacts are reported as such.

    ``root`` names **which install** to ask about, and passing it is not
    optional for anything that browses more than one.  Most of `DERIVED` is
    per-base (`coroot.PER_BASE`), so without a root this resolves every
    artefact against whatever install is *configured* -- which is how the
    viewer came to report "Derived data: built" while showing a client that
    had no index at all, and no way to say so.  The user was told everything
    was fine and then waited 56 s for a list.  MEASURED on 7878.

    "Inherited" means *another checkout*, so the comparison has to be against
    the same keyed path `find_derived` resolved (`coroot.derived_rel`), not
    the plain literal.  Against the literal, every per-base artefact reads as
    inherited the moment indexes are keyed -- which is exactly how this
    printed on the first run after the change."""
    artefacts = []
    for rel, argv, cost, why in DERIVED:
        p = coroot.find_derived(rel, root)
        found = p is not None and p.is_file()
        # The command has to name the install too, for the same reason the
        # lookup does: `py -3 tools/meshtex.py --coverage` builds for the
        # CONFIGURED root, which is not necessarily the one being browsed.
        cmd = "py -3 " + " ".join(argv)
        if root is not None:
            cmd += f' --root "{root}"'
        artefacts.append({
            "path": rel, "exists": found,
            "inherited": found and not (
                REPO / coroot.derived_rel(rel, root)).is_file(),
            "bytes": p.stat().st_size if found else 0,
            "command": cmd, "cost": cost, "why": why,
        })
    missing = [a for a in artefacts if not a["exists"]]
    return {
        "dir": str(REPO / "out"),
        "artefacts": artefacts,
        "missing": [a["path"] for a in missing],
        "inherited": [a["path"] for a in artefacts if a["inherited"]],
        "ok": not missing,
        "fix": ("Build it in one step: `py -3 tools/health.py --bootstrap` "
                "(about 6-10 minutes, once). Or run each command listed in "
                "the report, in order."),
    }


def check_provenance(explicit=None) -> dict:
    """Which derived artefacts can say what install they were built from.

    Reports rather than judges, and that is deliberate for now.  On the day
    this lands *every* artefact in the tree is unstamped, so failing on
    "unstamped" would make `health.py` red for everyone immediately -- and a
    check people have to switch off to get work done has stopped being a
    check.  Only `foreign` is treated as a fault, because it is the one
    verdict backed by positive evidence: the artefact itself says it came
    from another client.

    `unclassified` is the number worth watching. Those artefacts live in a
    namespace that cannot distinguish installs at all, so nothing about them
    can ever be checked -- ``out/dll/`` is the standing example, holding two
    installs' answers under one set of filenames.
    """
    try:
        rep = provenance.audit(REPO, explicit)
    except Exception as e:                           # pragma: no cover
        return {"ok": True, "available": False,
                "error": f"{type(e).__name__}: {e}", "scanned": 0,
                "foreign": [], "unclassified": [], "unstamped": []}
    rep["available"] = True
    return rep


def bootstrap(only_missing: bool = True) -> int:
    """Run the derived-data builders, in dependency order.

    Deliberately a thin sequencer over the existing tools -- it adds the
    ordering constraint and nothing else.
    """
    import subprocess                                # noqa: PLC0415
    todo = [(rel, argv, cost) for rel, argv, cost, _ in DERIVED
            if not only_missing or coroot.find_derived(rel) is None]
    if not todo:
        print("derived data is already built; nothing to do "
              "(use --bootstrap-all to force)")
        return 0
    print(f"building {len(todo)} artefact(s) into {REPO / 'out'}\n")
    for i, (rel, argv, cost) in enumerate(todo, 1):
        cmd = [sys.executable, str(REPO / argv[0]), *argv[1:]]
        print(f"[{i}/{len(todo)}] {rel}  (~{cost})")
        print(f"        {' '.join(argv)}")
        t0 = time.time()
        r = subprocess.run(cmd, cwd=str(REPO), stdout=subprocess.DEVNULL)
        if r.returncode != 0:
            print(f"        FAILED (exit {r.returncode}). Re-run it directly "
                  f"to see why:\n        py -3 {' '.join(argv)}")
            return r.returncode
        got = REPO / rel
        print(f"        done in {time.time() - t0:.0f} s"
              + (f", {got.stat().st_size / 1e6:.1f} MB" if got.is_file() else ""))
    print("\nderived data built.")
    return 0


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def default_jobs() -> int:
    """The worker count tools/thumbs.py would pick if not told otherwise."""
    return max(1, (os.cpu_count() or 2) - 1)


def estimate_seconds(jobs: int, meshes: bool = True,
                     textures: bool = True) -> tuple[int, int]:
    """(optimistic, pessimistic) seconds for a cold run at `jobs` workers."""
    core = 0
    if meshes:
        core += MESH_CORE_SECONDS
    if textures:
        core += TEXTURE_CORE_SECONDS
    low = max(20, int(core / max(1, jobs)))
    # Doubling, not tripling: the reference run's own wall clock sat at the
    # linear figure, and a slower machine loses to per-core speed and disk
    # rather than to scaling.  On a 4-core laptop this lands at roughly
    # 25-50 minutes for the full run, which matches the range people report.
    return low, low * 2


def human_duration(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} s"
    m = seconds / 60.0
    return f"{m:.0f} min" if m < 90 else f"{m / 60:.1f} h"


def thumbnail_state(server: str = "") -> dict:
    """What the thumbnail cache currently holds, and what filling it would
    cost.  With ``server``, reports that library view's own cache
    (out/thumbs/servers/<name>/) instead of the install's.

    Everything factual comes from `tools/thumbs.py` (its output directory and
    manifest); this function only reads and describes.
    """
    import thumbs                                   # noqa: PLC0415

    if server:
        out_dir = thumbs.REPO / "out" / "thumbs" / "servers" / server
        manifest = out_dir / "manifest.json"
    else:
        out_dir = thumbs.OUT_DIR
        manifest = thumbs.MANIFEST
    state: dict = {
        "dir": str(out_dir),
        "exists": out_dir.is_dir(),
        "manifest": str(manifest),
        "meshes": 0, "textures": 0, "bytes": 0,
        "generated": None,
    }
    if manifest.is_file():
        try:
            doc = json.loads(manifest.read_text("utf-8"))
            counts = doc.get("counts") or {}
            state.update({
                "meshes": int(counts.get("meshes", 0) or 0),
                "textures": int(counts.get("textures", 0) or 0),
                "bytes": int(counts.get("bytes", 0) or 0),
                "generated": doc.get("generated"),
            })
        except (OSError, ValueError) as e:
            state["error"] = f"manifest unreadable: {e}"

    if server:
        state["server"] = server
        # a library's corpus size is not the install's; report presence
        # rather than pretending to know the denominator.
        state["status"] = ("none" if not (state["meshes"] or state["textures"])
                           else "generated")
        return state

    want_m = THUMB_FACTS["meshes"]["count"]
    want_t = THUMB_FACTS["textures"]["count"]
    have_m, have_t = state["meshes"], state["textures"]
    if have_m == 0 and have_t == 0:
        state["status"] = "none"
    elif have_m >= want_m * 0.95 and have_t >= want_t * 0.95:
        state["status"] = "complete"
    elif have_m >= want_m * 0.95:
        state["status"] = "meshes-only"
    else:
        state["status"] = "partial"

    jobs = default_jobs()
    total_mb = sum(f["megabytes"] for f in THUMB_FACTS.values())
    mesh_mb = (THUMB_FACTS["meshes"]["megabytes"]
               + THUMB_FACTS["manifests"]["megabytes"])
    m_low, m_high = estimate_seconds(jobs, True, False)
    a_low, a_high = estimate_seconds(jobs, True, True)
    state["plan"] = {
        "cpus": os.cpu_count(),
        "jobs": jobs,
        "reference": REFERENCE_RUN,
        "resumable": True,
        "options": [
            {
                "id": "meshes",
                "label": "Meshes only  (recommended)",
                "count": want_m,
                "megabytes": mesh_mb,
                "estimate": f"{human_duration(m_low)}-{human_duration(m_high)}",
                "why": "The 4,950 model thumbnails are what the character "
                       "builder and model mode use. This is the half that "
                       "matters and about a third of the cost.",
                "argv": ["--all", "--resume"],
            },
            {
                "id": "all",
                "label": "Everything (meshes + textures)",
                "count": want_m + want_t,
                "megabytes": total_mb,
                "estimate": f"{human_duration(a_low)}-{human_duration(a_high)}",
                "why": "Adds thumbnails for all 66,834 textures. Nice for "
                       "browsing the texture library; most of the time and "
                       "nearly all of the disk.",
                "argv": ["--all", "--textures", "--resume"],
            },
            {
                "id": "textures",
                "label": "Textures only (finish a meshes-only run)",
                "count": want_t,
                "megabytes": THUMB_FACTS["textures"]["megabytes"],
                "estimate": f"{human_duration(a_low - m_low)}-"
                            f"{human_duration(a_high - m_high)}",
                "why": "Use this later, after meshes, to fill in the rest.",
                "argv": ["--textures", "--resume"],
            },
        ],
        "declining": "The viewer works without any of this. Assets that have "
                     "no thumbnail show a placeholder tile; everything else "
                     "— search, the 3D viewport, the builder, staging, "
                     "installing — is unaffected.",
        "cli": "py -3 tools/thumbs.py --all --textures --resume",
    }
    state["decision"] = coroot.read_settings().get("thumbnails") or None
    state["ok"] = state["status"] in ("complete", "meshes-only")
    return state


def remember_thumbnail_choice(choice: str) -> dict:
    """Persist the answer so the first-run prompt does not nag.

    ``choice`` is one of ``meshes`` / ``all`` / ``textures`` (started a run),
    ``later`` (ask again next launch) or ``never`` (stop asking).
    """
    rec = {"choice": choice, "when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if choice == "later":
        coroot.write_settings(thumbnails=None)
        return rec
    coroot.write_settings(thumbnails=rec)
    return rec


def should_prompt(state: Optional[dict] = None) -> bool:
    """Ask about generating thumbnails?  Only when there are none *and* the
    person has not already said no."""
    st = state or thumbnail_state()
    if st["status"] != "none":
        return False
    d = st.get("decision") or {}
    return d.get("choice") != "never"


# ---------------------------------------------------------------------------
# the whole report
# ---------------------------------------------------------------------------

def _base_id_safe(root=None) -> str:
    try:
        return coroot.base_id(root)
    except Exception:                                    # pragma: no cover
        return "unkeyed"


def collect(explicit=None, *, with_thumbnails: bool = True) -> dict:
    inst = check_install(explicit)
    # Ask about the install that was actually RESOLVED, not the one that was
    # requested: a rejected override would otherwise key the whole per-base
    # half of this report to a root nothing is reading.
    resolved = Path(inst["path"]) if inst.get("found") and inst.get("path")         else None
    rep: dict = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo": str(REPO),
        "platform": f"{platform.system()} {platform.release()}",
        "install": inst,
        "python": check_python(),
        "packages": check_packages(),
        "derived": check_derived(resolved),
        "provenance": check_provenance(explicit),
        "localServer": check_local_server(),
        # Which index namespace this report is about. Two clients' reports are
        # otherwise indistinguishable, which is what made the missing index
        # invisible.
        "baseId": _base_id_safe(resolved),
    }
    if with_thumbnails:
        try:
            rep["thumbnails"] = thumbnail_state()
        except Exception as e:                       # pragma: no cover
            rep["thumbnails"] = {"status": "unknown", "ok": False,
                                 "error": f"{type(e).__name__}: {e}"}

    problems = []
    for t in rep["install"].get("rejectedOverrides", []):
        problems.append({
            "severity": "warning",
            "what": f"The install root configured via {t['source']} "
                    f"({t['path']}) was rejected: {t['verdict']}."
                    + (" Auto-detection was used instead."
                       if rep["install"]["ok"] else ""),
            "fix": "Correct it, or clear it with "
                   "`py -3 core/coroot.py --forget`."})
    if not rep["install"]["ok"]:
        problems.append({"severity": "error",
                         "what": "The game install was not found (or is "
                                 "incomplete).",
                         "fix": rep["install"].get("fix", "")})
    if not rep["python"]["ok"]:
        problems.append({"severity": "error",
                         "what": f"Python {rep['python']['value']} is older "
                                 f"than {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.",
                         "fix": rep["python"]["fix"]})
    for p in rep["packages"]:
        if not p["present"]:
            problems.append({
                "severity": "error" if p["required"] else "warning",
                "what": f"{p['name']} is not installed -- {p['why']}",
                "fix": p["fix"]})
    der = rep["derived"]
    if not der["ok"]:
        problems.append({
            "severity": "warning",
            "what": f"{len(der['missing'])} generated artefact(s) have not "
                    "been built yet, so the catalogue can only see loose "
                    "files — the .wdf archives store a hash of each "
                    "filename, not the name, and the recovery has not run.",
            "fix": der["fix"]})
    # Called out separately from the count above, because this one has a
    # symptom the user will otherwise blame on the viewer: without it the
    # mesh<->texture relation is rebuilt in-process on every open of this
    # client, and that is 32-56 s during which the lists cannot collapse a
    # mesh and its skins into one row. MEASURED on 7878 (37,856 meshes);
    # 6090, which has the file, loads it in 1.6 s.
    if "out/meshtex/coverage.json" in (der.get("missing") or []):
        problems.append({
            "severity": "warning",
            "what": "This client has no mesh<->texture index "
                    f"(out/indexes/{rep.get('baseId', '?')}/meshtex/"
                    "coverage.json). Every time it is opened the viewer "
                    "rebuilds that relation in memory -- tens of seconds "
                    "during which asset lists show one row per FILE instead "
                    "of one per asset.",
            "fix": "Build it once from the Health & thumbnails panel, or on "
                   "the command line: "
                   + next((a["command"] for a in der["artefacts"]
                           if a["path"] == "out/meshtex/coverage.json"),
                          "py -3 tools/meshtex.py --coverage")})

    prov = rep.get("provenance") or {}
    if prov.get("foreign"):
        problems.append({
            "severity": "error",
            "what": f"{len(prov['foreign'])} derived artefact(s) were built "
                    "from a different install than the one configured, and "
                    "say so. Reading them serves one client's facts as "
                    "another's.",
            "fix": "Rebuild them against this install, or point the tools "
                   "back at the install they came from."})
    elif prov.get("unclassified"):
        problems.append({
            "severity": "info",
            "what": f"{len(prov['unclassified'])} derived artefact(s) live in "
                    "a namespace that cannot record which install they "
                    "describe, so nothing can check them.",
            "fix": prov.get("fix", "")})

    ls = rep.get("localServer") or {}
    if not ls.get("ready"):
        missing = [p["name"] for p in ls.get("parts", []) if not p["present"]]
        problems.append({
            "severity": "info",
            "what": "The optional local test server is not set up ("
                    + ", ".join(missing) + " missing). Nothing needs it: "
                    "client/ tests and `py -3 -m client selftest` run without "
                    "it. It gives client/ a real server to exchange packets "
                    "with.",
            "fix": "py -3 tools/setup_coemu.py --check   "
                   "(see docs/server_setup.md)"})

    th = rep.get("thumbnails") or {}
    if th.get("status") == "none":
        problems.append({
            "severity": "info",
            "what": "No thumbnails have been generated. Grids show "
                    "placeholders until they are.",
            "fix": th.get("plan", {}).get("cli", "py -3 tools/thumbs.py --all")})
    elif th.get("status") == "meshes-only":
        problems.append({
            "severity": "info",
            "what": "Mesh thumbnails are present; texture thumbnails are not.",
            "fix": "py -3 tools/thumbs.py --textures --resume"})

    rep["problems"] = problems
    rep["ok"] = not any(p["severity"] == "error" for p in problems)
    return rep


def write_report(rep: dict, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, indent=1), "utf-8")
    return path


# ---------------------------------------------------------------------------
# console rendering
# ---------------------------------------------------------------------------

_MARK = {True: "  ok  ", False: " FAIL ", None: " ---- "}


def render_text(rep: dict) -> str:
    L: list[str] = []
    add = L.append
    add("")
    add("  Conquer Online RE toolkit -- health check")
    add("  " + "-" * 54)
    add(f"  repo      : {rep['repo']}")
    add(f"  platform  : {rep['platform']}")
    add("")

    i = rep["install"]
    add(f"{_MARK[bool(i['ok'])]}game install")
    if i["found"]:
        add(f"         {i['path']}")
        add(f"         found via {i['source']} -- {i['detail']}")
        for part in i.get("files", {}).get("parts", []):
            state = ("ok" if part["exists"] and part["readable"]
                     else "MISSING" if not part["exists"] else "UNREADABLE")
            size = (f"  {part['bytes'] / 1e6:,.0f} MB"
                    if part.get("bytes") else "")
            add(f"           {state:<10} {part['name']}{size}")
    else:
        add(f"         searched {i['searchedCount']} location(s), found nothing")
        for t in i["searched"][:8]:
            add(f"           {t['path']}  --  {t['verdict']}")

    p = rep["python"]
    add(f"{_MARK[p['ok']]}Python {p['value']}   (need {p['requirement']})")
    add(f"         {p['detail']}")

    for pkg in rep["packages"]:
        tag = "required" if pkg["required"] else "optional"
        mark = _MARK[bool(pkg["present"])] if pkg["required"] else (
            _MARK[True] if pkg["present"] else " warn ")
        ver = f" {pkg['version']}" if pkg["present"] else " not installed"
        add(f"{mark}{pkg['name']}{ver}   ({tag})")

    der = rep.get("derived") or {}
    add(f"{_MARK[bool(der.get('ok'))] if der.get('ok') else ' warn '}"
        f"derived data in out/  "
        f"({sum(1 for a in der.get('artefacts', []) if a['exists'])}"
        f"/{len(der.get('artefacts', []))} built)")
    for a in der.get("artefacts", []):
        state = ("inherited" if a.get("inherited")
                 else "ok" if a["exists"] else "MISSING")
        add(f"           {state:<10} {a['path']}"
            + (f"  {a['bytes'] / 1e6:,.1f} MB" if a["exists"]
               else f"   <- {a['command']}   (~{a['cost']})"))

    prov = rep.get("provenance") or {}
    if prov.get("available"):
        mark = " warn " if prov.get("foreign") else _MARK[True]
        add(f"{mark}provenance   ({prov.get('base_id', '?')})")
        add(f"           {prov.get('scanned', 0)} artefact(s) scanned; "
            f"{len(prov.get('foreign', []))} foreign, "
            f"{len(prov.get('unclassified', []))} in an unkeyed namespace, "
            f"{len(prov.get('unstamped', []))} unstamped")
        orph = prov.get("orphaned") or []
        if orph:
            mb = sum(o["bytes"] for o in orph) / 1e6
            add(f"           {len(orph)} orphaned index namespace(s), "
                f"{mb:,.0f} MB -- no declared install claims them")
            for o in orph[:6]:
                add(f"             {o['name']:<28} {o['bytes']/1e6:8.1f} MB")
            try:
                plan = provenance.migration_plan(REPO)
            except Exception:                            # pragma: no cover
                plan = []
            for e in plan:
                if e["action"] == "rename":
                    add(f"             MIGRATE  {e['from']}")
                    add(f"                  ->  {e['to']}   ({e['why']})")
                elif e["action"] == "ambiguous":
                    add(f"             DECIDE   {len(e['from'])} orphans could be "
                        f"{e['to']}: {', '.join(e['from'])}")
                else:
                    add(f"             STALE    {e['from']} ({e['why']})")
            if plan:
                add("           A rename keeps the index -- it describes the same "
                    "install under a new name, and rebuilding may not be "
                    "possible (the entity crawl needs a server dump).")
            else:
                add("           Safe to delete once you have confirmed the "
                    "install they came from is gone or re-keyed; they rebuild.")
        for rel in prov.get("foreign", [])[:10]:
            add(f"           FOREIGN    {rel}")

    ls = rep.get("localServer") or {}
    if ls:
        mark = _MARK[True] if ls.get("ready") else " info "
        add(f"{mark}local test server (optional): "
            + ("ready" if ls.get("ready") else "not set up"))
        for part in ls.get("parts", []):
            add(f"           {'ok' if part['present'] else '--':<10} "
                f"{part['name']}: {part['detail']}")
        if not ls.get("ready"):
            add("         Optional. client/ and its 39 tests run without it; "
                "see docs/server_setup.md.")

    th = rep.get("thumbnails") or {}
    if th:
        status = th.get("status", "unknown")
        mark = _MARK[True] if th.get("ok") else " info "
        add(f"{mark}thumbnails: {status}")
        add(f"         {th.get('dir')}")
        if status != "none":
            add(f"         {th.get('meshes', 0):,} mesh + "
                f"{th.get('textures', 0):,} texture, "
                f"{th.get('bytes', 0) / 1e6:,.0f} MB of PNG "
                f"(~{sum(f['megabytes'] for f in THUMB_FACTS.values())} MB "
                f"on disk with the manifests), "
                f"generated {th.get('generated')}")
        plan = th.get("plan") or {}
        if status == "none" and plan:
            add(f"         nothing generated yet. Estimates for this machine "
                f"({plan.get('cpus')} cores, {plan.get('jobs')} workers):")
            for opt in plan.get("options", [])[:2]:
                add(f"           {opt['label']:<34} "
                    f"{opt['estimate']:>12}   {opt['megabytes']:>4} MB")
            add(f"         reference: {plan.get('reference')}")
            add("         Generation is opt-in and resumable; the viewer "
                "works without it.")

    add("")
    if rep["problems"]:
        add("  What to do:")
        for prob in rep["problems"]:
            add(f"    [{prob['severity']}] {prob['what']}")
            for line in str(prob["fix"]).splitlines():
                add(f"        {line}")
    add("")
    add("  RESULT: " + ("PASS -- everything required is present"
                        if rep["ok"] else
                        "FAIL -- see above"))
    add("")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Check that this checkout can run.")
    coroot.add_root_argument(ap)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--no-write", action="store_true",
                    help="do not write out/health.json")
    ap.add_argument("--bootstrap", action="store_true",
                    help="build the missing generated data in out/ "
                         "(one-off, about 6-10 minutes on a fresh clone)")
    ap.add_argument("--bootstrap-all", action="store_true",
                    help="rebuild all of it, even what already exists")
    ap.add_argument("--provenance", action="store_true",
                    help="only audit which derived artefacts can say what "
                         "install they were built from, and exit")
    a = ap.parse_args(argv)

    if a.provenance:
        return provenance.main(["--root", str(a.root)] if a.root else [])

    if a.bootstrap or a.bootstrap_all:
        rc = bootstrap(only_missing=not a.bootstrap_all)
        if rc:
            return rc
        print()

    rep = collect(a.root)
    if not a.no_write:
        try:
            rep["writtenTo"] = str(write_report(rep))
        except OSError as e:
            rep["writtenTo"] = f"(could not write: {e})"
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        print(render_text(rep))
        if rep.get("writtenTo"):
            print(f"  full report: {rep['writtenTo']}\n")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
