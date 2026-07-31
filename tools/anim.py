#!/usr/bin/env python3
r"""anim.py -- action animation playback for Conquer Online characters.

The data layer behind "show me swing, run and jump".  Resolves an action to a
motion set out of ``ini/3dmotion.ini``, binds it over a body mesh by ordinal
(``C3Mesh::SetMotion``, graphic.dll ``0x277C0``), and hands back per-frame bone
matrices plus the timing and loop model a renderer needs.

Everything here is either read out of the shipped tables or measured on the
shipped motions; nothing is curve-fitted.  The spec, with per-claim
verified/inferred marks and citations, is ``docs/animation.md``.

    py -3 tools/anim.py --actions                       # the action-code table
    py -3 tools/anim.py --keys                          # 3dmotion.ini key forms
    py -3 tools/anim.py --clip 002135000 100            # resolve + describe
    py -3 tools/anim.py --clip 002135000 401 --weapon 410009
    py -3 tools/anim.py --sequence 002135000 run        # the chained run cycle
    py -3 tools/anim.py --root-motion 002135000 131     # the jump arc
    py -3 tools/anim.py --actionctrl                    # ini/ActionCtrl.ini
    py -3 tools/anim.py --validate                      # the numbers in the doc

As a module::

    from anim import AnimDB
    db   = AnimDB()
    clip = db.clip("002135000", "401", weapon="410009")
    for f in clip.frames():
        M = clip.matrix(chunk=0, bone=0, frame=f)     # row-vector 4x4
"""
from __future__ import annotations

import argparse
import collections
import copy
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import c3phy                                              # noqa: E402
import effects as fx                                      # noqa: E402
import attach                                             # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot, parse_ini    # noqa: E402

Mat4 = tuple

# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------

#: Milliseconds per frame for character action motion.  **INFERRED** -- no ini
#: and no clean DLL states it; the owner of the clock is the packed exe.
#: 41 ms is chosen because every one of the 6,051 `ini/Action3DEffect.ini` rows
#: for the swing actions 401/402/403 and the cast actions 900/901/903 names an
#: effect whose `3DEffect.ini FrameInterval` is exactly 41, and those effects
#: are frame-locked to the body motion that spawns them.  41 ms is 24.4 fps,
#: the 3DSMax film rate these assets were authored at.
#: Competing candidates, both documented in `docs/animation.md` §5:
#:   33 -- graphic.dll divides the STEP UV rates by the literal 33.0 at
#:         RVA 0x5B44C / 0x5B4DD, and 33 is `3DEffect.json`'s default.
#:   50 -- `sound/walk.wav` is 1.032 s against the 20-frame walk clip, and the
#:         legacy 2D `ini/effect.ini` uses FrameInterval=50 throughout.
DEFAULT_FRAME_INTERVAL_MS = 41

#: `3DEffect.ini FrameInterval` of every effect bound to an action motion.
ACTION_LOCKED_EFFECT_INTERVAL_MS = 41


# ---------------------------------------------------------------------------
# the action vocabulary
# ---------------------------------------------------------------------------

VERIFIED = "verified"
INFERRED = "inferred"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Action:
    """One 3-digit action code."""
    code: str
    name: str
    group: str
    confidence: str
    evidence: str
    #: action that continues this one (run alternates feet, deaths hold a pose)
    chain: Optional[str] = None
    #: default loop behaviour, overridden by the measured classifier
    loop_hint: str = ""


def _A(code, name, group, conf, ev, chain=None, loop=""):
    return Action(code, name, group, conf, ev, chain, loop)


