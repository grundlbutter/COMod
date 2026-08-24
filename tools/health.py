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
    they depend on each other, and relays each one's own progress as it runs
    (`_run_relaying`) -- it used to discard it, and a builder that takes
    minutes in silence is indistinguishable from one that has hung;
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

#: **THE REFERENCE RUN'S CORPUS -- a divisor now, not a denominator.**
#: `--size 256 --ss 2`, on the install `coroot` knows as **Classic Conquer
#: 2.0**.  Named, not pathed, because `tests/test_sanitization.py` is right
#: that a literal install path does not belong in committed source.
#:
#: IDENTIFIED BY ARITHMETIC, 2026-08-23, because nothing recorded it:
#: 19,284 `.dds` in the recovered archive name table + 47,550 of that
#: install's 47,973 loose `.dds` = **66,834**; 4,531 archive `.c3` + 433 loose
#: = 4,964 meshes, of which meshtex resolves 4,950 to a texture.  No other
#: declared install fits either number.
#:
#: It used to be read as the corpus of WHATEVER CLIENT WAS SELECTED -- both
#: the denominator for "complete" and the work-list size in the estimate --
#: and that is what `thumbnail_corpus` replaced.  The spread it was papering
#: over is not a rounding error: Clients/5017's own work list is 2,562 meshes
#: + 21,586 textures and Clients/6609's is 11,068 + 92,256, so one table
#: over-quoted the first client by 3x and under-quoted the second by 2x.
#:
#: What survives is its use HERE: these counts are the divisor that turns the
#: reference run's wall clock into a per-item rate, which is the only part of
#: the estimate that a corpus census cannot supply.
#:
#: The `megabytes` are gone.  They were size ON DISK (allocated clusters),
#: while every other megabyte in this file and on the page is bytes of PNG,
#: and the mismatch was 35% on textures -- 486 stated against 359.6 measured
#: in that very run's own manifest.  Disk is now computed from
#: `thumbs.MESH_BYTES_PER_PIXEL` and friends, which agree with that manifest
#: to 0.5%.
THUMB_FACTS = {
    "meshes": {"count": 4950},
    "textures": {"count": 66834},
}
#: The install `THUMB_FACTS` was measured on, named so a report can say it.
#: A NAME, not a path -- see the comment above, and `coroot`'s declared-kinds
#: map for where the path lives.
THUMB_FACTS_BASIS = "Classic Conquer 2.0"
#: Wall-clock x worker-count from the reference run: 82 s for meshes and 144 s
#: for textures at 20 workers.  Divided by the reference corpus for a
#: per-item rate, then multiplied by THIS client's corpus and divided by the
#: worker count.  Scaling is not perfectly linear -- disk and the serial
#: manifest write do not parallelise -- so the estimate is reported as a range
#: with the low end at the linear figure.
MESH_CORE_SECONDS = 82 * 20
TEXTURE_CORE_SECONDS = 144 * 20
#: Core-seconds per thumbnail: 0.331 for a mesh, 0.043 for a texture.  The
#: rate is what transfers between installs; the totals above are not.
MESH_CORE_SECONDS_EACH = MESH_CORE_SECONDS / THUMB_FACTS["meshes"]["count"]
TEXTURE_CORE_SECONDS_EACH = (TEXTURE_CORE_SECONDS
                             / THUMB_FACTS["textures"]["count"])
#: The reference run itself, quoted so nobody has to trust the extrapolation
#: -- and it says WHICH INSTALL, because the corpus is the other half of the
#: number and a run time without one is not reproducible.
REFERENCE_RUN = ("226 s total (82 s meshes + 144 s textures) with 20 worker "
                 "processes on a 24-core desktop, over Classic Conquer 2.0's "
                 "4,950 meshes + 66,834 textures; ~14 s for a warm re-run. "
                 "The counts above are this client's own, so what is being "
                 "extrapolated is the per-item rate (0.33 core-seconds a "
                 "mesh, 0.043 a texture) and not the size of the job")


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
#: Artefacts that only matter when some other tool ships, as
#: ``rel -> (tool that must be present, what the artefact is actually for)``.
#: An artefact nothing in this tree can use is not "missing" and prompting for
#: a copy sends the owner to find a file nobody here would open.
#:
#: The reason string is checked, not guessed. The first version of this map
#: said `coplay.py` READS the skin manifest; it does not. `skinbuild.py`
#: writes `manifest.json` and reads it back for its own diffing, and nothing
#: else opens it -- `client/skins.py` names the path in a help string,
#: `pageshot.js` in a comment, and `coplay.py` serves the skin's PNGs rather
#: than its manifest. What is true is that the skin TREE exists for the game
#: view, and the game view is not shipped here.
#:
#: Absent from this map means "relevant wherever it is" -- only name an
#: artefact here when a specific tool's absence makes it pointless.
NOT_APPLICABLE_WITHOUT = {
    "out/skins/classic/manifest.json": (
        "tools/coplay.py",
        "the classic UI skin is drawn by the game view; this file is "
        "skinbuild's own record of that build"),
}


# ---------------------------------------------------------------------------
# what wdf_recover will cost HERE
# ---------------------------------------------------------------------------
#
# THE DEFECT THIS SECTION CLOSES.  `DERIVED` said `tools/wdf_recover.py` costs
# "5-9 min".  It took **2,164 s -- 36 minutes -- on Clients/5517**, and the
# Settings page put a button next to the 9.  Replacing 9 with 36 would have
# been a differently-unqualified number, so this measures instead.
#
# WHAT WAS ACTUALLY MEASURED, 2026-08-23, on the box that shipped the
# complaint.  Eleven entries under ConquerAssets/Clients, of which **eight**
# are clients, **seven** have `.wdf` archives at all, and **six** share the
# byte-identical baseline pair (c3.wdf 359,069,116 B + data.wdf 392,245,257 B),
# so across those six the ARCHIVE side of the job is a constant and only the
# install varies.  See `WDF_RECOVER_MEASUREMENTS`.
#
# THE PREDICTOR THE OWNER ASKED FOR IS THE WRONG ONE, and it is worth saying
# why rather than quietly substituting another.  The ask was "derive a rate
# from the directory count".  `wdf_recover`'s own progress line encourages it:
# it prints `enumerate dir 800/9045` on 5517.  **But 5517 has 1,593
# directories on disk, not 9,045.**  The 9,045 is the size of the *candidate
# namespace* -- every directory prefix appearing in any wordlist -- and that
# is mostly not this install's:
#
#     install   dirs on disk   namespace dirs   candidates hashed   timed
#     5017             365           8,801        231,747,642    2,167 s
#     5065             480           8,704        231,872,326        --
#     5165             878           8,812        234,830,546        --
#     5517           1,593           9,045        238,028,164    1,675 s
#     6090           2,803           9,274        247,315,820        --
#     6609           5,457           9,940        255,394,871        --
#     CCO 2.0        1,004           8,861        233,052,334    2,286 s
#
# **15x of install buys 1.13x of directories and 1.10x of work.**  Clients/
# 5017 is a quarter the size of Clients/5517 and took LONGER than it.  The
# reason is `discover_tpi_roots`: it pulls every DECLARED DatPkg client's
# plaintext index into the wordlist by default, one is declared here
# (Clients/7878, 146,196 names), and that single wordlist is ~8,400 of the
# ~8,800-9,900 namespace directories on every one of them.
#
# Turn it off and the install matters again, because then the wordlist IS the
# install -- MEASURED with `--no-tpi`, which the tool documents as
# reproducing its older behaviour:
#
#     5017     960 ns dirs    17,227,911 candidates      302 s
#     5517   2,087            35,448,132                 571 s
#     6609   5,710           139,024,562               2,040 s
#
# **That is where "5-9 min" came from.**  5.0 min and 9.5 min on the two
# smaller installs, and `wdf_recover.discover_tpi_roots` says so in its own
# docstring: *"`--tpi` has existed since this tool was written and the
# baseline run never used it, because it defaulted to empty."*  The estimate
# was not measured on a smaller install.  It was measured on an earlier tool,
# and then the tool changed under it.
#
# THE RATE, SINCE IT WAS ASKED FOR, AND WHY IT IS NOT USED.  Least squares
# over the seven installs that have the baseline archives, candidate count
# against each cheap predictor:
#
#     candidates ~ dirs on disk    230,056,313 + 4,916 x dirs     R2 0.962
#     candidates ~ file count      230,687,312 +   212 x files    R2 0.768
#     candidates ~ bytes           225,509,938 + 5.75M x GB       R2 0.574
#
# So directory count IS the best of the three -- and **96.3% of the fitted
# value is the intercept**, the part that has nothing to do with the install.
# The rate is real and it is 4,916 candidates, about 0.03 s, per directory.
#
# Against the thing actually being predicted it is worse than useless.  Over
# the three full timed runs in the DatPkg regime, seconds against directories
# on disk:
#
#     seconds = 2,430 - 0.392 x dirs      R2 0.553,  n = 3
#
# **The slope has the wrong sign.**  Not because more directories are faster,
# but because the install moves the answer by 1.10x while re-running the same
# install on the same box moves it by 1.29x.  A rate fitted through that is
# fitting noise, and with n = 3 it will fit it confidently.
#
# What this module estimates instead is the job's actual
# work -- the candidate count -- which it can compute in a couple of seconds
# because `wdf_recover` builds its directory list from `loose | tpi | ids`
# and NOT from the expensive string scrape.  Everything except the scrape and
# the hashing itself is cheap enough to just do.
#
#: THE POPULATION, measured 2026-08-23 on the box the complaint came from.
#: One row per install.  `dirs`/`files`/`bytes` are the install on disk;
#: `ns_dirs` and `candidates` are what `wdf_recover` actually walks and hashes
#: with the box's declared installs (one DatPkg client, Clients/7878);
#: `notpi_*` is the same install with `--no-tpi`, i.e. the tool's older
#: behaviour.  `seconds` is a full timed run, or None where the install was
#: not timed and is interpolated.
#:
#: Read the two candidate columns against the two directory columns.  That is
#: the whole finding: **4.4x of install buys 1.03x of work.**
WDF_RECOVER_MEASUREMENTS: dict = {
    # install:            dirs  files    GB scrapeGB  ns_dirs  candidates    s
    "Clients/5017":  dict(dirs=365,   files=5432,   gb=1.25, scrape_gb=0.48,
                          ns_dirs=8801, candidates=231_747_642, seconds=2167,
                          notpi_dirs=960, notpi_candidates=17_227_911,
                          notpi_seconds=302),
    "Clients/5065":  dict(dirs=480,   files=7812,   gb=1.31, scrape_gb=0.54,
                          ns_dirs=8704, candidates=231_872_326, seconds=None),
    "Clients/5165":  dict(dirs=878,   files=12848,  gb=1.46, scrape_gb=0.68,
                          ns_dirs=8812, candidates=234_830_546, seconds=None),
    "Clients/5517":  dict(dirs=1593,  files=26841,  gb=1.89, scrape_gb=1.10,
                          ns_dirs=9045, candidates=238_028_164, seconds=1675,
                          notpi_dirs=2087, notpi_candidates=35_448_132,
                          notpi_seconds=571),
    "Clients/6090":  dict(dirs=2803,  files=54217,  gb=2.62, scrape_gb=1.80,
                          ns_dirs=9274, candidates=247_315_820, seconds=None),
    "Clients/6609":  dict(dirs=5457,  files=110526, gb=4.40, scrape_gb=3.51,
                          ns_dirs=9940, candidates=255_394_871, seconds=None,
                          notpi_dirs=5710, notpi_candidates=139_024_562,
                          notpi_seconds=2040),
    "Classic Conquer 2.0":
                     dict(dirs=1004,  files=53795,  gb=3.36, scrape_gb=1.74,
                          ns_dirs=8861, candidates=233_052_334, seconds=2286),
    # Present under Clients/ and NOT costable, which is itself a finding: the
    # brief said "ten clients" and ten directories is not ten clients.
    "Clients/7878":  dict(dirs=12462, files=191162, gb=15.34, scrape_gb=12.65,
                          why="DatPkg -- no .wdf at all; wdf_recover would "
                              "open nothing. It IS the DatPkg wordlist every "
                              "other estimate here is inflated by."),
    "Clients/Zephyr": dict(dirs=734,  files=18038,  gb=4.41, scrape_gb=0.71,
                           why="five garments*.wdf and no c3/data.wdf. "
                               "wdf_recover.harvest_ids documents those "
                               "archives as a MEASURED dead end -- 14,051 "
                               "entries, 0.0% named against every wordlist."),
    "Clients/CCO":   dict(dirs=0, files=2, gb=0.0, scrape_gb=0.0,
                          why="an empty gitignored placeholder, .gitkeep "
                              "only. The CCO client is the Classic Conquer 2.0 "
                              "install above."),
    "Clients/Installers":
                     dict(dirs=5, files=22, gb=13.11, scrape_gb=0.0,
                          why="installer archives, not an install."),
}

