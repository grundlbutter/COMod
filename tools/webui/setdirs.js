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
    return box;
  }

  function renderScan(out, res) {
    out.textContent = '';
    if (res.error) { out.appendChild(mk('div', 'set-bad', res.error)); return; }
    const usable = (res.candidates || []).filter(c => c.client);
    const rejected = (res.candidates || []).filter(c => !c.client);
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