#: The action codes this build actually ships for a player body, with the
#: evidence for each.  `docs/animation.md` §3 is generated from this table.
ACTIONS: dict[str, Action] = {a.code: a for a in [
    # -- idle / stance -----------------------------------------------------
    _A("100", "stand / idle", "idle", VERIFIED,
       "the first motion of every shape; the action the always-on armour "
       "auras hang off (Action3DEffect 999.100.135.999); 19 of the 191 action "
       "codes a player body ships resolve to its 100.c3", loop="cyclic"),
    _A("101", "idle variant (long)", "idle", INFERRED,
       "c3/000N/000/101.c3, 60 frames, also used by 102/103", loop="cyclic"),
    _A("102", "idle variant", "idle", INFERRED, "aliases 101.c3", loop="cyclic"),
    _A("103", "idle variant", "idle", INFERRED, "aliases 101.c3", loop="cyclic"),
    _A("105", "combat stance", "idle", INFERRED,
       "own 16-frame clip, same length as 100 but a lower, hunched pose "
       "(top 159.8 vs 170.0 on 002135000)", loop="cyclic"),

    # -- locomotion --------------------------------------------------------
    _A("110", "walk", "move", VERIFIED,
       "ActionSound.ini <shape>.999.110 = sound/walk.wav", loop="cyclic"),
    _A("111", "walk", "move", VERIFIED,
       "aliases 110.c3; ActionSound 111 not present", loop="cyclic"),
    _A("115", "walk (variant 2)", "move", VERIFIED,
       "ActionSound.ini 115 = sound/walk.wav; a separate 20-frame clip that "
       "does NOT chain with 110 (cross-continuity ratio 16.7)", loop="cyclic"),
    _A("120", "run, left foot", "move", VERIFIED,
       "ActionSound.ini 120 = sound/runL.wav", chain="121", loop="chain"),
    _A("121", "run, right foot", "move", VERIFIED,
       "ActionSound.ini 121 = sound/runR.wav", chain="120", loop="chain"),
    _A("122", "run, left foot", "move", INFERRED, "aliases 120.c3",
       chain="123", loop="chain"),
    _A("123", "run, right foot", "move", INFERRED, "aliases 121.c3",
       chain="122", loop="chain"),
    _A("125", "run 2, left foot", "move", VERIFIED,
       "ActionSound.ini 125 = sound/runL.wav", chain="126", loop="chain"),
    _A("126", "run 2, right foot", "move", VERIFIED,
       "ActionSound.ini 126 = sound/runR.wav", chain="125", loop="chain"),
    _A("130", "jump (hop, in place)", "move", VERIFIED,
       "ActionSound.ini 130 = sound/jump.wav; centroid rises 91 -> 108 -> 91 "
       "and frame N-1 duplicates frame 0", loop="closed"),
    _A("131", "jump (leap)", "move", VERIFIED,
       "ActionSound.ini 131 = sound/jump.wav; centroid rises 92 -> 152 -> 82 "
       "-> 90, a full ballistic arc baked into the clip", loop="oneshot"),
    _A("132", "jump", "move", INFERRED, "aliases 130.c3", loop="closed"),

    # -- emotes / poses ----------------------------------------------------
    _A("140", "attack (alias)", "attack", INFERRED, "aliases 401.c3"),
    _A("150", "emote", "emote", UNKNOWN,
       "own 21-frame clip, upright, frame N-1 duplicates frame 0; also used "
       "by 151 and 180", loop="closed"),
    _A("151", "emote", "emote", UNKNOWN, "aliases 150.c3", loop="closed"),
    _A("160", "emote", "emote", UNKNOWN, "own 20-frame cyclic clip",
       loop="cyclic"),
    _A("170", "emote", "emote", UNKNOWN, "own 21-frame closed clip",
       loop="closed"),
    _A("180", "emote", "emote", UNKNOWN, "aliases 150.c3", loop="closed"),
    _A("190", "emote (raise arms)", "emote", UNKNOWN,
       "10 frames, the silhouette top rises 171 -> 175", loop="cyclic"),
    _A("200", "emote", "emote", UNKNOWN, "38 frames, upright", loop="closed"),
    _A("210", "kneel / crouch", "pose", INFERRED,
       "20-frame clip held at 123.5 units tall against a 170-unit body; "
       "frame N-1 duplicates frame 0", loop="closed"),
    _A("220", "kneel / crouch", "pose", INFERRED, "aliases 210.c3",
       loop="closed"),
    _A("230", "emote into hold", "pose", INFERRED,
       "38 frames that do not return to the start (wrap/step 23.9); its hold "
       "pose is action 231", chain="231", loop="oneshot"),
    _A("231", "hold of 230", "pose", VERIFIED,
       "2-frame track identical to the last frame of 230 on bodies 001/003/004",
       loop="cyclic"),
    _A("250", "sit", "pose", INFERRED,
       "20-frame cyclic clip held at 87.9 units, half body height",
       loop="cyclic"),
    _A("260", "sit", "pose", INFERRED, "aliases 250.c3", loop="cyclic"),
    _A("261", "sit", "pose", INFERRED, "aliases 250.c3", loop="cyclic"),
    _A("270", "lie down", "pose", INFERRED,
       "40 frames ending flat on the ground (top 170 -> 34); its hold pose is "
       "action 271", chain="271", loop="oneshot"),
    _A("271", "lying (hold of 270)", "pose", VERIFIED,
       "2-frame track identical to the last frame of 270 on 001/003/004",
       loop="cyclic"),
    _A("280", "emote", "emote", UNKNOWN, "16-frame cyclic clip", loop="cyclic"),
    _A("290", "dig / gather", "action", VERIFIED,
       "ActionSound.ini 290 = sound/dig.wav", loop="cyclic"),

    # -- reactions ---------------------------------------------------------
    _A("300", "hurt (light)", "react", INFERRED,
       "Action3DEffect suppresses every effect on 300/305/310/320/330/331/"
       "340/341 with the literal value `none`", loop="cyclic"),
    _A("305", "hurt", "react", INFERRED, "same suppression group",
       loop="cyclic"),
    _A("310", "knocked back", "react", INFERRED,
       "20-frame closed clip; 311-319 all alias it", loop="closed"),
    _A("320", "hurt", "react", VERIFIED,
       "ActionSound.ini 320 = sound/bruise0X.wav; 321-329 alias 320.c3",
       loop="closed"),
    _A("330", "die", "death", VERIFIED,
       "ActionSound.ini 330/332/334/336 = sound/d_monster0X.wav, and "
       "ActionMap3DEffect [104330999] plays `Feather` for a bird shape's 330; "
       "the clip ends with the silhouette top at 20.8 of 170",
       chain="331", loop="oneshot"),
    _A("331", "dead (corpse hold)", "death", VERIFIED,
       "2-frame track identical to the last frame of 330", loop="cyclic"),
    _A("332", "die variant", "death", VERIFIED,
       "ActionSound 332 = d_monster*.wav; aliases 330.c3 on player bodies",
       chain="333", loop="oneshot"),
    _A("333", "dead (hold of 332)", "death", INFERRED, "aliases 331.c3",
       loop="cyclic"),
    _A("334", "die variant", "death", VERIFIED, "ActionSound 334 = d_monster*",
       chain="335", loop="oneshot"),
    _A("335", "dead (hold of 334)", "death", INFERRED, "aliases 331.c3",
       loop="cyclic"),
    _A("336", "die variant", "death", VERIFIED, "ActionSound 336 = d_monster*",
       chain="337", loop="oneshot"),
    _A("337", "dead (hold of 336)", "death", INFERRED, "aliases 331.c3",
       loop="cyclic"),
    _A("340", "die (second family)", "death", VERIFIED,
       "175 shapes map 340 to their own 340.c3, whose bone-0 track collapses "
       "to the ground (monster 103: up 313 -> -15).  ActionSound 340 = "
       "sound/blk_monster0X.wav.  ini/ActionCtrl.ini carries a 5-point "
       "time->move curve for exactly 340/342/344/346/370",
       chain="341", loop="oneshot"),
    _A("341", "dead (hold of 340)", "death", VERIFIED,
       "2 frames equal to the last frame of 340", loop="cyclic"),
    _A("342", "die variant", "death", VERIFIED, "ActionSound 342 = blk_monster*",
       chain="343", loop="oneshot"),
    _A("343", "dead (hold of 342)", "death", INFERRED, "aliases 341.c3",
       loop="cyclic"),
    _A("344", "die variant", "death", VERIFIED, "ActionSound 344 = blk_monster*",
       chain="345", loop="oneshot"),
    _A("345", "dead (hold of 344)", "death", INFERRED, "aliases 341.c3",
       loop="cyclic"),
    _A("346", "die variant", "death", VERIFIED, "ActionSound 346 = blk_monster*",
       chain="347", loop="oneshot"),
    _A("347", "dead (hold of 346)", "death", INFERRED, "aliases 341.c3",
       loop="cyclic"),
    _A("370", "die variant", "death", VERIFIED,
       "ActionSound 370 = blk_monster*; 174 shapes map it to their own 340.c3",
       chain="371", loop="oneshot"),
    _A("371", "dead (hold of 370)", "death", VERIFIED, "aliases 341.c3",
       loop="cyclic"),

    # -- attacks -----------------------------------------------------------
    _A("401", "attack swing 1", "attack", VERIFIED,
       "ActionSound.ini 401 = sound/t<weapon>_m01.wav; Action3DEffect "
       "999.401.<type>.<sub> is the swing trail", loop="oneshot"),
    _A("402", "attack swing 2", "attack", VERIFIED,
       "ActionSound.ini 402 = sound/t<weapon>_m02.wav", loop="oneshot"),
    _A("403", "attack swing 3", "attack", VERIFIED,
       "ActionSound.ini 403 = sound/t<weapon>_m03.wav", loop="oneshot"),
    _A("404", "attack swing 1", "attack", INFERRED, "aliases 401.c3",
       loop="oneshot"),
    _A("405", "attack swing 2", "attack", INFERRED, "aliases 402.c3",
       loop="oneshot"),
    _A("406", "attack swing 3", "attack", INFERRED, "aliases 403.c3",
       loop="oneshot"),
    _A("407", "attack swing 1", "attack", INFERRED, "aliases 401.c3",
       loop="oneshot"),
    _A("408", "attack swing 2", "attack", INFERRED, "aliases 402.c3",
       loop="oneshot"),
    _A("451", "special attack", "attack", UNKNOWN,
       "own 31-frame clip, same length as 401", loop="oneshot"),
    _A("452", "special attack", "attack", UNKNOWN, "own 31-frame clip",
       loop="oneshot"),
    _A("453", "special attack", "attack", UNKNOWN, "own 31-frame clip",
       loop="oneshot"),

    # -- ranged / cast -----------------------------------------------------
    _A("501", "archer pose", "attack", INFERRED,
       "Action3DEffect 999.501.138.999 = archer-p; falls back to 100.c3 on "
       "player bodies", loop="cyclic"),
    _A("510", "archer pose", "attack", INFERRED,
       "Action3DEffect 999.510.138.999 = archer-p", loop="cyclic"),
    _A("900", "cast 1", "cast", VERIFIED,
       "Action3DEffect group-1 value 900 with a Flash* effect; own 10-frame "
       "clip that does not return to the start", loop="oneshot"),
    _A("901", "cast 2", "cast", VERIFIED,
       "Action3DEffect group-1 value 901; 16-frame cyclic clip -- the "
       "intoning loop", loop="cyclic"),
    _A("903", "cast 3", "cast", VERIFIED,
       "magictype.json SenderAction 903 on Thunder; Action3DEffect group-1 "
       "value 903", loop="oneshot"),
    _A("919", "cast / special", "cast", UNKNOWN, "own 41-frame clip",
       loop="oneshot"),
]}

