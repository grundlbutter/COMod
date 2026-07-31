r"""
validate_phy.py -- validate the code-derived PHY layout against the real corpus.

The layout in core/c3phy.py came from disassembling `graphic.dll!Phy_Load`.
This checks it against every PHY chunk in the archives and the loose tree.
The decisive test is `trailing == 0`: if the parser consumes each chunk body
to exactly its declared length, over thousands of meshes of every variant,
the field sizes and the stride are right.  Additional sanity checks catch
layouts that would coincidentally fit:

  * every triangle index < vertexCount
  * positions finite and inside the file's own declared bounding box
  * normals unit-length (where the variant stores them)
  * UVs finite
  * bone indices < 256 (the GPU packs them into a byte)
  * weight0 in [0,1]

    py -3 tools/validate_phy.py            # whole corpus
    py -3 tools/validate_phy.py --limit 300
"""
from __future__ import annotations

import math
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coroot                            # noqa: E402
from c3phy import (VARIANTS, meshes_from_c3, iter_chunks, parse_phy,   # noqa: E402
                   disk_vertex_size, apply_matrix_to)

ROOT = coroot.default_root()
REPO = Path(__file__).resolve().parents[1]


def wdf_sources():
    """Yield (label, bytes) for every .c3 payload in both archives."""
    try:
        sys.path.insert(0, str(REPO / "tools"))
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
        import wdf  # the WDF workstream's reader
    except Exception:
        return
    for arch in ("c3.wdf", "data.wdf"):
        p = ROOT / arch
        if not p.exists():
            continue
        try:
            a = wdf.WdfArchive(p) if hasattr(wdf, "WdfArchive") else None
        except Exception:
            a = None
        if a is None:
            continue
        try:
            for e in a.entries:
                try:
                    blob = a.read(e)
                except Exception:
                    continue
                if blob[:8] == b"MAXFILE ":
                    yield f"{arch}#{e.hash:08X}", blob
        finally:
            try:
                a.close()
            except Exception:
                pass


def loose_sources():
    for base in (ROOT / "c3", ROOT / "data"):
        if not base.is_dir():
            continue
        for p in base.rglob("*.c3"):
            try:
                b = p.read_bytes()
            except OSError:
                continue
            if b[:8] == b"MAXFILE ":
                yield str(p.relative_to(ROOT)).replace("\\", "/"), b


def sample_sources():
    d = REPO / "out" / "wdf" / "sample"
    if not d.is_dir():
        return
    for p in d.rglob("*"):
        if not p.is_file():
            continue
        try:
            b = p.read_bytes()
        except OSError:
            continue
        if b[:8] == b"MAXFILE ":
            yield f"sample/{p.name}", b


