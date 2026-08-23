/* fx.js -- effect playback for the CO asset viewer.
 *
 * This is docs/effects.md §8, implemented. It is a line-for-line mirror of the
 * reference implementation in `tools/effectplay.py` (frameAt / samplePart /
 * motionMatrix / ribbonAdvance), which is what `tools/test_viewer.py` actually
 * exercises against real assets -- so the algorithm is covered by tests rather
 * than by looking at the viewport and deciding it seems right.
 *
 * The four things that are easy to get wrong, and are not:
 *
 *  1. TIMING COMES FROM THE ALPHA ENVELOPE, NOT THE DECLARED TRACK LENGTH.
 *     Effect meshes habitually declare a 101-frame MOTI and fade to zero after
 *     ten. Playing the declared length turns a 363 ms impact spark into a 3.3 s
 *     one. The server computes `effectiveFrames` per part (docs/effects.md
 *     §6.5, INFERRED but unambiguous in the data) and we play that.
 *
 *  2. THE TEXTURE IS A FLIPBOOK ATLAS OF N x N CELLS, where N is the PHY
 *     chunk's own `frameCount` field -- not the animation length. The ChangeTex
 *     key value is the cell index, row-major, and the engine applies it as a UV
 *     *offset* with no scale, because the quad's own UVs already span one cell.
 *     (m-b02's Plane02: N=2, UVs authored 0..0.5, keys 0->0 2->1 4->2 6->3.)
 *
 *  3. THE THREE C3Key CHANNELS BEHAVE DIFFERENTLY. alpha is lerped between the
 *     bracketing keys; draw is an EXACT frame match only; changeTex is a step
 *     function. Treating all three the same is silently wrong.
 *
 *  4. A PARTICLE SYSTEM IS BAKED, NOT SIMULATED. `frames[i]` is the solved
 *     set for frame i -- position, flipbook phase and half-size per particle,
 *     plus one 4x4 for the whole frame -- so there is no integrator here, and
 *     writing one would be inventing motion the file already states. What the
 *     draw path does add is the billboard plane, because `Ptcl_Draw` gets it
 *     from a world matrix its caller built and we are that caller: the quads
 *     are rebuilt against the CAMERA's right/up every draw, not every tick.
 *
 *  5. A SHAP TRAIL IS DRIVEN BY THE PARENT'S MOTION, NOT ITS OWN. The SMOT on
 *     the shipped flash meshes is a constant matrix on all 101 frames; the
 *     streak exists because the weapon moves. A static weapon therefore
 *     produces a static line, which is correct and is why the viewer offers an
 *     explicit swing preview (labelled as ours, not the game's).
 */

'use strict';

// --------------------------------------------------------------- pure math

