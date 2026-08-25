r"""CCO Swap mode -- the mode itself, and the properties that keep it a MODE.

WHY THIS FILE EXISTS

    The obvious way to ship "COMod, but only the CCO swap flow" is a stripped
    branch. `docs/handoff_2026-08-24_cco_only_mode.md` argues at length why
    that is the wrong shape, and the argument is this repo's most expensive
    lesson: a second definition of one thing drifts from the thing it
    describes. Seven defects in three days, all that shape.

    So CCO Swap is a mode of the existing viewer -- one `coviewer`, one
    `nav.js`, one settings registry -- and the risk moves. A fork's risk is
    divergence you can see; a mode's risk is that the mode's own description
    of itself rots quietly:

        * `MODES` in nav.js names an id that `TABS` no longer has, so a mode
          silently shows one fewer tab than it means to;
        * `MODES` and `cosettings.SETTINGS["ui_mode"].choices` disagree about
          which modes EXIST, so the Settings page offers a mode the bar
          treats as garbage and falls back out of;
        * a seventh tab is added and nobody updates the parent map, so that
          page lights nothing in CCO mode -- a blank bar, on a page that
          renders perfectly.

    Every check here is aimed at one of those three. None of them asserts
    "the bar contains a link to /swap": that is the per-page assertion that
    let five navs disagree while each one passed, and `test_nav_tabs.py`'s
    header explains why at length.

NOT COVERED HERE, ON PURPOSE

    Whether a tab can be CLICKED in either mode. Reachability is a layout
    property -- a control wrapped off the bottom of a bar is still in this
    file's view of the world -- so it is driven in a browser with
    `elementFromPoint` hit-testing, with the hit-tester's own control
    demonstrated, rather than asserted from text. See the branch's report.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
WEBUI = PROJECT / "tools" / "webui"
sys.path[:0] = [str(PROJECT / "core"), str(PROJECT / "tools")]

import coroot                                             # noqa: E402
import cosettings                                         # noqa: E402
# The TABS parser, imported rather than restated. A second regex over
# nav.js is the same second-definition mistake this suite exists to
# catch, and it would keep passing after the real one changed.
from test_nav_tabs import parse_tabs                      # noqa: E402


def nav_src():
    return (WEBUI / "nav.js").read_text("utf-8")


def parse_tab_ids(src):
    """The ids `TABS` declares, in source order."""
    body = re.search(r"const TABS = \[(.*?)\n  \];", src, re.S)
    assert body, "nav.js no longer declares a TABS array"
    return re.findall(r"\{\s*id:\s*'([^']+)'", body.group(1))


def parse_modes(src):
    """`MODES` as nav.js declares it: name -> list of ids, or None."""
    body = re.search(r"const MODES = \{(.*?)\n  \};", src, re.S)
    assert body, "nav.js no longer declares MODES"
    out = {}
    for name, val in re.findall(r"(\w+):\s*(null|\[[^\]]*\]),", body.group(1)):
        out[name] = None if val == "null" else re.findall(r"'([^']+)'", val)
    return out


class TheModeIsDeclaredInOnePlace(unittest.TestCase):
    """The registry and the bar have to agree about which modes exist."""

    def test_the_setting_is_declared_with_its_two_choices(self):
        spec = cosettings.SETTINGS.get("ui_mode")
        self.assertIsNotNone(spec, "ui_mode is not in the settings registry")
        self.assertEqual(spec.kind, str)
        self.assertEqual(spec.default, "full",
                         "the default must be the full toolkit: a stored "
                         "preference is what turns the tool into a product, "
                         "not the other way round")
        self.assertEqual(set(spec.choices or ()), {"full", "cco"})

    def test_the_bar_and_the_registry_name_the_same_modes(self):
        """THE cross-check this file exists for.

        These are two hand-written lists in two languages. If the registry
        grows a mode the bar has never heard of, the Settings page offers it,
        the user picks it, and `nav.js` falls back to `full` -- so the
        preference reads as saved and changes nothing, which this repo's own
        settings module calls worse than having no toggle at all.
        """
        self.assertEqual(set(parse_modes(nav_src())),
                         set(cosettings.SETTINGS["ui_mode"].choices))

    def test_the_full_mode_is_not_a_second_copy_of_the_tab_list(self):
        """`full: null`, never a spelled-out list of all six ids.

        A written-out full list is `TABS` copied, and it goes stale the day a
        seventh tab is added -- silently, and in the mode whose entire job is
        to show everything.
        """
        self.assertIsNone(parse_modes(nav_src())["full"])


class AModeCannotInventOrLoseADestination(unittest.TestCase):

    def setUp(self):
        self.src = nav_src()
        self.ids = parse_tab_ids(self.src)
        self.modes = parse_modes(self.src)

    def test_every_id_a_mode_keeps_is_a_real_tab(self):
        """A typo here does not fail: it removes a destination. `'setings'`
        in the keep list drops the Settings tab and nothing anywhere says
        so."""
        for name, keep in self.modes.items():
            for tab_id in keep or ():
                with self.subTest(mode=name, id=tab_id):
                    self.assertIn(tab_id, self.ids)

    def test_a_mode_keeps_at_least_one_destination(self):
        """An empty keep list is a bar with no tabs and, via `under()`, no
        parent to fall back to -- every page in that mode lights nothing."""
        for name, keep in self.modes.items():
            if keep is not None:
                self.assertTrue(keep, f"{name}: keeps no tabs at all")

    def test_cco_keeps_the_swap_flow_and_drops_the_toolkit_sections(self):
        """The owner's ask, written out longhand rather than derived from
        nav.js -- a test that reads the list it is checking cannot notice
        the list changing.

        `viewer` is in the KEEP list deliberately: `/swap`'s entry point is a
        link on the Asset Viewer, so dropping it would leave the mode's
        headline feature reachable only by typing the URL.
        """
        self.assertEqual(self.modes["cco"], ["viewer", "swap", "settings"])

    def test_the_dropped_pages_still_light_a_tab(self):
        """`under()` DERIVES a parent for every tab a mode hides, so a route
        that is still served never renders a blank bar.

        Checked against the derivation's source rather than against a second
        hand-written map, because a second map is the thing that goes stale.
        """
        self.assertRegex(
            self.src,
            r"function under\(\)[\s\S]*?keep\.indexOf\(t\.id\) < 0",
            "under() no longer derives a parent for a mode's dropped tabs")

    def test_the_bar_renders_the_filtered_list_not_the_whole_one(self):
        """The filter is only a filter if `render` consumes it. A `tabs()`
        function nothing calls is a mode that does nothing, and the bar would
        look exactly as it always did."""
        block = re.search(r"function render\(\)(.*?)\n  \}", self.src, re.S)
        self.assertIsNotNone(block, "render() moved")
        body = block.group(1)
        # assertTrue, not assertIn: assertIn prints the whole haystack, and
        # the haystack here is the entire render() body. A gate whose failure
        # message is fifty lines of source is a gate people stop reading.
        self.assertTrue("tabs()" in body,
                        "render() never calls tabs() -- the mode filter is "
                        "defined and not consumed, so every mode draws the "
                        "full bar")
        self.assertTrue("for (const t of TABS)" not in body,
                        "render() still walks the unfiltered TABS")


class ModStagingIsNotAffectedByTheMode(unittest.TestCase):
    """It is a control, not a destination, and no mode may drop it."""

    def setUp(self):
        self.src = nav_src()

    def test_staging_is_not_a_tab_in_any_mode(self):
        for name, keep in parse_modes(self.src).items():
            with self.subTest(mode=name):
                self.assertNotIn("mods", keep or ())
                self.assertNotIn("staging", keep or ())

    def test_the_button_is_appended_outside_the_filtered_loop(self):
        """If the button were built inside the tab loop it would vanish with
        the tabs. It is assembled and appended on its own, unconditionally,
        and CCO mode is exactly the case that would expose the difference:
        staging is the whole point of the mode."""
        block = re.search(r"function render\(\)(.*?)\n  \}", self.src, re.S)
        body = block.group(1)
        append = body.find("bar.appendChild(mods);")
        loop = body.find("for (const t of drawn)")
        loop_end = body.find("bar.appendChild(list);")
        self.assertGreater(append, 0, "the staging button is no longer added")
        self.assertTrue(loop < loop_end < append,
                        "the staging button is inside the tab loop -- a mode "
                        "that filters tabs would filter it away")


class TheServerTellsThePageWhichModeItIsIn(unittest.TestCase):
    """`<meta name="co-mode">`, injected at serve time.

    Driven through the real `Handler` methods with the two reply helpers
    stubbed, the same way `tests/test_cosettings.py` drives `/api/settings`:
    `BaseHTTPRequestHandler` needs a socket to construct, and a
    re-implementation would be testing the re-implementation.
    """

    @classmethod
    def setUpClass(cls):
        import coviewer
        cls.coviewer = coviewer

        class Fake:
            def __init__(self):
                self.doc = None
                self.code = 200

            def _json(self, obj, code=200):
                self.doc, self.code = obj, code
                return obj

            def _error(self, code, msg):
                self.doc, self.code = {"error": msg}, code
                return self.doc

            cat = None

        # Taken from the real Handler, never restated: a local "cco" here
        # would be a second definition of the kind, and the test would keep
        # passing after the shipped one changed.
        Fake.CCO_KIND = coviewer.Handler.CCO_KIND
        Fake._ui_mode = coviewer.Handler._ui_mode
        Fake._inject_mode = coviewer.Handler._inject_mode
        Fake.api_mode = coviewer.Handler.api_mode
        cls.Fake = Fake

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev = os.environ.get(cosettings.ENV_OVERRIDE)
        os.environ[cosettings.ENV_OVERRIDE] = str(
            Path(self.tmp.name) / "settings.json")

    def tearDown(self):
        if self.prev is None:
            os.environ.pop(cosettings.ENV_OVERRIDE, None)
        else:
            os.environ[cosettings.ENV_OVERRIDE] = self.prev
        self.tmp.cleanup()

    PAGE = b"<!doctype html>\n<html>\n<head>\n<title>x</title>\n" \
           b"</head><body><script src=\"/ui/nav.js\"></script></body></html>"

    def test_the_default_page_says_full(self):
        out = self.Fake()._inject_mode(self.PAGE).decode("utf-8")
        self.assertIn('<meta name="co-mode" content="full">', out)

    def test_setting_the_mode_changes_what_the_page_carries(self):
        """The observable behaviour `cosettings` demands of every entry."""
        before = self.Fake()._inject_mode(self.PAGE)
        cosettings.set_value("ui_mode", "cco")
        after = self.Fake()._inject_mode(self.PAGE)
        self.assertNotEqual(before, after)
        self.assertIn('<meta name="co-mode" content="cco">',
                      after.decode("utf-8"))

    def test_the_meta_precedes_every_script_on_the_page(self):
        """nav.js renders the bar synchronously where its tag sits, so a meta
        that landed after it would be read as absent and every page would
        draw the full bar in CCO mode."""
        cosettings.set_value("ui_mode", "cco")
        out = self.Fake()._inject_mode(self.PAGE).decode("utf-8")
        self.assertLess(out.find('name="co-mode"'), out.find("<script"))

    def test_a_page_with_no_head_still_gets_told(self):
        out = self.Fake()._inject_mode(
            b"<body><script src=\"/ui/nav.js\"></script></body>")
        self.assertLess(out.find(b'name="co-mode"'), out.find(b"<script"))

    def test_api_mode_reports_the_mode_and_the_registry_s_own_choices(self):
        cosettings.set_value("ui_mode", "cco")
        h = self.Fake()
        h.api_mode(lambda n, d="": d)
        self.assertEqual(h.doc["mode"], "cco")
        self.assertEqual(set(h.doc["modes"]),
                         set(cosettings.SETTINGS["ui_mode"].choices))

    def test_cco_mode_lands_on_swap(self):
        """CCO mode exists to put the swap flow first. Landing on the Asset
        Viewer is the mode applying to every page except the one the user
        sees first -- which is what shipped, and what this pins shut."""
        src = (PROJECT / "tools" / "coviewer.py").read_text("utf-8")
        self.assertIn('if path == "/" and self._ui_mode() == "cco":', src)
        self.assertIn('return self._redirect("/swap")', src)

    def test_no_cco_tab_points_at_the_route_that_redirects(self):
        """THE COUPLING, and the reason the redirect is on "/" alone.

        The Asset Viewer is still a tab in CCO mode. If its href were "/",
        the redirect would swallow it and one of the three tabs would bounce
        to another -- a dead tab that looks like a routing bug, reported as
        "the viewer is broken". Any tab in the CCO set whose href is the
        redirecting route is that bug, so the check is on the SET, not on the
        one tab that has the problem today."""
        nav = (PROJECT / "tools" / "webui" / "nav.js").read_text("utf-8")
        tabs = dict((i, h) for i, h, _l in parse_tabs(nav))
        m = re.search(r"cco:\s*\[([^\]]*)\]", nav)
        self.assertIsNotNone(m, "MODES has no cco entry")
        cco = re.findall(r"'([^']+)'", m.group(1))
        self.assertTrue(cco, "the cco mode lists no tabs")
        for tab_id in cco:
            self.assertIn(tab_id, tabs, f"MODES names a tab that is not in TABS: {tab_id}")
            self.assertNotEqual(
                tabs[tab_id], "/",
                f"tab '{tab_id}' points at '/', which CCO mode redirects to "
                f"/swap -- that tab is unreachable in the mode that shows it")

    def test_the_default_root_is_coroot_s_and_not_a_copy_of_the_literal(self):
        """`C:\\Program Files\\Classic Conquer 2.0` is written down in
        `coroot.CONVENTIONAL_ROOT`. A copy in the viewer, or in setup.html,
        is the definition that keeps saying the old path after a rename."""
        h = self.Fake()
        h.api_mode(lambda n, d="": d)
        self.assertEqual(h.doc["defaultRoot"], coroot.CONVENTIONAL_ROOT)
        # The PATH, not the product name -- "Classic Conquer 2.0" appears in
        # coviewer.py's prose and that is fine. What must have one definition
        # is the conventional root itself.
        literal = re.compile(r"Program Files[\\/]+Classic Conquer 2\.0")
        for f in (PROJECT / "tools" / "coviewer.py", WEBUI / "setup.js"):
            self.assertIsNone(literal.search(f.read_text("utf-8")),
                              f"{f.name} spells out the conventional root as "
                              f"well as coroot -- one of the two will rot")

    def test_cco_mode_names_a_kind_a_plugin_actually_answers_to(self):
        """CCO mode skips the plugin PICKER by supplying the kind. That is a
        removed choice, not a removed check -- `/api/setroot` still puts the
        folder to the plugin's own `confidence` hook. But the supplied name
        has to be a name some plugin has, or the save fails on a string
        nobody typed."""
        cosettings.set_value("ui_mode", "cco")
        h = self.Fake()
        h.api_mode(lambda n, d="": d)
        self.assertEqual(h.doc["kind"], "cco")
        sys.path.insert(0, str(PROJECT))
        import plugins as plugmod
        names = {getattr(p, "name", p) for p in plugmod.available()}
        self.assertIn(h.doc["kind"], names)

    def test_full_mode_supplies_no_kind_so_the_picker_still_decides(self):
        h = self.Fake()
        h.api_mode(lambda n, d="": d)
        self.assertEqual(h.doc["mode"], "full")
        self.assertEqual(h.doc["kind"], "")


class TheFirstRunPageIsAModeOfItselfToo(unittest.TestCase):
    """setup.html is ONE document. CCO hides parts of it; it does not get a
    setup-cco.html that would drift the first time the required-files list or
    the save flow changed."""

    def test_there_is_no_second_setup_document(self):
        extra = [p.name for p in WEBUI.glob("setup*.html")]
        self.assertEqual(extra, ["setup.html"])

    def test_the_hidden_parts_are_addressable_as_blocks(self):
        html = (WEBUI / "setup.html").read_text("utf-8")
        for block in ('id="kinds-block"', 'id="searched-block"'):
            self.assertIn(block, html,
                          "setup.js hides whole sections by id; hiding only "
                          "the inner control leaves its heading and its "
                          "explanatory paragraph behind, describing a picker "
                          "that is not there")

    def test_the_page_asks_the_server_rather_than_hardcoding_the_mode(self):
        js = (WEBUI / "setup.js").read_text("utf-8")
        self.assertIn("/api/mode", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
