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
 * AND THE BOOTSTRAP IS FOR A CLIENT YOU PICK
 * ------------------------------------------
 * The owner's words: *"Bootstrapping should be done against any client that
 * the user supplies. Preferably with a checklist in the settings menu."*
 *
 * Until 2026-08-23 this page could only say that it did not do that. It
 * printed the configured install and the browsed install side by side and
 * admitted "tools/health.py --bootstrap carries no --root", because that was
 * true and papering over it would have been worse. It carries one now, so the
 * checklist below is the two-axis thing the ask describes: one row per
 * DECLARED CLIENT, one checkbox per ARTEFACT, and a cost.
 *
 * THE COST IS COMPUTED PER CLIENT, AND THE ROWS ARE NOT INDEPENDENT
 * ----------------------------------------------------------------
 * `health.wdf_recover_estimate(root)` measures this box and this install in
 * about 3 s, so the Estimate column is not the table's range. It is opt-in
 * per client because it costs a walk of the install.
 *
 * And the part a per-client table most easily implies away, which is
 * therefore printed at the top of it: **what one client costs depends on
 * which OTHER clients are declared.** wdf_recover pulls every declared DatPkg
 * client's plaintext index into the wordlist of every client, 85-93% of the
 * candidates hashed. MEASURED: Clients/5017 is a QUARTER the size of
 * Clients/5517 and took LONGER -- 2,167 s against 1,675 s -- and with
 * --no-tpi the same two are 302 s and 571 s, in the order size predicts. The
 * --no-tpi checkbox is a real user choice with a real price (~795 names only
 * a DatPkg index resolves) and it is offered as one.
 *
 * Nothing factual here is hardcoded in the browser: the artefact table, the
 * costs, the counts, the megabytes, the client list, the estimate for THIS
 * machine and every caveat come from /api/bootstrap/status,
 * /api/bootstrap/checklist and /api/thumbs/status.
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

  // ---- checklist state, kept OUTSIDE render() -------------------------
  // The panel re-renders every 2 s while a job runs. A selection stored in
  // the DOM would be wiped by the next poll, which on a page whose whole
  // point is "pick a client" means the button silently reverts to a client
  // the user did not pick. That is the same class of bug as the one this
  // section exists to close, so the selection lives here.
  let book = null;          // /api/bootstrap/checklist, cheap version
  let pickedRoot = null;    // which client. null = not chosen yet.
  let pickedArts = null;    // Set of rels, or null = "the missing ones"
  let noTpi = false;
  let estimating = false;

  function mb(n) { return (n || 0).toFixed(0) + ' MB'; }

  function clientRow(root) {
    for (const c of ((book && book.clients) || [])) {
      if (c.root === root) return c;
    }
    return null;
  }

  function chosenArts(row) {
    if (pickedArts) return pickedArts;
    // Default: exactly what is missing FOR THIS CLIENT. `row.missing` comes
    // from check_derived(root), so it is that client's gaps and not the
    // configured install's.
    return new Set((row && row.missing) || []);
  }

  // ------------------------------------------------- the client checklist
  // One row per DECLARED client, which is the owner's ask made literal:
  // "Bootstrapping should be done against any client that the user supplies."
  function checklistBlock() {
    const wrap = mk('div');
    wrap.id = 'boot-checklist';
    wrap.appendChild(mk('h4', null, 'Which client'));
    if (!book) {
      wrap.appendChild(mk('div', 'set-help', 'loading the client list…'));
      return wrap;
    }
    // THE COST DRIVER, AND IT IS NOT INSTALL SIZE. Printed above the rows,
    // not under them, because a per-client table implies per-client
    // independence and these rows are not independent.
    const tpi = book.tpiContext || {};
    if (tpi.why) {
      const w = mk('div', 'set-cost');
      w.appendChild(mk('h4', null, 'These rows are not independent'));
      const p = mk('p', null, tpi.why);
      p.id = 'boot-tpi-why';
      w.appendChild(p);
      wrap.appendChild(w);
    }

    const tbl = mk('table');
    tbl.style.width = '100%';
    tbl.style.borderCollapse = 'collapse';
    const head = mk('tr');
    ['', 'Client', 'Base', 'Archives', 'Built', 'wdf_recover here']
      .forEach(h => {
        const th = mk('th', null, h);
        th.style.textAlign = 'left';
        th.style.paddingRight = '.8rem';
        head.appendChild(th);
      });
    tbl.appendChild(head);
    let i = 0;
    for (const c of (book.clients || [])) {
      const tr = mk('tr');
      const cell = mk('td');
      const rb = mk('input');
      rb.type = 'radio';
      rb.name = 'boot-client';
      rb.id = 'boot-client-' + (i++);
      rb.className = 'boot-client';
      rb.setAttribute('data-root', c.root);
      rb.checked = (c.root === pickedRoot);
      rb.disabled = !c.exists;
      rb.addEventListener('change', () => {
        pickedRoot = c.root;
        // A new client means new gaps. Keeping the previous client's ticks
        // would tick artefacts this one already has and untick ones it needs.
        pickedArts = null;
        render();
      });
      cell.appendChild(rb);
      tr.appendChild(cell);
      const built = (c.artefacts || []).filter(a => a.exists).length;
      const est = c.estimate;
      // `countBasis` rides with the number because the two wordlist modes do
      // not deserve the same confidence and the number alone cannot say so:
      // the candidate count is exact to 0.1% with a DatPkg wordlist and
      // MEASURED at 0.88x-1.79x of the truth without one.
      const estText = !est ? 'not measured — press Estimate'
        : (est.ok ? (est.text + (est.countBasis ? '  — ' + est.countBasis : ''))
                  : ('cannot estimate: ' + (est.why || '?')));
      [c.name + (c.exists ? '' : ' (missing)'),
       c.baseId || '?',
       (c.archives || []).join(', ') || 'none',
       built + '/' + (c.artefacts || []).length,
       estText].forEach((v, k) => {
        const td = mk('td', null, v);
        td.style.paddingRight = '.8rem';
        td.style.verticalAlign = 'top';
        if (k === 0) td.title = c.root;
        if (k === 4) td.style.opacity = '.9';
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    }
    wrap.appendChild(tbl);

    const btnRow = mk('div', 'set-field');
    const estBtn = mk('button', 'ghost',
      estimating ? 'Measuring…' : 'Estimate every client (measures this box)');
    estBtn.id = 'boot-estimate';
    estBtn.disabled = estimating;
    estBtn.addEventListener('click', async () => {
      estimating = true;
      say('measuring — a walk of each install plus a hash benchmark…');
      render();
      try {
        book = await jget('/api/bootstrap/checklist?estimate=1&noTpi=' +
                          (noTpi ? '1' : '0'));
        say('');
      } catch (e) { say(String(e.message || e), true); }
      estimating = false;
      render();
    });
    btnRow.appendChild(estBtn);
    btnRow.appendChild(mk('span', 'set-help', book.estimateCostNote || ''));
    wrap.appendChild(btnRow);

    // -- the wordlist tradeoff, as a choice rather than a footnote --------
    const tpiRow = mk('div', 'set-field');
    const tpiBox = mk('input');
    tpiBox.type = 'checkbox';
    tpiBox.id = 'boot-notpi';
    tpiBox.checked = noTpi;
    tpiBox.addEventListener('change', () => {
      noTpi = tpiBox.checked;
      // The estimates on screen were measured in the OTHER mode and are now
      // 7-14x wrong. Dropped rather than left sitting under a changed
      // checkbox, which is the shape of stale number this page exists to
      // stop.
      for (const c of ((book && book.clients) || [])) c.estimate = null;
      render();
    });
    const tpiLab = mk('label', null,
      'Build the name table WITHOUT the DatPkg wordlist (--no-tpi)');
    tpiLab.htmlFor = 'boot-notpi';
    tpiRow.appendChild(tpiBox);
    tpiRow.appendChild(tpiLab);
    wrap.appendChild(tpiRow);
    wrap.appendChild(mk('div', 'set-effect',
      'Off (the default): every declared DatPkg client’s plaintext index ' +
      'joins the wordlist of the client you are building, which is 85-93% of ' +
      'the candidates hashed. On: only this install’s own paths. ' +
      'MEASURED on Clients/5017 — 2,167 s against 302 s, and on ' +
      'Clients/5517 1,675 s against 571 s. The price of turning it on is ' +
      'about 795 filenames that only a DatPkg index can resolve; they stay ' +
      'unnamed hashes. It changes nothing but out/wdf/.'));
    return wrap;
  }

  // ------------------------------------------------------------- bootstrap
  function bootstrapBlock(doc) {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Bootstrap the derived data'));
    if (pickedRoot === null) {
      pickedRoot = doc.browsing || doc.buildsFor || '';
    }
    box.appendChild(mk('div', 'set-help',
      'Six generated artefacts the viewer reads. Built once, in dependency ' +
      'order, for the client you pick below — tools/health.py --bootstrap ' +
      'takes a --root and this page passes it. Browsing ' +
      (doc.browsing || '?') + '; configured install ' +
      (doc.buildsFor || '(none)') + '.'));

    box.appendChild(checklistBlock());

    const row = clientRow(pickedRoot);
    const arts = row ? row.artefacts : ((doc.derived || {}).artefacts || []);
    const want = chosenArts(row);

    // -- THE COST, ABOVE THE BUTTON ---------------------------------------
    const cost = mk('div', 'set-cost');
    cost.appendChild(mk('h4', null,
      'What this will cost, before you start it' +
      (pickedRoot ? ' — for ' + pickedRoot : '')));
    const tbl = mk('table');
    tbl.style.width = '100%';
    tbl.style.borderCollapse = 'collapse';
    const thead = mk('tr');
    ['', 'Artefact', 'State', 'Estimate', 'Scope', 'What it is for']
      .forEach(h => {
        const th = mk('th', null, h);
        th.style.textAlign = 'left';
        th.style.paddingRight = '.8rem';
        thead.appendChild(th);
      });
    tbl.appendChild(thead);
    let todo = 0;
    let n = 0;
    for (const a of arts) {
      const tr = mk('tr');
      const state = !a.applicable ? 'n/a here'
        : a.suppliedFrom ? 'supplied'
        : a.exists ? (a.inherited ? 'inherited' : 'built')
        : a.buildable ? 'MISSING' : 'not buildable here';
      if (state === 'MISSING') todo += 1;
      // THE CHECKLIST'S OTHER AXIS: which artefacts, not only which client.
      const cell = mk('td');
      const cb = mk('input');
      cb.type = 'checkbox';
      cb.id = 'boot-art-' + (n++);
      cb.className = 'boot-art';
      cb.setAttribute('data-rel', a.path);
      cb.checked = want.has(a.path);
      cb.disabled = !a.buildable;
      cb.addEventListener('change', () => {
        const s = new Set(chosenArts(row));
        if (cb.checked) s.add(a.path); else s.delete(a.path);
        pickedArts = s;
        render();
      });
      cell.appendChild(cb);
      tr.appendChild(cell);
      // "shared" rows are labelled as shared rather than duplicated per
      // client: out/opcodes.json is built from refs/ and opens no install at
      // all, so a per-client copy of it would be the same bytes twice.
      const scope = a.perClient ? 'per client'
        : (a.rootAware ? 'shared, built from one install' : 'shared, no --root');
      [a.path, state, a.cost || '?', scope, a.why || ''].forEach((v, i) => {
        const td = mk('td', null, v);
        td.style.paddingRight = '.8rem';
        td.style.verticalAlign = 'top';
        if (i === 0) td.style.fontFamily = 'var(--mono, monospace)';
        if (i === 4) td.style.opacity = '.85';
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    }
    cost.appendChild(tbl);
    cost.appendChild(mk('p', null,
      want.size + ' artefact' + (want.size === 1 ? '' : 's') + ' ticked; ' +
      todo + ' missing for this client.'));
    if (book && book.sharedNote) {
      cost.appendChild(mk('p', 'set-measured', book.sharedNote));
    }
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
    const label = pickedRoot
      ? 'Run bootstrap for ' + (row ? row.name : pickedRoot)
      : 'Run bootstrap';
    const go = mk('button', 'primary',
                  run.running ? 'Bootstrap running…' : label);
    go.id = 'boot-start';
    go.disabled = !!run.running || want.size === 0;
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
        // `root` is the whole point: the run is for the client ticked above,
        // not for whichever install happens to be configured.
        const res = await jpost('/api/bootstrap/start', {
          all: allBox.checked,
          uses: uses,
          root: pickedRoot || '',
          noTpi: noTpi,
          only: Array.from(want),
        });
        say(res.started ? 'Running: ' + (res.steps || []).join('  then  ')
                        : (res.reason || 'not started'));
        startPoll();
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    btnRow.appendChild(go);
    if (want.size === 0) {
      btnRow.appendChild(mk('span', 'set-help',
        'Nothing is ticked, so there is nothing to run.'));
    }
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
        // TWO VERDICTS, NOT ONE. `health.py --bootstrap` runs the health
        // report after the builders and exits 1 whenever the TREE is
        // unhealthy — an artefact missing for some OTHER client, a
        // foreign-provenance one — which on a machine with nine declared
        // clients is the normal state. Reading that as "your build failed"
        // told users a bootstrap had broken when every builder had
        // succeeded and the artefact was on disk. `run.built` is the
        // builders' own verdict.
        const ok = run.built === true || run.returncode === 0;
        box.appendChild(mk('div', ok ? 'set-help' : 'set-bad',
          run.cancelled ? 'Stopped.'
            : run.built === true && run.returncode !== 0
              ? 'Built in ' + run.elapsedSeconds + ' s' +
                (run.root ? ' for ' + run.root : '') + '. The health report ' +
                'that ran afterwards exited ' + run.returncode + ' — that is ' +
                'the state of the whole tree, not of this build; the ' +
                'transcript above says which client is still short of what.'
            : run.returncode === 0
              ? 'Finished in ' + run.elapsedSeconds + ' s' +
                (run.root ? ' for ' + run.root : '') + '.'
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
      // The cheap version, once. The estimate is opt-in and never rides the
      // 2 s poll: it walks each install and benchmarks the CPU, MEASURED at
      // 1-14 s per client.
      if (!book) book = await jget('/api/bootstrap/checklist');
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
