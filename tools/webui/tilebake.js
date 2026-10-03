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
    attribute float aAlpha; // .pux layer mask, per VERTEX -- see _maskQuads
    uniform vec4 uRect;     // x0, y0, 1/w, 1/h of the target rect
    uniform float uFlipY;   // +1 into a texture, -1 straight onto a canvas
    varying vec2 vUV;
    varying float vA;
    void main() {
      vA = aAlpha;
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
    attribute float aAlpha; // .pux layer mask, per VERTEX -- see _maskQuads
    uniform mat4 uMVP;
    uniform vec3 uAff;      // CELL/64, CELL/32, CELL*K
    uniform vec2 uShift;    // parallax shift, image px (planes only)
    varying vec2 vUV;
    varying float vA;
    void main() {
      vA = aAlpha;
      vec2 p = aPos + uShift;
      vec2 w = vec2(p.x * uAff.x + p.y * uAff.y,
                    p.x * uAff.x - p.y * uAff.y - uAff.z);
      gl_Position = uMVP * vec4(w, 0.0, 1.0);
      vUV = aUV;
    }`;
  // `vA` is the .pux LAYER MASK, interpolated by the raster between the four
  // corners of a sub-quad -- the same job the client's own `AlphaAt` vertex
  // colours do (`docs/pux_f1_f2_attribution_2026-09-06.md` 2.4). It is 1.0 for
  // every other draw path: the attribute ARRAY is disabled there and the
  // generic vertex attribute is set to 1.0 explicitly, because WebGL's own
  // default for a disabled attribute is (0,0,0,1) -- x = 0 -- which would make
  // every plane, sprite and cover fully transparent.
  // `uTint` is the `.OtherData` per-cover modulation: (r, g, b, a) in 0..1,
  // NEUTRAL (1,1,1,1) for every draw path that does not set it. It multiplies
  // the sampled texel, which is the same thing `otherdata.apply_tint` does on
  // the PNG path -- ALPHA MULTIPLIES the sprite's own alpha rather than
  // replacing it, so a cover's transparent margin stays transparent.
  //
  // A uniform rather than a vertex attribute because sprites are drawn one
  // placement per call here (`_drawPlacements`), so there is nothing to batch
  // across and an attribute would cost a buffer upload per sprite for a value
  // that is constant over its four vertices.
  const FS = `
    precision mediump float;
    uniform sampler2D uTex;
    uniform vec4 uTint;
    varying vec2 vUV;
    varying float vA;
    void main() {
      gl_FragColor = texture2D(uTex, vUV) * uTint;
      gl_FragColor.a *= vA;
    }`;

  /** The manifest's slot grid: base64 of little-endian u32s.
   *
   *  **u32 since 2026-08-29** -- `tileset.SLOT_BYTES`. It was u16, which
   *  matched the .pul on disk and stopped matching the manifest once
   *  `puzzle.py` began minting synthetic ids at 1<<20 for a .pux tile's
   *  layer stack; four maps in the corpus carry them and none of them could
   *  be built at all. The single decoder serves both the ground grid and
   *  every plane's, so the two cannot drift on width.
   *
   *  `>>> 0` because `<< 24` on a byte >= 0x80 is negative in JS's signed
   *  32-bit bitwise ops, and Uint32Array would store the wrap silently --
   *  a slot grid that mis-decodes renders a plausible-looking WRONG map,
   *  which is the failure you cannot see. */
  /** The `.pux` layer mask's geometry: 5x5 vertices bounding a 4x4 quad
   *  subdivision of one tile, 25 bits, `row*5+col`. `core/dmap.py`'s
   *  PUX_MASK_SIDE / PUX_MASK_QUADS / PUX_MASK_FULL, mirrored -- a mask this
   *  file read with a different side length would draw a plausible-looking
   *  WRONG blend, which is the failure you cannot see. */
  const PUX_MASK_SIDE = 5;
  const PUX_MASK_QUADS = PUX_MASK_SIDE - 1;
  const PUX_MASK_FULL = (1 << (PUX_MASK_SIDE * PUX_MASK_SIDE)) - 1;

  function decodeSlots(b64) {
    const raw = atob(b64);
    const out = new Uint32Array(raw.length / 4);
    for (let i = 0; i < out.length; i++)
      out[i] = (raw.charCodeAt(i * 4) | (raw.charCodeAt(i * 4 + 1) << 8)
             | (raw.charCodeAt(i * 4 + 2) << 16)
             | (raw.charCodeAt(i * 4 + 3) << 24)) >>> 0;
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
      // NEUTRALISE `uTint` AT LINK TIME. An unset WebGL uniform is (0,0,0,0),
      // not (1,1,1,1), so `texture2D(...) * uTint` renders everything BLACK
      // for any caller that does not set it. `_drawQuads` sets it per draw,
      // but NOT every caller goes through `_drawQuads`:
      // `tests/test_tilebake_render.py` drives this program with
      // `gl.drawArrays` directly against a real WebGL context, and adding the
      // uniform took 5 of its 11 arms red -- a full mask "left clear pixels",
      // a partial mask "covered nothing", the two transposed masks drawing to
      // the same place. All of it black.
      //
      // A uniform's value persists on the PROGRAM, so setting it once here
      // makes NEUTRAL the program's own default and fixes every direct caller
      // that exists and every one written later. The alternative -- making
      // each caller set it -- is a requirement nobody can see from the call
      // site, and the next direct caller would break exactly the same way.
      const prev = gl.getParameter(gl.CURRENT_PROGRAM);
      gl.useProgram(p);
      const ut = gl.getUniformLocation(p, 'uTint');
      if (ut) gl.uniform4f(ut, 1, 1, 1, 1);
      gl.useProgram(prev);        // leave the caller's binding as we found it
      return { p,
               aPos: gl.getAttribLocation(p, 'aPos'),
               aUV: gl.getAttribLocation(p, 'aUV'),
               aAlpha: gl.getAttribLocation(p, 'aAlpha'),
               uRect: gl.getUniformLocation(p, 'uRect'),
               uFlipY: gl.getUniformLocation(p, 'uFlipY'),
               uMVP: gl.getUniformLocation(p, 'uMVP'),
               uAff: gl.getUniformLocation(p, 'uAff'),
               uShift: gl.getUniformLocation(p, 'uShift'),
               uTint: gl.getUniformLocation(p, 'uTint'),
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
      // The .DMap's SECOND record list. Absent from this bundle until
      // 2026-09-15, so the GPU path could not draw a layer the PNG path drew.
      this.interactive = manifest.interactive || [];
      // `TerrainLayer0.Puzzle*` -- one tint for the whole main ground. The
      // per-plane ones live on each `planes[]` record as `tint`, because they
      // come from a DIFFERENT section (`SceneLayerN`) and a different surface.
      this.groundTint = manifest.groundTint || null;
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
      this.interactive = [];
      this.groundTint = null;
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
    /** `alphas` is the OPTIONAL per-vertex `.pux` layer mask, one float per
     *  vertex, in the same order as `verts`. Ground runs pass it; every other
     *  caller omits it and gets a constant 1.0.
     *
     *  **The constant is not optional and must not be left to WebGL.** A
     *  disabled attribute defaults to `(0, 0, 0, 1)`, so `aAlpha` would read
     *  ZERO and the run would draw fully transparent -- the same trap
     *  `_drawRuns` documents. `_bindAlpha(pr, null)` sets it explicitly. */
    _drawQuads(verts, tex, prog, alphas, tint) {
      const gl = this.gl, pr = prog || this.prog;
      gl.bindTexture(gl.TEXTURE_2D, tex);
      // SET EVERY TIME, never only when tinted: a uniform persists on the
      // program, so one tinted sprite would colour every sprite drawn after
      // it. That failure looks like a rendering bug anywhere except where it
      // was caused, which is the expensive kind.
      if (pr.uTint) {
        if (tint) gl.uniform4f(pr.uTint, tint[0] / 255, tint[1] / 255,
                                         tint[2] / 255, tint[3] / 255);
        else gl.uniform4f(pr.uTint, 1, 1, 1, 1);
      }
      if (alphas && alphas.length === verts.length / 4) {
        if (!this._aBuf) this._aBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this._aBuf);
        gl.bufferData(gl.ARRAY_BUFFER, alphas, gl.STREAM_DRAW);
        this._bindAlpha(pr, this._aBuf);
      } else {
        // Includes the length-mismatch case ON PURPOSE: a mask array that
        // does not match the geometry would blend by the wrong vertices,
        // which looks plausible and is wrong. Falling back to opaque is the
        // visible failure rather than the convincing one.
        this._bindAlpha(pr, null);
      }
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

    /** One `.pux` layer's quads over a tile rect, carrying its VERTEX MASK.
     *
     *  A layer entry is a terrain row plus a 25-bit mask over the 5x5 grid of
     *  vertices bounding a 4x4 subdivision of the tile -- the client's own
     *  `AlphaAt` at `Clients/7878/Env_DX9/Conquer.exe` RVA 0x872A68 turns bit
     *  `row*5+col` into an alpha of 255 or 0 and hands it to the raster as a
     *  vertex colour (`docs/pux_f1_f2_attribution_2026-09-06.md`). So the tile
     *  is emitted as up to 16 sub-quads whose corner alphas the raster
     *  interpolates, which is what makes one terrain blend into another
     *  instead of the topmost opaque layer covering everything under it.
     *
     *  Returns `{ v, a }` -- vertices in `_quad`'s layout and ONE alpha per
     *  vertex, `a.length === v.length / 4`.
     *
     *  TWO SHORT CIRCUITS, and both are the common case rather than
     *  micro-optimisation: a FULL mask (0x1FFFFFF, the single most common
     *  value in the corpus, and what every layer of every map with no mask at
     *  all is given) emits the one quad this function's caller emitted before
     *  any of this existed, byte for byte; and a sub-quad whose four corners
     *  are all clear is dropped, because it would draw nothing.
     *
     *  Mirrors `core/dmap.pux_mask_field` -- same 5x5 vertices, same
     *  `row*5+col`. The FILL differs and it is not hidden: this emits two
     *  triangles per sub-quad and the raster interpolates them linearly
     *  (Gouraud), while `pux_mask_field` fills the same square bilinearly. The
     *  two agree at the vertices and along every sub-quad edge and differ in
     *  the interiors. The client's own fill is now traced: it is Gouraud on the
     *  **anti-diagonal** (`docs/pux_interp_diagonal_2026-09-10.md`), which is
     *  the split this function emits; `pux_mask_field`'s bilinear is the smooth
     *  approximation. `tests/test_tilebake_exec.py` compares the two where they
     *  must agree (vertices and edges), not in the interiors. */
    static _maskQuads(x0, y0, x1, y1, mask) {
      if ((mask & PUX_MASK_FULL) === PUX_MASK_FULL || !(mask & PUX_MASK_FULL))
        return { v: Baker._quad(x0, y0, x1, y1, 0, 0, 1, 1),
                 a: (mask & PUX_MASK_FULL) ? [1, 1, 1, 1, 1, 1]
                                           : [0, 0, 0, 0, 0, 0] };
      const v = [], a = [];
      const w = (x1 - x0) / PUX_MASK_QUADS, h = (y1 - y0) / PUX_MASK_QUADS;
      const bit = (c, r) => (mask >>> (r * PUX_MASK_SIDE + c)) & 1;
      for (let r = 0; r < PUX_MASK_QUADS; r++)
        for (let c = 0; c < PUX_MASK_QUADS; c++) {
          const a00 = bit(c, r), a10 = bit(c + 1, r),
                a01 = bit(c, r + 1), a11 = bit(c + 1, r + 1);
          if (!(a00 || a10 || a01 || a11)) continue;
          const qx0 = x0 + c * w, qy0 = y0 + r * h;
          const qx1 = qx0 + w, qy1 = qy0 + h;
          const u0 = c / PUX_MASK_QUADS, u1 = (c + 1) / PUX_MASK_QUADS;
          const t0 = r / PUX_MASK_QUADS, t1 = (r + 1) / PUX_MASK_QUADS;
          // THE ANTI-DIAGONAL split, NOT `_quad`'s main diagonal. The client
          // (Clients/7878/Env_DX9/Conquer.exe, caller at 0x87B26A) emits each
          // sub-quad as two triangles (v,u)(v,u+1)(v+1,u) and
          // (v,u+1)(v+1,u+1)(v+1,u) -- both carry (v,u+1) and (v+1,u), so the
          // shared edge is TR--BL, the anti-diagonal. `_quad` shares TL--BR and
          // would split every subdivided sub-quad the wrong way, a wrong
          // interior on the ~55% of drawn sub-quads whose corners are not
          // co-planar. `docs/pux_interp_diagonal_2026-09-10.md`. The per-vertex
          // alpha array below MUST follow the same six-vertex order.
          v.push(qx0, qy0, u0, t0,  qx1, qy0, u1, t0,  qx0, qy1, u0, t1,   // TL TR BL
                 qx1, qy0, u1, t0,  qx1, qy1, u1, t1,  qx0, qy1, u0, t1);  // TR BR BL
          a.push(a00, a10, a01,  a10, a11, a01);
        }
      return { v, a };
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
      //: The MAP's painted-image extent, which the plane is clipped to below.
      const MW = this.manifest.pixels[0], MH = this.manifest.pixels[1];
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
              // CLIP TO THE MAP'S OWN EXTENT. A backdrop is a backdrop FOR A
              // MAP: it repeats across whatever rect it is asked for, and the
              // rect at fit zoom is bigger than the map, so unclipped tiles
              // painted into the void OUTSIDE the art -- fragments of wall and
              // railing floating beside the map, which is content that can
              // never appear in game.
              //
              // Measured on 2024thx_new over art x -4096..-1024 (entirely off
              // the map): 954,220 non-void pixels drawn with planes, 0 with
              // `planes = []`. 2024xmas_new was clean only because it ships no
              // plane at all.
              //
              // The UVs are clipped with the rect rather than the quad being
              // dropped, so a tile straddling the edge keeps its remaining
              // part in register instead of shifting.
              const cx0 = Math.max(wx0, 0), cy0 = Math.max(wy0, 0);
              const cx1 = Math.min(wx0 + G, MW), cy1 = Math.min(wy0 + G, MH);
              if (cx1 <= cx0 || cy1 <= cy0) continue;
              let g = groups.get(ent);
              if (!g) groups.set(ent, g = []);
              g.push(...Baker._quad(cx0, cy0, cx1, cy1,
                                    (cx0 - wx0) / G, (cy0 - wy0) / G,
                                    (cx1 - wx0) / G, (cy1 - wy0) / G));
            }
      // `SceneLayerN.Puzzle*` tints backdrop plane N. The value rides on the
      // plane's own manifest record, so selecting one plane cannot pick up a
      // neighbour's colour -- `sdragon01_new` carries four different ones.
      for (const [ent, verts] of groups)
        this._drawQuads(new Float32Array(verts), this.sprites[ent],
                        null, null, p.tint);
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
        this._drawQuads(v, tex, null, null, c.tint);
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
      this._bindAlpha(pr, null);
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

    /** The drawable layers of a slot, bottom first, as [depth, tileIndex].
     *
     * A `.pux` slot may be a STACK: `tileset.py` gives it a synthetic index,
     * ships the terrain ROWS it is built from, and lists them here bottom
     * first. A single-layer slot is its own only layer at depth 0, so both
     * kinds go through one path and the caller never branches on which it has.
     *
     * **The per-layer mask IS consulted since 2026-09-06.** A manifest entry
     * is `[terrainRow, mask]`, the mask being the 25-bit per-vertex alpha the
     * client reads (`docs/pux_f1_f2_attribution_2026-09-06.md`); `_maskQuads`
     * turns it into geometry. Before this, layers were stacked full-tile and
     * the topmost opaque one covered everything beneath it.
     *
     * A bare number is still accepted in that slot and read as a FULL mask, so
     * a manifest cached by a page from before the change renders exactly as it
     * did rather than going blank.
     *
     * Returns [] for a slot whose art did not ship, which is what `empty` and
     * an unresolved tile have always done.
     */
    _layersOf(idx) {
      const m = this.manifest;
      if (idx === m.empty) return [];
      const st = m.stacks && m.stacks[idx];
      if (st) {
        const out = [];
        for (let d = 0; d < st.length; d++) {
          const e = st[d];
          const tile = (typeof e === 'number') ? e : e[0];
          const mask = (typeof e === 'number') ? PUX_MASK_FULL : (e[1] >>> 0);
          if (this.tiles.has(tile)) out.push([d, tile, mask]);
        }
        return out;
      }
      return this.tiles.has(idx) ? [[0, idx, PUX_MASK_FULL]] : [];
    }

    /** Build the static geometry for the whole map. `cell` is the world
     *  units per map cell (play.js's CELL). Call after load(). */
    buildWorld(cell) {
      const gl = this.gl;
      const m = this.manifest;
      this.cell = cell;
      this.k = m.pixels[0] / 64;
      const t0 = performance.now();



      // Ground: every painted slot, grouped by (depth, tile). A STACKED slot
      // contributes one quad per layer, and grouping by DEPTH FIRST is what
      // makes the draw order correct globally -- every slot's layer 0 is drawn
      // before any slot's layer 1, so a deep stack never paints under a
      // shallow one. Max depth measured on 2025tsf_new is 13.
      const groups = new Map();
      const G = m.grid, [tw, th] = m.pul;
      for (let j = 0; j < th; j++)
        for (let i = 0; i < tw; i++) {
          const idx = this.slots[j * tw + i];
          for (const [d, tile, mask] of this._layersOf(idx)) {
            const key = d * 4294967296 + tile;
            let g = groups.get(key);
            if (!g) groups.set(key, g = { d, tile, v: [], a: [] });
            // A partially-masked layer becomes up to 16 sub-quads with
            // per-vertex alpha; a full mask stays the single quad this loop
            // has always emitted. Grouping is unchanged -- the sub-quads of
            // one tile go into the same (depth, tile) run as the whole quad
            // did, so run order, run count and the depth-first painter order
            // below are all exactly as before.
            const q = Baker._maskQuads(i * G, j * G, (i + 1) * G, (j + 1) * G,
                                       mask);
            g.v.push(...q.v);
            g.a.push(...q.a);
          }
        }
      const runs = [];
      const parts = [];
      const aparts = [];
      let total = 0;
      for (const g of [...groups.values()].sort((a, b) => a.d - b.d)) {
        runs.push({ tex: this.tiles.get(g.tile), first: total / 4,
                    count: g.v.length / 4 });
        parts.push(g.v);
        aparts.push(g.a);
        total += g.v.length;
      }
      this.groundRuns = runs;
      this.groundBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.groundBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(parts, total), gl.STATIC_DRAW);
      // ONE FLOAT PER VERTEX, in its own buffer rather than a fifth component
      // of the shared 16-byte vertex: `aPos`/`aUV` at stride 16 is the format
      // FIVE draw paths bind (ground, planes, scenery, covers, the window
      // bake), and widening it would have touched all five to serve one.
      this.groundAlphaBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.groundAlphaBuf);
      gl.bufferData(gl.ARRAY_BUFFER, Baker._pack(aparts, total / 4),
                    gl.STATIC_DRAW);
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

    /** `aAlpha` from `buf`, or a constant 1.0 when there is no buffer.
     *  One place, so "disabled means 1.0, never the WebGL default" is stated
     *  once rather than at each of the binding sites. */
    _bindAlpha(pr, buf) {
      const gl = this.gl;
      if (pr.aAlpha === undefined || pr.aAlpha < 0) return;
      if (buf) {
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.enableVertexAttribArray(pr.aAlpha);
        gl.vertexAttribPointer(pr.aAlpha, 1, gl.FLOAT, false, 0, 0);
      } else {
        gl.disableVertexAttribArray(pr.aAlpha);
        gl.vertexAttrib1f(pr.aAlpha, 1.0);
      }
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
      this._bindAlpha(pr, null);
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      return pr;
    }

    _endWorld(pr) {
      const gl = this.gl;
      gl.disableVertexAttribArray(pr.aPos);
      gl.disableVertexAttribArray(pr.aUV);
      // The ground run may have left the mask array enabled. Leaving an array
      // enabled that points into a freed buffer is how a later unrelated draw
      // reads garbage, and gl.js shares this context.
      this._bindAlpha(pr, null);
      gl.enable(gl.DEPTH_TEST);
    }

    _drawRuns(pr, buf, runs, alphaBuf) {
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.vertexAttribPointer(pr.aPos, 2, gl.FLOAT, false, 16, 0);
      gl.vertexAttribPointer(pr.aUV, 2, gl.FLOAT, false, 16, 8);
      // The per-vertex `.pux` layer mask, GROUND ONLY. Every other run has to
      // put the attribute back to a constant 1.0, and MUST NOT rely on WebGL's
      // own default for a disabled attribute -- that is (0,0,0,1), so x is
      // ZERO and the run would draw fully transparent.
      this._bindAlpha(pr, alphaBuf);
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
      this._drawRuns(pr, this.groundBuf, this.groundRuns,
                     this.groundAlphaBuf);
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
      // THIS PATH DISCARDED THE `.pux` MASK UNTIL 2026-09-14, and the comment
      // on `buildWorld` -- "the per-layer mask IS consulted since 2026-09-06"
      // -- was true of the WORLD path only. `_layersOf` has returned
      // `[d, tile, mask]` since that change; this loop destructured
      // `[d, tile]` and called `_quad`, so every ground layer drew as one
      // fully opaque tile-sized quad and the topmost covered everything under
      // it. `drawInto` is what `mapedit.js:199` calls, so the Map Editor drew
      // HARD SEAMS wherever the art specifies a blend -- 72.1% of 2024xmas's
      // 4,101 layers carry a PARTIAL mask.
      //
      // Measured before the fix, tile (20,0) of 2024xmas_new, whose layer 538
      // specifies a clean left(255) -> right(0) ramp:
      //
      //     forcing every mask to FULL      0 of 65,536 pixels changed
      //     CONTROL, swapping the tile  65,536 of 65,536 pixels changed
      //
      // The control is why the 0 is evidence and not a broken probe.
      if (!this._runs || this._runSig !== sig) {
        const groups = new Map();      // depth<<32|tile -> {d,tile,v,a}
        for (let j = j0; j < j1; j++)
          for (let i = i0; i < i1; i++) {
            const idx = this.slots[j * m.pul[0] + i];
            for (const [d, tile, mask] of this._layersOf(idx)) {
              const key = d * 4294967296 + tile;
              let g = groups.get(key);
              if (!g) groups.set(key, g = { d, tile, v: [], a: [] });
              const q = Baker._maskQuads(i * G, j * G,
                                         (i + 1) * G, (j + 1) * G, mask);
              g.v.push(...q.v);
              g.a.push(...q.a);
            }
          }
        this._runs = [...groups.values()].sort((a, b) => a.d - b.d).map(g =>
          [this.tiles.get(g.tile), new Float32Array(g.v),
           new Float32Array(g.a)]);
        this._runSig = sig;
      }
      // `TerrainLayer0.Puzzle*` -- ONE tint for the whole main ground, from a
      // different section and a different surface than the per-plane ones.
      for (const [tex, verts, alphas] of this._runs)
        this._drawQuads(verts, tex, null, alphas, this.groundTint);
      return this._runs.length;
    }

    /** Draw `rect` of the painted image straight into the bound framebuffer,
     *  scaled to whatever the current viewport is. No intermediate texture,
     *  no `maxTex` ceiling -- so the whole of `newplain` (26368 x 17920 art
     *  px, far past any texture limit) draws into an 1400 x 900 canvas in one
     *  pass. This is what the asset viewer's MapEditor uses in place of the
     *  server's per-tile PNG pyramid.
     *
     *  `opts.layers` selects which layers to include, keyed by LAYER ID, and
     *  `opts.order` is the order to draw them in, furthest first. An id is
     *  `background:N` for one backdrop plane or a bare `ground` / `terrain` /
     *  `cover`; bare `background` still means every plane, which is what the
     *  pre-2026-09-14 callers passed and what `bake()` does.
     *
     *  WHY AN ORDER AND NOT FOUR BOOLEANS. The composite order here was
     *  hard-coded to planes -> tiles -> scenes -> covers, so the Map Editor
     *  could show you a map but not ask a question about it. The owner's
     *  question is exactly which order the client really uses, and the only
     *  instrument for that is a human eye plus the ability to try one --
     *  `docs/map_scenery.md` 7 is a reading of the composite, not a
     *  measurement of it. Passing the order in means the panel's order IS the
     *  draw order, with no second copy of the rule in this file.
     *
     *  With no `order`, the default is the old fixed sequence, so every
     *  existing caller (`bake()`, the tests, an older page against a newer
     *  file) draws byte for byte what it drew before.
     *
     *  `opts.timeMs` picks the animation frame.
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
      const order = (o.order && o.order.length) ? o.order : Baker.DEFAULT_ORDER;
      gl.enable(gl.BLEND);
      for (const id of order) {
        if (!want(id)) continue;
        const cut = id.indexOf(':');
        const kind = cut < 0 ? id : id.slice(0, cut);
        if (kind === 'background') {
          // **PLANES ARE NOT ALL OPAQUE, AND THIS USED TO ASSUME THEY WERE.**
          // The old comment here read "Planes are the FLOOR: opaque, blend
          // off", and disabled blending for the whole pass. That is true of a
          // bottom sky plane and false of the ones above it. MEASURED on
          // `zsjx03_new`, which is where the owner reported it:
          //
          //     plane 0  zsjx03-bg02.pul  320 tiles, 0 with partial alpha
          //     plane 1  zsjx03-bg01.pul  122 tiles, 36 with >2% PARTIAL,
          //                               worst tile 83.1% partial
          //
          // With BLEND off, a half-transparent cloud texel overwrites the sky
          // at full strength, so a feathered tile lands as a SOLID RECTANGLE
          // -- the owner's "transparency blocks that aren't transparent".
          //
          // Enabling blending does NOT cost the drag-on-top behaviour the old
          // comment was protecting: an alpha-255 texel still replaces the
          // destination completely under SRC_ALPHA/ONE_MINUS_SRC_ALPHA. The
          // only pixels whose result changes are the ones that were WRONG.
          const n = cut < 0 ? -1 : parseInt(id.slice(cut + 1), 10);
          const ps = cut < 0 ? this.planes
                   : (this.planes[n] ? [this.planes[n]] : []);
          for (const p of ps) this._drawPlane(p, rect);
        } else if (kind === 'ground') {
          groups = this._drawTiles(rect);
        } else if (kind === 'terrain') {
          this._drawPlacements(this.scenes, rect, o.timeMs || 0);
        } else if (kind === 'cover') {
          this._drawPlacements(this.covers, rect, o.timeMs || 0);
        } else if (kind === 'interactive') {
          this._drawPlacements(this.interactive, rect, o.timeMs || 0);
        }
        // `passability` is deliberately not here: it is drawn from the cell
        // grid on the 2D canvas and has no texture anywhere in it.
      }

      this._endQuads();
      if (hadDepth) gl.enable(gl.DEPTH_TEST);
      if (!hadBlend) gl.disable(gl.BLEND);
      this.stats.lastDrawMs = performance.now() - t0;
      this.stats.lastGroups = groups;
      return true;
    }
  }

  /** `drawInto`'s order when a caller passes none: the composite order
   *  `terrain.art_texture()` uses, with `background` meaning every plane.
   *  Named rather than inlined so a test can assert the default IS the old
   *  sequence -- "reordering is possible" must not mean "the default moved".
   */
  Baker.DEFAULT_ORDER = ['background', 'ground', 'terrain', 'cover',
                         'interactive'];

  return { Baker, decodeSlots };
})();
