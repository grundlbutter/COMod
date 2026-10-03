"""The site bar -- `tools/webui/nav.js` -- and the property it exists for.

WHAT WENT WRONG, AND WHY A TEST IS THE RIGHT SHAPE OF FIX

    Every page hand-rolled its own header links. `index.html` offered eight
    controls, `builder.html` four, `mapedit.html` four, `swap.html` two, and
    `settings.html` had a `.set-nav` listing only its own sections. Five copies
    of one thing, so "where can I go from here" had five different answers
    depending on where you already were.

    That is this project's most-repeated defect shape: a second definition
    drifting from the thing it describes. `cards.js` documents the instance
    where a `cursor: pointer` promise had a listener behind it on one page and
    nothing on the other, for the whole life of that page.

    So the checks here are deliberately NOT "does index.html contain
    href=/mapedit". That is the per-page assertion that let five navs disagree
    while each one passed. They are:

        * the list is in ONE file, and it is the list the owner asked for
        * every page CONSUMES that file rather than restating it
        * every page resolves to exactly ONE current tab
        * every tab is a route the server actually serves
        * no page has grown a second way to say the same thing

    The middle two are the ones with teeth. A bar that renders on four pages
    and silently no-ops on the fifth passes every "the file contains the
    markup" test ever written.

NOT COVERED HERE, ON PURPOSE

    Whether a tab can be CLICKED. Reachability is a layout property -- a
    control pushed off the right edge of a bar is still in this file's view of
    the world -- so it is driven in a browser with `elementFromPoint`
    hit-testing rather than asserted from text. See the branch's report.
"""

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
WEBUI = PROJECT / "tools" / "webui"

#: Every page that is part of the tool's navigation. `setup.html` is not here
#: and must not be: it is what the server serves when there is NO install
#: configured, where every tab but Settings would 503, so a bar offering them
#: would be a bar of dead ends. `companion.html`, `play.html` and
#: `showandtell.html` are separate surfaces with their own shells.
PAGES = ("index.html", "builder.html", "models.html", "mapedit.html",
         "settings.html", "swap.html", "effects.html")

#: The owner's list, in the owner's order. Written out longhand rather than
#: derived from nav.js -- a test that reads the list it is checking cannot
#: notice the list changing.
WANT_TABS = [
    # "/index.html", not "/". CCO mode redirects bare "/" to the Swap page,
    # and the Asset Viewer is STILL a tab in that mode -- so pointing its tab
    # at the shared route would make the one tab bounce to another.
    ("viewer",   "/index.html", "Asset Viewer"),
    ("builder",  "/builder",  "Character Builder"),
    ("models",   "/models",   "Model Viewer"),
    # The Effects Viewer. Its own tab rather than a panel on the Model Viewer
    # because it plays TWO systems, and one of them (ini/effect.ini ->
    # ani/effect.ani flipbooks) has no mesh in it at all.
    #
    # ONE TAB, AND IT WAS TWO. Until 2026-09-08 there was an "Effect Preview"
    # at /effects and an "Effects Viewer" at /fxview: the same subject seen
    # twice, one playing an effect and one cataloguing it. The owner asked for
    # one page called the Effects Viewer. The player's three-column shell
    # survived (nav left, content middle, options and info right) and the
    # library's panels moved into it, so the ROUTE is still /effects and only
    # the label changed. /fxview forwards there and is deliberately NOT a tab:
    # a redirect in the bar is a destination that moves under you.
    ("effects",  "/effects",  "Effects Viewer"),
    ("mapedit",  "/mapedit",  "Map Editor"),
    # The Map Asset Viewer. The owner asked for it by name -- "a Map Asset
    # Viewer page that helps people find, and collect map satellite files" --
    # so it is in the bar for the same reason every other entry is.
    #
    # A PEER OF THE MAP EDITOR RATHER THAN A PANEL ON IT, and the split is
    # the subject, not the screen space. The Editor's subject is ONE MAP; this
    # one's subject is a SATELLITE asked across every map at once -- which
    # covers exist, which maps draw each, and which maps could. A corpus-wide
    # catalogue hung off a page built around a single map's viewport is how
    # the shared-art question stayed unaskable: you could see what a map uses
    # and never what else uses it.
    ("mapassets", "/mapassets", "Map Assets"),
    ("swap",     "/swap",     "Swap"),
    ("settings", "/settings", "Settings"),
]


