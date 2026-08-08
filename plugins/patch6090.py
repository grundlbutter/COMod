#!/usr/bin/env python3
r"""
patch6090 -- the official Conquer Online patch 6090 client.

Everything here was measured against
``C:\Claude\ConquerAssets\Clients\6090`` and confirmed on screen. Where a
claim is an inference it says so; where a value came from a person looking
at the model and saying "that is not a ThunderApe", it says that too,
because that is the strongest evidence this project has.

WHAT MAKES 6090 DIFFERENT
-------------------------
**Every table ships twice.** The plaintext ``.ini`` files everyone knows are
stamped 2008-09 and were never updated; beside each one is a compiled twin
stamped 2015 that the client actually reads. The decoys parse cleanly, which
is what makes them dangerous: `armor.ini` offers 955 sections where
`armor.dbc` has 3,326, and reading the ini dressed the character builder in
seven-year-old data. Formats, all in `core/dbc.py`:

    RSDB   id -> path rows      3DObj, 3DTexture, 3DEffectobj, 3dmotion
    SIMO   simple objects       3DSimpleObj
    MESH   appearance records   armor, armet, weapon, head, pelvis, misc

**Two tables are encrypted.** ``itemtype.dat`` and ``Monster.dat`` are TQ
File Cipher, seed 9527 (`core/tqdat.py`). Without them the builder has no
item names and monsters have no rows. ``Server.dat`` is RSA and stays shut.

**Ids lost their padding.** The compiled tables store section numbers as
integers, so CCO's ``002135000`` is ``2135000`` here. Body-typed tables get
the nine-wide spelling restored on the way out, because every consumer's
bodyType arithmetic slices that form and the mesh files kept their zeros.

**Motion ids overflow.** ``npc.ini`` names ten-digit motion ids and
``3dmotion.dbc`` keys them by their low 32 bits -- the client atoi's into a
u32 and the table was built to match. The row then resolves to the *old*
nine-digit filename: the table is a renaming shim over an archive namespace
that never changed, which is consistent with all five official patch clients
shipping byte-identical ``c3.wdf``.

**Colour is in the id, and where depends on the family.** Monster and NPC
bodies vary the leading digit, equipment the tens digit, mounts the middle
digits. The leading-digit rule over-reaches badly -- ``203000000`` reads as
monster 103's second colour when it is monster 203's own skin -- so monster
sets are pinned from a visual scan rather than derived.

WHAT IS STILL OPEN
------------------
* Monster 109: neither the 108 family nor its own suits that mesh. Unpinned.
* Monsters 301, 218: dump joins name them "Shopkeeper" and duplicate 201's
  art; dropped to numeric until someone identifies them.
* Monster 207: two of three family neighbours (607, 907) unclaimed.
* ``npc_simple:217`` ships two motion-only files and no geometry anywhere --
  absent content, not a resolution failure.
* The stride-12 extra field in ``3dmotion.dbc`` (0, 2, 13634) is unread.
* **Left-hand weapon placement.** v_l_weapon's track differs from CCO's
  in orientation, not just scale, while v_r_weapon's is bit-identical --
  see the quirk. Right-hand placement is provably correct (the transform
  equals CCO's exactly); left-hand cannot be fixed by normalising. Either
  the engine derives the left socket some other way in 6090, or these
  tracks are stale data the client no longer reads. Read Role3D before
  guessing again.
* **Weapon placement on body type 002.** Its ``v_l_weapon`` dummy is
  authored with scale -- rows (0.111, 0.149, 0.994) -- where body type
  001's is a clean unit rotation, and **the same values appear in CCO**,
  so this is per-body authoring and not a 6090 difference at all. Three
  attempts to normalise it away each traded one artefact for another
  (see the expectedFailure test for the measurements): removing the
  scale renders a 120-unit sword, keeping it renders a 12-unit sliver.
  The open question is what the engine does with a scaled dummy, and it
  should be answered by reading Role3D rather than by fitting to
  screenshots -- which is what produced three wrong answers in a row.
  The degenerate frames (2 of 31 in attack swing 3) are identical in
  CCO, so any flattening there predates the 6090 work.
* Socket bases are SKEWED on about 5% of frames -- 23 of 465 sampled
  across five actions, determinant 0.52 to 0.9 with unit-length rows.
  That shears a weapon slightly. Squaring it would mean moving axes off
  where the file put them, which is the change that had weapons held at
  impossible angles, so it is left alone until someone reads how the
  engine handles a non-orthogonal dummy.
* ``[410199-f]`` sits beside the Super aura effect and nothing reads it.
* The builder's stats line computes its extent from the part bbox plus
  the socket translation, ignoring the socket's scale, so a scaled part
  reports a larger figure than it draws.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                            # noqa: E402


class Patch6090(Plugin):
    name = "patch6090"
    label = "Official patch client (5017-6090)"
    aliases = ("official", "6090")
    notes = (
        "Compiled .dbc tables (RSDB/SIMO/MESH) beside stale plaintext "
        "decoys, TQ-cipher .dat item and monster tables, u32-wrapped "
        "motion ids, and per-family colour conventions. Verified on the "
        "6090 base: NPCs, the standby set, ghosts, mounts, the "
        "character-select roles, effects and monsters all resolve."
    )

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """The compiled tables say "official patch client"; `version.dat`
        says *which*.

        Every client in this lineage answers yes to the format probes -- 5517
        ships the same .dbc set and the same TQ-cipher .dat tables, verified
        by running the readers on it -- so the formats alone cannot pick one.
        version.dat can: each client stamps it with its own patch number and
        nothing else (b'5017', b'5065', b'5165', b'5517', b'6090'). Claim
        6090 strongly, a sibling patch weakly enough that its own plugin
        wins, and an unstamped install weakly enough that a private-server
        plugin can outbid.
        """
        if not (exists("ini/3DSimpleObj.dbc") and exists("ini/3DObj.dbc")):
            return 0.0
        try:
            ver = (Path(root) / "version.dat").read_bytes()[:8].decode(
                "latin-1").strip()
        except Exception:
            ver = ""
        if ver == "6090":
            return 0.95
        if ver:
            # a sibling patch: readable by this profile, but its own plugin
            # knows what it does not have
            return 0.45
        return 0.75 if exists("ini/itemtype.dat") else 0.6

    # -- tables ------------------------------------------------------------
    def table_profile(self):
        import npcart
        return npcart.PROFILE_OFFICIAL

    def prefers_compiled_tables(self) -> bool:
        return True

    # -- art resolution ----------------------------------------------------
    _NPC_DIR = re.compile(r"c3/npc/(\d{3})/", re.I)
    _MOUNT_DIR = re.compile(r"c3/mount/(\d+)/", re.I)

    def texture_for_mesh(self, mesh, exists):
        """The two family conventions that cover what the tables miss.

        NPC directories skin from ``999<dir>0`` -- the convention every
        table-resolved dir follows (9992810 for 281, 9995200 for 520), and
        the reason 373 stopped wearing a monster's texture. Mounts skin
        from beside themselves: ``c3/mount/802/8020000.dds``. Both are
        INFERRED from the shipped layout, and both are namespace-correct
        where the generic guess crossed families.
        """
        m = (mesh or "").lower()
        mm = self._NPC_DIR.match(m)
        if mm:
            cand = f"c3/texture/999{mm.group(1)}0.dds"
            if exists(cand):
                return cand, "npc texture family (999<dir>0)", "inferred"
        mm = self._MOUNT_DIR.match(m)
        if mm:
            d = mm.group(1)
            cand = f"c3/mount/{d}/{d}0000.dds"
            if exists(cand):
                return cand, "mount dir texture (<n>0000)", "inferred"
        return None

    def colourways(self, texture, exists) -> Iterable[str]:
        """Colour by family: mounts in the middle digits, bodies in the
        leading digit, equipment in the tens digit. VERIFIED on disk --
        ThunderApe's nine skins, Ancient Armor's ten vests, mount 802's
        twenty."""
        p = (texture or "").replace("\\", "/")
        if not p.lower().endswith(".dds") or "/" not in p:
            return []
        head, fname = p.rsplit("/", 1)
        stem = fname[:-4]
        if not stem.isdigit():
            return []
        if "/mount/" in head.lower() and len(stem) == 7 and stem.endswith("00"):
            cands = [f"{head}/{stem[:3]}{k:02d}00.dds" for k in range(100)]
        elif len(stem) == 9 and stem.endswith("000000"):
            cands = [f"{head}/{d}{stem[1:]}.dds" for d in "123456789"]
        elif len(stem) >= 3:
            cands = [f"{head}/{stem[:-2]}{d}{stem[-1]}.dds"
                     for d in "0123456789"]
        else:
            return []
        return [c for c in cands if exists(c)]

    #: Monster directory -> its shipped skins, from a visual scan of the
    #: 6090 base (2026-08-05). Ground truth over any digit convention:
    #: the leading-digit rule pulled other monsters' base skins into these
    #: strips across the whole 1xx-2xx range. Most 1xx dirs step odd
    #: digits; 130/151/152 step 1-2; 111 wears 116000000; 154 borrows
    #: 703000000; 131's blue is 531000000, identified by decoding every
    #: candidate and measuring mean RGB.
    MONSTERS = {
        "103": ["103000000", "303000000", "503000000", "703000000"],
        "104n": ["104000000", "304000000", "504000000", "704000000",
                 "906000000"],
        "105": ["105000000", "305000000", "505000000", "705000000"],
        "108": ["108000000", "308000000", "508000000", "708000000"],
        "111": ["116000000"],
        "117": ["117000000", "317000000", "517000000", "717000000"],
        "129": ["129000000", "329000000"],
        "130": ["130000000", "230000000"],
        "131": ["131000000", "171000000", "531000000"],
        "133": ["133000000"],
        "136": ["136000000", "536000000", "736000000"],
        "141": ["141000000"],
        "148": ["148000000", "348000000", "548000000", "748000000"],
        "151": ["151000000", "251000000"],
        "152": ["152000000", "252000000"],
        "153": ["153000000", "353000000"],
        "154": ["154000000", "254000000", "354000000", "703000000"],
        "155": ["155000000", "255000000", "355000000"],
        "156": ["156000000", "356000000", "556000000", "756000000"],
        "157": ["157000000", "257000000", "357000000"],
        "160": ["160000000", "260000000", "360000000"],
        "161": ["361000000"],
        "162": ["162000000", "262000000", "362000000"],
        "163": ["163000000", "263000000", "363000000", "463000000"],
        "165": ["165000000", "265000000"],
        "166": ["166000000", "266000000"],
        "168": ["168000000", "268000000"],
        "179": ["179000000", "279000000"],
        "197": ["197000000", "397000000", "597000000", "797000000"],
        "200": ["200000000", "400000000"],
        "201": ["201000000", "401000000", "601000000", "801000000",
                "241000000"],
        "202": ["202000000", "402000000", "602000000", "802000000"],
        "203": ["203000000", "403000000", "603000000", "803000000"],
        "204": ["204000000", "404000000", "604000000", "804000000"],
        "205": ["205000000", "405000000", "605000000", "805000000"],
        "206": ["206000000", "406000000", "606000000", "806000000"],
        # 607000000 (bright orange) and 907000000 (mauve) are unclaimed:
        # they exist in the family, and nobody has confirmed the hare
        # wears them. 407 is the step-of-200 sibling every 2xx dir uses.
        "207": ["207000000", "407000000"],
        "208": ["208000000", "408000000", "608000000", "808000000"],
        "209": ["209000000", "369000000"],
    }

    def monster_colourways(self, ident):
        stems = self.MONSTERS.get(ident)
        if not stems:
            return None
        return [f"c3/texture/{s}.dds" for s in stems]

    def colour_provenance(self):
        """Authored, and this is the one place in the project where that
        word means a person rather than a table: `MONSTERS` came from the
        author eyeballing 36 monster directories on this base and saying
        which skins belong to which creature. A subclass on another client
        inherits the sets and must NOT inherit this claim."""
        return "verified colourway (default)", "authored"

    _FLAT_STEMS = ("c3/npc/999{g}.c3", "c3/npc/999{g}0.c3")

    def flat_family_base(self, group, paths, has_geometry):
        """A short-stem sibling is the family's one real body.

        MEASURED: ``999118100.c3`` is 3 vertices in a 3x4x6 box -- a shard
        -- while ``999118.c3`` beside it is 1,181 vertices at person
        scale. The numbered files are motion sets. Looks 118 and 256-273
        are this shape; look 001 is not (its body comes from the tables,
        through simple_object -> 3DObj) and has no short stem, so this
        returns None and the table answer stands.
        """
        for pat in self._FLAT_STEMS:
            cand = pat.format(g=group)
            if cand in paths and has_geometry(cand):
                return cand
        return None

    # -- table formats -----------------------------------------------------
    #: Four ways 6090 differs in FORM rather than content, every one of
    #: which failed silently and cost a debugging session. This is the list
    #: to read first when a 6090 lookup returns nothing.
    QUIRKS = {
        "stale ini decoys":
            "Every entity and appearance table ships twice: plaintext .ini "
            "stamped 2008-09 and a compiled twin stamped 2015 that the "
            "client actually reads. armor.ini offers 955 sections where "
            "armor.dbc has 3,326. The decoys parse cleanly, which is what "
            "makes them dangerous -- there is no error, only old answers.",
        "a second motion reader":
            "3dmotion.ini has a .dbc twin too, and attach.Catalogue kept "
            "its OWN load of the ini independent of anim.MotionIndex. "
            "Teaching one reader the twin left the other on 2009 data: "
            "idle_motion missed, every static preview drew an unposed body "
            "against a posed socket, and headgear sat 3 units off the head "
            "-- correcting itself the moment an animation played.",
        "unpadded ids in compiled tables":
            "The .dbc tables store section numbers as integers, so CCO's "
            "002135000 is 2135000 here. Body-typed tables need the "
            "nine-wide spelling restored on the way out, because every "
            "consumer's bodyType arithmetic slices that form -- and the "
            "mesh files themselves kept their zeros.",
        "four-wide action fields":
            "Action3DEffect keys the action field four wide and "
            "zero-padded (999.0100.130.300) where CCO uses three "
            "(999.100.135.999), on 10,267 of 10,299 rows. A literal string "
            "compare misses every non-wildcard row, which silenced every "
            "weapon and body effect in the builder at once.",
        "u32-wrapped motion ids":
            "npc.ini names ten-digit motion ids; 3dmotion.dbc keys them by "
            "their low 32 bits, because the client atoi's into a u32. The "
            "row then resolves to the OLD nine-digit filename -- the table "
            "is a renaming shim over an archive namespace that never "
            "changed.",
        "the per-weapon motion aliases are gone":
            "CCO's 3dmotion.ini spells out which motion folder each weapon "
            "set animates from, one key at a time -- 259 rows for set 480 "
            "alone, e.g. 1480100 = c3/0001/410/100.c3. 6090 ships NONE of "
            "them in either the stale ini or the compiled dbc, so an armed "
            "key like 2480100 simply is not there. The folders themselves "
            "all still ship (6090 keys 18 of them), so what went missing is "
            "the mapping, not the motions -- and because the lookup's own "
            "fallback chain ends at the unarmed set 000 AND REPORTS "
            "SUCCESS, an armed character was posed empty-handed with no "
            "error anywhere. attach.WEAPON_MOTION_SET recovers TQ's own "
            "pairing from CCO's table (480 -> 410, 350 -> 560, 370 -> 500, "
            "380 -> 741) and is applied before the fallback can hide the "
            "miss. The author confirms the armed pose is what the real 6090 "
            "client shows; do not revert this to the unarmed idle.",
        "the rewritten v_l_weapon track is FEMALE BODIES ONLY":
            "The single most useful fact about the weapon flattening, and it "
            "took a person noticing it on screen. MEASURED over the 410 "
            "motion set on all four body shapes, shortest basis row of the "
            "v_l_weapon dummy track across every frame:\n"
            "                 CCO     5517    6090\n"
            "    001 female  1.000    0.158   0.158   (idle 100)\n"
            "    002 female  1.000    0.111   0.111\n"
            "    003 male    1.000    1.000   1.000\n"
            "    004 male    1.000    1.000   1.000\n"
            "and on attack swing 3 the female figures fall to 0.020 and "
            "0.012 -- an 80x collapse in two axes, which is the flattening. "
            "CCO is a clean unit rotation on all four. So the official "
            "lineage rewrote this dummy's track for the FEMALE bodies and "
            "left the male ones alone; 5517 and 6090 are identical here. "
            "It is not an ordinal-binding slip: the degenerate track follows "
            "v_l_weapon whichever slot it occupies (index 1 on 001, index 2 "
            "on 002, whose mesh puts v_body first), and in each file exactly "
            "one track is degenerate and it is that one. No alternative "
            "assignment fixes it. Right-hand weapons and headgear are unit "
            "on every shape.",
        "v_l_weapon's track was rewritten and v_r_weapon's was not":
            "The left-hand weapon socket is the ONE thing 6090 changed here, "
            "and it is why weapons look wrong. Same body mesh, same motion "
            "file path, same socket POSITION to the millimetre -- but "
            "v_l_weapon's basis in 6090's c3/0001/410/100.c3 measures "
            "(0.989, 0.177, 0.158) with determinant 0.017 where CCO's is a "
            "clean unit rotation, and the two bases are not even a scaled "
            "version of each other (max element difference 0.87 after "
            "normalising). Meanwhile v_r_weapon and v_armet in the SAME file "
            "are bit-identical to CCO's, difference 0.00000. So this is not "
            "a format change, a decode error or a convention: 6090 rewrote "
            "one dummy's track. Right-hand weapons place correctly here; "
            "left-hand ones do not, and no normalisation can recover an "
            "orientation the file does not contain.",
        "socket bases are neither unit nor orthogonal":
            "A dummy bone's stored matrix carries scale and skew, and it "
            "varies per dummy WITHIN one file: in c3/0002/000/100.c3 the "
            "v_armet and v_r_weapon tracks are exact unit rotations while "
            "v_l_weapon's rows measure (0.111, 0.149, 0.994) -- and at "
            "frame 24 of attack swing 3 that same socket reads (0.999, "
            "0.051, 0.056), which squashes a rigid weapon flat. One key "
            "per frame, so interpolation is not involved: it is how the "
            "dummies were authored. The scale must be removed for the "
            "weapon to hold its shape, but ONLY by normalising each row in "
            "place -- reordering the axes to orthogonalise them (Gram-"
            "Schmidt, longest first) swings one 43 degrees and holds the "
            "weapon at an impossible angle. Rows below a fifth of the "
            "longest are noise and are rebuilt from the two that are not.",
    }

    #: Observed key-field widths. Declared so a padding mismatch is
    #: recognisable as one rather than read as missing content.
    FIELD_WIDTHS = {
        "Action3DEffect.ini": {"shape": 3, "action": 4, "type": 3, "sub": 3},
    }

    def table_quirks(self):
        return dict(self.QUIRKS)

    def key_field_widths(self):
        return {k: dict(v) for k, v in self.FIELD_WIDTHS.items()}

    def aura_convention(self) -> str:
        """6090 declares the Super glow by NAMING the effect after the
        appearance id -- `3DEffect.ini [410199]` is Rainbow Blade Super's
        aura, and no such section exists for 410195 or 410196. It ships
        zero always-on Action3DEffect rows, where CCO ships 826 whose only
        job is to point at an identically named effect.
        """
        return "effect-named-for-id"

    # -- attachment --------------------------------------------------------
    #: MEASURED over all 3,001 body meshes in the 6090 base: every single
    #: one carries exactly `v_body` + `v_armet` + `v_l_weapon` +
    #: `v_r_weapon` and nothing else (one carries `v_body01` instead of
    #: `v_body`). So the socket set is not a matter of opinion here.
    #:
    #: `RolePart.ini` disagrees with the art in both directions, which is
    #: why declarations cannot be trusted: it declares 8 parts (body,
    #: armet, r_weapon, l_weapon, mount, misc, head, shield) and 7 dummies
    #: (v_armet, v_misc, v_r_weapon, v_l_weapon, v_mount, v_l_shield,
    #: v_r_shield) -- and drops CCO's `v_head` entirely, where CCO listed
    #: 52 dummies including v_head, v_back, v_pelvis and v_pet.
    BODY_SOCKETS = ("v_body", "v_armet", "v_l_weapon", "v_r_weapon")

    #: Slot -> socket. "" means the client cannot attach that slot at all.
    SLOT_SOCKETS = {
        "body": None, "mix_body": None,
        "armet": "v_armet", "mix_armet": "v_armet",
        "armet_dx8": "v_armet", "mix_armet_dx8": "v_armet",
        "r_weapon": "v_r_weapon",
        "l_weapon": "v_l_weapon",
        # A shield is held in the left hand and no body ships v_l_shield,
        # so the left weapon socket is the shield's socket -- the same
        # fallback the app always had, now a positive statement. Moot for
        # now: 6090 ships no shield appearance table at all.
        "shield": "v_l_weapon",
        # The mount socket is INVERTED and it does ship: mount meshes carry
        # `v_mount` (c3/mount/801 20 verts, 802 and 850 12), so the rider
        # is placed on the mount rather than the mount on the rider. Mount
        # 850 additionally carries v_l_shield, v_r_shield, v_armet, arms
        # and v_pet -- a mount is a richer rig than a body.
        "mount": "v_mount",
        # No body carries v_misc, so an accessory has nowhere authored to
        # hang. Declared unattachable rather than dropped at the body
        # origin.
        "misc": "",
        # Nor v_head: 6090 dropped the socket CCO used for a bare head, and
        # ships no head appearance table either.
        "head": "",
        "pelvis": "",
    }

    def slot_socket(self, slot):
        return self.SLOT_SOCKETS.get(slot)

    #: Body shapes whose `v_l_weapon` dummy track this client ships broken.
    #: 001 and 002 are the two FEMALE shapes; 003 and 004 are male and are
    #: clean. See `socket_correction`.
    BROKEN_LWEAPON_SHAPES = ("001", "002")

    def socket_correction(self, socket, body_appearance):
        r"""The female `v_l_weapon` track is degenerate in this lineage.

        MEASURED, shortest basis row over every frame of the 410 motion set:

                             CCO     5517    6090
            001 female S    1.000    0.158   0.158     (idle 100)
            002 female L    1.000    0.111   0.111
            003 male S      1.000    1.000   1.000
            004 male L      1.000    1.000   1.000

        and on attack swing 403 the two female figures reach 0.020 and 0.012 --
        an eighty-fold collapse in two axes, which flattens a held weapon to a
        sliver. CCO is a clean unit rotation on all four shapes.

        WHY THIS IS A DEVIATION AND NOT A FIX
        -------------------------------------
        The engine does nothing to repair it, and that was checked rather than
        assumed. `Role3D!sub_30B0` multiplies the dummy's whole matrix in
        unmodified; the follow chain is one link deep for every socket on an
        unmounted figure, so the composition is exactly ours;
        `Motion_GetMatrix_Blend` reduces to the plain fetch outside a
        transition; and `RoleView` never branches on body shape at all -- its
        `nLook` only builds appearance ids and drives one sentinel. The mesh
        gives nothing to branch on either: the female `v_l_weapon` chunk is an
        ordinary 6-vertex dummy identical to its own `v_r_weapon`.

        **So the shipped client almost certainly draws the same squash**, and
        correcting it is a choice to show what the artist presumably meant
        rather than what the client shows. `docs/handoff_5517_base_prep.md`
        §6.5a has the whole account.

        WHY `unit-rows` AND NOT SOMETHING CLEVERER
        ------------------------------------------
        Nothing recovers the intended orientation, and that is measured four
        ways. The rows are skew, not scale: normalise them and the pairwise
        dots are 0.15 and -0.22 where a rotation gives 0. Gram-Schmidt swings
        an axis 43 degrees. Against CCO's clean track as ground truth the
        6090 rows are not a permutation either -- best-match dots run 0.73 to
        0.97 and on shape 002 two rows land on the same CCO row. The
        orientation is not in the file, so no arithmetic puts it back.

        SO THE ORIENTATION IS BORROWED FROM CCO, AND THAT IS EXACT
        ----------------------------------------------------------
        The one thing that *is* recoverable is CCO's own track, and it is the
        right source for a measured reason: **the socket's translation is
        identical between the two clients** -- 0.0000 across both female
        shapes, at rest and mid-swing --

            001 idle    CCO (13.90, -6.43, 103.20)   6090 (13.90, -6.43, 103.20)
            002 swing   CCO (-49.06, 33.31, 128.29)  6090 (-49.06, 33.31, 128.29)

        so the skeleton and the pose already agree and only the 3x3 was
        rewritten. Taking CCO's 3x3 and keeping our own translation restores
        the authored orientation without moving the hand.

        It is not a fixed rotation, which is why nothing simpler works: the
        angle from our basis to CCO's is steady through the idle (113 degrees
        on 001, 118 on 002) and swings from 92 to 161 degrees through attack
        3. A constant offset would fix the resting pose and wreck the
        animation.

        `unit-rows` remains the **fallback** for when CCO is not one of the
        declared installs, or its track does not line up (its action 130 is 25
        frames against this lineage's 20). It stops the collapse and leaves
        every direction where the file put it, so the weapon is full size and
        pointed somewhere the artist did not choose -- worse than correct,
        better than a sliver. The viewer says which of the two happened.
        """
        if socket != "v_l_weapon":
            return None
        shape = str(body_appearance or "")[:3]
        if shape not in self.BROKEN_LWEAPON_SHAPES:
            return None
        return ("reference-basis:cco",
                "This client ships a degenerate v_l_weapon dummy track on the "
                "female bodies (001, 002): its basis rows collapse to as "
                "little as 0.012 of unit length, which flattens a held weapon "
                "to a sliver and leaves it pointing the wrong way. The male "
                "bodies are unaffected and CCO is clean on all four. The "
                "orientation is taken from CCO's track for the same body, "
                "action and frame, keeping this client's own translation -- "
                "the two agree to 0.0000, so the hand does not move and only "
                "the rewritten 3x3 is restored. A VIEWER DEVIATION, NOT A "
                "FIX: the real client very likely shows the squash, and the "
                "compatible-client work must not inherit this.")

    def sockets_present(self):
        return {
            "v_armet": "on all 3,001 bodies (24 verts) -- headgear",
            "v_l_weapon": "on all 3,001 bodies (6 verts) -- left hand, "
                          "and where a shield would go",
            "v_r_weapon": "on all 3,001 bodies (6 verts) -- right hand",
            "v_mount": "on the MOUNT mesh, not the body -- the rider is "
                       "placed onto it (801/802/850 all carry it)",
            "v_misc": "ABSENT from every body: accessories cannot attach",
            "v_head": "ABSENT: 6090 dropped CCO's bare-head socket",
            "v_l_shield": "absent from bodies; present on mount 850",
        }

    # -- naming ------------------------------------------------------------
    #: Names a dump gets wrong or never had. From the same visual scan.
    NAME_PINS = {
        ("monster", "133"): "Ganoderma",
        ("monster", "134"): "RareMeteorDove",
        ("monster", "141"): "Mimic",
        ("monster", "209"): "FireMonster",
    }
    #: Joins that name a monster something it visibly is not: 109 and 301
    #: both came out "Shopkeeper" over meshes that are nothing of the kind,
    #: and 218 duplicates 201's art under its own name. A number is the
    #: honest label until the real name is known.
    NAME_DROPS = {("monster", "109"), ("monster", "301"), ("monster", "218")}

    def entity_name_overrides(self):
        return dict(self.NAME_PINS), set(self.NAME_DROPS)

    # -- library import ----------------------------------------------------
    def import_plan(self, root, exists) -> dict:
        """Official archives are byte-identical across the whole patch
        lineage -- same size, same md5, 25,013 packed assets in all five --
        so an importer that hashes 750 MB per client does 25,013 redundant
        reads. Detect the shared pair and skip it; the difference between
        patches is entirely the loose layer laid over them (6090 has
        50,664 loose files against 5017's 3,689).
        """
        plan = super().import_plan(root, exists)
        plan.update({
            "sharedArchives": True,
            "tables": ["ini/"],
            "note": "patch 6090: shared archives skipped, loose layer "
                    "imported, compiled .dbc tables read",
        })
        return plan


PLUGIN = Patch6090()
