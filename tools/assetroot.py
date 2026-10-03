#!/usr/bin/env python3
r"""assetroot.py -- ONE companion-set resolver, three presentations.

`docs/comod_backlog.md` section 6 asked for this in one sentence: the Model
Viewer, the Character Builder and the new "view asset root" panel are the SAME
SHAPE -- a SUBJECT plus its SATELLITES -- and must not be three separate
mechanisms that drift apart. This module is that one mechanism.

    from assetroot import resolve, render, to_json
    with depclose.DepGraph(root) as g:
        sat = resolve(g, "c3/mesh/440140.c3")
        render(sat)              # the Asset Root text view (nothing filtered)
        model = sat.model_view() # what the Model Viewer would show (present-only)

---------------------------------------------------------------------------
THIS IS A COMPOSITION, NOT A SECOND CLOSURE.
---------------------------------------------------------------------------

`tools/depclose.py` already computes the dependency closure of one asset: its
declarations, the appearance rows that reference it, the effect layers that
name it, its PHY/MOTI pairing, its role-part/motion mapping, and -- the whole
point of depclose -- the declared-and-present / declared-but-absent /
present-but-undeclared split plus the reference classes it CANNOT walk. This
module calls `DepGraph.impact()` for that spine and groups the answer into the
satellite types the panel names (geometry, textures incl. alternatives,
animations, effects across all three forms, the binding rows, materials),
adding the two things a closure-by-reference does not itself carry:

* **texture ALTERNATIVES** -- the garment index (`AssetRoot._art_by_garment`)
  already knows which stems share a garment id, so "pick another skin" is a
  query we can already answer;
* **effect FORM** -- an effect layer is one of PHY+MOTI, SHAP+SMOT (ribbon) or
  PTCL/PTC3 (particle); `EffectClosure` already classifies each layer and this
  module carries that label so all three forms sit in the panel on equal
  footing (all VIEWABLE per `tools/webui/fx.js`, only the WRITE column differs).

There is exactly one appearance walker (`depclose`) and one effect walker
(`effects.EffectDB`, reached through depclose) in this repo, and this module
adds neither.

---------------------------------------------------------------------------
BULLETPROOF MEANS NEVER CLAIMING A COMPLETENESS IT DOES NOT HAVE.
---------------------------------------------------------------------------

This panel is the single easiest place in COMod to mislead, because a tidy
list of companions reads as an exhaustive one. `depclose` prints its own blind
spots on every run (whole classes it cannot walk -- the Themida-packed exe,
npc/monster/scene tables, refs embedded in another asset's bytes -- and the
measured dangling counts). This module INHERITS that discipline rather than
dropping it for being untidy:

* every satellite carries `present`, so a declared-but-absent companion is
  SHOWN as absent, never omitted;
* `Satellites.limits` is `impact.limits` verbatim, carried into every view;
* `unresolved` collects the satellites the closure NAMED but could not resolve
  to a file, and `unresolved_count` drives the "and N I could not resolve"
  line. A list of 6 that hides 3 is worse than 6 + "and 3 unresolved".

The Model Viewer view (`model_view`) filters absent satellites OUT of the
lists -- but it keeps `unresolved_count` and `limits`, so filtering for
tidiness never becomes a claim of completeness.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent / "core") not in sys.path:
    sys.path.insert(0, str(_HERE.parent / "core"))

import depclose                                              # noqa: E402


# ---------------------------------------------------------------------------
# the satellite record
# ---------------------------------------------------------------------------

#: The satellite groups, in the order the panel lists them. Named here so the
#: text view, the JSON and the tests cannot drift on the spelling.
GROUPS = ("geometry", "texture", "animation", "effect", "binding", "material")

#: The three animation forms an effect layer can hold. Mirrors
#: `depclose.FORM_LABEL` values; re-exported so a caller need not import both.
FORM_LABEL = depclose.FORM_LABEL


@dataclass
class Satellite:
    """One companion of the subject. `present` is the honesty axis: a
    declared-but-absent satellite is a `Satellite(present=False)`, never a
    dropped row."""
    group: str                 # one of GROUPS
    label: str                 # human label, e.g. "paired texture"
    path: str = ""             # logical path when the satellite is a file
    asset_id: str = ""
    present: bool = True       # does THIS install ship it
    foreign: bool = False      # served from another install
    source: str = ""           # table / archive / how it was reached
    form: str = ""             # effect form label, for group == "effect"
    alternative: bool = False  # a texture the subject COULD use, not its primary
    note: str = ""
    #: THE KEY THIS SATELLITE WAS REACHED BY, structured -- for a binding,
    #: the rule row's own fields (`appearance`, `action`, `shape`, `terrain`,
    #: `weapon_type`, `type_name`, `category`, `role`, `table`).
    #:
    #: Empty for every other group. It exists because `label` is PROSE: a
    #: view that wants to navigate to the appearance a row names has to
    #: either be handed the id or parse it back out of a sentence, and a
    #: regex over a human string is how a rename becomes a silent dead link.
    #: The prose stays exactly as it was -- this is beside it, not instead
    #: of it, so nothing a reader sees changes when a caller ignores it.
    rule: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Satellites:
    """A subject and its satellites -- the value the three views render.

    Every list is the UNFILTERED closure. `model_view()` and `asset_root()`
    are two presentations of this one object; they differ only in what they
    hide, never in what they measure.
    """
    subject: str
    root: str
    kind: str                  # "mesh" | "texture" | "other"
    status: str                # depclose status string
    present: bool
    foreign: bool = False
    located: str = ""
    measured: bool = True
    geometry: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    animations: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    bindings: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    #: Satellites the closure NAMED but could not resolve to a file in this
    #: install -- declared-but-absent art, an effect layer whose id resolves
    #: nowhere. Kept as a list so the view can name them, and counted by
    #: `unresolved_count` so it can say how many.
    unresolved: list = field(default_factory=list)
    #: `impact.limits` verbatim: the reference classes this closure CANNOT
    #: walk, carried into every view including the tidy one.
    limits: list = field(default_factory=list)

    # -- EFFECT-subject fields ---------------------------------------------
    # Empty for a FILE subject, filled by `resolve_effect`. They are on the
    # one dataclass rather than in a subclass because the whole point of
    # backlog section 6 is that these are presentations of ONE shape; a second
    # class is the fourth subsystem it says not to build.
    #: `depclose.effect_tables()` rows -- which FILE actually answered for each
    #: effect table on this base. THE TWIN TRAP: where a compiled `.dbc` twin
    #: exists the client reads the twin and the `.ini` is a decoy, and the two
    #: are DIFFERENT ANSWERS. A view that does not print this is reading a
    #: file it cannot name.
    tables: list = field(default_factory=list)
    #: The `3DEffect` row's OWN `Amount`. Compared against `len(effects)` by
    #: the closure, which files a limit when they disagree.
    declared_layers: int = 0
    #: delay / loop / frame interval / duration, as `resolve_effect` built it.
    timing: dict = field(default_factory=dict)
    #: Substring matches, when the name is NOT defined. `found=False` plus a
    #: populated `near` is "no such effect, did you mean"; `found=False` with
    #: an empty `near` is "nothing like it either".
    near: list = field(default_factory=list)
    #: True when `3DEffect` holds more than one row under this name.
    duplicate_name: bool = False

    # -- groups ------------------------------------------------------------
    def group(self, name: str) -> list:
        return getattr(self, {"geometry": "geometry", "texture": "textures",
                              "animation": "animations", "effect": "effects",
                              "binding": "bindings", "material": "materials"
                              }[name])

    def all_satellites(self) -> list:
        """Every satellite of every group, absent ones included. This is the
        Asset Root view's spine -- it filters NOTHING."""
        out: list = []
        for name in GROUPS:
            out.extend(self.group(name))
        return out

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    # -- presentations -----------------------------------------------------
    def asset_root(self) -> "Satellites":
        """The unfiltered view -- the object itself. Named so call sites read
        as one of the three presentations rather than 'the raw thing'."""
        return self

    def model_view(self) -> dict:
        """What the Model Viewer would show: PRESENT satellites only, but with
        `unresolved_count` and `limits` kept so the tidy list never reads as a
        complete one. Returns a plain dict -- the view is a projection, not a
        second `Satellites`."""
        def keep(items):
            return [s for s in items if s.present]
        return {
            "subject": self.subject, "kind": self.kind, "status": self.status,
            "present": self.present, "foreign": self.foreign,
            "located": self.located, "measured": self.measured,
            "geometry": [s.as_dict() for s in keep(self.geometry)],
            "textures": [s.as_dict() for s in keep(self.textures)],
            "animations": [s.as_dict() for s in keep(self.animations)],
            "effects": [s.as_dict() for s in keep(self.effects)],
            "bindings": [s.as_dict() for s in self.bindings],
            "materials": [s.as_dict() for s in keep(self.materials)],
            # The honesty carries survive the filter, ALWAYS. `tables` is
            # one of them: the tidy view is the one most likely to be read as
            # the whole truth, so it is the LAST place provenance may be
            # dropped for being untidy.
            "unresolvedCount": self.unresolved_count,
            "limits": list(self.limits),
            "tables": list(self.tables),
        }


