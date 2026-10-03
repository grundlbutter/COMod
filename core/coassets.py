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

import contextlib
import hashlib
import json
import os
import pickle
import random
import struct
import sys
import zlib
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
        #: A `tpdcache.TpdCache`, or None. Set by `AssetRoot.use_tpd_cache`;
        #: this module never imports the cache (it is vendored into the
        #: Blender add-on, which has no settings store to switch it on).
        self.cache = None

    def _read(self, e) -> bytes:
        """Every whole-entry read, through the inflate cache when one is
        attached and on. The cache is keyed on the stored bytes, so they are
        read ONCE and either looked up or inflated -- never read twice."""
        c = self.cache
        if c is None or not getattr(c, "on", False) or e.flag not in (1, 2):
            return self._arc.read(e)
        raw = self._arc.read_compressed(e)
        hit = c.get(e, raw)
        if hit is not None:
            return hit
        data = self._arc.inflate(e, raw)
        c.put(e, raw, data)
        return data

    def read_compressed(self, e) -> bytes:
        """The stored bytes of one entry (for the cache's prepare job)."""
        return self._arc.read_compressed(e)

    def inflate_into_cache(self, e, raw: bytes) -> bytes:
        """Inflate stored bytes already read and store the result, if a cache
        is attached and on. Returns the inflated bytes either way."""
        data = self._arc.inflate(e, raw)
        c = self.cache
        if c is not None and getattr(c, "on", False):
            c.put(e, raw, data)
        return data

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
        return self._read(e)

    # -- the exact interface, preferred when present -----------------------
    def get_by_name(self, logical: str):
        return self._by_name.get(logical.replace("\\", "/").lower())

    def read_entry(self, entry) -> bytes:
        return self._read(entry)

    def read(self, entry) -> bytes:
        """`WdfArchive`'s name for `read_entry`, so a caller that ENUMERATES
        works against either container.

        THE SAME GAP `file_size` DOCUMENTS, FOUND THE SAME WAY. This adapter
        claims `WdfArchive`'s interface, and `read` is the half of it a caller
        uses when it walks `entries` instead of looking a path up. Every
        consumer written before 2026-09-05 looked paths up, so the omission
        had no way to show -- until `tools/validate_phy.archive_sources` was
        widened to walk archives and got an `AttributeError` per entry, caught
        by its own per-entry `except`, and reported **59,964 entries, 0 C3**
        for a `c3.tpi` holding 37,174 containers. A PASS with an empty archive
        behind it, which is the exact shape of the census gap that motivated
        the widening.
        """
        return self._read(entry)

    def peek(self, entry, n: int = 32) -> bytes:
        """First `n` bytes WITHOUT inflating the whole payload.

        `WdfArchive.peek` exists so a caller can test a magic number without
        paying for a 5 MB texture, and a magic test is how both writer gates
        decide whether an entry is a C3 container. Without this the widened
        walk inflates all 146,113 entries of a 7632 root to look at eight
        bytes each.

        A TPD entry is one or more INDEPENDENT zlib streams (see
        `TpdArchive.read`), so the FIRST chunk alone inflates to a valid
        prefix. `read_compressed` reads whatever chunks the object it is given
        carries, so a stand-in carrying only the first one is enough and no
        private state is touched. Anything unrecognised falls back to a full
        read: a wrong short answer here silently DROPS a container from a
        gate's corpus, and being slow is the cheaper failure.
        """
        if entry.flag == 0:                      # empty file, no payload
            return b""
        if entry.flag not in (1, 2) or not entry.chunks:
            return self._arc.read(entry)[:n]

        class _FirstChunkOnly:
            chunks = entry.chunks[:1]

        try:
            comp = self._arc.read_compressed(_FirstChunkOnly)
            return zlib.decompressobj().decompress(comp, n)
        except Exception:                                   # noqa: BLE001
            return self._arc.read(entry)[:n]

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

    def names(self) -> set[str]:
        """The logical paths this container DECLARES, normalised.

        `locate` has always preferred this container's stored path over a
        hash ("that is EXACT, where the hash path is only as good as the
        recovered name tables -- and it needs no tables at all").  What was
        missing is the other direction: ENUMERATING them.  A caller that
        wants to know what an install holds had only the recovered-name
        tables, which are keyed off WDF hashes, so a TPD install's entries
        were invisible to it however exactly they resolve when ASKED for by
        name.  `AssetRoot.container_names` is that caller's answer; `WdfArchive`
        deliberately has no `names` at all, because a WDF index stores only
        `tq_hash(name)` and genuinely declares nothing.
        """
        return set(self._by_name)

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


def _table_candidates(rel: str) -> list[str]:
    """The paths to try for a declared appearance table, best first.

    ``ini/RolePart.ini`` names ``ini/armor.ini``; the compiled ``ROPT`` record
    that says the same thing names ``ini/armor.dbc``, because the packer
    rewrites the path string as well as the payload. Both mean the same table.

    A ``.dbc`` string yields ``[<stem>.ini, <stem>.dbc]`` -- the plaintext
    FIRST, because `PartIni` prefers the compiled twin beside an ini anyway, so
    the ini path reaches the same bytes AND still works on the 113 shipped
    declarations (of 350, over 30 installs) where only the ``.ini`` exists.
    ``.dbc``-only is 0 of 350, so the second candidate has no shipped exercise.

    Anything else yields itself alone, so the plaintext path is byte-identical
    to what this code did before ROPT was read at all.
    """
    low = rel.lower()
    if low.endswith(".dbc"):
        return [rel[:-4] + ".ini", rel]
    return [rel]


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


def _file_identity(path) -> "tuple | None":
    """(size, blake2b-16 of EVERY byte) of a file, or None when it is not there.

    CONTENT, NOT A STAT. The first version keyed on (size, birth, mtime), the
    triple `provenance` uses -- and the must-fire arm
    `test_a_comod_style_same_size_copy_is_parsed_again` showed it cannot see
    COMod's own write: `shutil.copy2` of a same-size table with an equal
    mtime OVER an existing file moved none of the three on this box (SD's
    W1 gate, 2026-09-19), so the cache served the old parse after an install.
    Hashing the bytes costs a read of the table (armor.ini is ~700 KB) --
    far cheaper than the parse it guards -- and it moves exactly when the
    content does. Same fix, same reason, as patchdict's container identity."""
    import hashlib                                        # noqa: PLC0415
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return (len(data), hashlib.blake2b(data, digest_size=16).hexdigest())


#: Parsed appearance tables by file, re-parsed only when the file moves.
#: `AssetRoot.part_tables()` has 30 callers and re-parsed every table on every
#: call -- MEASURED 2026-09-18 on CCO: 22 `PartIni` parses, 2.47 s of a 2.7 s
#: `/api/buildersatellites` call, which the Character Builder makes on EVERY
#: body switch (twice: `assetroot._tables_by_file` and
#: `c3tex._appearance_meshes`). Keyed by the table's path with the identity of
#: the table AND its compiled `.dbc` twin (PartIni reads both), so a table a
#: mod replaces is parsed again rather than served stale. Process-wide and
#: guarded, because the viewer serves requests on threads.
_PART_INI_CACHE: dict = {}
_PART_INI_LOCK = __import__("threading").Lock()


def _part_ini_cached(path) -> "PartIni":
    """A parsed table, FRESH per call -- never an object another caller holds.

    NOT SHARED, BY CONSTRUCTION (SD ruling, 2026-09-18). Handing every caller
    the same `PartIni` would make the cache safe only as long as nobody ever
    mutates one -- a convention the next caller need not know about. So the
    cache holds a PICKLED SNAPSHOT and every hit unpickles its own copy: a
    caller can mutate what it gets and no other caller can see it. Unpickling
    is C code and is measured against the 2.47 s parse it replaces (OWED
    night batch); if it gives that back, freezing is the next option, not
    sharing."""
    import pickle                                         # noqa: PLC0415
    path = Path(path)
    ident = (_file_identity(path), _file_identity(path.with_suffix(".dbc")))
    key = str(path).lower()
    with _PART_INI_LOCK:
        hit = _PART_INI_CACHE.get(key)
    if hit is not None and hit[0] == ident and ident[0] is not None:
        return pickle.loads(hit[1])
    t = PartIni(path)                  # raises exactly as before on a bad table
    blob = pickle.dumps(t, protocol=pickle.HIGHEST_PROTOCOL)
    with _PART_INI_LOCK:
        _PART_INI_CACHE[key] = (ident, blob)
    return t                           # fresh: nothing else holds it


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

# ---------------------------------------------------------------------------
# THIS TABLE IS THE ANSWER TO "DO WE UNDERSTAND THIS CHUNK", AND IT WAS WRONG
# ---------------------------------------------------------------------------
# `C3Chunk.described` is the only place a caller asks that question, and three
# consumers ask it: `tools/comod.py info`, `tools/coviewer.py`'s C3 endpoint,
# and `routeb/oracle.py`'s c3.tags dimension. Until 2026-09-04 the table
# omitted FOUR tags this repo already decodes, so all three answered
# "unknown tag" about chunks that had working, verified readers:
#
#   PHY2 PHY3 PHY5   decoded by `core/c3phy.py` -- the variant table there is
#                    lifted from graphic.dll RVA 0x25BEC and has covered all
#                    five PHY forms since it landed. Only PHY /PHY4 were listed.
#   PTCX             decoded by `tools/effects.py` (`parse_ptcl`, generation 2)
#                    against the `Ptcl_Load(C3Ptcl2**)` overload at RVA 0x60A50,
#                    and re-proved by `tools/ptclprove.py`.
#
# The cost was not cosmetic. `oracle.py` scored those instances as UNKNOWN and
# filed them as a reverse-engineering work item; the work was already done.
#
# COUNTS ARE PER INSTALL AND ARE FULL ENUMERATIONS, not samples -- loose files
# plus every archive entry, walked 2026-09-04. They are given as a RANGE across
# the installs surveyed (4274, 5065, 5517, 6090, 6805, 7632) because the
# previous wording quoted one corpus as if it were the format:
#
# **AND THAT SURVEY IS SIX INSTALLS OF THIRTY-FOUR.** The table below is
# correct and it is a SIXTH of the corpus, which is the whole subject of
# `docs/claim_enumeration_audit_2026-09-07.md`: every "no other install" and
# every "--" in this table was a statement about six directories wearing the
# grammar of a statement about all of them, and four of the tag rows were
# wrong because of it (`MNEW`, `CCFL`, `OMNI`, and `RMOT` three rows further
# down). The full walk is `scratchpad/c3census_all.py`, which derives the
# install list from `coroot.clients_dir()` instead of taking it as arguments,
# and writes one JSON per install under `scratchpad/census/`. It reproduces
# every figure below exactly -- 7632's 245,463 MOTI, 179,586 PHY-family,
# 45,549 MNEW, 76,364 CCFL -- so the disagreements it surfaces are about
# SCOPE and never about the instrument.
#
# CORPUS TOTALS, 2026-09-07, all 34 installs (`py -3 scratchpad/tagmatrix.py`):
#
#   tag    corpus total   on installs   the row below says
#   MOTI      5,383,741       34 / 34   6 installs
#   PHY4      2,461,645       33 / 34   5
#   CCFL        541,869       13 / 34   1 (7632), and the entry said 4
#   PTC3        455,051       31 / 34   4
#   MNEW        308,742       17 / 34   1 (7632), and the entry said 4
#   PHY         292,213       34 / 34   6
#   CAME         79,522       34 / 34   6
#   PTCL         65,428       34 / 34   6
#   SHAP/SMOT    46,316       34 / 34   6
#   PHY3          2,526       30 / 34   4
#   PHY5          2,488        5 / 34   1 (7632)
#   PTCX            284       29 / 34   4
#   RIBB/RMOT       140        2 / 34   see the RIBB block below
#   OMNI              2        2 / 34   "1 instance, on 4274"
#   PHY2              0        0 / 34   "no install ships one" -- SURVIVED
#
# TWO ONSETS MOVE A WHOLE CLIENT GENERATION. `MNEW` starts at **6907**, with
# one instance in one file, and `CCFL` at **7083** with 19 in two -- not at
# 7632, where both were recorded as starting. A first appearance is a build
# boundary, so this is not only bookkeeping: it says the writer shipped
# earlier than the reader work assumed.
#
# ONE WALKER ARTEFACT, recorded so it is not rediscovered as a tag: the census
# reports a chunk tagged `b'\x00\x00\x80?'` (the float 1.0) once on Zephyr.
# That is the lenient walker mis-framing after one of Zephyr's three known
# MALFORMED CONTAINERS, not a fifteenth tag. It `break`s at the first bad
# chunk, so a malformed file donates whatever it was in the middle of.
#
#   tag    4274     5065     5517     6090     6805      7632     understood by
#   MOTI  10,792   12,362   28,607   58,838  150,232   245,463   effects.parse_moti
#   PHY4     --       244    7,865   16,188   45,749   176,531   c3phy
#   PHY   10,793    8,166    8,410    8,549    9,162     2,841    c3phy
#   PTC3     --       --     1,700    3,744    7,048    36,087    effects.parse_ptcl
#   CAME   4,727    1,758    1,874    2,236    2,449       891    effects.parse_came
#   PTCL     347      737    1,530    1,861    2,118     1,915    effects.parse_ptcl
#   SHAP     565      643      812    1,053    1,304     1,527    effects.parse_shap
#   SMOT     565      643      812    1,053    1,304     1,527    effects.parse_smot
#   CCFL     --       --       --       --       --     76,364    nothing
#   MNEW     --       --       --       --       --     45,549    nothing
#   PHY5     --       --       --       --       --        130    c3phy
#   PHY3     --       --        82       82       85        84    c3phy
#   PTCX     --       --         6        8        8        12    effects.parse_ptcl
#   OMNI       1      --       --       --       --        --     nothing
#
# WHAT CORPUS THIS IS -- AND IT IS NOW THE GATES' CORPUS TOO
# ----------------------------------------------------------
# **CLOSED 2026-09-05, LATER THE SAME DAY. The divergence this section was
# written to explain NO LONGER EXISTS**; `tools/validate_phy.wdf_sources` is
# now `archive_sources` and asks `_discover_archives`, the same discovery this
# census uses. Both writer gates reconcile with these rows on every install
# tested. The account below is kept because the rows are still counted the way
# it says, and because the shape of the gap is worth keeping:
#
#   install   MOTI census / gate      PHY census / gate            verdict
#   4274        10,792 / 10,792         10,793 / 10,793            PASS
#   6090        58,838 / 58,838         24,819 / 24,819            PASS
#   7632       245,463 / 245,463       179,586 / 179,586           PASS
#   7682       245,463 / 245,463       179,586 / 179,586           PASS  (*)
#   7867            -- / 348,482            -- / 282,130           PASS
#   7878            -- / 363,073            -- / 295,665           PASS
#   Zephyr          -- / 132,813            -- / 81,663            FAIL  (+)
#
#   (*) 7682 and 7632 are ONE corpus -- byte-identical archives, `c3.tpi` md5
#       c9efcfc1723b3498004bc3b673cbd821. Identical numbers there are a fact
#       about the installs, not a stuck `CO_ROOT`; the gates print `root:`
#       now so that is answerable from the output.
#   (+) Zephyr's failures are 3 MALFORMED CONTAINERS, not writer defects --
#       every chunk that parsed was byte-exact (132,813/132,813 MOTI,
#       81,663/81,663 PHY). NOTE FOR RECONCILING THEM: the census walker
#       `break`s at the first bad chunk where the gates RAISE, so it counts
#       the good chunks of two of them and zero chunks of the third. Lenient
#       walk, strict gate -- the numbers cannot agree on a malformed file.
#   7867/7878 have no census row above; the table's surveyed set stops at
#       7632, and the gate figures are simply what those installs hold.
#
# MEASURED 2026-09-05, after the 7632 MOTI row failed to reconcile with
# `tests/test_moti_roundtrip.py`. These counts are the census corpus:
#
#     every loose `.c3` ANYWHERE under the install root, plus every `.c3`
#     entry of every archive `AssetRoot._discover_archives` returns --
#     which is `CONTAINERS`, i.e. `.wdf` AND `.tpi`/`.tpd`.
#
# The two writer gates -- `tests/test_roundtrip.py` (PHY) and
# `tests/test_moti_roundtrip.py` (MOTI), which share a source list on purpose
# so both writers are held to the same bytes -- USED to draw a narrower one
# from `tools/validate_phy.py`: loose `$ROOT/c3` and `$ROOT/data`, plus
# `out/wdf/sample`, plus `c3.wdf` and `data.wdf` **BY NAME** -- the
# `for arch in ("c3.wdf", "data.wdf")` loop. That loop is gone.
#
# On an install that ships a `.wdf` pair the two corpora coincide and the
# census reconciles EXACTLY -- and the match is load-bearing, not luck,
# because most of these chunks are inside the archive:
#
#     4274   3,747 loose + 7,045 in c3.wdf  = 10,792   gate: 10,792
#     6090  47,745 loose + 11,093 in c3.wdf = 58,838   gate: 58,838
#
# **7632 SHIPS NO `.wdf` AT ALL.** Its archive pair is `c3.tpd`/`c3.tpi` and
# `data.tpd`/`data.tpi`, so the two-name loop yielded NOTHING and the gate saw
# the loose tree alone:
#
#     7632  81,963 loose + 163,500 in c3.tpi = 245,463   gate WAS: 81,963
#
# The row was RIGHT and the gate's corpus was a strict SUBSET -- on 7632, 7682,
# 7867, 7878 and Zephyr alike, none of which ship `c3.wdf`/`data.wdf` (7867 and
# 7878 ship four `.tpi` each; Zephyr adds five garment `.wdf` that the two-name
# loop also could not see). **The gate now reads 245,463 there.** The lesson
# that outlives the fix: a name-keyed source list fails SILENTLY and only on
# the installs that renamed the thing, and both gates printed PASS throughout.
#
# FALSIFIER for any row here, per install: `scratchpad/motigap.py` prints the
# three buckets and the CENSUS TOTAL / GATE-VISIBLE split, and its total is
# what a row must equal --
#
#     py -3 scratchpad/motigap.py 4274 6090 7632
#     py -3 scratchpad/motigap.py 7632 --tag PHY4
#
# run from the repo root, on a checkout at or after this comment. It counts
# tags with a raw walker that never calls a decoder, so it cannot be made to
# agree by a decoder bug.
#
# The `.7z` files are NOT the explanation, and an earlier note in
# `docs/moti_writer_2026-09-05.md` section 2.3 inferred that they were. There
# are 435, they are under `map/map/` and not `AutoPatch/`/`Download/`, they
# hold exactly ONE entry each, all 435 are `.DMap`, and ZERO are `.c3` -- full
# enumeration, not a sample. Neither walk opens them and neither needs to:
# 81,963 + 163,500 is 245,463 exactly, with nothing left to attribute.
#
# A row here is a CLAIM, and `routeb/oracle_control.py`'s `c3.tags[...]`
# controls are what make it falsifiable: every tag whose entry names a decoder
# is decoded on the install's own chunks and required to consume the body
# exactly, then re-run on the same body with seven bytes cut and required to
# fail. On 5517 that is 82/82 PHY3 and 6/6 PTCX exact. Do not add a row here
# without a decoder that survives that.
C3_TAGS = {
    b"PHY ": "physique / mesh, original encoding -- decoded by core/c3phy.py",
    b"PHY2": "physique / mesh, normals on disk + the 36-byte legacy gap -- "
             "decoded by core/c3phy.py; no install in this corpus ships one, "
             "and the graphic.dll dispatcher still tests for it. **SURVIVED "
             "2026-09-07**: this corpus-wide NEGATIVE rested on the eight "
             "directories the 2026-09-04 census walked, and a full 34-install "
             "walk finds ZERO PHY2 on every one of them. A negative is worth "
             "re-running precisely because nothing about it changes when it "
             "is wrong -- an unwalked install and an install with none read "
             "identically.",
    b"PHY3": "physique / mesh, normals on disk, no legacy gap -- decoded by "
             "core/c3phy.py",
    b"PHY4": "physique / mesh, generated normals, no legacy gap -- decoded by "
             "core/c3phy.py; the bulk form on 5517 and later",
    b"PHY5": "physique / mesh, second UV set (one 0x3C block per vertex) -- "
             "decoded by core/c3phy.py",
    b"MNEW": "a CONTENTLESS MARKER, not a mesh -- the body is one byte, 0x70. "
             "POPULATION CORRECTED 2026-09-07 by a full 34-install census: "
             "308,742 instances on SEVENTEEN installs, and the tag STARTS AT "
             "6907 (one instance, one file -- a first appearance), not at "
             "7632. Per install: 6907 1, 6968 149, 7009 192, 7065 625, "
             "7083 649, 7110 672, 7135 677, 7170 1,809, 7182 1,894, "
             "7189 3,358, 7205 3,822, 7632 45,549, 7682 45,549, 7867 95,184, "
             "7878 102,992, Zephyr 5,589, CCO-snapshot-2026-08-24 31. It read "
             "'all 266,656 instances across 7632/7682/7867/7878' -- four "
             "installs of the 34, and the onset a whole client generation "
             "later than it is. The body constant survived: parse_mnew "
             "consumes 1,616 out-of-sample bodies on 6907..7083 exactly, 0 "
             "refused. And no "
             "renderer in this corpus dispatches the tag (it takes the "
             "container dispatcher's default arm, a seek-past, in both 7632's "
             "and 7878's Env_DX9/graphic.dll). Always immediately follows a "
             "MOTI, 11,267/11,267 sampled. Consumed by tools/effects.py "
             "parse_mnew; what the byte MEANS is not recoverable from these "
             "inputs -- see docs/mnew_ccfl_re_2026-09-06.md section 6",
    b"MOTI": "motion / animation track -- decoded by tools/effects.py",
    b"CAME": "authoring camera: name, FOV, and a per-frame eye track plus a "
             "per-frame look-at track -- decoded by tools/effects.py "
             "parse_came, exact on 13,935/13,935 bodies across the six "
             "installs that finding walked -- 4274, 5065, 5517, 6090, 6805, "
             "7632. POPULATION CORRECTED 2026-09-07: CAME is on ALL 34 "
             "installs and the corpus holds 79,522, so 'all six installs' was "
             "6 of 34 and the number was a sixth of the population. The "
             "DECODE held out of sample, which is the part that matters: "
             "parse_came consumes 17,860 further bodies -- 5017, 5165, 6609, "
             "6907, 7205, Zephyr and CCO-snapshot-2026-08-24 -- exactly, 0 "
             "refused, MORE bodies than the sample it was published on. "
             "A RETIRED chunk, not an undecoded one: only "
             "4274/C3_CORE_DLL.dll and 5065's dispatch the tag, and every "
             "renderer from 5165 on seeks past it, so the 891 still shipping "
             "in 7632 are read by nothing. See docs/came_re_2026-09-06.md",
    b"PTCL": "particle system, generation 1 -- decoded by tools/effects.py",
    b"PTCX": "particle system, generation 2 (adds the per-particle u16 id "
             "array) -- decoded by tools/effects.py, proved by "
             "tools/ptclprove.py",
    b"PTC3": "particle system, generation 3 (adds the whole-system envelope) "
             "-- decoded by tools/effects.py",
    b"SHAP": "shape -- decoded by tools/effects.py",
    b"SMOT": "shape motion -- decoded by tools/effects.py",
    b"RIBB": "ribbon / trail definition, 124 bytes FIXED -- a `C3RibbonTrail` "
             "(Ogre's RibbonTrail : BillboardChain, by RTTI). Decoded by "
             "tools/effects.py parse_ribb against the loader at "
             "7878 Env_DX9/graphic.dll RVA 0x151FB0 (0xE2570 on 7632, the "
             "same function; 7867's is 0x122D40), exact on 140/140 across "
             "7867 and 7878. Eleven of the seventeen fields "
             "are read from the binary; four are read and DISCARDED by the "
             "loader; three are stored to members nothing reads. See the "
             "field map below and docs/ribb_re_2026-09-07.md",
    b"RMOT": "ribbon motion: `u32 n; mat4x4[n]`, the same body as SMOT and "
             "consumed EXACTLY by tools/effects.parse_smot on 140/140 -- "
             "7867's 69 and 7878's 71 (n = 61 and n = 101). CORRECTED "
             "2026-09-07: this read '71/71 of 7878's instances', which is the "
             "RIBB defect over again, three rows below the RIBB entry that "
             "had just been corrected for it -- RMOT pairs with RIBB and its "
             "population is identical, so 7867's 69 were always going to be "
             "there and nobody looked. Full 34-install census; 7867's 69 "
             "parse exactly, out of sample, 0 refused. The wire format is now READ OUT "
             "OF THE LOADER too -- Env_DX9/graphic.dll 0xE23F0 on 7632, "
             "reached from the dispatcher's RMOT arm at 0x11EF: new(0xC), "
             "Read(&n,1,4), malloc(n*0x40) with an overflow guard, "
             "Read(buf,0x40,n). The earlier note that 'nothing DISPATCHES to "
             "that reader for this tag' is superseded: something does",
    b"CCFL": "authoring/provenance sidecar bound to the PRECEDING content "
             "chunk -- a `ccflag` magic then a self-describing list of typed "
             "records, the universal one being a GBK artist string plus a "
             "CreateTime stamp. Decoded by tools/effects.py parse_ccfl, "
             "VERIFIED against 7632 Env_DX9/graphic.dll 0xEFA20 and exact on "
             "481,029 bodies. POPULATION CORRECTED 2026-09-07 by a full "
             "34-install census: 541,869 instances on THIRTEEN installs, "
             "STARTING AT 7083 (19 instances in 2 files), not at 7632. Per "
             "install: 7083 19, 7110 433, 7135 471, 7170 1,352, 7182 1,720, "
             "7189 4,837, 7205 5,144, 7632 76,364, 7682 76,364, "
             "7867 178,842, 7878 187,397, Zephyr 8,900, "
             "CCO-snapshot-2026-08-24 26. The old figure read "
             "'481,029/481,029 bodies across 7632/7682/7867/7878' -- and it "
             "is short in TWO directions: nine installs, and the instrument "
             "behind it (scratchpad/mnew/verify.py) walks loose .c3 only and "
             "opens no archives, which is why it records 66,897 on 7632 "
             "where the archive-inclusive census records 76,364. NOTE the "
             "reader is in Env_DX9/graphic.dll (2023), NOT the root "
             "graphicDX9.dll (2017), whose dispatcher predates the tag -- see "
             "docs/mnew_ccfl_re_2026-09-06.md",
    b"OMNI": "omni light; contents undecoded. TWO instances, on TWO installs: "
             "4274 and CCO-snapshot-2026-08-24, one file each. CORRECTED "
             "2026-09-07 -- it read '1 instance, on 4274', which was true of "
             "the eight installs the 2026-09-04 census walked and is the "
             "smallest possible version of this defect: a population of one "
             "that is really a population of two. Classic Conquer 2.0 keeps "
             "the other, which is worth knowing, because a tag surviving into "
             "a separate lineage is evidence about WHEN it was retired.",
}

