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

import safepath
from coassets import AssetRoot, Located, PartIni, parse_ini
from tqhash import tq_hash


class GameMapUnreadable(Exception):
    """``ini/GameMap.dat`` is present and does not parse.

    Distinct from "this server ships no map registry", which is an ordinary
    empty result. Conflating the two is what made the old inline parser
    silent: a drifted table and an absent one were both ``[]``.
    """


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

    def _library_path(self, rel: str) -> Path:
        r"""``self.library / rel``, confined -- or ``UnsafePath``.

        **The VALUE side of a filemap entry is as untrusted as the key side.**
        `3629237` confined the keys, because ``materialize_maproot`` writes
        them; but ``["l", "../../../Users/Public/creds.txt"]`` is the same
        third-party JSON steering a *read*, and every "l" lookup below joined
        it onto the library with a bare ``/``. On Windows an absolute value
        (``["l", "C:\Windows\win.ini"]``) discards the library entirely.

        Traced: `locate`/`read` returned the foreign bytes, and
        ``materialize_maproot`` then wrote them into the materialised tree at
        the confined *destination* -- so the write guard held and the file was
        still not the library's. Reported ``failed: 0``, i.e. a clean run.

        MEASURED before landing, on the real `COmmunity Library`: **108,297
        "l" entries across `zephyr` and `collection`, 0 refused** -- the guard
        cannot reject legitimate content, only the shape no writer produces.
        `tests/test_colibrary_source.py` carries the mutant control.
        """
        return safepath.confine(self.library, rel)

    # -- resolution --------------------------------------------------------

    def locate(self, logical: str) -> Optional[Located]:
        key = self._norm(logical)
        ref = self.filemap.get(key)
        if ref is None:
            # not something the client shipped: try the library tree (covers
            # probing with un-normalised IDs) and then the baseline.
            p = self._library_path("assets/" + key)
            if p.is_file():
                return Located(key, "library", p, p.stat().st_size)
            return super().locate(logical)
        kind = ref[0]
        if kind == "l":
            p = self._library_path(ref[1])
            if p.is_file():
                return Located(key, "library", p, p.stat().st_size)
            return None                              # library tree incomplete
        if kind in ("b", "e"):
            loc = super().locate(ref[1])
            if loc is None:
                return None
            # keep the *requested* path as the identity; the baseline path is
            # a storage detail.
            # `origin_root` is carried through: `super().locate` cannot
            # return a foreign hit today (only `resolve_garment`'s opt-in
            # fallback makes one), but dropping the field here is how that
            # would stop being true silently.
            return Located(key, loc.source, loc.real_path, loc.size,
                           origin_root=loc.origin_root)
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
            p = self._library_path("assets/" + key)
            if p.is_file():
                return p.read_bytes()
            return super().read(logical)
        kind = ref[0]
        if kind == "l":
            return self._library_path(ref[1]).read_bytes()
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

        `MeshIni<i>` is a path out of **the library's own ini snapshot**, so it
        is third-party text and is confined against `tables_dir` for the same
        reason the filemap's values are (see `_library_path`). A refused row is
        DROPPED, and the cost is named rather than hidden: it is
        indistinguishable here from a table the client does not ship -- which is
        already this method's contract for a row that does not resolve, and one
        poisoned row must not cost the other seven. MEASURED: 16 real
        `MeshIni`/`MotionIni` values on `zephyr`, 0 refused.
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
            try:
                p = safepath.confine(self.tables_dir, rel)
            except safepath.UnsafePath:
                continue
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

        **THE TRAP: after this runs, the library snapshot holds a table the
        client does not ship.**  A presence check against the snapshot and a
        presence check against the install are then *different questions*, and
        the snapshot's answer looks exactly like the install's::

            <library>/servers/zephyr/ini/armor.ini   530,520 B   <- written here
            <install>/Zephyr-1057-local/ini/armor.ini            -> ENOENT

        Someone asking "does this client ship an armour table?" against the
        library gets **yes**, and it is not merely a wrong answer -- it is a
        confident answer to a question they did not ask.

        Two independent tells, either sufficient:

        * **The header.**  Every file this writes opens with
          ``; SYNTHESISED by core/colibrary.py``, and the mount path uses the
          ``; SYNTHESISED-MOUNTS`` marker.
        * **The mtime, which needs no header and survives truncation,
          concatenation and a copy that drops the first line.**  On Zephyr the
          shipped ``weapon.ini`` carries **Dec 2008** and the synthesised
          ``armor.ini`` carries **this month's** date.  A part table with a
          date from this year is derived; TQ has not re-authored these since
          2015 and the plaintext ones since 2009.

        The cheap habit that covers both: **name the view you measured.**
        Install, library snapshot and ``ini/c3.wdb`` are three different
        questions about the same client -- and ``c3.wdb`` additionally answers
        ids it was never asked about, so a lookup succeeding there is not
        evidence the id is real.

        What this synthesises **from** is a directory convention
        (``synthesize_body_table`` above), and on Zephyr that convention does
        **not** cover the armour id space -- 9.2% of 6090's ``armor.dbc``
        series appear as ``<look>`` directories against a 3.5% random control
        and 46.0% for *weapons*.  So the output is a usable approximation, not
        a recovered table, and it is not evidence about how the client resolves
        armour.  `docs/CORRECTIONS.md`
        `C-2026-08-09-claude-elastic-elion-0da45c`.
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

        **Parsing is `dmap.read_gamemap_dat`'s job, not this method's.** This
        file used to carry its own copy of the layout, and the copy was the
        silent one: it raised on an EOF mismatch under the comment *"a layout
        drift would silently mis-scope every later row"* and then caught its
        own ``ValueError`` two lines below, returning ``[]``. The COre reader
        makes the same check and returns ``None``, and it is tested for it
        (`tests/test_client.py::GameMapDatTest`, truncation and trailing
        garbage on synthetic bytes). So the loud copy was the tested one and
        the silent copy was not, for byte-identical input.

        `dmap.read_gamemap_dat`'s own docstring is explicit that it lives in
        COre because *"both readers of the map index need it and neither can
        import the other"*. This was a third reader that did not ask.

        The one thing genuinely local: paths name the shipped ``.7z`` and the
        materialized tree stores the decompressed ``.DMap``, so the suffix is
        rewritten here. That is a property of *this destination*, not of the
        format, which is why it does not belong in the COre reader.

        Raises `GameMapUnreadable` when the table is present but does not
        parse -- a distinction the caller needs and could not previously make,
        since "no file", "empty table" and "layout drift" were all ``[]``.
        """
        import dmap
        p = self.tables_dir / "ini" / "GameMap.dat"
        if not p.is_file():
            return []
        rows = dmap.read_gamemap_dat(p)
        if rows is None:
            raise GameMapUnreadable(
                f"{p} is present but does not parse as GameMap.dat -- a "
                f"partial read would mis-scope every later row, so no rows "
                f"are returned. PuzzleGridSize lives only here, so every map "
                f"in the materialized tree would read as art-less.")
        for r in rows:
            fn = str(r["FileName"]).replace("\\", "/")
            if fn.lower().endswith(".7z"):
                fn = fn[:-3] + ".DMap"
            r["FileName"] = fn
        return rows

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
        # Probing candidates: `continue` on a candidate that will not run is
        # the RIGHT behaviour here -- this asks "is 7-Zip at this path", and a
        # no is an answer, not a swallowed error.  Unlike C25 nothing is
        # inverted by it: `seven is None` is reported per file below, counted
        # in `stats["failed"]`, and returned to the caller.
        #
        # What was missing is that `timeout=` raises `TimeoutExpired`, which is
        # NOT an OSError -- so a hung candidate took the whole materialise down
        # instead of moving to the next one.
        seven = None
        for cand in (r"C:\Program Files\7-Zip\7z.exe",
                     r"C:\Program Files (x86)\7-Zip\7z.exe", "7z"):
            if Path(cand).is_absolute() and not Path(cand).is_file():
                continue                      # cheaper than spawning to find out
            try:
                subprocess.run([cand], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue
            seven = cand
            break
        for logical in self.filemap:
            # map data, the ani/ placement scripts (MapScene.ani and kin) that
            # scene rendering parses from disk, and THE MAP REGISTRY.
            #
            # The registry is the one that was missing and it cost the whole
            # feature: `MapEditor.rows()` lists `ini/GameMap.json` (or the
            # binary `.dat`), not the directory, so a materialised root with
            # every map file and no registry offers ZERO maps. Selecting the
            # Collection in the Map Editor showed an empty picker with a
            # complete map tree sitting beside it.
            #
            # Named exactly, not `ini/`: a server view's `ini/` is the whole
            # of the client's tables and copying it here would put a second,
            # divergent copy of every one of them on disk under `out/`.
            low = logical.lower()
            if not (low.startswith("map/")
                    or (low.startswith("ani/") and low.endswith(".ani"))
                    or low in ("ini/gamemap.json", "ini/gamemap.dat")):
                continue
            # The key comes from a third-party COmmunity Library's
            # filemap.json (see the module docstring) -- untrusted. The old
            # `dest / Path(*split("/"))` filtered `..` only between forward
            # slashes, so a Windows backslash (`map/..\..\evil`) or a drive
            # letter (`C:\Windows\...`) re-parsed past the filter and wrote
            # anywhere on disk, at BOTH sinks below (write_bytes and the 7z
            # -o target). `safepath.confine` resolves before the containment
            # test, so it catches every one -- the single sink in this repo
            # that was not already using it.
            try:
                out = safepath.confine(dest, logical)
            except safepath.UnsafePath as e:
                stats["failed"] += 1
                log(f"refused unsafe library path: {e}")
                continue
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
            except safepath.UnsafePath as e:
                # The entry's VALUE pointed outside the library (`_library_path`).
                # Counted and logged like any other refusal rather than allowed
                # to abort the run: one poisoned entry must not cost the other
                # 108,296, and a refusal that kills the import would push users
                # back to the unguarded path.
                stats["failed"] += 1
                log(f"refused unsafe library source: {e}")
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
            # An unreadable table is recorded, not swallowed. It is not fatal
            # -- the rest of the tree is already written and usable -- but
            # "every map reads as art-less" must not be indistinguishable
            # from "this server ships no registry", which is what a bare
            # `if rows:` gave.
            try:
                rows = self.parse_gamemap_dat()
            except GameMapUnreadable as e:
                rows = []
                stats["gamemapError"] = str(e)
                log(f"GameMap.dat is present but unreadable: {e}")
            if rows:
                gm.parent.mkdir(parents=True, exist_ok=True)
                gm.write_text(json.dumps(rows, indent=1), "utf-8")
                stats["gamemapRows"] = len(rows)
        return stats

    # -- parse profile -------------------------------------------------------

    #: The composed case's pin.  Named once, here, because the reason is long
    #: and three call sites quote the answer.
    PINNED_COMPOSED_PROFILE = "plaintext"

    #: Baselines over which `PINNED_COMPOSED_PROFILE` is MEASURED invariant.
    #: Not a guess and not open-ended -- see `table_profile`.
    PIN_INVARIANT_OVER = ("patch5165", "patch5517", "patch6090")

    def ships(self, logical: str) -> bool:
        """Did the CLIENT ship this path -- as opposed to the composed view
        being able to resolve it?

        The distinction is the whole of the defect this method exists for.
        `locate`/`read` fall back to the library tree and then the baseline,
        so *every* probe of the composed view is answered by the baseline
        when the client is silent.  The filemap is the record of what the
        client itself carried, written by `tools/assetdiff.py` while it had
        the client's own tree open, so it is the only thing here that can
        answer a question *about the client*.

        Deliberately the filemap and NOT the `ini/` snapshot: the snapshot is
        contaminated.  `write_synthesized_tables` writes a 530 KB `armor.ini`
        into it for a client that ships none, so snapshot presence proves the
        library derived a table, not that the client shipped one.  MEASURED
        on `zephyr` 2026-08-09: `ini/armor.ini` snapshot=True filemap=absent.

        Kind is not consulted, deliberately.  A `"b"`/`"w"`/`"e"` entry means
        the client shipped that path with bytes the baseline already had --
        it shipped it.  Only absence from the filemap means it did not.
        """
        return self._norm(logical) in self.filemap

    def table_profile(self):
        r"""The `npcart.Profile` for the CLIENT THIS VIEW SHOWS, or None.

        **THE DEFECT THIS REPLACES.** `__init__` calls `super().__init__(root)`,
        so a `ServerView` *is* an `AssetRoot` rooted at the **baseline**.  Every
        parse-profile probe asked of the composed view -- `npcart.detect_profile`
        on `self.read`, or `plugin_for(self.root)` -- is therefore answered by
        whichever baseline the user happens to have configured, never by the
        community client whose assets are on screen.  Register citation:
        `docs/CORRECTIONS.md` **C-2026-08-09-plugin-c-serverview-profile** --
        *"a DatPkg `ServerView` takes its parse profile from the BASELINE, not
        from the client whose assets it shows -- and 25 of 397 NPCs silently
        lose their art"*.

        **WHAT THIS BUYS, AND -- MORE IMPORTANTLY -- WHAT IT DOES NOT.**

        It makes the answer **STABLE**.  It does **NOT** show that the chosen
        profile's answers are the CORRECT ones for this client.  Which profile
        is right for a community client is **UNMEASURED** and is a separate
        question with a separate owner; nothing below is evidence about it.
        Read that as written -- the temptation this method sits next to is to
        conclude that the profile which resolves more rows is the true one, and
        that inference is refuted, not merely unproven.  See `PIN`, below.

        **MEASURED, on `21f2501`**, `zephyr` over the five official baselines,
        superseding the register entry's `25 of 400` file-order sample:

            5165/plaintext vs 6090/official, ALL rows :  646 / 2785  =  23.2%
            the same, per distinct npc_type           :  639 / 2747  =  23.3%

        (Both are right.  Zephyr's `npc.ini` carries 38 duplicated `NpcType`
        sections, so 2,785 rows collapse to 2,747 types.  Quote the unit.)

        And the register's framing -- *"RESOLVED DIFFERENTLY"*, *"the answer
        moves"* -- is **REFUTED**.  Over all 105 pairings of 5 baselines x 3
        profiles the number of NPCs where both sides resolve to a **non-empty
        but different** mesh/texture is **ZERO**.  Every single difference is
        one-sided: one side resolves, the other returns `('', '')`.  The answer
        never moves; it only appears or disappears, and every option is a strict
        subset of 6090/official.  That nesting is exactly what a larger table
        answering ids from a foreign id space produces, which is why "6090
        resolves more" is not evidence that 6090 is right --
        `docs/handoff_zephyr_planning.md` §3.4 is the same trap caught once
        already on this client.

        **THE PIN, and why it is the STABLE profile rather than the rich one.**

        Fully-resolved rows, holding each axis fixed (MEASURED, `21f2501`):

            baseline   PROFILE_PLAINTEXT   PROFILE_OFFICIAL   PROFILE_CCO
            5017            1769                   0                0
            5065            1769                   0                0
            5165            1989                   0                0
            5517            1989                2224                0
            6090            1989                2628                0

        Pinning `plaintext` is **invariant across 5165 / 5517 / 6090** -- 0
        differences on every pairing -- because the plaintext lookup layer froze
        at 5165 and the later clients ship byte-identical copies
        (`tools/frozentables.py`).  Pinning `official` buys nothing: two
        baselines that both select `official` still disagree on **404** NPCs
        (5517 vs 6090).  **Stability is a property of WHICH profile is pinned,
        not of pinning one.**

        Scope the invariance honestly rather than rounding it up: it does
        **not** extend to 5017/5065, which differ from 5165 on **220** NPCs
        under `plaintext`.  Hence `PIN_INVARIANT_OVER`, which names the three
        it was measured over and no more.

        `official` over 6090 resolves **639 more** distinct npc_types (2628 vs
        1989; 646 more rows).  Those are NOT taken, and the reason is a control
        rather than a preference: splitting resolved art by filemap provenance
        scores 100.0% ("every resolved path is one Zephyr ships"), but the SAME
        metric scores 99.7% under an id mapping **shuffled wrong by
        construction** -- so it measures namespace density, not id-space
        agreement.  The sharpest sub-measure ran backwards (Zephyr-exclusive art
        663 real vs 939 shuffled).  639 rows that nothing distinguishes from
        noise are not a benefit to be smuggled in.  This is an instance of
        `docs/CORRECTIONS.md` **C-2026-08-09-reproduce-not-hold** -- *"a number
        that REPRODUCES is not a claim that HOLDS"*.

        **THE TRAP THIS MUST NOT FALL INTO.**  Probing the client's own
        namespace naively reaches `npcart.detect_profile`'s terminal
        `return PROFILE_CCO` -- Zephyr ships no `npc.json`, no
        `3DSimpleObj.dbc` and no `3DSimpleObj.ini`.  MEASURED: `PROFILE_CCO`
        over `zephyr` loads **0 rows and resolves 0 NPCs on every one of the
        five baselines**.  1,989 resolved NPCs would become 0, silently --
        `detect_profile`'s own *"a default wearing an identity's label"*,
        wearing a fix's clothes.  So the composed case below lands somewhere
        **CHOSEN**, and this method NEVER returns `PROFILE_CCO` by
        fallthrough: it returns `None` instead, which means "no opinion, the
        baseline route answers" and is a statement, not a default.

        Returns `None` when the client ships no npc table of its own -- the
        `collection` server (38 files, `clientVersion: "curated"`) is that
        case.  Callers then keep exactly today's behaviour.
        """
        import npcart                                    # noqa: PLC0415
        # Probes in `detect_profile`'s order and for its reasons: the compiled
        # twin wins wherever one exists, because 5517/6090 ship the frozen
        # plaintext tables too and the third probe would claim them if it ran
        # first.  Asked of `ships` -- the CLIENT -- not of `read`.
        if self.ships(npcart.PROFILE_CCO.npc_table):
            return npcart.PROFILE_CCO
        if self.ships(npcart.PROFILE_OFFICIAL.simple_obj_table):
            return npcart.PROFILE_OFFICIAL
        if (self.ships(npcart.PROFILE_PLAINTEXT.npc_table)
                and self.ships(npcart.PROFILE_PLAINTEXT.simple_obj_table)):
            return npcart.PROFILE_PLAINTEXT
        # THE COMPOSED CASE, and it is the common one rather than an edge:
        # the client ships the ROW table and NONE of the deciding lookup
        # tables, so the rows are its own and the lookups are the baseline's
        # whichever way this goes.  MEASURED on zephyr (162,050 filemap
        # entries, 548 under ini/): `ini/npc.ini` present; `npc.json`,
        # `3DSimpleObj.{ini,dbc}`, `3dobj.ini`/`3DObj.dbc`,
        # `3dtexture.ini`/`3DTexture.dbc` and `3dmotion.{ini,dbc}` ALL absent.
        # Pinned, not fallen through to.
        if self.ships(npcart.PROFILE_PLAINTEXT.npc_table):
            # Resolved by an explicit table rather than by building an
            # attribute name out of the string: a typo in the pin must fail
            # here, loudly and saying what it was, not as an `AttributeError`
            # naming a symbol nobody wrote.
            by_name = {p.name: p for p in (npcart.PROFILE_PLAINTEXT,
                                           npcart.PROFILE_OFFICIAL,
                                           npcart.PROFILE_CCO)}
            pin = by_name.get(self.PINNED_COMPOSED_PROFILE)
            if pin is None:                              # pragma: no cover
                raise ValueError(
                    f"PINNED_COMPOSED_PROFILE={self.PINNED_COMPOSED_PROFILE!r}"
                    f" is not one of {sorted(by_name)}")
            return pin
        return None

    def _table_origin(self, logical: str) -> str:
        """Which layer of the composition actually answered ``logical``.

        Three layers can, and which one did is not inferable from the profile
        name -- see the `npcTableFrom` note in `table_profile_report`.
        """
        if self.ships(logical):
            return "client"
        try:
            loc = self.locate(logical)
        except Exception:                                # pragma: no cover
            return "unresolved"
        if loc is None:
            return "unresolved"
        if loc.source == "library":
            return ("library tree (SHARED across servers -- may be another "
                    "server's file)")
        return "baseline"

    def table_profile_report(self, resolve: bool = True, tables=None) -> dict:
        """What answered, in a form a caller can show a user.

        **This is the deliverable, not decoration.**  Pinning removes the
        variance BY FIAT, which converts a wrong-and-unstable answer into a
        *narrower* one -- and a silent narrower answer is the single failure
        shape this project has recorded most often (`WeaponSkillName`'s vacuous
        zero, the armed-motion fallback that reported success,
        `detect_profile`'s default wearing an identity's label, `malformed`
        reading 0 because a garbage offset always finds some NUL).  **A
        fallback is not a fix unless the miss is audible.**

        So this reports the three things a user needs to tell *"we chose
        stability"* from *"we chose stability and you can tell"*:

        * `profile` / `how` -- the pin in force, and that it came from the
          client's own recorded namespace rather than from the baseline;
        * `baseline` / `baselineKind` -- what the lookup tables were composed
          over, because they still come from there and that is the honest
          description of the composition;
        * `unresolved` -- the count this pin could NOT resolve.

        It also pays forward: when someone settles the id-space question, the
        artefact already states what it gave up, so the correction lands where
        the error would be made rather than in a document nobody opens.

        ``resolve=False`` skips building the tables (a parse of the client's
        npc table plus the baseline's lookups, ~0.21 s on zephyr/6090) and
        omits the counts.

        **Pass ``tables=`` the `npcart.Tables` you are actually going to use.**
        Without it this builds a second one, and then the counts describe a
        parallel object rather than the one answering the user -- which is a
        small instance of exactly the failure this whole change is about, a
        report about something other than what answered.
        """
        import npcart                                    # noqa: PLC0415
        prof = self.table_profile()
        if prof is None:
            how = ("this client ships no npc table of its own; whatever the "
                   "composed view resolves answers")
            pinned = False
        elif self.ships(prof.simple_obj_table) or self.ships(
                npcart.PROFILE_CCO.npc_table):
            how = "declared by the client's own filemap"
            pinned = False
        else:
            how = (f"PINNED to {prof.name}: this client ships only the row "
                   f"table, so the lookup tables come from the baseline. "
                   f"Pinned to the profile MEASURED invariant across "
                   f"{'/'.join(k.replace('patch', '') for k in self.PIN_INVARIANT_OVER)}"
                   f", not to the one that resolves most")
            pinned = True
        kind = ""
        try:
            import coroot                                # noqa: PLC0415
            kind = coroot.kind_for_root(self.root) or ""
        except Exception:                                # pragma: no cover
            kind = ""
        out = {
            "server": self.server,
            "profile": prof.name if prof else None,
            "how": how,
            "pinned": pinned,
            "baseline": str(self.root),
            "baselineKind": kind,
            "clientVersion": self.profile.get("clientVersion"),
            # Stability is claimed only over the baselines it was measured
            # over.  `""` (an undeclared root) is not one of them, and saying
            # so beats implying a guarantee nobody measured.
            "stableOverBaseline": bool(pinned
                                       and kind in self.PIN_INVARIANT_OVER),
            "invariantOver": list(self.PIN_INVARIANT_OVER),
            "correction": "C-2026-08-09-plugin-c-serverview-profile",
            # WHERE THE ROWS ACTUALLY CAME FROM, which is not inferable from
            # `profile` and is not always what a reader would assume.  MEASURED
            # 2026-08-09: the `collection` server ships no `ini/npc.ini`, and
            # the composed read does NOT reach the baseline for it -- it lands
            # on `<library>/assets/ini/npc.ini`, which is **Zephyr's** file
            # (byte-identical, sha256 4bc335d0…, 480,386 bytes; the 6090
            # baseline's own is 6d00f5b3…).  `ServerView.locate`'s library-tree
            # fallback is keyed on the library, not on the server, so one
            # server's view can be answered by another server's extracted file.
            # That is a SEPARATE defect from the one this method fixes and it
            # is deliberately not fixed here; recording it at the point of use
            # so the count above is not read as the baseline's answer.
            "npcTableFrom": self._table_origin(
                prof.npc_table if prof else npcart.PROFILE_PLAINTEXT.npc_table),
        }
        if not resolve:
            return out
        try:
            t = tables if tables is not None else npcart.Tables(self.read, prof)
            n = res = 0
            for row in t.npcs:
                n += 1
                p = t.plan_for_npc(row)
                if p.geometry and p.texture:
                    res += 1
            out.update(npcs=n, resolved=res, unresolved=n - res)
        except Exception as e:                           # pragma: no cover
            out["countsError"] = str(e)
        return out

    def table_profile_note(self, tables=None) -> str:
        """One line, in `coviewer`'s established ``parser plugin: <name>
        (<how>)`` shape, carrying the unresolved count.

        The count of NPCs a pin could not resolve must appear somewhere a user
        MEETS, not only in a docstring -- that is the whole difference between
        a named cost and a silent one.
        """
        r = self.table_profile_report(tables=tables)
        head = (f"npc parse profile: {r['profile'] or 'none'} "
                f"({r['how']}); lookup tables composed over baseline "
                f"{r['baselineKind'] or r['baseline']}")
        if r.get("npcTableFrom") and r["npcTableFrom"] != "client":
            head += f"; npc rows from the {r['npcTableFrom']}"
        if "unresolved" in r:
            head += (f" -- {r['resolved']}/{r['npcs']} rows resolve, "
                     f"{r['unresolved']} do NOT under this pin")
        if r["pinned"] and not r["stableOverBaseline"]:
            head += (" [the pin's invariance was measured over "
                     + "/".join(k.replace("patch", "")
                                for k in self.PIN_INVARIANT_OVER)
                     + "; this baseline is not one of them]")
        return head

    def motion_tables(self) -> dict[str, Path]:
        """part name -> the server's motion table file, where one exists.

        `MotionIni<i>` is confined against `tables_dir`, same as `MeshIni<i>` in
        `part_tables` and for the same reason: it is a path read out of a
        third-party library's ini snapshot, and the bare join let it name any
        file on the box (`MotionIni0=C:\\Windows\\win.ini`) -- which the caller
        then opens and parses."""
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
            try:
                p = safepath.confine(self.tables_dir, rel)
            except safepath.UnsafePath:
                continue
            if p.is_file():
                out[part] = p
        return out
