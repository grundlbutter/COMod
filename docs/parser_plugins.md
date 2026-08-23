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

> **"Not derivable from the bytes" is a test to run, not a description to
> agree with.** `docs/map_twin_precedence.md` is the worked example: two
> proposals for new hooks — the map registry's spelling, then which of a map's
> two shipped twins is live — both passed the eyeball test, and **measuring
> the criterion refuted both**. The registry's own `FileName` field turns out
> to name the form the client opens on 100% of rows on every install, private
> repack included, so the rule is `core`'s. Run the test before writing the
> hook; the measurement took less time than the write-up would have.

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

### The three parse families, and why the third one inverts a rule

| family | plugins | entity tables | compiled twins |
|---|---|---|---|
| CCO | `cco` | `npc.json`, `itemtype.json`, plaintext ini lookups | none |
| **plaintext** | `plaintext`, `patch5017`, `patch5065`, `patch5165`, `patch7878` | `npc.ini`, TQ-cipher `.dat`, **plaintext ini lookups — LIVE on 5017/5065/5165, ABANDONED on 7878** | **none** |
| compiled | `patch5517`, `patch6090`, `patch6609` | `npc.ini`, TQ-cipher `.dat`, `.dbc` | 14, 15 and 12 |

**The container is a separate axis from the family.** Every client in this
table ships WDF except `patch7878` (four DatPkg pairs) and Zephyr-1057 (a
DatPkg pair *and* five garment WDFs, both in one install). A client's
container says nothing about which family it is in: 7878 is
plaintext-family in TPD, Zephyr is CCO-family in a mixed root. See
`coassets.AssetRoot._discover_archives`. (The liveness vocabulary — LIVE /
ABANDONED / shadowed — is defined in *"Liveness is per table, not per
family"* below: on 7878, `npc.ini` grew to 900,246 bytes while the four art
lookup tables it points at are byte-identical to 6090's, so 1,827 of 4,087
npc rows resolve to nothing and nothing raises.)

`core/dbc.py` opens by warning that an official client's plaintext `ini`
tables are *"stale decoys"* the client no longer reads. **That is true from
5517 onward and false before it.** 5017, 5065 and 5165 ship **no `.dbc` at
all**, so those same files are the only tables that exist.

What settles it is a two-sided measurement, because the wrong profile never
raises — it answers, with paths the install does not ship:

```
                OFFICIAL ok      PLAINTEXT ok    plaintext paths that exist
    5017          0 / 503          503 / 503          2000 / 2000
    5065          0 / 575          575 / 575          2000 / 2000
    5165          0 / 880          855 / 880          1970 / 1970
    5517       1123 / 1123        1066 / 1123           840 / 1870
    6090       2256 / 2264        1815 / 2264           841 / 1880
```

**Do not implement an inversion as a pile of overrides.** `plugins/plaintext.py`
derives from `Plugin` and states every fact positively rather than subclassing
`Patch6090` and switching four answers off, because an override is what a
later tidy-up deletes — after which the family silently inherits the trap.
`patch5517` subclasses `patch6090` for the opposite reason: there it really is
the same client with two absences.

The three members then subclass one family class, because their **formats**
differ where their tables do: 5017 ships no `ItemTexture.ini` and declares
seven part slots where the other two declare eight, and 5165's `itemtype.dat`
carries a 40th column. One plugin with a version switch would have had to make
each of those a function of the root.

**A missing compiled table is not evidence of this family.** Zephyr-1057 ships
zero `.dbc` *and* four-wide action fields; CCO ships zero `.dbc` too. The
discriminator has to be positive — the plaintext lookup chain present and the
compiled one absent — and it then separates all seven installs on this
machine. `version.dat` picks the member.

The freeze is worth knowing on its own: nine plaintext tables are
**byte-identical** in 5165, 5517 and 6090, so *6090's stale decoy is literally
5165's live file*. A table stopped being maintained exactly when a compiled
twin appeared beside it, and the three that never got one — `npc.ini`,
`Action3DEffect.ini`, `ItemTexture.ini` — differ at all five patch levels.

