r"""
build_addon.py -- vendor the pure-stdlib C3 modules into the Blender addon and
zip it.

`core/` and `tools/` are the single source of truth for the format code.  A
Blender addon has to be self-contained, so the build copies those modules into
`blender/io_scene_c3/vendor/` and rewrites their imports into package-relative
ones.  Every rewrite is asserted, so a source change that invalidates one
breaks the build loudly.

**That is not, by itself, protection against shipping a stale copy, and this
docstring used to claim it was** -- see `docs/CORRECTIONS.md`
`C-2026-08-09-comod-vendor-drift-gate`.  The vendored copies are committed, so
a refusing build leaves the previous ones in place; `coroot.py` sat 496 lines
behind its source while this file exited 1 on every run, because nothing ran
it.  What actually protects the tree is `tests/test_vendor_sync.py`, which
re-runs `vendor()` into a temp dir and diffs.  **If you add a rewrite here, or
a module to `VENDORED`, that test is what proves it landed.**

Note that the assertions only cover *declared* rewrites.  A lazy import in a
function body is not in this table and will not break the build -- it fails
inside the addon, and at two sites in `coassets`/`dbcshadow` it is swallowed
by a bare `except`.  `test_vendor_sync` walks the AST for exactly that.

    py -3 tools/build_addon.py            # vendor + zip
    py -3 tools/build_addon.py --no-zip   # vendor only (for live editing)

Output: build/io_scene_c3.zip
"""

from __future__ import annotations

import ast
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
#: The shared foundation. Half the vendored modules live here now -- see
#: `core/README.md` and `docs/repo_split.md`. Blender ships its own Python and
#: cannot see this tree at all, which is why the addon vendors copies rather
#: than depending on COre like every other consumer does.
CORE = REPO / "core"
ADDON = REPO / "blender" / "io_scene_c3"
VENDOR = ADDON / "vendor"
BUILD = REPO / "build"


def _source_of(name: str) -> Path:
    """Where a vendored module actually lives, `core/` before `tools/`.

    Searched rather than hardcoded per module so that moving one more file into
    COre does not silently produce a stale vendored copy -- the failure mode
    would be an addon that works today and breaks after the split.
    """
    for cand in (CORE / name, TOOLS / name):
        if cand.is_file():
            return cand
    raise SystemExit(f"build_addon: {name} is in neither core/ nor tools/")

