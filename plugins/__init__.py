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

import dataclasses as _dc
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
    #: Who ships this client -- ``"official"`` for TQ Digital's own patch
    #: releases, ``"server"`` for a private server's own client.
    #:
    #: Declared here rather than inferred in the UI, which used to group by
    #: *where the folder was declared* and so filed **Classic Conquer 2.0**
    #: under "installed clients" beside the official patches. CCO is a
    #: private server's client that happens to be installed locally; being
    #: installed is not the same claim as being official, and only the
    #: plugin knows which it is. A name-prefix rule (``patch*``) would have
    #: worked today and broken on the first community plugin called
    #: ``patch-something``.
    #:
    #: ``"unknown"`` is the default on purpose: a new plugin that has not
    #: said gets grouped as unclassified rather than silently claiming to be
    #: TQ's.
    origin = "unknown"

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

    # -- TWO BASES ARE NOT ENOUGH TO CLASSIFY ANYTHING ---------------------
    #
    # The single most expensive method error in this package's history, and
    # it is cheap to repeat.  A session classified failing tests by running
    # them on CCO and on 5517 and calling the ones that failed on both "not a
    # base mismatch".  **All five of those were pinned to 6090** -- a base
    # the comparison never ran.  A test pinned to a third client fails on
    # both of the two you tried, by construction, so a two-base comparison
    # cannot see it and reports the one answer that looks like a finding.
    #
    #   * Passing on CCO tells you a test is base-dependent.  It does not
    #     tell you WHY.  Settle the why by running the two code paths side by
    #     side, not by reading the assertion.
    #   * CCO is not a neutral reference.  It ships the INTERLEAVED
    #     `PHY MOTI PHY MOTI` chunk layout where every official client ships
    #     the BLOCKED `PHY PHY MOTI MOTI` one, so a reader that pairs by
    #     adjacency instead of by ordinal is green on CCO and wrong
    #     everywhere else.  That is exactly how the render-matrix
    #     "disagreement" survived for weeks (`docs/CORRECTIONS.md` C16).
    #     `attach.PartMesh` is the authority on that pairing; nothing should
    #     walk the chunk stream itself.
    #   * Classify against EVERY declared install
    #     (`coroot.declare_kind` records them), not against a chosen pair.
    #
    # `docs/CORRECTIONS.md` §3 and C16 · `docs/socket_basis_settled.md`.

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

    def provides_reference(self) -> dict:
        """`{topic: note}` for what *other* plugins may legitimately borrow
        from this install, and on what evidence.

        The other half of `socket_correction`'s `reference-basis:<plugin>`.
        That mode names another plugin in a bare string and nothing on the
        far side ever agreed to it: `Patch6090` says `reference-basis:cco`,
        `coviewer._reference_root` looks the name up among the user's
        declared installs, and if the `cco` plugin were renamed, retired, or
        had never claimed to be a reference at all, the only symptom would be
        a silent downgrade to `unit-rows` -- the failure mode this project
        keeps paying for.

        So a borrower's claim is checkable: the referenced plugin declares
        what it is an authority *for*, and a test can assert the two agree.
        This is a declaration, not a mechanism -- nothing dispatches on it --
        but a claim with a named owner is one somebody can refute.

        Almost every plugin returns `{}`. Claim a topic only where this
        client is the *only* place the answer survives, and say why in the
        note, because that is the part a reader needs.
        """
        return {}

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
          points at the effect. **Every client measured so far does this:**
          CCO ships 826 such rows, 5517 ships 972, 6090 ships 2,604.
        * `"effect-named-for-id"` -- no row; an effect whose *name* is the
          appearance id is itself the declaration. **No known client. This
          value exists because it was once reported for 6090, on the strength
          of a lookup that returned nothing because the reader spelled the
          always-on action three wide while that client writes it four.** The
          rows were there the whole time; see CORRECTIONS C35. Do not reach
          for this because a table lookup came back empty -- measure the raw
          `ini/Action3DEffect.ini` first, and count the all-nines rows at
          whatever width that client writes them.
        * `"both"` -- try the table, then the name. The default, because it is
          the only honest answer for a client nobody has measured.

        The reason this hook is narrow: it is served to humans through
        `/api/plugins`, and a convention reported for a client that does not
        have it is worse than no answer at all -- it reads as two real designs
        to choose between when there is one.
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
    #: DatPkg index files an official client ships, base pair then overlays
    #: (7867+ adds `c31`/`data1`). Order is the order they are imported.
    DATPKG_ARCHIVES = ("c3.tpi", "data.tpi", "c31.tpi", "data1.tpi")

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
        archives = ["c3.wdf", "data.wdf"]
        # A DatPkg install: `.tpi` pairs present and no `.wdf` pair. Found
        # 2026-09-19 by the patch7280 plugin agent: every plugin without an
        # override (patch7878 included, through PlaintextFamily) named the
        # WDF pair here, and `assetdiff.catalog_baseline` skipped a missing
        # archive without a word -- so importing 7878 indexed NO archive and
        # reported success. The WDF pair stays the default whenever it or
        # nothing is found, which is the literal behaviour the official
        # clients and the tests pin.
        tpi = [n for n in self.DATPKG_ARCHIVES if exists(n)]
        if tpi and not any(exists(n) for n in archives):
            archives = tpi
        return {"archives": archives, "loose": True,
                "tables": ["ini/"], "skip": ["log/", "debug/", "AutoPatch"],
                "note": f"importing as {self.label}"}

    # -- catalogs: what this build can enumerate, and what it cannot -------
    #
    # This machinery started inside `patch7878` because 7878 needed it first.
    # It is not 7878 machinery, and the hierarchy forks below this class
    # (`PlaintextFamily`, `Patch6090`, and two plugins deriving straight from
    # here), so there is no lower point that every build shares. It lands here
    # and reads its detail from `plugins/catalog.py`.

    #: Text encoding for this build's tables. **Measured per build, never
    #: assumed** -- 7878 declares cp1256 in `ini/codepage.ini` and is actually
    #: GBK, and every candidate decodes 8-bit bytes without raising, so "it
    #: decoded" is not evidence. The default is the documented latin1 that
    #: `coassets.parse_ini` reasons to for the builds that ship `codepage.ini`
    #: as 0 or not at all.
    TEXT_ENCODING = "latin1"

    #: Column positions in an `@@` row. Only the ones a build has established.
    ITEM_COLUMNS = {"id": 0, "name": 1}

    #: Per-subject override of the label column, for tables that do not carry
    #: the default. Empty here: a build states its own after measuring, and
    #: assuming a `Name` key on 7878's `mounttype.dat` printed 1,937 blank
    #: cells before it was.
    ROW_LABEL_KEY: dict = {}
    ROW_LABEL_DEFAULT = "Name"

    #: `{subject: {"id": i, "name": j}}` -- the POSITIONAL twin of
    #: `ROW_LABEL_KEY`, MEASURED per build.
    #:
    #: `TableSpec.columns` is the right home for a hand-written spec, and it
    #: still wins here. It has no reach at all over the CENSUSED specs: those
    #: are generated tuples in `plugins/catalog/censused.py` with no `columns`
    #: field, so every one of the ~170 positional `.ini` tables fell back to
    #: `ITEM_COLUMNS` -- an `itemtype.dat` fact ("the name is column 1")
    #: applied to tables that are not `itemtype.dat`. `browse region` listed
    #: `1002 -> 4` on ten builds, where column 6 holds `TwinCity`.
    #:
    #: Empty here, and that is the point: a build declares its own after
    #: measuring its own file. `PositionalLabelsAreMeasuredPerBuild` re-derives
    #: every entry from the bytes on the install it names, so an entry
    #: inherited from a superclass is CHECKED on the subclass rather than
    #: assumed to carry over.
    ROW_COLUMNS: dict = {}

    #: `{filename_lower: encoding}` -- PER-TABLE overrides of `TEXT_ENCODING`,
    #: MEASURED per build. Applied onto every spec (hand-written or censused)
    #: whose own `TableSpec.encoding` is unset, by `apply_table_encoding`.
    #: Empty here, so a plugin that does not set it behaves exactly as before.
    #: For a GBK build whose table is not GBK: `{"chatfilter.ini": "latin1"}`.
    TABLE_ENCODING: dict = {}

    def spec_encoding(self, spec) -> str:
        """The codec that decodes `spec`'s text: its own, else the build's."""
        return getattr(spec, "encoding", None) or self.TEXT_ENCODING

    def apply_table_encoding(self, specs) -> tuple:
        """`specs` with `TABLE_ENCODING` applied where a spec says nothing."""
        if not self.TABLE_ENCODING:
            return tuple(specs)
        return tuple(
            _dc.replace(s, encoding=self.TABLE_ENCODING[s.filename.lower()])
            if s.encoding is None and s.filename.lower() in self.TABLE_ENCODING
            else s for s in specs)

    def measured_specs(self, root: Path) -> tuple:
        """`table_specs` with this build's measured positional columns on.

        The one place `ROW_COLUMNS` is applied, so `catalogs`, `browse` and
        `open_row` cannot disagree about which column a row's label is in --
        which is the shape of the defect this exists to fix, one level up:
        `catalogs` counted a table that `browse` then labelled from a
        different column than the control probe had checked.

        A spec that carries its own `columns` is left alone: it was measured
        against that one table by someone who knew what the table means.
        """
        specs = self.apply_table_encoding(self.table_specs(root))
        if not self.ROW_COLUMNS:
            return specs
        return tuple(
            _dc.replace(s, columns=self.ROW_COLUMNS[s.subject])
            if s.columns is None and s.subject in self.ROW_COLUMNS else s
            for s in specs)

    def table_specs(self, root: Path) -> tuple:
        """The `TableSpec`s this build declares, censused from its own `ini/`.

        Empty by default, and that is a real answer rather than a gap: a
        plugin that has not been measured must not inherit another build's
        table list. `catalogs()` turns an empty spec into a named refusal.
        """
        return ()

    def why_no_tables(self) -> str:
        """The reason `table_specs` is empty, for the refusal text."""
        return ("no table census has been done for this client, so nothing is "
                "declared rather than another build's shape being assumed")

    def load_table(self, spec, root: Path) -> tuple:
        """`(text, witness, control_kind, refusal)` for one spec.

        The default handles the two codecs that need no build-specific
        knowledge. A build whose tables live somewhere else -- 7878's decrypted
        corpus -- overrides this.
        """
        from . import catalog as _cat
        try:
            from core import tqdat
        except ImportError:                           # pragma: no cover
            import tqdat

        path = self._find_table(spec, root)
        if path is None:
            return None, b"", None, f"{spec.display} is not present"
        raw = path.read_bytes()

        if spec.codec == "plaintext":
            # **A UTF-8 BOM is stripped from the TEXT and kept in the
            # WITNESS.** 13 files across the installs carry one.
            #
            # Unstripped it prefixes the first line, and every grammar
            # mis-reads it in its own way: Zephyr's `ProfessionalName.ini`
            # yielded a first "row" of one field (the BOM alone), which
            # inflated the count by one AND handed `control_at_row` a row too
            # short to probe, so a clean 77-row table was refused outright.
            # `RacePointShop.ini` lost its `[Normal]` header the same way.
            #
            # It is NOT stripped from the witness: the witness is what the
            # file actually contains, and a control that searches a doctored
            # copy of the bytes is checking the reader against itself.
            text = raw
            if text.startswith(b"\xef\xbb\xbf"):
                text = text[3:]
            return (text.decode(self.spec_encoding(spec), "replace"), raw,
                    _cat.CONTROL_RAW, None)
        if spec.codec == "tq-stream":
            # A tq decrypt of `itemtype.dat` costs 736 ms and 6609's whole
            # catalogue costs 2,544 ms, so this is where a cache earns its
            # keep -- see `core/dcache.py`.
            try:
                from core import dcache            # noqa: PLC0415
            except ImportError:                    # pragma: no cover
                import dcache                      # noqa: PLC0415
            # `root` is passed EXPLICITLY. dcache's tree is per-install, and
            # coroot's default root is a per-process configured value -- so a
            # tool holding two catalogues at once (the viewer serving 6090 and
            # 5517 side by side) would otherwise read and write both builds'
            # decodes into whichever install happens to be configured. The
            # entry key carries the source path so it could not serve WRONG
            # bytes, but it would miss on every read while filling one tree
            # with another's tables: a cache that is never a hit and never
            # says why.
            cached = dcache.get(path, "tq-stream", root)
            if cached is not None:
                # **The cached decode cannot witness itself.** Handing these
                # same bytes back as the "independent re-decode" would make
                # the witness the parse's own output -- an identical-looking
                # count with the evidence removed. Reported as its own kind.
                return (cached.decode(self.spec_encoding(spec), "replace"),
                        cached, _cat.CONTROL_CACHED, None)
            # Decoded twice, from disk both times. The second decode is the
            # witness: it makes the PARSE checkable without making the CIPHER
            # checkable, which is why the control kind differs from plaintext.
            witness = tqdat.decrypt(path.read_bytes())
            decoded = tqdat.decrypt(raw)
            # Stored only AFTER the independent witness has been taken, so the
            # first run of a table always earns a real re-decode control and a
            # cache can never manufacture one.
            dcache.put(path, "tq-stream", decoded, root)
            return (decoded.decode(self.spec_encoding(spec), "replace"),
                    witness, _cat.CONTROL_REDECODE, None)
        if spec.codec == "block96":
            # The 6907..7878 ECB block cipher, read through the dictionary in
            # `core/block96.py`. Added 2026-08-30 for `patch7205`; every build
            # in that range needs it, so it lives here rather than in one
            # plugin's override.
            #
            # THREE THINGS THIS BRANCH GETS RIGHT AND A NAIVE ONE DOES NOT:
            #
            # 1. **The dictionary's absence is not the table's.** A box without
            #    `derived/7878-dat-decrypted/` refuses BY NAME, so "we cannot
            #    read this here" never renders as "this client has no items".
            #
            #    **AND THE LOOKUP IS ROOTED AT THE INSTALL, which it was not.**
            #    `block96.dictionary_path(None)` answers None unless
            #    `$CO_BLOCK96_DICT` is set -- the un-rooted call has no way to
            #    find `<ConquerAssets>/derived/`, because the relationship it
            #    resolves is `Clients/<build>` -> `../../derived`. So this
            #    branch refused BY NAME on a box that has the dictionary:
            #    MEASURED 2026-09-07, `patch7205.catalogs()` returned 16
            #    refusals all reading "the block96 dictionary is not on this
            #    box" against a `block_dict.pkl` sitting right there, and
            #    `patch6907`, whose override passes `root`, read the same
            #    cipher on ten sibling installs in the same sweep.
            #
            #    **The test that exists for this could not fire**:
            #    `tests/test_patch7205.py`'s `have_dict()` asked the same
            #    un-rooted question, so `test_with_the_dictionary_the_same_
            #    call_returns_rows` -- whose whole job is to be the control
            #    for the refusal above -- SKIPPED, and took nine other tests
            #    with it. A guard that shares the defect it guards against
            #    reports health exactly when the thing is broken.
            # 2. **Damaged records are dropped whole, and sections differently
            #    from rows.** `clean_sections` discards a section whose header
            #    or any key is holed; dropping the holed LINE would donate its
            #    remaining keys to the section above. See `core/block96.py`.
            # 3. **The control is `CONTROL_REDECODE`, never `CONTROL_RAW`.**
            #    The witness is a second decode from disk, so it witnesses the
            #    PARSE and not the CIPHER -- searching the witness for a served
            #    row asks the same dictionary twice. That is exactly the
            #    strength `tq-stream` claims above, for exactly the same
            #    reason, and calling it `raw` would overstate it.
            try:
                from core import block96                # noqa: PLC0415
            except ImportError:                         # pragma: no cover
                import block96                          # noqa: PLC0415
            table = block96.load_dictionary(root)
            if table is None:
                return None, b"", None, (
                    f"{spec.display}: {block96.why_no_dictionary(root)}")
            pt, total, unk = block96.decode(raw, table)
            if unk == total and total:
                return None, b"", None, (
                    f"{spec.display}: the dictionary carries none of this "
                    f"file's {total} blocks, so there is nothing to serve. "
                    f"Cross-table block reuse is small; a table 7878 never "
                    f"shipped is the expected case, not a defect.")
            if spec.kind == _cat.KIND_SECTIONS:
                text, kept, dropped = block96.clean_sections(pt)
            else:
                text, kept, dropped = block96.clean_rows(pt)
            if not kept:
                return None, b"", None, (
                    f"{spec.display}: {100.0 * (total - unk) / max(1, total):.1f}% "
                    f"of blocks are known but not one record came out whole "
                    f"({dropped} damaged). Block coverage is not record "
                    f"coverage -- see core/block96.py.")
            # The witness is an INDEPENDENT re-decode, read from disk again,
            # exactly as the tq-stream branch does. Same cleaning, or the
            # control would search a witness the served text is not in.
            wit_pt, _t, _u = block96.decode(path.read_bytes(), table)
            witness = (block96.clean_sections(wit_pt)[0]
                       if spec.kind == _cat.KIND_SECTIONS
                       else block96.clean_rows(wit_pt)[0])
            return (text.decode(self.spec_encoding(spec), "replace"), witness,
                    _cat.CONTROL_REDECODE, None)
        if spec.codec == "json":
            # JSON is plaintext, so the strong control applies: the witness is
            # the file itself and `control_json_row` searches the undecoded
            # bytes. Always utf-8 -- these are not the 8-bit ini tables and
            # TEXT_ENCODING does not apply to them.
            return raw.decode("utf-8", "replace"), raw, _cat.CONTROL_RAW, None
        if spec.codec == "binary-plain":
            # `GameMap.dat` is the one binary table whose grammar IS
            # established -- and on all seven builds at once, which is why it
            # reads here rather than in a per-build override. Everything else
            # binary is still named and refused.
            #
            # **`MagicType.dat` on 5017/5065 joined it 2026-09-07**
            # (`KIND_MAGIC_RECORDS`), which is why this is a set and not a
            # comparison: the next binary grammar that gets measured is a
            # one-line change here, and the refusal below stays the default.
            if spec.kind in (_cat.KIND_GAMEMAP, _cat.KIND_MAGIC_RECORDS):
                return None, raw, _cat.CONTROL_RAW, None
            return None, b"", None, (
                "binary record layout, not sections or rows -- readable bytes "
                "whose record shape this build has not established, so it is "
                "named rather than parsed wrongly")
        return None, b"", None, (
            f"{spec.display}: codec {spec.codec!r} has no reader on "
            f"{self.name}")

    def _find_table(self, spec, root: Path) -> Optional[Path]:
        """`ini/<filename>`, matched case-insensitively.

        6609 renames `Monster.dat` and `MagicType.dat` to lowercase while the
        builds either side of it do not, so an exact-case lookup silently
        loses two tables on exactly one build.
        """
        ini = Path(root) / "ini"
        if not ini.is_dir():
            return None
        exact = ini / spec.filename
        if exact.is_file():
            return exact
        want = spec.filename.lower()
        for p in ini.iterdir():
            if p.is_file() and p.name.lower() == want:
                return p
        return None

    def catalogs(self, root: Path) -> dict:
        """Per subject: how many rows, and a row actually opened.

        Every entry either carries a `control` -- a named row this reader
        opened and checked back against bytes it did not produce -- or a
        `refusal` saying what was missing.
        """
        from . import catalog as _cat
        root = Path(root)
        specs = self.measured_specs(root)
        if not specs:
            return _cat.no_tables_declared(self.name, self.why_no_tables())
        return _cat.build_catalogs(
            specs, lambda s: self.load_table(s, root),
            encoding=self.TEXT_ENCODING, columns=self.ITEM_COLUMNS)

    def _label_key(self, subject: str) -> Optional[str]:
        if subject in self.ROW_LABEL_KEY:
            return self.ROW_LABEL_KEY[subject]
        return self.ROW_LABEL_DEFAULT

    def browse(self, subject: str, root: Path, query: str = "",
               limit: int = 0) -> tuple:
        """`(rows, total, refusal)` -- `(id, label)` pairs for one subject.

        The listing half of `catalogs()`. Returns the refusal rather than an
        empty list when the subject cannot be read, so a caller can tell "this
        client has none" from "we could not look" without asking twice.
        """
        from . import catalog as _cat
        root = Path(root)

        # **The spec first, and `catalogs()` only when there is none.**
        #
        # This opened with `self.catalogs(root).get(subject)` -- building
        # EVERY table on the install to look up one. That was ~9 subjects per
        # build when it was written and is 130-164 now that the censused
        # `.ini` are declared, so browsing each subject once became quadratic:
        # ~17,000 table reads per build where 130 are needed, and the guard
        # test that browses every subject went from seconds to over ten
        # minutes.
        #
        # A subject WITH a spec needs one table read. The full-catalog path
        # stays for subjects that have no spec -- 7878's curated `npc:*`,
        # `monster`, `mount`, `item` and `garment` come from its bespoke
        # `catalogs()` and are not spec-driven -- so behaviour is unchanged
        # for them; they are a handful, not a hundred.
        spec = next((s for s in self.measured_specs(root)
                     if s.subject == subject), None)
        if spec is None:
            cat = self.catalogs(root).get(subject)
            if cat is None:
                known = ", ".join(sorted(self.catalogs(root))) or "none"
                return [], 0, (f"{self.name} has no subject {subject!r}; "
                               f"it declares: {known}")
            if not cat.ok:
                return [], 0, cat.refusal or f"{subject}: not readable"
            return [], 0, f"{subject}: no spec backs its catalog"
        text, witness, _kind, refusal = self.load_table(spec, root)
        if refusal:
            return [], 0, refusal

        # A spec's own `label_key` wins where it is set, because it is a
        # measurement of ONE table; `ROW_LABEL_KEY` is the plugin's per-subject
        # map and applies when the spec says nothing.
        key = (spec.label_key if spec.label_key != ""
               else self._label_key(subject))
        pairs = []
        if spec.kind == _cat.KIND_JSON_ROWS:
            try:
                pairs, _note, _first = _cat.json_rows(
                    witness, spec.id_key, spec.label_key or "name")
            except ValueError as e:
                return [], 0, f"{subject}: {e}"
        elif spec.kind == _cat.KIND_GAMEMAP:
            # Binary records, so this reads the witness rather than text. The
            # label is the map's PATH, which is the thing a caller goes on to
            # use -- `map <id>` and `extract <path>` both take it.
            try:
                pairs, _note = _cat.gamemap_records(witness)
            except ValueError as e:
                return [], 0, f"{subject}: {e}"
        elif spec.kind == _cat.KIND_MAGIC_RECORDS:
            # Binary records again, so the witness rather than text. The label
            # is the skill NAME out of the record's `char[16]`, which is the
            # half of the row this project has established; the level is
            # `id % 10` and is left in the id rather than split out.
            #
            # **Deduplicated FIRST-WINS, through the same helper `catalogs()`
            # uses.** The index array repeats ids -- 648 records over 642
            # distinct on 5017 -- and listing all 648 here while the catalog
            # reported 642 is a surface that tells the user one number and
            # shows them another. `test_subject_source_agreement` caught
            # exactly that on 5065 (651 vs 657); the repeats are not lost,
            # they are on `Catalog.duplicated`.
            try:
                pairs, _note = _cat.magic_records(witness)
            except ValueError as e:
                return [], 0, f"{subject}: {e}"
            pairs, _dupes = _cat.magic_unique(pairs)
        elif text is None:
            return [], 0, f"{subject}: unreadable on a second pass"
        elif spec.kind == _cat.KIND_SECTIONS:
            secs, _ = _cat.sections_from_text(text)
            for ident, body in secs.items():
                pairs.append((ident, body.get(key, "") if key else ""))
        elif spec.kind == _cat.KIND_FLAT_KEYS:
            # The VALUE is the label, and it is the useful half: these tables
            # map an appearance id to a mesh path, and the path is what a
            # caller goes on to use.
            try:
                fk, _dupes = _cat.flat_keys_from_text(text)
            except ValueError as e:
                return [], 0, f"{subject}: {e}"
            pairs = list(fk.items())
        elif spec.kind == _cat.KIND_LIST:
            try:
                pairs = [(v, "") for v in _cat.list_from_text(text)]
            except ValueError as e:
                return [], 0, f"{subject}: {e}"
        else:
            # **THE TABLE'S OWN DELIMITER, not `@@`.**
            #
            # This branch called `at_rows_from_text` for every row kind, so a
            # `csv-rows` or `space-rows` table was split on `@@`, produced one
            # field per line, failed the `len(r) > max(...)` test on every row
            # and returned an EMPTY list -- with no refusal, because nothing
            # here raised.
            #
            # It was live on master and not small: `browse magic:op` on 6090
            # returned 0 rows against a catalogue claiming 647, and `browse
            # item` on 5017/5065/5165 returned 0 against 11,255. The surface
            # said "this client has none" in exactly the words it uses for
            # "we could not look" -- the distinction `browse`'s own docstring
            # promises to keep.
            #
            # It survived because `NoSubjectPrintsAColumnOfBlanks` skips a
            # subject that browses zero rows (`if refusal or not rows:
            # continue`), so the one test looking at this surface treated the
            # symptom as "nothing to check".
            delim = _cat.ROW_KINDS.get(spec.kind, "@@")
            rows = _cat.rows_from_text(text, delim)
            # **THE SPEC'S OWN COLUMNS FIRST.** `ITEM_COLUMNS` is a fact
            # about `itemtype.dat` -- name in column 1 -- applied here to
            # every positional table on the build. `Achievement.dat` carries
            # a message id in column 1 and the name in column 2, so under the
            # plugin-wide map this listed a column of numbers where the names
            # are. A spec that has MEASURED its own columns says so.
            cols = spec.columns or self.ITEM_COLUMNS
            i_id = cols.get("id", 0)
            i_nm = cols.get("name", 1)
            if i_nm is None:
                # **MEASURED: no column of this table is text.** The 6907
                # era's `.dat` config tables are numbers all the way across --
                # 24 of the 25 censused there -- and `ITEM_COLUMNS["name"] = 1`
                # would label every row with a number, which is the `magic`
                # defect one table at a time. An empty label is what "the id
                # stands alone" looks like, and the spec says so with
                # `label_key=None` so the blank-label guard permits it.
                pairs = [(r[i_id], "") for r in rows if len(r) > i_id]
            else:
                # **A ROW TOO SHORT TO REACH THE NAME COLUMN KEEPS ITS PLACE
                # AND LOSES ITS LABEL. It used to lose its existence.**
                #
                # `block96.recover_rows` serves a damaged row TRUNCATED at the
                # damage, so a table whose name is column 3 comes back as a
                # mixture of full rows and rows of 2 and 3 fields. Dropping
                # the short ones made `browse` return fewer rows than
                # `catalogs` counted, with no refusal -- the difference in
                # EXISTENCE that `test_a_counted_table_never_browses_EMPTY_
                # without_saying_why` forbids, one notch below total.
                #
                # MEASURED before the change, over every positional table on
                # all nineteen installs on this box: ZERO subjects lost a row
                # to it, because nothing yet declared a name column past its
                # shortest row. `patch6907.hairface_storage_type` is the first
                # -- 35 of 186 rows on 7009 and 14 of 195 on 7135 -- and the
                # comment that held that declaration back named this line as
                # the alternative to fixing the recovery.
                #
                # The id is still required: a row with no `i_id` field has no
                # identity to list, which is a different thing from having no
                # name.
                for r in rows:
                    if len(r) > i_id:
                        pairs.append((r[i_id],
                                      r[i_nm] if len(r) > i_nm else ""))

        if query:
            q = query.lower()
            pairs = [p for p in pairs if q in p[0].lower() or q in p[1].lower()]
        total = len(pairs)
        if limit and limit > 0:
            pairs = pairs[:limit]
        return pairs, total, None

    #: **UNCONSUMED IN PRODUCTION, and declared so rather than left to be
    #: discovered.** Nothing in `core/` or `tools/` calls `open_row`; its only
    #: callers are its own tests. `catalogs` and `browse` are both reached from
    #: `tools/comod.py`, so this is the one hook of the four that no surface
    #: exercises.
    #:
    #: It stays because it is the "can I open one" half of the contract the
    #: rest of this machinery rests on -- a count is not evidence unless a
    #: specific row can be opened -- and the tests use it for exactly that.
    #: But a declared hook with a passing test and no consumer is the
    #: `C46-dds-numpy` shape: it reads as supported, it is green, and nothing
    #: exercises it in anger. `patch5017.PART_SLOTS` carries the same
    #: declaration for the same reason.
    #:
    #: The consumer it is waiting for is a `comod show-row <subject> <id>`
    #: verb, or the web UI's row detail panel. Whoever adds either should
    #: delete this note in the same change.
    UNCONSUMED_HOOKS = ("open_row",)

    def open_row(self, subject: str, root: Path, ident: str) -> Optional[dict]:
        """One row by id, as a dict, or None when the subject cannot be read."""
        from . import catalog as _cat
        root = Path(root)
        spec = next((s for s in self.measured_specs(root)
                     if s.subject == subject), None)
        if spec is None:
            return None
        text, _witness, _kind, refusal = self.load_table(spec, root)
        if refusal or text is None:
            return None
        if spec.kind == _cat.KIND_JSON_ROWS:
            import json as _json
            try:
                doc = _json.loads(text)
            except ValueError:
                return None
            for entry in doc if isinstance(doc, list) else ():
                if isinstance(entry, dict) and str(
                        entry.get(spec.id_key)) == ident:
                    return dict(entry)
            return None
        if spec.kind == _cat.KIND_SECTIONS:
            secs, _ = _cat.sections_from_text(text)
            body = secs.get(ident)
            return dict(body) if body is not None else None
        # **THE TABLE'S OWN DELIMITER AND ITS OWN COLUMNS**, the same two
        # corrections `browse` above carries. `at_rows_from_text` splits on
        # `@@` whatever the kind says, so a `csv-rows` or `space-rows` table
        # produced one field per line here and `open_row` returned None for
        # every id -- indistinguishable from "no such row".
        cols = spec.columns or self.ITEM_COLUMNS
        i_id = cols.get("id", 0)
        delim = _cat.ROW_KINDS.get(spec.kind, "@@")
        for r in _cat.rows_from_text(text, delim):
            if len(r) > i_id and r[i_id] == ident:
                return {str(i): v for i, v in enumerate(r)}
        return None

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


