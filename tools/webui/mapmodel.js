/* mapmodel.js -- the MapEditor's state and geometry, as pure functions.
 *
 * NOT ONE LINE OF THIS FILE TOUCHES THE DOM
 * -----------------------------------------
 * Same rule as `views.js`, for the same reason: `docs/viewer.md` and the task
 * brief both ask that state -> view stay separable from DOM manipulation,
 * because the client has to port off the browser eventually. So there is no
 * `document`, no `window`, no `fetch`, no `localStorage` and no `Image` below
 * this comment -- only arithmetic and descriptors. `mapedit.js` is the only
 * thing that turns them into pixels, and `tools/test_viewer.py::MapEditorUi`
 * greps for exactly that.
 *
 * THE CAMERA IS NOT A PARAMETER
 * -----------------------------
 * There is no camera state here at all, and that is the point. The painted
 * image *is* the game's view: `docs/map_scenery.md` §6 measures the locked
 * camera -- orthographic, yaw -45 degrees, pitch asin(1/2) -- and finds it
 * maps art pixels to screen pixels with a worst residual of 0.00 px over 121
 * cell centres. So drawing the painted image is drawing the game's camera, and
 * `zoom` below is a scale on that image and nothing else. A projection this
 * file cannot express is a projection the game does not use.
 *
 * ZOOM, AND WHY IT HAS TWO NUMBERS
 * --------------------------------
 * `state.zoom` is continuous -- display pixels per art pixel -- so the wheel
 * feels like a wheel. `tileLevel()` turns it into the integer decimation the
 * server can actually render (`mapedit.ZOOMS`), and the canvas scales the
 * result. `Gulf` is 34,944 x 22,400 art pixels; nothing may ever ask for that
 * at level 1, and `visibleTiles()` is what makes sure nothing does.
 *
 * The notch is 5% of the current zoom (`WHEEL`), the buttons spend five of
 * them (`BUTTON_NOTCHES`), and `wheelNotches()` is what turns a device's
 * `deltaY` into notches so the step is 5% per DETENT rather than 5% per
 * event. `zoomPercent()` formats the result; all four are pinned by
 * `tests/test_mapedit_zoom_step.py`, executed in a real V8.
 */

'use strict';

