/* mapfx.js -- the map's ANIMATED effects, drawn on the Map Editor page.
 *
 * The `.DMap`'s v1006 LATE list places 10,172 animated effects across 115
 * maps on 7878. `/api/mapedit/effects?map=<n>` joins each placement to its
 * definition; this module turns that into GL and draws it over the baked
 * ground, on the page's own `#glcanvas`.
 *
 * WHY THIS IS NOT `gl.js`'s DRAW PATH
 * -----------------------------------
 * `Viewer._drawEffects` is welded to gl.js's shader uniforms (`uMode`,
 * `uAlphaMode`, `uModel`, `uMVP`) and to a 3D camera. The Map Editor has
 * neither -- it runs a raw `tilebake.Baker` on a bare context and composites
 * in PAINTED-IMAGE PIXELS. So the pass below is its own program.
 *
 * **What it does NOT duplicate is the part that was hard.** `fx.js` is
 * shader-agnostic: `EffectInstance` builds the buffers, `tick()` runs the
 * clock, `FX.samplePart` / `FX.motionMatrix` / `FX.particleQuads` are the
 * algorithm out of docs/effects.md 8. All of that is reused verbatim. This
 * file is a projection and a blend state.
 *
 * THE PROJECTION, AND WHICH HALF OF IT IS MEASURED
 * -------------------------------------------------
 * An effect's `origin` IS a painted-image pixel -- that is the whole finding
 * behind `mapfx.origin_to_cell`, so a placement needs no camera at all, only
 * the page's existing pan/zoom rect.
 *
 * Its GEOMETRY is in world units, and those reach image pixels through an
 * affine this repo already owns twice over. `tilebake.js`'s world vertex
 * shader states image->world as
 *
 *     world.x = CELL * (px/64 + py/32)
 *     world.y = CELL * (px/64 - py/32) - CELL*K
 *
 * and inverting the linear part gives, for a DISPLACEMENT from the origin:
 *
 *     dpx = (dwx + dwy) * 32/CELL
 *     dpy = (dwx - dwy) * 16/CELL   -   dwz * KZ
 *
 * **`KX` AND `KY` ARE DERIVED, NOT FITTED.** One cell is `terrain.CELL` = 64
 * world units, and one cell projects to (32, 16) image pixels -- the affine
 * traced out of the client at RVA `0x86A8E2`. Both halves were established
 * separately, for other reasons, before this file existed.
 *
 * **`KZ` IS INFERRED AND NOTHING HERE MEASURES IT.** The height axis is an
 * independent camera parameter: this projection is NOT an orthographic view
 * of an orthonormal frame (solve it and `R22^2` comes out NEGATIVE -- a 2:1
 * game "isometric" is a squashed projection, not an axonometric one), so the
 * ground affine cannot determine it.
 *
 * I tried to bound it from `ini/C3DMapEffect.lua`'s cull cylinder and THE
 * INSTRUMENT FAILED, which is worth more than a number: the premise was "a
 * cull margin covers its effect", and at the DERIVED horizontal scale 21 of
 * 128 named effects violate it, the worst by 13x. `r`/`dz` are loosely
 * hand-set margins that accept pop-in, so they bound nothing -- and the tell
 * was that the violation count simply scaled with the candidate (12 / 21 / 43
 * at k = 0.25 / 0.5 / 1.0), never showing a minimum. A relation with no
 * minimum cannot pick a value.
 *
 * So `KZ` is set to `KX` -- one world unit of height reads at the same
 * pixels-per-unit as one world unit across -- and it is ONE CONSTANT, here,
 * so that when someone traces the real camera there is a single line to
 * change. **Its falsifier: effects would be systematically too tall or too
 * short against the map art they decorate, in a fixed ratio, on every map.**
 * That is a real observation, and it is not the one the page can make for
 * you by looking plausible.
 *
 * THE BILLBOARD BASIS
 * -------------------
 * `FX.particleQuads` wants a camera right/up in world space. Ours is a fixed
 * projection, so they are constants -- but they are NOT both unit vectors.
 * A billboard should be a SCREEN-space rectangle, so the two basis vectors
 * must project to the SAME pixel length, and world-unit vectors do not:
 * `(1,1,0)/sqrt(2)` projects to 0.707 px/unit while `(0,0,1)` projects to
 * `KZ` = 0.5. `RIGHT` is therefore scaled so both project to `KX` per unit of
 * particle half-size, which also puts particles on the same scale as every
 * other pixel on the page.
 */

'use strict';