# ---------------------------------------------------------------------------
# Discovery, and the one thing it must never do quietly
# ---------------------------------------------------------------------------
#
# `available()` used to import every discovered module under a blanket
# ``except Exception: continue``. That ``except`` was written for a good
# reason -- **a contributor's broken plugin must not take down the picker** --
# and it also swallowed breakage in this package's own shared machinery,
# where the same silence is a confident wrong answer.
#
# MEASURED on this tree (`C:\Claude\co-discovery` @ 9cfd5ca), with a
# `sys.meta_path` hook making one named module unimportable and nothing else
# changed. CONTROL first, because a poisoned run that returns a short list is
# only evidence if the unpoisoned run returns the long one:
#
#     poisoned module      available() returned                      count
#     (nothing)  CONTROL   all ten plugins                             10
#     plugins.catalog      plaintext                                    1
#     plugins.plaintext    cco, patch5517, patch6090, patch6609         4
#     plugins.patch6090    everything except 5517/6090/6609             7
#     plugins.patch5017    everything except 5017/5065/5165             7
#     struct               plaintext                                    1
#     core.tqdat           all ten                                     10
#     core.coroot          all ten                                     10
#
# **Nothing raised in any run.** And the shortfall reached the user as a
# claim, not as a gap -- with `plugins.catalog` poisoned, the setup page's
# own words are:
#
#     no parser plugin named 'patch6090'. Available: plaintext
#
# which is `docs/CORRECTIONS.md` C21's shape exactly: a check that has
# stopped discriminating still returns a plausible answer. A user with ten
# clients cannot tell that from a machine with one.
#
# Two things the sweep settles, and they shape the fix:
#
# * The blast radius is **entirely inside `plugins/`**. `core.tqdat`,
#   `core.coroot`, `core.dcache` and `npcart` are all imported *inside
#   functions*, so none of them can affect discovery at all. What actually
#   breaks it is the package's own siblings -- `catalog` (9 of 10 plugins
#   import it at module level; `plaintext` is the only one that does not,
#   which is the whole of the asymmetry) and the three base-class modules
#   `plaintext`, `patch5017`, `patch6090`.
# * `struct` losing 9 plugins shows a **transitive** failure has the same
#   shape, so a rule about "which module raised" would have to walk a chain
#   and would still be a guess. It is not guessed here.
#
# WHY A MANIFEST RATHER THAN A HEURISTIC
# --------------------------------------
# The interesting problem is telling a contributor's breakage from ours, and
# nothing in the traceback answers it reliably: a third-party plugin whose
# own `import yaml` fails and a first-party plugin whose shared base class
# fails are the same exception shape. So the two cases are separated by a
# **declaration** instead of by inference. `FIRST_PARTY` is the list of
# modules that ship in this repo and are therefore *ours*:
#
#   * one of them fails  ->  the checkout is broken, and no plausible answer
#     may be returned. `available()` raises `DiscoveryError`.
#   * anything else fails ->  tolerated exactly as before, and **recorded**,
#     so `problems()` can carry the reason out to a surface.
#
# Same fail-closed, hand-maintained shape as `tools/gates.py` CLASSES and
# `coroot.VOLATILE_INI`, and the same carried-out-reason shape as
# `capture/coprofile._scan`, which already returns `(profiles, problems)`
# because *"the layout for your build failed to parse" and "there is no
# layout for your build" are different problems with different fixes*.
#
# WHAT THIS DECLARATION DOES NOT CATCH, said here rather than discovered
# ---------------------------------------------------------------------
# Add a first-party plugin and forget to list it, and it is treated as
# third-party: tolerated and recorded rather than fatal. That is a weaker
# guard, not a false alarm, and `tests/test_plugin_discovery.py` asserts
# every listed module exists so the list cannot rot in the other direction.
# The manifest is deliberately silent about modules it does not name, which
# is also what keeps "drop a file in and it is discovered" true.