const FX = {
  IDENT: [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],

  /** Billboard corner offsets as (right, up) multiples plus the (u, v) corner
   *  of the atlas cell, in two-triangle order. V grows DOWNWARD: the viewer
   *  sets no UNPACK_FLIP_Y_WEBGL and D3D's V axis runs top-down, so the atlas
   *  row index counts from the top exactly as the PHY ChangeTex channel's does
   *  (docs/effects.md §6.4) and screen-up is the SMALL v. */
  QUAD: [
    [-1,  1, 0, 0],
    [-1, -1, 0, 1],
    [ 1,  1, 1, 0],
    [ 1,  1, 1, 0],
    [-1, -1, 0, 1],
    [ 1, -1, 1, 1],
  ],

  mul(a, b) {                        // column-major a*b: apply b, then a
    const o = new Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  },

  xform(m, p) {
    return [m[0]*p[0] + m[4]*p[1] + m[8]*p[2]  + m[12],
            m[1]*p[0] + m[5]*p[1] + m[9]*p[2]  + m[13],
            m[2]*p[0] + m[6]*p[1] + m[10]*p[2] + m[14]];
  },

  translation(x, y, z) {
    const m = FX.IDENT.slice(); m[12] = x; m[13] = y; m[14] = z; return m;
  },

  /** docs/effects.md §8: where an instance is `elapsed` ms after spawning.
   *  `frames` is the EFFECTIVE length, not the declared MOTI length. */
  frameAt(elapsed, d) {
    const fi = Math.max(1, d.frameIntervalMs || 1);
    const n = Math.max(1, d.frames || 1);
    const delay = d.delayMs || 0;
    if (elapsed < delay) return { waiting: true, done: false, frame: 0, loop: 0, gap: true };
    const t = elapsed - delay;
    const cycle = n * fi + Math.max(0, d.loopIntervalMs || 0);
    const loop = Math.floor(t / cycle);
    if (!d.endless && loop >= Math.max(1, d.loopTime || 1))
      return { waiting: false, done: true, frame: n - 1, loop, gap: false };
    const frame = Math.floor((t % cycle) / fi);
    return { waiting: false, done: false, frame: Math.min(frame, n - 1), loop,
             gap: frame >= n };
  },

  /** Alpha / visibility / UV offset of one PHY part at `frame`. */
  samplePart(part, frame) {
    const keys = part.keys || {};
    const alphas = keys.alphas || [], draws = keys.draws || [], texs = keys.changeTexs || [];

    // draw: exact frame match ONLY. No key here means "unchanged" -- visible.
    let visible = true;
    for (const k of draws) if (k.frame === frame) { visible = !!k.b; break; }

    // alpha: linear interpolation between the bracketing keys, clamped
    let alpha = 1.0;
    if (alphas.length) {
      let prev = null, next = null;
      for (const k of alphas) {
        if (k.frame <= frame && (!prev || k.frame > prev.frame)) prev = k;
        if (k.frame > frame && (!next || k.frame < next.frame)) next = k;
      }
      if (!prev) alpha = next.f;
      else if (!next) alpha = prev.f;
      else {
        const span = next.frame - prev.frame;
        const t = span === 0 ? 0 : (frame - prev.frame) / span;
        alpha = prev.f + (next.f - prev.f) * t;
      }
      alpha = Math.max(0, Math.min(1, alpha));
    }

    // changeTex: step function. Mirrors effects.key_change_tex exactly,
    // including the tail case where no key sits past `frame` (-> no cell).
    let cell = null;
    for (let i = 0; i < texs.length; i++) {
      if (texs[i].frame === frame) { cell = texs[i].n; break; }
      if (i && frame < texs[i].frame) { cell = texs[i - 1].n; break; }
    }

    let uv;
    if (cell !== null && cell >= 0) {
      const N = Math.max(1, part.uvGrid || 1);
      uv = [(cell % N) / N, Math.floor(cell / N) / N];
    } else {
      const s = part.uvStep || [0, 0];
      uv = [frame * s[0], frame * s[1]];
    }
    return { visible, alpha, cell, uv: [wrap11(uv[0]), wrap11(uv[1])] };
  },

  /** Motion_GetMatrix (RVA 0x551A0): clamp at both ends, else element-wise
   *  lerp all 16 elements. Element-wise, not slerp -- ZKEY quaternions were
   *  already baked to matrices at load time. */
  motionMatrix(motion, bone, frame) {
    const keys = (motion && motion.keys) || [];
    if (!keys.length) return FX.IDENT.slice();
    const bones = (motion.bones && motion.bones.length) ? motion.bones : [0];
    let bi = bones.indexOf(bone); if (bi < 0) bi = 0;
    const at = k => k.m.slice(bi * 16, bi * 16 + 16);
    if (frame <= keys[0].frame) return at(keys[0]);
    if (frame >= keys[keys.length - 1].frame) return at(keys[keys.length - 1]);
    for (let i = 1; i < keys.length; i++) {
      if (frame < keys[i].frame) {
        const a = keys[i - 1], b = keys[i];
        const span = b.frame - a.frame;
        const t = span === 0 ? 0 : (frame - a.frame) / span;
        const ma = at(a), mb = at(b), o = new Array(16);
        for (let k = 0; k < 16; k++) o[k] = ma[k] + (mb[k] - ma[k]) * t;
        return o;
      }
    }
    return at(keys[keys.length - 1]);
  },

  // ------------------------------------------------------------- particles
  //
  // docs/effects.md §6.6. The simulation is BAKED -- `frames[i]` is the solved
  // particle set for frame i -- so there is no integrator here and there is
  // not supposed to be one. Mirrors effectplay.particle_scale /
  // particle_frame / particle_quads; that module's docstrings carry the RVAs
  // and the list of fields this path does NOT apply.

  /** `Ptcl_Draw`'s size multiplier (RVA 0x1EC968): the length of the matrix's
   *  transformed (1,1,1) direction times 1/sqrt(3). 1.0 for a rotation. */
  particleScale(m) {
    const x = m[0] + m[4] + m[8];
    const y = m[1] + m[5] + m[9];
    const z = m[2] + m[6] + m[10];
    return Math.sqrt(x*x + y*y + z*z) / Math.sqrt(3);
  },

  /** The solved set for `frame`, or null when nothing is alive. The modulus is
   *  `Ptcl_SetFrame` (0x61570); the empty case is `Ptcl_Draw`'s early-out at
   *  0x605CF and it is the COMMON case -- 47 % of patch5517's particle frames
   *  have no particles at all. */
  particleFrame(part, frame) {
    const frames = (part && part.frames) || [];
    if (!frames.length) return null;
    const f = frames[((frame | 0) % frames.length + frames.length) % frames.length];
    return (f && f.n) ? f : null;
  },

  /** One baked frame as camera-facing quads: `{n, pos, uv, alpha}` with 6*n
   *  vertices in each flat array (two triangles per particle).
   *
   *  `right` / `up` are the CAMERA basis in world space and are passed in
   *  rather than derived, because the engine gets its billboard plane from a
   *  world matrix its caller built and we have no such caller. gl.js takes
   *  them straight out of the view matrix it is already building -- NOT out of
   *  `_basis()`, whose `right` is the opposite sign (see the note on
   *  `screenToGround`). */
  particleQuads(part, frame, world, right, up) {
    const out = { n: 0, pos: null, uv: null, alpha: 1.0 };
    const f = FX.particleFrame(part, frame);
    if (!f) return out;
    const m = FX.mul(Array.from(world), f.m || FX.IDENT);
    const scale = FX.particleScale(m);
    const N = Math.max(1, part.atlas || 1);
    const cell = 1 / N, last = N * N - 1;
    const sa = part.systemAlpha;
    if (sa && sa.length) out.alpha = sa[Math.min(frame | 0, sa.length - 1)];

    const n = f.n | 0;
    const pos = new Float32Array(n * 18);
    const uv = new Float32Array(n * 12);
    for (let i = 0; i < n; i++) {
      const p = FX.xform(m, [f.p[i*3], f.p[i*3+1], f.p[i*3+2]]);
      const s = f.s[i] * scale;
      const c = Math.min(Math.trunc(f.c[i] * N * N), last);
      const u0 = (c % N) * cell, v0 = Math.floor(c / N) * cell;
      for (let q = 0; q < 6; q++) {
        const [dr, du, cu, cv] = FX.QUAD[q];
        const o = (i * 6 + q) * 3, t = (i * 6 + q) * 2;
        pos[o]     = p[0] + right[0]*dr*s + up[0]*du*s;
        pos[o + 1] = p[1] + right[1]*dr*s + up[1]*du*s;
        pos[o + 2] = p[2] + right[2]*dr*s + up[2]*du*s;
        uv[t]     = u0 + cu * cell;
        uv[t + 1] = v0 + cv * cell;
      }
    }
    out.n = n; out.pos = pos; out.uv = uv;
    return out;
  },

  /** Push one more transformed blade line onto a ribbon, subdividing into
   *  `subdiv` pairs between the previous line and the new one, and dropping
   *  the oldest once `maxPairs` is reached (Shape_SetSegment, RVA 0x5D8C0). */
  ribbonAdvance(line, world, history, maxPairs, subdiv) {
    subdiv = subdiv || 5;
    const a = FX.xform(world, line[0]), b = FX.xform(world, line[1]);
    if (!history.length) history.push([a, b]);
    else {
      const [pa, pb] = history[history.length - 1];
      for (let s = 1; s <= subdiv; s++) {
        const t = s / subdiv;
        history.push([
          [pa[0] + (a[0]-pa[0])*t, pa[1] + (a[1]-pa[1])*t, pa[2] + (a[2]-pa[2])*t],
          [pb[0] + (b[0]-pb[0])*t, pb[1] + (b[1]-pb[1])*t, pb[2] + (b[2]-pb[2])*t],
        ]);
      }
    }
    while (history.length > maxPairs) history.shift();
    return history;
  },
};

