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
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = ["UnsafePath", "confine", "is_confined", "normalize"]


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
