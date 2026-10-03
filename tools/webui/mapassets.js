/* mapassets.js -- the Map Asset Viewer.
 *
 * The owner's ask: "a Map Asset Viewer page that helps people find, and
 * collect map satellite files", and then "a UI to use collected assets in the
 * Map Editor".
 *
 * THIS FILE RENDERS AND IT POSTS. It does not decide what a satellite is
 * (`tools/mapparts.py` owns the closure), which maps can hold a cover
 * (`tools/mapcover.py`), or where one may be placed (`tools/mapassets.py`).
 * Every value in a row arrives from `/api/mapassets` already decided. That is
 * the same rule `portagepanel.js` states for itself, and for the same reason:
 * a second answer here would drift from the first, and the direction of the
 * drift is the page promising something the editor will not do.
 *
 * THE ONE THING THIS PAGE MUST NOT DO
 * -----------------------------------
 * List a cover without saying where it can go. A cover whose key is resolved
 * by an index the target map does not load produces a VALID record, a file
 * that re-parses, a sprite this page can draw -- and nothing in game. So
 * `placeableOn` is a column, never a tooltip, and a row with an unidentified
 * index says UNKNOWN rather than showing a bare count that reads as "one".
 */
'use strict';

(function () {

  var API = '/api/mapassets';
  var state = { kind: 'covers', q: '', data: null, busy: false };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
               "'": '&#39;' }[c];
    });
  }

  async function getJSON(url) {
    var r = await fetch(url, { cache: 'no-store' });
    var b = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(b.error || ('HTTP ' + r.status));
    return b;
  }

  /* COVERS. `placeable` is the column the feature exists for, and the
   * "used by" count beside it is what makes the difference visible: a cover
   * used by one map and placeable on five is the whole point. */
  function coverHead() {
    return '<th>cover key</th><th class="n">records</th>' +
           '<th class="n">used by</th><th class="n">can go on</th>' +
           '<th>index</th><th>source record</th>';
  }

  function coverRow(r) {
    var used = (r.maps || []).length;
    var can = r.placeableCount;
    /* A row whose index could not be identified is NOT shown as "can go on
     * N maps" -- that number is the maps already drawing it, and printed
     * bare it reads as an answer to a question nobody could answer. */
    var canCell = r.indexes && r.indexes.length
      ? '<b>' + can + '</b>'
      : '<span class="ma-unknown" title="The index that resolves this key ' +
        'was not identified, so where else it could go is unknown. Only the ' +
        'map(s) already drawing it are offered.">' + can + '?</span>';
    var wider = (r.indexes && r.indexes.length && can > used)
      ? ' <span class="ma-gain">+' + (can - used) + '</span>' : '';
    var src = (r.sources && r.sources[0])
      ? esc(r.sources[0].map) + ' [' + r.sources[0].index + ']' : '—';
    return '<tr>' +
      '<td><code>' + esc(r.key) + '</code>' +
        (r.size ? ' <span class="mut">' + r.size[0] + '×' + r.size[1] +
                  '</span>' : '') + '</td>' +
      '<td class="n">' + r.records + '</td>' +
      '<td class="n">' + used + '</td>' +
      '<td class="n">' + canCell + wider + '</td>' +
      '<td class="mut">' + esc((r.indexes || []).join(', ') || '—') + '</td>' +
      '<td class="mut">' + src +
        ' <button class="ma-btn tiny" data-collect="' + esc(
          (r.sources && r.sources[0]) ? r.sources[0].map : '') +
        '">Collect map</button></td>' +
      '</tr>';
  }

  function sharedHead() {
    return '<th>file</th><th class="n">maps</th><th>used by</th>';
  }

  function sharedRow(r) {
    var shown = r.maps.slice(0, 6).map(esc).join(', ');
    var more = r.maps.length > 6 ? ' <span class="mut">+' +
      (r.maps.length - 6) + ' more</span>' : '';
    return '<tr>' +
      '<td><code>' + esc(r.rel) + '</code></td>' +
      '<td class="n"><b>' + r.mapCount + '</b></td>' +
      '<td class="mut">' + shown + more + '</td>' +
      '</tr>';
  }

  function render() {
    var d = state.data;
    var head = document.getElementById('head');
    var body = document.querySelector('#rows tbody');
    var foot = document.getElementById('foot');
    var count = document.getElementById('count');
    var spread = document.getElementById('spread');
    if (!d) { body.innerHTML = ''; return; }

    if (d.kind === 'covers') {
      head.innerHTML = coverHead();
      body.innerHTML = d.rows.map(coverRow).join('') ||
        '<tr><td colspan="6" class="mut">nothing matches that filter</td></tr>';
      count.textContent = d.total + ' cover(s), ' + d.recordTotal + ' record(s)';
      /* THE HONESTY CARRY, DRAWN WHETHER OR NOT IT IS ZERO. Shown only when
       * non-zero, a reader cannot tell "none" from "not reported". */
      foot.textContent = d.unknownIndexCount
        ? d.unknownIndexCount + ' cover(s) have no identified index. ' +
          d.unknownIndexNote
        : 'Every cover listed has an identified index, so "can go on" is a ' +
          'measured answer for all of them.';
      var gains = d.rows.filter(function (r) {
        return r.indexes.length && r.placeableCount > (r.maps || []).length;
      }).length;
      spread.innerHTML = gains
        ? '<b>' + gains + '</b> of these are usable on more maps than ' +
          'currently draw them.'
        : '';
    } else {
      head.innerHTML = sharedHead();
      body.innerHTML = d.rows.map(sharedRow).join('') ||
        '<tr><td colspan="3" class="mut">nothing matches that filter</td></tr>';
      count.textContent = d.total + ' shared file(s)';
      foot.textContent = d.truncated
        ? 'Showing ' + d.rows.length + '; ' + d.truncated + ' more not drawn.'
        : 'Every shared file is listed. Editing one of these changes every ' +
          'map named beside it.';
      spread.innerHTML = '';
    }

    body.querySelectorAll('[data-collect]').forEach(function (b) {
      b.onclick = function () { collect(b.getAttribute('data-collect')); };
    });
  }

  /* COLLECT GOES THROUGH THE EXISTING SURFACE. `/api/keep/map` is a dry run
   * by construction and already explains a map's three surprises -- shared
   * scenery, art inside the archives, and the DMap's integrity manifest
   * entry. Re-implementing any of that here would be a second answer. */
  async function collect(name) {
    if (!name || state.busy) return;
    state.busy = true;
    try {
      var pre = await getJSON('/api/keep/map?name=' + encodeURIComponent(name));
      var n = (pre.files && pre.files.length) || pre.fileCount || 0;
      var msg = 'Collect ' + name + '?\n\n' + n + ' file(s).';
      if (pre.integrity && pre.integrity.checked) {
        msg += '\n\nIts .DMap is listed in the install’s integrity ' +
               'manifest.';
      }
      if (pre.shared && pre.shared.length) {
        msg += '\n\n' + pre.shared.length + ' of them are shared with other ' +
               'maps.';
      }
      if (!window.confirm(msg)) return;
      await fetch('/api/keep/map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
      });
    } catch (e) {
      window.alert('could not collect: ' + e.message);
    } finally {
      state.busy = false;
    }
  }

  async function load() {
    var body = document.querySelector('#rows tbody');
    body.innerHTML = '<tr><td colspan="6" class="mut">reading every map ' +
                     'once… this is cached after the first time</td></tr>';
    try {
      state.data = await getJSON(
        API + '?kind=' + encodeURIComponent(state.kind) +
        '&q=' + encodeURIComponent(state.q));
      document.getElementById('unavailable').hidden = true;
    } catch (e) {
      document.getElementById('unavailable').hidden = false;
      document.getElementById('why').textContent = e.message;
      state.data = null;
    }
    render();
  }

  function mount() {
    document.querySelectorAll('.ma-tab').forEach(function (b) {
      b.onclick = function () {
        document.querySelectorAll('.ma-tab').forEach(function (x) {
          x.classList.remove('on');
        });
        b.classList.add('on');
        state.kind = b.getAttribute('data-kind');
        load();
      };
    });
    var q = document.getElementById('q');
    var t = null;
    q.addEventListener('input', function () {
      clearTimeout(t);
      t = setTimeout(function () { state.q = q.value; load(); }, 200);
    });
    load();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
