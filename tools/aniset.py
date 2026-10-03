#!/usr/bin/env python3
r"""
aniset.py -- an `.ani` sequence handled AS A SEQUENCE: show, export, re-import.

An `.ani` names an ordered set of separate `.dds` frames per section::

    [Puzzle0]
    FrameAmount=1
    Frame0=data/map/puzzle/archer/bg/01/pic0000.dds

Nothing here needs a parser -- `core/ani.py` is the manifest reader and the
frames are ordinary textures. What this adds is that the SET is one unit: the
frames come out together in order, go back together in order, and `FrameAmount`
is written from the same list that numbers `Frame0..N-1`, in the same call.

THE FAILURE MODE THIS IS BUILT AROUND
-------------------------------------
**The order is the animation, and getting it wrong is silent.** An export that
loses ordering, or an import that renumbers inconsistently with `FrameAmount`,
produces a manifest the client reads as a *different* animation -- not a broken
one. No crash, no missing-file dialog, no log line. So:

* The exported filenames carry a **zero-padded ordinal prefix**, which is the
  ordering authority on the way back in. It sorts correctly in every file
  browser, every shell glob and every `sorted()`.
* Import **refuses** an ordinal set that is not exactly ``0..N-1``: a gap, a
  duplicate or a non-numeric name is an error naming the file, never a
  best-effort renumber.
* A frame the install does not ship is exported as a **zero-byte `.absent`
  marker at its own ordinal**, not omitted. Omitting it is the specific bug
  that renumbers every frame after it -- see `--drop-missing`, which is the
  only way to remove one and which SAYS what shifted.
* `ani.AniFile.rewrite_frames` asserts its own output: it re-parses and
  compares the frames it read back against the list it was given, and checks
  `FrameAmount` against that same length. A writer that cannot check itself is
  how a wrong answer gets shipped.

DECLARED-BUT-ABSENT IS A REPORTED STATE, NOT A DROPPED ROW
----------------------------------------------------------
MEASURED 2026-09-07 by `tools/aniset.py` over a seeded random sample of 2,000
non-empty sequences per install (``random.seed(20260907)``), resolving each
frame through `coassets.AssetRoot` -- archive-aware, which is the only way to
ask: ``data/ItemMinIcon/`` holds a few hundred LOOSE files against tens of
thousands of references, so an `os.path.exists` check would report almost
every frame missing and the number would be meaningless::

    client   population   sampled   frames   absent   sequences with >=1 absent
    5517       117,918      2,000    2,068      175                        153
    6609       223,525      2,000    2,135      373                        333
    7205       259,060      2,000    2,151      515                        509
    7878       484,101      2,000    2,490      463                        456

So between 8% and 24% of sampled sequences reference at least one frame the
install does not ship. Absent frames are ordinary, not exotic -- which is
exactly why dropping one quietly is unacceptable: it renumbers every frame
after it.

USAGE::

    py -3 tools/aniset.py list   --root <client>
    py -3 tools/aniset.py show   ani/Effect.ani MagicType1000 --root <client>
    py -3 tools/aniset.py export ani/Effect.ani MagicType1000 --root <client>
    #  ... edit the .dds / .png files in Installed/work/aniset/... ...
    py -3 tools/aniset.py import Installed/work/aniset/Effect/MagicType1000
    py -3 tools/aniset.py verify ani/Effect.ani --root <client>

The same four verbs are reachable as ``comod.py ani-list / ani-show /
ani-export / ani-import``, which is where a user meets them.

Writes only into ``Installed/work`` (export) and ``Installed/stage`` (import).
The client install is never written -- that is `comod.py install`'s job, and it
picks the staged `.ani` up like any other loose file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

import ani as anilib                       # noqa: E402
import comod                               # noqa: E402
import coroot                              # noqa: E402
import safepath                            # noqa: E402
from coassets import AssetRoot, dds_info    # noqa: E402

PROJECT = HERE.parent
#: Same two trees `comod.py` uses, and for the same reason -- the dev checkout
#: and the shipped layout must not be two different paths.
#:
#: IMPORTED, NOT RE-DERIVED. This block used to build both paths from its own
#: string literals while the comment above claimed they were `comod.py`'s.
#: They agreed, which is exactly why it survived: agreement at runtime is not
#: the property that matters -- two independent definitions of one tree are
#: one edit away from being two trees, and the copy that is not the authority
#: is the one nobody updates. `tests/test_one_stage_tree.py` reads the source
#: rather than the values for the same reason, and it is what caught this.
STAGE = comod.STAGE
WORK = comod.WORK

MANIFEST = "sequence.json"
FRAMES = "frames"
#: `NNN_name.ext` or `NNN.ext`. The ordinal is the ordering authority.
_ORDINAL = re.compile(r"^(\d+)(?:[_-](.*))?$")
#: Anything a filesystem or a shell would fight over in a section name.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class SequenceError(RuntimeError):
    """A refusal that names what is wrong. Never a silent best effort."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def safe_name(section: str) -> str:
    """A directory name for a section, reversible enough to be recognisable.

    The manifest carries the section's REAL name; this is only the folder
    label, so a lossy squeeze is fine and a collision is caught by the manifest
    on import (which compares its recorded name, not the folder).
    """
    s = _UNSAFE.sub("_", section.strip())
    return s or "_"


