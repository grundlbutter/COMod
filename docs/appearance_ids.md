# Body appearance IDs — class, gender and body size

How to read a 9-digit body appearance ID, and how the asset viewer's three
filters are derived from it. Everything below was measured against this install;
each claim is marked **VERIFIED** or **INFERRED**.

Tooling: `tools/bodyfacets.py` (module + CLI). `py -3 tools/bodyfacets.py`
prints the summary this document explains.

---

## 1. The shape of the ID

`armor.ini` backs both the `body` and `mix_body` part tables and holds **3,418
distinct appearance IDs**. Every one is 9 digits:

```
        002 135 3 0 0
        │   │   │ │ └─ always 0
        │   │   │ └─── mesh / cut variant   (mesh id = TTT SSS 0 M 0)
        │   │   └───── colour variant       (texture id = the whole ID)
        │   └───────── armour series  -> CLASS
        └───────────── body type      -> GENDER + SIZE
```

**VERIFIED** by inspection of `armor.ini` itself. `002135300` has
`Mesh0=002135000, Texture0=002135300`; `002135310` has `Mesh0=002135010`. So the
7th digit selects the colour and the 8th selects the mesh, and the mesh ID is
the same ID with the colour digit zeroed. That is why **many appearances share
one mesh and differ only by texture** — the single most useful fact for
retexturing, and why the viewer offers a *group by mesh* mode.

### 1.1 Two populations

| | count |
|---|---|
| player bodies, prefixed `001`–`004` | 3,233 |
| other body types, all of the form `NNN000000` | 185 |
| **total** | **3,418** |

The 185 others are one base body each for prefixes `098`, `099`, `103` … `900`,
`910` — NPC and monster body types, with no armour and no items behind them.
**VERIFIED**: every one of the 185 ends in `000000`. The viewer buckets them as
kind `npc body`, class `unknown`, and never hides them.

---

## 2. Gender and size — the first three digits

| prefix | gender | size | appearances | height | arm span | depth |
|---|---|---|---:|---:|---:|---:|
| `001` | female | small | 824 | 168.1 | 135.6 | 24.6 |
| `002` | female | large | 824 | 170.5 | 159.5 | 30.9 |
| `003` | male | small | 793 | 176.2 | 178.9 | 32.3 |
| `004` | male | large | 792 | 195.7 | 217.7 | 41.0 |

Measurements are the render-space bounding box of the `v_body` chunk of
`c3/mesh/00X000000.c3` after the chunk matrix and the `(x, y, −z)` conversion.

### 2.1 Gender — VERIFIED

`itemtype.json` carries `requiredSex`, but it is `0` on 11,125 of 11,142 rows,
so it is useless in bulk. The **17 rows that do set it** are decisive:

> For each gender-locked item, find every body appearance whose ID contains that
> item's family. **16 of the 17 land under exactly two prefixes, and the split is
> perfectly clean:** every `requiredSex = 2` garment appears under `001` + `002`,
> every `requiredSex = 1` garment under `003` + `004`.

```
182335 BlueDream        sex 2  ->  001, 002        184375 Spartan`sPride  sex 1  ->  003, 004
183365 PartyDress(Lady) sex 2  ->  001, 002        187465 AncientGeneral  sex 1  ->  003, 004
187455 AncientBeauty    sex 2  ->  001, 002        192185 AssassinSuit    sex 1  ->  003, 004
                                        ... 16 of 17 agree ...
