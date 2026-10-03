/* nav.js -- the top bar, for EVERY page. One definition, five consumers.
 *
 * WHY THIS FILE EXISTS
 *
 *   Every page hand-rolled its own header links and they all disagreed.
 *   `index.html` offered eight controls, `builder.html` four, `mapedit.html`
 *   four, `swap.html` two, and `settings.html` had a `.set-nav` of its own
 *   that listed only its own sections. Five copies of one thing, so "where
 *   can I go from here" had five different answers depending on where you
 *   already were -- and adding a destination meant remembering all five.
 *
 *   Same failure this project has paid for repeatedly: a second definition
 *   drifting from the thing it describes (cards.js's own header explains the
 *   collapsible-panel instance of it). So the bar is data here, rendered the
 *   same way on every page, and a page cannot express an opinion about it
 *   beyond saying WHICH page it is.
 *
 * THE CONTRACT A PAGE SIGNS
 *
 *   <body data-co-page="viewer">        <- which tab is current
 *   <div id="co-nav"></div>             <- where the bar goes, first in body
 *   <script src="/ui/nav.js"></script>  <- immediately after the host
 *
 *   The script tag sits right after the host and is NOT deferred, so the bar
 *   exists before any page script runs. `#btn-mods` is rendered by this file,
 *   and swap.js binds to it from the bottom of the document -- a `defer`
 *   here would run after that and the staging button would be dead.
 *
 * TABS ARE DESTINATIONS. THE BUTTON IS A CONTROL.
 *
 *   Five tabs, all routes, exactly one `aria-current="page"`. Mod staging is
 *   a PANEL, not a location, so it is a button and it sits apart from the
 *   tabs -- position carries the difference, which is why there is no divider
 *   or colour treatment doing it a second time. A control that looks like a
 *   destination and is not is the specific thing this bar must never grow.
 *
 * NARROW WINDOWS
 *
 *   The row WRAPS. It does not scroll and it does not truncate labels: both
 *   of those leave a real control off the edge of a bar that still passes a
 *   "the element is in the DOM" test. Wrapping makes the bar taller and every
 *   tab stays hit-testable, which is the property that gets driven.
 */
'use strict';