# ---------------------------------------------------------------------------
# RIBB -- the field map, and what each row's evidence actually is
# ---------------------------------------------------------------------------
# UPGRADED 2026-09-07 from INFERRED to DECODED, field by field, against the
# loader.  The distribution table further down is unchanged and is still the
# thing the reading has to agree with; what is new is a second, independent
# source -- the code -- and the two agree everywhere they overlap except in one
# place, noted at dw25..30.
#
# A SCOPING CORRECTION LANDED WITH IT.  The enumeration below says "71
# instances ... on NO other install in the corpus" and then names the seven it
# checked: 4274, 5065, 5517, 6090, 6805, 7632, Zephyr.  **7867 and 7682 were
# not among them.**  Re-walked 2026-09-07: 7867 ships 69 more in 48 files (7682
# ships none), so the population is 140 bodies in 98 files across TWO installs.
# The claim was true of what it named and read as true of the corpus -- a
# narrow limit recorded against a broad thing, and the ratios below are quoted
# per install because a total hides one install moving while another does not.
#
# The decode is unaffected, and that is the point of having taken it from the
# code: it was derived against 7878's 71 and predicts 7867's 69 exactly, having
# been fitted to none of them.  Every check in the prediction table is 140/140.
#
#   binary       7878 Env_DX9/graphic.dll  sha256 1b4bc0fc0b7549a4...  5,000,728 b
#   dispatcher   RIBB arm at RVA 0x1EDC2, calls the loader at 0x1EE24
#   loader       RVA 0x151FB0
#   also         7632 Env_DX9/graphic.dll  sha256 32a6348bce59ef42...  2,008,992 b
#                RIBB arm 0x1162 / 0x11C0, loader 0xE2570 -- THE SAME FUNCTION:
#                856 of its 875 bytes are identical and all 19 differences are
#                relocated immediates in 8 runs of 1-3 bytes.
#   and          7867 Env_DX9/graphic.dll  sha256 acf7d86477ad3659...  4,703,384 b
#                RIBB arm 0x1EE24, loader 0x122D40 -- the same function again,
#                849 of 875 bytes, 26 differences in 15 runs of 1-3 bytes.
#   class        RTTI on the loader's vtable: `.?AUC3RibbonTrail@@` deriving
#                from `.?AUC3BillboardChain@@`.
#
# The loader reads all 124 bytes in 17 calls and the offsets it reads into are
# recovered by tracking ESP through the function; the per-field trace lives in
# tools/effects.py above `parse_ribb`, so it is beside the code that uses it.
# Below, each row of the old table gains a VERDICT.  Three verdicts are used:
#
#   DECODED    a named setter or a per-frame consumer in graphic.dll
#   DISCARDED  the loader reads it and overwrites it before anything sees it
#   INERT      it reaches a member that NOTHING in graphic.dll reads back
#
#   dw   off  verdict     what it is, and where that is established
#    0     0  DECODED     initial width      -> SetInitialWidth 0xE22D0
#    1     4  DECODED     width change /s    -> 0xE2310; SUBTRACTED at 0xE39C4
#    2     8  DISCARDED   forced to 2 at 0xE26A7 before vtbl+0x48
#    3    12  DISCARDED   forced to dw4 at 0xE26B3 before vtbl+0x84
#    4    16  DECODED     max chain elements -> vtbl+0x00 0xE2B30 -> obj+8
#    5    20  DISCARDED   forced to 10,000,000 at 0xE26BB before vtbl+0x88
#    6    24  DECODED     trail length       -> SetTrailLength 0xE2140, which
#                         divides it BY dw4 -- the pairing is in the code
#    7    28  DISCARDED   forced to dw6 at 0xE2698
#    8    32  DISCARDED   forced to dw6 at 0xE2694
#    9    36  DISCARDED   forced to the -1.0f at .rdata 0x17843C (0xE26A1)
#   10    40  DECODED     texture-coord scroll rate /s, component 0; NEGATED
#   11    44  DECODED       at load (0xE26C3), as is component 2 (0xE26CD).
#   12    48  DECODED       Consumed at 0xE41F1: rate * elapsed seconds,
#   13    52  DECODED       wrapped to +-1.1, pushed at the material handle.
#   14    56  DECODED  \   initial colour RGBA -> 0xE22A0 -> obj+0x174..0x180
#   15    60  DECODED   |  (default-initialised from a constant colour at
#   16    64  DECODED   |  .rdata 0x1BBD30 before the file value lands, which
#   17    68  DECODED  /   is how a field's role shows even when it is written)
#   18    72  DECODED  \   colour change PER SECOND -> 0xE22E0 -> obj+0x184..
#   19    76  DECODED   |  0x190, and SUBTRACTED from the element colour at
#   20    80  DECODED   |  0xE39F6..0xE3A18.  NOT an end colour: the update
#   21    84  DECODED  /   takes it away, it does not lerp toward it.
#   22    88  DECODED     boolean; obj+0x120 = (dw22 != 1), and if dw22 != 1
#                         obj+0x1C8 is cleared.  Read at 0xE1A16 / 0xE3167,
#                         both behind `obj+4 == 2 && obj+0x120`.  WHAT the
#                         gated step does is NOT settled.
#   23    92  INERT       stored to obj+0x118; a displacement scan of the whole
#                         class (0xE1000..0xE4400) finds no reader.
#   24    96  DECODED     FORMAT VERSION, and a hard gate: 0xE2670 compares it
#                         against 1 and the loader returns false otherwise,
#                         before allocating anything.
#   25   100  DECODED  \  the source segment's two endpoints, stored verbatim
#   26   104  DECODED   | to obj+0x128..0x13C.  The GROUPING into 2x3 is the
#   27   108  DECODED   | DATA's claim, not the code's -- see below.  Nothing
#   28   112  DECODED   | in graphic.dll reads these members back, so their
#   29   116  DECODED   | ROLE is inferred from `Shape.line`; that they are
#   30   120  DECODED  /  24 bytes at offset 100 is read from the loader.
#
# THE ONE CORRECTION to the table below.  It says dw28..30 is the "exact
# NEGATION" of dw25..27 on all 71 instances.  Re-measured: 55 of 71 are
# bit-exact (108 of 140 over both installs) and the rest differ in the last
# ulp (e.g. 0.16406892 vs -0.16406890).  The claim holds to 1e-6 relative on
# 140/140 and `effects.parse_ribb` is written to that tolerance.  A reader
# that tested bit equality would refuse nearly a quarter of the shipped files.
#
# THE PREDICTIONS THIS READING MAKES, AND A CONTROL.  A field map that is right
# should say something about the 140 that a wrong one does not.  Measured over
# BOTH installs; 71 of the 140 are the ones the reading was developed against
# and 69 are out of sample:
#
# 140/140  dw24 == 1                     the loader refuses anything else
# 140/140  dw2, dw3, dw7, dw8 == 0       the four the loader discards are the
#                                        four the exporter leaves at zero
# 140/140  dw5 == 10000                  invariant, because nothing reads it
# 140/140  dw9 == -1.0                   the exact constant the loader
#                                        substitutes for it
# 140/140  dw18..21 >= 0                 they are subtracted, not lerped to
# 140/140  dw22 in {0,1}                 it is a boolean
# 140/140  |dw28..30 + dw25..27| ~ 0     to 1e-6 relative
# 140/140  dw6 / dw4 in [3.25, 23.0]     element length, median 6.25
#   0/140  dw4 / dw6 in [1, 60]          THE CONTROL: the inverted pairing --
#                                        length and count swapped -- lands at
#                                        0.089..0.2 and fails every instance.
#                                        The element-length band is therefore
#                                        a discriminating result, not a
#                                        tautology about small numbers.
#
# One prediction was tried and DID NOT DISCRIMINATE, recorded so it is not
# tried again: width fade time (dw0/dw1) against alpha fade time (dw17/dw21).
# They agree on 0/67, but a shuffled-column control also scores 0/67, so the
# test has no power -- alpha fades over almost exactly 1.0 s on nearly every
# instance while width takes 0.75..15.3 s.  They are independent authoring
# knobs; the disagreement is not evidence against either reading.
#
# ---------------------------------------------------------------------------
# The original enumeration, unchanged
# ---------------------------------------------------------------------------
# FOUND 2026-09-04 by full enumeration: 71 instances in 50 files on 7878, and
# on NO other install in the corpus (4274, 5065, 5517, 6090, 6805, 7632,
# Zephyr).  <- SUPERSEDED 2026-09-07, and the parenthesis is why: 7867 and
# 7682 are not in that list.  7867 ships 69 more.  See the correction above. Always in the layout `RIBB+CCFL+RMOT` (x29) or that triple twice
# (x21) -- never with a PHY, never with a SHAP. RIBB : RMOT reads as the newer
# spelling of SHAP : SMOT, which is the trail/ribbon source line and its
# per-frame matrix (`tools/effects.shape_ribbon`).
#
# The body is 124 bytes in ALL 71, so it is a fixed struct, not a counted one.
# `effects.parse_shap` refuses every one of them, so it is NOT a SHAP.
#
# MEASURED -- the distinct-value count of each dword over all 71 instances:
#
#   dw  off  type  distinct  values
#    0    0  f32     17      100, 66, 88, 46, 160 ...
#    1    4  f32      8      40, 20, 15, 50, 100 ...
#    2    8  f32      1      0
#    3   12  f32      1      0
#    4   16  u32      6      80, 60, 40, 15, 10 (small integers; the f32 read
#                            is denormal, so this field is an INTEGER)
#    5   20  u32      1      10000, on every instance
#    6   24  f32     13      500, 260, 400, 900, 620 ...
#    7   28  f32      1      0
#    8   32  f32      1      0
#    9   36  f32      1      -1
#   10   40  f32      7      -1.5, -0.5, -1, -0.4, -0.6 ...
#   11   44  f32      1      0
#   12   48  f32      1      0
#   13   52  f32      1      0
#   14   56  f32      3   \
#   15   60  f32      5    |  every value is k/255 exactly (1, 0.588235=150/255,
#   16   64  f32      5    |  0.509804=130/255, 0.784314=200/255, 0.392157=100/255,
#   17   68  f32      3   /   0.00392157=1/255): a byte-quantised RGBA
#   18   72  f32      2   \
#   19   76  f32      3    |  a second byte-quantised RGBA
#   20   80  f32      1    |
#   21   84  f32      3   /
#   22   88  f32      1      0
#   23   92  f32      1      1
#   24   96  u32      1      1, on every instance
#   25  100  f32      3   \
#   26  104  f32      6    |  a 3-vector, and dw28..30 is its exact NEGATION on
#   27  108  f32      3   /   all 71 instances: the two endpoints of a segment
#   28  112  f32      3   \   symmetric about the origin -- the same thing
#   29  116  f32      6    |  `effects.Shape.line` calls "the two endpoints
#   30  120  f32      3   /   Shape_Draw actually consumes"
#
# THE 2026-09-04 INFERENCES, SETTLED 2026-09-07 -- kept here with their
# verdicts because a guess that turned out right and a guess that turned out
# wrong look identical once both are deleted:
#
#   "dw14..17 and dw18..21 are start/end colours"
#       HALF RIGHT.  dw14..17 is the initial colour.  dw18..21 is NOT an end
#       colour: the update at 0xE39F6 multiplies it by dt and SUBTRACTS it, so
#       it is a rate.  A renderer built on the lerp reading would have shown
#       the wrong colour on every ribbon whose fade is not exactly 1 second.
#   "dw4 is a segment count"
#       RIGHT.  0xE2B30 is setMaxChainElements and it lands in obj+8.
#   "dw5 is a lifetime in milliseconds"
#       WRONG, and unknowably so from the data: the loader overwrites dw5 with
#       10,000,000 at 0xE26BB before anything reads it.  Its invariant 10000
#       across all 140 is the exporter's habit, not a value the client uses.
#   "dw25..30 is the source segment"
#       RIGHT about the bytes -- 24 of them at offset 100, read in one call --
#       and still INFERRED about the meaning: no code in graphic.dll reads
#       obj+0x128..0x13C back.
#
# THE LOADER'S RVA (located 2026-09-06 while decoding CCFL, read 2026-09-07):
# the container dispatcher in `7632/Env_DX9/graphic.dll` tests 'R','I','B','B'
# at RVA 0x1162 and its arm at 0x11C0 calls **0x100E2570** with (slot, stream),
# the same shape as every other tag's arm; 7878's is 0x1EDC2 / 0x1EE24 ->
# 0x10151FB0.  Note it is Env_DX9/graphic.dll (2023-06-01 on 7632, 2025-11-06
# on 7878), NOT the root graphicDX9.dll (2017), whose dispatcher has no RIBB
# arm at all.


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


#: An empty slot in a `.pul` -- no tile painted there.  NOT a tile index.
#:
#: It lives beside the reader because it is a property of the format, and
#: because a consumer that does not know it reads 0xFFFF as a reference to
#: tile 65535 and then reports it as art it could not resolve.  MEASURED
#: across three installs before the constant was moved here: 27 of CCO's 136
#: maps, 36 of 5517's 192 and 72 of 7878's 470 place it, and **no `.ani` on
#: any of the three defines `Puzzle65535`** -- not once, which is what a
#: sentinel looks like and what a real index does not.  `tools/puzzle.py`
#: knew this and `tools/mapindex.py` did not.
PUL_EMPTY = 0xFFFF