#: convenience aliases the CLI and the viewer can use
ACTION_ALIASES = {
    "idle": "100", "stand": "100", "stance": "105",
    "walk": "110", "run": "120", "jump": "130", "leap": "131",
    "swing": "401", "swing1": "401", "swing2": "402", "swing3": "403",
    "attack": "401", "cast": "903", "die": "330", "hurt": "320",
    "sit": "250", "kneel": "210", "dig": "290",
}

#: The three attack swings, in the order the client cycles them.
ATTACK_CYCLE = ("401", "402", "403")


def action_of(name: str) -> str:
    """Accept either a 3-digit code or a friendly alias."""
    n = (name or "").strip().lower()
    return ACTION_ALIASES.get(n, n)


# ---------------------------------------------------------------------------
# ini/3dmotion.ini -- the key space
# ---------------------------------------------------------------------------

#: Key layouts observed in `ini/3dmotion.ini` (229,481 rows in this build).
#: Every key is `<shape><weaponset:3><action:3>` with the shape written at its
#: natural width, plus one extra leading field on the jump rows.
#: Counts are of DISTINCT keys: the file has 229,481 rows but 228,985 keys,
#: 496 of them repeated and 62 of those repeats disagreeing with the first
#: value.  This index keeps the last row, as a naive ini reader would.
KEY_FORMS = [
    (7,  "shape1 + weaponset3 + action3",  "player bodies 1-4",            89705),
    (8,  "shape2 + weaponset3 + action3",  "ghost shapes 98/99",             416),
    (9,  "shape3 + weaponset3 + action3",  "monster / npc shapes",         36298),
    (10, "shape4 + weaponset3 + action3",  "0001-0004 and 1001-1004, "
                                           "plus 4-digit npc/monster ids", 96662),
    (12, "dist2 + shape4 + weaponset3 + action3", "jump distance 10..90",   4428),
    (13, "dist3 + shape4 + weaponset3 + action3", "jump distance 100..120", 1476),
]

#: The 12 jump-distance tiers and the clip each selects for shape 1001.
#: VERIFIED from the data: the leading field steps 10,20,...,120 and selects
#: `130.c3`, `130-1.c3`, `130-2.c3`, `130-3.c3` in four bands.
JUMP_DISTANCES = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120)

#: `<weaponset>` values that are not a literal `weapon.ini` type prefix.
#: `7XY` is arithmetically "7" followed by the first two digits of a weapon
#: type -- all 17 shipped codes fit exactly (700<-000, 735<-350, 736<-360,
#: 741<-410 ... 758<-580).  `6XY` is a two-weapon combination code whose two
#: family digits are not recoverable from the shipped data.
WEAPONSET_UNARMED = "000"


def weaponset_from_type(weapon_type: str) -> str:
    """`7XY` form of a single weapon type, per the arithmetic in §4.3."""
    t = (weapon_type or "").zfill(3)
    return "7" + t[:2]


@dataclass
class MotionKey:
    raw: str
    shape: str
    weaponset: str
    action: str
    distance: Optional[int] = None
    path: str = ""

    @property
    def form(self) -> int:
        return len(self.raw)


class MotionIndex:
    """`ini/3dmotion.ini`, decomposed."""

    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)
        self.raw: dict[str, str] = {}
        self.row_count = 0
        self.duplicate_rows = 0
        self.conflicting_rows = 0
        p = self.root / "ini" / "3dmotion.ini"
        if p.is_file():
            for line in p.read_text("latin-1", errors="replace").splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if not k:
                    continue
                v = v.strip().replace("\\", "/")
                self.row_count += 1
                if k in self.raw:
                    self.duplicate_rows += 1
                    self.conflicting_rows += (self.raw[k] != v)
                self.raw[k] = v
        self.keys: list[MotionKey] = [self._split(k, v) for k, v in self.raw.items()]
        self.by_shape: dict[str, set[str]] = {}
        self.by_shape_ws: dict[tuple[str, str], set[str]] = {}
        for mk in self.keys:
            self.by_shape.setdefault(mk.shape, set()).add(mk.weaponset)
            self.by_shape_ws.setdefault((mk.shape, mk.weaponset), set()).add(mk.action)

    @staticmethod
    def _split(k: str, v: str) -> MotionKey:
        n = len(k)
        if n in (12, 13):
            dist = int(k[:n - 10])
            return MotionKey(k, k[n - 10:n - 6], k[n - 6:n - 3], k[-3:], dist, v)
        return MotionKey(k, k[:n - 6], k[n - 6:n - 3], k[-3:], None, v)

    # -- lookup ------------------------------------------------------------
    def key(self, shape: str, weaponset: str, action: str,
            distance: Optional[int] = None) -> str:
        d = "" if distance is None else str(distance)
        return f"{d}{shape}{weaponset}{action}"

    def lookup(self, shape: str, weaponset: str, action: str,
               distance: Optional[int] = None) -> tuple[Optional[str], str]:
        """Resolve to a motion path, returning `(path, how)`.

        Fallback chain, **INFERRED** (the matcher is in the packed exe) but it
        is the only chain that leaves no hole in the shipped grid:

            1. <dist><shape><weaponset><action>      (jump rows only)
            2. <shape><weaponset><action>
            3. <shape>000<action>                    unarmed motion set
            4. <shape><weaponset>100                 that set's idle
            5. <shape>000100                         the universal idle
        """
        cands: list[tuple[str, str]] = []
        if distance is not None:
            cands.append((self.key(shape, weaponset, action, distance),
                          "exact (jump distance)"))
        cands += [
            (self.key(shape, weaponset, action), "exact"),
            (self.key(shape, WEAPONSET_UNARMED, action), "weaponset -> 000"),
            (self.key(shape, weaponset, "100"), "action -> 100 (idle)"),
            (self.key(shape, WEAPONSET_UNARMED, "100"), "-> unarmed idle"),
        ]
        for k, how in cands:
            v = self.raw.get(k)
            if v:
                return v, how
        return None, "unresolved"

    def actions_for(self, shape: str, weaponset: str) -> list[str]:
        return sorted(self.by_shape_ws.get((shape, weaponset), ()))

    def weaponsets_for(self, shape: str) -> list[str]:
        return sorted(self.by_shape.get(shape, ()))


# ---------------------------------------------------------------------------
# ini/ActionCtrl.ini
# ---------------------------------------------------------------------------