const MapFx = (() => {
  /** World units per cell. `tools/terrain.py` CELL. */
  const CELL = 64.0;
  /** Image px per world unit, x component. DERIVED -- see the header. */
  const KX = 32.0 / CELL;         // 0.5
  /** Image px per world unit, y component. DERIVED. */
  const KY = 16.0 / CELL;         // 0.25
  /** Image px per world unit of HEIGHT. **INFERRED** -- see the header.
   *  This is the one line to change when the real camera is traced. */
  const KZ = 32.0 / CELL;         // 0.5

  /** Painted-image pixels per unit of the definition's `offset[1]`.
   *
   *  **THE AXIS IS DERIVED; THIS NUMBER IS FITTED, AND THEY ARE NOT THE SAME
   *  KIND OF CLAIM.** That `offset[1]` is a HEIGHT rather than a ground axis
   *  is carried by two independent measurements (`liftOffset`). That one
   *  unit of it is one PIXEL is carried by a single observation: on
   *  `zsjx03_new` the `mj_hhdc_19b` flame lands in its brazier at 1.0 and
   *  sits about half a brazier low at `KZ` (0.5), which is the scale the
   *  effect's own GEOMETRY uses and which `mj_hhdc_19` confirms exactly.
   *
   *  So the offset and the geometry appear to be in different units, which
   *  is odd enough that it deserves suspicion rather than a footnote. I
   *  could not find a second case clear enough to check it against -- the
   *  obvious candidate (`denglong2`, a lantern with `offset[1] = -325`) sits
   *  on a map with no visible pole to judge it by.
   *
   *  **Falsifier:** if this is wrong, every effect carrying a non-zero
   *  `offset[1]` -- 108 of 325 definitions -- is vertically off by a
   *  CONSTANT RATIO, and the error grows with the offset. A lamp on a tall
   *  pole is the case to look at; 2x out on a -325 lantern is 160 px and
   *  impossible to miss. `mj_hhdc_19` and every effect with no offset are
   *  unaffected either way, which is why the map still reads correctly
   *  overall.
   */
  const OFFSET_Y_PX_PER_UNIT = 1.0;

  /** Camera right in world space, scaled so it projects to `KX` px per unit
   *  -- the same as `UP` does, which is what makes a billboard square. */
  const RIGHT = [0.5, 0.5, 0.0];
  /** Camera up in world space. Projects to (0, -KZ) px, i.e. straight up. */
  const UP = [0.0, 0.0, 1.0];

  /** Placements past this are not built. A map is 93 at the worst we have
   *  (gsjx03_new) so nothing real is near it; it exists so that a map we
   *  have not seen cannot wedge the page building thousands of instances. */
  const MAX_INSTANCES = 400;

  const VS = `
    attribute vec3 aPos;      // effect-local WORLD units
    attribute vec2 aUV;
    uniform mat4 uModel;      // the part's motion matrix for this frame
    uniform vec2 uOrigin;     // the placement, in painted-image pixels
    uniform vec2 uUVOffset;   // ChangeTex atlas cell, as a UV offset
    uniform vec4 uRect;       // x0, y0, 1/w, 1/h of the visible art rect
    uniform float uFlipY;     // -1 straight onto the canvas (tilebake's rule)
    uniform vec3 uK;          // KX, KY, KZ
    varying vec2 vUV;
    varying vec2 vRaw;
    void main() {
      vec4 w = uModel * vec4(aPos, 1.0);
      vec2 px = uOrigin + vec2((w.x + w.y) * uK.x,
                               (w.x - w.y) * uK.y - w.z * uK.z);
      vec2 t = (px - uRect.xy) * uRect.zw;
      gl_Position = vec4(t.x * 2.0 - 1.0, (t.y * 2.0 - 1.0) * uFlipY, 0.0, 1.0);
      vUV = aUV + uUVOffset;
      vRaw = aUV;               // pre-offset, for the glow's radial coord
    }`;

  // Straight alpha out, exactly as gl.js's does: `c.a *= uMeshAlpha` and a
  // discard below the same 0.004. The effect's own authored ASB/ADB does the
  // compositing, so premultiplying here would double-apply it on every
  // additive layer -- which is most of them.
  const FS = `
    precision mediump float;
    uniform sampler2D uTex;
    uniform float uHasTex;
    uniform float uMeshAlpha;
    uniform float uGlow;      // 1 = draw the untextured GLOW FALLBACK
    uniform float uGrid;      // atlas / uvGrid, to find the cell-local coord
    uniform vec3  uGlowTint;
    varying vec2 vUV;
    varying vec2 vRaw;
    void main() {
      vec4 c = vec4(1.0);
      if (uHasTex > 0.5) {
        c = texture2D(uTex, vUV);
      } else if (uGlow > 0.5) {
        // A SOFT CIRCLE INSTEAD OF THE QUAD'S OWN RECTANGLE.
        // vRaw runs 0..1 across one atlas cell, so fract(vRaw * uGrid) is
        // the cell-local coordinate whatever the flipbook size is.
        // (No backticks in here: this whole shader is a template literal
        //  and one of those ends it -- the parse gate caught exactly that.)
        vec2 q = fract(vRaw * max(uGrid, 1.0)) * 2.0 - 1.0;
        float r = clamp(1.0 - length(q), 0.0, 1.0);
        float f = r * r;                     // quadratic: no hard edge
        // THE FALLOFF SCALES THE COLOUR, NOT ONLY THE ALPHA. These layers
        // carry their own authored blend, and 13 of the map-effect pairs are
        // ONE/ONE -- pure additive, where the fragment's alpha is IGNORED.
        // Putting the falloff only in the ALPHA there produced a uniform tint with
        // a circular cutout from the discard below: the right silhouette and
        // no gradient at all, which the gate caught as 0% of lit pixels
        // below half the peak.
        c = vec4(uGlowTint * f, f);
      } else {
        c = vec4(1.0);
      }
      c.a *= uMeshAlpha;
      if (c.a < 0.004) discard;
      gl_FragColor = c;
    }`;

  let gl = null;
  let prog = null;
  const uni = {};
  const attr = {};
  let white = null;

  /** texture path -> WebGLTexture. Shared across placements, so a map that
   *  drops `cloudmist1` sixteen times uploads one texture. */
  const textures = new Map();
  /** effect name -> the /api/effect payload. Same sharing, one level up. */
  const scenes = new Map();
  /** Textures that had to fall back to CLAMP because they are not
   *  power-of-two. Those cannot wrap, so a mesh addressing outside 0..1 still
   *  samples an edge texel there -- reported rather than silently accepted. */
  let npot = 0;
  /** One entry per PLACEMENT: { name, px, inst }, `px` in PAINTED-IMAGE
   *  pixels -- which is NOT what the record stores. See `imagePx`. */
  let placed = [];
  /** The painted image's origin constant, `pm.k`, off the effects payload. */
  let originCells = 0;

  let loadedMap = '';
  let loading = null;
  let enabled = true;
  /** TEST MODE: draw a soft circle where a layer has no texture.
   *
   *  **THE SHAPE IS PLAUSIBLE; THE COLOUR IS INVENTED, AND NOTHING IN THE
   *  DATA SUPPLIES ONE.** Searched, for the layers that need it:
   *
   *    per-layer EFFE fields   scale / asb / adb / zbuffer / billboard -- no colour
   *    PHY vertex colours      0 of them on every untextured part checked
   *    particle frame `c`      the flipbook ATLAS CELL index, not a colour
   *    `.OtherData` sidecar    NO per-effect section. 113 of 115 maps carry
   *                            one whose object count matches the late tag-4
   *                            COVER list (the known binding, and the control
   *                            that shows the test works); only 4 match the
   *                            EFFECT count and all four are sections already
   *                            bound to something else with a coincidental
   *                            size.
   *
   *  What the data DOES carry is `colorEnable`, true on every one of these,
   *  and alpha -- `systemAlpha` per frame and a per-particle life envelope.
   *  So a glow can be SHAPED by the effect's own alpha, and tinted only by
   *  us. That is why this is off by default and labelled a test: switching
   *  it on paints something onto the map that the client did not specify.
   */
  let glowOn = false;
  let glowTint = [1.0, 0.85, 0.55];
  //: `built` + `overCap` + `noScene` accounts for every RESOLVED placement.
  //: It did not at first: `2020tsf_new` reported `built 376 of 392` with
  //: `skipped: 0`, and the missing 16 were simply gone -- their effect name
  //: had not loaded, and nothing counted that. A number that does not add up
  //: invites the reader to assume a cause, and the two causes here (a cap I
  //: chose, a fetch that failed) deserve very different reactions.
  //: `ready` distinguishes "this map has no animated effects" from "the
  //: effects have not arrived yet", and the page needs that distinction to
  //: label its toggle. Both states are `count: 0`, and 290 of 7878's 470 maps
  //: are permanently in the first one -- so a page that cannot tell them
  //: apart either flashes "none" on every load or never says it at all.
  const blank = () => ({ map: '', count: 0, resolved: 0, built: 0, drawn: 0,
                         overCap: 0, noScene: 0, badNames: [], why: '',
                         names: 0, untexturedLayers: 0, ready: false });
  let report = blank();

  function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(s) || 'shader compile failed');
    return s;
  }

  /** Compile the program and make a 1x1 white fallback texture.
   *
   *  The white texture is not decoration: a layer whose texture has not
   *  arrived yet would otherwise sample an UNBOUND unit, which reads as
   *  (0,0,0,1) -- black quads over the map, indistinguishable from an
   *  authored black effect. */
  function init(context) {
    if (prog) return true;
    gl = context;
    if (!gl) return false;
    try {
      prog = gl.createProgram();
      gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
      gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(prog) || 'link failed');
    } catch (e) {
      prog = null;
      report.why = 'map-effect shader: ' + e.message;
      return false;
    }
    for (const n of ['uModel', 'uOrigin', 'uUVOffset', 'uRect', 'uFlipY',
                     'uK', 'uTex', 'uHasTex', 'uMeshAlpha',
                     'uGlow', 'uGrid', 'uGlowTint'])
      uni[n] = gl.getUniformLocation(prog, n);
    for (const n of ['aPos', 'aUV'])
      attr[n] = gl.getAttribLocation(prog, n);

    white = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, white);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));
    gl.bindTexture(gl.TEXTURE_2D, null);
    return true;
  }

  /** A placement's fractional cell -> its painted-image pixel.
   *
   *  **THE RECORD'S `origin` IS NOT A PAINTED-IMAGE PIXEL, and using it as
   *  one drew this pass in the wrong place for a while without looking
   *  wrong.** The origin is a pixel in the map's FULL isometric diamond; the
   *  `.pul` the page composites is a CROPPED WINDOW of that diamond with its
   *  own origin constant. On gsjx03_new the diamond is 43,520 px across and
   *  the image is 17,920, so every placement landed up to 12,800 px to the
   *  right of where it belonged -- far enough off-image that 93 instances
   *  built and issued ZERO draw calls, which is how it was caught. On
   *  zsjx03_new the error was smaller than the viewport, so SOME effects drew
   *  -- in the wrong places, and a screenshot of that map would have looked
   *  like a success.
   *
   *  The conversion is the page's own placement rule, `mapmodel.corner`:
   *  `px = (gx - gy + K)*32`, `py = (gx + gy - K)*16`, verified on 131 of 132
   *  maps (docs/ground_art.md 1). Repeated here rather than imported so this
   *  module does not depend on the page, and pinned against `puzzle.py`'s
   *  constants by the same gate that pins `mapmodel.js`'s copy.
   */
  function imagePx(cell) {
    const gx = cell[0], gy = cell[1], K = originCells;
    return [(gx - gy + K) * 32, (gx + gy - K) * 16];
  }

  /** The definition's `offset`, as a PAINTED-IMAGE PIXEL displacement.
   *
   *  **THE Y COMPONENT IS A HEIGHT, NOT A GROUND AXIS**, and treating it as
   *  one put every torch flame on this map below and to the left of its
   *  brazier. The owner spotted it on `zsjx03_new`, and the map is an
   *  unusually good test because it places TWO variants of one torch:
   *
   *      mj_hhdc_19   offset (0, 0, 0)       drew PERFECTLY, in the bowl
   *      mj_hhdc_19b  offset (0, -100, 0)    drew 52 px left, 5 px down
   *
   *  The difference between the two is exactly what a -100 on a ground axis
   *  produces through the isometric affine: (-50, +25). Same art, same
   *  placement code, one field different -- so the placement machinery was
   *  never in question and the axis was.
   *
   *  WHAT SAYS IT IS A HEIGHT, over the 325 map-effect definitions on 7878:
   *
   *      X   non-zero in  20   10 negative / 10 positive
   *      Y   non-zero in 108   **99 negative**, median -100
   *      Z   non-zero in   7    7 negative
   *
   *  An axis used in a third of all definitions and carrying the SAME SIGN
   *  in 92% of them is not a horizontal displacement -- authors raise
   *  things; they do not systematically shift them one way across a corpus.
   *  X, used in 6% and split evenly, is what a real ground axis looks like.
   *  Negative is UP, which is the same convention `effectplay` already
   *  assumed when it negated Z for `offsetRender`.
   *
   *  **ONLY Y IS REINTERPRETED HERE.** X (20 definitions) and Z (7) keep
   *  going through the world matrix exactly as before: I have a measurement
   *  for Y and none for them, and moving an axis I cannot test would be
   *  trading a defect I can see for one I cannot.
   */
  function liftOffset(def) {
    const o = def && def.offset;
    if (!o) return [0, 0];
    // `offset[1]` negative = up; `KZ` is the height scale, as everywhere else.
    return [0, (+o[1] || 0) * OFFSET_Y_PX_PER_UNIT];
  }

  function texUrl(path) {
    return '/api/texture?path=' + encodeURIComponent(path);
  }

  /** The texture bundle's per-request path limit (`api_texbundle`). */
  const BUNDLE_MAX = 64;
  /** How this map's textures arrived: straight DXT from `/api/texbundle`, or
   *  the PNG route. Cumulative, like `npot` -- a property of the cache. */
  const texStats = { dxt: 0, rgba: 0, png: 0, bundles: 0, bundleMs: 0 };
  let s3tc;

  function s3tcFormat(fmt) {
    if (s3tc === undefined) {
      s3tc = gl.getExtension('WEBGL_compressed_texture_s3tc')
          || gl.getExtension('MOZ_WEBGL_compressed_texture_s3tc')
          || gl.getExtension('WEBKIT_WEBGL_compressed_texture_s3tc')
          || null;
    }
    if (!s3tc) return undefined;
    // RGBA-DXT1 for punch-through alpha, as gl.js and tilebake.js choose.
    return { DXT1: s3tc.COMPRESSED_RGBA_S3TC_DXT1_EXT,
             DXT3: s3tc.COMPRESSED_RGBA_S3TC_DXT3_EXT,
             DXT5: s3tc.COMPRESSED_RGBA_S3TC_DXT5_EXT }[fmt];
  }

  /** One bundle entry to a GL texture, keeping `loadTexture`'s rules:
   *  REPEAT on power-of-two (43% of these meshes address outside 0..1) and
   *  mipmapped minification. A DXT texture cannot `generateMipmap` in WebGL 1,
   *  so its chain comes FROM THE FILE (`mips=1`, `levels`); one without a
   *  complete chain is drawn LINEAR rather than mipmapped, because a partial
   *  chain is an incomplete texture and draws black. Returns null when this
   *  entry cannot be uploaded here -- the caller then takes the PNG route. */
  function uploadEntry(e, buf, base) {
    const pot = v => v > 0 && (v & (v - 1)) === 0;
    const fmt = e.fmt === 'RGBA' ? null : s3tcFormat(e.fmt);
    if (e.fmt !== 'RGBA' && fmt === undefined) return null;
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    let mipped = false;
    if (e.fmt === 'RGBA') {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, e.w, e.h, 0, gl.RGBA,
                    gl.UNSIGNED_BYTE, new Uint8Array(buf, base + e.off, e.size));
      if (pot(e.w) && pot(e.h)) { gl.generateMipmap(gl.TEXTURE_2D); mipped = true; }
      texStats.rgba++;
    } else {
      const block = e.fmt === 'DXT1' ? 8 : 16;
      const levels = Math.max(1, e.levels || 1);
      let off = base + e.off, w = e.w, h = e.h;
      for (let i = 0; i < levels; i++) {
        const n = Math.max(1, (w + 3) >> 2) * Math.max(1, (h + 3) >> 2) * block;
        gl.compressedTexImage2D(gl.TEXTURE_2D, i, fmt, w, h, 0,
                                new Uint8Array(buf, off, n));
        off += n; w = Math.max(1, w >> 1); h = Math.max(1, h >> 1);
      }
      // WebGL 1 cannot mipmap a non-power-of-two texture: sample level 0.
      mipped = levels > 1 && pot(e.w) && pot(e.h);
      texStats.dxt++;
    }
    if (pot(e.w) && pot(e.h)) {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    } else {
      npot++;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER,
                     mipped ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.bindTexture(gl.TEXTURE_2D, null);
    return t;
  }

  /** Every texture a map's effects draw, as DXT, 64 to a request -- instead of
   *  one `/api/texture` PNG (server-side DXT decode + PNG encode, browser
   *  decode, RGBA upload) per path. Anything the bundle cannot supply -- no
   *  S3TC, a missing path, a failed request -- goes to `loadTexture`'s PNG
   *  route, so no layer is left on the white fallback that could have had
   *  its art. `onDone` fires as each chunk lands. */
  async function loadTextures(paths, onDone) {
    const want = [...new Set(paths.filter(p => p && !textures.has(p)))];
    if (!want.length) { if (onDone) onDone(); return; }
    for (const p of want) textures.set(p, null);            // claimed
    for (let i = 0; i < want.length; i += BUNDLE_MAX) {
      const chunk = want.slice(i, i + BUNDLE_MAX);
      const got = new Set();
      try {
        const t0 = performance.now();
        const r = await fetch('/api/texbundle?mips=1&paths='
                              + encodeURIComponent(chunk.join('|')));
        if (r.ok) {
          const buf = await r.arrayBuffer();
          const magic = String.fromCharCode(...new Uint8Array(buf, 0, 4));
          if (magic === 'COTB' && gl) {
            const mlen = new DataView(buf).getUint32(8, true);
            const man = JSON.parse(new TextDecoder().decode(
              new Uint8Array(buf, 12, mlen)));
            for (const e of man.entries || []) {
              if (!e.path) continue;
              const t = uploadEntry(e, buf, 12 + mlen);
              if (t) { textures.set(e.path, t); got.add(e.path); }
            }
          }
        }
        texStats.bundles++;
        texStats.bundleMs += performance.now() - t0;
      } catch (err) { /* the chunk takes the PNG route below */ }
      for (const p of chunk) {
        if (got.has(p)) continue;
        textures.delete(p);                  // unclaim, so loadTexture runs
        texStats.png++;
        loadTexture(p, onDone);
      }
      if (onDone) onDone();
    }
  }

  /** Upload one texture, once. Returns immediately; the draw pass uses the
   *  white fallback until the image lands and then picks it up with no
   *  further bookkeeping, because the map is keyed on the path. */
  function loadTexture(path, onDone) {
    if (!path || textures.has(path)) { if (onDone) onDone(); return; }
    textures.set(path, null);                 // claimed, so we fetch once
    const img = new Image();
    img.onload = () => {
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      // No UNPACK_FLIP_Y: fx.js's QUAD comment states that V grows DOWNWARD
      // for these assets and that the atlas row index counts from the top.
      // Flipping here would invert every flipbook.
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      // **REPEAT, NOT CLAMP, AND THIS WAS THE BUG THAT HID THE REEDS.**
      //
      // These meshes address their texture OUTSIDE 0..1 -- `lhd_lw3`'s UVs
      // are u 0..1, v **-1..0** -- and the engine wraps: `fx.js` records
      // `graphic.dll` folding UVs into [-1.1, 1.1]. Under CLAMP_TO_EDGE every
      // one of those texels resolves to a single edge row, so a 512x512
      // texture that is 98.2% transparent -- a wispy grass frond on clear
      // ground -- painted the reed with its own
      // empty margin. The draw calls all issued, the geometry was right
      // (475 x 355 px on screen), alpha was 1, the texture was bound: it
      // rendered a faint smudge. (It was reported as "no banners"; the
      // `lhd_lw*` art is REEDS, and the hanging cloth on this map is cover
      // sprites. The rendering bug was real and the name was mine.)
      //
      // **1,353 of the 3,120 map-effect meshes -- 43% -- address outside
      // 0..1**, so this was never only those.
      //
      // `gl.js:1451` has had the correct rule all along. This module is a
      // SECOND texture uploader that I wrote without copying it, which is the
      // same "a second copy is how one of them stays wrong" that
      // `dmap.pux_terrain_refs` exists to end, committed in a new file.
      //
      // The POT test is not ceremony: WebGL1 renders an NPOT texture as BLACK
      // under REPEAT, and 19 of the 937 map-effect textures did not parse as
      // DDS here, so the fallback is a real branch and not a hypothetical.
      // Mipmaps come with it for the same reason `gl.js` takes them -- the
      // engine generates its own, and without them a frond minified to a
      // fit view aliases into noise.
      const pot = v => v > 0 && (v & (v - 1)) === 0;
      if (pot(img.width) && pot(img.height)) {
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER,
                         gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      } else {
        npot++;
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      }
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.bindTexture(gl.TEXTURE_2D, null);
      textures.set(path, t);
      if (onDone) onDone();
    };
    img.onerror = () => { textures.delete(path); if (onDone) onDone(); };
    img.src = texUrl(path);
  }

  async function fetchJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
    return r.json();
  }

  /** Drop every built instance.
   *
   *  **`loadedMap` IS CLEARED HERE, and it was not at first.** `load()`
   *  short-circuits when the name it is given is the one already loaded, so
   *  a caller that disposed and then re-loaded the SAME map got the
   *  short-circuit and an empty `placed` -- the module reported the map as
   *  loaded and drew nothing. Caught by the browser gate's own controls,
   *  whose partner arms draw into the same framebuffer with the same code
   *  and so could tell "nothing because it is off" from "nothing because the
   *  module lost its instances".
   *
   *  The scene and texture caches are deliberately NOT cleared: they are
   *  keyed by effect name and texture path, both immutable, and they are
   *  most of the cost of switching between two maps that share effects.
   */
  function dispose() {
    for (const p of placed) if (p.inst) p.inst.dispose();
    placed = [];
    loadedMap = '';
  }

  /** Build every placement on one map. Idempotent per map name.
   *
   *  `onProgress` is called as scenes arrive so the page can redraw while a
   *  93-placement map is still loading, rather than showing nothing for the
   *  whole round trip and then everything at once.
   */
  async function load(mapName, onProgress) {
    if (!mapName) { dispose(); loadedMap = ''; report = blank(); return report; }
    if (mapName === loadedMap) return report;
    if (loading) { try { await loading; } catch (e) { /* fall through */ } }
    loading = (async () => {
      dispose();
      loadedMap = mapName;
      report = blank();
      report.map = mapName;
      if (!prog) { report.why = report.why || 'no GL program'; return report; }
      let d;
      try {
        d = await fetchJson('/api/mapedit/effects?map='
                            + encodeURIComponent(mapName));
      } catch (e) {
        report.why = 'map effects: ' + e.message;
        return report;
      }
      if (mapName !== loadedMap) return report;   // a newer load won
      report.count = d.count || 0;
      report.resolved = d.resolved || 0;
      originCells = +d.originCells || 0;
      const rows = (d.effects || []).filter(r => r.resolved);
      if (rows.length > MAX_INSTANCES) {
        report.overCap = rows.length - MAX_INSTANCES;
        rows.length = MAX_INSTANCES;
      }
      const names = Array.from(new Set(rows.map(r => r.name)));
      report.names = names.length;
      // Fetch each DISTINCT name once. `&mapfx=1` is already on `r.url`, but
      // the name is what dedupes, so the URL is rebuilt from it here rather
      // than trusting sixteen copies of one string to be identical.
      for (const nm of names) {
        if (scenes.has(nm)) continue;
        try {
          const e = await fetchJson('/api/effect?name='
                                    + encodeURIComponent(nm) + '&mapfx=1');
          if (e && e.found) scenes.set(nm, e);
        } catch (err) { /* recorded below, with the name */ }
        if (!scenes.has(nm) && report.badNames.length < 12)
          report.badNames.push(nm);
        if (mapName !== loadedMap) return report;
      }
      // ONE bundle per 64 textures, DXT straight to the GPU, instead of a
      // PNG per texture (owner item 7: map effects on the direct DXT path).
      // Not awaited: placements build now and draw on the white fallback
      // until their art lands, exactly as the per-<img> route behaved.
      const texPaths = [];
      for (const nm of names) {
        const def = scenes.get(nm);
        if (!def) continue;
        for (const lay of def.layers || [])
          if (lay.texture) texPaths.push(lay.texture);
      }
      loadTextures(texPaths, onProgress);
      for (const r of rows) {
        const def = scenes.get(r.name);
        if (!def) { report.noScene++; continue; }
        let inst;
        try {
          // ANCHOR IS IDENTITY ON PURPOSE. The placement is applied in the
          // shader as `uOrigin`, in image pixels, because that is the space
          // the origin is already in. Putting it in the anchor would mean
          // converting pixels to world units and back again on every vertex.
          // Y is removed from the world translation because `liftOffset`
          // now applies it in pixels; X and Z stay, untouched.
          if (def.offsetRender && def.offsetRender[1] !== 0) {
            def.offsetRender = [def.offsetRender[0], 0, def.offsetRender[2]];
          }
          inst = new EffectInstance(gl, def, { role: 'mapfx', slot: r.name,
                                               anchor: FX.IDENT });
        } catch (e) { continue; }
        // THE DEFINITION'S OWN OFFSET, applied HERE and not through the
        // world matrix -- see `liftOffset`.
        const lift = liftOffset(def);
        placed.push({ name: r.name,
                      px: [imagePx(r.cell || [0, 0])[0] + lift[0],
                           imagePx(r.cell || [0, 0])[1] + lift[1]],
                      origin: r.origin, inst });
      }
      report.built = placed.length;
      if (report.noScene)
        report.why = report.noScene + ' placement(s) whose effect did not '
                   + 'load: ' + report.badNames.join(', ');
      if (!placed.length && report.count)
        report.why = 'no placement resolved to playable geometry';
      report.ready = true;
      if (onProgress) onProgress();
      return report;
    })();
    try { return await loading; } finally { loading = null; }
  }

  /** Advance every instance to `ms`.
   *
   *  ONE CLOCK, NO PHASE OFFSETS, and that is the engine's own behaviour:
   *  `fx.js` records that the client spawns a map's ambient decoration at map
   *  load, so every torch on a map really is in phase. `tOffset` exists for
   *  effects that start when something happens to one entity; nothing here
   *  is one of those.
   */
  function tick(ms) {
    if (!enabled) return 0;
    let live = 0;
    for (const p of placed) {
      p.inst.tick(ms);
      if (!p.inst.done || p.inst.def.endless) live++;
    }
    return live;
  }

  function blendOf(name) {
    const T = {
      ZERO: gl.ZERO, ONE: gl.ONE,
      SRC_COLOR: gl.SRC_COLOR, ONE_MINUS_SRC_COLOR: gl.ONE_MINUS_SRC_COLOR,
      SRC_ALPHA: gl.SRC_ALPHA, ONE_MINUS_SRC_ALPHA: gl.ONE_MINUS_SRC_ALPHA,
      DST_ALPHA: gl.DST_ALPHA, ONE_MINUS_DST_ALPHA: gl.ONE_MINUS_DST_ALPHA,
      DST_COLOR: gl.DST_COLOR, ONE_MINUS_DST_COLOR: gl.ONE_MINUS_DST_COLOR,
      SRC_ALPHA_SATURATE: gl.SRC_ALPHA_SATURATE,
    };
    return T[name] !== undefined ? T[name] : gl.ONE;
  }

  /** Draw every live placement into the current framebuffer.
   *
   *  `rect` is the visible art rectangle in painted-image pixels -- the same
   *  one `Baker.drawInto` takes, so the effects pan and zoom with the ground
   *  by construction rather than by a second copy of the transform.
   *
   *  Returns the number of DRAW CALLS issued. Not the number of instances:
   *  an instance whose alpha envelope has run out issues none, and a count
   *  of instances would report a still picture as drawing.
   */
  function draw(rect, flipY) {
    // `report.drawn` IS ZEROED ON EVERY EARLY RETURN, and it was not at
    // first. Turning the toggle off left the last live number standing, so
    // the status object cheerfully reported 51 draw calls for a pass that had
    // stopped running -- and I read that number while testing the toggle and
    // believed it. A counter that is only written on the success path is a
    // counter that lies exactly when you most need it.
    if (!enabled || !prog || !placed.length) { report.drawn = 0; return 0; }
    const w = rect[2] - rect[0], h = rect[3] - rect[1];
    if (!(w > 0) || !(h > 0)) { report.drawn = 0; return 0; }
    const saveProg = gl.getParameter(gl.CURRENT_PROGRAM);
    const saveBlend = gl.getParameter(gl.BLEND);
    gl.useProgram(prog);
    gl.enable(gl.BLEND);
    gl.uniform4f(uni.uRect, rect[0], rect[1], 1 / w, 1 / h);
    gl.uniform1f(uni.uFlipY, flipY === undefined ? -1 : flipY);
    gl.uniform3f(uni.uK, KX, KY, KZ);
    gl.uniform1i(uni.uTex, 0);
    let draws = 0;
    let untextured = 0;

    for (const p of placed) {
      const inst = p.inst;
      if (inst.done && !inst.def.endless) continue;
      const st = inst.state;
      if (!st || st.waiting || st.gap) continue;
      // CULLED IN PIXELS, against the rect the page is showing. `r` from
      // `ini/C3DMapEffect.lua` is the client's own screen-cull radius and is
      // used as one here -- it is too loose to bound the ART (21 of 128 named
      // effects overflow it) but that is the safe direction for a cull: too generous
      // draws something offscreen, too tight pops it out.
      const pad = 512;
      if (p.px[0] < rect[0] - pad || p.px[0] > rect[2] + pad ||
          p.px[1] < rect[1] - pad || p.px[1] > rect[3] + pad) continue;
      gl.uniform2f(uni.uOrigin, p.px[0], p.px[1]);
      const world = inst._world(inst.anchor);

      for (const lay of inst.layers) {
        if (lay.visible === false) continue;
        // The effect's OWN authored blend state (docs/effects.md 7), not the
        // page's. Alpha is kept separate so the transparent canvas ends up
        // with a sane alpha channel -- the same pair `tilebake` uses.
        gl.blendFuncSeparate(blendOf(lay.src.glSrcBlend),
                             blendOf(lay.src.glDstBlend),
                             gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        // **A LAYER WE CANNOT TEXTURE IS NOT DRAWN.**
        //
        // The owner found `sary02_new` covered in little white squares and
        // broad flat translucent panes. Both are one thing: an UNTEXTURED
        // quad. A particle with no texture is a white square; an additive
        // glow with no texture is its own bounding rectangle.
        //
        // **THIS COMMENT USED TO SAY THE TEXTURES DO NOT SHIP. THEY DO.**
        // It claimed, of `sary02_new`, that 19 of 27 layers had no art in
        // any form -- checked for `.dds`, `.tga`, `.bmp`, `.png` and `.jpg`,
        // across four clients. Every one of those searches ran, and every one
        // of them ran BESIDE THE MESH, because the premise underneath was
        // that a layer carries one id naming one path and that
        // `effectId === textureId` on every map effect. Both are false: an
        // RSDB id is a row in ELEVEN TYPED TABLES sharing one id space, so an
        // id has a `.c3` row AND a `.dds` row (69,727 ids carry both), and
        // `lhd_lw4` is a map effect whose two ids differ. Reading the id's
        // texture row instead of guessing at the mesh's neighbours took
        // `sary02_new` to 27/27 and `2024tsf_new` to 34/34, and removed the
        // black diamonds and orange bars with it. A thorough search of the
        // wrong place is not evidence. See `core/wdb.py!rows_for`.
        //
        // What survives, and is still the rule here: where a layer's texture
        // genuinely cannot be resolved we draw NOTHING rather than a white
        // rectangle. A white square is a claim about the map we cannot
        // support and it is the loudest thing on the screen. The count
        // travels in `status()` and onto the layer row, so the map says how
        // much it is not drawing instead of quietly looking finished. Nine
        // layers corpus-wide are in that state -- an id whose `.dds` row
        // names a file that is not on disk.
        //
        // This also covers the LOAD RACE without a special case -- a texture
        // still uploading has an entry whose value is null, so its layer
        // waits for the frame after it lands rather than flashing white.
        const tex = lay.src.texture ? textures.get(lay.src.texture) : null;
        const noTex = lay.src.texture && !tex;
        if (noTex) {
          untextured++;
          // Skipped unless the glow test is on -- EXCEPT while a texture
          // is still uploading, which lands here too and must keep waiting:
          // a glow for one frame and the real art the next would flicker.
          //
          // The cache distinguishes the two: `set(path, null)` claims a
          // pending fetch and `delete(path)` records one that FAILED. So
          // pending is `has() && get() === null`, and everything else that
          // reaches here has no texture and never will.
          const pending = textures.has(lay.src.texture)
                       && textures.get(lay.src.texture) === null;
          if (!glowOn || pending) continue;
        }
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tex || white);
        gl.uniform1f(uni.uHasTex, tex ? 1 : 0);
        gl.uniform1f(uni.uGlow, noTex ? 1 : 0);
        gl.uniform3f(uni.uGlowTint, glowTint[0], glowTint[1], glowTint[2]);

        for (const part of lay.parts) {
          const eff = part.src.effectiveFrames || inst.def.effectiveFrames || 1;
          if (st.frame >= eff) continue;
          gl.uniform1f(uni.uGrid, part.kind === 'particle'
                       ? (part.src.atlas || 1) : (part.src.uvGrid || 1));
          if (part.kind === 'phy') {
            const s = FX.samplePart(part.src, st.frame);
            if (!s.visible || s.alpha <= 0.002) continue;
            // ONE BONE OR MANY -- and the difference is the whole reason the
            // reeds stood still. `motion.bones[0]` poses the entire mesh by
            // the first bone, which is right for the 2,865 one-bone parts and
            // wrong for a jointed one: on `lhd_lw3` -- a nine-bone REED --
            // bone 0 moves 5 units over
            // the track and bone 8 moves 23, so the wave IS the spread between
            // them and taking bone 0 discards exactly the signal.
            //
            // A skinned part gets its positions blended per vertex instead,
            // which bakes the motion in -- so `uModel` is then the placement
            // alone and must NOT also carry a bone matrix, or the pose is
            // applied twice.
            let posVbo = part.vbo, model = null;
            const skin = inst.poseSkin(part, st.frame);
            if (skin) { posVbo = skin.vbo; model = world; }
            if (model === null) {
              const bone = (part.src.motion && part.src.motion.bones &&
                            part.src.motion.bones[0]) || 0;
              model = FX.mul(world,
                             FX.motionMatrix(part.src.motion, bone, st.frame));
            }
            gl.uniform1f(uni.uMeshAlpha, s.alpha);
            gl.uniform2f(uni.uUVOffset, s.uv[0], s.uv[1]);
            gl.uniformMatrix4fv(uni.uModel, false, new Float32Array(model));
            gl.bindBuffer(gl.ARRAY_BUFFER, posVbo);
            gl.enableVertexAttribArray(attr.aPos);
            gl.vertexAttribPointer(attr.aPos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, part.tbo);
            gl.enableVertexAttribArray(attr.aUV);
            gl.vertexAttribPointer(attr.aUV, 2, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.ibo);
            gl.drawElements(gl.TRIANGLES, part.count, gl.UNSIGNED_SHORT, 0);
            draws++;
          } else if (part.kind === 'particle') {
            // The quads come back already in world space -- built from the
            // transformed positions and the basis above -- so the model
            // matrix is identity here, as it is in gl.js.
            const n = inst.uploadParticles(part, st.frame, world, RIGHT, UP);
            if (!n) continue;
            gl.uniform1f(uni.uMeshAlpha, part.alpha);
            gl.uniform2f(uni.uUVOffset, 0, 0);
            gl.uniformMatrix4fv(uni.uModel, false, new Float32Array(FX.IDENT));
            gl.bindBuffer(gl.ARRAY_BUFFER, part.vbo);
            gl.enableVertexAttribArray(attr.aPos);
            gl.vertexAttribPointer(attr.aPos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, part.tbo);
            gl.enableVertexAttribArray(attr.aUV);
            gl.vertexAttribPointer(attr.aUV, 2, gl.FLOAT, false, 0, 0);
            gl.drawArrays(gl.TRIANGLES, 0, n);
            draws++;
          } else if (part.kind === 'shape' && part.count >= 4) {
            gl.uniform1f(uni.uMeshAlpha, 1.0);
            gl.uniform2f(uni.uUVOffset, 0, 0);
            gl.uniformMatrix4fv(uni.uModel, false, new Float32Array(FX.IDENT));
            gl.bindBuffer(gl.ARRAY_BUFFER, part.vbo);
            gl.enableVertexAttribArray(attr.aPos);
            gl.vertexAttribPointer(attr.aPos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, part.tbo);
            gl.enableVertexAttribArray(attr.aUV);
            gl.vertexAttribPointer(attr.aUV, 2, gl.FLOAT, false, 0, 0);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, part.count);
            draws++;
          }
        }
      }
    }
    // LEAVE THE CONTEXT AS IT WAS FOUND. Attribute arrays are global state
    // indexed by LOCATION, not by program, so leaving ours enabled would arm
    // them under `tilebake`'s program on the next frame -- and tilebake's own
    // header records what a stray enabled attribute costs there.
    gl.disableVertexAttribArray(attr.aPos);
    gl.disableVertexAttribArray(attr.aUV);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, null);
    gl.bindTexture(gl.TEXTURE_2D, null);
    if (!saveBlend) gl.disable(gl.BLEND);
    gl.useProgram(saveProg || null);
    report.drawn = draws;
    report.untexturedLayers = untextured;
    return draws;
  }

  /** The texture cache, as data. For diagnosis only.
   *
   *  Added because "is the texture bound?" could not be answered from
   *  outside, and the measurement I reached for instead -- colour spread in
   *  an effects-on vs effects-off difference -- is CONFOUNDED: an additive
   *  WHITE quad over coloured ground clips per channel at saturation, so the
   *  difference reads as coloured even when no texture is bound at all. It
   *  told me textures were working when none were. Rendering the effects
   *  alone on black is the instrument that answers it (0 of 47,528 lit
   *  pixels carried any colour), and this makes the cause visible instead of
   *  only the symptom.
   */
  function debugTextures() {
    const out = [];
    textures.forEach((v, k) => out.push({ path: k, state: v === null ? 'pending'
                                          : (gl && gl.isTexture(v)) ? 'texture' : 'BAD' }));
    return { count: textures.size, entries: out };
  }

  function setEnabled(v) { enabled = !!v; }
  /** TEST MODE -- see `glowOn`. `tint` is `[r, g, b]` in 0..1, ours alone. */
  function setGlow(v, tint) {
    glowOn = !!v;
    if (tint && tint.length === 3) glowTint = tint.map(Number);
  }
  function glowState() { return { on: glowOn, tint: glowTint.slice() }; }
  function isEnabled() { return enabled; }
  function status() {
    // `npotTextures` is carried on the status rather than in `report`
    // because it is a property of the TEXTURE CACHE, which outlives any
    // one map -- `report` is rebuilt per load and would keep resetting a
    // count of uploads that already happened.
    return Object.assign({}, report, { enabled, npotTextures: npot,
                                       textureRoute: Object.assign({}, texStats) });
  }
  /** Live instances, for the page's HUD and for tests. */
  function count() { return placed.length; }

  return { init, load, tick, draw, setEnabled, isEnabled, status, count,
           dispose, imagePx, debugTextures, setGlow, glowState,
           // exported for tests and for the page's HUD text
           CELL, KX, KY, KZ, RIGHT, UP, MAX_INSTANCES };
})();

window.MapFx = MapFx;
