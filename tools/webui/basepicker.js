/* basepicker.js -- the active path: what this page is drawing from.
 *
 * ONE CONTROL, BECAUSE THERE IS ONE QUESTION
 * ------------------------------------------
 * There used to be two dropdowns here, "Client" and "Server". That split was
 * the tool's internal shape leaking onto the screen: an installed client and
 * a COmmunity Library server are both just a path the viewer draws from, and
 * separating them left the user holding a distinction that is ours, plus two
 * controls whose legal combinations they had to work out. One list now.
 *
 * The parser plugin is deliberately NOT a control. It follows the path --
 * `Catalog.plugin` resolves it from the root via `coroot.kind_for_root` --
 * and everything downstream follows from that: which tables are
 * authoritative, where colour lives in an id, which socket a slot hangs off,
 * whether a colour set is measured or inherited. Choosing the path is the
 * only decision there is to make.
 *
 * WHY SWITCHING KEEPS YOUR PLACE
 * ------------------------------
 * It exists to answer a question no single client can: **is what I am
 * looking at a regression in this tool, or a difference between the
 * clients?** A left-hand weapon that flattens on both official bases is the
 * art (6090 rewrote `v_l_weapon`'s dummy track and 5517 inherits it byte for
 * byte); one that flattens on only one is ours.
 *
 * So the loadout and the chosen action are already in `localStorage`, and the
 * reload restores them: the same figure in the same pose comes back drawn
 * from the other path. A comparison you have to set up twice is a comparison
 * nobody makes twice.
 *
 * The list is whatever is loaded -- installs the user has declared
 * (`coroot.declare_kind`) plus any library servers. Nothing here enumerates
 * clients; declare another install on the setup page and it appears.
 *
 * WHERE IT RUNS. Any page that puts `#path-label` / `#path-select` in its
 * header and loads this file. It uses the page's own `api()` and `toast()`
 * when they exist and falls back to plain `fetch` when they do not, so a page
 * needs no helpers of its own to get the control -- which is how the Map
 * Editor and the Effects Viewer got it without either growing a network
 * layer. The Effects Viewer has no `api()` at all.
 */
'use strict';

/** The page's `api()` if it has one, else a minimal equivalent.
 *
 * Every page that wants this control should not have to grow a network
 * helper for it. The fallback matches what the pages that DO have one do:
 * throw on a non-2xx with the body as the message, and parse JSON.
 */
function bpApi(url, opts) {
  if (typeof api === 'function') return api(url, opts);
  return fetch(url, opts).then(async r => {
    if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
    return r.json();
  });
}

function bpToast(msg, ms) {
  if (typeof toast === 'function') { toast(msg, ms); return; }
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(bpToast._t);
  bpToast._t = setTimeout(() => t.classList.add('hidden'), ms || 2200);
}

async function initBasePicker() {
  const sel = document.getElementById('path-select');
  const label = document.getElementById('path-label');
  if (!sel || !label) return;

  let doc;
  try { doc = await bpApi('/api/bases'); } catch (e) { return; }
  const paths = (doc && doc.paths) || [];
  // One path is not a choice. Stay out of the header rather than offer a
  // control whose only option is the state you are already in.
  if (paths.length < 2) return;

  sel.innerHTML = '';
  let group = null;
  for (const p of paths) {
    // "not built" is not "broken": a base with no index has simply never had
    // meshtex run for it, and saying which is the difference between "go
    // build it" and "go debug it".
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.label + (p.note ? '  — ' + p.note : '');
    o.title = (p.detail || '') + (p.plugin ? '\nparser plugin: ' + p.plugin : '');
    if (p.kind !== group) {
      group = p.kind;
      const g = document.createElement('optgroup');
      // Named after what the entry IS, not where we keep it or how it got
      // here. "Installed" was the wrong axis: Classic Conquer 2.0 is a
      // private server's client that happens to sit in Program Files, and
      // grouping by installed-ness filed it beside TQ's own releases. The
      // plugin declares its `origin`; this only renders it.
      //
      // "OFFLINE CLIENTS", NOT "OFFICIAL CLIENTS" -- and not a path test.
      // These are the standalone TQ patch clients kept locally to compare
      // against: nothing to log into, no server behind them. The owner
      // describes the set by where it sits ("anything in
      // ConquerAssets/Clients"), and today that folder holds exactly this
      // group -- but membership is still decided by the plugin's declared
      // `origin`, for two reasons. It keeps the one axis this picker already
      // chose (what a client IS, not where it lives), and `ConquerAssets/` is
      // a GENERATED tree -- `tools/extract_comod.py` writes it, its location
      // is not a fact about the repo, and `tests/test_sanitization.py` exists
      // precisely so shipped code does not learn one machine's directories.
      // A patch client declared from a USB stick is still an offline client.
      g.label = {install: 'Offline Clients',
                 server: 'Private server clients',
                 collection: 'Curated collections',
                 other: 'Unclassified clients'}[p.kind] || p.kind;
      sel.appendChild(g);
    }
    sel.lastChild.appendChild(o);
  }
  sel.value = doc.current || paths[0].id;
  label.classList.remove('hidden');

  sel.addEventListener('change', async () => {
    const id = sel.value;
    const prev = doc.current || '';
    sel.disabled = true;
    bpToast('switching… the page reloads from the new path', 8000);
    try {
      await bpApi('/api/base', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: id }),
      });
      // Everything derived from the path -- model list, appearance tables,
      // cached clips, thumbnails -- belongs to the old one. Reload rather
      // than invalidate them one by one; the state worth keeping is already
      // in localStorage.
      location.reload();
    } catch (e) {
      sel.disabled = false;
      sel.value = prev;
      bpToast('switch failed: ' + e.message, 6000);
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBasePicker);
} else {
  initBasePicker();
}