# ---------------------------------------------------------------------------
# the resolver -- the ONE interface
# ---------------------------------------------------------------------------

def resolve(graph: "depclose.DepGraph", subject: str, *,
            geometry: bool = True, effect_forms: bool = True,
            alternatives: bool = True,
            loadout: Optional[dict] = None,
            loadout_given: bool = False) -> Satellites:
    r"""The subject's satellites, grouped, with the closure's blind spots kept.

    `graph` is a live `depclose.DepGraph` over one install; `subject` is a
    logical path (``c3/mesh/440140.c3``) or a bare id the caller has already
    turned into a path via `depclose.resolve_target`.

    Flags exist so a cheap caller (a test on a synthetic root, a fast webui
    poll) can turn off the parts that read C3 geometry or walk the effect
    tables without changing WHICH satellites are reported -- only how much is
    known about each. Nothing a flag turns off is silently dropped: it lands
    in `limits` instead.

    `loadout` is ``{"body": <appearance>, "right": <appearance>,
    "left": <appearance>, "shape": <override>}`` -- the EQUIPPED COMPOSITION,
    which is the one thing a per-file resolver cannot know and cannot infer.
    Which motion set animates a body depends on the weapon in its hand, and
    the weapon is a different subject in a different table; with no loadout
    this reports the motion BINDING exactly as it always did, and with one it
    additionally enumerates the motions that composition can actually play.
    See `_fill_animations`.
    """
    imp = graph.impact(subject)
    sat = Satellites(
        subject=imp.target, root=imp.root, kind=imp.kind,
        status=imp.status, present=imp.located is not None,
        foreign=imp.foreign,
        located=str(imp.located) if imp.located is not None else "",
        limits=list(imp.limits))

    if imp.kind == "other":
        # depclose already refused this and said the sections mean UNMEASURED.
        sat.measured = False
        return sat

    _fill_geometry(graph, sat, imp, geometry)
    _fill_materials(graph, sat, imp, geometry)
    _fill_textures(graph, sat, imp, alternatives)
    _fill_animations(sat, imp, loadout, loadout_given)
    _fill_effects(graph, sat, imp, effect_forms)
    _fill_bindings(sat, imp)
    # De-duplicate the blind-spot lines, order preserved. `depclose.impact`
    # re-appends the install-specific limits (no c3.wdb, ...) to the shared
    # `build_limits` on every call, so two resolves on ONE graph would
    # otherwise carry a growing tail of identical lines. A duplicate blind
    # spot adds no information; dropping it makes the view deterministic --
    # the re-point invariant `tests/test_assetroot.py` pins -- without ever
    # dropping a DISTINCT one.
    sat.limits = list(dict.fromkeys(sat.limits))
    return sat


#: Write status per effect-family chunk form, MEASURED, so the Effects Viewer
#: cannot over- or under-promise. All seven forms in `WRITABLE_FORMS` round-trip
#: BYTE-EXACT (444,303 / 444,303 chunks); the earlier "effects are view only"
#: reading was made against a base that predated the writers and is obsolete.
#: `MNEW` is deliberately in neither list -- all 137,232 enumerated instances
#: are the single byte `0x70`, zero variance, so it needs no writer and
#: calling it "read-only" would report a limitation that does not exist.
WRITABLE_FORMS = ("SHAP", "SMOT", "PTCL", "PTCX", "PTC3", "RIBB", "RMOT")
READ_ONLY_FORMS = {
    "CCFL": "cloth/flag -- decoded exactly (541,869 chunks on 13 installs), "
            "no writer",
    "CAME": "camera -- decoded, no writer",
    "OMNI": "2 chunks in the whole corpus, NOT parsed",
}
NO_WRITER_NEEDED = {
    "MNEW": "all 137,232 enumerated instances are the single byte 0x70, "
            "zero variance -- nothing to write",
}


def resolve_effect(graph: "depclose.DepGraph", name: str, *,
                   geometry: bool = True, connections: bool = True
                   ) -> Satellites:
    r"""An EFFECT as a first-class subject, in the same `Satellites` shape.

    This is the one genuinely new piece the Effects Viewer needed. Everything
    else it shows is a presentation of something that already existed; making
    an effect a SUBJECT is not, because `resolve()` above takes a FILE and an
    effect is not a file -- it is a row in `3DEffect` naming layers that name
    ids that name files.

    The mapping onto the six groups, and each is a real edge, not a label:

      geometry   each layer's MESH    (id -> path via `3DEffectObj`)
      texture    each layer's TEXTURE (id -> path via `3dtexture`)
      effect     the layer itself, tagged with the animation FORMS it carries
                 and with that form's WRITE STATUS
      binding    **the REVERSE** -- the weapon / action / map rule rows that
                 name this effect (`DepGraph.effect_users`)
      animation  the effect's own timing: delay, loop, frame interval
      material   not applicable to an effect; always empty

    THE HONESTY CARRIES ARE NOT OPTIONAL AND ARE NOT TIDIED.
    A layer id that neither `3DEffectObj` nor `3dtexture` resolves lands in
    `unresolved`, never dropped. **THE STANDING "125 of 3,987" FIGURE IS NOT
    CORPUS-WIDE and this docstring used to say it was.** 3,987 is CCO's
    `effect_layers_total` alone -- `docs/effects.md` states it twice, in a
    CCO-only table and in the CCO column of the six-base table -- against
    5,099 on 5165/7878, 8,759 on 5517 and 13,248 on 6090. So a rate computed
    from it describes ONE install. The per-base unresolved counts that ARE
    published (`tests/test_effect_closure.py`) are 0 on 5017/5065/5165/5517/
    7878 and 9 on 6090/6609/7205, which is a different measurement again --
    it counts LAYERS where 125 counts LINKS. Do not re-broaden this: a narrow
    limit recorded against a broad thing is misscoped from birth and waiting
    never fixes it. `limits` inherits `effect_closure.limits` AND
    `effect_users.limits` verbatim, which is what carries the six sibling rule
    tables `EffectDB` does not read (674 keys on 6090, 1,168 on 6609, 135 on
    7632, 140 on 7878). A connection list that omitted those silently would be
    a trap the user cannot detect; backlog section 6 is explicit that this is
    worse than no panel at all.
    """
    c = graph.effect_closure(name, geometry=geometry)
    sat = Satellites(
        subject=name, root=str(getattr(graph, "root", "")), kind="effect",
        status=("defined" if c.found else "NOT DEFINED in 3DEffect"),
        present=c.found, located=c.source, measured=c.measured,
        limits=list(c.limits))
    # The provenance rows, verbatim from `depclose.effect_tables()`. This is
    # THE TWIN TRAP defence: where a compiled `.dbc` twin exists the client
    # reads the twin and the `.ini` beside it is a decoy, and the two give
    # DIFFERENT ANSWERS on the same base (see the `table_file` header in
    # depclose). Carried on the object so the page names the file that
    # answered, per base, rather than a reader assuming the plaintext.
    sat.tables = list(c.tables)
    sat.declared_layers = c.declared_layers
    sat.timing = {
        "delay": c.delay, "loopTime": c.loop_time,
        "loopInterval": c.loop_interval, "frameInterval": c.frame_interval,
        "endless": c.endless, "durationMs": c.duration_ms,
        "colorEnable": c.color_enable, "offset": list(c.offset),
    }
    sat.near = list(c.near)
    sat.duplicate_name = c.duplicate_name

    if not c.measured:
        # Every list below would mean UNMEASURED, not zero. Said once here
        # rather than letting an empty panel read as a finding.
        return sat

    # THE CONNECTION WALK RUNS EVEN WHEN THE EFFECT IS NOT DEFINED, and that
    # is the whole point rather than a tolerance. A rule row that names an
    # effect `3DEffect` does not define is a REAL and reportable state --
    # 32 such names on 5517 -- and it is a BROKEN RULE, which is a different
    # and more actionable finding than a broken effect. Returning early here
    # reported "nothing reaches this" for every one of them, which is exactly
    # the silently-empty answer backlog section 6 forbids: `effect_users`
    # had the rows the whole time and this function threw them away.
    if connections:
        _fill_effect_connections(graph, sat, name)
    else:
        sat.limits.append(
            "the connection walk was turned OFF for this call: the weapon, "
            "action and map rows that name this effect are UNMEASURED here, "
            "not absent")

    if not c.found:
        if sat.bindings:
            sat.limits.append(
                f"{len(sat.bindings)} rule row(s) NAME this effect and "
                f"3DEffect does not define it -- the rule is broken, not the "
                f"art. The client has nothing to play for these rows.")
        sat.limits = list(dict.fromkeys(sat.limits))
        return sat

    for L in c.layers:
        _fill_effect_layer(sat, L, geometry)

    sat.animations.append(Satellite(
        group="animation", label=_timing_label(c), present=True,
        source=c.source or "3DEffect",
        note=("ENDLESS -- the client loops it until something stops it, so it "
              "has no duration" if c.endless else
              "duration is delay + loops x frames x frame interval")))

    sat.limits = list(dict.fromkeys(sat.limits))
    return sat


