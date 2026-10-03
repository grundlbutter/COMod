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

import hashlib
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


# ---------------------------------------------------------------------------
# KNOWN-BAD CONTAINER MANIFEST
# ---------------------------------------------------------------------------
# An exclusion that turns a red gate green is the most dangerous edit in this
# repo's history -- `core/coassets.py`'s census comment and the appearance
# metric both record a version of "a rule that raised a score by DELETING
# EVIDENCE". So this manifest is built to be unable to excuse anything it was
# not written for:
#
#   * KEYED ON (install, exact label). Never a pattern, never a prefix, never
#     a directory. Three entries means three containers.
#   * THE BYTES MUST MATCH. Each entry carries the md5 of the exact blob that
#     was diagnosed. If the file changes at all -- repaired, repacked,
#     substituted -- the entry stops matching and the container FAILS again.
#   * THE ERROR MUST MATCH. A listed container that fails a DIFFERENT way is
#     a new defect wearing a known name, and is treated as new.
#   * A MISS IS STILL A FAILURE. Anything not matching all three is counted,
#     printed and FAILS, exactly as before this existed.
#   * THE COUNTS ARE PRINTED ON EVERY RUN, including zero. A silent manifest
#     would reintroduce the invisibility that let the two-name archive loop
#     hide for as long as it did.
#   * A STALE ENTRY IS REPORTED. An entry whose container no longer fails
#     excuses nothing, so it does not fail the gate -- but it is dead weight
#     that would otherwise be copied forward forever, and a control that can
#     no longer fire is not evidence. It is named in the output.
#
# Diagnosis, and the disproof of every repair route, is in
# `docs/zephyr_broken_containers_2026-09-06.md`. All three are missing bytes
# that exist nowhere on this box -- verified by `scratchpad/zephyr_hunt.py`,
# which searches every install loose and archived, by name and by hash.
#
# DO NOT ADD A ROW HERE TO MAKE A GATE GREEN. Add one only when a container
# has been proved unrepairable and the proof is written down.
KNOWN_BAD_CONTAINERS = {
    ("Zephyr", "c3.tpi#c3/effect/garment/189050/6v2.c3"): (
        "e9450d8884391ece5039351a44da6fa5",
        "truncated at 16710",
        "18 PHY4 bodies short of their own length fields; the sibling 6.c3 "
        "carries the same length sequence and parses exactly. MOTI intact.",
    ),
    ("Zephyr", "c3.tpi#c3/effect/weapon/421548/2.c3"): (
        "c0e66157fbd0cf7c6912b9d3fd100c48",
        "truncated at 6580",
        "missing 25 bytes: an 8-byte SHAP header plus 17 body bytes. The "
        "38-byte gap is the TAIL of a SHAP body, not the head.",
    ),
    ("Zephyr", "garments.wdf#15D70C70"): (
        "c2177b9ae2b7b664ce547937adff2813",
        "truncated at 16",
        "one PHY4 declaring 908,433 bytes in a 749,138-byte file: 159,319 "
        "short, no other known tag anywhere to resume from.",
    ),
}

#: Filled by `classify_container_failure`, reset by `reset_manifest_state`.
#: `matched` are known-bad hits; `unmatched_entries` are manifest rows that
#: never fired on this run.
MANIFEST_MATCHED: list = []


def reset_manifest_state():
    MANIFEST_MATCHED.clear()


def classify_container_failure(label: str, blob: bytes, err: str):
    """Is this refused container a KNOWN-bad one, or a new defect?

    Returns the manifest note when all three of (install, label), md5 and
    error text agree, and None otherwise. **None means FAIL** -- every
    disagreement, including a near miss, is a new defect.
    """
    key = (Path(ROOT).name, label)
    entry = KNOWN_BAD_CONTAINERS.get(key)
    if entry is None:
        return None
    want_md5, want_err, note = entry
    got_md5 = hashlib.md5(blob).hexdigest()
    if got_md5 != want_md5:
        return None
    if want_err not in err:
        return None
    MANIFEST_MATCHED.append((label, note))
    return note


def format_manifest_report(new_unreadable: int) -> str:
    """The manifest block, printed on EVERY run including when it is empty."""
    inst = Path(ROOT).name
    rows = [k for k in KNOWN_BAD_CONTAINERS if k[0] == inst]
    out = [f"known-bad manifest: {len(rows)} entr(ies) for {inst}, "
           f"{len(MANIFEST_MATCHED)} matched, "
           f"{new_unreadable} unreadable container(s) NOT in the manifest"]
    for label, note in MANIFEST_MATCHED:
        out.append(f"    known-bad  {label}")
        out.append(f"               {note}")
    stale = [k[1] for k in rows
             if k[1] not in {m[0] for m in MANIFEST_MATCHED}]
    for label in stale:
        out.append(f"    !! STALE   {label} is listed but did NOT fail on this "
                   f"run -- the entry no longer fires; verify and remove it")
    return "\n".join(out)


#: One row per archive `archive_sources` TRIED, rebuilt on every call:
#: ``(name, reader, entries, c3_payloads, note)``.  The gates print it.
#:
#: THIS EXISTS BECAUSE THE OLD BEHAVIOUR WAS INVISIBLE.  Until 2026-09-05 this
#: module opened `c3.wdf` and `data.wdf` BY NAME, and 7632/7682/7867/7878 and
#: Zephyr ship no `.wdf` at all -- so on those five installs the archive source
#: silently yielded nothing and both gates quietly ran on the loose tree.  The
#: census in `core/coassets.C3_TAGS` said 245,463 MOTI for 7632 and the gate
#: reported 81,963 with a PASS.  Nothing in either output named an archive, so
#: there was nothing to notice.  A skipped archive now costs a printed row.
LAST_ARCHIVE_REPORT: list = []


