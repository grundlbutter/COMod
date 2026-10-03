#!/usr/bin/env python3
r"""effects.py -- weapon/action -> 3D effect linkage, and the effect playback model.

This module answers two questions the asset viewer needs:

  1. **Which effect belongs to this weapon?**  ``ini/Action3DEffect.ini`` keys an
     effect name off ``<shape>.<action>.<idHi>.<idLo>`` where ``idHi+idLo`` is the
     6-digit equipment appearance ID, and ``ini/WeaponEffect.ini`` keys the impact
     spark off the 3-digit weapon type.

  2. **How is that effect played back?**  ``ini/3DEffect.ini`` gives per-effect
     timing and a list of layers; each layer names a mesh through
     ``ini/3DEffectObj.ini`` and a texture through ``ini/3dtexture.ini``.
     **On 5517 and 6090 all three of those tables are read from their compiled
     ``.dbc`` twins instead** -- the client reads the twin where one exists, so
     `EffectDB` does too, per file and per base.  On 5017/5065/5165/CCO there
     are no twins and the plaintext ini *is* the live table; that is the normal
     answer on four of the six declared bases, not a broken install.
     ``--coverage`` prints a ``tables_read`` block naming the file behind every
     number, so "did this run see the live table?" is a line of output rather
     than a question about the code.  The
     mesh is a C3 container holding one of three animation forms -- ``PHY``+
     ``MOTI`` (node/bone matrix track), ``SHAP``+``SMOT`` (a two-point blade line
     smeared into a ribbon trail), or ``PTCL``/``PTC3`` (particle system).

Everything the module claims is marked VERIFIED or INFERRED in ``docs/effects.md``.
The four binary parsers below (``parse_moti``, ``parse_shap``, ``parse_smot`` and
``parse_ptcl``) were recovered instruction-by-instruction from ``graphic.dll`` and
consume their chunk to exactly the declared length on 100% of this install's corpus
-- run ``--validate``.  Independent of that, ``tools/ptclprove.py`` mutation-tests
the particle layout: consuming the right *number* of bytes does not prove the
fields are in the right *order*, and that tool is what tells the two apart.

CLI::

    py -3 tools/effects.py --weapon 410009        # everything for one weapon
    py -3 tools/effects.py --effect Flash4102     # resolve one effect to assets
    py -3 tools/effects.py --list --form ribbon   # every ribbon-trail effect
    py -3 tools/effects.py --list --form phy --form ribbon --form-mode all
    py -3 tools/effects.py --form-census          # how many carry each form
    py -3 tools/effects.py --coverage             # the numbers in docs/effects.md
    py -3 tools/effects.py --validate             # re-derive the parser proof
    py -3 tools/effects.py --linkage              # write out/effects/linkage.json
    py -3 tools/ptclprove.py --all-bases          # the particle LAYOUT proof

Prefix any of these with ``CO_ROOT=<install>``.  ``--coverage`` and
``--validate`` print the ``root`` and ``base_id`` they resolved as their first
two keys: the numbers differ substantially between installs, and a figure
quoted without its base has been wrong here before (docs/CORRECTIONS.md C33).
"""
from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import AssetRoot, C3File, DEFAULT_ROOT          # noqa: E402
import coroot                                                  # noqa: E402
import c3ccfl                                                  # noqa: E402
import c3phy                                                   # noqa: E402
import dbc                                                     # noqa: E402
import dbcshadow                                               # noqa: E402

#: The three-wide wildcard, which is what CCO writes in **every** key field.
#:
#: **This constant is a WIDTH as well as a value, and that is the trap.** The
#: official clients write the *action* field four wide (`999.0100.130.300`),
#: so their always-on rows are keyed `9999`, which is neither equal to this
#: string nor numerically equal to it. `_field_matches` compares numerically
#: and is right to refuse them: 9999 and 999 are different numbers.
#:
#: The fix is NOT to widen this constant or to make an all-nines value of any
#: width read as the wildcard here. MEASURED (2026-08-09): treating any
#: all-nines value as the wildcard changes **19,143** action-effect answers on
#: 5517 and **30,942** on 6090 -- 19,041 and 30,840 of them turning "this
#: action has no effect" into "this action has the always-on aura", i.e.
#: serving the aura as if it were the per-attack trail. On CCO, where the
#: sentinel already matches the field width, the same change moves **0** of
#: 91,528 answers. Zero is what correct looks like here; five figures is not.
#: See `is_always_on` for the narrow, measured fix and CORRECTIONS C35.
WILDCARD = "999"


def is_always_on(action: str) -> bool:
    r"""Is this Action3DEffect action field the **always-on** (aura) group?

    All-nines of *any width*, because the width is a per-client spelling and
    the meaning is not: CCO writes `999`, 5517 and 6090 write `9999`. Asking
    the question this way removes the sentinel from the caller entirely, which
    is the point -- a literal `"999"` in a caller is the same bug one width
    along, and this project has now hit that class three times.

    MEASURED on the raw `ini/Action3DEffect.ini` of all three installs: the
    always-on rows exist in every client and only the wildcard's spelling
    moved -- CCO 826 rows keyed `999`, 5517 972 and 6090 2,604 keyed `9999`.
    The earlier claim that 6090 ships none is refuted; see CORRECTIONS C35.

    Note this is deliberately *narrower* than "is the wildcard": it says which
    ACTION GROUP a row belongs to, not that the field matches every query.
    `WILDCARD` explains why that distinction is load-bearing.
    """
    return _all_nines(action)


def all_nines(field: str) -> bool:
    r"""Is this key field all nines, at whatever width the client writes it?

    The one definition of the spelling, so a second one cannot drift from it.
    `is_always_on` is this question asked about the action field, and
    `specificity` is it asked about any of the four. Both used to spell it
    themselves and one of them was three wide -- which is CORRECTIONS C35.

    `len >= 3` because a bare `"9"` is a plausible *unpadded action code*, not
    a wildcard; every client writes the sentinel at least three wide.
    """
    return bool(field) and len(field) >= 3 and set(field) == {"9"}


#: The spelling this module used before the predicate had an outside
#: caller. Kept so nothing in here has to change and so the ONE
#: definition stays one definition -- `depclose.effect_users` asks the
#: same question about `group_hi`/`group_lo` and must not re-spell it.
_all_nines = all_nines

# ---------------------------------------------------------------------------
# tiny ini readers.  The 3D tables are not RFC-anything; they are three shapes:
#   * flat  key=value                         (3DEffectObj, 3dtexture, ...)
#   * [section] + key=value                   (3DEffect, WeaponEffect, ...)
#   * dotted key=value with 999 wildcards     (Action3DEffect, ActionSound)
# codepage.ini is 1 byte; every legacy ini in this install decodes as GBK, and
# GBK is a superset of ASCII so pure-ASCII tables are unaffected.
# ---------------------------------------------------------------------------

ENCODING = "gbk"


#: ini tables an install does not ship, in the order they were first asked
#: for.  Module-level because both readers below are module functions and
#: every caller funnels through `_lines`; a run against two roots in one
#: process therefore accumulates both, which is why `report_missing_tables`
#: prints names and not a root.
MISSING_TABLES: list[str] = []


def report_missing_tables() -> None:
    """Print the absences once, or say nothing when there were none."""
    if MISSING_TABLES:
        print(f"[effects] {len(MISSING_TABLES)} ini table(s) this install "
              f"does not ship, so what they describe is absent rather than "
              f"empty: {', '.join(MISSING_TABLES)}", file=sys.stderr,
              flush=True)


def _lines(path: Path) -> Iterator[str]:
    # An install may legitimately not ship a table. `read_bytes` raised, so
    # ONE absent ini took the whole build down: `tools/effects.py --linkage`
    # died on Zephyr's missing `ini/3DEffect.ini` (measured 2026-09-18, the
    # nine-client matrix), and Zephyr is missing FIVE of the sixteen this
    # module names -- 3DEffect, 3DEffectObj, 3dmotion, 3dobj, 3dtexture -- so
    # guarding the one that raised would only have moved the crash.
    #
    # This is the same defect `tools/meshtex.py` carried and the same fix:
    # the readers that DO guard did it caller by caller (see `read_flat(path)
    # if path.is_file() else {}` below), which is a convention rather than a
    # contract, and a convention is exactly what the next caller forgets.
    # Recorded rather than swallowed, for the reason meshtex gives: "has no
    # table" and "has an empty table" are different facts and the second is
    # the one that hides.
    p = Path(path)
    if not p.is_file():
        if p.name not in MISSING_TABLES:
            MISSING_TABLES.append(p.name)
        return
    for raw in p.read_bytes().decode(ENCODING, errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", ";")):
            continue
        yield line


def read_flat(path: Path) -> dict[str, str]:
    """``key=value`` per line, later wins."""
    out: dict[str, str] = {}
    for line in _lines(path):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


#: Filled by `read_flat_live` on every call, keyed by ini filename: what it
#: actually read. A plain report, not a cache -- every entry is overwritten by
#: the next call for that file, and nothing consults it to decide anything.
#: Kept so `--linkage` and `--coverage` can state their own provenance.
FLAT_SOURCES: dict[str, dict] = {}


def read_flat_live(path: Path) -> dict[str, str]:
    r"""`read_flat`, plus the compiled twin overlaid on top where one exists.

    **This is the table the client reads.** `GraphicData.dll` reads the
    compiled `ini/*.dbc`; from 5517 on, the plaintext beside it is a 2009
    decoy. Reading only the ini here is what made every effect coverage figure
    a floor rather than a count.

    MEASURED on the id -> path tables (`ini keys` -> `twin keys`):

        3DEffectObj   5517  3,268 -> 6,268     6090  3,268 ->  9,472
        3dtexture     5517  8,793 -> 13,803    6090  8,793 -> 18,638
        3dobj         5517  1,443 -> 2,125     6090  1,443 ->  2,985
        miscmotion    both      0 -> 218
        MountMotion   5517      0 -> 784       6090      0 ->  8,758

    `miscmotion.ini` and `MountMotion.ini` are **zero bytes** on both installs.
    That is the sharpest form of the trap: a plaintext crawl reads them as
    *"this table is empty"* rather than *"you opened the wrong file"*, so the
    failure looks like absence of data instead of a misread, and nothing
    anywhere reports an error.

    **The overlay is refused when the key spaces are disjoint.** The twin is
    keyed `str(id)`, which is the ini's own spelling for the tables above but
    not for `WeaponMotion` -- 25,944 ini keys against 169,968 twin keys with
    zero in common on 6090. Merging those would build a map whose keys mean two
    different things, and `EffectDB` matches weapon motion keys by *string
    prefix*, so injected u32 ids would match spuriously rather than merely
    uselessly. When nothing overlaps, this returns the ini unchanged and
    records `overlaid: False` in `FLAT_SOURCES` for the caller to report.

    **The ini's own keys are canonicalised before either question is asked,
    and that is not tidiness -- an earlier edition of this function compared
    raw strings and under-counted the overlap.** `ini/3dtexture.ini` spells 46
    of its ids zero-padded (`001137360` where the twin has `1137360`) and
    `ini/3dobj.ini` spells 48 that way. Against raw strings those keys match
    nothing in the twin, so they survived the `update` **beside** the row they
    are a spelling of: one logical id, two entries, and the padded one still
    carrying the 2009 value. Measured on 5517 and 6090: `3dtexture` merged to
    13,856 / 18,691 keys where the true count is 13,810 / 18,645, and `3dobj`
    to 2,174 / 3,034 against 2,126 / 2,986. It is the key-width rule again
    (docs/CORRECTIONS.md §2) -- **a width difference in a KEY reports absence,
    confidently** -- and it reported 53 missing texture ids where there are 7.

    Stated exactly, because the honest version is narrower than the alarming
    one: **all 94 ghost pairs currently carry the same path under both
    spellings**, so nothing was reading a stale value. It is C4's shape -- the
    right bytes for the wrong reason -- and it would have started returning
    the 2009 path the first time TQ re-pointed one of those ids. Registered as
    `C49-effe-definitions-merge`.

    Canonicalisation is applied **only where the twin has that id**, so a base
    with no twin (the whole plaintext lineage) is returned byte-for-byte as
    before, and a padded key with no compiled counterpart keeps its spelling.

    Per file, per base, per call: the twin is located by `dbcshadow`, which
    resolves from this path's own directory and holds nothing (C21/C22).
    """
    path = Path(path)
    out = read_flat(path) if path.is_file() else {}
    ini_keys = len(out)
    rows = dbcshadow.twin_rows(path)
    rec = {"ini": path.name, "ini_keys": ini_keys, "twin": None,
           "twin_keys": 0, "overlaid": False, "shared": 0}
    if rows:
        twin = dbcshadow.compiled_twin(path)
        rec["twin"] = twin.name if twin else None
        rec["twin_keys"] = len(rows)
        # Fold each padded ini key onto the canonical spelling the twin uses,
        # BEFORE the intersection is measured and before the update -- doing it
        # after would leave the ghost entry and only fix the count.
        ghosts = [k for k in out
                  if k not in rows and k.isdigit() and str(int(k)) in rows]
        for k in ghosts:
            out[str(int(k))] = out.pop(k)
        rec["padded_ini_keys_folded"] = len(ghosts)
        shared = len(set(out) & set(rows)) if out else 0
        rec["shared"] = shared
        # An empty ini cannot disagree with anything, so an empty-vs-N overlay
        # is always right; the disjointness question only arises when both
        # sides have keys.
        if not out or shared:
            out.update(rows)                    # the file the client reads wins
            rec["overlaid"] = True
        else:
            # WeaponMotion reaches here, and reaches it *after* folding --
            # which is the check that matters, because a normalisation that
            # manufactured an overlap would silently re-enable the merge this
            # branch exists to refuse. It does not: folding moves 0 of its
            # 25,944 keys and the intersection is still empty.
            rec["disjoint"] = True
    rec["result_keys"] = len(out)
    FLAT_SOURCES[path.name] = rec
    return out


def read_flat_live_reported(path: Path) -> tuple[dict[str, str], dict]:
    """`read_flat_live`, returning its provenance record with the table.

    `FLAT_SOURCES` is keyed by **filename**, so two `EffectDB`s for two
    different installs in one process overwrite each other's entries and a
    later snapshot of the global attributes one base's provenance to another.
    That is C21's shape exactly, in the reporting layer rather than the data
    layer -- and this project has already shipped one tool that reported the
    wrong base with no error at all. Callers that keep the record should take
    it from **their own call**, which is what this returns; `FLAT_SOURCES`
    stays as the informal per-run report it was documented to be.
    """
    out = read_flat_live(path)
    return out, dict(FLAT_SOURCES[Path(path).name])


def read_sections(path: Path) -> dict[str, dict[str, str]]:
    """``[section]`` + ``key=value``."""
    out: dict[str, dict[str, str]] = {}
    cur: Optional[dict[str, str]] = None
    for line in _lines(path):
        if line[0] == "[" and line.endswith("]"):
            cur = out.setdefault(line[1:-1].strip(), {})
            continue
        if cur is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cur[k.strip()] = v.strip()
    return out


def read_dotted(path: Path) -> list[tuple[tuple[str, ...], str]]:
    """``a.b.c[.d]=value``.  Order preserved; duplicate keys are kept (the file
    has 251 of them, always with an identical value)."""
    rows: list[tuple[tuple[str, ...], str]] = []
    for line in _lines(path):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        rows.append((tuple(p.strip() for p in k.strip().split(".")), v.strip()))
    return rows


def read_weapon_skill_names(ini_dir: Path) -> dict[str, str]:
    r"""``ini/WeaponSkillName`` -> ``{"410": "Blade", ...}``, either spelling.

    The community client ships a pre-parsed ``WeaponSkillName.json``
    (``[{"id": 410, "name": "Blade"}, ...]``); every official client ships
    TQ's original ``WeaponSkillName.ini``, which is **not** sectioned ini at
    all but a flat ``id,name`` list::

        000,Boxing
        410,Blade

    Reading only the JSON left `weapon_type_names` **empty on all five
    official clients** -- measured 48 rows on CCO against 0 on 5017, 5065,
    5165, 5517 and 6090, each of which ships the ``.ini`` and never had it
    opened. It failed inside a bare ``except Exception: pass``, so every
    weapon rendered with a blank type name and nothing said why. Same shape
    as ``docs/CORRECTIONS.md`` C-2026-08-09-ani-json-spelling, one table over.

    MEASURED, and it is what makes the two spellings interchangeable rather
    than merely similar: CCO's 48 JSON rows and 5017's 48 ini rows agree on
    **all 48 ids and all 48 names, with no disagreement and no duplicate id
    on any base**. Counts grow with the patch level (48, 48, 49, 50, 58) the
    way a weapon table should.

    Keys are zero-padded to three, which is the width `weapon_type_of` slices
    an appearance id to and the width both sources already use; a wider id
    would be left alone rather than truncated. Names are stripped -- 6090
    ships ``610,PrayerBeads `` with a trailing space -- so a caller cannot
    tell which source answered.
    """
    out: dict[str, str] = {}
    js = Path(ini_dir) / "WeaponSkillName.json"
    if js.is_file():
        try:
            rows = json.loads(js.read_text("utf-8-sig"))
        except (OSError, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else ():
            if isinstance(row, dict) and row.get("id") is not None:
                out[str(row["id"]).strip().zfill(3)] = str(
                    row.get("name", "")).strip()
        if out:
            return out
    ini = Path(ini_dir) / "WeaponSkillName.ini"
    if not ini.is_file():
        return out
    for line in _lines(ini):
        if "," not in line:
            continue
        ident, name = line.split(",", 1)
        ident = ident.strip()
        if ident:
            out[ident.zfill(3)] = name.strip()
    return out


def _int(d: dict[str, str], key: str, default: int = 0) -> int:
    v = d.get(key)
    if v is None:
        return default
    try:
        return int(float(v.split()[0]))
    except (ValueError, IndexError):
        return default


def _float(d: dict[str, str], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v.split()[0])
    except (ValueError, IndexError):
        return default


# ---------------------------------------------------------------------------
# D3D blend factors -- ASB / ADB.  D3DBLEND from d3d9types.h; the same numbers
# appear as Asb/Adb in weapon.ini and armor.ini, so this is one shared enum.
# ---------------------------------------------------------------------------

D3DBLEND = {
    1: "ZERO", 2: "ONE", 3: "SRCCOLOR", 4: "INVSRCCOLOR", 5: "SRCALPHA",
    6: "INVSRCALPHA", 7: "DESTALPHA", 8: "INVDESTALPHA", 9: "DESTCOLOR",
    10: "INVDESTCOLOR", 11: "SRCALPHASAT", 12: "BOTHSRCALPHA",
    13: "BOTHINVSRCALPHA", 14: "BLENDFACTOR", 15: "INVBLENDFACTOR",
}


def blend_name(v: int) -> str:
    return D3DBLEND.get(v, f"UNKNOWN({v})")


# ---------------------------------------------------------------------------
# C3 animation chunks
# ---------------------------------------------------------------------------

Mat4 = tuple  # 16 floats, row-major, row-vector convention (translation in row 3)

IDENTITY: Mat4 = (1.0, 0.0, 0.0, 0.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0)


def quat_to_matrix(x: float, y: float, z: float, w: float) -> Mat4:
    """D3DXMatrixRotationQuaternion, row-vector convention.

    graphic.dll!Motion_Load tail-calls the real D3DX function for ZKEY frames
    (the thunk at RVA 0x1D1F0A jumps to d3dx10_43!D3DXMatrixRotationQuaternion),
    so this reproduces it rather than inventing a convention.
    """
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy), 0.0,
            2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx), 0.0,
            2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy), 0.0,
            0.0, 0.0, 0.0, 1.0)


def mat_mul(a: Mat4, b: Mat4) -> Mat4:
    out = []
    for r in range(4):
        for c in range(4):
            out.append(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)))
    return tuple(out)


