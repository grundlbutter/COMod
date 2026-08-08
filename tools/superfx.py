#!/usr/bin/env python3
r"""superfx.py -- "super" weapon effects, anchored to the weapon.

The always-on glow a top-quality weapon carries -- `c3/effect/blade/410099.c3`
and its 800-odd siblings.  `docs/effects.md` already covers *which* effect a
weapon gets and *how* an effect plays; the thing that was missing, and the
thing the viewer got wrong, is *where it goes*.  It is not a world-space
object: it rides the weapon socket.

    p_world = p_bind x M_fxBone(frame_fx) x M_offset x M_socket x M_role

`M_socket` is the same `v_l_weapon` / `v_r_weapon` matrix the weapon itself
uses (`docs/attachment.md` sections 2 and 4).  The effect is a **sibling** of
the weapon at that socket, not a child of it: it must NOT inherit the weapon's
own bone-0 matrix.  Proven in `--validate` section 3 -- the raw effect geometry
already occupies the space the weapon reaches *after* its own bone-0 matrix,
so applying that matrix again shrinks the glow by the weapon's own scale
(0.25x on a Blade) and buries it in the hilt.

    py -3 tools/superfx.py --weapon 410099          # resolve one weapon
    py -3 tools/superfx.py --families               # every attachable family
    py -3 tools/superfx.py --anchor 002135000 410099   # the actual matrices
    py -3 tools/superfx.py --validate               # the numbers in the doc

As a module::

    from superfx import SuperFxDB
    db = SuperFxDB()
    sfx = db.super_effect("410099")             # -> SuperEffect | None
    M   = db.anchor(body_mesh, "r_weapon", frame=0, motion_set=clip.motion)
"""
from __future__ import annotations

import argparse
import collections
import copy
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import c3phy                                              # noqa: E402
import effects as fx                                      # noqa: E402
import attach                                             # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot              # noqa: E402

Mat4 = tuple

#: The action group that carries the always-on effect (`docs/effects.md` 2.1).
AURA_ACTION = "999"

#: Slot -> the `[Dumy]` socket its effect anchors to.  Same table the weapon
#: itself uses; `attach.SLOT_SOCKET` is the authority.
SLOT_SOCKET = {"r_weapon": "v_r_weapon", "l_weapon": "v_l_weapon",
               "shield": "v_l_shield", "body": None, "armet": "v_armet"}


def translation(x: float, y: float, z: float) -> Mat4:
    return (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            x, y, z, 1.0)


# ---------------------------------------------------------------------------

@dataclass
class SuperLayer:
    index: int
    effect_id: str
    mesh: Optional[str]
    texture_id: str
    texture: Optional[str]
    asb: int
    adb: int
    scale: Optional[float] = None

    @property
    def resolved(self) -> bool:
        return bool(self.mesh and self.texture)


@dataclass
class SuperEffect:
    """The always-on effect of one equipment appearance, ready to place."""
    appearance: str
    slot: str
    name: str
    frame_interval_ms: int
    loop_time: int
    loop_interval_ms: int
    delay_ms: int
    offset: tuple
    layers: list[SuperLayer] = field(default_factory=list)
    frames: int = 0
    family: str = ""

    @property
    def endless(self) -> bool:
        return self.loop_time >= 99999

    @property
    def offset_matrix(self) -> Mat4:
        return translation(*self.offset)

    @property
    def resolved(self) -> bool:
        return bool(self.layers) and all(L.resolved for L in self.layers)