function wrap11(v) {                 // graphic.dll wraps into [-1.1, 1.1]
  while (v > 1.1) v -= Math.ceil(v);
  while (v < -1.1) v -= Math.floor(v);
  return v;
}

// --------------------------------------------------------------- instance

/** One playing effect: a `3DEffect.ini` name plus where it is anchored.
 *
 * `anchor` is the world matrix the effect rides. Per docs/effects.md §8 that
 * is the weapon socket for an aura or a trail, and the TARGET for an impact
 * spark -- which is why the viewer draws impacts on a separate dummy anchor
 * and says so, rather than sticking them on the character.
 */
class EffectInstance {
  constructor(gl, def, opts) {
    this.gl = gl;
    this.def = def;
    opts = opts || {};
    this.role = opts.role || '';
    /** Which equipment slot this instance belongs to, when it belongs to one.
     *  Two weapon auras are two instances of the same `role`, riding two
     *  different sockets, so `role` alone cannot tell them apart and the
     *  caller that supplies the moving parent matrix needs to. Empty for
     *  effects that are not slot-bound. */
    this.slot = opts.slot || '';
    /** Milliseconds to subtract from the clock this instance is ticked with.
     *
     *  ONE CLOCK, MANY PHASES. `Viewer.setEffectTime(ms, ..)` advances every
     *  live instance to the same `ms`, which is right for the map's ambient
     *  decoration -- the engine spawns those at map load, so every torch on a
     *  map really is in phase -- and wrong for anything that starts when
     *  something happens to one entity. Two players buffed four seconds apart
     *  are four seconds out of phase, and drawing them in lockstep is the
     *  scene-level version of putting the player's walk stride on every
     *  lookalike: it reads as an animation choice rather than as the bug it
     *  is.
     *
     *  A phase is therefore PER INSTANCE, and it lives here rather than in the
     *  caller because `setEffectTime` takes one number. Default 0, so every
     *  existing caller -- the builder's two auras, `mapfxAttach`'s torches --
     *  is bit-for-bit unchanged. */
    this.tOffset = +opts.tOffset || 0;
    /** ONE-SHOT, OR ENDLESS? The channel's whole population used to be the
     *  second kind -- torches, status auras, weapon glows -- and each of them
     *  lives exactly as long as its owner does, so nothing ever had to leave
     *  on its own. A hit spark is the opposite: it spawns at a point, plays
     *  its authored length and must remove itself.
     *
     *  This flag is what `Viewer.setEffectTime` reaps on, and what
     *  `Viewer.setEffects` refuses to replace. It is NOT the same question as
     *  `def.endless`: that one is the SCENE's own loop flag out of
     *  `3DEffect.ini`, and a non-endless scene attached as an endless
     *  instance (an aura whose art happens to declare `loopTime 1`) must
     *  still not be reaped. The instance's ROLE decides its lifecycle; the
     *  scene decides its animation.
     */
    this.transient = !!opts.transient;
    /** When this instance was spawned, on the CALLER'S clock, in ms.
     *
     *  Opaque here -- gl.js never reads a wall clock -- and stored so that a
     *  transient can be re-phased when the shared clock restarts under it.
     *  See `Viewer.rebaseTransients`. Meaningless for an endless instance,
     *  whose phase its composer recomputes on every attach. */
    this.spawnAt = +opts.spawnAt || 0;
    this.anchor = opts.anchor ? Float32Array.from(opts.anchor) : Float32Array.from(FX.IDENT);
    this.textureKeys = opts.textureKeys || {};   // layer index -> texture key
    this.spawn = 0;
    this.frame = 0;
    this.done = false;
    this.ribbons = new Map();
    this.layers = [];
    this._build();
  }

