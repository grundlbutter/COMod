#!/usr/bin/env python3
r"""Input handlers must not do synchronous redraws.

**The defect this exists to stop, and why the browser makes it worse than it
looks.** ``gl.js`` binds ``wheel`` with ``{ passive: false }`` -- it has to,
because it calls ``preventDefault()`` to zoom instead of scroll. A
non-passive wheel listener is a *blocking* step in the browser's input
pipeline: the compositor cannot scroll anything until the handler returns.

So when that handler called ``this.draw()`` -- a full synchronous WebGL
redraw -- the cost was not "the canvas is a bit late". It was one redraw per
EVENT, on the main thread, in front of the scroll. High-resolution and
free-spin wheels emit many events per notch, so one flick could queue dozens
of redraws, and every one of them held up input handling.

``mapedit.js`` already had this right: its ``onWheel`` accumulates and calls
``schedule()``. ``gl.js`` did not, and the swap page loads ``gl.js``. That
divergence -- the same problem solved in one file and not its neighbour --
is the shape this project keeps paying for, so the check below covers BOTH
files rather than the one that had the bug.

**What is asserted, and what is deliberately not.** This does not measure
frame time; a timing assertion in CI is a flake generator. It asserts the
structural property that makes the timing impossible to get wrong: no input
handler body contains a direct synchronous draw call, and the coalescer it
uses instead is guarded by a pending flag so repeated calls collapse.

Mutation, tool output not prediction::

    put `this.draw()` back in the wheel handler        -> 1 failing  (control)
    drop the `if (this._drawPending) return;` guard    -> 1 failing
    unchanged                                          -> 0 failing

The first is the control: it proves this gate runs and can red on exactly
the code that shipped. The second proves it is not merely matching the name
``_drawSoon`` -- a coalescer without its guard is not a coalescer, and would
schedule one animation frame per event instead of one per frame.
"""
import io
import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
WEBUI = PROJECT / "tools" / "webui"


def read(name):
    return io.open(WEBUI / name, encoding="utf-8").read()


def brace_body(src, start):
    """The `{...}` body beginning at or after `start`, brace-matched."""
    i = src.index("{", start)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError("unbalanced braces from offset %d" % start)


def listener_body(src, event):
    """The body of the `addEventListener('<event>', ...)` arrow function."""
    m = re.search(r"addEventListener\(\s*['\"]%s['\"]\s*,\s*[^{]*" % event, src)
    if not m:
        return None
    return brace_body(src, m.end() - 1)


class InputHandlersDoNotRedrawSynchronously(unittest.TestCase):
    """One redraw per animation frame, never one per event."""

    #: The handlers that sit in front of scrolling and dragging. `wheel` is
    #: the one that blocks the compositor; `mousemove` fires at device rate
    #: during a drag and has the same shape of cost.
    BLOCKING = ("wheel", "mousemove")

    def test_gl_js_input_handlers_do_not_call_draw_directly(self):
        src = read("gl.js")
        for event in self.BLOCKING:
            body = listener_body(src, event)
            self.assertIsNotNone(
                body, "gl.js no longer binds '%s' -- if that is deliberate, "
                      "update this gate; if not, the binding was lost" % event)
            self.assertNotIn(
                "this.draw()", body,
                "gl.js's '%s' handler calls this.draw() directly. That is a "
                "synchronous WebGL redraw per EVENT, inside a non-passive "
                "listener the compositor waits on. Use this._drawSoon()."
                % event)
            self.assertIn(
                "_drawSoon", body,
                "gl.js's '%s' handler neither draws nor schedules a draw -- "
                "the camera would move with nothing repainting it" % event)

    def test_the_coalescer_actually_coalesces(self):
        """A `_drawSoon` with no pending guard schedules a frame per event.

        That still repaints once per frame, so it LOOKS fixed, while the
        callback queue grows with every event. The guard is the thing that
        makes it a coalescer, so the guard is what gets asserted."""
        src = read("gl.js")
        m = re.search(r"_drawSoon\s*\(\s*\)\s*", src)
        self.assertIsNotNone(m, "gl.js has no _drawSoon")
        body = brace_body(src, m.end())
        self.assertIn("requestAnimationFrame", body,
                      "_drawSoon does not defer to an animation frame")
        self.assertRegex(
            body, r"if\s*\(\s*this\._drawPending\s*\)\s*return",
            "_drawSoon has no early return on a pending frame, so it "
            "schedules one callback per event instead of one per frame")
        self.assertRegex(
            body, r"this\._drawPending\s*=\s*0",
            "_drawSoon never clears _drawPending, so after the first frame "
            "it would return early forever and the canvas would freeze")

    def test_mapedit_keeps_the_pattern_it_already_had(self):
        """The neighbour that was already right. Checked here so the two
        cannot drift apart again -- which is how gl.js came to differ."""
        src = read("mapedit.js")
        m = re.search(r"function\s+onWheel\s*\([^)]*\)\s*", src)
        self.assertIsNotNone(m, "mapedit.js has no onWheel")
        body = brace_body(src, m.end())
        self.assertNotIn("draw()", body,
                         "mapedit's onWheel draws synchronously; it used to "
                         "call schedule() and should still")
        self.assertIn("schedule()", body,
                      "mapedit's onWheel no longer schedules a repaint")


if __name__ == "__main__":
    unittest.main(verbosity=2)
