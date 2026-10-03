#!/usr/bin/env python3
r"""
strtable.py -- the client's string table, and the format-signature gate that
makes it safe to point a table-driven message layer at a DIFFERENT build.

VibeCO renders its text FROM the shipped string table rather than from
hard-coded strings, so that the build it targets is a data choice rather than a
code commit.  That design has exactly one sharp edge, and this module exists to
put a gate on it.

THE SHARP EDGE, MEASURED (2026-09-20, over `strindex` across 38 builds)
----------------------------------------------------------------------
A key's format string can be REWORDED between patches in a way that moves its
argument slots.  Binding arguments positionally to a key whose slots have moved
does not crash -- it renders the wrong value, or reads past the end.

  * 21.5% of keys carry a format slot at all; the rest are static text.
  * **93 distinct keys move their slot signature** somewhere across the
    lineage (97 changes over 37 consecutive steps), measured against
    `strindex.sqlite` at PARSER_VERSION=2, 38 builds 5165..7878. COMod's
    independently written sweep converges on the SAME 93 keys exactly.

    *Two earlier figures were published and both were wrong, in OPPOSITE
    directions. 103/107 was too HIGH -- this parser counted C's space flag as
    a conversion (see SPEC), and the index was rebuilt underneath the
    measurement when 6772/6805 were re-decoded from latin1 to UTF-16. The
    correction to 89/93 was then too LOW, because `%S` was collapsed into
    `%s` (see `_CLASS`). Only the key-set DIFF against a second implementation
    found the last one; the totals had looked close enough to accept.*
  * **ZERO keys use POSIX positional specs** (`%1$s`).  Arguments are consumed
    strictly in order of appearance, so a slot inserted ANYWHERE shifts every
    argument after it.
  * The four observed modes: a slot PREPENDED (`STR_TASK_TRACE_COUNT_DOWN_TIME`
    gained a leading `<color=%x>`), a slot APPENDED, a static string GAINING a
    slot (`STR_PACKAGE_EMOTION_EQUIP_TITLE`, "Emoji Setup" -> "Press the `%s`
    key..."), and a width change.

**The length modifier is part of the signature and dropping it reports false
clean.**  15 of the 93 differ ONLY in length -- all `%d` <-> `%I64d`, which is
4 bytes against 8.  A conversion-class-only comparison calls those "no change".
12 of the 15 land at the 7622 -> 7867 step, where a family of counters was
widened to 64-bit.  One goes the other way: `STR_PACKAGE_FACE_MONEY` NARROWS
`%I64d` -> `%d` at 7589 -> 7622, and narrowing is the worse direction -- the
low 4 bytes of a small value render CORRECTLY, so the argument you would
spot-check looks right and everything after it is garbage.

NOT A DEFECT IN THE SHIPPING CLIENT.  Each build ships its code and its table
matched.  The corruption is created by binding one build's call sites to
another build's table, which is precisely what this module enables -- so the
gate is not optional.

WHY THIS DOES NOT USE `str.splitlines()`
----------------------------------------
`str.splitlines()` also breaks on `\x0b`, `\x0c`, `\x1c`-`\x1e`, `\x85`,
` ` and ` `.  The client's tables contain such bytes inside values,
and splitting on them silently truncates a value and invents a junk key from
its tail.  That exact defect hid 1,029 table declarations across 37 of 41
clients until 2026-09-20.  Lines here are split on CR/LF and nothing else.

Usage:

    from strtable import StringTable, signature

    base = StringTable.load_ini("Clients/7878/ini/Cn_Res.ini")
    base.format("STR_ROLE_TITL", 3, 1)          # verified, then rendered

    # the load-time gate: what a call site EXPECTS, checked against the table
    bad = base.verify({"STR_ROLE_TITL": ("INT", "INT")})
    if bad:
        raise SystemExit(bad[0].explain())
"""

import io
import re

__all__ = [
    "signature", "to_python_format", "StringTable", "Mismatch",
    "SignatureMismatch", "SPEC",
]

