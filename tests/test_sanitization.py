#!/usr/bin/env python3
r"""
test_sanitization.py -- the repository must not carry anybody's personal data,
and must not hardcode anybody's machine.

    py -3 tests/test_sanitization.py           # run the checks
    py -3 tests/test_sanitization.py -v        # list every file scanned
    py -3 tests/test_sanitization.py --learn "C:\path\to\login_history.json"
                                               # print hashes for a new list

Three checks, over every file git would publish (tracked + untracked-but-not-
ignored), excluding `refs/`, which is vendored third-party material:

  1. **No personal identifiers.**  Account names and in-game character names
     that appeared in live recon sessions, plus the original author's name,
     email and machine name.
  2. **No absolute user paths.**  `C:\Users\<someone>\...` anywhere means the
     repo only runs on the machine it was written on.
  3. **No hardcoded install path, of any kind, on any drive.**  A finding is
     a *drive-rooted absolute path* one of whose segments names something this
     project **resolves at runtime** -- an install archive, an install
     directory name, or one of `coroot.RESOLVED_TREE_NAMES`
     (``ConquerAssets``, ``Clients``, ``COmmunityLibrary``, ``Installed``).
     The vocabulary is imported from `core/coroot.py`, never copied.

     Scope is *executable code*: python string literals that are not
     docstrings, plus the whole text of non-prose files (`.ps1`, `.cmd`,
     `.html`, ...).  Docstrings, comments and `.md`/`.txt`/`.rst` are
     deliberately out -- this repo records its measurements with real paths in
     prose and a check that reddens them gets neutered.  `core/coroot.py` is
     the one file allowed a literal, because validating a discovery hint is
     its job.  See the long note above `ABS_PATH_RE` for what this used to be,
     why one vendor prefix was not a check, and what is still not covered.

## Why the banned list is hashed

Writing the identifiers into this file would put back exactly what was taken
out.  Instead the list is stored as truncated SHA-256 of the lowercased
identifier, and the scanner hashes what it finds.  The list is regenerable
from the machine owner's `login_history.json` with `--learn`; that file is
PII and is never copied into the repo.

## Why two matching modes

A naive substring scan is useless here: one of the removed names is a
substring of the words "login" and "logic", and matching it that way flagged
~120 files that were all false positives.  So:

  * short or ambiguous identifiers are matched as **whole tokens** -- runs of
    `[A-Za-z0-9]` compared entire, case-insensitively, so a four-letter name
    can never match inside a longer ordinary word that happens to start with
    the same four letters;
  * long or multi-part identifiers (an email, a machine name, a home
    directory, a 14-character character name) are matched against the file
    **squashed** to `[a-z0-9]`, which survives whatever punctuation or
    separator they were written with.

Adding an identifier: run `--learn`, paste the two sets below.

## Why the scan also reads DECODED views of every file

Both modes above read the file as text, and **a real captured frame is not
text.** `capture/fixtures/*.jsonl` stores payloads as
`"head": "20000170584b5154..."` -- so an account or character name that is
genuinely present, byte for byte, appears as `616c696365` and the token scan
walks straight past it. The gate then reports **clean** on a file that carries
real traffic from a real account. Measured by CCO Plugin: their opcode fixtures
were caught twice when the name appeared as a plain token, and not at all when
the same name sat beside it in hex.

That is a guard that is safe by convention -- *nobody hex-encodes PII on
purpose* -- rather than by construction, and it is the same family as the
attach gate and `companion._static` (`tests/test_boundary_guards.py`).

So every file is matched **as text and again through each decoded view**:

| view | what it recovers |
|---|---|
| `hex` | contiguous hex runs, decoded at **both** byte alignments |
| `hex-separated` | `61 6c 69 63 65`, `\x61\x6c`, `0x61,0x6c` |
| `base64` | standard and url-safe |
| `wide` | the above with NUL bytes dropped -- a UTF-16LE name in a memory capture decodes to `a\0l\0i\0c\0e`, which the token matcher would otherwise split into five one-letter tokens |

**The identifiers never have to be known in plaintext for this to work** — the
file is decoded and the *result* is hashed, so the hash-only design is
preserved exactly.

**The fix is in the instrument, not the data.** The captured fixtures stay real:
a self-built one already decoded to the wrong uid once, which is why they are
real now. A scan that cannot see a real fixture is the thing to change.

Not covered, and named rather than left implied: decimal byte lists
(`[97, 108, ...]`), compressed or encrypted payloads, and any encoding that is
not one of the four above. `--views` prints how much each view decoded, because
**a view that decoded nothing has not checked anything.**
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The same path, and deliberately a SECOND name for it.  `REPO` says *which
#: tree is being scanned* and callers reassign it -- `test_boundary_guards`
#: points it at a temp directory to exercise `scan` end to end.  This one says
#: *which checkout this instrument belongs to*, and nothing may reassign it:
#: check 3's vocabulary is imported from `core/coroot.py`, and it must come
#: from the checkout that owns this file rather than from whatever tree is
#: under the microscope.  Conflating the two made the gate raise
#: `VocabularyUnavailable` on a temp fixture directory, which is a check
#: reporting on itself instead of on its subject.
_OWN_REPO = REPO

#: Directories never scanned.  `refs/` is downloaded third-party reference
#: material (emulator sources, wiki mirrors); its contents are not ours to
#: edit and its hits are all coincidental.
SKIP_DIRS = {"refs", ".git", "__pycache__", "out", "mods", "build", "sessions"}

#: SHA-256 (first 32 hex chars) of lowercased identifiers matched as whole
#: `[A-Za-z0-9]+` tokens.  Account names and short character names.
BANNED_TOKENS = {
    "f0b139242b497c8a5ac33bf176ad89d5",
    "68892dda0232282bc2a574a131c113c4",
    "adc5b3ec3cf30a23c675da5a43a4c0f9",
    "3ed1f621d7a2b5e2f1772c7f5324cbea",
    # OWNER RULING 2026-08-26: the owner's FIRST NAME is acceptable PII and
    # its entry is removed here.  RELAXED EXACTLY ONE TOKEN.  The squashed
    # email entry in BANNED_SQUASHED is NOT covered by this ruling and stays
    # -- it is the control that proves this relaxation did not over-apply to
    # the adjacent forbidden thing.  Do not widen without a fresh ruling.
    "a1fce31d9b8cd02f4fa150c81ee0e705",
    "9c7cf46e652a7e7d650e78f9f8ae6176",
    "3e21b5457152fe8520fb999f2ed252c9",
    "fe9d2cad19c77c77ca0e90d6fbba323f",
    "dda31ea2eec8f8c29172f7b69aaf4e00",
    "f741e58960ae3ce6ddf54cb987854ab4",
    "c8cb05cf55e1a6889af8bf9e90a55636",
    "b6989845d3355c3f6ff106eb473a223e",
    "bc99eefefad59caddb127cfe2134ecac",
    "147cf97ab3bb2c9df8124b4ffd2ec892",
    "34c7adcffb08f125a76036bf51fd5911",
    "4b4ac4cff08f3a5bdf6a285a045c7721",
    "ac1125cad10107d3cb27f66b0f3691ea",
}

#: SHA-256 (first 32 hex chars) of lowercased identifiers matched against the
#: file squashed to `[a-z0-9]`.  Long character names, the author's email,
#: machine name and home directory.
BANNED_SQUASHED = {
    "e978a35a17f72fe48efe5fc0eacd3076",
    "c7980d1799fbde06337b1922fb36dfdc",
    "3659c661750b8b0e1ae1912c30350d57",
    "c954a866c1c533669be31b60b89ebb92",
    "877bfe3d497f2aaaf941efb0d5ec6e15",
    "1502d8b0840edba20690b08546d08f7d",
    "df0fda2a18a2a2efaffeb33e0ac61859",
    "e308f88da630ad862ec2ef277618b4ca",
}

# ---------------------------------------------------------------------------
# Per-contributor identifiers, kept out of the repository entirely.
# ---------------------------------------------------------------------------
#
# WHY THE HASHED LIST ABOVE CANNOT SIMPLY BE EXTENDED
# ---------------------------------------------------
# The sets above protect the original author, and they work because they are
# *already public knowledge to the attacker who has the repo* -- there is no
# way to un-know them. For a **second contributor**, adding hashes would be
# actively harmful: character names and account handles are short, lowercase,
# and drawn from a small guessable space, so a hash of one is not a protection.
# It is a **verification oracle**. Anyone with the repository can test a guess
# against the list, and a wordlist of common Conquer Online names cracks the
# short ones in seconds. Publishing a hash of a four-letter handle publishes
# the handle.
#
# So contributors' identifiers never enter the repository in any form, hashed
# or otherwise. Each person keeps a `.sanitize-local` file, gitignored, holding
# their own identifiers in **plaintext** -- which is safe precisely because it
# never leaves their machine, and is simpler than hashing something that was
# never going to be shared.
#
# The check then runs against the union: the committed hashes plus whatever the
# local file names. Everyone is protected by the same gate, and nobody has to
# publish what they are protecting.
LOCAL_LIST = REPO / ".sanitize-local"


def load_local_identifiers() -> tuple[set[str], set[str]]:
    """`(tokens, squashed)` from `.sanitize-local`, or two empty sets.

    Format is deliberately the least a person can get wrong: one identifier per
    line, `#` starts a comment, blank lines ignored. Classification into
    whole-token versus squashed matching mirrors `learn()` exactly, so a local
    entry behaves the same as a committed one.
    """
    tokens: set[str] = set()
    squashed: set[str] = set()
    try:
        raw = LOCAL_LIST.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return tokens, squashed
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        sq = "".join(c for c in line.lower() if c.isalnum())
        if len(sq) >= 10:
            squashed.add(sq)
        for t in TOKEN_RE.findall(line.lower()):
            # Three characters is the floor the `--learn` path uses too: below
            # that a "name" matches half the English language and every run
            # would be a wall of false positives.
            if len(t) >= 3:
                tokens.add(t)
    tokens -= squashed
    return tokens, squashed


def local_list_is_tracked() -> bool:
    """True if `.sanitize-local` is in git's index -- which would mean the file
    protecting your identifiers has itself published them.

    Checked on every run rather than trusted to `.gitignore`, because
    `git add -f` overrides an ignore rule silently and this is the one file
    where that mistake is unrecoverable.
    """
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", ".sanitize-local"],
                           cwd=REPO, capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


#: Any absolute path into a user profile.  `Public` is a shared, non-personal
#: Windows account and is not a leak.
USER_PATH_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b)([A-Za-z0-9._-]+)",
                          re.IGNORECASE)

# ---------------------------------------------------------------------------
# Check 3 -- hardcoded install paths.
#
# WHAT THIS USED TO BE, AND WHY THAT WAS NOT A CHECK
# --------------------------------------------------
# Until 2026-08-23 this was one regex::
#
#     re.compile(r"Program Files[^\"'\n]{0,12}[\\/]+Classic Conquer")
#
# -- the *Program Files* install path, and only that one.  Meanwhile the
# standard home for this project's data on the machine it is developed on is
# ``C:\COMod`` (``ConquerAssets\Clients``, ``COmmunityLibrary``, ``Installed``),
# so the path new code was overwhelmingly likely to hardcode was the one path
# the guard could not see.  Measured with the rule below, over the tree as it
# stood: **68 findings across 56 files** in this checkout, and **5 across 2**
# in the tree that actually ships (`tools/extract_comod.py`).  The old check
# found none of them, and the summary line printed "no hardcoded install
# paths" on every run.
#
# That is this repository's recurring defect and it has its own name in
# CONTRIBUTING.md: **a guard scoped so it cannot fire on the current layout
# reads as coverage and is not.**  A check that cannot fail must not print a
# reassurance, so the verdict line below now names the class it actually
# checked rather than claiming the general one.
#
# THE RULE, AND WHY IT IS A CLASS RATHER THAN A SECOND LITERAL
# ------------------------------------------------------------
# Replacing one prefix with a list of two would have exactly the same defect
# one machine later.  So the rule is split into a *where* and a *what*, and
# only the *what* is enumerated:
#
#   where   ANY drive-rooted absolute path -- any drive letter, any parent.
#           ``C:\COMod\...``, ``D:\Games\...``, ``E:\junk\...`` are all the
#           same finding.  Nothing about the front of the path is special,
#           because the front of the path is precisely the part that differs
#           per machine.
#
#   what    a path segment naming something **this project resolves at
#           runtime** -- the install archives and install directory names in
#           `coroot.REQUIRED` / `coroot._INSTALL_DIR_NAMES`, plus the trees in
#           `coroot.RESOLVED_TREE_NAMES` (``ConquerAssets``, ``Clients``,
#           ``COmmunityLibrary``, ``Installed``).  The vocabulary is IMPORTED
#           from `core/coroot.py`, not copied, so declaring a new resolvable
#           tree guards it in the same commit.
#
# The consequence worth stating: this does **not** flag every absolute path.
# ``C:\Windows\System32``, ``C:\Program Files\7-Zip\7z.exe`` and the
# deliberately-nonexistent ``C:\nope\gone.dat`` that a test feeds a function
# are not findings, because none of them names something `coroot` could have
# answered.  They are not this defect.
#
# WHAT IS IN SCOPE, AND WHY NOT EVERYTHING
# ----------------------------------------
# This repository documents its measurements with real paths in prose,
# everywhere -- 35 files under `docs/` quote one, and so do dozens of module
# docstrings ("measured on ``C:\COMod\ConquerAssets\Clients\6090``").  Those
# are the record of where a number came from; a check that reddens them is a
# check somebody neuters within a week, and the value it protects is real.
# So scope is decided by whether the text can hardcode a path *into a running
# program*:
#
#   * ``.py``  -- every string literal that is **not** a docstring, found by
#     parsing the file.  Comments are not in the AST and are therefore out by
#     construction.  A file that will not parse is a FINDING, not a skip: a
#     scanner that silently stops looking is the thing this file exists to
#     prevent.
#   * ``.py``, additionally -- the literal must **be** the path, not mention
#     it.  ``Path(r"C:\COMod\ConquerAssets\Clients")`` is a location;
#     ``"REFUSED: it used to default to C:\COMod\...\5065, which"`` is a
#     sentence, and the message is the point.  Judged by whether the match
#     spans the whole literal (whitespace and a trailing separator aside).
#   * anything else that is not prose -- ``.ps1``, ``.cmd``, ``.bat``,
#     ``.sh``, ``.js``, ``.html``, ``.json``, ``.yml`` ... -- scanned WHOLE.
#     There is no AST to ask, a shell script has no docstrings, and a batch
#     file naming an asset tree is a hardcode wherever on the line it sits.
#   * ``.md``, ``.txt``, ``.rst`` -- OUT.  Prose.  A document cannot make a
#     program run on one machine only.  This is a deliberate hole and it is
#     the one that keeps the rest of the check alive.
#
# Known gaps, named rather than left implied: a path assembled at runtime
# (``"C:/" + "COMod"``), a path inside a longer command string
# (``"cd C:\COMod\ConquerAssets && build"``), a UNC path (``\\host\share``),
# and a POSIX-rooted path (``/mnt/c/COMod/...``).  None of them has been seen
# in this tree; all of them would pass.
# ---------------------------------------------------------------------------

#: Any absolute path into a user profile.  `Public` is a shared, non-personal
#: Windows account and is not a leak.
USER_PATH_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b)([A-Za-z0-9._-]+)",
                          re.IGNORECASE)

#: A drive-rooted absolute path, with as many segments as it has.  The drive
#: letter must not be preceded by an alphanumeric, or ``https://`` matches it
#: as drive ``s``.  ``<>`` and ``{}`` are inside the segment character class on
#: purpose: ``D:\<your install>\Clients`` and
#: ``C:/COMod/ConquerAssets/Clients/{}`` are both still *paths*, and whether
#: the placeholder excuses them is `PLACEHOLDER_HEAD_RE`'s decision, not a
#: question of whether this regex can see them at all.  A path the matcher
#: cannot parse is a path it silently permits.
ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:(?=[\\/])"
    r"(?:[\\/]{1,2}[A-Za-z0-9_.@ ()+#'{}<>*\-]{1,64})+")

_SEP_RE = re.compile(r"[\\/]+")

#: A first segment that announces itself as a fill-in-your-own.  Help text has
#: to be able to show the shape of the answer -- `coroot`'s own "could not find
#: the install" message prints ``D:\path\to\Classic Conquer 2.0`` -- and this
#: is the sanctioned way to write one.  Use it instead of asking for an
#: allowlist entry.
#:
#: **Only the first segment after the drive letter is examined**, and that
#: restriction is the whole safety of this exemption.  The first segment is the
#: machine-specific part -- the part that differs between two people's
#: checkouts -- so a placeholder there means the path names no machine.
#: Anywhere else it is decoration on a real location:
#: ``C:\COMod\ConquerAssets\Clients\<build>`` and
#: ``"C:/COMod/ConquerAssets/Clients/{}".format(b)`` still hardcode
#: ``C:\COMod``, and both are findings.
PLACEHOLDER_HEAD_RE = re.compile(r"^(?:path|\.\.\.|<[^>]*>)$", re.IGNORECASE)

#: Prose.  Out of scope -- see the note above.
PROSE_SUFFIXES = {".md", ".txt", ".rst"}

#: The files allowed to name an install path anyway, and the reason each is.
#: **Two entries, both the same file.**  `core/coroot.py` is the resolver, and
#: `CONVENTIONAL_ROOT` is a discovery *hint* it validates before believing --
#: the one place where naming a location is the job rather than an assumption.
#: The blender entry is the vendored copy `tools/build_addon.py` generates
#: from it, so it is not a second decision.
#: This test is here for its own bite-proof fixtures at the bottom of the file.
#:
#: Adding a path here is saying "this file may only work on one machine".
#: Route through `coroot` instead: `game_root`, `clients_root` / `clients_dir`,
#: `community_library`, `installs_root`, `find_derived`, `declared_kinds`.
INSTALL_PATH_ALLOWED = {"core/coroot.py",
                        "blender/io_scene_c3/vendor/coroot.py",
                        "tests/test_sanitization.py"}


#: The two-directional control for check 3, run on EVERY invocation before any
#: real file is read.  `(relative path, text, should it be a finding, why)`.
#:
#: A gate that cannot fail is the defect this check was rewritten to remove, so
#: it is not enough for the rewrite to have been careful once -- the property
#: has to be re-measured every run, and by both controls.  A positive-only
#: control passes a check that flags everything; a negative-only control passes
#: a check that flags nothing.
#:
#: The negatives are not hypothetical.  Every one of them is a shape that
#: exists in this tree today and that a broader rule reddened when it was
#: measured: 35 files under `docs/` quote an install path in prose, `coroot`
#: and `health` print ``D:\path\to\...`` in help text, `colibrary` probes
#: 7-Zip's own conventional location, `test_dcache` feeds a deliberately
#: absent path to a cache, and `clientdisplay` REFUSES to default to a path and
#: says so in the refusal.  A check that reddens those is a check somebody
#: turns off.
CHECK3_CONTROLS: "tuple[tuple[str, str, bool, str], ...]" = (
    # -- POSITIVE: it must still bite --------------------------------------
    ("tools/_control.py", 'CLIENTS = Path(r"C:\\COMod\\ConquerAssets\\Clients")',
     True, "a newly hardcoded COMod asset tree, the layout the old check missed"),
    ("tools/_control.py", 'ROOT = Path("D:/Games/Conquer Online/ini")',
     True, "another drive, another parent -- the class, not one prefix"),
    ("tools/_control.py", 'LIB = Path(r"E:\\stuff\\COmmunityLibrary")',
     True, "the community library, resolved by coroot.community_library"),
    ("tools/_control.py", 'A = Path(r"C:\\x\\Classic Conquer 2.0\\c3.wdf")',
     True, "an install directory name and an archive name"),
    ("_control.cmd", 'set "CLIENT=C:\\COMod\\ConquerAssets\\Clients\\5065"',
     True, "a non-python file, scanned whole -- no AST to ask"),
    ("tools/_control.py", 'T = "C:/COMod/ConquerAssets/Clients/{}".format(b)',
     True, "a placeholder in the TAIL leaves the machine-specific head hardcoded"),
    ("tools/_control.py", 'U = Path(r"C:\\COMod\\ConquerAssets\\Clients\\<build>")',
     True, "...and so does an angle-bracket one"),
    # -- NEGATIVE: it must not bite ----------------------------------------
    ("docs/_control.md", "measured on C:\\COMod\\ConquerAssets\\Clients\\6090",
     False, "prose recording where a measurement was made"),
    ("tools/_control.py",
     'def f():\n    """Measured on C:\\\\COMod\\\\ConquerAssets\\\\Clients\\\\6090."""\n',
     False, "the same record, in a docstring"),
    ("tools/_control.py",
     '# corpus at C:\\COMod\\ConquerAssets\\derived\\7878-dat-decrypted\nX = 1\n',
     False, "the same record, in a comment"),
    ("tools/_control.py", 'HELP = "set CO_ROOT=D:\\\\path\\\\to\\\\Classic Conquer 2.0"',
     False, "help text using the sanctioned placeholder"),
    ("tools/_control.py", 'E = r"D:\\<your install>\\Clients\\5065"',
     False, "the other sanctioned placeholder form, in the head"),
    ("tools/_control.py",
     'raise SystemExit("REFUSED: it used to default to one machine\'s "\n'
     '                 r"C:\\COMod\\ConquerAssets\\Clients\\5065, which is why "\n'
     '                 "--client-dir is required")',
     False, "a message ABOUT a path -- the literal is a sentence, not a location"),
    ("tools/_control.py", 'SEVEN = r"C:\\Program Files\\7-Zip\\7z.exe"',
     False, "a third-party tool's own location; coroot resolves no such thing"),
    ("tools/_control.py", 'W = Path(r"C:\\Windows\\System32\\kernel32.dll")',
     False, "an OS-guaranteed location"),
    ("tools/_control.py", 'self.assertIsNone(dcache.get(Path("C:/nope/gone.dat")))',
     False, "a deliberately absent path fed to a function as test input"),
)


def prove_check3_bites(vocab: "set[str]") -> "list[str]":
    """Run `CHECK3_CONTROLS`.  Empty means the check still works both ways."""
    broken = []
    for rel, text, want, why in CHECK3_CONTROLS:
        got = bool(install_path_findings(rel, text, vocab))
        if got != want:
            broken.append(
                f"control FAILED: {rel} should {'' if want else 'NOT '}have "
                f"been a finding and was {'' if got else 'not '}-- {why}\n"
                f"            input: {text!r}")
    return broken


#: One-slot cache for `_install_vocabulary`.  A list rather than a global so
#: it is obvious there is exactly one, and empty means "not built yet" -- never
#: "built and empty", which the builder refuses to produce.
_VOCAB_CACHE: "list[frozenset]" = []


class VocabularyUnavailable(RuntimeError):
    """`core/coroot.py` could not be imported, so check 3 has no vocabulary.

    Fatal, and reported before any file is scanned.  A check that cannot build
    the list of things it is looking for finds none of them and says PASS,
    which is the exact failure this whole file is a reaction to.
    """


def _install_vocabulary() -> "set[str]":
    """Lowercased names that make a drive-rooted path a finding.

    Built from `core/coroot.py` so the gate and the resolver cannot drift:
    every archive filename in `REQUIRED` (both container generations), every
    install directory name discovery knows, and every tree in
    `RESOLVED_TREE_NAMES`.

    ``ini`` -- the third `REQUIRED` entry -- is deliberately excluded.  It is
    a three-letter directory name that appears in unrelated paths, and the
    archives beside it already identify an install root.

    Read from `_OWN_REPO`, never `REPO`: the word list belongs to the checkout
    that owns this test, not to whichever tree is being scanned.  Cached, so a
    936-file run pays for it once.
    """
    if _VOCAB_CACHE:
        return set(_VOCAB_CACHE[0])
    path = str(_OWN_REPO / "core")
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import coroot                                       # noqa: PLC0415
    except Exception as e:                                  # noqa: BLE001
        raise VocabularyUnavailable(
            f"could not import core/coroot.py ({type(e).__name__}: {e}); "
            f"check 3 cannot run") from e
    vocab: set[str] = set()
    for spec, _kind in coroot.REQUIRED:
        for alternative in spec.split("|"):
            for part in alternative.split("+"):
                if "." in part:                    # an archive, not a dir name
                    vocab.add(part.lower())
    vocab |= {n.lower() for n in coroot._INSTALL_DIR_NAMES}
    vocab |= {n.lower() for n in coroot.RESOLVED_TREE_NAMES}
    if not vocab:                                           # pragma: no cover
        raise VocabularyUnavailable("coroot declared no resolvable names")
    _VOCAB_CACHE.append(frozenset(vocab))
    return vocab


def install_paths_in(text: str, vocab: "set[str]") -> "list[tuple[int, int, str]]":
    """``[(start, end, path)]`` for every drive-rooted path naming `vocab`."""
    out = []
    for m in ABS_PATH_RE.finditer(text):
        p = m.group(0)
        segs = [s.strip() for s in _SEP_RE.split(p) if s.strip()][1:]   # drop "C:"
        if segs and PLACEHOLDER_HEAD_RE.match(segs[0]):
            continue
        if any(s.lower() in vocab for s in segs):
            out.append((m.start(), m.end(), p))
    return out


_ONLY_SPACE_RE = re.compile(r"^\s*$")
_ONLY_TAIL_RE = re.compile(r"^[\\/\s]*$")


def _is_whole_literal(s: str, a: int, b: int) -> bool:
    """True when the literal IS the path rather than a sentence quoting one."""
    return bool(_ONLY_SPACE_RE.match(s[:a]) and _ONLY_TAIL_RE.match(s[b:]))


def _python_string_literals(text: str) -> "list[tuple[int, str]]":
    """``[(lineno, value)]`` for every non-docstring string literal.

    Raises `SyntaxError` -- the caller turns that into a finding rather than
    swallowing it.
    """
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def install_path_findings(rel: str, text: str, vocab: "set[str]",
                          stats: "dict[str, int] | None" = None) -> "list[str]":
    """Check 3 for one file, as human sentences.  Empty means clean.

    `stats` accumulates what the check actually looked at -- how many files
    were in scope, how many were skipped as prose or allowlisted, and how many
    drive-rooted paths of ANY kind it read.  `main` prints them, because zero
    findings over zero absolute paths and zero findings over four hundred of
    them are different results and only the count separates them.
    """
    def bump(k, n=1):
        if stats is not None:
            stats[k] = stats.get(k, 0) + n

    if rel in INSTALL_PATH_ALLOWED:
        bump("allowlisted")
        return []
    suffix = ("." + rel.rsplit(".", 1)[-1].lower()) if "." in rel else ""
    if suffix in PROSE_SUFFIXES:
        bump("prose")
        return []
    bump("scanned")
    bump("absolute paths", len(ABS_PATH_RE.findall(text)))

    advice = ("resolve it through core/coroot.py instead -- game_root, "
              "clients_root/clients_dir, community_library, installs_root, "
              "find_derived, declared_kinds.  If it is help text, write the "
              "variable part as path/to/... or <name>")

    if suffix != ".py":
        return [f"hardcoded install path {p!r} -- {advice}"
                for _a, _b, p in install_paths_in(text, vocab)]

    if not install_paths_in(text, vocab):
        return []                       # cheap reject before paying for a parse
    try:
        literals = _python_string_literals(text)
    except SyntaxError as e:
        return [f"check 3 could not parse this file ({e}), so its string "
                f"literals were NOT scanned for hardcoded install paths. A "
                f"scan that stops looking and reports clean is the failure "
                f"this message exists to prevent"]
    bad = []
    for lineno, value in literals:
        for a, b, p in install_paths_in(value, vocab):
            if _is_whole_literal(value, a, b):
                bad.append(f"line {lineno}: hardcoded install path {p!r} "
                           f"-- {advice}")
    return bad

# ---------------------------------------------------------------------------
# Decompiled source material.
#
# The 6090 client shipped with its asserts intact, so `tools/asserts.py` can
# recover the source path, line number and the *text of the failing condition*
# for ~5,300 sites. Those condition fragments are literal text from TQ
# Digital's proprietary source. Under the project's "ship an engine, never the
# data" rule they sit on the same side of the line as the game assets: they may
# live in this private tree, and must not reach COre / VibeCo / COMod.
#
# This is a different category from the personal data above -- it is not a leak
# about the author, it is someone else's copyrighted text -- but it wants the
# same treatment, so it reuses the same machinery.
#
# The identifiers are stored **as hashes**, for the same reason the personal
# ones are: a check that carries a copy of the thing it exists to contain has
# not contained it. Only Hungarian-notation parameter names distinctive enough
# to have come from an assert are listed. Class names and .cpp filenames are
# deliberately excluded -- they collide with this project's own long-standing
# vocabulary (`RolePart.ini` is a real game data file), and a filename is not
# source text.
# ---------------------------------------------------------------------------

BANNED_SOURCE = {
    "10abcc50f3f16c93fa8c61fb4f456064",
    "135c3545fd422c4aef79eabe659e1243",
    "147fb2c46121a572aa1680132f8261bd",
    "326d0e268630850298381bb31bf3724d",
    "697d81a98308d6fe746ea1a910e61b88",
    "9745841f2dfd4f229747e8b7443a3cd4",
    "b3b366a1cdbdb3f7c50daa29d4ab5752",
}

#: The vendor source trees the asserts name. Matched literally rather than
#: hashed: these are *paths*, and naming a filename is not reproducing a file.
#: They remain a reliable marker that a file carries assert-derived material,
#: which is what makes them worth gating on.
VENDOR_TREE_RE = re.compile(r"c3engine_official|cq2clientcn", re.IGNORECASE)

#: Files allowed to carry it, because recording the finding is what they are
#: for. **Everything in this set is private-tree-only** and must not survive an
#: extraction -- see docs/repo_split.md §3. Adding a path here is a decision
#: that the file can never be published, so add deliberately.
DECOMPILED_ALLOWED = {"docs/sockets_6090.md",
                      "docs/handoff_socket_basis.md",
                      "docs/dll_analysis.md",
                      "docs/STATUS.md",
                      "tests/test_sanitization.py",
                      "docs/sanitization.md",
                      "docs/repo_split.md"}

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

#: Squashed identifiers span at most this many consecutive alphanumeric runs
#: (`c` + `users` + `<name>` is the worst case at three).
MAX_WINDOW = 3
#: Length bounds of everything in BANNED_SQUASHED -- used only to skip work.
MIN_SQUASHED_LEN = 8
MAX_SQUASHED_LEN = 24


def h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Decoded views -- see "Why the scan also reads DECODED views" above.
# ---------------------------------------------------------------------------

#: A contiguous hex run. 8 is the floor because the shortest thing worth finding
#: is a 3-character token (6 hex chars) with a byte of context either side, and
#: shorter runs are overwhelmingly ordinary numbers.
HEX_RUN_RE = re.compile(r"[0-9a-fA-F]{8,}")
#: Hex byte pairs written with separators: `61 6c 69 63 65`, `\x61\x6c`,
#: `0x61,0x6c`, `61-6c-69`.
HEX_SEP_RE = re.compile(r"(?:[0-9a-fA-F]{2}[\s,;:_\-]{1,2}|\\x[0-9a-fA-F]{2}|"
                        r"0x[0-9a-fA-F]{2}[\s,;]{0,2}){4,}[0-9a-fA-F]{0,2}")
B64_RUN_RE = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_NONHEX_RE = re.compile(r"[^0-9a-fA-F]")

#: Decoded bytes per view per file. A bound is needed (a repo can contain a
#: multi-megabyte hex blob) but a SILENT bound would make the gate quietly stop
#: looking, so `decoded_views` reports truncation and `main` prints it.
DECODE_BUDGET = 4_000_000


def _hex_view(text: str, budget: int) -> tuple[str, bool]:
    r"""Contiguous hex runs, decoded at both byte alignments.

    Both alignments, because a hex run is only guaranteed byte-aligned at its
    own start: a run that picked up one extra leading nibble from a length
    prefix or a neighbouring field would hide every byte in it from an
    offset-0-only decode, and the second pass costs one more `fromhex`.
    """
    out, spent, truncated = [], 0, False
    for m in HEX_RUN_RE.finditer(text):
        run = m.group(0)
        for start in (0, 1):
            body = run[start:]
            body = body[:len(body) & ~1]
            if len(body) < 6:
                continue
            if spent + len(body) // 2 > budget:
                truncated = True
                continue
            out.append(bytes.fromhex(body).decode("latin-1"))
            spent += len(body) // 2
    return "\n".join(out), truncated


def _hex_sep_view(text: str, budget: int) -> tuple[str, bool]:
    out, spent, truncated = [], 0, False
    for m in HEX_SEP_RE.finditer(text):
        body = _NONHEX_RE.sub("", m.group(0).replace("0x", "").replace("\\x", ""))
        body = body[:len(body) & ~1]
        if len(body) < 6:
            continue
        if spent + len(body) // 2 > budget:
            truncated = True
            continue
        out.append(bytes.fromhex(body).decode("latin-1"))
        spent += len(body) // 2
    return "\n".join(out), truncated


def _b64_view(text: str, budget: int) -> tuple[str, bool]:
    out, spent, truncated = [], 0, False
    for m in B64_RUN_RE.finditer(text):
        pad = m.group(0) + "=" * (-len(m.group(0)) % 4)
        for dec in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = dec(pad)
            except Exception:                     # noqa: BLE001 -- not base64
                continue
            if spent + len(raw) > budget:
                truncated = True
                continue
            out.append(raw.decode("latin-1"))
            spent += len(raw)
    return "\n".join(out), truncated


def decoded_views(text: str, budget: int = DECODE_BUDGET) -> list[tuple[str, str, bool]]:
    """`[(name, decoded_text, truncated)]` -- the file as the wire saw it.

    Empty views are dropped: a caller that iterates these is asking *what else
    is in this file*, and an empty string is not an answer, it is the absence
    of one. `--views` reports the sizes so an all-empty result is visible.
    """
    hx, hxt = _hex_view(text, budget)
    hs, hst = _hex_sep_view(text, budget)
    b6, b6t = _b64_view(text, budget)
    out = [("hex", hx, hxt), ("hex-separated", hs, hst), ("base64", b6, b6t)]
    joined = "\n".join(v for _, v, _ in out if v)
    if "\x00" in joined:
        # UTF-16LE, the shape a memory capture stores a name in.
        out.append(("wide", joined.replace("\x00", ""),
                    hxt or hst or b6t))
    return [(n, v, t) for n, v, t in out if v]


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Check 4 -- no recovered name table is publishable.
#
# COMod is MIT licensed and its safety story is one sentence: *the tool ships
# no game data; every path it reads points into an install the user already
# has*.  A pre-bootstrap name profile (`tools/profilepack.py`) is the first
# artefact that would put an asterisk on that, so it ships as a SEPARATE,
# opt-in file and never as part of this repository.
#
# `.gitignore` already ignores `out/` whole, which is where a profile is
# written -- and that stops the accident.  It does not stop `git add -f`,
# which overrides an ignore rule silently.  That is the identical reasoning
# `local_list_is_tracked` above was written for, and this is the same guard
# pointed at the other file that must never be committed.
#
# Detected by CONTENT and not by filename, because a rename defeats a name
# check and the thing being guarded is a compilation, not a path.

#: A recovered table is `{u32 hash hex: asset path}`.  The threshold is high
#: on purpose: a handful of hash-keyed pairs is a fixture, tens of thousands
#: are a harvest.  Measured, the real tables are 10,160 and 14,266 pairs.
NAME_TABLE_MIN_PAIRS = 200

_HASH_KEY_RE = re.compile(r"^[0-9a-fA-F]{1,8}$")
_ASSET_PATH_RE = re.compile(
    r"\.(c3|dds|jpg|jpeg|png|bmp|cur|ico|wav|mp3|ini|dat|ani|3ds|phy)$",
    re.IGNORECASE)


def _looks_like_name_table(doc) -> int:
    """How many `{hash: asset path}` pairs a parsed document holds."""
    if not isinstance(doc, dict):
        return 0
    n = 0
    for k, v in doc.items():
        if (isinstance(k, str) and isinstance(v, str)
                and _HASH_KEY_RE.match(k) and _ASSET_PATH_RE.search(v)):
            n += 1
    return n


def name_table_findings(path: Path) -> list:
    """Sentences naming a recovered name table, or `[]`.

    Looks inside `.zip` too: a packed profile is the distributable shape, and
    a check that only read loose JSON would miss the exact artefact this
    guard exists for.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".json", ".zip"):
        return []
    try:
        if suffix == ".json":
            pairs = _looks_like_name_table(json.loads(p.read_text("utf-8")))
            if pairs >= NAME_TABLE_MIN_PAIRS:
                return [f"holds {pairs:,} recovered {{hash -> asset path}} "
                        f"pairs -- this is game-derived DATA and must not be "
                        f"in this repository. Profiles ship separately; see "
                        f"tools/profilepack.py."]
            return []
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            for n in names:
                if not n.lower().endswith(".json"):
                    continue
                try:
                    pairs = _looks_like_name_table(json.loads(z.read(n)))
                except (ValueError, OSError):
                    continue
                if pairs >= NAME_TABLE_MIN_PAIRS:
                    return [f"is an archive whose member {n!r} holds "
                            f"{pairs:,} recovered {{hash -> asset path}} "
                            f"pairs -- a packed name profile. It must not be "
                            f"in this repository."]
    except (OSError, ValueError, zipfile.BadZipFile):
        return []
    return []