@dataclass
class Pul:
    """Puzzle: the tiled background of a map.

    VERIFIED on all 381 .pul files in map/puzzle/: ``char[8] version``,
    ``char[256] ani path``, ``uint32 width``, ``uint32 height``, then
    height*width ``uint16`` indices into the named .ani file. Version
    ``PUZZLE2`` (350 files) appends two roll-speed fields; version
    ``PUZZLE`` (31 files) does not. Every file's length matched exactly.

    **The roll-speed pair is int32, not uint32.**  It was read ``<II`` here
    until 2026-08-26, which is not a cosmetic difference: a backdrop that
    scrolls the other way came back as ~4.29 billion.

    MEASURED over **2,724** ``.pul`` files carrying the field, across nine
    independent corpora (the resolved install plus
    ``Clients/{5017,5065,5165,5517,6090,6609,7878,Zephyr,CCO-snapshot}``) --
    141 of them non-zero:

    ==========  =========================  ==============================
    read as     component range            files with a component > 2**20
    ==========  =========================  ==============================
    ``<ii``     ``[-35, 60]``              **0**
    ``<II``     ``[0, 4294967286]``        **37**
    ==========  =========================  ==============================

    Signed, the whole corpus is ten distinct pairs and every component is a
    multiple of 5 -- ``(-35,35) (10,-10) (10,0) (10,10) (15,15) (20,0)
    (20,20) (40,-30) (40,40) (60,40)``.  Unsigned, 37 of the 141 sit eight
    orders of magnitude from the other 104, and the two axes of a plane that
    scrolls diagonally (``shipbg`` at ``(-35, 35)``) disagree by 4e9.  A
    one-family reading and a bimodal one is the whole test; nothing else in
    the format is signed, so this is the field's own evidence.

    On the resolved install the defect reaches **2 of the 35** backdrop-plane
    instances that carry a roll (``l-arena`` and ``p-arena``, both
    ``newplainbg-move.pul`` at ``(40, -30)``).  It is latent today only
    because no renderer consumes ``roll_speed`` yet -- see
    ``docs/ground_animation.md`` 10.
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
            # `<ii`, NOT `<II` -- a leftward/upward scroll is negative. See
            # the class docstring for the 2,724-file measurement.
            roll = struct.unpack_from("<ii", d, 272 + n * 2)
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
    source: str          # "loose" | "c3.wdf" | "data.wdf" | "<install>:<source>"
    real_path: Optional[Path]
    size: int

    #: **Where this file ACTUALLY came from, or None for "the root that was
    #: asked".**  A non-None value means the answer DID NOT COME OUT OF THE
    #: INSTALL, and there are exactly two ways that happens:
    #:
    #:   * a FALLBACK INSTALL -- `AssetRoot.resolve_garment`'s old-corpus
    #:     fallback served a 7878 lookup out of a pre-7878 install, because
    #:     7878 never shipped the geometry (see
    #:     `docs/garment_missing_art_2026-08-28.md`).  `origin_root` is that
    #:     install's root.
    #:   * an OVERLAY DIRECTORY -- `tools/garment_overlay_build.py`
    #:     materialised a donor's mesh into a directory at *7878's own path
    #:     shape*, which is precisely what makes it invisible: the file is
    #:     named the way 7878 would have named it, so `[overlay, N bytes]`
    #:     alone reads exactly like art the install shipped.  `origin_root` is
    #:     the overlay directory.  **This was NOT stamped before 2026-08-29**;
    #:     the overlay was opt-in, so "unmarked" and "not asked for" coincided.
    #:     Making the overlay a declared default (`AssetRoot.ASSET_PROFILES`)
    #:     separates them, and an unmarked overlay hit would then be a row
    #:     nothing downstream could tell from 7878's own art.
    #:
    #: WHY A FIELD AND NOT JUST A PRETTIER STRING.  A caller that receives
    #: 6090's mesh while asking about 7878 has to be able to TELL, and the
    #: two ways it could tell are a string it has to parse and a value it can
    #: test.  Both are provided -- `source` is prefixed with the install name
    #: so any log line or `str(loc)` shows the crossing to a human, and this
    #: field is what a program branches on.  `real_path` is NOT sufficient:
    #: a WDF-backed hit has `real_path=None` in both installs, so the only
    #: difference would have been invisible.
    origin_root: Optional[Path] = None

    @property
    def foreign(self) -> bool:
        """True iff this file came from an install other than the one asked."""
        return self.origin_root is not None

    def __str__(self) -> str:
        if self.origin_root is None:
            return f"{self.logical}  [{self.source}, {self.size} bytes]"
        return (f"{self.logical}  [{self.source}, {self.size} bytes,"
                f" FOREIGN <- {self.origin_root}]")


# ---------------------------------------------------------------------------
# asset profiles -- "which build is this, and where else may its art come from"
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssetProfile:
    """A DECLARED statement that one client build needs art it does not ship.

    Two axes, both already implemented and both previously opt-in:

    ``fallback_installs``
        Directory names under `coroot.clients_dir()`, in fallback order,
        handed to `AssetRoot(fallback_roots=)`.  Names, never paths --
        the clients tree is one box's fact and `tests/test_sanitization.py`
        refuses a literal for it.
    ``overlay_globs``
        Repo-relative globs under ``out/``, resolved for READING through
        `coroot.find_derived` so a fresh worktree inherits the primary
        checkout's build.  **A glob and not a list of directories, and that
        is the extension point**: `tools/garment_overlay_build.py` writes one
        directory per build, so an armet or weapon overlay materialised
        beside the armor one is picked up with NO CODE CHANGE HERE -- it
        only has to match the pattern.  Every matching directory is used,
        sorted, and each is stamped into the `Located`s it serves.

    WHY THIS LIVES IN `core/coassets.py` AND NOT IN `capture/coprofile/`.
    `capture/coprofile/builds.json` is a MEMORY profile: it answers "at what
    address does this PE hold the entity table", it is keyed on PE build
    hashes, it is read by the live-capture stack, and it declares no 7878
    entry at all.  This answers "where may this install's ART come from",
    it is keyed on the install, and it is read by the asset layer.  Two
    different questions with two different keys and two different consumers;
    filing them together would mean either that a capture profile implies an
    asset one or that adding a build to one silently half-answers the other.

    The second reason is mechanical and decides it on its own: `AssetRoot` is
    VENDORED into the Blender addon by `tools/build_addon.py`, and the addon
    cannot see this repository tree.  A profile in a data file outside `core/`
    would be present for `coviewer` and absent for the importer, which is the
    exact class of defect -- one instrument sees the asset, the other does not
    -- that `_art_by_garment`'s overlay bug already cost this project once.
    Declared here, it is vendored with the code that reads it.
    """

    #: The install this profile is FOR.  Matched against the install's names
    #: by `AssetRoot.profile_for`, never against a path.
    name: str
    fallback_installs: tuple = ()
    overlay_globs: tuple = ()
    #: One line, printed by `AssetRoot.profile_summary()`.  A profile that
    #: cannot say why it exists is a magic default.
    why: str = ""

    def overlay_dirs(self, find=None) -> list:
        """Every existing directory this profile's globs match, sorted.

        `find` is the resolver for the glob's PARENT and defaults to
        `coroot.find_derived`; a test passes its own so the extension point
        ("another overlay directory drops in") can be exercised without
        writing into anybody's `out/`.

        Returns `[]` when nothing matches, which is a real answer -- and one
        `AssetRoot` records in `profile_notes` rather than swallowing, because
        an overlay that was declared and is not on disk produces EXACTLY the
        run an overlay with no effect produces.
        """
        finder = find if find is not None else coroot.find_derived
        out: list = []
        for glob in self.overlay_globs:
            g = str(glob).replace("\\", "/")
            parent, _, pat = g.rpartition("/")
            base = finder(parent) if parent else None
            if base is None:
                continue
            for p in sorted(Path(base).glob(pat)):
                if p.is_dir() and p not in out:
                    out.append(p)
        return out


@dataclass(frozen=True)
class ProfileMatch:
    """WHY an install has, or has not, the profile it has.  Never a bare None.

    `AssetRoot.profile_for` answers `AssetProfile | None`, and None carried
    two completely different facts that nothing could tell apart:

      * **nobody asked** -- the install was never put to any test, so None is
        an unexercised code path, and
      * **asked and answered no** -- the install was measured and does not
        qualify, so None is a RESULT.

    Those were the same value until this existed, and the cost was measured:
    `7632`, `7682` and `7867` sat at a 13% appearance-resolution rate for as
    long as `ASSET_PROFILES` was keyed on the literal string ``"7878"``,
    silently, because a name that does not match and a name that was never
    compared both produced None.  `verdict` is the field that separates them
    and `note` is the line a human reads.

    A `ProfileMatch` is also the INCONCLUSIVE channel.  A rate that lands
    between the two bands is not rounded to a decision in either direction:
    it is `INCONCLUSIVE`, it names the rate it measured, and it applies no
    profile.  See `AssetRoot.qualifies_for`.
    """

    #: Matched on the install's own name -- the original, cheapest rule.
    NAMED = "NAMED"
    #: No name matched, and the SHIPPED-ART predicate said yes.
    QUALIFIES = "QUALIFIES"
    #: Measured, and the install satisfies its own declarations bare. It has
    #: no hurdle for a profile to clear.
    NO_HURDLE = "NO_HURDLE"
    #: Measured, the install does have the hurdle, and this profile's corpus
    #: does not clear it. A real negative, and a different one.
    NO_HELP = "NO_HELP"
    #: Measured and landed BETWEEN the bands. No profile is applied and the
    #: rate is named. This is the never-silent case.
    INCONCLUSIVE = "INCONCLUSIVE"
    #: This install is one of the profile's own `fallback_installs`. Hard
    #: refusal, taken before anything is measured -- see `qualifies_for`.
    REFUSED_DONOR = "REFUSED_DONOR"
    #: The predicate could not be run at all (no tables, no clients tree).
    #: NOT the same as a negative, and it says which.
    NOT_MEASURED = "NOT_MEASURED"

    profile: Optional[AssetProfile]
    verdict: str
    note: str
    #: Fraction of the pinned sample this install satisfies out of its OWN
    #: archives. None when nothing was measured.
    bare_rate: Optional[float] = None
    #: The same rows, with the candidate profile's corpus and overlay in
    #: force. None when the bare half already decided it and the expensive
    #: half was never run -- which is the point of reporting them separately.
    full_rate: Optional[float] = None
    sampled: int = 0
    population: int = 0
    #: sha256[:12] over the pinned sample's row keys. Two runs that disagree
    #: about a rate but agree on this were measuring the same rows; two that
    #: disagree here were not, and that is the first thing to check.
    #:
    #: **IT IDENTIFIES THE ROWS, NEVER THE INSTALL**, and the difference is
    #: not academic: MEASURED, 7632, 7682, 7867 and 7878 all produce
    #: `083c0a317b43` because their pinned 24 rows are identical -- while
    #: their FULL row sets are not. That is the property that makes their
    #: rates comparable (the same rows, four installs, 8-19% bare against
    #: 94-99% profiled) and it is the same trap C33 records for
    #: `len(rows) == 955`: a digest satisfied by four roots identifies none
    #: of them. Do not mix the install name in to make it "better".
    sample_sig: str = ""

    @property
    def matched(self) -> bool:
        return self.profile is not None

    def __str__(self) -> str:
        return "%s: %s" % (self.verdict, self.note)


#: Every declared asset profile, by `AssetProfile.name`.
#:
#: ONE ENTRY, and the entry is the finding.  7878 declares 6,951 appearance
#: rows across `armor.ini`, `armet.ini` and `weapon.ini` and resolves a tenth
#: of them out of its own archives; the art for most of the rest is on this
#: box already, in the pre-7878 corpus.  Both routes to it were built, both
#: measured LOST 0 / CHANGED 0, and both were left OFF, so the default
#: experience of opening the viewer on 7878 was a client that mostly cannot
#: show its own items.
ASSET_PROFILES: dict = {
    "7878": AssetProfile(
        name="7878",
        # The same corpus, in the same order, as
        # `AssetRoot.OLD_CORPUS_INSTALLS`.  Written out rather than aliased
        # because `AssetRoot` is defined below this table, and DRIFT IS
        # TESTED rather than prevented by construction:
        # `test_asset_profile_7878.Declaration.test_fallback_installs_match_
        # the_old_corpus` fails if the two ever disagree.  A silent alias
        # would also have hidden the reverse change -- someone narrowing the
        # profile's corpus and widening the class attribute.
        fallback_installs=("6090", "4274", "CCO-snapshot-2026-08-24", "Zephyr"),
        # `overlay-7878` today (armor, donor 7205).  `overlay-7878-armet` and
        # `overlay-7878-weapon` are being built as this lands and need no edit
        # here: the pattern already admits them.
        overlay_globs=("out/garment/overlay-7878*",),
        why=("7878 ships appearance tables for art it does not ship. "
             "Both routes to that art are measured LOST 0 / CHANGED 0 and "
             "every row they add is marked foreign."),
    ),
}


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

    ONE ROOT, ONE NAMESPACE -- WITH TWO LABELLED EXCEPTIONS, AND ON A
    DECLARED INSTALL THEY ARE NOW ON BY DEFAULT.  See `AssetProfile` and
    `ASSET_PROFILES`; `AssetRoot.bare(root)` is the one call that turns both
    off and asks what the install itself ships.

    **SUPERSEDED 2026-08-29, and the reasoning below is kept because half of
    it still holds.**  The two exceptions -- `resolve_garment`'s old-corpus
    fallback and the `overlay=` directory -- used to be off unless a caller
    passed `fallback_roots=` or `overlay=`.  What that bought was
    answerability, and what it cost was measured: on 7878 the default resolved
    958 of 6,951 declared appearance rows, because being opt-in meant being
    off in `coviewer`, `comod` and the Blender importer alike.  The four
    paragraphs below argue for opt-in; the third of them is the one that
    survives, and it survives as `bare()` rather than as a default.  What
    changed is not the argument but a defect it rested on: **the overlay path
    carried no provenance at all** -- an overlay hit came back as
    `[overlay, N bytes]` with `origin_root=None`, indistinguishable from the
    install's own art -- so "always-on but labelled" was not actually
    available for that axis until it was fixed here.  Now it is, and the
    choice between a labelled default and an unlabelled opt-in is a different
    choice from the one recorded below.

    Why it exists: 7878 declares 6,951 appearance rows whose mesh it never
    shipped, and 5,573 of them are on this box already, in the pre-7878
    installs, under `c3/mesh/00<mesh6>.c3` where 7878 would write
    `c3/body/<bodytype><garment6>.c3`. `core/c3phy.py` parses those files
    today, unconverted. Measured and written up in
    `docs/garment_missing_art_2026-08-28.md`.

    Why it WAS opt-in rather than always-on-but-labelled -- the choice as it
    was decided on 2026-08-28, with what became of each half:

    * `resolve_asset` -> `resolve_garment` is reached by `tools/coviewer.py`,
      `tools/comod.py` and the Blender importer. Turning it on for all of them
      changes what those tools export and stage, and a mod built from 6090
      geometry under a 7878 id is a *different artefact*, not a better lookup.
      A label helps a human reading a log; it does not help a program that
      just takes `loc.logical`.
      -- STILL TRUE, AND STILL THE THING TO WATCH. It is why `Located.foreign`
      and `foreign_hits`/`overlay_hits` are the mechanism and the log line is
      not, and why `comod`'s staging path is the one place that should learn
      to refuse a foreign `Located` without an explicit flag. What it does not
      justify any more is leaving the *reader* off: the cost of opt-in fell
      entirely on the honest consumers, and the one consumer that must not
      cross an install boundary silently is one place to guard, not three.
    * Always-on would also spend the probe on every install: the fallback only
      fires where the answer was going to be None, but on the WDF corpora each
      probe is a real filesystem stat, which is the exact cost
      `MESH_DIRS_EXTRA` above is written around.
      -- ANSWERED BY THE PROFILE BEING DECLARED PER INSTALL. It is on for
      exactly one build; `profile_for` returns None for every other root on
      this box, so no other install pays a probe it did not pay before.
    * Opt-in keeps the default answer for "does 7878 ship this?" honest. That
      question has a true answer -- no -- and a resolver that quietly says yes
      makes the survey above unrepeatable.
      -- THE ONE THAT DECIDES THE SHAPE, and it is preserved as
      `AssetRoot.bare(root)`: one call, no profile, no overlay, no fallback,
      whatever the environment says. Answerability is a property of *being
      able to ask*, not of the default; what would destroy it is a resolver
      that says yes with nothing on the answer to say where it came from,
      which is why the overlay's missing `origin_root` had to be fixed in the
      same change that made it a default.

    It is inspectable as well as declared: every fallback crossing lands in
    `foreign_hits`, every overlay hit in `overlay_hits`, every unusable
    fallback root in `fallback_refusals`, every profile decision in
    `profile_notes`/`profile_summary()`, and each returned `Located` carries
    `origin_root` plus an install-prefixed `source`.
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

    #: The pre-7878 corpus, by install DIRECTORY NAME under
    #: `coroot.clients_dir()` -- never an absolute path, because the clients
    #: tree is one box's fact and `tests/test_sanitization.py` refuses a
    #: literal for it.
    #:
    #: MEASURED 2026-08-28 (`tools/garment_missing_survey.py`, written up in
    #: `docs/garment_missing_art_2026-08-28.md`): `5017 5065 5165 5517 6090
    #: 6609` have BYTE-IDENTICAL archive hash sets, 25,013 entries each, so
    #: they are ONE corpus and 6090 stands for all six. The distinct corpora
    #: on this box are 6090, 4274, the CCO snapshot and Zephyr; they are
    #: listed here in that order and the fallback tries them in it.
    #:
    #: This is a DEFAULT for `old_corpus_roots()`, not something `__init__`
    #: reads. Nothing here is consulted unless a caller asks for it.
    OLD_CORPUS_INSTALLS = ("6090", "4274", "CCO-snapshot-2026-08-24", "Zephyr")

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

    #: The module-level `ASSET_PROFILES` table, bound here so
    #: `AssetRoot.ASSET_PROFILES` is the one name a caller or a test needs.
    #: BOUND, NOT COPIED -- in a class body the right-hand side still resolves
    #: to the module global, so there is one table and patching it in a test
    #: is visible through both names.
    ASSET_PROFILES = ASSET_PROFILES

    #: Environment escape hatch for the declared default, read once per
    #: construction.  `off`/`0`/`no`/`none` disables profiles process-wide --
    #: which is what a SURVEY wants, because a survey's whole job is to ask
    #: the bare question over installs it does not enumerate itself.  Any
    #: other value names a profile to force.  `profile=` on the constructor
    #: still wins over this, in both directions, so a test can be explicit.
    PROFILE_ENV = "CO_ASSET_PROFILE"

    @staticmethod
    def install_names(root) -> set:
        """Every name this box has for `root`, lowercased.

        THE PROFILE IS KEYED ON A NAME, NOT A FINGERPRINT, and that is a
        decision with a cost either way:

        * A fingerprint (`coroot.base_fingerprint`) is exact -- it would apply
          the profile to precisely the artefact it was measured against and to
          nothing else.  It is also SILENT on the next repack: a 7878 with one
          patched `ini/` file gets no profile, no message, and a viewer that
          resolves a tenth of its rows.  Silence is the failure this profile
          exists to remove, so it cannot be the selection rule.
        * A name is what a human already wrote down, in one of two places, and
          both are read here: the install DIRECTORY NAME (which is how
          `OLD_CORPUS_INSTALLS` has always identified a corpus) and the
          DECLARED KIND from `coroot.declared_kinds()` (``patch7878``), which
          survives an install being renamed or living outside the clients
          tree.  Neither is trusted to exist; the union is used.

        NOT `coroot.base_id`, and the reason is cost, measured: `base_id` is
        ``<kind>-<fingerprint>`` and the fingerprint half is a sha256 over
        every file in ``ini/`` -- **314 ms on 7878 on this box**, which this
        would then spend on EVERY `AssetRoot(...)`, on every install, to
        decide something the kind half already answers.  `declared_kinds()`
        is the same declaration without the hash: 0.65 ms, and it is the map
        `declare_kind` writes.  The fingerprint would also have been the wrong
        input anyway -- see the note above on why a profile is not keyed on
        one.
        """
        names = set()
        try:
            names.add(Path(root).name.lower())
        except (OSError, TypeError):            # pragma: no cover
            pass
        try:
            key = str(Path(root).resolve()).lower()
            for decl, kind in coroot.declared_kinds().items():
                if str(Path(decl)).lower() != key:
                    continue
                kind = str(kind).lower()
                names.add(kind)
                # `patch7878` and `7878` are the same statement written two
                # ways -- the settings map spells it one way and the clients
                # tree the other -- so a profile should not have to declare
                # itself twice to be found by both.
                if kind.startswith("patch"):
                    names.add(kind[len("patch"):])
        except Exception:                       # pragma: no cover
            pass
        return names

    @classmethod
    def profile_for(cls, root) -> Optional[AssetProfile]:
        """The declared `AssetProfile` for `root`, or None.

        None is the answer for every install that is not declared, which is
        every install but one -- so nothing about any other client changes by
        this existing.
        """
        names = cls.install_names(root)
        for prof in cls.ASSET_PROFILES.values():
            if prof.name.lower() in names:
                return prof
        return None

    # -- the shipped-art predicate -----------------------------------------
    #
    # WHY A PREDICATE OVER SHIPPED ART AND NOT A SECOND NAME, A FINGERPRINT,
    # A `version.dat`, OR THE TABLE CONTENT.  Every one of the cheaper axes
    # was measured on this box's readable installs -- **34**, re-derived from
    # `coroot.clients_dir()` on 2026-09-07; this line read 33 -- and each fails:
    #
    #   DIRECTORY NAME    is the defect this replaces.  `ASSET_PROFILES` had
    #                     one key, the literal `"7878"`, so `7632`, `7682`
    #                     and `7867` -- which have the same hurdle and which
    #                     the same corpus clears -- got no profile, no
    #                     message, and a 13% appearance-resolution rate.
    #   `version.dat`     does not separate them: 7632 and 7682 both declare
    #                     7622 (`tests/test_client_binaries.py`, and C33 in
    #                     `tests/test_garment_overlay.py` records the same
    #                     disagreement).
    #   TABLE CONTENT     collides 29 WAYS.  MEASURED 2026-09-04: 29 of the
    #                     34 installs ship `ini/armor.ini` byte for
    #                     byte -- md5 9c12b784059f44380ec9ccb9611fb187,
    #                     108,283 bytes -- every install from 5165 onward,
    #                     which is C33's freeze generalised.  [Read "33
    #                     readable installs" until 2026-09-07.  RE-MEASURED
    #                     over every install `coroot.clients_dir()` holds and
    #                     the SUBSTANCE SURVIVED INTACT: the 29 are exactly
    #                     5165..7878, the md5 and the 108,283 are right, and
    #                     the other five are 4274 (102,522 B), 5017 (288,123),
    #                     5065 (98,542), CCO-snapshot-2026-08-24 (718,096) and
    #                     **Zephyr, which ships no `ini/armor.ini` at all** --
    #                     an absence, not a difference, and the two are not
    #                     the same answer.  Only the denominator moved, which
    #                     is the cheapest and least visible form of the defect
    #                     in `docs/claim_enumeration_audit_2026-09-07.md`: the
    #                     number beside it is correct, so nothing about the
    #                     sentence reads wrong.]  Keying on
    #                     content would hand the 7878 profile to all 29,
    #                     **including 6090, which is in that profile's own
    #                     `fallback_installs`**: the profile would be applied
    #                     to its own donor.  The class it must describe has
    #                     four members.  Hashing the whole appearance-table
    #                     SET rather than `armor.ini` alone does not rescue
    #                     it -- it gives a 27-member class that does not even
    #                     CONTAIN 7878, because 7878 ships 7 tables where
    #                     7632 ships 9.
    #
    # SHIPPED ART IS THE ONLY AXIS THAT SEPARATES THE CLASSES, and it does so
    # by 80 points.  MEASURED, 400 pinned rows, seeded 20260904, fraction of
    # declared appearance rows whose every mesh resolves:
    #
    #     install   BARE     with the 7878 corpus     class
    #     5017      100.00%  100.00%                  ships its art
    #     5065       99.50%  100.00%                  ships its art
    #     6090       99.50%   99.50%                  ships its art (a DONOR)
    #     7205       99.50%   99.50%                  ships its art
    #     7632       13.00%   94.00%                  HURDLE, corpus clears it
    #     7682       13.00%   94.00%                  HURDLE, corpus clears it
    #     7867       13.25%   94.00%                  HURDLE, corpus clears it
    #     7878       19.00%   99.75%                  HURDLE, corpus clears it
    #
    # The two classes are 19.00% and 99.50% at their nearest points.  Every
    # threshold below is CHOSEN from that gap and each says what it was
    # chosen from; none of them is a fitted number.

    #: How many declared rows the predicate measures.  CHOSEN: the two classes
    #: are 80 points apart, so the sample only has to be big enough that one
    #: row cannot move a verdict across a 31-point margin -- at 24 rows one
    #: row is 4.2 points.  It is also the cost: this runs inside `__init__`
    #: for every install with no name match, and a row costs up to 39 stats.
    HURDLE_SAMPLE = 24
    #: Pinned, and PINNED IS THE REQUIREMENT: a predicate whose answer depends
    #: on sampling order is not a key.  Changing this number re-keys every
    #: install and is a deliberate act -- `sample_sig` on the `ProfileMatch`
    #: is what makes such a change visible in a run that reports it.
    HURDLE_SEED = 20260904
    #: At or below this bare rate the install visibly cannot satisfy its own
    #: declarations.  CHOSEN: 31 points above the highest measured hurdle
    #: install (7878, 19.00%) and 49 below the lowest measured art-shipping
    #: one (5065/6090/7205, 99.50%).
    HURDLE_BARE_MAX = 0.50
    #: At or above this bare rate the install ships what it declares and has
    #: no hurdle.  CHOSEN: 9.5 points below that same 99.50%.
    HURDLE_BARE_MIN = 0.90
    #: At or above this rate WITH the profile's corpus, the corpus clears the
    #: hurdle.  CHOSEN: 14 points below the lowest measured clearing rate
    #: (7632/7682/7867, 94.00%).
    HURDLE_FULL_MIN = 0.80
    #: At or below this, the corpus does not clear it.  Between the two, the
    #: answer is INCONCLUSIVE and is recorded as such.
    HURDLE_FULL_MAX = 0.50

    #: `{(resolved root, profile name): ProfileMatch}`, process-lifetime.
    #: The predicate opens the root bare AND opens the profile's whole
    #: fallback corpus, so it is far too expensive to repeat per `AssetRoot`.
    #: Tests that mutate an install in place must `clear()` this -- it is
    #: keyed on the path, not on the content, precisely because hashing the
    #: content is the axis this predicate exists to reject.
    _hurdle_memo: dict = {}

    #: `(root, profile name)` pairs whose predicate is CURRENTLY on the stack.
    #: Not a cache -- a cycle breaker; see `qualifies_for`. Empty between
    #: calls, and an entry surviving a call would mean an exception escaped
    #: the `finally`, so a test may assert it is empty.
    _hurdle_active: set = set()

    @classmethod
    def hurdle_sample(cls, tables: dict) -> tuple:
        """`(rows, population, sample_sig)` -- the PINNED sample, deterministic.

        `rows` is `[(table, ident, (mesh, ...)), ...]`, sorted before it is
        sampled and sampled with `HURDLE_SEED`, so the same install produces
        the same rows on every run, in every process, in any order of calls.
        `sample_sig` is a digest of the rows chosen: two runs that disagree
        about a rate while agreeing on this were measuring the same rows.

        Rows that declare no mesh are dropped BEFORE sampling rather than
        after, so `HURDLE_SAMPLE` is a count of rows that can actually answer
        the question and not a count that silently shrinks.
        """
        rows: list = []
        for tname in sorted(tables):
            for app in tables[tname]:
                meshes = tuple(p.mesh for p in app.parts
                               if p.mesh and p.mesh != "0")
                if meshes:
                    rows.append((tname, app.ident, meshes))
        rows.sort()
        population = len(rows)
        if population > cls.HURDLE_SAMPLE:
            rows = random.Random(cls.HURDLE_SEED).sample(rows,
                                                         cls.HURDLE_SAMPLE)
            rows.sort()
        sig = hashlib.sha256(
            "\n".join("%s|%s|%s" % (t, i, ",".join(m)) for t, i, m in rows)
            .encode("utf-8")).hexdigest()[:12]
        return rows, population, sig

    @classmethod
    def _hurdle_rate(cls, ar: "AssetRoot", rows) -> float:
        ok = 0
        for _t, _i, meshes in rows:
            try:
                if all(ar.resolve_asset(m, "mesh") is not None for m in meshes):
                    ok += 1
            except Exception:                       # pragma: no cover
                pass
        return ok / len(rows) if rows else 0.0

    @classmethod
    def qualifies_for(cls, root, prof: AssetProfile) -> ProfileMatch:
        """Does `prof` describe `root`'s hurdle?  A verdict, never a bare None.

        The predicate, in the order the three constraints require:

          0. **A profile MUST NOT match an install named in its own
             `fallback_installs`.**  Taken FIRST, before anything is
             measured, and it is a `REFUSED_DONOR` that says why rather than
             a quiet filter.  Forced by 6090: it is 7878's donor, it shares
             `armor.ini` with 7878 byte for byte, and any content-shaped rule
             hands it the profile whose corpus it *is*.  A donor scored
             against its own corpus is a resolver marking its own homework.
          1. **The hurdle half.**  What does the install satisfy out of its
             OWN archives, over the pinned sample?  Above `HURDLE_BARE_MIN`
             there is no hurdle and the answer is `NO_HURDLE` -- and the
             expensive half is never run, which is what keeps this affordable
             on the installs that make up most of the corpus.
          2. **The corpus half.**  The SAME rows, with `prof` forced on.  At
             or above `HURDLE_FULL_MIN` the corpus clears the hurdle and the
             profile applies.  At or below `HURDLE_FULL_MAX` it does not, and
             that is `NO_HELP` -- a different negative from `NO_HURDLE`, and
             worth telling apart: one says "this install is fine", the other
             says "this install is broken and I cannot fix it".

        Anything between the bands on either axis is `INCONCLUSIVE`, names
        the rate it measured, and applies no profile.  **A near miss is a
        recorded result, not a None** -- that is constraint (b), and it is
        the whole reason this returns a `ProfileMatch`.
        """
        key = (str(Path(root).resolve()).lower(), prof.name)
        hit = cls._hurdle_memo.get(key)
        if hit is not None:
            return hit
        # RE-ENTRANCY.  The corpus half opens the profile's fallback installs,
        # and opening an install runs this predicate on IT.  With one declared
        # profile that terminates by construction -- every member of the
        # corpus is one of the profile's own `fallback_installs`, so step 0
        # refuses it before anything opens -- but the whole point of this
        # change is that a profile now describes a CLASS, and a second profile
        # whose corpus contains an install qualifying for the first closes the
        # loop.  A stack overflow inside `AssetRoot.__init__` is a bad way to
        # find that out, and a verdict that NAMES the cycle is the same
        # never-silent rule this file applies everywhere else.
        #
        # HOW FAR THIS IS ACTUALLY ESTABLISHED -- less than the paragraph
        # above implies, so it is written down.  MEASURED 2026-09-04 against a
        # constructed crossed pair (cyc1's corpus names 7682, cyc2's names
        # 7632, neither names the install it is asked about): the branch below
        # IS REACHED, exactly once.  But with the guard disabled that same
        # case STILL COMPLETES -- `_fallbacks()` is lazy, so the chain bottoms
        # out well before the recursion limit.  This is therefore a
        # TERMINATION GUARANTEE and a named verdict; it is NOT the fix for an
        # unbounded recursion anybody has produced, and no case on this box
        # recurses without it.  Do not cite it as one.
        if key in cls._hurdle_active:
            return ProfileMatch(
                None, ProfileMatch.NOT_MEASURED,
                "profile %r NOT MEASURED for %s: measuring it requires opening "
                "this same root through a profile corpus that is already being "
                "measured (%s). A cycle, not a negative -- break it by naming "
                "the shared install in one of the two profiles' "
                "fallback_installs, which step 0 then refuses outright."
                % (prof.name, Path(root).name,
                   " -> ".join("%s/%s" % (Path(r).name, n)
                               for r, n in cls._hurdle_active)))
        cls._hurdle_active.add(key)
        try:
            # WHO OWNS THE ARCHIVE HANDLES.  The measurement below opens up to
            # two whole installs -- `bare(root)`, and `root` again with `prof`
            # forced on, which opens the profile's fallback installs too -- and
            # it reaches them down EIGHT different `return` paths.  Every one of
            # those used to drop the `AssetRoot` without closing it, so a
            # caller who did nothing wrong leaked two `.wdf` handles per
            # declared profile per install.  `_hurdle_memo` keeps the VERDICT,
            # which is floats and strings; it never needs the roots, so the
            # stack closes them here and the lifetime is this call.
            #
            # SAFE TO CLOSE, and this is the part worth checking before adding
            # a third root: nothing the measurement returns reads from one
            # afterwards.  `hurdle_sample` copies rows out as plain
            # strings, `_hurdle_rate` returns a float, and the two process-wide
            # caches the resolve path fills (`_PART_INI_CACHE`,
            # `_ITEM_TABLE_CACHE`) hold a pickled snapshot and plain rows
            # respectively -- not an mmap-backed object between them.
            with contextlib.ExitStack() as owned:
                m = cls._qualifies_for_uncached(root, prof, owned)
        finally:
            cls._hurdle_active.discard(key)
        cls._hurdle_memo[key] = m
        return m

    @classmethod
    def _qualifies_for_uncached(cls, root, prof,
                               owned: contextlib.ExitStack) -> ProfileMatch:
        # -- 0. the donor refusal, before anything is measured --------------
        names = cls.install_names(root)
        donors = {str(d).lower() for d in prof.fallback_installs}
        both = sorted(names & donors)
        if both:
            return ProfileMatch(
                None, ProfileMatch.REFUSED_DONOR,
                "profile %r REFUSED for %s: this install is named in the "
                "profile's own fallback_installs (%s), so the profile would "
                "be applied to its own donor. Nothing was measured -- the "
                "refusal is structural and does not depend on a rate."
                % (prof.name, Path(root).name, ", ".join(both)))
        try:
            bare = owned.enter_context(cls.bare(root))
            tables = bare.part_tables()
        except Exception as e:                      # noqa: BLE001
            return ProfileMatch(
                None, ProfileMatch.NOT_MEASURED,
                "profile %r NOT MEASURED for %s: its appearance tables would "
                "not open (%s: %s). This is not a negative."
                % (prof.name, Path(root).name, type(e).__name__, e))
        rows, population, sig = cls.hurdle_sample(tables)
        if not rows:
            return ProfileMatch(
                None, ProfileMatch.NOT_MEASURED,
                "profile %r NOT MEASURED for %s: it declares no appearance "
                "row naming a mesh, so there is no hurdle to measure. This is "
                "not a negative." % (prof.name, Path(root).name),
                population=population, sample_sig=sig)
        # -- 1. the hurdle half ---------------------------------------------
        bare_rate = cls._hurdle_rate(bare, rows)
        common = dict(bare_rate=bare_rate, sampled=len(rows),
                      population=population, sample_sig=sig)
        if bare_rate >= cls.HURDLE_BARE_MIN:
            return ProfileMatch(
                None, ProfileMatch.NO_HURDLE,
                "profile %r does not apply to %s: it satisfies %.2f%% of %d "
                "pinned declared rows out of its OWN archives (>= %.0f%%), so "
                "it has no hurdle for a profile to clear. MEASURED; the "
                "profile's corpus was never opened."
                % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
                   100 * cls.HURDLE_BARE_MIN), **common)
        if bare_rate > cls.HURDLE_BARE_MAX:
            return ProfileMatch(
                None, ProfileMatch.INCONCLUSIVE,
                "profile %r INCONCLUSIVE for %s: it satisfies %.2f%% of %d "
                "pinned declared rows bare, which is BETWEEN the bands "
                "(<= %.0f%% is a hurdle, >= %.0f%% is none). No profile is "
                "applied and no rate is rounded to a decision. Re-measure "
                "with `py -3 tools/assetprofile.py --install %s`."
                % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
                   100 * cls.HURDLE_BARE_MAX, 100 * cls.HURDLE_BARE_MIN,
                   Path(root).name), **common)
        # -- 2. the corpus half ---------------------------------------------
        try:
            full = owned.enter_context(cls(root, profile=prof))
        except Exception as e:                      # noqa: BLE001
            return ProfileMatch(
                None, ProfileMatch.NOT_MEASURED,
                "profile %r NOT MEASURED for %s: it satisfies %.2f%% of %d "
                "pinned rows bare, which IS the hurdle, but the profile's own "
                "corpus would not open (%s: %s). This is not a negative."
                % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
                   type(e).__name__, e), **common)
        full_rate = cls._hurdle_rate(full, rows)
        common["full_rate"] = full_rate
        if full_rate >= cls.HURDLE_FULL_MIN:
            return ProfileMatch(
                prof, ProfileMatch.QUALIFIES,
                "profile %r APPLIES to %s: %.2f%% of %d pinned declared rows "
                "resolve out of its own archives and %.2f%% resolve with this "
                "profile's corpus (>= %.0f%%). MEASURED on shipped art, which "
                "is the only axis that separates this class -- the appearance "
                "tables collide 29 ways and the directory name is the defect "
                "this replaces."
                % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
                   100 * full_rate, 100 * cls.HURDLE_FULL_MIN), **common)
        if full_rate <= cls.HURDLE_FULL_MAX:
            return ProfileMatch(
                None, ProfileMatch.NO_HELP,
                "profile %r does not apply to %s: it satisfies only %.2f%% of "
                "%d pinned declared rows bare -- it DOES have the hurdle -- "
                "but this profile's corpus only takes it to %.2f%% "
                "(<= %.0f%%). A real gap that this corpus does not close."
                % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
                   100 * full_rate, 100 * cls.HURDLE_FULL_MAX), **common)
        return ProfileMatch(
            None, ProfileMatch.INCONCLUSIVE,
            "profile %r INCONCLUSIVE for %s: %.2f%% of %d pinned declared "
            "rows bare and %.2f%% with this profile's corpus, which is "
            "BETWEEN the bands (>= %.0f%% applies, <= %.0f%% does not). No "
            "profile is applied and no rate is rounded to a decision."
            % (prof.name, Path(root).name, 100 * bare_rate, len(rows),
               100 * full_rate, 100 * cls.HURDLE_FULL_MIN,
               100 * cls.HURDLE_FULL_MAX), **common)

    @classmethod
    def profile_match(cls, root) -> ProfileMatch:
        """The full answer for `root`: which profile applies, and WHY NOT.

        Name first -- it is free, it is what a human already wrote down, and
        it must keep deciding the installs it already decides, so that adding
        this predicate cannot move an answer the old rule already gave.  Only
        when NO name matches is the shipped-art predicate run, over every
        declared profile in turn.

        The returned `ProfileMatch` is the SINGLE most informative verdict:
        a `QUALIFIES` if one exists, else the first real measurement, else
        `NOT_MEASURED`.  Every profile's verdict is reachable through
        `all_matches`; this is the one a caller acts on.
        """
        all_m = cls.all_matches(root)
        for m in all_m:
            if m.matched:
                return m
        rank = {ProfileMatch.NO_HELP: 0, ProfileMatch.INCONCLUSIVE: 1,
                ProfileMatch.NO_HURDLE: 2, ProfileMatch.REFUSED_DONOR: 3,
                ProfileMatch.NOT_MEASURED: 4}
        if not all_m:
            return ProfileMatch(
                None, ProfileMatch.NOT_MEASURED,
                "no asset profile is declared at all, so %s was not measured "
                "against anything" % Path(root).name)
        return sorted(all_m, key=lambda m: rank.get(m.verdict, 9))[0]

    @classmethod
    def all_matches(cls, root) -> list:
        """One `ProfileMatch` per declared profile, in table order."""
        names = cls.install_names(root)
        out: list = []
        for prof in cls.ASSET_PROFILES.values():
            if prof.name.lower() in names:
                out.append(ProfileMatch(
                    prof, ProfileMatch.NAMED,
                    "profile %r matched %s BY NAME (names: %s) -- %s"
                    % (prof.name, Path(root).name, sorted(names), prof.why)))
            else:
                out.append(cls.qualifies_for(root, prof))
        return out

    @classmethod
    def bare(cls, root: Path | str | None = None, **kw) -> "AssetRoot":
        """**What does this install ITSELF ship?**  The bare question, one call.

        This is the answerability guarantee that made the fallback and the
        overlay opt-in in the first place, kept as a call instead of as a
        default.  A root opened this way has no profile, no overlay and no
        fallback whatever `ASSET_PROFILES` or `CO_ASSET_PROFILE` say, so every
        `Located` it returns came out of `root` and `Located.foreign` is False
        for all of them.

        Use it for any survey, census or provenance claim about a build.  The
        thing it protects against is a resolver that quietly says yes: 7878
        does NOT ship most of its declared appearance art, that has a true
        answer, and a default that made the true answer unaskable would be a
        regression however much it raised the numbers.

            bare  = AssetRoot.bare(root)          # what 7878 ships
            full  = AssetRoot(root)               # what the tooling can show
            gained = [i for i in ids
                      if full.resolve_asset(i, "mesh")
                      and not bare.resolve_asset(i, "mesh")]
        """
        kw.pop("profile", None)
        kw.pop("overlay", None)
        kw.pop("fallback_roots", None)
        return cls(root, profile=False, **kw)

    def __init__(self, root: Path | str | None = None, overlay=None,
                 fallback_roots=None, *, profile=None):
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
        # -- the declared profile -------------------------------------------
        #: The `AssetProfile` in force, or None.  A program branches on this;
        #: `profile_summary()` is the line a human reads.
        self.profile: Optional[AssetProfile] = None
        #: How the profile was decided, and why each axis is or is not on.
        #: **An empty profile is not a clean profile**: a declared profile
        #: whose overlay globs match nothing produces exactly the run no
        #: profile produces, so the miss is recorded here rather than
        #: swallowed.
        self.profile_notes: list = []
        #: The `ProfileMatch` behind `self.profile` -- WHY this root has the
        #: profile it has, and, when it has none, whether that None was
        #: measured or merely never asked.  Always set; a caller that only
        #: wants the boolean still has `self.profile`.
        self.match = ProfileMatch(
            None, ProfileMatch.NOT_MEASURED,
            "profile selection has not run yet")
        prof, how = self._pick_profile(profile)
        self.profile = prof
        self.profile_notes.append(how)

        # -- overlays: EXPLICIT WINS, PER AXIS -------------------------------
        # A caller that passed `overlay=` is measuring that overlay and
        # nothing else; silently adding the profile's would make their
        # ablation ("remove the overlay, the rows go back to None") false
        # without touching their code.  Same for `fallback_roots=`.
        if overlay is not None:
            overlays = [Path(overlay)] if isinstance(overlay, (str, Path)) \
                else [Path(p) for p in overlay]
            self.profile_notes.append(
                "overlays: %d from the caller (explicit beats profile)"
                % len(overlays))
        elif prof is not None:
            overlays = prof.overlay_dirs()
            if overlays:
                self.profile_notes.append(
                    "overlays: %d matched %r -> %s"
                    % (len(overlays), list(prof.overlay_globs),
                       [p.name for p in overlays]))
            else:
                self.profile_notes.append(
                    "overlays: NONE -- profile %r declares %r and nothing on "
                    "disk matches. Build one with "
                    "`py -3 tools/garment_overlay_build.py`; until then this "
                    "root resolves exactly as it would with no profile."
                    % (prof.name, list(prof.overlay_globs)))
        else:
            overlays = []
        #: Every overlay directory, in precedence order, ahead of the loose
        #: tree and the archives.  A LIST and not a single path: one build of
        #: `tools/garment_overlay_build.py` writes one directory, and armor,
        #: armet and weapon are three builds.  `self.overlay` stays as the
        #: first of them for callers that predate this.
        self.overlays: list = [Path(p) for p in overlays]
        self._archives: dict = {}
        #: Built on first use by `_art_by_garment`, never in __init__ -- it
        #: walks every archive entry and every loose file under c3/, which is
        #: ~150,000 names on 7878, and most callers never resolve a garment
        #: at all. None means "not built yet", not "empty".
        self._garment_index: Optional[dict] = None
        #: False = not built; None = this root cannot be enumerated
        #: (a hash-indexed .wdf); otherwise the set of c3/ names.
        self._c3_name_set = False
        # A PARTIAL OPEN CLOSES WHAT IT GOT.  `reader(p)` is deliberately
        # allowed to propagate (see below), and when it does on the SECOND
        # archive the first one is already open -- with the exception leaving
        # the caller no `AssetRoot` to `close()`, so those handles were
        # unreachable for the rest of the process.  CCO opens two archives, so
        # this is one bad `data.wdf` away from being the common case.
        try:
            for p in self._discover_archives(self.root):
                reader = self._reader_for(p)
                if reader is None:                  # pragma: no cover
                    continue
                # An archive that will not open is not silently skipped: a
                # client missing half its art because one container failed
                # reads exactly like a client that never shipped it.
                self._archives[p.name] = reader(p)
        except Exception:
            for _a in self._archives.values():
                try:
                    _a.close()
                except Exception:                   # pragma: no cover
                    pass
            self._archives.clear()
            raise
        self._names: Optional[dict[int, str]] = None
        #: Lazy `{appearance id: declared mesh id}` from the part tables.
        #: `part_tables()` re-parses on every call, so it is read once.
        self._declared: Optional[dict[str, str]] = None
        #: Lazy `ini/c3.wdb`. False = not looked for yet; None = this install
        #: ships none, or the one it ships would not parse.
        self._resource_db = False
        #: Lazy `ini/3dobj.ini` (or its compiled twin). False = not looked
        #: for yet; {} = this install declares nothing we can read. See
        #: `object_db` -- five installs in the corpus have ONLY this table.
        self._object_db = False
        self._texture_db = False

        # -- old-corpus fallback: OPT-IN, and empty unless a caller asks -----
        #: What the caller asked for: paths, or already-open `AssetRoot`s.
        #: Kept unopened until the first fallback lookup -- opening four
        #: installs costs four archive-set discoveries, and most callers
        #: never resolve a garment at all.
        if fallback_roots is not None:
            self._fallback_spec = list(fallback_roots)
            self.profile_notes.append(
                "fallback: %d root(s) from the caller (explicit beats profile)"
                % len(self._fallback_spec))
        elif self.profile is not None:
            self._fallback_spec = list(self.old_corpus_roots(
                names=self.profile.fallback_installs))
            if self._fallback_spec:
                self.profile_notes.append(
                    "fallback: %s"
                    % [Path(p).name for p in self._fallback_spec])
            else:
                self.profile_notes.append(
                    "fallback: NONE -- profile %r names %r and none of them "
                    "is installed under the clients tree. Not an error; this "
                    "root then behaves as it would with no profile."
                    % (self.profile.name,
                       list(self.profile.fallback_installs)))
        else:
            self._fallback_spec = []
        self._fallback_open: Optional[list] = None
        #: Only the ones THIS object opened. An `AssetRoot` handed in by a
        #: caller is borrowed, and closing it here would shut an archive the
        #: caller is still reading.
        self._fallback_owned: list = []
        #: Every install-boundary crossing this object has SERVED, oldest
        #: first: `{asked, declared, logical, origin, source}`. The record
        #: exists so a caller that does not inspect `Located.origin_root`
        #: per hit can still audit, afterwards, whether any answer it got
        #: came from another install.
        self.foreign_hits: list = []
        #: `{logical path: overlay directory}` for every overlay file this
        #: object has SERVED.  The overlay's counterpart to `foreign_hits`,
        #: and a DICT rather than a list on purpose: `locate` is called once
        #: per render, per row, per request, so an append-per-hit would grow
        #: without bound for a set of files that is fixed and small.  Keyed by
        #: logical path, it is bounded by the size of the overlay.
        self.overlay_hits: dict = {}
        #: Fallback roots that were asked for and NOT usable, with the
        #: reason. **An empty fallback list is not a clean fallback list**:
        #: without this, a typo'd install name and a correctly-configured
        #: run that simply found nothing look identical.
        self.fallback_refusals: list = []

    # -- profile selection --------------------------------------------------
    def _pick_profile(self, asked):
        """`(AssetProfile|None, one-line reason)`.

        Precedence, strongest first, and each step says which one fired so
        `profile_summary()` can never claim a default it did not take:

            profile=False / None-the-object   -> OFF, whatever else says
            profile=<AssetProfile> or "<name>" -> that one, forced
            CO_ASSET_PROFILE=off              -> OFF
            CO_ASSET_PROFILE=<name>           -> that one, forced
            (nothing)                         -> the DECLARED default, if the
                                                 install matches one
        """
        def forced(p, why):
            """Record the verdict for a decision no measurement took part in.

            `NOT_MEASURED` is the honest verdict for every branch above the
            default one: a forced profile was not measured against this
            install and a caller that reads `match.verdict` must not be told
            it was. `match.profile` still carries what is in force.
            """
            self.match = ProfileMatch(p, ProfileMatch.NOT_MEASURED, why)
            return p, why

        if asked is False:
            return forced(None, "profile: OFF (caller passed profile=False)")
        if isinstance(asked, AssetProfile):
            return forced(asked,
                          "profile: %r forced by the caller" % asked.name)
        if isinstance(asked, str):
            prof = self.ASSET_PROFILES.get(asked)
            if prof is None:
                # Named and not there is a REFUSAL, not a fall-through to the
                # default: being answered about a different profile than the
                # one you named is worse than not being answered.  Same rule
                # `coroot` applies to a set-but-invalid `CO_ROOT`.
                raise KeyError(
                    "no asset profile named %r; declared profiles are %s "
                    "(core/coassets.py ASSET_PROFILES)"
                    % (asked, sorted(self.ASSET_PROFILES)))
            return forced(prof,
                          "profile: %r forced by the caller" % prof.name)
        env = os.environ.get(self.PROFILE_ENV, "").strip()
        if env:
            if env.lower() in ("0", "off", "no", "none"):
                return forced(None, "profile: OFF (%s=%s)"
                              % (self.PROFILE_ENV, env))
            prof = self.ASSET_PROFILES.get(env)
            if prof is None:
                raise KeyError(
                    "%s=%r names no declared asset profile; declared are %s"
                    % (self.PROFILE_ENV, env, sorted(self.ASSET_PROFILES)))
            return forced(prof, "profile: %r forced by %s"
                          % (prof.name, self.PROFILE_ENV))
        prof = self.profile_for(self.root)
        if prof is not None:
            self.match = ProfileMatch(
                prof, ProfileMatch.NAMED,
                "profile %r matched %s BY NAME" % (prof.name,
                                                   Path(self.root).name))
            return prof, "profile: %r, declared default -- %s" % (prof.name,
                                                                  prof.why)
        # -- NO NAME MATCHED, AND THAT USED TO BE THE END OF IT --------------
        #
        # `profile_for` returned None here and the note said "none declared",
        # which is true and is not the fact a reader needs: it does not say
        # whether the install was MEASURED and found not to need one, or was
        # never put to any test.  Those were the same None, and `7632`,
        # `7682` and `7867` sat at a 13% appearance-resolution rate inside it
        # for as long as the table was keyed on the literal `"7878"`.
        #
        # The measurement runs here, it is REPORTED here, and since
        # 2026-09-04 it also DECIDES here: a `QUALIFIES` verdict applies the
        # profile.  ONE PROFILE, MANY INSTALLS.  Detection picks WHICH
        # declared profile applies to this root; it does not author one.  A
        # root that qualifies gets the SAME `AssetProfile` object 7878 gets,
        # with the same corpus and the same overlay globs -- there is still
        # exactly one entry in `ASSET_PROFILES` and adding three literals to
        # it was the thing this replaces.
        #
        # Every other verdict returns None, and each of them SAYS which None
        # it is.
        if self._hurdle_probe_enabled():
            try:
                m = self.profile_match(self.root)
            except Exception as e:                          # noqa: BLE001
                m = ProfileMatch(
                    None, ProfileMatch.NOT_MEASURED,
                    "the shipped-art predicate raised %s: %s"
                    % (type(e).__name__, e))
            self.match = m
            if m.matched:
                return m.profile, "profile: %r, by the shipped-art predicate " \
                    "-- %s" % (m.profile.name, m.note)
            return None, "profile: none for %s -- %s" % (
                Path(self.root).name, m.note)
        self.match = ProfileMatch(
            None, ProfileMatch.NOT_MEASURED,
            "the shipped-art predicate is OFF (%s), so None here means NOBODY "
            "ASKED and not that %s was measured and does not qualify"
            % (self.HURDLE_ENV, Path(self.root).name))
        return None, ("profile: none declared for %s (names: %s) -- %s"
                      % (Path(self.root).name,
                         sorted(self.install_names(self.root)),
                         self.match.note))

    #: Kill switch for the shipped-art predicate, read once per construction.
    #: `off`/`0`/`no`/`none` turns it off.  It exists because the predicate
    #: costs filesystem work on every install with no name match, and a
    #: caller measuring that cost must be able to remove it -- but the OFF
    #: state is itself recorded, so "not asked" never reads as "asked and
    #: answered no".  That distinction is the whole point of this machinery
    #: and a switch that erased it would put the defect straight back.
    HURDLE_ENV = "CO_ASSET_HURDLE"

    @classmethod
    def _hurdle_probe_enabled(cls) -> bool:
        return os.environ.get(cls.HURDLE_ENV, "").strip().lower() not in (
            "0", "off", "no", "none")

    @property
    def overlay(self) -> Optional[Path]:
        """The FIRST overlay directory, or None.

        Kept because `overlay` was a single path before overlays became a
        list, and read-only because the list is now what every lookup walks --
        an assignment here would set a value nothing consults, which is the
        quietest kind of wrong.  Write `AssetRoot(root, overlay=[a, b])`.
        """
        return self.overlays[0] if self.overlays else None

    def profile_summary(self) -> str:
        """Every profile decision this root made, one per line.

        Printed by the tools rather than inferred: `AssetRoot(root)` on a
        declared install now answers with art from four other installs and a
        materialised overlay, and a run that does not SAY that is a run whose
        numbers cannot be reproduced.

        THE VERDICT LINE IS NOT DECORATION.  A root with no profile prints
        WHY it has none -- measured and does not need one, measured and this
        corpus does not help, between the bands, or never asked.  Those were
        one silent None until 2026-09-04 and the silence cost three installs
        an 80-point resolution rate.
        """
        head = "root %s" % self.root
        lines = [head] + ["  " + n for n in self.profile_notes]
        m = getattr(self, "match", None)
        if m is not None:
            lines.append("  verdict: %s" % m.verdict)
            if m.bare_rate is not None:
                lines.append(
                    "  measured: bare %.2f%%%s over %d of %d declared rows "
                    "(sample %s, seed %d)"
                    % (100 * m.bare_rate,
                       "" if m.full_rate is None
                       else ", with this profile %.2f%%" % (100 * m.full_rate),
                       m.sampled, m.population, m.sample_sig,
                       self.HURDLE_SEED))
        return "\n".join(lines)

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
        for ov in self.overlays:
            p = safepath.confine(ov, logical)
            if p.is_file():
                # STAMPED, and this is the whole provenance requirement for
                # the overlay in one line. The overlay writes a donor's mesh
                # at 7878's OWN path shape -- that is what makes the client
                # load it, and it is also what makes it indistinguishable
                # from art 7878 shipped. `source="overlay"` is unchanged
                # (`tests/test_garment_overlay.py` asserts it), and
                # `origin_root` is what a PROGRAM branches on: `.foreign` is
                # now True for these, `str(loc)` says FOREIGN <- <dir>, and
                # `read_located` reads them from the directory they came from
                # instead of re-resolving.
                self.overlay_hits[logical] = str(ov)
                return Located(logical, "overlay", p, p.stat().st_size,
                               origin_root=Path(ov))
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

    def peek(self, logical: str, n: int = 32) -> bytes:
        """The first `n` bytes of `logical`, WITHOUT reading the whole asset.

        **THE MISSING SEAM.** Both containers have had a `peek(entry, n)` for
        some time -- `WdfArchive.peek` slices its mmap, `_TpdContainer.peek`
        inflates only the first zlib chunk -- and four tools pair it with
        `wdf.detect_magic` to ask "what IS this entry". But nothing routed a
        LOGICAL PATH to it: this class had `read` and no `peek`, so a caller
        holding a path had only a full read and decompress available.

        **THE 6x IS NOT THIS SEAM'S EITHER, AND THIS DOCSTRING USED TO SAY IT
        WAS.** Per `C-2026-09-30-claude-coviewer-6x-provenance`, whose home is
        `tools/catalog.py:575`, the "6x slower" classify measurement
        (0.22s -> 1.28s per 40k paths, provenance `b1979d9b`) is the cost of
        `catalog.py`'s `classify` default reaching the LOOSE FILE for every path
        in the browse index -- `_peek_loose`'s `open(q, "rb").read(16)`,
        `tools/catalog.py:610`. Three different operations have now each been
        described as that one number. This seam is a real gap on its own terms;
        the 6x is not its evidence, and a cost for it is owed by measurement.

        Resolution order is `read`'s, so a peek and a read of the same path
        can never disagree about which bytes they mean.
        """
        loc = self.locate(logical)
        if loc is None:
            raise FileNotFoundError(logical)
        if loc.real_path:
            with open(loc.real_path, "rb") as fh:
                return fh.read(n)
        arc = self._archives[loc.source]
        by_name = getattr(arc, "get_by_name", None)
        e = by_name(loc.logical) if by_name is not None else None
        if e is None:
            e = arc.get(tq_hash(loc.logical))
        if e is None:
            # `locate` found it, so this is not a miss; fall back to `read`
            # rather than report a present asset as empty.
            return self.read(logical)[:n]
        return arc.peek(e, n)

    def tpd_containers(self) -> list:
        """This install's DatPkg containers (empty for a WDF-only install)."""
        return [a for a in self._archives.values()
                if isinstance(a, _TpdContainer)]

    def use_tpd_cache(self, cache) -> None:
        """Route every `.tpd` inflate through ``cache`` (a
        `tpdcache.TpdCache`), or stop with None. Loose files and WDF entries
        are not compressed, so they have nothing to cache and are untouched.
        """
        for a in self.tpd_containers():
            a.cache = cache

    def exists(self, logical: str) -> bool:
        return self.locate(logical) is not None

    def in_archives(self, logical: str) -> bool:
        """Does one of this install's ARCHIVES hold `logical`?

        **NOT a substitute for `locate`, and not a cheaper `exists`.** It
        skips the overlay and loose-file stages entirely, so it answers
        "somewhere in the archives" and nothing else; a caller that uses it
        where `locate` was meant will report a loose file as absent.

        It exists for ONE caller shape: asking about a path already known to
        have no loose file behind it -- `meshtex._global_only`, which is the
        pooled recovered names MINUS the install's own loose tree and its
        container-declared paths. For those, `locate`'s `safepath.confine` +
        `is_file` is provably wasted work, and on Windows that stat is the
        whole cost: MEASURED over 2,000 such names on Clients/5017,

            locate()       0.32 ms/name
            in_archives()  0.01 ms/name        28x

        which is the difference between a texture work list that can afford
        to check what it offers and one that cannot.
        """
        h = tq_hash(logical)
        for arc in self._archives.values():
            by_name = getattr(arc, "get_by_name", None)
            if by_name is not None:
                if by_name(logical):
                    return True
                continue
            if arc.get(h):
                return True
        return False

    def container_names(self) -> set[str]:
        """Every logical path this install's CONTAINERS declare, normalised.

        Not "every asset": a WDF index stores only `tq_hash(name)`, so a WDF
        declares NOTHING and its entries stay reachable only through the
        recovered-name tables in `out/wdf/` and `out/dll/`.  A `.tpd`/`.tpi`
        pair stores the path itself and needs no table at all.  So this is
        the half of an install's universe that requires no recovery, and on a
        TPD client it is nearly all of it.

        MEASURED on Zephyr 2026-09-18: 130,532 declared -- 29,717 `.c3` and
        98,812 `.dds` across `c3.tpi` and `data.tpi` -- against 14,051
        hash-only entries in five `garments*.wdf`.  Before this existed the
        census counted those 130,532 (it walks `_discover_archives`) and
        `meshtex`'s universe did not (it reads the recovered tables), so the
        two corpora disagreed by two orders of magnitude on the same install
        and the thumbnail worklist took the smaller one: 324 loose `.c3`
        offered against a census of 31,421.

        **THREE METHODS ON THIS CLASS HAVE NEARLY THE SAME NAME AND ANSWER
        DIFFERENT QUESTIONS.**  Spelled out so nobody picks by autocomplete:

            declared_paths(asset_id, kind)  what this install's TABLES
                                            (`c3.wdb`, the inis) name as the
                                            path for ONE asset id. Per id,
                                            existence unchecked.
            contested_names()               the hashes on which two RECOVERED
                                            name tables DISAGREE -- a fact
                                            about the recovery, not about
                                            this install's contents.
            container_names()               THIS ONE. What the CONTAINER
                                            INDEXES store, every entry, no id
                                            involved and nothing recovered.

        The first draft of this method was called `declared_names`, and
        Python's own `AttributeError` offered `declared_paths` as the
        correction -- which is how close the two read to a reader who is not
        looking for the distinction.
        """
        out: set[str] = set()
        for arc in self._archives.values():
            names = getattr(arc, "names", None)
            if names is None:
                continue
            out.update(n.replace("\\", "/").lower() for n in names())
        return out

    # -- appearance tables -------------------------------------------------
    @staticmethod
    def _table_path(loc: "Located") -> Path:
        """The real path of a located appearance table, or a loud refusal.

        `PartIni` needs a filesystem path because it reads the compiled `.dbc`
        twin sitting BESIDE the ini. A table resolved out of an archive has no
        such path, and materialising one to a temp file would also strip it of
        its twin -- silently downgrading a compiled table to its stale
        plaintext. MEASURED 2026-08-29 across 5065/6090/6609/7205/7878: of the
        50 tables those installs declare, ZERO resolve to an archive. So this
        branch guards a case no install has, and it refuses instead of
        skipping, because a silent skip here reads as "this install ships no
        armour table".
        """
        if loc.real_path is None:
            raise NotImplementedError(
                f"appearance table {loc.logical!r} resolves inside {loc.source}; "
                f"PartIni reads the .dbc twin beside the file and so needs a real "
                f"path. No measured install ships a table this way -- if you are "
                f"seeing this, the archive layout changed.")
        return loc.real_path

    def role_parts(self) -> list[dict]:
        r"""**The install's own declaration of which parts exist and which
        table backs each one**, as ``[{"part", "mesh_ini", "motion_ini",
        "source"}, ...]``.

        Two sources say this, and until 2026-09-06 only the older one was read:

        * ``ini/RolePart.ini`` ``[Config]`` -- ``Count``, ``Part<i>``,
          ``MeshIni<i>``, ``MotionIni<i>``. Plaintext, and **stale on the
          5165-6868 lineage** the same way `PartIni`'s inis are stale.
        * the ``ROPT`` section of ``ini/c3.wdb`` -- the compiled form, which is
          what the client actually loads. `wdb.ResourceDb.role_parts`.

        **THE SEMANTIC POINT, which the tree used to get by inference:**
        ``armor.ini`` is not "the armor table". It is *the mesh table for the
        part named* ``body``, and the part list is declarative and extensible
        -- ``body``, ``armet``, ``r_weapon``, ``l_weapon``, ``mount``,
        ``misc``, ``head``, ``shield``, ``mix_body``, ``mix_armet`` on 5517,
        growing to fifteen by 7867 (``cape``, ``pelvis``, ``spirit``,
        ``armet_dx8``, ``mix_armet_dx8``).

        WHAT THE TWO SOURCES SAY, MEASURED 2026-09-06 over the 36 directories
        under `coroot.clients_dir()`
        ---------------------------------------------------------------------
        30 ship an ``ini/c3.wdb``, and all 30 carry a decodable ROPT. 33 ship a
        ``RolePart.ini``. On the **30 installs that have both**, comparing part
        name -> (mesh table, motion table) with the extension normalised away:

            conflicts .................................. 0 of 30 installs
            parts declared by ROPT and not by the ini .. 40, over 22 installs
            parts declared by the ini and not by ROPT ..  0

        **The one-sidedness of that last pair is the thing to distrust, so it
        was checked in the direction that can produce it.** A comparison that
        omitted half the corpus would look exactly like this. It did not: the
        "only in RolePart.ini" column is non-empty for 5017 (7 parts), 5065 (8)
        and CCO-snapshot-2026-08-24 (13) -- **the three installs that ship a
        RolePart.ini and no c3.wdb** -- so the control fires in both
        directions, and the emptiness of that column on the other 30 is a
        result rather than an artefact of never looking.

        The 40 extras are all cases where the plaintext went stale and the
        compiled form moved on: ``mix_body``/``mix_armet`` on the fourteen
        installs from 5165 to 6868 (whose ``RolePart.ini`` still says
        ``Count=8``, while 6907's identical-content ini says 12 and lists them),
        ``armet_dx8``/``mix_armet_dx8`` on the six from 6652 to 6868, and
        ``spirit`` on 7867/7878.

        MERGE RULE, AND IT IS ADDITIVE BY CONSTRUCTION
        ----------------------------------------------
        The plaintext's entries come first, in ITS order and with ITS strings;
        ROPT-only parts are appended. So an install with no ROPT is served
        exactly what it was served before, and an install with one gains parts
        and never loses or reorders any. Where a part is in both, the
        plaintext's row wins -- **not because it is better, but because 0 of 30
        installs disagree, so the choice is unobservable on shipped data and
        the additive one is the one that cannot regress anything.** A future
        install that does disagree would be served the stale answer; that is a
        declared limit, not a measurement.

        Returns ``[]`` -- not an error -- when the install declares neither.
        `part_tables` is where "no declaration at all" is still fatal.
        """
        out: list[dict] = []
        seen: set[str] = set()
        cfg_loc = self.locate("ini/RolePart.ini")
        if cfg_loc is not None:
            conf = parse_ini(self._table_path(cfg_loc)).get("Config", {})
            for i in range(int(conf.get("Count", "0") or 0)):
                part = conf.get(f"Part{i}")
                mesh = conf.get(f"MeshIni{i}")
                if not part or not mesh:
                    continue
                seen.add(part)
                out.append({"part": part, "mesh_ini": mesh,
                            "motion_ini": conf.get(f"MotionIni{i}", ""),
                            "source": "ini/RolePart.ini"})
        for rec in self._compiled_role_parts():
            if rec["part"] in seen:
                continue
            seen.add(rec["part"])
            out.append({"part": rec["part"], "mesh_ini": rec["mesh_ini"],
                        "motion_ini": rec["motion_ini"],
                        "source": "ini/c3.wdb ROPT"})
        return out

    def _compiled_role_parts(self) -> list[dict]:
        """`ROPT`'s part records, or `[]` when this install declares none.

        Goes through `resource_db`, which is lazy and cached per instance, so
        the ``ini/c3.wdb`` walk is paid AT MOST ONCE per `AssetRoot` and is
        shared with `resolve_declared`, which already pays it for every asset
        lookup. That matters: `part_tables()` re-parses on every call and is
        reached per id from `resolve_appearance`, so decoding ROPT there
        directly would put a 4.5s walk (7878, MEASURED) inside a loop.

        `locate()` searches overlay, loose and this install's OWN archives and
        **does not consult `_fallbacks()`**, so a donor install can never
        supply the part list for an install that ships no `c3.wdb`. Checked:
        `part_tables()` on 5065 and CCO-snapshot -- both profile-eligible and
        neither shipping a `c3.wdb` -- returns exactly what it returned before
        this existed.

        `NotImplementedError` is caught HERE and nowhere else. `resource_db`
        goes through `_table_path`, which refuses a `c3.wdb` living inside an
        archive; that refusal is right for an appearance table, where a silent
        skip would read as "this install ships no armour table", and wrong
        here, where the honest answer is "then it declares no extra parts and
        the plaintext still works". No install measured resolves `c3.wdb` out
        of an archive, so this arm is UNEXERCISED by shipped data.
        """
        try:
            db = self.resource_db
        except NotImplementedError:
            return []
        if db is None:
            return []
        return list(db.role_parts or ())

    def part_tables(self) -> dict[str, PartIni]:
        """Load every appearance table this install DECLARES that actually
        ships in it.

        ONE ENTRY PER DECLARED **SLOT**, AND SLOTS SHARE TABLES ON PURPOSE.
        `Part<i>` is a slot the client renders; `MeshIni<i>` is the table that
        slot reads. Two slots over one table is the NORMAL declaration, not a
        duplicate to be collapsed. MEASURED 2026-09-06 over every install here
        that ships a `RolePart.ini` -- **33 of 33 keep at least one shared
        table pair**: `r_weapon`/`l_weapon` both read `ini/weapon.ini`
        everywhere, and from 5165 up `mix_body`/`body` share `ini/armor.ini`
        and `mix_armet`/`armet` share `ini/armet.ini`.

        The mapping is keyed by `part`, so a slot declared TWICE already
        collapses on its own. A dedup that keyed on the TABLE instead would
        delete a slot the builder uses on every install in the corpus, and
        which of the two weapon hands disappeared would be decided by nothing
        but the order the config happens to list them in.

        This paragraph is salvaged from `claude/q-tail`, whose code change is
        superseded -- master already keeps all 33, verified by the measurement
        above before that branch was retired. The REASONING is kept because
        the next person to look at this dict will see what appears to be
        duplicate values and be tempted by exactly the guard that branch was
        written to remove.

        The declaration comes from `role_parts()` -- ``ini/RolePart.ini``
        first, then the ``ROPT`` section of ``ini/c3.wdb`` for parts the
        plaintext does not mention. **Additive**: an install with no ROPT gets
        precisely what it got before, and no install loses a part.

        **Resolution goes through `locate()`, so the tables obey the same
        precedence as the assets they name: overlay, then loose, then
        archives.** This used to join `self.root` directly, which meant
        `AssetRoot(root, overlay=...)` redirected every mesh and texture
        lookup EXCEPT the tables that decide which meshes exist -- an overlay
        `armor.ini` was read straight past while the meshes it declared
        resolved through the overlay perfectly well.

        MEASURED on 7878 (2026-08-29): a staged overlay takes the builder's
        body slot from 216 options over **1** distinct mesh to 478 over 263,
        weapons from 47/8 to 121/82, and armet from unusable to 12/12. Through
        the old direct join the same overlay changed nothing at all -- not one
        row -- which is the shape of this bug: not an error, an omission that
        looks like a correctly-loaded install.

        The join is also now confined. `rel` comes out of the install's own
        declaration, and `self.root / rel` followed `../` wherever it pointed;
        `locate()` refuses an escape rather than reading it.

        WHAT A ROPT ROW NAMES, AND WHY BOTH EXTENSIONS ARE TRIED
        --------------------------------------------------------
        ROPT names ``ini/armor.dbc`` where the plaintext names
        ``ini/armor.ini``: the compiler rewrites the path string, not just the
        payload. `PartIni` wants the ``.ini`` and finds the compiled twin
        beside it by itself, so the ``.ini`` sibling is tried FIRST and the
        literal string second.

        **That ordering is a measurement, not a preference.** Over the 350 ROPT
        declarations on the 30 installs that ship one: 149 ship both
        extensions, 113 ship only the ``.ini``, 88 ship neither, and
        **``.dbc``-only is 0**. Resolving the literal string alone would lose
        113 tables and gain none. The ``.dbc`` arm therefore has no shipped
        exercise and exists so a client that finally drops the plaintext still
        loads -- it is UNVERIFIED against real data and is written to be
        greppable rather than silent about that.

        A NOTE ON THE DUPLICATE GUARD BELOW, WHICH CANNOT FIRE
        ------------------------------------------------------
        ``rel not in {t.path.name for t in out.values()}`` compares
        ``"ini/weapon.ini"`` against ``"weapon.ini"``, so it is never True and
        every part gets a key even when two parts share one table. MEASURED
        2026-09-06 across the corpus: ``r_weapon`` AND ``l_weapon`` are both
        present in the result on every install that declares both, which is
        only possible because the guard does not fire. **It is left exactly as
        it is.** Making it work would DELETE keys that callers use today
        (`tools/builder.PRIMARY_SLOTS` names both weapons), which is the
        opposite of additive; it is recorded here so the next reader does not
        mistake it for a working invariant, and so nothing new is built on top
        of it.
        """
        declared = self.role_parts()
        if not declared:
            # Same failure the direct join produced, kept loud on purpose: an
            # install that declares no parts at all is broken, not table-less.
            # Named in both sources, because "no RolePart.ini" is no longer the
            # only way to get here.
            raise FileNotFoundError(
                "ini/RolePart.ini (and no ROPT section in ini/c3.wdb)")
        out: dict[str, PartIni] = {}
        for rec in declared:
            part, rel = rec["part"], rec["mesh_ini"]
            loc = None
            for cand in _table_candidates(rel):
                loc = self.locate(cand)
                if loc is not None:
                    rel = cand
                    break
            if loc is None:
                continue                    # declared in the config, not shipped
            p = self._table_path(loc)
            if rel not in {t.path.name for t in out.values()}:
                # NARROWED from `except Exception: pass` (2026-08-10), for the
                # same reason as `PartIni.__init__`: this one sits one layer
                # up and would have re-hidden anything the narrowing there
                # let through. A table that is declared in the config and
                # present on disk but unreadable is dropped from the mapping
                # with no key, so a caller sees "this install has no armour
                # table" -- the shape `dbc.weaponmotion_join` was changed to
                # refuse rather than return empty.
                try:
                    out[part] = _part_ini_cached(p)
                except (ValueError, struct.error, OSError):
                    continue
        return out

    # -- ID -> file --------------------------------------------------------
    # INFERRED: bare numeric IDs in the appearance tables are resolved by
    # probing these subdirectories, trying the ID as written, zero-padded to 9
    # digits, and stripped of leading zeros. This reproduces 94% of armor.ini,
    # 98% of weapon.ini and 79% of armet.ini references. The client's real rule
    # lives inside the Themida-packed exe and has not been read directly.
    # 7878 ships NEITHER `c3/mesh` NOR `c3/texture` -- it splits art into
    # per-kind directories instead, and adds kinds that did not exist in
    # 5065/6090/6609 (armet, head, cape, pelvis, spirit).  Headgear was
    # therefore unresolvable BY CONSTRUCTION on that client: `armet` was
    # not in either list, so no probe ever looked where the files are.
    #
    # The new names are APPENDED, never inserted.  Resolution is
    # first-hit-wins, so appending cannot change what an older client
    # resolves to -- verified by re-measuring 5065/5517/6090/6609 against
    # these lists before and after.
    MESH_DIRS = ("mesh", "weapon", "body", "hair", "mount", "npc", "monster")
    TEX_DIRS = ("texture", "weapon", "body", "hair", "mount", "npc", "monster")

    #: Tried ONLY where the namespace is enumerable -- see `_c3_names`. Every
    #: probe is a filesystem stat, so appending these unconditionally taxed
    #: the clients that never had them: measured, 6609 went from 14s to 53s
    #: over 360 appearances for six directories it does not ship. Where the
    #: name set IS known the probe is a set lookup and costs nothing, and
    #: that is exactly the case where these directories exist.
    MESH_DIRS_EXTRA = ("armet", "head", "cape", "pelvis", "spirit", "misc")
    TEX_DIRS_EXTRA = ("armet", "head", "cape", "pelvis", "spirit", "misc")

    def _c3_names(self):
        """Every `c3/...` logical path this root can serve, or None.

        **None means "cannot be enumerated" and is NOT an empty set** -- the
        distinction is the whole safety of this index.  A `.wdf` is
        hash-indexed: you can ask it whether a name is present but you cannot
        list what it holds.  So on a WDF root this returns None and every
        caller falls back to probing, exactly as before.

        Used only as a NECESSARY CONDITION: a name absent from here cannot be
        found by `locate` either, so the probe can be skipped.  A name
        present here still goes through `locate`, which keeps the real
        precedence (overlay, then loose, then archives) in one place.
        """
        if self._c3_name_set is not False:
            return self._c3_name_set
        names: set = set()
        for arc in self._archives.values():
            entries = getattr(arc, "entries", None)
            if not entries:
                self._c3_name_set = None       # nothing to enumerate
                return None
            for e in entries:
                n = getattr(e, "name", None)
                if not n:
                    # A NAMELESS entry makes the whole set unknowable. This
                    # is not hypothetical and it is not rare: 6609's c3.wdf
                    # has 10,274 entries and ALL 10,274 are nameless -- a WDF
                    # stores only tq_hash(name), so a name is RECOVERED, not
                    # read. An earlier version of this guard tested `entries`
                    # for emptiness, which is true of no WDF, so the set was
                    # built from the loose tree alone and every archived mesh
                    # looked absent. Measured: 6609 body meshes fell from
                    # 120/120 to 91/120. The skip is only sound when the set
                    # is COMPLETE, so anything less than complete is None.
                    self._c3_name_set = None
                    return None
                names.add(n.replace(chr(92), "/").lower())
        root = Path(self.root)
        for base in [root] + list(self.overlays):
            c3 = Path(base) / "c3"
            if not c3.is_dir():
                continue
            # `os.walk` hands back the filenames the directory read already
            # returned. `rglob("*") + is_file()` asks the OS again, one stat
            # per entry, for an answer it was just given. MEASURED on 7878
            # (191,162 files): the walk `Catalog._scan_loose` does costs 1.5s
            # and this cost ~6s for a SUBSET of the same tree, which is what
            # made a cold open walk the install three times over.
            #
            # The SET IS UNCHANGED and that is the safety-critical part: an
            # incomplete set here is not a slow answer, it is a wrong one --
            # 6609's body meshes fell 120/120 -> 91/120 the last time this
            # index lost entries. `tests/test_c3_names_walk.py` pins the two
            # constructions equal on every installed client.
            base_len = len(str(c3)) + 1
            for dirpath, _subdirs, filenames in os.walk(c3):
                prefix = str(dirpath)[base_len:]
                for fn in filenames:
                    rel = (prefix + os.sep + fn) if prefix else fn
                    names.add(("c3/" + rel).replace(chr(92), "/").lower())
        self._c3_name_set = names
        return names

    def _art_by_garment(self) -> dict:
        """`{(kind, last6): [stem, ...]}` over every art file this install has.

        WHY THIS EXISTS.  From 7878 the appearance tables and the art use
        DIFFERENT id widths for the same garment.  `armor.ini` says mesh
        ``1130000``; the files are ``c3/body/7130000.c3`` and
        ``c3/body/8130000.c3``.  The trailing six digits -- the garment --
        agree; the leading digits are the BODY TYPE, and the table's leading
        `1` is a placeholder the client substitutes for whoever is wearing it.

        DISCOVERED, NOT GUESSED.  The body types are read off the install
        (7 and 8 for bodies, plus 1995/1996 for armets on this one) rather
        than hardcoded.  That matters both ways: an install with other body
        types still resolves, and -- more important -- a garment this install
        does NOT ship cannot be resolved to a plausible neighbour, because
        the only candidates are stems that exist.

        Name-indexed containers only.  A `.wdf` is hash-indexed and cannot be
        enumerated, but those clients keep everything in `c3/mesh` and
        `c3/texture` and already resolve directly, so they never need this.
        """
        if self._garment_index is not None:
            return self._garment_index
        idx: dict = {}
        textured: set = set()

        def add(kind: str, base: str) -> None:
            stem, _, ext = base.rpartition(".")
            ext = ext.lower()
            if ext not in ("c3", "dds") or len(stem) < 6:
                return
            if ext == "dds":
                textured.add((kind, stem))
            idx.setdefault((kind, stem[-6:]), [])
            if stem not in idx[(kind, stem[-6:])]:
                idx[(kind, stem[-6:])].append(stem)

        for arc in self._archives.values():
            entries = getattr(arc, "entries", None)
            if not entries:
                continue                      # hash-indexed: nothing to walk
            for e in entries:
                n = getattr(e, "name", None)
                if not n:
                    continue
                n = n.replace(chr(92), "/").lower()
                parts = n.split("/")
                if len(parts) < 3 or parts[0] != "c3":
                    continue
                add(parts[1], parts[-1])
        # THE OVERLAY IS WALKED TOO, and it was not until 2026-08-29.
        #
        # `locate` has always given the overlay precedence, and `_c3_names`
        # has always walked it -- this function walked the loose tree and the
        # archives only. So an overlay file was VISIBLE to a direct
        # `locate("c3/body/7130030.c3")` and INVISIBLE to the garment index
        # built one function below, which is the only path `resolve_garment`
        # takes. Measured before the fix, with 7205's mesh dropped into an
        # overlay at 7878's own path shape:
        #
        #     locate            c3/body/7130030.c3  [overlay, 58721 bytes]
        #     _c3_names         contains it
        #     _art_by_garment   ('body','130030') -> None
        #     resolve_asset     None
        #
        # An asset that IS there, reported absent -- the same defect class as
        # `AnUnrecoveredNameIsNotAMissingFile`, and here it made the whole
        # point of the overlay parameter unreachable for garments. Purely
        # additive: an overlay stem is a stem, `locate` still decides
        # precedence, and a root with no overlay walks exactly what it did.
        for base in [self.root] + list(self.overlays):
            c3 = Path(base) / "c3"
            if c3.is_dir():
                for d in c3.iterdir():
                    if not d.is_dir():
                        continue
                    # `os.walk`, not `rglob("*") + is_file()`. MEASURED
                    # 2026-09-05 on 7867: that pattern issued **175,869
                    # `is_file()` calls costing 14.3 s of a 22.6 s
                    # `AssetRoot()` construction** -- one stat per entry, to
                    # learn what the directory listing already said. os.walk
                    # yields filenames already separated from directories, so
                    # the same answer costs no stat at all. The identical
                    # substitution was made in `_c3_names` earlier the same
                    # day; this is the second site, found by profiling a
                    # 22-second install open that nobody had attributed.
                    kind = d.name.lower()
                    for _dirpath, _dirnames, filenames in os.walk(d):
                        for fname in filenames:
                            add(kind, fname.lower())
        # A stem that HAS a sibling .dds sorts first. One garment can ship
        # under several body types and they are not interchangeable: 37 of
        # 7878's 90 armet meshes are the 10-digit 1995xxxxxx/1996xxxxxx form
        # and NONE of those has a texture, while all 53 of the 7xxxxxx and
        # 8xxxxxx do. A plain sort puts "1995111000" ahead of "7111000"
        # because '1' < '7', so every headgear resolved to the one variant
        # that cannot be textured -- armet meshes came back 10 of 120 and
        # armet textures 0 of 120. Preferring a mesh you can texture is not
        # a tie-break, it is the difference between a model and a model with
        # no skin.
        for (kind, _g), v in idx.items():
            v.sort(key=lambda s: ((kind, s) not in textured, s))
        self._garment_index = idx
        return idx

    def resolve_garment(self, asset_id: str, kind_dirs, ext: str, *,
                        fallback: bool = True):
        """Resolve `asset_id` by GARMENT (its last six digits) + body type.

        Returns `(Located, stem)` or None.  The stem is returned because the
        texture that goes with a mesh is the mesh's OWN name with a `.dds`
        extension on these clients -- see `resolve_appearance`.

        `fallback=False` disables the old-corpus fallback for ONE call without
        touching how the root was configured.  That is what makes the fallback
        falsifiable: `tests/test_garment_oldform_fallback.py` re-asks every
        newly-resolving row with it off and requires the answer to go back to
        None.  A fallback that cannot be switched off cannot be ablated, and a
        test that cannot fail is not a test.
        """
        if not asset_id or len(asset_id) < 6:
            return None
        # Only where the namespace is ENUMERABLE. On a WDF root the archive
        # entries are nameless, so this index would be built from the loose
        # tree alone -- it could not find an archived garment, which is the
        # only thing it exists to do, and it would cost a full walk of the
        # install to answer nothing. `_c3_names` is the same knowability
        # test the probe skip uses, so the two agree by construction.
        if self._c3_names() is None:
            # Unchanged when no fallback is configured: `_old_corpus_garment`
            # returns None on an empty fallback list, which is the `return
            # None` that stood here.
            return self._old_corpus_garment(asset_id, kind_dirs, ext) \
                if fallback else None
        idx = self._art_by_garment()
        # THE DECLARED MESH FIRST, THE DERIVED ONE AS FALLBACK.
        #
        # This function derives a garment key from the id it was handed. The
        # install DECLARES the answer instead: every section of `armor.ini` and
        # its siblings carries `Mesh0=`, already parsed into `PartRef.mesh` and
        # already used by `resolve_appearance` -- just never consulted here.
        #
        # Measured on 7878's `armor.ini`, 955 sections:
        #     rows carrying an explicit Mesh0        955  100.0%
        #     rows where Mesh0 != the section id     714   74.8%
        #     both mechanisms resolve, SAME file     264
        #     both resolve, DIFFERENT file             8   <- declared wins
        #     only the declared Mesh0 resolves        21   <- gained
        #     only the section id resolves            83   <- why it is a
        #                                                     FALLBACK and not
        #                                                     a replacement
        #
        # The 8 are not cosmetic: `1132000`, `2132000`, `3132000` and `4132000`
        # all DERIVE to `c3/body/7132000.c3` and all DECLARE `1135000`, so the
        # derived rule shows the wrong armour on four body types at once.
        keys = []
        dm = self._declared_mesh(asset_id)
        if dm and len(dm) >= 6 and dm[-6:] != asset_id[-6:]:
            keys.append(dm[-6:])
        keys.append(asset_id[-6:])
        # AN ALL-ZERO KEY CARRIES NO INFORMATION, AND IT DOES NOT MISS -- IT
        # HITS THE WRONG FILE.  `armor.ini` declares 9-digit `Mesh0` values in a
        # second id space (`104000000`, `202000000`, ...) whose last six digits
        # are `000000`; so do 3 rows via their own section id.  There is exactly
        # one art file on 7878 whose stem ends in six zeros -- `c3/head/
        # 7000000.c3` -- so all 219 of them matched it and 216 body rows were
        # served A HEAD MESH.  That is worse than a miss: `resolve_asset`
        # returned a `Located` and every caller counted it as resolved.
        #
        # MEASURED on 7878, declared default, `garment_oldform_compare
        # --profile`:
        #     armor.ini   955 rows   955 "resolved"   219 of them -> c3/head/
        #                                             7000000.c3
        #     all 219 carry a last-six key of `000000`, from `Mesh0` (216) or
        #     from the section id (3: 118000000, 256000000, 407000000)
        #     the predictor is exact: 216 of 216 all-zero Mesh0 keys degenerate
        #
        # This does NOT resolve those rows -- it stops MIS-resolving them.  The
        # honest armor figure is 736 of 955, not 955, and whether a second rule
        # reaches the 9-digit space is an open question, not this guard's job.
        keys = [k for k in keys if k.strip("0")]
        for sub in kind_dirs:
            for key in keys:
                for stem in idx.get((sub, key), ()):
                    loc = self.locate("c3/%s/%s%s" % (sub, stem, ext))
                    if loc:
                        return loc, stem
        # LAST, after every one of this install's OWN candidates has missed,
        # so a row that resolves inside this install resolves to the SAME
        # file as before. Verified, not asserted: 955 armor.ini + 1168
        # armet.ini + 4828 weapon.ini rows compared against master, LOST 0
        # and CHANGED 0.
        if fallback:
            return self._old_corpus_garment(asset_id, kind_dirs, ext)
        return None

    # -- the old-corpus fallback -------------------------------------------
    @classmethod
    def old_corpus_roots(cls, clients=None, names=None) -> list:
        """The pre-7878 installs present on this box, in fallback order.

        `names` overrides `OLD_CORPUS_INSTALLS` -- that is how an
        `AssetProfile` names its own corpus without this method having to know
        profiles exist.

        Resolved through `coroot.clients_dir()`, never written down: the
        clients tree is ONE box's fact and `tests/test_sanitization.py`
        refuses a literal for exactly that reason.  Pass `clients` to point
        the fallback at another corpus.

        Returns `[]` where none of them is installed, which is a real answer
        and not an error -- the caller then gets a root with no fallback,
        which behaves exactly as it did before this existed.
        """
        base = Path(clients) if clients else coroot.clients_dir()
        want = cls.OLD_CORPUS_INSTALLS if names is None else tuple(names)
        return [base / n for n in want if (base / n).is_dir()]

    def _fallbacks(self) -> list:
        """The opened fallback installs, built once.

        A spec that cannot be opened is REFUSED WITH A REASON into
        `fallback_refusals` rather than skipped, and a spec naming this very
        root is refused too: serving 7878 out of 7878 is not a crossing, and
        counting it as one would make the provenance record lie in the
        direction that matters.
        """
        if self._fallback_open is not None:
            return self._fallback_open
        out: list = []
        for spec in self._fallback_spec:
            if isinstance(spec, AssetRoot):
                ar = spec                    # borrowed: not ours to close
            else:
                p = Path(spec)
                if not p.is_dir():
                    self.fallback_refusals.append(
                        {"root": str(p), "why": "not a directory"})
                    continue
                missing = coroot.missing_parts(p)
                if missing:
                    self.fallback_refusals.append(
                        {"root": str(p),
                         "why": "not a complete install: missing "
                                + ", ".join(missing)})
                    continue
                try:
                    ar = AssetRoot(p)
                except Exception as exc:            # pragma: no cover
                    self.fallback_refusals.append(
                        {"root": str(p), "why": "%s: %s"
                         % (type(exc).__name__, exc)})
                    continue
                self._fallback_owned.append(ar)     # opened here, closed here
            if Path(ar.root) == Path(self.root):
                self.fallback_refusals.append(
                    {"root": str(ar.root),
                     "why": "same install as the root being asked -- not a "
                            "crossing, so it would forge provenance"})
                continue
            out.append(ar)
        self._fallback_open = out
        return out

    @staticmethod
    def _old_corpus_stems(v: str) -> list:
        r"""Every stem the PRE-7878 corpus is known to use for an id.

        The old corpus names a garment `c3/mesh/00<bodytype><garment6>.c3`
        where 7878 names it `c3/body/<bodytype><garment6>.c3` -- one leading
        `00`, and a different directory.  Worked end to end for
        `armor.ini [1181400]`, which declares `Mesh0=1137020`:

            7878  c3/body/7181400.c3      ABSENT
            6090  c3/mesh/001137020.c3    PRESENT, 75,634 bytes, 4 nodes

        MEASURED from the recovered name table (`out/wdf/c3_names.json`):
        `c3/mesh/` stems are 9 digits (551 -- `<bodytype:03d><garment:06d>`),
        6 digits (338 -- weapons) and 7 digits (63).  All four forms are
        generated and any hit counts.

        THE UNION IS NOT OVER-BROAD, and that is measured too, by the
        negative control in `tools/garment_missing_survey.py`: 2,000 random
        ids of each shape probe 2, 0 and 2 hits per 2,000 across the four old
        corpora -- a 0.05% background, so a hit is signal.  A candidate
        generator with no negative control is how a fallback starts handing
        back a plausible neighbour instead of the garment that was asked for.
        """
        if not v or not v.isdigit() or len(v) < 6:
            return []
        g = v[-6:]
        # AN ALL-ZERO GARMENT KEY IS DROPPED HERE TOO, AND ONLY THE DERIVED
        # FORMS ARE DROPPED.  `00<bodytype><g>` and bare `g` are both built out
        # of `g`; when `g` is `000000` they carry none of `v`, so every
        # `Mesh0` sharing a first digit collapses onto ONE stem.  Measured
        # after the same guard landed in `_garment_in_index`: 210 armor rows
        # stopped resolving to `c3/head/7000000.c3` and resolved to FOUR files
        # instead -- 134 to `c3/mesh/001000000.c3`, 73 to `002000000` -- which
        # is the identical defect one install further out.
        #
        # `v.zfill(9)` and `v` are the WHOLE id and still discriminate, so they
        # stay: a row whose art genuinely is named for the full 9-digit id is
        # unaffected.  This drops candidates, never adds them.
        derived = [("00" + v[0] + g) if len(v) >= 7 else None, g]
        if not g.strip("0"):
            derived = []
        out: list = []
        for s in [v.zfill(9)] + derived[:1] + [v] + derived[1:]:
            if s and s not in out:
                out.append(s)
        return out

    def _old_corpus_garment(self, asset_id: str, kind_dirs, ext: str):
        """`(Located, stem)` from a FALLBACK install, or None.

        Returns None immediately where no fallback was configured, which is
        every caller that has not opted in.

        The declared `Mesh0` is tried before the asked-for id here for the
        same reason `resolve_garment` prefers it above: the install states
        the answer, and `1181400` DECLARES `1137020`, which is the id the old
        corpus actually holds.  Deriving from `1181400` alone finds nothing.
        """
        fbs = self._fallbacks() if (self._fallback_spec) else []
        if not fbs:
            return None
        ids: list = []
        dm = self._declared_mesh(asset_id)
        if dm:
            ids.append(dm)
        if asset_id not in ids:
            ids.append(asset_id)
        for fb in fbs:
            label = Path(fb.root).name
            for sub in kind_dirs:
                for ident in ids:
                    for stem in self._old_corpus_stems(ident):
                        loc = fb.locate("c3/%s/%s%s" % (sub, stem, ext))
                        if loc is None:
                            continue
                        # The crossing is stamped into BOTH the structured
                        # field and the human-readable source string. A hit
                        # that came back looking like `[loose, N bytes]` --
                        # which is what an unwrapped `fb.locate` result says
                        # -- is indistinguishable from this install's own
                        # art, and that is the defect this whole function is
                        # written around.
                        out = Located(loc.logical, "%s:%s" % (label, loc.source),
                                      loc.real_path, loc.size,
                                      origin_root=Path(fb.root))
                        self.foreign_hits.append(
                            {"asked": asset_id, "declared": dm,
                             "logical": out.logical, "origin": str(fb.root),
                             "source": loc.source})
                        return out, stem
        return None

    def read_located(self, loc: Located) -> bytes:
        """Read `loc` FROM THE INSTALL IT CAME FROM.

        `read(loc.logical)` would re-resolve the path against THIS root, and
        for a foreign hit that means reading a different file or -- far more
        often -- raising `FileNotFoundError` for a file that plainly exists.
        A foreign `Located` whose origin is not among this root's fallbacks
        is refused by name rather than read from the wrong install.
        """
        if loc.origin_root is None:
            return self.read(loc.logical)
        # An OVERLAY origin, first: an overlay directory is not an install and
        # has no `AssetRoot`, so it must be read as a plain confined path.
        # `self.read` would in fact have worked here (an overlay wins in
        # `locate`), but only for this object -- hand the `Located` to a root
        # opened without that overlay and `read` silently returns THIS
        # INSTALL'S file, or none, for a path that plainly exists. The whole
        # reason this method exists is to refuse that, and it now refuses it
        # for both kinds of crossing rather than one.
        for ov in self.overlays:
            if Path(ov) == Path(loc.origin_root):
                return safepath.confine(ov, loc.logical).read_bytes()
        for fb in self._fallbacks():
            if Path(fb.root) == Path(loc.origin_root):
                return fb.read(loc.logical)
        raise FileNotFoundError(
            "%s came from %s, which is neither one of this AssetRoot's "
            "overlay directories (%s) nor one of its fallback installs (%s). "
            "Reading it through %s would read a DIFFERENT file or none at all."
            % (loc.logical, loc.origin_root,
               ", ".join(str(o) for o in self.overlays) or "none",
               ", ".join(str(f.root) for f in self._fallbacks()) or "none",
               self.root))

    def _declared_mesh(self, ident: str) -> Optional[str]:
        """The mesh id this install DECLARES for `ident`, or None.

        Read from the part tables, which already parse `Mesh<i>` into
        `PartRef.mesh` -- `resolve_appearance` has used them all along, and
        this function derived a key instead. Built once, because
        `part_tables()` re-parses on every call and this is reached per id.

        **Single-part appearances only.** A multi-part appearance has no one
        mesh, and taking `parts[0]` for those would be a guess wearing a
        measurement's clothes -- so they return None and fall through to the
        derived rule, which is exactly what answered them before.
        """
        if self._declared is None:
            m: dict[str, str] = {}
            try:
                tables = self.part_tables()
            except Exception:
                # An install with no RolePart.ini is not an error here; it just
                # declares nothing, and the derived rule stands alone.
                tables = {}
            for ini in tables.values():
                for app in ini:
                    if len(app.parts) == 1 and app.parts[0].mesh:
                        m.setdefault(app.ident, app.parts[0].mesh)
            self._declared = m
        for key in (ident, ident.zfill(9), ident.lstrip("0")):
            v = self._declared.get(key)
            if v:
                return v
        return None

    #: The c3 families a ROLE part may legitimately live in, for
    #: `resolve_declared`. `MESH_DIRS` + `MESH_DIRS_EXTRA` are the directories
    #: this module already probes; `mountsaddle` and `ghost` are the two the
    #: probe never had because they are two levels deep and cannot be probed
    #: for at all -- `c3/mountsaddle/801/8010001.c3`, `c3/ghost/098/100.c3`.
    #:
    #: **The set is a whitelist and it is load-bearing.** `core/wdb.py`'s own
    #: measurement is that the table merges several id spaces and the EFFECT
    #: space is dense from zero, so a lookup succeeding is not evidence the id
    #: was real. MEASURED here: on 6907 and 7205 two currently-unresolved
    #: appearance refs get a `c3/effect/...` row back from `c3.wdb`, and on
    #: 6609 one does. Those are the rows this set exists to refuse, and they
    #: are refused in the same run that accepts the mounts.
    ROLE_FAMILIES = (tuple(MESH_DIRS) + tuple(MESH_DIRS_EXTRA)
                     + ("mountsaddle", "ghost"))

    #: Below this width a `c3.wdb` hit is not evidence. `core/wdb.py` measured
    #: the false-positive rate against 4,000 random ids per width: 41% at 1-2
    #: digits, 26.7% at 3, 20.9% at 4, 13.1% at 5, and <=0.2% at 6 and up. No
    #: appearance table in the corpus declares a mesh id narrower than six.
    DECLARED_MIN_DIGITS = 6

    @property
    def resource_db(self):
        """The install's own `ini/c3.wdb`, or None.

        Read through `locate()` for the same reason `part_tables` is: an
        overlay or an archive is as legitimate a home for it as the loose
        tree, and a direct `self.root / "ini" / "c3.wdb"` join would read past
        an overlay that redirects everything else.

        None means "this install ships none, or the one it ships would not
        parse". The 5017/5065/5165 lineage ships none at all, which is why
        `resolve_declared` has to be additive rather than authoritative.
        """
        if self._resource_db is False:
            self._resource_db = None
            try:
                loc = self.locate("ini/c3.wdb")
                if loc is not None:
                    import wdb
                    tp = self._table_path(loc)
                    # MEASURED 2026-09-05: parsing c3.wdb is 47-49% of a COLD
                    # catalog build on 6609/7205 (1.5-2.7 s), and it is the same
                    # unchanged file every time. The pickle round-trip is ~5x
                    # cheaper. See `_wdb_disk_key` for why the parser is in the
                    # key; a stale entry here is a 21x row under-count that
                    # reads as a correct answer.
                    dk = _wdb_disk_key(tp)
                    cached = _wdb_disk_load(dk)
                    db = _wdb_from_state(cached) if cached is not None else None
                    if db is None:
                        db = wdb.ResourceDb(tp)
                        _wdb_disk_store(dk, db)
                    self._resource_db = db
            except (ValueError, struct.error, OSError):
                # Same narrowing as `PartIni`: a table that will not parse
                # degrades to "no declaration", an ImportError does not.
                self._resource_db = None
        return self._resource_db

    def resolve_declared(self, asset_id: str,
                         kind: str = "mesh") -> Optional[Located]:
        r"""The path the install's OWN `ini/c3.wdb` declares for `asset_id`.

        THE SECOND MECHANISM. `resolve_asset` derives a path from the id by
        directory convention -- `c3/<kind>/<id>.c3` -- and that convention
        describes exactly one of the two families of role art this client
        ships. The other family is **two levels deep with a filename that is
        not the id**, and no amount of probing reaches it:

            mount    8010000  -> c3/mount/801/8010000.c3
            saddle   8010012  -> c3/mountsaddle/801/8010012.c3
            monster 103000000 -> c3/monster/103/1.c3
            ghost   098000000 -> c3/ghost/098/100.c3

        None of those four shapes is derivable from the id, and all four are
        DECLARED, by name, in a table the client ships and `core/wdb.py` has
        parsed since 2026-08-09. This is the `armor.ini` `Mesh0=` lesson
        again (`docs/CORRECTIONS.md`): the declaration already existed and one
        resolver never asked for it.

        MEASURED, full enumeration of every declared mesh ref on 6907, no
        sampling. Two id classes resolved essentially ZERO times through the
        derived rule -- not a low rate:

            len07 leading 8   mounts           1 of 2,215 refs -> 2,215
            len08 leading 8   mounts           0 of    12     ->    12
            len09 non-0 lead  monster bodies   0 of   620     ->   562

        2,806 refs gained on that install, 0 lost, 0 changed. The 58 monster
        refs still missing are the `104000000` family, whose art 6907 does not
        ship at the path its own table names.

        THREE CHECKS, AND EACH ONE FIRES ON THE SHIPPED CORPUS:

        * **Width.** `DECLARED_MIN_DIGITS`; see there for the measured
          false-positive rate by width.
        * **Family.** `ROLE_FAMILIES`; refuses the `c3/effect/` rows, which
          occur.
        * **Existence.** The declared path must resolve through `locate()` in
          THIS install. `c3.wdb` is a shipped table and outlives the art it
          names: on 6907, 28 refs name a path the install does not ship.

        The kind check is the extension, and for a texture there are two
        spellings because the table only ever stores the MESH path. The
        directory is the mesh's, the stem is the id you asked for --
        `c3/mount/801/8014100.dds` beside `c3/mount/801/8010000.c3` -- and
        that is how a mount texture VARIANT is named. Falling back to the
        mesh's own stem is what a variant-less id wants.
        """
        for cand in self.declared_paths(asset_id, kind):
            loc = self.locate(cand)
            if loc is not None:
                return loc
        return None

    #: The install's SECOND id -> path declaration, in the same role as
    #: `c3.wdb`. See `object_db`.
    OBJ_TABLE = "ini/3dobj.ini"

    @property
    def object_db(self) -> dict:
        """The install's authoritative `3dobj` table as `{str(id): path}`.

        THE SECOND DECLARATION, AND FOUR INSTALLS HAVE ONLY THIS ONE.
        `resource_db` reads `ini/c3.wdb`; MEASURED over the shipped tree,
        **4274, 5017, 5065 and CCO-snapshot-2026-08-24 ship no `c3.wdb`
        at all**, so `resolve_declared` was a no-op on every one of them --
        not a low rate, an unreachable branch. All four ship
        `ini/3dobj.ini`, which is the same fact in the same shape:

        RE-MEASURED 2026-09-07 against every install `coroot.clients_dir()`
        holds, and this one SURVIVED: the set with no `c3.wdb` is exactly
        those four and the other 30 all ship it. The only edit is the
        DENOMINATOR -- it read "FIVE INSTALLS" and named `CCO` beside
        `CCO-snapshot-2026-08-24`, and `CCO` is a placeholder directory
        holding `.gitignore` and `.gitkeep`, refused as an install by
        `coroot.looks_like_root` and by `coroot.client_binaries` alike. It
        cannot lack a `c3.wdb` in any sense that means anything, and counting
        it made the claim's own denominator wrong by one while every named
        install in it was right. Audited in
        `docs/claim_enumeration_audit_2026-09-07.md`.

        AND THE SPLIT IS NOT CLEAN IN THE OTHER DIRECTION. The sentence below
        used to read "Four of those five ship `ini/3dobj.ini`", which invites
        reading the two declarations as complements. They are not: **Zephyr
        ships `c3.wdb` and NO `3dobj.ini`** -- the only install asymmetric
        that way, found while re-measuring this. So 30 installs ship `c3.wdb`,
        33 ship an `ini/3dobj.ini`, and 29 ship both.

            9990160  = c3/effect/lifestone/stone/1.c3
            98000000 = c3/ghost/098/100.c3

        This is the `armor.ini` `Mesh0=` lesson for the third time in this
        project (`docs/CORRECTIONS.md`, and `resolve_declared` above records
        the second): the declaration already existed, a parser for it already
        existed -- `core/dbc.py` has read the compiled form since 2026-08-09
        and `core/dbcshadow.py` pairs the two -- and one resolver never asked.

        **THE PLAINTEXT IS A DECOY WHERE A COMPILED TWIN EXISTS, so the twin
        is preferred.** That is `dbcshadow.check_ini`'s whole subject: 5517
        and 6090 ship `ini/3DObj.dbc` beside `ini/3dobj.ini` and the client
        reads the `.dbc`. Reading the plaintext there would not fail -- it
        would quietly answer out of a frozen table, which is the failure mode
        `WeaponMotion.ini` demonstrates at 1.7% agreement. `twin_rows`
        returns None when there is no twin or it will not parse, and only
        then is the plaintext read.

        None of the guards move: everything this returns still goes through
        `_declared_forms` (width, family) and `locate` (existence).

        **THE TWIN IS READ THROUGH `locate()`, NOT AS A FILESYSTEM SIBLING,
        and that distinction is not cosmetic.** `dbcshadow.compiled_twin`
        answers by listing the directory the ini really lives in, so it is
        used HERE only to learn the twin's NAME -- case included, because
        6090 pairs `3dobj.ini` with `3DObj.dbc` -- and the name is then
        located like any other table. Joining the sibling path directly would
        reproduce, exactly, the bug `part_tables` documents: an overlay would
        redirect every mesh this table names while the table itself was read
        straight past. MEASURED, and it is why this paragraph exists:
        `oracle_control.ini.appearance.obj` reported BLIND on 5517 the first
        time this ran, because the damaged twin in the overlay was never the
        file being read.
        """
        if self._object_db is False:
            self._object_db = self._load_id_path_table(self.OBJ_TABLE)
        return self._object_db

    #: The TEXTURE half of the same declaration: `Texture<i>=<id>` in an
    #: appearance table is an id in `ini/3dtexture.ini`, exactly as
    #: `Mesh<i>=<id>` is an id in `ini/3dobj.ini`.
    TEX_TABLE = "ini/3dtexture.ini"

    @property
    def texture_db(self) -> dict:
        """The install's `3dtexture` table as `{str(id): path}` -- the
        texture twin of `object_db`, read the same way (compiled `.dbc` twin
        preferred, through `locate()`).

        THE `armor.ini` `Mesh0=` LESSON, A FOURTH TIME: the declaration
        existed and one resolver never asked. `declared_paths(kind="texture")`
        only ever DERIVED a texture from the MESH table's path (the mesh's
        own directory, `.c3` -> `.dds`), so a garment whose texture file is
        not named after its id was unresolvable. MEASURED 2026-09-18 on CCO
        (the owner: "a lot of bodies/garments were missing"): 78 of the 3,418
        `armor.ini` rows with a Texture0 id reach their texture ONLY through
        this table -- e.g. `[004188490]` Texture0=004188490 ->
        `c3/texture/004188495.dds` -- and the Character Builder dropped every
        one of them."""
        if self._texture_db is False:
            self._texture_db = self._load_id_path_table(self.TEX_TABLE)
        return self._texture_db

    def _load_id_path_table(self, table: str) -> dict:
        """`{str(id): path}` from an id -> path table (`3dobj`, `3dtexture`):
        its compiled `.dbc` twin when one exists, else the plaintext. Shared so
        the two tables cannot be read by two rules that drift."""
        try:
            loc = self.locate(table)
            if loc is not None:
                p = self._table_path(loc)
                rows = None
                twin = dbcshadow.compiled_twin(p)
                if twin is not None:
                    # A TWIN THAT WILL NOT PARSE MEANS NO DECLARATION,
                    # NOT "READ THE DECOY". `dbcshadow.check_ini` RAISES
                    # on a shadowed ini rather than reading it, and the
                    # reason is `WeaponMotion.ini`: byte-identical on all
                    # five official clients while the twin has moved on,
                    # agreeing with it on 1.7% of rows. Falling back here
                    # would answer confidently out of a frozen table with
                    # nothing raised, which is worse than answering
                    # nothing -- the existence check does not save us,
                    # because a stale row can still name a shipped file.
                    # So the plaintext is read ONLY where it is the
                    # authoritative table, i.e. where there is no twin.
                    try:
                        # `dbcshadow` is imported at module scope; `dbc`
                        # is not, and stays lazy for the reason `PartIni`
                        # keeps it lazy -- see tools/build_addon.py
                        # VENDORED, which rewrites each lazy site by its
                        # exact literal and so needs this one spelled
                        # differently from that one.
                        import dbc                  # noqa: PLC0415
                        tloc = self.locate(
                            table.rsplit("/", 1)[0] + "/"
                            + twin.name)
                        if tloc is not None:
                            tp = self._table_path(tloc)
                            if dbcshadow.twin_magic(tp) == b"RSDB":
                                rows = {str(k): v for k, v in
                                        dbc.Rsdb.parse(
                                            tp.read_bytes()).paths.items()}
                    except Exception:               # noqa: BLE001
                        rows = None
                else:
                    rows = self._parse_id_path_ini(p)
                return {str(k): v for k, v in (rows or {}).items()}
        except (ValueError, struct.error, OSError):
            return {}
        return {}

    @staticmethod
    def _parse_id_path_ini(path) -> dict:
        """`{id: path}` from a flat `<id>=<path>` ini. Bytes, not text.

        Read as bytes and decoded latin-1 because some tables in this corpus
        are not valid UTF-8 and a decode error here would present as "this
        install declares nothing", which is the one answer that must not be
        manufactured. `setdefault` keeps the FIRST spelling of a repeated id,
        matching the order `c3.wdb` rows are read in.
        """
        out: dict = {}
        with open(path, "rb") as fh:
            data = fh.read()
        for line in data.split(b"\n"):
            line = line.strip()
            if not line or line[:1] in (b";", b"#", b"[") or b"=" not in line:
                continue
            k, v = line.split(b"=", 1)
            k = k.strip().decode("latin-1")
            v = v.strip().decode("latin-1")
            if k.isdigit() and v:
                out.setdefault(k, v)
        return out

    def _declared_forms(self, p: str, s: str, kind: str) -> list:
        """The guards, applied to ONE declared path. Shared by both sources.

        Split out so the width/family/spelling rules cannot drift between
        `c3.wdb` and `3dobj.ini`; every sentence of `resolve_declared`'s
        THREE CHECKS applies to both callers because it is this function.
        """
        if not p:
            return []
        p = p.replace("\\", "/")
        lp = p.lower()
        if not lp.startswith("c3/") or lp.count("/") < 2 or not lp.endswith(".c3"):
            return []
        if lp.split("/")[1] not in self.ROLE_FAMILIES:
            return []
        if kind != "texture":
            return [p]
        head = p.rsplit("/", 1)[0]
        return [f"{head}/{s}.dds", p[:-3] + ".dds"]

    def declared_paths(self, asset_id: str, kind: str = "mesh") -> list:
        """The path(s) this install's own tables name for `asset_id`,
        EXISTENCE UNCHECKED.

        Split out of `resolve_declared` because the two answers are different
        facts and a caller needs to tell them apart. An empty list means the
        client does not name this id at all -- we do not know where its art
        should be. A non-empty list whose paths all fail to `locate` means the
        client DOES name it and does not ship it, which is a statement about
        the corpus and not about any resolution rule.

        `routeb/oracle.py` is the reader: `ini.appearance` counts the second
        case as EXCLUDED rather than as a failure, on the same grounds
        `map.build` excludes a map whose bytes the install does not ship.

        TWO SOURCES, `c3.wdb` FIRST. The order is precedence and it is chosen
        so the change that added the second source cannot move an answer the
        first one already gave: where both name an id, the `c3.wdb` path is
        tried first and `resolve_declared` returns on the first that exists.
        MEASURED, and this is what makes the ordering more than a preference:
        on 5517/6090/6609/6907/7205/7632/7878 LOST is 0 and CORRECTED is 0.
        The whole of the gain is on installs that ship no `c3.wdb`.

        SCOPE, CORRECTED 2026-09-07. That sentence used to end "-- every
        install that has both --", and **29 installs have both**, not seven:
        5165, 5517, 6090, 6256, 6271, 6609, 6609.cn, 6652, 6680, 6707, 6716,
        6772, 6805, 6868, 6907, 6968, 7009, 7065, 7083, 7110, 7135, 7170,
        7182, 7189, 7205, 7632, 7682, 7867, 7878 (the four with only
        `3dobj.ini` are 4274, 5017, 5065 and CCO-snapshot-2026-08-24). The
        measurement stands for its seven and **the other 22 are UNMEASURED**;
        what was wrong was the universal quantifier, which turned a result
        about a quarter of the corpus into a statement about all of it. A
        quantifier is a claim about the DENOMINATOR and rots the moment a
        client is added, exactly like the RIBB parenthesis -- see
        `docs/claim_enumeration_audit_2026-09-07.md` and the `both-declarations`
        row of `tests/test_claim_enumeration.CLAIMS`, which now derives that
        29 at run time.
        """
        s = str(asset_id).strip()
        if not s.isdigit() or len(s.lstrip("0")) < self.DECLARED_MIN_DIGITS:
            return []
        out: list = []
        db = self.resource_db
        if db is not None:
            out.extend(self._declared_forms(db.path_for(s) or "", s, kind))
        obj = self.object_db
        if obj:
            # Both spellings, because the two tables disagree about leading
            # zeros: `c3.wdb` keys the padded id and `3dobj.ini` the bare one.
            for key in (s, s.lstrip("0")):
                p = obj.get(key)
                if p:
                    out.extend(self._declared_forms(p, s, kind))
                    break
        if kind == "texture":
            tex = self.texture_db
            if tex:
                for key in (s, s.lstrip("0"), s.zfill(9)):
                    tp = tex.get(key)
                    if tp:
                        tp = tp.replace("\\", "/")
                        lp = tp.lower()
                        # The shape guard for a TEXTURE row: a .dds under c3/.
                        # `_declared_forms` guards MESH rows (.c3, role family)
                        # and would refuse every one of these.
                        if lp.startswith("c3/") and lp.endswith(".dds") \
                                and lp.count("/") >= 2:
                            out.append(tp)
                        break
        seen = set()
        return [p for p in out if not (p in seen or seen.add(p))]

    def resolve_asset(self, asset_id: str, kind: str = "texture") -> Optional[Located]:
        if not asset_id or asset_id == "0":
            return None
        # A reference that is already a path (synthesised tables for old
        # clients name their meshes by full path -- bare IDs never contain a
        # slash) resolves directly, no directory probing.
        if "/" in asset_id or "\\" in asset_id:
            return self.locate(asset_id)
        tex = kind == "texture"
        base = self.TEX_DIRS if tex else self.MESH_DIRS
        extra = self.TEX_DIRS_EXTRA if tex else self.MESH_DIRS_EXTRA
        # THE EXTRA DIRECTORIES ARE ALWAYS SEARCHED NOW, JUST SEARCHED LAST.
        #
        # They used to be added ONLY when `_c3_names()` was knowable, and the
        # reason was cost: where the set is None every candidate is a real
        # filesystem stat, so the list was kept short. The cost is real and the
        # consequence was not measured -- on an un-enumerable root this never
        # looked in `armet/`, `head/`, `cape/`, `pelvis/`, `spirit/`, `misc/`
        # at all, and reported an asset that IS THERE as absent. Measured:
        #
        #     armet_dx8[5119040] texture '5119040'
        #        catalogue   c3/armet/5119040.dds   (source: data.wdf, exists)
        #        AssetRoot   None
        #
        # Which is exactly the thing `AnUnrecoveredNameIsNotAMissingFile` is
        # named for: a name this reader cannot recover is not a missing file.
        # `Catalog` has always searched base+extra unconditionally, so the two
        # readers disagreed about a file that exists.
        #
        # Ordering, not inclusion, is what keeps the cost bounded: base first,
        # so every id that resolved before resolves to the SAME file at the
        # same cost, and the extra dirs are only reached on a lookup that was
        # going to return None anyway. Precedence is unchanged.
        dirs = tuple(base) + tuple(extra)
        ext = ".dds" if kind == "texture" else ".c3"
        ids = []
        for cand in (asset_id, asset_id.zfill(9), asset_id.lstrip("0")):
            if cand and cand not in ids:
                ids.append(cand)
        # `locate` stats the filesystem, and this probes len(dirs)*len(ids)
        # candidates -- 39 on a modern client. Where the name set IS knowable,
        # a candidate that is in neither the archives nor the loose tree
        # cannot be found by `locate` either, so the stat is skipped. This is
        # a NECESSARY condition only: a candidate that survives it still goes
        # through `locate`, so precedence is unchanged and a WDF root (where
        # the set is None) probes exactly as it always did.
        known = self._c3_names()
        for sub in dirs:
            for i in ids:
                logical = f"c3/{sub}/{i}{ext}"
                if known is not None and logical not in known:
                    continue
                loc = self.locate(logical)
                if loc:
                    return loc
        # THE INSTALL'S OWN DECLARATION, and it goes HERE -- after every exact
        # probe, before the derived garment fallback.
        #
        # NOT EARLIER, and the number is why. `c3.wdb` and the directory probe
        # disagree about where an id lives on **10,416 refs of 6907's 41,905**
        # -- almost all of them `c3/mesh/<id>.c3` (what the probe finds first,
        # because `mesh` leads `MESH_DIRS`) against `c3/weapon/<id>.c3` (what
        # the table says). Both files ship. Putting the declaration first
        # would have "corrected" ten thousand answers that were never wrong,
        # which is the shape `map.build`'s control caught in item 1: the
        # damage was a valid target. Sitting below every exact probe, this
        # rule cannot touch any of them: LOST 0 against the exact probes holds
        # by construction, and MEASURED across 5017/5065/5517/6090/6609/6907/
        # 7205/7632/7878 the LOST column is 0 on all nine.
        #
        # NOT LATER, i.e. not after `resolve_garment`, and this one IS a
        # measured change rather than a free one. The garment fallback is
        # derived and may answer out of a FOREIGN install; a name the client
        # itself declares beats a name we inferred. The two both answer on
        # **28 mesh refs and 158 texture refs of 7878** with the profile on,
        # and putting the declaration first changes exactly those:
        #
        #   561000  mesh  c3/weapon/561020.c3 (a NEAR id, not this one)
        #                       -> c3/weapon/800205.c3   -- and `c3.wdb` on
        #                          6907, 7205 and 7878 all give 561020 its
        #                          own separate row, so the alias is deliberate
        #   144000000 tex c3/texture/104000000.dds, FOREIGN, out of 6090
        #                       -> c3/monster/144/1.dds, this install's own
        #
        # CLASSIFIED, all 186, none left over: 28 mesh refs where the base
        # answer was a different id's file, 158 texture refs where it was
        # foreign art. 7632 shares 7878's corpus with no profile and shows the
        # same 28 and no textures; on 5017, 5065, 5517, 6090, 6609, 6907 and
        # 7205 the CORRECTED column is 0. They are reported as CORRECTED, not
        # folded into gained.
        declared = self.resolve_declared(asset_id, kind)
        if declared is not None:
            return declared
        # LAST resort, after every exact probe above has missed, so a
        # client that resolves exactly keeps resolving to the same file.
        hit = self.resolve_garment(asset_id, dirs, ext)
        return hit[0] if hit else None

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
                mesh = self.resolve_asset(pr.mesh, "mesh")
                tex = self.resolve_asset(pr.texture, "texture")
                tex_id = pr.texture
                if tex is None and mesh is not None:
                    # From 7878 the texture is the MESH's own name with a
                    # .dds extension; the table's separate Texture0 id is
                    # legacy and resolves NOWHERE on that client -- 0 of
                    # 955 body and 0 of 1168 armet references exist under
                    # it, while the beside-the-mesh file exists for every
                    # body mesh that resolves at all (73 of 73).
                    stem = mesh.logical.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    sub = mesh.logical.split("/")[1]
                    tex = self.locate("c3/%s/%s.dds" % (sub, stem))
                    if tex is not None:
                        tex_id = stem
                parts.append({
                    "index": pr.index,
                    "mesh_id": pr.mesh,
                    "mesh": mesh,
                    "texture_id": tex_id,
                    "texture": tex,
                    "material": pr.material,
                })
            results.append({"part": part_name, "ini": ini.name,
                            "ident": app.ident, "parts": parts})
        return results

    def close(self) -> None:
        """Release every archive handle this root owns.  IDEMPOTENT.

        ONE FAILING CONTAINER MUST NOT ORPHAN THE REST.  The old loop let the
        first `close()` that raised abandon every handle after it, which turned
        one odd container into a leak of all the others -- and this is now
        called from an `ExitStack` in `qualifies_for`, where a throw would
        also strand the SECOND root the measurement opened.  So: close
        everything, remember the first error, and re-raise it once at the end.
        """
        first = None
        for a in list(self._archives.values()) + list(self._fallback_owned):
            try:
                a.close()
            except Exception as exc:                # noqa: BLE001
                if first is None:
                    first = exc
        # `_archives` IS LEFT POPULATED ON PURPOSE.  Emptying it would make a
        # resolve-after-close answer "not in any archive" instead of raising,
        # and a silent wrong answer is worse than a loud one: a closed mmap
        # raises `ValueError: mmap closed or invalid`, which names the bug.
        # Re-closing a closed mmap or file is a no-op, so this stays idempotent
        # without the clear.
        self._fallback_owned = []
        if first is not None:
            raise first

    def __enter__(self) -> "AssetRoot":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# item catalogue