  _build() {
    const gl = this.gl;
    for (const lay of this.def.layers || []) {
      const parts = [];
      for (const p of lay.parts || []) {
        if (p.kind === 'phy') {
          const g = p.geometry;
          if (!g || !g.indices.length) continue;
          parts.push({
            kind: 'phy', src: p, meta: g,
            vbo: buf(gl, g.positions, Float32Array),
            nbo: buf(gl, g.normals, Float32Array),
            tbo: buf(gl, g.uv0, Float32Array),
            ibo: buf(gl, g.indices, Uint16Array, gl.ELEMENT_ARRAY_BUFFER),
            count: g.indices.length,
          });
        } else if (p.kind === 'shape' && p.line && p.line.length === 2) {
          // Two dynamic buffers: the ribbon is rebuilt every frame from the
          // rolling pair history.
          parts.push({
            kind: 'shape', src: p,
            vbo: gl.createBuffer(), tbo: gl.createBuffer(),
            count: 0, capacity: 0,
          });
        } else if (p.kind === 'particle' && p.decoded && p.frames &&
                   p.frames.length) {
          // Two dynamic buffers, like a ribbon: the quads are rebuilt every
          // frame because they face the camera, so they change when the
          // camera moves and not only when the clock does. Sized once for the
          // system's peak so the common frame does no allocation.
          const cap = Math.max(1, p.peakParticles || p.maxParticles || 1);
          parts.push({
            kind: 'particle', src: p,
            vbo: gl.createBuffer(), tbo: gl.createBuffer(),
            count: 0, capacity: cap, alpha: 1,
          });
          gl.bindBuffer(gl.ARRAY_BUFFER, parts[parts.length - 1].vbo);
          gl.bufferData(gl.ARRAY_BUFFER, cap * 18 * 4, gl.DYNAMIC_DRAW);
          gl.bindBuffer(gl.ARRAY_BUFFER, parts[parts.length - 1].tbo);
          gl.bufferData(gl.ARRAY_BUFFER, cap * 12 * 4, gl.DYNAMIC_DRAW);
        }
        // A particle chunk that did not decode still arrives in the payload
        // with `decoded: false`; it is skipped rather than faked.
      }
      this.layers.push({ src: lay, parts });
    }
  }

