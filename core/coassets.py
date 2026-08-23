"""
coassets.py -- asset layer for the Classic Conquer 2.0 client.

Everything needed to *read* the client's graphics assets and work out which file
backs a given in-game thing:

    AssetRoot   virtual filesystem: loose files shadow the WDF archives
    C3File      the MAXFILE C3 chunk container (meshes / motions / effects)
    dds_info    DirectDraw Surface header reader
    PartIni     the ini/ tables that map appearance IDs -> mesh + texture IDs
    DMap        world map file

Verification status of every format is recorded in docs/modding.md. Nothing here
guesses silently: functions that rely on an inferred rule say so in their
docstring.

READ-ONLY with respect to the game install. Nothing in this module writes to
$ROOT. See comod.py for the mod build/stage workflow.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coroot                       # noqa: E402
import dbcshadow                    # noqa: E402
import safepath                     # noqa: E402
import tqdat                        # noqa: E402
from tqhash import tq_hash          # noqa: E402
from tpd import TpdArchive          # noqa: E402
from wdf import WdfArchive          # noqa: E402


class _TpdContainer:
    """A NetDragon DatPkg pair behind `WdfArchive`'s lookup interface.

    `core/tpd.py` has read this format since it landed -- verified against
    Zephyr-1057's two pairs, 53,609 and 76,923 entries -- but nothing ever
    consumed it: until now no reference to `tpd`, `TpdArchive` or `.tpi`
    existed in `coassets.py`, `coroot.py` or any plugin. So `AssetRoot`
    refused the very client the reader was verified on. This adapter is the
    consumer, and it lands Zephyr and 7878 from one change.

    **A TPD root resolves EXACTLY, and that is a capability WDF does not
    have.** The index stores plaintext paths, so a name is looked up as
    itself. WDF stores only `tq_hash(name)`, which is why `out/wdf/*.json`
    name recovery exists at all and why 331 of the official archive's 24,757
    names were never recovered -- a real texture can be nameless yet
    perfectly readable there. Nothing of that applies here: `get_by_name` is
    the primary lookup and `get` is kept only so callers written against the
    WDF interface keep working.
    """

    def __init__(self, path: Path):
        self._arc = TpdArchive(path)
        self.path = Path(path)
        self._by_name = {e.name.replace("\\", "/").lower(): e
                         for e in self._arc.entries}
        #: Built on FIRST HASH LOOKUP, not here.  See `_hashes`.
        self._by_hash: Optional[dict] = None

    #: **This map cost 2.7 of the 3.0 seconds it took to open a 7878 root, for
    #: a lookup that root never performs.**  MEASURED before the change:
    #:
    #:      c3.tpi    59,964 entries   parse 137.6 ms   by_name 13.7 ms   by_hash   960.3 ms
    #:      data.tpi  86,149 entries   parse 212.1 ms   by_name 17.3 ms   by_hash 1,773.1 ms
    #:
    #: 86-89% of the whole open, against 6.8 ms for a WDF root's two archives.
    #: The index PARSE is only 350 ms; the rest was hashing 146,113 names.
    #:
    #: It is not dead code, which is why it is deferred rather than deleted:
    #: `AssetRoot.locate` and `.read` prefer `get_by_name` whenever a container
    #: has it, so a DatPkg root never reaches `get`/`read_by_hash` -- but
    #: `core/colibrary.py` looks entries up by a stored hex hash and can. Every
    #: caller was checked before this was changed.
    #:
    #: So the cost now falls on whoever actually asks by hash, once, instead of
    #: on every tool that opens the install.  Same map, same collision
    #: behaviour (last wins, as the eager comprehension did), same answers.
    def _hashes(self) -> dict:
        if self._by_hash is None:
            self._by_hash = {tq_hash(n): e for n, e in self._by_name.items()}
        return self._by_hash

    @property
    def file_size(self) -> int:
        """Bytes on disk, under the name `WdfArchive` uses for it.

        This adapter claims WdfArchive's interface, and `file_size` is part of
        that interface -- `coviewer.api_status` reads it for every archive, so
        a container missing it takes the whole status endpoint down with an
        `AttributeError` rather than degrading. That is what happened the first
        time a TPD root was actually served: the reader had been correct for
        months and had no consumer, so the gap could not show.

        **Both halves are counted, because a TPD is a PAIR.** Returning only
        the `.tpd` would understate every archive by its whole index, and
        returning only the `.tpi` would understate it by the payload -- and
        either would look like a plausible number sitting next to WDF's, which
        is the worst way to be wrong. A half that cannot be stat'd contributes
        nothing rather than raising, since a size is a nicety and the catalogue
        is not.
        """
        total = 0
        for half in (getattr(self._arc, "tpi", None),
                     getattr(self._arc, "tpd", None)):
            try:
                if half is not None:
                    total += Path(half).stat().st_size
            except OSError:
                continue
        return total

    # -- the WdfArchive lookup interface -----------------------------------
    def get(self, name_hash: int):
        return self._hashes().get(name_hash)

    def read_by_hash(self, name_hash: int) -> bytes:
        e = self._hashes().get(name_hash)
        if e is None:
            raise KeyError(name_hash)
        return self._arc.read(e)

    # -- the exact interface, preferred when present -----------------------
    def get_by_name(self, logical: str):
        return self._by_name.get(logical.replace("\\", "/").lower())

    def read_entry(self, entry) -> bytes:
        return self._arc.read(entry)

    @property
    def entries(self):
        """The entry list, under the name `WdfArchive` uses for it.

        Callers that enumerate an archive rather than look one path up --
        `coviewer.Catalog` building its archive index is the one that matters
        -- iterate `arc.entries`. A `TpdEntry` carries `.name` and `.size` but
        **no `.hash`**, because this container is keyed by path and does not
        need one; a caller wanting the WDF-style key computes
        `tq_hash(e.name)`, which is exact here rather than recovered.
        """
        return self._arc.entries

    def close(self) -> None:
        self._arc.close()

    def __len__(self) -> int:
        return len(self._by_name)

#: The install root, resolved once per process by ``coroot`` -- explicit
#: ``--root`` beats ``CO_ROOT`` beats a saved config beats auto-discovery.
#: It is a plain Path so it can go on being a default argument value; when
#: nothing is found it falls back to the conventional path and the failure
#: surfaces in ``AssetRoot`` with a message that names what is missing.
DEFAULT_ROOT = coroot.default_root()


# ---------------------------------------------------------------------------
# ini tables
# ---------------------------------------------------------------------------

def parse_ini(path: Path, encoding: str = "latin1", *,
              allow_stale: bool = False) -> dict[str, dict[str, str]]:
    """Parse a TQ-style ini into {section: {key: value}}.

    TQ inis are not standard: keys repeat across sections, values are untyped,
    and section names are usually numeric asset IDs. codepage.ini is "0" in this
    install, so text is read as latin1 and any GBK names are kept as raw bytes
    round-trippable through latin1.

    **Raises `dbcshadow.ShadowedIni` if the file has a compiled `.dbc` twin on
    its own base.** From 5517 onward the client reads the twin, so the
    plaintext here is not the live table; see `core/dbcshadow.py`. The check
    is per path and per call, so it is silent on 5017/5065/5165/CCO, which
    ship no `.dbc`. Pass `allow_stale=True` where reading the stale plaintext
    is the point -- a Rosetta comparison, or a table whose compiled form has
    no reader yet -- and say which at the call site.
    """
    dbcshadow.check_ini(path, allow_stale=allow_stale)
    sections: dict[str, dict[str, str]] = {}
    cur: Optional[dict[str, str]] = None
    for line in path.read_text(encoding, errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = {}
            sections[line[1:-1].strip()] = cur
        elif "=" in line and cur is not None:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.strip()
    return sections


@dataclass
class PartRef:
    """One (mesh, texture) pair of a multi-part appearance."""
    index: int
    mesh: str
    texture: str
    mix_tex: str = "0"
    third_tex: str = "0"
    fourth_tex: str = "0"
    material: str = "default"


@dataclass
class Appearance:
    """One section of armor.ini / weapon.ini / armet.ini / ..."""
    ident: str
    source_ini: str
    parts: list[PartRef] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)


class PartIni:
    """A body-part appearance table (armor.ini, weapon.ini, armet.ini, ...).

    VERIFIED: section = appearance ID; Part=N gives N sub-parts; each sub-part i
    has Mesh<i> and Texture<i> holding bare numeric asset IDs. Confirmed against
    armor.ini (3418 sections), weapon.ini (5384), armet.ini (2918).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = self.path.name
        # Official 6090-era clients ship every appearance table twice: this
        # ini, stamped 2008-09 and never updated, and a compiled `.dbc` twin
        # (magic MESH) the client actually reads -- armet's stale ini is
        # missing 1,441 of the dbc's 2,609 rows. Prefer the twin when it
        # parses; the ini remains the format everywhere it is all there is.
        self.source = self.name
        #: The twin's filename when a **MESH** twin is on disk, else None.
        #: Set whether or not it parsed, so `twin_error` reads against it.
        self.twin: Optional[str] = None
        #: None when the twin parsed, when there is no twin, and when the
        #: `.dbc` beside this ini is a different kind of table. A string ONLY
        #: when a MESH twin was **present and unreadable** -- a defect in us
        #: or in the install, and NOT the same event as shipping no twin.
        self.twin_error: Optional[str] = None
        dbc_twin = self.path.with_suffix(".dbc")
        # MEASURED on 5517: 14 of the inis with a `.dbc` beside them, and only
        # **6** of those twins are MESH. The other 8 are `RSDB` path tables
        # (`3dobj`, `3dtexture`, `WeaponMotion`, ...), one `SIMO` and one
        # `EFFE`. Those are not appearance tables and `read_mesh` refusing
        # them is the correct answer, not a failure -- so the magic is checked
        # BEFORE the load, and a non-MESH twin leaves `twin_error` None.
        #
        # This distinction is not decoration: without it `twin_error` is
        # non-None for 8 tables that are working exactly as intended, and the
        # gate built on it goes red over nothing. It did, on the first run.
        if dbc_twin.is_file() and dbcshadow.twin_magic(dbc_twin) == b"MESH":
            self.twin = dbc_twin.name
            try:
                self._load_mesh_dbc(dbc_twin)
                return
            except (ValueError, struct.error, OSError) as e:
                # NARROWED from `except Exception: pass` (2026-08-10).
                #
                # Two halves, and the second is the one the rule is about.
                #
                # Narrowing: these three are what a real parse failure looks
                # like -- `dbc.read_mesh` raises `ValueError` on bad magic and
                # on a walk that does not reach EOF, `struct` raises on a
                # short read, and the file read raises `OSError`. Everything
                # else now propagates. **`ImportError` in particular**: `dbc`
                # is imported inside `_load_mesh_dbc`, which is called from
                # inside this `try`, so a broken first-party module used to
                # arrive here as "this install ships no twin". `CONTRIBUTING`
                # §"A tool degrades; a test refuses" -- a guard on a
                # first-party module protects nothing.
                #
                # Audibility: `PartIni` is a reader that tools use, so it
                # still degrades to the ini rather than refusing. What changes
                # is that the degradation leaves a trace. Before this, the
                # stale read below was reached by two completely different
                # events -- "no twin, and the ini IS the live table" (true on
                # the whole 5017/5065/5165/CCO lineage) and "the twin is right
                # there and we could not read it" -- and **nothing could tell
                # them apart**, because `self.source` is `armor.ini` either
                # way. On a 5517+ base the second silently serves the 2008-era
                # table: armet's ini is missing 1,441 of the twin's 2,609
                # rows.
                #
                # `tools/test_viewer.py::PartIniTwinFailureIsAudible` is the
                # reader. `self.source` alone was not one: it is written in
                # both branches and, MEASURED across `core/`, `tools/`,
                # `client/`, `capture/` and `tests/`, **had no readers at
                # all** -- the same shape as `_np` in C46, a channel nobody
                # listened to.
                self.twin_error = f"{type(e).__name__}: {e}"
        # Declared stale read: the MESH twin was preferred above and either is
        # absent (the whole 5017/5065/5165/CCO lineage, where this ini IS the
        # live table) or failed to parse -- and `twin_error` now says which.
        self.sections = parse_ini(self.path, allow_stale=True)
        self.appearances: dict[str, Appearance] = {}
        for ident, kv in self.sections.items():
            try:
                n = int(kv.get("Part", "0") or 0)
            except ValueError:
                n = 0
            app = Appearance(ident=ident, source_ini=self.name, raw=kv)
            for i in range(n):
                mesh = kv.get(f"Mesh{i}", "")
                tex = kv.get(f"Texture{i}", "")
                if not mesh and not tex:
                    continue
                app.parts.append(PartRef(
                    index=i, mesh=mesh, texture=tex,
                    mix_tex=kv.get(f"MixTex{i}", "0"),
                    third_tex=kv.get(f"ThirdTex{i}", "0"),
                    fourth_tex=kv.get(f"FourthTex{i}", "0"),
                    material=kv.get(f"Material{i}", "default"),
                ))
            self.appearances[ident] = app

    def _load_mesh_dbc(self, twin: Path) -> None:
        """Appearances out of the compiled MESH twin (`dbc.read_mesh`).

        Idents are `str(id)` -- the dbc stores section numbers as ints,
        which is also the 6090 inis' own unpadded spelling; `get` bridges
        the zero-padded CCO form. A multi-part appearance arrives as one
        record carrying a **list** of parts, in part order -- `read_mesh`
        used to be read as a fixed-stride table in which such a record could
        not occur at all, and `mount.dbc`'s 1,004 of them are why that was
        found. `docs/CORRECTIONS.md` `C-2026-08-09-claude-elastic-elion-0da45c`.
        """
        import dbc
        rows = dbc.read_mesh(twin.read_bytes())
        self.source = twin.name
        self.sections = {}
        self.appearances = {}
        # CCO spells body-typed sections nine wide ([002000000]) and every
        # consumer's bodyType arithmetic slices that form; the dbc stores the
        # same numbers as ints. Restore the width for the tables that carry
        # a body-type prefix -- weapon idents are six wide in both worlds
        # and must not be padded.
        pad9 = self.path.stem.lower() in (
            "armor", "armet", "head", "pelvis", "misc",
            "mix_armor", "mix_armet", "mix_head", "mix_pelvis")
        for rid, parts in rows.items():
            ident = str(rid).zfill(9) if pad9 and rid < 10 ** 9 else str(rid)
            app = Appearance(ident=ident, source_ini=self.name)

            def w(n: int) -> str:
                return str(n).zfill(9) if pad9 and 0 < n < 10 ** 9 else str(n)

            for i, r in enumerate(parts):
                app.parts.append(PartRef(
                    index=i, mesh=w(r["mesh"]), texture=w(r["texture"]),
                    mix_tex=w(r["mixtex"]) if r["mixtex"] else "0"))
            app.raw = {"Part": str(len(parts)),
                       "Asb0": str(parts[0]["asb"]),
                       "Adb0": str(parts[0]["adb"])} if parts else {}
            self.appearances[ident] = app

    def get(self, ident: str) -> Optional[Appearance]:
        for key in (ident, ident.zfill(9), ident.lstrip("0")):
            if key in self.appearances:
                return self.appearances[key]
        return None

    def __len__(self) -> int:
        return len(self.appearances)

    def __iter__(self) -> Iterator[Appearance]:
        return iter(self.appearances.values())


