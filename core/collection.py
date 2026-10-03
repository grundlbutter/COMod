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
#: `scene` and `otherdata` joined 2026-09-15 with the rest of the DMap's
#: record kinds (see `tools/mapparts.ROLES`). Both stage at their own logical
#: path, both are referenced by the map by that path, and `scene` is shared
#: between maps exactly as a tile is -- so both want the whole of the
#: treatment this tuple selects, including the shared-art policy.
#:
#: `sound` is NOT in here, and that is deliberate rather than an oversight: a
#: MODEL entry can carry a `sound` part too, and `stage` already routes the
#: role to its own logical path. Adding it would put every model's sound
#: under the map shared-art policy for no map-side gain.
MAP_ROLES = ("puzzle", "plane", "ani", "art", "scene", "otherdata",
             "config", "effectart")

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


# ===========================================================================
# SATELLITES -- what travels with a collected asset.  Backlog section 7.
# ===========================================================================
#
# The owner asked for checkboxes: *"Effects (all of them, including hit
# effects, weapon glow effects, particles), animations, textures, alt skin
# textures, sounds ... make it up to the user to decide via checkboxes and to
# provide a select all/select none button."*
#
# THE ENTRY SCHEMA IS **B + E**, decided by the owner 2026-09-06 and recorded
# in `docs/comod_backlog.md` section 7.  Two blocks, spelled here so nothing
# re-litigates them:
#
#     satellites : { <kind>: [ { path, sha, takenAt } ] }   -- B: what was TAKEN
#     offered    : { <kind>: { available, taken, asOf } }   -- E: what was SEEN
#
# B is why `_unique_id` does not change and re-collect **merges**: identity is
# still the mesh, so the library stays a set of decisions rather than growing a
# second "zephyr-0816 (with effects)" beside the first.  E is why a gap is
# *visible* rather than something the tool has to detect: the entry says
# "Effects: 3 of 7 collected", so a user is never misled about what they kept.
#
# WHY `offered` IS NOT DERIVABLE FROM `satellites`, WHICH IS THE POINT
# -------------------------------------------------------------------
# An entry holding one effect is one of three different worlds and they are
# not the same fact:
#
#   * one was offered and one was taken       -> complete
#   * seven were offered and one was taken    -> six are still available
#   * nothing measured what was on offer      -> UNKNOWN, and must say so
#
# Without `offered` all three read as "it has one effect".  `SAT_UNKNOWN` is
# the third, and it is the *resting state of every entry written before this
# existed*: back-filling it with a zero would manufacture a measurement nobody
# took, which is the exact failure the bulletproof rule of section 6 forbids.
# So migration falls out for free -- an old entry displays as "1 collected,
# availability unknown -- re-check".

#: `offered[kind].available` when nothing measured it.  Not zero, not absent:
#: a value a renderer can print, so the honest answer survives the trip to the
#: UI instead of being flattened into a number on the way.
SAT_UNKNOWN = "unknown"

#: The entry keys holding the two blocks.  Spelled once so a reader grepping
#: for either finds every site, exactly as `PROVENANCE_KEY` is.
SATELLITES_KEY = "satellites"
OFFERED_KEY = "offered"


class SatelliteKind:
    """One checkbox: what it offers, what backs it, and whether SELECT ALL is
    allowed to reach it.

    ``select_all`` is the ruling, not a preference.  **OWNER, 2026-09-06:**
    *"Make sure the animations can be exported, but put them in their own 'not
    recommended' section that select all doesn't toggle."*  A `MOTI` motion set
    is an external table bound by ordinal and used by dozens of meshes, so
    ticking "animations" on one body does not copy *its* animation -- it copies
    something other assets depend on.  The user may still tick it deliberately;
    they may not arrive there by accident.

    The backlog generalises the rule and so does this class: **any satellite
    whose collection has consequences beyond this asset belongs in that
    group.**  That is what `select_all` is False *for* -- it is not a list of
    one special case, it is the predicate, and `why_not` is the consequence
    stated in the row itself so a UI need not invent an explanation.
    """

    __slots__ = ("name", "label", "backed_by", "role", "select_all", "why_not")

    def __init__(self, name: str, label: str, backed_by: str, role: str,
                 select_all: bool = True, why_not: str = ""):
        if not select_all and not why_not:
            # A row excluded from SELECT ALL with no reason attached is how a
            # ruling becomes folklore. Refused at import.
            raise ValueError(
                f"satellite kind {name!r} is outside SELECT ALL and says no "
                f"why_not; the consequence has to travel with the row")
        self.name = name
        self.label = label
        self.backed_by = backed_by
        self.role = role
        self.select_all = select_all
        self.why_not = why_not

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label,
                "backedBy": self.backed_by, "role": self.role,
                "selectAll": self.select_all, "whyNot": self.why_not}

    def __repr__(self) -> str:                               # pragma: no cover
        return f"<SatelliteKind {self.name} selectAll={self.select_all}>"


