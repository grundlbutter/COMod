#!/usr/bin/env python3
r"""
cco -- Classic Conquer 2.0, the install this project started from.

**This is not legacy support. It is the reference.** Two answers the app
relies on every day exist only because a CCO install is on hand, and neither
can be re-derived from any official client:

* ``attach.WEAPON_MOTION_SET`` -- which body-motion folder each weapon set
  animates from -- was read out of CCO's ``ini/3dmotion.ini``. 5517 and 6090
  ship **none** of those rows in either the stale ini or the compiled twin,
  and the ``.dbc`` rebuilds its keys from paths, so an alias cannot survive
  there even in principle. Without it every armed character is posed
  empty-handed, and the lookup's own fallback chain *reports success*.
* The **female ``v_l_weapon`` orientation**. The official lineage rewrote
  that one dummy's track on body shapes 001 and 002; CCO is a clean unit
  rotation on all four. ``Patch6090.socket_correction`` borrows CCO's 3x3
  (``reference-basis:cco``) and degrades to ``unit-rows`` when no CCO install
  is declared.

Both are declared in `provides_reference` rather than left implicit, so a
borrower's ``reference-basis:cco`` has something on the far side that agreed
to be borrowed from.

WHAT WAS MEASURED, AND AGAINST WHAT
-----------------------------------
Everything below was measured on 2026-08-09 against the CCO 2.0 install
declared to `coroot.declare_kind` as ``cco`` -- resolve it through
`coroot`, never by listing a clients tree, because a declared root can be an
empty directory and measuring one silently measures nothing -- **and re-run on
both official clients** (5517 and 6090). That is deliberate rather than
thorough: a session that used CCO as its sole reference had all five of the
tests it cleared turn out to be 6090 pins, because a fact checked against one
other client cannot
tell "CCO is different" from "the other client is different"
(``docs/CORRECTIONS.md`` §3). Two bases are not enough to classify a pin.

Where a number here came from one install alone it says so.

**And the cross-check is not symmetric.** CCO ships the *interleaved*
``PHY MOTI PHY MOTI`` chunk layout where pairing by adjacency is accidentally
correct, and the official clients ship *blocked* ``PHY PHY MOTI MOTI`` where
it is not -- which is what made the render-matrix "socket basis disagreement"
look like a real defect for weeks (``docs/CORRECTIONS.md`` C16). So CCO is
the single place in this codebase where **false agreement** is most likely:
anything touching chunk order, socket pairing or motion binding can agree
here for a reason that has nothing to do with the fact being tested. Every
socket claim below was taken through `parts.socket_anchors`, which pairs by
**ordinal** on both layouts, and the layout difference itself is stated as a
quirk rather than left to bite the next reader.

HOW CCO DIFFERS FROM THE OFFICIAL LINEAGE
-----------------------------------------
`Patch6090.QUIRKS` is six form differences stated from the 6090 side. Most of
them are a CCO fact seen in a mirror, and `QUIRKS` below states them in the
positive. The short version, all MEASURED over ``ini/``:

                            CCO        5517       6090
    files in ini/            75        176        250
    compiled .dbc tables      0         14         15
    .json tables             25          0          0
    TQ-cipher .dat            0          2          2      (itemtype, Monster)
    version stamp      version.json   version.dat  version.dat

**Nothing ships twice.** There is no compiled twin anywhere, so the stale-ini
decoy trap -- the single most expensive quirk in the official plugin -- does
not exist here. Every plaintext table CCO ships is the live one, which is why
`prefers_compiled_tables` is False as a statement rather than as a default.

**The archives are CCO's own.** All five official patches ship byte-identical
``c3.wdf``/``data.wdf``, so `Patch6090.import_plan` sets ``sharedArchives``.
CCO's are different files -- ``c3.wdf`` 534,811,690 bytes (md5 8679cd57b714)
against the official 359,069,116 (1c16683437bc) -- so that optimisation is
**wrong here** and `import_plan` says so.

WHAT IS STILL OPEN
------------------
* **No visual scan has been done on this base.** `monster_colourways` and
  `entity_name_overrides` are therefore empty rather than inherited: 6090's
  sets were authored by a person looking at 36 monster directories in *that*
  client over *those* archives, and CCO's archives are not those archives.
  `colour_provenance` keeps the weak default. This is the same call
  `patch5517` made, for a stronger reason -- 5517 at least shares the art.
* **437 of 437 NPC plans resolve, but 175 of them name an asset the install
  does not ship** (518 of 2,185 logical paths unreadable). "Resolves" is a
  statement about the tables, not about the art, and the old `notes` string
  read as though it were both.
* `RolePart.ini` names ``ini/shield.ini``, ``ini/pelvis.ini`` and
  ``ini/armetmotion.ini``; CCO ships none of the three. Declarations again,
  not content.
* Whether the real CCO client draws the female left-hand weapon the way ours
  does is untested on screen here. The *6090* version of that question is
  ``docs/handoff_5517_base_prep.md`` §8.3 and is the one that matters; CCO's
  track being clean is what makes it answerable at all.
* `aura_convention` returns "table" and that is correct for CCO, but the
  contrast it is usually quoted against is **not** what it looks like -- see
  the hook, and ``docs/CORRECTIONS.md`` C35.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                            # noqa: E402
from plugins.catalog import (                         # noqa: E402
    TableSpec, KIND_JSON_ROWS)

#: **CCO's content is JSON, and the first census of this client missed it
#: entirely.**  Globbing `ini/*.dat` and `ini/*.ini` finds 2 and 48
#: respectively, no item table, no monster table, no npc table -- and that
#: census was one sentence away from reporting that Classic Conquer 2.0 ships
#: no content tables at all.  It ships six, as JSON.  An instrument that
#: cannot see a format reports its absence in the same words as a client that
#: genuinely does not have it.
#:
#: What is deliberately NOT here: `armor.ini`, `armet.ini`, `weapon.ini`,
#: `misc.ini`, `head.ini` and `mount.ini`.  Those are the APPEARANCE tables and
#: `comod tables` already lists them through `AssetRoot.part_tables()` --
#: 3,418 body, 5,384 weapon, 2,918 armet and so on.  Declaring them here too
#: would put one table on two surfaces with two counts, which is how the same
#: file comes to be quoted at two different numbers.
#:
#: `magictype.json` has NO unique id: 610 rows over 289 distinct `MagicType`
#: values, because a skill carries one row per level.  It is declared with
#: `MagicType` as the id anyway and its count is ROWS -- deduplicating would
#: report 289 and silently lose more than half the table.
SPECS_CCO = (
    TableSpec("item", "itemtype.json", KIND_JSON_ROWS, "json",
              source="ini/itemtype.json", id_key="id", label_key="name"),
    TableSpec("npc", "npc.json", KIND_JSON_ROWS, "json",
              source="ini/npc.json", id_key="type", label_key="name"),
    TableSpec("npc:terrain", "TerrainNpc.json", KIND_JSON_ROWS, "json",
              source="ini/TerrainNpc.json", id_key="type", label_key="name"),
    TableSpec("monster", "monster.json", KIND_JSON_ROWS, "json",
              source="ini/monster.json", id_key="type", label_key="name"),
    TableSpec("magic", "magictype.json", KIND_JSON_ROWS, "json",
              source="ini/magictype.json", id_key="MagicType",
              label_key="Name"),
    TableSpec("levexp:weaponskill", "WeaponSkillLevelExp.json",
              KIND_JSON_ROWS, "json",
              source="ini/WeaponSkillLevelExp.json", id_key="level",
              label_key="exp"),
)

#: **CCO ships no `GameMap.dat`.** It has exactly two `.dat` in `ini/` --
#: `cache.dat` and `LevelExp.dat` -- so the one binary table that reads
#: identically on all seven OFFICIAL builds is simply absent here. Declaring
#: it would produce a permanent "is not present" row, which is the inheritance
#: error this whole per-build census exists to avoid: a spec should say what a
#: client HAS.


class ClassicConquer(Plugin):
    name = "cco"
    label = "Classic Conquer 2.0"
    #: A private server's own client, not a TQ release -- it is
    #: installed locally, which is a different claim from official.
    origin = "server"
    aliases = ("classic", "cco2")

    def table_specs(self, root):
        return SPECS_CCO
    notes = (
        "Plaintext-only tables -- JSON entity tables (npc/monster/itemtype), "
        "ini appearance tables, no compiled twin anywhere -- nine-wide "
        "zero-padded appearance ids, three-wide Action3DEffect key fields, "
        "motion ids that name their file directly, and one appearance row "
        "per colourway rather than a colour index. MEASURED here and "
        "cross-checked on 5517 and 6090. Its own archives, not the official "
        "shared pair. THE LINEAGE'S ONLY GROUND TRUTH for the per-weapon-set "
        "motion aliases and for the female v_l_weapon orientation; both are "
        "declared in provides_reference(). No visual scan has been done on "
        "this base, so no monster colour sets or name pins are claimed."
    )

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        r"""``ini/npc.json`` is the tell, and the absence of a compiled table
        is what stops it claiming an official client that also ships JSON.

        MEASURED on all three installs: ``ini/npc.json`` exists on CCO and on
        neither official client; ``ini/3DSimpleObj.dbc`` exists on both
        official clients and not here. Each probe alone is a single-sided
        test -- a private-server repack could ship both -- so the claim is
        the conjunction, and it is deliberately capped below the 0.95 the
        version-stamped official plugins claim: a repack *of CCO* should be
        able to outbid this with its own marker.

        ``version.json`` (CCO) against ``version.dat`` (every official patch)
        is a third, independent discriminator. It is not read here because it
        is not needed to separate these two families and an unread file
        cannot go stale; it is recorded because it is the cheapest thing to
        reach for if a fourth client ever muddies the first two.
        """
        if exists("ini/npc.json") and not exists("ini/3DSimpleObj.dbc"):
            return 0.85
        return 0.0

    # -- tables ------------------------------------------------------------
    def table_profile(self):
        import npcart
        return npcart.PROFILE_CCO

    def prefers_compiled_tables(self) -> bool:
        """False, and as a measurement rather than as the default: CCO ships
        **zero** ``.dbc`` files in ``ini/`` (against 14 in 5517 and 15 in
        6090), so there is no twin to prefer and no stale decoy to avoid.
        Every plaintext table here is the live one."""
        return False

    # -- art resolution ----------------------------------------------------
    _NPC_DIR_MESH = re.compile(r"^(c3/npc/\d{3}/\d+)\.c3$", re.I)

    def texture_for_mesh(self, mesh, exists):
        r"""A directory-layout NPC skins from **beside itself**:
        ``c3/npc/013/1.c3`` -> ``c3/npc/013/1.dds``.

        This is the exact inverse of the official convention, and the
        measurement is clean in both directions. Over every
        ``c3/npc/<ddd>/<n>.c3`` mesh named by each install's object table:

                                       CCO      5517     6090
            meshes in that layout       33        34       98
            texture beside the mesh     26         0        0
            `999<dir>0` family           4        23       87

        So the official ``999<dir>0`` rule -- which is what
        `Patch6090.texture_for_mesh` declares, and which resolves for 87 of
        6090's 98 -- answers only 4 of CCO's 33, while the beside-the-mesh
        texture that CCO uses 26 times **does not exist at all** on either
        official client. Applying 6090's rule here would be the exact failure
        the plugin system exists to prevent, and it fails silently: the wrong
        texture is still a texture.

        INFERRED, not authored: this is a layout convention measured on disk,
        not a row in a shipped table. CCO's object table already answers for
        most of these; this is the fallback for the ones it misses.
        """
        m = self._NPC_DIR_MESH.match((mesh or "").replace("\\", "/"))
        if m:
            cand = m.group(1) + ".dds"
            if exists(cand):
                return cand, "npc dir texture (beside the mesh)", "inferred"
        return None

    def colourways(self, texture, exists):
        r"""None, and the reason is structural rather than a shrug.

        **CCO has no colour index.** It ships no ``ItemTexture.ini`` at all,
        where 5517 ships 1,466 sections of one and 6090 ships 1,966 -- the
        table that maps ``items.Color`` onto a texture id (``docs/CORRECTIONS``
        C20). Colour here is a whole appearance row. MEASURED on ``armor.ini``:
        ``002135000`` **is not a section**; ``002135300``, ``002135400`` and
        ``002135500`` are, each carrying ``Mesh0=002135000`` and its own
        ``Texture0``.

        So "every appearance sharing this mesh" -- which the app can compute
        from the shipped table without help -- already *is* the colour set,
        and a digit convention laid over the top would invent siblings. The
        official plugin needs `colourways` precisely because 6090 folded the
        variants into one row plus an index; CCO never did.
        """
        return ()

    def monster_colourways(self, ident):
        """None: **no visual scan has been done on this base.**

        6090's sets could be copied -- `patch5517` copies them -- but 5517
        shares 6090's archives byte for byte and CCO does not
        (``c3.wdf`` md5 8679cd57b714 against 1c16683437bc), so the art behind
        an inherited set is not even the same file. An inherited set that
        resolves is worth having; an inherited set over different art is a
        confident wrong answer, which is the thing this project keeps paying
        for. Nothing until someone looks.
        """
        return None

    def colour_provenance(self):
        """The base class's weak default, restated so it is a decision.

        `default_colour` still returns something useful, and the app should
        still use it -- it is just not a measurement, and this hook exists so
        that saying so costs one line instead of a session
        (``docs/parser_plugins.md``, "a subclass does not inherit its
        parent's evidence").
        """
        return "colourway (default, unverified on this base)", "inferred"

    _FLAT_STEM = "c3/npc/999{g}.c3"

    def flat_family_base(self, group, paths, has_geometry):
        r"""The short-stem sibling is the family's one real body, here too.

        MEASURED on CCO, and identical on both official clients -- this is an
        archive fact, and the one place in this file where CCO and the
        official lineage genuinely agree on something worth declaring:

            look 118   999118.c3  1,181 verts   999118100.c3  3 verts
            look 256   999256.c3    838 verts   999256100.c3  4 verts

        The numbered files are motion sets whose embedded geometry is a
        shard; the app binds them over the short stem. Declared rather than
        inherited from `Patch6090` because a plugin's claim should rest on
        its own reading of its own install, and this one does.

        `Patch6090` also tries ``999<g>0.c3``. That stem exists on **no**
        install of the three, so it is not repeated here.
        """
        cand = self._FLAT_STEM.format(g=group)
        if cand in paths and has_geometry(cand):
            return cand
        return None

    # -- attachment --------------------------------------------------------
    #: MEASURED over every distinct character body named by ``armor.ini``
    #: (680 meshes, all readable): there are exactly **two** socket-set
    #: shapes, and they are the same two the official clients ship --
    #:
    #:     677  v_armet + v_body   + v_l_weapon + v_r_weapon
    #:       3  v_armet + v_body01 + v_l_weapon + v_r_weapon
    #:
    #: against 571/0 on 5517 and 892/2 on 6090. **No character body in any of
    #: the three carries anything else.** So the socket set is not where the
    #: clients differ, and a plugin claiming otherwise for CCO would be
    #: reading the declaration instead of the art.
    BODY_SOCKETS = ("v_body", "v_armet", "v_l_weapon", "v_r_weapon")

    #: Slot -> socket. "" means "this client cannot attach that slot".
    #:
    #: This map ends up matching `Patch6090.SLOT_SOCKETS`, and that is a
    #: finding rather than a copy: it was derived from the census above, on
    #: this install, and CCO's own `RolePart.ini` would have predicted
    #: something much richer. See `sockets_present`.
    SLOT_SOCKETS = {
        "body": None, "mix_body": None,
        "armet": "v_armet", "mix_armet": "v_armet",
        "armet_dx8": "v_armet", "mix_armet_dx8": "v_armet",
        "r_weapon": "v_r_weapon",
        "l_weapon": "v_l_weapon",
        # No body carries v_l_shield, and CCO ships no shield.ini for the
        # slot to draw from either, so the left hand is where a shield would
        # go. Same answer as 6090, reached the same way.
        "shield": "v_l_weapon",
        # INVERTED, and CCO ships the richer rig 6090 does too: the one
        # resolvable mount mesh carries v_mount, v_l_shield, v_r_shield,
        # v_armet, v_l_arm, v_r_arm and v_pet. The rider is placed onto the
        # mount, not the mount onto the rider.
        "mount": "v_mount",
        # DECLARED but ABSENT, and this is the interesting half. CCO's
        # RolePart.ini lists 52 dummies -- v_head, v_misc, v_pelvis, v_back,
        # v_pet, v_mantle and 46 more -- and 13 parts including `head`
        # (ini/head.ini, 4 rows) and `misc` (ini/misc.ini, 272 rows). Not one
        # of the 680 character bodies carries v_head, v_misc or v_pelvis. The
        # tables are real; the sockets are not. Declared unattachable rather
        # than dropped at the body origin, per the hook's own contract.
        "misc": "",
        "head": "",
        "pelvis": "",
    }

    def slot_socket(self, slot):
        return self.SLOT_SOCKETS.get(slot)

    def socket_correction(self, socket, body_appearance):
        r"""**None, on every socket, and that is the load-bearing answer.**

        This is the hook that says "what the client ships is wrong". CCO is
        the client the others are corrected *towards*, so it returning None
        is not an absence of opinion -- it is the claim the correction rests
        on, and it is measured. Shortest basis row of each dummy's track over
        every frame of motion set 410, re-run today on all three installs
        through `parts.socket_anchors`:

                                CCO     5517    6090
            001 female  idle   1.000    0.158   0.158
            001 female  swing  1.000    0.020   0.020
            002 female  idle   1.000    0.111   0.111
            002 female  swing  1.000    0.012   0.012
            003 male    both   1.000    1.000   1.000
            004 male    both   1.000    1.000   1.000

        ``v_r_weapon`` and ``v_armet`` are 1.000 on every shape, action and
        client. So the collapse is one dummy on the two female shapes in the
        official lineage, CCO is clean everywhere, and 5517 and 6090 are each
        other's copy -- which is why **CCO is the only contrast in the lineage
        for weapon placement** and why comparing the two official clients on
        this question can only ever show agreement.

        Should a future pass find a socket CCO genuinely ships broken, the
        correction goes here and is applied in `coviewer` and nowhere else.
        `tools/attach.py` and `tools/parts.py` stay a faithful read -- they
        are what `client/` and any engine port consume, and
        ``test_viewer.SocketCorrectionIsViewerOnly`` is the fence.
        """
        return None

    def sockets_present(self):
        r"""What the meshes carry, against what ``RolePart.ini`` declares.

        This client is the sharpest example in the repo of why the two are
        different questions. ``RolePart.ini`` here declares **13 parts and 52
        dummies** -- against 6090's 8 and 7 -- and names three table files
        (``shield.ini``, ``pelvis.ini``, ``armetmotion.ini``) that the install
        does not contain. Read as an inventory it promises a rig with
        shoulders, flaps, feet, forearms, a mantle and three weapon sockets.
        The art ships four chunks.
        """
        return {
            "v_body": "on 677 of 680 character bodies (v_body01 on the "
                      "other 3) -- the frame everything else is placed in",
            "v_armet": "on all 680 character bodies -- headgear",
            "v_l_weapon": "on all 680 -- left hand, and where a shield would "
                          "go. THE REFERENCE TRACK: unit rotation on all four "
                          "body shapes, where 5517 and 6090 collapse it to "
                          "0.012 on the female ones",
            "v_r_weapon": "on all 680 -- right hand",
            "v_mount": "on the MOUNT mesh, not the body -- the rider is "
                       "placed onto it, alongside v_l_shield, v_r_shield, "
                       "v_armet, v_l_arm, v_r_arm and v_pet",
            "v_head": "DECLARED in RolePart.ini and ABSENT from every "
                      "character body, although ini/head.ini ships 4 rows",
            "v_misc": "DECLARED and ABSENT from every character body, "
                      "although ini/misc.ini ships 272 rows",
            "v_pelvis": "DECLARED and ABSENT; ini/pelvis.ini is not shipped "
                        "either, so the slot has no table and no socket",
            "v_l_shield": "absent from bodies; present on the mount mesh",
        }

    # -- what other plugins may borrow -------------------------------------
    def provides_reference(self) -> dict:
        r"""The two things only a CCO install can answer.

        `Patch6090.socket_correction` returns ``"reference-basis:cco"`` and
        `attach.WEAPON_MOTION_SET` is 122 literals with a comment naming this
        client. Both were, until now, one-sided: nothing on this side had
        agreed to be the source, so renaming or retiring this plugin would
        have degraded the viewer to ``unit-rows`` and left the armed poses to
        a fallback that reports success.
        """
        return {
            "weapon-motion-aliases":
                "`attach.WEAPON_MOTION_SET`, 122 entries, read out of this "
                "client's ini/3dmotion.ini. MEASURED: CCO keys 123 weapon "
                "sets onto 12 body-motion folders (716 rows for set 480 "
                "alone), where 5517 keys 13 sets onto 13 folders and 6090 "
                "keys 23 onto 23 -- one for one, which is no aliasing at "
                "all. Both key ZERO rows for set 480, and the "
                ".dbc rebuilds keys from paths so an alias cannot survive "
                "there even in principle. Every one of the 122 baked entries "
                "re-derives from this install by the documented rule -- see "
                "`derive_weapon_motion_aliases`. Without them an armed "
                "character is posed empty-handed AND THE LOOKUP REPORTS "
                "SUCCESS.",
            "female-v_l_weapon-basis":
                "The 3x3 `Patch6090.socket_correction` borrows for body "
                "shapes 001 and 002 (`reference-basis:cco`). MEASURED: unit "
                "rotation on all four shapes here, collapsing to 0.158/0.111 "
                "at idle and 0.020/0.012 mid-swing on the female shapes in "
                "BOTH official clients. Borrowing is legitimate only because "
                "the socket's translation is identical between the clients, "
                "so the hand does not move; that is asserted by "
                "`SocketCorrectionIsViewerOnly` against the live installs. "
                "A VIEWER DEVIATION on the borrower's side, never a fix.",
        }

    #: The rule that produced `attach.WEAPON_MOTION_SET`, kept as code so the
    #: 122 literals are *reproducible* rather than merely sourced. See
    #: `derive_weapon_motion_aliases`.
    ALIAS_KEY = re.compile(r"^c3/(\d{4})/(\d{3})/(\d{1,3})\.c3$", re.I)

    def derive_weapon_motion_aliases(self, root) -> dict:
        r"""Re-derive the weapon-set -> motion-folder map from a CCO install.

        The provenance of `attach.WEAPON_MOTION_SET` was a sentence in a
        handoff and 122 literals in another module. A sentence cannot be
        re-run; this can. The rule, stated once and executed rather than
        described: read ``ini/3dmotion.ini``, keep the keys of the form
        ``<shape><set><action>`` whose value is a body motion
        ``c3/000<shape>/<folder>/<action>.c3``, and take the **dominant
        non-000 folder** per weapon set.

        Returns ``{}`` for any install that is not CCO rather than raising --
        an official client legitimately has nothing to say here, which is the
        whole point.

        Verified: on this install it reproduces all 122 baked entries exactly,
        with set ``000`` correctly excluded (it maps to itself).
        """
        from collections import Counter, defaultdict

        per: dict = defaultdict(Counter)
        p = Path(root) / "ini" / "3dmotion.ini"
        if not p.is_file():
            return {}
        for line in p.read_text("latin-1", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if not key.isdigit() or len(key) < 7:
                continue
            if key[:-6] not in ("1", "2", "3", "4"):
                continue
            m = self.ALIAS_KEY.match(val.strip().replace("\\", "/").lower())
            if m:
                per[key[-6:-3]][m.group(2)] += 1
        out = {}
        for ws, folders in per.items():
            nz = [(f, n) for f, n in folders.items() if f != "000"]
            if nz:
                out[ws] = max(nz, key=lambda t: t[1])[0]
        return out

    # -- table formats -----------------------------------------------------
    #: `Patch6090.QUIRKS`, inverted and measured from this side. Six of the
    #: seven are the positive form of a 6090 entry; each says what it looks
    #: like when someone brings the official assumption here.
    QUIRKS = {
        "chunks are INTERLEAVED, which makes a broken reader look right":
            "The most dangerous entry in this file, because it does not "
            "produce a wrong answer HERE -- it produces a wrong answer "
            "somewhere else and blames this client. MEASURED on the one "
            "logical path all three installs share, c3/0002/410/100.c3, and "
            "on the body c3/mesh/002135000.c3:\n"
            "    CCO   body   PHY MOTI CAME PHY MOTI PHY MOTI PHY MOTI\n"
            "    CCO   motion PHY MOTI CAME PHY MOTI PHY MOTI PHY MOTI\n"
            "    5517  body   PHY PHY PHY PHY  MOTI MOTI MOTI MOTI\n"
            "    5517  motion MOTI MOTI MOTI MOTI      (no PHY, NO NAMES)\n"
            "    6090  identical to 5517 in both files.\n"
            "CCO interleaves, so pairing a MOTI with the PHY that happens to "
            "precede it gives the same answer as pairing by ordinal. The "
            "official clients block them, so it does not: the first MOTI "
            "pairs with the LAST PHY name. That is C16 -- a test pairing by "
            "adjacency was green on CCO, red on both official clients, and "
            "was read as a base-dependent code defect for weeks when it was "
            "the test. Third sighting of the same split "
            "(handoff_community_update sec 3 hit it in the garment "
            "archives).\n"
            "Two consequences. (1) An official body-motion file carries MOTI "
            "tracks and NOTHING ELSE -- no PHY, no names -- so socket names "
            "must come from the body mesh and bind to motion tracks by "
            "ORDINAL. CCO's motion files name their own tracks, so a reader "
            "that looks the name up in the motion file works here and finds "
            "nothing there. (2) CCO agreeing with an official client about "
            "anything involving chunk order is not corroboration. "
            "attach.PartMesh.parse and parts.socket_anchors both pair by "
            "ordinal and are correct on both layouts; measure through them.\n"
            "NOT a clean split by client, and the imprecise form of this "
            "claim is itself a trap: over all character bodies CCO is 261 "
            "interleaved / 419 blocked, 5517 is 39/532 and 6090 is 75/819. "
            "It is a strong tendency in the meshes and an INVARIANT in the "
            "16 body-motion files sampled (CCO 16/16 interleaved, both "
            "official clients 16/16 motion-only).",
        "nothing ships twice":
            "There is no compiled twin of anything: 0 .dbc files in ini/, "
            "against 14 in 5517 and 15 in 6090. Every plaintext table CCO "
            "ships is the live one, so the stale-ini decoy that cost the "
            "6090 work six sessions cannot happen here -- and the reverse "
            "trap can: code that reaches for a .dbc and treats its absence "
            "as an empty client will find no tables at all. "
            "`prefers_compiled_tables` is False as a statement.",
        "the entity tables are JSON, plaintext and current":
            "npc.json (437 rows), monster.json (374) and itemtype.json "
            "(11,142) are UTF-8 JSON arrays, not sectioned ini and not "
            "encrypted. The official clients ship none of the three and put "
            "the same content in TQ File Cipher .dat tables at seed 9527 "
            "(itemtype.dat, Monster.dat), which CCO does not ship at all. So "
            "core/tqdat.py has nothing to do here, and an importer that "
            "requires it will read this client as having no items.",
        "ids are nine wide and zero padded, everywhere":
            "armor.ini's sections are 9 characters to the last row; the "
            "compiled tables store the same numbers as integers, so CCO's "
            "002135000 is 2135000 in 6090. Comparing the two spellings "
            "literally shares zero rows and normalised shares thousands -- "
            "the same trap C20 recorded for ItemTexture.ini against "
            "armor.ini WITHIN one client. Every consumer's bodyType "
            "arithmetic slices the nine-wide form, and the mesh filenames "
            "kept their zeros, so this is the form to normalise TO.",
        "Action3DEffect key fields are three wide":
            "MEASURED: 8,926 of 8,926 rows key (3,3,3,3) -- "
            "999.100.135.293. 6090 widened the action field to four, "
            "zero-padded (999.0100.130.300) on 10,113 of its 10,267 rows, "
            "and 5517 on 10,556 of 10,616. A literal string compare across "
            "the two misses every non-wildcard row and silences every weapon "
            "and body effect at once, with no error. effects.EffectDB "
            "compares numerically where both sides are numeric, which is "
            "what makes one reader work on both -- but see `aura_convention` "
            "for the place that fix did not reach.",
        "motion ids ARE filenames":
            "999001100 is c3/npc/999001100.c3, with no lookup and no table. "
            "The official clients key the same ids through 3dmotion.dbc by "
            "their low 32 bits, because the client atoi's into a u32, and "
            "the row then resolves to the old nine-digit filename -- a "
            "renaming shim over an archive namespace that never changed. "
            "There is no dbc here and no wrap; a reader that applies the u32 "
            "fold to a CCO id corrupts a key that was already correct.",
        "the per-weapon-set motion aliases are HERE, and only here":
            "CCO's 3dmotion.ini spells out which motion folder each weapon "
            "set animates from, one key at a time -- 1480100 = "
            "c3/0001/410/100.c3, 716 rows for set 480 alone. The cleanest "
            "way to see that it is an ALIAS table is to count both sides:\n"
            "                     weapon sets keyed   folders that exist\n"
            "    CCO                    123                  12\n"
            "    5517                    13                  13\n"
            "    6090                    23                  23\n"
            "CCO maps 123 sets onto 12 folders; each official client keys "
            "exactly the sets that ARE folders, one for one, which is the "
            "same as keying no aliases at all. Both key ZERO rows for set "
            "480, and their .dbc rebuilds keys from paths, so an alias could "
            "not survive there even in principle. This is the source of "
            "attach.WEAPON_MOTION_SET; `derive_weapon_motion_aliases` re-runs "
            "the derivation against a live install.",
        "colour is a whole appearance row, and there is no colour index":
            "002135000 is not a section; 002135300/400/500 are, each with "
            "Mesh0=002135000 and its own Texture0. CCO ships no "
            "ItemTexture.ini at all, where 5517 ships 1,466 sections and "
            "6090 ships 1,966. So the items.Color -> ItemTexture -> texture "
            "chain of C20 has no CCO equivalent, and a digit convention laid "
            "over these ids would invent siblings the table already "
            "enumerates. `colourways` is empty for that reason.",
        "RolePart.ini promises a rig the art does not ship":
            "13 parts and 52 dummies declared -- v_head, v_misc, v_pelvis, "
            "v_back, v_mantle, shoulders, flaps, feet, forearms -- and three "
            "of the part tables it names (shield.ini, pelvis.ini, "
            "armetmotion.ini) are not in the install. MEASURED over all 680 "
            "character bodies: every one carries exactly v_body (or "
            "v_body01) + v_armet + v_l_weapon + v_r_weapon and nothing else, "
            "which is the same four the official clients ship. Read the "
            "declaration as an inventory and you offer the user sockets no "
            "body has. See `sockets_present`.",
    }

    #: Observed key-field widths. Declared so a padding mismatch is
    #: recognisable as one rather than read as missing content.
    FIELD_WIDTHS = {
        "Action3DEffect.ini": {"shape": 3, "action": 3, "type": 3, "sub": 3},
    }

    def table_quirks(self):
        return dict(self.QUIRKS)

    def key_field_widths(self):
        return {k: dict(v) for k, v in self.FIELD_WIDTHS.items()}

    def aura_convention(self) -> str:
        r""""table": an always-on ``Action3DEffect`` row points at the effect.

        MEASURED: 826 rows of the form ``999.999.410.009=410009`` -- shape
        wildcard, action wildcard, and a value naming a ``3DEffect.ini``
        section. The row's only job is to say the effect exists.

        **The contrast this used to be quoted against was wrong, and the
        correction matters more than the value.** `Patch6090.aura_convention`
        returned "effect-named-for-id" on the stated grounds that 6090 "ships
        ZERO always-on Action3DEffect rows". It does not: 6090 ships 2,604 and
        5517 ships 972, spelled ``999.9999.410.009=410009``. The action field
        is the **wildcard**, and it widened from three nines to four along
        with every other action field -- which is 6090's own "four-wide action
        fields" quirk, in the one place the numeric-compare fix did not reach:
        ``superfx.AURA_ACTION`` is the literal ``"999"`` and
        ``EffectDB._field_matches("9999", "999")`` is False, because 9999 and
        999 are different *numbers* rather than different paddings.

        Reproduced on both official clients:

            lookup_action_effect("410199", "999")  -> None  None   (5517/6090)
            lookup_action_effect("410199", "9999") -> the effect, both

        and on CCO both spellings resolve, because here the wildcard is 999.
        The effect-named-for-id fallback then made 6090 look correct from the
        outside while the table half was dark. So "table" is right for CCO for
        its own reason, and the *difference* between the clients was narrower
        than the two return values suggested.

        **SETTLED since this was written.** Recorded as ``C35`` in
        ``docs/CORRECTIONS.md`` and fixed there, on the 6090 base, by whoever
        owned it -- but **not the way this docstring proposed**. Widening the
        wildcard was measured and REFUTED (it changes tens of thousands of
        answers on the official clients and 0 on CCO, and overwrites answers
        that were already right). The fix was ``effects.is_always_on``, which
        removes the sentinel from the callers instead. ``WILDCARD`` is still
        ``"999"``. All three clients now report "table", which is what they
        all do; the paragraphs above are kept because the measurement is still
        the evidence, not because the contrast is still live.
        """
        return "table"

    # -- naming ------------------------------------------------------------
    def entity_name_overrides(self):
        """Nothing pinned and nothing dropped, for the same reason
        `monster_colourways` is empty: no one has done a visual scan on this
        base. 6090's pins came from a person naming creatures in that client;
        CCO's entity tables are a different file over different archives, and
        a name pinned from the wrong client is exactly the "names seven years
        stale" failure the plugin system was built for."""
        return {}, set()

    # -- library import ----------------------------------------------------
    def import_plan(self, root, exists) -> dict:
        r"""The official archive optimisation is **wrong here**, measured.

        `Patch6090.import_plan` sets ``sharedArchives`` because all five
        official patches ship byte-identical ``c3.wdf``/``data.wdf``, so
        hashing 750 MB per client is 25,013 redundant reads. CCO ships the
        same two filenames and they are **not** those files:

                        CCO                             official
            c3.wdf      534,811,690  8679cd57b714       359,069,116  1c16683437bc
            data.wdf    383,863,672  92d41816ea3d       392,245,257  e43f4d5af290

        Inheriting the flag would skip the only copy of CCO's art, and the
        symptom would be a catalogue that quietly omits every archived asset
        rather than an error -- the same shape as the DatPkg baseline bug in
        ``docs/handoff_5517_base_prep.md`` §8.4.5. Stated as False rather than
        omitted, so it reads as a decision.
        """
        plan = super().import_plan(root, exists)
        plan.update({
            "sharedArchives": False,
            "tables": ["ini/"],
            "note": "Classic Conquer 2.0: own archives (NOT the official "
                    "shared pair), loose layer imported, plaintext ini and "
                    "JSON tables read -- no compiled twin exists",
        })
        return plan


PLUGIN = ClassicConquer()