# ---------------------------------------------------------------------------
# C3 container
# ---------------------------------------------------------------------------

C3_MAGIC = b"MAXFILE C3 00001"

# Chunk tags seen in this install. Counts are from the full corpus
# (4538 archived + 2002 loose C3 files).
C3_TAGS = {
    b"PHY ": "physique / mesh, original encoding",
    b"PHY4": "physique / mesh, newer encoding (used by the 2022 loose patches)",
    b"MOTI": "motion / animation track",
    b"CAME": "camera",
    b"PTCL": "particle system, original encoding",
    b"PTC3": "particle system, newer encoding",
    b"SHAP": "shape",
    b"SMOT": "shape motion",
    b"MNEW": "newer mesh variant",
    b"CCFL": "seen only in loose patches; contents undecoded",
    b"OMNI": "omni light (1 occurrence)",
}


@dataclass
class C3Chunk:
    tag: bytes
    body: bytes
    offset: int = -1

    @property
    def name(self) -> str:
        return self.tag.decode("latin1")

    @property
    def size(self) -> int:
        return len(self.body)

    @property
    def described(self) -> str:
        return C3_TAGS.get(self.tag, "unknown tag")


class C3File:
    """The MAXFILE C3 chunk container.

    VERIFIED, exhaustively: 16-byte magic ``MAXFILE C3 00001`` followed by a flat
    sequence of ``fourcc[4] + uint32 length + body[length]`` chunks running
    exactly to EOF. Walked cleanly over all 4538 C3 files in c3.wdf and all 2002
    loose C3 files with zero size mismatches and zero trailing bytes.

    Chunk *bodies* are only partially decoded -- see docs/modding.md. That is
    enough to reorder, drop, swap and re-pack chunks losslessly, which is what
    most mesh modding actually needs.
    """

    def __init__(self, data: bytes, strict: bool = True):
        """``strict=False`` reproduces the engine's tolerance: it walks
        chunks until one does not fit and quietly stops there.  A handful of
        community-client files carry trailing garbage after their last valid
        chunk; the game draws them, so a viewer should too.  Writers must
        stay strict -- re-packing a leniently-parsed file would silently drop
        the tail."""
        if not data.startswith(C3_MAGIC):
            raise ValueError(f"not a C3 file (magic {data[:16]!r})")
        self.magic = data[:16]
        self.chunks: list[C3Chunk] = []
        self.truncated_at: Optional[int] = None
        off = 16
        while off + 8 <= len(data):
            tag = bytes(data[off:off + 4])
            (size,) = struct.unpack_from("<I", data, off + 4)
            if off + 8 + size > len(data):
                if strict:
                    raise ValueError(
                        f"chunk {tag!r} at {off} claims {size} bytes, only "
                        f"{len(data) - off - 8} remain"
                    )
                self.truncated_at = off
                return
            self.chunks.append(C3Chunk(tag, bytes(data[off + 8:off + 8 + size]), off))
            off += 8 + size
        if off != len(data):
            if strict:
                raise ValueError(
                    f"trailing bytes: stopped at {off}, file is {len(data)}")
            self.truncated_at = off

    @classmethod
    def load(cls, path: Path) -> "C3File":
        return cls(Path(path).read_bytes())

    def to_bytes(self) -> bytes:
        out = bytearray(self.magic)
        for c in self.chunks:
            out += c.tag + struct.pack("<I", len(c.body)) + c.body
        return bytes(out)

    def tags(self) -> list[str]:
        return [c.name for c in self.chunks]

    def of_tag(self, tag: str | bytes) -> list[C3Chunk]:
        t = tag.encode("latin1") if isinstance(tag, str) else tag
        return [c for c in self.chunks if c.tag == t]

    #: Tags whose body is known to start with a length-prefixed node name.
    NAMED_TAGS = (b"PHY ", b"PHY4", b"MNEW")

    def node_name(self, chunk: C3Chunk) -> Optional[str]:
        """Node name for a PHY / PHY4 / MNEW chunk, else None.

        VERIFIED for PHY-family chunks: the body begins with ``uint32 nameLen``
        followed by ``nameLen`` bytes of name, and the names recovered this way
        are exactly the ``Dumy`` attachment-point identifiers listed in
        ini/RolePart.ini (v_body, v_armet, v_l_weapon, v_l_foot, ...).

        Deliberately returns None for MOTI and other tags: a MOTI body starts
        with two uint32 counts, and reading those as a length-prefixed string
        produces convincing garbage (a 1-character name "e"). Guessing there
        would be worse than declining.
        """
        b = chunk.body
        if chunk.tag not in self.NAMED_TAGS or len(b) < 4:
            return None
        (n,) = struct.unpack_from("<I", b, 0)
        if not (0 < n <= 64) or 4 + n > len(b):
            return None
        raw = b[4:4 + n]
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")

    def phy_header(self, chunk: C3Chunk) -> Optional[dict]:
        """Partially decoded PHY/PHY4 header.

        PARTIAL. What is established:
          * ``uint32 nameLen`` + name  (VERIFIED, see node_name)
          * two uint32 fields follow the name. The first is 0 or 2 across the
            whole corpus -- 2 exactly on skinned "v_body" meshes and 0 on rigid
            attachment meshes like v_armet / v_l_weapon -- so it reads as a
            skinning flag. The second scales with mesh complexity and is the
            best candidate for a vertex count, but no fixed byte stride
            reproduces the chunk length from it, so the arrays that follow are
            NOT a flat interleaved vertex buffer. Reported as raw values.

        What the engine says the data *is* (from graphic.dll export signatures,
        which are unmangled and therefore reliable):
          ``Phy_DynamicCreateEx(u32, u32, int, D3DXVECTOR3* pos,
              D3DXVECTOR2* uv0, D3DXVECTOR2* uv1, D3DXVECTOR3* normal,
              u32* color, u16* index, bool)``
        so a physique is positions + two UV sets + normals + packed colours +
        16-bit indices. ``Phy_Load`` reads 60-byte matrix records, an index
        block sized ``count * 3`` of 2-byte elements, and two optional named
        blocks, ``C3EXP_COLOR`` and ``C3EXP_STRETCH``.

        Field-level offsets for the vertex arrays are NOT yet pinned down. Do
        not use this to rewrite geometry; use it to inspect. Chunk-level
        editing (swap/replace/reorder whole chunks) is lossless and safe --
        that is what C3File.to_bytes round-trips.
        """
        if chunk.tag not in (b"PHY ", b"PHY4"):
            return None
        b = chunk.body
        name = self.node_name(chunk)
        if name is None:
            return None
        (n,) = struct.unpack_from("<I", b, 0)
        off = 4 + n
        if off + 8 > len(b):
            return None
        flag, count = struct.unpack_from("<II", b, off)
        return {
            "name": name,
            "skinned": flag == 2,
            "flag_raw": flag,
            "count_raw": count,
            "body_len": len(b),
            "data_offset": off + 8,
        }

    def __len__(self) -> int:
        return len(self.chunks)

    def __repr__(self) -> str:
        return f"<C3File {len(self.chunks)} chunks: {'+'.join(self.tags())}>"


