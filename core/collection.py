#!/usr/bin/env python3
r"""
collection.py -- the curated asset collection inside a COmmunity Library.

Everything else in this project *describes* what a client shipped.  This is
the one place that is **chosen**: assets you have looked at, judged, and kept.
It is meant to become a cleared repository -- art you are willing to reuse,
filed so it can be found, viewed, and swapped in.

Layout, one folder per category::

    <library>/Collection/collection.json     the index (every entry)
    <library>/Collection/Weapons/            one folder per category
        zephyr-0816.c3                       the mesh
        zephyr-0816.dds                      its skin  (same stem, so the
        zephyr-0816_s1.dds                   viewer pairs them for free)
        zephyr-0816.json                     provenance for this entry alone

    <library>/servers/collection/            published on every change, so the
        filemap.json                         viewer browses the Collection as
        profile.json                         it browses any imported client

An entry records where it came from and what it may replace:

    id            stable slug, unique in the collection
    category      Weapons / Characters / ... (see CATEGORIES)
    name          your label, free text
    server        which imported client it came from ("" = the base install)
    sourceMesh    the logical path it was collected from
    sourceTexture the skin that was paired with it
    swapFor       the logical path this is intended to replace, if any
    sha           content hashes, so a re-collect is recognised
    collectedAt   ISO-8601 UTC
    provenance    WHICH INSTALL the bytes came from -- see below

`server` AND `provenance` ARE DIFFERENT QUESTIONS
-------------------------------------------------
`server` is a **library-local view name**: the folder under
``<library>/servers/`` the collector was browsing, ``""`` meaning "the base
install being served".  `by_source` and `_unique_id` both key on it, so it is
part of an entry's *identity* and re-collect semantics -- not a label that can
be repurposed.

More decisively, it cannot answer the question at all.  Assets collected from
CCO and from patch5517 both carry ``server = ""``, because in both cases the
collector was browsing the base install rather than an imported client.  A
reader asking "which client's conventions describe these bytes?" gets the same
answer for two clients that disagree.

`provenance` is the answer, and it is `core/provenance.stamp()`'s record
verbatim -- ``{schema, base_id, kind, fingerprint, install, generated, tool}``.
``base_id`` is ``<kind>-<fingerprint>`` where the fingerprint is a sha256 over
``ini/``, which is exactly the layer that differs between installs and exactly
what `RolePart.ini [Dumy]` is read from.  **No absolute paths**: `provenance`
forbids them by design, so an entry copied to another machine still names its
source install in terms that machine can check.

WHY THIS MATTERS TO SOMETHING VISIBLE
-------------------------------------
Which mesh chunks are attachment points rather than geometry is
``ini/RolePart.ini [Dumy]``, and that list is per install: CCO and 7878
declare 52 names including ``v_zero``; 5065/5165/5517/6609/Zephyr declare 7.
A chunk not on the list is *drawn* -- the textured box at a model's feet.  A
library entry previewed while some other install is being served was being
classified with the served install's vocabulary, which is right for install
assets and wrong for library ones.  The entry has to carry its own answer.

``provenance`` is ``None`` -- or, on entries written before this field
existed, absent -- when nothing recorded it.  That is **not** the same as
"came from the install you happen to be serving", and no reader may treat it
so silently; `provenance_state` is how a caller says which of the two it has.

The category set is deliberately the game's own vocabulary rather than a
generic "models/textures" split, because the point is finding a thing to put
in a slot: a headgear and a weapon are different questions even though both
are a mesh plus a skin.

Usage::

    from collection import Collection
    col = Collection(library)
    col.add(category="Weapons", name="Frost Katana",
            mesh_bytes=..., mesh_name="0816.c3",
            skins=[("0816.dds", b"...")], server="zephyr",
            source_mesh="zephyr/garments/0816.c3")
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

#: Folder name -> what belongs there.  Order is the order the UI offers them.
CATEGORIES: dict[str, str] = {
    "Characters": "player bodies and full character models",
    "Garments": "body art worn over a character (fashion, armour skins)",
    "Weapons": "held weapons, one- and two-handed",
    "Shields": "off-hand shields",
    "Headgear": "helmets, hats, hair pieces",
    "Mounts": "rideable models and their tack",
    "NPCs": "town and quest NPCs",
    "Monsters": "hostile models",
    "Effects": "particle and glow effects",
    "Maps": "map art: ground puzzles, scenery, backdrops",
    "UI": "interface art, icons, cursors",
    "Other": "anything that does not fit above -- never a dumping ground, "
             "just an honest home for the unclassified",
}

INDEX_NAME = "collection.json"
ROOT_NAME = "Collection"

#: Part roles a **map** entry uses.  Unlike a figure's parts, each of these
#: stages to its own logical path rather than under the target: the DMap
#: names its `.pul`, and the `.ani` names its tiles, so moving any of them
#: breaks the reference that made it part of the map.  They are also the
#: roles that can be *shared* with other maps -- see `Collection.stage`.
MAP_ROLES = ("puzzle", "ani", "art")

#: The Collection browses like any imported client: it publishes a server
#: profile of its own under this name, so it gets the viewer's tree,
#: thumbnails, tags and model stage with no second code path.
PROFILE_NAME = "collection"
PROFILE_TAG = "Collection"

#: The entry key holding `core/provenance.stamp()`'s record.  Spelled once so
#: a reader grepping for it finds every site.
PROVENANCE_KEY = "provenance"

#: What a reader knows about an entry's source install.  Three states, and the
#: middle one is the whole reason there are three: an entry can *have* a
#: recorded install that this machine cannot produce, and that is a different
#: fact from having none.
#:
#: `PROV_UNRECORDED` is the resting state of every entry collected before the
#: field existed, and it **cannot be repaired retroactively** -- `sourceMesh`
#: is a logical path, which names a location inside a client and not which
#: client.  Guessing one would manufacture a provenance that reads exactly
#: like a measured one.
PROV_RECORDED = "recorded"       # a stamp is present and well-formed
PROV_UNRECORDED = "unrecorded"   # nothing recorded it; do not guess
PROV_MALFORMED = "malformed"     # something is there and is not a stamp


def entry_provenance(entry: dict) -> Optional[dict]:
    """The stamp on an entry, or ``None``.

    Never raises and never invents: a missing, null, non-dict or
    `base_id`-less value is all one answer -- *nothing vouches for this* --
    because a reader that told them apart would still have to do the same
    thing about all four.
    """
    st = (entry or {}).get(PROVENANCE_KEY)
    if not isinstance(st, dict):
        return None
    return st if str(st.get("base_id") or "").strip() else None


def provenance_state(entry: dict) -> str:
    """`PROV_RECORDED`, `PROV_UNRECORDED` or `PROV_MALFORMED`.

    Split out from `entry_provenance` because a caller *rendering* the state
    wants to distinguish "nobody recorded it" from "something is there and I
    cannot read it" -- the second is a bug report, the first is history.
    """
    raw = (entry or {}).get(PROVENANCE_KEY)
    if raw is None or raw == "":
        return PROV_UNRECORDED
    if entry_provenance(entry) is not None:
        return PROV_RECORDED
    return PROV_MALFORMED

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    out = _SLUG.sub("-", (s or "").strip().lower()).strip("-")
    return out or "item"


def _sha(b: bytes) -> str:
    return hashlib.blake2b(b, digest_size=16).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


#: A model's actions number in the dozens; a folder with more 4-digit .c3
#: files than this is a content folder, not one model's action set.
MAX_ACTIONS = 40

#: How this module files an entry's parts: ``<entry>__<role>-<name>``.
PART_MARK = "__motion-"


def action_code(mesh_stem: str, sib_stem: str) -> tuple[str, bool]:
    """The action ``sib_stem`` plays for ``mesh_stem``, and whether the match
    is *anchored* to this model's own name.

    **Three layouts ship, and knowing one of them finds nothing for the other
    two.**  Both halves of this project got that wrong in turn, which is why
    the rule lives here rather than beside either caller:

    ``c3/npc/001/1.c3``
        one directory per look; the actions are ``100.c3``, ``101.c3``,
        ``190.c3`` beside it.  *Unanchored* -- any short numeric name in the
        directory qualifies -- so it needs `MAX_ACTIONS` behind it.

    ``c3/npc/999001100.c3``
        one *flat* family, ``999<type><action>``, all sharing `c3/npc/`.
        Anchored on the 999+type prefix.  The storekeeper collected from
        this layout kept no animation at all: nine digits is not "four or
        fewer", and its directory holds 127 files, so the content-folder cap
        discarded whatever survived.

    ``collection/npcs/zephyr-npc-001.c3``
        collected; the parts carry the entry's own name because one category
        folder holds every entry on that shelf.  Anchored on the stem.

    Returns ``("", False)`` when the sibling is not an action of this mesh.
    """
    if not sib_stem or sib_stem == mesh_stem:
        return "", False
    # collected: the part names the entry it belongs to
    marker = mesh_stem + PART_MARK
    if sib_stem.startswith(marker):
        return sib_stem[len(marker):], True
    # the flat family: 999 + look + a 3-digit action, both sides
    if _is_flat_action(mesh_stem):
        # Its layout is known, so the guess below must not also apply: the
        # flat family shares one directory with a stray `c3/npc/1.c3`, which
        # the short-numeric rule would offer as "action 1" of every NPC in it.
        if _is_flat_action(sib_stem) and sib_stem[:-3] == mesh_stem[:-3]:
            return sib_stem[-3:], True
        return "", False
    # one directory per look: a short numeric name is an action code
    if sib_stem.isdigit() and len(sib_stem) <= 4:
        return sib_stem, False
    return "", False


def _is_flat_action(stem: str) -> bool:
    """``999`` + a 3- or 4-digit look + a 3-digit action.

    Nine is the minimum, which is also what separates an action from the
    family's *base* file: ``999118.c3`` and ``9990217.c3`` are geometry with
    no action code, and reading their last three digits as one would file
    them under a look that does not exist.
    """
    return len(stem) >= 9 and stem.isdigit() and stem.startswith("999")


def actions_beside(list_under, mesh: str, *,
                   record: "Optional[dict]" = None) -> list[tuple[str, str, bool]]:
    """``(code, logical, anchored)`` for every action file belonging to
    ``mesh``.

    The `MAX_ACTIONS` cap applies only to the unanchored layout, because it
    is the only one that can over-collect: the recovered garment archives are
    directories of hundreds of 4-digit models, and reading them as one
    model's action set pulled 455 unrelated meshes into a single entry.  A
    prefix-anchored match cannot make that mistake, so capping it would only
    lose real animation.

    **`MAX_ACTIONS` is a CLASSIFIER, not a truncation**, and the difference
    is the whole reason this reads ``loose = []`` rather than ``loose[:40]``.
    The count is evidence about *what kind of directory this is*
    (`MAX_ACTIONS`' own comment: "a folder with more 4-digit .c3 files than
    this is a content folder, not one model's action set"), and once it says
    "content folder" every unanchored match in it is a false positive.
    Keeping the first 40 would keep 40 arbitrary false positives and look
    tidier doing it. **Do not "fix" this by truncating.**

    Pass ``record`` -- a dict this fills in -- to hear the judgement when it
    fires. Without it the discard is invisible: a caller cannot tell "this
    directory has no unanchored actions" from "this directory was judged a
    content folder and N candidates were thrown away", because both are an
    empty list. The classification is correct; its *silence* was not, and it
    was costing real directories (below).

    ``record`` is keyword-only and defaults to None, so **every existing
    caller receives exactly what it received before.**
    """
    key = (mesh or "").replace("\\", "/").lower()
    if not key.endswith(".c3"):
        return []
    d = key.rsplit("/", 1)[0] + "/"
    depth = key.count("/")
    stem = key.rsplit("/", 1)[-1][:-3]
    anchored: list[tuple[str, str, bool]] = []
    loose: list[tuple[str, str, bool]] = []
    for p in sorted(list_under(d)):
        if not p.endswith(".c3") or p == key or p.count("/") != depth:
            continue
        code, is_anchored = action_code(stem, p.rsplit("/", 1)[-1][:-3])
        if not code:
            continue
        (anchored if is_anchored else loose).append((code, p, is_anchored))
    if len(loose) > MAX_ACTIONS:
        if record is not None:
            # The candidates, not just the count. "Which ones" is the next
            # question anyone asks, and a bare number sends them to re-derive
            # a list this function already had in its hand.
            record.update(discarded=len(loose), reason="content-folder",
                          threshold=MAX_ACTIONS, directory=d,
                          candidates=[p for _c, p, _a in sorted(loose)])
        loose = []
    return sorted(anchored + loose)


def action_target_name(target_stem: str, code: str, suffix: str = ".c3") -> str:
    """What an action of ``code`` is called in the *target's* naming.

    Staging a swap writes the replacement's action files into the target's
    directory, and in the flat family the action code is part of the
    filename: staging look 001 over look 002 while its actions keep their
    own names writes ``999001101.c3``, which replaces the *donor's* walk
    cycle and leaves the target's untouched -- the swap then plays the old
    animation, or none.
    """
    if _is_flat_action(target_stem) and len(code) == 3:
        return target_stem[:-3] + code + suffix
    return code + suffix


def layout_of(blob: bytes) -> str:
    """Which of the two shipped model layouts a container belongs to.

    ``per-action``  PHY + MOTI in the SAME file, one file per action. The
                    flat NPC family is this: `c3/npc/999001101.c3` carries
                    the geometry the client draws while playing action 101.
    ``motion-only`` MOTI and no geometry -- an action file that binds over a
                    separate mesh. `c3/npc/001/101.c3` is this.
    ``mesh``        geometry and no motion.

    The distinction decides whether a swap can work at all. Staging a
    motion-only file over a per-action mesh removes the geometry that path
    was supplying, and the client then plays the new motion on whatever it
    still has -- which is exactly "the animations changed but the model did
    not".
    """
    geom, moti = _has_geometry(blob), (b"MOTI" in blob)
    if geom and moti:
        return "per-action"
    if moti:
        return "motion-only"
    return "mesh"


class LayoutError(RuntimeError):
    """A swap the two files' shapes cannot express."""


def compose_action(mesh_blob: bytes, motion_blob: bytes) -> bytes:
    """One container carrying ``mesh_blob``'s geometry and ``motion_blob``'s
    motion -- what a *per-action* family needs and a motion-only file is not.

    The client ships two layouts and they do not mix.  Dropping a
    skeleton+motion model into `c3/npc/999<look><action>.c3` replaces files
    that each carry their own geometry with files that carry none, and the
    result is a model that animates differently and never changes shape.
    Composing is what makes that swap expressible: every action file gets the
    donor's mesh *and* that action's motion.

    ``MOTI`` binds to ``PHY`` **by ordinal** (`C3Mesh::SetMotion`), so the
    counts have to agree -- a composition that silently paired the wrong
    chunks would animate one submesh with another's track.  Emitted
    interleaved, PHY then MOTI per ordinal, which is the arrangement every
    per-action file in the baseline uses.
    """
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    from c3phy import C3_MAGIC, VARIANTS
    from coassets import C3File

    geom = [c for c in C3File(mesh_blob, strict=False).chunks
            if c.tag in VARIANTS]
    moti = [c for c in C3File(motion_blob, strict=False).chunks
            if c.tag == b"MOTI"]
    if not geom:
        raise LayoutError("the donor mesh carries no geometry chunk")
    if not moti:
        raise LayoutError("the action file carries no MOTI chunk")
    if len(geom) != len(moti):
        raise LayoutError(
            f"{len(geom)} geometry chunk(s) against {len(moti)} motion "
            "chunk(s); MOTI binds to PHY by ordinal, so pairing them would "
            "be a guess")

    out = bytearray(C3_MAGIC)
    for g, m in zip(geom, moti):
        for ch in (g, m):
            out += ch.tag + len(ch.body).to_bytes(4, "little") + ch.body
    return bytes(out)


def _has_geometry(blob: bytes) -> bool:
    """True if the container holds a PHY chunk of any variant."""
    try:
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parent))
        from c3phy import VARIANTS
        from coassets import C3File
        c3 = C3File(blob, strict=False)
        return any(ch.tag in VARIANTS for ch in c3.chunks)
    except Exception:
        # Unparseable: treat it as geometry so it is left alone rather than
        # swept into someone else's entry.
        return True


