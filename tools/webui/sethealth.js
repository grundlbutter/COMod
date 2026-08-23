/* sethealth.js -- Health management, on the settings page.
 *
 * THE ONE RULE THIS FILE EXISTS TO ENFORCE
 * ----------------------------------------
 * **The cost is stated before the button, not after it.** Every long job on
 * this page prints what it will cost -- counts, megabytes, the tool's own
 * time estimate -- in a block that sits ABOVE the control that starts it. A
 * warning underneath a button is a warning read after the click.
 *
 * AND THE ESTIMATES ARE LABELLED AS PER-INSTALL, BECAUSE THEY ARE
 * ---------------------------------------------------------------
 * The numbers in `health.DERIVED` and `health.THUMB_FACTS` were measured on
 * ONE install and are repeated by the tool for every install. Two measured
 * counter-examples ship with this page and are printed beside the estimates
 * rather than filed in a docstring:
 *
 *   * `wdf_recover` is documented at "5-9 min" and took **2,164 s -- 36
 *     minutes** on Clients/5517. Four times the top of the range.
 *   * The thumbnail plan's census is 4,950 meshes / 66,834 textures; Classic
 *     Conquer 2.0 measured 47,973 textures + 1,352 meshes, 313 MB of PNG,
 *     ~637 MB on disk, with the tool's own estimate at 15-30 minutes -- and
 *     that is PER CLIENT, once for each one declared.
 *
 * A user who clicks a button expecting 9 minutes and waits 36 is the exact
 * complaint that started this work.
 *
 * Nothing factual here is hardcoded in the browser: the artefact table, the
 * costs, the counts, the megabytes, the estimate for THIS machine and both
 * caveats all come from /api/bootstrap/status and /api/thumbs/status.
 */

'use strict';

