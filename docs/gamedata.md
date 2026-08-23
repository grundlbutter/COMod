# `ini/` — what the client knows about items, spells, monsters, NPCs and maps

Source: `$ROOT/ini/` (167 files, read-only). `$ROOT` is the install root,
located by `core/coroot.py`.
Loader and schema profiler: `tools/inidb.py`.
Full machine-readable profile: `out/ini/schemas.json` (2.3 MB).

Everything here is **verified** by parsing — field names, row counts, types and
value ranges are measured, not assumed. Where a field's *meaning* is a guess
from its name, that is called out as **inferred**.

## Encoding

`codepage.ini` contains the single byte `'0'`. Every `.json` file decodes as
strict UTF-8. **No `.ini` file in this build contains a byte above 0x7F**, so the
codepage never actually matters here. `inidb.py` still honours it
(0 → cp1252, 936 → gbk, 950 → big5) because original TQ clients shipped GBK data
and a future patch could reintroduce it. *(verified — byte-scanned all 167 files.)*

## File shapes

| Shape | Count | Description |
|---|---|---|
| `json_list` | 21 | JSON array of flat objects — the real game tables |
| `json_dict` | 4 | JSON object used as a map |
| `ini_sectioned` | 27 | `[Section]` + `key=value` — 3D/render data |
| `ini_flat_kv` | 7 | `key=value`, no sections; keys are compound ids |
| `ini_records` | 10 | whitespace-delimited positional rows |
| `binary` | 94 | `.TME` terrain-magic blobs and `cache.dat` — not text |
| `empty` | 4 | zero bytes |

The 94 binary files are almost all `ini/TerrainMagic/*.TME`. They are reported
but not parsed.

---

## Compiled twins: where this document does **not** apply

**Everything else here is measured on the CCO install**, which ships no
compiled tables. From patch **5517** onward an official client ships some of
these tables twice — the plaintext `.ini` and a compiled `.dbc` — and
`GraphicData.dll` reads the `.dbc`. **Where a compiled twin exists, the client
reads the twin**, so profiling the `.ini` on those roots describes a file the
client ignores. On 6090 the `.ini` files are stamped **2009** and the `.dbc`
**2015**.

That is a claim about *which file is authoritative*, and it does not rest on
the two disagreeing: even if a table's `.ini` and `.dbc` were found to match
today, reading the ignored file would still be wrong — a matching diff is a
fact about current content, not about which file is loaded.

They do **not** match, and it is not one odd table. **Three** unrelated tables
have now been caught the same way, in three subsystems, by three sessions —
**none of whom were looking for it**:

* **`3DEffect`** — on 6090 the `.ini` misses a large fraction of the effect
  table and misreads rows it does share, some of which change which mesh and
  texture is drawn. Measured on `comod/dbc-effe-emoi`; figures in
  `docs/effects.md` **§7a**, and the coverage numbers derived from them
  re-taken against the compiled table in **§9a**. Not restated here.
  `docs/CORRECTIONS.md` **C44** for the divergence, **C50-effe-definitions**
  for the re-derivation, `C45-ini-shadow-class` for the class. (An earlier
  revision cited **C30** — wrong, and the cause is worth naming: that entry
  was renumbered C30 → C36 → C44 with three branches in flight, so a citation
  copied at any of those moments now points at an unrelated anchor-alignment
  refutation.)
* **`3dmotion`** — `3dmotion.ini` names **four** of the sixteen per-body-type
  motion families; `3dmotion.dbc` names all sixteen. The viewer's motion rule
  was therefore 75 % wrong, silently, for an unknown length of time
  (`comod/anim-toggle-viewer`; `character/motion` 1,739 → 2,475,
  `character/misc` 780 → 44).
* **the whole effects asset path** — the largest. Distinct C3 paths named by
  the six path-valued inis: CCO 12,600 → 12,600 (no twins), 5517 5,867 → 8,898,
  6090 5,867 → **14,113**. On 6090 more than half of every C3 the client can
  reach by name was invisible.