#: The modules this repo ships, and the `PLUGIN.name` each must declare.
#: ``None`` means shared machinery with no plugin of its own. Anything not
#: named here is a contributor's, and its failure is survivable.
FIRST_PARTY: dict = {
    "catalog": None,            # shared machinery -- 9 of 10 plugins import it
    "cco": "cco",
    "patch5017": "patch5017",
    "patch5065": "patch5065",
    "patch5165": "patch5165",
    "patch5517": "patch5517",
    "patch6090": "patch6090",
    "patch6609": "patch6609",
    "patch6907": "patch6907",
    "patch7205": "patch7205",
    # -- RESERVED PLUGIN SLOTS, the 7205..7632 official patches (owner,
    #    2026-09-19; plan: scratchpad/PLAN-official-patch-notes.md). Each
    #    plugin PR replaces ONLY its own `slot` line. The bare `#` lines
    #    between slots are never edited: git conflicts on ADJACENT line
    #    edits, so one untouched line between slots is what lets twelve
    #    parallel plugin PRs merge without a conflict push.
    "patch7217": "patch7217",
    #
    "patch7250": "patch7250",
    #
    "patch7275": "patch7275",
    #
    "patch7280": "patch7280",
    #
    "patch7320": "patch7320",
    #
    "patch7336": "patch7336",
    #
    "patch7373": "patch7373",
    #
    "patch7387": "patch7387",
    #
    "patch7506": "patch7506",
    #
    "patch7535": "patch7535",
    #
    "patch7562": "patch7562",
    #
    "patch7589": "patch7589",
    # -- end of reserved plugin slots
    # -- RESERVED PLUGIN SLOTS (2), the 20 official patches still without a plugin
    #    (owner, 2026-09-19 ~15:50Z: "a parser plugin and a patch notes for every
    #    patch that now exists in our corpus"). Same rule as the block above: each
    #    plugin PR replaces ONLY its own `slot` line and never edits the bare `#`
    #    lines.
    "patch4274": "patch4274",
    #
    "patch6256": "patch6256",
    #
    "patch6271": "patch6271",
    #
    "patch6652": "patch6652",
    #
    "patch6680": "patch6680",
    #
    "patch6707": "patch6707",
    #
    "patch6772": "patch6772",
    #
    "patch6805": "patch6805",
    #
    "patch6868": "patch6868",
    #
    "patch6968": "patch6968",
    #
    "patch7009": "patch7009",
    #
    "patch7065": "patch7065",
    #
    "patch7083": "patch7083",
    #
    "patch7110": "patch7110",
    #
    "patch7135": "patch7135",
    #
    "patch7170": "patch7170",
    #
    "patch7182": "patch7182",
    #
    "patch7189": "patch7189",
    #
    "patch7622": "patch7622",
    #
    "patch7867": "patch7867",
    # -- end of reserved plugin slots (2)
    "patch7878": "patch7878",
    "plaintext": "plaintext",
    "zephyr1057": "zephyr1057",
}


