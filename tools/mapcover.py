#!/usr/bin/env python3
r"""
mapcover.py -- which installs can carry map cover sprites, and how many maps.

    py -3 tools/mapcover.py                      # the matrix, as a table
    py -3 tools/mapcover.py --json out/x.json    # ...and as data
    py -3 tools/mapcover.py --markdown docs/map_cover_support.md
    py -3 tools/mapcover.py --root "C:/Program Files/Classic Conquer 2.0"

THE QUESTION THIS ANSWERS, AND THE ONE IT DOES NOT
---------------------------------------------------
Cover sprites and ambient effects live in the `.DMap`'s **second counted
record list**, which only v1006 writes. `core/dmap.py` already establishes
that and this module does not re-derive it -- `LATE_LAYERS_MIN_VERSION = 1006`
is declared there with its evidence: 25,308 tag-4 covers, 11,597 tag-19
effects and 4 tag-1 scenes across 176 v1006 maps, while the same walk closes
19 of 19 v1005 maps and all 1,831 pre-1005 maps **without** one and
desynchronises immediately if the list is assumed. That asymmetry is why the
boundary is version-gated rather than sniffed.

What is NOT in the repo, and what this adds, is the **per-install** answer:
*on the install in front of me, how many maps can carry a cover at all?* An
editor that offers to place a cover has to answer that per map, and an owner
asking "does this work on my client?" is asking it per install.

WHY A MAP WITH NO RECORDS IS NOT A MAP THAT CANNOT HOLD ONE
------------------------------------------------------------
`capable` and `populated` are separate counts and must stay separate. A v1006
map with zero late records is an empty canvas -- the format is there and a
cover may be added. A v1004 map with zero late records cannot take one from
this write path at all -- its covers, if any, sit in the FIRST layer table,
which nothing here writes yet (the limit is the editor's, not the map's).
Collapsing them into "maps without covers" would tell the editor to offer the
same thing in both cases, and it is right in only one of them.

THE READ PATH IS ARCHIVE-AWARE, DELIBERATELY AND BY NAME
---------------------------------------------------------
Everything here goes through `dmap.map_names` / `dmap.parse_map`. **Never a
loose glob.** Globbing `map/map/*.DMap` on 7878 reports **0 maps**; it has
**470**, because that build ships them inside containers. The same error, on
the same night, reported 408 of 437 NPCs broken on an install whose art is
packed. An install that packs its assets answers "absent" to every loose-file
question, and the answer is confident, specific and wrong.

Related, one level up: a build is pinned by `version.dat`, never by folder
name -- `ThroneOfKings7939` is 7938, and no install on this box declares 7939.
This module reports the directory name as `dir` and makes no claim that it is
a version.

WHAT AN UNPARSEABLE MAP DOES
-----------------------------
It is counted in `refused`, with its reason, and never silently dropped. A
matrix that quietly omitted what it could not read would understate the
corpus and overstate its own coverage -- and the count of things a survey
could not do is the part a reader most needs.

Several files carry a `version` of 1346456900 (`0x503A4344`). That is not a
version; it is counted under `unknown_version` rather than being compared
against 1006. **`DMap.version` is a STRING** -- comparing it to an int
silently yields False and would ship as "the frontier does not exist."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _sub in ("core", "tools"):
    _p = str(_HERE.parent / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmap                                                  # noqa: E402

#: Versions this survey recognises. Anything else is `unknown_version` and is
#: never compared against the boundary.
KNOWN_VERSIONS = ("1003", "1004", "1005", "1006")


def version_of(d) -> str:
    """The map's version as a STRING, or "" when it is not a known one.

    `DMap.version` is a string and the bogus `1346456900` appears across every
    install. Returning "" for it keeps an unrecognised value out of every
    comparison instead of letting it fall on one side of the boundary.
    """
    v = str(getattr(d, "version", "") or "").strip()
    return v if v in KNOWN_VERSIONS else ""


def capability(d) -> dict:
    """Can THIS map carry a cover record, and does it carry any?

    `capable` is about the FORMAT and `populated` is about the CONTENT, and
    an editor needs both: a capable-but-empty map is where a cover can be
    added, which is precisely the interesting case and the one a single
    "has covers" boolean hides.
    """
    v = version_of(d)
    capable = bool(v) and int(v) >= dmap.LATE_LAYERS_MIN_VERSION
    late = list(getattr(d, "late_layers", None) or [])
    return {
        "version": v or str(getattr(d, "version", "") or ""),
        "known_version": bool(v),
        "capable": capable,
        "populated": bool(late),
        "records": len(late),
        "declared": int(getattr(d, "late_layer_count", 0) or 0),
        "error": getattr(d, "late_layer_error", None) or "",
        "unconsumed": int(getattr(d, "bytes_unconsumed", 0) or 0),
    }


def survey_install(root, *, limit: int = 0, progress=None) -> dict:
    """One install's matrix row. Archive-aware; never globs."""
    root = str(root)
    out = {
        "root": root, "dir": os.path.basename(os.path.normpath(root)),
        "maps": 0, "parsed": 0, "refused": 0, "refusals": {},
        "versions": {}, "unknown_version": 0,
        "capable": 0, "populated": 0, "records": 0,
        "capable_empty": 0, "unconsumed_nonzero": 0,
    }
    # A PATH THAT IS NOT AN INSTALL MUST NOT RENDER AS AN INSTALL WITH NO
    # MAPS. `map_names` answers [] for both, and the two are opposite facts:
    # one is "this build ships no v1006 content", which belongs in the matrix,
    # and the other is "I was pointed at the wrong directory", which does not.
    # A row of zeros reads as the first. Caught by its own arm.
    if not os.path.isdir(root):
        out["refusals"]["not a directory"] = 1
        return out
    try:
        names = dmap.map_names(root)
    except Exception as e:
        out["refusals"]["map_names: %s" % e.__class__.__name__] = 1
        return out
    if not names:
        out["refusals"]["no map/map content -- not an install?"] = 1
        return out
    out["maps"] = len(names)
    if limit:
        names = names[:limit]
    for i, nm in enumerate(names):
        if progress and i % 50 == 0:
            progress(out["dir"], i, len(names))
        try:
            d, _how = dmap.parse_map(root, nm, want_cells=False)
        except Exception as e:
            out["refused"] += 1
            k = e.__class__.__name__
            out["refusals"][k] = out["refusals"].get(k, 0) + 1
            continue
        if d is None:
            out["refused"] += 1
            out["refusals"]["none"] = out["refusals"].get("none", 0) + 1
            continue
        out["parsed"] += 1
        cap = capability(d)
        key = cap["version"] if cap["known_version"] else "unknown"
        out["versions"][key] = out["versions"].get(key, 0) + 1
        if not cap["known_version"]:
            out["unknown_version"] += 1
        if cap["capable"]:
            out["capable"] += 1
            if cap["populated"]:
                out["populated"] += 1
                out["records"] += cap["records"]
            else:
                # THE EMPTY CANVAS. A v1006 map with no records is where a
                # cover CAN be added; a v1004 map with none cannot hold one.
                # Same zero, opposite meanings.
                out["capable_empty"] += 1
        if cap["unconsumed"]:
            out["unconsumed_nonzero"] += 1
    return out