def _timing_label(c) -> str:
    if c.endless:
        return "endless, frame interval %d ms" % c.frame_interval
    d = ("%.0f ms" % c.duration_ms) if c.duration_ms is not None else "unknown"
    return ("%s  (delay %d, %d loop(s), frame interval %d ms)"
            % (d, c.delay, c.loop_time, c.frame_interval))


def _fill_effect_layer(sat: Satellites, L, geometry: bool) -> None:
    """One layer's mesh, texture and form, with the two unresolvable states
    kept APART: an id that resolves to no path at all, and an id that resolves
    to a path this install does not ship. Both are `present=False`; only the
    first is a broken TABLE, and the note says which."""
    for asset in (L.mesh, L.texture):
        if not asset.asset_id:
            continue
        group = "geometry" if asset.role == "mesh" else "texture"
        if not asset.path:
            note = ("id %s resolves to NO PATH in %s (%s) -- the table has no "
                    "row for it" % (asset.asset_id, asset.table,
                                    asset.table_file))
        elif not asset.present:
            note = ("declared in %s (%s) and this install does not ship the "
                    "file" % (asset.table, asset.table_file))
        else:
            note = "from %s (%s)" % (asset.table, asset.table_file)
        s = Satellite(
            group=group, label="layer %d %s" % (L.index, asset.role),
            path=asset.path, asset_id=str(asset.asset_id),
            present=bool(asset.present), foreign=bool(asset.foreign),
            source=asset.table_file or asset.table, note=note)
        sat.group(group).append(s)
        if not asset.present:
            sat.unresolved.append(s)

    forms = list(L.forms)
    if L.error:
        form_txt = "geometry error: " + L.error
    elif forms:
        form_txt = "; ".join(forms)
    elif not geometry:
        form_txt = "form UNMEASURED (geometry read was off)"
    elif not L.geometry_read:
        form_txt = ("form UNKNOWN -- the mesh was not read, so this is NOT "
                    "'no animation'")
    else:
        form_txt = "no animation chunk in the mesh"
    note = ("%d part(s), %d frame(s); blend %s" % (L.parts, L.frames, L.blend)
            if L.geometry_read else "blend " + L.blend)
    if L.undecoded:
        note += "; UNDECODED chunk(s): " + ", ".join(L.undecoded)
    sat.effects.append(Satellite(
        group="effect", label="layer %d" % L.index, form=form_txt,
        present=True, source=_write_status(L), note=note))


def _write_status(L) -> str:
    """What can be WRITTEN back for this layer, per chunk form.

    Asked per layer because a container can carry more than one form and the
    answer differs between them -- a layer that is writable in its PTCL and
    read-only in its CAME is BOTH, and reporting only one of the two is what
    the obsolete "effects are view only" reading got wrong in the other
    direction.
    """
    seen = {c.upper() for c in L.undecoded}
    ro = sorted(seen & set(READ_ONLY_FORMS))
    if ro:
        return "READ-ONLY chunk(s) present: " + ", ".join(
            "%s (%s)" % (c, READ_ONLY_FORMS[c]) for c in ro)
    return "writable (all seven chunk forms round-trip byte-exact)"


def _fill_effect_connections(graph, sat: Satellites, name: str) -> None:
    """The reverse edge: who reaches this effect. Bindings are shown in FULL
    and never filtered -- hiding one hides a user, which is the same rule
    `_fill_bindings` follows for appearance rows."""
    try:
        uses = graph.effect_users(name)
    except Exception as e:                                   # noqa: BLE001
        sat.limits.append(
            "the connection walk RAISED (%s: %s); weapons/actions/map effects "
            "are UNMEASURED, not absent" % (e.__class__.__name__, e))
        return
    if not uses.measured:
        sat.limits.append(
            "the rule tables would not load: the weapon, action and map rows "
            "that name this effect are UNMEASURED, not absent")
        sat.limits.extend(uses.limits)
        return
    for cat, rows in (("weapon", uses.weapons), ("action", uses.actions),
                      ("map", uses.maps)):
        for r in rows:
            sat.bindings.append(Satellite(
                group="binding", label=r.label, present=True,
                source=r.table_file, form=cat,
                note="%s -> %s" % (r.table, r.role),
                # The row's OWN fields, so a view can walk to what the row
                # names instead of parsing `label`. `category` is carried
                # even though it duplicates `form` because `form` is the
                # UI's bucket and `category` is the walk's answer, and the
                # day they diverge the honest thing is to be able to see it.
                rule={"category": r.category, "role": r.role,
                      "table": r.table, "tableFile": r.table_file,
                      "appearance": r.appearance, "action": r.action,
                      "shape": r.shape, "terrain": r.terrain,
                      "weaponType": r.weapon_type, "typeName": r.type_name,
                      "effect": r.effect}))
    sat.limits.extend(uses.limits)


# ---------------------------------------------------------------------------
# MULTI-BASE COMPARISON -- the same named effect across several installs
#
# WHY THIS IS THE ONE VIEW THAT CANNOT GUESS THE FILE THAT ANSWERED.
# The twin trap is a PER-BASE fact. `depclose.table_file`'s header states it:
# *"an id that resolves out of `3DEffectobj.dbc` and an id that resolves out of
# `3DEffectObj.ini` are DIFFERENT ANSWERS on the same base."* The twins are
# `3DEffect.dbc`, `3DEffectobj.dbc`, `3DTexture.dbc` and `3DObj.dbc`, and they
# ship ONLY on 5517/6090/6609/7205. So a side-by-side is the one place where
# "this base answered out of a .dbc and that one out of the .ini" becomes
# visible -- and equally the one place where getting the provenance wrong
# silently MANUFACTURES or ERASES a divergence.
#
# MEASURED, 2026-09-07, over the read-only baseline, and it is not
# hypothetical. Reading the DECOY `3DEffect.ini` instead of the live
# `3DEffect.dbc` changes the answer for 18 names on 6090 and 19 on 6609/7205.
# `InsigniaNoble02s` is the cleanest of them:
#
#     base   file that answered   layers
#     5017   3DEffect.ini (no twin)   4     9474 9475 9476 9477
#     5165   3DEffect.ini (no twin)   4     9474 9475 9476 9477
#     5517   3DEffect.dbc             4     9474 9475 9476 9477
#     6090   3DEffect.dbc             2     9474 9475
#     6609   3DEffect.dbc             2     9474 9475
#     7205   3DEffect.dbc             2     9474 9475
#     7878   3DEffect.ini (no twin)   4     9474 9475 9476 9477
#
# and on 6090/6609/7205 the decoy `3DEffect.ini` sitting beside the dbc still
# says FOUR. So a comparison that read the decoy would render this screen as
# SEVEN IDENTICAL COLUMNS -- it would not merely mislabel the divergence, it
# would delete it. That is the mutant `tests/test_fxcompare.py` fires.
#
# THE THREE COLUMN STATES, and they are three different field settings rather
# than three empty cells:
#
#   * `measured=False`  -- the effect tables would not load on that base.
#     Every value in the column means UNMEASURED, never zero, and the column
#     takes NO part in any verdict.
#   * `measured=True, tablesKnown=False` -- the tables loaded but the
#     provenance did not. The values are real but nothing can say which file
#     produced them, so the column is UNPROVENANCED and also takes no part in
#     a verdict. A comparison that folded these in would be asserting a
#     difference between two files it cannot name.
#   * `measured=True, tablesKnown=True` -- COMPARABLE. Only these carry
#     verdicts, and `comparedOver` names exactly which ones did.
# ---------------------------------------------------------------------------

#: The effect tables whose provenance the comparison puts in its own row.
#: `3DEffect` is the definition, `3DEffectObj` the layer -> mesh table and
#: `3dtexture` the layer -> texture table -- the three-link chain backlog
#: section 15 says the page must show all three links of.
COMPARE_TABLES = ("3DEffect", "3DEffectObj", "3dtexture")