def read(name):
    return (WEBUI / name).read_text("utf-8")


def parse_tabs(nav_js):
    """The TABS array as nav.js actually declares it, in source order."""
    body = re.search(r"const TABS = \[(.*?)\n  \];", nav_js, re.S)
    assert body, "nav.js no longer declares a TABS array"
    return re.findall(
        r"\{\s*id:\s*'([^']+)',\s*href:\s*'([^']+)',\s*label:\s*'([^']+)'\s*\}",
        body.group(1))


def parse_under(nav_js):
    """Pages that are not tabs, and the tab they sit under."""
    body = re.search(r"const UNDER = \{(.*?)\};", nav_js, re.S)
    assert body, "nav.js no longer declares UNDER"
    return dict(re.findall(r"(\w+):\s*'([^']+)'", body.group(1)))


class OneNav(unittest.TestCase):
    """One definition, and every page consuming it rather than restating it."""

    def setUp(self):
        self.nav = read("nav.js")

    def test_the_bar_is_the_list_the_owner_asked_for(self):
        self.assertEqual(parse_tabs(self.nav), WANT_TABS)

    def test_every_page_hosts_the_bar_and_loads_it(self):
        """Both halves. A host div with no script is an empty strip; a script
        with no host is a file that returns `null` and says nothing -- and
        both look completely fine in the file they are missing from."""
        for page in PAGES:
            html = read(page)
            self.assertIn('<div id="co-nav"></div>', html,
                          f"{page}: no host for the bar")
            self.assertIn('<script src="/ui/nav.js"></script>', html,
                          f"{page}: hosts the bar and never loads it")

    def test_the_bar_is_not_deferred(self):
        """`swap.js` binds `#btn-mods` from the bottom of the document, and
        nav.js is what renders that button. A deferred script runs AFTER the
        in-body ones, so the button would exist and be dead -- which is the
        failure that reads as "my mod staging is gone", already reported once
        on this codebase."""
        for page in PAGES:
            self.assertNotRegex(
                read(page), r'<script [^>]*src="/ui/nav\.js"[^>]*defer',
                f"{page}: a deferred bar renders after its consumers bind")

    def test_every_page_resolves_to_exactly_one_current_tab(self):
        """The owner's actual ask: the same UI whichever page is active, with
        one of them active. A page that declares nothing, or declares a name
        no tab answers to, renders a bar with nothing lit -- and nothing about
        the markup would look wrong."""
        tabs = {t[0] for t in parse_tabs(self.nav)}
        under = parse_under(self.nav)
        for page in PAGES:
            m = re.search(r'<body[^>]*\bdata-co-page="([^"]+)"', read(page))
            self.assertIsNotNone(m, f"{page}: declares no data-co-page")
            pid = m.group(1)
            cur = under.get(pid, pid)
            self.assertIn(cur, tabs,
                          f"{page}: data-co-page={pid!r} lights no tab")

    def test_exactly_one_tab_gets_aria_current(self):
        """`on` is the paint; `aria-current` is the same statement in a form a
        machine can read. They are set together in one branch precisely so
        they cannot disagree."""
        block = re.search(r"if \(t\.id === cur\) \{(.*?)\n      \}",
                          self.nav, re.S)
        self.assertIsNotNone(block, "the active-tab branch moved")
        self.assertIn("classList.add('on')", block.group(1))
        self.assertIn("aria-current", block.group(1))

    def test_every_tab_is_a_route_the_server_serves(self):
        """A tab pointing at a 404 is the same lie as a tab that is really a
        button: it looks like a destination and is not."""
        src = (PROJECT / "tools" / "coviewer.py").read_text("utf-8")
        for _id, href, label in parse_tabs(self.nav):
            if href == "/index.html":
                self.assertIn('if path == "/" or path == "/index.html":', src)
                continue
            self.assertIn(f'if path in ("{href}", "{href}/", "{href}.html")',
                          src, f"{label}: {href} is not a route")

    def test_no_page_hand_rolls_a_second_way_to_the_same_places(self):
        """The bar is only one bar if nothing competes with it. A page-content
        link INTO a section is fine and wanted -- `/swap`'s entry point is one
        -- so this is scoped to the <header>, which is where all five copies
        lived."""
        for page in PAGES:
            head = re.search(r"<header>(.*?)</header>", read(page), re.S)
            if not head:
                continue
            for href in re.findall(r'href="([^"]+)"', head.group(1)):
                self.assertFalse(
                    href.startswith("/"),
                    f"{page}: <header> links to {href} -- that is the bar's job")