#: Fitted 2026-08-23 against six full timed runs, and the fit is only as good
#: as its worst term -- so here is which term that is.
#:
#: **The candidate count is essentially exact.**  `wdf_recover_workload`
#: against the tool's own `candidates_tested`, on three installs including one
#: with a different archive pair: -0.08%, +0.15%, +0.31%.
#:
#: **The RATE is not.**  Achieved prefixed-hash rate across the six runs:
#:
#:     13:04  5017 --no-tpi      70.1k/s
#:     13:09  5517 --no-tpi      78.9k/s
#:     13:18  6609 --no-tpi      87.2k/s
#:     14:08  5017             110.7k/s
#:     14:45  Classic Conquer  151.1k/s
#:     15:23  5517             149.9k/s
#:
#: 2.15x, monotone in wall-clock time, on one box against workloads whose
#: shape barely differs -- 5017 and Classic Conquer have the same candidates
#: per prefixed call within 0.2% and are 1.36x apart in rate.  That is
#: **concurrent load on the machine**, and it is why `_hash_rates` is
#: benchmarked at estimate time instead of baked in: the benchmark moves with
#: the load, so the RATIO below is what stays put.
#:
#: How much that matters, MEASURED.  Pooling the six achieved rates and
#: predicting the held-out 5517 gives 2,286 s against an actual of 1,675 s,
#: **+36%** -- the pooled rate averages over load states that no longer
#: apply.  Fitting the ratio against Classic Conquer 2.0 alone (idle box,
#: different install, different archives) and predicting 5517 gives
#: 1,665-2,056 s against 1,675 s, **-0.6% at the warm end**.
#:
#: AND THE FLOOR UNDER ALL OF IT: 5517 was measured at **2,164 s** by another
#: agent earlier the same day and at **1,675 s** here -- same install, same
#: configuration, **29% apart**.  Nothing built on this box can resolve better
#: than that, so the estimate is reported as a range and the range is not
#: cosmetic.  It is still worth having: the defect it replaces was 4x.
WDF_RECOVER_MODEL: dict = {
    # achieved / benchmarked, from the two runs on an idle box.
    "dict_efficiency": 0.505,
    "prefixed_efficiency": 0.826,
    # The scrape, seconds per GB read.  Both ends MEASURED: 40 s/GB warm
    # (Clients/5017, re-read minutes after the previous run) and 394 s/GB
    # cold (Classic Conquer 2.0, untouched that day and outside the tree).  The
    # 5.5x cold/warm on Clients/5517 that `_run_relaying` documents sits
    # inside this 10x, because that "cold" had been read the same session.
    "harvest_fixed_s": 3.0,
    "harvest_warm_s_per_gb": 40.0,
    "harvest_cold_s_per_gb": 394.0,
    # Verify + emit. 0-5 s on all six runs; it reads 32 bytes per entry.
    "verify_s": 3.0,
    # THE SEARCH BAND, and it is the widest term in the answer.
    #
    # `_hash_rates` measures the box as it is WHEN ASKED. The run happens
    # afterwards, and on this box the achievable rate moved 2.15x over three
    # hours as other work came and went. Two measured bounds on how wrong
    # that can go:
    #
    #   * the floor -- Clients/5517 measured 2,164 s and 1,675 s on the same
    #     day under the same configuration, 29% apart, with nothing about the
    #     install or the tool different between them;
    #   * the ceiling -- 70.1k/s to 151.1k/s achieved across the six runs.
    #
    # The band below is the 29% floor rounded outward, which covers a box
    # whose load is roughly what it was at estimate time. A box that gets
    # BUSY after the estimate can still overrun it, by up to the 2.15x, and
    # `wdf_recover_estimate` says so in `caveat` rather than pretending.
    "search_band_low": 0.78,
    "search_band_high": 1.32,
    # THE CANDIDATE COUNT IS EXACT IN ONE REGIME AND NOT IN THE OTHER, and
    # the two must not be reported the same way. MEASURED 2026-08-23,
    # `wdf_recover_workload` against the tool's own `candidates_tested`:
    #
    #     install   WITH tpi          --no-tpi
    #     5017      0.999  (231.6M)   0.558  (  9.6M vs   17.2M)
    #     5517      1.001  (238.4M)   1.135  ( 40.2M vs   35.4M)
    #     6609      1.000  (255.4M)   0.996  (138.5M vs  139.0M)
    #
    # WHY, and it is the model's one documented inexactness biting where it
    # was measured not to: `wdf_recover_workload` approximates the tool's
    # `found` directory set by `known = loose | tpi | ids`, leaving out the
    # expensive string scrape's contribution. With a DatPkg index in the
    # wordlist that residual is under a percent because the index IS the
    # namespace. Without one the scrape is most of it -- Clients/5017 has 316
    # directories the model can see against 960 the tool walks -- and the
    # count comes out 44% low. Clients/6609 is 5,161 against 5,710 and lands
    # within 0.4%, because there the loose tree dominates instead.
    #
    # So the `--no-tpi` estimate carries the measured residual as a WIDER
    # BAND rather than a footnote: actual/model ran 0.88x-1.80x over the
    # three. A number that is exact in one mode and 2x out in the other,
    # printed identically, is the defect this whole module exists to close.
    "notpi_residual_low": 0.88,
    "notpi_residual_high": 1.80,
    "notpi_residual_basis":
        "three full --no-tpi runs, 2026-08-23: model/actual candidates 0.558 "
        "(Clients/5017), 1.135 (Clients/5517), 0.996 (Clients/6609). The "
        "median is 1.00 and the spread is 2.0x.",
    "basis": "six full timed runs 2026-08-23 on Clients/5017, Clients/5517, "
             "Clients/6609 and Classic Conquer 2.0; held out Clients/5517 "
             "and predicted it to -0.6%",
}

#: The row `DERIVED` shows when nobody has named an install to estimate for.
#: A range, not a point, and it says what moves it -- because the thing it is
#: standing in for is `wdf_recover_estimate`, which answers for a *named*
#: install and is what the Settings page should be calling.
WDF_RECOVER_FALLBACK_COST = (
    "28-38 min on the four installs timed with a DatPkg client declared, "
    "5-34 min on the three timed without one -- MEASURED, see "
    "WDF_RECOVER_MEASUREMENTS. Ask wdf_recover_estimate for a named install "
    "rather than reading this row; it answers in about 3 s")

#: **Every estimate below names the install it was measured on.**  An
#: unqualified number reads as universal, and that is the defect this comment
#: and the `measured on ...` clause in every `cost` string exist to close: the
#: table used to say `wdf_recover` costs "5-9 min" and it took **2,164 s -- 36
#: minutes -- on Clients/5517**, four times the top of the range, with a
#: button next to it.
#:
#: Two different things scale, and they are not the same thing:
#:
#:   * the **harvest** -- walking and string-scraping the install -- scales
#:     with the install, and is the smaller half.  MEASURED across seven
#:     installs, `WDF_RECOVER_MEASUREMENTS`.
#:   * the **search** -- hashing candidate paths -- scales with the *wordlist
#:     namespace*, which is mostly NOT this install's.  `wdf_recover` pulls in
#:     every declared DatPkg client's plaintext index by default, so a client
#:     one tenth the size costs the same.  See `WDF_RECOVER_MODEL`.
#:
#: `wdf_recover_estimate` computes the number for a *named* install from a
#: one-second census, so the page can say what THIS client costs instead of
#: quoting the row below.  The row is the fallback for "no install named".
DERIVED = [
    ("out/wdf/c3_names.json",
     ["tools/wdf_recover.py"], WDF_RECOVER_FALLBACK_COST,
     "recovers 24,426 of the 24,757 filenames hashed in the two .wdf "
     "archives. Without it nothing inside the archives can be named, "
     "browsed or exported."),
    # CORRECTED 2026-08-23. This row used to read "reads the shipped DLL
    # table, not the install" -- and `tools/wdf_names.py` does no such thing.
    # It walks the WHOLE install tree (`harvest`: `root.rglob("*")` plus every
    # ini/*.json and map/*.DMap) to build its wordlist, and opens that
    # install's c3.wdf/data.wdf for the hashes. The name `out/dll/` is
    # historical -- the FIRST wordlist came from DLL strings -- and it was
    # read as a claim about the inputs. The artefact stays shared
    # (`coroot.GLOBAL`, a hash->name table is true anywhere); its CONTENT
    # depends entirely on which client built it, which is why it now takes
    # --root.
    ("out/dll/wdf_name_recovery.json",
     ["tools/wdf_names.py"], "21-25 s on Clients/5017, 5517, 6609 and Classic "
     "Conquer 2.0 -- it walks the whole install tree for its wordlist, so it "
     "scales with the install and NOT with a fixed table, whatever out/dll/ "
     "suggests",
     "the smaller name table coassets.py loads by default. Built FROM one "
     "install and shared by all of them: pointing it at a second client tops "
     "the same table up."),
    ("out/meshtex/coverage.json",
     ["tools/meshtex.py", "--coverage"], "32 s on Clients/5017, 41 s on "
     "Clients/5517, 84 s on Clients/6609, 80 s on Classic Conquer 2.0 -- "
     "scales with the install's loose mesh and texture count",
     "which texture belongs to which mesh. Drives the merged catalogue rows "
     "and the thumbnail work list. MUST be built after wdf_recover."),
    ("out/effects/linkage.json",
     ["tools/effects.py", "--linkage"], "26 s on Classic Conquer 2.0, 44 s on "
     "Clients/5017, 87 s on Clients/5517, 139 s on Clients/6609 -- it tracks "
     "the effect and action tables, not the install's size",
     "weapon/action to 3D effect linkage."),
    ("out/opcodes.json",
     ["tools/build_opcodes.py"], "0.2 s on Clients/5017, 5517, 6609 and Classic "
     "Conquer 2.0; built from refs/, not from an install, so it is the "
     "same everywhere",
     "the protocol opcode table."),
    # Genuinely optional, and reported for the same reason CoEmu is: a
    # gitignored dependency is otherwise invisible. Skipping it costs the
    # pictures and nothing else -- skins resolve token by token, so an unbuilt
    # `classic` falls back to the `plain` skin's value for each missing image
    # and the in-game UI draws a flat gold frame instead of a bitmap one.
    ("out/skins/classic/manifest.json",
     ["tools/skinbuild.py"], "0.5-0.6 s on Clients/5017, 5517, 6609 and "
     "Classic Conquer 2.0; reads a fixed handful of files out of "
     "data/Interface/, so it is flat",
     "the `classic` UI skin: the 9-slice frame and window captions from the "
     "game's own data/Interface/. Optional -- docs/ui.md §3."),
]

WDF_RECOVER_REL = DERIVED[0][0]


