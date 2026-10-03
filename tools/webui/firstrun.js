/* firstrun.js -- the health panel, and the thumbnail question.
 *
 * The one rule this file exists to enforce: **thumbnail generation is opt-in.**
 * A fresh clone has no `out/thumbs/`, and filling it is 15-30 minutes on a
 * modest machine and ~637 MB of disk. Nothing starts until someone picks an
 * option, the answer is remembered, and declining leaves a fully working
 * viewer -- assets without a thumbnail simply show a placeholder tile.
 *
 * Everything factual here (counts, megabytes, time estimates for THIS
 * machine, the reference measurement) comes from /api/health, which gets it
 * from tools/health.py. Nothing is hardcoded in the browser.
 */

'use strict';

(function () {
  const q = s => document.querySelector(s);
  const mk = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  };

  const host = q('#firstrun');
  const card = q('#firstrun-card');
  let poll = null;
  let lastReport = null;

  function open() { host.classList.remove('hidden'); }
  function close() {
    host.classList.add('hidden');
    if (poll) { clearInterval(poll); poll = null; }
  }

  async function jget(path) { return (await fetch(path)).json(); }
  async function jpost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  }

  // ---------------------------------------------------------------- health
  /* Rows are [level, label, value, detail] with level 'ok' | 'warn' | 'bad'.
   * Something optional that is simply absent is 'warn', never 'bad': a missing
   * thumbnail cache is a choice, not a fault, and a red cross beside it would
   * tell a first-time user their install is broken when it is not. */
  function healthRows(rep) {
    const box = mk('div');
    const rows = [];
    const lv = ok => (ok ? 'ok' : 'bad');
    const inst = rep.install || {};
    rows.push([lv(inst.ok), 'Game install',
               inst.found ? `${inst.path}` : 'not found',
               inst.found ? `found via ${inst.source} \u2014 ${inst.detail}`
                          : (inst.fix || '')]);
    for (const p of (inst.files || {}).parts || []) {
      rows.push([lv(p.exists && p.readable), '  ' + p.name,
                 p.exists ? (p.bytes ? (p.bytes / 1e6).toFixed(0) + ' MB' : 'present')
                          : 'MISSING', p.error || '']);
    }
    const py = rep.python || {};
    rows.push([lv(py.ok), 'Python', py.value, py.detail]);
    for (const p of rep.packages || []) {
      rows.push([p.present ? 'ok' : (p.required ? 'bad' : 'warn'), p.name,
                 p.present ? p.version : 'not installed',
                 p.present ? (p.required ? 'required' : 'optional')
                           : p.fix + '   \u2014 ' + p.why]);
    }
    const der = rep.derived || {};
    rows.push([der.ok ? 'ok' : 'warn', 'Derived data',
               der.ok ? 'built'
                      : `${(der.missing || []).length} of ` +
                        `${(der.artefacts || []).length} not built yet`,
               der.ok ? (der.dir || '')
                      : 'Until this is built the catalogue can only see loose ' +
                        'files \u2014 the archives have no filenames of their own. ' +
                        (der.fix || '')]);
    const th = rep.thumbnails || {};
    rows.push([th.ok ? 'ok' : 'warn', 'Thumbnails', th.status || 'unknown',
               th.status === 'none' ? 'none generated \u2014 optional, see below'
                 : `${(th.meshes || 0).toLocaleString()} mesh + ` +
                   `${(th.textures || 0).toLocaleString()} texture` +
                   (th.legacy ? ' (shown from an older per-checkout folder; ' +
                                'the shared folder is still empty)' : '')]);

    const MARK = { ok: '\u2713', warn: '!', bad: '\u2717' };
    for (const [level, what, value, detail] of rows) {
      const r = mk('div', 'health-row ' + level);
      r.appendChild(mk('span', 'mark', MARK[level]));
      const g = mk('span', 'what');
      g.appendChild(mk('b', null, what + ': '));
      g.appendChild(document.createTextNode(String(value)));
      if (detail) { g.appendChild(mk('div', 'fix', String(detail))); }
      r.appendChild(g);
      box.appendChild(r);
    }
    return box;
  }

  // ------------------------------------------------------------ thumbnails
  //
  // With a private server's client selected, generation targets that
  // server's own cache (out/thumbs/servers/<name>/) -- a much simpler card,
  // because the install-corpus estimates do not apply to an arbitrary client.
  // "library" is where we store the imported bytes; the thing on screen is a
  // game client, and saying "library" described our storage instead.
  function serverThumbPrompt(rep) {
    const th = rep.thumbnails || {};
    const box = mk('div');
    box.appendChild(mk('h3', null,
      `Thumbnails — “${rep.activeServer}” client`));
    if (th.status === 'generated') {
      box.appendChild(mk('p', null,
        `This client's cache holds ${(th.meshes || 0).toLocaleString()} ` +
        `mesh and ${(th.textures || 0).toLocaleString()} texture ` +
        `thumbnails. Re-running only renders what changed.`));
    } else {
      box.appendChild(mk('p', null,
        'No thumbnails have been generated for this client yet. ' +
        'Generation renders this client’s own meshes and textures ' +
        '(paired through the server’s tables and conventions) into ' +
        'its own cache — the base install’s thumbnails are ' +
        'never reused for it, because the same path can be different art.'));
    }
    const row = mk('div', 'setup-actions');
    const goM = mk('button', 'primary', 'Generate meshes');
    goM.addEventListener('click', () => start('meshes'));
    const goA = mk('button', 'primary', 'Generate meshes + textures');
    goA.addEventListener('click', () => start('all'));
    row.appendChild(goM);
    row.appendChild(goA);
    box.appendChild(row);
    box.appendChild(mk('p', 'mut small',
      'Resumable and cancellable, like the base run. Switch the Server ' +
      'menu back to base to generate for the install instead. CLI: ' +
      `py -3 tools/thumbs.py --library <dir> --server ${rep.activeServer} --all --textures`));
    return box;
  }

  function thumbPrompt(rep) {
    if (rep.activeServer) return serverThumbPrompt(rep);
    const th = rep.thumbnails || {};
    const plan = th.plan || {};
    const box = mk('div');

    box.appendChild(mk('h3', null, 'Thumbnails'));
    box.appendChild(mk('p', null,
      'The catalogue can show a rendered thumbnail for every mesh and every ' +
      'texture. None have been generated in this checkout yet. Generating ' +
      'them is entirely optional and is never started without you asking.'));

    const note = mk('p', 'mut small');
    note.textContent =
      `Estimates are for this machine — ${plan.cpus} cores, ` +
      `${plan.jobs} worker processes. How long it really takes depends ` +
      `heavily on the machine; on a 4-core laptop expect the upper end or ` +
      `beyond. Reference measurement: ${plan.reference}.`;
    box.appendChild(note);

    for (const opt of plan.options || []) {
      // "Textures only" is only meaningful once meshes exist.
      if (opt.id === 'textures' && th.status === 'none') continue;
      const row = mk('div', 'thumb-opt');
      const grow = mk('div', 'grow');
      grow.appendChild(mk('b', null, opt.label));
      grow.appendChild(mk('div', 'why', opt.why));
      grow.appendChild(mk('div', 'why',
        `${opt.count.toLocaleString()} images`));
      row.appendChild(grow);
      const right = mk('div');
      right.appendChild(mk('div', 'cost', opt.estimate));
      right.appendChild(mk('div', 'cost', opt.megabytes + ' MB'));
      const go = mk('button', 'primary', 'Generate');
      go.addEventListener('click', () => start(opt.id));
      right.appendChild(go);
      row.appendChild(right);
      box.appendChild(row);
    }

    const adv = mk('p', 'mut small');
    adv.textContent =
      'It resumes: an interrupted run picks up where it stopped rather than ' +
      'starting over (content-hashed, so a re-run when nothing changed takes ' +
      'seconds). You can close this window while it runs, and stop it at any ' +
      'time.';
    box.appendChild(adv);

    const jobs = mk('div', 'setup-actions');
    const jl = mk('label', 'chk', 'worker processes ');
    const ji = mk('input');
    ji.type = 'number'; ji.id = 'thumb-jobs'; ji.min = '1'; ji.max = '64';
    ji.value = String(plan.jobs || 1);
    ji.style.width = '70px';
    jl.appendChild(ji);
    jobs.appendChild(jl);
    const ll = mk('label', 'chk', 'stop after N images (blank = all) ');
    const li = mk('input');
    li.type = 'number'; li.id = 'thumb-limit'; li.min = '1';
    li.placeholder = 'all'; li.style.width = '90px';
    ll.appendChild(li);
    jobs.appendChild(ll);
    box.appendChild(jobs);
    box.appendChild(mk('p', 'mut small',
      'Fewer workers leaves the machine more responsive; a limit is a quick ' +
      'taste before committing to the whole run.'));

    box.appendChild(mk('h3', null, 'Or don’t'));
    box.appendChild(mk('p', null, plan.declining || ''));

    const actions = mk('div', 'setup-actions');
    const later = mk('button', 'primary secondary', 'Not now');
    later.addEventListener('click', async () => {
      await jpost('/api/thumbs/decision', { choice: 'later' });
      close();
    });
    const never = mk('button', 'primary secondary', 'Not now, and stop asking');
    never.addEventListener('click', async () => {
      await jpost('/api/thumbs/decision', { choice: 'never' });
      close();
    });
    actions.appendChild(later);
    actions.appendChild(never);
    box.appendChild(actions);

    box.appendChild(mk('p', 'mut small',
      'You can start this later from Settings → Health management, or ' +
      'on the command line: ' + (plan.cli || 'py -3 tools/thumbs.py --all')));
    return box;
  }

  function thumbRunning(run) {
    const box = mk('div');
    const p = run.progress || {};
    box.appendChild(mk('h3', null, run.server
      ? `Generating thumbnails — “${run.server}” client`
      : 'Generating thumbnails'));
    const frac = p.total ? p.done / p.total : 0;
    const track = mk('div', 'progress-track');
    const fill = mk('div', 'progress-fill');
    fill.style.width = (frac * 100).toFixed(1) + '%';
    track.appendChild(fill);
    box.appendChild(track);
    box.appendChild(mk('div', 'mut small',
      p.total
        ? `${p.label}: ${p.done.toLocaleString()} / ${p.total.toLocaleString()}` +
          `  ·  ${p.rate} /s  ·  eta ${p.etaSeconds} s  ·  ` +
          `${p.megabytes} MB written  ·  ${p.failed} failed`
        : 'starting…'));
    box.appendChild(mk('p', 'mut small',
      `running for ${run.elapsedSeconds} s. You can close this panel — the ` +
      'job keeps going, and the catalogue picks up thumbnails as they land. ' +
      'Reload the page to see them.'));
    if ((run.tail || []).length) {
      box.appendChild(mk('pre', 'setup-pre', run.tail.join('\n')));
    }
    const actions = mk('div', 'setup-actions');
    const stop = mk('button', 'primary secondary', 'Stop');
    stop.addEventListener('click', async () => {
      await jpost('/api/thumbs/cancel', {});
      refresh();
    });
    const hide = mk('button', 'primary secondary', 'Close (keeps running)');
    hide.addEventListener('click', close);
    actions.appendChild(stop);
    actions.appendChild(hide);
    box.appendChild(actions);
    return box;
  }

  async function start(mode) {
    const jobs = parseInt((q('#thumb-jobs') || {}).value, 10) || 0;
    const limit = parseInt((q('#thumb-limit') || {}).value, 10) || 0;
    await jpost('/api/thumbs/start', { mode, jobs, limit });
    await refresh();
  }

  // ------------------------------------------------------------ asset index
  //
  // The mesh<->texture index (out/indexes/<base>/meshtex/coverage.json). Same
  // shape as the thumbnail card above, deliberately: it is the same kind of
  // thing -- a one-off derived build, offered rather than imposed, run in a
  // separate process, polled, cancellable.
  //
  // The difference worth showing is that this one has a LIVE fallback. The
  // viewer will build the relation in memory when a page asks for it, so the
  // catalogue is never wrong without it -- it is only slower, and only until
  // the build lands. So this card says two things at once: what is happening
  // right now, and how to stop it happening every time.
  function progressBar(p) {
    const frac = p && p.total ? p.done / p.total : 0;
    const track = mk('div', 'progress-track');
    const fill = mk('div', 'progress-fill');
    fill.style.width = (frac * 100).toFixed(1) + '%';
    track.appendChild(fill);
    return track;
  }

  function indexCard(rep) {
    const idx = rep.unifiedIndex || {};
    const run = rep.indexRun || {};
    const live = idx.state === 'building';
    if (!rep.indexOffer && !live && !run.running) return null;

    const box = mk('div');
    box.appendChild(mk('h3', null, 'Asset index'));

    if (live) {
      const p = idx.progress || {};
      box.appendChild(mk('p', null,
        'The mesh↔texture relation for this client is being built right now, ' +
        'in memory, because there is no saved index for it. The viewer stays ' +
        'usable while it runs — asset lists just show one row per file ' +
        'instead of folding a mesh and its skins into one row.'));
      box.appendChild(progressBar(p));
      box.appendChild(mk('div', 'mut small',
        (p.total
          ? `${p.done.toLocaleString()} / ${p.total.toLocaleString()} meshes`
          : 'starting…') +
        `  ·  ${idx.elapsedSeconds} s elapsed`));
    }

    if (run.running) {
      const p = run.progress || {};
      box.appendChild(mk('p', null, 'Building the saved index…'));
      // Only draw a bar when there is something to put in it. A bar stuck at
      // zero for a minute says "hung"; the builder's own last line says what
      // it is doing.
      if (p.total) box.appendChild(progressBar(p));
      box.appendChild(mk('div', 'mut small',
        (p.total
          ? `scanning ${p.done.toLocaleString()} / ${p.total.toLocaleString()}`
          : (run.phase || 'starting…')) +
        `  ·  running for ${run.elapsedSeconds} s. You can close this panel — ` +
        'the job keeps going. Reload the page when it finishes.'));
      if ((run.tail || []).length) {
        box.appendChild(mk('pre', 'setup-pre', run.tail.join('\n')));
      }
      const actions = mk('div', 'setup-actions');
      const stop = mk('button', 'primary secondary', 'Stop');
      stop.addEventListener('click', async () => {
        await jpost('/api/index/cancel', {}); refresh();
      });
      const hide = mk('button', 'primary secondary', 'Close (keeps running)');
      hide.addEventListener('click', close);
      actions.appendChild(stop); actions.appendChild(hide);
      box.appendChild(actions);
      return box;
    }

    if (rep.indexOffer) {
      box.appendChild(mk('p', null,
        `This client (${idx.baseId || rep.baseId || 'this base'}) has no ` +
        'saved mesh↔texture index, so the viewer rebuilds it in memory every ' +
        'time the client is opened. Building it once writes it to disk and ' +
        'the next open is near-instant instead.'));
      box.appendChild(mk('p', 'mut small',
        'One process, a minute or so, a few tens of MB. Nothing starts ' +
        'without you asking, and declining costs only the wait — the ' +
        'catalogue is complete either way. Cancelling part-way leaves the ' +
        'container scan behind, so a second attempt is quicker; unlike ' +
        'thumbnails it is not resumable image by image.'));
      const actions = mk('div', 'setup-actions');
      const go = mk('button', 'primary', 'Build the index');
      go.addEventListener('click', async () => {
        await jpost('/api/index/start', {}); await refresh();
      });
      actions.appendChild(go);
      box.appendChild(actions);
      const cmd = ((rep.derived || {}).artefacts || [])
        .find(a => a.path === 'out/meshtex/coverage.json');
      if (cmd) box.appendChild(mk('p', 'mut small', 'CLI: ' + cmd.command));
    }
    if (run.returncode !== null && run.returncode !== undefined) {
      box.appendChild(mk('p', 'mut small',
        run.cancelled
          ? 'The last index build was stopped.'
          : `Last index build finished with exit code ${run.returncode}. ` +
            'Reload the page to use it.'));
    }
    return box;
  }

  // ------------------------------------------------------------- rendering
  function render(rep) {
    lastReport = rep;
    card.textContent = '';
    card.appendChild(mk('h2', null, 'Health check'));
    card.appendChild(healthRows(rep));

    if (rep.problems && rep.problems.length) {
      card.appendChild(mk('h3', null, 'What to do'));
      for (const prob of rep.problems) {
        const r = mk('div', 'health-row ' +
          (prob.severity === 'error' ? 'bad'
            : prob.severity === 'warning' ? 'warn' : 'ok'));
        r.appendChild(mk('span', 'mark',
          prob.severity === 'error' ? '✗'
            : prob.severity === 'warning' ? '!' : 'i'));
        const g = mk('span', 'what');
        g.appendChild(document.createTextNode(prob.what));
        g.appendChild(mk('div', 'fix', prob.fix || ''));
        r.appendChild(g);
        card.appendChild(r);
      }
    }

    // Before thumbnails: this is the one that is costing time *right now*.
    const ic = indexCard(rep);
    if (ic) card.appendChild(ic);

    const run = rep.thumbnailRun || {};
    const th = rep.thumbnails || {};
    if (run.running) {
      card.appendChild(thumbRunning(run));
    } else {
      if (rep.activeServer || th.status === 'none'
          || th.status === 'meshes-only') {
        card.appendChild(thumbPrompt(rep));
      } else {
        card.appendChild(mk('h3', null, 'Thumbnails'));
        card.appendChild(mk('p', 'mut small',
          `${(th.meshes || 0).toLocaleString()} mesh and ` +
          `${(th.textures || 0).toLocaleString()} texture thumbnails, ` +
          `generated ${th.generated}. Re-run any time: ` +
          `${(th.plan || {}).cli || ''}`));
      }
      if (run.returncode !== null && run.returncode !== undefined) {
        card.appendChild(mk('p', 'mut small',
          run.cancelled ? 'Last run was stopped. Restarting resumes where it '
                        + 'left off.'
                        : `Last run finished with exit code ${run.returncode}.`));
      }
    }

    // ONE place decides whether to keep polling, and it asks about every job
    // on the card. Two independent decisions is how the index poll came to be
    // cancelled by the thumbnail branch it knew nothing about.
    const busy = (rep.thumbnailRun || {}).running ||
                 (rep.indexRun || {}).running ||
                 (rep.unifiedIndex || {}).state === 'building';
    if (busy && !poll) poll = setInterval(refresh, 1500);
    if (!busy && poll) { clearInterval(poll); poll = null; }

    const foot = mk('div', 'setup-actions');
    const done = mk('button', 'primary', 'Close');
    done.addEventListener('click', close);
    foot.appendChild(done);
    card.appendChild(foot);
    card.appendChild(mk('p', 'mut small',
      'This report is also written to out/health.json, and available without ' +
      'a browser: py -3 tools/coviewer.py --health'));
  }

  async function refresh() {
    try {
      render(await jget('/api/health'));
    } catch (e) {
      card.textContent = 'health check failed: ' + e.message;
    }
  }

  // ------------------------------------------------------------------ wire
  // There is no manual opener any more. `#btn-health` was the "Health &
  // thumbnails" button in index.html, and that button is gone -- the report it
  // showed lives on the Settings page, whose Health management section is the
  // same data. Looking up an id nobody defines is not harmless: it reads as a
  // wired control to anyone maintaining this file, and `test_firstrun_optin`
  // exists to refuse exactly that.
  //
  // The AUTO-open below is unaffected and is the path that matters. It sits
  // outside this block (it always did), so removing the button did not remove
  // the behaviour -- a distinction worth stating, because the commit that took
  // the button out claimed in a comment that firstrun "still opens it by
  // itself", and that claim happened to be true only by the brace structure.

  // Auto-open on first run only: something is actually wrong, or there are no
  // thumbnails and the question has not been answered yet. A healthy, already
  // answered checkout never sees this.
  (async () => {
    try {
      const rep = await jget('/api/health');
      const bad = (rep.problems || []).some(p => p.severity === 'error');
      const needsBootstrap = rep.derived && rep.derived.ok === false;
      if (bad || needsBootstrap || rep.firstRunPrompt ||
          (rep.thumbnailRun || {}).running) {
        render(rep);
        open();
      }
    } catch (e) { /* the page still works; Settings has the same report */ }
  })();
})();