**Two distinct failure modes, and the second is worse.** `miscmotion.ini` and
`MountMotion.ini` are **zero bytes** on both 5517 and 6090, while their twins
carry 218 / 784 and 218 / 8,758 rows. A *disagreeing* file yields wrong data,
which a comparison can catch. A *zero-byte* file yields no data, which reads as
**"this table is empty"** rather than **"you opened the wrong file"** — nothing
errors, every loop runs zero times, and an empty table is indistinguishable
from an absent feature. A gate keyed on disagreement would never fire here,
which is why this one keys on **twin existence**.

**It also manufactures false equivalence between bases.** Measured by hashing:
every shadowed `.ini` is byte-identical between 5517 and 6090 (8 of 8), while
the `.dbc` twins differ (7 of 8). The plaintext layer is one frozen 2009
snapshot shipped to both installs, so **any cross-base comparison performed on
it returns "identical" as a tautology** — visible above as 5,867 on both bases
through the ini against 8,898 and 14,113 through the twins. This already cost
one published conclusion: `ptclprove` reported 5517 and 6090 as a
byte-identical particle corpus — correct on the plaintext, **wrong by 2,303
chunks** on the real one. Two errors cancelling into something that looks like
a result.

All three survived because the plaintext file exists and parses cleanly, so
nothing prompted anyone to ask whether the client reads it. That is what the
gate is for. The full argument and the register status live in
`core/dbcshadow.py`'s docstring.

Twin presence is a **per-file** property, not a per-client one — `3DEffect.dbc`
ships on 5517 and 6090, `EmotionIco.dbc` on 6090 only. A client-level verdict
would refuse a legitimate plaintext read of `EmotionIco.ini` on 5517, which is
the same error pointed the other way. The gate is keyed on "does a compiled
twin exist for **this file** on **this base**", asserted by
`test_viewer.py::DbcShadowGate::test_twin_presence_is_per_file_not_per_client`.
`3dmotion` shows the same rule from the opposite direction: CCO ships only the
first four motion families *and no `.dbc`*, so there the plaintext file is
complete and correct. The same filename is authoritative on one base and a
decoy on another.

**What the gate does and does not cover.** It sits in the two shared loaders,
`coassets.parse_ini` and `inidb.load`, so anything reading a table through
those is gated. Ad-hoc `read_text` on a path under `ini/` bypasses it; the two
known raw readers, `anim.MotionIndex` and `attach.Catalogue._load_flat`, both
overlay the compiled twin themselves and now ask `dbcshadow` for it rather than
spelling the `.dbc` filename locally — `attach` previously used
`Path.with_suffix('.dbc')`, which only ever found `3DObj.dbc` from `3dobj.ini`
because NTFS is case-insensitive.

`tools/inidb.py` **refuses** rather than profiles. `load()` raises
`dbcshadow.ShadowedIni` naming the twin, `schemas` records the shadowed files
without profiling them and exits **2**, and `--allow-stale-ini` declares the
exception. The check is `core/dbcshadow.py`, resolved from each file's own path
on every call — never at import time and never process-global, which are the
two defects recorded as C21 and C22 in `docs/CORRECTIONS.md`.

MEASURED 2026-08-09, by pairing stems in each install's `ini/`:

| install | `.dbc` files | shadowed `.ini` | `inidb schemas` exit |
|---|---:|---:|---:|
| 5017 | 0 | 0 | 0 |
| 5065 | 0 | 0 | 0 |
| 5165 | 0 | 0 | 0 |
| 5517 | 14 | 14 | **2** |
| 6090 | 15 | 15 | **2** |
| CCO | 0 | 0 | 0 |

The fifteen shadowed tables on 6090 are `3DEffect`, `3DEffectObj`, `3dmotion`,
`3dobj`, `3DSimpleObj`, `3dtexture`, `armet`, `armor`, `EmotionIco`, `misc`,
`miscmotion`, `Mount`, `MountMotion`, `weapon` and `WeaponMotion`. 5517 is the
same set without `EmotionIco`. Every `.dbc` in both installs has an `.ini` of
the same stem: there is no compiled table without a plaintext decoy beside it.
The stem match must be **case-insensitive** — `3dobj.ini` pairs with
`3DObj.dbc`, `3dtexture.ini` with `3DTexture.dbc`, `3DEffectObj.ini` with
`3DEffectobj.dbc`.