  dispose() {
    const gl = this.gl;
    for (const l of this.layers) for (const p of l.parts) {
      for (const k of ['vbo', 'nbo', 'tbo', 'ibo']) if (p[k]) gl.deleteBuffer(p[k]);
    }
    this.layers = [];
  }

  /** Advance to `elapsed` ms since spawn. Returns the frame state. */
  tick(elapsed, parentMatrix) {
    const st = FX.frameAt(elapsed, {
      frameIntervalMs: this.def.frameIntervalMs,
      frames: this.def.effectiveFrames || this.def.frames,
      loopTime: this.def.loopTime, loopIntervalMs: this.def.loopIntervalMs,
      delayMs: this.def.delayMs, endless: this.def.endless,
    });
    this.frame = st.frame;
    this.done = st.done;
    this.state = st;
    // The trail is smeared by the PARENT's movement, so it has to be advanced
    // once per tick with wherever the parent is now -- not recomputed from
    // scratch at a frame index.
    if (!st.waiting && !st.gap) this._advanceRibbons(parentMatrix || this.anchor);
    return st;
  }

  reset() {
    this.ribbons.clear();
    this.frame = 0;
    this.done = false;
  }

  _world(parentMatrix) {
    const o = this.def.offsetRender || [0, 0, 0];
    const off = FX.translation(o[0], o[1], o[2]);
    return FX.mul(Array.from(parentMatrix || this.anchor), off);
  }