class Removals(unittest.TestCase):
    """Two controls the owner asked to be gone, and the behaviour that stays."""

    def test_health_and_thumbnails_is_gone_from_the_viewer(self):
        """It lives on Settings now, whose Health management section is the
        same report."""
        html = read("index.html")
        self.assertNotIn('id="btn-health"', html)
        self.assertNotIn("Health &amp; thumbnails", html)
        self.assertIn('id="sec-health"', read("settings.html"),
                      "removed from one page and not present on the other")

    def test_the_first_run_health_prompt_survived_losing_its_button(self):
        r"""The button was an ENTRY POINT, not the feature -- and this test used
        to assert the wrong thing.

        It asserted that `const btn = q('#btn-health');` was still PRESENT, on
        the reasoning that a guarded binding proves the behaviour survived. It
        does not. The lookup is a proxy: it can be present while the auto-open
        is broken, and absent while the auto-open is fine -- which is exactly
        the state the tree is in now. Worse, `test_firstrun_optin` forbids
        looking up an id that nothing defines, so the two guards contradicted
        each other and one of them had to be wrong. It was this one.

        What actually makes the prompt survive is that the auto-open runs at
        TOP LEVEL, not inside any `if (btn)`. That is the property, so that is
        what is asserted: the health fetch and `open()` are reachable with no
        button in the file at all.
        """
        js = read("firstrun.js")

        # The dead lookup must be gone -- `test_firstrun_optin` refuses it.
        self.assertNotIn("q('#btn-health')", js,
                         "firstrun.js reaches for a button no page defines")

        # And the behaviour must still be there: an auto-open that asks
        # /api/health and opens the panel when something is wrong.
        self.assertIn("jget('/api/health')", js)
        self.assertIn("open();", js)

        # The load-bearing part: that call is NOT nested inside a conditional
        # on a button. Measured by brace depth rather than by reading, because
        # "it looked top level" is how the previous version of this test came
        # to assert a proxy. Depth 0 at the IIFE that performs the fetch.
        lines = js.splitlines()
        target = next(i for i, l in enumerate(lines)
                      if "Auto-open on first run only" in l)
        depth = 0
        for l in lines[:target]:
            depth += l.count("{") - l.count("}")
        self.assertEqual(
            depth, 1,
            "the auto-open is nested %d braces deep; it must sit at the "
            "module IIFE's own level so no button guards it" % depth)

    def test_the_collapse_button_is_gone_from_every_page_that_had_it(self):
        for page in ("index.html", "builder.html", "mapedit.html"):
            self.assertNotIn('id="btn-collapse-all"', read(page),
                             f"{page}: the duplicate collapse control is back")

    def test_no_code_still_reaches_for_the_button_it_removed(self):
        """The specific way this goes wrong: `app.js` did

            case 'c': CardPanels.toggleAll();
                      $('#btn-collapse-all').textContent = ...

        which dereferences the button unconditionally. Deleting it from the
        HTML alone leaves `C` throwing on a control that no longer exists --
        the button removed AND the behaviour removed, which is exactly what
        was not asked for."""
        for js in ("app.js", "builder.js", "mapedit.js", "cards.js"):
            self.assertNotIn("btn-collapse-all", read(js),
                             f"{js} still names the removed button")

    def test_collapsing_still_works_both_ways(self):
        """Per-card headers and the `C` key. cards.js owns both; each page
        binds the shared implementation and none of them grew a copy."""
        cards = read("cards.js")
        self.assertIn("h2.addEventListener('click', () => toggle(", cards)
        self.assertIn("function toggleAll()", cards)
        # ...and the key still reaches it on all three pages.
        self.assertIn("case 'c': CardPanels.toggleAll(); break;", read("app.js"))
        self.assertIn("case 'c': toggleAllPanels(); break;", read("builder.js"))
        self.assertIn("CardPanels.toggleAll();", read("mapedit.js"))


