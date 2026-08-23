#!/usr/bin/env python3
r"""
assetdiff.py -- what does another client's asset set add or change?

Compares a second Conquer Online client install (the "other" tree) against the
baseline install the rest of the tools use, by **content hash**, and optionally
extracts everything unique into a library tree.

Both packaging schemes are understood:

  * WDF archives (`core/wdf.py`)          -- hashed names, baseline + garments
  * NetDragonDatPkg .tpi/.tpd (`core/tpd.py`) -- plaintext names, old clients
  * loose files under the install root    -- both trees

The unit of comparison is the *decompressed payload*: a Zephyr `.tpd` entry and
a baseline `.wdf` entry with identical bytes are the same asset no matter how
they are packaged.  Name matching uses the client's own TQ string hash
(`core/tqhash.py`), so a plaintext path from the other client is matched
against the baseline's hashed WDF index exactly the way the client would.

Statuses, per file in the OTHER tree:

  identical   content exists somewhere in the baseline (any name)  -> skipped
  modified    baseline has the same *path* (WDF name-hash or loose file)
              but different bytes
  new         neither the path nor the content exists in the baseline
  new-unnamed a WDF entry whose name could not be recovered and whose
              content is not in the baseline

Code is never extracted: .exe .dll .ocx .crm and anything MZ-headed is
reported but left behind, whatever else it matches.

Usage:
    py -3 tools/assetdiff.py --other "D:\Zephyr Conquer 1057"
    py -3 tools/assetdiff.py --other DIR --extract-to "D:\COmmunity Library" \
        --report out/zephyr/assetdiff_report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                    # noqa: E402
from tpd import TpdArchive, TPD_MAGIC            # noqa: E402
from tqhash import tq_hash                       # noqa: E402
from wdf import WdfArchive, detect_magic         # noqa: E402

#: never extracted, never diffed as "assets" -- executable code.
CODE_EXT = {".exe", ".dll", ".ocx", ".crm", ".sys"}
#: directories under either root that are logs/scratch, not shipped content.
#: Directories that hold no shipped art.  `.sentry-native` is here for a
#: measured reason: the crash reporter creates `<uuid>.run.lock` while the
#: game runs and deletes it after, and one happened to exist during an
#: import.  It became the canonical empty file (see `_alias_ok`), 28 of
#: Zephyr's tables were aliased to it, and it was gone by the time anything
#: tried to read them.  An alias target has to outlive the import.
SKIP_DIRS = {"log", "debug", "screenshot", "screenshots", "logs",
             ".sentry-native", "crashpad", "cache", "temp", "tmp"}
SKIP_EXT = {".log", ".lock", ".tmp", ".pid", ".bak"}

HASH = lambda b: hashlib.blake2b(b, digest_size=16).hexdigest()  # noqa: E731

#: The hash every empty file has.
EMPTY_HASH = HASH(b"")


def _alias_ok(h: str, rel: str) -> bool:
    """May these bytes stand in for another file's, by content?

    **No, if there are none.** Content dedup says "the baseline already has
    these bytes, point at its copy" -- which is sound for real content and
    meaningless for an empty file: every empty file hashes identically, so
    the first one scanned becomes the canonical answer for all of them. That
    produced a filemap claiming `ini/miscmotion.ini` *is*
    `.sentry-native/<uuid>.run.lock`. Both were zero bytes, so nothing was
    lost -- but the provenance was nonsense, and when the transient file went
    away the entry resolved to nothing at all.

    An empty file is cheaper to store than to alias, so it is stored.
    """
    return h != EMPTY_HASH

#: characters NTFS refuses in a component.  Two Zephyr entries carry literal
#: '?' runs -- CJK characters flattened by whatever packed that client.
_FS_BAD = '<>:"|?*'


def fs_safe(name: str) -> str:
    """A relative path Windows will accept, altering only illegal characters."""
    return "/".join(
        "".join("_" if (c in _FS_BAD or ord(c) < 32) else c for c in part)
        for part in name.split("/"))


def _progress(label: str, i: int, total: int, t0: float) -> None:
    if i % 2000 == 0 or i == total:
        dt = time.time() - t0
        print(f"  {label}: {i}/{total}  ({dt:.0f}s)", flush=True)


def _walk_loose(root: Path, extra_skip: Iterable[str] | None = None) -> list[Path]:
    """Loose files worth hashing, minus logs, caches and scratch.

    A plugin's ``import_plan["skip"]`` is **added** to `SKIP_DIRS`, never
    substituted for it. `SKIP_DIRS` is not a default anyone should be able
    to override away: `.sentry-native` is in it because a transient
    `<uuid>.run.lock` once became the canonical empty file and 28 of
    Zephyr's tables were aliased to something that no longer existed.
    """
    frags = [s.strip("/\\").lower() for s in (extra_skip or ()) if s.strip("/\\")]
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = [part.lower() for part in rel.parts[:-1]]
        if any(part in SKIP_DIRS for part in parts):
            continue
        if any(f in parts for f in frags):
            continue
        if p.suffix.lower() in SKIP_EXT:
            continue
        out.append(p)
    return out


def _rel_name(root: Path, p: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


# ---------------------------------------------------------------------------
# baseline catalog
# ---------------------------------------------------------------------------

def load_name_tables(repo: Path) -> dict[int, str]:
    """hash -> recovered name, from the tables wdf_recover.py maintains."""
    merged: dict[int, str] = {}
    for rel in ("out/wdf/c3_names.json", "out/wdf/data_names.json"):
        p = repo / rel
        if p.is_file():
            raw = json.loads(p.read_text("utf-8"))
            if isinstance(raw, dict) and "resolved" in raw:
                raw = raw["resolved"]
            merged.update({int(k, 16): v for k, v in raw.items()})
    return merged


def plugin_for(root: Path):
    """The parser plugin for an install, by the rule the viewer already uses.

    A stored declaration outranks detection, because the user knows things
    the bytes do not -- a private server's repack of 6090 reads as 6090 to
    any test of the bytes.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import plugins as plugmod
    kind = coroot.kind_for_root(root)
    return (plugmod.for_kind(kind) if kind else None) or plugmod.detect(Path(root))