def transform_point(m: Mat4, p) -> tuple[float, float, float]:
    """Row-vector: p' = p * M (matches c3phy.apply_matrix_to)."""
    x, y, z = p
    return (x * m[0] + y * m[4] + z * m[8] + m[12],
            x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


@dataclass
class MotionKey:
    frame: int
    matrices: list[Mat4]        # one per bone
    #: The **on-disk** floats this key was decoded from, one tuple per bone --
    #: populated only where `matrices` is a *derived* view and the write side
    #: therefore cannot recover the source from it:
    #:
    #:   ZKEY  7 floats  (qx, qy, qz, qw, tx, ty, tz)
    #:   XKEY  12 floats (the 4x3 rows, before the 0/0/0/1 column is injected)
    #:
    #: Empty for KKEY and RAW, where the 16 matrix floats *are* the disk form.
    #: For ZKEY this is not a convenience: `quat_to_matrix` is lossy (it
    #: normalises, and q and -q give the same matrix), so a serializer working
    #: from `matrices` alone cannot reproduce the bytes it was handed.
    source: list[tuple] = field(default_factory=list)


@dataclass
class Motion:
    """A decoded ``MOTI`` chunk -- see docs/effects.md §6.1."""
    bone_count: int
    frame_count: int
    encoding: str               # "RAW" | "KKEY" | "ZKEY" | "XKEY"
    keys: list[MotionKey]
    extra_channels: int = 0     # trailing block the engine skips
    consumed: int = 0
    size: int = 0
    #: The `extraChannels * frameCount * 4` bytes the engine seeks past.  The
    #: engine does not interpret them and neither do we, so they are carried
    #: verbatim -- without this `serialize_moti` would silently drop them.
    #: Shorter than the declared length only on a truncated chunk, which is
    #: also what makes `exact` False.
    extra_payload: bytes = b""
    #: Anything after the modelled end of the chunk.  `exact` already reports
    #: its presence; this keeps the bytes so a round trip is still byte-exact.
    trailing: bytes = b""

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    def matrix(self, bone: int, frame: int) -> Mat4:
        """Reproduces graphic.dll!Motion_GetMatrix (RVA 0x551A0).

        Clamps to the first/last key, otherwise linearly interpolates the 16
        matrix elements between the bracketing keys.  Element-wise lerp is what
        the engine literally does -- no quaternion slerp at this level, because
        ZKEY has already been baked to matrices at load time.
        """
        if not self.keys or bone >= self.bone_count:
            return IDENTITY
        if frame <= self.keys[0].frame:
            return self.keys[0].matrices[bone]
        if frame >= self.keys[-1].frame:
            return self.keys[-1].matrices[bone]
        for i in range(1, len(self.keys)):
            if frame < self.keys[i].frame:
                a, b = self.keys[i - 1], self.keys[i]
                span = b.frame - a.frame
                t = 0.0 if span == 0 else (frame - a.frame) / span
                ma, mb = a.matrices[bone], b.matrices[bone]
                return tuple(ma[k] + (mb[k] - ma[k]) * t for k in range(16))
        return self.keys[-1].matrices[bone]


def parse_moti(body: bytes) -> Motion:
    """Decode a ``MOTI`` chunk.  VERIFIED against graphic.dll!Motion_Load (0x557A0).

    ::

        u32 boneCount           # error "invalid bone count : %d, 255 is MAX" if > 255
        u32 frameCount          # the modulus used by Phy_NextFrame
        char[4] encoding        # "KKEY" | "ZKEY" | "XKEY", else rewind 4 and use RAW
        -- KKEY --  u32 keyCount ; keyCount x { u32 frame ; mat4x4[boneCount] }
        -- ZKEY --  u32 keyCount ; keyCount x { u16 frame ; {quat xyzw; float3 t}[boneCount] }
        -- XKEY --  u32 keyCount ; keyCount x { u16 frame ; float[12][boneCount] }
        -- RAW  --  mat4x4[frameCount] per bone, bone-major; keys are frames 0..n-1
        u32 extraChannels ; skip extraChannels * frameCount * 4 bytes

    Everything needed to rebuild the chunk byte-for-byte is retained, so
    ``serialize_moti(parse_moti(b)) == b``.  Two of those things are *not*
    reachable from the decoded matrices and are kept alongside them:
    ``MotionKey.source`` (the ZKEY quaternion/translation and the XKEY 4x3
    rows, which the matrix view is a lossy projection of) and
    ``Motion.extra_payload`` (the extraChannels block the engine seeks past
    without reading).  ``Motion.trailing`` keeps anything after the modelled
    end.  See ``docs/moti_writer_2026-09-05.md``.
    """
    bone_count, frame_count = struct.unpack_from("<II", body, 0)
    if bone_count > 255:
        raise ValueError(f"invalid bone count {bone_count} (255 is MAX)")
    off = 8
    tag = bytes(body[8:12])
    keys: list[MotionKey] = []

    if tag in (b"KKEY", b"ZKEY", b"XKEY"):
        off = 12
        (key_count,) = struct.unpack_from("<I", body, off)
        off += 4
        for _ in range(key_count):
            src: list[tuple] = []
            if tag == b"KKEY":
                (frame,) = struct.unpack_from("<I", body, off)
                off += 4
                mats = [tuple(struct.unpack_from("<16f", body, off + 64 * b))
                        for b in range(bone_count)]
                off += 64 * bone_count
            elif tag == b"ZKEY":
                (frame,) = struct.unpack_from("<H", body, off)
                off += 2
                mats = []
                for b in range(bone_count):
                    q = struct.unpack_from("<7f", body, off + 28 * b)
                    qx, qy, qz, qw, tx, ty, tz = q
                    m = list(quat_to_matrix(qx, qy, qz, qw))
                    m[12], m[13], m[14], m[15] = tx, ty, tz, 1.0
                    mats.append(tuple(m))
                    src.append(q)
                off += 28 * bone_count
            else:  # XKEY -- 4x3, expanded to 4x4
                (frame,) = struct.unpack_from("<H", body, off)
                off += 2
                mats = []
                for b in range(bone_count):
                    f = struct.unpack_from("<12f", body, off + 48 * b)
                    mats.append((f[0], f[1], f[2], 0.0,
                                 f[3], f[4], f[5], 0.0,
                                 f[6], f[7], f[8], 0.0,
                                 f[9], f[10], f[11], 1.0))
                    src.append(f)
                off += 48 * bone_count
            keys.append(MotionKey(frame, mats, src))
        enc = tag.decode("ascii")
    else:
        # No recognised tag: the loader seeks back 4 and reads a dense
        # bone-major block of frameCount matrices per bone.
        enc = "RAW"
        per_bone: list[list[Mat4]] = []
        for _ in range(bone_count):
            per_bone.append([tuple(struct.unpack_from("<16f", body, off + 64 * f))
                             for f in range(frame_count)])
            off += 64 * frame_count
        for f in range(frame_count):
            keys.append(MotionKey(f, [per_bone[b][f] for b in range(bone_count)]))

    (extra,) = struct.unpack_from("<I", body, off)
    off += 4
    payload = b""
    if extra:
        n = extra * frame_count * 4
        payload = bytes(body[off:off + n])   # short only on a truncated chunk
        off += n
    # `consumed` stays the *declared* end (payload length included) so `exact`
    # keeps its old meaning; `trailing` is what a byte-exact writer still owes.
    return Motion(bone_count, frame_count, enc, keys, extra, off, len(body),
                  payload, bytes(body[off:]))


class MotiWriteError(ValueError):
    """A `Motion` that cannot be expressed as a MOTI chunk."""


def serialize_moti(m: Motion) -> bytes:
    r"""Encode a `Motion` back to a ``MOTI`` chunk body -- the inverse of
    `parse_moti`, in the shape `tools/c3write.py` uses for ``PHY``.

    **Byte-exactness first.**  Every field is written from the value the parser
    read, and the two blocks the parser does not interpret -- the extraChannels
    payload and any trailing bytes -- are re-emitted verbatim::

        from effects import parse_moti, serialize_moti
        assert serialize_moti(parse_moti(body)) == body

    The gate is `tests/test_moti_roundtrip.py`, which asserts that over every
    MOTI chunk in the corpus and refuses to pass on an empty corpus or on an
    encoding it never saw.

    Where the two sides differ, this one writes what the *engine* reads:

      * ZKEY and XKEY are written from `MotionKey.source`, not from
        `MotionKey.matrices`.  The matrix view is derived and, for ZKEY, lossy;
        rebuilding a quaternion from it would change the bytes.  A ZKEY/XKEY
        key with no `source` is refused rather than guessed at.
      * RAW has no keyCount and no per-key frame number on disk: the frames are
        implicitly 0..frameCount-1 and the block is **bone-major**.  A RAW
        `Motion` whose key list does not match that is refused, because the
        frame numbers would be silently discarded.
    """
    if not (0 <= int(m.bone_count) <= 255):
        raise MotiWriteError(
            f"invalid bone count {m.bone_count} (255 is MAX)")
    if not (0 <= int(m.frame_count) <= 0xFFFFFFFF):
        raise MotiWriteError(f"frameCount out of range: {m.frame_count}")
    bc = int(m.bone_count)

    out = bytearray(struct.pack("<II", bc, int(m.frame_count)))

    def _check_bones(k, n, what):
        if len(k.matrices) != bc:
            raise MotiWriteError(
                f"key at frame {k.frame} has {len(k.matrices)} matrices, "
                f"boneCount is {bc}")
        if n is not None and len(k.source) != bc:
            raise MotiWriteError(
                f"{m.encoding} key at frame {k.frame} carries "
                f"{len(k.source)} source tuples, boneCount is {bc}. The "
                f"on-disk {what} cannot be recovered from the matrices "
                f"(parse_moti fills MotionKey.source; a hand-built key must "
                f"too).")

    if m.encoding in ("KKEY", "ZKEY", "XKEY"):
        out += m.encoding.encode("ascii")
        out += struct.pack("<I", len(m.keys))
        for k in m.keys:
            if m.encoding == "KKEY":
                _check_bones(k, None, "")
                if not (0 <= int(k.frame) <= 0xFFFFFFFF):
                    raise MotiWriteError(f"KKEY frame {k.frame} exceeds u32")
                out += struct.pack("<I", int(k.frame))
                for mat in k.matrices:
                    out += struct.pack("<16f", *mat)
            else:
                width, what = ((7, "quaternion + translation")
                               if m.encoding == "ZKEY" else (12, "4x3 rows"))
                _check_bones(k, width, what)
                if not (0 <= int(k.frame) <= 0xFFFF):
                    raise MotiWriteError(
                        f"{m.encoding} stores the frame as u16; {k.frame} "
                        f"does not fit")
                out += struct.pack("<H", int(k.frame))
                for s in k.source:
                    if len(s) != width:
                        raise MotiWriteError(
                            f"{m.encoding} source tuple has {len(s)} floats, "
                            f"expected {width}")
                    out += struct.pack(f"<{width}f", *s)
    elif m.encoding == "RAW":
        # No tag, no keyCount, no per-key frame: bone-major mat4x4[frameCount].
        if len(m.keys) != int(m.frame_count):
            raise MotiWriteError(
                f"RAW carries one key per frame; got {len(m.keys)} keys for "
                f"frameCount {m.frame_count}")
        for i, k in enumerate(m.keys):
            if int(k.frame) != i:
                raise MotiWriteError(
                    f"RAW frame numbers are implicit 0..n-1; key {i} says "
                    f"frame {k.frame}, and writing it would drop that")
            if len(k.matrices) != bc:
                raise MotiWriteError(
                    f"RAW key {i} has {len(k.matrices)} matrices, boneCount "
                    f"is {bc}")
        for b in range(bc):
            for k in m.keys:
                out += struct.pack("<16f", *k.matrices[b])
    else:
        raise MotiWriteError(f"unknown MOTI encoding {m.encoding!r}")

    if not (0 <= int(m.extra_channels) <= 0xFFFFFFFF):
        raise MotiWriteError(
            f"extraChannels out of range: {m.extra_channels}")
    out += struct.pack("<I", int(m.extra_channels))
    out += m.extra_payload
    out += m.trailing
    return bytes(out)


@dataclass
class Shape:
    """A decoded ``SHAP`` chunk -- the trail/ribbon source line."""
    name: str
    frames: list[list[tuple[float, float, float]]]
    label: str
    segments: int
    consumed: int = 0
    size: int = 0
    #: THE ON-DISK FORMS, carried so `serialize_shap` can be byte-exact.
    #: `name`/`label` are `gbk`-decoded with ``errors="replace"``, which is not
    #: reversible for every byte string, and `segments` has had an on-disk 0
    #: normalised to 1 (the engine does that at 0x5D63A).  Re-encoding the
    #: decoded view is therefore a guess; these are the bytes that were read.
    #: The writer prefers each of them ONLY while the decoded view still
    #: matches, so editing `name`/`label`/`segments` still works -- see
    #: `serialize_shap`.
    name_raw: bytes = b""
    label_raw: bytes = b""
    segments_raw: Optional[int] = None
    #: Bytes past the modelled end of the chunk.  The engine stops reading
    #: here; nothing in the corpus has any (measured), and carrying them is
    #: what keeps that a measurement rather than an assumption.
    trailing: bytes = b""

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def line(self) -> list[tuple[float, float, float]]:
        """The two endpoints Shape_Draw actually consumes: frames[0][0..1]."""
        return self.frames[0][:2] if self.frames else []


def parse_shap(body: bytes) -> Shape:
    """Decode a ``SHAP`` chunk.  VERIFIED against graphic.dll!Shape_Load (0x5D570)::

        u32 nameLen ; char[nameLen] name        # skipped by Seek in the engine
        u32 frameCount
        frameCount x { u32 pointCount ; float3[pointCount] }
        u32 labelLen ; char[labelLen] label     # skipped by Seek
        u32 segments                            # 0 is normalised to 1
    """
    off = 0
    (n,) = struct.unpack_from("<I", body, off); off += 4
    name = bytes(body[off:off + n]); off += n
    (fc,) = struct.unpack_from("<I", body, off); off += 4
    frames = []
    for _ in range(fc):
        (pc,) = struct.unpack_from("<I", body, off); off += 4
        frames.append([struct.unpack_from("<3f", body, off + 12 * j) for j in range(pc)])
        off += 12 * pc
    (n2,) = struct.unpack_from("<I", body, off); off += 4
    label = bytes(body[off:off + n2]); off += n2
    (seg,) = struct.unpack_from("<I", body, off); off += 4
    return Shape(name.decode(ENCODING, "replace"), frames,
                 label.decode(ENCODING, "replace"), seg or 1, off, len(body),
                 name_raw=name, label_raw=label, segments_raw=seg,
                 trailing=bytes(body[off:]))


# ---------------------------------------------------------------------------
# The writers for the trail/particle chunk family.
#
# Every one of these is the inverse of the parser directly above it, in the
# shape `serialize_moti` established: byte-exactness first, and a REFUSAL
# wherever the on-disk form cannot be recovered from the decoded view.
#
# THE ONE RULE THEY ALL SHARE, and the reason they can be byte-exact at all:
# several fields are decoded LOSSILY -- `gbk` with ``errors="replace"``, a 0
# normalised to 1, a billboard mode with 100 subtracted, a texture scroll with
# two components negated.  Each parser now also carries the value it actually
# read.  `_prefer_raw` writes that stored value while the decoded view still
# agrees with it, and falls back to re-deriving from the view once a caller has
# edited it.  So an untouched chunk round-trips by construction, and an edited
# one is written from the edit -- with no branch that silently prefers a
# remembered original over what it was handed (the failure
# `mutation_control` in the gate exists to catch).
# ---------------------------------------------------------------------------

class ShapeWriteError(ValueError):
    """A `Shape` that cannot be expressed as a SHAP chunk."""


class SMotionWriteError(ValueError):
    """An `SMotion` that cannot be expressed as an SMOT/RMOT chunk."""


class PtclWriteError(ValueError):
    """A `Particle` that cannot be expressed as a PTCL/PTCX/PTC3 chunk."""


class RibbWriteError(ValueError):
    """A `Ribbon` that cannot be expressed as a RIBB chunk."""


def _prefer_raw(raw, view, decode):
    """`raw` if `decode(raw)` still equals `view`, else `None`.

    `None` means "the caller edited the decoded view, write that instead".
    The comparison is what makes this safe: it can only return `raw` when raw
    and view are the same value said two ways.
    """
    if raw is None:
        return None
    try:
        return raw if decode(raw) == view else None
    except Exception:                                          # noqa: BLE001
        return None


def _enc_str(s: str, raw: bytes, what: str, exc=ValueError) -> bytes:
    """The on-disk bytes for a counted string field.

    Prefers the bytes the parser read; re-encodes the decoded view once that
    view has been edited.  A view that `gbk` cannot encode is REFUSED rather
    than written with U+FFFD replacements, because that would be a silent
    content change wearing a successful write.
    """
    keep = _prefer_raw(raw or None, s, lambda b: b.decode(ENCODING, "replace"))
    if keep is not None:
        return keep
    try:
        return s.encode(ENCODING)
    except UnicodeEncodeError as e:
        raise exc(
            f"{what}: {s!r} cannot be encoded as {ENCODING} ({e}); the "
            f"on-disk bytes are gone and writing replacements would change "
            f"the string") from None


def _u32(v: int, what: str, exc=ValueError) -> bytes:
    v = int(v)
    if not 0 <= v <= 0xFFFFFFFF:
        raise exc(f"{what}: {v} does not fit in a u32")
    return struct.pack("<I", v)


def serialize_shap(s: Shape) -> bytes:
    r"""Encode a `Shape` back to a ``SHAP`` chunk body -- `parse_shap`'s
    inverse::

        from effects import parse_shap, serialize_shap
        assert serialize_shap(parse_shap(body)) == body

    The gate is `tests/test_fx_roundtrip.py`, which asserts that over every
    SHAP chunk it can reach and refuses to pass on an empty corpus.

    Three fields are written from the value the parser READ rather than from
    the decoded view, for as long as the two agree (`_prefer_raw`):

      * `name` / `label` -- decoded `gbk` with ``errors="replace"``, which is
        not a reversible transform.
      * `segments` -- the engine normalises an on-disk 0 to 1 (0x5D63A) and
        `parse_shap` reproduces that, so `segments == 1` is two different
        files.  `segments_raw` says which one this was.

    Anything past the modelled end is re-emitted verbatim from `trailing`.
    """
    out = bytearray()
    nm = _enc_str(s.name, s.name_raw, "SHAP name", ShapeWriteError)
    out += _u32(len(nm), "SHAP nameLen", ShapeWriteError) + nm
    out += _u32(len(s.frames), "SHAP frameCount", ShapeWriteError)
    for i, pts in enumerate(s.frames):
        out += _u32(len(pts), f"SHAP frame {i} pointCount", ShapeWriteError)
        for p in pts:
            if len(p) != 3:
                raise ShapeWriteError(
                    f"SHAP frame {i}: point has {len(p)} components, not 3")
            out += struct.pack("<3f", *p)
    lb = _enc_str(s.label, s.label_raw, "SHAP label", ShapeWriteError)
    out += _u32(len(lb), "SHAP labelLen", ShapeWriteError) + lb
    seg = _prefer_raw(s.segments_raw, s.segments, lambda v: v or 1)
    out += _u32(s.segments if seg is None else seg, "SHAP segments",
                ShapeWriteError)
    out += s.trailing
    return bytes(out)


@dataclass
class SMotion:
    """A decoded ``SMOT`` chunk: one 4x4 per frame, nothing else."""
    matrices: list[Mat4]
    consumed: int = 0
    size: int = 0
    #: Bytes past ``4 + 64*n``, re-emitted verbatim by `serialize_smot`.
    trailing: bytes = b""

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def frame_count(self) -> int:
        return len(self.matrices)


def parse_smot(body: bytes) -> SMotion:
    """VERIFIED against graphic.dll!SMotion_Load (0x5CE30): ``u32 n; mat4x4[n]``."""
    (n,) = struct.unpack_from("<I", body, 0)
    mats = [tuple(struct.unpack_from("<16f", body, 4 + 64 * i)) for i in range(n)]
    return SMotion(mats, 4 + 64 * n, len(body), bytes(body[4 + 64 * n:]))


def serialize_smot(s: SMotion) -> bytes:
    r"""Encode an `SMotion` back to an ``SMOT`` chunk body::

        assert serialize_smot(parse_smot(body)) == body

    There is nothing lossy in this form -- ``u32 n`` then ``n`` 16-float
    matrices -- so the only ways to be wrong are the count and the trailing
    bytes, and both are asserted by the gate.
    """
    out = bytearray(_u32(len(s.matrices), "SMOT frameCount", SMotionWriteError))
    for i, m in enumerate(s.matrices):
        if len(m) != 16:
            raise SMotionWriteError(
                f"SMOT matrix {i} has {len(m)} floats, not 16")
        out += struct.pack("<16f", *m)
    out += s.trailing
    return bytes(out)


#: RMOT is SMOT's newer spelling and the SAME loader (see `parse_rmot`), so it
#: is the same writer.  The alias is deliberate: making them two functions
#: would let the identity drift without anything going red.
serialize_rmot = serialize_smot


# ---------------------------------------------------------------------------
# RIBB / RMOT -- the ribbon trail, and the newer spelling of SHAP : SMOT
#
# DECODED 2026-09-07 against the loader, not against the value distributions.
# The container dispatcher's RIBB arm and the function it calls:
#
#     7632 Env_DX9/graphic.dll  sha256 32a6348b..  dispatcher 0x1162, arm
#                               0x11C0 -> RIBB loader RVA 0xE2570
#     7867 Env_DX9/graphic.dll  sha256 acf7d864..  arm 0x1EE24
#                                     -> RIBB loader RVA 0x122D40
#     7878 Env_DX9/graphic.dll  sha256 1b4bc0fc..  dispatcher 0x1EDC2, arm
#                               0x1EE24 -> RIBB loader RVA 0x151FB0
#
# All three are the SAME 875-byte function.  7632 vs 7878: 856 bytes identical,
# 19 differences in 8 runs of 1-3 bytes.  7632 vs 7867: 849 identical, 26 in 15
# runs.  Every difference is a relocated immediate or a relative call
# displacement.  That matters because the shipped instances are on 7867 and
# 7878 while the readable dispatcher notes in this tree were taken from 7632 --
# the reading is not being transplanted across builds, it is the same code
# three times.
#
# The object the loader builds names itself.  Its vtable carries an RTTI
# complete-object locator, and the type descriptors read
#
#     .?AUC3RibbonTrail@@   :   .?AUC3BillboardChain@@
#
# which is Ogre3D's `RibbonTrail : BillboardChain` with a C3 prefix.  The
# setters line up with that API one for one and are quoted per field below.
# The shipped content agrees: all 140 instances (71 on 7878, 69 on 7867 --
# see the scoping correction in `core/coassets.py`) live under
# `c3/effect/**`, and 46 of 7878's 50 files sit in a directory named
# `*_tuowei*` (tuowei = trail) or `zuji_*` (footprint).  Nobody named those
# from a disassembly.
#
# THE BODY IS 124 BYTES AND THE LOADER READS ALL 124, in 17 calls through the
# stream reader at [0x101781C4], in this order (offsets are into the body;
# `esp-` names are the loader's own locals, kept so the read can be re-checked
# against the listing):
#
#     off   bytes  local     what the loader then does with it
#       0     4    E-0x6C    -> SetInitialWidth   (0xE22D0)  obj+0x194
#       4     4    E-0x70    -> SetWidthChange    (0xE2310)  obj+0x198
#       8     4    E-0x8C    OVERWRITTEN with 2, then vtbl+0x48 -> obj+4
#      12     4    E-0xA4    OVERWRITTEN with off16, then vtbl+0x84
#      16     4    E-0x94    -> vtbl+0x00 SetMaxChainElements    obj+8
#      20     4    E-0x9C    OVERWRITTEN with 10,000,000, then vtbl+0x88
#      24     4    E-0xAC    -> SetTrailLength    (0xE2140)  obj+0x160
#      28     4    E-0xA8    OVERWRITTEN with off24, then vtbl+0x18 -> obj+0x54
#      32     4    E-0xA0    OVERWRITTEN with off24, then vtbl+0x20 -> obj+0x58
#      36     4    E-0x98    OVERWRITTEN with -1.0f, then 0xE2320 -> obj+0x19C
#      40    16    E-0x88    x and z NEGATED, then 0xE2180 -> obj+0x14C..0x158
#      56    16    E-0x68    -> 0xE22A0  obj+0x174..0x180
#      72    16    E-0x48    -> 0xE22E0  obj+0x184..0x190
#      88     4    E-0x90    obj+0x120 = (off88 != 1); if off88 != 1, obj+0x1C8 = 0
#      92     4    E-0x74    -> obj+0x118   (written; NO reader in the binary)
#      96     4    E-0x78    THE GATE: != 1 and the loader returns false
#     100    24    E-0x28    -> obj+0x128..0x13C verbatim
#
# 10*4 + 3*16 + 3*4 + 24 = 124.  Nothing is left over and nothing is skipped,
# which is the structural check the value-distribution reading could not make.
#
# WHAT EACH SETTER IS.  `SetTrailLength` (0xE2140) is the decisive one::
#
#     0e2140  fld  [esp+4]            ; the value from off24
#     0e2144  mov  eax, [ecx+8]       ; obj+8, set from off16
#     0e2147  fst  [ecx+0x160]        ;   mTrailLength      = len
#     0e214d  fild [ecx+8]
#     0e215a  fdivp
#     0e2164  fst  [ecx+0x168]        ;   mElemLength       = len / maxElements
#     0e216a  fmul st(0), st(0)
#     0e216c  fstp [ecx+0x16c]        ;   mSquaredElemLength
#
# That is `RibbonTrail::setTrailLength` verbatim, and it PAIRS off24 with off16
# in code: one is a length, the other the count it is divided by.  vtbl+0x00
# (0xE2B30) is the matching `setMaxChainElements` -- it calls the base setter
# for obj+8 and then recomputes the same two derived fields.
#
# The per-frame update at 0xE3770 is where the colour and width pairs are
# consumed, and it fixes their SIGN convention::
#
#     0e39b9  fld  [edi+0xc]          ; element width
#     0e39bc  fld  [esi+0x198]        ; from off4
#     0e39c2  fmul st(2)              ; * dt
#     0e39c4  fsubp                   ; width -= change * dt   <- SUBTRACTED
#     0e39f6  fmul [esi+0x184]        ; from off72, likewise subtracted from
#     0e3a00  fld  [esi+0x188]        ; the element's colour, component by
#     0e3a0c  fld  [esi+0x18c]        ; component
#     0e3a18  fmul [esi+0x190]
#
# so off4 and off72..87 are RATES PER SECOND that are taken away, which is why
# the shipped files carry them positive (measured: 140/140 non-negative).
#
# obj+0x14C..0x158 (from off40..52) is consumed at 0xE41F1 in the same class::
#
#     0e41c6  call 0x100E4500                 ; now, in ms
#     0e41cb  sub  eax, [edi+0x110]           ; since this ribbon started
#     0e41e3  fdiv qword [0x101904A0]         ; / 1000.0  -> seconds
#     0e41f1  fld  [edi+0x14c] ... x4         ; each component * elapsed
#     0e4227  call 0x100E1AC0 x4              ; wrap into (-1.1, +1.1)
#     0e4252  call 0x10011F50(obj+0x4c, 1, &v); hand to the material handle
#
# A four-float per-second rate, wrapped to +-1.1 and pushed at a material, is a
# texture-coordinate scroll; the load-time negation of components 0 and 2 --
# the two U's of a (u,v,u,v) pair -- is the same convention flip C3 applies
# elsewhere.  MEASURED that it is a rate applied to elapsed seconds and wrapped;
# INFERRED that the four components are (u0,v0,u1,v1).  All 71 leave the last
# three at zero, so the data cannot separate the two orderings.
#
# WHAT STAYS UNSETTLED, stated as the negative it is:
#
#   * off92 -> obj+0x118 is WRITTEN AND NEVER READ.  A displacement scan over
#     the whole C3RibbonTrail/C3BillboardChain code (0xE1000..0xE4400) finds
#     exactly one instruction touching +0x118, the loader's own store.  Its
#     value is 1.0 on 140/140, so the data cannot say either.
#   * off88 -> obj+0x120, read at 0xE1A16 and 0xE3167, both behind
#     `if (obj+4 == 2 && obj+0x120)`.  It is a BOOLEAN (140/140 in {0,1}) that
#     gates an extra per-element step; what that step means is not settled.
#   * off100..123 -> obj+0x128..0x13C is written by the loader and copied by
#     the clone at 0x58A0, and NOTHING in graphic.dll reads it.  The 2x3 float
#     grouping is the DATA's claim, not the code's: the last read call passes
#     the factors 2 and 12, and the underlying reader multiplies them, so the
#     order (2 items of 12 bytes vs 12 of 2) is not recoverable statically.
#     What settles the grouping is that the second triple is the negation of
#     the first on 140/140 (to 1e-6; only 108 are bit-exact) -- see
#     `Ribbon.line`.
#
# RMOT, settled in the same pass.  Its arm is at 0x11EF and calls 0xE23F0::
#
#     0e23f5  operator new(0xC)               ; {u32 n; void* data; u32 _}
#     0e2418  Read(obj, 1, 4, f)              ; n
#     0e2425  mul edx=0x40                    ; n * 64, with an overflow guard
#     0e242f  malloc
#     0e2445  Read(buf, 0x40, n, f)           ; n matrices of 16 floats
#
# which is `u32 n; mat4x4[n]` from the code -- the same body `parse_smot`
# already consumed exactly on 71/71 of 7878's instances.  That reader was
# labelled "nothing dispatches to it for this tag yet"; something does.
# ---------------------------------------------------------------------------

#: The one value ``off96`` is allowed to hold.  The loader compares it against
#: 1 at 0xE2670 and returns false on any other value -- before the object is
#: allocated, so a wrong version is a load failure, not a default.
RIBB_VERSION = 1

#: The body is fixed-size: 124 bytes on all 140 instances, and the loader
#: reads exactly 124 with no length in the stream.
RIBB_BODY_SIZE = 124


@dataclass
class Ribbon:
    """A decoded ``RIBB`` chunk -- one ``C3RibbonTrail``.

    Field names follow the loader's own setters (and so Ogre's
    ``RibbonTrail``), not this project's guesses.  `discarded` carries the
    file values the loader reads and then throws away, because they are the
    evidence that it does: an install whose exporter starts varying one of
    them is worth seeing.
    """
    initial_width: float
    width_change: float
    max_elements: int
    trail_length: float
    tex_scroll: tuple[float, float, float, float]
    initial_colour: tuple[float, float, float, float]
    colour_change: tuple[float, float, float, float]
    flag: int
    version: int
    endpoints: list[tuple[float, float, float]]
    tex_scroll_raw: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    discarded: dict = field(default_factory=dict)
    consumed: int = 0
    size: int = 0

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def element_length(self) -> float:
        """``mElemLength``: what the loader computes at 0xE2164."""
        return self.trail_length / self.max_elements if self.max_elements else 0.0

    @property
    def line(self) -> list[tuple[float, float, float]]:
        """The source segment, named to match `Shape.line`.

        RIBB : RMOT is the newer spelling of SHAP : SMOT, and this is the same
        pair of endpoints `Shape.line` returns -- symmetric about the origin
        on all 140 instances (to 1e-6 relative; only 108 are bit-exact).
        """
        return self.endpoints


def parse_ribb(body: bytes) -> Ribbon:
    """Decode a ``RIBB`` chunk.  DECODED against the loader -- see above.

    Raises on a body that is not 124 bytes, and on ``version != 1``, because
    the engine refuses both: a reader that is more permissive than the client
    would report a file as readable that the client will not load.
    """
    if len(body) != RIBB_BODY_SIZE:
        raise ValueError(f"RIBB body is {len(body)} bytes, not "
                         f"{RIBB_BODY_SIZE}; the loader reads a fixed struct")
    f = struct.unpack_from("<31f", body, 0)
    u = struct.unpack_from("<31I", body, 0)
    version = u[24]
    if version != RIBB_VERSION:
        raise ValueError(f"RIBB version dword is {version}, not "
                         f"{RIBB_VERSION}; graphic.dll refuses this body "
                         f"(0xE2670 cmp / 0xE2676 xor al,al / ret false)")
    raw_scroll = (f[10], f[11], f[12], f[13])
    return Ribbon(
        initial_width=f[0],
        width_change=f[1],
        max_elements=u[4],
        trail_length=f[6],
        # the loader negates components 0 and 2 at 0xE26C3 / 0xE26CD
        tex_scroll=(-f[10], f[11], -f[12], f[13]),
        tex_scroll_raw=raw_scroll,
        initial_colour=(f[14], f[15], f[16], f[17]),
        colour_change=(f[18], f[19], f[20], f[21]),
        flag=u[22],
        version=version,
        endpoints=[(f[25], f[26], f[27]), (f[28], f[29], f[30])],
        discarded={
            # each of these is read and then overwritten before it is used
            "off8_chain_count": u[2],       # forced to 2
            "off12_capacity": u[3],         # forced to off16
            "off20": u[5],                  # forced to 10,000,000
            "off28": f[7],                  # forced to off24
            "off32": f[8],                  # forced to off24
            "off36": f[9],                  # forced to -1.0
            # stored to obj+0x118 and never read back
            "off92": f[23],
        },
        consumed=RIBB_BODY_SIZE,
        size=len(body),
    )


#: The `Ribbon.discarded` keys, in the on-disk order of the dwords they came
#: from.  `serialize_ribb` walks this rather than naming them inline, so a key
#: added to the parser and forgotten in the writer is a KeyError at the first
#: chunk, not a silently zeroed dword.
RIBB_DISCARDED_ORDER = ("off8_chain_count", "off12_capacity", "off20",
                        "off28", "off32", "off36", "off92")


def serialize_ribb(r: Ribbon) -> bytes:
    r"""Encode a `Ribbon` back to a ``RIBB`` chunk body::

        assert serialize_ribb(parse_ribb(body)) == body

    **The seven `discarded` dwords are what make this possible.**  The loader
    reads them and then overwrites each one before it is used (see the table
    above `parse_ribb`), so a writer that reconstructed the body from the
    *meaningful* fields alone would emit seven wrong dwords and still produce
    a chunk the client loads identically -- a round trip that fails on bytes
    while passing on behaviour.  `parse_ribb` keeps them precisely so the byte
    comparison stays available, and this writer puts every one of them back.

    `tex_scroll` is stored negated in components 0 and 2 (the loader negates
    them at 0xE26C3 / 0xE26CD) and is simply negated back -- see the note at
    the call site for why `tex_scroll_raw` is NOT consulted.

    The result is always exactly `RIBB_BODY_SIZE` bytes, and the function
    asserts that before returning: the loader reads a fixed 124-byte struct
    with no length in the stream, so a body of any other size is not a RIBB.
    """
    d = r.discarded
    missing = [k for k in RIBB_DISCARDED_ORDER if k not in d]
    if missing:
        raise RibbWriteError(
            f"Ribbon.discarded is missing {missing}. Those dwords are on disk "
            f"and the loader throws them away, so they cannot be re-derived "
            f"from the meaningful fields; parse_ribb fills them and a "
            f"hand-built Ribbon must too")
    if int(r.version) != RIBB_VERSION:
        raise RibbWriteError(
            f"RIBB version dword is {r.version}, not {RIBB_VERSION}; "
            f"graphic.dll refuses that body outright (0xE2670), so writing it "
            f"would produce a file the client will not load")
    if len(r.endpoints) != 2 or any(len(p) != 3 for p in r.endpoints):
        raise RibbWriteError(
            f"RIBB carries exactly two 3-float endpoints; got "
            f"{[len(p) for p in r.endpoints]}")
    # `tex_scroll` is the file's components 0 and 2 negated, and NEGATION IS AN
    # INVOLUTION -- measured over 0.0, -0.0, +/-inf, the smallest subnormal and
    # four NaN bit patterns, `struct.pack("<f", -(-x))` reproduced the input
    # dword every time.  So there is nothing to prefer here and no
    # `tex_scroll_raw` branch: re-deriving IS the inverse.
    #
    # This is not an aesthetic choice.  An earlier version did carry a
    # `_prefer_raw` branch for this field, and the ablation
    # (`scratchpad/fxwrite/ablate.py`) showed the mutant that removes it
    # SURVIVING the whole gate -- because the two arms compute the same bytes.
    # A branch no mutant can distinguish is a branch that cannot be shown
    # right, so it is gone rather than left as coverage nobody is holding.
    t = tuple(r.tex_scroll)
    scroll = (-t[0], t[1], -t[2], t[3])

    out = bytearray()
    out += struct.pack("<2f", r.initial_width, r.width_change)      # 0, 4
    out += _u32(d["off8_chain_count"], "RIBB off8", RibbWriteError)   # 8
    out += _u32(d["off12_capacity"], "RIBB off12", RibbWriteError)    # 12
    out += _u32(r.max_elements, "RIBB maxElements", RibbWriteError)   # 16
    out += _u32(d["off20"], "RIBB off20", RibbWriteError)             # 20
    out += struct.pack("<f", r.trail_length)                        # 24
    out += struct.pack("<3f", d["off28"], d["off32"], d["off36"])   # 28,32,36
    for nm, v in (("tex_scroll", scroll), ("initial_colour", r.initial_colour),
                  ("colour_change", r.colour_change)):
        if len(tuple(v)) != 4:
            raise RibbWriteError(f"RIBB {nm} has {len(tuple(v))} floats, not 4")
    out += struct.pack("<4f", *scroll)                              # 40
    out += struct.pack("<4f", *r.initial_colour)                    # 56
    out += struct.pack("<4f", *r.colour_change)                     # 72
    out += _u32(r.flag, "RIBB flag", RibbWriteError)                  # 88
    out += struct.pack("<f", d["off92"])                            # 92
    out += _u32(r.version, "RIBB version", RibbWriteError)            # 96
    out += struct.pack("<3f", *r.endpoints[0])                      # 100
    out += struct.pack("<3f", *r.endpoints[1])                      # 112
    if len(out) != RIBB_BODY_SIZE:
        raise RibbWriteError(
            f"internal: built {len(out)} bytes, not {RIBB_BODY_SIZE}")
    return bytes(out)


def parse_rmot(body: bytes) -> SMotion:
    """Decode an ``RMOT`` chunk: ``u32 n; mat4x4[n]``, the same as ``SMOT``.

    This is a one-line delegation on purpose.  It exists so the identity is
    an assertion something can fail rather than a sentence in a table: RMOT's
    own loader (7632 ``Env_DX9/graphic.dll`` 0xE23F0, reached from the
    dispatcher's RMOT arm at 0x11EF) allocates a 12-byte header, reads the
    count with ``Read(&n, 1, 4)``, multiplies by 0x40 under an overflow guard
    and reads ``Read(buf, 0x40, n)`` -- byte for byte what ``SMotion_Load``
    (0x5CE30) does, so the two readers are the same reader.
    """
    return parse_smot(body)


# ---------------------------------------------------------------------------
# CCFL -- the "ccflag" sidecar, and MNEW, the one-byte marker beside it
#
# Both tags appear for the first time on 7632 and are absent from every earlier
# install (4274, 5065, 5517, 6090, 6805).  The renderer that reads them is
# `Env_DX9/graphic.dll` (7632's is dated 2023-06-01), NOT the root
# `graphicDX9.dll` (2017), whose container dispatcher predates both tags.
#
# CCFL -- VERIFIED against 7632 `Env_DX9/graphic.dll`, three call sites
# ------------------------------------------------------------------------
# The container dispatcher at RVA 0x1150..0x1765 compares the tag one byte at a
# time -- which is why NO 4-byte literal for any C3 tag exists in the image, in
# either byte order (measured: 0 occurrences of `PHY4`/`CCFL`/`MNEW` in every
# renderer binary of 4274..7878 and CCO).  Its CCFL arm is at 0x13A0::
#
#     0013a0  cmp al, 0x43                  ; 'C'
#     0013a8  cmp byte ptr [esp+0x29], al   ; 'C'  (reuses al, hence no literal)
#     0013b2  cmp byte ptr [esp+0x2a], 0x46 ; 'F'
#     0013bd  cmp byte ptr [esp+0x2b], 0x4c ; 'L'
#     0013fb  call 0x100EFA20               ; the reader below
#     001417  mov dword ptr [ecx+0x1f4], edx; attach to the PRECEDING object
#
# and the reader at 0xEFA20 establishes every field of the layout::
#
#     0efacf  Read(dst, 6)                  ; the magic
#     0efad1  mov al, 0x63                  ; 'c'
#     0efad6..0efb0b  cmp 'c','c','f','l','a','g'   -- six byte compares
#     0efe20  on mismatch: Seek(-6, cur)    ; rewind, this was not a ccflag
#     0efb20  Read(dst, 8)                  ; u32 total; u32 count
#     0efb38  Read(dst, 4)                  ; the record header, ONE dword:
#     0efb4a  and ecx, 0xffff               ;   low  u16 = size, INCLUSIVE of
#     0efb50  shr eax, 0x10                 ;   high u16 = type   this header
#     0efb53  add ecx, -4                   ; payload = size - 4
#     0efdff  default: Seek(size-4, cur)    ; UNKNOWN TYPES ARE SKIPPED
#
# The engine's own default arm is why a reader needs no per-type knowledge to
# be exact: the record list is self-describing.  `parse_ccfl` therefore returns
# raw payloads and consumes the body exactly regardless of which types appear.
#
# MEASURED, full enumeration of the loose `.c3` trees (no sampling) --
# `scratchpad/mnew/final.py`, 2026-09-05:
#
#     install   CCFL bodies   parsed EXACTLY   refused
#     7632         66,897         66,897          0
#     7878        177,895        177,895          0
#
# The record types below carry MEASURED payload widths from those 244,792
# bodies and, where the disassembly names them, the read that fixes the width.
# **Type 8 is the one that makes this more than curve-fitting**: the C3_CORE
# dispatcher at 0x18061 reads it as two dwords, no instance of it exists on
# 7632, and 7878 -- a corpus not consulted when the code was read -- then
# produced 6 of them at exactly 8 bytes.
CCFL_MAGIC = b"ccflag"

class CcflScopeError(ValueError):
    """A CCFL type was looked up without saying which READER reads it.

    Raised rather than answered, because there is no global answer -- see
    `CCFL_READER_TYPES`. The engine does the same thing: every reader's
    default arm calls `Common_SeekRes` and drops the record rather than
    guessing a width.
    """


#: **(parent class, type) -> (payload width or None if variable, what the
#: loader does).**
#:
#: THIS USED TO BE A FLAT `dict[int, tuple]` AND THAT PREMISE WAS FALSE.
#: A `ccflag` type number is scoped to the READER, and the reader is chosen by
#: the PARENT CHUNK'S CLASS. Four separately compiled readers were enumerated
#: and their arm lists predict the observed parent-tag distribution of every
#: kind exactly -- data and code showing the same partition twice
#: (`docs/ccfl_kind_7_2026-09-16.md` §5.1).
#:
#: **Type 5 is the proof, and it is why a bare lookup now refuses**: the
#: PARTICLE reader's type 5 is **40 bytes** (four Reads -- 16+16+4+4 into
#: +0x30/+0x40/+0x50/+0x54, flag +0x2C, cleaned `add esp,0x40`), while the MESH
#: reader's type 5 is a variable 52-byte header then a counted list. Two
#: different fields sharing one number. The old flat table recorded ONE of
#: them, so for the other parent class it was simply wrong -- and a caller
#: could not have known which.
#:
#: This is the fourth time this project has met **one number naming two
#: things**: an RSDB id that is a row in eleven typed tables, an `effectId`
#: that is not a `textureId`, a CCFL type under two readers, and the
#: `MapObjIndex` binding that is not a sorted position.
#:
#: **MEASURED OFF THE CODE, so corpus occurrence is irrelevant to a width.**
#: Director of RE, 2026-09-16, `docs/ccfl_reader_type_map_2026-09-16.md` on
#: `director/re-ccfl-reader-map @ e64cf9e1`: read from the reader dispatch in
#: `7878/Env_DX9/graphic.dll` (image base 0x10000000) with `tools/disfn.py`.
#: Each arm is `cmp eax,<type>; jne next; (push <W>; ...; call ebp)xk;
#: add esp,N`, and the width is the `push <W>` before each `Read`. These are
#: what the client reads whether or not any shipped asset exercises the arm.
#:
#: Falsifier, re-runnable on any build (pin it by `version.dat`, never by
#: folder name)::
#:
#:     py -3 tools/disfn.py "<build>/Env_DX9/graphic.dll" 0x150580 0x1506E0
#:     py -3 tools/disfn.py "<build>/Env_DX9/graphic.dll" 0x15EFF0 0x15F380
#:
#: **TWO ARMS THE FLAT TABLE NEVER HAD**: mesh type 2 (4 bytes, 0x15F2BC) and
#: mesh type 15 (24 bytes = two 12-byte reads, 0x15F306). A table keyed the
#: wrong way does not merely mis-state what it holds; it hides what it lacks.
CCFL_READER_TYPES: dict[tuple, tuple] = {
    # -- Reader A, PARTICLE (parent PTC3), dispatch at graphic.dll 0x15053C --
    ("PTC3", 3):  (4,    "one dword -> +0x10: the ATLAS COLUMN COUNT (0x150581)"),
    ("PTC3", 4):  (16,   "four dwords -> +0x14..+0x20 (0x150599)"),
    ("PTC3", 5):  (40,   "16+16+4+4 -> +0x30/+0x40/+0x50/+0x54, flag +0x2C "
                         "(0x1505D1) -- NOT the mesh type 5"),
    ("PTC3", 6):  (4,    "dword -> +0x28, flag +0x24 (0x150646)"),
    ("PTC3", 7):  (4,    "one f32 -> +0x4 (0x15060E)"),
    ("PTC3", 8):  (8,    "two dwords -> +0x8,+0xC (0x150626)"),
    ("PTC3", 10): (16,   "four f32 -> +0x5C, flag +0x58 (0x15065F)"),
    ("PTC3", 11): (8,    "two dwords -> +0x6C,+0x70; sets +0x74 := 1.0f "
                         "(0x150678)"),
    ("PTC3", 14): (0,    "presence-only; sets a byte (0x15069D)"),
    ("PTC3", 19): (0,    "presence-only; 7878+, absent on 7632 (0x1506AB)"),
    # -- Reader B, MESH (parent PHY* / SHAP / RIBB), dispatch at 0x15EFF0 ----
    ("PHY",  1):  (None, "authoring string: malloc+Read+NUL (0x15F045)"),
    ("PHY",  2):  (4,    "one dword (0x15F2BC)"),
    ("PHY",  5):  (None, "52-byte header then a counted list, then 4,4 and an "
                         "f32 (0x15F095) -- NOT the particle type 5. LENGTH "
                         "IS GATED ON `mode` AT +0x2C: `cmp [ebx+0x2C],2` at "
                         "0x15F252 unlocks a 20-byte tail (+0x58..+0x68). The "
                         "corpus is mode==1 on 347/347, so the tail is unseen "
                         "-- do NOT collapse this to a constant width because "
                         "every shipped instance agrees (RE leg 2, PR #36)"),
    ("PHY",  6):  (4,    "one dword (0x15F07A)"),
    ("PHY",  9):  (8,    "two f32 -> parent+0x1CC on 7878 "
                         "(add eax,0x1CC at 0x15F2AA); +0x1C4 on 7632, "
                         "docs/ccfl_kind_9_2026-09-16.md §5.1 -- the "
                         "displacement MOVED between builds "
                         "(0x15F29E)"),
    ("PHY",  13): (4,    "int period ms -> obj+0x6C; 0x21 throughout the "
                         "corpus (0x15F2E3)"),
    ("PHY",  14): (0,    "presence-only (0x15F2F8)"),
    ("PHY",  15): (24,   "two 12-byte reads; 7878+ (0x15F306)"),
    ("PHY",  17): (0,    "arm present, body short; 7878+ (0x15F333)"),
    # -- Reader C, the third class, dispatch at 0x147B05 ---------------------
    ("C3",   1):  (None, "authoring string (0x147B54)"),
    ("C3",   6):  (4,    "one dword (0x147B7E)"),
}

#: Parent chunk tag -> reader class key in `CCFL_READER_TYPES`.
#:
#: `SHAP` is here on RE's reading that it is a mesh-class chunk, and it
#: carries the ONE loose end in the map: **type 18 follows `SHAP` on all five
#: instances and is in no reader's arm list**, so it reaches the mesh reader's
#: default arm and is skipped -- unless `SHAP` routes to a fifth reader nobody
#: has located. Recorded as dormant, not as decoded. The flat table used to
#: claim type 18 was "16, four dwords; no loader arm located", which read like
#: a decode and was a guess about a record the engine drops.
CCFL_PARENT_READER: dict[bytes, str] = {
    b"PTC3": "PTC3",
    b"PTCL": "PTC3",
    b"PHY ": "PHY", b"PHY2": "PHY", b"PHY3": "PHY", b"PHY4": "PHY",
    b"PHY5": "PHY",
    b"SHAP": "PHY", b"RIBB": "PHY",
}


def ccfl_reader(parent_tag) -> Optional[str]:
    """Reader class for a parent chunk tag, or `None` if it routes nowhere."""
    if parent_tag is None:
        return None
    if isinstance(parent_tag, str):
        parent_tag = parent_tag.encode("latin-1")
    return CCFL_PARENT_READER.get(bytes(parent_tag))


def ccfl_width(reader: Optional[str], typ: int):
    """`(width, note)` for one `(reader, type)`, or `None` if the arm is absent.

    **Refuses a bare-type lookup**, which is the whole of M12: there is no
    global width, and answering one is how the old flat table was wrong about
    type 5 for whichever parent class it did not describe. `None` here means
    the engine's default arm -- `Common_SeekRes`, rewind and drop -- not
    "width unknown, read it anyway".
    """
    if not reader:
        raise CcflScopeError(
            f"CCFL type {typ} has no width on its own: the type number is "
            f"scoped to the READER, which the parent chunk's class chooses. "
            f"Particle type 5 is 40 bytes and mesh type 5 is variable. Pass "
            f"the reader from `ccfl_reader(parent_tag)`.")
    return CCFL_READER_TYPES.get((reader, int(typ)))

#: MNEW's entire body: one byte, 0x70, with zero variance.
#:
#: CORRECTED 2026-09-07, and this line was wrong in a way worth keeping.  It
#: read "on 137,232 instances across 7632/7682/7867/7878" -- and **137,232 is
#: 7632 (39,907) plus 7878 (97,325)**, two installs, not four.  A real number
#: sitting beside an enumeration it does not match; each half looks fine and
#: only holding them together shows it.  `tests/test_claim_enumeration` checks
#: a stated total as the SUM of its per-install mapping for exactly this.
#:
#: The measured population, by a full 34-install census
#: (`scratchpad/c3census_all.py`, loose files PLUS every archive entry) is
#: **308,742 instances on 17 installs**, starting at 6907 with a single
#: instance -- not at 7632.  Those 39,907 / 97,325 are also loose-only counts;
#: the archive-inclusive figures for the same two installs are 45,549 and
#: 102,992.  See `docs/claim_enumeration_audit_2026-09-07.md`.
#:
#: The CONSTANT survived all of it: `parse_mnew` consumes 1,616 out-of-sample
#: bodies on 6907/6968/7009/7065/7083 exactly, 0 refused.  See `parse_mnew`.
MNEW_BODY = b"\x70"


@dataclass
class CCFlagRecord:
    """One record of a CCFL body.  `payload` is verbatim: the engine skips any
    type it does not know, so a reader that interpreted them would be claiming
    more than the loader does."""
    type: int
    payload: bytes

    def described_for(self, reader: Optional[str]) -> bool:
        """Is there a loader arm for this type UNDER THIS READER?"""
        return ccfl_width(reader, self.type) is not None

    @property
    def described(self) -> bool:
        """REFUSES, on purpose. A type number is not describable alone -- M12.

        This used to answer `self.type in CCFL_TYPES` off a flat global table,
        which is the question with no answer: particle type 5 is 40 bytes and
        mesh type 5 is variable. Use `described_for(reader)`, with the reader
        from `ccfl_reader(parent_tag)`.
        """
        raise CcflScopeError(
            f"CCFL type {self.type} is not `described` on its own: the type "
            f"is scoped to the reader that reads it. Use "
            f"`described_for(ccfl_reader(parent_tag))`.")

    @property
    def text(self) -> Optional[str]:
        """Type 1 only -- the authoring string, which is GBK on disk.

        MEASURED: 244,791 of the 244,792 bodies carry exactly one type-1
        record, and 100% of those match ``<text> CreateTime:YYYY/MM/DD
        HH:MM:SS`` (`scratchpad/mnew/an3.py`).  Decoded permissively because
        the engine only ever NUL-terminates it -- it never parses it.
        """
        if self.type != 1:
            return None
        return self.payload.decode("gbk", "replace")


@dataclass
class CCFlag:
    """A decoded ``CCFL`` chunk: a self-describing list of typed records."""
    records: list[CCFlagRecord] = field(default_factory=list)
    declared: int = 0          # the u32 at +6: bytes from +14 to the end
    count: int = 0             # the u32 at +10: number of records
    consumed: int = 0
    size: int = 0
    #: Which reader's arm list the widths were checked against, or `None` when
    #: the caller did not say.
    #:
    #: **A parse with `reader is None` checked NO widths**, and this field is
    #: the only way a caller tells that apart from a parse that checked and
    #: passed. Without it the two results are identical objects -- which is
    #: the shape `[[instrument-validity-rules]]` calls a control that cannot
    #: fire, and it would be this module's own version of the defect M12
    #: exists to remove.
    reader: Optional[str] = None

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def created(self) -> Optional[str]:
        """The `CreateTime:` stamp of the type-1 record, or None."""
        for r in self.records:
            t = r.text
            if t and " CreateTime:" in t:
                return t.split(" CreateTime:", 1)[1]
        return None

    @property
    def label(self) -> Optional[str]:
        """The type-1 string with the `CreateTime:` suffix removed."""
        for r in self.records:
            t = r.text
            if t is not None:
                return t.split(" CreateTime:", 1)[0]
        return None


def parse_ccfl(body: bytes, parent_tag=None) -> CCFlag:
    """Decode a ``CCFL`` chunk body.

    VERIFIED against 7632 `Env_DX9/graphic.dll` 0xEFA20 (see the block comment
    above) and consumed EXACTLY on 244,792 of 244,792 bodies across 7632 and
    7878.  Refuses rather than guessing: every framing field is checked against
    the body length, so a wrong stride cannot pass silently.

    SCOPE, 2026-09-07.  Those 244,792 are the LOOSE `.c3` of two installs;
    counting archive entries too, the same two hold 263,761, and **the corpus
    holds 541,869 `CCFL` on 13 installs, starting at 7083** -- not at 7632.
    Nine installs were never walked, and the loose-only corpus is about 12%
    short even on the two that were (`docs/claim_enumeration_audit_2026-09-07.md`).

    THE READER SURVIVED IT, which is the half that matters and the reason the
    correction is a scope note rather than a retraction: it consumes 14,196
    out-of-sample bodies exactly, 0 refused -- Zephyr 8,900,
    CCO-snapshot-2026-08-24 26, 7110 433, 7189 4,837.  Four installs the
    finding never touched, including two whole lineages.

    That figure read 8,926 for one commit, which is Zephyr plus CCO-snapshot
    alone: the run was still going and its first two lines were quoted as its
    total.  A PARTIAL RUN READS EXACTLY LIKE A FINISHED ONE -- the same shape
    as the enumeration defect this whole correction is about, committed while
    correcting it.  Read the run's own exit line, not the rows that have
    landed so far.
    """
    reader = ccfl_reader(parent_tag)
    if len(body) < 14 or body[:6] != CCFL_MAGIC:
        raise ValueError(f"not a ccflag block: {body[:6]!r}")
    declared, count = struct.unpack_from("<II", body, 6)
    if declared != len(body) - 14:
        raise ValueError(f"ccflag declares {declared} bytes, body has "
                         f"{len(body) - 14} after the header")
    off = 14
    recs: list[CCFlagRecord] = []
    for i in range(count):
        if off + 4 > len(body):
            raise ValueError(f"ccflag record {i} of {count}: header runs past "
                             f"the body ({off} + 4 > {len(body)})")
        size, typ = struct.unpack_from("<HH", body, off)
        if size < 4 or off + size > len(body):
            raise ValueError(f"ccflag record {i} type {typ}: size {size} is "
                             f"unusable at offset {off} of {len(body)}")
        # WIDTH-CHECKED ONLY WHEN THE CALLER SAYS WHICH READER READS THIS.
        # Without a parent there is no width to check against -- see
        # `ccfl_width` -- and checking against a guess is worse than not
        # checking, because both a refusal and a pass then read as knowledge.
        # `CCFlag.reader` records which it was.
        if reader:
            hit = CCFL_READER_TYPES.get((reader, typ))
            want = hit[0] if hit else None
            if want is not None and size - 4 != want:
                raise ValueError(f"ccflag record {i} type {typ} under the "
                                 f"{reader} reader: payload {size - 4} bytes, "
                                 f"the loader reads {want}")
        recs.append(CCFlagRecord(typ, body[off + 4:off + size]))
        off += size
    if off != len(body):
        raise ValueError(f"ccflag: {len(body) - off} bytes left after "
                         f"{count} records")
    return CCFlag(recs, declared, count, off, len(body), reader)


# ---------------------------------------------------------------------------
# CAME -- the authoring camera, and the one chunk in this family that is being
# RETIRED rather than introduced.
#
# Its census shape is the opposite of MNEW's and CCFL's, and that is what
# located the reader.  MNEW/CCFL appear first at 7632; CAME is at its MAXIMUM
# on the OLDEST install and decays:
#
#     4274   5065   5517   6090   6805   7632
#    4,727  1,758  1,874  2,236  2,449    891
#
# So the reader is in the OLD renderers, and it was REMOVED from the new ones.
# MEASURED, by the byte-compare cluster scan (`scratchpad/came/whodispatches.py`,
# which is `scratchpad/mnew/tagdisc.py` over every renderer in the corpus):
# **only `4274/C3_CORE_DLL.dll` and `5065/C3_CORE_DLL.dll` dispatch CAME.**
# From 5165 onward -- through 5517, 6090, 6609, 7205, 7632, 7878, Zephyr, and
# the C3 Toolkit's own `graphic.dll` -- no binary tests for the tag, and the
# chunk takes the dispatcher's default seek-past arm.  The negatives are
# usable because each carries a positive control in the same scan: every one
# of those binaries still reports the tags it IS known to dispatch (PHY, MOTI,
# SHAP, PTCL, SMOT, STEP, 2SID).  Rows whose control came back empty --
# `GraphicData.dll`, `Role3D.dll` -- prove nothing and are not counted.
#
# THE CHUNKS KEPT SHIPPING FOR TWELVE YEARS AFTER THE LAST READER WAS DELETED.
# 891 of them are still in 7632.  Nothing reads them.  That is the finding.
#
# The reader, `4274/C3_CORE_DLL.dll` RVA 0x1141 (identical at 0x1141 in 5065),
# reached from the byte-at-a-time dispatcher at 0x10EC.  `ebp` is
# `Read(buf,size,count,file)`, `ebx` is `Seek(file,offset,whence)`::
#
#     001141  push 0x20 ; call malloc            ; the object is 32 bytes
#     00114f  call 0x10001000                    ; its constructor -- see below
#     00115e  Read(&nameLen, 4, 1, file)
#     001166  malloc(nameLen + 1)                ; +1 for a NUL the FILE LACKS
#     00117c  Read(name, 1, nameLen, file)
#     00118e  name[nameLen] = 0                  ; ...which it writes itself
#     001192  Seek(file, 4, SEEK_CUR)            ; <-- SKIPS one float
#     00119f  Read(&obj->0x18, 4, 1, file)       ; frame count
#     0011b0  malloc(frameCount * 12) -> obj+0x04
#     0011d7  Read(obj->0x04, 12, frameCount, file)   ; ONE contiguous block
#     0011e5  malloc(frameCount * 12) -> obj+0x08
#     00120c  Read(obj->0x08, 12, frameCount, file)   ; a SECOND block
#
# Two things fall straight out of that listing.  The `malloc(nameLen + 1)`
# with the explicit `name[nameLen] = 0` says the name is **not** NUL-terminated
# on disk.  And the two arrays are read as two separate contiguous blocks, so
# the layout is **BLOCKED, NOT INTERLEAVED** -- a reader that walked 24-byte
# records would mix the two tracks together and never notice.
#
# The constructor at 0x10001000 is the Rosetta the C3Tools `Camera.ini` files
# were supposed to be (all five of them are ZERO BYTES; that route is dead).
# It names the class's own fields by their defaults::
#
#     [obj+0x00] = 0            char*  name
#     [obj+0x04] = 0            vec3*  eye track
#     [obj+0x08] = 0            vec3*  target track
#     [obj+0x0c] = 0x41200000   float  10.0      -- near plane
#     [obj+0x10] = 0x461C4000   float  10000.0   -- far plane
#     [obj+0x14] = 0x3F490FDB   float  0.7853982 -- FOV, and that is pi/4
#                                                   BIT-EXACTLY
#     [obj+0x18] = 0            u32    frame count
#
# Near and far are never in the file -- there is only one float on disk, and
# both readers SEEK PAST IT.  So no renderer in this corpus has ever loaded
# the FOV, and its identity is INFERRED, not read.  What makes the inference
# safe is that it lands on two quantities nobody here chose (`scratchpad/came/
# an5.py`): the commonest on-disk value, 11,435 of 13,935 bodies, is 0.602416
# rad, which is 3ds Max's stock 45-degree HORIZONTAL camera converted to
# vertical at 4:3 -- agreeing to **0.00002 degrees**; the second commonest,
# 1,662 bodies, is pi/4 to 0.00001 degrees, which is this constructor's own
# default written back out.  All 13,935 values lie inside 5.6-141.7 degrees.
# A field that were anything else would not do that.
# ---------------------------------------------------------------------------


@dataclass
class Camera:
    """A decoded ``CAME`` chunk: an authoring camera, baked per frame.

    `eye` and `target` are both `frame_count` long -- the chunk stores no
    interpolation, just one sample per animation frame.

    MEASURED over 13,935 instances in 4274/5065/5517/6090/6805/7632 -- SIX
    installs of 34, and the corpus holds 79,522 `CAME` on ALL 34 (corrected
    2026-09-07, docs/claim_enumeration_audit_2026-09-07.md; the word 'all'
    used to sit in front of the 13,935).  The READING held out of sample:
    `parse_came` consumes 17,860 further bodies exactly, 0 refused, on
    5017/5165/6609/6907/7205/Zephyr/CCO-snapshot-2026-08-24 -- more bodies
    than the sample it was derived on.  The DISTRIBUTIONAL claims below
    are still the six installs' and are not re-measured
    (`scratchpad/came/an3.py`): **the eye track is constant in 13,935 of
    13,935**, and the target track varies in only 42.  Those 42 pan smoothly
    and linearly.  So in practice this is a fixed camera with an occasional
    sweeping look-at, and a consumer that treats `eye` as a single point is
    right about the whole corpus -- but the file can say otherwise, so the
    track is kept.
    """
    name: str
    fov: float
    eye: list[tuple[float, float, float]] = field(default_factory=list)
    target: list[tuple[float, float, float]] = field(default_factory=list)
    consumed: int = 0
    size: int = 0

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def frame_count(self) -> int:
        return len(self.eye)

    @property
    def static(self) -> bool:
        """True when neither track moves -- 13,893 of 13,935 bodies."""
        return len(set(self.eye)) <= 1 and len(set(self.target)) <= 1

    @property
    def fov_degrees(self) -> float:
        return math.degrees(self.fov)


def parse_came(body: bytes) -> Camera:
    r"""Decode a ``CAME`` chunk body.

    VERIFIED against `4274/C3_CORE_DLL.dll` RVA 0x1141 (see the block comment
    above) and consumed EXACTLY on **13,935 of 13,935** bodies -- a full
    enumeration of 4274, 5065, 5517, 6090, 6805 and 7632, loose files and
    archive entries alike.  Those six counts reproduce the census in
    `core/coassets.py` exactly, which is what says the enumeration is whole::

        u32   nameLen
        char  name[nameLen]         # NOT NUL-terminated; the loader adds one
        f32   fov                   # radians; both readers SEEK PAST IT
        u32   frameCount
        f32   eye[frameCount][3]    # one contiguous block
        f32   target[frameCount][3] # a second contiguous block

    `frameCount` is the ANIMATION frame count, not a keyframe count: it equals
    the `frame_count` of a ``MOTI`` in the same container in **8,630 of 8,630**
    containers that hold both, with zero disagreements (`scratchpad/came/
    an5.py`).

    Which array is which was settled without using any camera field, against
    the geometry in the same container (`scratchpad/came/an4.py`): over 8,630
    camera/mesh pairs the SECOND array is nearer the mesh centre in 99.5% of
    them (median 0.93 mesh-diagonals against the first array's 2.37), and sits
    inside the mesh bounding box 16.8% of the time against 0.9%.  That is a
    camera looking at its subject from outside it, and it fixes the order.

    Refuses rather than guessing: every framing field is checked against the
    body length and the body must be consumed exactly, so a wrong stride
    cannot pass silently.
    """
    if len(body) < 12:
        raise ValueError(f"CAME body is {len(body)} bytes, too short for a header")
    (nl,) = struct.unpack_from("<I", body, 0)
    if nl > len(body) - 12:
        raise ValueError(f"CAME name length {nl} does not fit in a "
                         f"{len(body)}-byte body")
    name = bytes(body[4:4 + nl])
    off = 4 + nl
    fov, nframes = struct.unpack_from("<fI", body, off)
    off += 8
    want = off + 24 * nframes
    if want != len(body):
        raise ValueError(f"CAME declares {nframes} frames: needs {want} bytes, "
                         f"body has {len(body)} (nameLen={nl})")
    eye = [struct.unpack_from("<3f", body, off + 12 * i) for i in range(nframes)]
    off += 12 * nframes
    target = [struct.unpack_from("<3f", body, off + 12 * i) for i in range(nframes)]
    off += 12 * nframes
    return Camera(name.decode(ENCODING, "replace"), fov, eye, target,
                  off, len(body))


def parse_mnew(body: bytes) -> bytes:
    """Decode an ``MNEW`` chunk body -- which carries no information.

    **This is a negative result, stated as one.**  MEASURED by full
    enumeration of the loose `.c3` trees: 39,907 instances on 7632 and 97,325
    on 7878 (plus 7682 and 7867), and **every one is a single byte 0x70**.
    Zero variance means the data cannot say what the byte means.

    Nor can the code: MNEW is NOT in the tag vocabulary of the container
    dispatcher in either 7632 or 7878 `Env_DX9/graphic.dll`.  The same scan
    over the same function finds all twelve other tags it dispatches -- RIBB,
    RMOT, PHY /2/3/4/5, CCFL, MOTI, SHAP, PTCL/PTCX/PTC3, SMOT -- so the
    absence is a reading of the switch, not a failure to look.  MNEW falls to
    the default arm at RVA 0x173A, which is `Seek(chunkLength, cur)`: the
    renderer skips it.

    What IS established is its position: MNEW immediately follows a ``MOTI``
    in 11,267 of 11,267 sampled instances, never anything else.

    Returns the body verbatim.  Raises if it is not the constant, so that an
    install which finally varies the byte SHOWS UP instead of being absorbed.
    """
    if body != MNEW_BODY:
        raise ValueError(f"MNEW body is {body!r}, not the constant "
                         f"{MNEW_BODY!r} measured on 308,742 instances "
                         f"across 17 installs")
    return body


# ---------------------------------------------------------------------------
# PTCL / PTCX / PTC3 -- the particle systems
#
# Three generations, three loaders, one dispatcher.  ``sub_1A1E0`` (the C3
# container walker that also drives Phy_Load / Motion_Load / Shape_Load /
# SMotion_Load) compares the four tag bytes and hands the *open stream* to one
# of the overloads -- so a reader has to consume exactly the chunk length or
# every later chunk is misread:
#
#     'P','T','C','L' -> Ptcl_Load(C3Ptcl**,  void*)   RVA 0x61150   (0x1A567)
#     'P','T','C','X' -> Ptcl_Load(C3Ptcl2**, void*)   RVA 0x60A50   (0x1A5C3)
#     'P','T','C','3' -> Ptcl_Load(C3Ptcl3**, void*)   RVA 0x60CE0   (0x1A623)
#
# RVAs are into the 509-export ``bin/64/graphic.dll`` -- which is **AMD64**
# (COFF machine 0x8664), not i386; see docs/CORRECTIONS.md C33.  Every offset
# named below is a byte offset into the C3Ptcl* object as the loader writes it,
# quoted so the read can be re-checked against the listing.
# ---------------------------------------------------------------------------

#: Chunk tag -> particle generation.  ``PTCX`` is the middle generation; it is
#: named nowhere in the docs because this install ships none, but the
#: dispatcher tests for it (0x1A58C ``cmp al, 0x58``) so a reader that omits it
#: would silently mis-walk any file that has one.
PTCL_TAGS: dict[bytes, int] = {b"PTCL": 1, b"PTCX": 2, b"PTC3": 3}

#: The engine's own particle-system caps, read out of the loader/draw path.
PTCL_VERTEX_STRIDE = 20        # 3 floats position + 2 floats UV (Ptcl_Draw)
PTCL3_VERTEX_STRIDE = 24       # C3Ptcl3 allocates 0x18 per vertex (0x60F84)
PTCL_VERTS_PER_PARTICLE = 4    # ``shl ecx, 2`` before the size multiply


@dataclass
class ParticleFrame:
    """One frame of a baked particle simulation.

    The engine does not *simulate*: `Ptcl_Draw` indexes this array with
    ``ptcl->currentFrame`` (``movsxd rax,[rcx+0x20]``; stride 0x60 for PTCL,
    0x68 for PTCX/PTC3) and emits one camera-facing quad per live particle.
    Everything below is therefore per-particle-per-frame, already solved.
    """
    count: int
    ids: tuple[int, ...] = ()            # PTCX/PTC3 only -- see `ids` note below
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    cells: tuple[float, ...] = ()        # flipbook phase, [0,1)
    sizes: tuple[float, ...] = ()        # half-extent, pre world scale
    matrix: Mat4 = IDENTITY              # premultiplied onto the world matrix


@dataclass
class ParticleEnvelope:
    """The `PTC3`-only header block.  Fields named from `Ptcl_Draw(C3Ptcl3*)`
    (RVA 0x5F110); offsets are into the C3Ptcl3 object."""
    billboard: int = 0          # +0x30, one byte; 100 is subtracted if >= 100
    #: The byte as it sits on disk, before the ``- 100``.  That subtraction is
    #: not injective -- billboard 5 is both an on-disk 5 and an on-disk 105 --
    #: so `serialize_ptcl` needs the original.  See `_prefer_raw`.
    billboard_raw: Optional[int] = None
    world_space: int = 0        # +0x34, one byte; non-zero skips the transform
    roll: tuple[float, float] = (0.0, 0.0)              # +0x3C, +0x40
    alpha: tuple[float, float, float] = (1.0, 1.0, 1.0)  # +0x44, +0x48, +0x4C
    fade_frames: tuple[int, int] = (0, 0)                # +0x50, +0x54
    particle_alpha: tuple[float, float, float] = (1.0, 1.0, 1.0)  # +0x58/5C/60
    particle_life: tuple[float, float] = (0.0, 0.0)      # +0x64, +0x68


@dataclass
class Particle:
    """A decoded ``PTCL`` / ``PTCX`` / ``PTC3`` chunk."""
    generation: int             # 1, 2 or 3
    tag: str = ""
    name: str = ""
    label: str = ""             # the 3DSMax source path; the loader Seeks past it
    tex_grid: int = 1           # +0x14: the texture is a tex_grid x tex_grid atlas
    max_particles: int = 0      # +0x10: the vertex buffer is sized off this
    frames: list[ParticleFrame] = field(default_factory=list)
    envelope: Optional[ParticleEnvelope] = None
    stretch: Optional[float] = None    # PTC3 only, and it comes from `name`
    consumed: int = 0
    size: int = 0
    #: The on-disk forms, for the same reason `Shape` carries them: `name` and
    #: `label` are `gbk`-decoded with ``errors="replace"``, and `tex_grid` has
    #: had an on-disk 0 normalised to 1.  `serialize_ptcl` prefers these while
    #: the decoded view still agrees with them.
    name_raw: bytes = b""
    label_raw: bytes = b""
    tex_grid_raw: Optional[int] = None
    #: Bytes past the modelled end, re-emitted verbatim.
    trailing: bytes = b""

    @property
    def exact(self) -> bool:
        return self.consumed == self.size

    @property
    def frame_count(self) -> int:
        """+0x24 -- the modulus in Ptcl_NextFrame/Ptcl_SetFrame, exactly as
        `motion->frameCount` is for a PHY."""
        return len(self.frames)

    @property
    def peak_particles(self) -> int:
        return max([f.count for f in self.frames] or [0])

    @property
    def effective_frames(self) -> int:
        """Playable length: one past the last frame that has any particle.

        The same reasoning as the PHY alpha envelope (docs/effects.md §6.5)
        applied to the channel particles actually have.  A particle system
        habitually declares 101 frames and empties after a dozen -- 976 of
        patch5517's 1,259 chunks carry an empty tail, 39,451 dead frames in
        total -- and an empty frame draws nothing, exactly as alpha 0 does.
        Unlike the alpha rule this one is not inferred: `Ptcl_Draw` returns
        immediately when ``frame->count == 0`` (RVA 0x605CF).
        """
        last = -1
        for i, f in enumerate(self.frames):
            if f.count:
                last = i
        return last + 1

    def cell(self, phase: float, cols: Optional[int] = None) -> tuple:
        """The flipbook cell a stored phase selects. **The atlas may not be
        square.**

        `Ptcl_Draw` 0x607D0-0x607F6 with no override: ``i = int(phase * N*N)``
        then ``col = i % N, row = i // N``, UV offset ``(col/N, row/N)``, cell
        size ``1/N``. Same convention as the PHY ChangeTex channel
        (docs/effects.md §6.4).

        **`cols` IS CCFL KIND 3, AND WITHOUT IT 75 FILES INDEX THE WRONG
        CELL.** The record is a COLUMN-COUNT override: ``totalCells = K*N``,
        the modulus becomes ``K``, the u-axis cell width becomes ``1/K``, and
        **v keeps ``1/N``**. Decoded on three separately compiled
        implementations -- 7878 and 7632 `Env_DX9/graphic.dll` and 7632
        `Env_DX8/C3_CORE_DLL.dll` -- agreeing field for field across two
        object layouts (`docs/ccfl_kind_3_2026-09-16.md`), and the arm exists
        only in the PARTICLE reader (graphic.dll 0x150581, one dword into
        +0x10; `CCFL_READER_TYPES`).

        MEASURED over the whole of RSDB section 1 on 7878: **225 entries in 75
        files, values 4x196 / 8x13 / 3x12 / 2x4, never 0 and never 1, and
        differing from the file's own `texGrid` on 224 of 225.** Zero-
        initialised in the engine, so an absent override is 0 and `None`/0
        here means "square", which must reproduce the old numbers exactly --
        the cheapest regression check this change has.

        Returns ``(col, row)``. **The caller needs BOTH counts**: `u` steps by
        ``1/cols`` and `v` by ``1/texGrid``, and a caller that uses one number
        for both is the defect this signature exists to prevent.
        """
        n = self.tex_grid or 1
        k = int(cols) if cols else n
        k = max(1, k)
        i = int(phase * k * n)
        return (i % k, i // k)

    def system_alpha(self, frame: int) -> float:
        """The PTC3 whole-system alpha envelope, RVA 0x5F5BA-0x5F648.

        Three levels and two frame thresholds: ramp to `alpha[1]` by frame
        `fade_frames[0]`, hold, then ramp to `alpha[2]` over the remainder.
        Clamped to [0,1].  Returns 1.0 for PTCL/PTCX, which have no envelope.
        """
        e = self.envelope
        if e is None:
            return 1.0
        a0, a1, a2 = e.alpha
        f0, f1 = e.fade_frames
        total = self.frame_count
        if f0 and frame < f0:
            t = frame / f0
            v = a0 * (1.0 - t) + a1 * t
        elif frame < f1:
            v = a1
        elif total > f1:
            t = (frame - f1) / (total - f1)
            v = a1 * (1.0 - t) + a2 * t
        else:
            v = a2
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


_STRETCH_TOKEN = "_C3EXP_STRETCH="


def parse_ptcl(body: bytes, generation: int, tag: str = "") -> Particle:
    r"""Decode a particle chunk.  VERIFIED against the three `Ptcl_Load`
    overloads in ``bin/64/graphic.dll``.

    All three share one shape.  ``TQFRead(dst, elemSize, count, file)`` and
    ``TqFSeek(file, n, SEEK_CUR)`` are the only stream calls, so the byte
    sequence is unambiguous::

        u32 nameLen  ; char[nameLen] name    # gen 1/2 Seek past it; gen 3 KEEPS it
        u32 labelLen ; char[labelLen] label  # all three Seek past it
        u32 texGrid                          # -> +0x14
        --- PTC3 only ------------------------------------------------------
        u8   billboard                       # -> +0x30 (minus 100 if >= 100)
        u8   worldSpace                      # -> +0x34
        f32  roll[2] ; f32 alpha[3] ; u32 fadeFrame[2]
        f32  particleAlpha[3] ; f32 particleLife[2]     # 12 dwords, +0x3C..+0x68
        --------------------------------------------------------------------
        u32 maxParticles                     # -> +0x10
        u32 frameCount                       # -> +0x24
        frameCount x {
            u32 count                        # -> frame+0x00
            if count:                        # count == 0 reads NOTHING further
                u16   ids[count]             # PTCX/PTC3 only
                f32x3 position[count]
                f32   cellPhase[count]
                f32   size[count]
                f32   matrix[16]             # a D3DXMATRIX, 0x40 bytes
        }

    **The zero-count branch is the whole difficulty.** At 0x612B9 / 0x60BB5 /
    0x61016 a count of 0 jumps straight to the null-pointer tail: no arrays and
    **no matrix**.  Reading the 0x40 matrix unconditionally desynchronises the
    stream, and 54,713 of patch5517's 115,561 particle frames are empty (47%)
    -- so a reader that gets it wrong fails on most files, not a few.

    ``generation`` is 1 (`PTCL`), 2 (`PTCX`) or 3 (`PTC3`); use `PTCL_TAGS`.
    """
    if generation not in (1, 2, 3):
        raise ValueError(f"unknown particle generation {generation}")
    off = 0
    (n,) = struct.unpack_from("<I", body, off); off += 4
    raw_name = bytes(body[off:off + n]); off += n
    (n2,) = struct.unpack_from("<I", body, off); off += 4
    label = bytes(body[off:off + n2]); off += n2
    (grid,) = struct.unpack_from("<I", body, off); off += 4

    env = None
    if generation == 3:
        env = ParticleEnvelope()
        billboard = body[off]; off += 1
        env.billboard_raw = billboard
        env.billboard = billboard - 100 if billboard >= 100 else billboard
        env.world_space = body[off]; off += 1
        fl = struct.unpack_from("<12f", body, off)
        ui = struct.unpack_from("<12I", body, off)
        off += 48
        env.roll = (fl[0], fl[1])
        env.alpha = (fl[2], fl[3], fl[4])
        env.fade_frames = (ui[5], ui[6])
        env.particle_alpha = (fl[7], fl[8], fl[9])
        env.particle_life = (fl[10], fl[11])

    (max_particles,) = struct.unpack_from("<I", body, off); off += 4
    (frame_count,) = struct.unpack_from("<I", body, off); off += 4

    frames: list[ParticleFrame] = []
    for _ in range(frame_count):
        (count,) = struct.unpack_from("<I", body, off); off += 4
        if not count:
            frames.append(ParticleFrame(0))
            continue
        ids: tuple[int, ...] = ()
        if generation in (2, 3):
            ids = struct.unpack_from(f"<{count}H", body, off); off += 2 * count
        flat = struct.unpack_from(f"<{3 * count}f", body, off); off += 12 * count
        positions = [(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2])
                     for i in range(count)]
        cells = struct.unpack_from(f"<{count}f", body, off); off += 4 * count
        sizes = struct.unpack_from(f"<{count}f", body, off); off += 4 * count
        matrix = tuple(struct.unpack_from("<16f", body, off)); off += 64
        frames.append(ParticleFrame(count, ids, positions, cells, sizes, matrix))

    name = raw_name.decode(ENCODING, "replace")
    stretch = None
    if generation == 3 and _STRETCH_TOKEN in name:
        # Ptcl_Load(C3Ptcl3**) 0x60D75: strstr(name, "_C3EXP_STRETCH="), then
        # atof(match + 15) -> +0x38.  The authoring tool smuggles a render
        # parameter through the object name; nothing else in the chunk carries
        # it, and this install ships no instance of it.
        try:
            stretch = float(name.split(_STRETCH_TOKEN, 1)[1].split()[0])
        except (ValueError, IndexError):
            stretch = None
    return Particle(generation, tag or "", name,
                    label.decode(ENCODING, "replace"),
                    grid or 1, max_particles, frames, env, stretch,
                    off, len(body),
                    name_raw=raw_name, label_raw=label, tex_grid_raw=grid,
                    trailing=bytes(body[off:]))


def serialize_ptcl(p: Particle) -> bytes:
    r"""Encode a `Particle` back to a ``PTCL`` / ``PTCX`` / ``PTC3`` chunk
    body -- `parse_ptcl`'s inverse::

        from effects import parse_ptcl, serialize_ptcl, PTCL_TAGS
        assert serialize_ptcl(parse_ptcl(body, PTCL_TAGS[tag])) == body

    `Particle.generation` selects the layout, exactly as it does on the read
    side: the `ids` array is written for generations 2 and 3 only, and the
    50-byte envelope for generation 3 only.

    **The zero-count branch is the whole difficulty here too.**  A frame whose
    ``count`` is 0 carries no arrays *and no matrix* -- the loader jumps past
    both (0x612B9 / 0x60BB5 / 0x61016) -- and 47% of one install's particle
    frames are empty, so a writer that emitted the 0x40 matrix anyway would be
    wrong on most files rather than a few.  `ParticleFrame.count` is the field
    that decides, not ``len(positions)``, and a frame whose arrays disagree
    with its own count is REFUSED rather than trimmed: silently writing the
    shorter of the two would drop particles and still round-trip its own
    output.

    `stretch` is NOT written.  It is not a field: `Ptcl_Load(C3Ptcl3**)` at
    0x60D75 parses it back out of the object *name* with ``strstr``, so it is
    already in the bytes `name` produces, and writing it again would duplicate
    it.
    """
    gen = int(p.generation)
    if gen not in (1, 2, 3):
        raise PtclWriteError(f"unknown particle generation {gen}")
    out = bytearray()
    nm = _enc_str(p.name, p.name_raw, "particle name", PtclWriteError)
    out += _u32(len(nm), "particle nameLen", PtclWriteError) + nm
    lb = _enc_str(p.label, p.label_raw, "particle label", PtclWriteError)
    out += _u32(len(lb), "particle labelLen", PtclWriteError) + lb
    grid = _prefer_raw(p.tex_grid_raw, p.tex_grid, lambda v: v or 1)
    out += _u32(p.tex_grid if grid is None else grid, "particle texGrid",
                PtclWriteError)

    if gen == 3:
        e = p.envelope
        if e is None:
            raise PtclWriteError(
                "PTC3 carries a 50-byte envelope and this Particle has none; "
                "parse_ptcl always fills it for generation 3")
        bb = _prefer_raw(e.billboard_raw, e.billboard,
                         lambda v: v - 100 if v >= 100 else v)
        bb = e.billboard if bb is None else bb
        for nmm, v in (("billboard", bb), ("world_space", e.world_space)):
            if not 0 <= int(v) <= 0xFF:
                raise PtclWriteError(
                    f"PTC3 {nmm} is one byte on disk; {v} does not fit")
        out += bytes((int(bb), int(e.world_space)))
        if len(e.roll) != 2 or len(e.alpha) != 3 or len(e.fade_frames) != 2 \
                or len(e.particle_alpha) != 3 or len(e.particle_life) != 2:
            raise PtclWriteError("PTC3 envelope tuple has the wrong arity")
        out += struct.pack("<2f", *e.roll)
        out += struct.pack("<3f", *e.alpha)
        out += struct.pack("<2I", *(int(v) & 0xFFFFFFFF for v in e.fade_frames))
        out += struct.pack("<3f", *e.particle_alpha)
        out += struct.pack("<2f", *e.particle_life)
    elif p.envelope is not None:
        raise PtclWriteError(
            f"generation {gen} has no envelope on disk, but this Particle "
            f"carries one; writing it would desynchronise the stream")

    out += _u32(p.max_particles, "particle maxParticles", PtclWriteError)
    out += _u32(len(p.frames), "particle frameCount", PtclWriteError)
    for i, f in enumerate(p.frames):
        cnt = int(f.count)
        out += _u32(cnt, f"particle frame {i} count", PtclWriteError)
        if not cnt:
            # THE BRANCH: no arrays, no matrix.  Anything the frame is still
            # carrying would be dropped, so say so instead of dropping it.
            if f.positions or f.cells or f.sizes or f.ids:
                raise PtclWriteError(
                    f"particle frame {i}: count is 0 but the frame carries "
                    f"{len(f.positions)} position(s); the engine reads "
                    f"nothing further for an empty frame, so those would be "
                    f"silently discarded")
            continue
        want_ids = cnt if gen in (2, 3) else 0
        for nmm, seq, need in (("ids", f.ids, want_ids),
                               ("positions", f.positions, cnt),
                               ("cells", f.cells, cnt),
                               ("sizes", f.sizes, cnt)):
            if len(seq) != need:
                raise PtclWriteError(
                    f"particle frame {i}: {nmm} has {len(seq)} entries, "
                    f"count says {need} (generation {gen})")
        if want_ids:
            for v in f.ids:
                if not 0 <= int(v) <= 0xFFFF:
                    raise PtclWriteError(
                        f"particle frame {i}: id {v} is not a u16")
            out += struct.pack(f"<{cnt}H", *(int(v) for v in f.ids))
        for j, pos in enumerate(f.positions):
            if len(pos) != 3:
                raise PtclWriteError(
                    f"particle frame {i} position {j} has {len(pos)} "
                    f"components, not 3")
        out += struct.pack(f"<{3 * cnt}f",
                           *(c for pos in f.positions for c in pos))
        out += struct.pack(f"<{cnt}f", *f.cells)
        out += struct.pack(f"<{cnt}f", *f.sizes)
        if len(f.matrix) != 16:
            raise PtclWriteError(
                f"particle frame {i} matrix has {len(f.matrix)} floats, not 16")
        out += struct.pack("<16f", *f.matrix)
    out += p.trailing
    return bytes(out)


# --- C3Key channels (the 16-byte C3Frame records c3phy keeps opaque) --------

@dataclass
class KeyFrame:
    frame: int
    f_param: float
    b_param: int
    n_param: int


def decode_c3frames(raw: Iterable[bytes]) -> list[KeyFrame]:
    """``struct C3Frame { int nFrame; float fParam; BOOL bParam; int nParam; }``.

    VERIFIED by which member each Key_Process* export reads:
      * Key_ProcessAlpha      (0x725A0) reads +0x04 as float  -> fParam
      * Key_ProcessDraw       (0x72720) reads +0x08 as bool   -> bParam
      * Key_ProcessChangeTex  (0x726B0) reads +0x0C as int    -> nParam
    """
    out = []
    for r in raw:
        if len(r) < 16:
            continue
        n, f, b, i = struct.unpack_from("<ifii", r, 0)
        out.append(KeyFrame(n, f, b, i))
    return out


def key_alpha(keys: list[KeyFrame], frame: int) -> Optional[float]:
    """Key_ProcessAlpha: linear interpolation between the bracketing keys,
    clamped at both ends.  None when the channel is empty."""
    if not keys:
        return None
    prev = max((k for k in keys if k.frame <= frame), key=lambda k: k.frame, default=None)
    nxt = min((k for k in keys if k.frame > frame), key=lambda k: k.frame, default=None)
    if prev is None:
        return nxt.f_param
    if nxt is None:
        return prev.f_param
    span = nxt.frame - prev.frame
    t = 0.0 if span == 0 else (frame - prev.frame) / span
    return prev.f_param + (nxt.f_param - prev.f_param) * t


def key_change_tex(keys: list[KeyFrame], frame: int) -> Optional[int]:
    """Key_ProcessChangeTex: step function, no interpolation.  Exact frame match
    wins; otherwise the key immediately before the first key past `frame`."""
    if not keys:
        return None
    for i, k in enumerate(keys):
        if k.frame == frame:
            return k.n_param
        if i and frame < k.frame:
            return keys[i - 1].n_param
    return None


def key_draw(keys: list[KeyFrame], frame: int) -> Optional[bool]:
    """Key_ProcessDraw: exact frame match only -- visibility toggles."""
    for k in keys:
        if k.frame == frame:
            return k.b_param != 0
    return None


# ---------------------------------------------------------------------------
# an effect mesh, loaded
# ---------------------------------------------------------------------------

PART_PHY = "phy_motion"
PART_SHAPE = "shape_trail"
PART_PARTICLE = "particle"


# ---------------------------------------------------------------------------
# the three animation forms, as something you can FILTER on
# ---------------------------------------------------------------------------
#
# The classification itself is not new and is not re-derived here: the module
# docstring names the three forms, `load_effect_object` already sorts every
# chunk into one of them (`EffectPart.kind`), and `comod anim` already prints
# all three rows for one container.  What was missing was a *set-valued*
# spelling of the answer that a caller can query.
#
# **SET-VALUED, NOT A CATEGORY, AND THAT IS THE WHOLE POINT.**  A container
# routinely carries more than one form.  MEASURED on 5517,
# `c3/effect/lance/560029.C3` holds 4 `PHY` + 4 `MOTI` **and** 2 `SHAP` +
# 2 `SMOT` (plus a `CAME`); across the corpus 1,222 of 5517's 3,391 effects
# resolve to more than one form.  A single-value category would have to pick
# one and would silently drop the other, so every function below returns a
# `set` and every filter is membership.

FORM_PHY_MOTI = "PHY+MOTI"
FORM_SHAP_SMOT = "SHAP+SMOT"
FORM_PTCL = "PTCL/PTC3"

#: The three, in the order `docs/effects.md` and `comod anim` print them.
FORMS: tuple[str, ...] = (FORM_PHY_MOTI, FORM_SHAP_SMOT, FORM_PTCL)

#: `EffectPart.kind` -> form.  The decoded path and the tag-only path must
#: agree; `tests/test_effect_forms.py::TheTwoReadingsAgree` asserts they do on
#: every container of a real install, which is what stops this table drifting
#: from `load_effect_object`'s dispatch.
FORM_OF_PART = {
    PART_PHY: FORM_PHY_MOTI,
    PART_SHAPE: FORM_SHAP_SMOT,
    PART_PARTICLE: FORM_PTCL,
}

#: What a user may type for a form.  Spelled out rather than "any prefix"
#: because `p` is ambiguous between PHY and PTCL and a filter that guesses
#: between the two answers a question nobody asked.
FORM_ALIASES = {
    "phy": FORM_PHY_MOTI, "moti": FORM_PHY_MOTI, "phy+moti": FORM_PHY_MOTI,
    "mesh": FORM_PHY_MOTI, "bone": FORM_PHY_MOTI, "node": FORM_PHY_MOTI,
    "shap": FORM_SHAP_SMOT, "smot": FORM_SHAP_SMOT, "shap+smot": FORM_SHAP_SMOT,
    "ribbon": FORM_SHAP_SMOT, "trail": FORM_SHAP_SMOT,
    "ptcl": FORM_PTCL, "ptcx": FORM_PTCL, "ptc3": FORM_PTCL,
    "ptcl/ptc3": FORM_PTCL, "particle": FORM_PTCL, "particles": FORM_PTCL,
}


class UnknownForm(ValueError):
    """A `--form` value that names none of the three."""


def parse_form(text: str) -> str:
    """One user-typed form name -> its canonical spelling.

    Raises `UnknownForm` naming every accepted spelling.  A filter that
    silently ignored a form it did not recognise would return the UNFILTERED
    list and read as "nothing matches that" -- the one failure mode a filter
    must not have.
    """
    key = (text or "").strip().lower().replace(" ", "")
    if key in FORM_ALIASES:
        return FORM_ALIASES[key]
    raise UnknownForm(
        f"{text!r} is not one of the three animation forms. Accepted: "
        + ", ".join(sorted(set(FORM_ALIASES))))


def container_forms(data: bytes) -> set[str]:
    r"""Which of the three forms one C3 container carries, from its chunk TAGS.

    Tag-only on purpose: the filter has to run over every container of an
    install (10,390 of them on 6609) and decoding each one costs orders of
    magnitude more than walking its chunk chain.  The tags are what the
    classification is made of anyway -- `load_effect_object` dispatches on
    exactly these tags.

    Counted, never paired by adjacency, for the reason `comod._c3_forms`
    records: `MeshCreate` walks the file once for PHY chunks and again for
    MOTI chunks and binds them by ordinal, so a lone `MOTI` (a motion-only
    container) is still the PHY+MOTI form and an unpaired `SHAP` is still the
    ribbon form.  Membership answers "what KIND of animation is in here",
    which is the modder's question; whether the ordinals pair up is a
    different question and `comod anim` already answers it.
    """
    tags = {t for t, _ in c3phy.iter_chunks(data)}
    out: set[str] = set()
    if (tags & set(c3phy.VARIANTS)) or b"MOTI" in tags:
        out.add(FORM_PHY_MOTI)
    if b"SHAP" in tags or b"SMOT" in tags:
        out.add(FORM_SHAP_SMOT)
    if tags & set(PTCL_TAGS):
        out.add(FORM_PTCL)
    return out


def forms_from_kinds(kinds: Iterable[str]) -> set[str]:
    """`EffectObject.kinds` (or any part-kind iterable) -> the form set.

    The decoded-path twin of `container_forms`.  `build_linkage` already
    computes `kinds` per object, so it gets `forms` for free rather than
    re-reading 10,000 containers.
    """
    return {FORM_OF_PART[k] for k in kinds if k in FORM_OF_PART}


#: Why one layer of an effect contributed no form.  These are NOT the same
#: answer and collapsing them is how "this client ships no ribbons" gets
#: confused with "this client's ribbon meshes are in an archive we did not
#: read".  MEASURED 2026-09-07: on 7878, 1,521 of 2,595 effects resolve to no
#: form at all, and the reason is `missing`, not `bare` -- the table names
#: containers the install does not ship.
LAYER_OK = "ok"
LAYER_NO_PATH = "no-mesh-path"     # 3DEffectObj has no row for the layer's id
LAYER_MISSING = "missing"          # named, but this install does not ship it
LAYER_UNREADABLE = "unreadable"    # present and the chunk walk refused it


# -- BREAKAGE STATE: how whole one effect is on ONE base --------------------
#
# `coverage()` has counted full / partial / broken since it was written, but
# only as three integers -- there was no way to ask *which* effects were in
# which bucket, so a UI that wanted to filter on it had to re-derive the rule
# and would then drift from the published number the first time either side
# was touched.  The rule lives here now and `coverage()` calls it, so the
# census a page renders and the census docs/effects.md quotes are the SAME
# classification by construction rather than by inspection.
#
# THERE ARE FOUR STATES AND THE FOURTH IS NOT A BROKEN EFFECT.
#
#   whole      every layer resolves to a mesh file AND a texture file that
#              this install actually ships
#   partial    some layers do, some do not
#   broken     none do -- INCLUDING an effect whose definition declares no
#              layers at all (`coverage()` has always counted it here; the
#              composition is published separately as
#              `effects_declaring_no_layers` rather than moved, because
#              moving it would silently restate a number in the docs)
#   undefined  A RULE ROW NAMES IT AND `3DEffect` DOES NOT DEFINE IT.
#
# `undefined` is a different POPULATION, not a fourth bucket of the same one:
# these names are not in `db.effects`, so they are not in any effect list, and
# a page cannot offer them as rows.  Folding them into `broken` would destroy
# the only distinction that matters for fixing one -- a `broken` effect is BAD
# ART (the definition is there, the files are not), an `undefined` one is a
# BAD RULE (the rule points at a definition that was never written).  They are
# repaired in different files by different people.
EFFECT_WHOLE = "whole"
EFFECT_PARTIAL = "partial"
EFFECT_BROKEN = "broken"
#: Not a state a DEFINED effect can be in. Kept beside the other three so a
#: reader meets the distinction where the states are declared.
EFFECT_UNDEFINED = "undefined"

#: The states a name in `db.effects` can hold. `EFFECT_UNDEFINED` is
#: deliberately NOT here -- iterating this to build a filter must not offer a
#: category the list can never contain a row for.
EFFECT_STATES: tuple[str, ...] = (EFFECT_WHOLE, EFFECT_PARTIAL, EFFECT_BROKEN)


def effect_state(e: Optional["EffectDef"]) -> str:
    """Which of `EFFECT_STATES` one RESOLVED effect definition is in.

    Takes the output of `EffectDB.resolve`, which is the only thing that has
    filled in `mesh_found` / `texture_found`; an unresolved `EffectDef` read
    straight off `db.effects` has those flags at their `False` default and
    would classify as `broken` on every base.  Passing one is a caller bug,
    not a finding, and there is no way to detect it from here -- so resolve
    first, always.

    ``None`` (the name is not in this install's table) is `EFFECT_BROKEN` only
    because `coverage()` has never had such a name to hand: it iterates
    `db.effects`.  A caller holding a name that might not be defined wants
    `EFFECT_UNDEFINED` and must check membership itself.
    """
    if e is None or not e.layers:
        return EFFECT_BROKEN
    ok = sum(1 for L in e.layers if L.mesh_found and L.texture_found)
    if ok == len(e.layers):
        return EFFECT_WHOLE
    if ok:
        return EFFECT_PARTIAL
    return EFFECT_BROKEN


def effect_state_index(db: "EffectDB",
                       progress: Optional[Callable[[int, int], None]] = None,
                       ) -> dict:
    r"""Every effect on this base, classified, WITH the census that sums it.

    Returns ``{"states": {name: state}, "census": {...}}`` from ONE pass, so
    the per-effect answer a filter uses and the totals a census panel prints
    cannot disagree -- the only way to get two different numbers out of this
    is to call it twice against two different `EffectDB`s.

    **COST.** One `EffectDB.resolve` per effect: table lookups plus an
    `assets.exists` per layer path.  MEASURED 2026-09-07, cold: 5517 10.1 s /
    3,391 effects.  Cheaper than `form_index` (13.0 s on the same base)
    because it never opens a container, but far too slow to serve inline --
    consumers run it the way `EffectPlayer.form_index_now` runs the form
    index, in the background, publishing a state.

    **THE CENSUS NAMES THE FILES IT WAS COMPUTED FROM.** `tables_read` is
    `db.sources` verbatim.  Where a compiled `.dbc` twin exists the client
    reads the TWIN and the `.ini` beside it is a decoy, and the same id
    resolves DIFFERENTLY out of the two on one base -- so a census with no
    provenance is a number whose meaning depends on a file it did not name.
    A consumer that renders provenance from a SECOND reader must compare the
    two rather than assume they agree.

    **WHAT IT COULD NOT CLASSIFY IS A FIELD, NOT AN OMISSION.**
    ``unclassified`` is expected to be 0 and is emitted anyway; a census that
    only prints the buckets it filled reads as complete whether it is or not.
    """
    states: dict[str, str] = {}
    counts = {st: 0 for st in EFFECT_STATES}
    no_layers = 0
    unclassified: list[str] = []
    layers = 0
    # The four-way split the chain rows in the UI already draw, counted here
    # so the census can say WHICH LINK broke rather than only how many did.
    # "the layer names no id", "the id has no row in the path table" and "the
    # row names a file this install does not ship" are three different
    # repairs, and only the middle one is a table edit.
    lay = {"mesh_no_id": 0, "mesh_no_row": 0, "mesh_absent": 0, "mesh_ok": 0,
           "tex_no_id": 0, "tex_no_row": 0, "tex_absent": 0, "tex_ok": 0}
    names = sorted(db.effects)
    if progress is not None:
        progress(0, len(names))
    for i, name in enumerate(names, 1):
        if progress is not None and (i % 50 == 0 or i == len(names)):
            progress(i, len(names))
        e = db.resolve(name)
        st = effect_state(e)
        if st not in counts:                                # pragma: no cover
            unclassified.append(name)
            continue
        states[name] = st
        counts[st] += 1
        if e is None or not e.layers:
            no_layers += 1
            continue
        for L in e.layers:
            layers += 1
            for key, ident, path, found in (
                    ("mesh", L.effect_id, L.mesh_path, L.mesh_found),
                    ("tex", L.texture_id, L.texture_path, L.texture_found)):
                if not ident:
                    lay[key + "_no_id"] += 1
                elif not path:
                    lay[key + "_no_row"] += 1
                elif not found:
                    lay[key + "_absent"] += 1
                else:
                    lay[key + "_ok"] += 1

    # The FOURTH state, and it is a different population -- see the block
    # comment above `EFFECT_WHOLE`. Same derivation `coverage()` uses.
    referenced: set[str] = set()
    for r in db.action_rules:
        referenced.add(r.effect)
    for r in db.action_map:
        referenced.add(r.effect)
    for w in db.weapon_impact.values():
        referenced.update((w.hit_effect, w.blk_effect))
    referenced = {n for n in referenced if n and n.lower() != "none"}
    undefined = sorted(referenced - set(db.effects))

    census = {
        "effects": len(db.effects),
        "by_state": counts,
        "declaring_no_layers": no_layers,
        "unclassified": len(unclassified),
        "unclassified_names": unclassified[:50],
        "layers": layers,
        "layer_links": lay,
        # A layer link whose id is present in the definition and has NO ROW in
        # the path table.
        #
        # THIS IS NOT THE STANDING "125 of 3,987 effect layers" NUMBER and
        # must not be quoted as it. That figure is `assetroot`'s, is described
        # in two places with two different scopes ("across the corpus" in
        # `assetroot.py:311`, against a 3,987 layer total that belongs to CCO
        # alone in docs/effects.md §9), and counts a layer, not a link -- a
        # layer with an unresolvable mesh id AND an unresolvable texture id is
        # one there and two here. It is the same CLASS of hole measured a
        # different way. On 5517 this is 0: every id resolves to a row, and
        # the 229 mesh / 223 texture holes are rows naming files the install
        # does not ship, which is `*_absent`, a different repair.
        "layers_naming_an_unresolvable_id": lay["mesh_no_row"] + lay["tex_no_row"],
        "referenced_but_undefined": undefined,
        "referenced_but_undefined_count": len(undefined),
        "names_referenced_by_rules": len(referenced),
        "duplicate_effect_names": len(db.duplicate_effect_names),
        "tables_read": {k: v.as_dict() for k, v in sorted(db.sources.items())},
    }
    return {"states": states, "census": census}


@dataclass
class EffectPart:
    kind: str
    name: str = ""
    mesh: object = None            # c3phy.PhyMesh   (kind == phy_motion)
    motion: Optional[Motion] = None
    shape: Optional[Shape] = None  # kind == shape_trail
    smotion: Optional[SMotion] = None
    particle: Optional[Particle] = None   # kind == particle
    raw_size: int = 0              # chunk byte length, decoded or not
    #: CCFL kind 3: the atlas COLUMN count, overriding the square
    #: assumption. **0 means square** -- the engine zero-initialises it, and
    #: an absent override must reproduce the pre-2026-09-16 numbers exactly.
    #: Particle parts only; the arm exists in no other reader.
    atlas_cols: int = 0
    #: CCFL kind 10: ``(off_u, off_v, ext_u, ext_v)``, empty when absent.
    #: APPLIED by `effectplay.particle_quads` and `fx.js particleQuads` as the
    #: traced hybrid: offset ADDED to the cell origin, extent MULTIPLIED into
    #: the cell size (`c3ccfl.KIND_FLOAT4`).
    uv_rect: tuple = ()
    #: CCFL kind 13: the MESH scroll period in milliseconds, or 0 when absent.
    #:
    #: The client drives this mesh's scrolling UVs as ``timeGetTime()/period``
    #: -- an absolute, shared wall clock -- where `effectplay.frame_at` is
    #: per-instance and restarts when an effect spawns. **0 means "no kind 13",
    #: which means USE `frame_at`**, and that default must stay byte-identical
    #: for the 59,000-odd meshes that carry no kind 13.
    #: `effectplay.wall_counter` is the reference division; `CoEffects.
    #: wallCounter` is its JS mirror.
    period_ms: int = 0
    #: CCFL kind 9: the `(rate_u, rate_v)` UV-animation step, or `()`.
    #:
    #: The renderer adds `rate * per-effect-counter` to the mesh's PRIMARY UV.
    #: That is the SAME channel `period_ms` (kind 13) scrolls, on a DIFFERENT
    #: counter -- kind 13 substitutes an absolute wall clock, kind 9 keeps the
    #: effect's own frame counter. Read out of the shipped shader:
    #: `PixelTexCoord0 = c3_TexCoord0 + c3_UVAnimStep`, 25 of 30 uses.
    #:
    #: DISTINCT FROM `uv_step`, which is the PHY mesh's own `STEP` block at
    #: C3Phy+0x198/+0x19C times the counter at +0x194. Different field,
    #: different counter, same output channel. Do not conflate them.
    #:
    #: No shipped chunk carries BOTH kind 9 and kind 13 -- measured, 66 and
    #: 1,405 respectively over 212,992 CCFL bodies on 7939, zero overlap -- so
    #: their precedence is undefined by the corpus and is not invented here.
    uv_anim_step: tuple = ()
    alpha_keys: list[KeyFrame] = field(default_factory=list)
    draw_keys: list[KeyFrame] = field(default_factory=list)
    tex_keys: list[KeyFrame] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        if self.motion:
            return self.motion.frame_count
        if self.smotion:
            return self.smotion.frame_count
        if self.particle:
            return self.particle.frame_count
        if self.mesh is not None:
            return getattr(self.mesh, "frame_count", 0)
        return 0

    @property
    def decoded(self) -> bool:
        """False only for a part whose chunk we could not decode -- today that
        is a particle chunk that failed to parse.  `EffectObject.playable`
        is built from this rather than from the chunk *kind*, so a new
        undecodable form shows up as unplayable instead of silently counting
        as geometry."""
        if self.kind == PART_PARTICLE:
            return self.particle is not None and self.particle.exact
        return True

    @property
    def uv_grid(self) -> int:
        """Flipbook grid size N for ChangeTex: the PHY's own ``frameCount``
        field (C3Phy+0x190).  Cell for key value ``n`` is ``(n % N, n // N)``
        with cell size ``1/N``.  VERIFIED at graphic.dll 0x56102-0x5616C."""
        return getattr(self.mesh, "frame_count", 0) if self.mesh is not None else 0

    @property
    def uv_step(self) -> Optional[tuple[float, float]]:
        """The optional ``STEP`` block, reinterpreted as two floats: the per-tick
        U/V scroll used when the mesh has no ChangeTex keys.  VERIFIED: Phy_Load
        stores STEP's two dwords at C3Phy+0x198/+0x19C (RVA 0x5B21E/0x5B235) and
        Phy_Calculate multiplies them by the animation counter at +0x194."""
        step = getattr(self.mesh, "step", None) if self.mesh is not None else None
        if not step:
            return None
        # NO REINTERPRETATION SINCE M8: `PhyMesh.step` is two f32 now. This
        # used to unpack them out of u32 here, which is why the renderer was
        # always right while `editbundle` showed a human 3163243414.
        return tuple(float(v) for v in step)

    @property
    def alpha_end(self) -> Optional[int]:
        """Last frame at which the alpha envelope is still non-zero.

        Effect meshes routinely carry a 101-frame motion track but fade to zero
        after ~10 frames; that is the real length of the animation.
        """
        if not self.alpha_keys:
            return None
        if all(k.f_param == 0.0 for k in self.alpha_keys):
            return 0
        return max(k.frame for k in self.alpha_keys if k.f_param > 0.0)

    @property
    def effective_frames(self) -> int:
        """Playable length in frames.

        The alpha envelope is the only thing in the data that says when a burst
        is over: effect meshes habitually declare a 101-frame motion track and
        fade to zero after ~10.  So: if the channel has two or more keys and the
        last one is alpha 0, the animation ends there; otherwise there is no
        fade-out and the full motion length stands.  INFERRED, but it is what
        the data plainly shows and it makes the impact effects come out at
        sensible lengths (m-b02 = 11 frames x 33 ms = 363 ms).
        """
        if self.particle is not None:
            return self.particle.effective_frames
        if len(self.alpha_keys) < 2:
            return self.frame_count
        last = max(self.alpha_keys, key=lambda k: k.frame)
        if last.f_param > 0.0:
            return self.frame_count
        return min(self.frame_count or (last.frame + 1), last.frame + 1)


@dataclass
class EffectObject:
    """A ``3DEffectObj.ini`` entry, loaded and decoded."""
    obj_id: str
    logical: str
    source: str = ""
    parts: list[EffectPart] = field(default_factory=list)
    error: str = ""
    #: The ``CAME`` chunks this container carries, decoded.  Almost always
    #: zero or one; the list is kept because the format does not forbid more.
    #:
    #: **This is the artist's own framing of the effect and NOTHING HAS READ
    #: IT.** `parse_came` has consumed 13,935 of 13,935 bodies exactly since
    #: the format was settled, and `load_effect_object` threw the chunk away
    #: with the comment "authoring metadata; nothing to play" until
    #: 2026-09-06.  A preview that fits its own camera to the bounding box is
    #: guessing at a question the file answers.
    cameras: list["Camera"] = field(default_factory=list)

    @property
    def kinds(self) -> set[str]:
        return {p.kind for p in self.parts}

    @property
    def frame_count(self) -> int:
        return max([p.frame_count for p in self.parts] or [0])

    @property
    def effective_frames(self) -> int:
        return max([p.effective_frames for p in self.parts] or [0])

    @property
    def undecoded(self) -> list[str]:
        """Parts whose chunk this build cannot play.  Empty means every chunk
        in the file has a reader."""
        return [p.name or p.kind for p in self.parts if not p.decoded]

    @property
    def playable(self) -> bool:
        return bool(self.parts) and not self.undecoded and not self.error


def apply_ccfl(part: EffectPart, cf) -> EffectPart:
    r"""Apply one decoded `CCFL` to the object it annotates. **By CLASS.**

    The `ccflag` type number is scoped to the READER, and the reader is chosen
    by the parent chunk's class (`CCFL_READER_TYPES`). Kind 3 -- the atlas
    column count -- has an arm only in the PARTICLE reader; kind 13 -- the
    mesh scroll period -- only in the MESH reader. Reading both fields off
    every CCFL regardless of parent would be the flat-table mistake again, in
    a new place.

    **THIS IS A FUNCTION BECAUSE THE CORPUS CANNOT TEST IT.** It was five
    inline lines, and the must-fire for them did not fire: planting "read both
    fields, ignore the parent" left every arm green, because the shipped data
    already respects the partition -- 225/225 kind 3 after a `PTC3`, 775/775
    kind 13 after a `PHY4`/`PHY5`, and never the other way round. On this
    corpus the wrong code and the right code produce identical output, so an
    arm driven from the corpus asserts a property of the DATA and calls it a
    property of the CODE. `tests/test_ccfl_period_ms` now hands this function
    a body carrying BOTH kinds, which no shipped file does.

    Mutates and returns `part`, so a caller reads as one statement.
    """
    if part is None or cf is None:
        return part
    if part.kind == PART_PARTICLE:
        if getattr(cf, "atlas_cols", 0) > 0:
            part.atlas_cols = cf.atlas_cols
        # Kind 10 is particle-side too; `particle_quads` applies it.
        rect = getattr(cf, "uv_rect", ())
        if rect:
            part.uv_rect = tuple(rect)
    elif part.kind == PART_PHY:
        if getattr(cf, "period_ms", 0) > 0:
            part.period_ms = cf.period_ms
        # Kind 9 is MESH-side too, and PHY5-only on the corpus (109/109). The
        # class partition this function exists to respect puts it here beside
        # kind 13 and nowhere near the particle arm above.
        step = getattr(cf, "uv_anim_step", ())
        if step:
            part.uv_anim_step = tuple(step)
    return part


def load_effect_object(root: AssetRoot, obj_id: str, logical: str) -> EffectObject:
    """Read one effect C3 and decode every chunk we know how to play."""
    logical = logical.replace("\\", "/")
    loc = root.locate(logical)
    if loc is None:
        return EffectObject(obj_id, logical, error="asset not found")
    obj = EffectObject(obj_id, logical, loc.source)
    try:
        c3 = C3File(root.read(logical))
    except Exception as exc:                                  # pragma: no cover
        obj.error = f"C3 parse failed: {exc}"
        return obj

    pending_phy: Optional[EffectPart] = None
    pending_shape: Optional[EffectPart] = None
    #: The last OBJECT-bearing part appended, for the CCFL arm.
    last_object: Optional[EffectPart] = None
    for ch in c3.chunks:
        try:
            if ch.tag in (b"PHY ", b"PHY2", b"PHY3", b"PHY4", b"PHY5"):
                mesh = c3phy.parse_phy(ch.tag, ch.body)
                pending_phy = EffectPart(
                    PART_PHY, mesh.name, mesh=mesh,
                    alpha_keys=decode_c3frames(mesh.keys.alphas),
                    draw_keys=decode_c3frames(mesh.keys.draws),
                    tex_keys=decode_c3frames(mesh.keys.change_texs))
                obj.parts.append(pending_phy)
                last_object = pending_phy
            elif ch.tag == b"MOTI":
                m = parse_moti(ch.body)
                if pending_phy is not None and pending_phy.motion is None:
                    pending_phy.motion = m
                    pending_phy = None
                else:
                    obj.parts.append(EffectPart(PART_PHY, motion=m))
            elif ch.tag == b"SHAP":
                sh = parse_shap(ch.body)
                pending_shape = EffectPart(PART_SHAPE, sh.name, shape=sh)
                obj.parts.append(pending_shape)
                last_object = pending_shape
            elif ch.tag == b"SMOT":
                sm = parse_smot(ch.body)
                if pending_shape is not None and pending_shape.smotion is None:
                    pending_shape.smotion = sm
                    pending_shape = None
                else:
                    obj.parts.append(EffectPart(PART_SHAPE, smotion=sm))
            elif ch.tag in PTCL_TAGS:
                part = EffectPart(PART_PARTICLE, ch.tag.decode("latin1"),
                                  raw_size=len(ch.body))
                try:
                    part.particle = parse_ptcl(ch.body, PTCL_TAGS[ch.tag],
                                               ch.tag.decode("latin1"))
                except Exception as exc:
                    obj.error = f"{ch.name}: {exc}"
                obj.parts.append(part)
                last_object = part
            elif ch.tag == b"CCFL":
                # THE ATLAS COLUMN-COUNT OVERRIDE REACHES THE RENDERER HERE,
                # and until 2026-09-16 this walk had no CCFL arm at all: the
                # chunk was read by `parse_ccfl` in tests and by nothing on
                # the draw path, so kind 3 could not have been honoured even
                # once it was decoded.
                #
                # Attached to the PRECEDING particle, which is the engine's
                # own rule -- the annotation applies to the last object read,
                # and the corpus agrees: all 225 kind-3 entries follow a PTC3
                # and nothing else. Parsed through `core/c3ccfl`, the single
                # home for these fields, rather than re-reading the payload
                # here; two decoders of one field is how they drift.
                #
                # A CCFL that will not decode is NOT an error for the effect:
                # it is an authoring annotation, and the engine's readers drop
                # what they cannot use. The part keeps `atlas_cols = 0`, which
                # means "square" and reproduces the pre-2026-09-16 numbers.
                if last_object is not None:
                    try:
                        cf = c3ccfl.parse_ccfl(bytes(ch.body))
                    except Exception:                         # noqa: BLE001
                        cf = None
                    if cf is not None:
                        apply_ccfl(last_object, cf)
                    # `last_object` is NOT cleared. The engine's rule is "the
                    # last object read", not "the next chunk only", and a file
                    # may carry two annotations for one object. Clearing here
                    # would drop the second silently -- which is the shape of
                    # failure this file keeps finding in its own code.
            elif ch.tag == b"CAME":
                # NOT "nothing to play" -- this is the framing the artist
                # authored for this effect, and a viewer that ignores it has
                # to invent one. Decoded here, consumed by
                # `effectplay.EffectScene.payload["camera"]`.
                #
                # A body that will not decode is recorded on the part list as
                # nothing and left OUT of `cameras`, never appended half-read:
                # a camera is used to POINT THE VIEW, so a partially-parsed one
                # would frame the preview somewhere the file never said.
                obj.cameras.append(parse_came(ch.body))
        except Exception as exc:                              # pragma: no cover
            obj.error = f"{ch.name}: {exc}"
    return obj


def shape_ribbon(shape: Shape, smotion: Optional[SMotion], world: Mat4,
                 frame: int, history: list[tuple], *, subdiv: int = 5,
                 max_segments: int = 800) -> list[tuple]:
    """Advance a SHAP trail by one frame and return the ribbon vertex pairs.

    Mirrors graphic.dll!Shape_Draw / Shape_SetSegment: the ribbon holds
    ``min(segments * 5, 800)`` segments, each draw pushes ``subdiv`` linearly
    interpolated pairs between the previous transformed line and the current
    one, and the strip is ``segments*2 + 2`` vertices wide.

    ``history`` is the caller's rolling list of ``(a, b)`` point pairs, newest
    last; it is mutated and returned.  Pass ``[]`` on the first frame.
    """
    line = shape.line
    if len(line) < 2:
        return history
    m = world
    if smotion and smotion.matrices:
        m = mat_mul(smotion.matrices[frame % len(smotion.matrices)], world)
    a = transform_point(m, line[0])
    b = transform_point(m, line[1])
    cap = min(shape.segments * subdiv, max_segments)
    if history:
        pa, pb = history[-1]
        for s in range(1, subdiv + 1):
            t = s / subdiv
            history.append((
                tuple(pa[i] + (a[i] - pa[i]) * t for i in range(3)),
                tuple(pb[i] + (b[i] - pb[i]) * t for i in range(3)),
            ))
    else:
        history.append((a, b))
    if len(history) > cap + 1:
        del history[:len(history) - (cap + 1)]
    return history


# ---------------------------------------------------------------------------
# effect definitions -- ini/3DEffect.ini
# ---------------------------------------------------------------------------

@dataclass
class EffectLayer:
    index: int
    effect_id: str = ""
    texture_id: str = ""
    extra_texture_ids: list[str] = field(default_factory=list)
    asb: int = 5
    adb: int = 6
    scale: Optional[int] = None
    frame_offset: Optional[int] = None
    interval: Optional[int] = None
    loop_once: Optional[int] = None
    zbuffer: Optional[int] = None
    ztest: Optional[int] = None
    billboard: Optional[int] = None
    lev: Optional[int] = None
    mix_opt: Optional[int] = None
    x_self: Optional[float] = None
    y_self: Optional[float] = None
    z_self: Optional[float] = None
    # filled in by EffectDB.resolve()
    mesh_path: str = ""
    mesh_found: bool = False
    texture_path: str = ""
    texture_found: bool = False

    @property
    def asb_name(self) -> str:
        return blend_name(self.asb)

    @property
    def adb_name(self) -> str:
        return blend_name(self.adb)


@dataclass
class EffectDef:
    name: str
    amount: int = 0
    delay: int = 0                 # ms before the effect first appears
    loop_time: int = 1             # number of loops; 99999999 == "forever"
    loop_interval: int = 0         # ms of dead time between loops
    frame_interval: int = 33       # ms per animation frame
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    color_enable: bool = False
    billboard: Optional[int] = None
    lev: Optional[int] = None
    layers: list[EffectLayer] = field(default_factory=list)
    source: str = "3DEffect.ini"
    #: What the compiled record carries that this class does not model as a
    #: field, on the same footing as ``Rsdb.extra``: **preserved, not
    #: interpreted.**  Empty when the definition came from the plaintext ini.
    #:
    #: **NARROWED 2026-09-05 from three bytes to one.**  It used to hold
    #: ``unk62``/``unk63``/``unk64``; ``billboard`` and ``color_enable`` are
    #: named now and mapped onto the fields above (see `_effect_from_effe`).
    #: ``lev`` stays here: its byte POSITION is identified and its VALUES do
    #: not track the ini's ``Lev``, so promoting it would put a number nobody
    #: has explained behind a name everybody would trust.
    extra: dict = field(default_factory=dict)

    @property
    def fps(self) -> float:
        return 1000.0 / self.frame_interval if self.frame_interval else 0.0

    @property
    def endless(self) -> bool:
        return self.loop_time >= 99999


def _parse_effect_section(name: str, d: dict[str, str], source: str) -> EffectDef:
    amount = _int(d, "Amount", 0)
    e = EffectDef(
        name=name,
        amount=amount,
        delay=_int(d, "Delay"),
        loop_time=_int(d, "LoopTime", 1),
        loop_interval=_int(d, "LoopInterval"),
        frame_interval=_int(d, "FrameInterval", 33),
        offset=(_float(d, "OffsetX"), _float(d, "OffsetY"), _float(d, "OffsetZ")),
        color_enable=bool(_int(d, "ColorEnable")),
        billboard=_int(d, "Billboard") if "Billboard" in d else None,
        lev=_int(d, "Lev") if "Lev" in d else None,
        source=source,
    )
    for i in range(amount):
        layer = EffectLayer(
            index=i,
            effect_id=d.get(f"EffectId{i}", ""),
            texture_id=d.get(f"TextureId{i}", ""),
            extra_texture_ids=[d[k] for k in (f"TextureId{i}_1", f"TextureId{i}_2",
                                              f"TextureId{i}_3") if k in d],
            asb=_int(d, f"ASB{i}", 5),
            adb=_int(d, f"ADB{i}", 6),
        )
        for attr, key, conv in (
            ("scale", f"Scale{i}", int), ("frame_offset", f"FrameOffset{i}", int),
            ("interval", f"Interval{i}", int), ("loop_once", f"LoopOnce{i}", int),
            ("zbuffer", f"ZBuffer{i}", int), ("ztest", f"ZTest{i}", int),
            ("billboard", f"Billboard{i}", int), ("lev", f"Lev{i}", int),
            ("mix_opt", f"MixOpt{i}", int), ("x_self", f"XSelf{i}", float),
            ("y_self", f"YSelf{i}", float), ("z_self", f"ZSelf{i}", float),
        ):
            if key in d:
                setattr(layer, attr, (_int if conv is int else _float)(d, key))
        e.layers.append(layer)
    return e


#: The plaintext ``Scale<i>`` is an integer **percent** and the compiled
#: ``scale`` is the fraction.  Not assumed -- measured as a Rosetta on 5517,
#: where the two tables otherwise agree: under ``ini/100`` **5,099 of 5,099
#: shared layers match exactly**, and ``red-flower-small``'s ``Scale0=78``
#: reads 0.78 compiled.  (On 6090 five disagree, and all five sit inside the
#: 56 records §7a already reports as content edits.)  A field-offset or stride
#: error cannot score 5,099/5,099 on a unit conversion nobody fitted, so this
#: is also an independent check on the layer layout.
EFFE_SCALE_PERCENT = 100


def _effect_from_effe(rec: dict) -> EffectDef:
    """One ``core/dbc.py::read_effe`` record as an `EffectDef`.

    **UPDATED 2026-09-05, and this docstring used to say the opposite.**  It
    read: *"the compiled record carries no field identified as ColorEnable,
    Billboard or Lev ... all three identifications are either refuted or
    unfalsifiable from these files (C44)."*  Four of the six bytes are
    identified now, from the vendor's own ``ini``<->``wdb`` converter DLLs
    rather than from another table fit -- see `core/dbc.py`'s EFFE section and
    ``docs/effe_writer_2026-09-05.md``.  So they are mapped:

      * ``ColorEnable`` and record-level ``Billboard`` are populated.
      * per-layer ``ZBuffer`` and ``Billboard`` are populated -- the layer's
        old ``u16 pad``.
      * ``Lev`` is **NOT** populated, and that is the one deliberate gap.
        The byte at +64 is where the converter's format-string block puts
        ``Lev``, but its VALUES do not behave like a copy of the ini's:
        agreement is 47.87% over 54,559 name-matched pairs against a
        99.34-100% band for every field that is certainly right.  Naming a
        position is not the same as knowing the value, so it stays in
        `extra` where nothing consumes it as a level.
    """
    e = EffectDef(
        name=rec["name"],
        amount=rec["amount"],
        delay=rec["delay"],
        loop_time=rec["loop_time"],
        loop_interval=rec["loop_interval"],
        frame_interval=rec["frame_interval"],
        offset=rec["offset"],
        color_enable=bool(rec["color_enable"]),
        billboard=rec["billboard"],
        source="3DEffect.dbc",
        extra={"lev": rec["lev"]},
    )
    for i, L in enumerate(rec["layers"]):
        lay = EffectLayer(
            index=i,
            effect_id=str(L["effect"]),
            texture_id=str(L["texture"]),
            asb=L["asb"], adb=L["adb"],
            zbuffer=L["z_buffer"], billboard=L["billboard"])
        # `Scale<i>` is absent from the ini whenever it is 1, so an explicit
        # 1.0 here maps to None -- otherwise every compiled layer would appear
        # to declare a scale the plaintext lineage never does, and the
        # "27 layers" figure in §7 would jump for a formatting reason.
        if abs(L["scale"] - 1.0) > 1e-6:
            lay.scale = int(round(L["scale"] * EFFE_SCALE_PERCENT))
        e.layers.append(lay)
    return e


# ---------------------------------------------------------------------------
# the database
# ---------------------------------------------------------------------------


@dataclass
class TableSource:
    """Which file one table was *actually* read from, on this base.

    Emitted beside every count `coverage()` produces.  The defect this exists
    to make visible is the one that produced this class of bug in the first
    place: a reader that exists, is not on the path, and leaves a number that
    looks like a measurement.  With the source printed, "is this the live
    table?" stops being a question about the code and becomes a line of
    output.
    """
    table: str                       # stem, e.g. "3DEffect"
    path: Optional[Path]             # the file read, or None when absent
    form: str                        # "dbc" | "ini" | "missing"
    rows: int = 0
    twin: Optional[Path] = None      # the twin NOT read, when there is one
    note: str = ""

    def as_dict(self) -> dict:
        d = {"form": self.form, "file": self.path.name if self.path else None,
             "rows": self.rows}
        if self.twin is not None:
            d["shadowed_ini_not_read" if self.form == "dbc"
              else "compiled_twin_not_read"] = self.twin.name
        if self.note:
            d["note"] = self.note
        return d


def _compiled_twin(ini_path: Path, want: bytes) -> tuple[Optional[Path], Optional[bytes]]:
    """``(twin, bytes)`` when a compiled twin exists **and** carries ``want``.

    Absence is the normal answer on four of the six declared bases, so it is
    returned as ``(None, None)`` rather than raised.  The magic is checked
    because twin-presence and twin-*parseability* are different questions:
    a twin whose magic we do not know must not silently become an empty
    table -- the caller keeps reading the plaintext and says so.
    """
    twin = dbcshadow.compiled_twin(ini_path)
    if twin is None:
        return None, None
    try:
        data = twin.read_bytes()
    except OSError:                                           # pragma: no cover
        return twin, None
    return (twin, data) if dbc.magic(data) == want else (twin, None)


#: `weapon.dbc` field order, as ini field names.  `read_mesh` returns a row of
#: ints; `weapon.ini` writes the same six values under these names, plus a
#: `Part` count and a `Material0` the compiled table does not carry.  VERIFIED
#: on 6090 against the 4,828 ids the two files share: `Mesh0` and `Texture0`
#: agree on 4,821 -- see `_weapon_appearances` for the seven that do not.
_MESH_AS_INI = (("Mesh0", "mesh"), ("Texture0", "texture"),
                ("MixTex0", "mixtex"), ("MixOpt0", "mixopt"),
                ("Asb0", "asb"), ("Adb0", "adb"))


def _weapon_appearances(ini_dir: Path) -> dict[str, dict[str, str]]:
    r"""The weapon appearance table, **from the compiled twin where there is one**.

    `_load_effects` states this project's rule twenty lines from where this is
    called: *where a twin exists the client reads the twin, so an ini-only
    definition is one the client does not have.*  This reader did not apply it,
    and `weapon.ini` is the file where that costs the most, because it is
    FROZEN.

    MEASURED on the read-only baseline, 2026-09-10 (backlog item 25, whose
    table this reproduces exactly).  `ini/weapon.ini` is byte-identical --
    md5 `aa7473e`, 521,496 bytes -- on **seven** bases, 5165 through 7878,
    while the table the client actually reads keeps growing::

        base   weapon.ini        weapon.dbc     appearances hit by
                                                ini      dbc
        5517   521,496 frozen    172,064        76.3%    88.8%
        6090   521,496 frozen    268,448        53.0%    85.6%
        6609   521,496 frozen    319,520        44.5%    76.2%
        7205   521,496 frozen    319,520         4.5%    28.7%

    So the weapon/action split -- an `Action3DEffect` row is a WEAPON when its
    appearance is a section in this table -- was being decided against a file
    5165 shipped and nobody has touched since.

    **NOTHING IS LOST BY THE SUBSTITUTION, and that was measured rather than
    assumed.**  On all four twin bases the twin's id set is a strict SUPERSET
    of the ini's: `set(weapon.ini) - set(weapon.dbc)` is **0** on every one.
    The reclassification is one-directional -- 409 / 1,392 / 1,609 / 298
    appearances move from ACTIONS to WEAPONS on 5517 / 6090 / 6609 / 7205 and
    none moves the other way.

    **AND SEVEN ROWS ARE CORRECTED, not merely added.**  Ids 900113..900119
    name `Mesh0=900110` in the frozen ini and `900120` in the compiled table;
    900116..900119 additionally disagree on `Texture0` (`3900110` vs
    `900120`).  The client reads the twin, so the twin is the right answer and
    the ini was quietly wrong about seven weapons.  This is the same shape as
    armor.ini's Mesh0 recovery: 21 gained, 0 lost, 4 corrected.

    THE SPELLING, because it is the trap here.  Ids are stored as ints and
    stringified plainly.  A zero-padded alias (`str(i).zfill(9)`, the CCO
    convention `coassets.PartIni.get` bridges) hits **ZERO** appearances on
    all four bases and would only inflate `len(weapon_appearances)` -- the
    census number -- by a factor of two.  Plain `str(id)` and nothing else.

    WHAT THIS DOES NOT FIX, and do not read the numbers above as if it did.
    7632 and 7878 ship **no `.dbc` files at all** and the same frozen
    `weapon.ini`, so they stay at 3.2% and 2.7%; 7205 only reaches 28.7%.
    **A hit rate that is low but nonzero across a client boundary is two
    mechanisms, not one with gaps.**  The open half is backlog 25's second
    mechanism -- the newest clients appear to have moved the declaration into
    the `.dat` family (`WeaponActionData.dat`, `WeaponMotionData.dat`) -- and
    it is untouched here.  Whoever takes it must report the two eras
    separately; a single blended percentage hides whichever half is still
    broken.

    `Material0` is not in the compiled table.  It is `default` on every ini
    row and nothing reads it (`core/colibrary.py` writes the string, never
    reads it), so the twin-derived sections do not carry it.
    """
    ini_path = ini_dir / "weapon.ini"
    twin, data = _compiled_twin(ini_path, b"MESH")
    if data is None:
        # No twin, or a twin whose magic we do not know.  `_compiled_twin`
        # distinguishes those from "no file" for exactly this reason: the
        # plaintext is still the best available answer and must not be
        # replaced by an empty table.
        return read_sections(ini_path)
    out: dict[str, dict[str, str]] = {}
    for rid, rows in dbc.read_mesh(data).items():
        if not rows:
            continue
        sec: dict[str, str] = {"Part": str(len(rows))}
        for i, row in enumerate(rows):
            for name, key in _MESH_AS_INI:
                sec[name[:-1] + str(i)] = str(row[key])
        out[str(rid)] = sec
    return out


@dataclass
class WeaponImpact:
    """One ``ini/WeaponEffect.ini`` section: the spark shown at the target."""
    weapon_type: str
    hit_effect: str = ""
    hit_sound: str = ""
    blk_effect: str = ""
    blk_sound: str = ""


def effect_names(value: str) -> list:
    r"""The effect names an `Action3DEffect.ini` VALUE refers to.

    **The value column carries four forms and only the first is a bare
    name.** Measured 2026-09-10 across the baseline tree (backlog item 27):

        410009                           a row in the 3DEffect table
        _p_24_wing10_open                a `c3/effect/...` asset directory
        195040~100~100~100~1             either of those, PARAMETERISED
        _p_0_18dog_armet,_p_21_18dog_l_weapon,...~100~100~100~1
                                         a COMMA-SEPARATED LIST of them,
                                         sharing one `~parameter` suffix

    The last form is a MULTI-EFFECT ROW: one rule lighting several pieces at
    once. On 7878, **675 of 4,197 rule rows are lists**, up to nineteen names
    long, and the names read as body parts -- `_p_0_..._armet`,
    `_p_21_..._l_weapon`, `_p_30_..._l_foot`. That is the owner's "multi
    effect skill" in the newest clients' own data.

    THE COST OF NOT PARSING IT, measured before this existed: taking the whole
    string as one table key resolved **1.6% of 7878's rule values**; parsing
    it resolves **84.6%**. A grammar the reader does not know looks exactly
    like data that is not there.

    **The `~parameters` are NOT decoded.** `~100~100~100~1` beside `~80~80~80`
    reads like scale plus a flag, and nothing here acts on them -- they are
    stripped so the NAME can be found, and the raw value stays available on
    the row for whoever decodes them. Stripping a field is not understanding
    it, and this docstring is the only place that says so.

    THE OLDER CLIENTS ARE NOT UNTOUCHED, and my first version of this
    docstring said they were.  6090 has zero comma-lists but **45 of its
    1,699 values ARE parameterised** -- `610009~80~80~80`.  Parsing them is
    an improvement there too, not a regression: 6090 goes from 97.4% to
    99.9% resolved.  What never happens on an old client is a SPLIT: no
    value there parses to more than one name.  The gate asserts that
    distinction rather than the sweeping claim, which is how the false one
    was caught.
    """
    head = str(value or "").split("~")[0]
    return [p for p in (x.strip() for x in head.split(",")) if p]


@dataclass
class ActionEffectRule:
    """One ``ini/Action3DEffect.ini`` row."""
    shape: str
    action: str
    group_hi: str
    group_lo: str
    effect: str

    @property
    def appearance(self) -> str:
        return self.group_hi + self.group_lo

    @property
    def effect_names(self) -> list:
        """`effect` parsed -- see `effects.effect_names`.

        A property rather than a field so `effect` stays the RAW value: the
        `~parameters` are undecoded and a consumer that wants them must not
        have to reconstruct the string this already threw away.
        """
        return effect_names(self.effect)

    @property
    def specificity(self) -> int:
        r"""How many key fields this row pins down; ties go to file order.

        `_all_nines`, **not `!= WILDCARD`**. This was the fourth site of C35's
        three-wide sentinel: an official client's
        aura row is keyed `9999`, which is not the literal `"999"`, so it used
        to count as a *pinned* field and an aura row scored 3 where it pins 2.

        **It was measured before it was changed, and it was inert.** Sweeping
        every appearance x every non-all-nines action, this change moves **0**
        answers on all three bases -- CCO 45,526, 5517 114,380, 6090 230,040 --
        because `_field_matches` matches a `9999` rule only against an
        all-nines query, so an aura row and an action row are never in one
        candidate set and the inflated score was never compared against
        anything. On CCO they do compete, and there the sentinel was already
        the right width. Re-measured at 0 again *after* the change.

        Fixed anyway, because inert is not the same as right and this file has
        now paid four times for leaving one spelling behind. It stops being
        inert the moment `_field_matches` is widened -- the refuted fix, where
        the aura row ties the genuine action row and wins on file order,
        overwriting 102 correct answers on 5517 and 132 on 6090. If you are
        here because you are about to widen the sentinel, that measurement is
        the answer: see `WILDCARD` and CORRECTIONS C35.
        """
        return sum(1 for f in (self.shape, self.action, self.group_hi,
                               self.group_lo) if not _all_nines(f))


@dataclass
class ActionMapRule:
    """One ``ini/ActionMap3DEffect.ini`` section."""
    shape: str
    action: str
    terrain: str
    effect: str
    show_time: int = 0             # 0 = at motion start, 1 = at motion end
    dir_enable: int = 0            # 1 = effect rotates with the character facing

    @property
    def specificity(self) -> int:
        """Same predicate as `ActionEffectRule.specificity`, and for the same
        reason -- leaving the sibling on the literal is exactly the mistake
        C35 records. **Currently unexercised on the official clients**, and not
        because their sentinel is narrow: `EffectDB` drops every one of their
        126 rows before this is ever reached (see `action_map`'s note), so
        there is nothing here to measure yet. It is correct in advance rather
        than a second thing to remember when that is fixed."""
        return sum(1 for f in (self.shape, self.action, self.terrain)
                   if not _all_nines(f))


@dataclass
class WeaponEffectSet:
    """Everything the viewer needs to show one equipped weapon's effects."""
    appearance: str
    weapon_type: str
    type_name: str = ""
    aura: str = ""                                  # action 999 -- always on
    attack: dict[str, str] = field(default_factory=dict)   # action -> effect
    impact: Optional[WeaponImpact] = None
    motion_meshes: dict[str, str] = field(default_factory=dict)
    in_weapon_ini: bool = False

    @property
    def effect_names(self) -> list[str]:
        out = [self.aura] + list(self.attack.values())
        if self.impact:
            out += [self.impact.hit_effect, self.impact.blk_effect]
        seen, res = set(), []
        for n in out:
            if n and n.lower() != "none" and n not in seen:
                seen.add(n)
                res.append(n)
        return res


class EffectDB:
    """All effect tables, loaded once."""

    def __init__(self, root: Path | str = DEFAULT_ROOT, assets: Optional[AssetRoot] = None):
        self.root = Path(root)
        self.ini = self.root / "ini"
        self.assets = assets or AssetRoot(self.root)
        #: Did WE open that root?  `close()` must close it ONLY if we did.
        #: `assets=` is adopted by identity and its owner is almost always
        #: still reading through it (`depclose`, `effectplay`, `mapparts`,
        #: `superfx` all hand one in), so closing a BORROWED root would shut
        #: containers out from under the caller -- a worse bug than the leak
        #: `close()` exists to fix, and one that fails intermittently and
        #: nowhere near its cause.  Spelled as in `tools/meshtex.py`.
        self._owns_assets = assets is None

        #: table stem -> the file this instance actually read.  See
        #: `TableSource`; surfaced by `coverage()` as `tables_read`.
        self.sources: dict[str, TableSource] = {}
        #: ini filename -> `read_flat_live`'s record for *this instance's* call.
        self._flat_records: dict[str, dict] = {}

        #: logical C3 path -> `(form set, LAYER_* status)`. See
        #: `container_forms_cached`: effects share containers heavily and the
        #: scan is archive-read bound, so this is the difference between a
        #: 13-second corpus pass and a several-minute one.
        self._form_cache: dict[str, tuple[set[str], str]] = {}
        #: effect name -> form set, built by `form_index()` on first use.
        self._form_index: Optional[dict[str, set[str]]] = None

        # -- effect definitions.  GraphicData.dll names BOTH `ini/3DEffect.ini`
        # (.rdata 0x921A8) and `ini/3DEffect.dbc` (0x369A8); where the compiled
        # twin exists the client reads the twin, so this reads the twin too.
        # On 5017/5065/5165/CCO there is none and the plaintext ini IS the live
        # table -- absence is the normal answer on four of the six declared
        # bases, not a broken install.
        self.effects, self.duplicate_effect_names = self._load_effects()
        # The shipped 3DEffect.json is a stale 2022 export of the *plaintext*
        # lineage; merge in names neither source has so nothing referenced
        # elsewhere is silently missing.  It ships on CCO only (15 names) and
        # does not exist on any official client, so this is a no-op wherever
        # the compiled table is in play.
        self.json_only: list[str] = []
        for e in self._load_effect_json():
            if e.name not in self.effects:
                self.effects[e.name] = e
                self.json_only.append(e.name)

        # The id -> path tables, read as the client reads them: the compiled
        # twin overlaid on the plaintext where one exists. Before this, every
        # layer that resolved through these was resolved against 2009 data on
        # any 5517/6090 root, and every coverage figure derived from them was
        # a floor. See `read_flat_live` -- including why the ini's padded keys
        # are folded onto the twin's spelling before the overlay.
        self.objs = self._load_path_table("3DEffectObj.ini")
        self.textures = self._load_path_table("3dtexture.ini")
        self.meshes = self._load_path_table("3dobj.ini")
        # STALE-INI: weaponmotion.dbc shadows this, and `read_flat_live`
        # declines to overlay it -- the two key spaces are disjoint (25,944 vs
        # 169,968, zero shared on 6090, and still zero after the padded-key
        # fold) and `effects_for_weapon` matches these keys by string prefix,
        # so injected u32 ids would match spuriously rather than merely
        # uselessly. The refusal is a *mechanism*, not a name check: any table
        # whose twin cannot be keyed takes the same path. The rows do
        # correspond -- within one mesh the ini's low three digits track the
        # twin's exactly, a constant +456 -- so what is missing is the
        # composition, not the data. The twin is exposed unmerged below rather
        # than hidden; docs/effects.md §9a tracks it as OPEN.
        self.weapon_motion = self._load_path_table("WeaponMotion.ini")
        self.weapon_motion_twin: dict[str, str] = (
            dbcshadow.twin_rows(self.ini / "WeaponMotion.ini") or {})
        #: What each flat table was actually read from, for callers that
        #: report numbers derived from them. Built from **this instance's own
        #: calls**, not from a snapshot of the module-level `FLAT_SOURCES`:
        #: that dict is keyed by filename, so two installs in one process
        #: overwrite each other and a snapshot attributes one base's
        #: provenance to another (C21).
        self.table_sources: dict[str, dict] = self._flat_records

        # -- weapon impact
        self.weapon_impact: dict[str, WeaponImpact] = {}
        for t, d in read_sections(self.ini / "WeaponEffect.ini").items():
            self.weapon_impact[t] = WeaponImpact(
                t, d.get("HitEffect", ""), d.get("HitSound", ""),
                d.get("BlkEffect", ""), d.get("BlkSound", ""))

        # -- action -> effect
        self.action_rules: list[ActionEffectRule] = []
        seen_rule = set()
        for key, val in read_dotted(self.ini / "Action3DEffect.ini"):
            if len(key) != 4:
                continue
            if (key, val) in seen_rule:
                continue
            seen_rule.add((key, val))
            self.action_rules.append(ActionEffectRule(*key, val))
        self._by_appearance: dict[str, list[ActionEffectRule]] = {}
        for r in self.action_rules:
            self._by_appearance.setdefault(r.appearance, []).append(r)

        self.action_map: list[ActionMapRule] = []
        # The key is shape+action+terrain with NO separators, so its total
        # length is the only thing that says how to cut it -- and the action
        # field is four wide on the official clients, exactly as in
        # `Action3DEffect.ini`. `len(sec) != 9` therefore used to drop **every
        # row** on 5517 and 6090: CCO/5017/5065 ship 60 keys and 5165 ships 62,
        # all 9 chars, while 5517 and 6090 ship 126 each at **10**. The whole
        # table was dark on both official clients and `lookup_action_map`
        # answered None for every query. Fifth site of C35's four-wide action
        # field; the widening lands between 5165 and 5517, the same place all
        # the others do.
        #
        # WHICH cut, MEASURED rather than assumed -- `104|0330|999` and
        # `1040|330|999` are the same ten digits, so the split was chosen on
        # evidence, not on the field order in the file's header:
        #   * every one of CCO's 60 keys has an exact official counterpart
        #     under "insert a 0 at index 3", and all 60 agree on `Effect`;
        #   * index 3 is `0` on all 126, which is what zero-padding a 3-digit
        #     action to 4 looks like and what a widened SHAPE would not do;
        #   * decisively, against this client's own `Action3DEffect.ini`
        #     vocabularies: 3/4/3 puts 116 of 126 shapes and 102 of 126 actions
        #     in them, while 4/3/3 puts **0 and 0**.
        # See CORRECTIONS C35.
        for sec, d in read_sections(self.ini / "ActionMap3DEffect.ini").items():
            if not sec.isdigit() or len(sec) not in (9, 10):
                continue
            act_end = len(sec) - 3                    # terrain is always 3
            self.action_map.append(ActionMapRule(
                sec[0:3], sec[3:act_end], sec[act_end:], d.get("Effect", ""),
                _int(d, "ShowTime"), _int(d, "DirEnable")))

        # -- ancillary
        self.action_delay: dict[str, dict[str, int]] = {}
        for sec, d in read_sections(self.ini / "ActionDelay.ini").items():
            self.action_delay[sec] = {k: _int(d, k) for k in
                                      ("WoundDelay", "BlockDelay", "DieDelay")}
        self.flying: dict[str, dict[str, str]] = read_sections(self.ini / "3DFlyingObj.ini")
        self.media_effect: dict[str, dict[str, str]] = read_sections(self.ini / "MediaEffect.ini")
        # Both spellings, because reading only the JSON left this empty on
        # every official client -- and the bare `except: pass` that used to
        # be here is what made it silent. See `read_weapon_skill_names`.
        self.weapon_type_names: dict[str, str] = read_weapon_skill_names(self.ini)
        self.weapon_appearances: dict[str, dict[str, str]] = \
            _weapon_appearances(self.ini)

    # -- loading helpers ---------------------------------------------------

    def _load_effects(self) -> tuple[dict[str, EffectDef], list[str]]:
        """The effect definitions, from the compiled twin where there is one.

        Returns ``(effects, duplicate_names)``.  **Names are not unique in the
        compiled table** -- 6090 ships 4,483 records under 4,472 names -- so a
        name-keyed dict has to pick.  It picks **the last record**, which is
        what `read_sections` does for a repeated ``[section]`` and therefore
        keeps the two paths consistent; the duplicates are returned rather
        than swallowed, because which one the client uses is **not decidable
        from this file** and a silent choice would look like knowledge.

        The compiled table is **substituted, not unioned**, with the ini --
        and that is the opposite of what `_load_path_table` does two methods
        down, deliberately, because they answer different questions.  Here the
        question is *what does the client define*, and where a twin exists the
        client reads the twin, so an ini-only definition is one the client
        does not have.  There (and in `asset_paths`) the question is *what
        assets exist*, where an ini-only row still names a real file and
        dropping it trades one blind spot for another.  Measured cost of
        substituting here: **0 names on 5517, 1 on 6090**
        (``red-flower-smallrain``).
        """
        p = self.ini / "3DEffect.ini"
        twin, data = _compiled_twin(p, b"EFFE")
        out: dict[str, EffectDef] = {}
        dupes: list[str] = []
        if data is not None:
            seen: set[str] = set()
            for rec in dbc.read_effe(data):
                if rec["name"] in seen:
                    dupes.append(rec["name"])
                seen.add(rec["name"])
                out[rec["name"]] = _effect_from_effe(rec)
            self.sources["3DEffect"] = TableSource(
                "3DEffect", twin, "dbc", len(out), twin=p,
                note=f"{len(seen) + len(dupes)} records, {len(out)} names")
        else:
            for name, d in read_sections(p).items():
                out[name] = _parse_effect_section(name, d, "3DEffect.ini")
            self.sources["3DEffect"] = TableSource(
                "3DEffect", p, "ini" if p.is_file() else "missing",
                len(out), twin=twin)
        return out, dupes

    def _load_path_table(self, ini_name: str) -> dict[str, str]:
        """An ``id -> asset path`` table, read the way the client reads it.

        Thin wrapper over `read_flat_live` -- **the overlay itself lives
        there**, including the disjoint-key refusal and the padded-key fold.
        This adds only the per-instance provenance record, taken from *this
        call* rather than from the module-level `FLAT_SOURCES` snapshot.

        The overlay is a **union with the twin winning**, not a substitution.
        Two teams built it independently and measured the same growth
        (`3DEffectObj` 3,268 -> 6,268 / 9,472, `3dtexture` 8,793 -> 13,803 /
        18,638, `3dobj` 1,443 -> 2,125 / 2,985), and the union is the right
        half of the two: an ini-only row still names a real asset, and the
        twin still wins wherever both have the id, so the six paths that
        changed between the two files resolve to the compiled value either
        way. Measured cost of preferring union over substitution on the
        effect path: **0 layers resolve differently** on either base.

        `form` in the record is `"dbc"` when the overlay happened, `"ini"`
        when there was no twin **or the overlay was refused**, which is the
        `WeaponMotion` case -- a twin that exists and cannot be keyed reads
        as plaintext here on purpose, and the record says `disjoint` so the
        reason is in the output rather than in a comment.
        """
        p = self.ini / ini_name
        out, rec = read_flat_live_reported(p)
        self._flat_records[p.name] = rec
        twin = dbcshadow.compiled_twin(p)
        note = ""
        if rec.get("disjoint"):
            note = (f"twin present but its key space is disjoint "
                    f"({rec['ini_keys']} ini vs {rec['twin_keys']} twin ids, "
                    f"0 shared); overlay refused, plaintext kept")
        elif rec.get("padded_ini_keys_folded"):
            note = (f"{rec['padded_ini_keys_folded']} zero-padded ini keys "
                    f"folded onto the twin's spelling")
        self.sources[p.stem] = TableSource(
            p.stem, twin if rec["overlaid"] else p,
            "dbc" if rec["overlaid"] else ("ini" if p.is_file() else "missing"),
            len(out), twin=(p if rec["overlaid"] else twin), note=note)
        return out

    def _load_effect_json(self) -> list[EffectDef]:
        p = self.ini / "3DEffect.json"
        if not p.is_file():
            return []
        try:
            rows = json.loads(p.read_text("utf-8-sig"))
        except Exception:                                     # pragma: no cover
            return []
        out = []
        for row in rows:
            off = row.get("Offset") or {}
            e = EffectDef(
                name=row.get("Name", ""),
                amount=len(row.get("Effects") or []),
                delay=int(row.get("Delay", 0)),
                loop_time=int(row.get("LoopTime", 1)),
                loop_interval=int(row.get("LoopInterval", 0)),
                frame_interval=int(row.get("FrameInterval", 33)),
                offset=(float(off.get("X", 0)), float(off.get("Y", 0)),
                        float(off.get("Z", 0))),
                color_enable=bool(row.get("ColorEnable", False)),
                source="3DEffect.json",
            )
            for i, lay in enumerate(row.get("Effects") or []):
                e.layers.append(EffectLayer(
                    index=i,
                    effect_id=str(lay.get("Effect", "")),
                    texture_id=str(lay.get("Texture", "")),
                    asb=int(lay.get("ASB", 5)), adb=int(lay.get("ADB", 6))))
            out.append(e)
        return out

    # -- weapon queries ----------------------------------------------------

    @staticmethod
    def split_appearance(appearance: str) -> tuple[str, str]:
        """``410009`` -> ``('410', '009')``.  The last three digits are the
        style+quality group; everything before is the weapon type."""
        a = str(appearance).strip()
        return (a[:-3], a[-3:]) if len(a) > 3 else (a, "")

    def weapon_type_of(self, appearance: str) -> str:
        return self.split_appearance(appearance)[0]

    def rules_for_appearance(self, appearance: str) -> list[ActionEffectRule]:
        hi, lo = self.split_appearance(appearance)
        out = list(self._by_appearance.get(hi + lo, []))
        # wildcard on the low group -- INFERRED, no such row ships today for
        # weapons but the format allows it and ActionMap documents 999.
        out += [r for r in self._by_appearance.get(hi + WILDCARD, [])]
        return out

    def effects_for_weapon(self, appearance: str, *, shape: str = WILDCARD) -> WeaponEffectSet:
        """Everything keyed off one weapon appearance ID (e.g. ``410009``)."""
        appearance = str(appearance).strip()
        hi, _lo = self.split_appearance(appearance)
        es = WeaponEffectSet(
            appearance=appearance, weapon_type=hi,
            type_name=self.weapon_type_names.get(hi, ""),
            impact=self.weapon_impact.get(hi),
            in_weapon_ini=appearance in self.weapon_appearances)
        for r in self.rules_for_appearance(appearance):
            if r.shape not in (WILDCARD, shape):
                continue
            # `is_always_on`, not `== WILDCARD`: on both official clients the
            # aura rows are keyed `9999` and this branch used to miss every
            # one of them, filing 972 (5517) / 2,604 (6090) always-on rows in
            # `attack` under a bogus action code and reporting **0 of 4,828**
            # weapon auras where CCO reports 1,286 of 5,384.
            if is_always_on(r.action):
                if not es.aura or r.effect.lower() != "none":
                    es.aura = r.effect
            else:
                es.attack[r.action] = r.effect
        # `none` is an explicit "no effect" row (the hurt/die actions carry it on
        # every weapon).  It means the same as no row at all here, so drop it
        # from this convenience view; the raw rows are still in `action_rules`.
        es.attack = {k: v for k, v in sorted(es.attack.items())
                     if v.lower() != "none"}
        if es.aura.lower() == "none":
            es.aura = ""
        for mid, mesh in self.weapon_motion.items():
            if mid.startswith(appearance):
                es.motion_meshes[mid[len(appearance):]] = mesh
        return es

    @staticmethod
    def _field_matches(rule_val: str, query: str) -> bool:
        r"""One key field, compared so that a padding change cannot hide a row.

        **CCO writes the action field three wide and 6090 writes it four,
        zero-padded** -- `999.100.135.999` against `999.0100.130.300`, over
        10,267 of 10,299 rows. A string comparison therefore misses every
        non-wildcard row on 6090, which is why no weapon reported an
        always-on aura and every super effect went quiet. Same number, same
        meaning, different width; compare numerically when both sides are
        numeric and fall back to the literal match when they are not.
        """
        if rule_val == WILDCARD or rule_val == query:
            return True
        if rule_val.isdigit() and query.isdigit():
            return int(rule_val) == int(query)
        return False

    def lookup_action_effect(self, appearance: str, action: str,
                             shape: str = WILDCARD) -> Optional[str]:
        """Most specific matching Action3DEffect row, or None."""
        hi, lo = self.split_appearance(appearance)
        best: Optional[ActionEffectRule] = None
        for r in self.action_rules:
            if not self._field_matches(r.shape, shape):
                continue
            if not self._field_matches(r.action, action):
                continue
            if not self._field_matches(r.group_hi, hi):
                continue
            if not self._field_matches(r.group_lo, lo):
                continue
            if best is None or r.specificity > best.specificity:
                best = r
        return best.effect if best else None

    def always_on_effect(self, appearance: str,
                         shape: str = WILDCARD) -> Optional[str]:
        r"""The always-on (aura) row for an appearance, **width-agnostic**.

        `lookup_action_effect` needs the caller to spell the action, and the
        always-on action is the one field whose spelling changes between
        clients (`999` on CCO, `9999` on 5517 and 6090). Every caller that
        spelled it itself was silently returning nothing on both official
        clients. Asking here instead means no caller carries the sentinel.

        Identical to `lookup_action_effect(appearance, <that client's
        spelling>)` on all three bases -- asserted in
        `test_viewer::AlwaysOnSentinelWidth`, not assumed -- because the
        candidate set is the same set either way. The difference is only that
        this one cannot be given the wrong width.
        """
        hi, lo = self.split_appearance(appearance)
        best: Optional[ActionEffectRule] = None
        for r in self.action_rules:
            if not is_always_on(r.action):
                continue
            if not self._field_matches(r.shape, shape):
                continue
            if not self._field_matches(r.group_hi, hi):
                continue
            if not self._field_matches(r.group_lo, lo):
                continue
            if best is None or r.specificity > best.specificity:
                best = r
        return best.effect if best else None

    def always_on_rules(self) -> list[ActionEffectRule]:
        """Every always-on row, whatever this client's wildcard width is."""
        return [r for r in self.action_rules if is_always_on(r.action)]

    def lookup_action_map(self, shape: str, action: str,
                          terrain: str) -> Optional[ActionMapRule]:
        """Most specific matching ActionMap3DEffect row, or None.

        Key format, from the file's own GBK header comment: nine digits in three
        groups of three -- shape (外形), action (动作), terrain (地形).  "999" is
        a wildcard and two wildcards may appear at once.  **The header is the
        CCO-era shape**; 5517 and 6090 write the action group four wide, so the
        key is ten digits there.  `EffectDB.__init__` cuts it accordingly.

        `_field_matches`, not a literal `in (WILDCARD, ...)`, for the same
        reason `lookup_action_effect` uses it: a caller spelling the action
        `330` must reach a rule spelling it `0330`.  Getting the rows parsed
        without this would have been a fix that changed the row count and not
        the answers.  MEASURED on all three bases -- see CORRECTIONS C35.

        Note the action group here is never all-nines on any base (0 of 60 on
        CCO, 0 of 126 on both official clients), so the *sentinel* half of C35
        does not arise in this table; shape and terrain wildcards are `999`
        everywhere and `WILDCARD` is right for them.
        """
        best: Optional[ActionMapRule] = None
        for r in self.action_map:
            if not self._field_matches(r.shape, str(shape)):
                continue
            if not self._field_matches(r.action, str(action)):
                continue
            if not self._field_matches(r.terrain, str(terrain)):
                continue
            if best is None or r.specificity > best.specificity:
                best = r
        return best

    # -- effect resolution -------------------------------------------------

    def _map_effect(self, name: str):
        """A MAP effect definition from `ini/c3.wdb`'s own EFFE section.

        **A SEPARATE TABLE FROM `self.effects`, AND THE SEPARATION IS THE
        POINT.** `self.effects` means *what `3DEffect.ini` (or its compiled
        twin) defines*, and several gates pin measurements over exactly that
        population -- `test_effect_names_resolve` stamps per-base resolution
        counts and asserts the plaintext table is frozen across the modern
        range.

        The first version of this merged `c3.wdb`'s ~12,000 EFFE names into
        `self.effects`. It worked, and it took that gate from 1 failure to 3:
        **widening a table silently changes the meaning of every measurement
        taken over it.** The gate's own message says "do not reconcile the
        numbers to the code", and it was right -- the defect was the merge,
        not the numbers.

        So map effects live here, and `resolve()` consults this only when the
        name is not in `3DEffect.ini`. Nothing that counts `self.effects`
        moves.

        **Why the map effects are here at all:** the `.DMap` places 10,172
        tag-19 EFFECT records across 115 maps, and **1 of 377 distinct names
        is in `3DEffect.ini`** (denominator control run: invented names
        correctly fail). 325 of 377 are in this section, 97.5% by placement.
        Same record format, so `_effect_from_effe` reads it unchanged.
        """
        if getattr(self, "_map_fx", None) is None:
            out: dict = {}
            try:
                wp = self.ini / "c3.wdb"
                if wp.is_file():
                    import wdb as _wdb                      # noqa: PLC0415
                    raw = wp.read_bytes()
                    for sec in _wdb.ResourceDb(wp).section_table:
                        if sec.get("tag") != "EFFE":
                            continue
                        o_, sp = int(sec["offset"]), int(sec["span"])
                        for rec in dbc.read_effe(raw[o_:o_ + sp]):
                            nm = rec.get("name")
                            if nm and nm not in out:
                                e = _effect_from_effe(rec)
                                e.source = "c3.wdb:EFFE"
                                out[nm] = e
                        break
            except Exception:                               # noqa: BLE001
                # A client with no c3.wdb must still serve the effects it has.
                out = {}
            self._map_fx = out
        return self._map_fx.get(name)

    def _rsdb_path(self, ident) -> str:
        """An asset id resolved through `ini/c3.wdb`'s RSDB, or `''`.

        The fallback for ids that are not in the plaintext path tables --
        which is every id a MAP effect carries. Built lazily and cached,
        because a client without `c3.wdb` must still serve its other effects
        and opening a 20 MB database to answer nothing would be the wrong
        trade.
        """
        if getattr(self, "_rsdb", None) is None:
            try:
                import wdb as _wdb                          # noqa: PLC0415
                wp = self.ini / "c3.wdb"
                self._rsdb = _wdb.ResourceDb(wp) if wp.is_file() else False
            except Exception:                               # noqa: BLE001
                self._rsdb = False
        if not self._rsdb:
            return ""
        try:
            return self._rsdb.path_for(int(str(ident).strip() or 0)) or ""
        except Exception:                                   # noqa: BLE001
            return ""

    def _rsdb_rows(self, ident) -> list:
        """EVERY path the RSDB holds for one id, in file order.

        **AN ID IS NOT A PATH. IT IS A ROW IN SEVERAL TYPED TABLES.**
        `ini/c3.wdb` lays 11 sections back to back sharing one id space --
        section 1 is effect meshes, section 2 is THE texture table and the
        only section holding a `.dds`, the rest are meshes and motion by
        category. Sections 1 and 2 share 66,876 ids, and that overlap IS the
        "one id, two assets" relation. `path_for` returns the first row, so
        a texture id answered with its MESH every time.

        `wdb.ResourceDb.rows_for` owns the index; this is the lazy-open
        wrapper, so a client with no `c3.wdb` still serves its other effects.
        """
        self._rsdb_path(0)                       # owns the lazy open
        db = getattr(self, "_rsdb", False)
        if not db or not hasattr(db, "rows_for"):
            return []
        try:
            return db.rows_for(ident)
        except Exception:                                   # noqa: BLE001
            return []

    def _rsdb_pick(self, ident, ext: str) -> str:
        """The one row of `ident` whose path ends in `ext`, or `''`.

        MEASURED UNAMBIGUOUS on the population that needs it: of the 1,182
        map-effect layer texture ids on 7878, **every single one has exactly
        one `.dds` row** -- never two, never none. So this is a lookup, not a
        choice, and it replaces the extension-guessing it used to do.
        """
        self._rsdb_path(0)
        db = getattr(self, "_rsdb", False)
        if not db or not hasattr(db, "rows_for"):
            return ""
        try:
            hit = db.rows_for(ident, ext)
        except Exception:                                   # noqa: BLE001
            return ""
        return hit[0] if hit else ""

    @staticmethod
    def _table_get(table: dict[str, str], key: str) -> str:
        """One id looked up so a **padding difference cannot read as absence.**

        The gate is per file, so a base may legitimately serve the effect
        definitions compiled (ids as plain ints) and the path tables plaintext
        (ids sometimes zero-padded, 46 of them in `3dtexture.ini`), or the
        reverse.  A raw string lookup across that boundary misses every padded
        row and reports "the table does not have it" -- the failure mode
        docs/CORRECTIONS.md §2 records four separate instances of.  Try the
        literal spelling first, then the canonical integer one.
        """
        if key in table:
            return table[key]
        if key.isdigit():
            return table.get(str(int(key)), "")
        return ""

    def resolve(self, name: str, *, map_fx: bool = False
                ) -> Optional[EffectDef]:
        """Return a copy of the effect definition with asset paths filled in.

        `map_fx` OPTS IN to the second population: `c3.wdb`'s EFFE section
        and the RSDB path space that its layer ids live in.

        **IT DEFAULTS OFF, AND THE DEFAULT IS THE WHOLE POINT.** I reached
        this signature by breaking two gates in a row with the same mistake
        at two different depths. First I merged EFFE into `self.effects`;
        `test_effect_names_resolve` went 1 failure -> 3. I made it a separate
        table -- and `test_effect_closure` then failed on `horse_grid`/5165,
        because a separate table consulted UNCONDITIONALLY is still a wider
        population to everything downstream of `resolve`.

        The measurement that settles it, taken across the corpus:

            client   EFFE recs   horse_grid in c3.wdb
            5017          --     (no c3.wdb)
            5165       2,734     5 layers      <- `3DEffect.ini`: ABSENT
            5517       2,747     5 layers
            6090       2,747     5 layers
            6609       5,393     0 layers
            7205       7,201     0 layers
            7878      14,378     0 layers      <- `3DEffect.ini`: ABSENT

        So the gate is not stale and its numbers are not wrong: it measures
        **the `3DEffect.ini` lineage**, says so in its own docstring, and on
        that population `horse_grid` really is absent from 5165 and 7878.
        `c3.wdb` tells a fuller story -- the same 5-layer -> 0-layer emptying
        happens at a DIFFERENT boundary there (6090 -> 6609, not 5517 ->
        6090) -- but that is a second finding about a second file, not a
        correction to the first, and it must not silently rewrite what every
        existing caller counts.

        Map-effect callers pass `map_fx=True`. Nothing else changes.
        """
        base = self.effects.get(name)
        if base is None and map_fx:
            # Not in `3DEffect.ini`: it may be a MAP effect, which lives in
            # `c3.wdb`'s EFFE section. See `_map_effect` for why that is a
            # separate table and not a merge.
            base = self._map_effect(name)
        if base is None:
            return None
        e = EffectDef(**{**asdict(base), "layers": []})
        for lay in base.layers:
            L = EffectLayer(**asdict(lay))
            raw_mesh = self._table_get(self.objs, L.effect_id)
            if not raw_mesh and map_fx:
                # **A MAP EFFECT'S IDS ARE IN A DIFFERENT SPACE.**
                # `3DEffectObj.ini` is keyed by small ints ('1','2','3', 3,268
                # of them); a `c3.wdb` EFFE layer carries ids like 22371 that
                # resolve through the wdb's own RSDB to `c3/effect/.../N.c3`.
                # Measured: 1,163 of 1,163 such ids resolve to a path, and
                # every sampled file is on disk.
                raw_mesh = (self._rsdb_pick(L.effect_id, ".c3")
                            or self._rsdb_path(L.effect_id))
            L.mesh_path = raw_mesh.replace("\\", "/")
            L.mesh_found = bool(L.mesh_path) and self.assets.exists(L.mesh_path)
            raw_tex = self._table_get(self.textures, L.texture_id)
            if not raw_tex and map_fx:
                # **THE ID CARRIES ITS OWN `.dds` ROW. TAKE IT.**
                #
                # This used to resolve the id with `path_for`, get the `.c3`
                # back, and then guess the texture by swapping the extension
                # -- because `path_for` returns only the FIRST of an id's
                # rows and I did not know there were others. The database
                # says it plainly:
                #
                #     id 81953 -> c3/effect/lua/2024/2024qqcj13/8.c3
                #                 c3/effect/map/lhd_lw1/1.dds
                #
                # The swap produced `2024qqcj13/8.dds`, a different effect's
                # texture, which alpha-blended over a banner mesh is the
                # black diamond on `2024tsf_new`. It was also quietly wrong
                # where it "worked": `AirWall_gold`'s id resolves to
                # `airwall_blue/1.c3` and `airwall_GOLD/1.dds`, and the swap
                # handed it the BLUE one.
                #
                # Measured over all 1,182 map-effect layers on 7878: every
                # texture id has EXACTLY ONE `.dds` row -- never two, never
                # none -- so there is nothing to choose. 540 of the 1,182
                # change, and the other 642 were already right by luck of
                # the two files sharing a folder.
                raw_tex = self._rsdb_pick(L.texture_id, ".dds")
            L.texture_path = raw_tex.replace("\\", "/")
            L.texture_found = bool(L.texture_path) and self.assets.exists(L.texture_path)
            e.layers.append(L)
        return e

    def load_geometry(self, name: str) -> list[EffectObject]:
        """Resolve an effect and load every layer's C3, decoded and playable."""
        e = self.resolve(name)
        if e is None:
            return []
        return [load_effect_object(self.assets, L.effect_id, L.mesh_path)
                for L in e.layers if L.mesh_path]

    # -- the three animation forms, per container and per effect -----------

    def container_forms_cached(self, logical: str) -> tuple[set[str], str]:
        r"""`(forms, status)` for one logical C3 path, memoised per instance.

        Memoised because effects SHARE containers: 5517's 3,391 effects name
        4,584 distinct containers between them but resolve far more layers
        than that, and 6609's 5,290 effects name 10,390.  Without the cache
        the corpus scan re-reads the same archive member dozens of times.

        `status` is one of the `LAYER_*` constants and is returned rather than
        folded into an empty set, because "no form" and "no file" are
        different answers (see the note on `LAYER_OK`).
        """
        if logical in self._form_cache:
            return self._form_cache[logical]
        try:
            data = self.assets.read(logical)
        except Exception:
            hit = (set(), LAYER_MISSING)
        else:
            if data is None:
                hit = (set(), LAYER_MISSING)
            else:
                try:
                    hit = (container_forms(data), LAYER_OK)
                except Exception:
                    hit = (set(), LAYER_UNREADABLE)
        self._form_cache[logical] = hit
        return hit

    def forms_for(self, name: str) -> Optional[dict]:
        r"""Every animation form one effect NAME carries, across all its layers.

        Returns ``None`` when the name is not in this install's effect table --
        distinct from an effect that resolves to no form at all, which returns
        a record with an empty ``forms``.

        ``forms`` is the UNION over the layers.  An effect whose first layer is
        a PHY mesh and whose second is a particle system is BOTH, and shows up
        under either filter.
        """
        e = self.resolve(name)
        if e is None:
            return None
        layers = []
        union: set[str] = set()
        for L in e.layers:
            if not L.mesh_path:
                layers.append({"index": L.index, "effect_id": L.effect_id,
                               "mesh_path": "", "forms": [],
                               "status": LAYER_NO_PATH})
                continue
            f, st = self.container_forms_cached(L.mesh_path)
            union |= f
            layers.append({"index": L.index, "effect_id": L.effect_id,
                           "mesh_path": L.mesh_path, "forms": sorted(f),
                           "status": st})
        return {"name": name, "forms": [f for f in FORMS if f in union],
                "layers": layers,
                "unresolved": sum(1 for L in layers if L["status"] != LAYER_OK)}

    def form_index(self) -> dict[str, set[str]]:
        """effect name -> its form set, for every effect in this install.

        Built once per instance.  MEASURED 2026-09-07 (cold archive cache):
        5017 12.5 s / 1,791 containers, 5517 13.0 s / 4,584, 6090 25.7 s /
        7,350, 6609 107.5 s / 10,390, 7205 89.6 s / 10,390, 7878 16.9 s /
        2,380.  That cost is why this is a method you call rather than
        something `EffectDB.__init__` does, and why `comod catalogs` does not
        build it.
        """
        if self._form_index is None:
            self._form_index = {
                n: set(self.forms_for(n)["forms"]) for n in self.effects}
        return self._form_index

    def frames_for(self, name: str) -> tuple[int, int]:
        """``(motion_frames, effective_frames)`` across every layer of an effect."""
        total = eff = 0
        for obj in self.load_geometry(name):
            total = max(total, obj.frame_count)
            eff = max(eff, obj.effective_frames)
        return total, eff

    def duration_ms(self, name: str, *, effective: bool = True) -> Optional[float]:
        """Wall-clock length of one playthrough, or None when endless.

        ``delay + loops * (frames * frameInterval) + (loops - 1) * loopInterval``.

        ``frames`` is the alpha-envelope length by default, because effect meshes
        habitually declare a 101-frame motion track and fade out after ~10.
        INFERRED -- the timer itself lives in the packed exe -- but every field it
        uses is read by GraphicData.dll, so the shape of the formula is not a guess.
        """
        e = self.resolve(name)
        if e is None or e.endless:
            return None
        total, effn = self.frames_for(name)
        frames = (effn or total) if effective else total
        if not frames:
            return None
        one = frames * e.frame_interval
        return e.delay + e.loop_time * one + max(0, e.loop_time - 1) * e.loop_interval

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Release the asset root IF THIS INSTANCE OPENED IT.

        Constructing an `EffectDB` with no `assets=` opens every container in
        the install (MEASURED on 5517: `c3.wdf` + `data.wdf`), and before this
        existed no caller could ever hand them back -- the only reference that
        could was gone the moment the instance was dropped.

        THE FLAG IS THE WHOLE POINT, not bookkeeping: a `close()` that closed a
        BORROWED root would be the worse bug.  See `_owns_assets`.

        `self.assets` IS DELIBERATELY LEFT BOUND.  Every method here reads
        through it, and `AssetRoot.close()` already leaves `_archives`
        populated so a use-after-close raises `ValueError: mmap closed` --
        which names the bug.  Unbinding it would turn that into an
        `AttributeError` from an unrelated line, and emptying it would answer
        "this install ships no such mesh", which is worse still.  Clearing the
        flag instead is what makes this idempotent at this level.
        """
        if self._owns_assets:
            self._owns_assets = False
            try:
                self.assets.close()
            except Exception:                              # noqa: BLE001
                pass

    def __enter__(self) -> "EffectDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _base_identity(db: EffectDB) -> dict:
    """Which install these numbers are *about*, emitted beside every number.

    A tool that resolves a root silently reports the wrong base's answer with
    no error at all (docs/CORRECTIONS.md C21, C22, C33).  Stating the resolved
    root and its `base_id` in the output turns that into something a reader
    can catch: two bases with identical counts *and* an identical `root` are
    one measurement printed twice.
    """
    try:
        base = coroot.base_id(db.root)
    except Exception as exc:                                  # pragma: no cover
        base = f"<unavailable: {exc}>"
    return {"root": str(db.root), "base_id": base,
            "tables_read": {k: v.as_dict() for k, v in sorted(db.sources.items())}}


def referenced_effect_names(db: EffectDB) -> set:
    r"""Every effect name a RULE TABLE points at, `none` excluded.

    `none` is an explicit "no effect" row -- the hurt/die actions carry it on
    every weapon -- so counting it would manufacture a dangling reference on
    every base in the corpus.
    """
    ref: set = set()
    for r in db.action_rules:
        ref.add(r.effect)
    for r in db.action_map:
        ref.add(r.effect)
    for w in db.weapon_impact.values():
        ref.update((w.hit_effect, w.blk_effect))
    return {n for n in ref if n and n.strip().lower() != "none"}


def dangling_effect_names(db: EffectDB) -> list:
    r"""Names a rule table points at that `3DEffect` does NOT define.

    A FOURTH state, and it is not "a broken effect": it is a BROKEN RULE.
    The art is not missing -- the rule names something that was never
    authored, and the client has nothing to play for those rows. Calling it a
    broken effect sends someone hunting for a mesh that was never referenced.

    32 such names on 5517. **They are absent from `EffectDB.effects`**, which
    is what every list in this project is built from, so nothing that
    enumerates effects can surface one of these by filtering; a caller that
    wants to show them has to ask for them by name. That is why this is a
    function rather than a line inside `coverage`.
    """
    return sorted(referenced_effect_names(db) - set(db.effects))


def coverage(db: EffectDB) -> dict:
    """The numbers quoted in docs/effects.md."""
    out: dict = dict(_base_identity(db))

    types = sorted(db.weapon_impact)
    out["weapon_types_with_impact"] = len(types)
    out["weapon_types_in_skill_table"] = len(db.weapon_type_names)
    out["weapon_types_missing_impact"] = sorted(
        set(db.weapon_type_names) - set(types))

    apps = sorted(db.weapon_appearances)
    with_aura = with_attack = with_impact = 0
    per_type: dict[str, list[int]] = {}
    for a in apps:
        es = db.effects_for_weapon(a)
        hit = per_type.setdefault(es.weapon_type, [0, 0])
        hit[1] += 1
        if es.aura:
            with_aura += 1
        if es.attack:
            with_attack += 1
            hit[0] += 1
        if es.impact:
            with_impact += 1
    out["weapon_appearances"] = len(apps)
    out["weapon_appearances_with_attack_trail"] = with_attack
    out["weapon_appearances_with_aura"] = with_aura
    out["weapon_appearances_with_impact"] = with_impact
    out["per_type_attack_coverage"] = {
        t: {"with_trail": v[0], "appearances": v[1]}
        for t, v in sorted(per_type.items())}

    # effect name resolution. Deferred to `referenced_effect_names` /
    # `dangling_effect_names` rather than repeated here: the viewer needs the
    # same answer, and two spellings of one set is how this project's
    # sentinel bugs have started every time.
    out["effect_names_referenced"] = len(referenced_effect_names(db))
    out["effect_names_defined"] = len(db.effects)
    out["effect_names_referenced_but_undefined"] = dangling_effect_names(db)

    # The full/partial/broken rule is `effect_state`, NOT a copy of it. It was
    # lifted out of this loop when the Effects Viewer needed the same answer
    # per effect: two copies of a classification drift the first time either
    # is touched, and the drift is invisible because both sides keep printing
    # numbers. The bucket definitions are unchanged -- an effect declaring no
    # layers still counts as `broken` here, and its size is published as
    # `effects_declaring_no_layers` rather than moved, so no number quoted in
    # docs/effects.md is silently restated.
    layers = meshes_ok = texes_ok = 0
    full = partial = broken = no_layers = 0
    for name in sorted(db.effects):
        e = db.resolve(name)
        st = effect_state(e)
        if st == EFFECT_WHOLE:
            full += 1
        elif st == EFFECT_PARTIAL:
            partial += 1
        else:
            broken += 1
        if e is None or not e.layers:
            no_layers += 1
            continue
        for L in e.layers:
            layers += 1
            meshes_ok += L.mesh_found
            texes_ok += L.texture_found
    out["effect_layers_total"] = layers
    out["effect_layers_mesh_found"] = meshes_ok
    out["effect_layers_texture_found"] = texes_ok
    out["effects_fully_resolved"] = full
    out["effects_partially_resolved"] = partial
    out["effects_unresolved"] = broken
    #: The share of `effects_unresolved` that is a definition declaring NO
    #: layers at all -- a fact about the table, not a missing file. Both are
    #: "broken" and they are repaired differently.
    out["effects_declaring_no_layers"] = no_layers
    out["json_only_effect_names"] = len(db.json_only)
    # Names are unique in the plaintext table and in 5517's compiled one, and
    # NOT in 6090's -- printed so a name-keyed count is never mistaken for a
    # record count.
    out["duplicate_effect_names"] = len(db.duplicate_effect_names)

    # which animation form backs each 3DEffectObj entry
    kinds: dict[str, int] = {}
    obj_kind: dict[str, set[str]] = {}
    obj_ok: dict[str, bool] = {}
    obj_undecoded: dict[str, bool] = {}
    ptcl_chunks = ptcl_exact = 0
    for oid, path in db.objs.items():
        o = load_effect_object(db.assets, oid, path)
        obj_kind[oid] = o.kinds
        obj_ok[oid] = o.playable
        obj_undecoded[oid] = bool(o.undecoded)
        for p in o.parts:
            if p.kind == PART_PARTICLE:
                ptcl_chunks += 1
                ptcl_exact += bool(p.particle and p.particle.exact)
        k = "+".join(sorted(o.kinds)) or ("missing" if o.error else "empty")
        kinds[k] = kinds.get(k, 0) + 1
    out["effect_objects_total"] = len(db.objs)
    out["effect_objects_by_animation_form"] = dict(sorted(kinds.items()))
    out["particle_chunks_in_effect_objects"] = ptcl_chunks
    out["particle_chunks_exact"] = ptcl_exact

    # and what that means for the effects that reference them.
    #
    # Two classifications are emitted from ONE pass, deliberately.  The
    # `*_geometry_only` numbers reproduce the pre-particle-reader definition
    # -- "playable" meant "has no particle layer" -- so the before/after
    # comparison in docs/effects.md §9 is two numbers from one run of one
    # build, rather than two runs of two builds against a moving corpus.
    playable = geom_only = particle_only = mixed = unknown = undecoded = 0
    reader_gap = asset_gap = 0
    for name in sorted(db.effects):
        e = db.resolve(name)
        ks: set[str] = set()
        ok = True
        gap_reader = gap_asset = False
        for L in e.layers:
            ks |= obj_kind.get(L.effect_id, set())
            if L.effect_id in obj_ok and not obj_ok[L.effect_id]:
                ok = False
                if obj_undecoded.get(L.effect_id):
                    gap_reader = True
                else:
                    gap_asset = True
        if not ks:
            unknown += 1
            continue
        if ks == {PART_PARTICLE}:
            particle_only += 1
        elif PART_PARTICLE in ks:
            mixed += 1
        else:
            geom_only += 1
        if ok:
            playable += 1
        else:
            undecoded += 1
            reader_gap += gap_reader
            asset_gap += gap_asset and not gap_reader
    out["effects_playable_today"] = playable
    out["effects_playable_geometry_only"] = geom_only
    out["effects_particle_only"] = particle_only
    out["effects_partly_particle"] = mixed
    out["effects_needing_particles"] = particle_only + mixed
    # **This key conflates two unrelated failures and is kept as it was so the
    # before/after in docs/effects.md §9 compares like with like.**  It counts
    # an effect that has *some* decodable geometry and *some* unplayable
    # layer -- and a layer whose C3 is simply absent from the install counts
    # here too, which the name does not say.  6090's single hit is exactly
    # that: `800540` names eight layers, seven of whose C3s do not exist and
    # one of which points at `800530/8.c3`, another effect's file.  A data
    # bug, not a reader gap.  The two causes are split out below; quote those.
    out["effects_with_undecoded_chunk"] = undecoded
    out["effects_blocked_by_undecoded_chunk"] = reader_gap
    out["effects_blocked_by_missing_asset"] = asset_gap
    out["effects_no_geometry"] = unknown

    # the subset the viewer actually needs: effects reachable from a weapon
    weapon_effects: set[str] = set()
    for a in apps:
        weapon_effects.update(db.effects_for_weapon(a).effect_names)
    wp = wgeom = wpart = wmiss = 0
    for n in sorted(weapon_effects):
        e = db.resolve(n)
        if e is None:
            wmiss += 1
            continue
        ks: set[str] = set()
        ok = True
        for L in e.layers:
            ks |= obj_kind.get(L.effect_id, set())
            if L.effect_id in obj_ok and not obj_ok[L.effect_id]:
                ok = False
        if not ks:
            wmiss += 1
            continue
        if PART_PARTICLE in ks:
            wpart += 1
        else:
            wgeom += 1
        wp += ok
    out["weapon_reachable_effects"] = len(weapon_effects)
    out["weapon_reachable_playable"] = wp
    out["weapon_reachable_geometry_only"] = wgeom
    out["weapon_reachable_partly_particle"] = wpart
    out["weapon_reachable_unresolved"] = wmiss
    return out


#: Every ini in this install whose values are asset paths.  Together they name
#: every C3 the client can reach by name -- **on a base that has no compiled
#: twins**.  See `PATH_DBCS` and `asset_paths`: on patch5517/6090 this set is
#: badly incomplete, and two of its members are zero bytes long.
PATH_INIS = ("3DEffectObj.ini", "WeaponMotion.ini", "3dobj.ini", "3dmotion.ini",
             "miscmotion.ini", "MountMotion.ini")

# ---------------------------------------------------------------------------
# filtering by animation form
# ---------------------------------------------------------------------------

def filter_effects(db: EffectDB, *, forms: Optional[Iterable[str]] = None,
                   mode: str = "any", match: str = "") -> list[dict]:
    r"""Every effect in this install, optionally narrowed to a form set.

    ``forms`` is a set of canonical form names (`FORMS`); pass ``None`` or an
    empty iterable for "no form filter", which is NOT the same as "match
    nothing".

    ``mode``:
      * ``"any"`` (default) -- the effect carries AT LEAST ONE of the named
        forms.  ``--form ribbon --form particle`` is "ribbons or particles".
      * ``"all"`` -- it carries ALL of them.  This is how you find the
        multi-form effects: ``--form phy --form ribbon --form-mode all``
        selects effects like 5517's `410239`.
      * ``"none"`` -- it carries none of them.

    **Set membership throughout.**  An effect carrying two forms is returned by
    a query for either one; nothing here picks a "primary" form.

    ``match`` is a case-insensitive substring of the effect name.
    """
    want = {f for f in (forms or ())}
    bad = want - set(FORMS)
    if bad:
        raise UnknownForm(f"not a form: {sorted(bad)}")
    if mode not in ("any", "all", "none"):
        raise ValueError(f"mode must be any/all/none, not {mode!r}")
    idx = db.form_index()
    needle = (match or "").lower()
    rows = []
    for name in sorted(idx):
        if needle and needle not in name.lower():
            continue
        got = idx[name]
        if want:
            if mode == "any" and not (got & want):
                continue
            if mode == "all" and not want <= got:
                continue
            if mode == "none" and (got & want):
                continue
        rows.append({"name": name, "forms": [f for f in FORMS if f in got]})
    return rows


def form_census(db: EffectDB) -> dict:
    r"""How many effects carry each form, in this install.

    **All three rows are always present**, so an absent form is a measured
    zero rather than a missing line -- the same rule `comod anim`'s three-row
    block follows.

    The per-form counts do NOT sum to ``effects``: an effect carrying two
    forms is counted under both.  ``multi_form`` says how many, and
    ``combinations`` breaks them out, so the overlap is visible instead of
    being an unexplained discrepancy a reader has to guess at.

    ``no_form`` is split: ``bare`` read fine and carried none of the three,
    ``unresolved`` named containers this install does not ship.  On 7878 that
    distinction is most of the answer (MEASURED 2026-09-07: 1,521 of 2,595
    effects carry no form, and it is the table naming assets the install does
    not ship, not the client shipping fewer animated effects).
    """
    idx = db.form_index()
    counts = {f: 0 for f in FORMS}
    combos: dict[str, int] = {}
    multi = 0
    bare = unresolved = 0
    for name, got in idx.items():
        for f in got:
            counts[f] += 1
        if len(got) > 1:
            multi += 1
        key = " + ".join(f for f in FORMS if f in got) or "(none)"
        combos[key] = combos.get(key, 0) + 1
        if not got:
            rec = db.forms_for(name)
            if rec and rec["unresolved"]:
                unresolved += 1
            else:
                bare += 1
    return {
        "root": str(db.root),
        "base_id": _base_identity(db).get("base_id", ""),
        "effects": len(idx),
        "by_form": counts,
        "multi_form": multi,
        "combinations": dict(sorted(combos.items())),
        "no_form": {"bare": bare, "unresolved": unresolved},
        "containers_read": len(db._form_cache),
        "note": ("per-form counts overlap: an effect carrying two forms is "
                 "counted under both, so they do not sum to `effects`."),
    }


#: The compiled `RSDB` twins of the same tables.  `core/dbc.py` states the rule
#: this exists to honour: *where a compiled twin exists the client reads the
#: twin, and the `.ini` beside it is a decoy* -- per file and per base, never
#: per client.  Filenames differ in case from their ini partners in the
#: shipped installs, so the lookup below is case-insensitive.
#:
#: **This list is used for its VALUES only** -- `asset_paths` wants the set of
#: paths an install names, and never looks up a row by id.  That is why
#: `weaponmotion.dbc` is safe here and is *not* safe on the definitions path:
#: its key encoding is undecoded and joins 0 of 25,944 ini ids.  See
#: `EffectDB.__init__`.
PATH_DBCS = ("3DEffectobj.dbc", "weaponmotion.dbc", "3DObj.dbc",
             "3dmotion.dbc", "miscmotion.dbc", "mountmotion.dbc")


def asset_paths(root: Path | str) -> list[str]:
    """Every asset path this install names, from the ini tables **and their
    compiled twins**.

    MEASURED, and it is not a rounding error.  On `patch5517` the plaintext
    tables name 13,533 distinct paths and the `.dbc` twins name 21,993, of
    which **8,493 appear in no ini at all**; 4,324 of those are C3 files that
    exist, and they hold 328 `PTCL`, 1,610 `PTC3` and **all 6 of that
    install's `PTCX` chunks**.  `ini/miscmotion.ini` and `ini/MountMotion.ini`
    are **zero bytes** there while their twins hold 218 and 784 rows.
    Enumerating the plaintext alone reports `PTCX` as a form no install ships
    -- which is how this was found, and is exactly wrong.

    CCO is the mirror case: it ships **no `.dbc` at all** and its plaintext is
    complete.  So twin-presence and twin-absence both have to be checked, per
    file and per base, rather than inferred from the client.

    The two sets are **unioned**, not substituted: `.dbc` is the client's
    source of truth where it exists, but an ini-only row still names a real
    asset and dropping it would trade one blind spot for another.
    """
    root = Path(root)
    ini = root / "ini"
    out: dict[str, None] = {}
    for name in PATH_INIS:
        p = ini / name
        if p.is_file():
            for v in read_flat(p).values():
                out.setdefault(v.replace("\\", "/"), None)
    lower = {p.name.lower(): p for p in ini.glob("*.dbc")} if ini.is_dir() else {}
    for name in PATH_DBCS:
        p = lower.get(name.lower())
        if p is None:
            continue
        try:
            data = p.read_bytes()
            if dbc.magic(data) != b"RSDB":
                continue
            for v in dbc.Rsdb.parse(data).paths.values():
                out.setdefault(v.replace("\\", "/"), None)
        except Exception:                                     # pragma: no cover
            continue
    return list(out)


def validate(db: EffectDB, limit: Optional[int] = None) -> dict:
    """Re-derive the parser proof: every MOTI/SHAP/SMOT/PTCL/PTCX/PTC3 chunk in
    every C3 this install names must consume exactly its declared chunk length.

    The corpus comes from `asset_paths`, i.e. the ini tables **unioned with
    their compiled `.dbc` twins**.  Enumerating the plaintext alone misses
    4,324 existing C3 files on patch5517 and every `PTCX` chunk in the
    project; see `asset_paths` for the measurement.
    """
    seen: set[str] = set()
    stats: dict = dict(_base_identity(db))
    stats |= {k: 0 for k in ("files", "missing", "MOTI", "MOTI_exact", "SHAP",
                            "SHAP_exact", "SMOT", "SMOT_exact",
                            "PTCL", "PTCL_exact", "PTCX", "PTCX_exact",
                            "PTC3", "PTC3_exact",
                            "particle_frames", "particle_frames_empty",
                            "particle_peak", "errors")}
    enc: dict[str, int] = {}
    problems: list[str] = []
    paths = asset_paths(db.root)
    stats["paths_named"] = len(paths)
    for lg in paths:
        lg = lg.replace("\\", "/")
        key = lg.lower()
        if key in seen:
            continue
        seen.add(key)
        if limit and stats["files"] >= limit:
            break
        if not db.assets.exists(lg):
            stats["missing"] += 1
            continue
        stats["files"] += 1
        try:
            c3 = C3File(db.assets.read(lg))
        except Exception as exc:
            stats["errors"] += 1
            problems.append(f"{lg}: {exc}")
            continue
        for ch in c3.chunks:
            try:
                if ch.tag == b"MOTI":
                    m = parse_moti(ch.body)
                    stats["MOTI"] += 1
                    stats["MOTI_exact"] += m.exact
                    enc[m.encoding] = enc.get(m.encoding, 0) + 1
                    if not m.exact:
                        problems.append(f"{lg} MOTI {m.consumed}/{m.size}")
                elif ch.tag == b"SHAP":
                    s = parse_shap(ch.body)
                    stats["SHAP"] += 1
                    stats["SHAP_exact"] += s.exact
                    if not s.exact:
                        problems.append(f"{lg} SHAP {s.consumed}/{s.size}")
                elif ch.tag == b"SMOT":
                    s = parse_smot(ch.body)
                    stats["SMOT"] += 1
                    stats["SMOT_exact"] += s.exact
                    if not s.exact:
                        problems.append(f"{lg} SMOT {s.consumed}/{s.size}")
                elif ch.tag in PTCL_TAGS:
                    tag = ch.tag.decode()
                    p = parse_ptcl(ch.body, PTCL_TAGS[ch.tag], tag)
                    stats[tag] += 1
                    stats[tag + "_exact"] += p.exact
                    stats["particle_frames"] += p.frame_count
                    stats["particle_frames_empty"] += sum(
                        1 for f in p.frames if not f.count)
                    stats["particle_peak"] = max(stats["particle_peak"],
                                                 p.peak_particles)
                    if not p.exact:
                        problems.append(f"{lg} {tag} {p.consumed}/{p.size}")
            except Exception as exc:
                stats["errors"] += 1
                problems.append(f"{lg} {ch.name}: {exc}")
    stats["moti_encodings"] = enc
    stats["problems"] = problems[:40]
    return stats


def _effect_json(db: EffectDB, name: str) -> Optional[dict]:
    e = db.resolve(name)
    if e is None:
        return None
    d = {
        "name": e.name, "source": e.source,
        "delay_ms": e.delay, "loop_time": e.loop_time,
        "loop_interval_ms": e.loop_interval,
        "frame_interval_ms": e.frame_interval,
        "fps": round(e.fps, 3), "endless": e.endless,
        "offset": {"x": e.offset[0], "y": e.offset[1], "z": e.offset[2]},
        "color_enable": e.color_enable,
        "layers": [],
    }
    if e.billboard is not None:
        d["billboard"] = e.billboard
    if e.lev is not None:
        d["lev"] = e.lev
    for L in e.layers:
        ld = {
            "index": L.index,
            "effect_id": L.effect_id, "mesh": L.mesh_path, "mesh_found": L.mesh_found,
            "texture_id": L.texture_id, "texture": L.texture_path,
            "texture_found": L.texture_found,
            "src_blend": L.asb, "src_blend_name": L.asb_name,
            "dst_blend": L.adb, "dst_blend_name": L.adb_name,
        }
        for key, val in (("extra_textures", L.extra_texture_ids), ("scale", L.scale),
                         ("frame_offset", L.frame_offset), ("interval", L.interval),
                         ("loop_once", L.loop_once), ("zbuffer", L.zbuffer),
                         ("ztest", L.ztest), ("billboard", L.billboard),
                         ("lev", L.lev), ("mix_opt", L.mix_opt),
                         ("x_self", L.x_self), ("y_self", L.y_self),
                         ("z_self", L.z_self)):
            if val:
                ld[key] = val
        d["layers"].append(ld)
    return d


def build_linkage(db: EffectDB, *, with_geometry: bool = True) -> dict:
    """The machine-readable bundle written to out/effects/linkage.json."""
    doc: dict = {
        "schema": 1,
        "source_root": str(db.root),
        "notes": "See docs/effects.md. show_time: 0=at motion start, 1=at motion end.",
        "wildcard": WILDCARD,
    }

    doc["weapon_types"] = {
        t: {
            "name": db.weapon_type_names.get(t, ""),
            "hit_effect": w.hit_effect, "hit_sound": w.hit_sound,
            "blk_effect": w.blk_effect, "blk_sound": w.blk_sound,
        } for t, w in sorted(db.weapon_impact.items())
    }
    for t, n in sorted(db.weapon_type_names.items()):
        doc["weapon_types"].setdefault(t, {"name": n, "hit_effect": "",
                                           "hit_sound": "", "blk_effect": "",
                                           "blk_sound": ""})

    weapons: dict[str, dict] = {}
    for app in sorted(db.weapon_appearances):
        es = db.effects_for_weapon(app)
        if not (es.aura or es.attack):
            continue
        weapons[app] = {
            "type": es.weapon_type, "type_name": es.type_name,
            "aura": es.aura, "attack": es.attack,
            "hit_effect": es.impact.hit_effect if es.impact else "",
            "blk_effect": es.impact.blk_effect if es.impact else "",
        }
    doc["weapon_appearances"] = weapons

    doc["action_effect_rules"] = [
        {"shape": r.shape, "action": r.action, "group_hi": r.group_hi,
         "group_lo": r.group_lo, "appearance": r.appearance, "effect": r.effect}
        for r in db.action_rules]
    doc["action_map_rules"] = [
        {"shape": r.shape, "action": r.action, "terrain": r.terrain,
         "effect": r.effect, "show_time": r.show_time, "dir_enable": r.dir_enable}
        for r in db.action_map]
    doc["action_delay"] = db.action_delay

    geo: dict[str, dict] = {}
    if with_geometry:
        for oid, path in sorted(db.objs.items()):
            obj = load_effect_object(db.assets, oid, path)
            # `forms` beside `kinds`: same fact, in the vocabulary the filter
            # and `comod anim` use. Derived from the kinds already computed,
            # so the catalog gains the filterable spelling at zero extra cost.
            entry = {"path": obj.logical, "kinds": sorted(obj.kinds),
                     "forms": [f for f in FORMS
                               if f in forms_from_kinds(obj.kinds)],
                     "frames": obj.frame_count,
                     "effective_frames": obj.effective_frames,
                     "parts": len(obj.parts),
                     "playable": obj.playable}
            if obj.undecoded:
                entry["undecoded"] = obj.undecoded
            if obj.error:
                entry["error"] = obj.error
            if obj.source:
                entry["source"] = obj.source
            geo[oid] = entry

    doc["effects"] = {}
    for name in sorted(db.effects):
        ed = _effect_json(db, name)
        if ed is None:
            continue
        if geo:
            # The UNION over the layers -- an effect whose layers are a PHY
            # mesh and a particle system is BOTH, and a catalog consumer
            # filtering on either form must find it.
            union: set[str] = set()
            for L in ed["layers"]:
                union |= set(geo.get(L["effect_id"], {}).get("forms", ()))
            ed["forms"] = [f for f in FORMS if f in union]
            frames = max([geo.get(L["effect_id"], {}).get("frames", 0)
                          for L in ed["layers"]] or [0])
            eff = max([geo.get(L["effect_id"], {}).get("effective_frames", 0)
                       for L in ed["layers"]] or [0])
            ed["frames"] = frames
            ed["effective_frames"] = eff
            play = eff or frames
            if play and not ed["endless"]:
                ed["duration_ms"] = (ed["delay_ms"]
                                     + ed["loop_time"] * play * ed["frame_interval_ms"]
                                     + max(0, ed["loop_time"] - 1) * ed["loop_interval_ms"])
        doc["effects"][name] = ed

    # bows have no swing trail; their visible projectile comes from here.
    # Key: "<bowAppearance>.<arrowAppearance>", 999999 = any bow.
    doc["flying_objects"] = {
        k: {"simple_obj_id": v.get("SimpleObjID", ""),
            "effect": v.get("EffectIndex", ""),
            "flying_sound": v.get("FlyingSound", ""),
            "hit_sound": v.get("HitSound", ""),
            "target_effect": v.get("TargetEffect", "")}
        for k, v in sorted(db.flying.items())}

    # Which file each of these came from. On a base with compiled twins the
    # ini alone is a floor, so an artefact built from it has to say which it
    # read -- the same reason a derived index carries its base id.
    doc["table_sources"] = {k: dict(v) for k, v in sorted(db.table_sources.items())}
    doc["effect_objects"] = {k: v.replace("\\", "/") for k, v in sorted(db.objs.items())}
    doc["effect_textures"] = {k: v.replace("\\", "/") for k, v in sorted(db.textures.items())}
    doc["weapon_motion"] = {k: v.replace("\\", "/") for k, v in sorted(db.weapon_motion.items())}
    if geo:
        doc["effect_object_geometry"] = geo

    doc["coverage"] = coverage(db)
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: The three forms as a fixed-width column, so a listing lines up and a form
#: an effect does NOT carry is a blank in a known place rather than a shorter
#: line.  `.` reads as "measured absent"; the legend says so.
_FORM_COL = {FORM_PHY_MOTI: "P", FORM_SHAP_SMOT: "S", FORM_PTCL: "T"}


def _print_effect_list(db: EffectDB, rows: list[dict], want: list[str],
                       mode: str, match: str, limit: int) -> None:
    r"""`--list`, human-readable.

    The header states the install and the filter, and the footer states the
    TOTAL separately from the number of lines printed: a `--limit`ed listing
    that only printed its rows would read as the whole answer.

    An empty result prints WHY it could be empty rather than nothing at all.
    A filter that returns zero rows passes every "no false positives" check
    trivially, so the one thing this must never do is look like success.
    """
    ident = _base_identity(db)
    print(f"  install : {ident['root']}")
    print(f"  base_id : {ident['base_id']}")
    filt = (", ".join(want) + f"  (mode: {mode})") if want else "none"
    print(f"  form    : {filt}")
    if match:
        print(f"  match   : {match!r}")
    print(f"  effects in table: {len(db.effects)}")
    print()
    if not rows:
        print("  0 effects match.")
        if not db.effects:
            print("  AND THIS INSTALL'S EFFECT TABLE IS EMPTY -- neither "
                  "ini/3DEffect.ini nor its .dbc twin was read, so this says "
                  "nothing about the filter. Run --coverage.")
        else:
            print(f"  The table holds {len(db.effects)} effects and none of "
                  f"them carries this form set. Re-run without --form to see "
                  f"what the install does ship, or --form-census for the "
                  f"per-form totals.")
        return
    print("  P = PHY+MOTI (node/bone track)   S = SHAP+SMOT (ribbon trail)   "
          "T = PTCL/PTC3 (particles)")
    print("  a '.' is a MEASURED absence, not an unknown.\n")
    print(f"  {'P S T':<7}  effect")
    print(f"  {'-' * 7}  {'-' * 40}")
    shown = rows if limit <= 0 else rows[:limit]
    for r in shown:
        got = set(r["forms"])
        cols = " ".join(_FORM_COL[f] if f in got else "." for f in FORMS)
        print(f"  {cols:<7}  {r['name']}")
    print()
    if len(shown) != len(rows):
        print(f"  {len(shown)} of {len(rows)} shown (--limit {limit}); "
              f"{len(rows)} match in total.")
    else:
        print(f"  {len(rows)} effect(s) match.")
    multi = sum(1 for r in rows if len(r["forms"]) > 1)
    print(f"  {multi} of them carry more than one form and are listed under "
          f"each (set membership, not a category).")


def _print_effect(db: EffectDB, name: str) -> None:
    e = db.resolve(name)
    if e is None:
        print(f"no effect named {name!r}")
        near = [n for n in db.effects if name.lower() in n.lower()][:12]
        if near:
            print("  did you mean:", ", ".join(near))
        return
    loops = "forever" if e.endless else f"{e.loop_time}x"
    print(f"[{e.name}]   ({e.source})")
    print(f"  timing     frameInterval={e.frame_interval} ms  ({e.fps:.1f} fps)  "
          f"loop={loops}  loopInterval={e.loop_interval} ms  delay={e.delay} ms")
    print(f"  offset     {e.offset}   colorEnable={e.color_enable}")
    for L in e.layers:
        print(f"  layer {L.index}: obj {L.effect_id:>6} -> {L.mesh_path or '(unmapped)'}"
              f"  {'OK' if L.mesh_found else 'MISSING'}")
        print(f"           tex {L.texture_id:>6} -> {L.texture_path or '(unmapped)'}"
              f"  {'OK' if L.texture_found else 'MISSING'}")
        print(f"           blend {L.asb_name} -> {L.adb_name}")
    for obj in db.load_geometry(name):
        if obj.error:
            print(f"  geom  {obj.logical}: {obj.error}")
            continue
        print(f"  geom  {obj.logical}  [{obj.source}]  {len(obj.parts)} part(s), "
              f"{obj.frame_count} frames ({obj.effective_frames} effective), "
              f"{'+'.join(sorted(obj.kinds))}")
        for p in obj.parts:
            if p.kind == PART_PHY and p.mesh is not None:
                m = p.motion
                print(f"          PHY  {p.name or '-':<14} verts={len(p.mesh.vertices):<5}"
                      f" faces={len(p.mesh.faces):<5}"
                      f" motion={m.encoding if m else 'none'}"
                      f" bones={m.bone_count if m else 0}"
                      f" frames={m.frame_count if m else 0}"
                      f" keys={len(m.keys) if m else 0}"
                      f" a/d/t={len(p.alpha_keys)}/{len(p.draw_keys)}/{len(p.tex_keys)}"
                      f" eff={p.effective_frames}"
                      f"{' uvgrid=%d' % p.uv_grid if p.tex_keys else ''}"
                      f"{' uvstep=%s' % (p.uv_step,) if p.uv_step else ''}")
            elif p.kind == PART_SHAPE and p.shape is not None:
                print(f"          SHAP {p.name or '-':<14} line={p.shape.line}"
                      f" segments={p.shape.segments}"
                      f" smot_frames={p.smotion.frame_count if p.smotion else 0}")
            elif p.kind == PART_PARTICLE:
                q = p.particle
                if q is None:
                    print(f"          {p.name:<5}{'':<14} {p.raw_size} bytes"
                          f" (UNDECODED)")
                    continue
                env = ""
                if q.envelope is not None:
                    e3 = q.envelope
                    env = (f" billboard={e3.billboard} world={e3.world_space}"
                           f" alpha={e3.alpha} fade={e3.fade_frames}")
                print(f"          {p.name:<5}{q.name[:14]:<14}"
                      f" frames={q.frame_count} peak={q.peak_particles}"
                      f" max={q.max_particles} atlas={q.tex_grid}x{q.tex_grid}"
                      f" {q.consumed}/{q.size} bytes{env}")
    ms = db.duration_ms(name)
    if ms is not None:
        print(f"  duration   {ms:.0f} ms")


def _print_weapon(db: EffectDB, app: str) -> None:
    es = db.effects_for_weapon(app)
    print(f"weapon appearance {es.appearance}  type {es.weapon_type} "
          f"({es.type_name or 'unnamed'})"
          f"{'' if es.in_weapon_ini else '   [NOT in weapon.ini]'}")
    print(f"  aura (action 999) : {es.aura or '(none)'}")
    if es.attack:
        for a, n in es.attack.items():
            print(f"  action {a}         : {n}")
    else:
        print("  attack trail      : (none)")
    if es.impact:
        w = es.impact
        print(f"  hit               : {w.hit_effect}   {w.hit_sound}")
        print(f"  block             : {w.blk_effect}   {w.blk_sound}")
    if es.motion_meshes:
        print(f"  weapon motion meshes: {len(es.motion_meshes)}")
        for k, v in list(es.motion_meshes.items())[:6]:
            print(f"      +{k} -> {v}")
    print()
    for n in es.effect_names:
        _print_effect(db, n)
        print()


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--weapon", help="weapon appearance ID, e.g. 410009")
    ap.add_argument("--effect", help="effect name, e.g. Flash4102")
    ap.add_argument("--action-map", nargs=3, metavar=("SHAPE", "ACTION", "TERRAIN"))
    ap.add_argument("--list", action="store_true", dest="do_list",
                    help="list every effect name with the animation form(s) "
                         "it carries; narrow with --form")
    ap.add_argument("--form", action="append", default=[], metavar="FORM",
                    help="keep only effects carrying this form. Repeatable. "
                         "One of: " + ", ".join(FORMS) + " (aliases: phy, "
                         "moti, ribbon, trail, shap, smot, particle, ptcl, "
                         "ptc3). Membership, not category: an effect carrying "
                         "two forms is listed under both.")
    ap.add_argument("--form-mode", choices=("any", "all", "none"),
                    default="any",
                    help="with several --form: 'any' (default, the union), "
                         "'all' (carries every one -- this is how you find "
                         "the multi-form effects), 'none' (carries no named "
                         "form)")
    ap.add_argument("--match", default="",
                    help="substring of the effect name, case-insensitive")
    ap.add_argument("--limit", type=int, default=0,
                    help="print at most N rows (0 = all); the TOTAL is "
                         "printed either way")
    ap.add_argument("--form-census", action="store_true",
                    help="how many effects carry each form in this install "
                         "(all three rows always printed)")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--linkage", action="store_true",
                    help="write out/effects/linkage.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pretty", action="store_true",
                    help="indent out/effects/linkage.json (roughly 2x the size)")
    args = ap.parse_args(argv[1:])

    # `with`: every path out of here -- including the `return 2` below --
    # used to drop an install this function opened itself.
    with EffectDB(args.root) as db:
        if args.do_list or args.form or args.form_census:
            try:
                want = [parse_form(f) for f in args.form]
            except UnknownForm as exc:
                print(str(exc), file=sys.stderr)
                return 2
        if args.form_census:
            print(json.dumps(form_census(db), indent=2, ensure_ascii=False))
        if args.do_list or args.form:
            rows = filter_effects(db, forms=want, mode=args.form_mode,
                                  match=args.match)
            if args.json:
                print(json.dumps({**_base_identity(db),
                                  "filter": {"forms": want,
                                             "mode": args.form_mode,
                                             "match": args.match},
                                  "total": len(rows), "effects": rows},
                                 indent=2, ensure_ascii=False))
            else:
                _print_effect_list(db, rows, want, args.form_mode, args.match,
                                   args.limit)

        if args.weapon:
            if args.json:
                print(json.dumps(asdict(db.effects_for_weapon(args.weapon)),
                                 indent=2, ensure_ascii=False))
            else:
                _print_weapon(db, args.weapon)
        if args.effect:
            if args.json:
                print(json.dumps(_effect_json(db, args.effect), indent=2, ensure_ascii=False))
            else:
                _print_effect(db, args.effect)
        if args.action_map:
            r = db.lookup_action_map(*args.action_map)
            print(r if r else "no match")
        if args.coverage:
            print(json.dumps(coverage(db), indent=2, ensure_ascii=False))
        if args.validate:
            print(json.dumps(validate(db), indent=2, ensure_ascii=False))
        if args.linkage:
            # `derived_path` with no root resolves the *configured* install, not
            # the one `--root` just made us read -- so `--root <B> --linkage`
            # used to read B and write into A's namespace, reporting "wrote"
            # either way.  Same defect class as docs/CORRECTIONS.md C22; measured
            # and registered as C33.  Pass the root we actually read.
            out = (Path(args.out) if args.out
                   else coroot.derived_path("out/effects/linkage.json", args.root))
            out.parent.mkdir(parents=True, exist_ok=True)
            doc = build_linkage(db)
            text = (json.dumps(doc, indent=1, ensure_ascii=False) if args.pretty
                    else json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
            out.write_text(text, "utf-8")
            print(f"wrote {out}  ({out.stat().st_size / 1e6:.2f} MB)")
            print(json.dumps(doc["coverage"], indent=2, ensure_ascii=False))
        if not any((args.weapon, args.effect, args.action_map, args.coverage,
                    args.validate, args.linkage, args.do_list, args.form,
                    args.form_census)):
            ap.print_help()
        # Last, so it is the line still on screen after a long build rather than
        # something scrolled past by the coverage dump.
        report_missing_tables()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
