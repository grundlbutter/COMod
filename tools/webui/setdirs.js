/* setdirs.js -- Directory management, on the settings page.
 *
 * THE ONE RULE THIS FILE EXISTS TO ENFORCE
 * ----------------------------------------
 * **Nothing is ever declared silently.** The scan is a read; it produces an
 * OFFER. A folder becomes a declared client only when someone clicks the
 * button beside it, and the kind that gets recorded is the one shown in the
 * control at the time. There is no code path here that POSTs
 * /api/installs/declare without a user gesture, and that is the point: a tool
 * that adopts directories by itself is one bad guess away from indexing the
 * wrong tree, and the wrong kind is then baked into an index directory name,
 * a parse profile and a thumbnail cache.
 *
 * SECOND RULE: A GUESS IS NEVER PRESENTED AS AN IDENTIFICATION
 * -----------------------------------------------------------
 * `plugins.rank` hands back every plugin's confidence, and the server turns
 * that into one of four verdicts (`confident` / `weak` / `tie` / `none`).
 * Only `confident` -- top score at or above 0.9 with the runner-up more than
 * 0.05 below -- pre-selects an answer. Everything else renders as a QUESTION
 * with the whole ranking visible, because a 0.95 from a version stamp and a
 * 0.5 from "this looks vaguely like my family" are the same answer through
 * `detect` and are not the same claim.
 *
 * Measured on this machine, which is why the distinction is not theoretical:
 * the seven patch clients and Zephyr all score 0.95 (confident), while
 * Classic Conquer 2.0 scores 0.85 -- below the bar, so it is asked about.
 *
 * Nothing factual here is hardcoded. The clients folder, the thresholds, the
 * plugin list and every confidence come from the server.
 */

'use strict';