def _slug(logical: str) -> str:
    r"""A frame's identity in a filename: enough path to be UNIQUE, sanitised.

    The bare basename is not enough and that is not a corner case. 7878's
    ``cartoon.ani`` ``[Puzzle0]`` is a 17-frame ping-pong over nine directories
    whose files are ALL called ``pic000.dds``, so exporting by basename gives
    seventeen files that differ only in their ordinal -- and a user who
    reorders them by renaming would be expressing a permutation the importer
    could not read back, because no two names carry different identities.

    Three trailing components, joined with ``-``: ``five/01/pic000.dds`` ->
    ``five-01-pic000``. Ordinal prefix plus this is what `import_set` matches a
    file back to its declared path with.
    """
    parts = [p for p in str(logical).replace("\\", "/").split("/") if p]
    tail = parts[-3:] if len(parts) >= 3 else parts
    if tail:
        tail[-1] = Path(tail[-1]).stem
    return _UNSAFE.sub("_", "-".join(tail))


def _load_ani(R: AssetRoot, logical: str) -> tuple[anilib.AniFile, str]:
    """Read an `.ani` -- from the stage tree if it is staged, else the install.

    Staged first is what lets two sequences in the same file be edited one
    after the other: the second import must build on the first's output, not
    on the pristine install copy, or it silently reverts it.
    """
    staged = safepath.confine(STAGE, logical)
    if staged.is_file():
        return anilib.AniFile.read(staged), "stage"
    loc = R.locate(logical)
    if not loc:
        raise SequenceError(f"not found on this install: {logical}")
    data = R.read(logical)
    return anilib.AniFile(data.decode("latin-1", errors="replace"),
                          Path(logical)), loc.source


def _frame_present(R: AssetRoot, logical: str) -> bool:
    if not logical:
        return False
    try:
        return R.locate(logical) is not None
    except Exception:                      # noqa: BLE001 -- an unsafe path is "no"
        return False


def _sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def iter_ani(root: Path):
    """Every `.ani` under ``<root>/ani`` that is actually a TEXT manifest.

    `data/Cursor/*.ani` is NOT one of these: those are Windows animated
    cursors, RIFF/`ACON` binaries that merely share the extension (45 of them
    on 7878, all four bytes ``52 49 46 46``). Counting them as frame manifests
    is how "141 .ani on 7878" and "~448,000 frame references" came to be two
    numbers measured over two different populations -- the reference count is
    the 92 files under ``ani/``.
    """
    d = Path(root) / "ani"
    if not d.is_dir():
        return
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".ani":
            head = p.read_bytes()[:4]
            if head[:4] == b"RIFF":
                continue
            yield p