# module -> list of (old, new) import rewrites that MUST all apply
VENDORED = {
    "c3phy.py": [],
    # Where a bone belongs, derived from the skin (`core/boneplace.py`).
    #
    # VENDORED BECAUSE THE MATHS MUST BE TESTABLE WITHOUT BLENDER.  The
    # placement is the part that can be silently wrong -- a bad principal axis
    # lays a forearm bone across the arm instead of along it, which still
    # imports, still poses and still exports byte-exactly.  Putting it in
    # `c3_import.py` would have hidden it behind that module's `import bpy`,
    # so the only check available would have been opening Blender and looking.
    # `tests/test_boneplace.py` imports THIS copy out of `vendor/`.
    #
    # NO REWRITES, and the empty list is the answer rather than an oversight:
    # it imports `math` and nothing of ours, and it is duck-typed on
    # `PhyVertex` precisely so it does not have to import `c3phy`.
    "boneplace.py": [],
    # The bone HIERARCHY for that placement (`core/bonetree.py`).
    #
    # VENDORED FOR THE SAME REASON AS `boneplace`: the derivation is the part
    # that can be silently wrong. A tree that parents the wrong bone still
    # imports, still poses and still exports -- it just drags the wrong limb,
    # and on a 25-bone mesh nobody can eyeball whether bone_042's parent
    # should be bone_001. `tests/test_bonetree.py` imports THIS copy.
    #
    # NO REWRITES: it imports nothing at all, not even `math`, and is
    # duck-typed on `PhyVertex` so it never reaches for `c3phy`.
    "bonetree.py": [],
    # The skeleton SOLVED from a multi-frame motion set (`core/bonejoint.py`).
    #
    # VENDORED because it is the only thing in the addon that can tell an
    # articulation from a coincidence, and it is pure arithmetic: a least
    # squares over every frame for the point both bones carry to the same
    # place. Imports nothing, not even `math`.
    "bonejoint.py": [],
    # WHICH clips that solve is measured on (`core/motionpool.py`).
    #
    # VENDORED BECAUSE THE CHOICE OF EVIDENCE DECIDES THE ANSWER, and it is the
    # half of the skeleton solve that `bonejoint` cannot check. The importer
    # used to take the first ten loose files, which on CCO body 0003 means 21
    # of the 287 clips that install actually ships and not one of the unarmed
    # set. Reaching the other 266 WITHOUT ranking them for articulation
    # coverage inverts body 0003's head and neck. Both halves are silent --
    # the report says "solved from 10 motion file(s)" either way -- so the
    # only protection is a test that owns the selection, and
    # `tests/test_motionpool.py` imports THIS copy.
    #
    # THREE REWRITES, one of them a LAZY import inside `motion_rows`. The `dbc`
    # one is the reason this table is asserted rather than best-effort: a
    # function-body import is invisible to the addon until the code path runs,
    # and on a 5517-era base that path runs on the FIRST body import.
    #
    # It deliberately does NOT import `effects` for the MOTI parse -- COre may
    # not reach into `tools/` (`tools/test_viewer.py::CoreBoundary`), so the
    # parse arrives as a callable from `c3_import._load_moti`.
    "motionpool.py": [
        ('import bonejoint\nimport dbcshadow',
         'from . import bonejoint\nfrom . import dbcshadow'),
        ('        import dbc as dbcmod', '        from . import dbc as dbcmod'),
    ],
    # Anatomical NAMES for that solved skeleton (`core/bonelabel.py`).
    #
    # VENDORED FOR THE SAME REASON AS ITS THREE NEIGHBOURS: the classification
    # is the part that can be silently wrong, and wrong here is worse than
    # missing. A skeleton rooted at the PELVIS instead of the chest labels the
    # legs as arms -- 20 confident names, anatomically inverted, with the
    # coverage number still reading 20 of 25. That happened on body 0002 and
    # only a test that owns the geometry catches it.
    #
    # NO REWRITES: it imports nothing at all and is duck-typed on plain dicts,
    # so it never reaches for `bonejoint` or `c3phy`.
    "bonelabel.py": [],
    # The `ccflag` sidecar's field decode -- CCFL kind 13 (the mesh scroll
    # period) and kind 3 (the particle atlas COLUMN count).
    #
    # VENDORED 2026-09-16 BECAUSE `effects.py` NOW IMPORTS IT, and the addon
    # ships `effects.py`. It was not needed before because nothing on the
    # draw path read a CCFL at all: the chunk was parsed in tests and by
    # nobody else, so kind 3 could not have been honoured even after it was
    # decoded.
    #
    # NO REWRITES, and the empty list is the honest answer rather than an
    # oversight: `core/c3ccfl.py` imports `struct` and `dataclasses` and
    # nothing of ours, so there is no statement here to respell.
    "c3ccfl.py": [],
    # The install-root resolver.  Vendored so the addon locates the game the
    # same way every CLI does, instead of carrying its own hardcoded path.
    #
    # STILL no rewrites, and its two project imports are the reason to say so
    # out loud rather than leave the empty list looking untouched. `coroot`
    # reaches `verdict` and `profilecheck` through `coroot._sibling`, which
    # resolves them relative to whatever package it finds itself in, so there
    # is no import statement here to rewrite. That is not a dodge of the
    # rewrite table: a *statement* has one correct spelling per import style
    # and this module is imported three ways (see `_sibling`), so a rewritten
    # statement would fix the addon and break `from core import coroot`.
    "coroot.py": [],
    # The three-state safety answer the override gate returns. Pure stdlib and
    # imports nothing from the project, so no rewrites.
    #
    # WHY THESE TWO ARE VENDORED AT ALL: the addon reads name tables ONLY
    # through a user-set override -- `_repo_dir()` is None once the zip is
    # installed, so there is no checkout to fall back to -- which makes it the
    # consumer most exposed to an unverified table AND the one most damaged by
    # a gate that cannot run. Unvendored, the `profilecheck` lookup misses, the
    # gate answers UNKNOWN, and inside a verifier's domain UNKNOWN fails
    # closed: every supplied name table in Blender refused. Correct,
    # fail-safe, and a regression.
    "verdict.py": [],
    # The name-table verifier the gate consults. `main()` is unreachable from
    # the addon (and exempt from the vendored-import walk), but `check` and
    # `verdict` are exactly what runs there.
    "profilecheck.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent '
         '/ "core"))\n'
         'import coroot                                            '
         '# noqa: E402\n'
         'from tqhash import tq_hash                               '
         '# noqa: E402\n'
         'from wdf import WdfArchive                               '
         '# noqa: E402',
         'from . import coroot                                     '
         '# noqa: E402\n'
         'from .tqhash import tq_hash                              '
         '# noqa: E402\n'
         'from .wdf import WdfArchive                              '
         '# noqa: E402'),
    ],
    # The MOTI reader/writer -- the reason the addon can animate at all.
    # Vendored WHOLE rather than split: `parse_moti`/`serialize_moti` are
    # byte-exact over 1,404,924 chunks AS THEY STAND, and lifting two functions
    # into a smaller module would fork the thing that validation is about.
    # All five of its project imports are already vendored above and below, so
    # the cost of taking the whole file is size and nothing else.
    "effects.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n'
         '\n'
         'from coassets import AssetRoot, C3File, DEFAULT_ROOT          '
         '# noqa: E402\n'
         'import coroot                                                  '
         '# noqa: E402\n'
         'import c3ccfl                                                  '
         '# noqa: E402\n'
         'import c3phy                                                   '
         '# noqa: E402\n'
         'import dbc                                                     '
         '# noqa: E402\n'
         'import dbcshadow                                               '
         '# noqa: E402',
         'from .coassets import AssetRoot, C3File, DEFAULT_ROOT         '
         '# noqa: E402\n'
         'from . import coroot                                          '
         '# noqa: E402\n'
         'from . import c3ccfl                                          '
         '# noqa: E402\n'
         'from . import c3phy                                           '
         '# noqa: E402\n'
         'from . import dbc                                             '
         '# noqa: E402\n'
         'from . import dbcshadow                                       '
         '# noqa: E402'),
        # `c3.wdb` is read lazily at TWO sites and both alias to `_wdb`.
        # MEASURED 2026-09-16: master carried both imports with NO entry
        # here, so `test_vendor_sync` was RED ON MASTER -- the addon would
        # ship an `effects.py` importing a bare `wdb` it cannot resolve.
        # Same shape as the `block96` note above, caught the same way. The
        # two literals differ only in INDENTATION (20 vs 16) and in the
        # padding before the comment; that is what makes them two distinct
        # strings rather than one string replaced twice.
        ('                    import wdb as _wdb                      '
         '# noqa: PLC0415\n',
         '                    from . import wdb as _wdb               '
         '# noqa: PLC0415\n'),
        ('                import wdb as _wdb                          '
         '# noqa: PLC0415\n',
         '                from . import wdb as _wdb                   '
         '# noqa: PLC0415\n'),
    ],
    # MOTI <-> armature-action conversion: every line of the addon's animation
    # maths, deliberately outside `bpy` so `tests/test_moti_action.py` can run
    # it with no Blender at all. `blender/io_scene_c3/c3_anim.py` is the glue
    # that imports it and holds none of it.
    #
    # The rewrite swallows `from pathlib import Path` along with the two
    # bootstrap lines, because inside the package nothing else in that module
    # uses `Path` -- see the comment above the block in the source.
    "c3anim.py": [
        ('from pathlib import Path\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n'
         '\n'
         'from effects import (Motion, MotionKey, MotiWriteError, parse_moti,'
         '  # noqa: E402\n'
         '                     quat_to_matrix, serialize_moti)',
         'from .effects import (Motion, MotionKey, MotiWriteError,'
         '     # noqa: E402\n'
         '                      parse_moti, quat_to_matrix, serialize_moti)'),
    ],
    "c3write.py": [
        # Both bootstrap lines go: inside the addon these are a package, so
        # there is no sys.path to fix up and nothing named `core/` to find.
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n\n'
         'from c3phy import (',
         'from .c3phy import ('),
    ],
    "tqhash.py": [],
    "wdf.py": [
        # `read_by_name` imports this lazily, so the unrewritten form does not
        # break the addon at import time -- it raises ImportError on the first
        # name-keyed archive read instead, which is much later and reads as a
        # missing asset rather than a build fault.
        ('        from tqhash import tq_hash  # local import; optional dependency',
         '        from .tqhash import tq_hash  # local import; optional dependency'),
    ],
    # The compiled-table reader (MESH / RSDB). Only ever imported lazily, and
    # at both call sites the failure is swallowed -- see the rewrites below --
    # so leaving it unvendored does not raise anywhere; it just quietly costs
    # the addon every compiled table on 5517+.
    "dbc.py": [],
    # Path confinement. Vendored because `coassets.locate()` calls it on every
    # lookup, so the addon would not import without it.
    "safepath.py": [],
    # The compiled-twin guard. `coassets.parse_ini()` calls it on every ini
    # read, so the addon would not import without it -- and without it the
    # addon would read 5517+ plaintext inis the client itself ignores.
    "dbcshadow.py": [
        # Swallowed by `except Exception: return None` -- unrewritten, every
        # RSDB path table silently reads as absent.
        ('        import dbc                                        '
         '# noqa: E402,PLC0415',
         '        from . import dbc                                 '
         '# noqa: E402,PLC0415'),
    ],
    # The TQ-cipher .dat reader. Official clients ship `ini/itemtype.dat`
    # rather than the community client's `ini/itemtype.json`, and
    # `coassets.load_items()` falls back to it, so the addon needs it to see
    # item names on the five official roots.
    "tqdat.py": [],
    # The `.dat` family classifier. Pure stdlib apart from `tqdat`, which is
    # vendored above. Vendored because `block96` below asks it whether a file
    # is really block96 before applying the block dictionary to it -- the
    # guard against the eleven poisoned dictionary pairs that decode at 100%
    # coverage into ciphertext. Without it the addon would have to choose
    # between no 6907+ item names and possibly-garbage ones.
    "inidat.py": [
        ('import tqdat', 'from . import tqdat'),
    ],
    # The 6907..7878 `.dat` reader. `coassets.load_items()` falls back to it
    # for every client from 6907 up, where the TQ cipher no longer applies --
    # which is 11 of the 31 installs this project holds, so leaving it
    # unvendored would give the addon no item names on a third of them.
    "block96.py": [
        ('import inidat', 'from . import inidat'),
    ],
    # The client's own resource database, `ini/c3.wdb`: asset id -> file path.
    # Pure stdlib (argparse, struct, sys, pathlib), so no rewrites.
    #
    # Vendored because `coassets.AssetRoot.resolve_declared` reads it, and that
    # is the ONLY route to the role art this client keeps two levels deep under
    # a filename that is not the id -- `c3/mount/801/8010000.c3`,
    # `c3/mountsaddle/801/8010012.c3`, `c3/monster/103/1.c3`. MEASURED on 6907:
    # those classes resolve 1 time in 2,847 refs without it and 2,807 with it.
    # Unvendored, `resource_db`'s lazy `import wdb` raises ImportError, and it
    # is NOT swallowed there (the except is (ValueError, struct.error,
    # OSError), deliberately, for the same reason `PartIni`'s is narrow), so
    # the addon would fail hard on the first mount -- which is the loud half of
    # the two failure modes this table exists to prevent.
    # `wdb` gained a `dbc` dependency on 2026-09-05: walking every section of
    # c3.wdb needs the per-magic record shapes, and those live in `dbc` so the
    # container reader and the record reader do not each carry a copy. `dbc` is
    # already vendored above, so inside the addon they are siblings in one
    # package and the absolute import has to become a relative one.
    #
    # The pre-commit hook caught this on the commit that added the import --
    # unvendored, the addon would have shipped a `wdb.py` that raises
    # ModuleNotFoundError on import inside Blender, which is a failure a long
    # way from the change that caused it.
    "wdb.py": [("import dbc", "from . import dbc")],
    # The DatPkg (.tpd/.tpi) reader. Pure stdlib, no rewrites needed. Vendored
    # since `coassets.AssetRoot` learned to discover archives by suffix
    # (Parser's _discover_archives): without it the vendored coassets fails at
    # import on `from .tpd import TpdArchive`, which is how master went red on
    # test_vendor_sync the day the discovery landed.
    "tpd.py": [],
    "coassets.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n\n'
         'import coroot                       # noqa: E402\n'
         'import dbcshadow                    # noqa: E402\n'
         'import safepath                     # noqa: E402\n'
         'import tqdat                        # noqa: E402\n'
         'from tqhash import tq_hash          # noqa: E402\n'
         'from tpd import TpdArchive          # noqa: E402\n'
         'from wdf import WdfArchive          # noqa: E402',
         'from . import coroot                 # noqa: E402\n'
         'from . import dbcshadow              # noqa: E402\n'
         'from . import safepath               # noqa: E402\n'
         'from . import tqdat                  # noqa: E402\n'
         'from .tqhash import tq_hash          # noqa: E402\n'
         'from .tpd import TpdArchive          # noqa: E402\n'
         'from .wdf import WdfArchive          # noqa: E402'),
        # `PartIni.__init__` swallows this one with `except Exception: pass`
        # and falls through to the stale plaintext ini. Unrewritten, the addon
        # silently reads the 2008-era table on every 5517+ client, with no
        # error to notice. MEASURED on 5517 through the vendored package:
        # armet 1168 appearances unrewritten vs 1909 from the twin, armor 955
        # vs 2357. This is the rewrite whose absence is least visible.
        ('        import dbc\n',
         '        from . import dbc\n'),
        # `_load_items_block96` imports this lazily. Unrewritten it raises
        # ImportError inside `load_items` on every 6907+ client -- not
        # swallowed, so it would be a hard failure in the addon rather than a
        # quiet one, but a failure all the same.
        ('    import block96                                        '
         '# noqa: PLC0415\n',
         '    from . import block96                                 '
         '# noqa: PLC0415\n'),
        # `resource_db` imports this lazily, and its `except` is narrow on
        # purpose, so an unrewritten form raises ImportError out of the addon
        # on the first id `resolve_declared` is asked about -- loud, not
        # quiet, and still a build fault rather than a missing asset.
        ('                    import wdb\n',
         '                    from . import wdb\n'),
        # `_wdb_module()` -- the c3.wdb disk cache's ONLY import site, and kept
        # to one deliberately. This table replaces a literal ONCE, so two
        # identical `import wdb` lines collide into a single rewrite and ship a
        # vendored file that cannot import; the generator refused exactly that
        # and is why the cache has one accessor instead of two imports. A
        # distinct literal from the entry above -- 8 spaces and a noqa against
        # 20 spaces -- so neither rewrite can consume the other's line.
        ('        import wdb                                          # noqa: PLC0415\n',
         '        from . import wdb                                   # noqa: PLC0415\n'),
        # `object_db` imports this lazily to read `ini/3DObj.dbc`, the
        # compiled twin of `ini/3dobj.ini`. A SECOND lazy `import dbc` in
        # this module, and it needs its own entry because the table replaces
        # a LITERAL: `PartIni`'s site is indented 8 and this one 24, so the
        # two strings are distinct and neither rewrite can stand in for the
        # other. Unrewritten, the `except Exception` around it falls back to
        # the plaintext ini -- which is the decoy `dbcshadow.check_ini`
        # exists to refuse, so the addon would answer out of a frozen table
        # with nothing raised. Same silent shape as the `PartIni` entry above
        # and for the same reason.
        # Re-indented 2026-09-18: the object_db body moved into
        # `_load_id_path_table` (shared with the new `texture_db`), one level
        # shallower, so the literal moved with it -- a stale literal here
        # would leave the vendored import unrewritten, silently.
        ('                        import dbc                  '
         '# noqa: PLC0415\n',
         '                        from . import dbc           '
         '# noqa: PLC0415\n'),
        # `_item_disk_key` imports it under an ALIAS so the two lazy
        # imports are distinct strings. They were identical for one
        # commit, and this table replaces a literal -- it rewrote the
        # first and left the second, which `test_vendor_sync` caught as
        # an unrewritten repo import. Two call sites needing the same
        # lazy module need two entries, or one name each.
        ('    import block96 as _b96                                '
         '# noqa: PLC0415\n',
         '    from . import block96 as _b96                         '
         '# noqa: PLC0415\n'),
    ],
    # The MESH -> TEXTURE PAIRING WORK (tools/meshtex.py), so the addon
    # answers with the SAME ranked rules the CLI does instead of a flat
    # first-hit list. 13 named methods, each with a MEASURED confidence:
    # `bodytype_dir` 0.98, `appearance_table` 0.95, down to `dir_sibling` at
    # 0.30, which its own docstring calls a shortlist rather than an answer.
    #
    # TWO OF ITS IMPORTS ARE DELIBERATELY NOT VENDORED. Both are LAZY and both
    # call sites were READ rather than assumed:
    #   * `dds` imports PIL and numpy. The addon's whole promise is pure
    #     stdlib plus what Blender ships, and Blender loads DDS natively, so
    #     vendoring it would trade the promise for nothing. Its one call site
    #     is a texture-VERIFICATION helper, not the matching path.
    #   * `assetdiff` imports the entire `plugins` package. Its call site is
    #     already `try/except` -> `prof = None`, falling back to the default
    #     table profile.
    # The cost is stated in `c3_material.ranked_matches`, where a caller can
    # see it, rather than left to surface as an ImportError.
    "meshtex.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n\n'
         'from coassets import AssetRoot, C3File, DEFAULT_ROOT, parse_ini   '
         '# noqa: E402\n'
         'from wdf import detect_magic                                     '
         '# noqa: E402\n'
         'import c3phy                                                      '
         '# noqa: E402\n'
         'import coroot                                                     '
         '# noqa: E402\n'
         'import provenance                                                 '
         '# noqa: E402',
         'from .coassets import AssetRoot, C3File, DEFAULT_ROOT, parse_ini   '
         '# noqa: E402\n'
         'from .wdf import detect_magic                                     '
         '# noqa: E402\n'
         'from . import c3phy                                               '
         '# noqa: E402\n'
         'from . import coroot                                              '
         '# noqa: E402\n'
         'from . import provenance                                          '
         '# noqa: E402'),
        # `npcart` IS vendored (0.95 authored method; see below).
        ('            import npcart                                '
         '# noqa: PLC0415',
         '            from . import npcart                         '
         '# noqa: PLC0415'),
        # `tqhash` IS vendored. #234 added this lazy import for the
        # unclaimable-hash count: C3 content in a hash-only WDF that no
        # path in the universe produces, counted so it is not invisible.
        ('        from tqhash import tq_hash                      '
         '# noqa: PLC0415',
         '        from .tqhash import tq_hash                     '
         '# noqa: PLC0415'),
        # NOT VENDORED, and the raise is the point: the call site is already
        # `try/except` -> `prof = None`, so this reproduces the documented
        # degradation exactly while saying WHY at the place someone reading
        # the vendored copy would ask. `assetdiff` imports the whole `plugins`
        # package; the fallback is the default table profile.
        ('                from assetdiff import table_profile_for   '
         '# noqa: PLC0415',
         '                raise ImportError(                        '
         '# noqa: PLC0415\n'
         '                    "assetdiff is not vendored: it imports the "\n'
         '                    "whole plugins package. The caller falls back "\n'
         '                    "to the default table profile.")'),
        # NOT VENDORED for the same kind of reason: `dds` imports PIL and
        # numpy, and the addon is pure stdlib plus what Blender ships. This
        # call site is a texture-VERIFICATION helper, not the matching path,
        # and it already records the failure as a note on its result.
        ("            import dds as _dds",
         '            raise ImportError(\n'
         '                "dds is not vendored: it imports PIL and numpy, and "\n'
         '                "Blender loads DDS natively. Texture verification is "\n'
         '                "unavailable in the addon; matching is unaffected.")'),
    ],
    # `meshtex` imports this at module level; its only dependency is `coroot`,
    # already vendored.
    "provenance.py": [
        ("import coroot", "from . import coroot"),
    ],
    # The NPC art resolver `meshtex` routes `npc_table` through -- a 0.95
    # AUTHORED method, so dropping it would make the addon's answer quietly
    # worse than the CLI's for every NPC mesh. Its only dependency is `dbc`,
    # already vendored, and both of its imports are lazy and indented.
    "npcart.py": [
        ("                import dbc", "                from . import dbc"),
        ("            import dbc", "            from . import dbc"),
    ],
    "c3tex.py": [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))\n\n'
         'from coassets import AssetRoot, Located, DEFAULT_ROOT, dds_info'
         '   # noqa: E402',
         'from .coassets import AssetRoot, Located, DEFAULT_ROOT, dds_info'
         '   # noqa: E402'),
    ],
}