# ---------------------------------------------------------------------------
# DDS
# ---------------------------------------------------------------------------

@dataclass
class DdsInfo:
    width: int
    height: int
    mipmaps: int
    fourcc: str
    rgb_bits: int
    has_alpha: bool

    @property
    def compressed(self) -> bool:
        return self.fourcc.startswith("DXT")

    def __str__(self) -> str:
        f = self.fourcc or f"RGB{self.rgb_bits}"
        return f"{self.width}x{self.height} {f} mips={self.mipmaps}"


def dds_info(data: bytes) -> Optional[DdsInfo]:
    """Read a DDS header. VERIFIED against the standard DDS_HEADER layout on
    all 19295 archived and ~48000 loose DDS files in this install."""
    if len(data) < 128 or data[:4] != b"DDS ":
        return None
    height, width = struct.unpack_from("<II", data, 12)
    (mips,) = struct.unpack_from("<I", data, 28)
    pf_flags, fourcc = struct.unpack_from("<II", data, 80)
    rgb_bits = struct.unpack_from("<I", data, 88)[0]
    fc = ""
    if pf_flags & 0x4:
        fc = struct.pack("<I", fourcc).decode("latin1").strip("\0 ")
    return DdsInfo(width, height, mips, fc, rgb_bits, bool(pf_flags & 0x1))


