/* dirscan.js -- the launch scan for undeclared client folders.
 *
 * Runs once when the viewer's main page opens, asks the server what client
 * folders sit beside the declared ones, and -- if there are any -- says so.
 *
 * IT DECLARES NOTHING. THAT IS THE WHOLE DESIGN.
 * ---------------------------------------------
 * `/api/installs/scan` is a GET and a read; it returns an offer. This file
 * renders that offer as a prompt with two answers, and the only thing either
 * answer does is decide whether to open the Settings page. Adopting a folder
 * needs the picker on that page, an explicit kind, and a click.
 *
 * The owner's ruling, in their words: nothing is ever declared silently. A
 * tool that adopts directories on startup is one bad guess away from indexing
 * the wrong tree -- and the wrong parse profile is then baked into an index
 * directory name, a thumbnail cache and every table the viewer reads.
 *
 * WHY IT IS NOT A NAG
 * -------------------
 * The prompt is keyed on the SET OF PATHS it found. "Not now" silences it for
 * this browser session; "Don't ask again" remembers those exact paths, so a
 * folder added tomorrow still gets offered while the ones already declined
 * stay quiet. A dismissal that also swallowed the next discovery would make
 * the scan worse than not having one.
 */

'use strict';

(function () {
  const SEEN_KEY = 'co.dirscan.declined';   // paths the user said no to
  const SESSION_KEY = 'co.dirscan.later';   // silenced for this tab session

  const mk = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  };

  function declined() {
    try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); }
    catch (e) { return []; }
  }
  function decline(paths) {
    const all = declined().concat(paths);
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(all)); }
    catch (e) { /* private mode: the prompt simply returns next launch */ }
  }

  function show(found) {
    const host = mk('div', 'overlay');
    host.id = 'dirscan-prompt';
    const card = mk('div', 'setup-card');
    card.appendChild(mk('h2',  null,
      found.length === 1 ? 'A client folder is not declared'
                         : found.length + ' client folders are not declared'));
    card.appendChild(mk('p', null,
      'These look like Conquer Online clients and this tool has not been ' +
      'told about them. Nothing has been added — a folder is only adopted ' +
      'when you say so, on the Settings page, with the kind you choose.'));
    const ul = mk('ul');
    for (const c of found) {
      const li = mk('li');
      li.appendChild(mk('code', null, c.root));
      const v = c.verdict === 'confident'
        ? '  looks like ' + c.suggested + ' (' +
          (c.confidence * 100).toFixed(0) + '%)'
        : c.verdict === 'tie'
          ? '  two plugins tied — this one needs an answer'
          : c.verdict === 'weak'
            ? '  best guess only (' + (c.confidence * 100).toFixed(0) +
              '%) — this one needs an answer'
            : '  no plugin claimed it';
      li.appendChild(mk('span', 'small', v));
      ul.appendChild(li);
    }
    card.appendChild(ul);

    const acts = mk('div', 'setup-actions');
    const go = mk('button', 'primary', 'Review in Settings');
    go.id = 'dirscan-review';
    go.addEventListener('click', () => {
      window.location.href = '/settings#sec-dirs';
    });
    const later = mk('button', 'ghost', 'Not now');
    later.id = 'dirscan-later';
    later.addEventListener('click', () => {
      try { sessionStorage.setItem(SESSION_KEY, '1'); } catch (e) {}
      host.remove();
    });
    const never = mk('button', 'ghost', 'Don’t ask about these again');
    never.id = 'dirscan-never';
    never.addEventListener('click', () => {
      decline(found.map(c => c.root));
      host.remove();
    });
    acts.appendChild(go);
    acts.appendChild(later);
    acts.appendChild(never);
    card.appendChild(acts);
    card.appendChild(mk('p', 'mut small',
      'The same list, with the full plugin ranking for each folder, is under ' +
      'Settings → Directory management.'));
    host.appendChild(card);
    document.body.appendChild(host);
  }

  (async () => {
    try {
      if (sessionStorage.getItem(SESSION_KEY) === '1') return;
    } catch (e) { /* no sessionStorage: prompt anyway, it is one dialog */ }
    let res;
    try {
      const r = await fetch('/api/installs/scan');
      if (!r.ok) return;                  // the viewer still works; be quiet
      res = await r.json();
    } catch (e) { return; }
    if (!res || res.error || !res.scanned) return;
    const no = new Set(declined().map(s => String(s).toLowerCase()));
    const found = (res.candidates || [])
      .filter(c => c.client && !no.has(String(c.root).toLowerCase()));
    if (!found.length) return;
    // The first-run health dialog owns the screen when it is up; two overlays
    // stacked is two questions and no order to answer them in.
    const fr = document.querySelector('#firstrun');
    if (fr && !fr.classList.contains('hidden')) {
      const obs = new MutationObserver(() => {
        if (fr.classList.contains('hidden')) { obs.disconnect(); show(found); }
      });
      obs.observe(fr, { attributes: true, attributeFilter: ['class'] });
      return;
    }
    show(found);
  })();
})();