VENDOR_INIT = '''"""
Vendored from the CO Client RE project's `tools/` directory by
`tools/build_addon.py`.  **Do not edit these files here** -- edit them in
`tools/` and re-run the build; the only difference is that their top-level
imports were rewritten to be package-relative.

Every module in this package is pure stdlib and imports no `bpy`, which is what
lets the corpus round-trip test (`tests/test_roundtrip.py`) run the exact same
code on bare system Python.
"""
'''


def _write_lf(path: Path, text: str) -> None:
    r"""Write `text` as UTF-8 with LF endings, in BINARY mode.

    `Path.write_text()` is text mode, so on Windows every `\n` goes out as
    `\r\n`.  The files this module writes are then COMMITTED, and the repo
    stores them LF, so a text-mode write makes the build's own output differ
    from its own committed copy on every run.

    Measured 2026-08-31: a `vendor()` run emitted CRLF on all 17 files (CR
    count equal to LF count on every one) against LF-only blobs, and
    `git -c core.autocrlf=false status` then showed **17 modified, 11,892
    insertions / 11,892 deletions, and nothing at all under
    `--ignore-cr-at-eol`** -- a diff whose entire content is line endings.

    It stayed invisible here because `core.autocrlf=true` on this box converts
    on the way back in, so `git status` reads clean.  It is NOT invisible on a
    clone with `autocrlf=false` (git's default off Windows), which is where the
    Senior Director hit it four times resolving vendor conflicts.  Nothing in
    the tree pins the endings: `git check-attr text eol` on a vendored file
    answers `unspecified` for both, and `.gitattributes` holds one unrelated
    line.  The masking is a property of one machine's config, not of the repo.
    """
    path.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n")
                     .encode("utf-8"))


