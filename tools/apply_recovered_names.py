#!/usr/bin/env python3
r"""
apply_recovered_names.py -- give the extracted "garments-unnamed" library files
their recovered names.

`assetdiff.py` pulls every asset unique to a community client's garments*.wdf
archives into `<library>/garments-unnamed/<archive>/<blake2b12>.<ext>` because
those WDF entries carry only a hash, not a name.  `wdf_recover.py` then recovers
many of those names (dictionary + pattern attack, seeded by a DatPkg client's
plaintext .tpi index).  This tool joins the two:

    recovered  tq_hash -> name          (wdf_recover.py, out/garments/*_names.json)
    archive    tq_hash -> payload bytes -> blake2b content hash
    manifest   content hash -> the extracted library file

so a recovered name flows to the right file on disk.  It renames
`garments-unnamed/<archive>/<hash>.<ext>` to `assets/<name>`, and rewrites the
matching `manifest.json` record (`name`, `library`, `status: new-unnamed->new`).
Entries that were only ever recorded as duplicates of an already-named baseline
asset get their `name` filled in too, in place.

Collisions with a name already present under `assets/` are never overwritten:
if the bytes are identical the unnamed copy is dropped as a duplicate; if they
differ (a same-id reskin) the file is left where it is and the clash reported.

DRY-RUN BY DEFAULT.  Pass --apply to move files and rewrite the manifest.

Usage:
    py -3 tools/apply_recovered_names.py \
        --library "<path>/COmmunity Library" \
        --root    "<path>/Zephyr Conquer 1057" \
        --names   out/garments \
        --archives garments.wdf garments1.wdf garments2.wdf garments3.wdf garments4.wdf
    # add --apply once the dry-run report looks right
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from wdf import WdfArchive                                   # noqa: E402

GARMENTS = ("garments.wdf", "garments1.wdf", "garments2.wdf",
            "garments3.wdf", "garments4.wdf")

HASH = lambda b: hashlib.blake2b(b, digest_size=16).hexdigest()   # noqa: E731

_FS_BAD = '<>:"|?*'


def fs_safe(name: str) -> str:
    """Relative path Windows accepts, altering only illegal characters."""
    return "/".join(
        "".join("_" if (c in _FS_BAD or ord(c) < 32) else c for c in part)
        for part in name.split("/"))


def load_recovered(names_dir: Path, archives) -> dict[int, str]:
    """Merge every <stem>_names.json wdf_recover emitted for these archives."""
    out: dict[int, str] = {}
    for a in archives:
        p = names_dir / f"{Path(a).stem}_names.json"
        if not p.is_file():
            continue
        raw = json.loads(p.read_text("utf-8"))
        if isinstance(raw, dict) and "resolved" in raw:
            raw = raw["resolved"]
        for k, v in raw.items():
            out[int(k, 16)] = v
    return out


def content_names(root: Path, archives, recovered: dict[int, str]
                  ) -> dict[str, set[str]]:
    """blake2b(payload) -> {recovered name, ...} for every named entry."""
    by_content: dict[str, set[str]] = defaultdict(set)
    t0 = time.time()
    for a in archives:
        path = root / a
        if not path.exists():
            print(f"  !! missing {path}", flush=True)
            continue
        ar = WdfArchive(path)
        hit = 0
        for e in ar:
            nm = recovered.get(e.hash)
            if nm is None:
                continue
            by_content[HASH(ar.read(e))].add(nm)
            hit += 1
        ar.close()
        print(f"  {a}: {hit} named payloads  ({time.time()-t0:.0f}s)", flush=True)
    return by_content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True,
                    help="the community client install (holds garments*.wdf)")
    ap.add_argument("--names", type=Path, required=True,
                    help="dir of wdf_recover <stem>_names.json outputs")
    ap.add_argument("--archives", nargs="+", default=list(GARMENTS))
    ap.add_argument("--apply", action="store_true",
                    help="actually move files and rewrite manifest.json")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()

    manifest_path = a.library / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    recovered = load_recovered(a.names, a.archives)
    print(f"recovered names: {len(recovered)}", flush=True)
    by_content = content_names(a.root, a.archives, recovered)
    print(f"distinct named payloads: {len(by_content)}", flush=True)

    actions = {"renamed": [], "named_in_place": [], "dropped_dup": [],
               "collision_kept": [], "no_match": 0}
    # a payload can carry several recovered names; rename the file to the
    # lexicographically first and record the rest as aliases in the manifest.
    def pick(content: str) -> tuple[str, list[str]]:
        names = sorted(by_content[content])
        return names[0], names[1:]

    moves: list[tuple[Path, Path]] = []
    used_targets: set[str] = set()
    for f in manifest["files"]:
        if f.get("status") != "new-unnamed" or f.get("name"):
            continue
        content = f["hash"]
        if content not in by_content:
            actions["no_match"] += 1
            continue
        name, aliases = pick(content)
        safe = fs_safe(name)
        f["name"] = name
        if aliases:
            f["aliases"] = aliases

        if "duplicateOf" in f:
            # no own file on disk -- just record the identity we recovered.
            actions["named_in_place"].append({"hash": content, "name": name})
            continue

        src = a.library / f["library"]
        dst_rel = f"assets/{safe}"
        dst = a.library / dst_rel
        if dst.exists() or dst_rel in used_targets:
            existing = dst.read_bytes() if dst.exists() else None
            if existing is not None and HASH(existing) == content:
                actions["dropped_dup"].append({"hash": content, "name": name})
                f["status"] = "identical"
                f["duplicateOf"] = dst_rel
                f.pop("library", None)
                if a.apply and src.exists():
                    src.unlink()
                continue
            actions["collision_kept"].append(
                {"hash": content, "name": name, "library": f["library"]})
            f.pop("name", None)          # not resolved after all -- leave as-is
            f.pop("aliases", None)
            continue

        used_targets.add(dst_rel)
        moves.append((src, dst))
        f["library"] = dst_rel
        f["status"] = "new"
        actions["renamed"].append({"hash": content, "name": name})

    # ---- summary -----------------------------------------------------------
    renamed = len(actions["renamed"])
    named_ip = len(actions["named_in_place"])
    dropped = len(actions["dropped_dup"])
    clash = len(actions["collision_kept"])
    print("\n=== plan ===")
    print(f"  files to rename into assets/ : {renamed}")
    print(f"  dup entries named in place   : {named_ip}")
    print(f"  dropped as existing dup      : {dropped}")
    print(f"  collisions left in place     : {clash}")
    print(f"  unnamed still unresolved     : {actions['no_match']}")
    for ex in actions["renamed"][:8]:
        print(f"    + assets/{fs_safe(ex['name'])}")

    if a.apply:
        moved = 0
        for src, dst in moves:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
            moved += 1
        # refresh counts
        counts = Counter(f.get("status", "?") for f in manifest["files"])
        manifest.setdefault("counts", {})
        manifest["counts"].update(dict(counts))
        manifest["counts"]["named_from_recovery"] = renamed + named_ip
        manifest_path.write_text(json.dumps(manifest, indent=1), "utf-8")
        print(f"\napplied: moved {moved} files, manifest rewritten.")
    else:
        print("\n(dry run -- nothing written; pass --apply to commit)")

    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(actions, indent=1), "utf-8")
        print(f"report -> {a.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
