# Mesh → texture — which skin goes on which model

Every `.c3` mesh in this install, matched to the `.dds` textures that can be
painted on it, with the rule and the evidence recorded per match.

Tool: `tools/meshtex.py`. Output: `out/meshtex/`.
Companion to `docs/modding.md` (formats) and `docs/appearance_ids.md` (ID scheme).

Everything below is marked **VERIFIED** (the pairing is stated by a shipped
data file, or proven by an arithmetic/decoding check) or **INFERRED** (a naming
convention whose precision has been *measured*, with the number quoted).

---

## 1. Headline

| | |
|---|---:|
| `.c3` files reachable (loose + recovered archive names) | **6,390** |
| …containing no geometry at all (motion / particle / camera / shape) | **1,426** |
| **Real meshes** (≥ 1 `PHY`-family chunk) | **4,964** |
| Meshes matched to ≥ 1 texture | **4,950** |
| **Coverage** | **99.72 %** |
| …of which matched by an *authored* table, not a guess | **4,245 = 85.52 %** |
| Meshes with no candidate at all | **14** |
| Chosen textures that exist, parse and decode | **4,950 / 4,950** |
| Distinct textures in the searched universe | 66,838 |

```bash
py -3 tools/meshtex.py --coverage --verify     # regenerate everything
py -3 tools/meshtex.py --precision             # re-measure the inferred rules
py -3 tools/meshtex.py c3/monster/105/401.c3   # rank one mesh
```

### The single most important correction to the framing

The task started from "4,498 archived meshes". **1,426 of the 6,390 `.c3` files
are not meshes.** A `.c3` is a chunk container (`docs/modding.md` §5); these
files carry only `MOTI`, `PTCL`/`PTC3`, `CAME`, `SHAP`+`SMOT` and no
`PHY`-family chunk whatsoever, so they have no vertices, no UVs, and nothing a
texture could be applied to.

| tag signature | files |
|---|---:|
| `MOTI` only | 630 |
| `CAME`+`SHAP`+`SMOT` | 254 |
| `PTCL` | 215 |
| `PTC3` | 162 |
| `CAME`+`PTCL` | 97 |
| `SHAP`+`SMOT` | 63 |
| `CAME` only | 3 |
| other combinations | 2 |

790 of them are in `c3/effect`, 453 in `c3/monster`. Counting these as
"unmatched meshes" would have made the denominator wrong by 22 %.
`MeshTextureIndex.is_mesh()` is the check; `all_meshes()` excludes them.

---

## 2. Coverage by directory

| directory | meshes | matched | rate | authored |
|---|---:|---:|---:|---:|
| `c3/mesh` | 1427 | 1421 | 99.6 % | 1393 |
| `c3/effect` | 1326 | 1326 | 100 % | 1092 |
| `c3/monster` | 636 | 636 | 100 % | 414 |
| `c3/0001` | 271 | 271 | 100 % | 271 |
| `c3/0003` | 270 | 270 | 100 % | 270 |
| `c3/0004` | 265 | 265 | 100 % | 265 |
| `c3/0002` | 240 | 240 | 100 % | 240 |
| `c3/hair` | 179 | 173 | 96.6 % | 23 |
| `c3/npc` | 167 | 165 | 98.8 % | 142 |
| `c3/weapon` | 166 | 166 | 100 % | 130 |
| `graphics/cosmetics` | 5 | 5 | 100 % | 3 |
| `c3/ghost` | 4 | 4 | 100 % | 1 |
| `graphics/garments` | 4 | 4 | 100 % | 0 |
| `c3/body` | 3 | 3 | 100 % | 0 |
| `c3/mount` | 1 | 1 | 100 % | 1 |

"authored" counts meshes whose *best* match comes from a shipped table.

---

## 3. The rules

Candidates are ranked **by kind first** — every authored match outranks every
inferred one — then by confidence. `Match.kind` is exactly the
"the appearance table says so" vs "same-name sibling guess" distinction the
caller needs.

| method | kind | conf | best-match count |
|---|---|---:|---:|
| `motion_appearance` | authored | 0.90 | 1575 |
| `appearance_table` | authored | 0.95 | 1389 |
| `effect_table` | authored | 0.98 | 1094 |
| `simpleobj_table` | authored | 0.98 | 103 |
| `npc_table` | authored | 0.95 | 76 |
| `simplerole_table` | authored | 0.95 | 8 |
| `stem_pair` | inferred | 0.97 | 259 |
| `bodytype_dir` | inferred | 0.98 | 229 |
| `objtex_id` | inferred | 0.73 | 191 |
| `npc_look_id` | inferred | 0.97 | 16 |
| `dir_sibling` | inferred | 0.30 | 5 |
| `dir_consensus` | inferred | 0.71 | 4 |
| `label_sibling` | inferred | 0.62 | 1 |

