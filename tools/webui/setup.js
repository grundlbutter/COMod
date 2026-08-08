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