#: An action code in the nested per-action layout. MEASURED over every
#: cap-surviving numeric-named directory on 7878 and CCO: the codes are
#: 3-digit (100, 101, 110, 121, 190, 250, 310, 401 ...) with a 4-digit tail
#: (1114, 1501, 1560); 13,772 three-digit and 353 four-digit against 3,025
#: one-digit and 284 two-digit. **The short stems are not actions** -- `1` is
#: the model's own base mesh, and the 1-2 digit families are variant sets, not
#: action sets (see `_directory_anchored`).
_ACTION_CODE = re.compile(r"^\d{3,4}$")


def _directory_anchored(logical: str) -> bool:
    r"""True when the model's **own directory** is the anchor.

    `c3/npc/2231/100.c3` -- a directory named for the model, holding that
    model's action codes. That is a *stronger* anchor than any filename
    convention: the directory is the model, so a sibling action code in it
    cannot belong to anything else.

    Both halves are load-bearing and **each blocks a measured counterexample**
    that the other lets through. This is the guard `MAX_ACTIONS` was bought
    for, at a size the cap cannot see -- 27 and 3 models, not 455:

    ``c3/npc/10023/`` on 7878 -- 28 files, stems ``01 02 ... 11 12 ... 66``,
        every one PHY+MOTI. A tile/domino set: **27 unrelated models inside
        `c3/npc/<id>/`**, and 28 is under `MAX_ACTIONS`, so the cap never
        fires. Blocked here by the code shape: 2-digit stems are not actions.
        (This also refutes "the hazard cannot occur inside `c3/npc/<id>/`".)

    ``c3/effect/bow/`` -- stems ``179 219 69-89``, all PHY+MOTI, three
        unrelated bow effects with names that *are* action-shaped. Blocked
        here by the directory: ``bow`` is not a model id.

    And the original 455: the recovered garment archives browse as
    ``zephyr/garments``, ``zephyr/garments1`` .. -- 1,217 / 567 / 462 / 527 /
    425 four-digit models in flat, **non-numeric** directories. Refused twice
    over: this predicate says no on the directory name, and `MAX_ACTIONS`
    still condemns them exactly as before, because nothing here touches the
    classifier or what it counts.
    """
    parts = logical.rsplit("/", 2)
    if len(parts) < 3 or not parts[-2].isdigit():
        return False
    return bool(_ACTION_CODE.match(parts[-1][:-3]))


