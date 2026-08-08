# Parser plugins

**A parser plugin teaches the app how to read one flavour of Conquer Online
client.** One module per patch level or private server. `plugins/patch6090.py`
is the worked example; `plugins/cco.py` is a deliberately thin one.

The app holds the *mechanics* — archive readers, mesh parsers, ciphers, the
renderer. A plugin holds the *policy*: which tables this client actually
reads, how its ids are spelled, where it keeps colour, and which of its rows
are simply wrong. None of that is derivable from the bytes, and every time
the app assumed one client's habits held for another it produced confident
wrong answers — monsters in another monster's skin, characters T-posing,
names seven years stale.

## Writing one

Drop a module in `plugins/` with a module-level `PLUGIN`:

```python
from plugins import Plugin

class MyServer(Plugin):
    name = "myserver"                 # what config stores; lowercase
    label = "My Private Server"       # what the picker shows
    notes = "What is verified, what is inferred."

    def confidence(self, root, exists):
        return 0.95 if exists("ini/myserver.marker") else 0.0

PLUGIN = MyServer()
```

That is a complete, working plugin. `Plugin` implements every hook as "I have
no opinion", so you override only what your client does differently, and the
app falls back to its own inference everywhere else.

Read `plugins/__init__.py` for the full hook list — each docstring is the
specification. The ones that matter most in practice:

| Hook | Owns |
|---|---|
| `confidence(root, exists)` | Whether a folder is your kind. Judge contents, never the folder's name. |
| `table_profile()` | Which entity tables exist and in what container (an `npcart.Profile`). |
| `texture_for_mesh(mesh, exists)` | Family conventions: which texture a directory skins from. |
| `colourways(texture, exists)` | Where colour lives in an id. |
| `monster_colourways(ident)` | Verified colour sets, when conventions over-reach. |
| `flat_family_base(group, paths, has_geometry)` | Which file in a flat family is the real body. |
| `entity_name_overrides()` | Names to force, and joins to refuse. |
| `colour_provenance()` | How to label the skin `default_colour` picked. |
| `socket_correction(socket, body)` | A socket this client ships broken. **Viewer-only — never port it.** |
| `import_plan(root, exists)` | What importing this client involves. |

### Declare what differs in *form*, not just in content

`table_quirks()`, `key_field_widths()` and `aura_convention()` exist because
of a pattern that cost this project six debugging sessions: **format
differences fail silently.** A stale file parses cleanly and returns old
answers. A key padded four wide instead of three just misses, and a missing
effect is indistinguishable from a weapon that has none. Nothing raises.

So when you find one, write it down in the plugin. `patch6090.QUIRKS` has
six entries and each one names the symptom it produced, because the symptom
is what the next person will search for:

| quirk | what it looked like |
|---|---|
| stale `.ini` decoys beside 2015 `.dbc` twins | 955 armours offered instead of 3,326 |
| a *second* motion reader with its own `ini` load | headgear 3 units off the head, correcting itself when an animation played |
| unpadded ids in compiled tables | every `bodyType` slice silently wrong |
| four-wide action fields | every weapon and body effect silent at once |
| u32-wrapped motion ids | no motion resolved for any NPC |
| socket tracks carrying scale | weapons flattening mid-swing |

None of these are enforced by the readers — the readers are tolerant by
construction, comparing numerically where both sides are numeric and
preferring compiled twins wherever they exist. The declarations are for
people. They are surfaced through `/api/plugins`, so a contributor can read
another client's hard-won list before rediscovering it.

### Two rules that are not style preferences

**Label inference as inference.** `texture_for_mesh` returns
`(path, method, kind)`; set `kind` to `"authored"` only when a shipped table
says so, and `"inferred"` when it is a convention you measured. This project
lost a day to a guess reported at 0.95 confidence and called authored — the
confidence is what stopped anyone questioning it. A wrong answer labelled
authored is worse than no answer.

**A subclass does not inherit its parent's evidence.** `colour_provenance()`
exists because the app used to hard-code "verified colourway (default)" /
"authored" over whatever `default_colour` returned, for every plugin. So
`patch5517`, which inherits 6090's monster colour sets and says plainly in its
own docstring that nobody has checked them here, rendered them on screen as
verified. Reusing another client's table is fine and often right; claiming the
pass that produced it is not. Override the hook and say what you actually have.

**Ask `exists`, not the filesystem.** It resolves logical paths across loose
files and archives. 331 of the official archive's 24,757 names were never
recovered, so a real texture can be nameless yet perfectly readable —
`c3/texture/109000000.dds` is a 16 KB skin the renderer loads by hash all
day, and a name-based check calls it missing.

### `socket_correction` is the one hook that lies, and it is fenced

Every other hook says *what a client does*. `socket_correction` says "what it
does is broken, show something else" — so it carries a boundary the others do
not, and the boundary is structural rather than a promise.