def _peek(arc, entry, n=16):
    """First `n` bytes of `entry`, cheaply where the container allows it.

    `WdfArchive.peek` and `_TpdContainer.peek` both avoid inflating a whole
    payload to read a magic number; the TPD half of that knowledge lives on
    the container, not here, because it is format knowledge.

    A container with no `peek` gets a full read rather than a short answer --
    **a wrong `b""` here DROPS the entry from the corpus silently**, which is
    the failure this whole widening exists to remove.
    """
    peek = getattr(arc, "peek", None)
    if peek is not None:
        return peek(entry, n)
    read = getattr(arc, "read", None) or getattr(arc, "read_entry")
    return read(entry)[:n]


def archive_sources():
    """Yield (label, bytes) for every C3 payload in every archive of $ROOT.

    WIDENED 2026-09-05.  This used to be `wdf_sources` and it opened exactly
    `c3.wdf` and `data.wdf`.  It now asks `AssetRoot._discover_archives` --
    the same discovery the `core/coassets.C3_TAGS` census uses -- so a `.tpi`
    pair is opened too, and `AssetRoot._reader_for` picks the reader.

    Both writer gates draw from here, and that is deliberate: widening this
    widens `tests/test_roundtrip.py` (PHY) and `tests/test_moti_roundtrip.py`
    (MOTI) in one step, so the two writers stay held to the SAME bytes.

    MEMBERSHIP IS BY MAGIC, NOT BY NAME, on every reader.  The census filters
    TPD entries with `e.name.endswith('.c3')` and WDF entries with a magic
    peek; that inconsistency is not imported here.  Measured on 7632 before
    choosing: of `c3.tpi`'s 59,964 entries, 37,178 are named `.c3` and 37,174
    carry the magic -- the four extras are not containers, and **zero**
    containers hide under another name, so on that install the two rules agree
    and the stricter one is free.  On an install where they disagree this
    yields the container and the census would not.
    """
    try:
        sys.path.insert(0, str(REPO / "tools"))
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
        from coassets import AssetRoot
    except Exception as ex:                                 # noqa: BLE001
        LAST_ARCHIVE_REPORT.append(
            ("(none)", "-", 0, 0, f"coassets import failed: {ex!r}"))
        return

    LAST_ARCHIVE_REPORT.clear()
    try:
        archives = AssetRoot._discover_archives(Path(ROOT))
    except Exception as ex:                                 # noqa: BLE001
        LAST_ARCHIVE_REPORT.append(
            ("(none)", "-", 0, 0, f"discovery failed: {ex!r}"))
        return
    if not archives:
        LAST_ARCHIVE_REPORT.append(
            ("(none)", "-", 0, 0, "no archive found in $ROOT"))
        return

    for ap in archives:
        reader = AssetRoot._reader_for(ap)
        rname = getattr(reader, "__name__", str(reader))
        if reader is None:
            LAST_ARCHIVE_REPORT.append(
                (ap.name, "-", 0, 0, "no reader for this suffix"))
            continue
        try:
            arc = reader(ap)
        except Exception as ex:                             # noqa: BLE001
            LAST_ARCHIVE_REPORT.append(
                (ap.name, rname, 0, 0, f"open failed: {ex!r}"))
            continue
        row = [ap.name, rname, 0, 0, ""]
        LAST_ARCHIVE_REPORT.append(row)
        try:
            ents = getattr(arc, "entries", None)
            ents = list(ents) if ents is not None else list(arc)
            # `_TpdContainer` exposes `read_entry`; `WdfArchive` exposes `read`.
            read = getattr(arc, "read", None) or getattr(arc, "read_entry")
            for e in ents:
                row[2] += 1
                try:
                    if _peek(arc, e, 8) != b"MAXFILE ":
                        continue
                    blob = read(e)
                except Exception:                           # noqa: BLE001
                    continue
                if blob[:8] != b"MAXFILE ":
                    continue
                row[3] += 1
                h = getattr(e, "hash", None)
                if h is not None:            # WDF is keyed by hash, not name
                    yield f"{ap.name}#{h:08X}", blob
                else:
                    yield f"{ap.name}#{getattr(e, 'name', '?')}", blob
        finally:
            try:
                arc.close()
            except Exception:                               # noqa: BLE001
                pass


#: The name both gates used before the widening, and the name in every
#: recorded command in `docs/moti_writer_2026-09-05.md`.  Kept so those keep
#: running; it is the SAME function now, not the old two-name loop.
wdf_sources = archive_sources


def format_archive_report() -> str:
    """The archive provenance block the gates print under their verdict."""
    # THE ROOT LEADS THIS BLOCK, AND THAT IS NOT DECORATION. Neither gate
    # printed $ROOT anywhere, so two runs against different installs were
    # indistinguishable from two runs against the same one. MEASURED
    # 2026-09-05: 7682 returned 57,381 containers / 245,463 MOTI / 179,586
    # PHY, digit for digit what 7632 returned, and nothing in either output
    # said which install it was. That is genuine -- the two ship byte-identical
    # archives (c3.tpi md5 c9efcfc1..., data.tpi 6421f674...) -- but "the
    # corpora really are identical" and "CO_ROOT never took effect" produce the
    # SAME OUTPUT, and only one of them is a result.
    root_line = f"root: {ROOT}"
    if not LAST_ARCHIVE_REPORT:
        return root_line + "\narchives: (archive source not consulted)"
    out = [root_line, "archives opened:"]
    for name, reader, ents, c3, note in LAST_ARCHIVE_REPORT:
        line = f"    {name:<16} {reader:<16} {ents:>8,} entries {c3:>8,} C3"
        if note:
            line += f"   !! {note}"
        out.append(line)
    return "\n".join(out)


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