```bash
py -3 core/dbcshadow.py report --root <install>   # which lineage is this?
py -3 tools/inidb.py --root <install> shadowed    # the same, from the loader
```

### Which readers exist for the twins

| magic | tables | reader |
|---|---|---|
| `RSDB` | `3DObj`, `3DTexture`, `3DEffectobj`, `3dmotion` | `core/dbc.py::Rsdb` |
| `SIMO` | `3DSimpleObj` | `core/dbc.py::read_simo` |
| `MESH` | `armor`, `armet`, `weapon`, `misc`, `mount`, `*motion` | `core/dbc.py::read_mesh`, preferred automatically by `coassets.PartIni` |
| `EFFE` | `3DEffect` | **none yet** — being built on `comod/dbc-effe-emoi` |
| `EMOI` | `EmotionIco` | **none yet** — same branch |

### Declared stale reads (open debt)

Where no reader exists, or where switching to one would move a published
measurement, the plaintext read is *declared* rather than silently performed:
each carries a `STALE-INI:` note at the call site, and
`test_viewer.py::StaleIniDebt` asserts that the set of files doing this is
exactly the declared one — so adding another is a deliberate act with a test
change attached.

**Closed 2026-08-09:** `tools/effects.py`'s id→path tables. `EffectDB.objs`,
`.textures` and `.meshes`, and the `validate()` path census, now read the twin
overlaid on the plaintext via `effects.read_flat_live`. On 6090 that was 58.4 %
of every C3 the client can name; see `docs/effects.md` §9.

Re-checked and still open:

* `tools/meshtex.py::_build_effect_table` — `3DEffect.ini`; **still blocked**,
  no `EFFE` reader has merged (one is on `comod/dbc-effe-emoi`). `3DEffect.dbc`
  is a record table, so `dbcshadow.twin_rows` — which serves `RSDB` only —
  cannot substitute.
* `tools/meshtex.py` (`_build_simpleobj_table`, `_build_simplerole_table`) and
  `tools/models.py::_simple_obj` — `3DSimpleObj.ini`; a `SIMO` reader exists,
  but switching moves the meshtex coverage index, and rebuilding that index
  underneath other sessions is what produced C21.
* `tools/attach.py::Catalogue.table` — `armor`/`armet`/`weapon`/`mount`; a
  `MESH` reader exists and `coassets.PartIni` already uses it, but converting
  the Catalogue moves ident padding and row counts in the preview pipeline.

**Newly identified, not yet declared — `tools/meshtex.py::_flat_ini`.** It
reads `3dobj.ini`, `3dtexture.ini`, `3DEffectObj.ini`, `3dmotion.ini`,
`miscmotion.ini` and `MountMotion.ini` — every one `RSDB`-twinned, so every one
directly fixable with `dbcshadow.twin_rows`, the same one-line change made in
`effects.py`. It is the **largest remaining instance** and it is left alone
deliberately: `meshtex --coverage` writes the per-base index that other
sessions consume, and moving it mid-flight is the C21 hazard. Exposure, ini →
twin keys:

| table | 5517 | 6090 |
|---|---|---|
| `3DEffectObj` | 3,268 → 6,268 | 3,268 → 9,472 |
| `3dtexture` | 8,793 → 13,803 | 8,793 → 18,638 |
| `3dobj` | 1,443 → 2,125 | 1,443 → 2,985 |
| `3dmotion` | 63,004 → 569,699 | 63,004 → 568,581 |
| `miscmotion` | **0** → 218 | **0** → 218 |
| `MountMotion` | **0** → 784 | **0** → 8,758 |

It should be done as its own change, with the index rebuild announced.

---

## The main tables (`json_list`)

> **SCOPE, added 2026-08-09 — every row of this table is CCO's, and only
> CCO's.** `ini/*.json` does not exist on any official client (measured: 25
> such files on CCO, **zero** on each of 5017, 5065, 5165, 5517 and 6090), so
> these counts describe one install rather than the game. The same tables on
> an official base are `.dat` or `.ini` and are **larger** — `monster.json`'s
> 374 rows against `Monster.dat`'s 431 / 439 / 586 / 762 / 1,010, and
> `itemtype.json`'s 11,142 against `itemtype.dat`'s 6,865 … 24,270. Reading a
> figure here as a fact about "the client" is how **twelve** readers ended up
> silently returning nothing on an official base — four found by earlier
> sessions, eight by the sweep in `docs/CORRECTIONS.md`
> `C-2026-08-09-comod-json-official-sweep`, which also lists each file's
> official counterpart and which of those counterparts actually has a reader.