### 3.1 `effect_table` — VERIFIED. The whole effect bucket, at once.

`c3/effect` was the biggest mesh bucket and had no known link. Three shipped
files solve it:

```
ini/3DEffect.ini      [<effect name>] Amount=N, then EffectId<i> / TextureId<i>
ini/3DEffectObj.ini   <effect id>  = C3/Effect/MFire/1.C3      2,351 entries
ini/3dtexture.ini     <texture id> = C3/Effect/b_fire/1.dds    7,929 entries
```

```ini
[M_Fire]
Amount=1
EffectId0=1066        ; -> 3DEffectObj.ini  -> C3/Effect/MFire/1.C3
TextureId0=1420       ; -> 3dtexture.ini    -> C3/Effect/b_fire/1.dds
```

Both id spaces resolve to **explicit paths**, so nothing is guessed: 2,314 of
2,351 object paths and 7,785 of 7,929 texture paths exist in this build.
3,877 `(EffectId, TextureId)` pairs, covering **1,092 of the 1,326 effect
meshes**. `ini/3dobj.ini` (2,037 entries) is a second object table read the
same way.

> The `3DEffect.json` mentioned in the brief is the same data pre-parsed —
> `{'Effect': 1066, 'Texture': 1420}` is `EffectId0`/`TextureId0`. The `.ini`
> is used here because it is the file the client actually loads and it carries
> `Amount` explicitly. Weapon→effect mapping is task #13's; not touched.

### 3.2 `appearance_table` — VERIFIED structure, INFERRED id→file

The `RolePart.ini` tables (`armor.ini`, `weapon.ini`, `armet.ini`,
`armet1.ini`, `Mount.ini`, `misc.ini`, `head.ini`) give `Mesh<i>`/`Texture<i>`
per appearance. This is what `tools/c3tex.py` already inverts; it is
reproduced here so all rules share one ranking.

**Measured limit: this rule alone covers 1,389 of 4,964 meshes = 28 %.** It
covers equipment and nothing else — no monster, no NPC, no effect. That is the
"real remainder" the brief asked to establish. `MixTex<i>`/`ThirdTex<i>`/
`FourthTex<i>` are also emitted, which is why the rule contributes 4,917
candidate matches across all ranks.

### 3.3 `motion_appearance` — VERIFIED key layout. The biggest single win.

The motion tables name the `.c3` that plays a motion, and the motion id
encodes the appearance that motion belongs to:

```
ini/3dmotion.ini    228,985 entries    <bodytype><3-digit weapon set><3-digit action> = path
ini/WeaponMotion.ini  1,863 entries    <weapon appearance><3-digit action>            = path
ini/MountMotion.ini  40,618 entries    <mount appearance><3-digit action>             = path
```

**VERIFIED by decomposition:**

```
105000100 = c3/monster/105/100.c3      body type 105, weapon set 000, action 100
105000140 = c3/monster/105/401.c3
1000001   = c3/0001/000/001.c3         body type 1
510000300 = c3/mesh/510000401.c3       weapon appearance 510000, action 300
```

Strip the last 6 digits and append `000000` and you get an appearance id —
which is exactly the 185 `NNN000000` monster/NPC body rows `armor.ini` carries
(`docs/appearance_ids.md` §1.1). `armor.ini [105000000] Texture0=105000000` →
`c3/texture/105000000.dds`.

This is what unlocks `c3/monster` and all four `c3/000X` player-body motion
sets: **1,575 meshes**, all from authored data.

It legitimately returns **several** textures per mesh. Body types 120 and 122
both animate through `c3/monster/120/*.c3` and carry different skins — the same
"one mesh, many textures" pattern the armour tables show. Measured 81 % top-1
agreement with the other authored rules where both fire; the disagreements are
this multiplicity, not error.

`miscmotion.ini` (93,178 entries) resolves to **zero** files — it points at
`c3/miscmotion/**`, a tree this build does not ship.

### 3.4 `simpleobj_table` / `npc_table` / `simplerole_table` — VERIFIED

