#!/usr/bin/env python3
r"""
safepath.py -- join an untrusted relative path onto a trusted root, or refuse.

WHY THIS IS ONE MODULE AND NOT THREE CHECKS
-------------------------------------------
A security review of this tree found the same bug in three separate files, and
called it three findings:

  * `tools/coviewer.py`  `STAGE / logical`   -- stage/unstage write and delete
  * `core/coassets.py`  `self.root / logical` -- asset reads
  * `tools/comod.py`     `root / logical`    -- install into the game directory

All three take a path from outside the process and join it onto a root with no
check that the result stays under that root. On Windows, `Path("C:/a") / ".."`
is `C:/`, and `Path("C:/a") / "D:/evil"` is `D:/evil` -- `/` discards the left
side entirely when the right side is absolute. So every one of those joins can
be steered anywhere the process can write.

Fixing it three times in three styles is how one of them ends up subtly wrong
and stays wrong. So there is exactly one function, it is the only thing any of
those call sites is allowed to use, and it has its own tests.

WHAT "SAFE" MEANS HERE
----------------------
`confine(root, logical)` returns `root / logical` **only if** the fully
resolved result is inside the fully resolved root. Otherwise it raises
`UnsafePath`, loudly, naming what it objected to -- a silent `return None`
would turn an attack into a mysterious 404 and lose the evidence.

Resolution happens *before* the containment test, which is what makes this
robust against the cases a string check misses:

  * `..` in any position, including `a/../../b` and Windows `a\..\..\b`
  * an absolute right-hand side (`/etc/passwd`, `C:\Windows\...`, `\\server\share`)
  * a **symlink** inside the root pointing out of it -- `resolve()` follows it,
    so the containment test sees the real destination, not the link
  * `.` and doubled separators, which are merely noise but would defeat a
    naive `".." not in logical` test used together with them

The root itself is not a valid answer: these call sites always want a file
*under* the root, and returning the root would let `logical=""` mean "the
install directory", which no caller wants and `write_bytes` would fail on
confusingly.

WINDOWS SPECIFICS THAT ARE NOT PARANOIA
---------------------------------------
`NUL`, `CON`, `AUX`, `COM1`.. are **reserved device names** at every directory
level, regardless of extension: opening `stage/CON` does not create a file, it
talks to the console, and `stage/NUL` silently swallows writes. A path that
resolves inside the root can still be one of these, so they are rejected by
name. This is not a directory escape, but it is the same class of "the path
did not mean what the caller thought" bug, and this is the one place that
knows about paths.

A NUL byte is rejected outright: it truncates the path at the OS boundary in
some APIs, so what gets checked and what gets opened can differ.

A component that ends in a **space or a dot** is rejected, and a component
containing a **colon** is rejected.  Windows strips a trailing space/dot when
it opens a path, so `y.DMap ` is checked under one name and written under
another (`y.DMap`) -- the same "the path did not mean what the caller thought"
class as the reserved names, and an integrity attack against a call site that
processes attacker-ordered keys and keeps the first write (`colibrary`).  A
colon opens an NTFS **alternate data stream** (`x.DMap:hidden`), a readable
payload absent from the directory listing.  Everything here stays *under* the
root -- these are integrity, not escape -- but "under the root" is not the same
as "the file the caller named".  `C-2026-08-12-quickfix-confine-collapse`.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = ["UnsafePath", "confine", "confined_files", "is_confined", "normalize"]


class UnsafePath(ValueError):
    """A logical path would leave its root, or cannot be used as a file name.

    Deliberately a `ValueError` subclass so an existing `except ValueError`
    around a path join keeps working, and deliberately loud rather than a
    `None` return: at every call site this means either a bug or an attack,
    and both deserve a traceback with the offending string in it.
    """

    def __init__(self, logical: str, reason: str) -> None:
        super().__init__(f"unsafe path {logical!r}: {reason}")
        self.logical = logical
        self.reason = reason


#: Windows reserved device names, checked per path component, extension
#: stripped. Harmless on POSIX; rejected everywhere so that a mod tree or a
#: capture made on Linux cannot produce a path that detonates on Windows.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def normalize(logical: str) -> str:
    """A logical path in the one spelling this project uses: forward slashes,
    no leading separator, no `.` components.

    Does **not** make a path safe -- `..` survives this untouched, on purpose.
    Safety is `confine`'s job; this is only about spelling, and the two are
    kept separate so that no caller can mistake normalisation for validation.
    """
    if not isinstance(logical, str):
        raise UnsafePath(repr(logical), f"expected a string, got {type(logical).__name__}")
    s = logical.replace("\\", "/")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    return "/".join(parts)


def confine(root: os.PathLike | str, logical: str) -> Path:
    """`root / logical`, guaranteed to be under `root`, or `UnsafePath`.

    `root` is trusted (it comes from `coroot` or a module constant); `logical`
    is not (it comes from an HTTP query string, a staged tree, or a manifest).
    """
    if not isinstance(logical, str):
        raise UnsafePath(repr(logical),
                         f"expected a string, got {type(logical).__name__}")
    if "\x00" in logical:
        raise UnsafePath(logical, "contains a NUL byte")

    # Reject an absolute right-hand side before joining, because `/` would
    # silently discard the root. Both flavours are checked regardless of the
    # host OS: a manifest written on one must not mean something else on the
    # other.
    if PurePosixPath(logical).is_absolute() or PureWindowsPath(logical).is_absolute():
        raise UnsafePath(logical, "is absolute; logical paths must be relative")
    if PureWindowsPath(logical).drive:
        raise UnsafePath(logical, "names a drive")

    rel = normalize(logical)
    if not rel:
        raise UnsafePath(logical, "is empty; a file under the root is required")

    for part in rel.split("/"):
        if part == "..":
            continue                      # caught by the containment test below
        # Windows COLLAPSES a trailing space or dot when it opens a path, so a
        # component validated as `y.DMap ` is written as `y.DMap` -- a
        # different file than the one checked. In `colibrary`, whose keys come
        # from a third-party filemap and whose loop is attacker-ordered, listing
        # `foo.DMap ` before `foo.DMap` overwrites the legitimate file while the
        # real entry is skipped and reported "kept". And `nul ` collapses to the
        # reserved device the check below would otherwise catch. The check must
        # see the name the OS will resolve, so reject any component that is not
        # already in its collapsed form. (CONFIRMED integrity attack,
        # `C-2026-08-12-quickfix-confine-collapse`; RE DX9.)
        if part != part.rstrip(" ."):
            raise UnsafePath(
                logical,
                f"component {part!r} ends in a space or dot, which Windows "
                f"strips on open -- it would resolve to a different name than "
                f"the one checked")
        # A colon opens an NTFS alternate data stream: `x.DMap:hidden` stores a
        # readable payload invisible to a directory listing, with the carrier's
        # size unchanged, so any integrity check that enumerates or hashes files
        # misses it. A logical asset path never contains one (the drive form
        # `C:` is already rejected above).
        if ":" in part:
            raise UnsafePath(
                logical,
                f"component {part!r} contains ':' -- an NTFS alternate data "
                f"stream, not a file under the root")
        stem = part.split(".", 1)[0].lower()
        if stem in _RESERVED:
            raise UnsafePath(logical,
                             f"component {part!r} is a Windows reserved device name")

    root_res = Path(root).resolve()
    # `strict=False` is the default and is what we want: the destination of a
    # stage or install usually does not exist yet. Resolution still collapses
    # `..` and follows any symlink that *does* exist along the way, which is
    # the case a string check cannot see.
    dest = (root_res / rel).resolve()

    if dest == root_res:
        raise UnsafePath(logical, "resolves to the root itself, not a path under it")
    if not _is_relative_to(dest, root_res):
        raise UnsafePath(logical, f"resolves to {dest}, which is outside {root_res}")
    return dest


def confined_files(root: os.PathLike | str) -> tuple[list[Path], list[tuple[Path, str]]]:
    r"""`(kept, refused)` -- every regular file under `root` that is *really*
    under `root`, and every entry that only looked like it was.

    WHY THIS IS HERE AND NOT IN THE TWO CALLERS
    -------------------------------------------
    `confine` guards a path a caller is about to *write to*. This guards the
    other direction: a tree a caller is about to *read from and copy out of*.
    `tools/comod.py` and `tools/coviewer.py` each had a byte-identical

        sorted(p for p in STAGE.rglob("*") if p.is_file())

    and `comod.cmd_install` then `shutil.copy2`'d the result into a game
    install. The destination of that copy is confined; the source was not, and
    the source is the half a third-party mod controls. Two copies of the fix is
    how one of them ends up subtly wrong -- the argument this whole module is
    built on -- so there is one function.

    MEASURED on Windows / Python 3.14.6, with an honest file as the control:

        c3/texture/002135300.dds  resolves_inside=True   b'DDS legit'          <- control
        junction/loot.txt         resolves_inside=False  b'OUTSIDE-THE-STAGE'
        junction/secrets/id_rsa   resolves_inside=False  b'PRIVATE-KEY'
        c3/hard.dds               resolves_inside=True   b'OUTSIDE-THE-STAGE'  <- SEE BELOW

    A **directory junction** is the live vector: `mklink /J` needs no
    privilege, `rglob` recurses straight through one, and `Path.is_symlink()`
    reports **False** for it -- so `recurse_symlinks=False`, the 3.13+ default,
    does not cover it. A **symlink** is the vector the old comment named and is
    the *harder* one to produce: `os.symlink` raises `WinError 1314` without
    the privilege, so it needs Developer Mode.

    WHAT THIS DOES NOT CATCH, STATED SO NOBODY READS IT AS COMPLETE
    ---------------------------------------------------------------
    **Hard links.** The fourth row above is kept, deliberately and unavoidably:
    a hard link is a second directory entry for the same file, so the path
    genuinely *is* under `root` and `resolve()` has nothing to object to. The
    only signal is `st_nlink > 1`, which is not one -- ordinary files carry
    extra links for ordinary reasons, and a hard link to a file the user
    already owns is not an escape at all. So this closes the junction and
    symlink rows and names the third rather than implying it is covered.

    Unlike `confine` this does **not** raise: listing a staged tree is exactly
    the case `is_confined` exists for -- one bad entry is skipped and reported,
    not fatal. **The `refused` half is the load-bearing half.** A caller that
    drops it turns an attack into a shorter file list, which is the silent
    outcome the whole module exists to prevent, and
    `tests/test_boundary_guards.py` gates against exactly that.
    """
    base = Path(root)
    if not base.is_dir():
        return [], []
    real_base = base.resolve()
    kept: list[Path] = []
    refused: list[tuple[Path, str]] = []
    for p in sorted(base.rglob("*")):
        try:
            if not p.is_file():
                continue
            real = p.resolve()
        except OSError as e:                    # a broken link, a denied entry
            refused.append((p, f"cannot be resolved: {e}"))
            continue
        if _is_relative_to(real, real_base):
            kept.append(p)
        else:
            refused.append((p, f"resolves to {real}, which is outside {real_base}"))
    return kept, refused


def is_confined(root: os.PathLike | str, logical: str) -> bool:
    """`confine` as a predicate, for callers that want to filter rather than
    fail -- listing a staged tree, say, where one bad entry should be skipped
    and reported rather than aborting the listing."""
    try:
        confine(root, logical)
        return True
    except UnsafePath:
        return False


def _is_relative_to(path: Path, other: Path) -> bool:
    """`Path.is_relative_to`, which is 3.9+. Spelled out because this module is
    also vendored into the Blender addon, which runs on whatever Python the
    installed Blender ships."""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    if len(sys.argv) != 3:
        print(f"usage: {Path(sys.argv[0]).name} ROOT LOGICAL", file=sys.stderr)
        raise SystemExit(2)
    try:
        print(confine(sys.argv[1], sys.argv[2]))
    except UnsafePath as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        raise SystemExit(1)