@dataclass
class ActionCtrl:
    """One `ini/ActionCtrl.ini` section: a 5-point piecewise-linear curve.

    Key is `<shape4><weaponset3><action3>`, `0999` being the wildcard shape.
    `Section` is the number of `TimePercent<i>` / `MovePercent<i>` pairs.

    **What it is not:** the brief expected the run/jump root motion here.  It
    is not that.  Every shipped section is on a *death* action
    (340/342/344/346/370) for the one monster family 148/188/348/548/748 plus
    a `0999` wildcard, and walk / run / jump are all animated in place with no
    translation to supply (`--validate` section 5).
    """
    shape: str
    weaponset: str
    action: str
    section: int
    points: list[tuple[float, float]]      # (time %, move %)
    visible: bool = True

    def move_at(self, t: float) -> float:
        """Piecewise-linear `time% -> move%`, clamped at both ends."""
        pts = self.points
        if not pts:
            return t
        if t <= pts[0][0]:
            return pts[0][1] * (t / pts[0][0]) if pts[0][0] else pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            if t < pts[i][0]:
                (t0, m0), (t1, m1) = pts[i - 1], pts[i]
                s = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return m0 + (m1 - m0) * s
        return pts[-1][1]


def load_action_ctrl(root: Path | str = DEFAULT_ROOT) -> list[ActionCtrl]:
    out: list[ActionCtrl] = []
    for sec, d in parse_ini(Path(root) / "ini" / "ActionCtrl.ini").items():
        if len(sec) != 10 or not sec.isdigit():
            continue
        n = int(d.get("Section", "0") or 0)
        pts = []
        for i in range(n):
            t = d.get(f"TimePercent{i}")
            m = d.get(f"MovePercent{i}")
            if t is None or m is None:
                continue
            pts.append((float(t), float(m)))
        out.append(ActionCtrl(sec[:4], sec[4:7], sec[7:], n, pts,
                              bool(int(d.get("Visible", "1") or 0))))
    return out


def action_ctrl_for(rules: Iterable[ActionCtrl], shape: str, weaponset: str,
                    action: str) -> Optional[ActionCtrl]:
    """Most-specific match, `0999` being the shape wildcard."""
    best = None
    for r in rules:
        if r.action != action:
            continue
        if r.weaponset not in (weaponset, "999"):
            continue
        if r.shape not in (shape.zfill(4), "0999"):
            continue
        spec = (r.shape != "0999") + (r.weaponset != "999")
        if best is None or spec > best[0]:
            best = (spec, r)
    return None if best is None else best[1]


# ---------------------------------------------------------------------------
# the playable clip
# ---------------------------------------------------------------------------

LOOP_CYCLIC = "cyclic"      # frame N-1 flows back into frame 0
LOOP_CLOSED = "closed"      # frame N-1 duplicates frame 0 -- play [0, N-2]
LOOP_ONESHOT = "oneshot"    # does not return; play once, then chain or hold


