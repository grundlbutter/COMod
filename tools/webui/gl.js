/* gl.js -- the 3D viewport for the CO asset viewer.
 *
 * Hand-written WebGL 1. No three.js, no CDN, nothing to vendor: the whole
 * renderer is ~450 lines because the job is narrow -- draw a handful of
 * triangle-list meshes with one texture each, using the engine's own
 * conventions.
 *
 * The conventions that matter, and where they come from (docs/modding.md 9):
 *
 *   Winding.  C3 triangles are wound D3D clockwise-front in a left-handed,
 *   Z-points-DOWN frame.  The server converts positions to (x, y, -z) and
 *   leaves the index order untouched; a single-axis mirror flips handedness,
 *   so in this right-handed Z-up frame the shipped order is counter-clockwise
 *   front-facing.  Hence gl.frontFace(CCW) + gl.cullFace(BACK) reproduces the
 *   engine's culling exactly.  Meshes carrying the "2SID" chunk tag are drawn
 *   with culling off, which is what that tag means.
 *
 *   Matrix.  Already applied server-side (the engine applies it at load time).
 *
 *   UVs.  D3D's V axis runs top-down and so does PNG row order, and we do NOT
 *   set UNPACK_FLIP_Y_WEBGL, so v=0 is the top row -- no flip anywhere.
 */

'use strict';

// --------------------------------------------------------------- mat4 utils
const M4 = {
  ident() { return new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]); },
  mul(a, b) { // returns a*b (column-major, GL convention)
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  },
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0]);
  },
  /** Orthographic, given half-extents. Conquer Online's world is pre-rendered
   *  2D art at a fixed 2:1 isometric projection, and pre-rendered art has no
   *  perspective divergence: under a perspective camera a character at the
   *  screen edge is viewed from a different angle than the ground beneath
   *  them and shears against it. See ISO_YAW below. */
  ortho(halfW, halfH, near, far) {
    const nf = 1 / (near - far);
    return new Float32Array([
      1 / halfW, 0, 0, 0,
      0, 1 / halfH, 0, 0,
      0, 0, 2 * nf, 0,
      0, 0, (far + near) * nf, 1]);
  },
  lookAt(eye, ctr, up) {
    const z = norm(sub(eye, ctr));
    let x = cross(up, z);
    if (len(x) < 1e-6) x = cross([0, 1, 0], z);
    x = norm(x);
    const y = cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
  },
};
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = a => Math.hypot(a[0], a[1], a[2]);
const norm = a => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

// --------------------------------------------------------------- shaders
const VS = `
precision highp float;
attribute vec3 aPos;
attribute vec3 aNormal;
attribute vec2 aUV0;
attribute vec4 aColor;
uniform mat4 uMVP;
uniform mat4 uModel;
uniform vec2 uUVOffset;    // flipbook cell / STEP scroll -- docs/effects.md 6.4
varying vec3 vNormal;
varying vec2 vUV;
varying vec4 vColor;
varying vec3 vWorld;
void main() {
  vNormal = mat3(uModel) * aNormal;
  vUV = aUV0 + uUVOffset;
  vColor = aColor;
  vWorld = (uModel * vec4(aPos, 1.0)).xyz;
  gl_Position = uMVP * vec4(aPos, 1.0);
}`;

const FS = `
precision highp float;
varying vec3 vNormal;
varying vec2 vUV;
varying vec4 vColor;
varying vec3 vWorld;
uniform sampler2D uTex;
uniform int  uHasTex;
uniform int  uMode;        // 0 unlit, 1 lit, 2 normals, 3 uv checker
uniform int  uUseVColor;
uniform int  uAlphaMode;   // 0 blend, 1 test, 2 opaque
uniform float uCutoff;
uniform float uMeshAlpha;  // C3Key alpha for the current frame
uniform vec3  uFlat;       // solid colour for wireframe / untextured
uniform int   uSolid;
void main() {
  if (uSolid == 1) { gl_FragColor = vec4(uFlat, 1.0); return; }

  vec4 c = vec4(1.0);
  if (uHasTex == 1) c = texture2D(uTex, vUV);
  if (uUseVColor == 1) c *= vColor;

  if (uMode == 2) {
    gl_FragColor = vec4(normalize(vNormal) * 0.5 + 0.5, 1.0);
    return;
  }
  if (uMode == 3) {
    vec2 g = floor(vUV * 16.0);
    float chk = mod(g.x + g.y, 2.0);
    gl_FragColor = vec4(mix(vec3(0.22), vec3(0.72), chk) * vec3(1.0, 0.95, 0.85), 1.0);
    return;
  }
  if (uMode == 1) {
    vec3 n = normalize(vNormal);
    vec3 l = normalize(vec3(0.4, 0.75, 0.55));
    float d = max(dot(n, l), 0.0);
    c.rgb *= (0.42 + 0.58 * d);
  }

  c.a *= uMeshAlpha;
  if (uAlphaMode == 2) c.a = 1.0;
  else if (uAlphaMode == 1) { if (c.a < uCutoff) discard; c.a = 1.0; }
  else if (c.a < 0.004) discard;
  gl_FragColor = c;
}`;

// --------------------------------------------------------------- viewer

/** The yaw a character is looked at from by default.
 *
 *  The corpus is authored facing -Y, so a camera at positive yaw is behind the
 *  model. This value is shared with `tools/thumbs.py`'s renderer so a thumbnail
 *  and the viewport show the same side. See the note in the constructor. */
const DEFAULT_YAW = -0.9;
/** The yaw that used to be the default, and was wrong. A stored camera sitting
 *  exactly on it was never orbited by the user -- it is the old default written
 *  through -- so it is migrated rather than respected. A camera the user
 *  actually moved will not land on it to seven decimal places. */
const LEGACY_DEFAULT_YAW = 0.9;