**Live is not the same as complete, and the two get conflated.** `3dmotion.ini`
names four body-motion families on *every* official client; 5517 and 6090 ship
sixteen and their compiled twin names the rest, so the shortfall never shows.
Take the twin away and it does — and it splits this family: 5017 and 5065 ship
four and name four, while **5165 ships 204 loose `.c3` files under
`c3/1001`..`c3/1004` that its own table never mentions and nothing else can
supply.** That is worse than the 6090 trap, not better: an under-reporting
table with no shadow behind it, where the filesystem is the only authority.
CCO is the control in the other direction — its ini names eight families and
only four resolve — so a table's completeness has to be measured in *both*
directions, against the disk, and declared per base.

### Liveness is per table, not per family — and there is a third state

The families table above answers "are the plaintext tables live?" once per
client. **7878 is the client where one answer per install stops being enough.**

It ships no `.dbc` at all, so it passes the plaintext family's positive test —
the lookup chain present, the compiled one absent. And its plaintext tables are
a **mix**:

    ini/npc.ini          376,991 -> 900,246 bytes across 6090..7878   MAINTAINED
    ini/EmotionIco.ini       712 -> 1,955                             MAINTAINED
    ini/3DSimpleObj.ini  f34f56332383  byte-identical on 6090/6609/7878  FROZEN
    ini/3dobj.ini        9202de93aa03  byte-identical                    FROZEN
    ini/3dtexture.ini    6be2fece83fd  byte-identical                    FROZEN
    ini/3dmotion.ini     9951727aa1ed  byte-identical                    FROZEN

The four lookups are the *same 2008 files* 6090 ships as stale decoys. There
they are shadowed and the compiled twin carries the truth. **Here there is no
twin and no `.dat` equivalent** — checked across all 200 of 7878's `.dat`
files. So the frozen table is the authority, and it under-reports:

    base    npc.ini rows   resolved   unresolved   compiled twin
    6090         2,277      1,828        449         present
    6609         2,784      2,031        753         present
    7878         4,087      2,260      1,827         ABSENT

(`SimpleObjID=N` into `[ObjIDType<N>]`. The 6090 row reproduces `npcart.py`'s
recorded 1,815/2,264 to within 13 rows of counting difference, which is what
licenses reading the 7878 row.)

**1,827 rows resolve to nothing and nothing raises**, because an npc whose
`SimpleObjID` the frozen table never heard of is indistinguishable from an npc
with no art. That is the 5165 trap above at nine times the scale.

**Why neither existing mechanism can express it.** There are three states, and
the contract has words for two:

| state | example | what should happen |
|---|---|---|
| shadowed | 6090 `3DSimpleObj.ini` | read the compiled twin |
| unshadowed and **live** | 5017 `3DSimpleObj.ini` | read the ini; it *is* the table |
| unshadowed and **abandoned** | 7878 `3DSimpleObj.ini` | read the ini, and **say it is short** |

`core/dbcshadow.py` answers from **file existence**, so on 7878 it returns
`None` for all four — *"use the ini"* — the same answer it gives 5017.
`prefers_compiled_tables()` returns `False`, also the same answer 5017 gives.
Measured: **they agree.** `test_viewer.ParserPlugins.UNCONSUMED_HOOKS` proposes
wiring them as a cross-check that fails loudly where they disagree; **on 7878
they do not disagree, they are both wrong in the same direction**, which is
exactly the case a disagreement-based cross-check cannot see.

**What a plugin should therefore declare.** Liveness is per table and per base,
measured, not inherited from the family. A family declaration of *"the
plaintext tables are the LIVE ones"* — correct for 5017/5065/5165 — is on 7878
**right about `npc.ini` and wrong about everything `npc.ini` points at**. Two
rules, both the existing ones applied one level down:

* **Declare frozen tables positively, with the bytes.** `patch7878` carries
  `FROZEN_LOOKUPS` (four sha256 prefixes) and `NPC_COVERAGE` (rows / resolved /
  unresolved). "Frozen" is a claim about content, so it is recorded as content
  rather than as prose.
* **A declared shortfall gets a reader in the same commit.** `table_quirks()`
  reaches `/api/plugins`; `tests/test_patch7878.py` re-derives both the hashes
  and the coverage from the install. A claim nobody checks drifts into fiction
  while still reading as authority — `PartIni.source`, one level up.

**A short answer from a base that declares its shortfall is a correct answer.**
A short answer from one that does not is indistinguishable from a bug, and this
project's register is mostly that distinction.

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

The only one that exists: `Patch6090` replaces the **female** `v_l_weapon`
basis with CCO's, because 5517 and 6090 ship it degenerate on body shapes 001
and 002 and clean on the male ones. (Unit-scaling the rows is the *fallback*,
for when CCO's track cannot be reached. Worth being precise about: the two
look very different on screen, and the viewer sat on the fallback unnoticed
because the borrow demanded an appearance id CCO does not ship —
`docs/CORRECTIONS.md` **C34**.) Four checks say the engine does not repair it,
so the real client very likely shows the squash. Correcting it is a judgement
that the tool should show what the artist meant; it is labelled as such
wherever it lands.

### Incompleteness is fine

A plugin is never obliged to be complete. Return `None` or an empty
collection and the app uses its own inference, labelled as such. Write down
what you know and leave the rest open — `plugins/patch6090.py` ends with a
"what is still open" list naming five unresolved things, which is more useful
to the next contributor than silence.

### The boundary: DatPkg clients do NOT get a plugin here

**A deliberate asymmetry, decided 2026-08-09. Written down so you meet a
documented boundary instead of an absence.**

Official WDF clients are resolved through `plugins/`. **Community `.tpi`/`.tpd`
(NetDragon DatPkg) clients — Zephyr and its kin — are resolved through
`core/colibrary.ServerView` instead, and there is deliberately no
`plugins/zephyr.py`.**

The reason is what `coroot.REQUIRED` *means*, not the cost of changing it:

```python
REQUIRED = (("c3.wdf", "file"), ("data.wdf", "file"), ("ini", "dir"))
```

It does not answer *"is this a Conquer client"*. It answers **"is this a
WDF-packaged install that `AssetRoot` can open"** — a narrow, true, load-bearing
question that roughly fifty call sites depend on being narrow (`REQUIRED` 22
refs across 4 files, `missing_parts` 17, `looks_like_root` 11). A DatPkg client
genuinely **is not one**, so `missing_parts(Zephyr)` returns
`['c3.wdf', 'data.wdf']` and refuses it *before any plugin is consulted*. **That
refusal is correct.**

Widening `REQUIRED` to accept WDF **or** DatPkg would not teach the system about
DatPkg. It would make a sharp predicate vague, and every call site would quietly
begin asking a fuzzier question than it was written for — a check that stops
discriminating still returns a plausible answer, which is `CORRECTIONS.md` C21's
shape exactly.

**The project has been here before, and the fix went the other way.** `REQUIRED`
once carried `bin/64/` and *"refused every client but one"*. The remedy was
**deleting a wrong entry**, not making the predicate polymorphic.

**Why `ServerView` is the right home and not a consolation prize.** A parse
profile describes *how to read a client's tables*. Zephyr's tables are
**synthesised** there — `colibrary rebuild-tables` derives 3,846 body
appearances and 160 mount appearances from directory convention, because that
client resolves parts in code rather than in tables. The profile belongs beside
the synthesis it describes, not behind a gate that correctly refuses it.

**The cost, stated rather than buried:** the plugin system is not uniform.
Official clients get `plugins/`; community DatPkg clients get a `ServerView`
profile. Someone will trip on that. If a **second** DatPkg client appears and
the duplication bites, revisit — two instances are evidence, one is a guess.