```
ini/3DSimpleObj.ini   [ObjIDType<n>] PartAmount=N, Part<i> (->3dobj.ini), Texture<i> (->3dtexture.ini)
ini/npc.json          {type, name, simple_object, standby_motion, blaze_motion, rest_motion}
ini/3DsimpleRole.ini  [Role<n>] 3DSimpleObjID, 3DStandByMotion, 3DBlazeMotion
```

`npc.json`'s `simple_object` indexes `3DSimpleObj.ini`, which names the
texture; its three motion ids index `3dmotion.ini`, which names the `.c3`
files. Joining the two attaches each NPC's own texture to every mesh file it
animates with. **436 of 437 `npc.json` rows resolve to at least one real
texture**, and where this and another authored rule both fire they agree
**100 %**.

`3DsimpleRole.ini` is the character-creation preview; it is what backs
`c3/mesh/99988*.c3`.

> `monster.json` (374 rows) carries **no art reference at all** — only
> `type`, `name`, and combat stats. It is a dead end for this question; the
> monster art link is `3dmotion.ini`, §3.3. `pet.json` (6 rows) has no ids
> either, `TerrainNpc.json` points at `.scene` files, and `cosmetics.json`
> points at `img/*.jpg` shop art. Checked, all four; none contribute.

### 3.5 `bodytype_dir` — INFERRED, **98 % top-1 / 100 % any** (n = 446)

`c3/{monster,ghost,npc,mount}/<NNN>/<action>.c3` → `c3/texture/<NNN>000000.dds`.
A generalisation of §3.3 to the files in such a directory that no motion table
happens to reference. `c3/monster/127/115.c3` is not in `3dmotion.ini`, but it
sits beside `c3/monster/127/100.c3` which is, and every PHY chunk in the
directory carries the same 3DSMax label. Non-digits in the directory name are
stripped (`104n` → `104`).

### 3.6 `stem_pair` — INFERRED, **97 % top-1** (n = 1,537)

Identical numeric stem: `c3/mesh/<id>.c3` ↔ `c3/texture/<id>.dds`, or a `.dds`
of the same stem in the mesh's own directory. Tried as written, zero-padded to
9, and stripped of leading zeros.

### 3.7 `npc_look_id` — INFERRED, **97 % top-1** (n = 63)

`c3/npc/999<look><action>.c3` → `c3/texture/999<look>*.dds`. The padding is
inconsistent in the shipped data (look 265 → `9992650`, look 118 → `9990118`),
so six forms are probed. Two misses, both NPC look 011, which is skinned from
`c3/npc/029/1.dds` instead.

### 3.8 `objtex_id` — INFERRED, **72 % top-1** (n = 1,774)

If `3dobj.ini`/`3DEffectObj.ini` map id *N* to this mesh, try `3dtexture.ini`
id *N*. Often a real pair (`9990010` is both) but the two id spaces are not
formally linked, hence the mediocre score. Ranked below `stem_pair`.

### 3.9 `dir_consensus` — INFERRED, **71 % top-1** (n = 2,144, held out)

Inherit the *authored* matches of the other `.c3` files in the same directory.
Fires only when the mesh has no authored match of its own, the directory holds
≤ 64 `.c3` files, and its siblings' matches agree to within 6 distinct
textures. **Both guards are load-bearing**: ungated, this rule pushed coverage
to a fake 99.76 % by handing `c3/npc/`'s 125 unrelated flat files the union of
each other's textures. Scored with the mesh's own authored row held out, since
the live rule stays silent whenever one exists.

### 3.10 `label_sibling` — INFERRED, **62 % top-1** (n = 624)

The embedded 3DSMax label. `docs/modding.md` §9.5 note 4 flagged this as the
best lead for the unmatched; **measured, it is the weakest useful rule.**

| | |
|---|---:|
| `PHY`-family chunks | 14,736 |
| …carrying a label | **14,542 (98.7 %)** |
| mesh files with ≥ 1 label | 4,892 / 4,964 |
| distinct labels | 1,936 |
| labels that resolve as a **full path** inside the game tree | **6** |

The labels are the *artist's own workstation* paths, not game paths —
`侠女\道士\body.tga`, `我的文档\My Pictures\0207czt01.jpg`, `E:\output10000.tga`,
`192.168.1.227\魔域\07_NPC动作\宝箱\宝箱4.tga`. So only the basename is usable,
and basenames are worthless in bulk: `1.tga` alone appears on hundreds of
unrelated effect meshes. Restricted to "basename resolved inside the mesh's own
directory" it scores 62 % and contributes exactly **one** best-match in the
whole corpus.