# ---------------------------------------------------------------------------
# show / list / verify
# ---------------------------------------------------------------------------

def show(R: AssetRoot, logical: str, section: Optional[str],
         limit: int = 40) -> int:
    af, src = _load_ani(R, logical)
    print(f"{logical}  [{src}]  {len(af)} section(s), "
          f"{sum(s.listed_count for s in af)} frame reference(s)")
    if section is None:
        multi = [s for s in af if s.listed_count > 1]
        print(f"  {len(multi)} section(s) have more than one frame; "
              f"showing the first {min(limit, len(multi))}")
        for s in multi[:limit]:
            print(f"    [{s.name}]  FrameAmount={s.declared}  "
                  f"{s.listed_count} frame(s)"
                  f"{'' if s.is_consistent else '  *** INCONSISTENT ***'}")
        return 0

    seq = af.sequence(section)
    if seq is None:
        print(f"  no [{section}] in {logical}")
        return 1
    others = len(af.occurrences(section))
    print(f"  [{seq.name}]  FrameAmount={seq.declared}  "
          f"{seq.listed_count} frame(s) listed")
    if others > 1:
        print(f"  NOTE: this name is declared {others} times; the client "
              f"resolves the FIRST, which is the one shown")
    if not seq.is_consistent:
        print("  *** INCONSISTENT: FrameAmount and the Frame<i> rows disagree; "
              "see the ordinals below ***")
    absent = 0
    for i, f in enumerate(seq.client_frames()):
        if f is None:
            print(f"    {i:>4}  <no Frame{i} key -- the client draws nothing here>")
            absent += 1
            continue
        ok = _frame_present(R, f.logical)
        absent += 0 if ok else 1
        print(f"    {i:>4}  {'   ' if ok else '!! '}{f.value}"
              f"{'' if ok else '   DECLARED BUT ABSENT'}")
    extra = [f for f in seq.listed_frames()
             if seq.declared is not None and f.index >= seq.declared]
    for f in extra:
        print(f"    ({f.key})  {f.value}   LISTED BUT BEYOND FrameAmount"
              f"={seq.declared} -- the client never reaches it")
    if absent:
        print(f"  {absent} of {len(seq.client_frames())} ordinal(s) have no art "
              f"on this install")
    return 0


def verify(R: AssetRoot, logical: str, limit: int = 20) -> int:
    """Report every finding in one `.ani`. Exit 0 always -- this is a report.

    It does NOT fail on a finding, because the shipped files carry them: 25 to
    83 sections per install declare a `FrameAmount` that disagrees with their
    own rows. A gate that reddened on vanilla data would be turned off.
    """
    af, src = _load_ani(R, logical)
    fs = af.findings()
    print(f"{logical}  [{src}]  {len(af)} section(s), "
          f"{sum(s.listed_count for s in af)} frame reference(s), "
          f"{len(fs)} finding(s)")
    for f in fs[:limit]:
        print(f"    {f}")
    if len(fs) > limit:
        print(f"    ... and {len(fs) - limit} more")
    return 0