#### ⚠ OPEN DEFECT — a `ServerView`'s parse profile comes from the BASELINE, not from the client whose assets you are looking at

**This is a live consequence of the decision above and it must be read with it.**
A community client has no plugin of its own, so it has **no way to declare its
own table conventions** — it inherits whichever baseline the user happens to have
configured. `ServerView.__init__` calls `super().__init__(root)`, so it *is* an
`AssetRoot` rooted at the baseline, and every profile probe answers about the
baseline rather than about the imported client.

Measured on one Zephyr import over two different baselines [V]:

```
ServerView(zephyr) over 5165  ->  detect_profile = plaintext   npcs = 2785
ServerView(zephyr) over 6090  ->  detect_profile = official    npcs = 2785

common npc_types compared : 397
RESOLVED DIFFERENTLY      :  25
  110: 5165=('','')  6090=('c3/npc/785/1.c3',      'c3/texture/9997850.dds')
  895: 5165=('','')  6090=('c3/npc/838/1.c3',      'c3/texture/9998380.dds')
  633: 5165=('','')  6090=('c3/mount/801/8010000.c3','c3/mount/801/8014400.dds')
```

**25 of 397 NPCs silently lose their art** when the same community server is
viewed over a 5165 baseline instead of a 6090 one. Empty strings, no error, no
warning — the C21 vantage-point shape at the library layer: one view serving
another's facts, failing as a plausible answer rather than a refusal.

**Scope, stated honestly.** This shows the answer *depends on an unrelated
choice*. It does **not** establish that 6090's answers are the correct ones for
Zephyr — which of the two is right for a DatPkg client is unmeasured, and is the
next question rather than a conclusion. Sample was the first 400 of 2,785 npcs;
the ratio may move on the full set.

**The fix is not a fourth probe.** A probe is asked about the composed view and
the baseline answers first. The library **already records the client's identity**
— the zephyr entry carries `clientVersion: 1064` and its source client path — and
**nothing consults it for parsing**: every reader of `clientVersion`
(`coviewer`, `colibrary`, `assetdiff`) uses it for display or metadata only. So
the open question is:

> **Should a `ServerView`'s parse profile come from the library's declared client
> version rather than from the baseline root?**

Until that is answered, treat any parse-dependent answer from a `ServerView` as
qualified by which baseline was configured, and say so when you report one.

The measurement behind all three options is in
[`handoff_zephyr_tpi.md`](handoff_zephyr_tpi.md) §1.2.

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

*Status: keyed and in use, `out/dll/` included as of 2026-08-07.*

`out/dll/` was the last unkeyed tree, and the reason recorded for deferring it
— that it held artefacts from an install no longer on this machine — was
**wrong**: the CCO install was there all along, so nothing had to be labelled
by guesswork. It was migrated by regeneration, and the migration verified
itself: a fresh CCO run and the archived `exports.json` agree on every shared
entry exactly, which proves by reproduction whose artefact it was.

One exception, deliberate: `out/dll/wdf_name_recovery.*` stays **global**.
Every official client ships byte-identical `c3.wdf` and `data.wdf`, so a
recovered name is a property of the archives rather than of the install that
mined it — the same reason `out/wdf/` is global. It lives under `out/dll/`
only because the wordlist came from DLL strings.

`import_plan` is consumed by `tools/assetdiff.py` now, so "importing a library
uses the plugin" is literally true for the archive list, the loose layer and
the skip set. It remains the right place for a plugin to declare which
*builders* to run on import; that part is still unwritten.

## Verifying a plugin

`tools/test_viewer.py::NpcModelArtPins` is the 6090 plugin's regression
harness: it pins what a human confirmed on screen — NPC art, the standby
set's real bodies, mount skins, monster colour sets, dropped labels — and
fails if a resolution change shifts any of it. A plugin worth trusting has
something like it, because the only real test of a parser is a person saying
"yes, that is a ThunderApe".