def effect_column(graph: "depclose.DepGraph", name: str, *,
                  base_id: str = "", label: str = "",
                  geometry: bool = True, connections: bool = True) -> dict:
    """One base's answer for one named effect, WITH the file that answered.

    A thin projection of `resolve_effect` -- it re-walks nothing. The two
    additions are `answeredBy`, lifted from `depclose.effect_tables()` so the
    column can name its own provenance, and `unreadTables`, which is itself a
    real per-base difference (674 keys on 6090, 1,168 on 6609, 135 on 7632,
    140 on 7878, 11 on 5517, none on 5017/5065/5165).

    `answeredBy` is derived from the `tables` rows and NOTHING ELSE. There is
    deliberately no fallback to "well, it must have been the .ini": that
    fallback is plausible, wrong on four bases, and is exactly how this class
    of defect stays invisible (see `depclose.table_file`, which was written
    after a fallback like it stamped every row in that module with a generic
    stem for its whole history).
    """
    sat = resolve_effect(graph, name, geometry=geometry,
                         connections=connections)
    root = str(getattr(graph, "root", ""))
    col = {
        "id": base_id or root,
        "label": label or Path(root).name or root,
        "root": root,
        "state": "ready",
        "measured": bool(sat.measured),
        "present": bool(sat.present),
        "status": sat.status,
        "located": sat.located,
        "declaredLayers": int(sat.declared_layers),
        "timing": dict(sat.timing),
        "near": list(sat.near),
        "duplicateName": bool(sat.duplicate_name),
        "unresolvedCount": int(sat.unresolved_count),
        "unresolved": [s.as_dict() for s in sat.unresolved],
        "limits": list(sat.limits),
        "tables": list(sat.tables),
    }
    col["answeredBy"] = _answered_by(sat.tables)
    # `tables` empty means the provenance read FAILED or the effect db would
    # not load -- never "there is no compiled twin here". `effect_tables()`
    # returns [] in exactly that case and in no other.
    col["tablesKnown"] = bool(sat.tables)
    try:
        unread = depclose.unread_effect_tables(root)
    except Exception as e:                                   # noqa: BLE001
        unread = []
        col["limits"].append(
            "the sibling rule-file scan RAISED on this base (%s: %s), so the "
            "count of rule files EffectDB does not read is UNMEASURED here, "
            "not zero" % (e.__class__.__name__, e))
        col["unreadKnown"] = False
    else:
        col["unreadKnown"] = True
    col["unreadTables"] = unread
    col["unreadRows"] = sum(int(u.get("rows") or 0) for u in unread)
    col["layers"] = _compare_layers(sat)
    col["parsedLayers"] = len(col["layers"])
    forms: list = []
    for L in col["layers"]:
        for f in L["forms"]:
            if f not in forms:
                forms.append(f)
    col["forms"] = forms
    #: Layers whose FORM could not be read on this base -- the mesh is not
    #: shipped here, or the geometry read was off. `forms` above is therefore
    #: a floor and not a census whenever this is non-zero, which is why it is
    #: counted rather than left to be inferred from a shorter list.
    col["formsUnreadLayers"] = sum(1 for L in col["layers"]
                                   if not L["formsKnown"])
    col["formsComplete"] = col["formsUnreadLayers"] == 0
    cats = {"weapon": 0, "action": 0, "map": 0}
    for b in sat.bindings:
        cats[b.form] = cats.get(b.form, 0) + 1
    cats["total"] = sum(v for k, v in cats.items() if k != "total")
    col["connections"] = cats
    return col


def _answered_by(tables: list) -> dict:
    """`{table: {file, form, rows, notRead, note}}`, from the rows and only
    from the rows. A table with no row is ABSENT from this dict rather than
    defaulting to anything -- "I was not told" and "it was the plaintext" are
    different answers and only one of them is ever knowledge."""
    out: dict = {}
    for r in tables or []:
        out[str(r.get("table"))] = {
            "file": r.get("file"),
            "form": r.get("form"),
            "rows": r.get("rows"),
            "notRead": r.get("not_read"),
            "note": r.get("note") or "",
        }
    return out


def _compare_layers(sat: Satellites) -> list:
    """The layer chain, re-keyed by layer index so columns line up by ROW.

    Each cell carries the id, the path, whether this install ships the file
    AND the table file that answered for that id -- the same per-satellite
    provenance `_fill_effect_layer` already wrote into `source`. Two bases can
    agree on the id and disagree on which table resolved it; a row that showed
    only the id would hide that.
    """
    by: dict = {}

    def slot(idx: int) -> dict:
        return by.setdefault(idx, {
            "index": idx,
            "meshId": "", "meshPath": "", "meshFile": "", "meshPresent": None,
            "texId": "", "texPath": "", "texFile": "", "texPresent": None,
            "forms": [], "formsKnown": False, "formNote": "",
            "writeStatus": "", "note": "",
        })

    def idx_of(lbl: str):
        bits = str(lbl).split()
        for b in bits:
            if b.isdigit():
                return int(b)
        return None

    for s in sat.geometry:
        i = idx_of(s.label)
        if i is None:
            continue
        d = slot(i)
        d["meshId"] = s.asset_id
        d["meshPath"] = s.path
        d["meshFile"] = s.source
        d["meshPresent"] = bool(s.present)
    for s in sat.textures:
        i = idx_of(s.label)
        if i is None:
            continue
        d = slot(i)
        d["texId"] = s.asset_id
        d["texPath"] = s.path
        d["texFile"] = s.source
        d["texPresent"] = bool(s.present)
    for s in sat.effects:
        i = idx_of(s.label)
        if i is None:
            continue
        d = slot(i)
        # A FORM THAT WAS NOT READ IS NOT A FORM, and on this screen the
        # distinction is load-bearing. `_fill_effect_layer` writes one of five
        # things into `form`, and three of them are statements about the READ
        # rather than about the layer -- 7878 does not ship two of
        # `InsigniaNoble02s`'s meshes, so its layers 2 and 3 carry "form
        # UNKNOWN". Folded in as a form, that renders as "5517 has PTCL and
        # 7878 has something else", which is a difference between the CLIENTS
        # that was never measured. The task is explicit that a form present on
        # one base and absent on another is a REAL difference (RIBB/RMOT ship
        # on 2 of 34 installs) and that the view must say WHICH it is looking
        # at; `formsKnown` is how it says so.
        txt = str(s.form or "")
        if (txt.startswith("form UNKNOWN") or txt.startswith("form UNMEASURED")
                or txt.startswith("geometry error:")):
            d["forms"] = []
            d["formsKnown"] = False
            d["formNote"] = txt
        else:
            # Includes "no animation chunk in the mesh", which IS a
            # measurement: the mesh was read and carries no animation.
            d["forms"] = [f.strip() for f in txt.split(";") if f.strip()
                          and not f.strip().startswith("no animation chunk")]
            d["formsKnown"] = True
            d["formNote"] = txt if not d["forms"] else ""
        d["writeStatus"] = s.source
        d["note"] = s.note
    return [by[k] for k in sorted(by)]


#: A row's `kind`, which decides how the page reads a `differs`.
#:
#:   `fact`        -- the bases really do hold different data. A finding.
#:   `provenance`  -- the bases answered out of different FILES. Not a defect
#:                    and not a data difference; it is the thing that makes
#:                    every `fact` row above it interpretable, and on
#:                    5517/6090/6609/7205 vs everything else it WILL differ.
#:   `coverage`    -- how much of the base this page could not read at all.
ROW_KINDS = ("provenance", "fact", "coverage")