class SuperFxDB:
    """Weapon appearance -> its super effect, plus the anchoring transform."""

    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        self.fx = fx.EffectDB(self.root, assets)
        self.assets = self.fx.assets
        self._mesh_cache: dict[str, attach.PartMesh] = {}

    # -- resolution --------------------------------------------------------
    def effect_name(self, appearance: str, *, shape: str = "999") -> str:
        """`Action3DEffect[<shape>.999.<type>.<sub>]`, wildcards honoured.

        VERIFIED table, INFERRED matcher (`docs/effects.md` 2.4): the row with
        the most non-wildcard fields wins.
        """
        name = self.fx.lookup_action_effect(appearance, AURA_ACTION, shape=shape)
        if name and name.lower() != "none":
            return name
        # 6090 dropped the indirection. CCO ships 826 always-on rows of the
        # form `999.999.410.009=410009` -- the effect is named after the
        # appearance id, and the row exists only to say so. 6090 ships ZERO
        # action-999 rows and instead defines the effect directly:
        # `3DEffect.ini [410199]` is Rainbow Blade Super's aura, and no such
        # section exists for 410195 or 410196. So the aura is declared by the
        # existence of an effect bearing the appearance's own id.
        #
        # VERIFIED both ways on the 6090 base: 410009, 410099 and 410199 all
        # resolve (every one a Super, ...9), and the Normal and Refined ids of
        # the same family resolve to nothing. The author confirms a Super Rainbow
        # Blade glows in the real client, which is what sent us looking.
        ident = (appearance or "").strip()
        if ident and self.fx.resolve(ident) is not None:
            return ident
        return ""

    def super_effect(self, appearance: str, slot: str = "r_weapon",
                     *, shape: str = "999") -> Optional[SuperEffect]:
        name = self.effect_name(appearance, shape=shape)
        if not name:
            return None
        d = self.fx.resolve(name)
        if d is None:
            return None
        layers: list[SuperLayer] = []
        fam = ""
        for i, L in enumerate(d.layers):
            mesh = self.fx.objs.get(str(L.effect_id))
            tex = self.fx.textures.get(str(L.texture_id))
            mesh = mesh.replace("\\", "/") if mesh else None
            tex = tex.replace("\\", "/") if tex else None
            if mesh and not fam:
                fam = family_of(mesh)
            layers.append(SuperLayer(i, str(L.effect_id), mesh,
                                     str(L.texture_id), tex, L.asb, L.adb,
                                     L.scale))
        frames, _eff = self.fx.frames_for(name)
        return SuperEffect(appearance, slot, name, d.frame_interval,
                           d.loop_time, d.loop_interval, d.delay, d.offset,
                           layers, frames, fam)

    # -- geometry ----------------------------------------------------------
    def mesh(self, logical: str) -> Optional[attach.PartMesh]:
        if logical in self._mesh_cache:
            return self._mesh_cache[logical]
        try:
            m = attach.PartMesh.load(self.assets, logical)
        except Exception:
            return None
        self._mesh_cache[logical] = m
        return m

    # -- the anchor --------------------------------------------------------
    @staticmethod
    def anchor(body: attach.PartMesh, slot: str = "r_weapon", frame: int = 0,
               motion_set: Optional[attach.PartMesh] = None,
               offset: tuple = (0.0, 0.0, 0.0),
               role: Mat4 = attach.IDENTITY) -> Optional[Mat4]:
        """`M_offset x M_socket x M_role` -- everything below the effect's own
        per-part matrix.

        `motion_set` is the action clip bound over the body (`anim.Clip.motion`);
        pass it so the glow follows the swing rather than sitting at the idle
        pose.  Returns None when the body has no such socket.
        """
        dumy = SLOT_SOCKET.get(slot, slot)
        if dumy is None:
            S: Optional[Mat4] = attach.IDENTITY
        else:
            S = attach.socket_matrix(body, dumy, frame, motion_set)
        if S is None:
            return None
        M = S if offset == (0.0, 0.0, 0.0) else fx.mat_mul(translation(*offset), S)
        return fx.mat_mul(M, role) if role is not attach.IDENTITY else M

    def world_vertices(self, effect_mesh: attach.PartMesh, anchor: Mat4,
                       frame: int = 0):
        """Every vertex of an effect mesh, placed.

        `p x M_partBone(vertex.bone0, frame) x anchor` -- the engine always
        applies the part's own motion (`Mesh::Draw` passes `useMotion = true`,
        graphic.dll `0x2607E`).
        """
        for c in effect_mesh.bind_chunks():
            q = copy.deepcopy(c.phy)
            c3phy.apply_matrix_to(q)
            for v in q.vertices:
                yield attach.transform_vertex(v, c.motion, anchor, frame)


def family_of(logical: str) -> str:
    """`c3/effect/blade/410009.C3` -> `blade`."""
    parts = logical.lower().replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "c3" and parts[1] == "effect":
        return parts[2]
    return "/".join(parts[:-1])