# ---------------------------------------------------------------------------
# which builder takes an install, and where its artefact is allowed to land
# ---------------------------------------------------------------------------
#
# THE DEFECT THIS SECTION CLOSES.  `bootstrap()` took no root.  It built for the
# CONFIGURED install whatever the caller was looking at -- and `health.py`
# already PARSED `--root`, so `py -3 tools/health.py --bootstrap --root D:/other`
# accepted the flag, dropped it, and built for somebody else.  A flag that is
# accepted and ignored is worse than no flag: the user has been told the answer
# is about D:/other and the artefact on disk is about the configured install.
#
# So a root is threaded through, and **every builder gets its own check that
# the artefact landed under the root it was given**.  That check is the point.
# Threading a `--root` into an argv proves nothing -- three of the six builders
# below resolve their own output path, and a fourth (`wdf_names.py`) resolved
# its INPUT from `coroot.default_root()` at import time and had no flag at all
# until this change.  The landing check is what turns "we passed --root" into
# "the bytes are where --root said".
#
# TWO OF THE SIX ARE NOT PER-ROOT, AND DUPLICATING THEM WOULD BE A LIE.
# `out/opcodes.json` is built from `refs/` and never opens an install;
# `out/wdf/` and `out/dll/wdf_name_recovery.json` are hash->name tables, and
# the TQ hash is a pure function of the filename string, so an entry recovered
# from one install is true in every install (`coroot.GLOBAL` says so, with the
# measurement).  Their landing check therefore asserts the GLOBAL path and
# says, in the same breath, that no per-root copy exists or should.
#
# The distinction that matters and is easy to blur: **install-independent
# OUTPUT is not the same as install-independent INPUT.**  `wdf_names.py` and
# `wdf_recover.py` both write a shared table and both read the install to do
# it -- a run against a different client recovers different names into the
# same file.  `build_opcodes.py` is the only one of the six whose inputs are
# install-independent too, and it is the only one offered no `--root`.
#
#: `flag`     the argv the builder accepts to be pointed at an install, or
#:            None when it takes none and a `--root` would be meaningless.
#: `scope`    "per-base" -- `coroot.PER_BASE`, one artefact per install;
#:            "shared"   -- `coroot.GLOBAL`, one artefact for all of them.
#: `inputs`   "install" or "repo": where the BYTES come from.  A "shared"
#:            artefact with "install" inputs is the interesting case: the file
#:            is one, its contents depend on which client last built it.
#: `why`      printed by the checklist beside the artefact, so a user choosing
#:            what to build for which client is told which rows are per-client
#:            and which are not.
BUILDER_INSTALL: dict = {
    "out/wdf/c3_names.json": dict(
        flag="--root", scope="shared", inputs="install",
        why="Shared by every client -- a WDF name table is {hash: name} and "
            "the TQ hash is a pure function of the filename string, so a name "
            "recovered from one install is true in all of them "
            "(coroot.GLOBAL). But it is BUILT from an install: --root chooses "
            "whose c3.wdf/data.wdf get their hashes resolved, and running it "
            "against a second client tops the same shared table up rather "
            "than making a second one."),
    "out/dll/wdf_name_recovery.json": dict(
        flag="--root", scope="shared", inputs="install",
        why="Shared for the same reason and built the same way. NOT "
            "install-independent, whatever its neighbours in out/dll/ are: "
            "tools/wdf_names.py harvests its whole wordlist by walking the "
            "install tree, so which client it ran against decides what it "
            "resolved. It took no --root at all before this change and read "
            "coroot.default_root() at import."),
    "out/meshtex/coverage.json": dict(
        flag="--root", scope="per-base", inputs="install",
        why="Per client. Which texture belongs to which mesh is a fact about "
            "one install's art, and it lands in that install's own keyed "
            "namespace (coroot.PER_BASE)."),
    "out/effects/linkage.json": dict(
        flag="--root", scope="per-base", inputs="install",
        why="Per client. The effect and action tables are the install's."),
    "out/opcodes.json": dict(
        flag=None, scope="shared", inputs="repo",
        why="THE ONLY GENUINELY INSTALL-INDEPENDENT ROW. Built from refs/ -- "
            "two offline wiki copies and two emulator headers -- and it opens "
            "no install at all, so tools/build_opcodes.py is offered no "
            "--root and a per-client copy of it would be the same bytes under "
            "a different name. Build it once; it serves every client."),
    "out/skins/classic/manifest.json": dict(
        flag="--root", scope="per-base", inputs="install",
        why="Per client, despite its flat cost. tools/skinbuild.py copies and "
            "converts the 9-slice frame out of the install's own "
            "data/Interface/, so two clients produce different bytes -- "
            "out/skins/ is keyed (coroot.PER_BASE) for exactly that reason. "
            "Flat COST is not install-independence."),
}


def builder_install(rel: str) -> dict:
    """`BUILDER_INSTALL[rel]`, and a loud failure rather than a guess.

    Every entry of `DERIVED` must be declared here -- `test_bootstrap_root`
    asserts the two sets are equal -- because the two possible defaults fail
    in opposite directions.  Assuming a builder takes `--root` invents a flag
    it will reject; assuming it does not silently builds the configured
    install while the caller is looking at another.  Neither is safe, so it is
    written down.
    """
    try:
        return BUILDER_INSTALL[rel]
    except KeyError:
        raise KeyError(
            f"{rel!r} is in health.DERIVED but not in health.BUILDER_INSTALL. "
            f"Declare whether its builder takes an install (`flag`), where "
            f"its artefact lands (`scope`) and where its bytes come from "
            f"(`inputs`). It is not guessed at: a wrong guess either invents "
            f"a flag or silently builds the wrong client.") from None


def builder_argv(rel: str, argv: list, root=None, *, no_tpi: bool = False) -> list:
    """The argv for `rel`'s builder, pointed at `root` where that means
    anything.

    `--root` is appended only for a builder whose `flag` says it accepts one.
    Appending it everywhere would make `build_opcodes.py` die on an unknown
    option, and defaulting the other way is the defect at the top of this
    section.
    """
    out = list(argv)
    spec = builder_install(rel)
    if root is not None and spec["flag"]:
        out += [spec["flag"], str(root)]
    if no_tpi and rel == WDF_RECOVER_REL:
        out.append("--no-tpi")
    return out


def landing_path(rel: str, root=None) -> Path:
    """The one path `rel` is allowed to appear at when built for `root`.

    `coroot.derived_rel` keys a per-base artefact into that install's
    namespace and leaves a shared one alone, so this is the same answer a
    READER will get -- which is the property the check needs.  An artefact
    written somewhere the reader will not look is not built.
    """
    return REPO / coroot.derived_rel(rel, root)


def check_landing(rel: str, root=None, since: Optional[float] = None) -> dict:
    r"""Did `rel` land under the root it was given?  Per builder, every build.

    **This is the check the `--root` flag is worthless without.**  Three of
    the six builders resolve their own output path from a root they parse
    themselves; one of them (`meshtex.py`) has a comment recording the day
    `--root` was accepted and *ignored*.  A bootstrap that passes the flag and
    then reports success because *some* file appeared is confidently wrong in
    the one direction that matters -- it tells the user the artefact is about
    client A when the bytes are about client B.

    So it asserts three things, and the third is the one that bites:

      * the artefact exists at `landing_path(rel, root)` -- the keyed path a
        reader for `root` will resolve;
      * it was written at or after `since`, when a timestamp is given, so an
        artefact left over from a previous run cannot pass for one this run
        produced;
      * **no OTHER declared base's copy was freshly written instead.**  That
        is what a builder ignoring `--root` looks like from outside: the
        expected keyed path is untouched and the configured install's is new.
        Reporting it as "missing" would send the user to re-run the thing that
        just ran; naming it sends them to the builder.

    Returns `{"ok": ..., "where": ..., "landedAt": [...], "why": ...}`.
    `ok` is False on anything it cannot positively confirm.
    """
    spec = builder_install(rel)
    want = landing_path(rel, root)
    fresh = (lambda p: since is None or p.stat().st_mtime >= since - 1.0)
    out = {
        "path": rel,
        "root": str(root) if root is not None else "",
        "scope": spec["scope"],
        "inputs": spec["inputs"],
        "rootAware": bool(spec["flag"]),
        "where": str(want),
        "exists": want.is_file(),
        "landedAt": [],
        "ok": False,
        "why": "",
    }
    if want.is_file() and fresh(want):
        out["ok"] = True
        out["bytes"] = want.stat().st_size
        out["why"] = (
            f"landed at {coroot.derived_rel(rel, root)}"
            + (f" -- this install's own keyed namespace"
               if spec["scope"] == "per-base" else
               f" -- the shared path, and correctly so: this artefact is "
               f"true in any install ({spec['inputs']} inputs). See "
               f"health.BUILDER_INSTALL."))
        return out
    # It is not where it should be. Say where it IS, over every base this
    # machine has declared, rather than leaving the user to guess.
    for other in _declared_roots():
        try:
            p = REPO / coroot.derived_rel(rel, other)
        except Exception:                                # pragma: no cover
            continue
        if p == want or not p.is_file():
            continue
        if fresh(p):
            out["landedAt"].append(str(other))
    if out["landedAt"]:
        out["why"] = (
            f"{rel} was NOT written for {root} -- it was freshly written for "
            f"{', '.join(out['landedAt'])} instead. The builder took the "
            f"--root it was given and resolved its output against a "
            f"different install; that is the failure this check exists for, "
            f"and it is not fixed by re-running the bootstrap.")
    elif want.is_file():
        out["why"] = (
            f"{rel} exists at {want} but was not written by this run "
            f"(mtime is older than the build started). The builder exited 0 "
            f"and produced nothing, so what is on disk is the previous "
            f"answer, for whatever install produced it.")
    else:
        out["why"] = (
            f"{rel} is not at {want}. The builder exited 0 and the artefact "
            f"a reader for {root} would resolve is absent.")
    return out


def _declared_roots() -> list:
    """Every install this machine has been told about, plus the configured
    one.  Used only to say WHERE a misdirected artefact went."""
    roots = []
    try:
        for path in (coroot.declared_kinds() or {}):
            roots.append(Path(str(path)))
    except Exception:                                    # pragma: no cover
        pass
    try:
        cfg = coroot.read_settings().get("game_root")
        if cfg and not any(str(r) == str(cfg) for r in roots):
            roots.append(Path(str(cfg)))
    except Exception:                                    # pragma: no cover
        pass
    return roots


def install_census(root, *, paths: bool = False) -> dict:
    """dirs / files / bytes for one install, by a single `os.scandir` walk.

    Metadata only -- no file is opened.  MEASURED warm on this box: 0.03 s for
    the 5,432-file Clients/5017 up to 1.13 s for the 191,162-file
    Clients/7878, so it is cheap enough to run before answering "what will
    this cost".

    `scrape_bytes` is the subset `wdf_recover.harvest_strings` will actually
    READ -- the top-level directories in its `SCRAPE_DIRS`, plus every .exe
    and .dll anywhere -- and it is that number, not the install's total size,
    that sets the harvest half of the bill.  On Clients/7878 the two differ by
    2.5 GB.

    With ``paths=True`` it also returns `paths`, **defined to be the same set
    `wdf_recover.harvest_loose` returns** -- every file's archive-relative,
    lowercased, forward-slashed path.  Returned from this walk rather than
    taken from a second one, because `wdf_recover_estimate` needs both and a
    second `rglob` over Clients/6609 costs 11 s.  `tests/
    test_bootstrap_progress.py` asserts the two sets are equal on a real
    install, so the duplication cannot drift silently.
    """
    scrape_dirs = ("map", "ani", "ini", "c3", "graphics", "data",
                   "LauncherResources", "sound")
    root = Path(root)
    dirs = files = total = scrape_files = scrape_bytes = 0
    wdf: dict[str, int] = {}
    rels: set[str] = set()
    stack = [(str(root), "")]
    while stack:
        d, rel = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    dirs += 1
                    stack.append((e.path, f"{rel}/{e.name}" if rel else e.name))
                    continue
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            files += 1
            total += size
            if paths:
                rels.add((f"{rel}/{e.name}" if rel else e.name).lower())
            ext = os.path.splitext(e.name)[1].lower()
            if ext == ".wdf":
                wdf[e.name] = size
            # `harvest_strings` reads SCRAPE_DIRS recursively AND every .exe
            # or .dll wherever it sits -- the binaries live at the root, which
            # is outside SCRAPE_DIRS, and that blind spot is documented there.
            top = rel.split("/")[0] if rel else ""
            if top in scrape_dirs or ext in (".exe", ".dll"):
                scrape_files += 1
                scrape_bytes += size
    out = {"root": str(root), "dirs": dirs, "files": files, "bytes": total,
           "wdf": wdf, "wdf_bytes": sum(wdf.values()),
           "scrape_files": scrape_files, "scrape_bytes": scrape_bytes}
    if paths:
        out["paths"] = rels
    return out