#: One C printf conversion specification.  `pos` is captured so a POSIX
#: positional spec can never be mistaken for an appearance-ordered one -- the
#: corpus has none, and a build that introduces one must fail loudly rather
#: than be bound positionally.
#:
#: **THE SPACE FLAG IS DELIBERATELY EXCLUDED, and this is a corpus decision, not
#: a parsing one.** C allows `% d` (space flag: a blank where a sign would go).
#: In this corpus it is NEVER that -- it is always prose containing a percent
#: sign. Measured across every indexed build: **6,664 space-flag matches in 16
#: forms**, `% c` 3,678 ("30% current level EXP"), `% o` 925 ("30% of current
#: EXP"), `% d` 519 ("...]% damage"), and `% to`, which is not a conversion at
#: all. Not one is a real slot.
#:
#: Including it read those prose strings as carrying CHR / UINT / INT slots and
#: inflated the lineage drift count from 93 changes to 111 -- 18 phantom moves,
#: every one of them a sentence being reworded around a percent sign.
#:
#: THE TRADE, stated because it is a real one: a genuine space-flagged
#: conversion is now invisible to this parser. None exists in this corpus, and
#: `tests/test_strtable.py` pins both halves -- that `% d` in prose is not a
#: slot, and that the exclusion is what makes it so.
SPEC = re.compile(r"""
    %
    (?P<pos>\d+\$)?
    (?P<flags>[-+\#0']*)
    (?P<width>\d+|\*)?
    (?P<prec>\.(?:\d+|\*)?)?
    (?P<len>hh|h|ll|l|L|q|j|z|t|I64|I32|I|w)?
    (?P<conv>[diouxXeEfFgGaAcCsSpn%])
""", re.X)

#: Conversion character -> the class of argument it consumes.
#:
#: **`%S` IS NOT `%s`, AND `%C` IS NOT `%c`.** In a narrow (char) printf the
#: capitals mean the WIDE variant: `%S` takes a `wchar_t*` where `%s` takes a
#: `char*`. Both are pointers, so nothing shifts on the stack -- which is
#: exactly why collapsing them is tempting and wrong. The callee reinterprets
#: the bytes: a `char*` rendered through `%S` reads ASCII pairs as UTF-16 code
#: units and prints CJK, and a `wchar_t*` through `%s` stops at the first NUL
#: byte, which for ASCII text is after ONE character.
#:
#: This collapse cost four keys. `STR_COACH_TARGET_CHANGES_NO_LEFT` and three
#: others move `%S` -> `%s` at 5165 -> 5517 and were invisible to this parser
#: while COMod's independent implementation caught all four -- the same defect
#: as a conversion-class comparison calling `%I64d` -> `%d` no-change, which
#: this module already refused to make for integers and then made for strings.
_CLASS = {
    "d": "INT", "i": "INT",
    "u": "UINT", "o": "UINT", "x": "UINT", "X": "UINT",
    "e": "FLT", "E": "FLT", "f": "FLT", "F": "FLT",
    "g": "FLT", "G": "FLT", "a": "FLT", "A": "FLT",
    "c": "CHR", "C": "WCHR",
    "s": "STR", "S": "WSTR",
    "p": "PTR", "n": "WRITEBACK",
}

#: Length modifiers Python's own `%` operator does not understand.  They carry
#: real ABI meaning (`I64` is 8 bytes where the default is 4) so they are KEPT
#: in the signature and only stripped when handing the string to Python.
_PY_DROP_LEN = ("hh", "h", "ll", "l", "L", "q", "j", "z", "t", "I64", "I32",
                "I", "w")

#: Python accepts `%d`/`%i`/`%u` but not the wide/pointer conversions.
_PY_CONV = {"S": "s", "C": "c", "F": "f", "p": "x", "u": "d", "i": "d"}


