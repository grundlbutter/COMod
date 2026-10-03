/* settpd.js -- the texture cache, on the settings page (`#sec-tpd`).
 *
 * One row per declared client. A client whose textures sit in `.tpd`
 * archives -- 7878 and Zephyr among the declared nine -- pays a zlib inflate
 * on every texture read (7878 MEASURED 6.09 ms per texture read, against
 * 0.5-1.4 ms on most WDF clients). The cache keeps the inflated bytes on disk
 * so the second read skips it. 6609 and 6090 ALSO read slowly (6-8 ms) but
 * ship no `.tpd`: that cost is not an inflate and this cache does not help it.
 *
 * It costs disk, so it is OFF until you switch it on, per client, and the
 * disk it costs is shown on the row -- counted from the folder each time the
 * page asks, never remembered. "Prepare" fills it ahead of time; without it
 * the cache fills as textures are viewed. "Clear" deletes the files and
 * leaves the switch as it is.
 *
 * Nothing here is decided in the browser: `/api/tpdcache` says which
 * installs have `.tpd` at all, and refuses anything else with the reason.
 */
'use strict';

(function () {
  const host = document.querySelector('#tpd-body');
  if (!host) return;

  const mk = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  };

  // Plain `fetch`: csrf.js attaches the token to every same-origin POST.
  async function jget(path) {
    const r = await fetch(path);
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(doc.error || ('HTTP ' + r.status));
    return doc;
  }
  async function jpost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(doc.error || ('HTTP ' + r.status));
    return doc;
  }

  function size(n) {
    if (!n) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
  }

  const note = mk('div', 'set-note');
  let timer = null;

  function jobLine(job) {
    if (!job) return '';
    if (job.state === 'running') {
      const pct = job.total ? Math.floor(100 * job.done / job.total) : 0;
      return `Preparing: ${job.done.toLocaleString()} of ` +
             `${job.total.toLocaleString()} entries (${pct}%), ${job.seconds} s`;
    }
    if (job.state === 'failed') return `Prepare failed: ${job.error}`;
    if (job.state === 'refused') return `Prepare refused: ${(job.result || {}).reason || ''}`;
    const r = job.result || {};
    const verb = job.state === 'stopped' ? 'Stopped' : 'Prepared';
    return `${verb}: ${(r.stored || 0).toLocaleString()} entries stored, ` +
           `${(r.alreadyPresent || 0).toLocaleString()} already there` +
           `${r.failed ? `, ${r.failed} unreadable` : ''}, in ${r.seconds} s`;
  }

  async function act(root, body, what) {
    note.textContent = what + '…';
    note.classList.remove('set-bad');
    try {
      await jpost('/api/tpdcache', { root, ...body });
      note.textContent = '';
    } catch (e) {
      note.textContent = e.message;
      note.classList.add('set-bad');
    }
    render();
  }

  async function render() {
    let doc;
    try {
      doc = await jget('/api/tpdcache');
    } catch (e) {
      host.textContent = 'Could not read the texture cache: ' + e.message;
      return;
    }
    host.textContent = '';
    const list = mk('div', 'set-clientlist');
    let anyRunning = false;
    for (const row of doc.installs || []) {
      const r = mk('div', 'set-clientrow');
      const head = mk('div', 'set-head');
      head.appendChild(mk('span', 'set-name', row.kind + (row.browsing ? ' (browsing)' : '')));
      head.appendChild(mk('span', 'set-groupname', row.root));
      r.appendChild(head);
      if (!row.hasTpd) {
        r.appendChild(mk('div', 'set-measured',
          'No .tpd archives: textures are read without an inflate, so there is nothing to cache.'));
        list.appendChild(r);
        continue;
      }
      const ctl = mk('div', 'set-head');
      const lab = mk('label');
      const box = mk('input');
      box.type = 'checkbox';
      box.checked = !!row.enabled;
      box.addEventListener('change', () =>
        act(row.root, { enabled: box.checked },
            box.checked ? 'Switching the cache on' : 'Switching the cache off'));
      lab.appendChild(box);
      lab.appendChild(document.createTextNode(' Cache inflated textures'));
      ctl.appendChild(lab);
      ctl.appendChild(mk('span', 'set-groupcount',
        `${size(row.bytes)} on disk, ${(row.files || 0).toLocaleString()} files`));
      const running = row.job && row.job.state === 'running';
      anyRunning = anyRunning || running;
      const prep = mk('button', null, running ? 'Stop' : 'Prepare this client');
      prep.disabled = !row.enabled && !running;
      prep.title = row.enabled ? 'Inflate every .tpd entry now, in the background'
                               : 'Switch the cache on first';
      prep.addEventListener('click', () =>
        act(row.root, running ? { cancel: true } : { prepare: true },
            running ? 'Stopping' : 'Starting'));
      ctl.appendChild(prep);
      const clr = mk('button', null, 'Clear');
      clr.disabled = !row.files || running;
      clr.addEventListener('click', () => {
        if (!confirm(`Delete ${size(row.bytes)} of cached textures for ${row.kind}? ` +
                     'They are rebuilt as textures are viewed, or by Prepare.')) return;
        act(row.root, { clear: true }, 'Clearing');
      });
      ctl.appendChild(clr);
      r.appendChild(ctl);
      const line = jobLine(row.job);
      if (line) r.appendChild(mk('div', 'set-measured', line));
      r.appendChild(mk('div', 'set-measured', row.dir || ''));
      list.appendChild(r);
    }
    host.appendChild(list);
    host.appendChild(note);
    clearTimeout(timer);
    if (anyRunning) timer = setTimeout(render, 1500);
  }

  render();
})();
