/* pageshot.js -- save the whole page as a PNG under out/viewer/shots/.
 *
 * Shared by the asset browser and the character builder, because 'what did
 * this look like' is a question both pages get asked and the viewport-only
 * snapshot does not answer it: a picker, a filter row or a panel of numbers is
 * exactly the part worth keeping.
 *
 * No library and no network. The DOM is cloned into an <svg><foreignObject>,
 * the stylesheet is inlined, the WebGL canvas is replaced by its own snapshot
 * (a live canvas cannot render inside an SVG), and every <img> is refetched and
 * re-encoded as a data: URI because the SVG, once it is a data URL, cannot make
 * requests of its own. The result is drawn to a 2D canvas and posted to
 * /api/snapshot like any other frame.
 */

'use strict';

async function inlineImage(url) {
  const r = await fetch(url);
  const blob = await r.blob();
  return await new Promise(res => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = () => res(null);
    fr.readAsDataURL(blob);
  });
}

/** Turn every `url(...)` in a stylesheet into a data URI.
 *
 *  The SVG, once it is a data URL, cannot make requests of its own -- which is
 *  why `<img src>` is already inlined. CSS backgrounds have exactly the same
 *  problem and were not: the skin's 9-slice frame (`ui.css` resolving
 *  `--co-frame-*` to `/skins/classic/border/*.png`) is a background image, so
 *  without this the gold border is missing from every screenshot of the very
 *  thing the screenshot is meant to show.
 */
async function inlineCssUrls(css) {
  const urls = new Set();
  for (const m of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)) {
    const u = m[1].trim();
    if (u && !u.startsWith('data:')) urls.add(u);
  }
  for (const u of urls) {
    let d = null;
    try { d = await inlineImage(new URL(u, location.href)); } catch (e) { d = null; }
    // A url that will not load is dropped rather than left pointing at nothing,
    // so the rule falls back to its colour instead of painting a broken image.
    const rep = d ? `url("${d}")` : 'none';
    css = css.split(`url(${u})`).join(rep)
             .split(`url("${u}")`).join(rep)
             .split(`url('${u}')`).join(rep);
  }
  return css;
}

const SHOT_BOX ='display:inline-flex;align-items:center;gap:6px;font:inherit;' +
                 'padding:5px 10px;border:1px solid #ffffff1f;border-radius:5px;' +
                 'background:#22242899;color:#e8e9ec;white-space:nowrap;';

/** Replace every native form control in a cloned subtree with plain markup
 *  that draws the same information. See the note at the call site. */
function flattenControls(root) {
  const sub = (old, node) => {
    for (const a of ['class', 'id', 'title']) {
      if (old.hasAttribute && old.hasAttribute(a)) {
        node.setAttribute(a, old.getAttribute(a));
      }
    }
    old.parentNode.replaceChild(node, old);
  };
  for (const b of [...root.querySelectorAll('button')]) {
    const s = document.createElement('span');
    s.setAttribute('style', SHOT_BOX +
      (b.classList.contains('primary') ? 'background:#6aa9ff;color:#06121f;' : '') +
      (b.disabled ? 'opacity:.45;' : ''));
    s.textContent = b.textContent;
    sub(b, s);
  }
  for (const sel of [...root.querySelectorAll('select')]) {
    const chosen = [...sel.options].find(o => o.hasAttribute('selected'))
                || sel.options[sel.selectedIndex] || sel.options[0];
    const s = document.createElement('span');
    s.setAttribute('style', SHOT_BOX + 'min-width:130px;');
    s.textContent = (chosen ? chosen.textContent : '') + '  ▾';
    sub(sel, s);
  }
  for (const i of [...root.querySelectorAll('input')]) {
    const t = (i.getAttribute('type') || 'text').toLowerCase();
    const s = document.createElement('span');
    if (t === 'checkbox' || t === 'radio') {
      s.setAttribute('style', 'font:inherit;color:#e8e9ec;');
      s.textContent = i.hasAttribute('checked') ? '☑' : '☐';
    } else if (t === 'range') {
      const lo = +(i.getAttribute('min') || 0), hi = +(i.getAttribute('max') || 100);
      const v = +(i.getAttribute('value') || lo);
      const pct = hi > lo ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) : 0;
      s.setAttribute('style',
        'display:inline-block;position:relative;vertical-align:middle;' +
        'width:150px;height:4px;border-radius:2px;background:#ffffff26;');
      const knob = document.createElement('span');
      knob.setAttribute('style',
        'position:absolute;top:-5px;left:' + (pct * 100).toFixed(1) + '%;' +
        'width:12px;height:12px;margin-left:-6px;border-radius:50%;' +
        'background:#6aa9ff;');
      s.appendChild(knob);
    } else {
      s.setAttribute('style', SHOT_BOX + 'min-width:120px;color:' +
        (i.getAttribute('value') ? '#e8e9ec' : '#8b8f98') + ';');
      s.textContent = i.getAttribute('value') ||
                      i.getAttribute('placeholder') || '';
    }
    sub(i, s);
  }
}