def survey(base, *, limit: int = 0, progress=None) -> list:
    """Every install directly under `base`, one row each."""
    base = Path(base)
    rows = []
    for name in sorted(os.listdir(base)):
        p = base / name
        if p.is_dir():
            rows.append(survey_install(p, limit=limit, progress=progress))
    return rows


PREAMBLE = """# Which installs can carry map cover sprites

Generated by `tools/mapcover.py`. **Re-run it rather than trusting this file**
— a number is a fact about the install it came from, at the time it was read.

## What the columns mean

* **capable (v1006)** — THIS EDITOR can write a cover to the map. It is not
  "the map can hold a cover at all": pre-1006 maps keep their covers in the
  `.DMap`'s FIRST layer table (17,397 on the live install, 2026-09-24), which
  nothing here writes yet. On v1006 maps, cover sprites and ambient effects
  live in the `.DMap`'s second counted
  record list, which only v1006 writes (`core/dmap.LATE_LAYERS_MIN_VERSION`,
  where the evidence lives: 25,308 tag-4 covers and 11,597 tag-19 effects
  across 176 v1006 maps, while the same walk closes 19 of 19 v1005 maps and
  all 1,831 pre-1005 maps *without* the list and desynchronises immediately
  if it is assumed).
* **with covers** — capable *and* carrying at least one record today.
* **empty & capable** — capable and carrying none. **This is the interesting
  column**: it is where a cover can be added. A v1006 map with no records is
  an empty canvas for this editor; a v1004 map with no late records cannot
  take one from this editor at all (its covers, if any, sit in the first
  layer table). Same zero, opposite meanings, so they are never summed.
* **refused** — maps this survey could not parse. Counted, never dropped: the
  count of what a survey could not do is the part a reader most needs.
* **unknown ver** — a version outside `1003–1006`, mostly `1346456900`
  (`0x503A4344`), which is not a version. Never compared against the boundary.
  `DMap.version` is a STRING; comparing it to an int silently yields False.

## Two things this table says that a boundary would not

**1. Adoption is gradual, not a switch.** Capability climbs monotonically with
the client — 2 maps at `6609`, 57 at `7065`, 162 at `7878`, 169 at `7952` —
because TQ re-authored content into v1006 progressively over ~1,300 patch
versions.

**2. So "can my client do covers" is the wrong question: it is a property of
the MAP's version, and what it decides is whether THIS EDITOR can write to
the map.** Even on the newest build here, 309 of 478 maps are outside the
write path. Every surface must answer it per map — `mapcover.capability(d)`
does — and must say it is the editor's limit, not the map's.

**Corrected on first full measurement.** A six-install sample previously put
the first capable install at `7065` and reported that as independent agreement
with the RE finding that the `MapObjIndex%d -> DMap` binding is 7065+. The
real first is **`6609`**; the sample had skipped every install between the
last zero and the one it called first. Two separate facts at two separate
versions, and neither is evidence for the other.

**Read archive-aware, always.** `dmap.map_names` / `parse_map`, never a loose
glob: globbing `map/map/*.DMap` on `7878` reports 0 maps and it has 470,
because that build ships them in containers.

"""


