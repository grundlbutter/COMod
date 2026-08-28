#!/usr/bin/env python3
r"""helptips.py -- read `UserHelpInfo.dat`, the client's CONTEXTUAL advice engine.

NOT a flat help file.  Every entry is GATED, and the gate is the interesting
part: the client picks which advice to show from the player's class, level,
guild membership and marital state::

    [HelpInfo2]
      Profession    = 20        <- class 2 (Warrior), promotion tier 0
      MinLevel      = 10        MaxLevel = 14
      Syndicate     = 2         Marriage = 2      Mete = ...
      HelpLineAmount= 6
      Line0 = Monsters:~Robin,~Central~Plain(418,682)
      Line1 = Equipment:~Use~LeafBlade~or~SpringSword

MEASURED on 7878 (`derived/7878-dat-decrypted/UserHelpInfo.dat.out`):

    entries with text     1,506      declared HelpInfoAmount 1506
    distinct texts        1,216      so ~290 are shared across gates
    level bands              24
    mean length             455 chars
    by class    Taoist 639 · Archer 216 · Trojan 213 · Warrior 210
                Pirate  96 · Ninja   90 · Monk    42

`~` is the space escape, the same convention `MapDestination.dat` uses -- see
`tools/landmarks.py`.
"""
from __future__ import annotations
import re, pathlib, collections
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "core"))
import coroot                                              # noqa: E402
from typing import Optional

SPACE = "~"

#: `Profession` packs TWO fields into one number: the class is the TENS digit
#: and up, the promotion tier is the ONES digit.  10..15 are all Trojan.
#:
#: **Reading the whole value as a class id yields forty phantom classes**
#: (`class 132`, `class 45`, ...) and I published that shape once before
#: catching it.  A field that produces implausibly MANY distinct values is
#: usually two fields.
CLASS_BY_TENS = {
    0: "any", 1: "Trojan", 2: "Warrior", 4: "Archer",
    5: "Taoist", 6: "Taoist", 7: "Taoist",
    10: "Monk", 13: "Ninja", 14: "Pirate",
}


def _unescape(s: str) -> str:
    return s.replace(SPACE, " ").strip()


def _int(v) -> Optional[int]:
    if v is None:
        return None
    v = v.strip()
    return int(v) if v.isdigit() else None


class Tip:
    __slots__ = ("key", "profession", "cls", "tier", "min_level", "max_level",
                 "syndicate", "marriage", "lines", "text", "raw")

    def __init__(self, key: str, kv: dict):
        self.key = key
        self.raw = kv
        p = _int(kv.get("Profession")) or 0
        self.profession = p
        self.cls = CLASS_BY_TENS.get(p // 10, f"class {p}")
        self.tier = p % 10
        self.min_level = _int(kv.get("MinLevel"))
        self.max_level = _int(kv.get("MaxLevel"))
        self.syndicate = _int(kv.get("Syndicate"))
        self.marriage = _int(kv.get("Marriage"))
        n = _int(kv.get("HelpLineAmount")) or 0
        self.lines = [_unescape(kv[f"Line{i}"]) for i in range(n) if f"Line{i}" in kv]
        self.text = " ".join(l for l in self.lines if l).strip()

    def applies_to(self, cls: str, level: int) -> bool:
        """Would the client show this tip to `cls` at `level`?

        Ignores guild and marriage -- those encodings are NOT established and
        pretending otherwise would make this look more precise than it is.
        """
        if self.cls not in (cls, "any"):
            return False
        lo = self.min_level if self.min_level is not None else 0
        hi = self.max_level if self.max_level is not None else 10 ** 6
        return lo <= level <= hi

    def __repr__(self) -> str:
        return (f"<Tip {self.key} {self.cls}"
                f"{'/' + str(self.tier) if self.tier else ''} "
                f"Lv{self.min_level}-{self.max_level} {len(self.text)}ch>")


def parse(text: str) -> dict:
    parts = re.split(r"^\[([^\]]+)\]\s*$", text, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        key = parts[i]
        if not key.startswith("HelpInfo"):
            continue
        kv = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", parts[i + 1], re.M))
        t = Tip(key, kv)
        if t.text:
            out[key] = t
    return out


def read(path) -> dict:
    """Parse a *decrypted* `UserHelpInfo.dat`. Does not decrypt -- see landmarks.py."""
    return parse(pathlib.Path(path).read_bytes().decode("latin-1"))


def by_class(tips: dict) -> dict:
    out = collections.defaultdict(list)
    for t in tips.values():
        out[t.cls].append(t)
    for v in out.values():
        v.sort(key=lambda t: (t.min_level or 0, t.tier))
    return dict(out)


def for_character(tips: dict, cls: str, level: int) -> list:
    return sorted((t for t in tips.values() if t.applies_to(cls, level)),
                  key=lambda t: (t.tier, t.key))


def _selftest() -> int:
    # Resolved, never written as a literal. tests/test_sanitization.py
    # caught this as a hardcoded install path on 2026-08-27; the four
    # sibling dat tools (datdict, datbulk, datmanifest, datpairs) all
    # already used this exact form and I wrote the literal anyway.
    p = coroot.assets_dir() / "derived" / "7878-dat-decrypted" / "UserHelpInfo.dat.out"
    if not p.exists():
        print("  SKIP: no decrypted UserHelpInfo on this box")
        return 0
    tips = read(p)
    assert len(tips) > 1400, len(tips)
    # POSITIVE: the declared count matches what we parsed
    hdr = re.search(r"HelpInfoAmount\s*=\s*(\d+)",
                    p.read_bytes().decode("latin-1"))
    assert hdr and int(hdr.group(1)) == len(tips), \
        f"header says {hdr.group(1) if hdr else '?'}, parsed {len(tips)}"
    bc = by_class(tips)
    assert "Taoist" in bc and "Archer" in bc, sorted(bc)
    # NEGATIVE CONTROL 1: the tens-digit split must not resurrect phantom
    # classes.  Any 'class NNN' key means the packing was read wrong.
    phantom = [c for c in bc if c.startswith("class ")]
    assert not phantom, f"phantom classes -- Profession read as one field: {phantom[:5]}"
    # NEGATIVE CONTROL 2: a query must be able to return NOTHING.  A filter that
    # always matches is the failure this repository is named around.
    assert not for_character(tips, "Warrior", 10 ** 6), "level filter never excludes"
    assert not for_character(tips, "NoSuchClass", 50), "class filter never excludes"
    got = for_character(tips, "Archer", 12)
    assert got, "Archer at Lv12 matched nothing"
    print(f"  OK  tips={len(tips)}  classes={len(bc)}  "
          f"distinct_texts={len({t.text for t in tips.values()})}  "
          f"Archer@12={len(got)}")
    return 0


def _cli(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 0
    tips = read(args[0])
    for cls, ts in sorted(by_class(tips).items()):
        print(f"{cls}  ({len(ts)} tips)")
        for t in ts[:3]:
            print(f"    Lv{t.min_level}-{t.max_level}: {t.text[:96]}")
    return 0


if __name__ == "__main__":
    # Stays LAST: see tools/landmarks.py for why.
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else _cli(sys.argv))