class ModStagingIsAButton(unittest.TestCase):
    """It is on every page, and it is not a tab.

    The owner ruled both halves: staging appears everywhere including
    Settings, and it is a button rather than a member of the bar. A control
    that looks like a destination and is not is the thing the bar must never
    contain, so the check is that `btn-mods` exists once, in nav.js, and in
    no page's own markup.
    """

    def test_the_button_has_exactly_one_definition(self):
        self.assertEqual(read("nav.js").count("mods.id = 'btn-mods';"), 1)
        for page in PAGES:
            self.assertNotIn('id="btn-mods"', read(page),
                             f"{page}: a second staging button")

    def test_it_is_not_in_the_tab_list(self):
        for tab_id, href, label in parse_tabs(read("nav.js")):
            self.assertNotIn("staging", label.lower(),
                             f"{href}: staging is a button, not a tab")
            self.assertNotIn(tab_id, ("mods", "staging", "stage"))

    def test_staging_is_reachable_from_every_page(self):
        """Either the page ships its own `#drawer`, or nav.js injects the
        standard one. Settings is the case that matters: it had no staging at
        all, and if it needed a special case here the bar would not really be
        one thing."""
        nav = read("nav.js")
        self.assertIn("function ensureDrawer()", nav)
        for page in PAGES:
            html = read(page)
            own = 'id="drawer"' in html
            # A page relying on the injected drawer must load swap.js (the
            # behaviour) and csrf.js (or its install POSTs are rejected at the
            # button, which is the worst place to discover it).
            if not own:
                self.assertIn('src="/ui/swap.js"', html,
                              f"{page}: injected drawer, no swap.js")
                self.assertIn('src="/ui/csrf.js"', html,
                              f"{page}: injected drawer, no csrf.js")
            self.assertTrue(own or 'src="/ui/swap.js"' in html,
                            f"{page}: staging button opens nothing")

    def test_the_injected_drawer_is_wired_only_where_it_was_injected(self):
        """Four pages call `Swap.initDrawer()` themselves. Binding a second
        time would give every install button two listeners, and "Install for
        real" running twice is not a cosmetic bug. So the condition is "I made
        this drawer", not a guess about which page this is."""
        nav = read("nav.js")
        self.assertIn("if (CoNav.ensureDrawer()) CoNav.wireStaging();", nav)

    def test_the_drawer_is_decided_after_the_document_is_parsed(self):
        """FOUND BY DRIVING, not by reading. nav.js executes where it sits,
        near the top of <body>, and a page's own `#drawer` lives near the
        bottom -- so at that moment `getElementById('drawer')` is null and
        `ensureDrawer` injected a SECOND drawer into every page that already
        had one. Two `#drawer`, two `#btn-install`, `querySelector` silently
        taking the first. Both were `hidden` and the bar looked perfect.

        The bar itself must still be drawn synchronously, because swap.js
        binds `#btn-mods` from the bottom of the document."""
        nav = read("nav.js")
        self.assertIn("CoNav.render();", nav)
        self.assertRegex(
            nav, r"if \(document\.readyState === 'loading'\) \{\s*"
                 r"document\.addEventListener\('DOMContentLoaded', later\);")
        # ...and the drawer must NOT be created from inside render().
        body = re.search(r"function render\(\) \{(.*?)\n  \}", nav, re.S)
        self.assertIsNotNone(body)
        self.assertNotIn("ensureDrawer", body.group(1),
                         "render() runs mid-parse and cannot see the page")