#: The checkboxes, in the order the panel offers them, and the ONE place the
#: SELECT-ALL ruling is written down.  Every one of the owner's named kinds is
#: here -- *all* three effect forms on equal footing, per section 6's verified
#: note that particles and ribbons are viewable and exportable.
#:
#: The three effect rows are separate rather than one "Effects" row because
#: they come from three different tables and can each be empty for different
#: reasons; collapsing them would make "Effects (0)" mean four things.
SATELLITE_KINDS: tuple = (
    SatelliteKind("textures", "Textures",
                  "the paired skin(s) the appearance rows declare", "skin"),
    SatelliteKind("altskins", "Alt skin textures",
                  "the garment index / npcaltskin.alt_skins() -- the distinct "
                  "textures this shared geometry wears", "skin"),
    SatelliteKind("effects_hit", "Effects - hit",
                  "WeaponEffect.ini, the impact spark keyed off the 3-digit "
                  "weapon type", "effect"),
    SatelliteKind("effects_glow", "Effects - weapon glow / aura",
                  "Action3DEffect.ini; shows as aura (always-on) in "
                  "`comod effects`", "effect"),
    SatelliteKind("effects_particle", "Effects - particles",
                  "PTCL / PTC3 -- viewable and exportable, not writable",
                  "effect"),
    SatelliteKind("sounds", "Sounds", "ordinary .wav / .mp3", "sound"),
    SatelliteKind("animations", "Animations",
                  "the shared motion set (`comod anim` resolves action -> set)",
                  "motion", select_all=False,
                  why_not="a MOTI motion set is an external table bound by "
                          "ordinal and used by many meshes: taking it copies "
                          "something dozens of other models depend on, so it "
                          "is opt-in and SELECT ALL does not reach it"),
)

KIND_BY_NAME: dict = {k.name: k for k in SATELLITE_KINDS}

#: Kind name -> the `parts` role it is filed under, so a selection turns into
#: `gather_parts` input without a second table that can disagree with this one.
KIND_ROLE: dict = {k.name: k.role for k in SATELLITE_KINDS}

#: Where an effect goes when the collector did not say WHICH effect kind it
#: ticked -- the directory sweep, or any caller written before the checkboxes
#: existed.  It is deliberately **not** one of the three real effect kinds:
#: role `effect` backs three of them, so picking one would be a guess printed
#: in the same typeface as a measurement.  It is also deliberately not dropped:
#: `satellite_report` reports any kind an entry holds, this one included, so an
#: unclassified effect is visible as unclassified rather than absent.
UNCLASSIFIED_EFFECTS = "effects_unclassified"

#: The demotion, used only when a part carries no `kind`.  A coarser answer
#: where the mapping is exact, and `UNCLASSIFIED_EFFECTS` where it is not.
_KIND_FOR_ROLE: dict = {
    "motion": "animations",
    "sound": "sounds",
    "skin": "textures",
    "effect": UNCLASSIFIED_EFFECTS,
}


def select_all_kinds() -> tuple:
    """Exactly the kinds a SELECT ALL button ticks.

    **THE MUST-FIRE LIVES HERE.**  This is the only definition of what SELECT
    ALL means, and it is derived from `SatelliteKind.select_all` rather than
    written out as a list, so the ruling cannot be honoured in the table and
    quietly broken in the button.  `tools/webui/builder.js` ticks precisely the
    names this returns -- shipped to it as `selectAllKinds` -- and holds no
    kind list of its own, so there is no second place for the two to drift.
    """
    return tuple(k.name for k in SATELLITE_KINDS if k.select_all)