# ---------------------------------------------------------------------------
# DMap
# ---------------------------------------------------------------------------

@dataclass
class DMapCell:
    blocked: int
    surface: int
    elevation: int


@dataclass
class DMap:
    """World map.

    VERIFIED on all 136 .DMap files in map/map/: 8-byte header union, then a
    fixed ``char[260]`` puzzle path, then uint32 width/height, then
    height*width cells of ``uint16 blocked, uint16 surface, int16 elevation``
    with a uint32 checksum after each row, then the passageway table, then the
    layer table.

    The header is a union of two forms, both 8 bytes:
      * ``uint32 version, uint32 0``      -- versions 1003/1004/1005/1006 (115 files)
      * ``"DMAP" + 3-char version + NUL`` -- versions "100"/"101" (21 files)

    Layer *bodies* are typed by a leading uint32 (cover / effect / scene / sound);
    those sub-structures are documented in the wiki and parsed lazily by
    ``iter_layers`` rather than eagerly, because layer type coverage in this
    install has not been exhaustively verified.
    """
    version: str
    puzzle_path: str
    width: int
    height: int
    cells_offset: int
    passageways: list[tuple[int, int, int]]
    layer_count: int
    layers_offset: int
    data: bytes = field(repr=False, default=b"")

    @classmethod
    def load(cls, path: Path) -> "DMap":
        return cls.parse(Path(path).read_bytes())

    @classmethod
    def parse(cls, d: bytes) -> "DMap":
        if len(d) < 276:
            raise ValueError("too small for a DMap")
        if d[:4] == b"DMAP":
            version = d[4:8].split(b"\0")[0].decode("latin1")
        else:
            ver, zero = struct.unpack_from("<II", d, 0)
            if zero != 0:
                raise ValueError(f"unexpected header form: {d[:8]!r}")
            version = str(ver)
        off = 8
        puzzle = d[off:off + 260].split(b"\0")[0].decode("latin1")
        off += 260
        width, height = struct.unpack_from("<II", d, off)
        off += 8
        if not (0 < width < 8000 and 0 < height < 8000):
            raise ValueError(f"implausible dimensions {width}x{height}")
        cells_offset = off
        off += width * height * 6 + height * 4
        if off + 4 > len(d):
            raise ValueError("cell block overruns file")
        (npass,) = struct.unpack_from("<I", d, off)
        off += 4
        passageways = []
        for _ in range(npass):
            passageways.append(struct.unpack_from("<III", d, off))
            off += 12
        (nlayer,) = struct.unpack_from("<I", d, off)
        off += 4
        return cls(version, puzzle, width, height, cells_offset,
                   passageways, nlayer, off, d)

    def cell(self, x: int, y: int) -> DMapCell:
        """Cell at (x, y). Rows are stored y-major with a uint32 checksum
        trailing each row."""
        row = self.cells_offset + y * (self.width * 6 + 4)
        blocked, surface, elev = struct.unpack_from("<HHh", self.data, row + x * 6)
        return DMapCell(blocked, surface, elev)

    def walkable_mask(self) -> list[list[bool]]:
        out = []
        for y in range(self.height):
            row = self.cells_offset + y * (self.width * 6 + 4)
            line = []
            for x in range(self.width):
                (blocked,) = struct.unpack_from("<H", self.data, row + x * 6)
                line.append(blocked == 0)
            out.append(line)
        return out


