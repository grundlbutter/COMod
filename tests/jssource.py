#!/usr/bin/env python3
r"""jssource.py -- read a browser `.js` file structurally, without a runtime.

There is **no standalone JavaScript engine on this box** -- node, deno, bun,
qjs and d8 are all absent, and no Python JS engine is installed. Most gates
over `tools/webui/*.js` therefore check the *source*, not the running file,
and the honest way to do that is the one `tests/test_billboard_basis.py`
records: parse the expression and apply it, rather than asking whether a token
is present.

    CORRECTED 2026-09-10. This paragraph used to read "no JavaScript engine on
    this box ... every gate therefore checks the source", and BOTH broad words
    were wrong. Chrome is installed, `tools/cdp.py` drives it, and
    `tests/test_effect_preview.py` has been running `effects.js` and
    `portagepanel.js` in it -- a real V8, on the real served page, asserting
    runtime values. So a RUNTIME gate over a webui file is available, and this
    module is a choice rather than the only option: it needs no browser, runs
    in milliseconds, and answers structural questions a running page cannot
    (whether a token is in code or in a string). Reach for it for those. Reach
    for `BrowserFixture` when the question is what the browser DOES -- a
    Content-Security-Policy decision, for one, which no source gate can see.

    The over-broad version cost something before it was caught: it was carried
    into a PI handoff as a standing constraint on the seat, where it read as
    "runtime gates are impossible here" and would have justified not writing
    one. **A narrow limit recorded against a broad thing** -- the register's
    costliest shape. The smallest true statement is the one above.

The failure this module exists to make avoidable is written down in that
file, measured::

    tools/test_viewer.py asserted `_cameraBasis` CONTAINS "view[0]".
    Flipping the sign to `-view[0]` still contains "view[0]"  ->  0 failing.

A substring check dies to a rename and is blind to the defect it was written
for. To do better a gate needs to know where code actually *is* -- which call
is inside which callback, which text is a string and which is syntax -- and
that needs a lexer. This is that lexer, deliberately small:

* :func:`blank` returns the source with every comment, string body, template
  body and regex body replaced by spaces **of equal length**, so offsets in
  the result address the same characters as offsets in the original. Brace
  and paren matching over the blanked text cannot be fooled by a brace inside
  a string or a `(` inside a regex -- both of which occur in `pageshot.js`.
* The literal spans are returned alongside, so a gate that wants the *text* a
  file builds can read the pieces back out in source order.

WHAT THIS IS NOT
----------------
It is not a JavaScript parser and must never be mistaken for one. It knows
nothing of ASI, arrow bodies, classes or scope. It answers exactly two
questions -- "is this offset inside code or inside text?" and "which bracket
matches this one?" -- and every gate built on it states its own blind spot
separately. The `${...}` interpolations inside a template literal are treated
as CODE (they are), so their parens participate in matching.

The regex-vs-divide decision uses the usual previous-significant-character
heuristic. It is a heuristic; :func:`balanced` exists so a caller can assert
the lex succeeded before trusting anything built on it, and every gate here
calls it. A file this heuristic mis-lexes will fail that assertion loudly
rather than quietly answering the wrong question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: A `/` here starts a regex literal, not a division. Anything that can end an
#: expression (an identifier, a digit, `)`, `]`) means division instead.
_REGEX_OK_AFTER = set("(,=:[!&|?{};+-*%~^<>") | {""}


@dataclass
class Lexed:
    """A blanked source plus the spans of the text that was blanked."""

    src: str
    text: str
    #: (start, end) of each string/template literal, delimiters INCLUDED.
    literals: list = field(default_factory=list)
    #: (start, end) of each comment.
    comments: list = field(default_factory=list)

    def balanced(self) -> bool:
        return all(self.text.count(a) == self.text.count(b)
                   for a, b in ("()", "[]", "{}"))

    def match(self, open_at: int) -> int:
        """Index just past the bracket closing the one at ``open_at``."""
        pairs = {"(": ")", "[": "]", "{": "}"}
        opener = self.text[open_at]
        if opener not in pairs:
            raise AssertionError(f"offset {open_at} is not an opening bracket")
        closer = pairs[opener]
        depth = 0
        for k in range(open_at, len(self.text)):
            if self.text[k] == opener:
                depth += 1
            elif self.text[k] == closer:
                depth -= 1
                if depth == 0:
                    return k + 1
        raise AssertionError(
            f"no closing '{closer}' for the '{opener}' at offset {open_at} -- "
            "the file does not lex, so this gate cannot answer and must not pass")

    def statement_end(self, start: int) -> int:
        """Index of the first `;` at bracket depth 0 at or after ``start``."""
        depth = 0
        for k in range(start, len(self.text)):
            c = self.text[k]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ";" and depth == 0:
                return k
        raise AssertionError("no statement terminator found")

    def literal_spans_in(self, start: int, end: int) -> list:
        return [(a, b) for a, b in self.literals if start <= a and b <= end]


def blank(src: str) -> Lexed:
    """Blank out comments and literal text, preserving every offset."""
    out = list(src)
    literals: list = []
    comments: list = []
    i, n = 0, len(src)
    prev = ""

    def space(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j))
            space(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            comments.append((i, j))
            space(i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            literals.append((i, j))
            space(i + 1, j - 1)
            i, prev = j, c
            continue
        if c == "`":
            # Template body is text; `${...}` is CODE and stays visible, so
            # its parens take part in bracket matching.
            j, depth = i + 1, 0
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if depth == 0 and src[j] == "`":
                    break
                if depth == 0 and src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                    depth, j = 1, j + 2
                    continue
                if depth:
                    if src[j] == "{":
                        depth += 1
                    elif src[j] == "}":
                        depth -= 1
                    j += 1
                    continue
                if src[j] != "\n":
                    out[j] = " "
                j += 1
            j = min(j + 1, n)
            literals.append((i, j))
            i, prev = j, "`"
            continue
        if c == "/" and prev in _REGEX_OK_AFTER:
            j, klass = i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    klass = True
                elif src[j] == "]":
                    klass = False
                elif src[j] == "\n" or (src[j] == "/" and not klass):
                    break
                j += 1
            space(i + 1, j)
            i, prev = j + 1, "/"
            continue
        if not c.isspace():
            prev = c
        i += 1

    return Lexed(src=src, text="".join(out), literals=literals, comments=comments)


_ESCAPES = {"\\n": "\n", "\\t": "\t", "\\'": "'", '\\"': '"', "\\`": "`",
            "\\\\": "\\"}


def split_interpolations(raw: str) -> list:
    """Split a template body into `(text, expr_or_None)` pieces, brace-aware.

    A naive ``\\$\\{[^{}]*\\}`` cannot express this and fails SILENTLY -- it
    simply does not match, the `${...}` survives verbatim into whatever the
    caller assembles, and a gate that then parses the result is measuring the
    wrong string. `pageshot.js` is exactly that case::

        ${css.replace(/[<>&]/g, c => ({ '<': '&lt;', ... }[c]))}

    The object literal's braces put the expression out of reach of the simple
    pattern, and the unresolved `${...}` carries raw `<`, `>` and `&` into a
    document the gate is about to hand to an XML parser. So match braces.

    Quoted spans are skipped so a `}` inside a string does not close the
    interpolation. This is the same heuristic level as :func:`blank` -- it does
    not know arrow bodies or nested templates -- and an unterminated `${` is
    returned as literal text rather than swallowing the rest of the body.
    """
    out: list = []
    i, n, text_from = 0, len(raw), 0
    while i < n:
        if raw[i] == "$" and i + 1 < n and raw[i + 1] == "{":
            j, depth = i + 2, 1
            while j < n and depth:
                c = raw[j]
                if c in "'\"`":
                    quote, j = c, j + 1
                    while j < n and raw[j] != quote:
                        j += 2 if raw[j] == "\\" else 1
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if not depth:
                        break
                j += 1
            if depth:                       # unterminated -- treat as text
                break
            out.append((raw[text_from:i], raw[i + 2:j]))
            i = text_from = j + 1
            continue
        i += 1
    out.append((raw[text_from:], None))
    return out


def literal_text(lx: Lexed, span, interp=None) -> str:
    """The text of one literal, with `${...}` resolved by ``interp``.

    ``interp`` is called with the raw expression source (without the ``${}``)
    and returns the text to stand in its place; the default stands in a token
    that is inert in both XML text and an XML attribute value.
    """
    a, b = span
    raw = lx.src[a + 1:b - 1]
    raw = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)
    for k, v in _ESCAPES.items():
        raw = raw.replace(k, v)
    out = []
    for text, expr in split_interpolations(raw):
        out.append(text)
        if expr is not None:
            out.append("STUB" if interp is None else interp(expr))
    return "".join(out)


def concat_literals(lx: Lexed, start: int, end: int, interp=None,
                    skip=()) -> str:
    """Join every literal in ``[start, end)`` in source order.

    ``skip`` is a list of (a, b) ranges whose literals belong to something the
    caller handles itself.
    """
    out = []
    for span in lx.literal_spans_in(start, end):
        if any(a <= span[0] and span[1] <= b for a, b in skip):
            continue
        out.append(literal_text(lx, span, interp))
    return "".join(out)


def calls_to(lx: Lexed, name: str) -> list:
    """Offsets of every call `name(`, excluding its own `function name(`.

    A member call `obj.name(` is excluded too: this answers "who invokes the
    free function", which is the question a reachability gate asks.
    """
    hits = []
    for m in re.finditer(r"(?<![\w.$])" + re.escape(name) + r"\s*\(", lx.text):
        before = lx.text[max(0, m.start() - 40):m.start()]
        if re.search(r"function\s+$", before):
            continue
        hits.append(m.start())
    return hits


def callback_spans(lx: Lexed, method: str, event: str) -> list:
    """(start, end) of every `x.method('event', ...)` argument list."""
    spans = []
    for m in re.finditer(r"(?<![\w$])" + re.escape(method) + r"\s*\(", lx.text):
        open_at = m.end() - 1
        end = lx.match(open_at)
        arg0 = lx.src[open_at:end]
        if f"'{event}'" in arg0 or f'"{event}"' in arg0:
            spans.append((open_at, end))
    return spans