class DiscoveryError(ImportError):
    """A module this repo ships would not import, so the plugin list is short.

    Raised rather than returned because the short list is indistinguishable
    from a real one: every caller of `available()` presents it as *the*
    answer, and `for_kind` turns it into "no parser plugin named X".
    """


def _discover() -> tuple:
    """`(plugins, problems)` -- what loaded, and why the rest did not.

    `problems` is `[(module, reason, ours)]` where `ours` is True for a
    module named in `FIRST_PARTY`. Never raises: this is the honest scan,
    and `available()` is the one that decides a first-party problem is fatal.
    """
    out, probs = [], []
    for name in _modules():
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except BaseException as e:
            # BaseException, not Exception: a module raising SystemExit or
            # KeyboardInterrupt at import time removed itself from the list
            # without even reaching the old `except`, which is the same
            # silence by a different door.
            probs.append((name, f"{type(e).__name__}: {e}",
                          name in FIRST_PARTY))
            continue
        p = getattr(mod, "PLUGIN", None)
        if p is not None:
            out.append(p)
        want = FIRST_PARTY.get(name, "")
        if want is None or want == "":
            continue                                  # machinery, or not ours
        got = getattr(p, "name", None)
        if got != want:
            # Imported fine but stopped declaring what it is. Silent today:
            # the module vanishes from the picker with nothing raised.
            probs.append((name, f"declares PLUGIN.name {got!r}, "
                                f"and this repo ships it as {want!r}", True))
    return out, probs