/* ---------------------------------------------------------------- the game camera
 *
 * Conquer Online is 2D art with 3D characters composited on it, seen from a
 * camera that never moves. The camera is therefore not a taste decision: the
 * art already fixes it, through the placement rule in `docs/ground_art.md`
 *
 *     px = (gx - gy + K) * 32        py = (gx + gy - K) * 16
 *
 * i.e. one map cell is a 64 x 32 pixel diamond -- a 2:1 dimetric projection.
 * Three things follow, and all three are derived rather than chosen.
 *
 * ORTHOGRAPHIC.  A pre-rendered ground plane has one projection everywhere.
 * Perspective gives the ground a vanishing point the painted art does not
 * have, so a character's feet drift off their cell as they walk away from the
 * screen centre. Ortho is the only projection that can agree with the art at
 * every screen position.
 *
 * YAW = -PI/4.  `lookAt` builds screen-right as `normalize(cross(Z, dir))`,
 * which is `(-sin(yaw), cos(yaw), 0)`. `tools/terrain.py` puts map cell
 * (cx, cy) at world (cx*CELL, -cy*CELL), and the art sends +x cell to
 * screen-right-and-down and +y cell to screen-left-and-down, so screen-right
 * must be world (1, 1, 0)/sqrt(2). That needs -sin(yaw) = cos(yaw) = 1/sqrt(2),
 * i.e. yaw = -PI/4. The sign also lands the camera on -Y, which is the side
 * the character corpus is authored facing (see DEFAULT_YAW above) -- the two
 * constraints agree, which is a good sign that neither is inverted.
 *
 * PITCH = asin(1/2) = 30 deg.  With yaw at 45 degrees a one-cell ground step
 * projects to `(CELL/sqrt2, -CELL*sin(pitch)/sqrt2)`, so the on-screen ratio
 * of vertical to horizontal travel is exactly `sin(pitch)`. The art wants
 * 16/32 = 1/2, hence `asin(1/2)`.
 *
 * **Not `atan(1/2)`.**  26.565 deg is the angle the cell axes make *on the
 * screen* -- it is `atan(16/32)`, a property of the finished picture -- and it
 * is a tempting but wrong value for the camera: it yields a ratio of 0.4472
 * instead of 0.5, an 11% vertical squash. 35.264 deg (`asin(1/sqrt3)`) is true
 * isometric and gives 0.5774. Only `asin(1/2)` reproduces the shipped art.
 * `tools/test_viewer.py::IsometricCamera` pins all three numbers.
 */
const ISO_YAW = -Math.PI / 4;
const ISO_PITCH = Math.asin(0.5);

