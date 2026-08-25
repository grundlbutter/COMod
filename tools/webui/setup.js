/* setup.js -- the page shown when no game install could be located.
 *
 * The server is up; it just has no catalogue. This page reports exactly what
 * auto-detection tried, takes a path, validates it server-side against the
 * files that must exist, and remembers it. */

'use strict';

const $ = s => document.querySelector(s);

/* CCO MODE -- one prefilled field, no parser-plugin picker, no detection log.
 *
 * This is a MODE OF THIS PAGE, not a second page. setup.html stays one
 * document and CCO hides the parts it does not need, rather than gaining a
 * setup-cco.html that would drift from this one the first time the
 * required-files list or the save flow changed. Same argument nav.js makes
 * for the tab bar, and the same defect shape behind it.
 *
 * WHAT IT REMOVES IS A CHOICE, NEVER A CHECK. `/api/setroot` still validates
 * the folder against the named plugin's own `confidence` hook before saving
 * it -- hiding the picker means the answer is SUPPLIED, not that it stops
 * being verified. A path that is not a CCO install is still refused, with its
 * reason, in the same `#msg`.
 *
 * The mode and the default root both come from `/api/mode`. This page does
 * not carry the path: `coroot.CONVENTIONAL_ROOT` is where it is written down,
 * and a copy here is the one that keeps naming the old folder after a rename.
 */
let MODE = { mode: 'full', defaultRoot: '', kind: '' };

/* ONE fetch, awaited by BOTH consumers -- `load()` and the plugin-picker IIFE
 * at the bottom of this file. They are independent async entry points that
 * both start at parse time, so a plain `await` inside `load()` would leave the
 * picker racing it: the IIFE would test a block that `applyMode` had not
 * hidden yet, enumerate the plugins anyway, and CCO mode would show a picker
 * on a fast machine and hide it on a slow one. A shared promise makes the
 * ordering a fact rather than a timing accident. */
const MODE_READY = fetch('/api/mode')
  .then(r => r.json())
  .then(d => { MODE = d; return d; })
  .catch(() => MODE);        // an older viewer with no /api/mode: stay full

function applyMode() {
  if (MODE.mode !== 'cco') return;
  document.body.classList.add('setup-cco');
  for (const id of ['#kinds-block', '#searched-block']) {
    const b = $(id);
    // Whole blocks, by id. Hiding only the inner control would leave its
    // heading and its explanatory paragraph behind, describing a picker that
    // is not on the page.
    if (b) b.hidden = true;
  }
  const h = document.querySelector('header .mut');
  if (h) h.textContent = 'first run — CCO Swap';
  const path = $('#path');
  if (path && !path.value && MODE.defaultRoot) path.value = MODE.defaultRoot;
  const note = $('#msg');
  if (note && !note.textContent) {
    note.textContent = 'CCO Swap expects Classic Conquer 2.0 at the path ' +
                       'above. Change it if yours is elsewhere.';
  }
}

function say(text, cls) {
  const m = $('#msg');
  m.textContent = text;
  m.className = 'setup-msg' + (cls ? ' ' + cls : '');
}

async function load() {
  // The mode FIRST, and awaited: everything below either fills in a block
  // CCO hides or prefills the field CCO owns, so learning the mode after
  // painting would show the full detection flow and then snatch it away.
  await MODE_READY;
  applyMode();

  let rep;
  try {
    rep = await (await fetch('/api/health')).json();
  } catch (e) {
    $('#searched').textContent = 'could not reach the server: ' + e.message;
    return;
  }

  const req = $('#required');
  req.textContent = '';
  for (const name of ['c3.wdf', 'data.wdf', 'ini/', 'bin/64/']) {
    const li = document.createElement('li');
    li.textContent = name;
    req.appendChild(li);
  }

  const inst = rep.install || {};
  const tried = inst.searched || [];
  $('#searched').textContent = tried.length
    ? `checked ${inst.searchedCount} location(s):\n\n` +
      tried.map(t => `[${t.source}] ${t.path}\n    ${t.verdict}`).join('\n')
    : 'no candidates were produced — auto-detection found nothing to check.';

  // Prefill with the most likely candidate so the common case is one click.
  // Prefill with the most likely candidate so the common case is one click.
  // `!value` is what keeps CCO mode's own prefill: `applyMode` ran above and
  // has already put `defaultRoot` there, and detection's guess must not
  // overwrite the path the mode exists to assume.
  const guess = tried.find(t => t.source === 'default-path');
  if (guess && !$('#path').value) $('#path').value = guess.path;

  const other = [];
  const py = rep.python || {};
  other.push(`Python ${py.value} — ${py.ok ? 'ok' : 'TOO OLD, need ' + py.requirement}`);
  for (const p of rep.packages || []) {
    other.push(`${p.name} ${p.present ? p.version : '— NOT INSTALLED: ' + p.fix}` +
               (p.required ? ' (required)' : ' (optional)'));
  }
  $('#other').textContent = other.join('\n');
  $('#other').style.whiteSpace = 'pre-wrap';
}