def compare_effect_columns(columns: list) -> dict:
    """Line up N `effect_column` payloads and say where they disagree.

    THE RULE THAT MAKES THIS SAFE: a verdict is computed over COMPARABLE
    columns only -- `measured` AND `tablesKnown` -- and `comparedOver` names
    them, every row, every time. Two columns where one is unmeasured are NOT
    "the same" and NOT "different"; they are `insufficient`, because a base
    whose tables would not load has told you nothing about the effect and
    folding its blank cell into an equality test invents an agreement that
    was never measured. The same applies to a column whose provenance failed:
    its values are real, but a difference between two files you cannot name
    is not a finding you can act on.

    `provenanceSplit` is the headline this whole view exists for: a dict, one
    entry per table in `COMPARE_TABLES` that the comparable columns did NOT
    all answer out of the same FORM for, naming the ids on each side. Empty
    means every compared column read that table the same way. A non-empty one
    means a `.dbc` base is being read beside an `.ini` base, and every `fact`
    row below it must be read in that light.
    """
    ids = [c["id"] for c in columns]
    ready = [c for c in columns if c.get("state") == "ready"]
    comparable = [c for c in ready
                  if c.get("measured") and c.get("tablesKnown")]
    unmeasured = [c for c in ready if not c.get("measured")]
    unprovenanced = [c for c in ready
                     if c.get("measured") and not c.get("tablesKnown")]
    pending = [c for c in columns if c.get("state") not in ("ready", "error")]
    failed = [c for c in columns if c.get("state") == "error"]
    cid = [c["id"] for c in comparable]

    rows: list = []

    def add(key, label, kind, value_of, note="", null_label="not defined here",
            applies_to=None):
        """One comparison row.

        `null_label` is what a `null` in this row MEANS, said per row rather
        than left to the renderer. A blank cell is the exact ambiguity this
        page exists to remove: "the effect is not defined on that base",
        "there is no decoy sibling to name" and "nobody measured it" are
        three different facts that all serialise as `null`, and only the row
        knows which one it is.

        `applies_to` narrows the verdict to the columns where THIS row was
        actually measured, and the columns it excludes are named in
        `notApplicable` rather than dropped. The case it exists for is the
        FORM rows: 7878 does not ship two of `InsigniaNoble02s`'s meshes, so
        its form there is UNREAD -- and a verdict that folded that in would
        report "5517 carries PTCL and 7878 does not" as a difference between
        the clients when nothing about 7878's form was ever measured. A form
        genuinely present on one base and absent on another IS a real
        difference (RIBB/RMOT ship on 2 of 34 installs) and still reads as
        one; only the unread case is set aside, and visibly.
        """
        vals = {}
        for c in ready:
            if not c.get("measured"):
                # AN UNMEASURED COLUMN CARRIES NO VALUE ON THE WIRE, not even
                # a plausible one. `effect_column` leaves `present` False on a
                # base whose tables would not load, which makes several of
                # these lambdas produce exactly the same string a MEASURED
                # absence produces -- `defined` would say "NOT DEFINED" for an
                # install nobody managed to read. Any consumer that trusted
                # the cell would then report an absence that was never
                # measured. The column's own `measured: false` is the
                # authority and this keeps the two from ever disagreeing.
                # An UNPROVENANCED column is NOT treated this way: its values
                # are real, only their attribution is missing.
                vals[c["id"]] = None
                continue
            try:
                vals[c["id"]] = value_of(c)
            except Exception:                                # noqa: BLE001
                vals[c["id"]] = None
        over = list(cid)
        skipped: list = []
        if applies_to is not None:
            keep = []
            for c in comparable:
                try:
                    ok = bool(applies_to(c))
                except Exception:                            # noqa: BLE001
                    ok = False
                (keep if ok else skipped).append(c["id"])
            over = keep
        seen = [vals[i] for i in over]
        if len(over) < 2:
            verdict = "insufficient"
        elif all(v == seen[0] for v in seen):
            verdict = "same"
        else:
            verdict = "differs"
        row = {"key": key, "label": label, "kind": kind,
               "values": vals, "verdict": verdict,
               "comparedOver": over, "note": note,
               "nullLabel": null_label}
        if skipped:
            # NAMED, never silently dropped. A row whose verdict was reached
            # over fewer columns than the header shows must say which ones it
            # left out, or "same" reads as "same everywhere you picked".
            row["notApplicable"] = skipped
        rows.append(row)
        return row

    # -- provenance FIRST. Every fact row below is only readable in its light.
    for t in COMPARE_TABLES:
        add("answeredBy:" + t, t + " read from", "provenance",
            lambda c, t=t: (c["answeredBy"].get(t) or {}).get("file"),
            note="the file that ACTUALLY answered on that base",
            null_label="NOT REPORTED — never read this as 'the .ini'")
        add("decoy:" + t, t + " present and NOT read", "provenance",
            lambda c, t=t: (c["answeredBy"].get(t) or {}).get("notRead"),
            note="a sibling the client does not read; an id resolved out of "
                 "it is a DIFFERENT answer",
            null_label="no unread sibling on that base")
    add("unreadRuleFiles", "sibling rule files EffectDB does not read",
        "coverage",
        lambda c: (None if not c.get("unreadKnown")
                   else "%d file(s), %d key(s)" % (len(c["unreadTables"]),
                                                  c["unreadRows"])),
        note="a real per-base difference, not a read failure: 674 keys on "
             "6090, 1,168 on 6609, 135 on 7632, 140 on 7878, none on "
             "5017/5065/5165",
        null_label="UNMEASURED — the scan did not run")

    # -- the effect itself.
    add("defined", "defined in 3DEffect", "fact",
        lambda c: "defined" if c["present"] else "NOT DEFINED",
        null_label="UNMEASURED")
    add("declaredLayers", "layers the table DECLARES (Amount)", "fact",
        lambda c: c["declaredLayers"] if c["present"] else None,
        null_label="not defined on that base")
    add("parsedLayers", "layers that PARSED", "fact",
        lambda c: c["parsedLayers"] if c["present"] else None,
        null_label="not defined on that base")

    depth = max([c["parsedLayers"] for c in comparable] or [0])
    for i in range(depth):
        nl = "that base's effect has no layer %d" % i
        add("layer%d.mesh" % i, "layer %d mesh id" % i, "fact",
            lambda c, i=i: _layer_field(c, i, "meshId"), null_label=nl)
        add("layer%d.meshFile" % i, "layer %d mesh from" % i, "provenance",
            lambda c, i=i: _layer_field(c, i, "meshFile"), null_label=nl)
        add("layer%d.tex" % i, "layer %d texture id" % i, "fact",
            lambda c, i=i: _layer_field(c, i, "texId"), null_label=nl)
        add("layer%d.texFile" % i, "layer %d texture from" % i, "provenance",
            lambda c, i=i: _layer_field(c, i, "texFile"), null_label=nl)
        add("layer%d.forms" % i, "layer %d form(s)" % i, "fact",
            lambda c, i=i: (lambda L: None if L is None or not L["formsKnown"]
                            else ("+".join(L["forms"]) if L["forms"]
                                  else "no animation chunk"))(
                _layer_slot(c, i)),
            note="a form present on one base and absent on another is a REAL "
                 "difference: RIBB/RMOT ship on 2 of 34 installs (7867, "
                 "7878); SHAP/SMOT are the same thing in the older spelling "
                 "and ship on all 34. A form that could not be READ is a "
                 "different thing and is set aside here, not compared",
            null_label=nl + ", or its form could not be read there",
            applies_to=lambda c, i=i: (lambda L: L is not None
                                       and L["formsKnown"])(_layer_slot(c, i)))
        add("layer%d.formRead" % i, "layer %d form was read" % i, "coverage",
            lambda c, i=i: (lambda L: None if L is None
                            else ("read" if L["formsKnown"]
                                  else "NOT READ — " + (L["formNote"] or "")))(
                _layer_slot(c, i)),
            note="the row above is compared ONLY over the columns this one "
                 "says 'read' for",
            null_label=nl)

    add("forms", "animation form(s), whole effect", "fact",
        lambda c: ("+".join(c["forms"]) if c["forms"] else "none read")
        if c["present"] else None,
        note="the union over the layers whose form WAS read; where the row "
             "below is non-zero this is a floor, not a census",
        null_label="not defined on that base",
        applies_to=lambda c: bool(c["present"]) and bool(c["formsComplete"]))
    add("formsUnread", "layers whose FORM could not be read", "coverage",
        lambda c: c["formsUnreadLayers"] if c["present"] else None,
        note="usually a mesh this install does not ship; the form row above "
             "is compared only over the bases where this is 0",
        null_label="not defined on that base")
    add("duration", "duration", "fact",
        lambda c: _timing_cell(c), null_label="not defined on that base")
    add("frameInterval", "ms per frame", "fact",
        lambda c: (c["timing"] or {}).get("frameInterval") if c["present"]
        else None, null_label="not defined on that base")
    add("connWeapon", "weapon rows that name it", "fact",
        lambda c: c["connections"]["weapon"],
        note="counted over the THREE rule tables EffectDB reads; the sibling "
             "rule files in the coverage row above are not in this number")
    add("connAction", "skill / action rows that name it", "fact",
        lambda c: c["connections"]["action"],
        note="counted over the THREE rule tables EffectDB reads; the sibling "
             "rule files in the coverage row above are not in this number")
    add("connMap", "map rows that name it", "fact",
        lambda c: c["connections"]["map"],
        note="counted over the THREE rule tables EffectDB reads; the sibling "
             "rule files in the coverage row above are not in this number")
    add("unresolved", "satellites named and NOT resolved", "coverage",
        lambda c: c["unresolvedCount"])

    forms_seen = {}
    for c in comparable:
        for f in c["forms"]:
            forms_seen.setdefault(f, []).append(c["id"])

    split = _provenance_split(comparable)
    return {
        "bases": ids,
        "comparedOver": cid,
        "unmeasured": [c["id"] for c in unmeasured],
        "unprovenanced": [c["id"] for c in unprovenanced],
        "pending": [c["id"] for c in pending],
        "failed": [c["id"] for c in failed],
        "rows": rows,
        "provenanceSplit": split,
        "differing": [r["key"] for r in rows
                      if r["verdict"] == "differs" and r["kind"] == "fact"],
        "formsPerBase": forms_seen,
        "note": (
            "Verdicts are computed over the %d comparable base(s) named in "
            "comparedOver and over no others. A base whose effect tables "
            "would not load is UNMEASURED, not empty, and a base whose "
            "provenance could not be read is UNPROVENANCED -- neither can "
            "make a row read 'same'." % len(cid)),
    }


