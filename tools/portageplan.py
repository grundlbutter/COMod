#!/usr/bin/env python3
r"""portageplan.py -- the two-tier Import/Export plan the pop-out renders.

    from portageplan import build_plan, export_items, to_json
    with depclose.DepGraph(root) as g:
        plan = build_plan(g, [{"asset": "c3/body/7130030.c3", "label": "body 130030"},
                              {"asset": "c3/weapon/410009.c3", "label": "weapon 410009"}])
        to_json(plan)                       # what the panel draws
        export_items(plan)                  # what core/portage.build_manifest takes

WHAT THIS MODULE IS, AND THE TWO THINGS IT IS NOT
-------------------------------------------------
The owner's pop-out (backlog item 6, "THE IMPORT/EXPORT TOOLS POP-OUT") is a
GROUPED LIST: tier 1 is each visible asset, tier 2 is that asset's satellites
grouped by type, each row showing its real file path and the tool that opens
it. That is a PROJECTION of two things that already exist, and this module is
only the projection:

* the satellite set comes from `tools/assetroot.resolve` -- the ONE companion
  resolver. There is no second closure here, and adding one is the mistake
  this module exists to not make;
* the transform, the type, the tool, the manifest and the zip layout come from
  `core/portage`. This module never decides how a file leaves or comes back;
  it asks `portage.classify` / `transform_for` / `default_tool` and prints the
  answer.

So a row's `exportPath` is not "a path this module made up" -- it is the path
`portage.build_manifest` will write, computed by the same rule, and
`tests/test_portageplan.py::ZipLayoutIsWhatThePanelShowed` pins the two
together by running a real export and comparing. That equality IS requirement
4 of the feature ("the structure shown in the pop-out is the structure on
disk"), and a drift in either half turns it red.

THE THREE HONESTY RULES, EACH WITH A REASON IT IS HERE
------------------------------------------------------
**1. A SHARED FILE IS NAMED ON BOTH SIDES.** In a Builder composition two
parts can reference the SAME texture. Tier-1 grouping shows it twice, and a
user who edits it under `body` has also changed it under `weapon` -- the
grouping actively CREATES that misunderstanding, so the panel has to undo it
at the point of the click, not in a footnote. Every row carries `shared` and
`sharedWith` (the OTHER asset labels that carry this same file), and
`Plan.shared` is the batch-level index. `portage.build_manifest` marks the
manifest records; this marks the rows the user sees before they press Export.

The zip really does contain a copy under each asset, and `portage.import_batch`
walks the manifest IN ORDER writing each record to `stage/<dest>` -- so two
copies of one dest means the LAST one in the manifest wins. That is not a bug
to hide; it is the reason `sharedWith` has to be visible.

**2. A DECLARED-BUT-ABSENT SATELLITE IS A ROW, NEVER A GAP.** 7,462 of 30,539
appearance references on the live install resolve to no file. Those rows are
shown, marked absent, and carried into the manifest with `absent:true` and no
export -- because a manifest that simply LACKS them cannot tell "this was
never there" from "the user deleted it" (backlog item 6, manifest point 4).

**3. THE PLAN INHERITS THE RESOLVER'S BLIND SPOTS.** `Satellites.limits` and
`unresolved` travel into `Plan.limits` / `Plan.unresolvedCount` unfiltered, and
the panel prints "and N I could not resolve". A list of 6 hiding 3 is a trap
the user cannot detect; a list of 6 that says "and 3 I could not resolve" is
usable. This module must never make the list tidier than the closure was.

WHAT IS A FILE AND WHAT IS A RELATIONSHIP
-----------------------------------------
Not every satellite is a file. `motion binding: PHY`, `role part body`, and an
effect layer that names this asset are RELATIONSHIPS -- true, worth showing,
and with nothing to put in a zip. They render as rows with no Export button and
`manifested:false`, and `Plan.note_rows` counts them so the batch can say how
many were shown-but-not-packed. Folding them into the manifest with an invented
destination would be a different lie from the one rule 2 forbids, in the same
family.

An effect row under a FILE subject is `tool: "not a file"`, and the reason is
narrower than it used to be. It said "view only", on the grounds that PTCL/PTC3
and SHAP/SMOT had no writer -- a reading made against a base that predated the
writers, and now false: all seven forms round-trip BYTE-EXACT (444,303 /
444,303). What remains true is that an effect LAYER named by an appearance row
is a REFERENCE, not a file, so there is nothing to put in a zip. A narrow limit
recorded against a broad thing is the costlier error of the two, so the row now
states the narrow one and points at where the effect IS exportable.

AN EFFECT AS A SUBJECT -- `build_effect_plan`
----------------------------------------------
The Effects Viewer's subject is an EFFECT, and `assetroot.resolve_effect`
already returns one in the `Satellites` shape. The projection is the same, with
ONE addition that is the whole reason this branch exists: an effect's
DEFINITION -- the `3DEffect` row naming its layers, timing and blend modes --
is not a file, so it cannot ride in the file plan. It becomes a
`group="definition"` row and a `portage.DefinitionItem`, and it is real: the
compiled table round-trips byte-exact on every base that ships one, so the row
can be carried and merged rather than merely described. A bundle that shipped
the layer meshes and textures and left the definition behind would land the ART
on the far install with the effect UNDEFINED -- section 6's trap exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import assetroot
import portage

#: Tier-2 group order and display names, in the order the owner drew them
#: (backlog item 6): Geometry / Animation / Texture / Effect / Binding rows.
#: `material` is assetroot's sixth group and is drawn after effect; it is not
#: in the owner's sketch because the sketch is an example, not an inventory.
GROUP_ORDER = (
    ("definition", "Definition (a table ROW, not a file)"),
    ("geometry", "Geometry"),
    ("animation", "Animation"),
    ("texture", "Texture"),
    ("effect", "Effect"),
    ("material", "Material"),
    ("binding", "Binding rows"),
)

#: Display name for each `portage` tool id. The ID is portage's answer; this
#: table only spells it the way the owner's sketch does. A tool this table
#: does not know falls through to the id itself rather than to a blank.
TOOL_LABEL = {
    "blender": "Blender",
    "gimp": "GIMP",
    "paint.net": "Paint.NET",
    "krita": "Krita",
    "calc": "LibreOffice Calc / Excel",
    "excel": "Excel",
    "libreoffice": "LibreOffice Calc",
    "audacity": "Audacity",
    "text": "text editor",
    "vscode": "VS Code",
}

#: What an effect row says in the tool column.
#:
#: **CORRECTED 2026-09-07, and the correction is the point.** This used to
#: read "view only", justified by "no writer exists for PTCL/PTC3 or SHAP/SMOT
#: on any client family". That reading was made against a base that PREDATED
#: the writers and it is now false: all seven chunk forms round-trip
#: BYTE-EXACT (444,303 / 444,303 chunks) -- SHAP, SMOT, PTCL, PTCX, PTC3,
#: RIBB, RMOT (`assetroot.WRITABLE_FORMS`). What is still true of this
#: particular row is narrower and has nothing to do with writers: an effect
#: LAYER named by an asset's appearance row is a REFERENCE, not a file of its
#: own, so there is nothing here to put in a zip. Saying "view only" said the
#: wrong thing for the right row -- a limit recorded against the wrong subject.
EFFECT_REF = "not a file"

#: Reason attached to every effect-reference row, so the tool column is never
#: a bare label the user has to interpret. The row's own subject -- the
#: effect -- IS exportable: the Effects Viewer exports it as a subject in its
#: own right, definition row and all, and this says so rather than leaving the
#: user with "no export" and no next step.
EFFECT_REF_WHY = ("an effect layer named by this asset is a REFERENCE, not a "
                  "file of its own, so there is nothing here to put in a zip. "
                  "The effect itself IS exportable -- open it in the Effects "
                  "Viewer, where it is the subject and its 3DEffect "
                  "definition row travels with it. Effect chunk forms are "
                  "WRITABLE: all seven round-trip byte-exact (SHAP, SMOT, "
                  "PTCL, PTCX, PTC3, RIBB, RMOT); CCFL, CAME and OMNI are the "
                  "read-only ones")

#: Reason a relationship row carries no Export button.
NOT_A_FILE = "this row is a relationship, not a file -- nothing to put in a zip"

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
#: Characters Windows refuses in a path segment, plus the separators.
_UNSAFE_SEG = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def slug(text: str) -> str:
    """A tier-1 folder name from a label. `body 130030` -> `body_130030`.

    The zip's first path segment, so it has to be filesystem-safe on Windows
    and stable enough that two exports of one composition agree.
    """
    s = _SLUG_RE.sub("_", str(text or "")).strip("_")
    return s.lower() or "asset"


def export_ext(transform: str) -> str:
    """The extension `portage._export_transform` gives a working file.

    Mirrored rather than imported because the real function needs the file's
    BYTES to do the conversion, and the plan is built without reading any
    asset. `ZipLayoutIsWhatThePanelShowed` pins this against a real export, so
    the mirror cannot drift silently.
    """
    if transform == portage.T_DDS_PNG:
        return ".png"
    if transform == portage.T_TABLE_CSV:
        return ".csv"
    return ""


def export_path(logical: str, group: str, *, readable: str = "",
                layout: str = portage.LAYOUT_GROUPED) -> str:
    """Where `portage.build_manifest` will write this file inside the bundle.

    `<asset>/<type>/<file>` -- the layout the backlog specifies and the layout
    the pop-out draws, which are required to be the same thing.
    """
    atype = portage.classify(logical)
    ext = export_ext(portage.transform_for(atype))
    return portage.layout_path(
        portage.ExportItem(logical=logical, group=group, readable=readable),
        group, atype, ext, layout)


def readable_sub(sat, logical: str) -> str:
    """A human sub-path for one satellite, or "" when nothing is known.

    `<group>/<name> (<number>)` for a motion whose action has a researched
    name -- `combat/attack (410)` -- and `<label> (<number>)` for anything
    else that carries a usable label. **Returns "" rather than a guess**: 126
    of the 192 action codes a loadout resolves have no researched name, and a
    readable layout that invented one would be the confident-wrong-answer
    failure this project is built around. An empty result makes
    `portage.layout_path` fall back to the file's own number, which is the
    one thing actually known about it.
    """
    stem = Path(str(logical)).stem
    rule = getattr(sat, "rule", None) or {}
    names = rule.get("names") or []
    if rule.get("actions") and names:
        grp = slug(rule.get("group") or "other")
        return "%s/%s (%s)" % (grp, _safe_seg(names[0]), stem)
    if rule.get("actions"):
        # A motion with no researched name still files under `other/`, which
        # is where the unnamed ones belong and where a reader will look.
        return "other/(%s)" % stem
    label = str(getattr(sat, "label", "") or "").strip()
    if not label or label.lower() == stem.lower():
        return ""
    return "%s (%s)" % (_safe_seg(label), stem)


def _safe_seg(text: str) -> str:
    """One path segment from prose: Windows-illegal characters folded out.

    Not `slug()` -- this keeps spaces and case, because the whole point of the
    readable layout is that a human reads it. `slug` is for the tier-1 folder,
    where stability across two exports matters more than looks.
    """
    out = _UNSAFE_SEG.sub("-", str(text or "")).strip().strip(".")
    return out or "unnamed"


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

@dataclass
class PlanRow:
    """One tier-2 row: a satellite, its real path, and the tool for it."""
    group: str                      # assetroot group id
    label: str                      # human label from the resolver
    path: str = ""                  # logical path; "" when there is no file
    asset_id: str = ""
    present: bool = True
    absent: bool = False            # declared but not in this install
    manifested: bool = False        # does this row become a manifest record
    file: bool = False              # is there a file to export
    type: str = ""                  # portage type: c3/dds/table/ani/audio/other
    transform: str = ""
    tool: str = ""                  # display name, or "view only" / "-"
    tool_id: str = ""               # portage's own id, for --for validation
    export_path: str = ""           # where it lands in the bundle
    readable: str = ""              # human sub-path, "" when unnamed
    source_path: str = ""           # where it lands under LAYOUT_SOURCE
    readable_path: str = ""         # where it lands under LAYOUT_READABLE
    dest: str = ""                  # the manifest's `dest` for this row
    shared: bool = False
    shared_with: list = field(default_factory=list)   # OTHER asset labels
    source: str = ""
    form: str = ""
    alternative: bool = False
    note: str = ""
    why: str = ""                   # why there is no Export button
    #: Set when an EARLIER row of the SAME tier-1 asset already names this
    #: file (a mesh is both the Geometry row and the row its MATR chunk is
    #: reported on). One file, one copy in the bundle -- so the second row
    #: says which row it repeats rather than offering a second Export that
    #: would write the same bytes to the same path.
    duplicate_of: str = ""
    #: THIS ROW IS A TABLE ROW, NOT A FILE. Set only by `build_effect_plan`
    #: for the subject's own `3DEffect` record. It has no `file`, so no
    #: checkbox and no per-file Export button reach it -- it rides with the
    #: batch through `definition_items`, because an effect exported without
    #: its definition is the omission this whole panel exists to refuse.
    definition: bool = False
    table: str = ""             # the table's stem, e.g. "3DEffect"
    table_file: str = ""        # LOGICAL path of the file that ANSWERED
    table_form: str = ""        # "dbc" | "ini"
    row_key: str = ""           # the row's key -- the effect name
    row: object = None          # the decoded row, carried into the manifest

    def as_dict(self) -> dict:
        return {
            "group": self.group, "label": self.label, "path": self.path,
            "assetId": self.asset_id, "present": self.present,
            "absent": self.absent, "manifested": self.manifested,
            "file": self.file, "type": self.type, "transform": self.transform,
            "tool": self.tool, "toolId": self.tool_id,
            "exportPath": self.export_path,
            "sourcePath": self.source_path,
            "readablePath": self.readable_path, "dest": self.dest,
            "shared": self.shared, "sharedWith": list(self.shared_with),
            "source": self.source, "form": self.form,
            "alternative": self.alternative, "note": self.note,
            "why": self.why, "duplicateOf": self.duplicate_of,
            # `row` itself is deliberately NOT in the payload: it is the
            # manifest's business, it can be a 60-layer record, and the panel
            # has nothing to draw with it. Everything the panel needs to say
            # WHAT is travelling and OUT OF WHICH FILE is here.
            "definition": self.definition, "table": self.table,
            "tableFile": self.table_file, "tableForm": self.table_form,
            "rowKey": self.row_key,
        }


@dataclass
class PlanAsset:
    """Tier 1: one visible asset and its satellites, grouped by type."""
    key: str                        # the bundle's first path segment
    label: str                      # what the user sees, e.g. "body 130030"
    subject: str                    # logical path the resolver was given
    requested: str = ""             # what the caller asked for (id or path)
    kind: str = ""
    status: str = ""
    present: bool = True
    measured: bool = True
    rows: list = field(default_factory=list)
    unresolved_count: int = 0
    limits: list = field(default_factory=list)
    error: str = ""
    note: str = ""

    def groups(self) -> list:
        out = []
        for gid, title in GROUP_ORDER:
            rows = [r for r in self.rows if r.group == gid]
            if not rows:
                continue
            out.append({"id": gid, "title": title,
                        "rows": [r.as_dict() for r in rows]})
        return out

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "subject": self.subject,
            "requested": self.requested, "kind": self.kind,
            "status": self.status, "present": self.present,
            "measured": self.measured, "groups": self.groups(),
            "rowCount": len(self.rows),
            "fileCount": sum(1 for r in self.rows if r.file),
            "absentCount": sum(1 for r in self.rows if r.absent),
            "noteCount": sum(1 for r in self.rows if not r.manifested),
            "definitionCount": sum(1 for r in self.rows if r.definition),
            "unresolvedCount": self.unresolved_count,
            "limits": list(self.limits), "error": self.error,
            "note": self.note,
        }


@dataclass
class Plan:
    """The whole pop-out: every visible asset, and what is shared between them."""
    assets: list = field(default_factory=list)
    #: logical path -> the asset labels that carry it. Only paths with more
    #: than one label are shared; the full index is kept so the panel can
    #: explain a shared file from either side.
    shared: dict = field(default_factory=dict)
    limits: list = field(default_factory=list)

    @property
    def unresolved_count(self) -> int:
        return sum(a.unresolved_count for a in self.assets)

    @property
    def file_count(self) -> int:
        return sum(1 for a in self.assets for r in a.rows if r.file)

    @property
    def absent_count(self) -> int:
        return sum(1 for a in self.assets for r in a.rows if r.absent)

    @property
    def note_rows(self) -> int:
        return sum(1 for a in self.assets for r in a.rows if not r.manifested)

    @property
    def definition_count(self) -> int:
        return sum(1 for a in self.assets for r in a.rows if r.definition)

    @property
    def shared_paths(self) -> list:
        return sorted(p for p, labels in self.shared.items() if len(labels) > 1)


def to_json(plan: Plan) -> dict:
    """The panel's payload. Every honesty number is top level, so a caller
    cannot render the list without also having the count of what is missing."""
    return {
        "assets": [a.as_dict() for a in plan.assets],
        "shared": {p: list(v) for p, v in plan.shared.items() if len(v) > 1},
        "sharedPaths": plan.shared_paths,
        "limits": list(plan.limits),
        "unresolvedCount": plan.unresolved_count,
        "fileCount": plan.file_count,
        "absentCount": plan.absent_count,
        "noteRows": plan.note_rows,
        "layout": "<asset>/<type>/<file>  +  " + portage.MANIFEST_NAME
                  + "  +  " + portage.README_NAME,
        "sharedWarning": SHARED_WARNING,
        # THE DEFINITION ANSWER IS A NUMBER AND A SENTENCE, NEVER AN ABSENCE.
        # A panel that shows only files leaves the reader to infer whether the
        # subject's own definition is in the bundle, and the inference a tidy
        # file list invites is "yes". Both are top level so no caller can
        # render the list without also having the answer.
        "definitionCount": plan.definition_count,
        "definitionNote": (DEFINITION_CARRIED if plan.definition_count
                           else DEFINITION_NONE),
    }


#: Printed when the batch DOES carry a definition row.
DEFINITION_CARRIED = (
    "This bundle carries the subject's DEFINITION as a table row, not as a "
    "file. On import COMod reads the target install's own copy of that table, "
    "merges this row into it and stages the result -- so every other row on "
    "the target is left as it was, and importing one effect cannot revert the "
    "rest.")

#: Printed when it does NOT -- which is every FILE subject, and an effect whose
#: definition could not be read. THE PANEL SAYS SO RATHER THAN SAYING NOTHING:
#: "no definition row is listed" and "this subject has no definition to carry"
#: look identical on screen unless one of them is written down.
DEFINITION_NONE = (
    "This bundle carries NO definition row. Everything below is a file. If "
    "the subject is defined by a table row somewhere -- an effect's 3DEffect "
    "record, an appearance row -- that row is NOT in this bundle and the far "
    "end will need it from somewhere else.")


#: The sentence the panel puts beside a shared file. It is here rather than in
#: the JS because it states a fact about `portage.import_batch`'s behaviour,
#: and the two should not be able to disagree.
SHARED_WARNING = (
    "This file is used by more than one of the assets below. The bundle "
    "carries a copy under each, they all reimport to the SAME destination, "
    "and the last one written wins -- so editing it under one asset changes "
    "it under every asset that uses it.")


# ---------------------------------------------------------------------------
# building it
# ---------------------------------------------------------------------------

def _is_loadout_body(want: str, path: str, loadout: dict) -> bool:
    """Is THIS subject the loadout's body?

    Compares the requested value first because that is what the Builder sent
    and what the loadout names. The path comparison is a fallback for a caller
    that passed a mesh path as the subject, and it matches on the STEM rather
    than the whole path so `c3/mesh/003188495.c3` can match a body given as
    `003188495` -- it will NOT match appearance `003188490`, which is correct:
    nothing in the path says which appearance chose it.
    """
    body = str(loadout.get("body") or "").strip()
    if not body:
        return False
    # THE CALLER'S OWN ANSWER FIRST. `bodyAsset` is the subject string the
    # caller sent for its body slot, and it is the only reliable link: a
    # Builder sends MESH PATHS as subjects while the loadout names APPEARANCE
    # ids, and appearance 002132300 resolves to mesh 002135000. Comparing
    # those two numbers never matches, which dropped the loadout on every
    # real composition -- the docstring below warned about exactly this and
    # the code still required it.
    asset = str(loadout.get("bodyAsset") or "").strip()
    if asset and str(want).strip() == asset:
        return True
    if str(want).strip() == body:
        return True
    stem = str(path).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return bool(stem) and stem.lstrip("0") == body.lstrip("0")


def build_plan(graph, subjects: list, *, resolve_kw: Optional[dict] = None,
               loadout: Optional[dict] = None) -> Plan:
    r"""Resolve every subject and project it into the two-tier plan.

    `subjects` is a list of `{"asset": <path or id>, "label": <optional>}`.
    An id is resolved through `depclose.resolve_target`, exactly as
    `/api/assetroot` does -- the panel's subjects come from the Builder's
    loadout, which knows appearance ids and sometimes a mesh path.

    A subject that will not resolve is kept as a PlanAsset with `error` set
    rather than dropped: a Builder composition of five parts that exports four
    and says nothing about the fifth is the failure this whole panel is about.

    `loadout` is the equipped composition -- ``{"body", "right", "left"}`` of
    appearance ids -- and it reaches `assetroot.resolve` for the BODY subject
    ONLY. Motions belong to the body's shape crossed with the weapon's type;
    the weapon mesh has none of its own, it is attached to the body's
    skeleton. Handing the loadout to every subject would list the same ~60
    motion files under the body AND under each weapon, which the shared-file
    machinery would then correctly mark as shared -- a true statement that
    doubles the export and reads as twice the animation. **The body is
    matched by the REQUESTED value, not the resolved path**: appearance
    `003188490` resolves to mesh `c3/mesh/003188495.c3`, so a path comparison
    would silently never match.
    """
    import depclose                                          # noqa: PLC0415

    plan = Plan()
    seen_keys: dict = {}
    for spec in subjects:
        want = str((spec or {}).get("asset") or "").strip()
        if not want:
            continue
        label = str((spec or {}).get("label") or "").strip() or want
        key = slug(label)
        # Two slots holding the same appearance would collide in the zip.
        # Suffix rather than merge: they are two tier-1 entries on screen.
        if key in seen_keys:
            seen_keys[key] += 1
            key = f"{key}_{seen_keys[key]}"
        else:
            seen_keys[key] = 1

        try:
            path, note = depclose.resolve_target(graph, want)
        except Exception as e:                               # noqa: BLE001
            plan.assets.append(PlanAsset(
                key=key, label=label, subject="", requested=want,
                present=False, measured=False,
                error=f"could not resolve {want!r}: {e.__class__.__name__}: {e}"))
            continue
        if not path:
            plan.assets.append(PlanAsset(
                key=key, label=label, subject="", requested=want,
                present=False, measured=False,
                error=note or f"no asset {want!r} in this install"))
            continue

        kw = dict(resolve_kw or {})
        if loadout:
            # Told to every subject, USED by the body. The flag is what stops
            # a weapon reporting "no loadout given" on a plan that has one.
            kw["loadout_given"] = True
            if _is_loadout_body(want, path, loadout):
                kw["loadout"] = loadout
        sat = assetroot.resolve(graph, path, **kw)
        asset = PlanAsset(
            key=key, label=label, subject=sat.subject, requested=want,
            kind=sat.kind, status=sat.status, present=sat.present,
            measured=sat.measured, unresolved_count=sat.unresolved_count,
            limits=list(sat.limits), note=note or "")
        unresolved = {id(s) for s in sat.unresolved}
        for s in sat.all_satellites():
            row = _row(graph, asset, s, id(s) in unresolved)
            if row is not None:
                asset.rows.append(row)
        _mark_duplicates(asset)
        plan.assets.append(asset)

    # `_index_shared`, plus the union of every subject's blind spots: a limit
    # that applies to one asset applies to the batch, because the batch is not
    # more complete than its least complete member.
    return _finish(plan)


def _finish(plan: Plan) -> Plan:
    """`_index_shared` plus the union of every subject's blind spots.

    Factored out of `build_plan` when `build_effect_plan` arrived, because a
    second plan builder that forgot either half would produce a plan that is
    tidier than its closure was -- honesty rule 3, dropped by omission rather
    than by decision, which is how it usually goes.
    """
    _index_shared(plan)
    seen: dict = {}
    for a in plan.assets:
        for L in a.limits:
            seen.setdefault(L, None)
    plan.limits = list(seen)
    return plan


# ---------------------------------------------------------------------------
# an EFFECT as the subject
# ---------------------------------------------------------------------------

def build_effect_plan(graph, subjects: list) -> Plan:
    r"""The same two-tier plan, with an EFFECT as each tier-1 subject.

    `subjects` is a list of `{"effect": <3DEffect name>, "label": <optional>}`.
    Everything below tier 1 is `assetroot.resolve_effect` projected exactly as
    `build_plan` projects `assetroot.resolve` -- there is no second closure
    here either.

    THE ONE THING THIS BUILDER DOES THAT THE FILE BUILDER CANNOT.
    An effect is not a file, so its definition -- the `3DEffect` row that
    names its layers, its timing and its blend modes -- has no path to put in
    a zip. `_definition_row` adds it as a `group="definition"` row and
    `definition_items` turns it into a `portage.DefinitionItem`, so the row
    travels with the art instead of being left behind.

    IT IS THE FILE THAT ANSWERED, NOT THE ONE BESIDE IT. The row is read out
    of `sat.tables`' `3DEffect` entry -- `3DEffect.dbc` on 5517/6090/6609/7205,
    where the `.ini` sitting next to it is a DECOY that answers DIFFERENTLY
    for the same id. Reaching for the plaintext because it is easier to parse
    would export a row the client does not read, and the bundle would be
    confidently wrong rather than empty.

    A definition that CANNOT be read is a limit, never a silence: the asset
    keeps a `note`, the plan's `definitionCount` stays at 0 and `to_json`
    prints `DEFINITION_NONE`, so a bundle without one says so on screen.
    """
    import assetroot                                          # noqa: PLC0415

    plan = Plan()
    seen_keys: dict = {}
    for spec in subjects:
        name = str((spec or {}).get("effect") or
                   (spec or {}).get("asset") or "").strip()
        if not name:
            continue
        label = str((spec or {}).get("label") or "").strip() or name
        key = slug(label)
        if key in seen_keys:
            seen_keys[key] += 1
            key = f"{key}_{seen_keys[key]}"
        else:
            seen_keys[key] = 1

        try:
            sat = assetroot.resolve_effect(graph, name)
        except Exception as e:                               # noqa: BLE001
            plan.assets.append(PlanAsset(
                key=key, label=label, subject=name, requested=name,
                kind="effect", present=False, measured=False,
                error=f"could not resolve effect {name!r}: "
                      f"{e.__class__.__name__}: {e}"))
            continue

        asset = PlanAsset(
            key=key, label=label, subject=sat.subject, requested=name,
            kind="effect", status=sat.status, present=sat.present,
            measured=sat.measured, unresolved_count=sat.unresolved_count,
            limits=list(sat.limits))
        drow, why = _definition_row(graph, sat, name, key)
        if drow is not None:
            asset.rows.append(drow)
        else:
            # NOT SILENCE. The reason the definition could not be carried is
            # attached to the asset AND filed as a limit, because the whole
            # point of carrying it is that its absence is invisible: the art
            # exports fine and the effect arrives undefined.
            asset.note = why
            asset.limits.append(why)
        unresolved = {id(s) for s in sat.unresolved}
        for s in sat.all_satellites():
            row = _row(graph, asset, s, id(s) in unresolved)
            if row is not None:
                asset.rows.append(row)
        _mark_duplicates(asset)
        plan.assets.append(asset)

    return _finish(plan)


#: `3DEffect` is the definition table. Named once rather than spelled at each
#: use, and matched case-insensitively against `sat.tables` because the corpus
#: spells the sibling tables three ways (`3DEffectObj`, `3DEffectobj`,
#: `3dtexture`) and a case-sensitive lookup is a bug waiting for a base that
#: spells this one differently too.
DEFINITION_TABLE = "3DEffect"


def _definition_row(graph, sat, name: str, key: str):
    """`(PlanRow, "")` for the effect's `3DEffect` record, or `(None, why)`.

    Reads the row out of the FILE THAT ANSWERED, through the same readers that
    can write it back -- `dbc.read_effe` for a compiled table (whose inverse
    `dbc.serialize_effe` round-trips byte-exact on every base that ships one),
    the raw `[name]` section for a plaintext one. A row that could be READ but
    not WRITTEN would be a bundle that promises a round trip it cannot make,
    so the two readers here are exactly the two forms `portage`'s import side
    can merge -- and any third form returns a refusal, not a best effort.
    """
    if not sat.measured:
        return None, ("the effect tables would not load on this install, so "
                      "the 3DEffect definition row is UNMEASURED, not absent "
                      "-- nothing was read and nothing can be carried")
    if not sat.present:
        return None, (f"{name!r} is not defined in {DEFINITION_TABLE} on this "
                      f"install, so there is no definition row to carry")
    if sat.duplicate_name:
        return None, (f"{DEFINITION_TABLE} holds MORE THAN ONE row under "
                      f"{name!r} on this install. Which one the client honours "
                      f"is not measured, so carrying one of them would be a "
                      f"guess the far end would receive as fact -- refused")

    trow = None
    for t in (sat.tables or []):
        if str(t.get("table", "")).lower() == DEFINITION_TABLE.lower():
            trow = t
            break
    if trow is None or not trow.get("file"):
        return None, (f"the file that answered for {DEFINITION_TABLE} on this "
                      f"install is UNKNOWN, so the definition row cannot be "
                      f"read out of it. It is NOT safe to fall back to the "
                      f".ini: where a compiled twin exists the client reads "
                      f"the twin and the two give different answers")

    logical = f"ini/{trow['file']}"
    form = portage.definition_form(logical)
    try:
        raw = graph.assets.read(logical)
    except Exception as e:                                   # noqa: BLE001
        return None, (f"{logical} could not be read ({e.__class__.__name__}: "
                      f"{e}), so the definition row cannot be carried")

    try:
        row, layers = _read_definition(raw, form, name)
    except Exception as e:                                   # noqa: BLE001
        return None, (f"{logical} would not parse as a {form or 'unknown'} "
                      f"definition table ({e.__class__.__name__}: {e}); the "
                      f"row is NOT carried")
    if row is None:
        return None, (f"{name!r} resolves as an effect but has no row in "
                      f"{trow['file']} -- the table that ACTUALLY answers on "
                      f"this base. Carrying a row from the sibling file "
                      f"instead would export something the client does not "
                      f"read")

    r = PlanRow(
        group="definition",
        label=f"{DEFINITION_TABLE} row “{name}”",
        present=True, manifested=True, file=False, definition=True,
        table=DEFINITION_TABLE, table_file=logical, table_form=form,
        row_key=name, row=row,
        # THE SAME RULE AS EVERY OTHER ROW: what the panel shows IS the path
        # on disk, computed by the function that writes it rather than spelled
        # again here, so the two cannot drift.
        dest=logical,
        export_path=f"{key}/definition/{portage._safe_name(name)}.json",
        type="table-row", transform=portage.T_TABLE_ROW,
        tool_id="", tool=TOOL_LABEL.get("text", "text editor"),
        source=trow["file"],
        note=(f"{layers} layer(s), read from the file that ANSWERED"
              + (f"; {trow['not_read']} is present and is NOT read"
                 if trow.get("not_read") else "")),
        why=("this is a table ROW, not a file. It rides with the batch and "
             "cannot be ticked off: an effect exported without its definition "
             "arrives with the art present and the effect UNDEFINED"))
    return r, ""


def _read_definition(raw: bytes, form: str, name: str):
    """`(row, layer_count)` for `name` in a definition table, or `(None, 0)`.

    Raises on a table that will not parse -- the caller turns that into a
    refusal with the exception named. It does NOT raise on a missing row: a
    name absent from the table that answers is a finding about the base, and
    the caller says so in those words.
    """
    if form == "dbc":
        import dbc                                            # noqa: PLC0415
        recs = dbc.read_effe(raw)
        hits = [r for r in recs if r.get("name") == name]
        if not hits:
            return None, 0
        if len(hits) > 1:
            raise ValueError(
                f"{name!r} appears {len(hits)} times in this table; which row "
                f"the client honours is not measured")
        row = dict(hits[0])
        row["offset"] = list(row.get("offset") or (0, 0, 0))
        return row, len(row.get("layers") or [])
    if form == "ini":
        text = raw.decode("latin-1")
        _pre, secs = portage.ini_sections(text)
        hits = [b for k, b in secs if k == name]
        if not hits:
            return None, 0
        if len(hits) > 1:
            raise ValueError(
                f"{name!r} appears {len(hits)} times in this table; which row "
                f"the client honours is not measured")
        block = hits[0]
        n = len(re.findall(r"(?mi)^EffectId\d+\s*=", block))
        return block, n
    raise ValueError(f"no reader for a {form or 'suffix-less'} definition table")


def definition_items(plan: Plan, select: Optional[list] = None) -> list:
    r"""`portage.DefinitionItem`s for the definition rows of the plan.

    `select` IS READ FOR ITS TIER-1 KEYS AND NOTHING ELSE, and that is the
    same ruling the absent records get. A definition carries no bytes for a
    per-row checkbox to mean, and what it carries is the difference between an
    effect that arrives DEFINED and one that arrives as loose art -- so a
    selection cannot drop the definition of a subject it IS exporting. What it
    can do is not drag in a subject it is not exporting: clicking Export on
    effect A in a two-effect batch must not ship B's row, which would put a
    definition on the far install that the user never asked to send.

    A `select` naming no tier-1 key at all (bare `dest` strings, the `curl`
    shape) means "wherever it appears", so every definition travels -- the
    same reading `export_items` gives it.
    """
    keys = _selected_keys(select)
    out: list = []
    for a in plan.assets:
        if keys is not None and a.key not in keys:
            continue
        for r in a.rows:
            if not r.definition:
                continue
            out.append(portage.DefinitionItem(
                table=r.table, key=r.row_key, dest=r.table_file,
                form=r.table_form, row=r.row, group=a.key,
                note=r.note))
    return out


def _selected_keys(select) -> Optional[set]:
    """The tier-1 keys a selection names, or None for "every subject"."""
    if select is None:
        return None
    pairs, _loose = _selection(select)
    keys = {k for k, _dest in pairs if k}
    return keys or None


def _row(graph, asset: PlanAsset, s, is_unresolved: bool) -> Optional[PlanRow]:
    """One satellite -> one row, or None when there is nothing to say.

    Nothing returns None today; the signature keeps the option so a future
    filter has one place to live rather than being scattered through the
    caller. Everything the resolver reported is SHOWN.
    """
    path = s.path or ""
    if not path and s.group == "binding":
        path = _binding_path(graph, s)
    present = bool(s.present) and bool(path)
    row = PlanRow(
        group=s.group, label=s.label, path=path, asset_id=s.asset_id or "",
        present=bool(s.present), source=s.source or "", form=s.form or "",
        alternative=bool(s.alternative), note=s.note or "")

    if s.group == "effect":
        row.tool = EFFECT_REF
        row.why = EFFECT_REF_WHY
        return row

    if path and s.present:
        atype = portage.classify(path)
        row.file = True
        row.manifested = True
        row.type = atype
        row.transform = portage.transform_for(atype)
        row.tool_id = portage.default_tool(atype)
        row.tool = TOOL_LABEL.get(row.tool_id, row.tool_id)
        row.dest = path
        row.export_path = export_path(path, asset.key)
        # ALL THREE LAYOUTS, COMPUTED ONCE. The panel's structure picker then
        # flips instantly instead of re-resolving the whole composition, and
        # -- the reason that matters -- the three paths are derived from ONE
        # row, so they cannot disagree about which file they describe.
        #
        # These are the layouts BEFORE `build_manifest`'s collision guard,
        # which can only ever push a file DEEPER (it inserts the file's own
        # parent directories). `grouped` is the layout where that happens;
        # `source` cannot collide at all, because logical paths are unique.
        row.readable = readable_sub(s, path)
        row.source_path = export_path(path, asset.key,
                                      layout=portage.LAYOUT_SOURCE)
        row.readable_path = export_path(path, asset.key, readable=row.readable,
                                        layout=portage.LAYOUT_READABLE)
        return row

    if is_unresolved or (not s.present):
        # DECLARED-BUT-ABSENT. It goes in the manifest with `absent:true` and
        # no export, so a reimport can tell "never there" from "deleted".
        # `dest` is the real path when the closure knew one; when it knew only
        # an id (an appearance row's `Texture0=8888888` that resolves nowhere)
        # there IS no path, and inventing a plausible one would be a guess the
        # manifest would then carry as fact.
        row.absent = True
        row.manifested = True
        row.present = False
        row.dest = path or f"unresolved/{s.group}/{s.asset_id or slug(s.label)}"
        if path:
            # A real path that this install does not ship: the type and the
            # tool ARE known, and saying so is what tells the user what they
            # would need if they found the file elsewhere.
            atype = portage.classify(path)
            row.type = atype
            row.transform = portage.transform_for(atype)
            row.tool_id = portage.default_tool(atype)
            row.tool = TOOL_LABEL.get(row.tool_id, row.tool_id)
        else:
            # An id the closure could not turn into a path. The FORMAT is not
            # known either -- `unresolved/texture/9999999` has no extension,
            # and classifying it would print "text editor" beside a texture.
            # A guess in the tool column is worse than a dash.
            row.type = ""
            row.transform = ""
        row.why = ("declared by this install but no file resolves for it -- "
                   "there is nothing to export, and the manifest records it "
                   "as absent so a reimport can tell 'never there' from "
                   "'deleted'")
        return row

    # Present, but not a file: a motion binding, a role part, a table this
    # install does not expose as a readable path.
    row.why = NOT_A_FILE
    row.tool = "-"
    return row


def _mark_duplicates(asset: PlanAsset) -> None:
    """Within ONE tier-1 asset, a file appears in the bundle once.

    The subject mesh is reported twice by the resolver -- as the Geometry row
    and again as the row carrying its MATR state -- and both name the same
    `.c3`. Two Export buttons writing the same bytes to the same bundle path
    is not two files, so the second row says which row it repeats. It stays
    VISIBLE, because the MATR fact is worth having; it just stops claiming to
    be a separate export.
    """
    first: dict = {}
    for r in asset.rows:
        key = r.dest or r.path
        if not key or not r.manifested:
            continue
        if key in first:
            r.duplicate_of = first[key]
            r.why = (f"the same file as the {first[key]} row above; the bundle "
                     f"carries it once")
            # No second Export button and no second manifest record: one file
            # under one asset is ONE entry in the zip. The row stays on screen
            # -- what it says about the file is still true -- it just stops
            # claiming to be a separate export.
            r.file = False
            r.manifested = False
            r.export_path = ""
        else:
            first[key] = r.group


def _binding_path(graph, s) -> str:
    """The logical path of the appearance table a binding row names.

    `assetroot` reports a binding as a LABEL plus the table's filename, because
    the reverse edge is about relationships. The pop-out has to show a real
    file path, so the filename is resolved back through the install's own part
    tables (`coassets.AssetRoot.part_tables`), which is where the resolver got
    it. A table that will not resolve returns "" and the row renders as a
    relationship, not as an invented `ini/<name>`.
    """
    name = str(getattr(s, "source", "") or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or "." not in name:
        return ""
    try:
        tables = graph.assets.part_tables()
    except Exception:                                        # noqa: BLE001
        return ""
    root = Path(getattr(graph, "root", "") or getattr(graph.assets, "root", ""))
    for ini in tables.values():
        if str(getattr(ini, "name", "")).lower() != name.lower():
            continue
        p = Path(getattr(ini, "path", "") or "")
        if not p.name:
            return ""
        try:
            logical = p.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            logical = f"ini/{p.name}"
        try:
            if graph.assets.locate(logical) is None:
                return ""
        except Exception:                                    # noqa: BLE001
            return ""
        return logical
    return ""


def _index_shared(plan: Plan) -> None:
    """Mark every row whose file is carried by more than one tier-1 asset.

    THE POINT OF THE FEATURE. Tier-1 grouping shows one texture twice, and
    without this the user reads two independent files. `shared_with` names the
    OTHER assets, so the sentence at the point of the click is "this is also
    weapon 410009's", not "shared" with no object.

    ONLY FILE ROWS ARE INDEXED, and that is a correction, not an omission.
    The first version indexed every manifested row, absent ones included, on
    the reasoning that two parts declaring the same missing texture is one
    fact about the composition. Running it against a live install showed what
    that actually produces: `c3/mesh/2000000.dds` -- a garment sibling NEITHER
    asset ships -- carried an amber "SHARED WITH" chip and the sentence
    "editing it under one asset changes it under every asset that uses it".
    That sentence is FALSE of a file that does not exist, and a warning that is
    false in the common case is how a true one stops being read. Absent rows
    are still SHOWN under every asset that declares them, and still recorded
    absent in the manifest; they simply do not claim an edit consequence they
    cannot have.
    """
    by_path: dict = {}
    for a in plan.assets:
        for r in a.rows:
            if not r.file:
                continue
            key = r.dest or r.path
            if not key:
                continue
            by_path.setdefault(key, [])
            if a.label not in by_path[key]:
                by_path[key].append(a.label)
    plan.shared = by_path
    for a in plan.assets:
        for r in a.rows:
            key = r.dest or r.path
            labels = by_path.get(key) or []
            if len(labels) > 1:
                r.shared = True
                r.shared_with = [L for L in labels if L != a.label]


# ---------------------------------------------------------------------------
# handing it to portage
# ---------------------------------------------------------------------------

def export_items(plan: Plan, select: Optional[list] = None) -> list:
    r"""`portage.ExportItem`s for the plan, in the order the panel drew them.

    `select` is None for the whole batch, or a list of ROW IDENTITIES. A row
    identity is the PAIR `(tier-1 key, dest)` -- accepted as a two-element
    list/tuple or as `{"key": ..., "dest": ...}` -- and a bare `dest` string
    means "this file under every asset that carries it".

    THE PAIR IS NOT PEDANTRY; A BARE `dest` IS WRONG FOR EXACTLY THE FILE THIS
    FEATURE IS ABOUT. Measured against a live server while building the panel:
    clicking `Export` on `body` with a dest-only selection wrote FOUR files
    instead of three, because the shared texture's dest also matches the
    weapon's row, so the weapon's folder came along uninvited. A shared
    satellite is the one case where a dest does not identify a row, and it is
    the case the whole pop-out exists for.

    `group` is set to the tier-1 key EXPLICITLY so the bundle's first path
    segment is the one the panel showed -- portage's own default would derive
    `body_7130030` from the file, which is not the label the user picked by.

    `shared` is passed through as well. `build_manifest` re-derives it from the
    groups it is given and takes the union, so this is belt and braces: a
    subset export that happens to contain only one of a shared file's two
    assets still records it as shared, because it IS shared in the install the
    bundle came from.
    """
    pairs, loose = _selection(select)
    out: list = []
    seen: set = set()
    contributing: set = set()
    for a in plan.assets:
        for r in a.rows:
            if not r.manifested or r.absent:
                continue
            if r.definition:
                # A DEFINITION IS NOT A FILE AND MUST NOT BECOME ONE HERE. Its
                # `dest` is the TABLE -- `ini/3DEffect.dbc` -- so a row that
                # fell through this branch would put all 3,391 (5517) / 5,313
                # (6609) definitions in the zip as one blob, and importing an
                # effect would revert every other effect on the target to the
                # exporter's. `definition_items` carries it, as a row.
                continue
            if pairs is not None and (a.key, r.dest) not in pairs \
                    and r.dest not in loose:
                continue
            key = (a.key, r.dest)
            if key in seen:          # one dest can be reached twice in a group
                continue
            seen.add(key)
            contributing.add(a.key)
            out.append(portage.ExportItem(logical=r.dest, group=a.key,
                                          shared=r.shared,
                                          readable=r.readable))

    # THE ABSENT ROWS ARE NOT SELECTABLE, AND THEY ARE NOT OPTIONAL.
    # They carry no bytes, so there is nothing for a checkbox to mean; what
    # they carry is the DISTINCTION between "this install never had it" and
    # "the user deleted it", which a reimport cannot recover from a manifest
    # that simply lacks them (backlog item 6, manifest point 4). Enforced here
    # rather than in the panel so no caller -- the pop-out, `curl`, a later
    # UI -- can drop them by forgetting to ask.
    for a in plan.assets:
        if a.key not in contributing:
            continue
        for r in a.rows:
            if not (r.manifested and r.absent):
                continue
            if (a.key, r.dest) in seen:
                continue
            seen.add((a.key, r.dest))
            out.append(portage.ExportItem(logical=r.dest, group=a.key,
                                          shared=r.shared))
    return out


def _selection(select) -> tuple:
    """`(pairs, loose)` from a `select` list, or `(None, set())` for the batch.

    Tolerant of the three shapes on purpose -- the pair is what the panel
    sends, and the bare string keeps the API usable from `curl` and from a
    caller that genuinely means "this file wherever it appears".
    """
    if select is None:
        return None, set()
    pairs: set = set()
    loose: set = set()
    for item in select:
        if isinstance(item, dict):
            pairs.add((str(item.get("key") or ""), str(item.get("dest") or "")))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.add((str(item[0]), str(item[1])))
        else:
            loose.add(str(item))
    return pairs, loose
