#!/usr/bin/env python3
r"""
plaintext -- the third parse family: official patch clients 5017 / 5065 / 5165.

**THE PLAINTEXT `ini/*.ini` TABLES ARE LIVE HERE. THEY ARE NOT DECOYS.**

That sentence is the whole plugin, and it is the exact inverse of the rule the
6090 work was built on. `core/dbc.py` opens by warning that an official
client's plaintext tables are *"stale decoys"* six years out of date beside a
compiled `.dbc` twin the client actually reads. On 5017, 5065 and 5165 there
is **no `.dbc` at all** -- compiled twins first appear at 5517 -- so the same
files are the only tables that exist and the client reads them.

**A plugin here must invert that assumption, not inherit it.** This class
therefore derives from `Plugin` and states every fact positively, rather than
subclassing `Patch6090` and switching four of its answers off. Four of 6090's
answers are wrong here -- `prefers_compiled_tables`, `table_profile`,
`aura_convention`, `key_field_widths` -- and an override is exactly the shape
that gets quietly deleted by a later tidy-up, after which the family silently
inherits the trap.

---

1. THE LINEAGE SPLITS TWICE, AND THE TWO SPLITS ARE IN DIFFERENT PLACES
-----------------------------------------------------------------------
Compiled tables directly in ``ini/``, counted:

    5017  0        5065  0        5165  0        5517  14      6090  15

But the *tables* do not split there. Every plaintext table that acquired a
compiled twin at 5517 is **byte-identical in 5165, 5517 and 6090** (md5, this
session)::

    3dobj.ini  3dtexture.ini  3dmotion.ini  3DSimpleObj.ini
    armor.ini  armet.ini      weapon.ini    3DEffect.ini  3DEffectObj.ini

So 6090's famous stale decoy **is literally 5165's file**. The plaintext layer
did not rot gradually; it stopped the moment a compiled twin appeared beside
it. The corroboration is that the tables which never got a twin kept moving:
`npc.ini`, `Action3DEffect.ini` and `ItemTexture.ini` differ at every one of
the five patch levels.

That is why the *engine* lineage (5017+5065 | 5165 | 5517 | 6090) and the
*table* lineage (5017 | 5065 | 5165+5517+6090 frozen) disagree, and a plugin
describes tables.

2. THE PROFILE, PROVED BY RUNNING IT BOTH WAYS
----------------------------------------------
Same code, both profiles, every official install. The wrong profile does not
raise on either side -- that is the point -- it answers with paths the install
does not ship::

                    OFFICIAL ok      PLAINTEXT ok    plaintext paths that exist
        5017          0 / 503          503 / 503          2000 / 2000
        5065          0 / 575          575 / 575          2000 / 2000
        5165          0 / 880          855 / 880          1970 / 1970
        5517       1123 / 1123        1066 / 1123           840 / 1870
        6090       2256 / 2264        1815 / 2264           841 / 1880

Read the last column: on 5517 and 6090 the plaintext profile still produces
1,066 and 1,815 confident plans, and **fewer than half the files they name are
in the install**. On the family every single path resolves. The failure is
inverted and it is silent in both directions.

3. WHAT THIS FAMILY DOES DIFFERENTLY, IN FORM
----------------------------------------------
Every entry in `QUIRKS` below was measured across all five official installs
plus CCO, and several of them are the *opposite* of the 6090 answer. The two
that would silently swallow half the client's content are the three-wide
action field and the ten-wide motion key.

4. THE DISCRIMINATOR, CHECKED AGAINST SEVEN INSTALLS
-----------------------------------------------------
"No `.dbc`" is **not** sufficient, and the negative control that proves it is
Zephyr-1057: it ships zero `.dbc` files and four-wide action fields, so the
absence of compiled tables says nothing on its own. CCO ships no `.dbc`
either. The claim has to be positive -- the plaintext lookup chain present and
the compiled one absent -- and then it separates all seven::

    5017 5065 5165   npc.ini + 3DSimpleObj.ini, no .dbc          -> CLAIM
    5517 6090        3DSimpleObj.dbc present                     -> reject
    CCO              npc.json, no npc.ini                        -> reject
    Zephyr-1057      npc.ini but none of the four lookup tables  -> reject

`version.dat` then says *which* of the three, exactly as it does for 5517 and
6090: every official client stamps it with its patch number and nothing else.

5. WHAT IS **NOT** CLAIMED HERE
--------------------------------
Nobody has looked at any of these three clients on screen. So this plugin
declines every hook whose honest answer is a person's judgement, rather than
borrowing 6090's:

* `monster_colourways` / `colourways` -- no scan has been done here.
* `texture_for_mesh` -- 6090's `999<dir>0` NPC rule is **measured not to hold
  here**: of the NPC directories named by `3dobj.ini`, the rule finds a
  texture for 0 of 3 on 5017, 0 of 3 on 5065 and 1 of 9 on 5165, against 22 of
  32 on 5517 and 85 of 95 on 6090. Inheriting it would have been a convention
  from a different era pointed at this one.
* `entity_name_overrides`, `sockets_present`, `slot_socket` -- unmeasured here.

`socket_correction` is the one judgement call that *is* made, and only because
it was measured on this family rather than assumed; see the override.

6. A COUPLING THIS PLUGIN MAKES MORE LIKELY -- OPEN, FLAGGED NOT FIXED
-----------------------------------------------------------------------
**`base_fingerprint` collides across the nine 5065 copies on this machine**,
and now there is a plugin name that invites declaring them. Measured::

    5065           patch5065-d5c2fea958d3
    5065-artwork   unknown-d5c2fea958d3      <- same fingerprint
    5065-fresh     unknown-d5c2fea958d3
    5065-loose     unknown-d5c2fea958d3
    5065-oracle    unknown-9ead28fe1932      (three more share this one)

That is by design -- the fingerprint hashes only `ini/`, and
`coroot.base_fingerprint` says plainly that two installs differing solely in
loose art hash the same, calling it the right trade for choosing an index
namespace. It *was* the right trade while those copies were undeclared and
landed in `unknown-*`. Declare four of them `patch5065` and they become one
namespace -- **and they differ in exactly the layer `meshtex` indexes.** The
cost of the collision stops being theoretical at that point.

So: declare a copy only when you mean it, and if the artwork/loose variants
ever need indexes of their own, that is a `base_fingerprint` change, which
C18 says must be announced rather than done quietly. Not changed here.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                            # noqa: E402

#: The patch numbers this family covers. `version.dat` carries one of these
#: and nothing else on an official install.
FAMILY_STAMPS = ("5017", "5065", "5165")

#: The plaintext lookup chain. Present *and* uncompiled is what defines the
#: family -- see the module docstring section 4 for why the absence of `.dbc`
#: alone is not enough (Zephyr-1057 refutes it).
PLAINTEXT_CHAIN = ("ini/3DSimpleObj.ini", "ini/3dobj.ini",
                   "ini/3dtexture.ini", "ini/3dmotion.ini")

#: The compiled tables whose presence means this is 5517 or later.
COMPILED_MARKERS = ("ini/3DSimpleObj.dbc", "ini/3DObj.dbc")


def version_stamp(root) -> str:
    """`version.dat`, which every official client stamps with its own patch
    number and nothing else -- b'5017', b'5065', b'5165', b'5517', b'6090'.
    The format probes cannot separate siblings within a family; this can."""
    try:
        return (Path(root) / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""


class PlaintextFamily(Plugin):
    """Patch clients that ship their entity tables **only** in plaintext.

    Concrete plugins for the three stamped members subclass this and add
    nothing but identity and their own absences (`plugins/patch5017.py`,
    `patch5065.py`, `patch5165.py`). This class itself is registered too, so
    an unstamped repack of one of them gets the family's parse profile
    instead of falling to GENERIC and resolving nothing -- weakly enough that
    any stamped or purpose-written plugin outbids it.
    """

    name = "plaintext"
    label = "Official patch client, plaintext tables (5017-5165)"
    origin = "official"
    aliases = ("plaintext-family",)
    notes = (
        "Ships NO compiled .dbc tables, so the plaintext ini files are the "
        "LIVE tables rather than the stale decoys they become at 5517. "
        "Three-wide Action3DEffect action fields, always-on aura rows in the "
        "table, nine-digit npc motion ids that resolve directly, and "
        "ten-wide zero-padded body-motion keys. Verified by running the "
        "readers on all three members and on 5517/6090/CCO as contrasts; "
        "nobody has looked at any of the three on screen, so no colour set "
        "or name is claimed here."
    )

    #: `version.dat` stamp this concrete plugin owns. Empty on the family
    #: class itself, which claims unstamped installs weakly instead.
    STAMP = ""

    # -- identification ----------------------------------------------------
    def confidence(self, root, exists) -> float:
        """Positive evidence only: the plaintext lookup chain present and no
        compiled twin. Section 4 of the module docstring has the seven-way
        check and the negative control that makes the positive form
        necessary.

        A compiled twin is disqualifying **whatever `version.dat` says**,
        because the stamp describes the patch and this plugin describes the
        tables -- a repack that added `.dbc` files is no longer this family
        however it is labelled.
        """
        if any(exists(m) for m in COMPILED_MARKERS):
            return 0.0
        if exists("ini/npc.json") or not exists("ini/npc.ini"):
            return 0.0
        if not all(exists(t) for t in PLAINTEXT_CHAIN):
            return 0.0
        ver = version_stamp(root)
        if self.STAMP:
            return 0.95 if ver == self.STAMP else 0.0
        # the family class: only for an install none of the three claims.
        return 0.0 if ver in FAMILY_STAMPS else 0.5

    # -- tables ------------------------------------------------------------
    def table_profile(self):
        import npcart
        return npcart.PROFILE_PLAINTEXT

    def prefers_compiled_tables(self) -> bool:
        """**False, and this is the load-bearing declaration of the whole
        plugin. Do not "fix" it to True.**

        `core/dbc.py` and `plugins/patch6090.py` both state the official rule
        as *"the plaintext .ini is a stale decoy, the client reads the .dbc"*,
        and that rule is correct -- for 5517 and later. These three clients
        ship **zero** compiled tables (measured: 0, 0, 0 against 14 at 5517
        and 15 at 6090), so there is no twin to prefer and the plaintext file
        is not stale, it is the table.

        Nothing today branches on this hook -- it is on
        `test_viewer.UNCONSUMED_HOOKS` -- so the value cannot currently be
        read as behaviour. It is here as the family's statement of record,
        and `PlaintextFamilyPlugin::test_the_plaintext_tables_are_the_live_
        ones` asserts it alongside the on-disk fact it summarises, so
        changing one without the other fails.
        """
        return False

    # -- table formats -----------------------------------------------------
    #: Measured across all five official installs, with CCO and Zephyr-1057
    #: as controls. Each entry names the symptom, because the symptom is what
    #: the next person searches for -- and every one of these fails silently.
    QUIRKS = {
        "the plaintext tables are LIVE, not decoys":
            "The inverse of the rule core/dbc.py opens with. There is no "
            ".dbc in this family at all (0 files, against 14 at 5517 and 15 "
            "at 6090), so ini/3dobj.ini, ini/3dtexture.ini, ini/3dmotion.ini "
            "and ini/3DSimpleObj.ini are the tables the client reads. "
            "Reading them is correct here and reading them on 6090 is the "
            "six-year-old answer. Proved by running both profiles on every "
            "install: the plaintext profile resolves 503/503, 575/575 and "
            "855/880 npcs here with 100% of the named paths present, and on "
            "5517/6090 it still produces 1066 and 1815 confident plans of "
            "which fewer than half the paths exist.",
        "the plaintext layer froze at 5165":
            "Every plaintext table that got a compiled twin at 5517 is "
            "BYTE-IDENTICAL in 5165, 5517 and 6090 -- 3dobj, 3dtexture, "
            "3dmotion, 3DSimpleObj, armor, armet, weapon, 3DEffect, "
            "3DEffectObj, by md5. So 6090's stale decoy is 5165's live file, "
            "and the freeze is not gradual rot: a table stopped being "
            "maintained exactly when a twin appeared beside it. The tables "
            "that never got a twin -- npc.ini, Action3DEffect.ini, "
            "ItemTexture.ini -- differ at all five patch levels.",
        "three-wide action fields":
            "Action3DEffect keys the action field THREE wide here "
            "(999.100.135.393), on 100% of rows -- 8,685 on 5017, 8,128 on "
            "5065, 9,772 on 5165, no exceptions. 5517 and 6090 pad it to "
            "four (999.0100.130.300). This is patch6090's four-wide quirk "
            "read backwards, and it is the CCO spelling. A literal string "
            "compare built for 6090 misses every non-wildcard row here, "
            "which silences every weapon and body effect at once and raises "
            "nothing. Zephyr-1057 is four-wide despite shipping no .dbc, so "
            "this cannot be inferred from the absence of compiled tables.",
        "the always-on aura is declared in the TABLE":
            "The CCO convention, not 6090's. This family ships 646 / 586 / "
            "622 rows of the form 999.999.<type>.<sub>=<type><sub>, whose "
            "only job is to point at an identically named effect. 5517 and "
            "6090 ship ZERO such rows and declare the aura by naming an "
            "effect after the appearance id instead. So the split is at "
            "5517 again, and a resolver that only knows 6090's rule finds no "
            "Super glow on any weapon here.",
        "npc motion ids are nine digits and resolve directly":
            "npc.ini names nine-digit StandByMotion ids here (999001100) "
            "against 5517/6090's ten and eleven, and they hit the plaintext "
            "3dmotion.ini as plain integers: 519/519 on 5017, 591/591 on "
            "5065, 876/901 on 5165, and ZERO rows anywhere in the family "
            "need the u32 wrap patch6090 documents. The wrap is a property "
            "of the compiled table, not of the client's id scheme. The "
            "control is the same lookup at 5517 and 6090, where 0 of 1135 "
            "and 0 of 2277 resolve against that same (now frozen) file.",
        "body-motion keys are TEN wide and zero-padded":
            "The one that hid the longest, because a compiled twin covers "
            "it. The official plaintext 3dmotion.ini writes a body motion as "
            "0001410100 = c3/0001/410/100.c3 where CCO writes 1410100, and "
            "both motion readers build the seven-wide form. On 5517/6090 the "
            ".dbc overlay rebuilds that spelling from each row's path, so "
            "the difference never showed; here there is no overlay and EVERY "
            "body-motion lookup missed -- parts.idle_motion None for ws=000 "
            "and ws=480 on all three, anim.AnimDB.clip None for both the "
            "bare and the armed idle -- after which a static preview falls "
            "back to the mesh's embedded MOTI, a T-pose on shape 004. "
            "Nothing raised. Fixed in attach.Catalogue._load_flat and "
            "anim.MotionIndex, which now index the path-derived spelling "
            "from the ini as well; both keep the compiled twin authoritative "
            "where one exists.",
        "the per-weapon-set alias rows are here, under the padded spelling":
            "patch6090's quirk list says the official clients ship NONE of "
            "CCO's per-(weapon set, action) alias rows. Measured: they are "
            "in every official plaintext ini, spelled 0002480100 = "
            "c3/0002/410/100.c3. What is absent is the seven-wide 2480100 "
            "that CCO writes. On this family that table is LIVE, so the "
            "client's own data answers the armed-motion question and "
            "attach.WEAPON_MOTION_SET is not needed -- the two agree on 108 "
            "of 122 weapon sets on 5017 (109 of 122 on 5165), disagree on "
            "one, and the rest are absent from the ini. On 5517/6090 those "
            "rows are stale, so the hardcoded table remains the right source "
            "there.",
        "itemtype.dat is space-separated, not the @@ record form":
            "TQ File Cipher seed 9527 as everywhere, but the classic "
            "space-separated layout: 39 columns on 5017 and 5065, exactly "
            "tqdat.FIELDS_SPACE. 5165 carries a 40th, and it is qualityColor "
            "-- joined to 5517's own column on 8,157 of 8,177 shared item "
            "ids, against 0/8,177 for each of the three neighbouring columns "
            "tried as controls. 5517 switches to the @@ record form (59 "
            "columns; 6090 has 66), so a reader that assumes @@ finds one "
            "field per row here.",
        "3DEffect.ini is authoritative here, and its schema moves at 5165":
            "No 3DEffect.dbc exists below 5517, so the plaintext effect table "
            "is the live one on all three -- the 42% plaintext error rate "
            "measured for 6090 is a statement about 6090's decoy and does "
            "not apply here. The schema is NOT constant across the family "
            "though, and the constants in docs/effects.md are CCO's file "
            "rather than the format's. MEASURED [V], sections / rows "
            "carrying ColorEnable: CCO 2243/111, 5017 2284/0, 5065 2300/0, "
            "and 5165, 5517 and 6090 all 2599/2599 (byte-identical -- the "
            "freeze). **ColorEnable is absent from 5017 and 5065 entirely**, "
            "as is Lev (0 rows against 1453), so any per-effect colour rule "
            "keyed on that field has no data to read on two of these three. "
            "Derive these per base; do not inherit a number.",
        "the 5065|5165 boundary is chronology, not the engine split":
            "Raised as a possible counterexample to handoff_5517_base_prep "
            "section 2.2, which says the engine splits 5017+5065 | 5165 | "
            "5517 | 6090 while the tables split 5017+5065+5165 | 5517+6090, "
            "and that a CODE finding must be pinned to its own split. "
            "ColorEnable's presence groups 5017+5065 against 5165+, which "
            "looks like the engine grouping applied to a table.\n"
            "TWO REASONS IT IS NOT. First, that grouping is a COARSENING "
            "CONSISTENT WITH BOTH: every split named above puts a boundary "
            "between 5065 and 5165, so an attribute that changes exactly "
            "there cannot discriminate between them -- it is not a "
            "counterexample, it is silent. Second, the discriminating check "
            "-- is ColorEnable alone on this boundary? -- says emphatically "
            "no. MEASURED [V], keys present from 5165 and absent in both "
            "5017 and 5065: **26 in 3DEffect.ini** (ColorEnable, Lev, "
            "Billboard, Scale0-6, and the 8-11 slots of ASB/ADB/EffectId/"
            "TextureId), plus ZoomPercent in npc.ini and qualityColor in "
            "itemtype.dat -- three unrelated tables. And nine whole "
            "plaintext tables change at that same boundary and never change "
            "again, which is the freeze this plugin is built on.\n"
            "GRADED: the presence counts are [V]. The interpretation is "
            "**OPEN** -- what is established is that many unrelated "
            "attributes arrive together at 5165, which is what a content "
            "release looks like; nothing here establishes WHY, and the "
            "engine reading is disfavoured rather than refuted. It did not "
            "decide the one-plugin-or-three question: that rests on the "
            "profile measurement and on 5017's own absences.",
        "how many body-motion families the ini names -- and it is not "
        "uniform across this family":
            "The shadow trap with the shadow removed, and it SPLITS these "
            "three, which is why it is declared per base. On 5517 and 6090 "
            "3dmotion.ini names four families (c3/0001..0004) while the "
            "compiled twin names sixteen (and twenty on 6090) -- the "
            "plaintext file under-reports its own table by 75% and the twin "
            "corrects it. Here there is no twin. MEASURED on the filesystem "
            "rather than from the table under test, families named by the "
            "ini against families present on disk: **5017 four / four and "
            "5065 four / four -- complete**, and **5165 four named against "
            "EIGHT on disk**, shipping 204 loose .c3 files under 1001-1004 "
            "(groups 000, 410, 611; all 120 sampled parse as pure motion "
            "sets) that its own table never mentions and nothing else can "
            "supply. CCO is the control in the other direction: its ini "
            "names eight families and only four resolve, so an ini can "
            "over-report too and 'the ini is complete' has to be measured in "
            "both directions.",
        "twin presence is per FILE, not per client":
            "Immaterial for this family and stated so it does not become the "
            "next wrong generalisation. 3DEffect.dbc exists on 5517 and 6090; "
            "EmotionIco.dbc on 6090 alone. These three have neither, and "
            "zero compiled tables of any kind, which is what makes the "
            "whole-directory assertion in "
            "PlaintextFamilyPlugin.test_the_plaintext_tables_are_the_live_ones "
            "the right shape HERE and the wrong shape for a general helper. "
            "Ask per file anywhere else.",
        "Monster.dat is sectioned text and 5017 is two fields short":
            "TQ cipher, [Name] sections rather than columns, and readable by "
            "core/tqdat.py unchanged: 411 / 418 / 568 sections against 745 "
            "at 5517 and 1,008 at 6090. 5017 alone ships no ArmetColor and "
            "no LWeaponColor key, so a monster's extra art is a field "
            "shorter there; the reader is tolerant and simply omits them, "
            "which is why this needs writing down rather than catching.",
        "this family AUTHORS the tables the later clients ship as decoys":
            "MEASURED by md5 of every table read through AssetRoot, across "
            "all five official installs. There is not one freeze, there are "
            "three patterns, and conflating them is how a base comparison "
            "becomes a tautology.\n"
            "  (a) FROZEN AT 5165 -- armor, armet, weapon, 3dobj, 3dtexture, "
            "3dmotion, 3DSimpleObj, 3DEffect. 5017, 5065 and 5165 each have "
            "their OWN file; 5165's is then byte-identical in 5517 and 6090. "
            "So this family is the ORIGIN of the stale decoy, not a victim "
            "of it: 3DSimpleObj.ini is 155 / 156 / 191 sections here with no "
            "compiled twin anywhere, and it is 5165's 191-section file that "
            "5517 ships beside a 235-record .dbc and 6090 beside a 388-record "
            "one -- the 44- and 197-row drops that make crawling the "
            "plaintext wrong THERE and right HERE.\n"
            "  (b) ONE FILE ACROSS ALL FIVE -- mount, misc, head, miscmotion, "
            "weaponmotion, armetmotion, mountmotion, AdditiveSize. Never "
            "touched between 5017 and 6090. Any claim of the form 'these two "
            "clients agree about mount.ini, therefore X' is void: it is one "
            "file and agreement was never possible to lose. See "
            "CORRECTIONS on the tautology rule.\n"
            "  (c) GENUINELY PER-CLIENT -- npc.ini (five distinct), "
            "Action3DEffect.ini (six, so CCO differs too), ItemTexture.ini "
            "(four, from 5065 on). These are the only tables where comparing "
            "two of these clients measures anything.\n"
            "RolePart.ini is its own case: 5017's is unique and 5065's is "
            "byte-identical through 6090, which is exactly the seven-versus-"
            "eight part-slot split.",
    }

    #: Three wide, which is CCO's spelling and not 6090's. Declared so a
    #: padding mismatch is recognisable as one instead of reading as content
    #: that does not exist.
    FIELD_WIDTHS = {
        "Action3DEffect.ini": {"shape": 3, "action": 3, "type": 3, "sub": 3},
    }

    def table_quirks(self):
        return dict(self.QUIRKS)

    def key_field_widths(self):
        return {k: dict(v) for k, v in self.FIELD_WIDTHS.items()}

    def aura_convention(self) -> str:
        """`"table"`. 646 / 586 / 622 always-on `Action3DEffect` rows against
        5517's and 6090's zero -- see the quirk. This is the CCO
        indirection, still present because it had not been dropped yet."""
        return "table"

    # -- art resolution ----------------------------------------------------
    def colour_provenance(self):
        """Nothing here is authored and nothing is inherited either: this
        plugin ships no colour sets, so whatever `default_colour` returns
        came from the app's own convention. Say so."""
        return "app convention (no scan on this client)", "inferred"

    # -- attachment --------------------------------------------------------
    #: Body shapes whose `v_l_weapon` dummy track this family ships
    #: degenerate. MEASURED HERE rather than inherited -- body mesh
    #: `00N131090` with its armed 480/100 motion bound, shortest basis row:
    #:
    #:                  5017    5065    5165    5517    6090     CCO
    #:     001 female  0.158   0.158   0.158   0.158   0.158   1.000
    #:     002 female  0.111   0.111   0.111   0.111   0.111   1.000
    #:     003 male    1.000   1.000   1.000   1.000   1.000   1.000
    #:     004 male    1.000   1.000   1.000   1.000   1.000   1.000
    #:
    #: So the rewritten track is **not** a 5517-or-6090 change: it is present
    #: in 5017, the earliest client here, and the five official figures come
    #: from byte-identical body meshes (md5 1109d384de on 001) and
    #: byte-identical motion files.
    #:
    #: **The load-bearing half of that table involves no cross-client
    #: comparison at all.** 5017 against 5517 and 6090 is the same bytes read
    #: the same way, which is why the family's claim does not rest on CCO.
    #: CCO is only the contrast showing a clean track, and it is read through
    #: the SAME ordinal pairing (`parts.socket_anchors`), never adjacency --
    #: which is the C16 trap. Do not restate that trap as "CCO uses the
    #: interleaved layout and the official clients use the blocked one": it
    #: is a tendency and not a per-client property (CCO measures 261
    #: interleaved against 419 blocked, and both layouts occur in every
    #: install). The real asymmetry is in the MOTION FILES -- on the same
    #: logical path CCO carries `PHY MOTI PHY MOTI` while the official
    #: clients carry bare unnamed `MOTI` chunks -- so read a layout per file
    #: and never infer one from which client you are on.
    BROKEN_LWEAPON_SHAPES = ("001", "002")

    def socket_correction(self, socket, body_appearance):
        """The same female `v_l_weapon` deviation 6090 declares, claimed here
        on this family's own numbers rather than by inheritance.

        **VIEWER-ONLY, and the fence is `docs/parser_plugins.md`'s**: the
        engine does nothing to repair this (four checks, recorded in
        `patch6090.socket_correction`), so the real client very likely draws
        the squash and the compatible-client work must not inherit the
        repair. `plugins/patch6090.py` carries the full account of why
        `reference-basis:cco` and not something cleverer; it is not restated
        here, per the register's rule 1 -- one home, citations elsewhere.
        """
        if socket != "v_l_weapon":
            return None
        if str(body_appearance or "")[:3] not in self.BROKEN_LWEAPON_SHAPES:
            return None
        return ("reference-basis:cco",
                "This client ships the same degenerate v_l_weapon dummy "
                "track as the rest of the official lineage on the female "
                "bodies (001, 002): shortest basis row 0.158 and 0.111 "
                "against CCO's 1.000, measured on this install. The "
                "orientation is taken from CCO's track for the same body, "
                "action and frame, keeping this client's own translation. "
                "A VIEWER DEVIATION, NOT A FIX -- see plugins/patch6090.py.")

    #: What a BODY MESH on this family actually carries, MEASURED rather than
    #: read out of `RolePart.ini`. Census: every distinct `Mesh0` named by
    #: `armor.ini`, resolved to `c3/mesh/<id>.c3` and parsed, counting chunks
    #: whose name is in the install's own `[Dumy]` list:
    #:
    #:     5017   306 distinct Mesh0, 243 present   243 x  v_armet v_l_weapon v_r_weapon
    #:     5065   306 distinct Mesh0, 243 present   243 x  v_armet v_l_weapon v_r_weapon
    #:     5165   360 distinct Mesh0, 295 present   295 x  v_armet v_l_weapon v_r_weapon
    #:
    #: (5517 and 6090 measure 360/295 identically; CCO 769/665, of which 664
    #: carry the same three and one carries none.) `v_body` is not in this
    #: list because it is not in `[Dumy]` -- it is the body geometry itself,
    #: which `attach.is_socket_name` is explicit about.
    #:
    #: **Not one body in this family ships `v_misc`, `v_l_shield`,
    #: `v_r_shield`, `v_mount` or `v_head`** -- all four of the first are
    #: declared in `RolePart.ini [Dumy]` and none of them exists in the art.
    #: That is the same conclusion `patch6090.BODY_SOCKETS` reaches, and it is
    #: claimed here on this family's own census rather than inherited.
    BODY_SOCKETS = ("v_armet", "v_l_weapon", "v_r_weapon")

    #: Slot -> socket. `""` means "this client cannot attach that slot at
    #: all", which is meaningfully different from `None` ("no opinion, use
    #: the app default") -- and `None` is what this family used to return for
    #: every slot, so the app silently fell back on all of them.
    #:
    #: Every entry below follows from the census above, not from the ini.
    SLOT_SOCKETS = {
        "body": None,
        "armet": "v_armet",
        "r_weapon": "v_r_weapon",
        "l_weapon": "v_l_weapon",
        # No body ships `v_l_shield` even though every official
        # `RolePart.ini [Dumy]` declares it. A shield is held in the left
        # hand, so the left-weapon socket is its socket -- the app's old
        # fallback, stated positively. `patch6090` reaches the same answer.
        "shield": "v_l_weapon",
        # Declared in `[Dumy]`, shipped by no body. An accessory has nowhere
        # authored to hang, so say so rather than let it drop to the body
        # origin and draw a trinket in someone's navel.
        "misc": "",
        # `head` is a PART in every official `RolePart.ini [Config]`, and
        # `v_head` is in none of their `[Dumy]` lists -- CCO is the only
        # install here that declares it. No body ships it either.
        "head": "",
        # Inverted, and it does ship -- on the MOUNT, not on the body. The
        # rider hangs off the mount's `v_mount`, which is why no body
        # carrying it is not evidence of absence.
        "mount": "v_mount",
    }

    def slot_socket(self, slot):
        return self.SLOT_SOCKETS.get(slot)

    def sockets_present(self):
        """What the art ships, keyed by socket, with the census as the note.

        `RolePart.ini` lists what the ENGINE understands; this lists what
        exists. They disagree in both directions on every client in this
        family, which is the whole reason the hook exists.
        """
        return {
            "v_armet": "on every body measured (243/243, 243/243, 295/295)",
            "v_l_weapon": "on every body measured; the female track is "
                          "degenerate -- see socket_correction",
            "v_r_weapon": "on every body measured",
            "v_misc": "DECLARED in RolePart.ini [Dumy], shipped by no body",
            "v_l_shield": "DECLARED in RolePart.ini [Dumy], shipped by no body",
            "v_r_shield": "DECLARED in RolePart.ini [Dumy], shipped by no body",
            "v_mount": "on mount meshes, not on bodies -- the attachment is "
                       "inverted, the body follows the mount",
        }

    # -- library import ----------------------------------------------------
    #: Tables this member does NOT ship. Stated per base rather than probed,
    #: so a missing file is a declared absence and not a silent zero.
    ABSENT_HERE: tuple = ()

    #: Body-motion families present **on disk** that this client's own
    #: `3dmotion.ini` does not name. Empty means the table is complete, and
    #: that is a measurement, not a default -- see
    #: `MOTION_FAMILIES_ON_DISK`.
    #:
    #: This is the shadow trap with the shadow removed. On 5517 and 6090 the
    #: plaintext ini names four families and the compiled twin names sixteen,
    #: so the ini under-reports its own table by 75% -- and the twin corrects
    #: it. Here there is no twin. Where the ini is short, nothing is
    #: authoritative but the filesystem.
    UNNAMED_MOTION_FAMILIES: tuple = ()

    #: Every `c3/<family>/` body-motion family this client actually ships,
    #: MEASURED on the filesystem rather than read out of the table under
    #: test. The archives are byte-identical across the whole official
    #: lineage, so a family that differs between these clients differs in the
    #: LOOSE layer and can be counted directly.
    MOTION_FAMILIES_ON_DISK: tuple = ("0001", "0002", "0003", "0004")

    def import_plan(self, root, exists) -> dict:
        """The archives are shared with the whole official lineage -- `c3.wdf`
        md5 `1c16683437bc54d2` and 359,069,116 bytes on all five, `data.wdf`
        392,245,257 -- so an importer that hashes 750 MB per client repeats
        25,013 reads it already did. The difference between patches is the
        loose layer and the `ini/` tables.
        """
        plan = super().import_plan(root, exists)
        plan.update({
            "sharedArchives": True,
            "tables": ["ini/"],
            "note": f"{self.label}: shared archives skipped, loose layer "
                    f"imported, plaintext ini tables read as LIVE "
                    f"(no compiled twins exist)",
        })
        return plan


PLUGIN = PlaintextFamily()
