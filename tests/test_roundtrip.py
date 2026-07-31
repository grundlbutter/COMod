r"""
test_roundtrip.py -- the byte-exactness gate for tools/c3write.py.

Parse every PHY chunk reachable in this install, re-serialize it, and require
the bytes to be identical.  Anything short of 100% means an unmodelled field.

    py -3 tests/test_roundtrip.py                # whole corpus
    py -3 tests/test_roundtrip.py --limit 300    # first N containers
    py -3 tests/test_roundtrip.py --no-wdf       # skip the archives (fast)

Sources, in the same order validate_phy.py uses them:
  * loose  $ROOT/c3/**.c3 and $ROOT/data/**.c3
  * out/wdf/sample/**
  * c3.wdf and data.wdf, every MAXFILE payload

$ROOT is READ-ONLY.  This test only reads.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from c3phy import VARIANTS, iter_chunks, parse_phy       # noqa: E402
from c3write import serialize_phy                        # noqa: E402
import validate_phy                                      # noqa: E402


def sources(use_wdf=True):
    yield from validate_phy.loose_sources()
    yield from validate_phy.sample_sources()
    if use_wdf:
        yield from validate_phy.wdf_sources()


def main(argv=()):
    argv = list(argv)
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    use_wdf = "--no-wdf" not in argv
    verbose = "-v" in argv or "--verbose" in argv

    per_tag = Counter()
    ok_tag = Counter()
    fails = []
    containers = 0
    container_exact = 0
    container_total = 0

    for label, blob in sources(use_wdf):
        containers += 1
        if limit and containers > limit:
            break
        try:
            chunks = list(iter_chunks(blob))
        except Exception as e:                              # noqa: BLE001
            fails.append((label, "container", repr(e)))
            continue

        # whole-container round trip: PHY chunks re-serialized, others verbatim
        rebuilt = bytearray(blob[:16])
        import struct
        has_phy = False
        for tag, body in chunks:
            if tag in VARIANTS:
                has_phy = True
                per_tag[tag] += 1
                try:
                    m = parse_phy(tag, body)
                    got = serialize_phy(m)
                except Exception as e:                      # noqa: BLE001
                    fails.append((label, tag.decode(errors="replace"),
                                  repr(e)))
                    got = body
                else:
                    if got == body:
                        ok_tag[tag] += 1
                    else:
                        n = min(len(got), len(body))
                        where = next((i for i in range(n)
                                      if got[i] != body[i]), n)
                        fails.append((
                            label, tag.decode(errors="replace"),
                            f"{m.name!r} len {len(got)}!={len(body)} "
                            f"diff@0x{where:X} "
                            f"got={got[where:where+8].hex()} "
                            f"want={body[where:where+8].hex()}"))
                body = got
            rebuilt += tag + struct.pack("<I", len(body)) + body
        if has_phy:
            container_total += 1
            if bytes(rebuilt) == blob:
                container_exact += 1

    total = sum(per_tag.values())
    good = sum(ok_tag.values())

    print(f"containers scanned : {containers}")
    print(f"PHY chunks         : {total}")
    print()
    print(f"{'tag':6s} {'seen':>8s} {'byte-exact':>11s} {'rate':>8s}")
    for tag in sorted(per_tag):
        s, o = per_tag[tag], ok_tag[tag]
        print(f"{tag.decode():6s} {s:8d} {o:11d} {100.0*o/s:7.2f}%")
    print(f"{'TOTAL':6s} {total:8d} {good:11d} "
          f"{100.0*good/max(total,1):7.2f}%")
    print(f"\nwhole .c3 containers byte-exact: "
          f"{container_exact}/{container_total}")

    if fails:
        print(f"\n=== {len(fails)} FAILURES (showing "
              f"{'all' if verbose else 'first 30'}) ===")
        for f in (fails if verbose else fails[:30]):
            print(f"  {f[0]}  [{f[1]}]  {f[2]}")
    else:
        print("\nPASS - every PHY chunk re-serializes byte-exactly.")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