/** Depends only on the host page's `viewer`, `api()` and `toast()`. */
async function pageShot(name) {
  // `documentElement.clientWidth` reads 0 on these pages (html is height:100%
  // over a flex body), which silently produces a 0x0 canvas and a "data:," URL.
  const w = Math.ceil(window.innerWidth || document.body.clientWidth || 1280);
  const h = Math.ceil(window.innerHeight || document.body.clientHeight || 800);
  // **Every** stylesheet the page links, not just `style.css`.
  //
  // This used to fetch `/ui/style.css` and nothing else, which was right while
  // the only two pages were the browser and the builder. `play.html` links
  // three (`style.css`, `play.css`, `ui.css`), so two thirds of its layout was
  // missing from the picture -- the viewport photographed 300px wide because
  // `#play-main`'s grid was simply absent and `style.css`'s three-column `main`
  // rule won by default. Reading the document's own links means a page cannot
  // grow a stylesheet this file does not know about.
  const links = [...document.querySelectorAll('link[rel="stylesheet"]')]
    .map(l => l.getAttribute('href')).filter(Boolean);
  let css = (await Promise.all(links.map(async href => {
    try { return await (await fetch(href)).text(); } catch (e) { return ''; }
  }))).join('\n');
  css = await inlineCssUrls(css);
  const rootStyle = (await inlineCssUrls(
    document.documentElement.getAttribute('style') || ''))
    .replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  const glShot = viewer ? viewer.snapshot() : null;
  const clone = document.body.cloneNode(true);
  // `cloneNode` copies ATTRIBUTES, not form *properties*: a checkbox the user
  // ticked, a range dragged, a <select> changed by script all clone back to
  // their authored markup, so the picture shows the wrong UI state. Write the
  // live values onto the clone as attributes before it is serialised.
  {
    const live = [...document.body.querySelectorAll('input, select, textarea')];
    const copy = [...clone.querySelectorAll('input, select, textarea')];
    for (let i = 0; i < live.length && i < copy.length; i++) {
      const a = live[i], b = copy[i];
      if (a.tagName === 'SELECT') {
        [...b.options].forEach((o, k) => {
          if (k === a.selectedIndex) o.setAttribute('selected', 'selected');
          else o.removeAttribute('selected');
        });
      } else if (a.type === 'checkbox' || a.type === 'radio') {
        if (a.checked) b.setAttribute('checked', 'checked');
        else b.removeAttribute('checked');
      } else if (a.tagName === 'TEXTAREA') {
        b.textContent = a.value;
      } else {
        b.setAttribute('value', a.value);
      }
      if (a.disabled) b.setAttribute('disabled', 'disabled');
    }
  }
  // A live canvas cannot render inside an SVG, so every one is substituted by
  // its own frame.
  //
  // This used to handle `#gl` alone, which was right while a page had exactly
  // one viewport. `play.html` has two: the world, and Status tab 1's character
  // preview, which is a second `gl.js` Viewer over the same `/api/figure`
  // payload. The preview photographed as an empty black box -- the one part of
  // the panel a picture is for. Canvases are matched live-to-clone by document
  // order, and a page that knows how to snapshot one (a WebGL context without
  // `preserveDrawingBuffer` reads back blank unless it redraws first) says so
  // through the optional `window.canvasShot` hook.
  {
    const liveCanvases = [...document.body.querySelectorAll('canvas')];
    const cloneCanvases = [...clone.querySelectorAll('canvas')];
    for (let i = 0; i < liveCanvases.length && i < cloneCanvases.length; i++) {
      const live = liveCanvases[i];
      let data = null;
      try {
        data = (live.id === 'gl' && glShot) ? glShot
             : (typeof window.canvasShot === 'function' ? window.canvasShot(live) : null)
               || live.toDataURL('image/png');
      } catch (e) { data = null; }
      if (!data) continue;
      const img = document.createElement('img');
      img.setAttribute('src', data);
      // Explicit pixels, not 100%: the canvas backing store is CSS size x DPR,
      // and inside the SVG's layout a percentage does not constrain it, so the
      // frame is drawn at 2x and cropped. Copy the live canvas's CSS box.
      const w2 = Math.round(live.clientWidth);
      const h2 = Math.round(live.clientHeight);
      img.setAttribute('style',
        `display:block;width:${w2}px;height:${h2}px;object-fit:contain`);
      img.setAttribute('width', String(w2));
      img.setAttribute('height', String(h2));
      cloneCanvases[i].parentNode.replaceChild(img, cloneCanvases[i]);
    }
  }
  // **Chrome does not paint native form controls inside an SVG foreignObject
  // rasterised through an <img>.** The element's own background and border are
  // drawn and everything the widget contributes -- a button's label, a
  // <select>'s chosen option, a checkbox's tick, a slider's thumb -- is not.
  // The visible result is a control bar that photographs as an empty panel,
  // which is exactly the part of the interface a whole-page shot exists to
  // record. So every control is swapped for a plain element that draws the
  // same thing. This runs on the CLONE only; the live page is untouched.
  flattenControls(clone);

  // scripts have no business in a picture, and would not run anyway
  clone.querySelectorAll('script').forEach(s => s.remove());
  // foreignObject content is parsed as XML, and this file's section comments
  // are `<!-- ---- left ---- -->`: a double hyphen inside a comment is a hard
  // XML parse error, so the comments go.
  const walk = document.createTreeWalker(clone, NodeFilter.SHOW_COMMENT);
  const comments = [];
  while (walk.nextNode()) comments.push(walk.currentNode);
  for (const c of comments) c.remove();

  const seen = new Map();
  for (const img of clone.querySelectorAll('img')) {
    const src = img.getAttribute('src');
    if (!src || src.startsWith('data:')) continue;
    if (!seen.has(src)) seen.set(src, await inlineImage(new URL(src, location.href)));
    const d = seen.get(src);
    if (d) img.setAttribute('src', d); else img.removeAttribute('src');
  }

  const html = new XMLSerializer().serializeToString(clone);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">` +
    `<foreignObject width="100%" height="100%">` +
    // The skin lives as custom properties on `<html>` (`UI.applySkin`), and the
    // clone starts at `<body>` -- so without carrying the root's inline style
    // across, every `var(--co-*)` in the picture falls back to its default and
    // the shot shows a skin nobody is looking at.
    `<div xmlns="http://www.w3.org/1999/xhtml" ` +
    `style="width:${w}px;height:${h}px;background:#16171a;${rootStyle}">` +
    `<style>${css.replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</style>` +
    // `<body>` becomes a `<div>`, and with it every `body { ... }` rule stops
    // matching -- including `display:flex; flex-direction:column; height:100%`,
    // which is what bounds the page. Without it the three-column grid grows to
    // its tallest column (a 64-row model list is ~3,000 px), the viewport's
    // flex:1 grows with it, and the control bar under the stage is pushed
    // thousands of pixels off the bottom of the picture. Every whole-page shot
    // this project has ever saved is missing that bar for this reason. Put the
    // layout back explicitly.
    html.replace(/^<body/,
      `<div style="margin:0;display:flex;flex-direction:column;` +
      `height:${h}px;overflow:hidden"`).replace(/<\/body>$/, '</div>') +
    `</div></foreignObject></svg>`;

  const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  const png = await new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const g = c.getContext('2d');
      g.fillStyle = '#16171a'; g.fillRect(0, 0, w, h);
      g.drawImage(im, 0, 0);
      res(c.toDataURL('image/png'));
    };
    im.onerror = () => rej(new Error('the page could not be rasterised'));
    im.src = url;
  });
  const r = await api('/api/snapshot?name=' + encodeURIComponent(name || 'builder_page'),
                      { method: 'POST', body: png });
  toast('saved ' + r.file, 3000);
  return r.file;
}
window.pageShot = pageShot;