It is retained because it is real provenance and it is excellent *evidence*
even when it is not a match: every file in `c3/monster/127/` sharing the label
`征服动作\++神耕父-xuan\1.tga` is what confirms §3.5 is safe. GBK is decoded for
display only — the raw bytes are never round-tripped (`docs/modding.md` §10.1
note 3).

### 3.11 `dir_sibling` — INFERRED, **35 % top-1 but 92 % any** (n = 1,053)

Any `.dds` in the mesh's own directory, same ≤ 64-file gate as §3.9. Read this
as a **shortlist, not an answer**: the right texture is in the returned set
92 % of the time but the ordering is arbitrary. Confidence 0.30 reflects
top-1, deliberately.

### 3.12 Rules that were measured and **rejected**

Honest negatives, because they look plausible and are not:

| candidate rule | measured | verdict |
|---|---|---|
| `parent_sibling` — any `.dds` in the parent directory | **0 %** top-1, n = 917 | rejected outright |
| `colour_variant` — mesh `AAABBBcDE` → texture `AAABBBxDE`, x = 0-9 (the armour scheme of `docs/appearance_ids.md` §1) | 51 % top-1, n = 511, **and 0 additional meshes covered** | rejected: no gain, halves precision |
| `999<TTT>000000` — `c3/npc/999<TTT>*.c3` → `c3/texture/<TTT>000000.dds` | **0 %**, n = 63 | rejected; the real form is §3.7 |
| `3dtexture` id `999<ttt>` | 10 %, n = 63 | rejected |

The `999<TTT>000000` rule is the instructive one: it *would* have "matched" 15
more meshes and pushed the headline number up, and it is measurably wrong.

---

## 4. The one technique that mattered most

**Probe the WDF index by hash instead of trusting the recovered-name list.**

The enumerable universe is 53,669 loose files plus the 24,426 archive names the
earlier workstream recovered (98.7 %, `docs/assets.md` §2.3). The missing 1.3 %
is not evenly spread — `c3/texture/223000000.dds`, `274000000.dds`,
`277000000.dds`, `9992700.dds` and friends are all *present in `c3.wdf`* and
all *absent from the name table*.

`MeshTextureIndex.exists()` falls back to `AssetRoot.locate()`, which runs the
real TQ hash over the candidate path and looks it up in the WDF index
directly. That answers "does this file exist" **decisively** for any name you
can guess, whether or not anyone ever recovered it.

Effect: coverage 99.29 % → 99.72 %, and it is what makes §3.7 work at all.
It also converts "the texture is missing" from a guess into a fact — the
residue below is *proven* absent, not merely un-named.

---

## 5. The residue — 14 meshes, characterised

Every one was probed against ~200 candidate names (stem forms, colour/mesh-digit
variants, body-type forms, and every embedded label basename, across nine `c3/`
subdirectories), each hashed against both WDF indexes. **None has a texture in
this build.**

| meshes | what they are |
|---|---|
| `c3/hair/002119052-055.c3` (4) | hair styles 52-55 for body type 002. `c3/hair` ships textures for styles 42-46, 62-66, …; nothing for 52-55. |
| `c3/hair/2119356.c3`, `4119356.c3` (2) | hair style 56. Neighbouring styles 46/66/76/86 ship textures; 56 does not. |
| `c3/mesh/004134030.c3` (1) | Taoist armour, body type 004, mesh variant 3. No `armor.ini` appearance references it and no `004134*30` texture exists. |
| `c3/mesh/004137020.c3`, `004137030.c3` (2) | series-137 garment meshes. `armor.ini` *does* reference `004137020` from six appearances (`004137320`…`004137820`) — and **not one of those six textures is shipped.** Art referenced by the database but absent from the build. |
| `c3/mesh/421080.c3`, `421180.c3` (2) | backsword meshes, single `Plane01`/`Object02` chunk. No `weapon.ini` section references either. |
| `c3/mesh/1050000401.c3` (1) | a bow (`Line01`), label `…\游戏用的标准弓\shi002(1).tga`. Not in any motion or weapon table. |
| `c3/npc/1.c3` (1) | a treasure-chest NPC (label `…\宝箱\宝箱4.tga`). Flat file, not in `npc.json`, no directory. |
| `c3/npc/9990217.c3` (1) | label `魔域\蛋.tga` ("egg"). 7-digit stem, so §3.7 does not apply. |

**These are not "vertex-coloured effect meshes", collision proxies or LODs** —
that hypothesis was checked and is dead. Across the whole corpus:

* **zero** of the 14,736 `PHY`-family chunks carry the `C3EXP_COLOR` label,
  which is the flag that tells the engine to use per-vertex colour instead of a
  texture (`docs/modding.md` §9.5 note 4). Not one mesh in this build is
  vertex-coloured.
* **zero** chunks have degenerate UVs — every one has a real, non-empty UV
  range, i.e. every mesh was authored expecting a texture.

So the residue is simply **orphaned art**: meshes whose skins were dropped from
this build, plus two weapon meshes and two NPCs the databases forgot. In the
`004137020` case the database still *names* the missing textures, which is
about as direct as evidence of a broken build gets.

---

## 6. Verification

`--verify` decodes the top-ranked texture of every matched mesh with
`core/dds.py`:

* **4,950 / 4,950 parse and decode level 0 cleanly.** Zero failures.
* Power-of-two check: zero violations.
* UV sanity: 31 meshes carry UVs outside ±8 — all bow/`Tube`/`Box` geometry
  using deliberate texture *tiling*, not a bad match. Reported as a note, not
  a failure.

This rules out "the file is not really a DDS" and "the pair is nonsense on its
face". It does not, and cannot, prove a texture is the *artistically intended*
one — only rendering can. `tools/coviewer.py` is the tool for that, and this
module's ranked output is meant to feed it.

---

## 7. API

```python
from meshtex import MeshTextureIndex, METHODS

with MeshTextureIndex() as idx:
    idx.matches("c3/monster/105/401.c3")   # -> [Match, ...] ranked, best first
    idx.best("c3/mesh/002135000.c3")       # -> Match | None
    idx.matches(p, include_inferred=False) # authored answers only
    idx.is_mesh(p)                         # has PHY geometry?
    idx.all_meshes()                       # the 4,964
    idx.non_mesh_c3()                      # the 1,426
    idx.labels_of(p)                       # embedded 3DSMax source paths
    idx.verify(mesh, texture)              # {'ok': bool, 'notes': [...], ...}
    idx.exists("c3/texture/223000000.dds") # hash-probes the WDF, see section 4
```

`Match` is a frozen dataclass: `texture` (logical path, guaranteed to exist),
`method`, `confidence`, `kind` (`"authored"` / `"inferred"`), `detail` (the
exact table row, e.g. `armor.ini [002135300] Mesh0=002135000 Texture0=002135300`).

Costs: construction walks the install tree (~1 s) and opens both WDFs; ini
tables load on the first `matches()` call (~4 s); the mesh census parses every
`.c3` (~2 min) and is cached in `out/meshtex/mesh_index.json`. `matches()` on a
single mesh does not need the census unless the `label_sibling` rule is reached.

The census cache defends itself two ways. It is **never written when the
recovered name tables were absent** — such a census sees only loose files, and
persisting it would poison every later run with a half-sized index that looks
fine — and a cache that no longer covers the current universe is **discarded
with a warning and rebuilt**, not trusted. Name tables, and the cache itself,
are located through `coroot.find_derived`, so a linked git worktree reads the
primary checkout's copies instead of silently starting from nothing.

### Output files

| file | contents |
|---|---|
| `out/meshtex/coverage.json` | every mesh, its ranked matches with method/confidence/detail, and the verify result |
| `out/meshtex/summary.json` | the headline numbers, by-directory and by-method breakdowns |
| `out/meshtex/unmatched.txt` | the 14 |
| `out/meshtex/precision.json` | measured precision of every inferred rule |
| `out/meshtex/mesh_index.json` | the `.c3` census: chunk tags, node names, UV ranges, labels |

---

## 8. Known limits

1. **Up to ~40 archived meshes are invisible to this census.** 78 `c3.wdf`
   entries still have unrecovered names, 40 of them `MAXF` payloads
   (`docs/assets.md` §2.3). They cannot be enumerated, so they are neither in
   the 4,964 nor in the residue. The denominator is "meshes we can name".
2. **`motion_appearance` returns a set, not an answer.** For a player body it
   returns the base skin plus the whole armour family; picking the one the
   server actually asked for is a gameplay-state question, not an asset
   question.
3. **`armet1.ini` (`armet_dx8`, 950 appearances) is folded into the same
   index** as `armet.ini`. If the DX8 variant ever diverges, that is a bug.
4. **No rendering was done.** §6 is structural verification only.
5. `misc.ini`, `head.ini` and `Mount.ini` still resolve at near-0 % because
   those assets genuinely are not shipped — as `docs/modding.md` §3 already
   records. Not a resolver bug.