const MapModel = (() => {

  /** Output edge of one tile, in pixels. Must match `mapedit.TILE`. */
  const TILE = 256;
  /** Integer decimations the server renders. Must match `mapedit.ZOOMS`. */
  const ZOOMS = [1, 2, 4, 8, 16, 32, 64];
  /** Draw order, furthest first. Must match `mapedit.LAYERS`. */
  const LAYERS = ['background', 'ground', 'terrain', 'cover', 'interactive',
                  'passability'];

  const MIN_ZOOM = 1 / 64;
  const MAX_ZOOM = 4;
  /** One wheel notch, MULTIPLICATIVE: 5% of the CURRENT zoom, not 5
   *  percentage points.
   *
   *  The owner asked for "5% per scroll tick" and the two readings are not
   *  close here, because MIN_ZOOM..MAX_ZOOM spans 256x. Additive 5 points
   *  would be a 320% jump on the first notch out of MIN_ZOOM (1.56% -> 6.56%)
   *  and a 1.25% nudge near MAX_ZOOM (400% -> 405%) -- the same gesture doing
   *  wildly different things at the two ends. Multiplicative is constant in
   *  the only unit the eye has, ratio: every notch resizes what you are
   *  looking at by the same 5% wherever you are. It is also what the old
   *  constant was (1.25 = 25% per notch), so this is a change of ONE number
   *  and not a change of law.
   *
   *  Measured on the resulting ladder: 14.2 notches per doubling (was 3.1),
   *  113.7 notches MIN_ZOOM -> MAX_ZOOM (was 24.9). A free-spin wheel crosses
   *  that in one flick; a detented one takes deliberate scrolling, which is
   *  what was asked for. `BUTTON_NOTCHES` below is what keeps the coarse step
   *  available for the +/- controls. */
  const WHEEL = 1.05;
  /** What the +/- buttons and the +/- keys spend per press.
   *
   *  1.05^5 = 1.2763, within 2.1% of the 1.25 those controls stepped before
   *  this change, so a click keeps the feel it had. Only the WHEEL was asked
   *  to get finer -- a button that moved 5% would need 14 presses to double,
   *  which is a regression dressed up as consistency. */
  const BUTTON_NOTCHES = 5;

  const clampZoom = z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  /** One physical wheel detent, per `WheelEvent.deltaMode`:
   *  0 DOM_DELTA_PIXEL (Chromium emits 100 px per detent),
   *  1 DOM_DELTA_LINE  (Gecko emits 3 lines per detent),
   *  2 DOM_DELTA_PAGE. */
  const DETENT = [100, 3, 1];
  /** Cap per EVENT. Some drivers deliver one accumulated delta of several
   *  thousand after a flick; uncapped that is a teleport to a clamp. */
  const MAX_EVENT_NOTCHES = 4;

  /** A wheel event's delta as SIGNED notches, positive = zoom in.
   *
   *  Counting one notch per event -- what this did before -- does not deliver
   *  "5% per tick" on the hardware people have: a high-resolution wheel emits
   *  several events per physical detent, so the per-tick step was the constant
   *  raised to a device-dependent power and no value of WHEEL could fix it.
   *  Scaling by the delta makes the notch mean a detent on every device, and
   *  makes a trackpad's small deltas fractional notches rather than full ones.
   *  Fractional is fine: `zoomAt` is a `Math.pow`, not a table lookup.
   */
  function wheelNotches(deltaY, deltaMode) {
    const unit = DETENT[deltaMode | 0] || DETENT[0];
    const n = -deltaY / unit;
    return Math.min(MAX_EVENT_NOTCHES, Math.max(-MAX_EVENT_NOTCHES, n));
  }

  /** The zoom as a percentage STRING, for the HUD and the zoom label.
   *
   *  The decimals are banded because a fixed 0 of them cannot show a 5% step:
   *  measured over the whole 114-rung ladder, `toFixed(0)` repeats the same
   *  integer for up to TEN consecutive notches down at MIN_ZOOM (everything
   *  from 1.56% to 2.4% prints "2%"). A readout that does not move is read as
   *  a wheel that does not work, which is the bug this change is fixing.
   *
   *  Banding on the value keeps the quantum at or under 1% of the value in
   *  every band, and 5% > 1%, so every notch changes the string: 0 duplicate
   *  pairs over those 114 rungs, asserted in tests/test_mapedit_zoom_step.py.
   */
  function zoomPercent(zoom) {
    const p = zoom * 100;
    const d = p >= 100 ? 0 : p >= 10 ? 1 : 2;
    return p.toFixed(d);
  }

  /** The layer rows for a map, furthest first, as the server derived them.
   *
   *  THE LIST IS THE MAP'S, NOT THE CONSTANT'S. `LAYERS` above names the
   *  five layer KINDS; a map's actual layers are one row per backdrop PLANE
   *  (`background:0`, `background:1`, ... -- `star10` has sixteen) plus
   *  whichever of ground / terrain / cover / passability it has anything in.
   *  `mapedit.MapArt.layers()` measures that and omits the empty ones, so
   *  this is a passthrough with a shape guarantee rather than a second
   *  derivation: a rule implemented twice is a rule that drifts, and the
   *  renderer addresses these ids straight through to `render_background`'s
   *  plane index.
   *
   *  An older payload (no `layers`, or the pre-2026-09-14 `[{id,title}]`
   *  form) falls back to the five kinds, so a stale tab still works.
   */
  function mapLayers(info) {
    const rows = (info && info.layers) || [];
    const usable = rows.filter(r => r && r.id && typeof r.count === 'number');
    if (usable.length) return usable;
    return LAYERS.map(id => ({ id, kind: id, plane: null, title: id,
                               count: 0, detail: '', help: '' }));
  }

  /** The default draw order for a map: exactly the order `mapLayers` is in,
   *  which is the order `terrain.art_texture()` composites in. */
  function defaultOrder(info) {
    return mapLayers(info).map(r => r.id);

  }

  /** Fresh state for a map. `layers` defaults to everything the game draws;
   *  the passability grid is a data overlay, so it starts off.
   *
   *  `order` is the user's draw order, an array of layer ids. It exists
   *  because the owner wants to hand BACK a human-determined order -- the
   *  tool cannot tell them what the client's real order is, so it has to let
   *  them try one and read it off. `orderedLayers()` is the only thing that
   *  should ever be iterated to draw; `order` on its own can go stale
   *  against the map. */
  function create(info) {
    const rows = mapLayers(info);
    const layers = {};
    for (const r of rows) layers[r.id] = r.kind !== 'passability';
    return {
      name: info ? info.name : '',
      px: info && info.pixels ? info.pixels.slice() : [0, 0],
      cells: info && info.mapSize ? info.mapSize.slice() : [0, 0],
      zoom: 1 / 8,
      ox: 0, oy: 0,
      layers,
      order: rows.map(r => r.id),
      t: 0,
      selection: null,
    };
  }

  // -- the layer order ----------------------------------------------------

  /**
   * The map's layer rows in the state's order, furthest first.
   *
   * RECONCILED, NOT TRUSTED. `state.order` is user data and the map under it
   * can change (open a different map, restage art, reload). Ids the map no
   * longer has are dropped and ids the state has never seen are appended in
   * their default position, so a panel rebuilt from this can never lose a
   * layer or show one that does not exist -- which is what "reordering
   * survives a re-render" actually requires.
   */
  function orderedLayers(state, info) {
    const rows = mapLayers(info);
    const by = new Map(rows.map(r => [r.id, r]));
    const out = [];
    const seen = new Set();
    for (const id of (state && state.order) || []) {
      if (by.has(id) && !seen.has(id)) { out.push(by.get(id)); seen.add(id); }
    }
    for (let i = 0; i < rows.length; i++) {
      if (seen.has(rows[i].id)) continue;
      // Back where it belongs by default, not at the end: a layer that
      // appears after a restage must not land on top of everything.
      out.splice(Math.min(i, out.length), 0, rows[i]);
      seen.add(rows[i].id);
    }
    return out;
  }

  /** The ids of the layers that are BOTH present and switched on, in order.
   *  This is what the renderer draws, and nothing else may decide it. */
  function activeLayers(state, info) {
    return orderedLayers(state, info)
      .filter(r => state && state.layers[r.id] !== false)
      .map(r => r.id);
  }

  /** Move one layer `delta` places (−1 = one step further back). Returns a
   *  new state; returns the SAME state when the move would fall off either
   *  end, so a caller can tell "nothing happened" from "moved". */
  function moveLayer(state, info, id, delta) {
    const ids = orderedLayers(state, info).map(r => r.id);
    const i = ids.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return state;
    ids.splice(j, 0, ids.splice(i, 1)[0]);
    return Object.assign({}, state, { order: ids });
  }

  /** Back to the composite order the game draws in. */
  function resetOrder(state, info) {
    return Object.assign({}, state, { order: defaultOrder(info) });
  }

  /**
   * The current order as one line of text the owner can copy back to us.
   *
   * THIS IS THE DELIVERABLE, not a debug aid. The reordering exists so a
   * human can arrive at a draw order by eye and tell us what it was; an
   * order that only lives in a panel's DOM cannot be told to anyone. Off
   * layers are marked rather than dropped, because "cover was off" is part
   * of what the eye was judging.
   */
  function orderText(state, info) {
    const rows = orderedLayers(state, info);
    if (!rows.length) return '';
    const on = rows.map(r => (state && state.layers[r.id] === false)
                             ? '(' + r.id + ')' : r.id);
    return `${(state && state.name) || ''}: ${on.join(' -> ')}`;
  }

  /** The integer decimation to ask the server for at this zoom.
   *
   *  The coarsest level whose native size is still at or above the displayed
   *  size, so a tile is never magnified by more than one step and never
   *  fetched at a detail the screen cannot show.
   */
  function tileLevel(zoom) {
    const want = 1 / Math.max(zoom, 1e-9);
    for (const z of ZOOMS) if (z >= want) return z;
    return ZOOMS[ZOOMS.length - 1];
  }

  /** The zoom that fits a whole map in a viewport, with a little margin. */
  function fitZoom(px, viewW, viewH) {
    if (!px || !px[0] || !px[1] || viewW <= 0 || viewH <= 0) return 1 / 8;
    return clampZoom(Math.min(viewW / px[0], viewH / px[1]) * 0.96);
  }

  /** Centre the whole map in the viewport. The "fit map" button, as data. */
  function fit(state, viewW, viewH) {
    const zoom = fitZoom(state.px, viewW, viewH);
    return Object.assign({}, state, {
      zoom,
      ox: state.px[0] / 2 - viewW / (2 * zoom),
      oy: state.px[1] / 2 - viewH / (2 * zoom),
    });
  }

  /** Put an art point in the middle of the viewport, keeping the zoom. */
  function centreOn(state, px, py, viewW, viewH) {
    return Object.assign({}, state, {
      ox: px - viewW / (2 * state.zoom),
      oy: py - viewH / (2 * state.zoom),
    });
  }

  // -- the transform ------------------------------------------------------

  const artToScreen = (state, px, py) =>
    [(px - state.ox) * state.zoom, (py - state.oy) * state.zoom];

  const screenToArt = (state, sx, sy) =>
    [state.ox + sx / state.zoom, state.oy + sy / state.zoom];

  /** Zoom about a screen point, so the art under the cursor stays under it.
   *  `steps` is signed wheel notches and MAY BE FRACTIONAL -- `wheelNotches`
   *  returns a fraction for a trackpad or a high-resolution wheel. */
  function zoomAt(state, steps, sx, sy) {
    const zoom = clampZoom(state.zoom * Math.pow(WHEEL, steps));
    if (zoom === state.zoom) return state;
    const [ax, ay] = screenToArt(state, sx, sy);
    return Object.assign({}, state, { zoom, ox: ax - sx / zoom, oy: ay - sy / zoom });
  }

  function panBy(state, dxScreen, dyScreen) {
    return Object.assign({}, state, {
      ox: state.ox - dxScreen / state.zoom,
      oy: state.oy - dyScreen / state.zoom,
    });
  }

  /** Keep at least a corner of the map on screen. Without this a fast drag
   *  loses the map entirely and the only way back is the reset button. */
  function clamp(state, viewW, viewH) {
    const marginX = viewW / state.zoom * 0.75;
    const marginY = viewH / state.zoom * 0.75;
    return Object.assign({}, state, {
      ox: Math.min(Math.max(state.ox, -marginX), state.px[0] + marginX - viewW / state.zoom),
      oy: Math.min(Math.max(state.oy, -marginY), state.px[1] + marginY - viewH / state.zoom),
    });
  }

  // -- what to fetch and where to put it ----------------------------------

  /**
   * Every tile that has to be on screen, in draw order.
   *
   * Returned as descriptors -- `{layer, z, tx, ty, x, y, w, h, key}` -- so the
   * renderer is a loop over data rather than a nest of arithmetic, and the
   * count of them is a thing a test can assert.
   */
  function visibleTiles(state, viewW, viewH, layers) {
    const z = tileLevel(state.zoom);
    const span = TILE * z;                       // art pixels per tile
    const size = span * state.zoom;              // display pixels per tile
    // THE STATE'S ORDER, not `LAYERS`. The PNG fallback path draws these in
    // the order they come back in, so a reorder in the panel has to reach it
    // here as well or the two renderers show different pictures for the same
    // panel. `state.order` holds per-plane ids (`background:3`), which
    // `/api/mapedit/tile` now accepts.
    const wanted = layers
      || (state.order ? state.order.filter(l => state.layers[l] !== false)
                      : LAYERS.filter(l => state.layers[l]));
    const tx0 = Math.max(0, Math.floor(state.ox / span));
    const ty0 = Math.max(0, Math.floor(state.oy / span));
    const tx1 = Math.floor((state.ox + viewW / state.zoom) / span);
    const ty1 = Math.floor((state.oy + viewH / state.zoom) / span);
    const nx = Math.ceil(state.px[0] / span);
    const ny = Math.ceil(state.px[1] / span);
    const out = [];
    for (const layer of wanted) {
      for (let ty = ty0; ty <= Math.min(ty1, ny - 1); ty++) {
        for (let tx = tx0; tx <= Math.min(tx1, nx - 1); tx++) {
          const [x, y] = artToScreen(state, tx * span, ty * span);
          out.push({
            layer, z, tx, ty, x, y, w: size, h: size,
            key: `${layer}|${z}|${tx}|${ty}`,
            // Only the animated layers carry the clock, so a static map's
            // tiles are cacheable forever.
            t: (layer === 'terrain' || layer === 'cover') ? state.t : 0,
          });
        }
      }
    }
    return out;
  }

  /** The query string for one tile descriptor. A string, not a URL object,
   *  because a native port will not have `URL`. */
  function tileQuery(name, tile) {
    return `name=${encodeURIComponent(name)}&layer=${tile.layer}` +
           `&z=${tile.z}&tx=${tile.tx}&ty=${tile.ty}` + (tile.t ? `&t=${tile.t}` : '');
  }

  // -- selection ----------------------------------------------------------

  /**
   * The outline to draw around whatever is selected, in **art pixels**.
   *
   * A puzzle cell is a diamond, because a map cell is a 64x32 diamond on the
   * painted image (`docs/ground_art.md` §1). A sprite is its own rectangle.
   * Both come back as polygons so the renderer has one code path.
   */
  function outlines(pick, info) {
    if (!pick) return [];
    const out = [];
    if (pick.kind === 'puzzle') {
      const [x0, y0, x1, y1] = pick.tileRect;
      out.push({ role: 'tile', points: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]] });
      if (pick.cell && pick.cell.inside && info) {
        out.push({ role: 'cell', points: diamond(info, pick.mapCell[0], pick.mapCell[1]) });
      }
    } else if (pick.kind === 'terrain' || pick.kind === 'cover') {
      const [ox, oy] = pick.spriteOrigin;
      const [w, h] = pick.spriteSize;
      out.push({ role: 'sprite', points: [[ox, oy], [ox + w, oy], [ox + w, oy + h], [ox, oy + h]] });
      const [cx0, cy0, cx1, cy1] = pick.cellBounds;
      for (let cy = cy0; cy <= cy1; cy++) {
        for (let cx = cx0; cx <= cx1; cx++) {
          out.push({ role: 'footprint', points: diamond(info, cx, cy) });
        }
      }
    }
    return out;
  }

  /** The four corners of map cell (x, y) on the painted image.
   *
   *  `px = (gx - gy + K) * 32`, `py = (gx + gy - K) * 16` -- the placement
   *  rule, VERIFIED on 131 of 132 maps (`docs/ground_art.md` §1). Repeated
   *  here rather than fetched because it is two lines and a round trip per
   *  click would be absurd; `MapEditorUi` asserts the constants match
   *  `tools/puzzle.py`.
   */
  function corner(info, gx, gy) {
    const K = info.originCells;
    return [(gx - gy + K) * 32, (gx + gy - K) * 16];
  }

  function diamond(info, cx, cy) {
    return [corner(info, cx, cy), corner(info, cx + 1, cy),
            corner(info, cx + 1, cy + 1), corner(info, cx, cy + 1)];
  }

  // -- the inspector ------------------------------------------------------

  const row = (k, v, extra) => Object.assign({ k, v }, extra || {});

  /**
   * The inspector, as sections of labelled rows.
   *
   * Every branch leads with the integrity verdict, because the brief asks for
   * it *always* rather than only when something is checked -- "this is free"
   * is as much a fact as "this is not".
   */
  function inspector(pick, info) {
    if (!pick || pick.kind === 'none') {
      return [{ title: 'Nothing selected',
                rows: [row('', pick && pick.why ? pick.why :
                           'Click a puzzle tile, a TERRAIN object or a COVER sprite.')] }];
    }
    const secs = [verdict(pick)];
    if (pick.kind === 'puzzle') secs.push(...puzzleSections(pick, info));
    else secs.push(...spriteSections(pick));
    return secs;
  }

  function verdict(pick) {
    const e = pick.editable || {};
    const i = pick.integrity || {};
    return {
      title: 'May I change this?',
      badge: i.checked ? 'checked' : 'free',
      rows: [
        row('Verdict', e.summary || ''),
        row('Placement lives in', e.source || '', { mono: true,
            note: i.checked ? 'integrity-checked' : 'not in integrity.json' }),
        row('Art files', (e.art || []).join('\n') || '(none)', { mono: true }),
        row('integrity.json', i.manifestRows
            ? `${i.manifestRows} entries naming ${i.manifestFiles} files ` +
              '— 136 .DMap and 7 ini JSONs, nothing else'
            : 'not read'),
      ],
    };
  }

  function puzzleSections(pick, info) {
    const c = pick.cell || {};
    const secs = [{
      title: 'Puzzle tile',
      rows: [
        row('Tile slot', `${pick.tileSlot[0]}, ${pick.tileSlot[1]}` +
                         (info ? ` of ${info.pul[0]}x${info.pul[1]}` : '')),
        row('.ani key', pick.tileKey || '(empty slot)', { mono: true }),
        row('Texture', pick.texture || '(no tile painted here)', { mono: true,
            asset: pick.texture || '' }),
        row('.ani file', pick.ani, { mono: true }),
        row('Placed by', pick.source, { mono: true }),
        row('Tile pixels', `${pick.tileRect[0]},${pick.tileRect[1]} .. ` +
                           `${pick.tileRect[2]},${pick.tileRect[3]}  ` +
                           `(${pick.gridSize}px grid)`),
        row('Clicked pixel', `${pick.px[0]}, ${pick.px[1]}`),
      ],
    }];
    secs.push({
      title: 'Map cell under the cursor',
      rows: c.inside ? [
        row('Cell', `${c.x}, ${c.y}`),
        row('Passable', c.walkable ? 'yes' : 'no',
            { note: c.fromScenery
                ? 'and the base grid disagrees — a TERRAIN layer decides this cell'
                : 'from the .DMap cell grid' }),
        row('Mask / surface / elevation', `${c.mask} / ${c.surface} / ${c.elevation}`),
        row('Lives in', pick.integrityDmap || 'map/map/*.DMap', { mono: true,
            note: 'integrity-checked — editing passability changes a verified file' }),
      ] : [row('', 'this pixel is outside the cell grid')],
    });
    return secs;
  }

  function spriteSections(pick) {
    const rows = [
      row('Kind', pick.kind === 'terrain'
          ? 'TERRAIN — a map/Scene object, drawn on the ground'
          : 'COVER — one sprite, drawn in front of the player'),
      row('.ani file', pick.ani, { mono: true }),
      row('.ani key', pick.aniKey, { mono: true }),
      row('Frames', (pick.frames || []).join('\n') || '(none resolved)',
          { mono: true, asset: (pick.frames || [])[0] || '' }),
      row('Frame count', String(pick.frameCount) +
          (pick.animated ? ` — animated, every ${pick.frameInterval} ms`
                         : ' — static')),
      row('Anchor cell', `${pick.anchorCell[0]}, ${pick.anchorCell[1]}`),
      row('Cell footprint', `${pick.cellSize[0]} x ${pick.cellSize[1]} cells, ` +
          `[${pick.cellBounds[0]}..${pick.cellBounds[2]}] x ` +
          `[${pick.cellBounds[1]}..${pick.cellBounds[3]}]`,
          { note: 'the array runs backwards from the anchor — docs/map_scenery.md §2' }),
      row('Pixel offset', `${pick.pixelOffset[0]}, ${pick.pixelOffset[1]}`),
      row('Sprite origin', `${pick.spriteOrigin[0]}, ${pick.spriteOrigin[1]} ` +
          `(${pick.spriteSize[0]} x ${pick.spriteSize[1]} px)`),
      row('Layer index', String(pick.layerIndex)),
    ];
    if (pick.kind === 'terrain') {
      rows.push(row('Passability', `opens ${pick.opensCells} cells, ` +
                                   `blocks ${pick.blocksCells}`,
                    { note: 'a TERRAIN layer replaces the grid underneath it' }));
      rows.push(row('Thickness', String(pick.thickness)));
      rows.push(row('Offset elevation', pick.elevationUsable
        ? String(pick.offsetElevation)
        : `${pick.offsetElevation} — uninitialised, read and never applied`));
      rows.push(row('Compiled from', pick.scenePath, { mono: true }));
      if (pick.partSource) {
        rows.push(row('Editable source', pick.partSource,
                      { mono: true, note: 'plain CRLF text — the easiest way in' }));
      }
    } else {
      rows.push(row('Passability', 'none — a COVER is art only'));
      rows.push(row('Placed by', pick.source, { mono: true }));
    }
    return [{ title: pick.kind === 'terrain' ? 'TERRAIN scene part' : 'COVER sprite',
              rows }];
  }

  // -- the map picker -----------------------------------------------------

  const STATE_NOTE = {
    ok: '',
    pux: 'ground art is map/PuzzleSave/*.pux (TqTerrain), undecoded',
    mismatch: 'the art and the .DMap disagree about the map size',
    noart: 'no usable .pul',
    // Spelling-neutral, like the server's own message. The registry is
    // GameMap.json on the community client and the binary GameMap.dat on
    // every official one -- no install ships both -- so naming one sends the
    // reader of the other to a file they do not have. `mapedit.py` was
    // corrected and this copy, which is what the user actually reads, was
    // not: the second reader of the same fact.
    missing: 'the map registry names it but no .DMap ships',
    broken: 'the .DMap did not parse',
    stale: 'this .DMap is not what the .7z beside it holds -- a previous '
         + "client's map",
  };

  /** The picker list, filtered and annotated. Sorting is the server's (largest
   *  first); this only decides what is shown and how it reads. */
  function pickerRows(rows, query) {
    const q = (query || '').trim().toLowerCase();
    return rows
      .filter(r => !q || r.name.toLowerCase().includes(q) ||
                   String(r.documentId || '').includes(q))
      .map(r => ({
        name: r.name,
        documentId: r.documentId,
        size: r.width ? `${r.width} x ${r.height}` : '—',
        area: r.area,
        state: r.state,
        // `stale` draws: the file is a valid map and the user can see it in
        // their own folder, so refusing would look like a bug. The note is
        // what carries the warning.
        //
        // `archive` DRAWS TOO, and leaving it out greyed every map on the
        // newest client. A `.DMap` shipping only inside a per-map `.7z` is
        // the NORM from 5517 on -- `mapedit._row` says so in its own comment
        // -- and those rows open, parse and render like any other. Measured:
        // 7878 is 730 of 730 `archive`, so ALL 730 were presented as
        // unusable while the editor drew every one of them; 6090 greyed 48,
        // 5517 greyed 11. The state is NEWER THAN THE CONDITION, which is why
        // this read as fine when it was written and is wrong now.
        // Pinned by `tests/test_mapedit_picker_drawable.py`.
        drawable: r.state === 'ok' || r.state === 'mismatch'
                  || r.state === 'stale' || r.state === 'archive',
        note: r.why || STATE_NOTE[r.state] || '',
      }));
  }

  return { TILE, ZOOMS, LAYERS, MIN_ZOOM, MAX_ZOOM, WHEEL, BUTTON_NOTCHES,
           mapLayers, defaultOrder, orderedLayers, activeLayers,
           moveLayer, resetOrder, orderText,
           create, tileLevel, fitZoom, fit, centreOn, wheelNotches, zoomPercent,
           artToScreen, screenToArt, zoomAt, panBy, clamp,
           visibleTiles, tileQuery, outlines, corner, diamond,
           inspector, pickerRows };
})();

if (typeof window !== 'undefined') window.MapModel = MapModel;
if (typeof module !== 'undefined' && module.exports) module.exports = MapModel;
