#!/usr/bin/env python3
r"""
motionsource.py -- which of a loadout's motions are THIS install's, and which
are only in other patch levels.

    py -3 tools/motionsource.py --body 003188490 --left 500219
    py -3 tools/motionsource.py --body 003188490 --left 500219 --json out/ms.json

THE QUESTION THIS ANSWERS
--------------------------
`ini/3dmotion.ini` declares more motions than a client ships. For the owner's
body-and-bow composition it names 198 actions resolving to 143 files, of which
**60 are present and 83 are declared and not found**. The owner asked whether
those 83 exist elsewhere and whether they can be seen on a body from THIS
install.

They do exist: 82 of the 83 are on other installs, and the distribution is the
finding -- they shipped roughly 5165 through 7250 and were **removed from 7320
onward**. This install follows the newer clients in not shipping them while
still carrying a table that declares them.

WHAT "COMPATIBLE" MEANS HERE, AND WHAT IT DOES NOT
---------------------------------------------------
A motion's MOTI chunks bind to the mesh's PHY chunks **by ordinal**, and
`C3Mesh::SetMotion` (graphic.dll 0x277C0) REJECTS a set with fewer chunks than
the mesh has PHY -- one short chunk costs the whole animation, not one limb.
So `aligned` is a real gate and it is checkable without the game:

    MEASURED 2026-09-22, body 003188490 (mesh 003188495.c3, 4 PHY / 4 MOTI):
      all 82 recoverable motions from 6609 carry 4 MOTI  -> aligned
      cross-install clips build and report 341-823 frames

**THAT IS NOT THE SAME AS "THEY LOOK RIGHT."** Chunk alignment says the engine
will accept the set, not that the poses suit this skeleton, and the bone
counts inside a MOTI chunk are not compared here. Whether a 6609-era motion
looks correct on a modern body is a question for eyes, which is precisely why
this ships a viewer rather than a verdict.

Nothing here writes. The body always comes from the configured install; a
foreign motion is bound to it in memory and drawn.
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

import anim                                                  # noqa: E402
import assetdict                                             # noqa: E402
import coassets                                              # noqa: E402
import coroot                                                # noqa: E402

# THE ROOT CACHE AND THE INSTALL LIST LIVE IN `assetdict`, not here.
#
# This module carried a byte-identical copy of `_ROOTS`, `_asset_root` and
# `installs`. Two copies means the bare-vs-profiled decision -- which is worth
# 4.6 s per install and changes which files a `verify` can confirm -- had to
# be made twice, and the second copy is the one nobody remembers.
#
# `installs` stays a NAME here because `tests/test_motion_source.py` imports
# it from this module; it is the same function object, not a re-spelling.
_asset_root = assetdict.root_for
installs = assetdict.installs


def _cache_path(root, body, right, left, names) -> Path:
    """Where the answer for one loadout is kept between runs.

    Keyed on the install, the loadout AND the list of install names, so
    adding or removing a client invalidates it. Derived output, so it lives
    under `out/` and never in the game tree.
    """
    import hashlib
    sig = "|".join([str(root), body, right, left, ",".join(names)])
    h = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    # Anchored to the REPO, not the working directory: a viewer started
    # from elsewhere would otherwise write its cache somewhere else and
    # silently re-pay the 190 s.
    return _HERE.parent / "out" / "motionsources" / ("%s.json" % h)


def sources_for(root, body: str, *, right: str = "", left: str = "",
                base=None, progress=None, refresh: bool = False) -> dict:
    """Every action this loadout resolves, with WHERE its motion can come from.

    One row per distinct motion FILE, because actions alias heavily -- 198
    actions land on 143 files and listing per action would show the same file
    six times and read as six animations.

    **This used to cost ~190 s cold and now costs ~3 s**, because the
    expensive part was never the asking. MEASURED 2026-09-22: opening 45
    AssetRoots was 149.93 s while every lookup together was 0.2 s. The old
    per-loadout cache was keyed on the wrong thing -- what an install
    CONTAINS does not depend on the loadout, so each new body+weapon
    combination re-derived unchanged facts and the cache could never
    amortise. `tools/assetdict.py` keys that on the install instead, so one
    build serves every loadout.

    The per-loadout cache below is kept, but it is no longer load-bearing:
    a miss now costs seconds rather than minutes. It is keyed on the loadout
    and the INSTALL LIST, so adding or removing a client invalidates it; it
    does NOT notice an install whose contents changed underneath it, and
    `refresh=True` is the answer to that. Stated rather than hidden, because
    a cached wrong answer about which client has which motion is exactly the
    kind that gets quoted.

    One inherited limit travels with the speed: the dictionary matches on
    `tq_hash`, so an install that does NOT list a file is exact, while a
    listed one carries a ~0.0035% chance of a 32-bit collision. That is the
    only identity a WDF index keeps. `assetdict.which(..., verify=True)`
    settles a specific claim at ~0.01-0.3 s per install (MEASURED
    2026-09-25; it was ~3.3 s while it opened PROFILED roots).
    """
    db = anim.AnimDB(str(root))
    lm = anim.loadout_motions(db, body, right=right, left=left)
    base = str(base or coroot.clients_dir())
    others = installs(base)

    cache = _cache_path(root, body, right, left, others)
    if not refresh and cache.is_file():
        try:
            return json.loads(cache.read_text("utf-8"))
        except Exception:
            pass                      # a corrupt cache is re-measured, not fatal

    # ONE dictionary lookup for every missing file, instead of opening each
    # install and asking it per path. MEASURED 2026-09-22 on the owner's
    # body+bow: opening 45 AssetRoots was 149.93 s of the ~190 s, while the
    # lookups themselves were 0.2 s -- so the cost was never the asking, it
    # was the re-opening. Warm, the whole answer is now 3.0 s, and it
    # reproduces the slow path's result exactly (82 of 83 found, 1 nowhere).
    missing = [m.path for m in lm.motions if not m.present]
    if progress and missing:
        progress("dictionary lookup", 0, len(missing))
    # `refresh` is deliberately NOT forwarded. It means "re-measure THIS
    # loadout", and forwarding it rebuilt all 45 install dictionaries --
    # which made a refresh cost 158 s and hid the whole improvement. The two
    # invalidations are different questions: a loadout is re-derived because
    # the caller wants it re-derived, an install dictionary because that
    # install CHANGED, which `_signature` already detects. Conflating them is
    # the same wrong-key error this tool was written to fix, one level up.
    try:
        found = assetdict.which(missing, base, verify=False)
    except Exception:
        found = {}                    # fall through to the per-install walk

    rows = []
    for i, m in enumerate(lm.motions):
        if progress and i % 20 == 0:
            progress(m.path, i, len(lm.motions))
        row = {
            "path": m.path,
            "actions": list(m.actions),
            "label": m.label,
            "group": m.group,
            "route": m.route,
            "local": bool(m.present),
            # Only asked for the ones this install lacks: a motion that is
            # here needs no source, and walking 45 archives per present file
            # would cost minutes to answer a question nobody asked.
            "sources": [],
        }
        if not m.present:
            if m.path in found:
                row["sources"] = list(found[m.path])
            else:
                # Only reached if the dictionary was unavailable. Kept so a
                # failure there is slow rather than silently wrong -- an
                # empty `sources` reads as "no install has it", which is a
                # real answer and must not be manufactured by a broken cache.
                for d in others:
                    ar = _asset_root(os.path.join(base, d))
                    if ar is not None and ar.exists(m.path):
                        row["sources"].append(d)
        rows.append(row)

    local = [r for r in rows if r["local"]]
    foreign = [r for r in rows if not r["local"]]
    nowhere = [r for r in foreign if not r["sources"]]
    out = {
        "root": str(root), "body": body, "right": right, "left": left,
        "shape": lm.shape, "weaponset": lm.weaponset,
        "ownSet": lm.own_set, "actions": lm.action_count,
        "rows": rows,
        "localCount": len(local),
        "foreignCount": len(foreign),
        # Stated even when zero: a count a reader only sees when it is
        # non-zero cannot be told from one that was not reported.
        "nowhereCount": len(nowhere),
        "limits": list(lm.limits),
        "note": ("`sources` lists installs that carry a file THIS install "
                 "declares and lacks. Chunk alignment is checkable; whether "
                 "the pose suits this skeleton is not, and is why this is a "
                 "viewer rather than a verdict."),
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out), "utf-8")
    except Exception:                                        # pragma: no cover
        pass                          # an unwritable cache must not fail a read
    return out


def _report(d: dict, limit: int = 30) -> None:
    print("body %s  shape %s  weaponset %s  (own set: %s)"
          % (d["body"], d["shape"], d["weaponset"], d["ownSet"]))
    print("%d action(s) -> %d motion file(s): %d here, %d only elsewhere, "
          "%d nowhere on this box"
          % (d["actions"], len(d["rows"]), d["localCount"], d["foreignCount"],
             d["nowhereCount"]))
    print()
    print("%-26s %-8s %-30s %s" % ("motion", "where", "label", "sources"))
    for r in d["rows"][:limit]:
        where = "HERE" if r["local"] else ("elsewhere" if r["sources"]
                                           else "NOWHERE")
        src = "" if r["local"] else ", ".join(r["sources"][:4])
        if not r["local"] and len(r["sources"]) > 4:
            src += " +%d" % (len(r["sources"]) - 4)
        print("  %-24s %-8s %-30s %s"
              % (r["path"].rsplit("/", 1)[-1], where, r["label"][:30], src))
    if len(d["rows"]) > limit:
        print("  ... and %d more" % (len(d["rows"]) - limit))
    for x in d["limits"]:
        print("\n  limit: %s" % x)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Which of a loadout's motions are this install's.")
    ap.add_argument("--root", default=None)
    ap.add_argument("--body", required=True)
    ap.add_argument("--right", default="")
    ap.add_argument("--left", default="")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = args.root
    if not root:
        found = coroot.config_root()
        root = found[0] if found else None
    if not root or not Path(root).is_dir():
        print("no install: pass --root", file=sys.stderr)
        return 2

    def progress(p, i, n):
        if not args.quiet:
            print("  ...%-30s %d/%d" % (p[-30:], i, n), file=sys.stderr)

    d = sources_for(root, args.body, right=args.right, left=args.left,
                    progress=progress)
    _report(d, args.limit)
    if args.json:
        Path(args.json).write_text(json.dumps(d, indent=2), "utf-8")
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
