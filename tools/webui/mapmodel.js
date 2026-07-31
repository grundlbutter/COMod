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
 */

'use strict';

const MapModel = (() => {

  /** Output edge of one tile, in pixels. Must match `mapedit.TILE`. */
  const TILE = 256;
  /** Integer decimations the server renders. Must match `mapedit.ZOOMS`. */
  const ZOOMS = [1, 2, 4, 8, 16, 32, 64];
  /** Draw order, furthest first. Must match `mapedit.LAYERS`. */
  const LAYERS = ['background', 'ground', 'terrain', 'cover', 'passability'];

  const MIN_ZOOM = 1 / 64;
  const MAX_ZOOM = 4;
  /** One wheel notch. 1.25 gives ~3 notches per doubling, which reads as
   *  smooth without needing many intermediate tile levels. */
  const WHEEL = 1.25;

  const clampZoom = z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  /** Fresh state for a map. `layers` defaults to everything the game draws;
   *  the passability grid is a data overlay, so it starts off. */
  function create(info) {
    return {
      name: info ? info.name : '',
      px: info && info.pixels ? info.pixels.slice() : [0, 0],
      cells: info && info.mapSize ? info.mapSize.slice() : [0, 0],
      zoom: 1 / 8,
      ox: 0, oy: 0,
      layers: { background: true, ground: true, terrain: true, cover: true,
                passability: false },
      t: 0,
      selection: null,
    };
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
   *  `steps` is signed wheel notches. */
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
    const wanted = layers || LAYERS.filter(l => state.layers[l]);
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
    missing: 'GameMap.json names it but no .DMap ships',
    broken: 'the .DMap did not parse',
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
        drawable: r.state === 'ok' || r.state === 'mismatch',
        note: r.why || STATE_NOTE[r.state] || '',
      }));
  }

  return { TILE, ZOOMS, LAYERS, MIN_ZOOM, MAX_ZOOM,
           create, tileLevel, fitZoom, fit, centreOn,
           artToScreen, screenToArt, zoomAt, panBy, clamp,
           visibleTiles, tileQuery, outlines, corner, diamond,
           inspector, pickerRows };
})();

if (typeof window !== 'undefined') window.MapModel = MapModel;
if (typeof module !== 'undefined' && module.exports) module.exports = MapModel;
