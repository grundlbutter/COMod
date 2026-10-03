#!/usr/bin/env python3
r"""effect2d.py -- the client's **2D** effect table, resolved to playable frames.

WHAT THIS IS, AND WHY IT IS NOT `tools/effects.py`
--------------------------------------------------
`tools/effects.py` + `tools/effectplay.py` own the **3D** effect system:
``ini/3DEffect.ini`` (or its ``.dbc`` twin) -> per-layer ``EffectId``/
``TextureId`` -> ``ini/3DEffectObj.ini`` -> a ``.C3`` container holding PHY
geometry, ``MOTI`` animation, ``PTCL`` particles, ``SHAP`` ribbons and a
``CAME`` authoring camera.  That is a *scene*.

There is a **second, entirely separate** effect system in the same client and
nothing in this repository read it before this module.  It is a flipbook of
whole images:

    ini/effect.ini      [Freeze]  AniTitle / FrameInterval / LoopTime /
                                  LoopInterval / Delay / OffsetX / OffsetY /
                                  ShowWay / Exigence          -- the TIMING
    ani/effect.ani      [Freeze]  FrameAmount / Frame0.. FrameN
                                  -> data/pic/...dds|png|tga|bmp -- the FRAMES

`AniTitle` is the join.  It is usually equal to the section name and
sometimes is not (``[MapItemFlash] AniTitle=MapItemFlush`` -- note the
spelling), which is exactly why the join must be read rather than assumed.

A CORRECTION THIS MODULE EXISTS TO RECORD
-----------------------------------------
The task that commissioned it stated that 2D effects are the numerically-keyed
files under ``c3/effect/<name>/`` -- ``1.C3``, ``2.C3``, ``3.C3`` beside
``1.dds`` -- and that those numbers are animation frames.  **They are not.**
MEASURED on 5517 (``scratchpad`` census, reproduced by ``coverage()`` below):
of 5,029 ``c3/effect/`` rows in ``ini/c3.wdb``, 3,608 are ``.C3`` and 1,421
are ``.dds``; the numeric leaf is the **layer index** of a 3D effect --
``3DEffectObj.ini`` maps ``41=C3/Effect/Intone/1.C3``, ``42=.../2.C3``,
``43=.../3.C3`` and ``[Intone] Amount=3`` names all three as *layers of one
effect*, drawn together, not as frames shown one after another.  ``N-M.dds``
is layer ``N``'s texture variant ``M``.

The genuine image-sequence system is the one above, and it is small and
uniform: **5,457 sections across 34 installs, every one of them carrying
exactly the same 9 keys** (see ``KEYS``).  [Read "33 installs" until
2026-09-07: the SECTION COUNT was right to the row and the DENOMINATOR was
stale.  Re-derived from `coroot.clients_dir()`, the corpus is 34 installs and
every one of them ships `ini/effect.ini`, so no install could have contributed
a zero and made 33 the honest figure -- the total reconciles exactly at 5,457
only when all 34 are counted.  Per install: 4274 5, 5017 17, 5065 17, 5165 50,
5517 76, 6090 111, 6256 126, 6271 128, 6609 142, 6609.cn 142, 6652 149,
6680 155, 6707 156, 6716 128, 6772 156, 6805 156, 6868 165, 6907 181,
6968 181, 7009 183, 7065 193, 7083 193, 7110 194, 7135 194, 7170 194,
7182 194, 7189 203, 7205 203, 7632 300, 7682 300, 7867 353, 7878 365,
CCO-snapshot-2026-08-24 5, Zephyr 142.  A count quoted against the wrong
denominator is the cheapest version of the defect in
`docs/claim_enumeration_audit_2026-09-07.md`, and the hardest to see, because
the number beside it is correct.]  Reading ``c3/effect/*/N.C3`` as
frames would have produced a player that flipped between the layers of a 3D
effect and called it an animation.

WHAT IS DECLARED AND WHAT IS NOT
--------------------------------
Declared, and therefore honoured exactly: ``FrameInterval`` (ms per frame),
``LoopTime`` (how many times the sequence repeats), ``LoopInterval`` (ms of
dead time between repeats), ``Delay`` (ms before the first frame), and
``OffsetX``/``OffsetY`` (screen pixels, the anchor offset).

**NOT declared: any blend or z-buffer field.**  This is a measured absence,
not an oversight in this reader.  ``ini/effect.ini`` carries 9 keys and only
9 -- ``AniTitle LoopTime FrameInterval ShowWay LoopInterval OffsetX OffsetY
Exigence Delay`` -- on 5,457 of 5,457 sections, and ``ani/effect.ani`` carries
one key, ``FrameAmount``, on 392 of 392 sections of 7878's copy.  The 3D
table's ``ASB``/``ADB``/``ZBuffer`` have no counterpart here.  A consumer that
wants a blend mode has to get it from the drawing code, not from the table, so
this module reports ``blend=None`` and says why rather than inventing one.
``ShowWay`` is 0 on 5,423 sections and 1 on 34 (all of them ``MapItemFlash``)
and ``Exigence`` is 1 on all 5,457 -- neither is decoded, both are carried
through raw.

Nothing here writes anything and nothing here touches the install except to
read.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                          # noqa: E402
from coassets import DEFAULT_ROOT, AssetRoot           # noqa: E402
from dmap import load_ani                              # noqa: E402

#: Every key ``ini/effect.ini`` has ever carried, MEASURED over all 34 installs
#: `coroot.clients_dir()` holds (5,457 sections; every section
#: carries every one of these and nothing else).  RE-MEASURED 2026-09-07 over
#: every install: 5,457 of 5,457 sections, **one key set, no exceptions** --
#: the substance is unchanged and stronger, and only the denominator moved,
#: from 33.  (It also stopped naming the path: the corpus is wherever
#: `coroot` says it is, and a comment that spells the box's own directory is
#: a second place for it to be wrong.)  A section that grows a tenth
#: key is a finding, and `Effect2D.unknown_keys` reports it instead of dropping
#: it on the floor.
KEYS = ("AniTitle", "LoopTime", "FrameInterval", "ShowWay", "LoopInterval",
        "OffsetX", "OffsetY", "Exigence", "Delay")

#: What the table does NOT say, stated once so every consumer prints the same
#: sentence.  The player puts this in the UI verbatim.
NO_BLEND_NOTE = (
    "ini/effect.ini declares NO blend mode and NO z-buffer field — measured, "
    "not missing from this reader: the table carries exactly 9 keys "
    "(AniTitle, LoopTime, FrameInterval, ShowWay, LoopInterval, OffsetX, "
    "OffsetY, Exigence, Delay) on 5,457 of 5,457 sections across 34 installs, "
    "and ani/effect.ani carries only FrameAmount. The preview composites these "
    "frames with straight alpha (source-over). THAT CHOICE IS OURS, NOT THE "
    "CLIENT'S — it is an APPROXIMATION and the client may draw them additively."
)

#: `ShowWay` and `Exigence` are read and passed through undecoded.
UNDECODED_NOTE = (
    "ShowWay and Exigence are carried through raw and are NOT decoded. "
    "ShowWay is 0 on 5,423 sections and 1 on 34 (every one of them named "
    "MapItemFlash, the only row that also sets LoopInterval=2000); Exigence is "
    "1 on all 5,457. Neither varies enough to fit a meaning from the data."
)

#: A loop count this large means "forever" in practice.  The shipped values are
#: 3, 99999, 999999 and 99999999 -- not a flag, just a big number -- so the
#: player needs a threshold rather than a sentinel test.
ENDLESS_LOOPS = 10000


def _int(d: dict, key: str, default: int = 0) -> int:
    try:
        return int(str(d.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def read_sections(path: Path) -> dict[str, dict[str, str]]:
    r"""``ini/effect.ini`` -> ``{section: {key: value}}``.

    Deliberately NOT `effects.read_sections`: that one is tuned for the 3D
    tables' comment styles.  This accepts ``;``, ``#`` and ``//`` leaders,
    because ``ani/``-adjacent files use ``//`` and the 3D ones do not, and it
    keeps the LAST value for a repeated key, which is what an ini reader does.
    """
    out: dict[str, dict[str, str]] = {}
    sec = ""
    try:
        text = Path(path).read_text("latin-1", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1].strip()
            out.setdefault(sec, {})
            continue
        if "=" in line and sec:
            k, v = (x.strip() for x in line.split("=", 1))
            out[sec][k] = v
    return out


@dataclass
class Frame2D:
    """One image of a 2D effect's flipbook."""
    index: int
    path: str                       #: logical, forward slashes, as the ani says
    found: bool = False             #: does this install actually hold it