def gather_parts(read, list_under, mesh: str,
                 effects: Iterable[str] = (), *,
                 motions: "Optional[Iterable[str]]" = None,
                 ) -> list[tuple[str, str, str, bytes]]:
    """Everything that belongs with ``mesh`` beyond its own skin.

    ``motion``  MOTI-only ``.c3`` siblings in the same directory -- the old
                client keeps a model's actions in separate files
                (c3/npc/001/100.c3 and friends), and a model collected
                without them cannot move.
    ``effect``  explicit extra logical paths the caller resolved (a weapon's
                aura, its attack trail), passed in because deciding *which*
                effects belong is the caller's job, not this function's.

    ``read``/``list_under`` come from the active view, so this works for
    assets that exist only inside an imported client.

    ``motions`` REPLACES the directory sweep with an explicit list, for
    families whose actions are named by a TABLE rather than found beside the
    mesh.  It is keyword-only and defaults to None, so **every existing
    caller gets exactly the behaviour it got before**; `None` means "sweep",
    and an empty list means "the caller looked and there are none", which are
    different instructions and are kept different here.

    Weapons need it.  Measured on 6609, 2026-08-17: `c3/weapon/` is 633 files
    holding 331 single-file `.c3` meshes with no per-action siblings, so
    `actions_beside` returns `[]` for **every weapon on the install** and the
    `MAX_ACTIONS` content-folder classifier never fires.  The sweep does not
    over-collect here, which is the failure that would be noticed -- it
    under-collects to exactly zero and reports success.  See
    `core/weaponcollect.plan_files`, which reads the motion rows out of
    `weaponmotion.dbc`/`.ini` and passes them here.
    """
    out: list[tuple[str, str, str, bytes]] = []
    if motions is not None:
        for p in motions:
            try:
                blob = read(p)
            except Exception:
                continue
            # No MOTI test and no geometry test: the table NAMED this file as
            # this weapon's motion. Re-deriving that judgement from the bytes
            # would let a layout check overrule the only authority that
            # actually knows, which is what the directory rule does and why
            # it is not used here.
            out.append(("motion", p.rsplit("/", 1)[-1], p, blob))
        for e in effects:
            role = ("sound" if e.rsplit(".", 1)[-1].lower()
                    in ("wav", "mp3", "ogg") else "effect")
            try:
                out.append((role, e.rsplit("/", 1)[-1], e, read(e)))
            except Exception:
                continue
        return out
    for _code, p, anchored in actions_beside(list_under, mesh):
        try:
            blob = read(p)
        except Exception:
            continue
        if b"MOTI" not in blob:
            continue
        # A motion file is MOTI *without geometry* -- but only where that is
        # the layout. Both ship (`docs/viewer.md` §5.3): the flat NPC family
        # is per-action *meshes*, every file PHY + MOTI, so requiring no
        # geometry rejected every action the storekeeper had. The test is
        # here to stop the UNANCHORED rule swallowing a folder of unrelated
        # models -- 455 of them, once -- and an anchored match cannot.
        #
        # The NESTED layout is per-action meshes too, and had the same defect
        # the flat family had: `c3/npc/2231/{100,101,190}.c3` are PHY+MOTI, so
        # this test discarded all three and the owner collected an NPC that
        # could not move. `_directory_anchored` is the third anchor -- the
        # model's own directory -- and it is a *test on the path*, deliberately
        # not a widening of `anchored`, so `MAX_ACTIONS` still classifies on
        # exactly what it classified on before.
        if not anchored and not _directory_anchored(p) and _has_geometry(blob):
            continue
        out.append(("motion", p.rsplit("/", 1)[-1], p, blob))
    for e in effects:
        # A weapon's entry names its sounds alongside its visual effects.
        # Both travel with the asset, but calling a .wav an effect makes the
        # manifest lie about what was collected.
        role = ("sound" if e.rsplit(".", 1)[-1].lower()
                in ("wav", "mp3", "ogg") else "effect")
        try:
            out.append((role, e.rsplit("/", 1)[-1], e, read(e)))
        except Exception:
            continue
    return out


