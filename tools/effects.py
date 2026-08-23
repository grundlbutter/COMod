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

import struct
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from coassets import AssetRoot, C3File, DEFAULT_ROOT          # noqa: E402
import coroot                                                  # noqa: E402
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


def _all_nines(field: str) -> bool:
    r"""Is this key field all nines, at whatever width the client writes it?

    The one definition of the spelling, so a second one cannot drift from it.
    `is_always_on` is this question asked about the action field, and
    `specificity` is it asked about any of the four. Both used to spell it
    themselves and one of them was three wide -- which is CORRECTIONS C35.

    `len >= 3` because a bare `"9"` is a plausible *unpadded action code*, not
    a wildcard; every client writes the sentinel at least three wide.
    """
    return bool(field) and len(field) >= 3 and set(field) == {"9"}


# ---------------------------------------------------------------------------
# tiny ini readers.  The 3D tables are not RFC-anything; they are three shapes:
#   * flat  key=value                         (3DEffectObj, 3dtexture, ...)
#   * [section] + key=value                   (3DEffect, WeaponEffect, ...)
#   * dotted key=value with 999 wildcards     (Action3DEffect, ActionSound)
# codepage.ini is 1 byte; every legacy ini in this install decodes as GBK, and
# GBK is a superset of ASCII so pure-ASCII tables are unaffected.
# ---------------------------------------------------------------------------

ENCODING = "gbk"


def _lines(path: Path) -> Iterator[str]:
    for raw in Path(path).read_bytes().decode(ENCODING, errors="replace").splitlines():
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
                    qx, qy, qz, qw, tx, ty, tz = struct.unpack_from("<7f", body, off + 28 * b)
                    m = list(quat_to_matrix(qx, qy, qz, qw))
                    m[12], m[13], m[14], m[15] = tx, ty, tz, 1.0
                    mats.append(tuple(m))
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
                off += 48 * bone_count
            keys.append(MotionKey(frame, mats))
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
    if extra:
        off += extra * frame_count * 4
    return Motion(bone_count, frame_count, enc, keys, extra, off, len(body))


@dataclass
class Shape:
    """A decoded ``SHAP`` chunk -- the trail/ribbon source line."""
    name: str
    frames: list[list[tuple[float, float, float]]]
    label: str
    segments: int
    consumed: int = 0
    size: int = 0

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
                 label.decode(ENCODING, "replace"), seg or 1, off, len(body))