# ---------------------------------------------------------------------------

#: Decoded item tables, keyed on the FILE THAT ANSWERED rather than on the
#: caller's argument. `Catalog` reaches `load_items` TWICE per open with two
#: different argument TYPES for the same install -- `BodyFacets` is handed the
#: `AssetRoot` and `BuilderIndex` is handed the `Path` -- so a key on the
#: argument would miss every time and cache nothing.
#:
#: MEASURED on 7878 (2026-09-03, cProfile by cumulative time): `load_items` cost
#: 14.35s across 2 calls in a 33.15s cold open, all of it the block96 decode.
#:
#: The key carries size and mtime, so a table edited on disk is re-read rather
#: than served stale -- the cache cannot outlive the fact it describes.
#:
#: **The cached list is SHARED, not copied.** 7878 returns 55,420 dicts and
#: duplicating them per call would give back the time this saves, so callers
#: must treat the result as READ-ONLY. Every caller in this tree reads it
#: (`items6`/`items5` store references; `uistate` and `bodyfacets` index it).
_ITEM_TABLE_CACHE: dict = {}


def _item_cache_lookup(p: Path):
    """`(rows_or_None, key_or_None)` for a resolved item table."""
    try:
        st = p.stat()
    except OSError:                                     # pragma: no cover
        return None, None
    key = (str(p), st.st_size, st.st_mtime_ns)
    return _ITEM_TABLE_CACHE.get(key), key