def signature(value):
    """The ordered tuple of argument-consuming slots in `value`.

    Length-aware: `%I64d` is ``INT:I64`` and does NOT compare equal to `%d`.
    `%%` consumes nothing and emits no slot.  A `*` width or `.*` precision
    each consume an extra int argument, emitted BEFORE their conversion in the
    order the client's varargs would read them.
    """
    out = []
    for m in SPEC.finditer(value):
        conv = m.group("conv")
        if conv == "%":                       # a literal percent, not a slot
            continue
        if m.group("width") == "*":
            out.append("INT*")
        if m.group("prec") == ".*":
            out.append("INT*")
        cls = _CLASS.get(conv, "?" + conv)
        if m.group("pos"):
            # Positional specs reorder arguments, so an appearance-ordered
            # binding is wrong by construction.  Tag it so equality fails.
            cls = "POSITIONAL:" + cls
        ln = m.group("len")
        out.append(cls + ":" + ln if ln else cls)
    return tuple(out)


def to_python_format(value):
    """`value` with each spec rewritten into one Python's `%` understands.

    Only the length modifier and the exotic conversions change; flags, width
    and precision are preserved, so `%02d` still pads.  This is a RENDERING
    convenience and is never used to decide whether two strings agree -- that
    is `signature`'s job, on the untranslated text.
    """
    def fix(m):
        if m.group("conv") == "%":
            return "%%"
        conv = _PY_CONV.get(m.group("conv"), m.group("conv"))
        return "%%%s%s%s%s" % (m.group("flags") or "", m.group("width") or "",
                               m.group("prec") or "", conv)
    return SPEC.sub(fix, value)


def _split_lines(text):
    r"""Split on CR/LF only.

    NOT `str.splitlines()`, which also breaks on `\x85` and friends and so
    truncates values that legitimately contain them.  See the module docstring.
    """
    return re.split(r"\r\n|\r|\n", text)


class Mismatch(object):
    """One key whose table signature is not what the caller expected."""

    __slots__ = ("key", "expected", "actual", "value", "table")

    def __init__(self, key, expected, actual, value, table):
        self.key = key
        self.expected = expected
        self.actual = actual
        self.value = value
        self.table = table

    def explain(self):
        if self.actual is None:
            return ("%s: key is ABSENT from %s (caller expected %s)"
                    % (self.key, self.table, list(self.expected)))
        return ("%s in %s\n    caller expects %s\n    table provides %s\n"
                "    value: %r"
                % (self.key, self.table, list(self.expected),
                   list(self.actual), self.value[:120]))

    def __repr__(self):
        return "<Mismatch %s expected=%r actual=%r>" % (
            self.key, self.expected, self.actual)


class SignatureMismatch(Exception):
    """Raised instead of rendering a string whose slots do not match the args.

    This is the whole point of the module: a LOUD failure at the call, rather
    than a plausible-looking line with the wrong numbers in it.
    """


#: Which slot classes a Python value may legally fill.
def _accepts(slot, arg):
    base = slot.split(":")[0]
    if base.startswith("POSITIONAL"):
        return False                       # never bind a positional spec
    if base in ("INT", "UINT", "INT*"):
        return isinstance(arg, int) and not isinstance(arg, bool)
    if base == "FLT":
        return isinstance(arg, (int, float)) and not isinstance(arg, bool)
    if base in ("STR", "WSTR"):
        # Python has no narrow/wide split, so both accept `str`.  The
        # distinction is kept in the SIGNATURE (where it detects drift between
        # builds) and dropped here (where it would reject a valid argument).
        return isinstance(arg, str)
    if base in ("CHR", "WCHR"):
        return isinstance(arg, str) and len(arg) == 1
    return False                           # PTR / WRITEBACK / unknown