def not_recommended_kinds() -> tuple:
    """The kinds in the separate NOT RECOMMENDED group: exportable, opt-in,
    and never reached by SELECT ALL."""
    return tuple(k.name for k in SATELLITE_KINDS if not k.select_all)


def sanitize_selection(names: Iterable[str]) -> list[str]:
    """The requested kinds, filtered to ones that exist, order preserved.

    An unknown kind is DROPPED rather than raising: a selection arrives from a
    browser that may be a version behind, and refusing the whole collect
    because one checkbox was renamed loses the six the user did tick.  It is
    also never invented -- a name that is not in `KIND_BY_NAME` collects
    nothing, so a typo cannot silently widen the take.
    """
    seen: set = set()
    out: list[str] = []
    for n in names or ():
        k = str(n or "").strip()
        if k in KIND_BY_NAME and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def kinds_json() -> list[dict]:
    """The checkbox table for the UI: every kind, its label, what backs it and
    whether SELECT ALL reaches it."""
    return [k.as_dict() for k in SATELLITE_KINDS]


def _sat_key(rec: dict) -> str:
    return str((rec or {}).get("path") or "").replace("\\", "/").lower()


def merge_satellites(prev: Optional[dict], new: Optional[dict],
                     *, at: str = "") -> dict:
    """**B**: the taken-satellite block, MERGED rather than replaced.

    *"Re-collecting with more satellites ADDS them; identity stays the mesh.
    This matches what the user actually means -- 'I kept this before, now I
    want its effects too' -- and nothing is ever silently dropped."*

    A satellite already held keeps its recorded `takenAt` unless its `sha`
    changed, in which case this take replaces it and stamps the new time: the
    field answers "when did these bytes get here", and carrying an old date
    over new bytes would make the entry lie about its own contents.

    Removal is never a side effect of collecting.  It is `prune_satellites`,
    which B requires anyway because a satellite's source can change after it
    was taken.
    """
    stamp = at or _now()
    out: dict = {}
    for kind, recs in (prev or {}).items():
        if not isinstance(recs, list):
            continue
        out[kind] = [dict(r) for r in recs if isinstance(r, dict)]
    for kind, recs in (new or {}).items():
        if not isinstance(recs, list):
            continue
        bucket = out.setdefault(kind, [])
        by_path = {_sat_key(r): i for i, r in enumerate(bucket)}
        for r in recs:
            if not isinstance(r, dict):
                continue
            rec = {"path": str(r.get("path") or ""),
                   "sha": str(r.get("sha") or ""),
                   "takenAt": str(r.get("takenAt") or stamp)}
            # Anything else the collector measured travels too, but never
            # over the three fields the schema names.
            for k, v in r.items():
                if k not in ("path", "sha", "takenAt"):
                    rec[k] = v
            i = by_path.get(_sat_key(rec))
            if i is None:
                by_path[_sat_key(rec)] = len(bucket)
                bucket.append(rec)
            elif bucket[i].get("sha") != rec["sha"]:
                bucket[i] = rec                  # new bytes, new takenAt
            else:
                # Same bytes: keep the ORIGINAL takenAt. Re-collecting an
                # unchanged file is not a new acquisition of it.
                keep = bucket[i].get("takenAt")
                bucket[i] = {**rec, "takenAt": keep or rec["takenAt"]}
    for kind in list(out):
        out[kind].sort(key=_sat_key)
    return out


def merge_offered(prev: Optional[dict], new: Optional[dict]) -> dict:
    """**E**: what was ON OFFER, refreshed for the kinds this collect
    measured and left alone for the kinds it did not.

    A kind absent from `new` was not looked at on this pass, and its old block
    is still the last real measurement of it -- with its own `asOf` saying how
    old.  Overwriting it with a zero, or deleting it, would both replace a
    stale measurement with a false one.  *"An `available` count is true only as
    of a moment ... and is re-checked against the source rather than trusted
    forever."*
    """
    out: dict = {}
    for kind, blk in (prev or {}).items():
        if isinstance(blk, dict):
            out[kind] = dict(blk)
    for kind, blk in (new or {}).items():
        if not isinstance(blk, dict):
            continue
        avail = blk.get("available", SAT_UNKNOWN)
        out[kind] = {
            "available": (avail if avail == SAT_UNKNOWN
                          else int(avail or 0)),
            "taken": int(blk.get("taken") or 0),
            "asOf": str(blk.get("asOf") or _now()),
        }
    return out


