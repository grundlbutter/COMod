#!/usr/bin/env python3
r"""cosettings.py -- declared user settings, their defaults, and what each one
actually changes.

    from cosettings import get, set_value, describe
    if get("ui_recommendations"):
        print("Note: `show <id>` resolves an appearance to mesh+texture.")

WHY THIS EXISTS AS A REGISTRY RATHER THAN A DICT
------------------------------------------------
Every setting here declares four things: its **default**, its **type**, what it
**does**, and -- the load-bearing one -- **what observably changes when you
flip it**.  A setting whose effect cannot be named is a setting nobody can test,
and a toggle that changes nothing is worse than no toggle: it reads as working.

So `Setting.effect` is not documentation.  `tests/test_cosettings.py` asserts a
real behaviour change for every entry in `SETTINGS`, and that test is written to
FAIL if a setting is added without one.  Adding a knob here obliges you to make
it do something.

WHERE IT IS STORED, AND WHY NOT IN A NEW FILE
----------------------------------------------
`core/coroot.py` already owns the per-user document -- `read_settings()` /
`write_settings()` over ``%APPDATA%/co-client-re/config.json`` (or
``~/.config`` off Windows), which is also where ``game_root`` lives.  A second
`settings.json` beside it would be a second store to keep in sync and a second
place to look when a preference does not stick, so this namespaces INTO the
existing document under one key instead.

The path is never hardcoded here: it comes from `coroot.user_config_path()`.
`CO_SETTINGS_FILE` overrides the whole store, which is how the tests run
without writing to the owner's real config -- a test suite that persists to a
developer's actual preferences is a test suite that edits the thing it is
measuring.

READING A SETTING NEVER RAISES
------------------------------
An unreadable or corrupt store yields defaults.  A preference file is not
authoritative about anything; losing it should degrade the UI's politeness, not
stop a tool from running.  `set_value` DOES raise on an unknown name or a wrong
type, because that is a caller bug and silently storing it would produce a
setting that reads back as its default forever.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import coroot
except ImportError:                                       # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import coroot

__all__ = ["Setting", "SETTINGS", "get", "set_value", "reset", "all_values",
           "describe", "store_path", "UnknownSetting", "BadValue"]


class UnknownSetting(KeyError):
    """A name that is not declared in `SETTINGS`."""


class BadValue(ValueError):
    """A value of the wrong type for its declared setting."""


#: The key everything here lives under inside coroot's per-user document, so
#: UI preferences cannot collide with `game_root` or anything coroot adds.
NAMESPACE = "ui"

#: Overrides the whole store. Set by the tests; also useful for a throwaway
#: profile. When unset the location comes from `coroot.user_config_path()`.
ENV_OVERRIDE = "CO_SETTINGS_FILE"


@dataclass(frozen=True)
class Setting:
    """One declared preference.

    `effect` must name something a caller can OBSERVE changing. "Improves the
    experience" is not an effect; "`catalogs` and `browse` stop printing their
    trailing `Note:` blocks" is.
    """
    name: str
    default: Any
    kind: type
    help: str
    effect: str
    #: For int settings: the inclusive range a value must fall in.
    bounds: Optional[tuple] = None
    #: For str settings: the values permitted.
    choices: Optional[tuple] = None
    #: WHICH PANEL OWNS THE CONTROL. The registry stays one registry -- this
    #: does not split it -- but a settings page with three sections has to be
    #: told where each row belongs, and the alternative is a list of names
    #: hardcoded in `settings.js`. That list is the thing that goes stale: a
    #: setting renamed here would keep rendering in the old section, or in
    #: both. Declaring it beside the setting means the page cannot disagree
    #: with the registry about where a control lives.
    #:
    #:   "preferences" -- the default; the Preferences section.
    #:   "developer"   -- the Developer options section.
    #:   "panel"       -- NOT rendered as a generic row at all: some other
    #:                    panel draws its own control for it. `page_size` as
    #:                    a number box is a fine generic row; a set of install
    #:                    paths is not, and rendering one as a comma-joined
    #:                    text field would invite a hand-edit that the health
    #:                    panel then silently prunes.
    surface: str = "preferences"


SETTINGS = {s.name: s for s in (
    Setting(
        "ui_recommendations", True, bool,
        "Show the trailing Note:/recommendation blocks after a listing.",
        "`comod catalogs` and `comod browse` stop printing their trailing "
        "`Note:` and art-caveat blocks; the rows and the refusals are "
        "unchanged. Turning this off never hides a REFUSAL -- a refusal is an "
        "answer about the client, and the recommendations are advice about "
        "what to run next.",
    ),
    Setting(
        "refusal_detail", "full", str,
        "How much of a refusal's reasoning to print: full or brief.",
        "A refusal prints its first sentence only under `brief`, and the whole "
        "reason under `full`. The refusal is always NAMED either way -- this "
        "trims the explanation, never the fact that something was refused.",
        choices=("full", "brief"),
    ),
    Setting(
        "page_size", 200, int,
        "Rows per page when a listing does not say otherwise.",
        "`comod browse` with no --limit stops after this many rows and says "
        "how many it is showing of the total. 0 means no paging.",
        bounds=(0, 1_000_000),
    ),
    Setting(
        "thumbnails", True, bool,
        "Generate thumbnail images for asset listings.",
        "The viewer stops offering to generate thumbnails, and "
        "`POST /api/thumbs/start` refuses with a 409 naming this setting. "
        "**Generation only** -- thumbnails already on disk are still served, "
        "because turning off a renderer should not hide work already done. "
        "It is the same preference as the first-run dialog's 'never ask "
        "again': answering that writes through to here, so the two cannot "
        "disagree.",
    ),
    Setting(
        "developer_notes", False, bool,
        "Show provenance and disagreement notes meant for someone working on "
        "the tools rather than on a mod.",
        "`comod info <path>` prints a CONTESTED block when the asset's name "
        "hash is one the recovery runs disagreed about -- both candidate "
        "names and which one is in force. Off by default because a modder "
        "wants the name, not the argument behind it; on, because when the "
        "name is wrong this is the only place that says a second candidate "
        "ever existed.",
        # Moved out of Preferences on the owner's ask. It is a UI move and
        # NOT a schema change: same entry, same registry, same effect, same
        # stored key, so a value already set survives the move untouched.
        surface="developer",
    ),
    Setting(
        "cache_derived", False, bool,
        "Reuse decoded client tables between runs instead of re-decoding.",
        "Decoded `tq-stream` tables are stored under the per-install derived "
        "tree and reused. MEASURED on 6609, five alternating runs, medians: "
        "`catalogs()` 3,614 ms -> 1,512 ms, **2.4x**, saving 2,102 ms, with "
        "every row count identical. "
        "**It also changes what a catalog's control MEANS**: a cached decode "
        "cannot be its own independent witness, so a served entry reports "
        "`control_kind = cached` rather than `re-decode`, and `comod "
        "catalogs` says so. "
        "DEFAULTS OFF, and not because of staleness -- the key carries the "
        "source's size and mtime, so a changed table misses. It is that a hit "
        "reports a WEAKER control: defaulting it on would make the tool's "
        "ordinary output claim less evidence than it could have had, and "
        "would make a control_kind depend on whether some earlier run "
        "happened to warm the entry. Strongest evidence is the right default; "
        "speed is the opt-in.",
    ),
    Setting(
        "show_advanced_options", False, bool,
        "Show the developer-facing controls that are hidden by default.",
        "The Settings page's 'Supply an artefact' section offers a path field "
        "for EVERY derived artefact. With this off it offers one field, for "
        "`out/opcodes.json`, and says why the others are withheld. "
        "The restriction is not decoration. `out/opcodes.json` is built from "
        "`refs/` and opens no install at all, so one machine's copy is "
        "legitimately byte-identical to another's and supplying it carries no "
        "claim about anybody's client. Every other artefact in `DERIVED` is "
        "derived FROM an install: a supplied `out/wdf/c3_names.json` is a "
        "claim about archives the supplier had and the recipient does not, "
        "which is the case `coroot.override_verdict` exists to judge and the "
        "case a casual user should not be walked into by an open text field.",
        surface="developer",
    ),
    Setting(
        # THE UNIFIED SELECTION. One set of installs, read by bootstrap AND by
        # thumbnail generation, because the owner's reason for merging them is
        # that both track "what you are working on" -- and two independent
        # pickers for one intent is how a user bootstraps 5517 and then
        # generates thumbnails for 6609 without noticing.
        #
        # It lives HERE and not in a third store: `coroot` already owns the
        # per-user document and `cosettings` already namespaces into it, so
        # this is the same file and the same write path as every other
        # preference. `surface="panel"` because the health panel draws it as
        # a client checklist; a generic text row would be unusable.
        "selected_installs", [], list,
        "Which declared installs the health panel acts on -- bootstrap and "
        "thumbnail generation both read this one set.",
        "`POST /api/bootstrap/start` and `POST /api/thumbs/start` run once "
        "per install named here instead of once for the configured install, "
        "and the Settings page ticks these rows on load. "
        "EMPTY MEANS THE INSTALL BEING BROWSED -- not all of them and not "
        "none. All-of-them would let one click on a fresh profile start a "
        "nine-client bootstrap whose `wdf_recover` step alone is MEASURED at "
        "302-2,167 s PER CLIENT; none-of-them would leave both buttons dead "
        "on first visit with nothing on screen explaining it. Falling back to "
        "the browsed install reproduces exactly the single-client behaviour "
        "this page had before the set existed, so a user who never touches "
        "the checklist sees no change at all.",
        surface="panel",
    ),
)}

#: **Deliberately NOT declared yet: `default_build`.**
#:
#: They are named in the settings brief and they are wanted. They are absent
#: because their consumers are not written: the derived cache and a default
#: build have nothing reading them today.
#:
#: `cache_derived` has ALSO moved out, once profiling found its consumer: the
#: decoded tables dominate everything else the asset layer does. Declaring it
#: earlier would have been a knob over an unwritten cache.
#:
#: `thumbnails` WAS in this list and has moved into `SETTINGS`, because a
#: consumer turned out to exist -- `ThumbRunner` and the first-run prompt. It
#: is also the case for keeping this list honest: wiring it revealed that
#: `health.should_prompt` ALREADY honoured a stored "never ask again", so a
#: freshly-declared toggle beside it would have been a second switch for one
#: preference, able to disagree with the first. The dialog now writes through
#: to the setting instead.
#:
#: Declaring them now would mean shipping knobs that change nothing --
#: exactly what this module's own docstring calls worse than no toggle, since
#: a setting that appears in a list reads as connected to something. They land
#: with the panel that reads them, in the same change, with the behaviour test
#: `tests/test_cosettings.py` demands of every entry.
DEFERRED = ("default_build",)


def store_path() -> Path:
    """Where settings are read from and written to.

    `CO_SETTINGS_FILE` first, then coroot's per-user document. Never a literal
    path in this module.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    return coroot.user_config_path()