class StringTable(object):
    """A build's `key -> value` table, with an optional fallback chain.

    The fallback is measured, not assumed: `ar_res.ini` shares the base key
    namespace (89 of its 90 keys exist in `cn_res.ini` at 7878), so an overlay
    can be laid over a base table and resolved per key.  It is an OVERLAY
    mechanism -- `ar_res` itself is a 90-key casino-feature overlay, not a
    localisation of the client, and there is no multi-locale dataset in the
    corpus.
    """

    def __init__(self, entries, name="<table>", fallback=None):
        self.entries = dict(entries)
        self.name = name
        self.fallback = fallback
        self._sig_cache = {}

    # ---- loading ---------------------------------------------------------
    @classmethod
    def load_ini(cls, path, encoding="utf-8", fallback=None, errors="strict"):
        """Read a `KEY=VALUE` table.

        `encoding` is explicit because the corpus is not uniform: four builds
        (6680/6707/6772/6805) ship a `cn_res.ini` that is neither strict UTF-8
        nor strict GBK.  Pass the encoding the caller measured; do not guess
        here, and do not silently fall back -- a mojibake table that loads is
        worse than one that refuses.
        """
        with io.open(path, "rb") as fh:
            raw = fh.read()
        text = raw.decode(encoding, errors)
        return cls(cls.parse(text), name=str(path), fallback=fallback)

    @staticmethod
    def parse(text):
        """`KEY=VALUE` lines -> dict.  First occurrence of a key wins."""
        out = {}
        for line in _split_lines(text):
            if not line or line[0] in ";#":
                continue
            eq = line.find("=")
            if eq <= 0:
                continue
            key = line[:eq].strip()
            if key and key not in out:
                out[key] = line[eq + 1:]
        return out

    # ---- lookup ----------------------------------------------------------
    def __contains__(self, key):
        return key in self.entries or (
            self.fallback is not None and key in self.fallback)

    def __len__(self):
        return len(self.entries)

    def get(self, key, default=None):
        """Resolve `key`, falling through to the fallback table per key."""
        if key in self.entries:
            return self.entries[key]
        if self.fallback is not None:
            return self.fallback.get(key, default)
        return default

    def signature_of(self, key):
        """`key`'s slot signature, or None if the key is absent entirely."""
        if key not in self._sig_cache:
            v = self.get(key)
            self._sig_cache[key] = None if v is None else signature(v)
        return self._sig_cache[key]

    # ---- the gate --------------------------------------------------------
    def verify(self, expectations):
        """Check `{key: expected_signature}` against the table.

        Returns a list of `Mismatch`.  Call this ONCE at load, with the
        signatures the call sites were written against; an empty list is the
        only safe state for positional binding.  An absent key is a mismatch,
        not a skip -- silence about a missing key is how a table swap goes
        unnoticed.
        """
        bad = []
        for key, expected in expectations.items():
            actual = self.signature_of(key)
            if actual is None:
                bad.append(Mismatch(key, tuple(expected), None, "", self.name))
            elif tuple(actual) != tuple(expected):
                bad.append(Mismatch(key, tuple(expected), actual,
                                    self.get(key, ""), self.name))
        return bad

    def signature_diff(self, other):
        """Keys shared with `other` whose signatures disagree.

        The build-swap check: run it between the table the code was written
        against and the one about to be loaded.
        """
        out = {}
        for key in self.entries:
            if key in other.entries:
                a, b = self.signature_of(key), other.signature_of(key)
                if a != b:
                    out[key] = (a, b)
        return out

    # ---- rendering -------------------------------------------------------
    def format(self, key, *args):
        """Render `key` with `args`, verifying the slots FIRST.

        Raises `SignatureMismatch` on a missing key, the wrong number of
        arguments, or an argument whose type cannot fill its slot.  It never
        renders a partially-correct line.
        """
        value = self.get(key)
        if value is None:
            raise SignatureMismatch("%s: absent from %s" % (key, self.name))
        sig = signature(value)
        if len(sig) != len(args):
            raise SignatureMismatch(
                "%s: table wants %d argument(s) %s, caller supplied %d -- %r"
                % (key, len(sig), list(sig), len(args), value[:120]))
        for i, (slot, arg) in enumerate(zip(sig, args)):
            if not _accepts(slot, arg):
                raise SignatureMismatch(
                    "%s: argument %d is %s, slot %d is %s -- %r"
                    % (key, i, type(arg).__name__, i, slot, value[:120]))
        return to_python_format(value) % args if args else to_python_format(value)