```

The one exception is `193655 FoxyGoblin` (`requiredSex = 2`) which has art under
all four prefixes — art shipped for bodies the item is not flagged for, not a
counterexample to the mapping. `tools/test_viewer.py` re-derives this check and
requires ≥ 90 % agreement.

Independently confirmed by sight: rendering the four base bodies shows `001` and
`002` female, `003` and `004` male. See `out/viewer/shots/body_00X_base.png`.

### 2.2 Size — VERIFIED by measurement

Within each gender the lower prefix is strictly smaller **on every axis at once**
— height, arm span and front-to-back depth. There is no axis on which the
ordering reverses, so "small" and "large" are not a judgement call:

```
001 < 002   168.1 < 170.5   135.6 < 159.5   24.6 < 30.9
003 < 004   176.2 < 195.7   178.9 < 217.7   32.3 < 41.0
```

Renders agree: `001` is a slight female figure and `002` a taller, broader one;
`003` is a lean male and `004` a heavily-built one.

The game's own strings think in these terms — `StrRes.ini` line 100159 reads
*"Small sized female Tao garment is unavailable at the moment."* — which
corroborates a (size × gender) product but does not by itself say which prefix
is which.

### 2.3 What this does NOT say

The prefixes are **not** claimed to equal the body-type numbers a Conquer Online
server sends over the wire. That mapping lives in the Themida-packed client and
has not been read. This is purely the asset-side ID scheme.

---

## 3. Class — the middle three digits

Class is **not** in the appearance ID directly. It comes from
`itemtype.json`'s `requiredProfession`, reached through the **armour series** —
digits 4–6.

### 3.1 Profession codes in this build — VERIFIED

Only nine values occur across all 11,142 items:

| code | items | class | evidence |
|---:|---:|---|---|
| 0 | 5,231 | any | no class requirement |
| 11 | 1,103 | **Trojan** | `DragonArmor`, `BreastPlate`, `WarCoronet` |
| 15 | 105 | **Trojan** | `ConquestArmor`, `MagicCoronet` |
| 21 | 1,614 | **Warrior** | `IronHelmet`, `OxhideArmor`, and **all shields (`900xxx`)** |
| 25 | 105 | **Warrior** | `ConquestHelmet`, `PhoenixArmor` |
| 40 | 1,301 | **Archer** | `MartenHat`, `DragonCoat`, and **all bows (`500xxx`)** |
| 45 | 105 | **Archer** | `PhoenixHat`, `PhoenixJerkin` |
| 190 | 1,576 | **Taoist** | `StarRobe`, `CraneVestment`, `FlowerBag`, backswords (`421xxx`) |
| 143 | 2 | Taoist | only `FireofHell` and `BombScroll`; never backs a body appearance |

The units digit is a tier inside the class (11 and 15 are both Trojan), so the
mapping folds on tens. The weapon exclusivity is what makes this solid rather
than a reading of names: shields are Warrior-only, bows Archer-only and
backswords Taoist-only in Conquer Online, and the codes line up exactly.

**There are no Ninja, Monk or Pirate codes in this build.** It really is the
user's four classes plus "any". *(VERIFIED — the table above is exhaustive.)*

### 3.2 Series → class — VERIFIED, 99.0 % coverage

Taking every 6-digit item whose ID starts with a given 3-digit series:

| series | appearances | profession | class | typical items |
|---|---:|---:|---|---|
| `000` | 4 | — | — | the naked base body |
| `130` | 320 | 11 | **Trojan** | BreastPlate, GothicPlate, DipperArmor |
| `131` | 320 | 21 | **Warrior** | BronzeArmor, SteelArmor, BrightArmor |
| `132` | 56 | 0 | any | Coat, Dress (the starting clothes) |
| `133` | 320 | 40 | **Archer** | DeerskinCoat, FoxCoat, WolfCoat |
| `134` | 320 | 190 | **Taoist** | TaijiRobe, StarRobe, PureGown |
| `135` | 88 | 15 | **Trojan** | ConquestArmor |
| `136` | 32 | 25 | **Warrior** | PhoenixArmor |
| `137` | 196 | 0 | any | NewYearCoat, GMRobe, PhoenixDress |
| `138` | 32 | 45 | **Archer** | PhoenixJerkin |
| `139` | 32 | 190 | **Taoist** | PineRobe |
| `181`–`194` | 1,488 | 0 | any | the modern garments/fashion outfits |
| `186`, `195` | 29 | — | unknown | no items exist for these series |

**Not one series is internally inconsistent** — every series whose items exist
maps to exactly one profession. `bodyfacets.series_profession()` deliberately
returns `None` (→ class `unknown`) if that ever stops being true, rather than
taking a majority vote, so a future patch cannot make the class axis quietly
wrong.

Totals: **Warrior 352, Trojan 408, Taoist 352, Archer 352, any 1,740,
unknown 214.** Trojan is larger because its `135` series has 88 appearances
where the other elite series have 32.

`unknown` = 185 NPC bodies + 24 (series `186`) + 5 (series `195`).

---

## 4. Appearance → item: why the obvious join mostly fails

The naive join `appearance[3:] == itemtype.id` resolves only **648 of 3,338
(19.4 %)**. The reason is structural, not missing data:

> **Item IDs carry a trailing quality digit; appearance IDs always end in 0.**

```
appearance 002130200        items 130203 130204 130205 130206 130207 130208 130209
                                       └── quality / tier 3..9