class ModelViewerIsAPage(unittest.TestCase):
    """The tab is a destination because the thing behind it became one."""

    def test_it_has_its_own_route_and_document(self):
        self.assertTrue((WEBUI / "models.html").exists())
        src = (PROJECT / "tools" / "coviewer.py").read_text("utf-8")
        self.assertIn(
            'if path in ("/models", "/models/", "/models.html")', src)

    def test_neither_page_carries_the_other_s_markup(self):
        """Two documents, one engine. If the split leaves the model rail on
        the builder it is two rails again with one of them dead, which is the
        drift this whole change is against."""
        builder, models = read("builder.html"), read("models.html")
        for gone in ('id="model-rail"', 'id="card-model"', 'id="mode-model"',
                     'id="mode-character"', 'id="modeswitch"'):
            self.assertNotIn(gone, builder, f"builder.html still has {gone}")
        for gone in ('id="slots-rail"', 'id="picker"', 'id="card-look"',
                     'id="mode-model"', 'id="modeswitch"'):
            self.assertNotIn(gone, models, f"models.html still has {gone}")

    def test_old_links_are_redirected_rather_than_broken(self):
        """`/builder#model=…` and `/builder#mesh=…` were the shareable output
        of `Copy link` and of the viewer's "open this mesh in the model
        viewer", so they are in people's scrollback. A fragment never reaches
        the server, so builder.js is the only place that can honour them."""
        js = read("builder.js")
        self.assertIn("location.replace('/models' + location.hash);", js)
        self.assertIn("if (h.get('model') || h.get('mesh')) {", js)

    def test_nothing_still_points_new_traffic_at_the_old_url(self):
        """A redirect that keeps being fed is a redirect that never retires."""
        for js in ("app.js", "builder.js"):
            src = read(js)
            self.assertNotIn("'/builder#' + p.toString()", src,
                             f"{js} still mints a model link on /builder")

    def test_the_page_knows_which_page_it_is_without_sniffing_the_url(self):
        """`/models`, `/models/` and `/models.html` are the same page, and a
        URL sniffer has to be taught each one. The page already knows -- and
        it is the SAME declaration nav.js lights the tab from, so the engine
        and the bar cannot disagree about where you are."""
        js = read("builder.js")
        self.assertIn("document.body.dataset.coPage", js)
        self.assertIn("const IS_MODELS = PAGE === 'models';", js)
        self.assertNotIn("location.pathname", js)


class SwapIsNotOrphaned(unittest.TestCase):
    """`/swap` is a real route and deliberately not a sixth tab."""

    def test_it_is_reachable_from_the_viewer(self):
        self.assertRegex(read("index.html"), r'href="/swap"')

    def test_it_lights_its_own_tab(self):
        r"""Swap is a tab in its own right now, so it lights ITSELF.

        This used to assert `UNDER['swap'] == 'viewer'` -- the mapping that
        lit Asset Viewer while you were on /swap. That was the least-bad
        answer while swap was off the bar: something lit beats nothing lit,
        because a blank bar reads as broken. It was still the bar naming a
        page you were not on, and the owner resolved it by giving swap a tab.

        So the assertion inverts. Swap must NOT be in the parent map, because
        being there would light a second section as well as its own.
        """
        under = parse_under(read("nav.js"))
        self.assertNotIn("swap", under,
                         "swap is a tab now; mapping it under another tab "
                         "would light two sections for one page")
        self.assertIn('data-co-page="swap"', read("swap.html"))

    def test_the_page_no_longer_claims_the_whole_viewport(self):
        """`.sw-wrap { height: 100vh }` predates the bar. `body` is a column
        flexbox, so a hard 100vh under a bar overflows by exactly the bar's
        height, and `overflow: hidden` eats the bottom of the page -- the
        install buttons among it."""
        rule = re.search(r"\.sw-wrap \{[^}]*\}", read("swap.html"))
        self.assertIsNotNone(rule, ".sw-wrap rule is gone")
        self.assertNotIn("100vh", rule.group(0))
        self.assertIn("flex: 1 1 auto", rule.group(0))


if __name__ == "__main__":
    unittest.main()
