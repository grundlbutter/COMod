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
import safepath                     # noqa: E402
from tqhash import tq_hash          # noqa: E402
from wdf import WdfArchive          # noqa: E402

#: The install root, resolved once per process by ``coroot`` -- explicit
#: ``--root`` beats ``CO_ROOT`` beats a saved config beats auto-discovery.
#: It is a plain Path so it can go on being a default argument value; when
#: nothing is found it falls back to the conventional path and the failure
#: surfaces in ``AssetRoot`` with a message that names what is missing.
DEFAULT_ROOT = coroot.default_root()


# ---------------------------------------------------------------------------
# ini tables
# ---------------------------------------------------------------------------

def parse_ini(path: Path, encoding: str = "latin1") -> dict[str, dict[str, str]]:
    """Parse a TQ-style ini into {section: {key: value}}.

    TQ inis are not standard: keys repeat across sections, values are untyped,
    and section names are usually numeric asset IDs. codepage.ini is "0" in this
    install, so text is read as latin1 and any GBK names are kept as raw bytes
    round-trippable through latin1.
    """
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
        self.sections = parse_ini(self.path)
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

    def __init__(self, data: bytes):
        if not data.startswith(C3_MAGIC):
            raise ValueError(f"not a C3 file (magic {data[:16]!r})")
        self.magic = data[:16]
        self.chunks: list[C3Chunk] = []
        off = 16
        while off + 8 <= len(data):
            tag = bytes(data[off:off + 4])
            (size,) = struct.unpack_from("<I", data, off + 4)
            if off + 8 + size > len(data):
                raise ValueError(
                    f"chunk {tag!r} at {off} claims {size} bytes, only "
                    f"{len(data) - off - 8} remain"
                )
            self.chunks.append(C3Chunk(tag, bytes(data[off + 8:off + 8 + size]), off))
            off += 8 + size
        if off != len(data):
            raise ValueError(f"trailing bytes: stopped at {off}, file is {len(data)}")

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
    """The client's asset namespace: loose files shadow the WDF archives.

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

    ARCHIVES = ("c3.wdf", "data.wdf")

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
        self._archives: dict[str, WdfArchive] = {}
        for a in self.ARCHIVES:
            p = self.root / a
            if p.is_file():
                self._archives[a] = WdfArchive(p)
        self._names: Optional[dict[int, str]] = None

    # -- name recovery -----------------------------------------------------
    #: Recovered WDF filename tables, best first. `out/wdf/{c3,data}_names.json`
    #: together carry 24,426 of 24,757 hashes (98.66%) and are what
    #: `tools/wdf_recover.py` produces. `out/dll/wdf_name_recovery.json` is the
    #: first pass and holds only 10,126 — kept as a fallback for the case where
    #: `out/` has not been bootstrapped, never preferred over the newer pair.
    NAME_TABLES = ("out/wdf/c3_names.json", "out/wdf/data_names.json")
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
        seeing none."""
        base = Path(__file__).resolve().parent.parent

        def resolve(x) -> Path:
            q = Path(x)
            if q.is_absolute():
                return q
            return coroot.find_derived(str(q)) or (base / q)

        if path is not None:
            self._names = self._read_name_table(resolve(path))
            return len(self._names)

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
        client does: overlay, then loose file, then archives."""
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
            e = arc.get(h)
            if e:
                return Located(logical, aname, None, e.size)
        return None

    def read(self, logical: str) -> bytes:
        loc = self.locate(logical)
        if loc is None:
            raise FileNotFoundError(logical)
        if loc.real_path:
            return loc.real_path.read_bytes()
        return self._archives[loc.source].read_by_hash(tq_hash(loc.logical))

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
                try:
                    out[part] = PartIni(p)
                except Exception:
                    pass
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
    """ini/itemtype.json -- 11142 items, id + name + stats. Plain JSON."""
    p = Path(root) / "ini" / "itemtype.json"
    return json.loads(p.read_text("utf-8", errors="replace"))


def find_items(name_substr: str, root: Path = DEFAULT_ROOT) -> list[dict]:
    q = name_substr.lower()
    return [i for i in load_items(root) if q in str(i.get("name", "")).lower()]
