/* setup.js -- the page shown when no game install could be located.
 *
 * The server is up; it just has no catalogue. This page reports exactly what
 * auto-detection tried, takes a path, validates it server-side against the
 * files that must exist, and remembers it. */

'use strict';

const $ = s => document.querySelector(s);

function say(text, cls) {
  const m = $('#msg');
  m.textContent = text;
  m.className = 'setup-msg' + (cls ? ' ' + cls : '');
}

async function load() {
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
        kind: (document.querySelector('input[name="kind"]:checked') || {}).value || '',
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
