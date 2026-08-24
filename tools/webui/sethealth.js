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
  // ONE SELECTION, SHARED BY BOOTSTRAP AND THUMBNAILS. It used to be a single
  // `pickedRoot` behind a radio button, and thumbnails had a separate "all
  // clients" list -- so bootstrapping 5517 and then generating thumbnails for
  // 6609 was two clicks apart and looked identical. The owner's reason for
  // merging them is that both track "what you are working on", so there is
  // one set and both readers take it from here.
  //
  // It is persisted through `cosettings.selected_installs` -- the config
  // `coroot` already owns -- and NOT in localStorage, because `tools/thumbs.py`
  // and `tools/health.py` run from a shell have to see the same answer. A
  // browser-only selection would be invisible to them.
  let picked = null;        // Set of roots. null = not loaded from the server yet.
  let selection = null;     // /api/selection: stored, roots, dropped, fallback
  let advanced = false;     // cosettings.show_advanced_options
  let pickedArts = null;    // Set of rels, or null = "the missing ones"
  let noTpi = false;
  let estimating = false;
  let saving = false;

  /** The installs to act on: what is ticked, or the browsed one standing in.
   *
   *  The fallback is the server's rule (`selected_installs`' declared effect)
   *  reproduced for the label only -- `/api/selection` computes the real one,
   *  and every POST re-resolves it server-side. The page never gets to decide
   *  which installs a builder runs against.
   */
  function pickedRoots() {
    if (picked && picked.size) return Array.from(picked);
    return (selection && selection.roots) || [];
  }

  async function saveSelection() {
    saving = true;
    try {
      await jpost('/api/settings',
                  { name: 'selected_installs', value: Array.from(picked) });
      // Re-read rather than trusting what we sent: the server prunes to
      // declared installs, so the set that came back is the set that will
      // actually be built and is what the rows must show.
      selection = await jget('/api/selection');
    } catch (e) {
      say(String(e.message || e), true);
    }
    saving = false;
  }

  function mb(n) { return (n || 0).toFixed(0) + ' MB'; }

  function clientRow(root) {
    for (const c of ((book && book.clients) || [])) {
      if (c.root === root) return c;
    }
    return null;
  }

  /** Every checklist row for the currently selected installs. */
  function pickedRows() {
    const out = [];
    for (const r of pickedRoots()) {
      const row = clientRow(r);
      if (row) out.push(row);
    }
    return out;
  }

  /** ONE ARTEFACT, ACROSS EVERY SELECTED INSTALL.
   *
   *  The owner's ask is that ticking an artefact builds it "of both" when an
   *  offline client and a private server are selected, which means a single
   *  checkbox now stands for N cells rather than one. The aggregation rules
   *  below are the whole design, and each is chosen so the SAFE reading wins:
   *
   *    * `refused` if ANY selected install refused a supplied copy. Not "most"
   *      and not "the first" -- a refusal is a broken state on that install
   *      and hiding it behind three healthy ones is how it stays broken.
   *    * `resolved` only if EVERY selected install has it built, inherited or
   *      supplied-and-accepted. One install short is work to do, so the box
   *      stays live.
   *    * `blocked` if the artefact cannot be built here at all.
   *
   *  `missing` is a count rather than a flag because the header says "built
   *  for 2 of 3 selected", and a flag cannot.
   */
  function aggregate(rel, rows) {
    const out = { rel: rel, total: rows.length, built: 0, missing: 0,
                  supplied: 0, refused: [], notApplicable: [], blocked: false,
                  unverified: 0, inherited: 0, sample: null,
                  defaultChecked: false, cost: '', why: '', scope: '' };
    for (const row of rows) {
      const a = (row.artefacts || []).find(x => x.path === rel);
      if (!a) continue;
      if (!out.sample) {
        out.sample = a;
        out.defaultChecked = !!a.defaultChecked;
        out.cost = a.cost || '?';
        out.why = a.why || '';
        out.scope = a.perClient ? 'per client'
          : (a.rootAware ? 'shared, built from one install'
                         : 'shared, no --root');
      }
      if (!a.buildable) out.blocked = true;
      if (!a.applicable) { out.notApplicable.push(row.name); continue; }
      // ORDER MATTERS AND MATCHES THE ROW LABEL BELOW: a refusal outranks
      // "supplied", because the override gate can decline a supplied copy and
      // the artefact is then NOT in force. Counting it as supplied would gray
      // the box on the strength of a file the tool has rejected.
      if (a.refusedWhy) { out.refused.push({ name: row.name, why: a.refusedWhy }); continue; }
      if (a.suppliedFrom) {
        out.supplied += 1;
        if (!a.verified) out.unverified += 1;
        if (a.exists) { out.built += 1; continue; }
        // Supplied, not refused, and still not readable. Rare, and it is
        // WORK rather than a settled state, so it counts as missing.
        out.missing += 1;
        continue;
      }
      if (a.exists) { out.built += 1; if (a.inherited) out.inherited += 1; }
      else out.missing += 1;
    }
    out.resolved = out.total > 0 && out.missing === 0 &&
                   out.refused.length === 0 &&
                   out.built + out.notApplicable.length >= out.total;
    return out;
  }

  /** WHY a box is grayed, in the page's own words -- or '' when it is live.
   *
   *  Every disabled control on this page returns a sentence from here. A
   *  disabled checkbox with no reason beside it is indistinguishable from a
   *  broken one, and this page has six of them in a column.
   */
  function grayReason(ag) {
    if (ag.notApplicable.length === ag.total && ag.total > 0) {
      const a = ag.sample || {};
      return a.notApplicableWhy ||
             'not applicable in this checkout';
    }
    // A REFUSAL NEVER GRAYS THE BOX. This is the case the brief singles out:
    // a supplied copy that `coroot.override_verdict` declined is not
    // "supplied", and disabling the row because a setting exists would hide a
    // broken state behind a dead control -- the user would see the artefact
    // listed as handled and have no way to act on it.
    if (ag.refused.length) return '';
    if (ag.blocked && !ag.resolved) {
      return 'no builder for this artefact in this checkout — supply a copy ' +
             'built elsewhere below';
    }
    if (ag.resolved) {
      if (ag.supplied) {
        return ag.unverified
          ? 'already supplied from a copy built elsewhere, and accepted — ' +
            'though NOT verified: no check exists for this artefact'
          : 'already supplied from a copy built elsewhere, and verified ' +
            'against your own archives';
      }
      if (ag.inherited === ag.total) {
        return 'already built — inherited from the primary checkout';
      }
      return ag.total === 1
        ? 'already built for the selected install'
        : 'already built for all ' + ag.total + ' selected installs';
    }
    return '';
  }

  function chosenArts(rows) {
    if (pickedArts) return pickedArts;
    // THE DEFAULT TICKS, and they are a property of the artefact rather than
    // of this page: `health.DEFAULT_BOOTSTRAP` names the five, and
    // `out/opcodes.json` is deliberately not among them. A rel is ticked when
    // it is one of those five AND there is still work to do for at least one
    // selected install -- ticking something already built everywhere would
    // ask for a rebuild nobody requested.
    const s = new Set();
    for (const rel of ((book && book.artefacts) || [])) {
      const ag = aggregate(rel, rows);
      if (ag.defaultChecked && !ag.resolved && !grayReason(ag)) s.add(rel);
    }
    return s;
  }

  // ------------------------------------------------- the client checklist
  // One row per DECLARED client, which is the owner's ask made literal:
  // "Bootstrapping should be done against any client that the user supplies."
  function checklistBlock() {
    const wrap = mk('div');
    wrap.id = 'boot-checklist';
    wrap.appendChild(mk('h4', null, 'Which installs'));
    if (!book) {
      wrap.appendChild(mk('div', 'set-help', 'loading the client list…'));
      return wrap;
    }
    wrap.appendChild(mk('div', 'set-help',
      'One selection, used by BOTH the bootstrap below and thumbnail ' +
      'generation — they track the same thing, which is whichever clients ' +
      'you are working on. It is stored in the config beside every other ' +
      'setting, so tools/health.py and tools/thumbs.py run from a shell see ' +
      'the same answer.'));
    // WHAT AN EMPTY SELECTION MEANS, SAID ON THE PAGE rather than left for
    // the user to discover by pressing a button. Nothing ticked falls back to
    // the install being browsed -- not all of them, because one click would
    // otherwise start a nine-client bootstrap.
    if (selection && selection.fallback) {
      wrap.appendChild(mk('div', 'set-effect',
        'Nothing is ticked, so both buttons act on the install you are ' +
        'browsing — ' + (selection.browsing || '(none)') + '. Ticking rows ' +
        'below replaces that. An empty selection never means "all of them": ' +
        'wdf_recover alone is MEASURED at 302-2,167 s per client.'));
    }
    // A REMEMBERED INSTALL THAT IS NO LONGER DECLARED. Reported rather than
    // dropped in silence, because "I ticked four and three ran" is otherwise
    // invisible. The stored entry is deliberately left alone server-side, so
    // re-declaring the client brings the tick back.
    if (selection && (selection.dropped || []).length) {
      const d = mk('div', 'set-bad');
      d.id = 'boot-dropped';
      d.textContent =
        (selection.dropped.length === 1
          ? 'One remembered install is no longer declared and will NOT be ' +
            'built: '
          : selection.dropped.length + ' remembered installs are no longer ' +
            'declared and will NOT be built: ') +
        selection.dropped.join(', ') +
        '. They are still remembered — re-declare one in Directory ' +
        'management and it returns to the selection.';
      wrap.appendChild(d);
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
      // A CHECKBOX, NOT A RADIO. The owner asked for a multi-select shared by
      // bootstrapping and thumbnail generation; a radio can express neither
      // half of that. `name` is dropped with the type -- a group name on
      // checkboxes does nothing but suggest they are still exclusive.
      const rb = mk('input');
      rb.type = 'checkbox';
      rb.id = 'boot-client-' + (i++);
      rb.className = 'boot-client';
      rb.setAttribute('data-root', c.root);
      rb.checked = !!(picked && picked.has(c.root));
      // An install whose folder is gone cannot be built for. It is shown --
      // removing the row would make a selection silently shrink with nothing
      // on screen saying why -- but it cannot be ticked.
      rb.disabled = !c.exists || saving;
      if (!c.exists) {
        rb.title = 'this folder is not there any more, so nothing can be ' +
                   'built from it';
      }
      rb.addEventListener('change', async () => {
        if (!picked) picked = new Set();
        if (rb.checked) picked.add(c.root); else picked.delete(c.root);
        // The ticked artefacts were defaulted from the PREVIOUS set of
        // installs' gaps. A changed set means different gaps, so they are
        // dropped back to the default rather than carried over: keeping them
        // would tick artefacts the newly-added client already has and leave
        // unticked ones it is missing.
        pickedArts = null;
        await saveSelection();
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
    box.appendChild(mk('div', 'set-help',
      'Six generated artefacts the viewer reads. Built once, in dependency ' +
      'order, for EVERY install ticked below — tools/health.py --bootstrap ' +
      'takes one --root, so this page runs it once per selected install and ' +
      'reports them in order. Browsing ' +
      (doc.browsing || '?') + '; configured install ' +
      (doc.buildsFor || '(none)') + '.'));

    box.appendChild(checklistBlock());

    const rows = pickedRows();
    const rels = (book && book.artefacts) ||
                 ((doc.derived || {}).artefacts || []).map(a => a.path);
    const want = chosenArts(rows);

    // -- THE COST, ABOVE THE BUTTON ---------------------------------------
    const cost = mk('div', 'set-cost');
    cost.appendChild(mk('h4', null,
      'What this will cost, before you start it' +
      (rows.length === 1 ? ' — for ' + rows[0].name
        : rows.length ? ' — for ' + rows.length + ' selected installs'
        : '')));
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
    for (const rel of rels) {
      const ag = aggregate(rel, rows);
      const gray = grayReason(ag);
      const tr = mk('tr');
      // THE STATE COLUMN, AGGREGATED. With one install selected it reads
      // exactly as it did before. With several it has to say how many, or a
      // row built for three clients and missing on the fourth reads as
      // "built" and the fourth never gets one.
      let state;
      if (ag.refused.length) {
        state = 'REFUSED on ' + ag.refused.map(r => r.name).join(', ');
      } else if (ag.notApplicable.length === ag.total && ag.total) {
        state = 'n/a here';
      } else if (ag.total === 0) {
        state = 'no install selected';
      } else if (ag.missing === 0) {
        state = ag.supplied ? 'supplied' :
                (ag.inherited === ag.total ? 'inherited' : 'built');
      } else if (ag.blocked) {
        state = 'not buildable here';
      } else {
        state = 'MISSING for ' + ag.missing + ' of ' + ag.total;
        todo += ag.missing;
      }
      const cell = mk('td');
      const cb = mk('input');
      cb.type = 'checkbox';
      cb.id = 'boot-art-' + (n++);
      // `artefact-check` is the class the anti-duplicate-work gate greps for,
      // named here so the gate and the implementation cannot drift apart.
      // `boot-art` is kept beside it because the existing browser tests
      // select on it.
      cb.className = 'boot-art artefact-check';
      cb.setAttribute('data-rel', rel);
      cb.checked = want.has(rel) && !gray;
      cb.disabled = !!gray;
      // A DISABLED BOX ALWAYS CARRIES ITS REASON, in the tooltip and in the
      // cell beside it. A grayed control with nothing saying why is
      // indistinguishable from one that is simply broken, and this column has
      // six of them.
      if (gray) cb.title = gray;
      cb.addEventListener('change', () => {
        const s = new Set(want);
        if (cb.checked) s.add(rel); else s.delete(rel);
        pickedArts = s;
        render();
      });
      cell.appendChild(cb);
      tr.appendChild(cell);
      const nameCell = mk('td');
      const nameText = mk('div', null, rel);
      nameText.style.fontFamily = 'var(--mono, monospace)';
      nameCell.appendChild(nameText);
      if (gray) {
        const g = mk('div', 'set-help', gray);
        g.className = 'set-help artefact-why';
        g.setAttribute('data-rel', rel);
        nameCell.appendChild(g);
      }
      // The refusal's own sentences, whole, on the row itself rather than
      // only in the supply section below -- this is the row the user is
      // looking at when they wonder why a supplied artefact is not in force.
      for (const r of ag.refused) {
        const w = mk('div', 'set-bad', r.name + ': ' + r.why);
        w.style.whiteSpace = 'pre-wrap';
        nameCell.appendChild(w);
      }
      nameCell.style.paddingRight = '.8rem';
      nameCell.style.verticalAlign = 'top';
      tr.appendChild(nameCell);
      [state, ag.cost || '?', ag.scope, ag.why].forEach((v, i) => {
        const td = mk('td', null, v);
        td.style.paddingRight = '.8rem';
        td.style.verticalAlign = 'top';
        if (i === 3) td.style.opacity = '.85';
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    }
    cost.appendChild(tbl);
    cost.appendChild(mk('p', null,
      want.size + ' artefact' + (want.size === 1 ? '' : 's') + ' ticked; ' +
      todo + ' artefact-install pair' + (todo === 1 ? '' : 's') +
      ' still to build across ' + rows.length + ' selected install' +
      (rows.length === 1 ? '' : 's') + '.'));
    if (book && book.sharedNote) {
      cost.appendChild(mk('p', 'set-measured', book.sharedNote));
    }
    // The caveat is the server's string, so the page cannot say something
    // milder than the tool believes.
    cost.appendChild(mk('p', 'set-measured', doc.costCaveat || ''));
    box.appendChild(cost);

    // -- --use: supply what this tree cannot build ------------------------
    //
    // WHICH ARTEFACTS MAY BE SUPPLIED, AND WHY THE LIST IS NORMALLY ONE.
    //
    // With `show_advanced_options` off, the only field offered is
    // `out/opcodes.json`. That is not an arbitrary safety rail, it follows
    // from where the artefact comes from: opcodes is built from `refs/` and
    // opens no install at all, so one machine's copy is legitimately
    // byte-identical to another's and handing one over carries no claim about
    // anybody's client.
    //
    // Every other artefact in DERIVED is derived FROM an install. A supplied
    // `out/wdf/c3_names.json` is a claim about archives the supplier had and
    // the recipient does not, which is precisely the case
    // `coroot.override_verdict` exists to judge -- and the case that produces
    // a REFUSED row further up when the claim does not hold. Offering those
    // fields to everyone invites a user to paste a stranger's name table and
    // then wonder why the page says REFUSED. Advanced users who know what
    // they are supplying still get all six.
    const SUPPLY_ALWAYS = 'out/opcodes.json';
    const supplyRels = advanced ? rels : rels.filter(r => r === SUPPLY_ALWAYS);
    const withheld = rels.length - supplyRels.length;
    const cannot = rels.filter(rel => {
      const ag = aggregate(rel, rows);
      return ag.blocked && ag.notApplicable.length < ag.total;
    });
    const sup = mk('details');
    sup.id = 'boot-supply';
    sup.open = cannot.length > 0;
    sup.appendChild(mk('summary', null,
      'Supply an artefact this tree cannot build (' + supplyRels.length +
      ' offered' + (withheld ? ', ' + withheld + ' hidden' : '') + ')'));
    sup.appendChild(mk('div', 'set-help',
      'Some builders are not shipped in every checkout — COMod ships the ' +
      'asset subset. Point at a copy built elsewhere and it is recorded in ' +
      'the config, exactly as ' +
      'py -3 tools/health.py --use "REL=PATH" would. A path that does not ' +
      'exist is refused rather than stored, and the bootstrap that follows ' +
      'is not run.'));
    if (withheld) {
      // THE WITHHELD ROWS SAY THEY ARE WITHHELD. A section that silently
      // shows one field where six exist reads as a tool that only supports
      // one, and the user has no way to find the switch.
      const w = mk('div', 'set-effect');
      w.id = 'boot-supply-withheld';
      w.textContent =
        withheld + ' more can be supplied, and are hidden because they are ' +
        'derived from an install rather than from refs/: supplying one is a ' +
        'claim about archives you have and this machine does not, which is ' +
        'what the verification gate then judges. Turn on ' +
        '"show_advanced_options" under Developer options to offer them.';
      sup.appendChild(w);
    }
    for (const rel of supplyRels) {
      const ag = aggregate(rel, rows);
      const a = ag.sample || { path: rel };
      const row = mk('div', 'set-field');
      const lab = mk('label', null, rel);
      lab.style.fontFamily = 'var(--mono, monospace)';
      lab.style.flex = '0 0 16rem';
      row.appendChild(lab);
      let inp = useFields[rel];
      if (!inp) {
        inp = mk('input');
        inp.type = 'text';
        inp.placeholder = ag.blocked
          ? 'path to a copy built elsewhere'
          : 'buildable here — leave blank';
        inp.value = a.suppliedFrom || '';
        inp.setAttribute('data-use-rel', rel);
        useFields[rel] = inp;
      }
      row.appendChild(inp);
      if (ag.refused.length) {
        // The server's own sentences, whole. A shortened refusal is a refusal
        // the user cannot act on, and the reason it was refused is the only
        // thing that tells them whether to fix the file or clear the setting.
        for (const r of ag.refused) {
          const w = mk('div', 'set-help', r.name + ': ' + r.why);
          w.style.whiteSpace = 'pre-wrap';
          row.appendChild(w);
        }
      } else if (a.suppliedFrom) {
        row.appendChild(mk('span', 'set-help',
          a.verified ? 'currently supplied — verified'
                     : 'currently supplied — NOT verified, no check exists ' +
                       'for this artefact'));
      } else if (ag.blocked) {
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
    // The button NAMES what it will act on, and names all of it. "Run
    // bootstrap" beside a four-install selection is the button that gets
    // pressed by someone expecting one client's worth of work.
    const label = rows.length === 1
      ? 'Run bootstrap for ' + rows[0].name
      : rows.length > 1
        ? 'Run bootstrap for ' + rows.length + ' installs (' +
          rows.map(r => r.name).join(', ') + ')'
        : 'Run bootstrap';
    const go = mk('button', 'primary',
                  run.running ? 'Bootstrap running…' : label);
    go.id = 'boot-start';
    go.disabled = !!run.running || want.size === 0;
    go.addEventListener('click', async () => {
      const uses = [];
      for (const rel of Object.keys(useFields)) {
        const v = (useFields[rel].value || '').trim();
        // Only fields the user actually CHANGED are posted. `suppliedFrom`
        // comes off the aggregate's sample row rather than a per-client
        // artefact list, because an override is recorded once in the config
        // and is not per install.
        const was = ((aggregate(rel, rows).sample) || {}).suppliedFrom || '';
        if (v !== was) uses.push({ rel: rel, path: v });
      }
      go.disabled = true;
      say('starting…');
      try {
        // `roots` is the whole point: the run is for the clients ticked
        // above, not for whichever install happens to be configured.
        const res = await jpost('/api/bootstrap/start', {
          all: allBox.checked,
          uses: uses,
          // THE SET, NOT A ROOT. The server re-resolves it against the
          // declared installs and refuses if none survive, so what is posted
          // here is a request and never the last word on what gets built.
          roots: pickedRoots(),
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

  // -------------------------------------------------- which folders (paths)
  /** The per-group checkbox list, priced from the live estimator.
   *
   *  Every number here is served: `plan.groups` is built by
   *  `health._group_rows` from `thumbnail_corpus`, one census of THIS
   *  client. Nothing in this function knows a count, a size or a folder
   *  name, which is the property that stops it from becoming the table the
   *  whole rewrite removed.
   */
  function pathBlock(doc) {
    const box = mk('div', 'set-paths');
    const plan = (doc.state || {}).plan || {};
    const rows = plan.groups || [];
    box.appendChild(mk('h4', 'set-sub', 'Which folders'));
    if (!rows.length) {
      box.appendChild(mk('div', 'set-help',
        'No folders could be listed for this client — the census above ' +
        'says why. Nothing to choose between until it can be counted.'));
      return box;
    }
    box.appendChild(mk('div', 'set-help',
      'Thumbnails are rendered per folder, and the folders are very ' +
      'uneven — untick the big ones you do not browse. These are the ' +
      'client’s own folders and its own counts, not a fixed list.'));

    // The unmeasured mesh half, said once and at the top of the list rather
    // than repeated per row: when this client has no mesh work list every
    // Meshes column below is 0 MEANING NOT COUNTED, and a reader who takes
    // it as "this folder has no models" would untick exactly the folders
    // that will turn out to be the expensive ones.
    if (rows.length && rows[0].meshesCounted === false) {
      box.appendChild(mk('div', 'set-measured',
        'The Meshes column reads “not counted” for every folder: this ' +
        'client has no mesh work list yet, so the model half of each row ' +
        'is unknown rather than zero. The Textures column is measured and ' +
        'is this client’s.'));
    }

    const sel = new Set(plan.selectedGroups || []);
    const all = plan.selectedAll !== false;
    const boxes = [];
    const tot = mk('div', 'set-measured');
    const refresh = () => {
      let m = 0, t = 0, mb = 0, n = 0;
      for (const b of boxes) {
        if (!b.el.checked) continue;
        n++; m += b.row.meshes; t += b.row.textures; mb += b.row.megabytes;
      }
      const counted = rows[0].meshesCounted !== false;
      tot.textContent = n === 0
        ? 'Nothing ticked — a run would render nothing. Tick at least one ' +
          'folder, or leave them all ticked for the whole client.'
        : 'Ticked: ' + n + ' of ' + rows.length + ' folders — ' +
          (counted ? m.toLocaleString() + ' meshes' : 'meshes not counted') +
          ' + ' + t.toLocaleString() + ' textures, about ' + mb + ' MB. ' +
          'The saved figure comes back from the server when you save.';
    };

    const list = mk('div', 'set-grouplist');
    for (const row of rows) {
      const line = mk('label', 'set-grouprow');
      const cb = mk('input');
      cb.type = 'checkbox';
      cb.checked = all || sel.has(row.group);
      cb.dataset.group = row.group;
      cb.className = 'thumb-group';
      cb.addEventListener('change', refresh);
      boxes.push({ el: cb, row: row });
      line.appendChild(cb);
      line.appendChild(mk('span', 'set-groupname', row.group));
      line.appendChild(mk('span', 'set-groupcount',
        (row.meshesCounted === false
          ? 'meshes not counted'
          : row.meshes.toLocaleString() + ' meshes') +
        ' · ' + row.textures.toLocaleString() + ' textures · ' +
        row.megabytes + ' MB'));
      list.appendChild(line);
    }
    box.appendChild(list);

    const btns = mk('div', 'set-field');
    const tick = (v) => () => {
      for (const b of boxes) b.el.checked = v;
      refresh();
    };
    const allBtn = mk('button', 'ghost', 'Tick all');
    allBtn.id = 'thumb-paths-all';
    allBtn.addEventListener('click', tick(true));
    const noneBtn = mk('button', 'ghost', 'Untick all');
    noneBtn.id = 'thumb-paths-none';
    noneBtn.addEventListener('click', tick(false));
    const save = mk('button', 'primary', 'Save folder selection');
    save.id = 'thumb-paths-save';
    save.addEventListener('click', async () => {
      save.disabled = true;
      const picked = boxes.filter(b => b.el.checked).map(b => b.row.group);
      try {
        // `all: true` when every folder is ticked, rather than the list of
        // today's folders: a client that grows a folder in a later patch
        // must keep rendering it, and a saved list would silently exclude
        // it with nothing on screen to say so.
        const body = picked.length === rows.length
          ? { all: true } : { groups: picked };
        const res = await jpost('/api/thumbs/paths', body);
        say('folder selection saved — ' +
            (res.selection === 'all' ? 'every folder'
                                     : (res.selection || []).length +
                                       ' folders'));
        render();
      } catch (e) { say(String(e.message || e), true); save.disabled = false; }
    });
    btns.appendChild(save);
    btns.appendChild(allBtn);
    btns.appendChild(noneBtn);
    refresh();
    box.appendChild(tot);
    box.appendChild(btns);
    if ((plan.unknownGroups || []).length) {
      box.appendChild(mk('div', 'set-measured',
        'Saved for this client but not present in it: ' +
        plan.unknownGroups.join(', ') + '. Those contribute nothing to the ' +
        'counts above — they are named rather than dropped so a selection ' +
        'carried over from another client is visible instead of silently ' +
        'shrinking the job.'));
    }
    return box;
  }

  // ------------------------------------------------- every declared client
  /** The owner's "all": offered, totalled BEFORE the button, and argued
   *  against beside it at the same weight.
   *
   *  The recommendation is `all.advice`, served by `health` next to the
   *  number it qualifies. It is rendered as a block with the same visual
   *  weight as the cost block above the button and NOT as a tooltip,
   *  because the brief was explicit and because a total this large reads as
   *  a target unless something argues with it in the same glance.
   */
  function allClientsBlock(doc) {
    const all = doc.allClients;
    const box = mk('div', 'set-allclients');
    if (!all || all.error) return box;
    box.appendChild(mk('h4', 'set-sub', 'Thumbnails for the selected installs'));

    const cost = mk('div', 'set-cost');
    cost.appendChild(mk('h4', null,
      'What all ' + all.clients.length + ' clients would cost'));
    // THE BUTTON RUNS THE SELECTION; THE TABLE BELOW COSTS EVERY DECLARED
    // CLIENT. Those are different sets, and saying so is not a footnote: a
    // total for nine clients sitting directly above a button that renders two
    // is a number the user will read as the price of pressing it. The
    // subtotal is computed from the same per-client rows, so the two cannot
    // disagree, and it ABSTAINS rather than printing a wrong number when any
    // selected client's corpus could not be counted -- `thumbnail_corpus`
    // declines to guess for exactly the same reason.
    (function () {
      const sel = new Set(pickedRoots());
      const mine = all.clients.filter(c => sel.has(c.root));
      if (!mine.length) return;
      const measured = mine.every(c => c.corpusMeasured);
      const imgs = mine.reduce(
        (n, c) => n + (c.meshes || 0) + (c.textures || 0), 0);
      const mbs = mine.reduce((n, c) => n + (c.megabytes || 0), 0);
      const p = mk('p', 'set-effect');
      p.id = 'thumb-selected-subtotal';
      p.textContent = measured
        ? 'Ticked right now: ' + mine.length + ' of ' + all.clients.length +
          ' — ' + imgs.toLocaleString() + ' images, ' + mbs.toFixed(0) +
          ' MB. That is what the button below will render.'
        : 'Ticked right now: ' + mine.length + ' of ' + all.clients.length +
          '. At least one has no mesh work list yet, so their cost is NOT ' +
          'totalled here rather than guessed at — build ' +
          'out/meshtex/coverage.json for it first.';
      cost.appendChild(p);
    })();
    // The total FIRST, and labelled for what it is. `totalMeasured` false
    // means at least one client's mesh work list could not be counted, and
    // then this is a lower bound with no duration attached -- the server
    // sends no `estimate` key at all in that case, and this must not invent
    // one or print a zero in its place.
    cost.appendChild(mk('p', null,
      (all.totalMeasured ? 'Total: ' : 'At least: ') +
      all.images.toLocaleString() + ' images, ' + all.megabytes + ' MB' +
      (all.estimate ? ', estimated ' + all.estimate : '') + '.'));
    if (!all.totalMeasured) {
      cost.appendChild(mk('p', 'set-measured',
        'That is a LOWER BOUND, not the total: ' + all.unmeasured.length +
        ' of ' + all.clients.length + ' clients have no mesh work list yet, ' +
        'so their model half is not counted and contributes nothing to the ' +
        'figures above. No time estimate is shown for the same reason — an ' +
        'estimate over an uncounted job would be a guess wearing a number.'));
    }
    box.appendChild(cost);

    // The recommendation, at the same weight as the cost block, above the
    // button. The owner's answer, verbatim from the server.
    const warn = mk('div', 'set-cost');
    warn.appendChild(mk('h4', null, 'Recommended: don’t do this'));
    warn.appendChild(mk('p', null, all.advice));
    box.appendChild(warn);

    const tbl = mk('div', 'set-clientlist');
    for (const c of all.clients) {
      const line = mk('div', 'set-clientrow');
      line.appendChild(mk('span', 'set-groupname', c.root));
      line.appendChild(mk('span', 'set-groupcount',
        c.corpusMeasured
          ? (c.meshes.toLocaleString() + ' meshes + ' +
             c.textures.toLocaleString() + ' textures · ' + c.megabytes + ' MB')
          // The refusal, per client and in its own words. No megabytes,
          // because the server sent none -- an absent key, not a zero.
          : (c.texturesCounted
              ? c.textures.toLocaleString() + ' textures counted; ' +
                'meshes not counted, so no size for this client'
              : 'nothing counted for this client')));
      if (!c.corpusMeasured && c.why) {
        line.appendChild(mk('div', 'set-measured', c.why));
      }
      tbl.appendChild(line);
    }
    box.appendChild(tbl);

    const row = mk('div', 'set-field');
    // The button NAMES the set it acts on, and that set is the selection --
    // not "all N clients", which is what it used to say and used to do.
    const picks = pickedRoots();
    const go = mk('button', 'ghost',
      picks.length === 1
        ? 'Generate thumbnails for the selected install'
        : 'Generate thumbnails for the ' + picks.length + ' selected installs');
    go.id = 'thumb-all-clients';
    go.disabled = !!(doc.run || {}).running || doc.enabled === false;
    go.addEventListener('click', async () => {
      go.disabled = true;
      try {
        // THE SAME SELECTION THE BOOTSTRAP USES -- `useSelection`, not
        // `allClients`. This is the half of the owner's ask that makes the
        // setting unified: before this, "all clients" meant every declared
        // install while the bootstrap meant one picked client, so the two
        // controls on this page acted on different sets by design.
        const res = await jpost('/api/thumbs/start',
                                { mode: 'meshes', useSelection: true,
                                  roots: pickedRoots() });
        say(res.started
          ? 'Queued — clients run one after another, not at once.'
          : (res.reason || 'not started'));
        startPoll();
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    row.appendChild(go);
    row.appendChild(mk('span', 'set-help',
      'Runs for the installs ticked under "Which installs" above — the same ' +
      'selection the bootstrap uses. Each client keeps its OWN folder ' +
      'selection and its own cache, and each is resumable on its own, so ' +
      'stopping part-way loses nothing. The two selections compose: this one ' +
      'says which clients, and each client’s own folder list says which ' +
      'groups within it.'));
    box.appendChild(row);
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

    // -- WHICH FOLDERS ----------------------------------------------------
    // The substantive half of the owner's Q3: "allow the user to select
    // which paths get thumbnails generated in the settings menu."
    //
    // A "path" here is the group `thumbs.logical_group` reads off a logical
    // name -- its second path segment, which is the partition the archives
    // already use and `thumbs.py --include` already takes. Not invented for
    // this list: the same string names the folder, prices the row, and goes
    // on the command line, so the number beside the checkbox and the number
    // the run does cannot drift.
    box.appendChild(pathBlock(doc));

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
      // THE SELECTION AND THE ADVANCED FLAG, from the server, once. Both are
      // stored settings, so a page opened in a second window shows the same
      // ticks -- which a `localStorage` selection could not.
      if (!selection) selection = await jget('/api/selection');
      if (picked === null) picked = new Set(selection.stored || []);
      try {
        const st = await jget('/api/settings');
        const adv = (st.settings || []).find(
          s => s.name === 'show_advanced_options');
        advanced = !!(adv && adv.value);
      } catch (e) {
        // The supply section falls back to its RESTRICTED form, never its
        // open one: failing closed is the only safe direction for a control
        // whose whole purpose is to withhold fields by default.
        advanced = false;
      }
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
    host.appendChild(allClientsBlock(thumb));
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