| File | Rows | Cols |
|---|---|---|
| `itemtype.json` | 11,142 | 33 |
| `magictype.json` | 610 | 47 |
| `npc.json` | 437 | 11 |
| `monster.json` | 374 | 13 |
| `cosmetics.json` | 517 | 15 |
| `3DEffect.json` | 2,209 | 8 |
| `MusicRegion.json` | 194 | 8 |
| `UserHelpInfo.json` | 284 | 6 |
| `GameMap.json` | 156 | 3 |
| `shop.json` | 22 | 4 |
| `region.json` | 21 | 13 |
| `tips.json` | 21 | 1 |
| `compose_package_info.json` | 21 | 3 |
| `WeaponSkillName.json` | 48 | 2 |
| `ProfessionalName.json` | 48 | 2 |
| `TerrainNpc.json` | 35 | 3 |
| `AdditiveSize.json` | 30 | 5 |
| `WeaponSkillLevelExp.json` | 20 | 2 |
| `legendary.json` | 17 | 3 |
| `ProfessionalSort.json` | 11 | 2 |
| `pet.json` | 6 | 6 |

### `itemtype.json` — 11,142 items, 33 columns

The most important table in the client. Every column is present in 100 % of
rows; there are no optional fields.

| Column | Type | Range / notes |
|---|---|---|
| `id` | int | 111,303 – 3,313,799. Encodes category — see below |
| `name` | string | ≤ 17 chars |
| `requiredProfession` | int | enum-like: 0, 11, 15, 21, 25, 40, 45, 143, 190 |
| `requiredWeaponSkill` | int | 0 – 12 |
| `requiredLevel` | int | 0 – 130 |
| `requiredSex` | int | small enum |
| `requiredStrength` | int | stat gate |
| `requiredAgility` | int | stat gate |
| `requiredVitality` | int | stat gate |
| `requiredSpirit` | int | stat gate |
| `monopoly` | int | bind/trade restriction flags (**inferred**) |
| `weight` | int | |
| `price` | int | |
| `attackMin` / `attackMax` | int | physical attack span |
| `defense` | int | |
| `dexterity` | int | |
| `dodge` | int | |
| `life` / `mana` | int | flat bonuses |
| `amount` / `amountLimit` | int | durability current / max (**inferred**) |
| `ident` | int | |
| `gem1` / `gem2` | int | socket contents |
| `magic1` / `magic2` / `magic3` | int | |
| `magicAttack` / `magicDefense` | int | |
| `attackRange` | int | |
| `attackSpeed` | int | |
| `description` | string | |

The `id` is structured: the leading digits select the equipment category and the
trailing digits the tier/quality, which is why `id` correlates with the asset
paths recovered from the archives (`c3/weapon/410.c3`, `data/itemminicon/580058.dds`,
`data/mapitemicon/440046.dds`). **Inferred** — the correlation is visible but the
exact digit split has not been pinned down.

### `magictype.json` — 610 spells, 47 columns

Column names are self-describing and cover targeting, cost, progression and
presentation:

- **identity / progression**: `MagicType`, `Name`, `Level`, `LearnLevel`,
  `ExpRequired`, `NextMagic`, `ProfessionalRequired`, `MonsterLevelRequired`,
  `WeaponSubType`, `UseItem`, `AutoLearn`, `AutoActive`, `Xp`, `UsePP`
- **combat**: `Power`, `HitPoint`, `MpCost`, `Distance`, `Range`, `Multi`,
  `Target`, `Status`, `Duration`, `ActiveTime`, `Crime`, `MagicBreak`,
  `DropWeapon`, `Ground`, `FloorAttribute`, `Disc`, `DiscEx`
