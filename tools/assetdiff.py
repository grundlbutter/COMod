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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import coroot                                    # noqa: E402
from tpd import TpdArchive, TPD_MAGIC            # noqa: E402
from tqhash import tq_hash                       # noqa: E402
from wdf import WdfArchive, detect_magic         # noqa: E402

#: never extracted, never diffed as "assets" -- executable code.
CODE_EXT = {".exe", ".dll", ".ocx", ".crm", ".sys"}
#: directories under either root that are logs/scratch, not shipped content.
SKIP_DIRS = {"log", "debug", "screenshot", "screenshots", "logs"}
SKIP_EXT = {".log"}

HASH = lambda b: hashlib.blake2b(b, digest_size=16).hexdigest()  # noqa: E731

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


def _walk_loose(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.lower() in SKIP_DIRS for part in rel.parts[:-1]):
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


def catalog_baseline(root: Path, names: dict[int, str] | None = None) -> dict:
    """content: set of payload hashes; nameHashes: wdf index; loose: rel->hash;
    locator: content hash -> a filemap ref saying where the baseline holds
    those bytes (named path preferred, archive-entry-by-hash otherwise)."""
    names = names or {}
    content: set[str] = set()
    name_hashes: dict[int, str] = {}          # tq name-hash -> content hash
    loose: dict[str, str] = {}                # lowercased rel path -> hash
    locator: dict[str, list] = {}             # content hash -> ["b",...]/["w",...]
    t0 = time.time()
    for arc_name in ("c3.wdf", "data.wdf"):
        p = root / arc_name
        if not p.is_file():
            continue
        with WdfArchive(p) as a:
            n = len(a.entries)
            print(f"baseline {arc_name}: {n} entries", flush=True)
            for i, e in enumerate(a.entries, 1):
                h = HASH(a.read(e))
                content.add(h)
                name_hashes[e.hash] = h
                nm = names.get(e.hash)
                if nm is not None:
                    locator.setdefault(h, ["b", nm.lower()])
                else:
                    locator.setdefault(h, ["w", arc_name, f"{e.hash:08x}"])
                _progress(arc_name, i, n, t0)
    files = _walk_loose(root)
    print(f"baseline loose files: {len(files)}", flush=True)
    for i, p in enumerate(files, 1):
        h = HASH(p.read_bytes())
        content.add(h)
        rel = _rel_name(root, p).lower()
        loose[rel] = h
        # a loose path beats an archive-by-hash ref, but never replaces an
        # already-named archive path (both resolve; keep the first name).
        if locator.get(h, ("w",))[0] == "w":
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
        if h in base_content:
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
                # unnamed entries are addressed by their library path itself
                filemap[key or extracted_content[h]] = \
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
