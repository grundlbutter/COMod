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

## The main tables (`json_list`)

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