def table_profile_for(assets=None, root=None, resolve=True, plugin=None):
    """``(npcart.Profile | None, report)`` for the thing whose assets are read.

    **ASK THE CLIENT BEING SHOWN, NOT THE BASELINE.**  Every `npcart.Tables`
    call site used to resolve its profile from a *root* -- `plugin_for(root)`
    or `coroot.kind_for_root(self.root)`.  For a bare install that is right.
    For a `colibrary.ServerView` it is wrong in a way nothing reports: a
    `ServerView` **is** an `AssetRoot` rooted at the **baseline**
    (`core/colibrary.py`, `super().__init__(root)`), so `.root` is the
    baseline's path and the community client whose assets are on screen is
    never asked.  `docs/CORRECTIONS.md`
    **C-2026-08-09-plugin-c-serverview-profile** -- *"a DatPkg `ServerView`
    takes its parse profile from the BASELINE, not from the client whose
    assets it shows -- and 25 of 397 NPCs silently lose their art"*.

    So: anything that can answer for itself (`ServerView.table_profile`) is
    asked first, and the baseline plugin answers only for things that cannot.

    **This function exists so there is ONE definition of that rule.**  Four
    sites build `npcart.Tables` and each had its own three-line spelling of
    "resolve the plugin from a root"; a fifth spelling is how the four drift.

    **What this DOES and DOES NOT establish**, stated here because this is the
    junction a later reader will arrive at.  It makes a community client's
    parse profile **STABLE** -- the same answer whichever baseline the user has
    configured.  It does **NOT** establish that the chosen profile's answers
    are **CORRECT** for that client; which profile is right is **UNMEASURED**
    and is a separate question with a separate owner.

    MEASURED on `21f2501`, `zephyr` over the five official baselines --
    supersedes the register entry's `25 of 400` file-order sample:

        5165/plaintext vs 6090/official : 646 / 2785 rows      = 23.2%
        the same, per distinct npc_type : 639 / 2747 types     = 23.3%
        answers that CONFLICT           :   0, over all 105 pairings

    Every difference is **one-sided** -- one side resolves, the other returns
    `('', '')` -- so the register's *"RESOLVED DIFFERENTLY"* is refuted: the
    answer never moves, it only appears or disappears.  Do not read the
    profile that resolves more as the right one; a strict-subset nesting is
    exactly what a larger table answering ids from a foreign id space
    produces (`docs/handoff_zephyr_planning.md` §3.4, and
    `docs/CORRECTIONS.md` **C-2026-08-09-reproduce-not-hold** -- *"a number
    that REPRODUCES is not a claim that HOLDS"*).

    `report` is never empty and is meant to be shown, not logged and dropped:
    a pin that removes variance by fiat gives a **narrower** answer, and a
    silent narrower answer is the failure shape this project has recorded most
    often.  See `ServerView.table_profile_report`.
    """
    ask = getattr(assets, "table_profile", None)
    if callable(ask):
        try:
            prof = ask()
        except Exception:                                # pragma: no cover
            prof = None
        if prof is not None:
            try:
                return prof, assets.table_profile_report(resolve=resolve)
            except Exception:                            # pragma: no cover
                return prof, {"profile": prof.name, "how": "viewed client"}
        # A view that ships no npc table of its own has no opinion; the
        # baseline route below is then the honest answer rather than a
        # fallthrough, and the report still says the view was asked.
    r = root if root is not None else getattr(assets, "root", None)
    if r is None:
        return None, {"profile": None, "how": "nothing to ask"}
    try:
        # `plugin=` lets a caller that already resolved one hand it over, and
        # that is not a convenience -- it preserves an answer this function
        # cannot reach. `plugin_for` ends at `plugins.detect(root)` with no
        # `exists`, which then probes with `(root / p).is_file()` and so
        # CANNOT SEE INSIDE THE WDF ARCHIVES. `coviewer` resolves its plugin
        # with `exists=self.assets.exists`, which does. For a *declared*
        # install both take the `for_kind` branch and agree; for an
        # **undeclared** one, re-deriving here would silently downgrade the
        # detection to the loose layer only.
        pl = plugin if plugin is not None else plugin_for(Path(r))
        prof = pl.table_profile() if pl else None
    except Exception:                                    # pragma: no cover
        pl, prof = None, None
    rep = {"profile": prof.name if prof else None,
           "how": (f"baseline plugin {pl.name}" if pl
                   else "no plugin claimed the root; npcart will probe"),
           "pinned": False, "baseline": str(r)}
    if callable(ask):
        try:
            rep = dict(assets.table_profile_report(resolve=resolve), **{
                k: v for k, v in rep.items() if k in ("profile", "how")})
        except Exception:                                # pragma: no cover
            pass
    return prof, rep