def _item_cache_store(key, rows):
    if key is not None:
        _ITEM_TABLE_CACHE[key] = rows
    return rows


def load_items(root: "Path | AssetRoot" = DEFAULT_ROOT) -> list[dict]:
    """The item table: id + name + stats, one dict per item.

    CCO ships it as plain JSON (ini/itemtype.json, 11142 items). Official
    clients carry the same data only as a TQ-cipher-encrypted
    ini/itemtype.dat (24,270 items in 6090), which ``tqdat`` decrypts and
    parses into rows keyed the same way, so both roots serve the same shape.
    Item names are labels over the appearance tables, not structure, so an
    install with neither table -- or a .dat some other seed encrypted --
    yields no rows rather than an exception. The character builder then
    offers every option unnamed instead of refusing to open.

    **`root` may be an `AssetRoot`**, and then the two lookups obey overlay ->
    loose -> archive precedence like every other asset; a plain path is joined
    directly, exactly as before. That is the only way a staged `itemtype`
    reaches the item names -- `Catalog` passes its `AssetRoot` down through
    `bodyfacets.BodyFacets`. The leniency above is unchanged: a table that is
    simply ABSENT still yields no rows. A table present but reachable only
    inside an archive raises, because "no rows" would report a table the
    install does ship as missing (see `coroot.locate_table`).

    **THE `.dat` CIPHER CHANGES AT 6907 AND THIS FUNCTION USED TO RETURN `[]`
    FOR EVERY CLIENT FROM THERE UP.** MEASURED 2026-08-30, before the block96
    branch below existed: 24,270 rows on 6090 and 6609, 33,639 on 6868, and
    **0** on 6907, 6968, 7009 and every later build -- because `tqdat` refuses
    them (rightly: they are not TQ-cipher files) and the refusal was the end of
    the road. `core/block96.py` reads that era through the 7878 ECB block
    dictionary, and this is where it reaches the item names.

    **The block96 rows are RECOVERED, not complete, and the shortfall is real:
    5,387 of 6907's 22,265 item rows.** That is the right trade for THIS
    caller and it is worth being explicit about why. `load_items` is a LOOKUP
    surface -- `uistate` maps an id to a name, `bodyfacets` groups ids into
    families -- and its documented behaviour for a missing table is already to
    leave options unnamed. So an id the dictionary could not recover behaves
    exactly like an id in a client with no table at all, which is the
    degradation this function was built for. A row that IS returned has its id
    and name straight out of the decode; `core/block96.recover_rows` truncates
    a damaged row rather than shifting it, so a field is either right or
    absent. Callers that need to know how much is missing ask
    `plugins/patch6907.py:recovery()`, which reports both numbers; a caller
    that needs a COUNT of the client's items must not take it from here.
    """
    p = coroot.locate_table(root, "ini/itemtype.json")
    if p is not None:
        hit, key = _item_cache_lookup(p)
        if key is None or hit is not None:
            return hit if hit is not None else json.loads(
                p.read_text("utf-8", errors="replace"))
        return _item_cache_store(
            key, json.loads(p.read_text("utf-8", errors="replace")))
    p = coroot.locate_table(root, "ini/itemtype.dat")
    if p is not None:
        hit, key = _item_cache_lookup(p)
        if hit is not None:
            return hit
        # The disk cache is consulted BEFORE `tqdat`, and the ordering below is
        # otherwise untouched. On a block96-era table `tqdat` reads the whole
        # 6.7 MB file and attempts a decrypt that CANNOT succeed -- measured at
        # 1.8s -- before refusing, and with a warm disk cache that refusal was
        # the entire remaining cost: 1.85s of which 1.80s was a decrypt whose
        # answer we already had. A disk entry exists only where a previous run
        # decoded this exact table with this exact dictionary, so consulting it
        # first cannot mis-route a TQ-cipher table: those never store one.
        _dk = _item_disk_key(p, root)
        _hit = _item_disk_load(_dk)
        if _hit is not None:
            return _item_cache_store(key, _hit)
        try:
            return _item_cache_store(key, tqdat.read_itemtype(p))
        except ValueError:
            # Not a TQ-cipher table. On 6907..7878 that is the block96 cipher
            # and not damage, so try the dictionary before giving up. The
            # order matters and is not arbitrary: `tqdat` gets first refusal
            # because it is the only one of the two that can be checked
            # against its own plaintext, and `block96.read_table` refuses any
            # file `inidat` does not place in the block96 family, so neither
            # reader can be handed the other's file.
            return _item_cache_store(key, _load_items_block96(root, p))
    return []


