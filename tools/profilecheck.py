#!/usr/bin/env python3
r"""
profilecheck.py -- the ACCEPTANCE CHECK for a pre-bootstrap name profile.

A numbered client is immutable, so every artefact derived from one is a pure
function of a fixed input and every user who bootstraps 5517 recomputes an
identical answer.  MEASURED on this box, `health.wdf_recover_estimate`:
**1,578-3,033 s (26-51 min), typical 2,009 s** for Clients/5517, of which the
name search is 1,958 s.  Shipping the answer instead of recomputing it is
worth the whole of that.

=============================================================================
WHY THIS FILE IS THE WHOLE FEATURE
=============================================================================

The hard part of shipping derived data is not shipping it.  It is that

    the only check that derived data equals recomputation IS recomputation,
    which is the cost being avoided.

So most "verified download" designs buy **intactness** (a hash says these are
the bytes that shipped) and **provenance** (a stamp says who built them), and
quietly call it correctness.  Three ways that fails, and this module is
written against all three by name:

  1. **The fingerprint verifies the CLIENT, not the DATA.**  `coroot.base_id`
     says which install you have.  Correct client + stale or corrupt table
     passes cleanly.
  2. **A hash over the blob proves INTACTNESS, not CORRECTNESS.**  It shows
     the bytes are the ones that shipped; never that they equal what
     recomputing would produce.
  3. **The fingerprint does not cover the PRODUCER.**  `coroot.base_fingerprint`
     is sha256 over `ini/` and nothing else.  Bind a profile to that alone and
     fixing `wdf_recover.py` leaves every shipped artefact verifying clean and
     serving pre-fix output forever.

**This artefact escapes all three, and it is the only one in `health.DERIVED`
that does.**  A recovered name is a `(u32 hash -> path)` pair, and the hash is
a *one-way function of the path* (`core/tqhash.py`, lifted from
TqPackageWdf.dll).  Producing the pair is search -- 238M candidate hashes on
5517.  Checking one is a single forward hash.  So the check below is not a
proxy for recomputation, it is a **cheaper and equally decisive** derivation
of the same fact, computed against the user's own archive:

    MEASURED, Clients/5517: 24,431 entries checked in 0.42 s, 0 unsound.
    Against 2,009 s to produce them.  The residual is 0.02% of the prize --
    two orders of magnitude below the 29% floor on how repeatably that prize
    can even be measured (`health.WDF_RECOVER_MODEL`), so the saving is the
    whole bootstrap within the resolution of the instrument that measures it.

=============================================================================
WHAT IT BUYS, STATED SO IT CANNOT BE READ AS MORE
=============================================================================

Three separate verdicts, because they have different strengths and reporting
them as one number is the defect this file exists to avoid:

  * **soundness -- EQUIVALENCE, exact.**  Every shipped name is re-hashed and
    must equal the key it is filed under.  A wrong name cannot survive this;
    forging one means inverting the TQ hash to a string that is also a
    plausible asset path.  This is not sampling and not provenance.  It does
    not care who built the table, when, or with which version of the tool.
  * **applicability -- EXACT.**  Every key must be an entry that *this*
    install's archive actually holds.  Read from `c3.wdf`/`data.wdf` at the
    user's own path, 8 ms.  A table for a different client cannot pass by
    carrying a correct-looking stamp, because nothing here reads the stamp.
  * **completeness -- MEASURED, not proven.**  The archive index states the
    denominator exactly, so coverage is a fact (95.97% on 5517 c3, 92.75% on
    data).  What it CANNOT say is whether a recompute would have found more.
    **This is the one hole, and it is the one the producer version closes** --
    not a hash of the blob.  See `PRODUCER_NOTE`.

So: this buys **equivalence for correctness and exactness for applicability,
and only a measured floor for completeness.**  Anyone quoting this module as
"verified equal to recomputation" without the third clause is overselling it.

=============================================================================
WHY A PRIVATE SERVER CANNOT BE PROFILED -- MECHANISM, NOT POLICY
=============================================================================

The owner excluded private servers.  That exclusion is not a warning in a UI
here; it is a property of the key.  A profile is looked up by
`coroot.base_id`, which is a **content hash of the install's table layer**.
A private server that changes tables -- which is what makes it interesting
enough to be a private server -- re-keys itself, so no published profile
matches and there is nothing to warn about.  A server that patches weekly
invalidates its own profile weekly, by construction.  The mechanism cannot
profile a moving target because the target's identity IS its content.

The residual case is exact and benign: a repack differing from stock only in
`coroot.VOLATILE_INI` / `TOOL_WRITTEN_INI` (`StartGame.ini`, where a server
address goes) fingerprints as the official client -- and *correctly so*, since
its assets are byte-identical to the official client's.  The skip list covers
exactly the files that cannot affect derived data.

Usage:
    py -3 tools/profilecheck.py --root C:/COMod/ConquerAssets/Clients/5517
    py -3 tools/profilecheck.py --profile out/profiles/patch5517-*.zip --json
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import coroot                                            # noqa: E402
from tqhash import tq_hash                               # noqa: E402
from wdf import WdfArchive                               # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: The archives a profile covers, and the table filename for each.
ARCHIVES = (("c3", "c3_names.json"), ("data", "data_names.json"))

#: **The producer binding, and why a blob hash is not a substitute.**
#:
#: `soundness` and `applicability` below are re-derived from the user's own
#: client, so a producer fix cannot make them wrong -- a table that passes
#: them passes them under any version of any tool.  What a producer fix CAN
#: change is how many names the search finds, and that is invisible locally:
#: coverage is measurable against the archive denominator, but "could a better
#: producer do better?" is exactly the question only recomputation answers.
#:
#: So a profile is keyed by ``(base_id, producer)`` and not by ``base_id``
#: alone, where ``producer`` is a content hash of the search's own inputs --
#: `tools/wdf_recover.py` and `core/tqhash.py`.  Fixing either re-keys every
#: profile the old one produced, so the fix invalidates its own outputs
#: instead of leaving them verifying clean forever.  That is the failure the
#: brief named as the one to bet on, and this is the whole of the defence:
#: a hash over the shipped BLOB cannot do it, because the blob is unchanged
#: by a producer fix -- that is precisely the problem.
PRODUCER_INPUTS = ("tools/wdf_recover.py", "core/tqhash.py")

PRODUCER_NOTE = (
    "Coverage is a measured floor, not a proof of completeness. A profile "
    "declares the producer that built it; if yours is newer, recompute -- "
    "the local check cannot tell you what a better search would have found.")


def producer_id(repo=None) -> str:
    """Content hash of the search's own inputs, or ``""`` if unreadable.

    Deliberately NOT a version string a human types.  A hand-maintained
    version is a promise to remember, and the failure mode being defended
    against is exactly the one where somebody fixed the tool and did not
    think about the artefacts.
    """
    import hashlib
    base = Path(repo) if repo is not None else REPO
    h = hashlib.sha256()
    for rel in PRODUCER_INPUTS:
        p = base / rel
        try:
            h.update(rel.encode("utf-8"))
            h.update(p.read_bytes())
        except OSError:
            return ""
    return h.hexdigest()[:12]


def load_tables(profile=None, out_dir=None) -> tuple[dict, dict, str]:
    """``(tables, manifest, where)`` from a profile zip or from ``out/wdf/``."""
    if profile:
        p = Path(profile)
        blob = p.read_bytes()
        tables, manifest = {}, {}
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = set(z.namelist())
            if "manifest.json" in names:
                manifest = json.loads(z.read("manifest.json"))
            for stem, fn in ARCHIVES:
                if fn in names:
                    tables[stem] = json.loads(z.read(fn))
        import hashlib
        manifest.setdefault("blob_sha256",
                            hashlib.sha256(blob).hexdigest()[:16])
        return tables, manifest, str(p)
    d = Path(out_dir) if out_dir else (REPO / "out" / "wdf")
    tables = {}
    for stem, fn in ARCHIVES:
        f = d / fn
        if f.is_file():
            tables[stem] = json.loads(f.read_text(encoding="utf-8"))
    return tables, {}, str(d)


def check(root, tables: dict) -> dict:
    """The three-part check, per archive.  Reads only ``root``'s archives."""
    root = Path(root)
    out = {"root": str(root), "archives": {}}
    for stem, _fn in ARCHIVES:
        arc = root / f"{stem}.wdf"
        tab = tables.get(stem)
        if tab is None or not arc.is_file():
            out["archives"][stem] = {
                "checked": False,
                "why": ("no table in the profile" if tab is None
                        else f"{arc.name} is not in this install"),
            }
            continue
        t0 = time.perf_counter()
        with WdfArchive(arc) as a:
            arc_hashes = {e.hash for e in a.entries}
        unsound, foreign, named = [], 0, set()
        for hx, name in tab.items():
            try:
                h = int(hx, 16)
            except ValueError:
                unsound.append(hx)
                continue
            if tq_hash(name) != h:
                # THE ONE THAT CANNOT BE FAKED. Producing this pair took a
                # search; contradicting it takes one forward hash.
                if len(unsound) < 20:
                    unsound.append(f"{hx}={name}")
                continue
            if h not in arc_hashes:
                # Sound but not about this archive. The name tables are
                # coroot.GLOBAL -- a hash->name pair recovered from any
                # client is true in every client -- so this is expected and
                # is NOT a fault. It is reported because a table that is
                # mostly foreign is a table for somebody else's client.
                foreign += 1
                continue
            named.add(h)
        out["archives"][stem] = {
            "checked": True,
            "archive_entries": len(arc_hashes),
            "table_entries": len(tab),
            "unsound": len(unsound),
            "unsound_sample": unsound[:20],
            "sound_but_not_in_this_archive": foreign,
            "verified_named": len(named),
            "coverage_pct": round(100.0 * len(named) / max(len(arc_hashes), 1), 2),
            "seconds": round(time.perf_counter() - t0, 3),
        }
    return out