# ---------------------------------------------------------------------------
# measurement helpers (the anchoring evidence)
# ---------------------------------------------------------------------------

def _bbox(part: attach.PartMesh, world: Mat4 = attach.IDENTITY,
          frame: int = 0, use_motion: bool = True):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    n = 0
    for c in part.bind_chunks():
        if attach.is_socket_name(c.name):
            continue
        q = copy.deepcopy(c.phy)
        c3phy.apply_matrix_to(q)
        for v in q.vertices:
            p = attach.transform_vertex(v, c.motion if use_motion else None,
                                        world, frame)
            n += 1
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
    return None if not n else (tuple(lo), tuple(hi))


def _span(bb) -> float:
    return 0.0 if bb is None else max(bb[1][i] - bb[0][i] for i in range(3))


def _union_bbox(db: "SuperFxDB", paths: Iterable[str],
                world: Mat4 = attach.IDENTITY, frame: int = 0):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    got = False
    for p in paths:
        m = db.mesh(p)
        if m is None:
            continue
        bb = _bbox(m, world, frame)
        if bb is None:
            continue
        got = True
        for i in range(3):
            lo[i] = min(lo[i], bb[0][i])
            hi[i] = max(hi[i], bb[1][i])
    return (tuple(lo), tuple(hi)) if got else None


def weapon_mesh_path(fxdb: SuperFxDB, appearance: str) -> Optional[str]:
    sec = fxdb.fx.weapon_appearances.get(appearance)
    if not sec:
        return None
    mid = sec.get("Mesh0")
    if not mid:
        return None
    p = fxdb.fx.meshes.get(mid)
    if p:
        return p.replace("\\", "/")
    loc = fxdb.assets.resolve_asset(mid, "mesh")
    return loc.logical if loc else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_weapon(db: SuperFxDB, appearance: str) -> None:
    for slot in ("r_weapon",):
        sfx = db.super_effect(appearance, slot)
        if sfx is None:
            print("%s: no always-on effect (Action3DEffect 999.999.%s.%s)"
                  % (appearance, appearance[:-3], appearance[-3:]))
            return
        print("weapon %s   effect [%s]   family %s" %
              (appearance, sfx.name, sfx.family or "-"))
        print("  timing   %d ms/frame (%.1f fps)  loop %s  delay %d ms  "
              "loopInterval %d ms" %
              (sfx.frame_interval_ms, 1000.0 / max(1, sfx.frame_interval_ms),
               "forever" if sfx.endless else "%dx" % sfx.loop_time,
               sfx.delay_ms, sfx.loop_interval_ms))
        print("  offset   %s   frames %d" % (sfx.offset, sfx.frames))
        for L in sfx.layers:
            print("  layer %d  obj %-6s %-42s %s" %
                  (L.index, L.effect_id, L.mesh or "UNRESOLVED",
                   "OK" if L.mesh else ""))
            print("           tex %-6s %-42s blend %s -> %s" %
                  (L.texture_id, L.texture or "UNRESOLVED",
                   fx.blend_name(L.asb), fx.blend_name(L.adb)))
            if L.mesh:
                m = db.mesh(L.mesh)
                if m:
                    kinds = collections.Counter(
                        ("PHY+MOTI" if c.motion else "PHY") for c in m.bind_chunks())
                    print("           geom %d chunk(s) %s   %s" %
                          (len(m.chunks), dict(kinds),
                           "  ".join("%s(%d verts)" % (c.name, len(c.phy.vertices))
                                     for c in list(m.bind_chunks())[:6])))
        wp = weapon_mesh_path(db, appearance)
        if wp:
            w = db.mesh(wp)
            e = db.mesh(sfx.layers[0].mesh) if sfx.layers and sfx.layers[0].mesh else None
            if w and e:
                bw, be = _bbox(w), _bbox(e)
                print("  weapon mesh %s" % wp)
                print("     weapon placed span %.1f   effect placed span %.1f "
                      " (ratio %.2f)" % (_span(bw), _span(be),
                                         _span(be) / max(_span(bw), 1e-6)))


