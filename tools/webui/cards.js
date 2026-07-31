/* cards.js -- collapsible right-hand panels, for BOTH pages.
 *
 * WHY THIS FILE EXISTS
 *
 *   `style.css` styled `.card h2` with `cursor: pointer; user-select: none`,
 *   which is a *promise* that the header is clickable. The builder page kept
 *   that promise: its four cards carry `data-card`, a `.cardtoggle` button and
 *   a `.cardbody` wrapper, and `builder.js` bound the listeners.
 *
 *   The asset browser page never did. Its eight cards had none of the three
 *   and `app.js` bound nothing — so every header showed a hand cursor and did
 *   absolutely nothing when clicked. That is the reported bug, exactly: "I can
 *   see the cursor change, but it doesnt collaps when clicked."
 *
 *   A hover style is not evidence of a listener. Having the markup on one page
 *   and the CSS on both is what let the two drift apart, so the behaviour now
 *   lives in one file that both pages load, the cursor rule is scoped to
 *   `.card[data-card]`, and `initCards()` refuses to style a card it cannot
 *   actually operate.
 *
 * Contract for a collapsible card:
 *
 *   <section class="card" data-card="NAME">
 *     <h2>Title</h2>                 <- the toggle button is injected here
 *     <div class="cardbody"> ... </div>
 *   </section>
 *
 * `.card.collapsed .cardbody { display: none }` does the hiding; the state is
 * one flat `{name: bool}` object in localStorage under the key you pass.
 */

'use strict';

const CardPanels = (() => {
  let KEY = 'coviewer.collapsed';
  let state = {};

  const cards = () => [...document.querySelectorAll('.card[data-card]')];

  /** A card is only operable if it has somewhere to put the collapsed content.
   *  Reporting the ones that do not is how the browser page's eight silent
   *  cards would have been caught the first time. */
  function unwired() {
    return cards().filter(c => !c.querySelector('.cardbody'))
                  .map(c => c.dataset.card);
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); }
    catch (e) { /* private mode: the panels still work, just not across loads */ }
  }

  function apply() {
    for (const card of cards()) {
      const on = !!state[card.dataset.card];
      card.classList.toggle('collapsed', on);
      const t = card.querySelector('.cardtoggle');
      if (t) {
        t.textContent = on ? '▸' : '▾';
        t.setAttribute('aria-expanded', String(!on));
        t.setAttribute('aria-label', (on ? 'Expand ' : 'Collapse ') +
                       (card.querySelector('h2') || {}).textContent);
      }
    }
  }

  function set(name, on) {
    state[name] = !!on;
    save();
    apply();
  }

  function toggle(name) { set(name, !state[name]); }

  /** One state, not per-card: with anything open this collapses the lot, with
   *  nothing open it expands the lot. */
  function toggleAll() {
    const list = cards();
    const anyOpen = list.some(c => !state[c.dataset.card]);
    for (const c of list) state[c.dataset.card] = anyOpen;
    save();
    apply();
    return anyOpen;         // true = we just collapsed everything
  }

  function anyOpen() {
    return cards().some(c => !state[c.dataset.card]);
  }

  /**
   * Wire every `.card[data-card]` on the page.
   *
   * @param {string} key       localStorage key for this page's state
   * @param {string} allBtnId  id of an optional "collapse everything" button
   */
  function init(key, allBtnId) {
    KEY = key || KEY;
    try { state = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; }
    catch (e) { state = {}; }

    const missing = unwired();
    if (missing.length) {
      // Loud, because the failure mode is silence: a card with no .cardbody
      // collapses to nothing visible and looks exactly like a dead listener.
      console.warn('cards.js: no .cardbody in card(s)', missing.join(', '),
                   '- their headers would click and do nothing');
    }

    for (const card of cards()) {
      const h2 = card.querySelector('h2');
      if (!h2) continue;
      if (!card.querySelector('.cardtoggle')) {
        const b = document.createElement('button');
        b.className = 'cardtoggle';
        b.type = 'button';
        b.setAttribute('aria-expanded', 'true');
        b.textContent = '▾';
        h2.insertBefore(b, h2.firstChild);
      }
      h2.addEventListener('click', () => toggle(card.dataset.card));
    }
    apply();

    const btn = allBtnId && document.getElementById(allBtnId);
    if (btn) {
      const label = () => {
        btn.textContent = anyOpen() ? 'Collapse panels' : 'Expand panels';
      };
      btn.addEventListener('click', () => { toggleAll(); label(); });
      label();
    }
    return { count: cards().length, unwired: missing };
  }

  return { init, apply, set, toggle, toggleAll, anyOpen,
           get state() { return state; },
           set state(v) { state = v || {}; save(); apply(); } };
})();

if (typeof window !== 'undefined') window.CardPanels = CardPanels;