```

So an appearance names a **family** of items that share one look and differ by
quality. Matching on the first 5 of the 6 digits lifts coverage from 19.4 % to
**65.9 %** (2,132 of 3,233), and **no family spans two professions**, so the
class it implies is never ambiguous.

The remaining 34 % are mostly the `181`–`195` garment series, where the art grid
is denser than the item list: the appearance grid steps in 10s across colours
and cuts while only some combinations were ever sold. Series `186` and `195`
have no items at all.

**None of this is hidden.** Every appearance that cannot be tied to an item still
gets its gender and size from the prefix, its class from the series where the
series is known, and lands in the explicit `unknown` bucket otherwise. The
viewer's `Unclassified` chip selects exactly those.

*(The series→class route is what the viewer actually uses for class, because it
covers 99 % rather than 66 %. The family match is used to show the item name.)*

---

## 4.5 Monsters do not use this id at all — VERIFIED

Everything above is about **body appearance** ids. A monster is addressed
differently, and the difference is easy to miss because both are numbers in
the same neighbourhood.

A monster's art is chosen by the **server**, through `monstertype.Mesh`, and
that value is an id in the same space as the *texture*, not a directory:

```
body 0103  ThunderApe   c3/monster/103/   own dir     c3/texture/103000000.dds
body 0303  SnowApe      c3/monster/103/   BORROWED    c3/texture/303000000.dds
body 0403  FireSnake    c3/monster/203/   BORROWED    c3/texture/403000000.dds
```

**A colour morph ships a texture and no geometry.** The trailing two digits
name a shipped directory and the leading digit(s) are the colour, counted in
hundreds above it — so `SnowApe` really is the ape wearing a different skin.
Families sharing a tail (117 BullMonster, 217 NightDevil) interleave by 200,
which is what puts `FireSnake 0403` on the snake dir rather than the ape dir.

`core/monsterart.py` holds the rule, its verified examples and the two traps:
a body with its own directory must never be morphed (217 satisfies the
arithmetic *and* ships its own art), and a directory name is not always its
number (`c3/monster/104n/`).

Two things the id genuinely cannot tell you:

* **The monster's name.** No client table links art to a name — `Monster.dat`
  has no mesh column and `bodyType` is 0 on every row at 5517 and 6090. One
  directory commonly serves several monsters (`0103` serves seven), so there
  is no single right label. That link is server-side.
* **Size.** Monsters sharing a mesh are differentiated by `zoomPercent` in
  `Monster.dat` — the seven on `0103` run 100/120/110/120/100/100/150 and are
  identical in every other field. **Texture plus zoom is the whole visual
  difference.**

Measured on 5517: 83 monster directories carry meshes, and 46 of them (55 %)
have more than one texture.

---

## 5. Reproducing

```bash
py -3 tools/bodyfacets.py                 # the summary tables above
py -3 tools/bodyfacets.py --id 002135300  # classify one appearance
py -3 tools/bodyfacets.py --json          # every record
py -3 tools/test_viewer.py BodyFacetsTests
```

The tests re-derive the gender mapping from `requiredSex` rather than asserting
it, check that no series is mixed, check that the family match beats the exact
match by more than 2×, and check that no appearance is dropped from the
classification.
