// Build-diff browser.
//
// EXTERNAL BY NECESSITY, not by preference: the viewer serves
// `Content-Security-Policy: script-src 'self'`, so an inline <script>
// is BLOCKED and the page renders its whole layout with no data in it.
// That failure is silent in the markup -- the HTML is valid, the table
// headers draw, and only the browser console says why the rows never
// arrive. A test that greps the page would have passed it.
(() => {
  const $ = s => document.querySelector(s);
  const get = (p) => fetch(p).then(r => r.ok ? r.json()
    : r.json().catch(() => ({ error: r.statusText })).then(e => Promise.reject(e)));
  const num = n => (n === null || n === undefined) ? '' : n.toLocaleString();

  let seq = null, offset = 0;
  const LIMIT = 200;

  function drawSteps(steps) {
    const tb = $('#steps tbody');
    tb.textContent = '';
    for (const s of steps) {
      const tr = document.createElement('tr');
      tr.dataset.seq = s.seq;
      const label = document.createElement('td');
      label.textContent = (s.prev || '?') + ' → ' + s.patch;
      const cells = [label];
      for (const v of [s.total, s.added, s.moved]) {
        const td = document.createElement('td');
        td.className = 'n';
        td.textContent = num(v);
        cells.push(td);
      }
      const f = document.createElement('td');
      if (s.repackaging) {
        const b = document.createElement('span');
        b.className = 'flag repack';
        b.textContent = 'REPACKAGING';
        // Never silently drop this in favour of a tidier column: a step whose
        // moved count dwarfs its added count changed almost nothing, and the
        // total is the number that misleads.
        b.title = num(s.moved) + ' moved against ' + num(s.added) +
                  ' added — mostly the same content in different archives';
        f.appendChild(b);
      }
      cells.push(f);
      cells.forEach(c => tr.appendChild(c));
      tr.addEventListener('click', () => select(s));
      tb.appendChild(tr);
    }
  }

  function select(s) {
    seq = s.seq; offset = 0;
    document.querySelectorAll('#steps tbody tr').forEach(tr =>
      tr.classList.toggle('sel', String(tr.dataset.seq) === String(s.seq)));
    $('#evh').textContent = 'Events — ' + (s.prev || '?') + ' → ' + s.patch;
    loadEvents();
  }

  function loadEvents() {
    if (seq === null) return;
    const q = new URLSearchParams({ seq, limit: LIMIT, offset });
    if ($('#kind').value) q.set('kind', $('#kind').value);
    if ($('#section').value) q.set('section', $('#section').value);
    get('/api/diff/step?' + q).then(d => {
      const tb = $('#events tbody');
      tb.textContent = '';
      for (const e of d.events) {
        const tr = document.createElement('tr');
        const mk = (text, cls) => {
          const td = document.createElement('td');
          if (cls) td.className = cls;
          td.textContent = text === null || text === undefined ? '' : text;
          return td;
        };
        // A WDF keys its entries by hash, so a REMOVED archive entry does
        // not always have a recoverable path. Show the asset id rather than
        // an empty cell, and say which it is -- a blank column reads as a
        // broken page, and this data genuinely cannot name those rows.
        if (e.named) {
          tr.appendChild(mk(e.name, 'bd-name'));
        } else {
          const td = mk(e.asset || '', 'bd-name bd-unnamed');
          td.title = 'no path recorded for this entry — archives key by ' +
                     'hash, so a removed entry cannot always be named';
          tr.appendChild(td);
        }
        tr.appendChild(mk(e.section));
        tr.appendChild(mk(e.kind, 'kind-' + e.kind));
        tr.appendChild(mk(e.container || ''));
        tr.appendChild(mk(num(e.new_size), 'n'));
        tb.appendChild(tr);
      }
      const from = d.total ? offset + 1 : 0;
      $('#page').textContent = from + '–' +
        Math.min(offset + d.events.length, d.total) + ' of ' + num(d.total);
      $('#empty').hidden = d.total > 0;
      if (!d.total) $('#empty').textContent = 'No events match this filter.';
      $('#prev').disabled = offset === 0;
      $('#next').disabled = offset + LIMIT >= d.total;
    }).catch(e => { $('#page').textContent = e.error || 'request failed'; });
  }

  $('#prev').addEventListener('click', () => {
    offset = Math.max(0, offset - LIMIT); loadEvents();
  });
  $('#next').addEventListener('click', () => { offset += LIMIT; loadEvents(); });
  $('#kind').addEventListener('change', () => { offset = 0; loadEvents(); });
  $('#section').addEventListener('change', () => { offset = 0; loadEvents(); });

  get('/api/diff/steps').then(d => {
    if (!d.available) {
      $('#unavailable').hidden = false;
      $('#why').textContent = d.why || '';
      $('#ranking-note').hidden = true;
      return;
    }
    $('#sub').textContent = d.steps.length + ' steps, ' +
      (d.steps[0] && d.steps[0].prev ? d.steps[0].prev : '?') + ' → ' +
      (d.steps.length ? d.steps[d.steps.length - 1].patch : '?');
    drawSteps(d.steps);
    $('#empty').hidden = false;
  }).catch(e => {
    $('#unavailable').hidden = false;
    $('#why').textContent = e.error || String(e);
  });
})();
