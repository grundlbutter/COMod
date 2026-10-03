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
  * every archive `AssetRoot._discover_archives` finds -- `.wdf` AND `.tpi`
    pairs -- every MAXFILE payload

$ROOT is READ-ONLY.  This test only reads.

WIDENED 2026-09-05.  The third source used to be `c3.wdf` and `data.wdf` by
name, so on the five installs that ship a `.tpi` pair and no `.wdf` at all
(7632, 7682, 7867, 7878, Zephyr) it yielded nothing and this gate ran on the
loose tree while printing a PASS.  It now prints which archives it opened.
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
        yield from validate_phy.archive_sources()


def main(argv=()):
    argv = list(argv)
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    use_wdf = "--no-wdf" not in argv
    validate_phy.reset_manifest_state()
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
            # A container in the known-bad manifest is recorded, not failed --
            # but ONLY on an exact (install, label) + md5 + error match. Every
            # other refusal, including a near miss, still fails.
            if validate_phy.classify_container_failure(label, blob, repr(e)):
                continue
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
    if use_wdf:
        # WHICH archives, not just how many containers. An archive that
        # contributes nothing is a printed row here rather than an absence.
        print(validate_phy.format_archive_report())
    print()
    print(f"{'tag':6s} {'seen':>8s} {'byte-exact':>11s} {'rate':>8s}")
    for tag in sorted(per_tag):
        s, o = per_tag[tag], ok_tag[tag]
        print(f"{tag.decode():6s} {s:8d} {o:11d} {100.0*o/s:7.2f}%")
    print(f"{'TOTAL':6s} {total:8d} {good:11d} "
          f"{100.0*good/max(total,1):7.2f}%")
    print(f"\nwhole .c3 containers byte-exact: "
          f"{container_exact}/{container_total}")

    # TWO FAILURE KINDS, COUNTED SEPARATELY. A chunk that did not round-trip
    # is a WRITER defect; a container `iter_chunks` refused never reached the
    # writer at all. Reporting one number for both says "the writer failed 3
    # times" about an install where every chunk that parsed was byte-exact.
    # Neither is downgraded -- both still FAIL -- they are just named.
    unreadable = [f for f in fails if f[1] == "container"]
    chunkfails = [f for f in fails if f[1] != "container"]
    if chunkfails:
        print(f"\n=== {len(chunkfails)} CHUNK FAILURES (showing "
              f"{'all' if verbose else 'first 30'}) ===")
        for f in (chunkfails if verbose else chunkfails[:30]):
            print(f"  {f[0]}  [{f[1]}]  {f[2]}")
    print()
    print(validate_phy.format_manifest_report(len(unreadable)))
    if unreadable:
        print(f"\n=== {len(unreadable)} CONTAINER(S) THE CHUNK WALKER REFUSED ===")
        print("  These never reached the writer. A container whose declared "
              "chunk length runs")
        print("  past its own end is malformed input, not a writer defect -- "
              "but it is still")
        print("  a corpus this gate cannot vouch for, so it FAILS.")
        for f in (unreadable if verbose else unreadable[:30]):
            print(f"  {f[0]}  {f[2]}")
    if not fails:
        print("\nPASS - every PHY chunk re-serializes byte-exactly.")
    # A `RESULT:` line with its counts on it, in the same shape as the other
    # script gates. Before this the only verdict was the prose "PASS - ..."
    # above, printed on success only -- so nothing could be grepped for a
    # verdict, and nothing could check the verdict against the work done.
    print(f"\nRESULT: {'FAIL' if fails else 'PASS'} -- {containers} container(s) "
          f"scanned, {total} PHY chunk(s), {good} byte-exact, "
          f"{len(chunkfails)} chunk failure(s), "
          f"{len(unreadable)} unreadable container(s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
