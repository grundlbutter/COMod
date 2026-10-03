#!/usr/bin/env python3
r"""
ani.py -- an `.ani` frame manifest as an ORDERED SEQUENCE, readable and writable.

WHAT AN `.ani` IS
-----------------
Not a sprite sheet. It is plain INI-flavoured text naming an ordered set of
separate `.dds` frames per sequence::

    [Puzzle0]
    FrameAmount=1
    Frame0=data/map/puzzle/archer/bg/01/pic0000.dds

Nothing here needs a binary parser. The value this module adds is that a
sequence is handled AS a sequence -- the frames keep their order, the declared
`FrameAmount` is checked against the frames actually present, and a rewrite
renumbers `Frame0..N-1` and `FrameAmount` together so the two cannot drift.

THE FAILURE THIS MODULE EXISTS TO PREVENT
-----------------------------------------
**The order is the animation.** A frame list that loses its order, or a
renumber inconsistent with `FrameAmount`, yields a manifest the client reads as
a *different* animation, not a broken one. There is no crash, no error, no
missing-file dialog -- the art simply plays wrong, and nothing says so. Every
check here exists because the wrong answer is silent.

TWO READ MODELS, AND THEY DISAGREE ON REAL SHIPPED FILES
--------------------------------------------------------
The client reads its inis through the Win32 profile API, which resolves a key
**by name** and returns the **first** occurrence (verified for section lookup
in `tools/itemart.py` by calling `GetPrivateProfileString` on
``ItemMinIcon.Ani``: ``[Item121223]`` yields the first body, not the last).
With `FrameAmount=N` present, the natural read is therefore ``Frame0`` through
``Frame<N-1>``, looked up by name. That is `Sequence.client_frames`.

The other model -- collect every ``Frame<i>`` key the section carries and sort
by `i`, ignoring `FrameAmount` -- is what `dmap.read_ani` has always done, and
it is the right model for "what art does this map reference", which is what
that reader is for. That is `Sequence.listed_frames`.

MEASURED (2026-09-07, `ani/**/*.ani` on four vanilla installs, the two models
compared section by section)::

    client  sections  FrameAmount != listed count  index gap / duplicate key
    5517     117,925                           25                         0
    6609     223,541                           24                         0
    7205     259,135                           26                         0
    7878     484,377                           83                        43

So the disagreement is real but rare: about 1 section in 5,000. **A validator
that refuses disagreement would refuse the vanilla files themselves**, which is
why `findings()` reports and `rewrite` repairs, and neither raises on the
shipped state. The 43 on 7878 are duplicate ``Frame0=`` keys and lone
``Frame10=`` keys in sections declaring ``FrameAmount=1``; on the client model
the lone-``Frame10`` sections resolve to nothing at all.

BYTE FIDELITY
-------------
`AniFile.dumps()` returns the input byte-for-byte when nothing was changed, and
a rewrite touches only the lines of the one section it was asked to change.
That is not neatness: these files carry duplicate section names in bulk
(**70,190 repeated ``(file, section)`` pairs on 7878**), and first-occurrence
wins, so a reformatting writer that reordered or merged them would change art
in sections nobody asked about.

Line terminators are preserved per line, and a rewritten row copies the
terminator and the trailing whitespace **of the row it replaces**, falling back
to the file's dominant terminator only when the section has no row to copy
from. That is three separate lessons paid for in the 2026-09-07 sweep, each of
which had made a rewrite differ from its own unchanged input:

* ``ani/bigalopolis.ani`` on 7205 and 7878 is LF-only, so "just write CRLF"
  would rewrite whole files that were never edited;
* ``6609/ani/TY.ani`` is genuinely MIXED -- 1,881 CRLF against 2,155 LF -- and
  its ``[Puzzle365]`` is LF inside a CRLF-dominant file, so even the file's
  dominant terminator is the wrong answer for one of its own sections;
* three files (5517 ``chat.ANI``, 6609 ``cartoon.ani``, 7878 ``chat.ANI``) end
  with NO final terminator, and their last section is exactly the one a rewrite
  of it touches.

MEASURED with that in place: **2,860 shipped sections across 5517 / 6609 / 7205
/ 7878 rewritten with their own frame lists -- 2,860 byte-identical, 0 losing
their order, 0 disagreeing with `FrameAmount`.**

Encoding is latin-1 with `errors="replace"` on read and latin-1 on write:
these files carry GBK comment text (``// 新建角色选择窗口``) that is not valid
UTF-8, and latin-1 is the only codec that round-trips arbitrary bytes 1:1.
No shipped file carries a BOM (0 of 292 across the four installs).

CLI::

    py -3 core/ani.py <file.ani>                    # summary + findings
    py -3 core/ani.py <file.ani> --section Puzzle0  # one sequence, in order
    py -3 core/ani.py <file.ani> --json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

__all__ = [
    "Frame", "Sequence", "AniFile", "Finding",
    "parse", "load", "normalize_logical",
]

#: A section header. Leading whitespace is tolerated; the shipped files do not
#: use it, but `GetPrivateProfileString` accepts it and refusing would be a
#: stricter reader than the client.
_SEC = re.compile(r"^\s*\[([^\]\r\n]*)\]\s*$")
#: `Key = value`. The value keeps its inner spacing; only the ends are trimmed.
_KV = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)[ \t]*$")
#: `Frame<digits>` and nothing else. `FrameAmount` must NOT match this.
_FRAMEKEY = re.compile(r"^[Ff][Rr][Aa][Mm][Ee](\d+)$")
_AMOUNTKEY = "frameamount"

#: Split a text into lines that KEEP their terminators, so join is exact.
_LINES = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")


def _split_lines(text: str) -> list[str]:
    out = [m.group(0) for m in _LINES.finditer(text)]
    # `finditer` yields one final empty match at the end of the string; a text
    # that ends with a terminator would otherwise gain a phantom empty line and
    # `dumps()` would still be byte-exact, but `len(lines)` would lie.
    while out and out[-1] == "":
        out.pop()
    return out


def _eol(line: str) -> str:
    for t in ("\r\n", "\n", "\r"):
        if line.endswith(t):
            return t
    return ""


def normalize_logical(value: str) -> str:
    r"""The comparable form of a frame reference.

    Backslashes to forward, no leading separator, lowercased. This is what
    `dmap.read_ani` has always returned and what `coassets.AssetRoot` resolves
    either casing of, so two installs that spell the same frame differently
    still compare equal. **The raw spelling is kept separately** -- a rewrite
    must not silently lowercase a path the file spelled in mixed case.
    """
    return str(value).replace("\\", "/").lstrip("/").lower()


@dataclass(frozen=True)
class Frame:
    """One ``Frame<i>=<path>`` row, as written."""

    index: int
    value: str          #: exactly as the file spells it
    key: str            #: exactly as the file spells it, e.g. "Frame0" or "FRAME2"
    line: int           #: 0-based index into `AniFile.lines`

    @property
    def logical(self) -> str:
        return normalize_logical(self.value)


@dataclass
class Sequence:
    """One ``[Section]`` and its ordered frames."""

    name: str
    header_line: int              #: 0-based index of the `[Name]` line
    end_line: int                 #: exclusive; the next header, or EOF
    occurrence: int               #: 0 for the first section with this name
    declared: Optional[int] = None        #: FrameAmount, None when absent
    declared_line: Optional[int] = None
    frames: list[Frame] = field(default_factory=list)   #: in FILE order

    # -- the two read models -------------------------------------------------

    def by_key(self) -> dict[int, Frame]:
        """``{i: Frame}`` with **first occurrence winning** per key.

        A duplicate ``Frame0=`` is not hypothetical: 7878 ships them (see the
        module docstring), and the Win32 profile API the client reads with
        returns the first. Keeping the last would silently disagree.
        """
        out: dict[int, Frame] = {}
        for f in self.frames:
            out.setdefault(f.index, f)
        return out

    def client_frames(self) -> list[Optional[Frame]]:
        """``Frame0..Frame<declared-1>`` looked up BY NAME; `None` where absent.

        This is the client's model. `None` entries are the honest answer for a
        section that declares more frames than it lists -- the slot exists in
        the animation and has no art. With no `FrameAmount` at all, falls back
        to `listed_frames`, because there is then no declared length to walk.
        """
        if self.declared is None:
            return list(self.listed_frames())
        keyed = self.by_key()
        return [keyed.get(i) for i in range(max(0, self.declared))]

    def listed_frames(self) -> list[Frame]:
        """Every ``Frame<i>`` the section carries, ordered by `i`, first wins.

        `FrameAmount` is ignored. This is `dmap.read_ani`'s model and the right
        one for "what art does this reference"; it is NOT what the client
        plays when the two disagree.
        """
        keyed = self.by_key()
        return [keyed[i] for i in sorted(keyed)]

    # -- agreement -----------------------------------------------------------

    @property
    def listed_count(self) -> int:
        return len(self.by_key())

    @property
    def declared_agrees(self) -> bool:
        """`FrameAmount` equals the number of distinct ``Frame<i>`` keys."""
        return self.declared is not None and self.declared == self.listed_count

    @property
    def contiguous(self) -> bool:
        """The keys are exactly ``Frame0..Frame<n-1>`` with no gap and no repeat."""
        keyed = self.by_key()
        return len(self.frames) == len(keyed) and \
            sorted(keyed) == list(range(len(keyed)))

    @property
    def is_consistent(self) -> bool:
        """Contiguous from 0 **and** `FrameAmount` agrees. The healthy shape."""
        return self.contiguous and self.declared_agrees

    def __len__(self) -> int:
        return self.listed_count


@dataclass(frozen=True)
class Finding:
    """One thing a caller should be told about a sequence. Never raised."""

    kind: str        #: "count-mismatch" | "index-gap" | "duplicate-key" |
                     #: "duplicate-section" | "no-frameamount" | "empty"
    section: str
    occurrence: int
    detail: str

    def __str__(self) -> str:
        occ = "" if self.occurrence == 0 else f"#{self.occurrence + 1}"
        return f"[{self.section}]{occ}: {self.kind}: {self.detail}"


class AniFile:
    """A parsed `.ani`, byte-faithful and rewritable.

    `lines` keeps every line **with its terminator**, so ``dumps()`` is exact.
    Sequences index into `lines`; any mutation goes through `rewrite_frames`,
    which repairs the indices of the sequences after the edit.
    """

    def __init__(self, text: str, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else None
        self.lines = _split_lines(text)
        self.sequences: list[Sequence] = []
        self._parse()

    # -- construction --------------------------------------------------------

    @classmethod
    def read(cls, path) -> "AniFile":
        p = Path(path)
        return cls(p.read_bytes().decode("latin-1", errors="replace"), p)

    def _parse(self) -> None:
        seen: dict[str, int] = {}
        cur: Optional[Sequence] = None
        for i, raw in enumerate(self.lines):
            line = raw.rstrip("\r\n")
            m = _SEC.match(line)
            if m:
                if cur is not None:
                    cur.end_line = i
                name = m.group(1).strip()
                occ = seen.get(name.lower(), 0)
                seen[name.lower()] = occ + 1
                cur = Sequence(name=name, header_line=i, end_line=len(self.lines),
                               occurrence=occ)
                self.sequences.append(cur)
                continue
            if cur is None:
                continue    # preamble: comments, stray keys. Kept, never parsed.
            kv = _KV.match(line)
            if not kv:
                continue
            key, val = kv.group(1), kv.group(2)
            if key.lower() == _AMOUNTKEY:
                if cur.declared is None:        # first wins, as the client reads
                    try:
                        cur.declared = int(val.strip())
                    except ValueError:
                        cur.declared = None
                    else:
                        cur.declared_line = i
                continue
            fm = _FRAMEKEY.match(key)
            if fm:
                cur.frames.append(Frame(index=int(fm.group(1)), value=val,
                                        key=key, line=i))
        if cur is not None:
            cur.end_line = len(self.lines)

    # -- lookup --------------------------------------------------------------

    def __iter__(self) -> Iterator[Sequence]:
        return iter(self.sequences)

    def __len__(self) -> int:
        return len(self.sequences)

    def occurrences(self, name: str) -> list[Sequence]:
        """Every section with this name, in file order. Case-insensitive."""
        k = name.strip().lower()
        return [s for s in self.sequences if s.name.lower() == k]

    def sequence(self, name: str) -> Optional[Sequence]:
        """The section the CLIENT would resolve: the FIRST with this name."""
        got = self.occurrences(name)
        return got[0] if got else None

    def names(self) -> list[str]:
        return [s.name for s in self.sequences]

    # -- reporting -----------------------------------------------------------

    def findings(self) -> list[Finding]:
        """Everything worth telling a user. Reports; never raises, never fixes.

        The shipped files trip these, so a caller must be able to see the state
        of a vanilla install without being refused.
        """
        out: list[Finding] = []
        counts: dict[str, int] = {}
        for s in self.sequences:
            counts[s.name.lower()] = counts.get(s.name.lower(), 0) + 1
        for s in self.sequences:
            if s.declared is None:
                out.append(Finding("no-frameamount", s.name, s.occurrence,
                                   f"no FrameAmount; {s.listed_count} frame(s) listed"))
            elif not s.declared_agrees:
                out.append(Finding(
                    "count-mismatch", s.name, s.occurrence,
                    f"FrameAmount={s.declared} but {s.listed_count} distinct "
                    f"Frame<i> key(s) present"))
            keyed = s.by_key()
            if len(s.frames) != len(keyed):
                dupes = sorted({f.index for f in s.frames} &
                               {f.index for f in s.frames[len(keyed):]}) or \
                    sorted({f.index for f in s.frames
                            if sum(1 for g in s.frames if g.index == f.index) > 1})
                out.append(Finding(
                    "duplicate-key", s.name, s.occurrence,
                    f"Frame{dupes[0]} appears more than once; the first wins"
                    if dupes else "a Frame<i> key appears more than once"))
            if keyed and sorted(keyed) != list(range(len(keyed))):
                out.append(Finding(
                    "index-gap", s.name, s.occurrence,
                    f"frame indices are {sorted(keyed)}, not 0..{len(keyed) - 1}"))
            if not keyed:
                out.append(Finding("empty", s.name, s.occurrence,
                                   "no Frame<i> rows"))
        for name, n in counts.items():
            if n > 1:
                first = self.occurrences(name)[0]
                out.append(Finding(
                    "duplicate-section", first.name, 0,
                    f"{n} sections share this name; the client resolves the first"))
        return out

    # -- writing -------------------------------------------------------------

    def dominant_eol(self) -> str:
        """The terminator most of this file uses. CRLF when it has no lines."""
        n = {"\r\n": 0, "\n": 0, "\r": 0}
        for ln in self.lines:
            t = _eol(ln)
            if t:
                n[t] += 1
        best = max(n, key=lambda k: n[k])
        return best if n[best] else "\r\n"

    def dumps(self) -> str:
        return "".join(self.lines)

    def dumpb(self) -> bytes:
        return self.dumps().encode("latin-1", errors="replace")

    def rewrite_frames(self, seq: Sequence, values: list[str]) -> Sequence:
        r"""Replace one sequence's frames with `values`, IN THE GIVEN ORDER.

        Writes ``FrameAmount=len(values)`` and ``Frame0..Frame<len-1>`` -- the
        two are set from the same list in the same call, which is the whole
        point: they cannot be renumbered inconsistently because there is no
        code path that sets one without the other.

        Everything outside this section is untouched, and inside it every line
        that is neither `FrameAmount` nor `Frame<i>` keeps its position
        relative to the block. New rows take the file's dominant terminator.

        The rewritten `Sequence` is returned; other sequences' line indices are
        repaired in place, so the `AniFile` stays usable afterwards.

        An empty `values` is allowed and writes ``FrameAmount=0`` with no rows:
        that is a real state in the shipped files (7878's ``Xinshop_zengpin``),
        and refusing it would make this writer unable to reproduce vanilla.
        """
        # IDENTITY, not equality. `Sequence` is a plain dataclass, so a
        # sequence parsed from an identical text compares EQUAL to this file's
        # own -- and its line indices would then point into the wrong list. A
        # `in` test here silently corrupted whichever file it was handed.
        if not any(s is seq for s in self.sequences):
            raise ValueError(f"[{seq.name}] does not belong to this AniFile")
        eol = self.dominant_eol()
        frame_lines = sorted({f.line for f in seq.frames})
        # Where the block goes: where the first frame row was, else right after
        # FrameAmount, else right after the header.
        if frame_lines:
            insert_at = frame_lines[0]
        elif seq.declared_line is not None:
            insert_at = seq.declared_line + 1
        else:
            insert_at = seq.header_line + 1

        # Trailing whitespace before the terminator is preserved from the rows
        # being replaced. It is semantically nothing -- the profile reader
        # trims it -- but 24 of 2,860 shipped sections rewritten in the
        # 2026-09-07 sweep differed from their own input by exactly this, and
        # a writer that is byte-exact on unchanged input is a far stronger
        # test property than one that is "equivalent". `Common.Ani`'s
        # ``[ChargeUp]`` on 7878 writes ``FrameAmount=2 `` with a trailing
        # space; so does every row under it.
        def _pad(i: Optional[int]) -> str:
            if i is None:
                return ""
            body = self.lines[i].rstrip("\r\n")
            return body[len(body.rstrip(" \t")):]

        # The terminator comes from the ROW BEING REPLACED, not from the file:
        # 6609's `ani/TY.ani` is genuinely mixed (1,881 CRLF against 2,155 LF)
        # and its `[Puzzle365]` is LF inside a CRLF-dominant file. Taking the
        # file's dominant terminator rewrote that section's line endings.
        # `eol` is the fallback for a section that has no row to copy from.
        def _rowend(i: Optional[int]) -> str:
            return _eol(self.lines[i]) or eol if i is not None else eol

        fpad = _pad(frame_lines[0] if frame_lines else None)
        apad = _pad(seq.declared_line)
        feol = _rowend(frame_lines[0] if frame_lines else seq.declared_line)
        aeol = _rowend(seq.declared_line if seq.declared_line is not None
                       else (frame_lines[0] if frame_lines else None))
        new_rows = [f"Frame{i}={v}{fpad}{feol}" for i, v in enumerate(values)]
        drop = set(frame_lines)

        out: list[str] = []
        placed = False
        amount_written = False
        for i, ln in enumerate(self.lines):
            if i == insert_at and not placed:
                out.extend(new_rows)
                placed = True
            if i in drop:
                continue
            if i == seq.declared_line:
                out.append(f"FrameAmount={len(values)}{apad}{aeol}")
                amount_written = True
                continue
            out.append(ln)
        if not placed:                      # insert_at == len(lines)
            out.extend(new_rows)
        if not amount_written:
            # No FrameAmount row existed. Put one immediately after the header,
            # which is where every shipped section puts it.
            at = seq.header_line + 1
            # `at` is an index into the ORIGINAL lines; translate by counting
            # how many originals before it survived.
            kept = sum(1 for i in range(at) if i not in drop)
            kept += len(new_rows) if insert_at < at else 0
            out.insert(kept, f"FrameAmount={len(values)}{apad}{aeol}")

        # A file whose last line carries no terminator must not gain one: three
        # shipped files end that way (5517 `chat.ANI`, 6609 `cartoon.ani`,
        # 7878 `chat.ANI`), their last section is the one a rewrite of it
        # touches, and a two-byte tail is the difference between "byte
        # identical" and "close enough".
        if out and self.lines and not _eol(self.lines[-1]):
            t = _eol(out[-1])
            if t:
                out[-1] = out[-1][:-len(t)]

        # A header line must survive intact -- assert rather than trust, because
        # a lost header silently merges two sequences into one animation.
        self.lines = out
        old_names = [s.name for s in self.sequences]
        self.sequences = []
        self._parse()
        if [s.name for s in self.sequences] != old_names:
            raise AssertionError(
                "rewrite changed the section list: "
                f"{old_names} -> {[s.name for s in self.sequences]}")
        fresh = self.occurrences(seq.name)[seq.occurrence]
        got = [f.value for f in fresh.listed_frames()]
        if got != list(values):
            raise AssertionError(
                f"rewrite did not round-trip [{seq.name}]: wrote {values!r}, "
                f"read back {got!r}")
        if fresh.declared != len(values):
            raise AssertionError(
                f"rewrite left FrameAmount={fresh.declared} against "
                f"{len(values)} frames in [{seq.name}]")
        return fresh


# ---------------------------------------------------------------------------
# module-level conveniences
# ---------------------------------------------------------------------------

def parse(text: str, path=None) -> AniFile:
    return AniFile(text, path)


def load(path) -> AniFile:
    return AniFile.read(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--section", help="show one sequence, in order")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)

    p = Path(a.path)
    if not p.is_file():
        print(f"no such file: {p}")
        return 1
    af = AniFile.read(p)

    if a.section:
        seq = af.sequence(a.section)
        if seq is None:
            print(f"no [{a.section}] in {p.name}")
            return 1
        if a.json:
            print(json.dumps({
                "section": seq.name,
                "frameAmount": seq.declared,
                "listed": [f.value for f in seq.listed_frames()],
                "client": [None if f is None else f.value
                           for f in seq.client_frames()],
                "consistent": seq.is_consistent,
            }, indent=2))
            return 0
        print(f"[{seq.name}]  FrameAmount={seq.declared}  "
              f"{seq.listed_count} frame(s) listed"
              f"{'' if seq.is_consistent else '   *** INCONSISTENT ***'}")
        for i, f in enumerate(seq.client_frames()):
            print(f"  {i:>4}  {'<absent key>' if f is None else f.value}")
        return 0

    findings = af.findings()
    if a.json:
        print(json.dumps({
            "file": str(p),
            "sections": len(af),
            "frames": sum(s.listed_count for s in af),
            "findings": [f.__dict__ for f in findings],
        }, indent=2))
        return 0
    print(f"{p.name}: {len(af)} section(s), "
          f"{sum(s.listed_count for s in af)} frame reference(s), "
          f"{sum(1 for s in af if s.listed_count > 1)} with more than one frame")
    if findings:
        print(f"  {len(findings)} finding(s):")
        for f in findings[:a.limit]:
            print(f"    {f}")
        if len(findings) > a.limit:
            print(f"    ... and {len(findings) - a.limit} more")
    else:
        print("  no findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
