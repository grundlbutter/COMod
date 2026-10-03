#!/usr/bin/env python3
r"""clientconv.py -- which ASSET CONVENTIONS a client uses, as one row per install.

Static and read-only. Opens no archive, launches nothing, writes nothing to any
install. It answers one question per client: which side of each convention
change does this install sit on?

WHY IT EXISTS
-------------
The conventions moved a long way between 6090 and 7878, and the move was not
gradual. Measured 2026-08-28:

    6090 6271   .wdf archives, c3/mesh, 4-node meshes, no Lua
    6609        BOTH mesh trees, c3/body at 15 nodes, Lua = 5 files
    7632+       no .wdf at all, c3/body only at 18 nodes, Lua in the hundreds

6609 introduces both new conventions in half-built form, so it looks new and
behaves partial. The interesting question is where in the gap between 6609 and
7632 each change actually lands, and that is answered by measuring clients as
they arrive rather than by re-deriving a pile of shell one-liners each time and
hoping they were the same ones.

THE ROOT PROBLEM, WHICH IS NOT A SIDE ISSUE
-------------------------------------------
Four of the five clients added on 2026-08-28 do not have their install at the
directory they were dropped in:

    Clients/6271/Conquer Online 3.0/     a branded distribution nests one level
    Clients/<id>/app/                    innoextract renders Inno's {app} constant

Pointed at the outer directory, `coroot.base_id` returns `unkeyed` -- which is
the same answer a BROKEN install gives, so the failure reads as a bad client
rather than a bad path. `find_root` therefore searches down a couple of levels
for the real thing and REPORTS what it picked, rather than quietly succeeding.

THE CONTROL
-----------
An instrument that returns a negative has told you nothing until you have seen
it return a positive, and this one's whole job is to report absences -- no
`.wdf`, no `c3/mesh`, no Lua. An absence and a mis-resolved root produce the
same row.

So `--control` re-measures the four installed clients whose values are already
published and requires them to come back exactly. It has to reproduce the
SPREAD, not just one row: 6090 as archive-era, 6609 as the transition carrying
both trees, 7878 as loose-era. A tool that reported "loose era, nothing found"
for all three would pass a one-row control and be worthless.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: The nodes whose presence separates a usable donor mesh from a stripped one.
#: `v_armet` and the two weapon nodes are the ones 6090-era meshes lack.
KEY_NODES = ("v_armet", "v_l_weapon", "v_r_weapon", "v_l_slot01",
             "v_r_slot01", "v_slot", "v_zero")

_NODE = re.compile(rb"v_[a-z0-9_]+")

#: How far below the given directory to look for the real install root.
#: Two is enough for both known shapes and shallow enough not to wander into
#: a client's own content directories.
MAX_DEPTH = 2


def find_root(start) -> tuple:
    """`(root, how)` -- the directory that actually holds the install.

    `how` is "given" when the path was already right, otherwise the relative
    path that was appended. Returned rather than swallowed so a caller can
    print it: a silently-corrected root is how a measurement ends up being
    about a different install than the one somebody named.
    """
    start = Path(start)
    if not start.is_dir():
        return None, "absent"
    if (start / "ini").is_dir() or (start / "c3").is_dir():
        return start, "given"
    for depth in range(1, MAX_DEPTH + 1):
        for cand in sorted(start.glob("/".join(["*"] * depth))):
            if cand.is_dir() and ((cand / "ini").is_dir() or (cand / "c3").is_dir()):
                return cand, cand.relative_to(start).as_posix()
    return None, "no ini/ or c3/ within %d level(s)" % MAX_DEPTH


def _count(root: Path, pattern: str) -> int:
    try:
        return sum(1 for p in root.rglob(pattern) if p.is_file())
    except OSError:                                        # pragma: no cover
        return 0


def node_stats(meshdir: Path, sample: int = 60) -> dict:
    """Median distinct `v_*` nodes per mesh, and which key nodes appear."""
    if not meshdir.is_dir():
        return {"n": 0, "median": 0, "distinct": 0, "key": 0}
    meshes = sorted(p for p in meshdir.glob("*") if p.suffix.lower() == ".c3")[:sample]
    if not meshes:
        return {"n": 0, "median": 0, "distinct": 0, "key": 0}
    seen, per = set(), []
    for m in meshes:
        try:
            names = {b.decode("ascii", "replace") for b in _NODE.findall(m.read_bytes())}
        except OSError:                                    # pragma: no cover
            continue
        seen |= names
        per.append(len(names))
    per.sort()
    return {"n": len(per), "median": per[len(per) // 2] if per else 0,
            "distinct": len(seen), "key": sum(1 for k in KEY_NODES if k in seen)}


def profile(path) -> dict:
    """Every convention marker for one install, or a row saying why not."""
    root, how = find_root(path)
    if root is None:
        return {"given": str(path), "root": None, "how": how}
    c3 = root / "c3"
    out = {
        "given": str(path), "root": str(root), "how": how,
        "wdf": sorted(p.name for p in root.glob("*.wdf")),
        "packs": sorted(p.name for p in root.glob("*.dat")),
        "c3_mesh": len(list((c3 / "mesh").glob("*"))) if (c3 / "mesh").is_dir() else None,
        "c3_body": len(list((c3 / "body").glob("*"))) if (c3 / "body").is_dir() else None,
        "c3_texture": (len(list((c3 / "texture").glob("*")))
                       if (c3 / "texture").is_dir() else None),
        "ini_files": _count(root / "ini", "*"),
        "lua": _count(root, "*.lua"), "json": _count(root, "*.json"),
        "xml": _count(root, "*.xml"), "dbc": _count(root, "*.dbc"),
        "dds": _count(root, "*.dds"),
    }
    # A tree with c3/ but no ini/ is a PARTIAL extraction, not a client that
    # ships no tables. Reporting lua=0 for it would be the same conflation this
    # whole file exists to avoid: "nothing found" and "nowhere looked" must not
    # print identically. Every count below is suspect in that state, so say so
    # once, loudly, rather than emitting a row that reads as measurement.
    # EXISTS is not the test -- an interrupted extraction leaves an EMPTY
    # ini/ behind, which passes an is_dir() check and then reports zero
    # tables as though it had counted them. Measured on 7632, whose
    # app/ini/ exists with 0 files after a filter that matched nothing.
    out["partial"] = (c3.is_dir() and out["ini_files"] == 0)
    out["markers"] = markers(root)
    out["renderer"] = renderer(out["markers"])
    exe = root / "Conquer.exe"
    out["exe_mb"] = round(exe.stat().st_size / 1e6, 2) if exe.is_file() else None
    out["dlls"] = len(list(root.glob("*.dll")))
    out["maps"] = len(list((root / "map").glob("*"))) if (root / "map").is_dir() else None
    out["nodes_body"] = node_stats(c3 / "body")
    out["nodes_mesh"] = node_stats(c3 / "mesh")
    out["era"] = era(out)
    return out


#: Functional markers. Each is a FILE OR DIRECTORY the client ships, chosen
#: because its presence changes what the client DOES rather than how its assets
#: are filed. Grouped by subsystem so a row reads as capabilities, not trivia.
#:
#: Cheap on purpose: every one is a top-level existence check, so widening the
#: table costs nothing per client. The recursive counts stay few and targeted --
#: a full rglob over a 4 GB install is minutes, and thirty of them is an hour.
MARKERS = {
    # renderer generation -- the DX8/DX9 pair ships side by side for a while
    "graphic.dll":            ("render", "gfx8"),
    "graphicDX9.dll":         ("render", "gfx9"),
    "Env_DX8":                ("render", "env8"),
    "Env_DX9":                ("render", "env9"),
    "C3_CORE_DLL.dll":        ("render", "c3core"),
    "C3RequireCheckDX9.dll":  ("render", "req9"),
    # packaging / archive readers
    "TqPackage.dll":          ("pack", "pkg"),
    "TqPackage9.dll":         ("pack", "pkg9"),
    "TqPackageWdf.dll":       ("pack", "pkgwdf"),
    "ndCompress.dll":         ("pack", "ndcomp"),
    # anti-cheat, three generations
    "AntiRobotClient.dll":    ("guard", "antirobot"),
    "TQAnp.dll":              ("guard", "tqanp"),
    "IsecPlus":               ("guard", "isec"),
    # audio stack
    "NDSound.dll":            ("audio", "ndsound"),
    "OpenAL32.dll":           ("audio", "openal"),
    # UI / scripting / content systems
    "Flash.ocx":              ("ui", "flash"),
    "Script":                 ("script", "scriptdir"),
    "video":                  ("media", "video"),
    "armetmotion":            ("anim", "armet"),
    "miscmotion":             ("anim", "misc"),
    "gw":                     ("content", "gw"),
    "data2":                  ("content", "data2"),
    # network
    "Net.dll":                ("net", "net"),
    "Server.dat":             ("net", "serverdat"),
}


def markers(root: Path) -> dict:
    """Which functional markers this install ships. Existence checks only."""
    out = {}
    for name, (group, key) in MARKERS.items():
        out[key] = (root / name).exists()
    return out


def renderer(m: dict) -> str:
    """What the client can draw with, from the DLLs it ships.

    Reported as the PAIR rather than a single generation, because the DX9
    rollout ships both for several versions -- calling such a client "DX9"
    hides that the DX8 path is still there and still what it uses by default.
    """
    if m.get("gfx9") and m.get("gfx8"):
        return "DX8+DX9"
    if m.get("gfx9"):
        return "DX9"
    if m.get("gfx8"):
        return "DX8"
    return "?"


def era(p: dict) -> str:
    """Which side of the break, from the markers rather than from the number.

    Deliberately keyed on `.wdf` and the mesh trees, not on the version in the
    directory name: the point of this tool is to find where a convention moved,
    and a classifier that reads the answer off the version number could never
    discover that it moved somewhere unexpected.
    """
    has_wdf, mesh, body = bool(p["wdf"]), p["c3_mesh"], p["c3_body"]
    if mesh and body:
        return "TRANSITION"
    if has_wdf or (mesh and not body):
        return "ARCHIVE"
    if body and not mesh:
        return "LOOSE"
    return "UNKNOWN"


#: Published values, measured 2026-08-28. `--control` requires these back
#: EXACTLY. They span all three eras on purpose.
CONTROL = {
    "6090": {"era": "ARCHIVE",    "wdf": 2, "c3_mesh": 1140, "c3_body": None},
    "6609": {"era": "TRANSITION", "wdf": 2, "c3_mesh": 1331, "c3_body": 713},
    "7878": {"era": "LOOSE",      "wdf": 0, "c3_mesh": None, "c3_body": 601},
}


def control(clients: Path) -> list:
    """`[]` when every published client re-measures exactly."""
    bad = []
    for name, want in CONTROL.items():
        got = profile(clients / name)
        if got.get("root") is None:
            bad.append(f"{name}: root not found ({got['how']})")
            continue
        if got["era"] != want["era"]:
            bad.append(f"{name}: era {got['era']}, expected {want['era']}")
        if len(got["wdf"]) != want["wdf"]:
            bad.append(f"{name}: {len(got['wdf'])} wdf, expected {want['wdf']}")
        for k in ("c3_mesh", "c3_body"):
            if got[k] != want[k]:
                bad.append(f"{name}: {k}={got[k]}, expected {want[k]}")
    eras = {CONTROL[n]["era"] for n in CONTROL}
    if len(eras) < 3:
        bad.append("the control does not span all three eras; it cannot show "
                   "the tool discriminates")
    return bad


def row(p: dict) -> str:
    if p.get("root") is None:
        return "%-10s  ROOT NOT FOUND -- %s" % (Path(p["given"]).name, p["how"])
    n = p["nodes_body"] if p["nodes_body"]["n"] else p["nodes_mesh"]
    if p.get("partial"):
        # Mesh figures survive -- they were measured on files that ARE present.
        # The table counts do not, and are printed as "?" rather than as zero.
        return ("%-10s %-11s PARTIAL TREE (c3/ present, ini/ absent) "
                "body=%-6s nodes=%-3s key=%d/7   ini/lua/json UNMEASURED%s"
                % (Path(p["given"]).name, p["era"],
                   p["c3_body"] if p["c3_body"] is not None else "-",
                   n["median"] or "-", n["key"],
                   "" if p["how"] == "given" else "   root: ./%s" % p["how"]))
    return ("%-10s %-11s wdf=%-2d mesh=%-6s body=%-6s tex=%-6s ini=%-5d "
            "lua=%-5d json=%-5d nodes=%-3s key=%d/7%s"
            % (Path(p["given"]).name, p["era"], len(p["wdf"]),
               p["c3_mesh"] if p["c3_mesh"] is not None else "-",
               p["c3_body"] if p["c3_body"] is not None else "-",
               p["c3_texture"] if p["c3_texture"] is not None else "-",
               p["ini_files"], p["lua"], p["json"],
               n["median"] or "-", n["key"],
               "" if p["how"] == "given" else "   root: ./%s" % p["how"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="install roots (or their parents)")
    ap.add_argument("--clients", help="a Clients/ directory; measures every entry")
    ap.add_argument("--control", action="store_true",
                    help="re-measure the published clients and stop unless exact")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    clients = Path(a.clients) if a.clients else None
    if a.control:
        if clients is None:
            print("--control needs --clients"); return 2
        bad = control(clients)
        print("CONTROL: re-measuring 6090 (archive), 6609 (transition), 7878 (loose)")
        if bad:
            print("  FAILED -- not reporting anything measured with this tool:")
            for b in bad:
                print("    " + b)
            return 1
        print("  all three reproduce exactly, spanning all three eras.\n")

    targets = [Path(p) for p in a.paths]
    if clients and not targets:
        targets = sorted(p for p in clients.iterdir() if p.is_dir())
    if not targets:
        print("nothing to measure: pass paths or --clients DIR"); return 2

    rows = [profile(t) for t in targets]
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        print(row(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
