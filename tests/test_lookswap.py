#!/usr/bin/env python3
r"""`tools/lookswap.py` -- the straight-replacement planner.

**What this gate is for.** `lookswap` is the only thing that writes staged
files for the kinds that do not split (monster, weapon, mount, body, head).
Two properties decide whether it is safe to ship:

1. It writes **only** inside `comod.STAGE`. Everything it stages is named by
   data read out of the install -- a logical path from the catalogue -- and a
   path that climbs out of the stage tree would let a swap overwrite the game,
   or anything else on disk. `safepath.confine` is what stops that, and the
   test below proves the refusal by attempting the traversal, not by reading
   the source and seeing the call.
2. **The preview and the act describe the same change.** The panel offers
   "Show me what to change" and "Stage this replacement" as separate buttons;
   if those two could disagree, the preview would be a lie. They are the same
   function with `dry` toggled, and that is asserted rather than assumed.

**Why a fake catalogue.** Building a real one needs a game install, ~24 s,
and a mesh/texture index -- so a test using one would be slow, would skip on
any machine without CCO, and would pass vacuously when it skipped. The fake
implements exactly the surface `lookswap` uses (`models.query`,
`builder.options`, `assets.exists`, `assets.read`), which also documents that
surface. Every refusal below is reachable with no install at all.

Mutation, tool output not prediction::

    drop the safepath.confine call in stage()      -> 1 failing  (control)
    let plan() accept target == donor              -> 1 failing
    make stage() re-plan instead of using the plan -> 2 failing
    unchanged                                      -> 0 failing

The third reds two checks rather than one, and the second is the interesting
one: a `stage()` that re-plans also loses the traversal refusal, because the
path it writes no longer comes from the plan it was handed. Re-planning is
not just a preview/act mismatch -- it takes the safety property with it.

The recolour half was added after the coupling broke in practice, so its
mutations are the defect that actually happened rather than invented ones::

    look_rows stops adding morphs                  -> 1 failing
      (this IS the bug: browse offered 148 monster looks while the planner
       knew 66, so 82 rows would have been refused at the moment of the swap)
    coviewer re-derives split_colour itself        -> 1 failing
    an unusable morph carries no reason            -> 1 failing
    unchanged                                      -> 0 failing
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
for _p in ("tools", "core"):
    _d = str(PROJECT / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import lookswap                                            # noqa: E402
import safepath                                            # noqa: E402


class FakeModel:
    def __init__(self, d):
        self._d = d

    def to_json(self):
        return dict(self._d)


class FakeModels:
    def __init__(self, rows):
        self._rows = rows

    def query(self, kind, text="", playable_only=False):
        return {"matched": [FakeModel(r) for r in self._rows]}


class FakeOption:
    def __init__(self, ident, name, mesh, texture):
        self.ident, self.name = ident, name
        self.mesh, self.texture = mesh, texture


class FakeBuilder:
    def __init__(self, options):
        self.options = options


class FakeAssets:
    """Only what lookswap asks of an AssetRoot."""

    def __init__(self, files):
        self._files = files

    def exists(self, rel):
        return rel in self._files

    def read(self, rel):
        return self._files.get(rel, b"")


class FakeCat:
    def __init__(self, models=None, builder=None, files=None):
        self.models = FakeModels(models or [])
        self.builder = builder
        self.assets = FakeAssets(files or {})


MONSTERS = [
    {"id": "103", "label": "Monster 103", "mesh": "c3/monster/103/100.c3",
     "texture": "c3/texture/103000000.dds"},
    {"id": "105", "label": "Monster 105", "mesh": "c3/monster/105/100.c3",
     "texture": "c3/texture/105000000.dds"},
    {"id": "108", "label": "Monster 108", "mesh": "",
     "texture": "c3/texture/108000000.dds"},
]
FILES = {
    "c3/monster/103/100.c3": b"TARGET-MESH",
    "c3/texture/103000000.dds": b"TARGET-TEX",
    "c3/monster/105/100.c3": b"DONOR-MESH-BYTES",
    "c3/texture/105000000.dds": b"DONOR-TEX-BYTES",
    "c3/texture/108000000.dds": b"X",
}


def monster_cat():
    return FakeCat(models=MONSTERS, files=FILES)


class ThePlanRefusesRatherThanGuessing(unittest.TestCase):
    """Each of these is a way a swap could go wrong quietly."""

    def test_same_look_is_refused(self):
        with self.assertRaises(lookswap.Refused) as c:
            lookswap.plan(monster_cat(), "monster", "103", "103")
        self.assertIn("nothing to change", str(c.exception))

    def test_unknown_target_is_refused_and_says_how_many_exist(self):
        with self.assertRaises(lookswap.Refused) as c:
            lookswap.plan(monster_cat(), "monster", "999", "105")
        msg = str(c.exception)
        self.assertIn("999", msg)
        self.assertIn("3 known", msg,
                      "the refusal should say how many looks this install has, "
                      "so 'not found' cannot read as 'the list is empty'")

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(lookswap.Refused):
            lookswap.plan(monster_cat(), "nonsense", "103", "105")

    def test_a_donor_whose_art_does_not_resolve_is_refused(self):
        """108 is listed by the catalogue with NO mesh. Offering it as a donor
        would stage an empty file over the target -- the swap would 'succeed'
        and the model would vanish."""
        with self.assertRaises(lookswap.Refused) as c:
            lookswap.plan(monster_cat(), "monster", "103", "108")
        self.assertIn("108", str(c.exception))

    def test_a_donor_naming_a_file_that_is_not_there_is_refused(self):
        cat = FakeCat(models=MONSTERS, files={k: v for k, v in FILES.items()
                                              if k != "c3/monster/105/100.c3"})
        with self.assertRaises(lookswap.Refused) as c:
            lookswap.plan(cat, "monster", "103", "105")
        self.assertIn("does not resolve", str(c.exception))


class TheWriteCannotLeaveTheStageTree(unittest.TestCase):
    """The property that makes this safe to ship."""

    def test_a_traversal_path_is_refused_not_written(self):
        """Logical paths come from the install's own tables. A table naming
        `../../x` must not be able to write outside the stage -- and the proof
        is the attempt, not the presence of a confine() call in the source."""
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "stage"
            stage.mkdir()
            outside = Path(td) / "OUTSIDE.txt"
            plan = {"steps": [{"slot": "mesh", "from": "c3/monster/105/100.c3",
                               "write": "../OUTSIDE.txt", "same": False}]}
            cat = monster_cat()
            orig = lookswap._stage_dir
            lookswap._stage_dir = lambda: stage
            try:
                with self.assertRaises((safepath.UnsafePath, ValueError, OSError)):
                    lookswap.stage(cat, plan)
            finally:
                lookswap._stage_dir = orig
            self.assertFalse(outside.exists(),
                             "a traversal path escaped the stage tree")

    def test_a_normal_stage_writes_the_donor_bytes_under_the_stage(self):
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "stage"
            stage.mkdir()
            cat = monster_cat()
            p = lookswap.plan(cat, "monster", "103", "105")
            orig = lookswap._stage_dir
            lookswap._stage_dir = lambda: stage
            try:
                res = lookswap.stage(cat, p)
            finally:
                lookswap._stage_dir = orig
            paths = [Path(w["dest"]) for w in res["written"]]
            self.assertEqual(len(paths), 2)
            for q in paths:
                self.assertTrue(str(q).startswith(str(stage)),
                                "%s is outside %s" % (q, stage))
                self.assertTrue(q.is_file())
            # The DONOR's bytes land on the TARGET's path -- the whole point.
            mesh = stage / "c3" / "monster" / "103" / "100.c3"
            self.assertEqual(mesh.read_bytes(), b"DONOR-MESH-BYTES")


class ThePreviewAndTheActAgree(unittest.TestCase):
    """Two buttons, one planner."""

    def test_stage_uses_the_plan_it_was_given(self):
        """`stage()` takes the plan as an argument and must not recompute it.
        If it re-planned, the preview a user approved and the files written
        could differ -- and nothing on screen would show the difference."""
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "stage"
            stage.mkdir()
            cat = monster_cat()
            p = lookswap.plan(cat, "monster", "103", "105")
            # Hand stage() a plan that does NOT match what plan() would say
            # now. If it honours the argument, only this file appears.
            p2 = {"steps": [{"slot": "mesh", "from": "c3/texture/105000000.dds",
                             "write": "c3/only/this/one.dds", "same": False}]}
            orig = lookswap._stage_dir
            lookswap._stage_dir = lambda: stage
            try:
                res = lookswap.stage(cat, p2)
            finally:
                lookswap._stage_dir = orig
            self.assertEqual([w["path"] for w in res["written"]],
                             ["c3/only/this/one.dds"])
            self.assertFalse((stage / "c3" / "monster").exists(),
                             "stage() re-planned instead of using its argument")

    def test_the_plan_names_both_slots_and_points_donor_to_target(self):
        p = lookswap.plan(monster_cat(), "monster", "103", "105")
        self.assertEqual([s["slot"] for s in p["steps"]], ["mesh", "texture"])
        for s in p["steps"]:
            self.assertTrue(s["write"].startswith("c3/"))
            self.assertIn("105", s["from"], "the donor supplies the bytes")
            self.assertIn("103", s["write"], "the target receives them")


MORPH_PATHS = [
    "c3/monster/103/100.c3",            # ThunderApe, owns a directory
    "c3/texture/103000000.dds",
    "c3/monster/203/100.c3",            # a second family, same tail
    "c3/texture/203000000.dds",
    "c3/texture/303000000.dds",         # SnowApe -- texture only, morph of 103
    "c3/texture/403000000.dds",         # FireSnake -- morph of 203
    "c3/texture/777000000.dds",         # resolves to nothing shipped
]


class FakeCatWithPaths(FakeCat):
    def __init__(self, paths, **kw):
        super().__init__(**kw)
        self.all_paths = list(paths)


class MonsterRecoloursAreFoundAndLabelled(unittest.TestCase):
    r"""A body id that ships a texture and no geometry borrows another
    family's mesh. The catalogue enumerates DIRECTORIES, so these are
    invisible to it -- 64 offerable looks on CCO against 148 that can
    actually be worn."""

    def rows(self):
        base = {"103": {"label": "Monster 103", "mesh": "c3/monster/103/100.c3",
                        "texture": "c3/texture/103000000.dds"},
                "203": {"label": "Monster 203", "mesh": "c3/monster/203/100.c3",
                        "texture": "c3/texture/203000000.dds"}}
        cat = FakeCatWithPaths(MORPH_PATHS, models=MONSTERS, files=FILES)
        return lookswap.monster_morph_rows(cat, base)

    def test_a_recolour_borrows_the_right_family(self):
        """The documented positives from core/monsterart.py: SnowApe 0303 is
        the APE's mesh, FireSnake 0403 is the SNAKE's -- not the other way
        round, which is what the interleave rule exists to get right."""
        r = self.rows()
        self.assertIn("303", r)
        self.assertEqual(r["303"]["mesh"], "c3/monster/103/100.c3")
        self.assertEqual(r["303"]["texture"], "c3/texture/303000000.dds")
        self.assertIn("403", r)
        self.assertEqual(r["403"]["mesh"], "c3/monster/203/100.c3")

    def test_a_body_that_owns_a_directory_is_not_reported_as_a_morph(self):
        r = self.rows()
        for own in ("103", "203"):
            self.assertNotIn(own, r,
                             "%s owns a directory; asking split_colour about "
                             "it would dress it in another family's mesh" % own)

    def test_an_unresolvable_body_is_LISTED_with_a_reason_not_dropped(self):
        """'there is no art' and 'we could not identify the art' are different
        facts, and a UI that omits the row states neither."""
        r = self.rows()
        self.assertIn("777", r)
        self.assertEqual(r["777"]["mesh"], "")
        self.assertTrue(r["777"]["why"],
                        "an unusable row must carry the reason it is unusable")

    def test_no_paths_means_no_guesses(self):
        self.assertEqual(lookswap.monster_morph_rows(FakeCat(), {}), {})


class CreatureNamesAppearOnlyWhenSomethingSuppliesThem(unittest.TestCase):
    r"""The owner asked for creature names "when present".

    Nothing client-side links monster art to a creature name: `bodyType` is 0
    on every row of `ini/monster.json`, and both `artcrawl.py` and
    `monsterart.py` record that the binding is the server's
    `monstertype.Mesh`. So the honest implementation is a path that lights up
    when a dump has been crawled and stays dark when it has not -- never a
    guess.

    The guess that was available and rejected: `ini/monster.json`'s `type`
    column lands on 13 of 374 art directories by coincidence. Using it would
    put a plausible wrong creature name on a model, which is worse than a
    body id because it cannot be spotted by eye.
    """

    def test_no_index_means_no_names(self):
        self.assertEqual(lookswap.monster_names(FakeCat()), {})

    def test_the_label_falls_back_to_the_body_id(self):
        self.assertEqual(lookswap.label_for("103", "c3/monster/103/100.c3", {}),
                         "Monster 103")

    def test_names_are_a_LIST_because_one_look_serves_several_monsters(self):
        """monsterart records that body 0103 serves seven monsters. Picking
        one would present an arbitrary choice as a fact."""
        names = {"c3/monster/103/100.c3": ["Apparition", "Pheasant", "Robin",
                                           "Turtledove"]}
        got = lookswap.label_for("103", "c3/monster/103/100.c3", names)
        self.assertIn("Pheasant", got)
        self.assertIn("...", got, "a truncated list must SAY it is truncated")
        self.assertIn("103", got,
                      "the body id must survive -- it is what the swap acts "
                      "on and what a refusal quotes back")

    def test_a_look_with_no_entry_keeps_its_id_even_when_others_are_named(self):
        names = {"c3/monster/103/100.c3": ["Pheasant"]}
        self.assertEqual(lookswap.label_for("105", "c3/monster/105/100.c3", names),
                         "Monster 105")


class ThePanelAndThePlannerCannotDisagree(unittest.TestCase):
    r"""THE COUPLING, and it is here because it broke.

    The morph rule was written into `coviewer.api_swap_browse` first. The
    panel then offered 148 monster looks while `lookswap` -- which does the
    staging -- still knew 66, so 82 of the new rows would have been refused
    at the moment of the swap. More rows that all dead-end is worse than
    fewer that work.

    One definition fixes it, and this asserts the viewer still uses it rather
    than growing its own copy back."""

    def test_look_rows_includes_every_morph(self):
        cat = FakeCatWithPaths(MORPH_PATHS, models=MONSTERS, files=FILES)
        rows = lookswap.look_rows(cat, "monster")
        for morph in ("303", "403"):
            self.assertIn(morph, rows,
                          "the planner does not know %s, so a user who picks "
                          "it from the panel gets 'not a monster look'" % morph)
        self.assertEqual(rows["303"]["mesh"], "c3/monster/103/100.c3")

    def test_the_viewer_delegates_instead_of_re_deriving(self):
        src = io.open(PROJECT / "tools" / "coviewer.py", encoding="utf-8").read()
        i = src.index("def _monster_morph_rows")
        body = src[i:src.index("def api_swap_browse", i)]
        self.assertIn("lookswap.monster_morph_rows", body,
                      "coviewer must call the one definition")
        self.assertNotIn("split_colour", body,
                         "coviewer is re-deriving the recolour rule instead of "
                         "delegating -- the panel and the planner will drift")


class TheJsonChannelIsClean(unittest.TestCase):
    r"""The endpoint parses stdout. A banner in front of it is a broken
    contract, and it shipped that way once: the viewer received
    ``[coviewer] loaded 11 appearance tables...{"kind":`` and could not parse
    it, because most of the catalogue banner is printed LAZILY when the plan
    first touches `cat.models`, not while the catalogue is built."""

    def test_everything_that_prints_is_inside_the_redirect(self):
        src = io.open(PROJECT / "tools" / "lookswap.py", encoding="utf-8").read()
        body = src[src.index("def main("):]
        red = body.index("redirect_stdout")
        self.assertLess(red, body.index("plan(cat,"),
                        "plan() runs outside the stdout redirect, so its lazy "
                        "banner lands in front of the JSON")
        self.assertLess(red, body.index("stage(cat,"),
                        "stage() runs outside the stdout redirect")
        self.assertIn("real_stdout", body,
                      "the answer must go to a stdout captured before the "
                      "redirect, or it is swallowed with the banner")
        self.assertIn("file=real_stdout", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