**The project's goal is a compatible client.** A rewrite that quietly repairs
the art has stopped being compatible, and the failure is silent: it disagrees
with a screenshot of the real client and nobody notices for months. So:

* A correction is applied in **`coviewer.apply_socket_corrections` and nowhere
  else**. `tools/attach.py` and `tools/parts.py` stay a faithful read, and
  they are what `client/` and any engine port consume. The fence is that the
  correction is not in a shared code path at all.
* Every corrected socket is **named in the payload and stated on screen**. A
  correction you cannot see is indistinguishable from a reader that is simply
  wrong — and that is not hypothetical here, since a hardcoded weapon set in
  one readout hid a real bug for two sessions.
* `docs/attachment.md` — the spec a rewrite reads — opens with a warning not
  to port it.

The only one that exists: `Patch6090` unit-scales the **female** `v_l_weapon`
basis, because 5517 and 6090 ship it degenerate on body shapes 001 and 002 and
clean on the male ones. Four checks say the engine does not repair it, so the
real client very likely shows the squash. Correcting it is a judgement that the
tool should show what the artist meant; it is labelled as such wherever it
lands.

### Incompleteness is fine

A plugin is never obliged to be complete. Return `None` or an empty
collection and the app uses its own inference, labelled as such. Write down
what you know and leave the rest open — `plugins/patch6090.py` ends with a
"what is still open" list naming five unresolved things, which is more useful
to the next contributor than silence.

## How one gets chosen

The user declares what a folder is when they add it, and the choice is stored
as `game_kind`. `plugins.for_kind()` resolves it; `plugins.detect()` is used
only when nothing was declared, taking the most confident `confidence()`.

**A stored declaration always beats detection.** A private-server repack of
6090 looks exactly like 6090 to any test of the bytes, and the person who
added the folder knows things the bytes do not.

The setup page renders whatever `available()` discovers, with `suggested`
pre-selected from detection — so a contributor's plugin appears in the UI by
being present, with no app change.

## Per-base indexes

Derived state (`out/meshtex/`, thumbnails, `out/artcrawl/`) is built *from* a
client and is only valid for that client. The switch from CCO to 6090 broke
four visible things through stale indexes alone — the geometry hint that made
monsters vanish was a CCO fact served as a 6090 answer.

Indexes therefore key by base:

    out/indexes/<base-id>/meshtex/...
    out/indexes/<base-id>/artcrawl/...
    out/indexes/<base-id>/thumbs/...

`<base-id>` is `<plugin name>-<fingerprint>` — for example
`patch5517-4a55ca2ba561`. The fingerprint is `coroot.base_fingerprint()`, a
sha256 over every file directly in `ini/`, contents included: about 50 ms, and
decisive where nothing else is. The archives cannot discriminate (all five
official patches ship byte-identical `c3.wdf` and `data.wdf`) and `version.dat`
is absent from private-server repacks, but the table layer is both what a
plugin is *about* and where the clients actually differ — 91 of the 176 `ini/`
files 5517 and 6090 share are not the same file.

Both halves are load-bearing. The fingerprint makes it correct; the plugin name
makes re-declaring a folder rebuild it, which is right — an index is what a
*plugin concluded about* an install, so a different parse profile is a
different index.

`coroot.find_derived()` rewrites per-base paths (`coroot.PER_BASE`) and
`coroot.derived_path()` is the writers' equivalent, so a tool keeps naming the
plain literal it always named. **There is no fallback to the unkeyed path**: a
missing index reads as "build me", never as permission to serve the last
client's answers.

What stays global, and why each is a claim rather than an oversight:
`out/wdf/` (hash-to-name maps — the key *is* the archive content),
`out/offsets_cache.json` (already refuses a foreign cache, keyed on module
sha256 — the pattern, not the exception), `out/opcodes.json` (protocol, not
assets), and `out/thumbs/servers/<name>/` (COmmunity Library server views,
which have nothing to do with the configured install).

Declare a folder's kind through `coroot.declare_kind(root, name)` rather than
writing `game_kind` directly. It records the kind against that root as well as
globally, so pointing the tools at another install and back finds the same
namespace instead of opening a fresh empty one.

*Status: keyed and in use. `out/dll/` is the one tree still unkeyed — it holds
artefacts from an install that no longer exists on this machine, so a migration
would have to label data it cannot regenerate. Still open:
`import_plan` is where a plugin will declare which builders to run on import.*

## Verifying a plugin

`tools/test_viewer.py::NpcModelArtPins` is the 6090 plugin's regression
harness: it pins what a human confirmed on screen — NPC art, the standby
set's real bodies, mount skins, monster colour sets, dropped labels — and
fails if a resolution change shifts any of it. A plugin worth trusting has
something like it, because the only real test of a parser is a person saying
"yes, that is a ThunderApe".