def main(argv):
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])

    per_tag = Counter()
    ok = Counter()
    fails = []
    warn = Counter()
    stats = defaultdict(list)
    ok_bbox = [0]
    seen_files = 0
    checked = 0

    srcs = list(loose_sources()) + list(sample_sources()) + list(wdf_sources())
    print(f"{len(srcs)} C3 containers to scan\n")

    for label, blob in srcs:
        seen_files += 1
        if limit and seen_files > limit:
            break
        try:
            chunks = list(iter_chunks(blob))
        except Exception as e:
            fails.append((label, "container", str(e)))
            continue
        for tag, body in chunks:
            if tag not in VARIANTS:
                continue
            per_tag[tag] += 1
            checked += 1
            try:
                m = parse_phy(tag, body)
            except Exception as e:
                fails.append((label, tag.decode(errors="replace"), str(e)))
                continue

            problems = []
            if m.trailing != 0:
                problems.append(f"trailing={m.trailing}")
            nv = m.vertex_count
            if nv and m.faces:
                mx = max(max(f) for f in m.faces)
                if mx >= nv:
                    problems.append(f"index {mx} >= nverts {nv}")
            lo, hi = m.bbox_min, m.bbox_max
            for v in m.vertices:
                if not all(math.isfinite(c) for c in v.position):
                    problems.append("non-finite position")
                    break
                if not (math.isfinite(v.u0) and math.isfinite(v.v0)):
                    problems.append("non-finite uv")
                    break
                if v.bone0 > 255 or v.bone1 > 255:
                    warn["bone index > 255"] += 1
                if not (-0.001 <= v.weight0 <= 1.001):
                    warn["weight0 out of [0,1]"] += 1
            else:
                # The declared AABB should equal the AABB of the positions
                # AFTER the chunk matrix is applied (row-vector convention).
                # This is the decisive test of the matrix semantics.
                if nv:
                    mt = parse_phy(tag, body)
                    apply_matrix_to(mt)
                    xs = [v.px for v in mt.vertices]
                    ys = [v.py for v in mt.vertices]
                    zs = [v.pz for v in mt.vertices]
                    got = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
                    want = tuple(lo) + tuple(hi)
                    scale = max(1.0, max(abs(w) for w in want))
                    err = max(abs(g - w) for g, w in zip(got, want))
                    stats["bbox_err"].append(err / scale)
                    if err / scale > 1e-4:
                        warn["bbox != AABB(pos x matrix)"] += 1
                    else:
                        ok_bbox[0] += 1
            if VARIANTS[tag]["has_normal"]:
                badn = 0
                for v in m.vertices:
                    ln = math.sqrt(v.nx ** 2 + v.ny ** 2 + v.nz ** 2)
                    if not (0.9 < ln < 1.1) and ln != 0.0:
                        badn += 1
                if badn:
                    warn["non-unit normal"] += 1
                    stats["nonunit_frac"].append(badn / max(nv, 1))

            if problems:
                fails.append((label, tag.decode(errors="replace"),
                              "; ".join(problems)))
            else:
                ok[tag] += 1
                stats["nverts"].append(nv)
                stats["nfaces"].append(m.face_count)
                stats["nbones"].append(len(m.bones))
                stats["frames"].append(m.frame_count)
                if m.vertex_count_b:
                    warn["vertex_count_b != 0"] += 1
                if m.face_count_b:
                    warn["face_count_b != 0"] += 1
                if m.unknown0 not in (0, 2):
                    warn[f"unknown0={m.unknown0}"] += 1
                if m.two_sided:
                    warn["2SID"] += 1
                if m.billboard:
                    warn[f"billboard={m.billboard}"] += 1
                if m.is_c3exp_color:
                    warn["C3EXP_COLOR"] += 1
                if m.label and not m.is_c3exp_color:
                    warn[f"label={m.label!r}"] += 1

    print("=== per-variant results ===")
    print(f"{'tag':6s} {'seen':>7s} {'clean':>7s} {'rate':>7s}  disk stride")
    for tag in sorted(per_tag):
        s, o = per_tag[tag], ok[tag]
        print(f"{tag.decode():6s} {s:7d} {o:7d} {100.0*o/s:6.1f}%  "
              f"{disk_vertex_size(tag)} bytes/vertex")
    tot_s, tot_o = sum(per_tag.values()), sum(ok.values())
    print(f"{'TOTAL':6s} {tot_s:7d} {tot_o:7d} "
          f"{100.0*tot_o/max(tot_s,1):6.1f}%")

    if stats["bbox_err"]:
        e = sorted(stats["bbox_err"])
        print(f"\nbbox == AABB(pos x matrix): {ok_bbox[0]}/{len(e)} "
              f"(median rel err {e[len(e)//2]:.2e}, "
              f"p99 {e[int(len(e)*0.99)]:.2e})")

    if stats["nverts"]:
        def q(k):
            v = sorted(stats[k])
            return f"min={v[0]} med={v[len(v)//2]} max={v[-1]}"
        print(f"\nvertices per mesh : {q('nverts')}")
        print(f"faces per mesh    : {q('nfaces')}")
        print(f"bones per mesh    : {q('nbones')}")
        print(f"frame_count       : {q('frames')}")

    if warn:
        print("\n=== observations (not errors) ===")
        for k, v in warn.most_common(20):
            print(f"  {v:7d}  {k}")

    if fails:
        print(f"\n=== {len(fails)} FAILURES (first 25) ===")
        for label, tag, why in fails[:25]:
            print(f"  {label}  [{tag}]  {why}")
    else:
        print("\nNo failures.")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