def cmd_anchor(db: SuperFxDB, body_appearance: str, appearance: str,
               slot: str, frame: int) -> None:
    cat = attach.Catalogue(db.root)
    bpath = (cat.appearance_mesh("armor.ini", body_appearance)
             or cat.mesh_path(body_appearance))
    body = cat.load(bpath) if bpath else None
    if body is None:
        print("cannot load body", body_appearance)
        return
    sfx = db.super_effect(appearance, slot)
    if sfx is None:
        print("no super effect on", appearance)
        return
    idle = cat.idle_motion(body_appearance, "000", "100")
    A = db.anchor(body, slot, frame, idle, sfx.offset)
    if A is None:
        print("body has no %s socket" % SLOT_SOCKET.get(slot, slot))
        return
    print("body %s (%s)   slot %s -> %s   frame %d"
          % (body_appearance, bpath, slot, SLOT_SOCKET[slot], frame))
    print("  M_socket x M_offset (row-vector, translation in row 3):")
    for r in range(4):
        print("     [%9.4f %9.4f %9.4f %9.4f]" % tuple(A[r * 4:r * 4 + 4]))
    print("  GL column-major (attach.to_render):")
    print("     %s" % [round(x, 4) for x in attach.to_render(A)])
    wp = weapon_mesh_path(db, appearance)
    w = db.mesh(wp) if wp else None
    for L in sfx.layers:
        e = db.mesh(L.mesh) if L.mesh else None
        if e is None:
            continue
        be = _bbox(e, A, 0)
        print("  layer %d %s placed  x[%.1f,%.1f] y[%.1f,%.1f] up[%.1f,%.1f]"
              % (L.index, L.mesh, be[0][0], be[1][0], be[0][1], be[1][1],
                 -be[1][2], -be[0][2]))
    if w:
        bw = _bbox(w, A, 0)
        print("  weapon   %s placed  x[%.1f,%.1f] y[%.1f,%.1f] up[%.1f,%.1f]"
              % (wp, bw[0][0], bw[1][0], bw[0][1], bw[1][1],
                 -bw[1][2], -bw[0][2]))
        print("  -> the two occupy the same volume, which is the point.")


def cmd_families(db: SuperFxDB) -> None:
    """Every effect family reachable from an always-on equipment effect."""
    fams: dict[str, dict] = {}
    weapons = set(db.fx.weapon_appearances)
    for r in db.fx.action_rules:
        if r.action != AURA_ACTION or not r.effect or r.effect == "none":
            continue
        d = db.fx.resolve(r.effect)
        if d is None:
            continue
        for L in d.layers:
            p = db.fx.objs.get(str(L.effect_id))
            if not p:
                continue
            f = family_of(p.replace("\\", "/"))
            e = fams.setdefault(f, {"layers": 0, "effects": set(),
                                    "appearances": set(), "weapon": 0,
                                    "other": 0})
            e["layers"] += 1
            e["effects"].add(r.effect)
            e["appearances"].add(r.appearance)
            if r.appearance in weapons:
                e["weapon"] += 1
            else:
                e["other"] += 1
    print("effect families reachable from an always-on (action 999) rule\n")
    print("%-22s %8s %8s %10s %10s %s" %
          ("family", "layers", "effects", "appearance", "in weapon.ini", "anchor"))
    for f in sorted(fams, key=lambda k: -fams[k]["layers"]):
        e = fams[f]
        anchor = "weapon socket" if e["weapon"] >= e["other"] else "body"
        print("%-22s %8d %8d %10d %10d   %s" %
              (f, e["layers"], len(e["effects"]), len(e["appearances"]),
               e["weapon"], anchor))
    print()
    print("Families whose appearances are NOT weapon.ini rows are garment /")
    print("cosmetic auras and anchor to the body, not to a weapon socket.")


# ---------------------------------------------------------------------------