#: One estimate per install **per wordlist mode** per process.  `check_derived`
#: is called on every poll of the Settings page's bootstrap panel, and an
#: estimate that walked the install each time would cost more than the thing it
#: is describing.
#:
#: Keyed `(root, tpi)`, not `root`.  With `--no-tpi` the same install is a
#: 7-14x different job -- Clients/5017 is 231.5M candidates against 17.2M --
#: so a cache keyed on the install alone would answer the second question with
#: the first question's number, which is the shape of wrong this whole module
#: exists to stop.
_WDF_ESTIMATE_CACHE: dict[tuple, dict] = {}


def _duration_range(low: float, high: float) -> str:
    """"30-40 min", not "30 min-40 min" -- one unit when both share it."""
    a, b = human_duration(round(low)), human_duration(round(high))
    ua, ub = a.split(" ", 1)[-1], b.split(" ", 1)[-1]
    if a == b:
        return a
    return f"{a.split(' ')[0]}-{b}" if ua == ub else f"{a}-{b}"


def _hash_rates(samples: int = 40_000) -> tuple[float, float]:
    r"""`(dictionary, prefixed)` candidates per second, measured on THIS box.

    **Two rates, not one.**  `Recovery.try_paths` hashes a whole path;
    `try_prefixed` reuses the cached mixing state for the shared directory
    prefix and pays only for the tail.  MEASURED on an idle box at 103k/s and
    183k/s, 1.8x apart, and the same ratio holds in the real runs -- 29-57k/s
    against 70-151k/s achieved across six of them.  Lumping them costs almost
    nothing in the DatPkg regime, where the enumeration is 98.6% of the
    candidates, and a fifth of the answer without it, where the dictionary
    passes are 9% of the candidates and 16% of the time.

    Benchmarked rather than hardcoded, because the number this module needs is
    a property of the machine reading it, not of the machine that wrote it.
    ~1 s.  Uses `wdf_recover`'s own hash, so a change there changes this.
    """
    import wdf_recover as W                              # noqa: PLC0415
    words = [f"c3/texture/{i:06d}.dds" for i in range(samples)]
    t0 = time.perf_counter()
    for w in words:
        W.tq_hash(w)
    dt_dict = time.perf_counter() - t0

    state, tail = W.hash_state(b"c3/texture")
    sfx = [f"/{i:06d}.dds".encode("latin-1") for i in range(samples)]
    t0 = time.perf_counter()
    for s in sfx:
        W.hash_from(state, tail + s)
    dt_pref = time.perf_counter() - t0
    return (samples / dt_dict if dt_dict > 0 else float("nan"),
            samples / dt_pref if dt_pref > 0 else float("nan"))


def wdf_recover_workload(root, max_digits: int = 4, loose=None,
                         tpi: bool = True) -> dict:
    r"""How many candidate paths `wdf_recover` will hash for `root`.

    Not a regression -- the count itself, computed the way the tool computes
    it.  That is possible because the tool's directory list comes from

        known = loose | tpi | id_words | found

    and only `found` needs the hashing.  The expensive string scrape
    (`harvest_strings`) contributes to `found` and to nothing else here, so
    everything this needs is a directory walk, four .tpi index reads and a
    handful of ini files: **MEASURED 1-14 s** against the 30-40 minutes it is
    estimating.

    Approximating `found` by `known` is the one inexactness.  It moves the
    directory list by well under a percent -- a recovered name almost always
    lands in a directory some loose file or .tpi entry already named -- and
    the residual is reported with the fit rather than hidden.
    """
    import wdf_recover as W                              # noqa: PLC0415
    from collections import Counter                      # noqa: PLC0415
    import contextlib, io                                # noqa: PLC0415,E401

    root = Path(root)
    hush = io.StringIO()
    with contextlib.redirect_stdout(hush):
        if loose is None:
            loose = W.harvest_loose(root)
        # `tpi=False` models `wdf_recover --no-tpi`, which the tool documents
        # as reproducing its pre-DatPkg behaviour. It is not a cosmetic
        # switch: it is 85-93% of the candidates, and it is the only term in
        # this count that depends on OTHER installs.
        tpi_roots = W.discover_tpi_roots() if tpi else []
        tpi_words = W.harvest_tpi(tpi_roots) if tpi_roots else set()
        ids = W.harvest_ids(root)
        id_words = W.id_paths(ids) if ids else set()

    known = loose | tpi_words | id_words
    dirs = {n[:n.rfind("/")] for n in known if "/" in n}
    pats = W.build_patterns(known)
    glob_pats: Counter = Counter()
    for ps in pats.values():
        for p in ps:
            glob_pats[p] += 1
    universal = [p for p, c in glob_pats.most_common(12)
                 if p[1] <= min(max_digits, 4) and c >= 3]

    enum = 0
    calls = 0
    for d in dirs:
        for (_lit, nd, _ext) in set(pats.get(d, ())) | set(universal):
            if nd <= max_digits:
                enum += 10 ** nd
                calls += 1
    # The dictionary passes: every word once, then every word x every
    # extension `wdf_recover.EXTS` offers.
    dict_c = len(known)
    permute_c = len(W.ext_permute(known))
    # The dir x basename cross at the end.  `bases` is taken over recovered
    # names; `known`'s basenames are the same population plus a tail.
    bases = {n[n.rfind("/") + 1:] for n in known if "/" in n}
    cross = len(dirs) * min(len(bases), 13_000)
    return {
        "loose": len(loose), "tpi": len(tpi_words), "id_words": len(id_words),
        "tpi_roots": [str(p) for p in tpi_roots],
        "tpi_used": bool(tpi),
        "namespace_dirs": len(dirs), "basenames": len(bases),
        "dict_candidates": dict_c, "permute_candidates": permute_c,
        "enum_candidates": enum, "cross_candidates": cross,
        "candidates": dict_c + permute_c + enum + cross,
        # `try_prefixed` rebuilds its candidate list once per call, so the
        # achieved rate depends on how many candidates each call amortises
        # that over. A directory whose widest pattern is 4 digits builds a
        # 10,000-element list; one whose widest is 1 digit builds ten. This
        # is recorded because it is MEASURED to matter -- see
        # WDF_RECOVER_MODEL's note on the 70k-151k/s spread -- and a later
        # refinement of the rate model needs it.
        "prefixed_calls": calls + len(dirs),
        "candidates_per_prefixed_call":
            round((enum + cross) / max(1, calls + len(dirs))),
    }


def wdf_recover_estimate(root=None, *, use_cache: bool = True,
                         tpi: bool = True) -> dict:
    r"""What `tools/wdf_recover.py` will cost on **this** install, on **this**
    box -- rather than what it cost on somebody else's.

    Three measurements, none of them a table lookup:

      * `install_census` -- how much there is to walk and scrape.  <=1.2 s.
      * `wdf_recover_workload` -- how many candidates will be hashed,
        counted the way the tool counts them.  1-14 s.
      * `_hash_rates` -- how fast this CPU hashes one, by both of the two
        paths the tool uses.  ~1 s.

    The range is not decoration, and it has two sources rather than one:

      * the **file cache**, MEASURED at 40 s/GB warm and 394 s/GB cold on the
        same scrape code -- and the low end assumes cache, the high end disk;
      * **what else the machine is doing**, which is the wider of the two and
        the one people do not expect.  The achievable hash rate on this box
        moved 2.15x over three hours as other work came and went.

    `seconds_typical` is the number to say out loud; the range is what to say
    next.  Held out Clients/5517, fitted on Classic Conquer 2.0 alone, and
    predicted it to -0.6% -- see `WDF_RECOVER_MODEL` for that and for the 29%
    same-install floor underneath it.

    Returns `{"ok": False, "why": ...}` rather than a number when it cannot
    tell -- no `.wdf` under `root` (Clients/7878 is DatPkg and has none;
    Zephyr has only `garments*.wdf`, which this tool is MEASURED not to
    recover), or `wdf_recover.py` not shipped in this tree.
    """
    key = (str(root or ""), bool(tpi))
    if use_cache and key in _WDF_ESTIMATE_CACHE:
        return _WDF_ESTIMATE_CACHE[key]
    out = _wdf_recover_estimate_uncached(root, tpi)
    if use_cache:
        _WDF_ESTIMATE_CACHE[key] = out
    return out