- **timing**: `IntoneDuration`, `TargetDelay`, `TargetWoundDelay`
- **presentation**: `ClientRepresent`, `ScreenRepresent`, `ActionSort`,
  `SenderAction`, `IntoneEffect`, `IntoneSound`, `SenderEffect`, `SenderSound`,
  `TargetEffect`, `TargetSound`, `TraceEffect`, `GroundEffect`
- **misc**: `CanBeusedInMarket`

The `*Effect` columns are keys into `3DEffect.ini` / `3DEffect.json`; the
`*Sound` columns are paths under `sound/`. **Inferred** from naming plus the
observation that `3DEffect.ini` section names (`M_Fire`, `zf2-e224`) are exactly
the token shape appearing in DMap effect layers.

### `monster.json` — 374 monsters, 13 columns

`type`, `name`, `sizeAdd`, `zoomPercent`, `maxLife`, `level`, `bornAction`,
`bornEffect`, `bornSound`, `actResCtrl`, `asb`, `adb`, `bodyType`.

`asb`/`adb` are alpha source-blend / dest-blend render-state values — the same
names appear in `armor.ini`, `3DEffect.ini` and `npc.json`, where typical values
are 5 and 6. **Inferred** (consistent naming across five files, not confirmed
against D3D enum values).

Note this table is *client-side presentation only* — it carries no stats beyond
`maxLife` and `level`. Combat numbers live on the server.

**On an official client this table is `ini/Monster.dat`**, TQ-cipher-encrypted
`[Name]` sections that `core/tqdat.read_monster` returns in exactly these
keys, plus the part slots (`armet`, `rWeapon`, `lWeapon`, `misc`, `mount`).
`tools/models.load_monster_rows` picks between the two. Row counts: 431 / 439
/ 586 / 762 / 1,010 on 5017 / 5065 / 5165 / 5517 / 6090.

**`bodyType` is 0 on EVERY row of ALL SIX bases** — 3,228 official rows plus
CCO's 374, 3,602 in total — so it is a dead column everywhere, not just here,
and no client holds a monster→art link at all. That link is `monstertype.Mesh`,
server-side. Do not read `Monster.dat` expecting to recover it:
`docs/CORRECTIONS.md` `C-2026-08-09-comod-json-official-sweep` exists because
that expectation was written into a work order twice.

### `npc.json` — 437 NPCs, 11 columns

`type`, `name`, `simple_object`, `standby_motion`, `blaze_motion`,
`rest_motion`, `effect`, `asb`, `adb`, `fixed_dir`, `zoom_percent`.

Again purely presentational: which mesh, which idle animations, which effect.

### `cosmetics.json` — 517 entries, 15 columns

`Id`, `Cost`, `Description`, `EquipmentSlot`, `Gender`, `ItemId`,
`ItemTypeSort`, `ItemTypeSubSort`, `Name`, `RequiredLevel`, `Image`,
`IsPurchasable`, `IsVisible`, `Category`, `SubCategory`. This is the modern
private-server addition — it is not part of the original TQ schema. `Image`
points at an asset path.

### `GameMap.json` — 156 maps

`DocumentId`, `FileName`, `PuzzleGridSize`. `DocumentId` is the map id used on
the wire (1000 = desert, 1001 = d_antre01, 1002 = newplain, …). `FileName` is
`map/map/<name>.DMap`. `PuzzleGridSize` is 256 for 114 maps and 128 for 22.

All 136 shipped `.DMap` files match a row here; the other 20 rows reference maps
not present in this build. See `docs/assets.md` §3.6.

### Smaller tables

- `shop.json` (22) — `id`, `name`, `type`, `items`
- `region.json` (21) — 13 columns, map regions
- `pet.json` (6) — `search_methods`, `environment_method`, `attack_range`,
  `attack_interval`, `scale`, `add_size`
- `ProfessionalName.json` / `WeaponSkillName.json` (48 each) — id→display-name
- `legendary.json` (17), `AdditiveSize.json` (30), `TerrainNpc.json` (35),
  `WeaponSkillLevelExp.json` (20), `ProfessionalSort.json` (11),
  `compose_package_info.json` (21), `tips.json` (21)

### `json_dict` files