def to_markdown(rows: list) -> str:
    """The matrix a human reads, with the honesty carries in the table."""
    hdr = (PREAMBLE
           + "## The matrix\n\n"
           + "| install | maps | parsed | refused | v1006 (capable) | with covers "
           "| empty & capable | records | unknown ver |\n"
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    body = []
    for r in sorted(rows, key=lambda x: (-x["capable"], x["dir"])):
        if not r["maps"] and not r["refusals"]:
            continue
        body.append("| `%s` | %d | %d | %d | %d | %d | %d | %d | %d |"
                    % (r["dir"], r["maps"], r["parsed"], r["refused"],
                       r["capable"], r["populated"], r["capable_empty"],
                       r["records"], r["unknown_version"]))
    tot = {k: sum(r[k] for r in rows)
           for k in ("maps", "parsed", "refused", "capable", "populated",
                     "capable_empty", "records", "unknown_version")}
    body.append("| **total** | %d | %d | %d | %d | %d | %d | %d | %d |"
                % (tot["maps"], tot["parsed"], tot["refused"], tot["capable"],
                   tot["populated"], tot["capable_empty"], tot["records"],
                   tot["unknown_version"]))
    return hdr + "\n".join(body) + "\n"


def _report(rows: list) -> None:
    print("%-30s %6s %6s %5s %8s %8s %7s %9s"
          % ("install", "maps", "parsed", "ref", "capable", "covers",
             "empty", "records"))
    for r in sorted(rows, key=lambda x: (-x["capable"], x["dir"])):
        if not r["maps"] and not r["refusals"]:
            continue
        print("%-30s %6d %6d %5d %8d %8d %7d %9d"
              % (r["dir"][:30], r["maps"], r["parsed"], r["refused"],
                 r["capable"], r["populated"], r["capable_empty"],
                 r["records"]))
    cap = sum(r["capable"] for r in rows)
    tot = sum(r["parsed"] for r in rows)
    ref = sum(r["refused"] for r in rows)
    print("\n%d of %d parsed maps can carry a cover record (%.1f%%); "
          "%d refused and are counted, not dropped."
          % (cap, tot, (100.0 * cap / tot) if tot else 0.0, ref))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Which installs can carry map cover sprites.")
    # RESOLVED, NOT LITERAL. `test_sanitization` refuses a drive-rooted path
    # in a published file, and it is right twice over: the folder is one
    # machine's, and `coroot.clients_dir()` already knows it from the user's
    # own declarations. I hardcoded this after fixing the identical defect in
    # a sibling test earlier the same night -- the rule was in a docstring I
    # wrote and not in the code I wrote next.
    ap.add_argument("--base", default=None,
                    help="directory of installs to survey "
                         "(default: coroot.clients_dir())")
    ap.add_argument("--root", action="append", default=[],
                    help="survey this install too (repeatable)")
    ap.add_argument("--limit", type=int, default=0,
                    help="parse at most N maps per install (0 = all)")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--markdown", metavar="PATH")
    ap.add_argument("--from-json", metavar="PATH",
                    help="render a saved survey instead of re-walking the "
                         "corpus (a full sweep is ~14 minutes)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    # RENDER FROM SAVED DATA RATHER THAN RE-MEASURING. A full sweep is 15,931
    # maps and ~14 minutes, and re-running it to reformat the same numbers is
    # pure waste -- I did exactly that once and stopped it. It is also the
    # safer default for a shared box: the survey reads 45 installs, and a
    # report is not a reason to touch them again.
    if args.from_json:
        rows = json.loads(Path(args.from_json).read_text("utf-8"))
        _report(rows)
        if args.markdown:
            Path(args.markdown).write_text(to_markdown(rows), "utf-8")
            print("wrote %s" % args.markdown)
        return 0

    def progress(name, i, n):
        if not args.quiet:
            print("  ...%-28s %d/%d" % (name[:28], i, n), file=sys.stderr)

    base = args.base
    if base is None:
        import coroot                                        # noqa: PLC0415
        base = coroot.clients_dir()
    rows = []
    if base and os.path.isdir(base):
        rows += survey(base, limit=args.limit, progress=progress)
    for r in args.root:
        rows.append(survey_install(r, limit=args.limit, progress=progress))
    if not rows:
        print("no installs found under %s" % base, file=sys.stderr)
        return 2
    _report(rows)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), "utf-8")
        print("\nwrote %s" % args.json)
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(rows), "utf-8")
        print("wrote %s" % args.markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