def _wdf_recover_estimate_uncached(root, tpi: bool = True) -> dict:
    if not (REPO / "tools" / "wdf_recover.py").is_file():
        return {"ok": False, "why": "tools/wdf_recover.py is not shipped in "
                                    "this tree, so there is nothing to cost."}
    if root is None:
        root = coroot.read_settings().get("game_root")
    if not root:
        return {"ok": False, "why": "no install named; "
                                    "DERIVED carries the fallback range."}
    root = Path(root)
    t0 = time.time()
    census = install_census(root, paths=True)
    loose = census.pop("paths")
    arcs = [a for a in ("c3.wdf", "data.wdf") if (root / a).is_file()]
    if not arcs:
        have = sorted(census["wdf"]) or ["none"]
        return {"ok": False, "root": str(root), "census": census,
                "why": f"{root.name} has no c3.wdf/data.wdf to recover names "
                       f"for -- .wdf files here: {', '.join(have)}. "
                       f"wdf_recover would open nothing and find nothing."}
    work = wdf_recover_workload(root, loose=loose, tpi=tpi)
    rate_dict, rate = _hash_rates()
    m = WDF_RECOVER_MODEL

    search = (
        (work["dict_candidates"] + work["permute_candidates"])
        / (rate_dict * m["dict_efficiency"])
        + (work["enum_candidates"] + work["cross_candidates"])
        / (rate * m["prefixed_efficiency"]))
    gb = census["scrape_bytes"] / 1e9
    harvest_warm = m["harvest_fixed_s"] + gb * m["harvest_warm_s_per_gb"]
    harvest_cold = m["harvest_fixed_s"] + gb * m["harvest_cold_s_per_gb"]
    # Both uncertainties, not one. The scrape's cache state sets the harvest
    # end; the machine's load sets the search end, and on the measurements
    # behind WDF_RECOVER_MODEL the search end is the wider of the two.
    # In `--no-tpi` the candidate count itself is uncertain, not just the
    # rate -- MEASURED 0.88x-1.80x against three real runs, because the
    # string scrape the workload model leaves out is most of the namespace
    # once the DatPkg index is gone. Carried as a widening of the band, so
    # the two modes are not reported with the same confidence.
    res_lo = 1.0 if tpi else m["notpi_residual_low"]
    res_hi = 1.0 if tpi else m["notpi_residual_high"]
    low = search * res_lo * m["search_band_low"] + harvest_warm + m["verify_s"]
    high = search * res_hi * m["search_band_high"] + harvest_cold + m["verify_s"]
    # The single number to say out loud, inside the range rather than instead
    # of it: this box at its current load, with the scrape reading from cache.
    # On the six runs the actual landed nearer this than either end.
    typical = search + harvest_warm + m["verify_s"]

    tpi_roots = work["tpi_roots"]
    if not tpi:
        # The user turned it off. Saying "no DatPkg client is declared" here
        # would be a fact about the machine standing in for a fact about the
        # request, and they are different: declaring one still costs nothing
        # in this mode, and un-checking the box is reversible where
        # un-declaring a client is not.
        why = (f"--no-tpi: no DatPkg wordlist at all, so the wordlist is this "
               f"install's own {work['loose']:,} loose paths plus the id "
               f"conventions -- {work['namespace_dirs']:,} directories. "
               f"That is 7-14x cheaper (MEASURED, Clients/5017: 231.5M "
               f"candidates to 17.2M, 2,167 s to 302 s) and recovers ~795 "
               f"fewer names, the ones only a DatPkg plaintext index can "
               f"resolve.")
    elif tpi_roots:
        why = (f"{work['tpi']:,} plaintext names from "
               f"{', '.join(Path(p).name for p in tpi_roots)} are pulled into the "
               f"wordlist by default, which is most of the "
               f"{work['namespace_dirs']:,} directories the enumerator walks "
               f"and 85-93% of the candidates it hashes. --no-tpi drops it: "
               f"MEASURED, Clients/5517 goes from 237.7M candidates to 35.4M "
               f"and Clients/5017 from 231.5M to 17.2M -- at the cost of the "
               f"~795 names only a DatPkg index can resolve.")
    else:
        why = (f"no DatPkg client is declared, so the wordlist is this "
               f"install's own {work['loose']:,} loose paths plus the id "
               f"conventions -- {work['namespace_dirs']:,} directories. "
               f"Declaring one adds ~795 recovered names and 7-14x the "
               f"candidates: MEASURED, 17.2M to 231.5M on Clients/5017.")

    out = {
        "ok": True,
        "root": str(root),
        "tpi": bool(tpi),
        "seconds_low": round(low),
        "seconds_high": round(high),
        "seconds_typical": round(typical),
        "text": f"about {human_duration(round(typical))} for {root.name} on "
                f"this box, {_duration_range(low, high)} allowing for the "
                f"file cache and what else the machine is doing -- computed "
                f"not quoted: {work['candidates'] / 1e6:,.0f}M candidate "
                f"paths at {rate / 1000:,.0f}k/s benchmarked here, plus a "
                f"{census['scrape_bytes'] / 1e9:,.1f} GB scrape"
                + ("" if tpi else
                   " -- and the count is the weak term without a DatPkg "
                   "wordlist: MEASURED 0.88x-1.80x of the truth over three "
                   "runs, which is why the range is this wide"),
        "why": why,
        "census": census,
        "workload": work,
        "hash_rate_per_s": round(rate),
        "dict_hash_rate_per_s": round(rate_dict),
        "search_seconds": round(search),
        "harvest_seconds_warm": round(harvest_warm),
        "harvest_seconds_cold": round(harvest_cold),
        "basis": m["basis"],
        # Never the same sentence for the two modes. The candidate count is
        # exact to 0.1% with a DatPkg wordlist and 2x uncertain without one,
        # and a reader cannot tell those apart from the number alone.
        "countBasis": (
            "The candidate count is essentially exact here: MEASURED against "
            "the tool's own candidates_tested on three installs, 0.999, "
            "1.001, 1.000." if tpi else
            "THE CANDIDATE COUNT IS THE WEAK TERM IN THIS MODE. Without the "
            "DatPkg wordlist most of the directory namespace comes from the "
            "string scrape, which this estimate does not do -- so the count "
            "is 0.88x-1.80x of the truth and the range above is widened to "
            "match. " + m["notpi_residual_basis"]),
        "caveat": (
            "The range is what this box can be expected to do given the load "
            "it has RIGHT NOW, which is what the hash benchmark above "
            "measured. If the machine gets busier after you read this the "
            "run can overrun the top: MEASURED on this box, the achievable "
            "rate moved 2.15x over three hours as other work came and went. "
            "The irreducible floor is 29% -- Clients/5517 took 2,164 s and "
            "1,675 s on the same day with nothing changed."),
        "estimate_cost_seconds": round(time.time() - t0, 1),
    }
    return out


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
        # Whether THIS tree can build it. Reporting an artefact whose
        # builder is absent by design as "missing" tells a complete
        # tree it is incomplete; `supply` is the move it can make.
        buildable = (REPO / argv[0]).is_file()
        # Two different reasons a thing cannot be built here, and only one of
        # them is a dead end. Without the distinction, an artefact the owner
        # asked to be able to SUPPLY was reported as "not used by this tree".
        needs = NOT_APPLICABLE_WITHOUT.get(rel)
        applicable = needs is None or (REPO / needs[0]).is_file()
        supplied = coroot.derived_override(rel)
        # THE COST COLUMN, FOR THIS INSTALL RATHER THAN FOR THE TABLE'S.
        # Only worth computing for the artefact whose cost actually varies --
        # and only when it would be paid, i.e. it is missing and buildable.
        # Cached per install per process (`_WDF_ESTIMATE_CACHE`), because this
        # runs on every poll of the Settings page.
        est = None
        if (rel == WDF_RECOVER_REL and root is not None and buildable
                and not found):
            try:
                est = wdf_recover_estimate(root)
            except Exception as e:                      # noqa: BLE001
                # An estimate that raises must not take the health report with
                # it; the table's fallback string is still true.
                est = {"ok": False, "why": f"{type(e).__name__}: {e}"}
            if est.get("ok"):
                cost = est["text"]
        artefacts.append({
            "costEstimate": est,
            "path": rel, "exists": found,
            "buildable": buildable,
            "applicable": applicable,
            "notApplicableWhy": "" if applicable else needs[1],
            "suppliedFrom": str(supplied) if supplied else "",
            "supply": None if buildable else
                      f'py -3 tools/health.py --use "{rel}=<path>"',
            "inherited": found and not (
                REPO / coroot.derived_rel(rel, root)).is_file(),
            "bytes": p.stat().st_size if found else 0,
            "command": cmd, "cost": cost, "why": why,
        })
    # "not built yet" and "cannot be built here" are different states
    # and only the first is something to go and do.
    missing = [a for a in artefacts
               if not a["exists"] and a["buildable"]]
    unavailable = [a for a in artefacts if not a["buildable"]]
    return {
        "dir": str(REPO / "out"),
        "artefacts": artefacts,
        "missing": [a["path"] for a in missing],
        "inherited": [a["path"] for a in artefacts if a["inherited"]],
        "ok": not missing,
        # NAMES THE INSTALL, and does not quote a duration. "about 6-10
        # minutes, once" was measured on one tool against one client and this
        # box takes 28-38 minutes on four of them; `--estimate` answers for
        # the install in front of you in a few seconds, which is the whole
        # reason it exists. And without `--root` the command builds for the
        # CONFIGURED install, which is not necessarily the one this report is
        # about -- `check_derived` was given a root precisely because those
        # differ.
        "fix": (
            "Build it in one step: `py -3 tools/health.py --bootstrap"
            + (f' --root "{root}"' if root is not None else "")
            + "`. Ask what it will cost here first -- `py -3 tools/health.py "
              "--estimate" + (f' --root "{root}"' if root is not None else "")
            + "` measures this install on this box in a few seconds, and "
              "almost all of the bill is wdf_recover. Or run each command "
              "listed in the report, in order."),
    }


def tpi_context() -> dict:
    r"""**What bootstrapping client A costs depends on which OTHER clients
    are declared.**  This is the fact a per-client checklist most easily
    implies away, so it is computed and served rather than left to a comment.

    `wdf_recover.discover_tpi_roots` pulls every DECLARED DatPkg client's
    plaintext index into the wordlist by default.  On this box one is declared
    (Clients/7878, 146,196 names) and it contributes ~8,400 of the ~8,800-9,900
    namespace directories **on every client**, which is 85-93% of the
    candidates hashed.  So the checklist's rows are not independent: declaring
    a tenth client can make the other nine slower, and un-checking `--no-tpi`
    is the only per-run lever over it.

    MEASURED 2026-08-23 (`WDF_RECOVER_MEASUREMENTS`): Clients/5017 is a
    QUARTER the size of Clients/5517 and took LONGER -- 2,167 s against
    1,675 s -- and with `--no-tpi` the same two are 302 s and 571 s, in the
    order install size predicts.  Any page that says "small client, small
    bill" is wrong in the default mode and right only in the other one.
    """
    out = {"roots": [], "names": 0, "available": False, "why": ""}
    try:
        import wdf_recover as W                            # noqa: PLC0415
    except Exception as e:                                 # noqa: BLE001
        out["why"] = (f"tools/wdf_recover.py is not importable here "
                      f"({type(e).__name__}), so the wordlist cannot be "
                      f"inspected.")
        return out
    out["available"] = True
    try:
        import contextlib, io                              # noqa: PLC0415,E401
        hush = io.StringIO()
        with contextlib.redirect_stdout(hush):
            roots = W.discover_tpi_roots()
    except Exception as e:                                 # noqa: BLE001
        out["why"] = f"could not read the declared DatPkg indexes: {e}"
        return out
    out["roots"] = [str(p) for p in roots]
    if roots:
        out["why"] = (
            f"{len(roots)} declared DatPkg client(s) -- "
            f"{', '.join(Path(p).name for p in roots)} -- contribute their "
            f"whole plaintext index to the wordlist of EVERY client below. "
            f"MEASURED: that is 85-93% of the candidates hashed, and it is "
            f"why Clients/5017 (1.25 GB) took 2,167 s while Clients/5517 "
            f"(1.89 GB) took 1,675 s. The rows in this table are NOT "
            f"independent, and the only per-run lever is --no-tpi, which "
            f"costs the ~795 names a DatPkg index alone can resolve.")
    else:
        out["why"] = (
            "No DatPkg client is declared, so each client's wordlist is its "
            "own -- and only in this state does the cost track the install's "
            "size. MEASURED with --no-tpi, which is the same regime: 302 s "
            "on Clients/5017, 571 s on Clients/5517, 2,040 s on Clients/6609. "
            "Declaring one DatPkg client adds ~795 recovered names and 7-14x "
            "the candidates on EVERY client here.")
    return out