  _advanceRibbons(parentMatrix) {
    const world = this._world(parentMatrix);
    for (let li = 0; li < this.layers.length; li++) {
      for (let pi = 0; pi < this.layers[li].parts.length; pi++) {
        const p = this.layers[li].parts[pi];
        if (p.kind !== 'shape') continue;
        const key = li + ':' + pi;
        let hist = this.ribbons.get(key);
        if (!hist) { hist = []; this.ribbons.set(key, hist); }
        const smot = p.src.smot || [];
        const m = smot.length
          ? FX.mul(world, smot[this.frame % smot.length])
          : world;
        FX.ribbonAdvance(p.src.line, m, hist, p.src.maxPairs || 36, p.src.subdiv || 5);
        this._uploadRibbon(p, hist);
      }
    }
  }

  /** Rebuild one particle part's quads for `frame` and upload them.
   *
   *  Returns the vertex count to draw (0 for an empty frame, which is the
   *  common case). Called from the draw pass rather than from `tick()`,
   *  because a billboard depends on where the camera is and the camera can
   *  move without the clock advancing -- ribbons are the opposite and are
   *  advanced in `tick()`. */
  uploadParticles(p, frame, world, right, up) {
    const gl = this.gl;
    const q = FX.particleQuads(p.src, frame, world, right, up);
    p.alpha = q.alpha;
    p.count = q.n * 6;
    if (!q.n) return 0;
    gl.bindBuffer(gl.ARRAY_BUFFER, p.vbo);
    if (q.n > p.capacity) gl.bufferData(gl.ARRAY_BUFFER, q.pos, gl.DYNAMIC_DRAW);
    else gl.bufferSubData(gl.ARRAY_BUFFER, 0, q.pos);
    gl.bindBuffer(gl.ARRAY_BUFFER, p.tbo);
    if (q.n > p.capacity) gl.bufferData(gl.ARRAY_BUFFER, q.uv, gl.DYNAMIC_DRAW);
    else gl.bufferSubData(gl.ARRAY_BUFFER, 0, q.uv);
    if (q.n > p.capacity) p.capacity = q.n;
    return p.count;
  }

  /** A ribbon is a triangle strip through the pair list: U runs along the
   *  strip (oldest 0 -> newest 1), V across it (the two blade endpoints). */
  _uploadRibbon(p, hist) {
    const gl = this.gl;
    const n = hist.length;
    p.count = n * 2;
    if (n < 2) return;
    const pos = new Float32Array(n * 6);
    const uv = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) {
      const [a, b] = hist[i];
      pos[i*6+0] = a[0]; pos[i*6+1] = a[1]; pos[i*6+2] = a[2];
      pos[i*6+3] = b[0]; pos[i*6+4] = b[1]; pos[i*6+5] = b[2];
      const u = i / (n - 1);
      uv[i*4+0] = u; uv[i*4+1] = 0;
      uv[i*4+2] = u; uv[i*4+3] = 1;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, p.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, p.tbo);
    gl.bufferData(gl.ARRAY_BUFFER, uv, gl.DYNAMIC_DRAW);
  }
}

function buf(gl, data, Type, target) {
  const b = gl.createBuffer();
  const t = target || gl.ARRAY_BUFFER;
  gl.bindBuffer(t, b);
  gl.bufferData(t, new Type(data), gl.STATIC_DRAW);
  return b;
}

window.FX = FX;
window.EffectInstance = EffectInstance;