def import_plan_for(root: Path) -> dict:
    """What importing `root` involves, as its own plugin describes it.

    The archive list is the part that mattered: this function used to be
    the literal ``("c3.wdf", "data.wdf")``, so a baseline packaged any other
    way -- a DatPkg client, a repack -- catalogued only its loose layer and
    then reported every archived asset in the *other* tree as new. Not an
    error; a quietly wrong diff, which is worse.

    Errors are not swallowed. A plugin that raises here should be fixed, and
    the old behaviour survives as `Plugin.import_plan`'s default, so an
    install whose plugin has no opinion is catalogued exactly as before.
    """
    p = plugin_for(root)
    plan = p.import_plan(Path(root), lambda rel: (Path(root) / rel).exists())
    print(f"baseline plugin: {p.name} -- archives={list(plan.get('archives', []))} "
          f"loose={plan.get('loose', True)}", flush=True)
    return plan


def _iter_archive(path: Path, names: dict[int, str]):
    """Yield ``(name_or_None, tq_name_hash, bytes)`` for any container.

    Dispatching on the suffix is what makes `import_plan`'s archive list
    useful rather than decorative: declaring ``c3.tpi`` is pointless if the
    reader can only open a WDF. `iter_other_assets` has always done this for
    the *other* tree; the baseline is catching up.
    """
    if path.suffix.lower() in (".tpi", ".tpd"):
        arc = TpdArchive(path)
        for e in arc.entries:
            yield e.name, tq_hash(e.name), arc.read(e)
        return
    with WdfArchive(path) as a:
        for e in a.entries:
            yield names.get(e.hash), e.hash, a.read(e)