def bootstrap_checklist(roots=None, *, estimate: bool = False,
                        no_tpi: bool = False) -> dict:
    r"""**The checklist the owner asked for**: which clients, which artefacts,
    what each will cost.

        "Bootstrapping should be done against any client that the user
        supplies. Preferably with a checklist in the settings menu."

    One row per client -- every install `coroot.declared_kinds` knows about,
    plus the configured one if it was never declared -- crossed with the six
    `DERIVED` artefacts.  For each cell: is it already built FOR THAT CLIENT
    (`coroot.find_derived(rel, root)`, not the configured install's copy), can
    this tree build it, and does its builder even take an install
    (`BUILDER_INSTALL`).

    THE COST IS COMPUTED, NOT QUOTED, and only when asked for.  `estimate`
    runs `wdf_recover_estimate` per client -- MEASURED at 1-14 s each, so nine
    clients is a minute and this must never happen on a poll.  Without it the
    rows carry `DERIVED`'s fallback strings and say so.

    AND THE ROWS ARE NOT INDEPENDENT.  `tpiContext` is part of the answer, not
    a footnote: what one client costs depends on which OTHER clients are
    declared.  See `tpi_context`.
    """
    if roots is None:
        roots = _declared_roots()
    rows = []
    for root in roots:
        root = Path(str(root))
        try:
            derived = check_derived(root)
        except Exception as e:                             # noqa: BLE001
            rows.append({"root": str(root), "name": root.name,
                         "exists": root.is_dir(), "error":
                         f"{type(e).__name__}: {e}", "artefacts": []})
            continue
        arcs = sorted(a for a in ("c3.wdf", "data.wdf")
                      if (root / a).is_file())
        arts = []
        for a in derived["artefacts"]:
            spec = builder_install(a["path"])
            arts.append({**a,
                         "scope": spec["scope"],
                         "inputs": spec["inputs"],
                         "rootAware": bool(spec["flag"]),
                         "perClient": spec["scope"] == "per-base",
                         "scopeWhy": spec["why"],
                         "landsAt": coroot.derived_rel(a["path"], root)})
        est = None
        if estimate:
            try:
                est = wdf_recover_estimate(root, tpi=not no_tpi)
            except Exception as e:                         # noqa: BLE001
                est = {"ok": False, "why": f"{type(e).__name__}: {e}"}
        try:
            base = coroot.base_id(root)
        except Exception as e:                             # noqa: BLE001
            base = f"(unkeyable: {type(e).__name__})"
        rows.append({
            "root": str(root),
            "name": root.name,
            "exists": root.is_dir(),
            "baseId": base,
            "archives": arcs,
            "artefacts": arts,
            "missing": derived["missing"],
            "estimate": est,
            "error": "",
        })
    try:
        configured = str(coroot.read_settings().get("game_root") or "")
    except Exception:                                      # pragma: no cover
        configured = ""
    return {
        "clients": rows,
        "configured": configured,
        "artefacts": [rel for rel, _a, _c, _w in DERIVED],
        "estimated": bool(estimate),
        "noTpi": bool(no_tpi),
        "tpiContext": tpi_context(),
        "estimateCostNote": (
            "An estimate walks the install and benchmarks this CPU: MEASURED "
            "1-14 s per client. It is not computed on a poll, and it is not "
            "computed for artefacts that are already built."),
        "sharedNote": (
            "Two of the six are NOT per-client and are shown once rather than "
            "duplicated down the table: out/opcodes.json is built from refs/ "
            "and opens no install at all, and the two name tables "
            "(out/wdf/c3_names.json, out/dll/wdf_name_recovery.json) are "
            "hash->name and true in any install -- though both are BUILT from "
            "one, so which client you point them at decides what they "
            "resolve."),
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


#: How often a *terminal* redraws the in-place status line, and how often a
#: non-interactive run (a pipe, a log file, CI) prints a fresh liveness line.
#: A pipe gets a much slower cadence because every line it prints is kept.
_REFRESH_SECONDS = 0.5
_HEARTBEAT_SECONDS = 5.0
#: After this long with no new line from the child, the status says so rather
#: than leaving a stale label sitting under a moving clock.
_QUIET_SECONDS = 15.0


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _run_relaying(cmd, cwd, indent: str = "        ") -> tuple[int, list[str]]:
    r"""Run `cmd` and show that it is alive, without inventing a number.

    `bootstrap` used to run each builder with `stdout=subprocess.DEVNULL`,
    which threw away progress the builders were *already writing*.  Between
    the "[1/4] ..." line and the "done in 368 s" line there was nothing at
    all, and a five-to-nine minute silence is indistinguishable from a hang.
    The owner watched one and believed it had stalled.  It had not.

    So this relays instead of reimplementing -- the same move `ThumbRunner`
    in `tools/coviewer.py` makes on `tools/thumbs.py`: read the child's own
    output, split on `\r` *or* `\n` (some tools in this tree write
    carriage-return progress), and show its most recent line.

    **Two signals, both substantiated, neither a percentage.**

    * The child's latest line, verbatim.  `wdf_recover.py` reports
      ``enumerate dir 800/9045   24233/25013 ( 96.9%) tested=... 461s``;
      `meshtex.py` reports ``scan 3500/6390`` on stderr.  Those counts are
      the builders' own and are relayed unaltered.
    * Elapsed time since *this* builder started, plus (on a terminal) a
      spinner.  That is the honest floor: it is true of a silent builder and
      of a chatty one, and it says "alive", not "77% done".

    Nothing here derives an overall percentage or an ETA.  A builder's phases
    do not cost proportionally, and not even repeatably: MEASURED on
    Clients/5517, `wdf_recover`'s wordlist harvest is 312 s cold and 57 s
    warm, in the same tool on the same install.  A bar drawn over that would
    be confidently wrong, and this project has paid for those before.

    **This is a progress display, not a transcript, and the difference is
    lossy on purpose.**  At most one update is painted per period, so when
    several lines arrive inside one window only the newest is shown.  That is
    right for progress lines, which supersede each other, and it does drop
    record lines a full log would keep -- run the builder directly (the
    report prints the command) if you want all of it.

    Returns `(returncode, tail)`; the tail is the last 40 lines, so a failure
    can print evidence instead of only telling you to re-run it.
    """
    import queue                                    # noqa: PLC0415
    import subprocess                               # noqa: PLC0415
    import threading                                # noqa: PLC0415

    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, errors="replace")
    lines: "queue.Queue[str]" = queue.Queue()

    def pump() -> None:
        buf = ""
        try:
            while True:
                ch = proc.stdout.read(1)            # type: ignore[union-attr]
                if not ch:
                    break
                if ch in ("\r", "\n"):
                    if buf.strip():
                        lines.put(buf.strip())
                    buf = ""
                else:
                    buf += ch
        except Exception as e:                       # pragma: no cover
            lines.put(f"(reader stopped: {e})")
        if buf.strip():
            lines.put(buf.strip())

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    tty = sys.stdout.isatty()
    period = _REFRESH_SECONDS if tty else _HEARTBEAT_SECONDS
    t0 = time.time()
    last, tail, width, tick, shown = "", [], 0, 0, 0.0
    last_at = t0
    while True:
        # Read liveness BEFORE draining: if the pump has finished, everything
        # it ever queued is already visible to this drain.
        alive = proc.poll() is None or reader.is_alive()
        while True:
            try:
                last = lines.get_nowait()
            except queue.Empty:
                break
            last_at = time.time()
            tail.append(last)
            del tail[:-40]
        now = time.time()
        if now - shown >= period:
            tick += 1
            spin = "|/-\\"[tick % 4] + "  " if tty else ""
            # A phase the builder has not spoken from in a while is exactly
            # the moment someone reaches for Ctrl-C, and a frozen line beside
            # a moving clock reads as "stuck at dictionary". Say which it is:
            # the clock is the run, this is the silence inside it.
            quiet = now - last_at
            mark = (f"   [+{_mmss(quiet)} with no new output]"
                    if last and quiet >= _QUIET_SECONDS else "")
            status = (f"{indent}{_mmss(now - t0):>6}  {spin}"
                      + (last or "(working; no output from it yet)") + mark)
            if tty:
                width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
                sys.stdout.write("\r" + status[:width].ljust(width))
            else:
                sys.stdout.write(status + "\n")
            sys.stdout.flush()
            shown = now
        if not alive:
            break
        time.sleep(0.1)
    if tty and width:                                # erase the status line
        sys.stdout.write("\r" + " " * width + "\r")
        sys.stdout.flush()
    rc = proc.wait()
    if proc.stdout is not None:
        proc.stdout.close()
    return rc, tail


def bootstrap(only_missing: bool = True, root=None, *,
              no_tpi: bool = False, only=None) -> int:
    r"""Run the derived-data builders **for `root`**, in dependency order.

    Deliberately a thin sequencer over the existing tools -- it adds the
    ordering constraint, relays their progress (`_run_relaying`), points each
    builder at the install it was asked about, and **checks that each one's
    artefact landed there**.  See `BUILDER_INSTALL` for why the last of those
    is not optional.

    `root` is which install to build for.  `None` means the configured one,
    which is the old behaviour and is still right for a machine with one
    client -- but it is now a choice the caller made rather than the only
    thing the function could do.  Before this, `health.py --bootstrap --root
    D:/other` PARSED the flag and built for the configured install anyway.

    `no_tpi` drops the DatPkg wordlist from `wdf_recover` alone.  It is
    offered here because it is the single biggest lever on what a bootstrap
    costs and it is not about this install at all: `discover_tpi_roots` pulls
    every DECLARED DatPkg client's plaintext index in, which is 85-93% of the
    candidates hashed **on every client**.  MEASURED, Clients/5017: 2,167 s
    with it and 302 s without, at the cost of the ~795 names only a DatPkg
    index can resolve.  See `WDF_RECOVER_MEASUREMENTS`.

    `only` is an iterable of `DERIVED` paths to restrict the run to, for the
    checklist.  `None` means all of them.
    """
    want = None if only is None else {str(x) for x in only}
    if want is not None:
        unknown = want - {rel for rel, _a, _c, _w in DERIVED}
        if unknown:
            print(f"not derived artefacts: {', '.join(sorted(unknown))}",
                  file=sys.stderr)
            return 2
    if root is not None:
        root = Path(root)
        print(f"bootstrapping for {root}")
        if not root.is_dir():
            print(f"  {root} is not a directory -- nothing here to build "
                  f"from.", file=sys.stderr)
            return 2
        try:
            print(f"  artefacts key to base {coroot.base_id(root)}\n")
        except Exception as e:                           # noqa: BLE001
            print(f"  (could not key this install: {type(e).__name__}: {e})\n")
    # `find_derived(rel)` with no root resolves every per-base artefact
    # against the CONFIGURED install -- so a bootstrap for another client
    # skipped the rows that client was missing because a different client had
    # them. The root goes here too, not only into the argv.
    todo = [(rel, argv, cost) for rel, argv, cost, _ in DERIVED
            if (want is None or rel in want)
            and (not only_missing or coroot.find_derived(rel, root) is None)]
    # An artefact whose BUILDER is not in this tree is not a failure.
    # COMod ships the asset/modding subset, and out/opcodes.json is
    # protocol ("protocol, not assets" -- coroot) built from refs/, not
    # from an install. Offering it and then dying on a missing script told
    # the owner their tree was broken when it was complete for what it is.
    # Named rather than dropped: a line that vanishes reads as one nobody
    # needed, and this one has a way out worth printing.
    absent = [(rel, argv, cost) for rel, argv, cost in todo
              if not (REPO / argv[0]).is_file()]
    todo = [x for x in todo if x not in absent]
    for rel, argv, _cost in absent:
        have = coroot.derived_override(rel)
        if have:
            print(f"  supplied: {rel}")
            print(f"        using {have}")
            continue
        needs = NOT_APPLICABLE_WITHOUT.get(rel)
        if needs is not None and not (REPO / needs[0]).is_file():
            # Neither the builder NOR the tool that gives it a purpose is
            # here. Asking for a path would be asking for a file nothing
            # in this tree would open.
            print(f"  not applicable here: {rel}")
            print(f"        {needs[1]}, and {needs[0]} is not shipped in "
                  f"this tree.")
            continue
        print(f"  not in this tree: {rel}")
        print(f"        {argv[0]} is not shipped here, so this artefact "
              f"cannot be built.")
        print( "        If you have one built elsewhere, point at it:")
        print(f'        py -3 tools/health.py --use "{rel}=<path>"')
    if absent:
        print()
    if not todo:
        # Say what is actually true. "already built" over a set that includes
        # artefacts this tree never built reads as a clean bill of health for
        # a state nobody checked -- and the two lines above just said they are
        # not there.
        #
        # COUNTED OVER WHAT WAS ASKED FOR, not over all six. With `--only` the
        # all-six count produces a sentence that is false twice: it reports
        # rows the user did not ask about, and its "N cannot be built in this
        # tree (named above)" names nothing, because the artefacts it is
        # counting were never in `todo` and so were never printed.
        asked = [rel for rel, _a, _c, _w in DERIVED
                 if want is None or rel in want]
        have = sum(1 for rel in asked
                   if coroot.find_derived(rel, root) is not None)
        total = len(asked)
        if have == total:
            print("derived data is already built; nothing to do "
                  "(use --bootstrap-all to force)")
        else:
            print(f"nothing left to build here: {have} of {total} artefact(s) "
                  f"present, {total - have} cannot be built in this tree "
                  f"(named above). Use --bootstrap-all to rebuild what can be.")
        return 0
    print(f"building {len(todo)} artefact(s) into {REPO / 'out'}\n")
    for i, (rel, argv, cost) in enumerate(todo, 1):
        full = builder_argv(rel, argv, root, no_tpi=no_tpi)
        cmd = [sys.executable, str(REPO / full[0]), *full[1:]]
        spec = builder_install(rel)
        print(f"[{i}/{len(todo)}] {rel}  (~{cost})")
        print(f"        {' '.join(full)}")
        if root is not None and not spec["flag"]:
            # Said out loud rather than skipped. A row that silently ignores
            # the install the user chose is the same defect one level down,
            # and the honest version is cheap: name it, once, with the reason.
            print(f"        (no --root: {argv[0]} opens no install -- "
                  f"{spec['scope']}, inputs from {spec['inputs']})")
        # Read BEFORE the builder starts, so "did this run write it" is a
        # comparison against a clock the builder cannot have beaten.
        t0 = time.time()
        rc, tail = _run_relaying(cmd, str(REPO))
        if rc != 0:
            print(f"        FAILED (exit {rc}). Its last words:")
            for line in tail[-15:]:
                print(f"        | {line}")
            print(f"        Re-run it directly to see the rest:\n"
                  f"        py -3 {' '.join(full)}")
            return rc
        # THE LANDING CHECK. Exit 0 is the builder's opinion; this is the
        # artefact's. A builder that accepts --root and writes to the
        # configured install exits 0 and is caught here and nowhere else.
        land = check_landing(rel, root, since=t0)
        if not land["ok"]:
            print(f"        LANDED WRONG after {time.time() - t0:.0f} s.")
            print(f"        {land['why']}")
            print(f"        expected: {land['where']}")
            if land["landedAt"]:
                print(f"        Its --root was ignored. Do not re-run the "
                      f"bootstrap; fix {argv[0]}.")
            return 4
        print(f"        done in {time.time() - t0:.0f} s"
              + (f", {land['bytes'] / 1e6:.1f} MB" if land.get("bytes") else "")
              + f"  -> {coroot.derived_rel(rel, root)}")
    print("\nderived data built"
          + (f" for {root}." if root is not None else "."))
    return 0


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def default_jobs() -> int:
    """The worker count tools/thumbs.py would pick if not told otherwise."""
    return max(1, (os.cpu_count() or 2) - 1)


