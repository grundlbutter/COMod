#!/usr/bin/env python3
r"""
patch6609 -- the official Conquer Online patch 6609 client.

**Thin for the same reason `patch5517` is thin, in the other direction.** 5517
is 6090 minus content; 6609 is 6090 **plus** content, and the parse family is
unchanged in both cases. Nothing here switches a 6090 answer off; it adds what
6609 has and states what has not been checked.

MEASURED 2026-08-10 against `Clients\6090` (paths taken as arguments -- the
client trees moved twice while this was being written, and a figure citing a
path is only as good as the path):

    ADDED       56,336     content 56,213 · environment 45 · root 78
    DIFFERING      856     content    805 ·                  root 51
    REMOVED         27     content     13 ·                  root 14

ADDED content is overwhelmingly art -- `data/` 31,994, `c3/` 23,811 -- against
**133 added `ini/` files**, so the parser-relevant surface is small. `ini/` is
133 added, 109 differing, 7 removed.

THE FORMAT IS 6090'S, VERIFIED BY RUNNING THE READERS
-----------------------------------------------------
Not asserted from the file names -- `core/dbc.py` and `core/tqdat.py` were run
against this base:

    armet.dbc     2,793 rows   (6090 2,609)      itemtype.dat  24,269 rows
    armor.dbc     3,754 rows   (6090 3,326)      Monster.dat   37,391 rows
    weapon.dbc   13,292 rows   (6090 11,164)     levexp.dat       203 rows
    mount.dbc     1,223 rows   (6090 1,651)

Both archives are byte-identical to 6090's, as across the whole official
lineage -- `c3.wdf` 359,069,116 bytes and `data.wdf` 392,245,257, md5
`1c16683437bc54d2` / sha256 `ab68f57cc24ae100` for `c3.wdf`. `itemtype.dat` and
`levexp.dat` are byte-identical to 6090's too, and open at the same seeds
(9527, and 1234 for levexp).

WHAT DIFFERS
------------
* **Three fewer compiled tables**: 12 against 6090's 15. `EmotionIco.dbc`,
  `misc.dbc` and `miscmotion.dbc` are 6090-only. The remaining 12 carry the
  same magics (`MESH`, `RSDB`, `SIMO`, `EFFE`) in the same files.
* **50 new top-level `ini/` tables**, and they are **not a new shape**: the new
  `.dat` tables (`ability_score`, `coat_storage_*`, `item_refine_*`,
  `official_type`, ...) are TQ-cipher `@@`-separated text at seed 9527, which
  `tqdat` already reads. This client needs a table list, not a parser.
* **Extensions absent from 6090 entirely**: `.lua` x5, `.pux` x20, `.ttf` x1.
* **The client binary is not at the root.** `Conquer.exe` there is a 41-byte
  stub; the real executables live in `Env_DX8/` and `Env_DX9/`, sibling
  environments of 22 and 23 files carrying their own `Conquer.exe`,
  `C3_CORE_DLL.dll`, `graphic.dll` and `Role3D.dll`. They hold **binaries and
  manifests only** -- no `ini/`, `c3/`, `map/` or `data/` -- so they are 45
  files, and a content diff that lumps them in is counting one client twice
  rather than finding new content. 6090 ships neither directory.
* **6609 is the later client**, despite what file dates suggest: its real
  `Conquer.exe` (in both Env trees) has a PE build stamp of **2017-08-10**
  against 6090's 2015-05-19. Extraction mtimes say the opposite and are not
  evidence.

WHAT IS INHERITED BUT NOT VERIFIED
----------------------------------
Monster colour sets and entity-name corrections come from a visual scan of the
**6090** base. The archives the art lives in are byte-identical here, so they
are inherited rather than dropped -- an inherited set that resolves beats no
answer -- but **nobody has looked at them on 6609**, and the loose layer adds
56,213 content files, so a set may name a texture this client re-skins.
`colour_provenance` says so rather than presenting them as verified.

STILL OPEN
----------
* `shlayout.dat` and `shlayout800x600.dat` **do not open** -- 38.1% printable
  under both known seeds. They neither refuse nor yield sense, which is the
  expensive shape: a reader pointed at one returns noise dressed as a table.
  Not assumed to be TQ-ciphered at an unknown seed; simply unread.
* `mount.dbc` has **428 fewer rows than 6090's** while armor, armet and weapon
  all grew. Content going backwards across a patch is unusual enough to be
  worth a look before anything trusts a mount count here.
* The 20 `.pux` files under `map/PuzzleSave/` (magic `TqTerrain`) and the 5
  `.lua` files are unread by anything in this repo.
"""
from __future__ import annotations

