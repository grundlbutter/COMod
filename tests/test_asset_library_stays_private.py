#!/usr/bin/env python3
r"""
test_asset_library_stays_private.py -- a game install must never be git-visible.

`ConquerAssets/Clients/<name>/` holds whole game installs. The COMod repo is
PUBLIC and its one-sentence claim is that it ships no game data, so a client
directory whose contents git can see is one `git add -A` from breaking that.

Protection used to be per-directory: every client folder carried its own
`.gitignore` (`*`, `!.gitignore`, `!.gitkeep`). **That only protects the
folders somebody remembered to create the pair for, and on 2026-08-27 it
failed twice in one day** -- `4274` left 5,973 files visible and `6271` left
70,045, the second arriving AFTER the first was fixed. A root-level rule now
covers every client directory including ones that do not exist yet.

This file is the other half. The rule is the construction; this is what
notices if the rule is ever edited away, or if a sibling tree appears that it
does not cover.

**It asserts the INVARIANT, not the rule.** "Nothing under `ConquerAssets` is
git-visible except placeholders" stays true whether that is achieved by a
root rule, by per-directory files, or by something later. A test that pinned
the rule's exact text would go red on a correct refactor and would say
nothing about a new sibling directory the text does not match -- which is the
failure mode being guarded against.

The live half only runs in a checkout that HAS an asset library (COMod does;
a bare co-client-re worktree does not), so the checker itself is driven from
fixtures that cannot skip. A guard whose only arm skips everywhere it runs is
not a guard.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

#: The only things that may be visible inside the asset tree. Both are
#: structural: `.gitignore` is the per-directory protection, `.gitkeep` keeps
#: an otherwise-empty directory in the tree.
PLACEHOLDERS = {".gitignore", ".gitkeep"}

ASSET_DIR = "ConquerAssets"


def _git(repo, *args):
    return subprocess.run(("git",) + args, cwd=str(repo), capture_output=True,
                          text=True, timeout=180)


def exposed(repo) -> list:
    """Paths under `ConquerAssets/` that git can see and should not.

    Uses git's own answer (`status --porcelain -uall`) rather than reading
    `.gitignore` and re-implementing its matching. Re-implementing it is how
    a checker ends up disagreeing with the tool whose behaviour is the thing
    that actually matters.
    """
    repo = Path(repo)
    if not (repo / ASSET_DIR).is_dir():
        return []
    r = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", ASSET_DIR)
    out = []
    for line in r.stdout.splitlines():
        rel = line[3:].strip().strip('"')
        if not rel:
            continue
        if Path(rel).name in PLACEHOLDERS:
            continue
        out.append(rel)
    return sorted(out)


def _make_repo(tmp, with_rule: bool):
    """A repo shaped like COMod's asset tree, with or without the guard."""
    repo = Path(tmp)
    _git(repo, "init", "-q")
    client = repo / ASSET_DIR / "Clients" / "9999" / "c3"
    client.mkdir(parents=True)
    (client / "art.dds").write_text("x", encoding="utf-8")
    (client.parent / "Conquer.exe").write_text("x", encoding="utf-8")
    (client.parent / ".gitkeep").write_text("", encoding="utf-8")
    rule = ("ConquerAssets/Clients/*/*\n"
            "!ConquerAssets/Clients/*/.gitignore\n"
            "!ConquerAssets/Clients/*/.gitkeep\n") if with_rule else ""
    (repo / ".gitignore").write_text(rule, encoding="utf-8")
    return repo


class TheCheckerSeesExposureWhenThereIsSome(unittest.TestCase):
    """Fixtures, so this file cannot pass by never looking at anything."""

    def test_an_unprotected_client_directory_is_REPORTED(self):
        # The acceptance arm. Without this, a checker that always returned []
        # would satisfy every other test here.
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(d, with_rule=False)
            found = exposed(repo)
            self.assertTrue(found, "an unprotected client was not reported")
            self.assertTrue(any(f.endswith("Conquer.exe") for f in found), found)
            self.assertTrue(any(f.endswith("art.dds") for f in found), found)

    def test_the_same_tree_with_the_rule_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(exposed(_make_repo(d, with_rule=True)), [])

    def test_placeholders_are_never_counted_as_exposure(self):
        # `.gitkeep` is deliberately visible -- it is how an empty directory
        # survives -- so it must not read as a leak.
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(d, with_rule=True)
            self.assertEqual([f for f in exposed(repo)
                              if Path(f).name in PLACEHOLDERS], [])

    def test_a_repo_with_no_asset_tree_reports_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            _git(repo, "init", "-q")
            self.assertEqual(exposed(repo), [])


class TheLiveCheckoutLeaksNothing(unittest.TestCase):
    """The one that matters, in the repo that actually holds the library."""

    def test_no_game_data_is_visible_to_git(self):
        if not (PROJECT / ASSET_DIR).is_dir():
            self.skipTest("no ConquerAssets/ in this checkout -- the fixture "
                          "class above covers the checker; this is the live "
                          "half and only COMod has the library")
        leaked = exposed(PROJECT)
        self.assertEqual(
            leaked[:20], [],
            f"{len(leaked)} file(s) under {ASSET_DIR}/ are visible to git. A "
            f"client directory is a whole game install and this repository is "
            f"public: one `git add -A` publishes it. Add the client to the "
            f"root .gitignore rule, or give it .gitignore + .gitkeep.")


if __name__ == "__main__":
    unittest.main()
