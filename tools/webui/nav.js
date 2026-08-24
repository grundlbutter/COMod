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
    { id: 'viewer',   href: '/',         label: 'Asset Viewer' },
    { id: 'builder',  href: '/builder',  label: 'Character Builder' },
    { id: 'models',   href: '/models',   label: 'Model Viewer' },
    { id: 'mapedit',  href: '/mapedit',  label: 'Map Editor' },
    { id: 'settings', href: '/settings', label: 'Settings' },
  ];

  /** Pages that are not themselves tabs, and the tab they belong under.
   *
   *  `/swap` is a real route and deliberately not a sixth tab: it is the
   *  Asset Viewer's swap flow with the window to itself, reached from the
   *  viewer, and its own <h2> says so. Mapping it here is what keeps "exactly
   *  one tab is current" true on a page that is not a tab, rather than
   *  leaving the bar with nothing lit and the reader with no idea which
   *  section they are in. */
  const UNDER = { swap: 'viewer' };

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
    return UNDER[p] || p;
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
    bar.appendChild(mods);

    const gap = document.createElement('span');
    gap.className = 'co-gap';
    bar.appendChild(gap);

    // RIGHT: the destinations.
    const list = document.createElement('div');
    list.className = 'co-tablist';
    for (const t of TABS) {
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
    host.appendChild(bar);

    return { page: pageId(), current: cur, tabs: TABS.length };
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

  return { TABS, UNDER, TITLE, render, pageId, currentTab, ensureDrawer,
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