- `common.json` — 4 keys: `PlayerFace`, `SystemPolicy`, `Statuary`, `PkTitles`
- `setup.json` — 17 client settings (`framerate_maximum`, `fullscreen_borderless`,
  `discord_rich_presence`, …). This is user config, not game data.
- `style.json` — `font`, `style`
- `statustips.json` — one key, `tips`

---

## Legacy `.ini` files

### Sectioned (`[Section]` + `key=value`) — 27 files

The 3D/render pipeline. Section names are asset ids.

| File | Sections | Distinct key layouts |
|---|---|---|
| `weapon.ini` | 5,384 | 1 |
| `armor.ini` | 3,418 | 2 |
| `armet.ini` | 2,918 | 1 |
| `ActionDelay.ini` | 2,712 | 1 |
| `3DEffect.ini` | 2,240 | 33 |
| `Mount.ini` | 1,317 | 2 |
| `armet1.ini` | 950 | 1 |
| `misc.ini` | 272 | 1 |
| `3DFlyingObj.ini` | 192 | 1 |
| `3DSimpleObj.ini` | 137 | 1 |
| `WeaponEffect.ini` | 109 | 1 |
| others | ≤ 60 | |

`armor.ini` section shape (section name is a 9-digit item id like `002000000`):

```
Part=1
Mesh0=002000000
Texture0=002000000
MixTex0=0
ThirdTex0=0
FourthTex0=0
MixOpt0=0
MixData0=1.000000 1.000000 1.000000 0.000000
Asb0=5
Adb0=6
ZBuffer0=1
Material0=default
```

`Mesh`/`Texture` ids resolve to `c3/` archive assets. The `N` suffix indexes
sub-parts. **Inferred** — the naming is unambiguous but not traced through code.

`3DEffect.ini` has 33 distinct key layouts because effects vary in part count.
Typical section:

```
[M_Fire]
Amount=1
EffectId0=1066
TextureId0=1420
ASB0=5
ADB0=6
Delay=0
LoopTime=99999999
FrameInterval=33
LoopInterval=0
OffsetX=0 / OffsetY=0 / OffsetZ=0
```

Section names here (`M_Fire`, and elsewhere `zf2-e224`-style tokens) are exactly
what DMap **effect** layers reference by name — see `docs/assets.md` §3.3.

### Flat `key=value` — 7 files

| File | Entries | Key shape |
|---|---|---|
| `miscmotion.ini` | 93,178 | compound numeric |
| `MountMotion.ini` | 40,618 | compound numeric |
| `Action3DEffect.ini` | 8,671 | compound numeric |
| `ActionSound.ini` | 3,734 | `<a>.<b>.<c>` → sound path |
| `WeaponMotion.ini` | 1,863 | compound numeric |
| `fuse.ini` | 1,530 | |
| `StrRes.ini` | 507 | numeric id → UI string |

`ActionSound.ini` example — the key is a dotted triple, the value a `sound/` path:

```
1.999.130=sound/jump.wav
1.999.110=sound/walk.wav
```

**Inferred**: the triple is (body type, ?, action id). Not confirmed.

`StrRes.ini` is the UI string table. Its own header comment documents the id
space: 10000–50000 interface, 100000–200000 3DRole, 300000–400000 map,
500000–600000 3DBaseCode. *(verified — the file says so.)*

### Positional records — 10 files

- `package.ini` — 2 rows: `data.wdf`, `c3.wdf`. Archive priority order.
- `Font.ini` — `font\cour.ttf 12`
- `graphic.ini` — `0 1.0 1.0 0.5 ffffffff ff888888 -45`
- `ItemAdd.ini` — 2,952 rows
- `EmotionIco.ini` (60), `statuseffect.ini` (20), `weather.ini` (7),
  `WeaponCombin.ini` (3), `Material.ini` (2), `codepage.ini` (1)

### Empty

`HairMotion.ini`, `TxtAction.ini`, `headmotion.ini`, `sound.ini` — 0 bytes.

---

## Compiled `ini/*.dbc` twins — and which of the two the client reads