def satellite_state(entry: dict, kind: str) -> dict:
    """What an entry can honestly say about ONE kind.

    Four `state` values and none of them collapse into another:

    ``complete``   every satellite that was on offer was taken.
    ``partial``    some were; ``available - taken`` are still there to get.
    ``unknown``    nothing measured the offer.  **This is what an entry
                   written before the `offered` block existed says**, and it
                   is the honest reading of a missing block -- not zero.
    ``none``       the offer was measured and was empty.

    A caller rendering "Effects: 3 of 7 collected" reads `taken` and
    `available`; a caller that finds ``available == SAT_UNKNOWN`` must print
    the uncertainty rather than a number, and the value is a string precisely
    so that arithmetic on it fails loudly instead of quietly producing 0.
    """
    taken = len(((entry or {}).get(SATELLITES_KEY) or {}).get(kind) or [])
    blk = ((entry or {}).get(OFFERED_KEY) or {}).get(kind)
    if not isinstance(blk, dict) or "available" not in blk:
        return {"kind": kind, "taken": taken, "available": SAT_UNKNOWN,
                "asOf": "", "state": "unknown",
                "why": ("nothing recorded what this asset was offered, so how "
                        "much is missing is not known -- re-check the source")}
    avail = blk.get("available", SAT_UNKNOWN)
    as_of = str(blk.get("asOf") or "")
    if avail == SAT_UNKNOWN:
        return {"kind": kind, "taken": taken, "available": SAT_UNKNOWN,
                "asOf": as_of, "state": "unknown",
                "why": "the offer was recorded as unmeasured"}
    avail = int(avail or 0)
    if avail == 0:
        state = "none"
    elif taken >= avail:
        state = "complete"
    else:
        state = "partial"
    return {"kind": kind, "taken": taken, "available": avail, "asOf": as_of,
            "state": state,
            "why": ("" if state != "partial"
                    else f"{avail - taken} more were on offer as of {as_of}")}


def satellite_report(entry: dict) -> list[dict]:
    """`satellite_state` for every kind, in the panel's order -- including the
    kinds this entry holds nothing of, because "no effects were kept" and "no
    effects were offered" are the two answers the report exists to separate.

    Kinds the entry holds that are NOT in the checkbox table are appended and
    flagged `extra`.  That covers `UNCLASSIFIED_EFFECTS` and anything a future
    version of this tool files that this one does not know about: reporting
    only the kinds in `SATELLITE_KINDS` would make a satellite that is on disk
    and in the index invisible in the one view that claims to list them.
    """
    known = {k.name for k in SATELLITE_KINDS}
    out = [satellite_state(entry, k.name) for k in SATELLITE_KINDS]
    for kind in sorted((entry or {}).get(SATELLITES_KEY) or {}):
        if kind in known:
            continue
        st = satellite_state(entry, kind)
        st["extra"] = True
        out.append(st)
    return out


