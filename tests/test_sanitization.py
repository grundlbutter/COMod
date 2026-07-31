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
  3. **No hardcoded game install path.**  Exactly one file is allowed to
     contain the conventional install path -- `core/coroot.py`, where it is a
     discovery *hint* that gets validated, not an assumption.  Everything else
     must go through `coroot`.

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
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

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
    "7b8bd6c0abf53d22888beafc48830e11",
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

#: The conventional install path.  Allowed in exactly one place.
INSTALL_PATH_RE = re.compile(
    r"Program Files[^\"'\n]{0,12}[\\/]+[^\"'\\/\n]{0,24}Conquer",
    re.IGNORECASE)
INSTALL_PATH_ALLOWED = {"core/coroot.py",
                        "blender/io_scene_c3/vendor/coroot.py",
                        "tests/test_sanitization.py"}

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


def scan(path: Path, local: "tuple[set[str], set[str]] | None" = None) -> list[str]:
    """Findings for one file, as human sentences.  Empty means clean.

    `local` is `(tokens, squashed)` from `.sanitize-local`, compared as
    plaintext. Findings from it deliberately **do not name the identifier**:
    the whole point is that it stays private, and printing it into a build log
    or a screenshot of a failing run would undo that.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return [f"unreadable: {e}"]
    text = raw.decode("utf-8", errors="replace")
    rel = path.relative_to(REPO).as_posix()
    bad: list[str] = []
    local_tokens, local_squashed = local or (set(), set())

    toks = [t.lower() for t in TOKEN_RE.findall(text)]

    for t in set(toks):
        if h(t) in BANNED_TOKENS:
            bad.append(f"personal identifier present as the token {t!r}")
        elif t in local_tokens:
            bad.append(f"one of your .sanitize-local identifiers is present as "
                       f"a whole token ({len(t)} chars) -- not naming it here")

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
                           f"{len(joined)} chars)")
            elif joined in local_squashed:
                bad.append(f"one of your .sanitize-local identifiers is present "
                           f"at token {i} (squashed match over {w + 1} token(s)) "
                           f"-- not naming it here")

    for m in USER_PATH_RE.finditer(text):
        bad.append(f"absolute user path {m.group(0)!r} -- "
                   "make it relative to the repo or resolve it at runtime")

    if rel not in INSTALL_PATH_ALLOWED and INSTALL_PATH_RE.search(text):
        bad.append("hardcoded game install path -- resolve it through "
                   "core/coroot.py instead")
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

    # A tracked `.sanitize-local` is the one failure this whole mechanism
    # exists to prevent, so it is fatal on its own and reported first.
    tracked = local_list_is_tracked()

    for p in files:
        bad = scan(p, local)
        rel = p.relative_to(REPO).as_posix()
        if bad:
            findings.append((rel, bad))
        elif verbose:
            print(f"  ok   {rel}")

    print(f"\nscanned {len(files)} publishable file(s), "
          f"skipping {sorted(SKIP_DIRS)}")
    print(f"banned identifiers: {len(BANNED_TOKENS)} token, "
          f"{len(BANNED_SQUASHED)} squashed")
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
        print("\nRESULT: FAIL")
        return 1

    if findings:
        print(f"\n{len(findings)} FILE(S) WITH FINDINGS:\n")
        for rel, bad in findings:
            print(f"  {rel}")
            for b in bad:
                print(f"      {b}")
        print("\nRESULT: FAIL")
        return 1
    print("\nno personal identifiers, no absolute user paths, "
          "no hardcoded install paths")
    print("RESULT: PASS")
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
