#!/usr/bin/env python3
r"""
colibrary.py -- per-server views over the COmmunity Library.

The library (built by ``tools/assetdiff.py``) stores only what is *unique*
relative to the baseline install: a file byte-identical to any baseline
content was skipped, so neither the library nor the baseline alone holds a
community client's complete asset namespace.  What preserves it is the
**server profile** the differ writes alongside the extraction:

    <library>/servers/<name>/
        profile.json    where the client came from, version, counts
        filemap.json    every logical path the client ships -> where the
                        bytes now live (library file, baseline path, or
                        baseline archive entry by hash)
        ini/            verbatim snapshot of the client's ini/ directory --
                        the linkage database: RolePart.ini, the appearance
                        tables (weapon.ini, mount.ini, ...) and the motion
                        tables (*motion.ini)

``ServerView`` composes those three with the baseline install and exposes the
same interface as ``coassets.AssetRoot``, so everything built on AssetRoot --
``resolve_appearance``, ``resolve_asset``, the viewer's catalogue -- resolves
mesh/texture/animation linkages **exactly as that server's client would**,
using the server's own tables, not the baseline's.

filemap entry forms (value is a list; first element is the kind):

    ["l", "<path under the library>",  "<source archive in the client>"]
    ["b", "<baseline logical path>",   "<source archive in the client>"]
    ["w", "<baseline .wdf>", "<hex name-hash>", "<source archive>"]
    ["e", "<baseline logical path>",   "<source archive in the client>"]

"e" is written by tools/deepdedup.py: the client's bytes differed from the
baseline's but decoded to identical pixels/geometry, so the library file was
removed and the path serves the baseline's (visually identical) bytes.

"b" targets can differ from the key: a client file whose bytes exist in the
baseline under a *different* path is recorded against that path, which is why
the plain library tree cannot answer these lookups but the filemap can.

Usage:
    from colibrary import ServerView, list_servers
    with ServerView("D:/COmmunity Library", "zephyr") as v:
        v.resolve_appearance("410000")     # the server's weapon.ini, not ours
        v.read("c3/mesh/410000.c3")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from coassets import AssetRoot, Located, PartIni, parse_ini
from tqhash import tq_hash


def list_servers(library: Path | str) -> list[str]:
    d = Path(library) / "servers"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if (p / "filemap.json").is_file())


def library_info(path: Path | str) -> dict:
    """Is ``path`` a usable COmmunity Library, and what is in it?

    Validated by *contents* -- a directory holding ``servers/<name>/
    filemap.json`` -- for the same reason ``coroot`` validates the install
    root that way: a path that merely looks right proves nothing.
    """
    p = Path(path)
    out: dict = {"path": str(p), "exists": p.is_dir(), "servers": [],
                 "ok": False, "why": ""}
    if not out["exists"]:
        out["why"] = "no such directory"
        return out
    names = list_servers(p)
    for n in names:
        prof: dict = {}
        pf = p / "servers" / n / "profile.json"
        try:
            if pf.is_file():
                prof = json.loads(pf.read_text("utf-8"))
        except (OSError, ValueError):
            prof = {}
        out["servers"].append({
            "name": n,
            "tag": server_tag(n, prof),
            "files": prof.get("files"),
            "clientVersion": prof.get("clientVersion"),
            "importedAt": prof.get("importedAt"),
            "client": prof.get("client"),
        })
    out["ok"] = bool(names)
    if not names:
        out["why"] = ("no servers/<name>/filemap.json here -- import a client "
                      "first with tools/assetdiff.py --server NAME")
    return out


def server_tag(name: str, profile: Optional[dict] = None) -> str:
    """The human label for a server's assets, e.g. ``zephyr`` -> ``Zephyr``.

    A profile may name it explicitly (``"tag"``); otherwise the directory
    name is title-cased, which is what an imported client is usually called.
    """
    if profile:
        t = str(profile.get("tag") or "").strip()
        if t:
            return t
    return name[:1].upper() + name[1:] if name else name


#: Where a library plausibly sits.  Used only to *suggest* folders in the UI;
#: every candidate is still validated by `library_info`.
def discover_libraries(extra: Optional[list[Path]] = None) -> list[dict]:
    """Valid libraries found in the usual places, best-known first.

    Deliberately shallow and bounded: a handful of parents, one level of
    children each.  Nothing here walks a whole drive -- a folder chooser
    that hangs is worse than one that asks you to paste a path.
    """
    seen: set[str] = set()
    out: list[dict] = []

    def consider(p: Path) -> None:
        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        info = library_info(p)
        if info["ok"]:
            out.append(info)

    home = Path.home()
    repo = Path(__file__).resolve().parent.parent
    parents: list[Path] = [repo.parent, home, home / "Documents",
                           home / "Desktop", home / "Claude"]
    if extra:
        parents = list(extra) + parents
    for base in parents:
        try:
            if not base.is_dir():
                continue
            consider(base)
            for child in base.iterdir():
                if child.is_dir():
                    consider(child)
        except OSError:
            continue
    return out


class ServerView(AssetRoot):
    """The asset namespace of one imported community client.

    Resolution order is the filemap's verdict, not a search: every logical
    path the client shipped has exactly one record saying where its bytes
    are.  Paths outside the filemap fall back to the library tree and then
    the baseline, so probing helpers (``resolve_asset`` zero-pad guesses)
    still work.
    """

    def __init__(self, library: Path | str, server: str,
                 root: Path | str | None = None):
        super().__init__(root)                       # validates the baseline
        self.library = Path(library)
        self.server = server
        base = self.library / "servers" / server
        if not (base / "filemap.json").is_file():
            have = ", ".join(list_servers(self.library)) or "none"
            raise FileNotFoundError(
                f"no server profile {server!r} under {self.library / 'servers'}"
                f" (have: {have})")
        self.profile: dict = {}
        p = base / "profile.json"
        if p.is_file():
            self.profile = json.loads(p.read_text("utf-8"))
        raw = json.loads((base / "filemap.json").read_text("utf-8"))
        self.filemap: dict[str, list] = raw["files"] if "files" in raw else raw
        self.tables_dir = base
        #: Human label for assets this server does not share with the baseline,
        #: e.g. "Zephyr".  Shown as a tag in the UI and filterable there.
        self.tag = server_tag(server, self.profile)

    def unique_paths(self) -> set[str]:
        """Paths whose bytes exist only in the library -- this server's own
        art.  A "b"/"w" entry is content the baseline already ships, however
        the client happened to package it, so it is not unique to the server.
        """
        return {k for k, ref in self.filemap.items() if ref and ref[0] == "l"}

    def group_tags(self) -> dict[str, str]:
        """logical -> extra tag, for assets filed under ``<Server>/<Group>/``.

        ``tools/garmentextract.py`` writes recovered archive contents to
        ``<library>/Zephyr/Garments4/...``; the folder is the only name those
        assets have, so it becomes a tag and the archive stays browsable as
        a unit.
        """
        out: dict[str, str] = {}
        me = self.server.lower()
        for k, ref in self.filemap.items():
            if ref and ref[0] == "l":
                parts = ref[1].split("/")
                if len(parts) >= 3 and parts[0].lower() == me:
                    out[k] = parts[1]
        return out

    def is_unique(self, logical: str) -> bool:
        ref = self.filemap.get(self._norm(logical))
        return bool(ref) and ref[0] == "l"

    # -- namespace ---------------------------------------------------------

    def logical_paths(self) -> list[str]:
        """Every logical path this server's client ships, sorted."""
        return sorted(self.filemap)

    def source_of(self, logical: str) -> Optional[str]:
        """Which archive/source in the *client* carried this path."""
        ref = self.filemap.get(self._norm(logical))
        return ref[-1] if ref else None

    @staticmethod
    def _norm(logical: str) -> str:
        return logical.replace("\\", "/").lstrip("/").lower()

    # -- resolution --------------------------------------------------------

    def locate(self, logical: str) -> Optional[Located]:
        key = self._norm(logical)
        ref = self.filemap.get(key)
        if ref is None:
            # not something the client shipped: try the library tree (covers
            # probing with un-normalised IDs) and then the baseline.
            p = self.library / "assets" / key
            if p.is_file():
                return Located(key, "library", p, p.stat().st_size)
            return super().locate(logical)
        kind = ref[0]
        if kind == "l":
            p = self.library / ref[1]
            if p.is_file():
                return Located(key, "library", p, p.stat().st_size)
            return None                              # library tree incomplete
        if kind in ("b", "e"):
            loc = super().locate(ref[1])
            if loc is None:
                return None
            # keep the *requested* path as the identity; the baseline path is
            # a storage detail.
            return Located(key, loc.source, loc.real_path, loc.size)
        if kind == "w":
            arc = self._archives.get(ref[1])
            if arc is None:
                return None
            e = arc.get(int(ref[2], 16))
            if e is None:
                return None
            return Located(key, ref[1], None, e.size)
        return None

    def read(self, logical: str) -> bytes:
        key = self._norm(logical)
        ref = self.filemap.get(key)
        if ref is None:
            p = self.library / "assets" / key
            if p.is_file():
                return p.read_bytes()
            return super().read(logical)
        kind = ref[0]
        if kind == "l":
            return (self.library / ref[1]).read_bytes()
        if kind in ("b", "e"):
            return super().read(ref[1])
        if kind == "w":
            return self._archives[ref[1]].read_by_hash(int(ref[2], 16))
        raise FileNotFoundError(logical)

    # -- linkage tables ----------------------------------------------------

    def part_tables(self) -> dict[str, PartIni]:
        """The *server's* appearance tables, from the profile's ini snapshot.

        Same contract as AssetRoot.part_tables(): every part RolePart.ini
        names whose table actually exists.  A table the client resolves by
        convention instead of a file (old-engine body armour) is simply
        absent, exactly as it is absent from the client's own disk.
        """
        rp = self.tables_dir / "ini" / "RolePart.ini"
        if not rp.is_file():
            return super().part_tables()
        cfg = parse_ini(rp)
        out: dict[str, PartIni] = {}
        conf = cfg.get("Config", {})
        n = int(conf.get("Count", "0") or 0)
        for i in range(n):
            part = conf.get(f"Part{i}")
            rel = conf.get(f"MeshIni{i}")
            if not part or not rel:
                continue
            p = self.tables_dir / Path(rel.replace("\\", "/"))
            if p.is_file() and rel not in {t.path.name for t in out.values()}:
                try:
                    out[part] = PartIni(p)
                except Exception:
                    pass
        return out

    # -- old-client conventions ---------------------------------------------

    def synthesize_body_table(self) -> str:
        r"""An armor.ini for a client that ships none, from its directory
        convention.

        Old-generation clients resolve bodies without a table:
        ``c3/<bodyType>/<look>/<action>.c3`` is the animated model (action
        100 = stand) and ``c3/texture/<look><variant>.dds`` its colourways.
        Both halves are observable in the filemap, so the table can be
        *derived* -- one ``[TTTlllvvv]`` section per body type + look +
        variant, in the exact format PartIni already parses.  Returns the
        ini text ("" when the convention is absent).
        """
        import re
        bodies: dict[tuple[str, str], dict[int, str]] = {}
        textures: dict[str, list[str]] = {}
        for k in self.filemap:
            m = re.fullmatch(r"c3/(\d{4})/(\d{3})/(\d+)\.c3", k)
            if m:
                bodies.setdefault((m.group(1), m.group(2)), {})[
                    int(m.group(3))] = k
                continue
            m = re.fullmatch(r"c3/texture/(\d{3})(\d{3})\.dds", k)
            if m:
                textures.setdefault(m.group(1), []).append(k)
        if not bodies:
            return ""
        lines = [
            "; SYNTHESISED by core/colibrary.py -- this client ships no",
            "; armor.ini; bodies resolve by the c3/<type>/<look>/<action>.c3",
            "; convention.  One section per body type + look + texture",
            "; variant, stand action (100) as the mesh.  Regenerate with:",
            ";   py -3 tools/colibrary.py rebuild-tables <server>",
            ""]
        n = 0
        for (btype, look), actions in sorted(bodies.items()):
            if btype == "0000":
                continue
            mesh = actions.get(100) or actions[min(actions)]
            texs = sorted(textures.get(look, []))
            variants = ([(t[-7:-4], t) for t in texs]
                        if texs else [("000", "")])
            for var, tex in variants:
                ident = f"{int(btype):03d}{look}{var}"
                lines += [f"[{ident}]", "Part=1", f"Mesh0={mesh}",
                          f"Texture0={tex or '0'}", "MixTex0=0", "MixOpt0=0",
                          "Asb0=5", "Adb0=6", "Material0=default", ""]
                n += 1
        return "\n".join(lines) if n else ""

    def synthesize_mount_table(self) -> str:
        r"""Mount sections from the old-client convention:
        ``c3/mount/<look>/<look>0000.c3`` is the mesh, and every
        ``c3/mount/<look>/<look>NN00.dds`` a colourway.  Ident = the texture
        stem, so the numbering the client uses is the numbering shown."""
        import re
        meshes: dict[str, str] = {}
        texs: dict[str, list[tuple[str, str]]] = {}
        for k in self.filemap:
            m = re.fullmatch(r"c3/mount/(\d+)/\1(\d+)\.(c3|dds)", k)
            if not m:
                continue
            look, tail, ext = m.groups()
            if ext == "c3":
                # the all-zero tail is the mesh; anything else would be a
                # variant mesh we have not observed.
                if set(tail) == {"0"} or look not in meshes:
                    meshes.setdefault(look, k)
            else:
                texs.setdefault(look, []).append((look + tail, k))
        lines = []
        for look in sorted(meshes):
            for ident, tex in sorted(texs.get(look, [])):
                lines += [f"[{ident}]", "Part=1", f"Mesh0={meshes[look]}",
                          f"Texture0={tex}", "MixTex0=0", "MixOpt0=0",
                          "Asb0=5", "Adb0=6", "Material0=default", ""]
        return "\n".join(lines) if lines else ""

    def write_synthesized_tables(self) -> list[str]:
        """Write derived tables into the profile snapshot where the client
        ships none.  A missing table is created; a table the client does ship
        is only ever *appended to* (below a marker), never rewritten.
        Returns the files written.
        """
        wrote = []
        armor = self.tables_dir / "ini" / "armor.ini"
        if not armor.is_file():
            text = self.synthesize_body_table()
            if text:
                armor.parent.mkdir(parents=True, exist_ok=True)
                armor.write_text(text, "utf-8")
                wrote.append(str(armor))
        marker = "; SYNTHESISED-MOUNTS by core/colibrary.py"
        mount = self.tables_dir / "ini" / "mount.ini"
        for cand in (self.tables_dir / "ini").glob("*.ini") \
                if (self.tables_dir / "ini").is_dir() else []:
            if cand.name.lower() == "mount.ini":
                mount = cand
                break
        existing = mount.read_text("latin-1") if mount.is_file() else ""
        if marker not in existing:
            text = self.synthesize_mount_table()
            if text:
                mount.parent.mkdir(parents=True, exist_ok=True)
                mount.write_text(
                    existing.rstrip() + ("\n\n" if existing.strip() else "")
                    + marker + " -- convention-derived sections follow;\n"
                    "; regenerate: py -3 tools/colibrary.py rebuild-tables "
                    "<server>\n\n" + text, "latin-1")
                wrote.append(str(mount))
        return wrote

    def parse_gamemap_dat(self) -> list[dict]:
        r"""The old client's binary map registry, as GameMap.json-shaped rows.

        ``ini/GameMap.dat`` layout (VERIFIED against the Zephyr snapshot --
        337 rows parse to exactly EOF):

            u32 rowCount
            per row: u32 documentId, u32 pathLen, char path[pathLen],
                     u32 puzzleGridSize

        Paths name the shipped ``.7z``; the materialized tree stores the
        decompressed ``.DMap``, so the suffix is rewritten to match.
        """
        import struct
        p = self.tables_dir / "ini" / "GameMap.dat"
        if not p.is_file():
            return []
        d = p.read_bytes()
        try:
            (count,) = struct.unpack_from("<I", d, 0)
            off = 4
            rows = []
            for _ in range(count):
                doc_id, plen = struct.unpack_from("<II", d, off)
                off += 8
                path = d[off:off + plen].decode("latin-1")
                off += plen
                (grid,) = struct.unpack_from("<I", d, off)
                off += 4
                fn = path.replace("\\", "/")
                if fn.lower().endswith(".7z"):
                    fn = fn[:-3] + ".DMap"
                rows.append({"DocumentId": doc_id, "FileName": fn,
                             "PuzzleGridSize": grid})
            if off != len(d):
                # a layout drift would silently mis-scope every later row
                raise ValueError(f"{off} != {len(d)} bytes consumed")
            return rows
        except (struct.error, ValueError):
            return []

    def materialize_maproot(self, dest: Path | str,
                            log=lambda s: None) -> dict:
        r"""A real directory tree of this server's ``map/`` data, for tools
        that read placement files from disk (the MapEditor's PuzzleLibrary /
        SceneLibrary walk directories; they cannot read through a view).

        ``map/map/*.7z`` archives -- the old client's compressed DMaps, one
        ``.DMap`` inside each -- are decompressed on the way out, so the tree
        looks exactly like a modern install's.  Art is NOT copied: everything
        that reads art already goes through the view.  Idempotent: files are
        re-written only when missing.
        """
        import subprocess
        dest = Path(dest)
        stats = {"copied": 0, "extracted": 0, "kept": 0, "failed": 0}
        seven = None
        for cand in (r"C:\Program Files\7-Zip\7z.exe",
                     r"C:\Program Files (x86)\7-Zip\7z.exe", "7z"):
            try:
                subprocess.run([cand], capture_output=True, timeout=10)
                seven = cand
                break
            except OSError:
                continue
        for logical in self.filemap:
            # map data plus the ani/ placement scripts (MapScene.ani and kin)
            # that scene rendering parses from disk.
            if not (logical.startswith("map/")
                    or (logical.startswith("ani/")
                        and logical.endswith(".ani"))):
                continue
            out = dest / Path(*[p for p in logical.split("/")
                                if p not in ("", ".", "..")])
            if logical.endswith(".7z"):
                out = out.with_suffix(".DMap")
            if out.is_file():
                stats["kept"] += 1
                continue
            try:
                data = self.read(logical)
            except (FileNotFoundError, KeyError):
                stats["failed"] += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            if logical.endswith(".7z"):
                if seven is None:
                    stats["failed"] += 1
                    log(f"no 7z.exe -- cannot extract {logical}")
                    continue
                tmp = out.with_suffix(".7z.tmp")
                tmp.write_bytes(data)
                r = subprocess.run(
                    [seven, "e", "-y", str(tmp), f"-o{out.parent}"],
                    capture_output=True, text=True)
                tmp.unlink(missing_ok=True)
                if r.returncode != 0:
                    stats["failed"] += 1
                    log(f"7z failed on {logical}: {r.stderr[:120]}")
                    continue
                # single inner file, but its case/name can differ: normalise
                got = [p for p in out.parent.glob("*")
                       if p.suffix.lower() == ".dmap"
                       and p.stem.lower() == out.stem.lower()]
                if got and got[0] != out and not out.exists():
                    got[0].rename(out)
                stats["extracted"] += 1
            else:
                out.write_bytes(data)
                stats["copied"] += 1
        # The map registry: PuzzleGridSize (how the ground art is tiled)
        # only exists here, so without it every map reads as art-less.
        gm = dest / "ini" / "GameMap.json"
        if not gm.is_file():
            rows = self.parse_gamemap_dat()
            if rows:
                gm.parent.mkdir(parents=True, exist_ok=True)
                gm.write_text(json.dumps(rows, indent=1), "utf-8")
                stats["gamemapRows"] = len(rows)
        return stats

    def motion_tables(self) -> dict[str, Path]:
        """part name -> the server's motion table file, where one exists."""
        rp = self.tables_dir / "ini" / "RolePart.ini"
        if not rp.is_file():
            return {}
        conf = parse_ini(rp).get("Config", {})
        out: dict[str, Path] = {}
        for i in range(int(conf.get("Count", "0") or 0)):
            part = conf.get(f"Part{i}")
            rel = conf.get(f"MotionIni{i}")
            if not part or not rel:
                continue
            p = self.tables_dir / Path(rel.replace("\\", "/"))
            if p.is_file():
                out[part] = p
        return out
