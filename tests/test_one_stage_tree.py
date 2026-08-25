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
for _p in ("tools", "core"):
    _d = str(PROJECT / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import comod                                                # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
