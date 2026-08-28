/* tilebake.js -- client-side ground compositing from resident tiles.
 *
 * The residency model of docs/map_memory.md, running in the page: fetch the
 * map's manifest (/api/game/puzzle) and bundle (/api/game/tileset) ONCE, and
 * upload EVERYTHING the map will ever draw, compressed
 * (WEBGL_compressed_texture_s3tc, no decode): ground tiles, backdrop-plane
 * tiles, TERRAIN scenery sprites and COVER sprites -- ~7 MB median, 58 MB
 * worst (Twin City) per map. Window textures then rebuild locally:
 *
 *     clear VOID -> backdrop planes -> tile quads -> scenery quads
 *
 * which is exactly terrain.art_texture()'s painter order, GPU-side, and the
 * COVER window bakes the same way on a local animation clock. The output
 * texture is the same rectangle the mesh UVs already address
 * (patch.ground.rect), so the mesh, the camera and every placement rule stay
 * byte-identical to the server-rendered path -- but after the one bundle
 * fetch, NO pixel ever crosses the wire again for this map.
 *
 * This is Option B of docs/map_memory.md 4.3 (bake per window), which is
 * also the shape the retail engine's CPuzzleBlockX implies. Option A
 * (texture-array, one draw per chunk, no bake) is the native-engine design
 * and needs WebGL2; this file is deliberately WebGL1.
 *
 * No dependency on gl.js internals: the caller hands in the shared GL
 * context and owns the returned texture.
 */
'use strict';