> **Scope of everything above.** The counts in this document were profiled on
> **CCO 2.0**, which ships no `.dbc` at all. They describe that install; they
> are not the format's dimensions. `3DEffect.ini` is 2,240 sections here,
> 2,278 at 5017, 2,294 at 5065 and 2,595 at 5165/5517/6090. `EmotionIco.ini`
> is 60 rows here and 68 on the official clients. [V]

Some tables ship **twice**: the plaintext `.ini` everybody knows, and a
compiled binary twin under the same stem. Reader: `core/dbc.py`, which
dispatches on a four-byte magic — `RSDB` id→path rows, `SIMO` simple objects,
`MESH` appearance rows, `EFFE` effect definitions, `EMOI` emotion icons.

**There is no single rule for the lineage, and assuming one is wrong on three
of the five official clients.** [V]

| install | `.dbc` files in `ini/` | which form is live |
|---|---:|---|
| CCO 2.0 | 0 | the `.ini` |
| 5017 | 0 | the `.ini` |
| 5065 | 0 | the `.ini` |
| 5165 | 0 | the `.ini` |
| 5517 | 14 | the `.dbc` **where one exists** |
| 6090 | 15 | the `.dbc` **where one exists** |

Compiled twins first appear at **5517**. Below that the plaintext file is the
live table and reading it is correct; at 5517 and above the `.ini` is a stale
decoy — in the 6090 install the plaintext files are stamped 2009 and the
compiled ones 2015, and they disagree.

**Gate per file, not per client.** `EmotionIco.dbc` exists **only on 6090**;
5517 ships fourteen other compiled twins and still reads the plaintext emotion
list. "This install is official" does not imply "this table is compiled". [V]

### How far apart the two forms are, where both exist

| table | 5517 | 6090 |
|---|---|---|
| `3DSimpleObj` | `.ini` 191 · `.dbc` **235** | `.ini` 191 · `.dbc` **388** |
| `3DEffect` | `.ini` 2,595 · `.dbc` **3,391** | `.ini` 2,595 · `.dbc` **4,483** (4,472 names) |
| `EmotionIco` | `.ini` 68 · no `.dbc` | `.ini` 68 · `.dbc` **70** |

For `3DEffect` on 6090 that is **1,878 of 4,472 effects (42.0 %) invisible to a
plaintext reader, plus 56 of the 2,594 shared entries silently different**; at
5517, 796 of 3,391 (23.5 %) and 5. Measured — `docs/effects.md` §7a has the
breakdown and `CO_ROOT=<install> py -3 tools/dbcdiff.py` reproduces it,
printing the resolved root, `base_id` and both files' stamps beside every
count. [V]

    py -3 core/dbc.py show "$ROOT/ini/3DEffect.dbc" --limit 5
    py -3 core/dbc.py show "$ROOT/ini/EmotionIco.dbc"

**OPEN:** three bytes of each `EFFE` record (`unk62`, `unk63`, `unk64`) are
preserved and uninterpreted. `unk64` is not `Lev` and `unk62` is not
`Billboard` — both refuted on 5517. Whether `unk63` is `ColorEnable` cannot be
settled from these files; it would read the same either way.

---

## What this tells us for a compatible client

**Verified takeaways:**

- The client-side database is presentation-focused. `monster.json` and `npc.json`
  carry meshes, animations and blend states — not combat stats. Anything
  authoritative (damage, drops, AI) lives server-side and must come off the wire.
- `itemtype.json` *does* carry full item stats, so item tooltips and inventory
  rendering can be driven entirely locally from this file.
- `GameMap.json` `DocumentId` is the map id the protocol will use; it maps
  directly to a `.DMap` whose passability grid is now fully decoded.
- `StrRes.ini` gives the UI string table with a documented id space, which will
  make protocol-driven UI messages readable.
- Asset references are consistent across the whole database: ids in
  `itemtype.json` / `armor.ini` / `weapon.ini` resolve to paths in the WDF
  archives whose names are now 98.66 % recovered.

**Open:**

- Exact digit split of `itemtype.id` into category/tier.
- Meaning of `monopoly`, `ident`, `Disc`/`DiscEx`, `FloorAttribute`.
- The dotted-key convention in the `*motion.ini` family.
- `ini/TerrainMagic/*.TME` (94 binary blobs) are entirely undecoded.
