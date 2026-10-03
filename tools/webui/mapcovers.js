/* mapcovers.js -- the Map Editor's cover palette.
 *
 * The owner's third ask: "build a UI to use collected assets in the Map
 * Editor". This is that UI. It renders and it posts; every decision arrives
 * from `/api/mapedit/palette` already made -- which covers this map can take,
 * and for each one it cannot, WHY.
 *
 * THE RULE THIS PANEL EXISTS TO MAKE VISIBLE
 * -------------------------------------------
 * A cover's `key` is resolved through an INDEX the open map loads. Place one
 * whose key no loaded index resolves and the record is VALID, the file
 * re-parses, the editor draws it -- and the client shows nothing. So an
 * unusable cover is struck through with its reason attached and its Place
 * button removed, NEVER filtered out: a user who sees art on the Map Assets
 * page and cannot find it here needs the sentence, not the silence.
 *
 * AND THE WRITE IS GATED. `map/map/*.DMap` is listed in `integrity.json`.
 * The acknowledgement is typed once per session and the server refuses
 * without the exact string -- the 409 carries it, so this panel never has to
 * hold a copy of a constant the server owns.
 */
'use strict';

(function () {

  var state = { map: '', data: null, acked: false, busy: false, sel: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
               "'": '&#39;' }[c];
    });
  }

  function body() { return document.getElementById('covers-body'); }

  async function getJSON(url) {
    var r = await fetch(url, { cache: 'no-store' });
    var b = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(b.error || ('HTTP ' + r.status));
    return b;
  }

  async function postJSON(url, obj) {
    /* NO TOKEN HEADER HERE, DELIBERATELY. `csrf.js` wraps `window.fetch`
     * for the whole page and attaches it. I wrote `window.CoCsrf.token()`
     * first -- an API that does not exist -- which would have silently sent
     * every request unauthenticated. Checked rather than assumed. */
    var r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj)
    });
    var b = await r.json().catch(function () { return {}; });
    return { status: r.status, body: b };
  }

  /* THE SOURCE MUST BE A RECORD OF THE OPEN MAP. `add` copies a record BY
   * INDEX inside the map being edited (`mapedit.apply_cover_edits` resolves
   * `from` in the open map's own late list). `usable` is decided by which
   * INDEX the open map loads, so a usable row can have every one of its
   * `sources` on some OTHER map -- 72 of 82 on luckytree01_new, measured
   * 2026-09-25. This used to post `sources[0].index` regardless: out of
   * range it was refused ("no record N"); in range it copied a DIFFERENT
   * cover of the open map and said "Placed". */
  function sourceOnOpenMap(r) {
    return (r.sources || []).find(function (s) { return s.map === state.map; });
  }

  function row(r) {
    var src = sourceOnOpenMap(r);
    var where = src ? esc(src.map) + ' [' + src.index + ']' : '—';
    if (!r.usable) {
      /* STRUCK THROUGH AND EXPLAINED, not hidden. The `why` is the server's
       * sentence, printed verbatim -- rewording it here would be this file
       * deciding something it does not know. */
      return '<div class="mc-row mc-no">' +
        '<code class="mc-key">' + esc(r.key) + '</code>' +
        '<span class="mc-why">' + esc(r.why) + '</span>' +
        '</div>';
    }
    var size = r.size
      ? '<span class="mut mc-sz">' + r.size[0] + '×' + r.size[1] + '</span>'
      : '';
    if (!src) {
      /* Usable -- the open map resolves the key -- but drawn only elsewhere.
       * Listed, with the sentence and without a button: copying a record
       * from another map is a server-side change this panel does not have.
       * The row is NEVER filtered. */
      var maps = r.maps || (r.sources || []).map(function (s) { return s.map; });
      return '<div class="mc-row">' +
        '<code class="mc-key">' + esc(r.key) + '</code>' + size +
        '<span class="mut mc-src">drawn on ' + esc(maps.join(', ')) +
        '; copying from another map is not supported yet</span>' +
        '</div>';
    }
    return '<div class="mc-row">' +
      '<code class="mc-key">' + esc(r.key) + '</code>' + size +
      '<span class="mut mc-src">' + where + '</span>' +
      '<button class="mc-btn" data-place="' + esc(r.key) + '" ' +
        'data-from="' + src.index + '" ' +
        'data-srcmap="' + esc(src.map) + '">Place…</button>' +
      '</div>';
  }

  function render() {
    var d = state.data;
    if (!d) { body().textContent = 'nothing loaded'; return; }
    var head = '';
    if (!d.capable) {
      /* THE LIMIT IS THE EDITOR'S, NOT THE MAP'S: pre-1006 maps keep their
       * covers in the first layer table, and this editor writes only the
       * v1006 second list. The old sentence -- "This map cannot hold a
       * cover" -- was false. Same wording as `mapassets.palette_reason`;
       * change both or neither. */
      /* The FLOOR comes from the server (`minVersion`), not from a literal
       * here. It used to read "v1006" in this string, which made the page a
       * sixth home for the one number that decides whether this panel can do
       * anything -- and the first place to go stale when the write path
       * reaches the first layer table. */
      var floor = d.minVersion ? ('v' + d.minVersion) : 'a later version';
      head = '<p class="mc-warn"><b>This editor cannot yet write a cover to ' +
        'this map.</b> It is version ' + esc(d.version) + ', and the editor ' +
        'writes only the second record list that ' + esc(floor) + ' and ' +
        'later carry; the map itself may already hold covers in its first ' +
        'layer table. Every cover below is listed with its reason rather ' +
        'than hidden.</p>';
    } else {
      head = '<p class="mut">' + d.usableCount + ' of ' + d.total +
        ' cover(s) can be placed on this map. It carries ' + d.records +
        ' record(s) now' +
        /* WHICH FILE that count describes. The palette used to read the
         * INSTALL always, so after a placement it reported the pre-edit
         * count for the rest of the process and the only evidence the edit
         * had happened was the alert that said so. */
        (d.staged ? ' <b>in your staged copy</b>' : '') + '.' +
        /* DRAWN EVEN WHEN ZERO: a count a reader only sees when it is
         * non-zero cannot be told from one that was not reported. */
        (d.blockedCount
          ? ' <b>' + d.blockedCount + '</b> cannot — each says why.'
          : ' None are blocked.') + '</p>';
    }
    if (d.capable && d.usableCount) {
      /* The head's "N can be placed" is the server's count; how many of
       * those this panel can actually copy is decided here, so it is said
       * here -- including when it is all of them. */
      var here = d.rows.filter(function (r) {
        return r.usable && sourceOnOpenMap(r);
      }).length;
      head += '<p class="mut">' + here + ' of those ' + d.usableCount +
        ' have a record on this map to copy' +
        (here < d.usableCount
          ? '; the other ' + (d.usableCount - here) + ' are drawn only on ' +
            'other maps, and copying from another map is not supported yet.'
          : '.') + '</p>';
    }
    head += '<p class="mc-warn"><b>Writes an integrity-checked file.</b> ' +
      'Staged to <code>Installed/stage/</code> only; <code>comod.py install</code> ' +
      'is still the only thing that touches the game.</p>';
    body().innerHTML = head +
      '<div class="mc-list">' + d.rows.map(row).join('') + '</div>';

    body().querySelectorAll('[data-place]').forEach(function (b) {
      b.onclick = function () {
        place(b.getAttribute('data-place'),
              parseInt(b.getAttribute('data-from'), 10),
              b.getAttribute('data-srcmap'));
      };
    });
  }

  async function place(key, from, srcMap) {
    if (state.busy) return;
    /* The coordinates are the map's own pixel space -- the same numbers the
     * inspector shows for a pick, so a user can read them off the map rather
     * than guess. */
    var at = window.prompt(
      'Place "' + key + '" at which map pixel?\n\n' +
      'x,y  (copied from ' + srcMap + ' record ' + from + ')', '');
    if (!at) return;
    var p = at.split(',');
    var x = parseInt(p[0], 10), y = parseInt(p[1], 10);
    if (!isFinite(x) || !isFinite(y)) { window.alert('need x,y'); return; }

    state.busy = true;
    try {
      var url = '/api/mapedit/covers?map=' + encodeURIComponent(state.map);
      var payload = { edits: [{ op: 'add', from: from, x: x, y: y }] };
      var res = await postJSON(url, payload);
      if (res.status === 409 && res.body.needsAck) {
        /* THE SERVER OWNS THE STRING. We show its own explanation and send
         * back the token it handed us -- this file never hardcodes it. */
        if (!window.confirm(res.body.why + '\n\nProceed?')) return;
        payload.ack = res.body.ack;
        res = await postJSON(url, payload);
      }
      if (!res.body.ok) {
        window.alert('refused: ' + (res.body.error || JSON.stringify(res.body)));
        return;
      }
      var msg = 'Placed. ' + res.body.recordsBefore + ' → ' +
                res.body.recordsAfter + ' record(s).';
      if (res.body.refusedCount) {
        msg += '\n\n' + res.body.refusedCount + ' edit(s) refused:\n' +
               res.body.refused.map(function (r) { return '• ' + r.why; })
                 .join('\n');
      }
      msg += '\n\nStaged: ' + res.body.staged;
      window.alert(msg);
      load(state.map);
    } catch (e) {
      window.alert('could not place: ' + e.message);
    } finally {
      state.busy = false;
    }
  }

  async function load(name) {
    state.map = name || '';
    if (!state.map) { state.data = null; render(); return; }
    body().textContent = 'reading the palette for ' + state.map + '…';
    try {
      state.data = await getJSON('/api/mapedit/palette?map=' +
                                 encodeURIComponent(state.map));
    } catch (e) {
      state.data = null;
      body().innerHTML = '<span class="mut">could not read the palette: ' +
                         esc(e.message) + '</span>';
      return;
    }
    render();
  }

  /* The Map Editor owns "which map is open". This listens rather than asks,
   * so there is one answer to that question and it is not this file's. */
  window.addEventListener('co-map-opened', function (e) {
    load(e && e.detail && e.detail.name);
  });
  window.CoMapCovers = { load: load };
})();