#: Where a decoded block96 item table is kept between runs. Repo-relative so a
#: read-only client install is never written to -- `ConquerAssets/Clients` is
#: baseline data and nothing here may touch it.
_ITEM_DISK_DIR = Path(__file__).resolve().parent.parent / "out" / "itemcache"


def _item_disk_key(src: Path, root) -> Optional[str]:
    """Identity of (this table, this dictionary), or None if either is unreadable.

    **THE DICTIONARY IS IN THE KEY AND THAT IS THE WHOLE SAFETY OF THIS CACHE.**
    The in-memory cache keys on the source table alone, which is correct for it:
    it dies with the process. This one SURVIVES restarts, and the dictionary is
    the other input to the decode.

    The 7878 dictionary is known to contain POISONED ENTRIES -- failed decrypts
    stored as plaintext, measured 2026-09-03 on four tables that decode at full
    coverage into 7.25-8.00 bits/byte where real plaintext runs 4-5 -- and a
    rebuild to remove them is in flight. Key on the source table alone and every
    launch after that rebuild would serve decoded-from-poisoned rows off disk,
    indefinitely, with the source file unchanged and nothing anywhere saying so.
    That is strictly worse than the 7 seconds this saves.

    Size and mtime rather than a content hash: the dictionary is 3.4M blocks and
    hashing it would cost more than the decode this avoids.
    """
    import block96 as _b96                                # noqa: PLC0415
    try:
        s = src.stat()
        # **`getattr(root, "root", root)` IS WRONG HERE and silently so**,
        # for the reason spelled out in `load_items` below: `pathlib.Path`
        # HAS a `.root` and it is the string `'\'`. Duck-type on `locate`.
        # This normalisation was MISSING when the disk cache was added, and
        # an AssetRoot reached `Path(root)` inside `dictionary_path` and
        # RAISED -- out of `_item_disk_key`, out of `load_items`, into
        # `coviewer`'s catch-all, which logged 'body facet classification
        # failed' and carried on with no facets at all. Both AssetRoot
        # callers (`bodyfacets`, `builder`) lost their entire item list to a
        # cache lookup that is supposed to be a pure optimisation.
        base = root.root if hasattr(root, "locate") else root
        dp = _b96.dictionary_path(base)
        if dp is None:
            return None
        d = Path(dp).stat()
    except OSError:
        return None
    raw = "|".join(str(x) for x in (
        src.resolve(), s.st_size, s.st_mtime_ns,
        Path(dp).resolve(), d.st_size, d.st_mtime_ns))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _item_disk_load(key: Optional[str]) -> Optional[list]:
    """Rows from a previous run, or None. A damaged cache is a MISS, never a raise."""
    if not key:
        return None
    p = _ITEM_DISK_DIR / (key + ".pkl")
    try:
        rows = pickle.loads(p.read_bytes())
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ValueError):
        return None                     # truncated, half-written, or from an
                                        # older layout: decode again rather than
                                        # hand a caller something shaped wrong.
    return rows if isinstance(rows, list) else None