def _layer_slot(c, i: int):
    if not c.get("present"):
        return None
    for L in c["layers"]:
        if L["index"] == i:
            return L
    return None


def _layer_field(c, i: int, key: str):
    L = _layer_slot(c, i)
    return None if L is None else L[key]


def _timing_cell(c):
    if not c.get("present"):
        return None
    t = c["timing"] or {}
    if t.get("endless"):
        return "endless"
    d = t.get("durationMs")
    return ("%d ms" % round(d)) if d is not None else "unknown"


def _provenance_split(comparable: list) -> dict:
    """Which of `COMPARE_TABLES` was read out of more than one FORM.

    `{table: {"dbc": [ids], "ini": [ids], "missing": [ids]}}`, only for tables
    where more than one form appears. This is the single most important thing
    on the screen: below it, "these two bases hold different layer ids" and
    "these two bases were read out of different KINDS of file" stop being the
    same-looking observation.
    """
    out: dict = {}
    for t in COMPARE_TABLES:
        by_form: dict = {}
        for c in comparable:
            f = (c["answeredBy"].get(t) or {}).get("form") or "unknown"
            by_form.setdefault(f, []).append(c["id"])
        if len(by_form) > 1:
            out[t] = by_form
    return out


def _fill_geometry(graph, sat: Satellites, imp, geometry: bool) -> None:
    """The mesh itself and its PHY/MOTI pairing state (subject is a mesh), or
    the meshes a texture pairs with (subject is a texture)."""
    if imp.kind == "mesh":
        note = ""
        present = imp.located is not None
        if imp.anim and "error" not in imp.anim:
            pair = "paired" if imp.anim.get("paired") else "NOT PAIRED"
            note = (f"{imp.anim.get('phy', '?')} PHY / "
                    f"{imp.anim.get('moti', '?')} MOTI -- {pair}")
        elif imp.anim and "error" in imp.anim:
            note = f"container would not read: {imp.anim['error']}"
        elif not geometry:
            note = "geometry not read (PHY/MOTI pairing UNMEASURED)"
        g = Satellite(group="geometry", label="mesh", path=imp.target,
                      present=present, foreign=imp.foreign,
                      source=sat.located, note=note)
        sat.geometry.append(g)
        if not present:
            sat.unresolved.append(g)
    else:
        # A texture's geometry satellites are the meshes that pair with it --
        # the garment siblings on the mesh side.
        stem = _stem(imp.target)
        for kind_dir, path, present in _garment_siblings(graph, imp.target,
                                                         "mesh"):
            g = Satellite(group="geometry", label="mesh (pairs with this skin)",
                          path=path, present=present,
                          source="garment index (last-6 match)")
            sat.geometry.append(g)
            if not present:
                sat.unresolved.append(g)


def _fill_materials(graph, sat: Satellites, imp, geometry: bool) -> None:
    """MATR chunk presence, read off the container where the family has one."""
    if imp.kind != "mesh" or imp.located is None:
        return
    if not geometry:
        return
    try:
        from coassets import C3File                          # noqa: PLC0415
        data = graph.assets.read(imp.target)
        tags = C3File(data, strict=False).tags()
    except Exception as e:                                   # noqa: BLE001
        sat.limits.append(
            f"the MATR check could not read {imp.target} "
            f"({e.__class__.__name__}: {e}); material state UNMEASURED")
        return
    matr = [t for t in tags if t.strip().upper().startswith("MATR")]
    if matr:
        sat.materials.append(Satellite(
            group="material", label="MATR", path=imp.target, present=True,
            source="C3 chunk", note=f"{len(matr)} MATR chunk(s) in container"))


#: How many distinct binding rows `_fill_textures` reads for the mesh case
#: before it stops and records the cap as a limit. A mesh with hundreds of
#: recolor rows is real (weapon.ini names 149 for one mesh), but each row is a
#: dict lookup plus a cached resolve, so the cap is generous.
_TEXTURE_ROW_CAP = 400


def _fill_textures(graph, sat: Satellites, imp, alternatives: bool) -> None:
    """The paired skin(s) the appearance rows DECLARE, plus garment siblings.

    THE PAIRED TEXTURE IS THE APPEARANCE ROW'S `Texture<i>` CELL, NOT the mesh
    stem. On 5065/5517/6609/7205 the texture is a separate id in `c3/texture/`
    (mesh 440140 pairs with texture 440146 -> `c3/texture/440146.dds`); the
    mesh-stem-beside rule only became the client's fallback from 7878, where
    the declared Texture id resolves nowhere. So the authoritative source is
    the binding rows themselves: every distinct `Texture<i>` across the rows
    that name this mesh is a skin it can wear, and 149 recolor rows sharing one
    mesh ARE the alternatives the panel wants. The garment index is then added
    for the 7878-era case, where no declared texture resolves and the beside
    rule is the only thing that finds a skin.
    """
    if imp.kind == "mesh":
        seen_paths: set = set()
        capped = False
        by_file = _tables_by_file(graph)
        # distinct (table, ident, part_index) mesh-side refs
        refs = [(r.table, r.ident, r.part_index)
                for r in imp.appearance_refs]
        for i, (table, ident, part_index) in enumerate(dict.fromkeys(refs)):
            if i >= _TEXTURE_ROW_CAP:
                capped = True
                break
            ini = by_file.get(_norm_name(table))
            if ini is None:
                continue
            app = ini.get(str(ident))
            if app is None:
                continue
            pr = _part_for(app, part_index, imp)
            if pr is None:
                continue
            for role, tid in (("Texture", pr.texture), ("MixTex", pr.mix_tex),
                             ("ThirdTex", pr.third_tex),
                             ("FourthTex", pr.fourth_tex)):
                if not tid or str(tid) in ("0", "-1"):
                    continue
                loc = graph.resolve(str(tid), "texture")
                path = loc.logical if loc is not None else ""
                key = path or f"{role}:{tid}"
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                first = not any(s.group == "texture" and not s.alternative
                               for s in sat.textures)
                a = Satellite(
                    group="texture",
                    label="paired texture" if first else "alternative skin",
                    path=path, asset_id=str(tid),
                    present=loc is not None,
                    foreign=bool(loc is not None and getattr(loc, "foreign",
                                                             False)),
                    alternative=not first,
                    source=f"{table} {role}{part_index}")
                sat.textures.append(a)
                if loc is None:
                    sat.unresolved.append(a)
        if capped:
            sat.limits.append(
                f"more than {_TEXTURE_ROW_CAP} binding rows name this mesh; "
                f"only the first {_TEXTURE_ROW_CAP} were read for their "
                f"Texture cells, so the skin list may be incomplete")
        # Garment siblings -- the 7878-era beside-the-mesh alternatives. Added
        # after the declared ones so a client that declares its textures shows
        # those first, and one that declares none still gets a skin list.
        if alternatives:
            stem = _stem(imp.target)
            for _k, path, present in _garment_siblings(graph, imp.target,
                                                       "texture"):
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                a = Satellite(group="texture", label="alternative skin",
                              path=path, present=present, alternative=True,
                              source="garment index (last-6 match)")
                sat.textures.append(a)
                if not present:
                    sat.unresolved.append(a)
        if not sat.textures:
            sat.textures.append(Satellite(
                group="texture", label="no declared skin",
                present=False,
                source="no binding row names a Texture for this mesh",
                note="the appearance rows carry no resolvable Texture cell and "
                     "no garment sibling was found"))
    else:
        # The subject IS a texture: it is its own primary.
        prim = Satellite(group="texture", label="this texture",
                         path=imp.target, present=imp.located is not None,
                         foreign=imp.foreign, source=sat.located)
        sat.textures.append(prim)
        if imp.located is None:
            sat.unresolved.append(prim)
        if alternatives:
            for _k, path, present in _garment_siblings(graph, imp.target,
                                                       "texture"):
                if _stem(path) == _stem(imp.target):
                    continue
                a = Satellite(group="texture", label="alternative skin",
                              path=path, present=present, alternative=True,
                              source="garment index (last-6 match)")
                sat.textures.append(a)
                if not present:
                    sat.unresolved.append(a)