#: `c3/npc/999<look><action>.c3` -- the flat NPC family, where the filename
#: carries the look and the texture is named after the look, not the mesh.
_FLAT_NPC = re.compile(r"^c3/npc/999(?P<look>\d{3})(?P<action>\d{3})\.c3$")


def skin_destination(target: str, exists, authored=None, tables=None) -> str:
    """Where a replacement skin belongs under ``target``.

    The destination is a property of the **target**, never of the donor. That
    distinction is the whole of a bug worth remembering: the derivation used
    to run off the donor's layout, so a Zephyr NPC -- whose skin sits beside
    its mesh -- staged over the flat NPC family put its texture at
    `c3/npc/999001100.dds`, a path no part of the client reads.

    ``exists``    tells whether a logical path is in the install being modded.
    ``authored``  optionally answers "what texture does this mesh use", from
                  whatever table the caller has (the viewer has one; the CLI
                  does not).

    **Only a path that exists is returned.** A derived name that is not on
    disk is a guess about where the client looks, and a texture written to a
    guessed path is read by nothing. Empty means "cannot be worked out" --
    which is a real answer, and better than a confident wrong one.
    """
    t = (target or "").replace("\\", "/").lstrip("/").lower()
    if not t:
        return ""
    # The client's own tables come first, because they are the answer rather
    # than an inference about it. For an NPC the texture is reached through
    # `simple_object` -> `Texture0` -> `3dtexture.ini`, which no arithmetic
    # on the mesh id can reproduce: the Storekeeper's mesh is 999001100 and
    # its texture is 9990211. Transposing the id gave 9990010 -- a real file,
    # loaded by nothing -- and reported it confidently.
    if tables is not None:
        try:
            plan = tables.plan_for_mesh(t)
        except Exception:                                # pragma: no cover
            plan = None
        if plan is not None and plan.texture:
            return plan.texture
    if authored is not None:
        try:
            found = authored(t)
        except Exception:                                # pragma: no cover
            found = None
        if found:
            return found
    cands = []
    stem = t.rsplit(".", 1)[0] if "." in t else t
    cands.append(stem + ".dds")                    # the skin beside the mesh
    m = _FLAT_NPC.match(t)
    if m:
        # Measured over the baseline: of 41 looks, 36 ship the trailing-zero
        # form and the rest the six-digit one. Both, so neither family
        # member is left to a guess.
        look = m.group("look")
        cands += [f"c3/texture/999{look}0.dds", f"c3/texture/999{look}.dds"]
    for c in cands:
        try:
            if exists(c):
                return c
        except Exception:                                # pragma: no cover
            continue
    return ""


