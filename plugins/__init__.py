#!/usr/bin/env python3
r"""
plugins -- parser plugins: one module per client flavour.

**A parser plugin teaches the app how to read one kind of client.** Patch
6090, patch 5065, CCO, Zephyr, a private server's repack -- each ships its
tables in different containers, spells its ids differently, and files its
colours by its own convention. None of that is guessable from the bytes, and
every time the app assumed one client's habits held for another it produced
confident wrong answers: monsters in another monster's skin, characters
T-posing, names seven years stale.

So the knowledge lives here rather than in the app, and the app asks.

WHAT A PLUGIN IS
----------------
A module in this package with a module-level ``PLUGIN`` -- an instance of
`Plugin` (or any object with the same attributes). Drop a file in, and it is
discovered; nothing else needs editing::

    plugins/
      __init__.py      this file: the registry and the contract
      patch6090.py     official patch 6090          (PLUGIN.name "patch6090")
      cco.py           Classic Conquer 2.0
      <yours>.py       your patch level or private server

`Plugin` implements every hook as "I have no opinion", so a plugin overrides
only what its client actually does differently. A three-line plugin is a
legitimate plugin.

HOW ONE IS CHOSEN
-----------------
The user says what a folder is when they add it (the first-run flow's client
kind), and that choice is stored as ``game_kind`` in the config. `for_kind`
resolves it. When nothing is stored, `detect` asks every plugin to look at
the install and takes the most confident answer -- but a stored declaration
always wins, because the user knows things the bytes do not (a repack of
6090 that a heuristic would read as vanilla).

THE CONTRACT
------------
Hooks are *policy*: which tables, which conventions, which corrections.
The *mechanics* stay in core -- `dbc.Rsdb` reads an RSDB table for anybody,
`tqdat.decrypt` decrypts for anybody, `c3phy` parses meshes for anybody. A
plugin that finds itself parsing bytes is usually a reader that belongs in
core with a plugin naming it.

Every hook may return None or an empty collection to mean "no opinion", and
the app falls back to its own inference. **A plugin is never obliged to be
complete**, and an honest gap beats a guess: the app can say "inferred" for
what it worked out itself, and only a plugin's answers are labelled
authored.

Usage::

    from plugins import for_kind, detect, available
    p = for_kind("patch6090") or detect(root)
    p.monster_colourways("103")     -> ['c3/texture/103000000.dds', ...]
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Callable, Iterable, Optional


class Plugin:
    """Base class and contract. Every hook is a no-opinion default.

    Subclass, set `name` and `label`, override what your client does
    differently. The docstring of each hook is the specification -- read the
    one you are overriding, and `patch6090.py` for a worked example of all
    of them.
    """

    #: Stable id, and what `game_kind` stores. Lowercase, no spaces.
    name = "generic"
    #: One line for the picker: what a user would recognise this as.
    label = "Generic Conquer Online client"
    #: Longer prose for the plugin's page in the UI. Say what is verified
    #: and what is inferred; a contributor's honesty here is load-bearing.
    notes = ""

    # -- identification ----------------------------------------------------
    def confidence(self, root: Path, exists: Callable[[str], bool]) -> float:
        """How sure this plugin is that `root` is its kind of client: 0.0
        (not mine) to 1.0 (certain). Judge by contents that only your kind
        ships -- a compiled table, a marker file, a version string -- never
        by the folder's name.

        Ties are broken by the most specific claim, so a private-server
        plugin recognising its own repack should out-confidence the patch
        level it derives from.
        """
        return 0.0

    # -- tables ------------------------------------------------------------
    def table_profile(self):
        """The entity-table set, as an `npcart.Profile`, or None to let
        `npcart.detect_profile` decide. This is what says whether NPCs come
        from `npc.json` or `npc.ini`, and whether the lookup tables are
        plaintext or compiled."""
        return None

    def prefers_compiled_tables(self) -> bool:
        """True when a compiled twin beside a plaintext table is the live
        one. Official 6090-era clients ship both and read the twin; the
        plaintext file is a stale decoy years out of date."""
        return False

    # -- art resolution ----------------------------------------------------
    def texture_for_mesh(self, mesh: str,
                         exists: Callable[[str], bool]) -> Optional[tuple]:
        """`(texture_path, method, kind)` for a mesh, or None for no
        opinion.

        `method` is shown to the user as the authority behind the answer, so
        name the rule ("npc texture family (999<dir>0)"), and set `kind` to
        "authored" only when a shipped table says so -- "inferred" when it
        is a convention you measured. A wrong answer labelled authored is
        worse than no answer: it stops anyone questioning it.

        `exists` resolves logical paths against the whole install, archives
        included. Use it rather than the filesystem: 331 of this install's
        archive names were never recovered, and a real texture can be
        unnamed but perfectly readable.
        """
        return None

    def colourways(self, texture: str,
                   exists: Callable[[str], bool]) -> Iterable[str]:
        """Every shipped colour of one texture, including it.

        Colour lives in the id, and where in the id is per-client and per
        family: 6090 varies the leading digit for monster bodies, the tens
        digit for equipment, and the middle digits for mounts. Return an
        empty list for no opinion.
        """
        return ()

    def monster_colourways(self, ident: str) -> Optional[list]:
        """The colour set of one monster directory, as logical texture
        paths, or None to fall back to `colourways`/observation.

        This exists because conventions over-reach: a digit rule that works
        for one family crosses into another's ids and dresses a monster in
        a stranger's skin. Verified sets belong here.
        """
        return None

    def default_colour(self, ident: str) -> Optional[str]:
        """Which colourway a model wears when nothing else says. Defaults
        to the first of `monster_colourways`."""
        cw = self.monster_colourways(ident)
        return cw[0] if cw else None

    def colour_provenance(self) -> tuple:
        """`(method, kind)` to label a skin that `default_colour` picked.

        The app used to hard-code "verified colourway (default)" /
        "authored" for every plugin's answer, which made a *subclass*
        inheriting an unverified set claim its parent's evidence. That is
        the exact failure this project has already paid for once -- a guess
        reported at high confidence, believed because of the confidence.

        So the claim belongs to whoever can support it. The default here is
        deliberately weak: a colour set that resolves is worth using and is
        not thereby measured. Override with "authored" only where a person
        looked at this client and said yes.
        """
        return "colourway (default)", "inferred"

    def flat_family_base(self, group: str, paths,
                         has_geometry: Callable[[str], bool]) -> Optional[str]:
        """The real body of a flat-numbered NPC family, or None.

        Some clients hide a skeleton layout in flat clothing: one file
        carries the mesh and its numbered siblings are motion sets whose
        embedded geometry is a fragment. Pick the mesh; the app binds the
        motions over it.
        """
        return None

    # -- attachment --------------------------------------------------------
    def slot_socket(self, slot: str) -> Optional[str]:
        """Which dummy chunk a part slot hangs off, or None for no opinion.

        Return the empty string to declare a slot **unattachable in this
        client** -- meaningfully different from None, which means "use the
        app's default map". A client whose bodies ship no `v_misc` cannot
        hang an accessory anywhere authored, and saying so is better than
        letting the app fall back to the body origin and draw a trinket in
        someone's navel.
        """
        return None

    def socket_correction(self, socket: str,
                          body_appearance: str) -> Optional[tuple]:
        """`(mode, note)` to repair a socket this client ships broken, or None.

        **This is the one hook that makes the app disagree with the client on
        purpose, and it is VIEWER-ONLY.** Everything else here describes what
        a client does; this says "what it does is wrong, show something else".
        So it carries a boundary the others do not:

        * Applied in `tools/coviewer.py` and nowhere else. `tools/attach.py`
          and `tools/parts.py` stay a faithful reading of the shipped data,
          which is what `client/` and any engine port read.
        * **Never port a correction into the client rewrite.** The goal is a
          *compatible* client: one that draws what the real client draws,
          artefacts included. A rewrite that quietly fixes the art has stopped
          being compatible and nobody will notice until it disagrees with a
          screenshot.
        * The app labels every corrected socket in its payload. A correction
          that cannot be seen is indistinguishable from a wrong reader.

        `mode` is what to do; `note` is why, in the user's words, and is shown
        on screen. Modes the viewer understands:

        * `"unit-rows"` -- scale each row of the 3x3 to unit length, keeping
          its direction and the translation untouched. Stops a degenerate
          basis collapsing the attached mesh. It does **not** make the basis a
          rotation when the rows are not orthogonal, and it restores the part
          to full size, which may itself look wrong.
        * `"reference-basis:<plugin>"` -- take the 3x3 from another **declared
          install** for the same body, socket, action and frame, keeping this
          client's own translation. Exact, where it applies: it is the right
          answer only when the two clients agree on where the socket *is* and
          differ only in how it is *oriented*, which is a thing to measure
          before claiming. Falls back to `unit-rows` when that install is not
          declared or its track does not line up, and the viewer says which
          of the two happened.

        A reference install is one the user declared (`coroot.declare_kind`);
        nothing goes looking for a client on its own.

        Return None for every socket you have no complaint about, which for
        almost every plugin is all of them.
        """
        return None

    def sockets_present(self) -> Optional[dict]:
        """`{slot_or_socket: note}` for what this client actually ships,
        measured rather than declared. `RolePart.ini` lists what the engine
        *understands*; the meshes decide what exists.
        """
        return None

    # -- table formats -----------------------------------------------------
    def table_quirks(self) -> dict:
        """How this client's tables differ in *form* from the obvious reading.

        `{short_name: description}`. Not consumed by the resolver -- the
        readers are tolerant by construction -- but this is where a
        contributor learns what bit them, and what to check first when a
        lookup silently returns nothing.

        Worth writing down precisely because these are the failures that do
        not raise: a stale file parses, a mispadded key just misses, and the
        result looks like content that does not exist.
        """
        return {}

    def key_field_widths(self) -> dict:
        """`{table: {field: width}}` where a key field is fixed-width.

        Declared, not enforced: comparisons are numeric where both sides are
        numeric, so a width change cannot hide a row. This says which widths
        were actually observed, so the next person can tell a real absence
        from a padding mismatch.
        """
        return {}

    def aura_convention(self) -> str:
        """How an always-on effect (the Super-weapon glow) is declared.

        * `"table"` -- an Action3DEffect row with the always-on action code
          points at the effect (CCO ships 826 of them).
        * `"effect-named-for-id"` -- no row; an effect whose *name* is the
          appearance id is itself the declaration.
        * `"both"` -- try the table, then the name.
        """
        return "both"

    # -- naming ------------------------------------------------------------
    def entity_name_overrides(self) -> tuple:
        """`(pins, drops)`: names to force, and names to refuse.

        Pins are `{(kind, ident): name}` for entities a dump names wrongly
        or not at all. Drops are `{(kind, ident)}` for joins that are
        confidently wrong -- a numeric label beats a name that describes a
        different creature.
        """
        return {}, set()

    def item_name(self, ident: str) -> Optional[str]:
        """An item or appearance's display name, when the plugin knows it
        better than the shipped item table does. Rarely needed."""
        return None

    # -- library import ----------------------------------------------------
    def import_plan(self, root: Path, exists: Callable[[str], bool]) -> dict:
        """What importing this client involves, for the add-a-library flow.

        Return keys the importer understands:

            archives   archive filenames to index (["c3.wdf", "data.wdf"])
            loose      True to walk loose files as an overlay layer
            tables     logical paths worth snapshotting for a server view
            skip       path fragments to leave alone (logs, caches)
            note       one line for the progress UI

        The default plan is the official one: two WDF archives plus a loose
        overlay. A DatPkg client overrides `archives`; a client with no
        archives at all sets it empty.
        """
        return {"archives": ["c3.wdf", "data.wdf"], "loose": True,
                "tables": ["ini/"], "skip": ["log/", "debug/", "AutoPatch"],
                "note": f"importing as {self.label}"}

    # -- housekeeping ------------------------------------------------------
    def __repr__(self) -> str:                        # pragma: no cover
        return f"<Plugin {self.name}>"


#: The no-opinion plugin, used when nothing matches. Named so the UI can say
#: "no parser plugin claimed this install" rather than pretending.
GENERIC = Plugin()


def _modules():
    for m in pkgutil.iter_modules([str(Path(__file__).resolve().parent)]):
        if not m.name.startswith("_"):
            yield m.name


def available() -> list:
    """Every discovered plugin, by declaration order of nothing in
    particular -- sort by `label` for display."""
    out = []
    for name in _modules():
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except Exception:                             # pragma: no cover
            continue
        p = getattr(mod, "PLUGIN", None)
        if p is not None:
            out.append(p)
    return out


def for_kind(kind: str):
    """The plugin a stored `game_kind` names, or None.

    Also accepts a plugin's `label` and any alias it declares, so a config
    written before a rename keeps working.
    """
    k = (kind or "").strip().lower()
    if not k:
        return None
    for p in available():
        names = {p.name.lower(), p.label.lower()}
        names |= {a.lower() for a in getattr(p, "aliases", ())}
        if k in names:
            return p
    return None


def detect(root: Path, exists: Optional[Callable[[str], bool]] = None):
    """The most confident plugin for an install, or GENERIC.

    Only for installs whose kind was never declared: a stored declaration
    is the user's and outranks any heuristic.
    """
    root = Path(root)
    if exists is None:
        def exists(p: str) -> bool:
            return (root / p).is_file()
    best, score = GENERIC, 0.0
    for p in available():
        try:
            c = float(p.confidence(root, exists))
        except Exception:                             # pragma: no cover
            continue
        if c > score:
            best, score = p, c
    return best