from .catalog import censused

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins.patch6090 import Patch6090, SPECS_6090   # noqa: E402
from plugins.catalog import (                         # noqa: E402
    TableSpec, KIND_AT_ROWS, KIND_SECTIONS)

#: 6609's OWN census, not 6090's with additions assumed. Two differences, and
#: only one of them is content:
#:
#:   * **6609 renames two tables to lowercase.** `monster.dat` and
#:     `magictype.dat`, where 6090 below it and 7878 above it both ship
#:     `Monster.dat` and `MagicType.dat`. The filenames here match what is
#:     actually on disk so the listing shows the real name; `_find_table`
#:     matches case-insensitively regardless, which is what keeps an
#:     exact-case lookup from losing exactly these two tables on exactly this
#:     build.
#:   * It adds the five `item_refine_*` tables, which 6090 does not ship.
SPECS_6609 = tuple(
    s for s in SPECS_6090
    if s.filename not in ("Monster.dat", "MagicType.dat")
) + (
    # `levexp.dat` is NOT re-declared here. It comes through
    # `SPECS_6090` unchanged, and that is a measurement rather than a
    # convenience: this build's copy is byte-identical to 6090's
    # (sha256 df41c576, 4,030 B, 203 rows of five numeric fields), so
    # 6090's refusal is true of these bytes word for word. Declaring a
    # second copy made the subject appear twice and `test_censused`
    # refused it.
    TableSpec("monster", "monster.dat", KIND_SECTIONS, "tq-stream"),
    # **The NAME is column 3, MEASURED, and the plugin-wide `ITEM_COLUMNS`
    # puts it on column 1.** Column 1 is a numeric group id, so `browse magic`
    # listed a column of numbers where the skill names are -- the same defect
    # `MEASURED_SECTION_LABELS` exists to prevent, one grammar over. Column 3
    # is the ONLY column of the 48-53 that is non-numeric on every row
    # (`Thunder`, `Fire`, `Tornado`), which is what makes this a measurement
    # rather than a preference. The 5165 file has one fewer leading id and
    # puts the same field at column 2; see `patch5165`.
    TableSpec("magic", "magictype.dat", KIND_AT_ROWS, "tq-stream",
              columns={"id": 0, "name": 3}),
    TableSpec("item:refine", "item_refine_attr.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:cost", "item_refine_cost.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:effect", "item_refine_effect.dat", KIND_AT_ROWS,
              "tq-stream"),
    TableSpec("item:refine:effect:ex", "item_refine_effect_ex.dat",
              KIND_AT_ROWS, "tq-stream"),
    TableSpec("item:refine:upgrade", "item_refine_upgrade.dat", KIND_AT_ROWS,
              "tq-stream"),
)


def _version(root: Path) -> str:
    """`version.dat`, which every official client stamps with its own patch
    number and nothing else. The cleanest discriminator in the lineage."""
    try:
        return (root / "version.dat").read_bytes()[:8].decode(
            "latin-1").strip()
    except Exception:
        return ""