const TileGround = (() => {

  /** Milliseconds per second -- half of the roll unit conversion.
   *
   *  Named rather than folded into the expression because it IS the recovered
   *  fact: the original client computes `rollSpeed * rate * dt_ms / 100000`,
   *  and that divisor is 1000 (ms -> s) times 100 (`ROLL_RATE_PCT`).
   *  A reader who wants to check the unit should find one constant to check,
   *  not a magic 1000 inside an arithmetic expression.
   *  `docs/ground_animation.md` 10. */
  const ROLL_MS_PER_SEC = 1000;

  /** The other half: `rate` is a PERCENT, and the percent is the plane's own
   *  PARALLAX.
   *
   *  CORRECTED 2026-08-26. This used to be assumed a fixed 100, which made
   *  `rollSpeedX/Y` read as plain pixels per second. It is not fixed. `rate`
   *  is a pair of members on the plane object (`3dgamemap/puzzlebmp.cpp`),
   *  initialised to 100 by its own Init but OVERWRITTEN on every draw from
   *  the draw-params struct the map's plane loop fills -- and what that loop
   *  puts there is the plane's parallax percentage, the same two integers it
   *  divides the camera offset by 100 with a few instructions earlier.
   *
   *  Measured on five client binaries where the code exists (5017, 5065,
   *  5165, 5517, 6090) -- ONE overwrite site each, identical shape, 5017
   *  included, so this was never a "later clients differ" story. The two
   *  integers are read straight out of the map file by an `fread` loop
   *  (5017 VA 0x474019) into the plane object's `+4`/`+8`, which is exactly
   *  `puzzle.Backdrop.parallax` = the `.DMap` trailer's `values[2..3]`.
   *
   *  So the authored speed is `rollSpeed * parallax / 100` px/s. Across the
   *  nine-install corpus, 365 of 400 rolling plane instances carry a
   *  parallax other than (100, 100) -- most of them (30, 30), which the old
   *  expression ran 3.3x too fast. `docs/ground_animation.md` 10.10. */
  const ROLL_RATE_PCT = 100;

  const VS = `
    attribute vec2 aPos;    // painted-image pixels
    attribute vec2 aUV;
    uniform vec4 uRect;     // x0, y0, 1/w, 1/h of the target rect
    uniform float uFlipY;   // +1 into a texture, -1 straight onto a canvas
    varying vec2 vUV;
    void main() {
      vec2 t = (aPos - uRect.xy) * uRect.zw;      // 0..1, y down like the image
      // Baking into a texture keeps the image's y-down convention, because
      // the UVs that later sample it are y-down too. Drawing straight to a
      // framebuffer has no such second half, so the sign flips there and
      // only there -- uFlipY is 1.0 for every pre-existing caller.
      gl_Position = vec4(t.x * 2.0 - 1.0, (t.y * 2.0 - 1.0) * uFlipY, 0.0, 1.0);
      vUV = aUV;
    }`;
  // World-space variant: the same painted-image-pixel geometry, sent through
  // the placement rule's affine (docs/ground_art.md 1) straight into the
  // scene's own MVP -- so the whole map draws in world coordinates with no
  // intermediate texture anywhere.
  //   world.x = CELL * (px/64 + py/32)
  //   world.y = CELL * (px/64 - py/32) - CELL * K
  const WVS = `
    attribute vec2 aPos;
    attribute vec2 aUV;
    uniform mat4 uMVP;
    uniform vec3 uAff;      // CELL/64, CELL/32, CELL*K
    uniform vec2 uShift;    // parallax shift, image px (planes only)
    varying vec2 vUV;
    void main() {
      vec2 p = aPos + uShift;
      vec2 w = vec2(p.x * uAff.x + p.y * uAff.y,
                    p.x * uAff.x - p.y * uAff.y - uAff.z);
      gl_Position = uMVP * vec4(w, 0.0, 1.0);
      vUV = aUV;
    }`;
  const FS = `
    precision mediump float;
    uniform sampler2D uTex;
    varying vec2 vUV;
    void main() { gl_FragColor = texture2D(uTex, vUV); }`;

  /** The manifest's slot grid: base64 of little-endian u16s. */
  function decodeSlots(b64) {
    const raw = atob(b64);
    const out = new Uint16Array(raw.length / 2);
    for (let i = 0; i < out.length; i++)
      out[i] = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
    return out;
  }

  class Baker {
    constructor(gl) {
      this.gl = gl;
      this.ext = gl.getExtension('WEBGL_compressed_texture_s3tc')
              || gl.getExtension('MOZ_WEBGL_compressed_texture_s3tc');
      this.maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      this.prog = null;
      this.tiles = new Map();          // tile index -> WebGLTexture
      this.manifest = null;
      this.slots = null;
      this.buf = gl.createBuffer();    // dynamic quad stream, reused per bake
      this.stats = { tiles: 0, bytes: 0, loadMs: 0, lastBakeMs: 0 };
    }

    get supported() { return !!this.ext; }

    _glFormat(fmt) {
      // RGBA DXT1, not RGB: a DXT1 block with color0 <= color1 is in
      // punch-through-alpha mode, and the RGB variant would render those
      // texels black instead of transparent.
      const e = this.ext;
      return { DXT1: e.COMPRESSED_RGBA_S3TC_DXT1_EXT,
               DXT3: e.COMPRESSED_RGBA_S3TC_DXT3_EXT,
               DXT5: e.COMPRESSED_RGBA_S3TC_DXT5_EXT }[fmt];
    }

    _mkProgram(vsrc) {
      const gl = this.gl;
      const mk = (type, src) => {
        const s = gl.createShader(type);
        gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
          throw new Error('tilebake shader: ' + gl.getShaderInfoLog(s));
        return s;
      };
      const p = gl.createProgram();
      gl.attachShader(p, mk(gl.VERTEX_SHADER, vsrc));
      gl.attachShader(p, mk(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS))
        throw new Error('tilebake link: ' + gl.getProgramInfoLog(p));
      return { p,
               aPos: gl.getAttribLocation(p, 'aPos'),
               aUV: gl.getAttribLocation(p, 'aUV'),
               uRect: gl.getUniformLocation(p, 'uRect'),
               uFlipY: gl.getUniformLocation(p, 'uFlipY'),
               uMVP: gl.getUniformLocation(p, 'uMVP'),
               uAff: gl.getUniformLocation(p, 'uAff'),
               uShift: gl.getUniformLocation(p, 'uShift'),
               uTex: gl.getUniformLocation(p, 'uTex') };
    }

    _program() {
      if (!this.prog) this.prog = this._mkProgram(VS);
      return this.prog;
    }

    _worldProgram() {
      if (!this.wprog) this.wprog = this._mkProgram(WVS);
      return this.wprog;
    }

    _uploadEntry(e, buffer) {
      const gl = this.gl;
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      if (e.fmt === 'RGBA') {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, e.w, e.h, 0, gl.RGBA,
                      gl.UNSIGNED_BYTE, new Uint8Array(buffer, e.off, e.size));
      } else {
        const fmt = this._glFormat(e.fmt);
        if (fmt === undefined) { gl.deleteTexture(tex); return null; }
        gl.compressedTexImage2D(gl.TEXTURE_2D, 0, fmt, e.w, e.h, 0,
                                new Uint8Array(buffer, e.off, e.size));
      }
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return tex;
    }

    /** Upload EVERYTHING the map draws: ground tiles, backdrop-plane tiles,
     *  scene and cover sprites. One compressed upload each, no decode
     *  anywhere, nothing left to fetch per window. Returns the tile count. */
    load(manifest, buffer) {
      const t0 = performance.now();
      this.dispose();
      this.manifest = manifest;
      this.slots = decodeSlots(manifest.slots);
      for (const e of manifest.entries) {
        const tex = this._uploadEntry(e, buffer);
        if (tex) this.tiles.set(e.i, tex);
      }
      // v2: sprite/plane payloads, indexed by entry position.
      this.sprites = [];
      for (const e of (manifest.sprites || [])) {
        this.sprites.push(this._uploadEntry(e, buffer));
        this.spriteMeta = manifest.sprites;
      }
      this.planes = (manifest.planes || []).map(p => ({
        ...p, slotGrid: decodeSlots(p.slots),
      })).sort((a, b) => (a.index || 0) - (b.index || 0));
      // Frame 0 is the still frame every existing test and every server-side
      // PNG renders, so a freshly loaded map looks exactly as it did.
      this.planeFrame = 0;
      this.scenes = manifest.scenes || [];
      this.covers = manifest.covers || [];
      this.stats.tiles = this.tiles.size;
      this.stats.sprites = this.sprites.length;
      this.stats.bytes = manifest.bytes;
      this.stats.loadMs = performance.now() - t0;
      return this.tiles.size;
    }

    get hasCovers() { return !!(this.covers && this.covers.length); }

    /** True when at least one backdrop-plane tile has more than one frame.
     *
     *  The whole point of asking: `play.js` only keeps a redraw timer alive
     *  for a STANDING player when something on screen actually moves, and
     *  that gate used to be `hasCovers` alone. Measured over the live
     *  install, 3 of 136 maps answer true here -- `beach`, `icecrypt-lev5`,
     *  `icecrypt-lev6` -- so this follows the existing "animate only what
     *  moves" precedent rather than starting a clock everywhere. */
    get hasAnimatedPlanes() {
      for (const p of (this.planes || []))
        if (p.anim && Object.keys(p.anim).length) return true;
      return false;
    }

    /** True when at least one backdrop plane carries a non-zero `rollSpeed`.
     *
     *  Asked for the same reason as `hasAnimatedPlanes`: `play.js` only keeps
     *  a redraw timer alive for a STANDING player when something on screen
     *  actually moves. Measured over the live install, **35 planes across 35
     *  of 137 maps** answer true here, backed by just four `.pul` files
     *  (`skybg-move` on 29 of them, `bravebg-move` on 3, `newplainbg-move` on
     *  2, `2009-7x-bg2` on 1) -- so this is the same "animate only what
     *  moves" gate rather than a clock started everywhere. */
    get hasRollingPlanes() {
      for (const p of (this.planes || [])) {
        const r = p.roll || [0, 0];
        if (r[0] || r[1]) return true;
      }
      return false;
    }

    /** The plane animation's cycle length, IN FRAMES -- 0 when nothing
     *  animates. A frame count, not a duration: the `.ani` format carries
     *  `FrameAmount` and `Frame<N>` and no interval at all, so the LENGTH is
     *  the only part of the rate the content actually authors. */
    planeFrames() {
      let n = 0;
      for (const p of (this.planes || []))
        if (p.animFrames > n) n = p.animFrames;
      return n;
    }

    /** Advance every animated plane tile to cycle position `frame`.
     *
     *  `frame` is an integer TICK COUNT, not milliseconds -- the caller owns
     *  the cadence. Costs one array index and one property write per animated
     *  run; no GL call, no buffer upload, no allocation. Returns the number of
     *  runs whose texture actually changed, which is what the non-vacuity
     *  test asserts on (0 for a map with no animated planes, at any frame). */
    setPlaneFrame(frame) {
      let changed = 0;
      for (const r of (this.planeRuns || [])) {
        if (!r.frames) continue;
        const t = r.frames[((frame % r.frames.length) + r.frames.length)
                           % r.frames.length];
        if (t !== r.tex) { r.tex = t; changed++; }
      }
      this.planeFrame = frame;
      return changed;
    }

    /** The longest a cover animation frame lasts, 0 if nothing animates. */
    coverInterval() {
      let iv = 0;
      for (const c of this.covers || [])
        if (c.interval > 0 && c.frames.length > 1)
          iv = iv ? Math.min(iv, c.interval) : c.interval;
      return iv;
    }

    dispose() {
      for (const t of this.tiles.values()) this.gl.deleteTexture(t);
      this.tiles.clear();
      for (const t of (this.sprites || [])) if (t) this.gl.deleteTexture(t);
      this.sprites = [];
      this.planes = [];
      this.planeFrame = 0;
      this.scenes = [];
      this.covers = [];
      this.manifest = null;
      this.slots = null;
      for (const b of ['groundBuf', 'planeBuf', 'sceneBuf', 'coverBuf'])
        if (this[b]) { this.gl.deleteBuffer(this[b]); this[b] = null; }
      // The shadow is map-independent art -- it survives a map change, and
      // is only released when the whole baker goes away.
      if (this._closing && this.shadowTex) {
        this.gl.deleteTexture(this.shadowTex);
        this.shadowTex = null;
      }
      this.groundRuns = this.planeRuns = this.sceneRuns = this.coverRuns = null;
      this._coverSig = null;
      // `_runs` holds WebGLTextures that were just deleted, and the cached
      // placement quads belong to the map that is going away.
      this._runs = null;
      this._runSig = null;
    }

    // `pr` defaults to the window-bake program; the world pass has its own
    // and must pass it in, or the attribute locations come from the wrong
    // program (or from null, when only the world path has ever run).
    _drawQuads(verts, tex, prog) {
      const gl = this.gl, pr = prog || this.prog;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
      gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STREAM_DRAW);
      gl.vertexAttribPointer(pr.aPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(pr.aUV, 2, gl.FLOAT, false, 16, 8);
      gl.drawArrays(gl.TRIANGLES, 0, verts.length / 4);
    }

    /** Concatenate vertex chunks into ONE Float32Array in a single pass.
     *
     *  This used to be `all = all.concat(part)` in the loop, which copies the
     *  whole accumulator every iteration -- quadratic in the number of draw
     *  runs. Twin City has 2,102 of them over ~7M floats, and the build time
     *  degraded from 0.5 s to 48 s as soon as the machine was under memory
     *  pressure. Preallocating makes it linear and immune to that. */
    static _pack(parts, total) {
      const out = new Float32Array(total);
      let at = 0;
      for (const p of parts) { out.set(p, at); at += p.length; }
      return out;
    }

    static _quad(x0, y0, x1, y1, u0, v0, u1, v1) {
      return [x0, y0, u0, v0,  x1, y0, u1, v0,  x1, y1, u1, v1,
              x0, y0, u0, v0,  x1, y1, u1, v1,  x0, y1, u0, v1];
    }

    /** The piece of this plane behind a ground rect, parallax-shifted.
     *  Mirrors `puzzle.Backdrop.sample_rect` INCLUDING ITS ROUNDING: that
     *  reference uses Python `int()`, which truncates TOWARD ZERO, and the
     *  `/ 100` is the client's own (see `backdrops()` for the disassembly
     *  site). `Math.floor` rounds toward -INFINITY instead. The two agree
     *  for every rect with a non-negative origin -- which is every rect any
     *  harness in this tree has ever sampled -- and disagree the moment the
     *  camera pans west or north of the origin, where floor lands one whole
     *  pixel further out and the plane shifts against the ground.
     *
     *  Static, and separated from `_drawPlane` for that reason: it is now
     *  callable with NO GL context, so it can be pinned by an executed test
     *  rather than by reading the source for the word `trunc`. */
    static _sampleRect(rect, parallax) {
      const [x0, y0, x1, y1] = rect;
      const [ppx, ppy] = parallax || [100, 100];
      const sx0 = Math.trunc(x0 * ppx / 100), sy0 = Math.trunc(y0 * ppy / 100);
      return [sx0, sy0, sx0 + (x1 - x0), sy0 + (y1 - y0)];
    }

    /** The first tile boundary at or BELOW `v`, the origin of the wrapped
     *  copy that covers it. Mirrors the reference's `(sy0 // ph) * ph`, and
     *  Python `//` IS FLOOR DIVISION -- toward negative infinity.
     *
     *  This one is FLOOR and `_sampleRect` is TRUNCATION, two lines apart,
     *  and that asymmetry is deliberate: the reference uses `int()` for the
     *  parallax `/ 100` and `//` for this. Making them agree in either
     *  direction is wrong. Truncating here would snap a negative origin
     *  TOWARD zero, i.e. to the boundary ABOVE `v`, leaving the strip
     *  between `v` and that boundary uncovered -- a seam that only appears
     *  west or north of the origin.
     *
     *  Extracted because the mutation `floor -> trunc` SURVIVED here
     *  (`tools/mutant.py`, exit 3) while the same mutation was killed two
     *  lines up: the rounding that must not change was the one nothing
     *  pinned. */
    /** Which frame of an animated cover shows at `timeMs`. THREE identical
     *  copies of this ternary were inline -- two draw paths and the
     *  signature that decides whether to rebuild -- so a fix to one left
     *  two, and the signature could disagree with what was drawn.
     *
     *  NOTE THE GUARD: `timeMs` itself is tested and 0 is FALSY, so t=0
     *  returns 0 WITHOUT evaluating the formula. An off-by-one in the
     *  formula is invisible at t=0 -- which is exactly where a first
     *  fixture naturally lands. The pin uses t>0 for that reason.
     *
     *  MEASURED before this existed: the `js-frame-index` mutation
     *  SURVIVED test_backdrop_roll, test_ground_animation,
     *  test_tilebake_exec AND test_page_constants -- all four. */
    static _frameIndex(timeMs, interval, nFrames) {
      return (interval > 0 && nFrames > 1 && timeMs)
        ? Math.floor(timeMs / interval) % nFrames : 0;
    }

    static _tileOrigin(v, span) {
      return Math.floor(v / span) * span;
    }

    /** Draw one backdrop plane behind `rect`, from its own resident tiles:
     *  parallax-shifted, wrapped in both axes -- puzzle.Backdrop.render_tiled
     *  in GPU form. Opaque (planes are the floor of the compositing stack). */
    _drawPlane(p, rect) {
      const [x0, y0, x1, y1] = rect;
      const [sx0, sy0, sx1, sy1] = Baker._sampleRect(rect, p.parallax);
      const pw = Math.max(1, p.pixels[0]), ph = Math.max(1, p.pixels[1]);
      const G = p.grid, [tw, th] = p.pul;
      const groups = new Map();
      for (let oy = Baker._tileOrigin(sy0, ph); oy < sy1; oy += ph)
        for (let ox = Baker._tileOrigin(sx0, pw); ox < sx1; ox += pw)
          for (let j = 0; j < th; j++)
            for (let i = 0; i < tw; i++) {
              const idx = p.slotGrid[j * tw + i];
              if (idx === this.manifest.empty) continue;
              let ent = p.tiles[String(idx)];
              // Same cycle position the world path is on, so the two
              // renderers cannot show different frames of the same plane.
              const seq = p.anim && p.anim[String(idx)];
              if (seq && seq.length > 1) {
                const f = this.planeFrame | 0;
                ent = seq[((f % seq.length) + seq.length) % seq.length];
              }
              if (ent === undefined || !this.sprites[ent]) continue;
              // tile rect in sample space -> window space
              const tx0 = ox + i * G, ty0 = oy + j * G;
              if (tx0 + G <= sx0 || tx0 >= sx1 || ty0 + G <= sy0 || ty0 >= sy1)
                continue;
              const wx0 = x0 + (tx0 - sx0), wy0 = y0 + (ty0 - sy0);
              let g = groups.get(ent);
              if (!g) groups.set(ent, g = []);
              g.push(...Baker._quad(wx0, wy0, wx0 + G, wy0 + G, 0, 0, 1, 1));
            }
      for (const [ent, verts] of groups)
        this._drawQuads(new Float32Array(verts), this.sprites[ent]);
    }

    /** Sprite placements (scenes or covers) over `rect`, in stored painter
     *  order. `timeMs` picks the frame of an animated placement. */
    _drawPlacements(list, rect, timeMs) {
      const [x0, y0, x1, y1] = rect;
      for (const c of list) {
        const fi = Baker._frameIndex(timeMs, c.interval, c.frames.length);
        const ent = c.frames[fi];
        const meta = this.spriteMeta && this.spriteMeta[ent];
        const tex = this.sprites[ent];
        if (!meta || !tex) continue;
        if (c.x + meta.w <= x0 || c.x >= x1 ||
            c.y + meta.h <= y0 || c.y >= y1) continue;
        // The quad is in painted-image pixels, so it depends on the
        // placement and its frame and not on the view. Built once per
        // (placement, frame) rather than once per placement per frame:
        // `newplain` has 2 151 covers and allocating a Float32Array for each
        // of them every frame is pure garbage. Painter order is untouched --
        // this caches the vertices, it does not reorder or batch the draws.
        if (!c._verts) c._verts = {};
        let v = c._verts[ent];
        if (!v) v = c._verts[ent] = new Float32Array(
          Baker._quad(c.x, c.y, c.x + meta.w, c.y + meta.h, 0, 0, 1, 1));
        this._drawQuads(v, tex);
      }
    }

    /** The character's ground shadow. `img` is a decoded <img>; the anchor
     *  (where the character's feet sit inside the art, in 0..1) is measured
     *  from the alpha channel rather than assumed, so a future shadow skin
     *  with a differently-placed blob still lands correctly. */
    setShadow(img) {
      const gl = this.gl;
      if (this.shadowTex) gl.deleteTexture(this.shadowTex);
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.shadowTex = t;
      this.shadowSize = [img.width, img.height];
      this.shadowAnchor = Baker._alphaCentroid(img);
      return this.shadowAnchor;
    }

    /** Alpha-weighted centroid of an image, in 0..1. */
    static _alphaCentroid(img) {
      try {
        const c = document.createElement('canvas');
        c.width = img.width; c.height = img.height;
        const g = c.getContext('2d', { willReadFrequently: true });
        g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, img.width, img.height).data;
        let sx = 0, sy = 0, sa = 0;
        for (let y = 0; y < img.height; y++)
          for (let x = 0; x < img.width; x++) {
            const a = d[(y * img.width + x) * 4 + 3];
            if (a > 8) { sx += x * a; sy += y * a; sa += a; }
          }
        if (!sa) return [0.5, 0.5];
        return [sx / sa / img.width, sy / sa / img.height];
      } catch (e) { return [0.5, 0.5]; }
    }

    /** Draw the shadow flat on the ground under a character.
     *
     *  `cx`,`cy` are continuous cell coordinates (the drawn avatar position,
     *  not the server's rounded one), so the shadow slides with the walk.
     *  It goes through the same image-pixel affine as the ground, so it lies
     *  in the map plane exactly like a scenery sprite -- which is what a
     *  shadow painted as a 2:1 ellipse wants. */
    drawShadow(mvp, cx, cy, scale) {
      if (!this.shadowTex || !this.manifest) return;
      const gl = this.gl;
      const K = this.manifest.pixels[0] / 64;
      // Cell -> painted-image pixels (docs/ground_art.md 1), continuous.
      const px = (cx - cy + K) * 32;
      const py = (cx + cy - K + 1) * 16;
      const s = scale || 1;
      const w = this.shadowSize[0] * s, h = this.shadowSize[1] * s;
      const [ax, ay] = this.shadowAnchor || [0.5, 0.5];
      const x0 = px - ax * w, y0 = py - ay * h;
      const pr = this._beginWorld(mvp);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                           gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      this._drawQuads(new Float32Array(
        Baker._quad(x0, y0, x0 + w, y0 + h, 0, 0, 1, 1)), this.shadowTex, pr);
      this._endWorld(pr);
    }

    _beginTarget(w, h, clear) {
      const gl = this.gl;
      const out = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, out);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA,
                    gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0,
                              gl.TEXTURE_2D, out, 0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.deleteFramebuffer(fbo); gl.deleteTexture(out);
        return null;
      }
      const saved = {
        fbo,
        view: gl.getParameter(gl.VIEWPORT),
        depth: gl.isEnabled(gl.DEPTH_TEST),
        blend: gl.isEnabled(gl.BLEND),
        cull: gl.isEnabled(gl.CULL_FACE),
        scissor: gl.isEnabled(gl.SCISSOR_TEST),
      };
      gl.viewport(0, 0, w, h);
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.SCISSOR_TEST);
      gl.clearColor(clear[0], clear[1], clear[2], clear[3]);
      gl.clear(gl.COLOR_BUFFER_BIT);
      return { out, saved };
    }

    _endTarget(saved) {
      const gl = this.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteFramebuffer(saved.fbo);
      gl.viewport(saved.view[0], saved.view[1], saved.view[2], saved.view[3]);
      if (saved.depth) gl.enable(gl.DEPTH_TEST);
      if (saved.blend) gl.enable(gl.BLEND); else gl.disable(gl.BLEND);
      if (saved.cull) gl.enable(gl.CULL_FACE);
      if (saved.scissor) gl.enable(gl.SCISSOR_TEST);
    }

    _beginQuads(rect, flipY) {
      const gl = this.gl, pr = this._program();
      gl.useProgram(pr.p);
      gl.uniform4f(pr.uRect, rect[0], rect[1],
                   1 / (rect[2] - rect[0]), 1 / (rect[3] - rect[1]));
      gl.uniform1f(pr.uFlipY, flipY === undefined ? 1 : flipY);
      gl.uniform1i(pr.uTex, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.enableVertexAttribArray(pr.aPos);
      gl.enableVertexAttribArray(pr.aUV);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                           gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    }

    _endQuads() {
      const gl = this.gl, pr = this.prog;
      gl.disableVertexAttribArray(pr.aPos);
      gl.disableVertexAttribArray(pr.aUV);
    }

    /** The COVER layer over `rect`, baked from resident sprites at `timeMs`
     *  on the caller's clock -- the same RGBA-over-transparency the server's
     *  cover_layer produces, with no server anywhere near it. Caller owns
     *  the texture. */
    bakeCover(rect, timeMs) {
      if (!this.supported || !this.manifest || !this.hasCovers) return null;
      const w = rect[2] - rect[0], h = rect[3] - rect[1];
      if (w <= 0 || h <= 0 || w > this.maxTex || h > this.maxTex) return null;
      const t = this._beginTarget(w, h, [0, 0, 0, 0]);
      if (!t) return null;
      this._beginQuads(rect);
      this.gl.enable(this.gl.BLEND);
      this._drawPlacements(this.covers, rect, timeMs || 0);
      this._endQuads();
      this._endTarget(t.saved);
      return t.out;
    }

    // ---------------------------------------------------- whole-map drawing
    //
    // No windows, no bakes: static world-space buffers built ONCE at map
    // load, drawn every frame straight into the scene's MVP. Zooming out to
    // the whole map is just a bigger orthographic box over the same buffers.

    /** Build the static geometry for the whole map. `cell` is the world
     *  units per map cell (play.js's CELL). Call after load(). */
    buildWorld(cell) {
      const gl = this.gl;
      const m = this.manifest;
      this.cell = cell;
      this.k = m.pixels[0] / 64;
      const t0 = performance.now();

      // Ground: every painted slot, grouped by tile, one static VBO of
      // (pos.px, uv) quads with per-group [first, count) draw ranges.
      const groups = new Map();
      const G = m.grid, [tw, th] = m.pul;
      for (let j = 0; j < th; j++)
        for (let i = 0; i < tw; i++) {
          const idx = this.slots[j * tw + i];
          if (idx === m.empty || !this.tiles.has(idx)) continue;
          let g = groups.get(idx);
          if (!g) groups.set(idx, g = []);
          g.push(...Baker._quad(i * G, j * G, (i + 1) * G, (j + 1) * G,
                                0, 0, 1, 1));
        }
      const runs = [];
      const parts = [];
      let total = 0;
      for (const [idx, verts] of groups) {
        runs.push({ tex: this.tiles.get(idx), first: total / 4,
                    count: verts.length / 4 });
        parts.push(verts);
        total += verts.length;
      }
      this.groundRuns = runs;
      this.groundBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.groundBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(parts, total), gl.STATIC_DRAW);
      this.groundVertBytes = total * 4;

      // Backdrop planes: wrap copies over the map's full-diamond bounding
      // box in painted-image px, padded a whole extent each side so the
      // parallax shift can never expose an edge.
      const W = this.k + m.pixels[1] / 32;           // map cells (square)
      const bx0 = (0 - W + this.k) * 32, bx1 = (W + this.k) * 32;
      const by0 = -this.k * 16, by1 = (2 * W - this.k) * 16;
      const padX = (bx1 - bx0), padY = (by1 - by0);
      this.planeRuns = [];
      const pparts = [];
      let ptotal = 0;
      for (const p of this.planes) {
        const pw = Math.max(1, p.pixels[0]), ph = Math.max(1, p.pixels[1]);
        const pg = p.grid, [ptw, pth] = p.pul;
        // ANIMATED PLANE TILES GET THEIR OWN RUN, keyed by tile index rather
        // than by texture. Every quad in a plane is exactly `pg` x `pg` with
        // full UVs, so the GEOMETRY of an animated tile is the same at frame
        // 0 and frame 44 -- only the bound texture differs. That is why
        // animating a plane needs no buffer traffic at all: `setPlaneFrame`
        // swaps `run.tex` and the STATIC_DRAW vertex buffer is never touched.
        // Contrast `_rebuildCovers`, which must respec a DYNAMIC_DRAW buffer
        // every frame because cover sprites change SIZE between frames.
        //
        // Splitting the grouping this way is safe because a plane's slot grid
        // is a partition: within one plane no two quads overlap, so run order
        // inside a plane cannot change the picture. Plane-to-plane order is
        // untouched -- `this.planes` is still walked in sorted `index` order
        // and its runs still land contiguously.  docs/ground_animation.md 5
        const anim = p.anim || {};
        const perKey = new Map();
        for (let oy = Baker._tileOrigin(by0 - padY, ph); oy < by1 + padY; oy += ph)
          for (let ox = Baker._tileOrigin(bx0 - padX, pw); ox < bx1 + padX; ox += pw)
            for (let j = 0; j < pth; j++)
              for (let i = 0; i < ptw; i++) {
                const idx = p.slotGrid[j * ptw + i];
                if (idx === m.empty) continue;
                const ent = p.tiles[String(idx)];
                if (ent === undefined || !this.sprites[ent]) continue;
                const seq = anim[String(idx)];
                const key = seq ? 'a' + idx : 'e' + ent;
                let g = perKey.get(key);
                if (!g) perKey.set(key, g = { ent, seq, verts: [] });
                const x = ox + i * pg, y = oy + j * pg;
                g.verts.push(...Baker._quad(x, y, x + pg, y + pg, 0, 0, 1, 1));
              }
        for (const g of perKey.values()) {
          const run = { tex: this.sprites[g.ent], first: ptotal / 4,
                        count: g.verts.length / 4,
                        parallax: p.parallax || [100, 100],
                        roll: p.roll || [0, 0],
                        period: [pw, ph] };
          if (g.seq) {
            // Resolve entry indices to textures ONCE. A frame whose upload
            // failed leaves a hole, and a hole would draw the previous
            // texture for one frame -- so an incomplete sequence is dropped
            // back to the still frame instead, the same all-or-nothing rule
            // tileset.build_full applies server-side.
            const texes = g.seq.map(e => this.sprites[e]);
            if (texes.every(t => !!t)) run.frames = texes;
          }
          this.planeRuns.push(run);
          pparts.push(g.verts);
          ptotal += g.verts.length;
        }
      }
      this.planeBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.planeBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(pparts, ptotal), gl.STATIC_DRAW);
      this.planeVertBytes = ptotal * 4;

      // Scenery: depth-sorted placements, consecutive same-texture runs
      // merged into one draw. Frame 0 (parity with the window renderer).
      this.sceneRuns = this._placementRuns(this.scenes, 0);
      const sparts = [];
      let stotal = 0;
      for (const r of this.sceneRuns) {
        r.first = stotal / 4;
        sparts.push(r.verts); stotal += r.verts.length; delete r.verts;
      }
      this.sceneBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.sceneBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(sparts, stotal), gl.STATIC_DRAW);
      this.sceneVertBytes = stotal * 4;

      // Covers rebuild on the animation clock; static maps build once here.
      this.coverBuf = gl.createBuffer();
      this._coverSig = null;
      this._rebuildCovers(0);

      this.stats.worldBuildMs = performance.now() - t0;
      this.stats.geometryBytes = this.groundVertBytes + this.planeVertBytes
        + this.sceneVertBytes + (this.coverVertBytes || 0);
      this.stats.drawRuns = this.groundRuns.length + this.planeRuns.length
        + this.sceneRuns.length + (this.coverRuns || []).length;
      return this.stats;
    }

    /** Placements -> depth-ordered texture runs of (px, uv) quads. */
    _placementRuns(list, timeMs) {
      const runs = [];
      let cur = null;
      for (const c of list) {                        // already depth-sorted
        const fi = Baker._frameIndex(timeMs, c.interval, c.frames.length);
        const ent = c.frames[fi];
        const meta = this.spriteMeta && this.spriteMeta[ent];
        const tex = this.sprites[ent];
        if (!meta || !tex) continue;
        const q = Baker._quad(c.x, c.y, c.x + meta.w, c.y + meta.h, 0, 0, 1, 1);
        if (cur && cur.tex === tex) { cur.verts.push(...q); cur.count += 6; }
        else runs.push(cur = { tex, verts: [...q], count: 6 });
      }
      return runs;
    }

    _rebuildCovers(timeMs) {
      // Frame signature: skip the rebuild when no animated cover changed cell.
      const sig = (this.covers || []).map(c =>
        Baker._frameIndex(timeMs, c.interval, c.frames.length)).join(',');
      if (sig === this._coverSig) return;
      this._coverSig = sig;
      const gl = this.gl;
      this.coverRuns = this._placementRuns(this.covers, timeMs);
      const cparts = [];
      let ctotal = 0;
      for (const r of this.coverRuns) {
        r.first = ctotal / 4;
        cparts.push(r.verts); ctotal += r.verts.length; delete r.verts;
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.coverBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(cparts, ctotal), gl.DYNAMIC_DRAW);
      this.coverVertBytes = ctotal * 4;
    }

    _beginWorld(mvp) {
      const gl = this.gl;
      const pr = this._worldProgram();
      gl.useProgram(pr.p);
      gl.uniformMatrix4fv(pr.uMVP, false, mvp);
      gl.uniform3f(pr.uAff, this.cell / 64, this.cell / 32, this.cell * this.k);
      gl.uniform2f(pr.uShift, 0, 0);
      gl.uniform1i(pr.uTex, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.enableVertexAttribArray(pr.aPos);
      gl.enableVertexAttribArray(pr.aUV);
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      return pr;
    }

    _endWorld(pr) {
      const gl = this.gl;
      gl.disableVertexAttribArray(pr.aPos);
      gl.disableVertexAttribArray(pr.aUV);
      gl.enable(gl.DEPTH_TEST);
    }

    _drawRuns(pr, buf, runs) {
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.vertexAttribPointer(pr.aPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(pr.aUV, 2, gl.FLOAT, false, 16, 8);
      for (const r of runs) {
        gl.bindTexture(gl.TEXTURE_2D, r.tex);
        gl.drawArrays(gl.TRIANGLES, r.first, r.count);
      }
    }

    /** The whole map, in world space: planes, ground, scenery. Call from the
     *  scene's ground hook with its MVP. `camPx` is the camera target in
     *  painted-image pixels, for the planes' parallax shift; `timeMs` is
     *  elapsed wall clock, for the rolling ones. */
    drawWorld(mvp, camPx, timeMs) {
      if (!this.groundRuns) return;
      const gl = this.gl;
      const pr = this._beginWorld(mvp);
      gl.disable(gl.BLEND);
      // Planes group by parallax AND ROLL; each group gets its shift once.
      //
      // Roll has to be in the key, not just parallax. `2009-7x` is the map
      // that proves it: its two planes share a parallax (100, 100) AND a
      // period (15360 x 10240) and differ only in roll -- `2009-7x-bg2`
      // rolls (40, 40), `2009-7x-bg1` does not. Keyed on parallax alone the
      // static plane joins the rolling one's group and inherits its shift,
      // so a plane the artist authored as still would scroll.
      let lastP = null, lastR = null;
      for (const r of this.planeRuns) {
        const [px, py] = r.parallax;
        const [rsx, rsy] = r.roll || [0, 0];
        if (!lastP || lastP[0] !== px || lastP[1] !== py
            || lastR[0] !== rsx || lastR[1] !== rsy) {
          lastP = [px, py];
          lastR = [rsx, rsy];
          // WRAP THE PARALLAX SHIFT INTO ONE PLANE PERIOD.
          //
          // The raw shift grows without bound with distance from the map's
          // origin -- on Twin City it reaches ~12,000 px against a plane only
          // 3,072 px wide, nearly four whole widths. The geometry was tiled
          // once at load with a fixed margin, so a shift that large slides it
          // off its intended place and eventually drags the tiled region's
          // edge into view: a background that degrades the further you
          // travel.
          //
          // The plane is exactly periodic -- it IS a tiling -- so shifting by
          // `shift % period` is not an approximation, it is the same picture
          // with the shift kept small. Bounded forever, wherever the player
          // stands.
          const [pw, ph] = r.period || [1, 1];
          const raw = camPx ? [camPx[0] * (100 - px) / 100,
                               camPx[1] * (100 - py) / 100] : [0, 0];
          // ROLL: the plane's own scroll, at `rollSpeed * parallax / 100`
          // PIXELS PER SECOND.
          //
          // The unit is recovered from the original client, not chosen here.
          // The plane object (5017 `Conquer.exe`, consumer at VA 0x474224)
          // computes `rollSpeed * rate * (timeGetTime() - timeBegin) /
          // 100000`, so the divisor is exactly 1000 (ms -> s) x 100 (the
          // percent normaliser), and the result reduces modulo the puzzle's
          // PIXEL extent. `mov esi, 100000` sits at VA 0x47427C, in a window
          // carrying the member displacements +0x120/+0x124 (the roll pair),
          // +0x12c (timeBegin) and +0x130/+0x134 (the rate pair), with
          // `timeGetTime` imported from WINMM.dll.
          //
          // `rate` IS THE PARALLAX, NOT A CONSTANT 100 -- see ROLL_RATE_PCT
          // above for the five-binary measurement. The Init at VA 0x47417F
          // sets both rate members to 100, and then the plane loop's draw
          // call overwrites both from the params struct at VA 0x486A7B,
          // one instruction before `call 0x474224`, with the plane's own
          // parallax pair.
          //
          // Getting this wrong is invisible: at 20 px/s `skybg-move` takes
          // 153.6 s to come round in x, at the correct 6 px/s it takes 512 s,
          // and the same number read per-frame would do it in 2.6 s at 60 fps
          // -- all three look equally "animated".
          //
          // PLUS, not minus, and on the SAME uniform as the parallax term.
          // In the client the block-origin negation and the phase term are
          // algebraically inverse and CANCEL, so a positive rollSpeed moves
          // the artwork along image +x -- the convention that already makes
          // the camera term above correct. Camera-derived parallax and
          // time-derived roll therefore COMBINE by addition rather than
          // competing for the uniform.
          //
          // "Image +x", not "screen right": the original draws the map in 2D
          // where those are the same thing, but here the world is projected
          // isometrically and image +x lands on the screen diagonal
          // (0.894, 0.447). Measured that way -- at 3 s of (40, 0) roll on
          // `l-arena` the artwork moved +10 screen px along that axis
          // against +10.0 predicted. docs/ground_animation.md 10.8.
          //
          // COMPUTED FROM ABSOLUTE ELAPSED TIME, NEVER ACCUMULATED. An
          // accumulator (`shift += roll * dt` each frame) drifts; this is
          // exact in a float64 for centuries -- the largest product it forms
          // is `60 * 100 * dt_ms`, which is still 400x below 2**53 after a
          // hundred years. The original client forms that product in a
          // 32-bit `imul` and overflows after ~11.9 minutes at 60 px/s,
          // making its backdrop visibly JUMP; that is a bug in the shipped
          // client and is deliberately NOT reproduced.
          //
          // The `% pw` / `% ph` below is what keeps it bounded, and it is
          // the same bound the parallax term already needed -- the sum still
          // lands in (-pw, pw), so the tiled geometry's existing margin
          // covers it and nothing about the buffer changes.
          // `px`/`py` here are the SAME two integers the client copies into
          // its rate members, and they are already the draw group's key, so
          // every plane in this group shares them exactly.
          const t = (timeMs || 0) / ROLL_MS_PER_SEC;
          const rx = rsx * px * t / ROLL_RATE_PCT,
                ry = rsy * py * t / ROLL_RATE_PCT;
          gl.uniform2f(pr.uShift, (raw[0] + rx) % pw, (raw[1] + ry) % ph);
          gl.bindBuffer(gl.ARRAY_BUFFER, this.planeBuf);
          gl.vertexAttribPointer(pr.aPos, 2, gl.FLOAT, false, 16, 0);
          gl.vertexAttribPointer(pr.aUV, 2, gl.FLOAT, false, 16, 8);
        }
        gl.bindTexture(gl.TEXTURE_2D, r.tex);
        gl.drawArrays(gl.TRIANGLES, r.first, r.count);
      }
      gl.uniform2f(pr.uShift, 0, 0);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                           gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      this._drawRuns(pr, this.groundBuf, this.groundRuns);
      this._drawRuns(pr, this.sceneBuf, this.sceneRuns);
      this._endWorld(pr);
    }

    /** The COVER layer, over everything. `timeMs` drives the animation. */
    drawCovers(mvp, timeMs) {
      if (!this.coverRuns || !this.coverRuns.length) return;
      const gl = this.gl;
      this._rebuildCovers(timeMs || 0);
      const pr = this._beginWorld(mvp);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                           gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      this._drawRuns(pr, this.coverBuf, this.coverRuns);
      this._endWorld(pr);
    }

    /** Composite the ground window. `rect` is [x0,y0,x1,y1] in painted-image
     *  pixels -- patch.ground.rect verbatim. Everything is resident: backdrop
     *  planes, ground tiles, TERRAIN scenery, in the server renderer's own
     *  painter order. Returns a WebGLTexture the CALLER owns (hand it to
     *  viewer.textures; setMeshes' clear() will delete it), or null when the
     *  rect cannot be baked -- caller falls back. */
    bake(rect) {
      const gl = this.gl;
      if (!this.supported || !this.manifest) return null;
      const [x0, y0, x1, y1] = rect;
      const w = x1 - x0, h = y1 - y0;
      if (w <= 0 || h <= 0 || w > this.maxTex || h > this.maxTex) return null;
      const t0 = performance.now();

      // VOID, the colour an unpainted / off-art pixel has always been.
      const v = this.manifest.void || [12, 12, 14];
      const t = this._beginTarget(w, h, [v[0] / 255, v[1] / 255, v[2] / 255, 1]);
      if (!t) return null;
      this._beginQuads(rect);

      gl.disable(gl.BLEND);                            // planes: opaque floor
      for (const p of this.planes) this._drawPlane(p, rect);

      gl.enable(gl.BLEND);                             // tiles: alpha over
      const groups = this._drawTiles(rect);

      this._drawPlacements(this.scenes, rect, 0);      // scenery: painter order

      this._endQuads();
      this._endTarget(t.saved);
      this.stats.lastBakeMs = performance.now() - t0;
      this.stats.lastGroups = groups;
      return t.out;
    }

    /** The ground slot grid over `rect`, one draw per distinct tile.
     *  Returns the number of draws. Shared by `bake()` and `drawInto()` so
     *  the two cannot drift on which slots they paint.
     *
     *  The vertex data is in painted-image pixels and the rect only enters
     *  through `uRect`, so the geometry depends on the visible SLOT RANGE and
     *  nothing else -- which means it can be built once and reused while that
     *  range holds. It matters: zoomed out, `newplain`'s range is the whole
     *  map and never changes, and rebuilding its 7 210 quads every frame was
     *  MEASURED at 79 ms of an 81 ms frame. Zoomed in, the range is a few
     *  tiles and each rebuild is trivial. */
    _drawTiles(rect) {
      const [x0, y0, x1, y1] = rect;
      const m = this.manifest, G = m.grid;
      const i0 = Math.max(0, Math.floor(x0 / G)), i1 = Math.min(m.pul[0], Math.ceil(x1 / G));
      const j0 = Math.max(0, Math.floor(y0 / G)), j1 = Math.min(m.pul[1], Math.ceil(y1 / G));
      const sig = i0 + ',' + i1 + ',' + j0 + ',' + j1;
      if (!this._runs || this._runSig !== sig) {
        const groups = new Map();                      // tile idx -> vert list
        for (let j = j0; j < j1; j++)
          for (let i = i0; i < i1; i++) {
            const idx = this.slots[j * m.pul[0] + i];
            if (idx === m.empty || !this.tiles.has(idx)) continue;
            let g = groups.get(idx);
            if (!g) groups.set(idx, g = []);
            g.push(...Baker._quad(i * G, j * G, (i + 1) * G, (j + 1) * G,
                                  0, 0, 1, 1));
          }
        this._runs = [...groups].map(([idx, v]) =>
          [this.tiles.get(idx), new Float32Array(v)]);
        this._runSig = sig;
      }
      for (const [tex, verts] of this._runs) this._drawQuads(verts, tex);
      return this._runs.length;
    }

    /** Draw `rect` of the painted image straight into the bound framebuffer,
     *  scaled to whatever the current viewport is. No intermediate texture,
     *  no `maxTex` ceiling -- so the whole of `newplain` (26368 x 17920 art
     *  px, far past any texture limit) draws into an 1400 x 900 canvas in one
     *  pass. This is what the asset viewer's MapEditor uses in place of the
     *  server's per-tile PNG pyramid.
     *
     *  `opts.layers` selects which of background / ground / terrain / cover
     *  to include, in the game's painter order (docs/map_scenery.md 7) --
     *  the editor's per-layer toggles become four booleans rather than four
     *  sets of HTTP requests. `opts.timeMs` picks the animation frame.
     *
     *  Returns false when it cannot draw and the caller must fall back. */
    drawInto(rect, opts) {
      const gl = this.gl;
      if (!this.supported || !this.manifest) return false;
      const o = opts || {};
      const L = o.layers || {};
      const want = k => L[k] !== false;
      if (!(rect[2] > rect[0] && rect[3] > rect[1])) return false;

      const t0 = performance.now();
      const hadDepth = gl.isEnabled(gl.DEPTH_TEST);
      const hadBlend = gl.isEnabled(gl.BLEND);
      gl.disable(gl.DEPTH_TEST);
      this._beginQuads(rect, -1);

      let groups = 0;
      if (want('background')) {
        gl.disable(gl.BLEND);          // planes are the floor, always opaque
        for (const p of this.planes) this._drawPlane(p, rect);
      }
      gl.enable(gl.BLEND);
      if (want('ground')) groups = this._drawTiles(rect);
      if (want('terrain')) this._drawPlacements(this.scenes, rect, o.timeMs || 0);
      if (want('cover')) this._drawPlacements(this.covers, rect, o.timeMs || 0);

      this._endQuads();
      if (hadDepth) gl.enable(gl.DEPTH_TEST);
      if (!hadBlend) gl.disable(gl.BLEND);
      this.stats.lastDrawMs = performance.now() - t0;
      this.stats.lastGroups = groups;
      return true;
    }
  }

  return { Baker, decodeSlots };
})();
