#!/usr/bin/env python3
r"""
mountart.py -- mounts, the entity row COMod had no tool and no test for.

    py -3 tools/mountart.py --client <DIR> --coverage
    py -3 tools/mountart.py --all-clients --coverage
    py -3 tools/mountart.py --client <DIR> --mount 801000
    py -3 tools/mountart.py --client <DIR> --list --limit 20

WHY THERE IS A TOOL AT ALL, WHEN THE RESOLVER ALREADY WORKS
------------------------------------------------------------
A mount is the same shape as a body -- geometry, animation, texture, binding
row, data row -- and `coassets` resolves the geometry and the texture
already: `resolve_declared` was written for exactly the two-levels-deep
`c3/mount/801/8010000.c3` shape, and it works. MEASURED here, per install,
the binding row's mesh refs resolve **2,122 of 2,122 on 5517, 2,655 of 2,655
on 6090, 2,227 of 2,227 on 6609**.

So this module is not a resolver. It is the three things a person could not
get at without one, and each is a fact about the client that nothing in the
tree recorded:

1. **WHICH TABLE IS THE LIVE ONE, AND ON THE OFFICIAL CLIENTS THE PLAINTEXT
   IS A 49-BYTE DECOY.** ``ini/Mount.ini`` is 49 bytes on **33 of the 34
   install directories here** -- one section, ``[Mount3422]``, ``Mesh=999`` /
   ``Texture=999``, stamped 2003 -- and it does not even use the
   ``Part=``/``Mesh0=`` grammar the other appearance tables use, so
   `coassets.PartIni` reads it as one row with ZERO parts. The live table is
   the compiled twin ``ini/mount.dbc`` (magic ``MESH``), and `PartIni`
   already prefers it. An install that ships no twin therefore has **no
   usable mount binding row at all** and reports 1 row / 0 parts -- which
   reads exactly like a working table with nothing in it. `MountTable.is_stub`
   is the distinction, and `--coverage` prints it rather than printing "1".

   **THE 34TH IS THE COUNTER-EXAMPLE AND IT IS NOT AN EXCEPTION TO WAVE
   AWAY.** ``CCO-snapshot-2026-08-24`` ships a **435 KB** ``Mount.ini`` in
   the ordinary ``Part=``/``Mesh0=`` grammar and no ``mount.dbc`` at all: the
   community client keeps its tables as plaintext. So "the plaintext is a
   decoy" is a fact about the OFFICIAL lineage, not about the format, and
   `is_stub` is written as a behavioural test (`rows > 0 and part_refs == 0`)
   precisely so it answers correctly for a client nobody here anticipated.
   This paragraph exists because the first draft of this module said "every
   install" and `tests/test_asset_coverage.py` went red on CCO.

2. **THE ANIMATION LAYER, WHICH ON THE OFFICIAL CLIENTS IS ONLY IN THE
   COMPILED TWIN.** ``ini/MountMotion.ini`` is **0 bytes on all 33 official
   installs that ship it** -- not stale, empty. There the motion set lives in
   ``ini/mountmotion.dbc``, an ``RSDB`` id->path table (8,787 rows on 6090,
   19,714 on 7205) naming ``c3/Mount/801/101.C3``-style action files. This is
   the third instance of the `armor.ini` ``Mesh0=`` lesson
   (`docs/CORRECTIONS.md`): the declaration existed, `core/dbc.Rsdb` has
   parsed the form since 2026-08-09, and no reader asked it for mounts.

   Again CCO is the other way round: **1.37 MB, 40,618 rows** of
   ``<id>=<path>`` plaintext and no twin. So the plaintext is read when it
   has content and there is no twin, and skipped when it is empty -- the
   empty case is what must not be reported as a successful read of nothing.

3. **SADDLES ARE MOUNT ART AND WERE FILED UNDER CHARACTERS.**
   ``c3/mountsaddle/801/8010012.c3`` is named by the same tables and is
   already in `coassets.AssetRoot.ROLE_FAMILIES` -- but `tools/catalog.py`
   had no ``c3/mountsaddle/`` rule, so every saddle fell through to the
   generic ``c3/`` rule and was filed as ``character/misc``. The rule is
   added there in the same commit; this module counts them so the fix has a
   number attached.

WHAT IS NOT HERE, DELIBERATELY
-------------------------------
The mount **data row** -- ``ini/mounttype.dat`` -- is already a declared
subject of the per-build catalog plugins (`plugins/patch5165.py` onward
declare ``TableSpec("mount", "mounttype.dat", ...)``), so `comod catalogs`
and `comod browse mount` answer for it today. Reading it again here would be
a second decoder for a table that has one. `--coverage` names the file and
points at that command instead of re-counting it.

Read-only. Opens no archive for writing, launches nothing, and touches no
install.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "core"))

import coassets                                    # noqa: E402
import coroot                                      # noqa: E402
import dbcshadow                                   # noqa: E402

#: The part slot the client declares for mounts. `RolePart.ini` / the ROPT
#: section of `c3.wdb` name it; nothing here invents the string.
PART = "mount"

#: The plaintext motion table and the compiled twin that actually carries it.
MOTION_INI = "ini/MountMotion.ini"

#: Where saddle art lives. Two levels deep, like the mount's own art, which
#: is why no id-derived probe reaches it -- see `coassets.resolve_declared`.
SADDLE_DIR = "c3/mountsaddle/"

#: The mount's own art directory, also two levels deep.
MOUNT_DIR = "c3/mount/"

#: The DATA row, owned by the catalog plugins. Named, never re-decoded here.
DATA_TABLE = "ini/mounttype.dat"


@dataclass
class MountRef:
    """One (mesh, texture) pair of one mount appearance, with where it went."""

    index: int
    mesh_id: str = ""
    mesh: str = ""              # logical path, or "" when unresolved
    texture_id: str = ""
    texture: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.mesh)


@dataclass
class MountAppearance:
    ident: str
    parts: list[MountRef] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"id": self.ident,
                "parts": [{"index": p.index, "meshId": p.mesh_id,
                           "mesh": p.mesh, "textureId": p.texture_id,
                           "texture": p.texture} for p in self.parts]}


@dataclass
class MountTable:
    """The binding row, and which of the two files answered."""

    #: The file `PartIni` was pointed at, e.g. "Mount.ini".
    name: str = ""
    #: What it actually READ -- "mount.dbc" for the compiled twin, the ini's
    #: own name when the plaintext was all there was. This is the field that
    #: separates a live table from the 2003 stub.
    source: str = ""
    #: Set when a MESH twin was present and would not parse. Not the same
    #: event as shipping no twin, and `PartIni` keeps them apart.
    twin_error: Optional[str] = None
    rows: int = 0
    part_refs: int = 0
    #: Why there is no table at all, when there is none.
    refusal: str = ""

    @property
    def is_stub(self) -> bool:
        r"""True for the 49-byte 2003 plaintext, which is not a mount table.

        **The test is BEHAVIOURAL, not a size and not a name**, and that is
        what saved it: a table that yields rows but not one single
        (mesh, texture) pair cannot bind any art, whatever file it came from.
        A size or name test would have called CCO's 435 KB plaintext
        ``Mount.ini`` a stub -- it is the real table there -- and would have
        called any future compiled-only client live without looking.
        """
        return self.rows > 0 and self.part_refs == 0


class MountArt:
    """Every layer of the mounts row for ONE install."""

    def __init__(self, root: Path | str, view=None):
        self.root = Path(root)
        self._view = view
        self._owns_view = view is None
        self._apps: Optional[list[MountAppearance]] = None
        self._table: Optional[MountTable] = None
        self._motions: Optional[dict] = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def view(self):
        if self._view is None:
            self._view = coassets.AssetRoot.bare(self.root)
        return self._view

    def close(self) -> None:
        if self._owns_view and self._view is not None:
            try:
                self._view.close()
            except Exception:                        # noqa: BLE001
                pass
            self._view = None

    def __enter__(self) -> "MountArt":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 1. the binding row ------------------------------------------------
    def table(self) -> MountTable:
        """The mount appearance table, or a MountTable saying why not.

        `part_tables()` RAISES `FileNotFoundError` on an install that
        declares no parts at all (4274 is the one in this corpus), and that
        refusal is carried into `refusal` rather than turned into an empty
        table: "this install declares no parts" and "this install declares
        mounts and ships none" are different facts and a caller must be able
        to tell them apart.
        """
        if self._table is not None:
            return self._table
        t = MountTable()
        try:
            tabs = self.view.part_tables()
        except FileNotFoundError as e:
            t.refusal = f"the install declares no parts at all ({e})"
            self._table = t
            return t
        ini = tabs.get(PART)
        if ini is None:
            t.refusal = (f"no {PART!r} slot in this install's part "
                         f"declaration (declared slots: "
                         f"{', '.join(sorted(tabs)) or 'none'})")
            self._table = t
            return t
        apps = list(ini)
        t.name = ini.name
        t.source = getattr(ini, "source", ini.name)
        t.twin_error = getattr(ini, "twin_error", None)
        t.rows = len(apps)
        t.part_refs = sum(len(a.parts) for a in apps)
        self._table = t
        return t

    def appearances(self) -> list[MountAppearance]:
        """Every mount binding row, with its art resolved through `AssetRoot`.

        Resolution is `resolve_asset`, unchanged and not re-implemented: the
        directory probe, the install's own `c3.wdb` declaration and the
        garment fallback all apply exactly as they do for a body.
        """
        if self._apps is not None:
            return self._apps
        out: list[MountAppearance] = []
        try:
            ini = self.view.part_tables().get(PART)
        except FileNotFoundError:
            ini = None
        if ini is not None:
            for app in ini:
                ma = MountAppearance(ident=app.ident)
                for pr in app.parts:
                    m = self.view.resolve_asset(pr.mesh, "mesh") if pr.mesh else None
                    tx = (self.view.resolve_asset(pr.texture, "texture")
                          if pr.texture else None)
                    ma.parts.append(MountRef(
                        index=pr.index, mesh_id=pr.mesh,
                        mesh=m.logical if m is not None else "",
                        texture_id=pr.texture,
                        texture=tx.logical if tx is not None else ""))
                out.append(ma)
        self._apps = out
        return out

    def get(self, ident: str) -> Optional[MountAppearance]:
        want = str(ident).lstrip("0") or "0"
        for a in self.appearances():
            if a.ident == ident or a.ident.lstrip("0") == want:
                return a
        return None

    # -- 2. the animation layer -------------------------------------------
    def motions(self) -> dict:
        r"""``{"state", "rows", "paths", "source"}`` for the mount motion set.

        `state` says which of five things happened -- ``ok``, ``empty``,
        ``no-twin``, ``unreadable``, ``absent`` -- for the same reason
        `catalog.LINKAGE_*` has four values: collapsing them is what lets
        "this install has no motion set" and "we could not read one" arrive
        as the same empty dict.

        **THE TWIN IS PREFERRED AND THE EMPTY PLAINTEXT IS REFUSED, WHICH ARE
        TWO DIFFERENT RULES.** The twin wins where there is one, for the
        reason `coassets.object_db` gives: a shipped plaintext beside a
        compiled twin is a decoy, and `WeaponMotion.ini` agrees with its twin
        on 1.7% of rows. Where there is NO twin the plaintext is the table --
        CCO's is 40,618 real rows -- so it is read. Where it is ZERO BYTES,
        which is all 33 official installs, it is refused: returning ``{}``
        from it would be a successful read of nothing, indistinguishable from
        a client that genuinely has no mounts.

        The twin is reached the way `coassets.object_db` reaches `3DObj.dbc`:
        `dbcshadow.compiled_twin` supplies the NAME (case included), and the
        name is then `locate()`d, so an overlay that redirects the assets
        redirects the table too.
        """
        if self._motions is not None:
            return self._motions
        out = {"state": "absent", "rows": 0, "paths": {}, "source": "",
               "detail": ""}
        loc = self.view.locate(MOTION_INI)
        if loc is None:
            out["detail"] = f"{MOTION_INI} is not in this install"
            self._motions = out
            return out
        p = self.view._table_path(loc)
        twin = dbcshadow.compiled_twin(p)
        if twin is None:
            out["source"] = MOTION_INI
            size = p.stat().st_size
            if size == 0:
                out["state"] = "empty"
                out["detail"] = (
                    f"no compiled twin beside {MOTION_INI}, and the plaintext "
                    f"is 0 bytes -- this install declares no mount motion "
                    f"set. Reading it would answer {{}} and look like a "
                    f"successful read of nothing")
                self._motions = out
                return out
            rows = coassets.AssetRoot._parse_id_path_ini(p)
            out["state"] = "ok" if rows else "unreadable"
            out["paths"] = {k: v.replace("\\", "/") for k, v in rows.items()}
            out["rows"] = len(out["paths"])
            out["detail"] = (f"no compiled twin; read the {size}-byte "
                             f"plaintext, which IS the table here")
            self._motions = out
            return out
        rel = MOTION_INI.rsplit("/", 1)[0] + "/" + twin.name
        tloc = self.view.locate(rel)
        if tloc is None:
            out["state"] = "unreadable"
            out["source"] = rel
            out["detail"] = "the twin is beside the ini but did not locate"
            self._motions = out
            return out
        tp = self.view._table_path(tloc)
        out["source"] = rel
        try:
            if dbcshadow.twin_magic(tp) != b"RSDB":
                out["state"] = "unreadable"
                out["detail"] = "the twin is not an RSDB path table"
                self._motions = out
                return out
            import dbc                              # noqa: PLC0415
            paths = dbc.Rsdb.parse(tp.read_bytes()).paths
        except Exception as e:                       # noqa: BLE001
            out["state"] = "unreadable"
            out["detail"] = f"{type(e).__name__}: {e}"
            self._motions = out
            return out
        out["state"] = "ok"
        out["paths"] = {str(k): v for k, v in paths.items()}
        out["rows"] = len(out["paths"])
        self._motions = out
        return out

    def motion_files(self) -> dict:
        """`{logical: bool present}` for the DISTINCT motion files named.

        Distinct, because 8,787 rows on 6090 name far fewer files and a
        per-row count would report the same file thousands of times and call
        it coverage.
        """
        out: dict[str, bool] = {}
        for v in self.motions()["paths"].values():
            s = (v.decode("latin-1") if isinstance(v, (bytes, bytearray))
                 else str(v)).replace("\\", "/").lstrip("/")
            if not s or s in out:
                continue
            out[s] = self.view.locate(s) is not None
        return out

    # -- 3. saddles --------------------------------------------------------
    def art_under(self, prefix: str) -> tuple[list[str], str]:
        r"""``(logical paths under `prefix`, how they were counted)``.

        **THE SECOND VALUE IS THE POINT, AND LEAVING IT OUT PRODUCED A WRONG
        NUMBER THE FIRST TIME THIS RAN.** `coassets.AssetRoot._c3_names`
        returns None for a root whose namespace cannot be listed -- MEASURED,
        that is 7205 and Zephyr among others -- and a caller that reads None
        as an empty set reports an install that ships 4,000 mount meshes as
        shipping none. It is the shape `_c3_names`' own docstring warns about
        ("None means cannot be enumerated and is NOT an empty set"), and this
        function walked straight into it before the source was reported.

        So there are three answers, never two:

        ``names``  the install's own name set answered; archives included.
        ``loose``  the name set was unavailable and the LOOSE tree was
                   walked. Real, and blind to every archive, so the count is
                   a floor and not a total.
        ``none``   neither -- nothing to walk and nothing to list.
        """
        names = None
        try:
            names = self.view._c3_names()
        except Exception:                            # noqa: BLE001
            names = None
        if names is not None:
            return sorted(n for n in names if n.startswith(prefix)), "names"
        d = self.root / prefix.strip("/")
        if not d.is_dir():
            return [], "none"
        out = []
        for p in sorted(d.rglob("*")):
            if p.is_file():
                out.append(str(p.relative_to(self.root)).replace("\\", "/").lower())
        return out, "loose"

    def saddles(self) -> tuple[list[str], str]:
        """Saddle art this install ships. See `art_under` for the second value."""
        return self.art_under(SADDLE_DIR)

    def declared_ids(self) -> dict:
        r"""Mount ids the install's ``ini/c3.wdb`` names, as ``{id: path}``.

        **THE SECOND ENUMERATOR, AND ON FOUR FAMILIES IT IS THE ONLY ONE.**
        `appearances()` reads the binding row and answers nothing at all
        where `mount.dbc` is absent. `c3.wdb` is the same fact in the other
        shape -- id -> path, the table `resolve_declared` has consulted since
        it was written -- and it is present on families where the binding row
        is not. MEASURED, rows whose path starts ``c3/mount/``:

            7878    79,492      and NO mount.dbc
            7867    78,441      and NO mount.dbc
            Zephyr  22,491      and NO mount.dbc, and no LOOSE c3/mount
                                either -- the art is in the archives
            7205    39,876      beside a 1,223-row mount.dbc
            6090       167      beside a 1,651-row mount.dbc

        The two disagree wildly in size and they are not measuring the same
        thing: the binding row is one entry per APPEARANCE a character can
        wear, `c3.wdb` is one entry per FILE-shaped id including every action
        frame. Reporting them as one number would be the "tidy number over a
        gap" this sprint is against, so they are two fields and the coverage
        line prints both.

        ``{}`` on the four installs that ship no `c3.wdb` at all (4274,
        5017, 5065, CCO-snapshot) -- which is a fact about them, and the same
        reason `object_db` exists.
        """
        db = self.view.resource_db
        if db is None:
            return {}
        out: dict[str, str] = {}
        for i, p in getattr(db, "by_id", {}).items():
            s = (p.decode("latin-1") if isinstance(p, (bytes, bytearray))
                 else str(p)).replace("\\", "/").lower()
            if s.startswith(MOUNT_DIR) or s.startswith(SADDLE_DIR):
                out[str(i)] = s
        return out

    def mount_art(self) -> tuple[list[str], str]:
        """Mount geometry/texture files this install ships, however counted.

        Separate from the binding row on purpose: MEASURED, the two come
        apart in BOTH directions -- 7867 and 7878 ship 4,346 and 4,420 files
        under `c3/mount/` and no `mount.dbc`, while 7205 ships a 1,223-row
        `mount.dbc`. A single "mounts: supported" would have been true of
        neither.
        """
        return self.art_under(MOUNT_DIR)

    # -- the number --------------------------------------------------------
    def coverage(self) -> dict:
        t = self.table()
        apps = self.appearances()
        refs = [p for a in apps for p in a.parts]
        mot = self.motions()
        mf = self.motion_files() if mot["state"] == "ok" else {}
        sad, sad_src = self.saddles()
        art, art_src = self.mount_art()
        decl = self.declared_ids()
        return {
            "install": self.root.name,
            "root": str(self.root),
            "table": {"name": t.name, "source": t.source, "rows": t.rows,
                      "part_refs": t.part_refs, "is_stub": t.is_stub,
                      "twin_error": t.twin_error, "refusal": t.refusal},
            "binding": {
                "appearances": len(apps),
                "mesh_refs": sum(1 for r in refs if r.mesh_id and r.mesh_id != "0"),
                "mesh_resolved": sum(1 for r in refs if r.mesh),
                "texture_refs": sum(1 for r in refs
                                    if r.texture_id and r.texture_id != "0"),
                "texture_resolved": sum(1 for r in refs if r.texture),
            },
            "motion": {"state": mot["state"], "source": mot["source"],
                       "rows": mot["rows"], "distinct_files": len(mf),
                       "present": sum(1 for v in mf.values() if v),
                       "detail": mot["detail"]},
            "saddles": len(sad), "saddles_counted": sad_src,
            "mount_art": len(art), "mount_art_counted": art_src,
            "declared_ids": len(decl),
            "declared_saddle_ids": sum(1 for v in decl.values()
                                       if v.startswith(SADDLE_DIR)),
            "has_resource_db": self.view.resource_db is not None,
            "data_table": DATA_TABLE,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_coverage(c: dict) -> None:
    t, b, m = c["table"], c["binding"], c["motion"]
    print(f"  {c['install']}")
    if t["refusal"]:
        print(f"      binding row : REFUSED -- {t['refusal']}")
    elif t["is_stub"]:
        # Says what was MEASURED (rows, zero pairs) and names the file that
        # answered, rather than inferring which file is absent. `twin_error`
        # below is what distinguishes "no twin shipped" from "the twin would
        # not parse", and it is printed on its own line.
        print(f"      binding row : STUB -- {t['source']} gives {t['rows']} "
              f"row(s) and 0 mesh/texture pairs, so this install has NO "
              f"usable mount binding row")
    else:
        print(f"      binding row : {t['source']:<14} {t['rows']:>5} rows  "
              f"mesh {b['mesh_resolved']}/{b['mesh_refs']}  "
              f"texture {b['texture_resolved']}/{b['texture_refs']}")
    if t["twin_error"]:
        print(f"                    twin present and UNREADABLE: {t['twin_error']}")
    if m["state"] == "ok":
        print(f"      animation   : {m['source']:<22} {m['rows']:>5} rows  "
              f"{m['present']}/{m['distinct_files']} distinct files present")
    else:
        print(f"      animation   : {m['state'].upper()} -- {m['detail']}")
    how = {"names": "", "loose": "  (loose tree only -- archives not "
                                 "enumerable, so this is a FLOOR)",
           "none": "  (nothing to enumerate)"}
    print(f"      mount art   : {c['mount_art']} files under {MOUNT_DIR}"
          f"{how.get(c['mount_art_counted'], '')}")
    print(f"      saddle art  : {c['saddles']} files under {SADDLE_DIR}"
          f"{how.get(c['saddles_counted'], '')}")
    if c["has_resource_db"]:
        # The "only enumerator" clause is CONDITIONAL. Printed unconditionally
        # it said "the only one" on every install including the ones whose
        # binding row is live and resolving 2,655 of 2,655 -- a sentence that
        # is false exactly where the reader is most likely to trust it.
        only = ("  -- and with the binding row a stub, the ONLY enumerator"
                if c["table"]["is_stub"] or c["table"]["refusal"] else "")
        print(f"      c3.wdb ids  : {c['declared_ids']} name mount art "
              f"({c['declared_saddle_ids']} saddle){only}")
    else:
        print("      c3.wdb ids  : this install ships no ini/c3.wdb")


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client")
    ap.add_argument("--all-clients", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--mount", default="")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    roots: list[Path] = []
    if a.all_clients:
        roots = [p for p in sorted(coroot.clients_dir().iterdir())
                 if p.is_dir() and coroot.looks_like_root(p)]
    elif a.client:
        roots = [Path(a.client)]
    else:
        ap.error("one of --client or --all-clients is required")

    import json as _json
    out = []
    for root in roots:
        try:
            ma = MountArt(root)
        except Exception as e:                       # noqa: BLE001
            print(f"  {root.name}: could not open ({e!r})")
            continue
        try:
            if a.mount:
                app = ma.get(a.mount)
                if app is None:
                    print(f"  {root.name}: no mount appearance {a.mount}")
                    return 1
                if a.json:
                    out.append(app.to_json())
                else:
                    print(f"  {root.name}  mount {app.ident}")
                    for p in app.parts:
                        print(f"      part{p.index}  mesh {p.mesh_id:<12}"
                              f"{p.mesh or 'NOT FOUND'}")
                        print(f"             tex  {p.texture_id:<12}"
                              f"{p.texture or 'NOT FOUND'}")
                continue
            if a.list:
                apps = ma.appearances()
                print(f"  {root.name}  {len(apps)} mount appearances")
                for app in apps[:a.limit]:
                    ids = ", ".join(f"{p.mesh_id}->{p.mesh or 'NOT FOUND'}"
                                    for p in app.parts)
                    print(f"      {app.ident:<12}{ids}")
                if len(apps) > a.limit:
                    print(f"      ... {len(apps) - a.limit} more (--limit)")
                continue
            c = ma.coverage()
            out.append(c)
            if not a.json:
                _print_coverage(c)
        finally:
            ma.close()
    if a.json and out:
        print(_json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
