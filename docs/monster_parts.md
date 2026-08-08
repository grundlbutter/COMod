# Monster part slots — why the properties panel showed no additional art

## It is not `monster.json`, and not 5517

**None of the five unpacked clients ship `ini/monster.json`.** 5017, 5065, 5165,
5517 and 6090 all carry **`ini/Monster.dat`**, the TQ-cipher table. `monster.json`
is the community-client (CCO) form, and `tools/models.py` already falls back to
the `.dat`.

That fallback works. 5517's `Monster.dat` is 246,812 bytes and decrypts cleanly
with `core/tqdat.py` at the standard seed **9527 (`0x2537`)**.

## The actual bug: a schema gap

`core/tqdat.parse_monster` **already preserved** every field — its docstring
names `Armet` as an example of what survives under `row["raw"]`. But
`tools/models.py`'s `MonsterRow` modelled only 13 render properties and dropped
the rest, so anything reading the json shape saw a monster as a bare body.

The dropped fields are exactly the additional art. Per
`docs/appearance_path.md`, the client feeds them to `SetPart` 5/6/7/9/8:

| field | part |
|---|---|
| `Armet` (+ `ArmetColor`) | 5 |
| `RWeapon` | 6 |
| `LWeapon` (+ `LWeaponColor`) | 7 |
| `Misc` | 9 |
| `Mount` | 8 |

Both layers now carry them: promoted to first-class keys in `tqdat`'s
`_MONSTER_KEYS`, modelled on `MonsterRow`, and emitted by `to_json()` — with a
`parts` dict holding only the non-zero slots, plus a `raw` fallback so an older
parse still works.

## But the honest finding: 5517 barely uses them

Of **762** rows, exactly **2** have any part set:

```
SwordMaster     type=4170   {'misc': 953410010}
DarkmoonDemon   type=4209   {'misc': 953410010}
```

That id resolves the way `appearance_path.md` §4 says a weapon slot does —
`% 1e6`, no body prefix:

```
953410010 % 1e6 = 410010
  -> weapon.ini [410010]  Mesh0=410010
  -> 3dobj.ini  410010 = c3/mesh/410010.c3
```

**So the panel showing nothing was correct for 760 of 762 monsters.** The fix
makes the two real ones visible and stops the loader silently discarding the
field, but it does not conjure art that is not in the table.

`armor.ini` is not a second source either: 955 sections, and **not one has more
than a single `Texture`**. `MixTex0` is 0 everywhere (`appearance_path.md` §6).

## Where a monster's art actually comes from

Not from `Monster.dat` at all. `MONSTER_LINK_NOTE` in `tools/models.py` has this
right and it is worth repeating: the table carries **no link to the art** — the
server picks the appearance at spawn (`look % 1000`, and for ids below 1,000,000
the client does a `Monster.dat` lookup **by name**). Monsters are browsed by
mesh directory, and `tools/meshtex.py` is the authority that resolves
`c3/monster/<id>/<action>.c3` to its texture.

So "additional textures" for a monster are the per-action meshes under its
directory, not extra texture rows — with the part slots above as the only
genuine extras, and only two monsters use them.