#: One corpus census per install per process.  `thumbnail_state` is called on
#: every poll of the Settings page's thumbnail panel -- every 1.5 s while a
#: render runs -- and the census walks the install, so an uncached one would
#: cost more than the thing it is describing.  Same shape and same reason as
#: `_WDF_ESTIMATE_CACHE`.
_THUMB_CORPUS_CACHE: dict[str, dict] = {}


def thumbnail_corpus(root=None, *, use_cache: bool = True) -> dict:
    r"""The mesh and texture work list `tools/thumbs.py` would build for
    **this** install, and what it would cost -- rather than what some other
    install cost.

    Two counts, neither of them a table lookup, both defined to be exactly
    what `thumbs.py` will enumerate:

      * **meshes** -- `thumbs.load_worklist` on this base's own
        `out/meshtex/coverage.json`, which is the very list the run
        iterates.  Its own function, called with this root's path, so the
        two cannot drift.  ~0.1 s.
      * **textures** -- `thumbs.texture_universe` is loose `.dds` union the
        recovered archive names, and that union is counted here from
        `install_census` + `meshtex.pooled_names` instead of by constructing
        the index.  Same answer, MEASURED equal on four installs
        (`tests/test_health_thumbs.py`), and 10-18x cheaper: 2.1 s against
        36.8 s on Clients/7878, which matters because this is on a 1.5 s
        poll.

    Returns ``{"ok": False, ...}`` with a `why` rather than a number when the
    mesh half cannot be counted -- `out/meshtex/coverage.json` has not been
    built for this base, and building it live costs ~2 minutes, which is not
    a thing a status poll may start.  **It does not fall back to
    `THUMB_FACTS`**: substituting another install's corpus is the defect this
    function exists to remove, and a caller that quietly did it again would
    be indistinguishable from the version before it.  `textures` is still
    reported in that case, because the census answers it without meshtex.

    The disk figures are bytes of PNG, from `thumbs`' own per-pixel
    constants -- the same arithmetic `--dry-run` prints, and within 0.5% of a
    real run's manifest on the install those constants were fitted to.  They
    read up to 24% HIGH on an install with simpler art; see
    `thumbs.MESH_BYTES_PER_PIXEL`.
    """
    key = str(root or "")
    if use_cache and key in _THUMB_CORPUS_CACHE:
        return _THUMB_CORPUS_CACHE[key]
    out = _thumbnail_corpus_uncached(root)
    if use_cache:
        _THUMB_CORPUS_CACHE[key] = out
    return out


def _thumbnail_corpus_uncached(root) -> dict:
    import thumbs                                   # noqa: PLC0415
    import meshtex                                  # noqa: PLC0415

    t0 = time.time()
    if root is None:
        root = coroot.read_settings().get("game_root")
    # `ok` is BOTH halves; `texturesCounted` is the cheap half on its own.
    # They are separate because the two refuse for different reasons and a
    # caller can use the texture half without the mesh half -- but only when
    # the census actually ran, which is what the flag records.
    out: dict = {"ok": False, "texturesCounted": False, "root": str(root or ""),
                 "meshes": 0, "unmatchedMeshes": 0, "textures": 0}
    if not root:
        out["why"] = "no install named, so there is no corpus to count."
        return out

    # -- textures: the census union, not a constructed index ---------------
    #
    # `install_census` DOES NOT RAISE on an unreadable root: it catches OSError
    # per directory and continues, so a path that does not exist returns zeros
    # rather than an error. That makes the guard below load-bearing rather than
    # defensive, and it is here because the first version of this function got
    # it wrong in the way this whole change exists to prevent.
    #
    # MEASURED on the broken version: `thumbnail_corpus("Z:/does/not/exist")`
    # returned **19,287 textures** -- which is exactly the `.dds` count of the
    # GLOBAL recovered-name pool, because the census contributed an empty set
    # and the union was the pool alone. A client on a disconnected drive or at
    # a stale path would have been offered "Textures only: 19,287 images,
    # 106 MB, 30-60 s" for an install that is not there. A global fact printed
    # as a per-client one, which is the defect this module was just rewritten
    # to remove, reintroduced one layer down.
    census = install_census(root, paths=True)
    if not Path(root).is_dir():
        out["why"] = (f"{root} is not a directory -- no install there to "
                      f"count. (A count taken anyway would be the global "
                      f"recovered-name pool, which is not this client's.)")
        return out
    if not census["files"]:
        out["why"] = (f"nothing at all under {root}, so there is no corpus to "
                      f"count. The recovered-name pool is GLOBAL and would "
                      f"answer for any path; a number from it alone would not "
                      f"be about this client.")
        return out
    pooled, names_loaded = meshtex.pooled_names()
    universe = census["paths"] | pooled
    out["textures"] = sum(1 for p in universe if p.endswith(".dds"))
    out["c3Files"] = sum(1 for p in universe if p.endswith(".c3"))
    out["namesRecovered"] = names_loaded
    out["looseFiles"] = census["files"]
    out["texturesCounted"] = True

    # -- meshes: thumbs.py's own work list, for THIS base -------------------
    cov = coroot.find_derived("out/meshtex/coverage.json", root)
    if cov is None:
        out["seconds"] = round(time.time() - t0, 2)
        # The command and its cost are READ OUT OF `DERIVED`, not restated.
        # This string is advice someone will act on, and a builder invocation
        # written twice is one that goes stale in the copy nobody runs.
        rel = "out/meshtex/coverage.json"
        argv, cost = next(((a, c) for r, a, c, _w in DERIVED if r == rel),
                          (["tools/meshtex.py", "--coverage"], ""))
        out["why"] = (
            f"the mesh work list comes from {rel} and this client has not "
            f"built one. Counting it live means parsing every .c3 (~2 "
            f"minutes), which is not something a status poll may start -- "
            f"run `py -3 {' '.join(argv)}` (or Bootstrap) and the count "
            f"appears"
            + (f" ({cost.split(' -- ')[0]})" if cost else "")
            + ". The texture half above is this client's and is not affected.")
        return out
    try:
        jobs, unmatched = thumbs.load_worklist(cov)
    except (OSError, ValueError, KeyError) as e:
        out["seconds"] = round(time.time() - t0, 2)
        out["why"] = f"{cov.name} is unreadable: {type(e).__name__}: {e}"
        return out
    out["meshes"] = len(jobs)
    out["unmatchedMeshes"] = len(unmatched)
    out["coverage"] = str(cov)
    out["ok"] = True
    out["megabytes"] = thumbnail_megabytes(out["meshes"], out["textures"])
    out["seconds"] = round(time.time() - t0, 2)
    return out


def thumbnail_megabytes(meshes: int, textures: int) -> dict:
    """Disk for a run of this size, in MB of PNG, from `thumbs`' constants.

    **Bytes of PNG, not size on disk** -- the distinction that put 486 MB in
    `THUMB_FACTS` against the 359.6 its own run recorded.  All three
    constants, their units and the installs they were fitted on are at
    `thumbs.MESH_BYTES_PER_PIXEL`; this function only multiplies.

    `manifests` is the two JSON manifests, which are not free at this scale:
    380 B an entry, MEASURED on two real runs -- 26.9 MB over 71,784 entries
    (Classic Conquer 2.0's corpus) and 15.6 MB over 40,291 (Clients/5517).
    It used to be a flat 27, i.e. the first of those totals charged whole to
    a client with a twentieth of the entries.
    """
    import thumbs                                   # noqa: PLC0415
    mesh_b = meshes * thumbs.DEFAULT_SIZE ** 2 * thumbs.MESH_BYTES_PER_PIXEL
    tex_b = (textures * thumbs.DEFAULT_TEX_SIZE ** 2
             * thumbs.TEXTURE_BYTES_PER_PIXEL)
    man_b = (meshes + textures) * thumbs.MANIFEST_BYTES_PER_ENTRY
    return {"meshes": round(mesh_b / 1e6), "textures": round(tex_b / 1e6),
            "manifests": round(man_b / 1e6)}


def estimate_seconds(jobs: int, meshes: int = 0,
                     textures: int = 0) -> tuple[int, int]:
    """(optimistic, pessimistic) seconds for a cold run at `jobs` workers.

    `meshes` and `textures` are **counts of thumbnails**, this client's own.
    They used to be booleans meaning "include the reference install's mesh
    half / texture half", so every client was quoted one client's job.  The
    parameters kept their names when they stopped being flags, so a boolean
    is REFUSED rather than counted: `True` is 1 in arithmetic and 1 lands on
    the 20 s floor below, which would print "20 s-40 s" over a seven-minute
    job -- wrong, and wrong in the shape that looks right.  The floor is
    what makes the guard worth writing; without it a stale call would return
    0 and be obvious.  Nothing outside this module calls it today.
    """
    for name, v in (("meshes", meshes), ("textures", textures)):
        if isinstance(v, bool):
            raise TypeError(
                f"estimate_seconds({name}=) is a COUNT of thumbnails, not a "
                f"flag; it stopped meaning \"include the reference install's "
                f"{name}\" when the plan started counting this client's. "
                f"Pass health.thumbnail_corpus(root)[{name!r}].")
    core = (meshes * MESH_CORE_SECONDS_EACH
            + textures * TEXTURE_CORE_SECONDS_EACH)
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