const CoNav = (() => {

  /** THE tab list. Adding a section means adding a line here and a route in
   *  coviewer.py -- nowhere else. `id` is what a page puts in
   *  `data-co-page`. */
  const TABS = [
    { id: 'viewer',   href: '/index.html', label: 'Asset Viewer' },
    { id: 'builder',  href: '/builder',  label: 'Character Builder' },
    { id: 'models',   href: '/models',   label: 'Model Viewer' },
    // ONE tab, and it was two until 2026-09-08. `/effects` (Effect Preview)
    // and `/fxview` (Effects Viewer) were the same subject seen twice: one
    // played an effect and the other catalogued it. The owner asked for one
    // page called "Effects Viewer", so the Effect Preview's three-column
    // shell survived and the library's panels moved into it. `/fxview` still
    // resolves -- old links do not break -- and forwards here, which is why
    // there is no `fxview` entry in `UNDER` either: it is not a page any
    // more, it is a redirect.
    { id: 'effects',  href: '/effects',  label: 'Effects Viewer' },
    { id: 'mapedit',  href: '/mapedit',  label: 'Map Editor' },
    // A PEER OF THE MAP EDITOR, not a panel on it: its subject is the
    // SATELLITE asked across every map at once, where the Editor's
    // subject is one map.
    { id: 'mapassets', href: '/mapassets', label: 'Map Assets' },
    { id: 'swap',     href: '/swap',     label: 'Swap' },
    { id: 'settings', href: '/settings', label: 'Settings' },
  ];

  /** WHICH DESTINATIONS A MODE ADVERTISES -- ids only.
   *
   *  `TABS` above stays the ONE definition of what a tab IS: its route and
   *  its label. This says only which of them a mode puts in the bar, so a
   *  mode cannot invent a destination, cannot relabel one, and cannot point
   *  one somewhere else. That split is why CCO Swap is a filter here rather
   *  than a second bar in a second file -- the fork shape this project has
   *  paid for seven times, which this file's own header describes.
   *
   *  `null` means "every tab", and it is null rather than a written-out copy
   *  of all six ids on purpose: a spelled-out full list is `TABS` duplicated,
   *  and it goes stale the day a seventh tab is added -- SILENTLY, and in the
   *  mode whose entire job is to show everything.
   *
   *  `cco` is the CCO Swap product: browse an asset, swap it, set your
   *  preferences. The three it drops -- Character Builder, Model Viewer, Map
   *  Editor -- are sections of the toolkit, not steps in that flow. Asset
   *  Viewer STAYS because `/swap`'s entry point is a link on it; dropping it
   *  would leave the mode's headline feature reachable only by typing a URL.
   */
  const MODES = {
    full: null,
    cco: ['viewer', 'swap', 'settings'],
  };

  /** Pages that are not themselves tabs, and the tab they belong under.
   *
   *  A page listed here lights its parent; a page listed nowhere lights
   *  nothing, which reads as broken and is why the map exists at all. It was
   *  empty until 2026-09-25: `/swap` used to live here mapped under `viewer`,
   *  which was the honest-but-uncomfortable answer to "which tab is current
   *  on /swap" -- Asset Viewer, a page you are not on -- and Swap becoming
   *  its own tab removed the question.
   *
   *  `/builddiff` is the situation the mechanism was kept for, and it is a
   *  FLOW rather than a section: "what changed between one client build and
   *  the next", read out of `<assets>/derived/patchdict/`, which most boxes
   *  do not have -- the page's own refusal banner is the ordinary outcome. A
   *  ninth tab spends a permanent slot in a bar everyone sees on a page most
   *  sessions cannot use, and the bar's whole discipline is that it does not
   *  grow casually.
   *
   *  UNDER `settings` because that is where the declared install set lives,
   *  and "what changed between two builds" is a question about that set.
   *  Settings is in `MODES.cco` as well, so the parent exists in both modes
   *  and `under()` has nothing to derive for it.
   *
   *  THIS MAP IS MODE-INDEPENDENT. The tabs a MODE hides are handled by
   *  `under()` below, which derives them -- see there for why they are not
   *  written out as a second map beside `MODES`. */
  const UNDER = {
    builddiff: 'settings',
  };

  /** Which mode the server says this page is in.
   *
   *  Read from a `<meta>` the server injects at serve time, exactly like
   *  `co-csrf`, and read SYNCHRONOUSLY because the bar renders where this
   *  script sits. It cannot be a fetch: the bar would draw in full mode and
   *  then redraw narrower a moment later, which is a flicker in the one
   *  component whose job is to be identical on every page.
   *
   *  An unknown or missing value is `full`, and the direction of that
   *  fallback is deliberate: a garbled mode string that HIDES real
   *  destinations is a broken tool, while one that shows a tab a CCO user
   *  does not need is a tab they do not click. */
  function mode() {
    const m = document.querySelector('meta[name="co-mode"]');
    const v = ((m && m.content) || '').trim();
    return Object.prototype.hasOwnProperty.call(MODES, v) ? v : 'full';
  }

  /** The bar's destinations for this mode. */
  function tabs() {
    const keep = MODES[mode()];
    if (!keep) return TABS.slice();
    return TABS.filter(t => keep.indexOf(t.id) >= 0);
  }

  /** The parent map in force, DERIVED for the tabs this mode drops.
   *
   *  A mode that hides `builder` does not make `/builder` stop existing --
   *  the route is still served and still reachable by URL or bookmark. If
   *  nothing claimed those pages they would render a bar with nothing lit,
   *  which is the exact "reads as broken" failure `UNDER` exists for.
   *
   *  COMPUTED from `MODES` rather than written out as a second map beside it,
   *  because a hand-written one must be edited every time `TABS` grows: add a
   *  seventh tab, forget the parallel entry, and that page silently lights
   *  nothing in CCO mode -- while every test that reads either list on its
   *  own still passes. Deriving it keeps the keep-list the single place a
   *  mode is described.
   *
   *  The parent is the mode's FIRST kept tab: its landing page, the one a
   *  user in that mode gets by opening the tool. There is no truer answer --
   *  the Map Editor is not "part of" the Asset Viewer -- and the bar's job
   *  here is to say "you are outside this mode's sections", not to invent a
   *  hierarchy it does not have. */
  function under() {
    const keep = MODES[mode()];
    const map = Object.assign({}, UNDER);
    if (!keep || !keep.length) return map;
    const home = keep[0];
    for (const t of TABS) {
      if (keep.indexOf(t.id) < 0 && !(t.id in map)) map[t.id] = home;
    }
    return map;
  }

  const TITLE = {};
  for (const t of TABS) TITLE[t.id] = t.label;

  /** Which tab this page is. Read from the page, never guessed from the URL:
   *  `/swap.html`, `/swap` and `/swap/` are the same page and a URL sniffer
   *  has to be taught each one. The page already knows. */
  function pageId() {
    return (document.body && document.body.dataset.coPage) || '';
  }

  function currentTab() {
    const p = pageId();
    return under()[p] || p;
  }

  /** The staging drawer, for a page that has none of its own.
   *
   *  Four pages ship their own `#drawer` and they are NOT identical -- the
   *  MapEditor's omits the install picker, the swap page's carries amend,
   *  clear and the install record. Unifying those is a separate change with
   *  its own risk; this only fills in a page that would otherwise have a
   *  staging button opening nothing. `Swap.initDrawer()` is already a no-op
   *  without `#drawer`, so the failure without this would be silent.
   *
   *  MUST NOT RUN DURING PARSE. This script executes where it sits, near the
   *  top of <body>, so the page's own `#drawer` -- which lives near the
   *  bottom -- does not exist yet and `getElementById` returns null. Called
   *  from there it injected a SECOND drawer into every page that already had
   *  one: two `#drawer` elements, two `#btn-dry`, two `#btn-install`, and
   *  `querySelector` silently picking the first. Found by driving the page,
   *  not by reading it; both copies were `hidden` and the bar looked perfect.
   *  Hence the DOMContentLoaded deferral at the bottom of this file.
   */
  function ensureDrawer() {
    if (document.getElementById('drawer')) return false;
    const d = document.createElement('div');
    d.id = 'drawer';
    d.className = 'hidden';
    d.innerHTML =
      '<div class="drawer-inner">' +
        '<div class="drawer-head">' +
          '<h2>Mod staging</h2>' +
          '<button id="drawer-close" class="ghost">close</button>' +
        '</div>' +
        '<p class="mut small">Staged files live in <code id="stage-dir"></code>. ' +
        'Nothing touches the game install until you press <b>Install</b>, which ' +
        'runs <code>comod.py install --yes</code> &mdash; the same path as the ' +
        'CLI, with its backups and its <code>uninstall</code> revert.</p>' +
        '<div id="stage-list"></div>' +
        '<label class="small mut" style="display:block;margin-top:8px">Install into' +
          '<select id="install-select" style="max-width:100%"></select></label>' +
        '<input id="install-path" type="text" autocomplete="off" ' +
               'style="width:100%;margin-top:4px" ' +
               'placeholder="or paste the path to another game install">' +
        '<div id="install-note" class="small mut" style="margin-top:4px"></div>' +
        '<div class="row">' +
          '<button id="btn-dry" class="ghost">Install (dry run)</button>' +
          '<button id="btn-install" class="danger">Install for real</button>' +
          '<button id="btn-uninstall" class="ghost">Uninstall / revert</button>' +
        '</div>' +
        '<pre id="mod-output" class="out"></pre>' +
      '</div>';
    document.body.appendChild(d);
    return true;
  }

  /** Draw the bar into `#co-nav`. Returns what it drew, so a test or a
   *  console can ask rather than re-deriving it from the markup. */
  function render() {
    const host = document.getElementById('co-nav');
    if (!host) return null;
    const cur = currentTab();

    host.innerHTML = '';
    const bar = document.createElement('nav');
    bar.className = 'co-tabs';
    bar.setAttribute('aria-label', 'Sections');

    // LEFT: the one control. A button, because it opens a panel on this page
    // rather than going anywhere.
    const mods = document.createElement('button');
    mods.type = 'button';
    mods.id = 'btn-mods';
    mods.className = 'co-stage';
    mods.textContent = 'Mod staging';
    mods.title = 'What is staged to replace an asset, and the install that ' +
                 'applies it. Nothing reaches the game until you press Install.';
    // Built first, appended LAST. The destinations lead the bar and the
    // staging control sits at the far end -- reading order matches the two
    // kinds of thing: where you can go, then what you can do here.
    // Assembling the button before the tabs keeps its wiring where it was,
    // so only the order in the DOM changed and nothing had to be re-bound.

    // LEFT: the destinations.
    const list = document.createElement('div');
    list.className = 'co-tablist';
    const drawn = tabs();
    for (const t of drawn) {
      const a = document.createElement('a');
      a.className = 'co-tab';
      a.id = 'co-tab-' + t.id;
      a.href = t.href;
      a.textContent = t.label;
      if (t.id === cur) {
        a.classList.add('on');
        // The whole point of the bar: one of these, always, and it is the
        // page you are on. `aria-current` is the machine-readable half of
        // the same statement the highlight makes.
        a.setAttribute('aria-current', 'page');
      }
      list.appendChild(a);
    }
    bar.appendChild(list);

    const gap = document.createElement('span');
    gap.className = 'co-gap';
    bar.appendChild(gap);

    // RIGHT: the one control in the bar. It is not a destination and never
    // takes `aria-current`; the gap is what says so without decoration.
    bar.appendChild(mods);

    host.appendChild(bar);

    // `tabs` is what was DRAWN, not how many exist: in CCO mode those differ,
    // and a driven test asking "did the mode take effect" needs the drawn
    // count. `mode` rides along so the answer names its own cause.
    return { page: pageId(), current: cur, mode: mode(),
             tabs: drawn.length, of: TABS.length };
  }

  /** Make the injected drawer work.
   *
   *  ONLY for a page whose drawer this file injected. The four pages that
   *  ship their own `#drawer` also call `Swap.configure` / `Swap.initDrawer`
   *  themselves with their own `api` and `toast` -- doing it twice would bind
   *  every install button twice, and "Install for real" running twice is not
   *  a cosmetic bug. So the condition is exactly "I made this drawer", not a
   *  guess about which page we are on.
   *
   *  The `api`/`toast` here are the smallest pair that satisfies swap.js's
   *  contract, for a page (Settings) that has neither.
   */
  function wireStaging() {
    if (typeof Swap === 'undefined' || !Swap.initDrawer) return false;
    if (!document.getElementById('toast')) {
      const t = document.createElement('div');
      t.id = 'toast';
      t.className = 'hidden';
      document.body.appendChild(t);
    }
    const toast = (msg, ms) => {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.remove('hidden');
      clearTimeout(toast._t);
      toast._t = setTimeout(() => t.classList.add('hidden'), ms || 3000);
    };
    // Plain `fetch`: csrf.js replaces `window.fetch` with one that attaches
    // `X-CO-Token` to every same-origin POST, so the token is handled by
    // loading that file rather than by anything written here. A page that
    // wants staging must load `/ui/csrf.js`, or the install POSTs are
    // rejected -- and the drawer would open and list correctly and fail only
    // on the button, which is the worst place to find out.
    const api = async (url, opt) => {
      const r = await fetch(url, opt);
      const txt = await r.text();
      if (!r.ok) throw new Error(txt.slice(0, 300) || ('HTTP ' + r.status));
      try { return JSON.parse(txt); } catch (e) { return txt; }
    };
    Swap.configure({ api, toast });
    Swap.initDrawer();
    return true;
  }

  return { TABS, MODES, UNDER, TITLE, mode, tabs, under,
           render, pageId, currentTab, ensureDrawer,
           wireStaging };
})();

if (typeof window !== 'undefined') {
  window.CoNav = CoNav;

  // The BAR is drawn now, synchronously, where this script sits. It has to
  // be: swap.js and the page scripts run from the bottom of the document and
  // bind `#btn-mods`, so the button must already exist by then.
  CoNav.render();

  // The DRAWER is not, and neither is its wiring. Both need the WHOLE
  // document -- the page's own `#drawer` to decide whether to inject one at
  // all, and swap.js to be loaded before anything can be bound to it.
  const later = () => {
    if (CoNav.ensureDrawer()) CoNav.wireStaging();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', later);
  } else {
    later();
  }
}