class Patch6609(Patch6090):
    #: PER-TABLE encoding, MEASURED 2026-09-19 by a strict decode of the
    #: whole file. Declared in full here (a dict does not merge with 6090's):
    #:   OperateActivity.ini       44,869 B, 9,808 high, 2 x 0x85; strict GBK
    #:                             OK, strict UTF-8 fails at byte 18 -> gbk
    #:   TexasChatGUI.ini           2,733 B, 744 high, 17 x 0x85; strict UTF-8
    #:                             OK, strict GBK fails at byte 2 -> utf-8
    #:   TexasChatGUI800X600.ini    2,788 B, same shape -> utf-8
    TABLE_ENCODING = {"operateactivity.ini": "gbk",
                      "texaschatgui.ini": "utf-8",
                      "texaschatgui800x600.ini": "utf-8"}

    name = "patch6609"
    label = "Official patch client 6609"
    origin = "official"
    aliases = ("6609",)

    #: **Re-measured on 6609's own `ini/`, not inherited.** The four tables
    #: 6090 declares are here too and the columns hold; `GoldenLeagueShop.ini`
    #: is not shipped by this build, so it is absent rather than carried over
    #: from the superclass.
    #:
    #:   region.ini         258 rows, 14 fields; column 6 non-numeric on
    #:                      258/258, 78 distinct; column 1 numeric on all 258.
    #:   EventTypeName.ini  24 rows; column 2 non-numeric on 24/24; column 0
    #:                      is one distinct value, column 1 is 24.
    #:   VipTrans.ini       31 rows; column 2 non-numeric on 31/31.
    #:   restrain.ini       5 rows; column 2 non-numeric on 5/5.
    ROW_COLUMNS = {
        "region": {"id": 0, "name": 6},
        "eventtypename": {"id": 1, "name": 2},
        "viptrans": {"id": 0, "name": 2},
        "restrain": {"id": 0, "name": 2},
    }

    def table_specs(self, root):
        # Curated specs plus every `.ini` the grammar census
        # settled -- see `plugins/catalog/censused.py`. A curated spec
        # always wins on subject and filename.
        return censused.extend(SPECS_6609, self.name)
    notes = ("Same parse family as 6090 -- compiled .dbc twins, TQ-cipher "
             "itemtype/Monster .dat at seed 9527, byte-identical c3.wdf and "
             "data.wdf -- with 56,336 files added and 856 changed. Three "
             "compiled tables fewer (EmotionIco, misc, miscmotion). The 50 "
             "new ini/ tables are TQ-cipher @@ text the existing reader "
             "already handles, not a new format. Monster colour and name "
             "data is inherited from the 6090 scan and has NOT been verified "
             "here. shlayout.dat does not open under either known seed.")

    #: 6090 ships these; 6609 does not. Named so a caller can tell "this
    #: client has no such table" from "we failed to read it" -- the two are
    #: different events and only one is a defect.
    ABSENT_HERE = ("ini/EmotionIco.dbc", "ini/misc.dbc", "ini/miscmotion.dbc")

    #: The real client binaries, because `Conquer.exe` at the root is a
    #: 41-byte stub. Anything probing the executable must look here.
    ENV_TREES = ("Env_DX8", "Env_DX9")

    def confidence(self, root, exists) -> float:
        """`version.dat` decides, because the formats cannot.

        6609 answers yes to every format probe 6090 does -- same compiled
        magics, same cipher, byte-identical archives -- so a format-based
        claim here would tie with `patch6090` and be broken by dictionary
        order, which is not evidence. `Patch6090.confidence` already returns
        0.45 for a stamped sibling, so claiming 0.95 on our own stamp wins
        cleanly without weakening its claim on its own base.
        """
        if _version(Path(root)) == "6609":
            return 0.95
        return 0.0

    def colour_provenance(self):
        """Inherited from the 6090 scan and **not checked here**.

        `Patch6090.colour_provenance` returns "authored", and in that one
        place the word means a person eyeballing 36 monster directories on
        *that* base. A subclass inherits the sets; it does not inherit the
        afternoon somebody spent looking. 5517 earned a stronger word by
        measuring that 38 of 39 directories resolve identically -- no such
        pass has been run on 6609, and this client adds 23,811 `c3/` files,
        so the inherited sets have more room to be wrong here than there.
        """
        return "colourway inherited from 6090 (unverified here)", "inferred"

    def table_quirks(self):
        """6090's quirks all apply -- verified by running the readers -- so
        they are inherited rather than restated. These are 6609's own."""
        q = super().table_quirks()
        q["three fewer compiled tables"] = (
            "12 .dbc against 6090's 15: EmotionIco.dbc, misc.dbc and "
            "miscmotion.dbc are 6090-only. The other 12 carry the same "
            "magics, and both .wdf archives are byte-identical to 6090's.")
        q["shlayout.dat does not open"] = (
            "shlayout.dat and shlayout800x600.dat decrypt to 38.1% printable "
            "under seeds 9527 and 1234 -- noise, not text, where every other "
            "new .dat table on this client opens at 100%. They do not raise "
            "either, so a reader that assumes the family cipher returns "
            "garbage that looks like a parsed table. Unread, not decoded.")
        q["mount.dbc shrank"] = (
            "1,223 rows against 6090's 1,651, while armor (3,754/3,326), "
            "armet (2,793/2,609) and weapon (13,292/11,164) all grew. A "
            "later patch shipping fewer mounts is odd enough that a mount "
            "count taken here should be confirmed before it is trusted.")
        q["client binary is not at the root"] = (
            "Conquer.exe at the root is a 41-byte stub; the real "
            "executables are in Env_DX8/ and Env_DX9/, which each carry a "
            "full binary set (Conquer.exe, C3_CORE_DLL.dll, graphic.dll, "
            "Role3D.dll) and no asset trees at all. Probing the root "
            "executable finds a stub and probing either tree finds the same "
            "client twice.")
        q["loose .DMap files are decoys (Explorer, 2026-08-10)"] = (
            "NOT MEASURED HERE -- recorded so nobody concludes otherwise "
            "from a loose-file walk. Explorer measured 6609's 184 loose "
            "map/map/*.DMap as byte-identical to 6090's, with the real map "
            "delta inside .7z archives beside them (25 differing, 50 new, "
            "independently reproduced by this plugin's content diff). 113 of "
            "6609's maps ship archive-only. A loose-file diff therefore "
            "reports 'maps unchanged' with every audible channel silent, "
            "because a stale map is a valid map. core/dmap.py is Explorer's.")
        return q


PLUGIN = Patch6609()