def list_files(root: Path) -> int:
    files = list(iter_ani(root))
    print(f"{len(files)} .ani frame manifest(s) under {root}")
    print(f"{'file':34} {'sections':>9} {'frames':>9} {'multi':>7} {'findings':>9}")
    tot_s = tot_f = tot_m = 0
    for p in files:
        af = anilib.AniFile.read(p)
        s = len(af)
        fr = sum(x.listed_count for x in af)
        m = sum(1 for x in af if x.listed_count > 1)
        tot_s += s
        tot_f += fr
        tot_m += m
        rel = p.relative_to(Path(root)).as_posix()
        print(f"{rel:34} {s:>9} {fr:>9} {m:>7} {len(af.findings()):>9}")
    print(f"{'TOTAL':34} {tot_s:>9} {tot_f:>9} {tot_m:>7}")
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export(R: AssetRoot, logical: str, section: str, out: Optional[Path] = None,
           png: bool = False) -> Path:
    r"""Write the whole ordered set to a work directory, and say what is absent.

    Layout::

        <out>/sequence.json
        <out>/frames/000_pic0000.dds
        <out>/frames/001_pic0001.dds
        <out>/frames/002_pic0002.absent      <- zero byte, declared but not shipped

    The ordinal prefix is the contract. `import_set` reads ordering from these
    filenames and from nothing else, so a user who reorders the animation
    renames the files and a user who edits it in place does not have to think
    about ordering at all.
    """
    af, src = _load_ani(R, logical)
    seq = af.sequence(section)
    if seq is None:
        raise SequenceError(f"no [{section}] in {logical}")
    dest = Path(out) if out else (WORK / "aniset" /
                                  safe_name(Path(logical).stem) /
                                  safe_name(seq.name))
    frames_dir = dest / FRAMES
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    ordered = seq.client_frames()
    width = max(3, len(str(max(0, len(ordered) - 1))))
    for i, f in enumerate(ordered):
        if f is None:
            name = f"{i:0{width}d}_no-Frame{i}-key.absent"
            (frames_dir / name).write_bytes(b"")
            rows.append({"ordinal": i, "declared": None, "present": False,
                         "reason": f"the section has no Frame{i} key",
                         "file": f"{FRAMES}/{name}", "sha256": None})
            continue
        slug = _slug(f.logical) or f"frame{i}"
        if _frame_present(R, f.logical):
            data = R.read(f.logical)
            name = f"{i:0{width}d}_{slug}.dds"
            (frames_dir / name).write_bytes(data)
            rows.append({"ordinal": i, "declared": f.value, "present": True,
                         "file": f"{FRAMES}/{name}", "sha256": _sha(data),
                         "bytes": len(data)})
            if png:
                _to_png(frames_dir / name)
        else:
            name = f"{i:0{width}d}_{slug}.absent"
            (frames_dir / name).write_bytes(b"")
            rows.append({"ordinal": i, "declared": f.value, "present": False,
                         "reason": "declared by the .ani, not shipped by this "
                                   "install (not loose, not in any archive)",
                         "file": f"{FRAMES}/{name}", "sha256": None})

    manifest = {
        "tool": "tools/aniset.py",
        "version": 1,
        "exportedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(R.root),
        "baseId": _base_id(R.root),
        "ani": logical,
        "aniSource": src,
        "section": seq.name,
        "occurrence": seq.occurrence,
        "occurrences": len(af.occurrences(seq.name)),
        "frameAmount": seq.declared,
        "listedCount": seq.listed_count,
        "consistent": seq.is_consistent,
        "ordinalWidth": width,
        "frames": rows,
        "ORDER IS THE ANIMATION": (
            "The NNN_ prefix on each file in frames/ is the frame ordinal and "
            "the only thing import reads ordering from. Renaming reorders the "
            "animation; deleting one leaves a gap that import REFUSES rather "
            "than closing. To remove a frame, run import with --drop-missing, "
            "which renumbers and tells you exactly what moved."),
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def _base_id(root) -> str:
    try:
        return coroot.base_id(root)
    except Exception:                      # noqa: BLE001
        return ""


def _to_png(dds: Path) -> Optional[Path]:
    try:
        from PIL import Image             # noqa: PLC0415
    except ImportError:
        return None
    try:
        im = Image.open(dds)
    except Exception:                      # noqa: BLE001
        return None
    out = dds.with_suffix(".png")
    im.convert("RGBA").save(out)
    return out


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def read_workdir(workdir: Path) -> tuple[dict, list[tuple[int, Path]]]:
    """Manifest plus the ordered frame files, or a refusal naming the problem.

    The ordinals must be exactly ``0..N-1``. A gap, a duplicate or a
    non-numeric name is an error: closing a gap silently is the renumber this
    whole module exists to prevent.
    """
    workdir = Path(workdir)
    mf = workdir / MANIFEST
    if not mf.is_file():
        raise SequenceError(f"{workdir} has no {MANIFEST}; "
                            f"it was not produced by `aniset export`")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    fdir = workdir / FRAMES
    if not fdir.is_dir():
        raise SequenceError(f"{workdir} has no {FRAMES}/ directory")

    found: dict[int, list[Path]] = {}
    junk: list[str] = []
    for p in sorted(fdir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() == ".png" and p.with_suffix(".dds").is_file():
            continue          # a .png beside its own .dds is the export's copy
        m = _ORDINAL.match(p.stem)
        if not m:
            junk.append(p.name)
            continue
        found.setdefault(int(m.group(1)), []).append(p)
    if junk:
        raise SequenceError(
            f"{len(junk)} file(s) in {fdir} do not start with a frame ordinal: "
            f"{', '.join(junk[:5])}"
            f"{' ...' if len(junk) > 5 else ''}. Every frame must be named "
            f"NNN_something -- the ordinal IS the animation order, and a file "
            f"without one has no position.")
    dupes = {k: v for k, v in found.items() if len(v) > 1}
    if dupes:
        k = sorted(dupes)[0]
        raise SequenceError(
            f"ordinal {k} is claimed by {len(dupes[k])} files "
            f"({', '.join(p.name for p in dupes[k])}). Two frames cannot "
            f"occupy one position; nothing was staged.")
    if not found:
        raise SequenceError(
            f"{fdir} holds no frames. An empty set would write FrameAmount=0 "
            f"and silently blank the animation; refusing instead. Delete the "
            f"work directory if that is what you meant.")
    want = list(range(len(found)))
    if sorted(found) != want:
        missing = [i for i in want if i not in found]
        extra = [i for i in sorted(found) if i >= len(found)]
        raise SequenceError(
            f"the frame ordinals are {sorted(found)}, not 0..{len(found) - 1}. "
            f"Missing: {missing or 'none'}. Beyond the end: {extra or 'none'}. "
            f"Closing that gap would renumber every later frame and change the "
            f"animation, so nothing was staged. Renumber the files, or use "
            f"--drop-missing to remove the absent ordinals deliberately.")
    return manifest, [(i, found[i][0]) for i in want]


def import_set(workdir: Path, R: AssetRoot, drop_missing: bool = False,
               dry_run: bool = False) -> tuple[int, list[str]]:
    """Stage the edited set: the frames, then the rewritten `.ani`.

    Returns ``(exit_code, report_lines)``. Nothing is written on a refusal.
    """
    manifest, ordered = read_workdir(Path(workdir))
    logical = manifest["ani"]
    section = manifest["section"]
    lines: list[str] = []

    af, src = _load_ani(R, logical)
    seq = af.sequence(section)
    if seq is None:
        raise SequenceError(
            f"[{section}] is no longer in {logical} (read from {src}); "
            f"the export is stale")

    # A frame's IDENTITY is its slug -- the exported filename after the
    # ordinal. Matching on that and not on the ordinal is what makes a rename
    # mean "this frame now plays at position N" instead of "move this image
    # onto whatever path position N used to name". The second reading writes
    # texture bytes onto paths other sequences share; the first does not touch
    # a texture at all. See `_slug` for why the basename alone will not do.
    by_slug: dict[str, dict] = {}
    for r in manifest.get("frames", []):
        m = _ORDINAL.match(Path(str(r["file"])).stem)
        if m and m.group(2):
            by_slug.setdefault(m.group(2).lower(), r)
    default_dir = ""
    for r in manifest.get("frames", []):
        if r.get("declared"):
            default_dir = str(Path(str(r["declared"]).replace("\\", "/")).parent)
            break

    values: list[str] = []
    stage_ops: list[tuple[Path, str]] = []      # (source file, logical dest)
    dropped: list[str] = []
    added: list[str] = []
    for i, p in ordered:
        slug = (_ORDINAL.match(p.stem).group(2) or "").lower()
        rec = by_slug.get(slug, {})
        declared = rec.get("declared")
        if p.suffix.lower() == ".absent":
            if drop_missing:
                dropped.append(f"ordinal {i} ({declared or 'no key'})")
                continue
            if not declared:
                raise SequenceError(
                    f"{p.name} is an .absent marker for a slot the section "
                    f"never had a Frame key for, so there is no path to keep. "
                    f"Re-run with --drop-missing to remove the slot, or put a "
                    f"real frame at that ordinal.")
            values.append(declared)
            lines.append(f"  {i:>4}  KEPT AS DECLARED (no art on this install): "
                         f"{declared}")
            continue
        if declared:
            dest_logical = declared
            # Only stage bytes that actually changed. An unchanged frame must
            # not be rewritten: `comod diff` would then report every frame of
            # the sequence as modified, and a DDS re-encode is lossy, so a
            # no-op round trip that rewrote them would degrade the art.
            if _sha(p.read_bytes()) != rec.get("sha256"):
                stage_ops.append((p, dest_logical))
        else:
            if not default_dir:
                raise SequenceError(
                    f"{p.name} is a new frame and the section declares no "
                    f"existing frame to take a directory from; there is "
                    f"nothing to derive a path from. Stage the .dds yourself "
                    f"and add the row by hand.")
            dest_logical = f"{default_dir}/{slug or f'frame{i}'}.dds"
            added.append(f"ordinal {i} -> {dest_logical}")
            stage_ops.append((p, dest_logical))
        values.append(dest_logical)

    if not values:
        raise SequenceError(
            "every ordinal was dropped; that would write FrameAmount=0 and "
            "blank the animation. Refusing.")

    # Two ordinals can legitimately name the SAME texture (a ping-pong loop
    # does it by design). Two ordinals staging DIFFERENT bytes onto that one
    # texture cannot both be honoured, and picking one silently would change
    # the frame the user did not edit -- and every other sequence that shares
    # the file.
    seen: dict[str, tuple[Path, str]] = {}
    for src_file, dest_logical in stage_ops:
        digest = _sha(src_file.read_bytes())
        prev = seen.get(dest_logical)
        if prev and prev[1] != digest:
            raise SequenceError(
                f"{prev[0].name} and {src_file.name} both write "
                f"{dest_logical} and their contents differ. That path is one "
                f"file the sequence plays more than once (and other sequences "
                f"may share it), so it cannot hold both. Make them identical, "
                f"or point one ordinal at a new file. Nothing was staged.")
        seen[dest_logical] = (src_file, digest)

    # -- the whole point: FrameAmount and the rows come from ONE list --------
    seq2 = af.rewrite_frames(seq, values)
    assert seq2.declared == len(values), "rewrite_frames broke its own contract"
    assert [f.value for f in seq2.listed_frames()] == values

    lines.insert(0, f"[{section}] in {logical}: "
                    f"{seq.listed_count} frame(s) -> {len(values)}, "
                    f"FrameAmount {seq.declared} -> {seq2.declared}")
    for i, v in enumerate(values):
        was = None
        keyed = seq.by_key().get(i)
        was = keyed.value if keyed else None
        mark = "  " if was == v else "* "
        lines.append(f"  {mark}Frame{i}={v}"
                     + ("" if was == v else f"      (was {was or '<none>'})"))
    for d in dropped:
        lines.append(f"  DROPPED {d} -- every later frame moved up one")
    for d in added:
        lines.append(f"  ADDED {d}")
    if dropped and not drop_missing:        # unreachable; kept as a tripwire
        raise AssertionError("frames were dropped without --drop-missing")
    lines.append(f"  {len(stage_ops)} texture(s) changed; "
                 f"{len(values) - len(stage_ops)} unchanged and left alone")
    # A texture played at more than one ordinal is normal -- a ping-pong loop
    # is built that way -- but editing it changes EVERY ordinal that names it,
    # and any other sequence that does. Silence here would let a user believe
    # they changed one frame.
    for src_file, dest_logical in stage_ops:
        at = [i for i, v in enumerate(values) if v == dest_logical]
        if len(at) > 1:
            lines.append(f"  NOTE {dest_logical} is played at ordinals "
                         f"{at} -- this edit changes all of them, and any "
                         f"other sequence that references the same file")

    if dry_run:
        lines.append("dry run: nothing written")
        return 0, lines

    for src_file, dest_logical in stage_ops:
        dest = safepath.confine(STAGE, dest_logical)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_file.suffix.lower() == ".png":
            _from_png(src_file, dest, R, dest_logical)
        else:
            shutil.copyfile(src_file, dest)
        lines.append(f"  staged {dest_logical}")
    ani_dest = safepath.confine(STAGE, logical)
    ani_dest.parent.mkdir(parents=True, exist_ok=True)
    ani_dest.write_bytes(af.dumpb())
    lines.append(f"  staged {logical}   ({len(af.dumpb())} bytes)")
    lines.append("run `comod.py diff` to review, `comod.py install` to apply.")
    return 0, lines


def _from_png(src: Path, dest: Path, R: AssetRoot, logical: str) -> None:
    try:
        from PIL import Image             # noqa: PLC0415
    except ImportError as e:
        raise SequenceError(
            f"{src.name} is a PNG and Pillow is not installed, so it cannot be "
            f"encoded to DDS: {e}") from e
    fmt = "DXT3"
    try:
        if R.locate(logical):
            info = dds_info(R.read(logical))
            if info and info.fourcc:
                fmt = info.fourcc
    except Exception:                      # noqa: BLE001
        pass
    Image.open(src).convert("RGBA").save(dest, format="DDS", pixel_format=fmt)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _root(a) -> Path:
    if a.root:
        return Path(a.root)
    with AssetRoot() as probe:
        return Path(probe.root)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="an .ani sequence handled as a sequence",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="client install root")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="every .ani frame manifest on a root")
    p = sub.add_parser("show", help="one sequence, in order, with absences")
    p.add_argument("ani")
    p.add_argument("section", nargs="?")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("verify", help="report a file's findings")
    p.add_argument("ani")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("export", help="write the ordered set to a work dir")
    p.add_argument("ani")
    p.add_argument("section")
    p.add_argument("--out")
    p.add_argument("--png", action="store_true", help="also decode each .dds")
    p = sub.add_parser("import", help="re-import an edited set and stage it")
    p.add_argument("workdir")
    p.add_argument("--drop-missing", dest="drop_missing", action="store_true",
                   help="remove absent ordinals and renumber; the report says "
                        "exactly what moved")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            return list_files(_root(a))
        with AssetRoot(a.root) as R:
            if a.cmd == "show":
                return show(R, a.ani, a.section, a.limit)
            if a.cmd == "verify":
                return verify(R, a.ani, a.limit)
            if a.cmd == "export":
                d = export(R, a.ani, a.section, a.out, a.png)
                n = len(json.loads((d / MANIFEST).read_text("utf-8"))["frames"])
                print(f"exported {n} ordinal(s) -> {d}")
                print(f"  ordering lives in the NNN_ prefixes; "
                      f"re-import with:  py -3 tools/aniset.py import {d}")
                return 0
            if a.cmd == "import":
                code, lines = import_set(Path(a.workdir), R, a.drop_missing,
                                         a.dry_run)
                for ln in lines:
                    print(ln)
                return code
    except SequenceError as e:
        print(f"REFUSED: {e}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