@dataclass
class Effect2D:
    """One ``ini/effect.ini`` row joined to its ``ani/effect.ani`` frames."""
    name: str
    ani_title: str = ""
    frame_interval: int = 50
    loop_time: int = 1
    loop_interval: int = 0
    delay: int = 0
    show_way: int = 0
    exigence: int = 1
    offset: tuple[int, int] = (0, 0)
    frames: list[Frame2D] = field(default_factory=list)
    #: which file answered for the frames -- ``ani/effect.ani`` on every
    #: official client, ``ani/effect.json`` on the community client.
    ani_source: str = ""
    #: ``ini/effect.ini``, or ``ani/effect.ani`` for a section the timing table
    #: never names (33 such on 7878: ItemUse, FireLight, the coloured lights...)
    source: str = ""
    timed: bool = True              #: False => frames exist, no effect.ini row
    unknown_keys: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def missing_frames(self) -> int:
        return sum(1 for f in self.frames if not f.found)

    @property
    def endless(self) -> bool:
        return self.loop_time >= ENDLESS_LOOPS

    @property
    def loop_ms(self) -> int:
        """One pass through the frames, in ms."""
        return self.frame_count * max(1, self.frame_interval)

    @property
    def cycle_ms(self) -> int:
        """One pass plus the dead time before the next one."""
        return self.loop_ms + max(0, self.loop_interval)

    @property
    def duration_ms(self) -> Optional[int]:
        """Total run length, or None when it never ends.

        `delay` is counted: it is time on the clock before anything is drawn,
        so a scrubber that ignored it would show frame 0 at t=0 for an effect
        the client holds blank for 500 ms.
        """
        if self.endless or self.loop_time <= 0:
            return None
        n = self.loop_time
        return self.delay + n * self.loop_ms + max(0, n - 1) * max(0, self.loop_interval)

    def frame_at(self, elapsed_ms: float) -> Optional[int]:
        """Which frame index is on screen at `elapsed_ms`, or None for blank.

        None is a real answer, not an error: during `delay`, during a
        `LoopInterval` gap, and after the last loop of a finite effect, the
        client is drawing nothing.  A player that clamped to frame 0 instead
        would show a permanent first frame through every gap.
        """
        if not self.frames:
            return None
        t = float(elapsed_ms) - self.delay
        if t < 0:
            return None
        cyc = self.cycle_ms
        if cyc <= 0:
            return 0
        n = int(t // cyc)
        if not self.endless and self.loop_time > 0 and n >= self.loop_time:
            return None
        within = t - n * cyc
        if within >= self.loop_ms:
            return None                                 # inside LoopInterval
        idx = int(within // max(1, self.frame_interval))
        return min(idx, len(self.frames) - 1)

    def payload(self) -> dict:
        return {
            "name": self.name,
            "kind": "2d",
            "aniTitle": self.ani_title,
            "frameIntervalMs": self.frame_interval,
            "fps": round(1000.0 / self.frame_interval, 3) if self.frame_interval else 0.0,
            "loopTime": self.loop_time,
            "loopIntervalMs": self.loop_interval,
            "delayMs": self.delay,
            "showWay": self.show_way,
            "exigence": self.exigence,
            "offset": list(self.offset),
            "endless": self.endless,
            "loopMs": self.loop_ms,
            "cycleMs": self.cycle_ms,
            "durationMs": self.duration_ms,
            "frameCount": self.frame_count,
            "missingFrames": self.missing_frames,
            "timed": self.timed,
            "source": self.source,
            "aniSource": self.ani_source,
            "unknownKeys": dict(self.unknown_keys),
            "error": self.error,
            "frames": [{"index": f.index, "path": f.path, "found": f.found}
                       for f in self.frames],
            "blend": None,
            "blendNote": NO_BLEND_NOTE,
            "undecodedNote": UNDECODED_NOTE,
        }


class Effect2DDB:
    r"""``ini/effect.ini`` + ``ani/effect.ani`` for one install.

    Both halves are optional and their absences are DIFFERENT answers, which is
    why they are reported separately:

    * no ``ini/effect.ini``  -> this install has no 2D effect timing at all.
    * no ``ani/effect.ani``  -> every row resolves to zero frames, and the
      names are still worth listing because the row is the modding surface.
    * a row whose ``AniTitle`` names no ``.ani`` section -> **an unresolved
      row**, and there are real ones: 1 on 5517 (``Silent``) and 6 on 7878
      (``Silent``, ``BattleAuraAssist``, ``BattleAuraAttack``,
      ``BattleAuraControl``, ``SuperState``, ``FatigueState``).  Those are
      data, not a reader defect, and `coverage()` names them.
    * an ``.ani`` section no row names -> **an untimed sequence**, 8 on 5517
      and 33 on 7878 (``ItemUse``, ``FireLight``, the coloured lights, the
      ``Aspirit_*`` set).  They are listed with ``timed=False`` and the player
      plays them at the table's own commonest interval, LABELLED as our guess.
    """

    #: `FrameInterval` when a sequence has frames but no timing row.  The
    #: modal shipped value, 4,532 of 5,457 sections.  A GUESS, and every
    #: surface that uses it says so (`Effect2D.timed` is False).
    UNTIMED_INTERVAL_MS = 50

    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        self.assets = assets
        self.ini_path = coroot.locate_table(assets or self.root, "ini/effect.ini")
        self.ani_source, ani = load_ani(self.root, "ani/effect.ani")
        #: lowercase ani section -> frame paths
        self._ani = {k.lower(): v for k, v in ani.items()}
        self._ani_names = {k.lower(): k for k in ani}
        self._sections = read_sections(self.ini_path) if self.ini_path else {}
        self._cache: dict[str, Effect2D] = {}
        self._exists_cache: dict[str, bool] = {}

    # -- health ------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True when *something* is readable.  Frames alone count."""
        return bool(self._sections or self._ani)

    @property
    def status(self) -> dict:
        return {
            "root": str(self.root),
            "iniPath": str(self.ini_path) if self.ini_path else "",
            "iniFound": bool(self.ini_path),
            "aniSource": self.ani_source,
            "aniFound": bool(self._ani),
            "rows": len(self._sections),
            "aniSections": len(self._ani),
            "available": self.available,
        }

    # -- lookup ------------------------------------------------------------
    def names(self) -> list[str]:
        """Every playable 2D effect name: timing rows first, then untimed."""
        rows = list(self._sections)
        titled = {(self._sections[n].get("AniTitle") or n).lower()
                  for n in self._sections}
        extra = [self._ani_names[k] for k in sorted(self._ani) if k not in titled]
        return sorted(rows) + extra

    def _exists(self, logical: str) -> bool:
        if not logical:
            return False
        key = logical.lower()
        hit = self._exists_cache.get(key)
        if hit is not None:
            return hit
        ok = False
        a = self.assets
        if a is not None:
            try:
                ok = bool(a.exists(logical))
            except Exception:
                ok = False
        if not ok:
            try:
                ok = (self.root / logical).is_file()
            except OSError:
                ok = False
        self._exists_cache[key] = ok
        return ok

    def _frames_for(self, title: str) -> list[Frame2D]:
        paths = self._ani.get(title.lower(), [])
        return [Frame2D(i, p, self._exists(p)) for i, p in enumerate(paths)]

    def get(self, name: str) -> Optional[Effect2D]:
        """One effect by ``ini/effect.ini`` section name, or by ``.ani`` name.

        Section names win: an untimed ``.ani`` section is only reachable by
        this route when no row claims it, which is the same precedence
        `names()` lists them in.
        """
        if not name:
            return None
        hit = self._cache.get(name.lower())
        if hit is not None:
            return hit
        e = self._build(name)
        if e is not None:
            self._cache[name.lower()] = e
        return e

    def _build(self, name: str) -> Optional[Effect2D]:
        low = name.lower()
        sec = None
        for k, v in self._sections.items():
            if k.lower() == low:
                sec, name = v, k
                break
        if sec is not None:
            title = sec.get("AniTitle") or name
            frames = self._frames_for(title)
            e = Effect2D(
                name=name,
                ani_title=title,
                frame_interval=max(1, _int(sec, "FrameInterval", 50)),
                loop_time=_int(sec, "LoopTime", 1),
                loop_interval=_int(sec, "LoopInterval", 0),
                delay=_int(sec, "Delay", 0),
                show_way=_int(sec, "ShowWay", 0),
                exigence=_int(sec, "Exigence", 1),
                offset=(_int(sec, "OffsetX", 0), _int(sec, "OffsetY", 0)),
                frames=frames,
                ani_source=self.ani_source,
                source=str(self.ini_path.name) if self.ini_path else "ini/effect.ini",
                timed=True,
                unknown_keys={k: v for k, v in sec.items() if k not in KEYS},
            )
            if not frames:
                e.error = (
                    f"AniTitle {title!r} names no section of "
                    f"{self.ani_source or 'ani/effect.ani'}. The timing row is "
                    f"real and the frames are absent from this install — that "
                    f"is data, not a decode failure.")
            return e
        # an .ani section nothing times
        if low in self._ani:
            real = self._ani_names[low]
            return Effect2D(
                name=real, ani_title=real,
                frame_interval=self.UNTIMED_INTERVAL_MS,
                loop_time=1, frames=self._frames_for(real),
                ani_source=self.ani_source, source=self.ani_source,
                timed=False,
                error=(f"No ini/effect.ini row names {real!r}, so this sequence "
                       f"has NO DECLARED TIMING. It is being played at "
                       f"{self.UNTIMED_INTERVAL_MS} ms/frame, which is the "
                       f"table's commonest value (4,532 of 5,457 sections) and "
                       f"is OUR GUESS, not the client's."))
        return None

    # -- census ------------------------------------------------------------
    def coverage(self) -> dict:
        """What this install's 2D effect surface actually looks like.

        Every number here is counted, and the ones that are zero say so: a
        `coverage()` on an install with no tables returns zeros WITH
        `available: False`, so a caller cannot read an empty corpus as a clean
        result.
        """
        rows = len(self._sections)
        resolved = unresolved = 0
        multi = single = 0
        frames_total = missing = 0
        unresolved_names: list[str] = []
        for n in self._sections:
            e = self.get(n)
            if e is None:
                continue
            if e.frame_count:
                resolved += 1
                frames_total += e.frame_count
                missing += e.missing_frames
                if e.frame_count > 1:
                    multi += 1
                else:
                    single += 1
            else:
                unresolved += 1
                unresolved_names.append(n)
        titled = {(self._sections[n].get("AniTitle") or n).lower()
                  for n in self._sections}
        untimed = [self._ani_names[k] for k in sorted(self._ani) if k not in titled]
        return {
            "available": self.available,
            "rows": rows,
            "aniSections": len(self._ani),
            "resolved": resolved,
            "unresolved": unresolved,
            "unresolvedNames": unresolved_names,
            "multiFrame": multi,
            "singleFrame": single,
            "framesTotal": frames_total,
            "framesMissingOnDisk": missing,
            "untimedSequences": len(untimed),
            "untimedNames": untimed,
            **self.status,
        }


def _cli(argv: list[str]) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", help="one effect.ini section")
    ap.add_argument("--root", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root) if a.root else coroot.find().path
    with AssetRoot(root) as assets:
        db = Effect2DDB(root, assets)
        if a.coverage or not (a.name or a.list):
            cov = db.coverage()
            if a.json:
                print(_json.dumps(cov, indent=2))
            else:
                print(f"root          {cov['root']}")
                print(f"ini/effect.ini{'':<2}{cov['iniPath'] or 'ABSENT'}")
                print(f"frames from   {cov['aniSource'] or 'ABSENT'}")
                print(f"rows          {cov['rows']}")
                print(f"  resolved    {cov['resolved']} "
                      f"({cov['multiFrame']} multi-frame, "
                      f"{cov['singleFrame']} single-frame)")
                print(f"  unresolved  {cov['unresolved']}  {cov['unresolvedNames']}")
                print(f"frames        {cov['framesTotal']} "
                      f"({cov['framesMissingOnDisk']} not present in this install)")
                print(f"untimed .ani  {cov['untimedSequences']}")
                print()
                print(NO_BLEND_NOTE)
            return 0 if cov["available"] else 1
        if a.list:
            for n in db.names():
                print(n)
            return 0
        e = db.get(a.name)
        if e is None:
            print(f"{a.name!r} is not a section of ini/effect.ini nor of "
                  f"{db.ani_source or 'ani/effect.ani'}")
            return 2
        if a.json:
            print(_json.dumps(e.payload(), indent=2))
            return 0
        print(f"{e.name}  (AniTitle={e.ani_title})  from {e.source}")
        print(f"  {e.frame_count} frames @ {e.frame_interval} ms  "
              f"loop x{e.loop_time}{' (endless)' if e.endless else ''}  "
              f"gap {e.loop_interval} ms  delay {e.delay} ms")
        print(f"  duration {e.duration_ms if e.duration_ms is not None else 'endless'}")
        for f in e.frames:
            print(f"   {f.index:3d}  {'ok ' if f.found else 'MISSING'}  {f.path}")
        if e.error:
            print(f"  ! {e.error}")
        print()
        print(f"  {NO_BLEND_NOTE}")
        return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))


# BASELINE RE-SCOPE, 2026-09-19. The owner removed 6609.cn, CCO and Installers
# from core/baseline_members.json -- "remove the three from baseline members,
# then land the windows" -- so the baseline is 33 members. The dated figures
# above were measured over the earlier population and stand as records of that
# measurement. Over the 33, as re-derived by tests/test_claim_enumeration.py:
#   **5,315 sections across 33 installs**, every one of them ships ini/effect.ini
#
# BASELINE RE-SCOPE (2), 2026-09-19. Later the same day the owner deleted 6716
# and 7682 (byte-level copies of 6271 and 7632), renamed 7632 to 7622 (its
# build stamp), and declared 7217 7250 7275 7280 7320 7336 7373 7387 7506 7535
# 7562 7589 baseline members, so core/baseline_members.json holds 43. The
# 33-member block above is kept as the record of that measurement. Over the
# 43, re-measured per install by tests/test_claim_enumeration.py (7622 measures
# exactly what 7632 recorded; the twelve new members were walked, not assumed):
#   **7,800 sections across 43 installs**, every one of them ships ini/effect.ini