def _fill_animations(sat: Satellites, imp, loadout: Optional[dict] = None,
                     loadout_given: bool = False) -> None:
    """Motion binding, the shared motion set(s), and -- given a loadout -- the
    motions that composition can actually play.

    WITHOUT A LOADOUT this is what it always was: the BINDING (is this mesh
    safe to re-cut) plus the motion sets the role parts name. `MotionBinding`
    answers a per-FILE question and its honest answer for a player body is
    LOCKED-or-UNKNOWN -- *some* shared external set drives it. **Which one is
    not a property of the file.** It depends on the equipped weapon, which
    lives in a different table and is a different subject, so no amount of
    looking at the mesh can resolve it. That is why the Import/Export panel
    showed `motion binding: UNKNOWN` above a row with nothing to export: not a
    gap in the closure, a question asked of the wrong object.

    WITH A LOADOUT the second half of the key arrives and `anim.loadout_motions`
    resolves the pair through `ini/3dmotion.ini`'s own fallback chain. Each
    distinct motion FILE becomes one satellite -- deduplicated, because actions
    alias heavily (a bow loadout's 198 actions land on 143 files) and one file
    listed six times reads as six animations.

    Two honesty carries travel with the rows and neither is optional:

    * `route` distinguishes a motion the weapon type's OWN set answered from
      one reached by falling back to the unarmed set. 8 of 31 weapon types on
      CCO own no set at all -- type 900 alone is 560 appearances -- and their
      motions are the unarmed ones. A row that presented those as the weapon's
      own would be claiming bespoke animation that does not exist.
    * absent motions are rows, never gaps, exactly as everywhere else here.

    Per-action frame timing is still `comod anim`'s job (frames, loop, chain);
    this reports which FILES a composition plays, not each action's frames.
    """
    binding_row = None
    if imp.anim and "error" not in imp.anim:
        binding = imp.anim.get("binding", "unknown")
        why = imp.anim.get("binding_why", "")
        binding_row = Satellite(
            group="animation", label=f"motion binding: {binding.upper()}",
            present=True, source="c3tex.MotionBinding", note=why)
        sat.animations.append(binding_row)
    seen: set = set()
    for r in imp.role_parts:
        motion = r.get("motion")
        if motion and motion not in seen:
            seen.add(motion)
            sat.animations.append(Satellite(
                group="animation", label="shared motion set", path=motion,
                present=True, source=f"role part {r.get('part', '')}",
                note="a PHY binds to its MOTI by ordinal (docs/modding.md 11)"))
    n_motions = _fill_loadout_motions(sat, imp, loadout, loadout_given)
    if binding_row is not None and n_motions:
        # THE ROW THAT MADE THE OWNER THINK THE FEATURE HAD NOT WORKED.
        # `motion binding: UNKNOWN / nothing to put in a zip` is TRUE and it
        # is about a different question -- whether this MESH may be re-cut --
        # but it is the first line of the ANIMATION section, so it reads as a
        # verdict on the section. The owner had 60 motion files listed under
        # it and reported that they still could not get animations.
        #
        # The row stays, because the fact is real and dropping it would hide
        # a genuine limit. What changes is that it now says what it is NOT
        # about, and names the count sitting below it.
        binding_row.note = (
            (binding_row.note + " " if binding_row.note else "")
            + "THIS ROW IS ABOUT RE-CUTTING THE MESH, NOT ABOUT THE MOTIONS: "
              "%d motion file(s) for this composition are listed below and "
              "are exportable." % n_motions)
    sat.limits.append(
        "per-action frames/timing are enumerated by `comod anim`, not here; "
        "this reports which motion FILES the composition plays, not each "
        "action's frames")


def _fill_loadout_motions(sat: Satellites, imp, loadout: Optional[dict],
                          loadout_given: bool = False) -> None:
    """The enumerated half of `_fill_animations`, split out so the binding
    half stays readable and so a caller with no loadout pays nothing.

    `loadout_given` distinguishes **"nobody told me the composition"** from
    **"this subject is not the body of the composition I was told."** Both
    arrive here as `loadout=None` and they are opposite facts: the first is a
    gap the caller can close, the second is the normal state of every weapon
    and helmet in a loadout that IS known. Reporting the first sentence for
    the second case put "no loadout given" on a panel that had just been
    given one -- a limit that is false is worse than a limit that is absent,
    because a reader acts on it.
    """
    if not loadout:
        if not loadout_given:
            sat.limits.append(
                "no loadout given, so the motion SET is classified but not "
                "enumerated: which set animates a body depends on the "
                "equipped weapon, which is a different subject")
        return 0
    body = str(loadout.get("body") or "").strip()
    if not body:
        return 0
    try:
        import anim as animmod                              # noqa: PLC0415
    except Exception as e:                                  # pragma: no cover
        sat.limits.append("motion enumeration unavailable: %s" % e)
        return 0
    db = loadout.get("db")
    own_db = db is None
    try:
        if db is None:
            db = animmod.AnimDB(imp.root)
        lm = animmod.loadout_motions(
            db, body, right=str(loadout.get("right") or ""),
            left=str(loadout.get("left") or ""),
            shape=loadout.get("shape") or None)
    except Exception as e:                                  # pragma: no cover
        sat.limits.append("motion enumeration failed: %s" % e)
        return 0
    finally:
        if own_db and db is not None:
            try:
                db.cat.assets.close()
            except Exception:
                pass

    for m in lm.motions:
        note = m.how
        if m.route == animmod.ROUTE_FALLBACK:
            note = ("%s -- NOT this weapon's own motion set" % m.how)
        sat.animations.append(Satellite(
            group="animation", label=m.label, path=m.path, present=m.present,
            source="ini/3dmotion %s%s%s" % (lm.shape, lm.weaponset,
                                            m.actions[0] if m.actions else ""),
            note=note,
            rule={"shape": lm.shape, "weaponset": lm.weaponset,
                  "weapon_type": lm.weapon_type, "actions": list(m.actions),
                  "route": m.route, "group": m.group,
                  "names": [n for _c, n, _g in m.named]}))
        if not m.present:
            sat.unresolved.append(sat.animations[-1])
    sat.limits.extend(lm.limits)
    return sum(1 for m in lm.motions if m.present)


def _fill_effects(graph, sat: Satellites, imp, effect_forms: bool) -> None:
    """Effect layers that NAME this asset, tagged with their animation form.

    `impact.effect_refs` is the cheap reverse index (which effect layers reach
    this file). The FORM of each -- PHY+MOTI, SHAP+SMOT ribbon, PTCL/PTC3
    particle -- comes from `EffectClosure`, resolved once per distinct effect
    so the three forms sit in the panel on equal footing.
    """
    forms_by_effect: dict = {}
    if effect_forms and imp.effect_refs:
        for name in {r.effect for r in imp.effect_refs}:
            try:
                c = graph.effect_closure(name, geometry=True)
            except Exception:                                # noqa: BLE001
                continue
            fs: set = set()
            for L in c.layers:
                fs.update(L.forms)
            forms_by_effect[name] = sorted(fs)
    for r in imp.effect_refs:
        forms = forms_by_effect.get(r.effect, [])
        e = Satellite(
            group="effect",
            label=f"{r.effect} layer {r.layer} ({r.role})",
            asset_id=str(r.asset_id), present=True,
            source=r.table,
            form="; ".join(forms) if forms else (
                "form UNMEASURED" if not effect_forms else "form not classified"),
            note=f"this asset is the {r.role} of the layer",
            # THE KEY, STRUCTURED, for the same reason `bindings` carries one.
            # `label` is prose ("tj layer 1 (mesh)") and a view that wants to
            # OPEN the effect had to either be handed the name or regex it back
            # out of that sentence -- which makes the next rewording a silent
            # dead link. Populated 2026-09-08 because the Asset Viewer could
            # show a file in full and never name the one effect that plays it:
            # `c3/effect/tj/2.c3` is layer 1 of `tj`, and there was no route
            # from the file to the effect anywhere in the UI.
            rule={"effect": r.effect, "layer": r.layer, "role": r.role,
                  "table": r.table})
        sat.effects.append(e)