class CollectionError(RuntimeError):
    pass


class Collection:
    """The curated set.  Reads and writes ``<library>/Collection/``."""

    def __init__(self, library: Path | str):
        self.library = Path(library)
        self.root = self.library / ROOT_NAME
        self.index_path = self.root / INDEX_NAME
        self.entries: list[dict] = []
        #: the server profile as last published; set by `save`/`publish`.
        self.profile: dict = {}
        if self.index_path.is_file():
            try:
                doc = json.loads(self.index_path.read_text("utf-8"))
                self.entries = doc.get("entries", [])
            except ValueError as e:
                raise CollectionError(f"{self.index_path} is unreadable: {e}")

    # -- queries -----------------------------------------------------------
    def by_id(self, ident: str) -> Optional[dict]:
        for e in self.entries:
            if e["id"] == ident:
                return e
        return None

    def by_source(self, server: str, mesh: str) -> Optional[dict]:
        m = (mesh or "").lower()
        for e in self.entries:
            if e.get("server", "") == server and \
                    (e.get("sourceMesh") or "").lower() == m:
                return e
        return None

    def counts(self) -> dict[str, int]:
        out = {c: 0 for c in CATEGORIES}
        for e in self.entries:
            out[e["category"]] = out.get(e["category"], 0) + 1
        return out

    # -- mutation ----------------------------------------------------------
    def _unique_id(self, base: str) -> str:
        taken = {e["id"] for e in self.entries}
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def add(self, *, category: str, name: str, mesh_bytes: bytes,
            mesh_name: str, skins: Iterable[tuple[str, bytes]] = (),
            server: str = "", source_mesh: str = "",
            source_texture: str = "", swap_for: str = "",
            note: str = "",
            parts: Iterable[tuple[str, str, str, bytes]] = (),
            provenance: Optional[dict] = None) -> dict:
        """Copy one mesh (and its skins) into a category folder.

        Re-collecting the same source updates that entry in place rather than
        making a second copy: the collection is a set of decisions, and the
        same decision twice is still one decision.

        ``provenance`` is `core/provenance.stamp()`'s record for the install
        the bytes were read out of.  It is **passed in, not computed here**:
        this module knows a library, not a game root, and `provenance` reaches
        `coroot` for the fingerprint.  A caller with no root passes ``None``
        and the entry says so -- see `provenance_state`.

        Deliberately NOT part of the `by_source` dedup key.  Re-collecting the
        same logical path from a *different* install is the same decision made
        again about the same slot, and the newer stamp replaces the older one;
        keying on it would leave two entries claiming one source path with
        nothing to choose between them.
        """
        if category not in CATEGORIES:
            raise CollectionError(
                f"unknown category {category!r}; expected one of "
                + ", ".join(CATEGORIES))
        if not mesh_bytes:
            raise CollectionError("no mesh bytes")

        prev = self.by_source(server, source_mesh) if source_mesh else None
        if prev:
            # Re-collecting replaces the entry, so the old copy goes first --
            # every file of it, not just when the shelf changed. A re-collect
            # that finds different parts (the storekeeper, once its action
            # files were reachable) writes them under new names, and the ones
            # it no longer claims were being left on disk with nothing in the
            # index pointing at them. The bytes are all in memory here, so
            # deleting before writing costs nothing even when the paths are
            # about to be identical.
            for rel in [prev["mesh"], *prev.get("skins", []),
                        *[q["file"] for q in prev.get("parts", [])],
                        f"{prev['category']}/{prev['id']}.json"]:
                q = self.root / rel
                if q.is_file():
                    q.unlink()
        stem = prev["id"] if prev else self._unique_id(
            slugify(f"{server or 'base'}-{name or Path(mesh_name).stem}"))

        folder = self.root / category
        folder.mkdir(parents=True, exist_ok=True)
        ext = Path(mesh_name).suffix or ".c3"
        (folder / f"{stem}{ext}").write_bytes(mesh_bytes)

        skin_files, skin_shas = [], []
        for i, (sname, sbytes) in enumerate(skins):
            sext = Path(sname).suffix or ".dds"
            fn = f"{stem}{sext}" if i == 0 else f"{stem}_s{i}{sext}"
            (folder / fn).write_bytes(sbytes)
            skin_files.append(fn)
            skin_shas.append(_sha(sbytes))

        # Everything else the asset needs to behave: its motion files, the
        # effects that ride on it. A model collected without these is a
        # statue -- it was the whole point of collecting it that it moves.
        part_recs = []
        src_stem = Path(source_mesh or "").stem.lower()
        for prec_in in parts:
            # A 5th element carries per-file facts the collector measured and
            # `stage` needs back -- whether a map's art is shared with other
            # maps, whether a file is integrity-checked. It travels on the
            # entry because `core/` is stdlib-only by test: it cannot re-derive
            # them, and re-deriving at stage time would be a second answer to
            # a question already answered.
            role, pname, psource, pbytes = prec_in[:4]
            extra = dict(prec_in[4]) if len(prec_in) > 4 and prec_in[4] else {}
            pext = Path(pname).suffix or ".c3"
            pstem = Path(pname).stem
            if role == "motion":
                # File the part by its ACTION CODE, not by whatever the
                # source layout called the file. The flat family names its
                # actions `999001101.c3`, so keeping the source stem gave a
                # collected entry an action list reading "999001101" instead
                # of "101 — stand (alt)". One shape here means one shape for
                # everything that reads a collected entry back.
                code, _ = action_code(src_stem, pstem.lower())
                if code:
                    pstem = code
            fn = f"{stem}__{role}-{slugify(pstem)}{pext}"
            (folder / fn).write_bytes(pbytes)
            part_recs.append({"role": role, "file": f"{category}/{fn}",
                              "source": psource, "sha": _sha(pbytes),
                              **extra})

        entry = {
            "id": stem,
            "category": category,
            "name": name or stem,
            "server": server,
            "mesh": f"{category}/{stem}{ext}",
            "skins": [f"{category}/{f}" for f in skin_files],
            "sourceMesh": source_mesh,
            "sourceTexture": source_texture,
            "swapFor": swap_for or source_mesh,
            "note": note,
            "parts": part_recs,
            "sha": {"mesh": _sha(mesh_bytes), "skins": skin_shas},
            "collectedAt": _now(),
            # Always written, `None` included. A key that is PRESENT and null
            # says "a collector that knew about provenance had none to give";
            # an ABSENT key says "written before the field existed". Both read
            # as `PROV_UNRECORDED`, so nothing branches on the difference --
            # but the difference is on disk if a later question needs it, and
            # it costs one word to keep.
            PROVENANCE_KEY: dict(provenance) if provenance else None,
        }
        # a per-entry sidecar, so a folder is self-describing even if the
        # index is lost or the folder is copied somewhere else
        (folder / f"{stem}.json").write_text(
            json.dumps(entry, indent=1), "utf-8")

        if prev:
            self.entries[self.entries.index(prev)] = entry
        else:
            self.entries.append(entry)
        self.save()
        return entry

    def remove(self, ident: str, *, delete_files: bool = True) -> bool:
        e = self.by_id(ident)
        if e is None:
            return False
        if delete_files:
            for rel in [e["mesh"], *e.get("skins", []),
                        *[p["file"] for p in e.get("parts", [])],
                        f"{e['category']}/{e['id']}.json"]:
                p = self.root / rel
                if p.is_file():
                    p.unlink()
        self.entries.remove(e)
        self.save()
        return True

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries.sort(key=lambda e: (e["category"], e["name"].lower()))
        self.index_path.write_text(json.dumps({
            "schema": 1,
            "categories": CATEGORIES,
            "counts": self.counts(),
            "entries": self.entries,
        }, indent=1), "utf-8")
        # The server profile is a *view* of the index, so it is rewritten
        # here rather than by a command you have to remember. Publishing by
        # hand meant the library showed the Collection as it was the last
        # time someone ran `collect.py publish` -- entries that had since
        # been removed still listed, entries since kept absent entirely.
        # The index is on disk before this runs, so a failure to publish
        # costs the profile, never the collection.
        self.profile = self.publish()

    # -- browsing it -------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        return self.library / "servers" / PROFILE_NAME

    def publish(self) -> dict:
        """Rewrite the server profile that makes the Collection browsable.

        Wholesale, never incrementally: the index is the truth and the
        filemap is derived from it, so a removed entry disappears from the
        library by construction instead of by remembering to delete it.
        """
        filemap: dict[str, list] = {}
        for e in self.entries:
            # An entry's motion and effect files are part of what was kept.
            # Listing only mesh and skin left them in the library folder but
            # outside its namespace: collected, and unbrowsable.
            rels = [e["mesh"], *e.get("skins", []),
                    *[p["file"] for p in e.get("parts", [])]]
            for rel in rels:
                filemap[f"{PROFILE_NAME}/{rel}".lower()] = [
                    "l", f"{ROOT_NAME}/{rel}", e["category"]]
        profile = {
            "server": PROFILE_NAME, "tag": PROFILE_TAG,
            "clientVersion": "curated",
            "files": len(filemap), "entries": len(self.entries),
            "counts": self.counts(),
            "publishedAt": _now(),
        }
        d = self.profile_dir
        d.mkdir(parents=True, exist_ok=True)
        # Replaced rather than truncated-and-rewritten: the viewer reads
        # these while you collect, and a half-written filemap is a view that
        # fails to open rather than one that is merely out of date.
        _write_atomic(d / "filemap.json",
                      json.dumps(filemap, indent=0, sort_keys=True))
        _write_atomic(d / "profile.json", json.dumps(profile, indent=1))
        return profile

    # -- using it ----------------------------------------------------------
    def stage(self, ident: str, stage_dir: Path | str,
              swap_for: str = "", *, skin: bool = True, skin_to: str = "",
              roles: Iterable[str] = ("motion", "effect", "sound", *MAP_ROLES),
              read_target=None, art_plan=None,
              shared_policy: str = "skip-identical") -> dict:
        """Write one entry into a mod stage tree, under the path it replaces.

        This is what makes the collection usable rather than merely tidy:
        `comod.py install` copies a stage tree into the install, and the game
        reads a loose file before the archive, so an entry staged at the right
        logical path *is* the swap.  Nothing here touches the install.

        ``skin`` and ``roles`` choose what travels with the geometry.  The
        default is everything, because a model swapped without its animation
        is a statue and that is rarely what was meant -- but replacing only
        the mesh, keeping the target's own skin and actions, is a real thing
        to want and used to require staging by hand.
        """
        e = self.by_id(ident)
        if e is None:
            raise CollectionError(f"no entry {ident!r}")
        target = swap_for or e.get("swapFor") or ""
        if not target:
            raise CollectionError(
                f"{ident} has no swapFor: say which logical path it replaces")
        want = {r.lower() for r in roles}
        stage = Path(stage_dir)
        out = {"id": ident, "target": target, "wrote": [], "skipped": []}

        # An `art_plan` says where the CLIENT reads each role from, which for
        # an NPC is three different tables and three different directories.
        # Without one, `target` has to serve as all three -- and for the flat
        # NPC family that is wrong twice over: the geometry comes from
        # `c3/mesh/` and the texture from an id in `3dtexture.ini`, so
        # writing both beside the motion file changes the animation and
        # nothing else. Exactly what a swap looked like before this existed.
        motion_dst: dict = {}
        if art_plan is not None:
            if getattr(art_plan, "geometry", ""):
                target = art_plan.geometry
                out["target"] = target
                out["plan"] = {"geometry": art_plan.geometry,
                               "texture": art_plan.texture,
                               "motions": dict(art_plan.motions)}
            if getattr(art_plan, "texture", "") and not skin_to:
                skin_to = art_plan.texture
            # Match the donor's action codes onto the target's own motion
            # paths, so `rest` gets the donor's rest rather than whatever
            # ordering the parts happen to be in.
            for role_name, path in getattr(art_plan, "motions", {}).items():
                stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                motion_dst[stem[-3:]] = path

        mesh_dst = stage / target
        mesh_dst.parent.mkdir(parents=True, exist_ok=True)
        #: kept, because a per-action target needs the geometry written into
        #: every action file, not only into the one the mesh replaces.
        mesh_bytes = (self.root / e["mesh"]).read_bytes()
        mesh_dst.write_bytes(mesh_bytes)
        out["wrote"].append(str(target))
        # Maps are the one area an install hashes. Measured on CCO 2.0:
        # `integrity.json` sits at the install root and covers 136 `.DMap`
        # plus 7 `.json` and no art file at all -- and it is absent from
        # every official patch client. So a staged DMap is the one thing
        # here that can trip a manifest, and it is worth saying at stage
        # time rather than leaving to be discovered at install time.
        if e.get("integrity"):
            out.setdefault("integrity", []).append({
                "path": str(target),
                "why": ("this install hashes it in integrity.json, so "
                        "replacing it is detectable"),
            })
        if not skin and e.get("skins"):
            out["skipped"].append("skin (the target keeps its own)")
        # The skin must follow the TARGET, not the source: staging this
        # model over c3/npc/002/1.c3 while its skin lands back at
        # c3/npc/001/1.dds leaves the game drawing 002's own texture on 001's
        # geometry -- a swap that changes only half of what you asked for.
        if skin and e.get("skins"):
            src_tex = (e.get("sourceTexture") or "").lower()
            # An explicit destination settles the case the guess below cannot:
            # a skin that does not sit beside its mesh (the flat NPC family
            # keeps its textures in c3/texture/) has no derivable home under
            # the target, and saying "stage it yourself with the path you
            # intend" while offering no way to do that is not a choice.
            tex_target = (skin_to or "").replace("\\", "/").lstrip("/").lower()
            if tex_target:
                pass
            elif src_tex:
                tsuffix = Path(src_tex).suffix or ".dds"
                if src_tex == (Path(e.get("sourceMesh") or "").with_suffix(
                        tsuffix).as_posix().lower()):
                    # the usual case: skin sits beside the mesh, same stem
                    tex_target = str(Path(target).with_suffix(
                        tsuffix)).replace("\\", "/")
                elif target == (e.get("swapFor") or "") ==                         (e.get("sourceMesh") or ""):
                    tex_target = src_tex          # replacing itself in place
            if tex_target:
                t = stage / tex_target
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes((self.root / e["skins"][0]).read_bytes())
                out["wrote"].append(tex_target)
            else:
                out["skinSkipped"] = (
                    f"the source skin ({src_tex or 'unknown'}) does not sit "
                    "beside its mesh, so where it belongs under the target "
                    "is a judgement call -- say where with skinTo")
                out["skinSource"] = src_tex

        # Motion files are read from the target's own directory, so they
        # follow the target's folder -- and, where the layout puts the action
        # code in the filename, take the TARGET's name for it.
        tdir = str(Path(target).parent).replace("\\", "/")
        tstem = Path(target).stem.lower()
        src_stem = Path(e.get("sourceMesh") or "").stem.lower()
        for prec in e.get("parts", []):
            role = prec["role"]
            if role not in want:
                out["skipped"].append(f"{role}: {Path(prec['source']).name}")
                continue
            src_name = Path(prec["source"]).name
            if role == "motion":
                code, _ = action_code(src_stem, Path(prec["source"]).stem.lower())
                if not code:
                    # Without a code there is nothing to say what this file is
                    # called in the target's naming, and the old fallback --
                    # the donor's own filename, in the target's directory --
                    # writes over a third model in the flat family, where the
                    # filename names the look as well as the action.
                    out["skipped"].append(
                        f"{role}: {src_name} (no action code for the target)")
                    continue
                # The plan's own motion paths win: they are what `npc.json`
                # names, so they are right even where the target's directory
                # and stem would derive something else. Falls back to the
                # derivation for anything the tables do not describe.
                dst = motion_dst.get(code) or \
                    f"{tdir}/{action_target_name(tstem, code)}"
            elif role in ("effect", "sound") or role in MAP_ROLES:
                # These live at their own logical path, not under the target.
                # An effect is addressed by name; a map's background, scenery
                # index and tiles are addressed by the paths the DMap and the
                # `.ani` name, and moving any of them breaks the reference
                # that made them part of the map.
                dst = prec["source"]
            else:
                continue

            # Shared art: stage it only where it would actually change
            # something, and never silently. Map tiles and the `.ani` index
            # are referenced by other maps -- one index by 31 of them -- so
            # overwriting one is a decision about all of them. Identical bytes
            # are a no-op worth skipping rather than a risk worth taking.
            if role in MAP_ROLES and prec.get("shared"):
                if shared_policy == "never":
                    out["skipped"].append(
                        f"{role}: {dst} is shared and you asked to leave "
                        "shared art alone")
                    continue
                old = None
                if read_target is not None and shared_policy != "always":
                    try:
                        old = read_target(dst)
                    except Exception:                    # pragma: no cover
                        old = None
                blob_now = (self.root / prec["file"]).read_bytes()
                if old is not None and old == blob_now:
                    out["skipped"].append(
                        f"{role}: {dst} is shared and already identical")
                    continue
                others = prec.get("sharedWith") or []
                out.setdefault("sharedArt", []).append({
                    "path": dst, "role": role, "maps": others,
                    "why": (f"{dst} is also used by {len(others)} other map(s); "
                            "staging it changes them too"),
                })
            if prec.get("integrity"):
                out.setdefault("integrity", []).append({
                    "path": dst,
                    "why": "this install hashes it in integrity.json",
                })
            if dst.lower() == target.lower():
                # In the per-action-mesh layout the target IS one of the
                # action files, so its own code resolves back onto it. The
                # geometry you chose has already been written there; letting
                # the part overwrite it would silently undo the swap.
                out["skipped"].append(
                    f"{role}: {src_name} is the target itself")
                continue
            blob = (self.root / prec["file"]).read_bytes()
            # The two layouts do not mix. If the path being replaced supplies
            # its own geometry and the file going there does not, that action
            # loses its model: the client plays the new motion on whatever it
            # still has, which reads as "the animation changed but the model
            # did not". Reported rather than refused, because the caller may
            # be replacing a whole family at once -- but never silent.
            if read_target is not None and role == "motion":
                try:
                    old = read_target(dst)
                except Exception:                        # pragma: no cover
                    old = None
                if old:
                    was, now = layout_of(old), layout_of(blob)
                    if was == "per-action" and now == "motion-only":
                        # Compose rather than refuse. The target wants
                        # geometry+motion in one file and the donor keeps
                        # them apart, so this is the swap the two shapes can
                        # both express -- without it the action loses its
                        # model and the client animates whatever it still
                        # has, which is what "the animation changed but the
                        # mesh did not" was.
                        try:
                            blob = compose_action(mesh_bytes, blob)
                            out.setdefault("composed", []).append({
                                "path": dst,
                                "why": (f"{dst} carries its own geometry in "
                                        "this client, so the model was built "
                                        "into it alongside this action"),
                            })
                        except LayoutError as e:
                            out.setdefault("mismatch", []).append({
                                "path": dst, "targetLayout": was,
                                "sourceLayout": now, "why": str(e),
                            })
                            out["skipped"].append(
                                f"{role}: {src_name} ({e})")
                            continue
            t = stage / dst
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(blob)
            out["wrote"].append(dst)
        return out