def thumbnail_state(server: str = "", root=None) -> dict:
    """What the thumbnail cache currently holds, and what filling it would
    cost.  With ``server``, reports that library view's own cache
    (out/thumbs/servers/<name>/) instead of the install's.

    Everything factual comes from `tools/thumbs.py` (its output directory and
    manifest); this function only reads and describes.

    ``root`` is WHICH INSTALL is being browsed, and both halves need it.  The
    cache directory is per-base, so reading `thumbs.OUT_DIR` -- resolved at
    import against the CONFIGURED root -- showed the configured client's
    cache while the page said it was describing another; and the plan is
    computed from `thumbnail_corpus(root)`, which is the point of the whole
    exercise.  Defaults to the configured install, which is right for the
    console report and for a viewer serving only that one.
    """
    import thumbs                                   # noqa: PLC0415

    if server:
        out_dir = thumbs.REPO / "out" / "thumbs" / "servers" / server
        manifest = out_dir / "manifest.json"
    else:
        out_dir = coroot.derived_path("out/thumbs", root)
        manifest = out_dir / "manifest.json"
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

    corpus = thumbnail_corpus(root)
    state["corpus"] = corpus
    want_m = corpus["meshes"]
    want_t = corpus["textures"]
    have_m, have_t = state["meshes"], state["textures"]
    if have_m == 0 and have_t == 0:
        state["status"] = "none"
    elif not corpus["ok"]:
        # No denominator for the mesh half, so "complete" is not answerable.
        # It is not assumed either way: `partial` is what a cache of unknown
        # completeness is, and the reason travels in `corpus.why`.
        state["status"] = "partial"
    elif have_m >= want_m * 0.95 and have_t >= want_t * 0.95:
        state["status"] = "complete"
    elif have_m >= want_m * 0.95:
        state["status"] = "meshes-only"
    else:
        state["status"] = "partial"

    jobs = default_jobs()
    mb = corpus.get("megabytes") or thumbnail_megabytes(want_m, want_t)
    # The manifests are one file pair for the whole run, so a meshes-only
    # option carries its share of them rather than all or none.
    mesh_mb = mb["meshes"] + round(
        (want_m / max(1, want_m + want_t)) * mb["manifests"])
    total_mb = mb["meshes"] + mb["textures"] + mb["manifests"]
    m_low, m_high = estimate_seconds(jobs, want_m, 0)
    a_low, a_high = estimate_seconds(jobs, want_m, want_t)

    def span(low: int, high: int, counted: bool) -> str:
        """The estimate, or the reason there isn't one.

        An option whose corpus was not counted must not print a duration.
        `want_m` is 0 when the mesh census is missing, and 0 meshes runs into
        `estimate_seconds`' own 20 s floor -- so the page would have said
        "Meshes only: 0 images, 20 s-40 s" over a job that is neither empty
        nor short.  A zero that means "not counted" and a zero that means
        "nothing to do" are the same number, and only this side of the wire
        knows which one it is holding.
        """
        if not counted:
            return "not counted yet"
        return f"{human_duration(low)}-{human_duration(high)}"

    counted_m = corpus["ok"]
    # The texture row used to pass `True` unconditionally, on the reasoning
    # that the census answers textures without meshtex. True -- until the
    # CENSUS is the thing that refused, at which point that row printed
    # "0 images, 0 s-0 s" over a root that does not exist. `estimate_seconds`
    # has a 20 s floor, but this row is a SUBTRACTION of two floored figures,
    # so the floor that protects the others does not reach it.
    counted_t = corpus.get("texturesCounted", False)
    unknown = (" This client's mesh work list has not been counted, so the "
               "number above is not an estimate: " + corpus.get("why", ""))
    state["plan"] = {
        "cpus": os.cpu_count(),
        "jobs": jobs,
        "reference": REFERENCE_RUN,
        "resumable": True,
        # Named so a reader can tell a counted number from an uncounted one
        # without knowing this module: the plan is only as good as the census
        # under it, and when the mesh census is missing the mesh rows below
        # are zero rather than somebody else's corpus.
        "corpusMeasured": corpus["ok"],
        "corpusFor": corpus["root"],
        "corpusWhy": corpus.get("why", ""),
        "options": [
            {
                "id": "meshes",
                "label": "Meshes only  (recommended)",
                "count": want_m,
                "megabytes": mesh_mb,
                "estimate": span(m_low, m_high, counted_m and counted_t),
                "why": (f"The {want_m:,} model thumbnails are what the "
                        "character builder and model mode use. This is the "
                        "half that matters and about a third of the cost."
                        if counted_m else
                        "The model thumbnails are what the character builder "
                        "and model mode use." + unknown),
                "argv": ["--all", "--resume"],
            },
            {
                "id": "all",
                "label": "Everything (meshes + textures)",
                "count": want_m + want_t,
                "megabytes": total_mb,
                "estimate": span(a_low, a_high, counted_m and counted_t),
                "why": (f"Adds thumbnails for all {want_t:,} textures. Nice "
                        "for browsing the texture library; most of the time "
                        "and nearly all of the disk.")
                       + ("" if counted_m else unknown),
                "argv": ["--all", "--textures", "--resume"],
            },
            {
                "id": "textures",
                "label": "Textures only (finish a meshes-only run)",
                # The texture half is counted from the install census, which
                # needs no meshtex index -- so this row stays a real estimate
                # when only the MESH half is missing, and abstains when the
                # census itself is what refused.
                "count": want_t,
                "megabytes": total_mb - mesh_mb,
                "estimate": span(a_low - m_low, a_high - m_high, counted_t),
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
            # `explicit`, not the configured root: the rest of this report is
            # about that install, and the thumbnail block would otherwise
            # describe a different one.
            rep["thumbnails"] = thumbnail_state(root=explicit)
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
        f"/{sum(1 for a in der.get('artefacts', []) if a.get('applicable', True))}"
        f" built"
        + (f", {sum(1 for a in der.get('artefacts', []) if not a.get('applicable', True))}"
           f" n/a here"
           if any(not a.get('applicable', True) for a in der.get('artefacts', []))
           else "")
        + ")")
    for a in der.get("artefacts", []):
        # Four states, and the last two were being printed as the second.
        # "MISSING <- py -3 tools/skinbuild.py" told the owner to run a script
        # this tree does not ship, and "inherited" was claiming a copy found
        # in a primary checkout when the truth was a path they had supplied.
        if a.get("suppliedFrom"):
            state = "supplied"
        elif a.get("inherited"):
            state = "inherited"
        elif a["exists"]:
            state = "ok"
        elif not a.get("applicable", True):
            state = "n/a"
        elif not a.get("buildable", True):
            state = "supply?"
        else:
            state = "MISSING"
        line = f"           {state:<10} {a['path']}"
        if a["exists"]:
            line += f"  {a['bytes'] / 1e6:,.1f} MB"
            if state == "supplied":
                line += f"  <- {a['suppliedFrom']}"
        elif state == "n/a":
            # No command: there is nothing here that would run it, and
            # nothing here that would read the result either.
            line += f"   not used by this tree -- {a.get('notApplicableWhy', '')}"
        elif state == "supply?":
            # Buildable nowhere in THIS tree, but usable if pointed at a copy.
            line += f"   <- {a['supply']}"
        else:
            line += f"   <- {a['command']}   (~{a['cost']})"
        add(line)

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
            mbs = (th.get("corpus") or {}).get("megabytes") or {}
            full = mbs.get("meshes", 0) + mbs.get("textures", 0) + \
                mbs.get("manifests", 0)
            add(f"         {th.get('meshes', 0):,} mesh + "
                f"{th.get('textures', 0):,} texture, "
                f"{th.get('bytes', 0) / 1e6:,.0f} MB of PNG"
                + (f" (a full run for this client is ~{full:,} MB with the "
                   f"manifests)" if full else "")
                + f", generated {th.get('generated')}")
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
    ap.add_argument("--use", action="append", metavar="REL=PATH",
                    help="point a derived artefact at an existing copy, "
                         "for artefacts this tree cannot build. Repeatable. "
                         'e.g. --use "out/opcodes.json=D:/co/out/opcodes.json". '
                         "REL=  with no path clears it.")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--no-write", action="store_true",
                    help="do not write out/health.json")
    ap.add_argument("--bootstrap", action="store_true",
                    help="build the missing generated data in out/. One-off, "
                         "and almost all of it is wdf_recover: MEASURED "
                         "2,164 s on Clients/5517 with a DatPkg client "
                         "declared and ~5 min without one. Run --estimate "
                         "first for the number on YOUR install.")
    ap.add_argument("--bootstrap-all", action="store_true",
                    help="rebuild all of it, even what already exists")
    ap.add_argument("--only", action="append", metavar="REL",
                    help="restrict --bootstrap to these artefacts. "
                         "Repeatable; default is all six.")
    ap.add_argument("--no-tpi", action="store_true",
                    help="build out/wdf/ WITHOUT the DatPkg wordlist. The "
                         "single biggest lever on the cost and it is not "
                         "about this install: every DECLARED DatPkg client's "
                         "plaintext index is pulled in by default, 85-93%% of "
                         "the candidates hashed on EVERY client. MEASURED on "
                         "Clients/5017: 2,167 s with, 302 s without, for the "
                         "~795 names only a DatPkg index resolves.")
    ap.add_argument("--checklist", action="store_true",
                    help="what a bootstrap would cost, per declared client "
                         "and per artefact, and build nothing")
    ap.add_argument("--no-estimate", action="store_true",
                    help="with --checklist: skip the measured per-client "
                         "wdf_recover estimate (1-14 s each) and print the "
                         "table's fallback range instead")
    ap.add_argument("--estimate", action="store_true",
                    help="measure what a bootstrap would cost on this "
                         "install -- takes a few seconds and builds nothing")
    ap.add_argument("--provenance", action="store_true",
                    help="only audit which derived artefacts can say what "
                         "install they were built from, and exit")
    a = ap.parse_args(argv)

    if a.checklist:
        book = bootstrap_checklist(
            [a.root] if a.root else None,
            estimate=not a.no_estimate, no_tpi=a.no_tpi)
        if a.json:
            print(json.dumps(book, indent=2))
            return 0
        print(f"bootstrap checklist -- {len(book['clients'])} client(s), "
              f"{len(book['artefacts'])} artefact(s)"
              + ("  [--no-tpi]" if book["noTpi"] else ""))
        print(f"\n  {book['tpiContext']['why']}\n")
        for row in book["clients"]:
            mark = " (configured)" if row["root"] == book["configured"] else ""
            print(f"{row['name']}{mark}  {row['root']}")
            if row.get("error"):
                print(f"    {row['error']}")
                continue
            print(f"    base {row['baseId']}   archives: "
                  f"{', '.join(row['archives']) or 'none'}")
            for art in row["artefacts"]:
                state = ("built" if art["exists"] else
                         "MISSING" if art["buildable"] else "not buildable")
                kind = ("per client" if art["perClient"] else
                        "shared" + ("" if art["rootAware"]
                                    else ", no --root"))
                print(f"    [{'x' if art['exists'] else ' '}] "
                      f"{art['path']:<38} {state:<14} {kind}")
                print(f"          -> {art['landsAt']}")
                print(f"          {art['cost']}")
            if row.get("estimate") and row["estimate"].get("ok"):
                e = row["estimate"]
                print(f"    wdf_recover here: {e['text']}")
        print(f"\n  {book['sharedNote']}")
        print(f"\n  {book['estimateCostNote']}")
        return 0

    if a.estimate:
        est = wdf_recover_estimate(coroot.root_from_args(a), tpi=not a.no_tpi)
        if a.json:
            print(json.dumps(est, indent=2))
            return 0
        if not est.get("ok"):
            print(f"cannot estimate: {est.get('why')}")
            return 0
        c, w = est["census"], est["workload"]
        print(f"tools/wdf_recover.py on {est['root']}")
        print(f"  install       {c['dirs']:,} dirs, {c['files']:,} files, "
              f"{c['bytes'] / 1e9:,.2f} GB "
              f"({c['scrape_bytes'] / 1e9:,.2f} GB of it gets scraped)")
        print(f"  namespace     {w['namespace_dirs']:,} directories, "
              f"{w['candidates']:,} candidate paths")
        print(f"    from        {w['loose']:,} loose + {w['tpi']:,} DatPkg + "
              f"{w['id_words']:,} id-convention paths")
        print(f"  this box      {est['hash_rate_per_s']:,} prefixed hashes/s, "
              f"{est['dict_hash_rate_per_s']:,} whole-path hashes/s")
        print(f"  ESTIMATE      about {human_duration(est['seconds_typical'])}"
              f", and {human_duration(est['seconds_low'])} to "
              f"{human_duration(est['seconds_high'])} allowing for cache and "
              f"machine load")
        print(f"                = {est['search_seconds']:,} s of hashing + "
              f"{est['harvest_seconds_warm']:,}-"
              f"{est['harvest_seconds_cold']:,} s of harvest")
        print(f"  measured in   {est['estimate_cost_seconds']} s")
        print(f"\n  {est['why']}")
        print(f"\n  {est['countBasis']}")
        print(f"\n  {est['caveat']}")
        print(f"\n  model: {est['basis']}")
        return 0

    if a.use:
        # Handled before anything reads an artefact, so a run that both
        # supplies a path and reports on it sees the new value.
        known = {rel for rel, _argv, _c, _w in DERIVED}
        for spec in a.use:
            rel, sep, path = spec.partition("=")
            rel = rel.strip()
            if not sep:
                print(f"--use wants REL=PATH; got {spec!r}", file=sys.stderr)
                return 2
            if rel not in known:
                # A typo here would otherwise sit in the config doing
                # nothing, and the artefact would still read as missing.
                print(f"{rel!r} is not a derived artefact. Known:",
                      file=sys.stderr)
                for k in sorted(known):
                    print(f"    {k}", file=sys.stderr)
                return 2
            path = path.strip().strip(chr(34))
            if not path:
                coroot.set_derived_override(rel, None)
                print(f"cleared: {rel}")
                continue
            p = Path(path)
            if not p.exists():
                # Refused rather than saved. A stored path that is not
                # there makes the artefact read as absent anyway, and the
                # setting then looks like it was ignored.
                print(f"no such file: {p}", file=sys.stderr)
                return 2
            coroot.set_derived_override(rel, p)
            print(f"{rel}\n    -> {p.resolve()}")
        return 0

    if a.provenance:
        return provenance.main(["--root", str(a.root)] if a.root else [])

    if a.bootstrap or a.bootstrap_all:
        # `a.root`, NOT `root_from_args`: "nobody said" has to stay
        # distinguishable from "the user named the configured install", or a
        # machine with no configured root cannot bootstrap at all.
        rc = bootstrap(only_missing=not a.bootstrap_all,
                       root=(Path(a.root) if a.root else None),
                       no_tpi=a.no_tpi, only=a.only)
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