def _read_doc() -> dict:
    """The whole document, or {} for anything unreadable."""
    override = os.environ.get(ENV_OVERRIDE)
    if not override:
        try:
            doc = coroot.read_settings()
        except Exception:                                 # pragma: no cover
            return {}
        return doc if isinstance(doc, dict) else {}
    try:
        p = Path(override)
        if p.is_file():
            doc = json.loads(p.read_text("utf-8"))
            if isinstance(doc, dict):
                return doc
    except (OSError, ValueError):
        pass
    return {}


def _write_doc(doc: dict) -> Path:
    override = os.environ.get(ENV_OVERRIDE)
    if not override:
        coroot.write_settings(**{NAMESPACE: doc.get(NAMESPACE, {})})
        return coroot.user_config_path()
    p = Path(override)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), "utf-8")
    tmp.replace(p)
    return p


def _coerce(spec: Setting, value: Any) -> Any:
    """The declared type, or `BadValue`.

    `bool` is checked before `int` deliberately: `isinstance(True, int)` is
    True in Python, so an int-typed setting would accept `True` and store a
    boolean that later reads back as 1.
    """
    if spec.kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in (
                "1", "0", "true", "false", "yes", "no", "on", "off"):
            return value.lower() in ("1", "true", "yes", "on")
        raise BadValue(f"{spec.name} is a true/false setting, got {value!r}")
    if spec.kind is int:
        if isinstance(value, bool):
            raise BadValue(f"{spec.name} is a number, got a boolean")
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise BadValue(f"{spec.name} is a number, got {value!r}") from None
        if spec.bounds and not (spec.bounds[0] <= n <= spec.bounds[1]):
            raise BadValue(f"{spec.name} must be between {spec.bounds[0]} and "
                           f"{spec.bounds[1]}, got {n}")
        return n
    if spec.kind is list:
        # CHECKED BEFORE THE `str()` FALLBACK, and that ordering is the whole
        # reason this branch is written out rather than left to the tail of
        # the function. Without it a list falls through to `str(value)` and
        # is stored as its own repr -- `"['C:/a', 'C:/b']"` -- which reads
        # back through `get()` as a 20-character string that is not a path,
        # is not a list, and type-checks forever. No error is raised at any
        # point. That is the exact shape of the silent-corruption bug this
        # module's docstring says `set_value` raises to avoid.
        if isinstance(value, (str, bytes)) or not isinstance(value, (list,
                                                                     tuple)):
            raise BadValue(f"{spec.name} is a list of strings, got "
                           f"{type(value).__name__}")
        out = []
        for item in value:
            if not isinstance(item, str):
                raise BadValue(f"{spec.name} takes strings, got a "
                               f"{type(item).__name__}: {item!r}")
            item = item.strip()
            # De-duplicated and order-preserved. A set would be the natural
            # type and is deliberately not used: this is serialised to JSON,
            # which has no set, and a stored order that changes on every
            # write makes the config file's diff noise rather than history.
            if item and item not in out:
                out.append(item)
        return out
    s = str(value)
    if spec.choices and s not in spec.choices:
        raise BadValue(f"{spec.name} must be one of "
                       f"{', '.join(spec.choices)}, got {s!r}")
    return s