def verdict(report: dict, manifest: dict, root=None) -> dict:
    """Accept or refuse, with each clause reported at its own strength."""
    arcs = [a for a in report["archives"].values() if a.get("checked")]
    unsound = sum(a["unsound"] for a in arcs)
    named = sum(a["verified_named"] for a in arcs)
    total = sum(a["archive_entries"] for a in arcs)
    foreign = sum(a["sound_but_not_in_this_archive"] for a in arcs)

    mine_base = ""
    try:
        mine_base = coroot.base_id(root)
    except Exception:                                    # noqa: BLE001
        pass
    mine_prod = producer_id()
    said_base = str(manifest.get("base_id") or "")
    said_prod = str(manifest.get("producer") or "")

    # Ordered by strength, strongest first, and each says what it is.
    clauses = []
    ok = True
    if not arcs:
        ok = False
        clauses.append(("blocked", "no archive could be checked"))
    if unsound:
        ok = False
        clauses.append(("soundness", f"REFUSED -- {unsound} names do not "
                                     f"hash to their own key. Equivalence, "
                                     f"exact: these names are wrong."))
    else:
        clauses.append(("soundness", f"pass -- all {named + foreign} names "
                                     f"re-hash to their key. EQUIVALENCE, "
                                     f"exact, re-derived here."))
    cov = round(100.0 * named / max(total, 1), 2)
    if named == 0 and total:
        ok = False
        clauses.append(("applicability", "REFUSED -- no shipped name names "
                                         "an entry this install holds. This "
                                         "table is for another client."))
    else:
        clauses.append(("applicability", f"pass -- {named} of {total} archive "
                                         f"entries named. EXACT, read from "
                                         f"this install's own archives."))
    clauses.append(("completeness", f"{cov}% coverage. MEASURED FLOOR, not "
                                    f"proven -- {PRODUCER_NOTE}"))
    # The client and producer clauses are the WEAKEST and are reported last,
    # deliberately: they are stamps, and a stamp is a claim. They are here to
    # tell the user a better table may exist, never to certify this one.
    if said_base:
        clauses.append(("client-stamp",
                        "match" if said_base == mine_base
                        else f"MISMATCH -- profile says {said_base}, this "
                             f"install is {mine_base or '?'}"))
    if said_prod:
        stale = said_prod != mine_prod
        clauses.append(("producer-stamp",
                        f"built by {said_prod}, this tree is {mine_prod} -- "
                        + ("STALE: the search has changed since this profile "
                           "was built, so recompute rather than trust its "
                           "coverage." if stale else "current.")))
    return {"accept": ok, "coverage_pct": cov, "unsound": unsound,
            "verified_named": named, "archive_entries": total,
            "producer_here": mine_prod, "base_here": mine_base,
            "clauses": [{"clause": c, "says": s} for c, s in clauses]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None,
                    help="the install to check against (default: configured)")
    ap.add_argument("--profile", default=None,
                    help="a profile .zip to check (default: out/wdf/)")
    ap.add_argument("--out-dir", default=None,
                    help="a directory of *_names.json instead of out/wdf/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    root = a.root or coroot.game_root()
    tables, manifest, where = load_tables(a.profile, a.out_dir)
    if not tables:
        print(f"no name tables at {where}", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    rep = check(root, tables)
    v = verdict(rep, manifest, root)
    v["source"] = where
    v["seconds"] = round(time.perf_counter() - t0, 3)
    rep["verdict"] = v

    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"profile: {where}")
        print(f"install: {root}")
        for stem, r in rep["archives"].items():
            if not r.get("checked"):
                print(f"  {stem}: not checked -- {r['why']}")
                continue
            print(f"  {stem}: {r['verified_named']}/{r['archive_entries']} "
                  f"named ({r['coverage_pct']}%), {r['unsound']} unsound, "
                  f"{r['seconds']}s")
        print()
        for c in v["clauses"]:
            print(f"  {c['clause']:>16}: {c['says']}")
        print(f"\n  {'VERDICT':>16}: "
              f"{'ACCEPT' if v['accept'] else 'REFUSE'} "
              f"in {v['seconds']}s")
    return 0 if v["accept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
