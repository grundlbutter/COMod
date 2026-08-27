#!/usr/bin/env python3
r"""lookswap.py -- put one look's art onto another look, for the kinds that
do not split.

    py -3 tools/lookswap.py --root <install> --kind monster --target 103 --donor 105
    py -3 tools/lookswap.py --root <install> --kind weapon  --target 410003 --donor 421003 --dry-run

WHY THIS EXISTS
---------------
`npcsplit`/`npcstage` handle NPCs, where several rows share one motion id and
a swap must first FORK the shared art so the change lands on one NPC instead
of forty. Monsters, weapons, mounts, bodies and heads have no such sharing:
each look owns its art outright, so the change is a straight replacement.

The swap page already said so in prose -- *"Each monster has its own art, so
there is nothing to split -- this is a straight replacement"* -- and then
offered a button that ran the NPC planner anyway. Measured 2026-08-26, every
monster and every weapon row failed the same way::

    GET /api/swap/instructions?npc=103     -> exit 2
    npcsplit: "npc '103' matches 0 rows in ini/npc.json; need exactly one"

A monster body id looked up in the NPC table. npcsplit refusing rather than
guessing was correct; the caller was wrong. This module is the planner that
caller should have had.

WHAT IT GUARANTEES, COPIED FROM npcsplit DELIBERATELY
-----------------------------------------------------
* **It never writes into the game install.** Every write is confined to
  `comod.STAGE` through `safepath.confine`, and `comod install` remains the
  only thing that touches the install.
* **It refuses rather than guessing.** An unknown look, a donor whose art
  does not resolve, or a target equal to the donor is an exit-2 refusal with
  the reason, never a partial stage.
* **`--dry-run` writes nothing** and prints the same plan the real run
  executes, so "show me what to change" and "do it" cannot disagree.

THE STAGE TREE IS comod's, NEVER A LOCAL COPY
---------------------------------------------
`comod.STAGE` is imported, not re-derived. Four modules once kept their own
`mods/stage` literal after comod moved to `Installed/stage`, and a user's
staged split landed where the installer never reads -- Mod staging reported
"Nothing is staged" over nine real files. `tests/test_one_stage_tree.py`
holds that shut now.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
for _p in (str(HERE), str(PROJECT / "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import safepath                                            # noqa: E402


class Refused(Exception):
    """Raised instead of staging something this module cannot vouch for."""


def _stage_dir() -> Path:
    import comod                                           # noqa: PLC0415
    return comod.STAGE


def _kinds():
    import swapplan                                        # noqa: PLC0415
    return swapplan.KIND_BY_KEY


def look_rows(cat, kind_key: str) -> "dict[str, dict]":
    """Every look of one kind, as ``{id: {label, mesh, texture}}``.

    Sourced exactly the way `/api/swap/browse` sources it -- models query for
    the directory kinds, builder options for the flat equipment kinds -- so
    what this planner can act on is what the panel can offer. A second way of
    listing looks is a second answer to "does this exist".
    """
    k = _kinds().get(kind_key)
    if k is None:
        raise Refused("unknown kind %r; known: %s"
                      % (kind_key, ", ".join(sorted(_kinds()))))
    out: dict = {}
    if k.source == "models":
        res = cat.models.query(kind=k.ref, text="", playable_only=False)
        for m in res.get("matched", []) or []:
            j = m.to_json()
            out[str(j.get("id", ""))] = {"label": j.get("label") or j.get("id"),
                                         "mesh": j.get("mesh") or "",
                                         "texture": j.get("texture") or ""}
    else:
        idx = cat.builder
        for o in ((idx.options.get(k.ref) or []) if idx else []):
            out[str(o.ident)] = {"label": o.name or o.ident,
                                 "mesh": o.mesh or "", "texture": o.texture or ""}
    if kind_key == "monster":
        # The recolours the catalogue cannot see. Without this the planner
        # knows 66 looks while the panel offers 148, and every recolour a
        # user picks is refused as "not a monster look in this install".
        for ident, row in monster_morph_rows(cat, out).items():
            out.setdefault(ident, {"label": row["label"], "mesh": row["mesh"],
                                   "texture": row["texture"]})
        # Creature names WHEN PRESENT. Empty unless a server dump has been
        # crawled, in which case every row whose mesh is named gets the
        # creature(s) instead of a bare body id. A recolour is deliberately
        # left with its own label: it wears a different skin, so calling it by
        # the base family's creature names would be a claim nobody measured.
        names = monster_names(cat)
        if names:
            for ident, row in out.items():
                if row.get("recolourOf"):
                    continue
                lab = label_for(ident, row.get("mesh", ""), names)
                if lab != "Monster %s" % ident:
                    row["label"] = lab
    return out


def monster_names(cat) -> "dict[str, list]":
    r"""``{mesh_path: [creature names]}`` -- empty unless a dump was crawled.

    **Nothing client-side links a monster's art to its name.** `bodyType` is 0
    on every row of `ini/monster.json` (measured here: 0 of 374 usable), and
    both `tools/artcrawl.py` and `core/monsterart.py` say the binding is the
    server's `monstertype.Mesh`. So a name is only ever available if somebody
    ran `artcrawl.py --sql <dump> --write`, which emits `out/artcrawl/*.json`.

    This reads that index when it exists and returns nothing when it does not.
    That is the whole of "show creature names WHEN PRESENT": the path is
    wired, and it lights up the moment the data is there. It never guesses --
    `ini/monster.json` has a `type` column that coincidentally lands on 13 of
    374 art directories, and using it would put a plausible wrong creature
    name on a model, which is worse than a body id.

    **Keyed by MESH, not by monster id, and the value is a LIST.** One
    directory commonly serves several monsters -- `monsterart` records that
    `0103` serves seven -- so there is no single right label and picking one
    would be an arbitrary choice presented as a fact.
    """
    out: dict = {}
    try:
        import coroot                                      # noqa: PLC0415
        d = coroot.derived_path("out/artcrawl", getattr(cat, "root", None))
    except Exception:
        return out
    if not d or not Path(d).is_dir():
        return out
    for f in sorted(Path(d).glob("*.json")):
        try:
            doc = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for e in (doc.get("entities") or []):
            if e.get("kind") != "monster":
                continue
            mesh, name = e.get("geometry") or "", (e.get("name") or "").strip()
            if mesh and name and name not in out.setdefault(mesh, []):
                out[mesh].append(name)
    for mesh in out:
        out[mesh].sort()
    return out


def label_for(body: str, mesh: str, names: "dict[str, list]") -> str:
    """The row's label: creature names when present, the body id otherwise.

    The body id is ALWAYS shown, even when names exist, because the id is
    what the swap acts on and what a refusal will quote back.
    """
    got = names.get(mesh) or []
    if not got:
        return "Monster %s" % body
    shown = ", ".join(got[:3]) + ("..." if len(got) > 3 else "")
    return "%s (body %s)" % (shown, body)


def monster_morph_rows(cat, base_rows: "dict[str, dict]") -> "dict[str, dict]":
    r"""Monster recolours, keyed like `look_rows` -- the ONE definition.

    A monster body id either owns a directory or is a COLOUR MORPH shipping
    **a texture and no geometry**, borrowing another family's mesh
    (`core/monsterart.py` holds the rule). The catalogue enumerates
    directories, so morphs are invisible to it: measured on CCO, 66 rows
    catalogued against 148 looks that can actually be worn.

    `coviewer.api_swap_browse` calls THIS, rather than keeping its own copy.
    The first version had one here and one there, and the panel immediately
    offered 148 looks the planner would refuse for 82 of them -- more rows
    that all dead-end, which is worse than fewer that work. That is the same
    second-definition defect as the mods/stage split, one day later.
    """
    import re                                              # noqa: PLC0415
    try:
        import monsterart                                  # noqa: PLC0415
    except Exception:
        return {}
    paths = getattr(cat, "all_paths", None) or []
    dirs = {m.group(1) for p in paths
            for m in [re.match(r"c3/monster/([^/]+)/", p, re.I)] if m}
    if not dirs:
        return {}
    out: dict = {}
    for p in paths:
        m = re.match(r"c3/texture/(\d{9})\.dds$", p, re.I)
        if not m:
            continue
        tid = m.group(1)
        if not tid.endswith("000000"):
            continue
        body = tid[:3]
        if body in dirs or body in base_rows:
            continue
        try:
            got = monsterart.split_colour(int(body), dirs)
        except Exception:
            got = None
        texture = "c3/texture/%s.dds" % tid
        if not got:
            out[body] = {"label": "Monster %s" % body, "mesh": "",
                         "texture": texture, "recolourOf": "",
                         "why": ("this install ships a body texture for %s but "
                                 "no geometry, and it does not resolve as a "
                                 "recolour of any shipped body" % body)}
            continue
        base = str(got[0])
        base_row = base_rows.get(base) or base_rows.get(base.lstrip("0")) or {}
        mesh = base_row.get("mesh", "")
        out[body] = {
            "label": "Monster %s (recolour of %s)" % (body, base),
            "mesh": mesh, "texture": texture, "recolourOf": base,
            "why": ("" if mesh else
                    "resolves as a recolour of body %s, but %s's own geometry "
                    "does not resolve in this install" % (base, base))}
    return out


def plan(cat, kind_key: str, target: str, donor: str) -> dict:
    """What a straight replacement would change. Reads only."""
    rows = look_rows(cat, kind_key)
    if target == donor:
        raise Refused("target and donor are the same look (%s); "
                      "there is nothing to change" % target)
    for role, ident in (("target", target), ("donor", donor)):
        if ident not in rows:
            near = [r for r in rows if r.startswith(ident[:2])][:6]
            raise Refused(
                "%s %r is not a %s look in this install (%d known)%s"
                % (role, ident, kind_key, len(rows),
                   ("; did you mean one of %s?" % ", ".join(near)) if near else ""))
    t, d = rows[target], rows[donor]

    steps = []
    for slot in ("mesh", "texture"):
        dst, src = t.get(slot) or "", d.get(slot) or ""
        if not dst:
            raise Refused("target %s has no %s path in this install, so there "
                          "is nothing to replace" % (target, slot))
        if not src:
            raise Refused("donor %s has no %s that resolves in this install; "
                          "it cannot be copied onto %s" % (donor, slot, target))
        if not cat.assets.exists(src):
            raise Refused("donor %s names %s but that file does not resolve "
                          "here -- refusing to stage an empty %s"
                          % (donor, src, slot))
        steps.append({"slot": slot, "write": dst, "from": src,
                      "same": dst == src})
    return {"kind": kind_key, "target": target, "donor": donor,
            "targetLabel": t["label"], "donorLabel": d["label"],
            "steps": steps,
            "note": ("Straight replacement: this kind does not share art "
                     "between looks, so nothing else changes.")}


def stage(cat, p: dict) -> dict:
    """Write the plan into comod's stage tree. Nothing else is touched."""
    stage_dir = _stage_dir()
    written = []
    for s in p["steps"]:
        data = cat.assets.read(s["from"])
        if not data:
            raise Refused("donor file %s read as empty; refusing to stage it "
                          "over %s" % (s["from"], s["write"]))
        dest = safepath.confine(stage_dir, s["write"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append({"path": s["write"], "bytes": len(data),
                        "dest": str(dest)})
    return {"stage": str(stage_dir), "written": written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="put one look's art onto another, for kinds that do not split")
    ap.add_argument("--root", required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--donor", required=True)
    ap.add_argument("--dry-run", action="store_true", dest="dry")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    import contextlib                                      # noqa: PLC0415
    import coviewer                                        # noqa: PLC0415

    # THE BANNER IS LAZY, and that is what made this subtle. Catalogue
    # construction prints some of it; the rest arrives when `plan()` first
    # touches cat.models / cat.builder -- including a 40s "unified index
    # built live" line. Wrapping only the constructor left those on stdout in
    # front of the JSON, so the viewer endpoint received
    # `[coviewer] loaded 11 appearance tables...{"kind":` and could not parse
    # it. Everything that can print is inside the redirect; the answer is
    # written to the stdout captured before it.
    real_stdout = sys.stdout
    p = None
    refused = None
    with contextlib.redirect_stdout(sys.stderr):
        cat = coviewer.Catalog(Path(a.root))
        try:
            p = plan(cat, a.kind, str(a.target), str(a.donor))
            if not a.dry:
                p.update(stage(cat, p))
        except Refused as e:
            p, refused = None, str(e)

    if refused is not None:
        if a.json:
            print(json.dumps({"ok": False, "refused": refused}), file=real_stdout)
        else:
            print("REFUSED: %s" % refused, file=sys.stderr)
        return 2

    p["ok"] = True
    p["staged"] = not a.dry
    if a.json:
        print(json.dumps(p, indent=1), file=real_stdout)
        return 0

    out = real_stdout
    print("%s  %s -> %s" % (a.kind, p["donorLabel"], p["targetLabel"]), file=out)
    print("  %s" % p["note"], file=out)
    for s in p["steps"]:
        print("    %-8s %s" % (s["slot"], s["write"]), file=out)
        print("             <- %s" % s["from"], file=out)
    if a.dry:
        print("", file=out)
        print("THIS COMMAND WROTE NOTHING. Re-run without --dry-run to stage it.",
              file=out)
    else:
        print("", file=out)
        print("staged into %s" % p["stage"], file=out)
        for w in p["written"]:
            print("  %8d bytes  %s" % (w["bytes"], w["path"]), file=out)
        print("", file=out)
        print("Nothing has reached the game install. To apply:", file=out)
        print("    py -3 tools/comod.py install --dry-run", file=out)
        print("    py -3 tools/comod.py install --yes", file=out)
        print("    py -3 tools/comod.py uninstall --yes    # reverts it", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
