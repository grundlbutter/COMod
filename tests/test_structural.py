r"""
test_structural.py -- adding and removing whole meshes must keep the
PHY <-> MOTI pairing intact.

A PHY chunk is bound to a MOTI chunk positionally (docs/modding.md section 11),
so every structural edit has to move both. This checks, over real containers:

  * removing mesh k drops exactly one PHY and one MOTI
  * every surviving mesh keeps the SAME motion track it had before, byte for
    byte -- verified by re-pairing the rebuilt file and comparing MOTI bodies
  * adding a mesh synthesises a MOTI whose boneCount covers the new mesh's
    bone palette, and leaves every pre-existing pair untouched
  * the result re-parses cleanly: all PHY chunks byte-exact through
    parse -> serialize, all MOTI chunks consumed to their declared length
  * a no-op rebuild is still byte-identical

    py -3 tests\test_structural.py [--count N]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from c3phy import VARIANTS, iter_chunks, meshes_from_c3, parse_phy  # noqa: E402
from c3write import (MOTI_TAG, rebuild_c3, serialize_phy,           # noqa: E402
                     synth_moti)
from effects import parse_moti                                      # noqa: E402
import validate_phy as V                                            # noqa: E402


def split(data):
    """-> (phy_bodies, moti_bodies, tags)"""
    chunks = list(iter_chunks(data))
    return ([b for t, b in chunks if t in VARIANTS],
            [b for t, b in chunks if t == MOTI_TAG],
            [t for t, _ in chunks])


def check_parses(data, label, problems):
    """Everything in the rebuilt container must still parse exactly."""
    for tag, body in iter_chunks(data):
        if tag in VARIANTS:
            m = parse_phy(tag, body)
            if m.trailing:
                problems.append(f"{label}: {m.name!r} trailing={m.trailing}")
            if serialize_phy(m) != body:
                problems.append(f"{label}: {m.name!r} not byte-stable")
            if m.faces and max(max(f) for f in m.faces) >= m.vertex_count:
                problems.append(f"{label}: {m.name!r} index out of range")
        elif tag == MOTI_TAG:
            mo = parse_moti(body)
            if not mo.exact:
                problems.append(f"{label}: MOTI consumed {mo.consumed}"
                                f"/{mo.size}")


def case_noop(data, problems):
    ms = meshes_from_c3(data)
    if rebuild_c3(data, ms) != data:
        problems.append("no-op rebuild is not byte-identical")


def case_remove(data, problems):
    ms = meshes_from_c3(data)
    if len(ms) < 2:
        return 0
    p0, m0, _ = split(data)
    drop = len(ms) // 2
    kept = [m for i, m in enumerate(ms) if i != drop]
    out = rebuild_c3(data, kept, allow_structural=True)
    p1, m1, _ = split(out)

    if len(p1) != len(p0) - 1 or len(m1) != len(m0) - 1:
        problems.append(f"remove: PHY {len(p0)}->{len(p1)}, "
                        f"MOTI {len(m0)}->{len(m1)}, expected both -1")
        return 1
    # THE point of the test: each survivor kept its own motion track
    want = [m0[i] for i in range(len(ms)) if i != drop]
    if m1 != want:
        problems.append("remove: surviving meshes did not keep their "
                        "original MOTI tracks")
    for a, b in zip([p0[i] for i in range(len(ms)) if i != drop], p1):
        if a != b:
            problems.append("remove: a surviving PHY chunk changed bytes")
            break
    check_parses(out, "remove", problems)
    return 1


def case_add(data, problems):
    ms = meshes_from_c3(data)
    p0, m0, _ = split(data)
    if not m0:
        return 0
    clone = parse_phy(ms[0].tag, p0[0])
    clone.source_index = None                 # a brand-new mesh
    clone.name = "added_mesh"
    clone.name_raw = b"added_mesh"
    out = rebuild_c3(data, list(ms) + [clone], allow_structural=True)
    p1, m1, _ = split(out)

    if len(p1) != len(p0) + 1 or len(m1) != len(m0) + 1:
        problems.append(f"add: PHY {len(p0)}->{len(p1)}, "
                        f"MOTI {len(m0)}->{len(m1)}, expected both +1")
        return 1
    if m1[:len(m0)] != m0:
        problems.append("add: pre-existing MOTI tracks were disturbed")
    if p1[:len(p0)] != p0:
        problems.append("add: pre-existing PHY chunks were disturbed")
    mo = parse_moti(m1[-1])
    need = (max(clone.bones) + 1) if clone.bones else 1
    if mo.bone_count < need:
        problems.append(f"add: synthesised MOTI boneCount {mo.bone_count} "
                        f"does not cover palette (needs {need})")
    check_parses(out, "add", problems)
    return 1


def main(argv=()):
    argv = list(argv)
    limit = 300
    if "--count" in argv:
        limit = int(argv[argv.index("--count") + 1])

    # synth_moti must be readable by the independent parser in tools/effects.py
    probs = []
    for bc in (1, 2, 51, 255):
        mo = parse_moti(synth_moti(bc, 31))
        if not mo.exact or mo.bone_count != bc or mo.encoding != "KKEY":
            probs.append(f"synth_moti({bc}) did not round-trip: "
                         f"{mo.encoding} bones={mo.bone_count} "
                         f"{mo.consumed}/{mo.size}")
        elif mo.matrix(bc - 1, 999) != tuple(
                [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]):
            probs.append(f"synth_moti({bc}) is not identity at every frame")
    print(f"synth_moti self-check: {'ok' if not probs else 'FAILED'}")

    tested = rem = add = 0
    for label, blob in V.loose_sources():
        if tested >= limit:
            break
        try:
            chunks = list(iter_chunks(blob))
        except Exception:
            continue
        if not any(t in VARIANTS for t, _ in chunks):
            continue
        tested += 1
        p = []
        try:
            case_noop(blob, p)
            rem += case_remove(blob, p)
            add += case_add(blob, p)
        except Exception as e:                                  # noqa: BLE001
            p.append(f"{type(e).__name__}: {e}")
        probs += [f"{label}: {x}" for x in p]

    print(f"containers exercised : {tested}")
    print(f"  remove cases       : {rem}")
    print(f"  add cases          : {add}")
    if probs:
        print(f"\n=== {len(probs)} PROBLEMS (first 25) ===")
        for x in probs[:25]:
            print(f"  {x}")
    # The count belongs ON the verdict line: a verdict without one cannot be
    # cross-checked against the work it claims to summarise, and a run that
    # exercised nothing reads identically to a run that passed.
    print(f"\nRESULT: {'FAIL' if probs else 'PASS'} -- {tested} container(s) "
          f"exercised, {rem} remove + {add} add case(s), {len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