def _fill_bindings(sat: Satellites, imp) -> None:
    """The appearance rows that point at this asset, and the role parts they
    back -- 'who wears this'. Bindings are always shown in full; they are the
    reverse edge and hiding one hides a wearer."""
    bytab: dict = {}
    for r in imp.appearance_refs:
        bytab.setdefault((r.table, r.parts), []).append(r)
    for (tab, parts), refs in sorted(bytab.items()):
        sat.bindings.append(Satellite(
            group="binding",
            label=f"{tab}  ({', '.join(parts)})",
            source=tab, present=True,
            note=f"{len(refs)} appearance row(s) name this asset"))
    for r in imp.role_parts:
        motion = f"  motion: {r['motion']}" if r.get("motion") else ""
        sat.bindings.append(Satellite(
            group="binding", label=f"role part {r['part']}",
            source=r.get("table", ""), present=True,
            note=f"[{r.get('ropt', '')}] read from {r.get('source', '')}{motion}"))


# ---------------------------------------------------------------------------
# garment siblings -- texture alternatives / paired meshes
# ---------------------------------------------------------------------------

def _garment_siblings(graph, subject: str, kind: str):
    """Yield `(kind_dir, logical_path, present)` for every stem the garment
    index buckets under the subject's last-6, on the `kind` side ("mesh" or
    "texture"). Present is checked with `locate`, so an absent sibling is
    yielded as `present=False` rather than skipped -- that is what lets the
    alternatives list SHOW the ones this install does not ship."""
    stem = _stem(subject)
    if len(stem) < 6:
        return
    sub = _subdir(subject)
    ext = "c3" if kind == "mesh" else "dds"
    try:
        idx = graph.assets._art_by_garment()
    except Exception as e:                                   # noqa: BLE001
        graph.build_limits.append(
            f"the garment index would not build ({e.__class__.__name__}: {e}); "
            f"texture alternatives were not enumerated")
        return
    last6 = stem[-6:]
    seen: set = set()
    # The index keys on (kind_dir, last6). The subject's own subdir first, then
    # any other subdir sharing the garment -- a body skin can live under body/
    # while its armet shares nothing, so we stay within the subject's subdir.
    for (kdir, g6), stems in idx.items():
        if g6 != last6:
            continue
        for st in stems:
            path = f"c3/{kdir}/{st}.{ext}"
            if path in seen:
                continue
            seen.add(path)
            loc = graph.assets.locate(path)
            yield kdir, path, loc is not None


def _norm_name(name: str) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()


def _tables_by_file(graph) -> dict:
    """`{filename: PartIni}` -- `part_tables()` is keyed by ROLE PART, and one
    file backs several parts (weapon.ini backs l_weapon and r_weapon), so a
    lookup by the appearance ref's table name needs this collapse. Built once
    per resolve; `part_tables()` is itself cached on the AssetRoot."""
    out: dict = {}
    try:
        tables = graph.assets.part_tables()
    except Exception:                                        # noqa: BLE001
        return out
    for _part, ini in tables.items():
        out.setdefault(_norm_name(getattr(ini, "name", "")), ini)
    return out


def _part_for(app, part_index: int, imp):
    """The `PartRef` a mesh-side appearance ref points at: the one at
    `part_index`, or the one whose mesh id resolves to the subject if the index
    does not line up."""
    parts = getattr(app, "parts", [])
    for pr in parts:
        if getattr(pr, "index", None) == part_index:
            return pr
    # Fall back to the part whose mesh matches the subject stem.
    want = _stem(imp.target)
    for pr in parts:
        if str(getattr(pr, "mesh", "")).lstrip("0") == want.lstrip("0"):
            return pr
    return parts[0] if parts else None


def _stem(path: str) -> str:
    base = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0]


def _subdir(path: str) -> str:
    parts = str(path).replace("\\", "/").split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "c3" else (
        parts[-2] if len(parts) >= 2 else "")


# ---------------------------------------------------------------------------
# rendering -- the Asset Root text view
#
# Same rule as `depclose.render`: EVERY group prints, including the empty ones,
# an absent satellite prints AS absent, and the closure's blind spots print
# under NOT ENUMERATED on every run. A tidy omission is the failure mode this
# whole module exists to avoid.
# ---------------------------------------------------------------------------

def render(sat: Satellites, out=None, limit: int = 24) -> None:
    p = (lambda *a: print(*a, file=out)) if out is not None else print
    p("ASSET ROOT -- everything that goes with this asset")
    p(f"subject    {sat.subject}")
    p(f"install    {sat.root}")
    p(f"kind       {sat.kind}")
    if sat.present:
        p(f"status     {sat.status.upper()}  --  {sat.located}")
    else:
        p(f"status     {sat.status.upper()}  --  this install ships no file "
          f"at that path")
    if sat.foreign:
        p("PROVENANCE this install does NOT ship this file; it was served "
          "from another install.")
    if not sat.measured:
        p("")
        p("NOT A .c3 OR .dds -- no satellite class was enumerated; the empty "
          "groups below mean UNMEASURED.")
        _render_limits(sat, p)
        return

    for name, title in (("geometry", "GEOMETRY"),
                        ("texture", "TEXTURES (including alternatives)"),
                        ("animation", "ANIMATION"),
                        ("effect", "EFFECTS (all three forms are viewable)"),
                        ("binding", "BINDINGS -- who references this"),
                        ("material", "MATERIALS")):
        items = sat.group(name)
        present = sum(1 for s in items if s.present)
        absent = len(items) - present
        p("")
        head = f"{title}   {present} present"
        if absent:
            head += f", {absent} DECLARED-BUT-ABSENT"
        p(head)
        if not items:
            p("   (none found)")
        for s in items[:limit]:
            _render_sat(s, p)
        if len(items) > limit:
            p(f"   ... and {len(items) - limit} more")

    p("")
    if sat.unresolved:
        p(f"COULD NOT RESOLVE   {sat.unresolved_count} satellite(s) are named "
          f"by this install but resolve to no file here:")
        for s in sat.unresolved[:limit]:
            tag = s.path or s.asset_id or s.label
            p(f"   - {s.group}: {s.label}  {tag}")
        if sat.unresolved_count > limit:
            p(f"   ... and {sat.unresolved_count - limit} more")
    else:
        p("COULD NOT RESOLVE   0 -- every satellite named above resolved to a "
          "file in this install")

    _render_limits(sat, p)


def _render_sat(s: Satellite, p) -> None:
    state = "present" if s.present else "ABSENT from this install"
    if s.foreign:
        state = "FOREIGN (served from another install)"
    tag = s.path or s.asset_id or ""
    extra = []
    if s.form:
        extra.append(s.form)
    if s.alternative:
        extra.append("alternative")
    if s.note:
        extra.append(s.note)
    suffix = ("   -- " + "; ".join(extra)) if extra else ""
    p(f"   {s.label:32s} {tag:30s} [{state}]{suffix}")


def _render_limits(sat: Satellites, p) -> None:
    p("")
    p("NOT ENUMERATED -- reference classes outside this closure")
    if not sat.limits:
        p("   (none recorded)")
    for line in sat.limits:
        p(f"   * {line}")


# ---------------------------------------------------------------------------
# JSON -- for the webui panel and any programmatic caller
# ---------------------------------------------------------------------------

def to_json(sat: Satellites, *, view: str = "asset-root") -> dict:
    """`view="asset-root"` -> the unfiltered object; `view="model"` -> the
    present-only projection `model_view` returns. Both carry `unresolvedCount`
    and `limits`, so neither can be read as complete when it is not."""
    if view == "model":
        return sat.model_view()
    return {
        "subject": sat.subject, "root": sat.root, "kind": sat.kind,
        "status": sat.status, "present": sat.present, "foreign": sat.foreign,
        "located": sat.located, "measured": sat.measured,
        "geometry": [s.as_dict() for s in sat.geometry],
        "textures": [s.as_dict() for s in sat.textures],
        "animations": [s.as_dict() for s in sat.animations],
        "effects": [s.as_dict() for s in sat.effects],
        "bindings": [s.as_dict() for s in sat.bindings],
        "materials": [s.as_dict() for s in sat.materials],
        "unresolved": [s.as_dict() for s in sat.unresolved],
        "unresolvedCount": sat.unresolved_count,
        "limits": list(sat.limits),
        # The effect-subject carries. `tables` in particular is NOT optional
        # decoration: it is the only thing on the wire that says which file
        # answered, and a page that cannot say that is reading the decoy for
        # all it knows.
        "tables": list(sat.tables),
        "declaredLayers": sat.declared_layers,
        "timing": dict(sat.timing),
        "near": list(sat.near),
        "duplicateName": sat.duplicate_name,
        "writable": list(WRITABLE_FORMS),
        "readOnly": dict(READ_ONLY_FORMS),
        "noWriterNeeded": dict(NO_WRITER_NEEDED),
    }