(function () {
  const host = document.querySelector('#dirs-body');
  if (!host) return;

  const mk = (tag, cls, txt) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined && txt !== null) e.textContent = txt;
    return e;
  };

  // Plain `fetch`: csrf.js wraps it and attaches this run's token to every
  // same-origin POST. A hand-rolled poster would be a second path to keep in
  // sync, and the one that gets forgotten when the token changes.
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

  let plugins = [];      // every parser plugin, for the manual picker
  let dirs = null;       // last /api/installs/dirs response
  // The build offer that follows a declaration. Held across the re-render
  // rather than appended to a node `render()` is about to clear.
  let pendingOffer = null;
  const note = mk('div', 'set-note');

  function say(msg, bad) {
    note.textContent = msg || '';
    note.className = bad ? 'set-note set-bad' : 'set-note';
  }

  const CATEGORY_WORD = {
    'offline-client': 'Offline Client',
    'private-server': 'Private server',
    'unknown': 'Unclassified',
  };

  /* Decimal GB, matching `coviewer._gb` and matching the unit
   * `health.WDF_RECOVER_MEASUREMENTS` counts in (15.34 GB for 7878 is
   * 15.34e9 bytes). Two units in one feature is how a user comes to believe
   * a copy shrank on the way across. */
  function gb(n) {
    n = Number(n || 0);
    if (n < 1e6) return (n / 1e3).toFixed(0) + ' kB';
    if (n < 1e9) return (n / 1e6).toFixed(1) + ' MB';
    return (n / 1e9).toFixed(2) + ' GB';
  }
  const num = n => Number(n || 0).toLocaleString();

  /* The four verdicts in the words a person needs, not the tokens.
   * `confident` is the only one that answers; the rest ASK. */
  function verdictLine(d) {
    const pc = n => (n * 100).toFixed(0) + '%';
    if (d.error) return ['none', d.error];
    if (d.verdict === 'confident') {
      return ['confident',
              'Identified as ' + d.suggested + ' — confidence ' +
              pc(d.confidence) +
              (d.gap === null ? ', nothing else claimed it'
                              : ', next best ' + pc(d.confidence - d.gap))];
    }
    if (d.verdict === 'tie') {
      return ['tie',
              'TIE, not an answer: ' + pc(d.confidence) + ' and ' +
              pc(d.confidence - d.gap) + ' are within ' +
              pc(dirs ? dirs.tieWindow : 0.05) +
              ' of each other. Which one wins is the order of a sort. Pick.'];
    }
    if (d.verdict === 'weak') {
      return ['weak',
              'A GUESS, not an identification: the best score is ' +
              pc(d.confidence) + ', below the ' +
              pc(dirs ? dirs.confidentAt : 0.9) +
              ' this codebase treats as an answer. Confirm or pick.'];
    }
    return ['none', 'No parser plugin claimed this folder. Pick one, or ' +
                    'leave it undeclared.'];
  }

  function rankingBlock(d) {
    if (!d.ranking || !d.ranking.length) return null;
    const box = mk('div', 'set-rank');
    // The whole ranking, always -- this is the part `detect` throws away and
    // the part a person needs in order to accept or overrule the top row.
    for (const r of d.ranking) {
      box.appendChild(mk('div', null,
        '  ' + (r.confidence * 100).toFixed(0).padStart(3) + '%  ' +
        r.name + '  (' + r.origin + ')  ' + r.label));
    }
    return box;
  }

  /* A <select> of every plugin, pre-selected ONLY on a confident read.
   * On a weak or tied read it opens on "— choose —", so the form cannot be
   * submitted without an actual decision. */
  function kindPicker(d) {
    const sel = mk('select');
    const blank = mk('option', null, '— choose —');
    blank.value = '';
    sel.appendChild(blank);
    for (const p of plugins) {
      const o = mk('option', null,
        p.name + '  —  ' + p.label +
        (p.origin === 'server' ? '  (private server)' : ''));
      o.value = p.name;
      sel.appendChild(o);
    }
    if (d && d.verdict === 'confident' && d.suggested) sel.value = d.suggested;
    return sel;
  }

  /* One offered folder. Returns the element; declaring happens on click and
   * nowhere else. */
  function candidateCard(d, opts) {
    opts = opts || {};
    const li = mk('li', 'set-item');
    const head = mk('div', 'set-item-head');
    head.appendChild(mk('strong', null, d.name || d.root));
    if (d.ranking && d.ranking.length) {
      const cat = d.ranking[0].origin === 'server'
        ? 'private-server' : 'offline-client';
      head.appendChild(mk('span', 'set-tag ' + cat,
                          CATEGORY_WORD[cat] + '?'));
    }
    li.appendChild(head);
    li.appendChild(mk('div', 'set-path', d.root));

    const [kind, text] = verdictLine(d);
    li.appendChild(mk('div', 'set-verdict ' + kind, text));
    const rk = rankingBlock(d);
    if (rk) li.appendChild(rk);

    // BEFORE the `missing` branch, deliberately. An interrupted copy can
    // already satisfy `missing_parts` — that check is content, and a
    // three-percent copy that got as far as ini/ and one archive passes it.
    // So the marker is asked about first, and it is the answer.
    if (d.incompleteCopy) {
      li.appendChild(mk('div', 'set-bad',
        'NOT A CLIENT — this is a copy that never finished. It still ' +
        'carries its marker file' +
        (d.incompleteCopy.copyOf ? ', and says it was a copy of ' +
                                   d.incompleteCopy.copyOf : '') + '.'));
      li.appendChild(mk('div', 'set-help',
        'It is refused by the marker rather than by its contents on ' +
        'purpose: a torn tree whose ini/ finished hashes identical to its ' +
        'source, so it would share the source’s index namespace and look ' +
        'entirely healthy. The declare endpoint refuses it too.'));
      const row = mk('div', 'set-field');
      const del = mk('button', 'ghost', 'Discard this unfinished copy');
      del.addEventListener('click', async () => {
        del.disabled = true;
        try {
          await jpost('/api/installs/copydiscard', { path: d.root });
          say('Deleted ' + d.root + '.');
          li.textContent = '';
          li.appendChild(mk('div', 'set-help', 'Deleted ' + d.root + '.'));
        } catch (e) { say(String(e.message || e), true); del.disabled = false; }
      });
      row.appendChild(del);
      li.appendChild(row);
      return li;
    }
    if (d.missing && d.missing.length) {
      // Shown rather than filtered out: "why is 5065_old not offered" is a
      // question this page should be able to answer.
      li.appendChild(mk('div', 'set-help',
        'Not offered — this is not a client folder (missing ' +
        d.missing.join(', ') + ').'));
      return li;
    }

    const ask = mk('div', kind === 'confident' ? '' : 'set-ask');
    if (kind !== 'confident') {
      ask.appendChild(mk('div', 'set-help',
        'Answer this rather than letting the tool guess. The declaration ' +
        'picks the parse profile and names the index directory, so a wrong ' +
        'one is expensive to notice.'));
    }
    const row = mk('div', 'set-field');
    row.appendChild(mk('label', null, 'Declare as'));
    const sel = kindPicker(d);
    row.appendChild(sel);

    // Private-server name. Asked for whenever the picked plugin declares
    // itself a private server's client -- no plugin can know what the server
    // is CALLED, and "Zephyr Conquer" is the label of a plugin, not of the
    // shard somebody is running.
    const nameWrap = mk('span', 'set-field');
    nameWrap.style.display = 'none';
    nameWrap.appendChild(mk('label', null, 'Which private server?'));
    const nameIn = mk('input');
    nameIn.type = 'text';
    nameIn.placeholder = 'e.g. Zephyr, Conquer Reborn…';
    nameIn.style.flex = '0 1 16rem';
    nameWrap.appendChild(nameIn);
    row.appendChild(nameWrap);

    function syncName() {
      const p = plugins.find(x => x.name === sel.value);
      nameWrap.style.display = (p && p.origin === 'server') ? '' : 'none';
    }
    sel.addEventListener('change', syncName);
    syncName();

    const btn = mk('button', 'primary', opts.label || 'Declare this client');
    btn.addEventListener('click', async () => {
      if (!sel.value) { say('Pick what this folder is first. Nothing is ' +
                            'declared without an answer.', true); return; }
      btn.disabled = true;
      say('declaring…');
      try {
        const res = await jpost('/api/installs/declare', {
          path: d.root, kind: sel.value,
          serverName: nameWrap.style.display === 'none'
            ? '' : nameIn.value.trim(),
        });
        say('Declared ' + res.path + ' as ' + res.kind + ' (' + res.plugin +
            '). Restart the viewer to browse it.');
        pendingOffer = res;
        await render();
      } catch (e) {
        say(String(e.message || e), true);
        btn.disabled = false;
      }
    });
    row.appendChild(btn);
    ask.appendChild(row);
    li.appendChild(ask);

    if (kind === 'confident') {
      li.appendChild(mk('div', 'set-help',
        'Confident, and still not automatic: click to declare it.'));
    }
    return li;
  }

  // ------------------------------------------- copy a private server, frozen
  /* THE PRICE IS PRINTED BEFORE THE BUTTON EXISTS.
   *
   * Not "above" it in the stylesheet — the start control is not appended to
   * the DOM until a cost read has come back, and it carries that read's byte
   * count as `confirmBytes`. The server requires that field and re-measures
   * against it, so a page that skipped the cost read cannot start a copy and
   * a tree that moved between the read and the click cannot be copied
   * against a stale price. The rule is enforced on both sides rather than
   * being a promise this file makes.
   *
   * Every figure shown is walked or read at the moment of the call: bytes,
   * files and directories from the source tree, free space from the
   * destination volume, and a process probe for a live server. None of them
   * is a table lookup, and the one estimate on the page — the census range —
   * is labelled as somebody else's install.
   */
  function copyPanel(r, host) {
    host.textContent = '';
    const box = mk('div', 'set-cost');
    host.appendChild(box);
    box.appendChild(mk('h4', null, 'Copy ' + (r.serverName || r.label) +
                                   ' into a frozen Offline Client'));
    box.appendChild(mk('p', null,
      'A private server changes under you — it patches, and it rewrites its ' +
      'own ini/ every time it shuts down. A copy is a fixed point: mods and ' +
      'measurements get a subject that does not move. The copy is a NEW ' +
      'install and is declared separately; nothing is declared by this ' +
      'button.'));

    const nameRow = mk('div', 'set-field');
    nameRow.appendChild(mk('label', null, 'Copy into'));
    const nameIn = mk('input');
    nameIn.type = 'text';
    nameIn.id = 'dirs-copy-name';
    nameIn.style.flex = '0 1 18rem';
    nameRow.appendChild(nameIn);
    const recheck = mk('button', 'ghost', 'Re-read the cost');
    recheck.id = 'dirs-copy-recost';
    nameRow.appendChild(recheck);
    box.appendChild(nameRow);

    const cost = mk('div');
    cost.id = 'dirs-copy-cost';
    box.appendChild(cost);
    const act = mk('div');
    act.id = 'dirs-copy-act';
    box.appendChild(act);
    const prog = mk('div');
    prog.id = 'dirs-copy-prog';
    box.appendChild(prog);
    const after = mk('div');
    after.id = 'dirs-copy-after';
    box.appendChild(after);

    const closeRow = mk('div', 'set-field');
    const close = mk('button', 'ghost', 'Close');
    close.addEventListener('click', () => { host.textContent = ''; });
    closeRow.appendChild(close);
    box.appendChild(closeRow);

    let poll = null;
    //: The last SUCCESSFUL cost read. The start control does not exist until
    //: this is set, and it is what the click sends back as `confirmBytes`.
    let priced = null;
    //: The start control itself, created once and thereafter MUTATED.
    //
    // MEASURED IN THE BROWSER, and the reason this is not simply rebuilt on
    // every read: the first version re-read the cost on the name field's
    // `change` event and rebuilt the action row from scratch. `change` fires
    // on BLUR -- so pressing Copy right after typing a name blurred the
    // field, the handler cleared the row, and the button was removed from
    // under the pointer between mousedown and click. The click went nowhere
    // and the page said nothing. Driven headless: the POST was never sent.
    //
    // So the node is stable. Renaming the destination does not re-read the
    // price either, because the price is a property of the SOURCE and the
    // name changes only where it lands -- and every name, conflict, space and
    // liveness condition is re-checked server-side at the POST anyway, which
    // is the authority. Re-reading is a button of its own.
    let goBtn = null;

    function bar(pct) {
      const outer = mk('div');
      outer.style.cssText = 'height:.6rem;border:1px solid currentColor;' +
                            'border-radius:.3rem;overflow:hidden;margin:.4rem 0';
      const inner = mk('div');
      inner.style.cssText = 'height:100%;background:currentColor;width:' +
                            Math.max(0, Math.min(100, pct)) + '%';
      outer.appendChild(inner);
      return outer;
    }

    function showStatus(s) {
      prog.textContent = '';
      if (!s || (!s.running && s.ok === null && !s.startedAt)) return;
      prog.appendChild(mk('div', 'set-measured',
        (s.running ? 'Copying — ' : 'Last copy — ') +
        gb(s.bytes) + ' of ' + gb(s.expectBytes) + ', ' +
        num(s.files) + ' of ' + num(s.expectFiles) + ' files, ' +
        s.elapsedSeconds + 's'));
      prog.appendChild(bar(s.percent));
      if (s.current) prog.appendChild(mk('div', 'set-path', s.current));
      if (s.running) {
        const row = mk('div', 'set-field');
        const stop = mk('button', 'ghost', 'Cancel this copy');
        stop.id = 'dirs-copy-cancel';
        stop.addEventListener('click', async () => {
          stop.disabled = true;
          try { await jpost('/api/installs/copycancel', {}); }
          catch (e) { say(String(e.message || e), true); }
        });
        row.appendChild(stop);
        prog.appendChild(row);
        return;
      }
      if (s.ok === false) {
        prog.appendChild(mk('div', 'set-bad',
          'The copy did not finish: ' + (s.error || 'unknown')));
        if (s.leftover) {
          prog.appendChild(mk('div', 'set-help',
            'What landed is at ' + s.leftover + ' — under a temporary name ' +
            'and still carrying its marker file, so nothing will mistake it ' +
            'for a client. It was deliberately not deleted; deleting ' +
            'gigabytes on the way out of a cancel is a second long job ' +
            'nobody asked for.'));
          const row = mk('div', 'set-field');
          const del = mk('button', 'ghost', 'Discard the unfinished copy');
          del.id = 'dirs-copy-discard';
          del.addEventListener('click', async () => {
            del.disabled = true;
            try {
              await jpost('/api/installs/copydiscard', { path: s.leftover });
              say('Deleted ' + s.leftover + '.');
              await refreshCost();
              await tick();
            } catch (e) { say(String(e.message || e), true); del.disabled = false; }
          });
          row.appendChild(del);
          prog.appendChild(row);
        }
        return;
      }
      if (s.ok === true && s.declarable) offerDeclare(s.dest);
    }

    /* The copy is finished and UNDECLARED, which is the standing rule: a
     * folder that exists is OFFERED, never adopted. It goes through the same
     * detection and the same single declare call site as any other found
     * folder — there is no second path to the config from here. */
    async function offerDeclare(dest) {
      if (after.dataset.done === dest) return;
      after.dataset.done = dest;
      after.textContent = '';
      after.appendChild(mk('div', 'set-help',
        'The copy is on disk at ' + dest + ' and is NOT declared. It is a ' +
        'new install with its own detection — answer it below.'));
      try {
        const d = await jget('/api/installs/detect?root=' +
                             encodeURIComponent(dest));
        d.name = dest;
        const ul = mk('ul', 'set-list');
        ul.appendChild(candidateCard(d, {
          label: 'Declare this frozen copy',
        }));
        after.appendChild(ul);
      } catch (e) {
        after.appendChild(mk('div', 'set-bad', String(e.message || e)));
      }
    }

    async function tick() {
      let s;
      try { s = (await jget('/api/installs/copystatus')).copy; }
      catch (e) { return; }
      showStatus(s);
      if (s.running && !poll) poll = setInterval(tick, 1000);
      if (!s.running && poll) { clearInterval(poll); poll = null; }
    }

    function dropStart() {
      act.textContent = '';
      goBtn = null;
      priced = null;
    }

    function labelStart() {
      if (!goBtn || !priced) return;
      goBtn.textContent = 'Copy ' + gb(priced.bytes) + ' to ' +
                          (nameIn.value.trim() || priced.name);
    }

    async function refreshCost() {
      cost.textContent = '';
      dropStart();
      cost.appendChild(mk('div', 'set-help', 'measuring…'));
      let c;
      try {
        c = await jget('/api/installs/copycost?root=' +
                       encodeURIComponent(r.root) + '&name=' +
                       encodeURIComponent(nameIn.value.trim()));
      } catch (e) {
        cost.textContent = '';
        cost.appendChild(mk('div', 'set-bad', String(e.message || e)));
        return;
      }
      cost.textContent = '';
      if (!nameIn.value.trim() && c.name) nameIn.value = c.name;

      const m = c.measure || {};
      cost.appendChild(mk('p', 'set-measured',
        'THIS COPY: ' + gb(m.bytes) + ' — ' + num(m.files) + ' files in ' +
        num(m.dirs) + ' directories, walked from ' + c.source + ' in ' +
        (m.seconds || 0) + 's' +
        (m.complete ? '.' : ' — INCOMPLETE WALK, so that is a lower bound.') +
        (m.unreadable ? ' ' + num(m.unreadable) + ' entries could not be ' +
                        'read and are not in the total.' : '')));
      cost.appendChild(mk('p', 'set-measured',
        'DESTINATION: ' + (c.dest || '(none)') + ' — ' + gb(c.free) +
        ' free on that volume, and this needs ' + gb(m.bytes) + ' plus ' +
        gb(c.headroom) + ' of headroom (' + gb(c.required) + ' in all). ' +
        (c.enoughSpace ? 'That fits.'
                       : 'That does NOT fit, so it is refused rather than ' +
                         'half-copied.')));

      const live = c.live || {};
      if (live.live && live.live.length) {
        cost.appendChild(mk('p', 'set-bad',
          'RUNNING: ' + live.live.map(p => p.name + ' (pid ' + p.pid + ')')
            .join(', ') + ' is running out of this folder. Copying a live ' +
          'server is a torn read — files change under the walk, so the copy ' +
          'is of no single moment. Close it first.'));
      } else if (live.error) {
        cost.appendChild(mk('p', 'set-bad',
          'COULD NOT TELL whether anything is running there: ' + live.error));
      } else {
        cost.appendChild(mk('p', 'set-help',
          'Nothing found running out of this folder — ' +
          num(live.checked) + ' processes checked' +
          (live.opaque
            ? ', but ' + num(live.opaque) + ' of them would not give up ' +
              'their image path (that is what an elevated process looks ' +
              'like to this probe), so this is ‘none found’, not ‘none’.'
            : ', every image path readable.')));
      }

      if (c.fingerprint) {
        cost.appendChild(mk('p', 'set-help',
          'Derived data: this install fingerprints as ' + c.fingerprint +
          ' (namespace ' + c.sourceBaseId + '). ' + c.fingerprintNote));
      }
      cost.appendChild(mk('p', 'set-help', c.caveat));

      (c.warnings || []).forEach(w =>
        cost.appendChild(mk('p', 'set-bad', w)));
      if (!c.ok) {
        (c.blockers || []).forEach(b =>
          cost.appendChild(mk('p', 'set-bad', 'Refused: ' + b)));
        // No start control at all. A disabled button still reads as "this is
        // the thing you would click"; an absent one cannot be clicked by a
        // script, a keyboard, or a stale hit-test.
        return;
      }

      priced = { bytes: m.bytes, name: c.name || '' };
      const row = mk('div', 'set-field');
      const go = mk('button', 'primary', '');
      go.id = 'dirs-copy-start';
      go.addEventListener('click', async () => {
        go.disabled = true;
        say('starting the copy…');
        try {
          await jpost('/api/installs/copy', {
            path: r.root, name: nameIn.value.trim(),
            // The number the button was labelled with, sent back so the
            // server can refuse a click made against a stale price.
            confirmBytes: priced.bytes,
          });
          say('Copying. Nothing is declared when it finishes — you will be ' +
              'asked.');
          after.dataset.done = '';
          await tick();
        } catch (e) { say(String(e.message || e), true); go.disabled = false; }
      });
      row.appendChild(go);
      act.appendChild(row);
      goBtn = go;
      labelStart();
    }

    recheck.addEventListener('click', refreshCost);
    // Relabel only. NOT a re-read -- see the note on `goBtn`: `change` fires
    // on blur, and an async rebuild there deletes the control the user is in
    // the middle of clicking.
    nameIn.addEventListener('input', labelStart);
    nameIn.addEventListener('change', labelStart);
    refreshCost();
    tick();
    return box;
  }

  /* An already-declared install. */
  function installCard(r) {
    const li = mk('li', 'set-item');
    const head = mk('div', 'set-item-head');
    head.appendChild(mk('strong', null, r.serverName || r.label));
    head.appendChild(mk('span', 'set-tag ' + r.category,
                        CATEGORY_WORD[r.category]));
    head.appendChild(mk('span', 'set-help', 'plugin ' + r.kind));
    if (r.current) head.appendChild(mk('span', 'set-tag', 'configured root'));
    li.appendChild(head);
    li.appendChild(mk('div', 'set-path', r.root));
    if (r.serverName) {
      li.appendChild(mk('div', 'set-help',
        'Private server: ' + r.serverName + ' — parsed with ' + r.label));
    }
    if (r.frozenCopy) {
      const when = new Date((r.frozenCopy.at || 0) * 1000);
      li.appendChild(mk('div', 'set-help',
        'Frozen copy of ' + r.frozenCopy.from + ', taken ' +
        (isNaN(when.getTime()) ? 'at an unknown time'
                               : when.toLocaleString()) + ' — ' +
        gb(r.frozenCopy.bytes) + ' in ' + num(r.frozenCopy.files) +
        ' files. It is listed as an Offline Client because there is no ' +
        'server behind it: the parser plugin is still whatever claimed it, ' +
        'but the folder does not move.'));
    }
    if (!r.exists) {
      li.appendChild(mk('div', 'set-bad',
        'This folder is gone. The declaration is still here, which is why ' +
        'it is listed — the picker drops it silently.'));
    } else if (r.missing && r.missing.length) {
      li.appendChild(mk('div', 'set-bad',
        'Declared, but no longer a complete client: missing ' +
        r.missing.join(', ')));
    } else if (!r.pluginKnown) {
      li.appendChild(mk('div', 'set-bad',
        'No parser plugin named ' + r.kind + ' is installed, so this ' +
        'declaration cannot be honoured.'));
    } else {
      li.appendChild(mk('div', 'set-help',
        r.indexed ? 'Asset index built.'
                  : 'No asset index built for this client yet — the ' +
                    'Health section below offers it.'));
    }
    // The owner's ask: *"For 'Offline Clients', I want the user to
    // electively choose to create a copy of any Private server they add."*
    // Electively — so it is a button on the server's own row, opening a
    // panel that states the price, and it is offered only where it means
    // something: a private server that is actually on disk and complete.
    // Copying a frozen copy is not refused by the server, but offering it
    // here would suggest it is the point of the feature.
    const copyHost = mk('div');
    if (r.category === 'private-server' && r.usable && !r.frozenCopy) {
      const crow = mk('div', 'set-field');
      const cbtn = mk('button', 'ghost',
                      'Copy this into a frozen Offline Client…');
      cbtn.id = 'dirs-copy-open';
      cbtn.title = 'Shows what it would cost in bytes and files, and how ' +
                   'much room is left, before anything is copied.';
      cbtn.addEventListener('click', () => copyPanel(r, copyHost));
      crow.appendChild(cbtn);
      li.appendChild(crow);
    }
    li.appendChild(copyHost);

    const row = mk('div', 'set-field');
    const forget = mk('button', 'ghost', 'Forget this declaration');
    forget.title = 'Removes the declaration only. The folder is not touched.';
    forget.addEventListener('click', async () => {
      forget.disabled = true;
      say('forgetting…');
      try {
        await jpost('/api/installs/forget', { path: r.root });
        say('Forgot ' + r.root + '. The folder itself was not touched.');
        await render();
      } catch (e) { say(String(e.message || e), true); forget.disabled = false; }
    });
    row.appendChild(forget);
    li.appendChild(row);
    return li;
  }

  /* The offer that follows a successful declaration.
   *
   * It is an OFFER and it states the price first. Indexing and thumbnails are
   * both per-client: a newly declared client has neither, and filling both is
   * the longest thing this tool does. The measured figures are quoted here
   * rather than implied, because the complaint that started this work was a
   * user who clicked a button expecting nine minutes and waited thirty-six.
   *
   * Building for a client requires the viewer to be POINTED at it -- both
   * builders resolve their output through the base currently open -- so
   * "build now" is spelled out as the base switch it actually is, and it is a
   * separate click. */
  function buildOffer(res) {
    const box = mk('div', 'set-cost');
    box.appendChild(mk('h4', null, 'Build the derived data for ' + res.kind + '?'));
    box.appendChild(mk('p', null,
      'A newly declared client has no asset index and no thumbnails, and ' +
      'both are per client — declaring an eighth client does not share the ' +
      'seventh’s. Nothing has been started.'));
    box.appendChild(mk('p', 'set-measured',
      'Asset index: one pass over the .c3 containers. Measured at 32.3 s ' +
      'live on 7878, against 1.6 s to load a saved one — so it is paid on ' +
      'every open until it is built once.'));
    box.appendChild(mk('p', 'set-measured',
      'Thumbnails: the tool’s own estimate is 15-30 minutes and ~637 MB, ' +
      'and that estimate is PER CLIENT and is not universal — measured on ' +
      'Classic Conquer 2.0 as 47,973 textures + 1,352 meshes and 313 MB of ' +
      'PNG. Treat the number as what it cost somewhere else.'));
    const row = mk('div', 'set-field');
    const go = mk('button', 'primary',
                  'Point the viewer at this client and open the build offer');
    go.id = 'dirs-build-offer';
    go.addEventListener('click', async () => {
      go.disabled = true;
      say('switching…');
      try {
        await jpost('/api/base', { path: 'install:' + res.kind });
        say('The viewer is now on ' + res.kind +
            '. The Health section below is about this client.');
        if (window.coSetHealth) await window.coSetHealth.render();
        const sec = document.querySelector('#sec-health');
        if (sec) sec.scrollIntoView({ behavior: 'smooth' });
      } catch (e) { say(String(e.message || e), true); go.disabled = false; }
    });
    row.appendChild(go);
    const no = mk('button', 'ghost', 'Not now');
    no.addEventListener('click', () => box.remove());
    row.appendChild(no);
    box.appendChild(row);
    return box;
  }

  function group(title, rows, emptyText) {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', title));
    if (!rows.length) {
      box.appendChild(mk('div', 'set-empty', emptyText));
      return box;
    }
    const ul = mk('ul', 'set-list');
    rows.forEach(r => ul.appendChild(installCard(r)));
    box.appendChild(ul);
    return box;
  }

  // -------------------------------------------------------------- add by path
  function addByPath() {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Add an Offline Client by path'));
    box.appendChild(mk('div', 'set-help',
      'Type or paste the folder. It is checked for c3/data archives and an ' +
      'ini/ directory, then every parser plugin is asked how confident it ' +
      'is. Nothing is declared until you answer.'));
    const row = mk('div', 'set-field');
    const input = mk('input');
    input.type = 'text';
    input.id = 'dirs-add-path';
    input.placeholder = 'full path to a client folder';
    const check = mk('button', 'primary', 'Check this folder');
    check.id = 'dirs-add-check';
    const out = mk('div');
    out.id = 'dirs-add-out';
    row.appendChild(input);
    row.appendChild(check);
    box.appendChild(row);
    box.appendChild(out);

    // A folder-picker button. `webkitdirectory` is what a browser gives us --
    // it hands over the RELATIVE tree, never the absolute path, so the value
    // is used to prefill the text field under the clients root and the user
    // still confirms. Saying that plainly beats a picker that appears to work
    // and silently drops the drive letter.
    const pick = mk('input');
    pick.type = 'file';
    pick.id = 'dirs-add-pick';
    pick.setAttribute('webkitdirectory', '');
    pick.style.display = 'none';
    const pickBtn = mk('button', 'ghost', 'Browse…');
    pickBtn.id = 'dirs-add-browse';
    pickBtn.title = 'A browser folder picker hands over the folder NAME, ' +
                    'not its absolute path. It is used to fill the field ' +
                    'under the scan folder; check it before declaring.';
    pickBtn.addEventListener('click', () => pick.click());
    pick.addEventListener('change', () => {
      const f = pick.files && pick.files[0];
      if (!f) return;
      const rel = (f.webkitRelativePath || '').split('/')[0];
      if (!rel) return;
      const base = (dirs && dirs.clientsRoot) || '';
      input.value = base ? (base.replace(/[\\/]+$/, '') + '\\' + rel) : rel;
      say('Filled from the picker as ' + input.value +
          ' — the browser only gave the folder name, so check the path.');
    });
    row.appendChild(pickBtn);
    row.appendChild(pick);

    check.addEventListener('click', async () => {
      const path = input.value.trim();
      out.textContent = '';
      if (!path) { say('Give a path first.', true); return; }
      say('checking…');
      try {
        const d = await jget('/api/installs/detect?root=' +
                             encodeURIComponent(path));
        d.name = path;
        const ul = mk('ul', 'set-list');
        ul.appendChild(candidateCard(d, { label: 'Declare this client' }));
        out.appendChild(ul);
        say('');
      } catch (e) { say(String(e.message || e), true); }
    });
    return box;
  }

  // ------------------------------------------------------- add private server
  function addPrivateServer() {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Add a Private Server'));
    box.appendChild(mk('div', 'set-help',
      'A private server’s own game client is just a folder, so it is ' +
      'added the same way — there is no default location and none is ' +
      'assumed. When you declare it, you are asked what private server it ' +
      'is: no plugin can know that, and the plugin label names the parser, ' +
      'not the shard.'));
    const row = mk('div', 'set-field');
    const input = mk('input');
    input.type = 'text';
    input.id = 'dirs-srv-path';
    input.placeholder = 'full path to the private server’s client folder';
    const pick = mk('input');
    pick.type = 'file';
    pick.setAttribute('webkitdirectory', '');
    pick.style.display = 'none';
    const pickBtn = mk('button', 'ghost', 'Browse…');
    pickBtn.id = 'dirs-srv-browse';
    pickBtn.addEventListener('click', () => pick.click());
    pick.addEventListener('change', () => {
      const f = pick.files && pick.files[0];
      if (!f) return;
      const rel = (f.webkitRelativePath || '').split('/')[0];
      if (rel) {
        input.value = rel;
        say('The browser folder picker gives the folder name only. Complete ' +
            'the path to ' + rel + ' before checking it.');
      }
    });
    const check = mk('button', 'primary', 'Check this folder');
    check.id = 'dirs-srv-check';
    const out = mk('div');
    out.id = 'dirs-srv-out';
    row.appendChild(input);
    row.appendChild(pickBtn);
    row.appendChild(pick);
    row.appendChild(check);
    box.appendChild(row);
    box.appendChild(out);
    check.addEventListener('click', async () => {
      const path = input.value.trim();
      out.textContent = '';
      if (!path) { say('Give a path first.', true); return; }
      say('checking…');
      try {
        const d = await jget('/api/installs/detect?root=' +
                             encodeURIComponent(path));
        d.name = path;
        const ul = mk('ul', 'set-list');
        const card = candidateCard(d, { label: 'Declare this private server' });
        ul.appendChild(card);
        out.appendChild(ul);
        say('');
      } catch (e) { say(String(e.message || e), true); }
    });
    return box;
  }

  // ------------------------------------------------------------------- scan
  function scanBlock() {
    const box = mk('div');
    box.appendChild(mk('h4', 'set-sub', 'Scan for clients not yet declared'));
    const where = mk('div', 'set-help');
    where.id = 'dirs-scan-where';
    if (dirs && dirs.clientsRoot) {
      where.textContent = 'Scans ' + dirs.clientsRoot + ' — ' +
                          dirs.clientsRootWhy + '.';
    } else {
      where.textContent = 'No folder to scan: ' +
                          (dirs ? dirs.clientsRootWhy : 'unknown') + '.';
    }
    box.appendChild(where);
    box.appendChild(mk('div', 'set-help',
      'This folder is resolved through the config, never compiled in — ' +
      'set it below to point the scan somewhere else.'));

    const row = mk('div', 'set-field');
    const btn = mk('button', 'primary', 'Scan');
    btn.id = 'dirs-scan';
    const out = mk('div');
    out.id = 'dirs-scan-out';
    btn.addEventListener('click', async () => {
      out.textContent = '';
      say('scanning…');
      try {
        const res = await jget('/api/installs/scan');
        renderScan(out, res);
        say('');
      } catch (e) { say(String(e.message || e), true); }
    });
    row.appendChild(btn);

    const rootIn = mk('input');
    rootIn.type = 'text';
    rootIn.id = 'dirs-scan-root';
    rootIn.placeholder = 'folder to scan for clients';
    rootIn.value = (dirs && dirs.clientsRoot) || '';
    const setBtn = mk('button', 'ghost', 'Set scan folder');
    setBtn.id = 'dirs-scan-setroot';
    setBtn.addEventListener('click', async () => {
      say('saving…');
      try {
        const res = await jpost('/api/installs/clientsroot',
                                { path: rootIn.value.trim() });
        say('Scan folder is now ' + (res.clientsRoot || '(derived)') +
            ' — ' + res.clientsRootWhy + '.');
        await render();
      } catch (e) { say(String(e.message || e), true); }
    });
    row.appendChild(rootIn);
    row.appendChild(setBtn);
    box.appendChild(row);
    box.appendChild(out);

    // Scan on arrival, without being asked. The launch-time check in
    // dirscan.js prompts when it finds an undeclared folder and sends the
    // user HERE -- and this page rendered nothing until the button was
    // pressed, so they arrived at an empty section with no sign that one
    // more click was needed. The popup made a promise this page did not keep.
    //
    // This does NOT weaken the standing rule that nothing is declared
    // silently. `/api/installs/scan` is a GET and a pure read: it OFFERS
    // folders and declares none. The write path is still
    // `/api/installs/declare`, still requires an explicit kind, and still has
    // exactly one call site inside a click handler. Scanning is looking;
    // declaring is answering.
    (async () => {
      try {
        renderScan(out, await jget('/api/installs/scan'));
      } catch (e) {
        // Silent by design: an arrival scan that fails must not shout over a
        // page opened for some other reason. The button is still there and
        // still reports its own errors loudly.
      }
    })();

    return box;
  }

  function renderScan(out, res) {
    out.textContent = '';
    if (res.error) { out.appendChild(mk('div', 'set-bad', res.error)); return; }
    const all = res.candidates || [];
    const torn = all.filter(c => c.incompleteCopy);
    const usable = all.filter(c => c.client);
    const rejected = all.filter(c => !c.client && !c.incompleteCopy);
    // Not inside the collapsed "other folders" details. A half-copied tree
    // is the one thing here that is actively costing the user disk and can
    // be deleted, so it is the one thing the scan must not fold away.
    if (torn.length) {
      out.appendChild(mk('div', 'set-bad',
        torn.length + ' unfinished cop' + (torn.length === 1 ? 'y' : 'ies') +
        ' under ' + res.clientsRoot + '. These are not clients and never ' +
        'will be — they are taking up room.'));
      const ul = mk('ul', 'set-list');
      torn.forEach(c => ul.appendChild(candidateCard(c)));
      out.appendChild(ul);
    }
    if (!usable.length) {
      out.appendChild(mk('div', 'set-empty',
        'Nothing new: every client folder under ' + res.clientsRoot +
        ' is already declared.'));
    } else {
      out.appendChild(mk('div', 'set-help',
        'Found ' + usable.length + ' undeclared client folder' +
        (usable.length === 1 ? '' : 's') +
        '. None of them has been declared — these are offers.'));
      const ul = mk('ul', 'set-list');
      usable.forEach(c => ul.appendChild(candidateCard(c)));
      out.appendChild(ul);
    }
    if (rejected.length) {
      const ul = mk('ul', 'set-list');
      rejected.forEach(c => ul.appendChild(candidateCard(c)));
      const det = mk('details');
      det.appendChild(mk('summary', null,
        rejected.length + ' other folder' + (rejected.length === 1 ? '' : 's') +
        ' here that are not clients'));
      det.appendChild(ul);
      out.appendChild(det);
    }
  }

  // ----------------------------------------------------------------- render
  async function render() {
    host.textContent = '';
    try {
      dirs = await jget('/api/installs/dirs');
    } catch (e) {
      host.appendChild(mk('div', 'set-bad', String(e.message || e)));
      return;
    }
    try {
      if (!plugins.length) {
        const doc = await jget('/api/plugins');
        plugins = (doc.plugins || []).map(p => ({
          name: p.name, label: p.label,
          // `/api/plugins` does not serve `origin`; the ranking does, and the
          // declared installs do. Fall back to what the declarations say
          // rather than inventing one.
          origin: (dirs.installs.find(i => i.kind === p.name) || {}).origin
                  || 'unknown',
        }));
      }
    } catch (e) { /* the manual picker degrades to an empty list, not a crash */ }

    host.appendChild(mk('div', 'set-help',
      'Declared in ' + dirs.storePath + '. A folder is a client because you ' +
      'said so — this list is your own record, not a scan result.'));

    if (pendingOffer) {
      host.appendChild(buildOffer(pendingOffer));
      pendingOffer = null;
    }

    host.appendChild(group('Offline Clients', dirs.offlineClients,
      'None declared.'));
    host.appendChild(group('Private servers', dirs.privateServers,
      'None declared. Add one below — there is no default location.'));
    if (dirs.unclassified.length) {
      host.appendChild(group('Unclassified', dirs.unclassified,
        'None.'));
    }

    host.appendChild(scanBlock());
    host.appendChild(addByPath());
    host.appendChild(addPrivateServer());
    host.appendChild(note);
  }

  window.coSetDirs = { render: render };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