def problems() -> list:
    """`[(module, reason, ours)]` for every module that did not contribute.

    The diagnostic half of discovery, and the reason `available()` can afford
    to be strict: a surface that wants to keep going -- the setup page's
    picker -- can show what is missing instead of a shorter list with no note.
    """
    return _discover()[1]


def available() -> list:
    """Every discovered plugin, by declaration order of nothing in
    particular -- sort by `label` for display.

    Raises `DiscoveryError` when a module named in `FIRST_PARTY` did not
    contribute. A contributor's broken plugin is still skipped, and is
    readable through `problems()`.
    """
    out, probs = _discover()
    ours = [p for p in probs if p[2]]
    if ours:
        detail = "; ".join(f"plugins/{n}: {why}" for n, why, _ in ours)
        raise DiscoveryError(
            f"{len(ours)} module(s) this repo ships did not load, so the "
            f"plugin list is short by an unknown amount -- {detail}. "
            f"Only {len(out)} plugin(s) were discovered "
            f"({', '.join(sorted(p.name for p in out)) or 'none'}); the "
            f"checkout is broken rather than this machine having that many "
            f"clients. `plugins.problems()` lists every module that did not "
            f"contribute, this repo's and a contributor's alike.")
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


#: Below this a detection is a GUESS, not an identification.
#:
#: THE THRESHOLDS LIVE HERE because `rank`'s own docstring already says the
#: threshold is the codebase's and not any one caller's -- and they were
#: written twice, in `tools/comod.py` and `tools/coviewer.py`, which is how a
#: literal drifts.
CONFIDENT = 0.9