(function () {
  const host = document.querySelector('#health-body');
  if (!host) return;

  const mk = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  };

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

  const note = mk('div', 'set-note');
  function say(msg, bad) {
    note.textContent = msg || '';
    note.className = bad ? 'set-note set-bad' : 'set-note';
  }

  let poll = null;
  const useFields = {};   // rel -> the <input> holding a --use path, kept
                          // across re-renders so a poll does not wipe typing

  function mb(n) { return (n || 0).toFixed(0) + ' MB'; }

  // ------------------------------------------------------------- bootstrap
  function bootstrapBlock(doc) {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Bootstrap the derived data'));
    box.appendChild(mk('div', 'set-help',
      'Six generated artefacts the viewer reads. Built once, in dependency ' +
      'order. Builds for ' + (doc.buildsFor || '(no configured root)') +
      ' — the configured install root, which is not necessarily the client ' +
      'you are browsing (' + (doc.browsing || '?') + '): ' +
      'tools/health.py --bootstrap carries no --root.'));

    const arts = (doc.derived || {}).artefacts || [];

    // -- THE COST, ABOVE THE BUTTON ---------------------------------------
    const cost = mk('div', 'set-cost');
    cost.appendChild(mk('h4', null, 'What this will cost, before you start it'));
    const tbl = mk('table');
    tbl.style.width = '100%';
    tbl.style.borderCollapse = 'collapse';
    const thead = mk('tr');
    ['Artefact', 'State', 'Estimate', 'What it is for'].forEach(h => {
      const th = mk('th', null, h);
      th.style.textAlign = 'left';
      th.style.paddingRight = '.8rem';
      thead.appendChild(th);
    });
    tbl.appendChild(thead);
    let todo = 0;
    for (const a of arts) {
      const tr = mk('tr');
      const state = !a.applicable ? 'n/a here'
        : a.suppliedFrom ? 'supplied'
        : a.exists ? (a.inherited ? 'inherited' : 'built')
        : a.buildable ? 'MISSING' : 'not buildable here';
      if (state === 'MISSING') todo += 1;
      [a.path, state, a.cost || '?', a.why || ''].forEach((v, i) => {
        const td = mk('td', null, v);
        td.style.paddingRight = '.8rem';
        td.style.verticalAlign = 'top';
        if (i === 0) td.style.fontFamily = 'var(--mono, monospace)';
        if (i === 3) td.style.opacity = '.85';
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    }
    cost.appendChild(tbl);
    cost.appendChild(mk('p', null,
      todo + ' artefact' + (todo === 1 ? '' : 's') + ' would be built by ' +
      '“Bootstrap”.'));
    // The caveat is the server's string, so the page cannot say something
    // milder than the tool believes.
    cost.appendChild(mk('p', 'set-measured', doc.costCaveat || ''));
    box.appendChild(cost);

    // -- --use: supply what this tree cannot build ------------------------
    const cannot = arts.filter(a => !a.buildable && a.applicable);
    const sup = mk('details');
    sup.open = cannot.length > 0;
    sup.appendChild(mk('summary', null,
      'Supply an artefact this tree cannot build (' + cannot.length +
      ' candidate' + (cannot.length === 1 ? '' : 's') + ')'));
    sup.appendChild(mk('div', 'set-help',
      'Some builders are not shipped in every checkout — COMod ships the ' +
      'asset subset. Point at a copy built elsewhere and it is recorded in ' +
      'the config, exactly as ' +
      'py -3 tools/health.py --use "REL=PATH" would. A path that does not ' +
      'exist is refused rather than stored, and the bootstrap that follows ' +
      'is not run.'));
    for (const a of arts) {
      const row = mk('div', 'set-field');
      const lab = mk('label', null, a.path);
      lab.style.fontFamily = 'var(--mono, monospace)';
      lab.style.flex = '0 0 16rem';
      row.appendChild(lab);
      let inp = useFields[a.path];
      if (!inp) {
        inp = mk('input');
        inp.type = 'text';
        inp.placeholder = a.buildable
          ? 'buildable here — leave blank'
          : 'path to a copy built elsewhere';
        inp.value = a.suppliedFrom || '';
        inp.setAttribute('data-use-rel', a.path);
        useFields[a.path] = inp;
      }
      row.appendChild(inp);
      if (a.suppliedFrom) {
        row.appendChild(mk('span', 'set-help', 'currently supplied'));
      } else if (!a.buildable) {
        row.appendChild(mk('span', 'set-help', 'not buildable here'));
      }
      sup.appendChild(row);
    }
    box.appendChild(sup);

    // -- bootstrap-all ----------------------------------------------------
    const allRow = mk('div', 'set-field');
    const allBox = mk('input');
    allBox.type = 'checkbox';
    allBox.id = 'boot-all';
    const allLab = mk('label', null, 'Rebuild everything (--bootstrap-all)');
    allLab.htmlFor = 'boot-all';
    allRow.appendChild(allBox);
    allRow.appendChild(allLab);
    box.appendChild(allRow);
    box.appendChild(mk('div', 'set-effect', doc.allExplains || ''));

    // -- the buttons ------------------------------------------------------
    const run = (doc.run || {});
    const btnRow = mk('div', 'set-field');
    const go = mk('button', 'primary',
                  run.running ? 'Bootstrap running…' : 'Run bootstrap');
    go.id = 'boot-start';
    go.disabled = !!run.running;
    go.addEventListener('click', async () => {
      const uses = [];
      for (const rel of Object.keys(useFields)) {
        const v = (useFields[rel].value || '').trim();
        const was = (arts.find(a => a.path === rel) || {}).suppliedFrom || '';
        if (v !== was) uses.push({ rel: rel, path: v });
      }
      go.disabled = true;
      say('starting…');
      try {
        const res = await jpost('/api/bootstrap/start',
                                { all: allBox.checked, uses: uses });
        say(res.started ? 'Running: ' + (res.steps || []).join('  then  ')
                        : (res.reason || 'not started'));
        startPoll();
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    btnRow.appendChild(go);
    const stop = mk('button', 'ghost', 'Stop');
    stop.id = 'boot-cancel';
    stop.disabled = !run.running;
    stop.addEventListener('click', async () => {
      try { await jpost('/api/bootstrap/cancel', {}); say('stopping…'); }
      catch (e) { say(String(e.message || e), true); }
    });
    btnRow.appendChild(stop);
    box.appendChild(btnRow);

    if (run.startedAt || run.running || run.returncode !== null) {
      const p = run.progress || {};
      const head = p.total
        ? 'building ' + p.done + '/' + p.total + '  ' + (p.label || '') +
          (p.cost ? '  (~' + p.cost + ')' : '')
        : (run.running ? 'starting…' : '');
      const pre = mk('div', 'set-progress',
        [head].concat(run.tail || []).filter(Boolean).join('\n') ||
        '(no output yet)');
      pre.id = 'boot-progress';
      box.appendChild(pre);
      if (!run.running && run.returncode !== null) {
        box.appendChild(mk('div', run.returncode === 0 ? 'set-help' : 'set-bad',
          run.cancelled ? 'Stopped.'
            : run.returncode === 0
              ? 'Finished in ' + run.elapsedSeconds + ' s.'
              : 'Exited ' + run.returncode + ' after ' + run.elapsedSeconds +
                ' s — the output above says why.'));
      }
    }
    return box;
  }

  // ------------------------------------------------------------ thumbnails
  function thumbBlock(doc) {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Generate thumbnails'));
    const st = doc.state || {};
    const plan = st.plan || {};
    const run = doc.run || {};
    const target = doc.activeServer
      ? 'the library client “' + doc.activeServer + '”'
      : (doc.forClient || 'the client being browsed');
    box.appendChild(mk('div', 'set-help',
      'For ' + target + '. Cached in ' + (st.dir || '?') + '. Resumable: ' +
      'tools/thumbs.py --resume skips anything whose content hash is ' +
      'unchanged, which is what makes a re-run cheap and “yes” a low-stakes ' +
      'answer.'));

    // -- THE COST, ABOVE THE BUTTON ---------------------------------------
    const cost = mk('div', 'set-cost');
    cost.appendChild(mk('h4', null, 'What this will cost, before you start it'));
    cost.appendChild(mk('p', null,
      'On disk now: ' + (st.meshes || 0).toLocaleString() + ' mesh + ' +
      (st.textures || 0).toLocaleString() + ' texture thumbnails, ' +
      mb((st.bytes || 0) / 1e6) + ' — status “' + (st.status || '?') + '”.'));
    for (const o of (plan.options || [])) {
      cost.appendChild(mk('p', null,
        o.label + ': ' + (o.count || 0).toLocaleString() + ' images, ' +
        mb(o.megabytes) + ', estimated ' + o.estimate +
        ' at ' + plan.jobs + ' workers on ' + plan.cpus + ' cores.'));
    }
    if (plan.reference) {
      cost.appendChild(mk('p', 'set-measured',
        'The estimate is extrapolated from one measured run: ' +
        plan.reference + '.'));
    }
    // The per-client caveat, from the server.
    cost.appendChild(mk('p', 'set-measured', doc.costCaveat || ''));
    if (plan.declining) {
      cost.appendChild(mk('p', 'set-measured',
        'Declining is fine: ' + plan.declining));
    }
    box.appendChild(cost);

    // -- the optional component -------------------------------------------
    const optRow = mk('div', 'set-field');
    const optBox = mk('input');
    optBox.type = 'checkbox';
    optBox.id = 'thumb-textures';
    const optLab = mk('label', null,
      'Include texture thumbnails (the optional component)');
    optLab.htmlFor = 'thumb-textures';
    optRow.appendChild(optBox);
    optRow.appendChild(optLab);
    box.appendChild(optRow);
    box.appendChild(mk('div', 'set-effect',
      'Off: meshes only — the model thumbnails the character builder and ' +
      'model mode actually use, about a third of the cost. On: adds every ' +
      'texture, which is most of the time and nearly all of the disk. ' +
      'Neither is required; assets with no thumbnail show a placeholder tile ' +
      'and everything else is unaffected.'));

    // -- the buttons ------------------------------------------------------
    // "Generate" and "Re-run" are the same job -- `--resume` means a second
    // run tops up rather than starting over. The label follows the cache so
    // the button never says "Generate" over an already-full cache.
    const btnRow = mk('div', 'set-field');
    const label = doc.rerun ? 'Re-run generate thumbnails'
                            : 'Generate thumbnails';
    const go = mk('button', 'primary', run.running ? 'Rendering…' : label);
    go.id = 'thumb-start';
    go.disabled = !!run.running || doc.enabled === false;
    go.addEventListener('click', async () => {
      go.disabled = true;
      say('starting…');
      try {
        const res = await jpost('/api/thumbs/start',
                                { mode: optBox.checked ? 'all' : 'meshes' });
        say(res.started ? 'Rendering — this is the long one.'
                        : (res.reason || 'not started'));
        startPoll();
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    btnRow.appendChild(go);
    if (doc.rerun) {
      btnRow.appendChild(mk('span', 'set-help',
        'A re-run is a top-up, not a restart: unchanged assets are skipped.'));
    }
    const stop = mk('button', 'ghost', 'Stop');
    stop.id = 'thumb-cancel';
    stop.disabled = !run.running;
    stop.addEventListener('click', async () => {
      try { await jpost('/api/thumbs/cancel', {}); say('stopping…'); }
      catch (e) { say(String(e.message || e), true); }
    });
    btnRow.appendChild(stop);
    box.appendChild(btnRow);
    if (doc.enabled === false) {
      box.appendChild(mk('div', 'set-help',
        'The `thumbnails` setting is off, so generation is refused. Turn it ' +
        'on under Preferences below.'));
    }

    // `ThumbRunner.status()` serves no start timestamp, so the condition is
    // the two fields it does serve. Asking for `run.started` here would be a
    // test on `undefined` that reads like a test on a time.
    if (run.running || run.returncode !== null) {
      const p = run.progress || {};
      const head = p.total
        ? (p.label || '') + ' ' + p.done + '/' + p.total +
          '  ok ' + p.ok + '  cached ' + p.cached + '  failed ' + p.failed +
          '  ' + (p.rate || 0).toFixed(1) + '/s  eta ' +
          (p.etaSeconds || 0) + ' s  ' + (p.megabytes || 0).toFixed(0) + ' MB'
        : (run.running ? 'starting…' : '');
      const pre = mk('div', 'set-progress',
        [head].concat(run.tail || []).filter(Boolean).join('\n') ||
        '(no output yet)');
      pre.id = 'thumb-progress';
      box.appendChild(pre);
    }
    return box;
  }

  // ----------------------------------------------------------- asset index
  function indexBlock(rep) {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Build the asset index'));
    const idx = rep.index || {};
    const run = rep.run || {};
    box.appendChild(mk('div', 'set-help',
      'The mesh↔texture relation for ' + (rep.root || '?') + '. ' +
      (idx.prebuilt
        ? 'Already built for this client — nothing to do.'
        : 'Not built for this client.')));
    if (!idx.prebuilt) {
      const cost = mk('div', 'set-cost');
      cost.appendChild(mk('h4', null, 'What this will cost, before you start it'));
      cost.appendChild(mk('p', null,
        'One pass over the .c3 containers. Measured: 6090, which has a saved ' +
        'index, opens in 1.6 s; 7878, which did not, built the same relation ' +
        'live in 32.3 s — and paid that 32.3 s EVERY time the client was ' +
        'opened. Building it once writes the artefact and the next open is ' +
        'the 1.6 s figure.'));
      cost.appendChild(mk('p', 'set-measured',
        'Per client, like everything else on this page: each declared client ' +
        'has its own index namespace and its own build. It is NOT ' +
        'incremental the way thumbnails are — cancelling halfway leaves the ' +
        'container scan cached and nothing else.'));
      box.appendChild(cost);
    }
    const btnRow = mk('div', 'set-field');
    const go = mk('button', idx.prebuilt ? 'ghost' : 'primary',
                  run.running ? 'Indexing…' : 'Build the asset index');
    go.id = 'index-start';
    go.disabled = !!run.running;
    go.addEventListener('click', async () => {
      go.disabled = true;
      say('starting…');
      try {
        await jpost('/api/index/start', {});
        say('Indexing.');
        startPoll();
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    btnRow.appendChild(go);
    const stop = mk('button', 'ghost', 'Stop');
    stop.id = 'index-cancel';
    stop.disabled = !run.running;
    stop.addEventListener('click', async () => {
      try { await jpost('/api/index/cancel', {}); say('stopping…'); }
      catch (e) { say(String(e.message || e), true); }
    });
    btnRow.appendChild(stop);
    box.appendChild(btnRow);
    if (run.running || run.returncode !== null) {
      const p = run.progress || {};
      box.appendChild(mk('div', 'set-progress',
        (p.total ? 'scan ' + p.done + '/' + p.total + '\n' : '') +
        (run.tail || []).join('\n') || run.phase || '(no output yet)'));
    }
    return box;
  }

  // ----------------------------------------------------------------- render
  let rendering = false;
  async function render() {
    if (rendering) return;
    rendering = true;
    let boot, thumb, index;
    try {
      boot = await jget('/api/bootstrap/status');
      thumb = await jget('/api/thumbs/status');
      index = await jget('/api/index/status');
    } catch (e) {
      host.textContent = '';
      host.appendChild(mk('div', 'set-bad', String(e.message || e)));
      rendering = false;
      return;
    }
    host.textContent = '';
    host.appendChild(bootstrapBlock(boot));
    host.appendChild(thumbBlock(thumb));
    host.appendChild(indexBlock(index));
    host.appendChild(note);

    const busy = (boot.run || {}).running || (thumb.run || {}).running ||
                 (index.run || {}).running;
    if (busy && !poll) poll = setInterval(render, 2000);
    if (!busy && poll) { clearInterval(poll); poll = null; }
    rendering = false;
  }

  function startPoll() {
    if (!poll) poll = setInterval(render, 2000);
    render();
  }

  window.coSetHealth = { render: render };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