#: `(description, document, must_be_a_finding)` -- the check must bite in
#: BOTH directions before it is allowed to report a clean tree.  A guard that
#: cannot fire reports every tree clean, which is the failure this whole file
#: was written about.
CHECK4_CONTROLS = (
    ("a real recovered name table",
     {f"{i:08x}": f"c3/effect/thing-{i}/2.c3" for i in range(NAME_TABLE_MIN_PAIRS)},
     True),
    ("a table one pair short of the threshold",
     {f"{i:08x}": f"c3/effect/thing-{i}/2.c3"
      for i in range(NAME_TABLE_MIN_PAIRS - 1)}, False),
    ("hash-keyed values that are not asset paths",
     {f"{i:08x}": f"some prose {i}" for i in range(NAME_TABLE_MIN_PAIRS * 2)},
     False),
    ("an ordinary config document", {"game_root": "somewhere", "kinds": {}},
     False),
)


def prove_check4_bites(tmp_dir=None) -> list:
    """Run `CHECK4_CONTROLS`; return the ones that behaved wrongly."""
    import tempfile                                       # noqa: PLC0415
    broken = []
    with tempfile.TemporaryDirectory(dir=tmp_dir) as td:
        for desc, doc, want in CHECK4_CONTROLS:
            f = Path(td) / "probe.json"
            f.write_text(json.dumps(doc), "utf-8")
            got = bool(name_table_findings(f))
            if got != want:
                broken.append(f"{desc}: expected "
                              f"{'a finding' if want else 'no finding'}, "
                              f"got {'a finding' if got else 'none'}")
        # ...and the same document inside a zip, which is the shipping shape.
        z = Path(td) / "probe.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("c3_names.json",
                        json.dumps(dict(CHECK4_CONTROLS[0][1])))
        if not name_table_findings(z):
            broken.append("a packed profile zip: expected a finding, got none")
    return broken