#: Two candidates within this of each other are a TIE. A tie is not an answer.
#:
#: **THE BOUNDARY IS `<=`, AND IT WAS TWO DIFFERENT RULES.** `comod` tested
#: `(score - second) < TIE` and the viewer `(top - second) <= DETECT_TIE`, so
#: a gap of exactly 0.05 was a tie on the picker and a clean identification
#: at `comod clients add` -- the two surfaces telling the same user opposite
#: things about the same folder. `<=` is what the viewer ships and what the
#: owner has been seeing, so `<=` is what this keeps.
TIE = 0.05


def verdict(ranked: list) -> dict:
    """`{verdict, suggested, confidence, gap, ask}` for a `rank()` result.

    One implementation of "is this an identification, a tie, a weak guess or
    nothing", because the two callers had it twice with different boundaries.
    Pure: it takes the ranking and returns a judgement, so it can be tested
    without an install.
    """
    out = {"verdict": "none", "suggested": None, "confidence": None,
           "gap": None, "ask": True}
    if not ranked:
        return out
    top = float(ranked[0][1])
    second = float(ranked[1][1]) if len(ranked) > 1 else None
    out["confidence"] = round(top, 4)
    out["gap"] = None if second is None else round(top - second, 4)
    out["suggested"] = ranked[0][0].name
    if second is not None and (top - second) <= TIE:
        out["verdict"] = "tie"
    elif top < CONFIDENT:
        out["verdict"] = "weak"
    else:
        out["verdict"] = "confident"
        out["ask"] = False
    return out


