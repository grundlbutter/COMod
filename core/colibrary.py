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
        if kind == "b":
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
        if kind == "b":
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