def _item_disk_store(key: Optional[str], rows: list) -> list:
    """Write atomically. A failure to cache is never a failure to answer."""
    if key and rows:
        try:
            _ITEM_DISK_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _ITEM_DISK_DIR / (key + ".tmp")
            tmp.write_bytes(pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(tmp, _ITEM_DISK_DIR / (key + ".pkl"))
        except OSError:                                     # pragma: no cover
            pass
    return rows


#: `ini/c3.wdb` parsed once and kept between runs, in the same shape as the item
#: cache above -- and with the same keying lesson, which is the part that took
#: two tries there: **the PARSER is in the key, not just the file.**
#:
#: `core/wdb.py` produced 32,419 rows for 7205 on 2026-09-04 and 692,532 for the
#: same unchanged bytes on 2026-09-05, because four defects in the section walk
#: were fixed. Key on `c3.wdb` alone and every box that had run the old parser
#: once would serve its 32,419 rows forever, off an unchanged file, with nothing
#: anywhere saying so -- a 21x under-count that looks exactly like a correct
#: answer. The parser's own source is hashed in, so a parser change misses.
_WDB_DISK_DIR = Path(__file__).resolve().parent.parent / "out" / "wdbcache"

#: Entries kept. Each is 10-40 MB and there are 30 installs; without a cap a
#: box that opens them all leaves ~1.2 GB behind.
_WDB_DISK_KEEP = 8


def _wdb_module():
    """`core/wdb.py`, or None. THE ONLY `import wdb` in this cache.

    One import site rather than one per helper: `tools/build_addon.py` rewrites
    a repo import by replacing a LITERAL once, so two identical `import wdb`
    lines need two table entries and silently collide into one. The generator
    refused this file until they became one site, which is the guard working.
    """
    try:
        import wdb                                          # noqa: PLC0415
        return wdb
    except ImportError:                                     # pragma: no cover
        return None


def _wdb_parser_id() -> Optional[str]:
    """Content hash of the WHOLE parser, or None if it cannot be read.

    **`core/wdb.py` IS NOT THE PARSER ON ITS OWN, and hashing only it was a
    real hole rather than a theoretical one.** `wdb.py:190` gets every
    non-RSDB section's record shape from `dbc.section_span`, so the walk's
    behaviour is split across two files.

    MEASURED 2026-09-05, hours after this cache was written: Reverse
    Engineering decoded RSDC/FE32/MESZ/SIM6 entirely inside `core/dbc.py`
    (`director/re-rsdc`), taking 7878 from 2 of 34 sections and 70,930 rows to
    34 of 34 and **967,100**. `wdb.py` is untouched by that change. Keyed on
    `wdb.py` alone, every box that had opened 7878 once would keep serving
    70,930 rows afterwards -- a 13.6x under-count, off an unchanged file, with
    no error anywhere. That is the exact failure this key exists to prevent,
    and the first change to arrive walked straight through it.

    So: every repo module the parser transitively pulls in, bounded to
    `wdb.py`'s own directory -- stdlib and third-party are excluded, since a
    Python upgrade is not a parser change we can or should invalidate on.

    Deliberately the CONTENT and not the mtime: a checkout rewrites mtimes
    without changing behaviour, and needless misses cost a 3-second parse.
    """
    mod = _wdb_module()
    if mod is None:
        return None
    try:
        files = _wdb_parser_files(mod)
        if not files:                        # pragma: no cover
            return None
        h = hashlib.sha256()
        for q in files:
            h.update(q.name.encode("utf-8"))
            h.update(hashlib.sha256(q.read_bytes()).digest())
        return h.hexdigest()[:16]
    except (OSError, AttributeError, TypeError):
        return None


def _wdb_parser_files(mod) -> list:
    """Every repo module the parse depends on, sorted. Its own function so a
    test can assert `dbc.py` is in it rather than trusting a hash to notice."""
    try:
        base = Path(mod.__file__).resolve().parent
    except (AttributeError, TypeError, OSError):        # pragma: no cover
        return []
    seen, queue, files = set(), [mod], set()
    while queue:                            # transitive, within core/ only
        m = queue.pop()
        if id(m) in seen:
            continue
        seen.add(id(m))
        f = getattr(m, "__file__", None)
        if not f:
            continue
        try:
            q = Path(f).resolve()
        except (OSError, ValueError):        # pragma: no cover
            continue
        if q.parent != base:
            continue                         # stdlib and third-party: not ours
        files.add(q)
        queue.extend(v for v in vars(m).values()
                     if getattr(v, "__file__", None))
    return sorted(files)


def _wdb_disk_key(src: Path) -> Optional[str]:
    """Identity of (this c3.wdb, this parser), or None if either is unreadable."""
    try:
        pid = _wdb_parser_id()
        if pid is None:
            return None
        # The FILE'S CONTENT, not its size and mtime. The item cache keys the
        # block96 dictionary on size+mtime because that pickle is 103 MB and
        # hashing it would cost more than the decode it saves; c3.wdb is 3-12 MB
        # and MEASURED 2026-09-05 at 0.003-0.010 s to hash, against a 0.7-2.9 s
        # parse -- 0.3% of the cost it protects. Size and mtime cannot see a
        # same-length in-place rewrite inside one clock tick, and that is a
        # stale-index class this cache does not need to carry for 3 ms. The
        # test for it failed intermittently against the size+mtime key, which
        # is how the gap was found.
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        raw = "|".join((str(src.resolve()), digest, pid))
    except OSError:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _wdb_disk_load(key: Optional[str]):
    """A previous run's parsed state, or None. A damaged cache is a MISS.

    Validated on the way in: a state without `by_id`/`by_path` would produce a
    ResourceDb whose methods raise later, somewhere with no context.
    """
    if not key:
        return None
    try:
        state = pickle.loads((_WDB_DISK_DIR / (key + ".pkl")).read_bytes())
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError,
            ValueError, ImportError):
        return None
    if not isinstance(state, dict):
        return None
    if not isinstance(state.get("by_id"), dict) or        not isinstance(state.get("by_path"), dict):
        return None
    return state