def validate(db: SuperFxDB, limit: Optional[int] = None) -> int:
    bad = 0
    weapons = set(db.fx.weapon_appearances)

    print("=" * 78)
    print("1. which equipment carries an always-on (super) effect")
    print("=" * 78)
    rows = [r for r in db.fx.action_rules
            if r.action == AURA_ACTION and r.effect and r.effect != "none"]
    apps = {r.appearance for r in rows}
    inw = {a for a in apps if a in weapons}
    print("  Action3DEffect rows with action 999 and a real effect : %d" % len(rows))
    print("  distinct appearances                                   : %d" % len(apps))
    print("  ... that are literal weapon.ini sections               : %d" % len(inw))
    print("  ... that are not (garment / cosmetic auras)            : %d"
          % (len(apps) - len(inw)))
    q = collections.Counter(a[-1] for a in inw)
    print("  quality digit of the weapon appearances                : %s"
          % dict(sorted(q.items())))
    tiers = collections.Counter(a[:-3] for a in inw)
    print("  weapon types covered (%d): %s"
          % (len(tiers), " ".join(sorted(tiers))))

    print()
    print("=" * 78)
    print("2. c3/effect/blade/ -- is it a filename convention or a table?")
    print("=" * 78)
    blade = {k: v for k, v in db.fx.objs.items()
             if v.replace("\\", "/").lower().startswith("c3/effect/blade/")}
    print("  3DEffectObj.ini entries under c3/effect/blade/ : %d" % len(blade))
    ids = sorted(int(k) for k in blade if k.isdigit())
    print("  object-id range                                : %d .. %d"
          % (min(ids), max(ids)))
    named = {k: v for k, v in blade.items()
             if v.rsplit("/", 1)[-1].split(".")[0].split("-")[0].isdigit()}
    print("  ... whose file stem is an appearance id        : %d" % len(named))
    hits = 0
    for k, v in named.items():
        stem = v.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0].split("-")[0]
        if stem in weapons:
            hits += 1
    print("  ... and that stem is a real weapon.ini section : %d" % hits)
    reach = 0
    for r in rows:
        d = db.fx.resolve(r.effect)
        if d and any(str(L.effect_id) in blade for L in d.layers):
            reach += 1
    print("  appearances reaching a blade/ mesh through the table: %d" % reach)
    print()
    print("  VERDICT: it is a TABLE, not a filename convention.  Every blade/")
    print("  mesh is reached as Action3DEffect -> 3DEffect.ini section ->")
    print("  EffectId<i> -> 3DEffectObj.ini.  The stem happening to equal the")
    print("  weapon appearance is an authoring habit; nothing looks it up by")
    print("  name.  (Example: 3DEffectObj 2196 = c3/effect/blade/410009.C3,")
    print("  named by 3DEffect.ini [410009] EffectId0, named by")
    print("  Action3DEffect 999.999.410.009.)")

    print()
    print("=" * 78)
    print("3. the anchor: effect geometry vs weapon geometry, same local frame")
    print("   the effect is authored where the weapon lands AFTER its own")
    print("   bone-0 matrix, so the effect must NOT inherit that matrix")
    print("=" * 78)
    print("  the 410 Blade family in full -- one effect mesh per appearance,")
    print("  measured against the mesh weapon.ini gives that same appearance:")
    print()
    print("  %-24s %-20s %8s %8s %7s %8s %8s" %
          ("effect mesh", "weapon mesh", "eff_len", "wpn_len", "ratio",
           "d_hilt", "d_tip"))
    per_family: dict[str, list] = collections.defaultdict(list)
    wrongs: list[float] = []
    wrong_blade: list[float] = []
    shown: set = set()
    seen_pair: set = set()
    n = 0
    for app in sorted(inw):
        sfx = db.super_effect(app)
        if sfx is None or not sfx.layers:
            continue
        wp = weapon_mesh_path(db, app)
        if not wp:
            continue
        w = db.mesh(wp)
        if w is None:
            continue
        be = _union_bbox(db, [L.mesh for L in sfx.layers if L.mesh])
        bw = _bbox(w)
        if be is None or bw is None or _span(bw) < 1e-6:
            continue
        m0 = (sfx.layers[0].mesh or "")
        if (m0, wp) in seen_pair:            # 819 appearances, far fewer pairs
            continue
        seen_pair.add((m0, wp))
        ax = max(range(3), key=lambda i: bw[1][i] - bw[0][i])
        row = (_span(be), _span(bw), _span(be) / _span(bw),
               abs(be[0][ax] - bw[0][ax]), be[1][ax] - bw[1][ax])
        per_family[sfx.family].append(row)
        wc = next(iter(w.bind_chunks()), None)
        if wc is not None:
            M = wc.matrix(0, 0)
            sc = attach.mat_scale_rot(M)[0]
            if abs(sc - 1.0) > 0.05:          # only where it can be told apart
                bwrong = _union_bbox(db, [L.mesh for L in sfx.layers if L.mesh], M)
                if bwrong:
                    wrongs.append(_span(bwrong) / _span(bw))
                    if sfx.family == "blade":
                        wrong_blade.append(_span(bwrong) / _span(bw))
        n += 1
        if sfx.family == "blade" and app.startswith("410"):
            print("  %-24s %-20s %8.1f %8.1f %7.2f %8.1f %+8.1f" %
                  (m0.rsplit("/", 1)[-1], wp.rsplit("/", 1)[-1], row[0],
                   row[1], row[2], row[3], row[4]))
        if limit and n >= limit:
            break

    print()
    print("  per family, over %d distinct (effect mesh, weapon mesh) pairs." % n)
    print("  d_hilt / d_tip are the gaps between the effect's and the weapon's")
    print("  ends along the weapon's longest axis, in the SAME local frame:")
    print("  %-22s %6s %8s %8s %8s %9s %9s" %
          ("family", "n", "medRatio", "p10", "p90", "med d_hilt", "med d_tip"))
    allr: list[float] = []
    alld: list[float] = []
    for f in sorted(per_family, key=lambda k: -len(per_family[k])):
        rows_f = per_family[f]
        r = [x[2] for x in rows_f]
        d = [x[3] for x in rows_f]
        t = [x[4] for x in rows_f]
        allr += r
        alld += d
        print("  %-22s %6d %8.2f %8.2f %8.2f %9.1f %+9.1f" %
              (f, len(rows_f), statistics.median(r), _pct(r, 10), _pct(r, 90),
               statistics.median(d), statistics.median(t)))
    if allr:
        print()
        print("  CORRECT anchor  (effect x M_socket), all families:")
        print("     span ratio effect/weapon   median %.2f  p10 %.2f  p90 %.2f"
              % (statistics.median(allr), _pct(allr, 10), _pct(allr, 90)))
        print("     hilt-end offset along the blade axis  median %.1f units"
              % statistics.median(alld))
        blade = [x[2] for x in per_family.get("blade", [])]
        bladed = [x[3] for x in per_family.get("blade", [])]
        if blade:
            print("     blade family only: ratio median %.2f, hilt offset "
                  "median %.1f units" % (statistics.median(blade),
                                         statistics.median(bladed)))
        if wrongs:
            print("  WRONG anchor (effect x M_weaponBone0 x M_socket), on the")
            print("  %d weapons whose own bone-0 matrix is not unit scale:"
                  % len(wrongs))
            print("     span ratio effect/weapon   median %.2f  p10 %.2f"
                  % (statistics.median(wrongs), _pct(wrongs, 10)))
        # the single decisive case, quoted in docs/animation.md
        w = db.mesh(weapon_mesh_path(db, "410009") or "")
        sfx = db.super_effect("410009")
        if w is not None and sfx is not None and sfx.layers[0].mesh:
            M = next(iter(w.bind_chunks())).matrix(0, 0)
            sc = attach.mat_scale_rot(M)[0]
            e = db.mesh(sfx.layers[0].mesh)
            print()
            print("  Decisive single case -- 410009, whose weapon mesh's own")
            print("  bone-0 matrix is a uniform %.3f scale "
                  "(docs/attachment.md section 6):" % sc)
            print("     weapon placed span            %6.1f" % _span(_bbox(w)))
            print("     effect x M_socket             %6.1f   <- matches"
                  % _span(_bbox(e)))
            print("     effect x M_wpnBone0 x M_socket %5.1f   <- a quarter "
                  "size, buried in the grip" % _span(_bbox(e, M)))
        if not (0.8 <= statistics.median(allr) <= 3.0):
            print("     !! effect/weapon ratio is not in the expected band")
            bad += 1
        if blade and statistics.median(bladed) > 25:
            print("     !! blade hilt ends do not line up")
            bad += 1

    print()
    print("=" * 78)
    print("4. asset resolution for every super effect")
    print("=" * 78)
    tot = res = miss_mesh = miss_tex = 0
    per_family: dict[str, int] = collections.Counter()
    for app in sorted(apps):
        sfx = db.super_effect(app)
        if sfx is None:
            continue
        tot += 1
        ok = True
        for L in sfx.layers:
            if not L.mesh:
                miss_mesh += 1
                ok = False
            if not L.texture:
                miss_tex += 1
                ok = False
        if ok:
            res += 1
        per_family[sfx.family or "?"] += 1
    print("  super effects resolved            : %d of %d" % (res, tot))
    print("  layers with no mesh path          : %d" % miss_mesh)
    print("  layers with no texture path       : %d" % miss_tex)
    print("  by family: %s" % dict(per_family.most_common()))
    if res != tot:
        bad += 1

    print()
    print("=" * 78)
    print("5. geometry loads and every part carries its own MOTI")
    print("=" * 78)
    files = ok = no_motion = failed = 0
    parts = 0
    for app in sorted(inw):
        sfx = db.super_effect(app)
        if sfx is None:
            continue
        for L in sfx.layers:
            if not L.mesh:
                continue
            files += 1
            m = db.mesh(L.mesh)
            if m is None:
                failed += 1
                continue
            ok += 1
            for c in m.bind_chunks():
                parts += 1
                if c.motion is None:
                    no_motion += 1
        if limit and files >= limit:
            break
    print("  layer meshes opened   : %d of %d" % (ok, files))
    print("  drawable parts        : %d" % parts)
    print("  parts with no MOTI    : %d" % no_motion)
    if failed:
        print("  !! %d meshes named by the ini are absent" % failed)

    print()
    print("=" * 78)
    print("6. placement on a real body: 002135000 holding 410099 at v_r_weapon")
    print("=" * 78)
    cat = attach.Catalogue(db.root)
    bpath = (cat.appearance_mesh("armor.ini", "002135000")
             or cat.mesh_path("002135000") or "c3/mesh/002135000.c3")
    body = cat.load(bpath)
    idle = cat.idle_motion("002135000", "000", "100")
    if body is not None:
        h = _span(_bbox(body))
        for app in ("410009", "410099", "410229", "560009"):
            sfx = db.super_effect(app)
            if sfx is None or not sfx.layers or not sfx.layers[0].mesh:
                print("  %s -- no effect" % app)
                continue
            A = db.anchor(body, "r_weapon", 0, idle, sfx.offset)
            e = db.mesh(sfx.layers[0].mesh)
            wp = weapon_mesh_path(db, app)
            w = db.mesh(wp) if wp else None
            be = _bbox(e, A, 0)
            bw = _bbox(w, A, 0) if w else None
            print("  %s  effect %-12s placed x[%7.1f,%7.1f] y[%7.1f,%7.1f] "
                  "up[%7.1f,%7.1f]" % (app, sfx.name, be[0][0], be[1][0],
                                       be[0][1], be[1][1], -be[1][2], -be[0][2]))
            if bw:
                print("  %s  weapon %-12s placed x[%7.1f,%7.1f] y[%7.1f,%7.1f] "
                      "up[%7.1f,%7.1f]" % (" " * len(app), wp.rsplit("/", 1)[-1],
                                           bw[0][0], bw[1][0], bw[0][1], bw[1][1],
                                           -bw[1][2], -bw[0][2]))
                sep = max(abs((be[0][i] + be[1][i]) / 2 - (bw[0][i] + bw[1][i]) / 2)
                          for i in range(3))
                print("  %s  centre separation %.1f units (body height %.1f)"
                      % (" " * len(app), sep, h))
                if sep > 0.5 * h:
                    print("     !! effect is not on the weapon")
                    bad += 1

    print()
    print("failed checks:", bad)
    return 1 if bad else 0


def _pct(v: list[float], p: int) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(len(s) * p / 100)))]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--weapon")
    ap.add_argument("--anchor", nargs=2, metavar=("BODY", "WEAPON"))
    ap.add_argument("--slot", default="r_weapon")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--families", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    db = SuperFxDB(a.root)
    if a.weapon:
        cmd_weapon(db, a.weapon)
        return 0
    if a.anchor:
        cmd_anchor(db, a.anchor[0], a.anchor[1], a.slot, a.frame)
        return 0
    if a.families:
        cmd_families(db)
        return 0
    if a.validate:
        return validate(db, a.limit)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