def publishable_files() -> list[Path]:
    """Every file `git push` would carry: tracked, plus untracked and not
    ignored.  Falls back to a filesystem walk outside a git checkout."""
    out: list[Path] = []
    try:
        for args in (["git", "ls-files"],
                     ["git", "ls-files", "--others", "--exclude-standard"]):
            r = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                               timeout=60)
            if r.returncode != 0:
                raise OSError(r.stderr)
            out += [REPO / line for line in r.stdout.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        out = [p for p in REPO.rglob("*") if p.is_file()]
    keep = []
    for p in out:
        try:
            rel = p.relative_to(REPO)
        except ValueError:
            continue
        if set(rel.parts) & SKIP_DIRS:
            continue
        if p.is_file():
            keep.append(p)
    return sorted(set(keep))


def identifier_findings(text: str, local: "tuple[set[str], set[str]] | None" = None,
                        *, view: str = "") -> list[str]:
    """Every banned identifier present in `text`, as human sentences.

    Split out of `scan` so that **one matcher serves every view of a file** --
    the plaintext and each decoded form. Two copies of this logic is how one of
    them ends up subtly wrong and stays wrong, which is the argument
    `core/safepath.py` is built on.

    `view` names which decoding produced `text`, and it goes in the message: a
    finding a reader cannot locate in the file is a finding they will assume is
    a false positive.
    """
    bad: list[str] = []
    local_tokens, local_squashed = local or (set(), set())
    where = f" [in the {view} view of the file]" if view else ""
    toks = [t.lower() for t in TOKEN_RE.findall(text)]

    for t in set(toks):
        if h(t) in BANNED_TOKENS:
            # The identifier is named only for the committed list, whose
            # contents are already knowable to anyone holding the repo. Never
            # for a decoded view: printing it would move a name out of an
            # encoding and into a build log, which is the opposite of the job.
            shown = f"the token {t!r}" if not view else f"a token ({len(t)} chars)"
            bad.append(f"personal identifier present as {shown}{where}")
        elif t in local_tokens:
            bad.append(f"one of your .sanitize-local identifiers is present as "
                       f"a whole token ({len(t)} chars){where} -- not naming it here")

    # Squashed match, done over *token windows* rather than every character
    # offset.  Every multi-part identifier we care about -- `C:\Users\<name>`,
    # `<user>@gmail.com`, `MACHINE-NAME`, a character name written with an
    # `&` or `~` in it -- is at most three
    # alphanumeric runs, so joining 1..MAX_WINDOW consecutive tokens catches
    # it regardless of the punctuation between them, and costs O(3n) hashes
    # instead of O(len(file) * len(lengths)).
    # The length bounds are an optimisation derived from `BANNED_SQUASHED`, so
    # they have to widen for local entries -- otherwise an identifier longer
    # than anything in the committed list is skipped by the very check that is
    # supposed to protect it, silently.
    max_sq = MAX_SQUASHED_LEN
    if local_squashed:
        max_sq = max(max_sq, max(len(s) for s in local_squashed))

    for i in range(len(toks)):
        joined = ""
        for w in range(MAX_WINDOW):
            if i + w >= len(toks):
                break
            joined += toks[i + w]
            if len(joined) > max_sq:
                break
            if len(joined) >= MIN_SQUASHED_LEN and h(joined) in BANNED_SQUASHED:
                bad.append("personal identifier present at token "
                           f"{i} (squashed match over {w + 1} token(s), "
                           f"{len(joined)} chars){where}")
            elif joined in local_squashed:
                bad.append(f"one of your .sanitize-local identifiers is present "
                           f"at token {i} (squashed match over {w + 1} token(s))"
                           f"{where} -- not naming it here")
    return bad


def source_findings(text: str, *, view: str = "") -> list[str]:
    """Decompiled-source identifiers in `text`. Same one-matcher rule as above."""
    where = f" [in the {view} view of the file]" if view else ""
    for t in {t.lower() for t in TOKEN_RE.findall(text)}:
        if h(t) in BANNED_SOURCE:
            return ["an identifier recovered from the client's own "
                    "asserts is present -- that is literal text from "
                    f"TQ's source, and belongs only in co-client-re{where} "
                    "(not naming it here)"]
    return []


def scan(path: Path, local: "tuple[set[str], set[str]] | None" = None,
         stats: "dict[str, int] | None" = None,
         vocab: "set[str] | None" = None,
         path_stats: "dict[str, int] | None" = None) -> list[str]:
    """Findings for one file, as human sentences.  Empty means clean.

    `local` is `(tokens, squashed)` from `.sanitize-local`, compared as
    plaintext. Findings from it deliberately **do not name the identifier**:
    the whole point is that it stays private, and printing it into a build log
    or a screenshot of a failing run would undo that.

    The identifier checks run over the plaintext **and over every decoded
    view** (`decoded_views`), so a name that is byte-for-byte present cannot
    hide behind hex or base64. The three *regex* checks below stay plaintext-
    only and deliberately: they are policy rules about how this repo writes
    paths, their allowlists are keyed on a path, and "there is a string that
    looks like an install path inside a decoded capture payload" is a different
    claim from "this file hardcodes an install path".

    `vocab` is check 3's word list (`_install_vocabulary`); it is passed in so
    the whole run builds it once, and rebuilt here when a caller scans a single
    file.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return [f"unreadable: {e}"]
    text = raw.decode("utf-8", errors="replace")
    rel = path.relative_to(REPO).as_posix()
    bad: list[str] = identifier_findings(text, local)

    for name, view, truncated in decoded_views(text):
        if stats is not None:
            stats[name] = stats.get(name, 0) + len(view)
        bad += identifier_findings(view, local, view=name)
        if truncated:
            bad.append(f"the {name} view hit the {DECODE_BUDGET:,}-byte decode "
                       f"budget, so part of this file was NOT checked in that "
                       f"view -- split the file or raise DECODE_BUDGET. A "
                       f"partial scan reporting clean is the failure this "
                       f"message exists to prevent")

    for m in USER_PATH_RE.finditer(text):
        bad.append(f"absolute user path {m.group(0)!r} -- "
                   "make it relative to the repo or resolve it at runtime")

    bad += install_path_findings(rel, text,
                                 vocab if vocab is not None
                                 else _install_vocabulary(), path_stats)

    # Decompiled source material.  Unlike everything above, a finding here is
    # not necessarily a bug in the file -- it may be a correct note that simply
    # cannot be published.  The message says so, because "delete this" is the
    # wrong fix about half the time.
    if rel not in DECOMPILED_ALLOWED:
        if VENDOR_TREE_RE.search(text):
            bad.append("names a vendor source tree recovered from the client's "
                       "compiled-in asserts -- private-tree-only material. "
                       "Either drop it, or add this path to DECOMPILED_ALLOWED "
                       "and accept that the file can never be extracted")
        bad += source_findings(text)
        for name, view, _ in decoded_views(text):
            bad += source_findings(view, view=name)
    return bad


def init_local(history_path: "str | None") -> int:
    """Write `.sanitize-local` from a `login_history.json`.

    The one-command version of "protect my own identifiers": it reads the PII
    file in place, writes the gitignored local list, and **prints only counts**
    -- never the identifiers themselves. That matters because this is the
    command a person runs while someone (or something) is watching their
    terminal, and the whole point is that these names stay off screens and out
    of logs.

    Nothing is copied into the repository: `.sanitize-local` is gitignored and
    `main()` fails the run if it is ever tracked.
    """
    import json
    if history_path is None:
        try:
            sys.path.insert(0, str(REPO / "tools"))
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
            import coroot                                   # noqa: PLC0415
            history_path = str(coroot.resolve().path / "login_history.json")
        except Exception as e:                              # noqa: BLE001
            print(f"could not locate a game install to read login_history.json "
                  f"from ({type(e).__name__}: {e}).\n"
                  f"Pass the path: --init-local C:\\path\\to\\login_history.json",
                  file=sys.stderr)
            return 2
    p = Path(history_path)
    if not p.is_file():
        print(f"no such file: {p}\n"
              f"If you have never logged in with the real client there may be "
              f"nothing to protect yet -- you can also write .sanitize-local by "
              f"hand, one identifier per line.", file=sys.stderr)
        return 2
    try:
        rows = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"could not read {p}: {e}", file=sys.stderr)
        return 2

    names = sorted({str(r.get(k) or "") for r in rows
                    for k in ("username", "player_name") if r.get(k)})
    if not names:
        print(f"{p} held no username or player_name fields; nothing written.")
        return 0

    if LOCAL_LIST.exists():
        existing = {ln.split("#", 1)[0].strip()
                    for ln in LOCAL_LIST.read_text("utf-8").splitlines()}
        added = [n for n in names if n not in existing]
        body = LOCAL_LIST.read_text("utf-8").rstrip("\n") + "\n"
    else:
        added = names
        body = (
            "# Your own identifiers, in plaintext, checked by\n"
            "# tests/test_sanitization.py before you publish anything.\n"
            "#\n"
            "# One per line. `#` starts a comment. This file is gitignored and\n"
            "# must NEVER be committed -- not even hashed. Short names hash to a\n"
            "# guessable value, so a published hash is an oracle, not a shield.\n"
            "#\n"
            "# Regenerate or extend:\n"
            "#   py -3 tests/test_sanitization.py --init-local\n"
        )
    for n in added:
        body += n + "\n"
    LOCAL_LIST.write_text(body, "utf-8")

    tok, sq = load_local_identifiers()
    print(f"wrote {LOCAL_LIST.relative_to(REPO)}: "
          f"{len(added)} identifier(s) added, {len(names)} found in the history.")
    print(f"it now yields {len(tok)} whole-token and {len(sq)} squashed pattern(s).")
    print("the identifiers themselves are deliberately not printed.")
    print("\nthis file is gitignored; `git status` should not show it. "
          "run the test now to check the tree against it:")
    print("    py -3 tests/test_sanitization.py")
    return 0


def main(argv: list[str]) -> int:
    if "--learn" in argv:
        return learn(argv[argv.index("--learn") + 1])
    if "--init-local" in argv:
        i = argv.index("--init-local")
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        return init_local(nxt if nxt and not nxt.startswith("-") else None)
    verbose = "-v" in argv or "--verbose" in argv

    files = publishable_files()
    local = load_local_identifiers()
    findings: list[tuple[str, list[str]]] = []

    # Check 3's word list, built once, BEFORE anything is scanned. If it
    # cannot be built the run stops here: a check whose vocabulary is empty
    # matches nothing and every file comes back clean.
    try:
        vocab = _install_vocabulary()
    except VocabularyUnavailable as e:
        print(f"\n*** CHECK 3 CANNOT RUN: {e} ***\n"
              "    Its vocabulary is declared in core/coroot.py "
              "(REQUIRED, _INSTALL_DIR_NAMES, RESOLVED_TREE_NAMES) and is\n"
              "    imported rather than copied, so an unimportable coroot "
              "means the install-path check has no list to match\n"
              "    against -- which would report every file clean. Refusing "
              "to scan.", file=sys.stderr)
        print(f"\nRESULT: FAIL -- check 3 has no vocabulary; "
              f"{len(files)} file(s) NOT scanned")
        return 2

    # ...and the check must prove it can still bite, in BOTH directions,
    # before it is allowed to report a clean tree.
    broken = prove_check3_bites(vocab)
    if broken:
        print("\n*** CHECK 3'S OWN CONTROLS FAILED ***\n"
              "    The install-path check no longer behaves as specified, so "
              "whatever it says about this\n    tree cannot be believed. "
              "Refusing to scan.\n")
        for b in broken:
            print(f"    {b}")
        print(f"\nRESULT: FAIL -- {len(broken)} of {len(CHECK3_CONTROLS)} "
              f"check-3 control(s) failed; {len(files)} file(s) NOT scanned")
        return 2

    # Check 4's controls, on the same rule as check 3's: a guard that cannot
    # fire reports every tree clean, so it proves it bites before it is
    # believed.
    broken4 = prove_check4_bites()
    if broken4:
        print("\n*** CHECK 4'S OWN CONTROLS FAILED ***\n"
              "    The recovered-name-table check no longer behaves as "
              "specified, so it cannot be believed.\n    Refusing to scan.\n")
        for b in broken4:
            print(f"    {b}")
        print(f"\nRESULT: FAIL -- {len(broken4)} of "
              f"{len(CHECK4_CONTROLS) + 1} check-4 control(s) failed; "
              f"{len(files)} file(s) NOT scanned")
        return 2

    # A tracked `.sanitize-local` is the one failure this whole mechanism
    # exists to prevent, so it is fatal on its own and reported first.
    tracked = local_list_is_tracked()

    # Check 4 itself: a recovered name table anywhere `git push` would carry.
    table_findings = []
    for p in files:
        for b in name_table_findings(p):
            table_findings.append((p.relative_to(REPO).as_posix(), b))

    view_bytes: dict[str, int] = {}
    path_stats: dict[str, int] = {}
    for p in files:
        bad = scan(p, local, view_bytes, vocab, path_stats)
        rel = p.relative_to(REPO).as_posix()
        if bad:
            findings.append((rel, bad))
        elif verbose:
            print(f"  ok   {rel}")

    print(f"\nscanned {len(files)} publishable file(s), "
          f"skipping {sorted(SKIP_DIRS)}")
    print(f"banned identifiers: {len(BANNED_TOKENS)} token, "
          f"{len(BANNED_SQUASHED)} squashed")
    # WHAT THE DECODED VIEWS ACTUALLY LOOKED AT. Printed on every run, next to
    # the verdict, because a view that decoded nothing has checked nothing --
    # and this whole mechanism exists because a check that could not fire was
    # reporting clean. A reader comparing "hex 0 B" against a tree they know
    # holds capture fixtures has been told something a bare PASS cannot say.
    if view_bytes:
        print("decoded views searched: "
              + ", ".join(f"{k} {v:,} B" for k, v in sorted(view_bytes.items())))
    else:
        print("decoded views searched: NONE -- no file in this tree carried "
              "hex, separated-hex or base64 runs, so the encoded-PII check "
              "did not fire on anything. That is a fact about the tree, not a "
              "pass.")
    # WHAT CHECK 3 ACTUALLY LOOKED AT, on the same principle. The old version
    # of this check could not fire on the layout it was guarding and the
    # summary line said "no hardcoded install paths" anyway. These four
    # numbers are what makes that visible: a reader who knows this tree holds
    # hundreds of Windows paths can see whether any reached the check.
    pos = sum(1 for _r, _t, want, _w in CHECK3_CONTROLS if want)
    print(f"check 3's own controls: {len(CHECK3_CONTROLS)} passed "
          f"({pos} that must be findings, {len(CHECK3_CONTROLS) - pos} that "
          f"must not) -- the check bit in both directions this run")
    print(f"install paths: {len(vocab)} resolvable name(s) from core/coroot.py, "
          f"matched against {path_stats.get('absolute paths', 0):,} "
          f"drive-rooted path(s) in {path_stats.get('scanned', 0)} in-scope "
          f"file(s); {path_stats.get('prose', 0)} prose file(s) and "
          f"{path_stats.get('allowlisted', 0)} allowlisted file(s) out of scope")
    if not path_stats.get("absolute paths"):
        print("  -- and NOT ONE drive-rooted path was in scope, so check 3 "
              "compared its vocabulary against nothing. That is a fact about "
              "the tree, not a pass.")

    if local[0] or local[1]:
        print(f"plus your .sanitize-local: {len(local[0])} token, "
              f"{len(local[1])} squashed (contents never printed)")
    else:
        print("no .sanitize-local found -- only the committed list is checked. "
              "See README 'Protecting your own identifiers'.")

    if tracked:
        print("\n*** .sanitize-local IS TRACKED BY GIT ***\n"
              "    That file holds your identifiers in plaintext and must never\n"
              "    be committed. Remove it from the index and rewrite any commit\n"
              "    that contains it:\n"
              "        git rm --cached .sanitize-local\n"
              "    It is listed in .gitignore, so this can only have happened\n"
              "    via `git add -f`.")
        print(f"\nRESULT: FAIL -- .sanitize-local is tracked; "
              f"{len(files)} file(s) scanned")
        return 1

    # Every verdict line below carries the count it was reached on. A verdict
    # with no count is invisible to the "does the summary agree with the work"
    # cross-check, which then passes by not applying -- CONTRIBUTING.md, "when
    # two readouts of one run disagree". Scanning zero files and finding
    # nothing is not a pass, and only the count can say which happened.
    ident = len(BANNED_TOKENS) + len(BANNED_SQUASHED) + len(local[0]) + len(local[1])

    # Stated with its count, on the same rule as every other verdict line
    # here: a check that scanned nothing and found nothing is not a pass.
    print(f"recovered name tables: {len(table_findings)} finding(s) over "
          f"{len(files)} publishable file(s); check 4's controls bit in both "
          f"directions this run ({len(CHECK4_CONTROLS) + 1} of them)")
    if table_findings:
        print("\n*** GAME-DERIVED DATA IS PUBLISHABLE FROM THIS TREE ***\n"
              "    This repository ships no game data -- that is what makes "
              "its MIT licence\n    unqualified. A name profile is a separate, "
              "opt-in artefact built by\n    tools/profilepack.py and written "
              "under out/, which .gitignore ignores whole.\n"
              "    A file here can only have arrived via `git add -f`.\n")
        for rel, b in table_findings:
            print(f"    {rel}\n        {b}")
        print(f"\nRESULT: FAIL -- {len(table_findings)} recovered name "
              f"table(s) publishable; {len(files)} file(s) scanned")
        return 1

    if findings:
        print(f"\n{len(findings)} FILE(S) WITH FINDINGS:\n")
        for rel, bad in findings:
            print(f"  {rel}")
            for b in bad:
                print(f"      {b}")
        print(f"\nRESULT: FAIL -- scanned {len(files)} file(s) against "
              f"{ident} identifier(s), {len(findings)} with findings")
        return 1
    # The verdict states the scope it was reached in. The previous wording --
    # "no hardcoded install paths" -- was a claim about the general class made
    # by a check that could only see one vendor prefix, and it stayed true-
    # sounding through 67 hardcoded paths. A summary that overstates its check
    # is part of the defect, not a description of it.
    print("\nno personal identifiers, no absolute user paths, and no "
          "drive-rooted path naming anything core/coroot.py resolves "
          "-- in executable code (python string literals that are not "
          "docstrings, plus non-prose files whole); prose is out of scope")
    print(f"RESULT: PASS -- scanned {len(files)} file(s) against "
          f"{ident} identifier(s), 0 with findings")
    return 0


def learn(history_path: str) -> int:
    """Print the two hash sets for a given `login_history.json`.

    Run this, paste the output over the sets above, and re-run the test.  The
    identifiers themselves are printed too so you can eyeball them -- do not
    paste *those* into the repo.
    """
    import json
    rows = json.loads(Path(history_path).read_text("utf-8"))
    names = {str(r.get(k) or "") for r in rows
             for k in ("username", "player_name") if r.get(k)}
    tokens, squashed = set(), set()
    for n in names:
        sq = "".join(c for c in n.lower() if c.isalnum())
        if len(sq) >= 10:
            squashed.add(sq)
        for t in TOKEN_RE.findall(n.lower()):
            if len(t) >= 3:
                tokens.add(t)
    tokens -= squashed
    print(f"# from {history_path}: {len(names)} identifiers")
    print("# tokens :", sorted(tokens))
    print("# squashed:", sorted(squashed))
    print("\nBANNED_TOKENS = {")
    for t in sorted(tokens):
        print(f'    "{h(t)}",')
    print("}\n\nBANNED_SQUASHED = {")
    for t in sorted(squashed):
        print(f'    "{h(t)}",')
    print("}")
    print("\nAlso keep the author-identity entries already in BANNED_SQUASHED "
          "(email, machine name, home directory).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