def get(name: str) -> Any:
    """The stored value, or the declared default. Never raises on the store."""
    spec = SETTINGS.get(name)
    if spec is None:
        raise UnknownSetting(name)
    stored = _read_doc().get(NAMESPACE, {})
    if not isinstance(stored, dict) or name not in stored:
        # COPIED, for the list kind. `Setting` is a frozen dataclass, which
        # freezes the ATTRIBUTE and not the list it points at: handing the
        # registry's own `[]` to every caller means one caller doing
        # `get("selected_installs").append(root)` silently edits the DEFAULT,
        # and from then on a fresh profile starts with somebody else's
        # install already selected. Frozen buys nothing here.
        if isinstance(spec.default, list):
            return list(spec.default)
        return spec.default
    try:
        return _coerce(spec, stored[name])
    except BadValue:
        # A stored value that no longer type-checks -- a hand-edited file, or a
        # setting whose type changed between versions. The default is the
        # honest answer; refusing to run because a PREFERENCE is malformed
        # would make the politeness layer load-bearing.
        return list(spec.default) if isinstance(spec.default, list) \
            else spec.default


def set_value(name: str, value: Any) -> Path:
    """Store one setting. Raises on an unknown name or a wrong type."""
    spec = SETTINGS.get(name)
    if spec is None:
        raise UnknownSetting(name)
    doc = _read_doc()
    ns = dict(doc.get(NAMESPACE) or {})
    ns[name] = _coerce(spec, value)
    doc[NAMESPACE] = ns
    return _write_doc(doc)


def reset(name: Optional[str] = None) -> Path:
    """Forget one setting, or all of them, so the defaults apply again."""
    doc = _read_doc()
    ns = dict(doc.get(NAMESPACE) or {})
    if name is None:
        ns = {}
    else:
        if name not in SETTINGS:
            raise UnknownSetting(name)
        ns.pop(name, None)
    doc[NAMESPACE] = ns
    return _write_doc(doc)


def all_values() -> dict:
    """Every declared setting's current value."""
    return {n: get(n) for n in SETTINGS}


def describe() -> list:
    """`(name, value, default, is_default, help, effect)` for a UI."""
    out = []
    for name, spec in SETTINGS.items():
        v = get(name)
        out.append((name, v, spec.default, v == spec.default,
                    spec.help, spec.effect))
    return out