@dataclass
class SMotion:
    """A decoded ``SMOT`` chunk: one 4x4 per frame, nothing else."""
    matrices: list[Mat4]
    consumed: int = 0
    size: int = 0

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
    return SMotion(mats, 4 + 64 * n, len(body))


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

    def cell(self, phase: float) -> tuple[int, int]:
        """The flipbook cell a stored phase selects.

        `Ptcl_Draw` 0x607D0-0x607F6: ``i = int(phase * N*N)`` then
        ``col = i % N, row = i // N``, and the UV offset is
        ``(col / N, row / N)`` with cell size ``1 / N``.  Same atlas
        convention as the PHY ChangeTex channel (docs/effects.md §6.4), and
        the same N-is-a-side-length reading.
        """
        n = self.tex_grid or 1
        i = int(phase * n * n)
        return (i % n, i // n)

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
                    off, len(body))


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
        return tuple(struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
                     for v in step)

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
            # CAME (camera) is authoring metadata; nothing to play.
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
    #: The compiled record's three unidentified bytes, on the same footing as
    #: ``Rsdb.extra``: **preserved, not interpreted.**  Empty when the
    #: definition came from the plaintext ini.  `unk63` is the only plausible
    #: `ColorEnable` candidate and it is deliberately NOT mapped onto
    #: `color_enable` -- it reads ~1 everywhere on both sides, so the check
    #: would look identical if the identification were wrong (C44, and the
    #: rule in docs/CORRECTIONS.md §2).
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

    Deliberately **lossy in one direction and honest about it**: the compiled
    record carries no field identified as ``ColorEnable``, ``Billboard`` or
    ``Lev``, so those read as their defaults here rather than being invented
    from ``unk62``/``unk63``/``unk64`` -- all three identifications are either
    refuted or unfalsifiable from these files (C44).  The raw bytes travel in
    `EffectDef.extra` so nothing is silently dropped.
    """
    e = EffectDef(
        name=rec["name"],
        amount=rec["amount"],
        delay=rec["delay"],
        loop_time=rec["loop_time"],
        loop_interval=rec["loop_interval"],
        frame_interval=rec["frame_interval"],
        offset=rec["offset"],
        source="3DEffect.dbc",
        extra={"unk62": rec["unk62"], "unk63": rec["unk63"],
               "unk64": rec["unk64"]},
    )
    for i, L in enumerate(rec["layers"]):
        lay = EffectLayer(
            index=i,
            effect_id=str(L["effect"]),
            texture_id=str(L["texture"]),
            asb=L["asb"], adb=L["adb"])
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


@dataclass
class WeaponImpact:
    """One ``ini/WeaponEffect.ini`` section: the spark shown at the target."""
    weapon_type: str
    hit_effect: str = ""
    hit_sound: str = ""
    blk_effect: str = ""
    blk_sound: str = ""


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

        #: table stem -> the file this instance actually read.  See
        #: `TableSource`; surfaced by `coverage()` as `tables_read`.
        self.sources: dict[str, TableSource] = {}
        #: ini filename -> `read_flat_live`'s record for *this instance's* call.
        self._flat_records: dict[str, dict] = {}

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
        self.weapon_appearances: dict[str, dict[str, str]] = read_sections(
            self.ini / "weapon.ini")

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

    def resolve(self, name: str) -> Optional[EffectDef]:
        """Return a copy of the effect definition with asset paths filled in."""
        base = self.effects.get(name)
        if base is None:
            return None
        e = EffectDef(**{**asdict(base), "layers": []})
        for lay in base.layers:
            L = EffectLayer(**asdict(lay))
            raw_mesh = self._table_get(self.objs, L.effect_id)
            L.mesh_path = raw_mesh.replace("\\", "/")
            L.mesh_found = bool(L.mesh_path) and self.assets.exists(L.mesh_path)
            raw_tex = self._table_get(self.textures, L.texture_id)
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

    # effect name resolution
    referenced: set[str] = set()
    for r in db.action_rules:
        referenced.add(r.effect)
    for r in db.action_map:
        referenced.add(r.effect)
    for w in db.weapon_impact.values():
        referenced.update((w.hit_effect, w.blk_effect))
    referenced = {n for n in referenced if n and n.lower() != "none"}
    out["effect_names_referenced"] = len(referenced)
    out["effect_names_defined"] = len(db.effects)
    out["effect_names_referenced_but_undefined"] = sorted(referenced - set(db.effects))

    layers = meshes_ok = texes_ok = 0
    full = partial = broken = 0
    for name in sorted(db.effects):
        e = db.resolve(name)
        if not e.layers:
            broken += 1
            continue
        ok = 0
        for L in e.layers:
            layers += 1
            meshes_ok += L.mesh_found
            texes_ok += L.texture_found
            ok += L.mesh_found and L.texture_found
        if ok == len(e.layers):
            full += 1
        elif ok:
            partial += 1
        else:
            broken += 1
    out["effect_layers_total"] = layers
    out["effect_layers_mesh_found"] = meshes_ok
    out["effect_layers_texture_found"] = texes_ok
    out["effects_fully_resolved"] = full
    out["effects_partially_resolved"] = partial
    out["effects_unresolved"] = broken
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
            entry = {"path": obj.logical, "kinds": sorted(obj.kinds),
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
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--linkage", action="store_true",
                    help="write out/effects/linkage.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pretty", action="store_true",
                    help="indent out/effects/linkage.json (roughly 2x the size)")
    args = ap.parse_args(argv[1:])

    db = EffectDB(args.root)

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
                args.validate, args.linkage)):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