@dataclass
class Pul:
    """Puzzle: the tiled background of a map.

    VERIFIED on all 381 .pul files in map/puzzle/: ``char[8] version``,
    ``char[256] ani path``, ``uint32 width``, ``uint32 height``, then
    height*width ``uint16`` indices into the named .ani file. Version
    ``PUZZLE2`` (350 files) appends two uint32 roll-speed fields; version
    ``PUZZLE`` (31 files) does not. Every file's length matched exactly.
    """
    version: str
    ani_path: str
    width: int
    height: int
    tiles: list[int]
    roll_speed: Optional[tuple[int, int]] = None

    @classmethod
    def load(cls, path: Path) -> "Pul":
        return cls.parse(Path(path).read_bytes())

    @classmethod
    def parse(cls, d: bytes) -> "Pul":
        version = d[:8].split(b"\0")[0].decode("latin1")
        ani = d[8:264].split(b"\0")[0].decode("latin1")
        width, height = struct.unpack_from("<II", d, 264)
        n = width * height
        tiles = list(struct.unpack_from(f"<{n}H", d, 272))
        roll = None
        if len(d) >= 272 + n * 2 + 8:
            roll = struct.unpack_from("<II", d, 272 + n * 2)
        return cls(version, ani, width, height, tiles, roll)


@dataclass
class ScenePart:
    path: str
    title: str
    origin: tuple[int, int]
    frame_interval: int
    width: int
    height: int
    thickness: int
    offset: tuple[int, int]
    offset_elevation: int


@dataclass
class Scene:
    """A multi-piece scenery object (bridges, buildings, ...).

    VERIFIED on all 299 .scene files in map/Scene/: ``uint32 partCount`` then
    per part ``char[256] path``, ``char[64] title``, eight uint32 fields
    (originX, originY, frameInterval, width, height, thickness, offsetX,
    offsetY), ``int32 offsetElevation``, then width*height cells of 12 bytes
    (uint32 blocked, uint32 surface, int32 elevation). Every file consumed to
    exactly its length.

    Note the sibling map/ScenePart/*.Part files are NOT this format -- they are
    plain CRLF text (AniFile=, Width=, Cell[x,y]={a,b,c}) and are the
    human-editable source form. Editing those is by far the easiest way into
    map scenery.
    """
    parts: list[ScenePart]

    @classmethod
    def load(cls, path: Path) -> "Scene":
        return cls.parse(Path(path).read_bytes())

    @classmethod
    def parse(cls, d: bytes) -> "Scene":
        (n,) = struct.unpack_from("<I", d, 0)
        off = 4
        parts = []
        for _ in range(n):
            path = d[off:off + 256].split(b"\0")[0].decode("latin1"); off += 256
            title = d[off:off + 64].split(b"\0")[0].decode("latin1"); off += 64
            ox, oy, fi, w, h, th, dx, dy = struct.unpack_from("<8I", d, off); off += 32
            (el,) = struct.unpack_from("<i", d, off); off += 4
            off += w * h * 12
            parts.append(ScenePart(path, title, (ox, oy), fi, w, h, th, (dx, dy), el))
        return cls(parts)