$('#save').addEventListener('click', async () => {
  const path = $('#path').value.trim();
  if (!path) { say('Type a path first.', 'bad'); return; }
  $('#save').disabled = true;
  say('checking…');
  try {
    const r = await fetch('/api/setroot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        // The picked plugin, or -- in CCO mode, where there is no picker --
        // the kind the mode declares. Still only a SUPPLIED answer: the
        // server puts it to that plugin's own `confidence` hook and refuses
        // a folder that is not one.
        kind: (document.querySelector('input[name="kind"]:checked') || {}).value
              || MODE.kind || '',
        scope: $('#scope-repo').checked ? 'repo' : 'user',
      }),
    });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      say(j.error || 'that path was rejected', 'bad');
    } else {
      say(`Saved to ${j.savedTo}. Restart the viewer (close this window and ` +
          `run "Asset Viewer.cmd" again) — it needs to build the catalogue.`, 'ok');
    }
  } catch (e) {
    say('failed: ' + e.message, 'bad');
  } finally {
    $('#save').disabled = false;
  }
});

$('#path').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('#save').click();
});

load();


/* The client-kind picker is the plugin registry, rendered. Nothing here
   knows what plugins exist -- /api/plugins discovers them, so a
   contributor's module shows up by being present. */
(async () => {
  const host = document.getElementById('kinds');
  if (!host) return;
  // No picker in CCO mode, so no reason to enumerate plugins for it. The
  // shared promise is what makes this deterministic -- see MODE_READY.
  await MODE_READY;
  if (MODE.mode === 'cco') return;
  let d;
  try { d = await (await fetch('/api/plugins')).json(); }
  catch (e) { host.textContent = 'could not list parser plugins: ' + e.message; return; }
  host.innerHTML = '';

  /* A SHORT LIST IS THE FAILURE, so the short list is what has to speak.
     Before this, a checkout where plugins/catalog.py would not import
     rendered ONE radio button and looked exactly like a machine with one
     client -- nothing on the page, and nothing in the payload, told the two
     apart. `error` is discovery refusing to answer at all (one of this
     repo's own modules is broken); `problems` is the survivable half, a
     contributor's plugin that was skipped, which is still worth saying out
     loud rather than deducting from a count nobody knows. */
  if (d.error) {
    const b = document.createElement('div');
    b.className = 'warn';
    b.textContent = 'The parser plugin list could not be built, so no client '
                  + 'kinds are offered. This is a broken checkout, not a '
                  + 'machine without clients. ' + d.error;
    host.appendChild(b);
  }
  for (const pr of (d.problems || [])) {
    const n = document.createElement('div');
    n.className = 'mut small';
    n.textContent = (pr.ours ? 'this repo’s module ' : 'contributed plugin ')
                  + 'plugins/' + pr.module + ' was skipped: ' + pr.why;
    host.appendChild(n);
  }

  for (const p of d.plugins) {
    const lab = document.createElement('label');
    lab.className = 'chk';
    const r = document.createElement('input');
    r.type = 'radio'; r.name = 'kind'; r.value = p.name;
    if (p.name === (d.suggested || d.current)) r.checked = true;
    lab.appendChild(r);
    lab.appendChild(document.createTextNode(' ' + p.label));
    if (p.notes) {
      const n = document.createElement('div');
      n.className = 'mut small';
      n.textContent = p.notes;
      lab.appendChild(n);
    }
    host.appendChild(lab);
  }
  if (!host.querySelector('input:checked')) {
    const first = host.querySelector('input');
    if (first) first.checked = true;
  }
})();