def catalog_baseline(root: Path, names: dict[int, str] | None = None,
                     plan: dict | None = None) -> dict:
    """content: set of payload hashes; nameHashes: wdf index; loose: rel->hash;
    locator: content hash -> a filemap ref saying where the baseline holds
    those bytes (named path preferred, archive-entry-by-hash otherwise)."""
    names = names or {}
    if plan is None:
        plan = import_plan_for(root)
    content: set[str] = set()
    name_hashes: dict[int, str] = {}          # tq name-hash -> content hash
    loose: dict[str, str] = {}                # lowercased rel path -> hash
    locator: dict[str, list] = {}             # content hash -> ["b",...]/["w",...]
    t0 = time.time()
    for arc_name in plan.get("archives", ("c3.wdf", "data.wdf")):
        p = root / arc_name
        if not p.is_file():
            continue
        rows = list(_iter_archive(p, names))
        n = len(rows)
        print(f"baseline {arc_name}: {n} entries", flush=True)
        for i, (nm, name_hash, blob) in enumerate(rows, 1):
            h = HASH(blob)
            content.add(h)
            name_hashes[name_hash] = h
            if not _alias_ok(h, nm or ""):
                pass                           # empty: never an alias target
            elif nm is not None:
                locator.setdefault(h, ["b", nm.lower()])
            else:
                locator.setdefault(h, ["w", arc_name, f"{name_hash:08x}"])
            _progress(arc_name, i, n, t0)
    if not plan.get("loose", True):
        print("baseline loose layer: skipped, the plugin declares none",
              flush=True)
        return {"content": content, "nameHashes": name_hashes, "loose": loose,
                "locator": locator}
    files = _walk_loose(root, plan.get("skip"))
    print(f"baseline loose files: {len(files)}", flush=True)
    for i, p in enumerate(files, 1):
        h = HASH(p.read_bytes())
        content.add(h)
        rel = _rel_name(root, p).lower()
        loose[rel] = h
        # a loose path beats an archive-by-hash ref, but never replaces an
        # already-named archive path (both resolve; keep the first name).
        if not _alias_ok(h, rel):
            pass                               # empty: never an alias target
        elif locator.get(h, ("w",))[0] == "w":
            locator[h] = ["b", rel]
        else:
            locator.setdefault(h, ["b", rel])
        _progress("loose", i, len(files), t0)
    return {"content": content, "nameHashes": name_hashes, "loose": loose,
            "locator": locator}


# ---------------------------------------------------------------------------
# the other client
# ---------------------------------------------------------------------------

def iter_other_assets(other: Path, known_names: dict[int, str]):
    """Yield (name_or_None, source, read_fn, size) for every asset in the
    other install: DatPkg pairs, every .wdf, then loose files."""
    for tpi in sorted(other.glob("*.tpi")):
        arc = TpdArchive(tpi)
        for e in arc.entries:
            yield e.name, tpi.name.lower(), (lambda a=arc, e=e: a.read(e)), e.uncompressed
    for wdf_path in sorted(other.glob("*.wdf")):
        a = WdfArchive(wdf_path)
        for e in a.entries:
            name = known_names.get(e.hash)
            yield name, wdf_path.name.lower(), (lambda a=a, e=e: a.read(e)), e.size
    for p in _walk_loose(other):
        # the archive containers themselves: their contents were just walked
        if p.suffix.lower() in (".tpi", ".tpd", ".wdf", ".wdi"):
            continue
        rel = _rel_name(other, p)
        yield rel, "loose", (lambda p=p: p.read_bytes()), p.stat().st_size