class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    const opts = { antialias: true, alpha: false, premultipliedAlpha: false,
                   preserveDrawingBuffer: true };
    this.gl = canvas.getContext('webgl', opts) || canvas.getContext('experimental-webgl', opts);
    if (!this.gl) throw new Error('WebGL is not available in this browser');
    const gl = this.gl;
    this.prog = this._program(VS, FS);
    this.attr = {
      pos: gl.getAttribLocation(this.prog, 'aPos'),
      nrm: gl.getAttribLocation(this.prog, 'aNormal'),
      uv: gl.getAttribLocation(this.prog, 'aUV0'),
      col: gl.getAttribLocation(this.prog, 'aColor'),
    };
    this.uni = {};
    for (const n of ['uMVP', 'uModel', 'uTex', 'uHasTex', 'uMode', 'uUseVColor',
                     'uAlphaMode', 'uCutoff', 'uMeshAlpha', 'uFlat', 'uSolid',
                     'uUVOffset'])
      this.uni[n] = gl.getUniformLocation(this.prog, n);

    this.meshes = [];        // {gpu buffers, meta}
    this.fx = [];            // EffectInstance -- see fx.js
    this.fxTime = 0;
    this.textures = new Map();
    this.white = this._solidTexture([255, 255, 255, 255]);

    this.opts = { cull: 'game', alpha: 'blend', shade: 'unlit', wire: false,
                  sockets: false, grid: true, vcolor: false, cutoff: 0.5, frame: 0,
                  lock: false,
                  // 'perspective' is the asset viewer's; 'iso' is the game's
                  // and is fixed -- see ISO_YAW. `setIsoCamera()` selects it.
                  projection: 'perspective', fixedCamera: false };
    this.center = [0, 0, 0];
    this.radius = 100;
    // **The corpus is authored facing -Y.** `_basis()` puts the eye along
    // `(cos yaw, sin yaw)`, so a positive yaw stands the camera at +Y and looks
    // at the back of every character. The default was +0.9 and had been since
    // the beginning; it went unnoticed because every saved shot was taken from
    // a camera the author had already orbited, which masked it.
    // -0.9 is the same elevation on the front side, and is the yaw
    // `tools/thumbs.py` renders all 4,950 mesh thumbnails from
    // (`out/thumbs/_selftest/yaw_grid.png` shows the four cardinal views: only
    // -Y has a face). Keep the two in step so a thumbnail and the viewport
    // agree.
    this.cam = { yaw: DEFAULT_YAW, pitch: 0.28, dist: 300, pan: [0, 0, 0] };
    this.stats = '';
    this._framed = false;
    this._lastBounds = null;      // bounds of the most recent model, lock or not
    this.viewMode = 'asset';
    try {
      const m = localStorage.getItem('coviewer.viewMode');
      if (m === 'character' || m === 'asset') this.viewMode = m;
    } catch (e) { /* ignore */ }
    this._restoreCam();
    this._grid = this._buildGrid();
    this._bindInput();
    this._resize();
    window.addEventListener('resize', () => { this._resize(); this.draw(); });
  }

  _program(vsrc, fsrc) {
    const gl = this.gl;
    const mk = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw new Error('shader: ' + gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, mk(gl.VERTEX_SHADER, vsrc));
    gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fsrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS))
      throw new Error('link: ' + gl.getProgramInfoLog(p));
    return p;
  }

  _solidTexture(rgba) {
    const gl = this.gl;
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                  new Uint8Array(rgba));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return t;
  }

  _resize() {
    const c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(c.clientWidth * dpr));
    const h = Math.max(1, Math.floor(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  }

  // ------------------------------------------------------------ input
  _bindInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('mousedown', e => {
      drag = { x: e.clientX, y: e.clientY, btn: e.button, shift: e.shiftKey };
      e.preventDefault();
    });
    window.addEventListener('mouseup', () => {
      if (drag) this._saveCam();      // orientation survives the next load
      drag = null;
    });
    window.addEventListener('mousemove', e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.btn === 2 || drag.shift || e.ctrlKey) {
        const s = this.cam.dist * 0.0018;
        // pan in the camera's screen plane
        const f = this._basis();
        this.cam.pan[0] += (-f.right[0] * dx + f.up[0] * dy) * s;
        this.cam.pan[1] += (-f.right[1] * dx + f.up[1] * dy) * s;
        this.cam.pan[2] += (-f.right[2] * dx + f.up[2] * dy) * s;
      } else if (!this.opts.fixedCamera) {
        // A locked camera is the point of a 2.5D game: orbiting it would tear
        // the 3D character off the projection the painted ground is drawn in.
        this.cam.yaw -= dx * 0.008;
        this.cam.pitch = Math.max(-1.54, Math.min(1.54, this.cam.pitch + dy * 0.008));
      }
      this.draw();
    });
    c.addEventListener('contextmenu', e => e.preventDefault());
    c.addEventListener('wheel', e => {
      e.preventDefault();
      this.cam.dist *= Math.exp(e.deltaY * 0.0012);
      this.cam.dist = Math.max(this.radius * 0.05, Math.min(this.radius * 40, this.cam.dist));
      this.draw();
      clearTimeout(this._wheelSave);
      this._wheelSave = setTimeout(() => this._saveCam(), 400);
    }, { passive: false });
  }

  /** The game camera: orthographic, 2:1 isometric, fixed. See ISO_YAW.
   *
   *  `free` restores the orbiting perspective camera, which stays available
   *  because it is genuinely the right tool for diagnosing placement -- but it
   *  is a debug view, not the game view. */
  setIsoCamera(free = false) {
    this.opts.projection = free ? 'perspective' : 'iso';
    this.opts.fixedCamera = !free;
    if (!free) {
      this.cam.yaw = ISO_YAW;
      this.cam.pitch = ISO_PITCH;
    }
    this._framed = true;          // never re-fit: the scale is the art's
    this.draw();
  }

  /** Half-height of the orthographic box, in world units. `cam.dist` remains
   *  the zoom control so the wheel behaves the same under both projections. */
  get isoHalfHeight() { return Math.max(1, this.cam.dist * 0.5); }

  /** Where a click lands on the ground plane, in world units.
   *
   *  `px`/`py` are canvas-relative CSS pixels; `groundZ` is the height of the
   *  plane to hit. Returns `[x, y, z]`, or null when the view is edge-on and
   *  the ray never meets the plane.
   *
   *  THE BASIS IS REDERIVED THE WAY `lookAt` DERIVES IT, DELIBERATELY.
   *  `_basis()` computes `right = cross(dir, up)` while `M4.lookAt` computes
   *  `x = cross(up, z)` -- the same axis with the opposite sign. Picking that
   *  borrowed `_basis()` would be mirrored in X, and the bug would look like
   *  "clicking left walks right", which is exactly the sort of thing that gets
   *  patched with a stray minus sign instead of understood. So this mirrors
   *  the render path: same `z`, same fallback when `z` is parallel to up.
   */
  screenToGround(px, py, groundZ = 0) {
    const c = this.canvas;
    const w = c.clientWidth || c.width, h = c.clientHeight || c.height;
    if (!w || !h) return null;
    const ndcX = (px / w) * 2 - 1;
    const ndcY = 1 - (py / h) * 2;

    const f = this._basis();
    const ctr = [this.center[0] + this.cam.pan[0],
                 this.center[1] + this.cam.pan[1],
                 this.center[2] + this.cam.pan[2]];
    const z = f.dir;                       // eye - ctr, normalised
    let x = cross([0, 0, 1], z);
    if (len(x) < 1e-6) x = cross([0, 1, 0], z);
    x = norm(x);
    const y = cross(z, x);

    const aspect = c.width / Math.max(1, c.height);
    let origin, ray;
    if (this.opts.projection === 'iso') {
      const hh = this.isoHalfHeight, hw = hh * aspect;
      origin = [ctr[0] + x[0] * ndcX * hw + y[0] * ndcY * hh,
                ctr[1] + x[1] * ndcX * hw + y[1] * ndcY * hh,
                ctr[2] + x[2] * ndcX * hw + y[2] * ndcY * hh];
      ray = [-z[0], -z[1], -z[2]];
    } else {
      const t = Math.tan(45 * Math.PI / 360);
      origin = [ctr[0] + z[0] * this.cam.dist,
                ctr[1] + z[1] * this.cam.dist,
                ctr[2] + z[2] * this.cam.dist];
      ray = norm([x[0] * ndcX * t * aspect + y[0] * ndcY * t - z[0],
                  x[1] * ndcX * t * aspect + y[1] * ndcY * t - z[1],
                  x[2] * ndcX * t * aspect + y[2] * ndcY * t - z[2]]);
    }
    if (Math.abs(ray[2]) < 1e-9) return null;
    const t = (groundZ - origin[2]) / ray[2];
    if (!isFinite(t)) return null;
    return [origin[0] + ray[0] * t, origin[1] + ray[1] * t, groundZ];
  }

  _basis() {
    const cp = Math.cos(this.cam.pitch), sp = Math.sin(this.cam.pitch);
    const cy = Math.cos(this.cam.yaw), sy = Math.sin(this.cam.yaw);
    const dir = [cp * cy, cp * sy, sp];            // world is Z-up
    const right = norm(cross(dir, [0, 0, 1]));
    const up = cross(right, dir);
    return { dir, right, up };
  }

  // ------------------------------------------------------------ camera state
  //
  // The camera used to snap back to a default on every load, which made it
  // impossible to compare two assets from the same angle. It now persists.
  // The tension that creates is scale: a character body is ~170 units tall and
  // a weapon socket mesh is ~5, so a *fully* persisted camera would leave you
  // staring at empty space when you switch between them. Hence:
  //
  //   unlocked (default)  orientation (yaw/pitch) always survives; distance is
  //                       re-fit to the new model's bounds; pan resets.
  //   locked              nothing moves at all -- distance, pan and the orbit
  //                       centre are frozen too. For flipping through colour
  //                       variants of one mesh, where even a re-fit would drift
  //                       the framing enough to read as the model changing.
  //
  // yaw / pitch / lock survive a page reload via localStorage.

  // Camera state is kept PER MODE. Character view stays anchored to the body;
  // asset view re-fits per asset. Coming back from inspecting a helmet must
  // restore the character framing, not inherit the helmet's zoom.
  _key() { return 'coviewer.cam.' + this.viewMode; }

  _saveCam() {
    // The game camera is derived, not chosen, so it must never be written into
    // the asset viewer's stored orientation -- otherwise opening coplay once
    // silently re-aims coviewer.
    if (this.opts.fixedCamera) return;
    try {
      localStorage.setItem(this._key(), JSON.stringify({
        yaw: this.cam.yaw, pitch: this.cam.pitch, lock: this.opts.lock,
        dist: this.cam.dist, pan: this.cam.pan,
      }));
      localStorage.setItem('coviewer.viewMode', this.viewMode);
    } catch (e) { /* private mode / disabled storage: not worth failing over */ }
  }

  _restoreCam() {
    let s = null;
    try {
      s = JSON.parse(localStorage.getItem(this._key()) ||
                     localStorage.getItem('coviewer.cam') || 'null');
    } catch (e) { return; }
    if (!s || typeof s !== 'object') return;
    if (Number.isFinite(s.yaw)) {
      // A returning user must not be left looking at the back of the model just
      // because the bad default was written into their storage before it was
      // fixed. Only an untouched old default is migrated; anything the user
      // actually orbited to is kept.
      this.cam.yaw = (Math.abs(s.yaw - LEGACY_DEFAULT_YAW) < 1e-7)
        ? DEFAULT_YAW : s.yaw;
    }
    if (Number.isFinite(s.pitch)) this.cam.pitch = clamp(s.pitch, -1.54, 1.54);
    this.opts.lock = !!s.lock;
    this._savedDist = null; this._savedPan = null;
    if (this.opts.lock) {
      if (Number.isFinite(s.dist) && s.dist > 0) this._savedDist = s.dist;
      if (Array.isArray(s.pan) && s.pan.length === 3 && s.pan.every(Number.isFinite)) {
        this._savedPan = s.pan.slice();
      }
    }
  }

  /** Switch between 'character' (body is the subject) and 'asset' (the thing
   *  you picked is the subject). Each mode owns its own camera and its own
   *  lock flag; neither leaks into the other. */
  setViewMode(mode) {
    if (mode === this.viewMode) return this.viewMode;
    this._saveCam();                       // park the outgoing mode's camera
    this.viewMode = mode;
    this._framed = false;                  // the next model re-frames this mode
    this._restoreCam();
    return this.viewMode;
  }

  setLock(on) {
    this.opts.lock = !!on;
    this._saveCam();
    return this.opts.lock;
  }

  /** Place the camera for a newly loaded model. See the note above. */
  _frame(center, radius) {
    const first = !this._framed;
    this._framed = true;
    if (this.opts.lock && !first) return;          // frozen: nothing moves
    this.center = center;
    this.radius = radius;
    if (this.opts.lock && first && this._savedDist) {
      // first model after a reload of a locked session: restore its framing
      this.cam.dist = this._savedDist;
      this.cam.pan = this._savedPan ? this._savedPan.slice() : [0, 0, 0];
    } else {
      this.cam.dist = radius * 2.6;
      this.cam.pan = [0, 0, 0];
    }
    this._saveCam();
  }

  /** Back to a known-good view of whatever is currently loaded. Works even when
   *  locked -- this is the escape hatch from a frozen camera. */
  resetView() {
    const b = this._lastBounds;
    if (b) { this.center = b.center.slice(); this.radius = b.radius; }
    this.cam.yaw = DEFAULT_YAW; this.cam.pitch = 0.28;
    this.cam.dist = this.radius * 2.6;
    this.cam.pan = [0, 0, 0];
    this._saveCam();
    this.draw();
  }

  // ------------------------------------------------------------ content
  clear() {
    const gl = this.gl;
    for (const m of this.meshes) {
      gl.deleteBuffer(m.vbo); gl.deleteBuffer(m.nbo);
      gl.deleteBuffer(m.tbo); gl.deleteBuffer(m.cbo);
      gl.deleteBuffer(m.ibo); if (m.wbo) gl.deleteBuffer(m.wbo);
    }
    this.meshes = [];
    // Effect layers keep their textures in the same map, so they go with it;
    // the caller re-attaches effects after a new model is loaded.
    this.clearEffects();
    for (const t of this.textures.values()) gl.deleteTexture(t);
    this.textures.clear();
  }

  /** meshDefs: array of {meta, textureKey, translate?}. Server JSON straight
   *  through. `translate` places an equipped part at its socket anchor.
   *  `opts.frameOn` overrides what the camera fits to -- in character view that
   *  is the body alone, so hanging a long weapon off a hand never yanks the
   *  view back. `opts.keepFraming` leaves the camera completely alone, which is
   *  what an equip change does. */
  setMeshes(defs, opts) {
    const gl = this.gl;
    this.clear();
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    let tris = 0, verts = 0;

    for (const d of defs) {
      const m = d.meta;
      if (!m.indices.length) continue;
      const buf = (data, Type, target) => {
        const b = gl.createBuffer();
        const t = target || gl.ARRAY_BUFFER;
        gl.bindBuffer(t, b);
        gl.bufferData(t, new Type(data), gl.STATIC_DRAW);
        return b;
      };
      const nv = m.vertexCount;
      let colors;
      if (m.colors && m.colors.length === nv) {
        colors = new Float32Array(nv * 4);
        for (let i = 0; i < nv; i++) {
          const c = m.colors[i] >>> 0;
          // Only 0x00000000 and 0xFFFFFFFF occur anywhere in this install, so
          // the channel order is not observable; treat memory order as RGBA.
          colors[i * 4 + 0] = (c & 0xff) / 255;
          colors[i * 4 + 1] = ((c >>> 8) & 0xff) / 255;
          colors[i * 4 + 2] = ((c >>> 16) & 0xff) / 255;
          colors[i * 4 + 3] = ((c >>> 24) & 0xff) / 255;
        }
      } else {
        colors = new Float32Array(nv * 4).fill(1);
      }

      // wireframe index list: three edges per triangle
      const wi = new Uint16Array(m.indices.length * 2);
      for (let i = 0, o = 0; i < m.indices.length; i += 3) {
        const a = m.indices[i], b = m.indices[i + 1], c = m.indices[i + 2];
        wi[o++] = a; wi[o++] = b; wi[o++] = b; wi[o++] = c; wi[o++] = c; wi[o++] = a;
      }

      const entry = {
        vbo: buf(m.positions, Float32Array),
        nbo: buf(m.normals, Float32Array),
        tbo: buf(m.uv0, Float32Array),
        cbo: buf(colors, Float32Array),
        ibo: buf(m.indices, Uint16Array, gl.ELEMENT_ARRAY_BUFFER),
        wbo: buf(wi, Uint16Array, gl.ELEMENT_ARRAY_BUFFER),
        count: m.indices.length,
        wcount: wi.length,
        meta: m,
        textureKey: d.textureKey || null,
        translate: d.translate || [0, 0, 0],
        // A socket may supply a full 4x4 (MOTI-derived: position AND
        // orientation). When it does not, fall back to a pure translation.
        model: d.matrix ? new Float32Array(d.matrix) : (() => {
          const m = M4.ident();
          if (d.translate) { m[12] = d.translate[0]; m[13] = d.translate[1]; m[14] = d.translate[2]; }
          return m;
        })(),
        slot: d.slot || null,
        socket: d.socket || null,
        // The world placement a socket matrix composes onto. The builder
        // leaves this null because its figure stands at the origin, so a
        // socket matrix *is* the model matrix there. A character standing on
        // a map has a placement as well, and `setPose` has to multiply rather
        // than overwrite or the sword drops to the world origin on frame 1.
        base: d.base ? new Float32Array(d.base) : null,
        // The socket-relative matrix this part was built with. Kept so the
        // caller can re-place a moving figure without rebuilding the scene:
        // `model = base * local`.
        local: d.local ? new Float32Array(d.local) : null,
        centroid: [(m.bboxRender[0][0] + m.bboxRender[1][0]) / 2 + (d.translate ? d.translate[0] : 0),
                   (m.bboxRender[0][1] + m.bboxRender[1][1]) / 2 + (d.translate ? d.translate[1] : 0),
                   (m.bboxRender[0][2] + m.bboxRender[1][2]) / 2 + (d.translate ? d.translate[2] : 0)],
        visible: true,
      };
      this.meshes.push(entry);
      tris += m.faceCount; verts += nv;
      if (!m.isSocket || defs.length === 1) {
        const t = entry.translate;
        for (let i = 0; i < 3; i++) {
          lo[i] = Math.min(lo[i], m.bboxRender[0][i] + t[i]);
          hi[i] = Math.max(hi[i], m.bboxRender[1][i] + t[i]);
        }
      }
    }
    if (!isFinite(lo[0])) { lo = [-50, -50, 0]; hi = [50, 50, 100]; }
    opts = opts || {};
    let flo = lo, fhi = hi;
    if (opts.frameOn && opts.frameOn.min && opts.frameOn.max) {
      flo = opts.frameOn.min; fhi = opts.frameOn.max;   // the body alone
    }
    const center = [(flo[0] + fhi[0]) / 2, (flo[1] + fhi[1]) / 2, (flo[2] + fhi[2]) / 2];
    const radius = Math.max(1, len(sub(fhi, flo)) / 2);
    this.bbox = [lo, hi];
    this._lastBounds = { center: center.slice(), radius };
    this.stats = `${defs.length} chunk(s)  ${verts} verts  ${tris} tris\n` +
                 `extent ${(hi[0]-lo[0]).toFixed(1)} x ${(hi[1]-lo[1]).toFixed(1)} x ${(hi[2]-lo[2]).toFixed(1)}`;
    if (!opts.keepFraming) this._frame(center, radius);
    this.draw();
  }

  /** Re-pose an already-loaded figure: swap each body chunk's position buffer
   *  and move every equipped part to that frame's socket matrix.
   *
   *  This is what makes animation cheap enough to play. The geometry, the UVs,
   *  the indices and the textures do not change from frame to frame -- only the
   *  skinned positions do -- so a frame costs one `bufferSubData` per chunk
   *  instead of a full rebuild, and the camera is never touched.
   *
   *  `positions` is chunk index -> flat [x,y,z,...]; `sockets` is socket name
   *  -> a 16-float column-major matrix. Both come straight from `/api/anim`,
   *  which evaluates the game's own `3dmotion.ini` track. Nothing here invents
   *  motion; it only uploads what the server computed.
   */
  setPose(positions, sockets) {
    const gl = this.gl;
    for (const m of this.meshes) {
      // Socket-attached geometry -- a weapon, a helmet -- is moved by its
      // socket matrix, not by rewriting vertices.
      //
      // This tests `socket`, not `slot`, and the difference matters: the game
      // view tags the player's *body* meshes with a slot (`_player`) so it can
      // find them again, and testing `slot` here skipped them, which is why a
      // placed character could never animate. A mesh is socket-driven when it
      // has a socket; a slot is just a label.
      if (m.socket) {
        const mat = sockets && sockets[m.socket];
        if (mat && mat.length === 16) {
          m.model = m.base ? M4.mul(m.base, new Float32Array(mat))
                           : new Float32Array(mat);
        }
        continue;
      }
      const p = positions && positions[m.meta.index];
      if (!p || p.length !== m.meta.vertexCount * 3) continue;
      gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array(p));
    }
  }

  // ------------------------------------------------------- effect playback
  //
  // Effects are a second content channel, kept separate from `meshes` because
  // they are animated, blended with their own D3D blend state and, in the case
  // of an impact spark, anchored somewhere the character is not.
  // The algorithm is fx.js / tools/effectplay.py; this is only the plumbing.

  /** defs: [{def, role, anchor, textureKeys}] straight from /api/effect. */
  setEffects(defs) {
    this.clearEffects();
    for (const d of defs || []) {
      if (!d || !d.def) continue;
      try {
        this.fx.push(new EffectInstance(this.gl, d.def, d));
      } catch (e) { /* a malformed layer must not take the viewport down */ }
    }
    this.fxTime = 0;
    this.draw();
    return this.fx.length;
  }

  clearEffects() {
    for (const f of this.fx || []) f.dispose();
    this.fx = [];
    this.fxTime = 0;
  }

  /** Advance every instance to `ms` since spawn and redraw.
   *  `parentFor(role)` supplies the world matrix the effect rides -- the weapon
   *  socket for an aura/trail, the target dummy for an impact. */
  setEffectTime(ms, parentFor) {
    this.fxTime = ms;
    let alive = 0;
    for (const f of this.fx) {
      const parent = parentFor ? parentFor(f) : null;
      // The parent matrix MOVES. An aura rides `v_r_weapon` and that socket is
      // a different 4x4 on every animation frame, so the new matrix has to
      // reach the static quads in `_drawEffects` — which read `inst.anchor` —
      // and not only the ribbon history, which is all `tick()` used it for.
      // Without this the glow stays wherever the hand was at the instant the
      // effect was attached and the sword swings out from under it: the same
      // "off in space" symptom as the child-instead-of-sibling bug, from a
      // different direction. Measured on 410009: at frame 9 of action 401 the
      // socket has moved to (28.1, -76.1, 72.5) while the frozen anchor was
      // still (-10.4, 18.7, 81.3), ~100 units away.
      if (parent && parent.length === 16) f.anchor.set(parent);
      const st = f.tick(ms, parent);
      if (!st.done) alive++;
    }
    this.draw();
    return alive;
  }

  /** Rewind every instance -- ribbons included, which otherwise keep a stale
   *  smear from the previous playthrough. */
  resetEffects() {
    for (const f of this.fx) f.reset();
    this.fxTime = 0;
  }

  _glBlend(name) {
    const gl = this.gl;
    const T = {
      ZERO: gl.ZERO, ONE: gl.ONE,
      SRC_COLOR: gl.SRC_COLOR, ONE_MINUS_SRC_COLOR: gl.ONE_MINUS_SRC_COLOR,
      SRC_ALPHA: gl.SRC_ALPHA, ONE_MINUS_SRC_ALPHA: gl.ONE_MINUS_SRC_ALPHA,
      DST_ALPHA: gl.DST_ALPHA, ONE_MINUS_DST_ALPHA: gl.ONE_MINUS_DST_ALPHA,
      DST_COLOR: gl.DST_COLOR, ONE_MINUS_DST_COLOR: gl.ONE_MINUS_DST_COLOR,
      SRC_ALPHA_SATURATE: gl.SRC_ALPHA_SATURATE,
      CONSTANT_COLOR: gl.CONSTANT_COLOR,
      ONE_MINUS_CONSTANT_COLOR: gl.ONE_MINUS_CONSTANT_COLOR,
    };
    return T[name] !== undefined ? T[name] : gl.ONE;
  }

  _drawEffects(mvp) {
    const gl = this.gl;
    if (!this.fx.length) return;
    gl.enable(gl.BLEND);
    gl.depthMask(false);          // effects never occlude each other
    gl.disable(gl.CULL_FACE);     // effect quads are viewed from both sides
    gl.uniform1i(this.uni.uMode, 0);        // always unlit: they are emissive
    gl.uniform1i(this.uni.uAlphaMode, 0);
    gl.uniform1i(this.uni.uUseVColor, 0);

    for (const inst of this.fx) {
      if (inst.done && !inst.def.endless) continue;
      const st = inst.state || { frame: inst.frame, waiting: false, gap: false };
      if (st.waiting || st.gap) continue;
      const world = inst._world(inst.anchor);
      for (const lay of inst.layers) {
        // ASB / ADB are D3DBLEND (docs/effects.md §7) -- this is the effect's
        // own authored blend state, not the viewer's global alpha setting.
        gl.blendFunc(this._glBlend(lay.src.glSrcBlend),
                     this._glBlend(lay.src.glDstBlend));
        const tex = this.textures.get(inst.textureKeys[lay.src.index]);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tex || this.white);
        gl.uniform1i(this.uni.uHasTex, tex ? 1 : 0);

        for (const p of lay.parts) {
          if (p.kind === 'phy') {
            const eff = p.src.effectiveFrames || inst.def.effectiveFrames || 1;
            if (st.frame >= eff) continue;
            const s = FX.samplePart(p.src, st.frame);
            if (!s.visible || s.alpha <= 0.002) continue;
            const bone = (p.src.motion && p.src.motion.bones &&
                          p.src.motion.bones[0]) || 0;
            const M = FX.motionMatrix(p.src.motion, bone, st.frame);
            const model = FX.mul(world, M);
            gl.uniform1f(this.uni.uMeshAlpha, s.alpha);
            gl.uniform2f(this.uni.uUVOffset, s.uv[0], s.uv[1]);
            gl.uniformMatrix4fv(this.uni.uModel, false, new Float32Array(model));
            gl.uniformMatrix4fv(this.uni.uMVP, false,
                                M4.mul(mvp, new Float32Array(model)));
            gl.bindBuffer(gl.ARRAY_BUFFER, p.vbo);
            gl.enableVertexAttribArray(this.attr.pos);
            gl.vertexAttribPointer(this.attr.pos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, p.nbo);
            gl.enableVertexAttribArray(this.attr.nrm);
            gl.vertexAttribPointer(this.attr.nrm, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, p.tbo);
            gl.enableVertexAttribArray(this.attr.uv);
            gl.vertexAttribPointer(this.attr.uv, 2, gl.FLOAT, false, 0, 0);
            gl.disableVertexAttribArray(this.attr.col);
            gl.vertexAttrib4f(this.attr.col, 1, 1, 1, 1);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.ibo);
            gl.drawElements(gl.TRIANGLES, p.count, gl.UNSIGNED_SHORT, 0);
          } else if (p.kind === 'shape' && p.count >= 4) {
            // The ribbon is already in world space -- it was built from
            // transformed points -- so the model matrix is identity here.
            gl.uniform1f(this.uni.uMeshAlpha, 1.0);
            gl.uniform2f(this.uni.uUVOffset, 0, 0);
            gl.uniformMatrix4fv(this.uni.uModel, false, M4.ident());
            gl.uniformMatrix4fv(this.uni.uMVP, false, mvp);
            gl.bindBuffer(gl.ARRAY_BUFFER, p.vbo);
            gl.enableVertexAttribArray(this.attr.pos);
            gl.vertexAttribPointer(this.attr.pos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, p.tbo);
            gl.enableVertexAttribArray(this.attr.uv);
            gl.vertexAttribPointer(this.attr.uv, 2, gl.FLOAT, false, 0, 0);
            gl.disableVertexAttribArray(this.attr.nrm);
            gl.vertexAttrib3f(this.attr.nrm, 0, 0, 1);
            gl.disableVertexAttribArray(this.attr.col);
            gl.vertexAttrib4f(this.attr.col, 1, 1, 1, 1);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, p.count);
          }
        }
      }
    }
    gl.depthMask(true);
    gl.uniform1f(this.uni.uMeshAlpha, 1.0);
    gl.uniform2f(this.uni.uUVOffset, 0, 0);
    gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  }

  /** Zoom to a named socket without changing orientation -- inspect a helmet
   *  up close, then `R` to get back to the whole figure. */
  focusOn(pos, spread) {
    if (!pos) return;
    this.center = pos.slice();
    this.cam.pan = [0, 0, 0];
    this.cam.dist = Math.max(1, (spread || this.radius * 0.18) * 4);
    this._saveCam();
    this.draw();
  }

  /** Upload an <img> as a texture under `key`. */
  setTexture(key, img) {
    const gl = this.gl;
    const old = this.textures.get(key);
    if (old) gl.deleteTexture(old);
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);   // D3D V axis == PNG row order
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    const pot = v => (v & (v - 1)) === 0;
    if (pot(img.width) && pot(img.height)) {
      gl.generateMipmap(gl.TEXTURE_2D);              // the engine generates its own
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    } else {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    this.textures.set(key, t);
  }

  _buildGrid() {
    const gl = this.gl;
    const v = [];
    const N = 10;
    for (let i = -N; i <= N; i++) {
      v.push(i, -N, 0, i, N, 0);
      v.push(-N, i, 0, N, i, 0);
    }
    const b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.STATIC_DRAW);
    return { buf: b, count: v.length / 3 };
  }

  /** C3Key alpha for the current frame. The channel is a keyframe track --
   *  {int nFrame; float fParam; BOOL bParam; int nParam} per TQ's c3_key.h --
   *  consumed by the exported Key_ProcessAlpha. We linearly interpolate
   *  fParam between keys, which is the obvious reading but is NOT verified
   *  against the engine's interpolator. */
  _alphaFor(meta) {
    const keys = meta.keys && meta.keys.alphas;
    if (!keys || !keys.length) return 1.0;
    const f = this.opts.frame;
    if (f <= keys[0].frame) return clamp01(keys[0].f);
    for (let i = 1; i < keys.length; i++) {
      if (f <= keys[i].frame) {
        const a = keys[i - 1], b = keys[i];
        const t = b.frame === a.frame ? 0 : (f - a.frame) / (b.frame - a.frame);
        return clamp01(a.f + (b.f - a.f) * t);
      }
    }
    return clamp01(keys[keys.length - 1].f);
  }

  draw() {
    const gl = this.gl;
    this._resize();
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0.055, 0.06, 0.067, 1);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.prog);

    const f = this._basis();
    const eye = [
      this.center[0] + this.cam.pan[0] + f.dir[0] * this.cam.dist,
      this.center[1] + this.cam.pan[1] + f.dir[1] * this.cam.dist,
      this.center[2] + this.cam.pan[2] + f.dir[2] * this.cam.dist];
    const ctr = [this.center[0] + this.cam.pan[0],
                 this.center[1] + this.cam.pan[1],
                 this.center[2] + this.cam.pan[2]];
    const aspect = this.canvas.width / Math.max(1, this.canvas.height);
    const near = Math.max(0.05, this.cam.dist / 400);
    let proj;
    if (this.opts.projection === 'iso') {
      // The far plane has to clear the whole patch from an eye that is only
      // `dist` away, because ortho does not pull distant geometry in.
      const hh = this.isoHalfHeight;
      proj = M4.ortho(hh * aspect, hh, -this.radius * 40, this.cam.dist + this.radius * 40);
    } else {
      proj = M4.perspective(45 * Math.PI / 180, aspect, near,
                            this.cam.dist + this.radius * 25);
    }
    const view = M4.lookAt(eye, ctr, [0, 0, 1]);
    const model = M4.ident();
    const mvp = M4.mul(proj, view);

    gl.uniformMatrix4fv(this.uni.uMVP, false, mvp);
    gl.uniformMatrix4fv(this.uni.uModel, false, model);
    gl.uniform1i(this.uni.uTex, 0);
    gl.uniform2f(this.uni.uUVOffset, 0, 0);   // only effects scroll their UVs

    // ---- grid ---------------------------------------------------------
    if (this.opts.grid) {
      const s = this.radius / 5;
      const gm = M4.ident(); gm[0] = s; gm[5] = s; gm[10] = s;
      gl.uniformMatrix4fv(this.uni.uMVP, false, M4.mul(mvp, gm));
      gl.uniform1i(this.uni.uSolid, 1);
      gl.uniform3f(this.uni.uFlat, 0.16, 0.17, 0.19);
      gl.disable(gl.BLEND); gl.disable(gl.CULL_FACE);
      gl.bindBuffer(gl.ARRAY_BUFFER, this._grid.buf);
      gl.enableVertexAttribArray(this.attr.pos);
      gl.vertexAttribPointer(this.attr.pos, 3, gl.FLOAT, false, 0, 0);
      this._disableExtra();
      gl.drawArrays(gl.LINES, 0, this._grid.count);
      gl.uniformMatrix4fv(this.uni.uMVP, false, mvp);
      gl.uniform1i(this.uni.uSolid, 0);
    }

    // ---- meshes -------------------------------------------------------
    const modeIdx = { unlit: 0, lit: 1, normals: 2, uv: 3 }[this.opts.shade] || 0;
    const alphaIdx = { blend: 0, test: 1, opaque: 2 }[this.opts.alpha] ?? 0;
    gl.uniform1i(this.uni.uMode, modeIdx);
    gl.uniform1i(this.uni.uAlphaMode, alphaIdx);
    gl.uniform1f(this.uni.uCutoff, this.opts.cutoff);
    gl.uniform1i(this.uni.uUseVColor, this.opts.vcolor ? 1 : 0);

    const drawable = this.meshes.filter(m =>
      m.visible && (this.opts.sockets || !m.meta.isSocket));

    // Back-to-front for the blended pass. The engine's own draw order comes
    // from the scene graph and is not recovered; depth-sorted centroids is the
    // usual approximation and is what we do here.
    if (alphaIdx === 0) {
      drawable.sort((a, b) => distTo(eye, b.centroid) - distTo(eye, a.centroid));
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    } else {
      gl.disable(gl.BLEND);
    }
    // A `.DMap` COVER layer is *defined* as the thing that draws in front of
    // the player -- it is how the engine hides you behind a roof edge or a
    // tree canopy as you walk past. So an overlay mesh always sorts last and
    // always ignores depth; anything else and the character punches through
    // the very sprite meant to occlude them.
    drawable.sort((a, b) => (a.meta.overlay ? 1 : 0) - (b.meta.overlay ? 1 : 0));
    let overlayOn = false;

    for (const m of drawable) {
      const alpha = this._alphaFor(m.meta);
      if (alpha <= 0.001 && alphaIdx !== 2) continue;
      if (!!m.meta.overlay !== overlayOn) {
        overlayOn = !!m.meta.overlay;
        gl.depthFunc(overlayOn ? gl.ALWAYS : gl.LEQUAL);
        if (overlayOn) {
          // Covers are cut-out art whichever alpha mode the page is in.
          gl.enable(gl.BLEND);
          gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                               gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
          gl.uniform1i(this.uni.uAlphaMode, 0);
        }
      }
      gl.uniform1f(this.uni.uMeshAlpha, alphaIdx === 2 ? 1.0 : alpha);
      // equipped parts carry their socket transform; the body is identity
      gl.uniformMatrix4fv(this.uni.uModel, false, m.model || model);
      gl.uniformMatrix4fv(this.uni.uMVP, false,
                          m.model ? M4.mul(mvp, m.model) : mvp);
      this._setCull(m.meta);
      const tex = m.textureKey && this.textures.get(m.textureKey);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, tex || this.white);
      gl.uniform1i(this.uni.uHasTex, tex ? 1 : 0);
      this._bindAttribs(m);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
      gl.drawElements(gl.TRIANGLES, m.count, gl.UNSIGNED_SHORT, 0);
    }
    if (overlayOn) {
      gl.depthFunc(gl.LEQUAL);
      gl.uniform1i(this.uni.uAlphaMode, alphaIdx);
    }

    if (this.opts.wire) {
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
      gl.uniform1i(this.uni.uSolid, 1);
      gl.uniform3f(this.uni.uFlat, 0.45, 0.72, 1.0);
      for (const m of drawable) {
        gl.uniformMatrix4fv(this.uni.uMVP, false,
                            m.model ? M4.mul(mvp, m.model) : mvp);
        this._bindAttribs(m);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.wbo);
        gl.drawElements(gl.LINES, m.wcount, gl.UNSIGNED_SHORT, 0);
      }
      gl.uniformMatrix4fv(this.uni.uMVP, false, mvp);
      gl.uniform1i(this.uni.uSolid, 0);
    }

    // ---- effects (always last: they are additive and never write depth) ---
    this._drawEffects(mvp);
  }

  _setCull(meta) {
    const gl = this.gl;
    const mode = this.opts.cull;
    if (mode === 'none' || meta.twoSided) { gl.disable(gl.CULL_FACE); return; }
    gl.enable(gl.CULL_FACE);
    gl.frontFace(gl.CCW);                 // see the header comment
    gl.cullFace(mode === 'front' ? gl.FRONT : gl.BACK);
  }

  _bindAttribs(m) {
    const gl = this.gl, a = this.attr;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
    gl.enableVertexAttribArray(a.pos); gl.vertexAttribPointer(a.pos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.nbo);
    gl.enableVertexAttribArray(a.nrm); gl.vertexAttribPointer(a.nrm, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.tbo);
    gl.enableVertexAttribArray(a.uv); gl.vertexAttribPointer(a.uv, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.cbo);
    gl.enableVertexAttribArray(a.col); gl.vertexAttribPointer(a.col, 4, gl.FLOAT, false, 0, 0);
  }

  _disableExtra() {
    const gl = this.gl, a = this.attr;
    for (const k of [a.nrm, a.uv, a.col]) {
      gl.disableVertexAttribArray(k);
    }
    gl.vertexAttrib3f(a.nrm, 0, 0, 1);
    gl.vertexAttrib2f(a.uv, 0, 0);
    gl.vertexAttrib4f(a.col, 1, 1, 1, 1);
  }

  /** PNG of the current frame -- used by the headless render check. */
  snapshot() { this.draw(); return this.canvas.toDataURL('image/png'); }
}

const clamp01 = v => Math.max(0, Math.min(1, v));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const distTo = (a, b) => (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2;

window.Viewer = Viewer;