@dataclass
class Clip:
    """A resolved, playable action motion."""
    shape: str
    weaponset: str
    action: str
    path: str
    how: str
    motion: attach.PartMesh                 # the motion-only `.c3`
    body: Optional[attach.PartMesh] = None  # the mesh it is bound over
    interval_ms: int = DEFAULT_FRAME_INTERVAL_MS
    distance: Optional[int] = None
    ctrl: Optional[ActionCtrl] = None

    # -- shape -------------------------------------------------------------
    @property
    def info(self) -> Optional[Action]:
        return ACTIONS.get(self.action)

    @property
    def frame_count(self) -> int:
        for c in self.motion.chunks:
            if c.motion is not None:
                return c.motion.frame_count
        return 0

    @property
    def chunk_count(self) -> int:
        return len(self.motion.chunks)

    @property
    def duration_ms(self) -> float:
        return self.play_length * self.interval_ms

    @property
    def aligned(self) -> bool:
        """Does the motion set line up with the body mesh, ordinal by ordinal?

        `C3Mesh::SetMotion` (graphic.dll `0x277C0`) rejects a motion set with
        fewer entries than the mesh has phys and otherwise assigns
        `phy[i]->motion = motionSet.Get(i)` -- there is no name matching.
        """
        if self.body is None:
            return True
        return len(self.motion.chunks) >= len(self.body.chunks)

    # -- loop model --------------------------------------------------------
    def classify(self) -> tuple[str, float, float]:
        """Measured loop kind, plus `(wrap, max_step)` in matrix-element units.

        `wrap` is the largest element difference between the last and the
        first key over a sample of bones; `max_step` the largest between
        consecutive keys.  A clip whose wrap is a normal step is a seamless
        cycle; one whose wrap is zero has a duplicated end frame; anything
        else does not return to its start.
        """
        mo = self._main()
        if mo is None or mo.frame_count < 3:
            return LOOP_CYCLIC, 0.0, 0.0
        n = mo.frame_count
        bones = range(0, mo.bone_count, max(1, mo.bone_count // 12))

        def d(f1: int, f2: int) -> float:
            return max(max(abs(x - y) for x, y in
                           zip(mo.matrix(b, f1), mo.matrix(b, f2)))
                       for b in bones)
        steps = [d(i, i + 1) for i in range(n - 1)]
        mx = max(steps) or 1e-9
        wrap = d(n - 1, 0)
        if wrap < 0.05 * mx:
            return LOOP_CLOSED, wrap, mx
        if wrap <= 1.25 * mx:
            return LOOP_CYCLIC, wrap, mx
        return LOOP_ONESHOT, wrap, mx

    @property
    def measured_loop(self) -> str:
        if not hasattr(self, "_mloop"):
            self._mloop = self.classify()[0]
        return self._mloop

    @property
    def loop(self) -> str:
        """The loop mode a player should use.

        The table wins where it has an entry: an attack swing is a one-shot
        even though its end pose happens to be within a step of its start, and
        a run half-cycle is a one-shot that chains rather than a loop.  The
        measurement (`measured_loop`) is the fallback for unlabelled codes and
        the cross-check in `--validate` section 4.
        """
        a = self.info
        if a and a.loop_hint:
            return LOOP_ONESHOT if a.loop_hint == "chain" else a.loop_hint
        return self.measured_loop

    @property
    def play_length(self) -> int:
        """Number of frames to advance through before wrapping or stopping."""
        n = self.frame_count
        return max(1, n - 1) if self.loop == LOOP_CLOSED else max(1, n)

    def frames(self) -> range:
        return range(self.play_length)

    @property
    def chain_next(self) -> Optional[str]:
        a = self.info
        return a.chain if a else None

    # -- sampling ----------------------------------------------------------
    def _main(self):
        """The motion track of the skinned body chunk: the one with the most
        bones (a socket track has exactly 1)."""
        best = None
        for c in self.motion.chunks:
            if c.motion is None:
                continue
            if best is None or c.motion.bone_count > best.bone_count:
                best = c.motion
        return best

    def matrix(self, chunk: int, bone: int = 0, frame: int = 0) -> Mat4:
        """`Motion_GetMatrix(motionSet[chunk], bone, frame)` -- graphic.dll
        `0x551A0`, argument order proven by the thunk at `0x26DE0`."""
        mo = self.motion.motion_for(chunk)
        return attach.IDENTITY if mo is None else mo.matrix(bone, frame % max(1, self.frame_count))

    def socket(self, dumy: str, frame: int = 0) -> Optional[Mat4]:
        """The world matrix of a `[Dumy]` socket at this frame of this clip.

        Exactly `docs/attachment.md` §2, with this clip's motion set bound over
        the body mesh instead of the mesh's own embedded `MOTI`.
        """
        if self.body is None:
            return None
        return attach.socket_matrix(self.body, dumy, frame, self.motion)

    # -- root motion -------------------------------------------------------
    def root_track(self) -> list[tuple[float, float, float]]:
        """Per-frame translation of bone 0 of the skinned track, in render
        space `(x, y, up)`.

        This is what the client would have to reproduce if a clip carried root
        motion.  On every player locomotion clip it is flat in x/y and only
        the jump moves in `up` -- see `--validate` section 5.
        """
        mo = self._main()
        if mo is None:
            return []
        out = []
        for f in range(mo.frame_count):
            m = mo.matrix(0, f)
            out.append((m[12], m[13], -m[14]))
        return out

    def silhouette_track(self, sample: int = 9) -> list[tuple[float, float, float]]:
        """Per-frame `(lowest, highest, centroid)` height of the posed body.

        The honest root-motion measurement: bone 0 is the pelvis, not a root
        locator, so a jump's rise shows up in the skin rather than in bone 0.
        """
        if self.body is None:
            return []
        chunk = None
        for c in self.body.bind_chunks():
            if c.name.lower() == "v_body":
                chunk = c
                break
        if chunk is None:
            chunk = next(iter(self.body.bind_chunks()), None)
        if chunk is None:
            return []
        idx = chunk.index
        mo = self.motion.motion_for(idx) or chunk.motion
        q = copy.deepcopy(chunk.phy)
        c3phy.apply_matrix_to(q)
        verts = q.vertices[::sample] or q.vertices
        out = []
        for f in range(self.frame_count):
            lo, hi, acc = 1e30, -1e30, 0.0
            for v in verts:
                z = -attach.transform_vertex(v, mo, attach.IDENTITY, f)[2]
                lo = min(lo, z)
                hi = max(hi, z)
                acc += z
            out.append((lo, hi, acc / len(verts)))
        return out


# ---------------------------------------------------------------------------
# the database
# ---------------------------------------------------------------------------

class AnimDB:
    """Resolve and load action motions for any shape in this build."""

    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 interval_ms: int = DEFAULT_FRAME_INTERVAL_MS):
        self.root = Path(root)
        self.interval_ms = interval_ms
        self.cat = attach.Catalogue(root)
        self.assets: AssetRoot = self.cat.assets
        self.index = MotionIndex(root)
        self.ctrl = load_action_ctrl(root)
        self._weapon_ini: Optional[dict] = None

    # -- identity ----------------------------------------------------------
    @staticmethod
    def shape_of(appearance: str) -> str:
        """`armor.ini` appearance -> the `3dmotion.ini` shape field.

        VERIFIED: `002135000` -> `2` -> `c3/0002/...`.  The shape is the first
        three digits with leading zeros stripped, which is how the 7-digit key
        form arises for player bodies (`docs/attachment.md` §8.1).
        """
        s = (appearance or "").strip()
        if len(s) >= 9 and s[:3].isdigit():
            return str(int(s[:3]))
        return str(int(s)) if s.isdigit() else s

    def weapon_type(self, weapon_appearance: str) -> str:
        """A `weapon.ini` section name split as `<type><sub>`, type = all but
        the last three digits (`docs/effects.md` §8 step 1)."""
        a = (weapon_appearance or "").strip()
        return a[:-3] if len(a) > 3 else ""

    def weaponset(self, right: str = "", left: str = "") -> str:
        """The `<weaponset>` field for an equipped loadout.

        * nothing equipped -> `000` (VERIFIED: it is the only set every shape
          ships for every action).
        * one weapon -> its 3-digit type (VERIFIED: the 19 single-type sets in
          `3dmotion.ini` are exactly `weapon.ini`'s one- and two-handed type
          prefixes, and they alias down to 7 motion folders).
        * two weapons -> a `6XY` combination code.  **UNKNOWN** -- the family
          digits X and Y are assigned by the packed exe and nothing in the
          shipped data spells them out.  This returns the right hand's type,
          which resolves through the normal fallback chain.
        """
        rt = self.weapon_type(right)
        lt = self.weapon_type(left)
        if not rt and not lt:
            return WEAPONSET_UNARMED
        if rt and lt:
            return rt          # 6XY unresolved -- see the docstring
        return rt or lt

    # -- resolution --------------------------------------------------------
    def resolve(self, shape: str, weaponset: str, action: str,
                distance: Optional[int] = None) -> tuple[Optional[str], str]:
        return self.index.lookup(shape, weaponset, action, distance)

    def clip(self, body_appearance: str, action: str, *, weapon: str = "",
             off_hand: str = "", weaponset: Optional[str] = None,
             distance: Optional[int] = None, shape: Optional[str] = None,
             bind_body: bool = True) -> Optional[Clip]:
        """The playable motion for one (body, loadout, action).

        `shape` overrides the shape derived from the appearance -- needed for
        the `1001`-`1004` body family and for monster / npc shapes, which no
        shipped table maps an appearance onto (docs/animation.md section 2.1).
        """
        act = action_of(action)
        shape = shape or self.shape_of(body_appearance)
        ws = weaponset or self.weaponset(weapon, off_hand)
        path, how = self.resolve(shape, ws, act, distance)
        if not path:
            return None
        mset = self.cat.load(path)
        if mset is None:
            return None
        body = None
        if bind_body:
            bpath = (self.cat.appearance_mesh("armor.ini", body_appearance)
                     or self.cat.mesh_path(body_appearance))
            if bpath:
                body = self.cat.load(bpath)
        return Clip(shape, ws, act, path, how, mset, body, self.interval_ms,
                    distance, action_ctrl_for(self.ctrl, shape, ws, act))

    def sequence(self, body_appearance: str, action: str, **kw) -> list[Clip]:
        """A clip plus whatever continues it (run alternates feet, a death
        holds its corpse pose).  Stops when the chain closes or repeats."""
        out: list[Clip] = []
        seen: set[str] = set()
        cur = action_of(action)
        while cur and cur not in seen:
            seen.add(cur)
            c = self.clip(body_appearance, cur, **kw)
            if c is None:
                break
            out.append(c)
            cur = c.chain_next
        return out


# ---------------------------------------------------------------------------
# reporting / CLI
# ---------------------------------------------------------------------------

def cmd_keys(db: AnimDB) -> None:
    counts = collections.Counter(len(k) for k in db.index.raw)
    print("ini/3dmotion.ini   %d rows, %d distinct keys "
          "(%d duplicate rows, %d of them conflicting)\n"
          % (db.index.row_count, len(db.index.raw),
             db.index.duplicate_rows, db.index.conflicting_rows))
    print("%-5s %-10s %-46s %s" % ("len", "keys", "layout", "population"))
    for n, layout, who, _ in KEY_FORMS:
        print("%-5d %-10d %-46s %s" % (n, counts.get(n, 0), layout, who))
    print()
    shapes = collections.Counter(mk.shape for mk in db.index.keys)
    print("distinct shapes: %d" % len(shapes))
    for w in (1, 2, 3, 4):
        ss = sorted(s for s in shapes if len(s) == w)
        print("  %d-digit (%3d): %s%s" % (w, len(ss), " ".join(ss[:16]),
                                          " ..." if len(ss) > 16 else ""))
    print()
    print("jump-distance tiers (12- and 13-digit keys), shape 1001 weaponset 000:")
    for d in JUMP_DISTANCES:
        p, _ = db.resolve("1001", "000", "130", d)
        print("   distance %3d -> %s" % (d, p))


def cmd_actions(db: AnimDB, shape: str = "2") -> None:
    ws = db.index.actions_for(shape, "000")
    print("action codes shipped for shape %s weaponset 000: %d" % (shape, len(ws)))
    print()
    print("%-5s %-10s %-26s %-9s %-8s %s" %
          ("code", "group", "meaning", "conf", "loop", "clip"))
    for code in sorted(ACTIONS):
        a = ACTIONS[code]
        p, how = db.resolve(shape, "000", code)
        tail = (p.rsplit("/", 1)[-1] if p else "-")
        if how != "exact":
            tail += "  (%s)" % how
        print("%-5s %-10s %-26s %-9s %-8s %s" %
              (a.code, a.group, a.name, a.confidence, a.loop_hint, tail))
    unknown = [c for c in ws if c not in ACTIONS]
    print()
    print("shipped but not identified here (%d): %s" % (len(unknown), " ".join(unknown)))


def cmd_clip(db: AnimDB, body: str, action: str, weapon: str, off: str,
             distance: Optional[int], shape: Optional[str] = None) -> None:
    clip = db.clip(body, action, weapon=weapon, off_hand=off,
                   distance=distance, shape=shape)
    if clip is None:
        sh = shape or db.shape_of(body)
        ws = db.weaponset(weapon, off)
        path, how = db.resolve(sh, ws, action_of(action), distance)
        if path:
            print("shape %s weaponset %s action %s -> %s  [%s]"
                  % (sh, ws, action_of(action), path, how))
            print("   but that file is not in this install "
                  "(1,100 of 3,260 named motions are absent -- see "
                  "docs/animation.md section 2.1)")
        else:
            print("no ini row for shape %s weaponset %s action %s"
                  % (sh, ws, action_of(action)))
        return
    a = clip.info
    kind, wrap, step = clip.classify()
    print("body %s  shape %s  weaponset %s  action %s%s" %
          (body, clip.shape, clip.weaponset, clip.action,
           "  (%s)" % a.name if a else ""))
    print("  motion    %s   [%s]" % (clip.path, clip.how))
    print("  frames    %d   chunks %d   body chunks %s   aligned %s" %
          (clip.frame_count, clip.chunk_count,
           len(clip.body.chunks) if clip.body else "-", clip.aligned))
    print("  timing    %d ms/frame  = %.1f fps   play %d frames = %.0f ms" %
          (clip.interval_ms, 1000.0 / clip.interval_ms,
           clip.play_length, clip.duration_ms))
    print("  loop      %s   (measured %s: wrap %.2f vs max step %.2f)"
          % (clip.loop, kind, wrap, step))
    if clip.chain_next:
        print("  chains to action %s" % clip.chain_next)
    if clip.ctrl:
        print("  ActionCtrl %s  points %s  visible=%s" %
              (clip.ctrl.shape + clip.ctrl.weaponset + clip.ctrl.action,
               clip.ctrl.points, clip.ctrl.visible))
    if clip.body:
        names = [c.name for c in clip.body.chunks]
        print("  chunk order  " + ", ".join(names))
        for dumy in ("v_armet", "v_l_weapon", "v_r_weapon"):
            S = clip.socket(dumy, 0)
            if S:
                print("    %-12s frame 0  (x %7.2f, y %7.2f, up %7.2f)"
                      % (dumy, S[12], S[13], -S[14]))


def cmd_sequence(db: AnimDB, body: str, action: str, weapon: str) -> None:
    clips = db.sequence(body, action, weapon=weapon)
    if not clips:
        print("unresolved")
        return
    total = 0.0
    for c in clips:
        total += c.duration_ms
        print("  %-4s %-28s %3d frames  %6.0f ms  %-8s -> %s" %
              (c.action, c.path, c.frame_count, c.duration_ms, c.loop,
               c.chain_next or "(loop)"))
    print("  total %.0f ms" % total)
    if len(clips) > 1:
        print()
        print("  cross-clip continuity (max matrix-element step):")
        for i, c in enumerate(clips):
            nxt = clips[(i + 1) % len(clips)]
            print("    %s.last -> %s.first : %s" %
                  (c.action, nxt.action, _cross(c, nxt)))


def _cross_pair(a: Clip, b: Clip) -> tuple[float, float]:
    """`(cross-clip step, largest step inside a)` in matrix-element units."""
    ma, mb = a._main(), b._main()
    if ma is None or mb is None:
        return 0.0, 1e-9
    bc = min(ma.bone_count, mb.bone_count)
    bones = range(0, bc, max(1, bc // 12))
    d = max(max(abs(x - y) for x, y in
                zip(ma.matrix(bo, ma.frame_count - 1), mb.matrix(bo, 0)))
            for bo in bones)
    steps = [max(max(abs(x - y) for x, y in zip(ma.matrix(bo, i), ma.matrix(bo, i + 1)))
                 for bo in bones) for i in range(ma.frame_count - 1)]
    mx = max(steps) if steps else 0.0
    if mx <= 1e-9:          # a 2-frame hold has no internal motion to compare
        return d, float("nan")
    return d, mx


def _cross(a: Clip, b: Clip) -> str:
    d, mx = _cross_pair(a, b)
    if mx != mx:                                          # NaN: static clip
        return "%7.2f  (the source clip is a static hold)" % d
    return "%7.2f  (vs max own step %7.2f -> ratio %.2f)" % (d, mx, d / mx)


def _cross_ratio(a: Clip, b: Clip) -> float:
    d, mx = _cross_pair(a, b)
    return float("inf") if mx != mx else d / mx


def _pose_distance(a: Clip, fa: int, b: Clip, fb: int,
                   sample: int = 5) -> Optional[float]:
    """Largest per-vertex distance between two posed frames of one body."""
    if a.body is None:
        return None
    chunk = next((c for c in a.body.bind_chunks() if c.name.lower() == "v_body"),
                 None) or next(iter(a.body.bind_chunks()), None)
    if chunk is None:
        return None
    ma = a.motion.motion_for(chunk.index) or chunk.motion
    mb = b.motion.motion_for(chunk.index) or chunk.motion
    q = copy.deepcopy(chunk.phy)
    c3phy.apply_matrix_to(q)
    worst = 0.0
    for v in q.vertices[::sample]:
        pa = attach.transform_vertex(v, ma, attach.IDENTITY, fa)
        pb = attach.transform_vertex(v, mb, attach.IDENTITY, fb)
        worst = max(worst, math.dist(pa, pb))
    return worst


def cmd_root_motion(db: AnimDB, body: str, action: str, weapon: str) -> None:
    clip = db.clip(body, action, weapon=weapon)
    if clip is None:
        print("unresolved")
        return
    print("%s  action %s  %s" % (body, clip.action, clip.path))
    print("  bone-0 translation and posed silhouette, per frame")
    print("  %-5s %9s %9s %9s | %9s %9s %9s" %
          ("frame", "b0.x", "b0.y", "b0.up", "feet", "head", "centroid"))
    root = clip.root_track()
    sil = clip.silhouette_track()
    for f in range(clip.frame_count):
        r = root[f] if f < len(root) else (0, 0, 0)
        s = sil[f] if f < len(sil) else (0, 0, 0)
        print("  %-5d %9.2f %9.2f %9.2f | %9.2f %9.2f %9.2f"
              % (f, r[0], r[1], r[2], s[0], s[1], s[2]))
    if sil:
        c = [x[2] for x in sil]
        print("  centroid height: min %.1f  max %.1f  rise %.1f" %
              (min(c), max(c), max(c) - min(c)))


def cmd_actionctrl(db: AnimDB) -> None:
    print("ini/ActionCtrl.ini -- %d sections, key <shape4><weaponset3><action3>"
          % len(db.ctrl))
    groups: dict[tuple, list[ActionCtrl]] = {}
    for r in db.ctrl:
        groups.setdefault(tuple(r.points), []).append(r)
    for pts, rows in groups.items():
        print()
        print("  curve %s" % (list(pts),))
        print("  used by %d sections: %s" %
              (len(rows), " ".join(sorted(r.shape + r.weaponset + r.action
                                          for r in rows))))
        curve = ActionCtrl("", "", "", 0, list(pts))
        print("  time -> move: " + "  ".join(
            "%d%%->%.0f%%" % (t, curve.move_at(t)) for t in (0, 25, 50, 75, 100)))
    print()
    print("  shapes covered:", sorted({r.shape for r in db.ctrl}))
    print("  actions covered:", sorted({r.action for r in db.ctrl}))
    print("  all of them are death actions; see docs/animation.md section 6.")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

PLAYER_BODIES = {"001": "001131000", "002": "002135000",
                 "003": "003133000", "004": "004134000"}


def validate(db: AnimDB, limit: Optional[int] = None) -> int:
    bad = 0
    print("=" * 78)
    print("1. ini/3dmotion.ini key space")
    print("=" * 78)
    counts = collections.Counter(len(k) for k in db.index.raw)
    tot = 0
    for n, layout, who, expect in KEY_FORMS:
        got = counts.get(n, 0)
        tot += got
        flag = "" if got == expect else "   != documented %d" % expect
        print("  len %-3d %-8d %-44s %s%s" % (n, got, layout, who, flag))
        if got != expect:
            bad += 1
    print("  total %d of %d rows accounted for" % (tot, len(db.index.raw)))
    if tot != len(db.index.raw):
        bad += 1

    print()
    print("=" * 78)
    print("2. resolution grid: bodies x weaponsets x actions")
    print("=" * 78)
    grand_ok = grand_miss = 0
    for bt, app in sorted(PLAYER_BODIES.items()):
        shape = db.shape_of(app)
        wss = db.index.weaponsets_for(shape)
        acts = sorted(ACTIONS)
        ok = exact = miss = 0
        for ws in wss:
            for a in acts:
                p, how = db.resolve(shape, ws, a)
                if not p:
                    miss += 1
                else:
                    ok += 1
                    exact += (how == "exact")
        grand_ok += ok
        grand_miss += miss
        print("  body %-3s shape %-2s  weaponsets %-4d x actions %-3d = %-6d "
              "resolved %-6d (exact %-6d)  unresolved %d"
              % (bt, shape, len(wss), len(acts), len(wss) * len(acts),
                 ok, exact, miss))
        if miss:
            bad += 1
    print("  ALL PLAYER BODIES: %d resolved, %d unresolved" % (grand_ok, grand_miss))

    shapes = sorted(db.index.by_shape)
    sok = smiss = 0
    gaps: list[str] = []
    for s in shapes:
        ws = db.index.weaponsets_for(s)
        ws0 = "000" if "000" in ws else (ws[0] if ws else "000")
        missing = []
        for a in ("100", "110", "120", "130", "401"):
            p, _ = db.resolve(s, ws0, a)
            if p:
                sok += 1
            else:
                smiss += 1
                missing.append(a)
        if missing:
            gaps.append("%s(%s)" % (s, ",".join(missing)))
    print("  every shape (%d) x {idle, walk, run, jump, attack}, using each "
          "shape's own\n  default weaponset: %d resolved, %d unresolved%s"
          % (len(shapes), sok, smiss,
             ("   gaps: " + " ".join(gaps)) if gaps else ""))

    print()
    print("  which of the named motion files actually ship in this install:")
    paths = sorted({v for v in db.index.raw.values()})
    present: set[str] = set()
    for p in paths:
        try:
            db.assets.read(p)
            present.add(p)
        except Exception:
            pass
    have = collections.Counter()
    want = collections.Counter()
    for p in paths:
        k = "/".join(p.split("/")[:2]).lower()
        want[k] += 1
        if p in present:
            have[k] += 1
    print("    distinct motion files named : %d" % len(paths))
    print("    ... present                 : %d" % len(present))
    print("    ... absent                  : %d" % (len(paths) - len(present)))
    dead = sum(1 for v in db.index.raw.values() if v not in present)
    print("    keys pointing at an absent file: %d of %d (%.0f %%)"
          % (dead, len(db.index.raw), 100.0 * dead / max(1, len(db.index.raw))))
    for k in sorted(want, key=lambda k: -want[k])[:10]:
        print("      %-14s %5d named, %5d present" % (k, want[k], have[k]))
    print("    NOTE: the whole c3/1001..c3/1004 tree is absent, so shapes")
    print("    1001-1004 and every jump-distance-tiered key are unplayable")
    print("    in this install.  The 1-4 player bodies are unaffected.")

    print()
    print("=" * 78)
    print("3. loading and ordinal binding")
    print("=" * 78)
    loaded = failed = aligned = misaligned = 0
    gone: list[str] = []
    for bt, app in sorted(PLAYER_BODIES.items()):
        n_ok = n_bad = 0
        for a in sorted(ACTIONS):
            c = db.clip(app, a)
            if c is None:
                failed += 1
                p, _ = db.resolve(db.shape_of(app), "000", a)
                gone.append("%s/%s -> %s" % (bt, a, p))
                continue
            loaded += 1
            if c.aligned:
                aligned += 1
                n_ok += 1
            else:
                misaligned += 1
                n_bad += 1
        print("  body %-3s  %d clips loaded, motion set covers the mesh's "
              "chunks on %d, short on %d" % (bt, n_ok + n_bad, n_ok, n_bad))
    print("  TOTAL loaded %d, aligned %d, misaligned %d" %
          (loaded, aligned, misaligned))
    print("  motion files named by the ini but absent from this install: %d%s"
          % (failed, ("   " + "; ".join(gone)) if gone else ""))
    if misaligned:
        bad += 1

    print()
    print("=" * 78)
    print("4. loop classification (measured, body 002135000, weaponset 000)")
    print("=" * 78)
    print("  %-5s %-24s %5s %8s %8s %-9s %s" %
          ("act", "clip", "n", "wrap", "maxstep", "measured", "table"))
    disagree = 0
    for a in sorted(ACTIONS):
        c = db.clip(PLAYER_BODIES["002"], a)
        if c is None:
            continue
        kind, wrap, step = c.classify()
        hint = ACTIONS[a].loop_hint
        mark = ""
        if hint and hint not in (kind, "chain"):
            mark = "  <-- differs"
            disagree += 1
        print("  %-5s %-24s %5d %8.2f %8.2f %-9s %s%s" %
              (a, c.path.rsplit("/", 1)[-1], c.frame_count, wrap, step,
               kind, hint or "-", mark))
    print("  table/measurement disagreements: %d" % disagree)

    print()
    print("=" * 78)
    print("5. root motion: does a locomotion clip translate the character?")
    print("   (the ActionCtrl.ini hypothesis, tested)")
    print("=" * 78)
    print("  %-5s %-8s %8s %8s %8s %8s %8s" %
          ("act", "meaning", "dx", "dy", "rise", "feet.min", "feet.max"))
    for a in ("100", "110", "115", "120", "121", "125", "126", "130", "131",
              "401", "330"):
        c = db.clip(PLAYER_BODIES["002"], a)
        if c is None:
            continue
        root = c.root_track()
        sil = c.silhouette_track()
        dx = max(r[0] for r in root) - min(r[0] for r in root)
        dy = max(r[1] for r in root) - min(r[1] for r in root)
        cz = [s[2] for s in sil]
        feet = [s[0] for s in sil]
        print("  %-5s %-8s %8.2f %8.2f %8.2f %8.2f %8.2f" %
              (a, (ACTIONS[a].name.split()[0] if a in ACTIONS else "?"),
               dx, dy, max(cz) - min(cz), min(feet), max(feet)))
    print()
    print("  Walk and run translate the root by less than a unit: they are")
    print("  in-place cycles, and the client moves the character externally.")
    print("  The jumps DO carry their vertical arc in the clip.")

    print()
    print("=" * 78)
    print("6. jump sanity: the arc must rise and come back down")
    print("=" * 78)
    for bt, app in sorted(PLAYER_BODIES.items()):
        for a in ("130", "131"):
            c = db.clip(app, a)
            if c is None:
                continue
            sil = c.silhouette_track()
            if not sil:
                continue
            cz = [s[2] for s in sil]
            apex = cz.index(max(cz))
            rise = max(cz) - cz[0]
            back = abs(cz[-1] - cz[0])
            ok = rise > 5 and apex not in (0, len(cz) - 1) and back < rise * 0.6
            print("  body %-3s action %-4s  start %6.1f  apex %6.1f @frame %-3d"
                  "  end %6.1f   rise %5.1f  return %5.1f   %s"
                  % (bt, a, cz[0], max(cz), apex, cz[-1], rise, back,
                     "OK" if ok else "!! not an arc"))
            if not ok:
                bad += 1

    print()
    print("=" * 78)
    print("7. run cycle: the 120 -> 121 -> 120 chain must be seamless")
    print("   (a seam is seamless when the cross-clip step is no larger than")
    print("    the largest step inside the clip, i.e. ratio <= 1.25)")
    print("=" * 78)
    for bt, app in sorted(PLAYER_BODIES.items()):
        for pair in (("120", "121"), ("125", "126"), ("110", "110")):
            a = db.clip(app, pair[0])
            b = db.clip(app, pair[1])
            if not a or not b:
                continue
            chain_r = _cross_ratio(a, b)
            self_r = _cross_ratio(a, a)
            ok = chain_r <= 1.25
            print("  body %-3s %s->%s ratio %.2f   %s->%s (self) ratio %.2f   %s"
                  % (bt, pair[0], pair[1], chain_r, pair[0], pair[0], self_r,
                     "OK seamless" if ok else "!! seam"))
            if not ok:
                bad += 1

    print()
    print("=" * 78)
    print("8. terminal-hold pairs: action N+1 holds the last pose of action N")
    print("   measured in POSED space -- max per-vertex distance between the")
    print("   last frame of N and frame 0 of N+1 on a 170-unit body")
    print("=" * 78)
    worst = 0.0
    for bt, app in sorted(PLAYER_BODIES.items()):
        for a, h in (("230", "231"), ("270", "271"), ("330", "331"),
                     ("340", "341")):
            ca, cb = db.clip(app, a), db.clip(app, h)
            if not ca or not cb:
                continue
            d = _pose_distance(ca, ca.frame_count - 1, cb, 0)
            if d is None:
                continue
            worst = max(worst, d)
            print("  body %-3s %s(last) vs %s(0)   max vertex delta %7.3f %s"
                  % (bt, a, h, d,
                     "exact" if d < 1e-6 else ("  <-- NOT a hold" if d > 25 else "")))
            if d > 25:
                bad += 1
    print("  worst delta across all pairs: %.3f units on a ~170-unit body."
          % worst)
    print("  Exact on 003/004; 001 and 002 settle by up to 17 units, so the")
    print("  hold is the same pose, not always the same matrices.")

    print()
    print("=" * 78)
    print("9. frame-rate evidence")
    print("=" * 78)
    try:
        db_fx = fx.EffectDB(db.root)
        by = collections.defaultdict(collections.Counter)
        for r in db_fx.action_rules:
            if r.effect and r.effect != "none":
                e = db_fx.resolve(r.effect)
                if e:
                    by[r.action][e.frame_interval] += 1
        for a in sorted(by):
            tot = sum(by[a].values())
            print("  Action3DEffect action %-4s -> %-5d effects, "
                  "3DEffect FrameInterval %s" % (a, tot, dict(by[a])))
        print()
        print("  Every effect bound to a body action (401/402/403 swing,")
        print("  900/901/903 cast) is 41 ms.  The free-running loops (999")
        print("  aura, 100 idle) are not, which is why 41 is the candidate.")
    except Exception as e:                                # pragma: no cover
        print("  (effects.py unavailable: %s)" % e)

    print()
    print("failed checks:", bad)
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--interval", type=int, default=DEFAULT_FRAME_INTERVAL_MS,
                    help="ms per frame (default %d)" % DEFAULT_FRAME_INTERVAL_MS)
    ap.add_argument("--weapon", default="", help="right-hand weapon appearance")
    ap.add_argument("--off-hand", dest="off_hand", default="")
    ap.add_argument("--distance", type=int, help="jump distance tier (10..120)")
    ap.add_argument("--actions", action="store_true")
    ap.add_argument("--keys", action="store_true")
    ap.add_argument("--actionctrl", action="store_true")
    ap.add_argument("--clip", nargs=2, metavar=("BODY", "ACTION"))
    ap.add_argument("--sequence", nargs=2, metavar=("BODY", "ACTION"))
    ap.add_argument("--root-motion", dest="rootmotion", nargs=2,
                    metavar=("BODY", "ACTION"))
    ap.add_argument("--shape", default="2",
                    help="shape for --actions (default 2)")
    ap.add_argument("--as-shape", dest="shape_override",
                    help="override the shape derived from the body appearance")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    db = AnimDB(a.root, a.interval)
    if a.keys:
        cmd_keys(db)
        return 0
    if a.actions:
        cmd_actions(db, a.shape)
        return 0
    if a.actionctrl:
        cmd_actionctrl(db)
        return 0
    if a.clip:
        cmd_clip(db, a.clip[0], a.clip[1], a.weapon, a.off_hand, a.distance,
                 a.shape_override)
        return 0
    if a.sequence:
        cmd_sequence(db, a.sequence[0], a.sequence[1], a.weapon)
        return 0
    if a.rootmotion:
        cmd_root_motion(db, a.rootmotion[0], a.rootmotion[1], a.weapon)
        return 0
    if a.validate:
        return validate(db, a.limit)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