def diff(root: Path, other: Path, repo: Path, extract_to: Path | None,
         server: str | None = None):
    names = load_name_tables(repo)
    base = catalog_baseline(root, names)
    base_content, base_nh, base_loose = (base["content"], base["nameHashes"],
                                         base["loose"])
    base_locator = base["locator"]
    #: logical path -> where the bytes live now (see core/colibrary.py).
    filemap: dict[str, list] = {}

    # every plaintext path the other client gives us, hashed the client's way,
    # doubles as name recovery for its own (and the baseline's) WDF entries.
    other_names: dict[int, str] = dict(names)
    for tpi in sorted(other.glob("*.tpi")):
        for e in TpdArchive(tpi).entries:
            other_names[tq_hash(e.name)] = e.name

    records: list[dict] = []
    counts = {"identical": 0, "modified": 0, "new": 0, "new-unnamed": 0,
              "code-skipped": 0, "dup-of-extracted": 0}
    extracted_content: dict[str, str] = {}    # content hash -> library path
    t0 = time.time()
    i = 0
    for name, source, read, size in iter_other_assets(other, other_names):
        i += 1
        if i % 2000 == 0:
            print(f"  other: {i} files  ({time.time()-t0:.0f}s)", flush=True)
        ext = Path(name).suffix.lower() if name else ""
        data = read()
        if (ext in CODE_EXT) or data[:2] == b"MZ":
            counts["code-skipped"] += 1
            records.append({"name": name, "source": source, "size": size,
                            "status": "code-skipped"})
            continue
        h = HASH(data)
        key = name.replace("\\", "/").lower() if name else None
        # `base_locator` deliberately has no entry for empty content, so an
        # empty file falls through and is EXTRACTED rather than aliased. It
        # costs nothing to store and the filemap then says what it is instead
        # of pointing at an unrelated file that happened to be empty too.
        if h in base_content and h in base_locator:
            counts["identical"] += 1
            if server and key:
                filemap[key] = base_locator[h] + [source]
            continue
        if name is None:
            status = "new-unnamed"
        else:
            nh = tq_hash(name)
            in_base = nh in base_nh or name.lower() in base_loose
            status = "modified" if in_base else "new"
        counts[status] += 1
        rec = {"name": name, "source": source, "size": len(data),
               "status": status, "hash": h}
        if extract_to is not None:
            if h in extracted_content:
                counts["dup-of-extracted"] += 1
                rec["duplicateOf"] = extracted_content[h]
            else:
                if name is None:
                    label, sniffed = detect_magic(data[:16])
                    rel = f"garments-unnamed/{Path(source).stem}/{h[:12]}{sniffed}"
                else:
                    rel = "assets/" + fs_safe("/".join(
                        p for p in name.split("/") if p not in ("", ".", "..")))
                out = extract_to / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                extracted_content[h] = rel
                rec["library"] = rel
            if server:
                if key:
                    filemap[key] = ["l", extracted_content[h], source]
                elif extracted_content[h].startswith("garments-unnamed/"):
                    # an unnamed entry extracted under its content hash is
                    # addressed by that path; an unnamed entry whose bytes
                    # duplicate a *named* file needs no entry at all -- the
                    # named key already reaches the content.
                    filemap[extracted_content[h]] = \
                        ["l", extracted_content[h], source]
        records.append(rec)

    print(f"other client: {i} files total  ({time.time()-t0:.0f}s)", flush=True)

    if server and extract_to is not None:
        sdir = extract_to / "servers" / server
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "filemap.json").write_text(
            json.dumps(filemap, indent=0, sort_keys=True), "utf-8")
        ini_src = other / "ini"
        if ini_src.is_dir():
            shutil.copytree(ini_src, sdir / "ini", dirs_exist_ok=True)
        version = None
        vd = other / "version.dat"
        if vd.is_file() and vd.stat().st_size < 64:
            version = vd.read_text("latin-1", errors="replace").strip()
        (sdir / "profile.json").write_text(json.dumps({
            "server": server,
            "client": str(other),
            "clientVersion": version,
            "importedAt": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "baseline": str(root),
            "counts": counts,
            "files": len(filemap),
        }, indent=1), "utf-8")
        print(f"server profile -> {sdir}  ({len(filemap)} paths)", flush=True)

    return {"baseline": str(root), "other": str(other),
            "otherFiles": i, "counts": counts, "records": records}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--other", required=True, help="the other client's root")
    coroot.add_root_argument(ap)
    ap.add_argument("--extract-to", metavar="DIR",
                    help="write unique assets into DIR (library tree)")
    ap.add_argument("--server", metavar="NAME",
                    help="also write DIR/servers/NAME/ -- the filemap and "
                         "ini-table snapshot core/colibrary.py needs to "
                         "resolve this client's linkages (needs --extract-to)")
    ap.add_argument("--report", metavar="PATH",
                    help="write the full JSON report here")
    a = ap.parse_args(argv)
    if a.server and not a.extract_to:
        ap.error("--server requires --extract-to")

    root = coroot.root_from_args(a)
    other = Path(a.other)
    if not other.is_dir():
        print(f"--other {other}: not a directory", file=sys.stderr)
        return 2
    repo = Path(__file__).resolve().parent.parent
    extract_to = Path(a.extract_to) if a.extract_to else None

    doc = diff(root, other, repo, extract_to, a.server)

    c = doc["counts"]
    print()
    print(f"identical        {c['identical']:>7}")
    print(f"modified         {c['modified']:>7}")
    print(f"new              {c['new']:>7}")
    print(f"new (unnamed)    {c['new-unnamed']:>7}")
    print(f"duplicate bytes  {c['dup-of-extracted']:>7}")
    print(f"code skipped     {c['code-skipped']:>7}")
    if a.report:
        rp = Path(a.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(doc, indent=1), "utf-8")
        print(f"\nreport -> {rp}")
    if extract_to is not None:
        manifest = extract_to / "manifest.json"
        uniq = [r for r in doc["records"] if "library" in r or "duplicateOf" in r]
        manifest.write_text(json.dumps(
            {"baseline": doc["baseline"], "other": doc["other"],
             "counts": c, "files": uniq}, indent=1), "utf-8")
        print(f"library  -> {extract_to}  ({len(uniq)} unique files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
