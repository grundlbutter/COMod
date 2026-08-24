#!/usr/bin/env python3
r"""test_library_provenance.py -- a collected asset carries its own install.

THE DEFECT
----------
Which chunks of a mesh are attachment points -- markers, never drawn -- is
`ini/RolePart.ini [Dumy]`, and that list is **per install**.  Measured on this
rig, 2026-08-15: CCO and 7878 declare 52 names including `v_zero`;
5017/5065/5165/5517/6090/6609/Zephyr declare 7.  A chunk not on the list is
drawn as geometry, which is the textured box at a model's feet.

`claude/library-box-artefact` made the socket test follow the install being
**served**.  That is right for an asset read out of that install and wrong for
a COmmunity Library entry, which was collected from some *other* client:
previewed while patch5517 is served, the owner's real entry
`base-blue-super-tao-lady` (collected from CCO) has 11 chunks of which

    v_body001    not a socket in either -- the model
    v_armet      a socket in both
    v_l_forearm v_l_shoulder v_r_forearm v_r_shoulder v_back v_l_foot
    v_r_foot v_zero v_pelvis        sockets under CCO, GEOMETRY under 5517

so 10 chunks draw instead of 1.  Nothing fails: a short `[Dumy]` list parses
fine and simply stops hiding attachment points.

THE FIX THIS GUARDS
-------------------
An entry records `provenance` -- `core/provenance.stamp()`, i.e. `base_id`
(`<kind>-<sha256 over ini/>`), never a path -- and the reader classifies with
*that* install.  Three things have to hold and each has its own class:

  * `server` is NOT that field and must not be made to carry it
    (`ServerIsNotProvenance`);
  * an entry whose install cannot be produced degrades to the served one and
    **says so** (`AnUnknownInstallDegradesOutLoud`);
  * the override is scoped to one decode on one thread
    (`TheOverrideDoesNotLeak`) -- a module global is the bug class this whole
    area keeps relapsing into.

Hermetic: every `[Dumy]` list here is written into a temp directory, so the
suite does not depend on which clients this rig holds.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

import attach                                             # noqa: E402
import collection as colmod                               # noqa: E402
import coviewer                                           # noqa: E402
import provenance                                         # noqa: E402


#: The nine names that separate a 52-entry `[Dumy]` from a 7-entry one on the
#: owner's own collected NPC.  Written out rather than read from an install so
#: the guard holds on a rig with no clients declared.
CCO_ONLY = ["v_l_forearm", "v_l_shoulder", "v_r_forearm", "v_r_shoulder",
            "v_back", "v_l_foot", "v_r_foot", "v_zero", "v_pelvis"]
SHARED = ["v_armet", "v_l_weapon", "v_r_weapon", "v_misc", "v_mount",
          "v_l_shield", "v_r_shield"]


def _root(names, ini_extra: str = "") -> Path:
    """A directory that `attach.dumy_names` and `coroot.base_fingerprint` can
    both read.  `ini_extra` perturbs the fingerprint without changing `[Dumy]`,
    which is how two roots get different `base_id`s here."""
    d = Path(tempfile.mkdtemp(prefix="libprov-"))
    (d / "ini").mkdir(parents=True)
    body = ["[Dumy]", "Count=%d" % len(names)]
    body += ["Dumy%d=%s" % (i, n) for i, n in enumerate(names)]
    (d / "ini" / "RolePart.ini").write_text("\n".join(body) + "\n", "utf-8")
    if ini_extra:
        (d / "ini" / "marker.ini").write_text(ini_extra, "utf-8")
    return d


class ServerIsNotProvenance(unittest.TestCase):
    r"""The field question, settled by what `server` already means.

    `core/collection.py` documents it as *"which imported client it came from
    ("" = the base install)"*, `Collection.by_source` keys dedup on it and
    `_unique_id` builds the entry's slug from it -- it is part of an entry's
    identity, not a spare label.

    The decisive test is not the docstring though: it is that `server`
    **cannot answer the question**.  Two assets collected out of two different
    base installs both carry `server == ""`, because in both cases the
    collector was browsing the base install rather than an imported client.
    """

    def setUp(self):
        self.lib = Path(tempfile.mkdtemp(prefix="libprov-lib-"))
        self.addCleanup(shutil.rmtree, self.lib, True)
        self.col = colmod.Collection(self.lib)

    def _add(self, name, src, prov):
        return self.col.add(category="NPCs", name=name, mesh_bytes=b"MAXFILE\0",
                            mesh_name="1.c3", server="", source_mesh=src,
                            provenance=prov)

    def test_two_installs_give_one_server_and_two_provenances(self):
        """The falsifier for the whole design choice.

        If `server` were the right field, these two entries would differ in
        it. They do not -- and before this change they differed in *nothing*
        that named an install.
        """
        a = self._add("tao lady", "c3/npc/2247/1.c3",
                      {"schema": 1, "base_id": "cco-aaaa", "kind": "cco",
                       "install": "Classic Conquer 2.0"})
        b = self._add("guard", "c3/npc/1001/1.c3",
                      {"schema": 1, "base_id": "patch5517-bbbb",
                       "kind": "patch5517", "install": "5517"})
        self.assertEqual((a["server"], b["server"]), ("", ""),
                         "both were collected from a base install")
        self.assertNotEqual(a["provenance"]["base_id"],
                            b["provenance"]["base_id"])

    def test_provenance_is_not_part_of_the_dedup_key(self):
        """Re-collecting one logical path from a different install is the same
        decision about the same slot, so it REPLACES rather than forking.

        Keying `by_source` on provenance would leave two entries claiming one
        source path with nothing to choose between them.
        """
        self._add("tao lady", "c3/npc/2247/1.c3",
                  {"schema": 1, "base_id": "cco-aaaa", "kind": "cco"})
        self._add("tao lady", "c3/npc/2247/1.c3",
                  {"schema": 1, "base_id": "patch7878-cccc", "kind": "patch7878"})
        self.assertEqual(len(self.col.entries), 1)
        self.assertEqual(self.col.entries[0]["provenance"]["kind"], "patch7878")

    def test_the_key_is_written_even_when_there_is_nothing_to_write(self):
        """`None`, not absent: a present-but-null key says a collector that
        knew about provenance had none to give, which is a different fact from
        an entry predating the field. Both read as unrecorded."""
        e = self._add("x", "c3/npc/9/1.c3", None)
        self.assertIn(colmod.PROVENANCE_KEY, e)
        self.assertIsNone(e[colmod.PROVENANCE_KEY])
        self.assertEqual(colmod.provenance_state(e), colmod.PROV_UNRECORDED)

    def test_the_sidecar_carries_it_too(self):
        """The per-entry `.json` is what survives the folder being copied
        somewhere else, which is the case provenance exists for."""
        e = self._add("tao lady", "c3/npc/2247/1.c3",
                      {"schema": 1, "base_id": "cco-aaaa", "kind": "cco"})
        side = json.loads((self.lib / "Collection" / e["category"] /
                           (e["id"] + ".json")).read_text("utf-8"))
        self.assertEqual(side["provenance"]["base_id"], "cco-aaaa")


class TheThreeRecordStates(unittest.TestCase):
    """`entry_provenance` / `provenance_state`, including the permit
    direction: a well-formed stamp must actually be accepted, or a reader that
    called everything unrecorded would pass every refusal test here."""

    def test_a_well_formed_stamp_is_accepted(self):
        e = {"provenance": {"schema": 1, "base_id": "cco-aa", "kind": "cco"}}
        self.assertIsNotNone(colmod.entry_provenance(e))
        self.assertEqual(colmod.provenance_state(e), colmod.PROV_RECORDED)

    def test_absent_and_null_are_both_unrecorded(self):
        for e in ({}, {"provenance": None}, {"provenance": ""}):
            self.assertIsNone(colmod.entry_provenance(e))
            self.assertEqual(colmod.provenance_state(e),
                             colmod.PROV_UNRECORDED, e)

    def test_present_but_unusable_is_malformed_not_unrecorded(self):
        """A `base_id`-less or non-dict record is a bug report; a missing one
        is history. Collapsing them would hide the first behind the second."""
        for bad in ("cco", ["cco"], {}, {"base_id": ""}, {"kind": "cco"}):
            e = {"provenance": bad}
            self.assertIsNone(colmod.entry_provenance(e))
            self.assertEqual(colmod.provenance_state(e),
                             colmod.PROV_MALFORMED, bad)


class TheStampRefusesToInventAnInstall(unittest.TestCase):
    r"""`provenance.optional_stamp` returns a stamp or ``None`` -- never
    ``unkeyed``.

    `coroot.base_id` degrades to ``unkeyed`` when the fingerprint fails, which
    is right for an artefact keyed by directory and wrong for a record
    embedded in data that travels: it resolves against no install while
    reading exactly like a measured one.
    """

    def test_a_root_that_cannot_be_fingerprinted_records_nothing(self):
        d = Path(tempfile.mkdtemp(prefix="libprov-empty-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(provenance.optional_stamp(d, "test"))

    def test_no_root_records_nothing(self):
        self.assertIsNone(provenance.optional_stamp(None, "test"))

    def test_a_real_root_does_produce_a_stamp(self):
        """The permit direction. Without it the two refusals above are also
        passed by a function that always returns `None`."""
        st = provenance.optional_stamp(_root(SHARED, "a=1"), "test")
        self.assertIsNotNone(st)
        self.assertTrue(st["base_id"] and st["base_id"] != "unkeyed")

    def test_a_stamp_names_no_absolute_path(self):
        """`provenance` forbids paths by design and this is the field that
        makes an entry portable: a stamp naming the collector's own home
        directory would tell a second machine nothing and would leak that
        directory into every library the entry is copied to."""
        d = _root(SHARED, "b=2")
        st = provenance.optional_stamp(d, "test")
        blob = json.dumps(st)
        self.assertNotIn(str(d), blob)
        self.assertNotIn(str(d).replace("\\", "/"), blob)


class _Resolving(unittest.TestCase):
    """Shared rig: two installs with different `[Dumy]` lists and different
    fingerprints, and `coviewer.declared_roots` pointed at a chosen subset."""

    def setUp(self):
        self.long_root = _root(SHARED + CCO_ONLY, "long=1")
        self.short_root = _root(SHARED, "short=1")
        for d in (self.long_root, self.short_root):
            self.addCleanup(shutil.rmtree, d, True)
        self.long_id = provenance.optional_stamp(self.long_root, "t")["base_id"]
        self.short_id = provenance.optional_stamp(self.short_root, "t")["base_id"]
        self.assertNotEqual(self.long_id, self.short_id,
                            "the fixture must produce two distinct installs")
        self.addCleanup(coviewer.set_active_root, coviewer.ACTIVE_ROOT)

    def declare(self, *roots):
        real = coviewer.declared_roots
        pairs = [(str(r), "fixture%d" % i) for i, r in enumerate(roots)]
        coviewer.declared_roots = lambda: list(pairs)
        self.addCleanup(setattr, coviewer, "declared_roots", real)

    def entry(self, base_id):
        return {"id": "e", "mesh": "NPCs/e.c3",
                "provenance": None if base_id is None else
                {"schema": 1, "base_id": base_id,
                 "kind": base_id.partition("-")[0], "install": "fixture"}}


class TheEntrysOwnInstallIsUsed(_Resolving):
    r"""The fix, at the level the box is drawn at.

    Serving the SHORT install and previewing art collected from the LONG one,
    `v_zero` must still classify as a socket.  Under the old rule it did not,
    and it is a real 12-triangle chunk: it draws as a textured box on the
    ground at the model's feet.
    """

    def test_a_resolved_entry_classifies_with_its_own_vocabulary(self):
        self.declare(self.long_root, self.short_root)
        coviewer.set_active_root(self.short_root)          # served
        self.assertFalse(coviewer.is_socket_chunk("v_zero"),
                         "precondition: the served install does not name it")
        p = coviewer.resolve_provenance(self.entry(self.long_id),
                                        self.short_root)
        self.assertEqual(p["state"], coviewer.PROV_RESOLVED)
        self.assertFalse(p["usingServed"])
        with coviewer.socket_root(p["root"]):
            self.assertTrue(coviewer.is_socket_chunk("v_zero"))
        self.assertFalse(coviewer.is_socket_chunk("v_zero"),
                         "the override must not outlive the block")

    def test_all_nine_of_the_disputed_chunks_move_together(self):
        """One name could pass by accident. The measured symptom was nine
        chunks at once, so the guard asserts nine."""
        self.declare(self.long_root)
        coviewer.set_active_root(self.short_root)
        before = [n for n in CCO_ONLY if coviewer.is_socket_chunk(n)]
        with coviewer.socket_root(self.long_root):
            after = [n for n in CCO_ONLY if coviewer.is_socket_chunk(n)]
        self.assertEqual(before, [])
        self.assertEqual(after, CCO_ONLY)

    def test_the_body_is_still_drawn(self):
        """The permit direction, and it is not decorative: a change that made
        everything a socket would hide the model and pass every assertion
        above."""
        self.declare(self.long_root)
        coviewer.set_active_root(self.short_root)
        with coviewer.socket_root(self.long_root):
            self.assertFalse(coviewer.is_socket_chunk("v_body"))


class AnUnknownInstallDegradesOutLoud(_Resolving):
    r"""Requirement 3 and 4 of the brief, and the one this project keeps
    re-learning: **falling back is defensible, falling back silently is the
    defect.**

    Both an unrecorded entry and one naming an install this machine does not
    have end up drawn with the served vocabulary.  They must be
    distinguishable, and both must set `usingServed`.
    """

    def test_an_unrecorded_entry_says_unrecorded_and_uses_the_served_install(self):
        self.declare(self.long_root, self.short_root)
        p = coviewer.resolve_provenance(self.entry(None), self.short_root)
        self.assertEqual(p["state"], coviewer.PROV_UNRECORDED)
        self.assertTrue(p["usingServed"])
        self.assertEqual(Path(p["root"]), self.short_root)
        self.assertIn("nothing recorded", p["note"])

    def test_a_recorded_install_that_is_not_declared_does_not_break(self):
        """Requirement 4. It draws, with the served install, and the note
        NAMES the install it wanted -- which is the difference between a
        setup step and a mystery."""
        self.declare(self.short_root)                      # long one gone
        p = coviewer.resolve_provenance(self.entry(self.long_id),
                                        self.short_root)
        self.assertEqual(p["state"], coviewer.PROV_UNRESOLVED)
        self.assertTrue(p["usingServed"])
        self.assertEqual(Path(p["root"]), self.short_root)
        self.assertIn(self.long_id.partition("-")[0], p["note"])
        # and it still draws rather than raising
        coviewer.set_active_root(self.short_root)
        with coviewer.socket_root(None):
            self.assertFalse(coviewer.is_socket_chunk("v_zero"))

    def test_the_two_fallbacks_are_not_the_same_state(self):
        """Collapsing them would make "you have not declared cco" read as
        "this entry is old", which sends the reader to the wrong repair."""
        self.declare(self.short_root)
        a = coviewer.resolve_provenance(self.entry(None), self.short_root)
        b = coviewer.resolve_provenance(self.entry(self.long_id),
                                        self.short_root)
        self.assertNotEqual(a["state"], b["state"])
        self.assertNotEqual(a["note"], b["note"])

    def test_a_malformed_record_is_reported_as_such(self):
        self.declare(self.short_root)
        p = coviewer.resolve_provenance({"provenance": "cco"}, self.short_root)
        self.assertEqual(p["state"], coviewer.PROV_MALFORMED)
        self.assertTrue(p["usingServed"])

    def test_no_served_install_means_no_served_kind(self):
        """`coroot.kind_for_root(None)` resolves the ENVIRONMENT's install,
        which would name a client the user is not looking at."""
        self.assertEqual(coviewer._served_kind(None), "")

    def test_resolved_is_the_only_state_that_does_not_set_usingServed(self):
        """The flag the UI branches on. If it were set on a resolved entry the
        page would print a fallback warning over a correct render, and users
        learn to ignore a field that cries wolf."""
        self.declare(self.long_root, self.short_root)
        ok = coviewer.resolve_provenance(self.entry(self.long_id),
                                         self.short_root)
        self.assertFalse(ok["usingServed"])
        for e in (self.entry(None), {"provenance": "junk"},
                  self.entry("nosuch-9999")):
            self.assertTrue(
                coviewer.resolve_provenance(e, self.short_root)["usingServed"],
                e)


class TheOverrideDoesNotLeak(_Resolving):
    r"""`ViewerServer` is a `ThreadingHTTPServer`.

    A module-global override would classify a *concurrent* request with the
    library entry's vocabulary -- the same defect with the roots swapped, and
    the exact shape `attach._DUMY_CACHE` and `ACTIVE_ROOT` have each already
    been fixed for once.  This is the third relapse point, guarded before it
    relapses.
    """

    def test_another_thread_keeps_the_served_answer(self):
        coviewer.set_active_root(self.short_root)
        seen = {}
        started, release = threading.Event(), threading.Event()

        def other():
            started.set()
            release.wait(5)
            seen["other"] = coviewer.is_socket_chunk("v_zero")

        t = threading.Thread(target=other)
        t.start()
        started.wait(5)
        with coviewer.socket_root(self.long_root):
            seen["inside"] = coviewer.is_socket_chunk("v_zero")
            release.set()
            t.join(5)
        self.assertTrue(seen["inside"], "the override must work at all")
        self.assertFalse(seen["other"],
                         "a concurrent request was classified with the "
                         "library entry's vocabulary")

    def test_none_does_not_clear_an_outer_override(self):
        """Nested `socket_root(None)` means "I have nothing to add", not
        "forget what the caller established"."""
        coviewer.set_active_root(self.short_root)
        with coviewer.socket_root(self.long_root):
            with coviewer.socket_root(None):
                self.assertTrue(coviewer.is_socket_chunk("v_zero"))

    def test_the_override_is_restored_after_an_exception(self):
        coviewer.set_active_root(self.short_root)
        try:
            with coviewer.socket_root(self.long_root):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(coviewer.is_socket_chunk("v_zero"))


class TheTwoVocabulariesAgree(unittest.TestCase):
    """`coviewer` names four states and `collection` names three of them.

    Two spellings of one vocabulary drift silently -- the UI would branch on a
    string the server had stopped sending and fall through to its default,
    which here is the fault styling. Cheaper to assert than to debug.
    """

    def test_the_shared_state_names_are_identical(self):
        self.assertEqual(coviewer.PROV_UNRECORDED, colmod.PROV_UNRECORDED)
        self.assertEqual(coviewer.PROV_MALFORMED, colmod.PROV_MALFORMED)

    def test_the_page_handles_every_state_the_server_can_send(self):
        """The UI's `PROV_UI` table, read out of the shipped file. A state
        with no entry renders as "unreadable", which is a lie about three of
        the four."""
        js = (HERE / "webui" / "swappage.js").read_text("utf-8")
        for s in (coviewer.PROV_RESOLVED, coviewer.PROV_UNRESOLVED,
                  coviewer.PROV_UNRECORDED, coviewer.PROV_MALFORMED):
            self.assertIn(s + ":", js, f"swappage.js has no case for {s!r}")


class TheCollectionViewFindsItsEntry(unittest.TestCase):
    """`_collected_entry` is what makes the second surface -- browsing the
    Collection, rather than the swap page's preview -- carry provenance too.

    It runs on the hot mesh path, so it also has to answer `None` cheaply for
    every ordinary asset.
    """

    ENTRIES = [{"id": "tao", "mesh": "NPCs/base-blue-super-tao-lady.c3"}]

    def _H(self, server_name, entries):
        """A minimal stand-in carrying the real method off `Handler`.

        Bound here rather than in a class body so that a tree WITHOUT the
        method fails these four tests and loads the rest of the file --
        a module-level `AttributeError` would take the other 24 with it and
        make the fails-before run unreadable.
        """
        h = type("H", (), {
            "server": type("S", (), {"server_name": server_name})(),
            "_collection": lambda s: type("C", (), {"entries": entries})(),
            "_collected_entry": getattr(coviewer.Handler,
                                        "_collected_entry", None),
        })()
        if h._collected_entry is None:
            self.fail("coviewer.Handler has no _collected_entry")
        return h

    def test_a_collection_path_finds_its_entry(self):
        h = self._H(colmod.PROFILE_NAME, self.ENTRIES)
        got = h._collected_entry("collection/NPCs/base-blue-super-tao-lady.c3")
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "tao")

    def test_the_match_is_case_insensitive(self):
        """`Collection.publish` lowercases its filemap keys, so the path that
        comes back from the browser is not the one in the index."""
        h = self._H(colmod.PROFILE_NAME, self.ENTRIES)
        self.assertIsNotNone(
            h._collected_entry("Collection/npcs/BASE-BLUE-SUPER-TAO-LADY.C3"))

    def test_an_install_asset_is_not_a_collection_entry(self):
        """The refusal direction. A path that merely starts with the profile
        name, or any path at all while an install is open, must answer None --
        otherwise every mesh request pays for a library scan and some of them
        get the wrong vocabulary."""
        self.assertIsNone(
            self._H("", self.ENTRIES)._collected_entry("c3/npc/2247/1.c3"))
        self.assertIsNone(
            self._H("", self.ENTRIES)._collected_entry(
                "collection/NPCs/base-blue-super-tao-lady.c3"),
            "an install is open: nothing here is a collected entry")
        self.assertIsNone(
            self._H(colmod.PROFILE_NAME, self.ENTRIES)._collected_entry(
                "c3/npc/2247/1.c3"))
        self.assertIsNone(
            self._H(colmod.PROFILE_NAME, self.ENTRIES)._collected_entry(
                "collection/NPCs/not-collected.c3"))


if __name__ == "__main__":                                # pragma: no cover
    unittest.main(verbosity=2)