def _repo_modules() -> set:
    """Every module name that exists only inside this repo."""
    return {q.stem for d in ("core", "tools") for q in (REPO / d).glob("*.py")}


def _absolute_repo_imports(text: str, repo_mods: set) -> list:
    """`[(lineno, statement)]` for repo imports the addon cannot resolve.

    Inside the addon the vendored tree is a package with no useful `sys.path`
    entry, so `import dbc` cannot resolve and `from . import dbc` is the only
    spelling that works. Walks the whole AST rather than the header, because
    every gap found so far has been a LAZY import in a function body.

    `main`/`_main` are exempt for the same reason `test_vendor_sync` exempts
    them: a vendored module's CLI entry point is unreachable from the addon and
    does its own path setup first.
    """
    out = []

    def _fallback_names(try_node) -> set:
        """Modules the `try` arm imported via `from ... import X`.

        These are the ONLY names an `except ImportError` arm may re-import
        absolutely -- see `walk` below for why that arm is legitimate.
        """
        names = set()
        for stmt in ast.walk(try_node):
            if isinstance(stmt, ast.ImportFrom):
                for a in stmt.names:
                    names.add(a.asname or a.name)
        return names

    def walk(node, in_cli, exempt=frozenset()):
        for child in ast.iter_child_nodes(node):
            nested = in_cli
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested = in_cli or child.name in ("main", "_main")
            elif isinstance(child, ast.Try):
                # THE VENDOR/SOURCE FALLBACK, WHICH IS CORRECT AND WAS BEING
                # REPORTED AS A DEFECT:
                #
                #     try:    from core import tqdat
                #     except ImportError:  import tqdat
                #
                # A module that must work BOTH vendored (where `from core` does
                # not exist) and in the repo (where the bare name does not
                # resolve from the addon) has to write both arms. Reading the
                # second arm alone calls the correct idiom broken -- it made
                # master red on `block96.py` and this walker was one of the two
                # instruments saying so.
                #
                # The exemption is NARROW ON PURPOSE: only the names the `try`
                # arm actually imported, and only inside a handler that catches
                # ImportError. A bare `import dbc` anywhere else, or a fallback
                # for a module the try arm never mentioned, is still reported.
                names = _fallback_names(child)
                walk(ast.Module(body=list(child.body + child.orelse
                                          + child.finalbody),
                                type_ignores=[]), nested, exempt)
                for h in child.handlers:
                    caught = h.type
                    ids = set()
                    if isinstance(caught, ast.Name):
                        ids = {caught.id}
                    elif isinstance(caught, ast.Tuple):
                        ids = {e.id for e in caught.elts if isinstance(e, ast.Name)}
                    elif caught is None:
                        ids = {"ImportError"}          # bare except catches it
                    inner = exempt | names if ids & {"ImportError", "ModuleNotFoundError"} else exempt
                    walk(ast.Module(body=list(h.body), type_ignores=[]),
                         nested, inner)
                continue
            elif not in_cli:
                if isinstance(child, ast.Import):
                    for a in child.names:
                        base = a.name.split(".")[0]
                        if base in repo_mods and base not in exempt:
                            out.append((child.lineno, "import " + a.name))
                elif isinstance(child, ast.ImportFrom):
                    mod = (child.module or "").split(".")[0]
                    if child.level == 0 and mod in repo_mods and mod not in exempt:
                        out.append((child.lineno, "from " + mod + " import ..."))
            walk(child, nested, exempt)

    walk(ast.parse(text), False)
    return out


