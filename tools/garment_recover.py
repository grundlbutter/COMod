#!/usr/bin/env python3
r"""
garment_recover.py -- trustworthy name recovery for a community client's
garments*.wdf archives, by numeric-ID *corroboration*.

Those archives hold custom fashion content whose paths appear in no wordlist
(a DatPkg client's .tpi index resolves 0 of them directly), so the only lever
is to brute-force numeric ids in the conventional 3D-asset directories
(`c3/mesh/<id>.c3`, `c3/texture/<id>.dds`, ...).  But a 32-bit hash against
~14k targets yields ~n/2**32 * 14000 spurious hits, and empirically the wide
sweep's hit count tracks that false-positive expectation almost 1:1 -- i.e. a
raw enumeration of garment ids is mostly birthday collisions and cannot be
trusted name-by-name (see docs/assets.md ss.6).

The fix is corroboration.  A Conquer 3D item ships its geometry and its skin
under the SAME id: `c3/mesh/<id>.c3` pairs with `c3/texture/<id>.dds` (and
`c3/weapon/<id>.c3` with `c3/weapon/<id>.dds`, etc.).  For two INDEPENDENT
hashes to both land on real entries at the same id by chance is
~n_hits_a * n_hits_b / 10**width -- negligible at width >= 5.  So an id that
hits in a paired slot is essentially certain, even though either slot alone is
not.  We keep:

  * PAIRED ids  -- hit in >=2 sibling slots at the same id (mesh+texture, or a
    dir's own .c3+.dds).  Certain.
  * RUN ids     -- part of a contiguous run (stride 1 or 10) of >=3 magic-clean
    hits in one slot.  Real id series come in runs; collisions do not.

Every kept name is additionally magic-checked (payload's first bytes must match
the extension).  Isolated single-slot magic-only hits are reported but NOT
emitted as names -- they are indistinguishable from collisions.

Output matches wdf_recover's layout (`out/garments/<stem>_names.json`), so
`apply_recovered_names.py` consumes it unchanged.

Usage:
    py -3 tools/garment_recover.py \
        --root "<path>/Zephyr Conquer 1057" \
        --out  out/garments
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from tqhash import normalise_path                              # noqa: E402
from wdf import WdfArchive, detect_magic                       # noqa: E402
from wdf_recover import hash_state, hash_from                  # noqa: E402

GARMENTS = ("garments.wdf", "garments1.wdf", "garments2.wdf",
            "garments3.wdf", "garments4.wdf")

EXT_MAGIC = {".dds": "DDS", ".c3": "MAXF"}

# (directory, extension) slots to enumerate.  Slots sharing a pair_group are
# corroborating: the same id in two of them is a confirmed asset.
SLOTS = [
    ("c3/mesh", ".c3", "body"),
    ("c3/texture", ".dds", "body"),
    ("c3/hair", ".c3", "hair"),
    ("c3/hair", ".dds", "hair"),
    ("c3/weapon", ".c3", "weapon"),
    ("c3/weapon", ".dds", "weapon"),
    ("c3/npc", ".c3", "npc"),
]

WIDTHS = (5, 6)          # 4 is too collision-prone to pair; 7 is slow noise


def enumerate_slot(prefix: str, ext: str, want: dict, width: int):
    """Yield (sid_str, hash) for every candidate <prefix>/<id:width><ext> that
    hits a wanted hash.  The id is kept as its exact zero-padded string so that
    "12345" (width 5) and "012345" (width 6) stay distinct.  Cost is ~1 mixing
    round per candidate (cached prefix).
    """
    state, tail = hash_state((prefix + "/").encode("latin-1", "replace"))
    hits = []
    for i in range(10 ** width):
        sid = f"{i:0{width}d}"
        h = hash_from(state, tail + (sid + ext).encode())
        if h in want:
            hits.append((sid, h))
    return hits


def contiguous_runs(sids: list[str], min_len: int = 3) -> set[str]:
    """Sids that sit in a run of >=min_len at stride 1 or 10, among same-width
    ids (compared as integers within each width bucket)."""
    keep: set[str] = set()
    by_width: dict[int, set[int]] = defaultdict(set)
    for sid in sids:
        by_width[len(sid)].add(int(sid))
    for width, ints in by_width.items():
        for stride in (1, 10):
            for i in ints:
                run = [i]
                j = i + stride
                while j in ints:
                    run.append(j); j += stride
                j = i - stride
                while j in ints:
                    run.append(j); j -= stride
                if len(run) >= min_len:
                    keep.update(f"{k:0{width}d}" for k in run)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--archives", nargs="+", default=list(GARMENTS))
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "out" / "garments")
    ap.add_argument("--widths", type=int, nargs="+", default=list(WIDTHS))
    a = ap.parse_args()

    t0 = time.time()
    arcs = {x: WdfArchive(a.root / x) for x in a.archives if (a.root / x).exists()}
    want: dict[int, list[str]] = defaultdict(list)   # hash -> archives holding it
    magic: dict[int, str] = {}
    for name, ar in arcs.items():
        for e in ar:
            want[e.hash].append(name)
            if e.hash not in magic:
                magic[e.hash] = detect_magic(ar.peek(e, 32))[0]
    print(f"targets: {len(want)} hashes across {list(arcs)}", flush=True)

    # slot -> {sid_str: hash}
    slot_hits: dict[tuple[str, str, str], dict[str, int]] = defaultdict(dict)
    for (d, ext, grp) in SLOTS:
        for w in a.widths:
            for (sid, h) in enumerate_slot(d, ext, want, w):
                slot_hits[(d, ext, grp)][sid] = h
        n = len(slot_hits[(d, ext, grp)])
        print(f"  {d:<12}{ext:<5} hits={n:5}  ({time.time()-t0:.0f}s)", flush=True)

    # ---- corroborate -------------------------------------------------------
    # group sids by pair_group across slots so mesh(body)+texture(body) meet at
    # the same id string.
    ids_by_group: dict[str, dict[str, list[tuple[str, str, int]]]] = defaultdict(
        lambda: defaultdict(list))
    for (d, ext, grp), hits in slot_hits.items():
        for sid, h in hits.items():
            ids_by_group[grp][sid].append((d, ext, h))

    found: dict[int, str] = {}          # hash -> recovered path
    tier: dict[int, str] = {}
    # PAIRED: an id string present in >=2 distinct sibling slots (mesh+texture,
    # or a dir's own .c3+.dds).  Two independent hashes landing on real entries
    # at the same id by chance is negligible, so every magic-clean occurrence is
    # emitted at its own path.
    for grp, idmap in ids_by_group.items():
        for sid, occ in idmap.items():
            if len({(d, ext) for (d, ext, h) in occ}) < 2:
                continue
            for (d, ext, h) in occ:
                if magic.get(h) == EXT_MAGIC.get(ext) and h not in found:
                    found[h] = f"{d}/{sid}{ext}"
                    tier[h] = "paired"

    # RUN: contiguous runs (stride 1 or 10, >=3) within a single magic-clean slot
    for (d, ext, grp), hits in slot_hits.items():
        clean = [sid for sid, h in hits.items()
                 if magic.get(h) == EXT_MAGIC.get(ext)]
        for sid in contiguous_runs(clean):
            h = hits[sid]
            if h not in found:
                found[h] = f"{d}/{sid}{ext}"
                tier[h] = "run"

    n_paired = sum(1 for v in tier.values() if v == "paired")
    n_run = sum(1 for v in tier.values() if v == "run")
    raw_hits = sum(len(h) for h in slot_hits.values())
    print(f"\nconfirmed: {len(found)}  (paired={n_paired} run={n_run})  "
          f"from {raw_hits} raw slot-hits  ({time.time()-t0:.0f}s)", flush=True)

    # ---- emit --------------------------------------------------------------
    a.out.mkdir(parents=True, exist_ok=True)
    for name, ar in arcs.items():
        mine = {e.hash: found[e.hash] for e in ar if e.hash in found}
        (a.out / f"{Path(name).stem}_names.json").write_text(
            json.dumps({f"{h:08x}": v for h, v in sorted(mine.items())}, indent=1),
            "utf-8")
    report = {
        "archives": list(arcs),
        "targets": len(want),
        "confirmed": len(found),
        "paired": n_paired,
        "run": n_run,
        "raw_slot_hits": raw_hits,
        "widths": a.widths,
        "note": ("Only paired (>=2 corroborating slots at one id) and run "
                 "(contiguous >=3) magic-clean ids are emitted as names; "
                 "isolated single-slot hits are treated as birthday collisions "
                 "and dropped."),
        "seconds": round(time.time() - t0, 1),
        "examples": [found[h] for h in list(found)[:20]],
    }
    (a.out / "garment_recovery_summary.json").write_text(
        json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report, indent=2)[:1500])
    for ar in arcs.values():
        ar.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