def parse_scene_part_text(path: Path) -> dict:
    """map/ScenePart/*.Part -- plain CRLF text, not binary. VERIFIED by
    inspection; all 126 files in this install begin with 'AniFile='."""
    out: dict = {"cells": {}}
    for line in Path(path).read_text("latin1", errors="replace").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.startswith("Cell["):
            coord = k[5:k.index("]")]
            x, y = (int(t) for t in coord.split(","))
            out["cells"][(x, y)] = tuple(int(t) for t in v.strip("{}").split(","))
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# virtual filesystem
# ---------------------------------------------------------------------------

@dataclass
class Located:
    logical: str
    source: str          # "loose" | "c3.wdf" | "data.wdf"
    real_path: Optional[Path]
    size: int

    def __str__(self) -> str:
        return f"{self.logical}  [{self.source}, {self.size} bytes]"


class AssetRoot:
    """The client's asset namespace: loose files shadow the archives.

    **The archives are a set, not a pair, and not all one format.** This used
    to be hardcoded to `c3.wdf` + `data.wdf`; `Zephyr-1057-local` ships
    `c3.tpi`/`c3.tpd` *and* `garments0-4.wdf` in one install, and 7878 ships
    four DatPkg pairs and no WDF at all. Both were unopenable. See
    `_discover_archives` for what is found and `_TpdContainer` for the second
    reader -- including why a DatPkg root resolves names exactly where a WDF
    root resolves them through a recovered hash table.

    VERIFIED load order. TqPackage!TqFOpen calls two handlers in sequence: the
    native-filesystem handler first, and only if it returns 3 (not found) does
    it fall through to the WDF handler, which is a thunk through the function
    table populated from the dynamically loaded TqPackageWdf.dll. Corroborated
    on disk: 28 loose files shadow same-named WDF entries with different bytes,
    all stamped 2022-12-29 against archives stamped 2017-12-11, and 3868 further
    loose files have no WDF entry at all.

    Consequence, and the whole basis of the mod workflow: **dropping a file at
    the right relative path under the install root overrides the archive.** The
    .wdf files never need to be repacked.
    """

    # `ARCHIVES = ("c3.wdf", "data.wdf")` used to live here and was kept "for
    # callers that read it" when discovery replaced it. MEASURED: there are
    # none -- the only greps outside this file are comments and
    # `wdf_recover.py`'s own unrelated module-level constant. So it was a
    # class attribute asserting the archives are two WDF files, which is false
    # on every DatPkg root, read by nobody, and exactly the shape that gets
    # believed later. What replaced it is below, and it is a function of the
    # root rather than a constant, because that is what the question actually
    # depends on.

    #: **A root is a set of archives, each with its own reader** -- not "a WDF
    #: root" or "a TPD root". `Zephyr-1057-local` settles it: it ships
    #: `c3.tpi`/`c3.tpd` AND `garments0-4.wdf`, both containers in one install,
    #: so a two-way switch on the root would have to pick one and lose the
    #: other.
    #:
    #: Index suffix -> reader. `.tpi` is the index half of the pair; `tpd.py`
    #: finds the `.tpd` beside it.
    CONTAINERS = ((".wdf", WdfArchive), (".tpi", _TpdContainer))

    #: Tried first and in this order, so the primary pair keeps the precedence
    #: it has always had. Everything else on disk is opened after them.
    ARCHIVE_STEMS = ("c3", "data")

    @classmethod
    def _discover_archives(cls, root: Path) -> list[Path]:
        """Every archive in `root`, primary pair first.

        MEASURED before this replaced the hardcoded pair: 5017, 5065, 5165,
        5517, 6090 and 6609 each ship **exactly** `c3.wdf` and `data.wdf`, so
        discovery is a no-op on every client that worked before it. Only the
        two that could not be opened at all gain anything -- 7878 (four TPD
        pairs, two of them patch overlays) and Zephyr (a TPD pair plus five
        garment WDFs that were previously invisible even though the root was
        already unopenable for a different reason).
        """
        found: list[Path] = []
        seen: set[str] = set()
        for stem in cls.ARCHIVE_STEMS:
            for suffix, _reader in cls.CONTAINERS:
                p = root / f"{stem}{suffix}"
                if p.is_file():
                    found.append(p)
                    seen.add(p.name.lower())
        for suffix, _reader in cls.CONTAINERS:
            for p in sorted(root.glob(f"*{suffix}")):
                if p.is_file() and p.name.lower() not in seen:
                    found.append(p)
                    seen.add(p.name.lower())
        return found

    @classmethod
    def _reader_for(cls, path: Path):
        for suffix, reader in cls.CONTAINERS:
            if path.suffix.lower() == suffix:
                return reader
        return None

    def __init__(self, root: Path | str | None = None, overlay: Optional[Path] = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        if not self.root.is_dir():
            raise FileNotFoundError(coroot.RootNotFound(
                coroot.last_report()).message())
        missing = coroot.missing_parts(self.root)
        if missing:
            raise FileNotFoundError(
                f"{self.root} is not a complete install: missing "
                + ", ".join(missing)
                + "\nPoint the tools somewhere else with "
                  "`py -3 core/coroot.py --set DIR`, CO_ROOT, or --root.")
        self.overlay = Path(overlay) if overlay else None
        self._archives: dict = {}
        for p in self._discover_archives(self.root):
            reader = self._reader_for(p)
            if reader is None:                      # pragma: no cover
                continue
            # An archive that will not open is not silently skipped: a client
            # missing half its art because one container failed reads exactly
            # like a client that never shipped it.
            self._archives[p.name] = reader(p)
        self._names: Optional[dict[int, str]] = None

    # -- name recovery -----------------------------------------------------
    #: Recovered WDF filename tables, best first. `out/wdf/{c3,data}_names.json`
    #: together carry 24,426 of 24,757 hashes (98.66%) and are what
    #: `tools/wdf_recover.py` produces. `out/dll/wdf_name_recovery.json` is the
    #: first pass and holds only 10,126 — kept as a fallback for the case where
    #: `out/` has not been bootstrapped, never preferred over the newer pair.
    NAME_TABLES = ("out/wdf/c3_names.json", "out/wdf/data_names.json")

    #: Hashes where two recovery runs produced DIFFERENT names, written by
    #: `tools/wdf_merge_names.py`. One hash is one archive entry, so both
    #: cannot be right; the merge keeps the committed name and records the
    #: loser here rather than discarding it.
    #:
    #: **A resolved conflict that forgets it happened is the failure this
    #: avoids.** The recovery verifies an enumerated name only by checking the
    #: payload's magic against the claimed extension, and against ~1,534
    #: expected spurious hash hits that dropped 7 -- a collision onto a
    #: same-extension neighbour is invisible to it. So a name being contested
    #: is real information about how much to trust it, and the only place it
    #: survives.
    CONTESTED_TABLES = ("out/wdf/c3_contested_names.json",
                        "out/wdf/data_contested_names.json")
    NAME_TABLE_FALLBACK = "out/dll/wdf_name_recovery.json"

    @staticmethod
    def _read_name_table(p: Path) -> dict[int, str]:
        """Both on-disk shapes: a flat {hexhash: name} dict (wdf_recover.py) or
        one nested under a "resolved" key (the first-pass dump)."""
        raw = json.loads(p.read_text("utf-8"))
        if isinstance(raw, dict) and "resolved" in raw:
            raw = raw["resolved"]
        return {int(k, 16): v for k, v in raw.items()}

    def load_names(self, path: Path | str | None = None) -> int:
        """Load the recovered hash->name tables. With no argument, merges every
        table in NAME_TABLES, falling back to the first-pass dump only if none
        of them exist.

        Relative paths go through ``coroot.find_derived``, so a linked git
        worktree reads the primary checkout's tables instead of silently
        seeing none.

        **Skipped when no WDF archive is open**, because the tables exist to
        put names back on WDF entries, which are keyed by ``tq_hash(name)``
        alone. A DatPkg index stores plaintext paths, so there is nothing to
        recover: a 7878 root used to build 24,426 hash->name entries it could
        never consult. The condition is *no WDF present*, **not** *is this a
        DatPkg root* -- `Zephyr-1057-local` is both at once, and its five
        `garments*.wdf` need the tables exactly as any official client does.
        An explicit ``path`` still loads unconditionally, so a caller that
        knows what it wants is never second-guessed."""
        base = Path(__file__).resolve().parent.parent

        def resolve(x) -> Path:
            q = Path(x)
            if q.is_absolute():
                return q
            return coroot.find_derived(str(q)) or (base / q)

        if path is not None:
            self._names = self._read_name_table(resolve(path))
            return len(self._names)

        if not any(n.lower().endswith(".wdf") for n in self._archives):
            self._names = {}
            return 0

        merged: dict[int, str] = {}
        for rel in self.NAME_TABLES:
            p = resolve(rel)
            if p.is_file():
                merged.update(self._read_name_table(p))
        if not merged:
            p = resolve(self.NAME_TABLE_FALLBACK)
            if p.is_file():
                merged = self._read_name_table(p)
        self._names = merged
        return len(self._names)

    def contested_names(self) -> dict:
        """`{hash: {kept, kept_from, rejected, rejected_from}}`, or `{}`.

        Resolved through `coroot.find_derived` for the same reason
        `load_names` is: a linked worktree must read the primary checkout's
        derived tree, and reading `./out/wdf` from a worktree finds nothing
        and reports "no disagreements" -- which is the wrong answer in the
        one direction that matters.

        Empty is a real answer here and means "the tables were installed by a
        merge that found no conflict, or by a plain run that never looked".
        It does NOT mean the names are certain.
        """
        base = Path(__file__).resolve().parent.parent
        out: dict = {}
        for rel in self.CONTESTED_TABLES:
            p = coroot.find_derived(rel) or (base / rel)
            try:
                if p and Path(p).is_file():
                    doc = json.loads(Path(p).read_text("utf-8"))
                    if isinstance(doc, dict):
                        out.update({int(k, 16): v for k, v in doc.items()})
            except (OSError, ValueError):
                continue
        return out

    def name_for(self, name_hash: int) -> Optional[str]:
        if self._names is None:
            try:
                self.load_names()
            except Exception:
                self._names = {}
        return self._names.get(name_hash)

    # -- lookup ------------------------------------------------------------
    def locate(self, logical: str) -> Optional[Located]:
        """Resolve a logical path (e.g. "c3/texture/001130200.dds") the way the
        client does: overlay, then loose file, then archives.

        A non-string `logical` is refused by name rather than left to fail on
        the next attribute access. `None` reaches here whenever a caller's own
        lookup missed -- an appearance id that this client does not ship, most
        often -- and the bare `.replace` turned that into
        `AttributeError: 'NoneType' object has no attribute 'replace'` four
        frames below the caller, which reads as a fault in the asset layer
        instead of a miss upstream. Loud, with the fix in the message.
        """
        if not isinstance(logical, str):
            raise TypeError(
                f"locate() wants a logical path string, got {type(logical).__name__}"
                f" ({logical!r}). A None here usually means the caller's own "
                f"lookup missed -- check that before looking at the asset layer.")
        logical = logical.replace("\\", "/").lstrip("/")
        # Both joins are confined. `logical` reaches here from HTTP query
        # strings (`/api/mesh`, `/api/texture`, `/api/rawinfo`), so a bare
        # `root / logical` let `../` walk out of the install and read any file
        # the process could open. An escape is refused rather than reported as
        # a miss: `locate` returning None for it would hide an attack as a 404.
        if self.overlay:
            p = safepath.confine(self.overlay, logical)
            if p.is_file():
                return Located(logical, "overlay", p, p.stat().st_size)
        p = safepath.confine(self.root, logical)
        if p.is_file():
            return Located(logical, "loose", p, p.stat().st_size)
        h = tq_hash(logical)
        for aname, arc in self._archives.items():
            # A container that stores plaintext paths is asked for the name
            # itself. That is EXACT, where the hash path is only as good as
            # the recovered name tables -- and it needs no tables at all.
            by_name = getattr(arc, "get_by_name", None)
            e = by_name(logical) if by_name is not None else arc.get(h)
            if e:
                return Located(logical, aname, None, e.size)
        return None

    def read(self, logical: str) -> bytes:
        loc = self.locate(logical)
        if loc is None:
            raise FileNotFoundError(logical)
        if loc.real_path:
            return loc.real_path.read_bytes()
        arc = self._archives[loc.source]
        by_name = getattr(arc, "get_by_name", None)
        if by_name is not None:
            e = by_name(loc.logical)
            if e is not None:
                return arc.read_entry(e)
        return arc.read_by_hash(tq_hash(loc.logical))

    def exists(self, logical: str) -> bool:
        return self.locate(logical) is not None

    # -- appearance tables -------------------------------------------------
    def part_tables(self) -> dict[str, PartIni]:
        """Load every appearance table named by ini/RolePart.ini that actually
        ships in this install."""
        cfg = parse_ini(self.root / "ini" / "RolePart.ini")
        out: dict[str, PartIni] = {}
        conf = cfg.get("Config", {})
        n = int(conf.get("Count", "0") or 0)
        for i in range(n):
            part = conf.get(f"Part{i}")
            rel = conf.get(f"MeshIni{i}")
            if not part or not rel:
                continue
            p = self.root / rel.replace("/", "\\")
            if p.is_file() and rel not in {t.path.name for t in out.values()}:
                # NARROWED from `except Exception: pass` (2026-08-10), for the
                # same reason as `PartIni.__init__`: this one sits one layer
                # up and would have re-hidden anything the narrowing there
                # let through. A table that is declared in the config and
                # present on disk but unreadable is dropped from the mapping
                # with no key, so a caller sees "this install has no armour
                # table" -- the shape `dbc.weaponmotion_join` was changed to
                # refuse rather than return empty.
                try:
                    out[part] = PartIni(p)
                except (ValueError, struct.error, OSError):
                    continue
        return out

    # -- ID -> file --------------------------------------------------------
    # INFERRED: bare numeric IDs in the appearance tables are resolved by
    # probing these subdirectories, trying the ID as written, zero-padded to 9
    # digits, and stripped of leading zeros. This reproduces 94% of armor.ini,
    # 98% of weapon.ini and 79% of armet.ini references. The client's real rule
    # lives inside the Themida-packed exe and has not been read directly.
    MESH_DIRS = ("mesh", "weapon", "body", "hair", "mount", "npc", "monster")
    TEX_DIRS = ("texture", "weapon", "body", "hair", "mount", "npc", "monster")

    def resolve_asset(self, asset_id: str, kind: str = "texture") -> Optional[Located]:
        if not asset_id or asset_id == "0":
            return None
        # A reference that is already a path (synthesised tables for old
        # clients name their meshes by full path -- bare IDs never contain a
        # slash) resolves directly, no directory probing.
        if "/" in asset_id or "\\" in asset_id:
            return self.locate(asset_id)
        dirs = self.TEX_DIRS if kind == "texture" else self.MESH_DIRS
        ext = ".dds" if kind == "texture" else ".c3"
        ids = []
        for cand in (asset_id, asset_id.zfill(9), asset_id.lstrip("0")):
            if cand and cand not in ids:
                ids.append(cand)
        for sub in dirs:
            for i in ids:
                loc = self.locate(f"c3/{sub}/{i}{ext}")
                if loc:
                    return loc
        return None

    def resolve_appearance(self, ident: str, table: Optional[str] = None):
        """Find an appearance ID across the part tables and resolve its files.

        Returns a list of dicts, one per matching table, each with the part's
        mesh/texture references and where each one actually lives.
        """
        results = []
        for part_name, ini in self.part_tables().items():
            if table and part_name != table:
                continue
            app = ini.get(ident)
            if not app:
                continue
            parts = []
            for pr in app.parts:
                parts.append({
                    "index": pr.index,
                    "mesh_id": pr.mesh,
                    "mesh": self.resolve_asset(pr.mesh, "mesh"),
                    "texture_id": pr.texture,
                    "texture": self.resolve_asset(pr.texture, "texture"),
                    "material": pr.material,
                })
            results.append({"part": part_name, "ini": ini.name,
                            "ident": app.ident, "parts": parts})
        return results

    def close(self) -> None:
        for a in self._archives.values():
            a.close()

    def __enter__(self) -> "AssetRoot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# item catalogue
# ---------------------------------------------------------------------------

def load_items(root: Path = DEFAULT_ROOT) -> list[dict]:
    """The item table: id + name + stats, one dict per item.

    CCO ships it as plain JSON (ini/itemtype.json, 11142 items). Official
    clients carry the same data only as a TQ-cipher-encrypted
    ini/itemtype.dat (24,270 items in 6090), which ``tqdat`` decrypts and
    parses into rows keyed the same way, so both roots serve the same shape.
    Item names are labels over the appearance tables, not structure, so an
    install with neither table -- or a .dat some other seed encrypted --
    yields no rows rather than an exception. The character builder then
    offers every option unnamed instead of refusing to open.
    """
    p = Path(root) / "ini" / "itemtype.json"
    if p.is_file():
        return json.loads(p.read_text("utf-8", errors="replace"))
    p = Path(root) / "ini" / "itemtype.dat"
    if p.is_file():
        try:
            return tqdat.read_itemtype(p)
        except ValueError:
            return []
    return []


def find_items(name_substr: str, root: Path = DEFAULT_ROOT) -> list[dict]:
    q = name_substr.lower()
    return [i for i in load_items(root) if q in str(i.get("name", "")).lower()]