def _wdb_disk_store(key: Optional[str], db) -> None:
    """Write atomically, then prune. Failing to cache is never failing to answer."""
    state = getattr(db, "__dict__", None)
    if not key or not state or not state.get("by_id"):
        return                      # an empty parse is not worth making sticky
    try:
        _WDB_DISK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _WDB_DISK_DIR / (key + ".tmp")
        tmp.write_bytes(pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL))
        os.replace(tmp, _WDB_DISK_DIR / (key + ".pkl"))
        kept = sorted(_WDB_DISK_DIR.glob("*.pkl"),
                      key=lambda q: q.stat().st_mtime, reverse=True)
        for stale in kept[_WDB_DISK_KEEP:]:
            try:
                stale.unlink()
            except OSError:                                 # pragma: no cover
                pass
    except OSError:                                         # pragma: no cover
        pass


def _wdb_from_state(state):
    """Rebuild a `ResourceDb` without re-parsing, or None."""
    mod = _wdb_module()
    if mod is None:                                         # pragma: no cover
        return None
    try:
        db = object.__new__(mod.ResourceDb)
        db.__dict__.update(state)
        return db
    except (AttributeError, TypeError):                     # pragma: no cover
        return None


def _load_items_block96(root, path) -> list[dict]:
    """`ini/itemtype.dat` rows for the 6907..7878 cipher era, or `[]`.

    **Positional, and only three columns are named.** 6907's rows carry 64
    fields and 7878's carry 68; `tqdat.FIELDS_AT` names 59 and diverges before
    it runs out, putting `elemResEarth` on the column that actually holds the
    item class. Both plugins measured that column independently -- 6907 sees
    `Gift`/`QuestItem`/`GiftPack` at index 52, 7878 sees `Garment` there -- so
    the index is an era fact and lives in `block96.ITEM_COLUMNS`. The rest are
    left out rather than mislabelled.

    `fields` travels on every row: it is how many columns survived the decode,
    so a caller can tell a whole row from a truncated one without guessing
    from which keys happen to be present.
    """
    # MEASURED 2026-09-03 on 7878: this decode is 7.05s and 65% of a cold open,
    # while reading the same 55,420 rows back from disk is 0.05s -- 155x, 2.7 MB.
    # It is the largest single cost in a launch and it produces the same answer
    # every time, so it is kept between runs. See `_item_disk_key` for why the
    # dictionary is part of the identity.
    _dk = _item_disk_key(Path(path), root)
    _hit = _item_disk_load(_dk)
    if _hit is not None:
        return _hit
    import block96                                        # noqa: PLC0415
    # **`getattr(root, "root", root)` IS WRONG HERE and silently so.**
    # `pathlib.Path` HAS a `.root` -- it is the string `'\\'` -- so the usual
    # AssetRoot-or-Path idiom hands the dictionary lookup a bare separator and
    # every client reports "no dictionary on this box". Measured: 0 rows on
    # 6907 with the reader working perfectly one call below. Duck-typed on
    # `locate` instead, which is the same discriminator `coroot.locate_table`
    # uses and which `Path` does not answer to.
    base = root.root if hasattr(root, "locate") else root
    d = block96.load_dictionary(base)
    if d is None:
        return []
    table = block96.read_table(path, d, "rows", b"@@")
    if not table.ok:
        return []
    i_id = block96.ITEM_COLUMNS["id"]
    i_name = block96.ITEM_COLUMNS["name"]
    i_cls = block96.ITEM_COLUMNS["itemClass"]
    out: list[dict] = []
    for line in table.text.split(b"\n"):
        if not line.strip():
            continue
        f = line.split(b"@@")
        if f and f[-1] == b"":
            f = f[:-1]
        if len(f) <= max(i_id, i_name):
            continue
        ident = f[i_id].decode("latin-1")
        row: dict = {"id": int(ident) if ident.isdigit() else ident,
                     "name": f[i_name].decode("latin-1"),
                     "fields": len(f)}
        if len(f) > i_cls:
            row["itemClass"] = f[i_cls].decode("latin-1")
        out.append(row)
    return _item_disk_store(_dk, out)


def find_items(name_substr: str, root: "Path | AssetRoot" = DEFAULT_ROOT) -> list[dict]:
    q = name_substr.lower()
    return [i for i in load_items(root) if q in str(i.get("name", "")).lower()]


# BASELINE RE-SCOPE, 2026-09-19. The owner removed 6609.cn, CCO and Installers
# from core/baseline_members.json -- "remove the three from baseline members,
# then land the windows" -- so the baseline is 33 members. The dated figures
# above were measured over the earlier population and stand as records of that
# measurement. Over the 33, as re-derived by tests/test_claim_enumeration.py:
#   29 installs ship `ini/c3.wdb`; the four that ship no `c3.wdb` are unchanged
#   **28 installs have both** `ini/c3.wdb` and `ini/3dobj.ini`
#   28 of the 33 installs ship the frozen `ini/armor.ini`
#   CAME is on ALL 33 installs, 77,086 instances
#
# BASELINE RE-SCOPE (2), 2026-09-19. Later the same day the owner deleted 6716
# and 7682 (byte-level copies of 6271 and 7632), renamed 7632 to 7622 (its
# build stamp), and declared 7217 7250 7275 7280 7320 7336 7373 7387 7506 7535
# 7562 7589 baseline members, so core/baseline_members.json holds 43. The
# 33-member block above, and the dated figures before it, stand as records of
# those measurements. Over the 43, as held by tests/test_claim_enumeration.py:
#   39 installs ship `ini/c3.wdb`; the four that ship no `c3.wdb` are still unchanged
#   **38 installs have both** `ini/c3.wdb` and `ini/3dobj.ini`
#   38 of the 43 installs ship the frozen `ini/armor.ini`
# The C3 chunk-tag populations below were RE-WALKED, not carried: the same
# scratchpad/c3census_all.py walk (loose .c3 plus every archive entry by MAXF
# magic) run on the twelve new members and on 7622, whose full tag table came
# back identical to the one recorded for 7632 (57,381 files, 588,921 chunks).
#   CAME is on ALL 43 installs, 89,458 instances
#   MNEW: 508,062 instances on 28 installs; onset still 6907
#   CCFL: 888,036 instances on 24 installs; onset still 7083
#   RIBB: still 140 on exactly two installs, 7867 69 and 7878 71
#   RMOT: still 140 on exactly two installs, 7867 69 and 7878 71
#   OMNI: still TWO instances, on TWO installs (4274, CCO-snapshot-2026-08-24)
#   PHY2: still ZERO on every one of the 43
# The twelve new members' per-install counts are in the registry; one shape
# worth knowing: CAME falls from 2,620 on 7275 to 686 on 7280, so the drop
# that 7632 showed happens between those two patches, not at 7622.