def rank(root: Path, exists: Optional[Callable[[str], bool]] = None) -> list:
    """`[(plugin, confidence)]`, most confident first, zero scores dropped.

    **`detect` throws away everything except the winner**, and the discarded
    part is what a person needs in order to accept or overrule it: a 0.95 from
    a version stamp and a 0.5 from "this looks vaguely like my family" are the
    same answer through `detect` and are not the same claim at all. Two
    plugins tied at 0.5 are not an answer either, and `detect` returns one of
    them with nothing to say it had a twin.

    So the ranking is what the picker shows and what `comod clients add`
    refuses on. `detect` keeps its own contract and is now written in terms of
    this.

    THE SECOND SWALLOW, and it is on the FIRST-RUN path
    ---------------------------------------------------
    `confidence()` raising used to be caught here by a bare
    ``except Exception: continue``, so a plugin that threw simply scored
    nothing and the ranking closed over the hole. That is the same defect as
    `available()`'s one door along, and worse placed: `detect` is written in
    terms of this, and `detect` is what runs when no `game_kind` is stored --
    a **fresh user's very first run**. The symptom was `detect` returning
    `GENERIC`, which the UI states as *"no parser plugin claimed this
    install"* -- a finding about the user's client, produced by a bug in ours.

    Precision worth keeping, because the line was reported one function
    over: the swallow was at `plugins/__init__.py:749` **in `rank`**, not in
    `detect`. `detect` has no `except` of its own; it inherits this one by
    delegation. Both halves of the report are right about the path.

    Split the same way as import failure: ours raises, a contributor's is
    skipped. A first-party `confidence()` that throws is a bug in this repo,
    and `comod._plugin_for` already argues the general case -- *"a guard
    around a first-party module protects nothing and converts a bug into a
    fact about the user's data."*
    """
    ranked, probs = _rank(root, exists)
    ours = [p for p in probs if p[2]]
    if ours:
        detail = "; ".join(f"{n}.confidence(): {why}" for n, why, _ in ours)
        raise DiscoveryError(
            f"{len(ours)} plugin(s) this repo ships raised while judging "
            f"{root} -- {detail}. The ranking is short by an unknown amount, "
            f"so `detect` would answer GENERIC and the UI would report that "
            f"as 'no parser plugin claimed this install' -- a statement about "
            f"the client, caused by a defect in this repo.")
    return ranked