def vendor(dest: Path = VENDOR) -> list[str]:
    """Write the vendored tree into `dest`, returning the names written.

    `dest` is a parameter so `tests/test_vendor_sync.py` can generate into a
    temp dir and compare against the committed tree without reaching in and
    rebinding this module's globals.  Everything else should call it with no
    argument.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    repo_mods = _repo_modules()
    _write_lf(dest / "__init__.py", VENDOR_INIT)
    written = ["vendor/__init__.py"]
    for name, rewrites in VENDORED.items():
        source = _source_of(name)
        src = source.read_text(encoding="utf-8")
        for old, new in rewrites:
            if old not in src:
                raise SystemExit(
                    f"build_addon: rewrite for {name} no longer matches.\n"
                    f"  looked for:\n{old!r}\n"
                    f"  Fix the table in tools/build_addon.py.")
            src = src.replace(old, new, 1)
        # THE GENERATOR CHECKS ITS OWN OUTPUT BEFORE WRITING IT.
        #
        # Refusing when a rewrite matches NOTHING (above) is not enough, and
        # that gap cost three incidents in one day. `str.replace(old, new, 1)`
        # rewrites the FIRST occurrence: when `core/coassets.py` grew a second
        # lazy `import block96` byte-identical to the first, the table rewrote
        # one and left the other -- and `old` was still present, so nothing
        # raised. Before that, the same import differed only in COMMENT
        # WHITESPACE, so the table matched the OTHER site and again said
        # nothing. Both emitted a vendored file that cannot import inside
        # Blender, and both were caught only by `test_vendor_sync`, at gate-run
        # time -- after the commit, and once after the merge that made master
        # red for two hours.
        #
        # So the output is parsed here, before it is written, and a repo import
        # the addon could not resolve is a hard refusal at the moment the
        # generator runs. `test_vendor_sync` keeps its own independent copy of
        # this check: a generator that grades its own homework needs a second
        # marker, and that test is the one that found all three.
        left = _absolute_repo_imports(src, repo_mods)
        if left:
            nl = chr(10)
            shown = nl.join("    line %d: %s" % (n, s) for n, s in left)
            raise SystemExit(
                "build_addon: " + name + " would be written with "
                + str(len(left)) + " repo import(s) the addon cannot resolve:"
                + nl + shown + nl
                + "  Each needs an entry in VENDORED[" + repr(name) + "], and "
                + "two call sites importing the same module need TWO entries "
                + "or one alias each -- the table replaces a literal, once.")
        rel = source.relative_to(REPO).as_posix()
        banner = (f"# VENDORED COPY -- generated by tools/build_addon.py "
                  f"from {rel}\n# Edit {rel}, not this file.\n")
        _write_lf(dest / name, banner + src)
        written.append(f"vendor/{name}")
    return written


def addon_version() -> str:
    """The addon's version, read from `bl_info` -- the ONLY source of truth.

    THIS EXISTS BECAUSE THE MANIFEST USED TO CARRY ITS OWN LITERAL AND THEY
    SILENTLY DIVERGED.  Blender 4.2+ installs this package as an *extension*,
    and an extension's version comes from `blender_manifest.toml`, NOT from
    `bl_info`.  So bumping `bl_info` to 1.1.0 changed nothing a user could
    see: the Add-ons panel kept reporting 1.0.0, which is indistinguishable
    from "your install did not take" -- and that is exactly how it was read,
    costing a round of uninstall/restart/reinstall against a build that was
    already correct.

    A version a user is told to check has to be derived from the thing that
    changed, or it is not a check.

    THE OWNER'S RULE FOR WHICH DIGIT MOVES (2026-09-20):

      * THIRD -- bump on EVERY build that leaves this repo.  A fix, a tweak,
        a rebuild: if someone is handed a new zip, this moves.  A version that
        does not move is indistinguishable from an install that did not take.
      * SECOND -- a major issue solved **and confirmed by a human**.  It is
        not self-awarded and it is not a judgement this code, or the seat
        running it, gets to make.

    Edit `bl_info` in `blender/io_scene_c3/__init__.py`.  Never edit the
    manifest literal instead: Blender shows the MANIFEST for an extension, so
    the two diverging is invisible until a user reports a stuck version.
    """
    src = (ADDON / "__init__.py").read_text("utf-8")
    m = re.search(r'"version"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',
                  src)
    if not m:
        raise SystemExit("build_addon: no bl_info version in __init__.py")
    return "%s.%s.%s" % m.groups()


MANIFEST = '''schema_version = "1.0.0"

id = "io_scene_c3"
version = "{version}"
name = "Conquer Online C3 (mesh)"
tagline = "Import and export Conquer Online PHY meshes, byte-exactly"
maintainer = "CO Client RE project"
type = "add-on"

blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]

[permissions]
files = "Read Conquer Online .c3 meshes and .wdf archives, write .c3 exports"
'''


def build_zip() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    _write_lf(ADDON / "blender_manifest.toml",
              MANIFEST.format(version=addon_version()))
    out = BUILD / "io_scene_c3.zip"
    files = sorted(p for p in ADDON.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path("io_scene_c3") / p.relative_to(ADDON))
    return out


def main(argv):
    written = vendor()
    print(f"vendored {len(written)} module(s) into "
          f"{VENDOR.relative_to(REPO)}:")
    for w in written:
        print(f"  {w}")
    if "--no-zip" in argv:
        return 0
    out = build_zip()
    size = out.stat().st_size
    print(f"\nwrote {out.relative_to(REPO)}  ({size:,} bytes)")
    print("\nInstall:  Blender > Edit > Preferences > Add-ons > "
          "(v) > Install from Disk...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
