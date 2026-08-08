#!/usr/bin/env python3
r"""
cco -- Classic Conquer 2.0, the install this project started from.

Deliberately thin, and that is the point: CCO is what every default in the
app was tuned to, so the plugin that describes it mostly declines to have
opinions. It exists so that

* the picker can offer CCO by name rather than as "not one of the others",
* `confidence` can recognise it and keep patch6090's heuristic honest,
* and the differences it *does* declare are written down somewhere instead
  of living as unstated assumptions in the resolver.

What CCO does differently from the official patch clients: JSON entity
tables (``npc.json``, ``monster.json``, ``itemtype.json``, all plaintext and
current), nine-wide zero-padded appearance ids, motion ids that name their
file directly (``999001100`` -> ``c3/npc/999001100.c3``, no lookup), and one
appearance row per colourway rather than colour encoded in a texture id.

PLANNED: THIS THIN PLUGIN IS A PLACEHOLDER
------------------------------------------
Writing it properly is queued behind trusting 6090 and 5517, deliberately --
a plugin is only as good as the pass that produced it, and CCO's has not been
done. The notes to write it from already exist and are scattered:

* ``docs/handoff_5517_base_prep.md`` §6.1a -- what has been read out of CCO's
  tables and *why it had to be CCO*. The armed-motion alias table in
  ``attach.WEAPON_MOTION_SET`` is 122 entries taken from CCO's
  ``3dmotion.ini`` because no official client ships those rows at all, and
  the proof that 6090 rewrote ``v_l_weapon``'s dummy track rests on diffing
  one file across CCO and 6090.
* ``docs/handoff_5517_base_prep.md`` §2 -- the engine/table lineage split.
* ``plugins.patch6090.QUIRKS`` -- six form differences stated from the 6090
  side; most of them are a CCO fact seen in a mirror and belong here in the
  positive.

**CCO is the lineage's only ground truth for weapon placement** -- the one
client where ``v_l_weapon`` is a clean unit rotation -- so this plugin is not
legacy support. It is the reference.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from plugins import Plugin                            # noqa: E402


class ClassicConquer(Plugin):
    name = "cco"
    label = "Classic Conquer 2.0"
    aliases = ("classic", "cco2")
    notes = ("Plaintext JSON entity tables, zero-padded nine-wide ids, "
             "motion ids that are filenames, one appearance row per "
             "colourway. 437/437 NPCs resolve.")

    def confidence(self, root, exists) -> float:
        """`npc.json` is the tell, and the absence of compiled twins keeps
        this from claiming an official client that also ships JSON."""
        if exists("ini/npc.json") and not exists("ini/3DSimpleObj.dbc"):
            return 0.85
        return 0.0

    def table_profile(self):
        import npcart
        return npcart.PROFILE_CCO

    def aura_convention(self) -> str:
        """826 always-on Action3DEffect rows, each pointing at an effect
        named after the appearance id. The indirection 6090 dropped."""
        return "table"

    def key_field_widths(self):
        return {"Action3DEffect.ini": {"shape": 3, "action": 3,
                                       "type": 3, "sub": 3}}

    def colourways(self, texture, exists):
        """None: CCO ships each colour as its own appearance row, so the
        app's "appearances sharing this mesh" answer is already right and a
        digit convention would only invent siblings."""
        return ()


PLUGIN = ClassicConquer()