def prune_satellites(entry: dict, kind: str = "",
                     paths: Iterable[str] = ()) -> list[dict]:
    """Remove taken satellites from an entry's block and return the records
    removed.  **The only removal path there is**, which is what B costs:
    because re-collect never drops anything, an entry can accumulate
    satellites whose source has since changed, and the way back out has to be
    something the user asked for by name.

    ``kind`` empty means every kind; ``paths`` empty means every satellite of
    the kinds named.  Does not touch `offered` -- pruning changes what is
    HELD, not what was once on offer, and rewriting the offer to match would
    erase the very gap the block exists to show.
    """
    sats = (entry or {}).get(SATELLITES_KEY) or {}
    want = {str(p).replace("\\", "/").lower() for p in (paths or ())}
    dropped: list[dict] = []
    for k in ([kind] if kind else list(sats)):
        recs = sats.get(k)
        if not isinstance(recs, list):
            continue
        keep = []
        for r in recs:
            if not want or _sat_key(r) in want:
                dropped.append(r)
            else:
                keep.append(r)
        sats[k] = keep
    return dropped


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
            provenance: Optional[dict] = None,
            offered: Optional[dict] = None,
            also_taken: Optional[dict] = None,
            merge_parts: bool = False) -> dict:
        """Copy one mesh (and its skins) into a category folder.

        Re-collecting the same source updates that entry in place rather than
        making a second copy: the collection is a set of decisions, and the
        same decision twice is still one decision.

        ``offered`` is **E** (backlog section 7): ``{kind: {available, taken,
        asOf}}`` -- what was ON OFFER when this collect ran, so a later gap is
        distinguishable from "never offered".  `None` means this collector did
        not measure the offer, and the entry then says `SAT_UNKNOWN` rather
        than being back-filled with a number nobody took.

        ``merge_parts`` is **B**, and it is **OFF by default**, which is a
        decision and not caution.  When on, parts the previous entry held and
        this collect did not re-take are carried forward -- bytes and record --
        so "I kept this before, now I want its effects too" cannot silently
        drop the motions that were already there.  Removal then becomes
        `prune`, never a side effect of collecting again, which is the cost
        section 7 explicitly accepted when it chose B.

        **Why it is off by default, and this was measured rather than
        guessed.**  B is a ruling about the CHECKBOX collect: a user who ticks
        "effects" on something they already kept means "add these", and
        dropping their motions would be the silent loss the ruling forbids.  A
        re-collect that sends NO selection means something else -- the
        collector re-resolved this asset and this is now what it finds -- and
        `test_viewer.Curation.test_re_collecting_does_not_leave_the_old_files_behind`
        pins that: the storekeeper's action files became reachable, the
        re-collect found different parts, and the ones it no longer claimed
        used to sit on disk with nothing in the index pointing at them.
        Merging by default turned that test red, and it was right to.

        So the two semantics are kept apart by the same discriminator the rest
        of this feature uses: a caller that sends a selection gets B, and a
        caller that does not gets exactly what it always got.  `post_collect`
        passes `merge_parts=selective` for that reason and no other.

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
        # **B, and it has to happen BEFORE the delete below.** The previous
        # entry's files are about to be unlinked, so anything this collect is
        # not re-taking has to be read into memory first or it is gone. The
        # carry set is keyed on the part's SOURCE path -- the logical path it
        # came from -- because that is what identifies a satellite across two
        # collects; the on-disk filename is derived and can change with the
        # category. A part the new take also names is NOT carried: the new
        # bytes win, which is a refresh, not a drop.
        carried: list[tuple[dict, bytes]] = []
        if prev and merge_parts:
            fresh = {str(p[2] or "").replace("\\", "/").lower()
                     for p in parts if len(p) > 2}
            for q in prev.get("parts", []):
                if str(q.get("source") or "").replace("\\", "/").lower() in fresh:
                    continue
                f = self.root / str(q.get("file") or "")
                if not f.is_file():
                    # The record outlived its bytes. Dropping the record is
                    # right -- an entry claiming a file that is not there is
                    # exactly the "collection that claims to hold something it
                    # does not" section 7 warns about.
                    continue
                carried.append((dict(q), f.read_bytes()))
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
        skin_taken: list[tuple[str, dict]] = []
        for i, srec in enumerate(skins):
            # A 3rd element carries `{kind, path}` for a skin taken through a
            # CHECKBOX rather than as the entry's primary. Skins do not travel
            # as `parts`, so without this the `satellites` block would
            # under-report exactly the alt skins the user ticked -- an entry
            # holding a file it does not admit to. Two-tuples still work, so
            # every existing caller is unchanged.
            sname, sbytes = srec[0], srec[1]
            extra = dict(srec[2]) if len(srec) > 2 and srec[2] else {}
            sext = Path(sname).suffix or ".dds"
            fn = f"{stem}{sext}" if i == 0 else f"{stem}_s{i}{sext}"
            (folder / fn).write_bytes(sbytes)
            skin_files.append(fn)
            skin_shas.append(_sha(sbytes))
            if extra.get("kind"):
                skin_taken.append((str(extra["kind"]),
                                   {"path": extra.get("path") or sname,
                                    "sha": skin_shas[-1]}))

        # Everything else the asset needs to behave: its motion files, the
        # effects that ride on it. A model collected without these is a
        # statue -- it was the whole point of collecting it that it moves.
        part_recs = []
        src_stem = Path(source_mesh or "").stem.lower()
        # -- ONE STORED FILE PER LOGICAL PATH ------------------------------
        #
        # The stored name is derived from the part's BASENAME, and a basename
        # is not unique across an entry's sources. Effect art is the extreme
        # case and it is not exotic: every effect container names its files
        # `1.c3`, `2.c3`, `1.dds`... so `c3/effect/scene-ff/airwall_bluef/2.c3`
        # and `c3/effect/scene-ff/airwall_gold/2.c3` both derived
        # `<map>__effectart-2.c3`. The second write TRUNCATED the first, the
        # filemap then pointed both logical paths at one file, and eleven of
        # `sary02_new`'s effect files became one. Nothing errored: the entry
        # listed every part, every part had a `sha`, and the shas were of the
        # bytes that had been written -- one of which was no longer there.
        #
        # Map art has the same shape waiting (`data/map/a/2/a239.dds` against
        # another folder's `a239.dds`); it had simply not collided yet.
        #
        # A short digest OF THE SOURCE PATH, not a counter: it is stable
        # across re-collects whatever order the closure walks in, so
        # re-collecting a map rewrites the same filenames rather than
        # shuffling them.
        used: set = set()

        def _unique(fn: str, psource: str) -> str:
            if fn.lower() not in used:
                used.add(fn.lower())
                return fn
            q = Path(fn)
            tag = hashlib.sha1(
                str(psource or fn).replace("\\", "/").lower().encode("utf-8")
            ).hexdigest()[:8]
            alt = f"{q.stem}-{tag}{q.suffix}"
            used.add(alt.lower())
            return alt

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
            fn = _unique(f"{stem}__{role}-{slugify(pstem)}{pext}", psource)
            (folder / fn).write_bytes(pbytes)
            part_recs.append({"role": role, "file": f"{category}/{fn}",
                              "source": psource, "sha": _sha(pbytes),
                              **extra})

        # The carried-forward half of B. Written into THIS category's folder
        # under this entry's stem, so a re-collect that also changed the shelf
        # does not leave them orphaned in the old one; the record's `file` is
        # rewritten to match, and everything else about it is kept verbatim --
        # including the `kind` the original collect recorded, which is the only
        # thing that can still say which checkbox it came from.
        for q, blob in carried:
            role = str(q.get("role") or "effect")
            base = Path(str(q.get("file") or "")).name
            fn = base if base.startswith(f"{stem}__") else \
                f"{stem}__{role}-{slugify(Path(base).stem)}{Path(base).suffix}"
            fn = _unique(fn, q.get("source") or fn)
            (folder / fn).write_bytes(blob)
            part_recs.append({**q, "file": f"{category}/{fn}",
                              "carriedForward": True})

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
        # ---- B + E, the two blocks section 7 decided on -------------------
        # `satellites` is built from the part records rather than passed in
        # separately, so the block and the files on disk cannot disagree: a
        # kind appears here exactly when a file for it was written above. The
        # part's `kind` is what the collector ticked; a part with no kind (an
        # older caller, or the directory sweep) is filed by its ROLE through
        # `_KIND_FOR_ROLE`, which is a demotion to a coarser answer and never
        # an invention of a finer one.
        taken_now: dict = {}
        for q in part_recs:
            kind = str(q.get("kind") or "") or _KIND_FOR_ROLE.get(
                str(q.get("role") or ""), "")
            if not kind:
                continue
            taken_now.setdefault(kind, []).append(
                {"path": q.get("source") or "", "sha": q.get("sha") or "",
                 "takenAt": q.get("takenAt") or entry["collectedAt"]})
        # The primary skin is a texture that travels today and always did;
        # `sourceTexture` stays as it was, and the `textures` kind is additive
        # AROUND it so existing entries keep working unchanged.
        if source_texture and skin_shas:
            taken_now.setdefault("textures", []).insert(
                0, {"path": source_texture, "sha": skin_shas[0],
                    "takenAt": entry["collectedAt"]})
        for kind, rec in skin_taken:
            taken_now.setdefault(kind, []).append(
                {**rec, "takenAt": entry["collectedAt"]})
        # `also_taken` files a satellite this entry ALREADY HOLDS under a
        # second kind, without writing a second copy of it.
        #
        # It exists for one measured case. On CCO's Steel Blade the offer
        # lists `c3/texture/410008.dds` under `altskins`, and the Builder
        # passes that same file as the entry's primary skin -- so the collect
        # writes it once, files it under `textures`, and the entry then read
        # "Alt skins: 2 of 3" while holding all three. Overstating a GAP is a
        # smaller sin than overstating completeness and it is still a
        # misreport, and E is worth nothing if its numbers are approximate.
        for kind, recs in (also_taken or {}).items():
            for r in recs:
                taken_now.setdefault(kind, []).append(
                    {"path": r.get("path") or "", "sha": r.get("sha") or "",
                     "takenAt": entry["collectedAt"],
                     # Named, so a reader of the index can see that this row
                     # shares a file with another kind rather than wondering
                     # why two entries carry one sha.
                     "alsoUnder": r.get("alsoUnder", "")})
        entry[SATELLITES_KEY] = merge_satellites(
            (prev or {}).get(SATELLITES_KEY) if merge_parts else None,
            taken_now, at=entry["collectedAt"])
        entry[OFFERED_KEY] = merge_offered(
            (prev or {}).get(OFFERED_KEY), offered)
        # **`taken` is MEASURED here, never carried in.** A caller building the
        # block from an offer knows what it INTENDED to take, and the two are
        # not the same number: a file can fail to read, or collide with the
        # primary skin and be dropped as a duplicate. Observed live on CCO
        # 2.0, 2026-09-07 -- an entry said `altskins taken=3` while holding 2.
        # E's whole purpose is that the entry does not overstate what it has,
        # so the count comes from the satellites block that was just written
        # and cannot disagree with it. On a re-collect it is the MERGED total,
        # which is also what "how much of the offer do I now hold" means.
        for kind, blk in entry[OFFERED_KEY].items():
            blk["taken"] = len(entry[SATELLITES_KEY].get(kind) or [])
            # `available` can never be less than `taken`, and correcting it
            # here is the weakest TRUE statement rather than a fabrication: a
            # satellite this entry holds was, by definition, available.
            #
            # It happens for a real reason. `sourceTexture` -- the primary
            # skin -- travels with every collect and is counted under
            # `textures`, but it comes from the CALLER, not from the offer:
            # the Builder passes the quality-8 texture off the loadout while
            # the offer's paired-texture row is the appearance's own declared
            # Texture0, and on CCO's Steel Blade those are two different
            # files. So the entry legitimately held 2 and the offer had
            # counted 1, which rendered as "2 of 1 collected".
            avail = blk.get("available")
            if isinstance(avail, int) and avail < blk["taken"]:
                blk["available"] = blk["taken"]
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

    def prune(self, ident: str, *, kind: str = "",
              paths: Iterable[str] = (), delete_files: bool = True) -> dict:
        """Drop satellites from an entry on purpose.

        **B requires this and it is the price of B.**  Because re-collect
        merges and never removes, an entry accumulates satellites whose source
        may since have changed, and the only way back out has to be an action
        the user named.  Nothing here is reachable from `add`.

        `offered` is deliberately untouched: pruning changes what is HELD, not
        what was once on offer, and rewriting the offer to match would erase
        the very gap E exists to show.  After a prune the entry reads
        "Effects: 0 of 7 collected", which is true.
        """
        e = self.by_id(ident)
        if e is None:
            raise CollectionError(f"no entry {ident!r}")
        dropped = prune_satellites(e, kind=kind, paths=paths)
        gone = {str(r.get("path") or "").replace("\\", "/").lower()
                for r in dropped}
        kept_parts, removed_files = [], []
        for q in e.get("parts", []):
            src = str(q.get("source") or "").replace("\\", "/").lower()
            if gone and src in gone:
                removed_files.append(str(q.get("file") or ""))
                continue
            kept_parts.append(q)
        e["parts"] = kept_parts
        if delete_files:
            for rel in removed_files:
                p = self.root / rel
                if p.is_file():
                    p.unlink()
        self.save()
        return {"id": ident, "dropped": dropped, "files": removed_files,
                "satellites": e.get(SATELLITES_KEY, {}),
                "report": satellite_report(e)}

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
        collisions = 0
        for e in self.entries:
            # An entry's motion and effect files are part of what was kept.
            # Listing only mesh and skin left them in the library folder but
            # outside its namespace: collected, and unbrowsable.
            rels = [e["mesh"], *e.get("skins", []),
                    *[p["file"] for p in e.get("parts", [])]]
            for rel in rels:
                filemap[f"{PROFILE_NAME}/{rel}".lower()] = [
                    "l", f"{ROOT_NAME}/{rel}", e["category"]]

            # -- AND AT THE PATH THE GAME KNOWS IT BY ----------------------
            #
            # The block above publishes what was kept as a browsable PILE,
            # under `collection/<category>/<file>`. That is the right shape
            # for "show me what I have collected" and it is the wrong shape
            # for everything that resolves an asset by the path the client
            # uses: a collected map landed at
            # `collection/maps/sary02_new__art-a239.dds` and nothing looking
            # for `data/map/a/2/a239.dds` found it. Selecting the Collection
            # in the Map Editor listed ZERO maps for exactly this reason --
            # `MapEditor` asks for `map/map/<name>.DMap`, which was in the
            # library and not in its namespace.
            #
            # `source` is the logical path the part was collected FROM, and
            # it is already what `stage` writes the part back to. Publishing
            # it here makes the Collection view a PREVIEW of that staging:
            # what the install looks like with this collection applied.
            #
            # Both spellings point at the same file, so this costs one
            # filemap row per part and no bytes.
            src_of = {"mesh": e.get("sourceMesh"),
                      **{p["file"]: p.get("source") for p in e.get("parts", [])}}
            for rel in rels:
                src = src_of.get("mesh") if rel == e["mesh"] else src_of.get(rel)
                src = str(src or "").replace("\\", "/").strip("/").lower()
                # A path that is not a real logical path is not published:
                # `skins` carry no source, and an entry collected before
                # sources were recorded has none either.
                if not src or src.startswith(PROFILE_NAME + "/"):
                    continue
                if src in filemap:
                    collisions += 1
                filemap[src] = ["l", f"{ROOT_NAME}/{rel}", e["category"]]
        profile = {
            "server": PROFILE_NAME, "tag": PROFILE_TAG,
            "clientVersion": "curated",
            "files": len(filemap), "entries": len(self.entries),
            # Two entries collected from different clients can claim one
            # logical path; the last one written wins, and saying how many
            # did is the difference between a view you can reason about and
            # one that quietly shows you somebody else's art.
            "pathCollisions": collisions,
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

            # A SNIPPET IS NOT A FILE, AND UNTIL 2026-09-16 THIS WROTE ONE
            # OVER THE OTHER.
            #
            # `mapparts` marks a part `apply="merge"` when `source` names a
            # SHARED table and the part's bytes are only the sections or rows
            # THIS map owns -- an `.ani` index, a `GameMap` row set, an
            # `3DEffect.ini` section. Nothing in this file read that field:
            # every part was written as bytes to `dst`, so staging a collected
            # map replaced the shared original with one map's cut of it.
            #
            # MEASURED, by running this function into a throwaway directory
            # rather than reading it (`scratchpad/stage_snippet_probe.py`,
            # sary02_new on 7878):
            #
            #     ani/mapscene-new.ani   staged   14,171 B over 1,127,723 B
            #     ani/ZF.ani             staged    2,322 B over    87,037 B
            #     ani/n-canyon.ani       staged  200,083 B over   387,423 B
            #     sharedArt decisions raised: 0
            #
            # `ani/mapscene-new.ani` is referenced by 208 other maps. That is
            # 98.7% of a shared index deleted, with no warning, by a function
            # whose own comment six lines below says overwriting one "is a
            # decision about all of them".
            #
            # REFUSED RATHER THAN MERGED, and that is the honest fix rather
            # than the lazy one: this project has no splicer for these
            # formats, and writing a merge we have not implemented is exactly
            # what just happened. The refusal names the format and the keys,
            # so the tool that does implement it knows what it owes.
            how = prec.get("apply", "replace")
            if how != "replace":
                keys = prec.get("mergeKeys") or []
                out["skipped"].append(
                    f"{role}: {dst} needs a {how} of "
                    f"{len(keys)} {prec.get('mergeFormat') or 'unknown-format'} "
                    f"key(s), and staging writes whole files -- writing this "
                    f"cut here would delete every other map's rows")
                out.setdefault("needsMerge", []).append({
                    "path": dst, "role": role, "apply": how,
                    "format": prec.get("mergeFormat") or "",
                    "keys": list(keys),
                    "why": (f"{dst} is a shared table; this part carries only "
                            f"the {len(keys)} key(s) this entry owns"),
                })
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
