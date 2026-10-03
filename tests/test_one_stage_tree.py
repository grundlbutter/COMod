#!/usr/bin/env python3
r"""There is ONE stage tree, and `comod.py` owns it.

**The defect, twice.** The staging layout moved from ``mods/`` to
``Installed/``. ``comod.py`` -- the tool that installs and reverts, and
therefore the only module whose opinion about where a staged file lives can
be wrong in a way the user sees -- was updated. The modules that *write*
into the stage tree were not.

The first time, it was ``coviewer.py``. It was fixed by importing
``comod.STAGE``, and a comment was left saying the two must never drift. The
comment was true and insufficient: it was advice attached to one call site,
not a check over all of them. Four more modules still had their own copy::

    tools/npcstage.py   STAGE = PROJECT / "mods" / "stage"
    tools/collect.py    STAGE = HERE.parent / "mods" / "stage"
    tools/mapedit.py    STAGE = _REPO / "mods" / "stage"
    tools/npcsplit.py   stage_root = PROJECT / "mods" / "stage"

So the bug came back through a different door: a user staged an NPC split
from the Swap UI, the files landed in ``mods/stage``, and Mod staging --
which reads ``comod.STAGE`` -- reported "Nothing is staged" over nine real
files. Nothing was broken and nothing was lost; the two halves were simply
looking at different directories.

**Why a test and not another comment.** The failure is silent in both
directions: the writer succeeds, the reader truthfully reports what it sees,
and no exception is raised anywhere. Only a comparison across modules can
see it, and only a test performs that comparison every run.

**A guard that had quietly stopped guarding.** ``npcsplit.py`` proved "this
command wrote nothing" by fingerprinting ``PROJECT/"mods"`` before and
after. Once staging moved, that guard watched a directory nothing writes to
-- so it would pass no matter what the command did. It now watches the tree
staging actually uses. That is checked here too, because a guard pointed at
the wrong tree is indistinguishable from a guard that works.

Mutation, tool output not prediction::

    npcstage.STAGE = PROJECT / "mods" / "stage"      -> 2 failing  (control)
    mapedit.STAGE  = _REPO / "mods" / "stage"        -> 2 failing
    unchanged                                        -> 0 failing

Each mutation reds BOTH the agreement check and the source sweep, which is
the intended overlap: the first catches a copy that disagrees today, the
second catches one that happens to agree today and will not after the next
layout move. Either alone would have missed one of the two times this
shipped.
"""
import io
import re
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
for _p in ("tests", "tools", "core"):
    _d = str(PROJECT / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import comod                                                # noqa: E402
import jssource                                             # noqa: E402

#: Every module that names the stage tree. Adding one here is cheap; leaving
#: one out is exactly the omission this file exists to catch, so the source
#: sweep below finds them independently of this list.
WRITERS = ("npcstage", "collect", "coviewer", "mapedit")


class OneStageTree(unittest.TestCase):

    def test_every_module_agrees_with_comod(self):
        """`comod.STAGE` is the definition. Everything else imports it."""
        for name in WRITERS:
            # Not every writer ships to every tree: COMod extracts a subset,
            # and `collect.py` is not in it. A missing module is skipped --
            # but ONLY after confirming its FILE is absent too, so a module
            # that exists and fails to import still reds this gate instead of
            # quietly dropping out of the comparison.
            try:
                mod = __import__(name)
            except ImportError:
                self.assertFalse(
                    (PROJECT / "tools" / (name + ".py")).is_file(),
                    "%s.py is on disk but will not import -- this gate cannot "
                    "check it, and silence would read as agreement" % name)
                continue
            self.assertTrue(
                hasattr(mod, "STAGE"),
                "%s has no STAGE -- if it stopped staging, drop it from "
                "WRITERS; if it renamed it, this gate is now blind" % name)
            self.assertEqual(
                mod.STAGE, comod.STAGE,
                "%s.STAGE is %s but comod installs from %s -- files staged "
                "by %s would be invisible to Mod staging"
                % (name, mod.STAGE, comod.STAGE, name))

    def test_no_module_re_derives_the_path_from_a_literal(self):
        """THE SWEEP, so a module nobody added to WRITERS is still caught.

        Agreement at runtime is not enough on its own: a fresh local copy
        that happens to be correct today is the same latent defect, and it
        was correct on the day it was written every previous time too."""
        bad = []
        for py in sorted((PROJECT / "tools").glob("*.py")):
            if py.name.startswith("test_") or py.name == "comod.py":
                continue
            src = io.open(py, encoding="utf-8").read()
            # An assignment whose right-hand side joins a "mods" or "stage"
            # path literal -- not prose, not a docstring reference.
            for m in re.finditer(
                    r"^\s*(?:STAGE|stage_root)\s*=\s*([^\n#]+)", src, re.M):
                rhs = m.group(1)
                if '"stage"' in rhs or "'stage'" in rhs:
                    bad.append("%s: %s" % (py.name, rhs.strip()))
        self.assertEqual(
            bad, [],
            "these build the stage path from literals instead of importing "
            "comod.STAGE:\n  " + "\n  ".join(bad))

    def test_the_wrote_nothing_guard_watches_the_tree_staging_uses(self):
        """A guard scoped to a directory nothing writes to always passes."""
        src = io.open(PROJECT / "tools" / "npcsplit.py", encoding="utf-8").read()
        self.assertNotIn(
            'tree_state(PROJECT / "mods")', src,
            "npcsplit's wrote-nothing guard watches PROJECT/mods, which "
            "staging no longer uses -- it cannot fail")
        self.assertIn(
            "tree_state(comod.STAGE.parent)", src,
            "npcsplit's wrote-nothing guard no longer watches the stage tree")


# ---------------------------------------------------------------------------
# THE WORDS, not only the code
# ---------------------------------------------------------------------------
#
# Everything above is about where files LAND. This is about where a person is
# TOLD they land. The code was fixed and the sentences were not: on
# 2026-10-03 eleven strings under tools/webui (the swap drawer's heading, its
# clear and install confirms, the map editor's install confirm among them),
# the writable warning coviewer serves to that drawer, the README and four
# places in docs/viewer.md still said `mods/stage`. Nothing in the code was
# wrong, so nothing above could red; a user following any of them opened a
# folder that is never written.

#: Where a user reads where staging goes. Pages are every file under
#: tools/webui; docs are the two that describe the viewer's write path.
USER_DOCS = ("README.md", "docs/viewer.md")
OLD_NAME = re.compile(r"mods[/\\]+stage")


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def stale_stage_names(files: dict) -> list:
    """``{path: text}`` -> every user-facing mention of the old tree.

    In JavaScript only string LITERALS count -- a comment explaining that the
    tree moved is for maintainers and is correct. In HTML, comments are
    skipped for the same reason. A document is read whole."""
    hits = []
    for path, text in sorted(files.items()):
        if path.endswith(".js"):
            lx = jssource.blank(text)
            for span in lx.literals:
                if OLD_NAME.search(jssource.literal_text(lx, span)):
                    hits.append("%s:%d" % (path, _line(text, span[0])))
            continue
        if path.endswith(".html"):
            text = re.sub(r"<!--.*?-->",
                          lambda m: re.sub(r"[^\n]", " ", m.group(0)),
                          text, flags=re.S)
        hits += ["%s:%d" % (path, _line(text, m.start()))
                 for m in OLD_NAME.finditer(text)]
    return hits


def user_facing_files() -> dict:
    webui = PROJECT / "tools" / "webui"
    paths = sorted(webui.glob("*.js")) + sorted(webui.glob("*.html"))
    paths += [PROJECT / d for d in USER_DOCS]
    return {p.relative_to(PROJECT).as_posix(): p.read_text(encoding="utf-8")
            for p in paths}


class TheWordsAgreeWithTheTree(unittest.TestCase):

    def test_no_user_facing_text_names_the_old_tree(self):
        files = user_facing_files()
        self.assertGreater(len(files), 20, "the sweep found almost nothing "
                           "to read -- it cannot answer and must not pass")
        bad = stale_stage_names(files)
        self.assertEqual(bad, [], "these tell the user staged files go to "
                         "mods/stage; comod installs from %s:\n  %s"
                         % (comod.STAGE, "\n  ".join(bad)))

    def test_the_fallback_names_agree_with_comod(self):
        """Where a page has to NAME the tree before the server has said, the
        name is comod's -- so the next layout move reds here instead of
        shipping a fourth stale string."""
        rel = comod.STAGE.relative_to(comod.PROJECT).as_posix()
        html = (PROJECT / "tools/webui/swap.html").read_text(encoding="utf-8")
        m = re.search(r'<code id="stage-dir">([^<]*)</code>', html)
        self.assertIsNotNone(m, "swap.html no longer has #stage-dir")
        self.assertEqual(m.group(1), rel)
        js = (PROJECT / "tools/webui/swappage.js").read_text(encoding="utf-8")
        m = re.search(r"return\s+STAGEDIR\s*\|\|\s*'([^']*)'", js)
        self.assertIsNotNone(m, "swappage.js stageDir() has no fallback")
        self.assertEqual(m.group(1), rel)
        import npcsplit                                     # noqa: PLC0415
        self.assertEqual(npcsplit.stage_label(), rel,
                         "npcsplit prints a different stage path than comod's "
                         "-- and the swap page shows that output verbatim")

    def test_MUST_FIRE_a_planted_old_name_is_found_where_a_user_reads_it(self):
        files = user_facing_files()
        html, js = "tools/webui/swap.html", "tools/webui/swappage.js"
        a = 'id="stage-dir">Installed/stage<'
        b = "return STAGEDIR || 'Installed/stage';"
        self.assertEqual(files[html].count(a), 1)
        self.assertEqual(files[js].count(b), 1)
        planted = dict(files)
        planted[html] = files[html].replace(a, 'id="stage-dir">mods/stage<')
        planted[js] = files[js].replace(b, "return STAGEDIR || 'mods/stage';")
        planted["README.md"] = files["README.md"] + "\nstaged into mods/stage/\n"
        hits = stale_stage_names(planted)
        self.assertEqual(sorted(h.split(":")[0] for h in hits),
                         ["README.md", html, js], hits)

    def test_a_comment_about_the_old_tree_is_not_a_finding(self):
        """The other direction: the sweep reads what a USER sees. swappage.js
        carries a comment that names `mods/stage` to explain the move, and
        index.html-style comments do the same; neither may red."""
        files = {"x.js": "// the tree moved from mods/stage\nvar a = 'ok';\n",
                 "y.html": "<!-- was mods/stage -->\n<p>Installed/stage</p>\n"}
        self.assertEqual(stale_stage_names(files), [])
        files["x.js"] += "var b = 'mods/stage';\n"
        self.assertEqual(stale_stage_names(files), ["x.js:3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