def _rank(root: Path, exists: Optional[Callable[[str], bool]] = None) -> tuple:
    """`([(plugin, confidence)], problems)` -- the honest ranking scan.

    Never raises for a plugin's own fault; `rank()` decides which faults are
    fatal. `problems` is `[(plugin_name, reason, ours)]`, same shape as
    `problems()`.
    """
    root = Path(root)
    if exists is None:
        def exists(p: str) -> bool:
            return (root / p).is_file()
    out, probs = [], []
    for p in available():
        try:
            c = float(p.confidence(root, exists))
        except BaseException as e:
            probs.append((p.name, f"{type(e).__name__}: {e}",
                          p.name in _FIRST_PARTY_PLUGIN_NAMES))
            continue
        if c > 0:
            out.append((p, c))
    out.sort(key=lambda pc: (-pc[1], pc[0].name))
    return out, probs


#: `FIRST_PARTY` keyed by the plugin name rather than the module name -- the
#: two differ nowhere today and the manifest is what says so.
_FIRST_PARTY_PLUGIN_NAMES = frozenset(
    n for n in FIRST_PARTY.values() if n)


def selfcheck(out=None) -> int:
    """Print a verdict on this checkout's discovery, and return an exit code.

    **The guard that ships.** It lives in `plugins/__init__.py` rather than
    in `tests/` on purpose: the public COMod extraction copies a hand-listed
    subset of this repo and ships no test for any of it, so a guard in
    `tests/` is a guard the tree that needs it most does not have. Run it
    anywhere::

        py -3 -c "import plugins, sys; sys.exit(plugins.selfcheck())"

    **The weaker form of this check is refuted, and by measurement.**
    ``py -3 -c "import plugins; plugins.available()"`` was proposed as the
    guard and **exited 0 on a tree where every single plugin was dead** --
    MEASURED on a real `tools/extract_comod.py` output, where
    `plugins/catalog/` is not shipped (`PLUGINS` lists module *names* and
    appends `.py` to each, so a package directory cannot be named) and
    `available()` returned `[]`. It exits 1 there now, but only because the
    swallow was removed first; as a guard by itself it was never testing
    anything.

    What this asserts, and why each half is needed:

    * every module `FIRST_PARTY` names **that is present in this tree**
      imported, and declared the plugin name the manifest says it does. Not
      "all ten are present": the public extraction legitimately ships three,
      and a guard that demanded ten would be permanently red there and would
      be turned off.
    * **zero import failures were swallowed** -- including a contributor's,
      which `available()` tolerates. Tolerated is not invisible.
    * at least one plugin was discovered. A tree that ships none has nothing
      to be short *of*, and `[]` is the answer the whole defect produced.
    """
    import sys as _sys
    out = out or _sys.stdout
    found, probs = _discover()
    present = {n for n in FIRST_PARTY if n in set(_modules())}
    want = {FIRST_PARTY[n] for n in present if FIRST_PARTY[n]}
    got = {p.name for p in found}
    bad = []
    for name, why, ours in probs:
        print(f"[plugins] {'REPO' if ours else 'contrib'}  plugins/{name}: "
              f"{why}", file=out)
        bad.append(name)
    if want - got:
        print(f"[plugins] MISSING  declared but not discovered: "
              f"{', '.join(sorted(want - got))}", file=out)
    if not got:
        print("[plugins] EMPTY    no plugin was discovered at all", file=out)
    ok = not bad and not (want - got) and got
    print(f"[plugins] VERDICT: {'PASSED' if ok else 'FAILED'} -- "
          f"{len(got)} discovered ({', '.join(sorted(got)) or 'none'}), "
          f"{len(want)} declared and present, {len(bad)} swallowed", file=out)
    return 0 if ok else 1


def detect(root: Path, exists: Optional[Callable[[str], bool]] = None):
    """The most confident plugin for an install, or GENERIC.

    Only for installs whose kind was never declared: a stored declaration
    is the user's and outranks any heuristic.
    """
    ranked = rank(root, exists)
    return ranked[0][0] if ranked else GENERIC
